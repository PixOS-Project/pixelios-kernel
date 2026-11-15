"""T-2712 split of tools/test_wlan_mutant_pool.py: pool mutants with index % SLICES == 1 must all be killed (own file so the unit shards run the slices in parallel)."""
import unittest

import test_wlan_mutant_pool as base


class PoolSlice1(unittest.TestCase):
    def test_every_pool_mutant_of_the_slice_is_killed(self):
        base.kill_slice(self, 1)


if __name__ == "__main__":
    unittest.main()
