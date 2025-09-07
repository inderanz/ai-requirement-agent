#!/usr/bin/env python3
"""
Collect metadata and context from a GitHub issue and its comments. This script
is intended to run inside a GitHub Action workflow on a self-hosted runner or
container. It uses the GitHub CLI (gh) to query the issue and comments. The
output JSON includes:

  * issue_number – integer ID of the issue
  * title – issue title
  * body – issue body
  * latest_comment – most recent comment text when triggered by a comment event
  * all_comments_text – concatenated text of all comments
  * pdf_urls – list of detected PDF references (HTTP(S) URLs OR repo-local paths)
  * is_update – True if this is an update (i.e., triggered by comment)

Notes:
- Previously only HTTP(S) URLs were detected. This version also recognizes
  repo-local paths like "upload-pdf/<name>.pdf" mentioned in the issue text.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Set
import requests

# Any http(s) URL candidates
URL_RE = re.compile(r"""https?://[^\s<>()\[\]"]+""", re.IGNORECASE)

# Repo-local PDF refs like:
#   upload-pdf/file.pdf
#   (upload-pdf/file.pdf)
#   [upload-pdf/file.pdf]
# We stop at whitespace or a closing bracket/paren, allow optional query/frag.
REPO_PDF_RE = re.compile(
    r"""(?P<prefix>^|[\s\(\[\{>])(?P<path>upload-pdf/[^\s\)\]\}\r\n]+?\.pdf)(?P<qf>[?#][^\s\)\]\}\r\n]+)?(?P<suffix>\)|\]|\}|[\s]|$)""",
    re.IGNORECASE,
)

def gh_json(args: List[str]):
    """Call gh CLI and parse the JSON result."""
    out = subprocess.check_output(args, text=True)
    return json.loads(out)

def find_urls(text: str) -> List[str]:
    if not text:
        return []
    return URL_RE.findall(text)

def find_repo_pdf_paths(text: str) -> List[str]:
    """Extract repo-local 'upload-pdf/*.pdf' paths from free text."""
    if not text:
        return []
    paths: List[str] = []
    for m in REPO_PDF_RE.finditer(text):
        p = m.group("path") or ""
        # Trim trailing markdown punctuation if any sneaks through
        p = p.rstrip(").],;")
        # Normalize leading ./ if present
        if p.startswith("./"):
            p = p[2:]
        paths.append(p)
    return paths

def is_pdf_url(url: str, headers: dict) -> bool:
    """Determine if the URL appears to point to a PDF.

    We first check if the path ends with .pdf. If not, we perform a HEAD
    request and inspect the Content-Type header for application/pdf.
    """
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    try:
        r = requests.head(url, headers=headers, timeout=8, allow_redirects=True)
        ctype = (r.headers.get("Content-Type") or "").lower()
        return "application/pdf" in ctype
    except requests.RequestException:
        return False

def collect_pdf_refs(issue_body: str, comments: List[dict], headers: dict) -> List[str]:
    """Collect unique PDF references from issue body and comments.

    Returns a combined list containing:
      - HTTP(S) PDF URLs
      - Repo-local paths like 'upload-pdf/<name>.pdf'
    """
    # 1) Collect URL candidates
    url_candidates: Set[str] = set()
    for u in find_urls(issue_body or ""):
        url_candidates.add(u)
    for c in comments or []:
        for u in find_urls(c.get("body") or ""):
            url_candidates.add(u)

    pdf_urls: List[str] = []
    for u in sorted(url_candidates):
        if is_pdf_url(u, headers=headers):
            pdf_urls.append(u)

    # 2) Collect repo-local PDF paths
    repo_candidates: Set[str] = set()
    for p in find_repo_pdf_paths(issue_body or ""):
        repo_candidates.add(p)
    for c in comments or []:
        for p in find_repo_pdf_paths(c.get("body") or ""):
            repo_candidates.add(p)

    # Merge (keep order stable: URLs first, then local paths)
    return pdf_urls + sorted(repo_candidates)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--issue", required=True, type=int)
    ap.add_argument("--event", required=True, choices=["issues", "issue_comment"])
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()

    # Query the issue and its comments using gh.
    issue = gh_json(["gh", "api", f"repos/{args.repo}/issues/{args.issue}"])
    comments = gh_json(["gh", "api", f"repos/{args.repo}/issues/{args.issue}/comments"])

    title = issue.get("title", "")
    body = issue.get("body") or ""
    latest_comment = None
    if args.event == "issue_comment" and comments:
        latest_comment = (comments[-1].get("body") or "")

    # Prepare authorization header for HEAD requests (for URL Content-Type checks)
    headers = {}
    gh_token = os.environ.get("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"
        headers["User-Agent"] = "github-actions-issue-pdf-agent"

    pdf_refs = collect_pdf_refs(body, comments, headers)

    out = {
        "issue_number": args.issue,
        "title": title,
        "body": body,
        "latest_comment": latest_comment,
        "all_comments_text": "\n\n".join([(c.get("body") or "") for c in comments]) if comments else "",
        "pdf_urls": pdf_refs,   # may contain URLs and/or repo-local paths
        "is_update": bool(latest_comment),
    }
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {args.out_json} with {len(pdf_refs)} PDF reference(s).")

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"gh api failed: {e}", file=sys.stderr)
        sys.exit(1)
