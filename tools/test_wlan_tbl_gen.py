#!/usr/bin/env python3
"""tools/wlan-tbl-gen on the proven-run slice (tools/testdata/wlan-tbl/proven-g2-slice.log, stores of run 20261102T184140Z-wlan-runG2-scan24r2):
the clean log must generate and check, and one mutant per rule must fail with its WIFI GAP rule name (rule 8)."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
warnings.simplefilter("ignore")

TOOLS = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(TOOLS, "wlan-tbl-gen")
SLICE = os.path.join(TOOLS, "testdata", "wlan-tbl", "proven-g2-slice.log")
LINES = open(SLICE).read().splitlines()


def run(*a):
    r = subprocess.run([sys.executable, GEN] + list(a), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def log(self, lines, name="x.log"):
        p = os.path.join(self.d, name)
        open(p, "w").write("\n".join(lines) + "\n")
        return p

    def gen(self, lines):
        return run("gen", "--run", self.log(lines), "--tsv", os.path.join(self.d, "t.tsv"))

    def tsv(self):
        return open(os.path.join(self.d, "t.tsv")).read().splitlines()

    def check(self, tsv_lines, lines=None):
        t = os.path.join(self.d, "m.tsv")
        open(t, "w").write("\n".join(tsv_lines) + "\n")
        return run("check", "--run", self.log(lines or LINES), "--tsv", t)

    def test_self_test(self):
        rc, out = run("--self-test")
        self.assertEqual(rc, 0, out)

    def test_clean(self):
        rc, out = self.gen(LINES)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"RESULT: PASS wlan-tbl-gen gen rows=\d+ table_sha256=[0-9a-f]{64} gaps=0")
        rc, out = run("check", "--run", self.log(LINES), "--tsv", os.path.join(self.d, "t.tsv"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(run("sha", "--tsv", os.path.join(self.d, "t.tsv"))[0], 0)

    def test_unparsed_line(self):
        bad = [re.sub(r" bp (0x[0-9a-f]+) old", r" xx \1 old", l) if "ring0 base bp" in l else l for l in LINES]
        rc, out = self.gen(bad)
        self.assertEqual(rc, 2)
        self.assertIn("WIFI GAP: UNPARSED", out)

    def test_row_outside_envelope(self):
        bad = [l.replace("G2 store hostready", "G2 store pcie_secret_reg") for l in LINES]
        rc, out = self.gen(bad)
        self.assertEqual(rc, 2)
        self.assertIn("WIFI GAP: OUTSIDE", out)

    def test_missing_restore(self):
        for drop, what in (("restore space 1 win 0 off 0x84", "window"), ("G2 undo ok", "undo")):
            rc, out = self.gen([l for l in LINES if drop not in l])
            self.assertEqual(rc, 2, what)
            self.assertIn("WIFI GAP: MISSING-RESTORE", out)

    def test_missing_doorbell(self):
        i = next(n for n, l in enumerate(LINES) if "d4d d2h_r slot" in l)
        rc, out = self.gen(LINES[: i + 1] + LINES[i + 2:])
        self.assertEqual(rc, 2)
        self.assertIn("WIFI GAP: MBX-PAIR", out)

    def test_constant_epoch(self):
        words = {}
        bad, run_ = [], 0
        for l in LINES:
            if "G2 store d4d item word" in l:
                if run_ % 10 == 0:
                    l = re.sub(r":= 0x[0-9a-f]+ read back 0x[0-9a-f]+", ":= 0x1000009 read back 0x1000009", l)
                run_ += 1
            else:
                run_ = 0
            bad.append(l)
        rc, out = self.gen(bad)
        self.assertEqual(rc, 2, out)
        self.assertIn("WIFI GAP: EPOCH", out)

    def test_poll_without_timeout(self):
        self.gen(LINES)
        t = self.tsv()
        cols = t[1].split()[2:]
        fields = ["P", "host", "wait", "0x0", "0x0", "0x1", "0xffffffff", "g2", "x", "obs", "-", "0", "0x0", "-", "0" * 64]
        rc, out = self.check(t[:4] + ["\t".join(["999"] + fields)] + t[4:])
        self.assertEqual(rc, 1)
        self.assertIn("TIMEOUT", out)
        self.assertEqual(len(cols), 16)

    def test_changed_dropped_reordered(self):
        self.gen(LINES)
        t = self.tsv()
        body = [i for i, l in enumerate(t) if not l.startswith("#")]
        a, b = body[10], body[11]
        ch = list(t)
        f = ch[a].split("\t")
        f[6] = "0xdeadbeef"
        ch[a] = "\t".join(f)
        rc, out = self.check(ch)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"CHANGED|SHA")
        dr = list(t)
        del dr[a]
        rc, out = self.check(dr)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"DROPPED")
        ro = list(t)
        ro[a], ro[b] = ro[b], ro[a]
        rc, out = self.check(ro)
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"REORDER|SHA")

    def test_replay_mutants(self):
        """T1: a store log equal to the baseline PASSES under tools/replay, a changed / dropped / reordered store FAILS (plan step 5)."""
        rep = os.path.join(TOOLS, "replay")

        def rp(lines):
            r = subprocess.run([sys.executable, rep, "wifi", "--run", self.log(lines, "r.log"), "--baseline", SLICE], capture_output=True, text=True)
            return r.returncode, r.stdout
        rc, out = rp(LINES)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"RESULT: PASS score=\d+ first_divergence=none")
        i = next(n for n, l in enumerate(LINES) if "d4d item word" in l)
        chg = list(LINES)
        chg[i] = re.sub(r":= (0x[0-9a-f]+|0) read back", ":= 0x7777 read back", chg[i])
        drp = LINES[:i] + LINES[i + 1:]
        swp = list(LINES)
        swp[i], swp[i + 1] = swp[i + 1], swp[i]
        for name, m in (("changed", chg), ("dropped", drp), ("reordered", swp)):
            rc, out = rp(m)
            self.assertEqual(rc, 1, name + out)
            self.assertIn("RESULT: FAIL", out)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0)
    ok = r.result.wasSuccessful()
    print("RESULT: %s test_wlan_tbl_gen tests=%d mutants=7 replay_mutants=3" % ("PASS" if ok else "FAIL", r.result.testsRun))
    sys.exit(0 if ok else 1)
