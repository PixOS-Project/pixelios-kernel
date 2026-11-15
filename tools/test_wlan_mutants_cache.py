"""Tests for tools/wlan_mutant_cache.py and its use in test_wlan_wr_mutants.build_and_run (research 71): a hit gives the fresh verdict, any input byte
invalidates, timeouts and infra errors are never cached, a cached survivor is still a survivor."""
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import test_wlan_wr_mutants as M
import wlan_mutant_cache as C
import wlan_mutant_pool as P

CHIP = "/* chip */\n"
OK = "#include <stdint.h>\n#define X 1\n"


class Env(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.cache = os.path.join(self.td.name, "cache")
        self.work = os.path.join(self.td.name, "work")
        os.makedirs(self.work)
        # tiny stand-in tests: the first returns exit code from the header macro, the second always passes
        self.t1 = os.path.join(self.td.name, "t1.c")
        self.t2 = os.path.join(self.td.name, "t2.c")
        self.write(self.t1, '#include <stdio.h>\n#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { if (RC) fputs("RESULT: FAIL stand-in\\n", stderr); return RC; }\n')
        self.write(self.t2, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { return 0; }\n')
        p = [mock.patch.dict(os.environ, {"GATE_MUT_CACHE": self.cache}), mock.patch.object(M, "TEST_NV", self.t1), mock.patch.object(M, "TEST", self.t2)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.runs = 0
        real = M.cpu_timeout.run_cpu_limited      # gcc now runs through cpu_timeout (trunk AP-105 merge)

        def counting(cmd, *a, **k):
            if cmd and cmd[0] == "gcc" and "--version" not in cmd:
                self.runs += 1
            return real(cmd, *a, **k)
        px = mock.patch.object(M.cpu_timeout, "run_cpu_limited", counting)
        px.start()
        self.addCleanup(px.stop)

    @staticmethod
    def write(p, t):
        with open(p, "w") as f:
            f.write(t)

    def hdr(self, rc):
        return "#define RC %d\n" % rc

    def test_hit_returns_the_fresh_verdict(self):
        for rc, st in ((0, "pass"), (3, "fail")):
            fresh = M.build_and_run(self.hdr(rc), CHIP, self.work)
            n = self.runs
            self.assertEqual(fresh[0], st)
            self.assertEqual(M.build_and_run(self.hdr(rc), CHIP, self.work), fresh)
            self.assertEqual(self.runs, n, "a hit must not compile")

    def test_compile_error_is_cached_and_killed(self):
        fresh = M.build_and_run("#error boom\n", CHIP, self.work)
        self.assertEqual(fresh[0], "compile")
        n = self.runs
        self.assertEqual(M.build_and_run("#error boom\n", CHIP, self.work), fresh)
        self.assertEqual(self.runs, n)

    def test_survivor_still_survives_from_the_cache(self):
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "pass")
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "pass")
        # the consumer treats a non-equivalent "pass" as a failure: the verdict is not rewritten on the way out
        st, _msg = M.build_and_run(self.hdr(0), CHIP, self.work)
        self.assertNotEqual(st, "fail")

    def test_one_header_byte_invalidates(self):
        M.build_and_run(self.hdr(0), CHIP, self.work)
        n = self.runs
        M.build_and_run(self.hdr(0) + " ", CHIP, self.work)
        self.assertGreater(self.runs, n)
        n = self.runs
        M.build_and_run(self.hdr(0), CHIP + " ", self.work)        # the chip header too
        self.assertGreater(self.runs, n)

    def test_test_source_invalidates(self):
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "pass")
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { return 9; }\n')
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "fail")      # a stale hit would say pass

    def test_gcc_version_and_flags_are_in_the_key(self):
        k = C.make_key(CHIP, OK, [("a", "b")], ["-O1"])
        self.assertNotEqual(k, C.make_key(CHIP, OK, [("a", "b")], ["-O0"]))
        with mock.patch.object(C, "_gcc_version", "gcc other 1.0"):
            self.assertNotEqual(k, C.make_key(CHIP, OK, [("a", "b")], ["-O1"]))
        self.assertNotEqual(C.make_key("ab", "c", [], []), C.make_key("a", "bc", [], []))      # no concatenation ambiguity

    def test_timeout_is_never_cached(self):
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { for (;;) { } }\n')      # spins: RLIMIT_CPU 1 s ends it
        for _ in range(2):
            st, msg = M.build_and_run(self.hdr(0), CHIP, self.work, limit=1)
            self.assertEqual(st, "timeout", msg)
        self.assertEqual(self.cached_files(), [])

    def cached_files(self):
        return [f for _r, _d, fs in os.walk(self.cache) for f in fs] if os.path.isdir(self.cache) else []

    def test_storage_rule(self):
        """D2: a fail is stored only with RESULT: FAIL in the stderr tail or a crash signal; SIGXCPU, ENOMEM, other exit codes are recomputed."""
        self.assertFalse(C.deterministic("timeout"))
        self.assertFalse(C.deterministic("infra", -24))
        self.assertTrue(C.deterministic("fail", 1, "x\nRESULT: FAIL wlan_wr_host_test (3 checks)\n"))
        for rc in (-11, -6, -8, -7):
            self.assertTrue(C.deterministic("fail", rc, ""), rc)
        for rc in (-24, -9, -15, 1, 3, 137, 12):
            self.assertFalse(C.deterministic("fail", rc, "some stderr"), rc)
        self.assertFalse(C.deterministic("compile", -9, ""))
        self.assertFalse(C.deterministic("compile", 1, "cc1: out of memory allocating 1 bytes"))
        self.assertFalse(C.deterministic("compile", 1, "gcc: internal compiler error: Killed (program cc1)"))
        self.assertTrue(C.deterministic("compile", 1, "<D>/sweet_wlan_wr.h:12:3: error: expected ';'"))
        self.assertTrue(C.deterministic("pass", 0))

    def test_compile_needs_a_source_location_error(self):
        """review A2: a compile kill is stored only with a file:line: error: diagnostic; link/driver failures have rc 1 and no location."""
        for t in ("error: expected ';'", "collect2: fatal error: ld terminated with signal 9 [Killed]", "gcc: fatal error: cannot execute 'cc1': No such file",
                  "/usr/bin/ld: cannot open output file t0: Too many open files", ""):
            self.assertFalse(C.deterministic("compile", 1, t), t)
        self.assertTrue(C.deterministic("compile", 1, "In file included from t.c:3:\n/tmp/x/sweet_wlan_wr.h:5:10: fatal error: y.h: No such file or directory"))
        # a location error AND an infra sign is still infra: fails if the infra_text() clause is removed from deterministic()
        self.assertFalse(C.deterministic("compile", 1, "t.c:1:2: error: foo\ngcc: fatal error: Killed signal terminated program cc1"))
        self.assertFalse(C.deterministic("compile", 1, "t.c:1:2: error: foo\ncc1: out of memory allocating 65536 bytes"))

    def test_fail_without_result_line_is_not_stored(self):
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { return 3; }\n')
        for _ in range(2):
            self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "fail")
        self.assertEqual(self.cached_files(), [])
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\n#include <stdlib.h>\nint main(void) { abort(); }\n')
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "fail")      # SIGABRT = crash: stored
        self.assertEqual(len(self.cached_files()), 1)

    def test_gcc_infra_is_never_cached_and_never_a_kill(self):
        real = M.cpu_timeout.run_cpu_limited
        for how in ("expired", "xcpu", "sigkill"):
            def fake(cmd, *a, **k):
                if cmd and cmd[0] == "gcc" and "--version" not in cmd:
                    if how == "expired":
                        raise M.cpu_timeout.CpuLimitExpired(cmd, 1, "wall-backstop")
                    if how == "xcpu":
                        raise M.cpu_timeout.CpuLimitExpired(cmd, 1, "rlimit-cpu")
                    return -signal.SIGKILL
                return real(cmd, *a, **k)
            with mock.patch.object(M.cpu_timeout, "run_cpu_limited", fake):
                st, msg = M.build_and_run(self.hdr(0), CHIP, self.work)
            self.assertEqual(st, "infra", how)
            self.assertNotIn(st, ("fail", "pass", "compile"))
        self.assertEqual(self.cached_files(), [])

    def test_compile_message_has_no_temp_dir(self):
        st, msg = M.build_and_run("#error boom\n", CHIP, self.work)
        self.assertEqual(st, "compile")
        self.assertNotIn(self.work, msg)

    def test_spot_check_detects_a_wrong_cached_verdict(self):
        """D2a: inject a wrong cached verdict (a survivor stored as a kill); the spot-check must report it, and quarantine moves the cache aside."""
        h = {}
        for n, rc in (("good-pass", 0), ("good-fail", 3), ("wrong", 0)):
            h[n] = self.hdr(rc) + ("/* %s */\n" % n)
            M.build_and_run(h[n], CHIP, self.work)
        # tamper: the "wrong" entry claims fail
        tests = [(os.path.basename(t), M._read(t)) for t in (M.TEST_NV, M.TEST)]
        C.put(C.make_key(CHIP, h["wrong"], tests, M.CC_FLAGS), "fail", "RESULT: FAIL forged")
        hits = {}
        for n, hd in h.items():
            hits[n] = (C.get(C.make_key(CHIP, hd, tests, M.CC_FLAGS)), n == "good-pass", hd)
        checked, bad = C.spot_check(hits, lambda hd: M.build_and_run(hd, CHIP, self.work, use_cache=False))
        self.assertEqual((checked, bad), (3, ["wrong"]))
        q = C.quarantine()
        self.assertRegex(q, r"\.bad-\d+$")
        self.assertFalse(os.path.exists(self.cache))
        self.assertTrue(os.path.isdir(q))

    def test_spot_check_passes_on_true_entries(self):
        h = {"a": self.hdr(0), "b": self.hdr(3)}
        for hd in h.values():
            M.build_and_run(hd, CHIP, self.work)
        tests = [(os.path.basename(t), M._read(t)) for t in (M.TEST_NV, M.TEST)]
        hits = {n: (C.get(C.make_key(CHIP, hd, tests, M.CC_FLAGS)), False, hd) for n, hd in h.items()}
        self.assertEqual(C.spot_check(hits, lambda hd: M.build_and_run(hd, CHIP, self.work, use_cache=False)), (2, []))

    def test_spot_sample_is_deterministic_and_complete(self):
        hits = {"f%d" % i: ("fail", False) for i in range(30)}
        hits.update({"p%d" % i: ("pass", False) for i in range(30)})
        hits.update({"e%d" % i: ("pass", True) for i in range(4)})
        a = C.spot_sample(hits)
        self.assertEqual(a, C.spot_sample(dict(reversed(list(hits.items())))))
        self.assertEqual(len(a), 16)                                   # 12 + all 4 equivalent survivors
        self.assertTrue({"e0", "e1", "e2", "e3"} <= set(a))
        self.assertTrue(any(n.startswith("f") for n in a) and any(n.startswith("p") for n in a))

    def test_spot_sample_rotates_with_the_salt(self):
        """review A3: the sample changes with the salt (so every entry is re-verified over time); equivalent survivors are always in."""
        hits = {"f%d" % i: ("fail", False) for i in range(60)}
        hits.update({"e%d" % i: ("pass", True) for i in range(2)})
        picks = [C.spot_sample(hits, salt=str(h)) for h in range(8)]
        self.assertGreater(len({tuple(p) for p in picks}), 1)
        self.assertEqual(picks[0], C.spot_sample(hits, salt="0"))
        for p in picks:
            self.assertTrue({"e0", "e1"} <= set(p))
        with mock.patch.dict(os.environ, {"GATE_MUT_SPOT_SALT": "abc"}):
            self.assertEqual(C.spot_salt(), "abc")
        with mock.patch.dict(os.environ, {"GATE_MUT_SPOT_SALT": ""}):
            self.assertRegex(C.spot_salt(), r"^\d+$")

    def test_sigkilled_test_is_infra_not_a_timeout(self):
        """review A5: SIGKILL (OOM killer) is not the CPU limit (SIGXCPU): infra, never a timeout kill, never cached."""
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\n#include <signal.h>\nint main(void) { raise(SIGKILL); return 0; }\n')
        st, msg = M.build_and_run(self.hdr(0), CHIP, self.work)
        self.assertEqual(st, "infra", msg)
        self.assertEqual(self.cached_files(), [])

    def test_temp_dir_rewritten_before_the_tail_cut(self):
        """review A6: a tail that starts inside the temp dir name must not keep a fragment of it."""
        d = tempfile.mkdtemp(dir=self.work)
        code = "import sys; sys.stderr.write(%r + 'x' * 190)" % d
        rc, err, _out = M.run_bounded([sys.executable, "-c", code], d, 5, wall=60)
        self.assertEqual(rc, 0)
        self.assertEqual(err, ("<D>" + "x" * 190)[-200:])

    def test_sigstopped_child_is_not_a_timeout(self):
        """L2: a child stopped for longer than its CPU limit uses no CPU, so it must still pass (a wall limit would have killed it)."""
        code = ("import os,signal,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));os.kill(os.getpid(),signal.SIGSTOP);"
                "t=time.process_time()\nwhile time.process_time()-t<0.2: pass")
        pidf = os.path.join(self.work, "pid")
        res = []
        th = threading.Thread(target=lambda: res.append(M.run_bounded([sys.executable, "-c", code, pidf], self.work, 1, wall=60)))
        th.start()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20 and not (os.path.exists(pidf) and open(pidf).read()):
            time.sleep(0.05)
        pid = int(open(pidf).read())
        while time.monotonic() - t0 < 20 and "T" not in open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[0]:
            time.sleep(0.05)
        time.sleep(2.5)                                                 # stopped for 2.5 s wall > the 1 CPU-s limit
        os.kill(pid, signal.SIGCONT)
        th.join(30)
        self.assertEqual(res[0][0], 0, res)

    def test_spinning_child_gets_sigxcpu(self):
        with self.assertRaises(subprocess.TimeoutExpired) as cm:      # run_cpu_limited raises (kind cpu or rlimit-cpu) instead of returning -SIGXCPU
            M.run_bounded([sys.executable, "-c", "while True: pass"], self.work, 1, wall=60)
        self.assertIn(cm.exception.kind, ("cpu", "rlimit-cpu"))

    def test_disabled_and_unwritable_cache(self):
        with mock.patch.dict(os.environ, {"GATE_MUT_CACHE": "0"}):
            M.build_and_run(self.hdr(0), CHIP, self.work)
            n = self.runs
            M.build_and_run(self.hdr(0), CHIP, self.work)
            self.assertGreater(self.runs, n)
        self.assertFalse(os.path.exists(self.cache))
        with mock.patch.dict(os.environ, {"GATE_MUT_CACHE": "/proc/nope"}):
            self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "pass")

    def test_corrupt_entry_is_a_miss(self):
        M.build_and_run(self.hdr(0), CHIP, self.work)
        for r, _d, fs in os.walk(self.cache):
            for f in fs:
                self.write(os.path.join(r, f), "{not json")
        n = self.runs
        self.assertEqual(M.build_and_run(self.hdr(0), CHIP, self.work)[0], "pass")
        self.assertGreater(self.runs, n)

    def test_unmutated_base_is_not_cached(self):
        M.build_and_run(self.hdr(0), CHIP, self.work)
        n = self.runs
        M.build_and_run(self.hdr(0), CHIP, self.work, use_cache=False)
        self.assertGreater(self.runs, n)


class RunAll(Env):
    """review A9: run_all wiring (cache line, cold == warm verdicts, spot-check MISMATCH, infra re-run alone, quarantine failure still reports)."""
    def setUp(self):
        super().setUp()
        muts = [("k1", [("RC 0", "RC 1")], False), ("k1dup", [("RC 0", "RC 1")], False), ("surv", [("A 1", "A 5")], False),
                ("eq", [("B 2", "B 3")], True), ("comp", [("#define A 1", "#error boom\\n#define A 1")], False), ("nm", [("zzz", "y")], False)]
        for x in (mock.patch.object(M, "final_header", lambda: "#define RC 0\n#define A 1\n#define B 2\n"), mock.patch.object(M, "new_file", lambda p, f: CHIP),
                  mock.patch.object(M, "chip_header", lambda: CHIP),     # G2 0174: chip_header() applies the real 0124+0174 chain (not new_file); keep the fake chip
                  mock.patch.object(M, "ROOT", self.td.name), mock.patch.object(M, "MUTANTS", muts),
                  mock.patch.dict(os.environ, {"GATE_MUT_JOBS": "2", "GATE_MUT_SPOT_SALT": "t"})):
            x.start()
            self.addCleanup(x.stop)

    def run_all(self, label):
        dump = os.path.join(self.td.name, label + ".json")
        with mock.patch.dict(os.environ, {"GATE_MUT_VERDICTS": dump}):
            _b, out = M.run_all()
        return M.run_all.line, open(dump).read(), out

    def test_cold_then_warm(self):
        cold, dc, out = self.run_all("cold")
        self.assertRegex(cold, r"^MUTCACHE cache=ok hits=0 fresh=4 stored=4 store_failed=0 dedup=1 cpu_timeouts=0 infra=0 spot=0/0 salt=t ok$")
        self.assertEqual({n: v[0] for n, v in out.items()}, {"k1": "fail", "k1dup": "fail", "surv": "pass", "eq": "pass", "comp": "compile", "nm": "nomatch"})
        warm, dw, _o = self.run_all("warm")
        self.assertRegex(warm, r"^MUTCACHE cache=ok hits=4 fresh=0 stored=0 store_failed=0 dedup=1 cpu_timeouts=0 infra=0 spot=4/4 salt=t ok$")
        self.assertEqual(dc, dw)

    def test_cache_off_and_unwritable_are_visible(self):
        with mock.patch.dict(os.environ, {"GATE_MUT_CACHE": "0"}):
            self.assertIn("MUTCACHE cache=off ", self.run_all("off")[0])
        with mock.patch.dict(os.environ, {"GATE_MUT_CACHE": "/proc/nope"}):
            self.assertRegex(self.run_all("ro")[0], r"^MUTCACHE cache=ro .*stored=0 store_failed=4 ")

    def test_all_hits_on_a_read_only_cache_say_ro(self):
        self.run_all("cold")
        os.chmod(self.cache, 0o555)
        self.addCleanup(os.chmod, self.cache, 0o755)
        if os.access(self.cache, os.W_OK):
            self.skipTest("running as root: chmod does not make the dir read-only")
        line = self.run_all("warm")[0]
        self.assertRegex(line, r"^MUTCACHE cache=ro hits=4 fresh=0 stored=0 store_failed=0 ")

    def test_poisoned_entry_is_a_mismatch_even_if_quarantine_fails(self):
        self.run_all("cold")
        tests = [(os.path.basename(t), M._read(t)) for t in (M.TEST_NV, M.TEST)]
        C.put(C.make_key(CHIP, "#define RC 0\n#define A 5\n#define B 2\n", tests, M.CC_FLAGS), "fail", "RESULT: FAIL forged")     # "surv" now claims a kill
        err = []
        with mock.patch.object(C, "quarantine", side_effect=OSError("EBUSY")), mock.patch.object(M.sys, "stderr", mock.Mock(write=err.append)):
            line = self.run_all("bad")[0]
        self.assertTrue(line.endswith(" MISMATCH"), line)
        self.assertEqual(M.run_all.mismatch, ["surv"])
        text = "".join(err)
        self.assertIn("MUTCACHE MISMATCH surv", text)
        self.assertIn("quarantine failed: EBUSY", text)

    def test_pool_infra_is_rerun_alone(self):
        real, seen = M.build_and_run, []

        def flaky(hdr, chip, work, limit=M.RUN_LIMIT, use_cache=True):
            if "A 5" in hdr and limit == M.RUN_LIMIT and use_cache:
                seen.append("pool")
                return "infra", "fake gcc kill"
            seen.append(limit)
            return real(hdr, chip, work, limit, use_cache)
        with mock.patch.object(M, "build_and_run", flaky):
            line, _d, out = self.run_all("inf")
        self.assertIn(" infra=1 ", line)
        self.assertEqual(out["surv"][0], "pass", out["surv"])       # re-run alone with ALONE_LIMIT gave the real verdict
        self.assertIn(M.ALONE_LIMIT, seen)

    def test_rerun_without_verdict_is_infra_not_a_kill(self):
        """review mutants-speed-r1 #2: a re-run that ends at the wall backstop (or stopped) is infra and fails the gate, never a kill."""
        real = M.build_and_run

        def backstop(hdr, chip, work, limit=M.RUN_LIMIT, use_cache=True):
            if "A 5" in hdr:
                return "timeout", "fake wall-backstop"
            return real(hdr, chip, work, limit, use_cache)
        with mock.patch.object(M, "build_and_run", backstop):
            _line, _d, out = self.run_all("bs")
        self.assertEqual(out["surv"][0], "infra", out["surv"])
        self.assertIn("fake wall-backstop", out["surv"][1])
        self.assertEqual(M.run_all.timeout_kills, 0)


    def spin_test(self):
        """t1: RC 7 spins (a hang), RC 9 fails without a RESULT line (not storable), other RC as before."""
        self.write(self.t1, '#include <stdio.h>\n#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { if (RC == 7) for (;;) { } if (RC && RC != 9) fputs("RESULT: FAIL stand-in\\n", stderr); return RC; }\n')
        for x in (mock.patch.object(M, "RUN_LIMIT", 1), mock.patch.object(M, "ALONE_LIMIT", 2),
                  mock.patch.object(M, "MUTANTS", M.MUTANTS + [("hang", [("RC 0", "RC 7")], False), ("quiet", [("RC 0", "RC 9")], False)])):
            x.start()
            self.addCleanup(x.stop)

    def test_rerun_hang_is_a_stored_kill(self):
        """AP-114: a mutant over its CPU limit in the pool AND in the re-run is a kill with a fixed text, stored; the warm run hits it (no gcc), the spot-check text matches."""
        self.spin_test()
        line, _d, out = self.run_all("cold")
        self.assertEqual(out["hang"][:2], ("fail", M.HANG_MSG))
        self.assertIn(" cpu_timeouts=1 ", line)
        self.assertEqual(M.run_all.timeout_kills, 1)
        n = self.runs
        line, _d, out2 = self.run_all("warm")
        self.assertEqual(out2["hang"], out["hang"])
        self.assertIn(" cpu_timeouts=0 ", line)
        self.assertNotIn("MISMATCH", line)
        tests = [(os.path.basename(t), M._read(t)) for t in (M.TEST_NV, M.TEST)]
        h = M.final_header().replace("RC 0", "RC 7")
        self.assertEqual(C.get(C.make_key(CHIP, h, tests, M.CC_FLAGS)), ("fail", M.HANG_MSG))
        self.assertLess(self.runs - n, 12)      # the warm run built only the base and spot-check samples, not a re-run per hang

    def test_pool_only_timeout_is_not_stored(self):
        """A timeout in the pool alone (limit < ALONE_LIMIT) is never stored, and a wall-backstop re-run is not a stored kill either."""
        self.write(self.t1, '#include "sweet_wlan_chip.h"\n#include "sweet_wlan_wr.h"\nint main(void) { for (;;) { } }\n')
        st, _m = M.build_and_run(self.hdr(0), CHIP, self.work, limit=1)
        self.assertEqual(st, "timeout")
        self.assertEqual(self.cached_files(), [])
        def wall(cmd, *a, **k):
            if cmd and cmd[0] != "gcc":
                raise M.cpu_timeout.CpuLimitExpired(cmd, 1, "wall-backstop")
            return real(cmd, *a, **k)
        real = M.cpu_timeout.run_cpu_limited
        with mock.patch.object(M.cpu_timeout, "run_cpu_limited", wall):
            st, _m = M.build_and_run(self.hdr(0), CHIP, self.work, limit=M.ALONE_LIMIT)
        self.assertEqual(st, "timeout")
        self.assertEqual(self.cached_files(), [])

    def test_unstored_verdicts_are_named(self):
        """rule 8 (watcher item e): the verdicts the cache does not hold are named on stderr and in run_all.unstored."""
        self.spin_test()
        err = []
        with mock.patch.object(M.sys, "stderr", mock.Mock(write=err.append)):
            self.run_all("cold")
        self.assertEqual(M.run_all.unstored, ["quiet (fail)"])
        self.assertIn("MUTCACHE unstored=1 (recomputed by every run): quiet (fail)", "".join(err))

    # ---- K1 wiring (review gate-cold-k14 r1 F2): every build of the header set runs inside the pool's permits ----
    def _patch_module(self, mod):
        """The same stand-ins RunAll.setUp puts on test_wlan_wr_mutants, for another copy of that module (the mutants below)."""
        stack = contextlib.ExitStack()
        muts = [("k1", [("RC 0", "RC 1")], False), ("k1dup", [("RC 0", "RC 1")], False), ("surv", [("A 1", "A 5")], False),
                ("eq", [("B 2", "B 3")], True), ("comp", [("#define A 1", "#error boom\\n#define A 1")], False), ("nm", [("zzz", "y")], False)]
        for name, val in (("TEST_NV", self.t1), ("TEST", self.t2), ("final_header", lambda: "#define RC 0\n#define A 1\n#define B 2\n"),
                          ("new_file", lambda p, f: CHIP), ("ROOT", self.td.name), ("MUTANTS", muts)):
            stack.enter_context(mock.patch.object(mod, name, val))
        self.addCleanup(stack.close)

    def _peak(self, mod, scenario, signal):
        """Highest number of builds alive at once in mod.run_all(). scenario: cold (empty cache), infra (every pool build ends infra, so the alone re-runs happen), warm (spot-check re-runs hits fresh).
        GATE_MUT_JOBS=2 derived on a 16-CPU host: a gate of 2 permits in a thread pool of 10; with the lanes-done file present the gate grows to 10 and the 4 distinct builds run together."""
        sig = os.path.join(self.td.name, "lanes-ab-done")
        if signal:
            open(sig, "w").close()
        live, peak, lock = [0], [0], threading.Lock()
        real, first = mod.build_and_run, [True]
        if scenario == "warm":
            self._run_mod(mod, "prime", sig, {})                       # fills the cache; the next run hits it and spot-checks every hit

        def counted(hdr, chip, work, limit=M.RUN_LIMIT, use_cache=True):
            if first[0]:
                first[0] = False                                         # the unmutated probe run: alone, before the pool
                return real(hdr, chip, work, limit, use_cache)
            with lock:
                live[0] += 1; peak[0] = max(peak[0], live[0])
            try:
                time.sleep(0.3)
                if scenario == "infra" and use_cache and limit == M.RUN_LIMIT:
                    return "infra", "fake gcc kill"
                return real(hdr, chip, work, limit, use_cache)
            finally:
                with lock:
                    live[0] -= 1
        self._run_mod(mod, "peak", sig, {"build_and_run": counted})
        return peak[0]

    def _run_mod(self, mod, label, sig, patches):
        dump = os.path.join(self.td.name, label + ".json")
        P._GROWN.clear()
        env = {"GATE_MUT_VERDICTS": dump, "GATE_MUT_JOBS": "2", "GATE_MUT_JOBS_SRC": "derived", "GATE_LANES_DONE": sig}
        try:
            with mock.patch.dict(os.environ, env), mock.patch.object(P, "host_cpus", return_value=16), contextlib.ExitStack() as st:
                for k, v in patches.items():
                    st.enter_context(mock.patch.object(mod, k, v))
                return mod.run_all()
        finally:
            P._GROWN.clear()

    def _peaks_of(self, mod):
        return {"cold": self._peak(mod, "cold", False), "infra": self._peak(mod, "infra", False), "warm": self._peak(mod, "warm", False)}

    def test_header_set_builds_run_inside_the_job_gate(self):
        pk = self._peaks_of(M)
        self.assertEqual(pk, {"cold": 2, "infra": 2, "warm": 2}, "GATE_MUT_JOBS=2 and no lanes-done file: at most 2 builds at once at each of the three call sites")

    def test_header_set_grows_when_the_signal_file_appears(self):
        self.assertEqual(self._peak(M, "cold", True), 4, "with the signal file the 4 distinct builds run together")

    def test_gate_wiring_mutants_are_killed(self):
        src = open(M.__file__).read()
        survived = []
        for name, old, new, scenario in GATE_WIRING_MUTANTS:
            self.assertEqual(src.count(old), 1, "needle must occur once: %r" % old)
            mod = types.ModuleType("test_wlan_wr_mutants_mutant"); mod.__file__ = M.__file__
            exec(compile(src.replace(old, new), M.__file__, "exec"), mod.__dict__)
            self._patch_module(mod)
            if self._peak(mod, scenario, False) <= 2:
                survived.append(name)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)


# the three gate.run call sites of test_wlan_wr_mutants.run_all, each with the scenario that reaches it
GATE_WIRING_MUTANTS = [
    ("first pass builds outside the permits", "futs[m] = ex.submit(gate.run, build_and_run, m, chip, work, run_all.pool_limit)", "futs[m] = ex.submit(build_and_run, m, chip, work, run_all.pool_limit)", "cold"),
    ("alone re-runs outside the permits", "again[subbed[name]] = ex2.submit(gate.run, build_and_run, subbed[name], chip, work, ALONE_LIMIT)",
     "again[subbed[name]] = ex2.submit(build_and_run, subbed[name], chip, work, ALONE_LIMIT)", "infra"),
    ("spot-check fresh builds outside the permits", "v = gate.run(lambda: build_and_run(m, chip, work, run_all.pool_limit, use_cache=False))", "v = build_and_run(m, chip, work, run_all.pool_limit, use_cache=False)", "warm"),
]


if __name__ == "__main__":
    unittest.main()
