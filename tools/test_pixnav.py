#!/usr/bin/env python3
"""Tests for tools/pixnav.c (host build -DPIXNAV_HOST, synthetic panels, no phone, no private data).

1. parity: pixnav -F classifies every synthetic panel exactly like tools/nav-classify (state, coarse distance, band to 0.05);
2. (moved) the T-26 / T-30 replays run closed-loop in tools/test_pixnav_sim.py;
3. a scenario where the start screen never comes: the action is skipped with a GAP and pixnav exits 1.
Last line: RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import importlib.machinery, importlib.util, os, re, shutil, struct, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_l = importlib.machinery.SourceFileLoader("navcls", os.path.join(HERE, "nav-classify"))
_s = importlib.util.spec_from_loader("navcls", _l)
nc = importlib.util.module_from_spec(_s)
_l.exec_module(nc)

W, H = 270, 600
LABELS = {"homeA": "c01-home-page-prev", "homeB": "c00-home-page-next", "settings": "c02-settings-open", "general": "c03-general-open",
          "about": "c04-about-open", "clock": "c12-app-launch", "spotlight": "c14-spotlight-open", "applib": "c20-applib-open"}
PRE = {"home-page-next": "homeA", "home-page-prev": "homeB", "settings-open": "homeA", "general-open": "settings", "about-open": "general",
       "about-scroll-up": "about", "about-scroll-down": "about", "about-back": "about", "general-back": "general", "settings-scroll-up": "settings",
       "settings-scroll-down": "settings", "app-home": "settings", "app-launch": "homeA", "app-idle": "clock", "app-close": "clock",
       "spotlight-open": "homeA", "spotlight-close": "spotlight"}
NORMAL = [("homeA", "homeB"), ("homeB", "homeA"), ("homeA", "settings"), ("settings", "general"), ("general", "about"), ("about", "about"),
          ("about", "about"), ("about", "general"), ("general", "settings"), ("settings", "settings"), ("settings", "settings"),
          ("settings", "homeA"), ("homeA", "clock"), ("clock", "clock"), ("clock", "homeA"), ("homeA", "spotlight"), ("spotlight", "homeA")]


def panel(path, state, variant, label=None):
    """A synthetic half-resolution ring slot (80 B header + label): each state has its own layout and nav-bar title block; `variant`
    shifts the content a little (another capture run). The settings screen carries a purple icon row above a grey one (the General probe)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(im)
    v = variant * 2
    if state.startswith("home") or state in ("applib", "spotlight"):
        bg = {"homeA": (40, 90, 160), "homeB": (160, 60, 40), "applib": (20, 20, 20), "spotlight": (90, 90, 120)}[state]
        dr.rectangle((0, 0, W, H), fill=bg)
        for i in range(16 if state != "homeB" else 20):
            x, y = 20 + (i % 4) * 60, 80 + v + (i // 4) * 90 + (30 if state == "homeA" and i < 4 else 0)
            dr.rectangle((x, y, x + 40, y + 40), fill=(230, 200, 60) if state != "applib" else (60, 200, 230))
    elif state == "clock":
        dr.rectangle((0, 0, W, H), fill=(0, 0, 0))
        dr.rectangle((20, 120 + v, 250, 160 + v), fill=(250, 250, 250))
    else:
        title = {"settings": 3, "general": 6, "about": 9, "unknown": 12}[state]
        for i in range(12):
            dr.rectangle((5, 70 + v + i * 42, W - 5, 100 + v + i * 42), fill=(242, 242, 247))
        dr.rectangle((W // 2 - title * 4, int(H * .05), W // 2 + title * 4, int(H * .07)), fill=(0, 0, 0))
        if state == "settings":   # icon column (touch x 40..215 = panel x 20..107 at half resolution): purple row block, then grey (General)
            dr.rectangle((20, 330, 107, 360), fill=(90, 60, 220))
            dr.rectangle((20, 420, 107, 460), fill=(150, 150, 152))
    raw = im.convert("RGBA").tobytes("raw", "BGRA")
    hdr = struct.pack("<8I", struct.unpack("<I", b"SPNH")[0], W, H, W * 4, 0, 0, 1, 1) + (label or LABELS.get(state, "x")).encode().ljust(48, b"\0")
    open(path, "wb").write(hdr + raw)


class PixnavHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pixnav-test-")
        d = cls.tmp.name
        cls.exe = os.path.join(d, "pixnav")
        cc = shutil.which("gcc") or shutil.which("cc")
        r = subprocess.run([cc, "-DPIXNAV_HOST", "-O2", "-Wall", "-Wextra", "-Werror", "-o", cls.exe, os.path.join(HERE, "pixnav.c")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("host build failed: " + r.stderr[-2000:])
        cls.caps = []
        for run in range(3):
            c = os.path.join(d, "cap%d" % run)
            os.mkdir(c)
            for i, st in enumerate(LABELS):
                panel(os.path.join(c, "snap-%02d.raw" % i), st, run)
            cls.caps.append(c)
        cls.refs = os.path.join(d, "refs.bin")
        r = subprocess.run([sys.executable, os.path.join(HERE, "nav-classify"), "refs-bin", cls.refs] + cls.caps, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("refs-bin failed: " + r.stdout + r.stderr)
        cls.p = {st: os.path.join(cls.caps[1], "snap-%02d.raw" % i) for i, st in enumerate(LABELS)}
        cls.p["unknown"] = os.path.join(d, "unknown.raw")
        panel(cls.p["unknown"], "unknown", 1, "x-unknown")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_scen(self, seq, reps="1"):
        sf = os.path.join(self.tmp.name, "scen.txt")
        open(sf, "w").write("\n".join(self.p[s] for s in seq) + "\n")
        r = subprocess.run([self.exe, "-r", reps, "-s", "7", "-R", self.refs, "-H", sf], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.splitlines()

    def no_blind(self, out):
        last = None
        for ln in out:
            m = re.search(r"SWEET NAV: STATE (\S+)", ln)
            if m:
                last = m.group(1)
            m = re.search(r"SWEET NAV: ACTION (\S+) begin", ln)
            if m:
                self.assertEqual(last, PRE[m.group(1)], "action %s began on %s" % (m.group(1), last))

    def test_parity_with_nav_classify(self):
        refs = [r for c in self.caps for r in nc.load_capture(c)]
        for st, f in self.p.items():
            py = nc.classify(nc.features(nc.read_panel(f)[1]), refs)
            out = subprocess.run([self.exe, "-R", self.refs, "-F", f], capture_output=True, text=True).stdout
            m = re.search(r"NAV STATE (\S+) d=([\d.]+) band=([\d.]+)", out)
            self.assertTrue(m, out)
            self.assertEqual(m.group(1), py[0], st)
            self.assertAlmostEqual(float(m.group(2)), py[1], delta=0.06)
            self.assertAlmostEqual(float(m.group(3)), py[2], delta=0.06)
        self.assertEqual(nc.classify(nc.features(nc.read_panel(self.p["unknown"])[1]), refs)[0], "unknown")

    # the T-26 (tap eaten) and T-30 (Settings on a sub-page) replays moved to tools/test_pixnav_sim.py (closed loop, scenarios rtap / rstress2):
    # since lane-nav 2026-11-12 pixnav waits up to POST_WAIT_MS for the post state, so a fixed open-loop panel list no longer fits

    def test_unreachable_start_is_skipped_loudly(self):
        seq = ["homeA", "homeB", "homeB", "homeA", "homeA", "settings", "unknown", "unknown", "unknown", "unknown", "unknown"]
        rc, out = self.run_scen(seq)
        self.assertEqual(rc, 1)
        self.assertTrue(any("SWEET GAP: NAV pixnav could not reach settings" in l for l in out), "\n".join(out[-15:]))
        self.no_blind(out)

    def run_script(self, seq, script):
        sf = os.path.join(self.tmp.name, "scen-s.txt")
        open(sf, "w").write("\n".join(self.p[s] for s in seq) + "\n")
        r = subprocess.run([self.exe, "-R", self.refs, "-H", sf, "-S", script], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.splitlines()

    def test_wait_succeeds_on_kth_panel(self):   # T-28 run3: W gates the intent on SpringBoard being back (lock/home), not on a timer
        rc, out = self.run_script(["unknown", "clock", "homeB", "homeA", "homeA"], "W:homeA|homeB,5000;I:prefs:root=General;L")
        self.assertEqual(rc, 0, "\n".join(out))
        ok = [i for i, l in enumerate(out) if re.search(r"SWEET NAV: WAIT homeB ok after \d+ ms", l)]
        self.assertEqual(len(ok), 1, "\n".join(out))
        self.assertEqual(sum("PIXNAV REPLAY panel" in l for l in out[:ok[0]]), 3, "wait must stop on the 3rd panel (k=3)")
        intent = [i for i, l in enumerate(out) if "INTENT open prefs:root=General" in l]
        self.assertTrue(intent and intent[0] > ok[0], "intent before the wait finished")
        self.assertIn("SWEET NAV: AB done (bad 0)", out[-1])
        self.assertFalse(any("PIXNAV GAP" in l for l in out))

    def test_wait_first_panel_matches(self):
        rc, out = self.run_script(["homeA"], "W:homeA,5000")
        self.assertEqual(rc, 0, "\n".join(out))
        self.assertTrue(any(re.search(r"SWEET NAV: WAIT homeA ok after \d+ ms", l) for l in out))

    def test_wait_times_out_and_stops_the_script(self):
        rc, out = self.run_script(["unknown"] * 40 + ["homeA"], "W:homeA|homeB,20;I:prefs:root=General;L")
        self.assertEqual(rc, 1, "\n".join(out))
        gap = [l for l in out if "PIXNAV GAP: wait homeA|homeB timed out after" in l]
        self.assertEqual(len(gap), 1, "\n".join(out))
        self.assertIn("(last unknown)", gap[0])
        self.assertFalse(any("INTENT open" in l for l in out), "a step after a failed wait must not run")
        self.assertFalse(any("AB done" in l for l in out))
        self.assertIn("SWEET NAV: AB aborted", out[-1])

    def test_wait_unknown_state_name_refused(self):
        rc, out = self.run_script(["homeA"], "W:homeA|nosuchstate,1000;L")
        self.assertEqual(rc, 1, "\n".join(out))
        self.assertTrue(any("PIXNAV GAP: wait state 'nosuchstate' is not a reference state" in l for l in out), "\n".join(out))
        self.assertFalse(any("PIXNAV REPLAY panel" in l for l in out), "a refused wait must not look")

    def test_load_end_reported_with_status(self):
        # a load child that exits rc 1 at once is reported with rc 1 (wait status 256); loads are not run on the host, so -E feeds the helper a status
        def line(spec):
            r = subprocess.run([self.exe, "-E", spec], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return r.stdout.strip()
        self.assertEqual(line("0,256,12,1,/bin/pixperf -s x"), "PIXNAV LOAD slot 0 pid 4242 ended rc 1 after 12 ms (end #1): /bin/pixperf -s x")
        self.assertIn("ended signal 9 after 30000 ms", line("2,9,30000,3,/bin/y"))
        self.assertIn("suppressed", line("0,256,12,11,/bin/pixperf"))   # bound: first 10, then every 10th
        self.assertIn("ended rc 1", line("0,256,12,20,/bin/pixperf"))
        self.assertIn("ended rc 1", line("0,256,12,10,/bin/pixperf"))


    def test_wait_malformed_refused(self):
        for bad in ("W:homeA", "W:homeA,", "W:homeA,0", "W:homeA,abc", "W:,100", "W:homeA,99999999"):
            rc, out = self.run_script(["homeA"], bad)
            self.assertEqual(rc, 1, bad)
            self.assertTrue(any("PIXNAV GAP: wait step" in l and "not understood" in l for l in out), "%s: %s" % (bad, out))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(PixnavHost))
    print("RESULT: %s tests=%d failures=%d errors=%d skipped=%d" % ("PASS" if res.wasSuccessful() else "FAIL", res.testsRun, len(res.failures),
                                                                   len(res.errors), len(res.skipped)))
    sys.exit(0 if res.wasSuccessful() else 1)
