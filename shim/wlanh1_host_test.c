/* Host test for wlanh1.c (docs/research/90 section 5): fake S2MPU/SSMT/DBI windows with per-address fault injection. try_write32 is deliberately NOT defined, so linking fails if the
 * probe ever references a writer. Uses the REAL generated board macros. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/wlanh1_host_test wlanh1_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "wlanh1.c"

#if !(SOC_GS101 && defined(WLAN_HSI2_S2MPU) && defined(WLAN_HSI2_SSMT) && defined(WLAN_DBI))
#error "needs the bluejay board_config.h"
#endif

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl;
static uintptr_t bad_pa;
static uint32_t ctrl0, fstat, cmd = 0x6, dbi_id = 0x44891AE0, attr0, attr1;
static uintptr_t seen[512];
static unsigned nseen;

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	assert(strlen(s) < RAMLOG_LINE_MAX);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}

static unsigned n_pstore;

void pstore_save(void)
{
	n_pstore++;
}

void con_hex(const char *label, uint64_t v)
{
	assert(nl < 512);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %016llX", label, (unsigned long long)v);
}

int try_read32(uintptr_t pa, uint32_t *v)
{
	assert(nseen < 512);
	seen[nseen++] = pa;
	if (pa == bad_pa)
		return 0;
	if (pa >= WLAN_HSI2_S2MPU && pa < WLAN_HSI2_S2MPU + 0x10000) {
		uintptr_t off = pa - WLAN_HSI2_S2MPU;

		*v = 0;
		if (off == 0)
			*v = ctrl0;
		else if (off == 0x2000)
			*v = fstat;
		else if (off >= 0x4000 && off < 0x4000 + 2 * 0x200 && (off & 7) == 4) {
			unsigned vid = (off - 0x4000) / 0x200, m = ((off - 0x4000) % 0x200) / 8;

			if (m == WLAN_H1_GB)
				*v = vid ? attr1 : attr0;
			else if (m == 0)
				*v = 6;
		}
		return 1;
	}
	if (pa >= WLAN_HSI2_SSMT && pa < WLAN_HSI2_SSMT + 0x10000) {
		*v = 0x11110000u + (uint32_t)(pa - WLAN_HSI2_SSMT);
		return 1;
	}
	if (pa == WLAN_DBI) { *v = dbi_id; return 1; }
	if (pa == WLAN_DBI + 4) { *v = cmd; return 1; }
	assert(!"read outside the S2MPU / SSMT / DBI words");
	return 0;
}

static void run(void)
{
	nl = 0;
	nseen = 0;
	wlan_h1_run();
}

static const char *summary(void)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], "H1 SUMMARY"))
			return lines[i];
	assert(!"no SUMMARY line");
	return 0;
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

int main(void)
{
	/* (a) not enforcing, fault status 0, Command 0x6 (MEM|MASTER) -> go */
	run();
	assert(strstr(summary(), "verdict=go") && strstr(summary(), "rc_master=1") && strstr(summary(), "s2mpu_en=0"));
	assert(has("WLAN H1 S2MPU CTRL0 pa=014480000 v=00000000"));
	assert(has("WLAN H1 S2MPU vid0 rdwr=0000000000000001 l2=0000000000000000 nonzero=01 bad=00"));
	assert(has("WLAN H1 S2MPU L1ATTR0 gb=22 pa=014484114 v=00000000"));
	assert(has("WLAN H1 S2MPU L1ATTR1 gb=22 pa=014484314 v=00000000"));
	assert(has("WLAN H1 SSMT+000 pa=0144F0000 v=11110000"));
	assert(has("WLAN H1 SSMT+03C pa=0144F003C v=1111003C"));
	assert(has("WLAN H1 DBI id pa=014800000 v=44891AE0"));
	assert(has("WLAN H1 RC cmd pa=014800004 v=00000006"));
	assert(!has("WIFI GAP"));
	/* (e) Command without MASTER -> dbi */
	cmd = 0x2;
	run();
	assert(strstr(summary(), "verdict=dbi") && strstr(summary(), "rc_master=0"));
	cmd = 0x6;
	/* (b) enforcing, VID0 RD+WR, VID1 closed -> s2mpu; both open -> go */
	ctrl0 = 1; attr0 = 6; attr1 = 0;
	run();
	assert(strstr(summary(), "verdict=s2mpu") && strstr(summary(), "v0=6 v1=0") && strstr(summary(), "s2mpu_en=1"));
	attr1 = 6;
	run();
	assert(strstr(summary(), "verdict=go"));
	/* WR missing, or (c) L2TABLE_EN set -> s2mpu */
	attr1 = 2;
	run();
	assert(strstr(summary(), "verdict=s2mpu"));
	attr1 = 7;
	run();
	assert(strstr(summary(), "verdict=s2mpu") && has("l2=0000000400000000"));
	attr1 = 6; attr0 = 7;
	run();
	assert(strstr(summary(), "verdict=s2mpu") && has("l2=0000000400000000"));
	attr0 = 6;
	/* FAULT_STATUS is reported, not decisive */
	fstat = 0x1;
	run();
	assert(strstr(summary(), "fs=00000001") && strstr(summary(), "verdict=go"));
	fstat = 0;
	ctrl0 = 0; attr0 = attr1 = 0;
	/* DBI id all ones -> stop */
	dbi_id = 0xFFFFFFFFu;
	run();
	assert(strstr(summary(), "verdict=stop"));
	dbi_id = 0x44891AE0;
	/* (f) the exact read set: S2MPU control/fault words + 2 x 64 L1 attrs + 16 SSMT words + two DBI words */
	run();
	assert(nseen == 5 + 6 + 128 + 16 + 2);
	for (unsigned i = 0; i < nseen; i++) {
		uintptr_t pa = seen[i];
		int in_s2 = pa >= WLAN_HSI2_S2MPU && pa < WLAN_HSI2_S2MPU + 0x10000;
		int in_ssmt = pa >= WLAN_HSI2_SSMT && pa < WLAN_HSI2_SSMT + 0x40;

		assert(in_s2 || in_ssmt || pa == WLAN_DBI || pa == WLAN_DBI + 4);
	}
	/* (d) every read address faulting on its own -> unreadable, bad>0, verdict stop, GAP line */
	for (unsigned i = 0; i < nseen; i++) {
		uintptr_t victim = seen[i];

		bad_pa = victim;
		run();
		assert(strstr(summary(), "verdict=stop"));
		assert(has("WIFI GAP: h1 unreadable"));
		assert(strstr(summary(), "bad=") && !strstr(summary(), "bad=00"));
	}
	bad_pa = WLAN_HSI2_SSMT + 8;
	run();
	assert(has("WLAN H1 SSMT+008 pa=0144F0008 unreadable"));
	bad_pa = 0;
	/* (i) wlan_h1_after_hold (T-191f): H1 runs only when the hold returned 0; any other result
	 * reads nothing, takes one checkpoint and says so (rule 8) */
	nl = nseen = n_pstore = 0;
	wlan_h1_after_hold(0);
	assert(n_pstore == 3 && nseen == 5 + 6 + 128 + 16 + 2);
	assert(has("SECTION WLAN h1") && has("WLAN H1 SUMMARY") && !has("WIFI GAP"));
	for (unsigned rc = 1; rc <= 2; rc++) {
		char want[96];

		nl = nseen = n_pstore = 0;
		wlan_h1_after_hold(rc);
		assert(n_pstore == 1 && nseen == 0 && !has("H1 SUMMARY"));
		snprintf(want, sizeof(want), "WIFI GAP: h1 skipped, wlan_link_hold rc (0=held) %016X", rc);
		assert(has(want));
	}
	/* (g) no line longer than 95 chars (also enforced by con_str above) and (h) the reference granule */
	assert((0x880100000ull >> 30) == WLAN_H1_GB);
	puts("wlanh1_host_test: OK");
	return 0;
}
