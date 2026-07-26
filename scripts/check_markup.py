#!/usr/bin/env python3
"""
scripts/check_markup.py

Verify Sphinx markup consistency between msgid and msgstr:
  - Roles like :term:`text <target>` — the *target* (or the whole role, if
    it has no explicit target) must match; the display text is expected to
    be translated, matching Sphinx's own translation convention.
  - Literal/code spans (``...``), substitution refs (|...|), and %s/{name}
    placeholders — these must match verbatim, since they're not prose.

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
TARGET_PATTERN = re.compile(r"^(.*)\s<([^<>]+)>$")

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


def check_file(path: Path) -> int:
    problems = 0
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
            problems += 1
            loc = f" ({entry.occurrences[0][0]}:{entry.occurrences[0][1]})" if entry.occurrences else ""
            tag = " [fuzzy]" if entry.fuzzy else ""
            print(f"{path}{loc}{tag}: {'; '.join(findings)}")
            print(f"    msgid : {entry.msgid[:100]}")
            print(f"    msgstr: {entry.msgstr[:100]}")
    return problems


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        files.extend(sorted(p.rglob("*.po"))) if p.is_dir() else files.append(p)

    total = sum(check_file(f) for f in files)

    if total:
        print(f"\n{total} markup mismatch(es) found.")
        sys.exit(1)
    print("No markup mismatches found.")


if __name__ == "__main__":
    main()