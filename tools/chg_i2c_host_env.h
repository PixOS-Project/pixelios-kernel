/* Host test environment for bsd/dev/sweet_chg_i2c.c (extracted from xnu/patches/0284). Phase 2 of the charger work: the driver's only stores go to HSI2C12 transfer registers. */
#ifndef CHG_I2C_HOST_ENV_H
#define CHG_I2C_HOST_ENV_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

static char fk_log[1 << 17];

static int
i2c_printf(const char *fmt, ...)
{
	char tmp[2048];
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
