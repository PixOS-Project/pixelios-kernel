/* Linux arm64 Image header; EL1 or EL2 entry, MMU off, x0 = DTB pointer. */
	.section .text.head, "ax"
	.globl _head
_head:
	b	_start			/* code0 */
	.long	0			/* code1 */
	.quad	0			/* text_offset */
	.quad	_end			/* image_size (linked at 0) */
	.quad	0xa			/* flags: LE, 4K pages, place anywhere */
	.quad	0			/* res2 */
	.quad	0			/* res3 */
	.quad	0			/* res4 */
	.ascii	"ARM\x64"		/* magic */
	.long	0			/* res5 (PE offset) */

	.text
	.globl _start
_start:
	msr	daifset, #0xf
	mov	x19, x0			/* DTB pointer from ABL */
	mrs	x21, CurrentEL
	lsr	x21, x21, #2
	msr	spsel, #1
	adr	x1, _stack_top
#ifdef EL2_TRAP
	cmp	x21, #2				/* (a) --el2-trap: the EL2 path gets its own stack (SPEC 3.3.1) */
	adr	x2, _el2_stack_top
	csel	x1, x2, x1, eq
#endif
	mov	sp, x1
	adr	x1, vectors
	msr	vbar_el1, x1
	isb
	/* zero .bss */
	adr	x1, _bss_start
	adr	x2, _bss_end
1:	cmp	x1, x2
	b.hs	2f
	str	xzr, [x1], #8
	b	1b
2:	adr	x1, probe_entry_el
	str	x21, [x1]
	cmp	x21, #2
	b.ne	3f
#ifdef EL2_TRAP
	bl	el2_census			/* (b) before HCR_EL2 is overwritten (SPEC 3.2) */
	adr	x1, el2_vectors			/* (c) EL2 vectors; VBAR_EL1 stays `vectors` */
	msr	vbar_el2, x1
	adr	x1, vectors
	msr	vbar_el1, x1
#else
	adr	x1, vectors
	msr	vbar_el2, x1
	msr	vbar_el1, x1
#endif
	mov	x2, #(1 << 31)			/* HCR_EL2: RW only (AArch64 EL1; AMO/IMO/FMO=0) */
	msr	hcr_el2, x2
	mrs	x2, midr_el1
	msr	vpidr_el2, x2
	mrs	x2, mpidr_el1
	msr	vmpidr_el2, x2
	/*
	 * SCTLR_EL1 configuration with MMU and caches disabled:
	 * Sets ARMv8.2 RES1 bits:
	 *   Bit 11: EOS (Exit from exception is Context Synchronizing)
	 *   Bit 20: RES1 / TSCXT (Trap EL0/EL1 SCXTNUM)
	 *   Bit 22: EIS (Exception Entry is Context Synchronizing)
	 *   Bit 23: SPAN (Set PSTATE.PAN on exception)
	 *   Bit 28: nTLSMD (No Trap Load/Store Multiple to Device)
	 *   Bit 29: LSMAOE (Load/Store Multiple Atomicity and Ordering Enable)
	 * Value = (1<<29)|(1<<28)|(1<<23)|(1<<22)|(1<<20)|(1<<11) = 0x30d00800.
	 * Policy bits explicitly clear:
	 *   Bit 0:  M   (MMU disabled)
	 *   Bit 1:  A   (Alignment check disabled)
	 *   Bit 2:  C   (Data / unified cache disabled)
	 *   Bit 12: I   (Instruction cache disabled)
	 *   Bit 19: WXN (Write permission implies XN disabled)
	 *   Bit 25: EE  (Little-endian exception handling)
	 */
	mov	x2, #0x0800
	movk	x2, #0x30d0, lsl #16
	msr	sctlr_el1, x2
	msr	cptr_el2, xzr			/* No FP/SIMD traps to EL2 */
	msr	hstr_el2, xzr			/* No CP15 register access traps to EL2 */
	mov	x2, #3				/* CNTHCTL_EL2: EL1PCTEN | EL1PCEN */
	msr	cnthctl_el2, x2
	msr	cntvoff_el2, xzr
	msr	vttbr_el2, xzr
	mrs	x2, icc_sre_el2
	mov	x3, #9
	orr	x2, x2, x3			/* SRE (bit 0) | Enable (bit 3) */
	msr	icc_sre_el2, x2
#ifdef EL2_TRAP
	bl	el2_enable			/* (d) after the hcr_el2 / vttbr_el2 writes above, before the eret (SPEC 3.3.1, 3.3.7) */
#endif
	isb
	adr	x2, 3f
	msr	elr_el2, x2
	mov	x2, #0x3c5			/* EL1h, DAIF masked */
	msr	spsr_el2, x2
	mov	x0, x19
	eret
3:	msr	spsel, #1
	adr	x1, _stack_top
	mov	sp, x1
	adr	x1, vectors
	msr	vbar_el1, x1
	isb
	mov	x0, x19
	bl	shim_main
4:	wfi
	b	4b

/* PSCI CPU_ON target. x0 = context id = cpu index (1..7). */
	.globl secondary_entry
secondary_entry:
	msr	daifset, #0xf
	mrs	x2, CurrentEL
	lsr	x2, x2, #2
	cmp	x2, #2
	b.ne	5f
	adr	x1, vectors
	msr	vbar_el2, x1
	msr	vbar_el1, x1
	mov	x2, #(1 << 31)			/* HCR_EL2: RW only */
	msr	hcr_el2, x2
	mrs	x2, midr_el1
	msr	vpidr_el2, x2
	mrs	x2, mpidr_el1
	msr	vmpidr_el2, x2
	/*
	 * SCTLR_EL1 ARMv8.2 RES1 bits: 11, 20, 22, 23, 28, 29 (0x30d00800).
	 * M/A/C/I/WXN/EE are all 0.
	 */
	mov	x2, #0x0800
	movk	x2, #0x30d0, lsl #16
	msr	sctlr_el1, x2
	msr	cptr_el2, xzr			/* No FP/SIMD traps to EL2 */
	msr	hstr_el2, xzr			/* No CP15 register access traps to EL2 */
	mov	x2, #3				/* CNTHCTL_EL2: EL1PCTEN | EL1PCEN */
	msr	cnthctl_el2, x2
	msr	cntvoff_el2, xzr
	msr	vttbr_el2, xzr
	mrs	x2, icc_sre_el2
	mov	x3, #9
	orr	x2, x2, x3			/* SRE (bit 0) | Enable (bit 3) */
	msr	icc_sre_el2, x2
	isb
	adr	x2, 5f
	msr	elr_el2, x2
	mov	x2, #0x3c5			/* EL1h, DAIF masked */
	msr	spsr_el2, x2
	eret
5:
	msr	spsel, #1
	adr	x1, _sec_stacks
	add	x1, x1, x0, lsl #12
	add	x1, x1, #0x1000
	mov	sp, x1
	adr	x1, vectors
	msr	vbar_el1, x1
	isb
	bl	secondary_main
6:	wfe
	b	6b

/*
 * Exception vectors. Each slot stashes x29/x30, loads its slot number
 * and jumps to the common path, which saves x0-x28 and calls
 * exc_handler(slot, regs). IRQs return; everything else ends in the
 * on-screen panic.
 */
.macro vent slot
	.balign	0x80
	stp	x29, x30, [sp, #-16]!
	mov	x29, #\slot
	b	exc_common
.endm

	.balign	0x800
	.globl vectors
vectors:
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

exc_common:
	sub	sp, sp, #(30 * 8)
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
	str	x28, [sp, #224]
	mov	x0, x29
	mov	x1, sp
	bl	exc_handler
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
	ldr	x28, [sp, #224]
	add	sp, sp, #(30 * 8)
	ldp	x29, x30, [sp], #16
	eret
