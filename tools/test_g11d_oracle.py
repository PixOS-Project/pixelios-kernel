#!/usr/bin/env python3
"""Tests for tools/g11d-oracle: synthetic logs (literal lines copied from real runs + format-string builds) in a tempdir (RUNS_ROOT),
no real-run class: logs/runs are gitignored and absent on gate hosts, so it could never run there (T-2f05)."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "g11d-oracle")
P = "|backboardd| "

# literal lines copied from 20261029T125945Z-gputest113g-capv7/merged.log
CENSUS = "SWEETGL: BDROP census t+1080s blur S/T/O 3277/4086/3446 fbfetch S/T/O 34314/30/362 cmatrix S/T/O 2515/15/181 lum S/T/O 2063/15/181 rect S/T/O 2699/0/0 other S/T/O 244881/4593/24362 copytex 0"
CENSUS0 = "SWEETGL: BDROP census t+1100s blur S/T/O 0/0/0 fbfetch S/T/O 1/0/0 cmatrix S/T/O 0/0/0 copytex 0"
FULL_MAP = "..++####|.++#####|++######|+#######|+#######|+######+|+#####++|+++++++."
QUAD_MAP = "+##+....|###+....|+##+....|++++....|........|........|........|........"
SPARSE_MAP = "+.......|........|........|........|........|........|........|........"
EMPTY_MAP = "|".join(["........"] * 8)
LIT_GRID = "SWEETGL: PIXELS grid r4 fbo 33 vp 1082x1226 cells 60/64 map " + FULL_MAP + " a5 0"
LIT_UNBIND = "SWEETGL: PIXELS unbind S fbo 33 vp 1082x1226 nonzero 16 of 16 maxa 0xff sum 0x265f written 1 round 4 quad 16/16/16/16"
LIT_CAPDRAWS = "SWEETGL: PIXELS capdraws r4 fbo 33 draws 5 kinds b:0 f:2 c:0 l:0 r:0 o:3 verts 66 progs 7..23 blendlast 0 since-bind"
LIT_CAPVP = "SWEETGL: PIXELS capvp r4 fbo 33 first vp 0,0,1082,1226 mode 0x4 count 6 last vp 0,0,1082,1226 mode 0x4 count 48"


def grid(r, f, cells, m, w=1082, h=1226):
    return "SWEETGL: PIXELS grid r%d fbo %d vp %dx%d cells %d/64 map %s a5 0" % (r, f, w, h, cells, m)


def unbind(r, f, nz, w=1082, h=1226):
    return "SWEETGL: PIXELS unbind S fbo %d vp %dx%d nonzero %d of 16 maxa 0xff sum 0x1 written 1 round %d quad 16/0/0/0" % (f, w, h, nz, r)


def capdraws(r, f, draws, verts):
    return "SWEETGL: PIXELS capdraws r%d fbo %d draws %d kinds b:0 f:0 c:0 l:0 r:0 o:%d verts %d progs 7..23 blendlast 0 since-bind" % (r, f, draws, draws, verts)


def capvp(r, f, c1, c2, w=1082, h=1226):
    return "SWEETGL: PIXELS capvp r%d fbo %d first vp 0,0,%d,%d mode 0x4 count %d last vp 0,0,%d,%d mode 0x4 count %d" % (r, f, w, h, c1, w, h, c2)


def run(*args, root=None):
    env = dict(os.environ)
    if root:
        env["RUNS_ROOT"] = root
    r = subprocess.run([sys.executable, "-I", TOOL] + list(args), capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def block(r, f, cells, m, nz, draws, verts, c1, c2):
    return [grid(r, f, cells, m), unbind(r, f, nz), capdraws(r, f, draws, verts), capvp(r, f, c1, c2)]


def cv_draw(f, d, count, verts, w=1082, h=1226, uni="-", tex="u0 6"):
    """capv8 lines of one draw, literal formats copied from 20261029T162816Z-gputest113h-capv8 (vp/count substituted)."""
    pre = "SWEETGL: PIXELS capv8 fbo %d vp %dx%d draw %d " % (f, w, h, d)
    v = ["%d,%d,0,1" % t for t in verts]
    vl = [pre + "v idx 0,1,2,2 min 0 max 3 v0 %s v1 %s" % (v[0], v[1])]
    if len(v) > 2:
        vl.append(pre + "v v2 %s v3 %s" % (v[2], v[3 % len(v)] if len(v) > 3 else v[2]))
    return ([pre + "prog 3 mode 0x4 count %d first - idx 0x1403 | tex %s" % (count, tex),
             pre + "a a0 en 1 buf 0 sz 4 ty 0x1406 st 48 off cli, a1 en 1 buf 0 sz 4 ty 0x140b st 48 off cli",
             pre + "a a2 en 1 buf 0 sz 2 ty 0x1406 st 48 off cli, a3 en 1 buf 0 sz 2 ty 0x1406 st 48 off cli"] + vl + [pre + "u " + uni])


QV = [(0, 0), (500, 0), (500, 600), (500, 600)]
FV = [(0, 0), (1082, 0), (1082, 1226), (1082, 1226)]
ORTHO = "vertex_matrix=0.00184843,0,0,0 vertex_matrix.4=0,0.00163132,0,0 vertex_matrix.8=0,0,0,0 vertex_matrix.12=-1,-1,0,1"


def cv_block(f, verts=QV, n=2, w=1082, h=1226, uni=ORTHO):
    out = cv_draw(f, 1, 6, FV, w, h)
    for d in range(2, n + 1):
        out += cv_draw(f, d, 48, verts, w, h, uni, tex="u0 6 u1 7")
    return out


def szt(f, d, z, t2, src, w=1082, h=1226, t3="ok"):
    """new batch-10b lines for draw d: z (list of 4 pts or None=unreadable), t2 (list of 8 (s,t) or None), src ('512x256' or '?' or None=no line)"""
    pre = "SWEETGL: PIXELS capv8 fbo %d vp %dx%d draw %d " % (f, w, h, d)
    out = []
    if src is not None:
        out.append(pre + "s u0 21 %s if 0x8058, u1 13 256x256 if 0x8058" % src)
    if z is None:
        out.append(pre + "z - nobuf")
    else:
        out.append(pre + "z idx 0,1,2,3 " + " ".join("l%d %d,%d,0,1" % (i, p[0], p[1]) for i, p in enumerate(z)))
    for a in (2, 3):
        if t2 is None:
            out.append(pre + "t a%d - noptr" % a)
        else:
            out.append(pre + "t a%d " % a + " ".join("f%d %g,%g" % (i, q[0], q[1]) for i, q in enumerate(t2[:4])) + " | "
                       + " ".join("l%d %g,%g" % (i, q[0], q[1]) for i, q in enumerate(t2[4:])))
    return out


FT = [(0, 0), (1, 0), (1, 1), (1, 1)] * 2
QT = [(0, 0), (0.5, 0), (0.5, 0.5), (0.5, 0.5)] * 2


def cv_block2(f, pos=FV, tex=FT, src="1082x1226", quad=FV, z=None, w=1082, h=1226, uni=ORTHO):
    """draw 1 count 6 (extent quad), draw 2 count 48 (extent pos), both with s/z/t lines"""
    out = cv_draw(f, 1, 6, quad, w, h) + szt(f, 1, quad, FT, "1082x1226")
    out += cv_draw(f, 2, 48, pos, w, h, uni, tex="u0 6 u1 7") + szt(f, 2, pos if z is None else z, tex, src)
    return out


class Join(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def go(self, lines):
        d = os.path.join(self.root, "logs", "runs", "j")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "merged.log"), "w") as f:
            f.write("".join(P + ln + "\n" for ln in lines))
        rc, out = run("--json", "j", root=self.root)
        body, last = out.rstrip("\n").rsplit("\n", 1)
        return json.loads(body)[0]

    def sample(self, r, f, cells=12, m=QUAD_MAP, draws=2, verts=54):
        return [unbind(r, f, 16)] + [grid(r, f, cells, m), capdraws(r, f, draws, verts), capvp(r, f, 6, 48)]

    def test_matching_block_quadrant(self):
        o = self.go([CENSUS] + cv_block(30) + self.sample(5, 30))
        self.assertEqual((o["join"]["available"], o["join"]["matched"], o["join"]["samples"]), (True, 1, 1))
        e = o["samples"][0]["join"]["count48"][0]
        self.assertEqual(e["draw"], 2)
        self.assertEqual((e["x_max"], e["y_max"]), (500.0, 600.0))
        self.assertAlmostEqual(e["fx"], 500 / 1082)
        self.assertEqual(e["ortho_ok"], "yes")
        self.assertEqual(e["tex"], "u0 6 u1 7")
        self.assertEqual(e["idx"], [0, 3])
        self.assertEqual(e["hint"], "positions-quadrant")
        self.assertEqual(o["join"]["hints"], {"quadrant": {"positions-quadrant": 1}})

    def test_require_quadrant_join(self):
        # matched quadrant sample: --require-quadrant-join 1 passes, 2 fails; unmatched (no capv8 block for the fbo) fails
        for name, lines, want_rc in (("m", [CENSUS] + cv_block(30) + self.sample(5, 30), (0, 1)),
                                     ("u", [CENSUS] + cv_block(31) + self.sample(5, 30), (1, 1))):
            d = os.path.join(self.root, "logs", "runs", name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "merged.log"), "w") as f:
                f.write("".join(P + ln + "\n" for ln in lines))
            rc1, out1 = run("--require-quadrant-join", "1", name, root=self.root)
            rc2, out2 = run("--require-quadrant-join", "2", name, root=self.root)
            self.assertEqual((rc1, rc2), (want_rc[0], 1), out1 + out2)
            self.assertIn("JOIN-QUADRANT: matched %d of 1 quadrant samples" % (1 - want_rc[0]), out1)
            if want_rc[0]:
                self.assertIn("RESULT: FAIL g11d-oracle", out1)

    def test_text_output(self):
        d = os.path.join(self.root, "logs", "runs", "t")
        os.makedirs(d)
        with open(os.path.join(d, "merged.log"), "w") as f:
            f.write("".join(P + ln + "\n" for ln in [CENSUS] + cv_block(30) + self.sample(5, 30)))
        rc, out = run("t", root=self.root)
        self.assertIn("hint: positions-quadrant", out)
        self.assertIn("JOIN: matched 1 of 1 samples", out)
        self.assertIn("ortho_ok yes", out)

    def test_full_and_other_hints(self):
        o = self.go([CENSUS] + cv_block(30, verts=FV, n=4, uni="-") + self.sample(6, 30, 60, FULL_MAP, 4, 108)
                    + cv_block(31, verts=[(0, 0), (1000, 0), (1000, 300), (1000, 300)]) + self.sample(6, 31))
        h = {s["fbo"]: s["join"]["count48"] for s in o["samples"]}
        self.assertEqual([e["hint"] for e in h[30]], ["positions-full"] * 3)
        self.assertEqual(len(h[30]), 3)           # draws 2,3,4 all count 48 in cv_block
        self.assertEqual(h[30][0]["ortho_ok"], "n/a")
        self.assertEqual(h[31][0]["hint"], "positions-other")
        self.assertEqual(o["join"]["matched"], 2)

    def test_name_reuse_takes_last_block_before_unbind(self):
        old = cv_block(32, verts=FV)
        new = cv_block(32, verts=QV)
        o = self.go([CENSUS] + old + self.sample(5, 32, 60, FULL_MAP) + new + self.sample(10, 32))
        by = {s["round"]: s["join"]["count48"][0]["hint"] for s in o["samples"]}
        self.assertEqual(by, {5: "positions-full", 10: "positions-quadrant"})
        self.assertEqual(o["join"]["matched"], 2)

    def test_block_restarts_when_draw_number_goes_back(self):
        # two binds of fbo 30 back to back (no other fbo between): the last one (QV) is the one joined
        o = self.go([CENSUS] + cv_block(30, verts=FV) + cv_block(30, verts=QV) + self.sample(5, 30))
        self.assertEqual(o["samples"][0]["join"]["count48"][0]["hint"], "positions-quadrant")

    def test_unmatched_draw_count(self):
        o = self.go([CENSUS] + cv_block(30, n=3) + self.sample(5, 30, draws=2))
        j = o["samples"][0]["join"]
        self.assertEqual(j["status"], "unmatched")
        self.assertIn("draws 3", j["reason"])
        self.assertEqual(o["join"]["matched"], 0)

    def test_vp_mismatch(self):
        o = self.go([CENSUS] + cv_block(30, w=1077) + self.sample(5, 30))
        j = o["samples"][0]["join"]
        self.assertEqual(j["status"], "unmatched")
        self.assertIn("vp mismatch", j["reason"])

    def test_block_after_unbind_is_ignored(self):
        o = self.go([CENSUS] + self.sample(5, 30) + cv_block(30))
        self.assertEqual(o["samples"][0]["join"]["status"], "unmatched")

    def test_no_capv8_lines(self):
        o = self.go([CENSUS] + self.sample(5, 30))
        self.assertFalse(o["join"]["available"])
        self.assertEqual(o["samples"][0]["join"], {"status": "n/a"})
        d = os.path.join(self.root, "logs", "runs", "j")
        rc, out = run(d)
        self.assertIn("JOIN: n/a (no capv8 lines)", out)

    # --- bind-level join (plan-g114c-r1 F1) ---
    def skip(self, f, reason="keycap", n=3, w=1082, h=1226):
        return "SWEETGL: PIXELS capv8 skip fbo %d vp %dx%d reason %s binds %d" % (f, w, h, reason, n)

    def req(self, lines, n=1):
        d = os.path.join(self.root, "logs", "runs", "q")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "merged.log"), "w") as f:
            f.write("".join(P + ln + "\n" for ln in lines))
        return run("--require-quadrant-join", str(n), "q", root=self.root)

    def test_recycled_name_skipped_bind_unmatched_and_gate_fails(self):
        # review false PASS: early block of fbo 30, later bind skipped by the key cap, sample of the later bind
        lines = [CENSUS] + cv_block(30) + [self.skip(30)] + self.sample(9, 30)
        o = self.go(lines)
        j = o["samples"][0]["join"]
        self.assertEqual(j["status"], "unmatched")
        self.assertIn("bind skipped by key cap", j["reason"])
        self.assertEqual(o["join"]["level"], "bind")
        rc, out = self.req(lines)
        self.assertEqual(rc, 1, out)
        self.assertIn("join level: bind", out)
        self.assertIn("skips keycap 1 keytable 0", out)
        self.assertIn("JOIN-QUADRANT: matched 0 of 1", out)
        self.assertIn("RESULT: FAIL g11d-oracle", out)

    def test_bind_level_block_right_before_sample_matched(self):
        lines = [CENSUS, self.skip(30, "keytable", 0)] + cv_block(30) + self.sample(5, 30)
        o = self.go(lines)
        self.assertEqual(o["samples"][0]["join"]["status"], "matched")
        rc, out = self.req(lines)
        self.assertEqual(rc, 0, out)
        self.assertIn("skips keycap 0 keytable 1", out)
        self.assertIn("JOIN-QUADRANT: matched 1 of 1", out)

    def test_block_then_skip_then_sample_unmatched(self):
        o = self.go([CENSUS, self.skip(30)] + cv_block(30) + [self.skip(30)] + self.sample(5, 30))
        self.assertEqual(o["samples"][0]["join"]["status"], "unmatched")

    def test_skip_of_other_fbo_does_not_matter(self):
        o = self.go([CENSUS] + cv_block(30) + [self.skip(31)] + self.sample(5, 30))
        self.assertEqual(o["samples"][0]["join"]["status"], "matched")

    def test_budget_exhausted_after_block_unmatched_stale(self):
        cap = "SWEETGL: PIXELS capv8 cap 400"
        lines = [CENSUS, self.skip(31)] + cv_block(30) + [cap] + self.sample(5, 30)
        o = self.go(lines)
        j = o["samples"][0]["join"]
        self.assertEqual(j["status"], "unmatched")
        self.assertIn("budget exhausted after the block", j["reason"])
        # cap BEFORE the block: unaffected
        o = self.go([CENSUS, self.skip(31), cap] + cv_block(30) + self.sample(5, 30))
        self.assertEqual(o["samples"][0]["join"]["status"], "matched")

    def test_skip_cap_line_tolerated(self):
        o = self.go([CENSUS, "SWEETGL: PIXELS capv8 skip cap 400"] + cv_block(30) + self.sample(5, 30))
        self.assertEqual(o["bad_lines"], 0)
        self.assertEqual((o["join"]["level"], o["samples"][0]["join"]["status"]), ("bind", "matched"))

    def test_key_level_without_skip_lines(self):
        lines = [CENSUS] + cv_block(30) + self.sample(5, 30)
        o = self.go(lines)
        self.assertEqual(o["join"]["level"], "key")
        d = os.path.join(self.root, "logs", "runs", "j")
        rc, out = run(d)
        self.assertIn("join level: key (no skip lines in the log)", out)
        self.assertIn("skips keycap 0 keytable 0", out)

    # --- DECIDE (batch 10b s/z/t tags) ---
    def dec(self, lines):
        o = self.go(lines)
        d = os.path.join(self.root, "logs", "runs", "j")
        rc, out = run(d)
        return o, out

    def one(self, blk, full=None):
        lines = [CENSUS] + blk + self.sample(5, 30)
        if full:
            lines += full + self.sample(6, 31, 60, FULL_MAP, 2)
        o, out = self.dec(lines)
        return o["decide"]["samples"][0]["outcome"], out

    def test_decide_o1_geometry(self):
        oc, out = self.one(cv_block2(30, pos=QV))
        self.assertEqual(oc, "O1")
        self.assertIn("DECIDE: round 5 fbo 30 O1 geometry-quadrant", out)
        self.assertIn("DECIDE-SUMMARY: o1 1 o2 0 o3 0 o4 0 o5 0 undecided 0", out)

    def test_decide_o1_via_quad_draw(self):
        self.assertEqual(self.one(cv_block2(30, quad=QV))[0], "O1")

    def test_decide_o2_texcoord(self):
        self.assertEqual(self.one(cv_block2(30, tex=QT))[0], "O2")

    def test_decide_o3_source_small(self):
        self.assertEqual(self.one(cv_block2(30, src="500x600"))[0], "O3")

    def test_decide_o4_source_content(self):
        oc, out = self.one(cv_block2(30))
        self.assertEqual(oc, "O4")
        self.assertIn("u0/fbo 1.00,1.00", out)

    def test_decide_o5_same_as_full(self):
        # fbo 31 is a matched full-class sample with the very same draws as quadrant fbo 30
        oc, out = self.one(cv_block2(30), full=cv_block2(31))
        self.assertEqual(oc, "O5")
        self.assertIn("DECIDE-SUMMARY: o1 0 o2 0 o3 0 o4 0 o5 1", out)
        # a full FBO with a different source size does not make it O5
        self.assertEqual(self.one(cv_block2(30), full=cv_block2(31, src="500x600"))[0], "O4")

    def test_decide_unknown_size_and_unreadable(self):
        for blk, want in ((cv_block2(30, src="?"), "s u0 (size ?)"), (cv_block2(30, tex=None), "t a2 (unreadable"),
                          (cv_block2(30, src=None), "s u0 (no line)")):
            oc, out = self.one(blk)
            self.assertEqual(oc, "undecided")
            self.assertIn(want, out)
            self.assertIn("undecided 1", out)
        blk = cv_draw(30, 1, 6, FV) + szt(30, 1, FV, FT, "1082x1226") + cv_draw(30, 2, 48, FV, uni=ORTHO) + szt(30, 2, None, FT, "1082x1226")
        oc, out = self.one(blk)
        self.assertEqual(oc, "undecided")
        self.assertIn("main draw z (unreadable", out)

    def test_decide_na_without_szt(self):
        o, out = self.dec([CENSUS] + cv_block(30) + self.sample(5, 30))
        self.assertIsNone(o["decide"])
        self.assertIn("DECIDE: n/a (no s/z/t lines)", out)
        self.assertNotIn("DECIDE-SUMMARY", out)

    def test_height_upper_bound(self):
        o = self.go([CENSUS, grid(6, 9, 60, FULL_MAP, 1127, 2438), unbind(6, 9, 16, 1127, 2438)] + self.sample(5, 30))
        self.assertEqual([s["fbo"] for s in o["samples"]], [30])


class Syn(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def mk(self, label, lines, name="merged.log", pre=P):
        d = os.path.join(self.root, "logs", "runs", label)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write("".join(pre + ln + "\n" for ln in lines))
        return d

    def js(self, label):
        rc, out = run("--json", label, root=self.root)
        body, last = out.rstrip("\n").rsplit("\n", 1)
        return rc, json.loads(body)[0], last

    def test_literals_full(self):
        self.mk("r", [CENSUS, LIT_GRID, LIT_UNBIND, LIT_CAPDRAWS, LIT_CAPVP])
        rc, o, last = self.js("r")
        self.assertEqual(rc, 0)
        self.assertEqual(o["counts"]["full"], 1)
        self.assertTrue(o["blur_present"])
        self.assertEqual((o["census"]["S"], o["census"]["T"], o["census"]["O"]), (3277, 4086, 3446))
        self.assertEqual(o["verdict"], "capture-fbo-full (no capture problem in the log)")
        self.assertEqual(o["per_class"]["full"]["capdraws"][0]["verts"], 66)
        self.assertEqual(o["per_class"]["full"]["capvp"][0]["last_count"], 48)
        self.assertIn("RESULT: PASS g11d-oracle runs=1 verdict=capture-fbo-full", last)

    def test_all_classes(self):
        L = [CENSUS] + block(1, 1, 60, FULL_MAP, 16, 4, 108, 6, 48) + block(2, 2, 16, QUAD_MAP, 0, 2, 54, 6, 48)
        L += block(3, 3, 1, SPARSE_MAP, 0, 1, 6, 6, 6) + block(4, 4, 0, EMPTY_MAP, 0, 1, 6, 6, 6)
        L += [unbind(5, 5, 16), capdraws(5, 5, 4, 108), capvp(5, 5, 6, 48)]       # no grid row, unbind nonzero -> nogrid
        self.mk("r", L)
        rc, o, _ = self.js("r")
        self.assertEqual(o["counts"], {"full": 1, "quadrant": 1, "sparse": 1, "empty": 1, "nogrid": 1})
        self.assertEqual(o["verdict"], "blur-shader-present, capture-fbo-partial (quadrant 1/5, full 1/5)")
        self.assertEqual(rc, 0)

    def test_quadrant_needs_8_cells_and_top_left(self):
        m_right = "....++++|....++++|....++++|....++++|........|........|........|........"   # 16 cells, top-right block
        m_five = "++++....|+.......|........|........|........|........|........|........"     # 5 cells in the block
        self.mk("r", [CENSUS] + block(1, 1, 16, m_right, 0, 2, 54, 6, 48) + block(2, 2, 5, m_five, 0, 1, 6, 6, 6))
        _, o, _ = self.js("r")
        self.assertEqual((o["counts"]["quadrant"], o["counts"]["sparse"]), (0, 2))

    def test_non_capture_fbo_ignored(self):
        self.mk("r", [CENSUS, grid(1, 4, 64, FULL_MAP, 1127, 149), unbind(1, 4, 16, 1127, 149), capdraws(1, 4, 5, 66), capvp(1, 4, 6, 6, 1127, 149)])
        rc, o, last = self.js("r")
        self.assertEqual(rc, 1)
        self.assertEqual(o["samples"], [])
        self.assertIn("RESULT: FAIL g11d-oracle", last)
        self.assertIn("n/a (missing capture-FBO rows)", o["verdict"])

    def test_blur_absent(self):
        self.mk("r", [CENSUS0] + block(2, 2, 16, QUAD_MAP, 0, 2, 54, 6, 48))
        rc, o, last = self.js("r")
        self.assertEqual(o["verdict"], "blur-shader-ABSENT")
        self.assertFalse(o["blur_present"])
        self.assertEqual(rc, 0)
        self.assertIn("verdict=blur-shader-ABSENT", last)

    def test_capture_empty(self):
        self.mk("r", [CENSUS] + block(1, 1, 0, EMPTY_MAP, 0, 1, 6, 6, 6) + [unbind(2, 2, 0), capdraws(2, 2, 1, 6), capvp(2, 2, 6, 6)])
        _, o, last = self.js("r")
        self.assertEqual(o["counts"]["empty"], 2)
        self.assertEqual(o["verdict"], "capture-fbo-empty")
        self.assertIn("verdict=capture-fbo-empty", last)

    def test_written0_grid_is_nogrid(self):
        self.mk("r", [CENSUS, "SWEETGL: PIXELS grid r1 fbo 3 vp 1082x1226 written 0", unbind(1, 3, 16)])
        _, o, _ = self.js("r")
        self.assertEqual(o["counts"]["nogrid"], 1)
        self.assertEqual(o["bad_lines"], 0)
        self.assertTrue(o["verdict"].startswith("n/a (missing grid rows"))

    def test_missing_census(self):
        self.mk("r", block(1, 1, 16, QUAD_MAP, 0, 2, 54, 6, 48))
        rc, o, last = self.js("r")
        self.assertEqual(rc, 1)
        self.assertEqual(o["verdict"], "n/a (missing BDROP census line)")
        self.assertIn("RESULT: FAIL g11d-oracle", last)

    def test_last_census_wins(self):
        self.mk("r", [CENSUS, CENSUS0] + block(1, 1, 16, QUAD_MAP, 0, 2, 54, 6, 48))
        _, o, _ = self.js("r")
        self.assertFalse(o["blur_present"])

    def test_prefix_variants_and_usbnet_fallback(self):
        lines = [CENSUS] + block(1, 1, 16, QUAD_MAP, 0, 2, 54, 6, 48)
        for pre in ("", P, "1789.123\t[ 12.500] " + P, "1789.123\t"):
            self.mk("r", lines, name="usbnet.log", pre=pre)
            rc, o, _ = self.js("r")
            self.assertEqual((rc, o["log"], o["counts"]["quadrant"]), (0, "usbnet.log", 1), repr(pre))
        self.mk("m", lines)
        d = os.path.join(self.root, "logs", "runs", "m")
        with open(os.path.join(d, "usbnet.log"), "w") as f:
            f.write("nothing\n")
        _, o, _ = self.js("m")
        self.assertEqual(o["log"], "merged.log")

    def test_consistency_flag(self):
        self.mk("r", [CENSUS] + block(1, 1, 16, QUAD_MAP, 0, 2, 54, 6, 48) + block(2, 2, 16, QUAD_MAP, 0, 1, 6, 6, 6))
        _, o, _ = self.js("r")
        self.assertFalse(o["per_class"]["quadrant"]["capdraws_consistent"])
        rc, out = run("r", root=self.root)
        self.assertIn("consistent within class: capdraws NO", out)

    def test_join_by_round_and_fbo(self):
        # same fbo in two rounds must not mix capdraws
        self.mk("r", [CENSUS] + block(1, 7, 16, QUAD_MAP, 0, 2, 54, 6, 48) + block(2, 7, 60, FULL_MAP, 16, 4, 108, 6, 48))
        _, o, _ = self.js("r")
        by = {s["round"]: s for s in o["samples"]}
        self.assertEqual((by[1]["class"], by[1]["verts"]), ("quadrant", 54))
        self.assertEqual((by[2]["class"], by[2]["verts"]), ("full", 108))

    def test_malformed_counted(self):
        self.mk("r", [CENSUS, "SWEETGL: PIXELS grid r1 fbo 3 vp 1082x1226 cells 16/64 map +##+...."] + block(2, 2, 16, QUAD_MAP, 0, 2, 54, 6, 48))
        _, o, _ = self.js("r")
        self.assertEqual(o["bad_lines"], 1)

    def test_bad_args(self):
        rc, out = run("--bogus", root=self.root)
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR", out)
        rc, out = run("no-such-label", root=self.root)
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR", out)
        os.makedirs(os.path.join(self.root, "logs", "runs", "empty"))
        rc, out = run("empty", root=self.root)
        self.assertEqual(rc, 2)

    def test_text_output_has_verdict_line(self):
        self.mk("r", [CENSUS] + block(2, 2, 16, QUAD_MAP, 0, 2, 54, 6, 48))
        rc, out = run("r", root=self.root)
        self.assertIn("VERDICT: blur-shader-present, capture-fbo-partial (quadrant 1/1, full 0/1)", out)
        self.assertTrue(out.rstrip("\n").endswith("verdict=capture-fbo-partial"))


if __name__ == "__main__":
    unittest.main()
