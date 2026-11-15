"""devd hardening test groups (push): split out of test_devd_hardening.py (T-c24f, R29 size limit); shared state lives in devd_hardening_lib."""
import atexit, json, os, re, shutil, signal, socket, subprocess, sys, tempfile, time

import devd_hardening_lib as H
import test_devd_lib as lib


def test_push_reject_framing(d: H.Daemon) -> None:
    """Review iteration 3: a PUSH rejected after the client streamed its
    payload must never let payload reach the parser as a command, and a
    correctly framed connection must survive a reject."""
    work = os.path.join(H.TMP_DIR, "desync")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    dst = os.path.join(work, "pushed.bin")

    s = d.conn()
    s.sendall(b"PUSH /tmp/../etc/evil 5\nHELLO")  # client-string reject
    r1 = H.recv_line(s, 5.0)
    s.sendall(b"PING\n")
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("rejected PUSH cannot smuggle RUN through its payload",
          b"ERR: path not allowed" in r1 and b"PONG" in r2 and b"RC:" not in r2,
          f"(r1={r1!r} r2={r2!r})")

    s = d.conn()
    s.sendall(b"PUSH /etc/devd-evil 5\nHELLO")  # resolved-path reject
    r1 = H.recv_line(s, 5.0)
    s.sendall(b"PING\n")
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("resolved-path reject answers only after draining its payload",
          b"ERR: path not allowed" in r1 and b"PONG" in r2 and b"RC:" not in r2,
          f"(r1={r1!r} r2={r2!r})")
    H.check("no file created at the rejected resolved path",
          not os.path.exists("/etc/devd-evil"))

    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" 5\nHELLO")
    r1 = H.recv_line(s, 5.0)
    s.sendall(b"PING\n")
    r2 = H.recv_line(s, 5.0)
    s.close()
    with open(dst, "rb") as f:
        content = f.read()
    H.check("valid PUSH keeps the connection framed (OK then PONG)",
          r1 == b"OK\n" and b"PONG" in r2 and content == b"HELLO",
          f"(r1={r1!r} r2={r2!r} content={content!r})")

    # review devd-r1 F1(b): a length-0 PUSH has no attributable payload (a client that misreported its
    # size streams the file anyway): ERR, then the connection is dropped, like the over-cap length.
    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" 0\n")  # header-only reject
    r1 = H.recv_line(s, 5.0)
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("length-0 PUSH: ERR then the connection is dropped (F1)",
          b"ERR: push length invalid" in r1 and (r2 == b"<EOF>" or b"ConnectionReset" in r2),
          f"(r1={r1!r} r2={r2!r})")

    # review devd-r1 F1(c): E9, the bytes of a file that reported size 0 hold a RUN line
    smuggle = os.path.join(H.TMP_DIR, "smuggle-marker")
    if os.path.exists(smuggle):
        os.unlink(smuggle)
    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" 0\n" + f"RUN {H.TB} touch {smuggle}\n".encode())
    r1 = H.recv_line(s, 5.0)
    s.close()
    time.sleep(0.5)
    H.check("size-0 PUSH whose body holds a RUN line does not run it (F1)",
          b"ERR: push length invalid" in r1 and not os.path.exists(smuggle),
          f"(r1={r1!r} marker={os.path.exists(smuggle)})")

    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" 99999999999999999999999\n")
    r1 = H.recv_line(s, 5.0)
    s.close()
    H.check("length overflow past size_t rejected, not truncated",
          b"ERR: push length invalid" in r1, f"(r1={r1!r})")

    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" 5 junk\n")
    r1 = H.recv_line(s, 5.0)
    s.close()
    H.check("PUSH with a trailing token rejected", b"ERR: invalid PUSH format" in r1,
          f"(r1={r1!r})")


def test_push_reject_inflight(d: H.Daemon) -> None:
    """Review gemini-devd-r1 F2, contract H2b: a rejected PUSH whose header
    parsed with a valid length 1..40 MiB owns exactly that many payload bytes.
    They are skipped (never parsed as commands, even if the client sends them
    after the verdict) and the next byte is the next command. A header whose
    payload cannot be attributed (bad format, over-cap length) cuts the
    connection after the ERR."""
    marker = os.path.join(H.TMP_DIR, "pwned-marker")
    for m in (marker, marker + "2"):
        if os.path.exists(m):
            os.unlink(m)

    # (a) header + payload + next command pipelined in ONE write: the command
    # must be answered, not swallowed with the payload.
    s = d.conn()
    s.sendall(b"PUSH /tmp/../etc/evil 5\nHELLOPING\n")
    r1 = H.recv_line(s, 5.0)
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("pipelined PING after a rejected PUSH payload is answered, not swallowed",
          b"ERR: path not allowed" in r1 and b"PONG" in r2, f"(r1={r1!r} r2={r2!r})")

    # (b) payload that is a valid RUN line, sent only AFTER the verdict.
    payload = (f"RUN {H.TB} touch " + marker + "\n").encode()
    s = d.conn()
    s.sendall(b"PUSH /tmp/../etc/evil " + str(len(payload)).encode() + b"\n")
    r1 = H.recv_line(s, 5.0)
    time.sleep(0.3)
    s.sendall(payload + b"PING\n")
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("late payload of a rejected PUSH is never executed, next command answered",
          b"ERR: path not allowed" in r1 and b"PONG" in r2 and not os.path.exists(marker),
          f"(r1={r1!r} r2={r2!r} marker={os.path.exists(marker)})")

    # (c) header accepted by policy but the temp file cannot be created:
    # the payload is still on its way; it must not be parsed as commands.
    work = os.path.join(H.TMP_DIR, "desync")
    os.makedirs(work, exist_ok=True)
    dst = os.path.join(work, "tmpfail.bin")
    if os.path.lexists(dst + ".sweettmp"):
        os.unlink(dst + ".sweettmp")
    os.symlink("/dev/null", dst + ".sweettmp")  # O_NOFOLLOW refuses it
    payload = (f"RUN {H.TB} touch " + marker + "2\n").encode()
    s = d.conn()
    s.sendall(b"PUSH " + dst.encode() + b" " + str(len(payload)).encode() + b"\n" + payload)
    r1 = H.recv_line(s, 5.0)
    time.sleep(0.3)
    try:
        s.sendall(b"PING\n")
        r2 = H.recv_line(s, 5.0)
    except OSError as e:  # connection already dropped by devd: expected
        r2 = repr(e).encode()
    s.close()
    # a PUSH that wrongly followed the symlink renames it over dst: clean both, then assert
    for leftover in (dst + ".sweettmp", dst):
        if os.path.lexists(leftover):
            os.unlink(leftover)
    H.check("open() failure after an accepted header does not execute the payload",
          b"ERR: cannot create temp file" in r1 and b"RC:" not in r2
          and not os.path.exists(marker + "2"),
          f"(r1={r1!r} r2={r2!r} marker2={os.path.exists(marker + '2')})")

    _inflight_cut(d, marker)


def _inflight_cut(d: H.Daemon, marker: str) -> None:
    """Cases (d) and the drain-cap rows of test_push_reject_inflight (split for the R29 function limit)."""
    # (d) bad format: payload not attributable, connection is cut after the ERR.
    s = d.conn()
    s.sendall(b"PUSH nonsense\nRUN " + H.TB.encode() + b" touch " + marker.encode() + b"\n")
    r1 = H.recv_line(s, 5.0)
    r2 = H.recv_line(s, 5.0)
    s.close()
    H.check("unattributable PUSH header: ERR then connection closed, nothing executed",
          b"ERR: invalid PUSH format" in r1 and (r2 == b"<EOF>" or b"ConnectionReset" in r2) and not os.path.exists(marker),
          f"(r1={r1!r} r2={r2!r})")

    for label, header, body in (
        ("traversal path", b"PUSH /tmp/../etc/evil 5\n", b"HELLO"),
        ("resolved path", b"PUSH /etc/devd-evil 5\n", b"HELLO"),
    ):
        s = d.conn()
        s.sendall(header + body)
        r1 = H.recv_line(s, 5.0)
        s.sendall(b"PING\n")
        r2 = H.recv_line(s, 5.0)
        s.close()
        H.check(f"{label} reject: streamed payload is drained, next command framed",
              b"ERR:" in r1 and b"PONG" in r2 and b"RC:" not in r2,
              f"(r1={r1!r} r2={r2!r})")

    body = b"\n".join(b"X" * 63 for _ in range(4200))[:H.MAX_PUSH + 1]
    s = d.conn()
    s.sendall(b"PUSH /tmp/../etc/evil 41943040\n")  # final reject on the client string
    s.sendall(body)
    r1 = H.recv_line(s, 20.0)
    s.settimeout(10.0)
    try:
        rest = s.recv(1)
    except OSError as e:
        rest = repr(e).encode()
    s.close()
    # Closed means no further byte is readable: the close being processed by
    # the client ('' or ECONNRESET) is what a cutoff of the one-deadline
    # drain looks like, and any 'E' would be a late ERR line for the stream.
    H.check("payload beyond the drain cap: verdict first, then no command is run",
          b"ERR: path not allowed" in r1 and b"X" not in rest and b"PONG" not in rest,
          f"(r1={r1!r} rest={rest!r})")


def test_push_rename_failure(d: H.Daemon) -> None:
    """The rename() that publishes a PUSH can fail (here: the target is an existing directory, so
    rename(file, dir) fails with EISDIR). That must be an ERR, never an OK for a file that is not there."""
    work = os.path.join(H.TMP_DIR, "desync")
    os.makedirs(work, exist_ok=True)
    target = os.path.join(work, "isdir")
    os.makedirs(target, exist_ok=True)
    s = d.conn()
    s.sendall(b"PUSH " + target.encode() + b" 5\nHELLO")
    r1 = H.recv_line(s, 5.0)
    s.close()
    time.sleep(0.2)
    # AP-337: the residue must be absent, not cleaned up by the test (review devd-r1 F6b/c)
    H.check("PUSH whose rename() fails answers ERR: rename failed, never OK",
          r1.startswith(b"ERR: rename failed") and os.path.isdir(target), f"(r1={r1!r})")
    H.check("a failed rename leaves no <path>.sweettmp behind (no stray executable file)",
          not os.path.lexists(target + ".sweettmp"), f"(residue mode={oct(os.lstat(target + '.sweettmp').st_mode) if os.path.lexists(target + '.sweettmp') else None})")
    if os.path.lexists(target + ".sweettmp"):
        os.unlink(target + ".sweettmp")


def test_push_policy(d: H.Daemon) -> None:
    """Review devd-r1 F4: PUSH matches the kernel overlay's path rules (0117 sd_path_check): reserved suffixes,
    '.' components, components over 245 bytes, any symlink component; the first replaced file is kept as
    <path>.sweetorig (0117:527-529); the mode does not depend on the umask."""
    work = os.path.join(H.TMP_DIR, "pushpol")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    victim = _policy_names(d, work)
    _policy_tmpname_key(d, work, victim)
    _policy_modes(d, work)


def _policy_names(d: H.Daemon, work: str) -> str:
    """Reserved names, components, symlinks, .sweetorig rows of test_push_policy; returns the victim path."""
    for suffix in (".sweetorig", ".sweettmp"):
        target = os.path.join(work, "lib.dylib" + suffix)
        r = H.push_one(d, target, b"X")
        H.check(f"(h) PUSH to a reserved {suffix} name refused, nothing created",
              b"ERR: path not allowed" in r and not os.path.lexists(target), f"(r={r!r})")
    keep = os.path.join(work, "kept.bin")
    with open(keep, "wb") as f:
        f.write(b"RESTORE-SOURCE")
    r = H.push_one(d, keep + ".sweetorig", b"OVERWRITE")
    with open(keep, "rb") as f:
        kept = f.read()
    H.check("(h) PUSH to <file>.sweetorig cannot overwrite a restore source", b"ERR" in r and kept == b"RESTORE-SOURCE",
          f"(r={r!r})")

    r = H.push_one(d, work + "/./dot.bin", b"X")
    H.check("(h) PUSH with a '.' component refused", b"ERR: path not allowed" in r and not os.path.exists(os.path.join(work, "dot.bin")),
          f"(r={r!r})")
    r = H.push_one(d, os.path.join(work, "x" * 246), b"X")
    H.check("(h) PUSH with a 246-byte component refused", b"ERR: path not allowed" in r, f"(r={r!r})")
    r = H.push_one(d, os.path.join(work, "y" * 245), b"X")
    H.check("(h) PUSH with a 245-byte component still works", r == b"OK\n", f"(r={r!r})")

    real = os.path.join(work, "realdir")
    os.makedirs(real)
    os.symlink(real, os.path.join(work, "dirlink"))
    r = H.push_one(d, os.path.join(work, "dirlink", "inside.bin"), b"X")
    H.check("(h) PUSH through a symlinked directory component refused (even inside the allowed root)",
          b"ERR: path not allowed" in r and not os.path.exists(os.path.join(real, "inside.bin")), f"(r={r!r})")
    victim = os.path.join(work, "victim.bin")
    with open(victim, "wb") as f:
        f.write(b"VICTIM")
    os.symlink(victim, os.path.join(work, "filelink"))
    r = H.push_one(d, os.path.join(work, "filelink"), b"REPLACED")
    with open(victim, "rb") as f:
        vdata = f.read()
    H.check("(h) PUSH onto a symlink as the last component refused, target untouched",
          b"ERR: path not allowed" in r and vdata == b"VICTIM" and os.path.islink(os.path.join(work, "filelink")), f"(r={r!r})")

    # .sweetorig: the first replaced file is kept, a second replacement does not overwrite the backup
    target = os.path.join(work, "replaced.bin")
    with open(target, "wb") as f:
        f.write(b"ORIGINAL")
    r1 = H.push_one(d, target, b"FIRST")
    r2 = H.push_one(d, target, b"SECOND")
    with open(target, "rb") as f:
        cur = f.read()
    orig = target + ".sweetorig"
    ocur = open(orig, "rb").read() if os.path.exists(orig) else None
    H.check("(h) replacing an existing file keeps the first original as <path>.sweetorig, a second push keeps it",
          r1 == b"OK\n" and r2 == b"OK\n" and cur == b"SECOND" and ocur == b"ORIGINAL", f"(r1={r1!r} r2={r2!r} cur={cur!r} orig={ocur!r})")
    fresh = os.path.join(work, "fresh.bin")
    H.push_one(d, fresh, b"NEW")
    H.check("(h) a new file leaves no .sweetorig", not os.path.lexists(fresh + ".sweetorig"))
    odir = os.path.join(work, "odir.bin")
    with open(odir, "wb") as f:
        f.write(b"OLD")
    os.makedirs(odir + ".sweetorig")
    r = H.push_one(d, odir, b"NEW")
    with open(odir, "rb") as f:
        cur = f.read()
    H.check("(h) a .sweetorig that is not a regular file blocks the replacement, original untouched",
          b"ERR" in r and cur == b"OLD" and not os.path.lexists(odir + ".sweettmp"), f"(r={r!r} cur={cur!r})")

    return victim


def _policy_tmpname_key(d: H.Daemon, work: str, victim: str) -> None:
    """F6/F12 rows of test_push_policy (split for the R29 function limit)."""
    # review devd-r1 F6(a): a hard link at the temp name must not let the push write through to its target
    hv = os.path.join(work, "hardvictim.bin")
    with open(hv, "wb") as f:
        f.write(b"HARDLINK-VICTIM")
    ht = os.path.join(work, "hl.bin")
    os.link(hv, ht + ".sweettmp")
    r = H.push_one(d, ht, b"XYZ")
    with open(hv, "rb") as f:
        hvdata = f.read()
    htdata = H.slurp(ht)
    H.check("(h) a hard link planted at <path>.sweettmp: the push does not write through it (F6a)",
          hvdata == b"HARDLINK-VICTIM" and r == b"OK\n" and htdata == b"XYZ" and not os.path.lexists(ht + ".sweettmp"),
          f"(r={r!r} victim={hvdata!r} target={htdata!r})")
    st_target = os.path.join(work, "stale.bin")
    with open(st_target + ".sweettmp", "wb") as f:
        f.write(b"STALE-FROM-A-CRASHED-PUSH")
    r = H.push_one(d, st_target, b"FRESH")
    stdata = H.slurp(st_target)
    H.check("(h) a stale regular <path>.sweettmp from a crashed push is replaced, not appended to (F6)",
          r == b"OK\n" and stdata == b"FRESH", f"(r={r!r} data={stdata!r})")

    # review devd-r1 F12: the key file the daemon loaded can be neither pushed over nor pulled (the key never
    # leaves the phone through PULL, and a PUSH would lock the next start out). A private daemon + key, so a
    # failing run cannot damage the key the other rows use.
    kdir = os.path.join(work, "keyd")
    os.makedirs(kdir, mode=0o700)
    kpath, key2 = lib.make_key_file(kdir)
    dk = H.Daemon(H.STOCK_BIN, key_file=kpath)
    try:
        def kconn():
            return lib.auth_conn(dk.port, key2, 5.0)
        with open(kpath, "rb") as f:
            key_before = f.read()
        s = kconn()
        s.sendall(b"PUSH " + kpath.encode() + b" 64\n" + b"0" * 64)
        r = H.recv_line(s, 5.0)
        s.close()
        with open(kpath, "rb") as f:
            key_after = f.read()
        H.check("(h) PUSH over the loaded key file refused, key unchanged (F12)",
              b"ERR: path not allowed" in r and key_before == key_after, f"(r={r!r})")
        s = kconn()
        s.sendall(b"PULL " + kpath.encode() + b"\n")
        r = H.recv_line(s, 5.0)
        s.close()
        H.check("(h) PULL of the loaded key file refused (F12)", b"ERR: path not allowed" in r, f"(r={r!r})")
        keylink = os.path.join(kdir, "keylink")
        os.symlink(kpath, keylink)
        s = kconn()
        s.sendall(b"PULL " + keylink.encode() + b"\n")
        r = H.recv_line(s, 5.0)
        s.close()
        H.check("(h) PULL of the key file through a symlink refused (F12)", b"ERR: path not allowed" in r, f"(r={r!r})")
        keyhard = os.path.join(kdir, "keyhard")
        os.link(kpath, keyhard)
        s = kconn()
        s.sendall(b"PULL " + keyhard.encode() + b"\n")
        r = H.recv_line(s, 5.0)
        s.close()
        H.check("(h) PULL of a hard link to the key file refused (F12)", b"ERR: path not allowed" in r, f"(r={r!r})")
    finally:
        dk.stop()
    s = d.conn()
    s.sendall(b"PULL " + victim.encode() + b" /etc/shadow\n")
    r = H.recv_line(s, 5.0)
    s.close()
    H.check("(h) PULL with a trailing token refused (review devd-r1 nit)", b"ERR: invalid PULL format" in r, f"(r={r!r})")


def _policy_modes(d: H.Daemon, work: str) -> None:
    """Mode rows (0755 / N1) of test_push_policy."""
    # mode: test mode pushes under /tmp/ are executable (RUN roots), exactly 0755 whatever the umask
    modefile = os.path.join(work, "mode.bin")
    dm = H.Daemon(H.STOCK_BIN, umask=0o077)  # a daemon whose umask would strip the exec bit from a plain open(.., 0755)
    try:
        H.push_one(dm, modefile, b"M")
    finally:
        dm.stop()
    H.check("(h) pushed file has mode 0755 exactly (not umask dependent)",
          os.path.exists(modefile) and (os.stat(modefile).st_mode & 0o7777) == 0o755,
          f"(mode={oct(os.stat(modefile).st_mode & 0o7777) if os.path.exists(modefile) else None})")

    # review devd-r2 N1 / AP-341: a replace keeps the replaced file's permission bits; the class default (0644 outside
    # the RUN roots) applies to a new file only. A <...>/devd-lib/ directory under /tmp/ is the test-mode stand-in for a
    # non-RUN root such as /usr/lib/ (push_mode); without the fix a 0755 fake dyld became 0644 = no exec = brick.
    libdir = os.path.join(work, "devd-lib")
    os.makedirs(libdir)
    fresh_lib = os.path.join(libdir, "new.dylib")
    r = H.push_one(d, fresh_lib, b"NEWLIB")
    fm = os.stat(fresh_lib).st_mode & 0o7777 if os.path.exists(fresh_lib) else None
    H.check("(h) N1: a new file in a non-RUN root gets the class default 0644", r == b"OK\n" and fm == 0o644,
          f"(r={r!r} mode={oct(fm) if fm is not None else None})")
    dyld = os.path.join(libdir, "fake-dyld")
    with open(dyld, "wb") as f:
        f.write(b"#!/bin/sh\nexit 0\n")
    os.chmod(dyld, 0o755)
    r = H.push_one(d, dyld, b"#!/bin/sh\nexit 7\n")
    dm_ = os.stat(dyld).st_mode & 0o7777
    H.check("(h) N1: PUSH over a 0755 file in a non-RUN root keeps 0755 (exec still works)",
          r == b"OK\n" and dm_ == 0o755 and os.access(dyld, os.X_OK), f"(r={r!r} mode={oct(dm_)})")
    suid = os.path.join(libdir, "suid.bin")
    with open(suid, "wb") as f:
        f.write(b"S")
    os.chmod(suid, 0o4755)
    r = H.push_one(d, suid, b"T")
    sm = os.stat(suid).st_mode & 0o7777
    time.sleep(0.2)
    with open(d.log.name) as f:
        dlog = f.read()
    H.check("(h) N1: PUSH over a 04755 file drops setuid (0755) with a SWEET GAP line (review devd-r3 F2)",
          r == b"OK\n" and sm == 0o755 and f"SWEET GAP: devd PUSH {suid}: replaced file had mode 4755" in dlog,
          f"(r={r!r} mode={oct(sm)})")
    priv = os.path.join(work, "private.bin")
    with open(priv, "wb") as f:
        f.write(b"P")
    os.chmod(priv, 0o600)
    r = H.push_one(d, priv, b"Q")
    pm = os.stat(priv).st_mode & 0o7777
    H.check("(h) N1: PUSH over a 0600 file in a RUN root keeps 0600 (not widened to 0755)",
          r == b"OK\n" and pm == 0o600, f"(r={r!r} mode={oct(pm)})")


def test_symlink_policy(d: H.Daemon) -> None:
    work = os.path.join(H.TMP_DIR, "sec")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    # An unlisted file outside /tmp/ (the test-mode root) and outside the k_allow roots: a private /var/tmp dir (AP-210: no fixed $HOME path)
    unlisted_dir = tempfile.mkdtemp(prefix="devd-sec-", dir="/var/tmp")
    atexit.register(shutil.rmtree, unlisted_dir, True)
    secret = os.path.join(unlisted_dir, "secret")
    with open(secret, "w") as f:
        f.write("TOPSECRET\n")

    esc = os.path.join(work, "esc")
    os.symlink(secret, esc)

    s = d.conn()
    s.sendall(f"PULL {esc}\n".encode())
    resp = s.recv(1024)
    H.check("PULL through symlink to unlisted file rejected",
          b"ERR: path not allowed" in resp and b"TOPSECRET" not in resp)
    s.close()

    # /etc is outside the allowlist; a dir-symlink must not smuggle writes.
    os.symlink("/etc", os.path.join(work, "etclink"))
    s = d.conn()
    s.sendall(f"PUSH {work}/etclink/devd-evil 5\n".encode())
    resp = s.recv(1024)
    H.check("PUSH through symlinked dir rejected", b"ERR: path not allowed" in resp)
    H.check("no file created at symlink target",
          not os.path.exists("/etc/devd-evil"))
    s.close()

    s = d.conn()
    inside = os.path.join(work, "inside")
    os.symlink(os.path.join(work, "ok.txt"), inside)
    good = os.path.join(work, "ok.txt")
    with open(good, "wb") as f:
        f.write(b"fine\n")
    s.sendall(f"PULL {inside}\n".encode())
    resp = H.recv_until(s, b"fine\n", 5.0)
    H.check("PULL via in-root symlink still allowed", b"OK 5" in resp and b"fine\n" in resp)

    target = os.path.join(work, "push.txt")
    s.sendall(f"PUSH {target} 6\n".encode() + b"pushed")
    resp = s.recv(1024)
    ok = b"OK" in resp and os.path.exists(target)
    with open(target, "rb") as f:
        ok = ok and f.read() == b"pushed"
    H.check("plain PUSH to new file still works", ok)
    s.close()
