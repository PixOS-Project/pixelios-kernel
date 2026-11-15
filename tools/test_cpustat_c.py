#!/usr/bin/env python3
"""Compile and run the REAL C of bsd/dev/sweet_cpustat.c (patch 0121 + 0175) against stub proc/task calls (host test, no phone).
AP-173: pid 1 and watched processes must print outside the CS_MAX_LINES cap, dropped rows must be counted loudly.

    tools/test_cpustat_c.py [--patch FILE] [--keep]

Builds the file with `git apply --include` of 0121 then 0175 (--patch replaces 0175, so a mutant can be tested; a mutant
must make this FAIL), strips the #include lines, and includes it into a C harness with a fake process list.
Prints RESULT; exit 0 PASS, 1 FAIL, 2 ERROR.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCHES = HERE.parent / "xnu" / "patches"
SRC = "bsd/dev/sweet_cpustat.c"

HARNESS = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define uint64_t unsigned long long   /* XNU's uint64_t is unsigned long long; makes -Werror=format check the %llu args (r1 F4) */
#define __unused __attribute__((unused))
#define MAXCOMLEN 16
#define NSEC_PER_MSEC 1000000ULL
#define NSEC_PER_USEC 1000ULL
#define PROC_RETURNED 0
#define PROC_ALLPROCLIST 1
#define PZERO 22
#define KERN_SUCCESS 0
#define TASK_NULL ((task_t)0)
#define TASK_ABSOLUTETIME_INFO 1
#define TASK_ABSOLUTETIME_INFO_COUNT 4
typedef struct fproc { int pid; const char *name; uint64_t u_ms, s_ms; int notask; } *proc_t;
typedef void *task_t;
typedef int kern_return_t, task_flavor_t, wait_result_t;
typedef unsigned mach_msg_type_number_t;
typedef int *task_info_t;
typedef void (*thread_continue_t)(void *, wait_result_t);
typedef void *thread_t;
typedef struct { uint64_t total_user, total_system, threads_user, threads_system; } task_absolutetime_info_data_t;
static char outbuf[1 << 20];
static size_t outlen;
#define printf(...) (outlen += (size_t)snprintf(outbuf + outlen, sizeof(outbuf) - outlen, __VA_ARGS__))
static void absolutetime_to_nanoseconds(uint64_t abs, uint64_t *ns) { *ns = abs; }   /* 1 tick = 1 ns in the harness */
static uint64_t mach_absolute_time(void) { return 0; }
static int hz = 100;
static uint64_t sweet_ufs_stat[6];
static int PE_parse_boot_argn(const char *n, void *v, int l) { (void)n; (void)v; (void)l; return 0; }
static int tsleep(void *c, int p, const char *w, int t) { (void)c; (void)p; (void)w; (void)t; return 0; }
static kern_return_t kernel_thread_start(thread_continue_t f, void *a, thread_t *t) { (void)f; (void)a; (void)t; return 1; }
static void thread_deallocate(thread_t t) { (void)t; }
static const char *proc_best_name(proc_t p) { return p->name; }
static int proc_pid(proc_t p) { return p->pid; }
static task_t proc_task(proc_t p) { return p->notask ? TASK_NULL : (task_t)p; }
static kern_return_t task_info(task_t t, task_flavor_t f, task_info_t i, mach_msg_type_number_t *c) {
	(void)f; (void)c; proc_t p = (proc_t)t; task_absolutetime_info_data_t *ti = (void *)i;
	ti->total_user = p->u_ms * NSEC_PER_MSEC; ti->total_system = p->s_ms * NSEC_PER_MSEC; return KERN_SUCCESS; }
static struct fproc *procs; static int nprocs;
static void proc_iterate(int w, int (*cb)(proc_t, void *), void *a, void *f, void *fa) {
	(void)w; (void)f; (void)fa; for (int i = 0; i < nprocs; i++) cb(&procs[i], a); }
#define extern static
#include "cpustat.inc"
#undef extern

static int fails;
static void want(const char *s, int yes) {
	if ((strstr(outbuf, s) != NULL) != yes) { fprintf(stdout, "FAIL: %s '%s'\n", yes ? "missing" : "unexpected", s); fails++; } }
static int count(const char *s) { int n = 0; for (char *p = outbuf; (p = strstr(p, s)); p++) n++; return n; }
static void reset_out(void) { outlen = 0; outbuf[0] = 0; }

int main(void) {
	/* B-A6 boot2 shape: 276 processes, newest pid first, all >= 100 ms; launchd (1) and mediaserverd (28) come last */
	static struct fproc big[276];
	static char names[276][16];
	for (int i = 0; i < 276; i++) {
		int pid = 400 - i;
		snprintf(names[i], sizeof(names[i]), "d%d", pid);
		big[i] = (struct fproc){ pid, names[i], 150, 50, 0 };
	}
	big[274] = (struct fproc){ 28, "mediaserverd", 1000, 25000, 0 };
	big[275] = (struct fproc){ 1, "launchd", 900, 28000, 0 };
	char w1[] = "com.apple.migrationpluginwrapper,com.apple.datamigrator,launchd,mediaserverd";
	cs_parse_watch(w1);
	if (cs_watch_n != 4) { fprintf(stdout, "FAIL: parsed %d names, want 4\n", cs_watch_n); fails++; }
	want("SWEET GAP", 0);
	want("CPUSTAT watching 4 names from \"com.apple.migrationpluginwrapper,com.apple.datamigrator,launchd,mediaserverd\" (max 8)\n", 1);
	procs = big; nprocs = 276;
	reset_out();
	cs_sample(true, 1000);
	want("CPUSTAT p 1 launchd user 900 sys 28000\n", 1);
	want("CPUSTAT p 28 mediaserverd user 1000 sys 25000\n", 1);
	want("CPUSTAT truncated 74 rows (cap 200; watched rows not capped)\n", 1);
	want("CPUSTAT total procs 276 ", 1);
	want("CPUSTAT skipped", 0);
	want("CPUSTAT watch launchd live 1 at 1000 ms\n", 1);
	want("CPUSTAT watch mediaserverd live 1 at 1000 ms\n", 1);
	if (count("CPUSTAT p ") != 202) { fprintf(stdout, "FAIL: %d CPUSTAT p rows, want 202\n", count("CPUSTAT p ")); fails++; }
	/* short tick: no table, no repeated watch line */
	reset_out();
	cs_sample(false, 6000);
	want("CPUSTAT p ", 0); want("CPUSTAT watch", 0); want("truncated", 0);
	/* small list: idle watched process still printed, pid 1 printed under 100 ms, no truncated line, task_info failure counted */
	struct fproc small[] = { { 50, "idle", 10, 10, 0 }, { 28, "mediaserverd", 1, 2, 0 }, { 7, "gone", 0, 0, 1 }, { 1, "launchd", 3, 4, 0 },
	                         { 29, "mediaserverd", 0, 0, 1 } };
	procs = small; nprocs = 5;
	reset_out();
	cs_sample(true, 7000);
	want("CPUSTAT p 28 mediaserverd user 1 sys 2\n", 1);
	want("CPUSTAT p 1 launchd user 3 sys 4\n", 1);
	want("CPUSTAT p 50 ", 0);
	want("truncated", 0);
	want("CPUSTAT skipped 2 procs", 1);
	want("CPUSTAT p 29 mediaserverd task_info failed\n", 1);
	want("CPUSTAT p 7 ", 0);
	want("CPUSTAT total procs 3 ", 1);
	/* pid 1 is printed even when its name is not in the watch list */
	cs_watch_n = 0;
	char w3[] = "mediaserverd";
	cs_parse_watch(w3);
	struct fproc renamed[] = { { 1, "init", 5, 6, 0 } };
	procs = renamed; nprocs = 1;
	reset_out();
	cs_sample(true, 8000);
	want("CPUSTAT p 1 init user 5 sys 6\n", 1);
	/* parse: empty, too long and over-limit names are loud GAP lines */
	reset_out();
	cs_watch_n = 0;
	char w2[] = "a,,b,0123456789012345678901234567890123456789,c,d,e,f,g,h,i";
	cs_parse_watch(w2);
	if (cs_watch_n != 8) { fprintf(stdout, "FAIL: parsed %d names, want 8\n", cs_watch_n); fails++; }
	want("SWEET GAP: CPUSTAT sweetcpuwatch name 1 (len 0) not watched (bad length", 1);
	want("SWEET GAP: CPUSTAT sweetcpuwatch name 2 (len 40) not watched (bad length", 1);
	want("SWEET GAP: CPUSTAT sweetcpuwatch name 8 (len 1) not watched (list full", 1);
	if (count("SWEET GAP") != 3) { fprintf(stdout, "FAIL: %d GAP lines, want 3\n", count("SWEET GAP")); fails++; }
	want("CPUSTAT watching 8 names from", 1);
	/* trailing comma: no GAP, the watching line shows the list; empty list: GAP */
	reset_out(); cs_watch_n = 0;
	char w4[] = "a,b,";
	cs_parse_watch(w4);
	want("CPUSTAT watching 2 names from \"a,b,\" (max 8)\n", 1);
	want("SWEET GAP", 0);
	reset_out(); cs_watch_n = 0;
	char w5[] = "";
	cs_parse_watch(w5);
	want("SWEET GAP: CPUSTAT sweetcpuwatch \"\" gives no name", 1);
	want("CPUSTAT watching", 0);
	if (fails) { fprintf(stdout, "%s", outbuf); fprintf(stdout, "RESULT: FAIL cpustat %d check(s)\n", fails); return 1; }
	fprintf(stdout, "RESULT: PASS cpustat cap, watched rows, truncated/skipped/GAP lines\n");
	return 0;
}
'''


def build_src(patch, work):
    base = sorted(PATCHES.glob("0121-sweet-cpustat.patch"))
    if len(base) != 1:
        return None
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    for p in (base[0], patch):
        r = subprocess.run(["git", "-C", str(work), "apply", "--include=" + SRC, str(p)], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-800:])
            return None
    src = (work / SRC).read_text()
    return re.sub(r"^#include .*$", "", src, flags=re.M)


def main(argv):
    patch, keep, i = None, False, 1
    while i < len(argv):
        if argv[i] == "--patch" and i + 1 < len(argv):
            patch = pathlib.Path(argv[i + 1]).resolve(); i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        else:
            print("usage: test_cpustat_c.py [--patch FILE] [--keep]\nRESULT: ERROR bad arguments"); return 2
    if patch is None:
        found = sorted(PATCHES.glob("*-sweet-cpustat-cap.patch"))
        if len(found) != 1:
            print("RESULT: ERROR need exactly one *-sweet-cpustat-cap.patch"); return 2
        patch = found[0]
    if not shutil.which("gcc"):
        print("RESULT: ERROR gcc not found"); return 2
    work = pathlib.Path(tempfile.mkdtemp(prefix="cpustat-c-"))
    try:
        src = build_src(patch, work / "tree")
        if src is None or "cs_sample" not in src:
            print("RESULT: ERROR %s does not build sweet_cpustat.c with cs_sample on top of 0121" % patch); return 2
        (work / "cpustat.inc").write_text(src)
        (work / "t.c").write_text(HARNESS)
        exe = work / "t"
        cc = ["gcc", "-std=gnu11", "-g", "-O1", "-Wall", "-Werror=format", "-Wno-unused-function", "-fsanitize=address,undefined",
              "-fno-sanitize-recover=all", "-iquote", str(work), str(work / "t.c"), "-o", str(exe)]
        r = subprocess.run(cc, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-1500:]); print("RESULT: ERROR compile failed"); return 2
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        sys.stdout.write(r.stdout[-3000:])
        if r.returncode and "RESULT:" not in r.stdout:
            sys.stdout.write(r.stderr[-1500:]); print("RESULT: FAIL sanitizer or crash (exit %d)" % r.returncode)
        return 1 if r.returncode else 0
    finally:
        if keep:
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


class CpustatC(unittest.TestCase):   # gate-unit-shards runs `unittest discover`: the real C test as one case
    def test_real_c(self):
        self.assertEqual(main(["test_cpustat_c.py"]), 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
