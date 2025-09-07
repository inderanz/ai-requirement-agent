#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run Gemini on PDFs in GCS via the stable Vertex AI SDK (enterprise-grade).

Why this version?
- Fixes "Multiple content parts are not supported" by concatenating all text parts.
- Adds debug & raw response dumping for reliable troubleshooting.
- Keeps Workload Identity (no API key) and gs:// ingestion.
- Continuation pass if output looks truncated or misses planned sections.
- Safety settings (SDK-version tolerant) + bounded retries with expo backoff.
- Optional "Figures" appendix if images exist but weren't embedded.

Inputs (unchanged):
  --context      issue_context.json (must include "gcs_uris": ["gs://..."])
  --model        Gemini model id (e.g., "gemini-2.5-pro")
  --prompt-file  prompt.txt built by build_prompt.py
  --project      GCP project id
  --location     Vertex AI region ("global" is typical for Pro)
  --out          Path to write Markdown output (e.g., summary.txt)

Optional:
  --max_output_tokens  default 8192
  --temperature        default 0.2
  --top_p              default 0.95
  --top_k              default 40
  --retries            default 3
  --continuations      default 1 (extra pass if truncated)
  --dump-response      path to write raw response JSON (for 1st main call)
  --debug              enable verbose diagnostics to stderr

Environment / dynamic sections:
  CUSTOM_REQUIRED_SECTIONS="A,B,C"
  required_sections.json => { "sections": ["A","B","C"] }

Exit behavior:
  Always exit 0 (so downstream steps can run), but writes clear errors to stderr.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Any

# Vertex AI SDK (stable)
from google.cloud import aiplatform
from vertexai import init as vertexai_init
from vertexai.generative_models import (
    GenerativeModel,
    Part,
    GenerationConfig,
    SafetySetting,
)

from util import mkdirp, read_json as _read_json

# ---------------------------
# Config / Utilities
# ---------------------------

TRANSIENT_HINTS = (
    "deadline exceeded",
    "503",
    "500",
    "busy",
    "temporar",   # temporary / temporarily
    "unavailable",
    "retry",
    "backoff",
    "connection reset",
    "rate limit",
)

DEFAULT_REQUIRED = (
    "Executive Summary",
    "Functional Requirements",
    "Non-Functional Requirements",
    "ISO 20022",
    "Controls",
    "Traceability",
    "Assumptions",
)

def debug_enabled(args) -> bool:
    return bool(getattr(args, "debug", False) or os.environ.get("GEMINI_DEBUG"))

def log_debug(args, *msg):
    if debug_enabled(args):
        print("[DEBUG]", *msg, file=sys.stderr)

def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")

def load_json(path: str) -> dict:
    return _read_json(path)

def is_transient(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in TRANSIENT_HINTS)

def _safe_get(dct: Any, path: List[str], default=None):
    """Safely crawl nested dict/list by keys/indexes."""
    cur = dct
    try:
        for k in path:
            if isinstance(k, int):
                cur = cur[k]
            else:
                cur = cur.get(k)
        return cur
    except Exception:
        return default

def _extract_all_text_from_response(resp_obj: Any) -> str:
    """Robustly extract and concatenate all text parts from a Vertex AI response object."""
    # Try the convenient .text first
    try:
        if getattr(resp_obj, "text", None):
            return resp_obj.text
    except Exception:
        pass

    # Fallback: concatenate all text parts from all candidates
    try:
        out_chunks: List[str] = []
        candidates = getattr(resp_obj, "candidates", None)
        if candidates:
            for cand in candidates:
                content = getattr(cand, "content", None)
                if content and getattr(content, "parts", None):
                    for part in content.parts:
                        # Part may contain text, inline data, etc. We only take text here.
                        txt = getattr(part, "text", None)
                        if txt:
                            out_chunks.append(txt)
        return "\n".join(out_chunks).strip()
    except Exception:
        pass

    # Final fallback: try to_dict and walk
    try:
        as_dict = resp_obj.to_dict()  # type: ignore[attr-defined]
        out_chunks = []
        for cand in as_dict.get("candidates", []) or []:
            for part in _safe_get(cand, ["content", "parts"], []) or []:
                txt = part.get("text")
                if txt:
                    out_chunks.append(txt)
        return "\n".join(out_chunks).strip()
    except Exception:
        return ""


def build_parts(gcs_uris: List[str], prompt_text: str) -> List:
    """Each PDF (gs://) + the prompt text (as the final part)."""
    parts: List = []
    for uri in gcs_uris:
        if uri.startswith("gs://"):
            parts.append(Part.from_uri(uri, mime_type="application/pdf"))
    # The SDK accepts raw strings as a text part.
    parts.append(prompt_text)
    return parts

# ---------------------------
# Safety (version tolerant)
# ---------------------------

def _build_safety_settings():
    try:
        HC = SafetySetting.HarmCategory
        HBT = SafetySetting.HarmBlockThreshold
        return [
            SafetySetting(category=HC.HARASSMENT, threshold=HBT.BLOCK_NONE),
            SafetySetting(category=HC.HATE_SPEECH, threshold=HBT.BLOCK_NONE),
            SafetySetting(category=HC.SEXUAL, threshold=HBT.BLOCK_MEDIUM_AND_ABOVE),
            SafetySetting(category=HC.DANGEROUS, threshold=HBT.BLOCK_NONE),
        ]
    except Exception:
        return None

# ---------------------------
# Dynamic sections + continuation
# ---------------------------

def _load_required_sections() -> List[str]:
    env_val = os.environ.get("CUSTOM_REQUIRED_SECTIONS")
    if env_val:
        got = [s.strip() for s in env_val.split(",") if s.strip()]
        if got:
            return got
    p = Path("required_sections.json")
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            sections = obj.get("sections") or []
            if isinstance(sections, list) and sections:
                return [str(s) for s in sections]
        except Exception:
            pass
    return list(DEFAULT_REQUIRED)

def looks_truncated(text: str) -> bool:
    req = [s.lower() for s in _load_required_sections()]
    if not text:
        return True
    t = text.strip()
    tail = t[-200:]
    # mid-sentence cutoff / incomplete trailing line
    if tail and not tail.endswith((".", "!", "?", "```")) and "\n## " not in tail:
        return True
    lowered = t.lower()
    for sec in req:
        if sec not in lowered:
            return True
    return False

def build_continuation_prompt() -> str:
    req_str = "; ".join(_load_required_sections())
    return (
        "CONTINUATION REQUEST:\n"
        "Continue the previously generated Markdown **without** repeating content. "
        "Resume from the exact point of truncation, finishing any incomplete section. "
        f"Ensure the required sections are all present: {req_str}.\n\n"
        "Do not restate earlier sections. Continue directly."
    )

# ---------------------------
# Retry wrapper
# ---------------------------

def call_model_with_retries(
    args,
    model: GenerativeModel,
    parts: List,
    gen_cfg: GenerationConfig,
    safety,  # may be None
    max_attempts: int = 3,
    dump_path: Optional[str] = None,  # only for the first main call
) -> Optional[str]:
    """
    Bounded retry with exponential backoff. On the 1st attempt of the first
    main call you can pass dump_path to save the raw JSON for troubleshooting.
    """
    attempt = 0
    while attempt < max_attempts:
        try:
            if debug_enabled(args):
                print("[DEBUG] generating content...", file=sys.stderr)
                print(f"[DEBUG] parts: {len(parts)} (pdfs+prompt)", file=sys.stderr)
                # Note: do not print the full prompt to avoid log bloat

            resp = model.generate_content(
                parts,
                generation_config=gen_cfg,
                safety_settings=safety,
            )

            # Dump raw response once if requested (best-effort)
            if dump_path and attempt == 0:
                try:
                    as_dict = resp.to_dict()  # type: ignore[attr-defined]
                    Path(dump_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(dump_path).write_text(json.dumps(as_dict, indent=2), encoding="utf-8")
                    print(f"[DEBUG] raw response dumped to: {dump_path}", file=sys.stderr)
                except Exception as e_dump:
                    print(f"[DEBUG] raw dump failed: {e_dump}", file=sys.stderr)

            text = _extract_all_text_from_response(resp)
            # Diagnostics to help you see why truncation might occur
            try:
                as_dict = resp.to_dict()  # type: ignore[attr-defined]
                usage = as_dict.get("usage_metadata") or {}
                model_version = as_dict.get("model_version")
                finish_reason = _safe_get(as_dict, ["candidates", 0, "finish_reason"])

                print(
                    json.dumps(
                        {
                            "gemini_usage": usage,
                            "model_version": model_version,
                            "finish_reason": finish_reason,
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
            except Exception:
                pass

            return text or ""
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts or not is_transient(e):
                print(f"ERROR: Gemini call failed ({attempt}/{max_attempts}): {e}", file=sys.stderr)
                return None
            sleep_for = 2.0 * (2 ** (attempt - 1))
            print(f"WARN: transient error ({attempt}/{max_attempts}): {e}", file=sys.stderr)
            print(f"Retrying in {sleep_for:.1f}s ...", file=sys.stderr)
            time.sleep(sleep_for)

# ---------------------------
# Image appendix
# ---------------------------

def _collect_existing_images(issue_dir: Path) -> List[str]:
    img_dir = issue_dir / "images"
    if not img_dir.exists():
        return []
    out = []
    for p in sorted(img_dir.glob("*")):
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
            try:
                if p.stat().st_size > 0:
                    out.append(str(Path("images") / p.name))
            except Exception:
                continue
    return out

def _output_has_image_embeds(md: str) -> bool:
    t = (md or "").lower()
    return "](images/" in t and "![" in t

def append_figures_section_if_needed(md: str, issue_dir: Path) -> str:
    if _output_has_image_embeds(md):
        return md
    images = _collect_existing_images(issue_dir)
    if not images:
        return md
    extra = ["\n\n## Figures\n"] + [f"- ![]({rel})" for rel in images]
    return (md or "") + "\n" + "\n".join(extra) + "\n"

# ---------------------------
# Main
# ---------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--context", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--out", required=True)
    # Tunables
    p.add_argument("--max_output_tokens", type=int, default=8192)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--top_p", type=float, default=0.95)
    p.add_argument("--top_k", type=int, default=40)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--continuations", type=int, default=1)
    # Debug / dump
    p.add_argument("--dump-response", default="", help="Write raw JSON of the first main response")
    p.add_argument("--debug", action="store_true", help="Verbose diagnostics to stderr")
    args = p.parse_args()

    # Load context + prompt
    ctx = load_json(args.context)
    gcs_uris = ctx.get("gcs_uris", [])
    if not gcs_uris:
        print("WARN: No gcs_uris found in context; proceeding with prompt only.", file=sys.stderr)

    prompt_text = read_text(args.prompt_file)

    # Initialize Vertex AI (Workload Identity picks up GOOGLE_APPLICATION_CREDENTIALS)
    vertexai_init(project=args.project, location=args.location)
    aiplatform.init(project=args.project, location=args.location)
    log_debug(args, f"Vertex initialized: project={args.project}, location={args.location}")

    # Model + config
    model = GenerativeModel(args.model)
    gen_cfg = GenerationConfig(
        max_output_tokens=args.max_output_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
    )
    safety = _build_safety_settings()
    parts = build_parts(gcs_uris, prompt_text)

    # First pass (dump raw JSON if requested)
    dump_path = args.dump_response or ""
    text = call_model_with_retries(
        args=args,
        model=model,
        parts=parts,
        gen_cfg=gen_cfg,
        safety=safety,
        max_attempts=max(1, args.retries),
        dump_path=dump_path if dump_path else None,
    ) or ""

    # Continuation loop (bounded)
    remaining = max(0, int(args.continuations))
    while remaining > 0 and looks_truncated(text):
        remaining -= 1
        cont_note = build_continuation_prompt()
        cont_parts = list(parts) + [
            "\n\nPRIOR_OUTPUT (do not repeat; continue from the end):\n" + text,
            "\n\n" + cont_note,
        ]
        more = call_model_with_retries(
            args=args,
            model=model,
            parts=cont_parts,
            gen_cfg=gen_cfg,
            safety=safety,
            max_attempts=max(1, args.retries),
            dump_path=None,  # Only dump the first main call
        ) or ""
        if more.strip():
            text = (text + "\n" + more).strip()
        else:
            break

    # Best-effort image appendix
    issue_dir = Path(ctx.get("artifact_dir") or ".")
    text = append_figures_section_if_needed(text, issue_dir)

    # Persist
    mkdirp(os.path.dirname(args.out) or ".")
    Path(args.out).write_text(text or "", encoding="utf-8")

    if text:
        print(f"OK: wrote Gemini output to {args.out} ({len(text)} chars)")
    else:
        print("ERROR: Gemini returned no text", file=sys.stderr)

if __name__ == "__main__":
    main()
