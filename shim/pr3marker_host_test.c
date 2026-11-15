/* Host test for pr3marker.c: round-trip, per-field corruption, counter rule, FNV KATs. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include "pr3marker.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)


/* fake 2-site memory behind try_read32/try_write32 (pr3_probe_site's only I/O) */
static uint32_t mem[2][16];
static uintptr_t rd_fault = (uintptr_t)-1, wr_fault = (uintptr_t)-1;
static int razwi, nwrites, stuck_word = -1;
static uint32_t stuck_mask;
static uintptr_t rb_fault = (uintptr_t)-1;	/* read fault only after the first write */
static uint32_t *cellp(uintptr_t a)
{
	if (a >= PR3_SITE_A && a < PR3_SITE_A + 64) return &mem[0][(a - PR3_SITE_A) / 4];
	if (a >= PR3_SITE_B && a < PR3_SITE_B + 64) return &mem[1][(a - PR3_SITE_B) / 4];
	return NULL;
}
int try_read32(uintptr_t a, uint32_t *v)
{
	uint32_t *c = cellp(a);
	if (!c || (a & 3) || a == rd_fault || (nwrites && a == rb_fault)) return 0;
	*v = razwi ? 0 : *c;
	return 1;
}
int try_write32(uintptr_t a, uint32_t v)
{
	uint32_t *c = cellp(a);
	if (!c || (a & 3) || a == wr_fault) return 0;
	nwrites++;
	if (stuck_word >= 0 && a == PR3_SITE_A + 4 * stuck_word) v &= ~stuck_mask;
	if (!razwi) *c = v;
	return 1;
}
static void reset(void) { memset(mem, 0, sizeof mem); rd_fault = wr_fault = rb_fault = (uintptr_t)-1; razwi = nwrites = 0; stuck_word = -1; stuck_mask = 0; }
static void setbytes(int s, const uint8_t *b) { memcpy(mem[s], b, 64); }
static void getbytes(int s, uint8_t *b) { memcpy(b, mem[s], 64); }

static void probe_tests(void)
{
	uint64_t b1 = pr3_hash_str("abc1234"), r1, w1, r2, w2;
	uint32_t wf;
	struct pr3_info in;
	uint8_t m[64], k[64];
	static const uint8_t kat[64] = {
		0x31, 0x52, 0x4b, 0x4d, 0x52, 0x33, 0x52, 0x50, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0xa3, 0xd6, 0xaf, 0xef, 0xc2, 0x05, 0xb7, 0xec, 0x00, 0x00, 0x00, 0x7e, 0x09, 0x00, 0x00, 0x00,
		0x15, 0x7c, 0x4a, 0x7f, 0xb9, 0x79, 0x37, 0x9e, 0x16, 0x7c, 0x4a, 0x7f, 0xb9, 0x79, 0x37, 0x9e,
		0x17, 0x7c, 0x4a, 0x7f, 0xb9, 0x79, 0x37, 0x9e, 0x8c, 0x87, 0x25, 0xc2, 0xa9, 0x3c, 0x31, 0xc6 };

	/* (g) known-answer vector, computed independently in python */
	pr3_encode(k, 1, b1, PR3_SITE_A);
	CHECK(memcmp(k, kat, 64) == 0);

	/* (a) boot 1 zero -> zero, boot 2 intact ctr 1; written ctr of boot 1 == read ctr of boot 2 */
	reset();
	CHECK(pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in) == PR3_ZERO && r1 == 0 && w1 == 1 && wf == 0 && in.readback == 0);
	CHECK(pr3_probe_site(PR3_SITE_B, b1, &r1, &w1, &wf, &in) == PR3_ZERO && wf == 0);
	CHECK(pr3_probe_site(PR3_SITE_A, b1, &r2, &w2, &wf, &in) == PR3_INTACT && r2 == 1 && w1 == r2 && w2 == 2 && wf == 0 && in.build == b1);
	getbytes(0, m);
	CHECK(memcmp(m, (uint8_t[64]){0}, 64) != 0);

	/* (b) read fault at every word -> FAULT */
	for (int w = 0; w < 16; w++) {
		reset();
		rd_fault = PR3_SITE_A + 4 * w;
		/* a read fault stops the site: nothing written, no readback (wfail 3) */
		CHECK(pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in) == PR3_FAULT && w1 == 0 && wf == 3 && in.readback == 3);
		CHECK(nwrites == 0);
		for (int j = 0; j < 16; j++) CHECK(mem[0][j] == 0);
	}
	/* (c) write fault -> wfail=1, readback not done */
	reset();
	wr_fault = PR3_SITE_A + 8;
	pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in);
	CHECK(wf == 1 && in.readback == 2);
	/* (d) RAZ/WI memory: write "works", readback differs -> wfail=2 (counted) */
	reset();
	razwi = 1;
	pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in);
	CHECK(wf == 2 && in.readback == 1);
	/* stuck byte / stuck last word: one byte of one word differs on readback -> wfail 2 */
	for (int w = 0; w < 16; w++)
		for (int sh = 0; sh < 32; sh += 8) {
			reset();
			stuck_word = w; stuck_mask = 0xFFu << sh;
			pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in);
			/* a stuck mask only bites where the encoded byte is non-zero; the encoded marker has no zero word-byte set for every (w, sh), so skip equal cases */
			uint8_t e[64];
			pr3_encode(e, 1, b1, PR3_SITE_A);
			if (e[4 * w + sh / 8] != 0) CHECK(wf == 2 && in.readback == 1);
		}
	reset();
	stuck_word = 15; stuck_mask = 0;
	pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in);
	CHECK(wf == 0 && in.readback == 0);
	/* readback read fault at +0x14: reported apart from a mismatch, wfail 2 */
	reset();
	rb_fault = PR3_SITE_A + 0x14;
	pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in);
	CHECK(wf == 2 && in.readback == 4 && in.fault_off == 0x14);
	/* (e) all zero except byte 63 -> CHANGED (zero check covers all 64 bytes) */
	reset();
	memset(m, 0, 64); m[63] = 1; setbytes(0, m);
	CHECK(pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in) == PR3_CHANGED && w1 == 1);
	/* (f) magic altered, checksum recomputed -> CHANGED, cs ok, first shows the bad magic */
	reset();
	pr3_encode(m, 3, b1, PR3_SITE_A);
	m[0] ^= 0x55;
	{ uint64_t h = pr3_fnv1a(m, 0x38); for (int i = 0; i < 8; i++) m[0x38 + i] = (uint8_t)(h >> (8 * i)); }
	setbytes(0, m);
	CHECK(pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in) == PR3_CHANGED && in.cs_ok && in.self_ok && in.first != PR3_MAGIC);
	/* (h) marker of another build -> intact, build differs, counter continues */
	reset();
	pr3_encode(m, 7, b1 ^ 1, PR3_SITE_A);
	setbytes(0, m);
	CHECK(pr3_probe_site(PR3_SITE_A, b1, &r1, &w1, &wf, &in) == PR3_INTACT && r1 == 7 && w1 == 8 && in.build == (b1 ^ 1) && in.build != b1);
}

int main(void)
{
	uint8_t m[PR3_MARKER_BYTES], z[PR3_MARKER_BYTES] = {0}, t[PR3_MARKER_BYTES];
	uint64_t c = 0, self = PR3_SITE_A, build = pr3_hash_str("abc1234");

	CHECK(pr3_fnv1a((const uint8_t *)"", 0) == 0xcbf29ce484222325ULL);
	CHECK(pr3_fnv1a((const uint8_t *)"a", 1) == 0xaf63dc4c8601ec8cULL);
	CHECK(pr3_hash_str("a") == 0xaf63dc4c8601ec8cULL);
	CHECK(pr3_hash_str(NULL) == 0);

	pr3_encode(m, 5, build, self);
	CHECK(m[0] == 0x31 && m[7] == 0x50);	/* little-endian "PR3RMKR1" */
	CHECK(pr3_classify(m, self, &c) == PR3_INTACT && c == 5);
	CHECK(pr3_classify(z, self, &c) == PR3_ZERO);
	CHECK(pr3_classify(m, PR3_SITE_B, &c) == PR3_CHANGED);	/* wrong self address */

	/* one flipped bit in each 8-byte field (and every byte) -> changed */
	for (int i = 0; i < PR3_MARKER_BYTES; i++) {
		memcpy(t, m, sizeof t);
		t[i] ^= 1;
		if (pr3_classify(t, self, &c) != PR3_CHANGED) {
			printf("FAIL flip byte %d not changed\n", i);
			fails++;
		}
	}
	/* a valid checksum over a wrong pattern must still be changed */
	memcpy(t, m, sizeof t);
	t[0x20] ^= 1;
	{ uint64_t h = pr3_fnv1a(t, 0x38); for (int i = 0; i < 8; i++) t[0x38 + i] = (uint8_t)(h >> (8 * i)); }
	CHECK(pr3_classify(t, self, &c) == PR3_CHANGED);

	CHECK(pr3_next_counter(PR3_INTACT, 5) == 6);
	CHECK(pr3_next_counter(PR3_ZERO, 5) == 1);
	CHECK(pr3_next_counter(PR3_CHANGED, 5) == 1);
	CHECK(pr3_next_counter(PR3_FAULT, 5) == 1);

	/* counter 1 and 2 markers differ and both classify intact */
	pr3_encode(t, 1, build, self);
	CHECK(memcmp(t, m, sizeof t) != 0 && pr3_classify(t, self, &c) == PR3_INTACT && c == 1);

	probe_tests();
	puts(fails ? "pr3marker_host_test FAIL" : "pr3marker_host_test OK");
	return fails != 0;
}
