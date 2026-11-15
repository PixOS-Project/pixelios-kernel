#!/usr/bin/env python3
"""Patch 0367 (lowclk, L-28, LOG ONLY CPU DVFS witness): text test of xnu/patches/0367-sweet-cpu-dvfs-witness.patch. Checks the ten token names, the dom 2/3/4 rows (before, after),
the asked/rc row, every GAP row, that nothing prints without a token (the block is guarded by dn != 0), that the 0214 MIF block and the 0363 call are untouched, and that the added
lines contain no register write. A mutant per check (text edited in memory, the check must fail). The shim side is shim/dvfs_probe_host_test.c.
`RESULT: PASS 0367-cpudvfs cases=N mutants=M`. Not a hardware or kernel-build test."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0367-sweet-cpu-dvfs-witness.patch")

TEXT = [
    ("token names asked", r'nk\[3\] = \{ "sweetdvfsk0", "sweetdvfsk1", "sweetdvfsk2" \}'),
    ("token names before", r'nb\[3\] = \{ "sweetdvfsb0", "sweetdvfsb1", "sweetdvfsb2" \}'),
    ("token names after", r'na\[3\] = \{ "sweetdvfsa0", "sweetdvfsa1", "sweetdvfsa2" \}'),
    ("token name rc", r'PE_parse_boot_argn\("sweetdvfsrc", &drc'),
    ("every token counted", r"PE_parse_boot_argn\(nk\[k\][^\n]*\n\s*dn\+\+;.*?PE_parse_boot_argn\(nb\[k\][^\n]*\n\s*dn\+\+;.*?PE_parse_boot_argn\(na\[k\][^\n]*\n\s*dn\+\+;"),
    ("nothing printed without a token", r"if \(dn != 0\) \{\s*for \(unsigned int k = 0; k < 3; k\+\+\) \{\s*IOLog\(\"SWEET DVFS: dom %u get khz=%u when=before"),
    ("before row dom 2+k", r'IOLog\("SWEET DVFS: dom %u get khz=%u when=before \(CPUCL%u, read by the shim\)\\n", 2 \+ k, db\[k\], k\)'),
    ("after row dom 2+k", r'IOLog\("SWEET DVFS: dom %u get khz=%u when=after \(CPUCL%u, after the request\)\\n", 2 \+ k, da\[k\], k\)'),
    ("asked row", r'IOLog\("SWEET DVFS: cpu-dvfs asked=%u,%u,%u rc=%u\\n", dk\[0\], dk\[1\], dk\[2\], drc\)'),
    ("gap incomplete", r"if \(dn != 10\) \{\s*IOLog\(\"SWEET GAP: DVFS cpu-dvfs tokens incomplete"),
    ("gap unknown rc", r"else if \(drc > 4\) \{\s*IOLog\(\"SWEET GAP: DVFS cpu-dvfs rc=%u is not a known result"),
    ("gap rc 1..4", r"else if \(drc != 0\) \{\s*IOLog\(\"SWEET GAP: DVFS cpu-dvfs rc=%u \(1 request refused or failed, 2 read-back differs, 3 read-back failed, 4 request step not reached\)"),
    ("gap rc 0 mismatch", r"if \(dk\[k\] != 0 && da\[k\] != dk\[k\]\) \{\s*IOLog\(\"SWEET GAP: DVFS cpu-dvfs rc=0 but CPUCL%u"),
    ("block sits before the 0363 call", r"\n\+\t\}\n \tsweet_probe_tokens\(\);"),
    ("0214 block is context, not edited", r"\n \t\t\tIOLog\(\"SWEET DVFS: no sweetmif\* boot-arg"),
]
NEG = [
    ("no removed lines", r"(?m)^-[^-\n]"),
    ("no register write or firmware call", r"(?m)^\+[^\n]*\b(wr|rr|w32|r32|acpm_[a-z_]+|writel|OSWriteLittle\w*)\("),
    ("no poll or sleep", r"(?m)^\+[^\n]*\b(IOSleep|IODelay|delay_ms|while)\b"),
]


def body(t):
    i = t.find("--- a/")
    if i < 0:
        raise SystemExit("RESULT: FAIL 0367-cpudvfs: no diff in the patch")
    return t[i:]


def rxf(rx):
    return rx.replace(r"\s*", r"[\s+]*")	# diff lines start with '+'


def check(src, added):
    for name, rx in TEXT:
        if not re.search(rxf(rx), src, re.S):
            return "patch text check failed: %s" % name
    for name, rx in NEG:
        if re.search(rx, src):
            return "forbidden text: %s" % name
    return None


def main():
    text = open(PATCH).read()
    src = body(text)
    added = "\n".join(l[1:] for l in src.splitlines() if l.startswith("+") and not l.startswith("+++"))
    if not re.search(r"(?m)^sweet-cpu-dvfs-witness ", text):
        print("RESULT: FAIL 0367-cpudvfs: no description line")
        return 1
    e = check(src, added)
    if e:
        print("RESULT: FAIL 0367-cpudvfs: " + e)
        return 1
    cases = len(TEXT) + len(NEG)
    dead = 0
    for name, rx in TEXT:
        m = re.sub(rxf(rx), "", src, count=1, flags=re.S)
        if m == src:
            print("RESULT: FAIL 0367-cpudvfs: text mutant %s did not change the patch" % name)
            return 1
        if check(m, "\n".join(l[1:] for l in m.splitlines() if l.startswith("+") and not l.startswith("+++"))) is None:
            print("RESULT: FAIL 0367-cpudvfs: text mutant %s survived" % name)
            return 1
        dead += 1
    muts = [
        ("dom index 3+k", src.replace("2 + k, db[k]", "3 + k, db[k]")),
        ("guard removed", src.replace("if (dn != 0) {", "if (1) {")),
        ("write added", src + "\n+\twr(0, 1);\n"),
        ("wrong token name", src.replace('"sweetdvfsrc"', '"sweetdvfsrx"')),
        ("a removed line", src + "\n-\tfoo();\n"),
    ]
    for name, m in muts:
        if m == src:
            print("RESULT: FAIL 0367-cpudvfs: mutant %s did not change the patch" % name)
            return 1
        if check(m, "\n".join(l[1:] for l in m.splitlines() if l.startswith("+") and not l.startswith("+++"))) is None:
            print("RESULT: FAIL 0367-cpudvfs: mutant %s survived" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0367-cpudvfs cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
