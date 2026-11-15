"""Runs tools/flash_cache.py --self-test, whose cases live in tools/flash_cache_selftest_lib.py (hash stamps, prep + independent verify, lookup, stale copy, disk guard, eviction, gc; real libsparse, tiny images)."""
import pathlib, subprocess, sys, unittest

TOOL = pathlib.Path(__file__).resolve().parent / "flash_cache.py"


class FlashCache(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, "-I", str(TOOL), "--self-test"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS flash_cache self-test", r.stdout)


if __name__ == "__main__":
    unittest.main()
