"""tools/heavy-job host test, part 'basic': lock, memory gate, PSI, load1, re-entrancy, rc mapping (tools/test_heavy_job_basic.sh; fake ssh, no network)."""
import os, pathlib, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_basic(self):
        heavy_job_parts.run_part(self, "basic")

    def test_registration_check_catches_a_missing_wrapper_or_script(self):
        """check_registered must fail when a wrapper, a part script or the manual runner's entry is gone (review gate-cold-k14 r1 F7)."""
        here = heavy_job_parts.DIR
        for victim in ("test_heavy_job_remote.py", "test_heavy_job_fifo.sh", "test_heavy_job.py"):
            with self.subTest(victim=victim), tempfile.TemporaryDirectory() as d:
                for f in list(here.glob("test_heavy_job*.py")) + list(here.glob("test_heavy_job*.sh")):
                    shutil.copy(f, d)
                os.remove(os.path.join(d, victim))
                with mock.patch.object(heavy_job_parts, "DIR", pathlib.Path(d)), self.assertRaises(AssertionError):
                    heavy_job_parts.check_registered(self)
        with tempfile.TemporaryDirectory() as d:                   # a wrapper without a part (an unregistered extra file) fails too
            for f in list(here.glob("test_heavy_job*.py")) + list(here.glob("test_heavy_job*.sh")):
                shutil.copy(f, d)
            shutil.copy(os.path.join(d, "test_heavy_job_r3.py"), os.path.join(d, "test_heavy_job_extra.py"))
            with mock.patch.object(heavy_job_parts, "DIR", pathlib.Path(d)), self.assertRaises(AssertionError):
                heavy_job_parts.check_registered(self)
        with tempfile.TemporaryDirectory() as d:                   # the manual runner drops a part
            for f in list(here.glob("test_heavy_job*.py")) + list(here.glob("test_heavy_job*.sh")):
                shutil.copy(f, d)
            sh = os.path.join(d, "test_heavy_job.sh")
            with open(sh) as fh:
                t = fh.read()
            self.assertIn("offload remote stale light cycle route; do", t)
            with open(sh, "w") as fh:
                fh.write(t.replace("offload remote stale light cycle route; do", "offload remote; do"))
            with mock.patch.object(heavy_job_parts, "DIR", pathlib.Path(d)), self.assertRaises(AssertionError):
                heavy_job_parts.check_registered(self)


if __name__ == "__main__":
    unittest.main()
