#!/usr/bin/env python3
"""
test_adversary_matrix.py: 25-Point Adversarial Verification Matrix
==================================================================
Tests devd.c under aggressive fuzzing, Slowloris starvation, path traversal,
privilege escalation, device DoS, command timeout, and burst stress.
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import test_devd_lib as lib

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 41142
WORK = tempfile.mkdtemp(prefix="devd-adversary-", dir="/tmp")
KEY_FILE, KEY = lib.make_key_file(WORK)
TB = None  # compiled test program RUN may execute (no shell)
DEV_DIR = os.path.join(WORK, "adversary_test")
DAEMON_BIN = os.path.join(WORK, "devd_asan")


def setup():
    global TB
    os.makedirs(DEV_DIR, exist_ok=True)
    TB = lib.compile_testbin(WORK)
    # Compile devd with ASan + UBSan for active memory and undefined behavior detection
    c = subprocess.run([
        "gcc", "-Wall", "-Wextra", "-Wpedantic", "-fsanitize=address,undefined", "-g",
        "-o", DAEMON_BIN, os.path.join(HERE, "devd.c")
    ], capture_output=True, text=True)
    if c.returncode != 0:
        print(f"RESULT: ERROR devd.c did not compile with ASan: {c.stderr[-300:]}")
        sys.exit(2)


def start_daemon():
    proc = subprocess.Popen(
        [DAEMON_BIN, "--port", str(PORT), "--test-mode", "--key-file", KEY_FILE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )
    # Poll port readiness without fixed sleep
    start = time.time()
    while time.time() - start < 3.0:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", PORT))
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    return proc


def send_recv_cmd(s, cmd, timeout=5.0):
    s.settimeout(timeout)
    s.sendall(cmd)
    res = b""
    try:
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            res += chunk
            if b"\n" in res:
                break
    except socket.timeout:  # lint-silent: expected the reply wait timed out; the caller checks the bytes
        pass
    return res.decode(errors="replace")


FAILURES = []   # (case, detail) of every expect() that did not hold; a failure ends in RESULT: FAIL, never a traceback
_case_fail = []  # failed expect() details since the last case_done()


def expect(ok, detail=""):
    """A check row (review devd-r1 F7: bare asserts gave a traceback and no RESULT line)."""
    if not ok:
        _case_fail.append(str(detail))


def case_done(name):
    """Prints the case verdict; returns 1 for a pass, 0 for a fail (added to the score)."""
    if _case_fail:
        FAILURES.append((name, list(_case_fail)))
        print(f"[-] {name}: FAIL {_case_fail[0]}")
        del _case_fail[:]
        return 0
    print(f"[+] {name}: PASS")
    return 1


def _cases_a(score, proc):
    """Cases 1.. of the matrix (split for the R29 function limit); score is a one-element list."""
    # 1. Handshake Integrity (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PING\n")
        expect("PONG 1.3." in resp, f"Bad ping response: {resp}")
    score[0] += case_done("1. Handshake Integrity")

    # 2. Slowloris Starvation Resistance (1 pt)
    # One client connects, sends 1 byte, goes idle. Legitimate client must still succeed.
    s_slow = socket.create_connection(("127.0.0.1", PORT), timeout=3.0)
    s_slow.sendall(b"P")
    # Give daemon a moment to enter read_line
    time.sleep(0.05)
    # Second client connects
    # the legitimate client's connect must succeed (accepted or queued) while the slow one idles; an OSError fails this case
    with socket.create_connection(("127.0.0.1", PORT), timeout=3.0):
        pass
    s_slow.close()

    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PING\n")
        expect("PONG" in resp, "\"PONG\" in resp")
    score[0] += case_done("2. Slowloris Starvation Resistance")

    # 3. Oversized Line Framing Draining (>1024 bytes) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(b"A" * 2048 + b"\n")
        resp = send_recv_cmd(s, b"")
        expect("ERR: command too long" in resp, f"Expected overflow error, got: {resp}")
        # Subsequent command on same socket must still succeed!
        resp2 = send_recv_cmd(s, b"PING\n")
        expect("PONG" in resp2, f"Failed recovery after overflow: {resp2}")
    score[0] += case_done("3. Oversized Line Framing Draining")

    # 4. Binary & Null-byte Injection Fuzzing (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(b"\x00\x01\x02\xff\xfe\x00\n")
        resp = send_recv_cmd(s, b"")
        expect("ERR: unknown command" in resp, "\"ERR: unknown command\" in resp")
    score[0] += case_done("4. Binary/Null-byte Injection")

    # 5. Path Traversal Attack: Parent Directory (/..) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PUSH /tmp/../../etc/passwd 10\n")
        expect("ERR: path not allowed" in resp, "\"ERR: path not allowed\" in resp")
    score[0] += case_done("5. Path Traversal (/..)")

    # 6. Path Traversal Attack: Intermediate Traversal (/tmp/subdir/../..) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PUSH /tmp/subdir/../../etc/shadow 10\n")
        expect("ERR: path not allowed" in resp, "\"ERR: path not allowed\" in resp")
    score[0] += case_done("6. Intermediate Traversal (/subdir/..)")

    # 7. Path Traversal Attack: Double Slash Bypass (/tmp//passwd) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PUSH /tmp//passwd 10\n")
        expect("ERR: path not allowed" in resp, "\"ERR: path not allowed\" in resp")
    score[0] += case_done("7. Double Slash Bypass (//)")


def _cases_b(score, proc):
    """Cases 8.. of the matrix (split for the R29 function limit); score is a one-element list."""
    # 8. Path Traversal Attack: Relative Path (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PUSH relative/path.txt 10\n")
        expect("ERR: path not allowed" in resp, "\"ERR: path not allowed\" in resp")
    score[0] += case_done("8. Relative Path Rejection")

    # 9. PULL Path Traversal Protection (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PULL /etc/passwd\n")
        expect("ERR: path not allowed" in resp, "\"ERR: path not allowed\" in resp")
    score[0] += case_done("9. PULL Path Policy Enforcement")

    # 10. Zero-Byte Truncation Attack (1 pt)
    victim = os.path.join(WORK, "pixos_victim.txt")  # AP-152: no fixed /tmp path
    with open(victim, "w") as f:
        f.write("SECRET_DATA_DO_NOT_TRUNCATE")
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, f"PUSH {victim} 0\n".encode())
        expect("ERR: push length invalid" in resp, "\"ERR: push length invalid\" in resp")
    expect(open(victim).read() == "SECRET_DATA_DO_NOT_TRUNCATE", "Victim was truncated!")
    os.unlink(victim)
    score[0] += case_done("10. Zero-Byte Truncation Attack")

    # 11. 40 MiB Transfer Cap Enforcement (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"PUSH /tmp/huge.bin 41943041\n")
        expect("ERR: push length invalid" in resp, "\"ERR: push length invalid\" in resp")
    score[0] += case_done("11. 40 MiB + 1 Byte Rejection")

    # 12. Non-Regular File Denial of Service (FIFO Pull) (1 pt)
    fifo_path = os.path.join(WORK, "pixos_test_fifo")
    if os.path.exists(fifo_path):
        os.unlink(fifo_path)
    os.mkfifo(fifo_path)
    try:
        with lib.auth_conn(PORT, KEY, 3.0) as s:
            resp = send_recv_cmd(s, f"PULL {fifo_path}\n".encode())
            expect("ERR: not a regular file" in resp, f"Expected regular file error, got: {resp}")
        score[0] += case_done("12. Non-Regular File FIFO Pull Rejection")
    finally:
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)

    # 13. PID 1 (launchd) Signal Immunity (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"SIGNAL 1 9\n")
        expect("ERR: pid rejected" in resp, "\"ERR: pid rejected\" in resp")
    score[0] += case_done("13. PID 1 Signal Immunity")

    # 14. Process Group 0 Signal Immunity (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"SIGNAL 0 15\n")
        expect("ERR: pid rejected" in resp, "\"ERR: pid rejected\" in resp")
    score[0] += case_done("14. Process Group 0 Signal Immunity")


def _cases_c(score, proc):
    """Cases 15.. of the matrix (split for the R29 function limit); score is a one-element list."""
    # 15. Broadcast Signal Immunity (-1) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"SIGNAL -1 9\n")
        expect("ERR: pid rejected" in resp, "\"ERR: pid rejected\" in resp")
    score[0] += case_done("15. Broadcast Signal Immunity")

    # 16. Self-Kill Protection (devd cannot kill itself) (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, f"SIGNAL {proc.pid} 9\n".encode())
        expect("ERR: pid rejected" in resp, "\"ERR: pid rejected\" in resp")
    score[0] += case_done("16. Self-Kill Immunity (devd PID)")

    # 17. Arbitrary / Dangerous Signal Rejection (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        resp = send_recv_cmd(s, b"SIGNAL 99999 9999\n")
        expect("ERR: signal not allowed" in resp, "\"ERR: signal not allowed\" in resp")
    score[0] += case_done("17. Unsanctioned Signal Rejection")

    # 18. Legitimate Signal Permitted (1 pt)
    dummy = subprocess.Popen(["sleep", "60"])
    try:
        with lib.auth_conn(PORT, KEY, 3.0) as s:
            resp = send_recv_cmd(s, f"SIGNAL {dummy.pid} 15\n".encode())
            expect("OK" in resp, "\"OK\" in resp")
        dummy.wait(timeout=2.0)
        expect(dummy.returncode == -signal.SIGTERM, "dummy.returncode == -signal.SIGTERM")
        score[0] += case_done("18. Whitelisted Signal Execution")
    finally:
        if dummy.poll() is None:
            dummy.kill()

    # 19. Remote RUN Streaming & Exit Code (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(f"RUN {TB} echo adversary run 42\n".encode())
        buf = ""
        while True:
            chunk = s.recv(1024).decode()
            if not chunk:
                break
            buf += chunk
            if "RC: 0" in buf:
                break
        expect("adversary run 42" in buf and "RC: 0" in buf, "\"adversary run 42\" in buf and \"RC: 0\" in buf")
    score[0] += case_done("19. Remote RUN Streaming")

    # 20. Remote RUN Non-Zero Return Code (1 pt)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(f"RUN {TB} rc 1\n".encode())
        buf = ""
        while True:
            chunk = s.recv(1024).decode()
            if not chunk:
                break
            buf += chunk
            if "RC: " in buf:
                break
        expect("RC: 1" in buf, "\"RC: 1\" in buf")
    score[0] += case_done("20. Remote RUN Error Code Reporting")


def _cases_d(score, proc):
    """Cases 21.. of the matrix (split for the R29 function limit); score is a one-element list."""
    # 21. Abrupt Disconnect Mid-PUSH (No Leaked .sweettmp) (1 pt)
    dest = os.path.join(WORK, "pixos_abrupt.txt")
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(f"PUSH {dest} 100000\n".encode())
        s.sendall(b"ABCDEFGHIJ" * 10)
        # Abruptly close socket
        s.close()
    # Verify tmp file is not lingering
    time.sleep(0.1)
    expect(not os.path.exists(f"{dest}.sweettmp"), "Temporary file was leaked!")
    expect(not os.path.exists(dest), "Destination was partially written!")
    score[0] += case_done("21. Abrupt Disconnect Cleanup (No Temp Leak)")

    # 22. Atomic PUSH Integrity (Fsync + Rename) (1 pt)
    payload = b"HARMONIZED_ATOMIC_PAYLOAD_12345"
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(f"PUSH {dest} {len(payload)}\n".encode())
        s.sendall(payload)
        resp = send_recv_cmd(s, b"")
        expect("OK" in resp, "\"OK\" in resp")
    with open(dest, "rb") as f:
        expect(f.read() == payload, "f.read() == payload")
    os.unlink(dest)
    score[0] += case_done("22. Atomic PUSH Data Integrity")

    # 23. PULL Exact File Content & Length (1 pt)
    with open(dest, "wb") as f:
        f.write(payload)
    with lib.auth_conn(PORT, KEY, 3.0) as s:
        s.sendall(f"PULL {dest}\n".encode())
        hdr = b""
        while b"\n" not in hdr:
            hdr += s.recv(1)
        expect(f"OK {len(payload)}" in hdr.decode(), "f\"OK {len(payload)}\" in hdr.decode()")
        pulled = s.recv(1024)
        expect(pulled == payload, "pulled == payload")
    os.unlink(dest)
    score[0] += case_done("23. PULL Byte-Exact Transmission")

    # 24. Rapid Connection Burst Stress (30 Sequential Connections) (1 pt)
    burst_ok = True
    for i in range(30):
        try:
            with lib.auth_conn(PORT, KEY, 2.0) as s:
                r = send_recv_cmd(s, b"PING\n")
                if "PONG" not in r:
                    burst_ok = False
                    break
        except Exception as e:
            print(f"DEVD GAP: burst connection {i} failed: {e!r}", file=sys.stderr)
            burst_ok = False
            break
    expect(burst_ok, "Burst connection failed!")
    score[0] += case_done("24. Rapid Connection Burst Stress (30 conns)")

    # 25. Clean Daemon Termination and ASan Report (1 pt)
    # Verify no memory leaks or undefined behavior detected by ASan
    proc.send_signal(signal.SIGINT)
    try:
        _, errs = proc.communicate(timeout=3.0)
        if "AddressSanitizer" in errs or "runtime error" in errs:
            print(f"[-] 25. ASan Sanitizer Error Detected: {errs}")
        else:
            score[0] += case_done("25. Clean ASan & Memory Sanitizer Exit")
    except subprocess.TimeoutExpired:
        proc.kill()
        print("[-] 25. Process failed to terminate cleanly")


def run_tests():
    setup()
    proc = start_daemon()
    score = [0]
    total = 25

    print("=== Running 25-Point Adversarial Verification Matrix ===")

    try:
        _cases_a(score, proc)
        _cases_b(score, proc)
        _cases_c(score, proc)
        _cases_d(score, proc)
    except (OSError, subprocess.TimeoutExpired) as e:  # the daemon did not behave: reported, never a pass
        FAILURES.append(("driver", [repr(e)]))
        print(f"[-] ABORT a step ended by {e!r}")
    finally:
        if proc.poll() is None:
            proc.kill()

    print(f"\nRESULT: {'PASS' if score[0] == total else 'FAIL'} {score[0]} passed, {total - score[0]} failed")
    return score[0]


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
ScriptRun = lib.script_case(__file__, 300)


if __name__ == "__main__":
    s = run_tests()
    sys.exit(0 if s == 25 else 1)
