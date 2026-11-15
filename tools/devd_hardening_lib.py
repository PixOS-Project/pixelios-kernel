"""Shared state and helpers of the devd hardening suite (tools/test_devd_hardening.py), split out under the R29 size limit (T-c24f).
The counters and KEY/TB/KEY_FILE live HERE and are used as H.<name> (module attributes, never copied)."""
import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import re
import time

import test_devd_lib as lib

HERE = os.path.dirname(os.path.abspath(__file__))
# test_devd_mutants.py runs this same file against a mutated devd.c: DEVD_MUT_SRC is the source to
# build, DEVD_MUT_TMP a private work dir under /tmp/ (parallel mutants must not share one), and
# DEVD_MUT_REPORT a JSON file that receives the passed/failed/aborted check names. Unset = stock.
DEVD_SRC = os.environ.get("DEVD_MUT_SRC") or os.path.join(HERE, "devd.c")
PATCH_0117 = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0117-sweet-dev-overlay.patch")
TMP_DIR = os.environ.get("DEVD_MUT_TMP") or "/tmp/devd-hardening"
REPORT = os.environ.get("DEVD_MUT_REPORT")
STOCK_BIN = os.path.join(TMP_DIR, "devd")
FAST_BIN = os.path.join(TMP_DIR, "devd-fast")
PHONE_BIN = os.path.join(TMP_DIR, "devd-phone")          # phone code path against loopback (review devd-r1 F2)
PHONE_BADIF_BIN = os.path.join(TMP_DIR, "devd-phone-badif")
PHONE_BADUID_BIN = os.path.join(TMP_DIR, "devd-phone-baduid")
MAX_PUSH = 40 * 1024 * 1024

FAILED = []
PASSED = []
ABORTED = []  # a step ended by a socket/OS error: reported, never a pass (a mutant suite does not count it as a kill)
passed_count = 0
skipped = []
KEY_FILE = None
KEY = None
TB = None  # compiled test program RUN may execute


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed_count
    if ok:
        passed_count += 1
        PASSED.append(name)
        print(f"[+] {name}: PASS")
    else:
        FAILED.append(name)
        print(f"[-] {name}: FAIL {detail}")


def skip(name: str, why: str) -> None:
    skipped.append(name)
    print(f"SKIP: {name}: {why}")


def compile_devd() -> None:
    global KEY_FILE, KEY, TB
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, mode=0o700)
    KEY_FILE, KEY = lib.make_key_file(TMP_DIR)
    TB = lib.compile_testbin(TMP_DIR)
    base = ["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread"]
    subprocess.run(base + ["-o", STOCK_BIN, DEVD_SRC], check=True)
    subprocess.run(base + ["-DDEVD_RUN_TIMEOUT_SEC=2", "-o", FAST_BIN, DEVD_SRC], check=True)
    euid = os.geteuid()
    subprocess.run(base + phone_defs("lo", euid) + ["-o", PHONE_BIN, DEVD_SRC], check=True)
    subprocess.run(base + phone_defs("nonexist0", euid) + ["-o", PHONE_BADIF_BIN, DEVD_SRC], check=True)
    # the phone key owner is a uid the caller is NOT (on the phone: 0, and the caller of a host test is not)
    subprocess.run(base + phone_defs("lo", euid + 1) + ["-o", PHONE_BADUID_BIN, DEVD_SRC], check=True)


def phone_defs(iface: str, key_uid: int):
    """-D flags that point devd's phone constants at loopback (devd.c DEVD_PHONE_*)."""
    return [f'-DDEVD_PHONE_IFACE="{iface}"', '-DDEVD_PHONE_ADDR="127.0.0.2"', '-DDEVD_PHONE_PEER="127.0.0.1"',
            f"-DDEVD_PHONE_KEY_UID={key_uid}", f'-DDEVD_KEY_PATH="{KEY_FILE}"']


class Daemon:
    def __init__(self, binary: str, key_file: str = None, umask: int = None):
        for _ in range(8):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
            probe.close()
            self.log = open(os.path.join(TMP_DIR, f"devd-{self.port}.log"), "w")
            self.proc = subprocess.Popen(
                [binary, "--port", str(self.port), "--test-mode", "--key-file", key_file or KEY_FILE],
                stdout=self.log, stderr=subprocess.STDOUT,
                # cwd inside the test-mode root: a relative RUN path ("./tb") would resolve to an
                # allowed file, so only the explicit "must be absolute" rule can refuse it
                cwd=TMP_DIR,
                preexec_fn=(lambda: os.umask(umask)) if umask is not None else None)
            time.sleep(0.1)
            if self.proc.poll() is None:
                return  # bound and listening
            self.log.close()
            # bind race (port reused between close and Popen): try another
        raise RuntimeError("could not find a free port for devd")

    def conn(self, timeout: float = 5.0) -> socket.socket:
        """An authenticated connection (challenge answered), ready for commands."""
        return lib.auth_conn(self.port, KEY, timeout)

    def raw(self, timeout: float = 5.0, src: str = None) -> socket.socket:
        """A connection that has not answered the challenge."""
        return lib.connect(self.port, timeout, src)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=5.0)
        self.log.close()
        with open(self.log.name) as f:
            for line in f:
                print("    " + line.rstrip())


def recv_line(sock: socket.socket, timeout: float) -> bytes:
    """One protocol line; b"<EOF>" when the peer closed (an empty ERR reads
    as b'' otherwise), or the exception's repr."""
    sock.settimeout(timeout)
    data = b""
    try:
        while not data.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                return data or b"<EOF>"
            data += chunk
    except OSError as e:
        return repr(e).encode()
    return data


def recv_until(sock: socket.socket, needle: bytes, timeout: float) -> bytes:
    sock.settimeout(timeout)
    data = b""
    end = time.monotonic() + timeout
    while needle not in data:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        if time.monotonic() > end:
            break
    return data


def read_all(sock: socket.socket, timeout: float = 3.0) -> bytes:
    """Everything until EOF/reset/timeout (what a rejected peer still receives)."""
    sock.settimeout(timeout)
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:  # lint-silent: expected a timeout or reset ends the read; the caller checks the bytes
        pass
    return data


def run_framed(d: Daemon, line: str, timeout: float = 8.0):
    """One RUN on a fresh connection; returns (raw response up to and including the RUNEND line of its own id,
    run id or None). Reads until that exact line arrives (a forged one in the output does not stop it)."""
    s = d.conn()
    s.sendall(f"RUN {line}\n".encode())
    s.settimeout(timeout)
    resp = b""
    end = time.monotonic() + timeout
    rid = None
    try:
        while time.monotonic() < end:
            chunk = s.recv(65536)
            if not chunk:
                break
            resp += chunk
            m = re.match(rb"RUNID ([0-9a-f]{16})\n", resp)
            if m:
                rid = m.group(1).decode()
                if re.search(rb"(^|\n)RUNEND " + rid.encode() + rb" RC: -?[0-9]+\n$", resp):
                    break
            elif resp.endswith(b"\n") and not resp.startswith(b"RUNID"):
                break  # an ERR answer, nothing else follows
    except OSError:  # lint-silent: expected a timeout or reset ends the read; the caller checks the bytes
        pass
    s.close()
    return resp, rid


def slurp(path: str):
    """File content, or None when it does not exist (a mutant may leave the file out)."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:  # lint-silent: expected the file was not written (the caller asserts on None)
        return None


def push_one(d: Daemon, path: str, data: bytes, timeout: float = 5.0) -> bytes:
    """PUSH data to path on a fresh connection; the first reply line (OK / ERR ...)."""
    s = d.conn()
    s.sendall(f"PUSH {path} {len(data)}\n".encode() + data)
    r = recv_line(s, timeout)
    s.close()
    return r


def pid_alive(pid: int) -> bool:
    """Alive and not a zombie (an orphan killed with SIGKILL may sit as a zombie until init reaps it)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):  # lint-silent: expected the pid has no /proc entry: the process exited
        return False


def conn_via(dst: str, port: int, src: str, timeout: float = 5.0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.bind((src, 0))
        s.connect((dst, port))
    except OSError:
        s.close()
        raise
    return s


def lo_has_127_0_0_2() -> bool:
    return lib.lo_has_addr("127.0.0.2")


def spawn_expect_exit(args, name: str, want_text: str, binary: str = None) -> None:
    """devd must exit 1 and print a `SWEET GAP: devd gate` line containing want_text."""
    logp = os.path.join(TMP_DIR, "gate.log")
    with open(logp, "w") as lg:
        p = subprocess.Popen([binary or STOCK_BIN] + args, stdout=lg, stderr=subprocess.STDOUT)
        try:
            rc = p.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            rc = "still running"
    with open(logp) as f:
        log = f.read()
    check(name, rc == 1 and "SWEET GAP: devd gate:" in log and want_text in log,
          f"(rc={rc} log={log[-200:]!r})")


def parse_allow_table(text: str, anchor: str):
    """[(prefix, kind)] from the `anchor ... = { ... };` initializer."""
    start = text.index(anchor)
    body = text[start:text.index("};", start)]
    return re.findall(r'\{\s*"([^"]+)"\s*,\s*(K_[A-Z]+)\s*\}', body)


def step(fn, *args) -> None:
    """Run one test group. A socket/OS error (the daemon did not behave) is reported as ABORTED, loudly,
    and fails the run; it is never counted as a pass. Any other exception is a bug here and propagates."""
    try:
        fn(*args)
    except (OSError, subprocess.TimeoutExpired) as e:
        ABORTED.append(f"{fn.__name__}: {e!r}")
        print(f"[-] ABORT {fn.__name__}: {e!r}")
