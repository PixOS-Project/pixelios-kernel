#!/usr/bin/env python3
"""Tests for tools/gate-lint-first and tools/gate-request (AP-278, R42): hermetic, fixture repos in a tempdir with stub tools, no network, no phone.

Run: python3 -I tools/test_gate_lint_first.py

Scenarios (each builds a fixture repo, runs the tools, returns None or an error string) are run on the real tools and, as mutants, on
copies of the two tools with one honest defect each; every mutant must be KILLED (at least one scenario returns an error). The drift test
checks tools/gate-lint-steps.tsv against tools/check-all.sh.

Split (PLAN-GATE-2X K4a): fixtures/scenarios/mutants live in tools/gate_lint_first_lib.py; the mutants run in 3 groups: here (group 0), test_gate_lint_first_m1.py, test_gate_lint_first_m2.py.
"""
import os, re, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_lint_first_lib import MUTANT_GROUPS, MUTANTS, SCENARIOS, STEPS, TOOLS, check, make_tooldir, mutant_group, run, run_mutants


class LintFirstStamp(unittest.TestCase):
    """T-f32e: gate-lint-first leaves shim/out/lint-first.result for tools/run-preflight (the first half of a quick RAM-boot admission)."""
    def repo(self, rows):
        import subprocess
        d = tempfile.mkdtemp(prefix="glf-stamp-")
        self.addCleanup(shutil.rmtree, d, True)
        for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", d, *c], check=True)
        os.makedirs(os.path.join(d, "tools"))
        with open(os.path.join(d, "tools", "gate-lint-steps.tsv"), "w") as f:
            f.write("".join("%s\tx\t%s\n" % r for r in rows))
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("shim/out/\n")
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)
        subprocess.run(["git", "-C", d, "commit", "-qm", "c"], check=True)
        return d, subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()

    def stamp(self, d):
        with open(os.path.join(d, "shim", "out", "lint-first.result")) as f:
            return f.read().strip()

    def test_pass_and_fail_stamp_the_exact_head(self):
        import subprocess
        d, h = self.repo([("one", "true"), ("two", "needs tools/missing true")])
        p = subprocess.run([sys.executable, os.path.join(TOOLS, "gate-lint-first"), "--repo", d], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self.stamp(d), "LINT-FIRST PASS %s steps=1 skipped=1" % h)
        d, h = self.repo([("one", "true"), ("bad", "false")])
        p = subprocess.run([sys.executable, os.path.join(TOOLS, "gate-lint-first"), "--repo", d], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.stamp(d), "LINT-FIRST FAIL %s steps=2 skipped=0" % h)

    def test_dirty_tree_is_stamped_dirty_and_list_writes_nothing(self):
        import subprocess
        d, h = self.repo([("one", "true")])
        with open(os.path.join(d, "untracked"), "w") as f:
            f.write("x")
        subprocess.run([sys.executable, os.path.join(TOOLS, "gate-lint-first"), "--repo", d], capture_output=True, text=True)
        self.assertEqual(self.stamp(d), "LINT-FIRST PASS %s+dirty steps=1 skipped=0" % h)
        os.remove(os.path.join(d, "shim", "out", "lint-first.result"))
        subprocess.run([sys.executable, os.path.join(TOOLS, "gate-lint-first"), "--repo", d, "--list"], capture_output=True, text=True)
        self.assertFalse(os.path.exists(os.path.join(d, "shim", "out", "lint-first.result")))


class GateLintFirst(unittest.TestCase):
    def test_scenarios_on_real_tools(self):
        tmp = tempfile.mkdtemp(prefix="glf-t-")
        try:
            td = make_tooldir(tmp)
            for sc in SCENARIOS:
                with self.subTest(sc.__name__):
                    self.assertIsNone(check(sc, td))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mutants_killed_group0(self):
        run_mutants(self, 0)

    def test_mutant_groups_partition_all_mutants(self):
        """No mutant is lost by the split: the groups (run here and in test_gate_lint_first_m1.py / _m2.py) are disjoint and together are all of MUTANTS."""
        names = [n for g in range(MUTANT_GROUPS) for n in mutant_group(g)]
        self.assertEqual(sorted(names), sorted(MUTANTS))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(MUTANTS), 20, "MUTANTS changed size: if a mutant was added or removed on purpose, update this count (the %d groups take every %dth item in table order)" % (MUTANT_GROUPS, MUTANT_GROUPS))

    def test_runner_file_check_catches_a_missing_extra_or_misnumbered_group_file(self):
        """check_runner_files must fail when a group file is gone, an extra one exists (MUTANT_GROUPS lowered) or a file runs the wrong group (review gate-cold-k14 r1 F7)."""
        import gate_lint_first_lib as lib
        from unittest import mock
        names = [n for n in os.listdir(TOOLS) if n.startswith("test_gate_lint_first_m")]
        for victim, extra, groups in (("test_gate_lint_first_m2.py", None, 3), (None, "test_gate_lint_first_m3.py", 3), (None, None, 4), (None, None, 2)):
            with self.subTest(victim=victim, extra=extra, groups=groups):
                with tempfile.TemporaryDirectory() as d:
                    for n in names:
                        shutil.copy(os.path.join(TOOLS, n), d)
                    if victim:
                        os.remove(os.path.join(d, victim))
                    if extra:
                        shutil.copy(os.path.join(TOOLS, names[0]), os.path.join(d, extra))
                    with mock.patch.object(lib, "TOOLS", d), mock.patch.object(lib, "MUTANT_GROUPS", groups), self.assertRaises(AssertionError):
                        lib.check_runner_files(self)
        with tempfile.TemporaryDirectory() as d:                   # m1 running group 2 and m2 running group 1
            for n in names:
                shutil.copy(os.path.join(TOOLS, n), d)
            for n, a, b in (("test_gate_lint_first_m1.py", "run_mutants(self, 1)", "run_mutants(self, 2)"),):
                with open(os.path.join(d, n)) as fh:
                    t = fh.read()
                with open(os.path.join(d, n), "w") as fh:
                    fh.write(t.replace(a, b))
            with mock.patch.object(lib, "TOOLS", d), self.assertRaises(AssertionError):
                lib.check_runner_files(self)
        lib.check_runner_files(self)

    def test_table_matches_check_all(self):
        """Every `gate = step` row of the table is a `step B NAME COMMAND` line of tools/check-all.sh with the same command (the two cannot drift)."""
        with open(os.path.join(TOOLS, "gate-lint-steps.tsv")) as fh:
            rows = [l.rstrip("\n").split("\t") for l in fh if l.strip() and not l.startswith("#")]
        self.assertEqual([r[0] for r in rows], STEPS)
        gate = {}
        with open(os.path.join(TOOLS, "check-all.sh")) as fh:
            for line in fh:
                m = re.match(r"\s*(?:par )?step B (\S+)\s+(.*?)\s*$", line)
                if m:
                    gate[m.group(1)] = m.group(2)
        for name, kind, cmd in rows:
            with self.subTest(step=name):
                if kind == "inside:validate":
                    with open(os.path.join(TOOLS, "validate-repo.py")) as fh:
                        src = fh.read()
                    tool, flag = cmd.split()[1].split("/")[-1], cmd.split()[2]
                    self.assertRegex(src, r'"%s"\)\s*,\s*"%s"' % (re.escape(tool), re.escape(flag)), "validate-repo.py no longer runs %s" % cmd)
                    continue
                self.assertEqual(kind, "step")
                parts = cmd.split()
                needs_file = parts[1] if parts[0] == "needs" else None
                if name not in gate:
                    # only a tool that is not in this checkout yet (unmerged branch) may lack its gate line
                    self.assertTrue(needs_file and not os.path.exists(os.path.join(os.path.dirname(TOOLS), needs_file)),
                                    "step %s is in the table but not a `step B %s` line of check-all.sh" % (name, name))
                    continue
                line = gate[name]
                self.assertTrue(line == cmd or line.split("#")[0].strip() == cmd,
                                "check-all.sh `step B %s` is %r, the table says %r" % (name, line, cmd))

    # check-all.sh laneB steps that are NOT seconds-long lint steps (reason each); a new `step B` line must be in the table or here
    EXCLUDED = {
        "shellcheck": "34 s over all shell files, needs the package",
        "unit": "gate-unit-shards, minutes",
        "phone-state": "self-test of a phone tool, not a lint of the checkout",
        "hang-recover": "self-test of a phone tool",
        "probe-run": "self-test of a phone tool, 70 s",
        "rootless": "bash -n over the rootless root builders",
        "wlan-tbl": "header-check of the committed Wi-Fi msgbuf table against its TSV (T-8b3c): a quick-gate step of its own and a chk in shim/build.sh; kept out of the pinned lint-first list (every row there has a fixture stub tool and mutants in gate_lint_first_lib.py)",
    }

    def test_every_check_all_step_is_covered(self):
        """The reverse direction: every `step B NAME` of check-all.sh is a table row or in EXCLUDED (a new seconds-long lint step cannot be forgotten)."""
        with open(os.path.join(TOOLS, "gate-lint-steps.tsv")) as fh:
            table = {l.split("\t")[0] for l in fh if l.strip() and not l.startswith("#")}
        names = []
        with open(os.path.join(TOOLS, "check-all.sh")) as fh:
            for line in fh:
                m = re.match(r"\s*(?:par )?step B (\S+)", line)
                if m:
                    names.append(m.group(1))
        self.assertTrue(names)
        for n in names:
            with self.subTest(step=n):
                self.assertTrue(n in table or n in self.EXCLUDED, "check-all.sh step %s is neither in tools/gate-lint-steps.tsv nor in EXCLUDED with a reason" % n)
        for n in self.EXCLUDED:
            self.assertIn(n, names, "EXCLUDED lists %s which is no `step B` line any more" % n)
            self.assertNotIn(n, table)

    def test_real_repo_list(self):
        rc, out = run([sys.executable, os.path.join(TOOLS, "gate-lint-first"), "--list"])
        self.assertEqual(rc, 0, out)
        self.assertEqual([x.split("\t")[0] for x in out.splitlines() if "\t" in x], STEPS)


if __name__ == "__main__":
    unittest.main(verbosity=1)
