"""resident_thermal: battery temperature safety of tools/resident (docs/SAFETY-CHECKLIST.md S1-S3, RULES H9; user 2026-11-12: the phone was
"hot as fuck", resident-204614-b1 logged temp=51.6 C under a GPU stress job, b457 52-53 C). Library of tools/resident_session_lib.py (split
out for the 800-line limit); host-tested by tools/test_resident.py. Until tools/safety-check lands, these are the resident's own S1-S3.

  S1 live, every row: battery temp= (patch 0307 SWEET CHG MON rows) >= hot_c 45.0 C -> stop the job (CUT), park in fastboot, no new boot until S2.
  S2 pre-boot: cooled since the last run: the fastboot `storage-temperature` (UFS sensor, the only temperature fastboot reports on bluejay
     [obs fastboot getvar all 2026-11-12 21:10Z: 54 C right after the 51.6 C park]) < cool_c 38.0 C; unreadable -> the last battery reading
     < 38 C and no hot park pending, else >= cool_park_s 900 s parked. After a hot park the FIRST temp row of the next boot must be < 38 C too.
  S3 live: no temp= row for > temp_gap_s 60 s while booted = hot (S1 action + GAP line; never assume cool). The kernel prints the first MON
     row only ~330 s after its charger start (~420 s uptime, resident-204614-b1 [obs]; the P3 rows carry no temp): before the first row the
     limit is temp_first_s 480 s from the fastboot boot (GAP named on the line).
State across sessions: $RESIDENT_DIR/thermal.json {last_c, last_at, hot, why, parked_at}."""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety_lib as SL  # noqa: E402  (the ONE S2 rule: tools/safety-check pre applies SL.s2_state, so the resident asks the same function; tracker L-17)

DEFAULTS = {"hot_c": 45.0, "cool_c": 38.0, "temp_gap_s": 60, "temp_first_s": 480, "cool_park_s": 900, "admit_margin_c": 1.0}
TEMP_RX = re.compile(r"SWEET CHG (?:P3|MON): t=\d+s [^\n]*?temp=(-?\d+(?:\.\d+)?) C")


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):   # lint-silent: expected no state yet (first run) or a torn write: no reading known
        return {}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, path)


class Thermal:
    def __init__(self, cfg=None, path=None):
        self.c = dict(DEFAULTS, **{k: v for k, v in (cfg or {}).items() if k in DEFAULTS})
        self.path = path
        self.st = load_state(path) if path else {}
        self.t_boot = self.last_t = self.last_c = None
        self.first_check = False

    def boot(self, t_boot):
        """The fastboot boot of a new image (xrun's t_boot): the S3 clock starts; after a hot park the first row is checked (S2)."""
        self.t_boot, self.last_t, self.last_c = t_boot, None, None
        self.first_check = bool(self.st.get("hot"))

    def feed(self, now, text):
        for m in TEMP_RX.finditer(text or ""):
            self.last_t, self.last_c = now, float(m.group(1))
            self.st.update(last_c=self.last_c, last_at=now)
        if self.path and self.last_t == now:
            save_state(self.path, self.st)

    def live(self, now):
        """'' while the boot is safe, else the SAFETY FAIL text (S1 / S2 first row / S3)."""
        c = self.c
        if self.last_c is not None and self.last_c >= c["hot_c"]:
            return "SAFETY FAIL S1: battery temp %.1f C >= %.1f C" % (self.last_c, c["hot_c"])
        if self.first_check and self.last_c is not None:
            self.first_check = False
            if self.last_c >= c["cool_c"]:
                return "SAFETY FAIL S2: first battery row after a hot park %.1f C >= %.1f C" % (self.last_c, c["cool_c"])
            self.st["hot"] = False
        if self.t_boot is None:
            return ""
        if self.last_t is None:
            if now - self.t_boot > c["temp_first_s"]:
                return "SAFETY FAIL S3: no temp= row within %d s of the fastboot boot (GAP: kernel without patch 0307 temp, or the rows stopped; treated as hot)" % c["temp_first_s"]
            return ""
        if now - self.last_t > c["temp_gap_s"]:
            return "SAFETY FAIL S3: no temp= row for %d s > %d s (treated as hot, never assumed cool)" % (now - self.last_t, c["temp_gap_s"])
        return ""

    def parked(self, now, why=""):
        """Every park: the S2 clock; a SAFETY FAIL park marks the state hot (no boot until S2)."""
        self.st["parked_at"] = now
        if why.startswith("SAFETY FAIL"):
            self.st.update(hot=True, why=why[:300])
        self.t_boot = None
        if self.path:
            save_state(self.path, self.st)

    def pre_boot(self, now, storage_c, shared=None):
        """S2 before a boot: '' when it may boot, else why not. STORAGE_C = fastboot storage-temperature (None = unreadable).
        SHARED = shared_s2(now) = (ok, text, seconds_left): xrun's own `safety-check pre` S2 (L-17: the resident admitted on a 37 C storage reading, xrun
        refused it seconds later on the last in-boot battery reading + park time, one wasted boot cycle each). It must pass in addition to the rules below."""
        c, st = self.c, self.st
        if shared is not None and not shared[0]:
            return "SAFETY FAIL S2 (the rule xrun's safety-check pre applies): %s%s" % (shared[1], "; %d s until it admits on the park time" % shared[2] if shared[2] else "")
        if storage_c is not None:
            # L-12: admit only admit_margin_c below xrun's own S2 line, so a reading on the edge cannot pass here and fail in xrun seconds later
            lim = c["cool_c"] - c["admit_margin_c"]
            return "" if storage_c < lim else "SAFETY FAIL S2: fastboot storage-temperature %.0f C >= %.1f C (S2 %.1f C minus the %.1f C admit margin; cooling in fastboot)" % (
                storage_c, lim, c["cool_c"], c["admit_margin_c"])
        if st.get("last_c") is not None and st["last_c"] < c["cool_c"] and not st.get("hot"):
            return ""
        waited = now - st.get("parked_at", 0)
        if st.get("parked_at") and waited >= c["cool_park_s"]:
            return ""
        return "SAFETY FAIL S2: no temperature readable in fastboot, last battery reading %s, parked %d s < %d s" % (
            "%.1f C" % st["last_c"] if st.get("last_c") is not None else "none", waited if st.get("parked_at") else 0, c["cool_park_s"])


def fastboot_storage_temp():
    """bluejay's fastboot `storage-temperature` (C), None when the device is not a bluejay in fastboot or the value is absent (AGENTS rule 2)."""
    try:
        pr = subprocess.run(["fastboot", "getvar", "product"], capture_output=True, text=True, timeout=15)
        if not re.search(r"^product:\s*bluejay\s*$", pr.stdout + pr.stderr, re.M):
            return None
        r = subprocess.run(["fastboot", "getvar", "storage-temperature"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):   # lint-silent: expected no fastboot binary / phone busy: None, the caller writes the GAP
        return None
    m = re.search(r"storage-temperature:\s*(-?\d+(?:\.\d+)?)", r.stdout + r.stderr)
    return float(m.group(1)) if m else None


def shared_s2(now, state_dir=None):
    """(ok, text, seconds_left) of tools/safety_lib.s2_state on the state xrun's safety-check pre reads (same dir, same park time). seconds_left = the
    park time still missing (0 when the rule waits for a cooler reading instead)."""
    d = state_dir or SL.default_state_dir()
    st = SL.load_state(d)
    ok, text, _ = SL.s2_state(st, now)
    park = SL.last_park(st)
    left = 0 if ok or park is None else max(0, int(SL.COOL_PARK_S - (now - park) + 0.999))
    return ok, text, left


def hot_park(sess, why, event, persist, notify):
    """S1/S3 action at a boundary: stop the boot, park, keep every queued job queued, start a new session (it waits at S2 in fastboot)."""
    sess.state, sess.why = "ended", why
    event("RESIDENT PARK: %s" % why)
    for j in list(sess.pending.values()):
        persist(j)
        notify(j, "RESIDENT JOB REQUEUED id=%s: %s; it stays queued, the next boot waits until the phone has cooled (S2)" % (j["id"], why[:160]))
    sess.stop_boot("hot")
    sess.ph.park("resident " + why[:80])
    sess.therm.parked(sess.ph.now(), why)
    sess.save()
    if sess.pending:
        sess.ph.restart_session()


def gate(sess, event):
    """S2 at a boot: True when the boot may go; else one line (on change) and a 60 s wait."""
    why = sess.therm.pre_boot(sess.ph.now(), sess.ph.storage_temp(), sess.ph.shared_s2(sess.ph.now()))
    if not why:
        return True
    if why != getattr(sess, "gate_told", None):
        event("RESIDENT WAIT: %s; no boot until it is cool" % why)
        sess.gate_told = why
    sess.ph.sleep(60)
    return False


def battery_rows_in(sess, t, now, event):
    """The 0298/0307 rows of a new log chunk T: the battery rule (moved here from Session.pump for the size limit) and the temperature (S1/S3)."""
    import resident_core as rc
    sess.therm.feed(now, t)
    for row_t, mv, ua, soc in rc.battery_rows(t):
        sess.bat.add(now, mv, ua, sess.buf is not None, soc)
        md = sess.bat.mode()
        # the P3 't=-0s ... (before the write)' snapshot never carries soc (0307 by design): it names no rule (resident-200221-b1 got a
        # false GAP from it); later rows announce the rule, and again whenever it changes
        if row_t > 0 and md != sess.bat_mode_told:
            sess.bat_mode_told = md
            if md == "soc":
                event("RESIDENT BATTERY: %s rule = fuel-gauge soc (floor %.1f %%, cut %.1f %%; raw vcell hard limit %d mV)" % (
                    sess.boot[1]["label"], sess.cfg["floor_soc"], sess.cfg["cut_soc"], sess.cfg["vcell_hard_mv"]))
            else:
                event("RESIDENT GAP: boot %s: battery rows carry no soc= (kernel without patch 0307): rule = load-compensated vcell (floor %d mV, r_int_mohm %d)%s" % (
                    sess.boot[1]["label"], sess.cfg["floor_mv"], sess.cfg["r_int_mohm"],
                    " + the boot's P2 RepSOC %.2f %%" % sess.bat.boot_soc if md == "vcell+p2" else "; no P2 RepSOC line either"))


# ---- tools/safety-check through tools/xrun (RULES H9; the call contract in `tools/safety-check --help`: xrun runs pre / fastboot / monitor)
SAFETY_RX = re.compile(r"SAFETY FAIL (S\d+|MON|pre|fastboot)\b[^\n]*")
WAIT_ITEMS = ("S2", "S4", "fastboot")   # cooling / charge: not the job's fault, the jobs wait; S3/S6/S7/S8/S9 refusals are the job's own


def safety_fail_in(path):
    """(item, line) of the first SAFETY FAIL line in xrun's output PATH, None when none (or no file)."""
    try:
        with open(path, errors="replace") as f:
            m = SAFETY_RX.search(f.read())
    except OSError:   # lint-silent: expected no xrun.out yet: no safety stop seen
        return None
    return (m.group(1), m.group(0)[:400]) if m else None


def safety_stop(sess, label, batch, sf, booted, persist, event, ends_alone, notify):
    """xrun ended on a SAFETY FAIL. Before the fastboot boot: S2/S4 = the jobs wait (no crash count, 120 s, the next boot is checked again);
    another item = the job's own refusal (resident_fit.ends_alone: one job FAIL, several solo). After it: the monitor stopped the boot (xrun
    parked the phone): the queue is kept and a new session waits (hot_park). True = the session goes on."""
    item, line = sf
    sess.stop_boot("safety")
    if booted:
        hot_park(sess, line, event, persist, notify)
        return False
    if item in WAIT_ITEMS:
        event("RESIDENT WAIT: boot %s refused by safety-check (%s): the jobs wait, checked again in 120 s" % (label, line))
        sess.therm.parked(sess.ph.now())
        sess.ph.sleep(120)
        return True
    return ends_alone(sess, label, batch, "safety-check refused its boot: " + line, persist, event)
