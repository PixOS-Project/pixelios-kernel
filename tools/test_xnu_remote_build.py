"""Runs tools/test_xnu_remote_build.sh (tools/xnu-remote-build dispatcher: fake ssh/rsync/docker + the real heavy-job queue, ledger, provenance, fallbacks, built-in mutants; ~90 s).
This wrapper is what puts the shell test into the gate: tools/gate-unit-shards runs every tools/test_*.py (review xnu-remote-build-r1 F1)."""
import os, pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_xnu_remote_build.sh"


class XnuRemoteBuild(unittest.TestCase):
    def test_xnu_remote_build(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=1500,
                           env={**os.environ, "HEAVY_JOB_DETACHED": "pixos-hj-unit-test-1-1", "HEAVY_JOB_NOTRUN_FILE": os.devnull})  # as in the gate unit scope (T-6269)
        self.assertEqual(r.returncode, 0, r.stdout[-6000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
