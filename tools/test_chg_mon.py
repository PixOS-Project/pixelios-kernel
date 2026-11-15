#!/usr/bin/env python3
"""Host tests for xnu/patches/0298-sweet-chg-mon.patch (one battery row per 30 s watcher tick for the whole boot; the resident session reads it).

    python3 tools/test_chg_mon.py  -> `RESULT: PASS chg-mon cases=N` / `RESULT: FAIL ...`

Proves: 0298 applies on 0284+0287; the combined source builds -Werror in the chg_icl host model; every 0287 case still passes; the MON cases
(tools/chg_icl_host_test.c under -DCHG_MON): rows every 30 s after the 10 samples, values with the 0287 conversions, no chip write beyond 0287's
one CNFG_09 write, one GAP and no more rows on a failed fuel-gauge read (the bus stops, as for every failed read since 0284); the patch body adds no chg_i2c write call (reads only); mutants fail.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_chg_icl as ICL  # noqa: E402

PATCH = os.path.join(ROOT, "xnu", "patches", "0298-sweet-chg-mon.patch")
CASES = []
run = ICL.run


class Mon(ICL.Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.apply0298 = run("git", "-C", os.path.join(cls.tmp, "tree"), "apply", PATCH)

    def cases(self, src=None, out="mon"):
        r, exe = self.flow(src=src, out=out, extra=("-DCHG_MON",))
        if r.returncode != 0:
            return None, r.stderr
        o = run(exe)
        return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M)), o.stdout

    def test_apply_and_flow(self):
        self.assertEqual(self.apply.returncode, 0, self.apply.stderr)
        self.assertEqual(self.apply0298.returncode, 0, self.apply0298.stderr)
        res, out = self.cases()
        self.assertIsNotNone(res, out)
        mon = {k for k in res if k.startswith("mon_")}
        self.assertEqual(len(mon), 5, out[-2000:])
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, out[-3000:])
        CASES.extend("flow:" + k for k in res)

    def test_reads_only(self):
        with open(PATCH) as f:
            added = "\n".join(l[1:] for l in f.read().split("\n") if l.startswith("+") and not l.startswith("+++"))
        self.assertNotRegex(added, r"i2c_cnfg09_write|chg_i2c_write|i2c_msg\s*\(")
        self.assertEqual(len(re.findall(r"chg_i2c_read\(0x36, 0x0[a9], 2", added)), 2)
        CASES.append("reads_only")

    def test_mutants_fail(self):
        with open(self.src) as f:
            src = f.read()
        muts = {
            "no_row_call": ("\t\t\tmon_row((uint32_t)((p3_next_ms - p3_t0_ms) / 1000u));\n", "\t\t\tif (0) mon_row(0);\n"),
            "wrong_scale": ("(int)((cs * 3125) / 10), (uint32_t)(((uint64_t)v * 78125u) / 1000000u));\n}\n\nstatic void\ni2c_p3_tick",
                            "(int)((cs * 3125) / 100), (uint32_t)(((uint64_t)v * 78125u) / 1000000u));\n}\n\nstatic void\ni2c_p3_tick"),
            "no_gap_line": ("i2c_log(\"SWEET CHG GAP: MON fuel-gauge read failed", "if (0) i2c_log(\"SWEET CHG GAP: MON fuel-gauge read failed"),
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
    print("RESULT: %s chg-mon cases=%d" % ("PASS" if ok else "FAIL", len(CASES)))
    sys.exit(0 if ok else 1)
