/* Host test of the pure NVRAM part of xnu patch 0131 (sweet_wlan_wr.h: swl_nv_process, swl_nv_dhd; docs/research/43 row W11): a C port of Broadcom's process_nvram_vars() (bcmutils.c:5101-5146)
 * and of what its caller dhdpcie_download_nvram() does around it (dhd_pcie.c:4208-4253). The expected bytes below are written out by hand from those source lines (known answers), not computed by
 * the header; a second, differently structured reference (two passes over the lines) is run against the port on a synthetic text of every length and shape. The synthetic text is made here; no
 * firmware or calibration file is read or committed (AGENTS.md rule 4). tools/test_wlan_wr_mutants.py builds this test against every header mutant too. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "sweet_wlan_chip.h"
#include "sweet_wlan_wr.h"

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

/* one known answer: the file text (length given, it may hold NULs), the expected swl_nv_dhd() result (varsz) and the first `want_len` bytes of the buffer after it (the rest up to varsize is zero) */
struct kat {
	const char *name;
	const char *in;
	uint32_t in_len;
	uint32_t varsz;
	const char *want;
	uint32_t want_len;
};

#define S(x) x, (uint32_t)(sizeof x - 1u)

static const struct kat kats[] = {
	/* plain: two lines, the final newline becomes the forced NUL (kept as a byte), 8 bytes, no pad, one extra NUL -> varsz 9 */
	{ "two lines", S("a=1\nb=2\n"), 9, "a=1\0b=2\0", 8 },
	/* CR is dropped wherever it is; the forced NUL replaces the last LF */
	{ "CRLF", S("a=1\r\nb=2\r\n"), 9, "a=1\0b=2\0", 8 },
	{ "CR in the middle of a line", S("a=\r1\nb=2\n"), 9, "a=1\0b=2\0", 8 },
	/* a comment line gives nothing, not even an empty variable; '#' after text ends the line there */
	{ "comment line", S("#c\na=1\n"), 5, "a=1\0", 4 },
	{ "comment after text", S("a=b#c\nd=e\n"), 9, "a=b\0d=e\0", 8 },
	/* empty lines give nothing; the forced NUL at the end is a byte of column 0 and is kept: "a=1\0\0" (5) does not pad to 8 (8 > 7), caller rounds to 8, +1 */
	{ "blank lines", S("\n\na=1\n\n"), 9, "a=1\0\0", 5 },
	/* the last line has no newline: the forced NUL replaces its last character */
	{ "no final newline", S("a=1\nb=2"), 9, "a=1\0b=\0", 7 },
	/* the pad fits (7 -> 8 <= 8): process_nvram_vars pads, the caller adds one NUL: varsz 9 */
	{ "pad fits", S("abc=1\n\n\n"), 9, "abc=1\0\0\0", 8 },
	/* a text of one newline: forced NUL kept (1 byte), no pad inside (4 > 1), caller rounds to 4, +1 */
	{ "one newline", S("\n"), 5, "\0", 1 },
	/* everything is comment (the newline that would end it is the forced NUL): DHD downloads nothing */
	{ "only a comment", S("#only a comment\n"), 0, "", 0 },
	{ "empty", "", 0, 0, "", 0 },
	/* processed length a multiple of 4 already (8): no pad; 9 bytes handed over, 12 stored */
	{ "multiple of four", S("abcd=12\n"), 9, "abcd=12\0", 8 },
	/* 7 bytes kept ("abc\0d\0" + the forced NUL), the input is longer (19), so the pad to 8 fits; the bytes up to len are zero-filled */
	{ "pad fits in a long input", S("abc\nd\n#zzzzzzzzzz\n\n"), 9, "abc\0d\0\0\0", 8 },
};

static uint8_t gbuf[16 + SWW_NV_DHD_MAX + 16];          /* 16 guard bytes in front of the buffer: a write before it is a bug */
#define buf (gbuf + 16)

/* round varsz up to 4 as dhdpcie_bus_write_vars does (dhd_pcie.c:10887) */
static uint32_t roundup4(uint32_t v)
{
	return (v + 3u) & ~3u;
}

static void known_answers(void)
{
	unsigned i;

	for (i = 0; i < sizeof kats / sizeof kats[0]; i++) {
		const struct kat *k = &kats[i];
		uint32_t vz, j, bad = 0;

		memset(gbuf, 0xA5, sizeof gbuf);        /* the scratch is dirty: only the bytes the port writes may show */
		memcpy(buf, k->in, k->in_len);
		vz = swl_nv_dhd(buf, k->in_len);
		for (j = 0; j < 16; j++)
			CHECK(gbuf[j] == 0xA5);
		for (j = k->in_len + 8u; j < k->in_len + 40u; j++)      /* nothing is written past len + 8 (the pad, the extra NUL and the rounding stay inside) */
			CHECK(buf[j] == 0xA5);
		if (k->in_len == 0)
			for (j = 0; j < 8; j++)
				CHECK(buf[j] == 0xA5);                  /* nothing at all for an empty file */
		if (vz != k->varsz) {
			fprintf(stderr, "FAIL kat '%s': varsz %u want %u\n", k->name, vz, k->varsz);
			fails++;
			continue;
		}
		if (vz == 0)
			continue;
		for (j = 0; j < roundup4(vz); j++) {
			uint8_t want = j < k->want_len ? (uint8_t)k->want[j] : 0;

			if (buf[j] != want)
				bad++;
		}
		if (bad) {
			fprintf(stderr, "FAIL kat '%s': %u bytes differ\n", k->name, bad);
			fails++;
		}
	}
}

/* the process step alone (no caller steps), its return value and the zero fill up to len */
static void process_alone(void)
{
	static const uint8_t in[] = "a=1\nb\n\n#c\r\nd=2\n";           /* 15 chars + NUL */
	uint8_t b[32];
	uint32_t r;

	memset(b, 0xA5, sizeof b);
	memcpy(b, in, sizeof in - 1u);
	r = swl_nv_process(b, (uint32_t)sizeof in - 1u);
	/* a=1 NUL b NUL d=2 NUL = 10 bytes; 10 % 4 = 2, pad 2 -> 12 <= 15 fits; the bytes up to len are zero, the byte at len is untouched */
	CHECK(r == 12 && memcmp(b, "a=1\0b\0d=2\0\0\0", 12) == 0 && b[12] == 0 && b[13] == 0 && b[14] == 0 && b[15] == 0xA5);
	memset(b, 0xA5, sizeof b);
	memcpy(b, in, 10);                                                  /* "a=1\nb\n\n#c\r": the comment is never ended */
	r = swl_nv_process(b, 10);
	CHECK(r == 8 && memcmp(b, "a=1\0b\0\0\0", 8) == 0 && b[8] == 0 && b[9] == 0 && b[10] == 0xA5);      /* "a=1\0b\0" = 6 -> pad 2 -> 8 <= 10 */
	memset(b, 0xA5, sizeof b);
	memcpy(b, "a=1\nb\n", 6);
	r = swl_nv_process(b, 6);
	CHECK(r == 6 && memcmp(b, "a=1\0b\0", 6) == 0);                    /* 6 -> pad 2 -> 8 > 6: no pad */
	memset(b, 0xA5, sizeof b);
	memcpy(b, "a=1\nb\n\n\n", 8);
	r = swl_nv_process(b, 8);
	CHECK(r == 8 && memcmp(b, "a=1\0b\0\0\0", 8) == 0 && b[8] == 0xA5);        /* 6 -> pad 2 -> 8 <= 8: the pad fits exactly */
}

/* the cap: DHD downloads only 0 < len < 32 KiB (dhd_pcie.c:4208, MAX_NVRAMBUF_SIZE bcm4389-dhd.h:509) */
static void cap(void)
{
	uint32_t i, n;

	for (n = 32766; n <= 32769; n++) {
		memset(buf, 0, SWW_NV_DHD_MAX + 16);
		for (i = 0; i < n; i++)
			buf[i] = (i % 8u) == 7u ? '\n' : (uint8_t)('a' + i % 8u);
		if (n < SWW_NV_DHD_MAX)
			CHECK(swl_nv_dhd(buf, n) == roundup4(n) + 1u);  /* every byte kept (8-byte lines, no empty ones), the last one is the forced NUL; the pad does not fit, the caller rounds up, one NUL more */
		else
			CHECK(swl_nv_dhd(buf, n) == 0 && buf[0] == 'a');        /* refused untouched */
	}
	CHECK(SWW_NV_DHD_MAX == 32768u);
}

/* a differently structured reference: split the (forced-NUL) text into lines at LF; per line drop CR, cut at the first '#', keep the rest if non-empty (a line that is empty after the cut gives nothing);
 * every kept line ends with a NUL. The NUL that replaced the last byte is an ordinary byte of its line. Then pad/round as the caller does. Returns varsz, out holds roundup4(varsz) bytes. */
static uint32_t ref_nv(const uint8_t *in, uint32_t len, uint8_t *out)
{
	uint8_t *t = malloc(len + 16);
	uint32_t o = 0, i = 0, l, k, fit;

	memcpy(t, in, len);
	t[len - 1u] = 0;
	while (i < len) {
		uint32_t st = i, ln = 0, n_ln;
		uint8_t line[SWW_NV_DHD_MAX];
		int cut = 0;

		while (i < len && t[i] != '\n')
			i++;
		n_ln = i - st;
		for (k = 0; k < n_ln; k++) {
			uint8_t c = t[st + k];

			if (c == '\r')
				continue;
			if (c == '#')
				cut = 1;
			if (!cut)
				line[ln++] = c;
		}
		if (i < len)
			i++;                    /* the LF */
		if (ln == 0)
			continue;
		memcpy(out + o, line, ln);
		o += ln;
		if (st + n_ln < len)            /* the line ended with an LF in the text: a NUL stands for it */
			out[o++] = 0;
	}
	/* process_nvram_vars pads inside len only; then the caller rounds up and adds one NUL */
	l = o;
	fit = (l % 4u) ? l + (4u - l % 4u) <= len : 1;
	if (l % 4u && fit)
		l += 4u - l % 4u;
	if (l % 4u)
		l += 4u - l % 4u;
	free(t);
	memset(out + o, 0, roundup4(l + 1u) + 8u - o);
	return l ? l + 1u : 0;
}

/* a synthetic text with every feature, of every length from 1 to 700: the port and the reference agree on varsz and every byte */
static void against_reference(void)
{
	static uint8_t txt[800], a[SWW_NV_DHD_MAX + 16], b[SWW_NV_DHD_MAX + 16];
	static const char *const frag[] = { "name=value\n", "# comment line\n", "\n", "key2=1 2 3\r\n", "x=y # trailing\n", "last=1", "\r\n", "abc\n" };
	uint32_t n, i, o, f = 0, vz, vr;

	for (n = 1; n <= 700; n++) {
		o = 0;
		while (o < n) {
			const char *s = frag[(f++ * 5u + n) % (sizeof frag / sizeof frag[0])];

			for (i = 0; s[i] && o < n; i++)
				txt[o++] = (uint8_t)s[i];
		}
		memset(a, 0xA5, sizeof a);
		memcpy(a, txt, n);
		vz = swl_nv_dhd(a, n);
		memset(b, 0, sizeof b);
		vr = ref_nv(txt, n, b);
		if (vz != vr || (vz && memcmp(a, b, roundup4(vz)) != 0)) {
			fprintf(stderr, "FAIL synthetic n=%u: port varsz %u reference %u\n", n, vz, vr);
			fails++;
			break;
		}
	}
}

/* the two bounds the writer relies on: processed text never grows, and the largest accepted input gives varsize <= SWW_NV_VARS_MAX */
static void bounds(void)
{
	static uint8_t a[SWW_NV_BUF + 16];
	uint32_t n, i, vz, worst = 0;

	for (n = SWW_NV_VARS_MAX - 12; n <= SWW_NV_VARS_MAX; n++) {
		memset(a, 0xA5, sizeof a);
		for (i = 0; i < n; i++)
			a[i] = (uint8_t)('a' + i % 20u);       /* no newline at all: nothing is dropped except that the last byte is NUL */
		vz = swl_nv_dhd(a, n);
		CHECK(vz >= n && vz <= n + 4u);
		if (roundup4(vz) > worst)
			worst = roundup4(vz);
	}
	CHECK(worst > SWW_NV_VARS_MAX);        /* the bound is not vacuous: an input of exactly SWW_NV_VARS_MAX bytes can give more than SWW_NV_VARS_MAX (the writer refuses it) */
	memset(a, 'q', sizeof a);
	vz = swl_nv_dhd(a, SWW_NV_VARS_MAX - 4u);
	CHECK(roundup4(vz) == SWW_NV_VARS_MAX);        /* 27064 B kept whole -> varsz 27065 -> varsize 27068 = the bound */
	CHECK(SWW_NV_VARS_MAX + 4u + SWW_RTLV_BYTES == 27208u && SWW_RAM_VARS == 27208u);
}

int main(void)
{
	known_answers();
	process_alone();
	cap();
	against_reference();
	bounds();
	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_nv_host_test (%u checks)\n", fails);
		return 1;
	}
	fprintf(stderr, "RESULT: PASS wlan_nv_host_test\n");
	return 0;
}
