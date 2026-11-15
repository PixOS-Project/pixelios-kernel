"""devd hardening test groups (misc): split out of test_devd_hardening.py (T-c24f, R29 size limit); shared state lives in devd_hardening_lib."""
import atexit, json, os, re, shutil, signal, socket, subprocess, sys, tempfile, time

import devd_hardening_lib as H
import test_devd_lib as lib


def test_cloexec_restart() -> None:
    """Review gemini-devd-r1 F3: a RUN child that outlives its connection must
    not inherit the listening or client socket, so a devd restart can bind
    the same port while the child still runs."""
    d = H.Daemon(H.STOCK_BIN)
    child = None
    d2 = None
    try:
        s = d.conn()
        s.sendall(f"RUN {H.TB} bgquiet\n".encode())
        resp = H.recv_until(s, b"RC: ", 5.0)
        s.close()
        try:
            child = int(resp.split(b"\n")[1])  # line 1 is RUNID <id>, line 2 the grandchild's pid
        except (ValueError, IndexError):
            print(f"DEVD GAP: no grandchild pid in the RUN reply {resp!r}", file=sys.stderr)
            child = None
        socks = []
        if child is not None:
            for fd in os.listdir(f"/proc/{child}/fd"):
                try:
                    if os.readlink(f"/proc/{child}/fd/{fd}").startswith("socket:"):
                        socks.append(fd)
                except OSError:  # lint-silent: expected the fd closed between listdir and readlink
                    pass
        H.check("backgrounded RUN child holds no inherited socket",
              child is not None and not socks, f"(child={child} socket fds={socks} resp={resp!r})")
        d.proc.kill()
        d.proc.wait(timeout=5.0)
        d.log.close()
        d2 = subprocess.Popen([H.STOCK_BIN, "--port", str(d.port), "--test-mode", "--key-file", H.KEY_FILE],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(0.4)
        up = d2.poll() is None
        out = b"" if up else d2.stdout.read()
        H.check("devd restarts on the same port while the RUN child still lives",
              up, f"(exit={d2.poll()} out={out[-120:]!r})")
    finally:
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)  # recorded PID only
            except ProcessLookupError:  # lint-silent: expected the recorded child already exited
                pass
        if d2 is not None and d2.poll() is None:
            d2.kill()
            d2.wait(timeout=5.0)
        if d2 is not None and d2.stdout:
            d2.stdout.close()
        if d.proc.poll() is None:
            d.proc.kill()
            d.proc.wait(timeout=5.0)


def test_exit_and_gap_logging() -> None:
    """Review gemini-devd-r1 F5 (devd side): accept() failure ends the loop
    with a non-zero exit and an errno= line; every rejected request logs a
    `SWEET GAP: devd rejected <cmd> reason=` line (rule 8)."""
    import resource
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    logp = os.path.join(H.TMP_DIR, "devd-nofile.log")
    with open(logp, "w") as lg:
        p = subprocess.Popen(
            [H.STOCK_BIN, "--port", str(port), "--test-mode", "--key-file", H.KEY_FILE], stdout=lg, stderr=subprocess.STDOUT,
            # fds 0-2 + the listening socket = 4: the first accept() hits EMFILE
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (4, 4)))
        time.sleep(0.3)
        t_acc = time.monotonic()
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        except OSError as e:
            print(f"DEVD GAP: lockout probe connect failed: {e!r}", file=sys.stderr)
            c = None
        try:
            rc = p.wait(timeout=20.0)   # review devd-r1 F14: 10 failures in a row, 1 s apart
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            rc = "timeout"
        acc_dt = time.monotonic() - t_acc
        if c is not None:
            c.close()
    with open(logp) as f:
        log = f.read()
    H.check("accept() failure: devd exits non-zero and logs errno=",
          isinstance(rc, int) and rc != 0 and "accept failed (errno=" in log,
          f"(rc={rc} log tail={log[-120:]!r})")
    gaps = [ln for ln in log.splitlines() if ln.startswith("SWEET GAP: devd accept failed")]
    H.check("accept() EMFILE: retried once a second with a SWEET GAP line each, exit 1 after 10 in a row (F14)",
          rc == 1 and len(gaps) == 10 and 7.0 <= acc_dt <= 16.0, f"(rc={rc} gaps={len(gaps)} dt={acc_dt:.1f}s)")

    d = H.Daemon(H.STOCK_BIN)
    try:
        s = d.conn()
        wanted = [
            (b"FROB\n", b"unknown command"),
            (b"PULL " + os.path.join(H.TMP_DIR, "no-such-file").encode() + b"\n", b"file not found"),
            (b"PULL /etc/passwd\n", b"path not allowed"),
            (b"SIGNAL 1 9\n", b"pid rejected"),
            (b"SIGNAL 99999 3\n", b"signal not allowed"),
            (b"SIGNAL x\n", b"invalid SIGNAL format"),
            (b"PULL\n", b"unknown command"),
            (b"PUSH /tmp/../etc/x 1\nA", b"path not allowed"),
            (b"PUSH /tmp/x 0\n", b"push length invalid"),
        ]
        errs = 0
        for req, _why in wanted:
            s.sendall(req)
            if H.recv_line(s, 5.0).startswith(b"ERR:"):
                errs += 1
        s.close()
        time.sleep(0.2)
        with open(d.log.name) as f:
            gaps = [ln for ln in f if "SWEET GAP: devd rejected " in ln]
        ok = errs == len(wanted) and len(gaps) == len(wanted)
        for (req, why), ln in zip(wanted, gaps):
            verb = req.split()[0].decode() if req.split()[0] in (b"PULL", b"PUSH", b"SIGNAL") and why != b"unknown command" else None
            if why.decode() not in ln or (verb and f"rejected {verb} " not in ln):
                ok = False
        H.check("every rejected request logs a SWEET GAP: devd rejected <cmd> reason= line",
              ok, f"(errs={errs} gaps={len(gaps)}/{len(wanted)} {gaps!r})")
    finally:
        d.stop()


def test_auth(d: H.Daemon) -> None:
    """Review F1, research 111: every connection is challenged; nothing is parsed before AUTH."""
    # (a) PING without AUTH is never answered
    s = d.raw()
    chal = lib.read_challenge(s)
    s.sendall(b"PING\n")
    got = H.read_all(s)
    s.close()
    H.check("(a) PING without AUTH: ERR: auth, no PONG, connection closed",
          b"ERR: auth" in got and b"PONG" not in got, f"(chal={chal} got={got!r})")

    # (a2) a command line smuggled in as the first line, a RUN too
    marker = os.path.join(H.TMP_DIR, "auth-marker")
    s = d.raw()
    lib.read_challenge(s)
    s.sendall(f"RUN {H.TB} touch {marker}\n".encode())
    got = H.read_all(s)
    s.close()
    time.sleep(0.2)
    H.check("(a) RUN as the first line is not executed", b"ERR: auth" in got and not os.path.exists(marker),
          f"(got={got!r})")

    # (a3) silence: ERR: auth after the 5 s auth window, connection closed
    t0 = time.monotonic()
    s = d.raw(timeout=12.0)
    lib.read_challenge(s)
    got = H.read_all(s, 12.0)
    dt = time.monotonic() - t0
    s.close()
    H.check("(a) silent client: ERR: auth after ~5 s (4.5..6.5 s window), connection closed",
          b"ERR: auth" in got and 4.5 <= dt <= 6.5, f"(dt={dt:.1f}s got={got!r})")

    # (b) wrong HMAC
    s = d.raw()
    chal = lib.read_challenge(s)
    s.sendall(f"AUTH {lib.hmac_hex(b'x' * 32, chal)}\n".encode())
    s.sendall(b"PING\n")
    got = H.read_all(s)
    s.close()
    H.check("(b) wrong HMAC rejected: ERR: auth, no PONG", b"ERR: auth" in got and b"PONG" not in got, f"(got={got!r})")

    good = None
    for label, mk in (
            ("uppercase hex", lambda c: lib.hmac_hex(H.KEY, c).upper()),
            ("truncated", lambda c: lib.hmac_hex(H.KEY, c)[:-2]),
            ("trailing junk", lambda c: lib.hmac_hex(H.KEY, c) + "00"),
            ("HMAC of another challenge", lambda c: lib.hmac_hex(H.KEY, "00" * 16)),
            # review devd-r1 R05: the correct HMAC with only its LAST hex char flipped (a compare of a prefix accepts it)
            ("correct HMAC, last hex char flipped", lambda c: lib.hmac_hex(H.KEY, c)[:-1] + ("0" if lib.hmac_hex(H.KEY, c)[-1] != "0" else "1")),
            ("correct HMAC, 33rd hex char flipped", lambda c: lib.hmac_hex(H.KEY, c)[:32] + ("0" if lib.hmac_hex(H.KEY, c)[32] != "0" else "1") + lib.hmac_hex(H.KEY, c)[33:])):
        s = d.raw()
        chal = lib.read_challenge(s)
        s.sendall(f"AUTH {mk(chal)}\n".encode())
        s.sendall(b"PING\n")
        got = H.read_all(s)
        s.close()
        H.check(f"(b) malformed/wrong AUTH ({label}) rejected", b"ERR: auth" in got and b"PONG" not in got,
              f"(got={got!r})")

    # (c) correct HMAC accepted; a replay of an earlier connection's AUTH rejected
    s = d.raw()
    chal1 = lib.read_challenge(s)
    auth1 = f"AUTH {lib.hmac_hex(H.KEY, chal1)}\n".encode()
    s.sendall(auth1)
    s.sendall(b"PING\n")
    r = H.recv_line(s, 5.0)
    s.close()
    H.check("(c) correct HMAC accepted: PONG", r.startswith(b"PONG"), f"(r={r!r})")

    s = d.raw()
    chal2 = lib.read_challenge(s)
    s.sendall(auth1)  # replay of the previous connection's answer
    s.sendall(b"PING\n")
    got = H.read_all(s)
    s.close()
    H.check("(c) replayed AUTH from an earlier connection rejected (challenges differ)",
          chal1 != chal2 and b"ERR: auth" in got and b"PONG" not in got, f"(chal1={chal1} chal2={chal2} got={got!r})")

    # AUTH is only valid as the first line
    s = d.conn()
    s.sendall(f"AUTH {lib.hmac_hex(H.KEY, chal1)}\n".encode())
    r = H.recv_line(s, 5.0)
    s.close()
    H.check("(c) a second AUTH line is an unknown command, not a re-auth", b"ERR: unknown command" in r, f"(r={r!r})")

    with open(d.log.name) as f:
        gaps = [ln for ln in f if "SWEET GAP: devd rejected AUTH reason=" in ln]
    H.check("every rejected AUTH logs a SWEET GAP line (raw count >= 8)", len(gaps) >= 8, f"(count={len(gaps)})")


def test_key_gate() -> None:
    """(d) the gate is the root-owned key file, not a boot-arg (research 111)."""
    kd = os.path.join(H.TMP_DIR, "keys")
    os.makedirs(kd, mode=0o700, exist_ok=True)
    base = ["--port", "0", "--test-mode", "--key-file"]

    H.spawn_expect_exit(base + [os.path.join(kd, "missing.key")], "(d) missing key file: exit 1 + SWEET GAP", "cannot open")
    for mode in (0o644, 0o640, 0o400, 0o666):
        path, _ = lib.make_key_file(os.path.join(kd), mode=mode)
        H.spawn_expect_exit(base + [path], f"(d) key file mode {mode:04o}: exit 1 + SWEET GAP", "mode is not 0600")
    path, _ = lib.make_key_file(kd, content="00" * 31 + "\n")
    H.spawn_expect_exit(base + [path], "(d) key file with 62 hex chars: exit 1", "64 hex")
    path, _ = lib.make_key_file(kd, content=("AB" * 32) + "\n")
    H.spawn_expect_exit(base + [path], "(d) key file with uppercase hex: exit 1", "lowercase hex")
    path, _ = lib.make_key_file(kd, content="zz" * 32 + "\n")
    H.spawn_expect_exit(base + [path], "(d) key file with non-hex text: exit 1", "lowercase hex")
    path, _ = lib.make_key_file(kd, content="")
    H.spawn_expect_exit(base + [path], "(d) empty key file: exit 1", "size is not 64")
    link = os.path.join(kd, "link.key")
    good, _ = lib.make_key_file(os.path.join(kd), mode=0o600)
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(good, link)
    H.spawn_expect_exit(base + [link], "(d) key file that is a symlink: exit 1 (O_NOFOLLOW)", "cannot open")
    H.spawn_expect_exit(base + [kd], "(d) key path that is a directory: exit 1", "")
    H.spawn_expect_exit(["--port", "0"], "(d) phone mode without /private/var/dev/devd.key: exit 1", "devd.key")
    H.spawn_expect_exit(["--port", "0", "--key-file", good], "(d) --key-file without --test-mode: exit 1", "--test-mode")

    # newline-less key starts and serves
    kd2 = os.path.join(H.TMP_DIR, "k2.d")
    os.makedirs(kd2, mode=0o700, exist_ok=True)
    path, key = lib.make_key_file(kd2)
    with open(path, "w") as f:
        f.write(key.hex())  # no trailing newline
    d = H.Daemon(H.STOCK_BIN, key_file=path)
    try:
        s = lib.auth_conn(d.port, key)
        s.sendall(b"PING\n")
        r = H.recv_line(s, 5.0)
        s.close()
        H.check("(d) valid key file without a trailing newline: daemon serves", r.startswith(b"PONG"), f"(r={r!r})")
    finally:
        d.stop()

    # owner: injected stat in the binary's selftest; the live check needs root to create a foreign-owned file
    res = subprocess.run([H.STOCK_BIN, "--selftest"], capture_output=True, text=True)
    last = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    H.check("(d) devd --selftest: SHA-256/HMAC vectors, injected-stat owner/mode checks, path policy",
          res.returncode == 0 and last.startswith("RESULT: PASS selftest"), f"(rc={res.returncode} out={res.stdout[-300:]!r})")
    foreign = None
    if os.geteuid() == 0:
        foreign, _ = lib.make_key_file(kd)
        try:
            os.chown(foreign, 12345, 12345)
        except OSError as e:  # a user namespace maps no uid 12345: EINVAL (review devd-r1 F7)
            H.skip("(d) live wrong-owner key file", f"chown to 12345 failed ({e}); covered by the injected-stat selftest case 'key stat: wrong owner rejected'")
            foreign = None
    if foreign is not None:
        H.spawn_expect_exit(base + [foreign], "(d) key file owned by another uid: exit 1", "owner")
    elif os.geteuid() != 0:
        H.skip("(d) live wrong-owner key file", "not root, cannot create a foreign-owned file; covered by the injected-stat selftest case 'key stat: wrong owner rejected'")


def test_peer_check(d: H.Daemon) -> None:
    """(f) a peer that is not 127.0.0.1 (test mode) is dropped before any challenge."""
    try:
        s = d.raw(src="127.0.0.2")
    except OSError as e:
        H.skip("(f) peer check", f"cannot bind source 127.0.0.2 on this host ({e})")
        return
    got = H.read_all(s, 3.0)
    s.close()
    time.sleep(0.2)
    with open(d.log.name) as f:
        log = f.read()
    H.check("(f) connection from 127.0.0.2 is dropped: no CHALLENGE, nothing sent",
          got == b"" and "SWEET GAP: devd dropped peer 127.0.0.2" in log, f"(got={got!r})")
    s = d.conn()
    s.sendall(b"PING\n")
    r = H.recv_line(s, 5.0)
    s.close()
    H.check("(f) the daemon still serves 127.0.0.1 after dropping a peer", r.startswith(b"PONG"), f"(r={r!r})")


def test_phone_mode() -> None:
    """Review devd-r1 F2 (D1/D3): the phone code path, built against loopback and run WITHOUT --test-mode:
    bind only the phone address, drop every peer but the phone peer, gate on the interface and the key owner,
    and no /tmp in the allowlists. Needs 127.0.0.2 on lo (add it in the test namespace: ip addr add 127.0.0.2/8 dev lo)."""
    H.spawn_expect_exit(["--port", "0"], "(p) phone build, interface without the address: exit 1 + SWEET GAP gate line",
                      "is not an address of interface nonexist0", binary=H.PHONE_BADIF_BIN)
    H.spawn_expect_exit(["--port", "0"], "(p) phone build, key file owned by the caller but the phone key uid is another: exit 1",
                      "owner is not the expected uid", binary=H.PHONE_BADUID_BIN)
    if not H.lo_has_127_0_0_2():
        # review devd-r3 F3: the phone-mode rows are never skipped (AP-341); a runner without 127.0.0.2 FAILS
        H.check("(p) phone-mode serving rows ran: 127.0.0.2 is an address of lo", False,
              "(127.0.0.2 is not an address of lo although the netns re-exec ran or was bypassed by DEVD_TEST_NETNS=1)")
        return
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    logp = os.path.join(H.TMP_DIR, "devd-phone.log")
    with open(logp, "w") as lg:
        p = subprocess.Popen([H.PHONE_BIN, "--port", str(port)], stdout=lg, stderr=subprocess.STDOUT, cwd=H.TMP_DIR)
        try:
            deadline = time.monotonic() + 5.0
            up = False
            while time.monotonic() < deadline and p.poll() is None:
                try:
                    H.conn_via("127.0.0.2", port, "127.0.0.1", 1.0).close()
                    up = True
                    break
                except OSError:
                    time.sleep(0.05)
            H.check("(p) phone build starts without --test-mode and listens on the phone address", up and p.poll() is None,
                  f"(rc={p.poll()})")
            try:
                H.conn_via("127.0.0.1", port, "127.0.0.1", 2.0).close()
                refused = False
            except ConnectionRefusedError:
                refused = True
            except OSError as e:
                print(f"DEVD GAP: 127.0.0.1 connect failed with {e!r}, not a refusal", file=sys.stderr)
                refused = False
            H.check("(p) a connection to 127.0.0.1 is refused: the bind is the phone address only", refused)

            s = H.conn_via("127.0.0.2", port, "127.0.0.3")
            got = H.read_all(s, 3.0)
            s.close()
            time.sleep(0.2)
            with open(logp) as f:
                log = f.read()
            H.check("(p) a peer that is not the phone peer (127.0.0.3) is dropped: no CHALLENGE, SWEET GAP line",
                  got == b"" and "SWEET GAP: devd dropped peer 127.0.0.3" in log, f"(got={got!r})")

            s = H.conn_via("127.0.0.2", port, "127.0.0.1")
            chal = lib.read_challenge(s)
            s.sendall(f"AUTH {lib.hmac_hex(H.KEY, chal)}\n".encode())
            s.sendall(b"PING\n")
            r = H.recv_line(s, 5.0)
            H.check("(p) the phone peer authenticates and is served", r.startswith(b"PONG"), f"(r={r!r})")
            # private per-run path (a mutant that lets phone mode write /tmp/ must not leave residue for the next run)
            px = os.path.join(H.TMP_DIR, "devd-phone-x")
            if os.path.lexists(px):
                os.unlink(px)
            s.sendall(f"PUSH {px} 1\nX".encode())
            r1 = H.recv_line(s, 5.0)
            s.sendall(f"RUN {H.TB} id\n".encode())
            r2 = H.recv_line(s, 5.0)
            s.sendall(b"PULL " + H.KEY_FILE.encode() + b"\n")
            r3 = H.recv_line(s, 5.0)
            s.close()
            H.check("(p) phone mode: /tmp PUSH, /tmp RUN and /tmp PULL are refused",
                  b"ERR: path not allowed" in r1 and b"ERR: RUN program not allowed" in r2 and b"ERR: path not allowed" in r3
                  and not os.path.lexists(px), f"(r1={r1!r} r2={r2!r} r3={r3!r} written={os.path.lexists(px)})")
            with open(logp) as f:
                log = f.read()
            H.check("(p) the listen line names the phone address and (phone)",
                  f"listening on 127.0.0.2:{port} (phone)" in log, f"(log={log[:200]!r})")
        finally:
            p.terminate()
            p.wait(timeout=5.0)


def test_allowlist_matches_patch() -> None:
    """(g) PUSH/PULL allowlist == the kernel's sd_allow[] (xnu/patches/0117-sweet-dev-overlay.patch)."""
    try:
        with open(H.PATCH_0117) as f:
            patch = f.read()
    except OSError as e:
        H.check("(g) allowlist vs patch 0117: patch readable", False, f"({H.PATCH_0117}: {e})")
        return
    with open(H.DEVD_SRC) as f:
        src = f.read()
    kernel = H.parse_allow_table(patch, "sd_allow[] = {")
    for pname in ("0280-sweet-devoverlay-whiteout.patch",    # appends to sd_allow[] (BT paths)
                  "0317-sweet-devoverlay-mgcache.patch"):    # appends the MG cache plist (ALL-ON T-36)
        pp = os.path.join(os.path.dirname(H.PATCH_0117), pname)
        if not os.path.exists(pp):
            continue
        added = []
        for line in open(pp).read().split("\n"):
            if "sd_wh_allow[] = {" in line:   # the whiteout table starts here: not part of sd_allow[]
                break
            if line.startswith("+"):
                added += re.findall(r'\{\s*"([^"]+)"\s*,\s*(K_[A-Z]+)\s*\}', line)
        kernel += added
    devd = H.parse_allow_table(src, "k_allow[] = {")
    H.check("(g) allowlist parsed from both sides (raw count >= 5)", len(kernel) >= 5 and len(devd) >= 5,
          f"(kernel={len(kernel)} devd={len(devd)})")
    H.check("(g) devd.c k_allow[] equals the kernel's sd_allow[] (same entries, order, kinds)", kernel == devd,
          f"(kernel={kernel} devd={devd})")
