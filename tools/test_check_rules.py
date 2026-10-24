"""tools/check-rules.py and tools/check_rules/*: replay fixtures (tools/testdata/check-rules/RNN/{fail,pass}), baseline ratchet, one mutant per pattern.
Fixtures were taken once with git show (headed with their source commit); no git at test time (public/shallow clones)."""
import collections, contextlib, importlib.util, io, pathlib, shutil, sys, tempfile, unittest
TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from check_rules import r14_chains as r14

FIX = TOOLS / "testdata" / "check-rules" / "R14"
spec = importlib.util.spec_from_file_location("check_rules_main", TOOLS / "check-rules.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

# fixture -> pattern names it must trigger (fail/) ; pass/ fixtures must trigger none of ALL_NAMES unless listed in PASS_ALLOWED
FAIL = {
    "kernel-c0c2ad7^-build.sh.txt": {"and-O"},
    "super-da03e99^-build.sh.txt": {"and-echo-pass", "and-out"},
    "synthetic-brace-o-build.sh.txt": {"and-braceO"},
    "synthetic-pipe-build.sh.txt": {"pipe-and"},
    "kernel-ab7debb-wlanlink_host_test.c.txt": {"if-link-n-eq"},
}
# super da03e99 predates the chk() rework: it has no echo-PASS chains but still has `&& ./out/` ones (observed, 17)
PASS_ALLOWED = {"super-da03e99-build.sh.txt": {"and-out"}}


def names(path, **kw):
    """Pattern names r14.check reports on one fixture file placed where the rule looks for it."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "shim").mkdir()
        dest = "shim/wlanlink_host_test.c" if ".c" in path.suffixes else "shim/build.sh"
        shutil.copy(path, root / dest)
        return collections.Counter(f.msg.split()[1].rstrip(":") for f in r14.check(root, **kw))


class Replay(unittest.TestCase):
    def test_fail_fixtures_fail(self):
        for f, want in FAIL.items():
            got = set(names(FIX / "fail" / f))
            self.assertEqual(got, want, f)

    def test_pass_fixtures_pass(self):
        for p in sorted((FIX / "pass").iterdir()):
            self.assertEqual(set(names(p)), PASS_ALLOWED.get(p.name, set()), p.name)

    def test_replay_counts(self):
        self.assertEqual(names(FIX / "fail" / "super-da03e99^-build.sh.txt")["and-echo-pass"], 3)
        self.assertEqual(names(FIX / "pass" / "super-da03e99-build.sh.txt")["and-echo-pass"], 0)
        self.assertGreaterEqual(names(FIX / "fail" / "kernel-c0c2ad7^-build.sh.txt")["and-O"], 30)
        self.assertEqual(set(names(FIX / "pass" / "kernel-c0c2ad7-build.sh.txt")), set())

    def test_every_fixture_is_headed_with_its_source(self):
        for p in FIX.rglob("*"):
            if p.is_file():
                self.assertIn("fixture source:", p.read_text().splitlines()[0], p.name)


class Mutants(unittest.TestCase):
    """Disable one pattern: the fail fixtures that need it must stop failing (so test_fail_fixtures_fail would catch the mutant)."""
    def test_each_pattern_is_needed(self):
        for name in list(r14.SH_PATTERNS) + list(r14.C_PATTERNS) + ["pipe-and"]:
            kw = {}
            if name == "pipe-and":
                kw["pipe"] = False
            elif name in r14.SH_PATTERNS:
                kw["sh_patterns"] = {k: v for k, v in r14.SH_PATTERNS.items() if k != name}
            else:
                kw["c_patterns"] = {k: v for k, v in r14.C_PATTERNS.items() if k != name}
            hit = [f for f, want in FAIL.items() if name in want and name not in names(FIX / "fail" / f, **kw)]
            self.assertTrue(hit, f"pattern {name}: disabling it changes no fail fixture (untested pattern)")


class Ratchet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "r"
        (self.root / "shim").mkdir(parents=True)
        self.bdir = pathlib.Path(self.tmp.name) / "b"
        self.bdir.mkdir()
        self.src = self.root / "shim" / "build.sh"
        self.src.write_text("set -euo pipefail\ngcc -o $O/x x.c && $O/x\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *a):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cr.main(["--root", str(self.root), "--baseline-dir", str(self.bdir), *a])
        return rc, out.getvalue()

    def test_new_finding_fails(self):
        rc, out = self.run_main("--strict")
        self.assertEqual(rc, 1)
        self.assertIn("RESULT: FAIL 1 new, 0 baselined", out)

    def test_baselined_finding_passes_and_survives_line_moves(self):
        self.assertEqual(self.run_main("--write-baseline")[0], 0)
        self.src.write_text("set -euo pipefail\n# moved\n\ngcc -o $O/x x.c && $O/x\n")
        rc, out = self.run_main("--strict")
        self.assertEqual((rc, out.splitlines()[-1]), (0, "RESULT: PASS 0 new, 1 baselined"))

    def test_second_copy_of_a_baselined_line_is_new(self):
        self.run_main("--write-baseline")
        self.src.write_text(self.src.read_text() + "gcc -o $O/x x.c && $O/x\n")
        self.assertEqual(self.run_main("--strict")[0], 1)

    def test_stale_baseline_entry_fails(self):
        self.run_main("--write-baseline")
        self.src.write_text("set -euo pipefail\nchk x $O/x\n")
        rc, out = self.run_main("--strict")
        self.assertEqual(rc, 1)
        self.assertIn("baseline stale: remove it", out)

    def test_report_never_fails(self):
        rc, out = self.run_main("--report")
        self.assertEqual(rc, 0)
        self.assertIn("1 new", out.splitlines()[-1])

    def test_usage_error_is_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cr.main([]), 2)

    def test_repo_tree_passes_strict(self):
        """The worktree itself: R14 findings must all be baselined (the gate step runs the same)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cr.main(["--strict"])
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
