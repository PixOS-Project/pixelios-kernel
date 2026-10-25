#!/usr/bin/env python3
"""tools/lint-c with fake cppcheck/clang-tidy in a fixture root (no real analyzer needed)."""
import os, stat, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lint-c")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.r = self.d.name
        os.makedirs(self.r + "/shim/out")
        open(self.r + "/shim/a.c", "w").close()
        open(self.r + "/shim/a_host_test.c", "w").close()

    def tearDown(self):
        self.d.cleanup()

    def fake(self, name, out):
        p = os.path.join(self.r, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\ncat <<'X'\n%s\nX\n" % out)
        os.chmod(p, 0o755)
        return p

    def run_tool(self, cpp="", tidy="", board=True, *args):
        if board:
            open(self.r + "/shim/out/board_config.h", "w").close()
        env = dict(os.environ, LINT_ROOT=self.r, LINT_CPPCHECK=self.fake("cpp", cpp), LINT_TIDY=self.fake("tidy", tidy))
        return subprocess.run([sys.executable, TOOL, *args], env=env, capture_output=True, text=True)

    def test_clean_and_noipa_filtered_and_host_test_skipped(self):
        r = self.run_tool("shim/a.c:3: style: x: y", "shim/a.c:1:1: warning: unknown attribute 'noipa' ignored")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RESULT: PASS files=1 findings=0", r.stdout)

    def test_findings_fail_and_strict_counts_style(self):
        self.assertEqual(self.run_tool("shim/a.c:3: warning: id: bad", "").returncode, 1)
        self.assertEqual(self.run_tool("", "shim/a.c:1:1: warning: bad [bugprone-x]").returncode, 1)
        self.assertEqual(self.run_tool("shim/a.c:3: style: x: y", "").returncode, 0)
        self.assertEqual(self.run_tool("shim/a.c:3: style: x: y", "", True, "--strict").returncode, 1)

    def test_missing_board_config_is_error(self):
        env = dict(os.environ, LINT_ROOT=self.r, LINT_CPPCHECK=self.fake("cpp", ""), LINT_TIDY=self.fake("tidy", ""))
        r = subprocess.run([sys.executable, TOOL], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("RESULT: ERROR", r.stdout)

    def test_out_dir_selects_the_header(self):
        os.makedirs(self.r + "/shim/out/gate/b0")
        open(self.r + "/shim/out/gate/b0/board_config.h", "w").close()     # only here, not in shim/out
        env = dict(os.environ, LINT_ROOT=self.r, LINT_CPPCHECK=self.fake("cpp", ""), LINT_TIDY=self.fake("tidy", ""))
        r = subprocess.run([sys.executable, TOOL, "--out", "shim/out/gate/b0"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, TOOL, "--out", "shim/out/gate/nope"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("shim/out/gate/nope/board_config.h", r.stdout)


if __name__ == "__main__":
    unittest.main()
