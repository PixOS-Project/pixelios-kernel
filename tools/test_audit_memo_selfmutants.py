"""Mutants of tools/audit_memo.py itself (and of the _memo_obj wiring in tools/test-bluejay-boot.py): each must make tools/test_audit_memo.py fail (the memo never turns a failing audit into a pass).
Runs on a throwaway copy in a temp dir (never the repo tree). A mutant whose text is not found is an ERROR (the table went stale), not a skip.
Speed (K3): every mutant runs in its OWN copy dir with its OWN TMPDIR (nothing shared, so the Pool test that counts audit-pool-* dirs in the temp dir is not disturbed), up to GATE_MUTANT_JOBS (default 4, capped at cpu_count-2, at least 1) at a time, and the suite runs with -f (stop at the first failing test: a killed mutant needs one failure; a SURVIVOR still runs every selected test, so nothing is judged by less). The printed result is in table order whatever the finish order."""
import concurrent.futures, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest

TOOLS = pathlib.Path(__file__).resolve().parent
MUTANTS = {  # name: (exact text in audit_memo.py, replacement)
    "key-drops-arg": ("fn_name.encode(), arg,", 'fn_name.encode(), b"",'),
    "key-drops-script-bytes": ("(pathlib.Path(script).read_bytes(), pathlib.Path(__file__)", '(b"", pathlib.Path(__file__)'),
    "key-drops-memo-code": ("pathlib.Path(__file__).read_bytes(), fn_name", 'b"", fn_name'),
    "key-drops-python-version": ("sys.version.encode(), platform", "platform"),  # X11
    "key-drops-fn-name": ("fn_name.encode(), arg,", 'b"", arg,'),
    "hit-skips-dep-rehash": ("if _sha(_abs(p)) != h:", "if False:"),
    "hit-skips-exist-recheck": ("if _exists(k, _abs(p)) != v:", "if False:"),
    "hit-skips-listing-recheck": ("if _list_sha(k, _abs(p), rec) != h:", "if False:"),
    "foreign-key-accepted": ('if e.get("key") != k or', "if"),
    "checksum-ignored": ('or e.get("sum") != _sum(e):', ":"),
    "corrupt-entry-is-a-hit": ('        _corrupt(path, f"not JSON: {x}")\n        return None', '        return {"stdout": "", "ret": 0, "deps": [], "lists": [], "exist": []}'),
    "attributeerror-not-caught": ("except (ValueError, KeyError, TypeError, AttributeError) as x:", "except (ValueError, KeyError, TypeError) as x:"),  # (the isinstance(e, dict) guard alone is not a mutant: its AttributeError is caught by this clause too, same miss)
    "missing-fields-not-checked": ('if missing or not isinstance(e["stdout"], str):', "if False:"),
    "corrupt-entry-is-silent": ('it", file=sys.stderr)', 'it", file=io.StringIO())'),
    "failed-store-counted-as-miss-too": ('    except (OSError, TypeError, ValueError):\n        _stat("cold", name, "not-stored:write")', '    except (OSError, TypeError, ValueError):\n        _stat("cold", name, "miss"); _stat("cold", name, "not-stored:write")'),
    "stats-failure-is-silent": ('the G5 memo counts are INCOMPLETE (the audit itself is unaffected)", file=sys.stderr)', 'the G5 memo counts are INCOMPLETE (the audit itself is unaffected)", file=io.StringIO())'),
    "summary-ignores-off": ("    if off or not enabled():", "    if False:"),
    "summary-ignores-env-disable": ("    if off or not enabled():", "    if off:"),
    "summary-accepts-empty-stats": ("    if expect_runs and hits + cold == 0:", "    if False:"),
    "summary-hides-unreadable-stats": ('        return f"memo STATS UNAVAILABLE', '        return f"memo hits=0 cold=0 STATS UNAVAILABLE'),
    "store-on-exception": ("        _stat(\"cold\", name, \"fail\")\n        raise", "        _stat(\"cold\", name, \"fail\")\n        ret = None"),
    "helper-deps-not-recorded": ("                    if os.path.isfile(d):\n                        h = _sha(d)", "                    if False:\n                        h = _sha(d)"),
    "subprocess-run-is-stored": ("    if event in _SPAWN:\n        r[\"spawn\"] = event", "    if event in _SPAWN:\n        pass"),
    "write-run-is-stored": ('                r["write"] = p', "                pass"),
    "hit-returns-wrong-result": ('return e["ret"]', "return 0"),
    "hit-counted-as-cold": ('_stat("hit", name)', '_stat("cold", name, "miss")'),
    "warm-always-true": ("    return _load(cache_dir() / (k + \".json\"), k) is not None", "    return True"),
    "unknown-no-memo-value-keeps-memo-on": ('and v not in _warned:', "and False:"),
}
BOOT_MUTANTS = {  # same shape, text in test-bluejay-boot.py
    "memo-warm-always-exits-0": ("sys.exit(0 if w else 1)", "sys.exit(0)"),  # X15
    "memo-warm-any-instead-of-all": ("w = all(audit_memo.warm(__file__, n)", "w = any(audit_memo.warm(__file__, n)"),  # X16
    "memo-obj-key-drops-disasm": ("json.dumps([info[0], sorted", 'json.dumps(["", sorted'),
    "memo-obj-key-drops-undefined": ("sorted(info[1]), sorted(info[2])", "[], sorted(info[2])"),
    "memo-obj-key-drops-extra-args": ("info[3], extra], default=sorted", "info[3]], default=sorted"),
    "memo-obj-key-keeps-the-build-dir": ("info = (_obj_text(info[0], obj_path), info[1], info[2], _obj_text(info[3], obj_path))", "pass"),
    "memo-obj-self-test-sees-no-extra-args": ("fn(info, *extra), arg", "fn(info), arg"),
}


# Table sizes: the K3 commit message said "34 new mutants"; the real count was LOCK 13 + POOL 14 + WIRING 4 = 31. The review fixes (gate-cold-k3 r1) add 5 LOCK + 7 POOL + 1 WIRING: LOCK 18 + POOL 21 + WIRING 5 = 44 K3 mutants, next to MUTANTS (28) + BOOT_MUTANTS (7); test_every_mutant_is_killed prints the total.
LOCK_MUTANTS = {  # K3: the per-key lock of audit_memo.run (exact text in audit_memo.py, replacement); judged by the Lock/Race classes only
    "lock-never-taken": ("        lock = _lock_take(f)  # waits", "        lock = None  # waits"),
    "lock-taken-on-a-hit-too": ("    lock = None\n    if e is None:", "    lock = None\n    if True:"),
    "no-reload-after-the-lock": ("        with _silent():\n            e = _load(f, k)", "        with _silent():\n            e = None"),
    "lock-never-dropped": ("        _lock_drop(lock)  # after the store", "        pass  # after the store"),
    "failed-open-wait-owns-the-foreign-lock": ("            if time.monotonic() >= deadline:\n                return None", "            if time.monotonic() >= deadline:\n                return lock"),
    "dead-holder-never-taken-over": ("            if _holder_gone(lock):", "            if False:"),
    "dead-holder-test-inverted": ("    except ProcessLookupError:\n        return True", "    except ProcessLookupError:\n        return False"),
    "unreadable-pid-is-a-dead-holder": ("        try:\n            return time.time() - os.stat(lock).st_mtime > _LOCK_GRACE\n        except OSError:   # lint-silent: expected the lock vanished between the read and the stat: not stale, the caller retries\n            return False", "        return True"),
    "old-unreadable-lock-never-reclaimed": ("os.stat(lock).st_mtime > _LOCK_GRACE", "os.stat(lock).st_mtime > 1e12"),
    "lock-wait-default-is-tiny": ("_LOCK_WAIT = 60.0", "_LOCK_WAIT = 0.01"),
    "stale-lock-unlink-oserror-escapes": ("                except OSError as x:  # e.g. PermissionError", "                except ZeroDivisionError as x:  # e.g. PermissionError"),
    "lock-write-oserror-escapes": ("                except OSError as x:  # ENOSPC etc.", "                except ZeroDivisionError as x:  # ENOSPC etc."),
    "dead-holder-takeover-is-silent": ('print(f"audit_memo: removed the lock of a dead holder: {lock}", file=sys.stderr)', 'print(f"audit_memo: removed the lock of a dead holder: {lock}", file=io.StringIO())'),
    "lock-wait-zero-still-locks": ("    if wait <= 0:\n        return None", "    if wait < 0:\n        return None"),
    "lock-open-is-an-input-of-the-outer-run": ("    with _silent():\n        try:\n            f.parent.mkdir", "    if True:\n        try:\n            f.parent.mkdir"),
    "corrupt-entry-reported-twice": ("    if _quiet:  # the silent reload", "    if False:  # the silent reload"),
    "bad-lock-wait-is-silent": ('using {_LOCK_WAIT}", file=sys.stderr)', 'using {_LOCK_WAIT}", file=io.StringIO())'),
    "quiet-flag-never-reset": ("    finally:\n        _quiet = False", "    finally:\n        pass"),
}
POOL_MUTANTS = {  # K3: tools/audit_pool.py; judged by the Pool/PoolWiring classes only
    "crash-without-result-is-a-pass": ('raise WorkerCrash(f"audit worker for {job.label} died without a result (wait status {job.status}: {_describe(job.status)}); the audit FAILS") from None', 'job.result = {"stdout": "", "err": None}'),
    "worker-failure-swallowed": ('                if it.result["err"]:', "                if False:"),
    "worker-stdout-dropped": ('                self.out.write(it.result["stdout"])', "                pass"),
    "jobs-bound-ignored": ("while self.queue and len(self.running) < self.jobs:", "while self.queue:"),
    "pool-is-serial": ("while self.queue and len(self.running) < self.jobs:", "while self.queue and len(self.running) < 1:"),
    "jobs-one-forks": ("    if jobs <= 1:\n        return body()", "    if jobs < 1:\n        return body()"),
    "abort-does-not-kill": ("                os.kill(job.pid, signal.SIGKILL)", "                pass"),
    "tmp-dir-leaked": ("        shutil.rmtree(self.tmp, ignore_errors=True)", "        pass"),
    "env-accepts-zero-jobs": ("or int(v) < 1:", "or int(v) < 0:"),
    "default-jobs-is-one": ("DEFAULT_JOBS = 4", "DEFAULT_JOBS = 1"),
    "output-in-completion-order-not-program-order": ("                it = self.items[0]\n                if isinstance(it, io.StringIO):", "                it = self.items[-1]\n                if isinstance(it, io.StringIO):"),
    "worker-traceback-not-printed": ('print(f"audit worker traceback ({it.label}):\\n{tb}", file=sys.stderr)', "pass"),
    "reentrant-run-allowed": ('    if _active is not None:\n        raise RuntimeError("audit_pool.run is not reentrant")', "    if False:\n        raise RuntimeError('')"),
    "drain-keeps-pooling": ("        else:\n            self.close()", "        else:\n            pass"),
    "sigterm-count-job-leaves-the-list-before-it-is-finished": ('                self._finish(it)\n                self.out.write(it.result["stdout"])\n                self.items.pop(0)', '                self.items.pop(0)\n                self._finish(it)\n                self.out.write(it.result["stdout"])'),
    "sigterm-handler-not-installed": ("        old_term = signal.signal(signal.SIGTERM, s._on_term)", "        old_term = None"),
    "worker-keeps-the-parent-sigterm-handler": ("        signal.signal(signal.SIGTERM, signal.SIG_DFL)  # the parent's handler", "        pass  # the parent's handler"),
    "sigterm-leaves-the-workers-running": ("        finally:\n            self.abort()\n            signal.signal(signal.SIGTERM, signal.SIG_DFL)", "        finally:\n            signal.signal(signal.SIGTERM, signal.SIG_DFL)"),
    "sigterm-writes-no-segments": ("                    if shown:\n                        self.out.write(it.getvalue())", "                    if shown:\n                        pass"),
    "sigterm-names-no-cut-job": ("{', '.join(cut) or '-'}", "-"),
    "sigterm-survives-the-handler": ("            signal.signal(signal.SIGTERM, signal.SIG_DFL)\n            os.kill(os.getpid(), signal.SIGTERM)", "            signal.signal(signal.SIGTERM, signal.SIG_DFL)"),
}
WIRING_MUTANTS = {  # K3: the wiring in test-bluejay-boot.py
    "memo-key-drops-arg": ("audit_memo.run(lambda: fn(*args), arg, script=__file__", 'audit_memo.run(lambda: fn(*args), b"", script=__file__'),  # review gate-cold-k3 r1 F6(i)
    "memo-not-dispatched-to-the-pool": ("return audit_pool.dispatch(lambda: audit_memo.run(lambda: fn(*args), arg, script=__file__, name=fn.__name__), fn.__name__)", "return audit_memo.run(lambda: fn(*args), arg, script=__file__, name=fn.__name__)"),
    "memo-obj-not-dispatched-to-the-pool": ("return audit_pool.dispatch(lambda: audit_memo.run(lambda: fn(info, *extra), arg, script=__file__, name=fn.__name__), fn.__name__)", "return audit_memo.run(lambda: fn(info, *extra), arg, script=__file__, name=fn.__name__)"),
    "main-audit-not-in-the-pool": ("audit_pool.run(lambda: _audit_main(args), audit_pool.jobs_from_env())", "_audit_main(args)"),
    "no-drain-before-the-host-tests": ("    audit_pool.drain()  # the self-tests above", "    pass  # the self-tests above"),
}



def jobs():
    """Parallel mutant runs: min(GATE_MUTANT_JOBS or 4, cpu_count - 2), at least 1. A bad value is loud, not ignored (rule 8)."""
    raw = os.environ.get("GATE_MUTANT_JOBS", "4")
    try:
        want = int(raw)
        if want < 1: raise ValueError
    except ValueError:
        print(f"SELFMUT GAP: bad GATE_MUTANT_JOBS={raw!r}, using 4", file=sys.stderr); want = 4
    return max(1, min(want, (os.cpu_count() or 4) - 2))


# (table, file the mutant edits, test classes that judge it; () = the whole suite)
TABLES = ((MUTANTS, "audit_memo.py", ()), (BOOT_MUTANTS, "test-bluejay-boot.py", ()),
          (LOCK_MUTANTS, "audit_memo.py", ("Lock", "Race")), (POOL_MUTANTS, "audit_pool.py", ("Pool", "PoolWiring", "Race")),
          (WIRING_MUTANTS, "test-bluejay-boot.py", ("PoolWiring",)))
FILES = ("audit_memo.py", "audit_pool.py", "audit_wlanh1.py", "gpuclk_audit.py", "mifclk_audit.py", "pmicsrc_audit.py", "test_audit_memo.py", "test-bluejay-boot.py")


def run_one(fname, text, only):
    """One suite run in a private copy of the tools files (text replaces fname; None = the control). Returns (returncode, stderr tail, seconds)."""
    t0 = time.monotonic()
    root = pathlib.Path(tempfile.mkdtemp(prefix="selfmut-"))
    try:
        w = root / "tools"; w.mkdir(); (root / "shim").mkdir(); (root / "tmp").mkdir()        # the suite reads ../shim/build.sh (G5 call shape)
        shutil.copy(TOOLS.parent / "shim" / "build.sh", root / "shim" / "build.sh")
        for f in FILES:
            shutil.copy(TOOLS / f, w / f)
        if text is not None:
            (w / fname).write_text(text)
        r = subprocess.run([sys.executable, "-I", str(w / "test_audit_memo.py"), "-f", *only], capture_output=True, text=True, timeout=300, cwd=w,
                           env={**os.environ, "TMPDIR": str(root / "tmp")})
        return r.returncode, r.stderr[-800:], time.monotonic() - t0
    finally:
        shutil.rmtree(root, ignore_errors=True)


class SelfMutants(unittest.TestCase):
    def test_control_passes(self):
        rc, err, _ = run_one("audit_memo.py", None, ())
        self.assertEqual(rc, 0, err)

    def test_the_split_files_cover_every_table(self):
        """T-2712: the kill test runs in four files (test_audit_memo_selfmutants_{memo,boot,lock,pool}.py) so no file is above ~60 s; together they must name every table."""
        self.assertEqual(sorted(i for g in SPLIT.values() for i in g), list(range(len(TABLES))))


# T-2712 split (PLAN-GATE-LAYERS L1: no unit file above 60 s): table indexes into TABLES per split file; kill_tables(case, name) is the old test_every_mutant_is_killed body
SPLIT = {"memo": (0,), "boot": (1, 4), "lock": (2,), "pool": (3,)}


def kill_tables(case, name):
    work = []                                                    # (name, fname, mutated text, only), in table order
    for table, fname, only in (TABLES[i] for i in SPLIT[name]):
        orig = (TOOLS / fname).read_text()
        for mname, (a, b) in table.items():
            case.assertEqual(orig.count(a), 1, f"mutant {mname}: text found {orig.count(a)} times (stale table)")
            work.append((mname, fname, orig.replace(a, b), only))
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs()) as ex:    # subprocess-bound: threads are enough
        results = list(ex.map(lambda w: run_one(w[1], w[2], w[3]), work))      # map keeps the table order
    survived = [w[0] for w, r in zip(work, results) if r[0] == 0]
    slow = max(zip(work, results), key=lambda x: x[1][2])
    total = len(work)
    print(f"audit_memo self-mutants[{name}]: killed={total - len(survived)}/{total} (jobs={jobs()}, slowest {slow[0][0]} {slow[1][2]:.1f}s)")
    case.assertEqual(survived, [], f"SURVIVED: {survived}")


if __name__ == "__main__":
    unittest.main()
