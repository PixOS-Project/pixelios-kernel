#!/usr/bin/env python3
"""Runs `tools/pixperf-server --self-test` (a loopback UDP upload of ~2 s through the real server code) so the gate's unit step covers it
(tools/test_selftests_wired.py: every tool with a --self-test is run by a gate step or a test file). No phone, no network beyond 127.0.0.1."""
import pathlib
import subprocess
import sys
import unittest

TOOL = pathlib.Path(__file__).resolve().parent / "pixperf-server"


class SelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        r = subprocess.run([sys.executable, "-I", str(TOOL), "--self-test"], capture_output=True, text=True, timeout=120)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertTrue(last.startswith("RESULT: PASS pixperf-server self-test"), last)


if __name__ == "__main__":
    unittest.main()
