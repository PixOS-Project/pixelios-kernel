#!/usr/bin/env python3
"""tools/gpu-nav-score v2 (T-b872): self-test, mutants (a required action that does not animate must FAIL), and the per-action camera frame hook of tools/qr-log.py."""
import importlib.util, importlib.machinery, os, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
SCORE = os.path.join(TOOLS, "gpu-nav-score")


def load(path, name):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, path))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, TOOLS)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, SCORE, "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_anim_required_action_fails(self):
        m = load(SCORE, "gns")
        got = []
        self.assertEqual(m.run(m.synth(drop_launch=True), 3, got.append), 1)
        self.assertIn("RESULT: FAIL gpu-nav unmeasured=app-launch(0/2)", got[-1])
        self.assertEqual(m.run(m.synth(drop_launch=True), 3, got.append, allow_unmeasured=True), 0)

    def test_idle_lead_gap_is_not_jank(self):
        m = load(SCORE, "gns2")
        got = []
        m.run(m.synth(), 3, got.append)
        self.assertTrue(any(x.startswith("1    page-scroll") and x.split()[-1] == "2000" for x in got), got)   # rep 1: the 2000 ms is lead_ms
        self.assertIn("worst_gap_ms=90 ", got[-1])

    def test_frames_required(self):
        m = load(SCORE, "gns3")
        with tempfile.TemporaryDirectory() as d:
            got = []
            self.assertEqual(m.run(m.synth(), 3, got.append, fdir=d, require_frames=True), 1)
            self.assertIn("frames=0/6", got[-1])
            for r, n in ((0, "page-scroll"), (0, "idle-tap"), (0, "app-launch"), (1, "page-scroll"), (1, "idle-tap"), (1, "app-launch")):
                open(os.path.join(d, "nav-r%02d-%s.jpg" % (r, n)), "wb").close()
            self.assertEqual(m.run(m.synth(), 3, got.append, fdir=d, require_frames=True), 0)
            self.assertTrue(got[-1].endswith(" frames=6/6 rate=distinct"), got[-1])   # synth() has no distinct4k: the default dist4k falls back (T-10ba)

    def test_rate_default_is_dist4k_and_differs_from_distinct(self):
        m = load(SCORE, "gns4")
        raw = m.synth(dense=True)
        d4, d32, dflt = [], [], []
        self.assertEqual(m.run(raw, 3, dflt.append), 0)
        self.assertEqual(m.run(raw, 3, d4.append, rate="dist4k"), 0)
        self.assertEqual(m.run(raw, 3, d32.append, rate="distinct"), 0)
        self.assertEqual(dflt[-1], d4[-1])                                              # default = dist4k
        self.assertTrue(d4[-1].endswith(" rate=dist4k") and d32[-1].endswith(" rate=distinct"))
        self.assertIn("p50_min=41.0 ", d4[-1])                                          # the dense series ...
        self.assertIn("p50_min=31.0 ", d32[-1])                                         # ... against the 32x32 one: the p50 differs
        self.assertFalse(any(x.startswith("NOTE:") for x in d4))

    def test_rate_falls_back_with_a_note_without_distinct4k(self):
        m = load(SCORE, "gns5")
        got = []
        self.assertEqual(m.run(m.synth(), 3, got.append), 0)
        self.assertTrue(any(x.startswith("NOTE: --rate dist4k asked") for x in got), got)
        self.assertTrue(got[-1].endswith(" rate=distinct"), got[-1])

    def test_rate_cli_option(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "usbnet.log")
            m = load(SCORE, "gns6")
            with open(log, "w") as f:
                f.write(m.synth(dense=True))
            def last(*a):
                r = subprocess.run([sys.executable, "-I", SCORE, log, *a], capture_output=True, text=True)
                return r.returncode, r.stdout.strip().splitlines()[-1]
            self.assertEqual(last(), (0, last("--rate", "dist4k")[1]))
            self.assertTrue(last("--rate", "distinct")[1].endswith(" rate=distinct"))
            rc, line = last("--rate", "bogus")
            self.assertEqual(rc, 2)
            self.assertIn("RESULT: FAIL gpu-nav --rate bogus", line)

    def test_navsnap_one_frame_per_end_marker(self):
        q = load(os.path.join(TOOLS, "qr-log.py"), "qrlog_nav")
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "usbnet.log")
            clock = [100.0]
            ns = q.NavSnap(log, os.path.join(d, "nav"), lag=0.5, now=lambda: clock[0])
            saved = []
            put = lambda p, i: saved.append(os.path.basename(p))
            ns.step("img0", put)                                                     # no log yet: nothing, no crash
            with open(log, "wb") as f:
                f.write(b"1.0\tSWEET NAV: ACTION settings-open begin rep 0 now 1\n1.1\tSWEET NAV: ACTION settings-open end rep 0 now 2\n1.2\tSWEET NAV: ACTION general-op")
            ns.step("img1", put)                                                     # marker read, lag not over: nothing yet
            self.assertEqual(saved, [])
            clock[0] = 100.6
            ns.step("img2", put)
            self.assertEqual(saved, ["nav-r00-settings-open.jpg"])
            with open(log, "ab") as f:
                f.write(b"en end rep 0 now 3\n")                                    # the partial line completes: one more marker
            clock[0] = 101.0
            ns.step("img3", put)
            clock[0] = 101.6
            ns.step("img4", put)
            ns.step("img5", put)                                                     # nothing pending: no duplicate
            self.assertEqual(saved, ["nav-r00-settings-open.jpg", "nav-r00-general-open.jpg"])


if __name__ == "__main__":
    unittest.main()
