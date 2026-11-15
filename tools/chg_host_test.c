/* Host test for bsd/dev/sweet_chg.c (xnu patch 0283, charger phase 1, READ-ONLY). Compiled by tools/test_chg_probe.py with -DSWEET_CHG_HOST
 * -DCHG_SRC="<file extracted from the patch>". Prints `CASE <name> ok` or `CASE <name> FAIL <why>`, one per case.
 *
 * The register offsets and window sizes below are an INDEPENDENT copy of docs/research/charger-max77759-gs101.md (section numbers in the comments);
 * the driver gets its own from the ADT (soc.json), so a typo in either side shows up here. The main property under test: the HSI2C12 window
 * (0x10D50000) is NEVER read unless the clock gate decision says open (AP-113), and RX_DATA (0x38, a read pops the FIFO) is never read at all. */
#include "chg_host_env.h"
#include CHG_SRC

static uint32_t
chg_hw_rd32(int win, uint32_t off)
{
	if (fk_nrd < 8192) {
		fk_rd[fk_nrd].win = win;
		fk_rd[fk_nrd].off = off;
		fk_nrd++;
	}
	return fk_reg[win][off / 4];
}

static int chg_arg_off(void) { return fk_arg_is_off; }
static uint64_t chg_now_ms(void) { return fk_now; }

/* note 1.1-1.4, 5.1: window sizes and word offsets */
static const uint32_t N_SIZE[CHG_W_N] = { [CHG_W_HSI2C] = 0x1000, [CHG_W_SYSREG] = 0x1000, [CHG_W_CMU] = 0x10000, [CHG_W_TOP] = 0x10000, [CHG_W_PAD] = 0x10 };
static const uint32_t N_OFF[CHG_R_N] = {
	[CHG_R_MUX] = 0x650, [CHG_R_DIV] = 0x1810, [CHG_R_GIP] = 0x2030, [CHG_R_GP] = 0x2050, [CHG_R_GR] = 0x206C, [CHG_R_QCH] = 0x3030,
	[CHG_R_TMUX] = 0x10E8, [CHG_R_TDIV] = 0x18E0, [CHG_R_TGATE] = 0x2104, [CHG_R_SW] = 0x10,
	[CHG_R_PCON] = 0x0, [CHG_R_PDAT] = 0x4, [CHG_R_PPUD] = 0x8,
	[CHG_R_CTL] = 0x00, [CHG_R_INTST] = 0x24, [CHG_R_FIFOST] = 0x30, [CHG_R_CONF] = 0x40, [CHG_R_TRANS] = 0x50,
	[CHG_R_FS1] = 0x60, [CHG_R_FS2] = 0x64, [CHG_R_FS3] = 0x68, [CHG_R_USICON] = 0xC4,
};

static uint32_t fk_ov[CHG_R_N];                 /* ADT offset overrides (allowlist cases) */
static int fk_has_ov[CHG_R_N];

static int
chg_load(void)
{
	int r;

	if (fk_load_fails) {
		chg_printf("SWEET CHG GAP: no /sweet-chg ADT node, probe not run (host fake)\n");
		return -1;
	}
	memcpy(chg_off, N_OFF, sizeof(chg_off));
	memcpy(chg_wsize, N_SIZE, sizeof(chg_wsize));
	for (r = 0; r < CHG_R_N; r++) {
		if (fk_has_ov[r]) {
			chg_off[r] = fk_ov[r];
		}
	}
	return 0;
}

/* the observed USI11 open pattern at handoff [obs HANDOFF-s07-s13.md:93,120]: mux 0x10 (top), div 0, gates 0x200000, QCH 0x3F0002, top mux 1 / div 0 / gate 0x200000 */
static void
set_open(void)
{
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_MUX] / 4] = 0x10;
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_DIV] / 4] = 0;
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_GIP] / 4] = 0x200000;
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_GP] / 4] = 0x200000;
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_GR] / 4] = 0x200000;
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_QCH] / 4] = 0x3F0002;
	fk_reg[CHG_W_TOP][N_OFF[CHG_R_TMUX] / 4] = 0x1;
	fk_reg[CHG_W_TOP][N_OFF[CHG_R_TDIV] / 4] = 0;
	fk_reg[CHG_W_TOP][N_OFF[CHG_R_TGATE] / 4] = 0x200000;
}

static void
reset_state(void)
{
	memset(fk_reg, 0, sizeof(fk_reg));
	memset(fk_rd, 0, sizeof(fk_rd));
	fk_nrd = 0;
	fk_log[0] = 0;
	fk_now = 1000;
	fk_arg_is_off = 0;
	fk_load_fails = 0;
	memset(fk_has_ov, 0, sizeof(fk_has_ov));
	memset(chg_off, 0, sizeof(chg_off));
	memset(chg_wsize, 0, sizeof(chg_wsize));
	for (int r = 0; r < CHG_R_N; r++) {
		fk_reg[chg_words[r].win][N_OFF[r] / 4] = 0xA5000000u + (uint32_t)r;     /* distinct marker per word */
	}
	memset(&chg_s, 0, sizeof(chg_s));
	chg_ipclk = 0;
	chg_gate = 0;
	chg_stage = 0;
	chg_a_ms = 0;
	chg_nreads = 0;
}

static int
reads_in(int win)
{
	int i, n = 0;

	for (i = 0; i < fk_nrd; i++) {
		n += fk_rd[i].win == win;
	}
	return n;
}

static int
read_at(int win, uint32_t off)
{
	int i;

	for (i = 0; i < fk_nrd; i++) {
		if (fk_rd[i].win == win && fk_rd[i].off == off) {
			return 1;
		}
	}
	return 0;
}

/* every read is a (window, offset) of the independent allowlist; hsi2c RX_DATA/TX_DATA never */
static int
all_reads_allowed(void)
{
	int i, j;

	for (i = 0; i < fk_nrd; i++) {
		int ok = 0;

		for (j = 0; j < CHG_R_N; j++) {
			ok |= chg_words[j].win == fk_rd[i].win && N_OFF[j] == fk_rd[i].off;
		}
		if (!ok) {
			return 0;
		}
	}
	return !read_at(CHG_W_HSI2C, 0x38) && !read_at(CHG_W_HSI2C, 0x34);
}

static void run_ticks(int n) { while (n-- > 0) { sweet_chg_tick(0); } }

static int fails;
#define CASE(name, cond, why) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL %s\n", name, why); fails++; } } while (0)
#define HAS(s) (strstr(fk_log, (s)) != NULL)

/* independent oracle of the gate for the exhaustive sweep (bits: see the header comment of chg_gate_open) */
static int
oracle_open(uint32_t mux, uint32_t gip, uint32_t gp, uint32_t gr, uint32_t qch, uint32_t tgate)
{
	if (mux & (1u << 16)) { return 0; }
	if (!(gip & (1u << 21)) || (gip & (1u << 28))) { return 0; }
	if (!(gp & (1u << 21)) || (gp & (1u << 28))) { return 0; }
	if (!(gr & (1u << 21)) || (gr & (1u << 28))) { return 0; }
	if (!(qch & 2u)) { return 0; }
	if ((mux & (1u << 4)) && !(tgate & (1u << 21))) { return 0; }
	return 1;
}

int
main(void)
{
	unsigned m;

	/* 1. the observed open pattern: stage A first, stage B only after the log drained, all 9 hsi2c words read, ipclk 400 MHz */
	reset_state();
	set_open();
	sweet_chg_tick(0);
	CASE("open_stage_a_prints", HAS("SWEET CHG P1: start") && HAS("cmu_peric1 usi12_mux[0x650]=0x10") && HAS("cmu_top ip_mux[0x10e8]=0x1") &&
	    HAS("SW_CONF[0x10]=") && HAS("gpp23 CON[0x0]=") && HAS("hsi2c12 clock check: open"), fk_log);
	CASE("open_stage_a_no_hsi2c", reads_in(CHG_W_HSI2C) == 0, "hsi2c12 read in stage A");
	CASE("open_ipclk_400mhz", HAS("ipclk 400000 kHz implied"), fk_log);
	sweet_chg_tick(1);      /* log still pending: B must wait */
	CASE("open_b_waits_for_log", reads_in(CHG_W_HSI2C) == 0 && !HAS("USI_CON["), "stage B ran while the log was pending");
	sweet_chg_tick(0);
	CASE("open_stage_b_reads_nine", reads_in(CHG_W_HSI2C) == 9 && HAS("hsi2c12 USI_CON[0xc4]=0xa5000015") && HAS("TRANS_STATUS[0x50]=") && HAS("INT_STATUS[0x24]="), fk_log);
	CASE("open_reads_allowlisted", all_reads_allowed(), "a read outside the allowlist or an RX/TX_DATA read");
	CASE("open_total_reads", fk_nrd == 6 + 3 + 1 + 3 + 9, "unexpected number of reads");
	CASE("open_done_once", HAS("done reads=22") && chg_stage == 2, fk_log);
	{
		int n = fk_nrd;
		run_ticks(5);
		CASE("open_one_shot", fk_nrd == n, "reads after done");
	}

	/* 2. gate closed (each reason): hsi2c12 window never read, GAP line with the gate words, done */
	{
		struct { const char *name; int role; uint32_t val; } closed[] = {
			{ "closed_ipclk_gate", CHG_R_GIP, 0 }, { "closed_pclk_gate", CHG_R_GP, 0 }, { "closed_rstn_gate", CHG_R_GR, 0 },
			{ "closed_ipclk_auto", CHG_R_GIP, 0x10200000 }, { "closed_pclk_auto", CHG_R_GP, 0x10200000 }, { "closed_rstn_auto", CHG_R_GR, 0x10200000 },
			{ "closed_qch_noreq", CHG_R_QCH, 0x3F0001 }, { "closed_mux_busy", CHG_R_MUX, 0x10010 }, { "closed_top_gate", CHG_R_TGATE, 0 },
			{ "closed_gate_allones", CHG_R_GP, 0xffffffffu },
		};
		unsigned i;

		for (i = 0; i < sizeof(closed) / sizeof(closed[0]); i++) {
			reset_state();
			set_open();
			fk_reg[chg_words[closed[i].role].win][N_OFF[closed[i].role] / 4] = closed[i].val;
			chg_stage = 0;
			chg_nreads = 0;
			fk_nrd = 0;
			fk_log[0] = 0;
			sweet_chg_tick(0);
			run_ticks(20);
			CASE(closed[i].name, HAS("SWEET CHG GAP: hsi2c12 clock gated, block not read (") && reads_in(CHG_W_HSI2C) == 0 && !HAS("USI_CON[") &&
			    HAS("done reads=") && HAS("(hsi2c12 not touched)") && all_reads_allowed(), fk_log);
		}
	}
	reset_state();
	set_open();
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_GP] / 4] = 0x0;
	chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
	sweet_chg_tick(0);
	CASE("gap_line_has_gate_values", HAS("gate_ipclk=0x200000 gate_pclk=0x0 gate_rstn=0x200000 qch=0x3f0002") && HAS("PCLK (bus) gate"), fk_log);

	/* 3. user mux on OSCCLK: gate open without the top gate, ipclk undecided with a GAP */
	reset_state();
	set_open();
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_MUX] / 4] = 0x0;
	fk_reg[CHG_W_TOP][N_OFF[CHG_R_TGATE] / 4] = 0x0;
	chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
	sweet_chg_tick(0);
	sweet_chg_tick(0);
	CASE("osc_open_ipclk_gap", HAS("clock check: open") && HAS("SWEET CHG GAP: ipclk undecided (user mux selects OSCCLK_PERIC1") && reads_in(CHG_W_HSI2C) == 9 &&
	    HAS("SCL not computed (ipclk undecided)"), fk_log);

	/* 4. ipclk table */
	{
		struct { const char *name; uint32_t div, tmux, tdiv; uint32_t khz; const char *gap; } t[] = {
			{ "ipclk_div0", 0, 1, 0, 400000, NULL }, { "ipclk_div1", 1, 1, 0, 200000, NULL }, { "ipclk_div2", 2, 1, 0, 133333, NULL }, { "ipclk_div3", 3, 1, 0, 100000, NULL },
			{ "ipclk_div4_gap", 4, 1, 0, 0, "above 3" }, { "ipclk_topmux2_gap", 0, 2, 0, 0, "not the note's 400 MHz row" }, { "ipclk_topdiv1_gap", 0, 1, 1, 0, "not the note's 400 MHz row" },
		};
		unsigned i;

		for (i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
			char want[64];

			reset_state();
			set_open();
					fk_reg[CHG_W_CMU][N_OFF[CHG_R_DIV] / 4] = t[i].div;
			fk_reg[CHG_W_TOP][N_OFF[CHG_R_TMUX] / 4] = t[i].tmux;
			fk_reg[CHG_W_TOP][N_OFF[CHG_R_TDIV] / 4] = t[i].tdiv;
			chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
			sweet_chg_tick(0);
			if (t[i].khz) {
				snprintf(want, sizeof(want), "ipclk %u kHz implied", t[i].khz);
				CASE(t[i].name, HAS(want), fk_log);
			} else {
				CASE(t[i].name, HAS("SWEET CHG GAP: ipclk undecided (") && HAS(t[i].gap), fk_log);
			}
		}
	}
	reset_state();
	set_open();
	fk_reg[CHG_W_CMU][N_OFF[CHG_R_DIV] / 4] = 1;
	fk_reg[CHG_W_HSI2C][N_OFF[CHG_R_FS3] / 4] = 33u << 16;
	chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
	sweet_chg_tick(0);
	sweet_chg_tick(0);
	CASE("scl_from_fs3", HAS("CLK_DIV=33 -> SCL 367647 Hz"), fk_log);      /* 200 MHz / (34 * 16) = 367647 */

	/* 5. exhaustive gate sweep against the independent oracle: 2^10 combinations of the bits the gate looks at */
	{
		int bad = 0, opened = 0;

		for (m = 0; m < 1024; m++) {
			uint32_t mux = ((m & 1) ? (1u << 4) : 0) | ((m & 2) ? (1u << 16) : 0);
			uint32_t gip = ((m & 4) ? (1u << 21) : 0) | ((m & 8) ? (1u << 28) : 0);
			uint32_t gp = ((m & 16) ? (1u << 21) : 0) | ((m & 32) ? (1u << 28) : 0);
			uint32_t gr = ((m & 64) ? (1u << 21) : 0) | ((m & 128) ? (1u << 28) : 0);
			uint32_t qch = (m & 256) ? 2u : 0;
			uint32_t tg = (m & 512) ? (1u << 21) : 0;
			int want = oracle_open(mux, gip, gp, gr, qch, tg);

			reset_state();
					fk_reg[CHG_W_CMU][N_OFF[CHG_R_MUX] / 4] = mux;
			fk_reg[CHG_W_CMU][N_OFF[CHG_R_GIP] / 4] = gip;
			fk_reg[CHG_W_CMU][N_OFF[CHG_R_GP] / 4] = gp;
			fk_reg[CHG_W_CMU][N_OFF[CHG_R_GR] / 4] = gr;
			fk_reg[CHG_W_CMU][N_OFF[CHG_R_QCH] / 4] = qch;
			fk_reg[CHG_W_TOP][N_OFF[CHG_R_TGATE] / 4] = tg;
			chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
			sweet_chg_tick(0);
			run_ticks(3);
			opened += want;
			if ((reads_in(CHG_W_HSI2C) != 0) != (want != 0) || !all_reads_allowed()) {
				bad++;
			}
		}
		CASE("gate_sweep_1024", bad == 0 && opened > 0 && opened < 1024, "gate differs from the oracle");
	}

	/* 6. allowlist: a role whose ADT offset is not on the list (RX_DATA 0x38) or outside / misaligned is refused with a GAP and not read */
	{
		struct { const char *name; uint32_t off; } a[] = { { "allow_rx_data", 0x38 }, { "allow_tx_data", 0x34 }, { "allow_outside", 0x1000 }, { "allow_misaligned", 0x25 }, { "allow_other", 0x20 } };
		unsigned i;

		for (i = 0; i < sizeof(a) / sizeof(a[0]); i++) {
			reset_state();
			set_open();
					fk_ov[CHG_R_CTL] = a[i].off; fk_has_ov[CHG_R_CTL] = 1;
			chg_stage = 0; fk_nrd = 0; fk_log[0] = 0; chg_nreads = 0;
			sweet_chg_tick(0);
			sweet_chg_tick(0);
			CASE(a[i].name, !read_at(CHG_W_HSI2C, a[i].off) && HAS("SWEET CHG GAP: hsi2c12.CTL offset") && HAS("hsi2c12 words not (all) read"), fk_log);
		}
		reset_state();
		set_open();
			fk_ov[CHG_R_MUX] = 0x654; fk_has_ov[CHG_R_MUX] = 1;     /* the mux's auto-gating word: not on the list */
		chg_stage = 0; fk_nrd = 0; fk_log[0] = 0;
		sweet_chg_tick(0);
		run_ticks(3);
		CASE("allow_cmu_other_closes_gate", !read_at(CHG_W_CMU, 0x654) && HAS("hsi2c12 clock gated, block not read") && reads_in(CHG_W_HSI2C) == 0, fk_log);
	}

	/* 7. opt-out and missing node: nothing is read */
	reset_state();
	set_open();
	fk_arg_is_off = 1;
	run_ticks(5);
	CASE("optout_zero_reads", fk_nrd == 0 && HAS("disabled by sweetchgprobe=0"), fk_log);
	reset_state();
	fk_load_fails = 1;
	run_ticks(5);
	CASE("no_node_zero_reads", fk_nrd == 0 && HAS("SWEET CHG GAP: no /sweet-chg ADT node"), fk_log);

	/* 8. log never drains: B waits CHG_FORCE_MS, then runs with a GAP line (gate was open) */
	reset_state();
	set_open();
	sweet_chg_tick(1);
	fk_now += CHG_FORCE_MS - 1;
	sweet_chg_tick(1);
	CASE("pending_waits", reads_in(CHG_W_HSI2C) == 0, "stage B ran before the force time");
	fk_now += 1;
	sweet_chg_tick(1);
	CASE("pending_forced_with_gap", reads_in(CHG_W_HSI2C) == 9 && HAS("stage A log lines still unsent"), fk_log);

	/* 9. defence in depth: stage B with a closed gate reads nothing */
	reset_state();
	set_open();
	chg_gate = 0;
	chg_stage_b();
	CASE("stage_b_closed_gate", reads_in(CHG_W_HSI2C) == 0 && HAS("stage B entered with a closed gate"), fk_log);

	printf("SUMMARY fails=%d\n", fails);
	return fails != 0;
}
