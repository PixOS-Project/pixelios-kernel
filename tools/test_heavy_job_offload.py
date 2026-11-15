"""tools/heavy-job host test, part 'offload-host': offload-host queue (fake ssh answers) (tools/test_heavy_job_offload.sh; fake ssh, no network)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_offload(self):
        heavy_job_parts.run_part(self, "offload")


if __name__ == "__main__":
    unittest.main()
