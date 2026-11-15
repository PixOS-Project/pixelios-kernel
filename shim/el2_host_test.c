/*
 * Host test of the EL2 trap probe's pure logic (spec S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 6 step B3, section 3.3.8): el2_decode, el2_dispatch,
 * el2_bound_reached, el2_account. The printer has its own test (el2_print_host_test.c, B3p). The nested-fault half is B3q (QEMU), not here.
 * Build: gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -o out/el2_host_test el2.c el2_host_test.c
 * Every ESR below is a literal built by hand from the ISS layout (EC [31:26], IL 25, ISV 24, SAS [23:22], SSE 21, SRT [20:16], SF 15, FnV 10, WnR 6, FSC [5:0]),
 * not by the code under test.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "el2.h"

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); exit(1); } } while (0)

/* the pure logic must never print: any console call is a failure */
void con_str(const char *l, const char *s) { (void)l; (void)s; fprintf(stderr, "FAIL: unexpected con_str\n"); exit(1); }
void con_hex(const char *l, uint64_t v) { (void)l; (void)v; fprintf(stderr, "FAIL: unexpected con_hex\n"); exit(1); }
void con_hex2(const char *l, const char *a, uint32_t va, const char *b, uint32_t vb) { (void)l; (void)a; (void)va; (void)b; (void)vb; fprintf(stderr, "FAIL: unexpected con_hex2\n"); exit(1); }

/* ldr w1, [x0] faulting on a stage-2 level-3 translation fault: EC 0x24 | IL | ISV | SAS 2 | SRT 1 | FSC 0x07 */
#define ESR_LDRW	0x93810007u

static void test_decode(void)
{
	struct el2_dec d;

	/* the run-plan row 3/4 access: GICD_TYPER, IPA 0x10400004; HPFAR_EL2.FIPA = IPA >> 8 (nibble 0), FAR = the VA */
	d = el2_decode(ESR_LDRW, 0x104000u, 0xFFFFFFC010400004ull);
	EXPECT(d.ipa == 0x10400004ull && d.width == 4 && d.rt == 1 && !d.write && d.isv == 1 && d.sse == 0 && d.sf == 0);
	/* HPFAR bits [3:0] are not part of the address; high IPA bits survive (T1 at 0x8_8123_4000) */
	d = el2_decode(ESR_LDRW, 0x8812345u, 0x1234ull);
	EXPECT(d.ipa == 0x881234000ull + 0x234ull);
	/* FnV (bit 10) = 1: FAR is not valid, the page offset is not added */
	d = el2_decode(ESR_LDRW | (1u << 10), 0x104000u, 0x10400004ull);
	EXPECT(d.ipa == 0x10400000ull);
	/* SAS 0..3 -> width 1,2,4,8 (ldrb/ldrh/ldr w/ldr x), SRT 5/6/7/8 */
	d = el2_decode(0x93000000u | (0u << 22) | (5u << 16) | 7u, 0x104000u, 0);
	EXPECT(d.width == 1 && d.rt == 5 && d.isv == 1);
	d = el2_decode(0x93000000u | (1u << 22) | (6u << 16) | 7u, 0x104000u, 0);
	EXPECT(d.width == 2 && d.rt == 6);
	d = el2_decode(0x93000000u | (2u << 22) | (7u << 16) | 7u, 0x104000u, 0);
	EXPECT(d.width == 4 && d.rt == 7);
	d = el2_decode(0x93000000u | (3u << 22) | (8u << 16) | 7u, 0x104000u, 0);
	EXPECT(d.width == 8 && d.rt == 8);
	/* SSE bit 21 and SF bit 15 (ldrsw x2 style) */
	d = el2_decode(ESR_LDRW | (1u << 21), 0x104000u, 0);
	EXPECT(d.sse == 1 && d.sf == 0 && d.rt == 1 && d.width == 4);
	d = el2_decode(ESR_LDRW | (1u << 15), 0x104000u, 0);
	EXPECT(d.sse == 0 && d.sf == 1);
	/* SRT = 31 (xzr/wzr destination: the value is discarded, never stored into a register) */
	d = el2_decode(0x93000000u | (2u << 22) | (31u << 16) | 7u, 0x104000u, 0);
	EXPECT(d.rt == 31 && d.width == 4 && d.isv == 1);
	/* WnR bit 6 */
	d = el2_decode(ESR_LDRW | (1u << 6), 0x104000u, 0);
	EXPECT(d.write == 1);
	/* ISV = 0 (e.g. ldp): SAS/SSE/SRT/SF bits are present but not valid, nothing is reported from them; the IPA still decodes */
	d = el2_decode(0x92000000u | (3u << 22) | (1u << 21) | (9u << 16) | (1u << 15) | 7u, 0x104000u, 0x10400004ull);
	EXPECT(d.isv == 0 && d.width == 0 && d.rt == 0 && d.sse == 0 && d.sf == 0 && d.ipa == 0x10400004ull);
}

static void test_dispatch(void)
{
#ifndef EL2_RESIDENT	/* the served/NISV classes exist only in probe mode (trap targets) */
	/* class 1 (EC 0x24 / 0x20, translation fault type 0x04, level in [1:0]) */
	EXPECT(el2_dispatch(ESR_LDRW, 1) == EL2_K_SERVED);
	EXPECT(el2_dispatch(0x93810004u, 1) == EL2_K_SERVED);	/* level 0 */
	EXPECT(el2_dispatch(0x93810005u, 1) == EL2_K_SERVED);	/* level 1 */
	EXPECT(el2_dispatch(ESR_LDRW, 0) == EL2_K_GAP_EC);	/* outside the trap targets: retry once (VM clear) */
	EXPECT(el2_dispatch(0x93410007u, 1) == EL2_K_NISV);	/* ldrh (SAS 1) */
	EXPECT(el2_dispatch(0x93C10007u, 1) == EL2_K_NISV);	/* ldr x (SAS 3) */
	EXPECT(el2_dispatch(0x93010007u, 1) == EL2_K_NISV);	/* ldrb (SAS 0) */
	EXPECT(el2_dispatch(0x92810007u, 1) == EL2_K_NISV);	/* ISV = 0 */
	EXPECT(el2_dispatch(ESR_LDRW | (1u << 6), 1) == EL2_K_NISV);	/* a write to a trap page */
	EXPECT(el2_dispatch(0x82000007u, 1) == EL2_K_GAP_EC);	/* EC 0x20 (IABT_LOW) translation fault, on a target */
	EXPECT(el2_dispatch(0x82000007u, 0) == EL2_K_GAP_EC);	/* EC 0x20, outside */
#endif
#ifdef EL2_RESIDENT
	/* review el2-b2-report-r1 F2: EC 0x24/0x20 with any fault type is a VM-clear GAP (never a park), on or off a target */
	EXPECT(el2_dispatch(0x9381000Cu, 1) == EL2_K_GAP_EC);
	EXPECT(el2_dispatch(0x9200000Fu, 0) == EL2_K_GAP_EC);	/* ESR from the review: permission fault level 3 */
	EXPECT(el2_dispatch(0x93810008u, 1) == EL2_K_GAP_EC);
	EXPECT(el2_dispatch(0x8200000Cu, 0) == EL2_K_GAP_EC);
	EXPECT(el2_dispatch(0x93810021u, 0) == EL2_K_GAP_EC);	/* alignment fault 0x21 */
	/* review el2-b2-report-r2 N1: every fault status code (6 bits), both abort ECs, both target states, ISV 0/1, IL 0/1 */
	for (uint32_t ec = 0x20; ec <= 0x24; ec += 4)
		for (uint32_t fsc = 0; fsc < 64; fsc++)
			for (int on = 0; on <= 1; on++)
				for (uint32_t hi = 0; hi < 4; hi++)	/* bit 0: IL (bit 25), bit 1: ISV (bit 24) */
					EXPECT(el2_dispatch((ec << 26) | ((hi & 1u) << 25) | ((hi >> 1) << 24) | fsc, on) == EL2_K_GAP_EC);
#else
	/* fault type other than 0x04 -> park, both ECs, both target states */
	EXPECT(el2_dispatch(0x9381000Cu, 1) == EL2_K_PARK);	/* permission fault type 0x0C */
	EXPECT(el2_dispatch(0x9381000Fu, 0) == EL2_K_PARK);	/* permission fault level 3 */
	EXPECT(el2_dispatch(0x93810008u, 1) == EL2_K_PARK);	/* access flag fault type 0x08 */
	EXPECT(el2_dispatch(0x8200000Cu, 1) == EL2_K_PARK);	/* IABT, permission */
#endif
	/* 0x16: GAP + return (HVC64, imm 0 here) */
	EXPECT(el2_dispatch(0x5A000000u, 0) == EL2_K_HVC);
	EXPECT(el2_dispatch(0x5A000000u, 1) == EL2_K_HVC);
	/* every other EC parks: 0x18 (MSR/MRS trap), 0x01 (WFx), 0x00 (unknown), 0x15 (SVC64), 0x25 (DABT_CUR), 0x21 (IABT_CUR), 0x17 (SMC64), 0x3F */
	EXPECT(el2_dispatch(0x62000000u, 1) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x06000000u, 0) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x02000000u, 0) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x56000000u, 0) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x96000007u, 1) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x86000007u, 1) == EL2_K_PARK);
	EXPECT(el2_dispatch(0x5E000000u, 1) == EL2_K_PARK);
	EXPECT(el2_dispatch(0xFE000000u, 1) == EL2_K_PARK);
}

static void test_bound(void)
{
	EXPECT(!el2_bound_reached(0));
	EXPECT(!el2_bound_reached(63));
	EXPECT(el2_bound_reached(64));
	EXPECT(el2_bound_reached(65));
}

static void reset(void)
{
	memset(el2_ring, 0, sizeof el2_ring);
	el2_ring_n = el2_ring_ovf = el2_traps = el2_gaps = el2_bound = 0;
}

static struct el2_ent ent(unsigned kind, unsigned fault)
{
	struct el2_ent e;
	memset(&e, 0, sizeof e);
	e.kind = kind;
	e.fault = fault;
	e.vm = 1;
	e.ipa = 0x10400004ull;
	return e;
}

static void test_account(void)
{
	struct el2_ent e;

	reset();
	e = ent(EL2_K_SERVED, 0);
	for (unsigned i = 0; i < 4; i++)
		el2_account(&e);
	EXPECT(el2_traps == 4 && el2_gaps == 0 && el2_ring_n == 4 && !el2_ring_ovf && !el2_bound);
	/* a faulted served read: traps + 1 and gaps + 1 */
	e = ent(EL2_K_SERVED, 1);
	el2_account(&e);
	EXPECT(el2_traps == 5 && el2_gaps == 1 && el2_ring_n == 5);
	/* kinds 1-4: gaps + 1 each, traps unchanged */
	for (unsigned k = EL2_K_GAP_EC; k <= EL2_K_PARK; k++) {
		e = ent(k, 0);
		el2_account(&e);
	}
	EXPECT(el2_traps == 5 && el2_gaps == 5 && el2_ring_n == 9);
	/* the bound: its own word, gaps + 1, no ring slot */
	e = ent(EL2_K_BOUND, 0);
	el2_account(&e);
	EXPECT(el2_bound == 1 && el2_gaps == 6 && el2_ring_n == 9);
	/* fill the ring, then overflow: traps still count, gaps + 1 once for the overflow, the bound is still counted with a full ring */
	e = ent(EL2_K_SERVED, 0);
	for (unsigned i = 0; i < 7; i++)
		el2_account(&e);
	EXPECT(el2_ring_n == EL2_RING_N && !el2_ring_ovf && el2_traps == 12 && el2_gaps == 6);
	el2_account(&e);
	EXPECT(el2_ring_ovf == 1 && el2_traps == 13 && el2_gaps == 7 && el2_ring_n == EL2_RING_N);
	el2_account(&e);
	EXPECT(el2_traps == 14 && el2_gaps == 7);		/* overflow is counted once */
	e = ent(EL2_K_NISV, 0);
	el2_account(&e);
	EXPECT(el2_gaps == 8 && el2_ring_n == EL2_RING_N);	/* a kind 1-4 event with a full ring still counts */
	e = ent(EL2_K_BOUND, 0);
	el2_account(&e);
	EXPECT(el2_gaps == 9 && el2_ring_n == EL2_RING_N);
	/* the ring keeps the first 16 events, not the last */
	reset();
	for (unsigned i = 0; i < 20; i++) {
		e = ent(EL2_K_SERVED, 0);
		e.val = i;
		el2_account(&e);
	}
	EXPECT(el2_ring[0].val == 0 && el2_ring[15].val == 15 && el2_traps == 20 && el2_gaps == 1);
}

/* SPEC 3.4 (b) design item 3: the one answered hvc. ESRs hand-built: EC 0x16 [31:26] | IL (1<<25) | ISS imm16. */
#define ESR_HVC(imm)	((0x16u << 26) | (1u << 25) | (imm))
#define FN_REPORT	0x53454C3200000001ull		/* "SEL2" + function 1, typed here, not taken from el2_abi.h */

static void test_report_match(void)
{
	EXPECT(el2_report_match(ESR_HVC(0x5345), FN_REPORT));				/* right imm + id */
	EXPECT(!el2_report_match(ESR_HVC(0x5346), FN_REPORT));				/* wrong imm */
	EXPECT(!el2_report_match(ESR_HVC(0x0), FN_REPORT));
	EXPECT(!el2_report_match(ESR_HVC(0x5345), FN_REPORT + 1));			/* wrong id: function 2 */
	EXPECT(!el2_report_match(ESR_HVC(0x5345), 0x53454C32000000AAull));		/* the answer magic is not a request */
	EXPECT(!el2_report_match(ESR_HVC(0x5345), 0));
	EXPECT(!el2_report_match(ESR_HVC(0x5345), FN_REPORT | (1ull << 63)));
	EXPECT(!el2_report_match(0x5E000000u | (1u << 25) | 0x5345u, FN_REPORT));	/* EC 0x17 (smc64) with the same ISS */
	EXPECT(!el2_report_match(0x92000000u | 0x5345u, FN_REPORT));			/* EC 0x24 (data abort) with ISS[15:0] = 0x5345 */
	EXPECT(!el2_report_match(0x00005345u, FN_REPORT));				/* EC 0 */
	EXPECT(el2_report_match(ESR_HVC(0x5345) | 0x00010000u, FN_REPORT));		/* ISS bits above [15:0] are not part of the imm */
}

int main(void)
{
	test_report_match();
	test_decode();
	test_dispatch();
	test_bound();
	test_account();
	puts("el2_host_test: ALL OK");
	return 0;
}
