/* Host unit test for the pure Mali VA allocator and page-table builder of the Pixel 6a GPU driver (xnu patch 0104, bsd/dev/sweet_mali_pt.h).
 * shim/build.sh extracts that header from the patch, so the kernel and this test see the same bytes. Build (also run by shim/build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Iout -o out/mali_pt_host_test mali_pt_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sweet_mali_pt.h"

static int allocs, fail_after = -1;

static uint64_t *
talloc(void *ctx, uint64_t *pa)
{
	(void)ctx;
	if (fail_after >= 0 && allocs >= fail_after) {
		return NULL;
	}
	allocs++;
	uint64_t *t = aligned_alloc(4096, 4096);
	assert(t);
	memset(t, 0, 4096);
	*pa = (uint64_t)(uintptr_t)t & 0x000000FFFFFFF000ull;   /* the PA is only a number here */
	return t;
}

int
main(void)
{
	/* the entry formats equal the values observed on hardware (docs/research/28 s.11) */
	assert(MALI_PTE_TABLE == 0x403ull);
	assert(mali_pte_leaf(0, MALI_F_EXEC) == 0x457ull);
	assert(mali_pte_leaf(0x80001000ull, 0) == (0x80001000ull | 0x457ull | MALI_PTE_NX));

	static struct mali_va v;
	assert(mali_va_alloc(&v, 0, 1) == 0 && mali_va_alloc(&v, 1, 3) == 0);
	uint64_t a = mali_va_alloc(&v, 4, 1), b = mali_va_alloc(&v, 1, 1), c = mali_va_alloc(&v, 16, 16);
	assert(a == MALI_VA_START && b == a + 4 * MALI_PG);
	assert(c && (c - MALI_VA_START) % (16 * MALI_PG) == 0 && c >= b + MALI_PG);
	assert(mali_va_free(&v, b, 2) == -1);                    /* second page was never allocated: nothing changes */
	assert(mali_va_test(&v, 4) == 1);
	assert(mali_va_free(&v, a, 4) == 0 && mali_va_free(&v, a, 4) == -1);
	assert(mali_va_free(&v, 0x1000, 1) == -1 && mali_va_free(&v, MALI_VA_END, 1) == -1);
	assert(mali_va_alloc(&v, 4, 1) == a);                     /* freed hole is reused after the hint rewinds */
	/* exhaustion and wrap: fill everything, free one hole in the middle, get exactly it back */
	static struct mali_va f;
	uint64_t whole = mali_va_alloc(&f, (uint32_t)MALI_VA_PAGES, 1);
	assert(whole == MALI_VA_START && mali_va_alloc(&f, 1, 1) == 0);
	assert(mali_va_free(&f, MALI_VA_START + 777 * MALI_PG, 3) == 0);
	assert(mali_va_alloc(&f, 4, 1) == 0 && mali_va_alloc(&f, 3, 1) == MALI_VA_START + 777 * MALI_PG);

	struct mali_pt p;
	assert(mali_pt_init(&p, (struct mali_pt_ops){ talloc, NULL }) == 0);
	assert(p.l0[0] == (p.l1_pa | 0x403ull));
	assert(mali_pt_map(&p, MALI_VA_START, 0x880001000ull, MALI_F_EXEC) == 0);
	assert(mali_pt_lookup(&p, MALI_VA_START + 0x123) == 0x880001123ull);
	assert(mali_pt_map(&p, MALI_VA_START, 0x880002000ull, 0) == -3);                  /* double map */
	assert(mali_pt_map(&p, MALI_VA_START + 1, 0x880002000ull, 0) == -1);              /* unaligned VA */
	assert(mali_pt_map(&p, MALI_VA_START - MALI_PG, 0x880002000ull, 0) == -1);        /* reserved window */
	assert(mali_pt_map(&p, MALI_VA_END, 0x880002000ull, 0) == -1);
	assert(mali_pt_map(&p, MALI_VA_START + MALI_PG, 1ull << 40, 0) == -1);            /* PA beyond 40 bits */
	/* a VA in the last gigabyte uses L2[3] and a different L3 */
	uint64_t hi = MALI_VA_END - MALI_PG;
	assert(mali_pt_map(&p, hi, 0x880003000ull, 0) == 0 && p.l2[3] && p.l1[3] == (p.l2_pa[3] | 0x403ull));
	assert(mali_pt_lookup(&p, hi) == 0x880003000ull);
	assert((p.l3[(uint32_t)(hi >> 21)][511] & MALI_PTE_NX) != 0);                     /* not executable */
	assert((p.l3[(uint32_t)(MALI_VA_START >> 21)][(MALI_VA_START >> 12) & 511] & MALI_PTE_NX) == 0);
	assert(mali_pt_unmap(&p, hi) == 0x880003000ull && mali_pt_unmap(&p, hi) == 0 && mali_pt_lookup(&p, hi) == 0);
	assert(mali_pt_map(&p, hi, 0x880004000ull, 0) == 0);                              /* remap after unmap */
	/* table allocation failure is reported, not hidden */
	struct mali_pt q;
	fail_after = allocs + 2;
	assert(mali_pt_init(&q, (struct mali_pt_ops){ talloc, NULL }) == 0);
	assert(mali_pt_map(&q, MALI_VA_START, 0x880001000ull, 0) == -2);
	printf("mali_pt_host_test: PASS\n");
	return 0;
}
