#!/usr/bin/env python3
"""Host test for tools/tools-sync (TP-021): temp git repo, no device."""
import os, shutil, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tsync-")
        self.addCleanup(shutil.rmtree, self.d)
        self.r = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.r, "tools"))
        for f in ("tools-sync", "phone-lock"):
            shutil.copy(os.path.join(TOOLS, f), os.path.join(self.r, "tools", f))
        self.env = dict(os.environ, PHONE_LOCK_DIR=os.path.join(self.d, "lock"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git("init", "-q", "-b", "main")
        self.w("tools/common-files.txt", "# c\ntools/a\ntools/b\n")
        self.w("tools/a", "A1\n"); self.w("tools/b", "B1\n")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.git("branch", "common-tools")
        # common-tools moves on: a changes
        self.git("checkout", "-q", "common-tools"); self.w("tools/a", "A2\n"); self.git("commit", "-qam", "a2"); self.git("checkout", "-q", "main")

    def git(self, *a):
        return subprocess.run(["git", "-C", self.r, *a], env=self.env, capture_output=True, text=True, check=True)

    def w(self, p, s):
        with open(os.path.join(self.r, p), "w") as f:
            f.write(s)

    def ts(self, *a):
        x = subprocess.run([sys.executable, os.path.join(self.r, "tools", "tools-sync"), *a], env=self.env, capture_output=True, text=True)
        self.assertRegex(x.stdout.splitlines()[-1], r"^RESULT: (PASS|FAIL|ERROR)")
        return x

    def test_status_and_pull(self):
        x = self.ts("status")
        self.assertEqual(x.returncode, 1)
        self.assertIn("differs    tools/a", x.stdout)
        self.assertIn("same       tools/b", x.stdout)
        x = self.ts("pull")
        self.assertEqual(x.returncode, 0, x.stdout)
        self.assertEqual(open(os.path.join(self.r, "tools/a")).read(), "A2\n")
        self.assertEqual(self.ts("status").returncode, 0)

    def test_pull_blocked_by_local_changes_and_force(self):
        self.w("tools/a", "LOCAL\n")
        x = self.ts("pull")
        self.assertEqual(x.returncode, 1)
        self.assertIn("blocked    tools/a", x.stdout)
        self.assertEqual(open(os.path.join(self.r, "tools/a")).read(), "LOCAL\n")
        self.assertEqual(self.ts("pull", "--force").returncode, 0)
        self.assertEqual(open(os.path.join(self.r, "tools/a")).read(), "A2\n")

    def test_pull_refused_while_the_phone_lock_is_held(self):
        subprocess.run([sys.executable, os.path.join(self.r, "tools", "phone-lock"), "acquire", "--owner", "x", "--pid", str(os.getpid())], env=self.env, check=True, capture_output=True)
        x = self.ts("pull")
        self.assertEqual(x.returncode, 1)
        self.assertIn("lock is held", x.stdout)
        self.assertEqual(open(os.path.join(self.r, "tools/a")).read(), "A1\n")

    def test_directory_entry_only_here_and_prune(self):
        # manifest line "patches/": every file of the dir at the ref; local extras are only-here (blocked until upstreamed or --prune)
        os.makedirs(os.path.join(self.r, "patches"))
        self.w("patches/0001-a.patch", "a\n"); self.w("tools/common-files.txt", "tools/a\ntools/b\npatches/\n")
        self.git("add", "-A"); self.git("commit", "-qm", "dir")
        self.git("checkout", "-q", "common-tools"); self.git("checkout", "-q", "main", "--", "tools/common-files.txt", "patches")
        self.w("patches/0002-b.patch", "b\n"); self.git("add", "-A"); self.git("commit", "-qm", "common patches"); self.git("checkout", "-q", "main")
        self.w("patches/0002-mine.patch", "x\n")  # untracked local extra with the same number
        x = self.ts("status")
        self.assertEqual(x.returncode, 1)
        self.assertIn("only-here  patches/0002-mine.patch", x.stdout)
        x = self.ts("pull")
        self.assertEqual(x.returncode, 1)
        self.assertIn("blocked    patches/0002-mine.patch", x.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.r, "patches/0002-b.patch")))  # the rest was pulled
        x = self.ts("pull", "--prune")
        self.assertEqual(x.returncode, 1)  # untracked = uncommitted work: not pruned without --force
        x = self.ts("pull", "--prune", "--force")
        self.assertEqual(x.returncode, 0, x.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.r, "patches/0002-mine.patch")))

    def test_committed_local_change_is_not_overwritten(self):
        # review 16 H3: a branch that COMMITTED its own change to a common file must not lose it to a pull
        self.w("tools/b", "B-LOCAL\n"); self.git("commit", "-qam", "local b")
        x = self.ts("pull")
        self.assertEqual(x.returncode, 1, x.stdout)
        self.assertEqual(open(os.path.join(self.r, "tools/b")).read(), "B-LOCAL\n")
        self.assertEqual(open(os.path.join(self.r, "tools/a")).read(), "A2\n")  # the file only the ref changed IS pulled
        self.assertEqual(self.ts("pull", "--force").returncode, 0)

    def test_pull_refused_while_a_build_holds_the_shim_out_lock(self):
        os.makedirs(os.path.join(self.r, "shim/out"))
        lk = open(os.path.join(self.r, "shim/out/.build.lock"), "w")
        import fcntl
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            x = self.ts("pull")
            self.assertEqual(x.returncode, 1)
            self.assertIn("shim/out/.build.lock", x.stdout)
        finally:
            lk.close()

    def test_pull_refused_while_a_gate_holds_the_gate_lock(self):
        os.makedirs(os.path.join(self.r, "shim/out"))
        lk = open(os.path.join(self.r, "shim/out/.gate.lock"), "w")
        import fcntl
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            x = self.ts("pull")
            self.assertEqual(x.returncode, 1)
            self.assertIn("shim/out/.gate.lock", x.stdout)
        finally:
            lk.close()

    def test_missing_file_and_missing_ref(self):
        os.remove(os.path.join(self.r, "tools/b"))
        self.assertIn("missing    tools/b", self.ts("status").stdout)
        self.assertEqual(self.ts("pull").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.r, "tools/b")))
        self.assertEqual(self.ts("status", "--ref", "nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
