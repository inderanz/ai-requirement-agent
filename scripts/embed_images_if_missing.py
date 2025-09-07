#!/usr/bin/env python3
import sys, re
from pathlib import Path

def main():
    if len(sys.argv) != 3:
        print("usage: embed_images_if_missing.py <report.md> <images_dir>")
        sys.exit(1)
    report_path = Path(sys.argv[1])
    images_dir  = Path(sys.argv[2])

    if not report_path.exists():
        print(f"ERR: report not found: {report_path}")
        sys.exit(2)
    if not images_dir.exists():
        print(f"INFO: images dir not found, nothing to embed: {images_dir}")
        sys.exit(0)

    text = report_path.read_text(encoding="utf-8")

    # collect existing image embeds
    embeds = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    has_images_section = "Extracted Figures" in text

    # normalize bad embeds pointing to plain filenames -> images/<file>
    changed = False
    def _fix_embed(m):
        nonlocal changed
        path = m.group(1)
        if not (path.startswith("images/") or path.startswith("./images/")):
            fname = Path(path).name
            if (images_dir / fname).exists():
                changed = True
                return f"![]({images_dir.name}/{fname})"
        return m.group(0)

    text2 = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", _fix_embed, text)

    # If still no embeds, append a section with all images
    embeds_after = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text2)
    if not embeds_after:
        lines = []
        lines.append("\n\n## Extracted Figures\n")
        for p in sorted(images_dir.glob("*")):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
                rel = f"{images_dir.name}/{p.name}"
                lines.append(f"![]({rel})\n")
        if len(lines) > 2:
            text2 += "\n".join(lines)
            changed = True

    if changed:
        report_path.write_text(text2, encoding="utf-8")
        print("OK: embedded/normalized image references.")
    else:
        print("OK: image embeds already present or no images to embed.")

if __name__ == "__main__":
    main()
