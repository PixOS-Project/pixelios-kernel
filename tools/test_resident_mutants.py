#!/usr/bin/env python3
"""Mutant tables of tools/resident_core.py and tools/resident_session_lib.py (run by tools/test_resident.py; needles checked by tools/lint-mutant-needles).

    python3 tools/test_resident_mutants.py   -> RESULT: PASS resident-mutants mutants=N killed=N | RESULT: FAIL ...

Each row (name, needle, replacement): the needle occurs exactly once in its file; the mutated module must make at least one case of
tools/test_resident.py fail (a mutant that raises or hangs past the fake-clock ceiling counts as killed).
"""
import contextlib, importlib.util, io, os, sys, unittest

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):   # T-7dff:help: answers before anything runs
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CORE_SRC = os.path.join(HERE, "resident_core.py")
SESSION_SRC = os.path.join(HERE, "resident_session_lib.py")
FIT_SRC = os.path.join(HERE, "resident_fit.py")
THERMAL_SRC = os.path.join(HERE, "resident_thermal.py")

# mutant-block: resident-core
CORE_MUTANTS = [
    ("no_prio", 'return (eff_prio(job, now, cfg), job["est_s"], job["submitted"], job["id"])', 'return (job["est_s"], job["submitted"], job["id"])'),
    ("no_sjf", 'return (eff_prio(job, now, cfg), job["est_s"], job["submitted"], job["id"])', 'return (eff_prio(job, now, cfg), job["submitted"], job["id"])'),
    ("no_aging", 'return job["prio"] - int(waited // cfg["aging_s"])', 'return job["prio"]'),
    ("compat_ignores_kernel", '    if job_kernel_sha(job, std_sha) != boot["kernel_sha"]:\n        return False\n', ''),
    ("compat_ignores_args", '        elif k not in boot["args"] or boot["args"][k] != v:\n            return False\n', '        else:\n            pass\n'),
    ("compat_ignores_files", '        if boot["overlay_files"].get(p) != h:\n            return False\n', '        pass\n'),
    ("merge_ignores_file_conflict", '        if p in files and files[p] != h:\n            return None, "overlay file %s differs" % p\n', ''),
    ("no_floor", '            if mv < c["floor_mv"]:\n                return "park"', '            if False:\n                return "park"'),
    ("no_falling", '        if m is not None and m < 0 and not no_falling:', '        if False:'),
    ("falling_counts_busy", 'and not s[3]]', ']'),
    ("no_median", '        tail = sorted((self.comp_mv(s[1], s[2]), s[1], s[2]) for s in self.samples[-3:])\n        return tail[len(tail) // 2]', '        s = self.samples[-1]\n        return (self.comp_mv(s[1], s[2]), s[1], s[2])'),
    ("raw_no_median", '        tail = sorted(s[1] for s in self.samples[-3:])\n        return tail[len(tail) // 2]', '        return self.samples[-1][1]'),
    ("soc_ignored", '        if self.samples and self.samples[-1][4] is not None:\n            return "soc"', '        if False:\n            return "soc"'),
    ("soc_no_floor", '            if soc < c["floor_soc"]:', '            if False:'),
    ("soc_no_median", '        tail = sorted(s[4] for s in self.samples[-3:] if s[4] is not None)', '        tail = [s[4] for s in self.samples[-1:] if s[4] is not None]'),
    ("soc_no_cut", 'if soc < self.cfg["cut_soc"] else ""', 'if False else ""'),
    ("soc_cut_falls_to_comp", '        if soc is not None:\n            return ("battery soc', '        if False:\n            return ("battery soc'),
    ("soc_no_charge_wait", '(self.last_soc() < c["floor_soc"] + c["start_margin_soc"]) if mode == "soc"', '(False) if mode == "soc"'),
    ("p2_ignored", '            if mode == "vcell+p2" and self.boot_soc < c["floor_soc"]:', '            if False:'),
    ("rows_no_soc", '(?: soc=(\\d+)\\.(\\d\\d) %)?', ''),
    ("mif_no_limit", 'and len(line.encode()) > lim:', 'and False:'),
    ("psrc_no_room", 'PSRC_TOK_CHARS = 50', 'PSRC_TOK_CHARS = 0'),
    ("psrc_both_ignored", '    if mif and psrc:\n        return (MIF_PSCLR_LINE_MAX if clr else MIF_PSRC_LINE_MAX)', '    if False:\n        return (MIF_PSCLR_LINE_MAX if clr else MIF_PSRC_LINE_MAX)'),
    ("psclr_token_not_reserved", 'PSCLR_TOK_CHARS = 79', 'PSCLR_TOK_CHARS = 50'),
    ("psclr_flag_ignored", '    mif, clr = any(f in xa for f in MIF_FLAGS), "--pmic-src-clear" in xa', '    mif, clr = any(f in xa for f in MIF_FLAGS), False'),
    ("mif_pair_ignored", 'MIF_FLAGS = ("--mif-clk", "--mif-int-pair", "--mif-clk-level")', 'MIF_FLAGS = ("--mif-clk",)'),
    ("mif_off_by_one", 'MIF_LINE_MAX = LINE_MAX - 128 ', 'MIF_LINE_MAX = LINE_MAX - 127 '),
    ("g2_any_session", '        return "no --profile/--kernel and no running session boot to inherit"', '        return ""'),
    ("g2_no_switch_check", 'if job["kind"] == "kernel" and (cfg["default_profile"], std_sha) != (boot_info.get("profile"), boot_info.get("kernel_sha")):', 'if False:'),
    ("g3_no_table", '        for lit in KERNEL_NEEDS.get(prog, []):', '        for lit in []:'),
    ("g4_no_check", 'if k not in LIST_ARGS and k in profile_args and profile_args[k] != v]', 'if False]'),
    ("notice_former_ignored", '        if submitter and submitter in former:', '        if False:'),
    ("notice_key_ignored", '    if owner_key:\n        cur = (owners.get(owner_key)', '    if False:\n        cur = (owners.get(owner_key)'),
    ("soc_falling_any_current", '        if mode == "soc":   # T-17', '        if False:   # T-17'),
    ("soc_falling_no_horizon", 'return rate, self.last_soc() - rate * self.cfg["falling_horizon_s"]', 'return rate, self.last_soc()'),
    ("soc_falling_counts_settle", 't0 = max(now - self.cfg["falling_window_s"], self.cfg["settle_s"] + (boot_up_at or 0))', 't0 = now - self.cfg["falling_window_s"]'),
    ("fresh_shares_boot", '    if s.get("fresh") and boot.get("for") != job["id"]:', '    if False:'),
    ("fresh_merges_others", '        return nxt, [seed["id"]], [(j["id"], "%s boot for %s" % ("fresh" if seed["spec"].get("fresh") else "solo", seed["id"])) for j in jobs[1:]]', '        pass'),
    ("merge_any_args", '        if j is not seed and not same_args(j["spec"].get("args"), seed["spec"].get("args")):', '        if False:'),
    ("compat_subset_line", '    if (job["kind"] == "kernel" or s["profile"] != "any") and not same_args(s.get("args"), boot["args"]):', '    if False:'),
    ("same_args_list_order", 'frozenset(_names(v)) if k in LIST_ARGS else v', 'v'),
    ("solo_seed_merges", 'if seed["spec"].get("fresh") or seed.get("solo"):', 'if seed["spec"].get("fresh"):'),
    ("solo_other_merges", '        if j.get("solo"):   # T-51', '        if False:   # T-51'),
    ("no_comp", '        return int(round(mv - ua * self.cfg["r_int_mohm"] / 1e6))', '        return mv'),
    ("comp_sign", '        return int(round(mv - ua * self.cfg["r_int_mohm"] / 1e6))', '        return int(round(mv + abs(ua) * self.cfg["r_int_mohm"] / 1e6))'),
    ("no_raw_hard", '        return mv is not None and mv < self.cfg["vcell_hard_mv"]', '        return False'),
    ("cut_raw_vcell", '        if r[0] < self.cfg["floor_mv"] - self.cfg["cut_margin_mv"]:', '        if r[1] < self.cfg["floor_mv"] - self.cfg["cut_margin_mv"]:'),
    ("no_no_data", '            if boot_up_at is not None and now - boot_up_at > c["no_data_s"]:', '            if False:'),
    ("no_stale", '        if boot_up_at is not None and now - self.samples[-1][0] > c["no_data_s"]:', '        if False:'),
    ("nodata_runs_here", '            if not other:\n                return ("park", why)', '            if True:\n                return ("wait", why)'),
    ("nodata_never_reboots", '            spec, batch, left = plan_boot(other, now, cfg, std_kernel, std_sha)\n            return ("reboot", spec, batch, left)', '            return ("park", why)'),
    ("falling_counts_settle", 'since = max(now - self.cfg["falling_window_s"], (boot_up_at or 0) + self.cfg["settle_s"])', 'since = now - self.cfg["falling_window_s"]'),
    ("no_falling_always", 'and m < 0 and not no_falling:', 'and m < 0 and False:'),
    ("no_falling_ignored", 'no_falling = any(r.get("no_falling") and r["from"] <= now < r["to"] for r in reserves or [])', 'no_falling = False'),
    ("no_falling_anytime", 'no_falling = any(r.get("no_falling") and r["from"] <= now < r["to"] for r in reserves or [])', 'no_falling = any(r.get("no_falling") for r in reserves or [])'),
    ("no_charge_wait", 'else (mv < c["floor_mv"] + c["start_margin_mv"]):', 'else (False):'),
    ("no_charge_wait_max", '            if now - self.charge_wait_since > c["charge_wait_max_s"]:', '            if False:'),
    ("no_cut", '        if r[0] < self.cfg["floor_mv"] - self.cfg["cut_margin_mv"]:', '        if False:'),
    ("no_idle_park", '        if boot is not None and idle_since is not None and now - idle_since > cfg["idle_park_s"]:', '        if False:'),
    ("charge_wait_runs", '    if v == "charge-wait":\n        return ("wait", why)\n', ''),
    ("no_kernel_window", '            if not runnable and not kernel_due and not prof_due and all(j["kind"] == "kernel" for j in waiting):', '            if False:'),
    ("kernel_never_due", 'kernel_due = [j for j in waiting if j["kind"] == "kernel" and now - j["submitted"] >= cfg["kernel_batch_s"]]', 'kernel_due = []'),
    ("no_switch_wait", 'prof_due = [j for j in waiting if now - j["submitted"] >= cfg["switch_wait_s"]]', 'prof_due = waiting'),
    ("no_lock_room", 'runnable = [j for j in pending if compatible(j, boot, std_sha) and j["max_s"] <= room]', 'runnable = [j for j in pending if compatible(j, boot, std_sha)]'),
    ("sweetnull_conflicts", '        if k in LIST_ARGS:\n            have = _names(args.get(k))', '        if False:\n            have = _names(args.get(k))'),
    ("sweetnull_compat_strict", '            if not set(_names(v)) <= set(_names(boot["args"].get(k))):', '            if v != boot["args"].get(k):'),
    ("camera_ignored", '    if s.get("camera") and not boot.get("camera"):', '    if False:'),
    ("camera_not_merged", '"camera": bool(spec.get("camera") or s.get("camera"))', '"camera": bool(spec.get("camera"))'),
    ("no_batch_first", '    if own:\n        return ("run", ordered(own, now, cfg)[0])', '    if False:\n        return ("run", ordered(own, now, cfg)[0])'),
    ("res_ignored", '            if r["from"] <= now < r["to"] and j["stream"] != r["stream"]:', '            if False:'),
    ("res_no_lookahead", '            elif now < r["from"] and j["stream"] != r["stream"] and now + j["max_s"] > r["from"]:', '            elif False:'),
    ("rows_drop_sign", 'current=(-?\\d+) uA', 'current=(\\d+) uA'),
    ("rows_no_mon", '"SWEET CHG (?:P3|MON): t=', '"SWEET CHG (?:P3): t='),
    ("dup_args_ok", '        if k in out and out[k] != val:\n            raise ValueError', '        if False:\n            raise ValueError'),
    ("cfg_any_key", '        if k not in DEFAULTS:\n            raise ValueError', '        if False:\n            raise ValueError'),
]


# mutant-block: resident-session
SESSION_MUTANTS = [
    ("mirror_lines_crash", 'for ln in (text or "").splitlines() if not rc.MIRROR_RX.search(ln)) if m), None)', 'for ln in (text or "").splitlines()) if m), None)'),
    ("infra_no_retry", 'if not bad and r != 0 and HOST_INFRA_RX.search(self.last_out or ""):\n                # devsh', 'if False:\n                # devsh'),
    ("infra_twice_is_fail", 'return "NOTRUN", "host infra twice', 'return "FAIL", "host infra twice'),
    ("refused_boot_no_why", 'return rf.ends_alone(self, label, batch, "boot refused: %s" % e, persist, event)', 'return rf.ends_alone(self, label, batch, "boot refused", persist, event)'),
    ("no_fit_check", '        if len(batch) > 1:   # T-51 rule 1', '        if False:   # T-51 rule 1'),
    ("no_host_fail", '            if hf:   # T-51 rule 2', '            if False:   # T-51 rule 2'),
    ("refused_crashes", '            return rf.ends_alone(self, label, batch, "boot refused: %s" % e, persist, event)', '            self.crash("refused")\n            return False'),
    ("host_uses_job_check", 'envx=self.hostenv, check=lim)', 'envx=self.hostenv)'),
    ("host_no_limit", 'if self.ph.now() > t_end else (', 'if False else ('),
    ("begin_before_log", '        if self.boot_feed and not self.boot_cut_begun and self.ph.log_exists(self.boot[1]):', '        if self.boot_feed and not self.boot_cut_begun and self.ph.run_dir(self.boot[1]):'),
    ("devd_timeout_default", 'self.env = dict(os.environ, PIXOS_DEVD_TIMEOUT=str(job["max_s"]))', 'self.env = dict(os.environ)'),
    ("no_start_notice", '        job_notify(self.ph, job, line)   # a job that needs a person', '        pass   # a job that needs a person'),
    ("crash_tells_nobody", '            job_notify(self.ph, j, "RESIDENT JOB REQUEUED id=%s: session %s crashed', '            (lambda *a: 0)(self.ph, j, "RESIDENT JOB REQUEUED id=%s: session %s crashed'),
    ("crash_ends_queue", '            jsave(p("jobs", j["id"], "job.json"), j)\n            job_notify', '            self.finish(j, "NOTRUN", "x", None)\n            continue\n            job_notify'),
    ("boot_crash_forever", '                if j["boot_crashes"] >= 2:', '                if False:'),
    ("boot_crash_not_counted", '            if during_boot and j["id"] in self.boot_batch:', '            if False:'),
    ("crash_no_restart", '            self.save()   # state=ended on disk first: the new start must not see this session as live\n            self.ph.restart_session()\n        return r', '            self.save()   # state=ended on disk first: the new start must not see this session as live\n        return r'),
    ("no_max_cut", '        if self.ph.now() - self.t0 > job["max_s"]:', '        if False:'),
    ("no_absent_check", '        for rx in self.job["expect_absent"]:', '        for rx in []:'),
    ("no_panic_scan", '        m = next((m for m in (re.search(PANIC_RX, ln)', '        m = None and next((m for m in (re.search(PANIC_RX, ln)'),
    ("park_keeps_jobs", '                self.end_all("NOTRUN", "session parked (%s): resubmit" % act[1])\n', ''),
    ("no_requeue_restart", '                if self.pending:\n                    self.ph.restart_session()', '                if False:\n                    self.ph.restart_session()'),
    ("step_fail_ignored", '            if r != 0:\n                return "FAIL"', '            if False:\n                return "FAIL"'),
    ("requeue_before_save", '                self.save()   # state=ended on disk first', '                pass   # state=ended on disk first'),
    ("stall_rx_too_wide", 'SWEET STALL: the usb-log drain thread did not run"', 'SWEET STALL"'),
    ("no_stall_rx", '|SWEET STALL: the usb-log drain thread did not run"', '"'),
    ("no_error_guard", '            return self.serve_loop()\n        except Exception as e:', '            return self.serve_loop()\n        except KeyboardInterrupt as e:'),
    ("no_boot_feed", '        for chunk in early or []:', '        for chunk in []:'),
    ("expect_never_checked", '            bad = self.oracles()', '            bad = None'),
    ("no_boot_cut_begin", '                _, out = self.ph.cut(self.boot[1], "begin", "rj-" + jid)', '                out = ""'),
    ("no_ready_wait", '        while want and self.ph.now() < t_end:', '        while False:'),
    ("stale_end_obeyed", '            if er and er.get("at", 0) < self.started:', '            if False:'),
    ("no_profile_reload", '        if fresh:\n            self.profiles = fresh', '        if False:\n            self.profiles = fresh'),
    ("boot_limit_from_start", '                t_end = tb + self.cfg["boot_cost_s"]', '                t_end = h["t0"] + self.cfg["boot_cost_s"]'),
    ("no_build_limit", '            if tb is None and self.ph.now() >= t_build_end:', '            if False:'),
    ("safety_stop_is_crash", '        if sf:   # RULES H9: a safety stop ends the job CUT', '        if False:   # RULES H9: a safety stop ends the job CUT'),
    ("safety_loop_crashes", '            if sf:   # RULES H9: the safety monitor stopped the boot (xrun parked the phone)', '            if False:   # RULES H9: the safety monitor stopped the boot (xrun parked the phone)'),
    ("safety_boot_ignored", '            if sf:   # RULES H9: xrun\'s safety-check refused or stopped the boot', '            if False:   # RULES H9: xrun\'s safety-check refused or stopped the boot'),
    ("no_notify", '        job_notify(self.ph, job, line)\n', ''),
]


# mutant-block: resident-fit
FIT_MUTANTS = [
    ("alone_fails_all", '    if len(live) == 1:\n        sess.finish(', '    if len(live) >= 1:\n        sess.finish('),
    ("alone_notrun", '"FAIL", "its boot %s did not build', '"NOTRUN", "its boot %s did not build'),
    ("refused_ignored", '    if refused:   # xrun', '    if False:   # xrun'),
    ("tag_ignored", '    if tag not in data:', '    if False:'),
    ("no_host_scan", '            m = HOST_BUILD_RX.search(f.read())', '            m = None'),
    ("split_no_solo", '    mark_solo(sess, batch[1:], why, persist)', '    pass'),
    ("g6_ignored", '    elif why:\n        return [("G6"', '    elif False:\n        return [("G6"'),
    ("prebuild_rc_ignored", '    if rc_ == 0 and "RESULT: PASS build-only" in out:', '    if "RESULT: ERROR" not in out:'),
    ("prebuild_no_build_only_env", 'env=dict(env, XRUN_BUILD_ONLY="1")', 'env=dict(env)'),
    ("limit_ignored", '    why = rc.mif_line_check(xr, line) if line is not None else ""', '    why = ""'),
]


# mutant-block: resident-thermal
THERMAL_MUTANTS = [
    ("s1_ignored", '        if self.last_c is not None and self.last_c >= c["hot_c"]:', '        if False:'),
    ("s1_strict_gt", 'self.last_c >= c["hot_c"]', 'self.last_c > c["hot_c"]'),
    ("s3_gap_ignored", '        if now - self.last_t > c["temp_gap_s"]:', '        if False:'),
    ("s3_first_ignored", '            if now - self.t_boot > c["temp_first_s"]:', '            if False:'),
    ("s2_storage_ignored", 'return "" if storage_c < lim else', 'return "" if True else'),
    ("s2_no_wait", '        if st.get("parked_at") and waited >= c["cool_park_s"]:', '        if True:'),
    ("s2_first_row_ignored", '            if self.last_c >= c["cool_c"]:\n                return "SAFETY FAIL S2', '            if False:\n                return "SAFETY FAIL S2'),
    ("s2_shared_ignored", '        if shared is not None and not shared[0]:', '        if False:'),
    ("s2_shared_not_asked", 'sess.ph.storage_temp(), sess.ph.shared_s2(sess.ph.now()))', 'sess.ph.storage_temp())'),
    ("hot_not_saved", '            self.st.update(hot=True, why=why[:300])', '            pass'),
    ("gate_open", '    if not why:\n        return True', '    if True:\n        return True'),
    ("hot_park_drops_queue", '    for j in list(sess.pending.values()):\n        persist(j)', '    for j in list(sess.pending.values()):\n        sess.finish(j, "NOTRUN", "x", None)'),
    ("safety_wait_fails_job", '    if item in WAIT_ITEMS:', '    if False:'),
    ("safety_refusal_waits_forever", '    return ends_alone(sess, label, batch, "safety-check refused its boot: " + line, persist, event)', '    sess.ph.sleep(120)\n    return True'),
    ("safety_live_drops_queue", '    if booted:\n        hot_park(sess, line, event, persist, notify)', '    if booted:\n        sess.end_all("NOTRUN", "x")'),
    ("rx_any_number", r'temp=(-?\d+(?:\.\d+)?) C")', r'temp=(-?\d+)\.?\d* C")'),
]


def load_thermal(src):
    """The session tool with a mutated tools/resident_thermal.py."""
    import test_resident as T
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("resident_thermal", loader=None))
    m.__dict__["__file__"] = THERMAL_SRC
    exec(compile(src, THERMAL_SRC, "exec"), m.__dict__)
    sys.modules["resident_thermal"] = m
    return T.load_tool()


def load_fit(src):
    """The session tool with a mutated tools/resident_fit.py (registered as the module the library and the cases import)."""
    import test_resident as T
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("resident_fit", loader=None))
    m.__dict__["__file__"] = FIT_SRC
    exec(compile(src, FIT_SRC, "exec"), m.__dict__)
    sys.modules["resident_fit"] = m
    return T.load_tool()


def _killed(cases_fn, module_fn, text):
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = cases_fn(module_fn(text))
        return any(not ok for _, ok in res)
    except Exception:   # lint-silent: expected a mutant that raises (or hits the fake-clock ceiling) counts as killed
        return True


def run_all():
    """(mutants, killed, problems)."""
    import test_resident as T
    total, killed, bad = 0, 0, []
    import resident_fit, resident_thermal
    for path, table, cases_fn, module_fn in ((CORE_SRC, CORE_MUTANTS, T.core_cases, T.load_core), (SESSION_SRC, SESSION_MUTANTS, T.session_cases, T.load_tool),
                                             (FIT_SRC, FIT_MUTANTS, T.session_cases, load_fit), (THERMAL_SRC, THERMAL_MUTANTS, T.session_cases, load_thermal)):
        with open(path) as f:
            src = f.read()
        for name, a, b in table:
            total += 1
            if src.count(a) != 1:
                bad.append("%s %s: needle found %d times" % (os.path.basename(path), name, src.count(a)))
                continue
            k = _killed(cases_fn, module_fn, src.replace(a, b))
            sys.modules["resident_fit"], sys.modules["resident_thermal"] = resident_fit, resident_thermal
            killed += k
            if not k:
                bad.append("%s %s survived" % (os.path.basename(path), name))
    return total, killed, bad


class ResidentMutants(unittest.TestCase):
    """unittest entry for the gate's unit shards: every mutant is killed and every needle is unique."""

    def test_all_killed(self):
        total, killed, bad = run_all()
        self.assertGreaterEqual(total, 50)
        self.assertEqual(bad, [])
        self.assertEqual(killed, total)


if __name__ == "__main__":
    t, k, bad = run_all()
    for b in bad:
        print("MUTANT " + b)
    print("RESULT: %s resident-mutants mutants=%d killed=%d" % ("FAIL" if bad else "PASS", t, k))
    sys.exit(1 if bad else 0)
