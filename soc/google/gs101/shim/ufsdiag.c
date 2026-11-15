/*
 * ufsdiag.c - read-only survey of the Pixel 6a UFS host after ABL's shutdown.
 * Register map and meanings: soc/google/gs101/docs/07-gs101-ufs-bringup.md (Linux
 * ufs-exynos.c, phy-gs101-ufs.c, clk-gs101.c, gs101.dtsi). Purpose: learn
 * which clocks/isolation ABL left on and whether the UniPro link is still
 * alive BEFORE writing the cold re-init, so the first writes are informed.
 */
#include <stdint.h>
#include "console.h"
#include "ufsdiag.h"

int try_read32(uintptr_t address, uint32_t *value);	/* shim.c, fault guarded */

#define HCI		0x14700000UL
#define VS_HCI		0x14701100UL
#define UNIPRO		0x14780000UL
#define PHY_PMA		0x14704000UL
#define SYSREG_HSI2	0x14420000UL
#define CMU_HSI2	0x14400000UL
#define CMU_TOP		0x1E080000UL
#define PMU		0x17460000UL

static int g_faults;
#define HCI_RD(o) (*(volatile uint32_t *)(HCI + (o)))

/* One guarded read, printed as "label pa=.. v=..". A fault is printed and counted. */
static void rd(const char *label, uint64_t pa)
{
	uint32_t v;
	if (!try_read32((uintptr_t)pa, &v)) {
		con_hex2(label, "FAULT pa=", (uint32_t)pa, "", 0);
		g_faults++;
		return;
	}
	con_hex2(label, "pa=", (uint32_t)pa, "v=", v);
}

static uint64_t now_ticks(void)
{
	uint64_t t;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(t));
	return t;
}

/* UIC DME_GET (UICCMD 0x01), polled; returns the UIC result (0 = success) or
 * 0xFFFF on timeout. Appendix C.1 of the spec. */
static uint32_t dme_op(uint32_t op, uint32_t attr, uint32_t sel, uint32_t *val)
{
	volatile uint32_t *hci = (volatile uint32_t *)HCI;
	uint64_t t0 = now_ticks(), lim = counter_freq() / 2;	/* 500 ms */

	/* bounded: now_ticks() - t0 > lim (500 ms) returns 0xFFFF below */
	while (!(hci[0x30 / 4] & 0x8)) {			/* HCS.UCRDY */
		if (now_ticks() - t0 > lim)
			return 0xFFFF;
	}
	hci[0x94 / 4] = (attr << 16) | sel;			/* UCMDARG1 */
	hci[0x98 / 4] = 0;					/* UCMDARG2 */
	hci[0x9C / 4] = 0;					/* UCMDARG3 */
	hci[0x20 / 4] = 0x400;					/* W1C stale UCCS */
	__asm__ volatile("dsb sy" ::: "memory");
	hci[0x90 / 4] = op;					/* DME_GET */
	t0 = now_ticks();
	/* bounded: now_ticks() - t0 > lim (500 ms) returns 0xFFFF below */
	while (!(hci[0x20 / 4] & 0x400)) {
		if (now_ticks() - t0 > lim)
			return 0xFFFF;
	}
	uint32_t res = hci[0x98 / 4] & 0xFF;
	*val = hci[0x9C / 4];
	hci[0x20 / 4] = 0x400;
	return res;
}

static uint32_t dme_get(uint32_t attr, uint32_t sel, uint32_t *val) { return dme_op(0x01, attr, sel, val); }

static void get(const char *label, uint32_t attr, uint32_t sel)
{
	uint32_t v = 0, res = dme_get(attr, sel, &v);
	con_hex2(label, "res=", res, "val=", v);
}

void ufs_vendor_diag(void)
{
	uint32_t v;

	con_str("UFSDIAG", "read-only survey of the post-shutdown state");

	/* 1. Standard host registers (all readable earlier; HCS decoded). */
	if (try_read32(HCI + 0x30, &v))
		con_hex2("UFSDIAG HCS", "v=", v, "UPMCRS=", (v >> 8) & 7);
	rd("UFSDIAG HCE", HCI + 0x34);
	rd("UFSDIAG IS", HCI + 0x20);
	rd("UFSDIAG UECPA", HCI + 0x38);

	/* 2. CMU gates/muxes (always accessible). Spec step 1: HSI2 gates 0x20d0
	 * I_ACLK, 0x20d4 UNIPRO, 0x20d8 FMP, 0x2090/0x2094 QE; mux 0x0630; QCH
	 * 0x3090/0x3094/0x3068; TOP mux 0x10ac, div 0x18a4, gate 0x20cc. */
	rd("UFSDIAG HSI2 20d0", CMU_HSI2 + 0x20d0);
	rd("UFSDIAG HSI2 20d4", CMU_HSI2 + 0x20d4);
	rd("UFSDIAG HSI2 20d8", CMU_HSI2 + 0x20d8);
	rd("UFSDIAG HSI2 2090", CMU_HSI2 + 0x2090);
	rd("UFSDIAG HSI2 2094", CMU_HSI2 + 0x2094);
	rd("UFSDIAG HSI2 0630", CMU_HSI2 + 0x0630);
	rd("UFSDIAG HSI2 3090", CMU_HSI2 + 0x3090);
	rd("UFSDIAG HSI2 3094", CMU_HSI2 + 0x3094);
	rd("UFSDIAG HSI2 3068", CMU_HSI2 + 0x3068);
	rd("UFSDIAG TOP 10ac", CMU_TOP + 0x10ac);
	rd("UFSDIAG TOP 18a4", CMU_TOP + 0x18a4);
	rd("UFSDIAG TOP 20cc", CMU_TOP + 0x20cc);

	/* 3. PMU PHY isolation (spec step 2: 0x3ec8 bit0). Plain MMIO read. */
	rd("UFSDIAG PMU 3ec8", PMU + 0x3ec8);
	/* 4. sysreg_hsi2 shareability (0x710). */
	rd("UFSDIAG SYSREG 710", SYSREG_HSI2 + 0x710);

	/* 5. Vendor HCI (vs_hci @ 0x14701100): the regs the re-init programs. */
	static const uint16_t vs[] = { 0x00, 0x04, 0x0C, 0x40, 0x44, 0x50, 0x60, 0x68,
				       0x6C, 0x70, 0x78, 0x7C, 0x80, 0x84, 0x88, 0x8C,
				       0xB0, 0xB4, 0x100 };
	for (unsigned i = 0; i < sizeof vs / sizeof vs[0]; i++)
		rd("UFSDIAG VS", VS_HCI + vs[i]);

	/* 6. UniPro: COMP_CLK_PERIOD (0x44) encodes ABL's mclk: 16e9 / value;
	 * the power-mode L2 timers 0x7888.. show what ABL programmed. */
	rd("UFSDIAG UNIPRO 44", UNIPRO + 0x44);
	rd("UFSDIAG UNIPRO 7888", UNIPRO + 0x7888);
	rd("UFSDIAG UNIPRO 788C", UNIPRO + 0x788C);
	rd("UFSDIAG UNIPRO 7890", UNIPRO + 0x7890);
	rd("UFSDIAG UNIPRO 78B8", UNIPRO + 0x78B8);
	rd("UFSDIAG UNIPRO 78BC", UNIPRO + 0x78BC);
	rd("UFSDIAG UNIPRO 78C0", UNIPRO + 0x78C0);

	/* 7. UIC DME_GET: is the UniPro link/power state still alive?
	 * PA_AVAILTXDATALANES 0x1520, AVAILRX 0x1540, CONNECTEDTX 0x1561,
	 * CONNECTEDRX 0x1581, PA_PWRMODE 0x1571, TXGEAR 0x1568, RXGEAR 0x1583. */
	get("UFSDIAG DME AVAILTX", 0x1520, 0);
	get("UFSDIAG DME AVAILRX", 0x1540, 0);
	get("UFSDIAG DME CONNTX", 0x1561, 0);
	get("UFSDIAG DME CONNRX", 0x1581, 0);
	get("UFSDIAG DME PWRMODE", 0x1571, 0);
	get("UFSDIAG DME TXGEAR", 0x1568, 0);
	get("UFSDIAG DME RXGEAR", 0x1583, 0);
	get("UFSDIAG DME HSSERIES", 0x156A, 0);

	/* 7b. DME_PEER_GET (0x03): the DEVICE answers over the link (read-only attributes).
	 * PA_MAXRXHSGEAR 0x1587, PA_AVAILRXDATALANES 0x1540, PA_HSSERIES 0x156A. */
	{
		uint32_t pv = 0, pr;
		pr = dme_op(0x03, 0x1587, 0, &pv); con_hex2("UFSDIAG PEER MAXRXHSGEAR", "res=", pr, "val=", pv);
		pr = dme_op(0x03, 0x1540, 0, &pv); con_hex2("UFSDIAG PEER AVAILRX", "res=", pr, "val=", pv);
		pr = dme_op(0x03, 0x1520, 0, &pv); con_hex2("UFSDIAG PEER AVAILTX", "res=", pr, "val=", pv);
		con_hex2("UFSDIAG after peer", "IS=", HCI_RD(0x20), "UECDL=", HCI_RD(0x3c));
		con_hex2("UFSDIAG after peer", "UECN=", HCI_RD(0x40), "UECDME=", HCI_RD(0x48));
	}

	/* 8. LAST (a clock-gated or isolated PHY may hang the bus, which no
	 * fault guard can catch): the PHY PMA registers the re-init touches.
	 * COMN 0x43 -> pma + (0x43 << 2); TRSV lane0 0x338, lane1 0x538. */
	con_str("UFSDIAG", "PHY PMA reads (may hang if the PHY is gated)");
	rd("UFSDIAG PMA COMN43", PHY_PMA + (0x43 << 2));
	rd("UFSDIAG PMA TRSV338", PHY_PMA + (0x338 << 2));
	rd("UFSDIAG PMA TRSV538", PHY_PMA + (0x538 << 2));
	con_hex2("UFSDIAG", "done faults=", g_faults, "", 0);
}

