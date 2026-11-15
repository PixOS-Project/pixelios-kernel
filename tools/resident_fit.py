"""resident_fit: which jobs may share one boot, and what a refused or unbuildable boot does to its jobs (ALL-ON T-51, 2026-11-12).
Library of tools/resident_session_lib.py (split out for the 800-line limit); host-tested by tools/test_resident.py.

Rules (T-51: lane-t02 991d + e5ad merged to 832 B > 824 B, the build was refused, the session crashed and the BT lane's 2c3d ended NOTRUN;
job 991d's kernel without xnu 0320 failed build-xnu-image and took the BT job down too):
  1. A boot of more than one job runs only when its merged line is PROVEN to build: `xrun --print-bootargs` (the build's own flags) gives a
     line, prints no REFUSED:, the line fits line_limit, and the kernel carries mkpayload's boot-line tag. Else the first job boots alone and
     every other job is marked solo (its own boot, its own check) and waits.
  2. A boot refused on the host, or whose xrun dies in the host build before it touches the phone (mkadt / mkwlancred / build-xnu-image /
     overlay_std), ends ALONE: one job = FAIL with the why; several = all solo, each tries alone. The session goes on (no crash, no NOTRUN
     for anyone else)."""
import os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
import resident_core as rc  # noqa: E402

WAIVERS = {"XRUN_NO_RUNPLAN": "user 2026-11-08 no run plans", "PREFLIGHT_GATE_WAIVER": "user 2026-11-02 + 2026-11-08"}


def xrun_env():
    env = dict(os.environ)
    for k, v in WAIVERS.items():
        env.setdefault(k, v)
    return env


# xrun's die texts of its host half (tools/xrun mkadt .. build-xnu-image, all before plock_take): the phone was not touched
HOST_BUILD_RX = re.compile(r"RESULT: ERROR (?:build-xnu-image failed|mkadt failed|mkwlancred failed|mkwlancred printed no blob path|"
                           r"overlay_std prepare failed)[^\n]*")


def host_fail_in(path):
    """xrun's host-build die line in its output file PATH, '' when none (or no file)."""
    try:
        with open(path, errors="replace") as f:
            m = HOST_BUILD_RX.search(f.read())
    except OSError:   # lint-silent: expected no xrun.out yet: no host failure seen
        return ""
    return m.group(0)[:400] if m else ""


def print_bootline(xr, env):
    """(line or None, output) of `xrun --print-bootargs` for the command XR (host only: no build, lock or phone), run in the root of the tree
    whose tools/xrun XR[0] is (T-50: a submit checks with the running session's tree)."""
    try:
        r = subprocess.run(xr[:1] + ["--print-bootargs"] + xr[1:], cwd=os.path.dirname(os.path.dirname(os.path.abspath(xr[0]))), env=env, capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        out = "print-bootargs failed: %s" % e
    return rc.bootline_of(out), out


def kernel_tag(tools_dir=HERE):
    """The tag mkpayload.build needs in the kernel (b'SWEET_BOOT_LINE_LENGTH=2048\\0' with xnu 0320), read from TOOLS_DIR/mkpayload.py
    (its BOOT_LINE_LENGTH line; no import: mkpayload needs the tools package); None when unreadable."""
    try:
        with open(os.path.join(tools_dir, "mkpayload.py")) as f:
            m = re.search(r"^BOOT_LINE_LENGTH = (\d+)", f.read(), re.M)
    except OSError:   # lint-silent: expected a tree without mkpayload.py: None, the caller writes the GAP line
        return None
    return b"SWEET_BOOT_LINE_LENGTH=%d\0" % int(m.group(1)) if m else None


def kernel_tag_check(kernel, tag):
    """'' when KERNEL (a path) carries TAG; else why mkpayload would refuse its image (T-50: trunk kernels without xnu 0320). TAG None = None
    (not checked: the caller says so)."""
    if not tag:
        return None
    try:
        with open(kernel, "rb") as f:
            data = f.read()
    except OSError as e:
        return "kernel %s unreadable: %s" % (kernel, e)
    if tag not in data:
        return "kernel %s lacks %r (xnu patch 0320 missing): mkpayload refuses its image" % (os.path.basename(kernel), tag[:-1].decode())
    return ""


def guard_g6(job, event):
    """Submit guard G6: [(G6, why, do instead)] when the job's --kernel lacks mkpayload's boot-line tag, else []."""
    k = job["spec"].get("kernel")
    why = kernel_tag_check(k, kernel_tag()) if k and os.path.isfile(k) else ""
    if why is None:
        event("RESIDENT GAP: submit %s: mkpayload boot-line tag unreadable: G6 kernel not checked for xnu 0320" % job["id"])
    elif why:
        return [("G6", why, "build the kernel with xnu patch 0320 (trunk has it once 0320 lands; until then add it to the build stack)")]
    return []


def bootline_verdict(xr, env, event):
    """(why refused or '', line or None) of the boot XR: xrun's own REFUSED: line, line_limit, the kernel tag. EVENT gets the GAP lines."""
    line, out = print_bootline(xr, env)
    refused = [ln for ln in out.splitlines() if ln.startswith("REFUSED:")]
    if refused:   # xrun / build-xnu-image's own refusal is the authority (T-35)
        return refused[0][len("REFUSED: "):][:300], line
    why = rc.mif_line_check(xr, line) if line is not None else ""
    if why:
        return why, line
    k = xr[xr.index("--kernel") + 1] if "--kernel" in xr else None
    kw = kernel_tag_check(k, kernel_tag()) if k else None
    if kw is None:
        event("RESIDENT GAP: boot-line tag of mkpayload unreadable (or no --kernel): kernel not checked for xnu 0320")
    return kw or "", line


def mark_solo(sess, jids, why, persist):
    """Each job of JIDS gets its own boot (and its own check) from now on; PERSIST(job) writes its job.json."""
    for jid in jids:
        j = sess.pending.get(jid)
        if j is None:
            continue
        j["solo"] = why[:200]
        persist(j)


def split_batch(sess, spec, batch, why, persist, event):
    """Rule 1: the merged line of BATCH is not proven: (spec, [seed]) of the first job alone; the others are solo and wait."""
    mark_solo(sess, batch[1:], why, persist)
    for jid in batch[1:]:
        event("RESIDENT BATCH: %s waits for its own boot (merged boot line not proven to build: %s)" % (jid, why[:200]))
    nspec, nb, _ = rc.plan_boot([sess.pending[batch[0]]], sess.ph.now(), sess.cfg, sess.std_kernel, sess.std_sha)
    return nspec, nb


def ends_alone(sess, label, batch, why, persist, event):
    """Rule 2: boot LABEL did not build (refused on the host, or xrun died in its host build): one job = FAIL, several = all solo. The
    session goes on; the phone was not booted by this boot."""
    live = [jid for jid in batch if jid in sess.pending]
    event("RESIDENT BUILD FAIL: boot %s (jobs %s): %s; the phone was not booted, the session goes on" % (label, live, why[:300]))
    if len(live) == 1:
        sess.finish(sess.pending[live[0]], "FAIL", "its boot %s did not build: %s" % (label, why[:300]), None)
    else:
        mark_solo(sess, live, "boot %s of %d jobs did not build" % (label, len(live)), persist)
        for jid in live:
            event("RESIDENT BATCH: %s waits for its own boot (boot %s of %d jobs did not build)" % (jid, label, len(live)))
    sess.boot_batch = set()
    return True


def xrun_has_build_only(xrun):
    """True when that tree's tools/xrun knows XRUN_BUILD_ONLY (else G7 must not run it: an old xrun would go on to take the phone)."""
    try:
        with open(xrun) as f:
            return "XRUN_BUILD_ONLY" in f.read()
    except OSError:   # lint-silent: expected an unreadable/missing xrun: no build-only, the caller writes the GAP line
        return False


def prebuild(xr, env, outpath, timeout=1800):
    """Submit guard G7 (user 2026-11-12: 'build and check every job's image at SUBMIT time'): run the boot's own xrun command XR with
    XRUN_BUILD_ONLY=1 (mkadt + build-xnu-image + mkpayload + the boot-line checks, stopped before the phone lock). '' = the image built;
    else the refusal (xrun's REFUSED:/RESULT: ERROR/mkpayload line). The whole output goes to OUTPATH."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(xr[0])))
    try:
        r = subprocess.run(xr, cwd=root, env=dict(env, XRUN_BUILD_ONLY="1"), capture_output=True, text=True, timeout=timeout)
        out, rc_ = r.stdout + r.stderr, r.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        out, rc_ = "prebuild failed: %s" % e, -1
    with open(outpath, "w") as f:
        f.write(out)
    if rc_ == 0 and "RESULT: PASS build-only" in out:
        return ""
    m = re.search(r"(logs/runs/[^/\s]+)/build\.out", out)
    found = []   # the build's own reason: mkpayload / REFUSED lines of build.out and xrun.log (mkpayload's exit text lands in xrun.log)
    for n in ("build.out", "xrun.log") if m else ():
        f = os.path.join(root, m.group(1), n)
        if os.path.exists(f):
            with open(f, errors="replace") as fh:
                found += [ln.strip() for ln in fh if ln.startswith(("mkpayload:", "REFUSED:"))]
    why = found or [ln for ln in out.splitlines() if ln.startswith(("REFUSED:", "mkpayload:", "RESULT: ERROR", "RESULT: FAIL"))]
    return "%s (xrun rc %d; output %s)" % ((why[0] if found else (why[-1] if why else "no RESULT line"))[:400], rc_, outpath)
