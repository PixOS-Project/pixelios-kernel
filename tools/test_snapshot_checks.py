#!/usr/bin/env python3
"""The repo-wide checks must give the same verdict on a clean `git archive HEAD` snapshot (what a gate container sees: no ignored or untracked files) as in the author's
worktree. The tool-census broke the gate on 2026-11-01 because a private gitignored tools/offload-host.conf was counted in the worktree only (T-b0e1). R30."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = [["tools/tool-census", "--check"], ["tools/lint-size"], ["tools/lint-silent"]]


def run(cwd, cmd):
    r = subprocess.run([sys.executable, "-I"] + cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]


class Snapshot(unittest.TestCase):
    def test_checks_pass_on_a_clean_archive_and_with_stray_private_files(self):
        with tempfile.TemporaryDirectory() as d:
            ar = subprocess.run(["git", "-C", ROOT, "archive", "HEAD"], capture_output=True, timeout=120)
            self.assertEqual(ar.returncode, 0, ar.stderr)
            subprocess.run(["tar", "-x", "-C", d], input=ar.stdout, check=True)
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "snap"], cwd=d, check=True)
            for cmd in CHECKS:
                if not os.path.exists(os.path.join(d, cmd[0])):
                    continue
                rc, last = run(d, cmd)
                self.assertEqual(rc, 0, "clean snapshot: %s -> %s" % (cmd, last))
            with open(os.path.join(d, ".git", "info", "exclude"), "a") as f:   # a private local config like the one that broke the census
                f.write("tools/private-local.conf\n")
            with open(os.path.join(d, "tools", "private-local.conf"), "w") as f:
                f.write("host=none\n")
            for cmd in CHECKS:
                if not os.path.exists(os.path.join(d, cmd[0])):
                    continue
                rc, last = run(d, cmd)
                self.assertEqual(rc, 0, "snapshot + git-excluded private file: %s -> %s" % (cmd, last))


if __name__ == "__main__":
    unittest.main()
