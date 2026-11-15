/*
 * QEMU nested-fault test harness (spec B3q, SPEC 3.3.2 test bullet): the real el2.S vectors, el2_ld32, el2_census, el2_enable and the real el2h.c handler, started at EL2 on
 * `qemu-system-aarch64 -M virt,virtualization=on -cpu max`. The fixture number is a word at FIXTURE_ADDR written by `-device loader`. Each fixture ends at a labelled `b .`:
 *   el2_test_done = returned as expected, a park label = parked for that reason, el2_test_fail = a self-check in this file failed. tools/el2-qemu-test reads PC from the QEMU monitor and compares it
 *   with the ELF symbols (nm); a timeout alone is never a pass. Test code only: never part of the shim image.
 * Fixtures: 0 control, 1 (i) el2_ld32 on RAM then on a hole, 2 (ii) an EL1 load that faults at stage 2 and whose emulated read faults, 4 (iv) EC forced to 0x24 at entry 4, 5 (v) guard clear with a real
 * fault at el2_ld32_try, 8 (viii) EL1 `hvc #0x5345` with the report function id is answered (x0 = SEL2 0xAA, x1 = HCR_EL2.VM, x2..x4 = gaps, traps, bound), 9 (ix) an hvc with a wrong imm, then one with a wrong id, comes back
 * with x0..x4 untouched and gaps + 1 each (SPEC 3.4 (b) items 3-4, 7; the ABI constants are typed here on purpose, not taken from el2_abi.h), 6 (vi) guard set with a real fault at another instruction, 7 (vii) a branch to VBAR_EL2 + 0x280.
 */
#ifdef EL2_TRAP

#define FIXTURE_ADDR	0x47fff000
#define HOLE		0x0c000004		/* inside T2 (0x0c000000), the trapped page; no memory region at 0x0c000000 in the virt map */

	.section .text.head, "ax"
	.globl	_head
	.globl	_start
_head:
_start:
	mrs	x0, CurrentEL
	cmp	x0, #8				/* started at EL2 (virtualization=on) */
	b.ne	el2_test_fail
	msr	spsel, #1
	adr	x1, _el2_stack_top
	mov	sp, x1
	adr	x1, el2_vectors
	msr	vbar_el2, x1
	isb
	adr	x1, _bss_start			/* zero .bss like start.S does */
	adr	x2, _bss_end
1:	cmp	x1, x2
	b.hs	2f
	str	xzr, [x1], #8
	b	1b
2:	bl	el2_census
	ldr	x1, =FIXTURE_ADDR
	ldr	w19, [x1]
	cmp	w19, #1
	b.eq	fix_i
	cmp	w19, #2
	b.eq	fix_ii
	cmp	w19, #4
	b.eq	fix_iv
	cmp	w19, #5
	b.eq	fix_v
	cmp	w19, #6
	b.eq	fix_vi
	cmp	w19, #7
	b.eq	fix_vii
	cmp	w19, #8
	b.eq	fix_viii
	cmp	w19, #9
	b.eq	fix_ix
	cmp	w19, #10
	b.eq	fix_x
	b	el2_test_done			/* 0: control, the park detector must see `returned` */
	.ltorg

	.text
	.globl	el2_test_done
el2_test_done:
	b	el2_test_done
	.globl	el2_test_fail
el2_test_fail:
	b	el2_test_fail

/* (i) the control (a RAM word) must return its value with fault = 0, else the hole is no proof; then the hole: fault = 1, value 0, guard cleared */
fix_i:
	adr	x0, ctl_word
	bl	el2_ld32
	mov	w2, #0x600D
	movk	w2, #0x600D, lsl #16
	cmp	w0, w2
	b.ne	el2_test_fail
	cbnz	x1, el2_test_fail
	ldr	x0, =HOLE
	bl	el2_ld32
	cbnz	x0, el2_test_fail
	cmp	x1, #1
	b.ne	el2_test_fail
	adr	x2, el2_guard
	ldr	w2, [x2]
	cbnz	w2, el2_test_fail
	b	el2_test_done
	.ltorg

/* (ii) stage 2 on, drop to EL1 with a known PSTATE, load from the trapped hole page: entry 8 -> el2_ld32 faults -> entry 4 -> back to EL1 at ELR + 4 with the original SPSR, Xt = 0 */
fix_ii:
	bl	el2_enable
	adr	x0, el2_s2_on
	ldr	w0, [x0]
	cbz	w0, el2_test_fail		/* stage 2 not enabled: the fixture cannot run (QEMU lacks 16K stage 2?) */
	adr	x0, fix_ii_el1
	msr	elr_el2, x0
	ldr	x0, =0xA00003c5			/* EL1h, DAIF masked, N and C set */
	msr	spsr_el2, x0
	eret
fix_ii_el1:
	ldr	x0, =HOLE
	mov	x5, #0x1234
	mov	x6, xzr
	mov	x7, xzr
	mov	x8, xzr
	ldr	w5, [x0]			/* the trapped load: ISV = 1, SAS 2, SRT 5 */
	mrs	x6, nzcv			/* skipped (x6 stays 0) if ELR came back more than 4 past the load */
	mrs	x7, daif
	mrs	x8, CurrentEL
	mov	x9, #0xA0000000
	cmp	x6, x9
	b.ne	el2_test_fail
	cmp	x7, #0x3c0
	b.ne	el2_test_fail
	cmp	x8, #4
	b.ne	el2_test_fail
	cbnz	w5, el2_test_fail		/* Xt = 0 after the faulted emulated read */
	adr	x0, el2_traps
	ldr	w1, [x0]
	cmp	w1, #1				/* the access ran once: no re-trap (ELR + 4) */
	b.ne	el2_test_fail
	adr	x0, el2_gaps
	ldr	w1, [x0]
	cmp	w1, #1				/* gaps + 1 for the faulted read */
	b.ne	el2_test_fail
	adr	x0, el2_ring
	ldr	w1, [x0, #32]			/* ring[0].fault */
	cmp	w1, #1
	b.ne	el2_test_fail
	ldr	w1, [x0, #36]			/* ring[0].kind = served */
	cbnz	w1, el2_test_fail
	ldr	w1, [x0, #40]			/* ring[0].vm = 1 */
	cmp	w1, #1
	b.ne	el2_test_fail
	adr	x0, el2_guard
	ldr	w1, [x0]
	cbnz	w1, el2_test_fail
	b	el2_test_done
	.ltorg

/* (iv) guard = 1, ELR = el2_ld32_try, EC forced to 0x24, then a branch to entry 4: must park at el2_park_ec. x30 = el2_test_done is set last, after the setup */
fix_iv:
	adr	x0, el2_guard
	mov	w1, #1
	str	w1, [x0]
	adr	x0, el2_ld32
	add	x0, x0, #LD32_TRY_OFF
	msr	elr_el2, x0
	mov	x0, #(0x24 << 26)
	msr	esr_el2, x0
	mov	x0, #0x3c9			/* EL2h, DAIF masked */
	msr	spsr_el2, x0
	mrs	x0, vbar_el2
	add	x0, x0, #0x200
	adr	x30, el2_test_done
	br	x0

/* (v) guard clear, a real fault at el2_ld32_try (called directly): must park at el2_park_guard */
fix_v:
	adr	x0, el2_guard
	str	wzr, [x0]
	ldr	x0, =HOLE
	adr	x1, el2_ld32
	add	x1, x1, #LD32_TRY_OFF
	adr	x30, el2_test_done
	br	x1
	.ltorg

/* (vi) guard set, a real fault at an instruction that is not el2_ld32_try: must park at el2_park_elr */
fix_vi:
	adr	x0, el2_guard
	mov	w1, #1
	str	w1, [x0]
	ldr	x0, =HOLE
	adr	x30, el2_test_done
	ldr	w1, [x0]
	b	el2_test_done			/* only reached by a mutated entry 4 that returned (it resumes at el2_ld32_after instead) */
	.ltorg

/* (vii) a synchronous exception branched to VBAR_EL2 + 0x280 (an entry that must park): el2_park_vec */
fix_vii:
	mrs	x0, vbar_el2
	add	x0, x0, #0x280
	adr	x30, el2_test_done
	br	x0

/* (viii) stage 2 on (so HCR_EL2.RW = 1 and VM = 1), counters preset to gaps 2, traps 3, bound 1, EL1 issues the report call: x0..x4 are the answer, every other register and PSTATE are untouched, only el2_reports changes */
fix_viii:
	bl	el2_enable
	adr	x0, el2_s2_on
	ldr	w0, [x0]
	cbz	w0, el2_test_fail
	bl	fix_preset
	adr	x0, fix_viii_el1
	msr	elr_el2, x0
	mov	x0, #0x3c5			/* EL1h, DAIF masked */
	msr	spsr_el2, x0
	eret
fix_viii_el1:
	ldr	x0, =0x53454C3200000001		/* "SEL2", function 1 */
	mov	x1, #0xB1
	mov	x2, #0xB2
	mov	x3, #0xB3
	mov	x4, #0xB4
	.irp	n, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
	mov	x\n, #(0x100 + \n)		/* review el2-b2-report-r1 F8: x5..x18 hold patterns across the call */
	.endr
	hvc	#0x5345
	.irp	n, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
	cmp	x\n, #(0x100 + \n)
	b.ne	el2_test_fail
	.endr
	ldr	x19, =0x53454C32000000AA	/* answered */
	cmp	x0, x19
	b.ne	el2_test_fail
	cmp	x1, #1				/* HCR_EL2.VM */
	b.ne	el2_test_fail
	cmp	x2, #2				/* gaps */
	b.ne	el2_test_fail
	cmp	x3, #3				/* traps */
	b.ne	el2_test_fail
	cmp	x4, #1				/* bound */
	b.ne	el2_test_fail
	mrs	x7, CurrentEL
	cmp	x7, #4
	b.ne	el2_test_fail
	mrs	x7, daif
	cmp	x7, #0x3c0
	b.ne	el2_test_fail
	adr	x0, el2_reports
	ldr	w1, [x0]
	cmp	w1, #1
	b.ne	el2_test_fail
	adr	x0, el2_gaps
	ldr	w1, [x0]
	cmp	w1, #2				/* not counted as a gap */
	b.ne	el2_test_fail
	adr	x0, el2_traps
	ldr	w1, [x0]
	cmp	w1, #3
	b.ne	el2_test_fail
	adr	x0, el2_ring_n
	ldr	w1, [x0]
	cbnz	w1, el2_test_fail		/* not a ring entry */
	b	el2_test_done
	.ltorg

/* (x) stage 2 on, EL1 loads a RAM word (Normal block) and the virt PL011 UARTFR (Device block), then asks for the counters: no abort reached EL2, so gaps = traps = bound = 0, VM still 1 (review r1 F8; the
 * resident map has no trap page, so both loads must pass natively) */
fix_x:
	bl	el2_enable
	adr	x0, el2_s2_on
	ldr	w0, [x0]
	cbz	w0, el2_test_fail
	adr	x0, fix_x_el1
	msr	elr_el2, x0
	mov	x0, #0x3c5
	msr	spsr_el2, x0
	eret
fix_x_el1:
	adr	x1, ctl_word
	ldr	w2, [x1]			/* Normal block */
	ldr	x3, =0x09000018			/* PL011 UARTFR of the virt machine: a Device block */
	ldr	w4, [x3]
	mov	w5, #0x600D
	movk	w5, #0x600D, lsl #16
	cmp	w2, w5
	b.ne	el2_test_fail
	ldr	x0, =0x53454C3200000001
	hvc	#0x5345
	ldr	x19, =0x53454C32000000AA
	cmp	x0, x19
	b.ne	el2_test_fail
	cmp	x1, #1
	b.ne	el2_test_fail
	cbnz	x2, el2_test_fail		/* gaps */
	cbnz	x3, el2_test_fail		/* traps */
	cbnz	x4, el2_test_fail		/* bound */
	b	el2_test_done
	.ltorg

/* (ix) the report id with a wrong imm, then the right imm with a wrong id: both come back with x0..x4 untouched (x0 != the answer), gaps 2 -> 3 -> 4, two ring entries of kind hvc, no report counted */
fix_ix:
	bl	el2_enable
	adr	x0, el2_s2_on
	ldr	w0, [x0]
	cbz	w0, el2_test_fail
	bl	fix_preset
	adr	x0, fix_ix_el1
	msr	elr_el2, x0
	mov	x0, #0x3c5
	msr	spsr_el2, x0
	eret
fix_ix_el1:
	ldr	x0, =0x53454C3200000001
	mov	x1, #0xB1
	mov	x2, #0xB2
	mov	x3, #0xB3
	mov	x4, #0xB4
	hvc	#0x5344				/* wrong imm */
	ldr	x9, =0x53454C3200000001
	cmp	x0, x9
	b.ne	el2_test_fail
	cmp	x1, #0xB1
	b.ne	el2_test_fail
	cmp	x2, #0xB2
	b.ne	el2_test_fail
	cmp	x3, #0xB3
	b.ne	el2_test_fail
	cmp	x4, #0xB4
	b.ne	el2_test_fail
	adr	x0, el2_gaps
	ldr	w10, [x0]			/* not x1..x4: the second call checks they are still the sentinels */
	cmp	w10, #3
	b.ne	el2_test_fail
	ldr	x0, =0x53454C3200000002		/* wrong id */
	hvc	#0x5345
	ldr	x9, =0x53454C3200000002
	cmp	x0, x9
	b.ne	el2_test_fail
	cmp	x1, #0xB1
	b.ne	el2_test_fail
	cmp	x4, #0xB4
	b.ne	el2_test_fail
	adr	x0, el2_gaps
	ldr	w1, [x0]
	cmp	w1, #4
	b.ne	el2_test_fail
	adr	x0, el2_reports
	ldr	w1, [x0]
	cbnz	w1, el2_test_fail
	adr	x0, el2_ring_n
	ldr	w1, [x0]
	cmp	w1, #2
	b.ne	el2_test_fail
	adr	x0, el2_ring
	ldr	w1, [x0, #36]			/* ring[0].kind = hvc (2) */
	cmp	w1, #2
	b.ne	el2_test_fail
	b	el2_test_done
	.ltorg

/* counters for (viii)/(ix): gaps 2, traps 3, bound 1 (written at EL2 before the eret) */
fix_preset:
	adr	x0, el2_gaps
	mov	w1, #2
	str	w1, [x0]
	adr	x0, el2_traps
	mov	w1, #3
	str	w1, [x0]
	adr	x0, el2_bound
	mov	w1, #1
	str	w1, [x0]
	ret

	.data
	.balign	4
ctl_word:
	.word	0x600D600D

#endif
