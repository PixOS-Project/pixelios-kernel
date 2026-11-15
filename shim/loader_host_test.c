#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "payload.h"

int main(void)
{
	uint8_t initrd[9000];
	memset(initrd, 0x5a, sizeof(initrd));
	memcpy(initrd + 4096, "SWEETXNU", 8);
	assert(shim_find_magic((uintptr_t)initrd, (uintptr_t)initrd + sizeof(initrd), "SWEETXNU", 8) ==
	       (uintptr_t)initrd + 4096);
	assert(shim_find_magic((uintptr_t)initrd, (uintptr_t)initrd + 4096, "SWEETXNU", 8) == 0);
	/* boot speed: tail search finds a valid payload just below hi - size (bootconfig after it), refuses a header that fails
	 * payload_check, and gives 0 when the payload is further down (the caller falls back to the full scan) */
	{
		static union { struct payload p; uint64_t al; uint8_t raw[1 << 16]; } rd;
		memset(rd.raw, 0x5a, sizeof(rd.raw));
		uint64_t size = 4096, trailer = 1000, at = sizeof(rd.raw) - trailer - size;
		at &= ~7ULL;
		struct payload *pp = (struct payload *)(rd.raw + at);
		memset(pp, 0, sizeof(*pp));
		memcpy(pp->magic, "SWEETXNU", 8);
		pp->version = PAYLOAD_VERSION;
		uintptr_t lo = (uintptr_t)rd.raw, hi = lo + sizeof(rd.raw);
		assert(shim_find_payload_tail(lo, hi, size, 65536) == (uintptr_t)pp);
		assert(shim_find_payload_tail(lo, hi, size, 100) == 0);		/* window too small: fallback */
		pp->version = PAYLOAD_VERSION + 1;
		assert(shim_find_payload_tail(lo, hi, size, 65536) == 0);	/* magic alone is not enough */
		pp->version = PAYLOAD_VERSION;
		memcpy(rd.raw + at + 64, "SWEETXNU", 8);			/* a stray magic above the real header (no valid payload there) */
		assert(shim_find_payload_tail(lo, hi, size, 65536) == (uintptr_t)pp);
		assert(shim_find_payload_tail(lo, lo + 10, size, 65536) == 0);
	}

	/* payload_check / payload_fill_boot_args (review bootline-1024-r1 F2/F3): a full 1023-char line arrives whole. */
	static union { struct payload p; uint8_t raw[sizeof(struct payload) + 64]; } pl;
	static union { struct boot_args b; uint64_t align; } ba;
	memcpy(pl.p.magic, "SWEETXNU", 8);
	pl.p.version = PAYLOAD_VERSION;
	pl.p.virt_base = 0xfffffe0007004000ULL;
	pl.p.phys_base = 0x880000000ULL;
	pl.p.mem_size = 0x1c0000000ULL;
	pl.p.adt_size = 16048;
	memset(pl.p.cmdline, 'a', BOOT_LINE_LENGTH - 1);
	assert(payload_check(&pl.p) == 0);
	pl.p.version = 1;
	assert(payload_check(&pl.p) != 0);
	pl.p.version = PAYLOAD_VERSION;
	pl.p.cmdline[BOOT_LINE_LENGTH - 1] = 'a';
	assert(payload_check(&pl.p) != 0);
	pl.p.cmdline[BOOT_LINE_LENGTH - 1] = 0;
	memset(&ba, 0x5a, sizeof(ba));
	payload_fill_boot_args(&ba.b, &pl.p, 0x880200000ULL, 0xfffffe0007100000ULL, 0);
	assert(ba.b.Revision == 2 && ba.b.Version == 2);
	assert(ba.b.virtBase == pl.p.virt_base && ba.b.physBase == pl.p.phys_base && ba.b.memSize == pl.p.mem_size);
	assert(ba.b.topOfKernelData == 0x880200000ULL && ba.b.deviceTreeP == 0xfffffe0007100000ULL);
	assert(ba.b.deviceTreeLength == 16048);
	assert(ba.b.CommandLine[0] == 'a' && ba.b.CommandLine[BOOT_LINE_LENGTH - 2] == 'a' && ba.b.CommandLine[BOOT_LINE_LENGTH - 1] == 0);
	assert(ba.b.memSizeActual == pl.p.mem_size);
	assert(ba.b.bootFlags == 0);	/* the page is filled, not just zeroed: a default build leaves bit 62 (and every other bit) clear */

	/* review el2-b2-report-r1 F3: half-open overlap used for the ramdisk and the EL2 image against XNU RAM */
	assert(payload_ranges_overlap(0x80000000ULL, 0x8005e000ULL, 0x880000000ULL, 0x980000000ULL) == 0);	/* the expected layout: image in the low bank */
	assert(payload_ranges_overlap(0x87ffff000ULL, 0x880001000ULL, 0x880000000ULL, 0x980000000ULL) == 1);	/* straddles the start */
	assert(payload_ranges_overlap(0x97ffff000ULL, 0x980001000ULL, 0x880000000ULL, 0x980000000ULL) == 1);	/* straddles the end */
	assert(payload_ranges_overlap(0x890000000ULL, 0x890001000ULL, 0x880000000ULL, 0x980000000ULL) == 1);	/* inside */
	assert(payload_ranges_overlap(0x87fff0000ULL, 0x880000000ULL, 0x880000000ULL, 0x980000000ULL) == 0);	/* touches, no overlap */
	assert(payload_ranges_overlap(0x980000000ULL, 0x980001000ULL, 0x880000000ULL, 0x980000000ULL) == 0);
	assert(payload_ranges_overlap(0x890000000ULL, 0x890000000ULL, 0x880000000ULL, 0x980000000ULL) == 0);	/* empty */

	/* SPEC-EL2-PROXY-PROBE 3.4 (b) item 2: bit 62 only in an EL2_RESIDENT build whose stage 2 is on (el2_s2_on == 1) */
	assert(kBootFlagsSweetEL2 == (1ULL << 62));
	assert(payload_bootflags(1, 1) == (1ULL << 62));
	assert(payload_bootflags(1, 0) == 0);		/* resident build, stage 2 fell back */
	assert(payload_bootflags(1, 2) == 0);		/* only exactly 1 */
	assert(payload_bootflags(0, 1) == 0);		/* stage 2 on in a non-resident build (B1 probe) */
	assert(payload_bootflags(0, 0) == 0);
	payload_fill_boot_args(&ba.b, &pl.p, 0x880200000ULL, 0xfffffe0007100000ULL, payload_bootflags(1, 1));
	assert(ba.b.bootFlags == (1ULL << 62) && (ba.b.bootFlags & 1) == 0);
	assert(ba.b.CommandLine[0] == 'a' && ba.b.memSizeActual == pl.p.mem_size);	/* neighbours of bootFlags untouched */

	/* T-1142 (review T-48a0 r1 F5 + Opus r1 F1/F2): a segment source must lie inside [lo, hi) (after the header and earlier sources, before the ADT) and be 8-aligned before the loader copies and wipes it */
	{
		struct payload_seg sg = { 0, 0x4000, 0x1000, 0x2001 };		/* source [0x1000, 0x3008) after rounding up */

		assert(payload_seg_src_ok(&sg, 0x448, 0x3008) == 1);		/* exactly fits */
		assert(payload_seg_src_ok(&sg, 0x448, 0x3007) == 0);		/* one byte short */
		assert(payload_seg_src_ok(&sg, 0x1000, 0x3008) == 1);		/* starts exactly at lo */
		assert(payload_seg_src_ok(&sg, 0x1008, 0x10000) == 0);		/* overlaps the header / the previous source */
		assert(payload_seg_src_ok(&sg, 0x448, 0x2000) == 0);		/* overlaps the ADT at hi */
		assert(payload_seg_src_ok(&sg, 0x3008, 0x3008) == 0);		/* lo past the source */
		assert(payload_seg_src_ok(&sg, 0x10000, 0x448) == 0);		/* lo > hi */
		sg.file_off = 0;
		assert(payload_seg_src_ok(&sg, 0x448, 0x10000) == 0);		/* a source at the very start of the payload overlaps the header */
		sg.file_off = 0x1004;
		assert(payload_seg_src_ok(&sg, 0x448, 0x10000) == 0);		/* not 8-aligned */
		sg.file_off = ~0ULL & ~7ULL;
		sg.file_size = 16;
		assert(payload_seg_src_ok(&sg, 0x448, 0x10000) == 0);		/* file_off + size wraps */
		sg.file_off = 0x448;
		sg.file_size = 0x20;
		assert(payload_seg_src_ok(&sg, 0x448, 0x10) == 0);		/* rounded size larger than the whole range: hi - lo must not wrap (Opus r1 F2) */
		assert(payload_seg_src_ok(&sg, 0x448, 0x458) == 0);		/* rounded size larger than hi - lo */
		sg.file_off = 0;
		sg.file_size = ~0ULL;
		assert(payload_seg_src_ok(&sg, 0, ~0ULL) == 0);			/* size rounding wraps */
		sg.file_size = 0;
		assert(payload_seg_src_ok(&sg, 0x448, 0) == 1);			/* an empty segment needs nothing */
	}
	/* Opus r1 F1/F5: payload_segs_check on a whole payload (header + table + sources + ADT) */
	{
		static struct { struct payload p; struct payload_seg more[2]; } q;
		uint32_t bad = 0;
		uint64_t lo = sizeof(struct payload) + 2 * sizeof(struct payload_seg);

		q.p.nsegs = 2;
		q.p.segs[0] = (struct payload_seg){ 0, 0x1000, lo, 0x20 };
		q.p.segs[1] = (struct payload_seg){ 0x1000, 0x1000, lo + 0x20, 0x18 };
		q.p.adt_off = lo + 0x38;
		q.p.adt_size = 0x40;
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x40, &bad) == 0 && bad == ~0U);
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x3f, &bad) != 0 && bad == ~0U);	/* ADT short */
		q.p.segs[1].file_off = lo + 0x18;	/* overlaps the first source */
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x40, &bad) != 0 && bad == 1);
		q.p.segs[1].file_off = lo + 0x20;
		q.p.segs[0].file_off = 0;		/* overlaps the header */
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x40, &bad) != 0 && bad == 0);
		q.p.segs[0].file_off = lo;
		q.p.segs[1].file_size = 0x20;		/* runs into the ADT */
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x40, &bad) != 0 && bad == 1);
		q.p.segs[1].file_size = 0x18;
		q.p.nsegs = 1000;			/* table longer than the payload */
		assert(payload_segs_check(&q.p, lo + 0x38 + 0x40, &bad) != 0 && bad == ~0U);
	}
	return 0;
}
