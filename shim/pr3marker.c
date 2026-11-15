/* PR-3 marker: pure encode/classify, see pr3marker.h. */
#include "pr3marker.h"
#include "shim_assert.h"

#define FNV_OFF 0xcbf29ce484222325ULL
#define FNV_PRIME 0x100000001b3ULL
#define PAT_MUL 0x9E3779B97F4A7C15ULL
#define PR3_STR_MAX 256u	/* longest build id hashed (PSTORE_BUILD_ID is a short git rev) */
#define PR3_CSUM_OFF 0x38u	/* the FNV-1a checksum covers bytes 0..0x37 and sits at 0x38 */

uint64_t pr3_fnv1a(const uint8_t *p, uint32_t n)
{
	uint64_t h = FNV_OFF;

	SHIM_ASSERT(p != 0 || n == 0);
	SHIM_ASSERT(n <= PR3_MARKER_BYTES);
	for (uint32_t i = 0; i < n; i++)
		h = (h ^ p[i]) * FNV_PRIME;
	return h;
}

uint64_t pr3_hash_str(const char *s)
{
	uint64_t h = FNV_OFF;
	uint32_t i = 0;

	if (!s)
		return 0;
	for (; i < PR3_STR_MAX && s[i]; i++)
		h = (h ^ (uint8_t)s[i]) * FNV_PRIME;
	SHIM_ASSERT(i <= PR3_STR_MAX);
	SHIM_ASSERT(s[i] == 0);	/* ended inside the cap: a longer id would hash a prefix only */
	return h;
}

static void put64(uint8_t *p, uint64_t v)
{
	SHIM_ASSERT(p != 0);
	for (uint32_t i = 0; i < 8; i++)
		p[i] = (uint8_t)(v >> (8 * i));
	SHIM_ASSERT(p[0] == (uint8_t)v);
}

static uint64_t get64(const uint8_t *p)
{
	uint64_t v = 0;

	SHIM_ASSERT(p != 0);
	for (uint32_t i = 0; i < 8; i++)
		v |= (uint64_t)p[i] << (8 * i);
	SHIM_ASSERT((uint8_t)v == p[0]);
	return v;
}

void pr3_encode(uint8_t out[PR3_MARKER_BYTES], uint64_t counter, uint64_t build, uint64_t self)
{
	SHIM_ASSERT(out != 0);
	SHIM_ASSERT(PR3_CSUM_OFF + 8u == PR3_MARKER_BYTES);
	put64(out + 0x00, PR3_MAGIC);
	put64(out + 0x08, counter);
	put64(out + 0x10, build);
	put64(out + 0x18, self);
	for (uint32_t i = 0; i < 3; i++)
		put64(out + 0x20 + 8u * (uint64_t)i, counter * PAT_MUL + (uint64_t)i);
	put64(out + PR3_CSUM_OFF, pr3_fnv1a(out, PR3_CSUM_OFF));
}

enum pr3_state pr3_classify(const uint8_t in[PR3_MARKER_BYTES], uint64_t self, uint64_t *counter)
{
	uint32_t zero = 1;
	uint64_t c;

	SHIM_ASSERT(in != 0);
	for (uint32_t i = 0; i < PR3_MARKER_BYTES; i++)
		if (in[i])
			zero = 0;
	if (zero)
		return PR3_ZERO;
	if (get64(in) != PR3_MAGIC || get64(in + 0x18) != self ||
	    get64(in + PR3_CSUM_OFF) != pr3_fnv1a(in, PR3_CSUM_OFF))
		return PR3_CHANGED;
	c = get64(in + 0x08);
	for (uint32_t i = 0; i < 3; i++)
		if (get64(in + 0x20 + 8u * (uint64_t)i) != c * PAT_MUL + (uint64_t)i)
			return PR3_CHANGED;
	if (counter)
		*counter = c;
	SHIM_ASSERT(get64(in) == PR3_MAGIC);	/* intact means the magic matched */
	return PR3_INTACT;
}

uint64_t pr3_next_counter(enum pr3_state s, uint64_t read_counter)
{
	SHIM_ASSERT(s <= PR3_FAULT);
	SHIM_ASSERT(s != PR3_INTACT || read_counter != UINT64_MAX);	/* +1 must not wrap to 0 */
	return s == PR3_INTACT ? read_counter + 1 : 1;
}

/* Read the 64 marker bytes at site with 32-bit accesses (little-endian). 1 ok, 0 = read fault at
 * byte offset *fault_off (buf is then partly filled). */
static uint32_t pr3_read_block(uint64_t site, uint8_t *buf, uint32_t *fault_off)
{
	SHIM_ASSERT(buf != 0 && fault_off != 0);
	SHIM_ASSERT((site & 3u) == 0);
	for (uint32_t i = 0; i < PR3_MARKER_BYTES; i += 4) {
		uint32_t w = 0;

		if (!try_read32((uintptr_t)site + i, &w)) {
			*fault_off = i;
			return 0;
		}
		for (uint32_t k = 0; k < 4; k++)
			buf[i + k] = (uint8_t)(w >> (8 * k));
	}
	return 1;
}

/* Write buf (64 B) to site with 32-bit accesses. 1 ok, 0 = write fault (rest not written). */
static uint32_t pr3_write_block(uint64_t site, const uint8_t *buf)
{
	SHIM_ASSERT(buf != 0);
	SHIM_ASSERT((site & 3u) == 0);
	for (uint32_t i = 0; i < PR3_MARKER_BYTES; i += 4) {
		uint32_t w = buf[i] | (uint32_t)buf[i + 1] << 8 | (uint32_t)buf[i + 2] << 16 |
			     (uint32_t)buf[i + 3] << 24;

		if (!try_write32((uintptr_t)site + i, w))
			return 0;
	}
	return 1;
}

/* Fill info from the 64 bytes read (what the log shows when the marker is not intact). */
static void pr3_describe(struct pr3_info *info, const uint8_t *buf, uint64_t site)
{
	SHIM_ASSERT(info != 0 && buf != 0);
	info->first = get64(buf);
	info->build = get64(buf + 0x10);
	info->self_ok = get64(buf + 0x18) == site;
	info->cs_ok = get64(buf + PR3_CSUM_OFF) == pr3_fnv1a(buf, PR3_CSUM_OFF);
	SHIM_ASSERT(info->self_ok <= 1 && info->cs_ok <= 1);
}

/* Read the marker back after the write and compare with buf. Sets info->readback (0 ok,
 * 1 mismatch, 4 read fault + fault_off) and returns the *wfail value (0 or 2). */
static uint32_t pr3_readback(uint64_t site, const uint8_t *buf, struct pr3_info *info)
{
	uint8_t rb[PR3_MARKER_BYTES];
	uint32_t off = 0;

	SHIM_ASSERT(buf != 0 && info != 0);
	SHIM_ASSERT((site & 3u) == 0);
	info->readback = 0;
	if (!pr3_read_block(site, rb, &off)) {
		info->readback = 4;
		info->fault_off = off;
	} else {
		for (uint32_t i = 0; i < PR3_MARKER_BYTES; i++)
			if (rb[i] != buf[i])
				info->readback = 1;
	}
	SHIM_ASSERT(info->readback <= 4);
	return info->readback ? 2u : 0u;	/* 1 mismatch, 4 read fault */
}

enum pr3_state pr3_probe_site(uint64_t site, uint64_t build, uint64_t *ctr_read,
			      uint64_t *ctr_written, uint32_t *wfail, struct pr3_info *info)
{
	uint8_t buf[PR3_MARKER_BYTES];
	uint64_t ctr = 0;
	uint32_t off = 0;
	enum pr3_state st;

	SHIM_ASSERT(ctr_read != 0 && ctr_written != 0 && wfail != 0 && info != 0);
	SHIM_ASSERT((site & 3u) == 0);
	*wfail = 0;
	info->readback = 2;
	info->fault_off = 0;
	info->first = info->build = 0;
	info->cs_ok = info->self_ok = 0;
	if (!pr3_read_block(site, buf, &off)) {	/* unreadable: never write (no write, no readback) */
		*ctr_read = *ctr_written = 0;
		*wfail = 3;
		info->readback = 3;
		return PR3_FAULT;
	}
	st = pr3_classify(buf, site, &ctr);
	pr3_describe(info, buf, site);
	*ctr_read = ctr;
	*ctr_written = pr3_next_counter(st, ctr);
	pr3_encode(buf, *ctr_written, build, site);
	if (!pr3_write_block(site, buf))
		*wfail = 1;
#ifdef __aarch64__
	__asm__ volatile("dsb sy" ::: "memory");
#endif
	if (*wfail)
		return st;
	*wfail = pr3_readback(site, buf, info);
	SHIM_ASSERT(*wfail <= 2);
	return st;
}
