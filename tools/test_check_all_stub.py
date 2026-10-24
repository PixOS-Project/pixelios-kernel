"""Runs tools/test_check_all_stub.sh (stub-based test of check-all.sh: interrupt, stamp, timeout validation, lock wait; ~15 s)."""
import pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_check_all_stub.sh"


class CheckAllStub(unittest.TestCase):
    def test_stub_gate(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
