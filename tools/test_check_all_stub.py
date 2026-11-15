"""Runs tools/test_check_all_stub.sh (stub-based test of check-all.sh: interrupt, stamp, timeout validation, lock wait; ~15 s)."""
import os, pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_check_all_stub.sh"


class CheckAllStub(unittest.TestCase):
    def test_stub_gate(self):
        # a cold gate exports GATE_COLD / GATE_MUT_CACHE / PIXELIOS_AUDIT_MEMO_DIR into its unit tests: the stub gates here must not inherit them (cold-base1: GATE_COLD=1 adds cold=1 to every GATE line)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_") and k != "PIXELIOS_AUDIT_MEMO_DIR"}
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=600, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
