#!/usr/bin/env python3
"""Tests for tools/frame-split: synthetic logs in a tempdir (RUNS_ROOT) + the real logs/runs of research 113 s.2-3 when present."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "frame-split")
REAL = os.path.join(os.path.dirname(HERE), "logs", "runs")


def run(*args, root=None):
    env = dict(os.environ)
    if root:
        env["RUNS_ROOT"] = root
    r = subprocess.run([sys.executable, "-I", TOOL] + list(args), capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def fps(t, swaps, distinct, gap=100):
    return "%.3f\t[ %.3f] SWEET FPS: swaps %d distinct %d max_gap_ms %d\n" % (t, t - 1000, swaps, distinct, gap)


def perf(t, jobs, busy, wait, cpu=5):
    return "%.3f\t[ %.3f] SWEET PERF: gpu_jobs %d busy_ms %d max_job_us 900 submit_wait_ms %d cpu_busy_pct %d cpus 1 2 3\n" % (t, t - 1000, jobs, busy, wait, cpu)


class Syn(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def mk(self, label, text, name="usbnet.log"):
        d = os.path.join(self.root, "logs", "runs", label)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
        return d

    def js(self, label, *extra):
        rc, out = run("--json", *extra, label, root=self.root)
        return rc, json.loads(out[:out.index("\n==")])[0], out

    def test_boundary(self):
        self.mk("r", fps(1, 12, 5) + fps(2, 11, 5) + fps(3, 12, 0) + fps(4, 30, 20, 50))
        rc, o, out = self.js("r")
        self.assertEqual(rc, 0)
        self.assertEqual((o["busy_s"], o["idle_s"], o["other_s"]), (2, 1, 1))   # 12/5 and 30/20 busy; 11/5 idle; 12/0 neither
        self.assertEqual(o["busy_distinct_median"], 12.5)
        self.assertEqual(o["idle_distinct_median"], 5)
        self.assertIn("RESULT: PASS frame-split runs=1 score=12.5", out)

    def test_join_window(self):
        txt = fps(100.5, 25, 10) + perf(101.4, 50, 40, 20) + fps(200.5, 25, 10) + perf(202.0, 50, 40, 20)   # 0.9 s joins, 1.5 s does not
        self.mk("r", txt)
        rc, o, _ = self.js("r", "--join-perf")
        self.assertEqual(o["join"]["seconds"], 1)
        self.assertEqual(o["join"]["jobs_per_frame_per_sec_median"], 5.0)
        self.assertEqual(o["join"]["gpu_busy_ms_per_frame_per_sec_median"], 4.0)
        self.assertEqual(o["join"]["submit_wait_ms_per_frame_per_sec_median"], 2.0)
        self.assertEqual(o["join"]["frame_ms"], 100.0)

    def test_join_needs_distinct8(self):
        self.mk("r", fps(100, 25, 7) + perf(100.2, 50, 40, 20))
        self.assertEqual(self.js("r", "--join-perf")[1]["join"]["seconds"], 0)

    def test_missing_perf(self):
        self.mk("r", fps(1, 20, 10))
        rc, o, out = self.js("r", "--join-perf")
        self.assertEqual(rc, 0)
        self.assertIsNone(o["pace_busy_since_vsync_avg_median"])
        self.assertIn("n/a", out)

    def test_merged_fallback(self):
        self.mk("r", fps(1, 20, 10), name="merged.log")
        self.assertEqual(run("r", root=self.root)[0], 0)

    def test_empty_log_exit1(self):
        self.mk("r", "")
        rc, out = run("r", root=self.root)
        self.assertEqual(rc, 1)
        self.assertTrue(out.splitlines()[-1].startswith("RESULT: FAIL frame-split"))

    def test_bad_dir_exit2(self):
        rc, out = run("nonexistent-label", root=self.root)
        self.assertEqual(rc, 2)
        self.assertTrue(out.splitlines()[-1].startswith("RESULT: ERROR"))
        self.assertEqual(run(root=self.root)[0], 2)           # no argument
        os.makedirs(os.path.join(self.root, "logs", "runs", "nolog"))
        self.assertEqual(run("nolog", root=self.root)[0], 2)  # dir without a log


# EXACT printf formats (kernel patch 0186 / userland gli_table.c sweet_ft_line)
F_SWAPT = "SWEET SWAPT: n %u now %llu vsync %llu prev %llu gpu_seq %llu gpu_done %llu"
F_PROC = "SWEET PROCCPU: bb_pid %d bb_us %llu sb_pid %d sb_us %llu"
F_FT = "SWEETGL: FLUSHT pid %d sec %u flush %u flush_in_us %llu flush_max_us %u finish %u finish_in_us %llu finish_max_us %u last_exit_ns %llu"
LIT_SWAPT = "SWEET SWAPT: n 12 now 5000000000 vsync 4990000000 prev 4960000000 gpu_seq 77 gpu_done 75"
LIT_PROC = "SWEET PROCCPU: bb_pid 61 bb_us 1234567 sb_pid 73 sb_us 7654321"
LIT_FT = "SWEETGL: FLUSHT pid 61 sec 4 flush 30 flush_in_us 12000 flush_max_us 900 finish 2 finish_in_us 8000 finish_max_us 5000 last_exit_ns 4999000000"


def c(fmt):
    return fmt.replace("%llu", "%d").replace("%u", "%d")


def swapt(t, n, now, vs, prev, sq=0, dn=0, pre=""):
    return "%.3f\t[ %.3f] %s%s\n" % (t, t - 1000, pre, c(F_SWAPT) % (n, now, vs, prev, sq, dn))


def proc(t, bp, bu, sp, su):
    return "%.3f\t[ %.3f] %s\n" % (t, t - 1000, c(F_PROC) % (bp, bu, sp, su))


def ft(t, pid, fl_us, fi_us, finish=2, pre="|backboardd| "):
    return "%.3f\t[ %.3f] %s%s\n" % (t, t - 1000, pre, c(F_FT) % (pid, 3, 30, fl_us, 900, finish, fi_us, 5000, 1))


class PG0(Syn):
    def base(self, n=6, distinct=10):
        return "".join(fps(100 + i, 25, distinct) for i in range(n))      # period 100 ms

    def cpu(self, bb_ms, sb_ms, n=6):
        return "".join(proc(100 + i, 61, 1000000 + i * bb_ms * 1000, 73, 5000000 + i * sb_ms * 1000) for i in range(n))

    def test_none(self):
        self.mk("r", self.base())
        rc, o, out = self.js("r")
        self.assertEqual(rc, 0)
        self.assertIn("P-G0 lines: none", out)
        self.assertTrue(out.splitlines()[-1].endswith("p_g0=no"), out)
        self.assertEqual(o["pg0"]["bad_lines"], 0)

    def test_literals(self):
        lit = "".join("1.000\t[ 1.0] %s%s\n" % (pre, l) for pre, l in (("", LIT_SWAPT), ("", LIT_PROC), ("|backboardd| ", LIT_FT)))
        self.mk("r", fps(1, 25, 10) + lit)
        rc, o, out = self.js("r")
        g = o["pg0"]
        self.assertEqual((g["swapt_lines"], g["proccpu_lines"], g["flusht_lines"], g["bad_lines"]), (1, 1, 1, 0))
        self.assertEqual(g["swap_gap_ms"]["median"], 40.0)      # 5000000000-4960000000 ns
        self.assertEqual(g["swap_vsync_age_ms"]["median"], 10.0)
        self.assertEqual(g["gpu_lag"]["outstanding_median"], 2)
        self.assertEqual(g["flusht"]["61"]["in_flush_share_per_s"]["median"], 0.02)
        self.assertTrue(out.splitlines()[-1].endswith("p_g0=yes"), out)

    def test_swapt_stats_and_variants(self):
        t = "SWEET SWAPT: gpu seq unavailable (sweet_mali not up or dead at swap n 3)"
        txt = self.base()
        txt += swapt(1, 1, 100000000, 90000000, 0, 5, 5) + swapt(2, 2, 140000000, 130000000, 100000000, 7, 5, pre="|backboardd| ")
        txt += "1.0\t[ 1.0] %s\n" % t
        txt += "1.0\t[ 1.0] SWEET SWAPT: n 3 now 200000000 vsync 190000000 prev 140000000 gpu_seq - gpu_done -\n"
        txt += swapt(3, 4, 300000000, 290000000, 200000000, 3, 4)      # stale peek: done > seq -> 0 outstanding, counted negative
        txt += "1.0\t[ 1.0] SWEET SWAPT: cap reached 36000\n"
        self.mk("r", txt)
        g = self.js("r")[1]["pg0"]
        self.assertEqual((g["swapt_lines"], g["bad_lines"], g["swapt_cap_reached"], g["variant_lines"]["swapt"]), (4, 0, True, 2))
        self.assertEqual(g["swap_gap_ms"]["n"], 3)                       # prev 0 skipped
        self.assertEqual(g["swap_gap_ms"]["max"], 100.0)
        self.assertEqual(g["swap_vsync_age_ms"]["median"], 10.0)
        self.assertEqual(g["gpu_lag"]["swaps_with_seq"], 3)
        self.assertEqual(g["gpu_lag"]["swaps_without_seq"], 1)
        self.assertEqual(g["gpu_lag"]["negative"], 1)
        self.assertEqual(g["gpu_lag"]["outstanding_max"], 2)
        self.assertAlmostEqual(g["gpu_lag"]["fraction_outstanding_gt0"], 0.333, places=3)

    def test_proccpu_restart_no_negative(self):
        txt = self.base()
        txt += proc(100, 61, 1000000, 73, 5000000) + proc(101, 61, 1020000, 73, 5010000)
        txt += proc(102, 90, 100, 73, 5020000)                         # backboardd restarted: new pid, smaller counter
        txt += proc(103, 90, 10100, 73, 5030000)
        txt += "103.1\t[ 1.0] SWEET PROCCPU: SpringBoard not found\n"
        txt += proc(104, 90, 20100, -1, 0) + proc(105, 90, 30100, -1, 0)
        self.mk("r", txt)
        g = self.js("r")[1]["pg0"]
        c_ = g["proccpu"]
        self.assertEqual(g["bad_lines"], 0)
        self.assertEqual(g["variant_lines"]["proccpu"], 1)
        self.assertGreaterEqual(c_["pid_changes"], 1)
        self.assertGreaterEqual(c_["bb_ms_per_s"]["median"], 0)
        self.assertEqual(c_["bb_ms_per_s"]["n"], 4)        # 5 intervals minus the pid-change one
        self.assertEqual(c_["sb_ms_per_s"]["n"], 3)        # 3 intervals before sb pid -1 (excluded)
        self.assertEqual(c_["bb_ms_per_frame"]["median"], 1.0)   # 10 ms / 10 distinct

    def test_appended_fields_and_pid_change_lines(self):
        """Patches after 0186 append fields to SWAPT / PROCCPU and add pid-change lines (0247, 0341): data lines still parse, variants are not bad lines.
        The literal lines are from boot resident-065552-b5 (usbnet.log)."""
        txt = self.base()
        txt += "100.0\t[ 1.0] SWEET SWAPT: n 1 now 1129832906860 vsync 1129823631103 prev 0 gpu_seq 2 gpu_done 2 busy_us 5561 wait_us 4 call1 1129826322000 calll 1129826898000 done 1129832317000\n"
        txt += "100.1\t[ 1.0] SWEET PROCCPU: Preferences pid -1 (was -2) not running\n"
        txt += "100.1\t[ 1.0] SWEET PROCCPU: backboardd pid 301 (was -1)\n"
        txt += "100.1\t[ 1.0] SWEET PROCCPU: Preferences pid 322 task_info failed (4)\n"
        txt += "100.2\t[ 1.0] SWEET PROCCPU: bb_pid 301 bb_us 401379056 sb_pid 303 sb_us 687960171 pf_pid 322 pf_us 231764858\n"
        txt += "101.2\t[ 2.0] SWEET PROCCPU: bb_pid 301 bb_us 401380042 sb_pid 303 sb_us 687974673 pf_pid -1 pf_us 0\n"
        txt += "101.3\t[ 2.0] SWEET PROCCPU: bb_pid 301 bb_us 5 sb_pid 303 sb_us 6 pf_pid x\n"          # malformed tail
        self.mk("r", txt)
        g = self.js("r")[1]["pg0"]
        self.assertEqual(g["swapt_lines"], 1)
        self.assertEqual(g["proccpu_lines"], 2)
        self.assertEqual(g["variant_lines"]["proccpu"], 3)
        self.assertEqual(g["bad_lines"], 1)
        self.assertEqual(g["proccpu"]["intervals"], 1)

    def test_flusht(self):
        txt = self.base() + ft(100.2, 61, 30000, 20000, 2) + ft(101.2, 61, 10000, 0, 0) + ft(100.3, 70, 0, 0, 0)
        txt += "100.5\t[ 1.0] SWEETGL: FLUSHT unavailable: mach_timebase_info failed, flush timing OFF\n"
        self.mk("r", txt)
        f = self.js("r")[1]["pg0"]["flusht"]
        self.assertEqual(f["61"]["windows"], 2)
        self.assertEqual(f["61"]["in_flush_share_per_s"]["max"], 0.05)
        self.assertEqual(f["61"]["in_flush_ms_per_frame"]["max"], 5.0)   # 50 ms / 10 distinct
        self.assertEqual(f["61"]["finish_per_frame"]["max"], 0.2)
        self.assertIn("70", f)

    def test_malformed_counts_bad(self):
        txt = self.base() + "100.0\t[ 1.0] SWEET SWAPT: n 1 now abc\n" + "100.0\t[ 1.0] SWEET PROCCPU: bb_pid 1\n"
        txt += "100.0\t[ 1.0] SWEETGL: FLUSHT pid 1 sec x\n" + "SWEET PROCCPU: bb_pid 1 bb_us 2 sb_pid 3 sb_us 4\n"   # last: no host time
        self.mk("r", txt)
        rc, o, out = self.js("r")
        self.assertEqual(rc, 0)
        self.assertEqual(o["pg0"]["bad_lines"], 4)
        self.assertIn("bad_lines=4", out)
        self.assertIn("VERDICT: n/a", out)

    def test_verdict_branches(self):
        for bb, sb, distinct, want in ((5, 5, 10, "VERDICT: ca-wait"),        # 1 ms/frame, period 100 ms
                                       (300, 200, 10, "VERDICT: ca-cpu"),     # 50 ms/frame
                                       (200, 100, 10, "VERDICT: inconclusive (")):   # 30 ms/frame
            self.mk("r", self.base(distinct=distinct) + self.cpu(bb, sb))
            out = run("r", root=self.root)[1]
            self.assertIn(want, out)
        # period boundary 60 ms: 50 ms (distinct 20) with low CPU -> inconclusive; 70 ms (distinct 14) -> ca-wait
        self.mk("r", self.base(distinct=20) + self.cpu(5, 5))
        self.assertIn("VERDICT: inconclusive (", run("r", root=self.root)[1])
        self.mk("r", self.base(distinct=14) + self.cpu(5, 5))
        self.assertIn("VERDICT: ca-wait", run("r", root=self.root)[1])
        # ca-wait needs the slow period: cpu low but period 25 ms (distinct 40) -> inconclusive
        self.mk("r", self.base(distinct=40) + self.cpu(40, 40))
        self.assertIn("VERDICT: inconclusive (", run("r", root=self.root)[1])

    def test_swapt_n_continuity(self):
        def run_n(ns):
            self.mk("r", self.base() + "".join(swapt(1, n, 100 + n, 50, 10) for n in ns))
            g = self.js("r")[1]["pg0"]
            return g["swapt_n_gaps"], g["swapt_n_missing"], g["swapt_n_resets"]
        self.assertEqual(run_n([1, 2, 3, 4]), (0, 0, 0))
        self.assertEqual(run_n([1, 2, 6, 7]), (1, 3, 0))      # lines 3,4,5 dropped
        self.assertEqual(run_n([5, 6, 7, 1, 2, 4]), (1, 1, 1))  # restart at 1, then one gap
        self.assertIn("swapt_n_gaps=1 swapt_n_missing=3 swapt_n_resets=0", (run_n([1, 5]), run("r", root=self.root)[1])[1])

    def test_verdict_na(self):
        self.mk("r", self.base())
        self.assertIn("P-G0 lines: none", run("r", root=self.root)[1])
        self.mk("r", self.base() + swapt(1, 1, 2, 1, 0))
        self.assertIn("VERDICT: n/a (missing PROCCPU", run("r", root=self.root)[1])
        self.mk("r", fps(1, 5, 5) + self.cpu(1, 1))
        self.assertIn("VERDICT: n/a (missing busy SWEET FPS", run("r", root=self.root)[1])


@unittest.skipUnless(os.path.isdir(REAL), "no logs/runs")
class Real(unittest.TestCase):
    """Research 113 s.2 numbers (busy s, swaps med/max, distinct med/mean/max). Statistic = over busy seconds, one value per SWEET FPS line."""
    CASES = [("20261026T081916Z-gputest48-fps", 66, 23, 32, 15, 14.8, 28),
             ("20261026T101941Z-bench53-cpu-fps", 80, 22, 37, 11, 12.7, 28),
             ("20261026T122841Z-gputest54-ctl", 60, 23, 32, 15.5, 14.6, 30),
             ("20261026T160403Z-gputest65-absvsync", 55, 25, 40, 17, 17.7, 34)]

    def go(self, label, *extra):
        if not os.path.isdir(os.path.join(REAL, label)):
            self.skipTest("run dir %s missing" % label)
        rc, out = run("--json", *extra, label)
        self.assertEqual(rc, 0, out[-300:])
        return json.loads(out[:out.index("\n==")])[0]

    def test_section2(self):
        for label, busy, sm, sx, dm, dmean, dx in self.CASES:
            o = self.go(label)
            self.assertEqual((o["busy_s"], o["busy_swaps_median"], o["busy_swaps_max"], o["busy_distinct_median"], o["busy_distinct_max"]), (busy, sm, sx, dm, dx), label)
            self.assertAlmostEqual(o["busy_distinct_mean"], dmean, delta=0.06, msg=label)

    def test_join_g61(self):
        # note: 4.9 jobs, 4.1 ms busy, 2.2 ms wait per frame, 51 s. Tool (nearest PERF within 1 s, median of per-second ratios): 4.89/4.20/2.30, 51 s.
        o = self.go("20261026T143021Z-gputest61-pace", "--join-perf")["join"]
        self.assertEqual(o["seconds"], 51)
        self.assertAlmostEqual(o["jobs_per_frame_per_sec_median"], 4.9, delta=0.05)
        self.assertAlmostEqual(o["gpu_busy_ms_per_frame_per_sec_median"], 4.1, delta=0.15)
        self.assertAlmostEqual(o["submit_wait_ms_per_frame_per_sec_median"], 2.2, delta=0.15)

    def test_join_g60_g65_known_discrepancy(self):
        # note: g60 4.9/4.0/2.1 (32 s), g65 4.2/3.5/1.9 (46 s). The tool gets 5.23/4.20/2.24 (32 s) and 4.58/3.91/2.24 (45 s) with the closest
        # definition tried; the note's exact join is unknown. We pin the second counts and the order of magnitude, NOT the note's values.
        g60 = self.go("20261026T134714Z-gputest60-perf", "--join-perf")["join"]
        g65 = self.go("20261026T160403Z-gputest65-absvsync", "--join-perf")["join"]
        self.assertEqual(g60["seconds"], 32)
        self.assertIn(g65["seconds"], (45, 46))
        for j in (g60, g65):
            self.assertTrue(3.5 < j["jobs_per_frame_per_sec_median"] < 6, j)
            self.assertTrue(3 < j["gpu_busy_ms_per_frame_per_sec_median"] < 5, j)


if __name__ == "__main__":
    unittest.main()
