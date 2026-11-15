#!/usr/bin/env python3
"""recover_recovery() of tools/xrun (research 59 item 7) against a STUB tools/phone: no adb, no fastboot, the phone is never touched (AP-55).
The function text is cut out of tools/xrun, so the test runs the real code."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os, re, stat, subprocess, tempfile, unittest

XRUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xrun")
STUB = """#!/bin/bash
echo "$*" >> "$STUB_CALLS"
case "$1" in
  state) echo "RESULT: PASS state=$STUB_STATE target=none steps=0" ;;
  goto) [ -z "$STUB_NOISE" ] || echo "$STUB_NOISE"; [ "$STUB_GOTO_RC" = 0 ] && echo "RESULT: PASS state=fastboot target=fastboot steps=1" || echo "RESULT: FAIL state=recovery target=fastboot steps=1"; exit "$STUB_GOTO_RC" ;;
esac
"""
PM_OK = "#!/bin/bash\necho 'bootreason: x'; echo 'RESULT: PASS postmortem 9/9 probes saved in /x'\n"
PM_FAIL = "#!/bin/bash\necho 'RESULT: FAIL no probe returned data'; exit 1\n"
PM_CRASH = "#!/bin/bash\nexit 7\n"


class T(unittest.TestCase):
    def run_fn(self, state, goto_rc=0, noise="", pm=None, want_log=False):
        src = open(XRUN).read()
        m = re.search(r"^recover_recovery\(\) \{.*?; fi; \}\n", src, re.S | re.M)
        self.assertTrue(m, "recover_recovery not found in tools/xrun")
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "phone"); calls = os.path.join(d, "calls")
            open(stub, "w").write(STUB); os.chmod(stub, 0o755)
            pms = os.path.join(d, "pm")
            open(pms, "w").write(pm if pm is not None else PM_OK); os.chmod(pms, 0o755)
            env = dict(os.environ, XRUN_POSTMORTEM=pms, XRUN_PHONE=stub, STUB_CALLS=calls, STUB_STATE=state, STUB_GOTO_RC=str(goto_rc), STUB_NOISE=noise)
            # as tools/xrun calls it: under its `set -euo pipefail` (xrun:34), via `rec=$(recover_recovery)` (errexit is not inherited by $(...), pipefail is),
            # so a postmortem failure that would abort or change xrun fails here too (review xrun-postmortem-r1 F1)
            r = subprocess.run(["bash", "-c", "set -euo pipefail\nLOG=%s/log\n%s\nrec=$(recover_recovery)\necho \"$rec\"" % (d, m.group(0))], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            if want_log:
                return r.stdout.strip(), open(os.path.join(d, "log")).read()
            return r.stdout.strip(), (open(calls).read().splitlines() if os.path.exists(calls) else [])

    def test_recovery_calls_goto_fastboot(self):
        out, calls = self.run_fn("recovery")
        self.assertEqual(out, "recovered")
        self.assertEqual(calls, ["state", "goto fastboot --timeout 180"])

    def test_recovery_goto_fails(self):
        out, calls = self.run_fn("recovery", 1)
        self.assertEqual(out, "recover-failed")
        self.assertEqual(calls[-1], "goto fastboot --timeout 180")

    def test_timeout_noise_in_goto_output_is_marked(self):
        # rule 8 (g97/g98, 2026-10-27): a goto that printed a timeout / FAIL before passing must not read as a clean recovery
        for noise in ("Timeout exceeded", "RESULT: FAIL state=recovery target=fastboot steps=1", "NEED USER: check cable"):
            out, _ = self.run_fn("recovery", 0, noise)
            self.assertEqual((noise, out), (noise, "recovered-after-timeout"))

    def test_postmortem_runs_in_recovery_and_is_logged(self):
        out, log = self.run_fn("recovery", want_log=True)
        self.assertEqual(out, "recovered")
        self.assertIn("POSTMORTEM RESULT: PASS postmortem 9/9", log)
        self.assertIn("bootreason: x", log)  # the probe output itself reaches the run log (review research-75-77-kernel-r1 F4: kills the tee mutant)
        self.assertNotIn("POSTMORTEM GAP:", log)

    def test_postmortem_failure_is_loud_and_does_not_block_recovery(self):  # rule 8
        for pm, want in ((PM_FAIL, "POSTMORTEM RESULT: FAIL no probe"), (PM_CRASH, "POSTMORTEM RESULT: FAIL no RESULT line")):
            out, log = self.run_fn("recovery", pm=pm, want_log=True)
            self.assertEqual(out, "recovered"); self.assertIn(want, log)
            self.assertRegex(log, r"(?m)^POSTMORTEM GAP: no post-reset evidence: FAIL ")  # counted on the RESULT line (rule 8)

    def test_postmortem_timeout_is_bounded_and_loud(self):  # r1 F3: the bound itself is tested (XRUN_POSTMORTEM_TIMEOUT=2 stands in for 120)
        os.environ["XRUN_POSTMORTEM_TIMEOUT"] = "2"
        try:
            out, log = self.run_fn("recovery", pm="#!/bin/bash\nsleep 30\necho 'RESULT: PASS late'\n", want_log=True)
        finally:
            del os.environ["XRUN_POSTMORTEM_TIMEOUT"]
        self.assertEqual(out, "recovered"); self.assertIn("POSTMORTEM GAP: no post-reset evidence: FAIL no RESULT line", log)

    def test_probe_text_cannot_pose_as_result_or_gap(self):  # r1 F2
        pm = "#!/bin/bash\necho 'RESULT: PASS spoofed'; echo 'SWEET GAP: spoofed'; echo 'RESULT: FAIL real'; exit 1\n"
        out, log = self.run_fn("recovery", pm=pm, want_log=True)
        self.assertEqual(out, "recovered")
        self.assertNotRegex(log, r"(?m)^\s*(SWEET GAP:|RESULT: PASS spoofed)")  # xrun_report strips a line before GAP_ANY (xrun_report.py:112): leading "|" is what blocks it
        self.assertIn("POSTMORTEM RESULT: FAIL real", log)

    def test_postmortem_not_run_on_other_states(self):
        for st in ("fastboot", "absent", "ios", "android"):
            out, calls = self.run_fn(st, pm="#!/bin/bash\necho ran >> \"$STUB_CALLS\"\n")
            self.assertEqual((st, out, calls), (st, "", ["state"]))  # the stub would add a line "ran" to calls

    def test_marker_text_in_summary(self):
        src = open(XRUN).read()
        self.assertIn("recovered-after-timeout) $R mark", src)
        self.assertIn('timeout 220 "$tofb" 180', src); self.assertIn("tofb=${XRUN_TO_FASTBOOT:-tools/to-fastboot.sh}", src)   # the first attempt must outlast reset -> recovery (~90 s, g98 phone.log)

    def test_no_call_on_other_states(self):
        for st in ("fastboot", "absent", "ios", "android", "booting", "other-device", "hung"):
            out, calls = self.run_fn(st)
            self.assertEqual((st, out, calls), (st, "", ["state"]))

    def test_wired_into_all_recovery_blocks(self):
        src = open(XRUN).read()
        self.assertEqual(src.count("rec=$(recover_recovery)"), 4)   # EARLY, TIMEOUT, DONE (T-35ba), SAFETY (RULES H9 safety stop)

    def test_done_recovers_only_a_recovery_landing(self):
        src = open(XRUN).read()
        m = re.search(r"^DONE\) .*?^\tfi ;;$", src, re.S | re.M)
        self.assertTrue(m, "no DONE branch in tools/xrun")
        self.assertIn('"usb_back_id": *"18d1:d001"', m.group(0))
        self.assertIn("XRUN GAP:", m.group(0))


if __name__ == "__main__":
    unittest.main()
