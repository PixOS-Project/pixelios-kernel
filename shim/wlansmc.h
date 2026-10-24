#pragma once
#include <stdint.h>

/*
 * wlansmc.h - W2a of docs/research/29-wlan-stage1-design.md (build.sh --wlan-smc-read, implies --wlan-probe, gs101 only): the smallest possible EL3 interaction. Two SMC READs of the PMU alive
 * registers PMU_PCIE1_ISO 0x17463EC4 (PHY isolation, bit 0) and, as a control, PMU_PCIE0_ISO 0x17463EC0, through Google's SMC_CMD_PRIV_REG:
 *     smc #0 with x0 = 0x82000504, x1 = the PA, x2 = PRIV_REG_OPTION_READ (0), x3 = 0
 * (exynos-smc.h:45 SMC_CMD_PRIV_REG; exynos-el3_mon.h:21-23 OPTION READ/WRITE/RMW = 0/1/2; get_priv_reg() = arm_smccc_smc(SMC_CMD_PRIV_REG, reg, PRIV_REG_OPTION_READ, 0, 0,0,0,0, &res), exynos-pd_el3.c:54-63).
 * smc_priv_read() is the ONLY `smc` instruction of the object; the function id and the option are constants of the instruction sequence (a WRITE / RMW option is unrepresentable), the
 * address is checked against exactly the two words above before the SMC (zero SMC otherwise). The call is synchronous (an SMCCC call returns in registers), no loop, no retry.
 *
 * Output (con_str "WLAN"):
 *   `SMC read pa=17463EC4 ret=<int32 a0> a0=.. a1=.. a2=.. a3=..`   for each address (a0..a3 = the four result registers as returned),
 *   `SMC cmp pa=.. mmio=<plain MMIO read> same=<yes|no|error>`      (same: a0 low 32 bits == the MMIO word; error: a0 is negative as int32 = EL3 refusal -EINVAL / SMCCC unknown-function -1, or the MMIO read faulted),
 *   `SMC mmio after pa=.. v=..` (+ `!!!` if a READ changed the register),
 *   `SMC summary same=<yes|no|error> ambiguous=<0|1>`   (ambiguous=1: a0 == 0 and the MMIO word == 0, i.e. the result cannot tell "value" from "status 0"; the observed word is 0, so the first run will be ambiguous: *guess*).
 * What x0 holds on success is UNKNOWN (docs/research/29 U2): Linux's get_priv_reg() returns (int)res.a0 and mainline notes the SMC read is not used because registers are readable by MMIO.
 */
int try_read32(uintptr_t address, uint32_t *value);
int wlan_smc_read(void);

/*
 * W2c, --wlan-iso (implies --wlan-probe and --wlan-smc-read; linked as wlansmc_iso.o): WRITER via EL3. Releases the PCIe1 PHY isolation exactly as Linux's first power-on step does and puts it back:
 *   preconditions (MMIO reads, else nothing is sent): 0x17463EC4 bit 0 == 0, PERST gph2-0 low, WLAN_EN gpp14-2 low, the timer runs, an SMC READ of 0x17463EC4 is accepted (a0 not negative);
 *   ONE RMW   `smc #0`: x0 = 0x82000504, x1 = 0x17463EC4, x2 = 2 (RMW), x3 = 1 (mask), x4 = 1 (value)       -> bounded read-back (<= 10 x 100 us) must show bit 0 == 1, every other bit unchanged, 0x17463EC0 unchanged;
 *   ONE RMW   `smc #0`: x0 = 0x82000504, x1 = 0x17463EC4, x2 = 2 (RMW), x3 = 1 (mask), x4 = 0 (value)       -> only if the bit reads set (or the read-back failed): read back must show bit 0 == 0.
 * Never a second set. Summary: `WLAN ISO release=<ok|refused|fault|mismatch> readback=0x.. restore=<ok|FAILED|n/a>`; a failed restore prints `!!! RESTORE FAILED`.
 * The helpers smc_priv_rmw_set / smc_priv_rmw_clear take no parameter but the result pointer: fid, register, option, mask and value are constants (audited in the object).
 */
int wlan_iso_run(void);

/* --wlan-link only (wlansmc_link.o, docs/research/29 section 14): the isolation release / clear of the long chain, same constants and bounded read-backs as wlan_iso_run; see wlansmc.c. */
int wlan_link_iso_set(uint32_t *rd);
int wlan_link_iso_clear(uint32_t *rd);
