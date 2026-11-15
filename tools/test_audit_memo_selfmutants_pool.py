"""T-2712 split of tools/test_audit_memo_selfmutants.py: the mutants of table group 'pool' (SPLIT there) must all be killed. Same body, own file, so the unit shards run the four groups in parallel."""
import unittest

import test_audit_memo_selfmutants as base


class SelfMutantsPool(unittest.TestCase):
    def test_every_mutant_is_killed(self):
        base.kill_tables(self, 'pool')


if __name__ == "__main__":
    unittest.main()
