#!/usr/bin/env python3
"""Mutant tables of tools/safety_lib.py and tools/safety-check (run here; needles checked by tools/lint-mutant-needles).

    python3 tools/test_safety_mutants.py   -> RESULT: PASS safety-mutants mutants=N killed=N | RESULT: FAIL ...

Each row (name, needle, replacement): the needle occurs exactly once in its file; the mutated copy (in a temp dir with the tools it imports)
must make tools/test_safety_check.py fail. A survivor is a test gap; a needle that is gone is a FAIL here and in lint-mutant-needles.
"""
import os, shutil, subprocess, sys, tempfile, unittest

if __name__ == "__main__" and sys.argv[1:2] in (["-h"], ["--help"]):   # T-7dff:help
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_SRC = os.path.join(HERE, "safety_lib.py")
CLI_SRC = os.path.join(HERE, "safety-check")
COPY = ("safety_lib.py", "safety-check", "simseams.py", "phone_lock_end.py", "phone_lock_root.py")

# mutant-block: safety-lib
LIB_MUTANTS = [
    ("s2_hot_passes", "        return False, \"%s; %s < %d s and no cooler reading since", "        return True, \"%s; %s < %d s and no cooler reading since"),
    ("s2_noread_passes", '    return False, "no temperature reading known and %s < %d s (never assumed cool)"', '    return True, "no temperature reading known and %s < %d s (never assumed cool)"'),
    ("s2_no_defer", "                why, pk, COOL_PARK_S, COOL_C, COOL_C), True", "                why, pk, COOL_PARK_S, COOL_C, COOL_C), False"),
    ("s1_gt", "self.last_temp >= HOT_C", "self.last_temp > HOT_C"),
    ("s1_off", "if self.last_temp is not None and self.last_temp >= HOT_C:", "if False:"),
    ("s2_first_off", "if self.defer_s2 and self.first_temp is not None", "if False and self.first_temp is not None"),
    ("s4_first_off", "if self.defer_s4 and self.socs and", "if False and self.socs and"),
    ("s4_cut_off", "if soc is not None and soc < SOC_CUT:", "if False:"),
    ("s4_vcell_off", "if mv is not None and mv < VCELL_MIN:", "if False:"),
    ("s4_no_median", "    return statistics.median(vals[-3:]) if vals else None", "    return vals[-1] if vals else None"),
    ("s6_first_off", "if now - self.t_boot > ALIVE_FIRST_S:", "if False:"),
    ("s6_gap_off", "if now - self.last_line > ALIVE_GAP_S:", "if False:"),
    ("s6_reset_off", "if now - self.gone_at > RESET_BACK_S:", "if False:"),
    ("s6_gadget_unseen", "if self.gone_at is None and self.gadget_seen and", "if self.gone_at is None and"),
    ("s3_first_off", "if now - self.t_boot > TEMP_FIRST_S:", "if False:"),
    ("s3_p3_off", "                if now - self.first_line > TEMP_GAP_S:", "                if False:"),
    ("s3_p3_ignored", "            if self.p3_temp:", "            if False:"),
    ("s3_gap_off", "if now - self.last_temp_at > TEMP_GAP_S:", "if False:"),
    ("hot_not_marked", "kv.update(hot=True, hot_why=why[:300], hot_at=now)", "pass"),
    ("p2_ignored", "    m = P2_RX.search(text)\n    if m:", "    m = None\n    if m:"),
    ("unsafe_ok", 'return v.startswith("SAFE") and "UNSAFE" not in v', 'return v.startswith("SAFE") or "UNSAFE" in v'),
    ("no_patch_marks", "            if MARK_RX.search(head):", "            if False:"),
    ("no_commit_marks", "        if MARK_RX.search(subj):", "        if False:"),
    ("monitor_gone_ok", 'why = why or "SAFETY FAIL MON: the safety monitor', 'why = why and "SAFETY FAIL MON: the safety monitor'),
    ("report_no_pre_ok", '        if not run.get("safety_pre"):\n            res, why = "FAIL"', '        if False:\n            res, why = "FAIL"'),
    ("report_no_mon_ok", '        elif not run.get("safety_monitor_pid"):', '        elif False:'),
    ("report_old_runs_fail", '    if run.get("safety") != "required":\n        return None, "", lines', '    if False:\n        return None, "", lines'),
    ("done_is_dead", '            if os.path.exists(p + ".done"):   # the monitor ended normally', '            if False:   # the monitor ended normally'),
    ("park_from_history_off", "        cands += [os.path.getmtime(p) for p in ends if os.path.exists(p)]", "        pass"),
]

# mutant-block: safety-cli
CLI_MUTANTS = [
    ("s4_low_passes", '        return False, "%s below soc %.1f %% / vcell %d mV; parked %s < %d s"', '        return True, "%s below soc %.1f %% / vcell %d mV; parked %s < %d s"'),
    ("s3_kernel_ignored", "    if L.TEMP_FMT not in kb:", "    if False:"),
    ("s3_chg_ignored", '    chg = argval(bl, "sweetchgi2c") == "1" and pint(argval(bl, "sweetchgicl")) > 0', "    chg = True"),
    ("s3_p3_state_lost", "    return items, d2, d4, L.P3_TEMP_FMT in kb", "    return items, d2, d4, False"),
    ("s5_trunk_ignored", "        if on is True:", "        if False:"),
    ("s5_wdt_arg_ignored", "    if early and wdt > 0:", "    if early:"),
    ("s6_usbnet_ignored", 'items.append(("S6", a.usbnet == "1",', 'items.append(("S6", True,'),
    ("s7_len_off", "    if n > lim - 1:", "    if n > lim + 100:"),
    ("s7_tag_mismatch_ok", "    if shim is not None and shim != lim:", "    if False:"),
    ("s7_refused_ok", "        if ref:\n            return (\"S7\", False", "        if False:\n            return (\"S7\", False"),
    ("s8_writes_ok", '    bad = [w for w in a.writes if w != "userdata"]', "    bad = []"),
    ("s8_guard_ok", "            if needle not in open(os.path.join(a.tree, f)).read():", "            if False:"),
    ("s9_unsafe_ok", "        if not L.is_safe_verdict(v):", "        if False:"),
    ("pre_fail_exit0", "    if bad:\n        out(\"RESULT: FAIL safety-check pre", "    if False:\n        out(\"RESULT: FAIL safety-check pre"),
    ("fb_product_ok", '    if prod != "bluejay":', "    if False:"),
    ("fb_vcell_ok", "    if mv is not None and mv < L.VCELL_MIN:", "    if False:"),
    ("fb_storage_ok", "    if sc is not None and sc >= L.COOL_C:", "    if False:"),
    ("fb_seam_free", '        simseams.require_sandbox(["SAFETY_FASTBOOT"])', "        pass"),
    ("mon_seam_free", '        simseams.require_sandbox(["SAFETY_TBOOT_BACKDATE"])', "        pass"),
    ("mon_no_stopfile", "            with open(stop, \"w\") as f:\n                f.write(why + \"\\n\")", "            pass"),
    ("mon_fail_exit0", "            out(why)\n            return 1", "            out(why)\n            return 0"),
    ("mon_no_temp_state", "                kv.update(temp_c=m.last_temp, temp_at=m.last_temp_at)", "                pass"),
    ("mon_never_clears", "                kv.update(hot=False, low=False, defer_s2=False, defer_s4=False)", "                pass"),
    ("stamp_any_sha", '    if s.get("sha256") != h:', "    if False:"),
    ("mon_no_done", '            with open(stop + ".done", "w") as f:', '            with open(os.devnull, "w") as f:'),
    ("notify_no_log", "    L.human_needed(a.state, a.line, now)", "    pass"),
]


def run_one(src, needle, new):
    d = tempfile.mkdtemp(prefix="safetymut_")
    try:
        for f in COPY:
            shutil.copy(os.path.join(HERE, f), os.path.join(d, f))
        p = os.path.join(d, os.path.basename(src))
        s = open(p).read()
        if s.count(needle) != 1:
            return "needle count %d" % s.count(needle)
        open(p, "w").write(s.replace(needle, new))
        env = dict(os.environ, SAFETY_CHECK_TOOL=os.path.join(d, "safety-check"), SAFETY_LIB_DIR=d)
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "test_safety_check.py")], env=env, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return "killed"   # a hang counts as killed
        return "killed" if r.returncode != 0 else "survived"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    from concurrent.futures import ThreadPoolExecutor
    rows = [(LIB_SRC,) + m for m in LIB_MUTANTS] + [(CLI_SRC,) + m for m in CLI_MUTANTS]
    with ThreadPoolExecutor(max_workers=int(os.environ.get("SAFETY_MUT_JOBS") or 4)) as ex:
        res = list(ex.map(lambda r: (r[1], run_one(r[0], r[2], r[3])), rows))
    bad = [(n, v) for n, v in res if v != "killed"]
    for n, v in bad:
        print("MUTANT %s %s" % (n, v.upper()))
    print("RESULT: %s safety-mutants mutants=%d killed=%d" % ("PASS" if not bad else "FAIL", len(res), len(res) - len(bad)))
    return 1 if bad else 0


class MutantTest(unittest.TestCase):
    def test_every_mutant_killed(self):   # the gate's unit step runs this (unittest discover)
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
