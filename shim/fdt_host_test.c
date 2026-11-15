#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fdt.h"
#include "fdt_test_builder.h"

static int gap_lines;	/* ALL-ON T-06: 'FDT GAP' lines printed by fdt.c */
void con_str(const char *label, const char *s) { (void)s; if (!strncmp(label, "FDT GAP", 7)) gap_lines++; }
void con_hex(const char *label, uint64_t v) { (void)label; (void)v; }

/* The builder now lives in fdt_test_builder.h (shared with
 * pstore_host_test.c); these aliases keep the test bodies unchanged. */
#define b_init fb_init
#define b_put32 fb_put32
#define b_begin_node fb_begin_node
#define b_end_node fb_end_node
#define b_prop fb_prop
#define b_prop_u32 fb_prop_u32
#define b_finish fb_finish
#define b_add_string fb_add_string
#define align4 fb_align4

static void test_original_case(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_begin_node(&b, "memory@880000000");
	uint8_t reg[16] = {
		0x00, 0x00, 0x00, 0x08, 0x80, 0x00, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00
	};
	b_prop(&b, "reg", reg, 16);
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	assert(fdt_memory_contains(fdt, 0x880000000ULL, 0x80000000ULL));
	assert(fdt_memory_contains(fdt, 0x880001000ULL, 0x1000));
	assert(!fdt_memory_contains(fdt, 0x80000000ULL, 0x80000000ULL));
	assert(!fdt_memory_contains(fdt, 0x8ffffffffULL, 2));
	assert(!fdt_memory_contains(0, 0x880000000ULL, 0x1000));
}

/* 1. 4x12-byte #size-cells=<1> memory case (gs101 layout: #address-cells=<2>, #size-cells=<1>) */
static void test_gs101_memory_cells(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	b_begin_node(&b, "memory@80000000");
	/* 4 banks x 12 bytes each = 48 bytes */
	uint8_t reg48[48] = {
		/* Bank 0: base 0x00000000_80000000, size 0x80000000 */
		0x00, 0x00, 0x00, 0x00,  0x80, 0x00, 0x00, 0x00,  0x80, 0x00, 0x00, 0x00,
		/* Bank 1: base 0x00000008_80000000, size 0x80000000 */
		0x00, 0x00, 0x00, 0x08,  0x80, 0x00, 0x00, 0x00,  0x80, 0x00, 0x00, 0x00,
		/* Bank 2: base 0x00000009_00000000, size 0x80000000 */
		0x00, 0x00, 0x00, 0x09,  0x00, 0x00, 0x00, 0x00,  0x80, 0x00, 0x00, 0x00,
		/* Bank 3: base 0x00000009_80000000, size 0x80000000 */
		0x00, 0x00, 0x00, 0x09,  0x80, 0x00, 0x00, 0x00,  0x80, 0x00, 0x00, 0x00,
	};
	b_prop(&b, "reg", reg48, sizeof(reg48));
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	/* Check every bank */
	assert(fdt_memory_contains(fdt, 0x80000000ULL, 0x80000000ULL));
	assert(fdt_memory_contains(fdt, 0x880000000ULL, 0x80000000ULL));
	assert(fdt_memory_contains(fdt, 0x900000000ULL, 0x80000000ULL));
	assert(fdt_memory_contains(fdt, 0x980000000ULL, 0x80000000ULL));
	/* Spanning across boundary of bank 0 and bank 1 should fail */
	assert(!fdt_memory_contains(fdt, 0x80000000ULL, 0x80000001ULL));
	/* Beyond last bank should fail */
	assert(!fdt_memory_contains(fdt, 0xa00000000ULL, 0x1000));
}

/* 2. Reserved-memory node with different cells from root */
static void test_reserved_memory_cells(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1); /* Root is 2/1 */

	b_begin_node(&b, "reserved-memory");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 2); /* /reserved-memory overrides to 2/2 */

	b_begin_node(&b, "cont_splash_mem@fac00000");
	uint8_t reg16[16] = {
		0x00, 0x00, 0x00, 0x00,  0xfa, 0xc0, 0x00, 0x00, /* base 0xfac00000 */
		0x00, 0x00, 0x00, 0x00,  0x01, 0x00, 0x00, 0x00  /* size 16MB */
	};
	b_prop(&b, "reg", reg16, sizeof(reg16));
	b_end_node(&b);

	b_end_node(&b); /* reserved-memory */
	b_end_node(&b); /* root */
	uintptr_t fdt = b_finish(&b);

	struct fdt_video vid = { 0 };
	assert(fdt_framebuffer(fdt, &vid) == 1);
	assert(vid.base == 0xfac00000ULL);
	assert(vid.size == 0x01000000ULL);
}

/* 3. Sibling-state leak case for framebuffer finder */
static void test_framebuffer_sibling_leak(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_begin_node(&b, "chosen");

	/*
	 * Sibling 1: framebuffer@0 has simple-framebuffer compatible, but NO reg
	 * (like mainline gs101).
	 */
	b_begin_node(&b, "framebuffer@0");
	b_prop(&b, "compatible", "simple-framebuffer", 19);
	b_end_node(&b);

	/*
	 * Sibling 2: other_device has a reg, but is NOT simple-framebuffer.
	 * If simple-framebuffer or found state leaks, it would erroneously match Sibling 2!
	 */
	b_begin_node(&b, "other_device");
	b_prop(&b, "compatible", "vendor,other-device", 20);
	uint8_t reg16[16] = {
		0x00, 0x00, 0x00, 0x00,  0x12, 0x34, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00,  0x00, 0x01, 0x00, 0x00
	};
	b_prop(&b, "reg", reg16, sizeof(reg16));
	b_end_node(&b);

	b_end_node(&b); /* chosen */
	b_end_node(&b); /* root */
	uintptr_t fdt = b_finish(&b);

	struct fdt_video vid = { 0 };
	/* Must return 0 because neither sibling is a valid framebuffer with reg */
	assert(fdt_framebuffer(fdt, &vid) == 0);
	assert(vid.base == 0);
}

/* 4. "memory" name must match exactly: memory-controller/reserved-memory
 *    with a reg must not be reported as a DRAM bank (N6). */
static void test_memory_name_exact(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	b_begin_node(&b, "memory-controller@1000");
	uint8_t reg16[16] = {
		0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00
	};
	b_prop(&b, "reg", reg16, sizeof(reg16));
	b_end_node(&b);
	b_begin_node(&b, "memory@900000000");
	uint8_t reg12[12] = {
		0x00, 0x00, 0x00, 0x09, 0x00, 0x00, 0x00, 0x00,
		0x80, 0x00, 0x00, 0x00
	};
	b_prop(&b, "reg", reg12, sizeof(reg12));
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	/* The real memory node still matches ... */
	assert(fdt_memory_contains(fdt, 0x900000000ULL, 0x80000000ULL));
	/* ... but the memory-controller range (0x1000..0x10000) must not. */
	assert(!fdt_memory_contains(fdt, 0x1000ULL, 0x1000));
}

/* 5. A framebuffer reg shorter than the cell stride must not decode past
 *    the property into the next header (N7). */
static void test_framebuffer_short_reg(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1); /* stride = 12; reg below is only 8 */
	b_begin_node(&b, "chosen");
	b_begin_node(&b, "framebuffer@0");
	b_prop(&b, "compatible", "simple-framebuffer", 19);
	uint8_t reg8[8] = {
		0x00, 0x00, 0x00, 0x00, 0xfa, 0xc0, 0x00, 0x00
	};
	b_prop(&b, "reg", reg8, sizeof(reg8));
	b_end_node(&b);
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	struct fdt_video vid = { 0 };
	/* Must be a clean miss (board fallback), not a garbage size. */
	assert(fdt_framebuffer(fdt, &vid) == 0);
	assert(vid.base == 0);
}

/* 6. A totalsize clipped inside the struct block must abort the walk (0),
 *    not look like a complete one (N8). */
static void test_clipped_totalsize(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_begin_node(&b, "chosen");
	b_prop_u32(&b, "panel-id", 0x1234); /* 4-byte /chosen property */
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	uint64_t out = 0;
	assert(fdt_chosen_u64(fdt, "panel-id", &out) == 1);
	assert(out == 0x1234);

	/* Clip totalsize into the middle of the struct block. */
	b.buf[4] = 0; b.buf[5] = 0; b.buf[6] = 0; b.buf[7] = 100;
	out = 0;
	assert(fdt_chosen_u64(fdt, "panel-id", &out) == 0);
	assert(!fdt_memory_contains(fdt, 0, 1));

	/* An absurd totalsize is rejected outright. */
	b.buf[4] = 0xff; b.buf[5] = 0xff; b.buf[6] = 0xff; b.buf[7] = 0xff;
	assert(fdt_chosen_u64(fdt, "panel-id", &out) == 0);
}

/* 7. fdt_collect_memmap: banks, reserved ranges, and no-map flags in
 *    either property order. */
static void test_collect_memmap(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	b_begin_node(&b, "memory@80000000");
	uint8_t bank0[12] = {
		0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00,
		0x80, 0x00, 0x00, 0x00
	};
	b_prop(&b, "reg", bank0, sizeof bank0);
	b_end_node(&b);
	b_begin_node(&b, "memory@880000000");
	uint8_t bank1[12] = {
		0x00, 0x00, 0x00, 0x08, 0x80, 0x00, 0x00, 0x00,
		0x80, 0x00, 0x00, 0x00
	};
	b_prop(&b, "reg", bank1, sizeof bank1);
	b_end_node(&b);
	b_begin_node(&b, "reserved-memory");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	/* no-map BEFORE reg */
	b_begin_node(&b, "splash@fac00000");
	b_prop(&b, "no-map", "", 0);
	uint8_t rsv0[12] = {
		0x00, 0x00, 0x00, 0x00, 0xfa, 0xc0, 0x00, 0x00,
		0x00, 0x10, 0x00, 0x00
	};
	b_prop(&b, "reg", rsv0, sizeof rsv0);
	b_end_node(&b);
	/* no-map AFTER reg */
	b_begin_node(&b, "ramoops@83000000");
	uint8_t rsv1[12] = {
		0x00, 0x00, 0x00, 0x00, 0x83, 0x00, 0x00, 0x00,
		0x00, 0x10, 0x00, 0x00
	};
	b_prop(&b, "reg", rsv1, sizeof rsv1);
	b_prop(&b, "no-map", "", 0);
	b_end_node(&b);
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	struct fdt_memmap mm;
	assert(fdt_collect_memmap(fdt, &mm) == 1);
	assert(mm.n_banks == 2);
	assert(mm.banks[0].base == 0x80000000ULL && mm.banks[0].size == 0x80000000ULL);
	assert(mm.banks[1].base == 0x880000000ULL && mm.banks[1].size == 0x80000000ULL);
	assert(mm.n_reserved == 2);
	assert(mm.reserved[0].base == 0xfac00000ULL && mm.reserved[0].size == 0x100000ULL);
	assert(mm.reserved[0].no_map == 1);
	assert(mm.reserved[1].base == 0x83000000ULL && mm.reserved[1].size == 0x100000ULL);
	assert(mm.reserved[1].no_map == 1);
	assert(!strcmp(mm.reserved[0].name, "splash@fac00000"));
	assert(!strcmp(mm.reserved[1].name, "ramoops@83000000"));

	/* A tree without memory nodes is a clean miss, not garbage. */
	b_init(&b);
	b_begin_node(&b, "");
	b_begin_node(&b, "chosen");
	b_prop_u32(&b, "#address-cells", 2);
	b_end_node(&b);
	b_end_node(&b);
	fdt = b_finish(&b);
	assert(fdt_collect_memmap(fdt, &mm) == 1);
	assert(mm.n_banks == 0 && mm.n_reserved == 0);
	assert(fdt_collect_memmap(0, &mm) == 0);
}

/* 8. More reserved children than the table holds: capped, walk intact,
 *    and the overflow is COUNTED so callers can fail closed. */
static void test_collect_reserved_overflow(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	b_begin_node(&b, "reserved-memory");
	char name[16];
	for (int i = 0; i < FDT_MAX_RESERVED + 4; i++) {
		snprintf(name, sizeof name, "rsv%d", i);
		b_begin_node(&b, name);
		uint8_t reg[12] = {
			0, 0, 0, 0, 0, 0, 0, (uint8_t)i,
			0, 0, 0x01, 0
		};
		b_prop(&b, "reg", reg, sizeof reg);
		b_end_node(&b);
	}
	b_end_node(&b);
	b_end_node(&b);
	uintptr_t fdt = b_finish(&b);

	struct fdt_memmap mm;
	assert(fdt_collect_memmap(fdt, &mm) == 1);
	assert(mm.n_reserved == FDT_MAX_RESERVED);
	assert(mm.reserved[FDT_MAX_RESERVED - 1].base == FDT_MAX_RESERVED - 1);
	assert(mm.n_reserved_dropped == 4);

	/* No overflow -> zero dropped. */
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);
	b_prop_u32(&b, "#size-cells", 1);
	b_begin_node(&b, "reserved-memory");
	b_begin_node(&b, "only@1000");
	b_prop(&b, "reg", ((uint8_t[]){0,0,0,0,0,0,0x10,0, 0,0,0x10,0}), 12);
	b_end_node(&b);
	b_end_node(&b);
	b_end_node(&b);
	fdt = b_finish(&b);
	assert(fdt_collect_memmap(fdt, &mm) == 1);
	assert(mm.n_reserved == 1 && mm.n_reserved_dropped == 0);
}

/* ALL-ON T-06: a malformed tree reads as "not found" AND says so; a valid tree without the item stays quiet */
static void test_malformed_gap(void)
{
	struct fdt_builder b;
	b_init(&b);
	b_begin_node(&b, "");
	b_prop_u32(&b, "#address-cells", 2);	/* a non-empty strings block: the header check refuses strings == end */
	b_end_node(&b);
	uintptr_t good = b_finish(&b);
	uint64_t v = 0;
	struct fdt_video vid;
	gap_lines = 0;
	assert(!fdt_memory_contains(good, 0x880000000ULL, 0x1000));
	assert(!fdt_chosen_u64(good, "linux,initrd-start", &v));
	assert(!fdt_framebuffer(good, &vid));
	assert(gap_lines == 0);
	static uint32_t bad[16];	/* zero magic */
	assert(!fdt_memory_contains((uintptr_t)bad, 0x880000000ULL, 0x1000));
	assert(gap_lines == 1);
	assert(!fdt_chosen_u64((uintptr_t)bad, "linux,initrd-start", &v));
	assert(gap_lines == 2);
	assert(!fdt_framebuffer((uintptr_t)bad, &vid));
	assert(gap_lines == 3);
}

int main(void)
{
	test_malformed_gap();
	test_original_case();
	test_gs101_memory_cells();
	test_reserved_memory_cells();
	test_framebuffer_sibling_leak();
	test_memory_name_exact();
	test_framebuffer_short_reg();
	test_clipped_totalsize();
	test_collect_memmap();
	test_collect_reserved_overflow();
	return 0;
}
