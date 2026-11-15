"""Runs tools/test_xnu_src_mirror.sh (xnu-src-mirror seed/env, no network: local bare remotes, unshare -rn fetch; a few seconds)."""
import pathlib, subprocess, unittest

SH = pathlib.Path(__file__).resolve().parent / "test_xnu_src_mirror.sh"


class XnuSrcMirror(unittest.TestCase):
    def test_xnu_src_mirror(self):
        r = subprocess.run(["bash", str(SH)], capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
