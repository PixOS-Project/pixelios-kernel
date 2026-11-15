#!/usr/bin/env python3
"""Tests for the PLAN-CHECK gate of tools/run-preflight (tools/run_preflight_plancheck.py, T-dd8e). Reuses the fixture of test_run_preflight.T (plan, rp); its own tests are switched off here."""
import pathlib
import importlib.machinery, importlib.util, os, re, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_run_preflight as base  # noqa: E402
from test_run_preflight import put  # noqa: E402
import run_preflight_plancheck as pc  # noqa: E402

CEIL = "Knob: keep the first submit off the A55\nCEILING: unknown, witness boot\nWitnesses: a; b; c\n"


class PlanCheck(base.T):
    def setUp(self):
        super().setUp()
        os.environ["PIXOS_SUPER_ROOT"] = self.root   # the refusal log goes to <root>/logs, not next to the temp dir
        self.addCleanup(os.environ.pop, "PIXOS_SUPER_ROOT", None)

    def plan(self, extra="", phase=None):
        t = (self.PLAN % "g1").replace("Block: TB\n", "Block: TB\n" + extra)
        return t.replace("Phase: P-T1\n", "Phase: %s\n" % phase) if phase else t

    def ok_line(self, text, who="opus-5-5", sha=None):
        return "PLAN-CHECK: OK ceiling and band checked (%s, 2026-11-08T09:00Z, plan sha %s)\n" % (who, sha or pc.plan_sha(text))

    def run_plan(self, text, intent="wlan"):
        return self.rp({"g1.md": text}, "g1", "--expect", "A+", intent=intent, env={"PIXOS_SUPER_ROOT": self.root})

    def test_refused_without_check_with_why_do_instead_escape(self):
        rc, out = self.run_plan(self.plan(CEIL))
        self.assertNotEqual(rc, 0, out)
        for w in ("c_plancheck", "Why:", "Do instead:", "tools/queue plan-check", "PLAN-CHECK-WAIVER"):
            self.assertIn(w, out)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "logs", "plan-check-refused.tsv")))   # metric (e): the refusal is counted

    def test_passes_with_check_of_the_current_sha(self):
        t = self.plan(CEIL)
        rc, out = self.run_plan(t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t)))
        self.assertEqual(rc, 0, out)

    def test_edited_plan_needs_a_new_check(self):
        t = self.plan(CEIL)
        checked = t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t))
        rc, out = self.run_plan(checked.replace("is X?", "is Y?"))   # the sha no longer matches
        self.assertNotEqual(rc, 0, out); self.assertIn("does not match this plan text", out)
        rc, out = self.run_plan(t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t, who="sonnet-5-5")))
        self.assertNotEqual(rc, 0, out); self.assertIn("not by opus-5-5", out)

    def test_change_verdict_does_not_admit_a_boot(self):
        t = self.plan(CEIL)
        rc, out = self.run_plan(t.replace("Block: TB\n", "Block: TB\nPLAN-CHECK: CHANGE oracle does not reproduce the baseline (opus-5-5, 2026-11-08T09:00Z, plan sha %s)\n" % pc.plan_sha(t)))
        self.assertNotEqual(rc, 0, out); self.assertIn("the last check said CHANGE", out)

    def test_host_only_and_tooling_plans_untouched(self):
        rc, out = self.run_plan(self.plan(""), intent="none")   # Phase P-T1, no stream intent, no HW-WRITE
        self.assertEqual(rc, 0, out)

    def test_hw_write_risk_and_bt_phase_need_the_check(self):
        rc, out = self.run_plan(self.plan("Risk: HW-WRITE\nWitnesses: a; b; c\n"), intent="none")
        self.assertNotEqual(rc, 0, out); self.assertIn("Risk: HW-WRITE", out)
        rc, out = self.run_plan(self.plan("Witnesses: a; b; c\n", phase="BT-S3"), intent="none")   # Phase BT-S3 is not a fixture GOALS row: c_block refuses first, so call the check directly
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(pc.c_plancheck("g1.md", self.plan("Witnesses: a; b; c\n", phase="BT-S3"), "none", self.root)[0], "FAIL")

    def test_unknown_ceiling_needs_three_witnesses_and_no_ceiling_line_needs_them_too(self):
        for extra in ("CEILING: unknown, witness boot\nWitnesses: a; b\n", ""):
            t = self.plan(extra)
            st, why = pc.c_plancheck("g1.md", t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t)), "gpu", self.root)
            self.assertEqual(st, "FAIL", why); self.assertIn("witness", why)
        t = self.plan("Witnesses: a; b; c\n")   # no CEILING line, 3 witnesses: allowed
        self.assertEqual(pc.c_plancheck("g1.md", t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t)), "gpu", self.root)[0], "PASS")

    def test_waiver_is_loud_and_needs_date_and_quote(self):
        w = lambda s: self.run_plan(self.plan(CEIL + s))
        rc, out = w('PLAN-CHECK-WAIVER: user 2026-11-08 "go ahead without the check"\n')
        self.assertEqual(rc, 0, out); self.assertIn("PLAN-CHECK WAIVER", out)
        for bad in ("PLAN-CHECK-WAIVER: because\n", "PLAN-CHECK-WAIVER: user 2026-11-08\n", 'PLAN-CHECK-WAIVER: 2026-11-08 "quote"\n'):
            self.assertNotEqual(w(bad)[0], 0, bad)

    def test_pre_method_plans_exempt(self):
        put(os.path.join(self.root, "plans", ".pre-method.txt"), "g1.md\n")
        self.assertEqual(self.run_plan(self.plan(CEIL))[0], 0)

    def test_sha_ignores_check_lines_only(self):
        t = self.plan(CEIL)
        self.assertEqual(pc.plan_sha(t), pc.plan_sha(t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t))))
        self.assertNotEqual(pc.plan_sha(t), pc.plan_sha(t + "x\n"))

    def test_mutants_are_killed(self):
        """Each clause of c_plancheck is needed: a mutant of tools/run_preflight_plancheck.py must flip one verdict of the case table."""
        src = open(os.path.join(HERE, "run_preflight_plancheck.py")).read()
        t = self.plan(CEIL)
        good = t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t))
        t2 = self.plan("CEILING: unknown, witness boot\nWitnesses: a; b\n")   # checked by Opus, but only 2 witnesses
        cases = [(t2.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t2)), "gpu", "FAIL"), (good, "wlan", "PASS"), (t, "wlan", "FAIL"), (good.replace("is X?", "is Y?"), "wlan", "FAIL"), (self.plan(CEIL, phase="P-T1"), "none", "PASS"),
                 (self.plan("Risk: HW-WRITE\n" + CEIL), "none", "FAIL"), (self.plan("CEILING: unknown, witness boot\n"), "gpu", "FAIL"),
                 (t.replace("Block: TB\n", "Block: TB\n" + self.ok_line(t, who="sonnet-5-5")), "wlan", "FAIL"),
                 (t.replace("Block: TB\n", "Block: TB\nPLAN-CHECK-WAIVER: user 2026-11-08 \"go on\"\n"), "wlan", "PASS")]
        muts = [("sha match", "if sha not in shas or not by_opus:", "if not oks or not by_opus:"), ("by opus", "not by_opus", "False"), ("hw-write branch", 'Risk\\W*:[ \\t]*HW-WRITE\\b', 'Risk\\W*:[ \\t]*NEVER\\b'),
                ("stream intents", 'STREAM_INTENTS = ("gpu", "timing", "wlan", "bt")', "STREAM_INTENTS = ()"), 
                ("witness count", "and n < 3:\n        return (\"c_plancheck: %s says", "and n < 0:\n        return (\"c_plancheck: %s says"), ("waiver", "if wv:", "if False:")]
        def verdicts(text):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "rppc.py")
                pathlib.Path(path).write_text(text)
                for dep in ("run_preflight_ceiling.py",):
                    shutil.copy(os.path.join(HERE, dep), os.path.join(d, dep))
                spec = importlib.util.spec_from_file_location("rppc", path); m = importlib.util.module_from_spec(spec); sys.path.insert(0, d)
                try:
                    spec.loader.exec_module(m)
                    return [m.c_plancheck("g1.md", x, i, d)[0] for x, i, _ in cases]
                finally:
                    sys.path.remove(d)
        want = [c[2] for c in cases]
        self.assertEqual(verdicts(src), want)
        for name, old, new in muts:
            self.assertGreaterEqual(src.count(old), 1, "mutant target missing: " + name)
            try:
                got = verdicts(src.replace(old, new))
            except Exception as exc:   # a mutant that crashes is killed; say so (rule 8)
                print("mutant %s crashed (%s: %s): killed" % (name, type(exc).__name__, exc), file=sys.stderr)
                continue
            self.assertNotEqual(got, want, "mutant survived: " + name)

    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "run_preflight_plancheck.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


for _n in [n for n in dir(base.T) if n.startswith("test_") and n not in PlanCheck.__dict__]:
    setattr(PlanCheck, _n, None)   # not callable: the loader skips the inherited tests


if __name__ == "__main__":
    unittest.main()
