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
        self.assertEqual(rc, 0, out)   # review B4: runs that died before a report never become readable: not a failure by default
        self.assertIn("unreadable: 20261025T100000Z-broken", out)
        self.assertIn("RESULT: PASS n=4 unreadable=1", out)
        rc, out = self.run_tool("list", "--strict")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: PARTIAL n=4 unreadable=1", out)

    def test_stats_args_form_and_timeout_column(self):   # review B2/B5
        for n, v in (("8", "PASS"), ("9", "TIMEOUT")):
            probe(self.r, "20261026T10000%sZ-w3a-n%s" % (n, n), v, 100)
            put(os.path.join(self.r, "logs", "probe", "20261026T10000%sZ-w3a-n%s" % (n, n), "run.args"), "--wlan-link=%s --wlan-probe\n" % n)
        rc, out = self.run_tool("stats", "--args=--wlan-probe --wlan-link=3", "--json")
        self.assertEqual(rc, 0, out)
        rows = json.loads(out.splitlines()[0])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["n"], rows[0]["PASS"], rows[0]["TIMEOUT"], rows[0]["other"]), (2, 1, 1, 0))
        rc, out = self.run_tool("stats", "--args=--nothing", "--json")
        self.assertEqual(json.loads(out.splitlines()[0]), [])

    def test_argkey_touch_probe_mode_and_bare_values(self):   # review round 2 B3
        for n, mode in (("1", "railsr"), ("2", "run")):
            nm = "20261026T10000%sZ-tp-%s" % (n, mode)
            probe(self.r, nm, "PASS", 100)
            put(os.path.join(self.r, "logs", "probe", nm, "run.args"), "--pstore-fast --touch-probe=%s\n" % mode)
        rc, out = self.run_tool("stats", "--args=--pstore-fast --touch-probe=run", "--json")
        self.assertEqual([r["n"] for r in json.loads(out.splitlines()[0])], [1], out)
        rc, out = self.run_tool("stats", "--args=--wlan-link 3 --wlan-probe", "--json")   # a bare value word is not its own key
        self.assertEqual(json.loads(out.splitlines()[0]), [], out)

    def test_xrun_wall_fallback_to_waited(self):   # review B6
        d = os.path.join(self.r, "logs", "runs", "20261027T100000Z-nowall")
        xrun(self.r, "20261027T100000Z-nowall", "PASS", 1, 2)
        os.remove(d + "/run.json")
        rc, out = self.run_tool("list", "--kind", "xrun")
        self.assertIn("nowall", out)
        self.assertRegex(out, r"nowall\s+xrun\s+PASS\s+1s")

    def test_bad_args(self):
        for a in (["stats"], ["bogus"], [], ["list", "--since", "x"], ["stats", "--form", "("]):
            rc, out = self.run_tool(*a)
            self.assertEqual(rc, 2, (a, out))
            self.assertIn("RESULT: ERROR", out)


if __name__ == "__main__":
    unittest.main()
