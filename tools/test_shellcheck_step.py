"""The gate's `shellcheck` step: tools/shell-files lists the right files, check-all.sh and gate_select.py agree on the step, and a planted bug fails it."""
import pathlib, re, shutil, subprocess, sys, tempfile, unittest

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import gate_select as g


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


class ShellcheckStep(unittest.TestCase):
    def test_shell_files_lists_scripts_with_and_without_extension(self):
        files = run(str(TOOLS / "shell-files")).stdout.split()
        for f in ("tools/check-all.sh", "shim/build.sh", "tools/xrun"):
            self.assertIn(f, files)
        self.assertFalse([f for f in files if f.endswith(".py")])

    def test_check_all_and_gate_select_agree(self):
        text = (TOOLS / "check-all.sh").read_text()
        self.assertEqual(re.search(r'^ALL="([^"]*)"', text, re.M).group(1).split(), g.ALL)
        self.assertIn("step B shellcheck", text)
        self.assertIn("shellcheck -S warning $(tools/shell-files)", text)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_planted_bug_fails_the_step_command(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "tools").mkdir()
            shutil.copy(TOOLS / "shell-files", d / "tools")
            (d / "bad").write_text('#!/bin/bash\nrm -rf $UNSET/*\ncd /x\nfoo\n')   # SC2164 (warning): cd without || exit
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
            cmd = "shellcheck -S warning $(tools/shell-files)"
            r = run("bash", "-c", cmd, cwd=d)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("bad", r.stdout)
            (d / "bad").write_text('#!/bin/bash\ncd /x || exit 1\n')
            self.assertEqual(run("bash", "-c", cmd, cwd=d).returncode, 0)


if __name__ == "__main__":
    unittest.main()
