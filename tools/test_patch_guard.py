#!/usr/bin/env python3
"""Host test for tools/patch-guard (the Claude hook) and the pre-commit side of tools/patch-numbers (TP-022)."""
import json, os, shutil, subprocess, sys, tempfile, unittest

T = os.path.dirname(os.path.abspath(__file__))


class G(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pguard-")
        self.addCleanup(shutil.rmtree, self.d)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        os.makedirs(os.path.join(self.d, "tools")); os.makedirs(os.path.join(self.d, "xnu/patches"))
        for f in ("patch-numbers", "patch-guard"):
            shutil.copy(os.path.join(T, f), os.path.join(self.d, "tools", f))
        self.git("init", "-q", "-b", "main")
        self.w("xnu/patches/0101-vsync.patch", "x"); self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.git("checkout", "-qb", "gpu"); self.w("xnu/patches/0102-gpu.patch", "g"); self.git("add", "-A"); self.git("commit", "-qm", "gpu")
        self.git("checkout", "-q", "main")

    def git(self, *a):
        return subprocess.run(["git", "-C", self.d, *a], env=self.env, capture_output=True, text=True, check=True)

    def w(self, p, s):
        with open(os.path.join(self.d, p), "w") as f:
            f.write(s)

    def pn(self, *a):
        return subprocess.run([sys.executable, os.path.join(self.d, "tools/patch-numbers"), *a], env=self.env, capture_output=True, text=True)

    def hook(self, **kw):
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools/patch-guard")], input=json.dumps(dict(kw, cwd=self.d)), env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"

    def test_write_needs_a_reservation(self):
        new = os.path.join(self.d, "xnu/patches/0102-wlan-standin.patch")  # 0102 exists on gpu under another name
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": new}), "deny")
        n = self.pn("next", "--owner", "wifi", "--name", "wlan-standin").stdout.splitlines()[0]
        self.assertEqual(n, "0103")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": new}), "deny")  # wrong number for the reservation
        ok = os.path.join(self.d, "xnu/patches/0103-wlan-standin.patch")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": ok}), "allow")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": os.path.join(self.d, "xnu/patches/0103-other-name.patch")}), "deny")

    def test_existing_patch_and_other_files_are_free(self):
        self.assertEqual(self.hook(tool_name="Edit", tool_input={"file_path": os.path.join(self.d, "xnu/patches/0101-vsync.patch")}), "allow")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": os.path.join(self.d, "docs/x.md")}), "allow")

    def test_bash_writes_are_caught_reads_are_not(self):
        self.assertEqual(self.hook(tool_name="Bash", tool_input={"command": "diff -u a b > xnu/patches/0150-foo.patch"}), "deny")
        self.assertEqual(self.hook(tool_name="Bash", tool_input={"command": "cp /tmp/x xnu/patches/0150-foo.patch"}), "deny")
        self.assertEqual(self.hook(tool_name="Bash", tool_input={"command": "cat xnu/patches/0150-foo.patch; ls xnu/patches/0150-foo.patch"}), "allow")
        self.assertEqual(self.hook(tool_name="Bash", tool_input={"command": "git show gpu:xnu/patches/0102-gpu.patch"}), "allow")

    def test_precommit_blocks_a_collision_and_reserves_a_clean_patch(self):
        self.w("xnu/patches/0102-wlan.patch", "w"); self.git("add", "-A")  # 0102 is 0102-gpu.patch on gpu
        r = self.pn("precommit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("overlapping", r.stdout)
        os.remove(os.path.join(self.d, "xnu/patches/0102-wlan.patch")); self.git("rm", "-q", "--cached", "xnu/patches/0102-wlan.patch")
        self.w("xnu/patches/0150-free.patch", "f"); self.git("add", "-A")
        self.assertEqual(self.pn("precommit").returncode, 0)
        self.assertIn("RESERVED by", self.pn("status").stdout.split("0150")[1])  # reserved for everyone by the commit

    def test_claim(self):
        self.assertEqual(self.pn("claim", "0140", "--owner", "a", "--name", "foo").returncode, 0)
        self.assertEqual(self.pn("claim", "0140", "--owner", "b", "--name", "foo").returncode, 1)  # already reserved
        self.assertEqual(self.pn("claim", "0102", "--owner", "b", "--name", "wlan").returncode, 1)  # committed on gpu as 0102-gpu


if __name__ == "__main__":
    unittest.main()
