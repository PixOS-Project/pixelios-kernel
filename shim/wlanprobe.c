/*
 * wlanprobe.c - read-only BCM4389 Wi-Fi path probe (see wlanprobe.h).
 *
 * Every register access is try_read32() (a bus fault prints "unreadable"
 * and the probe continues). No MMIO stores of any kind, no SMC, no GPIO
 * changes; the object is audited by tools/test-bluejay-boot.py with the
 * same stack-only-stores policy as usbprobe.o. No static state: the
 * summary re-reads the pins instead of keeping them.
 *
 * Register meaning ([src] = Google android-gs-bluejay-6.1-android16, soc-gs drivers/pci/controller/dwc-whi/, local copies in
 * ~/pixelios-artifacts/refs/wlan/; docs/research/12 sections 1.2 and 4, docs/research/20 section 3):
 *  - PMU +0x3EC4 bit0 = PCIe1 PHY isolation (1 = isolation bypassed / released; not a power switch: *guess*), +0x3EC0 the same for PCIe0 (modem, for comparison)
 *    (pcie-exynos-rc.h:120 mask, pcie-exynos-common.h:52 PCIE_PHY_BYPASS = 1)
 *  - ELBI +0x2C8 RDLH_LINKUP (pcie-exynos-rc.h:50), &0x3F == 0x11 is L0 (rc.h:51 mask, rc.c:3233-3237); +0x54 APP_LTSSM_ENABLE;
 *    +0x2E8 PM_DSTATE; +0x3A4 SOFT_RESET (pcie-exynos-rc.h:44-62); ELBI +0x80 = DEVICE_TYPE of the sub controller (rc.c:3158-3159, Linux writes 0x04 = RC there)
 *  - PMA (the PHY, `exynos_phy_read`) lock words, exactly the ones establish_link() checks and logs (rc.c:3103-3139, 3148-3157, 3115):
 *      +0x3F0 bit3 = PLL lock (rc.c:3125-3126), +0xE0C bit2 = CDR lock (rc.c:3129-3130, rc.h:318 RX_CDR_LOCK 0xE0C),
 *      +0xDE8 &0xF == 0xF = offset calibration done (rc.c:3134-3136), +0x5FC = "OC Initial Status" (rc.c:3115-3116),
 *      +0xE18 bit7 = OC ok after the soft power reset (rc.c:3148-3149)
 *    The old PMA/PCS 00..1C dumps had no citable meaning and are gone (review D2); a PCS dump returns only with a citation.
 *  - DBI +0x0 vendor/device, +0x8 class (live DWC RC answers)
 *  - gpp14-2 WLAN_EN, gph2-0 PERST, gph2-1 CLKREQ (func 2), gph2-5 dev_wake, gpa7-0 host_wake. Bank layout CON +0x0 / DAT +0x4 is VERIFIED
 *    (Linux drivers/pinctrl/samsung/pinctrl-exynos-arm64.c:56-67, docs/research/20 section 2.1).
 *  - PD_HSI2_A/B labels (review D5): 0x17462080/84 are the HSI0 domain's CFG/STATUS (pd-hsi0 reg 0x17462080, gs101-pm-domains.dtsi:78-88; HSI0_STATUS =
 *    PMU +0x2084 bit0, flexpmu_cal_local_gs101.h hsi0_status); 0x17462180/84 belong to the pd_hsi2 node, which is `status = "disabled"` (:90-98) and has no
 *    power sequence, so they are NOT an HSI2 status: printed for the record, never used as a gate.
 *  - gph2 (GPIO_HSI2, 0x14440000) is read in level 1 without a gate: HSI2 is never power-gated (pd_hsi2 disabled, above) and the UFS controller inside
 *    HSI2 (0x14700000) answers the shim's reads [obs, hardware/ufs-gpt-read-2026-10-21.txt]; the GPIO block's own clock gate is not citable, which is the
 *    accepted residual risk of level 1 (it is one 8-byte read pair that earlier runs of the same bank layout in docs/research/10 did not trip: [guess]).
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "wlanprobe.h"

#if SOC_GS101 && defined(WLAN_ELBI)

static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++)
		dst[pos++] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("WLAN", buf);
}

/* "<name> pa=XXXXXXXX v=XXXXXXXX" or "... unreadable"; returns 1 and *v when read. */
static int rd(const char *name, uintptr_t pa, uint32_t *v)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = 0;
	int ok = try_read32(pa, v);
	p = put_txt(line, sizeof(line), p, name);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, pa, 8);
	if (ok) {
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, *v, 8);
	} else {
		p = put_txt(line, sizeof(line), p, " unreadable");
	}
	emit(line, p);
	return ok;
}

/* rd() with the name "<pfx>+OOO" built on the stack. */
static int rd_off(const char *pfx, uintptr_t base, uint32_t off, uint32_t *v)
{
	char nm[24];
	unsigned p = put_txt(nm, sizeof(nm), 0, pfx);
	p = put_txt(nm, sizeof(nm), p, "+");
	p = put_hex(nm, sizeof(nm), p, off, 3);
	nm[p] = 0;
	return rd(nm, base + off, v);
}

static void rd_words(const char *pfx, uintptr_t base, uint32_t first, uint32_t last)
{
	uint32_t v;
	for (uint32_t off = first; off <= last; off += 4)
		rd_off(pfx, base, off, &v);
}

/* "<name> func=X lvl=Y" for one pin of a CON/DAT pair. */
static void pin_line(const char *name, int con_ok, uint32_t con, int dat_ok, uint32_t dat,
		     unsigned pin)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, name);
	p = put_txt(line, sizeof(line), p, " func=");
	p = con_ok ? put_hex(line, sizeof(line), p, wlan_gpio_func(con, pin), 1)
		   : put_txt(line, sizeof(line), p, "?");
	p = put_txt(line, sizeof(line), p, " lvl=");
	p = dat_ok ? put_hex(line, sizeof(line), p, wlan_gpio_level(dat, pin), 1)
		   : put_txt(line, sizeof(line), p, "?");
	emit(line, p);
}

/* Read a bank's CON/DAT and print the raw words (the pins are decoded by the caller). */
static void bank(const char *con_name, const char *dat_name, uintptr_t base,
		 int *con_ok, uint32_t *con, int *dat_ok, uint32_t *dat)
{
	*con_ok = rd(con_name, base, con);
	*dat_ok = rd(dat_name, base + 4, dat);
}

void wlan_probe_l1(void)
{
	uint32_t v, con, dat;
	int con_ok, dat_ok;

	rd("PMU_PCIE1_ISO", WLAN_PMU_PCIE1_ISO, &v);
	rd("PMU_PCIE0_ISO", WLAN_PMU_PCIE0_ISO, &v);
	rd("PD_HSI0_CFG", WLAN_PD_HSI2_B, &v);
	rd("PD_HSI0_STAT", WLAN_PD_HSI2_B + 4, &v);
	rd("PD_HSI2_A", WLAN_PD_HSI2_A, &v);
	rd("PD_HSI2_A+4", WLAN_PD_HSI2_A + 4, &v);

	bank("GPP14.CON", "GPP14.DAT", WLAN_GPP14, &con_ok, &con, &dat_ok, &dat);
	pin_line("WLAN_EN gpp14-2", con_ok, con, dat_ok, dat, 2);
	bank("GPH2.CON", "GPH2.DAT", WLAN_GPH2, &con_ok, &con, &dat_ok, &dat);
	pin_line("PERST gph2-0", con_ok, con, dat_ok, dat, 0);
	pin_line("CLKREQ gph2-1", con_ok, con, dat_ok, dat, 1);
	pin_line("DEVWAKE gph2-5", con_ok, con, dat_ok, dat, 5);
	bank("GPA7.CON", "GPA7.DAT", WLAN_GPA7, &con_ok, &con, &dat_ok, &dat);
	pin_line("HOSTWAKE gpa7-0", con_ok, con, dat_ok, dat, 0);
}

/* The gate, computed from two silent reads; print = also print the CMU word and the verdict line. */
static enum wlan_gate gate_now(int print)
{
	uint32_t iso = 0, mux = 0;
	int iso_ok = try_read32(WLAN_PMU_PCIE1_ISO, &iso);
	int mux_ok = 0;
	enum wlan_gate g;

	if (iso_ok && (iso & 1u)) {	/* the CMU word is read only once the PHY says it is powered */
		if (print)
			mux_ok = rd("CMU_HSI2_PCIE_MUX", WLAN_CMU_HSI2 + 0x620, &mux);
		else
			mux_ok = try_read32(WLAN_CMU_HSI2 + 0x620, &mux);
	}
	g = wlan_gate(iso_ok, iso, mux_ok, mux);
	if (print) {
		char line[RAMLOG_LINE_MAX];
		unsigned p = put_txt(line, sizeof(line), 0, g == WLAN_GATE_OPEN ? "l2 gate=open" : "l2 gated: ");

		if (g != WLAN_GATE_OPEN)
			p = put_txt(line, sizeof(line), p, wlan_gate_name(g));
		emit(line, p);
	}
	return g;
}

int wlan_probe_l2_open(void)
{
	return gate_now(1) == WLAN_GATE_OPEN;
}

void wlan_probe_l2_group(unsigned group)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t v, pll = 0, cdr = 0, oc = 0, ocr = 0, en = 0, perst = 0, link = 0, id = 0;
	int pll_ok, cdr_ok, oc_ok, ocr_ok, en_ok, perst_ok, link_ok, id_ok;
	enum wlan_gate g = gate_now(0);
	unsigned p;

	if (group == 4) {
		/* the one-line SUMMARY, always printed; the wrapper words only if the gate is open */
		id_ok = g == WLAN_GATE_OPEN && try_read32(WLAN_DBI, &id);
		link_ok = g == WLAN_GATE_OPEN && try_read32(WLAN_ELBI + 0x2c8, &link);
		p = put_txt(line, sizeof(line), 0, "SUMMARY gate=");
		p = put_txt(line, sizeof(line), p, wlan_gate_name(g));
		p = put_txt(line, sizeof(line), p, " en=");
		/* Re-read the pins (no static state). */
		en_ok = try_read32(WLAN_GPP14 + 4, &en);
		p = en_ok ? put_hex(line, sizeof(line), p, wlan_gpio_level(en, 2), 1)
			   : put_txt(line, sizeof(line), p, "?");
		p = put_txt(line, sizeof(line), p, " perst=");
		perst_ok = try_read32(WLAN_GPH2 + 4, &perst);
		p = perst_ok ? put_hex(line, sizeof(line), p, wlan_gpio_level(perst, 0), 1)
			     : put_txt(line, sizeof(line), p, "?");
		p = put_txt(line, sizeof(line), p, " dbi_id=");
		p = id_ok ? put_hex(line, sizeof(line), p, id, 8)
			  : put_txt(line, sizeof(line), p, "unreadable");
		p = put_txt(line, sizeof(line), p, " link=");
		p = link_ok ? put_hex(line, sizeof(line), p, link & 0x3fu, 2)
			    : put_txt(line, sizeof(line), p, "?");
		emit(line, p);
		return;
	}
	if (g != WLAN_GATE_OPEN)
		return;
	switch (group) {
	case 0:
		rd_words("SYSREG", WLAN_SYSREG, 0x00, 0x1c);
		break;
	case 1:
		rd_words("ELBI", WLAN_ELBI, 0x00, 0x40);
		rd_off("ELBI", WLAN_ELBI, 0x54, &v);		/* APP_LTSSM_ENABLE */
		rd_off("ELBI", WLAN_ELBI, 0x80, &v);		/* DEVICE_TYPE of the sub controller */
		rd_off("ELBI", WLAN_ELBI, 0x2c8, &v);		/* RDLH_LINKUP */
		rd_off("ELBI", WLAN_ELBI, 0x2e8, &v);		/* PM_DSTATE */
		rd_off("ELBI", WLAN_ELBI, 0x3a4, &v);		/* SOFT_RESET */
		break;
	case 2:
		rd_off("DBI", WLAN_DBI, 0x00, &v);
		rd_off("DBI", WLAN_DBI, 0x04, &v);
		rd_off("DBI", WLAN_DBI, 0x08, &v);
		rd_off("DBI", WLAN_DBI, 0x0c, &v);
		break;
	case 3:
		pll_ok = rd_off("PMA", WLAN_PMA, 0x3f0, &pll);	/* PLL lock bit 3 */
		cdr_ok = rd_off("PMA", WLAN_PMA, 0xe0c, &cdr);	/* CDR lock bit 2 */
		oc_ok = rd_off("PMA", WLAN_PMA, 0xde8, &oc);	/* offset calibration done: & 0xF == 0xF */
		rd_off("PMA", WLAN_PMA, 0x5fc, &v);		/* OC initial status */
		ocr_ok = rd_off("PMA", WLAN_PMA, 0xe18, &ocr);	/* OC ok after soft power reset: bit 7 */
		p = put_txt(line, sizeof(line), 0, "PHY pll_lock=");
		p = pll_ok ? put_hex(line, sizeof(line), p, (pll >> 3) & 1u, 1) : put_txt(line, sizeof(line), p, "?");
		p = put_txt(line, sizeof(line), p, " cdr_lock=");
		p = cdr_ok ? put_hex(line, sizeof(line), p, (cdr >> 2) & 1u, 1) : put_txt(line, sizeof(line), p, "?");
		p = put_txt(line, sizeof(line), p, " oc_done=");
		p = oc_ok ? put_hex(line, sizeof(line), p, (oc & 0xfu) == 0xfu, 1) : put_txt(line, sizeof(line), p, "?");
		p = put_txt(line, sizeof(line), p, " oc_after_rst=");
		p = ocr_ok ? put_hex(line, sizeof(line), p, (ocr >> 7) & 1u, 1) : put_txt(line, sizeof(line), p, "?");
		emit(line, p);
		break;
	default:	/* groups 0..3 here and 4 above are WLAN_L2_GROUPS (wlanprobe.h:84): anything else is a bug, say so (AGENTS rule 8) */
		p = put_txt(line, sizeof(line), 0, "WIFI GAP: wlan_probe_l2_group: unknown group 0x");
		p = put_hex(line, sizeof(line), p, group, 1);
		emit(line, p);
		break;
	}
}

#else	/* not gs101 (or no WLAN addresses): empty stub */

void wlan_probe_l1(void) {}
int wlan_probe_l2_open(void) { return 0; }
void wlan_probe_l2_group(unsigned group) { (void)group; }

#endif
