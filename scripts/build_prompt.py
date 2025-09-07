#!/usr/bin/env python3
"""
Compose the system instruction and user prompt for Gemini. The system
instruction is assembled from a persona template and one or more task
templates selected by the prompt selection script. The user prompt
includes issue context (title, body, comments), references to the
source documents in Google Cloud Storage, and relative image paths for
extracted images. When relevant, a small set of curated regulatory
snippets (micro-RAG) for Australian payments is appended.

The script writes the final prompt to a text file and emits system
instruction and other settings in a JSON file used by the Gemini CLI
GitHub Action.
"""

import argparse
import json
import os
from pathlib import Path
from util import read_json


def load_text(p):
    return Path(p).read_text(encoding="utf-8")


def _load_required_sections():
    """
    Load planned/required sections if provided by the planner step.
    Fallback: None (so existing behavior is unchanged).
    Also supports an env override CUSTOM_REQUIRED_SECTIONS="A,B,C".
    """
    env_val = os.environ.get("CUSTOM_REQUIRED_SECTIONS")
    if env_val:
        parts = [s.strip() for s in env_val.split(",") if s.strip()]
        return parts or None

    rs_path = Path("required_sections.json")
    if rs_path.exists():
        try:
            data = json.loads(rs_path.read_text(encoding="utf-8"))
            sections = data.get("sections") or None
            if isinstance(sections, list) and sections:
                return sections
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settings", required=True)
    args = ap.parse_args()

    ctx = read_json(args.context)
    issue = ctx["issue_number"]
    title = ctx.get("title", "")
    body = ctx.get("body", "") or ""
    latest_comment = ctx.get("latest_comment") or ""
    all_comments = ctx.get("all_comments_text") or ""
    gcs_uris = ctx.get("gcs_uris") or []

    # Determine prompts directory (passed via env in workflow)
    prompts_dir = os.environ.get("PROMPTS_DIR", "prompts")

    # Load prompt selection result
    chosen_ids = []
    if Path("prompt_selection.json").exists():
        try:
            chosen_ids = json.loads(Path("prompt_selection.json").read_text()).get("chosen", [])
        except Exception:
            chosen_ids = []
    if not chosen_ids:
        chosen_ids = ["npp_requirements"]

    # Compose system instruction from persona and tasks
    system_fp = Path(prompts_dir) / "system" / "analyst_system.md"
    system_text = load_text(system_fp)
    task_texts = []
    for pid in chosen_ids:
        tp = Path(prompts_dir) / "tasks" / f"{pid}.md"
        if tp.exists():
            task_texts.append(load_text(tp))
    # Append inline task prompt if present (created by workflow)
    inline_fp = Path(".agent/overrides/inline_task.md")
    if inline_fp.exists():
        task_texts.append(load_text(inline_fp))
    system_instruction = (system_text.strip() + "\n\n" + "\n\n".join(t.strip() for t in task_texts)).strip()

    # Determine images for embedding
    issue_dir = Path(f"docs/issue-reports/issue-{issue}")
    images_dir = issue_dir / "images"
    images = sorted([p.name for p in images_dir.glob("*.png")]) if images_dir.exists() else []

    # Load planned sections (if present)
    required_sections = _load_required_sections()

    # Build user prompt
    prompt = f"""
TASK:
Generate or update the Markdown report for GitHub issue #{issue}: "{title}".

CONTEXT:
- Issue description:
{body}

- Latest comment (if any, indicates update intent):
{latest_comment}

- Additional comments context (may include earlier requests):
{all_comments}

SOURCE DOCUMENT(S):
""".strip() + "\n" + "\n".join(["- " + u for u in gcs_uris]) + "\n\n"
    prompt += "IMAGES EXTRACTED FROM THE PDF (embed where they belong; if unsure, place near matching section):\n"
    prompt += "\n".join(["- " + "images/" + fn for fn in images]) + "\n\n"
    prompt += "OUTPUT:\n"
    prompt += f"- A single Markdown document beginning with \"# {title}\" followed by \"Executive Summary\".\n"
    prompt += "- Preserve document structure and tables faithfully (GitHub Markdown tables).\n"
    prompt += "- Embed images using the relative paths shown above.\n"
    prompt += "- If this is an update, integrate changes without duplicating sections.\n"
    if required_sections:
        prompt += "- You MUST include the following top-level sections in order: " \
                  + ", ".join(required_sections) + ". Use these headings exactly.\n"

    # Micro-RAG: include selected regulatory snippets when relevant
    rag_block = ""
    if any(pid in ("risk_compliance_au", "npp_requirements") for pid in chosen_ids):
        # Look up snippets relative to prompts_dir for robustness
        rag_path = Path(prompts_dir) / "standards" / "au_snippets.yaml"
        if rag_path.exists():
            try:
                import yaml  # lazy import so we don't hard-require it if not used
                snips = yaml.safe_load(rag_path.read_text(encoding="utf-8")) or []
                lines = []
                for s in snips[:8]:
                    # be defensive: each snippet should have id/title/text/source
                    sid = s.get("id", "ref")
                    ttl = s.get("title", "Untitled")
                    txt = s.get("text", "").strip()
                    src = s.get("source", "")
                    lines.append(f"- [{sid}] {ttl}: {txt} (Source: {src})")
                if lines:
                    rag_block = "REFERENCE SNIPPETS (AU standards):\n" + "\n".join(lines)
            except Exception:
                rag_block = ""
    if rag_block:
        prompt += "\n" + rag_block + "\n"

    # Write settings JSON
    settings = {
        "model": args.model,
        "vertexAI": {
            "project": os.environ.get("GCP_PROJECT_ID"),
            # Keep existing default to avoid breaking callers; location used by SDK step.
            "location": os.environ.get("GCP_LOCATION", "global"),
        },
        "systemInstruction": system_instruction,
    }
    Path(args.settings).parent.mkdir(parents=True, exist_ok=True)
    with open(args.settings, "w", encoding="utf-8") as f:
        json.dump(settings, f)

    # Write prompt text
    Path(args.out).write_text(prompt, encoding="utf-8")

    # Emit multi-line outputs for GitHub Actions
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"prompt_text<<EOF\n{prompt}\nEOF\n")
            f.write(f"settings_json<<EOF\n{json.dumps(settings)}\nEOF\n")


if __name__ == "__main__":
    main()
