#!/usr/bin/env python3
"""
scripts/translation_status.py

Report translation progress across .po files: how many strings are
translated, fuzzy, or untranslated per file, plus overall totals. Meant to
help a contributor quickly find files that need work.

Requires: pip install polib

Usage:
    python3 scripts/translation_status.py                    # whole repo, least-translated first
    python3 scripts/translation_status.py tutorial/           # just one directory
    python3 scripts/translation_status.py --only-incomplete   # hide fully-done files
    python3 scripts/translation_status.py --sort name
    python3 scripts/translation_status.py --sort fuzzy
    python3 scripts/translation_status.py --format markdown > STATUS.md
"""
import argparse
import sys
from pathlib import Path

import polib


def file_stats(path: Path):
    po = polib.pofile(str(path))
    return (
        len(po.translated_entries()),
        len(po.fuzzy_entries()),
        len(po.untranslated_entries()),
    )


def collect_files(paths, exclude=None):
    exclude = {Path(e).resolve() for e in (exclude or [])}
    files = []

    def is_excluded(path):
        resolved = path.resolve()
        return resolved in exclude or any(
            parent in exclude for parent in resolved.parents
        )

    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            if is_excluded(p):
                continue
            for f in p.rglob("*.po"):
                if any(part.startswith(".") for part in f.parts):
                    continue
                if is_excluded(f):
                    continue
                files.append(f)
        elif p.suffix == ".po":
            if not is_excluded(p):
                files.append(p)

    return files


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to scan (default: whole repo)",
    )
    parser.add_argument(
        "--sort",
        choices=["percent", "untranslated", "fuzzy", "name"],
        default="percent",
        help="Sort order (default: percent, least-translated first)",
    )
    parser.add_argument(
        "--only-incomplete",
        action="store_true",
        help="Hide files that are already fully translated",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        help="Exclude files or directories from scan",
    )
    parser.add_argument("--format", choices=["text", "markdown", "csv"], default="text")
    args = parser.parse_args()
    files = collect_files(args.paths, args.exclude)
    if not files:
        print("No .po files found.")
        sys.exit(1)

    rows = []
    for f in files:
        t, fz, u = file_stats(f)
        total = t + fz + u
        percent = (t / total * 100) if total else 100.0
        rows.append((str(f), t, fz, u, total, percent))

    if args.only_incomplete:
        rows = [r for r in rows if r[2] > 0 or r[3] > 0]

    if args.sort == "percent":
        rows.sort(key=lambda r: r[5])
    elif args.sort == "untranslated":
        rows.sort(key=lambda r: -r[3])
    elif args.sort == "fuzzy":
        rows.sort(key=lambda r: -r[2])
    else:
        rows.sort(key=lambda r: r[0])

    total_t = sum(r[1] for r in rows)
    total_fz = sum(r[2] for r in rows)
    total_u = sum(r[3] for r in rows)
    total_all = total_t + total_fz + total_u
    total_percent = (total_t / total_all * 100) if total_all else 100.0

    if args.format == "csv":
        print("file,translated,fuzzy,untranslated,total,percent")
        for path, t, fz, u, total, percent in rows:
            print(f"{path},{t},{fz},{u},{total},{percent:.1f}")
        print(f"TOTAL,{total_t},{total_fz},{total_u},{total_all},{total_percent:.1f}")
        return

    if args.format == "markdown":
        print("| File | Translated | Fuzzy | Untranslated | % done |")
        print("|---|---:|---:|---:|---:|")
        for path, t, fz, u, total, percent in rows:
            print(f"| `{path}` | {t} | {fz} | {u} | {percent:.1f}% |")
        print(
            f"| **TOTAL** | **{total_t}** | **{total_fz}** | **{total_u}** | **{total_percent:.1f}%** |"
        )
        return

    name_width = max((len(r[0]) for r in rows), default=4)
    header = f"{'File':<{name_width}}  {'Translated':>10}  {'Fuzzy':>6}  {'Untranslated':>12}  {'% done':>7}"
    print(header)
    print("-" * len(header))
    for path, t, fz, u, total, percent in rows:
        print(f"{path:<{name_width}}  {t:>10}  {fz:>6}  {u:>12}  {percent:>6.1f}%")
    print("-" * len(header))
    print(
        f"{'TOTAL':<{name_width}}  {total_t:>10}  {total_fz:>6}  {total_u:>12}  {total_percent:>6.1f}%"
    )
    print(
        f"\n{len(rows)} file(s) shown. "
        f"{sum(1 for r in rows if r[5] < 100)} file(s) not fully translated."
    )


if __name__ == "__main__":
    main()