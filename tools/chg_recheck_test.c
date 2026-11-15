/* Host test of sweet_chg_gate_still_open() (xnu patch 0284 hunk in sweet_chg.c): the phase-1 gate state must be re-checked READ-ONLY before phase-2 transfers.
 * Compiled with -DSWEET_CHG_HOST -DCHG_SRC=<sweet_chg.c as patched by 0283+0284>; the env is tools/chg_host_env.h of phase 1. */
#include "chg_host_env.h"
#include CHG_SRC

static int i2c_ticks, nstores;
void sweet_chg_i2c_tick(int log_pending) { (void)log_pending; i2c_ticks++; }

static uint32_t chg_hw_rd32(int win, uint32_t off)
{
	fk_rd[fk_nrd < 8192 ? fk_nrd : 0].win = win;
	fk_rd[fk_nrd < 8192 ? fk_nrd : 0].off = off;
	fk_nrd++;
	return fk_reg[win][off / 4];
}
static int chg_arg_off(void) { return fk_arg_is_off; }
static uint64_t chg_now_ms(void) { return fk_now; }
static int chg_load(void)
{
	static const uint32_t off[CHG_R_N] = { 0x650, 0x1810, 0x2030, 0x2050, 0x206C, 0x3030, 0x10E8, 0x18E0, 0x2104, 0x10, 0x0, 0x4, 0x8, 0x00, 0x24, 0x30, 0x40, 0x50, 0x60, 0x64, 0x68, 0xC4 };
	static const uint32_t sz[CHG_W_N] = { [CHG_W_HSI2C] = 0x1000, [CHG_W_SYSREG] = 0x1000, [CHG_W_CMU] = 0x10000, [CHG_W_TOP] = 0x10000, [CHG_W_PAD] = 0x10 };
	memcpy(chg_off, off, sizeof(chg_off));
	memcpy(chg_wsize, sz, sizeof(chg_wsize));
	return 0;
}

static int fails;
#define CASE(name, cond) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL\n%s\n", name, fk_log); fails++; } } while (0)
#define HAS(s) (strstr(fk_log, (s)) != NULL)

static void open_state(void)
{
	memset(fk_reg, 0, sizeof(fk_reg));
	fk_reg[CHG_W_CMU][0x650 / 4] = 0x10; fk_reg[CHG_W_CMU][0x2030 / 4] = 0x200000; fk_reg[CHG_W_CMU][0x2050 / 4] = 0x200000; fk_reg[CHG_W_CMU][0x206C / 4] = 0x200000;
	fk_reg[CHG_W_CMU][0x3030 / 4] = 0x3F0002; fk_reg[CHG_W_TOP][0x10E8 / 4] = 1; fk_reg[CHG_W_TOP][0x2104 / 4] = 0x200000; fk_reg[CHG_W_SYSREG][0x10 / 4] = 4;
	fk_reg[CHG_W_PAD][0] = 0x220000; fk_reg[CHG_W_PAD][1] = 0x30; fk_reg[CHG_W_PAD][2] = 0x330000;
	memset(&chg_s, 0, sizeof(chg_s)); chg_gate = 0; chg_stage = 0; chg_nreads = 0; fk_nrd = 0; fk_log[0] = 0; fk_now = 0; fk_arg_is_off = 0;
}

int main(void)
{
	(void)fk_load_fails;
	open_state();
	CASE("before_phase1_refused", sweet_chg_gate_still_open() == 0 && HAS("phase 1 did not establish an open gate"));
	sweet_chg_tick(0); sweet_chg_tick(0);   /* stage A, stage B */
	fk_log[0] = 0;
	CASE("still_open_when_unchanged", sweet_chg_gate_still_open() == 1 && fk_log[0] == 0);
	{
		int before = fk_nrd;
		(void)sweet_chg_gate_still_open();
		CASE("recheck_reads_thirteen_words_only", fk_nrd - before == 13);
	}
	fk_reg[CHG_W_CMU][0x2050 / 4] = 0;
	CASE("pclk_gate_closed_refused", sweet_chg_gate_still_open() == 0 && HAS("gate recheck: gate_pclk[0x2050] now 0x0"));
	fk_reg[CHG_W_CMU][0x2050 / 4] = 0x200000; fk_log[0] = 0;
	fk_reg[CHG_W_CMU][0x3030 / 4] = 0x3F0003;       /* any change at all, even a harmless-looking QCH bit, is "differs from the open pattern" */
	CASE("any_word_change_refused", sweet_chg_gate_still_open() == 0 && HAS("qch[0x3030] now 0x3f0003"));
	fk_reg[CHG_W_CMU][0x3030 / 4] = 0x3F0002; fk_log[0] = 0;
	fk_reg[CHG_W_SYSREG][0x10 / 4] = 0;
	CASE("sw_conf_change_refused", sweet_chg_gate_still_open() == 0 && HAS("SW_CONF[0x10] now 0x0"));
	fk_reg[CHG_W_SYSREG][0x10 / 4] = 4;
	CASE("restored_is_open_again", sweet_chg_gate_still_open() == 1);
	fk_reg[CHG_W_PAD][0] = 0x20000; fk_log[0] = 0;
	CASE("pad_con_change_refused", sweet_chg_gate_still_open() == 0 && HAS("CON[0x0] now 0x20000"));
	fk_reg[CHG_W_PAD][0] = 0x220000; fk_log[0] = 0;
	fk_reg[CHG_W_PAD][2] = 0x110000;
	CASE("pad_pud_change_refused", sweet_chg_gate_still_open() == 0 && HAS("PUD[0x8] now 0x110000"));
	fk_reg[CHG_W_PAD][2] = 0x330000; fk_log[0] = 0;
	fk_reg[CHG_W_PAD][1] = 0x30 | 0x1;              /* DAT pin 0 belongs to SPI11: may move */
	CASE("pad_dat_other_pins_ignored", sweet_chg_gate_still_open() == 1);
	fk_reg[CHG_W_PAD][1] = 0x20;                    /* SDA (pin 5) dat 0->... pin 4 SCL low */
	CASE("pad_dat_scl_sda_change_refused", sweet_chg_gate_still_open() == 0 && HAS("DAT[0x4] now 0x20"));
	fk_reg[CHG_W_PAD][1] = 0x30; fk_log[0] = 0;
	/* a gate that was CLOSED in phase 1 is never "still open" */
	open_state();
	fk_reg[CHG_W_CMU][0x2030 / 4] = 0;
	sweet_chg_tick(0);
	fk_reg[CHG_W_CMU][0x2030 / 4] = 0x200000;
	CASE("phase1_closed_stays_refused", sweet_chg_gate_still_open() == 0 && HAS("did not establish an open gate"));
	/* tick hands over to phase 2 only once phase 1 is finished */
	open_state();
	sweet_chg_tick(0); sweet_chg_tick(0);   /* A, B */
	CASE("phase1_finished", chg_stage == 2 && i2c_ticks == 0);
	sweet_chg_tick(0);
	CASE("tick_calls_phase2_after_phase1", i2c_ticks == 1);
	printf("SUMMARY fails=%d\n", fails);
	(void)nstores;
	return fails != 0;
}
