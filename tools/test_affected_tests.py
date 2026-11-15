#!/usr/bin/env python3
"""tools/affected-tests and its use in tools/gate: --list works, and a failing selection REFUSES the gate before anything starts (the 2026-11-02 gate found six unit failures after 8 minutes)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class AffectedTests(unittest.TestCase):
    def test_list_prints_a_result_line(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "affected-tests"), "--list"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r"^RESULT: PASS affected-tests selected=\d+ of \d+ failed=0")

    def test_max_defers_a_big_selection_loudly(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "affected-tests"), "--base", "HEAD~1", "--max", "1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"selected=\d+ of \d+ failed=0 .*(over --max, not run|nothing run|no test file)")

    def _gate_in_fixture(self, stub_rc, env_extra=None):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tools"))
            shutil.copy(os.path.join(TOOLS, "gate"), os.path.join(d, "tools", "gate"))
            for name, body in (("affected-tests", "echo 'RESULT: FAIL affected-tests stub'\nexit %d\n" % stub_rc), ("gate-trunk-check", "echo 'RESULT: PASS stub'\nexit 0\n"),
                               ("heavy-job", "echo 'heavy-job STARTED'\nexit 0\n")):
                with open(os.path.join(d, "tools", name), "w") as f:
                    f.write("#!/bin/bash\n" + body)
                os.chmod(os.path.join(d, "tools", name), 0o755)
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            env = dict(os.environ, **(env_extra or {}))
            for k in ("GATE_DRY", "GATE_AFFECTED_CHECKED", "GATE_AFFECTED_SKIP"):   # the gate's own unit run exports CHECKED; this test is about the unset case
                env.pop(k, None)
            env.update(env_extra or {})
            return subprocess.run(["bash", os.path.join(d, "tools", "gate"), "--quick", "--changed", "--label", "t"], capture_output=True, text=True, env=env, timeout=60)

    def test_failing_selection_refuses_the_gate(self):
        r = self._gate_in_fixture(1)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RESULT: FAIL gate refused: affected-tests rc=1", r.stdout)
        self.assertNotIn("STARTED", r.stdout)

    def test_skip_is_loud_not_silent(self):
        r = self._gate_in_fixture(1, {"GATE_AFFECTED_SKIP": "test escape"})
        self.assertIn("gate: GAP: affected-tests skipped on purpose: test escape", r.stdout + r.stderr)
        self.assertNotIn("refused: affected-tests", r.stdout)


if __name__ == "__main__":
    unittest.main()
