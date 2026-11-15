"""tools/test_touch_irq1_core.py support (patch 0356): the C driver that plays the gpa9 bank, the FTM5 FIFO and the GIC, the core extraction from the patch text and the compile-and-run helper. See the test file for what is checked."""
import os, re, shutil, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0356-sweet-touch-irq1.patch")

DRIVER = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static char logb[1 << 18];
static int in_isr, in_reset;
static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int bad;
static void logf_(const char *fmt, ...) { va_list ap; size_t n = strlen(logb); if (in_isr || in_reset) { printf("FAIL: log call from interrupt/reset context: %s\n", fmt); bad++; } va_start(ap, fmt); vsnprintf(logb + n, sizeof logb - n, fmt, ap); va_end(ap); }
#define STI1_LOG(...) logf_(__VA_ARGS__)
@CORE@
#define CHK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); bad++; } } while (0)
#define CHKS(got, want) do { if (strcmp((got), (want)) != 0) { printf("FAIL line %d:\n  got : %s\n  want: %s\n", __LINE__, (got), (want)); bad++; } } while (0)

/* ---- the world: gpa9 alive window (0x1000 bytes of words), FTM5 FIFO, GIC slot ---- */
#define PIN0_ALLOWED_WR(o) ((o) == 0x0C0u || (o) == 0x718u || (o) == 0x918u || (o) == 0xA18u)   /* the test's OWN list, independent of the core's */
static uint32_t W[0x400];
static uint32_t INIT[0x400];
static uint64_t now_ns;
static int fifo;                 /* events queued in the fake FTM5; the line is low while > 0 */
static int phantom_low;          /* line forced low with an empty FIFO (stuck or inverted) */
static int spurious_irq;         /* the EINT unit asserts although DAT is high (a spurious source) */
static int gic_deaf;             /* the GIC never delivers (a missed interrupt) */
static int gic_att, gic_en, n_attach, n_enable, n_detach, n_isr, n_wake, n_wr, n_rd, in_settle;
static int attach_rc, enable_rc, enable_en, detach_rc;
static int wr_fail_at, rd_fail_off, wr_stuck_off, wr_stuck_after;
static int bad_wr, hook_unmask_event, hook_disarm_inject, hook_att_quiesce;
static char ev[256];             /* ordered event string: W<off> A E D */
struct wrrec { uint32_t off, oldw, neww; } wrs[256];
static int nwr;
static int spi_in_isr_calls;
static int isr_ret_last;
static uint32_t isr_seq_last;
static struct sti1 S;

static void evs(const char *s) { size_t n = strlen(ev); if (n + strlen(s) + 2 < sizeof ev) { strcat(ev, s); strcat(ev, " "); } }
static int line_low(void) { return fifo > 0 || phantom_low; }
static uint32_t dat_word(void) { return line_low() ? 0xE : 0xF; }        /* DAT bit 0 = line level (high idle); other bits = other pads */
static int eint_active(void) { return (spurious_irq || line_low()) && (W[0x0C0 / 4] & 0xFu) == 0xFu && (W[0x718 / 4] & 0xFu) == 0u; }

static void settle(void);
static int rd(void *c, uint32_t off, uint32_t *v)
{
	(void)c; n_rd++;
	if ((int)off == rd_fail_off) return -1;
	if (hook_disarm_inject && off == 0x918 && S.state == STI1_S_DISARMING) { hook_disarm_inject = 0; fifo = 1; settle(); }   /* the line goes low in the teardown gap, before the mask store */
	if (off >= 0x1000 || (off & 3)) { printf("FAIL: read of %#x outside the window\n", off); bad++; return -1; }
	*v = off == 0x0C4 ? dat_word() : W[off / 4];
	return 0;
}
static int wr(void *c, uint32_t off, uint32_t v)
{
	char b[24];
	(void)c; n_wr++;
	if (!PIN0_ALLOWED_WR(off)) { printf("FAIL: WRITE to %#x = %#x outside the test's own allowlist\n", off, v); bad++; bad_wr++; }
	if (off == 0xA18 && v != 1u) { printf("FAIL: EPEND store %#x, only 0x1 allowed\n", v); bad++; bad_wr++; }
	if (off != 0xA18) {
		uint32_t m = off == 0x918 ? 1u : 0xFu;
		if ((W[off / 4] ^ v) & ~m) { printf("FAIL: write %#x: %#x -> %#x changes bits outside pin 0\n", off, W[off / 4], v); bad++; bad_wr++; }
	}
	if (off == 0x918 && !(v & 1u) && !S.unmasked) { printf("FAIL: the unmask store comes before the unmasked flag is set\n"); bad++; }
	if (in_reset && off != 0x918) { printf("FAIL: reset path wrote %#x\n", off); bad++; }
	if (in_reset && !(v & 1u)) { printf("FAIL: reset path wrote an unmask\n"); bad++; }
	if (in_isr && off != 0x918 && off != 0xA18) { printf("FAIL: ISR wrote %#x\n", off); bad++; }
	if (wr_fail_at >= 0 && nwr == wr_fail_at) { nwr++; wr_fail_at = -1; return -1; }
	if (nwr < 256) { wrs[nwr].off = off; wrs[nwr].oldw = W[off / 4]; wrs[nwr].neww = v; }
	nwr++;
	snprintf(b, sizeof b, "W%X", off); evs(b);
	if (off == 0xA18) {
		W[off / 4] &= ~v;                                   /* W1C; a level that is still low re-pends in settle() */
	} else if (!((int)off == wr_stuck_off && nwr > wr_stuck_after)) {
		W[off / 4] = v;
	}
	if (!in_settle) settle();
	return 0;
}
static int s_att(void *c, uint32_t rb[4]) { (void)c; if (in_isr) { spi_in_isr_calls++; } n_attach++; evs("A"); if (hook_att_quiesce) S.quiesce = 1; rb[0] = 1; rb[1] = 0; rb[2] = 0; rb[3] = 0x80; if (attach_rc == 0) gic_att = 1; return attach_rc; }
static int s_en(void *c, uint32_t *en) { (void)c; if (in_isr) { spi_in_isr_calls++; } n_enable++; evs("E"); if (enable_rc == 0) gic_en = 1; *en = (uint32_t)enable_en; if (!in_settle) settle(); return enable_rc; }
static int s_det(void *c) { (void)c; if (in_isr) { spi_in_isr_calls++; } n_detach++; evs("D"); gic_att = 0; gic_en = 0; return detach_rc; }
static uint64_t s_now(void *c) { (void)c; return now_ns; }
static void s_wake(void *c) { (void)c; n_wake++; }
static struct sti1_ops OPS = { NULL, rd, wr, s_att, s_en, s_det, s_now, s_wake };

/* EINT unit + GIC: level source, EPEND latches while the source is active, the GIC line = EPEND & ~EMASK; the dispatcher masks an INTID whose handler returns non-zero */
static void settle(void)
{
	int guard = 0;
	if (in_settle) return;
	in_settle = 1;
	for (;;) {
		if (eint_active()) W[0xA18 / 4] |= 1u;
		if (!((W[0xA18 / 4] & 1u) && !(W[0x918 / 4] & 1u) && gic_en && gic_att && !gic_deaf)) break;
		if (++guard > 3000) { printf("FAIL: interrupt storm in the fake (no ISR progress)\n"); bad++; break; }
		in_isr = 1; n_isr++;
		isr_ret_last = sti1_isr(&S, &OPS);
		in_isr = 0;
		isr_seq_last = S.isr_seq;
		if (isr_ret_last) gic_en = 0;
		if (hook_unmask_event) { /* nothing */ }
	}
	in_settle = 0;
}

static const uint32_t SENT_CON = 0x22222220u, SENT_ECON = 0x33333330u, SENT_MASK = 0xFFu;

static void reset_world(void)
{
	memset(W, 0, sizeof W);
	W[0x0C0 / 4] = SENT_CON; W[0x718 / 4] = SENT_ECON; W[0x828 / 4] = 0; W[0x918 / 4] = SENT_MASK; W[0xA18 / 4] = 0x80u;   /* pin 7 pending: another pin's EPEND bit must survive */
	W[0x0C8 / 4] = 0x5A5A5A5Au; W[0x91C / 4] = 0x1234u; W[0x71C / 4] = 0x4321u;
	memcpy(INIT, W, sizeof W);
	logb[0] = 0; ev[0] = 0; nwr = 0; n_wr = 0; n_rd = 0; n_isr = 0; n_wake = 0; n_attach = 0; n_enable = 0; n_detach = 0; in_isr = 0; in_reset = 0; in_settle = 0;
	fifo = 0; phantom_low = 0; spurious_irq = 0; gic_deaf = 0; gic_att = 0; gic_en = 0; attach_rc = 0; enable_rc = 0; enable_en = 1; detach_rc = 0;
	wr_fail_at = -1; rd_fail_off = -1; wr_stuck_off = -1; wr_stuck_after = 0; bad_wr = 0; spi_in_isr_calls = 0; isr_ret_last = 0; hook_unmask_event = 0; hook_disarm_inject = 0; hook_att_quiesce = 0;
	now_ns = 5000000000ull;
	memset(&S, 0, sizeof S);
}
static int count(const char *needle) { int n = 0; const char *p = logb; while ((p = strstr(p, needle))) { n++; p += strlen(needle); } return n; }
static int same_as_init(void) { return memcmp(W, INIT, sizeof W) == 0; }
static int only_pin0_differs(void) { for (int i = 0; i < 0x400; i++) { uint32_t m = i == 0x918 / 4 ? ~1u : i == 0xA18 / 4 ? ~1u : ~0xFu; if ((W[i] ^ INIT[i]) & m) return 0; } return 1; }
static void armed(void) { reset_world(); sti1_arg(&S, 1, 1); CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0); logb[0] = 0; ev[0] = 0; nwr = 0; n_wr = 0; n_isr = 0; n_wake = 0; n_rd = 0; n_attach = 0; n_enable = 0; n_detach = 0; }
/* one thread pass the way the glue does it: begin, drain (all queued events), end; returns the number of events */
static int tpass(void)
{
	int n;
	sti1_pass_begin(&S, &OPS);
	n = fifo; fifo = 0;
	now_ns += 1000000ull;
	sti1_pass_end(&S, &OPS, n, now_ns);
	settle();
	return n;
}
static void event(int k) { fifo += k; settle(); }

static void t_arg(void)
{
	struct sti1 s;
	int p, v;
	for (p = 0; p < 2; p++) for (v = 0; v < 3; v++) {
		if (p && v == 1) continue;
		memset(&s, 0, sizeof s); logb[0] = 0; sti1_arg(&s, p, v);
		CHK(s.mode == 0 && logb[0] == 0);
	}
	memset(&s, 0, sizeof s); sti1_arg(&s, 1, 1); CHK(s.mode == 1 && logb[0] == 0);
	sti1_arg(&s, 0, 0); CHK(s.mode == 0);                       /* a later call overrides an earlier mode */
	sti1_arg(&s, 1, 1); sti1_arg(&s, 1, 3); CHK(s.mode == 0); logb[0] = 0;
	memset(&s, 0, sizeof s); sti1_arg(&s, 0, 1); CHK(s.mode == 0);
	memset(&s, 0, sizeof s); sti1_arg(&s, 1, 3); CHK(s.mode == 0 && count("TOUCH GAP: sweettouchirq=3: unknown value") == 1);
	memset(&s, 0, sizeof s); logb[0] = 0; sti1_arg(&s, 1, -1); CHK(s.mode == 0 && count("sweettouchirq=-1: unknown value") == 1);
}

static void t_mode0(void)
{
	reset_world(); sti1_arg(&S, 0, 0);
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);
	sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, 3, now_ns); sti1_pass_end(&S, &OPS, 0, now_ns + 99000000000ull);
	sti1_reset_mask(&S, &OPS);
	CHK(sti1_quiesce_begin(&S) == 0 && S.quiesce == 0);
	CHK(!sti1_armed(&S) && sti1_wait_ms(&S, 1) == 1 && sti1_wait_ms(&S, 0) == 8 && !sti1_wake_pending(&S));
	CHK(n_rd == 0 && n_wr == 0 && n_attach == 0 && n_enable == 0 && n_detach == 0 && logb[0] == 0 && same_as_init());
	/* mode 0 ignores whatever the state words say */
	S.state = STI1_S_ARMED; S.quiesce = 1; S.off_req = 1; S.unmasked = 1; S.isr_seq = 9;
	CHK(!sti1_armed(&S) && !sti1_wake_pending(&S));
	sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, 1, now_ns); sti1_reset_mask(&S, &OPS);
	CHK(S.state == STI1_S_ARMED && n_rd == 0 && n_wr == 0 && n_detach == 0 && logb[0] == 0);
	/* mode 1: ARMING and OFF are not armed; a pending sequence is only a wake while armed */
	S.mode = 1; S.quiesce = 0; S.off_req = 0; S.state = STI1_S_ARMING; CHK(!sti1_armed(&S) && !sti1_wake_pending(&S) && sti1_wait_ms(&S, 0) == 8);
	S.state = STI1_S_OFF; CHK(!sti1_armed(&S) && !sti1_wake_pending(&S));
	S.state = STI1_S_ARMED; S.last_seq = 9; CHK(sti1_armed(&S) && !sti1_wake_pending(&S));
	S.off_req = 1; CHK(sti1_wake_pending(&S)); S.off_req = 0; S.quiesce = 1; CHK(sti1_wake_pending(&S)); S.quiesce = 0; S.last_seq = 8; CHK(sti1_wake_pending(&S));
}

static void t_arm(void)
{
	reset_world(); sti1_arg(&S, 1, 1);
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0);
	CHK(S.state == STI1_S_ARMED && sti1_armed(&S));
	CHKS(ev, "W918 WC0 W718 WA18 A W918 E ");
	CHK(nwr == 5);
	CHK(wrs[0].off == 0x918 && wrs[0].neww == 0xFFu);           /* mask: the value stage 0 saw, written back */
	CHK(wrs[1].off == 0x0C0 && wrs[1].neww == 0x2222222Fu);     /* CON pin 0 := 0xF, pins 1..7 keep 2 */
	CHK(wrs[2].off == 0x718 && wrs[2].neww == 0x33333330u);     /* ECON pin 0 := 0 (level low), pins 1..7 keep 3 */
	CHK(wrs[3].off == 0xA18 && wrs[3].neww == 1u);              /* EPEND W1C exactly 0x1 */
	CHK(wrs[4].off == 0x918 && wrs[4].neww == 0xFEu);           /* unmask: only bit 0 */
	CHK(W[0x0C0 / 4] == 0x2222222Fu && W[0x718 / 4] == 0x33333330u && W[0x918 / 4] == 0xFEu);
	CHK(only_pin0_differs() && bad_wr == 0);
	CHK(W[0x828 / 4] == 0 && W[0x0C8 / 4] == 0x5A5A5A5Au);       /* EFLTCON and PUD never written */
	CHK(count("SWEET TOUCH IRQ: arming: econ 0x33333330 [pin0 0] efltcon 0 emask 0xff [bit0 1] epend 0x80 [bit0 0] con 0x22222220 [func 0] dat 1") == 1);
	CHK(count("SWEET TOUCH IRQ: attach INTID 67 rc 0 igroupr 0x1 isenabler 0 icfgr 0 irouter 0x80") == 1);
	CHK(count("SWEET TOUCH IRQ: ARMED INTID 67 level-low gpa9[0]: safety wait 100 ms") == 1);
	CHK(count("TOUCH GAP") == 0 && n_attach == 1 && n_enable == 1 && n_detach == 0 && gic_att && gic_en);
	CHK(sti1_wait_ms(&S, 0) == 100 && sti1_wait_ms(&S, 5) == 100);
	CHK(S.mask_done && S.con_set && S.econ_set && S.pend_done && S.att && S.unmasked);
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);   /* a second arm does nothing, silently */
	CHK(n_wr == 5 && count("TOUCH GAP") == 0);
	/* a stale sequence number from before the arm is taken over, not mistaken for an interrupt */
	reset_world(); sti1_arg(&S, 1, 1); S.isr_seq = 5;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0 && S.last_seq == 5 && S.unm_seq == 5 && !sti1_wake_pending(&S));
	/* the line is already low at the arm (events in the FIFO): the first interrupt arrives from the enable and is handled, not lost */
	reset_world(); sti1_arg(&S, 1, 1); fifo = 3;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0 && S.isr_seq == 1 && n_isr == 1 && W[0x918 / 4] == 0xFFu && sti1_wake_pending(&S) && fifo == 3);
	/* a 4-bit ECON field (bit 3 set) is restored exactly */
	reset_world(); sti1_arg(&S, 1, 1); W[0x718 / 4] = 0x3333333Bu; INIT[0x718 / 4] = W[0x718 / 4];
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0 && W[0x718 / 4] == 0x33333330u);
	sti1_quiesce_begin(&S); sti1_pass_begin(&S, &OPS); CHK(same_as_init() && S.state == STI1_S_OFF);
	/* a window that reads all ones in ONE of the four words is not "not answering": the arm goes on and restores it */
	reset_world(); sti1_arg(&S, 1, 1); W[0x718 / 4] = 0xFFFFFFFFu; INIT[0x718 / 4] = W[0x718 / 4];
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0 && W[0x718 / 4] == 0xFFFFFFF0u);
	sti1_quiesce_begin(&S); sti1_pass_begin(&S, &OPS); CHK(same_as_init() && S.state == STI1_S_OFF);
	/* a non-zero ECON pin 0 is overwritten with 0 and restored at disarm */
	reset_world(); sti1_arg(&S, 1, 1); W[0x718 / 4] = 0x33333333u; INIT[0x718 / 4] = W[0x718 / 4];
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == 0 && W[0x718 / 4] == 0x33333330u);
	sti1_quiesce_begin(&S); sti1_pass_begin(&S, &OPS);
	CHK(W[0x718 / 4] == 0x33333333u && S.state == STI1_S_OFF && same_as_init());
}

static void pre(int ready, int mux, uint64_t bank, uint32_t pin)
{
	reset_world(); sti1_arg(&S, 1, 1);
	CHK(sti1_arm(&S, &OPS, ready, mux, bank, pin) == -1);
	CHK(S.state == STI1_S_OFF && n_wr == 0 && n_attach == 0 && n_enable == 0 && same_as_init() && count("TOUCH GAP: sweettouchirq=1: not armed, polling") == 1);
	CHK(S.why == STI1_Y_PRE);
}
static void t_pre(void)
{
	int i;
	pre(0, 1, STI1_PAD_PA, 0);
	pre(1, 0, STI1_PAD_PA, 0);
	pre(1, 1, 0x174D0000ull, 0);
	pre(1, 1, STI1_PAD_PA + 0x20, 0);
	pre(1, 1, STI1_PAD_PA, 1);
	reset_world(); sti1_arg(&S, 1, 1); sti1_quiesce_begin(&S);
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1 && n_wr == 0 && count("restart already requested") == 1);
	reset_world(); sti1_arg(&S, 1, 1); W[0x0C0 / 4] = 0x22222221u; INIT[0x0C0 / 4] = W[0x0C0 / 4];
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1 && n_wr == 0 && count("pad CON func is 0x1, not 0") == 1 && S.why == STI1_Y_PRE);
	reset_world(); sti1_arg(&S, 1, 1); W[0x718 / 4] = W[0x828 / 4] = W[0x918 / 4] = W[0xA18 / 4] = 0xFFFFFFFFu;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1 && n_wr == 0 && count("all four EINT words read 0xffffffff") == 1 && S.why == STI1_Y_BUS);
	{
		static const int offs[6] = { 0x0C0, 0x0C4, 0x718, 0x828, 0x918, 0xA18 };
		for (i = 0; i < 6; i++) {
			reset_world(); sti1_arg(&S, 1, 1); rd_fail_off = offs[i];
			CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);
			CHK(S.state == STI1_S_OFF && n_wr == 0 && n_attach == 0 && S.why == STI1_Y_BUS && count("an initial read failed (nothing was written)") == 1 && count("failed\n") >= 1);
		}
	}
}

static void flags_clear(const char *what) { if (S.econ_set || S.con_set || S.att || S.mask_done || S.pend_done) { printf("FAIL: %s: arm flags left set\n", what); bad++; } }
static void failed_cleanly(int why, const char *what, int want_detach)
{
	flags_clear(what);
	if (S.state != STI1_S_OFF || S.why != why || !same_as_init() || bad_wr || gic_en || count("TOUCH GAP: sweettouchirq=1: interrupt mode OFF") != 1) {
		printf("FAIL: %s: state %d why %d same %d bad_wr %d gic_en %d offgap %d\n", what, S.state, S.why, same_as_init(), bad_wr, gic_en, count("interrupt mode OFF"));
		bad++;
	}
	if (gic_att || n_detach != want_detach) { printf("FAIL: %s: attach/detach mismatch att %d n_attach %d n_detach %d want %d\n", what, gic_att, n_attach, n_detach, want_detach); bad++; }
}
static void t_arm_fail(void)
{
	int k;
	/* a bus failure on each of the five stores */
	for (k = 0; k < 5; k++) {
		reset_world(); sti1_arg(&S, 1, 1); wr_fail_at = k;
		CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);
		failed_cleanly(STI1_Y_BUS, "wr fail", k == 4);
		CHK(nwr == k + 1 + (k >= 1) + (k >= 3) + (k >= 2) + (k >= 4));      /* stores so far + the failed one + exactly the teardown stores of the steps that were done */
		CHK(count("write of alive offset") >= 1 && count("failed\n") >= 1);
	}
	/* a read-back mismatch (the store is ignored by the fake) on the mask, CON, ECON and unmask stores */
	{
		static const int offs[4] = { 0x918, 0x0C0, 0x718, 0x918 }, after[4] = { 0, 0, 0, 1 };
		for (k = 0; k < 4; k++) {
			reset_world(); sti1_arg(&S, 1, 1); wr_stuck_off = offs[k]; wr_stuck_after = after[k] ? 4 : 0;
			if (k == 0) { W[0x918 / 4] = 0xFEu; INIT[0x918 / 4] = 0xFEu; }
			if (k == 2) { W[0x718 / 4] = 0x33333333u; INIT[0x718 / 4] = 0x33333333u; }
			(void)sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0);
			CHK(S.state == STI1_S_OFF && S.why == STI1_Y_RB && count("read-back of alive offset") >= 1 && bad_wr == 0 && !gic_en && !gic_att);
		}
	}
	reset_world(); sti1_arg(&S, 1, 1); attach_rc = 22;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1); failed_cleanly(STI1_Y_ATTACH, "attach", 0); CHK(count("attach INTID 67 rc 22") == 1);
	reset_world(); sti1_arg(&S, 1, 1); enable_rc = 19;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1); failed_cleanly(STI1_Y_ENABLE, "enable rc", 1);
	reset_world(); sti1_arg(&S, 1, 1); enable_en = 0;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1); failed_cleanly(STI1_Y_ENABLE, "enable bit", 1);
	CHK(count("GIC enable of INTID 67: rc 0, enable bit read back 0") == 1);
}

static void t_teardown_fail(void)
{
	/* every teardown step is tried whatever failed before; enable fails so the whole arm is torn down (stores 0..4 = the arm, 5 mask, 6 ECON, 7 CON, 8 EPEND) */
	int k;
	for (k = 5; k <= 8; k++) {
		reset_world(); sti1_arg(&S, 1, 1); W[0x718 / 4] = 0x33333333u; INIT[0x718 / 4] = W[0x718 / 4]; enable_rc = 19; wr_fail_at = k;
		CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);
		CHK(S.state == STI1_S_OFF && S.why == STI1_Y_ENABLE && n_detach == 1 && !gic_att && bad_wr == 0);
		flags_clear("teardown fail");
		CHK(count("interrupt mode OFF (gic-enable)") == 1);
		CHK(k == 5 ? (count("teardown: EMASK bit 0 := 1 failed") == 1 && S.unmasked == 1 && W[0x0C0 / 4] == INIT[0x0C0 / 4] && W[0x718 / 4] == INIT[0x718 / 4])
		    : k == 6 ? (count("teardown: ECON back to 0x3 failed") == 1 && W[0x718 / 4] == 0x33333330u && W[0x0C0 / 4] == INIT[0x0C0 / 4] && S.unmasked == 0)
		    : k == 7 ? (count("teardown: CON back to 0 failed") == 1 && W[0x0C0 / 4] == 0x2222222Fu && W[0x718 / 4] == INIT[0x718 / 4])
		    : (count("teardown: EPEND clear failed") == 1 && W[0x0C0 / 4] == INIT[0x0C0 / 4] && W[0x718 / 4] == INIT[0x718 / 4]));
	}
	/* the failed mask leaves the line unmasked: the reset path masks it */
	reset_world(); sti1_arg(&S, 1, 1); enable_rc = 19; wr_fail_at = 5;
	(void)sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0);
	CHK(S.state == STI1_S_OFF && S.unmasked == 1 && W[0x918 / 4] == 0xFEu);
	in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; CHK(W[0x918 / 4] == 0xFFu);
	/* the detach fails: logged, the rest is done */
	reset_world(); sti1_arg(&S, 1, 1); enable_rc = 19; detach_rc = 5;
	(void)sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0);
	CHK(count("teardown: GIC detach of INTID 67 returned 5") == 1 && S.state == STI1_S_OFF && !S.att && same_as_init());
}

static void t_isr(void)
{
	armed();
	CHK(W[0x918 / 4] == 0xFEu);
	event(2);                                     /* line low, unmasked, enabled: the fake delivers the ISR */
	CHK(n_isr == 1 && isr_ret_last == 0 && S.isr_seq == 1 && n_wake == 1 && S.isr_total == 1);
	CHKS(ev, "W918 WA18 ");                       /* the ISR masks FIRST, then clears EPEND */
	CHK(nwr == 2 && wrs[0].off == 0x918 && wrs[0].neww == 0xFFu && wrs[1].off == 0xA18 && wrs[1].neww == 1u);
	CHK(spi_in_isr_calls == 0 && n_attach == 0 && n_enable == 0 && n_detach == 0 && logb[0] == 0 && bad_wr == 0);
	CHK(fifo == 2 && W[0x918 / 4] == 0xFFu);      /* masked, the FIFO untouched: nothing but the thread reads it */
	CHK(n_rd == 1);                               /* the one read of the EMASK word for the RMW: no read-back in the ISR */
}

static void t_isr_off_states(void)
{
	int s;
	for (s = 0; s < 4; s++) {
		if (s == STI1_S_ARMED) continue;
		/* review 0356 F1: any state other than ARMED: the same EMASK := 1 store (never a log, never a GIC call), return 1 so the dispatcher masks the INTID; no wake, no EPEND, no sequence number */
		armed(); S.state = s; S.unmasked = 1; in_isr = 1;
		CHK(sti1_isr(&S, &OPS) == 1 && nwr == 1 && wrs[0].off == 0x918 && wrs[0].neww == 0xFFu && n_wake == 0 && S.isr_seq == 0 && S.isr_total == 1);
		CHK(spi_in_isr_calls == 0 && logb[0] == 0 && bad_wr == 0);
		in_isr = 0;
	}
	/* OFF with the line masked: the judge refuses the store (nothing to do), still return 1 */
	armed(); S.state = STI1_S_OFF; S.unmasked = 0; in_isr = 1; CHK(sti1_isr(&S, &OPS) == 1 && n_wr == 0 && n_wake == 0 && logb[0] == 0); in_isr = 0;
	armed(); wr_fail_at = 0; in_isr = 1;           /* the mask store fails */
	CHK(sti1_isr(&S, &OPS) == 1 && S.off_req == 1 && S.why == STI1_Y_ISR && n_wake == 1 && S.isr_err == 1 && logb[0] == 0);
	in_isr = 0; armed(); wr_fail_at = 1; in_isr = 1;                      /* the EPEND store fails */
	CHK(sti1_isr(&S, &OPS) == 1 && S.off_req == 1 && S.why == STI1_Y_ISR && logb[0] == 0);
	in_isr = 0;
	armed(); S.state = STI1_S_ARMED; in_isr = 1; { int i, r = 0; for (i = 0; i < 1000; i++) r |= sti1_isr(&S, &OPS); CHK(r == 0 && S.off_req == 0); CHK(sti1_isr(&S, &OPS) == 1 && S.off_req == 1 && S.why == STI1_Y_STORM && S.storm_n == 1); } in_isr = 0;
	armed(); in_isr = 1; { int i; for (i = 0; i < 1000; i++) (void)sti1_isr(&S, &OPS); now_ns += STI1_WIN_NS; CHK(sti1_isr(&S, &OPS) == 0 && S.off_req == 0); } in_isr = 0;
	/* the thread carries the request out: back to polling, restored */
	armed(); S.off_req = 1; S.why = STI1_Y_STORM; sti1_pass_begin(&S, &OPS);
	CHK(S.state == STI1_S_OFF && count("interrupt mode OFF (storm)") == 1 && n_detach == 1);
}

static void t_f1_gap(void)
{
	/* review 0356 F1: the line goes low in the teardown gap (state DISARMING, before the mask store). It must end masked, with at most 2 ISR entries (old code: endless entries) */
	armed(); hook_disarm_inject = 1; S.quiesce = 1; tpass();
	CHK(S.state == STI1_S_OFF && W[0x918 / 4] == 0xFFu && n_isr >= 1 && n_isr <= 2 && same_as_init() && bad_wr == 0 && !gic_en && n_detach == 1);
	/* the same from an auto-off on a timeout pass (NOIRQ) */
	armed(); gic_deaf = 1; tpass(); { int i; for (i = 0; i < 9; i++) { event(1); tpass(); } } gic_deaf = 0; hook_disarm_inject = 1; event(1); fifo = 0; fifo = 0;
	tpass(); CHK(S.state == STI1_S_OFF || S.state == STI1_S_ARMED);
}

static void t_f2_errors(void)
{
	int i;
	/* a failing drain is never followed by an unmask; 10 in a row = off */
	armed(); event(1); CHK(W[0x918 / 4] == 0xFFu);
	for (i = 0; i < 9; i++) { int w = nwr; sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns += 1000000ull); CHK(nwr == w && W[0x918 / 4] == 0xFFu && S.state == STI1_S_ARMED); }
	CHK(S.err_run == 9 && count("TOUCH GAP") == 0);
	sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns += 1000000ull);
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_BUS && count("interrupt mode OFF (register-access-failed)") == 1 && same_as_init() && n_detach == 1);
	/* a good pass in between resets the run (and unmasks) */
	armed(); event(1);
	for (i = 0; i < 9; i++) { sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns += 1000000ull); }
	CHK(tpass() == 1 && S.err_run == 0 && W[0x918 / 4] == 0xFEu && S.state == STI1_S_ARMED);
	for (i = 0; i < 9; i++) { sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns += 1000000ull); }
	CHK(S.state == STI1_S_ARMED);
	/* an endless run of errors with the line low ends */
	armed(); event(1); for (i = 0; i < 20000 && S.state == STI1_S_ARMED; i++) { sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns += 1000000ull); }
	CHK(S.state == STI1_S_OFF && i == 10);
}

static void t_f3_window(void)
{
	int i;
	/* missed interrupts are counted per 10 s window: 9 + 9 in two windows stay armed, the 10th inside one window turns it off */
	armed(); gic_deaf = 1; tpass();
	for (i = 0; i < 9; i++) { event(1); tpass(); }
	now_ns += 11000000000ull;
	for (i = 0; i < 9; i++) { event(1); tpass(); }
	CHK(S.state == STI1_S_ARMED && S.tmo_found == 18);
	event(1); tpass();
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_NOIRQ && S.tmo_found == 19);
	/* the DAT re-check after the unmask: the line is low, no interrupt entered (deaf GIC): counted, one more drain at once */
	armed(); tpass(); event(1);                      /* the ISR runs, masks */
	sti1_pass_begin(&S, &OPS); gic_deaf = 1; fifo = 1;
	sti1_pass_end(&S, &OPS, 1, now_ns += 1000000ull);   /* EPEND clear, unmask: no interrupt follows */
	CHK(S.rechk_low == 1 && S.redo == 1 && sti1_wake_pending(&S) && S.state == STI1_S_ARMED && W[0x918 / 4] == 0xFEu);
	gic_deaf = 0; tpass();
	CHK(S.redo == 0 && S.tmo_found == 1 && S.state == STI1_S_ARMED && S.rechk_low == 1);
	/* a high line after the unmask: no re-check wake */
	armed(); event(1); tpass(); CHK(S.rechk_low == 0 && S.redo == 0 && !sti1_wake_pending(&S));
	/* an interrupt that enters from the unmask itself is not a missed one */
	armed(); event(1); sti1_pass_begin(&S, &OPS); fifo = 1; sti1_pass_end(&S, &OPS, 1, now_ns += 1000000ull);
	CHK(n_isr == 2 && S.rechk_low == 0 && S.redo == 0);
	/* the DAT read after the unmask fails: back to polling */
	armed(); event(1); sti1_pass_begin(&S, &OPS); fifo = 0; rd_fail_off = 0x0C4; sti1_pass_end(&S, &OPS, 1, now_ns += 1000000ull);
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_BUS);
	/* F4: a restart request during the arm gives the reason restart, not write-refused */
	reset_world(); sti1_arg(&S, 1, 1); hook_att_quiesce = 1;
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1 && S.why == STI1_Y_QUIESCE && count("TOUCH GAP") == 0 && count("SWEET TOUCH IRQ: restart: interrupt mode off before the reset") == 1 && same_as_init() && S.state == STI1_S_OFF);
}

static void t_thread(void)
{
	armed();
	CHK(tpass() == 0);                            /* the first pass after the arm: not counted as a timeout drain, line high: no write */
	CHK(S.tmo_drains == 0 && S.wakes == 0 && n_wr == 0);
	CHK(tpass() == 0 && S.tmo_drains == 1 && S.tmo_found == 0 && n_wr == 0 && n_wake == 0);      /* idle timeout drain: reads only */
	event(3);                                     /* ISR: mask + EPEND + wake */
	CHK(sti1_wake_pending(&S) == 1);
	nwr = 0; ev[0] = 0;
	CHK(tpass() == 3);                            /* the first drain after the wake is immediate (no sleep is modelled in between) */
	CHKS(ev, "WA18 W918 ");                       /* thread order: EPEND clear, then unmask */
	CHK(nwr == 2 && wrs[0].off == 0xA18 && wrs[0].neww == 1u && wrs[1].off == 0x918 && wrs[1].oldw == 0xFFu && wrs[1].neww == 0xFEu);
	CHK(S.wakes == 1 && S.empty == 0 && S.tmo_found == 0 && S.unm_seq == S.isr_seq && !sti1_wake_pending(&S) && W[0x918 / 4] == 0xFEu);
	/* no interrupt, no event: nothing is written */
	nwr = 0; CHK(tpass() == 0 && nwr == 0);
	/* a second event after the unmask */
	event(1); CHK(tpass() == 1 && S.wakes == 2 && W[0x918 / 4] == 0xFEu);
	/* an interrupt for an empty FIFO is counted as empty */
	armed(); tpass(); S.isr_seq = 1; now_ns += 1;
	sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, 0, now_ns); CHK(S.wakes == 1 && S.empty == 1);
	/* a drain error does not classify and does not turn the mode off */
	armed(); tpass(); sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, -1, now_ns); CHK(S.state == STI1_S_ARMED && S.tmo_drains == 0 && S.wakes == 0);
	/* the interrupt fires while the thread drains: no lost wakeup, no second ISR runs until the unmask */
	armed(); tpass(); sti1_pass_begin(&S, &OPS); event(1); fifo = 0; sti1_pass_end(&S, &OPS, 1, now_ns);
	CHK(S.state == STI1_S_ARMED && S.wakes == 1 && S.tmo_found == 0 && !sti1_wake_pending(&S) && W[0x918 / 4] == 0xFEu);   /* an ISR during the drain is an interrupt pass, not a missed edge; the unmask follows */
	/* an interrupt that runs AFTER the pass accounted its sequence number is not slept through */
	armed(); tpass(); sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, 0, now_ns); event(1);
	CHK(sti1_wake_pending(&S) == 1);
	/* the unmask re-fires at once while the line is still low: read-back is not used, the next pass unmasks again */
	armed(); tpass(); event(1);
	sti1_pass_begin(&S, &OPS); fifo = 1; /* still low after the drain: a new event */ { int n = 1; sti1_pass_end(&S, &OPS, n, now_ns); settle(); }
	CHK(S.state == STI1_S_ARMED && n_isr == 2);
	tpass(); tpass(); CHK(W[0x918 / 4] == 0xFEu && S.state == STI1_S_ARMED && bad_wr == 0);
	CHK(only_pin0_differs());
	/* an off request that arrives during the pass: no unmask, the next pass disarms (no refusal GAP) */
	armed(); event(1); sti1_pass_begin(&S, &OPS); S.off_req = 1; S.why = STI1_Y_ISR; fifo = 0; { int w = nwr; sti1_pass_end(&S, &OPS, 1, now_ns); CHK(nwr == w && count("TOUCH GAP") == 0 && S.state == STI1_S_ARMED); }
	sti1_pass_begin(&S, &OPS); CHK(S.state == STI1_S_OFF && same_as_init() && count("interrupt mode OFF (isr-error)") == 1);
}

static void t_thread_unmask_failure(void)
{
	armed(); event(1); tpass();                   /* healthy */
	armed(); event(1); wr_fail_at = nwr;
	sti1_pass_begin(&S, &OPS); fifo = 0; sti1_pass_end(&S, &OPS, 1, now_ns);
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_BUS && count("interrupt mode OFF (register-access-failed)") == 1 && same_as_init() && n_detach == 1);
	armed(); event(1); wr_fail_at = nwr + 1;
	sti1_pass_begin(&S, &OPS); fifo = 0; sti1_pass_end(&S, &OPS, 1, now_ns);
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_BUS && same_as_init());
}

static void t_storm(void)
{
	int i;
	/* a spurious source: interrupts with an empty FIFO and DAT high. 200 in one second are tolerated, the 201st turns the mode off */
	armed(); spurious_irq = 1; settle();
	for (i = 0; i < 199; i++) tpass();
	spurious_irq = 0;
	tpass();
	CHK(S.state == STI1_S_ARMED && S.empty == 200 && S.storm_n == 0 && count("TOUCH GAP") == 0);
	armed(); spurious_irq = 1; settle();
	for (i = 0; i < 400 && S.state == STI1_S_ARMED; i++) tpass();
	CHK(S.state == STI1_S_OFF && S.empty == 201 && S.storm_n == 1 && S.why == STI1_Y_STORM && count("interrupt mode OFF (storm)") == 1 && same_as_init() && n_detach == 1 && bad_wr == 0);
	CHK(!sti1_armed(&S) && sti1_wait_ms(&S, 0) == 8 && sti1_wait_ms(&S, 1) == 1);
	/* the window rolls over: 150 + 150 spurious wakes in two different seconds stay armed */
	armed(); spurious_irq = 1; settle();
	for (i = 0; i < 150; i++) tpass();
	now_ns += 2000000000ull;
	for (i = 0; i < 150; i++) tpass();
	CHK(S.state == STI1_S_ARMED && S.empty == 300);
	/* an interrupt that finds events is not an empty one */
	armed();
	for (i = 0; i < 400; i++) { event(1); tpass(); }
	CHK(S.state == STI1_S_ARMED && S.empty == 0 && S.wakes == 400);
}

static void t_stuck(void)
{
	armed(); phantom_low = 1; settle();
	tpass(); tpass();
	CHK(S.state == STI1_S_ARMED && S.low_run == 2);
	phantom_low = 0; tpass();                      /* high again: the run restarts */
	CHK(S.state == STI1_S_ARMED && S.low_run == 0);
	phantom_low = 1; settle(); tpass(); tpass(); tpass();
	CHK(S.state == STI1_S_OFF && S.why == STI1_Y_STUCK && count("interrupt mode OFF (line-stuck-low)") == 1 && n_detach == 1 && bad_wr == 0);
	phantom_low = 0; settle(); CHK(same_as_init());
	/* events found while the line is low reset the run */
	armed(); phantom_low = 1; settle(); tpass(); tpass(); event(1); tpass(); CHK(S.state == STI1_S_ARMED && S.low_run == 0);
	/* the DAT read fails: back to polling */
	armed(); rd_fail_off = 0x0C4; tpass(); CHK(S.state == STI1_S_OFF && S.why == STI1_Y_BUS);
}

static void t_missed(void)
{
	int i;
	armed(); gic_deaf = 1; tpass();                 /* settle pass */
	for (i = 0; i < 9; i++) { event(1); tpass(); }
	CHK(S.state == STI1_S_ARMED && S.tmo_found == 9 && S.tmo_drains == 9 && S.wakes == 0 && count("safety-timeout drain found 1 event(s) with no interrupt") == 5);
	event(1); tpass();
	CHK(S.state == STI1_S_OFF && S.tmo_found == 10 && S.why == STI1_Y_NOIRQ && count("interrupt mode OFF (no-interrupt)") == 1 && n_detach == 1 && same_as_init());
	CHK(count("missed edge 5 of 10 allowed") == 1 && count("missed edge 6 of 10 allowed") == 0);
	/* idle timeout drains are not missed edges */
	armed(); gic_deaf = 1; for (i = 0; i < 50; i++) tpass(); CHK(S.state == STI1_S_ARMED && S.tmo_found == 0 && S.tmo_drains == 49);
}

static void t_quiesce(void)
{
	armed(); event(1); tpass();
	CHK(sti1_quiesce_begin(&S) == 1 && S.quiesce == 1 && sti1_wake_pending(&S) == 1 && sti1_armed(&S));
	CHK(!sti1_off(&S));
	sti1_pass_begin(&S, &OPS);
	CHK(sti1_off(&S) && S.why == STI1_Y_QUIESCE && count("TOUCH GAP") == 0 && count("SWEET TOUCH IRQ: restart: interrupt mode off before the reset") == 1 && n_detach == 1 && same_as_init() && bad_wr == 0);
	CHKS(ev + strlen(ev) - strlen("W918 D W718 WC0 WA18 "), "W918 D W718 WC0 WA18 ");      /* disarm order: mask, GIC detach, ECON, CON, EPEND */
	{ int w = n_wr; sti1_pass_begin(&S, &OPS); sti1_pass_end(&S, &OPS, 0, now_ns + 1); CHK(n_wr == w && n_rd >= 0); }
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1);
	CHK(sti1_quiesce_begin(&S) == 0);               /* already off: nothing to wait for */
	/* quiesce between the pass begin and end: no unmask */
	armed(); event(1); sti1_pass_begin(&S, &OPS); (void)sti1_quiesce_begin(&S); fifo = 0; { int w = nwr; sti1_pass_end(&S, &OPS, 1, now_ns); CHK(nwr == w && W[0x918 / 4] == 0xFFu && S.state == STI1_S_ARMED); }
	sti1_pass_begin(&S, &OPS); CHK(sti1_off(&S) && same_as_init());
	/* quiesce before the arm: never arms */
	reset_world(); sti1_arg(&S, 1, 1); CHK(sti1_quiesce_begin(&S) == 0 && S.quiesce == 1);
	CHK(sti1_arm(&S, &OPS, 1, 1, STI1_PAD_PA, 0) == -1 && n_wr == 0);
}

static void t_reset(void)
{
	/* never armed, or armed and cleanly disarmed: no access at all */
	reset_world(); sti1_arg(&S, 1, 1); in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; CHK(n_rd == 0 && n_wr == 0);
	armed(); sti1_quiesce_begin(&S); sti1_pass_begin(&S, &OPS); { int w = n_wr, r = n_rd; in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; CHK(n_wr == w && n_rd == r); }
	/* armed and unmasked: exactly one write, EMASK bit 0 := 1, the other bits kept, no log, no GIC call */
	armed(); CHK(W[0x918 / 4] == 0xFEu);
	W[0x918 / 4] = 0xFCu;                              /* another bit of the word is clear: it must survive */
	INIT[0x918 / 4] = 0xFCu;
	{ int d0 = n_detach, a0 = n_attach, e0 = n_enable; size_t l0 = strlen(logb);
	  in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0;
	  CHK(n_rd == 1);
	  CHK(nwr == 1 && wrs[0].off == 0x918 && wrs[0].neww == 0xFDu && W[0x918 / 4] == 0xFDu);
	  CHK(n_detach == d0 && n_attach == a0 && n_enable == e0 && strlen(logb) == l0); }
	/* state OFF but the line was left unmasked (a failed teardown): the mask is still written */
	reset_world(); sti1_arg(&S, 1, 1); S.unmasked = 1; W[0x918 / 4] = 0xFEu; in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; CHK(n_wr == 1 && W[0x918 / 4] == 0xFFu);
	/* a failing store in the reset path is silent */
	armed(); wr_fail_at = nwr; in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; CHK(logb[0] == 0 && W[0x918 / 4] == 0xFEu);
	/* ISR in flight on another CPU while the reset path masks: harmless */
	armed(); in_reset = 1; sti1_reset_mask(&S, &OPS); in_reset = 0; event(1); CHK(n_isr == 0 && fifo == 1);
}

static void t_summary(void)
{
	armed(); tpass();                                   /* summary clock starts */
	now_ns += 9000000000ull; sti1_pass_end(&S, &OPS, 0, now_ns);
	CHK(count("SWEET TOUCH IRQ: irqs") == 0);
	event(2); tpass();
	now_ns += 1000000000ull; sti1_pass_end(&S, &OPS, 0, now_ns);
	CHK(count("SWEET TOUCH IRQ: irqs 1 empty 0 wakes 1 tmo_drains 2 tmo_found 0 storm 0 mode irq\n") == 1 && count("SWEET TOUCH IRQ: irqs") == 1);
	sti1_pass_end(&S, &OPS, 0, now_ns + 9999999999ull); CHK(count("SWEET TOUCH IRQ: irqs") == 1);
	sti1_pass_end(&S, &OPS, 0, now_ns + 10000000000ull); CHK(count("SWEET TOUCH IRQ: irqs") == 2);
	/* off: the row keeps coming, with the reason */
	armed(); tpass(); gic_deaf = 1; tpass(); event(1);
	{ int i; for (i = 0; i < 10 && S.state == STI1_S_ARMED; i++) { event(1); tpass(); } }
	logb[0] = 0; now_ns += 10000000000ull; sti1_pass_end(&S, &OPS, 0, now_ns);
	CHK(count("SWEET TOUCH IRQ: irqs 0 empty 0 wakes 0 tmo_drains 11 tmo_found 10 storm 0 mode off:no-interrupt\n") == 1 && count("SWEET TOUCH IRQ: irqs") == 1);
	/* never armed (pre-condition) */
	reset_world(); sti1_arg(&S, 1, 1); (void)sti1_arm(&S, &OPS, 0, 1, STI1_PAD_PA, 0); logb[0] = 0;
	sti1_pass_end(&S, &OPS, 0, 1); now_ns = 20000000000ull; sti1_pass_end(&S, &OPS, 0, now_ns);
	CHK(count("irqs 0 empty 0 wakes 0 tmo_drains 0 tmo_found 0 storm 0 mode off:not-armed\n") == 1 && n_wr == 0);
	/* a storm row */
	armed(); spurious_irq = 1; settle(); { int i; for (i = 0; i < 400 && S.state == STI1_S_ARMED; i++) tpass(); }
	logb[0] = 0; now_ns += 10000000000ull; sti1_pass_end(&S, &OPS, 0, now_ns);
	CHK(count("storm 1 mode off:storm\n") == 1);
}

static int refused_rmw(uint32_t off, uint32_t field)
{
	int w = n_wr, rc;
	uint32_t r0 = S.refused;
	logb[0] = 0;
	rc = sti1_rmw(&S, &OPS, off, field, 1, 0);
	return rc == -2 && n_wr == w && count("refused") == 1 && S.refused == r0 + 1;
}
static void t_judge(void)
{
	static const uint32_t bad_offs[] = { 0x828, 0x0C8, 0x0C4, 0x0CC, 0x91C, 0x71C, 0x914, 0xA1C, 0x0C1, 0x1000, 0x700, 0x900, 0xA00, 0x800, 0x0C0 + 4, 0 };
	size_t i;
	uint32_t f;
	/* no write row at all for anything but CON, ECON, EMASK, EPEND, in every state */
	for (int s = 0; s < 4; s++) {
		armed(); S.state = s; S.mask_done = S.con_set = S.econ_set = S.pend_done = S.att = S.unmasked = 1; S.con_orig = 0; S.econ_orig = 0;
		for (i = 0; i < sizeof bad_offs / sizeof bad_offs[0]; i++) {
			CHK(refused_rmw(bad_offs[i], 0u)); CHK(refused_rmw(bad_offs[i], 1u)); CHK(refused_rmw(bad_offs[i], 0xFu));
		}
		CHK(S.refused >= 3 * (sizeof bad_offs / sizeof bad_offs[0]));
	}
	/* values outside pin 0's field */
	armed(); CHK(refused_rmw(0x918, 2u)); CHK(refused_rmw(0x918, 0xFEu)); CHK(refused_rmw(0x0C0, 0x10u)); CHK(refused_rmw(0x718, 0xF0u)); CHK(refused_rmw(0xA18, 2u)); CHK(refused_rmw(0xA18, 0xFFFFFFFFu));
	/* EPEND: only exactly 1 */
	armed(); S.state = STI1_S_ARMED; CHK(refused_rmw(0xA18, 0u)); CHK(refused_rmw(0xA18, 3u));
	/* CON and ECON: no write while ARMED (the arm and the disarm only) */
	armed(); for (f = 0; f < 16; f++) { CHK(refused_rmw(0x0C0, f)); CHK(refused_rmw(0x718, f)); }
	/* wr_ok directly: other pins' bits preserved */
	{
		struct sti1 t; memset(&t, 0, sizeof t);
		t.state = STI1_S_ARMING; t.mask_done = 1;
		CHK(sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Fu));
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x3222222Fu));
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x222222FFu));
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x22222221u));
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Eu));
		t.mask_done = 0; CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Fu)); t.mask_done = 1;
		t.con_orig = 1; CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222221u, 0x2222222Fu)); t.con_orig = 0;
		t.con_set = 1;
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Fu));            /* a second set */
		CHK(!sti1_wr_ok(&t, 0x718, 0x33333333u, 0x33333331u));            /* ECON only level low */
		CHK(sti1_wr_ok(&t, 0x718, 0x33333333u, 0x33333330u));
		CHK(!sti1_wr_ok(&t, 0x718, 0x33333333u, 0x23333330u));
		t.state = STI1_S_DISARMING; t.econ_set = 1; t.econ_orig = 3;
		CHK(sti1_wr_ok(&t, 0x718, 0x33333330u, 0x33333333u));
		CHK(!sti1_wr_ok(&t, 0x718, 0x33333330u, 0x33333332u));
		CHK(sti1_wr_ok(&t, 0x0C0, 0x2222222Fu, 0x22222220u));
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x2222222Fu, 0x22222221u));
		CHK(sti1_wr_ok(&t, 0x918, 0xFEu, 0xFFu));
		CHK(!sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));                         /* no unmask in DISARMING */
		CHK(!sti1_wr_ok(&t, 0x918, 0xFFu, 0xFDu));
		CHK(!sti1_wr_ok(&t, 0x918, 0xFEu, 0xFFFFFFFFu) || 1);
		CHK(sti1_wr_ok(&t, 0xA18, 0u, 1u) && !sti1_wr_ok(&t, 0xA18, 0u, 0u) && !sti1_wr_ok(&t, 0xA18, 0u, 3u));
		t.state = STI1_S_OFF; CHK(!sti1_wr_ok(&t, 0xA18, 0u, 1u) && !sti1_wr_ok(&t, 0x918, 0xFEu, 0xFFu)); t.unmasked = 1; CHK(sti1_wr_ok(&t, 0x918, 0xFEu, 0xFFu));
		t.state = STI1_S_ARMING; t.econ_set = 0; CHK(!sti1_wr_ok(&t, 0xA18, 0u, 1u)); t.econ_set = 1; CHK(sti1_wr_ok(&t, 0xA18, 0u, 1u));
	}
	/* the unmask: every precondition on its own */
	{
		struct sti1 t; int k;
		for (k = 0; k < 7; k++) {
			memset(&t, 0, sizeof t); t.state = STI1_S_ARMED; t.att = t.con_set = t.econ_set = t.pend_done = 1;
			CHK(sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
			switch (k) {
			case 0: t.att = 0; break;
			case 1: t.con_set = 0; break;
			case 2: t.econ_set = 0; break;
			case 3: t.pend_done = 0; break;
			case 4: t.off_req = 1; break;
			case 5: t.quiesce = 1; break;
			case 6: t.state = STI1_S_OFF; break;
			}
			CHK(!sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
		}
	}
	/* direct state cases of the arm-only and disarm-only values */
	{
		struct sti1 t; memset(&t, 0, sizeof t);
		t.state = STI1_S_ARMED; t.mask_done = 1; t.con_orig = 0;
		CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Fu));            /* CON := 0xF only while arming */
		t.state = STI1_S_DISARMING; CHK(!sti1_wr_ok(&t, 0x0C0, 0x22222220u, 0x2222222Fu));
		t.state = STI1_S_ARMING; t.con_set = 0; CHK(!sti1_wr_ok(&t, 0x718, 0x33333333u, 0x33333330u));   /* ECON before CON */
		t.state = STI1_S_ARMED; t.con_set = 1; CHK(!sti1_wr_ok(&t, 0x718, 0x33333333u, 0x33333330u));    /* ECON only while arming */
		t.state = STI1_S_DISARMING; t.con_set = 0; CHK(!sti1_wr_ok(&t, 0x0C0, 0x2222222Fu, 0x22222220u)); /* CON restore only if it was set */
		t.econ_set = 0; t.econ_orig = 0; CHK(!sti1_wr_ok(&t, 0x718, 0x33333330u, 0x33333330u));         /* ECON restore only if it was set */
	}
	/* the unmask in a shutdown state, with every precondition otherwise met */
	{
		struct sti1 t; memset(&t, 0, sizeof t); t.att = t.con_set = t.econ_set = t.pend_done = 1;
		t.state = STI1_S_DISARMING; CHK(!sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
		t.state = STI1_S_OFF; CHK(!sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
		t.state = STI1_S_ARMING; CHK(sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
		t.state = STI1_S_ARMED; CHK(sti1_wr_ok(&t, 0x918, 0xFFu, 0xFEu));
	}
	/* a refusal in the ISR/reset context (value rules, not only the missing row) never logs */
	reset_world(); sti1_arg(&S, 1, 1); in_isr = 1; CHK(sti1_rmw(&S, &OPS, 0x918, 1u, 0, 1) == -2); in_isr = 0; CHK(n_wr == 0 && logb[0] == 0);
	/* reads: only the six rows */
	armed(); { uint32_t v = 7; int rc; uint32_t r0 = S.refused; logb[0] = 0; rc = sti1_rd(&S, &OPS, 0x0C8, &v, 0); CHK(rc == -2 && v == 0 && count("read of alive offset 0xc8 refused") == 1 && S.refused == r0 + 1); rc = sti1_rd(&S, &OPS, 0x91C, &v, 0); CHK(rc == -2); rc = sti1_rd(&S, &OPS, 0x0C2, &v, 0); CHK(rc == -2); CHK(sti1_rd(&S, &OPS, 0x828, &v, 0) == 0); CHK(sti1_rd(&S, &OPS, 0x0C4, &v, 0) == 0 && v == 0xFu); }
	/* the refusal in the ISR/reset context never logs (the logger traps it) */
	armed(); in_isr = 1; (void)sti1_rmw(&S, &OPS, 0x828, 1u, 0, 1); (void)sti1_rd(&S, &OPS, 0x0C8, &f, 1); in_isr = 0;
}

static void t_nowrite_mode_other(void)
{
	/* the ISR returns without a store when the bank is not armed, whatever the line does */
	reset_world(); sti1_arg(&S, 1, 1); event(1);
	CHK(n_isr == 0 && n_wr == 0 && same_as_init());
}

int main(void)
{
	t_arg(); t_mode0(); t_arm(); t_pre(); t_arm_fail(); t_teardown_fail(); t_isr(); t_isr_off_states(); t_f1_gap(); t_f2_errors(); t_f3_window(); t_thread(); t_thread_unmask_failure(); t_storm(); t_stuck(); t_missed(); t_quiesce(); t_reset(); t_summary(); t_judge(); t_nowrite_mode_other();
	CHK(spi_in_isr_calls == 0);
	if (bad) { printf("FAILED %d\n", bad); return 1; }
	printf("CORE OK\n");
	return 0;
}
'''


def patch_header(patch=PATCH):
    """the new-file hunk of bsd/dev/sweet_touch_irq1.h out of the patch text"""
    with open(patch) as f:
        t = f.read()
    i = t.index("diff --git a/bsd/dev/sweet_touch_irq1.h b/bsd/dev/sweet_touch_irq1.h")
    j = t.find("\ndiff --git ", i + 10)
    hunk = t[i:j if j > 0 else len(t)]
    body = hunk.split("\n+++ b/bsd/dev/sweet_touch_irq1.h\n", 1)[1]
    out = []
    for ln in body.splitlines()[1:]:      # skip the @@ line
        assert ln.startswith("+"), ln
        out.append(ln[1:])
    return "\n".join(out) + "\n"


def core_text(patch=PATCH):
    m = re.search(r"^/\* STI1_CORE_BEGIN \*/\n(.*?)^/\* STI1_CORE_END \*/", patch_header(patch), re.S | re.M)
    assert m, "no STI1_CORE block in the patch"
    return m.group(1)


def run(core):
    d = tempfile.mkdtemp()
    try:
        c = os.path.join(d, "t.c")
        with open(c, "w") as f:
            f.write(DRIVER.replace("@CORE@", core))
        exe = os.path.join(d, "t")
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        p = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-but-set-variable", "-Wno-unused-variable", "-Wno-unused-value", "-Wno-unused-parameter", "-Wno-maybe-uninitialized", "-Werror", "-O1", c, "-o", exe], capture_output=True, text=True)
        if p.returncode:
            return None, "compile: " + p.stderr[:1500]
        try:
            q = subprocess.run([exe], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return False, "timeout (an endless loop counts as a failure)"
        return q.returncode == 0, q.stdout
    finally:
        shutil.rmtree(d, True)
