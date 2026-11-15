#!/usr/bin/env python3
"""Regression test for T-3f7e: tools/gate-request --repo DIR must gate DIR's HEAD on the remote host, not the checkout of the repo that holds the tool.

remote-job gates `${REMOTE_JOB_REPO:-<repo holding the running tool>}`; gate-request used to leave REMOTE_JOB_REPO unset, so `--repo` (a worktree, or a
branch checked out elsewhere) was linted/labelled for one sha while the remote gated another (GATE FAIL labelled with the wrong sha, identity MISMATCH).
Hermetic: fixture repo + stub tools in a tempdir, GATE_REQUEST_DRY=1 (the DRY line carries the remote command), no network, no phone.
Fails on the pre-T-3f7e tool (DRY remote command has no REMOTE_JOB_REPO=<repo>).

Run: python3 -I tools/test_gate_request_repo.py   (prints `RESULT: PASS test_gate_request_repo cases=N` on success)
"""
import os, re, shutil, sys, tempfile, unittest
os.environ.setdefault("GATE_AFFECTED_CHECKED", "1")   # these tests run tools/gate for real: no nested tools/affected-tests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_lint_first_lib import make_fixture, make_tooldir, run

CASES = 0


class GateRequestRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="grp-")
        cls.td = make_tooldir(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def dry(self, name):
        global CASES
        CASES += 1
        root = os.path.join(self.tmp, name)
        make_fixture(root)
        rc, out = run([os.path.join(self.td, "gate-request"), "--repo", root, "--label", "L1"],
                      {"GATE_REQUEST_DRY": "1", "HJ_MARK": os.path.join(self.tmp, "hj.mark")})
        self.assertEqual(rc, 0, out)
        return os.path.realpath(root), out

    def test_remote_command_pins_the_commit_under_test_to_repo(self):
        root, out = self.dry("repoA")
        m = re.search(r"--remote-cmd '([^']*)'", out)
        self.assertIsNotNone(m, out)
        remote = m.group(1)
        self.assertIn("REMOTE_JOB_REPO=%s " % root, remote, remote)
        # the pin must apply to the remote-job call itself (env prefix of that command), not to a different command in the chain
        self.assertRegex(remote, r"REMOTE_JOB_REPO=%s \S*remote-job --where auto gate-pre" % re.escape(root))

    def test_import_reads_the_same_repo_the_remote_job_logged_under(self):
        root, out = self.dry("repoB")
        self.assertIn("gate-import-remote %s/logs/remote-jobs/gate-pre/out %s" % (root, root), out)


if __name__ == "__main__":
    r = unittest.main(verbosity=1, exit=False).result
    if r.wasSuccessful():
        print("RESULT: PASS test_gate_request_repo cases=%d" % CASES)
        sys.exit(0)
    print("RESULT: FAIL test_gate_request_repo failures=%d errors=%d" % (len(r.failures), len(r.errors)))
    sys.exit(1)
