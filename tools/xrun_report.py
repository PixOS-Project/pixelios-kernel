#!/usr/bin/env python3
"""Report builder + small helpers for tools/xrun (see docs/AUTOMATION.md).

  xrun_report.py mark    DIR key=value...   record run facts in DIR/run.json (value @now = current time; expect/expect_absent append)
  xrun_report.py waitusb DIR                wait for the phone to leave and re-enter USB (vendor 18d1) = the self-reset worked
  xrun_report.py error   DIR MESSAGE        write an ERROR report (exit 2)
  xrun_report.py report  DIR                merge snapshots, build report.json/report.txt, print report.txt (exit 0 PASS, 1 FAIL/PARTIAL, 2 ERROR)
  xrun_report.py prune   DIR                keep every 4th camera frame and the last 20

Run dir layout: run.json, snaps/qr-<UTC>[-sid].txt (tools/qr-log.py --keep-going), frames/*.jpg (mtime = capture time),
merged.log, luma.json (frame brightness cache), report.json, report.txt.
"""
import calendar, glob, json, os, pathlib, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrmerge  # noqa: E402

LISTKEYS = ("expect", "expect_absent", "stop_on", "fail_on")
BLACK = 25          # mean luma (0-255) under which a camera frame counts as a black screen
EXIT = {"PASS": 0, "FAIL": 1, "PARTIAL": 1, "ERROR": 2}
# label -> regex. Counted over the merged log; the first matching line is shown. (touch/keys/cpu lines are listed separately.)
MARKERS = [
    ("restart armed", r"SWEET RESTART: armed"),
    ("restart resetting", r"SWEET RESTART: resetting now"),
    ("QR takeover", r"SWEET QR: taking over"),
    ("UFS errors", r"SWEET UFS:.*(fail|error|timeout|ocs 0x[1-9a-f])"),
    ("USB running", r"SWEET USB: running"),
    ("USB configured (host enumerated)", r"SWEET USB: configured"),
    ("USB ue0 up", r"SWEET USB: ue0 .* up"),
    ("USB errors", r"SWEET USB:.*(fail|timeout|STUCK|not touching|no DMA|no usable|unexpected|not in device|not inside|NOT configured)"),
    ("display on", r"SWEET FB: display on"),
    ("panic/hang/watchdog", r"(?<!g)(?<!Dump)[Pp]anic|\bhung\b|\bhang\b|watchdog.*(timeout|expired|bite|bark)|Kernel trap|Assertion failed"),
    ("lockscreen (SBCoverSheet)", r"SBCoverSheet|SBLockScreen|[Ll]ock ?[Ss]creen"),
    ("SpringBoard lines", r"^\|SpringBoard\||^IOK SpringBoard:"),
    ("backboardd lines", r"^\|backboardd\||^IOK backboardd:"),
    ("extension check-in timeout", r"extension check-in timeout"),
]
LISTED = [("TOUCH/KEYS", r"^SWEET (TOUCH|KEYS):", 12), ("CPU", r"^SWEET CPU:", 4)]


def cut(s, n=140):
    return s if len(s) <= n else s[:n - 3] + "..."


def now():
    return time.time()


def load(p, default=None):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except (OSError, ValueError):
        return default


def save(p, obj):
    with open(p + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(p + ".tmp", p)


def at(t):
    return "+%ds" % round(t) if t is not None else "?"


# ---- helpers used by the shell -------------------------------------------------------------------------------
def mark(d, kvs):
    run = load(os.path.join(d, "run.json"), {})
    for kv in kvs:
        k, _, v = kv.partition("=")
        v = str(now()) if v == "@now" else v
        if k in LISTKEYS:
            run.setdefault(k, []).append(v)
        else:
            run[k] = v
    save(os.path.join(d, "run.json"), run)


def usb_present(vid="18d1"):
    for p in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if pathlib.Path(p).read_text().strip() == vid:
                return True
        except OSError:
            pass
    return False


def usb_ids(vid="18d1"):
    """vid:pid of the first Google USB device on the bus (18d1:4ee0 = fastboot, 18d1:d001 = recovery), or ''"""
    for p in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if pathlib.Path(p).read_text().strip() == vid:
                return "%s:%s" % (vid, pathlib.Path(p).with_name("idProduct").read_text().strip())
        except OSError:
            pass
    return ""


def stop_seen(d, pats):
    """True when every regex in pats occurs in the merged QR snapshots + usbnet log of run dir d (live early-stop check)."""
    snaps = sorted(glob.glob(os.path.join(d, "snaps", "qr-*.txt")))
    qr2 = sorted(glob.glob(os.path.join(d, "snaps", "qr2-*.txt")))
    lines = qrmerge.load_qr2(qr2)[0] if qr2 else qrmerge.merge([pathlib.Path(x).read_text(encoding="latin-1") for x in snaps])[0] if snaps else []
    lines = lines + read_usbnet(os.path.join(d, "usbnet.log"))[0]
    return all(any(re.search(p, ln) for ln in lines) for p in pats)


def waitusb(d):
    """Wait for the self-reset (usb leaves and re-enters), the duration, the hard timeout, or every stop_on pattern. Prints EARLY / TIMEOUT / DONE."""
    run = load(os.path.join(d, "run.json"), {})
    t0 = float(run.get("t_boot", now()))
    end = t0 + float(run.get("duration", 180))
    hard = float(run["t_start"]) + float(run["hard_timeout"]) if run.get("hard_timeout") and run.get("t_start") else None
    stop = run.get("stop_on", [])
    fails = run.get("fail_on", [])   # any one of these ends the run at once (a failing run must not wait out the restart window)
    expect_reset = bool(run.get("restart"))
    gone = None
    nxt = 0
    while now() < min(end, hard or end):
        if usb_present():
            if gone is not None:
                mark(d, ["usb_back=%f" % now(), "usb_back_id=%s" % usb_ids()])
                print("DONE")
                return
        elif gone is None:
            gone = now()
            mark(d, ["usb_gone=%f" % gone])
        if (stop or fails) and now() >= nxt:
            nxt = now() + 10
            hit = next((p for p in fails if stop_seen(d, [p])), None)
            if hit:
                mark(d, ["early=%f" % now(), "t_end=%f" % now(), "early_fail=%s" % hit])
                print("EARLY")
                return
            if stop and stop_seen(d, stop):
                mark(d, ["early=%f" % now(), "t_end=%f" % now()])
                print("EARLY")
                return
        time.sleep(1 if expect_reset else 5)
    timed_out = bool(hard and now() >= hard)
    m = re.match(r"\d+", str(run.get("restart") or ""))
    reset_due = bool(m) and now() - t0 >= float(m.group(0))
    if timed_out and gone is not None and reset_due:
        # the reset was due but the phone is not back yet when the hard timeout cuts the wait (it returns in recovery after ~30 s or more): short grace window.
        # usb_gone is when the phone left fastboot for XNU (+2 s), not proof that the reset fired.
        try:
            grace = float(os.environ.get("XRUN_GRACE") or 90)
        except ValueError:
            grace = 90.0
        grace_end = now() + grace
        while now() < grace_end:
            if usb_present():
                mark(d, ["usb_back=%f" % now(), "late_back=1", "usb_back_id=%s" % usb_ids()])
                break
            time.sleep(1)
    mark(d, ["t_end=%f" % now()] + (["timeout=1"] if timed_out else []))
    print("TIMEOUT" if timed_out else "DONE")


def prune(d):
    fr = sorted(glob.glob(os.path.join(d, "frames", "*.jpg")), key=os.path.getmtime)
    keep = set(fr[::4]) | set(fr[-20:])
    for f in fr:
        if f not in keep:
            os.remove(f)


def read_usbnet(path):
    """tools/usbnet-log.py file ("<epoch>\\t<line>") -> ([lines], [epoch]); [] when absent."""
    try:
        raw = pathlib.Path(path).read_text(encoding="latin-1").split("\n")
    except OSError:
        return [], []
    ls, ts = [], []
    for r in raw:
        t, tab, ln = r.partition("\t")
        try:
            ts.append(float(t))
        except ValueError:
            continue
        ls.append(ln)
    return ls, ts


def add_usbnet(lines, sidx, times, ulines, utimes):
    """Merge the USB console stream (complete from link-up) with the QR lines (tail snapshots): the QR lines past the end of the
    stream are appended (qrmerge alignment, so a log that outlives the link is still completed by QR).
    -> (lines, ltime) with ltime[i] = absolute epoch of line i (USB: datagram time, QR: snapshot time) or None."""
    qt = [times[s] if s is not None and times else None for s in sidx]
    if not ulines:
        return lines, qt
    # TAIL is read at call time: the stream can be much longer than one QR snapshot
    old, qrmerge.TAIL = qrmerge.TAIL, max(qrmerge.TAIL, len(ulines))
    try:
        merged, src, _ = qrmerge.merge(["\n".join(ulines) + "\n", "\n".join(lines) + "\n"] if lines else ["\n".join(ulines) + "\n"])
    finally:
        qrmerge.TAIL = old
    ltime, ui, qi = [], 0, len(lines) - sum(1 for x in src if x == 1)
    for sidx_ in src:
        if sidx_ == 0:
            ltime.append(utimes[ui]); ui += 1
        elif sidx_ == 1:
            ltime.append(qt[qi]); qi += 1
        else:
            ltime.append(None)
    return merged, ltime


# ---- report ---------------------------------------------------------------------------------------------------
def snap_time(path):
    m = re.search(r"qr-(\d{8}T\d{6}Z)", os.path.basename(path))
    return calendar.timegm(time.strptime(m.group(1), "%Y%m%dT%H%M%SZ")) if m else os.path.getmtime(path)


def luma_series(d, t0):
    """[(t_rel, mean luma)] of the camera frames, cached in luma.json so pruned frames keep their brightness."""
    cache_p = os.path.join(d, "luma.json")
    cache = load(cache_p, {})
    names = sorted(glob.glob(os.path.join(d, "frames", "*.jpg")), key=os.path.getmtime)
    try:
        from PIL import Image, ImageStat
    except ImportError:
        Image = None
    for f in names:
        b = os.path.basename(f)
        if b in cache or Image is None:
            continue
        try:
            with Image.open(f) as im:
                im = im.convert("L")
                im.thumbnail((64, 64))
                cache[b] = [os.path.getmtime(f), ImageStat.Stat(im).mean[0]]
        except OSError:
            pass
    if cache:
        save(cache_p, cache)
    return sorted((t - t0, m) for t, m in cache.values() if t - t0 >= 0)


def screen_summary(series):
    """-> (text, black_at). Segments of consecutive bright/black frames; black_at = first black frame after a bright one."""
    if not series:
        return "no camera frames", None
    segs = []       # [state, first_t, last_t]
    for t, m in series:
        st = "black" if m < BLACK else "bright"
        if segs and segs[-1][0] == st:
            segs[-1][2] = t
        else:
            segs.append([st, t, t])
    black_at = next((s[1] for i, s in enumerate(segs) if s[0] == "black" and i > 0), None)
    parts = ["%s %d-%d s" % (s[0], s[1], s[2]) if i < len(segs) - 1 else "%s from %d s" % (s[0], s[1])
             for i, s in enumerate(segs)]
    if len(parts) > 8:
        parts = parts[:3] + ["... %d changes" % (len(segs) - 6)] + parts[-3:]
    txt = ", ".join(parts)
    if len(segs) == 1:
        txt = "%s throughout (%d-%d s)" % (segs[0][0], segs[0][1], segs[0][2])
    return "%s | %d frames, mean luma %d..%d" % (txt, len(series), min(m for _, m in series), max(m for _, m in series)), black_at


def parse_crashes(lines):
    crashes = {}
    for ln in lines:
        m = re.match(r"CRASH (\S+?)\[(\d+)\] sig (\d+)", ln)
        if m:
            c = crashes.setdefault(m.group(1), {"count": 0, "sig": m.group(3), "pc": None, "reason": None})
            c["count"] += 1
            continue
        m = re.match(r"CRASH (\S+) pc (0x[0-9a-f]+)", ln)
        if m and m.group(1) in crashes and not crashes[m.group(1)]["pc"]:
            crashes[m.group(1)]["pc"] = m.group(2)
            continue
        m = re.match(r"CRASH (\S+) reason (.*)", ln)
        if m and m.group(1) in crashes and not crashes[m.group(1)]["reason"]:
            crashes[m.group(1)]["reason"] = m.group(2)
    return crashes


def previous_report(d, label):
    here = os.path.basename(os.path.normpath(d))
    sib = sorted(p for p in glob.glob(os.path.join(os.path.dirname(os.path.abspath(d)), "*-" + label))
                 if os.path.basename(p) < here and os.path.exists(os.path.join(p, "report.json")))
    return load(os.path.join(sib[-1], "report.json")) if sib else None


def build(d, stop_on=None):
    run = load(os.path.join(d, "run.json"), {})
    if stop_on:      # dry-run replay: pretend the run was stopped at the first moment every pattern was seen
        run["stop_on"], run["replay_stop"] = stop_on, True
    t0 = float(run["t_boot"]) if run.get("t_boot") else None
    label = run.get("label", "run")
    f = lambda k: float(run[k]) if run.get(k) else None
    expects = [(r, False) for r in run.get("expect", [])] + [(r, True) for r in run.get("expect_absent", [])]

    # merged log + per-line snapshot time
    snaps = sorted(glob.glob(os.path.join(d, "snaps", "qr-*.txt")))
    texts = [pathlib.Path(s).read_text(encoding="latin-1") for s in snaps]
    qr2 = sorted(glob.glob(os.path.join(d, "snaps", "qr2-*.txt")))     # QL2: the whole log recovered so far, one file per phone boot
    reread = not texts and not qr2 and os.path.exists(os.path.join(d, "merged.log"))
    qr2cov = None
    if qr2:
        lines, sidx, times, gaps, qr2_from_boot, qr2cov = qrmerge.load_qr2(qr2)
        snaps, texts = qr2, ["x"]
        pathlib.Path(d, "merged.log").write_text("\n".join(lines) + "\n", encoding="latin-1")
    elif texts:
        lines, sidx, gaps = qrmerge.merge(texts)
        times = [snap_time(s) for s in snaps]
        pathlib.Path(d, "merged.log").write_text("\n".join(lines) + "\n", encoding="latin-1")
    elif reread:      # re-read of a run whose snapshots were removed
        lines = pathlib.Path(d, "merged.log").read_text(encoding="latin-1").split("\n")[:-1]
        sidx, times, gaps = [None] * len(lines), [], sum(1 for x in lines if x == qrmerge.GAP)
    else:
        lines, sidx, times, gaps = [], [], [], 0
    first_snap_t = times[0] if times else None
    from_boot = qr2_from_boot if qr2 else bool(texts) and len(texts[0]) < 11800      # QL1: a full 12 kB tail means older lines were cut off
    ulines, utimes = read_usbnet(os.path.join(d, "usbnet.log"))
    use_usb = bool(ulines) and (bool(texts) or not reread)      # a re-read merged.log already holds the USB lines
    if use_usb:
        lines, ltime = add_usbnet(lines, sidx, times, ulines, utimes)
        pathlib.Path(d, "merged.log").write_text("\n".join(lines) + "\n", encoding="latin-1")
        gaps = sum(1 for x in lines if x == qrmerge.GAP)
        from_boot = True        # guess: the live stream replays the last 64 KiB at link-up and then every line; UDP may drop datagrams
    else:
        ltime = [times[x] if x is not None and times else None for x in sidx]
    tl = lambda i: ltime[i] - t0 if ltime[i] is not None and t0 else None     # seconds after the boot command

    def find(rx):
        r = re.compile(rx)
        hits = [i for i, ln in enumerate(lines) if r.search(ln)]
        return hits

    # EARLY STOP: live runs mark early=<t> (waitusb); a replay computes the moment the last stop pattern first appeared
    early_txt = None
    stop = run.get("stop_on", [])
    if stop and (run.get("early") or run.get("replay_stop")):
        firsts = [find(p) for p in stop]
        if all(firsts):
            last = max(h[0] for h in firsts)
            st = tl(last) if run.get("replay_stop") else (float(run["early"]) - t0 if t0 else None)
            if run.get("replay_stop"):
                lines = lines[:last + 1]
            full = float(run.get("duration") or 0)
            early_txt = "stopped at %s (full run would have waited %ds)" % (at(st), full)
    if stop and run.get("replay_stop") and not early_txt:
        early_txt = "NOT reached: stop pattern(s) never all seen, a live run would have waited the full %ds" % float(run.get("duration") or 0)
    # EXPECT
    exp = []
    for rx, absent in expects:
        hits = find(rx)
        e = {"regex": rx, "absent": absent, "found": bool(hits)}
        if hits:
            e["line"], e["t"] = cut(lines[hits[0]]), tl(hits[0])
        exp.append(e)

    # MARKERS
    markers = {}
    for lab, rx in MARKERS:
        h = find(rx)
        markers[lab] = {"count": len(h), "first": cut(lines[h[0]]) if h else None, "t": tl(h[0]) if h else None}
    swaps = [int(m.group(1)) for ln in lines for m in [re.match(r"SWEET FB: swap (\d+)", ln)] if m]
    markers["FB swaps (max n)"] = {"count": max(swaps) if swaps else 0, "first": None, "t": None}
    listed = {lab: [cut(lines[i]) for i in find(rx)[:cap]] for lab, rx, cap in LISTED}
    crashes = parse_crashes(lines)

    # SCREEN
    series = luma_series(d, t0) if t0 else []
    screen_txt, black_at = screen_summary(series)

    # RUN facts
    waited = (f("t_end") or now()) - t0 if t0 else None
    back = f("usb_back")
    reset_at = back - t0 if back and t0 else None
    restart = run.get("restart") or ""
    gone = f("usb_gone")
    restart_s = float(re.match(r"\d+", restart).group(0)) if re.match(r"\d+", restart) else 0.0
    # inferred, not observed: the hard timeout ended the wait after the reset was due and the bluejay itself (adb recovery / fastboot product bluejay) is on the bus
    phone = str(run.get("phone", ""))
    if (restart and reset_at is None and run.get("timeout") and gone and t0 and (f("t_end") or 0) - t0 >= restart_s
            and ("adb [recovery]" in phone or "fastboot product [bluejay]" in phone)):
        reset_at = (f("t_end") or now()) - t0
        run["late_back"] = "1"
    reset_txt = (("reset inferred at ~+%ds (the phone is in recovery/fastboot; its re-enumeration was not seen)" % reset_at if run.get("late_back") else "reset observed at +%ds%s" % (reset_at, " (back as %s)" % {"18d1:4ee0": "fastboot"}.get(run.get("usb_back_id"), run.get("usb_back_id")) if run.get("usb_back_id") else "")) if reset_at is not None else
                 ("reset NOT observed (waited %ds; the sweetrestart did not fire or the phone hung)" % (waited or 0)) if restart
                 else "no --restart (no reset expected)")
    qr_t = first_snap_t - t0 if first_snap_t and t0 else None
    cov = "%s, %s; first line: %s | last line: %s" % (
        qr2cov or "%d snapshot(s), %d gap(s)" % (len(snaps), gaps),
        "starts at boot" if from_boot else "TAIL ONLY (older lines cut off: set --qr earlier)" if not qr2 else "does NOT start at the first logged byte (ring overwritten or early units not received yet)",
        cut(lines[0], 50) if lines else "-", cut(lines[-1], 50) if lines else "-")
    if run.get("usbnet"):
        cov += " | usbnet: %s" % ("%d line(s), first at +%ds" % (len(ulines), utimes[0] - t0) if ulines and t0 else
                                 "NO DATAGRAM received (no enumeration, no ue0/ARP, or 192.168.7.1 not configured)")

    # RESULT
    bad = [e for e in exp if (e["found"] == e["absent"])]
    incomplete = not lines or gaps > 0 or not from_boot
    if run.get("error"):
        result, reason = "ERROR", run["error"]
    elif markers["panic/hang/watchdog"]["count"]:
        result, reason = "FAIL", "panic/hang/watchdog line in the log: %s" % cut(markers["panic/hang/watchdog"]["first"], 80)
    elif run.get("early_fail"):
        result, reason = "FAIL", "stopped early on a failure pattern (%s) at +%ds; the full run would have waited %ss" % (cut(str(run["early_fail"]), 60), waited or 0, run.get("duration"))
    elif restart and reset_at is None and run.get("timeout") and waited is not None and waited < restart_s and not (early_txt and not early_txt.startswith("NOT")):
        result, reason = "PARTIAL", "hard timeout after %ds, before the reset was due (restart %ds): the reset was not awaited%s" % (waited, restart_s, "; %d expectation(s) missing" % len(bad) if bad else "")
    elif restart and reset_at is None and not (early_txt and not early_txt.startswith("NOT")):
        result, reason = "FAIL", "%ssweetrestart did not fire or the phone hung: it never re-appeared on USB within %ds (check tools/phone-state before asking for a button hold)" % (
            "HARD TIMEOUT: " if run.get("timeout") else "", waited or 0)
    elif bad:
        what = "%d of %d expectations failed (first: %s%s)" % (len(bad), len(exp), "absent-violated " if bad[0]["absent"] else "missing ", cut(bad[0]["regex"], 50))
        if incomplete and not all(b["absent"] for b in bad):
            result, reason = "PARTIAL", what + "; log coverage is incomplete so the line may be outside the snapshots"
        else:
            result, reason = "FAIL", what
    elif not lines and exp:
        result, reason = "PARTIAL", "no QR log captured (qr off or camera saw nothing): expectations could not be evaluated"
    else:
        result = "PASS"
        reason = "%d expectation(s) met, no panic, %s" % (len(exp), "stopped early, reset not awaited" if early_txt and not early_txt.startswith("NOT") else reset_txt)

    # DIFF
    diff = {"previous": None}
    prev = previous_report(d, label)
    if prev:
        pm = {k for k, v in prev.get("markers", {}).items() if v["count"]}
        nm = {k for k, v in markers.items() if v["count"]}
        pc, nc = set(prev.get("crashes", {})), set(crashes)
        diff = {"previous": prev.get("result"), "markers_appeared": sorted(nm - pm), "markers_disappeared": sorted(pm - nm),
                "crashes_appeared": sorted(nc - pc), "crashes_disappeared": sorted(pc - nc)}

    return {
        "result": result, "reason": reason, "exit": EXIT[result],
        "run": {"label": label, "kernel": run.get("kernel"), "kernel_sha256": run.get("kernel_sha256"), "git_head": run.get("git_head"),
                "boot_args": run.get("boot_args"), "cpus": run.get("cpus"), "qr": run.get("qr"), "restart": restart,
                "reset_at": reset_at, "early": early_txt, "phone": run.get("phone"), "qr_first_snapshot_at": qr_t, "waited": waited, "reset_text": reset_txt, "dir": d},
        "coverage": {"snapshots": len(snaps), "usbnet_lines": len(ulines), "gaps": gaps, "from_boot": from_boot, "text": cov, "lines": len(lines)},
        "expect": exp, "markers": markers, "listed": listed, "crashes": crashes,
        "screen": {"text": screen_txt, "black_at": black_at, "frames": len(series)}, "diff": diff,
    }


def render(r):
    run, out = r["run"], []
    out.append("RESULT: %s %s" % (r["result"], r["reason"]))
    out.append("RUN: label %s | kernel sha256 %s | git %s | cpus %s qr %s restart %s" % (
        run["label"], (run["kernel_sha256"] or "?")[:12], run["git_head"] or "?", run["cpus"], run["qr"], run["restart"] or "-"))
    out.append("  boot-args: %s" % cut(run["boot_args"] or "?", 200))
    out.append("  boot->first QR snapshot %s | %s | waited %ss" % (
        ("+%ds" % run["qr_first_snapshot_at"]) if run["qr_first_snapshot_at"] is not None else "n/a", run["reset_text"],
        round(run["waited"]) if run["waited"] is not None else "?"))
    out.append("  log coverage: %s" % r["coverage"]["text"])
    if run.get("early"):
        out.append("EARLY: " + run["early"])
    if run.get("phone"):
        out.append("PHONE: " + run["phone"])
    out.append("  dir: %s (xrun.log = progress, merged.log, report.json)" % run["dir"])
    out.append("EXPECT:" + ("" if r["expect"] else " (none requested)"))
    for e in r["expect"]:
        if e["absent"]:
            out.append("  %s /%s/%s" % ("PRESENT(bad)" if e["found"] else "ABSENT ok", e["regex"], (" at %s: %s" % (at(e["t"]), e["line"])) if e["found"] else ""))
        elif e["found"]:
            out.append("  FOUND   /%s/ at %s: %s" % (e["regex"], at(e["t"]), e["line"]))
        else:
            out.append("  MISSING /%s/" % e["regex"])
    out.append("MARKERS:")
    seen = [(k, v) for k, v in r["markers"].items() if v["count"]]
    for k, v in seen:
        out.append("  %s: %d%s%s" % (k, v["count"], (" first " + at(v["t"]) if v["t"] is not None else ""), (": " + v["first"]) if v["first"] and k not in ("SpringBoard lines", "backboardd lines") else ""))
    out.append("  not seen: " + (", ".join(k for k, v in r["markers"].items() if not v["count"]) or "-"))
    for lab, ls in r["listed"].items():
        if ls:
            out.append("  SWEET %s lines (%d shown):" % (lab, len(ls)))
            out += ["    " + x for x in ls]
    out.append("CRASHES:" + ("" if r["crashes"] else " none in the log (counts are lower bounds: the phone's sanitiser drops digit-only-different repeats)"))
    top = sorted(r["crashes"].items(), key=lambda kv: -kv[1]["count"])
    for p, c in top[:8]:
        out.append("  %s: %dx sig %s pc %s%s" % (p, c["count"], c["sig"], c["pc"] or "?", (" | " + cut(c["reason"], 60)) if c["reason"] else ""))
    if len(top) > 8:
        out.append("  +%d more process(es)" % (len(top) - 8))
    s = r["screen"]
    out.append("SCREEN: %s%s" % (s["text"], ("; black-at +%ds" % round(s["black_at"])) if s["black_at"] is not None else ""))
    df = r["diff"]
    if df["previous"] is None:
        out.append("DIFF: no earlier run with label %s" % r["run"]["label"])
    else:
        ch = ["+" + x for x in df["markers_appeared"]] + ["-" + x for x in df["markers_disappeared"]] + \
             ["+crash " + x for x in df["crashes_appeared"]] + ["-crash " + x for x in df["crashes_disappeared"]]
        out.append("DIFF vs previous (%s): %s" % (df["previous"], cut(", ".join(ch), 300) if ch else "no change in markers/crashes"))
    return "\n".join(out) + "\n"


def report(d, stop_on=None):
    r = build(d, stop_on)
    txt = render(r)
    save(os.path.join(d, "report.json"), r)
    pathlib.Path(d, "report.txt").write_text(txt)
    sys.stdout.write(txt)
    return r["exit"]


def main(a):
    if len(a) < 2:
        sys.exit(__doc__)
    cmd, d = a[0], a[1]
    os.makedirs(d, exist_ok=True)
    if cmd == "mark":
        mark(d, a[2:])
    elif cmd == "waitusb":
        waitusb(d)
    elif cmd == "error":
        mark(d, ["error=" + " ".join(a[2:])])
        return report(d)
    elif cmd == "report":
        rest = a[2:]
        return report(d, [rest[i + 1] for i in range(len(rest) - 1) if rest[i] == "--stop-on"])
    elif cmd == "prune":
        prune(d)
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
