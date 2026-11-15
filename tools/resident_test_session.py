"""Session cases of tools/test_resident.py split out for the 800-line limit (lint-size): the boot refusal (C-02 line guard), host steps
under their own limit, and per-session start settings. Each part is (R, C, T): R = the loaded tools/resident, C = the case list,
T = the tools/test_resident.py module (session_env and the fake phone)."""
import argparse, contextlib, io, json, os, sys, types  # noqa: E401
HERE = os.path.dirname(os.path.abspath(__file__))


def _start_settings(R, C, T):
    """resident start --floor/--set: this session only (session-settings.json), config.json untouched (ALL-ON 2026-11-11: a persisted
    --floor 3500 ran every later session)."""
    case = T.session_env(R, C)[0]
    g = R.main.__globals__
    tmp = T.tmpdir()
    os.environ["RESIDENT_DIR"] = tmp
    k = os.path.join(tmp, "kernel")
    open(k, "w").write("k")
    calls = []
    real = g["subprocess"]
    g["subprocess"] = types.SimpleNamespace(run=lambda *a, **kw: calls.append(a) or types.SimpleNamespace(returncode=0))
    try:
        with contextlib.redirect_stdout(io.StringIO()) as o:
            refused = g["start_session"](argparse.Namespace(kernel=k, floor=3500, max=None, set=None))
        case("start_scratch_dir_refused", refused == 2 and calls == [] and "a scratch dir never drives phone-lock" in o.getvalue(), o.getvalue())
        os.environ["RESIDENT_REAL_PHONE"] = "1"
        with contextlib.redirect_stdout(io.StringIO()):
            g["start_session"](argparse.Namespace(kernel=k, floor=3500, max=None, set=["idle_park_s=600"]))
    finally:
        g["subprocess"] = real
        os.environ.pop("RESIDENT_REAL_PHONE", None)
    conf = json.load(open(os.path.join(tmp, "config.json")))
    once = json.load(open(os.path.join(tmp, "session-settings.json")))
    case("start_floor_not_persisted", conf.get("settings", {}) == {} and once == {"floor_mv": 3500, "idle_park_s": "600"} and len(calls) == 1, (conf, once))
    conf["settings"] = {"cut_soc": 12}
    cfg, lines = g["session_cfg"](conf)
    case("start_settings_merge", cfg["floor_mv"] == 3500 and cfg["idle_park_s"] == 600 and cfg["cut_soc"] == 12.0
         and not os.path.exists(os.path.join(tmp, "session-settings.json"))
         and any("floor_mv=3500 (default 3700) from this start" in ln for ln in lines) and any("cut_soc=12.0" in ln and "persistent" in ln for ln in lines), lines)
    cfg2, lines2 = g["session_cfg"]({"settings": {}})
    case("start_settings_once", cfg2["floor_mv"] == 3700 and lines2 == [], lines2)


def _host_steps(R, C, T):
    """Host steps run after the job's end under their own limit (ALL-ON a53c 2026-11-11: a job that ended on its max killed both host
    steps on their first poll, before they printed anything)."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    j = submit(["--run", "/usr/local/bin/x", "--expect", "NEVER SEEN LINE", "--max", "60", "--host", "echo HOSTDONE {dir}", "--thread", "TH"])
    run(s)
    hp = [pr for a, pr in ph.procs if a[:1] == ["bash"]]
    r = result(tmp, j["id"])
    case("host_runs_after_max", len(hp) == 1 and not getattr(hp[0], "killed", False) and hp[0].returncode == 0
         and r.get("result") == "FAIL" and "expected line(s) missing" in r.get("why", ""), (r, [getattr(p, "killed", False) for p in hp]))
    tmp, ph, s = setup()
    s.cfg["host_step_max_s"] = 30
    j = submit(["--run", "/usr/local/bin/x SAYHELLO", "--expect", "SWEET HELLO", "--host", "LONG host {dir}", "--thread", "TH"])
    out = run(s)
    hp = [pr for a, pr in ph.procs if a[:1] == ["bash"]]
    r = result(tmp, j["id"])
    case("host_step_limit", len(hp) == 1 and getattr(hp[0], "killed", False) and r.get("result") == "FAIL" and "host_step_max_s 30" in r.get("why", "")
         and "RESIDENT GAP: job %s: host step 0 past host_step_max_s" % j["id"] in out, (r, out[-400:]))


def _boot_refused(R, C, T):
    """A boot refused on the host (C-02 line length): its one job ends FAIL with the why (T-51: alone), the phone is never booted."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    ph.boot_refuse = R.BootRefused("boot-arg line 1922 B > 1919 B with --mif-clk: fake")
    j = submit(["--run", "/usr/local/bin/x", "--args", "sweetnull=pixnav", "--thread", "TB"])
    out = run(s)
    r = result(tmp, j["id"])
    case("boot_refused_fail", ph.boots == [] and r.get("result") == "FAIL" and "1922 B > 1919 B" in r.get("why", "")
         and "refused before the phone" in out, (r, out[-400:]))


def _host_infra(R, C, T):
    """devsh's pre-connect refusal (phone-lock check timed out under host load, ALL-ON 66d5): one retry, then NOTRUN as host infra."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    j = submit(["--run", "/usr/local/bin/x INFRA SAYHELLO", "--expect", "SWEET HELLO", "--thread", "TI"])
    out = run(s)
    r = result(tmp, j["id"])
    case("infra_retry_once", r.get("result") == "PASS" and "host infra (cannot run /x/tools/phone-lock check: timed out" in out, (r, out[-300:]))
    tmp, ph, s = setup()
    j = submit(["--run", "/usr/local/bin/x INFRAALWAYS", "--thread", "TI"])
    run(s)
    r = result(tmp, j["id"])
    case("infra_twice_notrun", r.get("result") == "NOTRUN" and "host infra twice at step 0" in r.get("why", ""), r)
    tmp, ph, s = setup()
    j = submit(["--run", "/usr/local/bin/x FAILSTEP", "--thread", "TI"])
    run(s)
    case("plain_fail_no_retry", result(tmp, j["id"]).get("result") == "FAIL" and "host infra" not in open(os.path.join(tmp, "events.log")).read(), result(tmp, j["id"]))


def _submit_guards(R, C, T):
    """resident submit guards G1-G4 (ALL-ON rules, coordinator 2026-11-12): a refused job is never queued; --guard-override = GAP."""
    case, setup, submit, run, result = T.session_env(R, C)
    parse = T.session_env.parse
    g = R.main.__globals__
    tmp = T.tmpdir()
    os.environ["RESIDENT_DIR"] = tmp
    kbad, kgood = os.path.join(tmp, "kbad"), os.path.join(tmp, "kgood")
    open(kbad, "wb").write(b"no features")
    open(kgood, "wb").write(b"x SWEET GESTURE: id %u begin y sweet: swaps and distinct frames z SWEET_BOOT_LINE_LENGTH=2048\0")
    host = os.path.join(tmp, "pixnav")
    open(host, "w").write("bin")
    json.dump({"kernel": kbad, "settings": {}}, open(os.path.join(tmp, "config.json"), "w"))
    json.dump({"p1": {"args": "sweetfoo=1", "xrun": []}, "pm": {"args": "", "xrun": ["--mif-clk"]},
               "pk": {"args": "", "xrun": [], "needs_kernel": ["SWEET GESTURE: id %u begin"]}}, open(os.path.join(tmp, "profiles.json"), "w"))

    def sub(argv, line=None, live=None, pre=""):
        saved = dict(g)
        g["prebuild_job"] = lambda *a_: pre(*a_) if callable(pre) else pre   # G7 stub: no real build in host tests
        if line is not None:
            g["print_bootline"] = lambda xr, env: (line, "")
        if live is not None:
            g["session_live"] = lambda: True
            json.dump(dict({"pid": 1}, **live), open(os.path.join(tmp, "session.json"), "w"))
        try:
            with contextlib.redirect_stdout(io.StringIO()) as o:
                j = R.build_job(parse(argv), guard=R.submit_guards)
            return j, o.getvalue()
        except Exception as e:   # lint-silent: expected the refusal under test (a Fail with its printed JOB REFUSED lines)
            return None, o.getvalue() + str(e)
        finally:
            g.update(saved)
            if live is not None:
                os.unlink(os.path.join(tmp, "session.json"))
    queued = lambda: len(os.listdir(os.path.join(tmp, "queue"))) if os.path.isdir(os.path.join(tmp, "queue")) else 0  # noqa: E731
    j, o = sub(["--run", "/usr/local/bin/x"])
    case("g2_no_session_refused", j is None and "JOB REFUSED (G2): no --profile/--kernel and no running session" in o and "Escape: --guard-override" in o and queued() == 0, o)
    j, o = sub(["--args", "sweetx=1"], live={"boot_profile": "p1", "boot_kernel": kbad, "boot_kernel_sha": "other"})
    case("g2_switch_refused", j is None and "would switch to plain" in o, o)
    j, o = sub(["--run", "/usr/local/bin/x"], live={"boot_profile": "p1", "boot_kernel": kbad, "boot_kernel_sha": R.sha256(kbad)})
    case("g2_inherit_ok", j is not None, o)
    j, o = sub(["--profile", "p1", "--args", "sweetfoo=2"])
    case("g4_refused", j is None and "JOB REFUSED (G4): --args overrides the profile: sweetfoo=2 (profile 1)" in o, o)
    j, o = sub(["--profile", "p1", "--push", host + ":/usr/local/bin/pixnav"])
    case("g3_table_refused", j is None and "JOB REFUSED (G3): kernel kbad lacks: SWEET GESTURE: id %u begin (needed by push pixnav)" in o, o)
    j, o = sub(["--profile", "p1", "--kernel", kgood, "--push", host + ":/usr/local/bin/pixnav"])
    case("g3_table_ok", j is not None, o)
    _guards_t50_g7(R, case, sub, g, parse, tmp, kgood)
    j, o = sub(["--profile", "p1", "--kernel", kbad, "--run", "/usr/local/bin/x"])
    case("g6_no_0320_refused", j is None and "JOB REFUSED (G6): kernel " in o and "lacks 'SWEET_BOOT_LINE_LENGTH=2048'" in o, o)
    j, o = sub(["--profile", "p1", "--kernel", kgood, "--needs-kernel", "NOPE[0-9]"])
    case("g3_needs_refused", j is None and "NOPE[0-9] (needed by --needs-kernel)" in o, o)
    j, o = sub(["--profile", "pk"])
    case("g3_profile_needs_refused", j is None and "lacks: SWEET GESTURE: id %u begin (needed by profile pk)" in o, o)
    j, o = sub(["--profile", "pk", "--kernel", kgood])
    case("g3_profile_needs_ok", j is not None, o)
    j, o = sub(["--profile", "pm"], line="a" * 1922)
    case("g1_refused", j is None and "JOB REFUSED (G1): boot-arg line 1922 B > 1919 B" in o, o)
    j, o = sub(["--profile", "pm"], line="a" * 1900)
    g_saved = g["print_bootline"]
    g["print_bootline"] = lambda xr, env: ("a" * 842, "boot-args: x\nREFUSED: boot-args are 842 bytes; with --pmic-src-clear and --mif-clk the limit is 824 (breadcrumb)\n")
    try:
        with contextlib.redirect_stdout(io.StringIO()) as o2:
            try:
                R.build_job(parse(["--profile", "p1"]), guard=R.submit_guards)
                jr = True
            except R.Fail:   # lint-silent: expected the refusal under test (a Fail, its JOB REFUSED lines printed)
                jr = None
    finally:
        g["print_bootline"] = g_saved
    case("g1_xrun_refusal_wins", jr is None and "JOB REFUSED (G1): boot-args are 842 bytes" in o2.getvalue() and "limit is 824" in o2.getvalue(), o2.getvalue())
    case("g1_ok", j is not None, o)
    j, o = sub(["--profile", "p1", "--host", "tools/no-such-tool {dir}"])
    case("g5_host_missing_refused", j is None and "JOB REFUSED (G5): host step program tools/no-such-tool not found" in o, o)
    here = os.getcwd()
    os.chdir(os.path.dirname(HERE))   # the kernel worktree: tools/resident exists under it
    try:
        j, o = sub(["--profile", "p1", "--host", "tools/resident {dir}"])
    finally:
        os.chdir(here)
    case("g5_host_present_ok", j is not None, o)
    q0 = queued()
    j, o = sub(["--profile", "p1", "--dry-run"])
    case("dry_run_not_queued", j is not None and queued() == q0 and not os.path.exists(os.path.join(tmp, "jobs", j["id"])), (q0, queued(), o))
    j, o = sub(["--run", "/usr/local/bin/x", "--guard-override", "test"])
    case("guard_override_gap", j is not None and "guard G2 overridden (test)" in open(os.path.join(tmp, "events.log")).read(), o)


def _mirror_not_panic(R, C, T):
    """A mirrored |prog| line with 'Assertion failed' / 'panic:' text is the program's own output, not a kernel crash (ALL-ON T-28)."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    j = submit(["--run", "/usr/local/bin/x MIRRORASSERT SAYHELLO", "--expect", "SWEET HELLO", "--thread", "TM"])
    out = run(s)
    case("mirror_assert_not_crash", result(tmp, j["id"]).get("result") == "PASS" and "RESIDENT CRASH" not in out, (result(tmp, j["id"]), out[-300:]))


def _owner_notices(R, C, T):
    """Job notices go to the stream owner resolved at send time: a former owner's job wakes the new owner, a key without an owner
    sends nothing (GAP line)."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    od = os.path.join(tmp, "owners")
    os.makedirs(od)
    old = os.environ.get("PIXOS_OWNERS_DIR")
    os.environ["PIXOS_OWNERS_DIR"] = od
    try:
        open(os.path.join(od, "all-on"), "w").write("NEWT\nformer OLDT\n")
        j1 = submit(["--run", "/usr/local/bin/x", "--thread", "OLDT"])
        j2 = submit(["--run", "/usr/local/bin/x", "--thread", "SOMEONE", "--owner", "ghost"])
        out = run(s)
        to = [t for t, l in ph.notes if j1["id"] in l]
        case("notice_former_to_owner", to and set(to) == {"NEWT"} and not [t for t, l in ph.notes if t == "OLDT"], ph.notes)
        case("notice_no_owner_gap", not [t for t, l in ph.notes if j2["id"] in l] and "notice for job %s not sent" % j2["id"] in out, out[-400:])
    finally:
        if old is None:
            os.environ.pop("PIXOS_OWNERS_DIR", None)
        else:
            os.environ["PIXOS_OWNERS_DIR"] = old


def core_moved(rc, cfg, case):
    """Core cases moved out of test_resident._core_b for the size limits (soc rule, C-02 line, submit guards, notice routing); run by
    test_resident.core_cases, so the core mutants see them."""
    good = [(t, 3900, 100000, False) for t in range(0, 600, 30)]
    up = 0

    def bat(samples, cf=None):
        b = rc.Battery(cf or cfg)
        for x in samples:
            b.add(*x)
        return b
    # fuel-gauge soc (patch 0307 rows): soc decides floor/charge-wait/cut, the loaded vcell does not
    s_ok = [(t, 3600, -1300000, False, 72.48) for t in range(0, 90, 30)]
    v = bat(s_ok).verdict(90, 0)
    case("soc_rules_over_comp", v[0] == "ok" and "soc 72.48 %" in v[1] and bat(s_ok).mode() == "soc" and bat(s_ok).cut() == "")
    case("soc_floor", bat([(t, 3950, 100000, False, 14.0) for t in range(0, 90, 30)]).verdict(90, 0)[0] == "park")
    case("soc_floor_median", bat([(t, 3950, 100000, False, 50.0) for t in range(0, 60, 30)] + [(60, 3950, 100000, False, 5.0)]).verdict(90, 0)[0] == "ok")
    case("soc_charge_wait", bat([(t, 3950, 100000, False, 16.5) for t in range(0, 90, 30)]).verdict(90, 0)[0] == "charge-wait"
         and bat([(t, 3950, 100000, False, 18.5) for t in range(0, 90, 30)]).verdict(90, 0)[0] == "ok")
    case("soc_cut", "soc 9.50 %" in bat([(0, 3950, -100000, True, 9.5)]).cut() and bat([(0, 3950, -100000, True, 10.5)]).cut() == "")
    case("soc_raw_hard_still", bat([(t, 3450, -2000000, True, 40.0) for t in range(0, 90, 30)]).verdict(90, 0)[0] == "park"
         and "hard limit" in bat([(t, 3450, -2000000, True, 40.0) for t in range(0, 90, 30)]).cut())
    bp = bat([(t, 3900, -500000, False) for t in range(0, 90, 30)])
    bp.boot_soc = 12.0
    bq = bat([(t, 3900, -500000, False) for t in range(0, 90, 30)])
    bq.boot_soc = 72.48
    case("p2_fallback", bp.mode() == "vcell+p2" and bp.verdict(90, 0)[0] == "park" and "P2" in bp.verdict(90, 0)[1]
         and bq.verdict(90, 0)[0] == "ok" and bat([(0, 3900, -500000, False)]).mode() == "vcell")
    # --fresh-boot (lane-t02 Q-01): its own boot, nothing merges in; a fresh job never joins another batch
    mk = lambda jid, fresh, n: {"id": jid, "prio": 1, "est_s": 10, "submitted": n, "spec": {"profile": "plain", "kernel": None, "kernel_sha": None,  # noqa: E731
                                "args": {}, "overlay": None, "overlay_files": {}, "fresh": fresh}, "kind": "kernel", "max_s": 60}
    sp, ba, le = rc.plan_boot([mk("A", True, 1), mk("B", False, 2)], 100, cfg, "K", "S")
    sp2, ba2, le2 = rc.plan_boot([mk("B", False, 1), mk("A", True, 2)], 100, cfg, "K", "S")
    case("fresh_plan", sp["for"] == "A" and ba == ["A"] and le[0][0] == "B" and ba2 == ["B"] and le2[0][0] == "A" and "for" not in sp2)
    case("fresh_compat", rc.compatible(mk("A", True, 1), sp, "S") and not rc.compatible(mk("A", True, 1), sp2, "S") and rc.compatible(mk("B", False, 1), sp2, "S"))
    # T-17: with soc rows, 'falling' = projected below floor + margin within falling_horizon_s (all-on draws -1.1 A idle at 73 %)
    drop = lambda soc0, per10: [(t, 3800, -1100000, False, soc0 - per10 * t / 600.0) for t in range(330, 900, 30)]  # noqa: E731
    v1 = bat(drop(73.0, 4.0)).verdict(900, 0)
    v2 = bat(drop(26.0, 4.0)).verdict(900, 0)
    case("soc_falling_far_from_floor_ok", v1[0] == "ok")
    case("soc_falling_toward_floor_parks", v2[0] == "park" and "dropping 4.00 %/10 min" in v2[1])
    case("soc_flat_negative_current_ok", bat([(t, 3800, -900000, False, 50.0) for t in range(330, 900, 30)]).verdict(900, 0)[0] == "ok")
    case("soc_falling_ignores_settle", bat([(t, 3800, -1100000, False, 26.0 - 4.0 * t / 600.0) for t in range(30, 330, 15)]).verdict(330, 0)[0] == "ok")
    case("vcell_falling_rule_unchanged", bat([(t, 3900, -50000, False) for t in range(330, 900, 30)]).verdict(900, 0)[0] == "park")
    br = bat(s_ok)
    br.reset()
    case("bat_reset", br.samples == [] and br.boot_soc is None and br.mode() == "vcell")
    # C-02 guard: a --mif-clk boot-arg line over 895 B is refused before the boot (shim mifclk.c:221)
    case("psrc_limit", rc.line_limit(["--pmic-src"])[0] == 1997 and rc.line_limit(["--pmic-src", "--mif-clk"])[0] == 1896 and rc.line_limit(["--dvfs"])[0] is None
         and rc.mif_line_check(["--pmic-src", "--mif-clk"], "a" * 1896) == "" and "1897 B > 1896 B" in rc.mif_line_check(["--pmic-src", "--mif-clk"], "a" * 1897)
         and "--pmic-src" in rc.mif_line_check(["--pmic-src"], "a" * 1998) and rc.mif_line_check(["--pmic-src"], "a" * 1997) == "")
    case("mif_limit", rc.MIF_LINE_MAX == 1919 and rc.mif_line_check(["--mif-clk"], "a" * 1919) == "" and "1922 B > 1919 B" in rc.mif_line_check(["--x", "--mif-clk"], "a" * 1922)
         and rc.mif_line_check(["--dvfs"], "a" * 2000) == "" and rc.mif_line_check(["--mif-clk"], None) == ""
         and rc.mif_line_check(["--mif-int-pair"], "a" * 1922) != "")   # --mif-int-pair implies --mif-clk (same tokens)
    # wall clock 2026-11-13 (xnu 0345, xrun adds sweetepoch=<host seconds> itself, always 22 characters): the token is in the line xrun --print-bootargs prints, so the shim-append limits count it;
    # it is never in a job's --args, so batching (same_args / compatible) and the G7 prebuild (builds, then discards the image) never compare it: a different time per submit changes nothing
    ep1, ep2 = " sweetepoch=1794585637", " sweetepoch=1794599999"
    room = "a" * (rc.MIF_LINE_MAX - len(ep1))
    case("epoch_token_counted", len(ep1) == len(ep2) == 22 and rc.mif_line_check(["--mif-clk"], room + ep1) == "" and rc.mif_line_check(["--mif-clk"], room + ep2) == ""
         and "B > 1919 B" in rc.mif_line_check(["--mif-clk"], room + "b" + ep1) and rc.bootline_of("implicit: sweetepoch=1794585637\nboot-args: x" + ep1 + "\n") == "x" + ep1)
    case("epoch_not_in_batching", rc.same_args({"sweetfoo": "1"}, {"sweetfoo": "1"}) and not rc.same_args({"sweetepoch": "1794585637"}, {"sweetepoch": "1794599999"})
         and not rc.same_args({"sweetepoch": "1794585637"}, {}))   # an explicit sweetepoch= in --args is an ordinary key: jobs with different values keep their own boot (documented, not normalised)
    sys.path.insert(0, HERE)
    import mkpayload
    case("bootline_len_one_source", rc.BOOT_LINE_LENGTH == mkpayload.BOOT_LINE_LENGTH)
    case("bootline_of", rc.bootline_of("implicit: sweetremote=1\nMIF CLK: request on\nboot-args: debug=0x8 a=b\n") == "debug=0x8 a=b" and rc.bootline_of("x\n") is None)
    # notice routing (coordinator 2026-11-12: replaced threads kept waking)
    ow = {"all-on": rc.parse_owner_file("# c\nNEW\nformer OLD1\nformer OLD2\n"), "lab": ("LAB", [])}
    case("owner_file", ow["all-on"] == ("NEW", ["OLD1", "OLD2"]))
    case("notice_routes", rc.resolve_notice("OLD2", None, ow) == ("NEW", "submitter replaced: owner all-on")
         and rc.resolve_notice("X", "all-on", ow)[0] == "NEW" and rc.resolve_notice("X", None, ow) == ("X", "submitter")
         and rc.resolve_notice("X", "nokey", ow)[0] is None and rc.resolve_notice(None, None, ow)[0] is None
         and rc.resolve_notice("OLD1", None, {"all-on": (None, ["OLD1"])})[0] is None and rc.resolve_notice("LAB", None, ow) == ("LAB", "owner lab"))


def _fresh_and_requeue(R, C, T):
    """lane-t02 Q-01: --fresh-boot = its own boot after the job before it; --requeue-on-crash = after a crash the phone parks and the job
    runs in a new session (once)."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    j1 = submit(["--run", "/usr/local/bin/x", "--fresh-boot"])
    j2 = submit(["--run", "/usr/local/bin/x", "--fresh-boot"])
    run(s)
    case("fresh_two_boots", len(ph.boots) == 2 and result(tmp, j1["id"]).get("result") == "PASS" and result(tmp, j2["id"]).get("result") == "PASS",
         [b[0] for b in ph.boots])
    tmp, ph, s = setup()
    j1 = submit(["--run", "/bin/x PANICME", "--fresh-boot"])
    j2 = submit(["--run", "/usr/local/bin/x", "--fresh-boot", "--requeue-on-crash"])
    j3 = submit(["--run", "/usr/local/bin/x", "--fresh-boot"])
    out = run(s)
    jj = json.load(open(os.path.join(tmp, "jobs", j2["id"], "job.json")))
    case("requeue_on_crash", result(tmp, j1["id"]).get("result") == "CRASH" and not result(tmp, j2["id"]) and not result(tmp, j3["id"])
         and ph.restarted == 1 and ph.restart_saw_state == "ended" and "NOTRUN" not in out
         and sum("a new session runs it after the park" in l for _, l in ph.notes) == 2, (out[-500:], ph.restarted))   # user 2026-11-12: queue kept
    s2 = R.Session(ph, R.rc.cfg_with(), s.std_kernel, s.profiles)
    s2.tick = 20
    ph.feed.append((ph.t + 3, "panic(cpu 0 caller 0x0): fake again\n"))
    run(s2)
    case("requeue_never_notrun", "NOTRUN" not in json.dumps([result(tmp, j2["id"]), result(tmp, j3["id"])]), [result(tmp, j2["id"]), result(tmp, j3["id"])])


def _batch_fit(R, C, T):
    """T-51 (lane-t02 991d+e5ad 832 B > 824 B; allon-k21 build fail): a merged boot runs only when proven to build; a refused or unbuildable
    boot ends alone (one job FAIL, several solo), the session goes on and the other jobs run."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    k2 = os.path.join(tmp, "k2")
    open(k2, "w").write("other kernel")
    ph.fit_refuse = lambda spec: "boot-arg line 832 B > 824 B: fake"
    a = submit(["--kernel", k2, "--args", "sweeta=1", "--run", "/bin/a", "--thread", "TA"])
    b = submit(["--kernel", k2, "--args", "sweeta=1", "--run", "/bin/b", "--thread", "TB"])
    out = run(s)
    jb = json.load(open(os.path.join(tmp, "jobs", b["id"], "job.json")))
    case("fit_split_runs_both", len(ph.boots) == 1 and ph.boots[0][1]["args"] == {"sweeta": "1"}
         and result(tmp, a["id"]).get("result") == "PASS" and result(tmp, b["id"]).get("result") == "PASS" and "merged boot line not proven" in out
         and "832 B > 824 B" in jb.get("solo", ""), ([x[1]["args"] for x in ph.boots], out[-600:]))
    case("fit_checked_before_boot", ph.fits and ph.fits[0][1] == {"sweeta": "1"}, ph.fits)
    tmp, ph, s = setup()
    open(os.path.join(tmp, "k2"), "w").write("other kernel")
    a = submit(["--kernel", os.path.join(tmp, "k2"), "--args", "sweeta=1", "--run", "/bin/a"])
    b = submit(["--kernel", os.path.join(tmp, "k2"), "--args", "sweeta=1", "--run", "/bin/b"])
    run(s)
    case("fit_proven_merges", [x[1]["args"] for x in ph.boots] == [{"sweeta": "1"}] and len(ph.fits) == 1, ph.boots)
    # a job whose kernel does not build ends alone as FAIL; the other lane's job (another kernel) still runs; no crash, no NOTRUN
    tmp, ph, s = setup()
    kbad = os.path.join(tmp, "kbad")
    open(kbad, "w").write("kernel without 0320")
    ph.build_fail = lambda spec: "without 0320" in open(spec["kernel"]).read()   # submit copies the kernel into the job dir
    x = submit(["--kernel", kbad, "--run", "/bin/x", "--thread", "TX", "--prio", "1"])
    y = submit(["--run", "/bin/y", "--thread", "TY", "--profile", "gpu"])
    out = run(s)
    rx, ry = result(tmp, x["id"]), result(tmp, y["id"])
    case("build_fail_alone", rx.get("result") == "FAIL" and "build-xnu-image failed" in rx.get("why", "") and ry.get("result") == "PASS"
         and "RESIDENT CRASH" not in out and "NOTRUN" not in out and ph.fits == [], (rx, ry, ph.fits, out[-700:]))
    # two merged jobs whose boot does not build: both solo, each tries alone; only the bad one FAILs
    tmp, ph, s = setup()
    k2 = os.path.join(tmp, "k2")
    open(k2, "w").write("other kernel")
    nb = []
    ph.build_fail = lambda spec: nb.append(1) or len(nb) == 1   # the merged (first) build fails, the solo ones build
    g = submit(["--kernel", k2, "--args", "sweetok=1", "--run", "/bin/g"])
    h = submit(["--kernel", k2, "--args", "sweetok=1", "--run", "/bin/h"])
    out = run(s)
    case("build_fail_batch_solo", result(tmp, g["id"]).get("result") == "PASS" and result(tmp, h["id"]).get("result") == "PASS"
         and len(ph.boots) == 2 and "boot resident" in out and "of 2 jobs did not build" in out and "RESIDENT CRASH" not in out,
         ([x[1]["args"] for x in ph.boots], out[-700:]))
    # rule 1 / 2 helpers on real files: the kernel tag of this tree's mkpayload, the host-build die line
    import resident_fit as rf
    tag = rf.kernel_tag()
    kt = os.path.join(tmp, "kt")
    open(kt, "wb").write(b"x" + (tag or b"") + b"y")
    case("kernel_tag_check", tag == b"SWEET_BOOT_LINE_LENGTH=2048\0" and rf.kernel_tag_check(kt, tag) == ""
         and "lacks 'SWEET_BOOT_LINE_LENGTH=2048'" in rf.kernel_tag_check(kbad, tag) and rf.kernel_tag_check(kt, None) is None, tag)
    xo = os.path.join(tmp, "xrun.out")
    open(xo, "w").write("XNU BUILD: x\nRESULT: ERROR build-xnu-image failed: ==> build.out <==\nRUN: label\n")
    case("host_fail_in", rf.host_fail_in(xo).startswith("RESULT: ERROR build-xnu-image failed") and rf.host_fail_in(os.path.join(tmp, "none")) == ""
         and rf.host_fail_in(kt) == "", rf.host_fail_in(xo))


def core_solo(rc, cfg, case):
    """T-51 core: a solo job (its merged boot was not proven to build) boots alone, as seed and as a later job."""
    import test_resident as T
    k1 = T.job(1, kind="kernel", ksha=T.KSHA, args={"sweeta": "1"})
    k2 = T.job(2, kind="kernel", ksha=T.KSHA, args={"sweetb": "1"})
    k1["solo"] = "fake"
    _, b1, l1 = rc.plan_boot([k1, k2], 100, cfg, "/k", T.SHA)
    case("solo_seed_alone", b1 == ["j01"] and l1 and "solo boot" in l1[0][1])
    k1.pop("solo")
    k2["solo"] = "merged line refused"
    _, b2, l2 = rc.plan_boot([k1, k2], 100, cfg, "/k", T.SHA)
    case("solo_not_merged", b2 == ["j01"] and l2 and "merged line refused" in l2[0][1])
    k1.pop("solo", None)
    _, bd, ld = rc.plan_boot([k1, T.job(5, kind="kernel", ksha=T.KSHA, args={"sweeta": "1", "sweetb": "2"})], 100, cfg, "/k", T.SHA)
    case("batch_disjoint_not_merged", bd == ["j01"] and "identical" in ld[0][1])   # T-51: 991d + e5ad were disjoint keys
    boot = T.std_boot(rc, args={"sweetx": "1"})
    case("compat_kernel_exact_line", not rc.compatible(T.job(1, kind="kernel"), boot, T.SHA) and rc.compatible(T.job(1, kind="kernel", args={"sweetx": "1"}), boot, T.SHA))


def verdict_cases(C, T):
    """resident_fit.bootline_verdict on a fake xrun (its REFUSED: line, line_limit, the kernel tag)."""
    import resident_fit as rf
    case = lambda n, ok, info="": (C.append((n, bool(ok))), ok or print("  case %s info: %s" % (n, str(info)[:600])))  # noqa: E731
    d = T.tmpdir()
    xr, kt, kb = os.path.join(d, "xrun"), os.path.join(d, "kt"), os.path.join(d, "kb")
    open(kt, "wb").write(b"x" + (rf.kernel_tag() or b"") + b"y")
    open(kb, "wb").write(b"no tag")
    open(xr, "w").write('#!/bin/sh\ncase "$*" in *REFUSEME*) echo "REFUSED: boot-args are 832 bytes, limit 824" >&2; exit 2;; esac\n'
                        'case "$*" in *LONGLINE*) printf "boot-args: %01950d\\n" 0;; *) echo "boot-args: a=1";; esac\n')
    os.chmod(xr, 0o755)
    gaps = []
    v = lambda *extra, k=kt: rf.bootline_verdict([xr, "--kernel", k] + list(extra), dict(os.environ), gaps.append)  # noqa: E731
    case("verdict_ok", v() == ("", "a=1") and not gaps, (v(), gaps))
    case("verdict_refused", v("REFUSEME")[0] == "boot-args are 832 bytes, limit 824", v("REFUSEME"))
    case("verdict_limit", "> 1919 B" in v("LONGLINE", "--mif-clk")[0] and v("LONGLINE")[0] == "", v("LONGLINE", "--mif-clk"))
    case("verdict_tag", "xnu patch 0320 missing" in v(k=kb)[0], v(k=kb))
    prebuild_cases(C, T)


def _guards_t50_g7(R, case, sub, g, parse, tmp, kgood):
    """T-50 (G1 with the running session's tree) and G7 (the image built at submit, user 2026-11-12); split out of _submit_guards (size)."""
    seen, saved = [], dict(g)   # T-50: with a live session from another tree, G1 checks with THAT tree's xrun
    g["print_bootline"] = lambda xr, env: seen.append(xr[0]) or ("a=1", "")
    g["prebuild_job"] = lambda job, prof, pname, kern, tools: seen.append(tools) or ""
    g["session_live"] = lambda: True
    json.dump({"pid": 1, "tools": "/other/tree/tools", "boot_profile": "p1", "boot_kernel": kgood, "boot_kernel_sha": R.sha256(kgood)},
              open(os.path.join(tmp, "session.json"), "w"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            R.build_job(parse(["--profile", "p1", "--kernel", kgood, "--run", "/usr/local/bin/x"]), guard=R.submit_guards)
    finally:
        g.update(saved)
        os.unlink(os.path.join(tmp, "session.json"))
    case("g1_session_tree_xrun", seen == ["/other/tree/tools/xrun", "/other/tree/tools"], seen)   # G7 builds with the session's tree too
    j, o = sub(["--profile", "p1", "--kernel", kgood, "--run", "/usr/local/bin/x"], pre="RESULT: ERROR build-xnu-image failed: fake")
    case("g7_build_refused", j is None and "JOB REFUSED (G7): its image does not build: RESULT: ERROR build-xnu-image failed: fake" in o, o)
    j, o = sub(["--profile", "p1", "--kernel", kgood, "--run", "/usr/local/bin/x"], pre=None)
    case("g7_old_tree_gap", j is not None and "has no XRUN_BUILD_ONLY" in open(os.path.join(tmp, "events.log")).read(), o)
    j, o = sub(["--profile", "p1", "--kernel", kgood, "--run", "/usr/local/bin/x"])
    case("g7_built_ok", j is not None and "G7 image built and checked" in o, o)
    xs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xrun")).read()   # build-only stops after the build, BEFORE the phone lock
    case("g7_xrun_stops_before_lock", 0 < xs.index("\nbuild_image\n") < xs.index('if [ -n "${XRUN_BUILD_ONLY:-}" ]') < xs.index("plock_take xrun"))


def prebuild_cases(C, T):
    """resident_fit.prebuild (G7) on a fake xrun: PASS only with XRUN_BUILD_ONLY set and its PASS line; a failure quotes mkpayload's line."""
    import resident_fit as rf
    case = lambda n, ok, info="": (C.append((n, bool(ok))), ok or print("  case %s info: %s" % (n, str(info)[:600])))  # noqa: E731
    d = T.tmpdir()
    os.makedirs(os.path.join(d, "tools"))
    os.makedirs(os.path.join(d, "logs", "runs", "R-x"))
    open(os.path.join(d, "logs", "runs", "R-x", "xrun.log"), "w").write("ADT GAP: x\nmkpayload: k lacks 'SWEET_BOOT_LINE_LENGTH=2048' (fake)\n")
    xr = os.path.join(d, "tools", "xrun")
    open(xr, "w").write('#!/bin/sh\n[ "$XRUN_BUILD_ONLY" = 1 ] || { echo "would take the phone"; exit 9; }\n'
                        'case "$*" in *DIE*) exit 3;; esac\n'
                        'case "$*" in *BAD*) echo "RESULT: ERROR build-xnu-image failed: ==> logs/runs/R-x/build.out <=="; exit 2;; esac\n'
                        'echo "RESULT: PASS build-only label=x boot-args: a=1"\n')
    os.chmod(xr, 0o755)
    out = os.path.join(d, "pb.out")
    case("prebuild_ok", rf.prebuild([xr, "--kernel", "k"], dict(os.environ), out) == "" and "PASS build-only" in open(out).read())
    why = rf.prebuild([xr, "--kernel", "k", "BAD"], dict(os.environ), out)
    case("prebuild_quotes_mkpayload", why.startswith("mkpayload: k lacks 'SWEET_BOOT_LINE_LENGTH=2048'") and "xrun rc 2" in why, why)
    case("prebuild_silent_death", "no RESULT line" in rf.prebuild([xr, "--kernel", "k", "DIE"], dict(os.environ), out))
    case("xrun_build_only_probe", rf.xrun_has_build_only(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xrun")) and not rf.xrun_has_build_only(out))


def thermal_cases(C, T):
    """SAFETY-CHECKLIST S1-S3 on resident_thermal.Thermal directly (no session)."""
    import resident_thermal as rt
    case = lambda n, ok, info="": (C.append((n, bool(ok))), ok or print("  case %s info: %s" % (n, str(info)[:600])))  # noqa: E731
    row = lambda c: "SWEET CHG MON: t=330s current=-927187 uA vcell=3736 mV soc=65.63 %% temp=%.1f C\n" % c  # noqa: E731  (resident-204614-b1 format)
    th = rt.Thermal(None, os.path.join(T.tmpdir(), "thermal.json"))
    th.boot(1000)
    case("th_no_row_early_ok", th.live(1000 + 479) == "")
    case("th_s3_first_row_missing", "SAFETY FAIL S3: no temp= row within 480 s" in th.live(1000 + 481))
    th.feed(1100, row(44.9))
    case("th_below_hot_ok", th.live(1150) == "")
    case("th_s3_gap", "SAFETY FAIL S3: no temp= row for 61 s" in th.live(1161), th.live(1161))
    th.feed(1170, row(45.0))
    case("th_s1_hot", th.live(1171).startswith("SAFETY FAIL S1: battery temp 45.0 C >= 45.0 C"), th.live(1171))
    th.parked(1200, th.live(1171))
    st = json.load(open(th.path))
    case("th_hot_state_saved", st.get("hot") is True and st.get("parked_at") == 1200 and st.get("last_c") == 45.0, st)
    th2 = rt.Thermal(None, th.path)   # the next session reads the state
    case("th_s2_storage_hot", "storage-temperature 38 C >= 37.0 C" in th2.pre_boot(1300, 38.0) and "storage-temperature 37 C >= 37.0 C" in th2.pre_boot(1300, 37.0) and th2.pre_boot(1300, 36.9) == "")   # L-12 admit margin 1 C
    case("th_s2_unreadable_waits", "parked 100 s < 900 s" in th2.pre_boot(1300, None) and th2.pre_boot(1200 + 900, None) == "", th2.pre_boot(1300, None))
    th2.boot(3000)
    th2.feed(3400, row(38.5))
    case("th_s2_first_row_after_hot", "SAFETY FAIL S2: first battery row after a hot park 38.5 C" in th2.live(3401), th2.live(3401))
    th3 = rt.Thermal(None, th.path)
    th3.boot(5000)
    th3.feed(5400, row(31.0))
    case("th_cool_first_row_clears", th3.live(5401) == "" and th3.st.get("hot") is False)
    # L-17 incident [obs 2026-11-14 23:42Z]: last in-boot battery reading 40 C, parked 751 s, fastboot storage 36 C: xrun's pre S2 refuses, so must the resident
    import safety_lib as SL
    sd = os.path.join(T.tmpdir(), "safety17")
    SL.save_state(sd, {"temp_c": 40.0, "end_at": 10000.0})
    th4 = rt.Thermal(None, os.path.join(T.tmpdir(), "thermal17.json"))
    old_art, os.environ["ARTIFACTS"] = os.environ.get("ARTIFACTS"), T.tmpdir()   # no phone-lock history of the real host in last_park
    try:
        w1, w2 = rt.shared_s2(10751.0, sd), rt.shared_s2(10901.0, sd)
        same = rt.shared_s2(10751.0, sd)[:2] == SL.s2_state(SL.load_state(sd), 10751.0)[:2]
    finally:
        os.environ.pop("ARTIFACTS") if old_art is None else os.environ.__setitem__("ARTIFACTS", old_art)
    case("th_l17_incident_waits", "last battery temp 40.0 C" in th4.pre_boot(10751.0, 36.0, w1) and w1[2] == 149 and "149 s" in th4.pre_boot(10751.0, 36.0, w1), (w1, th4.pre_boot(10751.0, 36.0, w1)))
    case("th_l17_parked_admits", th4.pre_boot(10901.0, 36.0, w2) == "" and th4.pre_boot(10901.0, 37.0, w2) != "", (w2, th4.pre_boot(10901.0, 36.0, w2)))
    case("th_l17_same_rule", same)
    case("th_rx_p3_without_temp", not rt.TEMP_RX.search("SWEET CHG P3: t=30s current=-385625 uA vcell=3810 mV soc=69.48 % cnfg09=0xa2"))


def _thermal(R, C, T):
    """S1 in a session: a hot row stops the running job (CUT, SAFETY FAIL S1), the phone parks, the queue stays, and the next boot waits
    in fastboot until S2; S3: rows without temp= end the boot too."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup(temp=lambda t: 50.0 if t > 400 else 30.0, storage=lambda t: 30.0 if t < 100 or t >= 3000 else 40.0)
    j1 = submit(["--run", "/bin/x LONG", "--thread", "TH", "--max", "1200"])
    j2 = submit(["--run", "/bin/y", "--thread", "TW", "--max", "120", "--profile", "gpu"])
    out = run(s)
    r1 = result(tmp, j1["id"])
    st = json.load(open(os.path.join(tmp, "thermal.json")))
    case("s_th_hot_cuts_job", r1.get("result") == "CUT" and "SAFETY FAIL S1: battery temp 50.0 C" in r1.get("why", ""), (r1, out[-600:]))
    case("s_th_parks_keeps_queue", "RESIDENT PARK: SAFETY FAIL S1" in out and not result(tmp, j2["id"]) and ph.restarted == 1 and st.get("hot") is True
         and any("next boot waits until the phone has cooled" in l for _, l in ph.notes), (out[-600:], st))
    s2 = R.Session(ph, R.rc.cfg_with(), s.std_kernel, s.profiles)
    s2.tick = 20
    nb = len(ph.boots)
    out2 = run(s2)
    case("s_th_waits_until_cool", "storage-temperature 40 C >= 37.0 C" in out2 and len(ph.boots) == nb + 1 and ph.boots[-1][1]["profile"] == "gpu"
         and ph.boot_t - ph.t0 >= 3000, (out2[-500:], ph.boot_t - ph.t0))
    # L-17 in a session: storage cool but xrun's S2 rule not yet: RESIDENT WAIT names the shared reason, the boot starts only after it admits
    tmp, ph, s = setup(storage=30.0)
    ph.s2 = lambda t: (t >= 900, "last battery temp 40.0 C >= 38.0 C; parked %d s < 900 s" % t, max(0, 900 - t))
    j4 = submit(["--run", "/bin/x", "--thread", "T4", "--max", "120"])
    out4 = run(s)
    case("s_th_l17_waits_for_shared_s2", "RESIDENT WAIT: SAFETY FAIL S2 (the rule xrun's safety-check pre applies): last battery temp 40.0 C" in out4
         and ph.boot_t - ph.t0 >= 900 and result(tmp, j4["id"]).get("result") == "PASS", (out4[-500:], ph.boot_t - ph.t0))
    tmp, ph, s = setup(temp=lambda t: None)
    j3 = submit(["--run", "/bin/x LONG", "--thread", "T3", "--max", "1200"])
    run(s)
    case("s_th_s3_no_temp_rows", "SAFETY FAIL S3" in result(tmp, j3["id"]).get("why", ""), result(tmp, j3["id"]))


def _safety_check(R, C, T):
    """RULES H9 through xrun: a pre S2 refusal = the jobs wait (no crash); a pre S9 refusal = that job FAIL alone; a live S1 stop = the running
    job CUT with the line, the queue kept, a new session started."""
    case, setup, submit, run, result = T.session_env(R, C)
    tmp, ph, s = setup()
    n = []
    ph.safety_pre = lambda spec: (n.append(1) or "SAFETY FAIL S2: last known battery 41.0 C >= 38.0 C") if len(n) < 2 else None
    j = submit(["--run", "/bin/x", "--thread", "TS"])
    out = run(s)
    case("sc_pre_s2_waits", result(tmp, j["id"]).get("result") == "PASS" and out.count("refused by safety-check (SAFETY FAIL S2") == 2
         and "RESIDENT CRASH" not in out, out[-600:])
    tmp, ph, s = setup()
    k2 = os.path.join(tmp, "k2")
    open(k2, "w").write("unreviewed kernel")
    ph.safety_pre = lambda spec: "SAFETY FAIL S9: patch 0399 [NEW PMU WRITE, UNREVIEWED]" if "unreviewed" in open(spec["kernel"]).read() else None
    a = submit(["--kernel", k2, "--run", "/bin/a", "--thread", "TA", "--prio", "1"])
    b = submit(["--run", "/bin/b", "--thread", "TB", "--profile", "gpu"])
    out = run(s)
    case("sc_pre_s9_fail_alone", result(tmp, a["id"]).get("result") == "FAIL" and "SAFETY FAIL S9" in result(tmp, a["id"]).get("why", "")
         and result(tmp, b["id"]).get("result") == "PASS" and "RESIDENT CRASH" not in out, (result(tmp, a["id"]), out[-600:]))
    tmp, ph, s = setup()
    ph.safety_live = (200, "SAFETY FAIL S1: battery temp 45.3 C >= 45.0 C")
    l1 = submit(["--run", "/bin/x LONG", "--thread", "TL", "--max", "1200"])
    l2 = submit(["--run", "/bin/y", "--thread", "TW", "--profile", "gpu"])
    out = run(s)
    r1 = result(tmp, l1["id"])
    case("sc_live_stop_cut_keeps_queue", r1.get("result") == "CUT" and "SAFETY FAIL S1: battery temp 45.3 C" in r1.get("why", "") and not result(tmp, l2["id"])
         and ph.restarted == 1 and "RESIDENT CRASH" not in out, (r1, out[-600:]))
    tmp, ph, s = setup()   # the monitor stops the boot BEFORE devd answers (+30 s < +60 s): no job ran, the queue is kept
    ph.safety_live = (30, "SAFETY FAIL S6: no USB log within 180 s of the fastboot boot")
    e1 = submit(["--run", "/bin/x", "--thread", "TE"])
    out = run(s)
    case("sc_boot_stop_keeps_queue", not result(tmp, e1["id"]) and ph.restarted == 1 and "RESIDENT PARK: SAFETY FAIL S6" in out and "NOTRUN" not in out, out[-500:])
    case("sc_fail_in", R.rt.safety_fail_in(os.path.join(tmp, "none")) is None)
