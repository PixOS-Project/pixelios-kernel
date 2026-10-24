#!/usr/bin/env python3
"""Content-keyed memo for the mutation self-tests of tools/test-bluejay-boot.py (docs/research/gate-speed.md R2).

run(fn, arg_bytes, script=PATH) calls fn() once per distinct input; a later call with the same input prints the captured stdout
(each line suffixed " (memo <key16>)") and returns the stored result without running fn.

Key = sha256 of: bytes of `script` and of this file (any audit or memo edit invalidates everything), fn name, arg bytes, sys.version, python implementation.
Deps: every file fn opened in the cold run (sys.addaudithook, event `open`), hashed at open time and re-hashed after fn (any change = nothing stored), stored as (path, sha256); a hit re-hashes them all.
Paths under the repo root (parent of tools/) are stored relative to it, so worktrees sharing the cache never verify each other's files.
Never stored: a .pyc without its source or whose recorded mtime/size differ from it (a same-size edit in the same mtime second cannot be told apart, Python accepts it too), a FAIL (exception), a run that started a process (subprocess/os.system/exec/spawn/fork audit events) or opened a
file for writing outside __pycache__ (fail closed).
Directory listings (os.listdir/os.scandir) and glob.glob calls are inputs too: stored as (kind, path, sha256 of the sorted names, taken when fn listed it; re-checked after fn, a difference = nothing stored) and re-listed on a hit;
a listing by fd or with root_dir/dir_fd, and pathlib glob/rglob, is not storable. Failed opens and os.stat/os.lstat calls (os.path.exists/isfile/isdir, Path.exists) are inputs: stored as (kind, path, exists) and re-checked on a hit (stat by fd/dir_fd is not storable; importlib's own probes use the posix module and are not seen). Corrupt or foreign entries are a miss. Writes are temp file + os.replace.
Cache dir: $PIXELIOS_AUDIT_MEMO_DIR, else ${XDG_CACHE_HOME:-~/.cache}/pixelios/audit. Off: PIXELIOS_AUDIT_NO_MEMO=1|true|yes or any unknown value (warning on stderr; fail safe), or disable(). On: unset, empty, 0|false|no|off (any case, whitespace stripped).
ponytail: stdlib modules already imported are not recorded as deps (python version is in the key); no cache pruning.
"""
import contextlib, glob, hashlib, io, json, os, pathlib, platform, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SPAWN = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty")
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_STAT, _LSTAT = os.stat, os.lstat  # the originals: os.stat/os.lstat are wrapped while fn runs
_stack = []  # active recorders (dicts), innermost last
_off = False


def disable():
    global _off
    _off = True


_warned = set()


def enabled():
    v = os.environ.get("PIXELIOS_AUDIT_NO_MEMO", "").strip().lower()
    if v not in ("", "0", "false", "no", "off", "1", "true", "yes") and v not in _warned:
        _warned.add(v)
        print(f"audit_memo: unknown PIXELIOS_AUDIT_NO_MEMO={v!r}: memo DISABLED (use 0/false/no/off to keep it on)", file=sys.stderr)
    return not _off and v in ("", "0", "false", "no", "off")


def cache_dir():
    d = os.environ.get("PIXELIOS_AUDIT_MEMO_DIR")
    return pathlib.Path(d) if d else pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache") / "pixelios" / "audit"


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
    try:
        (_STAT if kind == "stat" else _LSTAT)(p)
        return True
    except OSError:
        return False


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


def _hook(event, args):
    if not _stack:
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
                    if p.endswith(".pyc") and not (os.path.isfile(d) and _pyc_ok(p, d)):
                        r["nolist"] = "sourceless or stale pyc"  # fail closed
                    _note_exist(r, "stat", p)  # a failed open is an input too (the file may appear)
                    if os.path.isfile(d):
                        h = _sha(d)
                        if r["h"].setdefault(d, h) != h:
                            r["changed"] = True  # edited between two opens (even if reverted later): do not store
                except OSError:
                    pass
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
    for part in (pathlib.Path(script).read_bytes(), pathlib.Path(__file__).read_bytes(), fn_name.encode(), arg, sys.version.encode(), platform.python_implementation().encode()):
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
    """True if pyc is a timestamp pyc whose recorded source mtime/size match src (else fn ran code we did not hash)."""
    try:
        with open(pyc, "rb") as f:
            h = f.read(16)
        st = _STAT(src)
        return len(h) == 16 and h[4:8] == b"\0\0\0\0" and int.from_bytes(h[8:12], "little") == int(st.st_mtime) & 0xFFFFFFFF \
            and int.from_bytes(h[12:16], "little") == st.st_size & 0xFFFFFFFF
    except OSError:
        return False


def _list_sha(kind, path, rec):
    try:
        names = os.listdir(path) if kind == "dir" else [_rel(x) for x in glob.glob(path, recursive=rec)]
    except OSError:
        names = ["\0missing"]  # a missing / unreadable directory is a state too (a miss when it appears)
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8", "surrogateescape")).hexdigest()


def _deps(hashes):
    """Open-time hashes -> sorted (rel path, sha); None if any dep changed or vanished while fn ran (TOCTOU: do not store)."""
    try:
        if any(_sha(p) != h for p, h in hashes.items()):
            return None
    except OSError:
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


def _load(path, k):
    try:
        e = json.loads(path.read_text())
        if e.get("key") != k or e.get("version") != 2:
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
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run(fn, arg=b"", *, script, name=None):
    """Call fn() memoized; see module docstring. Returns fn's (JSON-able) result."""
    name = name or fn.__name__
    if not enabled():
        return fn()
    k = key(name, arg, script)
    f = cache_dir() / (k + ".json")
    e = _load(f, k)
    if e is not None:
        for line in e["stdout"].splitlines():
            print(f"{line} (memo {k[:16]})")
        return e["ret"]
    rec = {"opened": [], "h": {}, "lists": {}}
    buf = io.StringIO()
    _stack.append(rec)
    old_stat, old_lstat = os.stat, os.lstat
    os.stat, os.lstat = _wrap(old_stat, "stat"), _wrap(old_lstat, "lstat")
    try:
        with contextlib.redirect_stdout(buf):
            ret = fn()
    except BaseException:
        sys.stdout.write(buf.getvalue())  # a FAIL is never stored
        raise
    finally:
        os.stat, os.lstat = old_stat, old_lstat
        _stack.pop()
    sys.stdout.write(buf.getvalue())
    if "spawn" in rec or "write" in rec or "changed" in rec or "nolist" in rec:
        return ret
    deps = _deps(rec["h"])
    if deps is None:
        return ret  # an input changed while fn ran: the result may describe old content
    lists = _lists(rec["lists"])
    if lists is None:
        return ret  # a directory changed while fn ran
    exist = _exist_list(rec.get("exist", {}))
    if exist is None:
        return ret
    try:
        json.dumps(ret)
        entry = {"version": 2, "key": k, "name": name, "deps": deps, "lists": lists, "exist": exist, "stdout": buf.getvalue(), "ret": ret}
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as t:
            json.dump(entry, t)
        os.replace(tmp, f)
    except (OSError, TypeError, ValueError):
        pass  # not storable = no cache, never a failure of the audit
    return ret
