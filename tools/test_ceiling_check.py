#!/usr/bin/env python3
"""Tests for tools/ceiling-check (T-d4d9): the ceiling script must reproduce the scored baseline number (oracle) before its ceiling counts."""
import os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "ceiling-check")


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.run_dir = os.path.join(self.t.name, "run")
        os.makedirs(self.run_dir)

    def tearDown(self):
        self.t.cleanup()

    def script(self, body):
        p = os.path.join(self.t.name, "s.py")
        with open(p, "w") as f:
            f.write(body)
        return p

    def cc(self, script, scored="53.6", *more):
        p = subprocess.run([sys.executable, TOOL, "--script", script, "--run-dir", self.run_dir, "--scored", scored, *more], capture_output=True, text=True)
        return p.returncode, p.stdout

    def test_pass_quotes_the_line_the_plan_needs(self):
        rc, out = self.cc(self.script('print("noise"); print("ORACLE 53.6"); print("CEILING 58.2")'))
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"RESULT: PASS ceiling-check script=\S+ ceiling=58\.2 oracle=53\.6 scored=53\.6")

    def test_oracle_mismatch_fails_and_says_unproven(self):
        rc, out = self.cc(self.script('print("ORACLE 49.0"); print("CEILING 58.2")'))
        self.assertEqual(rc, 1, out); self.assertIn("RESULT: FAIL ceiling-check", out); self.assertIn("unproven", out)

    def test_tolerance_option(self):
        s = self.script('print("ORACLE 53.9"); print("CEILING 58.2")')
        self.assertEqual(self.cc(s)[0], 1)
        self.assertEqual(self.cc(s, "53.6", "--tol", "0.5")[0], 0)

    def test_missing_lines_script_error_and_usage(self):
        rc, out = self.cc(self.script('print("CEILING 58.2")'))
        self.assertEqual(rc, 1, out); self.assertIn("no ORACLE line", out)
        rc, out = self.cc(self.script('print("ORACLE 53.6")'))
        self.assertEqual(rc, 1, out); self.assertIn("no CEILING line", out)
        rc, out = self.cc(self.script("raise SystemExit(3)"))
        self.assertEqual(rc, 1, out); self.assertIn("rc=3", out)
        rc, out = self.cc("/nonexistent")
        self.assertEqual(rc, 2, out); self.assertIn("RESULT: ERROR", out)
        p = subprocess.run([sys.executable, TOOL], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_script_runs_isolated_with_the_run_dir_as_argument(self):
        rc, out = self.cc(self.script('import sys; print("ORACLE 1.0" if sys.argv[1].endswith("run") else "x"); print("CEILING 2.0")'), "1.0")
        self.assertEqual(rc, 0, out)

    def test_mutants_are_killed(self):
        """Each clause of the check is needed: the oracle comparison, the ORACLE line requirement, the exit codes."""
        src = open(TOOL).read()
        good = self.script('print("ORACLE 49.0"); print("CEILING 58.2")')
        muts = [("oracle compare", "if abs(oracle - scored) > tol:", "if False:"), ("need both lines", "if not o or not c:", "if False:")]
        for name, old, new in muts:
            self.assertEqual(src.count(old), 1, name)
            mp = os.path.join(self.t.name, "mut-" + name.replace(" ", "-"))
            with open(mp, "w") as f:
                f.write(src.replace(old, new))
            for body in ('print("ORACLE 49.0"); print("CEILING 58.2")', 'print("CEILING 58.2")'):
                p = subprocess.run([sys.executable, mp, "--script", self.script(body), "--run-dir", self.run_dir, "--scored", "53.6"], capture_output=True, text=True)
                if p.returncode != 1 or "RESULT: FAIL" not in p.stdout:
                    break
            else:
                self.fail("mutant survived: " + name)


if __name__ == "__main__":
    unittest.main()
