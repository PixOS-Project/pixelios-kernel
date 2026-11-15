#!/usr/bin/env python3
"""tools/wlan_g2l_harness_b: its self-test must pass."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "wlan_g2l_harness_b.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
