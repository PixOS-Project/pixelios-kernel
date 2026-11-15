/* Host test environment for bsd/dev/sweet_chg.c (extracted from xnu/patches/0283): a fake register file that records every read. Included by the source itself
 * under SWEET_CHG_HOST; tools/chg_host_test.c defines chg_hw_rd32 / chg_load / chg_arg_off / chg_now_ms after the source. Phase 1 of the charger work: reads only. */
#ifndef CHG_HOST_ENV_H
#define CHG_HOST_ENV_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

#define FK_WIN 5
#define FK_WORDS 0x4000                         /* 64 KiB per window, the largest the ADT hands out */
static uint32_t fk_reg[FK_WIN][FK_WORDS];
static struct { int win; uint32_t off; } fk_rd[8192];
static int fk_nrd;
static char fk_log[1 << 16];
static uint64_t fk_now;
static int fk_arg_is_off, fk_load_fails;

static int
chg_printf(const char *fmt, ...)
{
	char tmp[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	if (strlen(fk_log) + strlen(tmp) < sizeof(fk_log) - 1) {
		strcat(fk_log, tmp);
	}
	return 0;
}
#endif
