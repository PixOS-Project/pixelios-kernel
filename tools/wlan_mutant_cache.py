"""Persistent verdict cache for tools/test_wlan_wr_mutants.py (research 71).

A mutant verdict is a pure function of: both header texts, the two test sources (they include nothing else of the repo), the compiler, its flags and the libc. The key is the sha256 of all of them
plus CACHE_VERSION. Only deterministic verdicts are stored (compile error; a test failure that printed RESULT: FAIL or crashed with SIGSEGV/SIGABRT/SIGFPE/SIGBUS; clean run = survived); a CPU-limit hang of the
re-run (ALONE_LIMIT, CPU seconds: load-independent) is stored as a kill with a fixed text; a pool timeout, a wall-backstop or any infra
error (compiler killed or out of CPU, out of memory, run killed by SIGKILL/SIGTERM/SIGXCPU, any other exit code) is never stored. A hit returns exactly what the fresh run returned.
Env: GATE_MUT_CACHE=path overrides the directory, GATE_MUT_CACHE=0 disables."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

CACHE_VERSION = "4"                 # 2: fail stored only with RESULT: FAIL or a crash signal; messages carry <D> for the temp dir (PLAN-FAST-GATE D2)
                                    # 3: compile stored only with a source-location error: diagnostic; <D> rewritten before the tail cut (review mutants-fast A2, A6)
                                    # 4: a re-run (ALONE_LIMIT) over its CPU limit is stored as ("fail", HANG_MSG); the test binary is built fail-fast (-DSWW_FAILFAST=1, AP-114)
                                    # Bump by hand whenever the storage rule, RLIMIT_FSIZE/CPU or CC semantics change: old entries must not be reused.
CRASH_RCS = (-11, -6, -8, -7)       # SIGSEGV, SIGABRT, SIGFPE, SIGBUS: the test binary itself crashed (deterministic)
SPOT_K = 12
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".cache", "pixelios", "wlan-mutants")
# a deterministic compile failure names a source location: "file.c:12:3: error: ..." (or "fatal error:", e.g. a missing include)
_LOC_ERROR = re.compile(r"^[^\s:][^:\n]*:\d+(?::\d+)?: (?:fatal )?error: ", re.M)
_INFRA_SIGNS = ("virtual memory exhausted", "internal compiler error", "Killed signal", "out of memory", "No space left")
_gcc_version = None


def cache_dir():
    v = os.environ.get("GATE_MUT_CACHE")
    if v == "0":
        return None
    return v or DEFAULT_DIR


def gcc_version():
    global _gcc_version
    if _gcc_version is None:
        _gcc_version = subprocess.run(["gcc", "--version"], capture_output=True, text=True, timeout=60).stdout
    return _gcc_version


def make_key(chip, hdr, test_texts, flags):
    """test_texts: [(basename, text)] in build order."""
    h = hashlib.sha256()
    for part in (CACHE_VERSION, gcc_version(), os.confstr("CS_GNU_LIBC_VERSION") or "", json.dumps(flags), chip, hdr, json.dumps(test_texts)):
        b = part.encode("utf-8", "surrogateescape")
        h.update(str(len(b)).encode() + b":" + b)       # length-prefixed: no two inputs concatenate to the same stream
    return h.hexdigest()


def deterministic(status, rc=None, text=""):
    """May this verdict be stored? rc is the process return code of the step that decided it (None when there is none)."""
    if status not in ("compile", "fail", "pass"):
        return False
    if status == "compile":
        # whitelist, not only a blacklist (review A2): a link/driver failure ("collect2: fatal error: ld terminated with signal 9") has rc 1 and no location
        return bool(rc) and rc > 0 and bool(_LOC_ERROR.search(text)) and not infra_text(text)
    if status == "fail":
        return "RESULT: FAIL" in text or rc in CRASH_RCS      # anything else (rc -24 SIGXCPU, ENOMEM, other) is recomputed every time
    return True


def infra_text(text):
    return any(s in text for s in _INFRA_SIGNS)


def get(key):
    d = cache_dir()
    if d is None:
        return None
    try:
        with open(os.path.join(d, key[:2], key), encoding="utf-8") as f:
            v = json.load(f)
        if v.get("v") == CACHE_VERSION and v.get("status") in ("compile", "fail", "pass") and isinstance(v.get("msg"), str):
            return v["status"], v["msg"]
    except FileNotFoundError:   # a cache miss is the normal answer   # lint-silent: expected a cache miss is the normal answer
        pass
    except (OSError, ValueError) as _err:
        print(f"wlan_mutant_cache GAP: (OSError, ValueError) ignored in get(): {_err}", file=sys.stderr)
    return None


def state():
    """off (GATE_MUT_CACHE=0), ro (the directory exists but is not writable: an all-hits run stores nothing, so a failed put() would never show it), or on;
    the caller also turns on into ro when a put() failed."""
    d = cache_dir()
    if d is None:
        return "off"
    return "ro" if os.path.isdir(d) and not os.access(d, os.W_OK | os.X_OK) else "on"


def put(key, status, msg, tag=""):
    """-> True stored, False not stored (cache off, bad status, or the write failed: the caller reports it, rule 8).
    tag (e.g. the mutant index) goes into the temp file name: two workers storing the same key never share a temp file."""
    d = cache_dir()
    if d is None or status not in ("compile", "fail", "pass"):
        return False
    sub = os.path.join(d, key[:2])
    try:
        os.makedirs(sub, exist_ok=True)
        tmp = os.path.join(sub, ".%s.%d.%s.tmp" % (key, os.getpid(), tag))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": CACHE_VERSION, "status": status, "msg": msg}, f)
        os.replace(tmp, os.path.join(sub, key))
    except OSError:   # lint-silent: expected an unwritable cache costs only time; the MUTCACHE line says cache=ro (False)
        return False    # costs only time, but the MUTCACHE line says cache=ro
    return True


def quarantine():
    """Rename the cache directory to <dir>.bad-<epoch> after a spot-check mismatch (nothing in it is trusted any more). Returns the new path or None."""
    d = cache_dir()
    if d is None or not os.path.isdir(d):
        return None
    bad = "%s.bad-%d" % (d.rstrip("/"), time.time())
    os.rename(d, bad)
    return bad


def spot_salt():
    """Rotates the spot-check sample (review A3): GATE_MUT_SPOT_SALT, else the UTC hour, so every cached entry is re-verified over time. Printed in the MUTCACHE line."""
    return os.environ.get("GATE_MUT_SPOT_SALT") or str(int(time.time()) // 3600)


def spot_sample(hits, k=SPOT_K, salt=""):
    """hits: {name: (status, equiv)} of cache hits. Sample: every equivalent survivor that is a hit, plus k others taken round-robin from the 'fail', 'pass' and 'compile' hits in sha256(salt + name) order."""
    order = lambda names: sorted(names, key=lambda n: hashlib.sha256((salt + n).encode("utf-8", "surrogateescape")).hexdigest())
    eq = order(n for n, (st, e) in hits.items() if e and st == "pass")
    lists = [order(n for n, (st, e) in hits.items() if st == w and not (e and st == "pass")) for w in ("fail", "pass", "compile")]
    pick = []
    while len(pick) < k and any(lists):
        for lst in lists:
            if lst and len(pick) < k:
                pick.append(lst.pop(0))
    return eq + pick


def spot_check(hits, fresh, k=SPOT_K, map_fn=map, salt=""):
    """hits: {name: (cached (status, msg), equiv, mutated header)}; fresh(header) -> (status, msg) computed without the cache.
    Returns (checked, mismatched names). Every sampled hit is re-run; a difference in status or message is a mismatch."""
    names = spot_sample({n: (v[0][0], v[1]) for n, v in hits.items()}, k, salt)
    seen, uniq = set(), []
    for n in names:
        if hits[n][2] not in seen:         # identical headers share one verdict: re-run once
            seen.add(hits[n][2])
            uniq.append(n)
    got = list(map_fn(lambda n: fresh(hits[n][2]), uniq))
    bad = [n for n, g in zip(uniq, got) if tuple(g) != tuple(hits[n][0])]
    return len(uniq), bad
