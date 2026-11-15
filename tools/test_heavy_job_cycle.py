"""tools/heavy-job host test, part 'cycle': T-70cd: the wait-cycle breaker (tools/test_heavy_job_cycle.sh; no network, no phone)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_cycle(self):
        heavy_job_parts.run_part(self, "cycle")


if __name__ == "__main__":
    unittest.main()
