#!/usr/bin/env python3
"""tools/lint-phone-converge: its own fixture-tree + mutant self-test, and the real tree must be clean (R38, AP-291)."""
import os
import subprocess
import sys
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(TOOLS, "lint-phone-converge")


def run(*a):
    return subprocess.run([sys.executable, "-I", LINT, *a], capture_output=True, text=True, timeout=120)


class T(unittest.TestCase):
    def test_self_test(self):
        p = run("--self-test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("RESULT: PASS self-test", p.stdout)

    def test_real_tree_clean(self):
        p = run()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def copy_tools(self, d):
        import shutil
        os.makedirs(os.path.join(d, "tools"))
        for n in os.listdir(TOOLS):
            p = os.path.join(TOOLS, n)
            if os.path.isfile(p) and not n.endswith(".pyc"):
                shutil.copy2(p, os.path.join(d, "tools", n))

    def test_real_tree_copy_clean_then_mutants(self):
        """a copy of the real top-level tools/ is clean; then each mutant must be caught (rule 8: a lint nobody can fail is no lint)."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.copy_tools(d)
            self.assertEqual(run("--root", d).returncode, 0)
            allow = os.path.join(d, "tools", "phone-converge-allow.txt")
            with open(allow) as f:
                text = f.read()
            # mutant 1: a new run path with a hand check and no allowlist entry
            new = os.path.join(d, "tools", "zz-newrun")
            with open(new, "w") as f:
                f.write("#!/bin/bash\ntools/phone-lock check\nfastboot devices | grep -q .\n")
            os.chmod(new, 0o755)
            p = run("--root", d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("READINESS-CHECK tools/zz-newrun:3", p.stdout)
            os.remove(new)
            # mutant 2: the xrun allowlist entry removed -> the real `adb get-state` line in xrun is a finding
            with open(allow, "w") as f:
                f.write("".join(l for l in text.splitlines(True) if not l.startswith("tools/xrun ")))
            p = run("--root", d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("READINESS-CHECK tools/xrun:", p.stdout)
            with open(allow, "w") as f:
                f.write(text)

    def mutant(self, d, name, body, mode, want, append_to=None):
        """put/append BODY in the copy, run the lint, expect rc 1 and WANT in the output, then undo."""
        path = os.path.join(d, "tools", append_to or name)
        old = open(path).read() if append_to else None
        with open(path, "a" if append_to else "w") as f:
            f.write(body)
        if not append_to:
            os.chmod(path, mode)
        p = run("--root", d)
        if append_to:
            open(path, "w").write(old)
        else:
            os.remove(path)
        self.assertEqual(p.returncode, 1, name + "\n" + p.stdout)
        self.assertIn(want, p.stdout, name)

    def test_review_r1_mutants(self):
        """review phone-converge-r38-r1 F1/F2: the pre-fix probe-run, a second check in an allowlisted file, adb devices, a non-exec .py, the python list form."""
        import subprocess as sp
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.copy_tools(d)
            self.assertEqual(run("--root", d).returncode, 0)
            # F1: the probe-run before R38 (git show cfaff26:tools/probe-run) has four `$PS --expect` pre-checks; it must FAIL even with its allowlist entry
            r = sp.run(["git", "-C", TOOLS, "show", "cfaff26:tools/probe-run"], capture_output=True, text=True)
            if r.returncode == 0:
                path = os.path.join(d, "tools", "probe-run")
                keep = open(path).read()
                open(path, "w").write(r.stdout)
                p = run("--root", d)
                open(path, "w").write(keep)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("ALLOW-GREW tools/probe-run", p.stdout)
            else:   # no history in this checkout: the same shape inline
                self.mutant(d, "probe-run", '$PS --expect fastboot --wait 20 || $PS --expect recovery --wait 5\n', 0, "ALLOW-GREW tools/probe-run", append_to="probe-run")
            # F1: a second adb get-state next to xrun's allowlisted diagnostic
            self.mutant(d, "xrun", "timeout 5 adb get-state | grep -q device\n", 0, "ALLOW-GREW tools/xrun", append_to="xrun")
            # F2a: adb devices in a run path
            self.mutant(d, "fboot.sh", "adb devices | grep -q device\n", 0, "READINESS-CHECK tools/fboot.sh:", append_to="fboot.sh")
            # F2b: a NEW non-executable .py run path
            self.mutant(d, "zz.py", "import subprocess\nsubprocess.run(['tools/phone-lock', 'check'])\nsubprocess.run(['fastboot', 'devices'])\n", 0o644,
                        "READINESS-CHECK tools/zz.py:3")
            # F2b: the real non-executable tools/pstore-pull.py is scanned (its allowlist pin is the only thing that passes it)
            allow = os.path.join(d, "tools", "phone-converge-allow.txt")
            text = open(allow).read()
            open(allow, "w").write("".join(l for l in text.splitlines(True) if not l.startswith("tools/pstore-pull.py ")))
            p = run("--root", d)
            open(allow, "w").write(text)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("READINESS-CHECK tools/pstore-pull.py:", p.stdout)
            # F2c: python list form in a scanned run path
            self.mutant(d, "pstore-pull.py", 'subprocess.run(["adb", "get-state"])\n', 0, "ALLOW-GREW tools/pstore-pull.py", append_to="pstore-pull.py")
            self.assertEqual(run("--root", d).returncode, 0)


if __name__ == "__main__":
    unittest.main()
