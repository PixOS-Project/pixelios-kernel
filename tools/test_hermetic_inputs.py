#!/usr/bin/env python3
"""Gate tests must not read host state from a fixed /tmp path (AP-152: test_run_preflight read a stale /tmp/runF3.sh and failed the gate).
Scans tools/test_*.py and tools/test_*.sh for a fixed /tmp/<name> used as an input (open/exists/assignment in Python, cat/source/</test in
shell). mktemp/tempfile paths are fine: they never appear as a literal /tmp/<name>. A grep is a start, not proof (review the hit)."""
import glob, os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T = "/" + "tmp/"   # built, so this file never matches its own patterns
PY = [re.compile(r"\b(open|exists|isfile|isdir|listdir|Path|read_text|read_bytes)\(\s*[rbf]?[\"']" + T + r"[^\"']"),
      re.compile(r"^\s*[\w.]+(\s*,\s*[\w.]+)*\s*=\s*[rbf]?[\"']" + T + r"[^\"']")]
SH = [re.compile(r"(^|[\s;&|(])(cat|source|\.|<|-[efsrd])\s+[\"']?" + T + r"[A-Za-z0-9_]")]


def hits(name, text):
    pats = PY if name.endswith(".py") else SH
    return ["%s:%d: %s" % (name, i, l.strip()[:120]) for i, l in enumerate(text.splitlines(), 1) if any(p.search(l) for p in pats)]


class Hermetic(unittest.TestCase):
    def test_no_fixed_tmp_inputs(self):
        bad = []
        for f in sorted(glob.glob(os.path.join(HERE, "test_*.py")) + glob.glob(os.path.join(HERE, "test_*.sh"))):
            with open(f, errors="replace") as fh:
                bad += hits(os.path.basename(f), fh.read())
        self.assertEqual(bad, [], "tests read a fixed /tmp path (AP-152): build the input in a tempfile/mktemp dir instead")

    def test_patterns_catch_the_ap152_cases(self):   # the scanner itself: the real AP-152 line and its shell twin are flagged, data strings are not
        self.assertTrue(hits("t.py", '        sh, plans = "%srunF3.sh", os.path.expanduser("~/x")' % T))
        self.assertTrue(hits("t.py", 'txt = open("%srunF3.sh").read()' % T))
        self.assertTrue(hits("t.sh", 'argv=$(cat %srunF3.sh)' % T))
        self.assertTrue(hits("t.sh", '[ -s %srunF3.sh ] && x' % T))
        self.assertFalse(hits("t.py", 'cmd = "cp %sx xnu/patches/0150-foo.patch"' % T))   # a command string under test (test_patch_guard.py)
        self.assertFalse(hits("t.sh", 'grep -q "adb push x /data/local%sgp"' % T))
        self.assertFalse(hits("t.sh", 'd=$(mktemp -d); cat "$d/x"'))


if __name__ == "__main__":
    unittest.main()
