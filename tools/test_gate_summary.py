#!/usr/bin/env python3
"""Tests for tools/gate-summary (PLAN-GATE-2X D6, G1, G2): the RESULT line over a fixture gate.progress / gate.result (PASS and FAIL, slow= present and "-",
skipped count, base sha), --slow, --append (ledger row columns), the exit codes, and MUTANTS of the tool the suite must kill (honest edits of its source text,
each asserted to apply; a mutant that survives fails the suite).

  python3 -I tools/test_gate_summary.py        last line: RESULT: PASS|FAIL test_gate_summary tests=N mutants=K/M
"""
import contextlib, glob, importlib.machinery, importlib.util, io, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

TOOLS = pathlib.Path(__file__).resolve().parent
TOOL = TOOLS / "gate-summary"
# fixture manifest: 5 steps (so skipped = 5 - ran), budgets build 60 / unit 180 / wlan-mutants 300, totals as the real one
MANIFEST = {"steps": [{"step": "build", "budget_s": 60}, {"step": "unit", "budget_s": 180}, {"step": "wlan-mutants", "budget_s": 300},
                      {"step": "rules", "budget_s": 30}, {"step": "validate", "budget_s": 60}], "totals_s": {"quick": 360, "formal": 600, "cold": 1100}}
# build ends AT its budget (60: not slow), unit 200 > 180 (slow); wlan-mutants was stopped (not run); BASE recorded
PROGRESS = "BASE abc123def456\nSTART build 1000\nEND build PASS 60\nSTART unit 1000\nEND unit PASS 200\nSTART wlan-mutants 1000\nEND wlan-mutants STOPPED\n"
PROGRESS_FAST = "BASE abc123def456\nSTART build 1000\nEND build PASS 60\nSTART unit 1000\nEND unit PASS 100\n"


def load(path):
    loader = importlib.machinery.SourceFileLoader("gate_summary_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = mod.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Fix:
    """A temp dir with manifest + progress + result; call(...) runs main on them."""

    def __init__(self, mod, d, progress, result):
        self.mod, self.d = mod, pathlib.Path(d)
        (self.d / "m.json").write_text(json.dumps(MANIFEST))
        (self.d / "p").write_text(progress)
        (self.d / "r").write_text(result)

    def args(self, *extra):
        return ["--progress", str(self.d / "p"), "--result", str(self.d / "r"), "--manifest", str(self.d / "m.json"), *extra]

    def call(self, *extra):
        return run(self.mod, self.args(*extra))


def behaviour(mod):
    """Every check against module MOD -> list of failure descriptions (empty = all good). The mutants run this same function."""
    bad = []

    def eq(name, got, want):
        if got != want:
            bad.append("%s: got %r want %r" % (name, got, want))

    with tempfile.TemporaryDirectory() as d:
        def fx(progress, result):
            return Fix(mod, d, progress, result)

        # 1. quick PASS: unit over budget -> slow=unit:200/180; build exactly at budget is NOT slow; stopped step not counted as run; elapsed = result epoch - first START
        rc, out, _ = fx(PROGRESS, "GATE PASS 0676d2a quick: ran 2 of 5 steps 1250\ngate: skipped rules: no input changed\n").call()
        eq("quick PASS line", out, "RESULT: PASS gate kind=quick elapsed=250s ran=2 skipped=3 slow=unit:200/180 base=abc123def456\n")
        eq("quick PASS rc", rc, 0)
        # 2. nothing over budget: slow=-
        rc, out, _ = fx(PROGRESS_FAST, "GATE PASS 0676d2a quick: ran 2 of 5 steps 1250\n").call()
        eq("slow=- line", out, "RESULT: PASS gate kind=quick elapsed=250s ran=2 skipped=3 slow=- base=abc123def456\n")
        # 3. total budget: quick 360 exactly is not a breach, 361 is (slow=total:361/360)
        eq("total at budget", fx(PROGRESS_FAST, "GATE PASS h quick: ran 2 of 5 steps 1360\n").call()[1].split(" slow=")[1].split()[0], "-")
        eq("total over budget", fx(PROGRESS_FAST, "GATE PASS h quick: ran 2 of 5 steps 1361\n").call()[1].split(" slow=")[1].split()[0], "total:361/360")
        # 3b. elapsed counts from the FIRST step start (lanes start together; a late START must not shorten it)
        eq("elapsed from first START", fx("START build 1000\nEND build PASS 5\nSTART unit 1050\nEND unit PASS 5\n", "GATE PASS h quick: ran 2 of 5 steps 1100\n").call()[1].split()[4], "elapsed=100s")
        # 4. formal kind: plain PASS, "ran full" fallbacks; formal total 600
        for tag in ("", " quick: no usable stamp, ran full", " quick: selected all steps, ran full"):
            eq("formal kind for %r" % tag, fx(PROGRESS_FAST, "GATE PASS h%s 1300\n" % tag).call()[1].split()[3], "kind=formal")
        eq("formal total not the quick one", fx(PROGRESS_FAST, "GATE PASS h 1400\n").call()[1].split(" slow=")[1].split()[0], "-")
        eq("formal total over", fx(PROGRESS_FAST, "GATE PASS h 1601\n").call()[1].split(" slow=")[1].split()[0], "total:601/600")
        # 5. FAIL: verdict FAIL, a quick FAIL line is still kind quick, rc 0 unless --exit-code
        f = fx(PROGRESS.replace("END unit PASS 200", "END unit FAIL 50"), "GATE FAIL 0676d2a quick: unit 1100\n")
        rc, out, _ = f.call()
        eq("FAIL line", out, "RESULT: FAIL gate kind=quick elapsed=100s ran=2 skipped=3 slow=- base=abc123def456\n")
        eq("FAIL rc without --exit-code", rc, 0)
        eq("FAIL rc with --exit-code", f.call("--exit-code")[0], 1)
        eq("PASS rc with --exit-code", fx(PROGRESS_FAST, "GATE PASS h quick: ran 2 of 5 steps 1250\n").call("--exit-code")[0], 0)
        # 6. a timeout FAIL keeps its seconds; a FAIL step that is over budget is still slow
        eq("timeout END", fx("START unit 1000\nEND unit FAIL (timeout) 300\n", "GATE FAIL h quick: unit (timeout) 1300\n").call()[1].split(" slow=")[1].split()[0], "unit:300/180")
        # 7. no BASE line: base=-
        eq("no BASE", fx("START build 1000\nEND build PASS 5\n", "GATE PASS h 1010\n").call()[1].split()[-1], "base=-")
        # 8. unfinished (RUNNING) result / missing progress: ERROR rc 2, never a made-up PASS
        rc, out, _ = fx(PROGRESS, "RUNNING 123 1000\n").call()
        eq("RUNNING rc", rc, 2)
        eq("RUNNING line", out.startswith("RESULT: ERROR"), True)
        rc, out, _ = run(mod, ["--progress", str(pathlib.Path(d) / "nope"), "--result", str(pathlib.Path(d) / "r"), "--manifest", str(pathlib.Path(d) / "m.json")])
        eq("missing progress", (rc, out.startswith("RESULT: ERROR")), (2, True))
        # 9. --slow prints only the list (empty when nothing is over budget); --kind/--elapsed decide the total
        f = fx(PROGRESS, "")
        eq("--slow list", f.call("--slow", "--kind", "quick", "--elapsed", "250")[1], "unit:200/180\n")
        eq("--slow total", f.call("--slow", "--kind", "quick", "--elapsed", "400")[1], "unit:200/180,total:400/360\n")
        eq("--slow formal 400 ok", f.call("--slow", "--kind", "formal", "--elapsed", "400")[1], "unit:200/180\n")
        eq("--slow empty", fx(PROGRESS_FAST, "").call("--slow", "--kind", "quick", "--elapsed", "10")[1], "\n")
        # 10. --append: one row, columns epoch label kind host base ran skipped elapsed steps; stopped step has no seconds
        led = pathlib.Path(d) / "led" / "gate-times.tsv"
        f = fx(PROGRESS, "")
        rc, out, _ = f.call("--append", "--line", "GATE PASS 0676d2a quick: ran 2 of 5 steps", "--elapsed", "250", "--label", "gate-wt", "--host", "offload-host", "--ledger", str(led))
        eq("append rc", rc, 0)
        eq("append reports the PASS verdict", out.rstrip().endswith("verdict=PASS"), True)
        rows = led.read_text().splitlines() if led.exists() else []
        eq("append one row", len(rows), 1)
        cols = rows[0].split("\t") if rows else []
        eq("append row fields", len(cols), 10)
        if len(cols) == 10:
            eq("append epoch numeric", cols[0].isdigit(), True)
            eq("append row", cols[1:], ["gate-wt", "quick", "offload-host", "abc123def456", "2", "3", "250", "build:60,unit:200", "0"])
        out = f.call("--append", "--line", "GATE FAIL h interrupted", "--elapsed", "7", "--label", "w2", "--host", "h2", "--ledger", str(led))[1]
        eq("append reports the FAIL verdict", out.rstrip().endswith("verdict=FAIL"), True)
        rows = led.read_text().splitlines()
        eq("append is append-only", (len(rows), rows[-1].split("\t")[2:4] if len(rows) == 2 else None), (2, ["formal", "h2"]))
        # elapsed defaults to the progress file when --elapsed is absent
        f.call("--append", "--line", "GATE PASS h quick: ran 2 of 5 steps", "--label", "w3", "--host", "h3", "--ledger", str(led))
        eq("append elapsed fallback", led.read_text().splitlines()[-1].split("\t")[7].isdigit(), True)
        # 11. COLD (PLAN-GATE-2X K0): the ` cold=1` token on the GATE line -> `cold=1` on the RESULT line, cold total budget 1100 s, the ledger column `cold` (last)
        eq("cold RESULT line", fx(PROGRESS_FAST, "GATE PASS h cold=1 1300\n").call()[1],
           "RESULT: PASS gate kind=formal cold=1 elapsed=300s ran=2 skipped=3 slow=- base=abc123def456\n")
        eq("warm RESULT line has no cold=", "cold=" in fx(PROGRESS_FAST, "GATE PASS h 1300\n").call()[1], False)
        eq("cold FAIL line", fx(PROGRESS_FAST, "GATE FAIL h cold=1 (see log) 1300\n").call()[1].startswith("RESULT: FAIL gate kind=formal cold=1 elapsed=300s"), True)
        eq("cold token must be a whole word", ["cold=" in fx(PROGRESS_FAST, "GATE PASS h %s 1300\n" % t).call()[1] for t in ("xcold=1", "cold=10", "cold=0")], [False, False, False])
        eq("cold total: 1100 is no breach", fx(PROGRESS_FAST, "GATE PASS h cold=1 2100\n").call()[1].split(" slow=")[1].split()[0], "-")
        eq("cold total: 1101 is slow", fx(PROGRESS_FAST, "GATE PASS h cold=1 2101\n").call()[1].split(" slow=")[1].split()[0], "total:1101/1100")
        eq("cold run is not judged by the warm formal total", fx(PROGRESS_FAST, "GATE PASS h cold=1 1600\n").call()[1].split(" slow=")[1].split()[0], "-")
        eq("warm formal 601 stays slow", fx(PROGRESS_FAST, "GATE PASS h 1601\n").call()[1].split(" slow=")[1].split()[0], "total:601/600")
        eq("cold run: warm per-step budgets do not apply (unit 200 > 180)", fx(PROGRESS, "GATE PASS h cold=1 1300\n").call()[1].split(" slow=")[1].split()[0], "-")
        eq("--slow --cold", (f.call("--slow", "--kind", "formal", "--elapsed", "1200", "--cold")[1], f.call("--slow", "--kind", "formal", "--elapsed", "1100", "--cold")[1]),
           ("total:1200/1100\n", "\n"))
        crow = lambda line: (f.call("--append", "--line", line, "--elapsed", "1500", "--label", "cw", "--host", "h", "--ledger", str(led)), led.read_text().splitlines()[-1].split("\t"))[1]
        c = crow("GATE PASS h cold=1 slow=total:1500/1100 1700")
        eq("cold row: 10 columns, last = 1", (len(c), c[-1], c[2]), (10, "1", "formal"))
        c = crow("GATE FAIL h cold=1 interrupted")
        eq("cold FAIL row", (len(c), c[-1]), (10, "1"))
        c = crow("GATE PASS h 1700")
        eq("warm row: last = 0", (len(c), c[-1]), (10, "0"))
        c = crow("GATE PASS h cold=10 1700")
        eq("cold=10 is warm", c[-1], "0")
    return bad


class GateSummary(unittest.TestCase):
    def test_behaviour(self):
        self.assertEqual(behaviour(load(TOOL)), [])

    def test_mutants(self):
        src = TOOL.read_text()
        killed, survived = 0, []
        with tempfile.TemporaryDirectory() as d:
            for mid, old, new in MUTANTS:
                self.assertEqual(src.count(old), 1, "mutant %s: the tool text it edits is not unique/present: %r" % (mid, old))
                p = pathlib.Path(d) / ("gate-summary-" + mid)
                p.write_text(src.replace(old, new, 1))
                try:
                    caught = bool(behaviour(load(p)))
                except Exception:   # a mutant that crashes the tool is caught too
                    caught = True
                if caught:
                    killed += 1
                else:
                    survived.append(mid)
        print("\nMUTANTS gate-summary: killed %d/%d" % (killed, len(MUTANTS)), file=sys.stderr)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)


class GateEntry(unittest.TestCase):
    """tools/gate: GATE_DRY=1 never runs a gate, for the launch form and the internal --run form (review gate-2x-s2-r1 F6). The gate runs in a scratch tree whose
    check-all.sh is a stub that leaves a marker file: any marker means a step ran."""

    extra_env = {}

    def run_gate(self, *args, dry=True):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "tools").mkdir()
            shutil.copy(TOOLS / "gate", pathlib.Path(d) / "tools" / "gate")
            stub = pathlib.Path(d) / "tools" / "check-all.sh"
            stub.write_text('#!/bin/bash\necho "$@" > "$(dirname "$0")/../ran.marker"\n')
            stub.chmod(0o755)
            # a cold gate exports GATE_COLD / GATE_MUT_CACHE / PIXELIOS_AUDIT_MEMO_DIR (and a gate-pre GATE_*) into its own unit tests: this scratch gate must not inherit them
            # (first cold gate of 13eca56: `GATE_COLD=1 means a formal gate (got kind=quick)`, AP-146 class)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_") and k != "PIXELIOS_AUDIT_MEMO_DIR"}
            env.update(self.extra_env)
            if dry:
                env["GATE_DRY"] = "1"
            r = subprocess.run(["bash", str(pathlib.Path(d) / "tools" / "gate"), *args], capture_output=True, text=True, env=env)
            return r, (pathlib.Path(d) / "ran.marker").exists()

    def test_dry_run_form_runs_nothing(self):
        for args, kind in ((("--quick", "--changed", "--run"), "quick"), (("--formal", "--run"), "formal"), (("--quick", "--changed"), "quick"), (("--formal",), "formal")):
            r, ran = self.run_gate(*args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(ran, "GATE_DRY=1 %s ran check-all.sh" % " ".join(args))
            self.assertRegex(r.stdout.strip().splitlines()[-1], r"^RESULT: PASS gate dry-run kind=%s " % kind)

    def test_inherited_cold_gate_env_does_not_leak_into_the_scratch_gate(self):
        """A gate running these tests inside `tools/gate --cold` has GATE_COLD=1 in its environment; the scratch quick gate must still run (kills: scrub removed)."""
        saved = {k: os.environ.get(k) for k in ("GATE_COLD", "GATE_MUT_CACHE", "PIXELIOS_AUDIT_MEMO_DIR")}
        os.environ.update(GATE_COLD="1", GATE_MUT_CACHE="/nonexistent-cc", PIXELIOS_AUDIT_MEMO_DIR="/nonexistent-cd")
        try:
            r, ran = self.run_gate("--quick", "--changed", "--run", dry=False)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(ran, r.stdout + r.stderr)

    def test_without_dry_the_run_form_does_run_check_all(self):
        """The negative control: the stub marker does appear when GATE_DRY is unset (so the test above can fail)."""
        r, ran = self.run_gate("--quick", "--changed", "--run", dry=False)
        self.assertTrue(ran, r.stdout + r.stderr)

    def test_label_reaches_the_ledger_row(self):
        """T-db59: `tools/gate --formal --label L` must hand L to the detached --run (argv), and the --run form must export GATE_LABEL to check-all.sh, whose ledger append uses it
        (before: the row was always gate-<worktree dir>, so the nightly row never carried nightly-DATE). Kills: --label dropped from the detach argv, GATE_LABEL not exported, check-all ignores it."""
        r, _ = self.run_gate("--formal", "--label", "nightly-2099-01-01")
        self.assertIn("--formal --run --label nightly-2099-01-01", r.stdout)
        r, _ = self.run_gate("--quick", "--changed", "--label", "q-x")
        self.assertIn("--quick --changed --run --label q-x", r.stdout)
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "tools").mkdir()
            shutil.copy(TOOLS / "gate", pathlib.Path(d) / "tools" / "gate")
            stub = pathlib.Path(d) / "tools" / "check-all.sh"
            stub.write_text('#!/bin/bash\necho "${GATE_LABEL:-unset}" > "$(dirname "$0")/../label.seen"\n')
            stub.chmod(0o755)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_") and k != "PIXELIOS_AUDIT_MEMO_DIR"}
            subprocess.run(["bash", str(pathlib.Path(d) / "tools" / "gate"), "--formal", "--run", "--label", "nightly-2099-01-01"], capture_output=True, text=True, env=env)
            self.assertEqual((pathlib.Path(d) / "label.seen").read_text().strip(), "nightly-2099-01-01")
            subprocess.run(["bash", str(pathlib.Path(d) / "tools" / "gate"), "--formal", "--run"], capture_output=True, text=True, env=env)
            self.assertEqual((pathlib.Path(d) / "label.seen").read_text().strip(), "gate-" + os.path.basename(d))
        src = (TOOLS / "check-all.sh").read_text()
        self.assertIn('--label "${GATE_LABEL:-gate-$(basename "$PWD")}"', src)


GATE_REC_STUB = """#!/bin/bash
{ printf 'args:'; printf ' %s' "$@"; echo; env | grep -E '^(GATE_COLD|GATE_MUT_CACHE|PIXELIOS_AUDIT_MEMO_DIR)='; } > "$STUB_REC/heavy-job.rec"
echo "RESULT: PASS heavy-job stub"
"""
WORKTREE_NEW_STUB = """#!/bin/bash
br=$1; shift; dir=; base=
while [ $# -gt 0 ]; do case $1 in --dir) dir=$2; shift 2 ;; --base) base=$2; shift 2 ;; *) shift ;; esac; done
echo "$br $dir $base" >"$STUB_REC/worktree-new.args"
mkdir -p "$dir/tools" && cp "$(dirname "$0")/gate" "$dir/tools/gate" && cp "$(dirname "$0")/gate-trunk-check" "$dir/tools/gate-trunk-check" && cp "$(dirname "$0")/heavy-job.tpl" "$dir/tools/heavy-job" && chmod +x "$dir/tools/heavy-job"
echo "RESULT: PASS worktree-new $dir stub"
"""


def cold_behaviour(gate_path):
    """tools/gate --cold against the script GATE_PATH, in a scratch git repo whose worktree-new / heavy-job are stubs (nothing real is created) -> list of failures."""
    bad = []

    def eq(name, got, want):
        if got != want:
            bad.append("%s: got %r want %r" % (name, got, want))

    def tmpdirs():
        return set(glob.glob(str(d / "tmp" / "pixos-cold-*")))

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        repo, home, wts, rec = d / "repo", d / "home", d / "wts", d / "rec"
        for x in (repo / "tools", home, rec, d / "tmp"):
            x.mkdir(parents=True)
        shutil.copy(gate_path, repo / "tools" / "gate")
        shutil.copy(TOOLS / "gate-trunk-check", repo / "tools" / "gate-trunk-check")   # T-1386: gate runs it before any start (a one-commit fixture repo: no trunk ref -> printed SKIP)
        (repo / "tools" / "worktree-new").write_text(WORKTREE_NEW_STUB)
        (repo / "tools" / "worktree-new").chmod(0o755)
        (repo / "tools" / "heavy-job.tpl").write_text(GATE_REC_STUB)
        (repo / "f.txt").write_text("a\n")
        env = {k: v for k, v in os.environ.items() if k not in ("GATE_DRY", "GATE_COLD", "GATE_MUT_CACHE", "PIXELIOS_AUDIT_MEMO_DIR")}
        env.update(GATE_AFFECTED_CHECKED="1", GATE_COLD_TMP=str(d / "tmp"), HOME=str(home), WORKTREE_ROOT=str(wts), STUB_REC=str(rec), GIT_CONFIG_GLOBAL="/dev/null")
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *a], capture_output=True, text=True, env=env)
        g("init", "-q")
        g("add", "-A")
        g("commit", "-q", "-m", "x")
        head = g("rev-parse", "HEAD").stdout.strip()
        gate = lambda *a, **e: subprocess.run(["bash", str(repo / "tools" / "gate"), *a], capture_output=True, text=True, env={**env, **e})
        created, seen = [], tmpdirs()
        try:
            # 1. GATE_DRY=1 --cold: prints every command, creates NOTHING (no worktree dir, no /tmp cache dir, no stub call)
            before = tmpdirs()
            r = gate("--cold", "--label", "x", GATE_DRY="1")
            eq("dry rc", r.returncode, 0)
            eq("dry creates no worktree root", wts.exists(), False)
            eq("dry creates no cache dir", tmpdirs() - before, set())
            eq("dry calls no tool", list(rec.iterdir()), [])
            out = r.stdout
            eq("dry output names the commands", [t in out for t in ("worktree-new cold-x --dir %s/cold-x --base %s" % (wts, head), "GATE_COLD=1 GATE_MUT_CACHE=", "PIXELIOS_AUDIT_MEMO_DIR=", "worktree remove --force", "rm -rf --")], [True] * 5)
            eq("dry last line", out.strip().splitlines()[-1], "RESULT: PASS gate dry-run kind=formal cold=1 label=x")
            # 2. dirty tracked file: refused loudly (also in a dry run); an untracked file is not a reason (the run gates HEAD)
            (repo / "untracked.txt").write_text("u\n")
            eq("untracked file does not refuse", gate("--cold", "--label", "x", GATE_DRY="1").returncode, 0)
            (repo / "f.txt").write_text("changed\n")
            for dry in ("1", ""):
                r = gate("--cold", "--label", "x", **({"GATE_DRY": dry} if dry else {}))
                eq("dirty refused (dry=%r)" % dry, (r.returncode, "uncommitted changes in tracked files" in r.stdout, "RESULT: ERROR gate" in r.stdout.splitlines()[-1]), (2, True, True))
                eq("dirty creates nothing", (wts.exists(), list(rec.iterdir())), (False, []))
            g("checkout", "--", "f.txt")
            # 3. the real path (worktree-new and heavy-job are stubs): fresh dirs, env handed to the worktree's own gate, nothing removed
            before = tmpdirs()
            r = gate("--cold", "--label", "t1")
            created = sorted(tmpdirs() - before)
            eq("launch rc", r.returncode, 0)
            eq("launch last line", r.stdout.strip().splitlines()[-1].startswith("RESULT: PASS gate cold launched label=t1 worktree=%s/cold-t1 " % wts), True)
            eq("two new temp dirs", len(created), 2)
            eq("worktree-new args", (rec / "worktree-new.args").read_text().split() if (rec / "worktree-new.args").exists() else None, ["cold-t1", "%s/cold-t1" % wts, head])
            hj = (rec / "heavy-job.rec").read_text() if (rec / "heavy-job.rec").exists() else ""
            eq("heavy-job gets the formal run", "args: detach --label gate-cold-t1 -- bash %s/cold-t1/tools/gate --formal --run" % wts in hj, True)
            envs = dict(l.split("=", 1) for l in hj.splitlines() if "=" in l and not l.startswith("args:"))
            eq("GATE_COLD reaches the gate", envs.get("GATE_COLD"), "1")
            mut, memo = envs.get("GATE_MUT_CACHE", ""), envs.get("PIXELIOS_AUDIT_MEMO_DIR", "")
            eq("cache dirs are the two new, distinct, empty temp dirs", (sorted([mut, memo]) == created, mut != memo, [os.listdir(x) for x in created if os.path.isdir(x)]), (True, True, [[], []]))
            eq("dirs are not the real caches", [x.startswith(str(home)) or "/.cache/" in x for x in (mut, memo)], [False, False])
            eq("cleanup commands printed, nothing removed", ("worktree remove --force %s/cold-t1" % wts in r.stdout, "branch -D cold-t1" in r.stdout, all(os.path.isdir(x) for x in created), (wts / "cold-t1").is_dir()), (True, True, True, True))
            # 4. the worktree's own gate: GATE_COLD=1 needs two distinct, existing, EMPTY dirs (a used dir would make the run warm and the ledger row a lie)
            w = str(wts / "cold-t1" / "tools" / "gate")
            ok_a, ok_b = str(d / "ca"), str(d / "cb")
            os.mkdir(ok_a)
            os.mkdir(ok_b)
            rcs = lambda **e: subprocess.run(["bash", w, "--formal", "--label", "z"], capture_output=True, text=True, env={**env, "GATE_DRY": "1", "GATE_COLD": "1", **e})
            eq("inner: ok dirs accepted", rcs(GATE_MUT_CACHE=ok_a, PIXELIOS_AUDIT_MEMO_DIR=ok_b).returncode, 0)
            eq("inner: no dirs refused", rcs().returncode, 2)
            eq("inner: cache disabled with 0 refused", rcs(GATE_MUT_CACHE="0", PIXELIOS_AUDIT_MEMO_DIR=ok_b).returncode, 2)
            eq("inner: same dir twice refused", rcs(GATE_MUT_CACHE=ok_a, PIXELIOS_AUDIT_MEMO_DIR=ok_a).returncode, 2)
            eq("inner: missing dir refused", rcs(GATE_MUT_CACHE=ok_a, PIXELIOS_AUDIT_MEMO_DIR=str(d / "nope")).returncode, 2)
            (pathlib.Path(ok_b) / "used").write_text("x")
            eq("inner: non-empty dir refused", rcs(GATE_MUT_CACHE=ok_a, PIXELIOS_AUDIT_MEMO_DIR=ok_b).returncode, 2)
            os.mkdir(str(d / "cc"))
            os.mkdir(str(d / "cd"))
            eq("inner: quick refused", subprocess.run(["bash", w, "--quick", "--changed"], capture_output=True, text=True,
                                                      env={**env, "GATE_DRY": "1", "GATE_COLD": "1", "GATE_MUT_CACHE": str(d / "cc"), "PIXELIOS_AUDIT_MEMO_DIR": str(d / "cd")}).returncode, 2)
        finally:
            for x in tmpdirs() - seen:   # also what a broken mutant created
                shutil.rmtree(x, ignore_errors=True)
    return bad


class GateCold(unittest.TestCase):
    def test_behaviour(self):
        self.assertEqual(cold_behaviour(TOOLS / "gate"), [])

    def test_mutants(self):
        src = (TOOLS / "gate").read_text()
        killed, survived = 0, []
        with tempfile.TemporaryDirectory() as d:
            for mid, old, new in MUTANTS_OF_GATE_COLD:
                self.assertEqual(src.count(old), 1, "mutant %s: the tool text it edits is not unique/present: %r" % (mid, old))
                p = pathlib.Path(d) / ("gate-" + mid)
                p.write_text(src.replace(old, new, 1))
                try:
                    caught = bool(cold_behaviour(p))
                except Exception:   # a mutant that breaks the harness is caught too
                    caught = True
                if caught:
                    killed += 1
                else:
                    survived.append(mid)
        print("\nMUTANTS tools/gate --cold: killed %d/%d" % (killed, len(MUTANTS_OF_GATE_COLD)), file=sys.stderr)
        self.assertEqual(survived, [], "SURVIVED: %s" % survived)


# (id, exact text of tools/gate, replacement): one realistic defect each of the --cold code
MUTANTS_OF_GATE_COLD = [
    ("dirty-check-dropped", '[ -z "$dirty" ] || { printf', 'true || { printf'),
    ("real-cache-dirs", 'd1=$(mktemp -d "${GATE_COLD_TMP:-/tmp}/pixos-cold-mut.XXXXXX") && d2=$(mktemp -d "${GATE_COLD_TMP:-/tmp}/pixos-cold-memo.XXXXXX")', 'd1=$HOME/.cache/pixelios/wlan-mutants && d2=$HOME/.cache/pixelios/audit && mkdir -p "$d1" "$d2"'),
    ("one-dir-for-both", 'd2=$(mktemp -d "${GATE_COLD_TMP:-/tmp}/pixos-cold-memo.XXXXXX")', 'd2=$d1'),
    ("env-dropped", 'env GATE_COLD=1 GATE_MUT_CACHE="$d1" PIXELIOS_AUDIT_MEMO_DIR="$d2" bash', 'env GATE_COLD=1 bash'),
    ("cold-flag-dropped", 'env GATE_COLD=1 GATE_MUT_CACHE="$d1"', 'env GATE_MUT_CACHE="$d1"'),
    ("memo-dir-dropped", 'GATE_MUT_CACHE="$d1" PIXELIOS_AUDIT_MEMO_DIR="$d2" bash', 'GATE_MUT_CACHE="$d1" bash'),
    ("dry-creates-worktree-dir", 'inner=gate-cold-$label\n', 'inner=gate-cold-$label\n\tmkdir -p "$wt"\n'),
    ("auto-removes-dirs", '\tif [ "$rc" = 0 ]; then echo "RESULT: PASS gate cold launched', '\trm -rf -- "$d1" "$d2"\n\tif [ "$rc" = 0 ]; then echo "RESULT: PASS gate cold launched'),
    ("inner-accepts-used-dir", '[ -z "$(ls -A -- "$d")" ] ||', 'true ||'),
    ("inner-accepts-one-dir", '[ "$GATE_MUT_CACHE" != "$PIXELIOS_AUDIT_MEMO_DIR" ] ||', 'true ||'),
    ("base-is-not-head", '--base "$sha" 2>&1', '--base trunk 2>&1'),
    ("inner-quick-allowed", '[ "$kind" = formal ] || { echo "RESULT: ERROR gate: GATE_COLD=1 means a formal gate', 'true || { echo "RESULT: ERROR gate: GATE_COLD=1 means a formal gate'),
]

# (id, exact text of tools/gate-summary, replacement): one realistic defect each
MUTANTS = [
    ("budget-gte", 's["secs"] > b[n]]', 's["secs"] >= b[n]]'),
    ("total-gte", "elapsed > totals[tk]", "elapsed >= totals[tk]"),
    ("stopped-counted", 'if s["status"] != "STOPPED")', "if True)"),
    ("skipped-wrong", "len((manifest or {}).get(\"steps\", [])) - ran", "len((manifest or {}).get(\"steps\", [])) + ran"),
    ("ran-full-is-quick", ' and "ran full" not in line', ""),
    ("base-dropped", "base = f[1]", 'base = "-"'),
    ("dash-lost", '",".join(slow) or "-"', '",".join(slow)'),
    ("exit-code-inverted", 'got[1] == "FAIL"', 'got[1] == "PASS"'),
    ("columns-swapped", "[str(int(time.time())), a.label, kind or a.kind or \"formal\", a.host,", "[str(int(time.time())), a.host, kind or a.kind or \"formal\", a.label,"),
    ("elapsed-from-first-end", "max(0, last - min(starts))", "max(0, last - max(starts))"),
    ("verdict-from-kind", 'verdict = "PASS" if a.line.startswith("GATE PASS") else "FAIL"', 'verdict = "PASS"'),
    ("append-truncates", 'with open(p, "a") as fh:', 'with open(p, "w") as fh:'),
    # K0 (cold gate)
    ("cold-tag-dropped", '" cold=1" if cold else ""', '""'),
    ("cold-column-always-0", '"1" if is_cold(a.line) else "0"', '"0"'),
    ("cold-column-dropped", ', per or "-", "1" if is_cold(a.line) else "0"]', ', per or "-"]'),
    ("cold-uses-warm-total", 'tk = "cold" if cold else kind', "tk = kind"),
    ("cold-keeps-warm-step-budgets", "out = [] if cold else [", "out = ["),
    ("cold-token-prefix-match", 'r"(?:^|\\s)cold=1(?:\\s|$)"', 'r"cold=1"'),
    ("slow-cold-flag-ignored", "manifest, a.cold)))", "manifest)))"),
]

if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    ok = res.wasSuccessful()
    print("RESULT: %s test_gate_summary tests=%d mutants=%d+%d" % ("PASS" if ok else "FAIL", res.testsRun, len(MUTANTS), len(MUTANTS_OF_GATE_COLD)))
    sys.exit(0 if ok else 1)
