#!/usr/bin/env python3
"""Compile and run the REAL C of patch 0189 (bsd/dev/sweet_bt.c, BT-S3 f) against stub kernel headers (host test, no phone).

    tools/test_sweet_bt_c.py [--uart] [--patch FILE] [--keep]

--uart runs tools/test_sweet_bt_uart.c (BT-S4a backend, RESULT: PASS bt-s4a-uart-host) on the same extracted files.

Extracts bsd/dev/sweet_bt.c from the '+' lines of the patch (default: xnu/patches/*-sweet-bt-transport.patch), copies tools/test_sweet_bt_c.c next to it,
generates stub headers and builds with gcc -fsanitize=address,undefined. --patch lets a mutated copy be tested (a mutant must make this FAIL).
Also checks the other hunks of the patch by text: the ctl_ioctl hook in kern_control.c, the files entry, both init call sites.
Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent

STUB = r'''
#ifndef KSTUB_H
#define KSTUB_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/sysmacros.h>
#define printf sbt_printf
int sbt_printf(const char *fmt, ...);
#define strlcpy(d, s, n) (snprintf((d), (n), "%s", (s)))
typedef int errno_t;
typedef void *kern_ctl_ref;
#define MAX_KCTL_NAME 96
struct sockaddr_ctl { u_int32_t sc_unit; };
typedef errno_t (*ctl_connect_func)(kern_ctl_ref, struct sockaddr_ctl *, void **);
typedef errno_t (*ctl_disconnect_func)(kern_ctl_ref, u_int32_t, void *);
typedef struct mbuf *mbuf_t;
typedef errno_t (*ctl_send_func)(kern_ctl_ref, u_int32_t, void *, mbuf_t, int);
typedef errno_t (*ctl_setopt_func)(kern_ctl_ref, u_int32_t, void *, int, void *, size_t);
typedef errno_t (*ctl_getopt_func)(kern_ctl_ref, u_int32_t, void *, int, void *, size_t *);
struct kern_ctl_reg {
	char ctl_name[MAX_KCTL_NAME]; u_int32_t ctl_id, ctl_unit, ctl_flags, ctl_sendsize, ctl_recvsize;
	ctl_connect_func ctl_connect; ctl_disconnect_func ctl_disconnect; ctl_send_func ctl_send; ctl_setopt_func ctl_setopt; ctl_getopt_func ctl_getopt;
};
errno_t ctl_register(struct kern_ctl_reg *, kern_ctl_ref *);
errno_t ctl_enqueuedata(kern_ctl_ref, u_int32_t, void *, size_t, u_int32_t);
struct mbuf { const void *data; size_t len; struct mbuf *next; };
static inline void *mbuf_data(mbuf_t m) { return (void *)m->data; }
static inline size_t mbuf_len(mbuf_t m) { return m->len; }
static inline mbuf_t mbuf_next(mbuf_t m) { return m->next; }
void mbuf_freem(mbuf_t m);
struct proc;
#define UID_ROOT 0
#define GID_WHEEL 0
#define DEVFS_CHAR 0
#define DEVFS_BLOCK 1
struct cdevsw { void *d_open, *d_close, *d_read, *d_write, *d_ioctl, *d_stop, *d_reset, *d_ttys, *d_select, *d_mmap, *d_strategy,
    *d_reserved_1, *d_reserved_2; int d_type; };
#define eno_rdwrt NULL
#define eno_ioctl NULL
#define eno_stop NULL
#define eno_reset NULL
#define eno_select NULL
#define eno_mmap NULL
#define eno_strat NULL
#define eno_getc NULL
#define eno_putc NULL
int cdevsw_add(int, const struct cdevsw *);
void *devfs_make_node(dev_t, int, uid_t, gid_t, int, const char *, ...);
typedef void *DTEntry;
#define kSuccess 0
int SecureDTLookupEntry(void *, const char *, DTEntry *);
/* Darwin <sys/ioccom.h> / <sys/termios.h> (struct termios is 0x48 bytes there: 4 x tcflag_t(8) + cc_t[20] + 2 x speed_t(8)) */
typedef unsigned long tcflag_t; typedef unsigned char cc_t; typedef unsigned long speed_t;
struct termios { tcflag_t c_iflag, c_oflag, c_cflag, c_lflag; cc_t c_cc[20]; speed_t c_ispeed, c_ospeed; };
#define IOC_OUT 0x40000000UL
#define IOC_IN 0x80000000UL
#define _IOC(d, g, n, t) ((d) | (((unsigned long)sizeof(t) & 0x1fff) << 16) | ((g) << 8) | (n))
#define _IOR(g, n, t) _IOC(IOC_OUT, (g), (n), t)
#define _IOW(g, n, t) _IOC(IOC_IN, (g), (n), t)
#define TIOCGETA _IOR('t', 19, struct termios)
#define TIOCSETA _IOW('t', 20, struct termios)
#define TIOCMGET _IOR('t', 106, int)
#define TIOCM_CTS 0040
#define CSIZE 0x300UL
#define CS7 0x200UL
#define CS8 0x300UL
#define CSTOPB 0x400UL
#define PARENB 0x1000UL
#define CREAD 0x800UL
#define CLOCAL 0x8000UL
#define CCTS_OFLOW 0x10000UL
#define CRTS_IFLOW 0x20000UL
typedef int kern_return_t; typedef int wait_result_t; typedef void *thread_t; typedef void *thread_continue_t; typedef unsigned long vm_offset_t, vm_size_t;
#define KERN_SUCCESS 0
#define PZERO 22
#define PE_PARSE_UNUSED
int PE_parse_boot_argn(const char *, void *, size_t);
int kernel_thread_start(void *, void *, void **);
void thread_deallocate(void *);
int tsleep(void *, int, const char *, int);
void delay(int);
#endif
'''
HEADERS = ["sys/param.h", "sys/systm.h", "sys/errno.h", "sys/conf.h", "sys/termios.h", "sys/ioctl.h", "sys/kern_control.h",
           "sys/kpi_mbuf.h", "miscfs/devfs/devfs.h", "pexpert/pexpert.h", "pexpert/device_tree.h", "sys/ttycom.h", "kern/clock.h"]


def sections(text):
    """{path: [lines of that file's section]} of a multi-file git patch."""
    out, cur = {}, None
    for line in text.split("\n"):
        m = re.match(r"diff --git a/(\S+) b/", line)
        if m:
            cur = out.setdefault(m.group(1), [])
        if cur is not None:
            cur.append(line)
    return out


def added(lines):
    return "\n".join(l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")) + "\n"


def static_checks(secs):
    """The hunks outside sweet_bt.c, by text; returns a list of problems."""
    bad = []
    kc = "\n".join(secs.get("bsd/kern/kern_control.c", []))
    if not re.search(r"^\+\s*default:.*?^\+[^\n]*sweet_bt_kctl_ioctl\(", kc, re.M | re.S):
        bad.append("kern_control.c: no default case calling sweet_bt_kctl_ioctl")
    if not re.search(r"^\+bsd/dev/sweet_bt_uart\.c\s+standard", "\n".join(secs.get("bsd/conf/files", [])), re.M):
        bad.append("bsd/conf/files: no sweet_bt_uart.c entry")
    if not re.search(r"^\+bsd/dev/sweet_bt\.c\s+standard", "\n".join(secs.get("bsd/conf/files", [])), re.M):
        bad.append("bsd/conf/files: no sweet_bt.c entry")
    for f in ("bsd/dev/sweet_ufs.c", "bsd/dev/sweet_usbroot.c"):
        if not re.search(r"^\+\s*sweet_bt_init\(\);", "\n".join(secs.get(f, [])), re.M):
            bad.append("%s: no sweet_bt_init() call" % f)
    return bad


def main(argv):
    patch, keep, i, csrc = None, False, 1, "test_sweet_bt_c.c"
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]); i += 2
        elif argv[i] == "--uart":      # the BT-S4a backend test (tools/test_sweet_bt_uart.c) instead of the transport test
            csrc = "test_sweet_bt_uart.c"; i += 1
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_sweet_bt_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    if patch is None:
        found = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-bt-transport.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-bt-transport.patch"); return 2
        patch = found[0]
    if not shutil.which("gcc"):
        print("RESULT: ERROR gcc not found"); return 2
    secs = sections(patch.read_text())
    src = added(secs.get("bsd/dev/sweet_bt.c", []))
    if "sweet_bt_init" not in src:
        print("RESULT: ERROR no bsd/dev/sweet_bt.c in %s" % patch); return 2
    bad = static_checks(secs)
    if bad:
        print("\n".join(bad)); print("RESULT: FAIL bt-transport-patch hunks missing"); return 1
    work = pathlib.Path(tempfile.mkdtemp(prefix="sweet-bt-c-"))
    try:
        (work / "sweet_bt.c").write_text(src)
        (work / "sweet_bt.h").write_text(added(secs.get("bsd/dev/sweet_bt.h", [])))
        (work / "sweet_bt_uart.c").write_text(added(secs.get("bsd/dev/sweet_bt_uart.c", [])))
        (work / "kstub.h").write_text(STUB)
        shutil.copy(HERE / "test_sweet_bt_c.c", work / "test_sweet_bt_c.c")
        shutil.copy(HERE / "test_sweet_bt_uart.c", work / "test_sweet_bt_uart.c")
        for h in HEADERS:
            p = work / "inc" / h
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('#include "kstub.h"\n')
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Wextra", "-Wno-format", "-Wno-unused-parameter", "-Wno-pragmas", "-Wno-unknown-pragmas",
              "-Wno-incompatible-pointer-types", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-iquote", str(work), "-I", str(work / "inc"), "-I", str(work), str(work / csrc), "-o", str(exe)]
        r = subprocess.run(cc, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-2500:]); print("RESULT: ERROR compile failed"); return 2
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        sys.stdout.write(r.stdout[-3000:])
        if r.returncode and r.stderr:
            sys.stdout.write(r.stderr[:3500])
        if r.returncode and "RESULT:" not in r.stdout:
            sys.stdout.write(r.stderr[-1500:]); print("RESULT: FAIL sanitizer or crash (exit %d)" % r.returncode)
        return 1 if r.returncode else 0
    finally:
        if keep:
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


class TestSweetBt(unittest.TestCase):
    """The gate's unit step runs unittest on every tools/test_*.py (a script with no TestCase is 'NO TESTS RAN', rc 5): same runs as the script."""

    def test_transport_host(self):
        self.assertEqual(main(["x"]), 0, "RESULT: FAIL bt-transport-host-test")

    def test_uart_backend_host(self):
        self.assertEqual(main(["x", "--uart"]), 0, "RESULT: FAIL bt-s4a-uart-host")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "-v":   # script use: --uart / --patch P / --keep (the mutant suite and the T-e930 recipes)
        sys.exit(main(sys.argv))
    unittest.main()
