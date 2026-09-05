#!/usr/bin/env python3
"""
scripts/check_markup.py

Verify Sphinx markup consistency between msgid and msgstr:
  - Roles like :term:`text <target>` — the *target* (or the whole role, if
    it has no explicit target) must match; the display text is expected to
    be translated, matching Sphinx's own translation convention.
  - Literal/code spans (``...``), substitution refs (|...|), and %s/{name}
    placeholders — these must match verbatim, since they're not prose.

Output is grouped by file, with a per-file mismatch count and a grand
total at the end.

Requires: pip install polib

Usage:
    python3 scripts/check_markup.py library/functions.po
    python3 scripts/check_markup.py tutorial/*.po
    python3 scripts/check_markup.py .          # recurse a whole directory
"""
import re
import sys
from collections import Counter
from pathlib import Path

import polib

ROLE_PATTERN = re.compile(r":(?:\w+:)?[\w.-]+:`([^`]+)`")
# Sphinx itself accepts `label<target>` (no space) as well as the more
# common `label <target>` -- both are valid RST role syntax, and the
# upstream English source uses the no-space form in a few places (e.g.
# ":pypi:`file built<blurb>`"). The space before "<" is therefore made
# optional here so those roles are recognized as having an explicit
# target instead of being treated as if the *entire* display text were
# the target (which produced impossible-to-match false positives, since
# no translation could ever reproduce the literal English phrase).
TARGET_PATTERN = re.compile(r"^(.*?)\s?<([^<>]+)>$")

LITERAL_PATTERNS = [
    ("literal/code span", re.compile(r"``.*?``")),
    ("substitution ref", re.compile(r"\|[\w.-]+\|")),
    ("percent placeholder", re.compile(r"%\(\w+\)[a-zA-Z]|%[a-zA-Z]")),
    ("brace placeholder", re.compile(r"\{[^{}\s]*\}")),
]


def extract_role_targets(text: str):
    """For each Sphinx role, return its target: the <target> anchor if
    present, otherwise the role's full display text (which IS the target
    when there's no explicit anchor)."""
    targets = []
    for body in ROLE_PATTERN.findall(text):
        m = TARGET_PATTERN.match(body)
        targets.append(m.group(2).strip() if m else body.strip())
    return targets


def check_file(path: Path):
    """Return a list of finding-dicts for this file (empty if none)."""
    results = []
    po = polib.pofile(str(path))
    for entry in po:
        if entry.obsolete or not entry.msgid or not entry.msgstr:
            continue  # obsolete entry, header, or still untranslated

        findings = []

        expected_targets = Counter(extract_role_targets(entry.msgid))
        found_targets = Counter(extract_role_targets(entry.msgstr))
        if expected_targets != found_targets:
            missing = list((expected_targets - found_targets).elements())
            extra = list((found_targets - expected_targets).elements())
            if missing:
                findings.append(f"missing role target(s): {missing}")
            if extra:
                findings.append(f"role target(s) not in source: {extra}")

        for label, pattern in LITERAL_PATTERNS:
            expected = Counter(pattern.findall(entry.msgid))
            found = Counter(pattern.findall(entry.msgstr))
            if expected != found:
                missing = list((expected - found).elements())
                extra = list((found - expected).elements())
                if missing:
                    findings.append(f"missing {label}: {missing}")
                if extra:
                    findings.append(f"extra {label} not in source: {extra}")

        if findings:
            loc = (
                f"{entry.occurrences[0][0]}:{entry.occurrences[0][1]}"
                if entry.occurrences
                else ""
            )
            results.append(
                {
                    "loc": loc,
                    "fuzzy": entry.fuzzy,
                    "findings": findings,
                    "msgid": entry.msgid,
                    "msgstr": entry.msgstr,
                }
            )
    return results


def print_file_group(path: Path, results):
    print(f"\n{'=' * 70}")
    print(f"{path}  ({len(results)} mismatch(es))")
    print("=" * 70)
    for r in results:
        tag = " [fuzzy]" if r["fuzzy"] else ""
        loc = f" ({r['loc']})" if r["loc"] else ""
        print(f"{loc}{tag}: {'; '.join(r['findings'])}")
        print(f"    msgid : {r['msgid'][:100]}")
        print(f"    msgstr: {r['msgstr'][:100]}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            files.extend(
                sorted(f for f in p.rglob("*.po") if ".git" not in f.parts)
            )
        else:
            files.append(p)

    total = 0
    per_file_counts = []

    for f in files:
        results = check_file(f)
        if results:
            print_file_group(f, results)
            total += len(results)
            per_file_counts.append((f, len(results)))

    if total:
        print(f"\n{'=' * 70}")
        print("Summary by file (sorted by mismatch count, descending):")
        print("=" * 70)
        for f, count in sorted(per_file_counts, key=lambda x: -x[1]):
            print(f"  {count:4d}  {f}")
        print(f"\n{total} markup mismatch(es) found across {len(per_file_counts)} file(s).")
        sys.exit(1)

    print("No markup mismatches found.")


if __name__ == "__main__":
    main()