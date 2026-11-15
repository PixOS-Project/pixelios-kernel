#!/usr/bin/env python3
"""A caller's repo-pointing git environment (GIT_DIR ... set by any git hook) must never reach a unit test: tools/gate-unit-shards scrubs it (and tools/test_kernel_hooks.sh does too when run directly).
2026-11-02: a hook-launched test run inherited GIT_DIR, so `git -C scratch ...` fixtures hit the real repo (stray commit, core.bare=true, fixture branches)."""
import os
import subprocess
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
PROBE = '''import os, unittest


class P(unittest.TestCase):
    def test_no_repo_pointing_git_env(self):
        leaked = sorted(k for k in os.environ if k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"))
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
'''


class RunnerGitEnv(unittest.TestCase):
    def run_runner(self, script):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_probe.py"), "w") as f:
                f.write(PROBE)
            env = dict(os.environ, GATE_UNIT_DIR=d, GATE_UNIT_OUT=os.path.join(d, "out"), GIT_DIR="/nonexistent/leaked", GIT_INDEX_FILE="/nonexistent/index", GATE_AFFECTED_CHECKED="1")
            return subprocess.run(["bash", os.path.join(TOOLS, script)], cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)

    def test_gate_unit_shards_scrubs_git_env(self):
        r = self.run_runner("gate-unit-shards")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
        self.assertIn("RESULT: PASS", r.stdout)

    def test_the_probe_fails_without_the_scrub(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_probe.py"), "w") as f:
                f.write(PROBE)
            r = subprocess.run(["python3", "-m", "unittest", "discover", "-s", d, "-p", "test_probe.py"], capture_output=True, text=True, env=dict(os.environ, GIT_DIR="/nonexistent/leaked"))
            self.assertNotEqual(r.returncode, 0, "the probe must detect a leaked GIT_DIR (negative control)")


if __name__ == "__main__":
    unittest.main()
