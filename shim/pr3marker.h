/*
 * PR-3 marker probe (--pr3-marker, -DPR3_MARKER): does DRAM content survive
 * PMU SWRESET -> ABL -> fastboot -> `fastboot boot`? Pure functions only (no
 * MMIO); pr3_probe_site does the try_read32/try_write32 accesses.
 * Design: docs/research/pr3-marker-run-design.md section 5 (64 B layout).
 */
#ifndef PR3MARKER_H
#define PR3MARKER_H

#include <stdint.h>

/* Site A: top 32 MiB of the xnu_ram bank (docs/research/pr3-panic-region-facts.md
 * section 3; device/google/bluejay/board.json:10). */
#define PR3_SITE_A 0x97E000000ULL
/* Site B: ramoops pmsg zone start 0xFD5FF000 + 0x1000, off the Linux zone
 * header (design note section 2). */
#define PR3_SITE_B 0xFD600000ULL

#define PR3_MAGIC 0x505233524D4B5231ULL /* "PR3RMKR1" as stored in the design note */
#define PR3_MARKER_BYTES 64

enum pr3_state { PR3_ZERO, PR3_INTACT, PR3_CHANGED, PR3_FAULT };

uint64_t pr3_fnv1a(const uint8_t *p, uint32_t n);
/* Hash of a NUL-terminated build id string (0 if s is NULL). */
uint64_t pr3_hash_str(const char *s);
/* Fill out[64] for the marker at physical address self. */
void pr3_encode(uint8_t out[PR3_MARKER_BYTES], uint64_t counter, uint64_t build, uint64_t self);
/* Classify 64 read bytes of the marker at self. *counter = stored counter if intact. */
enum pr3_state pr3_classify(const uint8_t in[PR3_MARKER_BYTES], uint64_t self, uint64_t *counter);
/* Counter to write after a read: intact -> read + 1, else 1. */
uint64_t pr3_next_counter(enum pr3_state s, uint64_t read_counter);

/* Extra facts about one probed site (for the log lines). */
struct pr3_info {
	uint64_t first;		/* first 8 bytes read (little-endian) */
	uint64_t build;		/* build hash stored in the marker read */
	uint32_t cs_ok, self_ok;	/* checksum / self-address fields valid */
	uint32_t readback;	/* 0 ok, 1 mismatch, 2 skipped (write fault), 3 skipped (read fault),
				 * 4 readback read fault */
	uint32_t fault_off;	/* byte offset of the readback read fault (readback == 4) */
};

/* Read, classify, encode and write the marker at site, then read it back.
 * Uses extern try_read32/try_write32 (4-byte aligned, site+0..0x3C only), dsb sy after
 * the write loop. *wfail: 0 ok, 1 write fault, 2 readback mismatch/fault,
 * 3 site unreadable (nothing written). */
int try_read32(uintptr_t address, uint32_t *value);
int try_write32(uintptr_t address, uint32_t value);
enum pr3_state pr3_probe_site(uint64_t site, uint64_t build, uint64_t *ctr_read,
			      uint64_t *ctr_written, uint32_t *wfail, struct pr3_info *info);

#endif
