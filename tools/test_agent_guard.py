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
    ("S1 hard cap 200k -> 180k", "SOFT, HARD = 180_000, 200_000", "SOFT, HARD = 180_000, 180_000"),
    ("ctx_tokens without the type=assistant filter", 'if d.get("type") != "assistant":', "if False:"),
    ("rootish: no project-dir branch", "return bool(pd and os.path.basename(a) in LOADED_DOCS and os.path.dirname(p) == os.path.realpath(pd))", "return False"),
    ("S2 keeps no tail", "so[:KEEP] + cut + so[-KEEP:]", "so[:KEEP] + cut"),
    ("poll session id not sanitised", 're.sub(r"\\W", "_", c.h.get("session_id") or "x")', '(c.h.get("session_id") or "x")'),
    ("E5 quick PASS blocks the full gate", "(want_quick or not quick_line)", "True"),
    ("E4 ignores cd", "cur = None if cur is None", "cur = cwd if True else None if cur is None"),
    ("S3a image skip removed", "if os.path.splitext(p)[1].lower() in NOLIMIT:", "if False:"),
    ("A1 subshell/$( ) end keeps the cd", 'if ("(" in op or ")" in op or op == "&") and cur != cwd:', "if False:"),
    ("A1 popd keeps the dir", 'elif h and h[0] == "popd":', 'elif False:'),
    ("A1 nested parse restarts at the hook cwd", "cur = None if n and moved else cwd", "cur = cwd"),
    ("A1 close after an empty segment not seen", 'elif out and ")" in t:', "elif False:"),
    ("A2 E4 only looks at w[0]", 'if not h or h[0] != "git":\n            continue\n        a, i, d', 'if os.path.basename(w[0]) != "git":\n            continue\n        h = ("git", w[1:])\n        a, i, d'),
    ("A2 command not a wrapper", '"watch", "command", "builtin"}', '"watch"}'),
    ("A2 -C $ not unknown", 'if a[i] == "-C":\n                unk |= "$" in val or "`" in val', 'if a[i] == "-C":\n                unk |= False'),
    ("A2 remote $ not unknown", 'unk |= "$" in remote or "`" in remote', "unk |= False"),
    ("A2 --git-dir ignored", 'gd = val', "gd = None"),
    ("A2 --git-dir= ignored", 'gd = a[i][10:]', "gd = None"),
    ("A4 S3b ignores walk dir", "any(rootish(x, wd) for x in h[1])", "any(rootish(x, c.cwd) for x in h[1])"),
    ("A5 task_status not keyed", 'default=str).encode()).hexdigest()[:12]', 'default=str).encode()).hexdigest()[:12] if c.t.endswith("t3_thread_read") else ""'),
    ("A6 quick fallback counted as quick", ' and "ran full" not in " ".join(line)', ""),
    ("A7 E5 path root ignored", 'if "/" in path else wd', "if False else wd"),
    ("A7 E5 unknown dir not skipped", " or wd is None:\n            continue\n        path", ":\n            continue\n        path"),
    ("A7 cd - treated as a dir", ' or t == "-" else', " else"),
    ("A8 limit 400 on a small file", "(HOT.search(p) or size > BIG) and (not lim or lim > 300)", "(not lim and (HOT.search(p) or size > BIG)) or lim > 300"),
    ("A1 E5 $ path and missing root both unguarded",
     '        if "$" in path or "`" in path:\n            continue   # unknown dir, as for wd None (a literal `$K` would join to a non-repo)\n        seen = True\n'
     '        base = os.path.normpath(os.path.join(wd, os.path.dirname(os.path.dirname(path)))) if "/" in path else wd\n'
     '        root = git(base, "rev-parse", "--show-toplevel")\n        if not root:\n            continue\n',
     '        seen = True\n        base = os.path.normpath(os.path.join(wd, os.path.dirname(os.path.dirname(path)))) if "/" in path else wd\n'
     '        root = git(base, "rev-parse", "--show-toplevel")\n'),
    ("A1 E5 path not expanduser'd", "os.path.expanduser(next(x for x in w if", "(next(x for x in w if"),
    ("A1 E5 no repo root not skipped", "        if not root:\n            continue\n", ""),
    ("A1 pre without per-rule guard", "        except Exception:  # noqa: BLE001 - one crashing rule", "        except KeyboardInterrupt:  # noqa: BLE001 - one crashing rule"),
    ("A2a push option values not skipped", "            if x in PUSH_VAL:", "            if False:"),
    ("A2a --repo ignored", 'repo = a[k + 1]', "repo = None"),
    ("A2b GIT_DIR= prefix ignored", "unk |= any(GITENV.match(x) for x in w[:gi])", "pass"),
    ("A2b export GIT_DIR ignored", "unk = h[1], 0, None, None, envset", "unk = h[1], 0, None, None, False"),
    ("A2c background cd kept", 'or op == "&") and cur != cwd', ') and cur != cwd'),
    ("A2d moved only first parse", "for p in ps for w, _ in p)", "for p in ps[:1] for w, _ in p)"),
    ("S6b case-sensitive", 'lines\\b", p or "", re.I)', 'lines\\b", p or "")'),
]


class AgentGuard(unittest.TestCase):
    def run_script(self, path):
        with tempfile.TemporaryDirectory(prefix="agent-guard-cwd-") as cwd:
            return subprocess.run([sys.executable, "-B", str(path), "--self-test"], capture_output=True, text=True, timeout=300, cwd=cwd)

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
                return name, self.run_script(d / f"m{k}" / "agent-guard").stdout

            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                for name, out in ex.map(run, range(len(MUTANTS))):
                    self.assertIn("RESULT: FAIL", out, "mutant survived: " + name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
