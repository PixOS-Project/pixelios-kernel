#!/usr/bin/env python3
"""Regression test for tools/gate-request --reason and the L2 head refusal (T-cb93; layer rule T-db59, docs/recipes/commands.md, merge-to-trunk.md).

The formal gate runs on the trunk merge SHA only: gate-request refuses any other head (non-merge, not the tip of trunk/main/master) unless --reason "..." is given.
Hermetic: fixture repos in a tempdir with stub tools (tools/gate_lint_first_lib.py), GATE_REQUEST_DRY=1, no network, no phone.
Fails on the pre-T-cb93 tool ("unknown argument '--reason'", and a branch head was accepted without a reason).

Run: python3 -I tools/test_gate_request_reason.py   (prints `RESULT: PASS test_gate_request_reason cases=N` on success)
"""
import os, shutil, subprocess, sys, tempfile, unittest
os.environ.setdefault("GATE_AFFECTED_CHECKED", "1")   # these tests run tools/gate for real: no nested tools/affected-tests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_lint_first_lib import make_fixture, make_tooldir, run

GENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
CASES = 0


def git(root, *a):
    subprocess.run(["git", "-C", root] + list(a), env=GENV, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GateRequestReason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="grr-")
        cls.td = make_tooldir(cls.tmp)
        cls.mark = os.path.join(cls.tmp, "hj.mark")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def repo(self, kind):
        """kind: trunk (the fixture's only commit, branch master), branch (commit past trunk on another branch), merge (a --no-ff merge commit on a branch), detached-trunk (HEAD detached at the trunk tip)."""
        GateRequestReason.n += 1
        root = os.path.join(self.tmp, "r%d" % self.n)
        make_fixture(root)
        git(root, "branch", "-M", "master")
        if kind == "branch":
            git(root, "checkout", "-q", "-b", "feat"); git(root, "commit", "-q", "--allow-empty", "-m", "feat")
        elif kind == "merge":
            git(root, "checkout", "-q", "-b", "feat"); git(root, "commit", "-q", "--allow-empty", "-m", "feat")
            git(root, "checkout", "-q", "-b", "mergebr", "master"); git(root, "commit", "-q", "--allow-empty", "-m", "side")
            git(root, "merge", "-q", "--no-ff", "-m", "merge", "feat")
        elif kind == "detached-trunk":
            git(root, "checkout", "-q", "--detach", "master")
        return root

    def req(self, root, *extra):
        global CASES
        CASES += 1
        return run([os.path.join(self.td, "gate-request"), "--repo", root, "--label", "L1"] + list(extra),
                   {"GATE_REQUEST_DRY": "1", "HJ_MARK": self.mark})

    def test_trunk_head_needs_no_reason(self):
        for kind in ("trunk", "detached-trunk"):
            rc, out = self.req(self.repo(kind))
            self.assertEqual(rc, 0, (kind, out))
            self.assertIn("RESULT: PASS gate-request dry label=L1", out)
            self.assertNotIn("L2 override", out)

    def test_merge_head_needs_no_reason(self):
        rc, out = self.req(self.repo("merge"))
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS gate-request dry", out)

    def test_branch_head_refused_without_reason(self):
        rc, out = self.req(self.repo("branch"))
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate-request head=", out)
        self.assertIn("not-merge-not-trunk", out)
        self.assertNotIn("DRY:", out)
        self.assertFalse(os.path.exists(self.mark), "refusal must not start a gate")

    def test_refusal_prints_the_exact_refused_line_and_formal_flag_is_accepted(self):
        """T-db59 Done-when: `gate-request --formal` on a non-merge head prints the quoted REFUSED line (before the RESULT line); --formal on an L2 head starts as before."""
        refused = 'REFUSED: formal is L2 (merge SHA or nightly); run tools/gate --quick, or --reason "..."'
        for extra in ((), ("--formal",)):
            rc, out = self.req(self.repo("branch"), *extra)
            self.assertEqual(rc, 1, (extra, out))
            self.assertIn(refused, out.splitlines())
            self.assertLess(out.index("REFUSED:"), out.index("RESULT: FAIL gate-request head="))
        rc, out = self.req(self.repo("branch"), "--formal", "--reason", "x")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("REFUSED", out)
        rc, out = self.req(self.repo("merge"), "--formal")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("REFUSED", out)

    def test_branch_head_with_reason_starts_and_echoes_it(self):
        rc, out = self.req(self.repo("branch"), "--reason", "T-xxxx: gate the branch head")
        self.assertEqual(rc, 0, out)
        self.assertIn("L2 override", out)
        self.assertIn("reason=T-xxxx: gate the branch head", out)
        self.assertIn("RESULT: PASS gate-request dry label=L1", out)

    def test_reason_on_a_trunk_head_is_accepted(self):
        rc, out = self.req(self.repo("trunk"), "--reason", "extra")
        self.assertEqual(rc, 0, out)

    def test_empty_or_valueless_reason_is_an_error(self):
        for extra in (["--reason", ""], ["--reason", "   "], ["--reason"]):
            rc, out = self.req(self.repo("branch"), *extra)
            self.assertEqual(rc, 2, (extra, out))
            self.assertIn("RESULT: ERROR gate-request --reason", out)

    def test_dirty_tree_still_refused_with_reason(self):
        root = self.repo("branch")
        with open(os.path.join(root, "x"), "w") as f:
            f.write("x")
        rc, out = self.req(root, "--reason", "r")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate-request dirty=1", out)


if __name__ == "__main__":
    r = unittest.main(verbosity=1, exit=False).result
    if r.wasSuccessful():
        print("RESULT: PASS test_gate_request_reason cases=%d" % CASES)
        sys.exit(0)
    print("RESULT: FAIL test_gate_request_reason failures=%d errors=%d" % (len(r.failures), len(r.errors)))
    sys.exit(1)
