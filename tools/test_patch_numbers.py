#!/usr/bin/env python3
"""Host test for tools/patch-numbers (TP-022): temp git repo with two diverging branches."""
import os, shutil, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch-numbers")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pnum-")
        self.addCleanup(shutil.rmtree, self.d)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        os.makedirs(os.path.join(self.d, "tools"))
        shutil.copy(TOOL, os.path.join(self.d, "tools", "patch-numbers"))
        self.git("init", "-q", "-b", "main")
        self.add("0100-base.patch"); self.add("0101-vsync.patch")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.git("checkout", "-qb", "gpu"); self.add("0102-gpu-probe.patch"); self.git("add", "-A"); self.git("commit", "-qm", "gpu")
        self.git("checkout", "-q", "main"); self.git("checkout", "-qb", "wifi"); self.add("0102-wlan-standin.patch"); self.git("add", "-A"); self.git("commit", "-qm", "wifi")

    def git(self, *a):
        return subprocess.run(["git", "-C", self.d, *a], env=self.env, capture_output=True, text=True, check=True)

    def add(self, name):
        os.makedirs(os.path.join(self.d, "xnu", "patches"), exist_ok=True)
        open(os.path.join(self.d, "xnu", "patches", name), "w").write(name)

    def pn(self, *a):
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools", "patch-numbers"), *a], env=self.env, capture_output=True, text=True)
        self.assertRegex(r.stdout.splitlines()[-1], r"^RESULT: (PASS|FAIL|ERROR)")
        return r

    def test_overlap_is_found(self):
        r = self.pn("status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT 0102 used under 2 different names", r.stdout)
        self.assertEqual(self.pn("check").returncode, 1)  # current branch = wifi, 0102 differs on gpu

    def test_clean_when_names_agree(self):
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        self.assertEqual(self.pn("status").returncode, 1 - 1)
        self.assertEqual(self.pn("check").returncode, 0)

    def test_next_reserves_above_everything_and_is_unique(self):
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        a = self.pn("next", "--owner", "x", "--name", "foo").stdout.splitlines()[0]
        b = self.pn("next", "--owner", "y", "--name", "bar").stdout.splitlines()[0]
        self.assertEqual((a, b), ("0103", "0104"))
        self.assertIn("RESERVED by x (foo)", self.pn("status").stdout)
        self.pn("release", "0104")
        self.assertEqual(self.pn("next", "--owner", "z").stdout.splitlines()[0], "0104")

    def test_release_below_a_higher_number_keeps_a_skipped_placeholder(self):
        # 0233-0239 class (T-8ad1): deleting a reservation under a used number left an unheld series gap
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        self.pn("next", "--owner", "x", "--name", "foo"); self.pn("next", "--owner", "y", "--name", "bar")   # 0103, 0104
        r = self.pn("release", "0103")
        self.assertIn("skipped", r.stdout.splitlines()[-1])
        self.assertIn("0103  (not committed anywhere)   RESERVED by skipped (?)", self.pn("status").stdout)
        self.assertEqual(self.pn("next", "--owner", "z").stdout.splitlines()[0], "0105")
        self.git("checkout", "-q", "gpu"); self.add("0103-sweet-late.patch"); self.git("add", "-A")
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools", "patch-numbers"), "precommit"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)   # the placeholder is unnamed: a real patch may still take it

    def test_precommit_refuses_a_number_that_skips_past_the_top(self):
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        self.git("checkout", "-q", "gpu"); self.add("0106-sweet-far.patch"); self.git("add", "-A")   # top is 0102: 0103..0105 would be unheld
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools", "patch-numbers"), "precommit"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("skips 0103..0105", r.stdout)
        self.git("rm", "-q", "--cached", "xnu/patches/0106-sweet-far.patch")
        self.add("0103-sweet-next.patch"); self.git("add", "xnu/patches/0103-sweet-next.patch")
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools", "patch-numbers"), "precommit"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unnamed_placeholder_reservation_accepts_a_real_patch(self):
        # `claim N --owner wifi-reserved` (no name) is a placeholder: a real patch file with that number, any name, is not a conflict
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        self.assertEqual(self.pn("claim", "0103", "--owner", "wifi-reserved").returncode, 0)
        self.assertIn("RESERVED by wifi-reserved", self.pn("status").stdout)
        self.git("checkout", "-q", "gpu"); self.add("0103-sweet-x.patch"); self.git("add", "-A"); self.git("commit", "-qm", "real 0103")
        r = self.pn("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("reserved by", r.stdout)

    def test_reserved_number_under_another_name_is_a_conflict(self):
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        n = self.pn("next", "--owner", "x", "--name", "mine").stdout.splitlines()[0]
        self.git("checkout", "-q", "gpu"); self.add("%s-theirs.patch" % n); self.git("add", "-A"); self.git("commit", "-qm", "x")
        r = self.pn("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("reserved by x", r.stdout)


if __name__ == "__main__":
    unittest.main()
