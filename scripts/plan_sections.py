#!/usr/bin/env python3
"""
Plan required sections from natural-language issue text.
Priority:
1) Explicit user wishes (e.g., 'sections:', 'please include ...')
2) Simple bullet/heading hints in the issue/comment body
3) (Optional) TOC-like lines in first pages of the PDF (if present in context)
4) Fallback: domain default list (your existing seven)
Outputs: required_sections.json  -> { "sections": ["...","..."] }
"""

import argparse, json, re, os
from pathlib import Path

DEFAULTS = [
    "Executive Summary",
    "Functional Requirements",
    "Non-Functional Requirements",
    "ISO 20022",
    "Controls",
    "Traceability",
    "Assumptions",
]

def _pick_from_text(text: str):
    text = text or ""
    sections = []

    # 1) Explicit “sections:” (case-insensitive)
    m = re.search(r"(?im)^\s*sections?\s*:\s*(.+)$", text)
    if m:
        # comma-separated list
        parts = [p.strip(" -•\t\r\n") for p in re.split(r"[;,]", m.group(1))]
        sections = [p for p in parts if p]
        if sections:
            return sections

    # 2) Bulleted hints after a “sections” cue line
    # e.g. "Please include sections\n- Risk\n- Dependencies"
    cue = re.search(r"(?im)^\s*(please\s+include\s+sections?|sections?)\b.*$", text)
    if cue:
        tail = text[cue.end():]
        bullets = re.findall(r"(?m)^\s*[-*•]\s+(.+)$", tail)
        bullets = [b.strip() for b in bullets if b.strip()]
        if bullets:
            return bullets

    # 3) Common business headings in free text — grab a few if mentioned
    CANDIDATES = [
        "Executive Summary","Background","Scope","In Scope","Out of Scope",
        "Assumptions","Dependencies","Functional Requirements",
        "Non-Functional Requirements","Security","Privacy","Performance",
        "Availability","Resilience","Audit & Compliance","Controls",
        "Data Model","Process Flows","Interfaces","ISO 20022","Mapping",
        "Testing Strategy","Risks","Mitigations","Traceability","Appendix",
    ]
    found = []
    low = text.lower()
    for c in CANDIDATES:
        if c.lower() in low:
            found.append(c)
    # require at least 3 to avoid overfitting random matches
    if len(found) >= 3:
        # keep order by CANDIDATES
        uniq = []
        for c in CANDIDATES:
            if c in found and c not in uniq:
                uniq.append(c)
        return uniq

    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)  # issue_context.json
    ap.add_argument("--out", required=True, default="required_sections.json")
    args = ap.parse_args()

    ctx = json.loads(Path(args.context).read_text())
    text = (ctx.get("latest_comment") or "") + "\n\n" + (ctx.get("body") or "")
    sections = _pick_from_text(text)
    if not sections:
        sections = DEFAULTS[:]

    outp = {"sections": sections}
    Path(args.out).write_text(json.dumps(outp, indent=2))
    print("Planned sections:", sections)

if __name__ == "__main__":
    main()
