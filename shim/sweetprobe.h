#pragma once
#include <stdint.h>

/*
 * sweetprobe.h - opt-in READ-ONLY probes of the shim side of the LOWCLK B1/B2 batch (build.sh
 * --sweet-probe, gs101 only; xnu patch 0363, docs/research/161).
 *
 * sweet_probe_cmdline(cmd, cap): the loader calls it once, after the boot_args are filled and next
 * to mif_clk_cmdline, just before the jump to XNU. It asks four
 * QUERY-ONLY things and appends the answers to the XNU command line as decimal tokens (0 = not
 * read), which xnu patch 0363 prints as `SWEET PROBE:` lines (the shim
 * console never reaches the host in a handover boot, shim/mifclk.h):
 *   B2 PSCI [src Linux v6.6 include/uapi/linux/psci.h:33-50; research 161 s.2]: PSCI_VERSION,
 * PSCI_FEATURES(CPU_SUSPEND 64-bit 0xC4000001), PSCI_FEATURES(CPU_OFF 0x84000002),
 *      PSCI_FEATURES(CPU_SUSPEND 32-bit 0x84000001). CPU_SUSPEND / CPU_OFF themselves are never
 * called: the function ids live in a fixed table, no id is a parameter.
 *        sweetprobe_pv= sweetprobe_pf1= sweetprobe_pf2= sweetprobe_pf3=     raw 32-bit results (a
 * negative PSCI code is shown as its unsigned value, -1 = 4294967295)
 *   B1 SLC service over ACPM [src SG kernel/google-modules/soc/gs @ 20407875
 * drivers/soc/google/pt/slc_acpm.c:29-36, :126-195; research 161 s.1]:
 *      step A: acpm_slc_describe of channels 10 and 11 (reads only): sweetprobe_c10a= / c10b= /
 * c11a= / c11b= (a = qlen | esz raw << 9 | type << 16 | poll << 20 | why << 24 | present << 31,
 *      b = rr | rf << 8 | sr << 16 | sf << 24, every index clamped to 255).
 *      step B: ONE fixed PT_VERSION (0xd005) request on channel 10, only when step A shows a
 * validated descriptor and two empty queues. PT_CHECK / PT_ENABLE / PT_MUTATE / PT_DISABLE and
 *      the PMON opcodes are NOT expressible (acpm.h acpm_slc_version has no argument):
 * sweetprobe_slcv= = reply word 1 ((major << 16) | minor), sweetprobe_slcrc=
 *        0 sent and answered, 1 request refused or failed after the descriptor validated
 * (sweetprobe_slce= err | published << 8 | why << 16, sweetprobe_slcs= four queue pointers as b
 * above),
 *        2 channel 10 descriptor refused (nothing sent), 4 queues not empty (nothing sent).
 *   no room in the command line: nothing is asked and NO token is appended (console GAP only), the
 * pattern of mifclk.c
 * Every refusal is also a console line. No MMIO writer of its own: the only writes are the ones
 * acpm.c's audited xfer_eb() makes (the one PT_VERSION request); the one `smc #0` is the PSCI
 * query.
 */
#ifdef SWEET_PROBE
void sweet_probe_cmdline(volatile char *cmd, uint32_t cap);
#else
static inline void sweet_probe_cmdline(volatile char *cmd, uint32_t cap)
{
	(void)cmd;
	(void)cap;
}
#endif
