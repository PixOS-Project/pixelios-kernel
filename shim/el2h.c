/*
 * EL2 stage-2 trap probe, EL2 glue in C (--el2-trap only): the stage-2 table builder and the entry-8 handler. Spec: S/docs/specs/SPEC-EL2-PROXY-PROBE.md DRAFT 6 section 3.3.3, 3.3.8.
 * No system register is touched here (el2.S does every msr/mrs); the only load from a computed PA is el2_ld32() in el2.S. No libc, no MMIO of its own.
 */
#ifdef EL2_TRAP

#include <stddef.h>
#include "el2.h"

#ifndef EL2_VTCR
#error "build with -include board_config.h (generated EL2_* macros, tools/boards.py)"
#endif

_Static_assert(sizeof(struct el2_frame) == EL2_FRAME_SIZE, "el2.S frame size");
_Static_assert(offsetof(struct el2_frame, elr) == EL2_FRAME_ELR, "el2.S frame ELR offset");
_Static_assert(offsetof(struct el2_frame, spsr) == EL2_FRAME_SPSR, "el2.S frame SPSR offset");
_Static_assert(offsetof(struct el2_census_regs, sctlr) == 0 && offsetof(struct el2_census_regs, hcr) == 8 && offsetof(struct el2_census_regs, vtcr) == 16 &&
	       offsetof(struct el2_census_regs, vttbr) == 24 && offsetof(struct el2_census_regs, tcr) == 32 && offsetof(struct el2_census_regs, mdcr) == 40 &&
	       offsetof(struct el2_census_regs, cptr) == 48 && offsetof(struct el2_census_regs, mmfr1) == 56, "el2.S census stores");
_Static_assert(offsetof(struct el2_s2_readback, hcr) == 0 && offsetof(struct el2_s2_readback, vtcr) == 8 && offsetof(struct el2_s2_readback, vttbr) == 16, "el2.S readback stores");
_Static_assert((1u << EL2_GRANULE_SHIFT) == EL2_POOL_PAGE, "pool page = granule");
_Static_assert(EL2_TBL_ENTRIES * 8u == EL2_POOL_PAGE, "one table = one granule");
_Static_assert((1ull << EL2_BLK_SHIFT) == (uint64_t)EL2_TBL_ENTRIES << EL2_GRANULE_SHIFT, "an L2 block = one L3 table of pages");

uint64_t el2_t1_pa, el2_t2_pa, el2_vtcr_set, el2_vttbr_set;

#define BLK_SIZE	(1ull << EL2_BLK_SHIFT)
#define PAGE_SIZE	(1ull << EL2_GRANULE_SHIFT)

static int range_in_bank(const struct el2_cfg *c, uint64_t lo, uint64_t hi)	/* [lo, hi) inside one dram bank */
{
	for (uint32_t i = 0; i < c->nbanks; i++)
		if (lo >= c->banks[i].base && hi <= c->banks[i].base + c->banks[i].size && hi > lo)
			return 1;
	return 0;
}

#ifdef EL2_RESIDENT
/* B2 / EL2_RESIDENT (SPEC 3.4 (b) design item 1 (i)): identity map of the 2048 L2 blocks only, Normal inside a dram_banks bank, Device elsewhere; no L3 table, no invalid entry, so no
 * access can fault at stage 2 and the predicted trap count is 0. The P-B5 check stays (the shim image must be in Normal memory); the T1/T2 pages do not exist (el2_t1_pa = el2_t2_pa = 0). */
const uint32_t el2_resident_build = 1;		/* marker for tools/el2_audit.py: this object set is the EL2_RESIDENT (B2) variant, audited with the resident rules */

uint64_t el2_build(uint64_t pool, uint64_t head, uint64_t end)
{
	const struct el2_cfg *c = el2_cfg_get();
	if ((pool & (EL2_POOL_PAGE - 1u)) || !range_in_bank(c, head, end)) {
		el2_fallback = EL2_FB_OUTSIDE_BANKS;
		return EL2_FB_OUTSIDE_BANKS;
	}
	uint64_t *l2 = (uint64_t *)(uintptr_t)pool;
	for (uint64_t i = 0; i < EL2_TBL_ENTRIES; i++) {
		uint64_t ipa = i << EL2_BLK_SHIFT;
		l2[i] = ipa | (range_in_bank(c, ipa, ipa + BLK_SIZE) ? EL2_DESC_BLOCK_NORMAL : EL2_DESC_BLOCK_DEVICE);
	}
	el2_t1_pa = el2_t2_pa = 0;
	el2_vtcr_set = c->vtcr;
	el2_vttbr_set = pool;				/* VTTBR_EL2 = PA of the L2 table; VMID 0, CnP 0 [SPEC 3.3.5] */
	return 0;
}
#else
uint64_t el2_build(uint64_t pool, uint64_t head, uint64_t end)
{
	const struct el2_cfg *c = el2_cfg_get();
	uint64_t t1 = pool + 3 * EL2_POOL_PAGE;		/* pool: L2, L3(T2 block), L3(T1 block), T1 page */
	uint64_t l3_t2 = pool + EL2_POOL_PAGE, l3_t1 = pool + 2 * EL2_POOL_PAGE;
	uint64_t t2i = c->t2_ipa >> EL2_BLK_SHIFT, t1i = t1 >> EL2_BLK_SHIFT;
	uint64_t t2j = (c->t2_ipa >> EL2_GRANULE_SHIFT) & (EL2_TBL_ENTRIES - 1u), t1j = (t1 >> EL2_GRANULE_SHIFT) & (EL2_TBL_ENTRIES - 1u);

	/* P-B5 [SPEC 3.1, 3.3.3]: the shim image and the T1 page must lie in Normal memory, else the EL2 map would put the code in a Device+XN block */
	if ((pool & (EL2_POOL_PAGE - 1u)) || !range_in_bank(c, head, end) || !range_in_bank(c, t1, t1 + PAGE_SIZE) ||
	    t1i >= EL2_TBL_ENTRIES || t2i >= EL2_TBL_ENTRIES || t1i == t2i) {
		el2_fallback = EL2_FB_OUTSIDE_BANKS;
		return EL2_FB_OUTSIDE_BANKS;
	}
	uint64_t *l2 = (uint64_t *)(uintptr_t)pool, *a = (uint64_t *)(uintptr_t)l3_t2, *b = (uint64_t *)(uintptr_t)l3_t1;
	for (uint64_t i = 0; i < EL2_TBL_ENTRIES; i++) {	/* L2[i] = block for IPA i << 25: Normal inside a bank, Device elsewhere [SPEC 3.3.3] */
		uint64_t ipa = i << EL2_BLK_SHIFT;
		l2[i] = ipa | (range_in_bank(c, ipa, ipa + BLK_SIZE) ? EL2_DESC_BLOCK_NORMAL : EL2_DESC_BLOCK_DEVICE);
	}
	l2[t2i] = l3_t2 | EL2_DESC_TABLE;
	l2[t1i] = l3_t1 | EL2_DESC_TABLE;
	for (uint64_t j = 0; j < EL2_TBL_ENTRIES; j++) {	/* each L3: identity pages, one invalid entry (the trap target) */
		uint64_t pa2 = (t2i << EL2_BLK_SHIFT) + (j << EL2_GRANULE_SHIFT), pa1 = (t1i << EL2_BLK_SHIFT) + (j << EL2_GRANULE_SHIFT);
		a[j] = j == t2j ? EL2_DESC_INVALID : pa2 | (range_in_bank(c, pa2, pa2 + PAGE_SIZE) ? EL2_DESC_PAGE_NORMAL : EL2_DESC_PAGE_DEVICE);
		b[j] = j == t1j ? EL2_DESC_INVALID : pa1 | (range_in_bank(c, pa1, pa1 + PAGE_SIZE) ? EL2_DESC_PAGE_NORMAL : EL2_DESC_PAGE_DEVICE);
	}
	el2_t1_pa = t1;
	el2_t2_pa = c->t2_ipa & ~(PAGE_SIZE - 1u);
	el2_vtcr_set = c->vtcr;
	el2_vttbr_set = pool;				/* VTTBR_EL2 = PA of the L2 table; VMID 0, CnP 0 [SPEC 3.3.5] */
	return 0;
}

#endif

static int on_target(uint64_t ipa)
{
#ifdef EL2_RESIDENT
	(void)ipa;
	return 0;					/* no trap page exists: any stage-2 fault is a map defect (class 1, GAP + VM clear), never an emulated read */
#else
	return (ipa >> EL2_GRANULE_SHIFT) == (el2_t1_pa >> EL2_GRANULE_SHIFT) || (ipa >> EL2_GRANULE_SHIFT) == (el2_t2_pa >> EL2_GRANULE_SHIFT);
#endif
}

unsigned el2_handle(struct el2_frame *f, uint64_t esr64, uint64_t hpfar, uint64_t far)
{
	uint32_t esr = (uint32_t)esr64;
	struct el2_dec d = el2_decode(esr, hpfar, far);
	unsigned kind = el2_dispatch(esr, on_target(d.ipa));
	if (kind == EL2_K_HVC && el2_report_match(esr, f->x[0])) {
		/* the one answered call (SPEC 3.4 (b) design items 3-4): not a ring entry, not a gap or trap; only the frame's x0-x4 change, ELR is already past the hvc */
		f->x[0] = EL2_REPORT_ANS;
		f->x[1] = el2_hcr_vm() & 1u;
		f->x[2] = el2_gaps;
		f->x[3] = el2_traps;
		f->x[4] = el2_bound;
		el2_reports++;
		return EL2_A_RETURN;
	}
	unsigned act = EL2_A_RETURN;
	int bound = 0;
	struct el2_ent e;
	e.ipa = d.ipa;
	e.elr = f->elr;
	e.val = 0;
	e.esr = esr;
	e.width = d.width;
	e.fault = 0;
	e.kind = kind;
	e.vm = 1;
	if (kind == EL2_K_SERVED) {
		/* ELR/SPSR are in the frame and ESR/FAR/HPFAR in registers already (el2.S), so a nested abort inside el2_ld32 may overwrite them [SPEC 3.3.2 steps 1-5] */
		struct el2_ld r = el2_ld32(d.ipa);
		uint64_t v = r.fault ? 0 : (uint32_t)r.val;
		e.fault = r.fault ? 1u : 0u;
		if (d.sse && d.sf)
			v = (uint64_t)(int64_t)(int32_t)(uint32_t)v;	/* sign-extend the 32-bit word into an X register [SPEC 3.3.8 Read; L mmio.c:99-108] */
		e.val = v;
		if (d.rt < 31)					/* SRT = 31: discard */
			f->x[d.rt] = v;
		f->elr += 4;					/* kvm_incr_pc: the load must not run again */
		if (el2_bound_reached(el2_traps + 1)) {		/* the 64th trap clears VM: fail open, loud [SPEC 3.3.8 Bound] */
			e.vm = 0;
			bound = 1;
			act = EL2_A_CLEAR_VM;
		}
	} else if (kind == EL2_K_GAP_EC || kind == EL2_K_NISV) {
		e.vm = 0;					/* GAP, clear VM, re-run natively: ELR not advanced */
		act = EL2_A_CLEAR_VM;
		if (kind == EL2_K_GAP_EC)
			bound = 1;				/* class 1 also sets el2_bound + kind 5 [SPEC 3.3.8 Bound; review el2-probe-job2-r1 F4] */
	} else if (kind == EL2_K_PARK) {
		act = EL2_A_PARK;
	}							/* EL2_K_HVC: GAP + return, ELR is already past the hvc, VM stays on */
	el2_account(&e);
	if (bound) {
		struct el2_ent b;
		b.ipa = 0;
		b.elr = f->elr;
		b.val = 0;
		b.esr = 0;
		b.width = 0;
		b.fault = 0;
		b.kind = EL2_K_BOUND;
		b.vm = 0;
		el2_account(&b);
	}
	return act;
}

#endif
