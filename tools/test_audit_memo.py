"""tools/audit_memo.py: hit, miss on each key part, dep tracking, FAIL and subprocess runs not stored, corrupt entry, replay output."""
import contextlib, glob, importlib.util, io, json, os, pathlib, re, shutil, signal, subprocess, sys, tempfile, textwrap, time, unittest
import unittest.mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import audit_memo as am
import audit_pool as ap


class Memo(unittest.TestCase):
    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.t))
        self.script = self.t / "script.py"; self.script.write_text("v1")
        self.dep = self.t / "dep.txt"; self.dep.write_text("d1")
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "cache")
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO", None)
        self.calls = 0
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_DIR", None)

    def fn(self):
        self.calls += 1
        print("line one")
        print("PASS " + self.dep.read_text())
        return 7

    def go(self, fn=None, arg=b"", name="fn"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r = am.run(fn or self.fn, arg, script=self.script, name=name)
        return r, out.getvalue()

    def test_hit_replays_output(self):
        r1, o1 = self.go(); r2, o2 = self.go()
        self.assertEqual((self.calls, r1, r2), (1, 7, 7))
        self.assertEqual(o1, "line one\nPASS d1\n")
        k = am.key("fn", b"", self.script)[:16]
        self.assertEqual(o2, f"line one (memo {k})\nPASS d1 (memo {k})\n")

    def test_miss_on_each_key_part_and_dep(self):
        self.go()
        self.script.write_text("v2"); self.go()       # script bytes
        self.go(name="other")                          # function name
        self.go(arg=b"x")                              # argument
        self.assertEqual(self.calls, 4)
        self.go(); self.assertEqual(self.calls, 4)     # v2 entry hits
        self.dep.write_text("d2"); _, o = self.go()    # recorded dep changed
        self.assertEqual((self.calls, o), (5, "line one\nPASS d2\n"))

    def test_python_version_is_in_the_key(self):                    # r1 F2 / X11: a memo shared by two pythons must not serve one's verdict to the other
        other = sys.version.replace(sys.version.split()[0], "3.99.0", 1)
        self.assertNotEqual(other, sys.version)
        self.go(); self.go(); self.assertEqual(self.calls, 1)
        real = sys.version
        sys.version = other
        self.addCleanup(setattr, sys, "version", real)
        try:
            self.go(); self.assertEqual(self.calls, 2)              # miss under another version string, stored under its own key
            self.assertTrue(am.warm(self.script, "fn")); self.go(); self.assertEqual(self.calls, 2)
        finally:
            sys.version = real
        self.go(); self.assertEqual(self.calls, 2)                  # the first version's entry was not evicted
        self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 2)

    def test_fail_not_stored(self):
        def bad():
            self.calls += 1; print("partial"); raise AssertionError("no")
        for _ in range(2):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(AssertionError):
                am.run(bad, script=self.script, name="bad")
        self.assertEqual(self.calls, 2)
        self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_subprocess_and_write_not_stored(self):
        def spawn():
            self.calls += 1; subprocess.run(["true"]); return 1
        def writer():
            self.calls += 1; (self.t / "w.txt").write_text("x"); return 1
        for f in (spawn, writer):
            self.go(f, name=f.__name__); self.go(f, name=f.__name__)
        self.assertEqual(self.calls, 4)
        self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_corrupt_entry_is_a_miss(self):
        self.go()
        (e,) = (self.t / "cache").glob("*.json"); e.write_text("{not json")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.go(); self.assertEqual(self.calls, 2)
        self.assertIn("corrupt memo entry", err.getvalue())
        self.go(); self.assertEqual(self.calls, 2)     # rewritten

    def test_dep_edited_during_run_not_stored(self):
        new = self.t / "dep.new"; new.write_text("d2")   # created before the run: the edit below is a rename, not an open
        def edit_mid_run():
            self.calls += 1; v = self.dep.read_text(); os.replace(new, self.dep); print("PASS " + v); return 1
        self.go(edit_mid_run, name="e")
        self.assertFalse(list((self.t / "cache").glob("*.json")))
        _, o = self.go(name="e"); self.assertEqual((self.calls, o), (2, "line one\nPASS d2\n"))   # recomputed, not replayed

    def test_dep_edited_and_reverted_between_opens_not_stored(self):
        # the editor is a process started before run() (git stash/pop during a gate); fn only talks to it over pipes
        ed = subprocess.Popen([sys.executable, "-c", "import sys\nfor l in sys.stdin:\n    open(sys.argv[1], 'w').write(l.strip()); print('ok', flush=True)", str(self.dep)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (ed.stdin.close(), ed.wait()))
        def edit(v):
            ed.stdin.write(v + "\n"); ed.stdin.flush(); ed.stdout.readline()
        def aba():
            self.calls += 1
            a = self.dep.read_text(); edit("d2"); b = self.dep.read_text(); edit("d1")
            return a + b
        self.assertEqual(self.go(aba, name="aba")[0], "d1d2")
        self.go(aba, name="aba")
        self.assertEqual(self.calls, 2)
        self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_listing_and_glob_are_inputs(self):
        def ls():
            self.calls += 1
            return [sorted(os.listdir(self.t / "d")), sorted(pathlib.Path(x).name for x in __import__("glob").glob(str(self.t / "d" / "*.txt")))]
        (self.t / "d").mkdir()
        r1, _ = self.go(ls, name="ls"); self.go(ls, name="ls")
        self.assertEqual(self.calls, 1)                    # unchanged listing: hit
        (self.t / "d" / "new.txt").write_text("x")               # a new file changes both the listing and the glob
        r2, _ = self.go(ls, name="ls")
        self.assertEqual(self.calls, 2)
        self.assertIn("new.txt", r2[1]); self.assertNotIn("new.txt", r1[1])
        (self.t / "d" / "new.dat").write_text("x")               # changes only the os.listdir part
        self.go(ls, name="ls"); self.assertEqual(self.calls, 3)

    def test_dir_changed_after_listing_not_stored(self):
        (self.t / "d").mkdir(); (self.t / "late.src").write_text("x")   # made before the run: a rename is no open event, so only the listing check can see it
        def ls():
            self.calls += 1
            r = sorted(os.listdir(self.t / "d")); os.replace(self.t / "late.src", self.t / "d" / "late"); return r
        self.go(ls, name="late")
        os.replace(self.t / "d" / "late", self.t / "late.src"); self.go(ls, name="late")
        self.assertEqual(self.calls, 2)
        self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_pathlib_glob_not_stored(self):
        (self.t / "d" / "a").mkdir(parents=True)
        def g():
            self.calls += 1
            return [str(x) for x in (self.t / "d").glob("*/board.json")] + [str(x) for x in (self.t / "d").rglob("board.json")]
        self.go(g, name="pg"); self.go(g, name="pg")
        self.assertEqual(self.calls, 2)
        self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_missing_file_is_an_input(self):
        opt = self.t / "opt.json"
        def probe():
            self.calls += 1
            a = os.path.exists(opt)
            try:
                b = opt.read_text()
            except FileNotFoundError:
                b = "none"
            return [a, b, opt.is_file()]
        self.assertEqual(self.go(probe, name="pr")[0], [False, "none", False])
        self.go(probe, name="pr"); self.assertEqual(self.calls, 1)      # still absent: hit
        opt.write_text("v")
        self.assertEqual(self.go(probe, name="pr")[0], [True, "v", True]); self.assertEqual(self.calls, 2)
        self.go(probe, name="pr"); self.assertEqual(self.calls, 2)       # present state is stored too
        opt.unlink()
        self.assertEqual(self.go(probe, name="pr")[0], [False, "none", False]); self.assertEqual(self.calls, 3)

    def test_cold_import_is_stored(self):
        (self.t / "m").mkdir(); m = self.t / "m" / "coldmod.py"; m.write_text("X = 1\n")      # no __pycache__ yet: the import probes a missing pyc, then writes it
        def imp():
            self.calls += 1
            sys.path.insert(0, str(self.t / "m"))
            try:
                import importlib; importlib.invalidate_caches(); return importlib.import_module("coldmod").X
            finally:
                sys.path.remove(str(self.t / "m")); sys.modules.pop("coldmod", None)
        self.go(imp, name="cold"); self.go(imp, name="cold")
        self.assertEqual(self.calls, 1)

    def test_stat_wrappers_restored(self):
        a, b, c, d = os.stat, os.lstat, os.access, os.readlink
        self.go()
        self.assertIs(os.stat, a); self.assertIs(os.lstat, b); self.assertIs(os.access, c); self.assertIs(os.readlink, d)
        def boom():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            self.go(boom, name="boom")
        self.assertIs(os.stat, a); self.assertIs(os.lstat, b); self.assertIs(os.access, c); self.assertIs(os.readlink, d)

    def test_missing_dir_listing_is_an_input(self):
        def g():
            self.calls += 1
            return sorted(__import__("glob").glob(str(self.t / "nodir" / "*")))
        self.go(g, name="g"); self.go(g, name="g"); self.assertEqual(self.calls, 1)
        (self.t / "nodir").mkdir(); (self.t / "nodir" / "f").write_text("x")
        r, _ = self.go(g, name="g"); self.assertEqual((self.calls, len(r)), (2, 1))

    def test_listing_by_fd_not_stored(self):
        def lsfd():
            self.calls += 1
            fd = os.open(self.t, os.O_RDONLY)
            try:
                return sorted(os.listdir(fd))
            finally:
                os.close(fd)
        self.go(lsfd, name="lsfd"); self.go(lsfd, name="lsfd")
        self.assertEqual(self.calls, 2)

    def test_key_includes_memo_code(self):
        a = am.key("fn", b"", self.script)
        other = self.t / "memo_copy.py"; other.write_text("x")
        old = am.__file__; am.__file__ = str(other)
        self.addCleanup(setattr, am, "__file__", old)
        self.assertNotEqual(a, am.key("fn", b"", self.script))

    def test_worktrees_share_hits_by_relative_path(self):
        import json, shutil
        def worktree(name, text):
            r = self.t / name; (r / "shim").mkdir(parents=True); (r / "shim/x.c").write_text(text); return r
        a, b, c = worktree("A", "same"), worktree("B", "same"), worktree("C", "other")
        old = am.ROOT; self.addCleanup(setattr, am, "ROOT", old)
        def run_in(root):
            am.ROOT = root
            def fn():
                self.calls += 1; return (root / "shim/x.c").read_text()
            return self.go(fn, name="wt")[0]
        self.assertEqual(run_in(a), "same")
        (e,) = (self.t / "cache").glob("*.json")
        self.assertEqual([d[0] for d in json.loads(e.read_text())["deps"]], ["shim/x.c"])   # relative, not under A
        self.assertEqual((run_in(b), self.calls), ("same", 1))    # identical content in another root: hit
        self.assertEqual((run_in(c), self.calls), ("other", 2))   # same relative path, other content: miss

    def test_pyc_dep_maps_to_source(self):
        self.assertEqual(am._dep_path("/r/tools/__pycache__/boards.cpython-313.pyc"), "/r/tools/boards.py")
        self.assertEqual(am._dep_path("/r/tools/x.py"), "/r/tools/x.py")
        # end to end: a function importing a module from a warm __pycache__ stores the .py, never the .pyc
        m = self.t / "pycmod.py"; m.write_text("X = 1\n")
        os.utime(m, (1_700_000_000, 1_700_000_000))   # the pyc must be strictly newer than its source (round-5 F4)
        import importlib.util, py_compile; py_compile.compile(str(m), cfile=importlib.util.cache_from_source(str(m)))
        def imp():
            self.calls += 1
            sys.path.insert(0, str(self.t))
            try:
                import importlib; importlib.invalidate_caches(); return importlib.import_module("pycmod").X
            finally:
                sys.path.remove(str(self.t)); sys.modules.pop("pycmod", None)
        self.go(imp, name="imp")
        deps = [d for e in (self.t / "cache").glob("*.json") for d, _ in __import__("json").loads(e.read_text())["deps"]]
        self.assertTrue(deps and not any(d.endswith(".pyc") for d in deps), deps)

    def test_sourceless_or_stale_pyc_not_stored(self):
        import importlib.util, py_compile
        def reads(path):
            def f():
                self.calls += 1; return len(pathlib.Path(path).read_bytes())
            return f
        m = self.t / "stale.py"; m.write_text("X = 1\n"); os.utime(m, (1_700_000_000, 1_700_000_000))
        c = importlib.util.cache_from_source(str(m)); py_compile.compile(str(m), cfile=c)
        self.go(reads(c), name="ok"); self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 1)   # fresh pyc: stored
        m.write_text("X = 22222\n")                                   # stale: different size
        self.go(reads(c), name="stale"); self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 1)
        n = self.t / "gone.py"; n.write_text("Y = 1\n"); c2 = importlib.util.cache_from_source(str(n)); py_compile.compile(str(n), cfile=c2); n.unlink()
        self.go(reads(c2), name="gone"); self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 1)   # no source
        # round-5 F4: same size and same mtime second, but the source was written after the pyc: possibly stale, not stored
        q = self.t / "newer.py"; q.write_text("X = 1\n"); os.utime(q, (1_700_000_000, 1_700_000_000))
        c3 = importlib.util.cache_from_source(str(q)); py_compile.compile(str(q), cfile=c3)
        q.write_text("X = 2\n"); os.utime(q, (1_700_000_000, 1_700_000_000)); os.utime(c3, (1_700_000_000, 1_700_000_000))   # same mtime: ns equal
        self.assertFalse(am._pyc_ok(c3, str(q)))
        os.utime(c3, ns=(1_700_000_000_000_000_000 + 10**9,) * 2); self.assertTrue(am._pyc_ok(c3, str(q)))
        os.utime(c3, ns=(1_700_000_000_000_000_000 + 1,) * 2); self.assertTrue(am._pyc_ok(c3, str(q)))
        os.utime(c3, ns=(1_700_000_000_000_000_000,) * 2); n0 = len(list((self.t / "cache").glob("*.json")))
        self.go(reads(c3), name="newer"); self.assertEqual(len(list((self.t / "cache").glob("*.json"))), n0)
        legacy = self.t / "old.pyc"; py_compile.compile(str(m), cfile=str(legacy)); m.unlink()
        self.go(reads(str(legacy)), name="legacy"); self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 1)
        self.assertEqual(am._dep_path(str(legacy)), str(self.t / "old.py"))

    # ---- round-5 F1-F5 ----
    def stored(self):
        return len(list((self.t / "cache").glob("*.json")))

    def test_new_file_earlier_on_sys_path_is_a_miss(self):          # F1
        A, B = self.t / "A", self.t / "B"; A.mkdir(); B.mkdir(); (B / "shadowme.py").write_text("X = 'b'\n")
        def imp():
            self.calls += 1
            try:   # no invalidate_caches: like the real lazy `import copy`, the finders are already filled
                return __import__("importlib").import_module("shadowme").X
            finally:
                sys.modules.pop("shadowme", None)
        sys.path[:0] = [str(A), str(B)]; self.addCleanup(lambda: sys.path.__delitem__(slice(0, 2)))
        old_dwb = sys.dont_write_bytecode; sys.dont_write_bytecode = True; self.addCleanup(setattr, sys, "dont_write_bytecode", old_dwb)   # the pre-fill pyc would get the same mtime_ns tick as shadowme.py; _pyc_ok (<) then refuses it (nolist) and the warm run misses
        importlib = __import__("importlib"); importlib.invalidate_caches(); importlib.import_module("shadowme"); sys.modules.pop("shadowme")   # fill the FileFinders before the recorder starts
        self.assertEqual(self.go(imp, name="sh")[0], "b")
        k0 = am.key("sh", b"", self.script)
        n = self.calls; self.go(imp, name="sh"); self.assertEqual(self.calls, n)       # warm: hit
        (A / "shadowme.py").write_text("X = 'a'\n"); os.utime(A, (2_000_000_000, 2_000_000_000))   # FileFinder compares dir mtimes: make the change visible to it
        self.assertEqual(self.go(imp, name="sh")[0], "a"); self.assertEqual(self.calls, n + 1)
        sys.path.insert(0, str(self.t))
        self.assertNotEqual(k0, am.key("sh", b"", self.script)); sys.path.pop(0)    # sys.path is in the key

    def test_nested_run_hands_inputs_to_outer(self):               # F2
        (self.t / "g" / "a").mkdir(parents=True)
        def inner():
            return [str(x) for x in (self.t / "g").glob("*/board.json")]
        def outer():
            self.calls += 1
            return am.run(inner, script=self.script, name="inner")
        self.assertEqual(self.go(outer, name="outer")[0], []); self.assertEqual(self.stored(), 0)   # inner nolist reached the outer
        # inner hit path: its listing and dep reach the outer recorder
        (self.t / "d").mkdir()
        def inner2():
            return sorted(os.listdir(self.t / "d")) + [self.dep.read_text(), os.path.exists(self.t / "nofile")]
        def outer2():
            self.calls += 1
            return am.run(inner2, script=self.script, name="inner2")
        self.go(inner2, name="x"); am.run(inner2, script=self.script, name="inner2")    # stores inner2
        self.go(outer2, name="outer2"); n = self.calls; self.go(outer2, name="outer2"); self.assertEqual(self.calls, n)   # outer stored too: hit
        (self.t / "nofile").write_text("x")      # first: only the exist fact merged from the inner HIT can see this (_load uses the original stat)
        r, _ = self.go(outer2, name="outer2"); self.assertEqual((self.calls, r[-1]), (n + 1, True))
        (self.t / "d" / "new").write_text("x")
        r, _ = self.go(outer2, name="outer2"); self.assertEqual((self.calls, r[0]), (n + 2, "new"))
        self.dep.write_text("d9")
        r, _ = self.go(outer2, name="outer2"); self.assertEqual((self.calls, r[1]), (n + 3, "d9"))

    def test_nested_conflict_sets_changed(self):
        o = {"h": {"p": "1"}, "lists": {}, "exist": {}}
        am._merge(o, {"p": "2"}, {}, {}); self.assertTrue(o.get("changed"))
        o = {"h": {}, "lists": {}}
        am._merge(o, {}, {}, {}, {"spawn": "x"}); self.assertEqual(o["spawn"], "x"); self.assertNotIn("changed", o)

    def fn_probe(self, f):
        def fn():
            self.calls += 1
            return f()
        return fn

    def test_isdir_dir_replaced_by_file_is_a_miss(self):           # F3
        p = self.t / "x"; p.mkdir()
        fn = self.fn_probe(lambda: os.path.isdir(p))
        self.assertTrue(self.go(fn, name="k")[0]); self.go(fn, name="k"); self.assertEqual(self.calls, 1)
        p.rmdir(); p.write_text("f")
        self.assertFalse(self.go(fn, name="k")[0]); self.assertEqual(self.calls, 2)

    def test_getsize_resize_is_a_miss(self):
        p = self.t / "s"; p.write_text("ab")
        fn = self.fn_probe(lambda: os.path.getsize(p))
        self.assertEqual(self.go(fn, name="k")[0], 2); self.go(fn, name="k"); self.assertEqual(self.calls, 1)
        p.write_text("1234567"); self.assertEqual(self.go(fn, name="k")[0], 7); self.assertEqual(self.calls, 2)

    def test_access_is_an_input(self):
        p = self.t / "acc"
        fn = self.fn_probe(lambda: os.access(p, os.F_OK))
        self.assertFalse(self.go(fn, name="k")[0]); self.go(fn, name="k"); self.assertEqual(self.calls, 1)
        p.write_text("x"); self.assertTrue(self.go(fn, name="k")[0]); self.assertEqual(self.calls, 2)
        fd_fn = self.fn_probe(lambda: os.access(p, os.F_OK, effective_ids=True))
        self.go(fd_fn, name="e"); self.go(fd_fn, name="e"); self.assertEqual(self.calls, 4)   # variants are not storable
        os.chmod(p, 0)
        if os.geteuid() != 0:
            ro = self.fn_probe(lambda: os.access(p, os.R_OK)); self.assertFalse(self.go(ro, name="r")[0])
            os.chmod(p, 0o644); self.assertTrue(self.go(ro, name="r")[0])    # the mode is in the key of the fact

    def test_realpath_retargeted_symlink_is_a_miss(self):
        (self.t / "t1").write_text("1"); (self.t / "t2").write_text("2"); lnk = self.t / "lnk"; lnk.symlink_to(self.t / "t1")
        fn = self.fn_probe(lambda: os.path.realpath(lnk))
        self.assertTrue(self.go(fn, name="k")[0].endswith("t1")); self.go(fn, name="k"); self.assertEqual(self.calls, 1)
        lnk.unlink(); lnk.symlink_to(self.t / "t2")
        self.assertTrue(self.go(fn, name="k")[0].endswith("t2")); self.assertEqual(self.calls, 2)
        rl = self.fn_probe(lambda: [os.readlink(lnk), (lnk.unlink(), lnk.symlink_to(self.t / "t1")) and 0])
        self.go(rl, name="rl")     # target changed while fn ran: not stored
        self.assertEqual(self.stored(), 1)   # only the realpath entry (same name "k")

    def test_unreadable_opened_file_not_stored(self):
        if os.geteuid() == 0:
            self.skipTest("root reads chmod 000 files")
        p = self.t / "den"; p.write_text("x"); os.chmod(p, 0)
        def rd():
            self.calls += 1
            try:
                return p.read_text()
            except PermissionError:
                return "denied"
        self.assertEqual(self.go(rd, name="rd")[0], "denied"); self.assertEqual(self.stored(), 0)
        os.chmod(p, 0o644); self.assertEqual(self.go(rd, name="rd")[0], "x")

    def test_f5_failed_open_only(self):                             # (a)
        p = self.t / "o1"
        def fn():
            self.calls += 1
            try:
                open(p).close(); return 1
            except FileNotFoundError:   # lint-silent: expected the test fn probes a missing file: 0 is the observed fact the memo records
                return 0
        self.go(fn, name="a"); self.go(fn, name="a"); self.assertEqual(self.calls, 1)
        p.write_text("x"); self.assertEqual(self.go(fn, name="a")[0], 1); self.assertEqual(self.calls, 2)

    def test_f5_exists_only(self):                                  # (b)
        p = self.t / "o2"
        fn = self.fn_probe(lambda: os.path.exists(p))
        self.go(fn, name="b"); self.go(fn, name="b"); self.assertEqual(self.calls, 1)
        p.write_text("x"); self.assertTrue(self.go(fn, name="b")[0]); self.assertEqual(self.calls, 2)

    def test_f5_dir_listed_twice_with_change_between_not_stored(self):   # (c)
        (self.t / "d").mkdir(); (self.t / "src").write_text("x")
        def fn():
            self.calls += 1
            a = os.listdir(self.t / "d"); os.replace(self.t / "src", self.t / "d" / "f"); return [a, os.listdir(self.t / "d")]
        self.go(fn, name="c"); self.assertEqual(self.stored(), 0)

    def test_f5_exists_twice_with_change_between_not_stored(self):       # (d)
        (self.t / "src").write_text("x")
        def fn():
            self.calls += 1
            a = os.path.exists(self.t / "new"); os.replace(self.t / "src", self.t / "new"); return [a, os.path.exists(self.t / "new")]
        self.go(fn, name="d"); self.assertEqual(self.stored(), 0)

    def test_f5_lstat_dangling_symlink(self):                       # (e)
        lnk = self.t / "dang"; lnk.symlink_to(self.t / "nowhere")
        for name, f in (("lstat", lambda: (os.lstat(lnk), 1)[1]), ("lexists", lambda: os.path.lexists(lnk))):
            fn = self.fn_probe(f); c = self.calls
            self.assertTrue(self.go(fn, name=name)[0]); self.go(fn, name=name); self.assertEqual(self.calls, c + 1)
            lnk.unlink()
            try:
                self.go(fn, name=name)
            except FileNotFoundError:   # lint-silent: expected the test expects the probe on a missing link to raise; the next assert counts calls
                pass
            self.assertEqual(self.calls, c + 2, name)     # miss: the link is gone
            lnk.symlink_to(self.t / "nowhere")
        # follow_symlinks=False must be the lstat fact: a dangling link stat()s as missing but lstat()s as present
        fn = self.fn_probe(lambda: os.stat(lnk, follow_symlinks=False) is not None)
        self.go(fn, name="fsf")
        (e,) = [x for x in (self.t / "cache").glob("*.json") if __import__("json").loads(x.read_text())["name"] == "fsf"]
        self.assertEqual([k for k, *_ in __import__("json").loads(e.read_text())["exist"]], ["lstat"])

    def test_f5_stat_by_fd_and_root_dir_not_stored(self):           # (f)
        (self.t / "d").mkdir(); (self.t / "d" / "x").write_text("x")
        def byfd():
            self.calls += 1
            fd = os.open(self.t / "d", os.O_RDONLY)
            try:
                return os.stat(fd).st_mode > 0
            finally:
                os.close(fd)
        def rootdir():
            self.calls += 1
            return __import__("glob").glob("*", root_dir=self.t / "d")
        for f in (byfd, rootdir):
            self.go(f, name=f.__name__); self.go(f, name=f.__name__)
        self.assertEqual((self.calls, self.stored()), (4, 0))

    def test_no_memo_env_spellings(self):
        for v, off in (("1", 1), ("true", 1), ("YES", 1), ("True", 1), ("0", 0), ("", 0), ("no", 0), ("off", 0), ("False", 0), ("2", 1), ("on", 1), ("y", 1), (" 1", 1)):
            os.environ["PIXELIOS_AUDIT_NO_MEMO"] = v
            self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(am.enabled(), not off, v)
            known = v.strip().lower() in ("", "0", "false", "no", "off", "1", "true", "yes")
            self.assertEqual("DISABLED" in err.getvalue(), not known, v)   # unknown spellings warn (once)
            am._warned.clear()
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO")

    def test_no_memo_env(self):
        os.environ["PIXELIOS_AUDIT_NO_MEMO"] = "1"
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
        self.go(); self.go()
        self.assertEqual(self.calls, 2)

    # ---- round-6 findings ----
    def _on_path(self, *dirs):
        sys.path[:0] = [str(d) if d != "" else "" for d in dirs]
        self.addCleanup(lambda: sys.path.__delitem__(slice(0, len(dirs))))

    def test_zip_on_sys_path_is_hashed_by_bytes(self):             # r6 F1
        z = self.t / "z.zip"; self._on_path(z)
        fn = self.fn_probe(lambda: 1)
        self.go(fn, name="z"); self.go(fn, name="z"); self.assertEqual(self.calls, 1)
        z.write_bytes(b"PK-one"); self.go(fn, name="z"); self.assertEqual(self.calls, 2)    # appeared: miss
        self.go(fn, name="z"); self.assertEqual(self.calls, 2)
        z.write_bytes(b"PK-two"); self.go(fn, name="z"); self.assertEqual(self.calls, 3)    # one byte changed: miss

    def test_empty_sys_path_entry_is_cwd(self):                    # r6 (e)
        cwd = self.t / "cwd"; cwd.mkdir(); old = os.getcwd(); os.chdir(cwd); self.addCleanup(os.chdir, old)
        self._on_path("")
        fn = self.fn_probe(lambda: 1)
        self.go(fn, name="e"); self.go(fn, name="e"); self.assertEqual(self.calls, 1)
        (cwd / "shadowme.py").write_text("X = 1\n"); self.go(fn, name="e"); self.assertEqual(self.calls, 2)

    def test_lstat_symlink_vs_dir_same_name_is_a_miss(self):       # r6 (a)
        p = self.t / "sd"; p.symlink_to(self.t / "nowhere")
        fn = self.fn_probe(lambda: __import__('stat').S_IFMT(os.lstat(p).st_mode))
        self.go(fn, name="a"); self.go(fn, name="a"); self.assertEqual(self.calls, 1)
        p.unlink(); p.mkdir(); self.go(fn, name="a"); self.assertEqual(self.calls, 2)

    def test_readlink_dir_fd_not_stored(self):                     # r6 (b)
        (self.t / "lk").symlink_to("tgt"); fd = os.open(self.t, os.O_RDONLY); self.addCleanup(os.close, fd)
        fn = self.fn_probe(lambda: os.readlink("lk", dir_fd=fd))
        self.go(fn, name="b"); self.assertEqual(self.stored(), 0)

    def test_access_nofollow_not_stored(self):                     # r6 (c)
        (self.t / "ac").write_text("x")
        fn = self.fn_probe(lambda: os.access(self.t / "ac", os.F_OK, follow_symlinks=False))
        self.go(fn, name="c"); self.assertEqual(self.stored(), 0)

    def test_exists_appear_vanish_not_stored(self):                # r6 (f): A-B-A
        p = self.t / "aba"
        def fn():
            self.calls += 1
            a = os.path.exists(p); os.mkdir(p); b = os.path.exists(p); os.rmdir(p); return [a, b]
        self.go(fn, name="f"); self.assertEqual(self.stored(), 0)


class Entry(unittest.TestCase):
    """A stored entry that was altered in a way that still parses: a miss, never a pass (docs/research/gate-2x-s3-selftests.md)."""
    fn, go = Memo.fn, Memo.go

    def setUp(self):
        Memo.setUp(self)
        q = contextlib.redirect_stderr(io.StringIO()); q.__enter__(); self.addCleanup(q.__exit__, None, None, None)   # the loud corrupt-entry lines of these tests

    def entry(self):
        self.go()
        (f,) = (self.t / "cache").glob("*.json")
        return f, json.loads(f.read_text())

    def test_flipped_stdout_or_ret_is_a_miss(self):
        for field, val in (("stdout", "PASS forged\n"), ("ret", 8)):
            f, e = self.entry(); e[field] = val; f.write_text(json.dumps(e))
            n = self.calls; r, o = self.go()
            self.assertEqual((self.calls, r, o), (n + 1, 7, "line one\nPASS d1\n"), field)

    def test_foreign_key_or_version_or_missing_sum_is_a_miss(self):
        for edit in (lambda e: e.update(key="0" * 64), lambda e: e.update(version=2), lambda e: e.pop("sum"), lambda e: e.update(sum="0" * 64),
                     lambda e: e.update(deps=[]), lambda e: e.update(deps=[["dep.txt", "0" * 64]])):
            f, e = self.entry(); edit(e); f.write_text(json.dumps(e))
            n = self.calls; self.go()
            self.assertEqual(self.calls, n + 1)

    def test_valid_entry_of_another_key_under_this_name_is_a_miss(self):
        f, _ = self.entry(); self.go(arg=b"other")
        (g,) = [x for x in (self.t / "cache").glob("*.json") if x != f]
        f.write_text(g.read_text())          # well-formed, checksum valid, but it answers a different question
        n = self.calls; self.go(); self.assertEqual(self.calls, n + 1)

    def test_truncated_or_empty_entry_is_a_miss(self):
        for cut in (0, 10, -3):
            f, _ = self.entry(); t = f.read_text(); f.write_text(t[:cut] if cut >= 0 else t[:cut])
            n = self.calls; self.go(); self.assertEqual(self.calls, n + 1)

    def test_non_object_body_or_missing_fields_is_a_loud_miss_and_is_rewritten(self):      # r1 F1: AttributeError used to escape _load and stick
        for body in ("null", "[]", "123", '"x"', "true"):
            f, _ = self.entry(); f.write_text(body)
            with contextlib.redirect_stderr(io.StringIO()) as werr:
                self.assertFalse(am.warm(self.script, "fn"), body)                  # warm() goes through the same _load: False, no exception
            self.assertIn("corrupt memo entry", werr.getvalue(), body)
            n = self.calls; err = io.StringIO()
            with contextlib.redirect_stderr(err):
                r, o = self.go()
            self.assertEqual((self.calls, r, o), (n + 1, 7, "line one\nPASS d1\n"), body)
            self.assertIn(str(f), err.getvalue(), body)
            self.assertEqual(err.getvalue().count("corrupt memo entry"), 1, body)    # one loud line naming the file
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.go()
            self.assertEqual((self.calls, err.getvalue()), (n + 1, ""), body)        # rewritten by the cold run: next call is a quiet hit
        for field in ("deps", "lists", "exist", "stdout", "ret"):                    # consistent sum, field lacking: miss, not KeyError on the hit path
            f, e = self.entry(); e.pop(field); e["sum"] = am._sum(e); f.write_text(json.dumps(e))
            n = self.calls
            with contextlib.redirect_stderr(io.StringIO()) as err:
                r, _ = self.go()
            self.assertEqual((self.calls, r), (n + 1, 7), field); self.assertIn("corrupt memo entry", err.getvalue(), field)
        f, e = self.entry(); e["exist"] = [[1, "p", None]]; e["sum"] = am._sum(e); f.write_text(json.dumps(e))   # kind.startswith on an int: AttributeError in the loop
        n = self.calls
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.go()
        self.assertEqual(self.calls, n + 1); self.assertIn("corrupt memo entry", err.getvalue())
        f, e = self.entry(); e["stdout"] = ["x"]; e["sum"] = am._sum(e); f.write_text(json.dumps(e))
        n = self.calls
        with contextlib.redirect_stderr(io.StringIO()):
            self.go()
        self.assertEqual(self.calls, n + 1)

    def test_absent_and_stale_entries_are_silent_misses(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.go(); self.dep.write_text("D1"); self.go()
        self.assertEqual((self.calls, err.getvalue()), (2, ""))

    def test_hit_rehashes_every_dep(self):
        self.go(); self.dep.write_text("d1"); self.go(); self.assertEqual(self.calls, 1)   # same bytes: still a hit
        self.dep.write_text("D1"); self.go(); self.assertEqual(self.calls, 2)              # one byte


class Stats(unittest.TestCase):
    """PIXELIOS_AUDIT_MEMO_STATS: one hit|cold line per run() (G5, shim/build.sh `memo hits=N cold=M`)."""

    fn, go = Memo.fn, Memo.go

    def setUp(self):
        Memo.setUp(self)
        self.st = self.t / "stats.tsv"
        os.environ["PIXELIOS_AUDIT_MEMO_STATS"] = str(self.st)
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_STATS", None)

    def lines(self):
        return self.st.read_text().splitlines() if self.st.exists() else []

    def test_miss_then_hit(self):
        self.go(); self.go()
        self.assertEqual(self.lines(), ["cold\tfn\tmiss", "hit\tfn"])

    def test_fail_off_and_not_stored_reasons_are_loud(self):
        def bad():
            raise AssertionError("no")
        def spawn():
            subprocess.run(["true"]); return 1
        with self.assertRaises(AssertionError), contextlib.redirect_stdout(io.StringIO()):
            am.run(bad, script=self.script, name="bad")
        self.go(spawn, name="spawn")
        os.environ["PIXELIOS_AUDIT_NO_MEMO"] = "1"; self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
        self.go(name="off")
        self.assertEqual(self.lines(), ["cold\tbad\tfail", "cold\tspawn\tnot-stored:spawn:subprocess.Popen", "cold\toff\toff"])

    def test_no_stats_file_unless_asked_and_a_bad_path_never_fails_the_audit(self):
        os.environ.pop("PIXELIOS_AUDIT_MEMO_STATS"); self.go(); self.assertFalse(self.st.exists())
        bad = str(self.t / "no" / "such" / "dir" / "s.tsv")
        os.environ["PIXELIOS_AUDIT_MEMO_STATS"] = bad
        am._stat_warned.discard(bad)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.go(name="x")[0], 7); self.assertEqual(self.go(name="y")[0], 7)
        self.assertIn("cannot write the stats file " + bad, err.getvalue()); self.assertIn("INCOMPLETE", err.getvalue())   # r1 F5b: loud, once per file
        self.assertEqual(err.getvalue().count("cannot write the stats file"), 1)

    def test_a_failed_store_is_one_cold_line_not_two(self):          # r1 F5a
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.script)      # a regular file as the memo dir: mkdir/replace fail
        self.go(); self.go()
        self.assertEqual(self.lines(), ["cold\tfn\tnot-stored:write"] * 2)

    def test_summary_lines(self):                                   # r1 F5: the G5 line
        def line(rows, expect=False):
            self.st.write_text("".join(r + "\n" for r in rows))
            return am.summary(self.st, expect)
        self.assertEqual(line(["cold\ta\tmiss", "hit\tb", "hit\tc"]), "memo hits=2 cold=1")
        self.assertEqual(line([]), "memo hits=0 cold=0")             # a non-gs101 build runs no audit: legitimately zero
        self.assertEqual(line(["cold\ta\tnot-stored:spawn:x", "cold\tb\tfail"], True), "memo hits=0 cold=2")
        self.assertTrue(line([], True).startswith("memo STATS EMPTY"))
        self.assertTrue(line(["cold\ta\toff", "cold\tb\toff"]).startswith("memo DISABLED"))
        self.assertIn("hits=0 cold=2", line(["cold\ta\toff", "cold\tb\toff"]))
        self.assertTrue(line(["hit\ta", "cold\tb\toff"]).startswith("memo DISABLED"))     # any off row
        self.assertTrue(am.summary(self.t / "nonexistent.tsv", True).startswith("memo STATS UNAVAILABLE"))
        self.assertTrue(am.summary(self.t, True).startswith("memo STATS UNAVAILABLE"))        # a directory
        for v in ("1", "yes", "banana"):                              # disabled by the environment, no run counted (non-gs101 build)
            os.environ["PIXELIOS_AUDIT_NO_MEMO"] = v; self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(line([]).startswith("memo DISABLED"), v)
            am._warned.clear()
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO")

    def test_summary_cli_as_build_sh_calls_it(self):
        me = pathlib.Path(am.__file__)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PIXELIOS_AUDIT")}
        def cli(*a, **e):
            r = subprocess.run([sys.executable, "-I", str(me), "--summary", *a], env={**env, **e}, capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout.strip()
        self.st.write_text("hit\ta\ncold\tb\tmiss\n")
        self.assertEqual(cli(str(self.st), "--expect-runs"), (0, "memo hits=1 cold=1"))
        self.assertEqual(cli(str(self.st), PIXELIOS_AUDIT_NO_MEMO="1")[1][:13], "memo DISABLED")
        self.st.write_text("")
        self.assertEqual(cli(str(self.st))[1], "memo hits=0 cold=0")
        self.assertEqual(cli(str(self.st), "--expect-runs")[1][:15], "memo STATS EMPT")
        self.assertEqual(cli(str(self.t / "gone.tsv"), "--expect-runs")[0], 0)      # a stats failure never fails the build; it is loud in the line
        self.assertEqual(subprocess.run([sys.executable, "-I", str(me)], capture_output=True).returncode, 1)
        # build.sh: the gs101 call passes --expect-runs, the awk 0/0 line is gone
        sh = (me.parent.parent / "shim" / "build.sh").read_text()
        self.assertIn('audit_memo.py --summary "$PIXELIOS_AUDIT_MEMO_STATS" --expect-runs', sh); self.assertNotIn("memo hits=%d", sh)

    def test_warm(self):
        self.assertFalse(am.warm(self.script, "fn"))
        self.go(); self.assertTrue(am.warm(self.script, "fn")); self.assertFalse(am.warm(self.script, "other"))
        self.dep.write_text("d2"); self.assertFalse(am.warm(self.script, "fn"))       # an edited input = stale
        self.dep.write_text("d1"); self.script.write_text("v9"); self.assertFalse(am.warm(self.script, "fn"))   # an edited audit script = stale
        self.script.write_text("v1"); os.environ["PIXELIOS_AUDIT_NO_MEMO"] = "1"; self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
        self.assertFalse(am.warm(self.script, "fn"))


class MemoWarmCli(unittest.TestCase):
    """`test-bluejay-boot.py --memo-warm` as check-all.sh runs it (a subprocess): exit 0 only if EVERY canary entry is there (r1 F2, X15 always-0, X16 any()). The entries are
    stored by a stub (am.run of a no-op under the canary names) in a second subprocess with the same sys.path set-up, so the real 14 s / 3 s self-tests do not run."""
    BOOT = pathlib.Path(__file__).resolve().parent / "test-bluejay-boot.py"
    STORE = ("import runpy, sys\n"
             "g = runpy.run_path(sys.argv[1], run_name='not_main')\n"      # module level only: same sys.path as a direct run, main() not called
             "for n in sys.argv[2:]: g['audit_memo'].run(lambda: 1, b'', script=sys.argv[1], name=n)\n")

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.t))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PIXELIOS_AUDIT")}
        self.env["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "memo")
        self.canaries = _load_boot()._MEMO_CANARIES
        self.assertGreaterEqual(len(self.canaries), 2)

    def store(self, *names):
        r = subprocess.run([sys.executable, "-I", "-c", self.STORE, str(self.BOOT), *names], env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])

    def warm(self, **env):
        r = subprocess.run([sys.executable, "-I", str(self.BOOT), "--memo-warm"], env={**self.env, **env}, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    def test_empty_memo_is_cold_rc1(self):                          # kills X15 (always exit 0)
        rc, out = self.warm()
        self.assertEqual(rc, 1, out); self.assertIn("RESULT: COLD", out)

    def test_all_canaries_present_is_warm_rc0(self):
        self.store(*self.canaries)
        rc, out = self.warm()
        self.assertEqual(rc, 0, out); self.assertIn("RESULT: WARM", out)

    def test_one_canary_missing_is_cold_each_way(self):             # kills X16 (any() instead of all())
        for keep in self.canaries:
            shutil.rmtree(self.t / "memo", ignore_errors=True)
            self.store(keep)
            rc, out = self.warm()
            self.assertEqual(rc, 1, f"only {keep} stored: {out}"); self.assertIn("RESULT: COLD", out)

    def test_warm_memo_but_disabled_is_cold(self):
        self.store(*self.canaries)
        for v in ("1", "banana"):
            self.assertEqual(self.warm(PIXELIOS_AUDIT_NO_MEMO=v)[0], 1, v)


def _load_boot():
    sp = importlib.util.spec_from_file_location("tbb_for_memo_test", pathlib.Path(__file__).resolve().parent / "test-bluejay-boot.py")
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


class ObjectWiring(unittest.TestCase):
    """test-bluejay-boot.py _memo_obj: the key is every byte the object self-test sees, and not the build dir (D3)."""

    @classmethod
    def setUpClass(cls):
        cls.m = _load_boot()

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.t))
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "cache")
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO", None)
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_DIR", None)
        self.disasm = "out/gate/build-0/a.o:     file format elf64-little\n\n0000 <w32>:\n  0:\td65f03c0 \tret\n"
        self.undef, self.local, self.sect = {"con_str", "try_read32"}, {"w32"}, "out/gate/build-0/a.o:     file format elf64-little\nSections:\n"
        self.seen = []
        self.m._touch_obj_info = lambda p: (self.disasm.replace("out/gate/build-0/a.o", str(p)), set(self.undef), set(self.local), self.sect.replace("out/gate/build-0/a.o", str(p)))

    def selftest(self, info, *extra):
        self.seen.append((info, extra)); print("PASS fake self-test")
        return 3

    def go(self, d="out/gate/build-0", *extra):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.m._memo_obj(self.selftest, pathlib.Path(d) / "a.o", *extra)

    def test_other_build_dir_is_a_hit_and_the_path_is_not_in_what_the_self_test_sees(self):
        self.go("out/gate/build-0"); self.go("/abs/elsewhere/build-fast-0")
        self.assertEqual(len(self.seen), 1)
        self.assertNotIn("build-0", self.seen[0][0][0]); self.assertNotIn("build-0", self.seen[0][0][3]); self.assertTrue(self.seen[0][0][0].startswith("a.o:"))

    def test_one_object_byte_is_a_miss(self):                          # (a)
        self.go(); self.disasm = self.disasm.replace("d65f03c0", "d65f03c1"); self.go()
        self.assertEqual(len(self.seen), 2)

    def test_undefined_local_sections_and_extra_args_are_in_the_key(self):
        self.go(); self.undef = self.undef | {"memcpy"}; self.go(); self.assertEqual(len(self.seen), 2)
        self.local = self.local | {"evil"}; self.go(); self.assertEqual(len(self.seen), 3)
        self.sect += "x"; self.go(); self.assertEqual(len(self.seen), 4)
        self.go("out/gate/build-0", {"a", "b"}, "tag"); self.assertEqual(len(self.seen), 5)
        self.go("out/gate/build-0", {"b", "a"}, "tag"); self.assertEqual(len(self.seen), 5)     # set order is not an input
        self.go("out/gate/build-0", {"a", "b"}, "other"); self.assertEqual(len(self.seen), 6)
        self.go("out/gate/build-0", {"a"}, "tag"); self.assertEqual(len(self.seen), 7)

    def test_self_test_receives_the_extra_args_it_was_keyed_on(self):
        self.go("out/gate/build-0", {"a"}, "tag")
        self.assertEqual(self.seen[0][1], ({"a"}, "tag"))

    def test_a_raising_object_self_test_is_never_stored(self):
        def bad(info):
            self.seen.append(info); raise AssertionError("FAILED to catch")
        for _ in range(2):
            with self.assertRaises(AssertionError), contextlib.redirect_stdout(io.StringIO()):
                self.m._memo_obj(bad, pathlib.Path("out/a.o"))
        self.assertEqual(len(self.seen), 2); self.assertFalse(list((self.t / "cache").glob("*.json")))

    def test_audit_script_is_in_the_key(self):                         # (b) for the real wiring: the key reads the script that _memo_obj passes
        self.go(); k = am.key("selftest", b"x", self.m.__file__)
        other = self.t / "other_script.py"; other.write_text("x")
        self.assertNotEqual(k, am.key("selftest", b"x", other))

    def test_obj_text(self):
        p = pathlib.Path("out/gate/build-0/wlanlink.o")
        self.assertEqual(self.m._obj_text(f"{p}:     file format\n{p}: again", p), "wlanlink.o:     file format\nwlanlink.o: again")

    def test_canaries_exist_as_memoized_self_tests(self):
        src = pathlib.Path(self.m.__file__).read_text()
        for n in self.m._MEMO_CANARIES:
            self.assertIn(f"_memo({n}", src)                           # a renamed/unmemoized canary would make --memo-warm always say cold (fail safe, but pointless)
        self.assertFalse(am.warm(self.m.__file__, self.m._MEMO_CANARIES[0]))
        with contextlib.redirect_stdout(io.StringIO()):
            for n in self.m._MEMO_CANARIES:
                am.run(lambda: 1, b"", script=self.m.__file__, name=n)
        self.assertTrue(all(am.warm(self.m.__file__, n) for n in self.m._MEMO_CANARIES))


class Lock(unittest.TestCase):
    """K3 (PLAN-GATE-2X): the per-key lock of run(): one compute per entry across processes, fail open, never a pass."""

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.script = self.t / "script.py"; self.script.write_text("v1")
        self.cache = self.t / "cache"
        for k, v in (("PIXELIOS_AUDIT_MEMO_DIR", str(self.cache)), ("PIXELIOS_AUDIT_MEMO_LOCK_WAIT", "20")):
            os.environ[k] = v; self.addCleanup(os.environ.pop, k, None)
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO", None)
        self.calls, self.locks_seen = 0, []

    def fn(self):
        self.calls += 1
        self.locks_seen.append(self.cache.is_dir() and any(self.lockpath_exists(n) for n in ("fn",)))
        print("PASS body")
        return 5

    def go(self, fn=None, name="fn"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            r = am.run(fn or self.fn, b"", script=self.script, name=name)
        return r, out.getvalue(), err.getvalue()

    def lockpath_exists(self, name):
        try:
            am._STAT(self.lockpath(name)); return True              # the unwrapped os.stat: not an input of the run being recorded
        except OSError:   # lint-silent: expected a missing lock file is the False the test asserts on
            return False

    def lockpath(self, name="fn"):
        return self.cache / (am.key(name, b"", self.script) + ".lock")

    def test_lock_exists_while_computing_and_is_gone_after_the_store(self):
        self.go()
        self.assertEqual(self.locks_seen, [True])
        self.assertEqual(list(self.cache.glob("*.lock")), [])
        self.assertTrue(am.warm(self.script, "fn"))

    def test_lock_is_dropped_when_the_body_fails(self):
        def bad():
            raise AssertionError("FAIL audit")
        with self.assertRaises(AssertionError):
            self.go(bad)
        self.assertEqual(list(self.cache.glob("*.lock")), [])
        self.assertFalse(am.warm(self.script, "bad"))

    def test_lock_is_dropped_when_the_result_is_not_stored(self):
        def unstorable():
            return {1, 2}                                           # not JSON: a cold run that is not stored
        self.go(unstorable)
        self.assertEqual(list(self.cache.glob("*.lock")), [])

    def test_lock_wait_zero_means_no_lock_file(self):
        os.environ["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = "0"
        self.go()
        self.assertEqual(self.locks_seen, [False])

    def test_hit_takes_no_lock(self):
        self.go(); self.locks_seen.clear()
        real = am._lock_take
        am._lock_take = lambda f: self.fail("a hit must not take the lock")
        self.addCleanup(setattr, am, "_lock_take", real)
        self.go()
        self.assertEqual(self.calls, 1)

    def test_stale_lock_of_a_dead_holder_is_taken_over_at_once(self):
        p = subprocess.run([sys.executable, "-I", "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        self.cache.mkdir(parents=True)
        self.lockpath().write_text(p.stdout)                        # a pid that has exited
        t0 = time.monotonic(); r, o, e = self.go()
        self.assertLess(time.monotonic() - t0, 5)                  # not the 20 s wait
        self.assertEqual((r, self.calls, o), (5, 1, "PASS body\n"))
        self.assertTrue(am.warm(self.script, "fn"))
        self.assertFalse(self.lockpath().exists())

    def test_live_foreign_lock_times_out_and_fails_open_without_removing_it(self):
        os.environ["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = "0.5"
        self.cache.mkdir(parents=True)
        self.lockpath().write_text(f"{os.getppid()}\n")             # a live process that is not us
        t0 = time.monotonic(); r, o, e = self.go()
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)        # it waited
        self.assertEqual((r, self.calls), (5, 1))                  # then computed it itself (fail open: never a failure, never a pass)
        self.assertTrue(self.lockpath().exists())                  # and left the other process's lock alone
        self.assertTrue(am.warm(self.script, "fn"))

    def test_unreadable_lock_pid_is_not_a_dead_holder(self):
        os.environ["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = "0.3"
        self.cache.mkdir(parents=True)
        self.lockpath().write_text("")                              # half-written by a holder that is just starting
        self.assertFalse(am._holder_gone(str(self.lockpath())))
        self.lockpath().write_text("not a pid")
        self.assertFalse(am._holder_gone(str(self.lockpath())))
        self.assertFalse(am._holder_gone(str(self.cache / "nope.lock")))
        self.assertTrue(self.lockpath().exists())

    def test_empty_lock_older_than_the_grace_is_reclaimed_loudly(self):
        """F3a: a holder killed between the O_EXCL create and the pid write leaves an empty lock that used to be 'not gone' for ever (every compute of that key then waited the whole timeout)."""
        self.cache.mkdir(parents=True)
        self.lockpath().write_text("")
        old = time.time() - 60
        os.utime(self.lockpath(), (old, old))
        self.assertTrue(am._holder_gone(str(self.lockpath())))
        self.lockpath().write_text("not a pid"); os.utime(self.lockpath(), (old, old))
        self.assertTrue(am._holder_gone(str(self.lockpath())))
        t0 = time.monotonic(); r, o, e = self.go()
        self.assertLess(time.monotonic() - t0, 5)                  # not the 20 s wait
        self.assertEqual((r, self.calls, o), (5, 1, "PASS body\n"))
        self.assertIn("removed the lock of a dead holder", e)       # rule 8: said
        self.assertFalse(self.lockpath().exists())

    def test_young_empty_lock_is_waited_for_not_taken(self):
        os.environ["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = "0.4"
        self.cache.mkdir(parents=True)
        self.lockpath().write_text("")                              # just created: its holder is about to write the pid
        t0 = time.monotonic(); r, o, e = self.go()
        self.assertGreaterEqual(time.monotonic() - t0, 0.3)
        self.assertEqual((r, self.calls), (5, 1))                  # failed open after the wait
        self.assertNotIn("removed the lock", e)
        self.assertTrue(self.lockpath().exists())

    def test_oserror_removing_a_dead_holders_lock_fails_open_and_says_so(self):
        """F3c: a dead holder's lock of another uid in a sticky dir: os.unlink raises PermissionError. Fail open (compute without the lock), never raise out of the audit."""
        p = subprocess.run([sys.executable, "-I", "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        self.cache.mkdir(parents=True)
        self.lockpath().write_text(p.stdout)
        real = os.unlink
        def unlink(path, *a, **k):
            if str(path).endswith(".lock"):
                raise PermissionError(13, "Operation not permitted", str(path))
            return real(path, *a, **k)
        unlink_patch = unittest.mock.patch("os.unlink", unlink)
        with unlink_patch:
            t0 = time.monotonic(); r, o, e = self.go()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((r, self.calls, o), (5, 1, "PASS body\n"))
        self.assertIn("cannot remove the stale lock", e)
        self.assertIn("computing without the lock", e)

    def test_oserror_writing_the_lock_fails_open_and_leaves_no_lock(self):
        """F3c: ENOSPC on the pid write: the audit must not raise; the empty lock is removed so it cannot block the others."""
        real = os.write
        def write(fd, data):
            try:
                if os.readlink(f"/proc/self/fd/{fd}").endswith(".lock"):
                    raise OSError(28, "No space left on device")
            except FileNotFoundError:   # lint-silent: expected the fd is not the lock: no fault injected for other files
                pass
            return real(fd, data)
        with unittest.mock.patch("os.write", write):
            r, o, e = self.go()
        self.assertEqual((r, self.calls, o), (5, 1, "PASS body\n"))
        self.assertIn("cannot write the lock", e)
        self.assertIn("No space left on device", e)
        self.assertFalse(self.lockpath().exists())

    def test_lock_wait_default_is_sixty_seconds(self):
        """The default is part of the contract (a build variant waits that long for the one computing the same entry): the table of values above compares _LOCK_WAIT with itself."""
        self.assertEqual(am._LOCK_WAIT, 60.0)
        os.environ.pop("PIXELIOS_AUDIT_MEMO_LOCK_WAIT", None)
        self.assertEqual(am._lock_wait(), 60.0)

    def test_unwritable_cache_dir_fails_open(self):
        (self.t / "file").write_text("x")
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "file" / "sub")   # mkdir under a regular file: OSError
        t0 = time.monotonic(); r, o, e = self.go()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((r, self.calls, o), (5, 1, "PASS body\n"))

    def test_lock_wait_values(self):
        e = os.environ
        for v, want in (("2.5", 2.5), ("0", 0.0), (" 7 ", 7.0), ("", am._LOCK_WAIT)):
            e["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = v
            self.assertEqual(am._lock_wait(), want, v)
        for v in ("abc", "-1"):
            e["PIXELIOS_AUDIT_MEMO_LOCK_WAIT"] = v
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(am._lock_wait(), am._LOCK_WAIT)
            self.assertIn(f"bad PIXELIOS_AUDIT_MEMO_LOCK_WAIT={v!r}", err.getvalue())   # loud, rule 8

    def test_nested_run_is_still_stored_the_lock_is_no_input_of_the_outer_run(self):
        def outer():                                                # the inner result is not JSON: its cold run takes the lock but stores no tmp file (a stored inner run's tmp write is recorded by the outer run: pre-existing, not the lock)
            return len(am.run(lambda: {1, 2}, b"", script=self.script, name="inner"))
        self.assertEqual(self.go(outer, "outer")[0], 2)
        self.assertTrue(am.warm(self.script, "outer"))             # the lock create / write / unlink of the inner run were not recorded as a write of the outer one
        e = json.loads(next(p for p in self.cache.glob("*.json")).read_text())
        self.assertEqual(e["name"], "outer"); self.assertNotIn(".lock", json.dumps(e))

    def test_corrupt_entry_is_reported_once_not_again_after_the_lock(self):
        self.go()
        for p in self.cache.glob("*.json"):
            p.write_text("{not json")
        r, o, e = self.go()
        self.assertEqual(e.count("corrupt memo entry"), 1)
        self.assertEqual((r, self.calls), (5, 2))
        self.assertEqual(self.go()[0:2], (5, "PASS body (memo %s)\n" % am.key("fn", b"", self.script)[:16]))

    def test_the_quiet_flag_is_reset_after_the_lock_code(self):
        self.go()
        self.assertFalse(am._quiet)
        with self.assertRaises(RuntimeError):
            self.go(lambda: (_ for _ in ()).throw(RuntimeError("x")), "boom")
        self.assertFalse(am._quiet)


_CHILD = r"""
import os, sys, time, pathlib
sys.path.insert(0, sys.argv[1])
import audit_memo as am
script, gate, name = sys.argv[2:5]
while not os.path.exists(gate):
    time.sleep(0.005)
def fn():
    time.sleep(0.4)
    print("PASS racing body")
    return 11
r = am.run(fn, b"", script=script, name=name)
print("RET", r)
"""


class Race(unittest.TestCase):
    """4 processes missing the same entry at the same moment (the gate's build variants started at once on a cold memo)."""

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        self.script = self.t / "script.py"; self.script.write_text("v1")
        self.child = self.t / "child.py"; self.child.write_text(_CHILD)
        self.cache = self.t / "cache"
        self.env = {**os.environ, "PIXELIOS_AUDIT_MEMO_DIR": str(self.cache)}
        self.env.pop("PIXELIOS_AUDIT_NO_MEMO", None)

    def race(self, names, lock_wait):
        gate, count = self.t / "gate", self.t / "stats"
        env = {**self.env, "PIXELIOS_AUDIT_MEMO_LOCK_WAIT": lock_wait, "PIXELIOS_AUDIT_MEMO_STATS": str(count)}
        ps = [subprocess.Popen([sys.executable, "-I", str(self.child), str(pathlib.Path(__file__).resolve().parent), str(self.script), str(gate), n],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in names]
        time.sleep(0.5)                                              # all four are polling for the gate
        gate.write_text("go")
        outs = [p.communicate(timeout=60) for p in ps]
        for p, (o, e) in zip(ps, outs):
            self.assertEqual(p.returncode, 0, e)
        computed = [ln for ln in count.read_text().splitlines() if ln.startswith("cold\t") and ln.endswith("\tmiss")]   # one stats line per run(): "cold ... miss" = computed and stored
        return [o for o, _ in outs], [e for _, e in outs], computed

    def entries(self):
        return sorted(self.cache.glob("*.json"))

    def test_four_racing_writers_without_the_lock_leave_one_valid_entry(self):
        outs, errs, computed = self.race(["racing"] * 4, "0")
        self.assertEqual(len(computed), 4)                           # the race was real: every process computed
        self.assertEqual(outs, ["PASS racing body\nRET 11\n"] * 4)   # cold output of each is the plain output
        self.assertEqual(errs, [""] * 4)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(list(self.cache.glob("*.tmp")) + list(self.cache.glob("*.lock")), [])
        e = json.loads(self.entries()[0].read_text())                # a whole, checksummed entry: never a torn write
        self.assertEqual(e["sum"], am._sum(e)); self.assertEqual(e["ret"], 11)
        self.assertEqual(e["name"], "racing")                         # (not am.warm(): the key holds this process's sys.path, the children run with different ones)

    def test_four_racing_processes_with_the_lock_compute_once_and_the_others_hit(self):
        outs, errs, computed = self.race(["racing"] * 4, "30")
        self.assertEqual(len(computed), 1)                           # one compute
        self.assertEqual(sum(o.startswith("PASS racing body\n") for o in outs), 1)
        hits = [o for o in outs if "(memo " in o]
        self.assertEqual(len(hits), 3)                               # the other three replayed it (memo ...)
        for o in outs:
            self.assertTrue(o.endswith("RET 11\n"), o)
        self.assertEqual(errs, [""] * 4)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(list(self.cache.glob("*.lock")), [])

    def test_racing_writers_of_different_keys_do_not_wait_for_each_other(self):
        t0 = time.monotonic()
        outs, errs, computed = self.race(["a", "b", "c", "d"], "30")
        self.assertEqual(len(computed), 4)                           # four different entries: four computes
        self.assertLess(time.monotonic() - t0, 0.5 + 0.4 * 3)        # in parallel, not one after the other
        self.assertEqual(len(self.entries()), 4)


class Pool(unittest.TestCase):
    """K3: tools/audit_pool.py. Same output as the serial run, failures and crashes are loud."""

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)

    def fixture(self, delays=(0.30, 0.0, 0.15, 0.05, 0.2, 0.0)):
        """A body like the audit's main(): prints between dispatches; job i prints two lines after a delay that reverses the finishing order."""
        def body():
            print("header")
            for i, d in enumerate(delays):
                def job(i=i, d=d):
                    time.sleep(d)
                    print(f"PASS selftest {i}")
                    print(f"  detail {i}")
                ap.dispatch(job, f"job{i}")
                print(f"between {i}")
            print("footer")
        return body

    def capture(self, body, jobs):
        out, err = io.StringIO(), io.StringIO()
        exc = None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                ap.run(body, jobs)
            except BaseException as x:
                exc = x
        return out.getvalue(), err.getvalue(), exc

    def test_pool_output_equals_serial_output(self):
        serial, _, e1 = self.capture(self.fixture(), 1)
        self.assertIsNone(e1)
        self.assertEqual(serial.splitlines()[:3], ["header", "PASS selftest 0", "  detail 0"])
        for jobs in (2, 4, 8):
            pooled, err, e2 = self.capture(self.fixture(), jobs)
            self.assertIsNone(e2)
            self.assertEqual(pooled, serial, f"jobs={jobs}")
            self.assertEqual(err, "")

    def test_pool_output_equals_serial_output_through_the_memo(self):
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "cache"); self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_DIR", None)
        script = self.t / "s.py"; script.write_text("v")
        def body():
            for i in range(6):
                def st(i=i):
                    print(f"PASS memo selftest {i}")
                    return i
                ap.dispatch(lambda st=st, i=i: am.run(st, b"", script=script, name=f"st{i}"), f"st{i}")
            print("end")
        cold_serial, _, _ = self.capture(body, 1)                    # fills the memo
        warm_pool, _, e = self.capture(body, 4)                      # replayed from it, in a pool
        shutil.rmtree(self.t / "cache")
        cold_pool, _, e2 = self.capture(body, 4)                     # a cold pool run writes the same entries
        self.assertIsNone(e); self.assertIsNone(e2)
        self.assertEqual(cold_pool, cold_serial)
        self.assertEqual(len(list((self.t / "cache").glob("*.json"))), 6)
        self.assertEqual(warm_pool.count("(memo "), 6)
        self.assertEqual([ln.split(" (memo")[0] for ln in warm_pool.splitlines()], cold_serial.splitlines())

    def test_jobs_run_concurrently_and_never_more_than_jobs(self):
        run = self.t / "run"; run.mkdir()
        res = self.t / "res"; res.mkdir()
        def body():
            for i in range(8):
                def job(i=i):
                    (run / str(i)).write_text("")
                    time.sleep(0.15)
                    (res / str(i)).write_text(str(len(list(run.iterdir()))))
                    (run / str(i)).unlink()
                ap.dispatch(job, f"j{i}")
        _, _, e = self.capture(body, 3)
        self.assertIsNone(e)
        seen = [int(p.read_text()) for p in res.iterdir()]
        self.assertEqual(len(seen), 8)
        self.assertEqual(max(seen), 3)                                # three at a time (not serial, not 8)

    def test_jobs_one_is_inline_in_this_process(self):
        pids = []
        def body():
            ap.dispatch(lambda: pids.append(os.getpid()), "x")
        self.capture(body, 1)
        self.assertEqual(pids, [os.getpid()])
        pids.clear(); self.capture(body, 2)
        self.assertEqual(pids, [])                                    # ran in a child (appended there, not here)

    def test_failing_worker_fails_the_audit_after_the_output_before_it(self):
        def body():
            print("one")
            ap.dispatch(lambda: print("PASS a"), "a")
            def bad():
                print("partial of the failing job")
                raise AssertionError("FAIL: mutant survived")
            ap.dispatch(bad, "bad")
            print("never printed in the serial run (it stopped at the failure)")
            ap.dispatch(lambda: print("PASS c"), "c")
        for jobs in (1, 4):
            out, err, exc = self.capture(body, jobs)
            self.assertIsInstance(exc, AssertionError, jobs)
            self.assertEqual(str(exc), "FAIL: mutant survived")
            self.assertEqual(out, "one\nPASS a\npartial of the failing job\n") if jobs > 1 else self.assertTrue(out.startswith("one\nPASS a\n"))

    def test_failing_worker_exit_is_the_same_systemexit(self):
        def body():
            ap.dispatch(lambda: sys.exit("FAIL exit"), "x")
        out, err, exc = self.capture(body, 4)
        self.assertIsInstance(exc, SystemExit); self.assertEqual(exc.code, "FAIL exit")

    def test_the_first_failure_in_program_order_wins(self):
        def body():
            def slow_bad():
                time.sleep(0.3); raise AssertionError("first")
            def fast_bad():
                raise ValueError("second")
            ap.dispatch(slow_bad, "slow"); ap.dispatch(fast_bad, "fast")
        out, err, exc = self.capture(body, 4)
        self.assertIsInstance(exc, AssertionError); self.assertEqual(str(exc), "first")

    def test_crashing_worker_is_a_loud_failure_never_a_pass(self):
        for how in (lambda: os._exit(0), lambda: os._exit(3), lambda: os.kill(os.getpid(), signal.SIGKILL)):
            def body(how=how):
                print("before")
                ap.dispatch(how, "crasher")
                ap.dispatch(lambda: print("PASS after"), "after")
            out, err, exc = self.capture(body, 4)
            self.assertIsInstance(exc, ap.WorkerCrash)
            self.assertIn("crasher", str(exc)); self.assertIn("FAILS", str(exc))
            self.assertEqual(out, "before\n")

    def test_unpicklable_exception_is_still_a_failure_with_the_traceback(self):
        class Odd(Exception):
            def __init__(self, a, b): super().__init__(a); self.f = lambda: 0
        def body():
            def bad():
                raise Odd("x", "y")
            ap.dispatch(bad, "odd")
        out, err, exc = self.capture(body, 4)
        self.assertIsInstance(exc, RuntimeError)
        self.assertIn("Odd", str(exc)); self.assertIn("Traceback", err)

    def test_body_exception_comes_after_earlier_job_output(self):
        def body():
            ap.dispatch(lambda: print("PASS a"), "a")
            raise AssertionError("real check failed")
        out, err, exc = self.capture(body, 4)
        self.assertEqual(out, "PASS a\n"); self.assertEqual(str(exc), "real check failed")

    def test_drain_writes_everything_and_later_dispatches_run_inline(self):
        pids = []
        def body():
            ap.dispatch(lambda: print("PASS a"), "a")
            ap.drain()
            print("after drain")
            ap.dispatch(lambda: pids.append(os.getpid()), "b")
        out, err, exc = self.capture(body, 4)
        self.assertEqual(out, "PASS a\nafter drain\n"); self.assertEqual(pids, [os.getpid()])

    def test_no_children_or_temp_dirs_are_left_behind(self):
        own = self.t / "tmp"; own.mkdir()                              # a private TMPDIR: other processes' pool dirs (a parallel test run) are none of this test's business
        real = tempfile.tempdir; tempfile.tempdir = str(own); self.addCleanup(setattr, tempfile, "tempdir", real)
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "audit-pool-*")))
        self.capture(self.fixture(), 4)
        def bad():
            ap.dispatch(lambda: (_ for _ in ()).throw(AssertionError("x")), "bad"); ap.dispatch(lambda: time.sleep(5), "sleeper")
        t0 = time.monotonic(); self.capture(bad, 4)
        self.assertLess(time.monotonic() - t0, 4)                     # the sleeper was killed, not waited for
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "audit-pool-*"))), before)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)                                # no unreaped child

    def _sigterm_run(self, body_src, wait_files):
        """Run `body_src` (a function body() using ap, in a fresh `python3 -I`), wait until every file in wait_files exists, send SIGTERM to the parent. -> (returncode, stdout, stderr)."""
        script = self.t / "sigterm_case.py"
        script.write_text("import os, sys, time\nsys.path.insert(0, %r)\nimport audit_pool as ap\n%s\nap.run(body, 2)\n" % (str(pathlib.Path(ap.__file__).resolve().parent), textwrap.dedent(body_src)))
        p = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.t)
        try:
            deadline = time.monotonic() + 20
            while not all(os.path.exists(f) for f in wait_files):
                self.assertLess(time.monotonic(), deadline, "the case never reached its wait point")
                time.sleep(0.05)
            time.sleep(0.4)                                          # the finished worker's result file is written a moment after its marker
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                p.kill(); p.communicate()
        return p.returncode, out, err

    def test_sigterm_writes_the_output_so_far_names_the_cut_jobs_and_kills_the_workers(self):
        """F4: a hung pooled audit killed by the step timeout (SIGTERM) used to print nothing and leave its workers running."""
        done, pidf = self.t / "fast.done", self.t / "slow.pid"
        rc, out, err = self._sigterm_run(f"""
            def body():
                print("header")
                def fast():
                    print("PASS fast"); open({str(done)!r}, "w").close()
                ap.dispatch(fast, "fastjob")
                print("between")
                def slow():
                    open({str(pidf)!r}, "w").write(str(os.getpid())); time.sleep(60)
                ap.dispatch(slow, "slowjob")
                print("never shown")
                ap.drain()
            """, [done, pidf])
        self.assertEqual(rc, -signal.SIGTERM)                       # still dies by the signal
        self.assertEqual(out, "header\nPASS fast\nbetween\n")       # the serial run's output up to the unfinished job
        self.assertIn("audit pool: terminated by SIGTERM", err)
        self.assertIn("1 self-test result(s) not shown", err)
        self.assertIn("slowjob", err)
        pid = int(pidf.read_text())
        with self.assertRaises(ProcessLookupError):                  # the worker is gone (killed and reaped), not orphaned
            os.kill(pid, 0)

    def test_sigterm_before_the_drain_writes_the_unwritten_segments_up_to_the_first_unfinished_job(self):
        """The body is still running (no drain yet): the segments before the unfinished job were never written by drain, so _on_term must write them (mutant sigterm-writes-no-segments)."""
        done, pidf, ready = self.t / "fast.done", self.t / "slow.pid", self.t / "ready"
        rc, out, err = self._sigterm_run(f"""
            def body():
                print("header")
                def fast():
                    print("PASS fast"); open({str(done)!r}, "w").close()
                ap.dispatch(fast, "fastjob")
                print("between")
                def slow():
                    open({str(pidf)!r}, "w").write(str(os.getpid())); time.sleep(60)
                ap.dispatch(slow, "slowjob")
                print("after the unfinished job")
                open({str(ready)!r}, "w").close(); time.sleep(60)      # the body hangs: drain is never reached
            """, [done, pidf, ready])
        self.assertEqual(rc, -signal.SIGTERM)
        self.assertEqual(out, "header\nPASS fast\nbetween\n")        # not the segment after the unfinished job
        self.assertIn("1 self-test result(s) not shown", err)

    def test_a_worker_that_dies_by_sigterm_does_not_run_the_parents_handler(self):
        """Real process, real fd 2: a worker keeping the parent's handler would print 'terminated by SIGTERM' on the audit's stderr (mutant worker-keeps-the-parent-sigterm-handler; the in-process capture of the test below cannot see a child's print)."""
        script = self.t / "worker_term.py"
        script.write_text("import os, signal, sys\nsys.path.insert(0, %r)\nimport audit_pool as ap\n"
                          "def body():\n    print('header')\n    ap.dispatch(lambda: os.kill(os.getpid(), signal.SIGTERM), 'termed')\nap.run(body, 2)\n" % str(pathlib.Path(ap.__file__).resolve().parent))
        p = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=30, cwd=self.t)
        self.assertEqual(p.returncode, 1, p.stderr)                  # the audit fails with the WorkerCrash traceback
        self.assertEqual(p.stdout.count("header"), 1, p.stdout)       # a child running _on_term would also write the segments
        self.assertIn("killed by signal 15", p.stderr)
        self.assertNotIn("terminated by SIGTERM", p.stderr)

    def test_sigterm_to_a_worker_is_a_worker_crash_not_a_pool_shutdown(self):
        """The forked worker must not keep the parent's SIGTERM handler (it would kill the parent's session and re-signal the parent)."""
        def body():
            ap.dispatch(lambda: os.kill(os.getpid(), signal.SIGTERM), "termed")
            ap.dispatch(lambda: print("PASS after"), "after")
        out, err, exc = self.capture(body, 4)
        self.assertIsInstance(exc, ap.WorkerCrash)
        self.assertIn("killed by signal 15", str(exc))
        self.assertNotIn("terminated by SIGTERM", err)

    def test_sigterm_handler_is_removed_after_the_run(self):
        before = signal.getsignal(signal.SIGTERM)
        self.capture(self.fixture(), 2)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_jobs_from_env(self):
        self.assertEqual(ap.jobs_from_env({}), 4)
        self.assertEqual(ap.jobs_from_env({"GATE_AUDIT_JOBS": ""}), 4)
        self.assertEqual(ap.jobs_from_env({"GATE_AUDIT_JOBS": " 1 "}), 1)
        self.assertEqual(ap.jobs_from_env({"GATE_AUDIT_JOBS": "12"}), 12)
        for bad in ("0", "-1", "x", "2.5", "٣"):
            with self.assertRaises(SystemExit, msg=bad):
                ap.jobs_from_env({"GATE_AUDIT_JOBS": bad})

    def test_run_is_not_reentrant(self):
        def body():
            ap.run(lambda: None, 2)
        out, err, exc = self.capture(body, 2)
        self.assertIsInstance(exc, RuntimeError)


class PoolWiring(unittest.TestCase):
    """test-bluejay-boot.py: _memo / _memo_obj go through the pool, main() runs the audit inside it, and the serial-only parts stay outside."""

    @classmethod
    def setUpClass(cls):
        cls.m = _load_boot()

    def setUp(self):
        self.t = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t)
        os.environ["PIXELIOS_AUDIT_MEMO_DIR"] = str(self.t / "cache"); self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_MEMO_DIR", None)
        os.environ.pop("PIXELIOS_AUDIT_NO_MEMO", None)

    def test_memo_runs_in_a_child_inside_a_session_and_inline_outside(self):
        pids = self.t / "pids"
        fd = os.open(pids, os.O_WRONLY | os.O_CREAT | os.O_APPEND); self.addCleanup(os.close, fd)   # opened outside the self-tests: a write inside would make them unstorable
        def pure_selftest_a():
            os.write(fd, f"{os.getpid()}\n".encode()); print("PASS a")
        def pure_selftest_b():
            os.write(fd, f"{os.getpid()}\n".encode()); print("PASS b")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.m._memo(pure_selftest_a), None)    # inline, no session: runs here (its value is None anyway)
            ap.run(lambda: (self.m._memo(pure_selftest_a), self.m._memo(pure_selftest_b)), 4)
        self.assertEqual(out.getvalue().splitlines()[0], "PASS a")
        lines = pids.read_text().split()
        self.assertEqual(lines[0], str(os.getpid()))                  # the inline call
        self.assertEqual(len(lines), 2)                              # a (hit in the pool) / b (cold in a child)
        self.assertNotEqual(lines[1], str(os.getpid()))

    def test_memo_key_includes_the_arg_bytes(self):
        """F6(i): _memo(fn, arg, ...) keys on arg (the wlanlink object self-test passes the disassembly text that way): a changed arg must be a miss, not the old PASS."""
        log = self.t / "calls"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND); self.addCleanup(os.close, fd)
        def arg_selftest(tag):
            os.write(fd, tag.encode()); print("PASS", tag)
        with contextlib.redirect_stdout(io.StringIO()):
            self.m._memo(arg_selftest, b"disasm-1", "a")
            self.m._memo(arg_selftest, b"disasm-2", "b")
            self.m._memo(arg_selftest, b"disasm-1", "c")             # same arg as the first: a hit, fn does not run
        self.assertEqual(log.read_text(), "ab")

    def test_memo_obj_runs_in_a_child_inside_a_session(self):
        self.m._touch_obj_info = lambda p: ("d", set(), set(), "s")
        pids = self.t / "pids"
        fd = os.open(pids, os.O_WRONLY | os.O_CREAT | os.O_APPEND); self.addCleanup(os.close, fd)
        def obj_selftest(info, *extra):
            os.write(fd, f"{os.getpid()}\n".encode()); print("PASS obj")
        with contextlib.redirect_stdout(io.StringIO()):
            ap.run(lambda: self.m._memo_obj(obj_selftest, pathlib.Path("out/a.o")), 4)
        self.assertEqual(len(pids.read_text().split()), 1)
        self.assertNotEqual(pids.read_text().split()[0], str(os.getpid()))

    def test_every_memoized_self_test_is_a_pure_function_and_there_is_no_exemption_list(self):
        src = pathlib.Path(self.m.__file__).read_text()
        names = set(re.findall(r"_memo(?:_obj)?\(([a-z0-9_]+)", src)) - {"fn"}
        self.assertGreaterEqual(len(names), 20)
        for n in sorted(names):
            m = re.search(rf"^def {n}\(.*?(?=^def )", src, re.S | re.M)
            self.assertIsNotNone(m, n)
            self.assertNotRegex(m.group(0), r"subprocess|os\.system|Popen|write_text|write_bytes|open\([^)]*[\"']w", f"{n} starts a process or writes a file: it must stay out of the pool: call it inline, not through _memo/_memo_obj")
        self.assertFalse(hasattr(self.m, "_SERIAL_SELFTESTS"), "an exemption list nothing consults is a lie (review gate-cold-k3 r1 F5): _memo pools everything it is given")

    def test_main_runs_the_audit_in_the_pool_and_drains_before_the_host_tests(self):
        src = pathlib.Path(self.m.__file__).read_text()
        self.assertRegex(src, r"audit_pool\.run\(lambda: _audit_main\(args\), audit_pool\.jobs_from_env\(\)\)")
        i, j = src.index("audit_pool.drain()"), src.index('with tempfile.TemporaryDirectory() as temp:')
        self.assertLess(i, j)                                         # before the gcc host tests, which write to fd 1 themselves
        self.assertLess(src.index("n_stores = check_udump_object"), i)
        self.assertEqual(src.count("audit_memo.run("), 2)             # only _memo and _memo_obj, both dispatching


if __name__ == "__main__":
    unittest.main()
