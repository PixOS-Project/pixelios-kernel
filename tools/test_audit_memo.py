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

    def test_key_includes_memo_code(self):
        a = am.key("fn", b"", self.script)
        other = self.t / "memo_copy.py"; other.write_text("x")
        old = am.__file__; am.__file__ = str(other)
        self.addCleanup(setattr, am, "__file__", old)
        self.assertNotEqual(a, am.key("fn", b"", self.script))

    def test_no_memo_env(self):
        os.environ["PIXELIOS_AUDIT_NO_MEMO"] = "1"
        self.addCleanup(os.environ.pop, "PIXELIOS_AUDIT_NO_MEMO", None)
        self.go(); self.go()
        self.assertEqual(self.calls, 2)


if __name__ == "__main__":
    unittest.main()
