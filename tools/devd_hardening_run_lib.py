"""devd hardening test groups (run): split out of test_devd_hardening.py (T-c24f, R29 size limit); shared state lives in devd_hardening_lib."""
import atexit, json, os, re, shutil, signal, socket, subprocess, sys, tempfile, time

import devd_hardening_lib as H
import test_devd_lib as lib


def test_run_pipelined_trailing_ok(d: H.Daemon) -> None:
    """Five bytes of payload followed by '\n' is legal, not a desync."""
    path = os.path.join(H.TMP_DIR, "desync", "nl.bin")
    s = d.conn()
    s.sendall(b"PUSH " + path.encode() + b" 5\nHELLO\n")
    r1 = H.recv_line(s, 5.0)
    s.close()
    with open(path, "rb") as f:
        content = f.read()
    H.check("PUSH whose payload ends in a newline is accepted",
          r1 == b"OK\n" and content == b"HELLO", f"(r1={r1!r} content={content!r})")


def test_run_timeout(d: H.Daemon) -> None:
    t0 = time.monotonic()
    s = d.conn()
    s.sendall(f"RUN {H.TB} sleep 30\n".encode())
    resp = H.recv_until(s, b"RC: ", 8.0)
    dt = time.monotonic() - t0
    s.close()
    H.check("RUN sleep 30 returns RC:124 within ~3s",
          b"RC: 124" in resp and dt < 6.0, f"(dt={dt:.1f}s resp={resp[-40:]!r})")
    s2 = d.conn()
    s2.sendall(b"PING\n")
    H.check("daemon alive and serving after RUN timeout",
          d.alive() and s2.recv(64).startswith(b"PONG"))
    s2.close()


def test_run_grandchild_killed(d: H.Daemon) -> None:
    s = d.conn()
    s.sendall(f"RUN {H.TB} bgkeep\n".encode())
    resp = H.recv_until(s, b"RC: ", 8.0)
    s.close()
    H.check("grandchild RUN echoes then RC:124", b"grand" in resp and b"RC: 124" in resp)

    # The killed group must be empty. pgrep -f 'sleep 30' is useless here:
    # it matches unrelated sessions on a shared host.
    pgid = None
    with open(d.log.name) as f:
        for line in f:
            if "killed pgrp" in line:
                pgid = int(line.split("killed pgrp")[1].split()[0])
    ok = False
    if pgid is not None:
        ok = True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
                time.sleep(0.05)
            except ProcessLookupError:  # lint-silent: expected the process group is gone (the wait loop's exit)
                break
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:  # lint-silent: expected the process group already exited
            pass
        else:
            ok = False
    H.check("killed process group is empty (no orphans)", ok, f"(pgid={pgid})")


def test_stalled_client_no_freeze(d: H.Daemon) -> None:
    """Deaf client + pipe-flooding child must not wedge the daemon."""
    s = d.conn(timeout=5.0)
    t_start = time.monotonic()
    s.sendall(f"RUN {H.TB} yes appended-padding-to-flood-the-pipe-fast\n".encode())

    recovered = False
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        probe = None
        try:
            probe = d.conn(timeout=2.0)
            probe.sendall(b"PING\n")
            if probe.recv(64).startswith(b"PONG"):
                recovered = True
                break
        except OSError:
            time.sleep(0.25)
        finally:
            if probe is not None:
                probe.close()
    dt = time.monotonic() - t_start
    s.close()
    H.check("daemon recovers from deaf-client RUN flood within 25s",
          recovered and d.alive())
    # The send budget is 10 s. A daemon that drops the gone client serves the next one right after
    # it (~10-13 s here); one that keeps waiting on the dead connection also burns the 10 s read
    # window of the next command (>= 20 s).
    H.check("deaf-client RUN flood: the next client is served within 16 s (a gone client is dropped, not waited on)",
          recovered and dt < 16.0, f"(dt={dt:.1f}s)")


def test_signal_guards(d: H.Daemon) -> None:
    s = d.conn()
    victim = subprocess.Popen(["sleep", "60"])
    time.sleep(0.1)

    s.sendall(f"SIGNAL {d.proc.pid} 9\n".encode())
    H.check("self-kill (SIGNAL devdpid 9) rejected",
          b"ERR" in s.recv(64) and d.alive())

    s.sendall(b"SIGNAL 0 9\n")
    H.check("SIGNAL 0 9 (own process group) rejected", b"ERR" in s.recv(64))

    s.sendall(b"SIGNAL -1 9\n")
    H.check("negative pid rejected", b"ERR" in s.recv(64))

    s.sendall(f"SIGNAL {victim.pid} 3\n".encode())
    H.check("SIGQUIT (3) outside allowlist rejected",
          b"ERR" in s.recv(64))

    s.sendall(b"SIGNAL 99999 99\n")
    H.check("signo 99 rejected", b"ERR" in s.recv(64))

    s.sendall(b"SIGNAL 99999 -9\n")
    H.check("negative signo (group kill) rejected", b"ERR" in s.recv(64))

    s.sendall(f"SIGNAL {victim.pid} 15\n".encode())
    H.check("SIGTERM on a live pid still works", b"OK" in s.recv(64))
    victim.wait(timeout=5.0)

    victim2 = subprocess.Popen(["sleep", "60"])
    time.sleep(0.1)
    s.sendall(f"SIGNAL {victim2.pid} 9\n".encode())
    H.check("SIGKILL on an ordinary pid is allowed",
          b"OK" in s.recv(64) and victim2.wait(timeout=5.0) == -9)
    s.close()


def test_run_policy(d: H.Daemon) -> None:
    """(e) RUN has no shell and only runs allowlisted, resolved, executable regular files."""
    marker = os.path.join(H.TMP_DIR, "run-marker")
    if os.path.exists(marker):
        os.unlink(marker)
    noexec = os.path.join(H.TMP_DIR, "noexec")
    with open(noexec, "w") as f:
        f.write("#!/bin/sh\ntouch " + marker + "\n")
    os.chmod(noexec, 0o644)
    shlink = os.path.join(H.TMP_DIR, "shlink")
    if os.path.lexists(shlink):
        os.unlink(shlink)
    os.symlink("/bin/sh", shlink)

    rejects = [
        ("/bin/sh -c id", b"/bin/sh -c id"),
        ("/tmp/../bin/ls", b"/tmp/../bin/ls"),
        ("/bin/ls (not allowlisted)", b"/bin/ls"),
        ("ls (relative)", b"ls"),
        ("./tb (relative)", b"./tb"),
        ("/usr/bin/id", b"/usr/bin/id"),
        ("/usr/lib/anything (PUSH root, not a RUN root)", b"/usr/lib/anything"),
        ("symlink to /bin/sh inside /tmp", shlink.encode() + b" -c id"),
        ("allowlisted but not executable", noexec.encode()),
        ("allowlisted but missing", b"/usr/local/bin/no-such-program-devd-test"),
        ("a directory", H.TMP_DIR.encode() + b"/keys"),
        ("empty argument (double space)", H.TB.encode() + b"  x"),
        ("17 arguments", H.TB.encode() + b" echo " + b" ".join(b"a" * 1 for _ in range(16))),
        ("empty program", b""),
    ]
    for label, line in rejects:
        s = d.conn()
        s.sendall(b"RUN " + line + b"\n")
        r = H.recv_line(s, 5.0)
        s.sendall(b"PING\n")  # the connection stays usable
        r2 = H.recv_line(s, 5.0)
        s.close()
        H.check(f"(e) RUN {label} rejected", r.startswith(b"ERR: RUN") and b"RC:" not in r and b"PONG" in r2,
              f"(r={r!r} r2={r2!r})")
    H.check("(e) no rejected RUN executed anything", not os.path.exists(marker))

    # 16 args is the maximum and runs
    s = d.conn()
    s.sendall(f"RUN {H.TB} echo ".encode() + b" ".join(str(i).encode() for i in range(15)) + b"\n")  # echo + 15 = 16
    resp = H.recv_until(s, b"RC: ", 5.0)
    s.close()
    H.check("(e) 16 arguments run", b"RC: 0" in resp and b"14" in resp, f"(resp={resp!r})")

    # metacharacters are plain argument text
    s = d.conn()
    s.sendall(f"RUN {H.TB} echo a;touch {marker} $(id) `id` >{marker} |cat *\n".encode())
    resp = H.recv_until(s, b"RC: ", 5.0)
    s.close()
    H.check("(e) shell metacharacters are literal argument text, nothing interpreted",
          b"a;touch" in resp and b"$(id)" in resp and b"RC: 0" in resp and not os.path.exists(marker),
          f"(resp={resp!r})")

    # allowlisted test binary runs; its RC passes through
    s = d.conn()
    s.sendall(f"RUN {H.TB} echorc 7 hello world\n".encode())
    resp = H.recv_until(s, b"RC: ", 5.0)
    s.close()
    H.check("(e) allowlisted program runs, output + RC 7 pass through",
          b"hello world" in resp and resp.rstrip().endswith(b"RC: 7"), f"(resp={resp!r})")

    # PUSH policy edges the host can reach (positive cases of the kernel-equal table are in --selftest)
    for path in ("/usr/lib/", "/System/Library/LaunchDaemons/evil.plist",
                 "/System/Library/LaunchDaemons/org.pixelios.a/b", "/System/Library/xpc/launchd.plist.x",
                 "/private/var/mobile/x", "/usr/bin/x"):
        s = d.conn()
        s.sendall(f"PUSH {path} 1\nX".encode())
        r = H.recv_line(s, 5.0)
        s.close()
        H.check(f"(g) PUSH {path} refused by the kernel-equal allowlist", b"ERR: path not allowed" in r, f"(r={r!r})")


def test_run_child_env(d: H.Daemon) -> None:
    """Review devd-r1 F13: the RUN child gets a fixed environment and default SIGPIPE, and a grandchild that dropped
    the pipe does not outlive a normal RUN exit."""
    resp, rid = H.run_framed(d, f"{H.TB} env")
    lines = [ln for ln in resp.decode(errors="replace").split("\n") if "=" in ln]
    H.check("RUN child environment is exactly PATH and HOME (F13)",
          sorted(lines) == ["HOME=/var/root", "PATH=/usr/bin:/bin:/usr/sbin:/sbin"], f"(env={lines!r})")
    resp, rid = H.run_framed(d, f"{H.TB} sigpipe")
    H.check("RUN child has SIGPIPE at its default action (F13)", b"sigpipe_ignored=0" in resp, f"(resp={resp!r})")
    resp, rid = H.run_framed(d, f"{H.TB} bgsurvive")
    try:
        gpid = int(resp.split(b"\n")[1])
    except (ValueError, IndexError):
        print(f"DEVD GAP: no grandchild pid in the RUN reply {resp!r}", file=sys.stderr)
        gpid = None
    alive = True
    deadline = time.monotonic() + 3.0
    while gpid is not None and time.monotonic() < deadline:
        alive = H.pid_alive(gpid)
        if not alive:
            break
        time.sleep(0.05)
    if gpid is not None and alive:
        try:
            os.kill(gpid, signal.SIGKILL)  # recorded PID only
        except ProcessLookupError:  # lint-silent: expected the recorded grandchild already exited
            pass
    time.sleep(0.2)
    with open(d.log.name) as f:
        log = f.read()
    H.check("a grandchild that dropped the pipe is killed after a normal RUN exit, with a SWEET GAP line (F13)",
          gpid is not None and not alive and "SWEET GAP: devd RUN left background processes" in log,
          f"(gpid={gpid} alive={alive} resp={resp!r})")


def test_deaf_client_group_killed(d: H.Daemon) -> None:
    """Review devd-r1 R12: a deaf client of a flooding RUN with a pipe-dropping grandchild: the client-gone path
    kills the whole process group, not just the child."""
    pidfile = os.path.join(H.TMP_DIR, "bgflood.pid")
    if os.path.exists(pidfile):
        os.unlink(pidfile)
    s = d.conn(timeout=5.0)
    s.sendall(f"RUN {H.TB} bgflood {pidfile}\n".encode())  # and never read: the send budget (10 s) runs out
    recovered = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        probe = None
        try:
            probe = d.conn(timeout=2.0)
            probe.sendall(b"PING\n")
            if probe.recv(64).startswith(b"PONG"):
                recovered = True
                break
        except OSError:
            time.sleep(0.25)
        finally:
            if probe is not None:
                probe.close()
    s.close()
    gpid = None
    try:
        with open(pidfile) as f:
            gpid = int(f.read().strip())
    except (OSError, ValueError):  # lint-silent: expected the pid file is not written yet (gpid stays None, checked below)
        pass
    alive = gpid is not None
    t_end = time.monotonic() + 3.0
    while gpid is not None and time.monotonic() < t_end:
        alive = H.pid_alive(gpid)
        if not alive:
            break
        time.sleep(0.05)
    if gpid is not None and alive:
        try:
            os.kill(gpid, signal.SIGKILL)  # recorded PID only
        except ProcessLookupError:  # lint-silent: expected the recorded grandchild already exited
            pass
    H.check("deaf client of a flooding RUN: the whole process group dies, no surviving grandchild (R12)",
          recovered and gpid is not None and not alive, f"(recovered={recovered} gpid={gpid} alive={alive})")


def test_run_framing(d: H.Daemon) -> None:
    """Review devd-r1 F3: a RUN answer is `RUNID <16 hex>` first and `RUNEND <same id> RC: N` last. Program output
    cannot forge the end line because the id is not known to it."""
    resp, rid = H.run_framed(d, f"{H.TB} echorc 7 hello world")
    last = resp.rstrip(b"\n").split(b"\n")[-1]
    H.check("RUN answer starts with RUNID <16 hex> and ends with RUNEND <same id> RC: 7 (F3)",
          rid is not None and resp.startswith(b"RUNID " + rid.encode() + b"\nhello world\n")
          and last == f"RUNEND {rid} RC: 7".encode(), f"(resp={resp!r})")
    resp2, rid2 = H.run_framed(d, f"{H.TB} echo again")
    H.check("every RUN gets a fresh id", rid is not None and rid2 is not None and rid != rid2, f"(rid={rid} rid2={rid2})")
    resp, rid = H.run_framed(d, f"{H.TB} noeol")
    H.check("output without a trailing newline: devd ends the line, RUNEND is a line of its own (F3)",
          rid is not None and resp.endswith(f"no newline\nRUNEND {rid} RC: 0\n".encode()), f"(resp={resp!r})")
    resp, rid = H.run_framed(d, f"{H.TB} forge")
    H.check("a forged RUNEND line in the program output carries another id; the real one follows (F3)",
          rid is not None and b"RUNEND 0000000000000000 RC: 0\n" in resp
          and resp.endswith(f"RUNEND {rid} RC: 5\n".encode()), f"(resp={resp!r})")
    s = d.conn()
    s.sendall(b"RUN /bin/sh -c id\n")
    r = H.recv_line(s, 5.0)
    s.close()
    H.check("a refused RUN carries no RUNID (nothing ran)", r.startswith(b"ERR: RUN") and b"RUNID" not in r, f"(r={r!r})")
