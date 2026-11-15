#!/usr/bin/env python3
"""Compile and run the REAL C of the USB root disk patch against stub kernel headers (host test, no phone).

    tools/test_usbroot_c.py [--patch FILE] [--keep]

Extracts bsd/dev/sweet_usbroot.c from the '+' lines of the patch (default: xnu/patches/*-sweet-usbroot-ro.patch), copies
tools/test_usbroot_c.c next to it (AP-86: only the copy in the work dir is included), generates stub headers and builds with
gcc -fsanitize=address,undefined. --patch lets a mutated copy of the patch be tested (a mutant must make this FAIL).
Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, subprocess, sys, tempfile, shutil, unittest

HERE = pathlib.Path(__file__).resolve().parent

STUB = r'''
#ifndef KSTUB_H
#define KSTUB_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include <limits.h>
#include <sys/types.h>
#include <sys/sysmacros.h>
#define __unused __attribute__((unused))
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define DEV_BSIZE 512
#define NSEC_PER_MSEC 1000000ull
#define B_WRITE 0x0
#define B_READ 0x1
#define FWRITE 0x2
#define THREAD_UNINT 0
#define THREAD_INTERRUPTIBLE 1
#define THREAD_ABORTSAFE 2
#define THREAD_CONTINUE_NULL NULL
#define UID_ROOT 0
#define GID_OPERATOR 5
#define DEVFS_CHAR 0
#define DEVFS_BLOCK 1
#define DKIOCGETBLOCKSIZE 1
#define DKIOCSETBLOCKSIZE 2
#define DKIOCGETPHYSICALBLOCKSIZE 3
#define DKIOCGETBLOCKCOUNT 4
#define DKIOCISWRITABLE 5
#define DKIOCSYNCHRONIZE 6
#define DKIOCSYNCHRONIZECACHE 7
#define DKIOCGETMAXBLOCKCOUNTREAD 8
#define DKIOCGETMAXBLOCKCOUNTWRITE 9
#define DKIOCGETMAXSEGMENTCOUNTREAD 10
#define DKIOCGETMAXSEGMENTCOUNTWRITE 11
#define LCK_GRP_DECLARE(n, s) int n
#define LCK_MTX_DECLARE(n, g) int n
#define LCK_SPIN_DECLARE(n, g) int n
typedef int64_t daddr64_t;
struct proc; struct uio;
struct buf { int flags; daddr64_t blkno; uint32_t count, resid; int error; int done; caddr_t data; };
typedef struct buf *buf_t;
struct bdevsw { void *d_open, *d_close, *d_strategy, *d_ioctl, *d_dump, *d_psize; int d_type; };
struct cdevsw { void *d_open, *d_close, *d_read, *d_write, *d_ioctl, *d_stop, *d_reset, *d_ttys, *d_select, *d_mmap,
    *d_strategy, *d_reserved_1, *d_reserved_2; int d_type; };
#define D_DISK 1
#define Z_WAITOK_ZERO 0
extern int stub_kalloc_fail, stub_kalloc_calls;
/* stub_kalloc_fail: 1 = every call fails, 2 = only the second call fails */
static inline void *kalloc_data(size_t n, int f) { (void)f; stub_kalloc_calls++; return (stub_kalloc_fail == 1 || (stub_kalloc_fail == 2 && stub_kalloc_calls == 2)) ? NULL : calloc(1, n); }
#define kfree_data(p, n) free(p)
extern int stub_lock_depth;
static inline void lck_mtx_lock(int *l) { (void)l; stub_lock_depth++; }
static inline void lck_mtx_unlock(int *l) { (void)l; stub_lock_depth--; }
extern int stub_spin_depth;      /* ur_spin only (stub_lock_depth counts both locks) */
static inline void lck_spin_lock(int *l) { (void)l; stub_lock_depth++; stub_spin_depth++; }
static inline void lck_spin_unlock(int *l) { (void)l; stub_lock_depth--; stub_spin_depth--; }
/* zlib-compatible, like bsd/libkern/crc32.c:93-105 (init and final xor ~0, chainable) */
static inline uint32_t crc32(uint32_t crc, const void *buf, size_t len)
{
	const uint8_t *p = buf;
	crc = ~crc;
	while (len--) {
		crc ^= *p++;
		for (int k = 0; k < 8; k++) {
			crc = (crc >> 1) ^ (0xedb88320u & -(crc & 1));
		}
	}
	return ~crc;
}
/* the fake clock (ns) stays at stub_now; only the fake fast-receive sleep (0172) moves it, to its deadline on a timeout */
extern uint64_t stub_now;
static inline void clock_interval_to_deadline(unsigned a, uint64_t s, uint64_t *d) { *d = stub_now + a * s; }
static inline void clock_get_uptime(uint64_t *t) { *t = stub_now; }
static inline void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t; }
static inline int assert_wait_deadline(void *e, int m, uint64_t d) { (void)e; (void)m; (void)d; return 0; }
int thread_block(void *c);
/* patch 0172 (fast receive) */
#define NSEC_PER_USEC 1000ull
#define LCK_SLEEP_DEFAULT 0
#define THREAD_AWAKENED 0
#define THREAD_TIMED_OUT 1
int lck_spin_sleep_deadline(int *l, int action, void *e, int m, uint64_t deadline);
void thread_wakeup(void *e);
bool PE_parse_boot_argn(const char *name, void *p, size_t sz);
typedef int boolean_t;
boolean_t PE_parse_boot_arg_str(const char *arg_string, char *arg_ptr, int size);   /* pexpert/pexpert/pexpert.h:343 */
static inline void buf_setresid(buf_t b, uint32_t r) { b->resid = r; }
static inline int buf_flags(buf_t b) { return b->flags; }
static inline daddr64_t buf_blkno(buf_t b) { return b->blkno; }
static inline uint32_t buf_count(buf_t b) { return b->count; }
static inline int buf_map(buf_t b, caddr_t *v) { *v = b->data; return 0; }
static inline int buf_unmap(buf_t b) { (void)b; return 0; }
static inline void buf_seterror(buf_t b, int e) { b->error = e; }
static inline int buf_biodone(buf_t b) { b->done = 1; return 0; }
static inline int bdevsw_add(int m, const struct bdevsw *s) { (void)m; (void)s; return 1; }
static inline int cdevsw_add_with_bdev(int m, const struct cdevsw *s, int b) { (void)m; (void)s; (void)b; return 2; }
static inline int devfs_make_node(dev_t d, int t, int u, int g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return 0; }
static inline int eno_dump(void) { return ENODEV; }
static inline int eno_stop(void) { return ENODEV; }
static inline int eno_reset(void) { return ENODEV; }
static inline int eno_select(void) { return ENODEV; }
static inline int eno_mmap(void) { return ENODEV; }
static inline int eno_strat(void) { return ENODEV; }
static inline int eno_getc(void) { return ENODEV; }
static inline int eno_putc(void) { return ENODEV; }
static inline void minphys(void) {}
static inline int physio(void (*s)(buf_t), buf_t b, dev_t d, int rw, void *m, struct uio *u, int bs) { (void)s; (void)b; (void)d; (void)rw; (void)m; (void)u; (void)bs; return 0; }
#endif
'''
HEADERS = ["sys/param.h", "sys/systm.h", "sys/buf.h", "sys/conf.h", "sys/disk.h", "sys/errno.h", "sys/proc.h", "sys/uio.h",
           "miscfs/devfs/devfs.h", "kern/locks.h", "kern/clock.h", "kern/thread.h", "kern/sched_prim.h", "kern/kalloc.h",
           "pexpert/pexpert.h", "libkern/libkern.h"]


def extract(patch_text):
    out, on = [], False
    for line in patch_text.split("\n"):
        if line.startswith("+++ "):
            on = line.startswith("+++ b/bsd/dev/sweet_usbroot.c")
            continue
        if on and line.startswith("+"):
            out.append(line[1:])
    return "\n".join(out) + "\n"


def only_file(patch_text, path):
    """The section of a multi-file patch that changes path (from its 'diff --git' or '--- a/' line to the next file)."""
    out, on = [], False
    for line in patch_text.split("\n"):
        if line.startswith("diff --git ") or (line.startswith("--- ") and not on):
            on = line.startswith("diff --git a/%s " % path) or line == "--- a/%s" % path
        if on:
            out.append(line)
    return "\n".join(out) + "\n"


def main(argv):
    patch, keep, i = None, False, 1
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]); i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_usbroot_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    if patch is None:
        found = sorted((HERE.parent / "xnu" / "patches").glob("*-sweet-usbroot-ro.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-usbroot-ro.patch"); return 2
        patch = found[0]
    if not shutil.which("gcc"):
        print("RESULT: ERROR gcc not found"); return 2
    src = extract(patch.read_text())
    if "sweet_usbroot_rx" not in src:
        print("RESULT: ERROR no sweet_usbroot.c in %s" % patch); return 2
    work = pathlib.Path(tempfile.mkdtemp(prefix="usbroot-c-"))
    try:
        (work / "sweet_usbroot.c").write_text(src)
        # later patches that change the driver (0158 RAM overlay) apply in name order on top of the extracted file
        drv = work / "bsd" / "dev" / "sweet_usbroot.c"
        drv.parent.mkdir(parents=True)
        drv.write_text(src)
        # 0172 (fast receive) also changes bsd/dev/sweet_usb.c: only its sweet_usbroot.c section is applied here
        later = sorted(patch.parent.glob("*-sweet-usbroot-cow.patch")) + sorted(patch.parent.glob("*-sweet-usbroot-rxfast.patch"))
        for ep in later:
            part = work / (ep.name + ".usbroot")
            part.write_text(only_file(ep.read_text(), "bsd/dev/sweet_usbroot.c"))
            r = subprocess.run(["patch", "-p1", "-s", "-d", str(work), "-i", str(part), "--no-backup-if-mismatch"],
                               capture_output=True, text=True)
            if r.returncode:
                print(r.stdout[-800:], r.stderr[-800:]); print("RESULT: ERROR %s does not apply" % ep.name); return 2
        shutil.move(str(drv), str(work / "sweet_usbroot.c"))
        (work / "kstub.h").write_text(STUB)
        shutil.copy(HERE / "test_usbroot_c.c", work / "test_usbroot_c.c")
        for h in HEADERS:
            p = work / "inc" / h
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('#include "kstub.h"\n')
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Wno-format", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-DARM64_BOARD_CONFIG_SWEET", "-iquote", str(work), "-I", str(work / "inc"), "-I", str(work),
              str(work / "test_usbroot_c.c"), "-o", str(exe)]
        r = subprocess.run(cc, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-1500:]); print("RESULT: ERROR compile failed"); return 2
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        sys.stdout.write(r.stdout[-3000:])
        if r.returncode and "RESULT:" not in r.stdout:
            sys.stdout.write(r.stderr[-1500:]); print("RESULT: FAIL sanitizer or crash (exit %d)" % r.returncode)
        return 1 if r.returncode else 0
    finally:
        if keep:
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


class UsbrootC(unittest.TestCase):   # gate-unit-shards runs `unittest discover`: the real C test as one case
    def test_real_c(self):
        self.assertEqual(main(["test_usbroot_c.py"]), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
