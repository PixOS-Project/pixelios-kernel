/*
 * EL2 stage-2 trap probe, assembler side (--el2-trap only; an object without the define is empty). Spec: S/docs/specs/SPEC-EL2-PROXY-PROBE.md DRAFT 6 section 3.2-3.3.
 * HARDWARE-WRITING: system registers written here (all reset with the CPU): HCR_EL2 (el2_enable, VM-clear path), VTCR_EL2, VTTBR_EL2 (el2_enable), ELR_EL2 (entry 4, frame restore),
 * SPSR_EL2 (frame restore) + one TLBI per enable/disable path. No SP_EL2 write (the EL2 stack is set by start.S site (a): msr spsel, #1; mov sp), no VBAR write (start.S site (c)).
 * Audited by tools/el2_audit.py (object audit, run by build.sh --el2-trap through tools/test-bluejay-boot.py --el2-objects).
 */
#ifdef EL2_TRAP

#include "el2_abi.h"

	.text

/*
 * Vector table (SPEC 3.3.2): 16 entries of 0x80, 2 KiB aligned, same layout as `vectors` in start.S. Only entry 8 (sync from EL1/EL0, AArch64) is handled; entry 4 (sync, EL2h) is the
 * guarded-load skip; every other entry parks at el2_park_vec. A slot that outgrows 0x80 bytes is an assembly error (r5 F6), never a silent shift of the table.
 */
.macro vent n
	.balign	0x80
.Lss\@:
.if \n == 4
	.globl	el2_vent4
el2_vent4:
	/* uses only x16/x17 (and xzr/wzr): EL1's x0-x30 are already in entry 8's frame, and el2_ld32 keeps nothing in x16/x17 (SPEC 3.3.2 steps 3-4; audit rule "entry 4") */
	adr	x16, el2_guard
	ldr	w16, [x16]
	cmp	w16, #1
	b.ne	el2_park_guard			/* guard clear (or not 1): park */
	mrs	x16, esr_el2
	ubfx	x16, x16, #26, #6		/* EC = ESR_EL2[31:26]; ubfx, not lsr, so ESR[63:32] cannot alias it (r3 finding 10) */
	cmp	x16, #0x25			/* EC 0x25 = DABT_CUR */
	b.ne	el2_park_ec
	mrs	x16, elr_el2
	adr	x17, el2_ld32_try
	cmp	x16, x17			/* only the guarded load may fault */
	b.ne	el2_park_elr
	adr	x16, el2_ldfault
	mov	w17, #1
	str	w17, [x16]			/* el2_ldfault = 1 */
	adr	x16, el2_guard
	str	wzr, [x16]			/* el2_guard = 0 */
	adr	x17, el2_ld32_after
	msr	elr_el2, x17			/* resume after the faulting load; SPSR_EL2 is the EL2h state the hardware wrote */
	eret
.elseif \n == 8
	.globl	el2_vent8
el2_vent8:
	b	el2_sync_lower
.else
	b	el2_park_vec
.endif
.if (. - .Lss\@) > 0x80
	.error "el2 vector slot > 0x80"
.endif
.endm

	.balign	0x800
	.globl	el2_vectors
el2_vectors:
	vent 0
	vent 1
	vent 2
	vent 3
	vent 4
	vent 5
	vent 6
	vent 7
	vent 8
	vent 9
	vent 10
	vent 11
	vent 12
	vent 13
	vent 14
	vent 15

/* one park label per reason (SPEC 3.3.2 step 4): the QEMU test and the audit find them by ELF symbol; each is a single `b .` */
	.globl	el2_park_ec
el2_park_ec:
	b	el2_park_ec
	.globl	el2_park_guard
el2_park_guard:
	b	el2_park_guard
	.globl	el2_park_elr
el2_park_elr:
	b	el2_park_elr
	.globl	el2_park_vec
el2_park_vec:
	b	el2_park_vec
	.globl	el2_park_trap			/* entry 8 class 3 (SPEC 3.3.8): the spec names no label for it; added here */
el2_park_trap:
	b	el2_park_trap

/*
 * Entry 8: save x0-x30 and ELR/SPSR in the frame BEFORE anything can fault (SPEC 3.3.2 step 1), call the C handler (el2h.c: el2_handle(frame, esr, hpfar, far)), then
 * act on its answer. sp is the EL2 stack (start.S site (a)); the frame is 16-byte aligned.
 */
	.balign	4
el2_sync_lower:
	sub	sp, sp, #EL2_FRAME_SIZE
	stp	x0, x1, [sp, #0]
	stp	x2, x3, [sp, #16]
	stp	x4, x5, [sp, #32]
	stp	x6, x7, [sp, #48]
	stp	x8, x9, [sp, #64]
	stp	x10, x11, [sp, #80]
	stp	x12, x13, [sp, #96]
	stp	x14, x15, [sp, #112]
	stp	x16, x17, [sp, #128]
	stp	x18, x19, [sp, #144]
	stp	x20, x21, [sp, #160]
	stp	x22, x23, [sp, #176]
	stp	x24, x25, [sp, #192]
	stp	x26, x27, [sp, #208]
	stp	x28, x29, [sp, #224]
	str	x30, [sp, #240]
	mrs	x0, elr_el2
	mrs	x1, spsr_el2
	stp	x0, x1, [sp, #EL2_FRAME_ELR]
	mrs	x1, esr_el2
	mrs	x2, hpfar_el2
	mrs	x3, far_el2
	mov	x0, sp
	bl	el2_handle
	cbz	w0, el2_frame_restore		/* EL2_A_RETURN */
	cmp	w0, #EL2_A_PARK
	b.eq	el2_park_trap
	cmp	w0, #EL2_A_CLEAR_VM
	b.ne	el2_park_trap			/* any other value parks: never fail open on an unknown action (review r1 F5) */
	/* EL2_A_CLEAR_VM (SPEC 3.3.8 VM-clear path): the literal RW, never a bic of an mrs; then the pKVM sequence after an HCR change */
	mov	x1, #0x80000000			/* HCR_EL2 = RW */
	msr	hcr_el2, x1
	isb
	tlbi	vmalls12e1
	dsb	nsh
	isb
/* frame restore (r5 F1): ELR/SPSR first (through x0/x1), then EL1's x0-x30; the only writers of x19-x29 in this file; the range ends at the eret */
	.globl	el2_frame_restore
el2_frame_restore:
	ldp	x0, x1, [sp, #EL2_FRAME_ELR]
	msr	elr_el2, x0
	msr	spsr_el2, x1
	ldp	x0, x1, [sp, #0]
	ldp	x2, x3, [sp, #16]
	ldp	x4, x5, [sp, #32]
	ldp	x6, x7, [sp, #48]
	ldp	x8, x9, [sp, #64]
	ldp	x10, x11, [sp, #80]
	ldp	x12, x13, [sp, #96]
	ldp	x14, x15, [sp, #112]
	ldp	x16, x17, [sp, #128]
	ldp	x18, x19, [sp, #144]
	ldp	x20, x21, [sp, #160]
	ldp	x22, x23, [sp, #176]
	ldp	x24, x25, [sp, #192]
	ldp	x26, x27, [sp, #208]
	ldp	x28, x29, [sp, #224]
	ldr	x30, [sp, #240]
	add	sp, sp, #EL2_FRAME_SIZE
	eret

/*
 * el2_ld32(x0 = PA) -> x0 = word, x1 = fault (SPEC 3.3.2 step 3): the one load from a computed PA. Uses only x0-x3, never x16/x17; only x0 is live across el2_ld32_try; the
 * addresses needed after el2_ld32_after are recomputed with adr. A faulting load is skipped by entry 4 (guard, EC, ELR == el2_ld32_try) and leaves w0 = 0, w1 = 1.
 */
	.globl	el2_ld32
	.type	el2_ld32, %function
el2_ld32:
	adr	x2, el2_ldfault
	str	wzr, [x2]
	adr	x3, el2_guard
	mov	w2, #1
	str	w2, [x3]
	dsb	sy
el2_ld32_try:
	ldr	w0, [x0]
el2_ld32_after:
	adr	x3, el2_ldfault
	ldr	w1, [x3]
	adr	x3, el2_guard
	str	wzr, [x3]
	cbz	w1, 1f
	mov	w0, wzr				/* a faulted load never leaves an undefined Xt */
1:	ret
	.size	el2_ld32, . - el2_ld32

/*
 * el2_hcr_vm() -> x0 = HCR_EL2.VM (bit 0), the live value for the hvc report (SPEC 3.4 (b) design items 3-4). READ ONLY: one mrs, no msr; clobbers x0 only.
 */
	.globl	el2_hcr_vm
	.type	el2_hcr_vm, %function
el2_hcr_vm:
	mrs	x0, hcr_el2
	and	x0, x0, #1
	ret
	.size	el2_hcr_vm, . - el2_hcr_vm

/*
 * el2_census (start.S site (b), before HCR_EL2 is overwritten) (SPEC 3.2): read-only; stash ABL's EL2 registers in el2_cen, decide the fallback. Clobbers x0-x6, x30 untouched.
 * SCTLR_EL2.M = 1 -> EL2_FB_SCTLR_M; any MDCR_EL2 trap bit of EL2_MDCR_TRAPS -> EL2_FB_MDCR; el2_enable then writes nothing.
 */
	.globl	el2_census
	.type	el2_census, %function
el2_census:
	adr	x0, el2_cen
	mrs	x1, sctlr_el2
	str	x1, [x0, #0]
	mrs	x2, hcr_el2
	str	x2, [x0, #8]
	mrs	x2, vtcr_el2
	str	x2, [x0, #16]
	mrs	x2, vttbr_el2
	str	x2, [x0, #24]
	mrs	x2, tcr_el2
	str	x2, [x0, #32]
	mrs	x3, mdcr_el2
	str	x3, [x0, #40]
	mrs	x2, cptr_el2
	str	x2, [x0, #48]
	mrs	x2, id_aa64mmfr1_el1
	str	x2, [x0, #56]
	adr	x4, el2_census_ok
	mov	w5, #1
	str	w5, [x4]
	mov	w5, #EL2_FB_SCTLR_M
	tbnz	x1, #0, 2f
	mov	w5, #EL2_FB_MDCR
	mov	w6, #EL2_MDCR_TRAPS
	tst	w3, w6
	b.ne	2f
	mov	w5, #EL2_FB_NONE
2:	adr	x4, el2_fallback
	str	w5, [x4]
	ret
	.size	el2_census, . - el2_census

/*
 * Stage-2 pool: runtime round-up of el2_pool to 16K (the load address is not known at link time). el2_inval_pool invalidates the 4 used pool pages
 * (L2, L3(T2), L3(T1), T1 page [el2h.c:41-42]) to PoC by line, MMU off, the Linux __inval_dcache_area loop for a line-aligned range [L v5.10 mm/cache.S:142-167,
 * dcache_line_size assembler.h:296-301]; clobbers x0-x3 (review el2-probe-job2-r1 F3).
 */
	.macro	el2_pool_base reg, tmp
	adr	\reg, el2_pool
	mov	\tmp, #(EL2_POOL_PAGE - 1)
	add	\reg, \reg, \tmp
	bic	\reg, \reg, \tmp
	.endm
	.macro	el2_inval_pool
	el2_pool_base x0, x1
	mov	x1, #(4 * EL2_POOL_PAGE)
	add	x1, x1, x0
	mrs	x3, ctr_el0
	ubfx	x3, x3, #16, #4			/* CTR_EL0.DminLine: log2(words) */
	mov	x2, #4
	lsl	x2, x2, x3
1:	dc	ivac, x0
	add	x0, x0, x2
	cmp	x0, x1
	b.lo	1b
	dsb	sy
	.endm

/*
 * el2_enable (start.S site (d), after the hcr_el2 / vttbr_el2 writes of start.S) (SPEC 3.3.7): build the tables (el2h.c, P-B5 check inside), then pKVM's order: dsb ish; HCR_EL2 = RW|VM;
 * VTCR_EL2; VTTBR_EL2; isb; tlbi vmalls12e1; dsb nsh; isb; read HCR/VTCR/VTTBR back into el2_rb (the EL1 printer prints these, not the intended values). Nothing is written when
 * the census or P-B5 said fall back. Preserves x19-x29 (start.S needs x19, x21); clobbers x0-x4.
 */
	.globl	el2_enable
	.type	el2_enable, %function
el2_enable:
	str	x30, [sp, #-16]!
	adr	x0, el2_fallback
	ldr	w0, [x0]
	cbnz	w0, 9f
	el2_inval_pool				/* drop stale (ABL) lines before the non-cacheable table writes [L v5.10 head.S:276-287] */
	el2_pool_base x0, x1
	adr	x1, _head
	adr	x2, _end
	bl	el2_build
	cbnz	x0, 9f
	dmb	sy
	el2_inval_pool				/* and drop speculatively loaded lines after them [L v5.10 head.S:392-406] */
	adr	x0, el2_vtcr_set
	ldr	x1, [x0]
	adr	x0, el2_vttbr_set
	ldr	x2, [x0]
	dsb	ish
	mov	x0, #0x1
	movk	x0, #0x8000, lsl #16		/* HCR_EL2 = RW | VM */
	msr	hcr_el2, x0
	msr	vtcr_el2, x1
	msr	vttbr_el2, x2
	isb
	tlbi	vmalls12e1
	dsb	nsh
	isb
	adr	x3, el2_rb
	mrs	x0, hcr_el2
	str	x0, [x3, #0]
	mrs	x0, vtcr_el2
	str	x0, [x3, #8]
	mrs	x0, vttbr_el2
	str	x0, [x3, #16]
	adr	x3, el2_s2_on
	mov	w4, #1
	str	w4, [x3]
9:	ldr	x30, [sp], #16
	ret
	.size	el2_enable, . - el2_enable

	.bss
	.balign	4
	.globl	el2_guard, el2_ldfault			/* global only for the QEMU harness (shim/el2_qemu_test.S) */
el2_guard:	.space	4			/* 1 only around the one emulated load (el2_ld32) */
el2_ldfault:	.space	4			/* set by entry 4 when the guarded load aborted */
	.balign	16
el2_pool:	.space	EL2_POOL_SIZE		/* L2, two L3, T1 page + slack: rounded up to 16K at run time */

/* the EL2 stack: a section of its own so shim.ld puts it last in .bss and defines _el2_stack_top after it (PROVIDE: default builds never see it) */
	.section .el2stack, "aw", %nobits
	.balign	16
	.space	EL2_STACK_SIZE

#endif
