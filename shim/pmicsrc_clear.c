/*
 * pmicsrc_clear.c - the ONLY PMIC write of build.sh --pmic-src-clear (docs/research/142, ALL-ON
 * T-02/T-24; compiled and linked ONLY with that flag): write 0 to the three reset-source latches,
 * in Android's order, then read each back once. Freestanding, no static state, no MMIO of its own
 * (no try_read32, no try_write32, no SMC): the hardware access is acpm_pmic_src_clear (3-entry
 * allowlist, value 0 only) and acpm_pmic_src_read (6-entry read allowlist), nothing else.
 * Order (research 142 section 1): S2MPG10 OFFSRC 0xF (google_bcl.c:3462), S2MPG10 PWRONSRC 0xE
 * (:3463), S2MPG11 OFFSRC 0xD (google_bcl_core.c:1155); the read-backs in the same order.
 * pmic_src_clear() is called by pmic_src() only after every ACPM gate passed and all six reads
 * succeeded. The first failure (a refused or failed write, a failed read-back) stops the
 * sequence: no retry, no later step. Result code: see pmicsrc.h (sweetpsclr).
 */
#include <stdint.h>
#include "acpm.h"
#include "pmicsrc.h"

#if SOC_GS101 && defined(PMIC_SRC_CLEAR)

#define NCLR 3u

static uint32_t clr_chip(uint32_t k)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	return k < 2 ? ACPM_SRC_S10 : ACPM_SRC_S11;
}

static uint32_t clr_reg(uint32_t k)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	switch (k) {
	case 0: return 0x0F;	/* S2MPG10_PM_OFFSRC */
	case 1: return 0x0E;	/* S2MPG10_PM_PWRONSRC */
	default: return 0x0D;	/* S2MPG11_PM_OFFSRC */
	}
}

uint32_t pmic_src_clear(const struct acpm_cfg *c, struct acpm_trace *t, uint32_t *rb, uint32_t *nrb)
{
	/* style-allow: assert audited undefined-symbol set, see STYLE-C.md */
	uint32_t k;
	uint32_t v = 0;

	/* the read-backs, first one in the highest byte of the used part: no indexed store (the
	 * object audit allows only stack / argument stores) */
	*rb = 0;
	*nrb = 0;
	for (k = 0; k < NCLR; k++)
		if (acpm_pmic_src_clear(c, clr_chip(k), clr_reg(k), 0, t))
			return PMIC_CLR_WRITE + k;
	for (k = 0; k < NCLR; k++) {
		if (acpm_pmic_src_read(c, clr_chip(k), clr_reg(k), &v, t))
			return PMIC_CLR_READ + k;
		*rb = (*rb << 8) | (v & 0xffu);
		*nrb = k + 1;
	}
	/* the PWRONSRC read-back (step 1) is a value to REPORT, not an error: it holds the live
	 * power-on reason of this boot (hardware run resident-045835-b1: 0x04 after the write 0) */
	for (k = 0; k < NCLR; k++)
		if (k != 1 && ((*rb >> (8 * (NCLR - 1 - k))) & 0xffu) != 0)
			return PMIC_CLR_NONZERO + k;
	return 0;
}

#endif
