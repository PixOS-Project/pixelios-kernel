#!/usr/bin/env python3
"""Tests for the CEILING check of tools/run-preflight (c_ceiling in tools/run_preflight_ceiling.py, T-d4d9; split out of test_run_preflight.py by T-c8df: R29).
Reuses the fixture of test_run_preflight.T (plan, rp); its own tests are switched off here so they run once, in test_run_preflight."""
import importlib.machinery, importlib.util, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_run_preflight as base  # noqa: E402
from test_run_preflight import put  # noqa: E402


class Ceiling(base.T):
    # T-d4d9 c_ceiling: a stream boot (--intent wlan here; gpu/timing need their own flags) states its ceiling before the boot
    CEIL_OK = ("Baseline score: 53.6 +- 1.5 (gputest-nav2)\nKnob: keep the first submit off the A55\nCEILING: 58.0 (docs/reviews/x.py, baseline gputest-nav2)\n"
               "Ceiling check: RESULT: PASS ceiling-check script=docs/reviews/x.py ceiling=58.0 oracle=53.6 scored=53.6\n")

    def cplan(self, extra, strip=()):
        t = (self.PLAN % "g1").replace("Block: TB\n", "Block: TB\nPLAN-CHECK-WAIVER: user 2026-11-08 \"fixture: these tests are about the CEILING line\"\n" + extra)   # T-dd8e: the plan-check gate has its own tests
        for x in strip:
            t = t.replace(x, "")
        return {"g1.md": t}

    def ceil_rc(self, extra, intent="wlan", **kw):
        return self.rp(self.cplan(extra), "g1", "--expect", "A+", intent=intent, **kw)

    def test_ceiling_ok_and_non_stream_untouched(self):
        rc, out = self.ceil_rc(self.CEIL_OK)
        self.assertEqual(rc, 0, out)
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+", intent="none")   # no stream intent, no Knob line: not a knob boot
        self.assertEqual(rc, 0, out)

    def test_ceiling_missing_refused_with_why_do_instead_escape(self):
        rc, out = self.ceil_rc("Knob: x change\n")
        self.assertNotEqual(rc, 0, out)
        for w in ("c_ceiling", "Why:", "Do instead:", "ceiling-waiver"):
            self.assertIn(w, out)
        rc, out = self.ceil_rc("", intent="wlan")   # stream intent without any Knob/Witnesses/CEILING
        self.assertNotEqual(rc, 0, out); self.assertIn("c_ceiling", out)
        rc, out = self.ceil_rc("Knob: x change\n", intent="none")   # a Knob line alone makes it a knob boot, whatever the intent
        self.assertNotEqual(rc, 0, out); self.assertIn("c_ceiling", out)
        rc, out = self.ceil_rc("Knob: <the ONE change>\n", intent="none")   # the TEMPLATE placeholder is not a knob
        self.assertEqual(rc, 0, out)

    def test_ceiling_must_clear_baseline_plus_noise(self):
        for ceil in ("55.0", "55.1", "50.0", "53.6"):   # 53.6 + 1.5 = 55.1: equal or below does not clear
            ok = self.CEIL_OK.replace("58.0", ceil)
            rc, out = self.ceil_rc(ok)
            self.assertEqual(rc != 0, ceil in ("55.0", "55.1", "50.0", "53.6"), "%s: %s" % (ceil, out))
            self.assertIn("does not clear baseline", out)
        rc, out = self.ceil_rc(self.CEIL_OK.replace("58.0", "55.2"))
        self.assertEqual(rc, 0, out)

    def test_ceiling_lower_is_better_direction(self):
        low = "Direction: lower\n" + self.CEIL_OK.replace("53.6 +- 1.5", "16.8 +- 0.5").replace("58.0", "15.0").replace("scored=53.6", "scored=16.8").replace("oracle=53.6", "oracle=16.8")
        rc, out = self.ceil_rc(low)
        self.assertEqual(rc, 0, out)
        rc, out = self.ceil_rc(low.replace("15.0", "16.5").replace("ceiling=15.0", "ceiling=16.5").replace("ceiling=58.0", "ceiling=16.5"))
        self.assertNotEqual(rc, 0, out)

    def test_ceiling_needs_baseline_score_and_ceiling_check_line(self):
        rc, out = self.ceil_rc(self.CEIL_OK.replace("Baseline score: 53.6 +- 1.5 (gputest-nav2)\n", ""))
        self.assertNotEqual(rc, 0, out); self.assertIn("Baseline score", out)
        for edit in (("Ceiling check: RESULT: PASS ceiling-check script=docs/reviews/x.py ceiling=58.0 oracle=53.6 scored=53.6\n", ""),
                     ("RESULT: PASS", "RESULT: FAIL"), ("ceiling=58.0", "ceiling=59.0"), ("scored=53.6", "scored=54.0")):
            rc, out = self.ceil_rc(self.CEIL_OK.replace(*edit))
            self.assertNotEqual(rc, 0, "%s: %s" % (edit[0][:30], out)); self.assertIn("Ceiling check", out)
        rc, out = self.ceil_rc(self.CEIL_OK.replace("CEILING: 58.0 (docs/reviews/x.py, baseline gputest-nav2)", "CEILING: lots"))
        self.assertNotEqual(rc, 0, out); self.assertIn("not '<value>", out)

    def test_ceiling_unknown_needs_three_witnesses(self):
        for w, n_ok in (("Witnesses: a; b\n", False), ("Witnesses: a; b; c\n", True), ("Witnesses: a; ; <w>\n", False)):
            rc, out = self.ceil_rc("CEILING: unknown, witness boot\n" + w)
            self.assertEqual(rc == 0, n_ok, "%s: %s" % (w, out))
        plan = self.cplan("CEILING: unknown, witness boot\n")
        plan["g1.md"] = plan["g1.md"].replace("## Command", "## Batched diagnostics\n- one witness\n- two witness\n- three witness\n\n## Command")
        rc, out = self.rp(plan, "g1", "--expect", "A+", intent="wlan")
        self.assertEqual(rc, 0, out); self.assertIn("CEILING UNKNOWN", out)
        rc, out = self.ceil_rc("CEILING: unknown, witness boot\nWitnesses: a; b\n")
        self.assertIn("Do instead", out)

    def test_ceiling_waiver_is_loud_and_needs_a_reason(self):
        rc, out = self.ceil_rc("ceiling-waiver: first boot of a new block, no baseline exists\n")
        self.assertEqual(rc, 0, out); self.assertIn("CEILING WAIVER", out)
        rc, out = self.ceil_rc("ceiling-waiver: short\n")
        self.assertNotEqual(rc, 0, out)

    def test_ceiling_pre_method_plans_exempt(self):
        put(os.path.join(self.root, "plans", ".pre-method.txt"), "g1.md\n")
        rc, out = self.rp({"g1.md": self.PLAN % "g1"}, "g1", "--expect", "A+", intent="wlan")
        self.assertEqual(rc, 0, out)

    def test_ceiling_mutants_are_killed(self):
        """T-d4d9: each clause of c_ceiling is needed; a mutant of tools/run_preflight_ceiling.py must flip one verdict of the case table."""
        cases = [("", "wlan", "FAIL"), (self.CEIL_OK, "wlan", "PASS"), (self.CEIL_OK.replace("58.0", "55.0"), "wlan", "FAIL"), (self.CEIL_OK.replace("scored=53.6", "scored=54.0"), "wlan", "FAIL"),
                 (self.CEIL_OK.replace("Baseline score: 53.6 +- 1.5 (gputest-nav2)\n", ""), "wlan", "FAIL"), ("CEILING: unknown, witness boot\nWitnesses: a; b\n", "wlan", "FAIL"),
                 ("CEILING: unknown, witness boot\nWitnesses: a; b; c\n", "wlan", "PASS"), ("Direction: lower\n" + self.CEIL_OK, "wlan", "FAIL"), ("", "none", "PASS"),
                 ("ceiling-waiver: first boot of a new block, no baseline\n", "wlan", "PASS"), ("ceiling-waiver: short\n", "wlan", "FAIL"),
                 (self.CEIL_OK.replace("RESULT: PASS", "RESULT: FAIL"), "wlan", "FAIL")]
        src = open(os.path.join(HERE, "run_preflight_ceiling.py")).read()
        muts = [("stream intents", 'CEILING_INTENTS = ("gpu", "timing", "wlan")', 'CEILING_INTENTS = ()'), ("noise clause", "ceil > val + noise", "ceil > val"),
                ("direction", "ceil < val - noise if lower else ceil > val + noise", "ceil > val + noise"), ("oracle clause", "abs(float(cm.group(2)) - val) > 1e-9", "False"),
                ("ceiling-check pass", r"RESULT:\s*PASS\s+ceiling-check", r"RESULT:\s*\w+\s+ceiling-check"), ("witness count", "if n < 3:", "if n < 1:"),
                ("missing line", 'if not ce or ce.startswith("<"):', "if False:"), ("waiver reason", r'{9,})$", text, re.I | re.M)' + "\n    if wv:", r'*)$", text, re.I | re.M)' + "\n    if wv:"),
                ("baseline needed", "if not bm:", "if False and not bm:")]
        def verdicts(text):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "rpc.py")
                with open(path, "w") as f:
                    f.write(text)
                ldr = importlib.machinery.SourceFileLoader("rpc", path)
                m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rpc", ldr)); ldr.exec_module(m)
                out = []
                for extra, intent, _ in cases:
                    plan = os.path.join(d, "g1.md")
                    t = (self.PLAN % "g1").replace("Block: TB\n", "Block: TB\n" + extra)
                    put(plan, t)
                    out.append(m.c_ceiling(plan, t, intent, d)[0])
                return out
        want = [c[2] for c in cases]
        self.assertEqual(verdicts(src), want)
        for name, old, new in muts:
            self.assertGreaterEqual(src.count(old), 1, "mutant target missing: " + name)
            try:
                got = verdicts(src.replace(old, new))
            except Exception as _err:   # a mutant that crashes is killed
                print(f"test_run_preflight: ceiling mutant {name} crashed (killed): {type(_err).__name__}: {_err}", file=sys.stderr)
                continue
            self.assertNotEqual(got, want, "mutant survived: " + name)


for _n in [n for n in dir(base.T) if n.startswith("test_")]:
    setattr(Ceiling, _n, None)   # not callable: the loader skips the inherited tests


if __name__ == "__main__":
    unittest.main()
