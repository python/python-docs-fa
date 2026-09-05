#!/usr/bin/env python3
"""
scripts/triage_consistency.py

Turn the raw output of scripts/check_consistency.py into a prioritized,
action-oriented worklist.

Why: the raw report flags every *candidate* inconsistency, and its drift
variants keep morphological siblings as separate words (فهرست، فهرست‌ها،
فهرست‌های...). A human can't action 11k rows. This script re-clusters and
buckets every finding by the *decision* it needs:

  1. quick wins (mechanical fixes, no judgment)
       zwnj-spelling  -- same msgid, translations differ only by ZWNJ /
                        spacing / punctuation (پارامترها vs پارامترها).
                        The majority spelling is suggested as canonical.
       align-minority -- one translation dominates (default >= 60%);
                        minority locations are listed for alignment.

  2. glossary decisions (change the glossary, not the corpus)
       alt:<lexeme>   -- flagged entries consistently use ONE non-glossary
                        Persian word (widget -> ویجت). Either add it to
                        glossary.json or normalize the corpus.

  3. translation fixes
       untranslated-ref -- msgstr keeps an English :term:/:ref:/:doc:
                          display text; the display text should be
                          translated.
       kept-english     -- the English term sits untranslated in the
                          msgstr prose (heap, wildcard): decide per term.

  4. judgment calls
       scattered       -- no dominant pattern: paraphrases, ambiguous
                          English senses (method = متد or روش?).

  5. real drift (after lexeme canonicalization)
       variants are folded into lexemes (clitics stripped, broken plurals
       mapped back, spelling normalized) and filler words are dropped;
       only terms with >= 2 well-supported distinct lexemes remain. So
       lists -> فهرست/فهرست‌ها/فهرست‌های collapses to one lexeme and
       disappears, while literal -> لیترال/لفظی stays.

Outputs:
    reports/triage.md              -- human-readable, grouped, capped
    reports/triage_worklist.json   -- machine-readable, complete

Usage:
    python3 scripts/triage_consistency.py
    python3 scripts/triage_consistency.py --report my_report.json \
        --majority 0.7 --drift-share 0.15
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_consistency import (  # noqa: E402
    ARABIC_PLURALS,
    FARSI_STOPWORDS,
    FARSI_WORD_RE,
    normalize_fa,
    strip_markup,
    term_to_regex,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Lexeme canonicalization: morphological siblings must not count as drift.
# ---------------------------------------------------------------------------

MARKS_RE = re.compile(r"[\u064B-\u065F\u0670]")
PUNCT_STRIP_RE = re.compile(r"^[\u060c\u061b\u061f\u0640]+|[\u060c\u061b\u061f\u0640]+$")
BROKEN_PLURALS_BACK = {p: w for w, ps in ARABIC_PLURALS.items() for p in ps}

CLITICS = (
    "هایی", "های", "ها", "یی", "ترین", "تر", "انه", "شان", "مان",
    "تان", "یش", "یت", "یم", "اش", "ات", "ام", "ای", "ش", "ت", "م", "ی",
)
# unambiguous plural markers: safe to strip even when glued (پارامترها)
BARE_PLURALS = ("هایی", "های", "ها")


def _strip_clitics(w: str) -> str:
    # 1) clitics after ZWNJ (standard orthography: آرگومان‌ها)
    for c in CLITICS:
        suf = "\u200c" + c
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            w = w[: -len(suf)]
            break
    # 2) glued plural markers (پارامترها، متدهای)
    for c in BARE_PLURALS:
        if w.endswith(c) and len(w) - len(c) >= 3:
            w = w[: -len(c)]
            break
    return w


def canon_lexeme(word: str) -> str:
    """Fold a surface word to its lexeme for grouping/comparison.

    Only clitics attached with ZWNJ (the standard morpheme boundary)
    and bare plural markers are stripped, so stems are never mangled
    (آرگومان stays آرگومان despite ending in مان, پارامتر stays
    پارامتر despite ending in تر).
    """
    w = normalize_fa(word)
    w = PUNCT_STRIP_RE.sub("", w)
    w = MARKS_RE.sub("", w)
    w = _strip_clitics(w)
    w = BROKEN_PLURALS_BACK.get(w, w)
    return w.replace("\u200c", "")


def is_informative(lexeme: str) -> bool:
    return (
        len(lexeme) >= 2
        and lexeme not in FARSI_STOPWORDS
        and lexeme not in TRIVIAL_WORDS
    )


# Words that can never be terminology: verb/aux/copula/pro-forms and
# frame nouns that appear next to almost any English term. ZWNJ-less
# spellings (میشود for می‌شود) are included since canon strips ZWNJ.
TRIVIAL_WORDS = {canon_lexeme(w) for w in """
می‌کنند می‌شوند می‌شود می‌دهند می‌دهد می‌گیرند می‌گیرد می‌توانند می‌تواند
می‌یابند می‌یابد می‌باشد نمی‌شود نمی‌کنند نمی‌کند نمی‌توانند نمی‌تواند
خواهند خواهند هستند بوده بود باشد می‌کنیم می‌کنید می‌کند می‌کنم
انجام دارد دارند داشته وابسته مورد صورت عنوان مثل مانند
همراه بدون همیشه فقط سپس بنابراین یعنی درباره نسبت مربوط منظور
خالی برابر مشخص دیگر تنها چند همه هیچ این‌ها آن‌ها موارد حال
استفاده ایجاد بررسی شامل حاوی فراخوانی بازگرداندن برگرداند
""".split()}


def canon_msgstr(s: str) -> str:
    """ZWNJ/spacing/punctuation-insensitive form of a translation."""
    s = normalize_fa(s)
    s = MARKS_RE.sub("", s)
    s = re.sub(r"[\u200c\s]+", "", s)
    return s.strip(" .:;،؛!؟«»\"'()[]_").lower()


def viz(s: str) -> str:
    """Make invisible differences visible.

    ZWNJ and other zero-width chars are the usual culprit: استثناها and
    استثناها render identically but differ by U+200C. Show it as <ZWNJ>
    (or its codepoint) so the fix needed is obvious. Also show the raw
    hex form of the two words side by side when debugging.
    """
    out = []
    for ch in s:
        if ch == "\u200c":
            out.append("<ZWNJ>")
        elif ch == "\u200d":
            out.append("<ZWJ>")
        elif unicodedata.category(ch) in {"Cf", "Mn", "Me"} and ch not in " \t":
            out.append(f"<U+{ord(ch):04X}>")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# Glossary-violation classification
# ---------------------------------------------------------------------------

ROLE_DISPLAY_RE = re.compile(r":(?:term|ref|doc):`([^`]*)`")


def role_displays(text: str):
    out = []
    for body in ROLE_DISPLAY_RE.findall(text):
        out.append(body.rsplit("<", 1)[0].strip() if "<" in body else body)
    return out


def classify_glossary_item(term: str, item: dict):
    """Bucket a single glossary violation, or None if unclassified."""
    tp = re.compile(term_to_regex(term), re.IGNORECASE)
    msgstr = item["msgstr"]

    # 1. an English :term:/:ref:/:doc: display text was left untranslated
    for disp in role_displays(msgstr):
        if tp.search(disp):
            return "untranslated-ref"

    # 2. the English term itself sits in the translated prose
    if tp.search(strip_markup(msgstr)):
        return "kept-english"

    return None


def item_words(msgstr: str):
    """Informative lexemes of a msgstr (markup stripped)."""
    out = set()
    for w in FARSI_WORD_RE.findall(strip_markup(msgstr, keep_prose_roles=False)):
        lex = canon_lexeme(w)
        if is_informative(lex):
            out.add(lex)
    return out


def triage_glossary(violations, dominant_share, min_count, max_alts=3):
    """term -> {"buckets": {bucket: [items]}, "allowed": [...]}"""
    result = {}
    for term, items in sorted(violations.items(), key=lambda kv: -len(kv[1])):
        buckets = defaultdict(list)
        unresolved = []
        for it in items:
            kind = classify_glossary_item(term, it)
            if kind:
                buckets[kind].append(it)
            else:
                unresolved.append(it)

        if unresolved:
            n = len(unresolved)
            lex_counts = Counter()
            for it in unresolved:
                for lex in item_words(it["msgstr"]):
                    lex_counts[lex] += 1
            need = max(min_count, dominant_share * n)
            qualifying = [
                (lex, c) for lex, c in lex_counts.most_common() if c >= need
            ][:max_alts]
            for it in unresolved:
                words = item_words(it["msgstr"])
                for lex, _ in qualifying:
                    if lex in words:
                        buckets[f"alt:{lex}"].append(it)
                        break
                else:
                    buckets["scattered"].append(it)

        result[term] = {
            "total": len(items),
            "allowed": items[0]["allowed"] if items else [],
            "buckets": dict(buckets),
        }
    return result


# ---------------------------------------------------------------------------
# Duplicate-msgid classification
# ---------------------------------------------------------------------------

def triage_duplicates(dupes, majority):
    zwnj, align, review = {}, {}, {}
    for key, data in dupes.items():
        variants = data["variants"]
        forms = {canon_msgstr(v["msgstr"]) for v in variants}
        top = max(variants, key=lambda v: v["count"])
        if len(forms) == 1:
            zwnj[key] = {
                "canonical": top["msgstr"],
                "total": data["occurrences"],
                "fix_locations": [
                    loc for v in variants if v is not top
                    for loc in v["locations"]
                ],
                "all_variants": variants,
            }
        elif top["count"] / data["occurrences"] >= majority:
            align[key] = {
                "canonical": top["msgstr"],
                "canonical_share": round(top["count"] / data["occurrences"], 2),
                "total": data["occurrences"],
                "fix_locations": [
                    loc for v in variants if v is not top
                    for loc in v["locations"]
                ],
                "all_variants": variants,
            }
        else:
            review[key] = data
    return zwnj, align, review


# ---------------------------------------------------------------------------
# Drift re-clustering
# ---------------------------------------------------------------------------

def triage_drift(drift, min_share, min_count):
    out = {}
    for term, data in drift.items():
        freq = data["frequency"]
        lex_counts, surface = Counter(), {}
        for v in data["variants"]:
            lex = canon_lexeme(v["farsi_word"])
            if not is_informative(lex):
                continue
            lex_counts[lex] += v["count"]
            surface.setdefault(lex, v["farsi_word"])
        need = max(min_count, min_share * freq)
        strong = [
            {"lexeme": lex, "surface": surface[lex], "count": c,
             "share": round(c / freq, 2)}
            for lex, c in lex_counts.most_common() if c >= need
        ]
        if len(strong) >= 2:
            out[term] = {"frequency": freq, "lexemes": strong}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["frequency"]))


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def loc_str(items, max_examples):
    return [f"{it['file']}:{it['line']}" for it in items[:max_examples]]


def render_markdown(g, zwnj, align, review_dupes, drift, args):
    L = []
    A = L.append

    A("# Translation consistency triage")
    A("")
    A("Generated by `scripts/triage_consistency.py` from "
      "`reports/consistency_report.json`. Buckets are ordered by the "
      "kind of decision they need.")
    A("")

    # -- 1. quick wins
    A("## 1. Quick wins (mechanical fixes)")
    A("")
    A(f"### ZWNJ/spacing inconsistencies -- {len(zwnj)} msgid group(s)")
    A("")
    A("Same English string, translations identical except for ZWNJ, "
      "spacing or punctuation. `<ZWNJ>` marks the invisible U+200C "
      "character, so the variants look identical in a terminal but "
      "differ in bytes. The majority spelling wins; paste it over the "
      "listed locations:")
    A("")
    for key, d in sorted(zwnj.items(), key=lambda kv: -kv[1]["total"]):
        variants_line = " | ".join(
            f"`{viz(v['msgstr'])}` ×{v['count']}"
            for v in d["all_variants"]
        )
        A(f"- **{key[:80]!r}** ({d['total']}x): {variants_line}")
        A(f"  -> make all of them: `{d['canonical']}` "
          f"({len(d['fix_locations'])} to fix: "
          f"{', '.join(loc_str([{'file': l['file'], 'line': l['line']} for l in d['fix_locations']], args.max_examples))})")
    A("")
    A(f"### Minority variants to align -- {len(align)} msgid group(s)")
    A("")
    A("One translation clearly dominates; align the minority to it:")
    A("")
    for key, d in sorted(align.items(), key=lambda kv: -kv[1]["total"]):
        minority = [
            v for v in d["all_variants"] if v["msgstr"] != d["canonical"]
        ]
        minority_line = " | ".join(
            f"`{viz(v['msgstr'])[:50]}` ×{v['count']}" for v in minority[:3]
        )
        A(f"- **{key[:80]!r}** ({d['total']}x, top variant "
          f"{int(d['canonical_share']*100)}%)")
        A(f"  -> keep: `{d['canonical']}`; replace: {minority_line}")
        A(f"  ({len(d['fix_locations'])} to fix: "
          f"{', '.join(loc_str([{'file': l['file'], 'line': l['line']} for l in d['fix_locations']], args.max_examples))})")
    A("")

    # -- 2. glossary decisions
    A("## 2. Glossary decisions (dominant non-glossary renderings)")
    A("")
    A("The corpus consistently uses a Persian word that is not in the "
      "glossary. For each: **add it to `glossary.json` as an allowed "
      "variant, or normalize these entries** to the allowed ones.")
    A("")
    for term, data in sorted(g.items(), key=lambda kv: -kv[1]["total"]):
        alts = [(k, v) for k, v in data["buckets"].items() if k.startswith("alt:")]
        if not alts:
            continue
        allowed = "، ".join(data["allowed"])
        A(f"### {term}  (allowed: {allowed})")
        A("")
        for k, items in sorted(alts, key=lambda kv: -len(kv[1])):
            word = k[4:]
            A(f"- uses **{word}** in {len(items)} entries "
              f"(e.g. {', '.join(loc_str(items, 3))})")
        A("")

    # -- 3. translation fixes
    A("## 3. Translation fixes")
    A("")
    A("### Untranslated role displays "
      f"-- {sum(len(v['buckets'].get('untranslated-ref', [])) for v in g.values())} entries")
    A("")
    A("The msgstr keeps the English display text of a `:term:`/`:ref:`/"
      ":doc: role. Translate the display text (keep the `<target>` "
      "part English):")
    A("")
    for term, data in sorted(g.items(), key=lambda kv: -len(kv[1]["buckets"].get("untranslated-ref", []))):
        items = data["buckets"].get("untranslated-ref", [])
        if not items:
            continue
        A(f"- **{term}**: {len(items)} entries "
          f"(e.g. {', '.join(loc_str(items, 4))})")
    A("")
    A("### English term kept in prose "
      f"-- {sum(len(v['buckets'].get('kept-english', [])) for v in g.values())} entries")
    A("")
    A("The English term is left untranslated inside prose. If that is "
      "the intended convention, add the English form to `glossary.json`; "
      "otherwise translate these:")
    A("")
    for term, data in sorted(g.items(), key=lambda kv: -len(kv[1]["buckets"].get('kept-english', []))):
        items = data["buckets"].get("kept-english", [])
        if not items:
            continue
        A(f"- **{term}**: {len(items)} entries "
          f"(e.g. {', '.join(loc_str(items, 4))})")
    A("")

    # -- 4. judgment calls
    A("## 4. Judgment calls (scattered paraphrases)")
    A("")
    A("No dominant alternative: genuine paraphrases, ambiguous English "
      "senses, or residual checker noise. Skim per term:")
    A("")
    for term, data in sorted(g.items(), key=lambda kv: -len(kv[1]["buckets"].get("scattered", []))):
        items = data["buckets"].get("scattered", [])
        if not items:
            continue
        A(f"- **{term}**: {len(items)} entries "
          f"(e.g. {', '.join(loc_str(items, 3))})")
    A("")
    if review_dupes:
        A("### Duplicate msgids without a clear majority "
          f"-- {len(review_dupes)} group(s)")
        A("")
        for key, data in sorted(review_dupes.items(), key=lambda kv: -kv[1]["occurrences"])[:args.max_examples]:
            A(f"- **{key[:80]!r}** ({data['occurrences']}x): " + " | ".join(
                f"`{v['msgstr'][:40]}` ({v['count']}x)"
                for v in data["variants"][:4]))
        A("")

    # -- 5. real drift
    A(f"## 5. Real drift after lexeme clustering -- {len(drift)} terms")
    A("")
    A("Frequent English terms (not in the glossary) with 2+ distinct "
      "well-supported Persian lexemes. These are the candidates to add "
      "to `GLOSSARY.md`; pick one rendering per term and normalize:")
    A("")
    A("| term | freq | competing lexemes (share) |")
    A("| ---- | ---- | ------------------------ |")
    for term, data in list(drift.items())[:args.max_drift_rows]:
        lex = "، ".join(
            f"{v['surface']} ({int(v['share']*100)}%)" for v in data["lexemes"][:5]
        )
        A(f"| {term} | {data['frequency']}x | {lex} |")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--report",
                    default=str(REPO_ROOT / "reports" / "consistency_report.json"))
    ap.add_argument("--out-md", default=str(REPO_ROOT / "reports" / "triage.md"))
    ap.add_argument("--out-json",
                    default=str(REPO_ROOT / "reports" / "triage_worklist.json"))
    ap.add_argument("--majority", type=float, default=0.6,
                    help="Dominant-variant share to align minority dupes (default 0.6)")
    ap.add_argument("--dominant-share", type=float, default=0.25,
                    help="Share for a non-glossary word to count as dominant (default 0.25)")
    ap.add_argument("--drift-share", type=float, default=0.12,
                    help="Min lexeme share to count as a real drift variant (default 0.12)")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--max-examples", type=int, default=10,
                    help="Max file:line refs shown per line in the markdown")
    ap.add_argument("--max-drift-rows", type=int, default=60)
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    violations = report.get("glossary_violations", {})
    dupes = report.get("duplicate_drift", {})
    drift = report.get("non_glossary_drift", {})

    print("Classifying glossary violations ...")
    g = triage_glossary(violations, args.dominant_share, args.min_count)

    print("Classifying duplicate msgids ...")
    zwnj, align, review_dupes = triage_duplicates(dupes, args.majority)

    print("Re-clustering drift into lexemes ...")
    real_drift = triage_drift(drift, args.drift_share, args.min_count)

    counts = {
        "zwnj_spelling_groups": len(zwnj),
        "zwnj_spelling_entries": sum(d["total"] for d in zwnj.values()),
        "align_minority_groups": len(align),
        "align_minority_locations": sum(len(d["fix_locations"]) for d in align.values()),
        "glossary_alt_terms": sum(
            1 for v in g.values() if any(k.startswith("alt:") for k in v["buckets"])
        ),
        "untranslated_refs": sum(
            len(v["buckets"].get("untranslated-ref", [])) for v in g.values()
        ),
        "kept_english": sum(
            len(v["buckets"].get("kept-english", [])) for v in g.values()
        ),
        "scattered": sum(len(v["buckets"].get("scattered", [])) for v in g.values()),
        "dupes_needing_review": len(review_dupes),
        "real_drift_terms": len(real_drift),
        "raw_drift_terms_dropped": len(drift) - len(real_drift),
    }
    print(json.dumps(counts, ensure_ascii=False, indent=2))

    md = render_markdown(g, zwnj, align, review_dupes, real_drift, args)
    Path(args.out_md).write_text(md, encoding="utf-8")

    worklist = {
        "counts": counts,
        "quick_wins": {"zwnj_spelling": zwnj, "align_minority": align},
        "glossary": g,
        "duplicates_needing_review": review_dupes,
        "real_drift": real_drift,
    }
    Path(args.out_json).write_text(
        json.dumps(worklist, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nWrote {args.out_md}")
    print(f"Wrote {args.out_json}")
    print("\nTop glossary decisions (alt renderings):")
    for term, data in sorted(g.items(), key=lambda kv: -kv[1]["total"]):
        alts = [(k, v) for k, v in data["buckets"].items() if k.startswith("alt:")]
        if alts:
            words = ", ".join(f"{k[4:]}×{len(v)}" for k, v in alts)
            print(f"  {term}: {words}")
    print("\nTop real drift:")
    for term, data in list(real_drift.items())[:10]:
        lex = "، ".join(f"{v['surface']}({int(v['share']*100)}%)" for v in data["lexemes"][:4])
        print(f"  {term} ({data['frequency']}x): {lex}")


if __name__ == "__main__":
    main()
