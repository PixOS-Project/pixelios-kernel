/* Host test of the firmware container parser (xnu patches 0128 + 0185, bsd/dev/sweet_wlan_fw.h): synthetic blobs only, never the real firmware. */
#include <stdio.h>
#include <string.h>
#include "sweet_wlan_fw.h"

static unsigned fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

/* 3 entries FW/NVRM/CLM like tools/pack_wlanfw.py: blobs of 100, 10 and 17 bytes at 8-aligned offsets */
static size_t build(uint8_t *c)
{
	static const char *tag[3] = { "FW  ", "NVRM", "CLM " };
	static const uint32_t sz[3] = { 100, 10, 17 };
	size_t off = SWF_HDR + SWF_ENTRY * 3;
	unsigned i, k;

	memset(c, 0, 512);
	memcpy(c, "WLANFWB1", 8); put32(c + 8, 1); put32(c + 12, 3);
	for (i = 0; i < 3; i++) {
		uint8_t *r = c + SWF_HDR + SWF_ENTRY * i;

		off = (off + 7) & ~(size_t)7;
		for (k = 0; k < sz[i]; k++) c[off + k] = (uint8_t)(i * 77 + k * 3 + 1);
		memcpy(r, tag[i], 4); put32(r + 4, sz[i]); put32(r + 8, off); put32(r + 12, swf_crc32(c + off, sz[i]));
		off += sz[i];
	}
	return (off + 7) & ~(size_t)7;
}

/* swf_gaps() (patch 0185, X-W7 F1-F4): the exact lines the glue logs as WIFI GAP: fw ... for a parsed container with `n` entries of build() */
static void check_gaps(struct swf_entry *e, unsigned n, int want_clm)
{
	struct swf_gap g[SWF_GAP_MAX];
	unsigned k = swf_gaps(e, n, g);

	CHECK(k == 4);
	if (k != 4) return;
	CHECK(strcmp(g[0].what, want_clm ? "CLM packed but not loaded (no clmload yet)" : "no CLM entry in the container") == 0 && strcmp(g[0].sev, "high") == 0);
	CHECK(strcmp(g[1].what, "no txcap_blob (stock has none, not loaded)") == 0 && strcmp(g[1].sev, "low") == 0);
	CHECK(strcmp(g[2].what, "cal/clm = unsuffixed default, no region pick") == 0 && strcmp(g[2].sev, "medium") == 0);
	CHECK(strstr(g[3].what, "/vendor/etc/wifi") && strstr(g[3].what, "/vendor/firmware") && strcmp(g[3].sev, "low") == 0);
}

int main(void)
{
	uint8_t c[512], d[512];
	struct swf_entry e[SWF_MAX_ENTRIES];
	unsigned n = 0, i, b;
	size_t len = build(c);

	CHECK(swf_crc32((const uint8_t *)"123456789", 9) == 0xCBF43926u);	/* the standard CRC-32 check value */
	CHECK(swf_crc32((const uint8_t *)"", 0) == 0u);
	CHECK(swf_parse(c, len, e, &n) == 0 && n == 3 && e[0].length == 100 && e[1].offset % 8 == 0 && memcmp(e[2].tag, "CLM ", 4) == 0);
	CHECK(swf_parse(c, SWF_HDR - 1, e, &n) == 1 && swf_parse(c, SWF_MAX_BYTES + 1, e, &n) == 1);
	CHECK(swf_parse(c, len - 8, e, &n) == 4);					/* last blob runs past the end */
	for (i = 0; i < 8; i++) { memcpy(d, c, sizeof d); d[i] ^= 1; CHECK(swf_parse(d, len, e, &n) == 2); }
	memcpy(d, c, sizeof d); put32(d + 8, 2); CHECK(swf_parse(d, len, e, &n) == 3);
	memcpy(d, c, sizeof d); put32(d + 12, 0); CHECK(swf_parse(d, len, e, &n) == 3);
	memcpy(d, c, sizeof d); put32(d + 12, 4); CHECK(swf_parse(d, len, e, &n) == 3);
	memcpy(d, c, sizeof d); put32(d + 12, 3); put32(d + SWF_HDR + 8, 4); CHECK(swf_parse(d, len, e, &n) == 4);	/* offset unaligned */
	memcpy(d, c, sizeof d); put32(d + SWF_HDR + SWF_ENTRY * 2 + 8, swf_le32(c + SWF_HDR + SWF_ENTRY * 2 + 8) + 1); CHECK(swf_parse(d, len, e, &n) == 4);	/* in range but unaligned */
	memcpy(d, c, sizeof d); put32(d + SWF_HDR + 8, 8); CHECK(swf_parse(d, len, e, &n) == 4);	/* offset inside the header */
	memcpy(d, c, sizeof d); put32(d + SWF_HDR + 4, 0); CHECK(swf_parse(d, len, e, &n) == 4);	/* empty blob */
	memcpy(d, c, sizeof d); put32(d + SWF_HDR + 4, 0xFFFFFFF0u); CHECK(swf_parse(d, len, e, &n) == 4);	/* length overflow */
	memcpy(d, c, sizeof d); memcpy(d + SWF_HDR + SWF_ENTRY, "FW  ", 4); CHECK(swf_parse(d, len, e, &n) == 5);	/* duplicate tag */
	CHECK(swf_parse(c, len, e, &n) == 0);
	for (i = 0; i < 3; i++)
		for (b = 0; b < 8; b += 7) {
			memcpy(d, c, sizeof d); d[swf_le32(c + SWF_HDR + SWF_ENTRY * i + 8) + (b ? e[i].length - 1 : 0)] ^= 1u << b;
			CHECK(swf_parse(d, len, e, &n) == 6);
		}
	/* gap lines: full container, a container without CLM (1- and 2-entry containers pass swf_parse, F6), and the CLM tag only as the 3rd/1st entry */
	CHECK(swf_parse(c, len, e, &n) == 0 && n == 3);
	check_gaps(e, n, 1);
	check_gaps(e, 2, 0);		/* FW, NVRM only */
	check_gaps(e, 1, 0);		/* FW only */
	check_gaps(e, 0, 0);		/* never parsed this way, but the gap list must not depend on n > 0 */
	{
		struct swf_entry r[3] = { e[2], e[0], e[1] };	/* CLM first: order does not matter */

		check_gaps(r, 3, 1);
	}
	{
		struct swf_entry r[2] = { e[0], e[1] };

		r[1].tag[3] = 'X';		/* "NVRX": only an exact "CLM " counts */
		check_gaps(r, 2, 0);
		memcpy(r[1].tag, "CLM!", 4);	/* near miss */
		check_gaps(r, 2, 0);
		memcpy(r[1].tag, "CLM ", 4);
		check_gaps(r, 2, 1);
		memcpy(r[1].tag, "cLM ", 4);
		check_gaps(r, 2, 0);
	}
	if (fails) { printf("RESULT: FAIL wlan_fw_host_test (%u checks)\n", fails); return 1; }
	printf("RESULT: PASS wlan_fw_host_test\n");
	return 0;
}
