#!/usr/bin/env python3
"""Closed-loop replay of the recorded pixnav failures: host pixnav (-DPIXNAV_HOST, -X) against tools/pixnav-sim on SYNTHETIC panels (no phone, no
private data). ALL-ON T-31 lane-nav 2026-11-12.

Every scenario of tools/pixnav-sim (calm, r47f9 widget change + slow launches, r8a63/r758e lock start, rstress1 location alert, rstress2 Settings
on a sub-page, rtap eaten tap, rtoday Today View, rall = all) x seeds 1-4 must give: rc 0, 'done reps 3 (pixnav: skipped 0', every ACTION begins on
a confirmed start screen, no gesture while the last look was unknown except the bottom home swipe, no tap anywhere but a confirmed start screen
or a confirmed alert's Cancel, no widget-row tap and no alert left on (simulator report).
Negative control: the changed-widget homeA is farther than homeA's ceiling WITHOUT the mask (the 47f9 failure), and classified homeA with it.
Last line: RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import importlib.machinery, importlib.util, json, os, re, shutil, struct, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_l = importlib.machinery.SourceFileLoader("navcls", os.path.join(HERE, "nav-classify"))
_s = importlib.util.spec_from_loader("navcls", _l)
nc = importlib.util.module_from_spec(_s)
_l.exec_module(nc)

W, H = 270, 600
LABELS = {"homeA": "c01-home-page-prev", "homeB": "c00-home-page-next", "settings": "c02-settings-open", "general": "c03-general-open",
          "about": "c04-about-open", "clock": "c12-app-launch", "spotlight": "c14-spotlight-open"}
PRE = {"home-page-next": "homeA", "home-page-prev": "homeB", "settings-open": "homeA", "general-open": "settings", "about-open": "general",
       "about-scroll-up": "about", "about-scroll-down": "about", "about-back": "about", "general-back": "general", "settings-scroll-up": "settings",
       "settings-scroll-down": "settings", "app-home": "settings", "app-launch": "homeA", "app-idle": "clock", "app-close": "clock",
       "spotlight-open": "homeA", "spotlight-close": "spotlight"}
SCEN = ("calm", "r47f9", "r8a63", "r758e", "rstress1", "rstress2", "rtap", "rtoday", "rall")
HOME_SWIPE = re.compile(r"PIXNAV REPLAY gesture \d+ 540,2398 -> 540,1200 ")


def panel(path, state, v, label="x"):
    """synthetic half-resolution ring slot; v = capture-run variant (small shifts). Extra screens: lock, today, sub (a Settings sub-page),
    alert (the location alert over a Settings page), homeA-news (homeA with the top-right widget black: job 47f9)"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(im)
    o = v * 2
    if state.startswith("homeA") or state in ("homeB", "spotlight"):
        bg = {"homeB": (160, 60, 40), "spotlight": (90, 90, 120)}.get(state, (40, 90, 160))
        dr.rectangle((0, 0, W, H), fill=bg)
        if state.startswith("homeA"):   # widget row y 8.5-27.5 %: weather left, smart stack right
            dr.rectangle((10, 51, 128, 165), fill=(120, 140, 170))
            dr.rectangle((143, 51, 258, 165), fill=(28, 28, 30) if state == "homeA-news" else (250, 250, 250))
        for i in range(16):
            x, y = 20 + (i % 4) * 60, 190 + o + (i // 4) * 90
            dr.rectangle((x, y, x + 40, y + 40), fill=(230, 200, 60))
        if state == "spotlight":
            dr.rectangle((10, 380, 260, 600), fill=(200, 200, 205))
    elif state == "clock":
        dr.rectangle((0, 0, W, H), fill=(0, 0, 0))
        dr.rectangle((20, 120 + o, 250, 160 + o), fill=(250, 250, 250))
    elif state == "lock":
        dr.rectangle((0, 0, W, H), fill=(70, 70, 90))
        dr.rectangle((60, 90 + o, 210, 170 + o), fill=(240, 240, 240))
    elif state == "today":
        dr.rectangle((0, 0, W, H), fill=(60, 60, 70))
        dr.rectangle((10, 60, 130, 180), fill=(150, 150, 155))
        dr.rectangle((10, 330, 260, 570), fill=(250, 250, 250))
    else:
        title = {"settings": 3, "general": 6, "about": 9, "sub": 12, "alert": 3}[state]
        for i in range(12):
            dr.rectangle((5, 70 + o + i * 42, W - 5, 100 + o + i * 42), fill=(242, 242, 247))
        dr.rectangle((W // 2 - title * 4, int(H * .05), W // 2 + title * 4, int(H * .07)), fill=(0, 0, 0))
        if state in ("settings", "alert"):
            dr.rectangle((20, 330, 107, 360), fill=(90, 60, 220))
            dr.rectangle((20, 420, 107, 460), fill=(150, 150, 152))
        if state == "alert":   # dim + the box (x 13-87 %, y 41.5-59.5 %), title lines, two blue buttons
            im = Image.eval(im, lambda c: int(c * .6))
            dr = ImageDraw.Draw(im)
            dr.rectangle((35, 249, 236, 357), fill=(235, 235, 238))
            for k in range(3):
                dr.rectangle((55, 262 + k * 14, 215 - k * 20, 270 + k * 14), fill=(20, 20, 20))
            dr.rectangle((60, 338, 110, 346), fill=(10, 100, 250))
            dr.rectangle((166, 338, 205, 346), fill=(10, 100, 250))
    raw = im.convert("RGBA").tobytes("raw", "BGRA")
    hdr = struct.pack("<8I", struct.unpack("<I", b"SPNH")[0], W, H, W * 4, 0, 0, 1, 1) + label.encode().ljust(48, b"\0")
    open(path, "wb").write(hdr + raw)


class Sim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pixnav-sim-test-")
        d = cls.tmp.name
        cls.exe = os.path.join(d, "pixnav")
        cc = shutil.which("gcc") or shutil.which("cc")
        r = subprocess.run([cc, "-DPIXNAV_HOST", "-O2", "-Wall", "-Wextra", "-Werror", "-o", cls.exe, os.path.join(HERE, "pixnav.c")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("host build failed: " + r.stderr[-2000:])
        caps, lock, alert = [], [], []
        for run in range(3):
            c = os.path.join(d, "cap%d" % run)
            os.mkdir(c)
            for i, st in enumerate(LABELS):
                panel(os.path.join(c, "snap-%02d.raw" % i), st, run, LABELS[st])
            caps.append(c)
            for nm, lst in (("lock", lock), ("alert", alert)):
                q = os.path.join(d, "%s%d" % (nm, run))
                os.mkdir(q)
                panel(os.path.join(q, "p.raw"), nm, run)
                lst.append(q)
        cls.refs = os.path.join(d, "refs.bin")
        r = subprocess.run([sys.executable, os.path.join(HERE, "nav-classify"), "refs-bin", cls.refs] + caps + ["--as", "lock"] + lock +
                           ["--as", "alert-loc"] + alert, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("refs-bin failed: " + r.stdout + r.stderr)
        live = os.path.join(d, "live")
        os.mkdir(live)
        cls.map = {}
        for st in list(LABELS) + ["lock", "today", "sub", "alert", "homeA-news"]:
            f = os.path.join(live, st + ".raw")
            panel(f, st, 4)   # a run the references never saw
            cls.map[st.replace("homeA-news", "homeA:news")] = f
        cls.mapf = os.path.join(d, "panels.json")
        json.dump(cls.map, open(cls.mapf, "w"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_sim(self, scen, seed, extra=()):
        rep = os.path.join(self.tmp.name, "rep-%s-%d.json" % (scen, seed))
        cmd = "%s %s --panels %s --scenario %s --report %s" % (sys.executable, os.path.join(HERE, "pixnav-sim"), self.mapf, scen, rep)
        r = subprocess.run([self.exe, "-r", "3", "-s", str(seed), "-R", self.refs, "-X", cmd] + list(extra), capture_output=True, text=True,
                           timeout=120)
        return r.returncode, r.stdout.splitlines(), json.load(open(rep))

    def check(self, scen, seed):
        rc, out, rep = self.run_sim(scen, seed)
        tail = "\n".join(out[-25:])
        self.assertEqual(rc, 0, "%s seed %d\n%s" % (scen, seed, tail))
        self.assertIn("done reps 3 (pixnav: skipped 0", out[-1])
        last = None
        for ln in out:
            m = re.search(r"SWEET NAV: STATE (\S+)", ln)
            if m:
                last = m.group(1)
            m = re.search(r"SWEET NAV: ACTION (\S+) begin", ln)
            if m:
                self.assertEqual(last, PRE[m.group(1)], "%s seed %d: %s began on %s" % (scen, seed, m.group(1), last))
            m = re.search(r"PIXNAV REPLAY gesture \d+ (\d+),(\d+) -> (\d+),(\d+)", ln)
            if m:
                if last == "unknown":
                    self.assertRegex(ln, HOME_SWIPE, "%s seed %d: a gesture other than the home swipe on an unknown screen" % (scen, seed))
                x0, y0, x1, y1 = map(int, m.groups())
                if (x0, y0) == (x1, y1):
                    self.assertIn(last, set(PRE.values()) | {"alert-loc"}, "%s seed %d: tap on %s" % (scen, seed, last))
                    if last == "alert-loc":
                        self.assertEqual((x0, y0), (743, 1372))
        self.assertEqual(rep["widget_taps"], 0, scen)
        self.assertFalse(rep["alert"], "%s seed %d: alert left on top" % (scen, seed))
        return out, rep

    def test_every_recorded_scenario(self):
        for scen in SCEN:
            for seed in (1, 2, 3, 4):
                with self.subTest(scen=scen, seed=seed):
                    self.check(scen, seed)

    def test_injections_really_happen(self):   # the scenarios must exercise what they claim
        out, rep = self.check("rall", 1)
        ev = [e[2] for e in rep["log"] if e[1] == "event"]
        self.assertTrue(any("widget" in e for e in ev) and any("Today" in e for e in ev) and any("alert" in e for e in ev), ev)
        self.assertTrue(any("DISMISS alert-loc" in l for l in out))
        out, rep = self.check("r8a63", 1)
        self.assertTrue(any("RECOVER want homeA have lock" in l for l in out))
        self.assertTrue(any(l.startswith("SWEET NAV: INTENT open") for l in out))

    def test_unknown_dump_roundtrip(self):   # -U FILE: distinct unknown screens kept once each, split back into panels
        dump = os.path.join(self.tmp.name, "unk.bin")
        rc, out, rep = self.run_sim("rall", 2, ["-U", dump])
        self.assertEqual(rc, 0, "\n".join(out[-10:]))
        kept = [l for l in out if "SWEET NAV: UNKNOWN kept" in l]
        self.assertGreaterEqual(len(kept), 2, "the sub-page and the Today View are unknown screens")
        d = os.path.join(self.tmp.name, "unk")
        r = subprocess.run([sys.executable, os.path.join(HERE, "nav-classify"), "split-dump", dump, d], capture_output=True, text=True)
        self.assertIn("RESULT: PASS nav-classify split-dump %d panel(s)" % len(kept), r.stdout)
        gs = [nc.features(nc.read_panel(os.path.join(d, f))[1]).g for f in sorted(os.listdir(d))]
        for i in range(len(gs)):
            for j in range(i):
                self.assertGreaterEqual(nc.l1(gs[i], gs[j]), 1.0, "a duplicate unknown panel was kept")

    def test_heavy_bench_no_settle_and_confirmed_gestures(self):   # T-54 stage 1: -B, no settle waits, every gesture allowed by its last look
        for scen in ("calm", "rall", "r8a63"):
            with self.subTest(scen=scen):
                rep = os.path.join(self.tmp.name, "hv-%s.json" % scen)
                cmd = "%s %s --panels %s --scenario %s --report %s" % (sys.executable, os.path.join(HERE, "pixnav-sim"), self.mapf, scen, rep)
                r = subprocess.run([self.exe, "-B", "120", "-s", "3", "-R", self.refs, "-X", cmd, "-b", "/usr/local/bin/pixperf,-t,700"],
                                   capture_output=True, text=True, timeout=300)
                out = r.stdout.splitlines()
                self.assertEqual(r.returncode, 0, "\n".join(out[-15:]))
                done = [l for l in out if "SWEET NAV: HEAVY done" in l]
                self.assertTrue(done, "\n".join(out[-15:]))
                self.assertGreaterEqual(int(re.search(r"secs (\d+)", done[0]).group(1)), 120)
                nb = int(re.search(r"blocks (\d+)", done[0]).group(1))
                self.assertGreaterEqual(nb, 40)
                self.assertLessEqual(nb, 120 * 3, "%s: %d blocks in 120 s = a loop without pacing (an intent storm)" % (scen, nb))
                self.assertLessEqual(int(re.search(r"intents (\d+)", done[0]).group(1)), 120 * 4, "%s: intent storm" % scen)
                self.assertFalse(any("SETTLE" in l or "settled" in l for l in out), "heavy mode must not wait for a settled screen")
                # every switch-storm intent differs from the intent before it (a repeat draws nothing: user 2026-11-14)
                urls, in_storm, reps, ctx, locked = [], False, 0, [], False
                for k, ln in enumerate(out):
                    g = re.search(r"SWEET NAV: GLANCE (\S+)", ln)
                    if g: locked = g.group(1) == "lock"   # an intent from the lock screen unlocks: the screen changes, not a repeat
                    if "ACTION hv-switch-storm begin" in ln: in_storm = True
                    elif "ACTION hv-switch-storm inputend" in ln: in_storm = False
                    m = re.search(r"SWEET NAV: INTENT open (\S+) now", ln)
                    if m:
                        if in_storm and not locked and urls and urls[-1] == m.group(1): reps += 1; ctx += out[max(0, k - 4):k + 1]
                        urls.append(m.group(1))
                self.assertEqual(reps, 0, "%s: %d switch-storm intents repeat the intent before them:\n%s" % (scen, reps, "\n".join(ctx)))
                last = None
                for ln in out:
                    m = re.search(r"SWEET NAV: (?:GLANCE|STATE) (\S+)", ln)
                    if m:
                        last = m.group(1)
                    m = re.search(r"PIXNAV REPLAY gesture \d+ (\d+),(\d+) -> (\d+),(\d+)", ln)
                    if not m:
                        continue
                    x0, y0, x1, y1 = map(int, m.groups())
                    if y0 == 2398:
                        continue   # the bottom home swipe: position-agnostic
                    if (x0, y0) == (x1, y1):
                        self.assertEqual((last, x0, y0), ("alert-loc", 743, 1372), "%s: a tap on %s" % (scen, last))
                    elif x0 == 5:
                        self.assertIn(last, ("about", "general"), "%s: edge back-swipe on %s" % (scen, last))
                    elif abs(x1 - x0) > abs(y1 - y0):
                        self.assertIn(last, ("homeA", "homeB"), "%s: page flick on %s" % (scen, last))
                    else:
                        self.assertIn(last, ("settings", "general", "about", "homeA", "homeB"), "%s: fling/pull on %s" % (scen, last))
                self.assertFalse(json.load(open(rep))["alert"], "%s: alert left on" % scen)

    def test_mask_is_what_fixes_47f9(self):
        refs, ceil = nc.load_bin(self.refs)
        f = nc.features(nc.read_panel(self.map["homeA:news"])[1])
        st = nc.classify(f, refs, ceil)[0]
        self.assertEqual(st, "homeA")
        unmasked = min(nc.l1(f.g, r["g"]) for r in refs if r["state"] == "homeA")
        self.assertGreater(unmasked, ceil["homeA"], "the synthetic widget change must reproduce the 47f9 distance problem without the mask")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(Sim))
    print("RESULT: %s tests=%d failures=%d errors=%d skipped=%d" % ("PASS" if res.wasSuccessful() else "FAIL", res.testsRun, len(res.failures),
                                                                   len(res.errors), len(res.skipped)))
    sys.exit(0 if res.wasSuccessful() else 1)
