#pragma once
/*
 * --el2-trap (EL2_TRAP only): pure decode / dispatch / accounting and the EL1 report printer of the EL2 stage-2 trap probe.
 * Spec: S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 3.3.8 (handler), 3.3.9 (printer). No MMIO, no system registers here: the asm
 * handler (el2.S) calls these and the EL1 shim code prints with el2_print_report().
 */
#include <stdint.h>

#ifdef EL2_TRAP

#define EL2_RING_N	16u	/* ring entries [SPEC 3.3.8 "16-entry ring in shim .bss"] */
#define EL2_BOUND_TRAPS	64u	/* "after 64 traps ... clear VM" [SPEC 3.3.8 Bound] */

/* ring entry kind [SPEC 3.3.8]: 0 served read, 1 class-1 GAP, 2 hvc GAP, 3 nisv/write GAP, 4 class-3 park record, 5 trap bound (never a ring slot) */
enum { EL2_K_SERVED = 0, EL2_K_GAP_EC = 1, EL2_K_HVC = 2, EL2_K_NISV = 3, EL2_K_PARK = 4, EL2_K_BOUND = 5 };

#include "el2_abi.h"	/* EL2_FB_* fallback reasons (why stage 2 was not enabled) [SPEC 3.2, 3.1 P-B5], frame layout, handler actions */

struct el2_dec {
	uint64_t ipa;
	uint32_t width;		/* bytes = 1 << SAS when isv, else 0 */
	uint32_t rt;		/* SRT [20:16] when isv (31 = discard / xzr), else 0 */
	uint32_t write;		/* WnR bit 6 */
	uint32_t isv;		/* bit 24 */
	uint32_t sse;		/* bit 21 when isv */
	uint32_t sf;		/* bit 15 when isv */
};

struct el2_ent {
	uint64_t ipa, elr, val;
	uint32_t esr, width, fault, kind, vm;
};

struct el2_census_regs {
	uint64_t sctlr, hcr, vtcr, vttbr, tcr, mdcr, cptr, mmfr1;
};

struct el2_s2_readback {
	uint64_t hcr, vtcr, vttbr;
};

/* entry-8 register frame, built by el2.S (layout in el2_abi.h, checked by _Static_assert in el2h.c) */
struct el2_frame {
	uint64_t x[32];		/* x0..x30; x[31] unused */
	uint64_t elr, spsr;	/* ELR_EL2 / SPSR_EL2 of the trapped EL1 context */
};

/* memory the stage-2 map treats as Normal: board.json dram_banks, emitted by tools/boards.py (EL2_DRAM_BANKS) */
struct el2_bank { uint64_t base, size; };
/* what the table builder maps; the shim's instance comes from board_config.h (el2cfg.c), the QEMU test brings its own (S4 of the spec's open list) */
struct el2_cfg {
	const struct el2_bank *banks;
	uint32_t nbanks;
	uint64_t vtcr;		/* EL2_VTCR */
	uint64_t t2_ipa;	/* trap target T2 (the GICD page on the phone) */
};
const struct el2_cfg *el2_cfg_get(void);

/* el2_ld32 (el2.S): the one guarded load from a computed PA; val = the word, fault = 1 if the load aborted (val is then 0). Returned in x0/x1 (a 16-byte struct, AAPCS64) */
struct el2_ld { uint64_t val, fault; };
struct el2_ld el2_ld32(uint64_t pa);

/* el2h.c: the table builder (called by el2_enable) and the entry-8 handler glue (called by el2.S) */
uint64_t el2_build(uint64_t pool, uint64_t head, uint64_t end);	/* 0 = tables built; else the EL2_FB_* reason (also stored in el2_fallback) */
unsigned el2_handle(struct el2_frame *f, uint64_t esr, uint64_t hpfar, uint64_t far);	/* returns EL2_A_* */
extern uint64_t el2_t1_pa, el2_t2_pa, el2_vtcr_set, el2_vttbr_set;

struct el2_dec el2_decode(uint32_t esr, uint64_t hpfar, uint64_t far);
/* returns the ring kind to record: EL2_K_SERVED = emulate the 32-bit read of the trap target; EL2_K_GAP_EC / EL2_K_NISV = GAP, clear VM, return without advancing ELR;
 * EL2_K_HVC = GAP + return (ELR is already past the hvc); EL2_K_PARK = record and park. on_target = the fault IPA is a trap page (T1 or T2). */
unsigned el2_dispatch(uint32_t esr, int on_target);
/* the hvc report call (EL2_HVC_IMM_REPORT / EL2_REPORT_FN in el2_abi.h): 1 iff EC == 0x16 (HVC64), ESR ISS[15:0] == the report imm and x0 == the function id. Pure, host-tested. */
int el2_report_match(uint32_t esr, uint64_t x0);
/* el2.S: HCR_EL2.VM (bit 0) read live with mrs; read only */
uint64_t el2_hcr_vm(void);
/* the bound decision: clear VM once `traps` (already counting this one) reaches EL2_BOUND_TRAPS */
int el2_bound_reached(uint32_t traps);
/* record an event: ring slot (or overflow) and the S3 counters of [SPEC 3.3.8]; kind 5 sets el2_bound and counts one gap, no slot */
void el2_account(const struct el2_ent *e);

extern struct el2_ent el2_ring[EL2_RING_N];
extern uint32_t el2_ring_n, el2_ring_ovf, el2_traps, el2_gaps, el2_bound, el2_fallback, el2_census_ok, el2_s2_on, el2_reports;
extern struct el2_census_regs el2_cen;
extern struct el2_s2_readback el2_rb;

void el2_print_resident(uint64_t bootflags);	/* EL2_RESIDENT: "EL2 RESIDENT bootflags <16 hex>" (SPEC 3.4 (b) design item 2), printed by loader.c before the jump */
void el2_print_ring(void);	/* ring entries, then "EL2 GAP ring full" if overflowed, then "EL2 GAP bound" if el2_bound [SPEC 3.3.9] */
void el2_print_report(void);	/* census, fallback GAP or readback + ring + RESULT [SPEC 3.3.7, 3.3.9] */

#endif
