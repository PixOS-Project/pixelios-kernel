#!/usr/bin/env python3
"""Host tests for xnu/patches/0314-sweet-pmic-diag.patch (ALL-ON T-02: PMIC latch line + fast battery rows, READ ONLY). No phone, no kernel build.

    python3 tools/test_chg_pmic.py  -> `RESULT: PASS chg-pmic cases=N` / `RESULT: FAIL ...`

Proves: 0314 applies on 0284+0287+0298+0307; the combined source builds -Werror in the chg_icl host model (tools/chg_icl_host_test.c under -DCHG_PMIC); the latch line carries the
charger registers 0xb0/0xb1/0xb5..0xb7 and the two S2MPG GAP lines; the fast rows (default off, argument ranges, MINMAX every 10 s, one GAP and no retry on a failed read, bad VCell,
lost gate, phase 2 missing, coexistence with the phase-3 write) all behave; no chip write beyond 0287's single CNFG_09 write; the patch adds no write call; mutants fail; boot-args known.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_chg_icl as ICL  # noqa: E402

PATCH = os.path.join(ROOT, "xnu", "patches", "0314-sweet-pmic-diag.patch")
STACK = [os.path.join(ROOT, "xnu", "patches", n) for n in ("0298-sweet-chg-mon.patch", "0307-sweet-chg-soc.patch", "0314-sweet-pmic-diag.patch")]
CASES = []
run = ICL.run


class Pmic(ICL.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.applied = [run("git", "-C", os.path.join(cls.tmp, "tree"), "apply", p) for p in STACK]

    def cases(self, src=None, out="pmic"):
        r, exe = self.flow(src=src, out=out, extra=("-DCHG_PMIC",))
        if r.returncode != 0:
            return None, r.stderr
        o = run(exe)
        return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M)), o.stdout

    def test_apply_and_flow(self):
        for r in self.applied:
            self.assertEqual(r.returncode, 0, r.stderr)
        res, out = self.cases()
        self.assertIsNotNone(res, out)
        want = {k for k in res if k.startswith(("pmic_", "fast_"))}
        self.assertGreaterEqual(len(want), 20, out[-2000:])
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, out[-3000:])
        CASES.extend("flow:" + k for k in res)

    def test_reads_only(self):
        with open(PATCH) as f:
            added = "\n".join(l[1:] for l in f.read().split("\n") if l.startswith("+") and not l.startswith("+++"))
        self.assertNotRegex(added, r"i2c_cnfg09_write|chg_i2c_write|i2c_msg\s*\(|i2c_hw_wr32")
        self.assertEqual(sorted(re.findall(r"chg_i2c_read\((0x[0-9a-f]+), (0x[0-9a-f]+), (\d)", added)), [("0x36", "0x09", "2"), ("0x36", "0x0a", "2")])
        self.assertEqual(re.findall(r"i2c_wr\((\w+),", added), ["I_INTEN", "I_INTEN"])
        CASES.append("reads_only")

    def test_bootargs(self):
        known = open(os.path.join(ROOT, "tools", "bootargs-known.txt")).read()
        for a in ("sweetfg", "sweetfgn"):
            self.assertRegex(known, r"(?m)^%s\tpatch:0314$" % a)
            self.assertIn('PE_parse_boot_argn("%s"' % a, open(PATCH).read())
        CASES.append("bootargs")

    def test_mutants_fail(self):
        with open(self.src) as f:
            src = f.read()
        muts = {
            "no_fast_hook": ("\t\ti2c_fast_tick();                /* patch 0314: default off */\n", "\t\tif (0) i2c_fast_tick();\n"),
            "no_gate_check": ("\tif (!i2c_gate(0)) {\n\t\tfast_stop(", "\tif (0) {\n\t\tfast_stop("),
            "vmin_wrong": ("if (v < fast_vmin) {", "if (v > fast_vmin) {"),
            "latch_wrong_reg": ("C(0xb0), C(0xb1), C(0xb5), C(0xb6), C(0xb7));", "C(0xb0), C(0xb2), C(0xb5), C(0xb6), C(0xb7));"),
            "no_read_fail_gap": ("i2c_log(\"SWEET CHG GAP: FAST fuel-gauge read failed", "if (0) i2c_log(\"SWEET CHG GAP: FAST fuel-gauge read failed"),
            "no_bad_value_stop": ("\tif (v == 0 || v > FAST_VMAX_MV) {", "\tif (0) {"),
            "no_minmax_reset": ("\tfast_n = 0;\n\tfast_vmin = 0xffffffffu;\n\tfast_imax = 0;\n}", "}"),
            "no_gap_s2mpg11": ("i2c_log(\"SWEET PMIC GAP: s2mpg11", "if (0) i2c_log(\"SWEET PMIC GAP: s2mpg11"),
        }
        for name, (a, b) in muts.items():
            self.assertEqual(src.count(a), 1, "mutant %s needle" % name)
            p = os.path.join(self.tmp, "mut-%s.c" % name)
            with open(p, "w") as f:
                f.write(src.replace(a, b))
            res, out = self.cases(src=p, out="mut-" + name)
            self.assertIsNotNone(res, "mutant %s does not build (ERROR, not a kill): %s" % (name, out[-800:]))
            self.assertTrue(any(v != "ok" for v in res.values()), "mutant %s survived" % name)
            CASES.append("mutant:" + name)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("RESULT: %s chg-pmic cases=%d" % ("PASS" if ok else "FAIL", len(CASES)))
    sys.exit(0 if ok else 1)
