"""The fake phone + fake process of tools/test_resident.py (no phone, a fake clock); split out of it for the 800-line limit."""
import io, json, os, time


# ---------------------------------------------------------------- the fake phone
class FakeProc:
    def __init__(self, rc=0, out="", runs_for=0, ph=None):
        self.returncode, self.out, self.ph = None, out, ph
        self.end_at = ph.t + runs_for
        self.rc = rc
        self.stdout = io.StringIO(out)
        self.pid = 1

    def poll(self):
        if self.returncode is None and self.ph.t >= self.end_at:
            self.returncode = self.rc
        return self.returncode

    def wait(self):
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class FakePhone:
    def __init__(self, tmp, battery=lambda t: (3900, 100000), lock=7200, soc=None, temp=lambda t: 30.0, storage=30.0):
        self.temp, self.storage = temp, storage   # battery temp(t) per row (None = no row temp) and fastboot storage-temperature
        self.procs = []   # (argv, FakeProc) of every spawn
        self.soc = soc   # patch 0307 rows: 'soc=NN.NN %' after vcell, plus the P3 't=-0s (before the write)' row without soc at boot
        self.t = time.time()
        self.t0 = self.t
        self.tmp = tmp
        self.battery = battery
        self.lock = lock
        self.boots, self.notes, self.calls, self.parks, self.feed = [], [], [], [], []
        self.read_t = None
        self.dead = False
        self.restarted = 0
        self.envs = []
        self.cuts = {}
        self.fits = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.t - self.t0 > 200000:   # a hung loop (a mutant without a time limit): stop it; the case run counts it as killed
            raise RuntimeError("fake clock ceiling: the session loop did not end in 200000 fake seconds")

    def lock_left(self):
        return self.lock - (self.t - self.t0)

    def notify(self, thread, line):
        self.notes.append((thread, line))

    def storage_temp(self):
        return self.storage(self.t - self.t0) if callable(self.storage) else self.storage

    def shared_s2(self, now):   # xrun's S2 rule (safety_lib.s2_state); the fake phone has no safety state: it admits unless a case sets s2
        return self.s2(now - self.t0) if callable(getattr(self, "s2", None)) else (True, "", 0)

    def fit(self, spec, label, duration, profile):   # T-51: FIT_REFUSE(spec) -> why ('' = proven)
        self.fits.append((label, dict(spec["args"])))
        return self.fit_refuse(spec) if getattr(self, "fit_refuse", None) else ""

    def host_fail(self, h):   # T-51: xrun died in its host build (BUILD_FAIL(spec) true at boot), before fastboot
        return "RESULT: ERROR build-xnu-image failed: fake" if h.get("build_failed") and self.dead else ""

    def boot(self, spec, label, duration, profile):
        if getattr(self, "boot_refuse", None):
            raise self.boot_refuse
        self.boots.append((label, spec, profile))
        self.dead = False
        sp = self.safety_pre(spec) if getattr(self, "safety_pre", None) else None   # RULES H9: xrun's safety-check pre/fastboot refusal
        if sp:
            self.dead, self.boot_t, self.next_row = True, self.t + 10 ** 7, self.t + 10 ** 7
            return {"label": label, "t0": self.t, "safety": sp}
        if getattr(self, "build_fail", None) and self.build_fail(spec):
            self.dead, self.boot_t, self.next_row = True, self.t + 10 ** 7, self.t + 10 ** 7   # never booted (boot_started None), xrun gone
            return {"label": label, "t0": self.t, "build_failed": True}
        self.read_t = self.t
        self.boot_t = self.t
        self.next_row = self.t + 30
        if self.soc is not None:
            self.feed.append((self.t + 2, "SWEET CHG P3: t=-0s current=-418437 uA vcell=3827 mV (before the write)\n"))
        self.feed.append((self.t + 5, "SWEET STALL: armed: fault + reset when the usb-log drain thread does not run for 20 s (patch 0279)\n"))
        self.feed.append((self.t + 65, "SWEET STALL: usb-log drain max gap 12 ms in the last 60 s (limit 20 s, 3 ticks)\n"))
        if any(spec["args"].get(k) for k in ("sweetbootline",)):
            self.feed.append((self.t + 20, "SWEET BOOTLINE early boot line\n"))
        if profile.get("nav_at"):
            self.feed.append((self.t + profile["nav_at"], "SWEET NAV: done reps 1\n"))
        return {"label": label, "t0": self.t}

    def run_dir(self, h):
        return self.tmp

    def log_exists(self, h):
        return self.t - self.boot_t >= 10   # xrun makes usbnet.log a few seconds after the run dir

    def alive(self, h):
        if getattr(self, "safety_live", None) and not h.get("safety") and self.t >= self.boot_t + self.safety_live[0]:
            self.dead, h["safety"], self.safety_live = True, self.safety_live[1], None   # the monitor stopped this boot (one shot)
        return not self.dead

    def safety_fail(self, h):
        line = h.get("safety") if self.dead else None
        return (line.split()[2].rstrip(":"), line) if line else None

    def read_new(self, h):
        out = []
        while self.next_row <= self.t:
            mv, ua = self.battery(self.next_row - self.t0)
            tc = self.temp(self.next_row - self.t0) if self.temp else None   # S1/S3: temp= rows (patch 0307); None = no temp
            out.append("SWEET CHG MON: t=%ds current=%d uA vcell=%d mV%s%s\n" % (self.next_row - self.boot_t, ua, mv, " soc=%.2f %%" % self.soc if self.soc is not None else "",
                                                                             " temp=%.1f C" % tc if tc is not None else ""))
            self.next_row += 30
        keep = []
        for (t, line) in self.feed:
            (out if t <= self.t else keep).append(line if t <= self.t else (t, line))
        self.feed = keep
        return "".join(out)

    def boot_started(self, h):
        return self.boot_t + getattr(self, "build_delay", 0) if self.t >= self.boot_t + getattr(self, "build_delay", 0) else None

    def ready(self, h):
        if getattr(self, "ready_raises", False):
            raise RuntimeError("fake ping timeout")
        return self.t - self.boot_t - getattr(self, "build_delay", 0) >= 60

    def stop(self, h):
        self.calls.append(("stop", h["label"]))

    def crash_text(self, h):
        return "CRASH: panic (fake)\nmore"

    def park(self, why):
        self.parks.append(why)
        return "fastboot"

    def spawn(self, argv, cwd=None, env=None):
        pr = self._spawn(argv, cwd, env)
        self.procs.append((argv, pr))
        return pr

    def _spawn(self, argv, cwd=None, env=None):
        self.calls.append(("spawn", argv))
        self.envs.append(env or {})
        s = " ".join(argv)
        if "PANICME" in s:
            self.feed.append((self.t + 3, "panic(cpu 0 caller 0x0): fake\n"))
            return FakeProc(0, "", runs_for=30, ph=self)
        if "STALLME" in s:
            self.feed.append((self.t + 3, "SWEET STALL: the usb-log drain thread did not run for 20500 ms (limit 20 s, 5 ticks before); fake\n"))
            return FakeProc(0, "", runs_for=30, ph=self)
        if "MIRRORASSERT" in s:   # a mirrored userland program's own assert text (sweetnull): not a kernel panic
            self.feed.append((self.t + 2, "1794000000.000\t[   12.345] |backboardd| Assertion failed: sweetgl fake (x.c:1)\n"))
        if "SAYHELLO" in s:
            self.feed.append((self.t + 2, "SWEET HELLO from the phone\n"))
        if "FORBID" in s:
            self.feed.append((self.t + 2, "SWEET BAD thing\n"))
        if "LONG" in s:
            return FakeProc(0, "", runs_for=10000, ph=self)
        if "INFRA" in s and ("INFRAALWAYS" in s or not getattr(self, "infra_done", False)):
            self.infra_done = True   # devsh refused before any connect: its phone-lock check timed out (host load)
            return FakeProc(2, "devsh: cannot run /x/tools/phone-lock check: timed out after 30 seconds\n", ph=self)
        if "FAILSTEP" in s:
            return FakeProc(1, "RESULT: FAIL fake\n", ph=self)
        return FakeProc(0, "ok\n", runs_for=2, ph=self)

    def stop_proc(self, pr):
        pr.killed = True
        pr.wait()

    def hot_argv(self, args):
        return ["hot"] + args

    def cut(self, h, op, label):
        if op == "begin":
            if not self.log_exists(h):
                return None, "live-cut: no live session (no usbnet.log)"
            self.cuts[label] = self.t - getattr(self, "boot_t", self.t)
            return None, "live-cut: %s begins at byte 0 of usbnet.log" % label
        d = os.path.join(self.tmp, "cut-" + label)
        os.makedirs(d, exist_ok=True)
        return d, "LIVE CUT: " + d

    def restart_session(self):
        self.restarted += 1
        st = json.load(open(os.path.join(os.environ["RESIDENT_DIR"], "session.json")))
        self.restart_saw_state = st.get("state")
