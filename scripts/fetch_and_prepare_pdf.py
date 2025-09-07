#!/usr/bin/env python3
"""
Download or load one or more PDF files referenced in issue context, run OCR if needed,
selectively extract images, optionally split large PDFs, and prepare them for
upload to Google Cloud Storage. The script updates the context JSON with
paths to the final PDFs, their GCS URIs, and extraction policy metadata.

New behavior:
- Entries in ctx["pdf_urls"] may be HTTP(S) URLs OR repo-local paths (e.g., upload-pdf/my.pdf).
- Local paths are resolved relative to the repo root, validated, and copied into a temp workspace.

Selective extraction mode (default) extracts images only from interesting
pages based on heuristics: first few pages, every 20th page, pages that
contain headings, or pages flagged as image heavy. Full extraction can be
triggered by setting the EXTRACT_MODE environment variable to "full".

Large PDFs (>50MB or >1000 pages) are split into chunks so they fit within
Vertex AI size limits. OCR is applied only when the PDF lacks a text layer.

An up-front HEAD request caps the download size to avoid PDF bombs for HTTP(S) inputs.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import fitz
import subprocess
import json
import math
import re
import requests
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from util import mkdirp, http_get, read_json, write_json

MAX_BYTES = 200 * 1024 * 1024  # 200 MB guard against oversized downloads (HTTP and local)


def head_size(url: str, headers: dict) -> int:
    """Return Content-Length of URL or zero if unavailable."""
    try:
        r = requests.head(url, headers=headers, timeout=8, allow_redirects=True)
        cl = r.headers.get("Content-Length")
        return int(cl) if cl and cl.isdigit() else 0
    except Exception:
        return 0


def is_http_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s, re.I))


def resolve_repo_path(path_like: str) -> Path:
    """
    Resolve a repo-local path safely relative to the GitHub Actions checkout (cwd).
    Ensures the resolved file remains inside the repo directory to avoid traversal.
    """
    repo_root = Path.cwd().resolve()
    p = Path(path_like)
    if not p.is_absolute():
        p = repo_root / p
    p = p.resolve()
    # sandbox: ensure p is inside repo_root
    try:
        p.relative_to(repo_root)
    except ValueError:
        raise RuntimeError(f"Refusing to read path outside repo: {p}")
    return p


def quick_text_probe(pdf_path) -> bool:
    """Check if a PDF appears to contain any text layer by sampling pages."""
    try:
        doc = fitz.open(pdf_path)
        for i in range(min(5, len(doc))):
            if doc.load_page(i).get_text().strip():
                doc.close()
                return True
        doc.close()
        return False
    except Exception:
        return False


def ocr_pdf(in_path, out_path):
    """Run OCR on a PDF using ocrmypdf, preserving existing text."""
    subprocess.check_call(["ocrmypdf", "--skip-text", "--quiet", in_path, out_path])


def get_page_count(pdf_path) -> int:
    try:
        d = fitz.open(pdf_path)
        n = len(d)
        d.close()
        return n
    except Exception:
        return 0


def split_pdf_by_pages(pdf_path, out_dir, max_pages=1000, target_mb=50):
    """Split a PDF into chunks that meet page and size limits."""
    doc = fitz.open(pdf_path)
    total = len(doc)
    size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
    # approximate pages per part by size ratio
    pages_by_size = max(1, math.floor(total * (target_mb / max(1e-6, size_mb))))
    pages_per_part = min(max_pages, pages_by_size)
    parts, start = [], 0
    while start < total:
        end = min(total, start + pages_per_part)
        part = fitz.open()
        part.insert_pdf(doc, from_page=start, to_page=end - 1)
        outp = os.path.join(out_dir, f"{Path(pdf_path).stem}-part-{len(parts)+1}.pdf")
        part.save(outp)
        part.close()
        parts.append(outp)
        start = end
    doc.close()
    return parts


# ----- Selective image extraction helpers -----

def page_is_interesting(doc, i):
    """Identify pages that should always be included in selective extraction."""
    if i < 5 or i % 20 == 0:
        return True
    pg = doc.load_page(i)
    txt = pg.get_text("text") or ""
    return bool(re.search(r"^\s{0,3}(Chapter|Section|Table|Figure)\b", txt, re.I | re.M))


def image_heavy(pg):
    """Heuristic to detect if a page has significant imagery."""
    imgs = pg.get_images(full=True)
    txt = pg.get_text("text") or ""
    return (len(imgs) >= 2) or (len(txt) < 200 and len(imgs) >= 1)


def extract_page_images(pdf_path, out_dir, page_index):
    """Extract all images from a single page of a PDF and save them to disk."""
    import fitz as _fitz  # local alias
    doc = _fitz.open(pdf_path)
    pg = doc.load_page(page_index)
    out = []
    for ix, img in enumerate(pg.get_images(full=True)):
        xref = img[0]
        try:
            pix = _fitz.Pixmap(doc, xref)
            if pix.n >= 5:  # e.g., CMYK
                pix = _fitz.Pixmap(_fitz.csRGB, pix)
            name = f"page-{page_index+1}-img-{ix+1}.png"
            path = os.path.join(out_dir, name)
            pix.save(path)
            # guard against zero-byte artifacts
            if os.path.getsize(path) < 1024:
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            out.append(path)
        except Exception:
            # ignore corrupt or unsupported encodings
            continue
    doc.close()
    return out


def selective_extract_images(pdf_path, out_dir, mode="selective"):
    """Extract images from a PDF selectively or fully.

    In selective mode only pages flagged by heuristics are processed. In full
    mode every page is processed. Extraction is parallelised across CPU cores.
    """
    out = []
    doc = fitz.open(pdf_path)
    pages = range(len(doc))
    targets = []
    if mode == "full":
        targets = list(pages)
    else:
        for i in pages:
            pg = doc.load_page(i)
            if page_is_interesting(doc, i) or image_heavy(pg):
                targets.append(i)
    # Use a process pool to parallelise extraction across pages
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
        from functools import partial
        for imgs in ex.map(partial(extract_page_images, pdf_path, out_dir), targets):
            out.extend(imgs)
    doc.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--output-root", required=True)
    a = ap.parse_args()

    ctx = read_json(a.context)
    issue = ctx["issue_number"]
    issue_dir = os.path.join(a.output_root, f"issue-{issue}")
    images_dir = mkdirp(os.path.join(issue_dir, "images"))
    tmp = tempfile.mkdtemp()

    headers = {"Authorization": f"token {os.environ.get('GH_TOKEN','')}"}
    local_pdfs = []

    # --- Build local_pdfs from ctx["pdf_urls"] which may contain URLs or repo paths ---
    for i, u in enumerate(ctx.get("pdf_urls", []), start=1):
        if is_http_url(u):
            # HTTP(S) path: check size and download
            sz = head_size(u, headers)
            if sz and sz > MAX_BYTES:
                raise RuntimeError(f"Refusing to download oversized PDF ({sz} bytes): {u}")
            dest = os.path.join(tmp, f"src-{i}.pdf")
            http_get(u, headers=headers, dest_path=dest)
            local_pdfs.append(dest)
            continue

        # Repo-local path
        p = resolve_repo_path(u)
        if not p.is_file():
            raise RuntimeError(f"PDF path referenced in issue not found: {p}")
        if p.suffix.lower() != ".pdf":
            raise RuntimeError(f"Not a PDF: {p}")
        size = p.stat().st_size
        if size == 0:
            raise RuntimeError(f"PDF is empty: {p}")
        if size > MAX_BYTES:
            raise RuntimeError(f"Refusing to process oversized local PDF ({size} bytes): {p}")

        # Copy into temp workspace (downstream assumes temp copies)
        dest = os.path.join(tmp, f"src-{i}.pdf")
        with open(p, "rb") as src, open(dest, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
        local_pdfs.append(dest)

    # If no PDFs were found at all, fail early with a helpful message
    if not local_pdfs:
        raise RuntimeError(
            "No PDFs found in context. Ensure the issue includes a URL or a repo path like 'upload-pdf/your.pdf'."
        )

    final_pdfs, policy = [], {"chunked": False, "model": None, "reason": ""}
    extract_mode = os.environ.get("EXTRACT_MODE", "selective")

    for p in local_pdfs:
        has_text = quick_text_probe(p)
        outp = os.path.join(tmp, f"final-{os.path.basename(p)}")
        if not has_text:
            ocr_pdf(p, outp)
            use = outp
        else:
            use = p

        need_split = (os.path.getsize(use) > 50 * 1024 * 1024) or (get_page_count(use) > 1000)
        parts = split_pdf_by_pages(use, tmp) if need_split else [use]
        if need_split:
            policy["chunked"] = True

        for part in parts:
            # selective image extraction
            selective_extract_images(part, images_dir, mode=extract_mode)
            final_pdfs.append(part)

        # Simple policy selection (placeholder, unchanged)
        policy["model"] = "gemini-2.5-pro"
        policy["reason"] = "standard_pro"

    ctx["artifact_dir"] = issue_dir
    ctx["final_pdf_paths"] = final_pdfs
    bucket = os.environ.get("GCS_BUCKET", "").replace("gs://", "")
    ctx["gcs_uris"] = [f"gs://{bucket}/issues/{issue}/{Path(p).name}" for p in final_pdfs]
    ctx["policy"] = policy
    write_json(a.context, ctx)


if __name__ == "__main__":
    main()
