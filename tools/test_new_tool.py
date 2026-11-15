#!/usr/bin/env python3
"""tools/new-tool refuses bad input and never overwrites; the success path is checked on a copy of the tools dir."""
import os, shutil, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
NT = os.path.join(TOOLS, "new-tool")


def run(*a):
    return subprocess.run([sys.executable, NT, *a], capture_output=True, text=True)


class NewTool(unittest.TestCase):
    def test_refusals(self):
        self.assertEqual(run().returncode, 2)
        self.assertIn("name must match", run("Bad Name", "--purpose", "x" * 20).stdout)
        self.assertIn("--purpose needs", run("good-name").stdout)
        self.assertIn("exists", run("gate", "--purpose", "x" * 20).stdout)

    def test_success_writes_skeleton_test_and_row(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "tools"); os.makedirs(t)
            shutil.copy(NT, t)
            open(os.path.join(t, "tools-lifecycle.tsv"), "w").write("date\ttool\told\tnew\treason\tevidence\n")
            open(os.path.join(t, "tool-census"), "w").write("#!/usr/bin/env python3\nprint('RESULT: PASS stub census')\n")
            r = subprocess.run([sys.executable, os.path.join(t, "new-tool"), "demo-tool", "--purpose", "demo of the scaffolder, called by nothing"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.access(os.path.join(t, "demo-tool"), os.X_OK))
            self.assertIn("demo-tool\t-\tactive", open(os.path.join(t, "tools-lifecycle.tsv")).read())
            s = subprocess.run([sys.executable, os.path.join(t, "test_demo_tool.py")], capture_output=True, text=True)
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
            st = subprocess.run([sys.executable, os.path.join(t, "demo-tool"), "--self-test"], capture_output=True, text=True)   # the skeleton new-tool made runs its own --self-test
            self.assertEqual(st.returncode, 0, st.stdout + st.stderr)

    def test_precommit_check_denies_a_hand_made_tool(self):
        with tempfile.TemporaryDirectory() as d:
            def g(*a): return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)
            g("init", "-q"); os.makedirs(os.path.join(d, "tools"))
            open(os.path.join(d, "tools", "tools-lifecycle.tsv"), "w").write("date\ttool\told\tnew\treason\tevidence\n")
            g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            open(os.path.join(d, "tools", "hand-made"), "w").write("#!/bin/sh\n"); g("add", "tools/hand-made")
            r = subprocess.run([sys.executable, NT, "--check-staged"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("tools/new-tool NAME --purpose", r.stderr)
            open(os.path.join(d, "tools", "tools-lifecycle.tsv"), "a").write("2026-11-02\thand-made\t-\tactive\twhy\tev\n"); g("add", "tools/tools-lifecycle.tsv")
            r = subprocess.run([sys.executable, NT, "--check-staged"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            open(os.path.join(d, "tools", "test_x.py"), "w").write(""); g("add", "tools/test_x.py")   # a test file needs no row
            self.assertEqual(subprocess.run([sys.executable, NT, "--check-staged"], cwd=d, capture_output=True, text=True).returncode, 0)

    def test_precommit_check_denies_an_executable_without_help(self):   # 2026-11-10: 49 tools ran on a --help probe, one leaked a loop
        with tempfile.TemporaryDirectory() as d:
            def g(*a): return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)
            def chk(**env): return subprocess.run([sys.executable, NT, "--check-staged"], cwd=d, capture_output=True, text=True, env=dict(os.environ, **env))
            def put(name, text, mode=0o755):
                p = os.path.join(d, "tools", name); open(p, "w").write(text); os.chmod(p, mode); g("add", "tools/" + name)
            g("init", "-q"); os.makedirs(os.path.join(d, "tools"))
            rows = "date\ttool\told\tnew\treason\tevidence\n" + "".join("2026-11-11\t%s\t-\tactive\twhy\tev\n" % n for n in ("a.sh", "b", "c", "d", "e"))
            open(os.path.join(d, "tools", "tools-lifecycle.tsv"), "w").write(rows)
            g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            put("a.sh", "#!/bin/bash\n# a: does things\nrm -rf \"$1\"\n")
            r = chk()
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("new-tool: DENIED tools/a.sh is executable but has no -h/--help", r.stderr)
            self.assertIn('case "${1:-}" in -h|--help)', r.stderr)   # the do-instead is the exact snippet
            self.assertIn("RESULT: FAIL new-tool help-staged denied=1", r.stdout)
            e = chk(PIXOS_NO_HELP_HANDLER="vendored script, never run by hand")
            self.assertEqual(e.returncode, 0, e.stdout + e.stderr)
            self.assertIn("NOTE (escaped: vendored script, never run by hand)", e.stderr)
            put("a.sh", "#!/bin/bash\n# a: does things\n# T-7dff:help: -h/--help prints this header\ncase \"${1:-}\" in -h|--help) exit 0;; esac\nrm -rf \"$1\"\n")
            self.assertEqual(chk().returncode, 0, chk().stderr)
            put("b", "#!/usr/bin/env python3\nimport argparse\nargparse.ArgumentParser().parse_args()\n")      # argparse answers --help itself
            put("c", "#!/bin/sh\necho hi\n", 0o644)                                                         # not executable: not a tool run by path
            put("d", "not a script\n")                                                                        # no shebang
            r = chk()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            put("e", "#!/usr/bin/env python3\nimport sys\nprint(sys.argv)\n")                                # python without a handler
            r = chk()
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("DENIED tools/e ", r.stderr)
            self.assertIn('if __import__("sys").argv[1:2] in (["-h"], ["--help"])', r.stderr)

    def test_skeleton_answers_help_and_passes_the_check(self):
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "tools"); os.makedirs(t)
            for f in ("new-tool", "tool-census"):
                if os.path.exists(os.path.join(TOOLS, f)):
                    shutil.copy(os.path.join(TOOLS, f), os.path.join(t, f))
            open(os.path.join(t, "tools-lifecycle.tsv"), "w").write("date\ttool\told\tnew\treason\tevidence\n")
            subprocess.run([sys.executable, os.path.join(t, "new-tool"), "skel-tool", "--purpose", "a skeleton for the help test"], capture_output=True, text=True)
            h = subprocess.run([sys.executable, os.path.join(t, "skel-tool"), "--help"], capture_output=True, text=True)
            self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
            self.assertTrue(h.stdout.startswith("usage: skel-tool\nskel-tool: a skeleton for the help test"), h.stdout)
            src = open(os.path.join(t, "skel-tool")).read()
            self.assertIn("T-7dff:help", src)


if __name__ == "__main__":
    unittest.main()
