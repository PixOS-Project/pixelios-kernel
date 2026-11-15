"""Runs tools/test_fflash_scan.sh (fflash-userdata flash-cache paths: miss, background prep, hit, stale copy, missing/failing helper; fake fastboot, real libsparse)."""
import pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_fflash_scan.sh"


class FflashScan(unittest.TestCase):
    def test_fflash_scan(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
