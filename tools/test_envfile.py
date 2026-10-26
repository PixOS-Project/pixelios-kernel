#!/usr/bin/env python3
"""Host test for tools/envfile.py and the tools/xrun .env pre-check (AP-78): no device, no lock."""
import os, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import envfile


def git(cwd, *a):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


class T(unittest.TestCase):
    def test_worktree_resolves_main_env(self):
        with tempfile.TemporaryDirectory() as d:
            main, wt = os.path.join(d, "main"), os.path.join(d, "wt")
            os.mkdir(main)
            git(main, "init", "-q")
            git(main, "commit", "-q", "--allow-empty", "-m", "x")
            put(os.path.join(main, ".env"), "CAM_HOST=1.2.3.4\n")
            git(main, "worktree", "add", "-q", "-b", "b", wt)
            self.assertFalse(os.path.exists(os.path.join(wt, ".env")))
            os.environ.pop("PIXOS_ENV_FILE", None)
            self.assertEqual(os.path.realpath(envfile.find(wt)), os.path.realpath(os.path.join(main, ".env")))
            self.assertEqual(envfile.load(wt)["CAM_HOST"], "1.2.3.4")
            put(os.path.join(wt, ".env"), "CAM_HOST=own\n")   # the worktree's own file wins
            self.assertEqual(envfile.load(wt)["CAM_HOST"], "own")

    def test_xrun_fails_fast_before_lock(self):
        with tempfile.TemporaryDirectory() as d:
            envf, lock, kern = (os.path.join(d, n) for n in ("env", "phone.lock", "kern"))
            put(envf, "OTHER=1\n")
            put(kern, "x")
            e = {k: v for k, v in os.environ.items() if k != "CAM_HOST"}
            e.update(PIXOS_ENV_FILE=envf, PHONE_LOCK_DIR=lock)
            before = set(os.listdir(os.path.join(REPO, "logs", "runs"))) if os.path.isdir(os.path.join(REPO, "logs", "runs")) else set()
            r = subprocess.run([os.path.join(TOOLS, "xrun"), "--kernel", kern], env=e, capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertRegex(r.stdout.splitlines()[-1], r"^RESULT: ERROR missing CAM_HOST")
            self.assertFalse(os.path.exists(lock))
            after = set(os.listdir(os.path.join(REPO, "logs", "runs"))) if os.path.isdir(os.path.join(REPO, "logs", "runs")) else set()
            self.assertEqual(before, after)   # no run dir: it failed before the build

    def test_require_pass(self):
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, "env")
            put(envf, "CAM_HOST=h\n")
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "envfile.py"), "require", "CAM_HOST"],
                               env=dict(os.environ, PIXOS_ENV_FILE=envf), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("h\n", r.stdout.replace("has CAM_HOST", ""))


if __name__ == "__main__":
    unittest.main()
