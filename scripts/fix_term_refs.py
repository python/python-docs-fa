#!/usr/bin/env python3
"""
fix_term_refs.py

Converts plain Sphinx `:term:`English`` references inside the *translated*
(msgstr) strings of a gettext .po file into the explicit-title form:

    :term:`Persian translation <English>`

so that the reader sees translated text while the link still resolves to the
correct (English) glossary entry ID.

The English msgid lines are left completely untouched. Only msgstr strings
are modified, and only occurrences of :term:`X` where X has no `<...>`
already (i.e. plain references, not ones that already specify a custom title).

Persian translations are looked up in two places, in priority order:
  1. This .po file's own entries — i.e. if somewhere in the file there's a
     msgid "duck-typing" / msgstr "نوع‌دهی اردکی", that translation is used.
  2. A fallback dictionary (FALLBACK_MAP below) for terms that aren't
     separately defined in the .po file itself. Edit/extend this dict for
     your own glossary as needed.

If a term can't be resolved through either source, the reference is left
unchanged and reported at the end so you can add it to FALLBACK_MAP or fix
the source file.

Usage:
    python3 fix_term_refs.py input.po output.po

If output.po is omitted, writes to input.fixed.po next to the input file.
"""

import re
import sys

# ---------------------------------------------------------------------------
# Fallback English -> target-language translations, used only when a term
# isn't itself defined as a msgid/msgstr pair inside the .po file.
# Extend this for your own glossary/language as needed.
# ---------------------------------------------------------------------------
FALLBACK_MAP = {
    "accessibility": "دسترسی‌پذیری", "await": "await", "argument": "آرگومان",
    "async": "ناهمگام، غیرهمگام", "API": "API", "attribute": "ویژگی، صفت، شاخصه",
    "boolean": "بولی", "built-in": "توکار، درونی، درون‌ساخته",
    "callback": "کال‌بک، فراخوانی بازگشتی", "character": "نویسه",
    "context management": "مدیریت زمینه", "class": "کلاس", "cache": "نهانگاه",
    "coroutine": "هم‌روال", "command line": "خط فرمان", "community": "کامیونیتی",
    "component": "کامپوننت", "custom": "سفارشی، اختصاصی",
    "decorator": "دکوراتور، آراینده", "debugging": "اشکال‌زدایی، دیباگ کردن",
    "decoding": "کدگشایی", "deprecated": "منسوخ، از رده خارج شده",
    "dependency": "وابستگی", "dictionary": "دیکشنری", "directory": "پوشه",
    "duck-typing": "نوع‌دهی اردکی", "DOM": "DOM", "element": "المان، عنصر",
    "endpoint": "پایانه", "escape": "خنثی کردن", "encoding": "کدگذاری",
    "ecosystem": "اکوسیستم", "event": "رویداد", "exception": "استثنا",
    "expression": "عبارت", "function": "تابع", "f-string": "اف‌استرینگ",
    "generator": "تولیدگر", "global": "سراسری", "garbage collection": "زباله‌روبی",
    "generic function": "تابع عام، تابع عمومی", "hexadecimal": "مبنای شانزده",
    "immortal": "نامیرا", "import": "ایمپورت", "immutable": "تغییرناپذیر",
    "index": "اندیس، شماره", "instance": "نمونه", "integer": "عدد صحیح",
    "interface": "رابط", "interpreter": "مفسر", "item": "آیتم",
    "iterable": "تکرارپذیر", "keyword": "کلیدواژه",
    "keyword argument": "آرگومان کلیدواژه‌ای", "list": "فهرست",
    "list comprehension": "درک فهرستی", "load": "بارگذاری", "loader": "بارگذار",
    "local": "محلی", "loop": "حلقه", "method": "متد", "metaclass": "فراکلاس",
    "mock": "ماک", "module": "ماژول", "mutable": "تغییرپذیر",
    "namespace": "نام‌فضا", "object": "شیء", "operator": "عملگر",
    "package": "بسته", "parameter": "پارامتر", "positional": "جایگاهی",
    "property": "ویژگی، پراپرتی، خصوصیت", "parallelism": "موازی‌سازی",
    "quotation": "علامت نقل‌قول", "raise": "پرتاب", "return": "بازگشت، برگرداندن",
    "runtime": "ران‌تایم", "race": "رقابت", "scope": "محدوده",
    "shadowing": "پوشاندن", "stack traceback": "ردگیری پشته", "statement": "دستور",
    "string": "رشته", "syntax": "سینتکس، نحو", "shell": "پوسته",
    "syntactic sugar": "قند نحوی", "tracking": "پیگیری",
    "type": "نوع، نوع داده، تایپ", "thread": "نخ", "unit test": "یونیت تست",
    "unpacking": "واگشایی", "value": "مقدار", "variable": "متغیر",
    "wrapper": "پوششی، دربرگیرنده", "iterator": "تکرارگر",
}

# Matches BOTH forms so both get (re)normalized to :term:`Persian <target>`:
#   :term:`X`                -- plain ref, target/display are both X
#   :term:`Something <X>`    -- already has explicit title (target is X);
#                                "Something" may be stale English display
#                                text left over from the source file.
# Group 'target' is always the real glossary-entry id to link to.
TERM_RE = re.compile(r':term:`(?:(?P<target_only>[^`<>]+)|[^`<>]*<(?P<target_bracketed>[^`<>]+)>)`')


def po_unescape(s):
    return s.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')


def po_escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def parse_po_string_block(lines, i, n):
    """Parse a 'msgid "..."' or 'msgstr "..."' plus any continuation quoted
    lines. Returns (keyword, parts, next_index)."""
    m = re.match(r'^(msgid|msgstr)\s+"(.*)"\s*$', lines[i])
    keyword = m.group(1)
    parts = [m.group(2)]
    j = i + 1
    while j < n and re.match(r'^\s*"(.*)"\s*$', lines[j]):
        parts.append(re.match(r'^\s*"(.*)"\s*$', lines[j]).group(1))
        j += 1
    return keyword, parts, j


def parse_po(text):
    """Parse .po text into a list of entries: pairs (msgid/msgstr) or other
    raw lines (comments, headers, blanks), preserving order."""
    lines = text.split("\n")
    n = len(lines)
    entries = []
    i = 0
    while i < n:
        if re.match(r'^msgid\s+"', lines[i]):
            _, id_parts, j1 = parse_po_string_block(lines, i, n)
            if j1 < n and re.match(r'^msgstr\s+"', lines[j1]):
                _, str_parts, j2 = parse_po_string_block(lines, j1, n)
                entries.append({"type": "pair", "msgid_parts": id_parts,
                                 "msgstr_parts": str_parts})
                i = j2
            else:
                entries.append({"type": "other", "raw": [lines[i]]})
                i = i + 1  # fall back to line-by-line if malformed
        else:
            entries.append({"type": "other", "raw": [lines[i]]})
            i += 1
    return entries


def join_parts_decoded(parts):
    return "".join(po_unescape(p) for p in parts)


def build_term_map(entries):
    """Build English-term -> translation dict from the .po file's own short,
    markup-free msgid/msgstr pairs (these are glossary entry definitions)."""
    term_map = {}
    for e in entries:
        if e["type"] != "pair":
            continue
        msgid_full = join_parts_decoded(e["msgid_parts"]).strip()
        msgstr_full = join_parts_decoded(e["msgstr_parts"]).strip()
        if not msgid_full or not msgstr_full:
            continue
        if re.search(r'[`:]', msgid_full) or len(msgid_full) > 60:
            continue
        term_map[msgid_full] = msgstr_full
    return term_map


def fix_po(text):
    """Run the conversion. Returns (new_text, changed_count, missing_terms)."""
    entries = parse_po(text)
    term_map = build_term_map(entries)
    missing = set()

    def translate(term):
        if term in term_map:
            return term_map[term]
        if term in FALLBACK_MAP:
            return FALLBACK_MAP[term]
        return None

    def replace_in_text(s):
        def repl(m):
            term = m.group('target_only') or m.group('target_bracketed')
            translated = translate(term)
            if translated is None:
                missing.add(term)
                return m.group(0)  # leave completely unchanged
            return f':term:`{translated} <{term}>`'
        return TERM_RE.sub(repl, s)

    changed_count = 0
    for e in entries:
        if e["type"] != "pair":
            continue
        msgstr_full = join_parts_decoded(e["msgstr_parts"])
        if ':term:`' not in msgstr_full:
            continue
        new_full = replace_in_text(msgstr_full)
        if new_full == msgstr_full:
            continue
        changed_count += 1
        if len(e["msgstr_parts"]) > 1:
            segs = new_full.split("\n")
            new_parts = [""]
            for k, seg in enumerate(segs):
                suffix = "\\n" if k < len(segs) - 1 else ""
                new_parts.append(po_escape(seg) + suffix)
            e["msgstr_parts"] = new_parts
        else:
            e["msgstr_parts"] = [po_escape(new_full)]

    out_lines = []
    for e in entries:
        if e["type"] == "other":
            out_lines.extend(e["raw"])
        else:
            out_lines.append(f'msgid "{e["msgid_parts"][0]}"')
            for p in e["msgid_parts"][1:]:
                out_lines.append(f'"{p}"')
            out_lines.append(f'msgstr "{e["msgstr_parts"][0]}"')
            for p in e["msgstr_parts"][1:]:
                out_lines.append(f'"{p}"')

    return "\n".join(out_lines), changed_count, missing


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fix_term_refs.py input.po [output.po]")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else re.sub(
        r'\.po$', '.fixed.po', in_path)

    with open(in_path, encoding="utf-8") as f:
        text = f.read()

    new_text, changed_count, missing = fix_po(text)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_text)

    print(f"Wrote: {out_path}")
    print(f"Changed msgstr entries: {changed_count}")
    if missing:
        print(f"Terms left unchanged (no translation found, {len(missing)}):")
        for t in sorted(missing):
            print(f"  - {t}")
    else:
        print("All :term: references resolved.")


if __name__ == "__main__":
    main()