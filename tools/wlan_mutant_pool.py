"""Pooled, cached mutant-set runner for tools/test_wlan_wr_mutants.py (PLAN-GATE-2X D1/G4, docs/research/mutant-run-speed.md).

run_set(name, mutants, run_one, key_parts, jobs=GATE_MUT_JOBS) -> [(status, msg)] in input order (None when GATE_MUT_ONLY skips the set).
  mutants    [(label, subs)] or [(label, subs, equiv)]: subs is anything json.dumps can hold (the substitution list); equiv only widens the spot-check sample
  run_one    run_one(label, subs) -> (status, msg), status "pass" | "fail" | "compile" (stored) or anything else (never stored, never a kill: "nomatch", "infra", "timeout")
  key_parts  [str]: everything the verdict depends on that is not in `subs`: the base text, the harness text, every header it includes, the CC flags
Verdict key = sha256(cache version, gcc, libc, set name, key_parts, subs, the SOURCE FILE of run_one (the runner), this file), length-prefixed like make_key.
A harness, runner or pool change therefore misses; a cached verdict is never trusted for text that changed. A worker exception is a loud ("error", ..) verdict
(never stored, never "fail"). Spot-check re-runs hits uncached: ALL of them when a set has <= SPOT_ALL_MAX (40) hits, half of them (SPOT_HALF_MAX 100) up to 100 hits, else
max(SPOT_K, 3 %); a mismatch prints the names, quarantines the cache dir and raises MutantCacheMismatch (review gate-2x-s1-r1 F3; "all up to 100" cost a full cold run
for glue, 50 s warm at GATE_MUT_JOBS=6 against the 40 s budget, measured 2026-10-31, so 41..100 hits take half).
stderr: one `MUTCACHE set=<name> hits=N fresh=N stored=N spot=a/b ...` line per set; `MUTANT SET skipped: <name> (GATE_MUT_ONLY=...)` for a set not selected;
and, at the end of every run_set call, `MUTANT SET name=<n> ran=N skipped=M` (ran = mutants with a real pass/fail/compile verdict, skipped = the rest: worker error, nomatch, infra, timeout).
ran=0 raises MutantSetEmpty: a set that ran nothing is never OK (review F1).
Adopting a table (d4d g2/g2l/d4b/audit: a call-site change, tools/lint-mutant-needles G4 fails a table that is not passed to run_set).
KNOWN_SETS lists ONLY the sets that are wired through want()/run_set in tools/test_wlan_wr_mutants.py (a name that is accepted but not wired would skip every mutant and
pass, review F1); adopting a set = add its name to KNOWN_SETS in the same commit as its want()/run_set call:
  1. build the per-mutant text in a serial loop WITHOUT a compiler (keep assertIn / "pattern not found" messages), e.g. texts[label] = mutated;
  2. def run_one(label, subs): make a PRIVATE dir (tempfile.mkdtemp(dir=work)), write headers and texts[label] there, gcc, run_bounded, return (status, msg);
     g2_kill_test / d4b_kill_test do this (one mkdtemp per mutant, the base build in its own);
  3. add "g2" to KNOWN_SETS; if wlan_mutant_pool.want("g2") is False, return (the skip line is printed); res = wlan_mutant_pool.run_set("g2", G2_MUTANTS, run_one, [base text, wr text, harness, " ".join(flags)]);
  4. map the verdicts: "fail" = killed, "pass" = SURVIVED, "compile" = does not compile, anything else = failure with its message;
  5. add -DSWG_FAILFAST=1 to the mutant builds only (the unmutated base build stays full) after proving equal kill sets (failfast_mismatches);
  6. a pure-python audit set is the same with run_one(label, subs) -> ("pass" if audit(...) == [] else "fail", text); reserved name "audit".
Dynamic pool (PLAN-GATE-2X K1, cold gate): JobGate keeps a thread pool of the maximum size behind a semaphore of `jobs` permits (GATE_MUT_JOBS). When the file
GATE_LANES_DONE (default shim/out/gate/.lanes-ab-done; tools/check-all.sh creates it exactly when it releases the host lock, lanes A and B done) appears, GROW_EXTRA (8)
more permits are released, the total never above nproc-2 (K5). Waiting workers poll the file (acquire timeout POLL_S, no busy loop). One stderr line per process: `lane C jobs 6->14 at +NNNs`
(seconds since this module was imported = the start of lane C), or `lane C jobs stayed 6 (reason)` when it never grew. An explicitly set GATE_MUT_JOBS never grows (check-all.sh exports
GATE_MUT_JOBS_SRC=env|derived; with no _SRC variable a set GATE_MUT_JOBS counts as explicit). Callers outside run_set (test_wlan_wr_mutants.run_all) use JobGate(jobs).run / .max_workers / .report().
Env: GATE_MUT_JOBS (pool size), GATE_MUT_ONLY=<KNOWN_SETS, comma list> (or --only on the test file; unset = all sets, set but blank = error), GATE_MUT_CACHE (see wlan_mutant_cache)."""
import concurrent.futures
import hashlib
import inspect
import json
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wlan_mutant_cache as cache

KNOWN_SETS = ("header", "glue", "g2", "g2l", "d4b", "g2va", "g2host", "g2fix", "dma")      # exactly the sets wired through want()/run_set in tools/test_wlan_wr_mutants.py (header, glue, g2, g2l, d4b, g2va, g2host, g2fix) and tools/test_wlan_dma_mutants.py (dma); see the docstring before adding one
POOL_VERSION = "2"
SPOT_ALL_MAX = 40                    # a set with at most this many cache hits re-runs ALL of them in the spot-check (F3)
SPOT_HALF_MAX = 100                  # up to this many hits: half of them (glue: 66 hits -> 33 re-runs, ~28 s warm at 6 jobs)
CPU_TIMEOUT_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpu_timeout.py")     # run_cpu_limited semantics decide hang verdicts: part of the key (F5)
STORABLE = ("pass", "fail", "compile")
LAST = {}                    # set name -> stats of its last run_set (tests and callers read it)
_LOCK = threading.Lock()


class MutantSetEmpty(RuntimeError):
    """A set produced no real verdict (ran=0): it checked nothing and must not count as OK."""


class MutantCacheMismatch(RuntimeError):
    """A cached verdict differs from a fresh re-run: the cache was quarantined and nothing in it is trusted."""


def host_cpus():
    """The CPUs this process may run on: what `nproc` prints (sched_getaffinity), which check-all.sh and tools/gate-unit-shards use for their budget and cap.
    os.cpu_count() counts the whole host and ignores taskset / cpuset (review gate-cold-k14 r1 F4)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def default_jobs():
    return int(os.environ.get("GATE_MUT_JOBS", min(12, host_cpus())))


GATE_MUT_JOBS = default_jobs()
LANES_DONE_DEFAULT = "shim/out/gate/.lanes-ab-done"
GROW_EXTRA = 8                       # permits released when lanes A+B are done (6 -> 14 on a 16-thread host)
POLL_S = 0.5                         # a waiting worker looks for the signal file at least this often (never busy, never > 1 s)
_T0 = time.monotonic()
_GROWN = {}                          # process-wide: {"at": seconds, "total": N} once the pool has grown (a second set starts at the grown size)


def growth_allowed():
    """False when the user set GATE_MUT_JOBS explicitly (it wins): check-all.sh says so with GATE_MUT_JOBS_SRC=env; without that variable a set GATE_MUT_JOBS is explicit too."""
    src = os.environ.get("GATE_MUT_JOBS_SRC")
    return (src != "env") if src else "GATE_MUT_JOBS" not in os.environ


class JobGate:
    """Semaphore of `jobs` permits that grows by GROW_EXTRA once the lanes-done file exists (cap nproc-2); .max_workers sizes the thread pool, .run(fn, *a) runs fn under a permit.
    What the cap bounds (K5, F5): the mutant workers of this pool only. It grows when lanes A+B are done (no unit shard and no lane A step is running any more), so
    pool workers <= nproc-2 at that time; each mutant worker is a gcc/test child, and check-all.sh's own parents are not counted. The unit shards' growth (tools/gate-unit-shards)
    is capped at nproc-2 minus this pool's BASE size, so the two pools together stay <= nproc-2 while both run."""

    def __init__(self, jobs):
        self.base = max(1, int(jobs))
        self.cap = max(1, host_cpus() - 2)
        self.allowed = growth_allowed()
        self.total = max(self.base, min(self.base + GROW_EXTRA, self.cap)) if self.allowed else self.base
        self.path = os.environ.get("GATE_LANES_DONE") or LANES_DONE_DEFAULT
        self.grown = bool(_GROWN) and self.total > self.base          # an earlier set already grew: start at the grown size
        self.sem = threading.Semaphore(self.total if self.grown else self.base)
        self._glock = threading.Lock()

    @property
    def max_workers(self):
        return self.total

    def check(self):
        """Release the extra permits once, when the signal file exists."""
        if self.grown or self.total <= self.base or not os.path.exists(self.path):
            return
        with self._glock:
            if self.grown:
                return
            self.grown = True
            for _ in range(self.total - self.base):
                self.sem.release()
        with _LOCK:
            if not _GROWN:
                _GROWN.update(at=int(time.monotonic() - _T0), total=self.total)
                print("lane C jobs %d->%d at +%ds" % (self.base, self.total, _GROWN["at"]), file=sys.stderr, flush=True)

    def run(self, fn, *args):
        self.check()
        while not self.sem.acquire(timeout=POLL_S):
            self.check()
        try:
            return fn(*args)
        finally:
            self.sem.release()

    def report(self, what=""):
        """The `stayed` line when this gate never grew (a grown gate printed its line when it happened)."""
        if self.grown or _GROWN:
            return
        why = ("GATE_MUT_JOBS set explicitly, no growth" if not self.allowed else
               "already at the cpu cap nproc-2=%d" % self.cap if self.total <= self.base else "lanes A+B were still running (%s not seen)" % self.path)
        print("lane C jobs stayed %d (%s%s)" % (self.base, why, ", " + what if what else ""), file=sys.stderr, flush=True)


def only_sets(raw=None):
    """-> None (GATE_MUT_ONLY unset: all sets) or a frozenset of set names; a name outside KNOWN_SETS (unknown, or valid but not wired) and a set-but-blank value are errors (rule 8)."""
    raw = os.environ.get("GATE_MUT_ONLY") if raw is None else raw
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("GATE_MUT_ONLY/--only is set but blank %r (accepted sets: %s; unset it to run all sets)" % (raw, ", ".join(KNOWN_SETS)))
    names = [t.strip() for t in raw.split(",")]
    unknown = [t for t in names if t not in KNOWN_SETS]
    if unknown:
        raise ValueError("GATE_MUT_ONLY names unknown or unwired set(s) %s (accepted: %s)" % (", ".join(repr(u) for u in unknown), ", ".join(KNOWN_SETS)))
    return frozenset(names)


def want(name):
    """True if set `name` runs; False after one loud skip line. An unregistered set name is an error."""
    if name not in KNOWN_SETS:
        raise ValueError("mutant set %r is not in KNOWN_SETS %s (register it in tools/wlan_mutant_pool.py)" % (name, KNOWN_SETS))
    sel = only_sets()
    if sel is None or name in sel:
        return True
    print("MUTANT SET skipped: %s (GATE_MUT_ONLY=%s)" % (name, os.environ.get("GATE_MUT_ONLY", "")), file=sys.stderr)
    return False


def apply_only_argv(argv):
    """Remove `--only a,b` from argv (in place) and export it as GATE_MUT_ONLY; an unknown, unwired or blank value exits with the accepted list."""
    while "--only" in argv:
        i = argv.index("--only")
        if i + 1 >= len(argv):
            raise SystemExit("--only needs a value (%s)" % ",".join(KNOWN_SETS))
        os.environ["GATE_MUT_ONLY"] = argv[i + 1]
        del argv[i:i + 2]
    try:
        only_sets()
    except ValueError as e:
        raise SystemExit(str(e))


def _file_text(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def runner_text(run_one):
    """The source FILE that defines run_one: a change anywhere in the runner invalidates the set's cache."""
    f = run_one
    while hasattr(f, "func"):          # functools.partial
        f = f.func
    path = inspect.getsourcefile(inspect.unwrap(f))
    if not path:
        raise ValueError("cannot find the source file of run_one %r (the runner text is part of the cache key)" % (run_one,))
    return _file_text(path)


def verdict_key(set_name, key_parts, subs, runner):
    h = hashlib.sha256()
    for part in (cache.CACHE_VERSION, POOL_VERSION, cache.gcc_version(), os.confstr("CS_GNU_LIBC_VERSION") or "", set_name, json.dumps(list(key_parts)), json.dumps(subs, sort_keys=True), runner, _file_text(__file__), _file_text(CPU_TIMEOUT_PY)):
        b = part.encode("utf-8", "surrogateescape")
        h.update(str(len(b)).encode() + b":" + b)
    return h.hexdigest()


def store_ok(status, msg):
    """Default storage rule: a clean pass or a fail; a compile error only with a source-location diagnostic (wlan_mutant_cache.deterministic)."""
    if status == "compile":
        return cache.deterministic("compile", 1, msg)
    return status in STORABLE


def _verdict(v):
    if not (isinstance(v, (tuple, list)) and len(v) >= 2 and isinstance(v[0], str) and isinstance(v[1], str)):
        raise TypeError("run_one must return (status, msg) strings, got %r" % (v,))
    return v[0], v[1]


def _norm(mutants):
    items, seen = [], set()
    for m in mutants:
        if len(m) not in (2, 3):
            raise ValueError("mutant %r is not (label, subs[, equiv])" % (m,))
        label, subs, equiv = m[0], m[1], (m[2] if len(m) == 3 else False)
        if label in seen:
            raise ValueError("duplicate mutant label %r" % (label,))
        seen.add(label)
        items.append((label, subs, equiv))
    return items


def run_set(name, mutants, run_one, key_parts, jobs=GATE_MUT_JOBS, store=store_ok):
    if not want(name):
        return None
    items = _norm(mutants)
    runner = runner_text(run_one)
    state = cache.state()
    salt = cache.spot_salt()
    keys = [verdict_key(name, key_parts, subs, runner) for _l, subs, _e in items]
    st = {"hits": 0, "fresh": 0, "stored": 0, "store_failed": 0, "errors": 0}
    hits = {}
    subs_of = {l: s for l, s, _e in items}

    def one(i):
        label, subs, equiv = items[i]
        if state != "off":
            hit = cache.get(keys[i])
            if hit:
                with _LOCK:
                    st["hits"] += 1
                    hits[label] = (hit, equiv, label)
                return hit
        with _LOCK:
            st["fresh"] += 1
        try:
            v = _verdict(gate.run(run_one, label, subs))
        except Exception as e:             # a crashed worker is a loud failure, never a kill and never a pass
            with _LOCK:
                st["errors"] += 1
            print("MUTANT SET ERROR %s: %s: worker raised %s: %s" % (name, label, type(e).__name__, str(e)[:300]), file=sys.stderr)
            return "error", "worker raised %s: %s" % (type(e).__name__, str(e)[:300])
        if state != "off" and store(*v):
            with _LOCK:
                st["stored" if cache.put(keys[i], *v, tag=str(i)) else "store_failed"] += 1
        return v

    spot, bad = (0, 0), []
    gate = JobGate(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=gate.max_workers) as ex:
        out = list(ex.map(one, range(len(items))))             # ex.map yields in input order
        if hits:
            k = len(hits) if len(hits) <= SPOT_ALL_MAX else math.ceil(len(hits) / 2) if len(hits) <= SPOT_HALF_MAX else max(cache.SPOT_K, math.ceil(0.03 * len(hits)))
            try:
                checked, bad = cache.spot_check(hits, lambda label: _verdict(gate.run(run_one, label, subs_of[label])), k=k, map_fn=lambda f, xs: list(ex.map(f, xs)), salt=salt)
            except Exception as e:
                print("MUTCACHE set=%s spot-check re-run raised %s: %s" % (name, type(e).__name__, str(e)[:300]), file=sys.stderr)
                raise
            spot = (checked - len(bad), checked)
    gate.report("set " + name)
    c = state
    if c == "on":
        c = "ro" if st["store_failed"] else "ok"
    line = "MUTCACHE set=%s hits=%d fresh=%d stored=%d spot=%d/%d cache=%s store_failed=%d errors=%d salt=%s %s" % (
        name, st["hits"], st["fresh"], st["stored"], spot[0], spot[1], c, st["store_failed"], st["errors"], salt, "MISMATCH" if bad else "ok")
    LAST[name] = dict(st, spot=spot, mismatch=bad, line=line, cache=c)
    if bad:
        print("MUTCACHE MISMATCH set=%s %s" % (name, ", ".join(bad)), file=sys.stderr)
        try:
            print("MUTCACHE MISMATCH: cache dir renamed %s" % cache.quarantine(), file=sys.stderr)
        except OSError as e:
            print("MUTCACHE MISMATCH: quarantine failed: %s" % e, file=sys.stderr)
    print(line, file=sys.stderr)
    ran = sum(1 for v in out if v[0] in STORABLE)
    print("MUTANT SET name=%s ran=%d skipped=%d" % (name, ran, len(out) - ran), file=sys.stderr)
    if bad:
        raise MutantCacheMismatch("set %s: cached verdict differs from a fresh run for %s" % (name, ", ".join(bad)))
    if ran == 0:
        raise MutantSetEmpty("set %s ran=0 of %d mutants: no pass/fail/compile verdict at all, nothing was checked" % (name, len(out)))
    return out


def failfast_mismatches(mutants, run_full, run_ff, jobs=GATE_MUT_JOBS):
    """Proof helper for a harness fail-fast switch: run every mutant with the full sweep and with the fail-fast build (uncached, pooled) and return
    [(label, full status, fail-fast status)] for each mutant whose STATUS differs ([] = identical kill set). Callers also check the unmutated base separately."""
    items = _norm(mutants)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        full = list(ex.map(lambda m: _verdict(run_full(m[0], m[1]))[0], items))
        fast = list(ex.map(lambda m: _verdict(run_ff(m[0], m[1]))[0], items))
    return [(m[0], a, b) for m, a, b in zip(items, full, fast) if a != b]
