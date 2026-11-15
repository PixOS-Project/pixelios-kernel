#pragma once
#include <stdint.h>

/*
 * mifclk.h - opt-in MIF/INT clock read and one MIF request (build.sh --mif-clk, gs101 only; T-5030).
 *
 * mif_clk_cmdline(cmd, cap): the loader calls it once, after the boot_args are filled and just before the jump to XNU. It reads the live
 * DRAM-controller clock (ACPM DVFS dom 0 = MIF) and the interconnect clock (dom 1 = INT) with FREQ_GET, asks MIF for the table TOP row
 * (3172000 kHz, soc.json acpm.dvfs; acpm_dvfs_mif_top, the only request this module can build; with -DMIF_CLK_LEVEL_KHZ=KHZ (build.sh --mif-clk-level)
 * the ONE MIF table row KHZ instead, same path, same gates, same tokens), waits ~10 ms and reads both again.
 * The shim console never reaches the host in a handover boot (docs/runplans/gputest-dvfs-50eb.md), so the result is appended to the XNU
 * command line as decimal tokens (0 = not read) that xnu patch 0214 prints as `SWEET DVFS:` lines:
 *   sweetmif0b= / sweetmif1b=   MIF / INT kHz before the request      sweetmif0a= / sweetmif1a=   MIF / INT kHz after it
 *   sweetmifrc=  0 request sent and answered, 1 request refused or failed, 2 MIF already at the requested row (the top row, or the --mif-clk-level row; nothing asked),
 *                3 a GET failed (MIF or INT; nothing asked), 4 a gate refused (nothing sent)
 *   no room in the command line: nothing is asked and NO token is appended (console GAP only)
 * Every refusal is also a console line. No MMIO writer of its own: the only writes are the ones acpm.c's audited xfer() makes.
 */
#ifdef MIF_CLK
void mif_clk_cmdline(volatile char *cmd, uint32_t cap);
#else
static inline void mif_clk_cmdline(volatile char *cmd, uint32_t cap)
{
	(void)cmd;
	(void)cap;
}
#endif
