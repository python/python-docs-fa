#!/usr/bin/env python3
"""
Decode percent-encoded URLs inside .po file(s) (e.g. Persian Wikipedia
links like https://fa.wikipedia.org/wiki/%D9%85%D8%AD%D9%85%D9%88%D9%84)
into plain UTF-8 text. This avoids translation-checker tools mistaking
sequences like %D9, %A7, %E2 for printf-style placeholders (%D, %A, %E).

Usage:
    python3 decode_po_urls.py input.po [output.po]
    python3 decode_po_urls.py some_dir/ [output_dir/]
    python3 decode_po_urls.py some_dir/ --recursive

If a single input.po is given with no output.po, the file is edited in
place. If a directory is given with no output_dir, all *.po files in it
are edited in place (non-recursively by default; pass --recursive / -r
to also descend into subdirectories). If an output_dir is given, fixed
copies are written there (mirroring the relative paths found), and the
originals are left untouched.
"""

import re
import sys
from pathlib import Path
from urllib.parse import unquote

URL_RE = re.compile(r'https?://[^\s`<>"]+')


def decode_urls_in_url(url: str) -> str:
    if "%" not in url:
        return url
    return unquote(url)


def decode_urls(text: str) -> str:
    """Decode percent-encoded URLs, but only inside msgstr blocks.

    A .po file alternates msgid "..." / msgstr "..." blocks, each possibly
    spanning multiple quoted-string lines. We only want to touch the
    translated (msgstr) text -- the msgid must stay identical to the
    original source string.
    """
    lines = text.split("\n")
    out_lines = []
    in_msgstr = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("msgid"):
            in_msgstr = False
        elif stripped.startswith("msgstr"):
            in_msgstr = True
        elif not stripped.startswith('"'):
            # Any other line (comments, blank lines, flags) ends the block.
            in_msgstr = False

        if in_msgstr:
            line = URL_RE.sub(lambda m: decode_urls_in_url(m.group(0)), line)

        out_lines.append(line)

    return "\n".join(out_lines)


def process_file(in_path: Path, out_path: Path) -> int:
    content = in_path.read_text(encoding="utf-8")
    new_content = decode_urls(content)

    n_changed = sum(
        1 for a, b in zip(content.splitlines(), new_content.splitlines()) if a != b
    )

    if n_changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(new_content, encoding="utf-8")
        print(f"Decoded URLs on {n_changed} line(s): {in_path} -> {out_path}")
    elif out_path != in_path:
        # No changes, but writing to a different location: still copy it
        # over so the output directory mirrors the full input set.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

    return n_changed


def main() -> None:
    args = [a for a in sys.argv[1:] if a not in ("--recursive", "-r")]
    recursive = any(a in ("--recursive", "-r") for a in sys.argv[1:])

    if not args:
        print(__doc__)
        sys.exit(1)

    in_path = Path(args[0])
    out_arg = Path(args[1]) if len(args) > 1 else None

    if not in_path.exists():
        print(f"No such file or directory: {in_path}")
        sys.exit(1)

    total_files = 0
    total_changed = 0

    if in_path.is_file():
        out_path = out_arg if out_arg else in_path
        total_changed += process_file(in_path, out_path)
        total_files += 1
    else:
        pattern = "**/*.po" if recursive else "*.po"
        po_files = sorted(in_path.glob(pattern))
        if not po_files:
            print(f"No .po files found in {in_path}"
                  f"{' (recursively)' if recursive else ''}.")
            sys.exit(0)

        for f in po_files:
            rel = f.relative_to(in_path)
            out_path = (out_arg / rel) if out_arg else f
            total_changed += process_file(f, out_path)
            total_files += 1

    print(f"\nDone. Scanned {total_files} file(s).")


if __name__ == "__main__":
    main()