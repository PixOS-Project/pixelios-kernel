"""tools/heavy-job host test, part 'r3': heavy-fifo-r3 fixes (parent death, signals, grace) (tools/test_heavy_job_r3.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_r3(self):
        heavy_job_parts.run_part(self, "r3")


if __name__ == "__main__":
    unittest.main()
