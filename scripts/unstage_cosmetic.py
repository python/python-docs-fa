#!/usr/bin/env python3
"""
scripts/unstage_cosmetic.py

Unstage (and revert) any currently-staged file whose only diff is the
`POT-Creation-Date` header line. Ported out of the nightly workflow's
inline bash/awk step so the manual version-bump script can produce the
same minimal, noise-free diffs (item 3) -- previously only the workflow
did this, so a human running update_python_version.py and committing
with `git commit -am` would bake pure-timestamp churn into history,
which then became a spurious baseline for the *next* nightly diff too.

Usage:
    python scripts/unstage_cosmetic.py

Must be run with a git repo that already has changes staged (e.g. after
`git add --all`).
"""
from __future__ import annotations

import subprocess
import sys


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def staged_files() -> list:
    result = run(["git", "diff", "--staged", "--name-only"])
    return [line for line in result.stdout.splitlines() if line]


def has_only_cosmetic_diff(path: str) -> bool:
    """True if every changed (+/-) content line in the staged diff for
    `path` is a POT-Creation-Date line (i.e. nothing else changed)."""
    result = subprocess.run(
        ["git", "diff", "--staged", "-U0", "--", path],
        capture_output=True, text=True,
    )
    changed_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    if not changed_lines:
        return True
    return all("POT-Creation-Date" in line for line in changed_lines)


def main() -> None:
    files = staged_files()
    if not files:
        print("No staged files.")
        return

    unstaged = []
    for path in files:
        if has_only_cosmetic_diff(path):
            run(["git", "restore", "--staged", path])
            run(["git", "restore", path])
            unstaged.append(path)

    if unstaged:
        print(f"Unstaged {len(unstaged)} file(s) with only POT-Creation-Date changes:")
        for path in unstaged:
            print(f"  - {path}")
    else:
        print("No cosmetic-only files to unstage.")


if __name__ == "__main__":
    main()