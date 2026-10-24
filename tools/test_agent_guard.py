"""tools/agent-guard --self-test must pass, and must kill real code mutants (thresholds, parsing, sanitising), not only verdict toggles."""
import concurrent.futures
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE / "agent-guard").read_text()

# (name, old text, new text): each must change the source and make --self-test FAIL
MUTANTS = [
    ("S1 hard cap 160k -> 100k", "SOFT, HARD = 100_000, 160_000", "SOFT, HARD = 100_000, 100_000"),
    ("ctx_tokens without the type=assistant filter", 'if d.get("type") != "assistant":', "if False:"),
    ("rootish: no project-dir branch", "return bool(pd and os.path.basename(a) in LOADED_DOCS and os.path.dirname(p) == os.path.realpath(pd))", "return False"),
    ("S2 keeps no tail", "so[:KEEP] + cut + so[-KEEP:]", "so[:KEEP] + cut"),
    ("poll session id not sanitised", 're.sub(r"\\W", "_", c.h.get("session_id") or "x")', '(c.h.get("session_id") or "x")'),
    ("E5 quick PASS blocks the full gate", "(want_quick or not quick_line)", "True"),
    ("E4 ignores cd", "cur = None if cur is None", "cur = cwd if True else None if cur is None"),
    ("S3a image skip removed", "if os.path.splitext(p)[1].lower() in NOLIMIT:", "if False:"),
    ("S6b case-sensitive", 'lines\\b", p or "", re.I)', 'lines\\b", p or "")'),
]


class AgentGuard(unittest.TestCase):
    def run_script(self, path):
        return subprocess.run([sys.executable, "-B", str(path), "--self-test"], capture_output=True, text=True, timeout=300)

    def test_self_test_passes(self):
        r = self.run_script(HERE / "agent-guard")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-500:])
        self.assertIn("RESULT: PASS", r.stdout)

    def test_code_mutants_are_killed(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="agent-guard-mut-"))
        try:
            def run(k):
                name, old, new = MUTANTS[k]
                self.assertEqual(SRC.count(old), 1, "mutant target not found exactly once: " + name)
                (d / f"m{k}").mkdir()
                for helper in ("phone-lock", "phone-guard"):
                    (d / f"m{k}" / helper).symlink_to(HERE / helper)
                (d / f"m{k}" / "agent-guard").write_text(SRC.replace(old, new))
                return name, self.run_script(d / f"m{k}" / "agent-guard").returncode

            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                for name, rc in ex.map(run, range(len(MUTANTS))):
                    self.assertNotEqual(rc, 0, "mutant survived: " + name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
