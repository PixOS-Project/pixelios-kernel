"""tools/heavy-job host test, part 'light': T-66a6: the --light slot (tools/test_heavy_job_light.sh; no network, no phone)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heavy_job_parts


class HeavyJob(unittest.TestCase):
    def test_heavy_job_light(self):
        heavy_job_parts.run_part(self, "light")


if __name__ == "__main__":
    unittest.main()
