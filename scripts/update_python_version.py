#!/usr/bin/env python3
"""
scripts/update_python_version.py

Sync this repo's .po files against a target CPython release tag. This is
the manual, deliberate, human-reviewed sync -- e.g. bumping from 3.14.6 to
3.14.7 -- as opposed to .github/workflows/sync-with-cpython.yml, which runs
nightly and only merges into .po files that already exist.

This script does the full job: full clone (so every .pot can be built,
including for brand-new upstream pages), merge into existing .po files,
AND create fresh .po files (via msginit) for any new pages. It shares its
core logic (fetch/build/merge/validate) with the workflow via
scripts/po_sync.py, so both paths run msgmerge with identical flags and
neither can silently drift from the other.

Usage:
    python scripts/update_python_version.py v3.14.7
    python scripts/update_python_version.py v3.15.0 --keep-src
    python scripts/update_python_version.py v3.14.7 --no-commit-filter
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import po_sync  # noqa: E402
import unstage_cosmetic  # noqa: E402

REPO_ROOT = po_sync.REPO_ROOT
WORKDIR = REPO_ROOT / ".cpython-src"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", help="CPython git tag to sync against, e.g. v3.14.7")
    parser.add_argument("--keep-src", action="store_true",
                         help="keep the scratch CPython checkout instead of deleting it")
    parser.add_argument("--locale", default="fa",
                         help="locale code for new .po files (default: fa)")
    parser.add_argument("--no-commit-filter", action="store_true",
                         help="skip unstaging POT-Creation-Date-only changes "
                              "(item 3) -- useful if you want to inspect the raw diff")
    args = parser.parse_args()

    print(f"== Fetching CPython {args.tag} (full clone) ==")
    po_sync.fetch_cpython_full(args.tag, WORKDIR)

    print("\n== Building gettext templates ==")
    pot_root = po_sync.build_gettext(WORKDIR / "Doc")

    print("\n== Merging into this repo's .po files ==")
    report = po_sync.merge_all(pot_root, create_new=True, locale=args.locale)
    print(f"\n{report.summary()}")
    if report.new_po_created:
        print("New .po files created for upstream pages:")
        for rel in report.new_po_created:
            print(f"  - {rel}")

    print("\n== Validating .po files ==")
    bad = po_sync.check_po_files()
    if bad:
        print("\nBroken .po files (fix before committing):")
        for path, err in bad:
            print(f"  {path}:\n    {err}")
        sys.exit(1)
    print("All .po files pass `msgfmt --check`.")

    if not args.keep_src:
        shutil.rmtree(WORKDIR, ignore_errors=True)

    # Item 3: keep the same "don't commit timestamp-only churn" behavior
    # the workflow already had, so this manual path produces an equally
    # clean diff/baseline for the next nightly run.
    print("\n== Staging changes ==")
    subprocess.run(["git", "add", "--all"], cwd=REPO_ROOT, check=True)

    if not args.no_commit_filter:
        print("\n== Unstaging POT-Creation-Date-only changes ==")
        unstage_cosmetic.main()

    print(
        f"\nDone. Review the diff, then commit as something like:\n"
        f'  git commit -m "Sync translations with CPython {args.tag}"'
    )


if __name__ == "__main__":
    main()