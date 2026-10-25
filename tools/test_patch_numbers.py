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

    def test_reserved_number_under_another_name_is_a_conflict(self):
        self.git("checkout", "-q", "main"); self.git("branch", "-D", "wifi")
        n = self.pn("next", "--owner", "x", "--name", "mine").stdout.splitlines()[0]
        self.git("checkout", "-q", "gpu"); self.add("%s-theirs.patch" % n); self.git("add", "-A"); self.git("commit", "-qm", "x")
        r = self.pn("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("reserved by x", r.stdout)


if __name__ == "__main__":
    unittest.main()
