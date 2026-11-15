#!/usr/bin/env python3
"""tools/gpuclk_audit.py: its self-test (the mutants of the handover DVFS step and of gpu_probe_pmu_on) must pass."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "gpuclk_audit.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS gpuclk_audit self-test", r.stdout)


if __name__ == "__main__":
    unittest.main()
