#!/usr/bin/env python3
"""
test_devd_lib.py: shared helpers for the devd host tests (not a test itself).

devd gates on a key file and answers every connection with a CHALLENGE (review gemini-devd-r1 F1,
research 111). The helpers here write a key file the daemon accepts in --test-mode, answer the
challenge with Python's hmac (an independent implementation of the one in devd.c), and compile a
tiny multiplexed test program that RUN may execute (RUN has no shell).
Importing this module has no side effects; running it prints one RESULT line.
"""

import hashlib
import hmac
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEVD_SRC = os.path.join(HERE, "devd.c")

# One program, selected by argv[1]. RUN executes it by absolute path (no shell, no quoting).
TESTBIN_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
int main(int argc, char **argv) {
    if (argc < 2) return 64;
    if (strcmp(argv[1], "echo") == 0) {            /* echo ARGS...: argv[2..] joined by one space */
        for (int i = 2; i < argc; i++) printf("%s%s", i > 2 ? " " : "", argv[i]);
        printf("\n");
        return 0;
    }
    if (strcmp(argv[1], "echorc") == 0) {          /* echorc N ARGS...: print ARGS, exit N */
        for (int i = 3; i < argc; i++) printf("%s%s", i > 3 ? " " : "", argv[i]);
        printf("\n");
        return argc > 2 ? atoi(argv[2]) : 0;
    }
    if (strcmp(argv[1], "noeol") == 0) { printf("no newline"); return 0; }   /* no trailing newline */
    if (strcmp(argv[1], "rc") == 0) return argc > 2 ? atoi(argv[2]) : 0;
    if (strcmp(argv[1], "sleep") == 0) { sleep(argc > 2 ? (unsigned)atoi(argv[2]) : 1); return 0; }
    if (strcmp(argv[1], "yes") == 0) {             /* floods stdout until killed */
        const char *t = argc > 2 ? argv[2] : "y";
        size_t n = strlen(t);
        for (;;) { if (write(1, t, n) < 0) return 1; if (write(1, "\n", 1) < 0) return 1; }
    }
    if (strcmp(argv[1], "touch") == 0) {           /* touch PATH */
        int fd = open(argv[2], O_WRONLY | O_CREAT, 0644);
        if (fd < 0) return 1;
        close(fd);
        return 0;
    }
    if (strcmp(argv[1], "bgquiet") == 0) {         /* background daemon (setsid: leaves the RUN's group, review devd-r1 F13) with no stdio; prints its pid */
        pid_t c = fork();
        if (c == 0) {
            setsid();
            int dn = open("/dev/null", O_RDWR);
            dup2(dn, 0); dup2(dn, 1); dup2(dn, 2);
            sleep(30);
            _exit(0);
        }
        printf("%d\n", (int)c);
        return 0;
    }
    if (strcmp(argv[1], "bgkeep") == 0) {          /* background sleeper that keeps the pipe; prints grand */
        pid_t c = fork();
        if (c == 0) { sleep(30); _exit(0); }
        printf("grand\n");
        fflush(stdout);
        return 0;
    }
    if (strcmp(argv[1], "spoof") == 0) {           /* review devd-r1 E8b: forge a status line, kill devd, exit 3 */
        printf("RC: 0\n");
        fflush(stdout);
        sleep(1);
        kill(getppid(), SIGKILL);
        return 3;
    }
    if (strcmp(argv[1], "forge") == 0) {           /* prints a forged end line (it cannot know the real id), exits 5 */
        printf("RUNEND 0000000000000000 RC: 0\n");
        return 5;
    }
    if (strcmp(argv[1], "bgsurvive") == 0) {       /* review devd-r1 F13: a grandchild that drops the pipe; prints its pid */
        pid_t c = fork();
        if (c == 0) {
            int dn = open("/dev/null", O_RDWR);
            dup2(dn, 0); dup2(dn, 1); dup2(dn, 2);
            sleep(30);
            _exit(0);
        }
        printf("%d\n", (int)c);
        return 0;
    }
    if (strcmp(argv[1], "bgflood") == 0) {         /* review devd-r1 R12: PIDFILE; a pipe-dropping grandchild + a flooding parent */
        pid_t c = fork();
        if (c == 0) {
            int dn = open("/dev/null", O_RDWR);
            dup2(dn, 0); dup2(dn, 1); dup2(dn, 2);
            FILE *pf = fopen(argv[2], "w");
            if (pf) { fprintf(pf, "%d\n", (int)getpid()); fclose(pf); }
            sleep(60);
            _exit(0);
        }
        for (;;) { if (write(1, "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy\n", 32) < 0) return 1; }
    }
    if (strcmp(argv[1], "env") == 0) {             /* review devd-r1 F13: dump the environment, one var per line */
        extern char **environ;
        for (char **e = environ; *e; e++) printf("%s\n", *e);
        return 0;
    }
    if (strcmp(argv[1], "sigpipe") == 0) {         /* review devd-r1 F13: is SIGPIPE ignored here? 1 = ignored */
        struct sigaction sa;
        sigaction(SIGPIPE, NULL, &sa);
        printf("sigpipe_ignored=%d\n", sa.sa_handler == SIG_IGN);
        return 0;
    }
    if (strcmp(argv[1], "id") == 0) { printf("tb-id\n"); return 0; }
    return 65;
}
'''


def new_tmp(prefix: str) -> str:
    """A private dir under /tmp (devd's test-mode prefix), mode 0700."""
    return tempfile.mkdtemp(prefix=prefix, dir="/tmp")


def compile_testbin(dirpath: str) -> str:
    src = os.path.join(dirpath, "tb.c")
    out = os.path.join(dirpath, "tb")
    with open(src, "w") as f:
        f.write(TESTBIN_C)
    subprocess.run(["gcc", "-O1", "-Wall", "-o", out, src], check=True)
    os.chmod(out, 0o755)
    return out


def make_key_file(dirpath: str, mode: int = 0o600, content=None):
    """Write dirpath/devd.key; returns (path, key_bytes). content overrides the text."""
    key = secrets.token_bytes(32)
    path = os.path.join(dirpath, "devd.key")
    text = content if content is not None else key.hex() + "\n"
    if os.path.lexists(path):
        os.unlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, mode)
    return path, key


def read_line(sock: socket.socket, maxlen: int = 256) -> bytes:
    data = b""
    while not data.endswith(b"\n") and len(data) < maxlen:
        c = sock.recv(1)
        if not c:
            break
        data += c
    return data


def hmac_hex(key: bytes, challenge_hex: str) -> str:
    return hmac.new(key, bytes.fromhex(challenge_hex), hashlib.sha256).hexdigest()


def read_challenge(sock: socket.socket) -> str:
    line = read_line(sock).decode(errors="replace").strip()
    if not line.startswith("CHALLENGE ") or len(line) != 10 + 32:
        raise OSError(f"no CHALLENGE line, got {line!r}")
    return line[10:]


def connect(port: int, timeout: float = 5.0, src: str = None) -> socket.socket:
    deadline = time.monotonic() + 5.0
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if src:
                s.bind((src, 0))
            s.connect(("127.0.0.1", port))
            return s
        except OSError:
            s.close()
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def auth_conn(port: int, key: bytes, timeout: float = 5.0) -> socket.socket:
    """Connect and answer the challenge; the connection is then ready for commands."""
    s = connect(port, timeout)
    chal = read_challenge(s)
    s.sendall(f"AUTH {hmac_hex(key, chal)}\n".encode())
    return s


def cleanup(*dirs: str) -> None:
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


NETNS_ENV = "DEVD_TEST_NETNS"


def lo_has_addr(addr: str = "127.0.0.2") -> bool:
    """True when addr is a configured LOCAL address (getifaddrs, which devd's phone gate uses, lists only those)."""
    try:
        with open("/proc/net/fib_trie") as f:
            lines = f.read().splitlines()
    except OSError:  # lint-silent: expected no /proc/net/fib_trie: answered as "address not configured", the suite then re-execs in its own netns
        return False
    return any(ln.strip() == f"|-- {addr}" and i + 1 < len(lines) and "/32 host LOCAL" in lines[i + 1]
               for i, ln in enumerate(lines))


def reexec_in_netns(script: str) -> None:
    """Review devd-r2 N2 / AP-341: the suite creates its own precondition for the phone-mode rows. Unless 127.0.0.2
    is already a lo address (or this is the re-exec), re-run `script` in a private user+net namespace
    (`unshare -r -n`: lo up, 127.0.0.2/8 on lo), then a nested user namespace back at the caller's uid/gid, so the
    suite never runs as (namespace) root. Loopback only; nothing on the host network changes. Never returns: exits
    with the child's status. If the namespace cannot be made, it says so loudly and exits 2 (no silent skip)."""
    if os.environ.get(NETNS_ENV) == "1" or lo_has_addr("127.0.0.2"):
        return
    uid, gid = os.getuid(), os.getgid()
    try:  # review devd-r3 F4: a host without user namespaces gets a RESULT line, never a bare exit 1
        probe = subprocess.run(["unshare", "-r", "-n", "true"], capture_output=True, text=True)
        perr = probe.stderr.strip()[-200:] if probe.returncode != 0 else ""
    except OSError as e:
        perr = repr(e)
    if perr:
        print(f"RESULT: ERROR netns: `unshare -r -n` is not available here ({perr}); the phone-mode rows need "
              f"127.0.0.2 on lo: run on a host with user namespaces, or add the address and set {NETNS_ENV}=1")
        sys.exit(2)
    inner = ("{ ip link set lo up && ip addr add 127.0.0.2/8 dev lo; } || "
             "{ echo 'RESULT: ERROR netns: ip link/addr on lo failed'; exit 2; }; "
             f'exec unshare -U --map-user={uid} --map-group={gid} "$@"')
    cmd = ["unshare", "-r", "-n", "sh", "-c", inner, "sh", "env", f"{NETNS_ENV}=1",
           sys.executable, os.path.abspath(script)] + sys.argv[1:]
    print(f"NETNS: re-exec in a private user+net namespace with 127.0.0.2 on lo (uid {uid}): {script}", flush=True)
    try:
        rc = subprocess.run(cmd).returncode
    except OSError as e:
        rc = None
        err = repr(e)
    if rc is None or rc == 126 or rc == 127:
        print(f"RESULT: ERROR netns: cannot create the user+net namespace for the phone-mode rows "
              f"({err if rc is None else f'rc={rc}'}); set {NETNS_ENV}=1 only on a runner that already has 127.0.0.2 on lo")
        sys.exit(2)
    sys.exit(rc)


def run_script(script: str, timeout: int):
    """Run one of the RESULT-line devd test scripts as the gate would: own process, stdin closed, bounded.
    Returns (rc, output, last RESULT line or None, [SKIP lines])."""
    try:
        p = subprocess.run([sys.executable, os.path.abspath(script)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        rc = None
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += f"\nRESULT: ERROR {os.path.basename(script)} timed out after {timeout}s"
    results = [ln for ln in out.splitlines() if ln.startswith("RESULT:")]
    skips = [ln.strip() for ln in out.splitlines() if ln.lstrip().startswith("SKIP:")]
    return rc, out, (results[-1] if results else None), skips


def script_case(script: str, timeout: int):
    """T-c24f (gate FAIL 9030c86c: `unittest discover` found 0 tests in the devd scripts, rc=5): a TestCase that runs
    `script` once and passes only on exit 0 AND a last `RESULT: PASS` line. Every `SKIP:` line the script prints becomes
    a visible unittest skip (rule 8: the gate's `SKIPPED TEST` lines name it), never a silent pass. Use at module level:
    `ScriptRun = lib.script_case(__file__, 300)`."""
    class ScriptRun(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.rc, cls.out, cls.result, cls.skips = run_script(script, timeout)
            print(f"{os.path.basename(script)}: rc={cls.rc} {cls.result or 'RESULT: (none)'}", file=sys.stderr)

        def test_script_result_pass(self):
            tail = self.out[-6000:]
            self.assertIsNotNone(self.result, f"no RESULT line\n{tail}")
            self.assertTrue(self.result.startswith("RESULT: PASS"), f"{self.result}\n{tail}")
            self.assertEqual(self.rc, 0, f"{self.result} but exit {self.rc}\n{tail}")

        def test_script_skips_none(self):
            if self.skips:
                self.skipTest(f"{len(self.skips)} script SKIP line(s): " + " | ".join(self.skips)[:400])
    ScriptRun.__name__ = ScriptRun.__qualname__ = "ScriptRun"
    ScriptRun.__module__ = os.path.splitext(os.path.basename(script))[0]
    return ScriptRun


class ScriptCaseSelfTest(unittest.TestCase):
    """The wrapper itself fails a FAIL line, a missing RESULT line, a non-zero exit after PASS and a timeout."""
    def _verdict(self, body: str, timeout: int = 20):
        d = tempfile.mkdtemp(prefix="devd-scriptcase-")
        try:
            path = os.path.join(d, "fake.py")
            with open(path, "w") as f:
                f.write(body)
            case = script_case(path, timeout)
            res = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(case).run(res)
            return res
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_pass_passes(self):
        res = self._verdict('print("RESULT: PASS 3 passed, 0 failed")\n')
        self.assertTrue(res.wasSuccessful() and not res.skipped, (res.failures, res.skipped))

    def test_fail_line_fails(self):
        self.assertEqual(len(self._verdict('print("RESULT: FAIL 1 failed")\n').failures), 1)

    def test_no_result_line_fails(self):
        self.assertEqual(len(self._verdict('print("hello")\n').failures), 1)

    def test_pass_with_nonzero_exit_fails(self):
        self.assertEqual(len(self._verdict('import sys\nprint("RESULT: PASS x")\nsys.exit(1)\n').failures), 1)

    def test_timeout_fails(self):
        self.assertEqual(len(self._verdict('import time\nprint("RESULT: PASS x", flush=True)\ntime.sleep(30)\n', 2).failures), 1)

    def test_skip_line_is_a_visible_skip(self):
        res = self._verdict('print("SKIP: (d) needs root")\nprint("RESULT: PASS 1 passed, 1 skipped")\n')
        self.assertEqual(len(res.skipped), 1)
        self.assertIn("needs root", res.skipped[0][1])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
