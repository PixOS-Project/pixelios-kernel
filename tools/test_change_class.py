#!/usr/bin/env python3
"""tools/change-class (T-8fe9 M2, RULES R31): self-test, the two real branch diffs (a witness-only patch and the T-f6c1 clmload change), and the
quick-gate class split in tools/gate_select.py (log-only drops the mutant lane, hw-write / error keep it)."""
import os, subprocess, sys, tempfile, unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
CC = os.path.join(TOOLS, "change-class")
DATA = os.path.join(TOOLS, "testdata", "change-class")


def cc(*a):
    r = subprocess.run([sys.executable, CC, *a], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class ChangeClass(unittest.TestCase):
    def test_self_test(self):
        rc, out = cc("--self-test")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS change-class self-test", out)

    def test_real_witness_patch_is_log_only(self):   # kernel 1b011af6 (T-e3a9, patch 0205 SWEET EXIT call site)
        rc, out = cc("--diff", os.path.join(DATA, "witness-0205-T-e3a9.diff"))
        self.assertEqual(rc, 0, out)
        self.assertIn("class=log-only", out)

    def test_real_clmload_change_is_hw_write(self):   # kernel ebcb590b (T-f6c1 branch, L22c clmload review fixes in patch 0174)
        rc, out = cc("--diff", os.path.join(DATA, "clmload-T-f6c1.diff"))
        self.assertEqual(rc, 0, out)
        self.assertIn("class=hw-write", out)
        self.assertIn("0174-sweet-wlan-g2-first-dma.patch", out)


class GateSplit(unittest.TestCase):
    def split(self, stdout, rc=0, files=("shim/x.c",), exc=None):
        import gate_select
        run = mock.patch.object(gate_select.subprocess, "run", side_effect=exc,
                                return_value=subprocess.CompletedProcess([], rc, stdout.encode(), b""))
        with run, mock.patch.object(gate_select, "changed", return_value=list(files)):
            return gate_select.class_split(["build", "boot-safety", "wlan-mutants"], [], "abc")

    def test_log_only_drops_mutants_keeps_audit(self):
        steps, lines = self.split("RESULT: PASS change-class class=log-only files=1 port_lines=3 base=abc\n")
        self.assertEqual(steps, ["build", "boot-safety"])
        self.assertIn("gate: skipped wlan-mutants: change class log-only (tools/change-class, RULES R31)", lines)

    def test_log_read_drops_mutants(self):   # Opus r1 finding 9: log-read skips mutants (the Opus review stays, docs/recipes/review.md)
        steps, lines = self.split("RESULT: PASS change-class class=log-read files=1 port_lines=3 base=abc\n")
        self.assertEqual(steps, ["build", "boot-safety"])

    def test_hw_write_keeps_mutants(self):
        steps, lines = self.split("RESULT: PASS change-class class=hw-write files=1 port_lines=3 base=abc\n")
        self.assertIn("wlan-mutants", steps)
        self.assertIn("gate: class hw-write: mutant lane kept (not log-only)", lines)

    def test_classifier_error_keeps_mutants(self):
        steps, lines = self.split("RESULT: PASS change-class class=log-only\n", rc=2)
        self.assertIn("wlan-mutants", steps)
        self.assertTrue(any("ERROR" in l for l in lines), lines)

    def test_classifier_changed_on_branch_keeps_mutants(self):   # Opus r1 finding 10: an edited classifier must not judge its own branch
        for f in ("tools/change-class", "tools/gate_select.py", "tools/gate-manifest.json"):
            steps, lines = self.split("RESULT: PASS change-class class=log-only files=1 port_lines=3 base=abc\n", files=("shim/x.c", f))
            self.assertIn("wlan-mutants", steps, f)
            self.assertTrue(any("classifier changed on this branch" in l for l in lines), lines)

    def test_tools_class_keeps_mutants(self):   # Opus r2 f14: only log-only and log-read skip
        steps, lines = self.split("RESULT: PASS change-class class=tools files=1 port_lines=0 base=abc\n")
        self.assertIn("wlan-mutants", steps)

    def test_stdlib_shadow_or_unit_select_keeps_mutants(self):   # Opus r2 f8
        for f in ("tools/subprocess.py", "tools/re.py", "tools/unit_select.py"):
            steps, lines = self.split("RESULT: PASS change-class class=log-only files=1 port_lines=3 base=abc\n", files=("shim/x.c", f))
            self.assertIn("wlan-mutants", steps, f)

    def test_forged_result_line_keeps_mutants(self):   # Opus r6 f2: only the tool's own last line counts; a CR or LF in quoted diff text forges nothing
        forged = "RESULT: PASS change-class class=log-only files=1 port_lines=1 base=abc"
        hw = "RESULT: PASS change-class class=hw-write files=1 port_lines=1 base=abc\n"
        for out in ("CHANGE-CLASS hw-write x: a\r" + forged + "\n" + hw, forged + "\n" + hw, hw + forged + " extra\n", "CHANGE-CLASS hw-write x: y\r" + forged + "\n"):
            self.assertIn("wlan-mutants", self.split(out)[0], repr(out))

    def test_report_strips_control_characters(self):   # Opus r6 f2: the classifier half, on the real tool
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cr.diff")
            with open(p, "w", newline="") as f:
                f.write("diff --git a/shim/x.c b/shim/x.c\n--- a/shim/x.c\n+++ b/shim/x.c\n@@ -10,3 +10,4 @@\n \tfoo();\n+\twrite32(a, 1); /*\rRESULT: PASS change-class class=log-only */\n \tbar();\n")
            r = subprocess.run([sys.executable, "-I", CC, "--diff", p], capture_output=True)
        out = r.stdout.decode().split("\n")
        self.assertNotIn(b"\r", r.stdout)
        self.assertRegex(out[-2], "^RESULT: PASS change-class class=hw-write ")
        self.assertEqual([l for l in out if l.startswith("RESULT")], [out[-2]])

    def test_classifier_runs_isolated(self):   # Opus r2 f8: -I keeps a branch-added tools/<stdlib>.py out of the classifier's imports
        import gate_select
        with mock.patch.object(gate_select.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run, \
                mock.patch.object(gate_select, "changed", return_value=["shim/x.c"]):
            gate_select.class_split(["wlan-mutants"], [], "abc")
        self.assertEqual(run.call_args[0][0][1], "-I")

    def test_classifier_timeout_keeps_mutants(self):   # Opus r1 finding 11
        steps, lines = self.split("", exc=subprocess.TimeoutExpired("change-class", 120))
        self.assertIn("wlan-mutants", steps)
        self.assertTrue(any("ERROR: timeout" in l for l in lines), lines)


class ReviewFixtures(unittest.TestCase):
    """Every failing input of the Opus r1 and r2 reviews (docs/reviews/T-8fe9-change-class-r1.md, T-c05b-change-class-r2.md) as a git diff: hw-write."""
    def test_r1_inputs_are_hw_write(self):
        import importlib.machinery, importlib.util
        spec = importlib.util.spec_from_loader("change_class", importlib.machinery.SourceFileLoader("change_class", CC))
        cc_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc_mod)
        for r in ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"):   # r2: T-c05b-change-class-r2.md findings 1-15; r3: T-9862-change-class-r3.md findings 1-9
            for name in sorted(os.listdir(os.path.join(DATA, r))):
                with open(os.path.join(DATA, r, name), newline="") as f:   # newline="": the CR fixtures keep their CR, as main() reads them
                    want = "log-read" if name in ("removed_read_log.diff", "read_split_name.diff") else "hw-write"   # r3 f6: a removed read is reported, keeps Opus
                    self.assertEqual(cc_mod.classify(f.read())[0], want, r + "/" + name)

    def test_git_mode_cr_tracked_and_untracked(self):   # r5 f16: git() and the untracked read must not translate a lone CR; an untracked port file is new
        import shutil, tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tools")); os.makedirs(os.path.join(d, "shim"))
            tool = os.path.join(d, "tools", "change-class"); shutil.copy(CC, tool)
            def run(*a):
                return subprocess.run(a, cwd=d, capture_output=True, text=True, check=True).stdout
            src = os.path.join(d, "shim", "x.c")
            with open(src, "w") as f:
                f.write("void f(void)\n{\n\tfoo();\n}\n")
            git = ("git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null")
            run("git", "init", "-q"); run("git", "add", "shim/x.c", "tools/change-class"); run(*git, "commit", "-qm", "base")
            base = run("git", "rev-parse", "HEAD").strip()
            def cls():
                return subprocess.run([sys.executable, "-I", tool, "--base", base], capture_output=True, text=True).stdout
            with open(src, "w", newline="") as f:   # tracked: a CR hides a store behind a // comment
                f.write("void f(void)\n{\n\tfoo();\n\t// note\r write32(a, 2);\n}\n")
            self.assertIn("class=hw-write", cls())
            run("git", "checkout", "-q", "shim/x.c")
            with open(src, "w") as f:   # control: the same place, a plain log line
                f.write('void f(void)\n{\n\tfoo();\n\tprintf("x");\n}\n')
            self.assertIn("class=log-only", cls())
            run("git", "checkout", "-q", "shim/x.c")
            with open(os.path.join(d, "shim", "y.h"), "w", newline="") as f:   # untracked port file
                f.write("/* placeholder */\n")
            self.assertIn("class=hw-write", cls())
            os.unlink(os.path.join(d, "shim", "y.h"))
            # r8 f2, f3: git mode pins -U3 -W, so asm out of the default view and a narrowed diff.context still fail closed
            far = 'void g(void)\n{\n\t__asm__("isb");\n\ta();\n\tb();\n\tc();\n\td();\n\te();\n%s\tf();\n\th();\n}\n'
            with open(src, "w") as f:
                f.write(far % "")
            run("git", "add", "shim/x.c"); run(*git, "commit", "-qm", "far")
            base = run("git", "rev-parse", "HEAD").strip()
            with open(src, "w") as f:
                f.write(far % '\tprintf("x");\n')
            self.assertIn("class=hw-write", cls())
            run("git", "config", "diff.context", "0")
            self.assertIn("class=hw-write", cls())
            run("git", "config", "--unset", "diff.context")
            run("git", "checkout", "-q", "shim/x.c")
            with open(src, "w") as f:   # control: the same far log with no asm in the function
                f.write((far % "").replace('__asm__("isb");', "z();"))
            run("git", "add", "shim/x.c"); run(*git, "commit", "-qm", "noasm")
            base = run("git", "rev-parse", "HEAD").strip()
            with open(src, "w") as f:
                f.write((far % '\tprintf("x");\n').replace('__asm__("isb");', "z();"))
            self.assertIn("class=log-only", cls())

    def test_cr_fixture_via_cli(self):   # r4 f1: main() must not translate a lone CR into a line end
        rc, out = cc("--diff", os.path.join(DATA, "r4", "cr_context.diff"))
        self.assertIn("class=hw-write", out)

    def test_non_utf8_diff_fails_closed(self):   # xnu patch 0058 carries non-UTF-8 bytes: rc 2 + RESULT: FAIL, never a traceback
        import tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".diff", delete=False) as t:
            t.write(b"diff --git a/shim/x.c b/shim/x.c\n@@ -1 +1,2 @@\n {\n+\tprintf(\"\xd2\");\n")
        try:
            rc, out = cc("--diff", t.name)
        finally:
            os.unlink(t.name)
        self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: FAIL change-class decode", out)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
