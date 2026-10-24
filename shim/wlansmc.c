/*
 * wlansmc.c - W2a: two SMC_CMD_PRIV_REG READs of PMU alive registers, see wlansmc.h. Freestanding, no static state.
 *
 * [src] = Google android-gs-bluejay-6.1-android16 and Linux, local copies in ~/pixelios-artifacts/refs/wlan-stage1/:
 *  - SMC_CMD_PRIV_REG = 0x82000504 (exynos-smc.h:45); an SMCCC fast call, SMC32, owner SIP (bit31 = 1, bit30 = 0, owner 2: linux-arm-smccc.h:20-23,48).
 *  - PRIV_REG_OPTION_READ/WRITE/RMW = 0/1/2 (exynos-el3_mon.h:21-23); get_priv_reg(): arm_smccc_smc(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_READ, 0, 0, 0, 0, 0, &res), return (int)res.a0
 *    (exynos-pd_el3.c:54-63); set_priv_reg / rmw_priv_reg are the same call with options 1 / 2 (:41-52, :66-77).
 *  - Errors: mainline tensor_sec_reg_write/rmw: "returns -EINVAL if access isn't allowed or 0" (exynos-pmu.c:65,82); an unknown function id returns SMCCC_RET_NOT_SUPPORTED = -1
 *    (linux-arm-smccc.h:247-249). "only registers that can be written are readable via SMC" (exynos-pmu.c:91-92, a remark about mainline's EL3): *guess* that a successful READ of 0x17463EC4 here means it is writable (a0 = 0 is also ambiguous).
 *  - Conduit: the shim runs at EL1 (start.S drops from EL2, HCR_EL2 = RW only so SMC is not trapped to EL2, start.S:43-44) and already issues `smc #0` for PSCI (shim.c:608-634, PSCI_VERSION answered:
 *    observed) and for this same function id with option 1 (WRITE) for SYSIP_DAT0 / CPU_INFORM (shim.c:219: SYSIP_DAT0 option 1, observed `SMC ret 0 after 0xFC`, docs/archive/HANDOFF-s01-s06.md:374,382; the CPU_INFORM clear at shim.c:299 has no cited result). DT psci method is SMC (soc.json notes.psci).
 *  - W2c (--wlan-iso, the WLAN_ISO_RUN block below): what Linux does. exynos_pcie_phy_isolation(val) = rmw_priv_reg(pmu_alive_pa + pmu_offset (0x3EC4), PCIE_PHY_CONTROL_MASK (1), val), falling back to a plain
 *    regmap write if the SMC returned nonzero (pcie-exynos-rc.c:393-407; rc.h:120; common.h:51-52 PCIE_PHY_ISOLATION = 0, PCIE_PHY_BYPASS = 1). So bit 0 = 1 = isolation BYPASSED = released (the isolation cells between the PHY domain and the core are bypassed; the PHY supplies are the LDO rails, observed on in run w2rails, not this bit: *guess* on what the cells do), 0 = isolated (the probe gate's
 *    reading). rmw_priv_reg = arm_smccc_smc(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_RMW (2), mask, val, 0, 0, 0, &res), return (int)res.a0 (exynos-pd_el3.c:66-77): x0 = fid, x1 = reg, x2 = 2, x3 = mask, x4 = val
 *    (arm_smccc_smc copies a0..a7 to x0..x7, linux-arm-smccc.h:380-390). When: the FIRST thing exynos_pcie_rc_poweron() does for state LINK_DOWN (rc.c:3387-3389), before clocks, pinctrl, phy_all_pwrdn_clear and phy_config
 *    (:3395-3425) and with NO delay in between (no udelay/msleep in :3387-3425). The way back is the same call with val 0 as the LAST step of rc_poweroff (rc.c:3667-3669, after PERST low, LTSSM off, phy_all_pwrdn).
 *    The wait for the bit below is OUR bound (10 polls x 100 us), not Linux's. The EL3 semantics new = (old & ~mask) | (val & mask) are a *guess*; the read-back guard checks them.
 *  - The two registers: PMU_PCIE1_ISO = PMU + pmu-offset 0x3EC4 of pcie_1, PMU_PCIE0_ISO = + 0x3EC0 of pcie_0 (gs101-pcie.dtsi:48,91); plain MMIO reads of both returned 0 (run w1).
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "wlansmc.h"

#if SOC_GS101 && defined(WLAN_SMC_READ) && defined(WLAN_PMU_PCIE1_ISO) && defined(WLAN_PMU_PCIE0_ISO)

#define SMC_FID_PRIV_REG	0x82000504UL
#define PRIV_REG_OPTION_READ	0UL

struct smc_res {
	uint64_t a0, a1, a2, a3;
};

#ifndef __aarch64__
/* host test: the fake EL3 */
void wlan_host_smc(uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3, struct smc_res *r);
#ifdef WLAN_ISO_RUN
void wlan_host_smc5(uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3, uint64_t x4, struct smc_res *r);
#endif	/* WLAN_ISO_RUN */
#endif

/* The ONLY smc instruction. noipa: the address check and the constants stay inside this symbol (the object audit reads them). Returns 0 = the SMC was issued, -1 = refused, no SMC. */
__attribute__((noipa)) static int smc_priv_read(uint64_t pa, struct smc_res *r)
{
	if (pa != WLAN_PMU_PCIE1_ISO && pa != WLAN_PMU_PCIE0_ISO)
		return -1;
#ifdef __aarch64__
	register uint64_t x0 __asm__("x0") = SMC_FID_PRIV_REG;
	register uint64_t x1 __asm__("x1") = pa;
	register uint64_t x2 __asm__("x2") = PRIV_REG_OPTION_READ;
	register uint64_t x3 __asm__("x3") = 0;

	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
			 :
			 : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	r->a0 = x0;
	r->a1 = x1;
	r->a2 = x2;
	r->a3 = x3;
#else
	wlan_host_smc(SMC_FID_PRIV_REG, pa, PRIV_REG_OPTION_READ, 0, r);
#endif
	return 0;
}

__attribute__((noipa)) static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++) {
		unsigned d = (unsigned)((v >> (4 * (digits - 1 - i))) & 0xfu);

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("WLAN", buf);
}

/* one address: SMC READ + MMIO compare; returns 0 same, 1 differs, 2 error */
static int one(uint64_t pa, uint32_t *mmio_before, int *amb)
{
	char line[RAMLOG_LINE_MAX];
	struct smc_res r = { 0, 0, 0, 0 };
	uint32_t after = 0;
	int mm_ok = try_read32(pa, mmio_before), rc, res;
	unsigned p;

	rc = smc_priv_read(pa, &r);
	p = put_txt(line, sizeof(line), 0, "SMC read pa=");
	p = put_hex(line, sizeof(line), p, pa, 8);
	if (rc) {
		p = put_txt(line, sizeof(line), p, " REFUSED by the shim, no SMC issued");
		emit(line, p);
		return 2;
	}
	p = put_txt(line, sizeof(line), p, " ret=");
	p = put_hex(line, sizeof(line), p, (uint32_t)r.a0, 8);
	p = put_txt(line, sizeof(line), p, " a0=");
	p = put_hex(line, sizeof(line), p, r.a0, 16);
	emit(line, p);
	p = put_txt(line, sizeof(line), 0, "SMC read a1=");
	p = put_hex(line, sizeof(line), p, r.a1, 16);
	p = put_txt(line, sizeof(line), p, " a2=");
	p = put_hex(line, sizeof(line), p, r.a2, 16);
	p = put_txt(line, sizeof(line), p, " a3=");
	p = put_hex(line, sizeof(line), p, r.a3, 16);
	emit(line, p);
	if (!mm_ok || (int32_t)(uint32_t)r.a0 < 0)
		res = 2;
	else
		res = (uint32_t)r.a0 == *mmio_before ? 0 : 1;
	*amb = mm_ok && (uint32_t)r.a0 == 0 && *mmio_before == 0;
	p = put_txt(line, sizeof(line), 0, "SMC cmp pa=");
	p = put_hex(line, sizeof(line), p, pa, 8);
	p = put_txt(line, sizeof(line), p, mm_ok ? " mmio=" : " mmio unreadable");
	if (mm_ok)
		p = put_hex(line, sizeof(line), p, *mmio_before, 8);
	p = put_txt(line, sizeof(line), p, res == 0 ? " same=yes" : res == 1 ? " same=no" : " same=error");
	emit(line, p);
	if (try_read32(pa, &after)) {
		p = put_txt(line, sizeof(line), 0, "SMC mmio after pa=");
		p = put_hex(line, sizeof(line), p, pa, 8);
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, after, 8);
		if (mm_ok && after != *mmio_before) {
			p = put_txt(line, sizeof(line), p, " !!! a READ changed the register");
			res = 2;
		}
		emit(line, p);
	}
	return res;
}

int wlan_smc_read(void)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t m1 = 0, m0 = 0;
	int amb1 = 0, amb0 = 0, r1, r0, res;
	unsigned p;

	r1 = one(WLAN_PMU_PCIE1_ISO, &m1, &amb1);
	r0 = one(WLAN_PMU_PCIE0_ISO, &m0, &amb0);
	res = (r1 == 2 || r0 == 2) ? 2 : (r1 == 0 && r0 == 0) ? 0 : 1;
	p = put_txt(line, sizeof(line), 0, "SMC summary same=");
	p = put_txt(line, sizeof(line), p, res == 0 ? "yes" : res == 1 ? "no" : "error");
	p = put_txt(line, sizeof(line), p, " ambiguous=");
	p = put_txt(line, sizeof(line), p, (amb1 || amb0) ? "1" : "0");
	emit(line, p);
	return res;
}

#ifdef WLAN_ISO_RUN
#define PRIV_REG_OPTION_RMW	2UL
#define ISO_REG			WLAN_PMU_PCIE1_ISO
#define ISO_MASK		1UL
#define ISO_POLLS		10u
#define ISO_POLL_DIV		10000u	/* 100 us per poll */
#define ISO_FINAL_DIV		100u	/* the final look comes 10 ms after the clear */

/* The two RMW SMCs (x5..x7 = 0 like Linux's arm_smccc_smc(.., 0, 0, 0), review F7). Constants only: no parameter can carry the function id, the register, the option, the mask or the value. noipa: they stay separate symbols (the object audit reads them). */
__attribute__((noipa)) static void smc_priv_rmw_set(struct smc_res *r)
{
#ifdef __aarch64__
	register uint64_t x0 __asm__("x0") = SMC_FID_PRIV_REG;
	register uint64_t x1 __asm__("x1") = ISO_REG;
	register uint64_t x2 __asm__("x2") = PRIV_REG_OPTION_RMW;
	register uint64_t x3 __asm__("x3") = ISO_MASK;
	register uint64_t x4 __asm__("x4") = 1UL;
	register uint64_t x5 __asm__("x5") = 0;
	register uint64_t x6 __asm__("x6") = 0;
	register uint64_t x7 __asm__("x7") = 0;

	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
			 :
			 : "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	r->a0 = x0;
	r->a1 = x1;
	r->a2 = x2;
	r->a3 = x3;
#else
	wlan_host_smc5(SMC_FID_PRIV_REG, ISO_REG, PRIV_REG_OPTION_RMW, ISO_MASK, 1UL, r);
#endif
}

__attribute__((noipa)) static void smc_priv_rmw_clear(struct smc_res *r)
{
#ifdef __aarch64__
	register uint64_t x0 __asm__("x0") = SMC_FID_PRIV_REG;
	register uint64_t x1 __asm__("x1") = ISO_REG;
	register uint64_t x2 __asm__("x2") = PRIV_REG_OPTION_RMW;
	register uint64_t x3 __asm__("x3") = ISO_MASK;
	register uint64_t x4 __asm__("x4") = 0UL;
	register uint64_t x5 __asm__("x5") = 0;
	register uint64_t x6 __asm__("x6") = 0;
	register uint64_t x7 __asm__("x7") = 0;

	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
			 :
			 : "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	r->a0 = x0;
	r->a1 = x1;
	r->a2 = x2;
	r->a3 = x3;
#else
	wlan_host_smc5(SMC_FID_PRIV_REG, ISO_REG, PRIV_REG_OPTION_RMW, ISO_MASK, 0UL, r);
#endif
}

/* bounded wait (<= ISO_POLLS reads, 100 us apart) for bit 0 of the isolation word to read `want`; 1 = reached; *v / *rok = the last read */
static int poll_bit(uint32_t want, uint32_t *v, int *rok)
{
	for (unsigned i = 0; i < ISO_POLLS; i++) {
		*rok = try_read32(ISO_REG, v);
		if (*rok && (*v & 1u) == want)
			return 1;
		if (i + 1 < ISO_POLLS)
			wait_ticks(counter_freq() / ISO_POLL_DIV);
	}
	return 0;
}

#ifndef WLAN_LINK_RUN	/* the --wlan-iso procedure; a --wlan-link image has the two steps below instead */
static void iso_line(const char *what, uint32_t v1, uint32_t v2)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "ISO ");

	p = put_txt(line, sizeof(line), p, what);
	p = put_txt(line, sizeof(line), p, " ISO1=");
	p = put_hex(line, sizeof(line), p, v1, 8);
	p = put_txt(line, sizeof(line), p, " ISO0=");
	p = put_hex(line, sizeof(line), p, v2, 8);
	emit(line, p);
}

static void iso_ret(const char *what, const struct smc_res *r)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "ISO ");

	p = put_txt(line, sizeof(line), p, what);
	p = put_txt(line, sizeof(line), p, " ret=");
	p = put_hex(line, sizeof(line), p, (uint32_t)r->a0, 8);
	p = put_txt(line, sizeof(line), p, " a1=");
	p = put_hex(line, sizeof(line), p, r->a1, 16);
	emit(line, p);
}

static void say_iso_refused(const char *why)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, why[0] == '!' ? "ISO " : "ISO refused: ");

	p = put_txt(line, sizeof(line), p, why);
	emit(line, p);
}

static void iso_summary(const char *rel, int rb_ok, uint32_t rb, const char *rest)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "ISO release=");

	p = put_txt(line, sizeof(line), p, rel);
	p = put_txt(line, sizeof(line), p, " readback=");
	if (rb_ok) {
		p = put_txt(line, sizeof(line), p, "0x");
		p = put_hex(line, sizeof(line), p, rb, 8);
	} else {
		p = put_txt(line, sizeof(line), p, "n/a");
	}
	p = put_txt(line, sizeof(line), p, " restore=");
	p = put_txt(line, sizeof(line), p, rest);
	emit(line, p);
}

int wlan_iso_run(void)
{
	uint32_t before = 0, iso0 = 0, iso0b = 0, perst = 0, en = 0, after = 0, rb = 0, fin = 0;
	struct smc_res rd = { 0, 0, 0, 0 }, rs = { 0, 0, 0, 0 }, rc = { 0, 0, 0, 0 };
	int rok = 0, rok2 = 0, fin_ok, reached, ret, rel_ok;
	const char *rel, *rest = "n/a";

	if (!counter_freq()) {
		say_iso_refused("no timer, nothing sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	if (!try_read32(ISO_REG, &before) || !try_read32(WLAN_PMU_PCIE0_ISO, &iso0) || !try_read32(WLAN_GPH2 + 4u, &perst) || !try_read32(WLAN_GPP14 + 4u, &en)) {
		say_iso_refused("PMU / GPH2 / GPP14 words unreadable, nothing sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	iso_line("before", before, iso0);
	if (before & 1u) {
		say_iso_refused("PHY isolation already released (bit 0 set), nothing sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	if (perst & (1u << (unsigned)WLAN_GPH2_PERST_PIN)) {
		say_iso_refused("PERST gph2-0 reads high, nothing sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	if (en & (1u << (unsigned)WLAN_GPP14_EN_PIN)) {
		say_iso_refused("WLAN_EN gpp14-2 reads high, nothing sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	if (smc_priv_read(ISO_REG, &rd) || (int32_t)(uint32_t)rd.a0 < 0) {
		say_iso_refused("the SMC READ of the isolation word was refused (a0 negative), no RMW sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}
	if ((uint32_t)rd.a0 != before) {
		say_iso_refused("the SMC READ value differs from the MMIO word, no RMW sent");
		iso_summary("refused", 0, 0u, "n/a");
		return 2;
	}

	/* the release: ONE RMW (mask 1, value 1) */
	smc_priv_rmw_set(&rs);
	iso_ret("rmw set", &rs);
	ret = (int32_t)(uint32_t)rs.a0;
	reached = poll_bit(1u, &after, &rok);
	(void)try_read32(WLAN_PMU_PCIE0_ISO, &iso0b);
	iso_line("after set", rok ? after : 0u, iso0b);
	rel_ok = rok && reached && ret == 0 && ((after ^ before) & ~1u) == 0u && iso0b == iso0;
	if (!rok)
		rel = "fault";
	else if (rel_ok)
		rel = "ok";
	else if (!reached && ((after ^ before) & ~1u) == 0u && iso0b == iso0 && ret != 0)
		rel = "refused";
	else
		rel = "mismatch";
	/* the ONE clear, after EVERY set that was sent (review F1: an EL3 that applies the set late or silently would otherwise leave the isolation released); never a second set */
	smc_priv_rmw_clear(&rc);
	iso_ret("rmw clear", &rc);
	(void)poll_bit(0u, &rb, &rok2);
	iso_line("after clear", rok2 ? rb : 0u, iso0b);
	wait_ticks(counter_freq() / ISO_FINAL_DIV);	/* ~10 ms, then one more look: a late apply must have settled */
	fin_ok = try_read32(ISO_REG, &fin);
	iso_line("final", fin_ok ? fin : 0u, iso0b);
	rest = (rok2 && !(rb & 1u) && fin_ok && !(fin & 1u)) ? "ok" : "FAILED";
	if (rest[0] == 'F') {
		say_iso_refused("!!! RESTORE FAILED: PMU 0x17463EC4 bit 0 may be LEFT SET");
		say_iso_refused("!!! the PHY isolation stays released; reset the phone (guess: lost on a cold power-off)");
	}
	iso_summary(rel, rok, after, rest);
	return rel_ok && rest[0] != 'F' ? 0 : 1;
}
#endif	/* !WLAN_LINK_RUN */

#ifdef WLAN_LINK_RUN
/* --wlan-link (wlanlink.c, docs/research/29 section 14): the same two RMW calls as separate, individually reported steps of the long chain (S05 and the isolation clear that is LAST of the
 * teardown). 0 = ok, 1 = refused before any RMW was sent (isolation already released / the SMC READ is refused or differs from the MMIO word), 2 = sent but fault / mismatch (the caller runs the
 * clear anyway). *rd = the last read of the isolation word (0 if unreadable). */
int wlan_link_iso_set(uint32_t *rd)
{
	uint32_t before = 0, iso0 = 0, iso0b = 0, after = 0;
	struct smc_res r = { 0, 0, 0, 0 }, rs = { 0, 0, 0, 0 };
	int rok = 0, reached;

	*rd = 0;
	if (!try_read32(ISO_REG, &before) || !try_read32(WLAN_PMU_PCIE0_ISO, &iso0))
		return 1;
	if ((before & 1u) || smc_priv_read(ISO_REG, &r) || (int32_t)(uint32_t)r.a0 < 0 || (uint32_t)r.a0 != before)
		return 1;
	smc_priv_rmw_set(&rs);
	reached = poll_bit(1u, &after, &rok);
	(void)try_read32(WLAN_PMU_PCIE0_ISO, &iso0b);
	*rd = rok ? after : 0u;
	return (rok && reached && (int32_t)(uint32_t)rs.a0 == 0 && ((after ^ before) & ~1u) == 0u && iso0b == iso0) ? 0 : 2;
}

int wlan_link_iso_clear(uint32_t *rd)
{
	uint32_t rb = 0, fin = 0;
	struct smc_res rc = { 0, 0, 0, 0 };
	int rok2 = 0, fin_ok;

	smc_priv_rmw_clear(&rc);
	(void)poll_bit(0u, &rb, &rok2);
	wait_ticks(counter_freq() / ISO_FINAL_DIV);
	fin_ok = try_read32(ISO_REG, &fin);
	*rd = fin_ok ? fin : 0u;
	return (rok2 && !(rb & 1u) && fin_ok && !(fin & 1u)) ? 0 : 2;
}
#endif	/* WLAN_LINK_RUN */
#endif	/* WLAN_ISO_RUN */

#else	/* not gs101 / --wlan-smc-read not built: refuse, no SMC */

int wlan_smc_read(void)
{
	con_str("WLAN", "SMC refused: --wlan-smc-read not built or not gs101, no SMC issued");
	return 2;
}

int wlan_iso_run(void)
{
	con_str("WLAN", "ISO refused: --wlan-iso not built or not gs101, no SMC issued");
	return 2;
}

#endif
