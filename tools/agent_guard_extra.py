"""Rule bodies moved out of tools/agent-guard so it does not grow (R29): E26 (python3 -m unittest FILE), E33 (a mutant test run directly, R30) and E3c (tools/fflash-userdata.sh without --reason, R54). Each takes the
guard's `verdict` function as an argument; the fixtures stay in agent-guard."""
import os
import re
import subprocess

HEAVY_TEST_RX = re.compile(r"(?:^|/)test_\w*mutants\w*\.py$")


def scan(heads):
    """-> (seen, bad): seen = a mutant test file occurs in some head; bad = its path when the head is an interpreter / the file itself and no heavy-job is in that head."""
    seen = False
    for p, a, _, _w in heads:
        hit = [x for x in ([p] + list(a)) if HEAVY_TEST_RX.search(x)]
        if not hit:
            continue
        seen = True
        wrapped = any("heavy-job" in x for x in a) or os.path.basename(p) == "heavy-job"
        direct = os.path.basename(p) in ("python3", "python", "bash", "sh") or HEAVY_TEST_RX.search(p)
        if direct and not wrapped:
            return True, hit[0]
    return seen, ""


def e26(c, verdict):
    """AP-118: `python3 -m unittest tools/test_x.py` (a file path) cannot import sibling modules."""
    um = [a for p, a, _, _ in c.heads if p in ("python3", "python") and "-m" in a and "unittest" in a]
    bad = [x for a in um if "discover" not in a for x in a[a.index("unittest") + 1:] if "/" in x and not x.startswith("-")]
    return verdict("E26", bool(um), ("`python3 -m unittest %s` with a file path fails to import sibling modules: 0 tests run, 'FAILED (errors=1)' (AP-118). "
                                     "Instead: `python3 -m unittest discover -s /abs/path/tools -p test_NAME.py` (absolute paths, no cd: AP-222). Escape: none.") % bad[0] if bad else "")


BG_RX = re.compile(r"(?<!&)&\s*(?:$|\n|\))")                      # a trailing/closing single & (not &&)
HEAVY_LOOP_RX = re.compile(r"-fsanitize|\bmut\w*\.py\b|\bmutat\w+")   # a sanitizer compile loop, a mut*.py harness, a mutation run


def bg_heavy(c):
    """A backgrounded compile/mutation loop run by hand (observed 2026-11-02: a thread's /tmp mutation harness held the host at load 12 and starved the gate queue 90 min)."""
    cmd = c.cmd or ""
    bg = c.i.get("run_in_background") is True or bool(BG_RX.search(cmd))
    return bool(HEAVY_LOOP_RX.search(cmd)) and bg and "heavy-job" not in cmd


def e33(c, verdict):
    """R30 / R17: a mutant test file run directly, or a backgrounded compile/mutation loop, fans out CPU-bound processes (load 21 on 16 cores, 2026-11-02) and starves the heavy-job gate queue."""
    seen, bad = scan(c.heads)
    loop = not bad and bg_heavy(c)
    msg = ""
    if bad:
        msg = "%s is a mutant test that runs ~nproc-2 CPU-bound processes at once" % bad
    elif loop:
        msg = "this backgrounded compile/mutation loop (-fsanitize / mut*.py / mutation) runs CPU-bound processes outside the load control"
    name = os.path.basename(bad) if bad else "SCRIPT"
    return verdict("E33", seen or loop or bg_heavy(c), (msg + "; run like this it pushes the machine to load 12-20+ and starves the gate queue (R17, AP-03). "
                   "Instead: `tools/heavy-job detach --label NAME -- tools/heavy-job run --class mutant --label NAME -- python3 %s`, then read its log. A run known to use under 120 CPU-s (one mutant run, one self-test) adds `--light` after `--label NAME` so it does not queue behind full gates (T-66a6; over the cap it is reported as HEAVY GAP). Escape: none.") % name if msg else "")


def e3c(c, verdict):
    """R54 (user 2026-10-31): a userdata flash only when the root image itself changed; tools/fflash-userdata.sh needs --reason "TEXT" (the script
    checks length and control chars; this hook only sees that the option is absent). Parsed like the other rules: bash -c, env, absolute path."""
    fl = [a for p, a, _, _ in c.heads if p == "fflash-userdata.sh"]
    return verdict("E3c", bool(fl), ("`tools/fflash-userdata.sh` without --reason (R54, AGENTS.md rule 3: a userdata flash costs minutes of wall time and erases "
                                     "userdata; most changes do not need one). Instead: a userland binary change: `tools/devsh push` + `devsh run` (hot reload); "
                                     "a kernel/shim/patch change: RAM boot with `tools/xrun`; only a changed root image needs a flash. "
                                     "Escape: add `--reason \"<why the root image changed, >= 10 chars>\"`.") if not all("--reason" in a for a in fl) else "")


def fixtures(add, B):
    FF = "tools/fflash-userdata.sh /x/root.img --yes-erase-userdata"   # E3c (R54): --reason required
    add("E3c fflash without --reason", B(FF), "deny:E3c")
    add("E3c fflash --force without --reason", B(FF + " --force"), "deny:E3c")
    add("E3c fflash absolute path, bash script", B("bash /h/kernel/tools/fflash-userdata.sh /x/root.img --yes-erase-userdata"), "deny:E3c")
    add("E3c fflash via bash -c", B("bash -c 'tools/fflash-userdata.sh /x/root.img --yes-erase-userdata'"), "deny:E3c")
    add("E3c fflash via env", B("env FOO=1 " + FF), "deny:E3c")
    add("E3c fflash with --reason", B(FF + ' --reason "root image rebuilt: new launchd"'), "allow")
    add("E3c fflash with --reason via bash -c", B("""bash -c 'tools/fflash-userdata.sh /x/r.img --yes-erase-userdata --reason "root image rebuilt"'"""), "allow")
    add("E3c grep of the script name", B("grep -n fflash-userdata.sh tools/TOOLS.md"), "allow")
    add("E3c cat of the script is not a flash", B("cat tools/fflash-userdata.sh"), "allow")
    import tempfile
    d = os.path.join(tempfile.mkdtemp(prefix="e34-"), "tools"); os.makedirs(d); open(os.path.join(d, "new-tool"), "w").close()   # a tools dir that has the scaffolder
    W = lambda f: dict(tool_name="Write", tool_input={"file_path": os.path.join(d, f), "content": "x"})
    add("E34 Write of a new tool", W("brand-new"), "deny:E34")
    add("E34 Write of a new module", W("brand_new.py"), "deny:E34")
    add("E34 Write of a new test", W("test_brand_new.py"), "allow")
    add("E34 Write of a doc", W("NOTES.md"), "allow")
    add("E34 Write over an existing tool", W("new-tool"), "allow")
    # E26 (AP-118): unittest with a file path
    add("E26 unittest tools/test_x.py", B("python3 -m unittest tools/test_envfile.py"), "deny:E26")
    add("E26 python3 -I -m unittest -v /abs/tools/test_x.py", B("python3 -I -m unittest -v /abs/tools/test_envfile.py"), "deny:E26")
    add("E26 unittest tools/test_x (no .py)", B("python3 -m unittest tools/test_envfile"), "deny:E26")
    add("E26 unittest discover -s /abs/tools", B("python3 -m unittest discover -s /abs/tools -p test_envfile.py"), "allow")
    add("E26 unittest module name", B("python3 -m unittest test_envfile"), "allow")
    # E33 (R17): mutant tests only through heavy-job
    add("E33 direct python3 mutant test", B("python3 tools/test_wlan_wr_mutants.py"), "deny:E33")
    add("E33 direct exec of a mutant test", B("tools/test_el2_mutants.py"), "deny:E33")
    add("E33 bash of a mutant test", B("bash -c 'cd x && python3 tools/test_wlan_dma_mutants.py'"), "deny:E33")
    add("E33 via heavy-job", B("tools/heavy-job run --class mutant --label x -- python3 tools/test_wlan_wr_mutants.py"), "allow")
    add("E33 bg sanitizer mutation harness", B("cd /tmp/h && (python3 mut3.py out -g -fsanitize=address > o.txt 2>&1) &"), "deny:E33")
    add("E33 run_in_background mutation loop", dict(tool_name="Bash", tool_input={"command": "python3 mut3.py out", "run_in_background": True}), "deny:E33")
    add("E33 same loop via heavy-job", B("tools/heavy-job detach --label x -- tools/heavy-job run --class mutant --label x -- python3 mut3.py out &"), "allow")
    add("E33 plain gcc not backgrounded", B("gcc -fsanitize=address -c x.c -o x.o"), "allow")
    add("E33 other test", B("python3 tools/test_lint_size.py"), "allow")


def e34(c, verdict):
    """R30: a tool is created with `tools/new-tool`, not by a Write of a new tools/NAME file (the census, lifecycle, TOOLS.md and test rows would be forgotten). Existing files may be edited."""
    p = (c.i.get("file_path") or "") if c.t == "Write" else ""
    m = re.search(r"/tools/([^/]+)$", p)
    n = m.group(1) if m else ""
    new_tool = bool(n) and not n.startswith("test_") and (n.endswith((".py", ".sh")) or "." not in n) and os.path.exists(os.path.join(os.path.dirname(p), "new-tool"))
    return verdict("E34", new_tool, ("a new tools/%s written by hand skips the registries (census, tools-lifecycle.tsv, TOOLS.md, test) that broke gates on 2026-11-02 (R30). "
                   "Instead: `tools/new-tool %s --purpose \"what it does and who calls it\"` (add `--ext py` for a module), then edit the skeleton. Escape: none.") % (n, n.rsplit(".", 1)[0]) if not os.path.exists(p) else "")


def where(h):
    """T-7dff: the checkout and branch a denied command ran in (wrong checkout/worktree was the most repeated agent mistake, 31 of 369 incidents)."""
    cwd = h.get("cwd") if isinstance(h.get("cwd"), str) and os.path.isdir(h.get("cwd")) else os.getcwd()
    out = []
    for a in (["rev-parse", "--show-toplevel"], ["rev-parse", "--abbrev-ref", "HEAD"]):
        try:
            r = subprocess.run(["git", "-C", cwd] + a, capture_output=True, text=True, timeout=5)
            out.append(r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "?")
        except (OSError, subprocess.SubprocessError):
            out.append("?")
    return " [where: checkout=%s branch=%s; if this is not the tree you meant, cd to the ticket worktree (`tools/queue worktree T-xxxx`)]" % tuple(out)


PLAN_CHECK_RX = re.compile(r"\bPLAN-CHECK (T-[0-9a-f]{4})\b")


def plan_check_ok(c, notes):
    """S4b scope (T-dd8e, user 2026-11-08 "Opus plans, Sonnet builds"): the ONE Opus launch besides HW-WRITE reviews is the read-only plan check of a stream/HW-WRITE ticket:
    an Agent/delegate launch whose brief names `PLAN-CHECK T-xxxx`, whose subagent_type is a read-only role (researcher | reviewer) and whose brief does not ask for edits.
    -> True (and a loud note appended to `notes`) when it qualifies; an implementer, a general-purpose or a brief that implements stays denied."""
    m = PLAN_CHECK_RX.search(c.prompt or "")
    tg = c.i.get("target")
    st = c.i.get("subagent_type") or (tg.get("subagent_type") if isinstance(tg, dict) else None)
    if not m or st not in ("researcher", "reviewer") or re.search(r"\b(implement|edit the|write the code|apply the patch)\b", c.prompt or "", re.I):
        return False
    notes.append("agent-guard S4b ALLOW Opus plan check %s (read-only %s, T-dd8e)" % (m.group(1), st))
    return True
