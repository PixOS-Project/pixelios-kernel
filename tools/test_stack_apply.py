"""tools/stack-apply (gate step stack-apply, AP-258; review r42-gate-r1 F2): a scratch repo, a local "GitHub" and a scratch ARTIFACTS dir; never touches the real ones.
Rows: applies and PASSes; a patch that does not apply FAILs naming it; no network = loud "STACK-APPLY NOT RUN: no network" + SKIPPED, exit 0, nothing half-cloned;
a reachable remote without the tag = ERROR; the pinned stager source comes from the local mirror (offline run); one log per run.
Run: python3 -I tools/test_stack_apply.py"""
import glob
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent
TAG = "xnu-8796.141.3"


def sh(*a, cwd=None):
    p = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "protocol.file.allow=always", *a], cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(a), p.stderr))
    return p.stdout.strip()


class StackApply(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.t.name).resolve()
        # the fake pristine upstream: one commit, tagged
        self.up = self.d / "upstream"
        self.up.mkdir()
        sh("init", "-q", "-b", "main", str(self.up))
        (self.up / "README").write_text("one\ntwo\n")
        sh("add", "README", cwd=self.up)
        sh("commit", "-qm", "c", cwd=self.up)
        sh("tag", TAG, cwd=self.up)
        self.remote = "file://" + str(self.up)
        # the repo under test: tools/stack-apply + tools/xnu-src-mirror and a stack of one stager and one patch
        self.root = self.d / "repo"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "xnu" / "patches").mkdir(parents=True)
        for n in ("stack-apply", "xnu-src-mirror"):
            shutil.copy(TOOLS / n, self.root / "tools" / n)
        # the commit the stager "fetches" (sha is real: the mirror stores it as refs/pinned/<sha>)
        self.src = self.d / "pinsrc"
        self.src.mkdir()
        sh("init", "-q", "-b", "main", str(self.src))
        (self.src / "f").write_text("pinned\n")
        sh("add", "f", cwd=self.src)
        sh("commit", "-qm", "p", cwd=self.src)
        self.sha = sh("rev-parse", "HEAD", cwd=self.src)
        (self.root / "xnu" / "patches" / "0001-stage-x.sh").write_text(
            "#!/bin/bash\nset -e\nsha=%s\ngit fetch -q --depth 1 https://example.invalid/Pinned \"$sha\"\ngit rev-parse FETCH_HEAD >staged.txt\n" % self.sha)
        self.add_patch("0002-x.patch", "README", "one\ntwo\n", "one\nTWO\n")
        self.art = self.d / "art"
        self.art.mkdir()

    def tearDown(self):
        self.t.cleanup()

    def add_patch(self, name, f, old, new):
        w = self.d / "pw"
        shutil.rmtree(w, ignore_errors=True)
        w.mkdir()
        (w / f).write_text(old)
        sh("init", "-q", cwd=w)
        sh("add", f, cwd=w)
        sh("commit", "-qm", "o", cwd=w)
        (w / f).write_text(new)
        (self.root / "xnu" / "patches" / name).write_text(sh("diff", cwd=w) + "\n")

    def mirror(self):
        """Seed the local mirror the way tools/xnu-src-mirror seed would, but from the local pinned repo (the url in the stager is unreachable)."""
        m = self.art / "git-mirror" / "Pinned.git"
        if not m.is_dir():
            sh("init", "-q", "--bare", str(m))
        sh("--git-dir=" + str(m), "fetch", "-q", str(self.src), self.sha)
        sh("--git-dir=" + str(m), "update-ref", "refs/pinned/" + self.sha, self.sha)

    def run_it(self, remote=None, timeout="20"):
        env = dict(os.environ, ARTIFACTS=str(self.art), STACK_APPLY_REMOTE=remote or self.remote, STACK_APPLY_PROBE_TIMEOUT=timeout,
                   GIT_CONFIG_GLOBAL=str(self.d / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        (self.d / "gitconfig").write_text("[protocol \"file\"]\n\tallow = always\n[user]\n\temail = t@t\n\tname = t\n")
        p = subprocess.run(["bash", str(self.root / "tools" / "stack-apply")], capture_output=True, text=True, env=env, cwd="/", timeout=120)
        out = p.stdout + p.stderr
        return p.returncode, out, ([x for x in out.splitlines() if x.startswith("RESULT:")] or [""])[-1]

    def logs(self):
        return sorted(glob.glob(str(self.art / "scratch" / "stack-apply.*.log")))

    def test_no_network_is_loud_skipped_exit_0_and_leaves_nothing_half_cloned(self):
        rc, out, last = self.run_it(remote="file://" + str(self.d / "does-not-exist"))
        self.assertEqual(rc, 0, out)
        self.assertIn("STACK-APPLY NOT RUN: no network", out)
        self.assertTrue(last.startswith("RESULT: SKIPPED stack-apply NOT RUN: no network"), last)
        self.assertFalse((self.art / "xnu-pristine").exists())
        self.assertEqual(glob.glob(str(self.art / "xnu-pristine.clone.*")), [])
        self.assertEqual(glob.glob(str(self.art / "scratch" / "stack-*")), [str(l) for l in self.logs()])   # no scratch tree or mirror temp left behind

    def test_no_network_timeout_is_also_not_run(self):
        """git ls-remote killed by timeout(1) (rc 124) is a dead network too, not a pass of the stack."""
        fake = self.d / "bin"
        fake.mkdir()
        (fake / "git").write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = ls-remote ] && exec sleep 30; done\nexec /usr/bin/git \"$@\"\n")
        (fake / "git").chmod(0o755)
        os.environ["PATH"], old = str(fake) + os.pathsep + os.environ["PATH"], os.environ["PATH"]
        try:
            rc, out, last = self.run_it(timeout="1")
        finally:
            os.environ["PATH"] = old
        self.assertEqual(rc, 0, out)
        self.assertIn("STACK-APPLY NOT RUN: no network", out)
        self.assertIn("rc=124", out)

    def test_a_failed_clone_leaves_no_half_pristine_behind(self):
        """Reachable network, clone dies half way (fake git leaves a partial target): the shared pristine path must stay absent (the clone goes into a private temp dir)."""
        fake = self.d / "bin"
        fake.mkdir()
        (fake / "git").write_text("#!/bin/sh\nif [ \"$1\" = clone ]; then for a in \"$@\"; do t=$a; done; mkdir -p \"$t/.git\"; echo partial >\"$t/.git/HEAD\"; exit 128; fi\nexec /usr/bin/git \"$@\"\n")
        (fake / "git").chmod(0o755)
        os.environ["PATH"], old = str(fake) + os.pathsep + os.environ["PATH"], os.environ["PATH"]
        try:
            self.mirror()
            rc, out, last = self.run_it()
        finally:
            os.environ["PATH"] = old
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR cannot clone pristine", out)
        self.assertFalse((self.art / "xnu-pristine").exists())
        self.assertEqual(glob.glob(str(self.art / "xnu-pristine.clone.*")), [])

    def test_one_log_per_run(self):
        self.run_it(remote="file://" + str(self.d / "nope"))
        self.run_it(remote="file://" + str(self.d / "nope"))
        self.assertEqual(len(self.logs()), 2, self.logs())

    def test_applies_with_network_and_caches_the_pristine_atomically(self):
        self.mirror()
        rc, out, last = self.run_it()
        self.assertEqual(rc, 0, out)
        self.assertRegex(last, r"^RESULT: PASS 1/1 patches \(\+ 1 stagers\) apply cleanly in order on " + TAG)
        self.assertTrue((self.art / "xnu-pristine" / ".git").is_dir())
        self.assertEqual(glob.glob(str(self.art / "xnu-pristine.clone.*")), [])
        self.assertNotIn("NOT RUN", out)

    def test_a_patch_that_does_not_apply_fails_naming_it(self):
        self.mirror()
        self.add_patch("0003-bad.patch", "README", "zzz\n", "yyy\n")
        rc, out, last = self.run_it()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL", last)
        self.assertIn("0003-bad.patch", out)

    def test_reachable_remote_without_the_tag_is_an_error_not_a_skip(self):
        sh("tag", "-d", TAG, cwd=self.up)
        rc, out, last = self.run_it()
        self.assertEqual(rc, 2, out)
        self.assertIn("has no tag " + TAG, last)
        self.assertNotIn("NOT RUN", out)

    def test_pinned_stager_source_comes_from_the_local_mirror_offline(self):
        """Cached pristine + complete mirror = a full run with the network down: the stager's https fetch is rewritten to the mirror (tools/xnu-src-mirror env)."""
        rc, out, last = self.run_it()                 # networked run, no mirror yet: seeding the unreachable stager url fails, the stager's own fetch fails = ERROR, never a skip
        self.assertEqual(rc, 2, out)
        self.assertIn("stager 0001-stage-x.sh failed", last)
        self.assertTrue((self.art / "xnu-pristine" / ".git").is_dir())   # but the pristine is cached now
        self.mirror()
        rc, out, last = self.run_it(remote="file://" + str(self.d / "does-not-exist"))
        self.assertEqual(rc, 0, out)
        self.assertRegex(last, r"^RESULT: PASS 1/1 patches \(\+ 1 stagers\)")
        self.assertNotIn("NOT RUN", out)

    def test_incomplete_mirror_without_network_is_not_run(self):
        self.mirror()
        self.run_it()                                 # caches the pristine
        shutil.rmtree(self.art / "git-mirror")        # the pinned source is gone again
        rc, out, last = self.run_it(remote="file://" + str(self.d / "does-not-exist"))
        self.assertEqual(rc, 0, out)
        self.assertIn("STACK-APPLY NOT RUN: no network", out)


if __name__ == "__main__":
    unittest.main()
