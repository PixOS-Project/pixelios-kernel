#!/usr/bin/env python3
"""Host tests for xnu/patches/0339-sweet-chg-mon-after-refuse.patch (ALL-ON T-58: a refused P3 keeps the 30 s SWEET CHG MON rows, so the boot has a temp= row for the host safety monitor; READS ONLY).

    python3 tools/test_chg_mon_refused.py  -> `RESULT: PASS chg-mon-refused cases=N` / `RESULT: FAIL ...`

Proves: 0339 applies on the stack 0284+0287+0298+0307+0314+0336; the combined source builds -Werror in the chg_icl host model (tools/chg_icl_host_test.c under
-DCHG_MON -DCHG_PMIC -DCHG_MON_REFUSED); every 0287/0298/0314 case still passes with 0339 applied (a normal P3 boot is unchanged); the MON-after-refusal cases: P3 refused because CNFG_09 ILIM is
already the 875 mA code -> the 0339 start line, then rows at t=0, 30, 60.. s with the 0298/0307 conversions, the first at the next tick; zero chip writes; a lost phase-1 gate = exactly one GAP and no more rows;
a failed fuel-gauge read = the 0298 MON GAP once and no more rows; a stopped bus = one GAP; a refusal with a dead bus does not enter stage 10; the patch body adds no write call (comments excluded); mutants fail.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_chg_icl as ICL  # noqa: E402
from test_chg_mon_refused_mutants import CHG_MON_REFUSED_MUTANTS  # noqa: E402

PATCH = os.path.join(ROOT, "xnu", "patches", "0339-sweet-chg-mon-after-refuse.patch")
STACK = [os.path.join(ROOT, "xnu", "patches", n) for n in ("0298-sweet-chg-mon.patch", "0307-sweet-chg-soc.patch", "0314-sweet-pmic-diag.patch", "0336-sweet-thermal.patch", "0339-sweet-chg-mon-after-refuse.patch")]
FLAGS = ("-DCHG_MON", "-DCHG_PMIC", "-DCHG_MON_REFUSED")
CASES = []
run = ICL.run



def code_of(text):
    """Added patch lines without comments and string literals."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


class MonRefused(ICL.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.applied = [run("git", "-C", os.path.join(cls.tmp, "tree"), "apply", "--include=bsd/dev/sweet_chg_i2c.c", p) for p in STACK]

    def cases(self, src=None, out="monref"):
        r, exe = self.flow(src=src, out=out, extra=FLAGS)
        if r.returncode != 0:
            return None, r.stderr
        o = run(exe)
        return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M)), o.stdout

    def test_apply_and_flow(self):
        for r in self.applied:
            self.assertEqual(r.returncode, 0, r.stderr)
        res, out = self.cases()
        self.assertIsNotNone(res, out)
        mr = {k for k in res if k.startswith("mr_")}
        self.assertEqual(len(mr), 12, out[-2000:])
        self.assertGreaterEqual(len(res), 110, out[-2000:])      # the 0287 + 0298 + 0314 cases still run and pass with 0339 applied
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, out[-3000:])
        self.assertIn("SUMMARY fails=0", out)
        CASES.extend("flow:" + k for k in res)

    def test_patch_adds_no_write(self):
        with open(PATCH) as f:
            added = "\n".join(l[1:] for l in f.read().split("\n") if l.startswith("+") and not l.startswith("+++"))
        code = code_of(added)
        self.assertNotRegex(code, r"i2c_cnfg09_write\s*\(|i2c_msg\s*\(|i2c_hw_wr32\s*\(|chg_i2c_write|p3_write_verify\s*\(")
        self.assertEqual(re.findall(r"i2c_wr\((\w+),", code), ["I_INTEN"])      # only the controller interrupt-enable disarm that mon_row also does, never TX data
        self.assertEqual(len(re.findall(r"\bmon_row\(", code)), 2)             # the forward declaration and the one call
        CASES.append("patch_adds_no_write")

    def test_mutants_fail(self):
        with open(self.src) as f:
            src = f.read()
        for name, a, b in CHG_MON_REFUSED_MUTANTS:
            self.assertEqual(src.count(a), 1, "mutant %s needle" % name)
            p = os.path.join(self.tmp, "mut-%s.c" % name)
            with open(p, "w") as f:
                f.write(src.replace(a, b))
            res, out = self.cases(src=p, out="mut-" + name)
            self.assertIsNotNone(res, "mutant %s does not build (ERROR, not a kill): %s" % (name, out[-800:]))
            self.assertTrue(any(v != "ok" for k, v in res.items() if k.startswith("mr_") or k == "runaway"), "mutant %s survived" % name)
            CASES.append("mutant:" + name)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("RESULT: %s chg-mon-refused cases=%d" % ("PASS" if ok else "FAIL", len(CASES)))
    sys.exit(0 if ok else 1)
