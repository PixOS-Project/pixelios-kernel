"""Runs tools/test_build_hosttests.sh (host-test plumbing of shim/build.sh and the gate host-tests step; ~1 s)."""
import pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_build_hosttests.sh"


class BuildHostTests(unittest.TestCase):
    def test_plumbing(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
