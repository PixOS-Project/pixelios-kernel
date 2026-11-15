"""T-4ecb: remote-job and heavy-job fall back to this host when the offload host is lost mid-job, with one gap line (tools/test_offload_fallback.sh; fake ssh, no network)."""
import pathlib
import subprocess
import unittest

SH = pathlib.Path(__file__).resolve().parent / "test_offload_fallback.sh"


class OffloadFallback(unittest.TestCase):
    def test_offload_fallback(self):
        self.assertTrue(SH.is_file(), "missing %s" % SH)
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.rstrip().splitlines()[-1], r"^RESULT: PASS [a-z-]+-fallback cases=[0-9]+$", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
