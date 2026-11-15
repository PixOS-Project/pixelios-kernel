#!/usr/bin/env python3
"""tools/install-hooks run with a hook's leaked GIT_DIR (a linked worktree's gitdir) must still point core.hooksPath at the COMMON dir's pixos-hooks, never at the worktree's private dir
(2026-11-02: that made every worktree use one thread's hook copy, with the leaky pre-commit, and the copy vanishes with that worktree)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


def git(cwd, *a, env=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, capture_output=True, text=True, env=env)


class InstallHooksEnv(unittest.TestCase):
    def test_leaked_git_dir_does_not_redirect_hooks_path(self):
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "main"); wt_root = os.path.join(d, "wts"); os.makedirs(wt_root)
            os.makedirs(os.path.join(main, "tools", "hooks"))
            for name in ("pre-commit", "commit-msg"):
                with open(os.path.join(main, "tools", "hooks", name), "w") as f:
                    f.write("#!/bin/sh\nexit 0\n")
                os.chmod(os.path.join(main, "tools", "hooks", name), 0o755)
            with open(os.path.join(main, "tools", "claude-settings.json"), "w") as f:
                f.write("{}\n")
            shutil.copy(os.environ.get("INSTALL_HOOKS_SRC") or os.path.join(TOOLS, "install-hooks"), os.path.join(main, "tools", "install-hooks"))   # INSTALL_HOOKS_SRC: the mutant check runs the pre-fix script
            git(main, "init", "-q", "-b", "trunk"); git(main, "add", "-A"); git(main, "commit", "-qm", "base")
            wt = os.path.join(wt_root, "w1")
            self.assertEqual(git(main, "worktree", "add", "-q", "-b", "b1", wt).returncode, 0)
            gitdir = git(wt, "rev-parse", "--absolute-git-dir").stdout.strip()
            self.assertIn("/worktrees/", gitdir)
            env = dict(os.environ, GIT_DIR=gitdir, PIXOS_WORKTREES_DIR=wt_root, HOME=d)   # what a hook exports
            r = subprocess.run(["bash", os.path.join(main, "tools", "install-hooks")], cwd=wt, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            want = os.path.realpath(os.path.join(main, ".git", "pixos-hooks"))
            got = git(main, "config", "--get", "core.hooksPath").stdout.strip()
            self.assertEqual(os.path.realpath(got), want, "hooksPath must be the common dir's pixos-hooks, got %s" % got)


if __name__ == "__main__":
    unittest.main()
