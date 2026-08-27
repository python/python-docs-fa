#!/usr/bin/env python3
"""
scripts/update_python_version.py

Sync this repo's .po files against a target CPython release tag.

Usage:
    python scripts/update_python_version.py v3.14.6
    python scripts/update_python_version.py v3.15.0 --keep-src

Run this from anywhere inside the repo (it locates the repo root from
this file's location, assuming it lives in <repo>/scripts/).
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKDIR = REPO_ROOT / ".cpython-src"  # scratch clone, deleted by default when done


def run(cmd, cwd=None):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def fetch_cpython(tag: str) -> None:
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "https://github.com/python/cpython.git",
            str(WORKDIR),
        ]
    )


def build_gettext(tag: str) -> Path:
    """Build .pot templates from the CPython docs at `tag`, return their root dir."""
    doc_dir = WORKDIR / "Doc"
    venv_dir = doc_dir / "venv"
    run([sys.executable, "-m", "venv", str(venv_dir)])
    pip = venv_dir / "bin" / "pip"
    sphinx_build = venv_dir / "bin" / "sphinx-build"
    run([str(pip), "install", "-r", "requirements.txt"], cwd=doc_dir)
    run([str(sphinx_build), "-b", "gettext", ".", "build/gettext"], cwd=doc_dir)
    return doc_dir / "build" / "gettext"


def merge_all(pot_root: Path) -> None:
    updated = new_po = missing_pot = 0

    # existing .po files -> merge against matching .pot by relative path
    for po_path in sorted(REPO_ROOT.rglob("*.po")):
        if ".cpython-src" in po_path.parts or ".git" in po_path.parts:
            continue
        rel = po_path.relative_to(REPO_ROOT)
        pot_path = pot_root / rel.with_suffix(".pot")
        if not pot_path.exists():
            print(
                f"  ! no matching .pot for {rel} "
                f"(page may have been removed/renamed upstream — review manually)"
            )
            missing_pot += 1
            continue
        run(["msgmerge", "--update", "--backup=off", str(po_path), str(pot_path)])
        updated += 1

    # brand-new .pot files with no .po counterpart yet -> create empty .po via msginit
    for pot_path in sorted(pot_root.rglob("*.pot")):
        rel = pot_path.relative_to(pot_root)
        po_path = REPO_ROOT / rel.with_suffix(".po")
        if not po_path.exists():
            po_path.parent.mkdir(parents=True, exist_ok=True)
            run(
                [
                    "msginit",
                    "--no-translator",
                    "-l",
                    "fa",
                    "-i",
                    str(pot_path),
                    "-o",
                    str(po_path),
                ]
            )
            new_po += 1

    print(
        f"\nSummary: {updated} .po files merged, {new_po} new .po files created, "
        f"{missing_pot} .po files with no matching upstream source."
    )


def check_po_files() -> None:
    bad = []
    for po_path in sorted(REPO_ROOT.rglob("*.po")):
        if ".cpython-src" in po_path.parts or ".git" in po_path.parts:
            continue
        result = subprocess.run(
            ["msgfmt", "--check", "-o", "/dev/null", str(po_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            bad.append((po_path, result.stderr.strip()))

    if bad:
        print("\nBroken .po files (fix before committing):")
        for path, err in bad:
            print(f"  {path}:\n    {err}")
        sys.exit(1)
    print("\nAll .po files pass `msgfmt --check`.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="CPython git tag to sync against, e.g. v3.14.6")
    parser.add_argument(
        "--keep-src",
        action="store_true",
        help="keep the scratch CPython checkout instead of deleting it",
    )
    args = parser.parse_args()

    print(f"== Fetching CPython {args.tag} ==")
    fetch_cpython(args.tag)

    print("\n== Building gettext templates ==")
    pot_root = build_gettext(args.tag)

    print("\n== Merging into this repo's .po files ==")
    merge_all(pot_root)

    print("\n== Validating .po files ==")
    check_po_files()

    if not args.keep_src:
        shutil.rmtree(WORKDIR, ignore_errors=True)

    print(
        f"\nDone. Review the diff, then commit as something like:\n"
        f'  git commit -am "Sync translations with CPython {args.tag}"'
    )


if __name__ == "__main__":
    main()
