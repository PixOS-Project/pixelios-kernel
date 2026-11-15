"""T-2712 split of tools/test_gate_unit_shards.py: the ordering tests (OrderTest) in their own file so the unit shards run them in parallel with ShardsTest."""
import unittest

import test_gate_unit_shards as base


class OrderTest(base.PARTS["order"]):
    pass


if __name__ == "__main__":
    unittest.main()
