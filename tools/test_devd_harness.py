#!/usr/bin/env python3
"""
Oracle Test Harness: devd.c Protocol Verification
=================================================
Automated verifiable test harness for hill-climbing the implementation of
the org.pixelios.devd daemon.

Compiles tools/devd.c on host, spawns it on an ephemeral loopback port,
and validates protocol commands. Prints raw counts and a final RESULT: line (exit 0 all passed, 1 a check failed, 2 cannot run).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import test_devd_lib as lib

DEVD_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "devd.c"))
TMP_DIR = tempfile.mkdtemp(prefix="devd-harness-")
TOTAL = 6
BIN_PATH = os.path.join(TMP_DIR, "devd")


def compile_devd() -> tuple[bool, str]:
    if not os.path.exists(DEVD_SRC):
        return False, f"File {DEVD_SRC} does not exist."
    
    os.makedirs(TMP_DIR, exist_ok=True)
    cmd = ["gcc", "-O1", "-Wall", "-Wextra", "-pthread", "-o", BIN_PATH, DEVD_SRC]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return False, f"Compilation failed:\n{res.stderr}"
    return True, "OK"


def _tests_push_run_pull(s, tb) -> int:
    """Tests 3-5 (PUSH, RUN, PULL) on the authenticated socket; returns the points won (split for the R29 function limit)."""
    score = 0
    # Test 3: PUSH file atomically
    target_file = os.path.join(TMP_DIR, "pushed_test.txt")
    test_content = b"Hello from PixOS Hot Reload!\n"
    try:
        if os.path.exists(target_file):
            os.remove(target_file)
        push_cmd = f"PUSH {target_file} {len(test_content)}\n".encode()
        s.sendall(push_cmd + test_content)
        resp = s.recv(1024).decode()
        if "OK" in resp and os.path.exists(target_file):
            with open(target_file, "rb") as f:
                if f.read() == test_content:
                    print("[+] Test 3 (Atomic PUSH): PASSED")
                    score += 1
                else:
                    print("[-] Test 3 (Atomic PUSH): FAILED (content mismatch)")
        else:
            print(f"[-] Test 3 (Atomic PUSH): FAILED ({resp!r})")
    except Exception as e:
        print(f"[-] Test 3 (Atomic PUSH): FAILED ({e})")

    # Test 4: RUN command execution
    # rc 125 is devd's "client stalled" verdict: a grandchild of this test
    # process holding the daemon's stdout pipe past the 100 ms wait would
    # make the assertion fail for a reason unrelated to devd, so the only
    # retry is on rc 125.
    try:
        for attempt in range(3):
            s.sendall(f"RUN {tb} echo sweet hot reload\n".encode())
            resp = ""
            while "RC: " not in resp:
                chunk = s.recv(1024).decode()
                if not chunk:
                    break
                resp += chunk
            if "RC: 125" not in resp:
                break
            print(f"    (rc 125 = client stalled, retry {attempt + 1}/3)")
        if "sweet hot reload" in resp and "RC: 0" in resp:
            print("[+] Test 4 (Remote RUN Exec): PASSED")
            score += 1
        else:
            print(f"[-] Test 4 (Remote RUN Exec): FAILED ({resp!r})")
    except Exception as e:
        print(f"[-] Test 4 (Remote RUN Exec): FAILED ({e})")

    # Test 5: PULL file
    try:
        s.sendall(f"PULL {target_file}\n".encode())
        resp = b""
        while test_content not in resp:
            chunk = s.recv(1024)
            if not chunk:
                break
            resp += chunk
        if test_content in resp:
            print("[+] Test 5 (PULL File): PASSED")
            score += 1
        else:
            print(f"[-] Test 5 (PULL File): FAILED ({resp!r})")
    except Exception as e:
        print(f"[-] Test 5 (PULL File): FAILED ({e})")
    return score


def run_tests() -> int:
    score = 0
    print("--- Running devd.c Verifiable Test Suite ---")

    # Test 1: Compilation
    ok, msg = compile_devd()
    if not ok:
        print(f"[-] Test 1 (Compilation): FAILED ({msg})")
        return -1
    print("[+] Test 1 (Compilation): PASSED")
    score += 1

    # Start daemon in test mode on an ephemeral port (key file gate + challenge-response auth)
    key_path, key = lib.make_key_file(TMP_DIR)
    tb = lib.compile_testbin(TMP_DIR)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    proc = subprocess.Popen([BIN_PATH, "--port", str(port), "--test-mode", "--key-file", key_path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.01)

    try:
        # Test 2: Connection & PING
        try:
            s = lib.auth_conn(port, key, timeout=2.0)
            s.sendall(b"PING\n")
            resp = s.recv(1024).decode()
            if resp.startswith("PONG"):
                print("[+] Test 2 (PING/PONG Handshake): PASSED")
                score += 1
            else:
                print(f"[-] Test 2 (PING/PONG Handshake): FAILED (got {resp!r})")
        except Exception as e:
            print(f"[-] Test 2 (PING/PONG Handshake): FAILED ({e})")
            return score

        score += _tests_push_run_pull(s, tb)

        # Test 6: Memory Footprint (<64 KB binary)
        bin_size = os.path.getsize(BIN_PATH)
        if bin_size < 64 * 1024:
            print(f"[+] Test 6 (Lean Footprint - {bin_size} B < 64KB): PASSED")
            score += 1
        else:
            print(f"[-] Test 6 (Lean Footprint): FAILED ({bin_size} B >= 64KB)")

    finally:
        proc.terminate()
        proc.wait(timeout=2.0)

    return score


def main():
    score = run_tests()
    if score < 0:
        print("RESULT: ERROR devd.c did not compile")
        sys.exit(2)
    failed = TOTAL - score
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} {score} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
ScriptRun = lib.script_case(__file__, 300)


if __name__ == "__main__":
    main()
