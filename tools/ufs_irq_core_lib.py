"""tools/test_ufs_irq_core.py support (patch 0354): the C driver that plays the UFS host controller, the core extraction from the patch text and the compile-and-run helper. See the test file for what is checked."""
import os, re, shutil, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0354-sweet-ufs-irq.patch")

DRIVER = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@CORE@
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); bad++; } } while (0)
#define CHKS(got, want) do { if (strcmp((got), (want)) != 0) { printf("FAIL line %d: sequence\n  got : %s\n  want: %s\n", __LINE__, (got), (want)); bad++; } } while (0)
#define SLOT 2u

/* ---- fake controller, GIC, clock and the call recorder ---- */
static char tok[16384];
static void T(const char *s) { if (strlen(tok) + strlen(s) + 2 > sizeof tok) return; strcat(tok, tok[0] ? " " : ""); strcat(tok, s); }   /* long runs (loops of hundreds of commands) are truncated; the asserted sequences are short */
static uint32_t h_is, h_ie, h_db, h_ia, h_cn, h_ver, h_ocs;
static int rd_fail_off, wr_fail_off, ie_stuck, attach_rc, enable_rc, enable_rb, detach_rc, wb_ret, accesses, n_wake, n_kick, n_wait_begin, n_wait_cancel, n_block, n_pause, n_ocs, n_detach;
static int db_reads_until_complete, hook_is_complete, spin_complete_at_pause, db_fail_at, db_reads;
static int is_rd_fail_at, is_rd_n;              /* the Nth read of IS since reset fails (the ISR's re-read) */
static uint32_t is_after_w1c;                 /* OR-ed into IS right after the next W1C of IS (a completion between the ISR's W1C and its re-read) */
static uint64_t t, last_wake_arg;
static int scen_q[16], scen_n, scen_i;           /* block() scenarios: 1 = interrupt (complete + ISR), 2 = silent completion + timeout wake, 3 = nothing happens + timeout wake, 4 = interrupt, command NOT complete */
static int notes[256], nnotes; static uint32_t note_a[256], note_b[256];
static struct sui *G; static const struct sui_ops *GO;

static void complete_cmd(void) { h_db &= ~SLOT; h_is |= 1; }
static int rd(void *c, uint32_t off, uint32_t *v) {
	char b[48]; (void)c;
	if ((int)off == rd_fail_off) return -1;
	if (off == 0x20 && is_rd_fail_at && ++is_rd_n == is_rd_fail_at) return -1;
	accesses++;
	switch (off) {
	case 0x08: *v = h_ver; break;
	case 0x20: *v = h_is; break;
	case 0x24: *v = h_ie; break;
	case 0x4C: *v = h_ia; break;
	case 0x58:
		db_reads++;
		if (db_fail_at && db_reads == db_fail_at) return -1;
		if (db_reads_until_complete > 0 && --db_reads_until_complete == 0) complete_cmd();
		*v = h_db; break;
	case 0x64: *v = h_cn; break;
	default: *v = 0xDEAD0000u; break;
	}
	snprintf(b, sizeof b, "R.%x.%x", off, *v); T(b);
	if (off == 0x20 && hook_is_complete) { hook_is_complete = 0; complete_cmd(); }   /* a completion right after the ISR's IS read, before its W1C and before the waiter's doorbell scan */
	return 0;
}
static int wr(void *c, uint32_t off, uint32_t v) {
	char b[48]; (void)c;
	if ((int)off == wr_fail_off) return -1;
	accesses++;
	snprintf(b, sizeof b, "W.%x.%x", off, v); T(b);
	if (off == 0x20) { h_is &= ~v; h_is |= is_after_w1c; is_after_w1c = 0; }
	else if (off == 0x24) { if (!ie_stuck) h_ie = v; }
	else { printf("FAIL: write to offset %#x reached the bus\n", off); bad++; }
	return 0;
}
static int s_att(void *c, uint32_t rb[4]) { (void)c; T("A"); rb[0] = 1; rb[1] = 0; rb[2] = 0x22; rb[3] = 0; return attach_rc; }
static int armed_at_enable = -1;
static int s_en(void *c, uint32_t *rb) { (void)c; T("S"); armed_at_enable = G->armed; *rb = (uint32_t)enable_rb; return enable_rc; }
static int s_det(void *c) { (void)c; T("X"); n_detach++; return detach_rc; }
static uint64_t now(void *c) { (void)c; return t; }
static void pause_(void *c) { (void)c; n_pause++; t += 1000; if (spin_complete_at_pause && n_pause == spin_complete_at_pause) complete_cmd(); }
static int wait_begin(void *c, uint64_t wake) { (void)c; n_wait_begin++; last_wake_arg = wake; T("WB"); return wb_ret; }
static void wait_cancel(void *c) { (void)c; n_wait_cancel++; T("WC"); }
static int block(void *c) {
	int s = scen_i < scen_n ? scen_q[scen_i++] : 3;
	(void)c; n_block++; T("BL");
	if (s == 1) { complete_cmd(); (void)sui_isr(G, GO, t); return 0; }
	if (s == 4) { h_is |= 1; (void)sui_isr(G, GO, t); return 0; }
	if (s == 5) { h_db &= ~SLOT; return 0; }                             /* woken by an unrelated wakeup, the command is done, no interrupt of ours */
	if (s == 6) { t = last_wake_arg + 1; wb_ret = 0; db_reads_until_complete = 3; return 1; }   /* timeout wake with nothing done; the next round's assert_wait refuses; the command completes silently two reads later */
	if (s == 2) { h_db &= ~SLOT; t = last_wake_arg + 1; return 1; }       /* a timeout wake is never early: the clock is one ns past the wake time */
	t = last_wake_arg + 1; return 1;
}
static void wake(void *c) { (void)c; n_wake++; T("K"); }
static void kick(void *c) { (void)c; n_kick++; T("KT"); }
static uint32_t ocs(void *c) { (void)c; n_ocs++; return h_ocs; }
static void note(void *c, int code, uint32_t a, uint32_t b) { (void)c; if (nnotes < 256) { notes[nnotes] = code; note_a[nnotes] = a; note_b[nnotes++] = b; } }
static struct sui_ops ops = { NULL, rd, wr, s_att, s_en, s_det, now, pause_, wait_begin, wait_cancel, block, wake, kick, ocs, note };
static int count_note(int code) { int n = 0; for (int i = 0; i < nnotes; i++) if (notes[i] == code) n++; return n; }
static int find_note(int code) { for (int i = 0; i < nnotes; i++) if (notes[i] == code) return i; return -1; }

static void reset(struct sui *st, int mode) {
	memset(st, 0, sizeof *st); st->mode = (uint8_t)mode; G = st; GO = &ops;
	tok[0] = 0; h_is = 0; h_ie = 0; h_db = 0; h_ia = 0; h_cn = 0x1234; h_ver = 0x300; h_ocs = 0;
	is_after_w1c = 0; is_rd_fail_at = is_rd_n = 0; rd_fail_off = wr_fail_off = -1; ie_stuck = attach_rc = enable_rc = detach_rc = 0; enable_rb = 1; wb_ret = 1; accesses = 0;
	n_wake = n_kick = n_wait_begin = n_wait_cancel = n_block = n_pause = n_ocs = n_detach = 0; db_reads_until_complete = hook_is_complete = spin_complete_at_pause = db_fail_at = db_reads = 0;
	t = 1000000000ull; last_wake_arg = 0; scen_n = scen_i = 0; nnotes = 0;
}
static void scen(int a, int b, int c) { scen_n = 0; if (a) scen_q[scen_n++] = a; if (b) scen_q[scen_n++] = b; if (c) scen_q[scen_n++] = c; scen_i = 0; }
static void armed(struct sui *st) { reset(st, SUI_MODE_ON); CHK(sui_arm(st, &ops) == 0); tok[0] = 0; nnotes = 0; accesses = 0; }
static uint64_t SPIN = 50000ull, DL = 5000000000ull;
static int do_wait(struct sui *st) { h_db = SLOT; sui_begin(st); return sui_wait(st, &ops, SLOT, SPIN, t + DL); }

static void t_mode_arg(void) {
	int g = -1;
	CHK(sui_mode_from_arg(0, 0, &g) == SUI_MODE_0 && g == 0);
	CHK(sui_mode_from_arg(0, 1, &g) == SUI_MODE_0 && g == 0);      /* not present: the value is ignored */
	CHK(sui_mode_from_arg(1, 0, &g) == SUI_MODE_0 && g == 0);
	CHK(sui_mode_from_arg(1, 1, &g) == SUI_MODE_ON && g == 0);
	CHK(sui_mode_from_arg(1, 2, &g) == SUI_MODE_RO && g == 0);
	CHK(sui_mode_from_arg(1, 3, &g) == SUI_MODE_0 && g == 1);       /* stage 2 is not in this patch */
	CHK(sui_mode_from_arg(1, 4, &g) == SUI_MODE_0 && g == 1);
	CHK(sui_mode_from_arg(1, 0xFFFFFFFFu, &g) == SUI_MODE_0 && g == 1);
	CHK(sui_mode_from_arg(1, 0x100u, &g) == SUI_MODE_0 && g == 1);
	CHK(sui_mode_from_arg(1, 0x10000001u, &g) == SUI_MODE_0 && g == 1);
}

static void t_mode0_zero_access(void) {
	struct sui st; reset(&st, SUI_MODE_0);
	h_is = 0x400; h_db = 0; h_ie = 0x470;
	CHK(sui_arm(&st, &ops) == -1);
	CHK(sui_ro_init(&st, &ops) == -1);
	sui_ro_cmd(&st, &ops);
	CHK(sui_isr(&st, &ops, t) == 1);
	sui_finish(&st, &ops);
	sui_ack(&st, &ops);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_POLL);
	sui_disarm(&st, &ops, SUI_Y_RESTART);
	CHK(accesses == 0);
	CHKS(tok, "");
	CHK(st.refused > 0);                                           /* every attempted access was stopped by the judge */
	CHK(n_wake == 0 && n_kick == 0 && n_wait_begin == 0 && n_detach == 0);
	CHK(h_is == 0x400 && h_ie == 0x470);                           /* the registers are as the driver left them */
	CHKS(sui_mode_str(&st), "off:refused");                        /* the first reason sticks: the wait above was refused by the judge, the later disarm does not overwrite it */
	reset(&st, SUI_MODE_0);
	CHKS(sui_mode_str(&st), "idle");
}

static void t_arm_order(void) {
	struct sui st; reset(&st, SUI_MODE_ON);
	h_ia = 0x00000000;
	CHK(sui_arm(&st, &ops) == 0);
	CHKS(tok, "R.58.0 A R.20.0 R.4c.0 W.24.1 R.24.1 S");
	CHK(st.armed && st.ie_on && st.spi_att && st.why == 0);
	CHKS(sui_mode_str(&st), "irq");
	CHK(h_ie == 1);
	CHK(find_note(SUI_N_ATTACH1) >= 0 && note_a[find_note(SUI_N_ATTACH1)] == 1 && note_b[find_note(SUI_N_ATTACH1)] == 0);
	CHK(find_note(SUI_N_ATTACH2) >= 0 && note_a[find_note(SUI_N_ATTACH2)] == 0x22);
	CHK(find_note(SUI_N_UTRIACR) >= 0 && find_note(SUI_N_ARMED) >= 0 && note_a[find_note(SUI_N_ARMED)] == 1);
	CHK(find_note(SUI_G_FIRST) < 0 && nnotes == 4);
	CHK(st.refused == 0 && armed_at_enable == 0);                  /* no access was refused, and `armed` was still 0 when the SPI was enabled */
	/* stale IS: W1C of exactly the bits read, before the IE write */
	reset(&st, SUI_MODE_ON); h_is = 0x400;
	CHK(sui_arm(&st, &ops) == 0);
	CHKS(tok, "R.58.0 A R.20.400 W.20.400 R.4c.0 W.24.1 R.24.1 S");
	CHK(h_is == 0);
	reset(&st, SUI_MODE_ON); h_is = 0x10401;
	CHK(sui_arm(&st, &ops) == 0);
	CHKS(tok, "R.58.0 A R.20.10401 W.20.10401 R.4c.0 W.24.1 R.24.1 S");
	/* aggregation enabled: logged as a GAP, UTRIACR is never written, the arm goes on */
	reset(&st, SUI_MODE_ON); h_ia = 0x80000010u;
	CHK(sui_arm(&st, &ops) == 0);
	CHKS(tok, "R.58.0 A R.20.0 R.4c.80000010 W.24.1 R.24.1 S");
	CHK(count_note(SUI_G_AGGR) == 1 && note_a[find_note(SUI_G_AGGR)] == 0x80000010u);
	reset(&st, SUI_MODE_ON); h_ia = 0x7FFFFFFFu;
	CHK(sui_arm(&st, &ops) == 0 && count_note(SUI_G_AGGR) == 0);
	/* arm is not repeated by accident: a second arm on an armed state is refused by the judge (IE write rule: IE is already 1) */
}

static void t_arm_failures(void) {
	struct sui st;
	/* a command is in flight: nothing is touched */
	reset(&st, SUI_MODE_ON); h_db = 2;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "R.58.2");
	CHK(!st.armed && !st.ie_on && !st.spi_att && st.why == SUI_Y_ARM_BUSY && count_note(SUI_G_ARM_BUSY) == 1 && note_a[0] == 2);
	CHKS(sui_mode_str(&st), "off:busy");
	/* GIC attach fails (19 no GICD): no IE write, no detach */
	reset(&st, SUI_MODE_ON); attach_rc = 19;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "R.58.0 A");
	CHK(!st.armed && !st.spi_att && st.why == SUI_Y_ATTACH && count_note(SUI_G_ATTACH) == 1 && note_a[find_note(SUI_G_ATTACH)] == 19);
	/* IE reads back wrong: disarm in order IE 0, IS, detach; GAP with the value read */
	reset(&st, SUI_MODE_ON); ie_stuck = 1; h_ie = 0x470;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "R.58.0 A R.20.0 R.4c.0 W.24.1 R.24.470 W.24.0 R.20.0 X");
	CHK(!st.armed && !st.ie_on && !st.spi_att && st.why == SUI_Y_IE_RB && count_note(SUI_G_IE_RB) == 1 && note_a[find_note(SUI_G_IE_RB)] == 0x470 && count_note(SUI_G_OFF) == 1);
	/* the SPI enable fails: disarm incl. the detach; and a wrong read-back bit counts as a failure too */
	reset(&st, SUI_MODE_ON); enable_rc = 22;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "R.58.0 A R.20.0 R.4c.0 W.24.1 R.24.1 S W.24.0 R.20.0 X");
	CHK(!st.armed && st.why == SUI_Y_ENABLE && count_note(SUI_G_ENABLE) == 1 && note_a[find_note(SUI_G_ENABLE)] == 22 && h_ie == 0);
	reset(&st, SUI_MODE_ON); enable_rb = 0;
	CHK(sui_arm(&st, &ops) == -1);
	CHK(!st.armed && st.why == SUI_Y_ENABLE && h_ie == 0 && n_detach == 1);
	/* bus failures: before the attach (nothing to undo), after it (undo) */
	reset(&st, SUI_MODE_ON); rd_fail_off = 0x58;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "");
	CHK(st.why == SUI_Y_BUS && count_note(SUI_G_ARM_ACC) == 1 && count_note(SUI_G_BUS) == 0 && note_a[find_note(SUI_G_ARM_ACC)] == 0x58 && note_b[find_note(SUI_G_ARM_ACC)] == 0 && !st.spi_att);   /* nothing disarmed: not the "disarming" note */
	reset(&st, SUI_MODE_ON); rd_fail_off = 0x4c;
	CHK(sui_arm(&st, &ops) == -1);
	CHKS(tok, "R.58.0 A R.20.0 R.20.0 X");
	CHK(st.why == SUI_Y_BUS && !st.armed && h_ie == 0 && n_detach == 1);
	CHK(count_note(SUI_G_BUS) == 1 && note_a[find_note(SUI_G_BUS)] == 0x4c);   /* the note names the register that failed (finding 3d) */
	reset(&st, SUI_MODE_ON); wr_fail_off = 0x24;
	CHK(sui_arm(&st, &ops) == -1);
	CHK(st.why == SUI_Y_BUS && !st.armed && !st.ie_on && n_detach == 1 && h_ie == 0);
	CHK(count_note(SUI_G_BUS) == 1 && note_a[find_note(SUI_G_BUS)] == 0x24);
	reset(&st, SUI_MODE_ON); h_is = 4; wr_fail_off = 0x20;
	CHK(sui_arm(&st, &ops) == -1);
	CHK(st.why == SUI_Y_BUS && !st.armed && n_detach == 1);
	CHK(count_note(SUI_G_BUS) == 1 && note_a[find_note(SUI_G_BUS)] == 0x20);
	reset(&st, SUI_MODE_ON); rd_fail_off = 0x20; CHK(sui_arm(&st, &ops) == -1 && note_a[find_note(SUI_G_BUS)] == 0x20);
	reset(&st, SUI_MODE_ON); rd_fail_off = 0x24;                      /* IE read-back fails: IE was written, so it must be written back to 0 */
	CHK(sui_arm(&st, &ops) == -1);
	CHK(h_ie == 0 && !st.ie_on && n_detach == 1 && st.why == SUI_Y_BUS);
	/* a detach that fails is a GAP, the state is still cleared */
	reset(&st, SUI_MODE_ON); enable_rc = 22; detach_rc = 5;
	CHK(sui_arm(&st, &ops) == -1);
	CHK(count_note(SUI_G_TEARDOWN) == 1 && note_a[find_note(SUI_G_TEARDOWN)] == 3 && !st.spi_att);
	/* the first reason wins */
	reset(&st, SUI_MODE_ON); enable_rc = 22;
	CHK(sui_arm(&st, &ops) == -1 && st.why == SUI_Y_ENABLE);
	sui_disarm(&st, &ops, SUI_Y_STORM);
	CHK(st.why == SUI_Y_ENABLE);
}

static void t_isr_one_per_completion(void) {
	struct sui st; armed(&st);
	h_db = SLOT; complete_cmd();
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.1 R.24.1 W.20.1 R.20.0 K");
	CHK(st.isr_total == 1 && st.isr_seq == 1 && st.irqs == 1 && st.spurious == 0 && n_wake == 1 && h_is == 0);
	/* a second completion = a second interrupt = one more sequence step */
	complete_cmd(); tok[0] = 0;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHK(st.isr_seq == 2 && st.irqs == 2 && n_wake == 2);
	/* the ISR never issues anything but the two reads and the one W1C */
	CHK(count_note(SUI_G_FIRST) == 0);
}

static void t_isr_bits(void) {
	struct sui st;
	/* no bit: spurious, no write at all */
	armed(&st); h_is = 0;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.0 R.24.1");
	CHK(st.spurious == 1 && st.irqs == 0 && st.isr_seq == 0 && n_wake == 0);
	/* a bit that is not enabled: cleared (exactly), counted spurious, remembered for the GAP, no wake */
	armed(&st); h_is = 0x400;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.400 R.24.1 W.20.400 R.20.0");
	CHK(st.spurious == 1 && st.irqs == 0 && st.err_is == 0x400 && n_wake == 0 && h_is == 0);
	/* UTRCS together with another bit: W1C of the whole value read, interrupt counted, the other bit kept for the GAP */
	armed(&st); h_is = 0x401;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.401 R.24.1 W.20.401 R.20.0 K");
	CHK(st.irqs == 1 && st.isr_seq == 1 && st.err_is == 0x400 && n_wake == 1);
	/* UTRCS set but IE reads 0 (the enable was lost): not our interrupt, spurious, W1C of the bits read, no wake */
	armed(&st); h_is = 1; h_ie = 0;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.1 R.24.0 W.20.1 R.20.0");
	CHK(st.spurious == 1 && st.irqs == 0 && st.isr_seq == 0 && n_wake == 0);
	/* a bit set AFTER the read is never cleared by the W1C (hardware sets 0x400 between the read and the write) */
	armed(&st); h_is = 1;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHK(h_is == 0);
	h_is = 1; CHK(sui_isr(&st, &ops, t) == 0);
	/* ISR on a state that is not ON */
	struct sui s0; reset(&s0, SUI_MODE_RO);
	CHK(sui_isr(&s0, &ops, t) == 1 && accesses == 0);
	/* a failing read or write in the ISR turns the mode off through the ISR path */
	armed(&st); h_is = 1; rd_fail_off = 0x24;
	CHK(sui_isr(&st, &ops, t) == 1);
	CHK(!st.armed && st.off_pending && st.why == SUI_Y_ISRERR && st.isr_err == 1 && h_ie == 0 && n_kick == 1);
	armed(&st); h_is = 1; wr_fail_off = 0x20;
	CHK(sui_isr(&st, &ops, t) == 1);
	CHK(!st.armed && st.off_pending && st.why == SUI_Y_ISRERR && h_ie == 0);
}

static void t_isr_storm(void) {
	struct sui st; armed(&st);
	uint64_t t0 = t;
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0 + (uint64_t)i * 1000) == 0);       /* 100 spurious entries in one second are tolerated */
	CHK(st.armed && st.spurious == 100 && !st.off_pending);
	/* a legitimate interrupt does not count towards the window */
	h_is = 1; CHK(sui_isr(&st, &ops, t0 + 200000) == 0); h_is = 0;
	CHK(st.armed && st.irqs == 1);
	tok[0] = 0;
	CHK(sui_isr(&st, &ops, t0 + 300000) == 1);                                                  /* the 101st: off, the dispatcher masks the line */
	CHK(!st.armed && st.off_pending && st.why == SUI_Y_STORM && h_ie == 0 && n_kick == 1 && !st.ie_on);
	CHKS(tok, "R.20.0 R.24.1 W.24.0 KT");
	CHKS(sui_mode_str(&st), "off:storm");
	/* the thread finishes the teardown: IS, detach, one GAP */
	tok[0] = 0; nnotes = 0;
	sui_finish(&st, &ops);
	CHKS(tok, "R.20.0 X");
	CHK(!st.off_pending && !st.spi_att && count_note(SUI_G_OFF) == 1 && note_a[find_note(SUI_G_OFF)] == SUI_Y_STORM && note_b[find_note(SUI_G_OFF)] == 1);
	tok[0] = 0; sui_finish(&st, &ops); CHKS(tok, "");
	/* legitimate interrupts are not part of the storm count: 150 of them and 100 spurious entries in one window are fine */
	armed(&st); t0 = 3000000000ull;
	for (int i = 0; i < 150; i++) { h_is = 1; CHK(sui_isr(&st, &ops, t0 + (uint64_t)i) == 0); }
	h_is = 0;
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0 + 1000u + (uint64_t)i) == 0);
	CHK(st.armed && st.irqs == 150 && st.spurious == 100);
	/* the window is one second: 100 entries, then a second later 100 more are fine */
	armed(&st); t0 = 5000000000ull;
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0 + (uint64_t)i) == 0);
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0 + 1000000000ull + (uint64_t)i) == 0);
	CHK(st.armed);
	/* exactly 1 s after the window start the window restarts (>= WIN) */
	armed(&st); t0 = 7000000000ull;
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0) == 0);
	CHK(sui_isr(&st, &ops, t0 + 999999999ull) == 1);
	armed(&st); t0 = 7000000000ull;
	for (int i = 0; i < 100; i++) CHK(sui_isr(&st, &ops, t0) == 0);
	CHK(sui_isr(&st, &ops, t0 + 1000000000ull) == 0 && st.armed);
	/* storm with the IE write failing: the state still says off */
	armed(&st); t0 = 9000000000ull; for (int i = 0; i < 100; i++) (void)sui_isr(&st, &ops, t0);
	wr_fail_off = 0x24; CHK(sui_isr(&st, &ops, t0) == 1); CHK(!st.armed && st.off_pending);
}

static void t_wait_basic(void) {
	struct sui st;
	/* already done at the first read: nothing but one doorbell read; counted fast */
	armed(&st); h_db = 0; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
	CHKS(tok, "R.58.0");
	CHK(st.cmds == 1 && st.fast == 1 && st.hits == 0 && st.poll_found == 0 && n_wait_begin == 0 && n_pause == 0);
	/* done while spinning (the third spin step) */
	armed(&st); spin_complete_at_pause = 3;
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(n_pause == 3 && n_wait_begin == 0 && st.fast == 1 && st.cmds == 1);
	/* the interrupt wakes the waiter: spin, assert_wait, re-read, block, read; classified as a hit */
	armed(&st); scen(1, 0, 0);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(n_pause == 50 && n_wait_begin == 1 && n_block == 1 && n_wait_cancel == 0);
	CHK(strstr(tok, "WB R.58.2 BL R.20.1 R.24.1 W.20.1 R.20.0 K R.58.0") != NULL);
	CHK(st.hits == 1 && st.poll_found == 0 && st.fast == 0 && st.tmo_wakes == 0 && st.irqs == 1 && st.cmds == 1);
	/* the fallback wake is min(now + 1 ms, deadline): here 1 ms (the first block happens after the 50 spin steps of 1 us) */
	armed(&st); scen(1, 0, 0); do_wait(&st);
	CHK(last_wake_arg == 1000000000ull + 50000ull + SUI_FALLBACK_NS);
	/* near the deadline the wake is the deadline */
	armed(&st); scen(1, 0, 0); h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, 0, t + 400000ull) == SUI_W_DONE);
	CHK(last_wake_arg == 1000000000ull + 400000ull);
	/* a timeout wake that finds the command done: poll_found, no interrupt since the command was issued */
	armed(&st); scen(2, 0, 0);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(st.poll_found == 1 && st.hits == 0 && st.fast == 0 && st.tmo_wakes == 1 && st.irqs == 0 && st.cmds == 1);
	/* two timeout wakes with nothing done, then the completion on the third */
	armed(&st); scen(3, 3, 2);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(n_block == 3 && st.tmo_wakes == 3 && st.poll_found == 1 && st.cmds == 1);
	/* woken by something else (no timeout, no interrupt of ours), command done: fast, neither hit nor poll_found */
	armed(&st); scen(5, 0, 0);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(st.fast == 1 && st.poll_found == 0 && st.hits == 0 && st.tmo_wakes == 0 && n_block == 1);
	/* a timeout wake with nothing done, then a round without a wait, then the completion: the earlier timeout wake does not make it a poll_found */
	armed(&st); scen(6, 0, 0);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(st.fast == 1 && st.poll_found == 0 && st.hits == 0 && st.tmo_wakes == 1 && n_block == 1);
	/* the sequence is compared with the value taken before THIS command: a second command without an interrupt is not a hit */
	armed(&st); scen(1, 0, 0); CHK(do_wait(&st) == SUI_W_DONE);
	scen(2, 0, 0); CHK(do_wait(&st) == SUI_W_DONE);
	CHK(st.hits == 1 && st.poll_found == 1 && st.cmds == 2 && st.isr_seq == 1);
	/* an interrupt arrives but the command is NOT complete (stale interrupt): back to waiting; the command ends on a timeout wake with a changed sequence = a hit */
	armed(&st); scen(4, 2, 0);
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(n_block == 2 && st.hits == 1 && st.poll_found == 0 && st.irqs == 1);
	/* completion between assert_wait and the re-read: wait cancelled, no block */
	armed(&st); db_reads_until_complete = 52;      /* read 1 = loop top, reads 2.. = spin; the 52nd read is the re-read after assert_wait (51 loop reads + 1) */
	h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
	CHK(n_wait_begin == 1 && n_wait_cancel == 1 && n_block == 0);
	CHK(strstr(tok, "WB R.58.0 WC") != NULL);
	CHK(st.fast == 1 && st.poll_found == 0 && st.hits == 0);
	/* assert_wait refused (not waiting): no cancel, no block, the loop goes round again */
	armed(&st); wb_ret = 0; db_reads_until_complete = 56;
	CHK(do_wait(&st) == SUI_W_DONE);
	CHK(n_block == 0 && n_wait_cancel == 0 && n_wait_begin >= 2);
}

static void t_wait_race_isr(void) {
	struct sui st;
	/* the ISR reads IS (stale UTRCS of the previous command), the command completes right after that read, before the ISR's W1C and before the waiter's doorbell scan:
	 * the W1C swallows the new UTRCS, and the waiter still finds the command done through the doorbell (the completion is not lost) */
	armed(&st); h_is = 1; hook_is_complete = 1; h_db = SLOT;
	sui_begin(&st);
	CHK(sui_isr(&st, &ops, t) == 0);                               /* the interrupt for the previous command lands now */
	CHK(h_db == 0 && h_is == 0);                                   /* command done, its status bit cleared by the exact-bits W1C */
	tok[0] = 0;
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
	CHKS(tok, "R.58.0");
	CHK(st.hits == 1 && n_block == 0);
	/* the same race through block(): the interrupt of the stale completion, command done at once */
	armed(&st); h_is = 1; hook_is_complete = 1; scen(4, 0, 0);
	h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
	CHK(n_block == 1 && n_wait_cancel == 0 && st.hits == 1 && st.cmds == 1);
	/* the interrupt of this command arrives AFTER the waiter saw the doorbell clear: the ISR then finds UTRCS set, counts an interrupt (not spurious) and wakes nobody */
	armed(&st); h_db = 0; sui_begin(&st); (void)sui_wait(&st, &ops, SLOT, SPIN, t + DL);
	CHK(st.fast == 1);
	h_is = 1; CHK(sui_isr(&st, &ops, t) == 0);
	CHK(st.spurious == 0 && st.irqs == 1);
}

static void t_wait_timeout_and_noirq(void) {
	struct sui st;
	/* deadline passes: SUI_W_TIMEOUT, no register write by the core (the caller does UTRLCLR itself), cmds counted */
	armed(&st); h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + 3000000ull) == SUI_W_TIMEOUT);   /* 3 ms deadline: spin 50 us, then 1 ms wakes (scenario 3) until the deadline */
	CHK(st.cmds == 1 && st.hits == 0 && st.poll_found == 0 && st.fast == 0 && st.armed && n_block >= 2);
	CHK(strstr(tok, "W.") == NULL);
	/* a deadline equal to the clock is not past yet (now > deadline): one more round, the re-read finds the command done */
	armed(&st); h_db = SLOT; sui_begin(&st); db_reads_until_complete = 2;
	CHK(sui_wait(&st, &ops, SLOT, 0, t) == SUI_W_DONE && n_wait_begin == 1 && n_wait_cancel == 1);
	/* a deadline that is already past */
	armed(&st); h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t - 1) == SUI_W_TIMEOUT && n_block == 0 && n_wait_begin == 0);
	/* 200 consecutive commands with no interrupt: the 200th disarms (199 do not), polling again, GAP, IE 0 */
	armed(&st);
	for (int i = 0; i < 199; i++) { scen(2, 0, 0); CHK(do_wait(&st) == SUI_W_DONE); }
	CHK(st.armed && st.noirq_run == 199 && st.poll_found == 199);
	scen(2, 0, 0); tok[0] = 0; CHK(do_wait(&st) == SUI_W_DONE);
	CHK(!st.armed && st.why == SUI_Y_NOIRQ && !st.ie_on && !st.spi_att && h_ie == 0 && count_note(SUI_G_OFF) == 1 && st.cmds == 200);
	CHK(strstr(tok, "W.24.0 R.20.0 X") != NULL);
	CHKS(sui_mode_str(&st), "off:noirq");
	/* one interrupt anywhere resets the run, also one that arrives late (between two commands) */
	armed(&st);
	for (int i = 0; i < 150; i++) { scen(2, 0, 0); do_wait(&st); }
	h_is = 1; (void)sui_isr(&st, &ops, t);                         /* a late interrupt between two commands */
	for (int i = 0; i < 150; i++) { scen(2, 0, 0); do_wait(&st); }
	CHK(st.armed && st.noirq_run == 149);                          /* the first command after the late interrupt only moves the mark */
	for (int i = 0; i < 50; i++) { scen(2, 0, 0); do_wait(&st); }
	CHK(st.armed && st.noirq_run == 199);
	scen(2, 0, 0); do_wait(&st);
	CHK(!st.armed && st.why == SUI_Y_NOIRQ);
	/* commands that do get interrupts never trip it */
	armed(&st);
	for (int i = 0; i < 500; i++) { scen(1, 0, 0); do_wait(&st); }
	CHK(st.armed && st.hits == 500 && st.noirq_run == 0);
	/* interrupts every 100th command: never 200 in a row */
	armed(&st);
	for (int i = 0; i < 1000; i++) { if (i % 100 == 99) scen(1, 0, 0); else scen(2, 0, 0); do_wait(&st); }
	CHK(st.armed);
}

static void t_wait_bus(void) {
	struct sui st;
	armed(&st); rd_fail_off = 0x58; h_db = SLOT; sui_begin(&st);
	CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_POLL);
	CHK(!st.armed && st.why == SUI_Y_BUS && count_note(SUI_G_BUS) == 1 && h_ie == 0 && n_detach == 1);
	/* the failing read is the re-read after assert_wait: the assert is cancelled first */
	armed(&st); h_db = SLOT; sui_begin(&st); db_fail_at = db_reads + 2;
	CHK(sui_wait(&st, &ops, SLOT, 0, t + DL) == SUI_W_POLL);
	CHK(n_wait_begin == 1 && n_wait_cancel == 1 && n_block == 0 && !st.armed && st.why == SUI_Y_BUS && h_ie == 0);
	armed(&st); h_db = SLOT; sui_begin(&st); db_fail_at = db_reads + 2; wb_ret = 0;      /* not waiting: nothing to cancel */
	CHK(sui_wait(&st, &ops, SLOT, 0, t + DL) == SUI_W_POLL && n_wait_cancel == 0);
	/* judge refusal in the wait (state mode 0): POLL, the caller's loop finishes the command */
	struct sui s2; reset(&s2, SUI_MODE_0); h_db = SLOT; sui_begin(&s2);
	CHK(sui_wait(&s2, &ops, SLOT, SPIN, t + DL) == SUI_W_POLL && s2.why == SUI_Y_REFUSED);
}

static void t_ack(void) {
	struct sui st;
	/* while armed the waiter's ack NEVER writes IS (review 0354 finding 1): the ISR is the only writer */
	armed(&st); h_is = 1;
	sui_ack(&st, &ops);
	CHKS(tok, "R.20.1");
	CHK(nnotes == 0 && h_is == 1);
	armed(&st); h_is = 0;
	sui_ack(&st, &ops);
	CHKS(tok, "R.20.0");
	armed(&st); h_is = 0x401;
	sui_ack(&st, &ops);
	CHKS(tok, "R.20.401");
	CHK(count_note(SUI_G_OTHER_IS) == 1 && note_a[0] == 0x400 && note_b[0] == 1 && h_is == 0x401);
	armed(&st); h_is = 0x400;
	sui_ack(&st, &ops);
	CHKS(tok, "R.20.400");
	CHK(count_note(SUI_G_OTHER_IS) == 1 && h_is == 0x400);
	/* bits the ISR collected are reported as well, once */
	armed(&st); st.err_is = 0x800; h_is = 0;
	sui_ack(&st, &ops);
	CHK(count_note(SUI_G_OTHER_IS) == 1 && note_a[0] == 0x800 && st.err_is == 0);
	sui_ack(&st, &ops); CHK(count_note(SUI_G_OTHER_IS) == 1);
	/* rate limit: the first 8, then every 1024th */
	armed(&st);
	for (int i = 0; i < 3000; i++) { h_is = 0x400; sui_ack(&st, &ops); }
	CHK(count_note(SUI_G_OTHER_IS) == 8 + 2);                      /* 8 + #1024 + #2048 */
	/* a failing IS read is a GAP of its own (finding 3a): the mode stays armed, nothing is disarmed, no "disarming" text code, rate limited like the others */
	armed(&st); rd_fail_off = 0x20; sui_ack(&st, &ops);
	CHK(count_note(SUI_G_ACCESS) == 1 && count_note(SUI_G_BUS) == 0 && count_note(SUI_G_REFUSED) == 0 && note_a[0] == 0x20 && note_b[0] == ((1u << 8) | 0u));
	CHK(st.armed && st.why == SUI_Y_NONE && n_detach == 0 && h_ie == 1);
	for (int i = 0; i < 3000; i++) sui_ack(&st, &ops);
	CHK(count_note(SUI_G_ACCESS) == 8 + 2);                        /* 1 + 3000 failures: #1..#8, #1024, #2048 */
	/* a refused read (judge, here: the state left mode ON) is reported as refused, not as a bus failure */
	struct sui s2; reset(&s2, SUI_MODE_0); sui_ack(&s2, &ops);
	CHK(count_note(SUI_G_ACCESS) == 1 && note_b[0] == ((1u << 8) | 1u) && count_note(SUI_G_BUS) == 0);
}

/* the reviewer's race (review 0354 finding 1): the waiter, on another CPU, sees the doorbell clear and acks BEFORE the ISR on CPU0 runs. Order per command: complete, wait, ack, ISR.
 * An ack that wrote IS made the ISR read 0: a real interrupt counted as spurious (storm off) and as no interrupt (noirq off). */
static void t_ack_isr_race(void) {
	static const struct { int n; uint64_t step_ns; } runs[] = { { 101, 5000000ull }, { 400, 20000000ull } };   /* 101 commands in 0.5 s, 50 commands/s for 400 commands */
	for (size_t r = 0; r < sizeof runs / sizeof runs[0]; r++) {
		struct sui st; armed(&st);
		for (int i = 0; i < runs[r].n; i++) {
			h_db = SLOT; sui_begin(&st); complete_cmd();
			CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
			sui_ack(&st, &ops);
			CHK(h_is == 1);                                        /* the ack left IS alone */
			CHK(sui_isr(&st, &ops, t) == 0);
			t += runs[r].step_ns;
		}
		CHK(st.armed && st.why == SUI_Y_NONE && !st.off_pending && st.spurious == 0 && st.irqs == (uint32_t)runs[r].n && st.isr_total == (uint32_t)runs[r].n && h_ie == 1 && n_detach == 0);
		CHKS(sui_mode_str(&st), "irq");
	}
	/* one interrupt covers two commands (coalesced: UTRCS is one bit): 600 commands, 300 interrupts, no spurious entry, no false no-interrupt disarm */
	struct sui st; armed(&st);
	for (int i = 0; i < 600; i++) {
		h_db = SLOT; sui_begin(&st); complete_cmd();
		CHK(sui_wait(&st, &ops, SLOT, SPIN, t + DL) == SUI_W_DONE);
		sui_ack(&st, &ops);
		if (i & 1) CHK(sui_isr(&st, &ops, t) == 0);
		t += 5000000ull;
	}
	CHK(st.armed && st.why == SUI_Y_NONE && st.spurious == 0 && st.irqs == 300 && st.cmds == 600 && st.noirq_run < 4);
	/* the ISR of command N runs during command N+1's wait: IS (set by N) is still set, the interrupt is a real one, the wait ends through it, nothing is spurious */
	armed(&st); h_db = SLOT; complete_cmd(); sui_ack(&st, &ops);   /* command N done, its ISR has not run */
	scen(1, 0, 0); CHK(do_wait(&st) == SUI_W_DONE);                  /* N+1: the ISR runs in block() */
	CHK(st.armed && st.spurious == 0 && st.irqs == 1 && h_is == 0);
}

/* review 0354 finding 4: the ISR reads IS again after its W1C (Linux ufshcd_intr :6862); the value is only counted */
static void t_isr_reread(void) {
	struct sui st; armed(&st); h_is = 1; is_after_w1c = 0x400;
	CHK(sui_isr(&st, &ops, t) == 0);
	CHKS(tok, "R.20.1 R.24.1 W.20.1 R.20.400 K");
	CHK(st.isr_again == 1 && st.irqs == 1 && st.spurious == 0 && h_is == 0x400);        /* counted, not written, not cleared here */
	armed(&st); h_is = 1; tok[0] = 0;
	CHK(sui_isr(&st, &ops, t) == 0 && st.isr_again == 0);
	/* a completion lands between the W1C and the re-read: it is not part of THIS entry (the value is only counted); the next entry handles it, nothing is lost */
	armed(&st); h_is = 0x400; is_after_w1c = 1;
	CHK(sui_isr(&st, &ops, t) == 0 && st.isr_again == 1 && st.irqs == 0 && st.spurious == 1 && n_wake == 0 && h_is == 1);
	CHK(sui_isr(&st, &ops, t + 1) == 0 && st.irqs == 1 && st.spurious == 1 && n_wake == 1 && h_is == 0);
	armed(&st); h_is = 0; CHK(sui_isr(&st, &ops, t) == 0);                              /* IS == 0: no W1C, no re-read */
	CHKS(tok, "R.20.0 R.24.1");
	/* a failing re-read is an ISR error like the other reads: IE := 0 and the mode off */
	armed(&st); h_is = 1; rd_fail_off = 0x20;
	CHK(sui_isr(&st, &ops, t) == 1 && !st.armed && st.off_pending && st.why == SUI_Y_ISRERR);
	armed(&st); h_is = 1; is_rd_fail_at = 2;                                            /* only the RE-READ fails */
	CHK(sui_isr(&st, &ops, t) == 1 && !st.armed && st.off_pending && st.why == SUI_Y_ISRERR && st.isr_err == 1 && h_ie == 0 && n_wake == 0);
}

/* review 0354 finding 2: lock-free IE := 0 on every reset path */
static void t_ie_off(void) {
	struct sui st; armed(&st);
	sui_ie_off(&st, &ops);
	CHKS(tok, "W.24.0");                                           /* exactly one write: register 0x24, value 0, nothing else (no read, no IS, no detach) */
	CHK(!st.armed && !st.ie_on && h_ie == 0 && nnotes == 0 && n_detach == 0 && n_kick == 0 && n_wake == 0);
	tok[0] = 0; sui_ie_off(&st, &ops); CHKS(tok, "");              /* idempotent */
	/* nothing to undo: never armed (mode ON), mode 0, mode RO, arm refused = no register access at all */
	reset(&st, SUI_MODE_ON); sui_ie_off(&st, &ops); CHK(accesses == 0 && nnotes == 0);
	reset(&st, SUI_MODE_0); st.ie_on = 1; sui_ie_off(&st, &ops); CHK(accesses == 0);
	reset(&st, SUI_MODE_RO); st.ie_on = 1; sui_ie_off(&st, &ops); CHK(accesses == 0);
	/* a teardown in progress (ISR storm path wrote nothing yet: ie_on set, armed already clear): still written */
	armed(&st); st.armed = 0; sui_ie_off(&st, &ops); CHKS(tok, "W.24.0");
	/* a failing write must not print, block or throw: silent by design (reset context), state still says off */
	armed(&st); wr_fail_off = 0x24; sui_ie_off(&st, &ops); CHK(!st.armed && nnotes == 0 && n_detach == 0);
}

static void t_ro(void) {
	struct sui st; reset(&st, SUI_MODE_RO);
	h_ia = 0x7; h_cn = 0x55; h_ie = 0x470; h_is = 0x400; h_ver = 0x300;
	CHK(sui_ro_init(&st, &ops) == 0);
	CHKS(tok, "R.58.0 R.4c.7 R.64.55 R.24.470 R.20.400 R.8.300");
	CHK(nnotes == 3 && notes[0] == SUI_N_RO1 && note_a[0] == 7 && note_b[0] == 0x55 && notes[1] == SUI_N_RO2 && note_a[1] == 0x470 && note_b[1] == 0x400 && notes[2] == SUI_N_RO3 && note_a[2] == 0x300 && note_b[2] == 0);
	CHK(h_is == 0x400 && h_ie == 0x470);                           /* reads only */
	/* a command in flight is logged, the reads go on */
	reset(&st, SUI_MODE_RO); h_db = 2;
	CHK(sui_ro_init(&st, &ops) == 0 && count_note(SUI_G_ARM_BUSY) == 1);
	/* a failing read */
	reset(&st, SUI_MODE_RO); rd_fail_off = 0x64;
	CHK(sui_ro_init(&st, &ops) == -1 && count_note(SUI_G_ACCESS) == 1 && count_note(SUI_G_BUS) == 0 && note_a[0] == 0x64 && (note_b[0] >> 8) == 3);   /* names the register; no "disarming" code in a mode that has nothing to disarm */
	/* ro_init in another mode */
	reset(&st, SUI_MODE_ON); CHK(sui_ro_init(&st, &ops) == -1 && accesses == 0);
	/* per command */
	reset(&st, SUI_MODE_RO); h_is = 1; h_ocs = 0x0f; tok[0] = 0;
	sui_ro_cmd(&st, &ops);
	CHKS(tok, "R.20.1");
	CHK(st.ro_cmds == 1 && st.ro_is0 == 1 && st.ro_ocs0f == 1 && st.ro_isx == 0 && n_ocs == 1);
	h_is = 1; h_ocs = 0; sui_ro_cmd(&st, &ops);
	CHK(st.ro_cmds == 2 && st.ro_is0 == 2 && st.ro_ocs0f == 1 && n_ocs == 2);
	h_is = 0; sui_ro_cmd(&st, &ops);
	CHK(st.ro_cmds == 3 && st.ro_is0 == 2 && n_ocs == 2);          /* the OCS is read only when UTRCS is set */
	h_is = 0x400; sui_ro_cmd(&st, &ops);
	CHK(st.ro_cmds == 4 && st.ro_is0 == 2 && st.ro_isx == 1 && n_ocs == 2);
	h_is = 0x401; h_ocs = 0x0f; sui_ro_cmd(&st, &ops);
	CHK(st.ro_is0 == 3 && st.ro_isx == 2 && st.ro_ocs0f == 2);
	CHK(h_is == 0x401);                                            /* never written */
	h_ocs = 0x10f; h_is = 1; sui_ro_cmd(&st, &ops); CHK(st.ro_ocs0f == 3);   /* only the low byte of the OCS word counts */
	h_ocs = 0x1; h_is = 1; sui_ro_cmd(&st, &ops); CHK(st.ro_ocs0f == 3);
	rd_fail_off = 0x20; nnotes = 0; sui_ro_cmd(&st, &ops); CHK(count_note(SUI_G_ACCESS) == 1 && count_note(SUI_G_BUS) == 0 && (note_b[0] >> 8) == 2 && st.ro_cmds == 7);
	for (int i = 0; i < 3000; i++) sui_ro_cmd(&st, &ops);
	CHK(count_note(SUI_G_ACCESS) == 8 + 2 && st.ro_cmds == 7 && h_is == 1);   /* once per command would be 3001 lines: rate limited */
	/* other modes: nothing */
	reset(&st, SUI_MODE_ON); h_is = 1; sui_ro_cmd(&st, &ops); CHK(accesses == 0 && st.ro_cmds == 0);
	/* the RO mode has no write row: a write is refused before the bus, whatever the value */
	reset(&st, SUI_MODE_RO); h_is = 1; (void)sui_rd(&st, &ops, SUI_REG_IS, &(uint32_t){0});
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 1) == SUI_E_REFUSED && sui_wr(&st, &ops, SUI_REG_IE, 0) == SUI_E_REFUSED && sui_wr(&st, &ops, SUI_REG_IE, 1) == SUI_E_REFUSED);
	CHK(h_is == 1 && h_ie == 0);
	CHKS(sui_mode_str(&st), "ro");
}

static void t_judge(void) {
	struct sui st; uint32_t v = 0;
	/* allowlist: nothing but the rows is reachable, in mode ON */
	armed(&st);
	static const uint32_t rd_ok[] = { 0x08, 0x20, 0x24, 0x4C, 0x58, 0x64 };
	for (size_t i = 0; i < sizeof rd_ok / sizeof rd_ok[0]; i++) CHK(sui_rd(&st, &ops, rd_ok[i], &v) == 0);
	static const uint32_t rd_bad[] = { 0x00, 0x04, 0x0c, 0x28, 0x30, 0x34, 0x38, 0x3c, 0x40, 0x44, 0x48, 0x50, 0x54, 0x5c, 0x60, 0x68, 0x78, 0x100, 0x1100, 0x1140 };
	for (size_t i = 0; i < sizeof rd_bad / sizeof rd_bad[0]; i++) CHK(sui_rd(&st, &ops, rd_bad[i], &v) == SUI_E_REFUSED);
	static const uint32_t wr_bad[] = { 0x00, 0x08, 0x4C, 0x58, 0x5C, 0x60, 0x64, 0x78, 0x34, 0x30, 0x1100, 0x1140 };
	h_db = 0;
	for (size_t i = 0; i < sizeof wr_bad / sizeof wr_bad[0]; i++) CHK(sui_wr(&st, &ops, wr_bad[i], 1) == SUI_E_REFUSED && sui_wr(&st, &ops, wr_bad[i], 0) == SUI_E_REFUSED && sui_wr(&st, &ops, wr_bad[i], 0xFFFFFFFFu) == SUI_E_REFUSED);
	CHK(st.refused >= 20 + 36);
	/* IS write rules: only bits that were read set (the arm read nothing set, so only the waiter's 0x1 is possible) */
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0xFFFFFFFFu) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x400) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x7FFFFFFFu) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x1) == SUI_E_REFUSED);           /* no "exactly UTRCS" clause any more: the waiter does not write IS, and 0x1 was never read set */
	h_is = 0x10400; CHK(sui_rd(&st, &ops, SUI_REG_IS, &v) == 0);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x10400) == 0);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x400) == 0);                    /* a subset of the bits read */
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x20000) == SUI_E_REFUSED);       /* a bit never read */
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x10401) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0xFFFFFFFFu) == SUI_E_REFUSED);
	h_is = 1; CHK(sui_rd(&st, &ops, SUI_REG_IS, &v) == 0); CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x1) == 0);   /* a read that saw UTRCS set allows writing it back (the ISR's case) */
	CHK(sui_wr(&st, &ops, SUI_REG_IS, 0x10401) == 0);
	/* IE write rules: 0 always; 0x1 only after the attach, before the enable, while IE is 0 (armed state: refused); never anything else */
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0x1) == SUI_E_REFUSED);           /* armed */
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0x70885) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0x470) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0x5) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0xFFFFFFFFu) == SUI_E_REFUSED);
	CHK(sui_wr(&st, &ops, SUI_REG_IE, 0) == 0);
	struct sui s2; reset(&s2, SUI_MODE_ON);
	CHK(sui_wr(&s2, &ops, SUI_REG_IE, 1) == SUI_E_REFUSED);             /* before the attach */
	s2.spi_att = 1;
	s2.spi_att = 1; CHK(sui_wr(&s2, &ops, SUI_REG_IE, 5) == SUI_E_REFUSED && sui_wr(&s2, &ops, SUI_REG_IE, 0x70885) == SUI_E_REFUSED && !s2.ie_on);
	CHK(sui_wr(&s2, &ops, SUI_REG_IE, 1) == 0 && s2.ie_on);
	CHK(sui_wr(&s2, &ops, SUI_REG_IE, 1) == SUI_E_REFUSED);             /* IE already on */
	CHK(sui_wr(&s2, &ops, SUI_REG_IE, 0) == 0 && !s2.ie_on);
	s2.armed = 1; CHK(sui_wr(&s2, &ops, SUI_REG_IE, 1) == SUI_E_REFUSED);
	/* modes 0 and RO: no write row, the read rows of RO only */
	struct sui s0; reset(&s0, SUI_MODE_0);
	for (size_t i = 0; i < sizeof rd_ok / sizeof rd_ok[0]; i++) CHK(sui_rd(&s0, &ops, rd_ok[i], &v) == SUI_E_REFUSED);
	CHK(sui_wr(&s0, &ops, SUI_REG_IE, 0) == SUI_E_REFUSED && sui_wr(&s0, &ops, SUI_REG_IS, 1) == SUI_E_REFUSED);
	struct sui sr; reset(&sr, SUI_MODE_RO);
	for (size_t i = 0; i < sizeof rd_ok / sizeof rd_ok[0]; i++) CHK(sui_rd(&sr, &ops, rd_ok[i], &v) == 0);
	CHK(sui_wr(&sr, &ops, SUI_REG_IS, 1) == SUI_E_REFUSED);
	/* a bus error is not a refusal */
	armed(&st); rd_fail_off = 0x20; CHK(sui_rd(&st, &ops, SUI_REG_IS, &v) == SUI_E_BUS);
}

static void t_disarm_quiesce(void) {
	struct sui st; armed(&st); h_is = 0x400;
	sui_disarm(&st, &ops, SUI_Y_RESTART);
	CHKS(tok, "W.24.0 R.20.400 W.20.400 X");
	CHK(!st.armed && !st.ie_on && !st.spi_att && h_ie == 0 && h_is == 0 && st.why == SUI_Y_RESTART);
	CHK(count_note(SUI_N_QUIESCE) == 1 && count_note(SUI_G_OFF) == 0);   /* a restart is not a failure: no GAP */
	CHKS(sui_mode_str(&st), "off:restart");
	tok[0] = 0; sui_disarm(&st, &ops, SUI_Y_RESTART);
	CHKS(tok, "");                                                  /* idempotent: no register access the second time */
	CHK(count_note(SUI_N_QUIESCE) == 1);
	/* every step is tried even when an earlier one fails */
	armed(&st); wr_fail_off = 0x24; h_is = 0;
	sui_disarm(&st, &ops, SUI_Y_STORM);
	CHK(count_note(SUI_G_TEARDOWN) == 1 && note_a[find_note(SUI_G_TEARDOWN)] == 1 && n_detach == 1 && strstr(tok, "R.20.0") != NULL && !st.armed);
	armed(&st); rd_fail_off = 0x20;
	sui_disarm(&st, &ops, SUI_Y_STORM);
	CHK(count_note(SUI_G_TEARDOWN) == 1 && note_a[find_note(SUI_G_TEARDOWN)] == 2 && n_detach == 1 && h_ie == 0);
	armed(&st); h_is = 8; wr_fail_off = 0x20;
	sui_disarm(&st, &ops, SUI_Y_STORM);
	CHK(count_note(SUI_G_TEARDOWN) == 1 && note_a[find_note(SUI_G_TEARDOWN)] == 2 && note_b[find_note(SUI_G_TEARDOWN)] == 1 && n_detach == 1 && h_ie == 0);
	/* the GAP of a storm / noirq off carries the reason and the irq count */
	armed(&st); st.irqs = 77; sui_disarm(&st, &ops, SUI_Y_NOIRQ);
	CHK(count_note(SUI_G_OFF) == 1 && note_a[find_note(SUI_G_OFF)] == SUI_Y_NOIRQ && note_b[find_note(SUI_G_OFF)] == 77);
	/* disarm of a state that never touched anything: no access, still reported once */
	reset(&st, SUI_MODE_ON); sui_disarm(&st, &ops, SUI_Y_ARM_BUSY);
	CHK(accesses == 0 && count_note(SUI_G_OFF) == 1);
}

static void t_full_flow(void) {
	/* arm, 20 commands with interrupts, storm, finish: the counters of the summary row */
	struct sui st; reset(&st, SUI_MODE_ON);
	CHK(sui_arm(&st, &ops) == 0);
	for (int i = 0; i < 20; i++) { scen(1, 0, 0); CHK(do_wait(&st) == SUI_W_DONE); sui_ack(&st, &ops); }
	CHK(st.irqs == 20 && st.cmds == 20 && st.hits == 20 && st.spurious == 0 && st.poll_found == 0 && h_is == 0);
	CHK(st.isr_total == 20);
	CHKS(sui_mode_str(&st), "irq");
}

int main(void) {
	t_mode_arg(); t_mode0_zero_access(); t_arm_order(); t_arm_failures(); t_isr_one_per_completion(); t_isr_bits(); t_isr_storm(); t_wait_basic(); t_wait_race_isr();
	t_wait_timeout_and_noirq(); t_wait_bus(); t_ack(); t_ack_isr_race(); t_isr_reread(); t_ie_off(); t_ro(); t_judge(); t_disarm_quiesce(); t_full_flow();
	if (bad) { printf("%d failures\n", bad); return 1; }
	printf("CORE OK\n");
	return 0;
}
'''


def irq_header(patch=PATCH):
    """the new-file hunk of bsd/dev/sweet_ufs_irq.h out of the patch text"""
    with open(patch) as f:
        t = f.read()
    i = t.index("diff --git a/bsd/dev/sweet_ufs_irq.h b/bsd/dev/sweet_ufs_irq.h")
    j = t.find("\ndiff --git ", i + 10)
    hunk = t[i:j if j > 0 else len(t)]
    body = hunk.split("\n+++ b/bsd/dev/sweet_ufs_irq.h\n", 1)[1]
    out = []
    for ln in body.splitlines()[1:]:      # skip the @@ line
        assert ln.startswith("+"), ln
        out.append(ln[1:])
    return "\n".join(out) + "\n"


def core_text(patch=PATCH):
    txt = irq_header(patch)
    m = re.search(r"^/\* SUI_CORE_BEGIN \*/\n(.*?)^/\* SUI_CORE_END \*/", txt, re.S | re.M)
    assert m, "no SUI_CORE block in the patch"
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


