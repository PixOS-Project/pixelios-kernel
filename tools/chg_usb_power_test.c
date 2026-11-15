/* Host test of the sweet_usb.c part of xnu patch 0287 (review chg-0287 F1/F2/F7): the SuperSpeed configuration descriptor's bMaxPower byte and the accessor
 * sweet_usb_ss_configured(). tools/test_chg_icl.py extracts the two blocks between the BEGIN/END 0287 markers from the patch (their '+' lines) and compiles them
 * here against a fake `ud`; the flag clears (disconnect, bus reset, restart) are checked on the patch text. */
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>

#define DSTS_SPEED_SS 4u
static struct { bool configured, ss_power_ok; uint32_t speed; uint8_t ss_bmax_sent; } ud;
static uint32_t boot_arg_value;
static int boot_arg_present;
static int PE_parse_boot_argn(const char *n, void *out, int len)
{
	if (!boot_arg_present || strcmp(n, "sweetchgicl") != 0 || len != 4) { return 0; }
	memcpy(out, &boot_arg_value, 4);
	return 1;
}

#include POWER_C
#include ACCESSOR_C

static int fails;
#define CASE(name, cond) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL\n", name); fails++; } } while (0)

int main(int argc, char **argv)
{
	static const struct { int cfg, ok; uint32_t sp; uint8_t bm; uint32_t want; const char *n; } t[] = {
		{ 1, 1, 4, 112, 4, "acc_ss_896" }, { 1, 1, 5, 112, 5, "acc_ssp_896" }, { 1, 1, 4, 255, 4, "acc_ss_more" },
		{ 0, 1, 4, 112, 0, "acc_not_configured" }, { 1, 0, 4, 112, 0, "acc_flag_cleared" }, { 1, 1, 0, 112, 0, "acc_high_speed" }, { 1, 1, 3, 112, 0, "acc_speed_3" },
		{ 1, 1, 4, 62, 0, "acc_descriptor_496" }, { 1, 1, 4, 111, 0, "acc_descriptor_888" }, { 1, 1, 4, 0, 0, "acc_nothing_sent" },
	};
	unsigned i;

	if (argc < 2) { return 2; }
	boot_arg_present = strcmp(argv[1], "absent") != 0;
	boot_arg_value = (uint32_t)atoi(argv[1]);
	if (strcmp(argv[1], "absent") == 0) { CASE("byte_default_62", ud_ss_power_byte() == 62); }
	else if (strcmp(argv[1], "900") == 0) { CASE("byte_icl900_112", ud_ss_power_byte() == 112); }
	else if (strcmp(argv[1], "500") == 0) { CASE("byte_icl500_62", ud_ss_power_byte() == 62); }
	else { CASE("byte_other_62", ud_ss_power_byte() == 62); }
	for (i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
		ud.configured = t[i].cfg; ud.ss_power_ok = t[i].ok; ud.speed = t[i].sp; ud.ss_bmax_sent = t[i].bm;
		CASE(t[i].n, sweet_usb_ss_configured() == t[i].want);
	}
	printf("SUMMARY fails=%d\n", fails);
	return fails != 0;
}
