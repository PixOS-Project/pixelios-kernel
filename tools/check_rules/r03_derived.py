"""R03 (CR-03): lists, step ranges and ids are derived from the table, never typed. Rejects
  block-tuple  >= 3 consecutive quoted wlan_seq.json block names (read from the table's "blocks") in tools/*.py, shim/*.c, shim/*.h
  step-range   a literal `S\\d\\d..S\\d\\d` in tools/*.py, shim/*.c, shim/*.h
  json-cite    `wlan_seq.json:N` / `soc.json:N` anywhere in tools/, shim/, docs/ (a line number moves; cite (step, blk, off) or a T-id)
docs/CODING-RULES.md and the rule's own test are exempt: they quote the bad forms. No PASS/FAIL replay pair exists for the tuple/range forms (backlog row 7), so the tree
baseline holds the present hits. Spec: docs/CODING-RULES.md CR-03."""
import json, pathlib, re
from .lib import Finding, tracked

RULE = "R03"
TABLE = "soc/google/gs101/wlan_seq.json"
RANGE_RX = re.compile(r"\bS\d\d\.\.S\d\d\b")
CITE_RX = re.compile(r"\b(?:wlan_seq|soc)\.json:\d+")
EXEMPT = {"docs/CODING-RULES.md", "tools/test_check_rules_more.py"}   # the rules doc and this rule's own synthetic samples quote the bad forms


def blocks(root):
    f = pathlib.Path(root) / TABLE
    if not f.exists():
        raise SystemExit(f"RESULT: FAIL R03 needs {TABLE} (block names are derived from it)")
    return json.loads(f.read_text())["blocks"]


def tuple_rx(names):
    alt = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    q = rf"[\"'](?:{alt})[\"']"
    return re.compile(rf"{q}(?:\s*,\s*{q}){{2,}}")


def check(root, kinds=("block-tuple", "step-range", "json-cite")):
    out = []
    code = list(tracked(root, "tools/*.py", "shim/*.c", "shim/*.h"))
    if "block-tuple" in kinds and code:
        trx = tuple_rx(blocks(root))
        for rel, lines in code:
            if rel in EXEMPT:
                continue
            for n, l in enumerate(lines, 1):
                if trx.search(l):
                    out.append(Finding(rel, n, l, f"{RULE} block-tuple: literal tuple of table block names: derive it from wlan_seq.json blocks"))
    if "step-range" in kinds:
        for rel, lines in code:
            if rel in EXEMPT:
                continue
            for n, l in enumerate(lines, 1):
                if RANGE_RX.search(l):
                    out.append(Finding(rel, n, l, f"{RULE} step-range: literal step range: derive first/last step from the table"))
    if "json-cite" in kinds:
        for rel, lines in tracked(root, "tools/*", "shim/*.c", "shim/*.h", "docs/**/*.md"):
            if rel in EXEMPT or rel.startswith("tools/testdata/") or rel.startswith("tools/check_rules/") or rel.startswith("tools/test_check_rules"):
                continue
            for n, l in enumerate(lines, 1):
                if CITE_RX.search(l):
                    out.append(Finding(rel, n, l, f"{RULE} json-cite: cite (step, blk, off) or a T-id, not a JSON line number"))
    return out
