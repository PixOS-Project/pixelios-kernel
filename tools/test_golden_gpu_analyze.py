#!/usr/bin/env python3
"""Host tests of tools/golden-gpu-analyze with SYNTHETIC traces (no golden data copied).
Run from tools/:  python3 -m unittest test_golden_gpu_analyze   (AP-118; NOT -I: -I drops the cwd from sys.path and the import fails)
Class Mutants rewrites the tool 4 ways and requires class Core to fail on each. GGA_TOOL overrides the tool path."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.environ.get("GGA_TOOL") or os.path.join(HERE, "golden-gpu-analyze")
GOLDEN = os.path.expanduser("~/pixelios-artifacts/golden/20261028T034021Z")
PG = 0xffffffc00f088000
DPG = 0xffffffc00d8b0000


def line(ev, caller, val, addr, ts, w=32):
    return "  Task-1 [001] d..1. %.6f: %s: %s+0x1/0x2 [m] -> writel+0x18/0x8 [m] width=%d val=0x%x addr=0x%x\n" % (ts, ev, caller, w, val, addr)


def mark(ts=1.0):
    return "  sh-1 [000] ..... %.6f: tracing_mark_write: MARK x\n" % ts


def noise(ts):
    return "  Task-1 [001] d..1. %.6f: rwmmio_post_write: foo+0x1/0x2 [m] width=32 val=0x1 addr=0xffffffc000000000\n" % ts


def run(*args):
    p = subprocess.run([sys.executable, "-I", TOOL] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    return p.returncode, p.stdout


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def write(self, name, text):
        p = os.path.join(self.d.name, name)
        with open(p, "w") as f:
            f.write(text)
        return p


def gpu_trace(unreset_gap=True):
    """MARK(0); p-up1 (no L2off yet, reset before); reset+L2off+PWRON x2; third PWRON without reset right after an L2off."""
    t = mark() + noise(1.1)
    ts = 2.0
    out = [t]

    def w(o, v, c="kbase_reg_write32"):
        nonlocal ts
        ts += 0.001
        out.append(line("rwmmio_write", c, v, PG + o, ts))
    w(0x28, 0)                       # row 1 IRQ_MASK
    w(0x30, 1)                       # row 2 reset
    w(0x190, 3, "kbase_reg_write64")  # row 3 power-up #1 (reset)
    w(0x180, 3)                      # shader
    w(0x1E0, 1)                      # row 5 L2 off
    ts += 0.010
    w(0x30, 1)                       # row 6 reset
    w(0x190, 3, "kbase_reg_write64")  # row 7 power-up #2 (reset)
    w(0x1E0, 1)                      # row 8 L2 off
    ts += 0.0001
    w(0x190, 3, "kbase_reg_write64")  # row 9 power-up #3 (NO reset, 0.1 ms after L2off)
    w(0x180, 3)
    return "".join(out)


class Core(Tmp):
    def test_rows_numbering_mark_is_row0(self):
        p = self.write("t", noise(0.5) + mark() + line("rwmmio_post_read", "decon_a", 5, DPG + 0x10, 1.1) + noise(1.2) + line("rwmmio_write", "decon_b", 0x3070, DPG + 0x30, 1.2, 16))
        rc, out = run("rows", p, "--from", "0", "--to", "2")
        self.assertEqual(rc, 0, out)
        self.assertIn("row 0 mark", out)
        self.assertIn("row 1 r decon_a w=32 val=0x5", out)
        self.assertIn("row 2 w decon_b w=16 val=0x3070", out)
        self.assertIn("+0x030", out)
        self.assertNotIn("row 3", out)

    def test_rows_csv(self):
        p = self.write("t", mark() + line("rwmmio_write", "decon_b", 0x3070, DPG + 0x30, 1.2))
        rc, out = run("rows", p, "--from", "1", "--to", "1", "--csv")
        self.assertEqual(rc, 0, out)
        self.assertIn("1,w,decon_b,32,0x3070,0x%x,+0x030" % (DPG + 0x30), out)

    def test_bad_input_error(self):
        p = self.write("t", mark())
        for args in (["rows", "/nonexistent", "--from", "0", "--to", "1"], ["rows", p, "--from", "0", "--to", "5"],
                     ["rows", p, "--from", "3", "--to", "1"], ["power-cycles", self.write("e", "nothing\n")],
                     ["power-cycles", self.write("bad", "x rwmmio_write: garbage\n")], []):
            rc, out = run(*args)
            self.assertEqual(rc, 2, (args, out))
            if args:
                self.assertIn("RESULT: ERROR", out)

    def dpu_trace(self):
        t = mark()
        for i, (o, v, c, pg) in enumerate([
                (0x70, 0x1000, "decon_reg_clear_int", 0),
                (0x30, 0x3061, "decon_reg_set_trigger", 0),
                (0x30, 0x3070, "decon_reg_set_trigger", 0),
                (0x50, 0x10000000, "decon_reg_update", 0),
                (0x004, 0xff, "other_blk", 1),
                (0x004, 0xee, "other_blk", 1),
                (0x008, 0xee, "other_blk", 2),
                (0x20, 0x100113, "decon_reg_x", 0),
                (0x50, 0x1000003f, "decon_reg_update", 0),
                (0x20, 0x100113, "decon_reg_x", 0),
                (0x50, 0x10000000, "decon_reg_update", 0),
                (0x70, 0x1000, "decon_reg_clear_int", 0),
                (0x30, 0x3061, "decon_reg_set_trigger", 0)]):
            t += line("rwmmio_write", c, v, DPG + (pg << 12) + o, 2.0 + i / 1000.0)
            if i == 2:
                t += line("rwmmio_post_read", "decon_r", 0, DPG + 0x50, 2.0)   # a read must not count
        return t

    def dpu_spec(self, **kw):
        sp = {"shd": {"0x10000000": 2, "0x1000003f": 1}, "trig_3070_rows": [3], "trig_last": "0x3061", "int70_rows": [1, 1 + 0],
              "global_tail_rows": [0, 20], "global_tail_count": 2}
        # row numbers: 0 mark,1 int70,2 trig3061,3 trig3070,4 read,5 shd,...
        sp["trig_3070_rows"] = [3]
        sp["int70_rows"] = [1]
        sp.update(kw)
        return self.write("spec.json", json.dumps(sp))

    def test_dpu_commit_ok(self):
        p = self.write("d", self.dpu_trace())
        out_csv = os.path.join(self.d.name, "o.csv")
        rc, out = run("dpu-commit", p, "--from", "0", "--to", "14", "--spec", self.dpu_spec(), "--out", out_csv)
        self.assertEqual(rc, 0, out)
        self.assertIn("writes=13", out)
        self.assertIn("P0=DECON:", out)
        self.assertIn("pages=P0=DECON:10 P1:2 P2:1", out.replace("writes=13 ", ""))   # first-seen order, 3 pages
        self.assertIn("0x10000000 x2", out)
        self.assertIn("shd_req_0x10000000 spec=2 observed=2 OK", out)
        self.assertIn("shd_req_other_values spec=0 observed=0 OK", out)
        self.assertIn("TRIG_CON sequence: 2:0x3061 3:0x3070 14:0x3061", out)
        self.assertIn("RESULT: PASS", out.strip().splitlines()[-1])
        rows = open(out_csv).read().splitlines()
        self.assertEqual(rows[1], "1,P0,+0x070,decon_reg_clear_int,32,0x1000")
        self.assertTrue(any(",P1,+0x004," in r for r in rows))

    def test_dpu_histogram_key_full_width(self):
        # 0x3f and 0x1000003f / 0x9010003f must stay distinct keys
        t = mark() + line("rwmmio_write", "decon_reg_set_trigger", 0x3070, DPG + 0x30, 1.0)
        for v in (0x3f, 0x1000003f, 0x9010003f, 0x80100000):
            t += line("rwmmio_write", "decon_u", v, DPG + 0x50, 1.1)
        p = self.write("d", t)
        sp = self.write("s.json", json.dumps({"shd": {"0x3f": 1, "0x1000003f": 1, "0x9010003f": 1, "0x80100000": 1}, "trig_3070_rows": [1],
                                              "trig_last": "0x3070", "int70_rows": [], "global_tail_count": 0, "int70_val": "0x1000"}))
        rc, out = run("dpu-commit", p, "--from", "0", "--to", "5", "--spec", sp)
        self.assertIn("shd_req_0x3f spec=1 observed=1 OK", out)
        self.assertIn("shd_req_0x9010003f spec=1 observed=1 OK", out)

    def test_dpu_mismatch_exit1(self):
        p = self.write("d", self.dpu_trace())
        rc, out = run("dpu-commit", p, "--from", "0", "--to", "14", "--spec", self.dpu_spec(shd={"0x10000000": 3, "0x1000003f": 1}), "--out", os.path.join(self.d.name, "o"))
        self.assertEqual(rc, 1, out)
        self.assertIn("shd_req_0x10000000 spec=3 observed=2 MISMATCH", out)
        self.assertIn("RESULT: FAIL", out)

    def test_dpu_no_decon_page_error(self):
        p = self.write("d", mark() + line("rwmmio_write", "other", 1, DPG + 0x30, 1.0))
        rc, out = run("dpu-commit", p, "--from", "0", "--to", "1")
        self.assertEqual(rc, 2, out)

    def pspec(self, **kw):
        sp = {"tilerpwron": 3, "softreset": 2, "shader_pwron": 2, "l2_pwron": 0}
        sp.update(kw)
        return self.write("ps.json", json.dumps(sp))

    def test_power_cycles_pairing(self):
        p = self.write("g", gpu_trace())
        rc, out = run("power-cycles", p, "--spec", self.pspec())
        self.assertEqual(rc, 0, out)
        self.assertIn("CHECK tilerpwron spec=3 observed=3 OK", out)
        self.assertIn("CHECK softreset spec=2 observed=2 OK", out)
        self.assertIn("CHECK shader_pwron spec=2 observed=2 OK", out)
        self.assertIn("powerups=3 without_reset=1", out)
        tab = [l.split() for l in out.splitlines() if l[:1].isdigit()]
        self.assertEqual([(t[0], t[1]) for t in tab[:3]], [("3", "yes"), ("7", "yes"), ("9", "NO")])
        self.assertEqual(tab[2][2], "8")                    # previous L2_PWROFF row
        self.assertAlmostEqual(float(tab[2][3]), 1.1, delta=0.01)    # ms between L2off (row 8) and power-up
        self.assertIn("NO-RESET power-up row 9", out)
        self.assertIn("HYPOTHESIS", out)
        self.assertIn("RESULT: PASS", out.strip().splitlines()[-1])

    def test_power_cycles_no_pattern(self):
        t = gpu_trace()
        # make the unreset power-up far after its L2off and drop the L2off between #2 and #3 to break both patterns
        lines = t.splitlines(True)
        self.assertTrue(len(lines) > 8)
        p = self.write("g", "".join(l for i, l in enumerate(lines) if "addr=0x%x" % (PG + 0x1E0) not in l or i < 6))
        rc, out = run("power-cycles", p, "--spec", self.pspec())
        self.assertIn("no pattern found", out)

    def test_power_cycles_mismatch(self):
        p = self.write("g", gpu_trace())
        rc, out = run("power-cycles", p, "--spec", self.pspec(softreset=3))
        self.assertEqual(rc, 1, out)
        self.assertIn("CHECK softreset spec=3 observed=2 MISMATCH", out)

    def test_power_cycles_no_mali_page(self):
        rc, out = run("power-cycles", self.write("g", mark() + line("rwmmio_write", "x", 1, PG + 0x28, 1.0)))
        self.assertEqual(rc, 2, out)


@unittest.skipUnless(os.path.isdir(GOLDEN), "real golden dir absent")
class RealGoldenChecks(unittest.TestCase):
    """Optional: runs the SPEC-GPU numbers on the real golden traces (reads only, copies nothing)."""

    def test_dpu(self):
        rc, out = run("dpu-commit", os.path.join(GOLDEN, "trace-dpu.txt"), "--from", "17684", "--to", "18030", "--out", os.devnull)
        self.assertEqual(rc, 0, out)

    def test_gpu(self):
        rc, out = run("power-cycles", os.path.join(GOLDEN, "trace-gpu.txt"))
        self.assertEqual(rc, 0, out)


MUTANTS = {
    "row off-by-one": ("n = len(rows)\n", "n = len(rows) + 1\n"),
    "swapped PWRON offset": ("OFF_TILER_PWRON = 0x180, 0x190,", "OFF_TILER_PWRON = 0x190, 0x180,"),
    "reset counted twice": ("            resets_since += 1\n        elif", "            resets_since += 1\n            resets_since += 1\n        elif"),
    "histogram key width": ('k = "0x%x" % r["val"]\n', 'k = "0x%x" % (r["val"] & 0xffff)\n'),
}


class Mutants(unittest.TestCase):
    """Each mutant of the tool must make class Core fail (run in a subprocess with GGA_TOOL set)."""

    def test_mutants_killed(self):
        with open(os.path.join(HERE, "golden-gpu-analyze")) as fh:
            src = fh.read()
        survivors = []
        for name, (a, b) in MUTANTS.items():
            if name == "swapped PWRON offset":
                a, b = "OFF_SHADER_PWRON, OFF_TILER_PWRON, OFF_L2_PWRON, OFF_L2_PWROFF = 0x180, 0x190, 0x1A0, 0x1E0", \
                       "OFF_SHADER_PWRON, OFF_TILER_PWRON, OFF_L2_PWRON, OFF_L2_PWROFF = 0x190, 0x180, 0x1A0, 0x1E0"
            if name == "reset counted twice":
                a = "            resets_since += 1\n        elif o == OFF_L2_PWROFF"
                b = "            resets_since += 1\n            RESETS_EXTRA[0] += 1\n        elif o == OFF_L2_PWROFF"
                src_m = src.replace("def cmd_power(a):", "RESETS_EXTRA = [0]\n\n\ndef cmd_power(a):", 1)
                src_m = src_m.replace("return cnt(OFF_GPU_COMMAND", "return cnt(OFF_GPU_COMMAND")
                # count the reset twice in the CHECK as well
                src_m = src_m.replace('ck.check("softreset", spec["softreset"], cnt(OFF_GPU_COMMAND, SOFT_RESET))',
                                      'ck.check("softreset", spec["softreset"], cnt(OFF_GPU_COMMAND, SOFT_RESET) + RESETS_EXTRA[0])')
            else:
                src_m = src
            self.assertIn(a, src_m, name)
            mut = src_m.replace(a, b, 1)
            with tempfile.TemporaryDirectory() as d:
                mp = os.path.join(d, "golden-gpu-analyze")
                with open(mp, "w") as fh:
                    fh.write(mut)
                env = dict(os.environ, GGA_TOOL=mp)
                r = subprocess.run([sys.executable, "-m", "unittest", "test_golden_gpu_analyze.Core"], cwd=HERE, env=env,
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
                if r.returncode == 0:
                    survivors.append(name)
        self.assertEqual(survivors, [])


if __name__ == "__main__":
    unittest.main()
