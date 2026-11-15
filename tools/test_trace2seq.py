#!/usr/bin/env python3
"""Host test of tools/trace2seq.py (drafts only). Run: python3 tools/test_trace2seq.py </dev/null
Fixture rows are real lines of the trace-diff golden-regs CSV of golden 20261028T034021Z trace-wifi4.txt (seq 1, 7, 274899, 274900, 274904)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "trace2seq.py")
HDR = "seq,ts,src,mark,caller,op,width,space,win,addr,val"
GOOD = [
    "1,78.220048,trace-wifi4.txt,wifi-on4,,CR,32,cfg,,0x100,",
    "7,78.220068,trace-wifi4.txt,wifi-on4,,CW,16,cfg,,0x78,0x2110",
    "274899,80.056058,trace-wifi4.txt,wifi-on5,dhdpcie_bus_ringbell_fast,W,32,va,,0xffffffc01fc32140,0xff000001",
    "274900,80.056065,trace-wifi4.txt,wifi-on5,dhdpcie_bus_ringbell_fast,W,32,va,,0xffffffc01fc32140,0xdd020001",
    "274904,80.072388,trace-wifi4.txt,wifi-on5,dhdpcie_bus_ringbell_fast,W,32,bar0,,0x2140,0xff000002",
]


def run(lines, *extra, out=None):
    d = tempfile.mkdtemp(prefix="t2s-")
    src = os.path.join(d, "in.csv")
    with open(src, "w") as f:
        f.write("\n".join(lines) + "\n")
    out = out or os.path.join(d, "out.json")
    r = subprocess.run([sys.executable, "-I", TOOL, src, "-o", out] + list(extra), stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r, out


class DraftTests(unittest.TestCase):
    def test_every_row_is_a_draft(self):
        r, out = run(["# block=wifi", HDR] + GOOD)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS draft rows=5 steps=2", r.stdout)
        d = json.load(open(out))
        self.assertEqual(d["status"], "draft")
        self.assertEqual(len(d["rows"]), 5)
        for row in d["rows"]:
            self.assertEqual((row["status"], row["tag"], row["restore"]), ("draft", "guess", "TODO"))
            self.assertIn("seq", row["cite"])

    def test_kinds_blocks_and_steps(self):
        r, out = run([HDR] + GOOD)
        d = json.load(open(out))
        self.assertEqual([x["kind"] for x in d["rows"]], ["read_only", "mmio_write", "mmio_write", "mmio_write", "mmio_write"])
        self.assertEqual([x["blk"] for x in d["rows"]], ["CFG", "CFG", "VA", "VA", "BAR0"])
        self.assertEqual([x["step"] for x in d["rows"]], [0, 0, 1, 1, 1])      # a new step where the mark column changes
        self.assertEqual(d["rows"][1]["width"], 16)

    def test_range_and_writes_only(self):
        r, out = run([HDR] + GOOD, "--from-seq", "274899", "--writes-only")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(json.load(open(out))["rows"]), 3)


class RefusalTests(unittest.TestCase):
    def refuse(self, lines, want, *extra):
        r, out = run(lines, *extra)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("RESULT: ERROR", r.stdout)
        self.assertIn(want, r.stdout)
        self.assertFalse(os.path.exists(out), "no output file may exist after a refusal")

    def test_wrong_header_names_line(self):
        self.refuse(["seq,time"] + GOOD, ":1:")

    def test_old_free_text_format_refused(self):
        self.refuse(["W 0x17400004 0x1", "R 0x17400000 0x100"], ":1:")

    def test_unknown_op_names_line(self):
        self.refuse([HDR, GOOD[0], GOOD[1].replace(",CW,", ",XX,")], ":3:")

    def test_unknown_space_names_line(self):
        self.refuse([HDR, GOOD[0], GOOD[1].replace(",cfg,", ",io,")], ":3:")

    def test_short_row_names_line(self):
        self.refuse([HDR, GOOD[0], "7,78.2,trace,wifi-on4,,CW,16,cfg"], ":3:")

    def test_bad_hex_names_line(self):
        self.refuse([HDR, GOOD[0], GOOD[1].replace("0x78", "0xzz")], ":3:")

    def test_write_without_value_refused(self):
        self.refuse([HDR, "7,78.220068,trace-wifi4.txt,wifi-on4,,CW,16,cfg,,0x78,"], ":2:")

    def test_header_only_and_empty_refused(self):
        self.refuse([HDR], "zero rows")
        self.refuse([""], "no header")

    def test_empty_selection_refused(self):
        self.refuse([HDR] + GOOD, "no row", "--from-seq", "999999999")

    def test_missing_input_refused(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "/nonexistent/in.csv", "-o", "/tmp/t2s-never.json"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("RESULT: ERROR", r.stdout)


class NeverWritesIntoSocOrXnuTests(unittest.TestCase):
    def forbidden(self, rel):
        target = os.path.join(ROOT, rel)
        existed = os.path.exists(target)
        r, out = run([HDR] + GOOD, out=target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("inside soc/ or xnu/", r.stdout)
        self.assertEqual(os.path.exists(target), existed, "the forbidden path was touched")

    def test_soc_refused(self):
        self.forbidden("soc/google/gs101/draft_trace2seq_test.json")

    def test_xnu_refused(self):
        self.forbidden("xnu/patches/9999-draft-trace2seq-test.json")

    def test_dotdot_refused(self):
        self.forbidden("tools/../soc/draft_trace2seq_test.json")

    def test_symlink_into_soc_refused(self):
        d = tempfile.mkdtemp(prefix="t2s-ln-")
        ln = os.path.join(d, "soc-link")
        os.symlink(os.path.join(ROOT, "soc"), ln)
        target = os.path.join(ln, "draft_trace2seq_test.json")
        r, out = run([HDR] + GOOD, out=target)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "soc", "draft_trace2seq_test.json")))

    def test_other_dir_allowed(self):
        r, out = run([HDR] + GOOD)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    print("RESULT: %s tests=%d failures=%d errors=%d" % ("PASS" if res.wasSuccessful() else "FAIL", res.testsRun, len(res.failures), len(res.errors)))
    sys.exit(0 if res.wasSuccessful() else 1)
