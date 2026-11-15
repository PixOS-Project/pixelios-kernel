/*
 * Host model for bsd/dev/sweet_cputop.c (xnu patch 0341, SWEET CPUTOP). Built by tools/test_cputop.py:
 *   gcc -Wall -Wextra -Werror -DSWEET_CPUTOP_HOST -DCPUTOP_SRC="<file as patched>" [-DCT_CAP=N] tools/cputop_host_test.c
 * The kernel side (proc_iterate, task_info, the kernel thread walk, the clock, printf) is a fake world of processes and kernel threads the cases edit between
 * two ct_tick() calls. One case per process run (the file's state is static): `cputop CASE` prints the captured kernel lines and `CASE <name> ok|FAIL <why>`.
 * Constants are the kernel's: TH_FLAGS_IDLE 0x2 (osfmk/mach/thread_info.h), PROC_RETURNED 0 (bsd/sys/proc_internal.h).
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct hproc { int pid; const char *name; uint64_t us; int no_task; int kr; } *proc_t;
typedef struct hproc *task_t;
typedef struct { uint64_t total_user, total_system, threads_user, threads_system; } task_absolutetime_info_data_t;
typedef int *task_info_t;
typedef unsigned mach_msg_type_number_t;
typedef int kern_return_t;
#define TASK_ABSOLUTETIME_INFO        1
#define TASK_ABSOLUTETIME_INFO_COUNT  8
#define TASK_NULL                     ((task_t)0)
#define KERN_SUCCESS                  0
#define PROC_RETURNED                 0
#define PROC_ALLPROCLIST              1
#define PROC_NOWAITTRANS              2
#define TH_FLAGS_IDLE                 0x2

struct hthr { uint64_t tid, us, flags, cont; const char *name; };

#define H_MAXP 700
#define H_MAXK 900
static struct hproc h_p[H_MAXP];
static int h_np;
static struct hthr h_k[H_MAXK];
static int h_nk, h_kerr_at = -1;       /* h_kerr_at >= 0: the walk fails at that thread with error 5 */
static uint64_t h_now_ns;
static char h_out[1 << 20];
static size_t h_len;
static struct hproc h_kernel_task_obj;
static task_t kernel_task = &h_kernel_task_obj;

static int
h_printf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(h_out + h_len, sizeof(h_out) - h_len, fmt, ap);
	va_end(ap);
	if (n > 0) {
		h_len += (size_t)n;
	}
	return n;
}
#define printf h_printf

static size_t
h_strlcpy(char *d, const char *s, size_t n)
{
	size_t l = strlen(s);
	if (n) {
		size_t c = l < n - 1 ? l : n - 1;
		memcpy(d, s, c);
		d[c] = '\0';
	}
	return l;
}
#define strlcpy h_strlcpy

static void absolutetime_to_nanoseconds(uint64_t abs, uint64_t *ns) { *ns = abs; }     /* host: 1 tick = 1 ns */
static uint64_t mach_absolute_time(void) { return h_now_ns; }
static int proc_pid(proc_t p) { return p->pid; }
static task_t proc_task(proc_t p) { return p->no_task ? TASK_NULL : p; }
static const char *proc_best_name(proc_t p) { return p->name; }

static kern_return_t
task_info(task_t t, int flavor, task_info_t out, mach_msg_type_number_t *cnt)
{
	task_absolutetime_info_data_t *ti = (task_absolutetime_info_data_t *)(void *)out;
	(void)flavor;
	(void)cnt;
	if (t->kr) {
		return t->kr;
	}
	ti->total_user = t->us * 1000 / 4;                 /* split 1:3, the file must add both */
	ti->total_system = t->us * 1000 - ti->total_user;
	return KERN_SUCCESS;
}

static void
proc_iterate(int flags, int (*fn)(proc_t, void *), void *arg, void *ff, void *fa)
{
	(void)flags;
	(void)ff;
	(void)fa;
	for (int i = 0; i < h_np; i++) {
		(void)fn(&h_p[i], arg);
	}
}

static int
sweet_cputop_task(task_t task, uint64_t *out, char *names, int namesz, int max, int *total)
{
	int idx = 0;
	if (task != kernel_task) {
		return 99;
	}
	for (int i = 0; i < h_nk; i++) {
		if (idx < max) {
			if (i == h_kerr_at) {
				*total = idx;
				return 5;
			}
			out[idx * 4 + 0] = h_k[i].tid;
			out[idx * 4 + 1] = h_k[i].us;
			out[idx * 4 + 2] = h_k[i].flags;
			out[idx * 4 + 3] = h_k[i].cont;
			h_strlcpy(names + (size_t)idx * (size_t)namesz, h_k[i].name ? h_k[i].name : "", (size_t)namesz);
		}
		idx++;
	}
	*total = idx;
	return 0;
}

#include CPUTOP_SRC

/* ---------------------------------------------------------------- the cases */
static const char *h_fail;
#define NEED(c, why) do { if (!(c) && !h_fail) { h_fail = (why); } } while (0)

static int has(const char *s) { return strstr(h_out, s) != NULL; }
static int
count(const char *s)
{
	int n = 0;
	for (const char *p = h_out; (p = strstr(p, s)) != NULL; p += strlen(s)) {
		n++;
	}
	return n;
}
static void addp(int pid, const char *name, uint64_t us) { h_p[h_np].pid = pid; h_p[h_np].name = name; h_p[h_np].us = us; h_p[h_np].no_task = 0; h_p[h_np].kr = 0; h_np++; }
static void addk(uint64_t tid, const char *name, uint64_t us, uint64_t flags, uint64_t cont) { h_k[h_nk].tid = tid; h_k[h_nk].name = name; h_k[h_nk].us = us; h_k[h_nk].flags = flags; h_k[h_nk].cont = cont; h_nk++; }
static void tick(uint64_t ms) { h_now_ns += ms * 1000000ull; ct_tick(); }

/* 12 processes + 5 kernel threads (one idle), a baseline sample at t = 1000 ms */
static void
world(void)
{
	static const char *const names[12] = { "launchd", "backboardd", "SpringBoard", "bluetoothd", "coreduetd", "biomed", "pixnav", "wifid", "Preferences", "chronod", "profiled", "a name with spaces" };
	addp(0, "kernel_task", 777000000);             /* the proc list has pid 0; it must not become a row */
	for (int i = 0; i < 12; i++) {
		addp(i == 0 ? 1 : 300 + i, names[i], 1000000ull * (uint64_t)(i + 1));
	}
	addk(0x101, "sweet_fb_vsync", 5000000, 0, 0xfffffe0007123450ull);
	addk(0x102, "sweet_usb_log", 2000000, 0, 0);
	addk(0x103, NULL, 3000000, 0, 0xfffffe0007abcde0ull);
	addk(0x104, "idle #0", 900000000, TH_FLAGS_IDLE, 0xfffffe0007000010ull);
	addk(0x105, "sweet_cputop", 1000, 0, 0);
	tick(1000);
}

static void
c_baseline(void)
{
	world();
	NEED(has("SWEET CPUTOP: baseline procs 12 kthr 4\n"), "baseline line");
	NEED(count("SWEET CPUTOP") == 1, "only the baseline line on the first sample");
}

static void
c_rank(void)
{
	world();
	h_len = 0;
	/* deltas (us): SpringBoard 620000, vsync 410000, bluetoothd 300500, backboardd 300400, coreduetd 200000, unnamed kthread 120000, biomed 90000, usb_log 50000,
	 * pixnav 40000, wifid 30000, launchd 999 (0 ms); idle 700000 */
	h_p[3].us += 620000; h_k[0].us += 410000; h_p[4].us += 300500; h_p[2].us += 300400; h_p[5].us += 200000; h_k[2].us += 120000; h_p[6].us += 90000;
	h_k[1].us += 50000; h_p[7].us += 40000; h_p[8].us += 30000; h_p[1].us += 999; h_k[3].us += 700000;
	tick(1001);
	NEED(has("SWEET CPUTOP: t_ms 2001 dt_ms 1001 procs 12 kthr 4 sum_ms 2161 top_ms 2090 idle_ms 700 new 0 gone 0 late 0 fail 0 trunc 0 kerr 0\n"), "header");
	NEED(has("SWEET CPUTOP: r 1 p pid 302 ms 620 cum_ms 3620 name SpringBoard\n"), "row 1");
	NEED(has("SWEET CPUTOP: r 2 k tid 0x101 ms 410 cum_ms 5410 cont 0xfffffe0007123450 name sweet_fb_vsync\n"), "row 2 kernel thread");
	NEED(has("SWEET CPUTOP: r 3 p pid 303 ms 300 cum_ms 4300 name bluetoothd\n"), "row 3");
	NEED(has("SWEET CPUTOP: r 4 p pid 301 ms 300 cum_ms 2300 name backboardd\n"), "row 4 (100 us less than row 3)");
	NEED(has("SWEET CPUTOP: r 5 p pid 304 ms 200 cum_ms 5200 name coreduetd\n"), "row 5");
	NEED(has("SWEET CPUTOP: r 6 k tid 0x103 ms 120 cum_ms 3120 cont 0xfffffe0007abcde0 name -\n"), "row 6 unnamed kernel thread");
	NEED(has("SWEET CPUTOP: r 7 p pid 305 ms 90 cum_ms 6090 name biomed\n"), "row 7");
	NEED(has("SWEET CPUTOP: r 8 k tid 0x102 ms 50 cum_ms 2050 cont 0x0 name sweet_usb_log\n"), "row 8");
	NEED(!has(" r 9 "), "at most 8 rows");
	NEED(!has("idle #0") && !has("kernel_task") && !has("pid 0 "), "idle thread and pid 0 never ranked");
	NEED(count("SWEET CPUTOP") == 9, "9 lines");
}

static void
c_few(void)
{
	world();
	h_len = 0;
	h_p[12].us += 7000; h_k[4].us += 2400;
	tick(5000);
	NEED(has("dt_ms 5000 procs 12 kthr 4 sum_ms 9 top_ms 9 idle_ms 0 "), "header");
	NEED(has("SWEET CPUTOP: r 1 p pid 311 ms 7 cum_ms 12007 name a name with spaces\n"), "name with spaces to the end of the line");
	NEED(has("SWEET CPUTOP: r 2 k tid 0x105 ms 2 cum_ms 3 cont 0x0 name sweet_cputop\n"), "row 2");
	NEED(count("SWEET CPUTOP") == 3, "entries without CPU time are not rows");
}

static void
c_new_gone(void)
{
	world();
	h_len = 0;
	h_p[5] = h_p[--h_np];                          /* coreduetd exits; the last process takes its slot (walk order changes) */
	addp(400, "newproc", 250000);                  /* started inside the period: all of its time counts */
	addk(0x12d, "newthread", 2080000, 0, 0);       /* tid 0x12d = 301 = backboardd's pid: a thread is never matched to a process */
	h_p[2].us += 100000;
	tick(1000);
	NEED(has(" procs 12 kthr 5 sum_ms 2430 top_ms 2430 idle_ms 0 new 2 gone 1 late 0 "), "header new/gone");
	NEED(has("SWEET CPUTOP: r 1 k tid 0x12d ms 2080 cum_ms 2080 cont 0x0 name newthread\n"), "new thread row (tid equal to a pid)");
	NEED(has("SWEET CPUTOP: r 2 p pid 400 ms 250 cum_ms 250 name newproc\n"), "new process row");
	NEED(has("SWEET CPUTOP: r 3 p pid 301 ms 100 cum_ms 2100 name backboardd\n"), "old row");
}

static void
c_late_reuse(void)
{
	world();
	h_len = 0;
	addp(500, "oldtimer", 9000000);                /* 9 s of CPU appearing in a 1 s period: not from this period (8 cores x 1 s = 8 s is the bound) */
	h_p[3].us = 5000;                              /* pid 302 reused: the counter went back, the new process used 5 ms */
	h_p[3].name = "reborn";
	tick(1000);
	NEED(has(" sum_ms 5 top_ms 5 idle_ms 0 new 2 gone 0 late 1 "), "header late");
	NEED(has("SWEET CPUTOP: r 1 p pid 302 ms 5 cum_ms 5 name reborn\n"), "reused pid counts its own time");
	NEED(!has("oldtimer"), "late entry not ranked");
	h_len = 0;
	h_p[13].us += 30000;
	tick(1000);
	NEED(has("SWEET CPUTOP: r 1 p pid 500 ms 30 cum_ms 9030 name oldtimer\n") && has(" new 0 gone 0 late 0 "), "late entry is normal from the next period on");
}

static void
c_fail(void)
{
	world();
	h_len = 0;
	h_p[4].no_task = 1;
	h_p[6].kr = 4;
	h_p[2].us += 10000;
	tick(1000);
	NEED(has("SWEET CPUTOP GAP: 2 process(es) not ranked: first pid 303 bluetoothd has no task (-1); `fail` on every header counts them\n"), "GAP line names the first");
	NEED(has(" procs 10 kthr 4 ") && has(" gone 2 late 0 fail 2 trunc 0 kerr 0\n"), "header fail");
	h_len = 0;
	tick(1000);
	NEED(!has("GAP") && has(" fail 2 "), "GAP once, count on every header");
}

static void
c_trunc(void)
{
	static char nm[H_MAXP][12];
	for (int i = 0; i < 600; i++) {
		snprintf(nm[i], sizeof(nm[i]), "p%d", i);
		addp(1000 + i, nm[i], 1000);
	}
	for (int i = 0; i < 800; i++) {
		addk(0x1000 + (uint64_t)i, "kt", 1000, 0, 0);
	}
	tick(1000);
	NEED(has("SWEET CPUTOP GAP: table full, 120 entries not ranked (caps: 512 processes, 768 kernel threads); `trunc` on every header counts them\n"), "GAP table full");
	NEED(has("baseline procs 512 kthr 768\n"), "baseline at the caps");
	h_len = 0;
	h_p[511].us += 3000; h_p[512].us += 900000; h_k[767].us += 4000; h_k[768].us += 900000;
	tick(1000);
	NEED(has(" procs 512 kthr 768 sum_ms 7 top_ms 7 ") && has(" trunc 120 kerr 0\n") && !has("GAP"), "header trunc, no second GAP");
	NEED(has("r 1 k tid 0x12ff ms 4 ") && has("r 2 p pid 1511 ms 3 ") && !has("ms 900 "), "entries past the caps are not ranked");
}

static void
c_kerr(void)
{
	world();
	h_len = 0;
	h_kerr_at = 2;
	h_k[0].us += 20000; h_k[4].us += 500000;
	tick(1000);
	NEED(has("SWEET CPUTOP GAP: kernel thread walk stopped by thread_info error 5: the kernel threads after it are not ranked; `kerr` on every header\n"), "GAP kerr");
	NEED(has(" kthr 2 sum_ms 20 top_ms 20 ") && has(" gone 3 late 0 fail 0 trunc 0 kerr 5\n"), "header kerr");
	NEED(has("r 1 k tid 0x101 ms 20 ") && !has("0x105"), "threads before the failing one still rank");
}

static void
c_order(void)
{
	world();
	h_len = 0;
	for (int i = 1; i < 7; i++) {                  /* reverse the process walk order */
		struct hproc t = h_p[i];
		h_p[i] = h_p[13 - i];
		h_p[13 - i] = t;
	}
	struct hthr k = h_k[0];
	h_k[0] = h_k[4];
	h_k[4] = k;
	for (int i = 1; i < 13; i++) {
		h_p[i].us += 1000ull * (uint64_t)h_p[i].pid;
	}
	tick(1000);
	NEED(has(" new 0 gone 0 late 0 "), "a changed walk order is not new/gone");
	NEED(has("SWEET CPUTOP: r 1 p pid 311 ms 311 cum_ms 12311 name a name with spaces\n") && has("SWEET CPUTOP: r 8 p pid 304 ms 304 cum_ms 5304 name coreduetd\n"), "rows by delta");
}

static void
c_cap(void)
{
#ifdef CPUTOP_SMALL_CAP
	world();
	for (int i = 0; i < 6; i++) {
		h_p[2].us += 10000; h_p[3].us += 20000; h_p[4].us += 30000;
		tick(1000);
	}
	/* cap 10: baseline is not counted; period 1 = 4 lines, period 2 = 4, period 3 = header + 1 row, then the cap line, then silence */
	NEED(count("SWEET CPUTOP: t_ms") == 3 && count("SWEET CPUTOP: r ") == 7, "10 lines then stop");
	NEED(count("SWEET CPUTOP: cap reached 10\n") == 1, "cap line once");
#else
	h_fail = "built without -DCPUTOP_SMALL_CAP";
#endif
}

static const struct { const char *name; void (*fn)(void); } cases[] = {
	{ "baseline", c_baseline }, { "rank", c_rank }, { "few", c_few }, { "new_gone", c_new_gone }, { "late_reuse", c_late_reuse }, { "fail", c_fail },
	{ "trunc", c_trunc }, { "kerr", c_kerr }, { "order", c_order }, { "cap", c_cap },
};

int
main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "--list") == 0) {
		for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
			puts(cases[i].name);
		}
		return 0;
	}
	for (size_t i = 0; argc == 2 && i < sizeof(cases) / sizeof(cases[0]); i++) {
		if (strcmp(argv[1], cases[i].name) == 0) {
			cases[i].fn();
			fputs(h_out, stdout);
			fprintf(stdout, "CASE %s %s%s\n", cases[i].name, h_fail ? "FAIL " : "ok", h_fail ? h_fail : "");
			return h_fail ? 1 : 0;
		}
	}
	fprintf(stderr, "usage: cputop --list | CASE\n");
	return 2;
}
