#!/usr/bin/env python3
"""tools/mifclk_audit.py: its self-test (the mutants of shim/mifclk.c and of the --mif-clk-level allowlist: acpm_gpudom.h, acpm_mifdom.h, build.sh) must pass."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "mifclk_audit.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS mifclk_audit self-test", r.stdout)
        self.assertIn("PASS mifclk level self-test: 14/14", r.stdout)


if __name__ == "__main__":
    unittest.main()
