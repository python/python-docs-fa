#!/usr/bin/env python3
"""
scripts/update_po_headers.py

Regenerate the gettext header credits of .po files from git history:

* the ``# Translators:`` comment block -- every unique author who has ever
  committed to the file, oldest first, with the year of their most recent
  change to it;
* the ``Last-Translator:`` metadata field -- the author of the most recent
  commit.

Because contributors come and go through pull requests, attribution can be
rebuilt from the real git history instead of being hand-maintained.

With ``--merge`` the existing ``# Translators:`` entries are kept and git
identities are only *added*, so credits recorded before the git era (e.g.
on Transifex) are preserved alongside the git-derived ones.

Optionally rewrite the ``Language-Team:`` field too, e.g. to drop a stale
Transifex URL after abandoning Transifex. Use ``--no-credits`` for a
header-only fix that leaves the translator notes untouched.

Unlike a polib round-trip (which re-wraps every msgid/msgstr), this script
edits only the header, so translations and their line wrapping are
preserved byte-for-byte.

Automated accounts (GitHub Actions, Transifex sync jobs, ``[bot]`` users)
are excluded from the credits.

Requires: git history for the repo.

Usage:
    python3 scripts/update_po_headers.py                                  # whole repo
    python3 scripts/update_po_headers.py library/functions.po             # specific path(s)
    python3 scripts/update_po_headers.py tutorial/                        # a directory
    python3 scripts/update_po_headers.py --dry-run                        # just show changes
    python3 scripts/update_po_headers.py --merge                          # keep existing names, add git ones
    python3 scripts/update_po_headers.py --no-credits \\
        --language-team "Persian (https://github.com/revisto/python-docs-fa/)" \\
        bugs.po tutorial/ library/functions.po
"""
import argparse
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BOT_AUTHOR_RE = re.compile(r"(?i)(github[^\w]*action|\[bot\])")
BOT_EMAIL_RE = re.compile(
    r"(?i)(\[bot\]|@users\.noreply\.github\.com$|\+github-actions)"
)
TRANSLATOR_LINE_RE = re.compile(r"^# .+, \d{4}$")
LAST_TRANSLATOR_RE = re.compile(r'^(\s*)"Last-Translator: .*\\n"\s*$')
LANGUAGE_TEAM_START_RE = re.compile(r'^\s*"Language-Team: (.*)$')


def git_history(rel_path: str):
    """Return chronological (oldest first) ``(name <email>, year)`` rows for a file."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "log",
            "--format=%an|%ae|%ad",
            "--date=short",
            "--",
            rel_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git log failed for {rel_path}: {result.stderr.strip()}")
    rows = []
    for line in (l for l in result.stdout.splitlines() if l):
        name, email, date = line.rsplit("|", 2)
        if BOT_AUTHOR_RE.search(name) or BOT_EMAIL_RE.search(email):
            continue
        rows.append((f"{name} <{email}>", date[:4]))
    rows.reverse()
    return rows


def translate_block(rows):
    """Split history into (ordered identity -> year-of-last-change, last author)."""
    seen = {}
    for identity, year in rows:
        seen[identity] = year  # dict keeps insertion (oldest-first) order
    return list(seen.items()), (rows[-1] if rows else None)


def update_comment_block(comment_lines, translators):
    """Rebuild the ``# Translators:`` section of the header comments."""
    lines = list(comment_lines)
    idx = next((i for i, l in enumerate(lines) if l.strip() == "# Translators:"), None)
    listing = ["# Translators:"] + [
        f"# {identity}, {year}" for identity, year in translators
    ]

    if idx is not None:
        j = idx + 1
        while j < len(lines) and TRANSLATOR_LINE_RE.match(lines[j]):
            j += 1
        lines[idx:j] = listing if translators else []
        return lines

    if not translators:
        return lines

    # No Translators section yet: drop it in just before the "#, fuzzy" flag
    # (if any), otherwise at the end of the comment block.
    flag = next(
        (i for i, l in enumerate(lines) if l.lstrip().startswith("#,")), len(lines)
    )
    separated = flag > 0 and lines[flag - 1] in ("", "#")
    block = listing + ["#"]
    if not separated:
        block.insert(0, "#")
    lines[flag:flag] = block
    return lines


def existing_translators(comment_lines):
    """Parse the current ``# Translators:`` entries.

    Returns ``(entries, idx, end)`` where ``entries`` is a list of
    ``(raw_line, key)`` pairs and ``idx``/``end`` delimit the comment-line
    span of the section.  ``key`` is ``("email", ...)`` when the entry has
    an email address, otherwise ``("name", ...)``.
    """
    entries = []
    idx = next(
        (i for i, l in enumerate(comment_lines) if l.strip() == "# Translators:"), None
    )
    if idx is None:
        return [], None, None
    j = idx + 1
    while j < len(comment_lines) and TRANSLATOR_LINE_RE.match(comment_lines[j]):
        m = re.match(r"^# (.+?), \d{4}$", comment_lines[j])
        if m:
            identity = m.group(1).strip()
            em = re.search(r"<([^>]+)>", identity)
            key = (
                ("email", em.group(1).strip().lower())
                if em
                else ("name", identity.lower())
            )
            entries.append((comment_lines[j], key))
        j += 1
    return entries, idx, j


def merge_comment_block(comment_lines, translators):
    """Merge git-derived credits into the existing ``# Translators:`` block.

    Existing entries are kept verbatim (preserving Transifex-era credits
    that git history no longer records); git identities not already present
    are appended.  Matching is by email address, or by name when an
    existing entry has no email.  A missing section is created if needed.
    """
    lines = list(comment_lines)
    existing, idx, end = existing_translators(lines)

    known = {key for _, key in existing}
    additions = []
    for identity, year in translators:
        em = re.search(r"<([^>]+)>", identity)
        key = (
            ("email", em.group(1).strip().lower()) if em else ("name", identity.lower())
        )
        if key in known:
            continue
        known.add(key)
        additions.append(f"# {identity}, {year}")

    listing = ["# Translators:"] + [line for line, _ in existing] + additions
    if idx is not None:
        lines[idx:end] = listing if existing or additions else []
        return lines
    if not additions:
        return lines
    flag = next(
        (i for i, l in enumerate(lines) if l.lstrip().startswith("#,")), len(lines)
    )
    separated = flag > 0 and lines[flag - 1] in ("", "#")
    block = listing + ["#"]
    if not separated:
        block.insert(0, "#")
    lines[flag:flag] = block
    return lines


def strip_quotes(segment: str) -> str:
    """Strip the surrounding double quotes of one raw header line."""
    s = segment.strip()
    if s.startswith('"'):
        s = s[1:]
    if s.endswith('"'):
        s = s[:-1]
    return s


def update_header_entry(entry_lines, last_translator, language_team):
    """Update ``Last-Translator:`` and/or ``Language-Team:`` in the msgstr header."""
    unchanged = "\n".join(entry_lines)
    lines = list(entry_lines)

    if last_translator is not None:
        for i, line in enumerate(lines):
            m = LAST_TRANSLATOR_RE.match(line)
            if m:
                identity, year = last_translator
                lines[i] = f'{m.group(1)}"Last-Translator: {identity}, {year}\\n"'
                break

    if language_team is not None:
        for start, line in enumerate(lines):
            m = LANGUAGE_TEAM_START_RE.match(line)
            if m:
                indent = re.match(r"^\s*", line).group(0)
                value = m.group(1).rstrip('"')
                end = start + 1
                while (
                    end < len(lines)
                    and not value.endswith(")")
                    and not value.endswith(")\\n")
                ):
                    value += strip_quotes(lines[end])
                    end += 1
                lines[start:end] = [f'{indent}"Language-Team: {language_team}\\n"']
                break

    return lines if "\n".join(lines) != unchanged else None


def collect_files(paths):
    files = []
    for arg in paths:
        p = Path(arg) if Path(arg).is_absolute() else REPO_ROOT / arg
        if p.is_dir():
            for f in sorted(p.rglob("*.po")):
                rel = f.relative_to(REPO_ROOT)
                parts = set(rel.parts)
                if (
                    ".git" in parts
                    or ".cpython-src" in parts
                    or "venv" in parts
                    or ".venv" in parts
                ):
                    continue
                if any(part.startswith(".") for part in rel.parts):
                    continue
                files.append(rel)
        elif p.suffix == ".po":
            files.append(p.relative_to(REPO_ROOT))
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help=".po files or directories to update (default: whole repo)",
    )
    parser.add_argument(
        "--language-team",
        metavar="VALUE",
        help="set the Language-Team: header field to VALUE, e.g. "
        "'Persian (https://github.com/revisto/python-docs-fa/)'",
    )
    parser.add_argument(
        "--no-credits",
        action="store_true",
        help="skip regenerating the # Translators: / Last-Translator: credits",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="merge git identities into the existing # Translators: "
        "list instead of rebuilding it (preserves older credits)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without writing files",
    )
    parser.add_argument(
        "--no-last-translator",
        action="store_true",
        help="skip updating the Last-Translator: header field",
    )
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        parser.error("no .po files found")

    for rel in files:
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")

        mi = next((i for i, l in enumerate(lines) if l == 'msgid ""'), len(lines))
        ei = mi
        while ei < len(lines) and lines[ei] != "":
            ei += 1

        comment = lines[:mi]
        entry = lines[mi + 1 : ei]  # skip the 'msgid ""' line itself
        new_comment, new_entry = comment, entry

        if not args.no_credits:
            rows = git_history(str(rel))
            if rows:
                translators, last = translate_block(rows)
                if args.merge:
                    new_comment = merge_comment_block(comment, translators)
                else:
                    new_comment = update_comment_block(comment, translators)
                rebuilt = update_header_entry(
                    entry, None if args.no_last_translator else last, None
                )
                if rebuilt is not None:
                    new_entry = rebuilt

        if args.language_team:
            rebuilt = update_header_entry(new_entry, None, args.language_team)
            if rebuilt is not None:
                new_entry = rebuilt

        rebuilt_lines = new_comment + ['msgid ""'] + new_entry + lines[ei:]
        new_text = "\n".join(rebuilt_lines)
        if new_text.rstrip("\n") == text.rstrip("\n"):
            continue

        if args.dry_run:
            print(f"[dry-run] would update {rel}")
        else:
            path.write_text(new_text, encoding="utf-8")
            print(f"updated {rel}")


if __name__ == "__main__":
    main()
