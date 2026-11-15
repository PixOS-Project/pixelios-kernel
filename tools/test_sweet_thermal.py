#!/usr/bin/env python3
"""Host tests for xnu/patches/0336-sweet-thermal.patch (SWEET THERMAL: fuel-gauge battery temperature every 10 s, iOS feed, kernel backstop). No phone, no build.

    python3 tools/test_sweet_thermal.py  -> `RESULT: PASS sweet-thermal cases=N` / `RESULT: FAIL ...`

Needs the patched XNU tree of a build (STACK = trunk patches + 0336): CHG_STACK_TREE, or the newest <artifacts>/localbuild/work/*/xnu that has bsd/dev/sweet_thermal.h.
Proves:
  1. the policy (bsd/dev/sweet_thermal.h): state machine, EVERY threshold edge (47.9/48.0, 44.1/44.0, 44.9/45.0, charging or not, 42.1/42.0, 54.9/55.0), hysteresis, the 2-sample CRIT
     confirmation, lever results (done / nothing to do / busy -> retried / failed), sensor read failure and implausible values -> GAP and NO action, stale, log cadence;
  2. the charger side as patched (bsd/dev/sweet_chg_i2c.c) in the HSI2C12 + chip model of tools/chg_icl_host_test.c: the sampler reads exactly FG 0x08 and 0x0a, the lever writes only
     CNFG_09 with the two values of 0287, bounded, only while the 0287 raise stands, release only with the SuperSpeed link + CHGTYP, the 0287 restore on link loss still works once;
  3. the kernel glue (bsd/dev/sweet_thermal.c) with stubbed kernel API: boot-arg default on / sweetthermal=0 off, boot lines, thread cadence, cores mask 0x18 and the boot mask, CRIT call;
  4. rule-8 review fixes (docs/reviews/0336-thermal-review.md): F1 17th lever call keeps the bus, F2 first thermal transfer runs the pending-transfer checks, F3 failed/closed-gate hold retried (bounded),
     F4 0336 applies after 0333, F5 no gate GAP flood, F6 log pacing, F7 cpu-order GAP, F8 publish ordering; each has host cases that fail on the code before the fix;
  5. the TEST-ONLY ramp boot-arg sweetthermalramp (user 2026-11-13: prove X1 drop / charge hold / CRIT park without heating the battery): bias = ramp * (good samples - 2) from the 3rd good sample on, added only inside th_tick()
     to the value the state machine uses; the published value (IOPMPowerSource Temperature, HID TG0B) is the real sensor value; refusals (range 1..100, sweetthermal=0, failing sensor); every sample line shows
     "real T C, test bias +B C"; a hot battery that is not charging says "not charging, nothing to hold"; no boot print matches the run-failure marker regex of tools/xrun_report.py;
  6. text lints (sweet_thermal.c touches no register; the chip-write function is called from exactly the 0287 sites plus the one thermal site) and mutants that must fail.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(ROOT, "xnu", "patches", "0336-sweet-thermal.patch")
_LB = os.environ.get("LOCALBUILD") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "localbuild")
CASES = []


def default_tree():
    t = os.environ.get("CHG_STACK_TREE")
    if t:
        return t
    c = sorted(glob.glob(os.path.join(_LB, "work", "*", "xnu", "bsd", "dev", "sweet_thermal.h")), key=os.path.getmtime, reverse=True)
    return os.path.dirname(os.path.dirname(os.path.dirname(c[0]))) if c else os.path.join(_LB, "work", "<label>", "xnu")


def rd(path):
    with open(path) as f:
        return f.read()


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = default_tree()
        cls.tmp = tempfile.mkdtemp(prefix="sweet-thermal-")
        cls.inc = os.path.join(cls.tmp, "inc")
        for h in ("sys/param.h", "sys/systm.h", "kern/clock.h", "mach/mach_time.h", "machine/machine_routines.h", "pexpert/pexpert.h", "pexpert/device_tree.h", "libkern/libkern.h"):
            os.makedirs(os.path.dirname(os.path.join(cls.inc, h)), exist_ok=True)
            with open(os.path.join(cls.inc, h), "w"):
                pass
        d = os.path.join(cls.tmp, "tree", "bsd", "dev")
        os.makedirs(d)
        for f in ("sweet_thermal.h", "sweet_thermal.c", "sweet_chg_i2c.c", "sweet_restart.c"):
            src = os.path.join(cls.tree, "bsd", "dev", f)
            if not os.path.isfile(src):
                raise AssertionError("no patched XNU tree: %s is missing (set CHG_STACK_TREE to the tree of the build that has 0336; never skipped)" % src)
            shutil.copy(src, d)
        cls.d = d

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- builds ----
    def build_host(self, d=None, out="host"):
        d = d or self.d
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DCHG_PMIC", "-DCHG_MON", "-DSWEET_CHG_I2C_HOST", '-DCHG_I2C_SRC="%s"' % os.path.join(d, "sweet_chg_i2c.c"),
                '-DTH_HDR="%s"' % os.path.join(d, "sweet_thermal.h"), "-I", os.path.join(ROOT, "tools"), os.path.join(ROOT, "tools", "sweet_thermal_host_test.c"), "-o", exe)
        return r, exe

    def build_glue(self, d=None, out="glue"):
        d = d or self.d
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DSWEET_THERMAL_HOST", '-DTH_C="%s"' % os.path.join(d, "sweet_thermal.c"), "-I", self.inc,
                os.path.join(ROOT, "tools", "sweet_thermal_glue_test.c"), "-o", exe)
        return r, exe

    @staticmethod
    def cases(exe):
        o = run(exe)
        return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M)), o

    def mutate(self, f, old, new, count=1):
        d = os.path.join(self.tmp, "mut")
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(self.d, d)
        p = os.path.join(d, f)
        s = rd(p)
        self.assertEqual(s.count(old), count, "mutant anchor %r matches %d times (wanted %d) in %s" % (old, s.count(old), count, f))
        with open(p, "w") as f:
            f.write(s.replace(old, new))
        return d

    def killed(self, d):
        """True when the mutant is caught: a case fails, or the build breaks with -Werror from the mutation, or the run crashes."""
        r, exe = self.build_host(d, "mut-host")
        if r.returncode != 0:
            return True
        res, o = self.cases(exe)
        if o.returncode != 0 or any(v != "ok" for v in res.values()):
            return True
        r, exe = self.build_glue(d, "mut-glue")
        if r.returncode != 0:
            return True
        res, o = self.cases(exe)
        return o.returncode != 0 or any(v != "ok" for v in res.values())

    # ---- tests ----
    def test_host_policy_and_charger(self):
        r, exe = self.build_host()
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        res, o = self.cases(exe)
        bad = {k: v for k, v in res.items() if v != "ok"}
        self.assertEqual(bad, {}, o.stdout[-3000:])
        self.assertEqual(o.returncode, 0)
        self.assertGreaterEqual(len(res), 150, o.stdout[-500:])
        for must in ("x1_48_0_drops", "x1_47_9_no_drop", "x1_44_0_restores", "x1_44_1_stays_dropped", "chg_45_0_charging_holds", "chg_44_9_charging_no_hold", "chg_42_0_releases", "chg_42_1_stays_held",
                     "crit_second_sample_requests", "crit_55_0_first_sample_waits", "fail_gap_no_action", "implausible_hot_95_no_action", "lever_hold_writes_original", "lever_release_writes_875",
                     "lever_link_lost_after_release_restores_once", "guard_thermal_release_bounded_at_16_hold_never", "sample_reads_exactly_two_regs", "thermal_off_p2_off_no_engine",
                     "lever_17th_call_hold_lowers_bus_alive", "lever_17th_call_sampling_continues", "lever_release_at_bound_refused_bus_alive", "lever_hold_at_bound_still_lowers",
                     "lever_80_calls_total_thermal_writes_bounded_17_bus_alive", "thermal_first_transfer_pending_refused", "thermal_first_transfer_int_status_refused", "thermal_first_transfer_idle_goes",
                     "p2_path_first_group_pending_refused_same_state", "lever_gate_lost_no_write_gap", "lever_hold_gate_closed_then_open_holds", "chg_failed_logged_and_retried", "chg_failed_retry_bounded",
                     "chg_failed_once_then_done", "integ_hot_hold_gate_closed_twice_then_held", "sample_phase1_never_opened_noengine_no_gate_calls", "order_xrun_5cpus_silent", "order_dt_5cpus_differs",
                     "ramp_set_0_refused", "ramp_set_101_refused", "ramp10_walk_charge_hold_at_sample_12", "ramp10_walk_x1_drop_at_sample_15", "ramp10_walk_crit_x2_at_sample_23", "ramp10_walk_order_chg_x1_crit",
                     "ramp10_walk_real_value_never_biased", "ramp10_every_sample_has_a_line", "ramp10_not_charging_no_hold_line_says_so", "ramp_refused_while_sensor_failing_one_line",
                     "ramp_resumes_after_failure_without_skipping", "ramp_bias_capped_at_100C_crit_retried", "nochg_hot_discharging_says_nothing_to_hold", "nochg_line_repeats_every_60s"):
            self.assertIn(must, res)
        CASES.extend("host:" + k for k in res)

    def test_glue(self):
        r, exe = self.build_glue()
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        res, o = self.cases(exe)
        bad = {k: v for k, v in res.items() if v != "ok"}
        self.assertEqual(bad, {}, o.stdout[-3000:])
        self.assertGreaterEqual(len(res), 45, o.stdout[-500:])
        for must in ("enabled_by_default", "disabled_gap_line_and_no_thread", "x1_drop_then_restore_masks", "crit_calls_the_reboot_once", "thread_fail_gap_no_action_nothing_published",
                     "kick_keeps_log_pacing_log_pending_1", "order_xrun_cpus5_one_ok_line_no_gap", "order_dt_cpus5_gap_lists_physical_cores", "order_topology_unavailable_gap_and_thread_still_starts",
                     "ramp_loud_line_at_start", "ramp_0_refused", "ramp_101_refused", "ramp_refused_when_sweetthermal_0", "ramp_walk_x1_drop_and_charge_hold_reached_the_actuators", "ramp_walk_published_value_is_real",
                     "ramp_walk_crit_calls_the_real_reboot_path_once", "ramp_walk_published_value_still_real_at_crit", "ramp_walk_not_charging_no_hold_call_line_says_so",
                     "ramp_failing_sensor_no_bias_no_action_nothing_published", "no_ramp_same_samples_no_action_and_no_bias_text"):
            self.assertIn(must, res)
        CASES.extend("glue:" + k for k in res)

    def test_patch_matches_tree_and_scope(self):
        p = rd(PATCH)
        files = re.findall(r"^diff --git a/(\S+) b/", p, re.M)
        self.assertEqual(sorted(files), sorted(["bsd/conf/files", "bsd/dev/sweet_chg.c", "bsd/dev/sweet_chg_i2c.c", "bsd/dev/sweet_restart.c", "bsd/dev/sweet_thermal.c", "bsd/dev/sweet_thermal.h",
                                                 "iokit/Kernel/arm/SweetBattery.cpp", "iokit/Kernel/arm/hid/SweetHID.cpp", "iokit/KernelConfigTables.cpp"]))
        for f in ("sweet_thermal.c", "sweet_thermal.h"):
            i = p.index("+++ b/bsd/dev/%s\n" % f)
            body = p[i:].split("\n", 2)[2]
            j = body.find("\ndiff --git ")
            body = body[:j] if j >= 0 else body
            text = "\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n"
            self.assertEqual(text, rd(os.path.join(self.d, f)), "%s in the patch differs from the tested tree" % f)
        added = "\n".join(l[1:] for l in p.split("\n") if l.startswith("+") and not l.startswith("+++"))
        self.assertIn('PE_parse_boot_argn("sweetthermal"', added)
        known = rd(os.path.join(ROOT, "tools", "bootargs-known.txt"))
        self.assertRegex(known, r"(?m)^sweetthermal\tpatch:0336$")
        self.assertRegex(known, r"(?m)^sweetthermalramp\tpatch:0336$")
        self.assertIn('PE_parse_boot_argn("sweetthermalramp"', added)
        head = p.split("\ndiff --git ", 1)[0]
        for need in ("tools/xrun", "0,6,7,4,5", "cpus=5", "patch 0333", "THERM_MAX_WRITES"):
            self.assertIn(need, head, "patch header must state %r (review 0336 F7/F4/F1)" % need)
        CASES.append("patch_scope")

    def test_applies_after_0333(self):
        """Review 0336 F4: 0336 must `git apply --check` on the tree 0333 has patched (tools/xnu-local-build SKIPS a patch that does not, silently shipping no thermal backstop)."""
        p0333 = os.path.join(ROOT, "xnu", "patches", "0333-sweet-wdt-early-arm.patch")
        self.assertTrue(os.path.isfile(p0333), "0333 is not in xnu/patches of this checkout: merge fix/wdt-early (or trunk once 0333 landed)")
        both = sorted(set(re.findall(r"^\+\+\+ b/(\S+)$", rd(p0333), re.M)) & set(re.findall(r"^\+\+\+ b/(\S+)$", rd(PATCH), re.M)))
        self.assertTrue(both, "no common file: nothing to order")
        w = os.path.join(self.tmp, "order")
        shutil.rmtree(w, ignore_errors=True)
        for f in both:
            os.makedirs(os.path.dirname(os.path.join(w, f)), exist_ok=True)
            shutil.copy(os.path.join(self.tree, f), os.path.join(w, f))
        inc = ["--include=" + f for f in both]

        def g(*a):
            return run("git", "apply", *inc, *a, cwd=w)
        # later patches touching the same files (e.g. 0345 in sweet_restart.c) sit on top of 0336 in the tested tree: peel them off first, newest first
        pdir = os.path.join(ROOT, "xnu", "patches")
        later = []
        for n in sorted(os.listdir(pdir), reverse=True):
            m = re.match(r"(\d{4})-.*\.patch$", n)
            if m and int(m.group(1)) > 336 and set(re.findall(r"^\+\+\+ b/(\S+)$", rd(os.path.join(pdir, n)), re.M)) & set(both):
                later.append(("reverse " + n[:4], ("-R", os.path.join(pdir, n))))
        for step, args in later + [("reverse 0336", ("-R", PATCH)), ("reverse 0333", ("-R", p0333)), ("apply 0333", (p0333,)), ("check 0336 on the 0333-patched files", ("--check", PATCH))]:
            r = g(*args)
            self.assertEqual(r.returncode, 0, "%s failed for %s: %s" % (step, both, r.stderr[-600:]))
        CASES.append("applies_after_0333")

    def test_lints(self):
        c = rd(os.path.join(self.d, "sweet_thermal.c"))
        h = rd(os.path.join(self.d, "sweet_thermal.h"))
        for bad in ("ml_io_map", "volatile uint32_t *", "i2c_hw_wr32", "sweet_pmu_write", "PE_halt_restart", "acpm", "smc #0"):
            self.assertNotIn(bad, c + h, "sweet_thermal.c/h must not touch hardware: %s" % bad)
        i2c = rd(os.path.join(self.d, "sweet_chg_i2c.c"))
        # the single chip-write function is called from the 0287 sites plus exactly one thermal site
        calls = re.findall(r"i2c_cnfg09_write\(", i2c)
        self.assertEqual(len(calls), 5, "i2c_cnfg09_write: definition + 3 sites of 0287/0298 + the one thermal site; now %d" % len(calls))
        i = i2c.index("sweet_chg_icl_thermal(int hold)")
        self.assertEqual(len(re.findall(r"i2c_cnfg09_write\(", i2c[i:])), 1)
        self.assertEqual(i2c.count("p3_therm_arm = 1;"), 1)
        # the sampler reads only FG 0x0a and 0x08
        j = i2c.index("sweet_chg_fg_sample(int32_t *deci_c")
        k = i2c.index("The charge lever of the thermal backstop")
        self.assertEqual(sorted(re.findall(r"chg_i2c_read\((0x[0-9a-f]+), (0x[0-9a-f]+)", i2c[j:k])), [("0x36", "0x08"), ("0x36", "0x0a")])
        self.assertNotIn("i2c_cnfg09_write", i2c[j:k])
        # the reboot goes through the existing path only
        r = rd(os.path.join(self.d, "sweet_restart.c"))
        a = r.index("sweet_thermal_reboot_bootloader(void)\n{")
        body = r[a:r.index("\n}\n", a)]
        self.assertIn("sweet_clean_stop();", body)
        self.assertIn("sweet_machine_reset(1);", body)
        self.assertIn("if (!sweet_reset_ready)", body)
        # no boot print of the thermal code may match the run-failure marker of tools/xrun_report.py (a "panic/hang/watchdog" word makes a healthy proof boot read as a failed run)
        xr = rd(os.path.join(ROOT, "tools", "xrun_report.py"))
        m = re.search(r'\("panic/hang/watchdog", r"(.*)"\),', xr)
        self.assertIsNotNone(m, "the panic/hang/watchdog marker row moved in tools/xrun_report.py: update this lint")
        marker = re.compile(m.group(1))
        for name, text in (("sweet_thermal.c", c), ("sweet_thermal.h", h)):
            for n, line in enumerate(text.split("\n"), 1):
                if '"' in line and not line.lstrip().startswith(("/*", "*", "//")):
                    self.assertIsNone(marker.search(line), "%s:%d matches the run-failure marker regex: %s" % (name, n, line.strip()[:160]))
        self.assertTrue(self.f8_ok(self.d), "the published time must be stored with a release store AFTER the value and read with an acquire load (review 0336 F8)")
        CASES.append("lints")

    @staticmethod
    def f8_ok(d):
        c = rd(os.path.join(d, "sweet_thermal.c"))
        i = c.find("th_g_cc = th_c.last_real_dC * 10;")
        j = c.find("__atomic_store_n(&th_g_ms, now ? now : 1, __ATOMIC_RELEASE)")
        return i >= 0 and j > i and "__atomic_load_n(&th_g_ms, __ATOMIC_ACQUIRE)" in c and "th_g_ms = now" not in c and "t = th_g_ms" not in c

    @staticmethod
    def muts_core():
        H, C, I = "sweet_thermal.h", "sweet_thermal.c", "sweet_chg_i2c.c"
        return {
            "x1_drop_49": (H, "#define TH_X1_DROP_DC       480", "#define TH_X1_DROP_DC       490"),
            "x1_restore_45": (H, "#define TH_X1_RESTORE_DC    440", "#define TH_X1_RESTORE_DC    450"),
            "chg_hold_46": (H, "#define TH_CHG_HOLD_DC      450", "#define TH_CHG_HOLD_DC      460"),
            "chg_release_43": (H, "#define TH_CHG_RELEASE_DC   420", "#define TH_CHG_RELEASE_DC   430"),
            "crit_56": (H, "#define TH_CRIT_DC          550", "#define TH_CRIT_DC          560"),
            "crit_no_confirm": (H, "#define TH_CRIT_CONFIRM     2u", "#define TH_CRIT_CONFIRM     1u"),
            "mask_0x1c": (H, "#define TH_X1_DROP_MASK     0x18u", "#define TH_X1_DROP_MASK     0x1cu"),
            "period_30s": (H, "#define TH_PERIOD_MS        10000u", "#define TH_PERIOD_MS        30000u"),
            "charging_ge_0": (H, "dC >= TH_CHG_HOLD_DC && cur_uA > 0", "dC >= TH_CHG_HOLD_DC && cur_uA >= 0"),
            "hold_without_charging": (H, "dC >= TH_CHG_HOLD_DC && cur_uA > 0", "dC >= TH_CHG_HOLD_DC"),
            "x1_drop_gt": (H, "dC >= TH_X1_DROP_DC", "dC > TH_X1_DROP_DC"),
            "x1_restore_lt": (H, "dC <= TH_X1_RESTORE_DC", "dC < TH_X1_RESTORE_DC"),
            "chg_release_lt": (H, "dC <= TH_CHG_RELEASE_DC", "dC < TH_CHG_RELEASE_DC"),
            "crit_gt": (H, "if (dC >= TH_CRIT_DC) {", "if (dC > TH_CRIT_DC) {"),
            "no_sanity_max": (H, "(dC < TH_SANE_MIN_DC || dC > TH_SANE_MAX_DC)", "(dC < TH_SANE_MIN_DC)"),
            "no_sanity_min": (H, "(dC < TH_SANE_MIN_DC || dC > TH_SANE_MAX_DC)", "(dC > TH_SANE_MAX_DC)"),
            "failure_acts": (H, "\t\tth_gap(c, now_ms,\n\t\t    rd == TH_RD_NOTREADY", "\t\tdC = TH_X1_RESTORE_DC; rd = TH_RD_OK; th_gap(c, now_ms,\n\t\t    rd == TH_RD_NOTREADY"),
            "busy_not_retried_hold": (H, "\t\tif (r == 2) {\n\t\t\tc->chg_held = 0;", "\t\tif (r == 99) {\n\t\t\tc->chg_held = 0;"),
            "crit_refused_not_retried": (H, "\t\t\tc->crit_req = 0;        /* refused: stay loud, ask again at the next sample */", "\t\t\t;"),
            "no_stale_line": (H, "now_ms - c->last_ok_ms > TH_STALE_MS", "0"),
            "gap_every_sample": (H, "if (c->gap_said && now_ms - c->last_gap_ms < TH_GAP_EVERY_MS) {", "if (0) {"),
            "no_periodic_log": (H, "now_ms - c->last_log_ms >= TH_LOG_EVERY_MS", "0"),
            "no_state_change_log": (H, "if (st != c->last_state || c->last_log_ms == 0", "if (c->last_log_ms == 0"),
            "glue_drop_mask_wrong": (C, "uint32_t m = drop ? (th_boot_mask & TH_X1_DROP_MASK) : th_boot_mask;", "uint32_t m = drop ? th_boot_mask : th_boot_mask;"),
            "glue_restore_to_drop_mask": (C, ": th_boot_mask;\n\n\t(void)dC;", ": TH_X1_DROP_MASK;\n\n\t(void)dC;"),
            "glue_kick_early": (C, "now - t0 >= TH_KICK_MS", "now - t0 >= 1000u"),
            "glue_no_initial_delay": (C, "for (i = 0; i < 20; i++) {", "for (i = 0; i < 0; i++) {"),
            "glue_disabled_still_threads": (C, "if (!sweet_thermal_enabled()) {\n\t\tif (th_ramp_arg(&rv)) {", "if (0) {\n\t\tif (th_ramp_arg(&rv)) {"),
            "glue_default_off": (C, "th_on = !(PE_parse_boot_argn(\"sweetthermal\", &v, sizeof(v)) && v == 0);", "th_on = (PE_parse_boot_argn(\"sweetthermal\", &v, sizeof(v)) && v == 1);"),
            "glue_publish_stale_only_on_ok": (C, "if (rd == TH_RD_OK && th_c.last_ok_ms == now) {", "if (rd != TH_RD_BUSY) {"),
                        # "i2c_restore_keeps_arm" (restore without !p3_therm_arm) is an EQUIVALENT mutant since review F1: a hold is never bounded, so `therm` already admits every write `restore` would
            "i2c_therm_no_arm_needed": (I, "int therm = p3_therm_arm && p3_mode", "int therm = p3_mode"),
            "i2c_therm_unbounded": (I, "(p3_twrites < THERM_MAX_WRITES && val == p3_intended", "(val == p3_intended"),
            "i2c_hold_bounded_too": (I, "((val == p3_orig && cur == p3_intended) || (p3_twrites", "(p3_twrites < THERM_MAX_WRITES && (val == p3_orig && cur == p3_intended)) || (p3_twrites"),
            "i2c_therm_any_value": (I, "((val == p3_orig && cur == p3_intended) || (p3_twrites < THERM_MAX_WRITES && val == p3_intended && cur == p3_orig));", "1;"),
            "i2c_release_bound_check_removed": (I, "} else if (!hold && p3_twrites >= THERM_MAX_WRITES) {", "} else if (0) {"),
            "i2c_release_bound_check_off_by_one": (I, "} else if (!hold && p3_twrites >= THERM_MAX_WRITES) {", "} else if (!hold && p3_twrites > THERM_MAX_WRITES) {"),
            "i2c_sample_first_gate_zero": (I, "} else if (!i2c_gate(!i2c_xfer_seen)) {\n\t\trc = -1;", "} else if (!i2c_gate(0)) {\n\t\trc = -1;"),
            "i2c_sample_never_marks_seen": (I, "\t\ti2c_xfer_seen = 1;\n\t\t*cur_uA", "\t\t*cur_uA"),
            "i2c_p2_never_marks_seen": (I, "i2c_xfer_seen = 1;      /* patch 0336 F2: the first-transfer checks of i2c_gate(1) have been passed once */", ";"),
            "i2c_lever_gate_closed_minus1": (I, "rc = 2;         /* patch 0336 F3: transient", "rc = -1;        /* patch 0336 F3: transient"),
            "i2c_sample_no_established_check": (I, "!i2c_loaded || !sweet_chg_gate_established()", "!i2c_loaded"),
            "policy_no_retry_on_fail": (H, "} else if (r < 0 && c->chg_retry < TH_CHG_RETRY_MAX) {", "} else if (0) {"),
            "policy_retry_unbounded": (H, "} else if (r < 0 && c->chg_retry < TH_CHG_RETRY_MAX) {", "} else if (r < 0) {"),
            "policy_retry_not_reset_on_release": (H, "\t\tc->chg_held = 0;\n\t\tc->chg_retry = 0;\n\t\tth_fmt(lim, sizeof(lim), TH_CHG_RELEASE_DC);", "\t\tc->chg_held = 0;\n\t\tth_fmt(lim, sizeof(lim), TH_CHG_RELEASE_DC);"),
            "policy_retry_not_reset_on_success": (H, "} else if (r >= 0) {\n\t\t\tc->chg_retry = 0;\n\t\t}", "}"),
            "order_accepts_a55": (H, "(phys[i] != 4u && phys[i] != 5u)", "(phys[i] != 4u && phys[i] != 5u && phys[i] != 3u)"),
            "order_ignores_cpu_count": (H, "if (ncpu != TH_X1_DROP_NCPU) {", "if (0) {"),
            "order_unknown_topology_silent": (H, "return g | 4u;", "return g;"),
            "glue_kick_log_pending_0": (C, "sweet_chg_tick(1);      /* the USB log thread never", "sweet_chg_tick(0);      /* the USB log thread never"),
            "glue_order_check_skipped": (C, "\tth_check_cpu_order(ncpu);\n", "\tif (0) { th_check_cpu_order(ncpu); }\n"),
            "glue_phys_shift_wrong": (C, "(ti->cpus[i].phys_id >> 8) & 0xffu", "(ti->cpus[i].phys_id) & 0xffu"),
            "i2c_therm_counts_nwrites": (I, "\tif (therm) {\n\t\tp3_twrites++;\n\t} else {\n\t\tp3_nwrites++;\n\t}", "\tp3_twrites++;\n\tp3_nwrites++;"),
            "i2c_release_no_link_check": (I, "|| sweet_usb_ss_configured() == 0 || p3_chgtyp_ok() != 1)) {", "|| 0)) {"),
            "i2c_hold_no_foreign_check": (I, "} else if (c9 != (hold ? p3_intended : p3_orig)) {", "} else if (0) {"),
            "i2c_step_ignores_hold": (I, "\tif (p3_therm_hold) {\n\t\treturn p3_refuse(", "\tif (0) {\n\t\treturn p3_refuse("),
            "i2c_release_ignores_watcher_end": (I, "} else if (!hold && (p3_stage >= 9 ||", "} else if (!hold && (0 ||"),
            "i2c_sample_no_gate": (I, "} else if (!i2c_gate(!i2c_xfer_seen)) {\n\t\trc = -1;        /* GAP printed by i2c_gate; transient", "} else if (0) {\n\t\trc = -1;        /* GAP printed by i2c_gate; transient"),
            "i2c_sample_inten_left_armed": (I, "\t} else {\n\t\t(void)i2c_wr(I_INTEN, 0);\n\t\ti2c_xfer_seen = 1;", "\t} else {\n\t\ti2c_xfer_seen = 1;"),
            "i2c_sample_dead_ignored": (I, "} else if (i2c_dead) {\n\t\trc = -2;", "} else if (0) {\n\t\trc = -2;"),
            "i2c_sample_wrong_temp_scale": (I, "*deci_c = ((int32_t)(int16_t)(tc[0] | ((uint32_t)tc[1] << 8)) * 10) / 256;", "*deci_c = ((int32_t)(int16_t)(tc[0] | ((uint32_t)tc[1] << 8)) * 100) / 256;"),
            "i2c_sample_busy_ignored": (I, "\tif (!sweet_chg_enter()) {\n\t\treturn 2;\n\t}\n\tif (i2c_group < 9) {", "\tif (i2c_group < 9) {"),
        }

    @staticmethod
    def muts_ramp():
        H, C, I = "sweet_thermal.h", "sweet_thermal.c", "sweet_chg_i2c.c"
        return {
            "ramp_published_biased": (C, "th_g_cc = th_c.last_real_dC * 10;", "th_g_cc = th_c.last_dC * 10;"),
            "ramp_start_at_2": (H, "#define TH_RAMP_START_N     3u", "#define TH_RAMP_START_N     2u"),
            "ramp_start_at_4": (H, "#define TH_RAMP_START_N     3u", "#define TH_RAMP_START_N     4u"),
            "ramp_bias_off_by_one": (H, "(c->samples - (TH_RAMP_START_N - 1u))", "(c->samples - TH_RAMP_START_N)"),
            "ramp_bias_cap_removed": (H, "b > TH_RAMP_BIAS_CAP_DC ? TH_RAMP_BIAS_CAP_DC : b", "b"),
            "ramp_max_101": (H, "#define TH_RAMP_MAX_DC      100", "#define TH_RAMP_MAX_DC      101"),
            "ramp_min_0": (H, "#define TH_RAMP_MIN_DC      1 ", "#define TH_RAMP_MIN_DC      0 "),
            "ramp_set_accepts_zero": (H, "dC_per_sample < TH_RAMP_MIN_DC || dC_per_sample > TH_RAMP_MAX_DC", "dC_per_sample > TH_RAMP_MAX_DC"),
            "ramp_set_accepts_huge": (H, "dC_per_sample < TH_RAMP_MIN_DC || dC_per_sample > TH_RAMP_MAX_DC", "dC_per_sample < TH_RAMP_MIN_DC"),
            "ramp_failing_sensor_silent": (H, "if (c->ramp_dC && !c->ramp_gap_said) {\n\t\t\tc->ramp_gap_said = 1;\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: sweetthermalramp (test only) refused on this sample: the sensor read",
                                            "if (0 && !c->ramp_gap_said) {\n\t\t\tc->ramp_gap_said = 1;\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: sweetthermalramp (test only) refused on this sample: the sensor read"),
            "ramp_implausible_silent": (H, "if (c->ramp_dC && !c->ramp_gap_said) {\n\t\t\tc->ramp_gap_said = 1;\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: sweetthermalramp (test only) refused on this sample: the sensor value",
                                           "if (0 && !c->ramp_gap_said) {\n\t\t\tc->ramp_gap_said = 1;\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: sweetthermalramp (test only) refused on this sample: the sensor value"),
            "ramp_gap_flag_never_reset": (H, "\tc->ramp_gap_said = 0;\n\tc->have_temp = 1;", "\tc->have_temp = 1;"),
            "ramp_no_per_sample_line": (H, "|| c->ramp_dC || now_ms - c->last_log_ms >= TH_LOG_EVERY_MS", "|| now_ms - c->last_log_ms >= TH_LOG_EVERY_MS"),
            "ramp_real_value_stored_wrong": (H, "\tc->last_real_dC = real;", "\tc->last_real_dC = real + 1;"),
            "ramp_start_line_every_sample": (H, "if (c->samples == TH_RAMP_START_N) {", "if (c->samples >= TH_RAMP_START_N) {"),
            "ramp_tag_missing_in_crit": (H, "TH_LOG(\"SWEET THERMAL CRIT: batt %s C%s >= %s C on %u samples in a row: requesting the orderly reboot to the bootloader so the phone cools in fastboot\\n\", t, tag, lim,",
                                         "TH_LOG(\"SWEET THERMAL CRIT: batt %s C%s >= %s C on %u samples in a row: requesting the orderly reboot to the bootloader so the phone cools in fastboot\\n\", t, \"\", lim,"),
            "ramp_stale_shows_biased": (H, "th_fmt(t, sizeof(t), c->last_real_dC);\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: battery temperature STALE", "th_fmt(t, sizeof(t), c->last_dC);\n\t\t\tTH_LOG(\"SWEET THERMAL GAP: battery temperature STALE"),
            "nochg_silent": (H, "} else if (!c->chg_held && dC >= TH_CHG_HOLD_DC) {", "} else if (0) {"),
            "nochg_every_sample": (H, "if (!c->nochg_said || now_ms - c->last_nochg_ms >= TH_LOG_EVERY_MS) {", "if (1) {"),
            "nochg_once_forever": (H, "if (!c->nochg_said || now_ms - c->last_nochg_ms >= TH_LOG_EVERY_MS) {", "if (!c->nochg_said) {"),
            "nochg_flag_not_reset_when_cool": (H, "if (dC < TH_CHG_HOLD_DC) {\n\t\tc->nochg_said = 0;", "if (0) {\n\t\tc->nochg_said = 0;"),
            "glue_ramp_ignored_when_disabled_silent": (C, "\t\tif (th_ramp_arg(&rv)) {\n\t\t\tprintf(", "\t\tif (0) {\n\t\t\tprintf("),
            "glue_ramp_wrong_arg_name": (C, "PE_parse_boot_argn(\"sweetthermalramp\", v,", "PE_parse_boot_argn(\"sweetthermalrmp\", v,"),
            "glue_ramp_never_armed": (C, "if (th_ramp_set(&th_c, rv) == 0) {", "if (th_ramp_set(&th_c, 0) == 0) {"),
            "glue_ramp_loud_line_missing": (C, "a CRIT park is expected", "ok"),
        }

    @staticmethod
    def muts_tail():
        I = "sweet_chg_i2c.c"
        return {
            "i2c_engine_thermal_off_loads": (I, "if (sweet_thermal_enabled()) {\n\t\t\t\t/* patch 0336", "if (0) {\n\t\t\t\t/* patch 0336"),
        }

    def test_mutants_fail(self):
        H, C = "sweet_thermal.h", "sweet_thermal.c"
        muts = {**self.muts_core(), **self.muts_ramp(), **self.muts_tail()}
        survivors = []
        for name, (f, old, new) in muts.items():
            d = self.mutate(f, old, new)
            if not self.killed(d):
                survivors.append(name)
        self.assertEqual(survivors, [], "mutants that no case caught")
        # review 0336 F8 (memory ordering is not observable on the host): the text lint must reject the plain-store / plain-load forms
        lint_muts = {
            "glue_publish_plain_store": (C, "__atomic_store_n(&th_g_ms, now ? now : 1, __ATOMIC_RELEASE);", "th_g_ms = now ? now : 1;"),
            "glue_publish_ms_before_value": (C, "th_g_cc = th_c.last_real_dC * 10;\n\t\t\tth_g_state = (uint32_t)th_state(&th_c);\n\t\t\t__atomic_store_n(&th_g_ms, now ? now : 1, __ATOMIC_RELEASE);",
                                             "__atomic_store_n(&th_g_ms, now ? now : 1, __ATOMIC_RELEASE);\n\t\t\tth_g_cc = th_c.last_real_dC * 10;\n\t\t\tth_g_state = (uint32_t)th_state(&th_c);"),
            "glue_get_plain_load": (C, "__atomic_load_n(&th_g_ms, __ATOMIC_ACQUIRE)", "th_g_ms"),
        }
        for name, (f, old, new) in lint_muts.items():
            self.assertFalse(self.f8_ok(self.mutate(f, old, new)), "F8 lint mutant not caught: " + name)
        CASES.append("mutants=%d" % (len(muts) + len(lint_muts)))


if __name__ == "__main__":
    ok = unittest.main(exit=False, verbosity=1).result.wasSuccessful()
    print("RESULT: %s sweet-thermal cases=%d" % ("PASS" if ok else "FAIL", len(CASES)))
    sys.exit(0 if ok else 1)
