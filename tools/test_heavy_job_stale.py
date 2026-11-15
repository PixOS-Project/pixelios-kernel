"""tools/heavy-job host test, part 'stale': T-d543: a stale copy re-execs the canonical one, status flags STALE-WAITER (tools/test_heavy_job_stale.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_stale(self):
        heavy_job_parts.run_part(self, "stale")


if __name__ == "__main__":
    unittest.main()
