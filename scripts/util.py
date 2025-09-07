"""
util.py
Minimal utilities for the PDF pipeline.

This module provides helper functions for:
  - Creating directories (mkdirp)
  - Downloading files over HTTP(S) (http_get)  ← no GitHub CLI fallback
  - JSON read/write helpers
  - Simple file checks and image listing helpers

Notes:
- The previous GitHub user-attachments fallback via `gh api` has been removed.
- For the new flow, PDFs are committed to the repo (e.g., upload-pdf/*.pdf) and
  read locally by the pipeline. http_get remains for any plain public URLs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import requests

__all__ = [
    "mkdirp",
    "http_get",
    "write_json",
    "read_json",
    "file_nonempty",
    "list_images_nonempty",
]


def mkdirp(path: str) -> str:
    """Create a directory path if it doesn't exist; return the path."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def http_get(
    url: str,
    headers: Optional[dict] = None,
    dest_path: Optional[Union[str, Path]] = None,
    timeout: int = 120,
    chunk_size: int = 1024 * 1024,
) -> Union[bytes, str]:
    """
    Download a URL to memory (bytes) or to a file (returns dest path).

    Simplified downloader for plain HTTP(S) URLs.
    - Follows redirects.
    - No special handling for private GitHub attachments.

    Args:
        url: The HTTP(S) URL to fetch.
        headers: Optional HTTP headers.
        dest_path: If provided, stream to this path and return the path (str).
                   Otherwise return bytes.
        timeout: Per-request timeout (seconds).
        chunk_size: Streaming chunk size in bytes.

    Returns:
        str: dest_path if provided, else bytes with the content.

    Raises:
        requests.HTTPError for non-2xx responses.
    """
    headers = headers or {}
    with requests.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as r:
        r.raise_for_status()
        if dest_path:
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
            return str(dest)
        return r.content


def write_json(path: str, obj) -> None:
    """Write JSON data to a file with pretty indentation."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def read_json(path: str):
    """Read and return JSON data from a file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def file_nonempty(p: Path) -> bool:
    """Return True if the path exists, is a file, and has size > 0."""
    try:
        return p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


def list_images_nonempty(img_dir: Path):
    """
    Return a list of non-empty image files in img_dir (case-insensitive extensions).

    Supported extensions: .png, .jpg, .jpeg, .gif, .svg
    If the directory does not exist, returns an empty list.
    """
    img_dir = Path(img_dir)
    if not img_dir.exists():
        return []
    supported = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
    return [
        p
        for p in sorted(img_dir.glob("*"))
        if p.suffix.lower() in supported and file_nonempty(p)
    ]
