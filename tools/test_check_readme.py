"""Gate wrapper: runs tools/check-readme.py --self-test (fixtures + rule mutants) under `unittest discover`."""
import os, subprocess, sys, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-readme.py")


class CheckReadmeSelfTest(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, "-B", TOOL, "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("SURVIVED", r.stdout)


if __name__ == "__main__":
    unittest.main()
