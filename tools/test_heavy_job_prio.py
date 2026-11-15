"""tools/heavy-job host test, part 'prio': priority admission of the local queue (T-8bf1) (tools/test_heavy_job_prio.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_prio(self):
        heavy_job_parts.run_part(self, "prio")


if __name__ == "__main__":
    unittest.main()
