#!/usr/bin/env python3
"""
scripts/po_merge_back.py

Copy translations from one branch of this repo (default: 3.15) onto another
(default: 3.14), matching entries by (msgctxt, msgid).

    python3 scripts/po_merge_back.py [--dry-run] [options]

The script drives git itself, using temporary `git worktree` checkouts, so your
current branch and working tree are never touched:

  * the SOURCE branch is read from a throw-away detached worktree;
  * the DESTINATION branch is edited in a worktree of its own (or in the
    existing checkout, if that branch is already checked out somewhere);
  * the merged .po files are committed on the destination branch (local only;
    add --push to publish it, or --no-commit to review the diff by hand).

Rules, per entry present in both files with an identical msgid:
  * SRC entry untranslated or fuzzy          -> ignored
  * DST entry untranslated or fuzzy          -> SRC translation copied; the
                                                fuzzy flag and #| lines removed
  * both translated, same text               -> nothing to do
  * both translated, different text          -> CONFLICT: DST kept and listed
                                                (--overwrite takes SRC instead)
Entries whose msgid exists on only one side are never touched, headers are left
alone, and only the msgstr lines of merged entries are rewritten (raw text is
copied from SRC), so the git diff shows just the merged strings.
No third-party dependencies; needs git >= 2.17 (`git worktree`).

Examples:
    python3 scripts/po_merge_back.py --dry-run          # 3.15 -> 3.14, report only
    python3 scripts/po_merge_back.py --fetch            # refresh from origin first, commit on 3.14
    python3 scripts/po_merge_back.py --new-branch merge-3.15-into-3.14 --push   # for a PR
    python3 scripts/po_merge_back.py --no-commit        # leave the edits for manual review
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC_BRANCH = "3.15"
DEFAULT_DST_BRANCH = "3.14"

IGNORED = {".git", ".cpython-src", ".pot-templates", "glossary-searcher", "node_modules"}
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


# ---------------------------------------------------------------------------
# PO parsing / merging (unchanged logic)
# ---------------------------------------------------------------------------

def unquote(line: str) -> str:
    s = line.strip()
    assert s.startswith('"') and s.endswith('"'), line
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), "\\" + m.group(1)), s[1:-1])


class Entry:
    """One PO entry, as a slice of the file's lines."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.flags: set[str] = set()
        self.ctxt: str | None = None
        self.id = ""
        self.plural: str | None = None
        self.strs: dict[int, str] = {}          # decoded msgstr / msgstr[n]
        self.str_span: tuple[int, int] | None = None   # [start, end) of msgstr lines
        self.obsolete = all(l.startswith("#~") for l in lines if l.strip())
        self._parse()

    def _parse(self):
        field, idx = None, 0
        for i, l in enumerate(self.lines):
            if l.startswith("#,"):
                self.flags |= {f.strip() for f in l[2:].split(",")}
                continue
            if l.startswith("#"):
                continue
            m = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(\".*\")\s*$", l)
            if m:
                key = m.group(1).split("[")[0]
                val = unquote(m.group(3))
                if key == "msgctxt":
                    self.ctxt, field = val, "ctxt"
                elif key == "msgid":
                    self.id, field = val, "id"
                elif key == "msgid_plural":
                    self.plural, field = val, "plural"
                else:
                    idx = int(m.group(2) or 0)
                    self.strs[idx] = val
                    field = ("str", idx)
                    if self.str_span is None:
                        self.str_span = (i, i + 1)
                    else:
                        self.str_span = (self.str_span[0], i + 1)
                continue
            if l.startswith('"') and field:
                v = unquote(l)
                if field == "ctxt": self.ctxt += v
                elif field == "id": self.id += v
                elif field == "plural": self.plural += v
                else: self.strs[field[1]] += v
                if isinstance(field, tuple):
                    self.str_span = (self.str_span[0], i + 1)

    @property
    def key(self):
        return (self.ctxt, self.id)

    @property
    def translated(self) -> bool:
        return (not self.obsolete and "fuzzy" not in self.flags and bool(self.strs)
                and all(v.strip() for v in self.strs.values()))

    @property
    def text(self):
        return tuple(self.strs[k] for k in sorted(self.strs))

    def msgstr_lines(self) -> list[str]:
        a, b = self.str_span
        return self.lines[a:b]


def parse(path: Path):
    raw = path.read_text(encoding="utf-8").split("\n")
    blocks, cur = [], []
    for l in raw:
        if l.strip() == "":
            if cur:
                blocks.append(("entry", cur)); cur = []
            blocks.append(("blank", [l]))
        else:
            cur.append(l)
    if cur:
        blocks.append(("entry", cur))
    return [(k, Entry(v) if k == "entry" else v) for k, v in blocks]


def clean_fuzzy(lines: list[str]) -> list[str]:
    out = []
    for l in lines:
        if l.startswith("#|"):
            continue
        if l.startswith("#,"):
            fl = [f.strip() for f in l[2:].split(",") if f.strip() and f.strip() != "fuzzy"]
            if fl:
                out.append("#, " + ", ".join(fl))
            continue
        out.append(l)
    return out


def merge_file(src: Path, dst: Path, overwrite: bool):
    sidx = {e.key: e for k, e in parse(src) if k == "entry" and e.id != "" and e.translated}
    blocks = parse(dst)
    merged, same, conflicts = 0, 0, []
    out_lines: list[str] = []
    for kind, item in blocks:
        if kind == "blank":
            out_lines += item
            continue
        e: Entry = item
        se = sidx.get(e.key)
        take = False
        if e.id != "" and not e.obsolete and se is not None and e.str_span and se.str_span:
            if not e.translated:
                take = True
            elif e.text == se.text:
                same += 1
            elif overwrite:
                take = True
            else:
                conflicts.append((e.id, e.text, se.text))
        if take:
            a, b = e.str_span
            new = clean_fuzzy(e.lines[:a]) + se.msgstr_lines() + e.lines[b:]
            out_lines += new
            merged += 1
        else:
            out_lines += e.lines
    return "\n".join(out_lines), merged, same, conflicts


def po_files(root: Path):
    for p in sorted(root.rglob("*.po")):
        if not any(part in IGNORED for part in p.relative_to(root).parts):
            yield p.relative_to(root)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

class GitError(RuntimeError):
    pass


def git(*args: str, cwd: Path = REPO_ROOT, check: bool = True) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    if check and r.returncode != 0:
        raise GitError(f"`git {' '.join(args)}` failed:\n{(r.stderr or r.stdout).strip()}")
    return r


def ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def short_sha(ref: str) -> str:
    return git("rev-parse", "--short", ref).stdout.strip()


def resolve_branch(branch: str) -> str | None:
    """Local branch if there is one, otherwise origin/<branch>, otherwise None."""
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        if ref_exists(ref):
            return ref
    return None


def warn_if_diverged(branch: str) -> None:
    """Tell the user when the local branch and origin/<branch> have drifted apart."""
    local, remote = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
    if not (ref_exists(local) and ref_exists(remote)):
        return
    behind = int(git("rev-list", "--count", f"{local}..{remote}").stdout)
    ahead = int(git("rev-list", "--count", f"{remote}..{local}").stdout)
    if behind:
        print(f"warning: local {branch} is {behind} commit(s) behind origin/{branch} "
              f"(run `git fetch` and update it, or pass --fetch)")
    if ahead:
        print(f"note: local {branch} is {ahead} commit(s) ahead of origin/{branch}")


def checked_out_worktrees() -> dict[str, Path]:
    """Map 'branch name' -> path for every worktree that has a branch checked out."""
    out: dict[str, Path] = {}
    path, branch = None, None
    for line in git("worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif line == "":
            if path and branch:
                out[branch] = path
            path, branch = None, None
    return out


def count_po(root: Path) -> int:
    return sum(1 for _ in po_files(root))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=DEFAULT_SRC_BRANCH, metavar="BRANCH",
                    help=f"branch with the newer translations (default: {DEFAULT_SRC_BRANCH})")
    ap.add_argument("--dst", default=DEFAULT_DST_BRANCH, metavar="BRANCH",
                    help=f"branch that receives them (default: {DEFAULT_DST_BRANCH})")
    ap.add_argument("--new-branch", metavar="NAME",
                    help="don't touch DST itself: create NAME from DST and commit there (for a PR)")
    ap.add_argument("--fetch", action="store_true", help="`git fetch origin` for both branches first")
    ap.add_argument("--dry-run", action="store_true", help="report only, write and commit nothing")
    ap.add_argument("--no-commit", action="store_true",
                    help="write the merged files but don't commit; the worktree is kept for review")
    ap.add_argument("--push", action="store_true", help="push the resulting branch to origin after committing")
    ap.add_argument("--overwrite", action="store_true",
                    help="on conflicts take the SRC translation instead of keeping DST's")
    ap.add_argument("--copy-new-files", action="store_true", help="copy .po files that exist only in SRC")
    ap.add_argument("--conflicts-file", type=Path, help="write conflict details to this file")
    a = ap.parse_args()

    if a.push and (a.no_commit or a.dry_run):
        sys.exit("error: --push needs a commit (drop --no-commit / --dry-run)")
    if a.src == a.dst:
        sys.exit("error: --src and --dst are the same branch")
    if a.new_branch and a.new_branch in (a.src, a.dst):
        sys.exit("error: --new-branch must be a new name")

    try:
        return run(a)
    except GitError as exc:
        sys.exit(f"error: {exc}")


def run(a: argparse.Namespace) -> int:
    if git("rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        raise GitError(f"{REPO_ROOT} is not a git repository")

    if a.fetch:
        print("Fetching origin ...")
        git("fetch", "origin", a.src, a.dst)

    src_ref, dst_ref = resolve_branch(a.src), resolve_branch(a.dst)
    for name, ref in ((a.src, src_ref), (a.dst, dst_ref)):
        if ref is None:
            raise GitError(f"branch '{name}' not found locally or as origin/{name} "
                           "(try --fetch, or `git fetch origin`)")
    warn_if_diverged(a.src)
    warn_if_diverged(a.dst)
    if a.new_branch and ref_exists(f"refs/heads/{a.new_branch}"):
        raise GitError(f"branch '{a.new_branch}' already exists")

    print(f"SRC: {a.src} @ {short_sha(src_ref)}   (committed state; uncommitted edits are ignored)")
    print(f"DST: {a.dst} @ {short_sha(dst_ref)}" + (f"   -> new branch {a.new_branch}" if a.new_branch else ""))

    tmp = Path(tempfile.mkdtemp(prefix="po-merge-back-"))
    created: list[Path] = []     # worktrees we made (removed on exit unless kept)
    keep_dst = False
    try:
        # --- SOURCE: detached, read-only worktree --------------------------
        src_dir = tmp / "src"
        git("worktree", "add", "--detach", "--quiet", str(src_dir), src_ref)
        created.append(src_dir)

        # --- DESTINATION ---------------------------------------------------
        existing = checked_out_worktrees().get(a.dst)
        dst_branch = a.new_branch or a.dst
        if existing and not a.new_branch:
            dst_dir = existing
            dirty = git("status", "--porcelain", "--untracked-files=no", cwd=dst_dir).stdout.strip()
            print(f"DST is already checked out at {dst_dir}; editing it in place.")
            if dirty and not a.dry_run:
                raise GitError(f"{dst_dir} has uncommitted changes; commit/stash them first")
            dst_created = False
        else:
            dst_dir = tmp / "dst"
            if a.new_branch:
                git("worktree", "add", "--quiet", "-b", a.new_branch, str(dst_dir), dst_ref)
            elif ref_exists(f"refs/heads/{a.dst}"):
                git("worktree", "add", "--quiet", str(dst_dir), a.dst)
            else:   # only origin/<dst> exists: create the tracking branch
                git("worktree", "add", "--quiet", "--track", "-b", a.dst, str(dst_dir), f"origin/{a.dst}")
            created.append(dst_dir)
            dst_created = True

        for label, d in (("SRC", src_dir), ("DST", dst_dir)):
            n = count_po(d)
            print(f"{label}: {n} .po files")
            if n == 0:
                raise GitError(f"no .po files found in {label} branch")
        print()

        # --- merge ---------------------------------------------------------
        tm = tc = scanned = 0
        changed, clines, only_src, copied = [], [], [], []
        for rel in po_files(src_dir):
            dst = dst_dir / rel
            if not dst.exists():
                only_src.append(rel)
                if a.copy_new_files and not a.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_dir / rel, dst)
                    copied.append(rel)
                continue
            text, m, _same, conf = merge_file(src_dir / rel, dst, a.overwrite)
            tm += m; tc += len(conf); scanned += 1
            for mid, keep, other in conf:
                clines.append(f"## {rel}\nmsgid:      {mid}\nDST (kept): {' | '.join(keep)}\nSRC:        {' | '.join(other)}\n")
            if m:
                changed.append((rel, m))
                if not a.dry_run:
                    dst.write_text(text, encoding="utf-8")

        for rel, n in changed:
            print(f"{n:5d}  {rel}")
        print(f"\n{scanned} file pairs compared.")
        print(f"{tm} strings merged into {len(changed)} files; {tc} conflicts "
              f"{'overwritten' if a.overwrite else 'kept as DST'}; {len(only_src)} files only in SRC "
              f"({'copied' if copied else 'skipped'}).")
        if a.conflicts_file and clines:
            a.conflicts_file.write_text("\n".join(clines), encoding="utf-8")
            print(f"conflicts written to {a.conflicts_file}")
        if only_src:
            print("only in SRC:", ", ".join(map(str, only_src[:15])), "..." if len(only_src) > 15 else "")

        if a.dry_run:
            print("(dry run: nothing written)")
            return 0

        # --- commit / push -------------------------------------------------
        touched = [str(r) for r, _ in changed] + [str(r) for r in copied]
        if not touched:
            print("Nothing to commit.")
            return 0
        if a.no_commit:
            keep_dst = dst_created
            print(f"\nEdits left uncommitted in {dst_dir} (branch {dst_branch}).")
            if dst_created:
                print(f"Review with `git -C {dst_dir} diff`, then commit there; remove the worktree with\n"
                      f"  git worktree remove --force {dst_dir}")
            return 0

        git("add", "--", *touched, cwd=dst_dir)
        msg = (f"chore: merge translations from {a.src} into {a.dst}\n\n"
               f"{tm} string(s) in {len(changed)} file(s), matched by (msgctxt, msgid).\n"
               f"Source: {a.src} @ {short_sha(src_ref)}")
        git("commit", "-m", msg, "--", *touched, cwd=dst_dir)
        print(f"\nCommitted on {dst_branch}: {git('rev-parse', '--short', 'HEAD', cwd=dst_dir).stdout.strip()}")
        if a.push:
            git("push", "-u", "origin", dst_branch, cwd=dst_dir)
            print(f"Pushed {dst_branch} to origin.")
        else:
            print(f"Not pushed. When ready: git push origin {dst_branch}")
        return 0
    finally:
        for d in created:
            if d == tmp / "dst" and keep_dst:
                continue
            git("worktree", "remove", "--force", str(d), check=False)
        git("worktree", "prune", check=False)
        if not keep_dst:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())