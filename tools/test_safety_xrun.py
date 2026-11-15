#!/usr/bin/env python3
"""tools/xrun end to end with the REAL tools/safety-check (RULES H9, docs/SAFETY-CHECKLIST.md) on a FAKE phone. Host only: no adb, no fastboot, no phone.

The real tools/xrun, tools/xrun_report.py, tools/safety-check and tools/safety_lib.py run unchanged (XRUN_SAFETY is NOT set). Only the phone side is fake:
XRUN_BUILD / XRUN_FBOOT / XRUN_TO_FASTBOOT / XRUN_USB_PORT / XRUN_RUN_PREFLIGHT / XRUN_PHONE seams, a fake USB sysfs (XRUN_SYSFS_USB), the
fake adb / fastboot / lsusb of tools/sim/fakephone.py first on PATH (`safety-check fastboot` and xrun's probe call them), PHONE_LOCK_T3 = a fake t3.
The fake fboot returns at once and starts a background writer that appends "<epoch>\\t<line>" rows to the run's usbnet.log and flips the fake sysfs
(18d1 away, 1209 gadget; at a reset 1209 away, 18d1 back), the way the phone's console and USB gadget would.
Every xrun runs inside `unshare -r -n -m` (user + net + mount namespace): the UDP 5140 listener of the run binds in a private network namespace, so no
real listener or real run on this host is disturbed. Without unshare the tests FAIL (no silent skip).

  case 1  hot log (46.1 C row)        -> SAFETY FAIL S1, the SAFETY branch parks via to-fastboot, report SAFETY STOP, RESULT FAIL, state hot
  case 2  rows without temp= (500 s)  -> SAFETY FAIL S3, parked via to-fastboot
  case 3  silent boot (200 s)         -> SAFETY FAIL S6, notify at once (fake t3), park fails, HUMAN-NEEDED in the report / run.json / human-needed.log
  case 4  cold start, 31 C rows, reset -> DONE path, report SAFETY: RESULT: PASS safety-check pre, no SAFETY STOP, SAFETY MONITOR END
  case 5  boot line over the kernel limit -> refused before the phone lock: SAFETY FAIL S7, exit 2, no to-fastboot / fboot call, no lock entry

Seams used on purpose: SAFETY_TBOOT_BACKDATE (safety-check monitor: the boot started S s earlier; sandbox only) for cases 2 and 3, and a fake kernel
that carries the patch 0333 early-watchdog string (S5 PASS whatever trunk holds, so the cases do not change when the early watchdog lands on trunk).
  python3 tools/test_safety_xrun.py     (one RESULT: PASS|FAIL safety-xrun tests=N failures=N line)
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "sim"))
import fakephone  # noqa: E402

ART = os.path.join(tempfile.gettempdir(), "pixos-sc-tests")   # a few small files per case (fakes, one run dir); removed by tearDown on a pass
HOT_ROW = "SWEET CHG MON: t=330s current=-816562 uA vcell=3768 mV soc=89.96 % temp=46.1 C"
P3_ROW = "SWEET CHG P3: t=30s current=-432500 uA vcell=3777 mV soc=70.54 % cnfg09=0xa2 ilim=875 mA int_ok=0x5d aicl_ok=0"
BOOT_ARGS = "sweetusb=2 sweetchgi2c=1 sweetchgicl=900 sweetwdt=60 sweetremote=1"
BLL = int(re.search(r"^BOOT_LINE_LENGTH = (\d+)", open(os.path.join(HERE, "mkpayload.py")).read(), re.M).group(1))   # the tree's boot line field (1024, or 2048 with xnu 0320)
OVER = BLL + 76   # a boot line over this kernel's limit (case 5)
FAKE_KERNEL = (b"fake kernel\0SWEET_BOOT_LINE_LENGTH=" + str(BLL).encode() + b"\0 SWEET CHG MON: ... temp=%s%d.%d C ...\0 SWEET WDT: arm/pet source = timer (no USB needed)\n\0")   # S5 PASS

# ---- the fake phone side ---------------------------------------------------------------------------------------------------------------------
FAKE_BUILD = """#!/bin/bash
# XRUN_BUILD: the image files the way tools/test_sim_seams.sh's fake writes them, the boot-args line of the case; args carry --adt $D/A.adt = the run dir
echo "build" >>"{c}/calls.log"
mkdir -p shim/out; echo img >shim/out/shim-boot-avb.img; echo "{{}}" >shim/out/shim-boot-avb.img.meta.json
prev=""; for a in "$@"; do [ "$prev" != --adt ] || rd=$(dirname "$a"); prev=$a; done
[ ! -s "{c}/preseed.tsv" ] || [ -z "$rd" ] || sed "s/^/$(date +%s).000\\t/" "{c}/preseed.tsv" >>"$rd/usbnet.log"
printf 'boot-args: %s\\n' "$(cat "{c}/bootline.txt")"
"""
FAKE_FBOOT = """#!/bin/bash
# XRUN_FBOOT: returns at once; the writer plays the phone's console + USB gadget for the run dir of the image path (the last argument)
echo "fboot $*" >>"{c}/calls.log"
rd=$(dirname "${{@: -1}}"); : >"{c}/booted"
nohup python3 -I "{c}/writer.py" "$rd" >"{c}/writer.out" 2>&1 </dev/null &
echo $! >"{c}/writer.pid"
exit 0
"""
FAKE_TOFB = """#!/bin/bash
# XRUN_TO_FASTBOOT: before the boot it just succeeds; the park after the boot rests the phone in fastboot (18d1) or FAILS when fail_park exists
echo "to-fastboot $*" >>"{c}/calls.log"
if [ -f "{c}/booted" ]; then
	[ ! -f "{c}/fail_park" ] || {{ echo "to-fastboot: FAKE park failure"; exit 1; }}
	echo 18d1 >"{c}/sysfs/1-1/idVendor"; echo 4ee0 >"{c}/sysfs/1-1/idProduct"
fi
exit 0
"""
FAKE_USBPORT = """#!/bin/bash
echo "usb-port $*" >>"{c}/calls.log"
echo "usb-port: 1-1 speed=5000 product=4ee0"; echo "RESULT: PASS usbport 1-1 speed=5000 sweetusb3=1"
"""
FAKE_PREFLIGHT = """#!/bin/bash
echo "preflight $*" >>"{c}/calls.log"
case " $* " in *" --userdata-check "*) echo "RESULT: PASS userdata ios" ;; esac
exit 0
"""
FAKE_PHONE = """#!/bin/bash
# XRUN_PHONE (recover_recovery): the phone is absent, so no goto
echo "phone $*" >>"{c}/calls.log"
echo "RESULT: PASS state=absent target=none steps=0"
"""
FAKE_T3 = """#!/bin/bash
# PHONE_LOCK_T3: records its argv, one line per call
echo "$*" >>"{c}/t3.log"
"""
WRITER = """import json, os, sys, time
c = {c!r}
sc = json.load(open(os.path.join(c, "scenario.json")))
rd = sys.argv[1]
def sysfs(vid, pid):
    with open(os.path.join(c, "sysfs", "1-1", "idVendor"), "w") as f: f.write(vid + "\\n")
    with open(os.path.join(c, "sysfs", "1-1", "idProduct"), "w") as f: f.write(pid + "\\n")
def stopped():
    return os.path.exists(os.path.join(rd, "safety-end")) or os.path.exists(os.path.join(rd, "stop"))
sysfs("1209", "0001")   # the iOS gadget replaces the bootloader on the bus
t_end = time.time() + 90
log = os.path.join(rd, "usbnet.log")
for _ in range(sc.get("loops", 1)):
    for line in sc["rows"]:
        if stopped() or time.time() > t_end: sys.exit(0)
        with open(log, "a") as f: f.write("%.3f\\t%s\\n" % (time.time(), line))
        time.sleep(sc.get("interval", 0.4))
if sc.get("reset"):
    sysfs("18d1", "4ee0")   # the self-reset: the gadget is gone, the bootloader is back
while not stopped() and time.time() < t_end:
    time.sleep(0.2)
"""


def slurp(path):
    with open(path) as f:
        return f.read()


def jload(path):
    with open(path) as f:
        return json.load(f)


def write_exec(path, text, mode=0o755):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


class SafetyXrun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(ART, exist_ok=True)
        r = subprocess.run(["unshare", "-r", "-n", "-m", "true"], capture_output=True, text=True)
        if r.returncode != 0:   # no silent skip: without the namespaces the run's UDP listener would be a real one
            raise AssertionError("unshare -r -n -m is not available here (rc=%d: %s): safety-xrun tests cannot run isolated; FAIL, not skip" % (r.returncode, r.stderr.strip()))

    def scratch_root(self, c):
        """A private copy of tools/ as its own git checkout: the fake build writes shim/out and xrun writes logs/runs THERE, never into the live worktree
        (a gate building in this worktree at the same time must not see a fake image). safety-check judges that tree (S8 guards, S9 history: no marks)."""
        root = os.path.join(c, "root")
        shutil.copytree(HERE, os.path.join(root, "tools"), ignore=shutil.ignore_patterns("__pycache__", "third_party", "gate-image", "*.pyc"))
        os.makedirs(os.path.join(root, "xnu", "patches"))
        genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["git", "init", "-q", "-b", "trunk"], ["git", "commit", "-q", "--allow-empty", "-m", "scratch root of the safety-xrun host test"]):
            subprocess.run(cmd, cwd=root, env=genv, check=True, capture_output=True)
        return root

    # ---- one case ---------------------------------------------------------------------------------------------------------------------------
    def run_case(self, name, rows, bootline=BOOT_ARGS, preseed=(), reset=False, backdate=None, fail_park=False, interval=0.4, loops=1, timeout=100):
        """Build the sandbox, run the real xrun in it, return a namespace of everything the assertions read."""
        c = tempfile.mkdtemp(prefix=name + "-", dir=ART)
        self.addCleanup(self.kill_writer, c)
        for d in ("tmp", "art/safety", "owners", "bin", "fp"):
            os.makedirs(os.path.join(c, d))
        fp = fakephone.FakePhone(os.path.join(c, "fp"))   # fastboot state, product bluejay: the fake adb / fastboot / lsusb shims
        fp.write_shims(os.path.join(c, "fp", "bin"))
        os.symlink(fp.fake_sysfs(os.path.join(c, "fp", "sysfs")), os.path.join(c, "sysfs"))   # one path for the shell fakes and the writer
        for n, text in (("build", FAKE_BUILD), ("fboot", FAKE_FBOOT), ("tofb", FAKE_TOFB), ("usbport", FAKE_USBPORT), ("preflight", FAKE_PREFLIGHT),
                        ("phone", FAKE_PHONE), ("t3", FAKE_T3)):
            write_exec(os.path.join(c, "bin", n), text.format(c=c))
        write_exec(os.path.join(c, "writer.py"), WRITER.format(c=c), 0o644)
        with open(os.path.join(c, "scenario.json"), "w") as f:
            json.dump({"rows": list(rows), "reset": reset, "interval": interval, "loops": loops}, f)
        with open(os.path.join(c, "bootline.txt"), "w") as f:
            f.write(bootline)
        with open(os.path.join(c, "preseed.tsv"), "w") as f:
            f.write("".join(r + "\n" for r in preseed))
        with open(os.path.join(c, "kernel"), "wb") as f:
            f.write(FAKE_KERNEL)
        open(os.path.join(c, "adt"), "w").close()
        if fail_park:
            open(os.path.join(c, "fail_park"), "w").close()
        now = time.time()   # seeded cool and parked 2 h ago (S2 / S4 pass in pre and in `safety-check fastboot`)
        with open(os.path.join(c, "art", "safety", "state.json"), "w") as f:
            json.dump({"temp_c": 30.0, "temp_at": now - 7200, "end_at": now - 7200, "soc": 80.0, "vcell": 3900, "batt_at": now - 7200}, f)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XRUN_", "SAFETY_", "PHONE_", "PIXOS_", "T3CODE_")) and k != "PREFLIGHT_GATE_WAIVER"}
        env.update(
            PATH=os.path.join(c, "fp", "bin") + ":" + env.get("PATH", ""), TMPDIR=os.path.join(c, "tmp"), ARTIFACTS=os.path.join(c, "art"),
            PHONE_LOCK_DIR=os.path.join(c, "tmp", "lock"), PHONE_LOCK_T3=os.path.join(c, "bin", "t3"), PIXOS_OWNERS_DIR=os.path.join(c, "owners"),
            T3CODE_THREAD_ID="test-thread", PIXOS_SLEEP_SCALE="0", XRUN_SYSFS_USB=os.path.join(c, "sysfs"),
            XRUN_BUILD=os.path.join(c, "bin", "build"), XRUN_FBOOT=os.path.join(c, "bin", "fboot"), XRUN_TO_FASTBOOT=os.path.join(c, "bin", "tofb"),
            XRUN_USB_PORT=os.path.join(c, "bin", "usbport"), XRUN_RUN_PREFLIGHT=os.path.join(c, "bin", "preflight"), XRUN_PHONE=os.path.join(c, "bin", "phone"),
            XRUN_NO_RUNPLAN="safety-xrun host test, not a run", PREFLIGHT_GATE_WAIVER="safety-xrun host test, not a run")
        if backdate:
            env["SAFETY_TBOOT_BACKDATE"] = str(backdate)
        label = "sc-" + name
        root = self.scratch_root(c)
        self.addCleanup(shutil.rmtree, c, True)
        argv = ["unshare", "-r", "-n", "-m", os.path.join(root, "tools", "xrun"), "--kernel", os.path.join(c, "kernel"), "--adt", os.path.join(c, "adt"), "--no-camera",
                "--duration", "60", "--restart", "60", "--label", label]
        t0 = time.time()
        p = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, _ = p.communicate()
            self.fail("%s: xrun did not end within %d s; output tail:\n%s" % (name, timeout, out[-1500:]))
        r = type("R", (), {})()
        r.c, r.rc, r.out, r.secs, r.env = c, p.returncode, out, time.time() - t0, env
        new = sorted(glob.glob(os.path.join(root, "logs", "runs", "*-" + label)))
        r.run = None
        if new:   # move the run dir out of the worktree: the assertions read the copy under sc-tests
            r.run = os.path.join(c, "run")
            shutil.move(new[-1], r.run)
        with open(os.path.join(c, "xrun.out"), "w") as f:
            f.write(out)
        r.state = jload(os.path.join(c, "art", "safety", "state.json"))
        return r

    def kill_writer(self, c):
        try:
            pid = int(slurp(os.path.join(c, "writer.pid")))
            os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):   # lint-silent: expected no writer was started (refused before the boot) or it ended by itself
            pass

    # ---- helpers for the assertions ------------------------------------------------------------------------------------------------------------
    def text(self, r, *parts):
        with open(os.path.join(r.run, *parts), errors="replace") as f:
            return f.read()

    def lines(self, r, name):
        try:
            with open(os.path.join(r.c, name)) as f:
                return f.read().splitlines()
        except FileNotFoundError:   # lint-silent: expected a fake that was never called wrote no log: no lines
            return []

    def common(self, r, name):
        """Assertions every case shares: the run ended through the real files, nothing but reads reached the (fake) fastboot, the worktree is clean."""
        self.assertIsNotNone(r.run, "%s: no run dir was written; output:\n%s" % (name, r.out[-1500:]))
        self.assertNotIn("SIM SEAM ACTIVE: XRUN_SAFETY", r.out, "the real safety-check must run (XRUN_SAFETY unset)")
        fb = self.lines(r, "fp/fastboot.log")
        self.assertTrue(fb and all(x.startswith("getvar ") for x in fb), "fastboot got a non-read verb: %r" % fb)   # rule 1: no flash/erase/boot ever
        self.assertFalse(os.path.exists(os.path.join(ROOT, "shim", "out", "shim-boot-avb.img.safety")), "the test wrote into the live worktree")

    def assert_boot_then_park(self, r, name, stop_re):
        """Cases 1 and 2: the monitor stopped the boot, the SAFETY branch parked through to-fastboot, the report says FAIL."""
        self.common(r, name)
        self.assertEqual(r.rc, 1, r.out[-1500:])   # FAIL = exit 1
        mon = self.text(r, "safety-monitor.log")
        self.assertRegex(mon, r"(?m)^%s" % stop_re)
        self.assertRegex(self.text(r, "safety-stop"), "^%s" % stop_re)
        rep = self.text(r, "report.txt")
        self.assertRegex(rep, r"(?m)^SAFETY STOP: %s" % stop_re)
        self.assertRegex(rep, r"(?m)^RESULT: FAIL ")
        self.assertRegex(rep, r"(?m)^SAFETY: RESULT: PASS safety-check pre ")
        self.assertNotIn("HUMAN-NEEDED", rep)
        run = jload(os.path.join(r.run, "run.json"))
        self.assertNotIn("human_needed", run)
        self.assertEqual(run["safety"], "required")
        self.assertRegex(run["safety_fastboot"], r"^RESULT: PASS safety-check fastboot")
        self.assertTrue(run["safety_monitor_pid"])
        self.assertRegex(run["phone"], r"^back in fastboot \(safety stop")
        tofb = [x for x in self.lines(r, "calls.log") if x.startswith("to-fastboot")]
        self.assertEqual(tofb, ["to-fastboot 200", "to-fastboot 180"], "pre-boot to-fastboot, then exactly one park")
        self.assertEqual(self.lines(r, "t3.log"), [], "a park that worked must not notify")
        self.assertFalse(os.path.exists(os.path.join(r.c, "art", "safety", "human-needed.log")))
        self.assertEqual(slurp(os.path.join(r.c, "sysfs", "1-1", "idVendor")).strip(), "18d1", "the phone rests on the bus as the bootloader")
        return run

    # ---- case 1 -----------------------------------------------------------------------------------------------------------------------------
    def test_1_hot_log_parks(self):
        r = self.run_case("hot", [P3_ROW, "SWEET: console line", "SWEET CHG MON: t=300s current=-800000 uA vcell=3770 mV soc=89.90 % temp=41.2 C", HOT_ROW,
                                  "SWEET: after the hot row"], loops=40)
        run = self.assert_boot_then_park(r, "hot", r"SAFETY FAIL S1: battery temp 46\.1 C >= 45\.0 C")
        self.assertIn("SAFETY FAIL S1", self.text(r, "xrun.log"))
        self.assertTrue(r.state["hot"] is True, r.state)
        self.assertEqual(r.state["temp_c"], 46.1)
        self.assertRegex(r.state["hot_why"], r"^SAFETY FAIL S1")
        self.assertRegex(r.state["end_why"], r"^SAFETY FAIL S1")   # the park clock starts at the stop
        self.assertGreater(r.state["end_at"], time.time() - 120)
        self.assertEqual(run["restart"], "60")

    # ---- case 2 -----------------------------------------------------------------------------------------------------------------------------
    def test_2_missing_temp_parks(self):
        # the monitor starts BEFORE fboot, and its S6 first-row rule (180 s) is checked before S3 (480 s): with the 500 s backdate the first P3 row must already be in
        # usbnet.log when the monitor starts, else it stops with S6 first (the preseed is written by the fake build, into the run dir, before the listener starts)
        r = self.run_case("notemp", [P3_ROW] * 3, preseed=[P3_ROW], backdate=500, loops=40)
        run = self.assert_boot_then_park(r, "notemp", r"SAFETY FAIL S3: no temp= row within 480 s of the fastboot boot")
        self.assertIn("SIM SEAM ACTIVE: SAFETY_TBOOT_BACKDATE=500", self.text(r, "safety-monitor.log"))
        self.assertTrue(r.state["hot"] is True, r.state)   # S3 = treated as hot, never assumed cool
        self.assertRegex(r.state["hot_why"], r"^SAFETY FAIL S3")
        self.assertEqual(r.state["temp_c"], 30.0, "a missing temperature must not write a temperature (the seed stays)")
        self.assertEqual(run["safety"], "required")

    # ---- case 3 -----------------------------------------------------------------------------------------------------------------------------
    def test_3_silent_boot_is_human_needed(self):
        r = self.run_case("silent", [], backdate=200, fail_park=True)
        self.common(r, "silent")
        self.assertEqual(r.rc, 1, r.out[-1500:])
        stop_re = r"SAFETY FAIL S6: no USB log within 180 s of the fastboot boot"
        self.assertRegex(self.text(r, "safety-monitor.log"), r"(?m)^%s" % stop_re)
        rep = self.text(r, "report.txt")
        self.assertRegex(rep, r"(?m)^SAFETY STOP: %s" % stop_re)
        res = re.search(r"(?m)^RESULT: .*$", rep).group(0)
        self.assertTrue(res.startswith("RESULT: FAIL ") and res.endswith(" HUMAN-NEEDED"), res)
        self.assertRegex(rep, r"(?m)^HUMAN-NEEDED: SAFETY FAIL S6.*\(phone may need Power\+VolDown\)")
        run = jload(os.path.join(r.run, "run.json"))
        self.assertRegex(run["human_needed"], r"^SAFETY FAIL S6")
        self.assertRegex(run["phone"], r"^safety stop: park FAILED \(to-fastboot")
        # notify at once: `t3 thread send test-thread SAFETY HUMAN-NEEDED: <label>: <why> (run dir ...)`, exactly one send (the failed park must not send a second one)
        t3 = self.lines(r, "t3.log")
        self.assertEqual(len(t3), 1, t3)
        self.assertRegex(t3[0], r"^thread send test-thread SAFETY HUMAN-NEEDED: sc-silent: SAFETY FAIL S6: no USB log within 180 s .*\(run dir .*-sc-silent\)$")
        self.assertRegex(r.out, r"(?m)^NOTIFY ok thread=test-thread")
        hn = self.text(r, "xrun.log")
        self.assertIn("NOTIFY ok thread=test-thread", hn)
        log = os.path.join(r.c, "art", "safety", "human-needed.log")
        self.assertTrue(os.path.exists(log))
        hl = slurp(log).splitlines()
        self.assertEqual(len(hl), 1, hl)
        self.assertRegex(hl[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\tsc-silent: SAFETY FAIL S6")
        self.assertEqual([x for x in self.lines(r, "calls.log") if x.startswith("to-fastboot")], ["to-fastboot 200", "to-fastboot 180"])
        self.assertIn("phone state", self.lines(r, "calls.log"))   # recover_recovery asked the phone (XRUN_PHONE: absent) after the park failed
        self.assertNotIn("goto", " ".join(self.lines(r, "calls.log")))
        self.assertRegex(r.state["end_why"], r"^SAFETY FAIL S6")

    # ---- case 4 -----------------------------------------------------------------------------------------------------------------------------
    def test_4_cold_start_passes(self):
        rows = ["SWEET CHG MON: t=%ds current=-300000 uA vcell=3800 mV soc=80.00 %% temp=31.0 C" % (330 + 10 * i) for i in range(8)]
        r = self.run_case("cold", rows, reset=True, interval=0.5)
        self.common(r, "cold")
        rep = self.text(r, "report.txt")
        self.assertRegex(rep, r"(?m)^SAFETY: RESULT: PASS safety-check pre ")
        self.assertNotIn("SAFETY STOP", rep)
        self.assertNotIn("HUMAN-NEEDED", rep)
        self.assertNotIn("SAFETY FAIL", rep)
        mon = self.text(r, "safety-monitor.log")
        self.assertRegex(mon, r"(?m)^SAFETY MONITOR END: phone back on the bus as 18d1 \(reset\); last temp 31\.0 ")
        self.assertNotIn("SAFETY FAIL", mon)
        self.assertFalse(os.path.exists(os.path.join(r.run, "safety-stop")))
        run = jload(os.path.join(r.run, "run.json"))
        self.assertEqual(run["safety"], "required")
        self.assertRegex(run["safety_pre"], r"^RESULT: PASS safety-check pre sc-cold items=8")
        self.assertNotRegex(run["safety_pre"], r"S5 WARN")   # the fake kernel carries the early-watchdog string
        self.assertNotIn("safety_stop", run)
        self.assertTrue(run["usb_back"] and run["usb_back_id"].startswith("18d1"))   # the DONE path
        self.assertIn("SAFETY MONITOR END", self.text(r, "xrun.log"))   # xrun copies the monitor log into its own
        self.assertFalse(r.state.get("hot"), r.state)
        self.assertEqual(r.state["temp_c"], 31.0)
        self.assertRegex(r.state["end_why"], r"^normal end: ")
        self.assertEqual([x for x in self.lines(r, "calls.log") if x.startswith("to-fastboot")], ["to-fastboot 200"], "no park on a normal end")
        self.assertEqual(self.lines(r, "t3.log"), [])

    # ---- case 5 -----------------------------------------------------------------------------------------------------------------------------
    def test_5_overlong_boot_line_refused_before_the_lock(self):
        pad = "sweetpad=" + "x" * (OVER - len(BOOT_ARGS) - 1 - len("sweetpad="))
        line = BOOT_ARGS + " " + pad
        self.assertEqual(len(line.encode()), OVER)
        r = self.run_case("longline", [P3_ROW], bootline=line)
        self.assertEqual(r.rc, 2, r.out[-1500:])
        self.assertRegex(r.out, r"(?m)^SAFETY FAIL S7: boot line %d B > limit %d B of this kernel" % (OVER, BLL - 1))
        self.assertIsNotNone(r.run)
        self.assertIn("SAFETY FAIL S7", self.text(r, "xrun.log"))
        rep = self.text(r, "report.txt")
        self.assertRegex(rep, r"(?m)^RESULT: ERROR .*SAFETY FAIL S7")
        calls = self.lines(r, "calls.log")
        self.assertEqual([x for x in calls if x.startswith(("to-fastboot", "fboot"))], [], "the phone was touched: %r" % calls)
        self.assertFalse(os.path.exists(os.path.join(r.c, "booted")))
        self.assertFalse(os.path.exists(os.path.join(r.c, "writer.pid")))
        run = jload(os.path.join(r.run, "run.json"))
        self.assertNotIn("t_boot", run)
        self.assertNotIn("safety_pre", run)
        self.assertNotIn("safety_fastboot", run)
        self.assertNotIn("safety_monitor_pid", run)
        # the phone lock was never taken: no lock dir, no history, no .runs of the test PHONE_LOCK_DIR
        self.assertEqual(sorted(os.listdir(os.path.join(r.c, "tmp"))), [], "the test lock dir area holds entries: %r" % os.listdir(os.path.join(r.c, "tmp")))
        self.assertEqual(self.lines(r, "fp/fastboot.log"), [], "even `safety-check fastboot` must not have run")
        self.assertEqual(r.state["temp_c"], 30.0)   # the monitor never ran: the state is the seed


if __name__ == "__main__":
    res = unittest.main(exit=False).result
    print("RESULT: %s safety-xrun tests=%d failures=%d" % ("PASS" if res.wasSuccessful() else "FAIL", res.testsRun, len(res.failures) + len(res.errors)))
    sys.exit(0 if res.wasSuccessful() else 1)
