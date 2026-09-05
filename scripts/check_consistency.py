#!/usr/bin/env python3
"""
scripts/check_consistency.py

Audit the whole .po corpus (English msgid -> Farsi msgstr) for translation
consistency. Three independent checks:

  1. glossary   -- a glossary term occurs in the English msgid but none of
                   its allowed Farsi equivalents occur in the msgstr.
                   Matching is markup-aware (terms inside code spans, code
                   roles, URLs, kept-verbatim emphasis and proper-noun
                   phrases are ignored) and morphology-tolerant (Persian
                   plural/ezafe/verb endings, Arabic broken plurals,
                   hamza/yeh spelling variants all still count).
  2. duplicates -- the exact same English string is translated differently
                   in different places. The strongest consistency signal.
  3. drift      -- frequent English terms NOT in the glossary whose Farsi
                   rendering varies across occurrences; these are candidate
                   terms to add to GLOSSARY.md and then normalize.

Output: a JSON report (default reports/consistency_report.json) plus a
console summary. Findings are for human review -- the heuristics trade a
little noise for recall, and every finding carries file:line + both texts.

Requires: polib  (pip install polib)

Usage:
    python3 scripts/check_consistency.py
    python3 scripts/check_consistency.py --checks glossary,duplicates,drift
    python3 scripts/check_consistency.py --min-freq 6 --out report.json
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polib

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"}

# ---------------------------------------------------------------------------
# Markup handling
# ---------------------------------------------------------------------------

# Sphinx roles whose content is code and is (correctly) left untranslated.
# Terms appearing inside these must NOT trigger glossary checks.
CODE_ROLES = {
    "func", "meth", "mod", "class", "data", "const", "attr", "exc", "obj",
    "command", "cmdoption", "envvar", "file", "kbd", "option", "program",
    "regexp", "makevar", "dunder", "module", "method", "exception", "ref",
    "doc", "download", "env", "pep", "rfc", "issue", "source", "mimetype",
    "keyword", "literal", "token", "grammar", "confval", "setting",
}

ROLE_RE = re.compile(r":([\w+-]+):`([^`]*)`")
DOUBLE_BACKTICK_RE = re.compile(r"``.*?``", re.DOTALL)
SINGLE_BACKTICK_RE = re.compile(r"`([^`]*)`")
SUBREF_RE = re.compile(r"\|[\w.-]+\|")
URL_RE = re.compile(r"https?://\S+")
# asterisk emphasis only: underscores are code in these docs (__init__ etc.)
EMPH_RE = re.compile(r"(\*\*?)(.+?)\1")
TARGET_RE = re.compile(r"^(.*)\s<[^<>]+>$")
EMPH_TOKEN_RE = re.compile(r"\*([A-Za-z_][\w.\-]*)\*")


def strip_markup(text: str, keep_prose_roles: bool = True) -> str:
    """Reduce RST/Sphinx text to prose for term mining.

    Code spans, code-role contents and URLs are removed; the display text
    of prose roles (:term:, :ref: titles, ...) is kept because translators
    translate it.
    """
    def role_repl(m: re.Match) -> str:
        role, body = m.group(1).lower(), m.group(2)
        if role in CODE_ROLES:
            return " "
        if not keep_prose_roles:
            return " "
        t = TARGET_RE.match(body)
        return t.group(1) if t else body

    text = DOUBLE_BACKTICK_RE.sub(" ", text)
    text = ROLE_RE.sub(role_repl, text)
    text = SINGLE_BACKTICK_RE.sub(r" \1 ", text)
    text = SUBREF_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = EMPH_RE.sub(r"\2", text)
    return text


def looks_like_code_block(msgid: str) -> bool:
    """Whole-entry literal blocks (doctests, code listings)."""
    s = msgid.strip()
    if "\n" not in s:
        return False
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if not lines:
        return True
    codeish = sum(
        1 for ln in lines
        if ln.lstrip().startswith((">>>", "...", "#", "$"))
        or ln.startswith((" ", "\t"))
    )
    return codeish / len(lines) >= 0.8


WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]*[A-Za-z]|[A-Za-z]")

# quoted spans whose content looks like code ('Content-type', "import foo")
QUOTED_CODE_RE = re.compile(r"'([\w.\-/ ]+)'|\"([\w.\-/ ]+)\"")


def quoted_span_is_code(content: str) -> bool:
    words = content.split()
    return (
        len(words) == 1
        or any(c in content for c in ".-_")
        or content.startswith(("import ", "from "))
    )


def strip_quoted_code(text: str) -> str:
    def repl(m: re.Match) -> str:
        content = m.group(1) or m.group(2)
        return " " if quoted_span_is_code(content) else m.group(0)
    return QUOTED_CODE_RE.sub(repl, text)


CODE_LINE_START_RE = re.compile(
    r"^(?:import|from)\s"
    r"|^[a-z_][\w.]*(?:\[[^\]]*\])?\s*[=(]"
    r"|^[a-z_][\w.]*\("
)
CODE_STMT_RE = re.compile(
    r"^(?:if|elif|else|for|while|def|class|try|except|finally|with|return"
    r"|raise|break|continue|lambda|async|await)\b.*:\s*(?:#.*)?$"
)


def looks_codeish(line: str) -> bool:
    if line.startswith((">>>", "...", "$", "#")):
        return True
    if line.startswith(("'", '"', "[", "{", "<")) or line[0:1].isdigit():
        return True  # literal output of a doctest
    return bool(CODE_LINE_START_RE.match(line) or CODE_STMT_RE.match(line))


def prose_line(line: str) -> bool:
    s = line.strip()
    if not s or looks_codeish(s):
        return False
    return len(s.split()) >= 3 and s[0].isupper()


def standalone_code_entry(msgid: str) -> bool:
    """Entries that are pure code listings (no '::' marker included)."""
    lines = [ln for ln in msgid.split("\n") if ln.strip()]
    if not lines:
        return False
    codeish = sum(
        1 for ln in lines if ln[0] in " \t" or looks_codeish(ln.strip())
    )
    prose = sum(1 for ln in lines if prose_line(ln))
    return codeish >= max(1, len(lines) // 2) and prose <= len(lines) // 3


def strip_literal_blocks(msgid: str) -> str:
    """Drop literal-block content (the lines after a '::' marker), which
    in mixed prose+code entries is code and must not be term-mined."""
    out = []
    in_block = False
    for ln in msgid.split("\n"):
        stripped = ln.strip()
        if not in_block:
            if stripped == "::":
                in_block = True
                continue
            if stripped.endswith("::"):
                out.append(ln[:ln.rindex("::")].rstrip())
                in_block = True
                continue
            out.append(ln)
        else:
            if not stripped:
                continue  # blank line inside/after block: keep skipping
            if ln[0] in " \t" or looks_codeish(stripped):
                continue  # still block content
            in_block = False
            out.append(ln)
    return "\n".join(out)


def mostly_preserved_code(msgid: str, msgstr: str) -> bool:
    """Code listings whose only translated parts are comments: nearly all
    English tokens of the msgid reappear verbatim in the msgstr."""
    toks = [t.lower() for t in WORD_RE.findall(strip_markup(msgid))]
    if len(toks) < 6:
        return False
    kept = sum(1 for t in toks if t in msgstr)
    return kept / len(toks) >= 0.8


# ---------------------------------------------------------------------------
# Farsi text normalization
# ---------------------------------------------------------------------------

# Arabic-letter spelling variants used interchangeably in the corpus.
FA_CHAR_MAP = str.maketrans({
    "\u0622": "\u0627",   # آ
    "\u0623": "\u0627",   # أ
    "\u0625": "\u0627",   # إ
    "\u0671": "\u0627",   # ٱ
    "\u0626": "\u0621",   # ئ  (شیئی -> شیءی)
    "\u0624": "\u0621",   # ؤ
    "\u064A": "\u06CC",   # ي -> ی
    "\u0643": "\u06A9",   # ك -> ک
    "\u0629": "\u0647",   # ة -> ه
})


def normalize_fa(text: str) -> str:
    return text.translate(FA_CHAR_MAP)


# ---------------------------------------------------------------------------
# Glossary loading + pattern building
# ---------------------------------------------------------------------------

# Arabic *base letters* (not combining marks): a glossary variant followed
# by one of these is a different word, not the variant + a suffix.
AR_LETTERS = (
    "\u0620-\u064A"   # basic Arabic letters alef..yeh
    "\u066E-\u066F"   # dotless beh/qaf
    "\u0671-\u06D3"   # extended letters (peh, cheh, jeh, gaf, yeh barree...)
    "\u06EE-\u06FC"   # dal/rae with ring, waw/alef variants, ligatures
)
POST_BOUND = f"(?![{AR_LETTERS}A-Za-z0-9_])"
PRE_BOUND = f"(?<![{AR_LETTERS}A-Za-z])"

# ZWNJ is sometimes inserted inside words by editors/IMEs (شی‌ء for شیء),
# so allow an optional ZWNJ between every pair of letters of a variant word.
def flex_word(word: str) -> str:
    return "(?:\u200c)?".join(re.escape(c) for c in word)

# Common Persian clitics/suffixes that legitimately attach to a term,
# with or without ZWNJ: plural ها/های/هایی, indefinite/adjectival ی/یی,
# ezafe-ish ی, possessives (م/ت/ش/...), comparatives.
FA_SUFFIX = (
    r"(?:\u200c)?(?:هایی|های|ها|یی|ترین|تر|انه|شان|مان|تان"
    r"|یش|یت|یم|اش|ات|ام|ای|ش|ت|م|ی)?"
)

# Productive compound formers: شیءگرا (object-oriented), فهرست‌سازی, ...
# The term's concept is still present, so these count as a match.
FA_COMPOUND = (
    r"(?:\u200c)?(?:گرایی|گرا|محوری|محور|سازی|ساز|پذیری|پذیر|بندی|پایه|مند)?"
)
FA_TAIL = FA_COMPOUND + FA_SUFFIX

# Arabic broken plurals the corpus uses for glossary variants. Broken
# plurals are unpredictable, so they're listed explicitly here; extend
# as needed when reviewing violations.
ARABIC_PLURALS = {
    "تابع": ("توابع",),
    "مقدار": ("مقادیر",),
    "شیء": ("اشیاء", "اشیای", "اشیا"),
    "نوع": ("انواع",),
    "عدد": ("اعداد",),
    "عنصر": ("عناصر",),
    "عبارت": ("عبارات",),
    "استثنا": ("استثنائات",),
    "رابط": ("روابط",),
}

# Separable Persian preverbs: می/نمی/ن is inserted AFTER the preverb
# (برگرداندن -> برمی‌گرداند، برنمی‌گرداند). بر and باز are largely
# interchangeable in these compound verbs (برگرداندن ~ بازگرداندن).
PREVERB_ALTS = {
    "بر": "(?:بر|باز)",
    "باز": "(?:باز|بر)",
}
PREVERBS = ("بر", "باز", "در", "فر", "وا", "سر", "پیش", "رو")

# Endings that may follow a verb stem (past and present forms),
# longest first. "دن" covers the infinitive itself (بازگرداندن).
VERB_SUFFIX = r"(?:دیم|دید|دند|یند|دن|ده|دی|د|ید|یم|ند|ی)?"

# Same-concept verb pairs: forms of برگشتن (intransitive "return":
# بازمی‌گردد، برگشتی) are valid renderings of "return" alongside
# برگرداندن. Irregular present stems are listed in PRESENT_STEM.
RELATED_VERBS = {
    "برگرداندن": ("برگشتن",),
}
PRESENT_STEM = {
    "گشتن": "گرد",
}


def verb_pattern_for(infinitive: str) -> str:
    """Pattern for one infinitive's conjugated forms (no PRE_BOUND)."""
    preverb = ""
    rest = infinitive
    for pv in PREVERBS:
        if infinitive.startswith(pv) and len(infinitive) > len(pv) + 3:
            preverb, rest = pv, infinitive[len(pv):]
            break
    past = rest[:-1]
    stems = [past]
    short = rest[:-2]  # present stem for causatives (گرداندن -> گردان)
    if len(short) >= 3 and short not in stems:
        stems.append(short)
    if rest in PRESENT_STEM:
        stems.append(PRESENT_STEM[rest])
    stem_re = "(?:" + "|".join(flex_word(s) for s in stems) + ")"
    if preverb:
        pv_re = PREVERB_ALTS.get(preverb, re.escape(preverb))
        return (
            f"{pv_re}" + r"(?:\s|\u200c)?(?:می\u200c|نمی\u200c|ن)?"
            f"{stem_re}{VERB_SUFFIX}{POST_BOUND}"
        )
    return f"(?:می\u200c|نمی\u200c|ب|ن)?{stem_re}{VERB_SUFFIX}{POST_BOUND}"


def verb_pattern(w: str) -> str:
    """Single-word infinitive -> pattern covering its conjugated forms.

    پوشاندن -> پوشاند، پوشانده، می‌پوشاند، بپوشانید، می‌پوشانند ...
    برگرداندن -> برگرداند، برمی‌گرداند، برگردانید، بازگرداندند،
    and same-concept برگشتن forms (بازمی‌گردد، برگشتی) via RELATED_VERBS.
    """
    w = normalize_fa(w)
    alts = [verb_pattern_for(w)]
    for rel in RELATED_VERBS.get(w, ()):
        alts.append(verb_pattern_for(normalize_fa(rel)))
    return PRE_BOUND + "(?:" + "|".join(alts) + ")"


def word_alts(word: str) -> str:
    """Regex alternation for a variant word incl. known broken plurals."""
    word = normalize_fa(word)
    forms = [word] + [
        normalize_fa(p) for p in ARABIC_PLURALS.get(word, ()) if p != word
    ]
    return "(?:" + "|".join(flex_word(f) for f in forms) + ")"


def variant_to_regex(variant: str) -> str:
    """Build a tolerant regex for one glossary Farsi variant.

    Handles: ZWNJ-attached suffixes, known Arabic broken plurals,
    hamza/yeh spelling variants, verb variants in کردن/شدن infinitive
    form (conjugated stems still count, including می-insertion after
    separable preverbs), and multi-word variants with up to one filler
    word or a ZWNJ-plural in between
    (آرگومان کلیدواژه‌ای -> آرگومان‌های کلیدواژه‌ای).
    """
    v = normalize_fa(variant.strip())
    words = v.split()

    if len(words) == 1:
        w = words[0]
        if len(w) > 3 and w.endswith("ن") and w[-2] in "دت":
            return verb_pattern(w)
        return f"{PRE_BOUND}{word_alts(w)}{FA_TAIL}{POST_BOUND}"

    # multi-word variant
    last = words[-1]
    head = words[:-1]
    # between parts: plain space (+ up to one filler word), a bare ZWNJ
    # join (رشته‌مستند), or a ZWNJ-attached plural then a space
    # (آرگومان‌های کلیدواژه‌ای). A trailing ezafe mark (رشتهٔ) may
    # precede the space.
    marks = "\u064B-\u065F\u0670"
    fill = (
        r"(?:\u200c(?:هایی|های|ها)?\s+"
        r"|\u200c"
        rf"|[{marks}]*\s+(?:\S+\s+)?)"
    )

    if last == "کردن":
        h = fill.join(word_alts(w) for w in head)
        # verb stems, plus the deverbal noun (خنثی کردن -> خنثی‌سازی)
        noun = r"(?:\u200c)?(?:سازی|ساز)(?:\u200c(?:های|ها))?"
        return (
            f"{PRE_BOUND}{h}" + r"(?:\s+\S+){0,2}[\s\u200c]+"
            f"(?:می\u200c|نمی\u200c|ن)?"
            f"(?:کرد(?:ه|ی|یم|ید|ند|ن)?|کن(?:د|ید|یم|ند)?"
            f"|شد(?:ه|ی|یم|ید|ند|ن)?|شو(?:د|ند)?"
            f"|{noun}){POST_BOUND}"
        )
    if last == "شدن":
        h = fill.join(word_alts(w) for w in head)
        return (
            f"{PRE_BOUND}{h}" + r"(?:\s+\S+){0,2}[\s\u200c]+"
            f"(?:می\u200c|نمی\u200c|ن)?"
            f"(?:شد(?:ه|ی|یم|ید|ند|ن)?|شو(?:د|ند)?)?{POST_BOUND}"
        )
    parts = [word_alts(w) for w in words]
    return PRE_BOUND + fill.join(parts) + FA_TAIL + POST_BOUND


def term_to_regex(term: str) -> str:
    """English glossary term -> regex for locating it in msgid prose.

    Spaces and hyphens are interchangeable; an optional English plural
    suffix is allowed ("arguments" matches term "argument"). A leading
    dot or slash means code (list.index, if/while/def/class), not prose;
    for single-word terms a leading hyphen means a compound name
    (Content-type, first-class) and is rejected too.
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", term) if p]
    body = r"[\s\-]+".join(parts)
    pre = r"(?<![A-Za-z0-9_.\-])" if len(parts) == 1 else r"(?<![A-Za-z0-9_./])"
    return f"{pre}{body}(?:e?s)?(?![A-Za-z0-9_])"


def load_glossary(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    glossary = {}
    for term, variants_str in raw.items():
        variants = [
            v.strip() for v in re.split(r"[،,]", variants_str) if v.strip()
        ]
        glossary[term.lower()] = variants
    return glossary


def build_term_index(glossary):
    """One combined alternation (longest-first) -> list of (term) by group."""
    terms = sorted(glossary, key=len, reverse=True)
    alts = []
    index = []
    for i, term in enumerate(terms):
        alts.append(f"(?P<t{i}>{term_to_regex(term)})")
        index.append(term)
    combined = re.compile("|".join(alts), re.IGNORECASE)
    return combined, index


def build_variant_patterns(glossary):
    patterns = {}
    for term, variants in glossary.items():
        alt = "|".join(f"(?:{variant_to_regex(v)})" for v in variants)
        patterns[term] = re.compile(f"(?:{alt})")
    return patterns


# ---------------------------------------------------------------------------
# Entry loading
# ---------------------------------------------------------------------------

def iter_po_files(root: Path):
    for p in sorted(root.rglob("*.po")):
        if not (set(p.parts) & EXCLUDE_DIRS):
            yield p


def entry_translations(entry):
    """All msgstr texts of an entry (plural forms included)."""
    if entry.msgid_plural:
        return [s for s in entry.msgstr_plural.values() if s]
    return [entry.msgstr] if entry.msgstr else []


def load_entries(root: Path):
    entries = []
    n_files = 0
    for path in iter_po_files(root):
        n_files += 1
        try:
            po = polib.pofile(str(path))
        except Exception as e:
            print(f"WARNING: failed to parse {path}: {e}", file=sys.stderr)
            continue
        rel = str(path.relative_to(root))
        for entry in po:
            if entry.obsolete or entry.fuzzy or not entry.msgid:
                continue
            translations = entry_translations(entry)
            if not translations:
                continue  # untranslated
            entries.append({
                "file": rel,
                "line": entry.linenum,
                "msgid": entry.msgid,
                "msgstr": translations[0],
                "translations": translations,
                "code": looks_like_code_block(entry.msgid),
            })
    return entries, n_files


# ---------------------------------------------------------------------------
# Check 1: glossary violations
# ---------------------------------------------------------------------------

# Idiomatic English uses of glossary terms that don't refer to the concept
# (matched against the prose surrounding the term occurrence).
TERM_SKIP_CONTEXTS = {
    "instance": [re.compile(r"for\s+instance\b", re.I)],
    "type": [
        re.compile(r"\b(?:you|we|to)\s+type\b", re.I),
        re.compile(r"\btype\s+(?:the|in|it|this|these|them)\b", re.I),
        re.compile(r"\btype\s+of\b", re.I),
    ],
    "types": [re.compile(r"\btypes\s+of\b", re.I)],
    "return": [re.compile(r"\bin\s+return\b", re.I)],
    "escape": [re.compile(r"\bescape\s+key\b", re.I)],
    "function": [
        re.compile(r"\bto\s+function\b", re.I),
        re.compile(r"\bfunctions?\s+(?:as|correctly|properly|normally)\b", re.I),
        re.compile(r"\bfunctioning\b", re.I),
    ],
    "class": [
        re.compile(r"\bfirst[\s\-]class\b", re.I),
        re.compile(r"\bclass(?:es)?\s+of\b", re.I),
    ],
}


def term_in_skip_context(term: str, prose: str, m: re.Match) -> bool:
    keys = {term, m.group(0).lower()}
    for key in keys:
        for pat in TERM_SKIP_CONTEXTS.get(key, ()):
            for cm in pat.finditer(prose):
                if cm.start() <= m.start() < cm.end():
                    return True
    return False

def remove_kept_emphasis(prose: str, raw_msgid: str, msgstr: str) -> str:
    """Drop emphasized code-ish tokens (*encoding*, *op*, ...) that the
    translator kept verbatim -- those are parameter names, not prose."""
    for tok in set(EMPH_TOKEN_RE.findall(raw_msgid)):
        if tok in msgstr:
            prose = re.sub(
                r"(?<![A-Za-z0-9_.])" + re.escape(tok) + r"(?![A-Za-z0-9_])",
                " ", prose, flags=re.IGNORECASE,
            )
    return prose


def is_proper_noun_occurrence(prose: str, m: re.Match) -> bool:
    """Mid-sentence capitalized term followed by a capitalized word
    (Object Pascal, Boolean Method...) is a proper noun, not the term."""
    text = m.group(0)
    if not text[0].isupper():
        return False
    start = m.start()
    if start > 0:
        prev = prose[start - 1]
        if prev not in " \t\n([{'\"-":
            return False  # sentence start or after punctuation: keep it
    nxt = prose[m.end():m.end() + 30].split()
    return bool(nxt) and nxt[0][0].isupper()


def check_glossary(entries, glossary):
    combined, index = build_term_index(glossary)
    var_patterns = build_variant_patterns(glossary)

    violations = defaultdict(list)
    variant_usage = defaultdict(Counter)
    term_hits = Counter()

    for e in entries:
        msgid, msgstr = e["msgid"], e["msgstr"]
        if e["code"] or msgstr.strip() == msgid.strip():
            continue
        if mostly_preserved_code(msgid, msgstr):
            continue
        if standalone_code_entry(msgid):
            continue
        prose = strip_literal_blocks(msgid)
        prose = strip_markup(prose)
        prose = strip_quoted_code(prose)
        prose = remove_kept_emphasis(prose, msgid, msgstr)
        if not prose.strip():
            continue
        msgstr_n = normalize_fa(msgstr)

        found = set()
        for m in combined.finditer(prose):
            if is_proper_noun_occurrence(prose, m):
                continue
            term = index[int(m.lastgroup[1:])]
            if term_in_skip_context(term, prose, m):
                continue
            found.add(term)
        for term in found:
            term_hits[term] += 1
            if var_patterns[term].search(msgstr_n):
                for v in glossary[term]:
                    if re.search(variant_to_regex(v), msgstr_n):
                        variant_usage[term][v] += 1
            else:
                violations[term].append({
                    "file": e["file"],
                    "line": e["line"],
                    "msgid": msgid,
                    "msgstr": msgstr,
                    "allowed": glossary[term],
                })

    return {
        "violations": violations,
        "variant_usage": {t: dict(c) for t, c in variant_usage.items()},
        "term_hits": dict(term_hits),
    }


# ---------------------------------------------------------------------------
# Check 2: identical msgid, different msgstr
# ---------------------------------------------------------------------------

def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_duplicates(entries):
    groups = defaultdict(list)
    for e in entries:
        if e["code"]:
            continue
        key = norm_ws(e["msgid"])
        if not key:
            continue
        trans = tuple(norm_ws(t) for t in e["translations"])
        groups[key].append((trans, e))

    dupes = {}
    for key, occs in groups.items():
        distinct = {trans for trans, _ in occs}
        if len(distinct) < 2:
            continue
        by_variant = defaultdict(list)
        for trans, e in occs:
            by_variant[trans].append({"file": e["file"], "line": e["line"]})
        dupes[key] = {
            "occurrences": len(occs),
            "variants": [
                {
                    "msgstr": "\n".join(trans),
                    "count": len(locs),
                    "locations": locs[:10],
                }
                for trans, locs in sorted(
                    by_variant.items(), key=lambda kv: -len(kv[1])
                )
            ],
        }

    return dict(
        sorted(dupes.items(), key=lambda kv: -kv[1]["occurrences"])
    )


# ---------------------------------------------------------------------------
# Check 3: non-glossary terminology drift
# ---------------------------------------------------------------------------

ENGLISH_STOPWORDS = set("""
a an the and or but if then else when while for to of in on at by with
from as is are was were be been being this that these those it its it's
you your yours we our ours they their theirs he she his her him not no
nor so than too very can will would should could may might must shall
do does did done have has had having don't doesn't didn't isn't aren't
wasn't weren't won't wouldn't shouldn't couldn't mustn't i me my mine
us them what which who whom whose where why how all any both each few
more most other some such only own same new use used using uses also
into out up down over under again further once here there between see
also note notes example examples e.g one two three first second third
following however many much every well just like even since within
without via etc need needs needed want wants let lets say says take
takes give gives know known find found work works working call called
mean means meaning keep keeps run runs running look looks help helps
""".split())

MARKUP_NOISE = set("""
py class func meth mod exc data const attr obj term ref doc
versionadded versionchanged deprecated seealso rubric literalinclude
code-block highlight index note warning topic default-domain
""".split())

STOPWORDS = ENGLISH_STOPWORDS | MARKUP_NOISE

FARSI_WORD_RE = re.compile(r"[\u0600-\u06FF\u200c]+")
# U+060C ،  U+061B ؛  U+061F ؟  U+0640 ـ  are punctuation, not letters
FA_PUNCT = "\u060c\u061b\u061f\u0640"

FARSI_STOPWORDS = set("""
است را به از در که این با آن یک برای می‌شود می‌کند می‌توان تا هم نیز
شده شود کرد کند دارد دارند بود بودن باشد باشند ها های هایی و یا اگر
چون زیرا اما ولی هر همه بین روی زیر بالای کنار پس سپس دیگر خود
""".split())


def tokenize_en(text: str):
    return [w.lower().strip("-") for w in WORD_RE.findall(strip_markup(text))]


def tokenize_fa(text: str):
    tokens = FARSI_WORD_RE.findall(strip_markup(text, keep_prose_roles=False))
    return [
        t for t in tokens
        if t not in FARSI_STOPWORDS and len(t) > 1
        and not any(c in FA_PUNCT for c in t)
    ]


def check_drift(entries, glossary, min_freq,
                assoc_ratio=4.0, min_variant_count=3, max_examples=4):
    glossary_terms = set(glossary)

    unigrams = Counter()
    bigrams = Counter()
    occurrences = defaultdict(list)

    for e in entries:
        if e["code"] or e["msgstr"].strip() == e["msgid"].strip():
            continue
        if standalone_code_entry(e["msgid"]):
            continue
        if mostly_preserved_code(e["msgid"], e["msgstr"]):
            continue
        toks = [
            t for t in tokenize_en(e["msgid"])
            if len(t) > 2 and t not in STOPWORDS and t not in glossary_terms
        ]
        seen = set()
        for t in toks:
            unigrams[t] += 1
            seen.add(t)
        for i in range(len(toks) - 1):
            bg = f"{toks[i]} {toks[i + 1]}"
            bigrams[bg] += 1
            seen.add(bg)
        for t in seen:
            occurrences[t].append(e)

    candidates = {
        t for t, f in (unigrams | bigrams).items()
        if f >= min_freq and t.split()[0] not in glossary_terms
    }

    # corpus-wide Farsi baseline (document frequency)
    doc_freq = Counter()
    total = 0
    for e in entries:
        total += 1
        for w in set(tokenize_fa(e["msgstr"])):
            doc_freq[w] += 1

    drift = {}
    for term in candidates:
        occs = occurrences[term]
        n = len(occs)
        local = Counter()
        word_idx = defaultdict(list)
        for i, e in enumerate(occs):
            for w in set(tokenize_fa(e["msgstr"])):
                local[w] += 1
                word_idx[w].append(i)

        assoc = []
        for w, cnt in local.items():
            if cnt < min_variant_count:
                continue
            ratio = (cnt / n) / max(doc_freq[w] / total, 1e-6)
            if ratio >= assoc_ratio:
                assoc.append((w, cnt, ratio))

        if len(assoc) < 2:
            continue
        assoc.sort(key=lambda x: -x[1])
        drift[term] = {
            "frequency": n,
            "distinct_translations": len(assoc),
            "variants": [
                {
                    "farsi_word": w,
                    "count": cnt,
                    "association_ratio": round(ratio, 2),
                    "examples": [
                        {
                            "file": occs[i]["file"],
                            "line": occs[i]["line"],
                            "msgid": occs[i]["msgid"],
                            "msgstr": occs[i]["msgstr"],
                        }
                        for i in word_idx[w][:max_examples]
                    ],
                }
                for w, cnt, ratio in assoc
            ],
        }

    return dict(sorted(drift.items(), key=lambda kv: -kv[1]["frequency"]))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("po_dir", nargs="?", default=str(REPO_ROOT),
                    help="Directory containing .po files (default: repo root)")
    ap.add_argument("--glossary", default=str(REPO_ROOT / "glossary.json"),
                    help="Glossary JSON: term -> 'variant1، variant2'")
    ap.add_argument("--checks", default="glossary,duplicates,drift",
                    help="Comma-separated subset of: glossary,duplicates,drift")
    ap.add_argument("--min-freq", type=int, default=5,
                    help="Min occurrences for drift candidates (default: 5)")
    ap.add_argument("--assoc-ratio", type=float, default=4.0,
                    help="Min association ratio for drift (default: 4.0)")
    ap.add_argument("--out", default=str(REPO_ROOT / "reports" / "consistency_report.json"),
                    help="Output JSON report path")
    args = ap.parse_args()

    checks = {c.strip() for c in args.checks.split(",") if c.strip()}
    root = Path(args.po_dir)

    print(f"Loading glossary: {args.glossary}")
    glossary = load_glossary(Path(args.glossary))
    print(f"  {len(glossary)} terms")

    print(f"Loading .po files under {root} ...")
    entries, n_files = load_entries(root)
    print(f"  {n_files} files, {len(entries)} translated entries")

    report = {
        "summary": {
            "po_files": n_files,
            "translated_entries": len(entries),
            "glossary_terms": len(glossary),
        }
    }

    if "glossary" in checks:
        print("Check 1/3: glossary violations ...")
        res = check_glossary(entries, glossary)
        n_viol = sum(len(v) for v in res["violations"].values())
        report["summary"]["glossary_terms_with_violations"] = len(res["violations"])
        report["summary"]["glossary_violations_total"] = n_viol
        report["glossary_violations"] = res["violations"]
        report["glossary_variant_usage"] = res["variant_usage"]
        print(f"  {len(res['violations'])} terms with violations "
              f"({n_viol} flagged entries)")
        worst = sorted(res["violations"].items(), key=lambda kv: -len(kv[1]))[:10]
        for term, v in worst:
            print(f"    {term}: {len(v)}")

    if "duplicates" in checks:
        print("Check 2/3: identical msgid with different translations ...")
        dupes = check_duplicates(entries)
        report["summary"]["duplicate_msgids_with_drift"] = len(dupes)
        report["duplicate_drift"] = dupes
        print(f"  {len(dupes)} distinct msgids translated inconsistently")
        for key, data in list(dupes.items())[:10]:
            short = key if len(key) <= 60 else key[:57] + "..."
            print(f"    {data['occurrences']}x / {len(data['variants'])} variants: {short!r}")

    if "drift" in checks:
        print(f"Check 3/3: non-glossary drift (min-freq={args.min_freq}) ...")
        drift = check_drift(entries, glossary, args.min_freq,
                            assoc_ratio=args.assoc_ratio)
        report["summary"]["non_glossary_drift_terms"] = len(drift)
        report["non_glossary_drift"] = drift
        print(f"  {len(drift)} candidate terms with inconsistent rendering")
        for term, data in list(drift.items())[:10]:
            words = "، ".join(v["farsi_word"] for v in data["variants"][:4])
            print(f"    {term} ({data['frequency']}x): {words}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport written to {out}")


if __name__ == "__main__":
    main()
