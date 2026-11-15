"""Mutant group 2 of 3 of the gate-lint-first / gate-request tests (split from test_gate_lint_first.py, PLAN-GATE-2X K4a; fixtures: tools/gate_lint_first_lib.py)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_lint_first_lib import run_mutants


class GateLintFirstMutants(unittest.TestCase):
    def test_mutants_killed_group2(self):
        run_mutants(self, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
