"""R21 (CR-21): an OSObject pointer kept beyond the call is retained. Heuristic on added (`+`) patch lines outside comments:
  pass-unretained   mapMemory(X->memory, ...): hands the borrowed pointer to a call that keeps it (review gpu-mali-reset-review-1 H1, 7500955)
  local-unretained  `IO.. *p = X->memory;` with no `p->retain()` within the next 3 added lines
  store-unretained  `A.b = X->memory;` / `A[i] = X->memory;` with no `retain()` within 3 added lines either side (OSSharedPtr is never matched)
Known gap: a store of a parameter (`fBo[slot].md = md;`) is not tracked. Spec: docs/CODING-RULES.md CR-21."""
import re
from .lib import Finding, tracked, plus_lines, strip_c_comments

RULE = "R21"
PASS_RX = re.compile(r"\bmapMemory\s*\(\s*\w+(?:->|\.)memory\b")
LOCAL_RX = re.compile(r"\bIO\w*\s*\*\s*(\w+)\s*=\s*\w+(?:->|\.)memory\s*;")
STORE_RX = re.compile(r"(?:\.|->|\])[\w\[\]\.\->]*\s*=\s*\w+(?:->|\.)memory\s*;")
RETAIN_RX = re.compile(r"\bretain\s*\(")
PATTERNS = {"pass-unretained": 1, "local-unretained": 1, "store-unretained": 1}   # enabled names (a mutant removes one)


def scan(lines, enabled=None):
    """lines: raw patch lines -> [(line no, pattern name)]"""
    enabled = PATTERNS if enabled is None else enabled
    added = [(n, strip_c_comments(t)) for n, t in plus_lines(lines)]
    hits = []
    for i, (n, code) in enumerate(added):
        if "pass-unretained" in enabled and PASS_RX.search(code):
            hits.append((n, "pass-unretained"))
        m = LOCAL_RX.search(code)
        if m and "local-unretained" in enabled:
            if not any(re.search(r"\b%s\s*->\s*retain\s*\(" % re.escape(m.group(1)), c) for _, c in added[i + 1:i + 4]):
                hits.append((n, "local-unretained"))
        elif "store-unretained" in enabled and STORE_RX.search(code) and not LOCAL_RX.search(code):
            if not any(RETAIN_RX.search(c) for _, c in added[max(0, i - 3):i + 4]):
                hits.append((n, "store-unretained"))
    return hits


MSG = {"pass-unretained": "borrowed ->memory passed to mapMemory (which keeps it): retain first",
       "local-unretained": "`->memory` copied to a local with no retain() in the next 3 added lines",
       "store-unretained": "`->memory` stored into a member/array with no retain() within 3 added lines"}


def check(root, enabled=None):
    out = []
    for rel, lines in tracked(root, "xnu/patches/*.patch"):
        for n, name in scan(lines, enabled):
            out.append(Finding(rel, n, lines[n - 1], f"{RULE} {name}: {MSG[name]}"))
    return out
