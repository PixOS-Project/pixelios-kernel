import os
import contextlib, io, json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xrun_report

T0 = 1_800_000_000.0        # fixed "boot command" time of the synthetic run
try:
    from PIL import Image
except ImportError:   # lint-silent: expected optional dependency: the PIL tests are skipIf(Image is None) and gate-unit-shards names the skip
    Image = None


def stamp(t):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))


def make_run(d, snaps, frames=(), run=None, label="t"):
    """snaps: [(seconds after boot, text)]; frames: [(seconds after boot, gray level)]."""
    d = pathlib.Path(d)
    (d / "snaps").mkdir(parents=True)
    (d / "frames").mkdir()
    r = {"label": label, "kernel": "K", "kernel_sha256": "ab" * 32, "git_head": "abc1234", "boot_args": "debug=0xc cpus=2",
         "cpus": "2", "qr": "60", "restart": "", "duration": "180", "t_boot": str(T0), "t_end": str(T0 + 150)}
    r.update(run or {})
    (d / "run.json").write_text(json.dumps(r))
    for i, (t, text) in enumerate(snaps):
        (d / "snaps" / ("qr-%s-%08x.txt" % (stamp(T0 + t), i))).write_text(text)
    for t, g in frames:
        p = d / "frames" / ("f%d.jpg" % t)
        Image.new("L", (80, 60), g).save(p)
        os.utime(p, (T0 + t, T0 + t))
    return d


FIX = (HERE / "testdata" / "qr-b.txt").read_text(encoding="latin-1")
LOG1 = ("SWEET RESTART: armed, reset in 60 s (bootloader=0)\nSWEET CPU: cpu=0 mpidr=0x0 came alive; SGI1 OK\n"
        + FIX[:2000].rsplit("\n", 1)[0] + "\n")
LOG2 = LOG1 + "\n".join([
    "SWEET TOUCH: gs101 FTM5 driver, mode 0, starting in 5 s",
    "CRASH Foo[12] sig 9 code 0x0 sub 0x0 slide 0x1 mh 0x2",
    "CRASH Foo pc 0x1234 lr 0x1 sp 0x2 fp 0x3 far 0x4",
    "CRASH Foo reason ns 7 code 0x3: extension check-in timeout",
    "CRASH Foo[13] sig 9 code 0x0 sub 0x0 slide 0x1 mh 0x2",
    "CRASH Bar[14] sig 11 code 0x0 sub 0x0 slide 0x1 mh 0x2",
    "CRASH Bar pc 0x99 lr 0x1 sp 0x2 fp 0x3 far 0x4", ""])


@unittest.skipIf(Image is None, "PIL missing")
class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xrun-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        # hermetic UCRASH block (2026-11-11: 39 lines here with the host's ipsw + a2s cache, 42 on the offload host without them): every host renders the no-symbols worst case
        for k in ("PIXOS_IPSW", "PIXOS_DSC", "PIXOS_UCRASH_CACHE"):
            self.addCleanup(lambda k=k, v=os.environ.get(k): os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None))
            os.environ[k] = os.path.join(self.tmp, "none-" + k.lower())

    def run_dir(self, name="20260101T000000Z-t", **kw):
        return make_run(pathlib.Path(self.tmp) / "runs" / name, **kw)

    def test_expect_crashes_markers_screen(self):
        frames = [(t, 200) for t in range(0, 96, 4)] + [(t, 3) for t in range(96, 140, 4)]
        d = self.run_dir(snaps=[(30, LOG1), (90, LOG2)], frames=frames,
                         run={"expect": ["SWEET TOUCH: gs101", "NOPE"], "expect_absent": ["Kernel trap"], "restart": "60"})
        xrun_report.mark(str(d), ["usb_back=%f" % (T0 + 70)])
        r = xrun_report.build(str(d))
        found, missing, absent = r["expect"]
        self.assertTrue(found["found"])
        self.assertEqual(found["line"], "SWEET TOUCH: gs101 FTM5 driver, mode 0, starting in 5 s")
        self.assertEqual(round(found["t"]), 90)
        self.assertFalse(missing["found"])
        self.assertTrue(absent["absent"] and not absent["found"])
        self.assertEqual(r["crashes"]["Foo"], {"count": 2, "sig": "9", "pc": "0x1234", "reason": "ns 7 code 0x3: extension check-in timeout"})
        self.assertEqual(r["crashes"]["Bar"]["pc"], "0x99")
        self.assertEqual(r["markers"]["restart armed"]["count"], 1)
        self.assertEqual(r["markers"]["extension check-in timeout"]["count"], 1)
        self.assertEqual(r["run"]["reset_at"], 70)
        self.assertEqual(r["screen"]["black_at"], 96)
        self.assertIn("bright 0-92 s", r["screen"]["text"])
        self.assertIn("black from 96 s", r["screen"]["text"])
        self.assertEqual((r["result"], r["exit"]), ("FAIL", 1))        # NOPE is missing and the log starts at boot without gaps
        txt = xrun_report.render(r)
        self.assertTrue(txt.startswith("RESULT: FAIL 1 of 3 expectations failed"))
        self.assertLessEqual(len(txt.splitlines()), 40)

    def test_pass_then_reset_not_observed(self):
        d = self.run_dir(snaps=[(30, LOG1)], run={"expect": ["SWEET CPU: cpu=0"]})
        self.assertEqual(xrun_report.build(str(d))["result"], "PASS")
        with contextlib.redirect_stdout(io.StringIO()):
            xrun_report.report(str(d))      # writes report.json, the next run with the same label diffs against it
        d2 = self.run_dir("20260101T000100Z-t", snaps=[(30, LOG1)], run={"restart": "60", "expect": ["SWEET CPU"]})
        r = xrun_report.build(str(d2))
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("did not fire", r["reason"])
        self.assertEqual(r["diff"]["previous"], "PASS")

    def test_hard_timeout_cases(self):
        # TP-020 follow-up (review 13): a hard timeout is not "the reset did not fire"
        # 1. timed out before the reset was due -> PARTIAL, no FAIL
        d = self.run_dir("20260101T000200Z-a", snaps=[(30, LOG1)], run={"restart": "600", "timeout": "1", "expect": ["SWEET CPU"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PARTIAL")
        self.assertIn("before the reset was due", r["reason"])
        # 2. reset due, bluejay in recovery at the end -> PASS, worded as inferred
        d = self.run_dir("20260101T000300Z-b", snaps=[(30, LOG1)], run={"restart": "60", "timeout": "1", "usb_gone": str(T0 + 2), "t_end": str(T0 + 400), "phone": "usb ids [18d1:d001 ] adb [recovery] fastboot product [no answer]", "expect": ["SWEET CPU"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertIn("inferred", r["reason"])
        # 3. some other Google-VID device on the bus, bluejay absent -> NOT a pass
        d = self.run_dir("20260101T000400Z-c", snaps=[(30, LOG1)], run={"restart": "60", "timeout": "1", "usb_gone": str(T0 + 2), "t_end": str(T0 + 400), "phone": "usb ids [18d1:4ee7 ] adb [error: no devices/emulators found] fastboot product [no answer]", "expect": ["SWEET CPU"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "FAIL")
        self.assertNotIn("the phone reset", r["reason"])
        self.assertIn("tools/phone state", r["reason"])

    def test_waitusb_grace_only_when_reset_due(self):
        import io, contextlib
        calls = {"n": 0}
        t = {"v": T0}

        def fake_now():
            return t["v"]

        def fake_sleep(x):
            t["v"] += x

        def fake_present():
            calls["n"] += 1
            return calls["n"] > 3 and t["v"] > T0 + 70  # the phone is gone, and shows up 3 polls into the grace window

        def run(restart):
            d = self.run_dir("2026010100%d500Z-g" % len(restart), snaps=[], run={"restart": restart, "hard_timeout": "50", "t_start": str(T0), "t_boot": str(T0), "duration": "500"})
            xrun_report.mark(str(d), ["usb_gone=%f" % (T0 + 2)])
            calls["n"] = 0; t["v"] = T0
            old = (xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present)
            xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = fake_now, fake_sleep, fake_present
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    xrun_report.waitusb(str(d))
            finally:
                xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = old
            return out.getvalue().strip(), xrun_report.load(os.path.join(str(d), "run.json"), {})
        how, rj = run("30")      # reset due at +30, hard timeout at +50: grace runs and sees the phone return
        self.assertEqual(how, "TIMEOUT")
        self.assertIn("late_back", rj)
        how, rj = run("600")     # reset not due: no grace window
        self.assertEqual(how, "TIMEOUT")
        self.assertNotIn("late_back", rj)

    def test_duration_end_by_host_when_remote_reset_is_on(self):
        # B1 default: no stop pattern, the phone runs iOS (off the bus) when --duration is up -> EARLY + host_end, scored PASS without waiting for a timer
        t = {"v": T0}
        d = self.run_dir("20260101T000800Z-h", snaps=[(30, LOG1)], run={"remote": "1", "duration": "120", "t_boot": str(T0), "expect": ["SWEET CPU"]})
        xrun_report.mark(str(d), ["usb_gone=%f" % (T0 + 2)])
        old = (xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present)
        xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = (lambda: t["v"]), (lambda x: t.__setitem__("v", t["v"] + x)), (lambda: False)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                xrun_report.waitusb(str(d))
        finally:
            xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = old
        self.assertEqual(out.getvalue().strip(), "EARLY")
        self.assertAlmostEqual(t["v"] - T0, 120, delta=6)
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertIn("ended by the host", r["run"]["early"])
        self.assertIn("ran the full 120s (--duration), ended by the host", r["reason"])   # dk-0181: a host end is not "stopped early"
        self.assertNotIn("stopped early", r["reason"])

    def _waitusb_end(self, name, run, present):
        # run waitusb on a fake clock; the phone left the bus at +2 s; returns (stdout, run.json)
        t = {"v": T0}
        d = self.run_dir(name, snaps=[(30, LOG1)], run=dict(run, t_boot=str(T0), t_start=str(T0)))
        xrun_report.mark(str(d), ["usb_gone=%f" % (T0 + 2)])
        old = (xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present)
        xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = (lambda: t["v"]), (lambda x: t.__setitem__("v", t["v"] + x)), (lambda: present)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                xrun_report.waitusb(str(d))
        finally:
            xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = old
        return out.getvalue().strip(), xrun_report.load(os.path.join(str(d), "run.json"), {})

    def test_host_end_before_sweetrestart_is_not_reported_as_unobserved(self):
        # 21k el2-3-f7: --duration 1500 < sweetrestart 1650, the host reset pre-empts the backstop; "reset NOT observed ... hung" was misleading
        d = self.run_dir("20260101T000900Z-he", snaps=[(30, LOG1)], run={"remote": "1", "duration": "120", "restart": "1650", "host_end": "1", "early": str(T0 + 120), "t_boot": str(T0)})
        r = xrun_report.build(str(d))
        txt = json.dumps(r)
        self.assertIn("reset sent by the host at the end of --duration", txt)
        self.assertNotIn("reset NOT observed", txt)
        d = self.run_dir("20260101T000902Z-hl", snaps=[(30, LOG1)], run={"remote": "1", "duration": "300", "restart": "100", "host_end": "1", "early": str(T0 + 300), "t_boot": str(T0)})
        txt = json.dumps(xrun_report.build(str(d)))      # review r1 F1: restart <= duration, the backstop WAS due: keep the old (suspicious) wording
        self.assertIn("reset NOT observed", txt)
        self.assertNotIn("was not due", txt)
        d = self.run_dir("20260101T000901Z-nh", snaps=[(30, LOG1)], run={"remote": "1", "duration": "120", "restart": "1650", "t_boot": str(T0)})
        self.assertIn("reset NOT observed", json.dumps(xrun_report.build(str(d))))     # without host_end the wording stays

    def test_host_end_needs_remote_no_timeout_and_phone_off_the_bus(self):
        # review boot-dev-0121 F5: each condition of the host_end branch (xrun_report.py:152) is pinned
        how, rj = self._waitusb_end("20260101T000900Z-n", {"duration": "120", "hard_timeout": "150"}, False)
        self.assertNotIn("host_end", rj)            # no remote: the host never ends the run itself
        how, rj = self._waitusb_end("20260101T001000Z-o", {"remote": "1", "duration": "120", "hard_timeout": "60"}, False)
        self.assertEqual(how, "TIMEOUT")            # hard timeout first: not a host end
        self.assertNotIn("host_end", rj)
        how, rj = self._waitusb_end("20260101T001100Z-p", {"remote": "1", "duration": "120", "hard_timeout": "200"}, True)
        self.assertNotEqual(how, "EARLY")           # the phone is back on 18d1 already: nothing to reset
        self.assertNotIn("host_end", rj)

    def test_reset_text_names_what_the_phone_came_back_as(self):
        d = self.run_dir("20260101T000600Z-r", snaps=[(30, LOG1)], run={"restart": "60,bootloader", "expect": ["SWEET CPU"]})
        xrun_report.mark(str(d), ["usb_back=%f" % (T0 + 150), "usb_back_id=18d1:4ee0"])
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertIn("back as fastboot", r["reason"])

    def test_fail_on_ends_the_run_early_and_scores_fail(self):
        import io, contextlib
        t = {"v": T0}
        d = self.run_dir("20260101T000700Z-f", snaps=[(30, LOG1)], run={"restart": "600", "duration": "900", "t_boot": str(T0), "fail_on": ["SWEET CPU: cpu=0 mpidr"], "expect": ["SWEET CPU"]})
        old = (xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present)
        xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = (lambda: t["v"]), (lambda x: t.__setitem__("v", t["v"] + x)), (lambda: True)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                xrun_report.waitusb(str(d))
        finally:
            xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = old
        self.assertEqual(out.getvalue().strip(), "EARLY")
        self.assertLess(t["v"] - T0, 60)   # not the 900 s window
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("failure pattern", r["reason"])

    def _waitusb_clock(self, name, run, present=True):
        # fast-loop: waitusb on a fake clock; returns (stdout, seconds waited, run.json, report)
        t = {"v": T0}
        d = self.run_dir(name, snaps=[(30, LOG1)], run=dict(run, t_boot=str(T0), t_start=str(T0)))
        old = (xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present)
        xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = (lambda: t["v"]), (lambda x: t.__setitem__("v", t["v"] + x)), (lambda: present)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                xrun_report.waitusb(str(d))
        finally:
            xrun_report.now, xrun_report.time.sleep, xrun_report.usb_present = old
        return out.getvalue().strip(), t["v"] - T0, xrun_report.load(os.path.join(str(d), "run.json"), {}), xrun_report.build(str(d))

    def test_absent_patterns_no_longer_force_the_full_duration(self):
        # fast-loop 2026-11-09: every --expect seen -> stop after stop_grace, even with --expect-absent; the report names the absent window
        how, waited, rj, r = self._waitusb_clock("20260101T001200Z-ag", {"restart": "1150,bootloader", "duration": "1100", "stop_on": ["SWEET CPU"],
                                                 "expect": ["SWEET CPU"], "expect_absent": ["NEVER PRINTED"], "stop_grace": "30"})
        self.assertEqual(how, "EARLY")
        self.assertLess(waited, 60)                       # not the 1100 s window
        self.assertGreaterEqual(waited, 30)               # but the grace window ran
        self.assertIn("stop_all_seen", rj)
        self.assertIn("absent_window", rj)
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertIn("--expect-absent checked to +", r["reason"])

    def test_absent_line_ends_the_run_at_once_as_fail(self):
        how, waited, rj, r = self._waitusb_clock("20260101T001300Z-av", {"restart": "1150,bootloader", "duration": "1100", "stop_on": ["NOT YET"],
                                                 "expect": ["NOT YET"], "expect_absent": ["SWEET CPU: cpu=0 mpidr"], "stop_grace": "30"})
        self.assertEqual(how, "EARLY")
        self.assertLess(waited, 20)
        self.assertIn("absent-violated", rj.get("early_fail", ""))
        self.assertEqual(r["result"], "FAIL")

    def test_no_grace_without_absent_patterns(self):
        how, waited, rj, r = self._waitusb_clock("20260101T001400Z-ng", {"restart": "1150,bootloader", "duration": "1100", "stop_on": ["SWEET CPU"], "expect": ["SWEET CPU"]})
        self.assertEqual(how, "EARLY")
        self.assertLess(waited, 5)
        self.assertNotIn("absent_window", rj)
        self.assertNotIn("checked to", r["reason"])

    def test_kernel_timestamp_prefix_does_not_hide_lines(self):
        # kernel patch 0115 (sweetlogts=1): every console line starts with "[SSSSS.mmm] "; the anchored regexes must still match and the stamp is reported
        stamped = "".join("[%5d.%03d] %s\n" % (10 + i, 7, ln) for i, ln in enumerate(LOG1.splitlines()))
        d = self.run_dir("20260101T000800Z-k", snaps=[(30, stamped)], run={"expect": ["SWEET CPU: cpu=0 mpidr"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertTrue(r["expect"][0]["found"] if isinstance(r["expect"], list) else True)
        cpu = r["markers"]
        self.assertTrue(any(v.get("k") is not None for v in cpu.values()), cpu)
        self.assertEqual(xrun_report.kstamp("[   12.345] SWEET CPU: x"), 12.345)
        self.assertIsNone(xrun_report.kstamp("SWEET CPU: x"))

    def test_dead_root_is_a_fail(self):
        bad = LOG1 + "hfs_mountroot failed: 5\ncannot mount root, errno = 19\n"
        d = self.run_dir("20260101T000900Z-r", snaps=[(30, bad)], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("root mount failed (2 line(s))", r["reason"])
        d = self.run_dir("20260101T001000Z-r", snaps=[(30, bad)], run={"expect": ["SWEET CPU: cpu=0"], "no_root_ok": True})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertEqual(r["markers"]["root mount failed"]["count"], 2)
        d = self.run_dir("20260101T001100Z-r", snaps=[(30, LOG1 + "hfs: Initializing the journal\n")], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["markers"]["root mount failed"]["count"], 0)
        self.assertEqual(r["result"], "PASS")

    def test_dead_root_mutant_is_caught(self):
        # mutant: the new elif never fires -> the dead-root run is reported PASS, so test_dead_root_is_a_fail would fail
        src = (HERE / "xrun_report.py").read_text()
        branch = 'elif markers["root mount failed"]["count"] and not run.get("no_root_ok"):'
        self.assertIn(branch, src)
        mod = type(sys)("xrun_report_mutant")
        mod.__file__ = str(HERE / "xrun_report.py")
        exec(compile(src.replace(branch, "elif False:"), mod.__file__, "exec"), mod.__dict__)
        d = self.run_dir("20260101T001200Z-m", snaps=[(30, LOG1 + "hfs_mountroot failed: 5\n")], run={"expect": ["SWEET CPU: cpu=0"]})
        self.assertEqual(mod.build(str(d))["result"], "PASS")
        self.assertEqual(xrun_report.build(str(d))["result"], "FAIL")

    def test_panic_usb_boot_line_is_not_a_panic(self):
        # T-9d99 review r1 F1 (AP-229): patch 0242's boot line, both branches (xnu/patches/0242-sweet-panic-usb.patch:33), raises no panic marker
        tmpl = "[   12.345] SWEET FAULT USB: %s (patch 0242: first 8192 bytes of the fault log + 4096 bytes of console tail sent over the USB gadget at the start of the hold)\n"
        for i, v in enumerate(("on", "off (boot-arg = 0)")):
            d = self.run_dir("20260101T00120%dZ-pu" % i, snaps=[(30, LOG1 + tmpl % v)], run={"expect": ["SWEET CPU: cpu=0"]})
            r = xrun_report.build(str(d))
            self.assertEqual(r["result"], "PASS", r["reason"])
            self.assertEqual(r["markers"]["panic/hang/watchdog"]["count"], 0, v)

    def test_sweetpanic_bootarg_token_is_not_a_panic(self):
        # T-15e0 (AP-229 class): run 20261107T172408Z-panic-usb-forced FAILed on its SWEET BOOTARGS line, which names the
        # sweetpanic= boot-arg; a "sweetpanic*" token is never a panic event, a real panic( line still FAILs
        ba = "[    1.234] SWEET BOOTARGS: kern.bootargs view hides 2 tokens (sweetpanic=20 sweetpanichold=30 sweetpanicusb=1)\n"
        d = self.run_dir("20260101T001400Z-b", snaps=[(30, LOG1 + ba)], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertEqual(r["markers"]["panic/hang/watchdog"]["count"], 0)
        real = "[    2.000] panic(cpu 3 caller 0xfffffe000754ef7c): SWEET PANIC TEST: sweetpanic=20 elapsed @sweet_usb.c:1568\n"
        d = self.run_dir("20260101T001500Z-b", snaps=[(30, LOG1 + ba + real)], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "FAIL")
        self.assertEqual(r["markers"]["panic/hang/watchdog"]["count"], 1)

    def test_gap_line_is_not_a_panic(self):
        # patch 0182's boot line (xnu/patches/0182-sweet-panic-noregion-reset.patch:65) stays a counted gap, not a panic; a real panic( line still FAILs
        gap = "[   12.345] SWEET GAP: no panic region (no pram / embedded-panic-log-size in ADT); panic log not saved, panics spin (patch 0182; real fix PR-3)\n"
        d = self.run_dir("20260101T001200Z-g", snaps=[(30, LOG1 + gap)], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertEqual(r["markers"]["panic/hang/watchdog"]["count"], 0)
        self.assertEqual(r["gaps"]["distinct"], 1)
        real = "[   13.000] panic(cpu 0 caller 0xfffffe0007a1b2c4): Kernel data abort. at pc 0x1\n"
        d = self.run_dir("20260101T001300Z-g", snaps=[(30, LOG1 + gap + real)], run={"expect": ["SWEET CPU: cpu=0"]})
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "FAIL")
        self.assertEqual(r["markers"]["panic/hang/watchdog"]["count"], 1)
        self.assertIn("panic(cpu 0", r["markers"]["panic/hang/watchdog"]["first"])
        self.assertEqual(r["gaps"]["distinct"], 1)
        # r1 F1: leading blanks (gap counter strips) still not a panic; r1 F2: a panic( glued after a GAP prefix still FAILs
        d = self.run_dir("20260101T001400Z-g", snaps=[(30, LOG1 + "  " + gap)], run={"expect": ["SWEET CPU: cpu=0"]})
        self.assertEqual(xrun_report.build(str(d))["result"], "PASS")
        glued = "SWEET GAP: foo x1 (A)panic(cpu 1 caller 0xfffffe0007a1b2c4): Kernel trap at 0x1\n"
        d = self.run_dir("20260101T001500Z-g", snaps=[(30, LOG1 + glued)], run={"expect": ["SWEET CPU: cpu=0"]})
        self.assertEqual(xrun_report.build(str(d))["result"], "FAIL")

    def test_tail_only_log_makes_a_missing_expect_partial(self):
        d = self.run_dir(snaps=[(30, "x\n" * 6500)], run={"expect": ["NOPE"]})
        self.assertEqual(xrun_report.build(str(d))["result"], "PARTIAL")

    def test_qr2_log_replaces_snapshots(self):
        """QL2: snaps/qr2-<sid>.txt (the whole log, `[...gap...]` where units are missing) + .json: coverage line, gaps, times, from-boot."""
        d = self.run_dir(snaps=[], run={"expect": ["SWEET TOUCH: gs101", "NOPE"]})
        text = LOG2 + "[...gap...]\nlast line\n"
        runs = [[0, T0 + 30], [LOG1.count("\n"), T0 + 90]]
        (d / "snaps" / "qr2-1a2b.txt").write_text(text)
        (d / "snaps" / "qr2-1a2b.json").write_text(json.dumps({
            "sid": "1a2b", "start": 0, "lo": 0, "bytes": len(text), "total": len(text) + 4096, "gaps": [[len(text) - 11, 4096]], "blocks": 40,
            "hi": 41, "frames": 300, "dups": 120, "t_first": T0 + 30, "t_last": T0 + 100, "line_times": runs}))
        r = xrun_report.build(str(d))
        self.assertIn("QL2 1 session(s)", r["coverage"]["text"])
        self.assertIn("1 gap(s)", r["coverage"]["text"])
        self.assertTrue(r["coverage"]["from_boot"])
        self.assertEqual(r["coverage"]["gaps"], 1)
        found = r["expect"][0]
        self.assertEqual(found["line"], "SWEET TOUCH: gs101 FTM5 driver, mode 0, starting in 5 s")
        self.assertEqual(round(found["t"]), 90)                    # the line arrived with the second part
        self.assertEqual(r["result"], "PARTIAL")                   # NOPE missing, but a gap means the line may be in it
        d2 = self.run_dir("20260101T000200Z-t", snaps=[], run={"expect": ["NOPE"]})
        (d2 / "snaps" / "qr2-0001.txt").write_text(LOG1)
        (d2 / "snaps" / "qr2-0001.json").write_text(json.dumps({"sid": "0001", "start": 4096, "lo": 3, "bytes": 5, "total": 9, "gaps": []}))
        r2 = xrun_report.build(str(d2))
        self.assertFalse(r2["coverage"]["from_boot"])              # ring overwritten: not from the first byte
        self.assertEqual(r2["result"], "PARTIAL")

    def test_error_and_cli_roundtrip(self):
        d = self.run_dir(snaps=[])
        out = subprocess.run([sys.executable, str(HERE / "xrun_report.py"), "error", str(d), "needs Power+VolDown hold"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertTrue(out.stdout.startswith("RESULT: ERROR needs Power+VolDown hold"))
        self.assertEqual(json.loads((d / "report.json").read_text())["exit"], 2)
        again = subprocess.run([str(HERE / "xrun"), "--dry-run", "--from-dir", str(d)], capture_output=True, text=True)
        self.assertEqual(again.returncode, 2)



class AllGaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gaps-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.kn = pathlib.Path(self.tmp, "k.md")
        self.kn.write_text("| gap | first seen | count | effect |\n|---|---|---|---|\n| surface sel 9 (accepted and ignored) | g95 | 1 | x |\n")
        self.wk = pathlib.Path(self.tmp, "w.md")
        self.wk.write_text("| gap | first seen | effect | severity |\n|---|---|---|---|\n| f1 console pointer outside the RAM window, console not read | F1 | obs | medium |\n| stop W<row> STOP: <error name> | F2 | obs | high |\n")
        for n, v in (("GAPS_FILE_ALL", self.kn), ("GAPS_FILE", self.wk)):
            self.addCleanup(setattr, xrun_report, n, getattr(xrun_report, n))
            setattr(xrun_report, n, str(v))

    def gaps(self, lines):
        return xrun_report.all_gaps(lines, xrun_report.wifi_gaps(lines))

    def test_generic_keys_and_wifi(self):
        lines = ["SWEET GAP: surface sel 9 x1 (WeatherPoster)", "SWEET GAP: surface sel 9 x10 (WeatherPoster)",
                 "SWEET GAP: gpu fmt 0x4c unknown x1 (pid 123)",
                 "WIFI GAP: f1 console pointer outside the RAM window, console not read x1 [medium]",
                 "WIFI GAP: f1 console pointer outside the RAM window, console not read x4 [medium]",
                 "WIFI GAPS: 1 distinct, 4 total", "[ 12.345] qux"]
        g = self.gaps(lines)
        self.assertEqual((g["distinct"], g["total"]), (3, 15))      # sel 9 (largest x10) + gpu fmt (1) + WIFI f1 (largest x4)
        self.assertEqual(g["by_area"], {"SWEET": {"distinct": 2, "total": 11}, "WIFI": {"distinct": 1, "total": 4}})
        new = {e["gap"]: e["new"] for e in g["top"]}
        self.assertFalse(new["surface sel 9"])
        self.assertTrue(new["gpu fmt 0xN unknown"])
        self.assertFalse(new["f1 console pointer outside the RAM window, console not read"])
        self.assertLessEqual(len(g["top"][0]["example"]), 200)

    def test_n_wildcard_matches_digits_only(self):      # 21k el2-3-f7: kern.bootargs ENOMEM rows were NEW although registered
        self.kn.write_text("| gap | f | c | e |\n|---|---|---|---|\n| kern.bootargs ENOMEM to 256-byte buffer, line <N> chars, error <N> / kern.bootargs truncated to 608-byte buffer, line <N> chars, error <N> (log line x) | a | b | c |\n")
        g = self.gaps(["SWEET GAP: kern.bootargs ENOMEM to 256-byte buffer, line 277 chars, error 12 x4 (thermalmonitord pid 54)",
                       "SWEET GAP: kern.bootargs truncated to 608-byte buffer, line 926 chars, error 0 x1 (watchdogd pid 9)",
                       "SWEET GAP: kern.bootargs truncated to 127-byte buffer, line 926 chars, error 0 x1 (launchd pid 1)",     # the closed 0177 v1 regression stays NEW (review r1 F4)
                       "SWEET GAP: kern.bootargs ENOMEM to big-byte buffer, line 277 chars, error 12 x1 (a pid 2)",
                       "SWEET GAP: kern.bootargs ENOMEM to 256-byte buffer, line 277 chars, error 12 and more x1 (a pid 3)"])
        new = {e["gap"]: e["new"] for e in g["top"]}
        self.assertFalse(new["kern.bootargs ENOMEM to 256-byte buffer, line 277 chars, error 12"])
        self.assertFalse(new["kern.bootargs truncated to 608-byte buffer, line 926 chars, error 0"])
        self.assertTrue(new["kern.bootargs truncated to 127-byte buffer, line 926 chars, error 0"])
        self.assertTrue(new["kern.bootargs ENOMEM to big-byte buffer, line 277 chars, error 12"])
        self.assertTrue(new["kern.bootargs ENOMEM to 256-byte buffer, line 277 chars, error 12 and more"])     # a further word is NEW (rule 8)

    def test_f1_largest_counter(self):          # F1
        g = self.gaps(["SWEET GAP: a b c x1 (P)", "SWEET GAP: a b c x10 (P)", "SWEET GAP: a b c x100 (P)"])
        self.assertEqual((g["distinct"], g["total"]), (1, 100))
        g = self.gaps(["SWEET GAP: a b c x1000 (P)", "SWEET GAP: a b c x10 (Q)", "SWEET GAP: a b c x1 (R)", "SWEET GAP: no counter here", "SWEET GAP: no counter here"])
        self.assertEqual({e["gap"]: e["count"] for e in g["top"]}, {"a b c": 1011, "no counter here": 2})

    def test_f2_keep_identifiers(self):         # F2
        g = self.gaps(["SWEET GAP: surface sel 9 x1 (A)", "SWEET GAP: surface sel 27 x1 (A)", "SWEET GAP: ioservice AppleM2ScalerCSCDriver lookup x1 (backboardd) found 0",
                       "SWEET GAP: ioservice AppleM2ScalerCSCDriver lookup x1 (backboardd) found 3", "SWEET GAP: gpu fmt 0x4c x1 (pid 7)", "SWEET GAP: gpu fmt 0xff x1 (pid 8)",
                       "[   12.345] SWEET GAP: ts test x1 (A)"])
        self.assertEqual(sorted(e["gap"] for e in g["top"]), ["gpu fmt 0xN", "ioservice AppleM2ScalerCSCDriver lookup found N", "surface sel 27", "surface sel 9", "ts test"])

    def test_f3_new_marking(self):              # F3
        self.kn.write_text("| gap | a | b | c |\n|---|---|---|---|\n| surface sel 9 (accepted and ignored, reply zeroed) | g | 1 | x |\n"
                           "| surface sel 20 / sel 21 | g | 1 | x |\n| surface create key IOSurfaceNonPurgeable / IOSurfacePurgeWhenNotInUse | g | 1 | x |\n| two words | g | 1 | x |\n")
        g = self.gaps(["SWEET GAP: surface sel 9 (accepted and ignored, reply zeroed) x1 (A)", "SWEET GAP: surface sel 9 x1 (A)", "SWEET GAP: surface sel 27 SetValue key IOSurfaceName x1 (A)",
                       "SWEET GAP: surface sel 99 x1 (A)", "SWEET GAP: surface sel 21 x1 (A)", "SWEET GAP: surface create key IOSurfacePurgeWhenNotInUse x1 (A)", "SWEET GAP: two words extra x1 (A)",
                       "SWEET GAP: surface sel 9 SetValue key IOSurfaceName x1 (A)", "SWEET GAP: surface sel 90 x1 (A)"])
        new = {e["gap"]: e["new"] for e in g["top"]}
        self.assertFalse(new["surface sel 9 (accepted and ignored, reply zeroed)"])
        self.assertFalse(new["surface sel 9"])
        self.assertFalse(new["surface sel 21"])
        self.assertFalse(new["surface create key IOSurfacePurgeWhenNotInUse"])
        self.assertTrue(new["surface sel 27 SetValue key IOSurfaceName"])
        self.assertTrue(new["surface sel 99"])
        self.assertTrue(new["surface sel 90"])                       # digit boundary: "sel 9" must not match "sel 90"
        self.assertTrue(new["two words extra"])                      # cell shorter than 3 words never matches
        self.assertTrue(new["surface sel 9 SetValue key IOSurfaceName"])

    def test_f4_procs(self):                    # F4
        g = self.gaps(["SWEET GAP: a b c x1 (SpringBoard)", "SWEET GAP: a b c x1 (backboardd pid 5)", "SWEET GAP: a b c x1 (WeatherPoster)"])
        self.assertEqual(g["distinct"], 1)
        self.assertEqual(g["top"][0]["procs"], ["SpringBoard", "WeatherPoster", "backboardd"])
        self.assertEqual(len(self.gaps(["SWEET GAP: a b c x1 (P%d)" % i for i in range(8)])["top"][0]["procs"]), 5)

    def test_f5_usbport(self):                  # F5
        d = pathlib.Path(self.tmp, "usb")
        make_run(d, [(5, LOG1)], run={"usb_port": "USBPORT GAP: UNCHECKED (--no-flash-check, phone off the bus)"})
        r = xrun_report.build(str(d))
        self.assertEqual(r["gaps"]["by_area"], {"USBPORT": {"distinct": 1, "total": 1}})
        self.assertTrue(xrun_report.render(r).splitlines()[0].endswith(" gaps=1"))

    def test_xnu_build_mark(self):              # R26: the ledger line from tools/xrun is shown in the run summary and report.json
        d = pathlib.Path(self.tmp, "xnub")
        make_run(d, [(5, LOG1)], run={"xnu_build": "XNU BUILD: build-slot (label gate-x)"})
        r = xrun_report.build(str(d))
        self.assertEqual(r["run"]["xnu_build"], "XNU BUILD: build-slot (label gate-x)")
        self.assertIn("\nXNU BUILD: build-slot (label gate-x)\n", xrun_report.render(r))
        d2 = pathlib.Path(self.tmp, "xnub2")
        make_run(d2, [(5, LOG1)])
        self.assertNotIn("XNU BUILD", xrun_report.render(xrun_report.build(str(d2))))

    def test_f6_anchor(self):                   # F6
        g = self.gaps(["[ 12.345] SWEET GAP: real one x1 (A)", "[cpu1] [ 1.0] SWEET GAP: second x1 (A)", "SWEET GAP: third x1 (A)",
                       "--expect 'SWEET GAP: quoted' foo", "garbage SWEET GAP: mid line x1", "SWEET GAPS: 3 distinct", "lower GAP: x"])
        self.assertEqual(sorted(e["gap"] for e in g["top"]), ["real one", "second", "third"])

    def test_mutant_killers(self):
        lines = ["SWEET GAP: gap number %s x%d (A)" % (chr(97 + i), 100 - i) for i in range(12)]
        d = pathlib.Path(self.tmp, "twelve")
        make_run(d, [(5, LOG1 + "\n".join(lines) + "\n")])
        r = xrun_report.build(str(d))
        txt = xrun_report.render(r)
        self.assertEqual(txt.count("\n  SWEET GAP: gap number "), 10)        # top-10 cap
        self.assertIn("+2 more gap(s) in report.json", txt)
        self.assertEqual(len(r["gaps"]["top"]), 12)
        self.assertIn("wifi_gaps", r)                               # legacy key
        # missing KNOWN-GAPS.md: message and no NEW
        self.addCleanup(setattr, xrun_report, "GAPS_FILE_ALL", xrun_report.GAPS_FILE_ALL)
        xrun_report.GAPS_FILE_ALL = str(pathlib.Path(self.tmp, "absent.md"))
        r = xrun_report.build(str(d))
        self.assertIn("docs/KNOWN-GAPS.md not found", xrun_report.render(r))
        self.assertFalse(any(e["new"] for e in r["gaps"]["top"]))
        # trailing whitespace after the severity still parses as the patch-0137 format (GAP_RX \s*$)
        w = xrun_report.wifi_gaps(["WIFI GAP: f1 q x2 [low]  \t"])
        self.assertEqual((w["distinct"], w["top"][0]["count"]), (1, 2))
        self.assertEqual(xrun_report.wifi_gaps(["WIFI GAP: f1 q x2 [low] trailing junk"])["distinct"], 0)    # `$` anchor: not the 0137 format
        g = self.gaps(["WIFI GAP: f1 q x2 [low]  \t"])
        self.assertEqual(g["total"], 2)

    def test_run_dir_result_line(self):
        for extra, want in (("", " gaps=0"), ("SWEET GAP: surface sel 9 x1 (A)\nSWEET GAP: surface sel 9 x10 (A)\nSWEET GAP: other thing x1 (A)\nWIFI GAP: f1 q x2 [low]\n", " gaps=3")):
            d = pathlib.Path(self.tmp, "run" + str(len(extra)))
            make_run(d, [(5, LOG1 + extra)])
            r = xrun_report.build(str(d))
            self.assertTrue(xrun_report.render(r).splitlines()[0].endswith(want), xrun_report.render(r).splitlines()[0])
            self.assertIn("GAPS: %s distinct" % want[-1], xrun_report.render(r))
            self.assertEqual(r["gaps"]["distinct"], int(want[-1]))


class ReadUsbnet(unittest.TestCase):
    def test_trailing_newline_is_no_gap_but_garbage_still_is(self):   # T-3e5e: 304 GAP lines per boot came from the trailing "" of split("\n")
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "u.log"
            f.write_text("1.5\tone\n2.5\ttwo\n\n", encoding="latin-1")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(xrun_report.read_usbnet(str(f)), (["one", "two"], [1.5, 2.5]))
            self.assertEqual(err.getvalue(), "")
            f.write_text("1.5\tone\ngarbage\n", encoding="latin-1")
            with contextlib.redirect_stderr(err):
                self.assertEqual(xrun_report.read_usbnet(str(f)), (["one"], [1.5]))
            self.assertIn("xrun_report GAP: ValueError", err.getvalue())



class PanicOverUsb(unittest.TestCase):      # T-9d99, patch 0242: PXPANIC datagrams in usbnet.log -> PANIC: lines, frames out of the run log
    def make(self, d, dgrams, live="SWEET FAULT USB: on (patch 0242)\nSWEET FAULT TEST: firing now (test fault after 20 s, test only)\n"):
        import panic_capture
        make_run(d, [], run={"usbnet": "1"})
        text = live + "".join(dgrams)
        rows = ["%.3f\t%s" % (T0 + 30 + i * 0.001, ln) for i, ln in enumerate(text.split("\n")[:-1])]     # as tools/usbnet-log.py writes it
        (pathlib.Path(d) / "usbnet.log").write_text("\n".join(rows) + "\n", encoding="latin-1")
        return panic_capture

    def test_panic_lines_and_clean_log(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "r"
            import panic_capture as pc
            self.make(d, pc.datagrams(pc.full_text(pc.FIX_CAP, pc.FIX_TAIL), mx=150))
            r = xrun_report.build(str(d))
            out = xrun_report.render(r)
            self.assertIn("PANIC: string panic(cpu 0 caller 0xfffffe0012345678): Invalid kernel stack pointer", out)
            self.assertIn("PANIC: regs pc=0xfffffe0011112222 lr=0xfffffe0011113333", out)
            self.assertIn("PANIC: backtrace 2 frames (lr): 0xfffffe0010000100 0xfffffe0010000200", out)
            self.assertIn("PANIC: tail [  610.701] DP15 ring 3 full", out)
            self.assertEqual(r["result"], "FAIL")       # a panic stays a FAIL: the capture explains it, it does not excuse it
            merged = (d / "merged.log").read_text(encoding="latin-1")
            self.assertNotIn("PXPANIC", merged)
            self.assertNotIn("DP15 ring 3 full", merged)       # the tail repeats live lines: not in the run log
            self.assertIn("\t  pc:  0xfffffe0011112222 cpsr", merged)   # the panic log itself is, as whole lines

    def test_panic_seen_nothing_received_is_a_gap(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "r"
            self.make(d, [])
            out = xrun_report.render(xrun_report.build(str(d)))
            self.assertIn("PANIC GAP: panic seen but no panic capture: the kernel has patch 0242", out)

    def test_no_panic_no_lines(self):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "r"
            self.make(d, [], live="SWEET FAULT USB: on (patch 0242)\nboot fine\n")
            self.assertNotIn("PANIC", xrun_report.render(xrun_report.build(str(d))))


if __name__ == "__main__":
    unittest.main()
