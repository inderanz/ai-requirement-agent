#!/usr/bin/env python3
"""
Perform lightweight reflection on a generated Markdown report. The validator
checks for the presence of required top-level sections, verifies that
referenced images exist, and performs minimal table structure checks. It
prints a list of issues on stderr and returns a non-zero exit code when
problems are found. If no issues are detected the script prints a success
message.

This script can be extended to perform automatic repairs or invoke a
secondary LLM critic to fix formatting issues. Currently it only
validates and does not modify the document.
"""

import sys
import re
import json
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_and_fix_md.py <path>")
        sys.exit(1)
    p = Path(sys.argv[1])
    text = p.read_text(encoding="utf-8")
    issues = []
    required_sections = [
        "Executive Summary",
        "Functional Requirements",
        "Non-Functional Requirements",
        "ISO 20022",
        "Controls",
        "Traceability",
        "Assumptions",
    ]
    missing = []
    for sec in required_sections:
        pattern = rf"^\s{{0,3}}#{{1,6}}\s*{re.escape(sec)}\b"
        if not re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            missing.append(sec)
    if missing:
        issues.append({"missing_sections": missing})
    # Check that all image references exist
    img_refs = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", text)
    missing_imgs = [ref for ref in img_refs if not (p.parent / ref).exists()]
    if missing_imgs:
        issues.append({"missing_images": missing_imgs})
    # Basic table sanity: ensure each table has header separators with at least two columns
    bad_tables = []
    for m in re.finditer(r"(?:^|\n)(\|.+\|)\n(\|[-:\s|]+\|)\n((?:\|.*\|\n)+)", text):
        header = m.group(1)
        sep = m.group(2)
        if header.count("|") < 3 or sep.count("|") < 3:
            bad_tables.append(m.group(0)[:120] + "...")
    if bad_tables:
        issues.append({"malformed_tables": bad_tables})
    if issues:
        print("❌ Markdown validation issues:\n" + json.dumps(issues, indent=2))
        sys.exit(2)
    print("✅ Markdown validation passed.")


if __name__ == "__main__":
    main()