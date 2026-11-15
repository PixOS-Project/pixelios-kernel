"""tools/heavy-job host test, part 'remote': remote-job resubmit check, probe script, detach (tools/test_heavy_job_remote.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_remote(self):
        heavy_job_parts.run_part(self, "remote")


if __name__ == "__main__":
    unittest.main()
