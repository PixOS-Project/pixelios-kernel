#!/usr/bin/env python3
"""Host test of tools/worktree-new (AP-144): fixture main clone + worktrees in a tempdir; never touches the real clones."""
import os, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "worktree-new")


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True, text=True).stdout


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.d = self.t.name
        self.main = os.path.join(d, "main")
        os.makedirs(self.main + "/research/ipsw-x")
        os.makedirs(self.main + "/tools")
        with open(os.path.join(os.path.dirname(HERE), ".gitignore")) as f, open(self.main + "/.gitignore", "w") as g:
            g.write(f.read())   # the repo's real ignore rules: the research/.env/logs LINKS must be ignored, not only directories
        with open(self.main + "/research/ipsw-x/d22ap.adt", "w") as f:
            f.write("adt")
        with open(self.main + "/.env", "w") as f:
            f.write("CAM_HOST=192.0.2.1\n")
        git(self.main, "init", "-q", "-b", "trunk")
        git(self.main, "add", ".gitignore"); git(self.main, "commit", "-qm", "c")
        self.env = dict(os.environ, WORKTREE_ROOT=os.path.join(d, "wts"))

    def tearDown(self):
        self.t.cleanup()

    def run_tool(self, *a, repo=None):
        p = subprocess.run([TOOL, *a, "--repo", repo or self.main], capture_output=True, text=True, env=self.env, timeout=60)
        return p.returncode, p.stdout + p.stderr, (p.stdout.rstrip().splitlines() or [""])[-1]

    def test_pass_links_research_env_and_real_logs(self):
        rc, out, last = self.run_tool("tools/x-y")
        wt = os.path.join(self.d, "wts", "x-y")
        self.assertEqual(rc, 0, out)
        self.assertEqual(last, "RESULT: PASS worktree-new %s branch=tools/x-y base=trunk@%s links=research,.env logs=dir warnings=0" % (wt, git(self.main, "rev-parse", "--short", "trunk").strip()))
        self.assertEqual(os.readlink(wt + "/research"), self.main + "/research")
        self.assertEqual(os.readlink(wt + "/.env"), self.main + "/.env")
        self.assertFalse(os.path.lexists(wt + "/tools/third_party"))   # T-93e6: tracked in git, never linked
        self.assertTrue(os.path.isdir(wt + "/logs") and not os.path.islink(wt + "/logs"))
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip(), "tools/x-y")
        self.assertEqual(git(wt, "status", "--porcelain"), "")   # links are gitignored: the tree stays clean (gate stamp, AP-21)

    def test_links_point_at_main_clone_when_run_from_a_worktree(self):
        self.run_tool("a")
        rc, out, last = self.run_tool("b", repo=os.path.join(self.d, "wts", "a"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.readlink(os.path.join(self.d, "wts", "b", "research")), self.main + "/research")

    def test_link_logs_opt_in(self):
        rc, out, last = self.run_tool("c", "--link-logs")
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.readlink(os.path.join(self.d, "wts", "c", "logs")), self.main + "/logs")
        self.assertIn("links=research,.env,logs logs=linked", last)
        self.assertEqual(git(os.path.join(self.d, "wts", "c"), "status", "--porcelain"), "")

    def test_missing_research_fails_loudly(self):   # AP-144: the worktree exists, the gap is named, exit 1
        os.rename(self.main + "/research", self.main + "/research.away")
        rc, out, last = self.run_tool("d")
        self.assertEqual(rc, 1, out)
        self.assertTrue(last.startswith("RESULT: FAIL worktree-new %s missing: research" % os.path.join(self.d, "wts", "d")), last)
        os.makedirs(self.main + "/research")   # present but without the ADT: still FAIL
        rc, out, last = self.run_tool("e")
        self.assertEqual(rc, 1, out)
        self.assertIn("research/ipsw-x/d22ap.adt does not resolve", last)

    def test_missing_env_warns(self):
        os.remove(self.main + "/.env")
        rc, out, last = self.run_tool("f")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARNING %s/.env is missing" % self.main, out)
        self.assertIn("links=research logs=dir warnings=1", last)

    def test_refusals_change_nothing(self):
        self.run_tool("g")
        for args in (("g",), ("h", "--dir", os.path.join(self.d, "wts", "g")), ("i", "--base", "nope"), ("bad..name",), ("--zzz",), ()):
            rc, out, last = self.run_tool(*args)
            self.assertEqual(rc, 2, (args, out))
            self.assertTrue(last.startswith("RESULT: ERROR worktree-new"), last)
        self.assertNotIn("refs/heads/h", git(self.main, "for-each-ref"))
        self.assertNotIn("refs/heads/i", git(self.main, "for-each-ref"))

    def bare_wt(self, name="raw"):
        wt = os.path.join(self.d, "wts", name)
        git(self.main, "worktree", "add", "-q", "-b", name, wt, "trunk")
        return wt

    def test_link_only_creates_three_links(self):
        wt = self.bare_wt()
        rc, out, last = self.run_tool("--link-only", wt)
        self.assertEqual(rc, 0, out)
        self.assertEqual(last, "RESULT: PASS worktree-new link-only %s links=research,.env skipped=none warnings=0" % wt)
        self.assertEqual(os.readlink(wt + "/research"), self.main + "/research")
        self.assertEqual(os.readlink(wt + "/.env"), self.main + "/.env")

    def test_link_only_skips_existing_loudly(self):
        wt = self.bare_wt()
        os.makedirs(wt + "/research/ipsw-x")
        open(wt + "/research/ipsw-x/d22ap.adt", "w").write("own")
        rc, out, last = self.run_tool("--link-only", wt)
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIPPED %s/research exists, not replaced" % wt, out)
        self.assertIn("skipped=research", last)
        self.assertFalse(os.path.islink(wt + "/research"))
        self.assertEqual(open(wt + "/research/ipsw-x/d22ap.adt").read(), "own")
        rc, out, last = self.run_tool("--link-only", self.d + "/nope")   # not a worktree: ERROR, exit 2
        self.assertEqual(rc, 2, out)

    def hook(self, repo=None):
        shutil.copy(TOOL, self.main + "/tools/worktree-new")   # the hook runs the MAIN clone's copy
        return self.run_tool("install-hook", repo=repo)

    def test_hook_links_a_raw_worktree_add(self):
        rc, out, last = self.hook()
        self.assertEqual(rc, 0, out)
        self.assertTrue(last.startswith("RESULT: PASS worktree-new install-hook "), last)
        wt = self.bare_wt("viahook")
        self.assertEqual(os.readlink(wt + "/research"), self.main + "/research")
        self.assertEqual(os.readlink(wt + "/.env"), self.main + "/.env")
        self.assertEqual(self.hook()[0], 0)   # idempotent over our own hook

    def test_hook_failure_is_loud_but_never_fails_checkout(self):
        self.hook()
        os.rename(self.main + "/research", self.main + "/research.away")
        p = subprocess.run(["git", "-C", self.main, "worktree", "add", "-q", "-b", "bad", self.d + "/wts/bad", "trunk"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("WORKTREE-AUTOLINK FAIL:", p.stderr)

    def test_hook_refuses_foreign_post_checkout(self):
        hf = self.main + "/.git/hooks/post-checkout"
        open(hf, "w").write("#!/bin/sh\necho mine\n")
        rc, out, last = self.hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("is not ours", last)
        self.assertEqual(open(hf).read(), "#!/bin/sh\necho mine\n")

    def test_hook_ignores_non_new_checkout(self):
        self.hook()
        wt = self.bare_wt("existing")
        os.remove(wt + "/research")
        git(wt, "switch", "-q", "-c", "other")
        self.assertFalse(os.path.lexists(wt + "/research"))


if __name__ == "__main__":
    unittest.main()
