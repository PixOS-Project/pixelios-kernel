"""Runs tools/test_audit_memo_mutations.sh (end-to-end memo check: every mutant of an audited input still fails with a warm memo; ~30 s)."""
import pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_audit_memo_mutations.sh"


class AuditMemoMutations(unittest.TestCase):
    def test_mutants_caught(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=240)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"RESULT: PASS mutants=\d+ caught=\d+")


if __name__ == "__main__":
    unittest.main()
