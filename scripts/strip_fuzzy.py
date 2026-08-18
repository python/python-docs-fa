#!/usr/bin/env python3
"""Remove '#, fuzzy' marker lines from .po files.

CPython's Tools/i18n/msgfmt.py treats a stray fuzzy flag on the file
header as applying to every entry in the file, silently producing an
empty .mo catalog. Since our translations are complete (not actually
rough drafts), we strip the marker before compiling.
"""
import sys


def strip_fuzzy(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    kept = [line for line in lines if not line.lstrip().startswith("#, fuzzy")]

    if kept != lines:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)


def main() -> None:
    for path in sys.argv[1:]:
        strip_fuzzy(path)


if __name__ == "__main__":
    main()
