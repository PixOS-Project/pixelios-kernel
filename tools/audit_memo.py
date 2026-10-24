#!/usr/bin/env python3
"""Content-keyed memo for the mutation self-tests of tools/test-bluejay-boot.py (docs/research/gate-speed.md R2).

run(fn, arg_bytes, script=PATH) calls fn() once per distinct input; a later call with the same input prints the captured stdout
(each line suffixed " (memo <key16>)") and returns the stored result without running fn.

Key = sha256 of: bytes of `script` and of this file (any audit or memo edit invalidates everything), fn name, arg bytes, sys.version, python implementation.
Deps: every file fn opened in the cold run (sys.addaudithook, event `open`), hashed at open time and re-hashed after fn (any change = nothing stored), stored as (path, sha256); a hit re-hashes them all.
Paths under the repo root (parent of tools/) are stored relative to it, so worktrees sharing the cache never verify each other's files.
Never stored: a FAIL (exception), a run that started a process (subprocess/os.system/exec/spawn/fork audit events) or opened a
file for writing outside __pycache__ (fail closed). Corrupt or foreign entries are a miss. Writes are temp file + os.replace.
Cache dir: $PIXELIOS_AUDIT_MEMO_DIR, else ${XDG_CACHE_HOME:-~/.cache}/pixelios/audit. Off: PIXELIOS_AUDIT_NO_MEMO=1|true|yes (any case; anything else keeps the memo on) or disable().
ponytail: stdlib modules already imported are not recorded as deps (python version is in the key); no cache pruning.
"""
import contextlib, hashlib, io, json, os, pathlib, platform, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SPAWN = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty")
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_stack = []  # active recorders (dicts), innermost last
_off = False


def disable():
    global _off
    _off = True


def enabled():
    return not _off and os.environ.get("PIXELIOS_AUDIT_NO_MEMO", "").lower() not in ("1", "true", "yes")


def cache_dir():
    d = os.environ.get("PIXELIOS_AUDIT_MEMO_DIR")
    return pathlib.Path(d) if d else pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache") / "pixelios" / "audit"


def _hook(event, args):
    if not _stack:
        return
    r = _stack[-1]
    if event in _SPAWN:
        r["spawn"] = event
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
        return str(pathlib.Path(p).parent.parent / (p.rsplit("/", 1)[-1].split(".")[0] + ".py"))
    return p


def _deps(hashes):
    """Open-time hashes -> sorted (rel path, sha); None if any dep changed or vanished while fn ran (TOCTOU: do not store)."""
    try:
        if any(_sha(p) != h for p, h in hashes.items()):
            return None
    except OSError:
        return None
    return sorted((_rel(p), h) for p, h in hashes.items())


def _load(path, k):
    try:
        e = json.loads(path.read_text())
        if e.get("key") != k or e.get("version") != 1:
            return None
        for p, h in e["deps"]:
            if _sha(_abs(p)) != h:
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
    rec = {"opened": [], "h": {}}
    buf = io.StringIO()
    _stack.append(rec)
    try:
        with contextlib.redirect_stdout(buf):
            ret = fn()
    except BaseException:
        sys.stdout.write(buf.getvalue())  # a FAIL is never stored
        raise
    finally:
        _stack.pop()
    sys.stdout.write(buf.getvalue())
    if "spawn" in rec or "write" in rec or "changed" in rec:
        return ret
    deps = _deps(rec["h"])
    if deps is None:
        return ret  # an input changed while fn ran: the result may describe old content
    try:
        json.dumps(ret)
        entry = {"version": 1, "key": k, "name": name, "deps": deps, "stdout": buf.getvalue(), "ret": ret}
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as t:
            json.dump(entry, t)
        os.replace(tmp, f)
    except (OSError, TypeError, ValueError):
        pass  # not storable = no cache, never a failure of the audit
    return ret
