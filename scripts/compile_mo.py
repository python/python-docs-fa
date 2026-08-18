#!/usr/bin/env python3
"""Strip stray '#, fuzzy' markers and compile .po files to .mo, in one pass.

CPython's Tools/i18n/msgfmt.py treats a fuzzy flag on the file header as
applying to every entry in the file, silently producing an empty .mo
catalog. Since our translations are complete (not actually rough
drafts), we strip the marker before compiling.

Compilation calls msgfmt.make() directly instead of spawning a fresh
Python interpreter per file, since interpreter startup is the dominant
cost when compiling hundreds of small .po files.

Reads paths from stdin (one per line, e.g. via `find ... | python3
compile_mo.py`).
"""
import sys
import os

# venv/cpython/Tools/i18n/msgfmt.py, relative to repo root.
sys.path.insert(0, os.path.join("venv", "cpython", "Tools", "i18n"))
import msgfmt  # noqa: E402


def strip_fuzzy(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    kept = [line for line in lines if not line.lstrip().startswith("#, fuzzy")]

    if kept != lines:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)


def compile_po(path: str) -> None:
    msgfmt.MESSAGES = {}
    out = path[:-3] + ".mo" if path.endswith(".po") else path + ".mo"
    msgfmt.make(path, out)


def process(path: str) -> None:
    strip_fuzzy(path)
    compile_po(path)


def main() -> None:
    for line in sys.stdin:
        path = line.strip()
        if path:
            process(path)


if __name__ == "__main__":
    main()
