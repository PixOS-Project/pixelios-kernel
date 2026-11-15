/*
 * ufsinit.c - cold re-init of the Pixel 6a (gs101) UFS host after ABL's shutdown.
 * Every register, order and constant comes from soc/google/gs101/docs/07-gs101-ufs-bringup.md
 * (Linux ufs-exynos.c / ufshcd.c / phy-gs101-ufs.c at 72a16bc); step numbers refer to it.
 * Stops at "link up + post-link config"; the caller makes the host operational.
 * MMU is off (all Device memory), so plain volatile accesses are enough.
 */
#include <stdint.h>
#include "console.h"
#include "ufsinit.h"
#include "ufsphy_tables.h"

#define HCI	0x14700000UL
#define VS	0x14701100UL
#define UNIPRO	0x14780000UL
#define PMA	0x14704000UL
#define SYSREG	0x14420000UL
#define PMU	0x17460000UL

static inline uint32_t rd(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline void wr(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static uint64_t now(void)
{
	uint64_t t;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
	return t;
}

static void udelay(uint32_t us)
{
	uint64_t end = now() + (uint64_t)us * counter_freq() / 1000000;
	while (now() < end)
		;
}

/* Poll until (rd(a) & mask) == want; 0 on success, -1 on timeout. */
static int poll(uintptr_t a, uint32_t mask, uint32_t want, uint32_t timeout_us)
{
	uint64_t end = now() + (uint64_t)timeout_us * counter_freq() / 1000000;
	/* bounded: now() > end (timeout_us) returns -1 below */
	while ((rd(a) & mask) != want)
		if (now() > end)
			return -1;
	return 0;
}

/* Appendix C.1. Returns the UIC result (0 = success) or 0xFFFF on timeout; *val gets ARG3. */
static uint32_t uic(uint32_t op, uint32_t a1, uint32_t a3, uint32_t *val)
{
	if (poll(HCI + 0x30, 0x8, 0x8, 500000))
		return 0xFFFF;
	wr(HCI + 0x94, a1);
	wr(HCI + 0x98, 0);
	wr(HCI + 0x9C, a3);
	wr(HCI + 0x20, 0x400);
	__asm__ volatile("dsb sy" ::: "memory");
	wr(HCI + 0x90, op);
	if (poll(HCI + 0x20, 0x400, 0x400, 500000))
		return 0xFFFF;
	uint32_t r = rd(HCI + 0x98) & 0xFF;
	if (val)
		*val = rd(HCI + 0x9C);
	wr(HCI + 0x20, 0x400);
	return r;
}

static int fails;

static void dme_set(uint32_t attr, uint32_t sel, uint32_t v)
{
	uint32_t r = uic(0x02, attr << 16 | sel, v, 0);
	if (r) {
		con_hex2("UFSINIT dme_set FAIL", "attr=", attr, "res=", r);
		fails++;
	}
}

static uint32_t dme_get(uint32_t attr, uint32_t sel)
{
	uint32_t v = 0;
	uint32_t r = uic(0x01, attr << 16 | sel, 0, &v);
	if (r) {
		con_hex2("UFSINIT dme_get FAIL", "attr=", attr, "res=", r);
		fails++;
	}
	return v;
}

#define MIB_RX(lane)	(4 + (lane))	/* M-RX lane select = PA_MAXDATALANES + lane */

/* Steps 6-10: stop, host SW reset, device HW reset, HCE=1. */
static int host_enable(void)
{
	for (int tries = 0; tries < 3; tries++) {
		wr(HCI + 0x34, 0);
		poll(HCI + 0x34, 1, 0, 1000);
		uint32_t misc = rd(VS + 0xB4);
		wr(VS + 0xB4, misc & ~0x10u);
		wr(VS + 0x50, 0x3);
		if (poll(VS + 0x50, 0xFFFFFFFF, 0, 1000))
			con_str("UFSINIT", "SW_RST did not self-clear");
		wr(VS + 0xB4, misc);
		wr(VS + 0x70, 0);		/* device RST_n low */
		udelay(5);
		wr(VS + 0x70, 1);
		wr(HCI + 0x34, 1);
		udelay(1000);
		if (!poll(HCI + 0x34, 1, 1, 50000)) {
			wr(HCI + 0x24, rd(HCI + 0x24) | 0x470);
			wr(VS + 0xB4, rd(VS + 0xB4) | 0x10);		/* step 10 */
			return 0;
		}
	}
	return -1;
}

static void phy_write(const struct ufsphy_row *t, unsigned n)
{
	for (unsigned i = 0; i < n; i++) {
		wr(PMA + ((uint32_t)t[i].off << 2), t[i].val);
		if (t[i].trsv)
			wr(PMA + (((uint32_t)t[i].off + 0x200) << 2), t[i].val);
	}
}

static int prelink(uint32_t comp_abl)
{
	fails = 0;
	/* Step 11: error enables, ungate HCI clocks, UniPro attributes. */
	wr(VS + 0x7C, 0x80002020);
	wr(VS + 0x80, 0x80000007);
	wr(VS + 0x84, 0x80000017);
	wr(VS + 0xB0, rd(VS + 0xB0) & ~0x1Fu);
	wr(VS + 0xB4, rd(VS + 0xB4) & ~0xF0u);
	dme_set(0x1564, 0, 0xff);			/* PA_TXTRAILINGCLOCKS */
	dme_set(0x956a, 0, 0x90913C1C);
	dme_set(0x956d, 0, 0xE01C115F);

	/* Step 12: gs101 vendor attributes. mclk is recovered from ABL's COMP_CLK_PERIOD
	 * (= 16e9 / mclk), so the periods below match the clock ABL left running. */
	uint32_t comp = comp_abl;
	if (comp < 40 || comp > 320) {		/* mclk outside 50..400 MHz */
		con_hex2("UFSINIT", "COMP_CLK_PERIOD implausible=", comp, "", 0);
		return 2;
	}
	uint64_t mclk = 16000000000ULL / comp;
	uint32_t period = (uint32_t)((1000000000ULL + mclk - 1) / mclk);
	uint32_t rx_lr = (uint32_t)(1000ULL * mclk / 1000000);
	uint32_t tx_lr = (uint32_t)(3200ULL * mclk / 1000000);
	wr(UNIPRO + 0x44, comp);
	con_hex2("UFSINIT", "mclk=", (uint32_t)mclk, "period=", period);
	uint32_t lanes_rx = dme_get(0x1540, 0), lanes_tx = dme_get(0x1520, 0);	/* step 13a */
	con_hex2("UFSINIT", "lanes rx=", lanes_rx, "tx=", lanes_tx);
	if (lanes_rx != 2 || lanes_tx != 2)
		return 3;
	dme_set(0x200, 0, 0x40);
	for (uint32_t l = 0; l < lanes_rx; l++) {
		dme_set(0x12, MIB_RX(l), period);
		dme_set(0x11, MIB_RX(l), 0);
		dme_set(0x1B, MIB_RX(l), rx_lr >> 16 & 0xFF);
		dme_set(0x1C, MIB_RX(l), rx_lr >> 8 & 0xFF);
		dme_set(0x1D, MIB_RX(l), rx_lr & 0xFF);
		dme_set(0x2f, MIB_RX(l), 0x69);
		dme_set(0x84, MIB_RX(l), 0x1);
		dme_set(0x25, MIB_RX(l), 0xf6);
	}
	for (uint32_t l = 0; l < lanes_tx; l++) {
		dme_set(0xAA, l, period);
		dme_set(0xA9, l, 0x02);
		dme_set(0xAB, l, tx_lr >> 16 & 0xFF);
		dme_set(0xAC, l, tx_lr >> 8 & 0xFF);
		dme_set(0xAD, l, tx_lr & 0xFF);
		dme_set(0x04, l, 1);
		dme_set(0x7F, l, 0);
	}
	dme_set(0x200, 0, 0);
	dme_set(0x155E, 0, 0);
	dme_set(0x3000, 0, 0);
	dme_set(0x3001, 0, 1);
	dme_set(0x4021, 0, 1);
	dme_set(0x4020, 0, 1);
	dme_set(0xA006, 0, 0x8000);
	if (fails) {
		con_hex2("UFSINIT", "pre-link DME failures=", fails, "", 0);
		return 4;
	}

	/* Step 13: PHY. Isolation already off; run the pre-init table, wait for RX cal. */
	phy_write(ufsphy_pre_init_cfg, sizeof ufsphy_pre_init_cfg / sizeof ufsphy_pre_init_cfg[0]);
	for (uint32_t l = 0; l < lanes_rx; l++)
		if (poll(PMA + ((0x338 + l * 0x200) << 2), 0x8, 0x8, 40000)) {
			con_hex2("UFSINIT", "PHY RX cal timeout lane=", l, "", 0);
			return 5;
		}

	return 0;
}

/* Step 22: PWM-G1 -> HS-G4 Series B, 2 lanes (docs/research/07, Appendix C.2). Returns 0 or a
 * stage number; on failure the link may be unusable, so the caller reports it and carries on. */
#ifndef UFS_HS
#define UFS_HS 1
#endif
static int power_mode_hs(void)
{
	uint32_t peer = 0;
	if (uic(0x03, 0x1587u << 16, 0, &peer))		/* PA_MAXRXHSGEAR of the device */
		return 1;
	fails = 0;
	uint32_t rxg = dme_get(0x1587, 0), g_rx = rxg < 4 ? rxg : 4, g_tx = peer < 4 ? peer : 4;
	if (fails)
		return 2;
	con_hex2("UFSINIT hs gears", "dev max rx=", peer, "host max rx=", rxg);
	if (!g_rx || !g_tx)
		return 2;
	fails = 0;
	phy_write(ufsphy_pre_pwr_hs_config, sizeof ufsphy_pre_pwr_hs_config / sizeof ufsphy_pre_pwr_hs_config[0]);
	dme_set(0x2041, 0, 8064);
	dme_set(0x2042, 0, 28224);
	dme_set(0x2043, 0, 20160);
	dme_set(0x15B0, 0, 12000);
	dme_set(0x15B1, 0, 32000);
	dme_set(0x15B2, 0, 16000);
	wr(UNIPRO + 0x7888, 8064);
	wr(UNIPRO + 0x788C, 28224);
	wr(UNIPRO + 0x7890, 20160);
	wr(UNIPRO + 0x78B8, 12000);
	wr(UNIPRO + 0x78BC, 32000);
	wr(UNIPRO + 0x78C0, 16000);
	dme_set(0x1583, 0, g_rx);
	dme_set(0x1580, 0, 2);
	dme_set(0x1584, 0, 1);
	dme_set(0x1568, 0, g_tx);
	dme_set(0x1560, 0, 2);
	dme_set(0x1569, 0, 1);
	dme_set(0x156A, 0, 2);
	if (fails)
		return 3;
	wr(HCI + 0x20, 0x10);
	dme_set(0x1571, 0, 0x11);
	if (fails)
		return 4;
	if (poll(HCI + 0x20, 0x10, 0x10, 500000))
		return 5;
	wr(HCI + 0x20, 0x10);
	uint32_t upmcrs = rd(HCI + 0x30) >> 8 & 7;
	if (upmcrs != 1) {
		con_hex2("UFSINIT", "UPMCRS=", upmcrs, "", 0);
		return 6;
	}
	phy_write(ufsphy_post_pwr_hs_config, sizeof ufsphy_post_pwr_hs_config / sizeof ufsphy_post_pwr_hs_config[0]);
	for (uint32_t l = 0; l < 2; l++) {
		uintptr_t base = l * 0x200;
		int ok = 0;
		for (int i = 0; i < 100 && !ok; i++) {
			udelay(40);
			if (!poll(PMA + ((0x339 + base) << 2), 0x8, 0x8, 40))
				ok = 1;
			else {
				wr(PMA + ((0x222 + base) << 2), 0x10);
				wr(PMA + ((0x222 + base) << 2), 0x18);
			}
		}
		if (!ok) {
			con_hex2("UFSINIT", "CDR lock timeout lane=", l, "", 0);
			return 7;
		}
	}
	con_hex2("UFSINIT hs ok", "gear rx=", g_rx, "tx=", g_tx);
	return 0;
}

int ufs_cold_init(void)
{
	con_str("UFSINIT", "cold re-init start");
	/* Steps 2-4: PHY isolation off, shareability, ACG under UFS_ACG_DISABLE. */
	/* Only write when a bit is missing: the survey showed ABL left all three as needed
	 * and a PMU write may be blocked by EL3 (spec Q3). */
	uint32_t comp_abl = rd(UNIPRO + 0x44);	/* the SW reset reverts it to its default */
	uint32_t pmu = rd(PMU + 0x3ec8), shr = rd(SYSREG + 0x710), acg = rd(VS + 0x100);
	con_hex2("UFSINIT", "pmu=", pmu, "sysreg=", shr);
	if (!(pmu & 1))
		wr(PMU + 0x3ec8, pmu | 1);
	if ((shr & 3) != 3)
		wr(SYSREG + 0x710, shr | 3);
	if (acg & 1)
		wr(VS + 0x100, acg & ~1u);
	con_str("UFSINIT", "pre-HCE ok");

	if (host_enable()) {
		con_str("UFSINIT", "HCE did not come up");
		return 1;
	}
	con_hex2("UFSINIT", "hce ok hcs=", rd(HCI + 0x30), "is=", rd(HCI + 0x20));

	/* Steps 11-14 are re-run together on every link startup retry, as Linux does
	 * (the host reset in host_enable() reverts the pre-link attributes). */
	int up = 0;
	for (int t = 0; t < 3 && !up; t++) {
		if (t && host_enable())
			return 6;
		int rc = prelink(comp_abl);
		if (rc)
			return rc;
		uint32_t r = uic(0x16, 0, 0, 0);
		uint32_t hcs = rd(HCI + 0x30);
		con_hex2("UFSINIT linkstartup", "res=", r, "hcs=", hcs);
		up = r == 0 && (hcs & 1);
	}
	if (!up)
		return 7;

	/* Step 15: both directions must have 2 connected lanes. */
	if (dme_get(0x1561, 0) != 2 || dme_get(0x1581, 0) != 2) {
		con_str("UFSINIT", "connected lanes != 2");
		return 8;
	}

	/* Step 16: post-link. */
	dme_set(0x4020, 0, 0);
	dme_set(0x3000, 0, 0);
	dme_set(0x3001, 0, 1);
	dme_set(0x4021, 0, 1);
	dme_set(0x4022, 0, 0);
	dme_set(0x4025, 0, 0x6);
	dme_set(0x4023, 0, 0);
	dme_set(0x4020, 0, 1);
	wr(VS + 0x8C, rd(VS + 0x8C) | 1u << 16);
	/* HCI_1US_TO_CNT_VAL (0x0C): ABL's value (0xB2) is kept; recomputing needs the APB clock rate. */
	wr(VS + 0x60, 0xa);
	wr(VS + 0x00, 12);
	wr(VS + 0x04, 12);
	/* ABL ran with 0xFFFFFFFE: slot 0 = device-management nexus (NOP/query), the rest SCSI.
	 * Linux's (1 << nutrs) - 1 is 0 on arm64 for nutrs=32, so the spec value is not usable. */
	wr(VS + 0x40, 0xFFFFFFFEu);
	wr(VS + 0x44, 0xF);
	wr(VS + 0x6C, 0xf);
	dme_set(0x9529, 0, 1);
	dme_set(0x15AA, 0, 6);
	dme_set(0x9529, 0, 0);
	dme_set(0x15A8, 0, 3);
	dme_set(0x15A7, 0, 0x20);
	wr(VS + 0x6C, 0x98000003);
	dme_set(0x9529, 0, 1);
	dme_set(0x15A4, 0, 0x3e8);
	dme_set(0x9529, 0, 0);
	(void)rd(HCI + 0x38);			/* step 17: clear UECPA */
	wr(HCI + 0x20, rd(HCI + 0x20));
	con_hex2("UFSINIT", "post-link hcs=", rd(HCI + 0x30), "fails=", fails);
	if (fails)
		return 9;
#if UFS_HS
	int hs = power_mode_hs();
	if (hs) {
		con_hex2("UFSINIT", "HS-G4 change FAILED stage=", hs, "hcs=", rd(HCI + 0x30));
		return 10 + hs;		/* link state unknown: fail closed, do not run on a half-switched link */
	}
#endif
	return 0;
}

