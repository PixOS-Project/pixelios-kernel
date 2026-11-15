#!/usr/bin/env python3
"""Host tests of tools/safety-check + tools/safety_lib.py (docs/SAFETY-CHECKLIST.md, RULES H9). No phone, no real lock, no network.

    python3 tools/test_safety_check.py   -> RESULT: PASS safety-check tests=N failures=0
Fixtures: a temp git tree (tools/fboot.sh + tools/to-fastboot.sh guards, xnu/patches, a `trunk` branch), fake kernels with/without the patch
0307 / 0333 / 0176 strings, a temp $ARTIFACTS (state + phone.lock.runs), a fake fastboot (SAFETY_FASTBOOT seam, sandbox PHONE_LOCK_DIR) and a
fake t3 (PHONE_LOCK_T3). The xrun end-to-end cases on a fake phone are tools/test_safety_xrun.py; the mutants tools/test_safety_mutants.py.
"""
import warnings
import json, os, shutil, subprocess, sys, tempfile, time, unittest

if __name__ == "__main__" and sys.argv[1:2] in (["-h"], ["--help"]):   # T-7dff:help
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TOOL = os.environ.get("SAFETY_CHECK_TOOL") or os.path.join(HERE, "safety-check")   # the mutant runner points this at a mutated copy
if os.environ.get("SAFETY_LIB_DIR"):
    sys.path.insert(0, os.environ["SAFETY_LIB_DIR"])
import safety_lib as L  # noqa: E402

GOOD_LINE = "debug=0x8 sweetusb=2 sweetremote=1 sweetwdt=60 sweetchgi2c=1 sweetchgicl=900"
P3 = "[  226.101] SWEET CHG P3: t=30s current=-432500 uA vcell=3777 mV soc=70.54 % cnfg09=0xa2 ilim=875 mA int_ok=0x5d aicl_ok=0"
MON = "[  526.112] SWEET CHG MON: t=330s current=-1081562 uA vcell=3705 mV soc=66.97 %% temp=%s C"
P2 = ("[  196.004] SWEET CHG P2: fg[0x36] DEVNAME[0x21]=0x6200 VCell[0x09]=0xbd56 (3786 mV) Current[0x0a]=0xfbd2 (-334375 uA raw sign, driver negates) "
      "RepSOC[0x06]=0x4697 (70.58 %)")


def kernel(path, temp=True, wdt=False, tag=1024, p3=False):
    b = b"\0MACHO\0"
    if tag:
        b += b"SWEET_BOOT_LINE_LENGTH=%d\0" % tag
    if temp:
        b += L.TEMP_FMT + b"\0"
    if wdt:
        b += L.WDT_EARLY.encode() + b"\0"
    if p3:
        b += L.P3_TEMP_FMT + b"%s%d.%d C cnfg09=0x%x\0"
    with open(path, "wb") as f:
        f.write(b)
    return path


def sh(cmd, cwd, env=None):
    subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="safetychk_")
        self.art = os.path.join(self.tmp, "art")
        self.state = os.path.join(self.art, "safety")
        os.makedirs(self.state)
        self.tree = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(self.tree, "tools"))
        os.makedirs(os.path.join(self.tree, "xnu", "patches"))
        self.rev = os.path.join(self.tmp, "reviews")
        os.makedirs(self.rev)
        open(os.path.join(self.tree, "tools", "fboot.sh"), "w").write('tools/phone-lock check\n[ "$prod" = bluejay ] || exit 1\n')
        open(os.path.join(self.tree, "tools", "to-fastboot.sh"), "w").write("verify bluejay\n")
        open(os.path.join(self.tree, "tools", "mkpayload.py"), "w").write("BOOT_LINE_LENGTH = 1024\n")
        open(os.path.join(self.tree, "xnu", "patches", "0001-a.patch"), "w").write("plain patch\n")
        self.genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        sh(["git", "init", "-q", "-b", "trunk"], self.tree, self.genv)
        self.commit("base")
        self.env = dict(os.environ, ARTIFACTS=self.art, PIXOS_REVIEWS_DIR=self.rev)
        self.env.pop("T3CODE_THREAD_ID", None)
        self.k = kernel(os.path.join(self.tmp, "kernel"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, msg):
        sh(["git", "add", "-A"], self.tree, self.genv)
        sh(["git", "commit", "-q", "--allow-empty", "-m", msg], self.tree, self.genv)

    def seed(self, **kv):
        L.save_state(self.state, kv)

    def run_tool(self, *argv, env=None):
        r = subprocess.run([sys.executable, TOOL, *argv], env=env or self.env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def pre(self, *extra, kernel_path=None, line=GOOD_LINE):
        return self.run_tool("pre", "--kernel", kernel_path or self.k, "--bootline", line, "--tree", self.tree, "--state", self.state, *extra)

    def item(self, out, i):
        return [x for x in out.splitlines() if x.startswith("SAFETY ") and (" %s:" % i) in x][0]


class PreTest(Base):
    def test_cold_pass(self):
        self.seed(temp_c=30.0, temp_at=time.time() - 60, end_at=time.time() - 60)
        rc, out = self.pre()
        self.assertEqual(rc, 0, out)
        for i in ("S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"):
            self.assertTrue(self.item(out, i).startswith("SAFETY PASS pre"), out)
        self.assertIn("RESULT: PASS safety-check pre", out)
        self.assertIn("WARN", self.item(out, "S5"))

    def test_s2_hot_recent_fails(self):
        self.seed(temp_c=46.0, hot=True, hot_why="SAFETY FAIL S1: battery temp 46.0 C >= 45.0 C", end_at=time.time() - 60)
        rc, out = self.pre()
        self.assertEqual(rc, 1)
        self.assertTrue(self.item(out, "S2").startswith("SAFETY FAIL S2"), out)

    def test_s2_warm_reading_fails(self):
        self.seed(temp_c=39.0, end_at=time.time() - 60)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S2").startswith("SAFETY FAIL S2"), out)

    def test_s2_stale_hot_why_not_named(self):
        # L-21: hot cleared since an old S1 cut; the refusal must name the current reading, not the stale cause
        self.seed(temp_c=40.8, hot=False, hot_why="SAFETY FAIL S1: battery temp 45.0 C >= 45.0 C", end_at=time.time() - 60)
        rc, out = self.pre()
        s2 = self.item(out, "S2")
        self.assertTrue(s2.startswith("SAFETY FAIL S2"), out)
        self.assertNotIn("45.0 C", s2, out)
        self.assertIn("last battery temp 40.8 C", s2, out)

    def test_s2_hot_long_parked_defers(self):
        self.seed(temp_c=46.0, hot=True, end_at=time.time() - 1000)
        rc, out = self.pre()
        self.assertEqual(rc, 0, out)
        self.assertTrue(L.load_state(self.state)["defer_s2"])

    def test_s2_no_reading_needs_park(self):
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S2").startswith("SAFETY FAIL S2"), out)
        d = os.path.join(self.art, "phone.lock.runs", "r1")
        os.makedirs(d)
        p = os.path.join(d, "end.json")
        open(p, "w").write("{}")
        os.utime(p, (time.time() - 1000, time.time() - 1000))
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S2").startswith("SAFETY PASS"), out)
        self.assertTrue(L.load_state(self.state)["defer_s2"])

    def test_s3_kernel_without_temp(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "k2"), temp=False))
        self.assertTrue(self.item(out, "S3").startswith("SAFETY FAIL S3"), out)

    def test_s3_p3_state(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre()
        self.assertIn("GAP: no temp in the P3 rows", self.item(out, "S3"))
        self.assertFalse(L.load_state(self.state)["p3_temp"])
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "kp3"), p3=True))
        self.assertIn("P3 rows carry temp", self.item(out, "S3"))
        self.assertTrue(L.load_state(self.state)["p3_temp"])

    def test_s3_no_chg(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre(line="sweetusb=2 sweetwdt=60")
        self.assertTrue(self.item(out, "S3").startswith("SAFETY FAIL S3"), out)
        self.assertEqual(rc, 1)

    def test_s4_low(self):
        self.seed(temp_c=30.0, soc=12.0, vcell=3700, end_at=time.time() - 60)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S4").startswith("SAFETY FAIL S4"), out)
        self.seed(temp_c=30.0, soc=60.0, vcell=3400, end_at=time.time() - 60)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S4").startswith("SAFETY FAIL S4"), out)
        self.seed(temp_c=30.0, soc=12.0, end_at=time.time() - 1000)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S4").startswith("SAFETY PASS"), out)
        self.assertTrue(L.load_state(self.state)["defer_s4"])

    def test_s5_on_trunk_fails(self):
        self.seed(temp_c=30.0, end_at=time.time())
        open(os.path.join(self.tree, "xnu", "patches", "0333-w.patch"), "w").write("+ \"SWEET WDT: %s\" %s\n" % (L.WDT_EARLY, "x"))
        self.commit("0333")
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S5").startswith("SAFETY FAIL S5"), out)
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "k3"), wdt=True))
        self.assertTrue(self.item(out, "S5").startswith("SAFETY PASS"), out)
        self.assertNotIn("WARN", self.item(out, "S5"))
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "k4"), wdt=True), line="sweetusb=2 sweetchgi2c=1 sweetchgicl=900")
        self.assertTrue(self.item(out, "S5").startswith("SAFETY FAIL S5"), out)

    def test_s6_no_usbnet(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre("--usbnet", "0")
        self.assertTrue(self.item(out, "S6").startswith("SAFETY FAIL S6"), out)

    def test_s7(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre(line=GOOD_LINE + " x" * 600)
        self.assertTrue(self.item(out, "S7").startswith("SAFETY FAIL S7"), out)
        rc, out = self.pre(line=GOOD_LINE + " " + "y" * (1023 - len(GOOD_LINE) - 1))
        self.assertTrue(self.item(out, "S7").startswith("SAFETY PASS"), out)
        rc, out = self.pre(line=GOOD_LINE + " " + "y" * (1024 - len(GOOD_LINE) - 1))   # 1024 B: one over
        self.assertTrue(self.item(out, "S7").startswith("SAFETY FAIL S7"), out)
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "k5"), tag=2048))
        self.assertTrue(self.item(out, "S7").startswith("SAFETY FAIL S7"), out)
        rc, out = self.pre(kernel_path=kernel(os.path.join(self.tmp, "k6"), tag=0))
        self.assertTrue(self.item(out, "S7").startswith("SAFETY FAIL S7"), out)
        bo = os.path.join(self.tmp, "build.out")
        open(bo, "w").write("boot-args: x\nREFUSED: boot-args are 990 bytes\n")
        rc, out = self.pre("--build-out", bo)
        self.assertTrue(self.item(out, "S7").startswith("SAFETY FAIL S7"), out)
        open(bo, "w").write("boot-args: " + GOOD_LINE + "\n")
        rc, out = self.pre("--build-out", bo)
        self.assertTrue(self.item(out, "S7").startswith("SAFETY PASS"), out)

    def test_s8(self):
        self.seed(temp_c=30.0, end_at=time.time())
        rc, out = self.pre("--writes", "boot_a")
        self.assertTrue(self.item(out, "S8").startswith("SAFETY FAIL S8"), out)
        rc, out = self.pre("--writes", "userdata", "--action", "flash-userdata")
        self.assertTrue(self.item(out, "S8").startswith("SAFETY FAIL S8"), out)   # no tools/fflash-userdata.sh in the tree
        open(os.path.join(self.tree, "tools", "fboot.sh"), "w").write("exec fastboot boot\n")
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S8").startswith("SAFETY FAIL S8"), out)

    def test_s9(self):
        self.seed(temp_c=30.0, end_at=time.time())
        self.commit("shim: --x writes a PMIC register [NEW PMIC WRITE, UNREVIEWED]")
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S9").startswith("SAFETY FAIL S9"), out)
        sha = subprocess.run(["git", "-C", self.tree, "rev-parse", "HEAD"], capture_output=True, text=True).stdout[:8]
        open(os.path.join(self.rev, "0999-x-review.md"), "w").write("# Review of %s\n\n## Verdict: UNSAFE\n" % sha)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S9").startswith("SAFETY FAIL S9"), out)
        open(os.path.join(self.rev, "0999-x-review.md"), "w").write("# Review of %s\n\n## Verdict: SAFE WITH CONDITIONS\n" % sha)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S9").startswith("SAFETY PASS"), out)
        open(os.path.join(self.tree, "xnu", "patches", "0500-y.patch"), "w").write("Y (patch 0500) [NEW ACPM REQUEST, UNREVIEWED]\n")
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S9").startswith("SAFETY FAIL S9"), out)
        open(os.path.join(self.rev, "0500-y-review.md"), "w").write("## Verdict: SAFE\n")
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S9").startswith("SAFETY PASS"), out)

    def test_image_stamp(self):
        self.seed(temp_c=30.0, end_at=time.time())
        img = os.path.join(self.tmp, "img")
        open(img, "wb").write(b"image")
        rc, out = self.pre("--image", img)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_tool("stamp-check", img)
        self.assertEqual(rc, 0, out)
        open(img, "wb").write(b"other")
        rc, out = self.run_tool("stamp-check", img)
        self.assertEqual(rc, 1, out)
        rc, out = self.run_tool("stamp-check", os.path.join(self.tmp, "none"))
        self.assertEqual(rc, 1, out)

    def test_fail_writes_no_stamp_and_no_defer(self):
        self.seed(temp_c=30.0, end_at=time.time())
        img = os.path.join(self.tmp, "img")
        open(img, "wb").write(b"image")
        rc, out = self.pre("--image", img, "--usbnet", "0")
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(img + ".safety"))


class MonitorUnit(unittest.TestCase):
    def mon(self, **kw):
        return L.Monitor(1000.0, **kw)

    def test_rows(self):
        self.assertEqual(L.parse_row(MON % "46.1"), {"vcell": 3705, "soc": 66.97, "temp": 46.1})
        self.assertEqual(L.parse_row(P3), {"vcell": 3777, "soc": 70.54, "temp": None})
        self.assertEqual(L.parse_row(P2), {"vcell": 3786, "soc": 70.58, "temp": None})
        self.assertEqual(L.parse_row(MON % "-0.5")["temp"], -0.5)
        self.assertIsNone(L.parse_row("SWEET USBLOG: hello"))

    def test_s1(self):
        m = self.mon()
        m.feed(1010, MON % "44.9")
        self.assertEqual(m.check(1011), "")
        m.feed(1012, MON % "45.0")
        self.assertTrue(m.check(1013).startswith("SAFETY FAIL S1"))

    def test_s3(self):
        m = self.mon()
        m.feed(1010, P3)
        self.assertEqual(m.check(1000 + L.TEMP_FIRST_S - 1), "") if False else None
        for t in range(1010, 1000 + L.TEMP_FIRST_S + 5, 30):
            m.feed(t, P3)
            if t - 1000 <= L.TEMP_FIRST_S:
                self.assertEqual(m.check(t), "", t)
        self.assertTrue(m.check(1000 + L.TEMP_FIRST_S + 5).startswith("SAFETY FAIL S3"))
        m = self.mon()
        m.feed(1010, MON % "30.0")
        m.feed(1060, P3)
        self.assertEqual(m.check(1070), "")
        m.feed(1071, P3)
        self.assertTrue(m.check(1071).startswith("SAFETY FAIL S3"))

    def test_s3_p3_temp_kernel(self):   # patch 0334: no blind window
        m = self.mon(p3_temp=True)
        m.feed(1050, "SWEET USBLOG: replay starts now")
        m.feed(1100, "x")
        self.assertEqual(m.check(1050 + L.TEMP_GAP_S), "")
        m.feed(1111, "x")
        self.assertTrue(m.check(1111).startswith("SAFETY FAIL S3: no temp= row within 60 s of the first USB log line"))
        m = self.mon(p3_temp=True)
        m.feed(1050, "x")
        m.feed(1080, P3.replace(" cnfg09", " temp=33.5 C cnfg09"))
        self.assertEqual(m.last_temp, 33.5)
        self.assertEqual(m.check(1100), "")

    def test_s4(self):
        m = self.mon()
        for _ in range(3):
            m.feed(1010, MON.replace("soc=66.97", "soc=9.50") % "30.0")
        self.assertTrue(m.check(1011).startswith("SAFETY FAIL S4"))
        m = self.mon()
        m.feed(1008, MON % "30.0")
        m.feed(1009, MON % "30.0")
        m.feed(1010, MON.replace("vcell=3705", "vcell=3400") % "30.0")
        self.assertEqual(m.check(1011), "", "one dip is not the median of 3")
        m.feed(1011, MON.replace("vcell=3705", "vcell=3400") % "30.0")
        self.assertTrue(m.check(1012).startswith("SAFETY FAIL S4"))
        m = self.mon(defer_s4=True)
        m.feed(1010, P2.replace("70.58", "14.00"))
        self.assertTrue(m.check(1011).startswith("SAFETY FAIL S4"))

    def test_s2_first_row(self):
        m = self.mon(defer_s2=True)
        m.feed(1010, MON % "38.0")
        self.assertTrue(m.check(1011).startswith("SAFETY FAIL S2"))
        m = self.mon(defer_s2=True)
        m.feed(1010, MON % "37.9")
        self.assertEqual(m.check(1011), "")

    def test_s6(self):
        m = self.mon()
        self.assertEqual(m.check(1000 + L.ALIVE_FIRST_S), "")
        self.assertTrue(m.check(1000 + L.ALIVE_FIRST_S + 1).startswith("SAFETY FAIL S6"))
        m = self.mon()
        m.feed(1010, MON % "30.0")
        self.assertEqual(m.check(1010 + L.ALIVE_GAP_S), "")
        self.assertTrue(m.check(1011 + L.ALIVE_GAP_S).startswith("SAFETY FAIL S6"))

    def test_s6_reset(self):
        m = self.mon()
        m.feed(1010, MON % "30.0")
        m.usb(1011, False, False)
        self.assertIsNone(m.gone_at, "a gadget never seen cannot leave")
        m.usb(1012, True, False)
        m.usb(1020, False, False)
        self.assertEqual(m.check(1020 + L.RESET_BACK_S), "")
        self.assertTrue(m.check(1021 + L.RESET_BACK_S).startswith("SAFETY FAIL S6"))
        m.usb(1022, False, True)
        self.assertEqual(m.check(1300), "")

    def test_waitusb_stop_and_report(self):
        d = tempfile.mkdtemp(prefix="safetyrun_")
        try:
            marks = []
            mk = lambda _d, kv: marks.extend(kv)  # noqa: E731
            self.assertFalse(L.waitusb_stop(d, {}, mk, 1.0))
            p = subprocess.Popen(["sleep", "30"])
            self.assertFalse(L.waitusb_stop(d, {"safety_monitor_pid": str(p.pid)}, mk, 1.0))
            p.kill()
            p.wait()
            open(os.path.join(d, "safety-stop.done"), "w").write("SAFETY MONITOR END: phone back on the bus as 18d1 (reset)\n")
            self.assertFalse(L.waitusb_stop(d, {"safety_monitor_pid": str(p.pid)}, mk, 1.0), "a monitor that ended normally is not a dead one")
            os.remove(os.path.join(d, "safety-stop.done"))
            self.assertTrue(L.waitusb_stop(d, {"safety_monitor_pid": str(p.pid)}, mk, 1.0))
            self.assertTrue(any(x.startswith("safety_stop=SAFETY FAIL MON") for x in marks))
            os.remove(os.path.join(d, "safety-stop"))
            open(os.path.join(d, "safety-stop"), "w").write("SAFETY FAIL S1: hot\n")
            self.assertTrue(L.waitusb_stop(d, {}, mk, 1.0))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(L.report_safety({"t_boot": "1"})[0], None, "old run dirs replay unchanged")
        self.assertEqual(L.report_safety({"safety": "required", "t_boot": "1"})[0], "FAIL")
        self.assertEqual(L.report_safety({"safety": "required", "t_boot": "1", "safety_pre": "RESULT: PASS"})[0], "FAIL")
        self.assertIn("no SAFETY PASS pre", L.report_safety({"safety": "required", "t_boot": "1", "safety_monitor_pid": "9"})[1])
        self.assertIsNone(L.report_safety({"safety": "required", "t_boot": "1", "safety_pre": "RESULT: PASS", "safety_monitor_pid": "9"})[0])
        r = L.report_safety({"safety": "required", "t_boot": "1", "safety_pre": "P", "safety_monitor_pid": "9", "safety_stop": "SAFETY FAIL S1: x", "human_needed": "h"})
        self.assertEqual(r[0], "FAIL")
        self.assertIn("HUMAN-NEEDED: h", r[2])


class CliMonitorTest(Base):
    def mon(self, log, *extra, env=None):
        stop = os.path.join(self.tmp, "stop")
        until = os.path.join(self.tmp, "until")
        sysfs = os.path.join(self.tmp, "sysfs")
        os.makedirs(sysfs, exist_ok=True)
        rc, out = self.run_tool("monitor", "--log", log, "--state", self.state, "--stop", stop, "--until", until, "--sysfs", sysfs, *extra, env=env)
        return rc, out, (open(stop).read() if os.path.exists(stop) else "")

    def log(self, rows):
        p = os.path.join(self.tmp, "usbnet.log")
        with open(p, "w") as f:
            for ep, ln in rows:
                f.write("%.3f\t%s\n" % (ep, ln))
        return p

    def test_hot_stops(self):
        now = time.time()
        rc, out, stop = self.mon(self.log([(now, P3), (now, MON % "46.1")]))
        self.assertEqual(rc, 1, out)
        self.assertTrue(stop.startswith("SAFETY FAIL S1"), stop)
        st = L.load_state(self.state)
        self.assertTrue(st["hot"])
        self.assertEqual(st["temp_c"], 46.1)
        rc, out = self.pre()
        self.assertTrue(self.item(out, "S2").startswith("SAFETY FAIL S2"), out)

    def test_silent_stops_s6(self):
        senv = dict(self.env, SAFETY_TBOOT_BACKDATE="200", PHONE_LOCK_DIR=os.path.join(tempfile.gettempdir(), "safetychk-lock-%d" % os.getpid()))
        rc, out, stop = self.mon(os.path.join(self.tmp, "none.log"), env=senv)
        self.assertEqual(rc, 1, out)
        self.assertTrue(stop.startswith("SAFETY FAIL S6"), stop)
        self.assertIn("SIM SEAM ACTIVE: SAFETY_TBOOT_BACKDATE", out)

    def test_backdate_refused_outside_sandbox(self):
        env = dict(self.env, SAFETY_TBOOT_BACKDATE="200")
        env.pop("PHONE_LOCK_DIR", None)
        rc, out, _ = self.mon(os.path.join(self.tmp, "none.log"), env=env)
        self.assertEqual(rc, 2, out)

    def test_until_ends_cleanly(self):
        now = time.time()
        open(os.path.join(self.tmp, "until"), "w").close()
        rc, out, stop = self.mon(self.log([(now, MON % "31.0")]))
        self.assertEqual(rc, 0, out)
        self.assertEqual(stop, "")
        self.assertIn("SAFETY MONITOR END: until-file", out)
        self.assertIn("SAFETY MONITOR END", open(os.path.join(self.tmp, "stop.done")).read())
        st = L.load_state(self.state)
        self.assertEqual(st["temp_c"], 31.0)
        self.assertTrue(st["end_at"])

    def test_cool_first_row_clears_hot(self):
        self.seed(hot=True, temp_c=46.0, defer_s2=True, end_at=time.time() - 1000)
        now = time.time()
        open(os.path.join(self.tmp, "until"), "w").close()
        rc, out, _ = self.mon(self.log([(now, MON % "30.5")]))
        self.assertEqual(rc, 0, out)
        st = L.load_state(self.state)
        self.assertFalse(st["hot"])
        self.assertFalse(st["defer_s2"])


class FastbootAndNotifyTest(Base):
    def fb(self, product="bluejay", mv="3900", sc="30"):
        p = os.path.join(self.tmp, "fastboot")
        with open(p, "w") as f:
            f.write("#!/bin/sh\ncase \"$2\" in product) echo 'product: %s' >&2;; battery-voltage) %s;; storage-temperature) %s;; esac\n" % (
                product, "echo 'battery-voltage: %s' >&2" % mv if mv else "echo 'FAILED' >&2", "echo 'storage-temperature: %s' >&2" % sc if sc else "true"))
        os.chmod(p, 0o755)
        return dict(self.env, SAFETY_FASTBOOT=p, PHONE_LOCK_DIR=os.path.join(tempfile.gettempdir(), "safetychk-lock-%d" % os.getpid()))

    def test_fastboot(self):
        self.seed(temp_c=30.0, end_at=time.time() - 60)
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb())
        self.assertEqual(rc, 0, out)
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb(product="sweet"))
        self.assertEqual(rc, 1, out)
        self.assertIn("SAFETY FAIL S8", out)
        self.assertNotIn("S4", out, "nothing else is asked of another phone")
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb(mv="3450"))
        self.assertIn("SAFETY FAIL S4", out)
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb(sc="38"))
        self.assertIn("SAFETY FAIL S2", out)
        self.seed(temp_c=46.0, hot=True, end_at=time.time() - 60)
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb(sc=""))
        self.assertIn("SAFETY FAIL S2", out)
        self.assertEqual(rc, 1)
        rc, out = self.run_tool("fastboot", "--state", self.state, env=self.fb(sc="30"))
        self.assertEqual(rc, 0, out)

    def test_fastboot_seam_refused_outside_sandbox(self):
        env = self.fb()
        env.pop("PHONE_LOCK_DIR")
        rc, out = self.run_tool("fastboot", "--state", self.state, env=env)
        self.assertEqual(rc, 2, out)

    def test_notify(self):
        rec = os.path.join(self.tmp, "t3.rec")
        t3 = os.path.join(self.tmp, "t3")
        open(t3, "w").write("#!/bin/sh\necho \"$@\" >> %s\n" % rec)
        os.chmod(t3, 0o755)
        env = dict(self.env, PHONE_LOCK_T3=t3, T3CODE_THREAD_ID="th-1")
        rc, out = self.run_tool("notify", "--line", "SAFETY FAIL S6: silent", "--state", self.state, env=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTIFY ok thread=th-1", out)
        self.assertIn("thread send th-1 SAFETY HUMAN-NEEDED: SAFETY FAIL S6: silent", open(rec).read())
        self.assertIn("SAFETY FAIL S6", open(os.path.join(self.state, "human-needed.log")).read())
        env.pop("T3CODE_THREAD_ID")
        rc, out = self.run_tool("notify", "--line", "x", "--state", self.state, env=env)
        self.assertIn("NOTIFY none", out)

    def test_help_and_usage(self):
        rc, out = self.run_tool("--help")
        self.assertEqual(rc, 0)
        self.assertIn("CALL CONTRACT", out)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2)
        rc, out = self.run_tool("pre")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    warnings.simplefilter("ignore", ResourceWarning)
    r = unittest.main(exit=False, verbosity=1).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s safety-check tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f))
    sys.exit(1 if f else 0)
