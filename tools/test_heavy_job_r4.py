"""tools/heavy-job host test, part 'r4': heavy-fifo-r4 fixes (daemon children, SIGKILL during the grace) (tools/test_heavy_job_r4.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_r4(self):
        heavy_job_parts.run_part(self, "r4")


if __name__ == "__main__":
    unittest.main()
