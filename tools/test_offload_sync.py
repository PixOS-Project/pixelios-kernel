#!/usr/bin/env python3
"""Tests of tools/offload-sync (TP-055): temp lab-host repos, a fake ssh that runs the remote command locally (env OFFLOAD_SYNC_SSH), OFFLOAD_ROOT in a temp dir.

  python3 -I tools/test_offload_sync.py            the suite, then the mutants (MUTANTS below: each is a text edit of a COPY of the tool and the suite must FAIL on it)
  OFFLOAD_SYNC_TOOL=PATH python3 -I ...            run the suite against another copy (what the mutant pass does)
Rows: sync from scratch, branch move/forced rewrite/new/deletion, dirty tracked / untracked / race at apply, ignored files not copied, --check PASS/FAIL/branch-only diff,
--dry-run writes nothing, unreachable + hung ssh, free-space guard, lock overlap and background, writes only under OFFLOAD_ROOT, foreign dir, branch in another worktree.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.environ.get("OFFLOAD_SYNC_TOOL") or os.path.join(HERE, "offload-sync")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
REPOS = {"super": ".", "kernel": "pixos-project/pixelios-kernel", "userland": "pixos-project/pixelios-userland", "site": "pixos-project/pixelios-site", "upstream": "pixos-project/upstream"}
BRANCH = {"super": "main", "kernel": "trunk", "userland": "main", "site": "chore/standalone", "upstream": "main"}

FAKESSH = r'''#!/bin/bash
# fake ssh: the "remote" command runs locally. OFFLOAD_FAKE_DOWN: connection refused (rc 255); OFFLOAD_FAKE_HANG: never answers;
# OFFLOAD_FAKE_DIRTY_FILE: the apply script finds that file appended to just before it runs (a race after the probe).
[ -z "${OFFLOAD_FAKE_DOWN:-}" ] || { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
[ -z "${OFFLOAD_FAKE_HANG:-}" ] || { sleep 20; exit 255; }
while [ $# -gt 0 ]; do case $1 in -o|-p|-F|-i|-l) shift 2 ;; --) shift; break ;; -*) shift ;; *) break ;; esac; done
shift
if [ "$*" = "bash -s" ]; then
	s=$(cat)
	if [[ $s == *GAP-DIRTY* && -n "${OFFLOAD_FAKE_DIRTY_FILE:-}" ]]; then echo race >>"$OFFLOAD_FAKE_DIRTY_FILE"; fi
	printf '%s\n' "$s" | bash -s
else exec bash -c "$*"; fi
'''


def rd(path):
    with open(path) as f:
        return f.read()


def git(path, *a, check=True):
    r = subprocess.run(["git", "-C", path] + list(a), capture_output=True, text=True, env=GIT_ENV)
    if check and r.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (a, path, r.stderr))
    return r.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="offload-sync-test-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.src = os.path.join(self.t, "lab-host")
        self.offload_host = os.path.join(self.t, "offload-host")
        self.root = os.path.join(self.offload_host, "pixos")
        self.log = os.path.join(self.t, "log")
        os.makedirs(self.offload_host)
        self.fake = os.path.join(self.t, "fakessh")
        with open(self.fake, "w") as f:
            f.write(FAKESSH)
        os.chmod(self.fake, 0o755)
        for n, rel in REPOS.items():
            p = self.hp(n)
            os.makedirs(p)
            git(p, "init", "-q", "-b", BRANCH[n])
            self.write(p, "f", n + "\n")
            self.write(p, ".gitignore", "ignored\nlogs/\n.env\n" + ("pixos-project/\n" if n == "super" else ""))
            self.write(p, "ignored", "SECRET-" + n + "\n")
            git(p, "add", "f", ".gitignore")
            git(p, "commit", "-qm", "init")
        git(self.hp("super"), "branch", "wip/x")
        git(self.hp("kernel"), "branch", "acc/q1")
        self.env = dict(os.environ, OFFLOAD_SYNC_SRC=self.src, OFFLOAD_ROOT=self.root, OFFLOAD_SYNC_SSH=self.fake, OFFLOAD_SYNC_LOGDIR=self.log, OFFLOAD_MIN_FREE_GB="0",
                        OFFLOAD_SYNC_TIMEOUT="30", OFFLOAD_OFFLINE_FILE=os.path.join(self.root, "no-offline-file"), OFFLOAD_CONF=os.path.join(HERE, "offload-host.conf.example"), GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        for k in ("OFFLOAD_FAKE_DOWN", "OFFLOAD_FAKE_HANG", "OFFLOAD_FAKE_DIRTY_FILE", "OFFLOAD_SYNC_REPOS"):
            self.env.pop(k, None)

    def hp(self, n):
        return self.src if n == "super" else os.path.join(self.src, REPOS[n])

    def sp(self, n):
        return self.root if n == "super" else os.path.join(self.root, REPOS[n])

    @staticmethod
    def write(p, name, text):
        with open(os.path.join(p, name), "w") as f:
            f.write(text)

    def commit(self, n, text="x\n", name="f", msg="c"):
        self.write(self.hp(n), name, text)
        git(self.hp(n), "add", name)
        git(self.hp(n), "commit", "-qm", msg)
        return git(self.hp(n), "rev-parse", "HEAD")

    def run_tool(self, *args, env=None, timeout=60):
        e = dict(self.env)
        e.update(env or {})
        r = subprocess.run([sys.executable, "-I", TOOL] + list(args), capture_output=True, text=True, env=e, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def sync(self, *a, **k):
        return self.run_tool(*a, **k)

    def result(self, out):
        return [l for l in out.splitlines() if l.startswith("RESULT:")][-1]

    def head(self, n, where):
        return git(self.hp(n) if where == "h" else self.sp(n), "rev-parse", "HEAD")


class SyncTests(Base):
    def test_sync_from_scratch_check_pass_and_ignored_not_copied(self):
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS offload-sync repos_ok=5 gaps=0", out)
        for n in REPOS:
            self.assertEqual(self.head(n, "h"), self.head(n, "s"), n)
            self.assertEqual(git(self.sp(n), "symbolic-ref", "--short", "HEAD"), BRANCH[n])
            self.assertTrue(os.path.exists(os.path.join(self.sp(n), "f")))
            self.assertFalse(os.path.exists(os.path.join(self.sp(n), "ignored")), "gitignored file copied to offload-host in %s" % n)
        self.assertEqual(git(self.sp("super"), "branch", "--format=%(refname:short)").split(), ["main", "wip/x"])
        self.assertEqual(sorted(git(self.sp("kernel"), "branch", "--format=%(refname:short)").split()), ["acc/q1", "trunk"])
        self.assertEqual(git(self.sp("super"), "status", "--porcelain"), "")
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.result(out).split()[1], "PASS")
        for n in REPOS:
            self.assertRegex(out, r"%s lab-host=%s offload-host=%s ok branches_diff=0" % (n, self.head(n, "h")[:12], self.head(n, "s")[:12]))

    def test_failing_pre_push_hook_does_not_block_the_mirror(self):
        # R66 public-push-guard calls any url with "pixos-project/" public and may exit 1 (or scan for minutes): the private offload-host mirror must not run it
        for n in REPOS:
            h = os.path.join(self.hp(n), ".git", "hooks", "pre-push")
            self.write(os.path.dirname(h), "pre-push", "#!/bin/sh\necho GUARD-REFUSED >&2\nexit 1\n")
            os.chmod(h, 0o755)
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("GUARD-REFUSED", out)
        self.assertEqual(self.head("kernel", "h"), self.head("kernel", "s"))

    def test_shallow_lab_clone_is_mirrored(self):
        # upstream/ at the lab-host is a shallow clone; git refuses a shallow push unless the offload-host repo has receive.shallowUpdate
        up = self.hp("upstream")
        for i in range(3):
            self.commit("upstream", "v%d\n" % i)
        sh = os.path.join(self.t, "shallow")
        git(self.t, "clone", "-q", "--depth", "1", "file://" + up, sh)
        shutil.rmtree(up)
        shutil.move(sh, up)
        self.assertTrue(os.path.exists(os.path.join(up, ".git", "shallow")))
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.head("upstream", "h"), self.head("upstream", "s"))

    def test_idempotent(self):
        self.assertEqual(self.sync()[0], 0)
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("moved=0 created=0 deleted=0", out)

    def test_branch_move_new_and_forced_rewrite(self):
        self.sync()
        new = self.commit("kernel", "moved\n")
        git(self.hp("kernel"), "branch", "wip/new")
        git(self.hp("super"), "checkout", "-q", "wip/x")
        self.commit("super", "xx\n", msg="on x")
        git(self.hp("super"), "checkout", "-q", "main")
        git(self.hp("super"), "commit", "-q", "--amend", "-m", "rewritten")   # forced rewrite of main
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.head("kernel", "s"), new)
        self.assertEqual(rd(os.path.join(self.sp("kernel"), "f")), "moved\n")
        self.assertEqual(git(self.sp("kernel"), "rev-parse", "wip/new"), new)
        self.assertEqual(self.head("super", "s"), self.head("super", "h"))
        self.assertEqual(git(self.sp("super"), "rev-parse", "wip/x"), git(self.hp("super"), "rev-parse", "wip/x"))
        self.assertEqual(self.run_tool("--check")[0], 0)

    def test_forced_rewrite_is_noted(self):
        self.sync()
        self.commit("userland", "a\n")
        self.sync()
        git(self.hp("userland"), "reset", "-q", "--hard", "HEAD~1")
        self.commit("userland", "b\n")   # diverged
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("OFFLOAD-HOST NOTE: userland main old", out)
        self.assertEqual(self.head("userland", "s"), self.head("userland", "h"))

    def test_deletion_and_checked_out_branch_change(self):
        self.sync()
        git(self.hp("super"), "branch", "-D", "wip/x")
        git(self.hp("kernel"), "checkout", "-q", "-b", "other")
        git(self.hp("kernel"), "branch", "-D", "trunk")   # offload-host has trunk checked out: lab-host moved to another branch
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("wip/x", git(self.sp("super"), "branch", "--format=%(refname:short)").split())
        self.assertEqual(sorted(git(self.sp("kernel"), "branch", "--format=%(refname:short)").split()), ["acc/q1", "other"])
        self.assertEqual(git(self.sp("kernel"), "symbolic-ref", "--short", "HEAD"), "other")
        self.assertEqual(self.run_tool("--check")[0], 0)

    def test_dirty_tracked_tree_untouched_others_sync(self):
        self.sync()
        self.write(self.sp("kernel"), "f", "LOCAL EDIT\n")
        before = self.head("kernel", "s")
        self.commit("kernel", "hl\n")
        sup = self.commit("super", "hl\n")
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: kernel dirty, not updated", out)
        self.assertEqual(self.result(out).split()[1], "FAIL")
        self.assertEqual(rd(os.path.join(self.sp("kernel"), "f")), "LOCAL EDIT\n")
        self.assertEqual(self.head("kernel", "s"), before)
        self.assertEqual(self.head("super", "s"), sup, "the other repos must still sync")
        self.assertNotIn(self.head("kernel", "h"), git(self.sp("kernel"), "for-each-ref", "refs/pixos-sync"), "nothing is pushed into a dirty repo")
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"kernel lab-host=\w+ offload-host=\w+ DIRTY")

    def test_dirty_untracked_nonignored_vs_ignored(self):
        self.sync()
        self.write(self.sp("site"), "ignored", "mine\n")   # ignored: not dirty
        self.commit("site", "n\n")
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertEqual(rd(os.path.join(self.sp("site"), "ignored")), "mine\n")
        self.write(self.sp("upstream"), "scratch.txt", "x\n")   # untracked, not ignored: dirty
        self.commit("upstream", "n\n")
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: upstream dirty, not updated", out)
        self.assertTrue(os.path.exists(os.path.join(self.sp("upstream"), "scratch.txt")))
        self.assertNotEqual(self.head("upstream", "s"), self.head("upstream", "h"))
        self.assertNotIn(self.head("upstream", "h"), git(self.sp("upstream"), "for-each-ref", "refs/pixos-sync"), "nothing is pushed into a dirty repo")

    def test_dirty_never_deletes_checked_out_branch(self):
        self.sync()
        self.write(self.sp("super"), "f", "LOCAL\n")
        git(self.hp("super"), "checkout", "-q", "wip/x")
        git(self.hp("super"), "branch", "-D", "main")   # offload-host has main checked out and dirty
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertEqual(git(self.sp("super"), "symbolic-ref", "--short", "HEAD"), "main")
        self.assertTrue(git(self.sp("super"), "rev-parse", "--verify", "refs/heads/main"))
        self.assertEqual(rd(os.path.join(self.sp("super"), "f")), "LOCAL\n")

    def test_dirty_race_after_probe_caught_by_apply(self):
        self.sync()
        self.commit("userland", "hl\n")
        victim = os.path.join(self.sp("userland"), "f")
        before = self.head("userland", "s")
        rc, out = self.sync(env={"OFFLOAD_FAKE_DIRTY_FILE": victim, "OFFLOAD_SYNC_REPOS": "userland=pixos-project/pixelios-userland"})
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: userland dirty, not updated", out)
        self.assertEqual(self.head("userland", "s"), before)
        self.assertIn("race", rd(victim))

    def test_other_offload_worktree_branch_is_a_gap_not_moved(self):
        self.sync()
        git(self.sp("super"), "worktree", "add", "-q", os.path.join(self.t, "wt"), "wip/x")
        old = git(self.sp("super"), "rev-parse", "wip/x")
        new = self.commit("super", "w\n")
        git(self.hp("super"), "branch", "-f", "wip/x", new)   # a side branch moves while offload-host has it checked out elsewhere
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: super wip/x is checked out in another offload-host worktree, branch not moved", out)
        self.assertEqual(git(self.sp("super"), "rev-parse", "wip/x"), old)
        self.assertEqual(self.head("super", "s"), new, "the checked-out branch still follows the lab-host")
        git(self.hp("super"), "checkout", "-q", "wip/x")   # now the lab-host HEAD is the branch offload-host has in the other worktree
        git(self.sp("super"), "worktree", "remove", "--force", os.path.join(self.t, "wt"))
        git(self.sp("super"), "worktree", "add", "-q", "--detach", os.path.join(self.t, "wt2"), "main")
        git(self.sp("super"), "checkout", "-q", "--detach")
        git(self.sp("super"), "worktree", "remove", "--force", os.path.join(self.t, "wt2"))
        git(self.sp("super"), "worktree", "add", "-q", os.path.join(self.t, "wt3"), "wip/x")
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: super wip/x is checked out in another offload-host worktree, repo not updated", out)

    def test_foreign_directory_not_touched(self):
        os.makedirs(self.sp("site"))
        self.write(self.sp("site"), "mine.txt", "keep\n")
        os.makedirs(self.root, exist_ok=True)
        rc, out = self.sync()
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: site", out)
        self.assertIn("not a git repo", out)
        self.assertEqual(rd(os.path.join(self.sp("site"), "mine.txt")), "keep\n")

    def test_work_dir_in_root_is_not_foreign_and_not_dirty(self):
        # R64: remote-job keeps $OFFLOAD_ROOT/work inside the super clone; created before the first sync it must not make the root "foreign", after it must not make it dirty
        os.makedirs(os.path.join(self.root, "work", "mut-cache-root"))
        self.write(os.path.join(self.root, "work"), "env.sh", "keep\n")
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertEqual(git(self.sp("super"), "status", "--porcelain"), "")
        self.assertEqual(rd(os.path.join(self.root, "work", "env.sh")), "keep\n")
        self.write(os.path.join(self.root, "work"), "later.txt", "x\n")
        self.commit("super")
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 0, out)
        self.assertEqual(rd(os.path.join(self.root, "work", "later.txt")), "x\n")

    def test_check_fail_when_only_a_branch_differs(self):
        self.sync()
        git(self.sp("super"), "branch", "-f", "wip/x", "HEAD~0")
        self.commit("super", "z\n")
        self.sync()
        git(self.sp("super"), "update-ref", "refs/heads/wip/x", git(self.sp("super"), "rev-parse", "HEAD"))   # same head, a side branch moved by hand
        git(self.hp("super"), "branch", "-f", "wip/x", git(self.hp("super"), "rev-parse", "HEAD~1"))
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"super lab-host=(\w+) offload-host=\1 DIFF branches_diff=1")
        self.assertEqual(self.result(out).split()[1], "FAIL")

    def test_check_missing_and_no_write(self):
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"super lab-host=\w+ offload-host=none MISSING")
        self.assertFalse(os.path.exists(self.root), "--check must not create anything")

    def test_dry_run_writes_nothing(self):
        rc, out = self.run_tool("--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry-run super: would create", out)
        self.assertFalse(os.path.exists(self.root))
        self.sync()
        self.commit("kernel", "d\n")
        before = self.head("kernel", "s")
        rc, out = self.run_tool("--dry-run")
        self.assertIn("dry-run kernel: would push", out)
        self.assertEqual(self.head("kernel", "s"), before)

    def test_unreachable_is_loud_and_never_hangs(self):
        for mode in ([], ["--check"], ["--dry-run"]):
            rc, out = self.run_tool(*mode, env={"OFFLOAD_FAKE_DOWN": "1"})
            self.assertEqual(rc, 1, out)
            self.assertIn("OFFLOAD-HOST GAP: unreachable", out)
            self.assertEqual(self.result(out).split()[1], "FAIL")
        self.assertFalse(os.path.exists(self.root))
        t0 = time.time()
        rc, out = self.run_tool("--check", env={"OFFLOAD_FAKE_HANG": "1", "OFFLOAD_SYNC_TIMEOUT": "2"}, timeout=15)
        self.assertEqual(rc, 1, out)
        self.assertIn("OFFLOAD-HOST GAP: unreachable (ssh timed out after 2s)", out)
        self.assertLess(time.time() - t0, 12)

    def test_free_space_guard(self):
        rc, out = self.run_tool(env={"OFFLOAD_MIN_FREE_GB": "999999999"})
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"OFFLOAD-HOST GAP: \S+ \d+G free < 999999999G")
        self.assertEqual(self.result(out).split()[1], "FAIL")
        self.assertFalse(os.path.exists(self.root), "nothing may be written below the limit")
        rc, out = self.run_tool("--dry-run", env={"OFFLOAD_MIN_FREE_GB": "999999999"})
        self.assertEqual(rc, 1, out)
        self.assertIn("G free <", out)

    def test_config_is_checked_and_nothing_outside_root(self):
        side = os.path.join(self.offload_host, "other")
        os.makedirs(side)
        self.write(side, "keep", "k\n")
        for bad in ("/", "/mnt", "relative/x", "/tmp/../etc/x", "/tmp/a b/c"):
            rc, out = self.run_tool(env={"OFFLOAD_ROOT": bad})
            self.assertEqual(rc, 2, (bad, out))
            self.assertIn("RESULT: ERROR", out)
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(sorted(os.listdir(self.offload_host)), ["other", "pixos"])
        self.assertEqual(os.listdir(side), ["keep"])
        self.assertEqual(self.run_tool("--bogus")[0], 2)
        self.assertEqual(self.run_tool("--repo", "nope")[0], 2)


class BackgroundTests(Base):
    def wait_for(self, pred, secs=20):
        t0 = time.time()
        while time.time() - t0 < secs:
            if pred():
                return True
            time.sleep(0.2)
        return False

    def logtext(self):
        try:
            return rd(os.path.join(self.log, "offload-sync.log"))
        except OSError:   # lint-silent: expected no log yet means an empty log text, the assertions then fail with it
            return ""

    def test_background_detaches_syncs_and_logs(self):
        t0 = time.time()
        rc, out = self.run_tool("--background")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "")
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(self.wait_for(lambda: "RESULT: PASS offload-sync" in self.logtext()), self.logtext())
        self.assertEqual(self.head("kernel", "h"), self.head("kernel", "s"))

    def test_no_offload_host_configured_is_a_loud_local_only_skip(self):
        # T-dd93: no host in the config and none in the environment -> not a dependency, but never silent (R8)
        noh = {"OFFLOAD_CONF": "none", "OFFLOAD_HOST": "", "REMOTE_JOB_HOST": "", "OFFLOAD_ROOT": ""}
        for mode in ([], ["--check"], ["--dry-run"]):
            rc, out = self.run_tool(*mode, env=noh)
            self.assertEqual(rc, 0, out)
            self.assertIn("OFFLOAD GAP: no offload host configured", out)
            self.assertEqual(self.result(out).split()[1], "PASS")
        self.assertFalse(os.path.exists(self.root))
        rc, out = self.run_tool("--background", env=noh)
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.wait_for(lambda: "OFFLOAD GAP: no offload host configured" in self.logtext()), self.logtext())

    def test_use_sync_switch_skips_the_mirror_loudly_and_other_uses_do_not(self):
        # per-use switch (web board): the `<label>:sync` row (or the master row) = nothing is sent; the `:build` row does not matter
        off = os.path.join(self.t, "off.list")
        for row, skipped in (("offload-host:sync\tsync paused\n", True), ("offload-host\tall off\n", True), ("offload-host:build\tb\n", False)):
            with open(off, "w") as f:
                f.write(row)
            env = {"OFFLOAD_OFFLINE_FILE": off}
            rc, out = self.run_tool(env=env)
            self.assertEqual(rc, 0, out)
            self.assertEqual("OFFLOAD OFF: use=sync" in out, skipped, row + out)
            self.assertEqual(os.path.exists(self.root), not skipped, row)
            if skipped:
                self.assertIn("RESULT: PASS offload-sync skipped (OFFLOAD OFF use=sync)", out)
            shutil.rmtree(self.offload_host, True)
            os.makedirs(self.offload_host)
        with open(off, "w") as f:
            f.write("offload-host:sync\tsync paused\n")
        rc, out = self.run_tool("--background", env={"OFFLOAD_OFFLINE_FILE": off})
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.wait_for(lambda: "OFFLOAD OFF: use=sync" in self.logtext()), self.logtext())
        self.assertFalse(os.path.exists(self.root))

    def test_host_user_root_come_from_the_config_file(self):
        d = tempfile.mkdtemp(prefix="offload-conf-")
        try:
            conf = os.path.join(d, "host.conf")
            with open(conf, "w") as f:
                f.write("OFFLOAD_HOST=h.example\nOFFLOAD_USER=u\nOFFLOAD_ROOT=/srv/x/pixos\nBOGUS=1\n")
            code = "import sys,os;sys.path.insert(0,%r);import offload_conf;print(offload_conf.load(%r)[0])" % (HERE, HERE)
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=dict(os.environ, OFFLOAD_CONF=conf, OFFLOAD_HOST="", OFFLOAD_USER="", OFFLOAD_ROOT=""))
            self.assertIn("'OFFLOAD_HOST': 'h.example'", r.stdout)
            self.assertIn("'OFFLOAD_USER': 'u'", r.stdout)
            self.assertIn("'OFFLOAD_ROOT': '/srv/x/pixos'", r.stdout)
            self.assertIn("OFFLOAD GAP: %s: unknown key BOGUS ignored" % conf, r.stderr)
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=dict(os.environ, OFFLOAD_CONF=conf, OFFLOAD_HOST="env.example", OFFLOAD_USER="", OFFLOAD_ROOT=""))
            self.assertIn("'OFFLOAD_HOST': 'env.example'", r.stdout)   # environment beats the file
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_background_failure_never_fails_and_next_check_shows_it(self):
        rc, out = self.run_tool("--background", env={"OFFLOAD_FAKE_DOWN": "1"})
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.wait_for(lambda: "RESULT: FAIL" in self.logtext()), self.logtext())
        self.assertIn("OFFLOAD-HOST GAP: unreachable", self.logtext())
        self.assertEqual(self.sync()[0], 0)   # a foreground sync PASS also lands in the log and clears the note
        rc, out = self.run_tool("--check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("last background sync FAILED", out)
        os.makedirs(self.log, exist_ok=True)
        with open(os.path.join(self.log, "offload-sync.log"), "a") as f:
            f.write("2026-10-31T00:00:00Z RESULT: FAIL offload-sync offload-host unreachable\n")
        rc, out = self.run_tool("--check")
        self.assertIn("OFFLOAD-HOST NOTE: last background sync FAILED", out)

    def test_lock_overlap(self):
        import fcntl
        os.makedirs(self.log, exist_ok=True)
        fd = os.open(os.path.join(self.log, "offload-sync.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            rc, out = self.run_tool(env={"OFFLOAD_SYNC_LOCK_WAIT": "1"})
            self.assertEqual(rc, 1, out)
            self.assertIn("OFFLOAD-HOST GAP: another offload-sync holds", out)
            self.assertFalse(os.path.exists(self.root), "a run that lost the lock must not sync")
            rc, out = self.run_tool("--background")
            self.assertEqual(rc, 0, out)
            self.assertTrue(self.wait_for(lambda: os.path.exists(os.path.join(self.log, "offload-sync.again"))), self.logtext())
            self.assertIn("queued", self.logtext())
            self.assertFalse(os.path.exists(self.root))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(self.log, "offload-sync.again")), "the next run consumes the queued flag")


# Each mutant: (name, needle, replacement) on the tool text; the suite must FAIL on it. Needles must occur exactly once (checked).
MUTANTS = [
    ("python dirty guard dropped (pushes into a dirty repo)", 'if p["exists"] == "1" and p["dirty"] != "0":', "if False:"),
    ("apply-time dirty guard dropped", '[ -z "$st" ] || { echo "GAP-DIRTY"; exit 10; }', "true"),
    ("untracked files ignored by the dirty probe", "git -C \"$P\" status --porcelain 2>&1", "git -C \"$P\" status --porcelain --untracked-files=no 2>&1"),
    ("deletion dropped", 'git update-ref -d "$r" && deleted=$((deleted+1))', 'true && deleted=$((deleted+1))'),
    ("--prune dropped", '"--force", "--prune", url', '"--force", url'),
    ("forced rewrites refused", '"--no-verify", "--force", "--prune", url, "+refs/heads/*:refs/pixos-sync/heads/*"', '"--no-verify", "--prune", url, "refs/heads/*:refs/pixos-sync/heads/*"'),
    ("ignored files mirrored", '    if applied:\n        out.line("sync %s: %s" % (name, applied))', '    run_local(["cp", "-an", path + "/.", dest + "/"])\n    if applied:\n        out.line("sync %s: %s" % (name, applied))'),
    ("checked-out branch never updated", 'git reset -q --hard "$T" || { echo "ERR reset --hard $T"; exit 7; }', "true"),
    ("--check always PASS", '    out.line("RESULT: %s check offload-sync repos_not_ok=%d root=%s" % ("PASS" if bad == 0 else "FAIL", bad, cfg.root))\n    return 0 if bad == 0 else 1',
     '    out.line("RESULT: PASS check offload-sync repos_not_ok=%d root=%s" % (bad, cfg.root))\n    return 0'),
    ("--check blind to a dirty tree", '                if p["dirty"] != "0":\n                    st = "DIRTY"', '                if False:\n                    st = "DIRTY"'),
    ("--check blind to side-branch differences", 'or p["branch"] != hb or nd:', 'or p["branch"] != hb:'),
    ("ssh failure (rc 255) not treated as unreachable", "if r.returncode == 255:", "if False:"),
    ("no ssh timeout", "timeout=timeout or self.timeout)", "timeout=None)"),
    ("free-space guard dropped", "if g < cfg.min_free:\n        out.gap", "if False:\n        out.gap"),
    ("lock not taken", "                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n                got = True", "                got = True"),
    ("foreign directory adopted", 'if p["exists"] != "1" and p["foreign"] == "1":', "if False:"),
    ("branch in another worktree moved anyway", 'if [ -n "${wt[$b]:-}" ]; then echo "GAP-WT $b is checked out in another offload-host worktree, branch not moved"; rc=11; continue; fi', ":"),
    ("work dir not excluded from the super clone", 'grep -qxF /%s/ \\"$x\\" 2>/dev/null || echo /%s/ >>\\"$x\\"', 'true'),
    ("work dir counts as foreign", '"$(ls -A "$P" 2>/dev/null | grep -vx %s)"', '"$(ls -A "$P" 2>/dev/null)"'),
    ("pre-push guard hook runs on the mirror push", '"-q", "--no-verify", "--force"', '"-q", "--force"'),
    ("shallow updates not enabled on offload-host", "config receive.shallowUpdate true", "config receive.shallowUpdate false"),
    ("forced-rewrite note dropped", 'git merge-base --is-ancestor "$oldb" "$T" || echo "NOTE', 'git merge-base --is-ancestor "$oldb" "$T" || echo "NOTX'),
]


def run_mutants():
    src = rd(TOOL)
    killed = 0
    survivors = []
    me = os.path.abspath(__file__)
    for name, old, new in MUTANTS:
        if src.count(old) != 1:
            print("MUTANT NEEDLE %s: %r occurs %d times" % ("MISSING" if src.count(old) == 0 else "MULTI", name, src.count(old)))
            survivors.append(name)
            continue
        d = tempfile.mkdtemp(prefix="offload-sync-mut-")
        try:
            p = os.path.join(d, "offload-sync")
            shutil.copy(os.path.join(HERE, "offload_conf.py"), d)   # the tool imports its config loader from its own directory
            with open(p, "w") as f:
                f.write(src.replace(old, new))
            os.chmod(p, 0o755)
            r = subprocess.run([sys.executable, "-I", me], capture_output=True, text=True, env=dict(os.environ, OFFLOAD_SYNC_TOOL=p, OFFLOAD_SYNC_NO_MUTANTS="1"), timeout=900)
            if r.returncode != 0:
                killed += 1
                print("killed   %s" % name)
            else:
                survivors.append(name)
                print("SURVIVED %s" % name)
        finally:
            shutil.rmtree(d, True)
    print("mutants killed %d/%d" % (killed, len(MUTANTS)))
    return survivors


if __name__ == "__main__":
    res = unittest.main(exit=False, argv=[sys.argv[0]])
    ok = res.result.wasSuccessful()
    if ok and not os.environ.get("OFFLOAD_SYNC_NO_MUTANTS") and not os.environ.get("OFFLOAD_SYNC_TOOL"):
        ok = not run_mutants()
    print("RESULT: %s offload-sync tests" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)
