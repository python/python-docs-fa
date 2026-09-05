#!/usr/bin/env python3
"""
find_untranslated_terms.py (v2)

Scan a directory tree of .po files with polib and find msgstr entries that
still contain a given English technical term verbatim (whole word, case
insensitive) in PROSE, even though the corresponding msgid also contains
that term in prose.

This version is markup-aware: Sphinx roles (:class:`Foo`, :func:`bar`,
:mod:`os`, ...), inline code spans (`...`, ``...``), literal/code blocks,
and quoted code-ish snippets are stripped out before matching, so words
that are only appearing as ROLE NAMES or inside CODE (e.g. "class" in
":class:`bool`", "return"/"raise"/"import" inside a doctest) are not
counted as "untranslated prose".

Fixes vs the first version:
  - .git (and other VCS/venv/cache dirs) is excluded from the file walk.
  - Sphinx role markup is stripped, not just backticks, which is why
    "class", "api", "import", "return", "value", "type" etc. were wildly
    inflated before (those words double as role names / Python keywords
    that show up constantly in code, not prose).
  - Full per-term hit lists are always written out (JSON/CSV); the
    console summary intentionally still only *shows* a few examples
    per term, since printing 5000+ lines to a terminal isn't useful --
    use --json/--csv to get everything.

Requires: polib  (pip install polib --break-system-packages)
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import polib
except ImportError:
    sys.exit("This script requires polib. Install with: pip install polib --break-system-packages")


TERMS = [
    "heap", "type", "value", "async", "wildcard", "index", "property",
    "bootstrapping", "mock", "pipe", "docstring", "object", "action",
    "local", "escape", "raise", "return", "list", "operator", "element",
    "import", "encoding", "global", "string", "class", "module",
    "function", "shell", "exception", "event", "coroutine", "interface",
    "cache", "command line", "package", "method", "widget", "symlink",
    "item", "generator", "loop", "runtime", "built-in", "namespace",
    "syntax", "argument", "wrapper", "load", "attribute", "thread",
    "api", "variable", "expression", "f-string", "callback",
]

EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"}

# ---------------------------------------------------------------------------
# Markup stripping (adapted from check_consistency.py's approach)
# ---------------------------------------------------------------------------

# Sphinx/reST roles whose *content* is code/identifiers, not prose.
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
EMPH_RE = re.compile(r"(\*\*?)(.+?)\1")
TARGET_RE = re.compile(r"^(.*)\s<[^<>]+>$")


def strip_markup(text, keep_prose_roles=True):
    """Remove code spans / role markup; keep the *display text* of prose
    roles like :term:`Foo` or :ref:`title <target>` since translators do
    translate that part."""
    def role_repl(m):
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


WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]*[A-Za-z]|[A-Za-z]")
QUOTED_CODE_RE = re.compile(r"'([\w.\-/ ]+)'|\"([\w.\-/ ]+)\"")


def quoted_span_is_code(content):
    words = content.split()
    return (
        len(words) == 1
        or any(c in content for c in ".-_")
        or content.startswith(("import ", "from "))
    )


def strip_quoted_code(text):
    def repl(m):
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


def looks_codeish(line):
    if line.startswith((">>>", "...", "$", "#")):
        return True
    if line.startswith(("'", '"', "[", "{", "<")) or line[0:1].isdigit():
        return True
    return bool(CODE_LINE_START_RE.match(line) or CODE_STMT_RE.match(line))


def prose_line(line):
    s = line.strip()
    if not s or looks_codeish(s):
        return False
    return len(s.split()) >= 3 and s[0].isupper()


def looks_like_code_block(msgid):
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


def standalone_code_entry(msgid):
    lines = [ln for ln in msgid.split("\n") if ln.strip()]
    if not lines:
        return False
    codeish = sum(1 for ln in lines if ln[0] in " \t" or looks_codeish(ln.strip()))
    prose = sum(1 for ln in lines if prose_line(ln))
    return codeish >= max(1, len(lines) // 2) and prose <= len(lines) // 3


def strip_literal_blocks(msgid):
    """Drop the code content after a '::' literal-block marker."""
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
                continue
            if ln[0] in " \t" or looks_codeish(stripped):
                continue
            in_block = False
            out.append(ln)
    return "\n".join(out)


def mostly_preserved_code(msgid, msgstr):
    """Code listings where only comments were translated: almost all
    English tokens of msgid reappear verbatim in msgstr."""
    toks = [t.lower() for t in WORD_RE.findall(strip_markup(msgid))]
    if len(toks) < 6:
        return False
    kept = sum(1 for t in toks if t in msgstr)
    return kept / len(toks) >= 0.8


# Bare code fragments embedded directly in prose with NO backticks/roles at
# all, e.g. "...(e.g. list.index()) but functions for other (e.g. len(list))"
# or "using ``list(dictview)``" -- covers dotted-call (name.method(...)),
# bare call (name(...)), and subscript/dunder-ish tokens.
BARE_DOTTED_CALL_RE = re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+\(\)?")
BARE_CALL_RE = re.compile(r"\b[A-Za-z_][\w]*\([^()\s]{0,40}\)")


def strip_bare_code_fragments(text):
    text = BARE_DOTTED_CALL_RE.sub(" ", text)
    text = BARE_CALL_RE.sub(" ", text)
    return text


def clean_prose(msgid):
    """Full pipeline: literal blocks -> markup -> quoted code -> bare code."""
    text = strip_literal_blocks(msgid)
    text = strip_markup(text)
    text = strip_quoted_code(text)
    text = strip_bare_code_fragments(text)
    return text


# ---------------------------------------------------------------------------
# Term matching
# ---------------------------------------------------------------------------

def build_term_pattern(term):
    escaped = re.escape(term)
    # spaces/hyphens interchangeable, optional plural
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", term) if p]
    body = r"[\s\-]+".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9_.\-]){body}(?:e?s)?(?![A-Za-z0-9_])", re.IGNORECASE)


TERM_PATTERNS = {term: build_term_pattern(term) for term in TERMS}


def iter_po_files(root):
    root = Path(root)
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*.po")):
        if not (set(p.parts) & EXCLUDE_DIRS):
            yield p


def scan_file(path):
    hits = []
    try:
        po = polib.pofile(str(path))
    except Exception as e:
        print(f"  ! failed to parse {path}: {e}", file=sys.stderr)
        return hits

    for entry in po:
        if entry.obsolete or entry.fuzzy or not entry.msgstr or not entry.msgid:
            continue

        msgid, msgstr = entry.msgid, entry.msgstr
        if msgstr.strip() == msgid.strip():
            continue  # identical: probably an untranslated code-only string
        if looks_like_code_block(msgid) or standalone_code_entry(msgid):
            continue
        if mostly_preserved_code(msgid, msgstr):
            continue

        msgid_prose = clean_prose(msgid)
        if not msgid_prose.strip():
            continue

        # Strip the same code/markup out of msgstr too. Otherwise a fully
        # correct translation still "matches" because the term survives
        # inside a code snippet embedded in the msgstr (e.g. Farsi prose
        # around a kept ``list.insert()`` call) even though nothing was
        # actually left untranslated in the prose itself.
        msgstr_prose = clean_prose(msgstr)

        for term, pattern in TERM_PATTERNS.items():
            if pattern.search(msgid_prose) and pattern.search(msgstr_prose):
                hits.append((term, entry.linenum, msgid, msgstr))

    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="Root directory to search for .po files (or a single .po file)")
    ap.add_argument("--json", metavar="PATH", help="Write full results as JSON")
    ap.add_argument("--csv", metavar="PATH", help="Write full results as CSV")
    ap.add_argument("--examples", type=int, default=4,
                     help="Example locations to show per term in the console summary (default 4)")
    args = ap.parse_args()

    po_files = list(iter_po_files(args.root))
    if not po_files:
        sys.exit(f"No .po files found under {args.root}")

    print(f"Scanning {len(po_files)} .po file(s) under {args.root} ...\n", file=sys.stderr)

    results = defaultdict(list)
    for path in po_files:
        for term, linenum, msgid, msgstr in scan_file(path):
            results[term].append({"file": str(path), "line": linenum, "msgid": msgid, "msgstr": msgstr})

    total_entries = sum(len(v) for v in results.values())
    print(f"English term kept in prose -- {total_entries} entries")
    print("The English term is left untranslated inside prose. If that is the intended")
    print("convention, add the English form to glossary.json; otherwise translate these:\n")

    for term in sorted(results.keys(), key=lambda t: -len(results[t])):
        entries = results[term]
        example_str = ", ".join(f"{e['file']}:{e['line']}" for e in entries[: args.examples])
        print(f"{term}: {len(entries)} entries (e.g. {example_str})\n")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Full results written to {args.json}", file=sys.stderr)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["term", "file", "line", "msgid", "msgstr"])
            for term, entries in results.items():
                for e in entries:
                    writer.writerow([term, e["file"], e["line"], e["msgid"], e["msgstr"]])
        print(f"Full results written to {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()