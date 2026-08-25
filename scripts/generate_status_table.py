#!/usr/bin/env python3
"""
scripts/generate_status_table.py

Regenerate the per-file translation status table between marker comments in
a markdown file (default: STATUS.md) from the current state of all .po
files. Meant to run on a schedule so the table never goes stale, replacing
the old Transifex-exported report.

Requires: pip install polib

Usage:
    python3 scripts/generate_status_table.py
    python3 scripts/generate_status_table.py --file STATUS.md
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

import polib

REPO_ROOT = Path(__file__).resolve().parent.parent
START_MARKER = "<!-- TRANSLATION_STATUS_START -->"
END_MARKER = "<!-- TRANSLATION_STATUS_END -->"


def file_stats(path: Path):
    po = polib.pofile(str(path))
    return (
        len(po.translated_entries()),
        len(po.fuzzy_entries()),
        len(po.untranslated_entries()),
    )


def build_table() -> str:
    files = sorted(REPO_ROOT.rglob("*.po"))
    rows = []
    totals = {"translated": 0, "fuzzy": 0, "untranslated": 0}
    for f in files:
        if ".cpython-src" in f.parts or ".git" in f.parts:
            continue
        t, fz, u = file_stats(f)
        total = t + fz + u
        translated_pct = (t / total * 100) if total else 100.0
        fuzzy_pct = (fz / total * 100) if total else 0.0
        rel = f.relative_to(REPO_ROOT)
        rows.append((str(rel), translated_pct, fuzzy_pct, t, u, total))

        totals["translated"] += t
        totals["fuzzy"] += fz
        totals["untranslated"] += u

    # most-complete files first, matching the old report's ordering
    rows.sort(key=lambda r: (-r[1], r[0]))

    lines = [
        "| فایل | ترجمه‌شده | مبهم | تعداد ترجمه‌شده | تعداد ترجمه‌نشده |",
        "|:-----|:-----------:|:-----------:|:-----------:|:-----------:|",
    ]
    for name, t_pct, f_pct, t_count, u_count, _total in rows:
        lines.append(
            f"| {name} | {t_pct:.1f}% | {f_pct:.1f}% | {t_count} | {u_count} |"
        )

    grand_total = sum(totals.values())
    lines.append(
        f"| **مجموع** | "
        f"**{(totals['translated'] / grand_total * 100) if grand_total else 100.0:.1f}%** | "
        f"**{(totals['fuzzy'] / grand_total * 100) if grand_total else 0.0:.1f}%** | "
        f"**{totals['translated']}** | **{totals['untranslated']}** |"
    )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        default="STATUS.md",
        help="Markdown file containing the marker block to update",
    )
    args = parser.parse_args()

    target = REPO_ROOT / args.file
    text = target.read_text(encoding="utf-8")

    if START_MARKER not in text or END_MARKER not in text:
        print(f"Markers {START_MARKER} / {END_MARKER} not found in {args.file}.")
        print("Add them around the table you want auto-generated, then re-run.")
        sys.exit(1)

    table = build_table()
    block = (
        f"{START_MARKER}\n"
        f"### وضعیت ترجمه فایل‌ها\n"
        f"(به‌روزرسانی: {date.today().isoformat()})\n\n"
        f"{table}\n"
        f"{END_MARKER}"
    )

    new_text = re.sub(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        lambda _: block,  # avoid backslash-escape surprises from re.sub
        text,
        flags=re.DOTALL,
    )
    target.write_text(new_text, encoding="utf-8")
    print(f"Updated {args.file}.")


if __name__ == "__main__":
    main()
