#pragma once
#include <stdint.h>

/* = pexpert/pexpert/arm64/boot.h BOOT_LINE_LENGTH as set by xnu patch 0320 (0176: 1024, Apple: 608); mkpayload.py and
 * tools/run-preflight use the same number (tools/test_bootline_layout.py checks all of them). */
#define BOOT_LINE_LENGTH	2048
#define PAYLOAD_VERSION		3	/* 3 = cmdline[2048]; 2 was cmdline[1024] (segs at 0x448); 1 was cmdline[608] (segs at 0x2a8) */
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

/*
 * payload: magic 8 + version 4 + nsegs 4 + 7 u64 (56) = 0x48 = cmdline; segs = 0x48 + 2048 = 0x848 (8-aligned).
 * boot_args: Revision/Version 2+2, pad to 8, 4 u64 at 8..40, Video 6 u64 at 40..88, machineType 88..92, pad,
 * deviceTreeP 96..104, deviceTreeLength 104..108, CommandLine 108..108+2048 = 2156, pad to 8 -> bootFlags 2160,
 * memSizeActual 2168, sizeof 2176 (1024: bootFlags 1136, sizeof 1152; 608: sizeof 736; growth 2048-1024 = 1024).
 */
_Static_assert(__builtin_offsetof(struct payload, cmdline) == 0x48, "payload header layout");
_Static_assert(__builtin_offsetof(struct payload, segs) == 0x848, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, bootFlags) == 2160, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, memSizeActual) == 2168, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 2176, "boot_args layout");
_Static_assert(sizeof(struct boot_args) <= PAGE_16K, "boot_args must fit its zeroed 16 KB page (topOfKernelData = ba_pa + 16K)");

uintptr_t shim_find_magic(uintptr_t start, uintptr_t end, const char *magic, unsigned len);
/* NULL if the shim can boot this payload header, else why not (loud refusal; rule 8). */
const char *payload_check(const struct payload *p);
uintptr_t shim_find_payload_tail(uintptr_t lo, uintptr_t hi, uint64_t size, uint64_t window);
/* boot_args.bootFlags bit 62 = the shim keeps a resident EL2 with stage 2 on (SPEC-EL2-PROXY-PROBE 3.4 (b) design item 2). XNU defines only bit 0 (kBootFlagsDarkBoot,
 * pexpert/pexpert/arm64/boot.h:54) [read from source, patched tree dk-jetsam], so bit 62 is free; xnu/patches/0180 tests it. */
#define kBootFlagsSweetEL2	(1ULL << 62)
/* 1 if the segment's source [file_off, file_off + file_size rounded up to 8) lies inside the payload bytes [lo, hi) and file_off is 8-aligned (copy64 and the source wipe work in 8-byte words;
 * overflow-safe); an empty segment is always 1. The loader passes lo = end of the header + segment table (or of the previous non-empty source) and hi = adt_off, so a source never overlaps
 * the header, another source or the ADT, and refuses a payload with any segment for which this is 0 before it copies or wipes anything (T-1142, review T-48a0 r1 F5 + Opus r1 F1). */
uint32_t payload_seg_src_ok(const struct payload_seg *s, uint64_t lo, uint64_t hi);
/* NULL if the header + segment table, the ADT and every segment source lie inside the first `avail` bytes of the payload, in order and disjoint (payload_seg_src_ok for each); else why not, and
 * *bad = the index of the failing segment (~0 when the table or the ADT is what failed). Nothing is copied or wiped before this returns NULL (Opus r1 F1/F5). */
const char *payload_segs_check(const struct payload *p, uint64_t avail, uint32_t *bad);
/* 1 if the half-open ranges [a_lo, a_hi) and [b_lo, b_hi) share a byte (loader.c: ramdisk and, in an EL2_RESIDENT build, the EL2 image against XNU RAM) */
int payload_ranges_overlap(uint64_t a_lo, uint64_t a_hi, uint64_t b_lo, uint64_t b_hi);
/* bootFlags for payload_fill_boot_args: kBootFlagsSweetEL2 only in an EL2_RESIDENT build (resident = 1) whose stage 2 was enabled (s2_on = el2_s2_on == 1; a later pre-jump VM clear does not reset it, the live VM bit is what the hvc reports), else 0 */
uint64_t payload_bootflags(int resident, uint32_t s2_on);
/* Everything in boot_args except Video, from the payload header (host-tested in loader_host_test.c). */
void payload_fill_boot_args(volatile struct boot_args *ba, const struct payload *p, uint64_t top, uint64_t dt_va, uint64_t bootflags);
