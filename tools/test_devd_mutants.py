#!/usr/bin/env python3
"""
test_devd_mutants.py: honest mutation check of the devd.c guards (R53 step 4).

The checks that kill mutants are the named checks of tools/test_devd_hardening.py, run unchanged
against a mutated copy of devd.c (DEVD_MUT_SRC / DEVD_MUT_TMP / DEVD_MUT_REPORT, see that file).

Rules, in this order:
  1. Baseline first: the unmodified devd.c must pass 100% of those checks (and abort nothing);
     otherwise the suite prints `RESULT: FAIL baseline`, exits 2 and runs no mutant.
  2. Each mutant is a textual edit whose anchor must match EXACTLY ONCE. Zero or many matches is
     an ERROR, never a kill.
  3. KILLED = the mutated source compiles (same flags as the tests: -Wall -Wextra -Werror) AND at
     least one of the check names the mutant lists FAILED (a real assertion failure). A compile
     error, a missing report (the test driver crashed or timed out), an ABORT (a socket error in a
     step, reported but never a pass) without a failing named check, or a failing run whose failing
     checks are not the listed ones, is an ERROR. A mutant whose checks all pass SURVIVED.
  4. Any SURVIVED or ERROR makes the suite exit 1. Output is raw counts only, no percentage.

Not a behavioural mutant, so not counted: replacing the constant-time compare ct_equal() with
memcmp(). The result is identical on every input; only timing differs, and a host test cannot
observe a timing difference of a 64-byte compare through a socket reliably. It is a code-review item.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import test_devd_lib as lib

HERE = os.path.dirname(os.path.abspath(__file__))
DEVD_SRC = os.path.join(HERE, "devd.c")
HARDENING = os.path.join(HERE, "test_devd_hardening.py")
GCC = ["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread"]  # same flags as the hardening suite
RUN_LIMIT_S = 400

# (id, what it breaks, [(anchor, replacement)], [names of the checks that must fail], reason if no test can kill it)
MUTANTS = [
    ("M01_TMP_NONREGULAR_ACCEPTED", "the .sweettmp name may be a symlink (it is unlinked and replaced instead of refused)",
     [("        if (!S_ISREG(ts.st_mode)) {\n            errno = EEXIST;\n            return -1;\n        }\n        if (unlink(tmp_path) != 0)",
       "        if (unlink(tmp_path) != 0)")],
     ["open() failure after an accepted header does not execute the payload"]),
    ("M02_ALLOWLIST_ETC_ADDED", "add an /etc/ prefix to k_allow",
     [('{ "/usr/lib/", K_DIR }, { "/usr/local/", K_DIR },',
       '{ "/etc/", K_DIR }, { "/usr/lib/", K_DIR }, { "/usr/local/", K_DIR },')],
     ["(g) devd.c k_allow[] equals the kernel's sd_allow[]", "every rejected request logs a SWEET GAP"]),
    ("M03_RUN_CLIENT_GONE_IGNORED", "RUN loop ignores exec_command_bounded() = -1 (gone/stalled client)",
     [("if (exec_command_bounded(client_fd, rpath, argv) < 0)\n"
       "                return; /* peer is gone/stalled: drop the connection */",
       "(void)exec_command_bounded(client_fd, rpath, argv);")],
     ["deaf-client RUN flood: the next client is served within 16 s"]),
    ("M04_PUSH_RENAME_UNCHECKED", "PUSH does not check the rename() result",
     [("if (rename(tmp_path, rpath) != 0) {", "if ((void)rename(tmp_path, rpath), 0) {")],
     ["PUSH whose rename() fails answers ERR: rename failed, never OK"]),
    ("M05_PROD_ALLOWS_TMP", "the production (non-test-mode) allowlist accepts /tmp/",
     [("    if (test_mode) {\n        if (strncmp(path, k_test_prefix",
       "    (void)test_mode;\n    if (1) {\n        if (strncmp(path, k_test_prefix")],
     ["(d) devd --selftest"]),
    ("M06_AUTH_HMAC_COMPARE_SKIPPED", "always accept AUTH (the HMAC compare is skipped)",
     [("if (!ct_equal(want_hex, line + 5, 64)) {", "if (0 && !ct_equal(want_hex, line + 5, 64)) {")],
     ["(b) wrong HMAC rejected", "(b) malformed/wrong AUTH"]),
    ("M07_COMMANDS_AFTER_FAILED_AUTH", "a connection whose AUTH failed still gets its commands parsed",
     [("if (auth_client(client_fd, key) != 0)\n        return;", "if (auth_client(client_fd, key) != 0 && 0)\n        return;")],
     ["(b) wrong HMAC rejected", "(b) malformed/wrong AUTH"]),
    ("M08_PEER_CHECK_REMOVED", "no peer address check after accept()",
     [('if (client_addr.sin_addr.s_addr != inet_addr(test_mode ? "127.0.0.1" : DEVD_PHONE_PEER)) {', "if (0) {")],
     ["(f) connection from 127.0.0.2 is dropped"]),
    ("M09_RUN_THROUGH_SHELL", "RUN hands its line to /bin/sh -c",
     [("        execve(cmd, argv, k_run_env); /* cmd =",
       "        { char sc[1024] = \"\"; int ai;\n"
       "          for (ai = 0; argv[ai] != NULL; ai++) {\n"
       "              strncat(sc, argv[ai], sizeof(sc) - strlen(sc) - 1);\n"
       "              strncat(sc, \" \", sizeof(sc) - strlen(sc) - 1);\n"
       "          }\n"
       "          execl(\"/bin/sh\", \"sh\", \"-c\", sc, (char *)NULL); }\n"
       "        execve(cmd, argv, k_run_env); /* cmd =")],
     ["(e) shell metacharacters are literal argument text"]),
    ("M10_RELATIVE_PATH_ACCEPTED", "path_allowed() accepts a relative path (daemon cwd is inside the allowed root)",
     [("    if (path[0] != '/' || strlen(path) > 511)\n        return 0;",
       "    if (path[0] != '/')\n        return 1;\n    if (strlen(path) > 511)\n        return 0;")],
     ["(e) RUN ./tb (relative) rejected"]),
    ("M11_KEY_MODE_CHECK_REMOVED", "the key file mode check (must be 0600) is gone",
     [('if ((st->st_mode & 07777) != 0600) return "mode is not 0600";', "/* mutant: no mode check */")],
     ["(d) key file mode 0644", "(d) devd --selftest"]),
    ("M12_LISTEN_SOCKET_INHERITED", "the listening socket is not close-on-exec",
     [("int server_fd = cloexec_fd(socket(AF_INET, SOCK_STREAM, 0));", "int server_fd = socket(AF_INET, SOCK_STREAM, 0);")],
     ["backgrounded RUN child holds no inherited socket", "devd restarts on the same port"]),
    ("M13_PUSH_REJECT_NOT_DRAINED", "a rejected PUSH no longer skips its payload",
     [("if (skip && skip_payload(client_fd, total_len) < 0) {", "if (skip && 0 && skip_payload(client_fd, total_len) < 0) {")],
     ["resolved-path reject answers only after draining its payload", "reject: streamed payload is drained"]),
    ("M15_PUSH_LEN0_KEEPS_CONNECTION", "a length-0 PUSH answers ERR but keeps parsing the stream (review devd-r1 F1)",
     [('devd_log("PUSH: rejected length 0");\n                push_err = "ERR: push length invalid (must be 1..41943040)\\n";\n                cut = 1;',
       'devd_log("PUSH: rejected length 0");\n                push_err = "ERR: push length invalid (must be 1..41943040)\\n";')],
     ["length-0 PUSH: ERR then the connection is dropped", "size-0 PUSH whose body holds a RUN line"]),
    ("R01_IFACE_CHECK_OFF", "phone mode no longer requires the phone address to belong to the phone interface (review devd-r1 F2)",
     [("} else if (!iface_has_addr(DEVD_PHONE_IFACE, inet_addr(DEVD_PHONE_ADDR))) {", "} else if (0) {")],
     ["(p) phone build, interface without the address"]),
    ("R02_PHONE_BIND_ANY", "phone mode binds INADDR_ANY instead of the phone address",
     [('addr.sin_addr.s_addr = inet_addr(test_mode ? "127.0.0.1" : DEVD_PHONE_ADDR);',
       'addr.sin_addr.s_addr = test_mode ? inet_addr("127.0.0.1") : htonl(INADDR_ANY);')],
     ["(p) a connection to 127.0.0.1 is refused"]),
    ("R03_PEER_CHECK_TEST_MODE_ONLY", "the peer check runs only in test mode",
     [('if (client_addr.sin_addr.s_addr != inet_addr(test_mode ? "127.0.0.1" : DEVD_PHONE_PEER)) {',
       'if (test_mode && client_addr.sin_addr.s_addr != inet_addr("127.0.0.1")) {')],
     ["(p) a peer that is not the phone peer"]),
    ("R04_PHONE_KEY_OWNER_IS_CALLER", "phone mode expects the key file owned by the caller instead of the phone key uid",
     [("test_mode ? geteuid() : (uid_t)DEVD_PHONE_KEY_UID", "geteuid()")],
     ["(p) phone build, key file owned by the caller"]),
    ("M16_RUNEND_ID_CONSTANT", "the RUNEND line carries a constant id instead of the run's id (review devd-r1 F3)",
     [('"RUNEND %s RC: %d\\n", run_id, rc);', '"RUNEND %s RC: %d\\n", run_id[0] ? "0000000000000000" : "", rc);')],
     ["RUN answer starts with RUNID", "a forged RUNEND line in the program output"]),
    ("M17_RUN_ID_NOT_RANDOM", "every RUN gets the same id",
     [("hex_encode(id_raw, sizeof(id_raw), run_id);", "memset(id_raw, 7, sizeof(id_raw));\n    hex_encode(id_raw, sizeof(id_raw), run_id);")],
     ["every RUN gets a fresh id"]),
    ("M18_RUNEND_GLUED_TO_OUTPUT", "no line break before RUNEND when the output lacked one",
     [("    if (!last_nl)\n        b[n++] = '\\n';", "    (void)last_nl;")],
     ["output without a trailing newline: devd ends the line"]),
    ("M19_TMP_STALE_NOT_UNLINKED", "a stale or hard-linked .sweettmp is not unlinked before the O_EXCL open (review devd-r1 F6)",
     [("if (lstat(tmp_path, &ts) == 0) {", "errno = ENOENT;\n    if (0) {")],
     ["a stale regular <path>.sweettmp from a crashed push is replaced", "a hard link planted at <path>.sweettmp"]),
    ("M20_TMP_TRUNC_THROUGH_LINK", "the temp open truncates a pre-existing file (O_TRUNC, no unlink): a hard link writes through",
     [("if (lstat(tmp_path, &ts) == 0) {", "errno = ENOENT;\n    if (0) {"),
      ("O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600", "O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600")],
     ["a hard link planted at <path>.sweettmp"]),
    ("M21_RENAME_FAIL_LEAVES_TMP", "a failed rename leaves the .sweettmp behind",
     [("                unlink(tmp_path); /* no residue after a failed rename */\n", "")],
     ["a failed rename leaves no <path>.sweettmp behind"]),
    ("R07_PUSH_MODE_0777", "pushed files get mode 0777 (review devd-r1 F4)",
     [("            return 0755;\n    if (test_mode &&", "            return 0777;\n    if (test_mode &&"),
      ('return strstr(rpath, "/devd-lib/") != NULL ? 0644 : 0755;', 'return strstr(rpath, "/devd-lib/") != NULL ? 0644 : 0777;')],
     ["pushed file has mode 0755 exactly", "(d) devd --selftest"]),
    ("N01_RESERVED_SUFFIX_ALLOWED", "PUSH accepts .sweetorig / .sweettmp names",
     [('if (has_suffix(path, n, ".sweettmp") || has_suffix(path, n, ".sweetorig"))\n        return "reserved suffix";',
       'if (has_suffix(path, n, ".sweetmutant"))\n        return "reserved suffix";')],
     ["PUSH to a reserved .sweetorig name refused", "PUSH to <file>.sweetorig cannot overwrite a restore source"]),
    ("N02_SYMLINK_COMPONENT_ALLOWED", "PUSH follows symlink components inside the allowed root",
     [('if (S_ISLNK(st.st_mode)) return "symlink component";', "/* mutant: symlinks allowed */")],
     ["PUSH through a symlinked directory component refused", "PUSH onto a symlink as the last component refused"]),
    ("N03_COMPONENT_LIMIT_255", "the component limit is 255 instead of 245",
     [("if (len > 245) return", "if (len > 255) return")],
     ["PUSH with a 246-byte component refused"]),
    ("N04_DOT_COMPONENT_ALLOWED", "a '.' component is accepted",
     [("""if (len == 1 && path[start] == '.') return "'.' component";""", "/* mutant: dot allowed */")],
     ["PUSH with a '.' component refused"]),
    ("N05_SWEETORIG_NOT_KEPT", "a replaced file is not kept as .sweetorig",
     [("if (replace_regular && !orig_exists) { /* first replacement: keep the original */", "if (0) {")],
     ["replacing an existing file keeps the first original"]),
    ("N06_SWEETORIG_OVERWRITTEN", "a second replacement overwrites the kept original",
     [("if (replace_regular && !orig_exists) { /* first replacement: keep the original */", "if (replace_regular) {")],
     ["replacing an existing file keeps the first original"]),
    ("N07_REPLACE_USES_CLASS_MODE", "a replaced file gets the path-class mode instead of its own (review devd-r2 N1)",
     [("mode_t pmode = push_target_mode(rpath, test_mode, replace_regular, replace_regular ? tst.st_mode : 0);", "mode_t pmode = push_mode(rpath, test_mode);")],
     ["N1: PUSH over a 0755 file in a non-RUN root keeps 0755", "N1: PUSH over a 0600 file in a RUN root keeps 0600"]),
    ("N08_REPLACE_KEEPS_SETUID", "a replaced file keeps setuid/setgid/sticky (kernel caps entries at 0777)",
     [("    return old_mode & 0777;\n}", "    return old_mode & 07777;\n}")],
     ["(d) devd --selftest"]),
    ("N09_REPLACE_CAP_0755", "a replaced file's mode is masked to 0755 instead of 0777 (review devd-r3 F1)",
     [("    return old_mode & 0777;\n}", "    return old_mode & 0755;\n}")],
     ["(d) devd --selftest"]),
    ("N10_SETUID_DROP_SILENT", "a dropped setuid bit is not logged (review devd-r3 F2)",
     [("    if (old_mode & 07000)\n        fprintf(stderr,", "    if (0)\n        fprintf(stderr,")],
     ["N1: PUSH over a 04755 file drops setuid"]),
    ("M14_ACCEPT_FAILURE_EXITS_ZERO", "accept() failure exits 0 instead of non-zero",
     [('devd_log("accept failed %d times in a row; exiting non-zero", accept_fails);\n                close(server_fd);\n                return 1;',
       'devd_log("accept failed %d times in a row; exiting non-zero", accept_fails);\n                close(server_fd);\n                return 0;')],
     ["accept() failure: devd exits non-zero"]),
    ("R05_AUTH_COMPARE_32_HEX", "the AUTH HMAC compare covers only 32 of 64 hex chars (review devd-r1 R05)",
     [("if (!ct_equal(want_hex, line + 5, 64)) {", "if (!ct_equal(want_hex, line + 5, 32)) {")],
     ["correct HMAC, last hex char flipped"]),
    ("R06_RUN_ROOT_PRIVATE_VAR", "the RUN root /private/var/dev/ is widened to /private/var/",
     [('    "/usr/local/",\n    "/private/var/dev/",\n};', '    "/usr/local/",\n    "/private/var/",\n};')],
     ["(d) devd --selftest"]),
    ("R10_AUTH_WINDOW_8500", "the AUTH window is 8.5 s instead of 5 s (review devd-r1 R10)",
     [("#define DEVD_AUTH_TIMEOUT_MS 5000u", "#define DEVD_AUTH_TIMEOUT_MS 8500u")],
     ["(a) silent client: ERR: auth after ~5 s"]),
    ("R12_CLIENT_GONE_KILLS_PID_ONLY", "the client-gone path kills only the child, not its process group",
     [("kill(-pid, SIGKILL);\n        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}\n        return -1;",
       "kill(pid, SIGKILL);\n        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}\n        return -1;")],
     ["deaf client of a flooding RUN: the whole process group dies"]),
    ("K1_KEY_FILE_PUSHABLE", "the loaded key file may be pushed over or pulled (review devd-r1 F12)",
     [("    if (!g_key_known) return 1; /* fail closed */", "    return 0;")],
     ["PUSH over the loaded key file refused", "PULL of the loaded key file refused"]),
    ("E1_RUN_ENV_INHERITED", "the RUN child inherits devd's environment",
     [("        execve(cmd, argv, k_run_env); /* cmd =", "        (void)k_run_env; execv(cmd, argv); /* cmd =")],
     ["RUN child environment is exactly PATH and HOME"]),
    ("E2_RUN_SIGPIPE_IGNORED", "the RUN child inherits SIGPIPE ignored",
     [("        signal(SIGPIPE, SIG_DFL); /* devd ignores SIGPIPE; the program must not inherit that (review devd-r1 F13) */\n", "")],
     ["RUN child has SIGPIPE at its default action"]),
    ("E3_SURVIVORS_NOT_KILLED", "background processes of a finished RUN are left alone",
     [("if (kill(-pid, SIGKILL) == 0)", "if (0)")],
     ["a grandchild that dropped the pipe is killed after a normal RUN exit"]),
    ("A1_ACCEPT_GIVES_UP_AT_1", "devd exits at the first accept() failure",
     [("#define DEVD_ACCEPT_FAIL_MAX 10", "#define DEVD_ACCEPT_FAIL_MAX 1")],
     ["accept() EMFILE: retried once a second"]),
    ("A2_ACCEPT_RETRY_WITHOUT_WAIT", "accept() retries spin without the 1 s pause",
     [("sleep(DEVD_ACCEPT_RETRY_SEC);", "(void)DEVD_ACCEPT_RETRY_SEC;")],
     ["accept() EMFILE: retried once a second"]),
    ("P1_PULL_TRAILING_TOKEN_ACCEPTED", "PULL ignores trailing tokens",
     [('if (sscanf(cmd + 5, "%511s%1s", path, trailer) != 1) {', '(void)trailer;\n            if (sscanf(cmd + 5, "%511s", path) != 1) {')],
     ["PULL with a trailing token refused"]),
]


# devsh mutants (review devd-r1 S01): the same edit rules, killed by the named FAILED checks of
# tools/test_devsh_harness.py run against the mutated copy (DEVSH_MUT_SRC)
DEVSH_SRC = os.path.join(HERE, "devsh")
DEVSH_HARNESS = os.path.join(HERE, "test_devsh_harness.py")
# devsh host-client mutants (the devd.c table above is MUTANTS: not a *_MUTANTS name, no tag needed)
# mutant-block: devsh
DEVSH_MUTANTS = [
    ("S01_RUNPLAN_ANYWHERE", "devsh accepts a run plan in any */docs/runplans/ (review devd-r1 F9)",
     [("and os.path.dirname(real) == os.path.realpath(plans))", 'and os.path.basename(os.path.dirname(real)) == "runplans")')],
     ["decoy <elsewhere>/docs/runplans/t.md is refused"]),
    ("S04_KEY_OWNER_UNCHECKED", "devsh does not check the key file owner (F11)",
     [("if st.st_mode & 0o077 or st.st_uid != os.getuid():", "if st.st_mode & 0o077:")],
     ["key file owned by another uid is refused"]),
    ("S05_KEY_MODE_UNCHECKED", "devsh does not check the key file mode (F11)",
     [("if st.st_mode & 0o077 or st.st_uid != os.getuid():", "if st.st_uid != os.getuid():")],
     ["key file with mode 0644: refused"]),
    ("S07_RUNEND_ID_UNCHECKED", "devsh takes any RUNEND line, not only this run's id (F3)",
     [('r"RUNEND " + run_id + r" RC: (-?\\d+)"', 'r"RUNEND [0-9a-f]{16} RC: (-?\\d+)"')],
     ["fake devd: RUNEND with another id"]),
    ("S08_EMPTY_PUSH_ALLOWED", "devsh pushes an empty file (F1)",
     [("    if size == 0:", "    if size < 0:")],
     ["push of an empty file refused"]),
]


def run_devsh_checks(label: str, source_text: str, workroot: str):
    """Runs test_devsh_harness.py against source_text. Returns (status, failed check names, detail)."""
    d = os.path.join(workroot, label)
    os.makedirs(d)
    src = os.path.join(d, "devsh")
    # the copy lives outside tools/: point its phone-lock lookup at the real one
    fixed = source_text.replace('os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-lock")',
                                repr(os.path.join(HERE, "phone-lock")), 1)
    with open(src, "w") as f:
        f.write(fixed)
    env = dict(os.environ, DEVSH_MUT_SRC=src)
    try:
        p = subprocess.run([sys.executable, DEVSH_HARNESS], env=env, capture_output=True, text=True,
                           timeout=RUN_LIMIT_S, cwd=HERE)
    except subprocess.TimeoutExpired:
        return "DRIVER_TIMEOUT", [], f"test run exceeded {RUN_LIMIT_S} s"
    if "\nRESULT: " not in "\n" + p.stdout:
        return "DRIVER_CRASH", [], (p.stdout + p.stderr).strip()[-400:]
    failed = [ln[4:].rsplit(": FAIL", 1)[0] for ln in p.stdout.splitlines() if ln.startswith("[-] ")]
    return "RAN", failed, ""


def run_devsh_mutant(m, clean: str, workroot: str):
    mid, _desc, edits, killers = m
    mut, err = apply_mutant(clean, edits)
    if err:
        return mid, "ERROR", err
    status, failed, detail = run_devsh_checks(mid, mut, workroot)
    if status != "RAN":
        return mid, "ERROR", f"{status}: {detail}"
    hit = [n for n in failed if any(k in n for k in killers)]
    if hit:
        return mid, "KILLED", f"failed: {hit[0]}"
    if failed:
        return mid, "ERROR", f"checks failed but none of the named killers {killers}: {failed[:3]}"
    return mid, "SURVIVED", "every check passed"


def apply_mutant(src: str, edits):
    """Returns (mutated source, None) or (None, error text). Every anchor must match exactly once."""
    out = src
    for anchor, repl in edits:
        n = out.count(anchor)
        if n != 1:
            return None, f"anchor matches {n} times (need exactly 1): {anchor[:70]!r}"
        out = out.replace(anchor, repl, 1)
    return out, None


def run_checks(label: str, source_text: str, workroot: str):
    """Compile source_text, run the hardening suite on it. Returns (status, report-or-None, detail)."""
    d = os.path.join(workroot, label)
    os.makedirs(d)
    src = os.path.join(d, "devd.c")
    with open(src, "w") as f:
        f.write(source_text)
    c = subprocess.run(GCC + ["-o", os.path.join(d, "devd.chk"), src], capture_output=True, text=True)
    if c.returncode != 0:
        return "COMPILE_ERROR", None, c.stderr.strip()[-300:]
    report = os.path.join(d, "report.json")
    env = dict(os.environ, DEVD_MUT_SRC=src, DEVD_MUT_TMP=os.path.join(d, "w"), DEVD_MUT_REPORT=report)
    # the work dir must be under /tmp/ (devd's test-mode prefix): d is, because workroot is
    try:
        p = subprocess.run([sys.executable, HARDENING], env=env, capture_output=True, text=True,
                           timeout=RUN_LIMIT_S, cwd=HERE)
    except subprocess.TimeoutExpired:
        return "DRIVER_TIMEOUT", None, f"test run exceeded {RUN_LIMIT_S} s"
    if not os.path.exists(report):
        return "DRIVER_CRASH", None, (p.stdout + p.stderr).strip()[-400:]
    with open(report) as f:
        return "RAN", json.load(f), ""


def killers_not_run(rep: dict, killers):
    """Killer names that match no check that ran (passed or failed): a skipped killing check (review devd-r2 N2, AP-341)."""
    ran = rep["passed"] + rep["failed"]
    return [k for k in killers if not any(k in n for n in ran)]


def classify(rep: dict, killers):
    failed = [n for n in rep["failed"] if any(k in n for k in killers)]
    if failed:
        return "KILLED", f"failed: {failed[0]}"
    missing = killers_not_run(rep, killers)
    if missing:
        return "ERROR", f"killing check skipped / never ran (not SURVIVED, not KILLED): {missing}"
    if rep["failed"] or rep["aborted"]:
        return "ERROR", (f"checks failed/aborted but none of the named killers {killers}: "
                         f"failed={rep['failed'][:3]} aborted={rep['aborted'][:2]}")
    return "SURVIVED", "every check passed"


def run_mutant(m, clean_src: str, workroot: str):
    mid, desc, edits, killers = m
    mut_src, err = apply_mutant(clean_src, edits)
    if err:
        return mid, "ERROR", err
    status, rep, detail = run_checks(mid, mut_src, workroot)
    if status != "RAN":
        return mid, "ERROR", f"{status}: {detail}"
    verdict, why = classify(rep, killers)
    return mid, verdict, why


def main() -> int:
    lib.reexec_in_netns(__file__)  # review devd-r2 N2: the phone-mode killers (R02/R03) need 127.0.0.2 on lo
    if os.geteuid() == 0:
        print("RESULT: ERROR refusing to run as root: the allowlist-widening mutants would let the tests write /etc")
        return 2
    t0 = time.time()
    workroot = tempfile.mkdtemp(prefix="devd-mut-", dir="/tmp")
    try:
        with open(DEVD_SRC) as f:
            clean_src = f.read()
        # (1) baseline
        status, rep, detail = run_checks("baseline", clean_src, workroot)
        if status != "RAN":
            print(f"baseline checks=0 passed=0; {status}: {detail}")
            print("RESULT: FAIL baseline (no mutant run)")
            return 2
        total = len(rep["passed"]) + len(rep["failed"])
        print(f"baseline checks={total} passed={len(rep['passed'])} skipped={len(rep['skipped'])} "
              f"aborted={len(rep['aborted'])}")
        if rep["failed"] or rep["aborted"] or total == 0:
            print(f"baseline failing: {rep['failed'] + rep['aborted']}")
            print("RESULT: FAIL baseline (no mutant run)")
            return 2
        # AP-341: every killing check a mutant lists must have RUN in the baseline (a skipped killer would turn a
        # live mutant into SURVIVED or hide it); refuse to start instead.
        unrun = sorted({k for m in MUTANTS for k in killers_not_run(rep, m[3])})
        if unrun:
            print(f"baseline: killing checks skipped / never ran: {unrun} (skipped groups: {rep['skipped']})")
            print("RESULT: FAIL baseline (killing check skipped, no mutant run)")
            return 2
        # (2) mutants (DEVD_MUT_ONLY=ID,ID runs a subset while developing; the RESULT line then says so)
        only = [x for x in os.environ.get("DEVD_MUT_ONLY", "").split(",") if x]
        mutants = [m for m in MUTANTS if not only or any(m[0].startswith(o) for o in only)]
        if only and not mutants and not [m for m in DEVSH_MUTANTS if any(m[0].startswith(o) for o in only)]:
            print(f"RESULT: ERROR DEVD_MUT_ONLY={only} matches no mutant")
            return 2
        results = []
        with open(DEVSH_SRC) as f:
            clean_devsh = f.read()
        status, failed, detail = run_devsh_checks("devsh-baseline", clean_devsh, workroot)
        if status != "RAN" or failed:
            print(f"devsh baseline failing: {status} {failed or detail}")
            print("RESULT: FAIL baseline (devsh harness, no mutant run)")
            return 2
        dmutants = [m for m in DEVSH_MUTANTS if not only or any(m[0].startswith(o) for o in only)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(run_mutant, m, clean_src, workroot) for m in mutants]
            futs += [pool.submit(run_devsh_mutant, m, clean_devsh, workroot) for m in dmutants]
            for fut in futs:
                try:
                    res = fut.result()
                except Exception as e:  # a crash of this driver: an ERROR, never a kill
                    res = ("?", "ERROR", f"driver exception {e!r}")
                results.append(res)
        for mid, verdict, why in results:
            print(f"[{verdict:8s}] {mid}: {why}")
        killed = sum(1 for r in results if r[1] == "KILLED")
        survived = [r for r in results if r[1] == "SURVIVED"]
        errors = [r for r in results if r[1] == "ERROR"]
        print(f"baseline checks={total} passed={len(rep['passed'])}; mutants={len(mutants) + len(dmutants)} "
              f"killed={killed} survived={len(survived)} errors={len(errors)}")
        for r in survived:
            print(f"  SURVIVED {r[0]}")
        for r in errors:
            print(f"  ERROR    {r[0]}")
        ok = not survived and not errors
        print(f"RESULT: {'PASS' if ok else 'FAIL'} mutants={len(mutants) + len(dmutants)} killed={killed} "
              f"survived={len(survived)} errors={len(errors)} in {time.time() - t0:.0f}s"
              + (f" (SUBSET of {len(MUTANTS) + len(DEVSH_MUTANTS)}: DEVD_MUT_ONLY={','.join(only)})" if only else ""))
        return 0 if ok else 1
    finally:
        shutil.rmtree(workroot, ignore_errors=True)


# T-c24f: `unittest discover` (tools/gate-unit-shards) runs this script through lib.script_case: exit 0 + last `RESULT: PASS`
ScriptRun = lib.script_case(__file__, 2400)


if __name__ == "__main__":
    sys.exit(main())
