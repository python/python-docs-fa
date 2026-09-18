#!/usr/bin/env python3
"""
Builds corpus.json and glossary.json from the python/python-docs-fa repository.

corpus.json shape (consumed by the glossary searcher site):
    [ { "msgid": "...", "msgstr": "...", "file": "library/functions.po", "line": 123 }, ... ]

glossary.json shape:
    [ { "en": "decorator", "fa": "دکوراتور، آراینده" }, ... ]

Usage:
    python build_corpus.py --repo-dir ./python-docs-fa --glossary-tsv ./glossary.tsv --out-dir ./data
"""
import argparse
import csv
import json
import os
import sys

try:
    import polib
except ImportError:
    print("ERROR: polib is required. Install with: pip install polib", file=sys.stderr)
    sys.exit(1)


def find_po_files(repo_dir):
    po_files = []
    for root, _dirs, files in os.walk(repo_dir):
        # skip VCS/meta directories
        if "/.git" in root or root.endswith("/.git"):
            continue
        for fname in files:
            if fname.endswith(".po"):
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, repo_dir)
                po_files.append((full_path, rel_path))
    return sorted(po_files, key=lambda x: x[1])


def parse_po_files(repo_dir):
    """Parse every .po file into flattened msgid/msgstr corpus entries."""
    entries = []
    skipped = 0
    po_files = find_po_files(repo_dir)

    if not po_files:
        print(f"WARNING: no .po files found under {repo_dir}", file=sys.stderr)

    for full_path, rel_path in po_files:
        try:
            po = polib.pofile(full_path)
        except Exception as e:
            print(f"WARNING: failed to parse {rel_path}: {e}", file=sys.stderr)
            skipped += 1
            continue

        for entry in po:
            # Skip obsolete, fuzzy, or empty-translation entries -- they
            # aren't useful corpus results and fuzzy ones are unreviewed.
            if entry.obsolete:
                continue
            if "fuzzy" in entry.flags:
                continue
            if not entry.msgid or not entry.msgstr:
                continue

            entries.append(
                {
                    "msgid": entry.msgid,
                    "msgstr": entry.msgstr,
                    "file": rel_path,
                    "line": entry.linenum if hasattr(entry, "linenum") else 0,
                }
            )

    print(
        f"Parsed {len(po_files)} .po files ({skipped} skipped), "
        f"{len(entries)} translated entries",
        file=sys.stderr,
    )
    return entries


def parse_glossary_tsv(tsv_path):
    """Parse the glossary TSV (English<TAB>Persian) into glossary.json entries."""
    entries = []
    with open(tsv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        return entries

    # Skip header row if it looks like one
    start_idx = 1 if rows[0][:2] == ["English", "Persian"] else 0

    for row in rows[start_idx:]:
        if len(row) < 2:
            continue
        en, fa = row[0].strip(), row[1].strip()
        if en and fa:
            entries.append({"en": en, "fa": fa})

    print(f"Parsed {len(entries)} glossary entries", file=sys.stderr)
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-dir", required=True, help="Path to the cloned python-docs-fa checkout"
    )
    parser.add_argument(
        "--glossary-tsv",
        required=True,
        help="Path to the glossary TSV file (English<TAB>Persian)",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory to write corpus.json and glossary.json into",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    corpus = parse_po_files(args.repo_dir)
    glossary = parse_glossary_tsv(args.glossary_tsv)

    corpus_path = os.path.join(args.out_dir, "corpus.json")
    glossary_path = os.path.join(args.out_dir, "glossary.json")

    with open(corpus_path, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, separators=(",", ":"))

    with open(glossary_path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, separators=(",", ":"))

    print(
        f"Wrote {corpus_path} ({os.path.getsize(corpus_path):,} bytes)", file=sys.stderr
    )
    print(
        f"Wrote {glossary_path} ({os.path.getsize(glossary_path):,} bytes)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
