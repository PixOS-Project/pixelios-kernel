"""xnu patches 0347 + 0349 (sweetwifiirq): host test of the pure core. The block between SWI_CORE_BEGIN and SWI_CORE_END of bsd/dev/sweet_wlan_irq.h (new-file hunk of xnu/patches/0347-sweet-wlan-irq-doorbell.patch with 0349-sweet-wlan-irq-ack-once.patch applied on top)
(bsd/dev/sweet_wlan_irq.h) is extracted from the patch text and compiled with the host C compiler together with a driver that plays the hardware (fake ELBI, fake PCIe core
mailbox registers, fake GIC calls) and records EVERY call as a token, so the tests assert exact sequences:
  arm = the corrected enable order of study-check s.4 (0xC34:=0, ack 0xC30 to 0, ELBI W1C bit 14, GIC attach disabled, ELBI IRQ0_EN bit 14, SPI enable, 0xC34:=0xFFFF last),
  ISR = test bit 14, W1C bit 14 only, never anything else; thread = ack 0xC30 BEFORE the drain, once per interrupt entry (patch 0349, no re-read loop), unmask last; teardown order; the judge table (every forbidden
  offset / value / mode is refused before the bus); the missed / spurious / storm counters and the auto-disable thresholds; mode 0 touches nothing; mode 2 never writes.
Then 51 mutants of the core (swap two enable steps, ack after drain, clear more than bit 14, loosened judge rows, swapped teardown, off-by-one thresholds, ...) that the same driver must kill.
The kernel glue (struct swi_ops implementations in sweet_wlan.c, the GIC calls, the log lines) is NOT run here: only a build + a boot shows it."""
import os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0347-sweet-wlan-irq-doorbell.patch")
PATCH2 = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0349-sweet-wlan-irq-ack-once.patch")   # applied on top of 0347 for the core

DRIVER = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
%(core)s
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %%d: %%s\n", __LINE__, #c); bad++; } } while (0)
#define CHKS(got, want) do { if (strcmp((got), (want)) != 0) { printf("FAIL line %%d: sequence\n  got : %%s\n  want: %%s\n", __LINE__, (got), (want)); bad++; } } while (0)

/* ---- fake hardware and the call recorder ---- */
static char tok[8192];
static void T(const char *s) { strcat(tok, tok[0] ? " " : ""); strcat(tok, s); }
static void TF(const char *f, int a, unsigned o, unsigned v) { char b[64]; snprintf(b, sizeof b, f, a, o, v); T(b); }
static uint32_t h_el0, h_en, h_c30, h_c34;
static int el0_sticky, rd_fail_el0, rd_fail_en, c30_sticky, en_stuck, rd_fail_c30, rd_fail_at, rd_ones_at, c30_reads, wr_fail_c30, wr_fail_c30_n, gate_rc, attach_rc, enable_rc, enable_rb, unmask_rc, detach_rc, drains, drain_ret, close_on_drain, wakes, notes[64], nnotes;
static uint32_t note_a[64], note_b[64];
static int rd(void *c, int sp, uint32_t off, uint32_t *v) {
	char b[48]; (void)c;
	if (sp == SWI_SP_ELBI) { if ((off == 0x000 && rd_fail_el0) || (off == 0x010 && rd_fail_en)) return -1; *v = off == 0x000 ? h_el0 : off == 0x010 ? h_en : 0xDEAD0000u; }
	else if (sp == SWI_SP_BAR0) { if (off == 0xC30) { c30_reads++; if (rd_fail_c30 || (rd_fail_at && c30_reads == rd_fail_at)) return -1; } *v = off == 0xC30 ? ((rd_ones_at && c30_reads == rd_ones_at) ? 0xFFFFFFFFu : h_c30) : off == 0xC34 ? h_c34 : 0xDEAD0000u; }
	else return -1;
	snprintf(b, sizeof b, "R.%%c.%%x.%%x", sp == SWI_SP_ELBI ? 'E' : 'B', off, *v); T(b);
	return 0;
}
static int wr(void *c, int sp, uint32_t off, uint32_t v) {
	char b[48]; (void)c;
	if (sp == SWI_SP_BAR0 && off == 0xC30 && wr_fail_c30) { wr_fail_c30_n++; return -1; }
	snprintf(b, sizeof b, "W.%%c.%%x.%%x", sp == SWI_SP_ELBI ? 'E' : 'B', off, v); T(b);
	if (sp == SWI_SP_ELBI && off == 0x000) { if (!el0_sticky) h_el0 &= ~v; }
	else if (sp == SWI_SP_ELBI && off == 0x010) { if (!en_stuck) h_en = v; }
	else if (sp == SWI_SP_BAR0 && off == 0xC30) { if (!c30_sticky) h_c30 &= ~v; }
	else if (sp == SWI_SP_BAR0 && off == 0xC34) h_c34 = v;
	return 0;
}
static int gate(void *c) { (void)c; T("G"); return gate_rc; }
static int s_att(void *c, uint32_t *rb) { (void)c; T("A"); *rb = 0; return attach_rc; }
static int s_en(void *c, uint32_t *rb) { (void)c; T("S"); *rb = enable_rb; return enable_rc; }
static int s_um(void *c) { (void)c; T("U"); return unmask_rc; }
static int s_det(void *c) { (void)c; T("X"); return detach_rc; }
static uint32_t drain(void *c) { (void)c; T("D"); drains++; if (close_on_drain) gate_rc = -1; return drain_ret; }
static void wake(void *c) { (void)c; T("K"); wakes++; }
static void note(void *c, int code, uint32_t a, uint32_t b) { (void)c; if (nnotes < 64) { notes[nnotes] = code; note_b[nnotes] = b; note_a[nnotes++] = a; } }
static struct swi_ops ops = { NULL, rd, wr, gate, s_att, s_en, s_um, s_det, drain, wake, note };
static int has_note(int code) { for (int i = 0; i < nnotes; i++) if (notes[i] == code) return 1; return 0; }
static int count_note(int code) { int n = 0; for (int i = 0; i < nnotes; i++) if (notes[i] == code) n++; return n; }
static int count_tok(const char *what) { int n = 0; const char *p = tok; size_t l = strlen(what); while ((p = strstr(p, what)) != NULL) { n++; p += l; } return n; }

static void reset(struct swi *st, int mode) {
	memset(st, 0, sizeof *st); st->mode = (uint8_t)mode;
	tok[0] = 0; h_el0 = 0; h_en = 0; h_c30 = 0; h_c34 = 0xFFFF; el0_sticky = rd_fail_el0 = rd_fail_en = c30_sticky = en_stuck = rd_fail_c30 = rd_fail_at = rd_ones_at = c30_reads = wr_fail_c30 = wr_fail_c30_n = gate_rc = attach_rc = enable_rc = unmask_rc = detach_rc = 0; enable_rb = 1; drains = 0; drain_ret = 0; close_on_drain = 0; wakes = 0; nnotes = 0;
}

/* an armed state: stale latched IRQ0 bit 14 and one MAILBOXINT bit pending at arm time, 0xC34 = 0xFFFF as the D4b step left it */
static void armed(struct swi *st) { reset(st, SWI_MODE_DB); h_el0 = 0x4000; h_c30 = 1; CHK(swi_arm(st, &ops) == 0); tok[0] = 0; nnotes = 0; }

static void t_arm_order(void) {
	struct swi st; reset(&st, SWI_MODE_DB); h_el0 = 0x4000; h_c30 = 1;
	CHK(swi_arm(&st, &ops) == 0);
	CHKS(tok, "G R.B.c34.ffff W.B.c34.0 R.B.c30.1 W.B.c30.1 D G R.B.c30.0 R.E.0.4000 W.E.0.4000 R.E.0.0 A R.E.10.0 W.E.10.4000 R.E.10.4000 S W.B.c34.ffff R.B.c34.ffff");
	CHK(st.on && st.elbi_on && st.spi_att && st.spi_en && st.why == 0 && st.c34_cur == 0xFFFF && st.c34_orig == 0xFFFF);
	CHKS(swi_mode_str(&st), "doorbell");
	/* 0xC34 already 0 at arm time: no step-1 store */
	reset(&st, SWI_MODE_DB); h_c34 = 0;
	CHK(swi_arm(&st, &ops) == 0);
	CHKS(tok, "G R.B.c34.0 R.B.c30.0 R.E.0.0 A R.E.10.0 W.E.10.4000 R.E.10.4000 S W.B.c34.ffff R.B.c34.ffff");
}

static void t_arm_failures(void) {
	struct swi st;
	/* gate closed: nothing touched */
	reset(&st, SWI_MODE_DB); gate_rc = 1; CHK(swi_arm(&st, &ops) == -100); CHKS(tok, "G"); CHK(has_note(SWI_G_GATE) && st.why == SWI_Y_GATE && !st.on);
	/* 0xC34 neither 0 nor 0xFFFF */
	reset(&st, SWI_MODE_DB); h_c34 = 0x1234; CHK(swi_arm(&st, &ops) < 0); CHKS(tok, "G R.B.c34.1234"); CHK(has_note(SWI_G_C34ORIG));
	/* 0xC30 never clears: 8 acks, then stuck; teardown puts 0xC34 back to 0xFFFF; ELBI untouched */
	reset(&st, SWI_MODE_DB); h_c30 = 1; c30_sticky = 1; CHK(swi_arm(&st, &ops) == -2);
	CHK(count_tok("W.B.c30.1") == 8 && count_tok("D") == 8 && has_note(SWI_G_C30STUCK) && st.why == SWI_Y_C30);
	CHK(strstr(tok, "W.E.") == NULL && strstr(tok, " A") == NULL && strstr(tok, " S") == NULL);
	CHK(h_c34 == 0xFFFF);
	/* IRQ0 bit 14 stays set after its W1C (line follows the endpoint, not cleared): refuse, teardown */
	reset(&st, SWI_MODE_DB); h_el0 = 0x4000; el0_sticky = 1; CHK(swi_arm(&st, &ops) == -3);
	CHK(has_note(SWI_G_ELBISTUCK) && strstr(tok, " A") == NULL && h_c34 == 0xFFFF && !st.on && st.why == SWI_Y_ARM);
	/* attach fails: no ELBI enable is ever written; teardown restores 0xC34 */
	reset(&st, SWI_MODE_DB); attach_rc = 22; CHK(swi_arm(&st, &ops) == -4);
	CHK(strstr(tok, "W.E.10") == NULL && strstr(tok, "S") == NULL && h_c34 == 0xFFFF && has_note(SWI_G_ATTACH) && st.why == SWI_Y_ARM);
	/* IRQ0_EN already has other bits (the golden's 0x154000): refuse, detach, restore */
	reset(&st, SWI_MODE_DB); h_en = 0x154000; CHK(swi_arm(&st, &ops) == -5);
	CHK(strstr(tok, "W.E.10") == NULL && count_tok("X") == 1 && has_note(SWI_G_ENOTHER) && h_c34 == 0xFFFF);
	/* the enable store does not stick (read-back differs): teardown order = ELBI off, detach, 0xC34 back */
	reset(&st, SWI_MODE_DB); en_stuck = 1; CHK(swi_arm(&st, &ops) == -5);
	CHK(has_note(SWI_G_VERIFY));
	{ const char *e = strstr(tok, "R.E.10.0 W.E.10.0 X W.B.c34.ffff"); CHK(e != NULL && e[strlen("R.E.10.0 W.E.10.0 X W.B.c34.ffff")] == 0); }
	/* the GIC enable fails: SPI never enabled, teardown, no 0xC34 unmask */
	reset(&st, SWI_MODE_DB); enable_rc = 22; CHK(swi_arm(&st, &ops) == -6); CHK(has_note(SWI_G_ENABLE) && !st.on && h_c34 == 0xFFFF);
	CHK(count_tok("W.B.c34.ffff") == 1);       /* only the restore, no unmask */
	/* mode 2 and 0 never arm */
	reset(&st, SWI_MODE_RO); CHK(swi_arm(&st, &ops) == -100 && tok[0] == 0);
	reset(&st, SWI_MODE_0); CHK(swi_arm(&st, &ops) == -100 && tok[0] == 0);
}

static void t_isr(void) {
	struct swi st; int r;
	armed(&st);
	h_el0 = 0x1004000;                          /* bit 14 plus bit 24: W1C must write bit 14 ONLY */
	r = swi_isr(&st, &ops);
	CHK(r == SWI_ISR_DEFER); CHKS(tok, "R.E.0.1004000 W.E.0.4000 K"); CHK(st.irqs == 1 && st.pend == 1 && st.isr_total == 1 && st.spurious == 0 && h_el0 == 0x1000000);
	tok[0] = 0; h_el0 = 0x1000000;              /* no bit 14: not ours; no write, line masked, thread woken */
	r = swi_isr(&st, &ops);
	CHK(r == SWI_ISR_DEFER); CHKS(tok, "R.E.0.1000000 K"); CHK(st.spurious == 1 && st.irqs == 1);
	st.on = 0; tok[0] = 0;                      /* disabled: no register access, dispatcher mask */
	CHK(swi_isr(&st, &ops) == SWI_ISR_MASK); CHKS(tok, ""); CHK(st.isr_off == 1);
	/* a failing ELBI read in the ISR: mask for good, error counted */
	armed(&st); rd_fail_el0 = 1; tok[0] = 0;
	CHK(swi_isr(&st, &ops) == SWI_ISR_MASK && st.isr_err == 1 && st.irqs == 0 && st.pend == 0 && tok[0] == 0);
	/* the judge refuses every access in mode 0 even if `on` were set: nothing reaches the bus */
	armed(&st); st.mode = SWI_MODE_0; st.on = 1; tok[0] = 0;
	CHK(swi_isr(&st, &ops) == SWI_ISR_MASK && st.isr_err == 1 && st.refused >= 1 && tok[0] == 0);
}

static void t_thread(void) {
	struct swi st; uint32_t ex;
	/* interrupt pending, MAILBOXINT = 3: ack FIRST (before any ring work), then drain, re-read zero, unmask LAST */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; h_c30 = 3; drain_ret = 0;
	CHK(swi_pre(&st, &ops) == 1);
	CHKS(tok, "G R.B.c30.3 W.B.c30.3");
	T("D");                                     /* the glue's first drain (swg_keep_r2 + en0_live_poll) */
	ex = swi_post(&st, &ops, 1);
	CHKS(tok, "G R.B.c30.3 W.B.c30.3 D G R.B.c30.0 U"); CHK(ex == 0 && st.missed == 0 && st.unmasks == 1 && st.pend == 0);   /* patch 0349: no ack loop; one 0xC30 read before the unmask (review F1) */
	/* patch 0349 (review F1): a bit that appears after the ack is not chased in this entry (no second ack, no second drain), but the pre-unmask read sees it: NO unmask, pend set, the next pass is a new entry */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; h_c30 = 0; drain_ret = 2;
	swi_pre(&st, &ops); tok[0] = 0; h_c30 = 4; T("D");
	ex = swi_post(&st, &ops, 1);
	CHKS(tok, "D G R.B.c30.4"); CHK(ex == 0 && h_c30 == 4 && st.on && st.unmasks == 0 && st.pend == 1 && st.rechecks == 1);
	/* poll found work, no interrupt: counted as missed, no 0xC30 access (patch 0349: one ack per interrupt entry only), no unmask (the line is not masked) */
	armed(&st); h_c30 = 0;
	CHK(swi_pre(&st, &ops) == 0); CHKS(tok, "G");
	ex = swi_post(&st, &ops, 3);
	CHKS(tok, "G G"); CHK(st.missed == 1 && st.work_events == 1 && st.unmasks == 0);
	/* work found while an interrupt did fire since the last pass: not missed */
	armed(&st); st.isr_total = 1; st.irqs = 1; st.pend = 0; h_c30 = 0;
	swi_pre(&st, &ops); swi_post(&st, &ops, 3); CHK(st.missed == 0);
	/* nothing pending, nothing found: no access at all */
	armed(&st); swi_pre(&st, &ops); swi_post(&st, &ops, 0); CHKS(tok, "G");
	/* spurious entry: the line was masked, the thread unmasks, no 0xC30 access */
	armed(&st); st.spurious = 1; swi_pre(&st, &ops); ex = swi_post(&st, &ops, 0); CHKS(tok, "G G R.B.c30.0 U"); CHK(st.spur_run == 1);
	/* unmask failure turns the mode off loudly */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; unmask_rc = 22; swi_pre(&st, &ops); swi_post(&st, &ops, 0); CHK(!st.on && st.why == SWI_Y_UNMASK && has_note(SWI_G_UNMASK));
	/* 0xC30 read failing in the thread: loud, off */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; rd_fail_c30 = 1; swi_pre(&st, &ops); swi_post(&st, &ops, 1); CHK(!st.on && has_note(SWI_G_RD));
}

static void t_autooff(void) {
	struct swi st; int i;
	/* 200 poll passes that found work with zero interrupts ever -> off:noirq at the 200th, not before */
	armed(&st);
	for (i = 1; i <= 199; i++) { swi_pre(&st, &ops); swi_post(&st, &ops, 1); }
	CHK(st.on && st.work_events == 199 && st.missed == 199 && st.why == 0);
	tok[0] = 0; swi_pre(&st, &ops); swi_post(&st, &ops, 1);
	CHK(!st.on && st.why == SWI_Y_NOIRQ && has_note(SWI_G_NOIRQ)); CHKS(swi_mode_str(&st), "off:noirq");
	CHKS(tok, "G G R.E.10.4000 W.E.10.0 X");        /* teardown order: ELBI enable off, SPI detach; 0xC34 already at its pre-arm value */
	CHK(!st.elbi_on && !st.spi_att && h_en == 0);
	/* with interrupts seen the noirq rule is out, but patch 0349 (review F1) catches the silent doorbell: irqs not rising, 200 poll passes with work -> off:silent with its own GAP, at the 200th, not before */
	armed(&st); st.irqs = 1; st.silent_seen = 1;
	for (i = 1; i <= 199; i++) { tok[0] = 0; swi_pre(&st, &ops); swi_post(&st, &ops, 1); }
	CHK(st.on && st.silent_run == 199 && !has_note(SWI_G_SILENT) && !has_note(SWI_G_NOIRQ));
	swi_pre(&st, &ops); swi_post(&st, &ops, 1);
	CHK(!st.on && st.why == SWI_Y_SILENT && count_note(SWI_G_SILENT) == 1 && !has_note(SWI_G_NOIRQ)); CHKS(swi_mode_str(&st), "off:silent");
	{ int k; for (k = 0; k < nnotes; k++) if (notes[k] == SWI_G_SILENT) CHK(note_a[k] == 200u && note_b[k] == 1u); }
	/* an interrupt between the passes resets the silent run (a working doorbell with sparse traffic stays on) */
	armed(&st); st.irqs = 1; st.silent_seen = 1;
	for (i = 1; i <= 1000; i++) { tok[0] = 0; if (i % 150 == 0) st.irqs++; swi_pre(&st, &ops); swi_post(&st, &ops, 1); }
	CHK(st.on && st.silent_run < 150);
	/* storm: > 5000 ISR entries in a one-second window */
	armed(&st); swi_tick(&st, &ops, 1000); st.isr_total += 5000; swi_tick(&st, &ops, 2000); CHK(st.on && st.storm == 0);
	st.isr_total += 5001; swi_tick(&st, &ops, 2500); CHK(st.on);   /* window not over yet */
	swi_tick(&st, &ops, 3000); CHK(!st.on && st.storm == 1 && st.why == SWI_Y_STORM && has_note(SWI_G_STORM));
	/* three entries in a row without bit 14 */
	armed(&st); h_el0 = 0;
	for (i = 0; i < 3; i++) { CHK(swi_isr(&st, &ops) == SWI_ISR_DEFER); swi_pre(&st, &ops); swi_post(&st, &ops, 0); }
	CHK(!st.on && st.why == SWI_Y_SPUR && has_note(SWI_G_SPUR) && st.spurious == 3);
	/* an interrupt in between resets the run */
	armed(&st); h_el0 = 0; swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0);
	h_el0 = 0x4000; swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); h_el0 = 0; swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); CHK(st.on && st.spur_run == 1);
	/* a refused access anywhere turns it off: force a judge refusal by corrupting the mode bit of the state (a bug) */
	armed(&st); st.refused = 1; swi_pre(&st, &ops); swi_post(&st, &ops, 0); CHK(!st.on && st.why == SWI_Y_REFUSED);
	/* explicit teardown with the bus gone (engine end): no BAR0 access at all */
	armed(&st); st.c34_cur = 0; tok[0] = 0; swi_teardown(&st, &ops, 0); CHKS(tok, "R.E.10.4000 W.E.10.0 X");
	/* disable twice: the first reason wins, the teardown runs once */
	armed(&st); swi_disable(&st, &ops, SWI_Y_LINK, 0, 0); tok[0] = 0; swi_disable(&st, &ops, SWI_Y_END, 0, 0); CHKS(tok, ""); CHK(st.why == SWI_Y_LINK);
	/* teardown tries EVERY step even when the first fails: ELBI read fails -> detach still runs */
	armed(&st); rd_fail_en = 1; tok[0] = 0; nnotes = 0; swi_teardown(&st, &ops, 1);
	CHKS(tok, "X"); CHK(has_note(SWI_G_TEARDOWN) && note_a[0] == 1 && st.elbi_on && !st.spi_att);
}

static int judge(struct swi *st, int sp, int acc, uint32_t off, uint32_t v) {
	uint32_t x = v; size_t before = strlen(tok); int r = swi_io(st, &ops, sp, acc, off, &x);
	if (r == SWI_E_REFUSED && strlen(tok) != before) { printf("FAIL: a refused access reached the bus (sp %d acc %d off %#x v %#x)\n", sp, acc, off, v); bad++; }
	return r == 0;
}

static void t_judge(void) {
	struct swi st;
	armed(&st);                                 /* mode DB, elbi_on, spi_en */
	st.en_valid = 0;
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x4000));         /* no fresh read of the enable word: refused (en_valid is 0) */
	st.en_last = 0; st.en_valid = 1;
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x154000));       /* the golden's 4 enables: refused, only bit 14 */
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x4001));
	CHK(judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x4000)); st.en_last = 0x4000; st.en_valid = 1;
	CHK(judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0));               /* clear bit 14 */
	st.en_last = 0x154000; st.en_valid = 1;
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x154000 | 0x4000 | 0x1)); CHK(judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x150000));   /* teardown clears bit 14 only, keeps the rest */
	st.en_last = 0x154000; st.en_valid = 1; CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0));    /* ... never clears other bits */
	CHK(judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0x4000)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0x1004000)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0xFFFFFFFFu)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0));
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x004, 0x4000)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x014, 0)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x018, 0)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x2C8, 0));
	CHK(!judge(&st, SWI_SP_ELBI, SWI_R, 0x014, 0)); CHK(!judge(&st, SWI_SP_ELBI, SWI_R, 0x4, 0)); CHK(judge(&st, SWI_SP_ELBI, SWI_R, 0x000, 0)); CHK(judge(&st, SWI_SP_ELBI, SWI_R, 0x010, 0));
	st.c30_valid = 0; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 1));           /* W1C needs a read in the same pass */
	st.c30_last = 0x3; st.c30_valid = 1; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 0x4));   /* a bit that was not read */
	st.c30_last = 0x3; st.c30_valid = 1; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 0));
	st.c30_last = 0x3; st.c30_valid = 1; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 0x10003));
	st.c30_last = 0x10003; st.c30_valid = 1; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 0x10000));  /* a bit above the 16 doorbell bits */
	st.c30_last = 0xF; st.c30_valid = 1; CHK(judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 0x5)); CHK(!st.c30_valid);   /* a write consumes the read */
	/* 0xC34: 0 always, 0xFFFF only with BOTH halves armed or BOTH off */
	CHK(judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0)); CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0x1234)); CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0x8000));
	st.elbi_on = 1; st.spi_en = 1; CHK(judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0xFFFF));
	st.elbi_on = 1; st.spi_en = 0; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0xFFFF));
	st.elbi_on = 0; st.spi_en = 1; CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0xFFFF));
	st.elbi_on = 0; st.spi_en = 0; CHK(judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0xFFFF));
	/* nothing else on the PCIe core page */
	CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0x140, 0xFF000001u)); CHK(!judge(&st, SWI_SP_BAR0, SWI_R, 0x144, 0)); CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC38, 0)); CHK(!judge(&st, 3, SWI_R, 0, 0));
	/* mode 2: reads of the three words, no write row, no 0xC34 read */
	reset(&st, SWI_MODE_RO);
	CHK(judge(&st, SWI_SP_ELBI, SWI_R, 0x000, 0) && judge(&st, SWI_SP_ELBI, SWI_R, 0x010, 0) && judge(&st, SWI_SP_BAR0, SWI_R, 0xC30, 0));
	CHK(!judge(&st, SWI_SP_BAR0, SWI_R, 0xC34, 0));
	st.en_last = 0; st.en_valid = 1; st.c30_last = 1; st.c30_valid = 1; st.elbi_on = st.spi_en = 1;
	CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0x4000)); CHK(!judge(&st, SWI_SP_ELBI, SWI_W, 0x010, 0x4000)); CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC30, 1)); CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0));
	CHK(!judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0xFFFF));
	/* mode 0: nothing */
	reset(&st, SWI_MODE_0); st.en_last = 0; st.en_valid = 1; st.c30_last = 1; st.c30_valid = 1;
	CHK(!judge(&st, SWI_SP_ELBI, SWI_R, 0x000, 0) && !judge(&st, SWI_SP_BAR0, SWI_R, 0xC30, 0) && !judge(&st, SWI_SP_BAR0, SWI_W, 0xC34, 0) && !judge(&st, SWI_SP_ELBI, SWI_W, 0x000, 0x4000));
}

static void t_ro(void) {
	struct swi st; struct swi_ro r;
	reset(&st, SWI_MODE_RO); h_el0 = 0x4000; h_en = 0; h_c30 = 0x1;
	CHK(swi_ro_sample(&st, &ops, &r) == 0);
	CHKS(tok, "G R.E.0.4000 R.E.10.0 R.B.c30.1"); CHK(r.elbi0 == 0x4000 && r.en0 == 0 && r.c30 == 1 && r.err == 0 && st.ro_n == 1);
	CHK(strchr(tok, 'W') == NULL);
	CHKS(swi_mode_str(&st), "ro");
	/* gate closed: nothing is touched, counted */
	reset(&st, SWI_MODE_RO); gate_rc = 1; CHK(swi_ro_sample(&st, &ops, &r) == -1); CHKS(tok, "G"); CHK(st.ro_skip == 1 && r.elbi0 == 0xFFFFFFFFu);
	/* one read fails: that field is all ones, the others are still read, return -2 */
	reset(&st, SWI_MODE_RO); rd_fail_c30 = 1; CHK(swi_ro_sample(&st, &ops, &r) == -2); CHK(r.c30 == 0xFFFFFFFFu && r.err == 4u);
	/* in mode 2 no other entry point touches anything */
	reset(&st, SWI_MODE_RO); st.on = 0; CHK(swi_isr(&st, &ops) == SWI_ISR_MASK); CHK(swi_pre(&st, &ops) == 0); CHK(swi_post(&st, &ops, 1) == 0); swi_tick(&st, &ops, 5000); CHKS(tok, "");
	/* mode 0: the sampler and everything else refuse before any access */
	reset(&st, SWI_MODE_0); CHK(swi_ro_sample(&st, &ops, &r) == -1); CHK(swi_isr(&st, &ops) == SWI_ISR_MASK); CHK(swi_pre(&st, &ops) == 0); CHK(swi_post(&st, &ops, 1) == 0); swi_tick(&st, &ops, 9000); CHKS(tok, "");
}

/* review 0346 F1: a line that stays asserted with 0xC30 == 0 and no ring work, one pass every 250 us (the reviewer's case, which the storm limit of 5000/s never catches) */
static void t_stuck_line(void) {
	struct swi st; unsigned pass; uint32_t t_us = 0;
	armed(&st); el0_sticky = 1; h_el0 = 0x4000; h_c30 = 0;
	for (pass = 1; pass <= 80000 && st.on; pass++) {
		tok[0] = 0;
		swi_isr(&st, &ops); swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 0);
		t_us += 250; swi_tick(&st, &ops, t_us / 1000);
	}
	CHK(!st.on && st.why == SWI_Y_EMPTY && has_note(SWI_G_EMPTY) && st.storm == 0);
	CHK(pass - 1 == SWI_EMPTY_MAX); CHK(st.unmasks == SWI_EMPTY_MAX - 1u);
	CHK(count_tok("U") == 0);                   /* the pass that turned it off did NOT unmask */
	CHKS(swi_mode_str(&st), "off:emptyirq");
	/* a pass with real work resets the run: 63 empty, one with a pending bit, 63 empty again: still on */
	armed(&st); el0_sticky = 1; h_el0 = 0x4000; h_c30 = 0;
	for (pass = 0; pass < 63; pass++) { swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); }
	h_c30 = 1; swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); h_c30 = 0;
	for (pass = 0; pass < 63; pass++) { swi_isr(&st, &ops); swi_pre(&st, &ops); swi_post(&st, &ops, 0); }
	CHK(st.on && st.why == 0 && st.empty_run == 63);
}

/* review 0346 F3: an ISR ELBI error turns the mode off with its own GAP */
static void t_isr_err_off(void) {
	struct swi st;
	armed(&st); rd_fail_el0 = 1;
	CHK(swi_isr(&st, &ops) == SWI_ISR_MASK && st.isr_err == 1);
	swi_pre(&st, &ops); swi_post(&st, &ops, 0);
	CHK(!st.on && st.why == SWI_Y_ISR && has_note(SWI_G_ISRERR) && count_tok("X") == 1);
	CHKS(swi_mode_str(&st), "off:isr");
}

/* review 0346 F2: the gate first on every thread pass; closed = no BAR0 access, no unmask, one GAP per state change */
static void t_gate_pass(void) {
	struct swi st; uint32_t ex;
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; h_c30 = 3; gate_rc = -1;
	swi_pre(&st, &ops); T("D"); ex = swi_post(&st, &ops, 1);
	CHKS(tok, "G D"); CHK(ex == 0 && st.pend == 1 && st.gate_closed == 1 && count_note(SWI_G_GATECLOSE) == 1 && st.on);
	swi_pre(&st, &ops); ex = swi_post(&st, &ops, 1);
	CHKS(tok, "G D G"); CHK(count_note(SWI_G_GATECLOSE) == 1 && count_note(SWI_G_GATEOPEN) == 0);
	CHK(strstr(tok, "B.") == NULL && strstr(tok, "U") == NULL);
	gate_rc = 0; tok[0] = 0; swi_pre(&st, &ops); ex = swi_post(&st, &ops, 0);
	CHKS(tok, "G R.B.c30.3 W.B.c30.3 G R.B.c30.0 U"); CHK(count_note(SWI_G_GATEOPEN) == 1 && st.gate_closed == 0 && st.unmasks == 1);
	/* the gate closes between pre and post (the engine stopped inside the pass): nothing more, the unmask is owed */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; h_c30 = 0;
	swi_pre(&st, &ops); gate_rc = -1; ex = swi_post(&st, &ops, 1);
	CHKS(tok, "G R.B.c30.0 G"); CHK(st.owe_unmask == 1 && st.unmasks == 0);
	gate_rc = 0; swi_pre(&st, &ops); swi_post(&st, &ops, 0);
	CHKS(tok, "G R.B.c30.0 G G G R.B.c30.0 U"); CHK(st.owe_unmask == 0 && st.unmasks == 1);
	/* patch 0349: swi_post never drains and never re-reads 0xC30 (the glue's drain is the only one) */
	armed(&st); st.pend = 1; st.irqs = 1; st.isr_total = 1; h_c30 = 3;
	swi_pre(&st, &ops); h_c30 = 4; ex = swi_post(&st, &ops, 1);
	CHKS(tok, "G R.B.c30.3 W.B.c30.3 G R.B.c30.4"); CHK(count_tok("D") == 0 && count_tok("R.B.c30") == 2 && count_tok("W.B.c30") == 1 && count_tok("U") == 0);
}

/* patch 0349: one ack per interrupt entry; c30stuck only after SWI_C30_RUN_MAX (64) entries in a row with 0xC30 != 0 and drains that found no work */
static void entry(struct swi *st, uint32_t c30, uint32_t work) {
	if (!st->on) return;
	h_el0 = 0x4000; h_c30 = c30;
	swi_isr(st, &ops); swi_pre(st, &ops); T("D"); swi_post(st, &ops, work);
}
static void t_ack_once(void) {
	struct swi st; int i;
	CHK(SWI_C30_RUN_MAX == 64u);
	/* (a) steady stream: 0xC30 non-zero at every entry, every drain finds work: never disables */
	armed(&st);
	for (i = 0; i < 2000; i++) { tok[0] = 0; entry(&st, 1, 1 + (uint32_t)(i & 3)); }   /* tok cleared per entry: the recorder is 8 KB */
	CHK(st.on && st.why == 0 && st.c30_run == 0 && !has_note(SWI_G_C30STUCK) && st.irqs == 2000);
	/* (b) 63 entries non-zero with no work, then work: no disable, the counter resets; then 63 more: still on, the 64th (no work) after them turns it off */
	armed(&st);
	for (i = 0; i < 63; i++) { tok[0] = 0; entry(&st, 1, 0); }
	CHK(st.on && st.c30_run == 63);
	entry(&st, 1, 1); CHK(st.on && st.c30_run == 0);
	for (i = 0; i < 63; i++) { tok[0] = 0; entry(&st, 1, 0); }
	CHK(st.on && st.c30_run == 63 && !has_note(SWI_G_C30STUCK));
	/* a zero read also resets the run */
	entry(&st, 0, 0); CHK(st.on && st.c30_run == 0);
	for (i = 0; i < 63; i++) { tok[0] = 0; entry(&st, 3, 0); }
	CHK(st.on && st.c30_run == 63);
	/* (c) the 64th in a row: off as c30stuck, GAP with a = last value, b = the count, no unmask on that pass */
	armed(&st);
	for (i = 0; i < 64; i++) { if (i == 63) tok[0] = 0; entry(&st, 1, 0); }
	CHK(!st.on && st.why == SWI_Y_C30 && count_note(SWI_G_C30STUCK) == 1);
	{ int k, got = 0; for (k = 0; k < nnotes; k++) if (notes[k] == SWI_G_C30STUCK) { CHK(note_a[k] == 1u && note_b[k] == 64u); got++; } CHK(got == 1); }
	CHK(count_tok("U") == 0);          /* the last entry (64th) did not unmask */
	CHKS(swi_mode_str(&st), "off:c30stuck");
	/* (d) one entry = exactly one 0xC30 read and one W1C of exactly the bits read, then one drain, then the unmask */
	armed(&st); entry(&st, 0x5, 1);
	CHKS(tok, "R.E.0.4000 W.E.0.4000 K G R.B.c30.5 W.B.c30.5 D G R.B.c30.0 U");
	CHK(count_tok("R.B.c30") == 2 && count_tok("W.B.c30") == 1 && count_tok("D") == 1);   /* entry read + W1C, drain, ONE pre-unmask read */
	/* a zero entry: entry read + pre-unmask read, no write */
	armed(&st); entry(&st, 0, 0);
	CHK(count_tok("R.B.c30") == 2 && count_tok("W.B.c30") == 0);
	/* review F4: the W1C of an entry fails on the bus (driven through the ops fake): GAP "write failed", off as c30 at once, no unmask, teardown ran */
	armed(&st); wr_fail_c30 = 1; entry(&st, 3, 1);
	CHK(!st.on && st.why == SWI_Y_C30 && has_note(SWI_G_WR) && !has_note(SWI_G_RD) && wr_fail_c30_n == 1 && count_tok("U") == 0 && count_tok("X") == 1);
	/* the first 0xC30 read of an entry fails on the bus (the later reads would work): GAP "read failed", off, no ack, no unmask */
	armed(&st); c30_reads = 0; rd_fail_at = 1; entry(&st, 3, 1);
	CHK(!st.on && st.why == SWI_Y_C30 && has_note(SWI_G_RD) && !has_note(SWI_G_WR) && count_tok("W.B.c30") == 0 && count_tok("U") == 0);
	/* the pre-unmask read fails (the entry's own read and W1C worked): off, no unmask */
	armed(&st); c30_reads = 0; rd_fail_at = 2; entry(&st, 3, 1);
	CHK(!st.on && st.why == SWI_Y_C30 && has_note(SWI_G_RD) && count_tok("W.B.c30") == 1 && count_tok("U") == 0);
	/* review F3: an all-ones 0xC30 (dead link) is not acked and turns the mode off at the first entry (only the first read is all ones: the entry's own check must see it) */
	armed(&st); c30_reads = 0; rd_ones_at = 1; entry(&st, 0, 0);
	CHK(!st.on && st.why == SWI_Y_C30 && has_note(SWI_G_RD) && count_tok("W.B.c30") == 0 && count_tok("U") == 0 && st.refused == 0);
	armed(&st); c30_reads = 0; rd_ones_at = 1; entry(&st, 3, 3);       /* with work too */
	CHK(!st.on && st.why == SWI_Y_C30 && count_tok("W.B.c30") == 0);
	/* ... and an all-ones pre-unmask read (the entry's read was fine) */
	armed(&st); c30_reads = 0; rd_ones_at = 2; entry(&st, 3, 1);
	CHK(!st.on && st.why == SWI_Y_C30 && has_note(SWI_G_RD) && count_tok("U") == 0);
	/* re-run entries (pend set by the pre-unmask check, no interrupt: the line is masked) that find work are real traffic, not a silent doorbell: 400 of them never trip the silent rule, the c30 rule or the empty rule */
	armed(&st); st.irqs = 1; st.silent_seen = 1; st.pend = 1; h_c30 = 1;
	for (i = 0; i < 400; i++) { tok[0] = 0; swi_pre(&st, &ops); h_c30 |= 2; T("D"); swi_post(&st, &ops, 1); }
	CHK(st.on && st.why == 0 && st.silent_run == 0 && st.rechecks == 400 && st.unmasks == 0);
	/* review F2 (probe R3): the poll pass drains first (the interrupt fires mid-pass, after swi_pre), the interrupt's own pass finds the doorbell bit and no work: 64+ such pairs never disable */
	armed(&st);
	for (i = 0; i < 200; i++) {
		tok[0] = 0; h_c30 = 1; h_el0 = 0x4000;
		swi_pre(&st, &ops); swi_isr(&st, &ops); T("D"); swi_post(&st, &ops, 2);
		swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 0);
	}
	CHK(st.on && st.why == 0 && !has_note(SWI_G_C30STUCK) && st.c30_run == 1);
	/* ... and without the poll pass's work the same pairs do trip (64th): the reset is the work, not the pairing */
	armed(&st);
	for (i = 0; i < 70 && st.on; i++) {
		tok[0] = 0; h_c30 = 1; h_el0 = 0x4000;
		swi_pre(&st, &ops); swi_isr(&st, &ops); T("D"); swi_post(&st, &ops, 0);
		swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 0);
	}
	CHK(!st.on && has_note(SWI_G_C30STUCK));
	/* review F1 (probe R4): a second bit set after the thread's read (here: by the dongle during the entry) is still there at the unmask: the unmask is postponed, the next entry acks and drains it, then the unmask happens */
	armed(&st); tok[0] = 0; h_el0 = 0x4000; h_c30 = 1;
	swi_isr(&st, &ops); swi_pre(&st, &ops); h_c30 |= 2; T("D"); swi_post(&st, &ops, 1);
	CHK(st.on && st.unmasks == 0 && st.pend == 1 && st.rechecks == 1 && count_tok("U") == 0);
	tok[0] = 0; swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 1);
	CHKS(tok, "G R.B.c30.2 W.B.c30.2 D G R.B.c30.0 U"); CHK(st.on && st.unmasks == 1 && st.pend == 0);
	/* R4 continuation: the doorbell then stays silent (the line is never re-asserted: no ISR) while the poll keeps finding work: ends in the GAP and off:silent, never a silent "doorbell" mode */
	armed(&st); h_el0 = 0x4000; h_c30 = 1; swi_isr(&st, &ops); swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 1);
	for (i = 0; i < 500 && st.on; i++) { tok[0] = 0; swi_pre(&st, &ops); T("D"); swi_post(&st, &ops, 1); }
	CHK(!st.on && st.why == SWI_Y_SILENT && has_note(SWI_G_SILENT) && i == 200 && st.missed == 200);
	CHKS(swi_mode_str(&st), "off:silent");
}

int main(void) {
	t_arm_order(); t_arm_failures(); t_isr(); t_thread(); t_autooff(); t_judge(); t_ro(); t_stuck_line(); t_isr_err_off(); t_gate_pass(); t_ack_once();
	printf(bad ? "CORE FAIL %%d\n" : "CORE OK\n", bad);
	return bad != 0;
}
'''


def irq_header(patch=PATCH, patch2=PATCH2):
    """bsd/dev/sweet_wlan_irq.h as the patch chain leaves it: the new-file hunk of 0347, then the hunk of 0349 on top (patch(1), the way shim/build.sh chains 0174 onto 0124)"""
    with open(patch) as f:
        txt = f.read()
    m = re.search(r"^\+\+\+ b/bsd/dev/sweet_wlan_irq\.h\n(?:@@[^\n]*\n)(.*?)(?=^diff |\Z)", txt, re.S | re.M)
    assert m, "no sweet_wlan_irq.h hunk in 0347"
    body = "".join(l[1:] + "\n" for l in m.group(1).splitlines() if l.startswith("+"))
    with open(patch2) as f:
        t2 = f.read()
    i = t2.index("diff --git a/bsd/dev/sweet_wlan_irq.h ")
    j = t2.find("\ndiff --git ", i + 1)
    j = len(t2) if j < 0 else j + 1
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "bsd", "dev"))
        h = os.path.join(d, "bsd", "dev", "sweet_wlan_irq.h")
        with open(h, "w") as f:
            f.write(body)
        pf = os.path.join(d, "0349.diff")
        with open(pf, "w") as f:
            f.write(t2[i:j])
        p = subprocess.run(["patch", "-s", "-p1", "-d", d, "-i", pf], capture_output=True, text=True)
        assert p.returncode == 0, "patch 0349 does not apply on top of 0347: " + p.stdout + p.stderr
        with open(h) as f:
            return f.read()
    finally:
        shutil.rmtree(d, True)


def core_text(patch=PATCH):
    txt = irq_header(patch)
    m = re.search(r"^/\* SWI_CORE_BEGIN \*/\n(.*?)^/\* SWI_CORE_END \*/", txt, re.S | re.M)
    assert m, "no SWI_CORE block in the patch chain"
    return m.group(1)


def run(core):
    d = tempfile.mkdtemp()
    try:
        c = os.path.join(d, "t.c")
        with open(c, "w") as f:
            f.write(DRIVER.replace("%%", "%").replace("%(core)s", core))
        exe = os.path.join(d, "t")
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        p = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-but-set-variable", "-Wno-unused-variable", "-Wno-unused-value", "-Werror", "-O1", c, "-o", exe], capture_output=True, text=True)
        if p.returncode:
            return None, "compile: " + p.stderr[:900]
        q = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return q.returncode == 0, q.stdout
    finally:
        shutil.rmtree(d, True)


def swap_blocks(core, a, b, c):
    """swap the text from marker a up to marker b with the text from b up to c"""
    i, j, k = core.index(a), core.index(b), core.index(c)
    assert i < j < k, (a, b, c)
    return core[:i] + core[j:k] + core[i:j] + core[k:]


def text_mutants_0349():
    """(name, old text, new text) replacements of the patch 0349 rules; each old text occurs exactly once in the core"""
    return (
    ("0349: c30 run not reset by an entry whose drain found work or whose read was zero", "\t\t} else {\n\t\t\tst->c30_run = 0;\n\t\t}", "\t\t}"),
    ("0349: c30 run not reset by a zero read (only by work)", "\t\t} else {\n\t\t\tst->c30_run = 0;\n\t\t}", "\t\t} else if (got != 0u) {\n\t\t\tst->c30_run = 0;\n\t\t}"),
    ("0349: c30 run counts entries whose drain found work", "if (st->p_c30 != 0u && got == 0u) {", "if (st->p_c30 != 0u) {"),
    ("0349: K off by one (65)", "if (st->c30_run >= SWI_C30_RUN_MAX) {", "if (st->c30_run > SWI_C30_RUN_MAX) {"),
    ("0349: K off by one (63)", "if (st->c30_run >= SWI_C30_RUN_MAX) {", "if (st->c30_run >= SWI_C30_RUN_MAX - 1u) {"),
    ("0349: GAP count argument dropped", "swi_gap(o, SWI_G_C30STUCK, st->p_c30, st->c30_run);", "swi_gap(o, SWI_G_C30STUCK, st->p_c30, 0u);"),
    ("0349: c30stuck without disabling", "\t\t\t\tswi_gap(o, SWI_G_C30STUCK, st->p_c30, st->c30_run);\n\t\t\t\tswi_disable(st, o, SWI_Y_C30, 0, 0u);", "\t\t\t\tswi_gap(o, SWI_G_C30STUCK, st->p_c30, st->c30_run);"),
    ("0349: a failed 0xC30 access of an entry is ignored", "\t\tif (st->p_aerr != 0u) {", "\t\tif (0) {"),
    # review 0349 (F1-F4): the reviewer's 7 mutants (rev49_probe.py) and the mutants of the review fixes
    ("0349 R1: swi_pre W1C failure not recorded (p_aerr = WR dropped)", "\t\t\t\tst->p_aerr = r == SWI_E_REFUSED ? SWI_G_REFUSED : SWI_G_WR;\n", ""),
    ("0349 R2: swi_pre read failure not recorded (p_aerr = RD dropped)", "\t\t\tst->p_aerr = r == SWI_E_REFUSED ? SWI_G_REFUSED : SWI_G_RD;\n", ""),
    ("0349 R3: c30stuck GAP a = 0 instead of the last value", "swi_gap(o, SWI_G_C30STUCK, st->p_c30, st->c30_run);", "swi_gap(o, SWI_G_C30STUCK, 0u, st->c30_run);"),
    ("0349 R4: empty rule also counts non-zero 0xC30 (p_c30 test dropped)", "if (st->p_pend && st->p_c30 == 0u && got == 0u) {", "if (st->p_pend && got == 0u) {"),
    ("0349 R5: c30 run reset only by a pass with work and no interrupt (F2 reset moved under !p_pend)", "\tif (got != 0u) {                            /* review 0349 F2", "\tif (got != 0u && st->p_pend) {            /* review 0349 F2"),
    ("0349 F2: the pass that drained work does not reset the c30 run", "\tif (got != 0u) {                            /* review 0349 F2: a pass that drained work is not an idle entry, whichever pass carried the interrupt (poll pass first, interrupt pass finds the bit with nothing left) */\n\t\tst->c30_run = 0;\n\t}\n", ""),
    ("0349 F1: no 0xC30 re-check before the unmask", "\t\tif (c != 0u) {\n\t\t\t__atomic_store_n(&st->pend, 1u, __ATOMIC_RELEASE);", "\t\tif (0) {\n\t\t\t__atomic_store_n(&st->pend, 1u, __ATOMIC_RELEASE);"),
    ("0349 F1: re-check postpones the unmask but does not set pend (the entry is never re-run)", "\t\t\t__atomic_store_n(&st->pend, 1u, __ATOMIC_RELEASE);\n", ""),
    ("0349 F1: re-check read failure ignored", "\t\tif (r != 0 || c == 0xFFFFFFFFu) {\n\t\t\tswi_gap(o, r == SWI_E_REFUSED ? SWI_G_REFUSED : SWI_G_RD, SWI_SP_BAR0, SWI_MBX_INT);\n\t\t\tswi_disable(st, o, SWI_Y_C30, 0, 0u);\n\t\t\treturn 0;\n\t\t}\n", ""),
    ("0349 F1: silent threshold off by one (201)", "if (st->silent_run >= SWI_SILENT_MAX) {", "if (st->silent_run > SWI_SILENT_MAX) {"),
    ("0349 F1: silent threshold off by one (199)", "if (st->silent_run >= SWI_SILENT_MAX) {", "if (st->silent_run >= SWI_SILENT_MAX - 1u) {"),
    ("0349 F1: an interrupt does not reset the silent run", "\t\tst->silent_seen = st->irqs;\n\t\tst->silent_run = 0;\n", "\t\tst->silent_seen = st->irqs;\n"),
    ("0349 F1: the silent run counts interrupt passes too", "} else if (got != 0u && !st->p_pend) {\n\t\tst->silent_run++;", "} else if (got != 0u) {\n\t\tst->silent_run++;"),
    ("0349 F1: silent GAP not raised (off without a GAP)", "\t\tswi_gap(o, SWI_G_SILENT, st->silent_run, st->irqs);\n", ""),
    ("0349 F1: silent rule never disables", "\t\tswi_gap(o, SWI_G_SILENT, st->silent_run, st->irqs);\n\t\tswi_disable(st, o, SWI_Y_SILENT, 0, 0u);\n", "\t\tswi_gap(o, SWI_G_SILENT, st->silent_run, st->irqs);\n"),
    ("0349 F3: an all-ones read in swi_pre is treated as a normal value", "if (r == 0 && c == 0xFFFFFFFFu) {", "if (0) {"),
    ("0349 F3: an all-ones read before the unmask is accepted", "if (r != 0 || c == 0xFFFFFFFFu) {\n\t\t\tswi_gap(o, r == SWI_E_REFUSED", "if (r != 0) {\n\t\t\tswi_gap(o, r == SWI_E_REFUSED"),
    )


def mutants(core):
    """(name, mutated core): each must still compile and each must make the driver FAIL"""
    m = []
    m.append(("swap arm steps 4 and 5 (ELBI enable before GIC attach)", swap_blocks(core, "\tstep = 4;\n", "\tstep = 5;\n", "\tstep = 6;\n")))
    m.append(("swap arm steps 6 and 7 (0xC34 unmask before the SPI enable)", swap_blocks(core, "\tstep = 6;\n", "\tstep = 7;\n", "\treturn 0;\nfail:")))
    m.append(("swap arm steps 2 and 3 (ELBI W1C before the 0xC30 ack)", swap_blocks(core, "\tstep = 2;\n", "\tstep = 3;\n", "\tstep = 4;\n")))
    a = "\t\tif (extra != NULL) {\n\t\t\t*extra += o->drain(o->ctx);\n\t\t}\n"
    w = "\t\tr = swi_io(st, o, SWI_SP_BAR0, SWI_W, SWI_MBX_INT, &c);\n"
    assert a in core and w in core
    m.append(("ack loop drains before the 0xC30 ack", core.replace(a, "", 1).replace(w, a + w, 1)))
    ackw = "\t\tif (st->p_c30 != 0xFFFFFFFFu && c != 0u) {\n"
    assert core.count(ackw) == 1
    m.append(("thread acks after the drain (no ack in swi_pre)", core.replace(ackw, "\t\tif (0 && st->p_c30 != 0xFFFFFFFFu && c != 0u) {\n", 1)))
    # patch 0349: ack once per entry, stuck rule
    wr = "\t\t\tr = swi_io(st, o, SWI_SP_BAR0, SWI_W, SWI_MBX_INT, &c);      /* the bits just read (once per entry"
    assert core.count(wr) == 1
    m.append(("0349: W1C of the wrong value (a bit of the read dropped)", core.replace(wr, "\t\t\tc &= 0xFFFEu;\n" + wr, 1)))
    pb = "\tif (st->p_pend) {\n\t\tif (st->p_aerr != 0u) {"
    assert core.count(pb) == 1
    m.append(("0349: the old re-read/ack/drain loop is back in swi_post", core.replace(pb, "\tif (st->p_pend) {\n\t\t{ uint32_t x0 = 0; (void)swi_ack_loop(st, o, &x0); }\n\t\tif (st->p_aerr != 0u) {", 1)))
    for name, a, b in text_mutants_0349():
        assert core.count(a) == 1, name
        m.append((name, core.replace(a, b, 1)))
    isrw = "\tif (swi_io(st, o, SWI_SP_ELBI, SWI_W, SWI_ELBI_IRQ0, &w) != 0) {      /* W1C of bit 14 only */"
    assert isrw in core
    m.append(("ISR clears the whole status word (judge refuses)", core.replace(isrw, "\tw = s;\n" + isrw, 1)))
    j = "\t\treturn v == SWI_INTA;"
    assert j in core
    m.append(("ISR clears the whole status word AND the judge allows it", core.replace(isrw, "\tw = s;\n" + isrw, 1).replace(j, "\t\treturn (v & SWI_INTA) != 0u;", 1)))
    en = "\t\treturn (v == (st->en_last | SWI_INTA) && (st->en_last & ~SWI_INTA) == 0u) || v == (st->en_last & ~SWI_INTA);"
    assert en in core
    m.append(("judge allows any IRQ0_EN value", core.replace(en, "\t\treturn 1;", 1)))
    um = "\t\treturn v == SWI_MBX_ALL && ((st->elbi_on && st->spi_en) || (!st->elbi_on && !st->spi_en));"
    assert um in core
    m.append(("judge allows the 0xC34 unmask with one half armed", core.replace(um, "\t\treturn v == SWI_MBX_ALL;", 1)))
    c30 = "\t\treturn st->c30_valid && v != 0u && (v & ~SWI_MBX_ALL) == 0u && (v & ~st->c30_last) == 0u;"
    assert c30 in core
    m.append(("judge allows a 0xC30 ack of bits not read", core.replace(c30, "\t\treturn v != 0u;", 1)))
    t1 = "\tif (st->elbi_on) {\n\t\tif (swi_io(st, o, SWI_SP_ELBI, SWI_R, SWI_ELBI_IRQ0_EN, &en) != 0) {"
    t2 = "\tif (st->spi_att) {\n\t\tif (o->spi_detach(o->ctx) != 0) {"
    t3 = "\tif (bar0_ok && st->c34_known"
    m.append(("teardown detaches the SPI before the ELBI enable is cleared", swap_blocks(core, t1, t2, t3)))
    mm = "\t\tif (!st->p_pend && !fired) {"
    assert mm in core
    m.append(("missed counted when an interrupt did fire", core.replace(mm, "\t\tif (st->p_pend || fired) {", 1)))
    nn = "st->work_events >= SWI_NOIRQ_MAX"
    assert nn in core
    m.append(("no-interrupt threshold off by one", core.replace(nn, "st->work_events > SWI_NOIRQ_MAX", 1)))
    sm = "if (d > SWI_STORM_MAX) {"
    assert sm in core
    m.append(("storm threshold off by one", core.replace(sm, "if (d >= SWI_STORM_MAX) {", 1)))
    ro = "\t{ SWI_SP_ELBI, SWI_W, SWI_MB(SWI_MODE_DB),                       SWI_ELBI_IRQ0 },"
    assert ro in core
    m.append(("mode 2 gets a write row", core.replace(ro, "\t{ SWI_SP_ELBI, SWI_W, SWI_MB(SWI_MODE_DB) | SWI_MB(SWI_MODE_RO), SWI_ELBI_IRQ0 },", 1)))
    bit = "\tif ((s & SWI_INTA) == 0u) {"
    assert bit in core
    m.append(("ISR does not test bit 14", core.replace(bit, "\tif (0) {", 1)))
    um2 = "\t\tr = o->spi_unmask(o->ctx);\n"
    assert um2 in core
    m.append(("the SPI is never unmasked after an interrupt", core.replace(um2, "\t\tr = 0;\n", 1)))
    for name, a, b in (
        ("F1: the stuck-line (empty interrupt) threshold check removed", "\tif (st->empty_run >= SWI_EMPTY_MAX) {", "\tif (0) {"),
        ("F3: the ISR-error auto-off removed", "\tif (st->isr_err != 0u) {", "\tif (0) {"),
        ("F2: no gate check in swi_pre", "\tif (swi_gate_pass(st, o) != 0) {              /* gate first", "\tif (0) {              /* gate first"),
        ("F2: no gate check at the top of swi_post", "\tif (st->gate_closed) {                        /* gate first", "\tif (0) {                        /* gate first"),
        ("F2: no gate re-check in swi_post before the ack/unmask", "(st->p_pend || st->p_defer || got != 0u) && swi_gate_pass(st, o) != 0) {", "(st->p_pend || st->p_defer || got != 0u) && 0) {"),
        ("F2: no gate check after the drain in the ack loop", "\t\tif (swi_gate_pass(st, o) != 0) {         /* the drain may", "\t\tif (0) {         /* the drain may"),
    ):
        assert core.count(a) == 1, name
        m.append((name, core.replace(a, b, 1)))
    # reviewer mutant: the unmask runs before the stuck/empty rules (moved block)
    ub = "\tif (st->p_defer) {                          /* the line was masked by the ISR"
    i = core.index(ub)
    j = core.index("\treturn 0;\n}\n", i)
    sb = "\t/* patch 0349 (review F1): the doorbell went silent"
    k = core.index(sb)
    assert k < i
    m.append(("0349 R6: the unmask (with its re-check) runs before the silent/stuck/empty rules", core[:k] + core[i:j] + core[k:i] + core[j:]))
    return m


# reviewer mutant that is EQUIVALENT (review 0349 F4): the `p_aerr = 0` at the top of swi_pre is dropped. swi_pre sets p_aerr only inside its `if (st->p_pend)` block, after a second `p_aerr = 0` there, and every non-zero p_aerr
# makes swi_post disable the mode for good (a disabled mode never re-arms), so a stale value from an earlier pass can never be seen by a live pass. The test pins that it stays equivalent (same result as the unmutated core).
EQUIVALENT = [("0349 R7 (equivalent): p_aerr reset at the top of swi_pre dropped", "\tst->p_defer = 0;\n\tst->p_aerr = 0;\n", "\tst->p_defer = 0;\n")]


class Core(unittest.TestCase):
    def test_patch_core(self):
        ok, out = run(core_text())
        self.assertIsNotNone(ok, out); self.assertTrue(ok, out); self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        ms = mutants(core)
        self.assertGreaterEqual(len(ms), 51)
        for name, mc in ms:
            self.assertNotEqual(mc, core, "mutant did not change the core: %s" % name)
            ok, out = run(mc)
            self.assertIsNotNone(ok, "mutant does not compile (%s): %s" % (name, out))
            self.assertFalse(ok, "mutant survived: %s" % name)

    def test_equivalent_mutants_stay_equivalent(self):
        core = core_text()
        for name, a, b in EQUIVALENT:
            self.assertEqual(core.count(a), 1, name)
            ok, out = run(core.replace(a, b, 1))
            self.assertTrue(ok, "%s: not equivalent any more: %s" % (name, out))

    def test_glue_gap_row_in_0349(self):
        with open(PATCH2) as f:
            t = f.read()
        self.assertIn('{ SWI_G_SILENT, "doorbell silent: poll finds work, no interrupt" }', t)
        self.assertLessEqual(len("doorbell silent: poll finds work, no interrupt"), 63)
        self.assertIn("SWI_G_SILENT", core_text())

    def test_glue_text_in_patch(self):
        with open(PATCH) as f:
            t = f.read()
        for s in ("SWEET WLAN IRQ: ro elbi0=%#x en0=%#x c30=%#x rings_work=%u", "SWEET WLAN IRQ: irqs %u missed %u spurious %u storm %u mode %s", "PE_parse_boot_argn(\"sweetwifiirq\"",
                  "SW_IRQ_INTID 541u", "SW_G2K_MBX_ACK", "sw_irq_rows", "sweet_spi_attach_off(SW_IRQ_INTID", "swi_arm(&sw_irq, &sw_irq_ops)", "swi_teardown(&sw_irq, &sw_irq_ops, 0)", "!sw_irq.gate_closed", "BAR0 store refused: window is not the PCIe core", "h->cfg[SWW_CFG_WIN / 4] != SWW_WIN_PCIE"):
            self.assertIn(s, t)

    def test_no_arm_without_boot_arg_in_glue(self):
        """mode 0 byte-for-byte: every call site of the feature in the keep thread is behind sw_irq.mode / sw_irq_mode(); the loop's existing statements are unchanged lines"""
        with open(PATCH) as f:
            t = f.read()
        self.assertIn("irq_on_pass = sw_irq.mode != 0;", t)
        self.assertIn("if (irq_on_pass) {", t)
        for removed in re.findall(r"^-[^-].*$", t, re.M):
            self.assertNotRegex(removed, r"swg_keep_r2|en0_live_poll|thread_block|assert_wait_timeout", "an existing keep-thread statement was removed or changed")


    def test_0351_idle_wait_and_kick_flag(self):
        """patch 0351 (static: the glue is not run on the host): applies after 0350, the doorbell idle wait is 100000 us and chosen only through the live-doorbell test, the kick flag is set at the one kick site and taken after the wait"""
        d = os.path.dirname(PATCH)
        p50 = open(os.path.join(d, "0350-sweet-wlan-irq-ring2-work.patch")).read()
        t = open(os.path.join(d, "0351-sweet-wlan-irq-idle-wait.patch")).read()
        self.assertIn("ORDER: after 0350", t)
        self.assertIn("kr2_0 = x->kr2n;", p50)
        self.assertIn("#define SW_KEEP_IDLE_DB_US 100000u", t)
        self.assertIn("busy ? 100u : (sw_irq_db_live() ? SW_KEEP_IDLE_DB_US : 5000u)", t)
        self.assertRegex(t, r"sw_irq_db_live\(void\)\n\+\{\n\+\treturn sw_irq\.mode == SWI_MODE_DB && sw_irq\.on && !sw_irq\.gate_closed;")
        self.assertEqual(len(re.findall(r"^\+.*SW_KEEP_IDLE_DB_US", t, re.M)), 3)   # comment text, define, wait
        self.assertIn("os_atomic_store(&sw_keep.kickf, 1u, release);", t)
        self.assertIn("os_atomic_xchg(&sw_keep.kickf, 0u, acquire)", t)
        self.assertIn("if (sw_irq_peek() || kick_pend) {", t)
        self.assertLess(t.index("assert_wait_timeout((event_t)&sw_keep.kick"), t.index("os_atomic_xchg(&sw_keep.kickf"))
        self.assertLess(t.index("os_atomic_store(&sw_keep.kickf"), t.index("thread_wakeup((event_t)&sw_keep.kick);\n \t}"))
        for removed in re.findall(r"^-[^-].*$", t.split("--- a/")[1], re.M):
            self.assertNotRegex(removed, r"^-.*(swg_keep_r2|en0_live_poll|thread_block|SWI_)", "an existing statement was removed")
        self.assertFalse([l for l in t.split("--- a/")[1].splitlines() if l.startswith("+") and re.search(r"\b(swi_io|spi_write|bus_write|W1C)\b", l)])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s wlan-irq-core tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
