/*
 * Host test of the EL2 trap probe's table builder and entry-8 handler glue (el2h.c): spec S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 3.3.3 (tables, P-B5) and 3.3.8 (handler).
 * The asm (el2.S) is covered by the object audit (tools/el2_audit.py) and the QEMU test (tools/el2-qemu-test); here el2_ld32 and el2_cfg_get are fakes.
 * Build: gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -I$O -include $O/board_config.h -o $O/el2_handler_host_test el2.c el2h.c el2_handler_host_test.c
 * Expected descriptors are hand-derived from the spec's field list (valid 0, type 1, MemAttr [5:2], S2AP 6-7, SH 8-9, AF 10, XN 54), not from the generated macros.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "el2.h"

#ifdef EL2_RESIDENT	/* second compile (-DEL2_RESIDENT, SPEC 3.4 (b) item 1): only test_resident / test_report run; the probe-mode tests below are not built into main */
#pragma GCC diagnostic ignored "-Wunused-function"
#endif

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); exit(1); } } while (0)

void con_str(const char *l, const char *s) { (void)l; (void)s; fprintf(stderr, "FAIL: unexpected con_str\n"); exit(1); }
void con_hex(const char *l, uint64_t v) { (void)l; (void)v; fprintf(stderr, "FAIL: unexpected con_hex\n"); exit(1); }
void con_hex2(const char *l, const char *a, uint32_t va, const char *b, uint32_t vb) { (void)l; (void)a; (void)va; (void)b; (void)vb; fprintf(stderr, "FAIL: unexpected con_hex2\n"); exit(1); }

#define POOL	0x600000000ull			/* 24 GiB: inside the 36-bit IPA space, free in a host process; L2 index 768 */
#define BANK_SZ	0x4000000ull			/* two 32 MiB blocks */
static struct el2_bank fake_banks[1] = { { POOL, BANK_SZ } };
static struct el2_cfg fake_cfg = { fake_banks, 1, 0x8002B05Cull, 0x10400000ull };
const struct el2_cfg *el2_cfg_get(void) { return &fake_cfg; }

static int ld_calls;
static struct el2_ld ld_ret;
static uint64_t ld_last_pa;
struct el2_ld el2_ld32(uint64_t pa) { ld_calls++; ld_last_pa = pa; return ld_ret; }

#define NORMAL_BLK	0x7FDull		/* valid | MemAttr 0xF<<2 | S2AP 3<<6 | SH 3<<8 | AF 1<<10 */
#define DEVICE_BLK	0x400000000004C5ull	/* valid | MemAttr 1<<2 | S2AP 3<<6 | SH 0 | AF | XN (1<<54) */
#define NORMAL_PG	0x7FFull		/* the block value with the type bit (bit 1) set */
#define DEVICE_PG	0x400000000004C7ull

static void reset(void)
{
	memset(el2_ring, 0, sizeof el2_ring);
	el2_ring_n = el2_ring_ovf = el2_traps = el2_gaps = el2_bound = 0;
	ld_calls = 0;
	ld_ret.val = 0x79041E;
	ld_ret.fault = 0;
}

static void test_build(void)
{
	uint64_t *pool = mmap((void *)POOL, 5 * EL2_POOL_PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
	EXPECT(pool == (uint64_t *)POOL);
	EXPECT(el2_build(POOL, POOL, POOL + 5 * EL2_POOL_PAGE) == 0);
	uint64_t *l2 = pool, *l3a = pool + 2048, *l3b = pool + 4096;
	uint64_t t1 = POOL + 3 * 0x4000;
	EXPECT(el2_vttbr_set == POOL && el2_vtcr_set == 0x8002B05Cull && el2_t1_pa == t1 && el2_t2_pa == 0x10400000ull);
	/* L2: Device below the bank, Normal inside it, T2 block (8) and T1 block (POOL >> 25 = 768) are tables to their own L3 */
	EXPECT(l2[0] == DEVICE_BLK && l2[1] == (0x2000000ull | DEVICE_BLK) && l2[7] == (7ull << 25 | DEVICE_BLK) && l2[9] == (9ull << 25 | DEVICE_BLK));
	EXPECT(l2[8] == ((POOL + 0x4000) | 3) && l2[768] == ((POOL + 0x8000) | 3));
	EXPECT(l2[769] == ((769ull << 25) | NORMAL_BLK) && l2[770] == ((770ull << 25) | DEVICE_BLK) && l2[767] == ((767ull << 25) | DEVICE_BLK));
	EXPECT(l2[2047] == (2047ull << 25 | DEVICE_BLK));
	/* L3 of T2's block: every page Device identity except index (0x10400000 - 0x10000000) >> 14 = 256, which is invalid */
	EXPECT(l3a[256] == 0 && l3a[255] == ((0x10000000ull + 255 * 0x4000) | DEVICE_PG) && l3a[257] == ((0x10000000ull + 257 * 0x4000) | DEVICE_PG) && l3a[0] == (0x10000000ull | DEVICE_PG));
	/* L3 of T1's block: Normal identity pages except the T1 page ((POOL + 0xC000) >> 14) & 2047 */
	unsigned t1j = (unsigned)(((POOL + 0xC000) >> 14) & 2047);
	EXPECT(t1j == 3);				/* hand check: 0x60000C000 >> 14 = 0x18003, & 0x7FF = 3 */
	EXPECT(l3b[t1j] == 0);
	EXPECT(l3b[t1j - 1] == (((768ull << 25) + (t1j - 1) * 0x4000ull) | NORMAL_PG) && l3b[t1j + 1] == (((768ull << 25) + (t1j + 1) * 0x4000ull) | NORMAL_PG));
	int invalid = 0;
	for (unsigned j = 0; j < 2048; j++) {
		invalid += (l3a[j] == 0) + (l3b[j] == 0);
		EXPECT(l3a[j] == 0 || (l3a[j] & 0xFFFFFFFFC000ull) == 0x10000000ull + j * 0x4000ull);
	}
	EXPECT(invalid == 2);				/* exactly one invalid entry per L3 */
}

/* the failed builds leave every output untouched and name the reason */
static void test_build_refusals(void)
{
	uint64_t *pool = (uint64_t *)POOL;
	pool[0] = 0x1234;
	el2_fallback = 0;
	el2_t1_pa = 0;
	EXPECT(el2_build(POOL, POOL - 1, POOL + 5 * EL2_POOL_PAGE) == EL2_FB_OUTSIDE_BANKS && el2_fallback == EL2_FB_OUTSIDE_BANKS);	/* _head below the bank */
	el2_fallback = 0;
	EXPECT(el2_build(POOL, POOL, POOL + BANK_SZ + 1) == EL2_FB_OUTSIDE_BANKS && el2_fallback == EL2_FB_OUTSIDE_BANKS);		/* _end above the bank */
	el2_fallback = 0;
	EXPECT(el2_build(POOL + 0x1000, POOL, POOL + 5 * EL2_POOL_PAGE) == EL2_FB_OUTSIDE_BANKS);					/* pool not 16K aligned */
	el2_fallback = 0;
	fake_banks[0].size = 0x2000000ull;						/* bank shrinks to one block: T1 (+0xC000) stays inside it, an image end beyond it does not */
	EXPECT(el2_build(POOL, POOL, POOL + 0x2000001ull) == EL2_FB_OUTSIDE_BANKS);
	fake_banks[0].size = 0xC000ull;							/* the bank ends where the T1 page starts */
	EXPECT(el2_build(POOL, POOL, POOL + 0x8000) == EL2_FB_OUTSIDE_BANKS);
	fake_banks[0].size = BANK_SZ;
	EXPECT(el2_t1_pa == 0 && pool[0] == 0x1234);					/* nothing was built */
	/* T2 and T1 in one 32 MiB block cannot share an L3 */
	el2_fallback = 0;
	fake_cfg.t2_ipa = POOL + 0x100000;
	EXPECT(el2_build(POOL, POOL, POOL + 5 * EL2_POOL_PAGE) == EL2_FB_OUTSIDE_BANKS);
	fake_cfg.t2_ipa = 0x10400000ull;
	el2_fallback = 0;
}

static const uint32_t ESR_LDRW = 0x93810007u;	/* EC 0x24, IL, ISV, SAS 2, SRT 1, FSC 0x07 (L3 translation fault) */

static void frame_fill(struct el2_frame *f)
{
	for (unsigned i = 0; i < 32; i++)
		f->x[i] = 0xA000u + i;
	f->elr = 0xFFFF000000001000ull;
	f->spsr = 0x3c5;
}

static void test_served(void)
{
	struct el2_frame f, g;
	reset();
	frame_fill(&f);
	g = f;
	/* run-plan rows 3-4: GICD_TYPER at IPA 0x10400004 into w1 */
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 0xFFFFFFC010400004ull) == EL2_A_RETURN);
	EXPECT(ld_calls == 1 && ld_last_pa == 0x10400004ull);
	EXPECT(f.x[1] == 0x79041Eull && f.elr == g.elr + 4 && f.spsr == g.spsr);
	for (unsigned i = 0; i < 32; i++)
		if (i != 1)
			EXPECT(f.x[i] == g.x[i]);				/* nothing but Xt changes */
	EXPECT(el2_traps == 1 && el2_gaps == 0 && el2_ring_n == 1);
	EXPECT(el2_ring[0].kind == EL2_K_SERVED && el2_ring[0].ipa == 0x10400004ull && el2_ring[0].val == 0x79041Eull && el2_ring[0].esr == ESR_LDRW &&
	       el2_ring[0].elr == g.elr && el2_ring[0].fault == 0 && el2_ring[0].vm == 1 && el2_ring[0].width == 4);
	/* T1: the page at el2_t1_pa (set by test_build) */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, (el2_t1_pa >> 8) & ~0xFull, el2_t1_pa) == EL2_A_RETURN);
	EXPECT(ld_last_pa == el2_t1_pa && el2_ring[0].ipa == el2_t1_pa);
	/* SRT = 31: the value is discarded, no register changes (x[31] is the unused frame word) */
	reset();
	frame_fill(&f);
	g = f;
	EXPECT(el2_handle(&f, (ESR_LDRW & ~(0x1Fu << 16)) | (31u << 16), 0x104000u, 0x10400004ull) == EL2_A_RETURN);
	for (unsigned i = 0; i < 32; i++)
		EXPECT(f.x[i] == g.x[i]);
	EXPECT(f.elr == g.elr + 4 && el2_traps == 1);
	/* SRT = 30 reaches x30; SRT = 0 reaches x0 */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, (ESR_LDRW & ~(0x1Fu << 16)) | (30u << 16), 0x104000u, 4) == EL2_A_RETURN && f.x[30] == 0x79041Eull && f.x[31] == 0xA000u + 31);
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW & ~(0x1Fu << 16), 0x104000u, 4) == EL2_A_RETURN && f.x[0] == 0x79041Eull);
	/* SSE + SF: sign-extend a word with bit 31 into the X register; SSE without SF: zero-extend to 32 bits; neither: plain */
	ld_ret.val = 0x80000001u;
	reset();
	ld_ret.val = 0x80000001u;
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW | (1u << 21) | (1u << 15), 0x104000u, 4) == EL2_A_RETURN && f.x[1] == 0xFFFFFFFF80000001ull);
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW | (1u << 21), 0x104000u, 4) == EL2_A_RETURN && f.x[1] == 0x80000001ull);
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_RETURN && f.x[1] == 0x80000001ull);
	/* a faulted emulated load: Xt = 0 whatever the helper returned in w0, fault = 1 in the ring, gaps + 1, traps + 1 */
	reset();
	ld_ret.val = 0xDEAD;
	ld_ret.fault = 1;
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_RETURN && f.x[1] == 0 && f.elr == 0xFFFF000000001004ull);
	EXPECT(el2_traps == 1 && el2_gaps == 1 && el2_ring[0].fault == 1 && el2_ring[0].val == 0 && el2_ring[0].kind == EL2_K_SERVED);
}

static void test_classes(void)
{
	struct el2_frame f, g;
	/* class 1: translation fault outside both targets: GAP, clear VM, ELR not advanced, no emulated load */
	reset();
	frame_fill(&f);
	g = f;
	EXPECT(el2_handle(&f, ESR_LDRW, 0x300000u, 0x30000000ull) == EL2_A_CLEAR_VM && ld_calls == 0 && f.elr == g.elr && f.x[1] == g.x[1]);
	EXPECT(el2_gaps == 2 && el2_bound == 1 && el2_traps == 0 && el2_ring[0].kind == EL2_K_GAP_EC && el2_ring[0].vm == 0 && el2_ring[0].ipa == 0x30000000ull);	/* + kind 5 bound [SPEC 3.3.8 Bound, review r1 F4] */
	/* EC 0x20 (instruction abort) on a trap target is class 1 too [job-1 ambiguity, kept] */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, 0x82000007u, 0x104000u, 0) == EL2_A_CLEAR_VM && el2_ring[0].kind == EL2_K_GAP_EC && el2_bound == 1 && ld_calls == 0);
	/* on a target but ISV = 0 / SAS != 2 / WnR = 1: nisv GAP, clear VM, no load */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, 0x92000007u, 0x104000u, 4) == EL2_A_CLEAR_VM && el2_ring[0].kind == EL2_K_NISV && el2_ring[0].vm == 0 && el2_bound == 0 && ld_calls == 0 && f.elr == 0xFFFF000000001000ull);	/* only class 1 sets the bound */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, (ESR_LDRW & ~(3u << 22)) | (3u << 22), 0x104000u, 4) == EL2_A_CLEAR_VM && el2_ring[0].kind == EL2_K_NISV);	/* SAS 3 */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW | (1u << 6), 0x104000u, 4) == EL2_A_CLEAR_VM && el2_ring[0].kind == EL2_K_NISV && ld_calls == 0);	/* a write */
	/* HVC: GAP + return, ELR untouched (already past the hvc), VM stays on, nothing cleared */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, 0x5A000000u, 0, 0) == EL2_A_RETURN && el2_ring[0].kind == EL2_K_HVC && el2_ring[0].vm == 1 && el2_ring[0].elr == 0xFFFF000000001000ull && f.elr == 0xFFFF000000001000ull && el2_gaps == 1);
	/* any other EC, or a fault type other than translation: park */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, 0x62000000u, 0, 0) == EL2_A_PARK && el2_ring[0].kind == EL2_K_PARK && el2_gaps == 1);	/* EC 0x18 (msr/mrs) */
	reset();
	EXPECT(el2_handle(&f, 0x9381000Cu, 0x104000u, 4) == EL2_A_PARK && el2_ring[0].kind == EL2_K_PARK);			/* permission fault type 0x0C */
	reset();
	EXPECT(el2_handle(&f, 0x82000010u, 0x104000u, 4) == EL2_A_PARK && ld_calls == 0);					/* EC 0x20, FSC 0x10: external abort */
}

uint64_t fake_vm;
uint64_t el2_hcr_vm(void) { return fake_vm; }

#define ESR_HVC(imm)	((0x16u << 26) | (1u << 25) | (imm))
#define FN_REPORT	0x53454C3200000001ull
#define ANS		0x53454C32000000AAull

static void test_report(void)
{
	struct el2_frame f, g;
	/* the answered call: x0..x4 written, every other frame word and ELR/SPSR unchanged, counters and ring unchanged except el2_reports */
	reset();
	el2_reports = 0;
	el2_traps = 7;
	el2_gaps = 3;
	el2_bound = 1;
	el2_ring_n = 2;
	fake_vm = 1;
	frame_fill(&f);
	f.x[0] = FN_REPORT;
	g = f;
	EXPECT(el2_handle(&f, ESR_HVC(0x5345), 0, 0) == EL2_A_RETURN);
	EXPECT(f.x[0] == ANS && f.x[1] == 1 && f.x[2] == 3 && f.x[3] == 7 && f.x[4] == 1);
	EXPECT(f.elr == g.elr && f.spsr == g.spsr);
	for (int i = 5; i < 32; i++)
		EXPECT(f.x[i] == g.x[i]);
	EXPECT(el2_traps == 7 && el2_gaps == 3 && el2_bound == 1 && el2_ring_n == 2 && el2_ring_ovf == 0 && el2_reports == 1 && ld_calls == 0);
	fake_vm = 0x3;						/* only bit 0 is VM (the stub returns it masked; the handler masks again) */
	f.x[0] = FN_REPORT;
	EXPECT(el2_handle(&f, ESR_HVC(0x5345), 0, 0) == EL2_A_RETURN && f.x[1] == 1 && el2_reports == 2);
	fake_vm = 0;						/* stage 2 dropped: VM = 0 is reported, not hidden */
	f.x[0] = FN_REPORT;
	EXPECT(el2_handle(&f, ESR_HVC(0x5345), 0, 0) == EL2_A_RETURN && f.x[0] == ANS && f.x[1] == 0);
	/* wrong imm / wrong id / other EC: today's GAP + return with the frame untouched (x0 is NOT the answer) */
	for (int k = 0; k < 2; k++) {
		reset();
		el2_reports = 0;
		frame_fill(&f);
		f.x[0] = k ? FN_REPORT + 1 : FN_REPORT;
		g = f;
		EXPECT(el2_handle(&f, ESR_HVC(k ? 0x5345 : 0x5344), 0, 0) == EL2_A_RETURN);
		EXPECT(f.x[0] == g.x[0] && f.x[1] == g.x[1] && f.x[2] == g.x[2] && f.x[3] == g.x[3] && f.x[4] == g.x[4] && f.elr == g.elr);
		EXPECT(el2_gaps == 1 && el2_ring_n == 1 && el2_ring[0].kind == EL2_K_HVC && el2_reports == 0);
	}
}

#ifdef EL2_RESIDENT
/* EL2_RESIDENT: 2048 L2 blocks, nothing else; the L3 areas of the pool stay as the test left them (0xA5), no trap page */
static void test_resident(void)
{
	uint64_t *pool = mmap((void *)POOL, 5 * EL2_POOL_PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
	EXPECT(pool == (uint64_t *)POOL);
	memset(pool + 2048, 0xA5, 4 * EL2_POOL_PAGE);
	fake_cfg.t2_ipa = 0x10400000ull;
	el2_fallback = 0;
	EXPECT(el2_build(POOL, POOL, POOL + 5 * EL2_POOL_PAGE) == 0);
	for (uint64_t i = 0; i < 2048; i++) {
		uint64_t ipa = i << 25;
		uint64_t want = ipa | ((ipa >= POOL && ipa + (1ull << 25) <= POOL + BANK_SZ) ? NORMAL_BLK : DEVICE_BLK);
		EXPECT(pool[i] == want);			/* no table descriptor (type 3 with a table address) and no invalid (0) entry anywhere */
		EXPECT(pool[i] != 0 && (pool[i] & 3) == 1);	/* valid block */
	}
	EXPECT(pool[2048] == 0xA5A5A5A5A5A5A5A5ull && pool[4 * 2048 - 1] == 0xA5A5A5A5A5A5A5A5ull);	/* no L3 written */
	EXPECT(el2_t1_pa == 0 && el2_t2_pa == 0 && el2_vttbr_set == POOL && el2_vtcr_set == 0x8002B05Cull);
	/* P-B5 stays: image outside the bank, unaligned pool */
	el2_fallback = 0;
	EXPECT(el2_build(POOL, POOL - 1, POOL + 5 * EL2_POOL_PAGE) == EL2_FB_OUTSIDE_BANKS);
	EXPECT(el2_build(POOL + 0x1000, POOL, POOL + 5 * EL2_POOL_PAGE) == EL2_FB_OUTSIDE_BANKS);
	el2_fallback = 0;
	/* any stage-2 fault is a defect, never an emulated read: GICD page (the probe-mode T2) is class 1 here, no el2_ld32 call */
	struct el2_frame f;
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_CLEAR_VM && ld_calls == 0 && el2_ring[0].kind == EL2_K_GAP_EC && el2_traps == 0);	/* review el2-b2-report-r1 F2: a permission fault (EC 0x24, FSC 0x0F) clears VM and counts a gap, it never parks */
	reset();
	frame_fill(&f);
	EXPECT(el2_handle(&f, 0x9200000Full, 0x104000u, 4) == EL2_A_CLEAR_VM && el2_ring[0].kind == EL2_K_GAP_EC && el2_gaps == 2);	/* gap + the class-1 bound word */
}
#endif

static void test_bound(void)
{
	struct el2_frame f;
	reset();
	for (unsigned i = 1; i < EL2_BOUND_TRAPS; i++) {
		frame_fill(&f);
		EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_RETURN);
	}
	EXPECT(el2_traps == 63 && el2_bound == 0 && el2_gaps == 1 && el2_ring_ovf == 1);		/* the ring overflowed once (17th event): that one gap */
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_CLEAR_VM);				/* the 64th trap: clear VM, loud */
	EXPECT(el2_traps == 64 && el2_bound == 1 && el2_gaps == 2 && f.x[1] == 0x79041Eull && f.elr == 0xFFFF000000001004ull);
	/* with an empty ring the 64th served read is entry 1 with vm = 0 and the bound word is set (kind 5 never takes a slot) */
	reset();
	el2_traps = EL2_BOUND_TRAPS - 1;
	frame_fill(&f);
	EXPECT(el2_handle(&f, ESR_LDRW, 0x104000u, 4) == EL2_A_CLEAR_VM && el2_ring_n == 1 && el2_ring[0].vm == 0 && el2_bound == 1 && el2_gaps == 1);
	EXPECT(el2_traps == EL2_BOUND_TRAPS);
}

int main(void)
{
#ifdef EL2_RESIDENT
	test_resident();
	test_report();
#else
	test_build();
	test_build_refusals();
	test_served();
	test_classes();
	test_report();
	test_bound();
#endif
	#ifdef EL2_RESIDENT
	printf("PASS el2_handler_host_test (EL2_RESIDENT): L2-only identity map (no L3, no invalid entry, P-B5 kept), faults are class 1, hvc report frame\n");
	return 0;
#endif
	printf("PASS el2_handler_host_test: table builder (L2/L3 descriptors, one invalid page per L3, P-B5 refusals) and entry-8 handler (served read incl. SRT 31/SSE/SF/fault, classes 1-3, bound)\n");
	return 0;
}
