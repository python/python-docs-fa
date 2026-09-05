#!/usr/bin/env python3
"""
scripts/list_parentheticals.py

List every "فارسی (English)" gloss in the .po corpus: msgstr places that
use a Persian rendering and repeat the English term in parentheses right
next to it, e.g.

    مدیریت زمینه (context management)
    لیترال درون‌یابی‌شده (interpolated literal)
    «الگوی as» (as-pattern)

Scope rules:
  - only prose msgstrs: code blocks, doctests, mostly-preserved code
    listings and untranslated entries are skipped
  - the paren must follow a Persian word (guillemets, quotes, diacritics
    and whitespace in between are tolerated); a paren hanging off a
    stopword like «و (list)» or «را (foo)» is ignored, since the gloss
    there belongs to something else
  - the paren content must look like an English term: starts with a
    letter, only letters/digits/spaces and .+-_&/'#!, at most 6 words
    and 40 chars, not ending in '.' (so "(default: None)" and
    "(e.g. ...)" fall out automatically)

Two outputs:
  1. the full list, grouped by English term and Persian partner, with
     file:line refs  ->  reports/parentheticals.md
  2. a coverage view: for each glossed term, how many msgids containing
     that term were glossed vs. translated without the gloss (the msgstr
     still contains one of the term's Persian partner words). Glosses
     are often a first-mention-only convention, so read coverage as a
     map of where glosses appear vs. don't -- not as violations.

Usage:
    python3 scripts/list_parentheticals.py
    python3 scripts/list_parentheticals.py --max-examples 4
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_consistency import (  # noqa: E402
    FARSI_STOPWORDS,
    FARSI_WORD_RE,
    REPO_ROOT,
    load_entries,
    mostly_preserved_code,
    standalone_code_entry,
)
from triage_consistency import canon_lexeme  # noqa: E402

FA_WORD = r"[\u0620-\u064A\u066E-\u066F\u0671-\u06D3\u06EE-\u06FC\u200c]+"
# between the Persian word and '(': marks/ZWNJ/whitespace/guillemets/
# quotes, or (for «الگوی as») a single short Latin word closed by a
# guillemet. Kept strictly bounded: an unbounded (…+)+ here makes the
# regex backtrack catastrophically on long English tails.
GLUE = (
    r'(?:[\u064B-\u065F\u0670\u200c\s»«"\']*'
    r'|\s*[A-Za-z][A-Za-z0-9\-]{0,19}\s*»\s*)'
)

GLOSS_RE = re.compile(
    "(" + FA_WORD + ")" + GLUE + r"\(\s*([^()\n]{1,60})\s*\)"
)


def is_english_gloss(content: str) -> bool:
    c = content.strip()
    if not (2 <= len(c) <= 40):
        return False
    if c.endswith("."):
        return False
    if re.match(r"(?i)^(e\.g\.|i\.e\.|etc\.?|cf\.?|vs\.?)", c):
        return False  # abbreviation, not a term gloss
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .+\-_&/'#!]*", c):
        return False
    return 1 <= len(c.split()) <= 6


def scan_glosses(entries):
    """All (fa word, (english), file:line) occurrences in prose msgstrs."""
    occs = []
    for e in entries:
        msgid, msgstr = e["msgid"], e["msgstr"]
        if e["code"] or msgstr.strip() == msgid.strip():
            continue
        if standalone_code_entry(msgid) or mostly_preserved_code(msgid, msgstr):
            continue
        for m in GLOSS_RE.finditer(msgstr):
            fa, en = m.group(1), m.group(2).strip()
            if not is_english_gloss(en):
                continue
            if canon_lexeme(fa) in FARSI_STOPWORDS:
                continue
            occs.append({
                "en": en,
                "en_key": en.lower(),
                "fa": fa,
                "file": e["file"],
                "line": e["line"],
                "ctx": msgstr[max(0, m.start() - 30):m.end() + 30]
                              .replace("\n", " "),
            })
    return occs


def coverage(entries, terms, partner_lexemes):
    if not terms:
        return {}
    terms_parts = {t: re.split(r"[\s\-]+", t) for t in terms}
    by_first = defaultdict(list)
    for t, parts in terms_parts.items():
        by_first[parts[0].lower()].append(t)  # <-- lowercase for case-insensitive lookup

    gloss_re = {
        t: re.compile(
            r"\(\s*" + r"[\s\-]+".join(re.escape(p) for p in parts)
            + r"s?\s*\)", re.IGNORECASE
        )
        for t, parts in terms_parts.items()
    }
    stats = {
        t: {"glossed": [], "no_gloss_fa": [], "no_gloss_other": []}
        for t in terms
    }

    for e in entries:
        msgid, msgstr = e["msgid"], e["msgstr"]
        if e["code"] or msgstr.strip() == msgid.strip():
            continue
        if standalone_code_entry(msgid) or mostly_preserved_code(msgid, msgstr):
            continue
        if not msgid.strip():
            continue

        # FIX: tokenize on whitespace only, preserving hyphenated tokens,
        # then normalise each token to lowercase for lookup.
        raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-']*", msgid)
        low = [t.lower() for t in raw_tokens]
        n = len(low)
        found = set()
        for i, tok in enumerate(low):
            for term in by_first.get(tok, ()):
                parts = terms_parts[term]
                np = len(parts)
                if np == 1:
                    found.add(term)
                else:
                    # FIX: compare against hyphen-normalised msgid tokens
                    # so "as-pattern" in msgid matches parts ["as", "pattern"]
                    # whether written as "as-pattern" or "as pattern".
                    msgid_slice = [
                        re.sub(r"\-+", "-", low[j]) for j in range(i, min(i + np, n))
                    ]
                    term_parts_norm = [re.sub(r"\-+", "-", p.lower()) for p in parts]
                    if msgid_slice == term_parts_norm:
                        found.add(term)
                    # Also match when the whole hyphenated term appears as one token.
                    elif np > 1 and low[i] == "-".join(p.lower() for p in parts):
                        found.add(term)

        if not found:
            continue

        loc = {"file": e["file"], "line": e["line"], "msgid": msgid[:80]}
        fa_toks = {canon_lexeme(w) for w in FARSI_WORD_RE.findall(msgstr)}
        for t in found:
            if gloss_re[t].search(msgstr):
                stats[t]["glossed"].append(loc)
            elif partner_lexemes.get(t, set()) & fa_toks:
                stats[t]["no_gloss_fa"].append(loc)
            else:
                stats[t]["no_gloss_other"].append(loc)
    return stats


def render_markdown(pairs, cov, n_occ, n_files, args):
    L = []
    A = L.append

    n_terms = len(pairs)
    n_pairs = sum(len(v) for v in pairs.values())

    A("# Parenthetical English glosses — فارسی (English)")
    A("")
    A(f"{n_occ} gloss occurrences · {n_terms} English terms · "
      f"{n_pairs} Persian–English pairs · {n_files} files.")
    A("")
    A("Every place where a msgstr uses a Persian rendering and repeats "
      "the English term in parentheses next to it (prose msgstrs only; "
      "code blocks and doctests are skipped).")
    A("")

    if cov:
        A("## Coverage by English term")
        A("")
        A("For each glossed term: how many msgids containing that term "
          "carry the gloss, how many use the same Persian rendering "
          "*without* the gloss, and how many do neither. Glosses are "
          "often a first-mention-only convention, so read this as a map "
          "of where they appear vs. don't — not as violations.")
        A("")
        A("| English term | glossed | same فارسی, no gloss | other |")
        A("| --- | ---: | ---: | ---: |")
        rows = sorted(
            cov.items(),
            key=lambda kv: -(len(kv[1]["glossed"]) + len(kv[1]["no_gloss_fa"])),
        )
        for t, s in rows:
            A(f"| {t} | {len(s['glossed'])} | {len(s['no_gloss_fa'])} "
              f"| {len(s['no_gloss_other'])} |")
        A("")

    A("## Full list")
    A("")
    for en, fas in sorted(
        pairs.items(), key=lambda kv: -sum(len(v) for v in kv[1].items())
    ):
        total = sum(len(v) for v in fas.values())
        A(f"### {en} — {total}×")
        A("")
        for fa, occs in sorted(fas.items(), key=lambda kv: -len(kv[1])):
            A(f"- **{fa}** ×{len(occs)}")
            for o in occs[:args.max_examples]:
                A(f"  - {o['file']}:{o['line']} — “...{o['ctx']}...”")
            if len(occs) > args.max_examples:
                A(f"  - … and {len(occs) - args.max_examples} more "
                  f"(see JSON report)")
        A("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--po-dir", default=str(REPO_ROOT))
    ap.add_argument("--out-md",
                    default=str(REPO_ROOT / "reports" / "parentheticals.md"))
    ap.add_argument("--out-json",
                    default=str(REPO_ROOT / "reports" / "parentheticals.json"))
    ap.add_argument("--max-examples", type=int, default=4,
                    help="Max occurrences listed per Persian partner")
    ap.add_argument("--max-terms", type=int, default=400,
                    help="Max terms for the coverage table (0 = all; "
                         "the table is O(entries x terms))")
    args = ap.parse_args()

    print("Loading .po entries ...")
    entries, n_files = load_entries(Path(args.po_dir))
    print(f"  {n_files} files, {len(entries)} translated entries")

    print("Scanning for فارسی (English) glosses ...")
    occs = scan_glosses(entries)

    pairs = defaultdict(lambda: defaultdict(list))
    en_display = {}
    for o in occs:
        pairs[o["en_key"]][o["fa"]].append(o)
        en_display.setdefault(o["en_key"], o["en"])
    pairs = {k: dict(v) for k, v in pairs.items()}
    partner_lexemes = {
        en: {canon_lexeme(f) for f in fas} for en, fas in pairs.items()
    }

    n_occ = len(occs)
    n_gloss_files = len({o["file"] for o in occs})
    print(f"  {n_occ} glosses · {len(pairs)} English terms · "
          f"{sum(len(v) for v in pairs.values())} pairs · "
          f"{n_gloss_files} files")

    print("Computing per-term coverage ...")
    ranked = sorted(
        pairs.items(),
        key=lambda kv: -sum(len(v) for v in kv[1].values()),
    )
    cov_terms = [en for en, _ in ranked]
    if args.max_terms and len(cov_terms) > args.max_terms:
        print(f"  capping coverage to top {args.max_terms} glossed terms "
              f"(--max-terms 0 for all)")
        cov_terms = cov_terms[:args.max_terms]
    cov = coverage(entries, set(cov_terms), partner_lexemes)

    md = render_markdown(pairs, cov, n_occ, n_gloss_files, args)
    Path(args.out_md).write_text(md, encoding="utf-8")

    out = {
        "summary": {
            "gloss_occurrences": n_occ,
            "english_terms": len(pairs),
            "persian_english_pairs": sum(len(v) for v in pairs.values()),
            "files_with_glosses": n_gloss_files,
        },
        "pairs": {
            en: {
                fa: [{k: o[k] for k in ("file", "line", "ctx")} for o in occs]
                for fa, occs in fas.items()
            }
            for en, fas in pairs.items()
        },
        "coverage": {
            t: {k: v for k, v in s.items()} for t, s in cov.items()
        },
    }
    Path(args.out_json).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nWrote {args.out_md}")
    print(f"Wrote {args.out_json}")

    print("\nTop glossed English terms:")
    for en, fas in sorted(
        pairs.items(), key=lambda kv: -sum(len(v) for v in kv[1].items())
    )[:15]:
        total = sum(len(v) for v in fas.values())
        partners = "، ".join(
            f"{fa}×{len(occ)}" for fa, occ in
            sorted(fas.items(), key=lambda kv: -len(kv[1]))[:3]
        )
        print(f"  {en_display[en]} ({total}×): {partners}")
    if cov:
        print("\nMost often translated without the gloss "
              "(same Persian word, no paren):")
        ranked = sorted(
            cov.items(),
            key=lambda kv: -len(kv[1]["no_gloss_fa"]),
        )[:10]
        for t, s in ranked:
            print(f"  {t}: {len(s['glossed'])} glossed, "
                  f"{len(s['no_gloss_fa'])} without")


if __name__ == "__main__":
    main()
