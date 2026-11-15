#!/usr/bin/env python3
"""Tests for tools/gate-nightly (T-db59): hermetic, a fixture repo with stub tools/gate and tools/worktree-new that record their argv; no network, no systemd, no phone.

Run: python3 -I tools/test_gate_nightly.py   (mutants of the tool: each must be killed by at least one check)
"""
import os, shutil, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
ENV = dict({k: v for k, v in os.environ.items() if k != "HEAVY_JOB_WAIT"}, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
GATE_STUB = '#!/bin/bash\necho "gate $* rc=${STUB_GATE_RC:-0}" >>"$STUB_LOG"\necho "${HEAVY_JOB_WAIT:-unset}" >"$STUB_LOG.wait"\nexit "${STUB_GATE_RC:-0}"\n'
WT_STUB = '#!/bin/bash\nbr=$1; shift\nwhile [ $# -gt 0 ]; do case $1 in --dir) d=$2; shift 2;; --base) b=$2; shift 2;; *) shift;; esac; done\necho "worktree-new $br" >>"$STUB_LOG"\ngit -C "$(dirname "$0")/.." worktree add -q --detach "$d" "$b"\n'


def git(root, *a):
    return subprocess.run(["git", "-C", root] + list(a), env=ENV, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def fixture(tmp, tool, trunk=True):
    root = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(root, "tools"))
    for name, text in (("gate", GATE_STUB), ("worktree-new", WT_STUB)):
        with open(os.path.join(root, "tools", name), "w") as fh:
            fh.write(text)
        os.chmod(os.path.join(root, "tools", name), 0o755)
    shutil.copy(tool, os.path.join(root, "tools", "gate-nightly"))
    os.chmod(os.path.join(root, "tools", "gate-nightly"), 0o755)
    subprocess.run(["git", "init", "-q", "-b", "trunk" if trunk else "other", root], env=ENV, check=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


def run(root, tmp, *args, env=None):
    e = dict(ENV, STUB_LOG=os.path.join(tmp, "stub.log"), GATE_NIGHTLY_WT=os.path.join(tmp, "wt"), HOME=tmp)
    e.pop("GATE_DRY", None)
    e.update(env or {})
    p = subprocess.run([os.path.join(root, "tools", "gate-nightly")] + list(args), env=e, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    log = os.path.join(tmp, "stub.log")
    lines = []
    if os.path.exists(log):
        with open(log) as fh:
            lines = fh.read().splitlines()
    return p.returncode, p.stdout, lines


def behaviour(tool):
    """-> list of failure strings (empty = the tool behaves as specified)."""
    bad = []
    def eq(what, got, want):
        if got != want:
            bad.append("%s: got %r want %r" % (what, got, want))
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(tmp, tool)
        sha = git(root, "rev-parse", "trunk")
        rc, out, log = run(root, tmp, "--date", "2026-10-30")      # a Friday: formal only
        eq("weekday: rc", rc, 0)
        eq("weekday: worktree created from trunk, formal gate with the nightly label, no cold", log, ["worktree-new gate-nightly-wt", "gate --formal --label nightly-2026-10-30 rc=0"])
        eq("weekday: RESULT", out.strip().splitlines()[-1], "RESULT: PASS gate-nightly launched label=nightly-2026-10-30 sha=%s cold=no" % sha[:12])
        eq("worktree is at the trunk sha", git(os.path.join(tmp, "wt"), "rev-parse", "HEAD"), sha)
        with open(os.path.join(tmp, "stub.log.wait")) as fh:
            eq("queued gates wait for the host lock (HEAVY_JOB_WAIT)", fh.read().strip(), "7200")
        os.remove(os.path.join(tmp, "stub.log"))
        with open(os.path.join(root, "x"), "w") as fh:               # trunk moves on: the second night checks the new sha out (worktree reused, not recreated)
            fh.write("x")
        git(root, "add", "-A"); git(root, "commit", "-q", "-m", "next")
        sha2 = git(root, "rev-parse", "trunk")
        old = os.path.join(tmp, "cold-nightly-cold-2026-10-25")    # last Sunday's cold worktree (tools/gate --cold never removes it) and an unrelated one
        other = os.path.join(tmp, "cold-manual")
        git(root, "worktree", "add", "-q", "-b", "cold-nightly-cold-2026-10-25", old, "trunk")
        git(root, "worktree", "add", "-q", "-b", "cold-manual", other, "trunk")
        rc, out, log = run(root, tmp, "--date", "2026-11-01")      # a Sunday: formal + cold
        eq("sunday: old nightly cold worktree + branch pruned, a manual cold one kept", (os.path.exists(old), "cold-nightly-cold-2026-10-25" in git(root, "branch", "--list"), os.path.exists(other)), (False, False, True))
        eq("sunday: both gates, worktree reused", (rc, log), (0, ["gate --formal --label nightly-2026-11-01 rc=0", "gate --cold --label nightly-cold-2026-11-01 rc=0"]))
        eq("sunday: wt moved to the new trunk", git(os.path.join(tmp, "wt"), "rev-parse", "HEAD"), sha2)
        os.remove(os.path.join(tmp, "stub.log"))
        rc, out, log = run(root, tmp, "--date", "2026-11-01", "--no-cold")
        eq("--no-cold on a Sunday", log, ["gate --formal --label nightly-2026-11-01 rc=0"])
        os.remove(os.path.join(tmp, "stub.log"))
        rc, out, log = run(root, tmp, "--date", "2026-10-30", "--cold")
        eq("--cold on a Friday", log, ["gate --formal --label nightly-2026-10-30 rc=0", "gate --cold --label nightly-cold-2026-10-30 rc=0"])
        os.remove(os.path.join(tmp, "stub.log"))
        rc, out, log = run(root, tmp, "--date", "2026-11-01", env={"STUB_GATE_RC": "3"})
        eq("a failing launch is a FAIL and the cold gate does not start", (rc, log, out.strip().splitlines()[-1]),
           (3, ["gate --formal --label nightly-2026-11-01 rc=3"], "RESULT: FAIL gate-nightly launch rc=3 label=nightly-2026-11-01"))
        os.remove(os.path.join(tmp, "stub.log"))
        with open(os.path.join(tmp, "wt", "tools", "gate"), "a") as fh:
            fh.write("# edit\n")                                      # a tracked change in the nightly worktree: refused, nothing started
        rc, out, log = run(root, tmp, "--date", "2026-10-30")
        eq("dirty worktree refused", (rc, log, "RESULT: FAIL gate-nightly" in out and "tracked changes" in out), (1, [], True))
        git(os.path.join(tmp, "wt"), "checkout", "-q", "--", "tools/gate")
        rc, out, log = run(root, tmp, "--date", "2026-10-30", env={"GATE_DRY": "1"})
        eq("dry run starts nothing", (rc, log, out.strip().splitlines()[-1].startswith("RESULT: PASS gate-nightly dry-run label=nightly-2026-10-30")), (0, [], True))
        rc, out, log = run(root, tmp, "--date", "2026-02-30")
        eq("impossible date", (rc, "not a calendar date" in out), (2, True))
        rc, out, log = run(root, tmp, "--date", "30-10-2026")
        eq("bad date", (rc, "RESULT: ERROR gate-nightly --date" in out), (2, True))
        rc, out, log = run(root, tmp, "install", "--print", "--time", "04:15")
        eq("install --print", (rc, "OnCalendar=*-*-* 04:15:00" in out, "ExecStart=/bin/bash %s/tools/gate-nightly run" % root in out, "Persistent=true" in out), (0, True, True, True))
        git(root, "worktree", "add", "-q", "-b", "side", os.path.join(tmp, "side"), "trunk")
        r2 = subprocess.run([os.path.join(tmp, "side", "tools", "gate-nightly"), "install", "--print"], env=dict(ENV, HOME=tmp), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        eq("install from a worktree refused", (r2.returncode, "must run from the main clone" in r2.stdout), (2, True))
        rc, out, log = run(root, tmp, "install", "--print", "--time", "25:99")
        eq("bad time", rc, 2)
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(tmp, tool, trunk=False)
        rc, out, log = run(root, tmp, "--date", "2026-10-30")
        eq("no trunk branch", (rc, log, "RESULT: FAIL gate-nightly no local trunk" in out), (1, [], True))
    return bad


MUTANTS = [
    ("sunday-cold-dropped", 'then cold=yes; else cold=no; fi', 'then cold=no; else cold=no; fi'),
    ("cold-every-day", 'then cold=yes; else cold=no; fi', 'then cold=yes; else cold=yes; fi'),
    ("label-lost", 'label=nightly-$date', 'label=nightly'),
    ("dirty-not-refused", '[ -z "$dirty" ] ||', 'true ||'),
    ("no-checkout-of-new-trunk", 'git -C "$wt" checkout -q --detach "$sha" ||', 'true ||'),
    ("cold-after-failed-formal", 'if [ $rc -eq 0 ] && [ "$cold" = yes ]; then', 'if [ "$cold" = yes ]; then'),
    ("failure-reported-as-pass", '[ $rc -eq 0 ] && echo "RESULT: PASS', 'echo "RESULT: PASS'),
    ("dry-ignored", 'if [ "${GATE_DRY:-}" = 1 ]; then', 'if false; then'),
    ("no-trunk-ignored", 'refs/heads/trunk >/dev/null ||', 'refs/heads/trunk >/dev/null || true ||'),
    ("install-from-any-checkout", '[ "$mainclone" = "$repo" ] ||', 'true ||'),
    ("old-cold-not-pruned", 'git -C "$wt" worktree remove --force "$w" &&', 'true &&'),
    ("prune-everything-cold", 'cold-nightly-cold-[0-9-]*', 'cold-[a-z0-9-]*'),
    ("lock-wait-default", 'export HEAVY_JOB_WAIT=${HEAVY_JOB_WAIT:-7200}', 'true'),
    ("calendar-date-unchecked", '[ "$(date -d "$date" +%F 2>/dev/null)" = "$date" ] ||', 'true ||'),
    ("timer-not-persistent", 'Persistent=true', 'Persistent=false'),
]


class GateNightly(unittest.TestCase):
    def test_behaviour(self):
        self.assertEqual(behaviour(os.path.join(TOOLS, "gate-nightly")), [])

    def test_mutants_killed(self):
        with open(os.path.join(TOOLS, "gate-nightly")) as fh:
            src = fh.read()
        for name, needle, repl in MUTANTS:
            with self.subTest(mutant=name):
                self.assertEqual(src.count(needle), 1, "needle of %s occurs %d times" % (name, src.count(needle)))
                with tempfile.TemporaryDirectory() as d:
                    p = os.path.join(d, "gate-nightly")
                    with open(p, "w") as fh:
                        fh.write(src.replace(needle, repl))
                    self.assertTrue(behaviour(p), "mutant %s SURVIVED" % name)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0)
    print("RESULT: %s test_gate_nightly mutants=%d" % ("PASS" if r.result.wasSuccessful() else "FAIL", len(MUTANTS)))
    sys.exit(0 if r.result.wasSuccessful() else 1)
