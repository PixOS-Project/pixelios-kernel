"""safety_lib: the rules of docs/SAFETY-CHECKLIST.md (super repo, RULES H9; user 2026-11-12 "make a check list that every run must check for
safety and comply with it, then enforce that"). Library of tools/safety-check (CLI), tools/xrun_report.py (waitusb stop + report) and the host
tests tools/test_safety_check.py / tools/test_safety_mutants.py. stdlib only.

Thresholds are the checklist's; there is no override flag or environment value for S1-S6 (raise a limit only with the user).
State (shared by every caller, default $ARTIFACTS/safety = ~/pixelios-artifacts/safety): state.json {temp_c, temp_at, soc, vcell, batt_at,
hot, hot_why, hot_at, low, low_why, boot_at, end_at, end_why, defer_s2, defer_s4}; human-needed.log (one line per HUMAN-NEEDED stop).
"""
import json, os, re, statistics, subprocess, time

HOT_C = 45.0          # S1: battery temp at or above = stop + park
COOL_C = 38.0         # S2: next boot only below this
COOL_PARK_S = 900     # S2: >= 15 min parked when there is no reading
TEMP_GAP_S = 60       # S3: no temp= row for longer than this while booted = hot
TEMP_FIRST_S = 480    # S3 GAP: patch 0307 prints temp= only in the MON rows, ~330 s after the charger start (first row ~390-420 s after the
#                       fastboot boot [obs resident-054539-b1, resident-204614-b1]); before the first row the limit is this, counted from the boot.
#                       A kernel with patch 0334 (temp= in the P3 rows, P3_TEMP_FMT) has no such window: TEMP_GAP_S from the first USB log line.
SOC_FLOOR = 15.0      # S4: no boot below
SOC_CUT = 10.0        # S4: a running boot is cut below
VCELL_MIN = 3500      # S4: raw cell voltage floor (median of the last 3 rows, as tools/resident_core vcell_hard_mv)
ALIVE_FIRST_S = 180   # S6: first USB log line within this of the fastboot boot
ALIVE_GAP_S = 60      # S6: then a line at least this often
RESET_BACK_S = 180    # S6: the iOS gadget left the bus: the phone must re-appear as 18d1 (fastboot/recovery) within this

TEMP_FMT = b"temp=%s%d.%d C"                                    # xnu patch 0307 SWEET CHG MON row (bsd/dev/sweet_chg_i2c.c)
P3_TEMP_FMT = b"SWEET CHG P3: t=%us current=%d uA vcell=%u mV soc=%u.%02u %% temp="   # xnu patch 0334: the P3 rows (first 300 s) carry temp too
WDT_EARLY = "arm/pet source = timer (no USB needed)"           # xnu patch 0333 (ALL-ON T-53): the watchdog arms without USB
BOOTLINE_TAG = re.compile(rb"SWEET_BOOT_LINE_LENGTH=(\d+)\0")   # xnu patch 0176 / 0320 tag; tools/mkpayload.py KERNEL_TAG
MARK_RX = re.compile(r"\[NEW [^\]]*, UNREVIEWED\]")             # AGENTS rule 8 marker in a commit subject or a patch header
VERDICT_RX = re.compile(r"^\W*verdict\W*:\s*\**\s*(.+)$", re.I | re.M)

ROW_RX = re.compile(r"SWEET CHG (?:P3|MON): t=-?\d+s current=-?\d+ uA vcell=(\d+) mV(?: soc=(\d+\.\d+) %)?(?:[^\n]*? temp=(-?\d+(?:\.\d+)?) C)?")
P2_RX = re.compile(r"SWEET CHG P2: fg\[0x36\][^\n]*?VCell\[0x09\]=0x[0-9a-fA-F]+ \((\d+) mV\)[^\n]*?RepSOC\[0x06\]=0x[0-9a-fA-F]+ \((\d+\.\d+) %\)")


def artifacts():
    return os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts")


def default_state_dir():
    return os.path.join(artifacts(), "safety")


# ---- state ----------------------------------------------------------------------------------------------------
def load_state(d):
    try:
        with open(os.path.join(d, "state.json")) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except FileNotFoundError:   # lint-silent: expected first run, no reading known yet (every rule below treats "no reading" as not cool)
        return {}
    except ValueError as e:     # a torn/garbled state is no reading: loud, never "cool"
        print("SAFETY GAP: state %s/state.json unreadable (%s): treated as no reading" % (d, e))
        return {}


def save_state(d, st):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "state.json")
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def update_state(d, **kv):
    st = load_state(d)
    st.update(kv)
    save_state(d, st)
    return st


def last_park(st, d=None):
    """When the phone was last parked (end of the last monitored boot, else the newest phone-lock run end), or None."""
    cands = [st.get("end_at")]
    runs = os.path.join(artifacts(), "phone.lock.runs")
    try:
        ends = [os.path.join(runs, x, "end.json") for x in os.listdir(runs)]
        cands += [os.path.getmtime(p) for p in ends if os.path.exists(p)]
    except OSError:   # lint-silent: expected no phone-lock history on this host; the monitor's end_at is the only park time then
        pass
    cands = [float(c) for c in cands if c]
    return max(cands) if cands else None


# ---- S2 rule (the ONE copy: tools/safety-check pre and tools/resident_thermal.pre_boot both call it) ----------------
def s2_state(st, now):
    """(ok, text, defer): the last known temperature, else the park time."""
    park = last_park(st)
    parked = None if park is None else now - park
    pk = "parked %s" % ("%d s" % parked if parked is not None else "for an unknown time (no park recorded)")
    t = st.get("temp_c")
    if st.get("hot") or (t is not None and t >= COOL_C):
        # hot_why names the S1 cut only while hot is set; a stale one (hot cleared since) hid the real reason (tracker L-21)
        why = (st.get("hot") and st.get("hot_why")) or "last battery temp %.1f C >= %.1f C" % (t, COOL_C)
        if parked is not None and parked >= COOL_PARK_S:
            return True, "not cooled by reading (%s), %s >= %d s: admitted on the fastboot storage-temperature (< %.1f C, under the lock) and the boot's first temp row (< %.1f C)" % (
                why, pk, COOL_PARK_S, COOL_C, COOL_C), True
        return False, "%s; %s < %d s and no cooler reading since (no boot until a reading < %.1f C)" % (why, pk, COOL_PARK_S, COOL_C), False
    if t is not None:
        return True, "last battery temp %.1f C < %.1f C (%s)" % (t, COOL_C, pk), False
    if parked is not None and parked >= COOL_PARK_S:
        return True, "no temperature reading known, %s >= %d s; the boot's first temp row must be < %.1f C" % (pk, COOL_PARK_S, COOL_C), True
    return False, "no temperature reading known and %s < %d s (never assumed cool)" % (pk, COOL_PARK_S), False


# ---- kernel / tree facts --------------------------------------------------------------------------------------
def kernel_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def kernel_bootline_len(kb):
    m = BOOTLINE_TAG.search(kb)
    return int(m.group(1)) if m else None


def mkpayload_len(tree):
    """BOOT_LINE_LENGTH of this tree's tools/mkpayload.py (the shim side of the boot line), or None."""
    try:
        s = open(os.path.join(tree, "tools", "mkpayload.py")).read()
    except OSError:   # lint-silent: expected a tree without tools/mkpayload.py: None, printed as a GAP on the S7 line
        return None
    m = re.search(r"^BOOT_LINE_LENGTH = (\d+)", s, re.M)
    return int(m.group(1)) if m else None


def git(tree, *a, timeout=60):
    r = subprocess.run(["git", "-C", tree, *a], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout


def trunk_has(tree, needle, ref="trunk"):
    """True/False: NEEDLE in xnu/patches of the trunk ref; None when git cannot tell."""
    try:
        rc, _ = git(tree, "grep", "-q", "-F", needle, ref, "--", "xnu/patches")
    except (OSError, subprocess.TimeoutExpired):   # lint-silent: expected git missing/slow: None, printed as a GAP on the S5 line
        return None
    return True if rc == 0 else False if rc == 1 else None


def reviews_dir(tree):
    """The super repo's docs/reviews: $PIXOS_REVIEWS_DIR, else <kernel clone>/../../docs/reviews (the clone lives in the super repo's pixos-project/)."""
    if os.environ.get("PIXOS_REVIEWS_DIR"):
        return os.environ["PIXOS_REVIEWS_DIR"]
    try:
        rc, out = git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except (OSError, subprocess.TimeoutExpired):   # lint-silent: expected git missing/slow: None, S9 then FAILs on a marked item ("no reviews dir")
        return None
    if rc != 0:
        return None
    return os.path.normpath(os.path.join(out.strip(), "..", "..", "..", "docs", "reviews"))


def review_verdict(rdir, keys):
    """(file, verdict) of the first review in RDIR whose name or text holds one of KEYS, else (None, None)."""
    try:
        names = sorted(os.listdir(rdir))
    except OSError:   # lint-silent: expected an unreadable reviews dir = no review found: the marked item FAILs S9
        return None, None
    for n in names:
        if not n.endswith(".md"):
            continue
        try:
            txt = open(os.path.join(rdir, n), errors="replace").read()
        except OSError as e:
            print("SAFETY GAP: review %s unreadable (%s): not counted" % (n, e))
            continue
        if any(k and (n.startswith(k) or k in txt) for k in keys):
            m = VERDICT_RX.search(txt)
            return n, (m.group(1).strip().strip("*").strip() if m else "")
    return None, None


def is_safe_verdict(v):
    v = (v or "").upper()
    return v.startswith("SAFE") and "UNSAFE" not in v


def marked_items(tree, patch_dirs):
    """[(what, keys)] of every rule-8 marker in the commit subjects reachable from TREE's HEAD and in the patch headers of PATCH_DIRS."""
    items = []
    rc, out = git(tree, "log", "--format=%H %s", "HEAD")
    if rc != 0:
        raise RuntimeError("git log failed in %s" % tree)
    for ln in out.splitlines():
        sha, _, subj = ln.partition(" ")
        if MARK_RX.search(subj):
            items.append(("commit %s %s" % (sha[:8], subj[:90]), [sha[:8], sha[:12]]))
    for pd in patch_dirs:
        names = sorted(os.listdir(pd))   # an unreadable patch dir raises: S9 FAILs ("cannot list the rule-8 marks")
        for n in names:
            if not n.endswith(".patch"):
                continue
            with open(os.path.join(pd, n), errors="replace") as f:
                head = f.readline()
            if MARK_RX.search(head):
                items.append(("patch %s" % n, [n[:4] + "-", n]))
    return items


# ---- live rows -------------------------------------------------------------------------------------------------
def parse_row(text):
    """{'vcell','soc','temp'} of one console line (None for a value it does not carry), or None for a non-battery line."""
    m = ROW_RX.search(text)
    if m:
        return {"vcell": int(m.group(1)), "soc": float(m.group(2)) if m.group(2) else None, "temp": float(m.group(3)) if m.group(3) else None}
    m = P2_RX.search(text)
    if m:
        return {"vcell": int(m.group(1)), "soc": float(m.group(2)), "temp": None}
    return None


def median3(vals):
    return statistics.median(vals[-3:]) if vals else None


class Monitor:
    """Live S1/S3/S4/S6 of one boot. feed(epoch, text) per console line (epoch = host arrival time), check(now) -> '' or 'SAFETY FAIL ...'.
    usb(now, gadget_on_bus, fastboot_on_bus) per poll: the iOS gadget leaving the bus starts the reset wait (RESET_BACK_S)."""

    def __init__(self, t_boot, defer_s2=False, defer_s4=False, p3_temp=False):
        self.t_boot = t_boot
        self.p3_temp = p3_temp   # the kernel prints temp= in the P3 rows (patch 0334): no blind window, S3 counts from the first USB log line
        self.first_line = self.last_line = None
        self.first_temp = self.last_temp_at = self.last_temp = None
        self.vcells, self.socs = [], []
        self.defer_s2, self.defer_s4 = defer_s2, defer_s4
        self.gone_at = None
        self.gadget_seen = self.reset_done = False
        self.notes = []

    def feed(self, epoch, text):
        if self.first_line is None:
            self.first_line = epoch
        self.last_line = epoch
        r = parse_row(text)
        if not r:
            return None
        if r["vcell"] is not None:
            self.vcells.append(r["vcell"])
        if r["soc"] is not None:
            self.socs.append(r["soc"])
        if r["temp"] is not None:
            self.last_temp, self.last_temp_at = r["temp"], epoch
            if self.first_temp is None:
                self.first_temp = r["temp"]
        return r

    def usb(self, now, gadget, fastboot):
        """Only a gadget that was SEEN on the bus can leave it (a sysfs that never shows it must not switch the silence check off)."""
        if gadget:
            self.gadget_seen = True
        if self.gone_at is None and self.gadget_seen and self.first_line is not None and not gadget:
            self.gone_at = now
        if self.gone_at is not None and fastboot:
            self.reset_done = True

    def check(self, now):
        if self.last_temp is not None and self.last_temp >= HOT_C:
            return "SAFETY FAIL S1: battery temp %.1f C >= %.1f C" % (self.last_temp, HOT_C)
        if self.defer_s2 and self.first_temp is not None and self.first_temp >= COOL_C:
            return "SAFETY FAIL S2: first battery temp of this boot %.1f C >= %.1f C (the boot was admitted on a fastboot reading or a park time only)" % (self.first_temp, COOL_C)
        if self.defer_s4 and self.socs and self.socs[0] < SOC_FLOOR:
            return "SAFETY FAIL S4: first soc of this boot %.2f %% < %.1f %% (the last known soc was low)" % (self.socs[0], SOC_FLOOR)
        soc, mv = median3(self.socs), median3(self.vcells)
        if soc is not None and soc < SOC_CUT:
            return "SAFETY FAIL S4: battery soc %.2f %% < cut %.1f %% (median of the last 3 rows)" % (soc, SOC_CUT)
        if mv is not None and mv < VCELL_MIN:
            return "SAFETY FAIL S4: battery vcell %d mV < %d mV (median of the last 3 rows)" % (mv, VCELL_MIN)
        if self.reset_done:
            return ""
        if self.gone_at is not None:
            if now - self.gone_at > RESET_BACK_S:
                return "SAFETY FAIL S6: the phone left the bus %d s ago and is not back as fastboot/recovery (18d1): phone may need Power+VolDown" % (now - self.gone_at)
            return ""
        if self.first_line is None:
            if now - self.t_boot > ALIVE_FIRST_S:
                return "SAFETY FAIL S6: no USB log within %d s of the fastboot boot: phone may need Power+VolDown" % ALIVE_FIRST_S
            return ""
        if now - self.last_line > ALIVE_GAP_S:
            return "SAFETY FAIL S6: USB log silent for %d s > %d s: phone may need Power+VolDown" % (now - self.last_line, ALIVE_GAP_S)
        if self.last_temp_at is None:
            if self.p3_temp:
                if now - self.first_line > TEMP_GAP_S:
                    return "SAFETY FAIL S3: no temp= row within %d s of the first USB log line (treated as hot; this kernel prints temp= in the P3 rows, patch 0334)" % TEMP_GAP_S
                return ""
            if now - self.t_boot > TEMP_FIRST_S:
                return ("SAFETY FAIL S3: no temp= row within %d s of the fastboot boot (treated as hot; GAP: the kernel prints temp= only in the "
                        "patch 0307 MON rows, or the rows stopped)" % TEMP_FIRST_S)
            return ""
        if now - self.last_temp_at > TEMP_GAP_S:
            return "SAFETY FAIL S3: no temp= row for %d s > %d s (treated as hot, never assumed cool)" % (now - self.last_temp_at, TEMP_GAP_S)
        return ""


def record_fail(d, why, now):
    """A monitor stop: S1/S3 (and the S2 first-row check) mark the state hot, S4 low; every stop ends the boot (end_at = the park clock)."""
    kv = {"end_at": now, "end_why": why[:300]}
    if re.match(r"SAFETY FAIL S[123]:", why):
        kv.update(hot=True, hot_why=why[:300], hot_at=now)
    if why.startswith("SAFETY FAIL S4:"):
        kv.update(low=True, low_why=why[:300])
    return update_state(d, **kv)


def human_needed(d, line, now):
    """One line in DIR/human-needed.log (the 2-hourly check counts these as WALL)."""
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "human-needed.log"), "a") as f:
        f.write("%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), line.replace("\n", " ")[:400]))


# ---- xrun_report hooks (waitusb + report) ----------------------------------------------------------------------
def waitusb_stop(d, run, mark, now):
    """True (after marking safety_stop and printing SAFETY) when the boot must stop: the monitor's stop file, or a monitor that is not
    running (rule 7: a boot without a live monitor stops). False while the boot may go on."""
    p = os.path.join(d, "safety-stop")
    why = ""
    if os.path.exists(p):
        why = open(p, errors="replace").read().strip() or "SAFETY FAIL MON: empty stop file"
    elif run.get("safety_monitor_pid"):
        try:
            os.kill(int(run["safety_monitor_pid"]), 0)
        except (OSError, ValueError):
            if os.path.exists(p + ".done"):   # the monitor ended normally (the phone came back as 18d1 after a reset): the reset path goes on
                return False
            why = open(p, errors="replace").read().strip() if os.path.exists(p) else ""
            why = why or "SAFETY FAIL MON: the safety monitor (pid %s) is not running: no live S1/S3/S4/S6 check (rule 7)" % run["safety_monitor_pid"]
            with open(p, "w") as f:
                f.write(why + "\n")
    if not why:
        return False
    mark(d, ["safety_stop=" + why[:400], "early=%f" % now, "t_end=%f" % now])
    print("SAFETY")
    return True


def report_safety(run):
    """(result override or None, reason, [report lines]) from the run record's safety marks (only runs xrun marked safety=required:
    older run dirs replay unchanged). A booted run without its pre PASS or a monitor FAILs."""
    lines, res, why = [], None, ""
    if run.get("safety") != "required":
        return None, "", lines
    if run.get("safety_pre"):
        lines.append("SAFETY: " + run["safety_pre"])
    if run.get("safety_stop"):
        lines.append("SAFETY STOP: " + run["safety_stop"])
        res, why = "FAIL", run["safety_stop"]
    if run.get("human_needed"):
        lines.append("HUMAN-NEEDED: " + run["human_needed"])
    if run.get("t_boot") and not res:
        if not run.get("safety_pre"):
            res, why = "FAIL", "SAFETY FAIL: the boot has no SAFETY PASS pre line (tools/safety-check pre; rule 7, RULES H9)"
        elif not run.get("safety_monitor_pid"):
            res, why = "FAIL", "SAFETY FAIL: the boot ran without a live safety monitor (tools/safety-check monitor; rule 7, RULES H9)"
    return res, why, lines
