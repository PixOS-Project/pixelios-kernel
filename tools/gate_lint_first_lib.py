"""Shared fixtures, scenarios and mutants of the gate-lint-first / gate-request tests (PLAN-GATE-2X K4a: tools/test_gate_lint_first.py took 88 s as one file;
its 20 mutants now run in 3 files (tools/test_gate_lint_first*.py) so the unit shards run them side by side). Not a test file itself."""
import os, re, shutil, subprocess, sys, tempfile, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
STEPS = ["inputs", "validate", "mutant-needles", "boot-markers", "lint-silent", "consumed", "rules", "phone-converge", "census", "lint-c-style", "lint-size", "public-split", "public-guard"]
# fixture stub tools: file -> (interpreter, rc, text); public-split (make-public-tree.py), public-guard (public-push-guard) and consumed (lint-consumed) are absent = SKIP
STUBS = {
    "gate-inputs": ("bash", 0, "RESULT: PASS inputs stub"),
    "validate-repo.py": ("python3", 0, "RESULT: PASS"),
    "lint-mutant-needles": ("python3", 0, "RESULT: PASS mutant-needles tables=3 needles=9 missing=0"),
    "lint-boot-markers": ("python3", 0, "RESULT: PASS boot-markers"),
    "lint-silent": ("python3", 0, "RESULT: PASS lint-silent"),
    "lint-size": ("python3", 0, "RESULT: PASS lint-size"),
    "check-rules.py": ("python3", 0, "RESULT: PASS 0 new, 70 baselined"),
    "lint-phone-converge": ("python3", 0, "RESULT: PASS phone-converge"),
    "lint-c": ("python3", 0, "RESULT: PASS style new=0 baseline=0"),
    "tool-census": ("python3", 0, "WARN DOC-ONLY tools/x\nRESULT: PASS tools=1 active=1 deprecated=0 allowlisted=0 nocaller=0 doc_only=0"),
}
PASS_N, PASS_SKIPPED = 10, 3


def write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def stub(path, interp, rc, text):
    body = "#!/usr/bin/env python3\nimport sys\nprint(%r)\nsys.exit(%d)\n" % (text, rc) if interp == "python3" else "#!/bin/bash\necho %s\nexit %d\n" % (repr(text), rc)
    write(path, body, 0o755)


def make_fixture(root, override=None, drop=()):
    """A git repo with the real gate-lint-steps.tsv and stub tools; override = {file: (interp, rc, text)}; drop = files left out."""
    os.makedirs(root)
    shutil.copy(os.path.join(TOOLS, "gate-lint-steps.tsv"), os.path.join(root, "tools", "gate-lint-steps.tsv")) if os.path.isdir(os.path.join(root, "tools")) else None
    os.makedirs(os.path.join(root, "tools"), exist_ok=True)
    shutil.copy(os.path.join(TOOLS, "gate-lint-steps.tsv"), os.path.join(root, "tools", "gate-lint-steps.tsv"))
    st = dict(STUBS)
    st.update(override or {})
    for name, (interp, rc, text) in st.items():
        if name not in drop:
            stub(os.path.join(root, "tools", name), interp, rc, text)
    write(os.path.join(root, "docs", "README.md"), "x\n", 0o644)
    write(os.path.join(root, ".gitignore"), "shim/out/\n", 0o644)   # T-f32e: gate-lint-first leaves shim/out/lint-first.result, as in the real repo, where shim/out/ is ignored
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for a in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", "-C", root] + a, env=env, check=True, stdout=subprocess.DEVNULL)


def make_tooldir(dest, src=TOOLS, edits=()):
    """Copy the two tools + table into dest/tools (a stub heavy-job records its argv in $HJ_MARK); edits = [(file, needle, replacement)]."""
    d = os.path.join(dest, "tools")
    os.makedirs(d)
    for f in ("gate-lint-first", "gate-request", "gate-trunk-check", "gate-lint-steps.tsv"):
        shutil.copy(os.path.join(src, f), os.path.join(d, f))
        os.chmod(os.path.join(d, f), 0o755)
    write(os.path.join(d, "heavy-job"),
          '#!/bin/bash\nprintf "%s\\n" "$*" >"$HJ_MARK"\necho "heavy-job: detached unit=pixos-hj-$3-1 log=$5 end-line=x"\necho "RESULT: PASS heavy-job detach"\n')
    for f, needle, repl in edits:
        p = os.path.join(d, f)
        with open(p) as fh:
            t = fh.read()
        if t.count(needle) != 1:
            raise AssertionError("mutant needle %r occurs %d times in %s" % (needle, t.count(needle), f))
        with open(p, "w") as fh:
            fh.write(t.replace(needle, repl))
    return d


def run(argv, env_extra=None):
    env = dict(os.environ)
    env.pop("GATE_REQUEST_DRY", None)
    env.update(env_extra or {})
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, timeout=120)
    return p.returncode, p.stdout


class Case:
    def __init__(self, tooldir):
        self.t = tooldir
        self.tmp = tempfile.mkdtemp(prefix="glf-")
        self.mark = os.path.join(self.tmp, "hj.mark")

    def fixture(self, name, **kw):
        root = os.path.join(self.tmp, name)
        make_fixture(root, **kw)
        return root

    def lint(self, root, *extra, env=None):
        return run([sys.executable, os.path.join(self.t, "gate-lint-first"), "--repo", root] + list(extra), env)

    def request(self, root, dry=True, label="L1"):
        return run([os.path.join(self.t, "gate-request"), "--repo", root, "--label", label],
                   dict({"GATE_REQUEST_DRY": "1"} if dry else {}, HJ_MARK=self.mark))

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---- scenarios: each returns None (as the spec says) or an error string ----
def sc_needles_fail(c):
    root = c.fixture("nf", override={"lint-mutant-needles": ("python3", 1, "MUTANT NEEDLE MISSING test_a_mutants.py:M 'x'\nRESULT: FAIL mutant-needles missing=1")})
    rc, out = c.lint(root)
    if rc != 1:
        return "failing mutant-needles: rc=%d, want 1: %s" % (rc, out)
    if "FAIL mutant-needles" not in out or "PASS inputs" not in out or "PASS rules" not in out:
        return "step lines wrong (FAIL named step + the other steps still run): %s" % out
    last = out.strip().splitlines()[-1]
    if last != 'RESULT: FAIL gate-lint-first failed=mutant-needles first="MUTANT NEEDLE MISSING test_a_mutants.py:M \'x\'"':
        return "RESULT line wrong: %s" % last
    return None


def sc_request_refused_on_lint_fail(c):
    root = c.fixture("rf", override={"lint-mutant-needles": ("python3", 1, "MUTANT NEEDLE MISSING a\nRESULT: FAIL mutant-needles")})
    for dry in (True, False):
        rc, out = c.request(root, dry=dry)
        if rc != 1:
            return "gate-request on a lint failure (dry=%s): rc=%d, want 1: %s" % (dry, rc, out)
        if "heavy-job" in out.replace("gate-request: not started", "") or "DRY:" in out or os.path.exists(c.mark):
            return "gate-request started a gate although a lint step failed (dry=%s): %s" % (dry, out)
        if "failed=mutant-needles" not in out or not out.strip().splitlines()[-1].startswith("RESULT: FAIL gate-request"):
            return "gate-request must name the step and end in RESULT: FAIL gate-request: %s" % out
    return None


def sc_request_refused_on_lint_error(c):
    """gate-lint-first exiting 2 (unreadable table) must stop gate-request too, not only exit 1."""
    root = c.fixture("re")
    write(os.path.join(root, "tools", "gate-lint-steps.tsv"), "broken line without tabs\n", 0o644)
    subprocess.run(["git", "-C", root, "commit", "-q", "-am", "x", "--author=t <t@t>"], env=dict(os.environ, GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"), check=True)
    rc, out = c.request(root, dry=False)
    if rc != 1 or os.path.exists(c.mark) or "RESULT: FAIL gate-request lint-first rc=2" not in out:
        return "gate-lint-first rc 2 must refuse the gate (rc 1, no heavy-job): rc=%d %s" % (rc, out)
    return None


def sc_all_pass(c):
    root = c.fixture("ap", drop=("lint-consumed",))
    rc, out = c.lint(root)
    if rc != 0:
        return "all-pass fixture: rc=%d: %s" % (rc, out)
    if "SKIP consumed (no tool)" not in out or "SKIP public-split (no tool)" not in out or "SKIP public-guard (no tool)" not in out:
        return "missing tool must print a SKIP line (never silently): %s" % out
    if out.strip().splitlines()[-1] != "RESULT: PASS gate-lint-first steps=%d skipped=%d" % (PASS_N, PASS_SKIPPED):
        return "SKIP counted as a step, or RESULT wrong: %s" % out.strip().splitlines()[-1]
    if re.search(r"^PASS (consumed|public-split|public-guard)", out, re.M):
        return "a SKIPped step printed as PASS: %s" % out
    return None


def sc_request_starts_on_pass(c):
    root = c.fixture("rp")
    rc, out = c.request(root, dry=True, label="L2")
    if rc != 0:
        return "gate-request dry on a clean passing repo: rc=%d: %s" % (rc, out)
    want = re.compile(r"DRY: %s/heavy-job detach --label L2 --log /tmp/gate-L2-\d+-\d+\.log -- bash -c " % re.escape(c.t))   # T-7dff: one log per request
    dry = [x for x in out.splitlines() if x.startswith("DRY: ")]
    if len(dry) != 1 or not want.match(dry[0]):
        return "DRY line missing/wrong: %s" % out
    for part in ("%s/tools/gate-xnu-build --always --repo %s --label L2; x=$?; echo xrc=$x;" % (root, root), "HEAVY_JOB_WAIT=7200 %s/tools/check-all.sh; r=$?; echo rc=$r; [ $r != 0 ] || r=$x; exit $r" % root,
                 "--remote-cmd", "remote-job --where auto gate-pre && %s/gate-import-remote" % c.t):  # T-9fed: a host-name scrub once turned the --where value into prose; remote-job refused it (host-neutral: auto)
        if part not in dry[0]:
            return "DRY command lacks %r: %s" % (part, dry[0])
    if os.path.exists(c.mark):
        return "DRY must not call heavy-job"
    rc, out = c.request(root, dry=False, label="L3")
    if rc != 0 or not os.path.exists(c.mark):
        return "real path must call heavy-job: rc=%d out=%s" % (rc, out)
    with open(c.mark) as fh:
        argv = fh.read()
    if not re.match(r"detach --label L3 --log /tmp/gate-L3-\d+-\d+\.log -- bash -c ", argv):
        return "heavy-job argv wrong: %s" % argv
    if "unit=pixos-hj-L3-1" not in out or not re.search(r"log=/tmp/gate-L3-\d+-\d+\.log", out.strip().splitlines()[-1]):
        return "unit name / log path not printed in the RESULT line: %s" % out
    return None


def sc_census_drift(c):
    """Live super drift is a WARN (the formal gate passes it, tool-census W26); GATE_LINT_CENSUS_STRICT=1 makes it a FAIL."""
    for k in ("super_new", "super_stale"):
        root = c.fixture("cd" + k, override={"tool-census": ("python3", 0, "RESULT: PASS tools=1 active=1 deprecated=0 allowlisted=0 nocaller=0 doc_only=0 %s=2 (drift)" % k)})
        rc, out = c.lint(root)
        if rc != 0 or "WARN CENSUS DRIFT: run tools/tool-census --write" not in out or "PASS census" not in out:
            return "census %s=2 must PASS with a WARN CENSUS DRIFT line (rc=%d): %s" % (k, rc, out)
        rc, out = c.lint(root, env={"GATE_LINT_CENSUS_STRICT": "1"})
        if rc != 1 or "FAIL census" not in out or "CENSUS DRIFT: run tools/tool-census --write" not in out or "failed=census" not in out:
            return "strict census %s=2 must FAIL with CENSUS DRIFT (rc=%d): %s" % (k, rc, out)
    root = c.fixture("cd0", override={"tool-census": ("python3", 0, "RESULT: PASS tools=1 super_new=0 super_stale=0")})
    rc, out = c.lint(root, env={"GATE_LINT_CENSUS_STRICT": "1"})
    if rc != 0 or "WARN" in out:
        return "census super_new=0 super_stale=0 must pass quietly even strict (rc=%d): %s" % (rc, out)
    root = c.fixture("cdrc", override={"tool-census": ("python3", 1, "RESULT: FAIL census broken")})
    rc, out = c.lint(root)
    if rc != 1 or "failed=census" not in out:
        return "a failing tool-census (rc 1) must fail (rc=%d): %s" % (rc, out)
    return None


def sc_dirty_refused(c):
    for kind in ("modified", "untracked"):
        root = c.fixture("dt" + kind)
        if kind == "modified":
            with open(os.path.join(root, "tools", "check-rules.py"), "a") as f:
                f.write("# edit\n")
        else:
            write(os.path.join(root, "xnu", "patches", "9999-new.patch"), "x\n", 0o644)
        rc, out = c.request(root)
        if rc != 1 or "RESULT: FAIL gate-request dirty=1" not in out or "DRY:" in out or "PASS inputs" in out:
            return "dirty (%s) tree must be refused before the lint, rc=%d: %s" % (kind, rc, out)
    for d, f in (("docs", "README.md"), ("shim", "wlan.c"), ("soc", "gs101.json")):
        root = c.fixture("dt" + d)
        write(os.path.join(root, d, f), "dirty\n", 0o644)
        rc, out = c.request(root)
        if rc != 1 or "RESULT: FAIL gate-request dirty=1" not in out:
            return "a dirty %s/ path must be refused too (check-all.sh marks ANY dirty path +dirty and writes no stamp): rc=%d %s" % (d, rc, out)
    return None


def sc_label(c):
    root = c.fixture("lb")
    for bad in ("-x", ".", "..", "a b", "a;b", "$(id)", ""):
        rc, out = c.request(root, label=bad)
        if rc != 2 or "DRY:" in out or os.path.exists(c.mark):
            return "label %r must be refused with rc 2 (as gate-xnu-build), rc=%d: %s" % (bad, rc, out)
    rc, out = c.request(root, label="a.b_c-1")
    if rc != 0:
        return "a valid label must pass: rc=%d %s" % (rc, out)
    rc, out = run([os.path.join(c.t, "gate-request"), "--repo", os.path.join(c.tmp, "nope"), "--label", "L"])
    if rc != 2:
        return "a missing repo dir must be rc 2: rc=%d %s" % (rc, out)
    return None


def sc_timeout_and_rc(c):
    root = c.fixture("to")
    write(os.path.join(root, "tools", "check-rules.py"), "import time\ntime.sleep(5)\n", 0o644)
    rc, out = run([sys.executable, os.path.join(c.t, "gate-lint-first"), "--repo", root], {"GATE_LINT_TIMEOUT": "1"})
    if rc != 1 or "timeout after 1s" not in out or "failed=rules" not in out:
        return "a hanging step must FAIL with 'timeout after 1s': rc=%d %s" % (rc, out)
    rc, out = run([sys.executable, os.path.join(c.t, "gate-lint-first"), "--repo", root], {"GATE_LINT_TIMEOUT": "x"})
    if rc != 2:
        return "bad GATE_LINT_TIMEOUT must be rc 2: rc=%d %s" % (rc, out)
    root = c.fixture("nooutput", override={"lint-boot-markers": ("bash", 3, "")})
    write(os.path.join(root, "tools", "lint-boot-markers"), "import sys\nsys.exit(3)\n", 0o644)
    rc, out = c.lint(root)
    if rc != 1 or "rc=3 (no output)" not in out:
        return "a silent nonzero step must FAIL naming its rc: rc=%d %s" % (rc, out)
    return None


def sc_usage(c):
    rc, out = run([sys.executable, os.path.join(c.t, "gate-lint-first"), "--bogus"])
    if rc != 2 or "RESULT: ERROR" not in out:
        return "gate-lint-first --bogus: rc=%d: %s" % (rc, out)
    rc, out = run([os.path.join(c.t, "gate-request"), "--repo", c.tmp])
    if rc != 2:
        return "gate-request without --label: rc=%d: %s" % (rc, out)
    return None


def sc_list(c):
    rc, out = c.lint(c.fixture("ls"), "--list")
    names = [x.split("\t")[0] for x in out.splitlines() if "\t" in x]
    if rc != 0 or names != STEPS or "RESULT: PASS gate-lint-first list=%d" % len(STEPS) not in out:
        return "--list wrong: rc=%d names=%s" % (rc, names)
    return None


SCENARIOS = [sc_needles_fail, sc_request_refused_on_lint_fail, sc_request_refused_on_lint_error, sc_all_pass, sc_request_starts_on_pass, sc_census_drift, sc_dirty_refused, sc_label, sc_timeout_and_rc, sc_usage, sc_list]

GLF = "gate-lint-first"
GR = "gate-request"
MUTANTS = {
    "step-not-run": [(GLF, "        verdict, dt, first = run_step(repo, name, cmd, timeout)",
                      '        if name == "mutant-needles":\n            continue\n        verdict, dt, first = run_step(repo, name, cmd, timeout)')],
    "failure-ignored": [(GLF, "    if p.returncode != 0:", "    if False:")],
    "wrong-name": [(GLF, "failed.append(name)", "failed.append(_gate)")],
    "skip-counted-as-pass": [(GLF, 'return "SKIP", 0, "(no tool)"', 'return "PASS", 0, ""')],
    "exit-0-on-fail": [(GLF, '[:200]))\n        return 1', '[:200]))\n        return 0')],
    "census-drift-not-checked": [(GLF, "        if bad:", "        if False:")],
    "census-threshold": [(GLF, "int(m.group(2)) > 0", "int(m.group(2)) > 99")],
    "dry-ignored": [(GR, 'if [ "${GATE_REQUEST_DRY:-}" = 1 ]; then', "if false; then")],
    "lint-failure-does-not-stop": [(GR, "if [ $rc -ne 0 ]; then\n\techo \"gate-request: not started", "if false; then\n\techo \"gate-request: not started")],
    "dirty-not-refused": [(GR, 'if [ -n "$st" ]; then', "if false; then")],
    "first-line-lost": [(GLF, "firsts[0].replace", "'x'.replace")],
    "census-strict-ignored": [(GLF, 'if os.environ.get("GATE_LINT_CENSUS_STRICT") == "1":', "if False:")],
    "census-warn-fails": [(GLF, 'return "PASS", dt, "WARN " + msg', 'return "FAIL", dt, msg')],
    "label-leading-dash": [(GR, "[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)", "*[!A-Za-z0-9._-]*)")],
    "label-guard-off": [(GR, "[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)", "NOMATCHZZ)")],
    "dirty-narrowed": [(GR, "status --porcelain 2>&1", "status --porcelain -- tools shim xnu 2>&1")],
    "timeout-ignored": [(GLF, "timeout=timeout)", "timeout=9999)")],
    "silent-rc-hidden": [(GLF, 'return "FAIL", dt, first_line(p.stdout) if p.stdout.strip() else "rc=%d (no output)" % p.returncode', 'return "FAIL", dt, first_line(p.stdout)')],
    "lint-rc2-as-pass": [(GR, "if [ $rc -ne 0 ]; then\n\techo \"gate-request: not started", "if [ $rc -eq 1 ]; then\n\techo \"gate-request: not started")],
    "skip-silent": [(GLF, '            print("SKIP %s (no tool)" % name)\n', "")],
}


def check(scenario, tooldir=TOOLS):
    c = Case(tooldir)
    try:
        return scenario(c)
    finally:
        c.cleanup()


MUTANT_GROUPS = 3


def mutant_group(i):
    """The mutants of group i (0..MUTANT_GROUPS-1): every MUTANT_GROUPS-th item of MUTANTS in table order; the groups partition MUTANTS."""
    return dict(list(MUTANTS.items())[i::MUTANT_GROUPS])


def check_runner_files(tc):
    """tools/test_gate_lint_first.py runs group 0, tools/test_gate_lint_first_m<i>.py group i (1..MUTANT_GROUPS-1): a missing, extra or mis-numbered runner file is a failure, not
    mutants that silently never run (review gate-cold-k14 r1 F7: with MUTANT_GROUPS=4 the extra group had no file and 5 mutants vanished)."""
    found = {n for n in os.listdir(TOOLS) if re.fullmatch(r"test_gate_lint_first_m[0-9]+\.py", n)}
    tc.assertEqual(found, {"test_gate_lint_first_m%d.py" % i for i in range(1, MUTANT_GROUPS)}, "runner files must match MUTANT_GROUPS=%d" % MUTANT_GROUPS)
    for i in range(1, MUTANT_GROUPS):
        with open(os.path.join(TOOLS, "test_gate_lint_first_m%d.py" % i)) as fh:
            tc.assertIn("run_mutants(self, %d)" % i, fh.read(), "test_gate_lint_first_m%d.py must run its own group" % i)


def run_mutants(tc, group):
    """Each mutant of `group` must be killed (at least one scenario returns an error); the same assertions as the old single test_mutants_killed."""
    check_runner_files(tc)
    muts = mutant_group(group)
    killed = []
    for name, edits in muts.items():
        tmp = tempfile.mkdtemp(prefix="glf-m-")
        try:
            td = make_tooldir(tmp, edits=edits)
            errs = [e for e in (check(sc, td) for sc in SCENARIOS) if e]
            with tc.subTest(mutant=name):
                tc.assertTrue(errs, "mutant %s SURVIVED (no scenario failed)" % name)
            if errs:
                killed.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("gate-lint-first mutants group %d killed %d/%d" % (group, len(killed), len(muts)))
    tc.assertEqual(len(killed), len(muts))
    tc.assertGreaterEqual(len(muts), 2)
