/*
 * sweetprobe_smc.c - the one PSCI query of --sweet-probe (sweetprobe.c, xnu patch 0363,
 * docs/research/161 s.2): one `smc #0` with x0 = function id and x1 = argument,
 * the SMCCC convention and the SMC conduit the shim already uses [src shim.c smc(), soc.json
 * notes.psci "conduit SMC"]. Same 12 lines as shim.c smc() with a2 = a3 = 0.
 * No state, no other instruction. The only caller is sweetprobe.c psci_call, which takes its ids
 * from a fixed table (PSCI_VERSION, PSCI_FEATURES of three function ids);
 * tools/sweetprobe_audit.py checks that this object holds exactly one smc, inside sweetprobe_smc,
 * and nothing undefined.
 */
#include <stdint.h>

#ifdef SWEET_PROBE
uint64_t sweetprobe_smc(uint64_t fn, uint64_t a1)
{
	/* style-allow: assert audited object (sweetprobe_smc.o): one smc, no state */
	register uint64_t x0 __asm__("x0") = fn;
	register uint64_t x1 __asm__("x1") = a1;
	register uint64_t x2 __asm__("x2") = 0;
	register uint64_t x3 __asm__("x3") = 0;

	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
			 :
			 : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
			   "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	return x0;
}
#endif
