"""Hermetic tests of tools/wlan_mutant_pool.py (PLAN-GATE-2X D1/G4, slice S1): stub run_one, no gcc, a private verdict cache per check.
BEHAVIOUR is a list of checks run against any pool module; the real module must pass all of them, and each honest mutant of wlan_mutant_pool.py
(one changed expression: a dropped key part, spot-check off, a swallowed exception, scrambled order, a silent skip, ...) must make at least one fail.
Run: python3 -I tools/test_wlan_mutant_pool.py"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import wlan_mutant_cache as cache
import wlan_mutant_pool as real

cache._gcc_version = "stub-gcc 1.0\n"      # hermetic: the key needs the compiler version, not a compiler
POOL_SRC = os.path.join(TOOLS, "wlan_mutant_pool.py")
N = 8
MUTS = [("m%d" % i, [("old%d" % i, "new%d" % i)]) for i in range(N)]
KEY = ["base text", "harness text", "-O1 -Wall"]


class Fail(AssertionError):
    pass


def ensure(cond, msg):
    if not cond:
        raise Fail(msg)


def verdict_of(label):
    i = int(label[1:])
    return [("pass", "ok"), ("fail", "RESULT: FAIL x"), ("compile", "a.c:1:2: error: boom")][i % 3]


def make_run_one(boom=(), delay=0.0, calls=None, status=None):
    def run_one(label, subs):
        if calls is not None:
            calls.append(label)
        if delay:
            time.sleep(delay)
        if label in boom:
            raise RuntimeError("stub worker crash")
        return status or verdict_of(label)
    return run_one


@contextlib.contextmanager
def sandbox(**env):
    """A private cache dir, a fixed spot salt, captured stderr; env overrides (None = unset)."""
    with tempfile.TemporaryDirectory() as t:
        cdir = os.path.join(t, "cache")
        # K1: the lanes-done signal points at a file that does not exist and GATE_MUT_JOBS_SRC is unset, so a stale file or a gate-exported variable never grows a check's pool
        new = {"GATE_MUT_CACHE": cdir, "GATE_MUT_SPOT_SALT": "salt", "GATE_MUT_ONLY": None, "GATE_LANES_DONE": os.path.join(t, "no-such-lanes-done"), "GATE_MUT_JOBS_SRC": None}
        new.update(env)
        old = {k: os.environ.get(k) for k in new}
        for k, v in new.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                yield cdir, err
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def run(pool, name="glue", muts=MUTS, key=KEY, run_one=None, jobs=3):
    return pool.run_set(name, muts, run_one or make_run_one(), key, jobs=jobs)


# ---- the behaviour every pool module must have ------------------------------------------------------------------------------------------------------------------------

def c_order(pool):
    with sandbox():
        res = run(pool, run_one=make_run_one(delay=0.0), jobs=4)
        want = [verdict_of(l) for l, _s in MUTS]
        ensure(res == want, "serial order wrong: %r" % res)
    with sandbox():
        def slow_first(label, subs):          # the first mutants finish last
            time.sleep((N - int(label[1:])) * 0.02)
            return verdict_of(label)
        res = pool.run_set("glue", MUTS, slow_first, KEY, jobs=N)
        ensure(res == [verdict_of(l) for l, _s in MUTS], "results are not in input order when the workers finish out of order: %r" % res)


def c_cache_hit_on_second_run(pool):
    with sandbox() as (cdir, err):
        calls = []
        r1 = run(pool, run_one=make_run_one(calls=calls))
        ensure(len(calls) == N and pool.LAST["glue"]["fresh"] == N and pool.LAST["glue"]["hits"] == 0 and pool.LAST["glue"]["stored"] == N, "cold run counters: %r" % pool.LAST["glue"])
        del calls[:]
        r2 = run(pool, run_one=make_run_one(calls=calls))
        s = pool.LAST["glue"]
        ensure(r2 == r1, "a cached verdict differs from the fresh one")
        ensure((s["hits"], s["fresh"], s["stored"]) == (N, 0, 0), "warm run counters: %r" % s)
        ensure(len(calls) == N and s["spot"] == (N, N), "the warm run must only re-run the spot-check sample (%d of %d hits): calls=%d spot=%r" % (N, N, len(calls), s["spot"]))
        lines = [l for l in err.getvalue().splitlines() if l.startswith("MUTCACHE set=")]
        ensure(len(lines) == 2 and re.match(r"MUTCACHE set=glue hits=%d fresh=0 stored=0 spot=%d/%d " % (N, N, N), lines[1]), "MUTCACHE line: %r" % lines)


def c_miss_on_key_change(pool):
    with sandbox():
        run(pool)
        for i, tweak in enumerate((["base text!", "harness text", "-O1 -Wall"], ["base text", "harness texu", "-O1 -Wall"], ["base text", "harness text", "-O2 -Wall"], ["base text", "harness text"])):
            run(pool, key=tweak)
            s = pool.LAST["glue"]
            ensure((s["hits"], s["fresh"]) == (0, N), "key part %d changed but the run had hits: %r" % (i, s))
            run(pool)      # the original entries are still there
            ensure(pool.LAST["glue"]["hits"] == N, "the original key no longer hits")
        muts2 = list(MUTS)
        muts2[3] = ("m3", [("old3", "NEW3")])
        run(pool, muts=muts2)
        ensure((pool.LAST["glue"]["hits"], pool.LAST["glue"]["fresh"]) == (N - 1, 1), "one changed substitution must miss exactly once: %r" % pool.LAST["glue"])
        run(pool, name="header")
        ensure(pool.LAST["header"]["hits"] == 0, "another set name must not hit the verdicts of set glue")


def c_runner_text_is_keyed(pool):
    """The runner FILE text is part of the key: two run_one with the same behaviour but different source files do not share verdicts."""
    with sandbox(), tempfile.TemporaryDirectory() as d:
        fns = []
        for n, comment in enumerate(("# a", "# b")):
            p = os.path.join(d, "runner%d.py" % n)
            pathlib.Path(p).write_text("%s\ndef run_one(label, subs):\n    return ('pass', 'ok')\n" % comment)
            spec = importlib.util.spec_from_file_location("runner%d" % n, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            fns.append(m.run_one)
        pool.run_set("glue", MUTS, fns[0], KEY, jobs=2)
        pool.run_set("glue", MUTS, fns[1], KEY, jobs=2)
        ensure(pool.LAST["glue"]["hits"] == 0, "a changed runner file still hit the cache")
        pool.run_set("glue", MUTS, fns[1], KEY, jobs=2)
        ensure(pool.LAST["glue"]["hits"] == N, "an unchanged runner file missed")


def c_failfast_equivalence(pool):
    full = lambda l, s: ("fail", "BAD 1\nBAD 2\nFAIL accepted 3 bad 2") if int(l[1:]) % 2 else ("pass", "PASS accepted 3 bad 0")
    ff_ok = lambda l, s: ("fail", "BAD 1") if int(l[1:]) % 2 else ("pass", "PASS accepted 3 bad 0")
    ff_bad = lambda l, s: ("pass", "PASS") if l == "m3" else ff_ok(l, s)
    ensure(pool.failfast_mismatches(MUTS, full, ff_ok, jobs=3) == [], "equal kill sets reported as different")
    ensure(pool.failfast_mismatches(MUTS, full, ff_bad, jobs=3) == [("m3", "fail", "pass")], "a mutant that survives only in fail-fast mode was not reported")


def c_spot_mismatch_is_caught_and_quarantined(pool):
    with sandbox() as (cdir, err):
        run(pool)
        n = 0
        for root, _d, files in os.walk(cdir):
            for f in files:
                p = os.path.join(root, f)
                v = json.load(open(p))
                v["status"] = "fail" if v["status"] == "pass" else "pass"        # a poisoned entry
                json.dump(v, open(p, "w"))
                n += 1
        ensure(n == N, "expected %d cache entries, found %d" % (N, n))
        try:
            run(pool)
        except pool.MutantCacheMismatch:   # lint-silent: expected the test expects exactly this exception, the else branch raises Fail
            pass
        else:
            raise Fail("a poisoned cache entry was not detected by the spot-check")
        ensure(not os.path.exists(cdir), "the poisoned cache dir was not quarantined")
        ensure(any(x.startswith("cache.bad-") for x in os.listdir(os.path.dirname(cdir))), "no cache.bad-* directory")
        out = err.getvalue()
        ensure("MUTCACHE MISMATCH set=glue" in out and "MISMATCH" in pool.LAST["glue"]["line"], "the mismatch is not named loudly: %s" % out[-300:])


def c_only_skip_line(pool):
    with sandbox(GATE_MUT_ONLY="glue") as (cdir, err):
        calls = []
        ensure(pool.run_set("header", MUTS, make_run_one(calls=calls), KEY, jobs=2) is None, "an unselected set must return None")
        ensure(calls == [], "an unselected set ran %d mutants" % len(calls))
        ensure("MUTANT SET skipped: header (GATE_MUT_ONLY=glue)\n" in err.getvalue(), "skip line missing: %r" % err.getvalue())
        ensure(pool.want("glue") is True and pool.run_set("glue", MUTS, make_run_one(), KEY, jobs=2) is not None, "the selected set did not run")
    with sandbox(GATE_MUT_ONLY="glue, header"):
        ensure(pool.want("header") and pool.want("glue"), "comma list with a space")
    with sandbox(GATE_MUT_ONLY="header"):
        ensure(pool.want("header") and not pool.want("glue"), "one name")
    with sandbox():
        ensure(all(pool.want(n) for n in pool.KNOWN_SETS), "unset GATE_MUT_ONLY must select every known set")


def c_only_unknown_name_is_an_error(pool):
    with sandbox(GATE_MUT_ONLY="glue,bogus"):
        for call in (lambda: pool.only_sets(), lambda: pool.want("glue"), lambda: pool.run_set("glue", MUTS, make_run_one(), KEY, jobs=2)):
            try:
                call()
            except ValueError as e:
                ensure("bogus" in str(e), "the error does not name the unknown set: %s" % e)
            else:
                raise Fail("an unknown GATE_MUT_ONLY name was accepted")
    with sandbox():
        for bad in ("glue,", ",", "GLUE", "g3", "audit,d4x", "glue,g2lx"):          # typo, case, empty item, valid-but-unwired names (F1)
            try:
                pool.only_sets(bad)
            except ValueError as e:
                ensure("glue" in str(e) and "header" in str(e), "the error does not list the accepted sets: %s" % e)
            else:
                raise Fail("malformed or unwired GATE_MUT_ONLY %r accepted" % bad)
        try:
            pool.run_set("zzz", MUTS, make_run_one(), KEY, jobs=2)
        except ValueError:   # lint-silent: expected the test expects exactly this exception, the else branch raises Fail
            pass
        else:
            raise Fail("an unregistered set name ran")
        argv = ["prog", "--only", "header,glue", "-v"]
        pool.apply_only_argv(argv)
        ensure(argv == ["prog", "-v"] and os.environ.get("GATE_MUT_ONLY") == "header,glue", "--only was not consumed into GATE_MUT_ONLY: %r" % argv)
        for bad in ("nope", "g3", "", "  "):
            try:
                pool.apply_only_argv(["prog", "--only", bad])
            except SystemExit as e:
                ensure("glue" in str(e) and "header" in str(e), "--only %r: the exit message does not list the accepted sets: %s" % (bad, e))
            else:
                raise Fail("--only %r was accepted" % bad)


def c_only_blank_is_an_error(pool):
    """Set-but-blank GATE_MUT_ONLY is loud (an empty selection must not skip everything, F1/mutant I); unset runs all sets."""
    for blank in ("", "   ", "\t"):
        with sandbox(GATE_MUT_ONLY=blank):
            for call in (lambda: pool.only_sets(), lambda: pool.want("glue"), lambda: pool.run_set("glue", MUTS, make_run_one(), KEY, jobs=2)):
                try:
                    call()
                except ValueError as e:
                    ensure("blank" in str(e) and "glue" in str(e), "blank value: the error is not specific: %s" % e)
                else:
                    raise Fail("a blank GATE_MUT_ONLY %r was accepted (it would select or skip silently)" % blank)
    with sandbox():
        ensure(pool.only_sets() is None and pool.want("glue"), "an unset GATE_MUT_ONLY must select all sets")


def c_summary_line_and_ran_zero(pool):
    """`MUTANT SET name=<n> ran=N skipped=M` ends every run_set; a set with ran=0 fails (F1)."""
    with sandbox() as (cdir, err):
        run(pool)
        ensure("MUTANT SET name=glue ran=%d skipped=0\n" % N in err.getvalue(), "summary line missing: %r" % err.getvalue()[-200:])
        ensure(err.getvalue().rstrip().splitlines()[-1] == "MUTANT SET name=glue ran=%d skipped=0" % N, "the summary line is not the last line")
        run(pool, run_one=make_run_one(boom=("m2", "m5")), name="header")
        ensure("MUTANT SET name=header ran=%d skipped=2\n" % (N - 2) in err.getvalue(), "skipped count wrong: %r" % err.getvalue()[-200:])
    for label, kw in (("every worker raised", {"boom": tuple(l for l, _s in MUTS)}), ("only non-verdict statuses", {"status": ("nomatch", "pattern not found")})):
        with sandbox() as (cdir, err):
            try:
                run(pool, run_one=make_run_one(**kw))
            except RuntimeError as e:
                ensure("ran=0" in str(e), "the failure does not say ran=0: %s" % e)
            else:
                raise Fail("%s: a set with ran=0 returned normally (OK)" % label)
            ensure("MUTANT SET name=glue ran=0 skipped=%d" % N in err.getvalue(), "ran=0 summary line missing")
    with sandbox():
        try:
            run(pool, muts=[])
        except RuntimeError:   # lint-silent: expected the test expects exactly this exception, the else branch raises Fail
            pass
        else:
            raise Fail("an empty mutant table returned normally")


def c_key_parts(pool):
    """Every key input changes the key (F7 mutants C, F, G + F5): pool file text, length prefix, compiler, libc, cpu_timeout.py text."""
    k = lambda **o: pool.verdict_key(o.get("n", "s"), o.get("p", ["a", "b"]), o.get("s", [["x", "y"]]), o.get("r", "runner"))
    base = k()
    for o in ({"n": "glue"}, {"p": ["a", "c"]}, {"p": ["a"]}, {"p": ["ab", ""]}, {"s": [["x", "z"]]}, {"r": "runner "}):
        ensure(k(**o) != base, "key unchanged for %r" % (o,))
    ensure(k(s=1, r="23") != k(s=12, r="3"), "length prefix missing: (subs 1, runner '23') and (subs 12, runner '3') give one key")
    ensure(k(n="ab", p=[]) != k(n="a", p=["b"]), "set name and key_parts alias")
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "pool_a.py"), os.path.join(d, "pool_b.py")
        pathlib.Path(a).write_text("# a\n")
        pathlib.Path(b).write_text("# b\n")
        saved = pool.__file__, pool.CPU_TIMEOUT_PY
        try:
            pool.__file__ = a
            ka = k()
            pool.__file__ = b
            ensure(k() != ka, "the pool file text is not part of the key (a changed pool must miss)")
            pool.__file__, pool.CPU_TIMEOUT_PY = saved[0], a
            kc = k()
            pool.CPU_TIMEOUT_PY = b
            ensure(k() != kc, "the cpu_timeout.py text is not part of the key")
        finally:
            pool.__file__, pool.CPU_TIMEOUT_PY = saved
    old_gcc = cache._gcc_version
    try:
        cache._gcc_version = "other-gcc 9.9\n"
        ensure(k() != base, "the gcc version is not part of the key")
    finally:
        cache._gcc_version = old_gcc
    old_conf = os.confstr
    try:
        os.confstr = lambda name: "other-libc 9.9" if name == "CS_GNU_LIBC_VERSION" else old_conf(name)
        ensure(k() != base, "the libc version is not part of the key")
    finally:
        os.confstr = old_conf


def c_spot_covers_small_sets_and_salt_rotates(pool):
    """<= 100 hits: the spot-check re-runs ALL of them (F3). Larger sets: a sample that follows GATE_MUT_SPOT_SALT (mutant J: a constant salt)."""
    with sandbox() as (cdir, err):
        run(pool)
        calls = []
        run(pool, run_one=make_run_one(calls=calls))
        ensure(pool.LAST["glue"]["spot"] == (N, N) and sorted(calls) == sorted(l for l, _s in MUTS), "a small set was not re-run in full: %r" % calls)
    mid = [("m%d" % i, [("o%d" % i, "n%d" % i)]) for i in range(40)]         # more than SPOT_K (12) and 3 %, at most SPOT_ALL_MAX: all of them
    with sandbox() as (cdir, err):
        pool.run_set("glue", mid, make_run_one(), KEY, jobs=4)
        calls = []
        pool.run_set("glue", mid, make_run_one(calls=calls), KEY, jobs=4)
        ensure(pool.LAST["glue"]["spot"] == (40, 40) and len(calls) == 40, "a 40-entry set was sampled instead of re-run in full: spot=%r calls=%d" % (pool.LAST["glue"]["spot"], len(calls)))
    half = [("m%d" % i, [("o%d" % i, "n%d" % i)]) for i in range(66)]         # the glue size: half of them (cost budget), not 12
    with sandbox() as (cdir, err):
        pool.run_set("glue", half, make_run_one(), KEY, jobs=4)
        calls = []
        pool.run_set("glue", half, make_run_one(calls=calls), KEY, jobs=4)
        ensure(len(calls) == 33, "a 66-entry set must re-run half (33), got %d" % len(calls))
    big = [("m%d" % i, [("o%d" % i, "n%d" % i)]) for i in range(130)]
    seen = {}
    for salt in ("A", "B", "C"):
        with sandbox(GATE_MUT_SPOT_SALT=salt) as (cdir, err):
            pool.run_set("glue", big, make_run_one(), KEY, jobs=4)
            calls = []
            pool.run_set("glue", big, make_run_one(calls=calls), KEY, jobs=4)
            ensure("salt=%s " % salt in pool.LAST["glue"]["line"], "the MUTCACHE line does not show the salt %s: %s" % (salt, pool.LAST["glue"]["line"]))
            ensure(0 < len(calls) < 130, "a 130-entry set must be sampled, not re-run in full or not at all: %d calls" % len(calls))
            seen[salt] = frozenset(calls)
    ensure(len(set(seen.values())) == 3, "the spot-check sample does not change with the salt (no rotation)")


def c_worker_exception_is_loud(pool):
    with sandbox() as (cdir, err):
        res = run(pool, run_one=make_run_one(boom=("m2",)))
        ensure(res[2][0] == "error" and "stub worker crash" in res[2][1], "a crashed worker is not an error verdict: %r" % (res[2],))
        ensure(res[2][0] not in ("pass", "fail"), "a crashed worker counted as a verdict")
        ensure("MUTANT SET ERROR glue: m2" in err.getvalue(), "no loud line for the crashed worker")
        ensure(pool.LAST["glue"]["errors"] == 1, "errors not counted")
        run(pool)
        ensure((pool.LAST["glue"]["hits"], pool.LAST["glue"]["fresh"]) == (N - 1, 1), "an error verdict was stored: %r" % pool.LAST["glue"])
        try:
            run(pool, name="header", run_one=lambda l, s: "not a tuple")
        except pool.MutantSetEmpty:   # lint-silent: expected every verdict malformed: ran=0 is the expected failure the test asserts, anything else raises Fail
            pass           # every verdict malformed: ran=0 is the expected failure, not a raw TypeError
        except Exception:
            raise Fail("a malformed verdict escaped as an exception instead of an error verdict")


def c_only_deterministic_verdicts_are_stored(pool):
    with sandbox():
        for _ in range(2):
            try:
                run(pool, run_one=make_run_one(status=("infra", "gcc killed")))
            except pool.MutantSetEmpty:        # every verdict is infra: ran=0 (F1), but nothing may be stored   # lint-silent: expected every verdict is infra: ran=0 is the expected failure, the next assert checks nothing was stored
                pass
        ensure(pool.LAST["glue"]["hits"] == 0, "an infra verdict was served from the cache")
        run(pool, run_one=make_run_one(status=("compile", "ld: no location")))
        run(pool, run_one=make_run_one(status=("compile", "ld: no location")))
        ensure(pool.LAST["glue"]["hits"] == 0, "a compile failure without a source location was cached")


def c_pool_size_is_used(pool):
    with sandbox():
        live, peak, lock = [0], [0], threading.Lock()

        def slow(label, subs):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return ("pass", "ok")
        pool.run_set("glue", MUTS, slow, KEY, jobs=4)
        ensure(2 <= peak[0] <= 4, "pool of 4 ran %d at a time" % peak[0])


def c_duplicate_labels(pool):
    with sandbox():
        try:
            pool.run_set("glue", MUTS + [MUTS[0]], make_run_one(), KEY, jobs=2)
        except ValueError:   # lint-silent: expected the test expects exactly this exception, the else branch raises Fail
            pass
        else:
            raise Fail("duplicate labels accepted")


# ---- dynamic pool growth (PLAN-GATE-2X K1) ----------------------------------------------------------------------------------------------------------------------------

def _timeline(sig, create_at=2, delay=0.12):
    """A run_one that records the concurrency at each start with whether the signal file existed then, and creates the file when `create_at` mutants have started."""
    live, rec, lock, started = [0], [], threading.Lock(), [0]

    def run_one(label, subs):
        with lock:
            live[0] += 1
            started[0] += 1
            rec.append((live[0], os.path.exists(sig)))
            if sig and started[0] == create_at:      # appears 0.06 s later, while every other worker is already blocked waiting for a permit: only polling can see it
                threading.Timer(0.06, pathlib.Path(sig).write_text, [""]).start()
        time.sleep(delay)
        with lock:
            live[0] -= 1
        return verdict_of(label)
    return run_one, rec


@contextlib.contextmanager
def fast_poll(pool, cpus=None):
    old = getattr(pool, "POLL_S", None)
    getattr(pool, "_GROWN", {}).clear()
    pool.POLL_S = 0.02
    try:
        if cpus is None:
            yield
        else:
            from unittest import mock
            # nproc = the affinity set (F4): the whole-host os.cpu_count() is made different on purpose, so a pool that counts it is wrong on every cap check
            with mock.patch("os.sched_getaffinity", return_value=set(range(cpus))), mock.patch("os.cpu_count", return_value=64):
                yield
    finally:
        pool.POLL_S = old
        getattr(pool, "_GROWN", {}).clear()


def _lines(err):
    return [l for l in err.getvalue().splitlines() if l.startswith("lane C jobs")]


def c_poll_interval_is_short(pool):
    ensure(0 < pool.POLL_S <= 1.0, "a waiting worker looks for the signal file every %r s (must be <= 1 s)" % pool.POLL_S)


def c_pool_grows_after_lanes_done(pool):
    muts = [("m%d" % i, [("old%d" % i, "new%d" % i)]) for i in range(10)]
    with fast_poll(pool, cpus=16):
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            sig = os.path.join(os.path.dirname(cdir), "lanes-ab-done")
            os.environ["GATE_LANES_DONE"] = sig
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            run_one, rec = _timeline(sig)
            res = pool.run_set("glue", muts, run_one, KEY, jobs=2)
            ensure(res == [verdict_of(l) for l, _s in muts], "verdicts changed by the growth: %r" % res)
            before = [n for n, existed in rec if not existed]
            ensure(before and max(before) <= 2, "before the signal the pool ran %r at a time (jobs=2)" % (before,))
            ensure(max(n for n, _e in rec) <= 14, "above the cap nproc-2=14")
            ensure(max(n for n, _e in rec) >= 4, "the pool did not grow after the signal file appeared (peak %d)" % max(n for n, _e in rec))
            ls = _lines(err)
            ensure(len(ls) == 1 and re.match(r"lane C jobs 2->10 at \+[0-9]+s$", ls[0]), "growth line: %r" % ls)
            # a second set in the same process starts grown and prints no second line
            res2 = pool.run_set("header", muts[:4], make_run_one(), KEY, jobs=2)
            ensure(res2 == [verdict_of(l) for l, _s in muts[:4]], "second set verdicts")
            ensure(len(_lines(err)) == 1, "a second set printed another growth/stayed line: %r" % _lines(err))


def c_extra_permits_are_exact(pool):
    """JobGate.check() adds exactly total-base permits (not more: they would outlive a smaller thread pool of a later caller)."""
    with fast_poll(pool, cpus=16):
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            sig = os.path.join(os.path.dirname(cdir), "lanes-ab-done")
            os.environ["GATE_LANES_DONE"] = sig
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            g = pool.JobGate(2)
            n0 = 0
            while g.sem.acquire(blocking=False):
                n0 += 1
            ensure(n0 == 2, "before the signal %d permits, want 2" % n0)
            for _ in range(n0):
                g.sem.release()
            pathlib.Path(sig).write_text("")
            g.check()
            n1 = 0
            while g.sem.acquire(blocking=False):
                n1 += 1
            ensure(n1 == 10, "after the signal %d permits, want 2+8=10" % n1)
            g.check()
            for _ in range(n1):
                g.sem.release()
            m = 0
            while g.sem.acquire(blocking=False):
                m += 1
            ensure(m == 10, "a second check() added permits: %d" % m)


def c_pool_never_above_the_cpu_cap(pool):
    muts = [("m%d" % i, [("old%d" % i, "new%d" % i)]) for i in range(10)]
    with fast_poll(pool, cpus=6):                      # cap = nproc-2 = 4
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            sig = os.path.join(os.path.dirname(cdir), "lanes-ab-done")
            os.environ["GATE_LANES_DONE"] = sig
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            run_one, rec = _timeline(sig)
            pool.run_set("glue", muts, run_one, KEY, jobs=2)
            peak = max(n for n, _e in rec)
            ensure(peak <= 4, "pool ran %d at a time with nproc-2=4" % peak)
            ensure(peak >= 3, "pool did not grow up to the cap (peak %d)" % peak)
            ls = _lines(err)
            ensure(len(ls) == 1 and ls[0].startswith("lane C jobs 2->4 at +"), "growth line: %r" % ls)
    with fast_poll(pool, cpus=3):                      # cap 1 < jobs: no growth, never shrinks below jobs
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            sig = os.path.join(os.path.dirname(cdir), "lanes-ab-done")
            pathlib.Path(sig).write_text("")
            os.environ["GATE_LANES_DONE"] = sig
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            run_one, rec = _timeline(sig, create_at=0)
            pool.run_set("glue", muts[:4], run_one, KEY, jobs=2)
            ensure(max(n for n, _e in rec) <= 2, "pool above its base size when the cap is below it")
            ensure(any(l.startswith("lane C jobs stayed 2 (") and "cpu cap" in l for l in _lines(err)), "no stayed line naming the cap: %r" % _lines(err))


def c_cpu_count_is_the_affinity_set(pool):
    """F4: nproc (the CPUs this process may run on) sizes the cap and the default jobs, not os.cpu_count() (the whole host: taskset / cpuset are invisible to it)."""
    from unittest import mock
    with mock.patch("os.sched_getaffinity", return_value={0, 1, 2, 3}), mock.patch("os.cpu_count", return_value=16):
        ensure(pool.host_cpus() == 4, "host_cpus %r with an affinity of 4 CPUs on a 16-CPU host" % pool.host_cpus())
        with sandbox(GATE_MUT_JOBS=None):
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            g = pool.JobGate(1)
            ensure(g.cap == 2 and g.total == 2, "cap %r total %r (want nproc-2 = 2)" % (g.cap, g.total))
            ensure(pool.default_jobs() == 4, "default jobs %r (want min(12, nproc) = 4)" % pool.default_jobs())
    with mock.patch("os.sched_getaffinity", side_effect=OSError("no affinity")), mock.patch("os.cpu_count", return_value=8):
        ensure(pool.host_cpus() == 8, "without sched_getaffinity the whole-host count is the fallback: %r" % pool.host_cpus())


def c_explicit_jobs_never_grow(pool):
    muts = [("m%d" % i, [("old%d" % i, "new%d" % i)]) for i in range(8)]
    for src in ("env", None):                          # check-all.sh says env; without GATE_MUT_JOBS_SRC a set GATE_MUT_JOBS counts as explicit
        with fast_poll(pool, cpus=16):
            with sandbox(GATE_MUT_JOBS="2") as (cdir, err):
                sig = os.path.join(os.path.dirname(cdir), "lanes-ab-done")
                pathlib.Path(sig).write_text("")
                os.environ["GATE_LANES_DONE"] = sig
                if src:
                    os.environ["GATE_MUT_JOBS_SRC"] = src
                run_one, rec = _timeline(sig, create_at=0)
                pool.run_set("glue", muts, run_one, KEY, jobs=2)
                ensure(max(n for n, _e in rec) <= 2, "an explicit GATE_MUT_JOBS=2 grew to %d (src=%r)" % (max(n for n, _e in rec), src))
                ls = _lines(err)
                ensure(len(ls) == 1 and ls[0].startswith("lane C jobs stayed 2 (") and "explicitly" in ls[0], "no 'set explicitly' line: %r" % ls)


def c_pool_stays_without_the_signal(pool):
    muts = [("m%d" % i, [("old%d" % i, "new%d" % i)]) for i in range(6)]
    with fast_poll(pool, cpus=16):
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            run_one, rec = _timeline("", create_at=0, delay=0.1)
            pool.run_set("glue", muts, run_one, KEY, jobs=2)
            ensure(max(n for n, _e in rec) <= 2, "pool grew without the signal file")
            ls = _lines(err)
            ensure(len(ls) == 1 and ls[0].startswith("lane C jobs stayed 2 (") and "still running" in ls[0], "stayed line: %r" % ls)


def c_growth_keeps_cache_and_spot_check(pool):
    """Warm run with the signal present: same verdicts, all hits, the spot-check re-run fresh and inside the permits (not above jobs when the pool did not grow)."""
    with fast_poll(pool, cpus=16):
        with sandbox(GATE_MUT_JOBS=None) as (cdir, err):
            os.environ["GATE_MUT_JOBS_SRC"] = "derived"
            r1 = run(pool, run_one=make_run_one(), jobs=2)
            s1 = dict(pool.LAST["glue"])
            ensure(s1["fresh"] == N, "cold counters: %r" % s1)
            run_one, rec = _timeline("", create_at=0, delay=0.08)
            r2 = pool.run_set("glue", MUTS, run_one, KEY, jobs=2)
            s = pool.LAST["glue"]
            ensure(r2 == r1, "warm verdicts differ")
            ensure((s["hits"], s["fresh"]) == (N, 0) and s["spot"] == (N, N), "warm counters: %r" % s)
            ensure(max(n for n, _e in rec) <= 2, "the spot-check re-runs ran %d at a time with jobs=2 and no signal (they must hold a permit)" % max(n for n, _e in rec))


BEHAVIOUR = [c_order, c_cache_hit_on_second_run, c_miss_on_key_change, c_runner_text_is_keyed, c_failfast_equivalence, c_spot_mismatch_is_caught_and_quarantined,
             c_only_skip_line, c_only_unknown_name_is_an_error, c_only_blank_is_an_error, c_summary_line_and_ran_zero, c_key_parts, c_spot_covers_small_sets_and_salt_rotates, c_worker_exception_is_loud, c_only_deterministic_verdicts_are_stored, c_pool_size_is_used, c_duplicate_labels,
             c_poll_interval_is_short, c_pool_grows_after_lanes_done, c_extra_permits_are_exact, c_pool_never_above_the_cpu_cap, c_cpu_count_is_the_affinity_set, c_explicit_jobs_never_grow, c_pool_stays_without_the_signal, c_growth_keeps_cache_and_spot_check]


def failures(pool):
    out = []
    for chk in BEHAVIOUR:
        try:
            chk(pool)
        except Exception as e:      # a mutant may crash a check in any way: that is a kill too
            out.append("%s: %s: %s" % (chk.__name__, type(e).__name__, str(e)[:160]))
    return out


# ---- honest mutants of wlan_mutant_pool.py: (name, [(old, new)]) -----------------------------------------------------------------------------------------------------

POOL_MUTANTS = [
    ("cache key drops key_parts", [("json.dumps(list(key_parts))", "json.dumps([])")]),
    ("cache key drops the substitutions", [("json.dumps(subs, sort_keys=True)", "json.dumps(0)")]),
    ("cache key drops the runner file text", [("runner, _file_text(__file__)", "\"\", _file_text(__file__)")]),
    ("cache key drops the set name", [("cache.gcc_version(), os.confstr(\"CS_GNU_LIBC_VERSION\") or \"\", set_name,", "cache.gcc_version(), os.confstr(\"CS_GNU_LIBC_VERSION\") or \"\", \"\",")]),
    ("spot-check off", [("        if hits:\n            k = len(hits)", "        if False:\n            k = len(hits)")]),
    ("spot-check samples only 3 % of a small set", [("len(hits) if len(hits) <= SPOT_ALL_MAX else math.ceil(len(hits) / 2) if len(hits) <= SPOT_HALF_MAX else ", "")]),
    ("spot-check samples 3 % of a 41..100 set", [("math.ceil(len(hits) / 2) if len(hits) <= SPOT_HALF_MAX else ", "")]),
    ("cache key drops the pool file text (F7 C)", [("_file_text(__file__), _file_text(CPU_TIMEOUT_PY)", "\"\", _file_text(CPU_TIMEOUT_PY)")]),
    ("cache key drops the cpu_timeout.py text (F5)", [(", _file_text(__file__), _file_text(CPU_TIMEOUT_PY)):", ", _file_text(__file__)):")]),
    ("cache key drops the length prefix (F7 F)", [("h.update(str(len(b)).encode() + b\":\" + b)", "h.update(b)")]),
    ("cache key drops gcc and libc version (F7 G)", [("cache.gcc_version(), os.confstr(\"CS_GNU_LIBC_VERSION\") or \"\", set_name, json.dumps(list(key_parts))", "set_name, json.dumps(list(key_parts))")]),
    ("blank GATE_MUT_ONLY selects nothing (F7 I)", [("        raise ValueError(\"GATE_MUT_ONLY/--only is set but blank", "        return frozenset(); raise ValueError(\"GATE_MUT_ONLY/--only is set but blank")]),
    ("spot salt is a constant (F7 J)", [("    salt = cache.spot_salt()", "    salt = \"const\"")]),
    ("ran=0 does not fail", [("    if ran == 0:\n        raise MutantSetEmpty", "    if False:\n        raise MutantSetEmpty")]),
    ("no summary line", [("    print(\"MUTANT SET name=%s ran=%d skipped=%d\" % (name, ran, len(out) - ran), file=sys.stderr)", "    pass")]),
    ("ran counts errors as verdicts", [("ran = sum(1 for v in out if v[0] in STORABLE)", "ran = len(out)")]),
    ("an unwired set name is accepted", [("KNOWN_SETS = (\"header\", \"glue\", \"g2\", \"g2l\", \"d4b\", \"g2va\", \"g2host\", \"g2fix\", \"dma\")", "KNOWN_SETS = (\"header\", \"glue\", \"g2\", \"g2l\", \"d4b\", \"g2va\", \"g2host\", \"g2fix\", \"dma\", \"g3\")")]),
    ("spot-check mismatch not raised", [("    if bad:\n        raise MutantCacheMismatch", "    if False:\n        raise MutantCacheMismatch")]),
    ("poisoned cache not quarantined", [("cache.quarantine()", "None")]),
    ("worker exception swallowed as a kill", [("return \"error\", \"worker raised", "return \"fail\", \"worker raised")]),
    ("worker exception not loud", [("            print(\"MUTANT SET ERROR %s: %s: worker raised", "            (lambda *a, **k: None)(\"MUTANT SET ERROR %s: %s: worker raised")]),
    ("order scrambled (completion order)", [("out = list(ex.map(one, range(len(items))))", "futs = [ex.submit(one, i) for i in range(len(items))]; out = [f.result() for f in concurrent.futures.as_completed(futs)]")]),
    ("skip is silent", [("    print(\"MUTANT SET skipped: %s (GATE_MUT_ONLY=%s)\" % (name, os.environ.get(\"GATE_MUT_ONLY\", \"\")), file=sys.stderr)\n", "    pass\n")]),
    ("hit counted as fresh", [("                    st[\"hits\"] += 1", "                    st[\"fresh\"] += 1")]),
    ("unknown --only name accepted", [("    if unknown:\n", "    if False:\n")]),
    ("pool size ignored (serial)", [("ThreadPoolExecutor(max_workers=gate.max_workers) as ex:\n        out =", "ThreadPoolExecutor(max_workers=1) as ex:\n        out =")]),
    ("compile error without a location stored", [("return cache.deterministic(\"compile\", 1, msg)", "return True")]),
    ("fail-fast equivalence compares nothing", [("for m, a, b in zip(items, full, fast) if a != b]", "for m, a, b in zip(items, full, fast) if False]")]),
    # K1 dynamic pool (JobGate)
    ("growth never happens", [("        if self.grown or self.total <= self.base or not os.path.exists(self.path):\n            return", "        if True:\n            return")]),
    ("cpu cap ignored (nproc-0)", [("self.cap = max(1, host_cpus() - 2)", "self.cap = max(1, host_cpus())")]),
    ("cpu count from os.cpu_count() instead of the affinity set (F4)", [("return max(1, len(os.sched_getaffinity(0)))", "return max(1, os.cpu_count() or 1)")]),
    ("default jobs from os.cpu_count() (F4)", [("min(12, host_cpus())", "min(12, os.cpu_count() or 1)")]),
    ("grown total ignores the cap", [("min(self.base + GROW_EXTRA, self.cap)", "self.base + GROW_EXTRA")]),
    ("explicit GATE_MUT_JOBS=env still grows", [("return (src != \"env\") if src else \"GATE_MUT_JOBS\" not in os.environ", "return True")]),
    ("set GATE_MUT_JOBS without _SRC counts as derived", [("if src else \"GATE_MUT_JOBS\" not in os.environ", "if src else True")]),
    ("extra permits count wrong (total instead of extra)", [("for _ in range(self.total - self.base):", "for _ in range(self.total):")]),
    ("growth line not printed", [("                print(\"lane C jobs %d->%d at +%ds\" % (self.base, self.total, _GROWN[\"at\"]), file=sys.stderr, flush=True)", "                pass")]),
    ("stayed line not printed", [("        print(\"lane C jobs stayed %d (%s%s)\" % (self.base, why, \", \" + what if what else \"\"), file=sys.stderr, flush=True)", "        pass")]),
    ("all permits from the start", [("self.sem = threading.Semaphore(self.total if self.grown else self.base)", "self.sem = threading.Semaphore(self.total)")]),
    ("waiting workers never poll the signal", [("        while not self.sem.acquire(timeout=POLL_S):\n            self.check()", "        while not self.sem.acquire(timeout=POLL_S):\n            pass")]),
    ("poll interval 30 s", [("POLL_S = 0.5 ", "POLL_S = 30 ")]),
    ("thread pool stays at the base size", [("ThreadPoolExecutor(max_workers=gate.max_workers) as ex:\n        out =", "ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:\n        out =")]),
    ("spot-check re-runs outside the permits", [("_verdict(gate.run(run_one, label, subs_of[label]))", "_verdict(run_one(label, subs_of[label]))")]),
    ("fresh run outside the permits", [("v = _verdict(gate.run(run_one, label, subs))", "v = _verdict(run_one(label, subs))")]),
]


def load_mutant(src, d):
    p = os.path.join(d, "wlan_mutant_pool.py")
    pathlib.Path(p).write_text(src)
    shutil.copy(os.path.join(TOOLS, "cpu_timeout.py"), d)          # the pool keys on the cpu_timeout.py next to it; a missing file must not kill every mutant vacuously
    spec = importlib.util.spec_from_file_location("wlan_mutant_pool_mutant", p)
    m = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(m)           # the module puts its own directory first on sys.path: undo that
    finally:
        sys.path[:] = saved
    return m


SLICES = 3


def kill_slice(case, k):
    """The old test_every_pool_mutant_is_killed body for the mutants with index % SLICES == k (T-2712)."""
    base = pathlib.Path(POOL_SRC).read_text()
    survived, mine = [], [m for n, m in enumerate(POOL_MUTANTS) if n % SLICES == k]
    with tempfile.TemporaryDirectory() as d:
        for name, subs in mine:
            src = base
            for old, new in subs:
                case.assertEqual(src.count(old), 1, "%s: needle must occur exactly once: %r" % (name, old))
                src = src.replace(old, new)
            case.assertNotEqual(src, base, name)
            got = failures(load_mutant(src, d))
            if not got:
                survived.append(name)
            else:
                print("killed %-50s by %s" % (name, ",".join(g.split(":")[0] for g in got)), file=sys.__stderr__)
    case.assertEqual(survived, [], "SURVIVED: %s" % survived)
    case.assertGreaterEqual(len(mine), 1)
    print("RESULT: PASS wlan_mutant_pool mutants killed %d/%d (slice %d of %d)" % (len(mine), len(mine), k, SLICES), file=sys.__stderr__)


class Pool(unittest.TestCase):
    def test_real_module_passes_every_check(self):
        self.assertEqual(failures(real), [])

    def test_the_pool_mutants_are_split_over_the_slice_files(self):
        """T-2712 (no unit file above ~60 s): test_wlan_mutant_pool_k0/k1/k2.py each kill the mutants with index % SLICES == k (kill_slice); SLICES must match the files."""
        for k in range(SLICES):
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_wlan_mutant_pool_k%d.py" % k)), k)
        self.assertGreaterEqual(len(POOL_MUTANTS), 6)

    def test_put_tmp_name_includes_the_tag(self):
        """F6: two workers storing one key (identical subs) must not share a temp file."""
        names = []
        real_replace = os.replace
        with sandbox() as (cdir, err):
            os.replace = lambda src, dst: (names.append(os.path.basename(src)), real_replace(src, dst))[1]
            try:
                self.assertTrue(cache.put("ab" + "c" * 62, "pass", "ok", tag="3"))
                self.assertTrue(cache.put("ab" + "c" * 62, "pass", "ok", tag="4"))
            finally:
                os.replace = real_replace
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])

    def test_store_rule(self):
        self.assertTrue(real.store_ok("pass", ""))
        self.assertTrue(real.store_ok("fail", "BAD"))
        self.assertTrue(real.store_ok("compile", "g.c:3:4: error: x"))
        self.assertFalse(real.store_ok("compile", "collect2: fatal error: ld terminated"))
        for st in ("infra", "timeout", "nomatch", "error"):
            self.assertFalse(real.store_ok(st, "x"))


if __name__ == "__main__":
    unittest.main()
