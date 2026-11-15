#!/usr/bin/env python3
"""T-1386: a gate refuses a branch whose gate tooling (check-all.sh, heavy-job, ...) is behind trunk, so a gate fix reaches every branch at once.

Scenario (T-2172, ba1296e6): trunk got a new check-all.sh (new GATE FAIL line format listing `failed:` names); a branch cut earlier still carries the old copy, so its gate
printed the old line and the worker re-gated over a misread. tools/gate-trunk-check compares the committed gate tooling of HEAD with trunk's (merge-base decides whether the
branch or trunk moved the file) and tools/gate / tools/gate-request refuse with `merge trunk first: gate tooling behind trunk by <sha>`.
Hermetic: fixture repos + stub tools in a tempdir, no network, no phone, no real gate. MUTANTS: a checker that passes the old copy is caught by the same cases.
Run: python3 -I tools/test_gate_trunk_check.py   (prints `RESULT: PASS test_gate_trunk_check cases=N` on success)
"""
import os, shutil, subprocess, sys, tempfile, unittest
os.environ.setdefault("GATE_AFFECTED_CHECKED", "1")   # these tests run tools/gate for real: no nested tools/affected-tests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_lint_first_lib import TOOLS, make_fixture, write

CASES = 0
ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
for _v in ("GATE_TOOLING_OVERRIDE", "GATE_TOOLING_FILES", "GATE_TRUNK_REFS", "GATE_DRY", "GATE_REQUEST_DRY"):
    ENV.pop(_v, None)
OLD = "#!/bin/bash\nfinish() { echo \"$1\"; exit $2; }\nfinish 'GATE FAIL abc unit census super_new=3' 1\n"
NEW = "#!/bin/bash\nfinish() { echo \"$1\"; exit $2; }\nfinish 'GATE FAIL abc unit census failed: tools/x,tools/y' 1\n"


def git(root, *a):
    return subprocess.run(["git", "-C", root] + list(a), env=ENV, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def commit(root, path, text, msg):
    write(os.path.join(root, path), text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def sh(argv, cwd, env_extra=None):
    p = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(ENV, **(env_extra or {})), timeout=120)
    return p.returncode, p.stdout


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gtc-")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def repo(self, checker=None):
        """fixture with the real checker/gate/gate-request, a stub heavy-job, check-all.sh = OLD on trunk; HEAD on branch `feat` cut from trunk."""
        type(self).n += 1
        root = os.path.join(self.tmp, "r%d" % type(self).n)
        make_fixture(root)
        for f in ("gate-trunk-check", "gate", "gate-request", "gate-lint-first"):
            shutil.copy(os.path.join(TOOLS, f), os.path.join(root, "tools", f))
            os.chmod(os.path.join(root, "tools", f), 0o755)
        if checker:
            checker(os.path.join(root, "tools", "gate-trunk-check"))
        write(os.path.join(root, "tools", "heavy-job"), '#!/bin/bash\necho "$*" >"${HJ_MARK:-/dev/null}"\necho "heavy-job: detached unit=u log=l"\necho "RESULT: PASS heavy-job detach"\n')
        write(os.path.join(root, "tools", "check-all.sh"), OLD)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "gate tooling")
        git(root, "branch", "-M", "trunk")
        git(root, "checkout", "-q", "-b", "feat")
        return root

    def check(self, root, **env):
        global CASES
        CASES += 1
        return sh([os.path.join(root, "tools", "gate-trunk-check"), "--repo", root], root, env)

class Trunk(Base):
    def test_equal_to_trunk_passes(self):
        rc, out = self.check(self.repo())
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS gate-trunk-check files=14", out)

    def test_branch_behind_trunk_check_all_is_refused_with_trunk_sha_and_file(self):
        root = self.repo()
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk: new GATE line")
        git(root, "checkout", "-q", "feat")
        commit(root, "docs/work.txt", "w\n", "branch work")
        rc, out = self.check(root)
        self.assertEqual(rc, 1, out)
        t7 = git(root, "rev-parse", "--short=8", "trunk")
        self.assertIn("REFUSED: merge trunk first: gate tooling behind trunk by %s (tools/check-all.sh)" % t7, out)
        self.assertIn("RESULT: FAIL gate-trunk-check behind=1 trunk=%s files=tools/check-all.sh" % t7, out)

    def test_branch_behind_on_heavy_job_is_refused(self):
        root = self.repo()
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/heavy-job", "#!/bin/bash\n# fixed\n", "trunk: heavy-job fix")
        git(root, "checkout", "-q", "feat")
        rc, out = self.check(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("files=tools/heavy-job", out)

    def test_branch_that_edits_the_gate_itself_is_not_refused(self):
        root = self.repo()
        commit(root, "tools/check-all.sh", NEW, "branch changes the gate")
        git(root, "checkout", "-q", "trunk")
        commit(root, "docs/other.txt", "o\n", "trunk moves elsewhere")
        git(root, "checkout", "-q", "feat")
        rc, out = self.check(root)
        self.assertEqual(rc, 0, out)
        self.assertIn("branch-edits=tools/check-all.sh", out)

    def test_both_changed_is_refused(self):
        root = self.repo()
        commit(root, "tools/check-all.sh", OLD + "# branch\n", "branch edit")
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk edit")
        git(root, "checkout", "-q", "feat")
        rc, out = self.check(root)
        self.assertEqual(rc, 1, out)

    def test_merging_trunk_clears_the_refusal(self):
        root = self.repo()
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk: new GATE line")
        git(root, "checkout", "-q", "feat")
        self.assertEqual(self.check(root)[0], 1)
        git(root, "merge", "-q", "--no-edit", "trunk")
        rc, out = self.check(root)
        self.assertEqual(rc, 0, out)

    def test_override_is_logged_and_passes(self):
        root = self.repo()
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk")
        git(root, "checkout", "-q", "feat")
        rc, out = self.check(root, GATE_TOOLING_OVERRIDE="bisect old tooling")
        self.assertEqual(rc, 0, out)
        self.assertIn("gate-trunk-check: OVERRIDE bisect old tooling", out)

    def test_no_trunk_ref_is_a_printed_skip(self):
        root = self.repo()
        rc, out = self.check(root, GATE_TRUNK_REFS="nosuchref")
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIPPED no trunk ref", out)

    def behind_repo(self):
        root = self.repo()
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk: new GATE line")
        git(root, "checkout", "-q", "feat")
        return root

    def test_gate_request_refuses_a_behind_branch_and_starts_nothing(self):
        global CASES
        CASES += 1
        root = self.behind_repo()
        mark = os.path.join(self.tmp, "hj-req.mark")
        rc, out = sh([os.path.join(root, "tools", "gate-request"), "--repo", root, "--label", "L1", "--reason", "t"], root, {"HJ_MARK": mark})
        self.assertEqual(rc, 1, out)
        self.assertIn("merge trunk first: gate tooling behind trunk by", out)
        self.assertIn("RESULT: FAIL gate-request gate-trunk-check rc=1", out)
        self.assertFalse(os.path.exists(mark), "heavy-job must not run")

    def test_gate_request_on_current_branch_still_starts(self):
        global CASES
        CASES += 1
        root = self.repo()
        rc, out = sh([os.path.join(root, "tools", "gate-request"), "--repo", root, "--label", "L2", "--reason", "t"], root, {"GATE_REQUEST_DRY": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS gate-trunk-check", out)
        self.assertIn("RESULT: PASS gate-request dry", out)

    def test_gate_refuses_a_behind_branch_but_dry_run_only_reports(self):
        global CASES
        CASES += 1
        root = self.behind_repo()
        mark = os.path.join(self.tmp, "hj-gate.mark")
        rc, out = sh([os.path.join(root, "tools", "gate"), "--formal", "--label", "x"], root, {"HJ_MARK": mark})
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate refused: gate-trunk-check rc=1", out)
        self.assertFalse(os.path.exists(mark), "heavy-job must not run")
        rc, out = sh([os.path.join(root, "tools", "gate"), "--formal", "--label", "x"], root, {"GATE_DRY": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("a real run would be refused", out)

    def test_gate_on_current_branch_launches(self):
        global CASES
        CASES += 1
        root = self.repo()
        mark = os.path.join(self.tmp, "hj-ok.mark")
        rc, out = sh([os.path.join(root, "tools", "gate"), "--formal", "--label", "x"], root, {"HJ_MARK": mark})
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(mark), out)


def mutant(needle, repl):
    def edit(path):
        with open(path) as fh:
            t = fh.read()
        assert t.count(needle) == 1, needle
        with open(path, "w") as fh:
            fh.write(t.replace(needle, repl))
    return edit


class Mutants(Base):
    """A checker that passes the old copy (the bug) must fail the 'behind' cases: each mutant is run against them and has to be CAUGHT."""

    def caught(self, edit):
        global CASES
        CASES += 1
        root = self.repo(checker=edit)
        git(root, "checkout", "-q", "trunk")
        commit(root, "tools/check-all.sh", NEW, "trunk: new GATE line")
        git(root, "checkout", "-q", "feat")
        rc, out = sh([os.path.join(root, "tools", "gate-trunk-check"), "--repo", root], root)
        self.assertEqual(rc, 0, "mutant must pass the old copy (otherwise the mutant is not one): " + out)
        # the unmutated behaviour on the same scenario is a refusal
        shutil.copy(os.path.join(TOOLS, "gate-trunk-check"), os.path.join(root, "tools", "gate-trunk-check"))
        rc, out = sh([os.path.join(root, "tools", "gate-trunk-check"), "--repo", root], root)
        self.assertEqual(rc, 1, out)

    def test_mutant_always_treats_diff_as_branch_edit(self):
        self.caught(mutant('if [ "$t" = "$m" ]; then edits+=("$f"); else behind+=("$f"); fi', 'edits+=("$f")'))

    def test_mutant_ignores_check_all(self):
        self.caught(mutant("files=${GATE_TOOLING_FILES:-tools/check-all.sh ", "files=${GATE_TOOLING_FILES:-"))

    def test_mutant_without_refusal_exit(self):
        self.caught(mutant('\techo "RESULT: FAIL gate-trunk-check behind=${#behind[@]} trunk=$t7 files=$(join "${behind[@]}")"\n\texit 1', '\techo "RESULT: FAIL gate-trunk-check"\n\texit 0'))


if __name__ == "__main__":
    r = unittest.main(verbosity=1, exit=False).result
    if r.wasSuccessful():
        print("RESULT: PASS test_gate_trunk_check cases=%d" % CASES)
        sys.exit(0)
    print("RESULT: FAIL test_gate_trunk_check failures=%d errors=%d" % (len(r.failures), len(r.errors)))
    sys.exit(1)
