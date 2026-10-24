import os
import contextlib, io, json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xrun_report

T0 = 1_800_000_000.0        # fixed "boot command" time of the synthetic run
try:
    from PIL import Image
except ImportError:
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
        self.assertIn("phone-state", r["reason"])

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

    def test_reset_text_names_what_the_phone_came_back_as(self):
        d = self.run_dir("20260101T000600Z-r", snaps=[(30, LOG1)], run={"restart": "60,bootloader", "expect": ["SWEET CPU"]})
        xrun_report.mark(str(d), ["usb_back=%f" % (T0 + 150), "usb_back_id=18d1:4ee0"])
        r = xrun_report.build(str(d))
        self.assertEqual(r["result"], "PASS", r["reason"])
        self.assertIn("back as fastboot", r["reason"])

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


if __name__ == "__main__":
    unittest.main()
