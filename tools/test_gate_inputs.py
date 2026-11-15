"""tools/gate-inputs (gate step `inputs`, AP-11/144; review r42-gate-r1 F1/F4/F6): a scratch repo with a copy of the tool; ok / missing / empty / directory / dangling / escape.
Run: python3 -I tools/test_gate_inputs.py"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parent / "gate-inputs"
ADT = "research/ipsw-x/d22ap.adt"


class Inputs(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.main = pathlib.Path(self.t.name).resolve() / "main"
        (self.main / "tools").mkdir(parents=True)
        shutil.copy(TOOL, self.main / "tools" / "gate-inputs")
        subprocess.run(["git", "init", "-q", str(self.main)], check=True)
        self.good(self.main)

    def tearDown(self):
        self.t.cleanup()

    @staticmethod
    def put(root, rel, data="x"):
        p = pathlib.Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)

    def good(self, root):
        q = pathlib.Path(root, ADT)   # a previous subtest may have left a directory or empty file there
        if q.is_dir() and not q.is_symlink():
            shutil.rmtree(q)
        self.put(root, ADT, "adt")

    def run_in(self, root, **env):
        e = {k: v for k, v in os.environ.items() if k != "GATE_NO_INPUTS"}
        e.update(env)
        p = subprocess.run(["bash", str(pathlib.Path(root, "tools", "gate-inputs"))], capture_output=True, text=True, env=e, cwd="/")
        return p.returncode, p.stdout + p.stderr

    def wt(self):
        """A second checkout (git worktree) of the scratch repo with nothing linked: the state tools/worktree-new fixes."""
        subprocess.run(["git", "-C", str(self.main), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "c"], check=True)
        w = self.main.parent / "wt"
        subprocess.run(["git", "-C", str(self.main), "worktree", "add", "-q", "-b", "wt", str(w)], check=True, capture_output=True)
        (w / "tools").mkdir(exist_ok=True)
        shutil.copy(TOOL, w / "tools" / "gate-inputs")
        return w

    def test_adt_present_pass(self):
        rc, out = self.run_in(self.main)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: PASS inputs research/ipsw-x/d22ap.adt resolves")

    def test_adt_missing_fail_and_do_instead_names_it(self):
        w = self.wt()
        rc, out = self.run_in(w)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISSING INPUT " + ADT, out)
        self.assertNotIn("third_party", out)   # T-93e6: the AOSP tools are tracked, never an input
        self.assertIn("ln -sfn %s/research research" % self.main, out)
        self.assertIn("tools/worktree-new", out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: FAIL inputs missing the gitignored input above")

    def test_plain_worktree_with_only_research_linked_passes(self):
        """T-93e6: tools/third_party is tracked, so a plain `git worktree add` + the research link is all the gate needs."""
        w = self.wt()
        rc, out = self.run_in(w)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("MISSING INPUT tools/third_party", out)
        os.symlink(self.main / "research", w / "research")
        rc, out = self.run_in(w)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "RESULT: PASS inputs research/ipsw-x/d22ap.adt resolves")

    def test_empty_file_is_missing(self):
        for rel in (ADT,):
            with self.subTest(rel):
                self.good(self.main)
                pathlib.Path(self.main, rel).write_text("")
                rc, out = self.run_in(self.main)
                self.assertEqual(rc, 1, out)
                self.assertIn("MISSING INPUT " + rel, out)

    def test_directory_instead_of_file_is_missing(self):
        """r1 F4: `[ ! -s dir ]` is false for a non-empty directory; mkadt.py:46 needs a file."""
        for rel in (ADT,):
            with self.subTest(rel):
                self.good(self.main)
                os.unlink(self.main / rel)
                (self.main / rel / "inner").mkdir(parents=True)
                (self.main / rel / "inner" / "f").write_text("x")
                rc, out = self.run_in(self.main)
                self.assertEqual(rc, 1, out)
                self.assertIn("MISSING INPUT " + rel, out)
                self.assertIn("RESULT: FAIL inputs", out)

    def test_dangling_symlink_is_missing_and_replaceable(self):
        w = self.wt()
        os.symlink(self.main / "nope", w / "research")
        rc, out = self.run_in(w)
        self.assertEqual(rc, 1, out)
        self.assertIn("MISSING INPUT " + ADT, out)
        self.assertIn("(the link there is dangling)", out)

    def test_real_directory_in_the_way_is_named_not_nested(self):
        """F6: `ln -s X research` onto an existing directory would create research/research."""
        w = self.wt()
        (w / "research").mkdir()
        rc, out = self.run_in(w)
        self.assertEqual(rc, 1, out)
        self.assertIn("research exists here as a real file or directory", out)

    def test_main_clone_without_the_adt_says_where_it_must_be_restored(self):
        w = self.wt()
        shutil.rmtree(self.main / "research")
        rc, out = self.run_in(w)
        self.assertEqual(rc, 1, out)
        self.assertIn("main clone", out)
        self.assertIn("restore research/ipsw-x", out)

    def test_escape_with_a_reason_passes_loudly(self):
        w = self.wt()
        rc, out = self.run_in(w, GATE_NO_INPUTS="driver 20ac: formal container has no ADT")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "RESULT: PASS inputs SKIPPED by GATE_NO_INPUTS=driver 20ac: formal container has no ADT")

    def test_escape_with_quotes_and_shell_metacharacters_is_taken_literally(self):
        w = self.wt()
        reason = 'my "reason" $x `y` \\z'
        rc, out = self.run_in(w, GATE_NO_INPUTS=reason)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "RESULT: PASS inputs SKIPPED by GATE_NO_INPUTS=" + reason)

    def test_blank_or_symbol_only_reason_is_rejected(self):
        """r1 F4: a whitespace-only reason used to PASS and the GATE line then showed an empty reason."""
        w = self.wt()
        for reason in (" ", "   ", "\t", '"', '"$`\\', "...", " - "):
            with self.subTest(reason=reason):
                rc, out = self.run_in(w, GATE_NO_INPUTS=reason)
                self.assertEqual(rc, 1, out)
                self.assertIn("RESULT: FAIL inputs GATE_NO_INPUTS needs a reason", out)
                self.assertNotIn("SKIPPED", out)

    def test_empty_variable_is_no_escape_the_checks_run(self):
        w = self.wt()
        rc, out = self.run_in(w, GATE_NO_INPUTS="")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISSING INPUT " + ADT, out)
        rc, out = self.run_in(self.main, GATE_NO_INPUTS="")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("SKIPPED", out)


if __name__ == "__main__":
    unittest.main()
