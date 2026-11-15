#!/usr/bin/env python3
"""Content-keyed memo for the mutation self-tests of tools/test-bluejay-boot.py (docs/research/gate-speed.md R2).

run(fn, arg_bytes, script=PATH) calls fn() once per distinct input; a later call with the same input prints the captured stdout
(each line suffixed " (memo <key16>)") and returns the stored result without running fn.

Key = sha256 of: bytes of `script` and of this file (any audit or memo edit invalidates everything), fn name, arg bytes, sys.path, sys.version, python implementation.
Deps: every file fn opened in the cold run (sys.addaudithook, event `open`), hashed at open time and re-hashed after fn (any change = nothing stored), stored as (path, sha256); a hit re-hashes them all.
Paths under the repo root (parent of tools/) are stored relative to it, so worktrees sharing the cache never verify each other's files.
Never stored: a .pyc without its source or whose recorded mtime/size differ from it (a same-size edit in the same mtime second cannot be told apart, Python accepts it too; nor can an mtime-preserving copy (cp -p/rsync -a/tar) of a same-size source; a pyc written in the same mtime tick as its source is not stored: coarse kernel timestamps, perf only), a FAIL (exception), a run that started a process (subprocess/os.system/exec/spawn/fork audit events) or opened a
file for writing outside __pycache__ (fail closed).
Directory listings (os.listdir/os.scandir) and glob.glob calls are inputs too: stored as (kind, path, sha256 of the sorted names, taken when fn listed it; re-checked after fn, a difference = nothing stored) and re-listed on a hit;
a listing by fd or with root_dir/dir_fd, and pathlib glob/rglob, is not storable. Failed opens and os.stat/os.lstat/os.access/os.readlink calls (os.path.exists/isfile/isdir/getsize/realpath, Path.exists) are inputs: stored as (kind, path, state) where state is the file type (S_IFMT) plus st_size for a regular file, None if missing (st_mtime is NOT recorded: a gap), the access() bool, or the link target, and re-checked on a hit (stat by fd/dir_fd is not storable; an unreadable opened file is not storable; importlib's own probes use the posix module and are not seen). Corrupt or foreign entries are a miss (a body that is not JSON, not an object, or lacks a field run() reads: one stderr line naming the file, never an exception; the cold run rewrites it). Writes are temp file + os.replace.
Stats (G5, docs/plans/PLAN-GATE-2X.md): if $PIXELIOS_AUDIT_MEMO_STATS names a file, every run() appends one line "hit<TAB>name" or "cold<TAB>name<TAB>reason" (reason: miss | off | fail | not-stored:<why>; one line per run, a failed store is not-stored:write only); shim/build.sh turns them into its G5 line with `audit_memo.py --summary FILE [--expect-runs]`: `memo hits=N cold=M`, `memo DISABLED ...` (memo off), `memo STATS UNAVAILABLE|EMPTY ...` (never a silent hits=0 cold=0). An unwritable stats file warns on stderr once. A cold run that was not stored is loud here, never silent.
warm(SCRIPT, NAME) is True if the memo holds the entry of NAME (arg b"") for the current SCRIPT + memo code (`test-bluejay-boot.py --memo-warm`, used by check-all.sh to log a cold or stale memo; K3 removed the serial warm-up build it used to select).
Per-key lock (PLAN-GATE-2X K3): on a miss run() takes <key>.lock (O_CREAT|O_EXCL, holder pid inside) before computing and removes it after the store, so several processes missing the same entry (the gate's build variants started at once on a cold memo, or the workers of tools/audit_pool.py) compute it once: a process that finds the lock held waits until the entry appears or the lock goes away, then reloads. It fails open to computing the entry itself (never a failure, never a pass): after $PIXELIOS_AUDIT_MEMO_LOCK_WAIT seconds (waiters take the lock one after the other once the holder drops it, find the entry on the reload and hit) (default 60; 0 = no locking), when the cache dir is not writable, or when the holder's pid is gone or its lock file stays empty for > 10 s (a crashed holder's lock is removed, said on stderr), or on any OSError of the lock itself (said on stderr). The lock never changes a key or an entry; its opens are not inputs of an outer recorder.
Cache dir: $PIXELIOS_AUDIT_MEMO_DIR, else ${XDG_CACHE_HOME:-~/.cache}/pixelios/audit. Off: PIXELIOS_AUDIT_NO_MEMO=1|true|yes or any unknown value (warning on stderr; fail safe), or disable(). On: unset, empty, 0|false|no|off (any case, whitespace stripped).
Sys.path is an input: its directories are listed (a new file shadowing a lazily imported module is a miss) and the path list is in the key. A nested run() hands its inputs and flags to the outer recorder.
ponytail: stdlib modules already imported are not recorded as deps (python version is in the key); no cache pruning.
"""
import contextlib, glob, hashlib, io, json, os, pathlib, platform, stat, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SPAWN = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty")
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_STAT, _LSTAT, _ACCESS, _READLINK = os.stat, os.lstat, os.access, os.readlink  # the originals: os.stat/os.lstat/os.access/os.readlink are wrapped while fn runs
_stack = []  # active recorders (dicts), innermost last
_off = False
_quiet = False  # True while the lock / reload code runs: its opens are no input of an outer recorder and a corrupt entry is not reported twice


def disable():
    global _off
    _off = True


_warned = set()


def enabled():
    v = os.environ.get("PIXELIOS_AUDIT_NO_MEMO", "").strip().lower()
    if v not in ("", "0", "false", "no", "off", "1", "true", "yes") and v not in _warned:
        _warned.add(v)
        print(f"audit_memo GAP: unknown PIXELIOS_AUDIT_NO_MEMO={v!r}: memo DISABLED (use 0/false/no/off to keep it on)", file=sys.stderr)
    return not _off and v in ("", "0", "false", "no", "off")


def cache_dir():
    d = os.environ.get("PIXELIOS_AUDIT_MEMO_DIR")
    return pathlib.Path(d) if d else pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache") / "pixelios" / "audit"


_stat_warned = set()


def _stat(kind, name, reason=""):
    """One line per run() into $PIXELIOS_AUDIT_MEMO_STATS (a stats failure never changes an audit result, but warns on stderr once per file)."""
    f = os.environ.get("PIXELIOS_AUDIT_MEMO_STATS")
    if not f:
        return
    try:
        with open(f, "a") as t:
            t.write("\t".join([kind, name] + ([reason] if reason else [])) + "\n")
    except OSError as x:
        if f not in _stat_warned:
            _stat_warned.add(f)
            print(f"audit_memo GAP: cannot write the stats file {f}: {x}: the G5 memo counts are INCOMPLETE (the audit itself is unaffected)", file=sys.stderr)


def _note_list(r, key):
    """Hash the listing when it happens (a file created later in fn must not end up in the stored hash)."""
    if r.get("busy"):  # _list_sha itself fires listdir/glob events
        return
    r["busy"] = True
    try:
        h = _list_sha(*key)
    finally:
        r["busy"] = False
    if r["lists"].setdefault(key, h) != h:
        r["changed"] = True


def _exists(kind, p):
    """State of p as a JSON-able value (compared after a JSON round trip): [S_IFMT, size or None], None if missing; access bool; link target."""
    try:
        if kind == "link":
            return _READLINK(p)
        if kind.startswith("access:"):
            return _ACCESS(p, int(kind[7:]))
        st = (_STAT if kind == "stat" else _LSTAT)(p)
        m = stat.S_IFMT(st.st_mode)
        return [m, st.st_size if m == stat.S_IFREG else None]
    except OSError:   # lint-silent: expected a missing or unstat-able path is recorded as None: the contract of the exist records
        return None


def _note_exist(r, kind, p):
    """Record whether p exists now (taken when fn looks); a later difference means fn saw a moving target."""
    if r.setdefault("exist", {}).setdefault((kind, p), _exists(kind, p)) != _exists(kind, p):
        r["changed"] = True


def _wrap(orig, kind):
    def f(path, *a, **kw):
        if _stack and not _stack[-1].get("busy"):
            r = _stack[-1]
            if isinstance(path, (str, bytes, os.PathLike)) and kw.get("dir_fd") is None:
                _note_exist(r, "lstat" if kind == "lstat" or kw.get("follow_symlinks") is False else "stat", os.path.abspath(os.fsdecode(path)))
            else:
                r["nolist"] = "stat by fd / dir_fd"
        return orig(path, *a, **kw)
    return f


def _wrap_access(orig):
    def f(path, mode, *a, **kw):
        if _stack and not _stack[-1].get("busy"):
            r = _stack[-1]
            if isinstance(path, (str, bytes, os.PathLike)) and not a and kw.get("dir_fd") is None and not kw.get("effective_ids") and kw.get("follow_symlinks", True):
                _note_exist(r, f"access:{int(mode)}", os.path.abspath(os.fsdecode(path)))
            else:
                r["nolist"] = "access by fd / dir_fd / flags"
        return orig(path, mode, *a, **kw)
    return f


def _wrap_readlink(orig):
    def f(path, *a, **kw):
        if _stack and not _stack[-1].get("busy"):
            r = _stack[-1]
            if isinstance(path, (str, bytes, os.PathLike)) and kw.get("dir_fd") is None:
                _note_exist(r, "link", os.path.abspath(os.fsdecode(path)))
            else:
                r["nolist"] = "readlink by dir_fd"
        return orig(path, *a, **kw)
    return f


def _hook(event, args):
    if not _stack or _quiet:
        return
    r = _stack[-1]
    if event in _SPAWN:
        r["spawn"] = event
    elif event in ("pathlib.Path.glob", "pathlib.Path.rglob"):  # existence checks of literal segments are not audited: fail closed
        r["nolist"] = event
    elif event == "glob.glob/2":  # (pathname, recursive, root_dir, dir_fd)
        if args[2] is not None or args[3] is not None:
            r["nolist"] = event
    elif event in ("os.listdir", "os.scandir", "glob.glob"):
        a = args[0] if args else None
        if event == "glob.glob":
            if isinstance(a, (str, os.PathLike)):
                _note_list(r, ("glob", os.path.abspath(os.fspath(a)), bool(args[1])))
            else:
                r["nolist"] = event
        elif a is None or isinstance(a, (str, bytes, os.PathLike)):
            _note_list(r, ("dir", os.path.abspath(os.fsdecode("." if a is None else a)), False))
        else:
            r["nolist"] = event
    elif event == "open":
        path, flags = args[0], args[2]
        if not isinstance(path, (str, bytes, os.PathLike)):
            return  # an fd
        p = os.path.abspath(os.fsdecode(path))
        if isinstance(flags, int) and flags & _WRITE:
            if "__pycache__" not in p:
                r["write"] = p
        else:
            r["opened"].append(p)
            if not r.get("busy"):  # hash at open time (the hash read itself fires an open event)
                r["busy"] = True
                try:
                    d = _dep_path(p)
                    if p.endswith(".pyc") and os.path.isfile(p) and not (os.path.isfile(d) and _pyc_ok(p, d)):  # a missing pyc is only a probe
                        r["nolist"] = "sourceless or stale pyc"  # fail closed
                    if not p.endswith(".pyc"):  # a cold import probes a pyc it then writes; the source hash covers it
                        _note_exist(r, "stat", p)  # a failed open is an input too (the file may appear)
                    if os.path.isfile(d):
                        h = _sha(d)
                        if r["h"].setdefault(d, h) != h:
                            r["changed"] = True  # edited between two opens (even if reverted later): do not store
                except OSError:
                    r["nolist"] = "unhashable input"  # e.g. chmod 000: fn saw a denial we cannot key on
                finally:
                    r["busy"] = False


sys.addaudithook(_hook)


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _rel(p):
    try:
        return pathlib.Path(p).relative_to(ROOT).as_posix()
    except ValueError:
        return p


def _abs(p):
    return str(ROOT / p) if not os.path.isabs(p) else p


def key(fn_name, arg, script):
    h = hashlib.sha256()
    for part in (pathlib.Path(script).read_bytes(), pathlib.Path(__file__).read_bytes(), fn_name.encode(), arg, "\0".join(_rel(os.path.abspath(p or ".")) for p in sys.path).encode(), sys.version.encode(), platform.python_implementation().encode()):
        h.update(len(part).to_bytes(8, "big") + part)
    return h.hexdigest()


def _dep_path(p):
    if p.endswith(".pyc"):  # a valid pyc means the source is never opened: depend on the source instead
        q = pathlib.Path(p)
        if q.parent.name != "__pycache__":
            return p[:-1]  # legacy x.pyc next to x.py
        return str(q.parent.parent / (q.name.split(".")[0] + ".py"))
    return p


def _pyc_ok(pyc, src):
    """True if pyc is a timestamp pyc whose recorded source mtime/size match src and that is newer than src (else fn may have run code we did not hash)."""
    try:
        with open(pyc, "rb") as f:
            h = f.read(16)
        st, pst = _STAT(src), _STAT(pyc)
        return st.st_mtime_ns < pst.st_mtime_ns and len(h) == 16 and h[4:8] == b"\0\0\0\0" and int.from_bytes(h[8:12], "little") == int(st.st_mtime) & 0xFFFFFFFF \
            and int.from_bytes(h[12:16], "little") == st.st_size & 0xFFFFFFFF
    except OSError:   # lint-silent: expected a missing or unreadable .pyc is not a usable .pyc (False): the caller recomputes
        return False


def _list_sha(kind, path, rec):
    try:
        names = os.listdir(path) if kind == "dir" else [_rel(x) for x in glob.glob(path, recursive=rec)]
    except NotADirectoryError:  # a zip/egg file on sys.path: its bytes are its listing
        try:
            names = ["\0file", _sha(path)]
        except OSError:
            names = ["\0missing"]
    except OSError:
        names = ["\0missing"]  # a missing / unreadable directory is a state too (a miss when it appears)
    names = [n for n in names if "__pycache__" not in n]  # a cold import creates it; the pyc is covered by its source's hash
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8", "surrogateescape")).hexdigest()


def _deps(hashes):
    """Open-time hashes -> sorted (rel path, sha); None if any dep changed or vanished while fn ran (TOCTOU: do not store)."""
    try:
        if any(_sha(p) != h for p, h in hashes.items()):
            return None
    except OSError:   # lint-silent: expected a dependency that vanished or cannot be hashed makes the entry stale: a miss
        return None
    return sorted((_rel(p), h) for p, h in hashes.items())


def _exist_list(found):
    """Existence facts -> sorted [kind, rel path, exists]; None if any differs now from when fn looked."""
    if any(_exists(*key) != v for key, v in found.items()):
        return None
    return sorted([k, _rel(p), v] for (k, p), v in found.items())


def _lists(found):
    """Listings fn did -> sorted [kind, rel path, recursive, sha]; None if any listing differs now from when fn took it."""
    if any(_list_sha(*key) != h for key, h in found.items()):
        return None
    return sorted([k, _rel(p), rec, h] for (k, p, rec), h in found.items())


def _merge(o, h, lists, exist, flags=None):
    """Hand an inner run's inputs to the outer recorder o (a conflict = fn saw a moving target)."""
    for src, dst in ((h, o["h"]), (lists, o["lists"]), (exist, o.setdefault("exist", {}))):
        for kk, v in src.items():
            if dst.setdefault(kk, v) != v:
                o["changed"] = True
    for f in ("spawn", "write", "changed", "nolist"):
        if flags and f in flags:
            o[f] = flags[f]


def _sum(e):
    """Digest of every field but "sum": a flipped bit in stdout/ret/deps of a well-formed entry is a miss, not a pass."""
    return hashlib.sha256(json.dumps({f: v for f, v in e.items() if f != "sum"}, sort_keys=True).encode()).hexdigest()


_LOCK_WAIT = 60.0
_FIELDS = ("deps", "lists", "exist", "stdout", "ret")


def _corrupt(path, why):
    """A memo entry that is not a well-formed entry object is a miss (the cold run rewrites it), loudly: rule 8, never an exception that sticks."""
    if _quiet:  # the silent reload after taking the lock: the first load already said it
        return
    print(f"audit_memo GAP: corrupt memo entry {path} ({why}): treated as a miss, the cold run rewrites it", file=sys.stderr)


def _load(path, k):
    try:
        text = path.read_text()
    except OSError:   # lint-silent: expected an absent or unreadable memo entry is a miss: the caller recomputes
        return None  # absent (the normal miss) or unreadable
    try:
        e = json.loads(text)
    except ValueError as x:  # truncated / not JSON / not UTF-8
        _corrupt(path, f"not JSON: {x}")
        return None
    if not isinstance(e, dict):
        _corrupt(path, f"JSON body is {type(e).__name__}, not an object")
        return None
    try:
        if e.get("key") != k or e.get("version") != 3 or e.get("sum") != _sum(e):
            return None
        missing = [f for f in _FIELDS if f not in e]  # run() reads each of these on a hit
        if missing or not isinstance(e["stdout"], str):
            _corrupt(path, f"missing {missing}" if missing else "stdout is not a string")
            return None
        for p, h in e["deps"]:
            if _sha(_abs(p)) != h:
                return None
        for k, p, v in e["exist"]:
            if _exists(k, _abs(p)) != v:
                return None
        for k, p, rec, h in e.get("lists", []):
            if _list_sha(k, _abs(p), rec) != h:
                return None
        return e
    except OSError:   # lint-silent: expected a dependency vanished: the entry is stale, not corrupt (a miss)
        return None  # a dep vanished: stale, not corrupt
    except (ValueError, KeyError, TypeError, AttributeError) as x:  # right key and sum but a missing/odd field (deps, exist, lists, stdout, ret)
        _corrupt(path, f"{type(x).__name__}: {x}")
        return None


@contextlib.contextmanager
def _silent():
    global _quiet
    _quiet = True
    try:
        yield
    finally:
        _quiet = False


def _lock_wait():
    """Seconds a process waits for another one computing the same entry; 0 = no locking. A bad value is loud and means the default."""
    v = os.environ.get("PIXELIOS_AUDIT_MEMO_LOCK_WAIT", "").strip()
    if v:
        try:
            w = float(v)
            if w >= 0:
                return w
        except ValueError:   # lint-silent: expected a non-number falls through to the bad-value warning printed below
            pass
        if ("lockwait", v) not in _warned:
            _warned.add(("lockwait", v))
            print(f"audit_memo GAP: bad PIXELIOS_AUDIT_MEMO_LOCK_WAIT={v!r} (want seconds >= 0): using {_LOCK_WAIT}", file=sys.stderr)
    return _LOCK_WAIT


_LOCK_GRACE = 10.0  # seconds a lock file may stay empty / unreadable before it is a crashed holder's: the holder writes its pid right after the O_EXCL create (review gate-cold-k3 r1 F3a)


def _holder_gone(lock):
    """True if the pid in the lock file is not a live process (same host: the cache is local), or the file is empty / not a pid and older than _LOCK_GRACE (a holder killed between the create and
    the pid write, or ENOSPC on the write, leaves such a file for ever). A young empty file is a holder that is just starting; a lock that is gone is not 'gone holder' (the caller retries the create)."""
    try:
        pid = int(pathlib.Path(lock).read_text().strip())
    except FileNotFoundError:   # lint-silent: expected the lock file is gone: its holder is not stale, the caller retries the create
        return False
    except (OSError, ValueError):
        try:
            return time.time() - os.stat(lock).st_mtime > _LOCK_GRACE
        except OSError:   # lint-silent: expected the lock vanished between the read and the stat: not stale, the caller retries
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:  # PermissionError: alive, another uid   # lint-silent: expected kill(pid, 0) denied: the holder is alive under another uid
        return False
    return False


def _lock_take(f):
    """Own the compute of entry file f: returns the lock path, or None = fail open (compute without owning it; locking off, cache dir unwritable, wait timed out, a stale entry file does not end the wait: only the lock going away does)."""
    wait = _lock_wait()
    if wait <= 0:
        return None
    lock = str(f)[:-5] + ".lock"
    deadline = time.monotonic() + wait
    with _silent():
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
        except OSError:   # lint-silent: expected the cache directory cannot be created: the run computes without the memo (the miss path)
            return None
        while True:
            try:
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:   # lint-silent: expected the lock already exists: another holder, polled by the loop below
                pass
            except OSError:   # lint-silent: expected the lock cannot be created (read-only or full cache dir): the run computes without the memo
                return None
            else:
                try:
                    os.write(fd, f"{os.getpid()}\n".encode())
                except OSError as x:  # ENOSPC etc.: an empty lock nobody could read; remove it and fail open (rule 8: said, never a failure)
                    try:
                        os.unlink(lock)
                    except OSError:   # lint-silent: expected the empty lock is already gone; the write failure itself is printed below
                        pass
                    print(f"audit_memo GAP: cannot write the lock {lock}: {x}: computing without the lock", file=sys.stderr)
                    return None
                finally:
                    os.close(fd)
                return lock
            if _holder_gone(lock):  # a crashed holder; two waiters may both remove it (one removes the new owner's lock): that costs a duplicate compute, never a wrong result
                try:
                    os.unlink(lock)
                    print(f"audit_memo: removed the lock of a dead holder: {lock}", file=sys.stderr)
                except FileNotFoundError:   # lint-silent: expected the stale lock was removed by someone else first
                    pass
                except OSError as x:  # e.g. PermissionError on a dead holder's lock of another uid in a sticky dir: fail open, said
                    print(f"audit_memo GAP: cannot remove the stale lock {lock}: {x}: computing without the lock", file=sys.stderr)
                    return None
                continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


def _lock_drop(lock):
    if lock is not None:
        try:
            os.unlink(lock)
        except OSError:   # lint-silent: expected the lock is already gone at release
            pass


def run(fn, arg=b"", *, script, name=None):
    """Call fn() memoized; see module docstring. Returns fn's (JSON-able) result."""
    name = name or fn.__name__
    if not enabled():
        _stat("cold", name, "off")
        return fn()
    k = key(name, arg, script)
    f = cache_dir() / (k + ".json")
    e = _load(f, k)
    lock = None
    if e is None:
        lock = _lock_take(f)  # waits while another process computes this entry (see the docstring); None = compute it ourselves
        with _silent():
            e = _load(f, k)  # stored by the holder while we waited, or between our miss and our lock
    try:
        if e is not None:
            if _stack:
                _merge(_stack[-1], {_abs(p): h for p, h in e["deps"]}, {(kd, _abs(p), rc): h for kd, p, rc, h in e["lists"]}, {(kd, _abs(p)): v for kd, p, v in e["exist"]})
            _stat("hit", name)
            for line in e["stdout"].splitlines():
                print(f"{line} (memo {k[:16]})")
            return e["ret"]
        return _cold(fn, name, k, f)
    finally:
        _lock_drop(lock)  # after the store (os.replace) or the failure: waiters then find the entry, or take over


def _cold(fn, name, k, f):
    rec = {"opened": [], "h": {}, "lists": {}}
    buf = io.StringIO()
    _stack.append(rec)
    for d in sys.path:  # a new file in any sys.path dir can shadow a module imported lazily by fn
        _note_list(rec, ("dir", os.path.abspath(d or "."), False))
    old = os.stat, os.lstat, os.access, os.readlink
    os.stat, os.lstat, os.access, os.readlink = _wrap(old[0], "stat"), _wrap(old[1], "lstat"), _wrap_access(old[2]), _wrap_readlink(old[3])
    try:
        with contextlib.redirect_stdout(buf):
            ret = fn()
    except BaseException:
        sys.stdout.write(buf.getvalue())  # a FAIL is never stored
        _stat("cold", name, "fail")
        raise
    finally:
        os.stat, os.lstat, os.access, os.readlink = old
        _stack.pop()
        if _stack:
            _merge(_stack[-1], rec["h"], rec["lists"], rec.get("exist", {}), rec)
    sys.stdout.write(buf.getvalue())
    why = next((f"{f}:{rec[f]}" if isinstance(rec[f], str) else f for f in ("spawn", "write", "changed", "nolist") if f in rec), None)
    if why:
        _stat("cold", name, "not-stored:" + why)
        return ret
    deps = _deps(rec["h"])
    if deps is None:
        _stat("cold", name, "not-stored:dep-changed")
        return ret  # an input changed while fn ran: the result may describe old content
    lists = _lists(rec["lists"])
    if lists is None:
        _stat("cold", name, "not-stored:dir-changed")
        return ret  # a directory changed while fn ran
    exist = _exist_list(rec.get("exist", {}))
    if exist is None:
        _stat("cold", name, "not-stored:exist-changed")
        return ret
    try:
        json.dumps(ret)
        entry = {"version": 3, "key": k, "name": name, "deps": deps, "lists": lists, "exist": exist, "stdout": buf.getvalue(), "ret": ret}
        entry["sum"] = _sum(entry)
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as t:
            json.dump(entry, t)
        os.replace(tmp, f)
    except (OSError, TypeError, ValueError):
        _stat("cold", name, "not-stored:write")  # not storable = no cache, never a failure of the audit; one cold line per run (miss only if stored)
    else:
        _stat("cold", name, "miss")
    return ret


def warm(script, name):
    """True if the memo holds a valid entry of NAME (arg b"") for this SCRIPT + memo code + sys.path (a canary: a stale or empty memo = False = fail safe)."""
    if not enabled():
        return False
    k = key(name, b"", script)
    return _load(cache_dir() / (k + ".json"), k) is not None


def summary(stats_file, expect_runs=False):
    """The G5 line of shim/build.sh from the stats file. DISABLED if the memo is off in this environment or any run was cold with reason `off` (every self-test ran uncached);
    STATS UNAVAILABLE if the file cannot be read; STATS EMPTY if expect_runs and no run() was counted (an unwritable stats file or audits that never ran): never `hits=0 cold=0`."""
    try:
        rows = [ln.split("\t") for ln in pathlib.Path(stats_file).read_text().splitlines() if ln]
    except OSError as x:
        return f"memo STATS UNAVAILABLE (cannot read {stats_file}: {x}): hits/cold unknown"
    hits = sum(r[0] == "hit" for r in rows)
    cold = sum(r[0] == "cold" for r in rows)
    off = sum(r[0] == "cold" and len(r) > 2 and r[2] == "off" for r in rows)
    if off or not enabled():
        return f"memo DISABLED (PIXELIOS_AUDIT_NO_MEMO or --no-memo): {off} self-tests ran uncached, hits={hits} cold={cold}"
    if expect_runs and hits + cold == 0:
        return f"memo STATS EMPTY (no self-test counted in {stats_file}: unwritable stats file, or the audits did not run): hits/cold unknown"
    return f"memo hits={hits} cold={cold}"


if __name__ == "__main__":  # shim/build.sh: audit_memo.py --summary STATS_FILE [--expect-runs]
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--summary" and sys.argv[3:] in ([], ["--expect-runs"]):
        print(summary(sys.argv[2], len(sys.argv) == 4))
    else:
        sys.exit("usage: audit_memo.py --summary STATS_FILE [--expect-runs]")
