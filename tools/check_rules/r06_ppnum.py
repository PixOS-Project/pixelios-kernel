"""R06 (CR-06): a 32-bit ppnum_t shifted by PAGE_SHIFT wraps for PA >= 4 GB; use ptoa(). Rejects `<< PAGE_SHIFT` on added (`+`) lines of
xnu/patches/*.patch outside comments. Spec: docs/CODING-RULES.md CR-06; PASS fixture 72f1323:xnu/patches/0108-pmap-sync-page-attributes-64bit-pa.patch."""
import re
from .lib import Finding, tracked, plus_lines, strip_c_comments

RULE = "R06"
PATTERNS = {
    "shift-page-shift": (re.compile(r"<<\s*PAGE_SHIFT\b"), "`<< PAGE_SHIFT` on a ppnum_t wraps at 4 GB: use ptoa(pp)"),
}


def check(root, patterns=None):
    patterns = PATTERNS if patterns is None else patterns
    out = []
    for rel, lines in tracked(root, "xnu/patches/*.patch"):
        for n, t in plus_lines(lines):
            code = strip_c_comments(t)
            for name, (rx, msg) in patterns.items():
                if rx.search(code):
                    out.append(Finding(rel, n, lines[n - 1], f"{RULE} {name}: {msg}"))
    return out
