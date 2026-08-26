#!/usr/bin/env python3
"""
scripts/po_sync.py

Shared logic for syncing this repo's .po files against a CPython docs
checkout. Used by both:

  - scripts/update_python_version.py  (manual, deliberate version bumps,
    full clone, creates new .po files for brand-new pages)
  - .github/workflows/sync-with-cpython.yml (nightly automated msgid sync,
    sparse checkout, merge-only, opens an issue for fuzzy strings)

Keeping this logic in one place means both paths build .pot files and run
msgmerge/msgfmt identically -- no more silent flag drift (e.g. one path
passing --no-location --no-wrap and the other not), which otherwise shows
up as spurious rewrap-only diffs on whichever path runs next.

This module is a library first, CLI second. As a CLI it exposes just the
"mechanical middle" of the sync -- build .pot templates, merge them into
existing .po files, flag new upstream pages with no .po yet, validate --
so the GitHub Actions workflow can shell out to one command instead of
reimplementing the loop in bash/awk.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Flags msgmerge is run with everywhere. Keeping this in one constant is the
# whole point: previously the script omitted --no-location --no-wrap while
# the workflow included them, so whichever ran second would produce a huge
# rewrap-only diff on top of (and obscuring) any real content changes.
MSGMERGE_FLAGS = ["--update", "--backup=off", "--no-location", "--no-wrap"]

IGNORED_DIR_NAMES = {".git", ".cpython-src", ".pot-templates"}


def run(cmd: list, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in path.parts)


def iter_po_files(repo_root: Path = REPO_ROOT):
    for po_path in sorted(repo_root.rglob("*.po")):
        if not _is_ignored(po_path.relative_to(repo_root)):
            yield po_path


# ---------------------------------------------------------------------------
# Fetch + build .pot templates
# ---------------------------------------------------------------------------

def fetch_cpython_full(tag: str, workdir: Path) -> None:
    """Full clone of CPython at `tag`. Used by the version-bump script,
    which needs the full doc tree to build every .pot (including ones for
    brand-new pages that a sparse checkout might not anticipate)."""
    if workdir.exists():
        shutil.rmtree(workdir)
    run(["git", "clone", "--depth", "1", "--branch", tag,
         "https://github.com/python/cpython.git", str(workdir)])


def fetch_cpython_sparse(tag: str, workdir: Path) -> None:
    """Sparse, blobless clone of just Doc/ + Include/. Used by the nightly
    workflow, where we only ever merge into .po files that already exist,
    so we don't need the rest of the tree."""
    if workdir.exists():
        shutil.rmtree(workdir)
    run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
         "--branch", tag, "https://github.com/python/cpython.git", str(workdir)])
    run(["git", "sparse-checkout", "set", "Doc", "Include"], cwd=workdir)


def build_gettext(doc_dir: Path) -> Path:
    """Build .pot templates from a CPython Doc/ checkout, return their root dir."""
    venv_dir = doc_dir / "venv"
    run([sys.executable, "-m", "venv", str(venv_dir)])
    pip = venv_dir / "bin" / "pip"
    sphinx_build = venv_dir / "bin" / "sphinx-build"
    run([str(pip), "install", "-r", "requirements.txt"], cwd=doc_dir)
    pot_root = doc_dir / "build" / "gettext"
    run([str(sphinx_build), "-b", "gettext", ".", str(pot_root)], cwd=doc_dir)
    return pot_root


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

@dataclass
class MergeReport:
    updated: list = field(default_factory=list)
    new_po_created: list = field(default_factory=list)
    missing_pot: list = field(default_factory=list)  # .po with no matching .pot upstream
    new_pot_no_po: list = field(default_factory=list)  # .pot with no .po yet (new upstream page)

    def summary(self) -> str:
        lines = [
            f"{len(self.updated)} .po files merged",
            f"{len(self.new_po_created)} new .po files created",
            f"{len(self.missing_pot)} .po files with no matching upstream source "
            f"(page may have been removed/renamed upstream)",
            f"{len(self.new_pot_no_po)} new upstream .pot files with no .po yet",
        ]
        return "Summary: " + ", ".join(lines)


def merge_existing(pot_root: Path, repo_root: Path = REPO_ROOT) -> MergeReport:
    """Merge new .pot content into every existing .po file. This is the
    core operation both the nightly workflow and the version-bump script
    need, and previously the only one the workflow performed."""
    report = MergeReport()
    for po_path in iter_po_files(repo_root):
        rel = po_path.relative_to(repo_root)
        pot_path = pot_root / rel.with_suffix(".pot")
        if not pot_path.exists():
            print(f"  ! no matching .pot for {rel} "
                  f"(page may have been removed/renamed upstream -- review manually)")
            report.missing_pot.append(rel)
            continue
        run(["msgmerge", *MSGMERGE_FLAGS, str(po_path), str(pot_path)])
        report.updated.append(rel)
    return report


def detect_new_pot_files(pot_root: Path, repo_root: Path = REPO_ROOT) -> list:
    """Find .pot files with no corresponding .po file yet -- i.e. pages
    added upstream since the last sync. Both entry points can call this;
    only the version-bump script actually creates the .po (see
    create_po_for_new_pot), but the nightly workflow can now at least
    *report* these instead of silently dropping them (item 1)."""
    new_pot = []
    for pot_path in sorted(pot_root.rglob("*.pot")):
        rel = pot_path.relative_to(pot_root)
        po_path = repo_root / rel.with_suffix(".po")
        if not po_path.exists():
            new_pot.append(rel)
    return new_pot


def create_po_for_new_pot(pot_root: Path, rel_pot_paths: list, locale: str = "fa",
                           repo_root: Path = REPO_ROOT) -> list:
    """Create a fresh .po (via msginit) for each given new .pot. Only called
    from the version-bump script -- the nightly workflow reports these via
    detect_new_pot_files() but leaves creation to a human-reviewed run."""
    created = []
    for rel in rel_pot_paths:
        pot_path = pot_root / rel
        po_path = repo_root / rel.with_suffix(".po")
        po_path.parent.mkdir(parents=True, exist_ok=True)
        run(["msginit", "--no-translator", "-l", locale,
             "-i", str(pot_path), "-o", str(po_path)])
        created.append(rel)
    return created


def merge_all(pot_root: Path, create_new: bool, locale: str = "fa",
               repo_root: Path = REPO_ROOT) -> MergeReport:
    report = merge_existing(pot_root, repo_root)
    new_pot = detect_new_pot_files(pot_root, repo_root)
    if create_new:
        report.new_po_created = create_po_for_new_pot(pot_root, new_pot, locale, repo_root)
    else:
        report.new_pot_no_po = new_pot
    return report


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def check_po_files(repo_root: Path = REPO_ROOT) -> list:
    """Run msgfmt --check on every .po file. Returns a list of (path, stderr)
    for any that fail; empty list means all good."""
    bad = []
    for po_path in iter_po_files(repo_root):
        result = subprocess.run(
            ["msgfmt", "--check", "-o", "/dev/null", str(po_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            bad.append((po_path, result.stderr.strip()))
    return bad


# ---------------------------------------------------------------------------
# CLI -- the "sync-only" mode the workflow shells out to (item 4)
# ---------------------------------------------------------------------------

def _cli_sync_only(args: argparse.Namespace) -> int:
    """Sparse clone + build gettext + merge into existing .po files +
    validate. This is everything the nightly workflow needs, in one call,
    instead of inline bash/awk. Report-only for new upstream pages (does
    NOT create new .po files -- that stays a deliberate, human-run action
    via update_python_version.py)."""
    workdir = REPO_ROOT / ".cpython-src"
    tag = args.tag

    print(f"== Sparse-fetching CPython {tag} ==")
    fetch_cpython_sparse(tag, workdir)

    print("\n== Building gettext templates ==")
    pot_root = build_gettext(workdir / "Doc")

    print("\n== Merging into existing .po files ==")
    report = merge_all(pot_root, create_new=False)
    print(f"\n{report.summary()}")
    if report.new_pot_no_po:
        print("\nNew upstream pages with no .po yet (run update_python_version.py to create):")
        for rel in report.new_pot_no_po:
            print(f"  - {rel}")

    print("\n== Validating .po files ==")
    bad = check_po_files()
    if bad:
        print("\nBroken .po files (fix before committing):")
        for path, err in bad:
            print(f"  {path}:\n    {err}")

    if not args.keep_src:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(REPO_ROOT / ".pot-templates", ignore_errors=True)

    if bad:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser(
        "sync-only",
        help="Sparse-checkout sync used by the nightly workflow: fetch, "
             "build gettext, merge into existing .po files, report new "
             "upstream pages, validate. Does not create new .po files.",
    )
    sync.add_argument("tag", help="CPython git tag to sync against, e.g. v3.14.7")
    sync.add_argument("--keep-src", action="store_true",
                       help="keep the scratch CPython checkout instead of deleting it")
    sync.set_defaults(func=_cli_sync_only)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()