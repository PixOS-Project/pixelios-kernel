"""tools/heavy-job host test, part 'fifo': local FIFO and the heavy-fifo-r1 fixes (tools/test_heavy_job_fifo.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_fifo(self):
        heavy_job_parts.run_part(self, "fifo")


if __name__ == "__main__":
    unittest.main()
