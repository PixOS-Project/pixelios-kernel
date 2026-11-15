"""tools/agent-guard --self-test must pass, and must kill real code mutants (thresholds, parsing, sanitising), not only verdict toggles."""
import concurrent.futures
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE / "agent-guard").read_text()
EXTRA = (HERE / "agent_guard_extra.py").read_text()   # rule bodies E26/E33 live here (R29)
TFOWN = (HERE / "agent_guard_tfown.py").read_text()   # agent-guard imports it too (T-2197); a mutant dir without it dies on import and every mutant "survives" with empty output (T-7d5f)
# the TF fixtures need the real super tools/triage-settle-guard; the mutant runs execute a copy of agent-guard outside the repo
_gc = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
# only when the caller did not set it (the remote gate container ships the file and sets it: its checkout /job has no super repo three levels up, parents[2] raised IndexError there; T-78bc)
_gp = pathlib.Path(_gc).resolve().parents if _gc else []
if "AGENT_GUARD_TRIAGE_MOD" not in os.environ and len(_gp) > 2:
    os.environ["AGENT_GUARD_TRIAGE_MOD"] = str(_gp[2] / "tools" / "triage-settle-guard")
os.environ.setdefault("AGENT_GUARD_EXTRA_DIR", str(HERE))   # the mutant runs execute a copy outside tools/ that still imports agent_guard_extra

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
    ("A1 pre without per-rule guard", "        except Exception as x:  # noqa: BLE001 - one crashing rule", "        except KeyboardInterrupt as x:  # noqa: BLE001 - one crashing rule"),
    ("A2a push option values not skipped", "            if x in PUSH_VAL:", "            if False:"),
    ("A2a --repo ignored", 'repo = a[k + 1]', "repo = None"),
    ("A2b GIT_DIR= prefix ignored", "unk |= any(GITENV.match(x) for x in w[:gi])", "pass"),
    ("A2b export GIT_DIR ignored", "unk = h[1], 0, None, None, envset", "unk = h[1], 0, None, None, False"),
    ("A2c background cd kept", 'or op == "&") and cur != cwd', ') and cur != cwd'),
    ("A2d moved only first parse", "for p in ps for w, _ in p)", "for p in ps[:1] for w, _ in p)"),
    ("S6b case-sensitive", 'lines\\b", p or "", re.I)', 'lines\\b", p or "")'),
    ('E17 -- does not end the operands', 'if x == "--" or (h[0] == "find"', 'if (h[0] == "find"'),
    ('E15 -maxdepth ignored', '(md is None or md > 3)', 'True'),
    ('E22 merge in progress not exempt', 'if any(os.path.exists(os.path.join(d, x)) for x in seq):', 'if False:'),
    ('E22 tools/<slug> branch not allowed', ' or br.startswith("tools/"):', ':'),
    ('E8b cp source counted as written', 'cand = [dst] + (ops[:-1] if prog == "mv" else [])', 'cand = ops'),
    ('E18 pattern operand counted as a file', 'files = ops if pat else ops[1:]', 'files = ops'),
    ('E2c split $( pgrep ) not seen', ' or (w[-1].endswith("$") and "(" in op and nxt and nxt[0] in LOOKUP)', ''),
    ('E13 host-test loop not found', 'if m and any("wlanlink_host_test$n" in x for x in t[i + 1:i + 4]):', 'if m and False:'),
    ('E14 no margin', 'ms < (need + 60) * 1000', 'ms < need * 1000'),
    ('E8b git -C not followed', 'if cv and "$" not in cv and "`" not in cv:', 'if False:'),
    ('E22 shared-tool filter widened to all tools/', 'tf = [f for f in files if SHARED_TOOLS.match(f)]', 'tf = [f for f in files if f.startswith("tools/")]'),
    ('E22 git add in another repo counted', 'if os.path.realpath(top) == os.path.realpath(root):', 'if True:'),
    ('E22 commit PATH ignored', 'files += pathspec_files(d, named_ops(ca, COMMIT_VAL))', 'pass'),
    ('E22 build.sh with shim code denied', ' and f != "shim/build.sh" for f in files', ' for f in files'),
    ('E8b git rewrite in shared clone ignored', 'if gd and os.path.realpath(gd) == os.path.realpath(SHARED) and not', 'if False and not'),
    ('E18 -I accepted as text', '"--text", "--binary-files=text", "-uuu")', '"--text", "-I", "--binary-files=text", "-uuu")'),
    ('E14 xrun default ignored', 'n = 300         #', 'n = 0         #'),
    ('E14 lock-wait ignored', 'need = max(need, n + (int(lw) if lw and lw.isdigit() else 0))', 'need = max(need, n)'),
    ('E23 unknown intent accepted', 'if v is None or (v != "none" and v not in intent_names()):', 'if v is None:'),
    ('E23 phone-lock run xrun not seen', 'elif p == "phone-lock" and "run" in a:', 'elif False:'),
    ('E23 --dry-run not exempt', 'runs = [a for a in e23_runs(c.heads) if not ({"--dry-run", "--help", "-h"} & xrun_walk(a)[0])]', 'runs = e23_runs(c.heads); pass'),
    ('E23 pix xrun not seen', 'elif p == "pix" and a[:1] == ["xrun"]:', 'elif False:'),
    ('E23 phone-lock bash -c not re-parsed', 'elif depth < 3 and "--" in a:', 'elif False:'),
    ('E23 option value taken for a flag', 'if a[i] in XRUN_VAL:', 'if False:'),
    ('prep: # inside a word is a comment', 'elif ch == "#" and wb:', 'elif ch == "#":'),
    ('prep: backslash-newline kept', 'if cmd[i + 1] != "\\n":', 'if True:'),
    ('E10 poke inside phone-lock run not seen', 'if p == "phone-lock" or (preflight and p == "run-preflight"):', 'if preflight and p == "run-preflight":'),
    ('E2c for-loop variable over pgrep not tainted', 'taint.add(w[1])', 'pass'),
    ('E2c tainted variable in kill not seen', 'elif refs(h[1]):', 'elif False:'),
    ('E18 logs/runs not covered', 'elif not text and any(s in x for x in full for s in RUN_LOGS):', 'elif False:'),
    ('E25 clk read not flagged', 'hit = m.group(1)', 'pass'),
    ("S4b plan check allows any role", 'st not in ("researcher", "reviewer")', "False"),
    ("S4b plan check allows an implementing brief", '|apply the patch)\\b", c.prompt', '|apply the patch)\\bX", c.prompt'),
    ("S4b plan check needs no PLAN-CHECK marker", "if not m or st not in", "if st not in"),
    ('E26 unittest file path not flagged', 'bad = [x for a in um if "discover" not in a for x', 'bad = [x for a in um if False for x'),
    ('E27 piped self-test not flagged', '"--self-test" in h[1] and op == "|"', '"--self-test" in h[1] and False'),
    ('E28 Bash unshare/pid-1 guard ignored', 'guarded = PID1_CHECK.search(c.cmd) or', 'guarded = None or'),
    ('E28 Write pid-1 check ignored', 'return verdict("E28", True, "" if PID1_CHECK.search(text) else KILL_ALL_MSG)', 'return verdict("E28", True, KILL_ALL_MSG)'),
    ('E27 text loses its Instead', 'Instead: `tools/probe-run --self-test > /tmp/x.out 2>&1 < /dev/null`, then grep the file. ', ''),
    ('E26 text loses its Instead and backticks', 'Instead: `python3 -m unittest discover -s /abs/path/tools -p test_NAME.py` (absolute paths, no cd: AP-222). ', ''),
    ('E17 (non-THREE_PART) text loses its Instead', 'Instead: use `./{x}` or put', 'use `./{x}` or put'),
    ('E28 text loses its Escape (E28 in THREE_PART)', 'Escape: none (a pid-1 check or unshare -p in the same file/command passes).', 'none (a pid-1 check or unshare -p in the same file/command passes).'),
    ('E17 text loses its backticks (Instead needs a command)', 'Instead: use `./{x}` or put `--` before', 'Instead: use ./{x} or put -- before'),
    ('E2c kill -0 probe denied', 'if kill_probe(h[1]):', 'if False:'),
    ('E2c kill $(ps ..) not seen', '(\\$\\(|`)\\s*(pgrep|pidof|ps)\\b", x) for x in h[1])', '(\\$\\(|`)\\s*(pgrep|pidof)\\b", x) for x in h[1])'),
    ('E2c for over a tainted variable not followed', '(sub or btk or refs(w[3:]))', '(sub or btk)'),
    ('E2c echo $tainted | xargs kill not seen', ' or (h[0] in ("echo", "printf") and refs(h[1]))', ''),
    ('E28 kill operand -01 not matched', 'any(re.fullmatch(r"-0*1", x) or any(', 'any(re.fullmatch(r"-1", x) or any('),
    ('E28 variable holding -1 not followed', 'neg.add(m.group(1))', 'pass'),
    ('E28 killall5 not seen', 'if h[0] == "killall5":', 'if False:'),
    ('E28 unshare -f counted as a pid namespace', 'if re.fullmatch(r"-[A-Za-z]*p[A-Za-z]*", x) or x.startswith("--pid"):', 'if re.fullmatch(r"-[A-Za-z]*[pf][A-Za-z]*", x) or x.startswith("--pid"):'),
    ('E28 command after unshare not followed', 'if h and h[0] == "unshare" and depth < 3:', 'if False:'),
    ('E28 os.kill(~0) not matched', '|~\\s*0\\b)', ')'),
    ('E28 perl kill(SIG, -1) not matched', '|\\bkill\\s*\\(?\\s*[\\w-]+\\s*,\\s*-\\s*0*1\\b', ''),
    ('E28 comment lines scanned', 'if not l.lstrip().startswith("#"))', 'if True)'),
    ('E28 quotes kept in file text', '.replace(\'"\', "").replace("\'", "")', ''),
    ('E28 NAME=-1 then os.kill(NAME) not followed', 'for m in re.finditer(r"\\b(\\w+)\\s*=\\s*-\\s*0*1\\b", t):', 'for m in []:'),
    ('E29 commit --no-verify not flagged', 'elif sub in ("commit", "merge", "am") and ("--no-verify" in rest or', 'elif sub in ("commit", "merge", "am") and (False or'),
    ('E29 commit -n cluster not flagged', 'any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", x) for x in rest)', 'False'),
    ('E29 -c core.hooksPath not flagged', 'a[k + 1].lower().startswith("core.hookspath")', 'False'),
    ('E29 config core.hooksPath VALUE not flagged', 'if names and (names[0] + 1 < len(ops) or', 'if names and (False or'),
    ('E29 thread id on the git command not flagged', 'if any("T3CODE_THREAD_ID" in x for x in pre):', 'if False:'),
    ('E29 unset thread id not flagged', 'if not bad and git_heads and any(p == "unset"', 'if False and any(p == "unset"'),
    ('E31 --all not flagged', 'if x == "--all":\n            return True', 'if False:\n            return True'),
    ('E31 short -a cluster not flagged', 'if ch == "a":\n                    return True', 'if False:\n                    return True'),
    ('E31 -m value scanned as a cluster', 'if x in COMMIT_VAL:\n            k += 2\n            continue\n        step = 1', 'step = 1'),
    ('E31 args after -- scanned', 'if x == "--":\n            return False\n        if x == "--all":', 'if x == "--all":'),
    ('E31 text loses its Escape (E31 in THREE_PART)', '(new files first: `git add PATH`). Escape: none', '(new files first: `git add PATH`). none'),
    ('E29 text loses its Escape (E29 in THREE_PART)', 'Escape: none (a human may run it outside the agent).', 'none (a human may run it outside the agent).'),
    ("TF a launch message lifts the rule", 'if k == 0 and cls != "interactive":', "if False:"),
    ("TF a <wrapper> line counts as a user prompt", 'if s and not s.startswith("<"):', "if s:"),
    ("TF gitignored files are protected", 'if top and git(d, "check-ignore", "-q", p) is not None:', "if False:"),
    ("TF a worktree of the super repo is not found via the common dir", 'base = os.path.dirname(common) if os.path.basename(common) == ".git" else ""', 'base = ""'),
    ("TF the queue dir is protected", 'rel.startswith(("docs/queue/", "logs/"))', 'rel.startswith(("logs/",))'),
    ("TF any ticket status owns the thread", 'if h.get("status", "").strip() == "running" and h.get("owner", "").strip() == thread:', 'if h.get("owner", "").strip() == thread:'),
    ("TF a triage run is a chat", 'cls = "triage" if prompts and prompts[0].startswith(m.PROMPT) else', 'cls = "interactive" if False else'),
    ("TF an escape is not read", "        e = TF_ESC.search(s)\n", "        e = None\n"),
    ("E28 any program's args scanned as code",'elif CODE_PROGS.fullmatch(h[0]):', 'elif True:'),
    ('E28 shell forms scanned inside python strings', 'if kill_all_text(code, bool(re.search(r"os\\.system|subprocess|popen|\\bsystem\\(", code))):', 'if kill_all_text(code, True):'),
    ('E28 heredoc into a .md/.txt file scanned', '.strip("\'\\"").endswith((".md", ".txt"))):\n            if kill_all_text(body):', '.strip("\'\\"").endswith(("zz",))):\n            if kill_all_text(body):'),
    ('S9 queue-missing hint never added', 'hint = QUEUE_HINT if verdict("S9"', 'hint = "" if verdict("S9"'),
    ('S9 hint regex drops the python form', "|can't open file '[^']*tools/queue'", ""),
    ('E32 rule removed from RULES', '("E31", "deny", r_e31), ("E32", "deny", r_e32),', '("E31", "deny", r_e31),'),
    ('E32 destination under pixos-project not judged', 'for x in dests[:1])', 'for x in [])'),
    ('E32 clone detection by common dir dropped', 'in_clone = bool(common and "/pixos-project/" in os.path.realpath(common) + "/")', 'in_clone = False'),
    ('E32 escape never honoured', 'if hand:\n', 'if False:\n'),
    ('E32 empty escape value honoured', 'HAND_RX = re.compile(r"^PIXOS_WORKTREE_HAND=(.+)$", re.S)', 'HAND_RX = re.compile(r"^PIXOS_WORKTREE_HAND=(.*)$", re.S)\n_E31_EMPTY_OK = True'),
    ('E32 text loses the sanctioned command', 'Instead: `tools/queue worktree T-xxxx --kernel` (kernel worktree', 'Instead: ask someone (kernel worktree'),
    ('E28 heredoc fed to python not scanned', 'if re.match(r"\\s*(?:sudo\\s+)?(?:python[\\d.]*|perl|ruby|node|nodejs)\\b", intro):', 'if False:'),
]


class AgentGuard(unittest.TestCase):
    def run_script(self, path, fail_fast=False):
        with tempfile.TemporaryDirectory(prefix="agent-guard-cwd-") as cwd:   # a mutant dir holds its own agent_guard_extra.py: the module is found next to the script
            env = dict(os.environ, AGENT_GUARD_EXTRA_DIR=str(pathlib.Path(path).parent))
            if fail_fast:   # a mutant needs only the verdict FAIL; the unmutated self-test below still runs every check
                env["AGENT_GUARD_FAIL_FAST"] = "1"
            return subprocess.run([sys.executable, "-B", str(path), "--self-test"], capture_output=True, text=True, timeout=900, cwd=cwd, env=env)

    def test_self_test_passes(self):
        r = self.run_script(HERE / "agent-guard")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-500:])
        self.assertIn("RESULT: PASS", r.stdout)

    def test_code_mutants_are_killed(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="agent-guard-mut-"))
        try:
            def run(k):
                name, old, new = MUTANTS[k]
                self.assertEqual(SRC.count(old) + EXTRA.count(old) + TFOWN.count(old), 1, "mutant target not found exactly once (agent-guard + agent_guard_extra.py + agent_guard_tfown.py): " + name)
                (d / f"m{k}").mkdir()
                for helper in ("phone-lock", "phone-guard"):
                    (d / f"m{k}" / helper).symlink_to(HERE / helper)
                (d / f"m{k}" / "agent-guard").write_text(SRC.replace(old, new))
                (d / f"m{k}" / "agent_guard_extra.py").write_text(EXTRA.replace(old, new))
                (d / f"m{k}" / "agent_guard_tfown.py").write_text(TFOWN.replace(old, new))
                return name, self.run_script(d / f"m{k}" / "agent-guard", fail_fast=True).stdout

            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                for name, out in ex.map(run, range(len(MUTANTS))):
                    self.assertIn("RESULT: FAIL", out, "mutant survived: " + name)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_session_state_is_live_not_a_snapshot(self):
        """R41 H1: a stale committed docs/state.md must never be injected; the block comes from tools/gen-state.py now, and a failing generator is loud."""
        import json
        import os
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "docs").mkdir()
            (pathlib.Path(d) / "tools").mkdir()
            (pathlib.Path(d) / "docs/state.md").write_text("STALE SNAPSHOT 2026-10-25\n")
            gen = pathlib.Path(d) / "tools/gen-state.py"
            gen.write_text("import sys\nprint('LIVE STATE')\nprint('RESULT: PASS state.md 1 chars')\n")
            env = dict(os.environ, CLAUDE_PROJECT_DIR=d)
            r = subprocess.run([sys.executable, str(HERE / "agent-guard"), "--session"], input="{}", capture_output=True, text=True, env=env, timeout=30)
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("LIVE STATE", ctx)
            self.assertNotIn("STALE SNAPSHOT", ctx)
            self.assertNotIn("RESULT:", ctx)
            gen.write_text("import sys\nsys.exit(3)\n")
            r = subprocess.run([sys.executable, str(HERE / "agent-guard"), "--session"], input="{}", capture_output=True, text=True, env=env, timeout=30)
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("SESSION STATE UNAVAILABLE", ctx)
            self.assertNotIn("STALE SNAPSHOT", ctx)


    def test_hand_made_kernel_worktree_is_denied_through_the_hook(self):
        """T-82cb: `git worktree add` inside a pixos-project clone is denied by the real --pre hook with the sanctioned command named; the escape allows and is logged."""
        import json
        import os
        with tempfile.TemporaryDirectory() as d:
            sup = pathlib.Path(d) / "sup"
            clone = sup / "pixos-project" / "kern"
            clone.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(clone)], check=True)
            (sup / "logs").mkdir()
            env = dict(os.environ, QUEUE_ROOT=str(sup), CLAUDE_PROJECT_DIR=str(sup))

            def pre(cmd):
                h = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(clone)}
                r = subprocess.run([sys.executable, str(HERE / "agent-guard"), "--pre"], input=json.dumps(h), capture_output=True, text=True, env=env, timeout=60)
                return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

            out = pre("git worktree add -b x %s/wt" % d)
            self.assertEqual(out["permissionDecision"], "deny", out)
            self.assertIn("tools/queue worktree T-xxxx --kernel", out["permissionDecisionReason"])
            self.assertIn("PIXOS_WORKTREE_HAND", out["permissionDecisionReason"])
            self.assertIsNone(pre('PIXOS_WORKTREE_HAND="sandbox" git worktree add -b y %s/wt2' % d))
            self.assertIn("reason=sandbox", (sup / "logs" / "ticket-first.log").read_text())
            self.assertIsNone(pre("git worktree list"))


if __name__ == "__main__":
    # T-82cb: a closing RESULT line (the ticket Done-when quotes `RESULT: PASS cases=N`)
    res = unittest.main(exit=False, verbosity=1).result
    ok = res.wasSuccessful()
    print("RESULT: %s cases=%d (agent-guard self-test, code mutants, session state, hand-made kernel worktree denied)" % ("PASS" if ok else "FAIL", res.testsRun))
    sys.exit(0 if ok else 1)
