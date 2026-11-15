"""Runs tools/test_public_by_default.sh (T-6070: pre-commit/commit-msg hooks and the gate step `public-guard` block a leaked host name, IP, /home path,
.env value, host-named path, message and branch name; scratch repos only, seconds) and tools/test_public_push_guard.sh (the push guard, 18 cases).
This wrapper is how the gate (tools/gate-unit-shards: every tools/test_*.py) picks the .sh files up."""
import pathlib, subprocess, unittest

T = pathlib.Path(__file__).resolve().parent


class PublicByDefault(unittest.TestCase):
    def run_sh(self, name, want):
        r = subprocess.run(["bash", str(T / name)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(want, r.stdout.splitlines()[-1])

    def test_public_by_default(self):
        self.run_sh("test_public_by_default.sh", "RESULT: PASS public-by-default")

    def test_public_push_guard(self):
        self.run_sh("test_public_push_guard.sh", "RESULT: PASS test_public_push_guard")


if __name__ == "__main__":
    unittest.main()
