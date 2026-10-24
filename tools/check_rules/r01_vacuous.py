"""R01 (CR-01): an assertion must be able to fail. Rejects `|| 1)` / `|| true)` inside EXPECT/ASSERT/CHECK macros of shim/*_host_test.c,
and bare `assert` (stripped by python -O) in tools/gen_wlan_seq.py, tools/boards.py and the check_* functions of tools/test-bluejay-boot.py.
Spec: docs/CODING-RULES.md CR-01; FAIL fixture 4a456b8:shim/wlanlink_host_test.c:738."""
import ast, re
from .lib import Finding, tracked

RULE = "R01"
C_PATTERNS = {
    "or-true": (re.compile(r"\b\w*(?:EXPECT|ASSERT|CHECK)\w*\s*\(.*\|\|\s*(?:1|true|TRUE)\s*\)"), "`|| 1)` / `|| true)` makes the assertion always true"),
}
PY_FILES = {"tools/gen_wlan_seq.py": None, "tools/boards.py": None, "tools/test-bluejay-boot.py": "check_"}  # file -> function-name prefix (None = anywhere)


def bare_asserts(src, prefix):
    """Line numbers of `assert` statements (anywhere if prefix is None, else inside a function whose name starts with prefix)."""
    tree = ast.parse(src)
    hits = []

    def walk(node, inside):
        for ch in ast.iter_child_nodes(node):
            ins = inside or (isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and ch.name.startswith(prefix or ""))
            if isinstance(ch, ast.Assert) and (prefix is None or inside):
                hits.append(ch.lineno)
            walk(ch, ins)
    walk(tree, False)
    return sorted(hits)


def check(root, c_patterns=None, py=True):
    c_patterns = C_PATTERNS if c_patterns is None else c_patterns
    out = []
    for rel, lines in tracked(root, "shim/*_host_test.c"):
        for n, l in enumerate(lines, 1):
            for name, (rx, msg) in c_patterns.items():
                if rx.search(l):
                    out.append(Finding(rel, n, l, f"{RULE} {name}: {msg}"))
    if py:
        for rel, lines in tracked(root, *PY_FILES):
            for n in bare_asserts("\n".join(lines) + "\n", PY_FILES[rel]):
                out.append(Finding(rel, n, lines[n - 1], f"{RULE} bare-assert: use `if not cond: raise ...` (assert is stripped by python -O)"))
    return out
