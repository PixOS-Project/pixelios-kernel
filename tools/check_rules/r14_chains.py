"""R14 (CR-14): build.sh checks are `chk NAME CMD`, not `cmd && ./out/x` chains (set -e ignores a failing left side of &&);
host tests are selected with `>=`, not `#if WLAN_LINK_N ==`. Spec: docs/research/coding-rules-draft.md CR-14."""
import re
from .lib import Finding, tracked, strip_quotes

RULE = "R14"
# name -> (regex on the line with quoted spans blanked, message)
SH_PATTERNS = {
    "and-out": (re.compile(r"&&\s*\./out/"), "`&& ./out/` chain: use chk NAME CMD"),
    "and-O": (re.compile(r"&&\s*\$O/"), "`&& $O/` chain: use chk NAME CMD"),
    "and-braceO": (re.compile(r"&&\s*\$\{O\}/"), "`&& ${O}/` chain: use chk NAME CMD"),
    "and-echo-pass": (re.compile(r"&&\s*echo\s+\"?PASS"), "`&& echo \"PASS`: the PASS line must come from chk"),
}
RAW = {"and-echo-pass"}  # matched on the unblanked line (the PASS word is inside the quotes)
PIPE_AND = re.compile(r"(?<![|&])\|(?![|&]).*&&")
C_PATTERNS = {
    "if-link-n-eq": (re.compile(r"^\s*#\s*if\s+WLAN_LINK_N\s*=="), "`#if WLAN_LINK_N ==`: use >= so a later N keeps the earlier tests"),
}


def check(root, sh_patterns=None, c_patterns=None, pipe=True):
    sh_patterns = SH_PATTERNS if sh_patterns is None else sh_patterns
    c_patterns = C_PATTERNS if c_patterns is None else c_patterns
    out = []
    for rel, lines in tracked(root, "shim/build.sh"):
        pipefail = any(re.match(r"\s*set\s+-\w*o?\s*(-o\s+)?\w*pipefail|\s*set\s+.*pipefail", l) for l in lines)
        for n, l in enumerate(lines, 1):
            if l.lstrip().startswith("#"):
                continue
            q = strip_quotes(l)
            for name, (rx, msg) in sh_patterns.items():
                if rx.search(l if name in RAW else q):
                    out.append(Finding(rel, n, l, f"{RULE} {name}: {msg}"))
            if pipe and not pipefail and PIPE_AND.search(q):
                out.append(Finding(rel, n, l, f"{RULE} pipe-and: a pipe into && without pipefail"))
    for rel, lines in tracked(root, "shim/*host_test*.c"):
        for n, l in enumerate(lines, 1):
            for name, (rx, msg) in c_patterns.items():
                if rx.search(l):
                    out.append(Finding(rel, n, l, f"{RULE} {name}: {msg}"))
    return out
