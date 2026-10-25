"""Shared helpers for tools/check-rules.py rule modules."""
import pathlib, re, subprocess
from collections import namedtuple

Finding = namedtuple("Finding", "path line text msg")  # path: repo-relative posix; line: 1-based; text: the source line


def tracked(root, *globs):
    """Files under root matching the globs, as (relpath, [lines]). Reads the working tree; in a git work tree only `git ls-files` paths count, so
    untracked local files (.git/info/exclude notes) cannot change the publish gate. Non-git dirs (tests) scan everything."""
    root = pathlib.Path(root)
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    keep = {x for x in r.stdout.decode(errors="replace").split("\0") if x} if r.returncode == 0 else None
    seen = {}
    for g in globs:
        for p in sorted(root.glob(g)):
            if p.is_file() and (keep is None or p.relative_to(root).as_posix() in keep):
                seen[p.relative_to(root).as_posix()] = p
    for rel, p in sorted(seen.items()):
        try:
            yield rel, p.read_text(errors="replace").splitlines()
        except OSError:
            continue


def strip_quotes(s):
    """Blank out '...' and "..." spans of one shell line (good enough for the chain lint: no multi-line strings)."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", '""', s)


def strip_c_comments(s):
    """Blank /* ... */ (one line) and // comments of one C line; string literals are not parsed (good enough for the patch lints)."""
    return re.sub(r"/\*.*?\*/|//.*$", " ", s)


def plus_lines(lines):
    """Added lines of a unified diff / .patch file: (1-based line no, text without the leading '+'), skipping the '+++' header."""
    for n, l in enumerate(lines, 1):
        if l.startswith("+") and not l.startswith("+++"):
            yield n, l[1:]
