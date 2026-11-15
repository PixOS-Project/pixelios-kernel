#pragma once
/*
 * --el2-trap: constants shared by el2.S (assembler) and el2.c / el2h.c (C). Macros only, no C syntax (el2.S includes this through cpp).
 * Layout checks that the C side can prove (struct offsets) are _Static_asserts in el2h.c; the numbers below are the only copy.
 */
/* fallback reasons: why stage 2 was not enabled [SPEC 3.2 (census), 3.1 P-B5 (outside dram_banks)] */
#define EL2_FB_NONE		0
#define EL2_FB_SCTLR_M		1
#define EL2_FB_MDCR		2
#define EL2_FB_OUTSIDE_BANKS	3

/* MDCR_EL2 trap bits that make B1 fall back: TPMCR 5, TPM 6, TDE 8, TDA 9, TDOSA 10, TDRA 11 [SPEC 3.2; L kvm_arm.h:320-326] = 0xF60 */
#define EL2_MDCR_TRAPS		0xF60

/* entry-8 frame (SPEC 3.3.2 "x0-x30 plus ELR/SPSR, about 34 words"): x0..x30 at 0..240, word 31 unused, ELR_EL2 at 256, SPSR_EL2 at 264; 16-byte aligned total */
#define EL2_FRAME_SIZE		272
#define EL2_FRAME_ELR		256
#define EL2_FRAME_SPSR		264

/* what el2_handle() asks the asm to do after it returns */
#define EL2_A_RETURN		0	/* restore the frame and eret */
#define EL2_A_PARK		1	/* park at el2_park_trap (SPEC 3.3.8 class 3) */
#define EL2_A_CLEAR_VM		2	/* HCR_EL2 = RW literal + TLBI, then restore the frame and eret (SPEC 3.3.8 VM-clear path) */

/* hvc report ABI (SPEC 3.4 (b) design item 3; XNU side: xnu/patches/0180): `hvc #EL2_HVC_IMM_REPORT` with x0 = EL2_REPORT_FN is answered with x0 = EL2_REPORT_ANS, x1 = HCR_EL2.VM
 * (read live), x2 = gaps, x3 = traps, x4 = bound; any other hvc is GAP + return with x0 untouched. 0x5345 = 'SE', 0x53454C32 = "SEL2", low byte = function 1 / answered 0xAA. */
#define EL2_HVC_IMM_REPORT	0x5345
#define EL2_REPORT_FN		0x53454C3200000001ULL
#define EL2_REPORT_ANS		0x53454C32000000AAULL

/* the EL2 stack and the table pool, reserved in el2.S (the stack top is the linker symbol _el2_stack_top, shim.ld) */
#define EL2_STACK_SIZE		0x2000
#define EL2_POOL_PAGE		0x4000			/* 16K granule */
#define EL2_POOL_SIZE		(5 * EL2_POOL_PAGE)	/* L2 + L3(T2 block) + L3(T1 block) + T1 page + 16K slack for the runtime round-up */
