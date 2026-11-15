"""Unit wrapper of tools/test_job_wake.sh (R4, T-afd9): heavy-job detach / remote-job --wake-hook against a stub webhook server, no network beyond 127.0.0.1."""
import pathlib
import re
import subprocess
import unittest

DIR = pathlib.Path(__file__).resolve().parent


class JobWake(unittest.TestCase):
    def test_job_wake_script(self):
        r = subprocess.run(["bash", str(DIR / "test_job_wake.sh")], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.rstrip().splitlines()[-1], r"^RESULT: PASS job-wake cases=[0-9]+$", r.stdout + r.stderr)

    def test_job_wake_self_test(self):
        r = subprocess.run(["python3", "-I", str(DIR / "job-wake"), "--self-test"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(re.search(r"^RESULT: PASS job-wake [0-9]+/[0-9]+$", r.stdout, re.M), r.stdout)


if __name__ == "__main__":
    unittest.main()
