/* Host test for bsd/dev/sweet_chg_i2c.c (xnu patch 0284, charger phase 2). Compiled by tools/test_chg_i2c.py with -DSWEET_CHG_I2C_HOST -DCHG_I2C_SRC="<extracted>".
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
static uint8_t txlog[256]; static int ntx;
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

int sweet_chg_gate_still_open(void) { gate_calls++; return gate_ok; }
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
		if (ntx < 256) {
			txlog[ntx++] = (uint8_t)v;
		}
		if (!run_active || cur_rw) {
			bad("TX_DATA written outside a write message");
		}
		cur_reg = (uint8_t)v;
		tx_busy_reads = txbusy_cfg;
		hw[OFF_INTST / 4] |= (nack_slave == cur_slave || !present[cur_slave]) ? (1u << 9) : (hang_slave == cur_slave ? 0u : (1u << 7));
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
		if (!rw && (v & (1u << 17))) { bad("register-address message with STOP"); }
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
	chip[0x25][0x00] = 0xd1; chip[0x25][0x01] = 0x18; chip[0x25][0x02] = 0x0b; chip[0x25][0x03] = 0x00; chip[0x25][0x04] = 0x02; chip[0x25][0x05] = 0x00; chip[0x25][0x87] = 0x43;
	chip[0x69][0xb9] = 0x05; chip[0x69][0xbb] = 0x07; chip[0x69][0xbd] = 0x14; chip[0x69][0xc2] = 0x13; chip[0x69][0xc5] = 0x6a; chip[0x69][0xcb] = 0xc0;
	chip[0x69][0xb4] = 0x9f; chip[0x69][0xb6] = 0xf1; chip[0x69][0xb7] = 0x20;
	nw = nrd_rx = ntx = rxn = rxh = viol = run_active = 0;
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

int
main(void)
{
	int i;
	static const uint32_t shape1[10] = { 0x48, 0x04, 0x24, 0x70, 0x00, 0x20, 0x44, 0x44, 0x34, 0x24 };
	static const uint32_t shape2[9] = { 0x48, 0x04, 0x24, 0x70, 0x00, 0x20, 0x44, 0x44, 0x24 };

	/* 1. one transfer: the exact write sequence of note 5.4 (the reviewer's table) */
	reset();
	i2c_load();
	{
		uint8_t b = 0;
		int r = chg_i2c_read(0x66, 0x00, 1, &b);

		CASE("xfer_ok", r == 0 && b == 0x3b && viol == 0, viol_why);
		CASE("xfer_19_writes", nw == 19, "write count");
		for (i = 0; i < 10; i++) {
			if (wlog[i].off != shape1[i]) { break; }
		}
		for (int j = 0; j < 9 && i == 10; j++) {
			if (wlog[10 + j].off != shape2[j]) { i = 100 + j; }
		}
		CASE("xfer_offset_sequence", i == 10, "offset order differs from note 5.4");
		CASE("xfer_msg1_values", wlog[0].val == 0x5 && wlog[1].val == 0x10013 && wlog[2].val == 0 && wlog[3].val == (0x07f00000u | (0x66u << 10)) && wlog[4].val == 0x88 &&
		    wlog[5].val == 0xf80 && wlog[6].val == 1 && wlog[7].val == 0x80000001u && wlog[8].val == 0x00 && wlog[9].val == 0x80, "message 1 values");
		CASE("xfer_msg2_values", wlog[10].val == 0x5 && wlog[11].val == 0x10013 && wlog[12].val == 0 && wlog[13].val == wlog[3].val && wlog[14].val == 0x48 &&
		    wlog[15].val == 0xf80 && wlog[16].val == 0x30001 && wlog[17].val == 0x80030001u && wlog[18].val == 0x80, "message 2 values");
		CASE("xfer_one_tx_byte_is_the_register", ntx == 1 && txlog[0] == 0x00, "TX bytes");
		CASE("xfer_controller_untouched", hw[OFF_CONF / 4] == 0x980110ffu && hw[0x68 / 4] == 0x3e0000 && hw[0x60 / 4] == 0x1f0ff00 && hw[0x64 / 4] == 0x30003e0, "CONF/TIMING changed");
	}

	/* 2. full pass */
	reset();
	run_all(0);
	CASE("pass_done_43", HAS("SWEET CHG P2: done xfers=43 errors=0") && viol == 0, viol_why);
	CASE("pass_writes_allowlisted", wr_offsets_allowed() && nw == 43 * 19 + 1, "write offsets");
	{
		static const uint8_t want[43] = { 0, 1, 0x60, 0, 1, 2, 3, 4, 5, 0x87, 0x21, 0x09, 0x0a, 0x06,
			0xb0, 0xb1, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xbb, 0xbc, 0xbd, 0xbe, 0xbf, 0xc0, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xcb, 0xcc };
		CASE("pass_tx_bytes_only_register_addresses", ntx == 43 && memcmp(txlog, want, 43) == 0, "TX bytes differ from the read list");
	}
	CASE("pass_rows_pmic_tcpc", HAS("pmic[0x66] ID[0x00]=0x3b") && HAS("REVISION[0x01]=0x12 (VER=2 REV=2)") && HAS("tcpc[0x25] VID=0x18d1 PID=0x000b BCD_DEV=0x0002") &&
	    HAS("BC_STATUS1[0x87]=0x43 CHGTYP=3"), fk_log);
	CASE("pass_rows_fg", HAS("fg[0x36] DEVNAME[0x21]=0x6200 VCell[0x09]=0x3000 (960 mV)") && HAS("Current[0x0a]=0x0010") && HAS("RepSOC[0x06]=0x2800 (40.00 %)"), fk_log);
	CASE("pass_rows_chg", HAS("CNFG_00[0xb9]=0x5 MODE=0x5") && HAS("CNFG_02[0xbb]=0x7 CHGCC~466 mA") && HAS("CV = 4200 mV") &&
	    HAS("CNFG_09[0xc2]=0x13 NO_AUTOIBUS=0 CHGIN_ILIM=19 (~500 mA)") && HAS("CHGINSEL=1") && HAS("WDTEN=0") && HAS("CHG_DTLS=1 (fast-CC)") && HAS("CHGIN_STS=1") &&
	    HAS("INT_OK[0xb4]=0x9f CHGIN_OK=0 AICL_OK=1"), fk_log);
	{
		/* chg-p2 log bug: the kernel printf cut the 291-character DETAILS/INT_OK row after "INT_OK[0xb4]=0x". Every row stays short and ends with its own newline. */
		const char *l = fk_log;
		int longest = 0, rows = 0, nonl = 0;

		while (*l) {
			const char *e = strchr(l, '\n');

			if (e == NULL) { nonl = 1; break; }
			if ((int)(e - l) > longest) { longest = (int)(e - l); }
			rows++;
			l = e + 1;
		}
		CASE("pass_every_row_short_and_terminated", rows >= 13 && longest <= 200 && !nonl, "a P2 row is over 200 characters or has no newline");
		CASE("pass_int_ok_row_complete", HAS("INT_OK[0xb4]=0x9f CHGIN_OK=0 AICL_OK=1 (0 = AICL limiting)\n") && HAS("DETAILS_00[0xb5]=0x0") && HAS("DETAILS_01[0xb6]=0xf1"), fk_log);
	}
	CASE("pass_ordering_pmic_first", strstr(fk_log, "pmic[0x66]") < strstr(fk_log, "tcpc[0x25]") && strstr(fk_log, "tcpc[0x25]") < strstr(fk_log, "fg[0x36]") && strstr(fk_log, "fg[0x36]") < strstr(fk_log, "chg[0x69] raw"), fk_log);
	CASE("pass_gate_checked_each_group", gate_calls == 5, "gate_calls");

	/* 3. NACK: stops everything */
	reset();
	nack_slave = 0x25;
	run_all(0);
	CASE("nack_stops_all", HAS("SWEET CHG GAP: i2c NACK (NO_DEV_ACK/NO_DEV) in the register-address message slave 0x25 reg 0x0") && HAS("all further transfers stopped") &&
	    HAS("done xfers=4 errors=1") && !HAS("fg[0x36]") && !HAS("chg[0x69]") && ntx == 4, fk_log);
	reset();
	present[0x25] = 0;
	run_all(0);
	CASE("absent_slave_nack_in_msg1", HAS("NACK") && HAS("done xfers=4 errors=1"), fk_log);

	/* 4. timeout */
	reset();
	hang_slave = 0x36;
	run_all(0);
	CASE("timeout_stops_all", HAS("TIMEOUT in the") && HAS("INT_STATUS=0x0") && HAS("done xfers=11 errors=1") && !HAS("chg[0x69]"), fk_log);
	CASE("timeout_after_100ms", fk_us >= 100000 && fk_us < 400000, "poll time");

	/* 5. identity stops */
	reset();
	chip[0x66][0x00] = 0x3a;
	run_all(0);
	CASE("pmic_id_stop", HAS("PMIC_ID 0x3a != 0x3b, whole pass stopped") && HAS("done xfers=3 errors=0") && !HAS("tcpc[0x25]") && ntx == 3, fk_log);
	reset();
	fgw[0x21] = 0x6400;
	run_all(0);
	CASE("fg_devname_stop", HAS("FG DEVNAME 0x6400 high byte not 0x62/0x63") && !HAS("chg[0x69]"), fk_log);
	reset();
	fgw[0x21] = 0x6300;
	run_all(0);
	CASE("fg_devname_63_ok", HAS("done xfers=43 errors=0"), fk_log);

	/* 6. gate / controller-state blocks: nothing is written */
	reset();
	gate_ok = 0;
	run_all(0);
	CASE("gate_mismatch_blocks_all", nw == 0 && ntx == 0 && HAS("phase-1 gate state is not the open pattern any more") && HAS("done xfers=0 errors=1"), fk_log);
	{
		struct { const char *n; uint32_t off, val; } c[] = {
			{ "ctl_not_master", OFF_CTL, 0x40 }, { "conf_no_auto", OFF_CONF, 0x180110ff }, { "fifo_not_idle", 0, 0 }, { "ctl_swrst", OFF_CTL, 0x80000048u },
		};
		for (i = 0; i < 4; i++) {
			reset();
			if (c[i].off == 0 && c[i].val == 0) { fifost_override = 0x1000000; } else { hw[c[i].off / 4] = c[i].val; }
			run_all(0);
			CASE(c[i].n, nw == 0 && ntx == 0 && HAS("controller not idle / not in the phase-1 state"), fk_log);
		}
	}

	/* 7. default off, load failure */
	reset();
	arg_on = 0;
	run_all(0);
	CASE("default_off_no_access", nw == 0 && nrd_rx == 0 && gate_calls == 0 && HAS("SWEET CHG P2: off (default; sweetchgi2c=1"), fk_log);
	reset();
	load_fail = 1;
	run_all(0);
	CASE("load_fail_no_access", nw == 0 && gate_calls == 0, fk_log);


	/* 11. review chg-0284 F1: the controller must be IDLE at the gate; every refusal stores nothing and logs the values */
	{
		struct { const char *n; uint32_t off, val; const char *why; } g[] = {
			{ "gate_busy", OFF_TRANS, 0x80001u | (1u << 17), "MASTER_BUSY" }, { "gate_lose_state", OFF_TRANS, 0x8000c, "master state 0xC" },
			{ "gate_run_pending", OFF_AUTO, 0x80000001u, "MASTER_RUN set" }, { "gate_conf_hs", OFF_CONF, 0x980110ffu | (1u << 29), "CONF bit 29/30" },
			{ "gate_conf_10bit", OFF_CONF, 0x980110ffu | (1u << 30), "CONF bit 29/30" }, { "gate_ctl_22", OFF_CTL, 0x48u | (1u << 22), "CTL bit 31/30/29/22" },
			{ "gate_ctl_29", OFF_CTL, 0x48u | (1u << 29), "CTL bit 31/30/29/22" }, { "gate_ctl_30", OFF_CTL, 0x48u | (1u << 30), "CTL bit 31/30/29/22" },
			{ "gate_int_status", OFF_INTST, 0x80, "INT_STATUS not 0" },
		};
		for (i = 0; i < 9; i++) {
			reset();
			hw[g[i].off / 4] = g[i].val;
			run_all(0);
			CASE(g[i].n, nw == 0 && ntx == 0 && HAS(g[i].why) && HAS("TRANS_STATUS=0x") && HAS("AUTO_CONF=0x") && HAS("INT_STATUS=0x"), fk_log);
		}
	}
	reset();                                    /* observed phase-1 TRANS_STATUS 0x80001 / INT_STATUS 0 passes */
	run_all(0);
	CASE("gate_observed_state_passes", HAS("done xfers=43 errors=0"), fk_log);

	/* 12. F2: MASTER_BUSY after the STOP is waited for; a stuck one stops everything */
	reset();
	busy_cfg = 6;
	run_all(0);
	CASE("stop_busy_waited", HAS("done xfers=43 errors=0") && viol == 0, viol_why);
	reset();
	busy_cfg = 100000;
	run_all(0);
	CASE("stop_busy_timeout", HAS("TIMEOUT waiting for MASTER_BUSY to clear after the STOP") && HAS("all further transfers stopped") && HAS("done xfers=1 errors=1"), fk_log);

	/* 13. F3: interrupts disarmed and pending sources cleared on failure and at the end */
	reset();
	run_all(0);
	CASE("end_int_enable_zero", hw[OFF_INTEN / 4] == 0 && wlog[nw - 1].off == OFF_INTEN && wlog[nw - 1].val == 0, "INT_ENABLE not 0 at the end");
	reset();
	nack_slave = 0x25;
	run_all(0);
	{
		int z = 0, k;

		for (k = 0; k < nw; k++) { z += wlog[k].off == OFF_INTEN && wlog[k].val == 0; }
		CASE("fail_int_enable_zero", hw[OFF_INTEN / 4] == 0 && (hw[OFF_INTST / 4] & 0xf80) == 0 && wr_offsets_allowed() && z == 2, "INT_ENABLE/INT_STATUS not disarmed both at the failure and at the end");
	}
	reset();
	gate_ok = 0;
	run_all(0);
	CASE("no_store_when_nothing_written", nw == 0, "finish stored although nothing was started");

	/* 14. F4: wrong ADT role/offset pairs are refused at load */
	{
		struct { const char *n; int a, b; } sw[] = { { "adt_swap_txd_auto", I_TXD, I_AUTO }, { "adt_swap_intst_ctl", I_INTST, I_CTL }, { "adt_swap_inten_fifoctl", I_INTEN, I_FIFOCTL } };
		for (i = 0; i < 3; i++) {
			reset();
			fk_ov[sw[i].a] = i2c_expect[sw[i].b]; fk_has_ov[sw[i].a] = 1;
			fk_ov[sw[i].b] = i2c_expect[sw[i].a]; fk_has_ov[sw[i].b] = 1;
			run_all(0);
			CASE(sw[i].n, nw == 0 && ntx == 0 && HAS("expected 0x") && gate_calls == 0, fk_log);
		}
		reset();
		fk_ov[I_RXD] = 0x3c; fk_has_ov[I_RXD] = 1;
		run_all(0);
		CASE("adt_wrong_rxd", nw == 0 && HAS("SWEET CHG GAP: P2 /sweet-chg i-hsi2c12-rx-data = 0x3c, expected 0x38"), fk_log);
	}

	/* 15. F5: protocol corner cases the model can drive (each kills a mutant of the review) */
	reset();
	txbusy_cfg = 7;
	run_all(0);
	CASE("msg1_waits_for_tx_empty", HAS("done xfers=43 errors=0") && viol == 0, viol_why);
	reset();
	short_slave = 0x66;
	run_all(0);
	CASE("short_read_gap_and_stop", HAS("short read (TRANSFER_DONE with fewer bytes than asked)") && HAS("all further transfers stopped") && HAS("done xfers=1 errors=1") && ntx == 1, fk_log);
	reset();
	err2_slave = 0x25;
	run_all(0);
	CASE("error_bits_in_read_message", HAS("bus error in the read message slave 0x25") && HAS("done xfers=4 errors=1") && !HAS("fg[0x36]"), fk_log);
	reset();
	run_all(0);
	i2c_dead = 1;
	{
		uint8_t b = 0;
		int w0 = nw;

		CASE("read_after_dead_stores_nothing", chg_i2c_read(0x66, 0x00, 1, &b) != 0 && nw == w0 && ntx == 43, "a transfer ran after i2c_dead");
	}
	reset();
	hang_slave = 0x25;
	run_all(0);
	CASE("timeout_bounded_by_clock", HAS("TIMEOUT in the") && nrd_total < 3000, "poll loop ran far beyond the 100 ms budget");

	/* 8. store guards */
	reset();
	i2c_load();
	i2c_off[I_CONF] = OFF_CONF;
	CASE("wr_conf_refused", i2c_wr(I_CONF, 0) != 0 && nw == 0, "CONF write not refused");
	CASE("wr_swrst_refused", i2c_wr(I_CTL, 0x80000048u) != 0 && nw == 0, "SW_RST write not refused");
	CASE("wr_tout_en_refused", i2c_wr(I_TOUT, 0x80000000u) != 0 && nw == 0, "TIMEOUT_EN write not refused");
	i2c_off[I_TXD] = 0x38;
	CASE("wr_rx_data_refused", i2c_wr(I_TXD, 1) != 0 && nw == 0, "RX_DATA offset write not refused");
	i2c_off[I_TXD] = 0x68;
	CASE("wr_timing_refused", i2c_wr(I_TXD, 1) != 0 && nw == 0, "TIMING_FS3 write not refused");
	i2c_off[I_TXD] = 0x1000;
	CASE("wr_outside_refused", i2c_wr(I_TXD, 1) != 0 && nw == 0, "outside window write not refused");
	i2c_off[I_TXD] = OFF_TXD;
	i2c_off[I_RXD] = 0x68;
	{
		uint32_t v;

		CASE("rd_timing_refused", i2c_rd(I_RXD, &v) != 0, "TIMING read not refused");
	}

	/* 9. read-list guards */
	reset();
	i2c_load();
	{
		uint8_t b[16];

		CASE("target_charger_below", chg_i2c_read(0x69, 0xaf, 1, b) != 0 && ntx == 0, "");
		CASE("target_charger_above", chg_i2c_read(0x69, 0xcd, 1, b) != 0 && ntx == 0, "");
		CASE("target_pmic_other", chg_i2c_read(0x66, 0x02, 1, b) != 0 && ntx == 0, "");
		CASE("target_unknown_slave", chg_i2c_read(0x30, 0x00, 1, b) != 0 && ntx == 0, "");
		CASE("target_n_too_big", chg_i2c_read(0x69, 0xb0, 17, b) != 0 && ntx == 0, "");
		CASE("target_fg_n1", chg_i2c_read(0x36, 0x21, 1, b) != 0 && ntx == 0, "");
		CASE("refusals_do_not_stop", i2c_dead == 0 && nw == 0, "");
	}

	/* 10. FIFO trigger cap and a 12-byte read through the internal message function */
	reset();
	i2c_load();
	{
		uint8_t b[16];
		int j;

		for (j = 0; j < 12; j++) { chip[0x69][0xb0 + j] = (uint8_t)(0xa0 + j); }
		cur_reg = 0;
		CASE("msg_12_bytes", i2c_msg(0x69, 0xb0, 0, 1, NULL) == 0 && i2c_msg(0x69, 0xb0, 1, 12, b) == 0 && b[11] == 0xab && viol == 0, viol_why);
		CASE("fifo_trigger_capped_at_8", wlog[11 + 0].off == 0x04 && wlog[11].val == (3u | (8u << 4) | (8u << 16)), "trigger cap");
	}

	printf("SUMMARY fails=%d\n", fails);
	return fails != 0;
}
