"""tools/heavy-job host test, part 'route': per-use offload switches and the shared load of light host tests (tools/test_heavy_job_route.sh; fake ssh + stub remote-job, no network, no phone)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_route(self):
        heavy_job_parts.run_part(self, "route")


if __name__ == "__main__":
    unittest.main()
