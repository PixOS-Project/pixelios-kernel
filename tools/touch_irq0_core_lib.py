"""tools/test_touch_irq0_core.py support (patch 0355): the C driver that plays the alive pin controller, the core extraction from the patch text and the compile-and-run helper. See the test file for what is checked."""
import os, re, shutil, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0355-sweet-touch-irq0.patch")

DRIVER = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char logb[1 << 17];
static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#include <stdarg.h>
static void logf_(const char *fmt, ...) { va_list ap; size_t n = strlen(logb); va_start(ap, fmt); vsnprintf(logb + n, sizeof logb - n, fmt, ap); va_end(ap); }
#define STI_LOG(...) logf_(__VA_ARGS__)
@CORE@
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); bad++; } } while (0)
#define CHKS(got, want) do { if (strcmp((got), (want)) != 0) { printf("FAIL line %d:\n  got : %s\n  want: %s\n", __LINE__, (got), (want)); bad++; } } while (0)

/* ---- fake alive pin controller: reads are recorded and judged against the test's OWN list; ANY write fails the test ---- */
static const uint32_t OK_OFFS[6] = { 0x0C0, 0x0C4, 0x718, 0x828, 0x918, 0xA18 };
static uint32_t w_con, w_dat, w_econ, w_eflt, w_emask, w_epend;
static int rd_fail_off = -1, no_rd, nreads, wrote, bad_off, trace[64], ntrace;
static int rd(void *c, uint32_t off, uint32_t *v)
{
	int ok = 0, i;
	(void)c;
	for (i = 0; i < 6; i++) if (OK_OFFS[i] == off) ok = 1;
	if (!ok) { bad_off++; printf("FAIL: read of non-allowlisted offset %#x\n", off); bad++; }
	if ((int)off == rd_fail_off) return -1;
	nreads++;
	if (ntrace < 64) trace[ntrace++] = (int)off;
	switch (off) {
	case 0x0C0: *v = w_con; break;
	case 0x0C4: *v = w_dat; break;
	case 0x718: *v = w_econ; break;
	case 0x828: *v = w_eflt; break;
	case 0x918: *v = w_emask; break;
	case 0xA18: *v = w_epend; break;
	default: *v = 0xDEAD; break;
	}
	return 0;
}
static int wr(void *c, uint32_t off, uint32_t v) { (void)c; wrote++; printf("FAIL: WRITE to %#x = %#x\n", off, v); bad++; return 0; }
static struct sti_ops OPS = { NULL, rd, wr };

static void reset_world(void)
{
	logb[0] = 0; rd_fail_off = -1; no_rd = 0; nreads = 0; wrote = 0; bad_off = 0; ntrace = 0;
	w_con = 0xF; w_dat = 0x3; w_econ = 0x3A; w_eflt = 0x7F7F40C0u; w_emask = 0xFF; w_epend = 0x101;
	OPS.rd = rd;
}
static int count(const char *needle) { int n = 0; const char *p = logb; while ((p = strstr(p, needle))) { n++; p += strlen(needle); } return n; }
static int lines(void) { int n = 0; for (const char *p = logb; *p; p++) if (*p == '\n') n++; return n; }
static int tr_is(int a, int b, int c, int d, int e, int f) { int w[6] = { a, b, c, d, e, f }; if (ntrace != 6) return 0; for (int i = 0; i < 6; i++) if (trace[i] != w[i]) return 0; return 1; }
static void pass(struct sti *s, int dat, int n, uint64_t t) { int d; w_dat = (uint32_t)dat; d = sti_dat(s, &OPS); CHK(d == dat); sti_loop_obs(s, d, n, t); }
static void started(struct sti *s) { memset(s, 0, sizeof *s); reset_world(); sti_arg(s, 1, 2); CHK(sti_start(s, &OPS, STI_PAD_PA, 0, 1) == 0); logb[0] = 0; nreads = 0; ntrace = 0; }
static void quiet(const char *what) { if (wrote || bad_off) { printf("FAIL: %s: writes %d bad offsets %d\n", what, wrote, bad_off); bad++; } }

static void t_mode0(void)
{
	struct sti s; int present, val;
	for (present = 0; present < 2; present++) {
		memset(&s, 0, sizeof s); reset_world();
		val = 0;
		sti_arg(&s, present, val);
		CHK(s.mode == 0);
		CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1);
		CHK(sti_dat(&s, &OPS) == -1);
		sti_reset_begin(&s, 5); sti_reset_obs(&s, 1, 0, 6); sti_reset_end(&s, 1);
		sti_loop_obs(&s, 1, 1, 7); sti_loop_obs(&s, 0, 0, 99999999999ull);
		CHK(nreads == 0 && logb[0] == 0);
		CHK(s.passes == 0 && s.summaries == 0 && !s.on);
	}
	quiet("mode0");
	/* absent arg with junk in val must still be off */
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 0, 2); CHK(s.mode == 0 && logb[0] == 0);
}

static void t_judge(void)
{
	struct sti s; uint32_t v; int i;
	uint32_t refused[9] = { 0x0, 0xC8, 0x914, 0x71C, 0x80, 0xA1C, 0xC1, 0x1000, 0x700 };
	uint32_t allowed[6] = { 0x0C0, 0x0C4, 0x718, 0x828, 0x918, 0xA18 };
	for (i = 0; i < 9; i++) {
		started(&s);
		CHK(sti_rd(&s, &OPS, refused[i], &v) == -2 && !s.on && nreads == 0 && count("refused (not on the read allowlist), stage 0 off, poll continues") == 1 && lines() == 1);
		CHK(sti_dat(&s, &OPS) == -1 && nreads == 0);
	}
	for (i = 0; i < 6; i++) {
		started(&s);
		CHK(sti_rd(&s, &OPS, allowed[i], &v) == 0 && s.on && nreads == 1 && logb[0] == 0);
	}
	quiet("judge");
}

static void t_arg(void)
{
	struct sti s; int v[4] = { 1, 3, -1, 99 }, i;
	for (i = 0; i < 4; i++) {
		char want[200];
		memset(&s, 0, sizeof s); reset_world();
		sti_arg(&s, 1, v[i]);
		CHK(s.mode == 0 && !s.on);
		snprintf(want, sizeof want, "TOUCH GAP: sweettouchirq=%d: stage 1 not in this patch (patch 0355 has the read-only stage 0 = 2 only), treated as 0\n", v[i]);
		CHKS(logb, want);
		CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1);
		CHK(sti_dat(&s, &OPS) == -1);
		CHK(nreads == 0 && lines() == 1);
	}
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 0); CHK(s.mode == 0 && logb[0] == 0);
	memset(&s, 0, sizeof s); reset_world(); s.mode = 2; s.on = 1; sti_arg(&s, 1, 7); CHK(s.mode == 0 && s.on == 0);
	memset(&s, 0, sizeof s); reset_world(); s.mode = 2; s.on = 1; sti_arg(&s, 0, 0); CHK(s.mode == 0 && s.on == 0);
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2); CHK(s.mode == 2 && logb[0] == 0 && !s.on);
	quiet("arg");
}

static void t_start(void)
{
	struct sti s;
	memset(&s, 0, sizeof s); reset_world();
	sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0);
	CHK(s.on == 1);
	CHK(tr_is(0x0C0, 0x0C4, 0x718, 0x828, 0x918, 0xA18));
	CHKS(logb, "SWEET TOUCH IRQ0: gpa9 read once: econ 0x3a [pin0 10] efltcon 0x7f7f40c0 [byte0 0xc0] emask 0xff [bit0 1] epend 0x101 [bit0 1] con 0xf [func 0xf] dat 0x3 [pin0 1]\n");
	quiet("start");
	/* distinct values: a swapped word shows */
	memset(&s, 0, sizeof s); reset_world(); w_con = 0x0; w_dat = 0x2; w_econ = 0x4; w_eflt = 0xC0; w_emask = 0x0; w_epend = 0x0;
	sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0);
	CHKS(logb, "SWEET TOUCH IRQ0: gpa9 read once: econ 0x4 [pin0 4] efltcon 0xc0 [byte0 0xc0] emask 0 [bit0 0] epend 0 [bit0 0] con 0 [func 0] dat 0x2 [pin0 0]\n");
	/* start without the arg: nothing */
	memset(&s, 0, sizeof s); reset_world();
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1 && nreads == 0 && !logb[0] && !s.on);
	/* start twice is not blocked but every call reads exactly the six words */
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0 && sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0 && nreads == 12);
	quiet("start2");
}

static void t_start_fail(void)
{
	struct sti s;
	int offs[6] = { 0x0C0, 0x0C4, 0x718, 0x828, 0x918, 0xA18 }, i;
	/* no mapping */
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 0) == -1 && !s.on && nreads == 0);
	CHKS(logb, "TOUCH GAP: sweettouchirq=2: the alive pin controller window could not be mapped, stage 0 off, poll continues\n");
	CHK(sti_dat(&s, &OPS) == -1 && nreads == 0);
	/* wrong pad: bank and pin */
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, 0x174D0100ull, 0, 1) == -1 && !s.on && nreads == 0 && count("TOUCH GAP: sweettouchirq=2: the irq pad is bank 0x174d0100 pin 0, not gpa9[0]") == 1);
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 1, 1) == -1 && !s.on && nreads == 0 && count("pin 1, not gpa9[0]") == 1);
	/* a read of each of the six words fails */
	for (i = 0; i < 6; i++) {
		char want[160];
		memset(&s, 0, sizeof s); reset_world(); rd_fail_off = offs[i]; sti_arg(&s, 1, 2);
		CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1 && !s.on);
		CHK(nreads == i);
		snprintf(want, sizeof want, "alive offset %#x failed", offs[i]);
		CHK(count(want) == 1 && count("TOUCH GAP") == 1 && lines() == 1);
		CHK(sti_dat(&s, &OPS) == -1 && nreads == i);
		sti_loop_obs(&s, 1, 1, 0); sti_reset_obs(&s, 1, 0, 0); sti_reset_end(&s, 1);
		CHK(lines() == 1);
	}
	/* ops without rd */
	memset(&s, 0, sizeof s); reset_world(); sti_arg(&s, 1, 2); OPS.rd = NULL;
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1 && !s.on && count("failed (no mapping)") == 1);
	OPS.rd = rd;
	/* all four EINT words all-ones */
	memset(&s, 0, sizeof s); reset_world(); w_econ = w_eflt = w_emask = w_epend = 0xFFFFFFFFu; sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == -1 && !s.on && count("all four EINT words read 0xffffffff") == 1 && lines() == 1);
	/* three of four all-ones is a real value, not a dead window */
	memset(&s, 0, sizeof s); reset_world(); w_econ = w_eflt = w_emask = 0xFFFFFFFFu; w_epend = 0; sti_arg(&s, 1, 2);
	CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0 && s.on);
	for (i = 0; i < 4; i++) {
		memset(&s, 0, sizeof s); reset_world(); w_econ = w_eflt = w_emask = w_epend = 0xFFFFFFFFu; sti_arg(&s, 1, 2);
		(i == 0 ? (w_econ = 0) : i == 1 ? (w_eflt = 0) : i == 2 ? (w_emask = 0) : (w_epend = 0));
		CHK(sti_start(&s, &OPS, STI_PAD_PA, 0, 1) == 0 && s.on);
	}
	quiet("start_fail");
}

static void t_dat(void)
{
	struct sti s; int i;
	uint32_t vals[6] = { 0, 1, 2, 3, 0x80000000u, 0xFFFFFFFEu };
	int want[6] = { 0, 1, 0, 1, 0, 0 };
	started(&s);
	for (i = 0; i < 6; i++) {
		nreads = 0; ntrace = 0; w_dat = vals[i];
		CHK(sti_dat(&s, &OPS) == want[i]);
		CHK(nreads == 1 && trace[0] == 0xC4);
	}
	w_dat = 0xFFFFFFFFu; nreads = 0; logb[0] = 0;
	CHK(sti_dat(&s, &OPS) == -1 && !s.on && count("the pad DAT word reads 0xffffffff") == 1 && lines() == 1);
	nreads = 0; CHK(sti_dat(&s, &OPS) == -1 && nreads == 0);
	started(&s); rd_fail_off = 0xC4;
	CHK(sti_dat(&s, &OPS) == -1 && !s.on && count("alive offset 0xc4 failed") == 1);
	quiet("dat");
}

static void t_reset(void)
{
	struct sti s; int i; char want[200];
	started(&s);
	sti_reset_begin(&s, 1000);
	for (i = 0; i < 45; i++) {
		int d = i < 7 ? 1 : 0;
		w_dat = (uint32_t)d;
		d = sti_dat(&s, &OPS);
		sti_reset_obs(&s, d, i == 30 ? 0x03u : 0u, 1000u + 5000u * (uint64_t)i);
	}
	sti_reset_end(&s, 1);
	CHK(nreads == 45 && ntrace == 45 && trace[0] == 0xC4);
	CHK(count("SWEET TOUCH IRQ0: reset-wait i=") == 40);
	CHKS(strstr(logb, "SWEET TOUCH IRQ0: reset-wait i=0 dat=1 ev=00\n") ? "ok" : "no", "ok");
	CHK(count("reset-wait i=39 dat=0 ev=00\n") == 1 && count("reset-wait i=40 ") == 0 && count("reset-wait i=44 ") == 0);
	CHK(count("reset-wait i=6 dat=1 ev=00\n") == 1 && count("reset-wait i=7 dat=0 ev=00\n") == 1);
	CHK(count("reset-wait i=30 dat=0 ev=03\n") == 1);
	snprintf(want, sizeof want, "SWEET TOUCH IRQ0: reset-wait first DAT change 1 -> 0, %u us after reset release (i=7)\n", 1000u + 5000u * 7u - 1000u);
	CHK(count(want) == 1 && count("first DAT change") == 1);
	CHK(count("SWEET TOUCH IRQ0: reset-wait end: 45 samples, dat first 1 last 0, DAT changed, controller-ready seen\n") == 1);
	CHK(lines() == 40 + 1 + 1);
	/* never changes, not ready; a second reset starts fresh */
	logb[0] = 0; sti_reset_begin(&s, 0);
	for (i = 0; i < 3; i++) { w_dat = 1; sti_reset_obs(&s, sti_dat(&s, &OPS), 0, (uint64_t)i); }
	sti_reset_end(&s, 0);
	CHK(count("reset-wait end: 3 samples, dat first 1 last 1, DAT never changed, controller-ready not seen\n") == 1 && count("first DAT change") == 0);
	CHK(count("reset-wait i=0 dat=1") == 1 && count("reset-wait i=2 dat=1") == 1);
	/* a change back (1 -> 0 -> 1) is reported once */
	logb[0] = 0; sti_reset_begin(&s, 0);
	{ int seq[4] = { 1, 0, 1, 0 }; for (i = 0; i < 4; i++) { w_dat = (uint32_t)seq[i]; sti_reset_obs(&s, sti_dat(&s, &OPS), 0, 10u * (uint64_t)i); } }
	CHK(count("first DAT change") == 1 && count("first DAT change 1 -> 0, 10 us after reset release (i=1)") == 1);
	/* dat < 0 (stage 0 off for this read) is ignored */
	logb[0] = 0; sti_reset_obs(&s, -1, 0, 0); CHK(logb[0] == 0);
	quiet("reset");
}

static void t_loop(void)
{
	struct sti s; int i; uint64_t t = 0;
	started(&s);
	/* cells: low/empty low/found high/empty high/found, edges, run */
	pass(&s, 0, 0, t); pass(&s, 0, 0, t); pass(&s, 0, 0, t);        /* run 3, no edge (first sample) */
	pass(&s, 1, 0, t);                                              /* edge 1, run reset */
	pass(&s, 0, 2, t);                                              /* edge 2, found */
	pass(&s, 1, 5, t);                                              /* edge 3, high/found */
	pass(&s, 1, 0, t);
	pass(&s, 0, 0, t); pass(&s, 0, 0, t);                           /* edge 4, run 2 */
	CHK(s.cnt[0][0] == 5 && s.cnt[0][1] == 1 && s.cnt[1][0] == 2 && s.cnt[1][1] == 1);
	CHK(s.edges == 4 && s.maxrun == 3 && s.run == 2 && s.passes == 9 && s.drain_err == 0);
	/* a bus error: not in the 2x2, counted, the run is untouched, the edge still counts */
	w_dat = 1; sti_loop_obs(&s, sti_dat(&s, &OPS), -1, t);
	CHK(s.drain_err == 1 && s.passes == 9 && s.cnt[1][0] == 2 && s.edges == 5 && s.run == 2);
	CHK(logb[0] == 0);                                              /* no summary yet: all at t = 0 */
	quiet("loop");
	/* cadence: first obs sets the tick, 10 s later one line, 9.999999 s none */
	started(&s);
	pass(&s, 1, 0, 1000000ull);
	pass(&s, 1, 0, 10999999ull); CHK(logb[0] == 0);
	pass(&s, 1, 0, 11000000ull); CHK(count("SWEET TOUCH IRQ0: passes 3 ") == 1 && lines() == 1);
	pass(&s, 1, 0, 20999999ull); CHK(lines() == 1);
	pass(&s, 1, 0, 21000000ull); CHK(count("SWEET TOUCH IRQ0: passes 5 ") == 1 && lines() == 2);
	CHK(s.summaries == 2);
	CHK(count("verdict pending\n") == 2);
	/* exact line */
	started(&s);
	pass(&s, 0, 0, 0); pass(&s, 0, 3, 1); pass(&s, 1, 0, 2); pass(&s, 1, 0, 3); pass(&s, 1, 4, 4); pass(&s, 0, -1, 5); pass(&s, 1, 0, 10000000ull);
	CHKS(logb, "SWEET TOUCH IRQ0: passes 6 low/found 1 low/empty 1 high/found 1 high/empty 3 edges 3 maxrun_low_empty 1 drain_err 1 verdict pending\n");
	/* verdict boundaries */
	for (i = 0; i < 4; i++) {
		int total = i == 0 ? 99 : 100, good = i == 0 ? 99 : i == 1 ? 95 : i == 2 ? 94 : 100, k;
		started(&s);
		for (k = 0; k < total; k++) {
			if (k < good) pass(&s, k & 1, (k & 1) ? 0 : 1, 0);      /* high/empty, low/found */
			else pass(&s, 1, 1, 0);                                  /* high/found = a bad cell */
		}
		pass(&s, 1, 1, 10000000ull);                                  /* the tick; it is one more pass in a bad cell, so recompute the expectation below */
		{
			int tot = total + 1, ok = (uint64_t)good * 100 >= (uint64_t)tot * 95;
			CHK(count(ok && tot >= 100 ? "verdict LOW-confirmed\n" : tot < 100 ? "verdict pending\n" : "verdict NOT-confirmed\n") == 1);
			CHK(count("polarity LOW not confirmed") == ((!ok && tot >= 100) ? 1 : 0));
		}
	}
	/* explicit boundary: 100 passes, tick on the 100th */
	started(&s);
	for (i = 0; i < 99; i++) pass(&s, 1, 0, 0);
	for (i = 0; i < 0; i++) {}
	pass(&s, 1, 0, 10000000ull);                                    /* 100 passes, 100 good */
	CHK(count("passes 100 ") == 1 && count("verdict LOW-confirmed") == 1);
	started(&s);
	for (i = 0; i < 95; i++) pass(&s, 1, 0, 0);
	for (i = 0; i < 4; i++) pass(&s, 1, 1, 0);
	pass(&s, 1, 1, 10000000ull);                                    /* 100 passes, 95 good = exactly 95 % */
	CHK(count("passes 100 ") == 1 && count("verdict LOW-confirmed") == 1 && count("TOUCH GAP") == 0);
	started(&s);
	for (i = 0; i < 94; i++) pass(&s, 1, 0, 0);
	for (i = 0; i < 5; i++) pass(&s, 1, 1, 0);
	pass(&s, 1, 1, 10000000ull);                                    /* 94 good */
	CHK(count("verdict NOT-confirmed") == 1 && count("TOUCH GAP: sweettouchirq=2: polarity LOW not confirmed (94 of 100 passes in the two expected cells, need 95 %): do not enable stage 1, write the 2x2 table into the sprint log\n") == 1);
	/* the transition GAP is once: another summary while still NOT-confirmed adds only the summary line */
	logb[0] = 0; pass(&s, 1, 1, 20000000ull);
	CHK(count("verdict NOT-confirmed") == 1 && count("polarity LOW not confirmed") == 0);
	/* low/empty GAP: 20 of 100 = no GAP, 21 of 100 = GAP */
	started(&s);
	for (i = 0; i < 20; i++) pass(&s, 0, 0, 0);
	for (i = 0; i < 79; i++) pass(&s, 1, 0, 0);
	pass(&s, 1, 0, 10000000ull);
	CHK(count("DAT low with an empty FIFO") == 0 && count("passes 100 ") == 1);
	started(&s);
	for (i = 0; i < 21; i++) pass(&s, 0, 0, 0);
	for (i = 0; i < 78; i++) pass(&s, 1, 0, 0);
	pass(&s, 1, 0, 10000000ull);
	CHK(count("TOUCH GAP: sweettouchirq=2: DAT low with an empty FIFO in 21 of 100 passes (over 20 %): stuck-low or inverted line") == 1);
	/* the 99-pass case has no GAP at all even with everything low/empty */
	started(&s);
	for (i = 0; i < 98; i++) pass(&s, 0, 0, 0);
	pass(&s, 0, 0, 10000000ull);
	CHK(count("passes 99 ") == 1 && count("TOUCH GAP") == 0 && count("verdict pending") == 1);
	/* off stage: no read, no count */
	started(&s); s.on = 0; nreads = 0;
	sti_loop_obs(&s, 1, 1, 0); sti_loop_obs(&s, 0, 0, 99999999999ull);
	CHK(nreads == 0 && s.passes == 0 && logb[0] == 0);
	/* dat < 0 on a live stage is ignored */
	started(&s); sti_loop_obs(&s, -1, 1, 0); CHK(s.passes == 0 && s.tick_init == 0);
	quiet("loop2");
}

int main(void)
{
	t_mode0(); t_arg(); t_judge(); t_start(); t_start_fail(); t_dat(); t_reset(); t_loop();
	CHK(wrote == 0);
	if (bad) { printf("FAILED %d\n", bad); return 1; }
	printf("CORE OK\n");
	return 0;
}
'''


def patch_header(patch=PATCH):
    """the new-file hunk of bsd/dev/sweet_touch_irq0.h out of the patch text"""
    with open(patch) as f:
        t = f.read()
    i = t.index("diff --git a/bsd/dev/sweet_touch_irq0.h b/bsd/dev/sweet_touch_irq0.h")
    j = t.find("\ndiff --git ", i + 10)
    hunk = t[i:j if j > 0 else len(t)]
    body = hunk.split("\n+++ b/bsd/dev/sweet_touch_irq0.h\n", 1)[1]
    out = []
    for ln in body.splitlines()[1:]:      # skip the @@ line
        assert ln.startswith("+"), ln
        out.append(ln[1:])
    return "\n".join(out) + "\n"


def core_text(patch=PATCH):
    m = re.search(r"^/\* STI_CORE_BEGIN \*/\n(.*?)^/\* STI_CORE_END \*/", patch_header(patch), re.S | re.M)
    assert m, "no STI_CORE block in the patch"
    return m.group(1)


def run(core):
    d = tempfile.mkdtemp()
    try:
        c = os.path.join(d, "t.c")
        with open(c, "w") as f:
            f.write(DRIVER.replace("@CORE@", core))
        exe = os.path.join(d, "t")
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        p = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-but-set-variable", "-Wno-unused-variable", "-Wno-unused-value", "-Werror", "-O1", c, "-o", exe], capture_output=True, text=True)
        if p.returncode:
            return None, "compile: " + p.stderr[:900]
        try:
            q = subprocess.run([exe], capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return False, "timeout (an endless loop counts as a failure)"
        return q.returncode == 0, q.stdout
    finally:
        shutil.rmtree(d, True)
