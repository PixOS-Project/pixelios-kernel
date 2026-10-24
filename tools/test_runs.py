#!/usr/bin/env python3
"""Tests for tools/runs: fixture run dirs (xrun PASS, xrun FAIL, probe) in a tempdir, RUNS_ROOT."""
import json, os, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


def xrun(root, name, result, t0, t1):
    d = os.path.join(root, "logs", "runs", name)
    os.makedirs(d)
    put(d + "/report.json", json.dumps({"result": result, "reason": "r", "run": {"label": name, "git_head": "abc1234", "waited": 1.0}}))
    put(d + "/run.json", json.dumps({"t_start": str(t0), "t_end": str(t1)}))
    put(d + "/report.txt", "RESULT: %s r\nRUN: label x\n" % result)


def probe(root, name, verdict, secs):
    d = os.path.join(root, "logs", "probe", name)
    os.makedirs(d)
    put(d + "/report.txt", "RESULT: %s something\ndir: x\n" % verdict)
    put(d + "/fboot.log", "f\n")
    put(d + "/usb.txt", "u\n")
    os.utime(d + "/fboot.log", (1000, 1000))
    os.utime(d + "/usb.txt", (1000 + secs, 1000 + secs))


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = self.t.name
        xrun(self.r, "20261022T100000Z-w3a-n3", "PASS", 100, 400)
        xrun(self.r, "20261023T100000Z-w3a-n3", "FAIL", 100, 160)
        probe(self.r, "20261024T100000Z-w3a-n3_", "PASS", 178)

    def tearDown(self):
        self.t.cleanup()

    def run_tool(self, *a):
        p = subprocess.run([sys.executable, TOOL, *a], capture_output=True, text=True, env=dict(os.environ, RUNS_ROOT=self.r))
        return p.returncode, p.stdout

    def test_list(self):
        rc, out = self.run_tool("list")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS n=3 unreadable=0", out)
        self.assertIn("300s", out)
        self.assertIn("178s", out)
        rc, out = self.run_tool("list", "--kind", "probe")
        self.assertIn("n=1 ", out)
        rc, out = self.run_tool("list", "--since", "2026-10-23")
        self.assertIn("n=2 ", out)

    def test_stats(self):
        rc, out = self.run_tool("stats", "--form", "w3a-n3", "--json")
        self.assertEqual(rc, 0, out)
        rows = json.loads(out.splitlines()[0])
        self.assertEqual(len(rows), 1)  # trailing "_" is not part of the stem
        r = rows[0]
        self.assertEqual((r["n"], r["PASS"], r["FAIL"], r["HANG"], r["ERROR"]), (3, 2, 1, 0, 0))
        self.assertEqual((r["p50"], r["max"]), (178.0, 300.0))
        rc, out = self.run_tool("stats", "--form", "nomatch")
        self.assertIn("RESULT: PASS n=3", out)

    def test_show_compare(self):
        rc, out = self.run_tool("show", "20261022T100000Z-w3a-n3")
        self.assertEqual(rc, 0, out)
        self.assertIn("verdict=PASS wall=300s git=abc1234", out)
        rc, out = self.run_tool("compare", "20261022T100000Z-w3a-n3", "20261023T100000Z-w3a-n3")
        self.assertIn("verdict  PASS | FAIL   <-- differs", out)
        rc, out = self.run_tool("show", "nope")
        self.assertEqual(rc, 2)

    def test_unreadable(self):
        os.makedirs(os.path.join(self.r, "logs", "runs", "20261025T100000Z-broken"))
        rc, out = self.run_tool("list")
        self.assertEqual(rc, 1, out)
        self.assertIn("unreadable: 20261025T100000Z-broken", out)
        self.assertIn("RESULT: PARTIAL n=4 unreadable=1", out)

    def test_bad_args(self):
        for a in (["stats"], ["bogus"], [], ["list", "--since", "x"], ["stats", "--form", "("]):
            rc, out = self.run_tool(*a)
            self.assertEqual(rc, 2, (a, out))
            self.assertIn("RESULT: ERROR", out)


if __name__ == "__main__":
    unittest.main()
