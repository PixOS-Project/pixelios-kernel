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
        self.assertEqual(r.returncode, 3)
        self.assertIn("overlapping", r.stdout)
        os.remove(os.path.join(self.d, "xnu/patches/0102-wlan.patch")); self.git("rm", "-q", "--cached", "xnu/patches/0102-wlan.patch")
        self.w("xnu/patches/0103-free.patch", "f"); self.git("add", "-A")
        self.assertEqual(self.pn("precommit").returncode, 0)
        self.assertIn("RESERVED by", self.pn("status").stdout.split("\n0103  ")[1])  # reserved for everyone by the commit

    def test_cd_chain_absolute_path_foreign_repo_and_heredoc(self):
        sup = tempfile.mkdtemp(prefix="psuper-"); self.addCleanup(shutil.rmtree, sup)
        n = self.pn("next", "--owner", "x", "--name", "mine").stdout.splitlines()[0]
        # session cwd = some other dir, the command cd's into the kernel repo: the reservation of THAT repo counts
        cmd = "cd %s && cp /tmp/x xnu/patches/%s-mine.patch" % (self.d, n)
        self.assertEqual(self.hook2(sup, cmd), "allow")
        self.assertEqual(self.hook2(sup, "cd %s && cp /tmp/x xnu/patches/%s-other.patch" % (self.d, n)), "deny")
        self.assertEqual(self.hook2(sup, "cp /tmp/x %s/xnu/patches/0190-nores.patch" % self.d), "deny")
        # a repo without tools/patch-numbers is not guarded
        tmp = tempfile.mkdtemp(prefix="ptmp-"); self.addCleanup(shutil.rmtree, tmp)
        subprocess.run(["git", "-C", tmp, "init", "-q"], check=True)
        self.assertEqual(self.hook2(tmp, "echo a > xnu/patches/0190-nores.patch"), "allow")
        # heredoc text that merely mentions a write, and a read that contains the word install
        self.assertEqual(self.hook2(sup, "cat <<'EOF' > /tmp/doc.md\ntee xnu/patches/0120-foo.patch\nEOF"), "allow")
        self.assertEqual(self.hook2(self.d, "grep -n install xnu/patches/0190-nores.patch"), "allow")
        self.assertEqual(self.hook2(self.d, "git format-patch -1 --stdout > xnu/patches/0190-nores.patch"), "deny")

    def hook2(self, cwd, cmd):
        r = subprocess.run([sys.executable, os.path.join(self.d, "tools/patch-guard")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}),
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"

    def test_upstream_tree_writes_are_denied_reads_pass(self):
        """R11 / T-eff6: the fetched upstream XNU tree (xnu-pristine, localbuild/work/<label>/xnu) is never written by the agent."""
        pr = os.path.join(self.d, "art/xnu-pristine/bsd/kern/kern_proc.c")
        wk = os.path.join(self.d, "art/localbuild/work/lab1/xnu/osfmk/arm64/x.c")
        for tool in ("Write", "Edit", "MultiEdit"):
            self.assertEqual(self.hook(tool_name=tool, tool_input={"file_path": pr}), "deny", tool)
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": wk}), "deny")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": os.path.join(self.d, "art/localbuild/work/lab1/build.log")}), "allow")
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": os.path.join(self.d, "art/xnu-pristine-notes.md")}), "allow")
        sup = self.d
        for cmd in ("sed -i 's/a/b/' /h/pixelios-artifacts/xnu-pristine/bsd/kern/kern_proc.c",
                    "echo x >> /h/pixelios-artifacts/xnu-pristine/README",
                    "cp /tmp/f /h/pixelios-artifacts/localbuild/work/lab1/xnu/bsd/f.c",
                    "cd /h/pixelios-artifacts/xnu-pristine && patch -p1 < /tmp/x.diff",
                    "cd /h/pixelios-artifacts/xnu-pristine && git apply /tmp/x.diff",
                    "rm -rf /h/pixelios-artifacts/xnu-pristine/doc",
                    "tee /h/pixelios-artifacts/xnu-pristine/f < /tmp/x"):
            self.assertEqual(self.hook2(sup, cmd), "deny", cmd)
        for cmd in ("grep -rn BTOpenPath /h/pixelios-artifacts/xnu-pristine/bsd",
                    "cat /h/pixelios-artifacts/xnu-pristine/README > /tmp/readme.txt",
                    "cp /h/pixelios-artifacts/xnu-pristine/bsd/f.c /tmp/f.c",
                    "diff -u /h/pixelios-artifacts/xnu-pristine/a.c /tmp/a.c > /tmp/a.diff",
                    "sed -n 1,5p /h/pixelios-artifacts/xnu-pristine/README",
                    "git commit -m 'never sed -i xnu-pristine/x' -- f",
                    "cat <<'EOF' > /tmp/doc.md\nsed -i x xnu-pristine/f\nEOF",
                    "ls /h/pixelios-artifacts/localbuild/work/lab1/build.log"):
            self.assertEqual(self.hook2(sup, cmd), "allow", cmd)
        out = subprocess.run([sys.executable, os.path.join(self.d, "tools/patch-guard")], input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": pr}, "cwd": self.d}),
                             env=self.env, capture_output=True, text=True).stdout
        why = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("Instead:", why); self.assertIn("xnu/patches/", why); self.assertIn("Escape:", why); self.assertIn("R11", why)

    def test_broken_guard_fails_open(self):
        os.remove(os.path.join(self.d, "tools/patch-numbers"))  # the guard's helper is gone: no decision, exit 0
        self.assertEqual(self.hook(tool_name="Write", tool_input={"file_path": os.path.join(self.d, "xnu/patches/0190-x.patch")}), "allow")

    def test_precommit_rename_slug_fix_junk_ref_and_internal_error(self):
        # M2: a rename onto a number another branch holds is refused (git mv shows as R)
        self.git("checkout", "-qb", "wifi"); self.git("mv", "xnu/patches/0101-vsync.patch", "xnu/patches/0102-vsync.patch")
        self.assertEqual(self.pn("precommit").returncode, 3)
        self.git("reset", "-q", "--hard")
        # M3: fixing the slug of your own committed patch is fine
        self.git("checkout", "-q", "gpu"); self.git("mv", "xnu/patches/0102-gpu.patch", "xnu/patches/0102-gpu2.patch"); self.w("xnu/patches/0102-gpu2.patch", "changed"); self.git("add", "-A")
        self.assertEqual(self.pn("precommit").returncode, 0)
        self.git("reset", "-q", "--hard")
        # M1: a stray reservation ref must not break the hook
        blob = self.git("hash-object", "-w", "--stdin").stdout if False else subprocess.run(["git", "-C", self.d, "hash-object", "-w", "--stdin"], input="o\nn\n", env=self.env, capture_output=True, text=True).stdout.strip()
        self.git("update-ref", "refs/patch-numbers/junk", blob)
        self.w("xnu/patches/0103-free.patch", "f"); self.git("add", "-A")
        self.assertEqual(self.pn("precommit").returncode, 0)

    def test_installed_hook_fails_open_but_blocks_a_collision(self):
        self.pn("install-hook")
        hook = os.path.join(self.d, ".git/hooks/pre-commit")
        self.assertNotIn("common-tools", open(hook).read())   # AP-241: the hook must not point at a worktree that can be deleted
        self.git("checkout", "-qb", "wifi2"); self.w("xnu/patches/0102-wlan.patch", "w"); self.git("add", "-A")
        r = subprocess.run(["git", "-C", self.d, "commit", "-qm", "x"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)          # collision with gpu's 0102: blocked
        self.git("reset", "-q", "--hard")
        self.w("tools/patch-numbers", "this is not python (");  # a broken helper: internal error, commit goes through
        self.w("docs.txt", "d"); self.git("add", "docs.txt")
        r = subprocess.run(["git", "-C", self.d, "commit", "-qm", "doc"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_installed_hook_missing_script_is_loud(self):
        self.pn("install-hook")
        os.remove(os.path.join(self.d, "tools/patch-numbers"))
        self.w("docs.txt", "d"); self.git("add", "docs.txt")
        r = subprocess.run(["git", "-C", self.d, "commit", "-qm", "doc"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("patch numbers NOT checked", r.stderr)

    def test_installed_hook_blocks_a_collision_from_a_linked_worktree(self):
        # review patch-hook-toplevel-r1 F2: the real failure case is a commit in a LINKED worktree (hooks are shared through the common dir)
        self.pn("install-hook")
        wt = self.d + "-wt"; self.addCleanup(shutil.rmtree, wt, True)
        self.git("worktree", "add", "-q", "-b", "wifi3", wt)
        with open(os.path.join(wt, "xnu/patches/0102-wlan.patch"), "w") as f: f.write("w")
        subprocess.run(["git", "-C", wt, "add", "-A"], env=self.env, check=True)
        r = subprocess.run(["git", "-C", wt, "commit", "-qm", "x"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stderr); self.assertIn("overlapping", r.stderr + r.stdout)

    def test_claim(self):
        self.assertEqual(self.pn("claim", "0140", "--owner", "a", "--name", "foo").returncode, 0)
        self.assertEqual(self.pn("claim", "0140", "--owner", "b", "--name", "foo").returncode, 1)  # already reserved
        self.assertEqual(self.pn("claim", "0102", "--owner", "b", "--name", "wlan").returncode, 1)  # committed on gpu as 0102-gpu


if __name__ == "__main__":
    unittest.main()
