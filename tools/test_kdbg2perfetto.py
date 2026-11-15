#!/usr/bin/env python3
"""tools/kdbg2perfetto --self-test runs in the gate (CR-01: every --self-test tool is wired)."""
import os, subprocess, sys, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kdbg2perfetto")


class SelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "--self-test"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
