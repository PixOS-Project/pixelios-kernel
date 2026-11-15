/* Host test for the PHASE 3 part of bsd/dev/sweet_chg_i2c.c (xnu patch 0287: sweetchgicl=900, the only chip write). Model copied from chg_i2c_host_test.c (phase 2) and extended
 * with write messages and a chip write log. Phase 2 base text follows:
 * Host test for bsd/dev/sweet_chg_i2c.c (xnu patch 0284, charger phase 2). Compiled by tools/test_chg_i2c.py with -DSWEET_CHG_I2C_HOST -DCHG_I2C_SRC="<extracted>".
 * Prints `CASE <name> ok` or `CASE <name> FAIL <why>`. A fake HSI2C12 controller models the auto-mode transfer protocol of docs/research/charger-max77759-gs101.md
 * section 5.4 and CHECKS the sequence it is driven with; the chips are a byte array per slave that nothing can write (a second TX byte or a TX byte that is not a
 * register address of the read list is a violation). Offsets below are an INDEPENDENT copy of note 5.1. */
#include "chg_i2c_host_env.h"
#include CHG_I2C_SRC

#define OFF_CTL 0x00u
#define OFF_FIFOCTL 0x04u
#define OFF_INTEN 0x20u
#define OFF_INTST 0x24u
#define OFF_FIFOST 0x30u
#define OFF_TXD 0x34u
#define OFF_RXD 0x38u
#define OFF_CONF 0x40u
#define OFF_AUTO 0x44u
#define OFF_TOUT 0x48u
#define OFF_TRANS 0x50u
#define OFF_ADDR 0x70u

static uint32_t hw[0x1000 / 4];
static struct { uint32_t off, val; } wlog[8192];
static int nw, nrd_rx;
static uint8_t chip[256][256];          /* [slave][reg]; slave present flags below */
static int present[256];
static uint8_t rxq[64]; static int rxn, rxh;
static uint8_t txlog[512]; static int ntx;
static uint8_t wtx[4]; static int tx_expect, tx_got, cur_stop;
static struct { int slave, reg, val; } cwlog[16]; static int ncw;
static int wr_ignore, wr_garble, wr_nack, chg_gate_fail_after = 1000, usb_speed = 4, icl_arg = 900;
static int hang_slave = -1, nack_slave = -1, cur_slave, cur_rw, cur_len, run_active;
static uint8_t cur_reg;
static int viol;
static char viol_why[256];
static uint64_t fk_us, fk_ms;
static int arg_on = 1, load_fail, gate_ok = 1, gate_calls;
static uint32_t fk_window = 0x1000;
static uint32_t fifost_override;
static int txbusy_cfg, tx_busy_reads, busy_cfg, pending_busy, busy_forever, short_slave = -1, err2_slave = -1;
static uint32_t nrd_total, fk_ov[16];
static int fk_has_ov[16];
static uint16_t fgw[256];

#ifdef CHG_MON_REFUSED
/* patch 0336 externals (bsd/dev/sweet_chg.c, bsd/dev/sweet_thermal.c): the stack under test has 0336 below 0339; the thermal sampler is off in this model */
int sweet_thermal_enabled(void) { return 0; }
int sweet_chg_enter(void) { return 1; }
void sweet_chg_leave(void) { }
int sweet_chg_gate_established(void) { return 1; }
#endif
int sweet_chg_gate_still_open(void) { gate_calls++; return gate_ok && gate_calls <= chg_gate_fail_after; }
static uint64_t usb_off_ms = 1ull << 60, bc_change_ms = 1ull << 60;
static int bc_new, stop_at3 = 1;
uint32_t sweet_usb_ss_configured(void) { return fk_ms >= usb_off_ms ? 0u : (uint32_t)usb_speed; }
static int i2c_icl_arg(uint32_t *v) { *v = (uint32_t)icl_arg; return icl_arg != 0; }
#ifdef CHG_PMIC
static uint32_t fast_arg_ms, fast_arg_n = 600;   /* patch 0314: sweetfg / sweetfgn */
static int i2c_fast_arg(uint32_t *ms, uint32_t *secs) { *ms = fast_arg_ms; *secs = fast_arg_n; return fast_arg_ms != 0; }
#endif
static int i2c_arg_on(void) { return arg_on; }
static uint64_t i2c_now_us(void) { return fk_us += 1000; }
static uint64_t i2c_now_ms(void) { return fk_ms; }

static void
bad(const char *w)
{
	if (!viol) {
		snprintf(viol_why, sizeof(viol_why), "%s", w);
	}
	viol++;
}

static uint32_t
i2c_hw_rd32(uint32_t off)
{
	uint32_t v = hw[off / 4];

	if (++nrd_total > 30000) {
		printf("CASE runaway FAIL a poll loop did not end (no timeout)\n");
		exit(1);
	}
	if (off == OFF_TRANS) {
		if (busy_forever || pending_busy > 0) {
			v |= 1u << 17;
			if (pending_busy > 0) { pending_busy--; }
		}
	} else if (off == OFF_FIFOST && fifost_override) {
		v = fifost_override;
	} else if (off == OFF_FIFOST) {
		v = (hw[off / 4] & ~(1u << 24)) | ((rxn - rxh) == 0 ? (1u << 24) : 0) | (1u << 8);
		if (tx_busy_reads > 0) { v &= ~(1u << 8); tx_busy_reads--; }
	} else if (off == OFF_RXD) {
		if (rxn - rxh <= 0) {
			bad("RX_DATA read with an empty FIFO");
			return 0;
		}
		nrd_rx++;
		v = rxq[rxh++];
	} else if (off == 0xc4 || off == 0x60 || off == 0x64 || off == 0x68 || off == 0x08) {
		bad("read of a register the transfer code must not need (timing/USI)");
	}
	return v;
}

static void
i2c_hw_wr32(uint32_t off, uint32_t v)
{
	if (nw < 8192) {
		wlog[nw].off = off;
		wlog[nw].val = v;
		nw++;
	}
	if (off == OFF_CONF || off == 0x08 || off == 0x60 || off == 0x64 || off == 0x68 || off == 0xc4 || off == 0xc8 || off >= 0xc0) {
		bad("write to CONF/TRAILIG/TIMING/USI register");
	}
	if (off == OFF_CTL && (v & (1u << 31))) {
		bad("CTL.SW_RST written");
	}
	if (off == OFF_INTST) {
		hw[off / 4] &= ~v;      /* write-1-to-clear */
		return;
	}
	hw[off / 4] = v;
	if (off == OFF_TXD) {
		if (ntx < 512) {
			txlog[ntx++] = (uint8_t)v;
		}
		if (!run_active || cur_rw) {
			bad("TX_DATA written outside a write message");
			return;
		}
		if (tx_got < 4) { wtx[tx_got] = (uint8_t)v; }
		tx_got++;
		if (tx_got < tx_expect) {
			return;                         /* more bytes to come */
		}
		if (tx_expect == 1) {
			cur_reg = wtx[0];
			tx_busy_reads = txbusy_cfg;
		} else if (tx_expect == 2 && cur_stop) {
			/* a complete chip register write: [reg, value] with STOP */
			if (ncw < 16) { cwlog[ncw].slave = cur_slave; cwlog[ncw].reg = wtx[0]; cwlog[ncw].val = wtx[1]; ncw++; }
			if (!wr_ignore && !wr_nack && present[cur_slave]) {
				chip[cur_slave][wtx[0]] = wr_garble ? (uint8_t)(wtx[1] | 0x04) : wtx[1];
			}
			pending_busy = busy_cfg;
		} else {
			bad("write message of an unexpected shape");
		}
		hw[OFF_INTST / 4] |= (nack_slave == cur_slave || !present[cur_slave] || (tx_expect == 2 && wr_nack)) ? (1u << 9) : (hang_slave == cur_slave ? 0u : (1u << 7));
		run_active = 0;
	} else if (off == OFF_AUTO && (v & (1u << 31))) {
		uint32_t ctl = hw[OFF_CTL / 4], len = v & 0xffff, rw = (v >> 16) & 1;

		cur_slave = (hw[OFF_ADDR / 4] >> 10) & 0x7f;
		cur_rw = (int)rw;
		cur_len = (int)len;
		if (pending_busy > 0 || busy_forever) { bad("message started while MASTER_BUSY"); }
		if (tx_busy_reads > 0) { bad("message started with a TX byte still in the FIFO"); }
		if (!(ctl & (1u << 3))) { bad("CTL.MASTER lost"); }
		if (((ctl & (1u << 6)) != 0) != (rw != 0) || ((ctl & (1u << 7)) != 0) == (rw != 0)) { bad("CTL RXCHON/TXCHON does not match the message direction"); }
		if (rw && !(v & (1u << 17))) { bad("read message without STOP_AFTER_TRANS"); }
		if (!rw && len == 1 && (v & (1u << 17))) { bad("register-address message with STOP"); }
		if (!rw && len == 2 && !(v & (1u << 17))) { bad("chip register write without STOP"); }
		if (!rw && len != 1 && len != 2) { bad("write message length"); }
		cur_stop = (v >> 17) & 1;
		if ((hw[OFF_FIFOCTL / 4] & 3u) != 3u) { bad("FIFOs not enabled"); }
		if (hw[OFF_TOUT / 4] & (1u << 31)) { bad("TIMEOUT_EN still set"); }
		if ((hw[OFF_ADDR / 4] >> 24) != 7u) { bad("ADDR master id not 7"); }
		if (hw[OFF_INTEN / 4] != (0x80u | 0xf00u)) { bad("INT_ENABLE not DONE|bits 8-11"); }
		if (rw) {
			int i;

			rxn = rxh = 0;
			pending_busy = busy_cfg;
			if (err2_slave == cur_slave) {
				hw[OFF_INTST / 4] |= 1u << 11;
			} else if (!present[cur_slave]) {
				hw[OFF_INTST / 4] |= 1u << 9;
			} else if (hang_slave == cur_slave) {
				/* never completes */
			} else {
				for (i = 0; i < cur_len - (short_slave == cur_slave ? 1 : 0); i++) {
					rxq[rxn++] = cur_slave == 0x36 ? (uint8_t)(fgw[cur_reg] >> (8 * (i & 1))) : chip[cur_slave][(cur_reg + i) & 0xff];
				}
				hw[OFF_INTST / 4] |= 1u << 7;
			}
		} else {
			run_active = 1;
			tx_expect = cur_len;
			tx_got = 0;
		}
	}
}

static int
i2c_load(void)
{
	if (load_fail) {
		i2c_printf("SWEET CHG GAP: P2 /sweet-chg hsi2c12-reg missing (host fake)\n");
		return -1;
	}
	i2c_off[I_CTL] = OFF_CTL; i2c_off[I_INTST] = OFF_INTST; i2c_off[I_FIFOST] = OFF_FIFOST; i2c_off[I_CONF] = OFF_CONF; i2c_off[I_TRANS] = OFF_TRANS;
	i2c_off[I_FIFOCTL] = OFF_FIFOCTL; i2c_off[I_INTEN] = OFF_INTEN; i2c_off[I_TXD] = OFF_TXD; i2c_off[I_RXD] = OFF_RXD; i2c_off[I_AUTO] = OFF_AUTO;
	i2c_off[I_TOUT] = OFF_TOUT; i2c_off[I_ADDR] = OFF_ADDR;
	{
		int r;

		for (r = 0; r < I_N; r++) {
			if (fk_has_ov[r]) { i2c_off[r] = fk_ov[r]; }
		}
	}
	i2c_size = fk_window;
	return i2c_offsets_ok() ? 0 : -1;
}

static void
reset(void)
{
	memset(hw, 0, sizeof(hw));
	hw[OFF_CTL / 4] = 0x48;                 /* [obs chg-p1] */
	hw[OFF_CONF / 4] = 0x980110ffu;
	hw[0x60 / 4] = 0x1f0ff00; hw[0x64 / 4] = 0x30003e0; hw[0x68 / 4] = 0x3e0000;
	hw[OFF_FIFOST / 4] = 0x1000100;
	hw[OFF_TRANS / 4] = 0x80001;
	hw[OFF_TOUT / 4] = 0x80000005u;         /* TIMEOUT_EN set + junk low bits: must come back as 5 */
	hw[OFF_ADDR / 4] = 0x00fff3ffu;         /* junk: [19:0] cleared, [31:24] := 7, [23:20] kept */
	memset(chip, 0, sizeof(chip));
	memset(present, 0, sizeof(present));
	present[0x66] = present[0x25] = present[0x36] = present[0x69] = 1;
	chip[0x66][0x00] = 0x3b; chip[0x66][0x01] = 0x12; chip[0x66][0x60] = 0x59;
	chip[0x25][0x00] = 0xd1; chip[0x25][0x01] = 0x18; chip[0x25][0x02] = 0x0b; chip[0x25][0x03] = 0x00; chip[0x25][0x04] = 0x02; chip[0x25][0x05] = 0x00; chip[0x25][0x87] = 0x01;
	chip[0x69][0xb9] = 0x05; chip[0x69][0xbb] = 0x07; chip[0x69][0xbd] = 0x14; chip[0x69][0xc2] = 0x93; chip[0x69][0xbf] = 0x0c; chip[0x69][0xc5] = 0x6a; chip[0x69][0xcb] = 0xc0;
	chip[0x69][0xb4] = 0x9f; chip[0x69][0xb6] = 0xf1; chip[0x69][0xb7] = 0x20;
	nw = nrd_rx = ntx = rxn = rxh = viol = run_active = 0;
	ncw = 0; tx_expect = tx_got = cur_stop = 0; wr_ignore = wr_garble = wr_nack = 0; chg_gate_fail_after = 1000; usb_speed = 4; icl_arg = 900;
	usb_off_ms = bc_change_ms = 1ull << 60; stop_at3 = 1; p3_mode = 0; i2c_p2_ok = 0; p3_stage = 0; p3_nwrites = 0; p3_orig_valid = 0; p3_orig = p3_intended = 0; p3_samples = 0; p3_t0_ms = p3_next_ms = p3_w0_ms = 0;
	viol_why[0] = 0;
	hang_slave = nack_slave = -1;
	txbusy_cfg = tx_busy_reads = busy_cfg = pending_busy = busy_forever = 0;
	short_slave = err2_slave = -1;
	nrd_total = 0;
	memset(fk_has_ov, 0, sizeof(fk_has_ov));
	fifost_override = 0;
	memset(fgw, 0, sizeof(fgw));
	fgw[0x21] = 0x6200; fgw[0x09] = 0x3000; fgw[0x0a] = 0x0010; fgw[0x06] = 0x2800;
	fk_log[0] = 0;
	fk_us = fk_ms = 0;
#ifdef CHG_PMIC
	fast_arg_ms = 0; fast_arg_n = 600; fast_stage = 0; fast_n = 0; fast_vmin = 0; fast_imax = 0;
#endif
	arg_on = 1; load_fail = 0; gate_ok = 1; gate_calls = 0; fk_window = 0x1000;
	memset(i2c_off, 0, sizeof(i2c_off));
	i2c_size = 0;
	i2c_xfers = i2c_errors = i2c_nwr = 0;
	i2c_dead = 0;
	i2c_group = 0;
	i2c_t_ms = 0;
	memset(i2c_pmic, 0, sizeof(i2c_pmic)); memset(i2c_tcpc, 0, sizeof(i2c_tcpc)); memset(i2c_fgb, 0, sizeof(i2c_fgb)); memset(i2c_chg, 0, sizeof(i2c_chg));
}

static void run_all(int pending_first) { int i; for (i = 0; i < 20; i++) { sweet_chg_i2c_tick(i == 0 ? pending_first : 0); } }

static int fails;
#define CASE(name, cond, why) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL %s\n", name, why); fails++; } } while (0)
#define HAS(s) (strstr(fk_log, (s)) != NULL)

static int
wr_offsets_allowed(void)
{
	static const uint32_t ok[] = { 0x00, 0x04, 0x20, 0x24, 0x34, 0x44, 0x48, 0x70 };
	int i, j;

	for (i = 0; i < nw; i++) {
		int f = 0;

		for (j = 0; j < 8; j++) {
			f |= wlog[i].off == ok[j];
		}
		if (!f) {
			return 0;
		}
	}
	return 1;
}

static void
run_p3(int maxticks)
{
	int t;

	for (t = 0; t < maxticks && p3_stage != 9 && !(stop_at3 && p3_stage == 3); t++) {
		fk_ms += 1000;
		if (fk_ms >= bc_change_ms) { chip[0x25][0x87] = (uint8_t)bc_new; }
		sweet_chg_i2c_tick(0);
	}
}

static int
only_cnfg09_chip_writes(void)
{
	int i;

	for (i = 0; i < ncw; i++) {
		if (cwlog[i].slave != 0x69 || cwlog[i].reg != 0xc2) {
			return 0;
		}
	}
	return 1;
}

static int
rows_ok(void)
{
	const char *l = fk_log;
	int longest = 0;

	while (*l) {
		const char *e = strchr(l, '\n');

		if (e == NULL) { return 0; }
		if ((int)(e - l) > longest) { longest = (int)(e - l); }
		l = e + 1;
	}
	return longest <= 200;
}

static int
count(const char *needle)
{
	int n = 0;
	const char *p = fk_log;

	while ((p = strstr(p, needle)) != NULL) { n++; p += strlen(needle); }
	return n;
}

int
main(void)
{
	int i;

	(void)run_all;

	/* 1. the happy path: exactly one chip write, [0x69 <- 0xc2, 0xa2], read back, 10 samples */
	reset();
	run_p3(600);
	CASE("p3_one_chip_write_exact", ncw == 1 && cwlog[0].slave == 0x69 && cwlog[0].reg == 0xc2 && cwlog[0].val == 0xa2 && chip[0x69][0xc2] == 0xa2, "chip write list");
	{
		int pos = -1, k;

		for (k = 0; k + 1 < ntx; k++) { if (txlog[k] == 0xc2 && txlog[k + 1] == 0xa2) { pos = k; } }
		CASE("p3_tx_bytes_c2_a3_once", pos >= 0 && count("wrote=0xa2") == 1, "TX byte pair");
	}
	CASE("p3_preflight_row", HAS("SWEET CHG P3: preflight usb_speed=4 BC_STATUS1=0x1 CHGTYP=1 CNFG_00=0x5 MODE=0x5 CNFG_06=0xc CHGPROT=3 CNFG_09=0x93 ILIM=500 mA"), fk_log);
	CASE("p3_before_after_row", HAS("SWEET CHG P3: CNFG_09 before=0x93 (500 mA) wrote=0xa2 read back=0xa2 (875 mA)"), fk_log);
	CASE("p3_baseline_row", HAS("SWEET CHG P3: t=-0s current=") && HAS("(before the write)"), fk_log);
	CASE("p3_ten_samples", count("SWEET CHG P3: t=") == 11 && HAS("SWEET CHG P3: t=30s ") && HAS("SWEET CHG P3: t=300s ") && HAS("ilim=875 mA") && HAS("samples done (chip_writes=1 samples=10") && HAS("host suspend is not detected"), fk_log);
	CASE("p3_hsi2c_stores_allowlisted", wr_offsets_allowed() && viol == 0 && only_cnfg09_chip_writes(), viol_why);
	CASE("p3_write_message_registers", ({ int a = 0, b = 0, k; for (k = 0; k < nw; k++) { a += wlog[k].off == OFF_AUTO && wlog[k].val == 0x20002u; b += wlog[k].off == OFF_AUTO && wlog[k].val == 0x80020002u; } a == 1 && b == 1; }), "AUTO_CONF STOP|len2 once");
	CASE("p3_int_enable_disarmed", hw[OFF_INTEN / 4] == 0, "INT_ENABLE left armed");
	CASE("p3_rows_short", rows_ok(), "row over 200 chars or without newline");
	CASE("p3_no_conf_timing_store", viol == 0, viol_why);

	/* review F4: NO_AUTOIBUS = 0 is refused (Android always programs 1 with a limit) */
	reset();
	chip[0x69][0xc2] = 0x13;
	run_p3(600);
	CASE("p3_refuses_no_autoibus_clear", ncw == 0 && HAS("NO_AUTOIBUS is 0") && chip[0x69][0xc2] == 0x13, fk_log);
	reset();
	chip[0x69][0xc2] = 0x80 | 5;
	run_p3(600);
	CASE("p3_other_old_ilim_ok", ncw == 1 && cwlog[0].val == 0xa2, "write value");

	/* 2. refusals: no chip write, a GAP says why */
	{
		struct { const char *n; int what; const char *gap; } r[] = {
			{ "refuse_usb_not_ss", 1, "not a configured SuperSpeed link" }, { "refuse_chgtyp_none", 2, "CHGTYP is not SDP (1) or CDP (2)" },
			{ "refuse_chgtyp_dcp", 3, "CHGTYP is not SDP (1) or CDP (2)" }, { "refuse_mode_off", 4, "MODE is not 4 or 5" }, { "refuse_mode_boost", 5, "MODE is not 4 or 5" },
			{ "refuse_ilim_suspend", 6, "below 4" }, { "refuse_ilim_100", 7, "below 4" }, { "refuse_ilim_already", 8, "already 875 mA or more" },
			{ "refuse_ilim_higher", 9, "already 875 mA or more" }, { "refuse_gate_after_p2", 10, "phase-1 gate" }, { "refuse_arg_other", 11, "not supported (only 900 or 500)" },
			{ "refuse_p2_off", 12, "phase 2 did not finish" }, { "refuse_p2_nack", 13, "phase 2 did not finish" }, { "refuse_pmic_id", 14, "phase 2 did not finish" },
			{ "refuse_fg_id", 15, "phase 2 did not finish" }, { "refuse_usb_speed_unknown", 16, "not a configured SuperSpeed link" },
		};
		for (i = 0; i < 16; i++) {
			reset();
			switch (r[i].what) {
			case 1: usb_speed = 0; break;
			case 2: chip[0x25][0x87] = 0x00; break;
			case 3: chip[0x25][0x87] = 0x03; break;
			case 4: chip[0x69][0xb9] = 0x00; break;
			case 5: chip[0x69][0xb9] = 0x08 | 0x05; break;
			case 6: chip[0x69][0xc2] = 0x80; break;
			case 7: chip[0x69][0xc2] = 0x93 & 0x83; break;
			case 8: chip[0x69][0xc2] = 0x80 | 0x22; break;
			case 9: chip[0x69][0xc2] = 0x80 | 0x40; break;
			case 10: chg_gate_fail_after = 5; break;
			case 11: icl_arg = 1500; break;
			case 12: arg_on = 0; break;
			case 13: nack_slave = 0x25; break;
			case 14: chip[0x66][0x00] = 0x3a; break;
			case 15: fgw[0x21] = 0x6400; break;
			case 16: usb_speed = 0; break;
			}
			if (r[i].what == 7) { chip[0x69][0xc2] = 0x83; }
			{ uint8_t c2 = chip[0x69][0xc2];
			run_p3(600);
			CASE(r[i].n, ncw == 0 && HAS(r[i].gap) && chip[0x69][0xc2] == c2 && only_cnfg09_chip_writes() && count("SWEET CHG P3: t=3") == 0, fk_log); }
		}
	}
	reset();
	icl_arg = 0;
	run_p3(600);
	CASE("p3_default_off", ncw == 0 && HAS("SWEET CHG P3: off (default; sweetchgicl=900") && count("SWEET CHG P3: t=") == 0, fk_log);

	/* 3. read-back mismatch: restore once, GAP, stop */
	reset();
	wr_ignore = 1;
	run_p3(600);
	CASE("mismatch_ignored_write_restores_once", ncw == 2 && cwlog[0].val == 0xa2 && cwlog[1].val == 0x93 && chip[0x69][0xc2] == 0x93 &&
	    HAS("read-back 0x93 != written 0xa2: restore of 0x93 sent once") && HAS("done (mismatch)") && count("SWEET CHG P3: t=30s") == 0, fk_log);
	reset();
	wr_garble = 1;
	run_p3(600);
	CASE("mismatch_garbled_restored", ncw == 2 && cwlog[1].val == 0x93 && HAS("read back after restore 0x") && i2c_dead && only_cnfg09_chip_writes(), fk_log);
	reset();
	wr_nack = 1;
	run_p3(600);
	CASE("write_nack_no_restore", ncw == 1 && HAS("CNFG_09 write failed (CNFG_09 state unknown, bus stopped, no restore possible)") && i2c_dead, fk_log);

	/* 4. gate lost mid-sampling: sampling stops, no further chip write */
	reset();
	chg_gate_fail_after = 9;
	run_p3(600);
	CASE("sampling_stops_when_gate_lost", ncw == 1 && HAS("done (gate)") && count("SWEET CHG P3: t=") < 11, fk_log);

	/* 5. direct guard tests of the single chip-write function */
	{
		struct { const char *n; int mode; uint8_t cur, val; } g[] = {
			{ "guard_other_value", 900, 0x93, 0xa3 }, { "guard_zero", 900, 0x93, 0x00 }, { "guard_max", 900, 0x93, 0xff }, { "guard_value_flips_autoibus", 900, 0x93, 0x22 },
			{ "guard_old_875_value", 900, 0x93, 0xa3 }, { "guard_cur_ilim_3", 900, 0x83, 0xa2 }, { "guard_cur_ilim_0", 900, 0x80, 0xa2 }, { "guard_cur_already_875", 900, 0xa2, 0xa2 },
			{ "guard_cur_above", 900, 0xc0, 0xa2 }, { "guard_cur_no_autoibus", 900, 0x13, 0x22 }, { "guard_mode_0", 0, 0x93, 0xa2 }, { "guard_mode_other", 700, 0x93, 0xa2 },
			{ "guard_down_in_mode_900", 900, 0xa2, 0x93 }, { "guard_down_wrong_cur", 500, 0x93, 0x93 }, { "guard_down_wrong_value", 500, 0xa2, 0x92 }, { "guard_down_bit7_flip", 500, 0xa2, 0x13 },
			{ "guard_down_up", 500, 0x93, 0xa2 },
		};
		for (i = 0; i < 17; i++) {
			reset();
			i2c_load();
			p3_mode = g[i].mode;
			CASE(g[i].n, i2c_cnfg09_write(g[i].cur, g[i].val) != 0 && ncw == 0 && nw == 0 && ntx == 0, "chip write not refused");
		}
		reset();
		i2c_load();
		p3_mode = 900;
		CASE("guard_restore_without_first", i2c_cnfg09_write(0xa2, 0x93) != 0 && ncw == 0, "restore without a first write");
		reset();
		i2c_load();
		p3_mode = 900;
		CASE("guard_first_ok_then_second_first_refused", i2c_cnfg09_write(0x93, 0xa2) == 0 && ncw == 1 && i2c_cnfg09_write(0x93, 0xa2) != 0 && ncw == 1, "second first-write");
		reset();
		i2c_load();
		p3_mode = 900;
		(void)i2c_cnfg09_write(0x93, 0xa2);
		CASE("guard_restore_value_only", i2c_cnfg09_write(0xa2, 0x13) != 0 && ncw == 1 && i2c_cnfg09_write(0xa2, 0x93) == 0 && ncw == 2 && i2c_cnfg09_write(0xa2, 0x93) != 0 && ncw == 2, "restore rules");
		reset();
		i2c_load();
		p3_mode = 500;
		CASE("guard_down_ok_once", i2c_cnfg09_write(0xa2, 0x93) == 0 && ncw == 1 && cwlog[0].val == 0x93 && i2c_cnfg09_write(0xa2, 0x93) != 0 && ncw == 1, "down rules");
		reset();
		i2c_load();
		p3_mode = 900;
		i2c_dead = 1;
		CASE("guard_dead_no_write", i2c_cnfg09_write(0x93, 0xa2) != 0 && ncw == 0 && ntx == 0, "write after dead");
	}

	/* 6. review F2: the link or the source goes away later in the boot -> the original value is put back once */
	{
		struct { const char *n; uint64_t usb_ms, bc_ms; int bc; const char *gap; } l[] = {
			{ "lost_usb_during_samples", 100000, 1ull << 60, 0, "USB SuperSpeed link / 896 mA configuration lost" },
			{ "lost_usb_after_samples_watcher", 400000, 1ull << 60, 0, "USB SuperSpeed link / 896 mA configuration lost" },
			{ "lost_chgtyp_during_samples", 1ull << 60, 100000, 0, "TCPC CHGTYP (source no longer SDP/CDP) lost" },
			{ "lost_chgtyp_dcp_watcher", 1ull << 60, 400000, 3, "TCPC CHGTYP (source no longer SDP/CDP) lost" },
		};
		for (i = 0; i < 4; i++) {
			reset();
			stop_at3 = 0;
			usb_off_ms = l[i].usb_ms; bc_change_ms = l[i].bc_ms; bc_new = l[i].bc;
			run_p3(1500);
			CASE(l[i].n, ncw == 2 && cwlog[0].val == 0xa2 && cwlog[1].val == 0x93 && chip[0x69][0xc2] == 0x93 && HAS(l[i].gap) && HAS("done (link lost, restored)") && p3_stage == 9 &&
			    hw[OFF_INTEN / 4] == 0 && only_cnfg09_chip_writes() && ((i == 0 || i == 2) ? p3_samples < 10 : p3_samples == 10), fk_log);
		}
		reset();
		stop_at3 = 0;
		usb_off_ms = 100000;
		run_p3(1500);
		{ int w = ncw; run_p3(1000); CASE("lost_restore_only_once", ncw == w && w == 2, "second restore"); }
		reset();
		stop_at3 = 0;
		usb_off_ms = 100000;
		run_p3(95);
		chip[0x69][0xc2] = 0x91;                /* someone else changed CNFG_09 before the loss */
		run_p3(1500);
		CASE("lost_not_ours_not_touched", ncw == 1 && HAS("is 0x91, not the 0xa2 we wrote: not touched"), fk_log);
		reset();
		stop_at3 = 0;
		usb_off_ms = 90000;
		chg_gate_fail_after = 8;
		run_p3(1500);
		CASE("lost_gate_gone_no_restore", ncw == 1 && HAS("gate lost too, CNFG_09 stays"), fk_log);
		reset();
		stop_at3 = 0;
		run_p3(2000);
		CASE("watcher_quiet_while_link_ok", ncw == 1 && p3_stage == 3 && !HAS("lost"), fk_log);
	}

	/* 7. review F3: sweetchgicl=500 puts 0x22 back to 0x13 (bit 7 kept), guarded and read back */
	reset();
	icl_arg = 500; usb_speed = 0; chip[0x69][0xc2] = 0xa2;
	run_p3(600);
	CASE("down_500_writes_once", ncw == 1 && cwlog[0].val == 0x93 && chip[0x69][0xc2] == 0x93 && HAS("CNFG_09 before=0xa2 (875 mA) wrote=0x93 read back=0x93 (500 mA)") && HAS("done (put back to 500 mA)"), fk_log);
	reset();
	icl_arg = 500; chip[0x69][0xc2] = 0x22;
	run_p3(600);
	CASE("down_500_keeps_bit7_clear", ncw == 1 && cwlog[0].val == 0x13, fk_log);
	reset();
	icl_arg = 500;
	run_p3(600);
	CASE("down_500_nothing_to_restore", ncw == 0 && HAS("is not the 875 mA step: nothing to put back"), fk_log);
	reset();
	icl_arg = 500; chip[0x69][0xc2] = 0xa2; wr_ignore = 1;
	run_p3(600);
	CASE("down_500_mismatch_no_second_write", ncw == 1 && HAS("read-back 0x") && HAS("nothing more is written") && i2c_dead, fk_log);
	reset();
	icl_arg = 500; chip[0x69][0xc2] = 0xa2; arg_on = 0;
	run_p3(600);
	CASE("down_500_needs_phase2", ncw == 0 && HAS("phase 2 did not finish"), fk_log);
	reset();
	icl_arg = 500; chip[0x69][0xc2] = 0xa2; chg_gate_fail_after = 5;
	run_p3(600);
	CASE("down_500_needs_gate", ncw == 0 && HAS("phase-1 gate"), fk_log);

	/* 8. review F3: phase 2 flags an ILIM above 500 mA at boot */
	reset();
	icl_arg = 0; chip[0x69][0xc2] = 0xa2;
	run_all(0);
	CASE("p2_gap_ilim_above_500", HAS("SWEET CHG GAP: P2 CNFG_09 ILIM code 0x22 (875 mA) is above the 500 mA of a stock boot") && ncw == 0, fk_log);
	reset();
	icl_arg = 0;
	run_all(0);
	CASE("p2_no_gap_at_500", !HAS("above the 500 mA"), fk_log);

#ifdef CHG_MON
	/* 9. patch 0298 (tools/test_chg_mon.py): one battery row per 30 s watcher tick for the rest of the boot, reads only, one GAP on a failed read (the bus stops, phase 3 ends) */
	reset();
	stop_at3 = 0;
	run_p3(330);
	CASE("mon_none_during_samples", count("SWEET CHG MON: t=") <= 1 && p3_stage == 3, fk_log);
	run_p3(1800);
	CASE("mon_rows_every_30s", count("SWEET CHG MON: t=") >= 58 && HAS("SWEET CHG MON: t=330s current=5000 uA vcell=960 mV") && HAS("SWEET CHG MON: t=2100s "), fk_log);
	CASE("mon_reads_only", ncw == 1 && only_cnfg09_chip_writes() && p3_stage == 3, fk_log);
	reset();
	stop_at3 = 0;
	run_p3(400);
	fgw[0x0a] = 0xfe00;
	run_p3(31);
	CASE("mon_negative_current", HAS("current=-160000 uA"), fk_log);
	reset();
	stop_at3 = 0;
	run_p3(400);
	{
		int before = count("SWEET CHG MON: t=");

		nack_slave = 0x36;
		run_p3(200);
		CASE("mon_gap_once_on_read_fail", count("SWEET CHG GAP: MON fuel-gauge read failed") == 1 && count("SWEET CHG MON: t=") == before && ncw == 1 && p3_stage == 9, fk_log);
	}
#endif
#ifdef CHG_MON_REFUSED
	/* 9b. patch 0339 (tools/test_chg_mon_refused.py): a refused P3 with a live bus enters stage 10 = MON rows only, every 30 s, reads only; a lost gate or a failed read ends them with a GAP */
	{
		uint64_t t0;
		int rows;

		reset();
		chip[0x69][0xc2] = 0x80 | 0x22;         /* the chip kept the previous boot's 875 mA step: P3 refuses */
		fgw[0x08] = 0x1900;                     /* 25.0 C */
		for (i = 0; i < 600 && p3_stage != 10; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
		t0 = p3_t0_ms;
		CASE("mr_refused_enters_stage10", p3_stage == 10 && HAS("SWEET CHG GAP: P3 refused, CNFG_09 not written: CNFG_09 ILIM is already 875 mA or more") &&
		    HAS("SWEET CHG MON: P3 refused, battery rows go on every 30 s (patch 0339, reads only, no CNFG_09 write)\n") && count("SWEET CHG MON: P3 refused") == 1 && count("SWEET CHG MON: t=") == 0, fk_log);
		while (fk_ms < t0 + 125000u) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
		CASE("mr_rows_0_30_60_90_120", count("SWEET CHG MON: t=") == 5 && HAS("SWEET CHG MON: t=0s current=5000 uA vcell=960 mV soc=40.00 % temp=25.0 C\n") && HAS("SWEET CHG MON: t=30s ") &&
		    HAS("SWEET CHG MON: t=60s ") && HAS("SWEET CHG MON: t=90s ") && HAS("SWEET CHG MON: t=120s ") && !HAS("SWEET CHG MON: t=1s ") && p3_stage == 10, fk_log);
		CASE("mr_first_row_next_tick", ({ reset(); chip[0x69][0xc2] = 0xa2; for (i = 0; i < 600 && p3_stage != 10; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); } fk_ms += 1000; sweet_chg_i2c_tick(0);
		    count("SWEET CHG MON: t=0s ") == 1 && count("SWEET CHG MON: t=") == 1; }), fk_log);
		/* no chip write at all, no HSI2C store outside the per-transfer registers (zero CNFG_09 writes: ncw counts every complete register write message) */
		reset();
		chip[0x69][0xc2] = 0xa2;
		for (i = 0; i < 700; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
		rows = count("SWEET CHG MON: t=");
		CASE("mr_zero_chip_writes", ncw == 0 && p3_nwrites == 0 && chip[0x69][0xc2] == 0xa2 && p3_stage == 10 && rows >= 22 && viol == 0 && wr_offsets_allowed() && count("SWEET CHG P3: CNFG_09") == 0, viol_why);
		/* the restore / other-value pairs stay refused (p3_nwrites == 0). NOT asserted: the guard function itself has no p3_stage test, so the pair (0x93 -> 0xa2) would pass it; stage 10 has no caller of it (mr_zero_chip_writes) */
		CASE("mr_guard_refuses_restore_and_other", i2c_cnfg09_write(0xa2, 0x93) != 0 && i2c_cnfg09_write(0x93, 0xa3) != 0 && i2c_cnfg09_write(0xa2, 0xa2) != 0 && ncw == 0, "chip write allowed after a refused P3");
		CASE("mr_rows_short", rows_ok(), "row over 200 chars");
	}
	/* phase-1 gate lost during stage 10: exactly one GAP, no more rows, no write */
	reset();
	chip[0x69][0xc2] = 0xa2;
	for (i = 0; i < 70; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	{
		int before = count("SWEET CHG MON: t=");

		chg_gate_fail_after = gate_calls;
		for (i = 0; i < 300; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
		CASE("mr_gate_lost_one_gap", before >= 2 && count("SWEET CHG MON: t=") == before && count("SWEET CHG GAP: MON rows after a refused P3 stop (phase-1 gate lost)") == 1 &&
		    count("MON rows after a refused P3 stop") == 1 && p3_stage == 9 && ncw == 0 && hw[OFF_INTEN / 4] == 0, fk_log);
	}
	/* a failed fuel-gauge read: the 0298 MON read GAP once, no more rows, the bus stops */
	reset();
	chip[0x69][0xc2] = 0xa2;
	for (i = 0; i < 70; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	{
		int before = count("SWEET CHG MON: t=");

		nack_slave = 0x36;
		for (i = 0; i < 300; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
		CASE("mr_read_fail_gap_once", before >= 2 && count("SWEET CHG MON: t=") == before && count("SWEET CHG GAP: MON fuel-gauge read failed") == 1 && count("MON rows after a refused P3 stop") == 0 &&
		    p3_stage == 9 && i2c_dead && ncw == 0, fk_log);
	}
	/* the bus stopped by something else while in stage 10: the 0339 GAP names it */
	reset();
	chip[0x69][0xc2] = 0xa2;
	for (i = 0; i < 70; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	i2c_dead = 1;
	for (i = 0; i < 100; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	CASE("mr_bus_stopped_one_gap", count("MON rows after a refused P3 stop (bus stopped)") == 1 && p3_stage == 9 && ncw == 0, fk_log);
	/* a refusal with a dead bus (pre-read failed) does not enter stage 10 */
	reset();
	for (i = 0; i < 600 && p3_stage != 1; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	nack_slave = 0x25;
	for (i = 0; i < 100; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	CASE("mr_dead_bus_refusal_no_stage10", HAS("SWEET CHG GAP: P3 refused, CNFG_09 not written: pre-read") && p3_stage == 9 && i2c_dead && count("SWEET CHG MON: P3 refused") == 0 && count("SWEET CHG MON: t=") == 0, fk_log);
	/* (e) a normal P3 boot is unchanged: one write, 10 samples, stage 3 watcher, MON rows from 330 s, no stage 10 */
	reset();
	stop_at3 = 0;
	for (i = 0; i < 700; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	CASE("mr_normal_p3_unchanged", ncw == 1 && p3_nwrites == 1 && p3_stage == 3 && count("SWEET CHG P3: t=") == 11 && count("SWEET CHG MON: P3 refused") == 0 && count("MON rows after a refused P3 stop") == 0 &&
	    HAS("SWEET CHG MON: t=330s "), fk_log);
	/* P3 off (default) and a refusal that is not a refusal (unsupported argument) stay without rows */
	reset();
	icl_arg = 0;
	for (i = 0; i < 100; i++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
	CASE("mr_p3_off_no_rows", p3_stage == 9 && count("SWEET CHG MON: ") == 0, fk_log);
#endif
#ifdef CHG_PMIC
	/* 10. patch 0314 (tools/test_chg_pmic.py): R1 latch line + GAP lines, R2 fast rows; reads only */
	reset();
	icl_arg = 0;
	chip[0x69][0xb0] = 0x11; chip[0x69][0xb1] = 0x60; chip[0x69][0xb5] = 0x25; chip[0x69][0xb6] = 0xf8; chip[0x69][0xb7] = 0x20;
	run_all(0);
	CASE("pmic_latch_line", HAS("SWEET PMIC LATCH: max77759 CHG_INT=0x11 CHG_INT2=0x60 CHG_DETAILS_00..02=0x25,0xf8,0x20\n") && count("SWEET PMIC LATCH:") == 1, fk_log);
	CASE("pmic_gap_s2mpg10", HAS("SWEET PMIC GAP: s2mpg10 PWRONSRC[0xe] OFFSRC[0xf] STATUS1[0xc] STATUS2[0xd] INT1..INT6[0x0-0x5] no read path (needs an xnu ACPM client"), fk_log);
	CASE("pmic_gap_s2mpg11", HAS("SWEET PMIC GAP: s2mpg11 OFFSRC[0xd] STATUS1[0xc] INT1..INT6[0x0-0x5] no read path (needs an xnu ACPM client"), fk_log);
	CASE("pmic_reads_only", ncw == 0 && viol == 0 && wr_offsets_allowed() && rows_ok(), viol_why);
	reset();
	icl_arg = 0; nack_slave = 0x69;
	run_all(0);
	CASE("pmic_latch_no_line_on_read_fail", count("SWEET PMIC LATCH:") == 0 && HAS("SWEET PMIC GAP: max77759 CHG_INT[0xb0] CHG_INT2[0xb1] CHG_DETAILS_00..02[0xb5-0xb7] read failed at reg 0xb0"), fk_log);

	reset();
	icl_arg = 0;
	run_all(0);
	for (i = 0; i < 100; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	CASE("fast_default_off", HAS("SWEET CHG FAST: off (default;") && count("SWEET CHG FAST: t_ms=") == 0 && count("SWEET CHG FAST MINMAX") == 0 && ncw == 0, fk_log);

	reset();
	icl_arg = 0; fast_arg_ms = 200; fast_arg_n = 25;
	run_all(0);
	for (i = 0; i < 300; i++) {
		fk_ms += 100;
		if (i == 30) { fgw[0x09] = 0x2800; fgw[0x0a] = 0xfe00; }
		if (i == 40) { fgw[0x09] = 0x3000; fgw[0x0a] = 0x0010; }
		sweet_chg_i2c_tick(0);
	}
	CASE("fast_rows_count", count("SWEET CHG FAST: t_ms=") >= 120 && count("SWEET CHG FAST: t_ms=") <= 126 && HAS("SWEET CHG FAST: t_ms=0 i=5000uA v=960mV\n"), fk_log);
	CASE("fast_row_negative_current", HAS("i=-160000uA v=800mV\n"), fk_log);
	CASE("fast_minmax_window", HAS("SWEET CHG FAST MINMAX: vmin=800mV imax=160000uA n=") && HAS("SWEET CHG FAST MINMAX: vmin=960mV imax=5000uA n="), fk_log);
	CASE("fast_minmax_every_10s", count("SWEET CHG FAST MINMAX") == 3, fk_log);
	CASE("fast_done_and_silent", HAS("SWEET CHG FAST: done after 25s") && p3_stage == 9 && count("SWEET CHG GAP") == 0, fk_log);
	CASE("fast_reads_only", ncw == 0 && viol == 0 && wr_offsets_allowed() && rows_ok(), viol_why);
	CASE("fast_stops_for_good", ({ int before = count("SWEET CHG FAST: t_ms="); for (i = 0; i < 100; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); } count("SWEET CHG FAST: t_ms=") == before; }), fk_log);

	{
		struct { const char *n; uint32_t ms, secs; const char *gap; } b[] = {
			{ "fast_bad_ms_low", 50, 600, "SWEET CHG GAP: FAST sweetfg=50 is outside 100..5000" },
			{ "fast_bad_ms_high", 6000, 600, "SWEET CHG GAP: FAST sweetfg=6000 is outside 100..5000" },
			{ "fast_bad_secs_zero", 1000, 0, "SWEET CHG GAP: FAST sweetfgn=0 is outside 1..3600" },
			{ "fast_bad_secs_high", 1000, 5000, "SWEET CHG GAP: FAST sweetfgn=5000 is outside 1..3600" },
		};
		for (i = 0; i < 4; i++) {
			reset();
			icl_arg = 0; fast_arg_ms = b[i].ms; fast_arg_n = b[i].secs;
			run_all(0);
			{ int k; for (k = 0; k < 30; k++) { fk_ms += 100; sweet_chg_i2c_tick(0); } }
			CASE(b[i].n, HAS(b[i].gap) && count("SWEET CHG FAST: t_ms=") == 0 && count("SWEET CHG GAP: FAST") == 1, fk_log);
		}
	}
	reset();
	icl_arg = 0; fast_arg_ms = 200; arg_on = 0;
	run_all(0);
	{ int k; for (k = 0; k < 30; k++) { fk_ms += 100; sweet_chg_i2c_tick(0); } }
	CASE("fast_needs_phase2", HAS("SWEET CHG GAP: FAST refused: phase 2 did not finish") && count("SWEET CHG FAST: t_ms=") == 0, fk_log);

	reset();
	icl_arg = 0; fast_arg_ms = 100; fast_arg_n = 60;
	run_all(0);
	for (i = 0; i < 20; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	{
		int before = count("SWEET CHG FAST: t_ms=");
		uint32_t x;

		nack_slave = 0x36;
		for (i = 0; i < 5; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
		x = i2c_xfers;
		for (i = 0; i < 100; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
		CASE("fast_read_fail_gap_once_no_retry", before >= 15 && count("SWEET CHG GAP: FAST fuel-gauge read failed") == 1 && count("SWEET CHG FAST: t_ms=") == before && HAS("SWEET CHG FAST: stopped (read failed)") &&
		    x == i2c_xfers && i2c_dead && ncw == 0, fk_log);
	}
	reset();
	icl_arg = 0; fast_arg_ms = 100; fast_arg_n = 60;
	run_all(0);
	for (i = 0; i < 10; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	fgw[0x09] = 0;
	for (i = 0; i < 10; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	CASE("fast_bad_vcell_zero", HAS("SWEET CHG GAP: FAST bad vcell=0 mV (raw 0x0000)") && HAS("stopped (bad value)") && count("SWEET CHG GAP: FAST bad vcell") == 1, fk_log);
	reset();
	icl_arg = 0; fast_arg_ms = 100; fast_arg_n = 60;
	fgw[0x09] = 0xffff;
	run_all(0);
	for (i = 0; i < 10; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	CASE("fast_bad_vcell_full_scale", HAS("SWEET CHG GAP: FAST bad vcell=5119 mV (raw 0xffff)") && count("SWEET CHG FAST: t_ms=") == 0, fk_log);
	reset();
	icl_arg = 0; fast_arg_ms = 100; fast_arg_n = 60;
	run_all(0);
	chg_gate_fail_after = gate_calls + 12;
	for (i = 0; i < 40; i++) { fk_ms += 100; sweet_chg_i2c_tick(0); }
	CASE("fast_gate_lost_stops", HAS("SWEET CHG FAST: stopped (phase-1 gate or controller state changed") && ncw == 0, fk_log);

	/* coexistence with phase 3 (sweetchgicl=900): the one CNFG_09 write and the ten samples are unchanged, the fast rows run alongside */
	reset();
	fast_arg_ms = 1000; fast_arg_n = 600;
	run_p3(600);
	CASE("fast_with_p3_unchanged", ncw == 1 && only_cnfg09_chip_writes() && count("SWEET CHG P3: t=") == 11 && count("SWEET CHG FAST: t_ms=") >= 300 && viol == 0, fk_log);
#endif
	printf("SUMMARY fails=%d\n", fails);
	return fails != 0;
}
