#!/usr/bin/env python3
"""
test_devsh_harness.py: host test for tools/devsh against a live devd test instance (loopback only).

Checks the CLI contract: push/pull/run/ping/signal over a real devd built fresh from tools/devd.c,
the exit codes 0/1/2 with a final `RESULT:` line (review gemini-devd-r1 F5), run's remote status
taken from the LAST line, the phone-lock gate and the run-plan gate (F1 containment).
Paths resolve from this file's directory, never from the cwd. Prints raw counts and a RESULT line;
exit 0 all passed, 1 a check failed, 2 the harness could not run.
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

import test_devd_lib as lib

HERE = os.path.dirname(os.path.abspath(__file__))
DEVD_SRC = os.path.join(HERE, "devd.c")
# test_devd_mutants.py runs this file against a mutated copy of devsh (DEVSH_MUT_SRC)
DEVSH = os.environ.get("DEVSH_MUT_SRC") or os.path.join(HERE, "devsh")
PHONE_LOCK = os.path.join(HERE, "phone-lock")

passed = 0
failed = []
KEY_FILE = None  # set in run(): the key file devsh reads via $DEVD_KEY_FILE


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"[+] {name}: PASS")
    else:
        failed.append(name)
        print(f"[-] {name}: FAIL {detail}")


def free_port():
    p = socket.socket()
    p.bind(("127.0.0.1", 0))
    port = p.getsockname()[1]
    p.close()
    return port


def devsh(port, *args, host="127.0.0.1", env_extra=None, timeout=60):
    env = dict(os.environ)
    for k in ("DEVSH_TEST_LOOPBACK", "DEVSH_RUNPLAN", "PHONE_LOCK_TOKEN"):
        env.pop(k, None)
    if KEY_FILE:
        env["DEVD_KEY_FILE"] = KEY_FILE
    env.update(env_extra or {})
    return subprocess.run([sys.executable, DEVSH, "--host", host, "--port", str(port), "--timeout", "5", *args],
                          capture_output=True, text=True, env=env, timeout=timeout)


def last_line(res):
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def fake_server(reply: bytes):
    """One-shot server: sends a CHALLENGE, reads the AUTH line and the request line, writes `reply`,
    closes. Returns (port, thread)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        c.settimeout(3.0)
        try:
            c.sendall(b"CHALLENGE " + b"00" * 16 + b"\n")
            data = b""
            while data.count(b"\n") < 2:  # AUTH line, then the request line
                chunk = c.recv(1024)
                if not chunk:
                    break
                data += chunk
            c.sendall(reply)
        except OSError:  # lint-silent: expected the client under test hung up first
            pass
        c.close()
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return port, t


def _ping_push_pull(work, port, tb, lb):
    """Sections ping, push/pull and the E9 /proc push of run() (split for the R29 function limit)."""
    # ping needs no lock
    r = devsh(port, "ping")
    check("ping: exit 0, PONG, RESULT: PASS line",
          r.returncode == 0 and "PONG" in r.stdout and last_line(r).startswith("RESULT: PASS"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # push / pull (tmp dst under /tmp: devd test-mode allowlist)
    src = os.path.join(work, "source.txt")
    dst = os.path.join(work, "destination.txt")
    with open(src, "w") as f:
        f.write("PixOS DevSH Hot Reload Payload\n")
    r = devsh(port, "push", src, dst, env_extra=lb)
    ok = r.returncode == 0 and last_line(r).startswith("RESULT: PASS") and os.path.exists(dst)
    if ok:
        with open(dst) as f:
            ok = f.read() == "PixOS DevSH Hot Reload Payload\n"
    check("push: exit 0, content identical, RESULT: PASS", ok, f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    pulled = os.path.join(work, "pulled.txt")
    r = devsh(port, "pull", dst, pulled, env_extra=lb)
    ok = r.returncode == 0 and last_line(r).startswith("RESULT: PASS") and os.path.exists(pulled)
    if ok:
        with open(pulled) as f:
            ok = f.read() == "PixOS DevSH Hot Reload Payload\n"
    check("pull: exit 0, content identical, RESULT: PASS", ok, f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    r = devsh(port, "push", src, "/etc/devsh-harness-evil", env_extra=lb)
    check("push rejected by devd: exit 1, RESULT: FAIL",
          r.returncode == 1 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # review devd-r1 F1 (E9): /proc/self/environ reports size 0 but holds bytes; one of them is a RUN line.
    # devsh must send exactly the bytes it read (a push that succeeds, the RUN line stored as data)
    # and never let them reach devd's command parser.
    smug = os.path.join(work, "smuggled-marker")
    envdst = os.path.join(work, "environ.pushed")
    r = devsh(port, "push", "/proc/self/environ", envdst,
              env_extra={**lb, "DEVSH_SMUGGLE": f"x\nRUN {tb} touch {smug}\n"})
    time.sleep(0.5)
    pushed = open(envdst, "rb").read() if os.path.exists(envdst) else b""
    check("push of a file that reports size 0 (/proc/self/environ): body never runs as commands, bytes sent == bytes read (F1)",
          not os.path.exists(smug) and last_line(r).startswith("RESULT: PASS") and b"RUN " in pushed
          and f"({len(pushed)} bytes)" in r.stdout,
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r} marker={os.path.exists(smug)} pushed={len(pushed)})")
    empty = os.path.join(work, "empty.bin")
    open(empty, "wb").close()
    r = devsh(port, "push", empty, os.path.join(work, "empty.pushed"), env_extra=lb)
    check("push of an empty file refused before any connect: exit 2, RESULT: FAIL (F1)",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL")
          and not os.path.exists(os.path.join(work, "empty.pushed")),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    r = devsh(port, "pull", "/etc/passwd", os.path.join(work, "nope"), env_extra=lb)
    check("pull rejected by devd: exit 1, RESULT: FAIL",
          r.returncode == 1 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    return src


def _run_signal_framing(work, port, tb, lb, devd_bin):
    """Sections run, signal, connection drop and RUNID/RUNEND framing of run() (split for the R29 function limit)."""
    # run: status from the remote command
    r = devsh(port, "run", tb, "echo", "devsh exec ok", env_extra=lb)
    check("run ok: exit 0, output streamed, RESULT: PASS",
          r.returncode == 0 and "devsh exec ok" in r.stdout and last_line(r).startswith("RESULT: PASS"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    r = devsh(port, "run", tb, "rc", "3", env_extra=lb)
    check("run rc 3: devsh exit 1 (was 0), RESULT: FAIL naming rc=3",
          r.returncode == 1 and last_line(r).startswith("RESULT: FAIL") and "rc=3" in last_line(r),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    r = devsh(port, "run", tb, "echorc", "3", "RC:", "0", env_extra=lb)
    check("run printing 'RC: 0' then failing: nonzero (the LAST line is the status)",
          r.returncode == 1 and "RC: 0" in r.stdout and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    r = devsh(port, "run", tb, "noeol", env_extra=lb)
    check("run output without trailing newline still reports rc 0",
          r.returncode == 0 and "no newline" in r.stdout and last_line(r).startswith("RESULT: PASS"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # signal: pid 1 rejected by devd
    r = devsh(port, "signal", "1", "9", env_extra=lb)
    check("signal rejected by devd: exit 1, RESULT: FAIL",
          r.returncode == 1 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # connection drop without an RC line
    p2, t2 = fake_server(b"partial output\n")
    r = devsh(p2, "run", "/usr/local/bin/anything", env_extra=lb)
    t2.join(5)
    check("run, connection dropped without RC: exit 2, RESULT: FAIL",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    p2, t2 = fake_server(b"fake\nRC: 0")  # RC line cut before its newline
    r = devsh(p2, "run", "/usr/local/bin/anything", env_extra=lb)
    t2.join(5)
    check("run, RC line cut mid-line: exit 2, not 0",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # review devd-r1 F3: RUNID / RUNEND framing. The status is only the exact RUNEND line for this run's id,
    # directly followed by EOF.
    r = devsh(port, "run", tb, "forge", env_extra=lb)
    check("run: a forged RUNEND line in the output does not set the status (rc 5 wins, exit 1)",
          r.returncode == 1 and "RUNEND 0000000000000000 RC: 0" in r.stdout and "rc=5" in last_line(r),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    good = b"RUNID 0123456789abcdef\nout line\nRUNEND 0123456789abcdef RC: 0\n"
    for label, reply, want_rc in (
            ("exact RUNEND line then EOF", good, 0),
            ("RUNEND with another id", good.replace(b"RUNEND 0123", b"RUNEND 4123"), 2),
            ("data after the RUNEND line", good + b"extra\n", 2),
            ("no RUNID line", b"out line\nRUNEND 0123456789abcdef RC: 0\n", 2),
            ("RUNEND without a newline", good[:-1], 2)):
        p2, t2 = fake_server(reply)
        r = devsh(p2, "run", "/usr/local/bin/anything", env_extra=lb)
        t2.join(5)
        check(f"run, fake devd: {label}: exit {want_rc}",
              r.returncode == want_rc and last_line(r).startswith("RESULT: PASS" if want_rc == 0 else "RESULT: FAIL"),
              f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # E8b: the program prints `RC: 0`, kills devd and exits 3: devsh must not report a pass
    port3 = free_port()
    log3 = open(os.path.join(work, "devd3.log"), "w")
    devd3 = subprocess.Popen([devd_bin, "--port", str(port3), "--test-mode", "--key-file", KEY_FILE],
                             stdout=log3, stderr=subprocess.STDOUT)
    time.sleep(0.2)
    try:
        r = devsh(port3, "run", tb, "spoof", env_extra=lb)
        time.sleep(0.3)
        check("run: a program that prints `RC: 0` and kills devd is NOT a pass (exit 2, RESULT: FAIL) (F3, E8b)",
              r.returncode == 2 and last_line(r).startswith("RESULT: FAIL") and devd3.poll() is not None,
              f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r} devd_dead={devd3.poll() is not None})")
    finally:
        if devd3.poll() is None:
            devd3.kill()
        devd3.wait()
        log3.close()


def _refused_auth_keys(work, port, lb, src):
    """Sections refused/usage and auth (key file) rows of run() (split for the R29 function limit)."""
    # connection refused / usage
    r = devsh(free_port(), "ping")
    check("ping, nothing listening: exit 2, RESULT: FAIL",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    r = devsh(port, "bogus-subcommand")
    check("unknown subcommand: exit 2, RESULT: FAIL",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    r = devsh(port, "push", src, "relative/path", env_extra=lb)
    check("push with a relative device path: exit 2, RESULT: FAIL",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    # auth: missing / malformed key file ends before any connect; no shell, no relative program
    r = devsh(port, "ping", env_extra={"DEVD_KEY_FILE": os.path.join(work, "no-such.key")})
    check("ping with a missing key file: exit 2, RESULT: FAIL, key file named",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL") and "no-such.key" in r.stderr,
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    bad = os.path.join(work, "bad.key")
    with open(bad, "w") as f:
        f.write("not hex\n")
    r = devsh(port, "ping", env_extra={"DEVD_KEY_FILE": bad})
    check("ping with a malformed key file: exit 2, RESULT: FAIL",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    os.makedirs(os.path.join(work, "wrongkey"))
    wrong, _ = lib.make_key_file(os.path.join(work, "wrongkey"))
    r = devsh(port, "ping", env_extra={"DEVD_KEY_FILE": wrong})
    check("ping with the wrong key: exit 1, ERR auth, RESULT: FAIL",
          r.returncode == 1 and "ERR: auth" in r.stderr and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    r = devsh(port, "run", "echo", "hi", env_extra=lb)
    check("run with a relative program: exit 2 before connecting",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    r = devsh(port, "run", "/bin/sh", "-c", "id", env_extra=lb)
    check("run /bin/sh -c id: rejected by devd (not under /usr/local or /private/var/dev), exit 1",
          r.returncode == 1 and last_line(r).startswith("RESULT: FAIL"),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    r = devsh(port, "reload", "com.apple.SpringBoard", env_extra=lb)
    check("reload is gone: exit 2, RESULT: FAIL, hint names push / signal / run",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL")
          and "no `devsh reload`" in r.stderr and "devsh push SRC /usr/local/bin/NAME" in r.stderr
          and "devsh signal OLDPID 15" in r.stderr and "devsh run /usr/local/bin/NAME ARGS" in r.stderr,
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    h = subprocess.run([sys.executable, DEVSH, "--help"], capture_output=True, text=True)
    check("--help documents the push / signal / run workflow and no reload subcommand",
          h.returncode == 0 and "devsh push SRC /usr/local/bin/NAME" in h.stdout
          and "SIGTERM allowed" in h.stdout and "{ping,push,pull,run,signal}" in h.stdout
          and "BRICK RISK" in h.stdout and ".sweetorig" in h.stdout and "userdata reflash" in h.stdout
          and "RAM boot" in h.stdout,
          f"(rc={h.returncode} out={h.stdout[-300:]!r})")


def _lock_plan_keyperm(work, port, tb):
    """Sections phone-lock, run-plan gate and key-file permission rows of run() (split for the R29 function limit)."""
    # phone-lock gate (private lock dir, no phone involved)
    lockdir = os.path.join(work, "phone.lock")
    marker = os.path.join(work, "gate-marker")
    penv = {"PHONE_LOCK_DIR": lockdir, "DEVD_KEY_FILE": KEY_FILE}
    r = devsh(port, "run", f"{tb} touch {marker}", env_extra=penv)
    check("run without the phone lock: refused, exit 2, nothing executed",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL") and not os.path.exists(marker),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r} marker={os.path.exists(marker)})")

    r = devsh(port, "run", f"{tb} touch {marker}", env_extra={**penv, "DEVSH_TEST_LOOPBACK": "1"}, host="127.0.0.2")
    check("DEVSH_TEST_LOOPBACK=1 refused for a host other than 127.0.0.1 (exit 2)",
          r.returncode == 2 and "DEVSH_TEST_LOOPBACK" in r.stderr and not os.path.exists(marker),
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")

    held = subprocess.run(
        [sys.executable, PHONE_LOCK, "run", "--owner", "devsh-harness", "--", sys.executable, DEVSH,
         "--host", "127.0.0.1", "--port", str(port), "--timeout", "5", "run", f"{tb} touch {marker}"],
        capture_output=True, text=True, timeout=60,
        env={**{k: v for k, v in os.environ.items() if k not in ("PHONE_LOCK_TOKEN", "DEVSH_TEST_LOOPBACK")}, **penv})
    check("run under `phone-lock run` (lock held by the caller): allowed",
          held.returncode == 0 and os.path.exists(marker) and "RESULT: PASS run" in held.stdout,
          f"(rc={held.returncode} out={held.stdout[-200:]!r} err={held.stderr[-200:]!r})")

    # run-plan gate: a target other than 127.0.0.1 (127.0.0.2 is local, never the phone)
    plandir = os.path.join(work, "docs", "runplans")
    os.makedirs(plandir)
    plan = os.path.join(plandir, "t.md")
    with open(plan, "w") as f:
        f.write("# plan\n")
    cmd = [sys.executable, PHONE_LOCK, "run", "--owner", "devsh-harness", "--", sys.executable, DEVSH,
           "--host", "127.0.0.2", "--port", str(port), "--timeout", "2", "run", f"{tb} touch {marker}2"]
    base_env = {k: v for k, v in os.environ.items() if k not in ("PHONE_LOCK_TOKEN", "DEVSH_TEST_LOOPBACK", "DEVSH_RUNPLAN")}
    base_env.update(penv)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=base_env)
    check("non-loopback target without DEVSH_RUNPLAN: refused before connecting (exit 2)",
          r.returncode == 2 and "DEVSH_RUNPLAN" in r.stderr and not os.path.exists(marker + "2"),
          f"(rc={r.returncode} out={r.stdout[-200:]!r} err={r.stderr[-200:]!r})")
    bogus = os.path.join(work, "notaplan.md")
    with open(bogus, "w") as f:
        f.write("x\n")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env={**base_env, "DEVSH_RUNPLAN": bogus})
    check("DEVSH_RUNPLAN outside a docs/runplans/ dir: refused (exit 2)",
          r.returncode == 2 and "DEVSH_RUNPLAN" in r.stderr,
          f"(rc={r.returncode} err={r.stderr[-200:]!r})")
    # review devd-r1 F9: the plan must be inside THE super repo's docs/runplans/ ($PIXOS_RUNPLAN_DIR here); a
    # decoy /…/docs/runplans/x.md elsewhere (the first `plan`, which sits in work/docs/runplans) is not a plan
    superplans = os.path.join(work, "super", "docs", "runplans")
    os.makedirs(superplans)
    realplan = os.path.join(superplans, "t.md")
    with open(realplan, "w") as f:
        f.write("# plan\n")
    os.symlink(plan, os.path.join(superplans, "link.md"))
    pd = {**base_env, "PIXOS_RUNPLAN_DIR": superplans}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env={**pd, "DEVSH_RUNPLAN": realplan})
    check("valid DEVSH_RUNPLAN (inside the super docs/runplans/) passes the gate (then fails to connect: devd listens on 127.0.0.1 only)",
          r.returncode == 2 and "DEVSH_RUNPLAN" not in r.stderr and "phone lock" not in r.stderr
          and not os.path.exists(marker + "2"),
          f"(rc={r.returncode} err={r.stderr[-200:]!r})")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env={**pd, "DEVSH_RUNPLAN": plan})
    check("decoy <elsewhere>/docs/runplans/t.md is refused (F9)",
          r.returncode == 2 and "DEVSH_RUNPLAN" in r.stderr and not os.path.exists(marker + "2"),
          f"(rc={r.returncode} err={r.stderr[-200:]!r})")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                       env={**pd, "DEVSH_RUNPLAN": os.path.join(superplans, "link.md")})
    check("a symlink inside the super docs/runplans/ that points at a decoy is refused (F9)",
          r.returncode == 2 and "DEVSH_RUNPLAN" in r.stderr and not os.path.exists(marker + "2"),
          f"(rc={r.returncode} err={r.stderr[-200:]!r})")

    # review devd-r1 F11: a key file other users can read is refused before any connect
    loose = os.path.join(work, "loose")
    os.makedirs(loose)
    lpath, _ = lib.make_key_file(loose, mode=0o644)
    r = devsh(port, "ping", env_extra={"DEVD_KEY_FILE": lpath})
    check("key file with mode 0644: refused, exit 2, mode named (F11)",
          r.returncode == 2 and last_line(r).startswith("RESULT: FAIL") and "0644" in r.stderr,
          f"(rc={r.returncode} out={r.stdout!r} err={r.stderr!r})")
    import importlib.machinery, importlib.util
    from unittest import mock
    loader = importlib.machinery.SourceFileLoader("devsh_mod", DEVSH)
    spec = importlib.util.spec_from_loader("devsh_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    with mock.patch.dict(os.environ, {"DEVD_KEY_FILE": KEY_FILE}):
        ok_key = len(mod.load_key()) == 32
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            try:
                mod.load_key()
                owner_refused = False
            except mod.Fail as e:
                owner_refused = "owned by you" in str(e)
    check("key file owned by another uid is refused, the caller's own 0600 file loads (F11)",
          ok_key and owner_refused, f"(ok_key={ok_key} owner_refused={owner_refused})")


def run():
    global KEY_FILE
    work = tempfile.mkdtemp(prefix="devsh-harness-", dir="/tmp")
    devd_bin = os.path.join(work, "devd")
    c = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-o", devd_bin, DEVD_SRC], capture_output=True, text=True)
    if c.returncode != 0:
        print(f"RESULT: ERROR could not compile devd: {c.stderr[-200:]}")
        return 2

    KEY_FILE, _key = lib.make_key_file(work)
    tb = lib.compile_testbin(work)
    port = free_port()
    log = open(os.path.join(work, "devd.log"), "w")
    devd = subprocess.Popen([devd_bin, "--port", str(port), "--test-mode", "--key-file", KEY_FILE],
                            stdout=log, stderr=subprocess.STDOUT)
    time.sleep(0.2)
    lb = {"DEVSH_TEST_LOOPBACK": "1"}
    try:
        src = _ping_push_pull(work, port, tb, lb)
        _run_signal_framing(work, port, tb, lb, devd_bin)
        _refused_auth_keys(work, port, lb, src)
        _lock_plan_keyperm(work, port, tb)
    finally:
        devd.terminate()
        devd.wait()
        log.close()

    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} {passed} passed, {len(failed)} failed"
          + (f" -> {failed}" if failed else ""))
    return 0 if not failed else 1


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
ScriptRun = lib.script_case(__file__, 300)


if __name__ == "__main__":
    sys.exit(run())
