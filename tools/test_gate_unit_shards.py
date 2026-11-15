"""Self-check for tools/gate-unit-shards: equal counts on a toy dir, a failing test fails the run, the dynamic shards (PLAN-GATE-2X K4b: slots grow when lane A is done) and the per-file selection (S4: --selected + GATE_UNIT_ONLY)."""
import os, sys, pathlib, re, shutil, subprocess, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gate-unit-shards")
OK = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): pass\n"
BAD = "import unittest\nclass T(unittest.TestCase):\n    def test_c(self): self.fail('x')\n"


def run(files, extra=None, setup=None, args=()):
    """gate-unit-shards on a toy dir. GATE_UNIT_ONLY is never inherited from the caller (a quick gate exports the REAL list to every step, review gate-cold-s4-r1 F1): only `extra` sets it."""
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "t"); os.mkdir(t)
        for n, c in files.items():
            with open(os.path.join(t, n), "w") as fh:
                fh.write(c)
        env = dict({k: v for k, v in os.environ.items() if k != "GATE_UNIT_ONLY"}, GATE_UNIT_DIR=t, GATE_UNIT_OUT=os.path.join(d, "o"), **(extra or {}))
        for k in ("GATE_UNIT_SHARDS", "GATE_UNIT_SHARDS_SRC", "GATE_UNIT_SHARDS_MAX", "GATE_LANES_A_DONE", "GATE_MUT_JOBS", "GATE_UNIT_DURATIONS", "GATE_UNIT_DURATIONS_NEW"):   # a gate exports some of these to its own children
            if not extra or k not in extra:
                env.pop(k, None)
        if setup:
            env.update(setup(d))
        p = subprocess.run([TOOL, *args], env=env, capture_output=True, text=True)
        return p.returncode, p.stdout


# a timed toy test: logs "start/end <time>" to $MARK/<name> so the peak concurrency of the shards can be computed; `first` also creates the lanes-a-done file
def timed(name, secs, signal=False, pad=0):
    return ("import unittest, os, time\n# " + "x" * pad + "\nclass T(unittest.TestCase):\n    def test_t(self):\n"
            "        m = os.environ['MARK']\n        open(os.path.join(m, %r + '.s'), 'w').write(repr(time.time()))\n" % name
            + "        time.sleep(%s)\n        open(os.path.join(m, %r + '.e'), 'w').write(repr(time.time()))\n" % (secs, name)
            + ("        open(os.environ['GATE_LANES_A_DONE'], 'w').close()\n" if signal else ""))


def rd(path):
    with open(path) as fh:
        return fh.read()


def peak(mark, names):
    ev = []
    for n in names:
        ev.append((float(rd(os.path.join(mark, n + ".s"))), 1))
        ev.append((float(rd(os.path.join(mark, n + ".e"))), -1))
    cur = best = 0
    for _t, d in sorted(ev, key=lambda e: (e[0], e[1])):   # an end before a start at the same instant
        cur += d; best = max(best, cur)
    return best
SKIP = "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('no adt here')\n    def test_s(self): pass\n    def test_a(self): pass\n"


def allow_setup(text):
    """setup= for run(): a skip-pin file (tools/unit-skip-allow.txt format) with this text, passed as GATE_UNIT_SKIP_ALLOW."""
    def setup(d):
        p = os.path.join(d, "allow.txt")
        with open(p, "w") as fh:
            fh.write(text)
        return {"GATE_UNIT_SKIP_ALLOW": p}
    return setup


class ShardsTest(unittest.TestCase):
    def test_equal_counts(self):
        rc, out = run({"test_x.py": OK, "test_y.py": OK})
        self.assertEqual(rc, 0, out)
        self.assertIn("sharded 4 == unsharded 4", out)

    def test_failing_shard_fails(self):
        rc, out = run({"test_x.py": OK, "test_y.py": BAD})
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL", out)

    def test_mutant_file_is_not_a_shard(self):
        rc, out = run({"test_x.py": OK, "test_wlan_wr_mutants.py": BAD})
        self.assertEqual(rc, 0, out)
        self.assertIn("sharded 2 == unsharded 2", out)

    def test_exclude_override_is_rejected(self):
        rc, out = run({"test_x.py": OK, "test_y.py": OK}, {"GATE_UNIT_EXCLUDE": "test_x.py"})
        self.assertEqual(rc, 2, out)
        self.assertIn("no longer supported", out)

    def test_skip_is_named(self):
        rc, out = run({"test_x.py": SKIP}, setup=allow_setup("test_x.py::T.test_s | no adt here | the host has no ADT file\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped=1", out)
        self.assertRegex(out, r"SKIPPED TEST test_x\.py: test_s .*skipped 'no adt here'")
        self.assertNotIn("SKIP NOT ALLOWED", out)

    def test_skip_counts_environment_and_other(self):
        # T-9fb9: check-all.sh registers a PASS when "other=0"; a pin whose reason starts with "environment" counts as environment, any other pin as other
        rc, out = run({"test_x.py": SKIP}, setup=allow_setup("test_x.py::T.test_s | no adt here | environment (toolchain): the host lacks the tool\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("unit skips: environment=1 other=0 ", out)
        rc, out = run({"test_x.py": SKIP}, setup=allow_setup("test_x.py::T.test_s | no adt here | the host has no ADT file\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("unit skips: environment=0 other=1 ", out)

    def test_skip_after_stderr_line_is_named_and_checked(self):
        # T-9fb9: a test that prints to stderr before skipping puts "skipped 'why'" on the NEXT line; it must still be named, pinned or refused (it used to be counted in skipped=N only)
        pre = "import unittest, sys\nclass T(unittest.TestCase):\n    def test_s(self):\n        print('SKIP: noisy', file=sys.stderr)\n        self.skipTest('no tool here')\n    def test_a(self): pass\n"
        rc, out = run({"test_x.py": pre}, setup=allow_setup("test_x.py::T.test_s | no tool here | environment (toolchain): the host lacks the tool\n"))
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"SKIPPED TEST test_x\.py: .*test_s .*skipped 'no tool here'")
        self.assertIn("unit skips: environment=1 other=0 ", out)
        rc, out = run({"test_x.py": pre}, setup=allow_setup(""))
        self.assertEqual(rc, 1, out)
        self.assertIn("SKIP NOT ALLOWED test_x.py::T.test_s", out)

    def test_unpinned_skip_fails(self):
        rc, out = run({"test_x.py": SKIP}, setup=allow_setup(""))
        self.assertEqual(rc, 1, out)
        self.assertIn("SKIP NOT ALLOWED test_x.py::T.test_s", out)

    def test_pin_with_changed_text_short_reason_or_other_class_fails(self):
        for allow in ("test_x.py::T.test_s | other text | the host has no ADT file\n", "test_x.py::T.test_s | no adt here | short\n",
                      "test_x.py::U.test_s | no adt here | the host has no ADT file\n"):
            rc, out = run({"test_x.py": SKIP}, setup=allow_setup(allow))
            self.assertEqual(rc, 1, allow + out)
            self.assertIn("SKIP NOT ALLOWED test_x.py::T.test_s", out)

    def test_expected_failure_needs_a_pin(self):
        xf = "import unittest\nclass T(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_d(self): self.fail('x')\n"
        rc, out = run({"test_x.py": xf}, setup=allow_setup(""))
        self.assertEqual(rc, 1, out)
        self.assertIn("SKIP NOT ALLOWED test_x.py::T.test_d", out)
        rc, out = run({"test_x.py": xf}, setup=allow_setup("test_x.py::T.test_d | expected failure | known bug tracked in a ticket\n"))
        self.assertEqual(rc, 0, out)

    # ---- dynamic shards (K4b) ----
    def _timed(self, extra, signal_idx, names, secs, cpus=None, tool=TOOL):
        """Toy files named by `names` (biggest file = first, so it starts first); file number signal_idx creates the lanes-a-done file when it ends. Returns rc, text, peaks.
        cpus=N runs the tool pinned to N CPUs (taskset), so `nproc` is N: the nproc-2 cap becomes testable on any host."""
        files = {"test_%s.py" % n: timed(n, secs, signal=(i == signal_idx), pad=(len(names) - i) * 100) for i, n in enumerate(names)}
        peaks = {}
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "t"); os.mkdir(t)
            mark = os.path.join(d, "mark"); os.mkdir(mark)
            for n, c in files.items():
                with open(os.path.join(t, n), "w") as fh:
                    fh.write(c)
            env = {k: v for k, v in os.environ.items() if not k.startswith(("GATE_UNIT_SHARDS", "GATE_LANES", "GATE_MUT_JOBS"))}
            env.update(GATE_UNIT_DIR=t, GATE_UNIT_OUT=os.path.join(d, "o"), MARK=mark, GATE_LANES_A_DONE=os.path.join(d, "lanes-a-done"), **extra)
            if tool != TOOL:                        # a mutant copy lives at <tmp>/tools/gate-unit-shards: the script cds to its parent's parent
                os.makedirs(os.path.join(d, "tools")); sc = os.path.join(d, "tools", "gate-unit-shards"); shutil.copy(tool, sc); tool = sc
            cmd = [tool]
            if cpus:
                allowed = sorted(os.sched_getaffinity(0))
                if len(allowed) < cpus:
                    self.skipTest("needs %d allowed CPUs for taskset, have %d" % (cpus, len(allowed)))
                cmd = ["taskset", "-c", ",".join(map(str, allowed[:cpus]))] + cmd
            p = subprocess.run(cmd, env=env, capture_output=True, text=True)
            for k, sel in (("all", names), ("early", names[:signal_idx + 1]), ("late", names[signal_idx + 1:])):
                peaks[k] = peak(mark, [n for n in sel if os.path.exists(os.path.join(mark, n + ".e"))]) if sel else 0
        return p.returncode, p.stdout, peaks

    def test_shards_grow_when_lane_a_is_done(self):
        if len(os.sched_getaffinity(0)) < 5:
            self.skipTest("needs nproc >= 5 for a cap of 3 (GATE_UNIT_SHARDS_MAX=3)")
        rc, out, pk = self._timed({"GATE_UNIT_SHARDS": "1", "GATE_UNIT_SHARDS_SRC": "derived", "GATE_UNIT_SHARDS_MAX": "3"}, 1, ("a", "b", "c", "d", "e", "f"), 1.2)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"unit shards 1->3 at \+[0-9]+s")
        self.assertEqual(pk["early"], 1, "before the signal one shard at a time: %s" % out)
        self.assertEqual(pk["late"], 3, "after the signal the other four run 3 at a time (the cap), never more: %s" % out)
        self.assertIn("token check: 3 == 3 slots", out)
        self.assertIn("sharded 6 == unsharded 6", out)

    def test_shards_never_grow_without_the_signal(self):
        rc, out, pk = self._timed({"GATE_UNIT_SHARDS": "2", "GATE_UNIT_SHARDS_SRC": "derived", "GATE_UNIT_SHARDS_MAX": "6"}, 99, ("a", "b", "c", "d"), 0.8)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"unit shards stayed 2 \(lane A was still running")
        self.assertNotIn("unit shards 2->", out)
        self.assertEqual(pk["all"], 2, out)
        self.assertIn("token check: 2 == 2 slots", out)

    def test_explicit_shard_count_never_grows(self):
        for extra in ({"GATE_UNIT_SHARDS": "1", "GATE_UNIT_SHARDS_SRC": "env", "GATE_UNIT_SHARDS_MAX": "3"}, {"GATE_UNIT_SHARDS": "1", "GATE_UNIT_SHARDS_MAX": "3"}):
            with self.subTest(extra=extra):
                rc, out, pk = self._timed(extra, 0, ("a", "b", "c"), 0.5)
                self.assertEqual(rc, 0, out)
                self.assertIn("unit shards stayed 1 (GATE_UNIT_SHARDS set explicitly, no growth)", out)
                self.assertEqual(pk["all"], 1, out)

    def test_cap_is_never_exceeded(self):
        rc, out, pk = self._timed({"GATE_UNIT_SHARDS": "2", "GATE_UNIT_SHARDS_SRC": "derived", "GATE_UNIT_SHARDS_MAX": "2"}, 0, ("a", "b", "c", "d"), 0.5)
        self.assertEqual(rc, 0, out)
        self.assertIn("unit shards stayed 2 (already at the cpu cap min(2,nproc-2-0)=2)", out)
        self.assertEqual(pk["all"], 2, out)

    def test_bad_max_is_an_error(self):
        rc, out = run({"test_x.py": OK}, {"GATE_UNIT_SHARDS_MAX": "x"})
        self.assertEqual(rc, 2, out)

    def _cap_case(self, tool=TOOL, mut_jobs=None):
        """nproc 6: the shards (base 1, max 8) may grow to 6-2-GATE_MUT_JOBS; six files after the one that signals."""
        extra = {"GATE_UNIT_SHARDS": "1", "GATE_UNIT_SHARDS_SRC": "derived", "GATE_UNIT_SHARDS_MAX": "8"}
        if mut_jobs is not None:
            extra["GATE_MUT_JOBS"] = str(mut_jobs)
        return self._timed(extra, 0, ("a", "b", "c", "d", "e", "f", "g"), 1.0, cpus=6, tool=tool)

    def test_cap_is_nproc_minus_2_minus_lane_c_jobs(self):
        rc, out, pk = self._cap_case()
        self.assertEqual(rc, 0, out)
        self.assertIn("unit shard cap 4 = min(GATE_UNIT_SHARDS_MAX=8, nproc 6 - 2 - lane C mutant jobs 0)", out)
        self.assertRegex(out, r"unit shards 1->4 at ")
        self.assertEqual(pk["late"], 4, "nproc 6 (taskset): the cap is nproc-2=4, never nproc: %s" % out)
        rc, out, pk = self._cap_case(mut_jobs=2)           # lane C still runs 2 mutant jobs while the shards grow (F5): 6-2-2 = 2
        self.assertEqual(rc, 0, out)
        self.assertIn("unit shard cap 2 = min(GATE_UNIT_SHARDS_MAX=8, nproc 6 - 2 - lane C mutant jobs 2)", out)
        self.assertRegex(out, r"unit shards 1->2 at ")
        self.assertEqual(pk["late"], 2, out)

    def test_cap_mutants_are_killed(self):
        src = open(TOOL).read()
        muts = [("cap ignores nproc-2", "cap=$((np - 2 - mj))", "cap=$((np - mj))"), ("cap ignores lane C jobs", "cap=$((np - 2 - mj))", "cap=$((np - 2))"),
                ("cap uses the whole host", "np=$(nproc 2>/dev/null)", "np=$(grep -c ^processor /proc/cpuinfo)")]
        with tempfile.TemporaryDirectory() as d:
            for name, old, new in muts:
                with self.subTest(mutant=name):
                    self.assertEqual(src.count(old), 1, "needle %r must occur once" % old)
                    m = os.path.join(d, "m.sh"); open(m, "w").write(src.replace(old, new)); os.chmod(m, 0o755)
                    got = []
                    for mj in (None, 2):
                        _rc, out, pk = self._cap_case(tool=m, mut_jobs=mj)
                        got.append(pk["late"])
                    self.assertNotEqual(got, [4, 2], "mutant %r SURVIVED (peaks %r)" % (name, got))

    def test_nonnumeric_lane_c_jobs_or_absurd_shards_are_errors(self):
        rc, out = run({"test_x.py": OK}, {"GATE_MUT_JOBS": "x"})
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR GATE_MUT_JOBS", out)
        rc, out = run({"test_x.py": OK}, {"GATE_UNIT_SHARDS": "100000"})
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR GATE_UNIT_SHARDS and GATE_UNIT_SHARDS_MAX must be <= 1024", out)

    # ---- rule 8: the silent tails are loud (review gate-cold-k14 r1 F8) ----
    def test_unwritable_new_table_is_a_named_gap(self):
        rc, out = run({"test_x.py": OK}, {"GATE_UNIT_DURATIONS_NEW": "/proc/nonexistent/n.tsv"})
        self.assertEqual(rc, 0, out)
        self.assertIn("GAP: DURATIONS NOT WRITTEN: cannot write /proc/nonexistent/n.tsv", out)
        self.assertRegex(out, r"RESULT: PASS .* gaps=1$")

    def test_malformed_and_duplicate_table_rows_are_named(self):
        def setup(d):
            p = os.path.join(d, "t.tsv")
            open(p, "w").write("# c\n5 test_a.py\nabc\ttest_b.py\n7\ttest_c.py\r\n9\ttest_d.py\n9\ttest_d.py\n")
            return {"GATE_UNIT_DURATIONS": p}
        rc, out = run({"test_a.py": OK, "test_b.py": OK, "test_c.py": OK, "test_d.py": OK}, setup=setup)
        self.assertEqual(rc, 0, out)
        for n in (":2: 5 test_a.py", ":3: abc\ttest_b.py", ":4: 7\ttest_c.py"):
            self.assertRegex(out, r"TABLE ROW IGNORED \(malformed, file runs as unknown\) \S+" + re.escape(n))
        self.assertRegex(out, r"TABLE ROW DUPLICATE \S+: test_d.py has more than one row")
        self.assertRegex(out, r"RESULT: PASS .* gaps=4$")

    def test_unknown_src_value_is_a_named_gap(self):
        rc, out = run({"test_x.py": OK}, {"GATE_UNIT_SHARDS": "1", "GATE_UNIT_SHARDS_SRC": "dervied"})
        self.assertEqual(rc, 0, out)
        self.assertIn("GAP: GATE_UNIT_SHARDS_SRC='dervied' is neither env nor derived", out)
        self.assertRegex(out, r"gaps=1$")

    def test_a_stale_table_row_prints_duration_drift(self):
        def setup(d):
            p = os.path.join(d, "t.tsv")
            open(p, "w").write("50\ttest_x.py\n3\ttest_y.py\n")        # x really takes ~0 s (drift), y 3 s vs ~0 s is within the 10 s slack
            return {"GATE_UNIT_DURATIONS": p}
        rc, out = run({"test_x.py": OK, "test_y.py": OK}, setup=setup)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"DURATION DRIFT test_x.py: table 50 s -> now [0-9.]+ s")
        self.assertNotIn("DURATION DRIFT test_y.py", out)
        self.assertRegex(out, r"drift=1 gaps=0$")

    def test_token_accounting_after_a_failing_shard(self):
        rc, out = run({"test_x.py": OK, "test_y.py": BAD, "test_z.py": OK}, {"GATE_UNIT_SHARDS": "2"})
        self.assertEqual(rc, 1, out)
        self.assertIn("SHARD FAIL test_y.py", out)
        self.assertNotIn("TOKEN MISMATCH", out)   # the failed shard still returned its token
        self.assertEqual(out.count("unit shards stayed 2"), 1)

    def test_token_drain_survives_a_timed_read_that_eats_a_byte(self):
        """T-209d: bash's `read -t` can lose the byte it was reading when the timeout fires under load (8 slots, 7 tokens back).
        BASH_ENV wraps `read`: a SHORT timed read (-t 0.2 / -t 0.5) with data waiting swallows one byte and reports a timeout. The run must still PASS."""
        def setup(d):
            f = os.path.join(d, "stall.sh")
            with open(f, "w") as fh:
                fh.write('read() { case " $* " in *" -t 0.2 "*|*" -t 0.5 "*) if builtin read -t 0 -u 9 2>/dev/null; then builtin read -r -n1 -u 9 _; return 142; fi;; esac; builtin read "$@"; }\n')
            return {"BASH_ENV": f}
        rc, out = run({"test_x.py": OK, "test_y.py": OK, "test_z.py": OK}, {"GATE_UNIT_SHARDS": "2"}, setup=setup)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("TOKEN MISMATCH", out)
        self.assertRegex(out, r"token check: 2 == 2 slots")


# ---- order by recorded duration (K4c) ----
SLOW, BIG, UNK = 2.0, 0.6, 0.1
TABLE = {"test_slow.py": SLOW, "test_big1.py": BIG, "test_big2.py": BIG, "test_big3.py": BIG}      # test_unk.py is NOT in the table


def simulate(order, dur, n):
    """Makespan of `order` on n workers (the next file goes to the first free worker): what the shards do."""
    free = [0.0] * n
    for f in order:
        i = free.index(min(free))
        free[i] += dur[f]
    return max(free)


def order_run(script, shards, table=True, new=False):
    """Run `script` (a copy at <tmp>/tools/gate-unit-shards) on toy files: the slow test is the SMALLEST file, the three big ones are padded. Returns (start order, wall seconds, stdout, new-table text)."""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "tools")); sc = os.path.join(d, "tools", "gate-unit-shards"); shutil.copy(script, sc)
        t = os.path.join(d, "t"); os.mkdir(t); mark = os.path.join(d, "mark"); os.mkdir(mark)
        spec = {"slow": (SLOW, 0), "big1": (BIG, 3000), "big2": (BIG, 2000), "big3": (BIG, 1000), "unk": (UNK, 10)}
        for n, (secs, pad) in spec.items():
            open(os.path.join(t, "test_%s.py" % n), "w").write(timed(n, secs, pad=pad))
        tbl = os.path.join(d, "durations.tsv")
        open(tbl, "w").write("# comment\n" + "".join("%s\t%s\n" % (v, k) for k, v in TABLE.items()))
        newp = os.path.join(d, "out", "new.tsv")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GATE_UNIT", "GATE_LANES", "GATE_MUT_JOBS"))}
        env.update(GATE_UNIT_DIR=t, GATE_UNIT_OUT=os.path.join(d, "o"), GATE_UNIT_SHARDS=str(shards), GATE_UNIT_DURATIONS=tbl if table else os.path.join(d, "missing.tsv"),
                   GATE_UNIT_DURATIONS_NEW=newp, MARK=mark)
        p = subprocess.run([sc], env=env, capture_output=True, text=True)
        st = {n: float(rd(os.path.join(mark, n + ".s"))) for n in spec}
        en = {n: float(rd(os.path.join(mark, n + ".e"))) for n in spec}
        return ["test_%s.py" % n for n in sorted(st, key=st.get)], max(en.values()) - min(st.values()), p.stdout, (rd(newp) if os.path.exists(newp) else ""), rd(tbl)


def order_errors(script, timing=True):
    """[] when the script starts files unknown first, then slowest first (shards=1: the start order is the dispatch order), and beats the size order; else the defects found."""
    errs = []
    order, _w, out, new, _tbl = order_run(script, 1)
    want = ["test_unk.py", "test_slow.py", "test_big1.py", "test_big2.py", "test_big3.py"]
    if order != want:
        errs.append("start order %r, want %r" % (order, want))
    if "durations from" not in out or "(4 known, 1 unknown)" not in out:
        errs.append("no/incorrect order line: %r" % out.splitlines()[:2])
    if not timing:
        return errs
    size_order, wall_size, _o, _n, _t = order_run(script, 2, table=False)
    _o2, wall_dur, _o3, _n2, _t2 = order_run(script, 2)
    if not wall_dur + 0.3 < wall_size:
        errs.append("duration order %.2f s is not 0.3 s faster than size order %.2f s on 2 shards" % (wall_dur, wall_size))
    return errs


MUT = [("table dropped (size order)", 'awk -v tbl="$tbl"', 'awk -v tbl="/nonexistent"'),
       ("durations ascending", "-k2,2gr", "-k2,2g"),
       ("unknown last", "-k1,1n ", "-k1,1nr ")]


# T-2712: OrderTest runs from tools/test_gate_unit_shards_order.py (own file, so neither is above ~60 s); kept under a private name so this file's discovery does not run it twice
class _OrderTest(unittest.TestCase):
    def test_slow_tiny_test_starts_first_and_unknown_before_it(self):
        self.assertEqual(order_errors(TOOL), [])

    def test_makespan_simulation_prefers_duration_order(self):
        dur = dict(TABLE, **{"test_unk.py": UNK})
        by_size = ["test_big1.py", "test_big2.py", "test_big3.py", "test_slow.py", "test_unk.py"]
        by_dur = ["test_unk.py", "test_slow.py", "test_big1.py", "test_big2.py", "test_big3.py"]
        self.assertLess(simulate(by_dur, dur, 2) + 0.5, simulate(by_size, dur, 2))
        order, _w, _o, _n, _t = order_run(TOOL, 1)
        self.assertEqual(order, by_dur)

    def test_new_durations_file_is_written_and_the_table_is_not(self):
        _ord, _w, out, new, tbl = order_run(TOOL, 2)
        rows = [l.split("\t") for l in new.splitlines() if not l.startswith("#")]
        self.assertEqual(sorted(r[1] for r in rows), sorted(["test_slow.py", "test_big1.py", "test_big2.py", "test_big3.py", "test_unk.py"]))
        self.assertEqual([float(r[0]) for r in rows], sorted((float(r[0]) for r in rows), reverse=True))
        self.assertGreater(float(dict((r[1], r[0]) for r in rows)["test_slow.py"]), 1.0)
        self.assertEqual(tbl.count("test_unk"), 0, "the committed table must never be edited")

    def test_missing_table_is_loud(self):
        _ord, _w, out, _n, _t = order_run(TOOL, 2, table=False)
        self.assertIn("MISSING, size order", out)

    def test_mutants_of_the_ordering_are_killed(self):
        src = open(TOOL).read()
        with tempfile.TemporaryDirectory() as d:
            for name, old, new in MUT:
                with self.subTest(mutant=name):
                    self.assertEqual(src.count(old), 1, "needle %r must occur once" % old)
                    m = os.path.join(d, "m.sh"); open(m, "w").write(src.replace(old, new)); os.chmod(m, 0o755)
                    self.assertTrue(order_errors(m, timing=False), "mutant %r SURVIVED" % name)

    def test_seed_table_rows_name_existing_test_files(self):
        here = os.path.dirname(TOOL)
        with open(os.path.join(here, "gate-unit-durations.tsv")) as fh:
            rows = [l.rstrip("\n") for l in fh if l.strip() and not l.startswith("#")]
        self.assertGreater(len(rows), 50)
        for r in rows:
            self.assertRegex(r, r"^[0-9]+(\.[0-9]+)?\ttest_[A-Za-z0-9_]+\.py$")
            self.assertTrue(os.path.exists(os.path.join(here, r.split("\t")[1])), "stale row (file gone): %s" % r)
        secs = [float(r.split("\t")[0]) for r in rows]
        self.assertEqual(secs, sorted(secs, reverse=True))
        self.assertLess(max(secs), 150, "a file above ~150 s sets lane B's floor: split it (measured 2026-10-31: test_wlan_mutant_pool 125 s, test_xnu_remote_build 81 s, test_audit_memo_mutations 79 s)")


PARTS = {"order": _OrderTest}
del _OrderTest      # not a module attribute any more: unittest discovery of THIS file does not collect it


class OnlyTest(unittest.TestCase):
    """Per-file selection (S4): --selected + GATE_UNIT_ONLY."""
    def only(self, names, files=None, content=None):
        """GATE_UNIT_ONLY=<file listing names>: run that subset; the count check compares the same subset (S4)."""
        with tempfile.TemporaryDirectory() as d:
            lst = os.path.join(d, "only.txt")
            if content is None:
                open(lst, "w").write("".join(n + "\n" for n in names))
            else:
                open(lst, "w").write(content)
            return run(files or {"test_x.py": OK, "test_y.py": BAD, "test_z.py": SKIP}, {"GATE_UNIT_ONLY": lst}, args=("--selected",),
                       setup=allow_setup("test_z.py::T.test_s | no adt here | the toy host has no ADT file\n"))

    def test_only_runs_the_named_files_and_counts_that_subset(self):
        rc, out = self.only(["test_x.py", "test_z.py"])
        self.assertEqual(rc, 0, out)                 # the failing test_y.py is not selected, so it must not run
        self.assertIn("sharded 4 == unsharded 4", out)
        self.assertIn("in 2 files", out)

    def test_only_selected_failure_still_fails(self):
        rc, out = self.only(["test_y.py"])
        self.assertEqual(rc, 1, out)
        self.assertIn("SHARD FAIL test_y.py", out)

    def test_only_unknown_empty_or_excluded_name_is_an_error(self):
        for names in (["test_nope.py"], ["test_wlan_wr_mutants.py"]):
            rc, out = self.only(names, {"test_x.py": OK, "test_wlan_wr_mutants.py": BAD})
            self.assertEqual(rc, 2, out)
            self.assertIn("not a test file run by this step", out)
        rc, out = self.only([], content="")
        self.assertEqual((rc, "is missing, empty" in out), (2, True), out)

    def test_only_empty_variable_means_all(self):
        rc, out = run({"test_x.py": OK, "test_y.py": OK}, {"GATE_UNIT_ONLY": ""}, args=("--selected",))
        self.assertEqual(rc, 0, out)
        self.assertIn("sharded 4 == unsharded 4", out)
        self.assertNotIn("partial=", out)

    # ---- F1/F7: an inherited GATE_UNIT_ONLY is ignored (loudly) unless the caller passes --selected
    def test_inherited_variable_without_the_flag_is_ignored_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            lst = os.path.join(d, "only.txt"); open(lst, "w").write("test_x.py\n")
            rc, out = run({"test_x.py": OK, "test_y.py": OK}, {"GATE_UNIT_ONLY": lst})
        self.assertEqual(rc, 0, out)
        self.assertIn("sharded 4 == unsharded 4", out)         # all files ran
        self.assertIn("in 2 files", out)
        self.assertIn("ignoring inherited GATE_UNIT_ONLY=", out)
        self.assertNotIn("partial=", out)

    def test_inherited_variable_naming_nothing_real_does_not_break_a_plain_run(self):
        rc, out = run({"test_x.py": OK}, {"GATE_UNIT_ONLY": "/nonexistent/real-list.txt"})
        self.assertEqual(rc, 0, out)
        self.assertIn("ignoring inherited GATE_UNIT_ONLY=", out)

    def test_the_helper_does_not_inherit_the_callers_variable(self):
        """The failure the quick gate hit: this test file ran under check-all.sh's exported GATE_UNIT_ONLY and its toy runs saw the real list."""
        old = os.environ.get("GATE_UNIT_ONLY")
        os.environ["GATE_UNIT_ONLY"] = "/nonexistent/real-list.txt"
        try:
            rc, out = run({"test_x.py": OK})
        finally:
            if old is None:
                del os.environ["GATE_UNIT_ONLY"]
            else:
                os.environ["GATE_UNIT_ONLY"] = old
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ignoring inherited", out)
        self.assertNotIn("missing", out)

    # ---- F5: a list that selects nothing, or names a test twice, or has odd lines
    def test_only_blank_only_list_is_an_error(self):
        for content in ("\n\n", "  \n\t\n", "\n"):
            rc, out = self.only([], content=content)
            self.assertEqual(rc, 2, (content, out))
            self.assertIn("names no test file", out)
            self.assertNotIn("PASS", out)

    def test_only_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = run({"test_x.py": OK}, {"GATE_UNIT_ONLY": d}, args=("--selected",))
        self.assertEqual(rc, 2, out)
        self.assertIn("not a regular file", out)
        self.assertNotIn("RESULT: PASS", out)

    def test_only_duplicate_name_runs_once(self):
        rc, out = self.only(["test_x.py", "test_x.py", "test_x.py"])
        self.assertEqual(rc, 0, out)
        self.assertIn("in 1 files", out)
        self.assertIn("sharded 2 == unsharded 2", out)
        self.assertIn("partial=1/3", out)
        self.assertIn("gate: unit shards: ran 1 of 3 test files", out)   # the note counts distinct files (a duplicate line must not inflate it; the run list is rebuilt from the ranked list, so only the note shows a dropped dedupe)

    def test_only_line_with_spaces_or_glob_or_path_or_cr_is_rejected_not_split(self):
        for content in ("test_x.py test_z.py\n", " test_x.py\n", "test_x.py \n", "*\n", "test_*.py\n", "../t/test_x.py\n", "test_x.py\r\n", "\ttest_x.py\n"):
            rc, out = self.only([], content=content)
            self.assertEqual(rc, 2, (content, out))
            self.assertIn("is not a test_NAME.py basename", out)

    def test_only_blank_lines_between_names_and_a_last_line_without_newline(self):
        rc, out = self.only([], content="\ntest_x.py\n  \n\ntest_z.py")
        self.assertEqual(rc, 0, out)
        self.assertIn("in 2 files", out)
        rc, out = self.only([], content="test_x.py")            # no trailing newline at all
        self.assertEqual(rc, 0, out)
        self.assertIn("in 1 files", out)

    def test_only_partial_tag_counts_selected_of_all(self):
        rc, out = self.only(["test_x.py", "test_z.py"])
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped=1 partial=2/3", out)             # test_z has one skip; the 3 test files here are x, y, z
        self.assertIn("gate: unit shards: ran 2 of 3 test files", out)

    def test_flag_is_the_only_argument(self):
        rc, out = run({"test_x.py": OK}, args=("--bogus",))
        self.assertEqual(rc, 2, out)
        self.assertIn("usage:", out)


MUTANTS = [   # (name, anchor text in tools/gate-unit-shards, replacement); each must make at least one OnlyTest fail (AP-281: an anchor that no longer matches once fails loudly)
    ("env honoured without --selected", 'if [ -n "$selected" ]; then only=${GATE_UNIT_ONLY:-}', 'if true; then only=${GATE_UNIT_ONLY:-}'),
    ("blank-only list accepted", '[ "$nsel" -gt 0 ] || {', 'true || {'),
    ("duplicates not dropped", '[ -z "${seen[$b]:-}" ] || continue', ':'),
    ("directory list accepted", '[ -f "$only" ] && [ -s "$only" ]', '[ -s "$only" ]'),
    ("loop not quoted (read without IFS=)", 'while IFS= read -r b', 'while read -r b'),
    ("name regex dropped", '[[ $b =~ ^test_[A-Za-z0-9_]+\\.py$ ]] || {', 'true || {'),
    ("blank lines not skipped", '[[ $b =~ ^[[:space:]]*$ ]] && continue', ':'),
    ("last line without newline lost", '|| [ -n "$b" ]; do', '; do'),
    ("partial tag dropped", '${only:+ partial=$nf/$nall}', ''),
    ("selection note dropped", 'echo "gate: unit shards: ran $nsel of $nall test files (GATE_UNIT_ONLY=$only)"', ':'),
]


class ShardMutants(unittest.TestCase):
    def mutant_fails(self, old, new):
        global TOOL
        src = pathlib.Path(TOOL).read_text()
        self.assertEqual(src.count(old), 1, "mutant anchor %r matches %d times" % (old, src.count(old)))
        real = TOOL
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "tools").mkdir()
            m = pathlib.Path(d) / "tools" / "gate-unit-shards"
            m.write_text(src.replace(old, new)); m.chmod(0o755)
            TOOL = str(m)
            try:
                res = unittest.TextTestRunner(stream=open(os.devnull, "w"), failfast=True).run(unittest.defaultTestLoader.loadTestsFromTestCase(OnlyTest))
            finally:
                TOOL = real
        return not res.wasSuccessful()

    def test_every_mutant_is_killed(self):
        survived = [n for n, old, new in MUTANTS if not self.mutant_fails(old, new)]
        print("\ngate-unit-shards mutants killed: %d of %d" % (len(MUTANTS) - len(survived), len(MUTANTS)))
        self.assertEqual(survived, [])


if __name__ == "__main__":
    res = unittest.main(exit=False).result
    if res.wasSuccessful():
        print("RESULT: PASS test_gate_unit_shards cases=%d" % res.testsRun)
    else:
        print("RESULT: FAIL test_gate_unit_shards failures=%d errors=%d" % (len(res.failures), len(res.errors)))
    sys.exit(0 if res.wasSuccessful() else 1)
