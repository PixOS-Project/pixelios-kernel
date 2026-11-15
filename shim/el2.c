/*
 * EL2 stage-2 trap probe, C side (--el2-trap only). Spec: S/docs/specs/SPEC-EL2-PROXY-PROBE.md DRAFT 6 section 3.3.8-3.3.9.
 * Pure functions (decode, dispatch, account) and the EL1 printer; no MMIO, no system registers, no libc.
 * The printer uses only con_hex / con_hex2 / con_str (shim/console.c:239-292): `label <16 hex>`, `label a<8 hex> b<8 hex>`, `label s`.
 */
#ifdef EL2_TRAP

#include "console.h"
#include "el2.h"

/* ESR_ELx fields [SPEC 3.3.8 "Trap handler"; L arch/arm64/include/asm/esr.h:33,43,47,71-73,85-86,103-104,112,118,131-140] */
#define EC_HVC64	0x16u
#define EC_IABT_LOW	0x20u
#define EC_DABT_LOW	0x24u
#define FSC_TYPE_MASK	0x3Cu	/* FSC [5:2] = type, [1:0] = level (ESR_ELx_FSC_FAULT 0x04 = translation, level in bits [1:0]) */
#define FSC_TRANSLATION	0x04u
#define SAS_WORD	2u	/* B1 emulates 32-bit loads only [SPEC 3.3.8 class 1] */

struct el2_ent el2_ring[EL2_RING_N];
uint32_t el2_ring_n, el2_ring_ovf, el2_traps, el2_gaps, el2_bound, el2_fallback, el2_census_ok, el2_s2_on, el2_reports;
struct el2_census_regs el2_cen;
struct el2_s2_readback el2_rb;

struct el2_dec el2_decode(uint32_t esr, uint64_t hpfar, uint64_t far)
{
	struct el2_dec d;
	d.ipa = (hpfar & ~0xFull) << 8;		/* (HPFAR_EL2 & ~0xF) << 8 [SPEC 3.3.8, L kvm_arm.h:377] */
	if (!((esr >> 10) & 1u))		/* + FAR_EL2 & 0xFFF when ESR.FnV (bit 10) = 0 */
		d.ipa |= far & 0xFFFull;
	d.isv = (esr >> 24) & 1u;
	d.write = (esr >> 6) & 1u;
	d.width = 0;
	d.rt = 0;
	d.sse = 0;
	d.sf = 0;
	if (d.isv) {				/* the syndrome fields are valid only with ISV = 1 [SPEC 3.3.8 "If ISV = 0 ... no valid syndrome"] */
		d.width = 1u << ((esr >> 22) & 3u);
		d.sse = (esr >> 21) & 1u;
		d.rt = (esr >> 16) & 0x1Fu;
		d.sf = (esr >> 15) & 1u;
	}
	return d;
}

unsigned el2_dispatch(uint32_t esr, int on_target)
{
	uint32_t ec = esr >> 26;
	if (ec == EC_HVC64)
		return EL2_K_HVC;
	if (ec != EC_DABT_LOW && ec != EC_IABT_LOW)
		return EL2_K_PARK;			/* any other EC: park (a trapped instruction would trap again forever) */
#ifdef EL2_RESIDENT
	/* review el2-b2-report-r1 F2: under XNU a stage-2 abort of any fault type (permission, access flag, alignment) is a map defect: VM clear + GAP, never a park (a park needs Power+VolDown) */
	(void)on_target;
	return EL2_K_GAP_EC;
#else
	if ((esr & FSC_TYPE_MASK) != FSC_TRANSLATION)
		return EL2_K_PARK;			/* EC 0x24/0x20 with a fault type other than 0x04 */
	if (!on_target || ec != EC_DABT_LOW)
		return EL2_K_GAP_EC;			/* class 1: translation fault outside the targets (or an instruction fetch from one) */
	struct el2_dec d = el2_decode(esr, 0, 0);
	if (!d.isv || d.width != (1u << SAS_WORD) || d.write)
		return EL2_K_NISV;			/* on a target but ISV = 0, SAS != 2 or WnR = 1 */
	return EL2_K_SERVED;
#endif
}

int el2_report_match(uint32_t esr, uint64_t x0)
{
	return (esr >> 26) == EC_HVC64 && (esr & 0xFFFFu) == EL2_HVC_IMM_REPORT && x0 == EL2_REPORT_FN;
}

int el2_bound_reached(uint32_t traps)
{
	return traps >= EL2_BOUND_TRAPS;
}

void el2_account(const struct el2_ent *e)
{
	if (e->kind == EL2_K_BOUND) {			/* its own word, never a ring slot; always counted */
		el2_bound = 1;
		el2_gaps++;
		return;
	}
	if (e->kind == EL2_K_SERVED)
		el2_traps++;				/* even when the ring is full */
	if (e->kind >= EL2_K_GAP_EC && e->kind <= EL2_K_PARK)
		el2_gaps++;
	if (e->kind == EL2_K_SERVED && e->fault)
		el2_gaps++;
	if (el2_ring_n < EL2_RING_N) {
		struct el2_ent *r = &el2_ring[el2_ring_n++];	/* by hand: a struct assignment compiles to a memcpy call and the shim has no libc (acpm.c:729) */
		r->ipa = e->ipa;
		r->elr = e->elr;
		r->val = e->val;
		r->esr = e->esr;
		r->width = e->width;
		r->fault = e->fault;
		r->kind = e->kind;
		r->vm = e->vm;
	} else if (!el2_ring_ovf) {
		el2_ring_ovf = 1;
		el2_gaps++;				/* once, when the ring overflowed */
	}
}

/* "<pre>NN<suf>" with NN = 2 uppercase hex digits of n (1..16 print 01..10) */
static void lab(char *dst, const char *pre, unsigned n, const char *suf)
{
	static const char hex[] = "0123456789ABCDEF";
	/* bounded: pre is a string literal, ends at its NUL */
	while (*pre)
		*dst++ = *pre++;
	*dst++ = hex[(n >> 4) & 0xF];
	*dst++ = hex[n & 0xF];
	/* bounded: suf is a string literal, ends at its NUL */
	while (*suf)
		*dst++ = *suf++;
	*dst = 0;
}

static void print_one(unsigned n, const struct el2_ent *e)
{
	char l[40];
	uint32_t ec = e->esr >> 26;
	if (e->kind == EL2_K_SERVED) {
		lab(l, "EL2 TRAP n=", n, " ipa");
		con_hex(l, e->ipa);
		lab(l, "EL2 TRAP n=", n, " esr");
		con_hex(l, e->esr);
		lab(l, "EL2 TRAP n=", n, " val");
		con_hex(l, e->val);
		lab(l, "EL2 TRAP n=", n, " syn");
		con_hex2(l, "ec=", ec, "isv=", (e->esr >> 24) & 1u);
		lab(l, "EL2 TRAP n=", n, " sz");
		con_hex2(l, "sas=", (e->esr >> 22) & 3u, "dfsc=", e->esr & 0x3Fu);
		if (e->fault) {
			lab(l, "EL2 GAP n=", n, " fault");
			con_hex(l, e->ipa);
		}
	} else if (e->kind == EL2_K_GAP_EC) {
		lab(l, "EL2 GAP n=", n, "");
		con_hex2(l, "ec=", ec, "ipa=", (uint32_t)e->ipa);
		lab(l, "EL2 GAP n=", n, " ipa64");
		con_hex(l, e->ipa);
	} else if (e->kind == EL2_K_HVC) {
		lab(l, "EL2 GAP n=", n, " hvc");
		con_hex(l, e->elr);
	} else if (e->kind == EL2_K_NISV) {
		lab(l, "EL2 GAP n=", n, " nisv");
		con_hex(l, e->ipa);
	} else {					/* kind 4 (park) cannot reach EL1; anything else is a bug: say so instead of printing nothing */
		lab(l, "EL2 GAP n=", n, "");
		con_hex2(l, "kind=", e->kind, "esr=", e->esr);
	}
	if (!e->vm) {
		lab(l, "EL2 GAP n=", n, "");
		con_str(l, "vm cleared");
	}
}

void el2_print_resident(uint64_t bootflags)
{
	con_hex("EL2 RESIDENT bootflags", bootflags);
}

void el2_print_ring(void)
{
	for (unsigned i = 0; i < el2_ring_n && i < EL2_RING_N; i++)
		print_one(i + 1, &el2_ring[i]);
	if (el2_ring_ovf)
		con_str("EL2 GAP", "ring full");
	if (el2_bound)
		con_str("EL2 GAP", "bound");
}

void el2_print_report(void)
{
	if (!el2_census_ok) {
		con_str("EL2 GAP:", "census not taken");
		return;
	}
	con_hex("EL2 CENSUS sctlr", el2_cen.sctlr);
	con_hex("EL2 CENSUS hcr", el2_cen.hcr);
	con_hex("EL2 CENSUS vtcr", el2_cen.vtcr);
	con_hex("EL2 CENSUS vttbr", el2_cen.vttbr);
	con_hex("EL2 CENSUS tcr", el2_cen.tcr);
	con_hex("EL2 CENSUS mdcr", el2_cen.mdcr);
	con_hex("EL2 CENSUS cptr", el2_cen.cptr);
	con_hex("EL2 CENSUS mmfr1", el2_cen.mmfr1);
	if (el2_fallback == EL2_FB_SCTLR_M) {
		con_str("EL2 GAP:", "SCTLR_EL2.M=1");
		return;
	}
	if (el2_fallback == EL2_FB_MDCR) {
		con_str("EL2 GAP:", "MDCR_EL2 traps");
		return;
	}
	if (el2_fallback == EL2_FB_OUTSIDE_BANKS) {
		con_str("EL2 GAP:", "shim outside dram_banks");
		return;
	}
	if (el2_fallback != EL2_FB_NONE) {
		con_hex("EL2 GAP: unknown fallback", el2_fallback);
		return;
	}
	if (!el2_s2_on) {
		con_str("EL2 GAP:", "stage 2 not enabled");
		return;
	}
	con_hex("EL2 S2 ON hcr", el2_rb.hcr);
	con_hex("EL2 S2 ON vtcr", el2_rb.vtcr);
	con_hex("EL2 S2 ON vttbr", el2_rb.vttbr);
	el2_print_ring();
	con_hex2("EL2 RESULT", "traps=", el2_traps, "gaps=", el2_gaps);
}

#endif
