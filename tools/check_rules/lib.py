"""Shared helpers for tools/check-rules.py rule modules."""
import pathlib, re
from collections import namedtuple

Finding = namedtuple("Finding", "path line text msg")  # path: repo-relative posix; line: 1-based; text: the source line


def tracked(root, *globs):
    """Files under root matching the globs, as (relpath, [lines]). Reads the working tree (no git): the gate also runs in shallow clones."""
    root = pathlib.Path(root)
    seen = {}
    for g in globs:
        for p in sorted(root.glob(g)):
            if p.is_file():
                seen[p.relative_to(root).as_posix()] = p
    for rel, p in sorted(seen.items()):
        try:
            yield rel, p.read_text(errors="replace").splitlines()
        except OSError:
            continue


def strip_quotes(s):
    """Blank out '...' and "..." spans of one shell line (good enough for the chain lint: no multi-line strings)."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", '""', s)
