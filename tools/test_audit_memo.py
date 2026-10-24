"""tools/audit_memo.py: hit, miss on each key part, dep tracking, FAIL and subprocess runs not stored, corrupt entry, replay output."""
import contextlib, io, os, pathlib, subprocess, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import audit_memo as am


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
        self.go(); self.assertEqual(self.calls, 2)
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
        a, b = os.stat, os.lstat
        self.go()
        self.assertIs(os.stat, a); self.assertIs(os.lstat, b)

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
            except FileNotFoundError:
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
            except FileNotFoundError:
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


if __name__ == "__main__":
    unittest.main()
