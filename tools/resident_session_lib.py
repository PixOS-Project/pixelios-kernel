"""resident_session_lib: the session owner of tools/resident (Session, RealPhone, JobRun) and the shared file helpers. Library of tools/resident,
host-tested by tools/test_resident.py (a fake phone replaces RealPhone). Rules and file layout: tools/resident's docstring."""
import argparse, hashlib, importlib.machinery, importlib.util, json, os, re, shlex, shutil, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import resident_core as rc  # noqa: E402
from resident_notice import job_notify, load_owners, owners_dir  # noqa: E402,F401  (notice routing, split out for the 800-line limit)
import resident_fit as rf  # noqa: E402  (T-51 batch fit + host build failures)
import resident_thermal as rt  # noqa: E402  (SAFETY-CHECKLIST S1-S3: battery temperature)
from resident_phone_io import PhoneLogIO  # noqa: E402  (RealPhone's run-dir/log readers, split out for the 800-line limit)
from resident_fit import WAIVERS, print_bootline, xrun_env  # noqa: E402,F401  (tools/resident's submit guard G1 + its imports)

OWNER = "resident"
# the stall FAULT only (patch 0279:151 panic text); `SWEET STALL: armed` and `SWEET STALL: usb-log drain max gap` are healthy rows
# (run resident-134052-b1 2026-11-11 ended on `armed`: the first live session's false crash)
PANIC_RX = r"Kernel trap at|panic\(cpu|panic:|Assertion failed|SWEET STALL: the usb-log drain thread did not run"
STEP_OPS = ("push", "restart", "run", "hot", "wait-for", "sleep", "pull", "host")
STREAMS = ("gpu", "bt", "wlan", "tools", "demo")


# devsh's own refusals that happen on the host before it connects to the phone (tools/devsh hardware_gate): retry-safe
HOST_INFRA_RX = re.compile(r"cannot run \S*phone-lock check: [^\n]*")


def xrun_argv(spec, label, duration, profile, tree):
    """The xrun command of one boot: SPEC (kernel, args, camera) on PROFILE, overlay TREE. One copy: the boot and the submit guard (G1)."""
    args = rc.parse_args(profile.get("args"))
    names = ["devd"]
    for src in (profile.get("names"), args.get("sweetnull"), spec["args"].get("sweetnull")):   # one sweetnull= list: devd + all names
        names += [n for n in (src or "").split(",") if n and n not in names]
    for k, v in spec["args"].items():
        args[k] = v
    args["sweetnull"] = ",".join(names)
    xr = [os.path.join(HERE, "xrun"), "--kernel", spec["kernel"], "--overlay", tree, "--label", label, "--duration", str(int(duration)),
          "--hard-timeout", str(int(duration) + 60), "--restart", "%d,bootloader" % (int(duration) + 120), "--stop-on", "RESIDENT NEVER " + label,
          "--fail-on", PANIC_RX, "--args", rc.args_text(args)] + ([] if spec.get("camera") else ["--no-camera"])
    if profile.get("intent"):
        xr += ["--intent", profile["intent"]]
    return xr + [os.path.expanduser(x) for x in profile.get("xrun", [])]


class BootRefused(Exception):
    """A boot refused on the host before the phone is touched (the why goes to every job of its batch)."""


class Fail(Exception):
    pass


def rdir():
    return os.environ.get("RESIDENT_DIR") or os.path.expanduser("~/pixelios-artifacts/resident")


def p(*parts):
    return os.path.join(rdir(), *parts)


def jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:   # lint-silent: expected the state file does not exist yet (caller default)
        return default


def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tree_files(d):
    out = {}
    for base, _, files in os.walk(d):
        for n in files:
            fp = os.path.join(base, n)
            out[os.path.relpath(fp, d)] = sha256(fp)
    return out


def event(line):
    os.makedirs(rdir(), exist_ok=True)
    cap = int(os.environ.get("RESIDENT_EVENTS_CAP") or 0)   # tests only: a runaway session raises instead of writing GBs (2026-11-12)
    if cap and os.path.exists(p("events.log")) and os.path.getsize(p("events.log")) > cap:
        raise RuntimeError("events.log over RESIDENT_EVENTS_CAP %d B: runaway session" % cap)
    with open(p("events.log"), "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), line))
    print(line, flush=True)


def load(name, fname):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(HERE, fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def ple():
    """tools/phone_lock_end bound to tools/phone-lock: park/notify/stop_group come from there (no second copy)."""
    load("phone_lock_for_resident", "phone-lock")
    import phone_lock_end
    return phone_lock_end


def session_live():
    s = jload(p("session.json"))
    if not s or s.get("state") == "ended":
        return None
    try:
        os.kill(int(s["pid"]), 0)
    except (KeyError, TypeError, ValueError, ProcessLookupError):   # lint-silent: expected a dead or absent owner = no live session
        return None
    except PermissionError:   # lint-silent: expected EPERM = the pid exists
        pass
    return s


def starting():
    """A start that is still waiting for the phone lock (no session.json yet)."""
    s = jload(p("starting.json"))
    return s if s and time.time() - s.get("at", 0) < 7300 else None


# ---------------------------------------------------------------- the phone (real)
class RealPhone(PhoneLogIO):
    """Everything that touches the phone or the host clock. tools/test_resident.py replaces it with a fake."""

    def __init__(self):
        self.ple = ple()
        self.hotmod = None

    def now(self):
        return time.time()

    def sleep(self, s):
        time.sleep(s)

    def lock_left(self):
        i = self.ple.C.read() or {}
        d = self.ple.hard_deadline(i)
        return (d - time.time()) if d else 0

    def notify(self, thread, line):
        return self.ple.notify("thread:" + thread, line) if thread else 0

    def stage(self, spec, label, duration, profile):
        """(xrun argv, env) of boot LABEL with its overlay tree staged (no phone)."""
        tree = p("boots", label, "tree")
        if os.path.exists(tree):
            shutil.rmtree(tree)
        os.makedirs(tree)
        for ov in ([os.path.expanduser(profile["overlay"])] if profile.get("overlay") else []) + spec["overlays"]:
            shutil.copytree(ov, tree, symlinks=True, dirs_exist_ok=True)
        if self.hotmod is None:
            self.hotmod = load("hot_for_resident", "hot")
        base = os.path.expanduser(profile["launchd_base"]) if profile.get("launchd_base") and not os.path.isfile(os.path.join(tree, self.hotmod.PLIST_REL)) else None
        st = argparse.Namespace(into=tree, launchd_base=base, drop_job=None, build_dir=self.hotmod.BUILD_DIR)
        if self.hotmod.cmd_stage(st) != 0:
            raise Fail("hot stage --into %s failed" % tree)
        return xrun_argv(spec, label, duration, profile, tree), xrun_env()

    def fit(self, spec, label, duration, profile):
        """'' when SPEC's boot is proven to build (resident_fit rule 1), else why not. Host only."""
        why, line = rf.bootline_verdict(*self.stage(spec, label, duration, profile), event)
        return why or ("" if line is not None else "xrun --print-bootargs gave no boot-args line")

    def host_fail(self, h):
        """xrun's host-build die line when it ended before fastboot booted the image (resident_fit rule 2), else ''."""
        return "" if self.boot_started(h) is not None else rf.host_fail_in(p("boots", h["label"], "xrun.out"))

    def boot(self, spec, label, duration, profile):
        xr, env = self.stage(spec, label, duration, profile)
        self.check_bootline(xr, env, label)
        logf = open(p("boots", label, "xrun.out"), "w")
        proc = subprocess.Popen(xr, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        return {"proc": proc, "label": label, "run_dir": None, "pos": 0, "t0": time.time(), "cmd": xr}

    def check_bootline(self, xr, env, label):
        """The exact line this boot builds (`xrun --print-bootargs`, host only: no build, lock or phone), checked before the boot."""
        why, line = rf.bootline_verdict(xr, env, event)
        if line is None and not why:
            event("RESIDENT GAP: boot %s: xrun --print-bootargs gave no boot-args line: line length not checked" % label)
            return
        if why:
            raise BootRefused(why)
        lim, how = rc.line_limit(xr)
        event("RESIDENT BOOTLINE: %s %d B (shim append limit %s%s)" % (label, len(line.encode()), lim if lim is not None else "none",
                                                                     ", " + how if how else ", the shim appends nothing"))

    def alive(self, h):
        return h["proc"].poll() is None

    def ready(self, h):
        try:
            return subprocess.run([sys.executable, os.path.join(HERE, "hot"), "ping"], capture_output=True, text=True, timeout=30).returncode == 0
        except subprocess.TimeoutExpired:   # lint-silent: expected devd not up yet (USB net still coming): not ready, asked again (live session 2, 2026-11-11)
            return False

    def stop(self, h):
        if self.alive(h):
            self.ple.stop_group(os.getpgid(h["proc"].pid), h["proc"], "xrun " + h["label"])

    def park(self, why):
        return self.ple.park(why)

    def spawn(self, argv, cwd=None, env=None):
        return subprocess.Popen(argv, cwd=cwd or ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)

    def stop_proc(self, pr):
        try:
            os.killpg(os.getpgid(pr.pid), signal.SIGTERM)
            pr.wait(timeout=10)
        except subprocess.TimeoutExpired:   # lint-silent: expected a step that ignores TERM: it gets KILL next
            os.killpg(os.getpgid(pr.pid), signal.SIGKILL)
            pr.wait()
        except ProcessLookupError:   # lint-silent: expected the step ended by itself in between
            pr.wait()

    def hot_argv(self, args):
        return [sys.executable, os.path.join(HERE, "hot")] + args

    def cut(self, h, op, label):
        r = subprocess.run([sys.executable, os.path.join(HERE, "live-cut"), op, label, "--session", self.run_dir(h) or "-"], capture_output=True, text=True)
        m = re.search(r"LIVE CUT: (\S+)", r.stdout)
        return m.group(1) if m else None, r.stdout[-400:]

    def storage_temp(self):
        return rt.fastboot_storage_temp()

    def shared_s2(self, now):
        return rt.shared_s2(now)

    def restart_session(self):
        subprocess.Popen([sys.executable, os.path.join(HERE, "resident"), "start"], cwd=ROOT, env=dict(os.environ, RESIDENT_DIR=rdir()),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- the session owner
class Session:
    def __init__(self, phone, cfg, std_kernel, profiles):
        self.ph, self.cfg, self.profiles = phone, cfg, profiles
        self.std_kernel, self.std_sha = std_kernel, sha256(std_kernel)
        self.bat = rc.Battery(cfg)
        self.therm = rt.Thermal(cfg, p("thermal.json"))
        self.bat_mode_told = None    # the battery rule last announced on this boot (RESIDENT BATTERY / GAP line on every change)
        self.pending = {}
        self.boot = None          # (spec, handle)
        self.boot_up_at = None
        self.idle_since = phone.now()
        self.nboot = 0
        self.sid = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.buf = None           # while a job runs: its scanner (called with every new chunk of whole log lines), else None
        self.state = "starting"
        self.why = ""
        self.tick = 2             # seconds between two looks while waiting (tests use a larger fake step)
        self.boot_batch = set()   # ids the current boot was made for, not run yet (they run before any profile switch)
        self.started = phone.now()
        self.boot_feed, self.boot_feed_size, self.boot_cut_begun = {}, 0, False

    # ---- bookkeeping
    def save(self):
        jsave(p("session.json"), {"pid": os.getpid(), "tools": HERE, "sid": self.sid, "started_utc": self.sid, "state": self.state, "why": self.why,
                                 "boot_label": self.boot[1]["label"] if self.boot else None,
                                 "boot_profile": self.boot[0]["profile"] if self.boot else None, "boot_kernel": self.boot[0]["kernel"] if self.boot else None,
                                 "boot_kernel_sha": self.boot[0]["kernel_sha"] if self.boot else None, "battery_mv": self.bat.last_mv(), "battery_comp_mv": (self.bat.last_comp() or [None])[0],
                                 "lock_left": int(self.ph.lock_left()), "pending": sorted(self.pending)})

    def ingest(self):
        qd = p("queue")
        for n in sorted(os.listdir(qd)) if os.path.isdir(qd) else []:
            jid = n[:-5]
            if jid in self.pending:
                continue
            j = jload(p("jobs", jid, "job.json"))
            if not j:
                event("RESIDENT GAP: queue entry %s has no job.json: dropped" % n)
                os.unlink(os.path.join(qd, n))
                continue
            self.pending[jid] = j
            event("RESIDENT QUEUE: %s stream=%s kind=%s est=%ss max=%ss prio=%s" % (jid, j["stream"], j["kind"], j["est_s"], j["max_s"], j["prio"]))
        for jid in list(self.pending):
            if os.path.exists(p("jobs", jid, "cancel")):
                self.finish(self.pending[jid], "NOTRUN", "cancelled before it started", None)

    def finish(self, job, result, why, slice_dir):
        line = "RESIDENT JOB END id=%s stream=%s result=%s dir=%s why=%s" % (job["id"], job["stream"], result, slice_dir or "-", why[:300])
        jsave(p("jobs", job["id"], "result.json"), {"id": job["id"], "result": result, "why": why, "dir": slice_dir, "line": line, "ended": time.time(),
                                                   "boot": self.boot[1]["label"] if self.boot else None})
        if result in ("NOTRUN", "CUT", "ERROR", "CRASH"):
            event("RESIDENT GAP: job %s %s: %s" % (job["id"], result, why))
        event(line)
        self.pending.pop(job["id"], None)
        try:
            os.unlink(p("queue", job["id"] + ".json"))
        except FileNotFoundError:   # lint-silent: expected the queue entry was already removed
            pass
        job_notify(self.ph, job, line)

    def end_all(self, result, why):
        for j in list(self.pending.values()):
            self.finish(j, result, why, None)

    # ---- the log
    def pump(self):
        if not self.boot:
            return ""
        if self.boot_feed and not self.boot_cut_begun and self.ph.log_exists(self.boot[1]):
            # before the first read: the slice covers the boot from its start (live session 4: a begin before usbnet.log existed was
            # refused by live-cut and the job had no slice dir)
            for jid in self.boot_feed:
                _, out = self.ph.cut(self.boot[1], "begin", "rj-" + jid)
                if "begins at byte" not in out:
                    event("RESIDENT GAP: job %s: slice begin at the boot start refused (%s); it begins at the job's turn" % (jid, out.strip()[-160:]))
            self.boot_cut_begun = True
        t = self.ph.read_new(self.boot[1])
        if t:
            now = self.ph.now()
            if self.bat.boot_soc is None:
                self.bat.boot_soc = rc.p2_soc(t)
            rt.battery_rows_in(self, t, now, event)   # battery rule rows + S1/S3 temperature rows
            if self.buf is not None:
                self.buf(t)
            if self.boot_feed:
                if self.boot_feed_size + len(t) <= 64 << 20:
                    for chunks in self.boot_feed.values():
                        chunks.append(t)
                    self.boot_feed_size += len(t)
                elif self.boot_feed_size <= 64 << 20:
                    self.boot_feed_size += len(t)
                    event("RESIDENT GAP: boot log over 64 MiB before the batch's kernel jobs ran: their oracles miss the rest of the boot-time lines")
        return t

    def boot_dead(self, text=""):
        """None while the boot is healthy, else why it is not (crash, panic, xrun ended)."""
        if not self.boot:
            return None
        m = next((m for m in (re.search(PANIC_RX, ln) for ln in (text or "").splitlines() if not rc.MIRROR_RX.search(ln)) if m), None)
        if m:
            return "panic/stall line in the log: %r" % m.group(0)
        if not self.ph.alive(self.boot[1]):
            first = (self.ph.crash_text(self.boot[1]).splitlines() or ["no report"])[0]
            return "xrun ended (the boot is gone): " + first[:200]
        return None

    def crash(self, why, during_boot=False):
        """User 2026-11-12: a crash ends only the RUNNING job (JobRun ended it CRASH already); every queued job stays queued. A crash while
        booting counts for that boot's own jobs: a job whose boot crashed twice ends CRASH (a kernel that never comes up is not retried
        forever). The phone parks (H3), then a new session starts for the kept jobs."""
        self.state, self.why = "ended", "crash: " + why
        event("RESIDENT CRASH: %s; report: %s" % (why, self.ph.crash_text(self.boot[1])[:600].replace("\n", " | ") if self.boot else "-"))
        for j in list(self.pending.values()):
            if during_boot and j["id"] in self.boot_batch:
                j["boot_crashes"] = j.get("boot_crashes", 0) + 1
                if j["boot_crashes"] >= 2:
                    self.finish(j, "CRASH", "its boot crashed twice (last: %s)" % why, None)
                    continue
            jsave(p("jobs", j["id"], "job.json"), j)
            job_notify(self.ph, j, "RESIDENT JOB REQUEUED id=%s: session %s crashed (%s); it stays queued, a new session runs it after the park" % (j["id"], self.sid, why[:160]))
        r = self.stop_boot("crash")
        if self.pending:
            event("RESIDENT REQUEUE: %d job(s) stay queued after the crash; the phone parks, then a new session starts" % len(self.pending))
            self.save()   # state=ended on disk first: the new start must not see this session as live
            self.ph.restart_session()
        return r

    def stop_boot(self, why):
        if self.boot:
            self.ph.stop(self.boot[1])
            self.boot = None
        self.boot_up_at = None
        self.boot_feed, self.boot_feed_size, self.boot_cut_begun = {}, 0, False
        return why

    # ---- boots
    def do_boot(self, spec, batch):
        fresh = jload(p("profiles.json"))   # re-read at every boot: a profile added after the session started is usable (2026-11-11 all_on1)
        if fresh:
            self.profiles = fresh
        prof = self.profiles.get(spec["profile"])
        if prof is None:
            for jid in batch:
                if jid in self.pending:
                    self.finish(self.pending[jid], "NOTRUN", "unknown profile %r (profiles.json has %s)" % (spec["profile"], sorted(self.profiles)), None)
            return True
        self.nboot += 1
        label = "resident-%s-b%d" % (self.sid[-7:-1], self.nboot)
        dur = max(300, self.ph.lock_left() - self.cfg["lock_margin_s"])
        persist = lambda j: jsave(p("jobs", j["id"], "job.json"), j)  # noqa: E731
        if len(batch) > 1:   # T-51 rule 1: a merged boot runs only when its line is proven to build (checked while the old boot still runs)
            why = self.ph.fit(spec, label, dur, prof)
            if why:
                spec, batch = rf.split_batch(self, spec, batch, why, persist, event)
        if self.boot:
            self.stop_boot("reboot")
            self.ph.park("resident reboot")
            self.therm.parked(self.ph.now())
        if not rt.gate(self, event):   # S2: no boot until the phone has cooled (it waits in fastboot)
            self.nboot -= 1
            return True
        event("RESIDENT BOOT: %s profile=%s kernel=%s args=%r jobs=%s duration=%ds" % (label, spec["profile"], spec["kernel_sha"][:12], rc.args_text(spec["args"]), batch, dur))
        try:
            h = self.ph.boot(spec, label, dur, prof)
        except BootRefused as e:   # T-51 rule 2: its jobs end alone (FAIL / solo), the session goes on
            event("RESIDENT GAP: boot %s refused before the phone: %s" % (label, e))
            return rf.ends_alone(self, label, batch, "boot refused: %s" % e, persist, event)
        self.boot = (spec, h)
        # kernel jobs of this batch read the WHOLE boot log (their lines come during the boot, before their own turn): their slice
        # begins at the boot start, and the chunks seen before their turn are kept for their scanner (cap 64 MiB each, GAP beyond)
        self.boot_batch = set(batch)
        self.boot_feed = {jid: [] for jid in batch if jid in self.pending}   # every job the boot was made for (demo: job 1 = boot timing)
        self.boot_feed_size = 0
        self.boot_cut_begun = False
        self.bat.reset()
        self.bat_mode_told = None
        self.save()
        # two limits (live 2026-11-11 resident-173747-b1: a 6-min shim rebuild ate the old single 420 s limit and the session called a crash
        # 11 s after the first USB line): build_s until fastboot boots the image (xrun's t_boot), then boot_cost_s from that moment
        t_build_end = self.ph.now() + self.cfg["build_s"]
        t_end = None
        # T-40 (resident-054539-b1, lane-nav 2026-11-12) [obs]: SBCoverSheet came at 05:47:52Z, devd answered the ping only at 05:48:08Z (first
        # reply 11.8 s); the text pumped here was dropped, so the ready wait never saw the line and waited 600 s. Ready regexes are matched here too.
        want_ready = list(prof.get("ready") or [])
        seen_early = set()
        while True:
            tb = self.ph.boot_started(h)
            if tb is None and self.ph.now() >= t_build_end:
                self.crash("boot %s: xrun did not boot the image within %d s (build/converge)" % (label, self.cfg["build_s"]), during_boot=True)
                return False
            if tb is not None and t_end is None:
                t_end = tb + self.cfg["boot_cost_s"]
                self.therm.boot(tb)
            if t_end is not None and self.ph.now() >= t_end:
                break
            t = self.pump()
            dead = self.boot_dead(t)
            hf = self.ph.host_fail(h) if dead else ""
            if hf:   # T-51 rule 2: xrun died in its host build, the phone untouched
                self.stop_boot("host build failed")
                return rf.ends_alone(self, label, batch, hf, persist, event)
            sf = self.ph.safety_fail(h) if dead else None
            if sf:   # RULES H9: xrun's safety-check refused or stopped the boot
                return rt.safety_stop(self, label, batch, sf, tb is not None, persist, event, rf.ends_alone,
                                      lambda j, ln: job_notify(self.ph, j, ln))
            if dead:
                self.crash("boot %s: %s" % (label, dead), during_boot=True)
                return False
            seen_early |= {rx for rx in want_ready if re.search(rx, t, re.M)}
            if self.ph.ready(h):
                self.boot_up_at = self.idle_since = self.ph.now()
                event("RESIDENT READY: %s up after %d s (devd answers)" % (label, self.ph.now() - h["t0"]))
                return self.wait_ready_lines(label, prof, seen_early)
            self.ph.sleep(5)
        self.crash("boot %s: devd did not answer within %d s of the fastboot boot" % (label, self.cfg["boot_cost_s"]), during_boot=True)
        return False

    def wait_ready_lines(self, label, prof, seen_early=frozenset()):
        """The profile's `ready` regexes (e.g. the GPU boot nav that unlocks the phone): jobs start after them. Missing at `ready_s` = GAP, the
        boot is still used (its jobs' own oracles decide). seen_early: regexes already matched while waiting for devd (T-40)."""
        want = list(prof.get("ready") or [])
        seen = set(seen_early) & set(want)
        if want and seen >= set(want):
            event("RESIDENT READY: %s ready lines seen: %s (before devd answered)" % (label, want))
            self.idle_since = self.ph.now()
            return True
        t_end = self.ph.now() + float(prof.get("ready_s", 300))
        while want and self.ph.now() < t_end:
            t = self.pump()
            dead = self.boot_dead(t)
            if dead:
                self.crash("boot %s: %s" % (label, dead), during_boot=True)
                return False
            seen |= {rx for rx in want if re.search(rx, t, re.M)}
            if seen >= set(want):
                event("RESIDENT READY: %s ready lines seen: %s" % (label, want))
                break
            self.ph.sleep(5)
        else:
            if want:
                event("RESIDENT GAP: boot %s: ready line(s) %s not seen within %s s; jobs start anyway" % (label, sorted(set(want) - seen), prof.get("ready_s", 300)))
        self.idle_since = self.ph.now()
        return True

    # ---- one job
    def run_job(self, job):
        """One job on the running boot; False when the session crashed during it."""
        return JobRun(self, job).run()

    # ---- main loop
    def serve(self):
        """serve_loop under a guard: an unexpected error ends the session like a crash (every submitter told, boot stopped), never silently."""
        try:
            return self.serve_loop()
        except Exception as e:   # noqa: BLE001 - every error must reach the submitters (rule 7); the traceback goes to the run log
            import traceback
            traceback.print_exc()
            why = "session owner error %s: %s" % (type(e).__name__, str(e)[:200])
            self.state, self.why = "ended", why
            event("RESIDENT GAP: %s" % why)
            self.end_all("NOTRUN", "the session failed (%s): resubmit; the phone parks" % why)
            self.stop_boot("error")
            self.save()
            return 1

    def serve_loop(self):
        self.state = "running"
        event("RESIDENT SESSION: %s start, kernel %s, floor %d mV (load-compensated, r_int_mohm %d; raw vcell hard limit %d mV), idle park %d s, profiles %s" % (
            self.sid, self.std_sha[:12], self.cfg["floor_mv"], self.cfg["r_int_mohm"], self.cfg["vcell_hard_mv"], self.cfg["idle_park_s"], sorted(self.profiles)))
        while True:
            self.ingest()
            dead = self.boot_dead(self.pump())
            sf = self.ph.safety_fail(self.boot[1]) if dead else None
            if sf:   # RULES H9: the safety monitor stopped the boot (xrun parked the phone): the queue is kept
                rt.hot_park(self, sf[1], event, lambda j: jsave(p("jobs", j["id"], "job.json"), j), lambda j, ln: job_notify(self.ph, j, ln))
                break
            if dead:
                self.crash(dead)
                break
            hot = self.therm.live(self.ph.now()) if self.boot else ""
            if hot:   # S1/S3 at a boundary: park, keep the queue, no boot until S2
                rt.hot_park(self, hot, event, lambda j: jsave(p("jobs", j["id"], "job.json"), j), lambda j, ln: job_notify(self.ph, j, ln))
                break
            self.save()
            er = jload(p("end-request.json"))
            if er and er.get("at", 0) < self.started:   # written for an earlier session (2026-11-11: a stale request ended a new one at once)
                os.unlink(p("end-request.json"))
                event("RESIDENT GAP: end request from %s (before this session started) ignored: %s" % (
                    time.strftime("%H:%M:%SZ", time.gmtime(er.get("at", 0))), er.get("reason", "")))
                er = None
            if er:
                os.unlink(p("end-request.json"))
                self.state, self.why = "ended", "end request: " + er.get("reason", "")
                self.end_all("NOTRUN", "session ended by request (%s): resubmit" % er.get("reason", ""))
                self.stop_boot("end")
                break
            now = self.ph.now()
            act = rc.decide(now, self.cfg, self.boot[0] if self.boot else None, list(self.pending.values()), self.bat, self.boot_up_at,
                            self.idle_since, self.ph.lock_left(), self.std_kernel, self.std_sha, self.boot_batch, jload(p("reserve.json"), []) or [])
            if act[0] == "park":
                self.state, self.why = "ended", "park: " + act[1]
                event("RESIDENT PARK: %s" % act[1])
                self.end_all("NOTRUN", "session parked (%s): resubmit" % act[1])
                self.stop_boot("park")
                break
            if act[0] == "end":
                self.state, self.why = "ended", act[1]
                event("RESIDENT END: %s" % act[1])
                if self.pending:
                    event("RESIDENT REQUEUE: %d job(s) stay queued; a new session is started (it queues behind the lock)" % len(self.pending))
                    for j in self.pending.values():
                        job_notify(self.ph, j, "RESIDENT JOB REQUEUED id=%s: session %s ended (%s); it runs in the next session" % (j["id"], self.sid, act[1][:160]))
                self.stop_boot("end")
                self.save()   # state=ended on disk first: the new start must not see this session as live
                if self.pending:
                    self.ph.restart_session()
                break
            if act[0] == "wait":
                if self.state != "wait:" + act[1]:
                    event("RESIDENT WAIT: %s" % act[1])
                self.state = "wait:" + act[1]
                self.ph.sleep(self.tick)
                continue
            if act[0] == "reboot":
                _, spec, batch, left = act
                for jid, w in left:
                    event("RESIDENT BATCH: %s waits for a later boot (%s)" % (jid, w))
                if not self.do_boot(spec, batch):
                    break
                continue
            if act[0] == "run":
                self.state = "job:" + act[1]["id"]
                if not self.run_job(act[1]):
                    break
        self.therm.parked(self.ph.now(), self.why)   # S2 clock (phone-lock parks the phone after this owner exits)
        self.save()
        return 0


class JobRun:
    """One job: its log slice, its oracles (each new chunk scanned once), its steps, its result. Session.run_job's body."""

    def __init__(self, s, job):
        self.s, self.job, self.ph = s, job, s.ph
        self.jd = p("jobs", job["id"])
        self.h = s.boot[1]
        self.label = "rj-" + job["id"]
        self.watch = set(job["expect"]) | {v for op, v in job["steps"] if op == "wait-for"}
        self.seen, self.forbidden = set(), []
        # a quiet program (bt-client scans 180 s without output) must not trip devsh's 10 s idle read timeout (live session 4 bt-6537:
        # 'devsh: connection/io error: timed out'); the job's --max is the limit, enforced by check()
        self.env = dict(os.environ, PIXOS_DEVD_TIMEOUT=str(job["max_s"]))
        self.hostenv = {k: v for k, v in os.environ.items() if k != "PHONE_LOCK_TOKEN"}
        self.t0 = None
        self.steplog = None
        self.last_out = ""

    def feed(self, t):   # each chunk is scanned once (whole lines: read_new and step outputs never split a line)
        if not t:
            return
        for rx in self.job["expect_absent"]:
            m = re.search(rx, t, re.M)
            if m and not self.forbidden:
                self.forbidden.append("forbidden line %r: %s" % (rx, m.group(0)[:160]))
        for rx in self.watch - self.seen:
            if re.search(rx, t, re.M):
                self.seen.add(rx)

    def check(self):
        s, job = self.s, self.job
        dead = s.boot_dead(s.pump())
        sf = self.ph.safety_fail(self.h) if dead else None
        if sf:   # RULES H9: a safety stop ends the job CUT (not a crash of the kernel); the loop then keeps the queue
            return "CUT", sf[1]
        if dead:
            return "CRASH", dead
        if self.forbidden:
            return "FAIL", self.forbidden[0]
        why = s.bat.cut() or s.therm.live(self.ph.now())   # S1/S3 stop the job; the loop then parks (hot_park)
        if why:
            return "CUT", why
        if self.ph.now() - self.t0 > job["max_s"]:
            return "CUT", "job max %d s reached" % job["max_s"]
        if os.path.exists(os.path.join(self.jd, "cancel")):
            return "CUT", "cancelled while running"
        return None

    def proc(self, argv, cwd=None, envx=None, check=None):
        pr = self.ph.spawn(argv, cwd=cwd, env=envx or self.env)
        bad = None
        while pr.poll() is None:
            bad = (check or self.check)()
            if bad:
                self.ph.stop_proc(pr)
                break
            self.ph.sleep(1)
        o = pr.stdout.read() if pr.stdout else ""
        self.last_out = o
        self.feed(o)
        self.steplog.write(o)
        return (None, bad) if bad else (pr.returncode, None)

    def wait_until(self, done):
        bad = None
        while not bad and not done():
            bad = self.check()
            self.ph.sleep(1)
        return bad

    def step(self, i, op, val):
        """(rc, bad) of one phone step."""
        hot = self.ph.hot_argv
        if op == "push":
            src, _, dst = val.rpartition(":")
            return self.proc(hot(["push", src, dst]))
        if op == "restart":
            return self.proc(hot(["restart", val]))
        if op == "run":
            return self.proc(hot(["run"] + val.split(" ")))
        if op == "hot":
            return self.proc(hot(shlex.split(val)))
        if op == "pull":
            src, _, dst = val.partition(":")
            keep = os.path.join(self.jd, "pulled", os.path.basename(src))
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            r, bad = self.proc(hot(["pull", src, keep]))
            if r == 0 and dst:
                shutil.copy2(keep, dst)
            return r, bad
        if op == "sleep":
            t_end = self.ph.now() + float(val)
            return 0, self.wait_until(lambda: self.ph.now() >= t_end)
        if op == "wait-for":
            return 0, self.wait_until(lambda: val in self.seen)
        raise Fail("unknown step %s" % op)

    def steps(self):
        """Run the phone steps; returns (result, why, bad, host steps)."""
        host = []
        for i, (op, val) in enumerate(self.job["steps"]):
            self.steplog.write("--- step %d %s %s\n" % (i, op, val))
            if op == "host":
                host.append(val)
                continue
            r, bad = self.step(i, op, val)
            if not bad and r != 0 and HOST_INFRA_RX.search(self.last_out or ""):
                # devsh refused BEFORE any connect (its phone-lock check timed out under host load, ALL-ON 66d5 2026-11-11 at load1 37):
                # the phone never saw the step, so one retry is safe; a second one is host infra, not a phone result
                m = HOST_INFRA_RX.search(self.last_out).group(0)
                event("RESIDENT GAP: job %s step %d: host infra (%s): retry once in 10 s" % (self.job["id"], i, m))
                self.steplog.write("RESIDENT GAP: host infra (%s): retry once in 10 s\n" % m)
                bad = self.wait_until(lambda t_end=self.ph.now() + 10: self.ph.now() >= t_end)
                if not bad:
                    r, bad = self.step(i, op, val)
                    if not bad and r != 0 and HOST_INFRA_RX.search(self.last_out or ""):
                        return "NOTRUN", "host infra twice at step %d (%s): not a phone result, resubmit" % (i, m), None, host
            if bad:
                return "PASS", "", bad, host
            if r != 0:
                return "FAIL", "step %d (%s %s) rc %s" % (i, op, val[:120], r), None, host
        return "PASS", "all steps ok", None, host

    def oracles(self):
        missing = list(self.job["expect"])
        bad = None
        while missing and not bad:
            missing = [rx for rx in missing if rx not in self.seen]
            if missing:
                bad = self.check()
                self.ph.sleep(1)
        if bad and missing and bad[0] == "CUT" and "job max" in bad[1]:
            return ("FAIL", "expected line(s) missing after %d s: %s" % (self.job["max_s"], missing))
        return bad or self.check()

    def host_steps(self, host, d, result, why):
        """Host steps run after the log cut, so the job's own check() (max, forbidden line, battery) no longer applies: a job that ended on
        its max or a forbidden line killed every host step on the first poll, before it printed a line (ALL-ON a53c 2026-11-11). Each host
        step gets its own limit cfg host_step_max_s; a cut or failing host step is a GAP line and fails a passing job."""
        for i, val in enumerate(host):
            self.steplog.write("--- host %d %s\n" % (i, val))
            t_end = self.ph.now() + self.s.cfg["host_step_max_s"]
            lim = lambda: ("CUT", "host step %d past host_step_max_s %d s" % (i, self.s.cfg["host_step_max_s"])) if self.ph.now() > t_end else (  # noqa: E731
                ("CUT", "host step %d cancelled" % i) if os.path.exists(os.path.join(self.jd, "cancel")) else None)
            r, bad = self.proc(["bash", "-c", val.replace("{dir}", shlex.quote(d))], cwd=self.job.get("cwd"), envx=self.hostenv, check=lim)
            if bad:
                event("RESIDENT GAP: job %s: %s (stopped)" % (self.job["id"], bad[1]))
                self.steplog.write("RESIDENT GAP: %s (stopped)\n" % bad[1])
                if result == "PASS":
                    result, why = "FAIL", bad[1]
            elif r != 0 and result == "PASS":
                result, why = "FAIL", "host step %d rc %s" % (i, r)
        return result, why

    def run(self):
        s, job = self.s, self.job
        job["state"] = "running"
        jsave(os.path.join(self.jd, "job.json"), job)
        line = "RESIDENT JOB START id=%s stream=%s boot=%s max=%ss" % (job["id"], job["stream"], self.h["label"], job["max_s"])
        event(line)
        job_notify(self.ph, job, line)   # a job that needs a person (a finger tap) starts on this line
        s.boot_batch.discard(job["id"])
        early = s.boot_feed.pop(job["id"], None) if s.boot_cut_begun else None
        if early is None:
            s.boot_feed.pop(job["id"], None)
            self.ph.cut(self.h, "begin", self.label)
        self.t0 = self.ph.now()
        s.buf = self.feed
        for chunk in early or []:   # the boot so far (jobs the boot was made for), scanned once
            self.feed(chunk)
        self.steplog = open(os.path.join(self.jd, "steps.log"), "a")
        result, why, bad, host = self.steps()
        if not bad and result == "PASS":
            bad = self.oracles()
        if bad:
            result, why = bad
        s.pump()
        d, cutout = self.ph.cut(self.h, "end", self.label)
        if not d:
            event("RESIDENT GAP: job %s: live-cut end made no slice: %s" % (job["id"], cutout.strip()[-200:]))
        else:
            result, why = self.host_steps(host, d, result, why)
        self.steplog.close()
        s.buf = None
        s.idle_since = self.ph.now()
        crashed = bad and bad[0] == "CRASH"
        s.finish(job, result, ("session crashed during the job: " if crashed else "") + why, d)
        if crashed:
            s.crash(bad[1])
            return False
        return True
