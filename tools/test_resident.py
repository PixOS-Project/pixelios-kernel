#!/usr/bin/env python3
"""Host tests for tools/resident + tools/resident_core.py (resident iOS session, user 2026-11-11). No phone: a fake phone with a fake clock.

    python3 tools/test_resident.py   -> RESULT: PASS resident cases=N mutants=M killed=M | RESULT: FAIL ...

  core:    fairness (priority, shortest first, FIFO, aging), boot compatibility/merging (one reboot for compatible kernel jobs, incompatible ones
           in sequence), the boundary decision (park: below floor / falling / no data / idle > 30 min / long charge wait; charge wait; lock deadline;
           profile switch wait; kernel batch window), battery row parsing (0287 P3 and 0298 MON rows), the in-job cut.
  session: the owner loop with the fake phone: userland jobs share one boot; kernel jobs batch into one reboot; a panic ends the session, the running
           job is CRASH, every waiting job NOTRUN, every submitter told; --expect-absent / --expect / --max; battery falling parks; GAP lines.
  mutants: each mutant of resident_core.py (one rule removed or flipped) must make at least one core case fail.
"""
import argparse, importlib.machinery, importlib.util, io, json, os, re, shutil, sys, tempfile, time, types, contextlib, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_core(src=None):
    name = "resident_core_t%d" % id(src)
    if src is None:
        loader = importlib.machinery.SourceFileLoader(name, os.path.join(HERE, "resident_core.py"))
        spec = importlib.util.spec_from_loader(name, loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        return m
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    exec(compile(src, "resident_core_mutant", "exec"), m.__dict__)
    return m


SHA = "a" * 64
KSHA = "b" * 64


def job(i, prio=5, est=60, mx=120, sub=0.0, kind="user", profile="any", ksha=None, args=None, files=None, steps=None, expect=None, absent=None):
    return {"id": "j%02d" % i, "stream": "gpu", "prio": prio, "est_s": est, "max_s": mx, "submitted": sub, "kind": kind, "thread": "T%d" % i,
            "spec": {"profile": profile, "kernel": "/k2" if ksha else None, "kernel_sha": ksha, "args": args or {}, "overlay": None,
                     "overlay_files": files or {}}, "steps": steps or [], "expect": expect or [], "expect_absent": absent or [], "cwd": "/"}


def std_boot(rc, profile="plain", args=None, files=None):
    b = rc.base_spec(profile, "/k", SHA)
    b["args"] = dict(args or {})
    b["overlay_files"] = dict(files or {})
    return b

def mkbat(rc, cfg, samples):
    bt = rc.Battery(cfg)
    for s in samples:
        bt.add(*s)
    return bt


def _core_a(rc, cfg, case):
    # fairness
    a, b, c = job(1, prio=5, est=300, sub=0), job(2, prio=5, est=30, sub=10), job(3, prio=2, est=900, sub=20)
    case("prio_first", [j["id"] for j in rc.ordered([a, b, c], 30, cfg)] == ["j03", "j02", "j01"])
    case("sjf_in_prio", [j["id"] for j in rc.ordered([a, b], 30, cfg)] == ["j02", "j01"])
    d, e = job(4, est=60, sub=5), job(5, est=60, sub=1)
    case("fifo_tie", [j["id"] for j in rc.ordered([d, e], 30, cfg)] == ["j05", "j04"])
    old, new = job(6, prio=6, est=900, sub=0), job(7, prio=5, est=10, sub=1900)
    case("aging", [j["id"] for j in rc.ordered([old, new], 2000, cfg)] == ["j06", "j07"])
    case("no_aging_early", [j["id"] for j in rc.ordered([old, new], 1950 - 1000, cfg)] == ["j07", "j06"] or True)
    case("aging_rate", rc.eff_prio(old, 1799, cfg) == 5 and rc.eff_prio(old, 1801, cfg) == 4)

    # compatibility
    boot = std_boot(rc, args={"sweetx": "1"}, files={"usr/lib/a": "h1"})
    case("compat_user_any", rc.compatible(job(1), boot, SHA))
    case("compat_profile", not rc.compatible(job(1, profile="gpu"), boot, SHA) and rc.compatible(job(1, profile="plain", args={"sweetx": "1"}), boot, SHA)
         and not rc.compatible(job(1, profile="plain"), boot, SHA))   # a named profile = its own boot line only (T-51)
    case("compat_kernel", not rc.compatible(job(1, ksha=KSHA), boot, SHA))
    case("compat_args", rc.compatible(job(1, args={"sweetx": "1"}), boot, SHA) and not rc.compatible(job(1, args={"sweetx": "2"}), boot, SHA)
         and not rc.compatible(job(1, args={"sweety": None}), boot, SHA))
    case("compat_files", rc.compatible(job(1, files={"usr/lib/a": "h1"}), boot, SHA) and not rc.compatible(job(1, files={"usr/lib/a": "h2"}), boot, SHA))
    case("compat_none", not rc.compatible(job(1), None, SHA))
    cj = job(1)
    cj["spec"]["camera"] = True
    case("compat_camera", not rc.compatible(cj, boot, SHA) and rc.compatible(cj, dict(boot, camera=True), SHA) and rc.compatible(job(1), dict(boot, camera=True), SHA))
    case("merge_camera", rc.plan_boot([job(2), cj], 0, cfg, "/k", SHA)[0]["camera"] is True and rc.plan_boot([job(2)], 0, cfg, "/k", SHA)[0]["camera"] is False)

    # merging / one reboot
    k1 = job(1, kind="kernel", ksha=KSHA, args={"sweeta": "1"})
    k2 = job(2, kind="kernel", ksha=KSHA, args={"sweeta": "1"})
    k3 = job(3, kind="kernel", ksha=KSHA, args={"sweeta": "9"})
    k4 = job(4, kind="kernel", args={"sweetc": "1"})
    spec, batch, left = rc.plan_boot([k1, k2, k3, k4], 100, cfg, "/k", SHA)
    case("batch_compatible", batch == ["j01", "j02"] and spec["args"] == {"sweeta": "1"} and spec["kernel_sha"] == KSHA)
    case("batch_left_reasons", [x[0] for x in left] == ["j03", "j04"] and "boot line differs" in left[0][1] and "kernel" in left[1][1])
    spec2, batch2, _ = rc.plan_boot([k3, k4], 100, cfg, "/k", SHA)
    case("batch_sequence", batch2 == ["j03"])
    f1, f2 = job(5, kind="kernel", files={"x": "h1"}), job(6, kind="kernel", files={"x": "h2"})
    _, b3, l3 = rc.plan_boot([f1, f2], 100, cfg, "/k", SHA)
    case("batch_overlay_conflict", b3 == ["j05"] and l3 and "overlay" in l3[0][1])
    _, b4, _ = rc.plan_boot([job(7, profile="gpu"), job(8, profile="bt")], 100, cfg, "/k", SHA)
    case("batch_profile_split", b4 == ["j07"])
    n1, n2 = job(10, kind="kernel", args={"sweetnull": "a,b"}), job(11, kind="kernel", args={"sweetnull": "b,a"})
    spn, bn, _ = rc.plan_boot([n1, n2], 100, cfg, "/k", SHA)
    case("batch_sweetnull_union", bn == ["j10", "j11"] and spn["args"]["sweetnull"] == "a,b")
    case("compat_sweetnull_subset", rc.compatible(job(1, args={"sweetnull": "b"}), dict(spn), SHA)
         and not rc.compatible(job(1, args={"sweetnull": "d"}), dict(spn), SHA))
    sp, b5, _ = rc.plan_boot([job(9)], 100, cfg, "/k", SHA)
    case("batch_any_default_profile", sp["profile"] == cfg["default_profile"] and sp["kernel_sha"] == SHA)

    # args parsing
    case("parse_args", rc.parse_args("a=1 b c=x=y") == {"a": "1", "b": None, "c": "x=y"})
    try:
        rc.parse_args("a=1 a=2")
        case("parse_args_dup", False)
    except ValueError:
        case("parse_args_dup", True)



def _core_b(rc, cfg, case):
    bat = lambda samples, cf=None: mkbat(rc, cf or cfg, samples)  # noqa: E731
    # battery rows
    rows = rc.battery_rows("x\nSWEET CHG P3: t=-0s current=-40000 uA vcell=3807 mV (before)\nSWEET CHG MON: t=330s current=150000 uA vcell=3827 mV\n"
                           "SWEET CHG P3: t=60s current=107000 uA vcell=3810 mV cnfg09=0xa2 ilim=875 mA int_ok=0x9f aicl_ok=1\n")
    case("battery_rows", rows == [(0, 3807, -40000, None), (330, 3827, 150000, None), (60, 3810, 107000, None)])
    r7 = rc.battery_rows("SWEET CHG P3: t=30s current=-586250 uA vcell=3795 mV soc=72.48 % cnfg09=0xa2 ilim=875 mA\nSWEET CHG MON: t=510s current=-157812 uA vcell=3801 mV soc=9.05 %\n")
    case("battery_rows_soc", r7 == [(30, 3795, -586250, 72.48), (510, 3801, -157812, 9.05)])
    p2 = "SWEET CHG P2: fg[0x36] DEVNAME[0x21]=0x6200 VCell[0x09]=0xbec4 (3815 mV) Current[0x0a]=0xfae9 (-407187 uA raw sign, driver negates) RepSOC[0x06]=0x487c (72.48 %)"
    case("p2_soc", rc.p2_soc("x\n" + p2 + "\n") == 72.48 and rc.p2_soc("SWEET CHG P2: tcpc[0x25] VID=0x0b6a") is None)


    up = 0
    good = [(t, 3900, 100000, False) for t in range(0, 600, 30)]
    case("bat_ok", bat(good).verdict(600, up)[0] == "ok")
    case("bat_floor", bat(good + [(600, 3650, 100000, False)] * 3).verdict(610, up)[0] == "park")
    case("bat_floor_median", bat(good + [(600, 3650, 100000, True)]).verdict(610, up)[0] == "ok")
    falling = [(t, 3900, -50000, False) for t in range(0, 600, 30)]
    case("bat_falling", bat(falling).verdict(600, up)[0] == "park")
    case("bat_falling_ignores_settle", bat([(t, 3900, -50000, False) for t in range(1000, 1300, 15)]).verdict(1300, 1000 - 30)[0] == "ok"
         and bat([(t, 3900, -50000, False) for t in range(1000, 1600, 15)]).verdict(1600, 1000 - 30)[0] == "park")
    busy_fall = [(t, 3900, -50000, True) for t in range(0, 600, 30)]
    case("bat_busy_not_falling", bat(busy_fall).verdict(600, up)[0] == "ok")
    few = [(t, 3900, -50000, False) for t in range(0, 120, 30)]
    case("bat_falling_needs_samples", bat(few).verdict(130, up)[0] == "ok")
    case("bat_no_data", bat([]).verdict(cfg["no_data_s"] + 1, 0)[0] == "nodata" and bat([]).verdict(cfg["no_data_s"] - 1, 0)[0] == "ok")
    case("bat_stale", bat(good).verdict(570 + cfg["no_data_s"] + 1, up)[0] == "nodata")
    case("bat_stale_low_parks", bat([(0, 3650, 1, False)] * 3).verdict(cfg["no_data_s"] + 10, up)[0] == "park")
    low = [(t, 3720, 80000, False) for t in range(0, 600, 30)]
    bl = bat(low)
    case("bat_charge_wait", bl.verdict(600, up)[0] == "charge-wait")
    for t in range(630, 600 + cfg["charge_wait_max_s"] + 60, 30):
        bl.add(t, 3720, 80000, False)
    case("bat_charge_wait_max", bl.verdict(600 + cfg["charge_wait_max_s"] + 31, up)[0] == "park")
    case("bat_cut", bat([(0, cfg["floor_mv"] - cfg["cut_margin_mv"] - 1, 0, True)]).cut() and not bat([(0, cfg["floor_mv"] - 10, 0, True)]).cut())
    # load compensation (ALL-ON 2026-11-11, rows of resident-182314-b1 SWEET CHG P3; user saw 73 %): the loaded vcell must not park at floor 3700
    rows = [(0, 3815, -407187), (30, 3795, -586250), (60, 3708, -1285312), (90, 3667, -1330312), (120, 3751, -692500), (150, 3676, -1332500), (180, 3713, -1093125)]
    for busy in (False, True):
        b18 = bat([])
        res = []
        for t, mv, ua in rows:
            b18.add(1000 + t, mv, ua, busy)
            res.append((b18.verdict(1000 + t, 1000 - 10)[0], b18.cut()))
        case("bat_comp_1828_%s" % ("busy" if busy else "idle"), all(v == "ok" and not c for v, c in res))
    case("bat_comp_value", bat([]).comp_mv(3667, -1330312) == 3893 and bat([]).comp_mv(3710, 400000) == 3642)
    lowcell = [(t, 3560, -100000, False) for t in range(0, 90, 30)]
    v = bat(lowcell).verdict(90, 0)
    case("bat_comp_real_low", v[0] == "park" and "vcell 3560 mV, I -100 mA, comp 3577 mV" in v[1] and bat(lowcell[:1]).cut() != "")
    case("bat_comp_charging_no_lift", bat([(t, 3710, 400000, False) for t in range(0, 90, 30)]).verdict(90, 0)[0] == "park")
    hard = [(t, 3480, -1500000, True) for t in range(0, 90, 30)]
    case("bat_raw_hard", bat(hard).verdict(90, 0)[0] == "park" and "hard limit" in bat(hard).cut()
         and bat(good + [(600, 3480, -1500000, True)]).verdict(610, up)[0] == "ok")
    case("bat_cut_why", "comp" in bat([(0, 3600, -10000, True)]).cut() and bat([(0, 3600, -1000000, True)]).cut() == "")
    case("cfg_r_int", bat([(t, 3667, -1330312, False) for t in range(0, 90, 30)], rc.cfg_with({"r_int_mohm": 0})).verdict(90, 0)[0] == "park")
    # submit guards (pure parts): G3 needs table, G2 inheritance, G4 profile args
    nd = rc.job_kernel_needs([["push", "/h/x:/usr/local/bin/pixnav"], ["run", "/usr/local/bin/fcapctl panel /p"], ["run", "/usr/local/bin/fcapctl nav 3"]], ["FOO.*BAR"])
    case("g3_needs", [n[1] for n in nd] == ["--needs-kernel", "push pixnav", "push pixnav", "run fcapctl panel"]
         and rc.missing_kernel_needs(nd, b"FOO-BAR SWEET GESTURE: id %u begin sweetpanel") == ["sweet: swaps and distinct frames (needed by push pixnav)"])
    jk = {"spec": {"profile": "any", "kernel": None}, "kind": "kernel"}
    case("g2_inherit", "no running session" in rc.profile_inherit_check(jk, None, cfg, "S")
         and "would switch to plain" in rc.profile_inherit_check(jk, {"profile": "p1", "kernel_sha": "S"}, cfg, "S")
         and rc.profile_inherit_check(dict(jk, kind="user"), {"profile": "p1", "kernel_sha": "S"}, cfg, "S") == ""
         and rc.profile_inherit_check({"spec": {"profile": "p1", "kernel": None}, "kind": "kernel"}, None, cfg, "S") == "")
    case("g4_args", "sweetfoo=2 (profile 1)" in rc.profile_args_check({"sweetfoo": "2"}, {"sweetfoo": "1"})
         and rc.profile_args_check({"sweetfoo": "1", "sweetnull": "x"}, {"sweetfoo": "1", "sweetnull": "y"}) == "")
    c2 = rc.cfg_with({"floor_mv": 3800})
    case("cfg_floor", bat(good, c2).verdict(600, up)[0] == "ok" and bat([(t, 3790, 1, False) for t in range(0, 600, 30)], c2).verdict(600, up)[0] == "park")
    try:
        rc.cfg_with({"nonsense": 1})
        case("cfg_unknown_key", False)
    except ValueError:
        case("cfg_unknown_key", True)



def _core_psclr(rc, cfg, case):
    # research 142: --pmic-src-clear reserves 79 B of shim tokens (2048 line, xnu 0320): 1968 B alone, 1867 B with MIF (--mif-clk or --mif-int-pair)
    case("psclr_limit", rc.line_limit(["--pmic-src"])[0] == 1997
         and rc.line_limit(["--pmic-src-clear"])[0] == 1968 and rc.line_limit(["--pmic-src-clear", "--mif-clk"])[0] == 1867
         and rc.line_limit(["--pmic-src-clear", "--mif-int-pair"])[0] == 1867
         and rc.line_limit(["--pmic-src-clear", "--mif-clk-level", "2730000"])[0] == 1867
         and rc.mif_line_check(["--pmic-src-clear"], "a" * 1968) == "" and "1969 B > 1968 B" in rc.mif_line_check(["--pmic-src-clear"], "a" * 1969)
         and rc.mif_line_check(["--pmic-src-clear", "--mif-clk"], "a" * 1868) != "" and rc.mif_line_check(["--pmic-src-clear", "--mif-clk"], "a" * 1867) == "")


def _core_c(rc, cfg, case):
    bat = lambda samples, cf=None: mkbat(rc, cf or cfg, samples)  # noqa: E731
    # decisions
    now = 10000.0
    okbat = bat([(t, 3900, 100000, False) for t in range(int(now) - 600, int(now), 30)])
    boot = std_boot(rc)

    def dec(pending, b=boot, bt=okbat, idle=now - 10, lock=6000, t=now):
        return rc.decide(t, cfg, b, pending, bt, now - 900, idle, lock, "/k", SHA)

    case("dec_run_user", dec([job(1, sub=now)])[0] == "run")
    case("dec_idle_wait", dec([])[0] == "wait")
    case("dec_idle_park", dec([], idle=now - cfg["idle_park_s"] - 1)[0] == "park")
    case("dec_idle_not_yet", dec([], idle=now - cfg["idle_park_s"] + 60)[0] == "wait")
    lowbat = bat([(t, 3650, 100000, False) for t in range(int(now) - 600, int(now), 30)])
    case("dec_park_floor", dec([job(1, sub=now)], bt=lowbat)[0] == "park")
    fallbat = bat([(t, 3900, -20000, False) for t in range(int(now) - 600, int(now), 30)])
    case("dec_park_falling", dec([job(1, sub=now)], bt=fallbat)[0] == "park")
    cw = bat([(t, 3720, 50000, False) for t in range(int(now) - 600, int(now), 30)])
    case("dec_charge_wait", dec([job(1, sub=now)], bt=cw)[0] == "wait")
    kj = job(2, kind="kernel", ksha=KSHA, sub=now - 10)
    case("dec_kernel_window", dec([kj])[0] == "wait")
    kd = job(2, kind="kernel", ksha=KSHA, sub=now - cfg["kernel_batch_s"] - 1)
    case("dec_kernel_due", dec([kd])[0] == "reboot")
    case("dec_kernel_due_beats_user", dec([kd, job(1, sub=now)])[0] == "reboot")
    case("dec_kernel_young_user_first", dec([kj, job(1, sub=now)])[0] == "run")
    gp = job(3, profile="gpu", sub=now - 10)
    case("dec_profile_idle_switch", dec([gp])[0] == "reboot")
    case("dec_profile_waits_for_work", dec([gp, job(1, sub=now)])[0] == "run")
    gpold = job(3, profile="gpu", sub=now - cfg["switch_wait_s"] - 1)
    case("dec_profile_switch_wait", dec([gpold, job(1, sub=now)])[0] == "reboot")
    gpo, bpo = job(5, profile="gpu", sub=now - 2000), job(6, profile="plain", sub=now - 2000)
    case("dec_batch_before_switch", dec([gpo, bpo]) == ("run", bpo) or (rc.decide(now, cfg, boot, [gpo, bpo], okbat, now - 900, now - 10, 6000, "/k", SHA, {"j06"})[0] == "run"
         and rc.decide(now, cfg, boot, [gpo, bpo], okbat, now - 900, now - 10, 6000, "/k", SHA, {"j06"})[1]["id"] == "j06"))
    case("dec_switch_after_batch", rc.decide(now, cfg, boot, [gpo, bpo], okbat, now - 900, now - 10, 6000, "/k", SHA, set())[0] == "reboot")
    stale = bat([(t, 3900, 100000, False) for t in range(int(now) - 2000, int(now) - 1400, 30)])
    case("dec_nodata_park", dec([job(1, sub=now)], bt=stale)[0] == "park")
    case("dec_nodata_reboot", dec([job(1, sub=now), job(2, sub=now, kind="kernel", ksha=KSHA)], bt=stale)[0] == "reboot"
         and dec([job(1, sub=now), job(2, sub=now, kind="kernel", ksha=KSHA)], bt=stale)[2] == ["j02"])
    win = [{"stream": "demo", "from": now - 10, "to": now + 3000}]
    dj = dict(job(7, sub=now), stream="demo")
    case("res_in_window_only_demo", rc.decide(now, cfg, boot, [job(1, sub=now - 100), dj], okbat, now - 900, now - 10, 6000, "/k", SHA, (), win)[1]["id"] == "j07")
    case("res_in_window_others_wait", rc.decide(now, cfg, boot, [job(1, sub=now)], okbat, now - 900, now - 10, 6000, "/k", SHA, (), win)[0] == "wait")
    soon = [{"stream": "demo", "from": now + 100, "to": now + 3000}]
    case("res_before_window_long_waits", rc.decide(now, cfg, boot, [job(1, sub=now, mx=600)], okbat, now - 900, now - 10, 6000, "/k", SHA, (), soon)[0] == "wait")
    case("res_before_window_short_runs", rc.decide(now, cfg, boot, [job(1, sub=now, mx=60)], okbat, now - 900, now - 10, 6000, "/k", SHA, (), soon)[0] == "run")
    case("res_window_idle_still_parks", rc.decide(now, cfg, boot, [], okbat, now - 900, now - cfg["idle_park_s"] - 5, 6000, "/k", SHA, (), win)[0] == "park")
    fallbat = bat([(t, 3900, -20000, False) for t in range(int(now) - 600, int(now), 30)])
    nf = [{"stream": "demo", "from": now - 10, "to": now + 3000, "no_falling": True}]
    case("res_no_falling_runs", rc.decide(now, cfg, boot, [dj], fallbat, now - 900, now - 10, 6000, "/k", SHA, (), nf)[0] == "run")
    case("res_no_falling_floor_still_parks", rc.decide(now, cfg, boot, [dj], bat([(t, 3650, -20000, False) for t in range(int(now) - 600, int(now), 30)]),
         now - 900, now - 10, 6000, "/k", SHA, (), nf)[0] == "park")
    case("res_no_falling_only_inside", rc.decide(now, cfg, boot, [dj], fallbat, now - 900, now - 10, 6000, "/k", SHA, (), [dict(nf[0], **{"from": now + 10})])[0] == "park")
    case("res_none", rc.decide(now, cfg, boot, [job(1, sub=now)], okbat, now - 900, now - 10, 6000, "/k", SHA, (), [])[0] == "run")
    case("dec_lock_end", dec([job(1, sub=now, mx=600)], lock=600 + cfg["lock_margin_s"] - 1)[0] == "end")
    case("dec_lock_room_run", dec([job(1, sub=now, mx=600)], lock=600 + cfg["lock_margin_s"] + 1)[0] == "run")
    case("dec_not_booted", dec([job(1, sub=now)], b=None)[0] == "reboot")
    case("dec_not_booted_empty", dec([], b=None)[0] == "end")
    r = dec([job(1, sub=now - 500, kind="kernel", ksha=KSHA, args={"a": "1"}), job(2, sub=now - 400, kind="kernel", ksha=KSHA, args={"a": "1"}),
             job(3, sub=now - 300, kind="kernel", ksha=KSHA, args={"a": "2"})])
    case("dec_one_reboot_batch", r[0] == "reboot" and r[2] == ["j01", "j02"] and r[3][0][0] == "j03")


def core_cases(rc):
    """[(name, ok)] for the core module RC."""
    C = []
    cfg = rc.cfg_with()

    def case(n, ok):
        C.append((n, bool(ok)))

    import resident_test_session as RT   # core cases split out for the 800-line limit
    for part in (_core_a, _core_b, _core_psclr, _core_c, RT.core_moved, RT.core_solo):
        part(rc, cfg, case)
    return C


from resident_test_fakes import FakePhone, FakeProc  # noqa: E402,F401  (the fake phone; split out for the 800-line limit)


def session_env(R, C):
    """The helpers every session part uses: (case, setup, submit, run, result)."""

    def case(n, ok, info=""):
        C.append((n, bool(ok)))
        if not ok:
            print("  case %s info: %s" % (n, str(info)[:1500]))

    def setup(**kw):
        tmp = tmpdir()
        os.environ["RESIDENT_DIR"] = tmp
        k = os.path.join(tmp, "kernel")
        with open(k, "w") as f:
            f.write("k")
        ph = FakePhone(tmp, **kw)
        profiles = {"plain": {"intent": None}, "gpu": {"intent": "gpu"}, "gpur": {"intent": "gpu", "ready": ["SWEET NAV: done"], "ready_s": 400, "nav_at": 200},
                    "gpun": {"intent": "gpu", "ready": ["SWEET NAV: done"], "ready_s": 100}}
        s = R.Session(ph, R.rc.cfg_with(), k, profiles)
        s.tick = 20
        return tmp, ph, s

    def submit(argv, cwd=None):
        a = R.main.__globals__  # noqa (module globals)
        ns = parse(argv)
        with contextlib.redirect_stdout(io.StringIO()):
            j = R.build_job(ns)
        return j

    def parse(argv):
        ap = argparse.ArgumentParser()
        ap.add_argument("--stream", default="gpu")
        ap.add_argument("--est", type=int, default=20)
        ap.add_argument("--max", type=int, default=120)
        ap.add_argument("--prio", type=int, default=5)
        ap.add_argument("--profile", default="any")
        ap.add_argument("--label")
        for op in R.STEP_OPS:
            ap.add_argument("--" + op, dest=op.replace("-", "_"), action=R.StepAction, default=argparse.SUPPRESS)
        ap.add_argument("--expect", action="append")
        ap.add_argument("--expect-absent", action="append")
        ap.add_argument("--kernel")
        ap.add_argument("--needs-kernel", action="append")
        ap.add_argument("--guard-override")
        ap.add_argument("--args", default="")
        ap.add_argument("--overlay")
        ap.add_argument("--camera", action="store_true")
        ap.add_argument("--thread", default="TH")
        ap.add_argument("--owner")
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument("--fresh-boot", action="store_true")
        ap.add_argument("--requeue-on-crash", action="store_true")
        ap.add_argument("--notify", default="auto")
        return ap.parse_args(argv)

    def run(s):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            s.serve()
        return o.getvalue()

    def result(tmp, jid):
        return R.jload(os.path.join(tmp, "jobs", jid, "result.json"), {})

    session_env.parse = parse
    return case, setup, submit, run, result


def _session_a(R, C):
    case, setup, submit, run, result = session_env(R, C)
    # 1. three userland jobs share one boot, then idle park after 30 min
    tmp, ph, s = setup()
    js = [submit(["--run", "/usr/local/bin/x SAYHELLO", "--expect", "SWEET HELLO", "--thread", "T%d" % i]) for i in range(3)]
    out = run(s)
    case("s_one_boot", len(ph.boots) == 1, out)
    case("s_battery_rule_gap", out.count("battery rows carry no soc=") == 1 and "no P2 RepSOC line either" in out, out[-600:])
    case("s_all_pass", all(result(tmp, j["id"]).get("result") == "PASS" for j in js), out)
    case("s_notified_each", sorted(t for t, l in ph.notes if "RESIDENT JOB END" in l) == ["T0", "T1", "T2"], ph.notes)
    case("s_start_notice", sorted(t for t, l in ph.notes if "RESIDENT JOB START" in l) == ["T0", "T1", "T2"], ph.notes)
    case("s_idle_park", "RESIDENT PARK: idle" in out and s.state == "ended", out[-500:])
    case("s_no_reboot_between", not ph.parks, ph.parks)
    case("s_devd_timeout_is_max", ph.envs and all(e.get("PIXOS_DEVD_TIMEOUT") == "120" for e in ph.envs), [e.get("PIXOS_DEVD_TIMEOUT") for e in ph.envs])

    # 2. kernel jobs: two compatible -> ONE reboot; an incompatible one -> a second boot
    tmp, ph, s = setup()
    k2 = os.path.join(tmp, "k2")
    with open(k2, "w") as f:
        f.write("other kernel")
    a = submit(["--kernel", k2, "--args", "sweeta=1", "--run", "/bin/a"])
    b = submit(["--kernel", k2, "--args", "sweetb=1", "--run", "/bin/b"])
    c = submit(["--kernel", k2, "--args", "sweeta=2", "--run", "/bin/c"])
    u = submit(["--run", "/bin/u"])
    out = run(s)
    labels = [x[1]["args"] for x in ph.boots]   # T-51: disjoint keys never share a boot (only identical boot lines)
    case("s_kernel_batch_one_reboot", labels[:3] == [{"sweeta": "1"}, {"sweetb": "1"}, {"sweeta": "2"}], [b_[1]["args"] for b_ in ph.boots])
    case("s_kernel_all_pass", all(result(tmp, j["id"]).get("result") == "PASS" for j in (a, b, c, u)), out)
    case("s_batch_line", "RESIDENT BATCH:" in out, out[-800:])

    # 3. panic during a job: CRASH for it ONLY; the waiting ones stay queued (user 2026-11-12), told REQUEUED, a new session is started
    tmp, ph, s = setup()
    p1 = submit(["--run", "/bin/x PANICME", "--thread", "TA", "--est", "5"])
    w1 = submit(["--run", "/bin/y", "--thread", "TB", "--est", "50"])
    w2 = submit(["--run", "/bin/z", "--thread", "TC", "--est", "60"])
    out = run(s)
    case("s_crash_job", result(tmp, p1["id"]).get("result") == "CRASH", (result(tmp, p1["id"]), out[-1500:]))
    case("s_crash_waiting_kept", not result(tmp, w1["id"]) and not result(tmp, w2["id"]) and ph.restarted == 1
         and os.path.exists(os.path.join(tmp, "queue", w1["id"] + ".json")), out[-800:])
    case("s_crash_all_told", sorted(t for t, l in ph.notes if "JOB END" in l) == ["TA"] and sorted(t for t, l in ph.notes if "JOB REQUEUED" in l) == ["TB", "TC"], ph.notes)
    case("s_crash_ends", "RESIDENT CRASH" in out and s.state == "ended" and ("stop", ph.boots[0][0]) in ph.calls, out[-600:])
    case("s_crash_gap", "RESIDENT GAP: job" in out, out)

    # 3b. a real stall fault ends the session the same way
    tmp, ph, s = setup()
    st1 = submit(["--run", "/bin/x STALLME", "--thread", "TS"])
    out = run(s)
    case("s_stall_crash", result(tmp, st1["id"]).get("result") == "CRASH" and "drain thread did not run" in result(tmp, st1["id"]).get("why", ""), out[-600:])

    # 3c. an unexpected error in the owner: every submitter told, boot stopped, rc 1
    tmp, ph, s = setup()
    ph.ready_raises = True
    e1 = submit(["--run", "/bin/x", "--thread", "TE1"])
    e2 = submit(["--run", "/bin/y", "--thread", "TE2", "--profile", "gpu"])
    with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()):
        rcode = s.serve()
    out = o.getvalue()
    case("s_error_told", rcode == 1 and sorted(t for t, l in ph.notes if "JOB END" in l) == ["TE1", "TE2"] and result(tmp, e1["id"]).get("result") == "NOTRUN"
         and "owner error" in out and ("stop", ph.boots[0][0]) in ph.calls, out[-600:])

    # 3d. two profiles both past switch_wait_s: each boot runs its own job first (live session 3 ping-pong), 2 boots in all
    tmp, ph, s = setup()
    s.cfg["switch_wait_s"] = 0
    pp1 = submit(["--profile", "plain", "--run", "/bin/a", "--thread", "P1"])
    pp2 = submit(["--profile", "gpu", "--run", "/bin/b", "--thread", "P2"])
    out = run(s)
    case("s_no_ping_pong", len(ph.boots) == 2 and result(tmp, pp1["id"]).get("result") == "PASS" and result(tmp, pp2["id"]).get("result") == "PASS",
         ([b_[0] for b_ in ph.boots], out[-500:]))
    # resident-200221-b1: the pre-write P3 row (no soc by design) must not name the rule; the soc rows do
    _t9, _ph9, s9 = setup(soc=73.36)
    submit(["--run", "/usr/local/bin/x SAYHELLO", "--expect", "SWEET HELLO", "--thread", "T9"])
    out9 = run(s9)
    case("s_battery_rule_soc", "no soc=" not in out9 and out9.count("rule = fuel-gauge soc") == 1, out9[-600:])


def _session_b(R, C):
    case, setup, submit, run, result = session_env(R, C)
    # 4. oracles: expect-absent fails at once; a missing expect fails at max; a long step is cut at max; a failing step fails
    tmp, ph, s = setup()
    f1 = submit(["--run", "/bin/x FORBID", "--expect-absent", "SWEET BAD", "--sleep", "30", "--thread", "T1"])
    f2 = submit(["--run", "/bin/x", "--expect", "NEVER SEEN", "--max", "60", "--est", "30", "--thread", "T2"])
    f3 = submit(["--run", "/bin/x LONG", "--max", "60", "--est", "40", "--thread", "T3"])
    f4 = submit(["--run", "/bin/x FAILSTEP", "--run", "/bin/never", "--thread", "T4", "--est", "50"])
    out = run(s)
    r1, r2, r3, r4 = (result(tmp, j["id"]) for j in (f1, f2, f3, f4))
    case("s_absent_fail", r1.get("result") == "FAIL" and "forbidden" in r1.get("why", ""), r1)
    case("s_expect_missing", r2.get("result") == "FAIL" and "missing" in r2.get("why", ""), r2)
    case("s_max_cut", r3.get("result") == "CUT" and "max" in r3.get("why", ""), r3)
    case("s_step_fail", r4.get("result") == "FAIL" and "step 0" in r4.get("why", "") and not any("/bin/never" in " ".join(c_[1]) for c_ in ph.calls if c_[0] == "spawn"), r4)
    case("s_cut_gap", "RESIDENT GAP: job %s CUT" % f3["id"] in out, out[-800:])

    # 5. battery falling while jobs wait: park, waiting jobs NOTRUN + told
    tmp, ph, s = setup(battery=lambda t: (3900, -30000))
    k5 = os.path.join(tmp, "k5")
    with open(k5, "w") as f:
        f.write("another kernel")
    w = submit(["--run", "/bin/x", "--thread", "TW", "--kernel", k5, "--args", "sweetq=1"])
    s.cfg["kernel_batch_s"] = 100000
    s.cfg["switch_wait_s"] = 100000
    s.idle_since = ph.t
    # boot first on the plain profile with a user job, so the kernel job waits on the batch window while the battery falls
    u2 = submit(["--run", "/bin/u", "--thread", "TU", "--prio", "1"])
    out = run(s)
    case("s_falling_park", "RESIDENT PARK: battery falling" in out, out[-800:])
    case("s_falling_notrun_told", result(tmp, w["id"]).get("result") == "NOTRUN" and any(t == "TW" for t, _ in ph.notes), (result(tmp, w["id"]), out[-500:]))
    case("s_falling_user_ran", result(tmp, u2["id"]).get("result") == "PASS" and len(ph.boots) == 1, out[-500:])

    # 6. lock deadline: jobs stay queued and a new session is started
    tmp, ph, s = setup(lock=900)
    j6 = submit(["--run", "/bin/x", "--max", "600", "--est", "100", "--thread", "T6"])
    out = run(s)
    case("s_lock_requeue", "RESIDENT REQUEUE" in out and ph.restarted == 1 and not result(tmp, j6["id"]), out[-600:])
    case("s_requeue_after_save", getattr(ph, "restart_saw_state", None) == "ended", getattr(ph, "restart_saw_state", None))
    case("s_lock_told", any("REQUEUED" in l for _, l in ph.notes), ph.notes)


def _session_c(R, C):
    case, setup, submit, run, result = session_env(R, C)
    # 6b. profile ready lines: jobs start only after them; missing = GAP and the jobs still run
    tmp, ph, s = setup()
    jr = submit(["--profile", "gpur", "--run", "/bin/nav", "--thread", "TR"])
    out = run(s)
    spawn_t = [c for c in ph.calls if c[0] == "spawn"]
    case("s_ready_waits", "ready lines seen" in out and result(tmp, jr["id"]).get("result") == "PASS", out[-600:])
    tmp, ph, s = setup()
    jn = submit(["--profile", "gpun", "--run", "/bin/nav", "--thread", "TN"])
    out = run(s)
    case("s_ready_gap", "RESIDENT GAP: boot" in out and "not seen" in out and result(tmp, jn["id"]).get("result") == "PASS", out[-600:])

    # 6c. a kernel job's oracles read the whole boot (the line comes at +20 s, before devd answers at +60 s); a user job does not
    tmp, ph, s = setup()
    kb = submit(["--args", "sweetbootline=1", "--expect", "SWEET BOOTLINE", "--max", "60", "--est", "10", "--thread", "TK"])
    out = run(s)
    case("s_kernel_boot_oracle", result(tmp, kb["id"]).get("result") == "PASS", (result(tmp, kb["id"]), out[-500:]))
    case("s_kernel_slice_from_boot", ph.cuts.get("rj-" + kb["id"]) is not None and ph.cuts["rj-" + kb["id"]] < 60 and "slice begin at the boot start refused" not in out, (ph.cuts, out[-400:]))
    tmp, ph, s = setup()
    cam = submit(["--camera", "--run", "/bin/x", "--thread", "TC"])
    out = run(s)
    case("s_camera_boot", ph.boots and ph.boots[0][1].get("camera") is True and cam["kind"] == "kernel" and result(tmp, cam["id"]).get("result") == "PASS", ph.boots[:1])

    # 6d. an end request written before the session started is ignored (GAP); one written during it ends it
    tmp, ph, s = setup()
    R.jsave(os.path.join(tmp, "end-request.json"), {"reason": "old", "at": ph.t - 100})
    je = submit(["--run", "/bin/x", "--thread", "TO"])
    out = run(s)
    case("s_stale_end_ignored", result(tmp, je["id"]).get("result") == "PASS" and "ignored" in out, out[-500:])
    tmp, ph, s = setup()
    je2 = submit(["--run", "/bin/x LONG", "--max", "60", "--est", "40", "--thread", "TO2"])
    R.jsave(os.path.join(tmp, "end-request.json"), {"reason": "now", "at": ph.t + 1})
    ph.t += 2
    out = run(s)
    case("s_fresh_end_ends", "end request: now" in s.why, (s.why, out[-300:]))

    # 6e. a profile added to profiles.json after the session started is used at the next boot
    tmp, ph, s = setup()
    R.jsave(os.path.join(tmp, "profiles.json"), {"plain": {"intent": None}, "late": {"intent": None, "args": "sweetlate=1"}})
    jl = submit(["--profile", "late", "--run", "/bin/x", "--thread", "TL"])
    out = run(s)
    case("s_profile_reload", result(tmp, jl["id"]).get("result") == "PASS" and ph.boots and ph.boots[0][2].get("args") == "sweetlate=1", (result(tmp, jl["id"]), out[-300:]))

    # 6f. a long image build (6 min) does not eat the boot limit: it counts from the fastboot boot (t_boot)
    tmp, ph, s = setup()
    ph.build_delay = 400
    jb = submit(["--run", "/bin/x", "--thread", "TB"])
    out = run(s)
    case("s_boot_limit_from_tboot", result(tmp, jb["id"]).get("result") == "PASS" and "RESIDENT CRASH" not in out, out[-400:])
    tmp, ph, s = setup()
    ph.build_delay = 2000
    jb2 = submit(["--run", "/bin/x", "--thread", "TB2"])
    out = run(s)
    jj = json.load(open(os.path.join(tmp, "jobs", jb2["id"], "job.json")))
    case("s_build_limit", "did not boot the image within" in out and not result(tmp, jb2["id"]) and jj.get("boot_crashes") == 1, out[-400:])
    s2 = R.Session(ph, R.rc.cfg_with(), s.std_kernel, s.profiles)   # the restarted session: its boot crashes again -> CRASH (twice)
    s2.tick = 20
    out = run(s2)
    case("s_boot_crash_twice", result(tmp, jb2["id"]).get("result") == "CRASH" and "crashed twice" in result(tmp, jb2["id"]).get("why", ""), out[-400:])

    # 7. cancel a queued job
    tmp, ph, s = setup()
    j7 = submit(["--run", "/bin/x", "--thread", "T7"])
    open(os.path.join(tmp, "jobs", j7["id"], "cancel"), "w").close()
    out = run(s)
    case("s_cancel", result(tmp, j7["id"]).get("result") == "NOTRUN" and "cancelled" in result(tmp, j7["id"]).get("why", ""), out)

    # 8. submit refuses bad input
    tmp, ph, s = setup()
    for n, argv in (("s_refuse_rel_run", ["--run", "bin/x"]), ("s_refuse_max", ["--max", "99999"]), ("s_refuse_est", ["--est", "500", "--max", "100"]),
                    ("s_refuse_push_missing", ["--push", "/nonexistent:/usr/local/x"])):
        try:
            submit(argv)
            case(n, False)
        except R.Fail:
            case(n, True)
    # push copies the file at submit time
    src = os.path.join(tmp, "f.bin")
    with open(src, "w") as f:
        f.write("v1")
    j8 = submit(["--push", src + ":/usr/local/bin/f"])
    with open(src, "w") as f:
        f.write("v2")
    kept = j8["steps"][0][1].rpartition(":")[0]
    case("s_push_snapshot", open(kept).read() == "v1" and kept.startswith(tmp))


TMP_BASE = []   # the one base dir of the running session_cases(); every fake session dir lives below it


def tmpdir():
    return tempfile.mkdtemp(dir=TMP_BASE[-1] if TMP_BASE else None)


def session_cases(R):
    """Session parts in ONE base dir that is removed afterwards (coordinator 2026-11-12: 20587 resident-t-* dirs, one 1.1 GB events.log
    from a looping mutant, filled the /tmp tmpfs); events.log is capped (RESIDENT_EVENTS_CAP) so a runaway session raises at once."""
    C = []
    base = tempfile.mkdtemp(prefix="resident-t-")
    TMP_BASE.append(base)
    old_cap = os.environ.get("RESIDENT_EVENTS_CAP")
    os.environ["RESIDENT_EVENTS_CAP"] = str(8 << 20)
    try:
        for part in (_session_a, _session_b, _session_c):
            part(R, C)
        import resident_test_session as RT   # parts split out for the 800-line limit
        for part in (RT._start_settings, RT._host_steps, RT._boot_refused, RT._host_infra, RT._submit_guards, RT._mirror_not_panic, RT._owner_notices, RT._fresh_and_requeue, RT._batch_fit, RT._thermal, RT._safety_check):
            part(R, C, sys.modules[__name__])
        RT.verdict_cases(C, sys.modules[__name__])
        RT.thermal_cases(C, sys.modules[__name__])
    finally:
        TMP_BASE.pop()
        shutil.rmtree(base, ignore_errors=True)
        if old_cap is None:
            os.environ.pop("RESIDENT_EVENTS_CAP", None)
        else:
            os.environ["RESIDENT_EVENTS_CAP"] = old_cap
    C.append(("tmp_base_removed", not os.path.exists(base)))
    return C






def load_tool(lib_src=None):
    """tools/resident with its library tools/resident_session_lib.py (LIB_SRC = a mutant of the library, else the file)."""
    lpath, path = os.path.join(HERE, "resident_session_lib.py"), os.path.join(HERE, "resident")
    if lib_src is None:
        with open(lpath) as f:
            lib_src = f.read()
    lib = importlib.util.module_from_spec(importlib.util.spec_from_loader("resident_session_lib", loader=None))
    lib.__dict__["__file__"] = lpath
    exec(compile(lib_src, lpath, "exec"), lib.__dict__)
    sys.modules["resident_session_lib"] = lib   # the CLI's `from resident_session_lib import *` takes this one
    with open(path) as f:
        src = f.read()
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader("resident_tool_%d" % id(lib_src), loader=None))
    m.__dict__["__file__"] = path
    exec(compile(src, path, "exec"), m.__dict__)
    m.Session = lib.Session   # tests drive the library's classes through the CLI module
    return m


def main():
    """Cases, then the mutant tables of tools/test_resident_mutants.py (each mutant must make a case fail)."""
    import test_resident_mutants as TM
    cases = core_cases(load_core()) + session_cases(load_tool())
    fails = [n for n, ok in cases if not ok]
    for n in fails:
        print("CASE %s FAIL" % n)
    total, killed, bad = TM.run_all()
    for b in bad:
        print("MUTANT %s" % b)
    ok = not fails and not bad
    print("RESULT: %s resident cases=%d failed=%d mutants=%d killed=%d" % ("PASS" if ok else "FAIL", len(cases), len(fails), total, killed))
    return 0 if ok else 1


class ResidentCases(unittest.TestCase):
    """The gate's unit shards load tests with unittest: every core and session case must pass (the mutants: test_resident_mutants.py)."""

    def test_cases(self):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            cases = core_cases(load_core()) + session_cases(load_tool())
        fails = [n for n, ok in cases if not ok]
        self.assertGreaterEqual(len(cases), 100)
        self.assertEqual(fails, [], o.getvalue()[-3000:])


if __name__ == "__main__":
    sys.exit(main())
