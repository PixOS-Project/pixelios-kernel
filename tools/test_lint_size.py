#!/usr/bin/env python3
"""tools/lint-size: the inline fixtures and mutants (OVER, ALLOW-GREW, ALLOW-SHRUNK, ALLOW-STALE) and the real tree must pass (R29)."""
import os
import subprocess
import sys
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(TOOLS, "lint-size")


class LintSize(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, LINT, "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_real_tree_passes(self):
        r = subprocess.run([sys.executable, LINT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS lint-size", r.stdout)


if __name__ == "__main__":
    unittest.main()
