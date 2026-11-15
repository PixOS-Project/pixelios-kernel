/* Host test of the pure DMA block of xnu patch 0167 (sweet_wlan_dma.h, extracted from the patch by build.sh), docs/research/83. The expected numbers are written out by hand from the DHD
 * constants (dhd_msgbuf.c:3525-3565 ring depth x item size, :140 scratch, :370-372 pad, :12148-12151 index array size, dhd_pcie.c:13771 rw_index_sz), not computed by the header.
 * tools/test_wlan_dma_mutants.py builds this test against every header mutant too (CR-02). Nothing here touches hardware. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "sweet_wlan_dma.h"

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

struct want { const char *name; uint32_t len; };

static void
plan_known(uint32_t rw, uint32_t sub, uint32_t cpl, const struct want *w, uint32_t total)
{
	struct swd_plan p;
	uint32_t off = 0;
	unsigned i;

	memset(&p, 0xA5, sizeof p);
	CHECK(swd_plan_make(rw, sub, cpl, &p) == 0);
	for (i = 0; i < SWD_NBUF; i++) {
		if (i == SWD_TRAP) {
			off = (off + 4095u) / 4096u * 4096u;      /* the trap page starts at the next 4 KiB offset (R94 step 3: golden PA 4 KiB aligned) */
			CHECK(off % 4096u == 0 && off == 163840u);
		}
		if (i == SWD_EVBUF) {
			off = (off + 4095u) / 4096u * 4096u;      /* W-D4b D-10: the event buffers (W-D4e: 8 x 8192) start at the next 4 KiB offset, right after the trap page */
			CHECK(off % 4096u == 0 && off == 167936u);
		}
		if (i == SWD_RESPBUF) {
			off = (off + 4095u) / 4096u * 4096u;      /* W-D4d: the response buffer starts at the next 4 KiB offset, right after the event buffer */
			CHECK(off % 4096u == 0 && off == 233472u);
		}
		if (i == SWD_IOCTBUF) {
			off = (off + 4095u) / 4096u * 4096u;      /* W-D4d: the payload buffer: 8200 B padded to 8320, 4 KiB aligned after the response buffer */
			CHECK(off % 4096u == 0 && off == 299008u);
		}
		CHECK(strcmp(p.b[i].name, w[i].name) == 0);
		CHECK(p.b[i].len == w[i].len);
		CHECK(p.b[i].off == off);               /* contiguous, no gap, no overlap */
		CHECK(p.b[i].off % 128u == 0 && p.b[i].len % 128u == 0 && p.b[i].len != 0);
		off += w[i].len;
	}
	CHECK(p.total == total && p.total == off);
}

static void
plans(void)
{
	/* Android golden depths (R94 s.2 step 8): 128x40, 2048x32, 64x24, 2048x24, 1024x40 = 5120, 65536, 1536, 49152, 40960 = 162304. 4-byte indices, 43 / 4 rings (run F3): scratch 8->128, 172->256 (x2), 16->128 (x2), then the 4096 B trap page at 163840 */
	static const struct want w4[SWD_NBUF] = {
		{ "h2d ctrl submit", 5120 }, { "h2d rxpost", 65536 }, { "d2h ctrl complete", 1536 }, { "d2h tx complete", 49152 }, { "d2h rx complete", 40960 },
		{ "scratch", 128 }, { "idx h2d wr", 256 }, { "idx h2d rd", 256 }, { "idx d2h wr", 128 }, { "idx d2h rd", 128 }, { "trap", 4096 }, { "evbuf", 65536 }, { "respbuf", 65536 }, { "ioctbuf", 8320 },
	};
	/* 2-byte indices: 86 -> 128, 8 -> 128 */
	static const struct want w2[SWD_NBUF] = {
		{ "h2d ctrl submit", 5120 }, { "h2d rxpost", 65536 }, { "d2h ctrl complete", 1536 }, { "d2h tx complete", 49152 }, { "d2h rx complete", 40960 },
		{ "scratch", 128 }, { "idx h2d wr", 128 }, { "idx h2d rd", 128 }, { "idx d2h wr", 128 }, { "idx d2h rd", 128 }, { "trap", 4096 }, { "evbuf", 65536 }, { "respbuf", 65536 }, { "ioctbuf", 8320 },
	};
	/* 40 / 3 rings: the h2d and d2h counts must not be swapped; 32 x 4 = 128 B exactly (below) needs no extra pad */
	static const struct want w40[SWD_NBUF] = {
		{ "h2d ctrl submit", 5120 }, { "h2d rxpost", 65536 }, { "d2h ctrl complete", 1536 }, { "d2h tx complete", 49152 }, { "d2h rx complete", 40960 },
		{ "scratch", 128 }, { "idx h2d wr", 256 }, { "idx h2d rd", 256 }, { "idx d2h wr", 128 }, { "idx d2h rd", 128 }, { "trap", 4096 }, { "evbuf", 65536 }, { "respbuf", 65536 }, { "ioctbuf", 8320 },
	};
	static const struct want w32[SWD_NBUF] = {
		{ "h2d ctrl submit", 5120 }, { "h2d rxpost", 65536 }, { "d2h ctrl complete", 1536 }, { "d2h tx complete", 49152 }, { "d2h rx complete", 40960 },
		{ "scratch", 128 }, { "idx h2d wr", 128 }, { "idx h2d rd", 128 }, { "idx d2h wr", 128 }, { "idx d2h rd", 128 }, { "trap", 4096 }, { "evbuf", 65536 }, { "respbuf", 65536 }, { "ioctbuf", 8320 },
	};
	struct swd_plan p;

	plan_known(SWD_RW_INDEX_SZ, SWD_MAX_SUB, SWD_MAX_CPL, w4, 307328);
	plan_known(4, 43, 4, w4, 307328);
	plan_known(2, 43, 4, w2, 307328);
	plan_known(4, 40, 3, w40, 307328);
	plan_known(4, 32, 32, w32, 307328);              /* 128 B exactly: no extra pad */
	/* the h2d arrays follow max_sub, the d2h arrays max_cpl: 33 vs 1 gives 256 / 256 / 128 / 128, 1 vs 33 gives 128 / 128 / 256 / 256 */
	CHECK(swd_plan_make(4, 33, 1, &p) == 0 && p.b[SWD_IDX_H2D_W].len == 256 && p.b[SWD_IDX_H2D_R].len == 256 && p.b[SWD_IDX_D2H_W].len == 128 && p.b[SWD_IDX_D2H_R].len == 128);
	CHECK(swd_plan_make(4, 1, 33, &p) == 0 && p.b[SWD_IDX_H2D_W].len == 128 && p.b[SWD_IDX_H2D_R].len == 128 && p.b[SWD_IDX_D2H_W].len == 256 && p.b[SWD_IDX_D2H_R].len == 256);
	/* refusals */
	CHECK(swd_plan_make(0, 43, 4, &p) == -1 && swd_plan_make(1, 43, 4, &p) == -1 && swd_plan_make(3, 43, 4, &p) == -1 && swd_plan_make(8, 43, 4, &p) == -1);
	CHECK(swd_plan_make(4, 0, 4, &p) == -2 && swd_plan_make(4, 43, 0, &p) == -2);
	CHECK(swd_plan_make(4, SWD_MAX_RINGS, 4, &p) == 0 && swd_plan_make(4, SWD_MAX_RINGS + 1, 4, &p) == -2);
	CHECK(swd_plan_make(4, 43, SWD_MAX_RINGS, &p) == 0 && swd_plan_make(4, 43, SWD_MAX_RINGS + 1, &p) == -2);
	CHECK(swd_plan_make(4, 43, 4, 0) == -3);
	/* the block of the run (W-D4e: 8 event + 8 response buffers): 307328 B -> 19 pages of 16K = 311296; the rings alone are 162304 B */
	CHECK(swd_plan_make(4, 43, 4, &p) == 0 && p.b[SWD_H2D_CTRL].len + p.b[SWD_H2D_RXP].len + p.b[SWD_D2H_CTRL].len + p.b[SWD_D2H_TXC].len + p.b[SWD_D2H_RXC].len == 162304u);
	CHECK(SWD_PAGE == 16384u);
	CHECK(SWD_EVBUF_N == 8u && SWD_RESPBUF_N == 8u);                   /* W-D4e: 8 + 8 buffers (design s.1 item 6), the plan is 307328 B = 19 pages */
	CHECK(swd_plan_make(4, 43, 4, &p) == 0 && p.total == 307328u && p.b[SWD_EVBUF].len == 8u * SWD_EVBUF_LEN && p.b[SWD_RESPBUF].len == 8u * SWD_RESPBUF_LEN);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0 && (p.total + SWD_PAGE - 1) / SWD_PAGE == 19);
}

static void
audits(void)
{
	CHECK(swd_audit(0x880000000ull, 4096, 4096) == 0);             /* above 4 GiB: only the low 32 bits count, 0x80000000 + 4096 does not wrap */
	CHECK(swd_audit(0x80000000ull, 307328, 4096) == 0);
	CHECK(swd_audit(0x80000080ull, 128, 128) == 0);
	CHECK(swd_audit(0, 4096, 4096) == 1);
	CHECK(swd_audit(0x80000010ull, 4096, 4096) == 2);
	CHECK(swd_audit(0x80000040ull, 128, 128) == 2);
	CHECK(swd_audit(0x80000001ull, 128, 128) == 2 && swd_audit(0x80000001ull, 4096, 4096) == 2);   /* low bits below the 4-byte DHD alignment too */
	CHECK(swd_audit(0x80000080ull, 128, 4096) == 2);
	CHECK(swd_audit(0x80000000ull, 0, 4096) == 3);
	CHECK(swd_audit(0x80000000ull, 100, 4096) == 3);
	CHECK(swd_audit(0x80000000ull, 129, 4096) == 3);
	CHECK(swd_audit(0xFFFFF000ull, 8192, 4096) == 4);              /* crosses 4 GiB in the low word */
	CHECK(swd_audit(0x8FFFFF000ull, 8192, 4096) == 4);             /* the same with a non-zero high word */
	CHECK(swd_audit(0xFFFFF000ull, 4096, 4096) == 4);              /* ends at exactly 4 GiB: DHD's (uint32)(lo + len) < lo refuses it too (end 0 < lo) */
	CHECK(swd_audit(0xFFFFEF80ull, 128 * 32, 128) == 0);           /* ends at 0xFFFFFF80: ok */
	CHECK(swd_audit(0x100000000ull, 4096, 4096) == 0);             /* low word 0, high word set: pa is non-zero */
	CHECK(swd_audit(0x100000000ull, 0, 4096) == 3);
	/* the order of the rules: zero pa wins over a bad length; a misaligned pa wins over a bad length */
	CHECK(swd_audit(0, 0, 4096) == 1);
	CHECK(swd_audit(0x80000010ull, 0, 4096) == 2);
}

static void
blocks(void)
{
	struct swd_plan p;
	const uint32_t sz = 311296;
	const uint64_t pa = 0x880000000ull;

	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	CHECK(swd_contig(pa, pa + sz - SWD_PAGE, sz, SWD_PAGE) == 0);
	CHECK(swd_contig(pa, pa + sz, sz, SWD_PAGE) == 1);                  /* last page too far */
	CHECK(swd_contig(pa, pa + sz - 2 * SWD_PAGE, sz, SWD_PAGE) == 1);   /* last page too near */
	CHECK(swd_contig(pa, pa, sz, SWD_PAGE) == 1);                       /* both pages the same */
	CHECK(swd_contig(pa, pa, SWD_PAGE, SWD_PAGE) == 0);                 /* one page: first == last */
	CHECK(swd_contig(pa, pa, 0, SWD_PAGE) == 1);
	CHECK(swd_contig(pa, pa + 4096, 8192, SWD_PAGE) == 1);              /* size not a whole number of pages */
	CHECK(swd_contig(pa, pa - 8192, 8192, SWD_PAGE) == 1);              /* half a page: the compare alone would pass */
	CHECK(swd_contig(pa, pa - SWD_PAGE, 0, SWD_PAGE) == 1);             /* size 0: the compare alone would pass */
	CHECK(swd_audit_all(pa, sz, &p) == 0);
	CHECK(swd_audit_all(0, sz, &p) == 1);
	CHECK(swd_audit_all(pa + 4096, sz, &p) == 2);                       /* block not 16K aligned */
	CHECK(swd_audit_all(0xFFFFC000ull, sz, &p) == 4);                   /* block crosses 4 GiB */
	CHECK(swd_audit_all(0xFFFF8000ull, sz, &p) == 4);                   /* block crosses 4 GiB (end past) */
	CHECK(swd_audit_all(pa, 163840, &p) == 3);                          /* plan (307328) does not fit the block */
	p.b[SWD_IDX_D2H_R].len = 100;                                       /* a sub-buffer with a bad length */
	CHECK(swd_audit_all(pa, sz, &p) == 3);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	p.b[SWD_TRAP].off = 163200;                                         /* trap page 128 aligned only: refused (golden PA is 4 KiB aligned) */
	CHECK(swd_audit_all(pa, sz, &p) == 2);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	p.b[SWD_EVBUF].off = 167936 + 128;                                  /* W-D4b D-10: event buffer 128 aligned only: refused (4 KiB like the trap page) */
	CHECK(swd_audit_all(pa, sz, &p) == 2);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0 && p.b[SWD_EVBUF].len == 65536u && p.b[SWD_EVBUF].off == 167936u && p.total == 307328u);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0 && p.b[SWD_RESPBUF].len == 65536u && p.b[SWD_RESPBUF].off == 233472u && p.b[SWD_IOCTBUF].len == 8320u && p.b[SWD_IOCTBUF].off == 299008u);
	p.b[SWD_RESPBUF].off += 128;                                        /* W-D4d: response buffer 128 aligned only: refused (4 KiB) */
	CHECK(swd_audit_all(pa, sz, &p) == 2);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	p.b[SWD_IOCTBUF].off += 128;                                        /* W-D4d: payload buffer 128 aligned only: refused (4 KiB) */
	CHECK(swd_audit_all(pa, sz, &p) == 2);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	p.b[SWD_IOCTBUF].len = SWD_IOCTBUF_LEN;                             /* W-D4d: 8200 B is not a multiple of the 128 B pad: refused */
	CHECK(swd_audit_all(pa, sz, &p) == 3);
	CHECK(swd_plan_make(4, 43, 4, &p) == 0);
	p.b[SWD_SCRATCH].off = 64;                                          /* a sub-buffer pa not 128 aligned */
	CHECK(swd_audit_all(pa, sz, &p) == 2);
}

int
main(void)
{
	plans();
	audits();
	blocks();
	if (fails) {
		printf("RESULT: FAIL wlan_dma_host_test (%u checks)\n", fails);
		return 1;
	}
	printf("RESULT: PASS wlan_dma_host_test\n");
	return 0;
}
