/*
 * wlanh1.c - read-only HSI2 S2MPU + SSMT + RC DBI Command probe (see wlanh1.h,
 * docs/research/90). Freestanding, no static state, no MMIO store of any kind (audited like
 * gpus2mpu.o).
 * Offsets [src: s2mpu-regs.h]: CTRL0 0x00, CTRL1 0x04, CFG 0x10, INTERRUPT_ENABLE_PER_VID_SET
 * 0x20, FAULT_STATUS 0x2000, FAULT_PA_LOW/HIGH/INFO(n) 0x2004/8/10 + n*0x20,
 * L1ENTRY_ATTR(n, m) 0x4004 + n*0x200 + m*8 (bit0 L2TABLE_EN, bit1 RD, bit2 WR).
 * RC DBI: +0 vendor/device, +4 Command (bit2 BUS MASTER) / Status.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "wlanh1.h"

#if SOC_GS101 && defined(WLAN_HSI2_S2MPU) && defined(WLAN_HSI2_SSMT) && defined(WLAN_DBI)

#define S_CTRL0		0x000u
#define S_CTRL1		0x004u
#define S_CFG		0x010u
#define S_IRQEN_VID	0x020u
#define S_FAULT_STATUS	0x2000u
#define S_FAULT_PA_LOW(n)	(0x2004u + (n) * 0x20u)
#define S_FAULT_PA_HIGH(n)	(0x2008u + (n) * 0x20u)
#define S_FAULT_INFO(n)		(0x2010u + (n) * 0x20u)
#define S_L1_ATTR(n, m)		(0x4004u + (n) * 0x200u + (m) * 8u)
#define S_GRANULES	64u
#define SSMT_WORDS	16u

__attribute__((noipa)) static uint32_t put_txt(char *dst, uint32_t cap, uint32_t pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static uint32_t put_hex(char *dst, uint32_t cap, uint32_t pos,
					       uint64_t v, uint32_t digits)
{
	for (uint32_t i = 0; i < digits && pos + 1 < cap; i++) {
		uint32_t d = (uint32_t)((v >> (4 * (digits - 1 - i))) & 0xf);

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, uint32_t pos)
{
	/* style-allow: assert the audited wlanh1.o must not reference shim_assert_fail (STYLE-C.md) */
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("WLAN", buf);
}

/* "H1 <name> pa=XXXXXXXXX v=XXXXXXXX" or "... unreadable". */
static int32_t rd(const char *name, uint32_t idx, int32_t with_idx, uintptr_t pa, uint32_t *v)
{
	/* style-allow: assert the audited wlanh1.o must not reference shim_assert_fail (STYLE-C.md) */
	char line[RAMLOG_LINE_MAX];
	uint32_t p = put_txt(line, sizeof(line), 0, "H1 ");
	int32_t ok;

	ok = try_read32(pa, v);
	p = put_txt(line, sizeof(line), p, name);
	if (with_idx)
		p = put_hex(line, sizeof(line), p, idx, with_idx == 2 ? 3 : 1);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, pa, 9);
	if (ok) {
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, *v, 8);
	} else {
		p = put_txt(line, sizeof(line), p, " unreadable");
	}
	emit(line, p);
	return ok;
}

/* One VID: scan its 64 L1 entries, print the vid and L1ATTR lines, *l1 = the entry of WLAN_H1_GB.
 * Returns the number of unreadable entries. */
static uint32_t scan_vid(uint32_t n, uint32_t *l1)
{
	/* style-allow: assert the audited wlanh1.o must not reference shim_assert_fail (STYLE-C.md) */
	char line[RAMLOG_LINE_MAX];
	uint32_t p;
	uint64_t rdwr = 0, l2 = 0;
	uint32_t nonzero = 0, vbad = 0;

	for (uint32_t m = 0; m < S_GRANULES; m++) {
		uint32_t a = 0;
		int32_t ok = try_read32(WLAN_HSI2_S2MPU + S_L1_ATTR(n, m), &a);

		if (!ok) {
			vbad++;
			continue;
		}
		if (a)
			nonzero++;
		if ((a & 6u) == 6u)
			rdwr |= (uint64_t)1 << m;
		if (a & 1u)
			l2 |= (uint64_t)1 << m;
		if (m == WLAN_H1_GB)
			*l1 = a;
	}
	p = put_txt(line, sizeof(line), 0, "H1 S2MPU vid");
	p = put_hex(line, sizeof(line), p, n, 1);
	p = put_txt(line, sizeof(line), p, " rdwr=");
	p = put_hex(line, sizeof(line), p, rdwr, 16);
	p = put_txt(line, sizeof(line), p, " l2=");
	p = put_hex(line, sizeof(line), p, l2, 16);
	p = put_txt(line, sizeof(line), p, " nonzero=");
	p = put_hex(line, sizeof(line), p, nonzero, 2);
	p = put_txt(line, sizeof(line), p, " bad=");
	p = put_hex(line, sizeof(line), p, vbad, 2);
	emit(line, p);
	/* the exact L1 entry of the reference granule (re-read: a bad read of it is already
	 * in vbad, this line says so) */
	p = put_txt(line, sizeof(line), 0, "H1 S2MPU L1ATTR");
	p = put_hex(line, sizeof(line), p, n, 1);
	p = put_txt(line, sizeof(line), p, " gb=");
	p = put_hex(line, sizeof(line), p, WLAN_H1_GB, 2);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, WLAN_HSI2_S2MPU + S_L1_ATTR(n, WLAN_H1_GB), 9);
	p = put_txt(line, sizeof(line), p, " v=");
	p = put_hex(line, sizeof(line), p, *l1, 8);
	emit(line, p);
	return vbad;
}

void wlan_h1_run(void)
{
	/* style-allow: assert the audited wlanh1.o must not reference shim_assert_fail (STYLE-C.md) */
	char line[RAMLOG_LINE_MAX];
	uint32_t v, ctrl0 = 0, fs = 0, id = 0, cmd = 0, l1[2] = {0, 0};
	uint32_t bad = 0, p;
	enum wlan_h1_verdict verdict;
	const char *vs;

	bad += !rd("S2MPU CTRL0", 0, 0, WLAN_HSI2_S2MPU + S_CTRL0, &ctrl0);
	bad += !rd("S2MPU CTRL1", 0, 0, WLAN_HSI2_S2MPU + S_CTRL1, &v);
	bad += !rd("S2MPU CFG", 0, 0, WLAN_HSI2_S2MPU + S_CFG, &v);
	bad += !rd("S2MPU IRQEN_VID", 0, 0, WLAN_HSI2_S2MPU + S_IRQEN_VID, &v);
	bad += !rd("S2MPU FAULT_STATUS", 0, 0, WLAN_HSI2_S2MPU + S_FAULT_STATUS, &fs);
	for (uint32_t n = 0; n < 2; n++) {
		bad += !rd("S2MPU FAULT_PA_LOW", n, 1, WLAN_HSI2_S2MPU + S_FAULT_PA_LOW(n), &v);
		bad += !rd("S2MPU FAULT_PA_HIGH", n, 1, WLAN_HSI2_S2MPU + S_FAULT_PA_HIGH(n), &v);
		bad += !rd("S2MPU FAULT_INFO", n, 1, WLAN_HSI2_S2MPU + S_FAULT_INFO(n), &v);
	}
	for (uint32_t n = 0; n < 2; n++)
		bad += scan_vid(n, &l1[n]);
	for (uint32_t w = 0; w < SSMT_WORDS; w++)
		bad += !rd("SSMT+", w * 4u, 2, WLAN_HSI2_SSMT + w * 4u, &v);
	bad += !rd("DBI id", 0, 0, WLAN_DBI, &id);
	bad += !rd("RC cmd", 0, 0, WLAN_DBI + 4u, &cmd);
	verdict = wlan_h1_decide(bad == 0, ctrl0, id, cmd, l1[0], l1[1]);
	vs = verdict == H1_GO ? "go" : verdict == H1_S2MPU ? "s2mpu" :
	     verdict == H1_DBI ? "dbi" : "stop";
	p = put_txt(line, sizeof(line), 0, "H1 SUMMARY s2mpu_en=");
	p = put_hex(line, sizeof(line), p, ctrl0 & 1u, 1);
	p = put_txt(line, sizeof(line), p, " fs=");
	p = put_hex(line, sizeof(line), p, fs, 8);
	p = put_txt(line, sizeof(line), p, " v0=");
	p = put_hex(line, sizeof(line), p, l1[0] & 7u, 1);
	p = put_txt(line, sizeof(line), p, " v1=");
	p = put_hex(line, sizeof(line), p, l1[1] & 7u, 1);
	p = put_txt(line, sizeof(line), p, " rc_master=");
	p = put_hex(line, sizeof(line), p, (cmd >> 2) & 1u, 1);
	p = put_txt(line, sizeof(line), p, " bad=");
	p = put_hex(line, sizeof(line), p, bad, 2);
	p = put_txt(line, sizeof(line), p, " verdict=");
	p = put_txt(line, sizeof(line), p, vs);
	emit(line, p);
	if (bad)
		con_str("WLAN",
			"WIFI GAP: h1 unreadable S2MPU/SSMT/DBI word(s): verdict stop, no G2 (U-H1-3)");
}

#endif
