/* Host test of patch 0305: sweet_chg_gate_still_open() compares only pins 4/5 (SCL/SDA) of the gpp23 CON/PUD words; pins 0-3 are the touch SPI11
 * pads and may move (observed resident-152843-b1: CON 0x220000 -> 0x221222). Compiled with -DSWEET_CHG_HOST -DCHG_SRC=<sweet_chg.c 0283+0284+0305>. */
#include "chg_host_env.h"
#include CHG_SRC

void sweet_chg_i2c_tick(int log_pending) { (void)log_pending; }
static uint32_t chg_hw_rd32(int win, uint32_t off) { fk_nrd++; return fk_reg[win][off / 4]; }
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

int main(void)
{
	(void)fk_load_fails; (void)fk_rd;
	memset(fk_reg, 0, sizeof(fk_reg));
	fk_reg[CHG_W_CMU][0x650 / 4] = 0x10; fk_reg[CHG_W_CMU][0x2030 / 4] = 0x200000; fk_reg[CHG_W_CMU][0x2050 / 4] = 0x200000; fk_reg[CHG_W_CMU][0x206C / 4] = 0x200000;
	fk_reg[CHG_W_CMU][0x3030 / 4] = 0x3F0002; fk_reg[CHG_W_TOP][0x10E8 / 4] = 1; fk_reg[CHG_W_TOP][0x2104 / 4] = 0x200000; fk_reg[CHG_W_SYSREG][0x10 / 4] = 4;
	fk_reg[CHG_W_PAD][0] = 0x220000; fk_reg[CHG_W_PAD][1] = 0x30; fk_reg[CHG_W_PAD][2] = 0x11331111;
	memset(&chg_s, 0, sizeof(chg_s)); chg_gate = 0; chg_stage = 0; fk_log[0] = 0;
	sweet_chg_tick(0); sweet_chg_tick(0);
	fk_log[0] = 0;
	CASE("open_unchanged", sweet_chg_gate_still_open() == 1);
	fk_reg[CHG_W_PAD][0] = 0x221222;                /* the observed touch change: pins 0-3 = SPI func 2,2,2 + cs output 1 */
	CASE("con_touch_pins_ignored", sweet_chg_gate_still_open() == 1 && fk_log[0] == 0);
	fk_reg[CHG_W_PAD][2] = 0x00330000;              /* touch sweettouchcs=1: pads PUD := 0 on pins 0-3 */
	CASE("pud_touch_pins_ignored", sweet_chg_gate_still_open() == 1 && fk_log[0] == 0);
	fk_reg[CHG_W_PAD][0] = 0x201222;                /* pin 4 (SCL) func 2 -> 0 */
	CASE("con_scl_change_refused", sweet_chg_gate_still_open() == 0 && HAS("CON[0x0] now 0x201222"));
	fk_reg[CHG_W_PAD][0] = 0x121222; fk_log[0] = 0; /* pin 5 (SDA) func 2 -> 1 */
	CASE("con_sda_change_refused", sweet_chg_gate_still_open() == 0 && HAS("CON[0x0] now 0x121222"));
	fk_reg[CHG_W_PAD][0] = 0x221222; fk_log[0] = 0;
	fk_reg[CHG_W_PAD][2] = 0x00130000;              /* pin 5 PUD 3 -> 1 */
	CASE("pud_sda_change_refused", sweet_chg_gate_still_open() == 0 && HAS("PUD[0x8] now 0x130000"));
	fk_reg[CHG_W_PAD][2] = 0x00330000; fk_log[0] = 0;
	fk_reg[CHG_W_PAD][0] = 0x1221222;               /* pin 6 (not ours, not touch): outside the mask too */
	CASE("con_other_pins_ignored", sweet_chg_gate_still_open() == 1);
	fk_reg[CHG_W_CMU][0x3030 / 4] = 0x3F0003; fk_log[0] = 0;   /* every non-pad word is still compared whole */
	CASE("qch_change_still_refused", sweet_chg_gate_still_open() == 0 && HAS("qch[0x3030] now 0x3f0003"));
	printf("RESULT %s fails=%d\n", fails ? "FAIL" : "PASS", fails);
	return fails != 0;
}
