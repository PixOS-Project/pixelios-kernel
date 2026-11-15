#!/usr/bin/env python3
"""Host test of tools/replay (T1 Wi-Fi replay oracle). Run: python3 tools/test_replay.py </dev/null
Fixtures: tools/testdata/replay/ (f1-run.log = lines cut from the F1 run 20261031T115557Z-wlan-runG2-d4d17; golden-cut.csv and tw4-cut.txt =
real rows cut from the stock golden captures). No artifact paths are needed."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "replay")
FIX = os.path.join(HERE, "testdata", "replay")
CSV = os.path.join(FIX, "golden-cut.csv")
TW4 = os.path.join(FIX, "tw4-cut.txt")
RUN = os.path.join(FIX, "f1-run.log")


def st(name, off, val):
    return "[1.0] SWEET WLAN: G2 store %s off %#x := %#x (old and read-back not made, golden writes only)\n" % (name, off, val)


def cold(base=1, swap=False):
    """The stock cold DS-ack window as a G2 log: hostready, ISR mimic, ff/dd doorbells (indices shifted by base-1), mask restore."""
    i = base - 1
    rows = [("hostready", 0x144, 0x12345678), ("isr", 0xc30, 1), ("isr", 0xc34, 0),
            ("ack", 0x140, 0xff000000 + i + 1), ("rd1", 0x140, 0xdd020000 + i + 1), ("mask", 0xc34, 0xffff),
            ("wake", 0x140, 0xff000000 + i + 2)]
    if swap:
        rows[3], rows[4] = rows[4], rows[3]
    return "".join(st(*r) for r in rows)


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.td.cleanup()

    def log(self, text, name="run.log"):
        p = os.path.join(self.td.name, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def rp(self, *args):
        r = subprocess.run([sys.executable, TOOL, "wifi"] + list(args), stdin=subprocess.DEVNULL, capture_output=True, text=True)
        res = [l for l in r.stdout.splitlines() if l.startswith("RESULT:")]
        self.assertEqual(len(res), 1, r.stdout + r.stderr)
        return r.returncode, res[0], r.stdout

    def test_exact_match_pass_with_shifted_ring_index(self):
        for base in (1, 7):   # ring index offset (a mid-session run starts at ff000007) is normalised away
            rc, res, _ = self.rp("--golden", CSV, "--golden-doorbells", TW4, "--run", self.log(cold(base)))
            self.assertEqual(rc, 0, res)
            self.assertIn("PASS score=7 first_divergence=none", res)

    def test_swapped_doorbell_fails_at_the_right_row(self):
        rc, res, out = self.rp("--golden", CSV, "--golden-doorbells", TW4, "--run", self.log(cold(swap=True)))
        self.assertEqual(rc, 1, res)
        self.assertIn("score=3 first_divergence=bar0:TW4:5/line4", res)
        self.assertIn("0xff000001", out)
        self.assertIn("0xdd020001", out)

    def test_unparsed_store_line_is_counted_loudly(self):
        bad = cold() + "[1.0] SWEET WLAN: G2 store wat off 0x140 = banana\n"
        rc, res, out = self.rp("--golden", CSV, "--golden-doorbells", TW4, "--run", self.log(bad))
        self.assertEqual(rc, 2, res)
        self.assertIn("REPLAY GAP: UNPARSED store line 8", out)
        self.assertIn("gaps=1", res)

    def test_ring_payload_and_tcm_stores_are_gaps_not_compared(self):
        extra = "[1.0] SWEET WLAN: G2 store d4b item w0 host 0 old 0 := 0x180000d read back 0x180000d\n" \
                "[1.0] SWEET WLAN: G2 store ring0 base bp 0x3ec528 old 0 := 0x84d9c000 read back 0x84d9c000\n"
        rc, res, out = self.rp("--golden", CSV, "--golden-doorbells", TW4, "--run", self.log(cold() + extra))
        self.assertEqual(rc, 0, res)
        self.assertIn("REPLAY GAP: 1 stores of class host-ring-payload", out)
        self.assertIn("REPLAY GAP: 1 stores of class tcm-bp", out)
        self.assertIn("gaps=2", res)

    def test_real_f1_fixture_diverges_from_golden(self):
        rc, res, out = self.rp("--golden", CSV, "--run", RUN)
        self.assertEqual(rc, 1, res)
        self.assertIn("first_divergence=cfg:", res)
        self.assertIn("LANE cfg: FIRST DIVERGENCE after matched=", out)   # golden has 0x84 stores where F1 writes rc command 0x507
        self.assertIn("REPLAY GAP:", out)

    def test_baseline_same_pass_changed_value_fails(self):
        a = self.log(cold(), "a.log")
        rc, res, _ = self.rp("--run", a, "--baseline", a)
        self.assertEqual((rc, res.split()[1]), (0, "PASS"), res)
        b = self.log(cold().replace("0xdd020001", "0xdd020009"), "b.log")
        rc, res, out = self.rp("--run", b, "--baseline", a)
        self.assertEqual(rc, 1, res)
        self.assertIn("score=4 first_divergence=baseline:", res)
        rc, res, _ = self.rp("--run", b, "--baseline", a, "--mask", "0x140")
        self.assertEqual(rc, 0, res)

    def test_no_comparable_rows_is_partial_not_pass(self):
        rc, res, _ = self.rp("--golden", CSV, "--golden-doorbells", TW4, "--run", self.log("[1.0] boot\n"))
        self.assertEqual(rc, 2, res)

    def test_missing_input_is_error(self):
        rc, res, _ = self.rp("--golden", CSV, "--run", "/nonexistent/run")
        self.assertEqual(rc, 2, res)


if __name__ == "__main__":
    unittest.main()
