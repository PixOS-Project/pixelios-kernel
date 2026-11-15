/*
 * Host test of the L25/L26 engine pieces (T-f5f7, SPEC-WIFI Part E TRANSPORT block s.2-s.3). No hardware.
 *
 * Step 2 (this file, part A): the WLANCRED reader sweet_wlan_cred.h. Oracles: the blob and the PMK below were produced by tools/mkwlancred.py build_blob and
 * python hashlib.pbkdf2_hmac on made-up canary values (host twin of the shim code, not derived from it); the crc32 core is pinned to the standard check
 * value 0xCBF43926 of "123456789". Clauses (each a mutant in tools/test_wlan_l25_mutants.py once the mutant lane exists):
 *   C1 good blob: loaded line printed once, fields and PMK exact   C2 the region (all avail bytes) and the working copy are zero afterwards
 *   C3 no canary passphrase / PMK in the log, raw, hex or base64-free hex   C4 each bad field (magic, crc, length, security, padding, short, NULL) prints the
 *   creds gap line, stops (-1), leaves *out zero and still wipes the region   C5 failure and success lines carry no value
 * The data path (envelope, flow ring, rx posts, host 4-way, keys, ARP and echo) is tested in wlan_l25_host_test.c (T-38cc).
 */
#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static char logbuf[1 << 16];
static unsigned loglen;
static void lg(const char *s) { loglen += (unsigned)snprintf(logbuf + loglen, sizeof logbuf - loglen, "%s\n", s); }
#define SWJ_LOG(s) lg(s)
#define SWJ_GAP(s) lg(s)
#define SWJ_RANDOM(buf, len) memset((buf), 0x42, (len))
#define SWC_LOG(s) lg(s)
#define SWC_GAP(s) lg(s)
#include "sweet_wlan_join.h"
#include "sweet_wlan_cred.h"

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

#define PSK "canary-pass-0123456789"
static const uint8_t BLOB[SWC_SIZE] = {
	0x57,0x4c,0x41,0x4e,0x43,0x52,0x42,0x31,0x00,0x00,0x00,0x00,0x0b,0x00,0x00,0x00,0x16,0x00,0x00,0x00,0x63,0x61,0x6e,0x61,0x72,0x79,0x2d,0x73,0x73,0x69,0x64,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x63,0x61,0x6e,0x61,0x72,0x79,0x2d,0x70,0x61,0x73,0x73,0x2d,
	0x30,0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xc0,0x00,0x02,0x0a,0xc0,0x00,0x02,0x01,0xcc,0xba,0x16,0xbd };
static const uint8_t PMK[32] = {
	0x49,0x1f,0xac,0x1d,0x58,0xde,0x9e,0x8d,0x8b,0xe4,0x1a,0x2d,0x4d,0xc6,0xf0,0x9a,0x44,0x9f,0xab,0xdc,0x16,0x8f,0xc2,0xc3,0xd3,0xc8,0x72,0xf1,0x8c,0xe0,0x69,0x10 };

static int all_zero(const volatile uint8_t *p, uint32_t n)
{
	uint32_t i;

	for (i = 0; i < n; i++)
		if (p[i] != 0u)
			return 0;
	return 1;
}
/* 1 when `needle` (n bytes) occurs in the log as raw bytes or as lower/upper-case hex */
static int log_has_secret(const uint8_t *needle, uint32_t n)
{
	char *hx = malloc(2u * n + 1u), *hu = malloc(2u * n + 1u);
	uint32_t i;
	int r;

	if (hx == NULL || hu == NULL)
		abort();
	for (i = 0; i < n; i++) {
		snprintf(hx + 2u * i, 3, "%02x", needle[i]);
		snprintf(hu + 2u * i, 3, "%02X", needle[i]);
	}
	r = memmem(logbuf, loglen, needle, n) != NULL || strstr(logbuf, hx) != NULL || strstr(logbuf, hu) != NULL;
	free(hx);
	free(hu);
	return r;
}

/* a region of `avail` bytes holding `blob` (exact-size heap copies: ASan sees any over-read or over-write) */
static int run_load(const uint8_t *blob, uint32_t avail, struct swc_creds *c, uint8_t **regp)
{
	uint8_t *reg = avail != 0u ? malloc(avail) : NULL;
	uint8_t *work = malloc(SWC_SIZE);
	int r;

	if (reg == NULL && avail != 0u)
		abort();
	if (reg != NULL)
		memcpy(reg, blob, avail < SWC_SIZE ? avail : SWC_SIZE);
	if (reg != NULL && avail > SWC_SIZE)
		memset(reg + SWC_SIZE, 0x5A, avail - SWC_SIZE);     /* the rest of the page: must be wiped too */
	loglen = 0u;
	logbuf[0] = '\0';
	r = swc_load(reg, avail, work, c);
	CHECK(all_zero(work, SWC_SIZE));                                    /* C2: the stack copy of the passphrase */
	free(work);
	*regp = reg;
	return r;
}

static void fix_crc(uint8_t *b)
{
	uint32_t c = swc_crc32(b, SWC_SIZE - 4u);

	b[0x7c] = (uint8_t)c; b[0x7d] = (uint8_t)(c >> 8); b[0x7e] = (uint8_t)(c >> 16); b[0x7f] = (uint8_t)(c >> 24);
}

static void test_good(void)
{
	struct swc_creds c;
	uint8_t *reg;
	int r;

	CHECK(swc_crc32((const uint8_t *)"123456789", 9u) == 0xCBF43926u);          /* the crc core against the standard check value */
	CHECK(swc_check(BLOB) == 0);
	r = run_load(BLOB, 4096u, &c, &reg);
	CHECK(r == 0);                                                      /* C1 */
	CHECK(strcmp(logbuf, "SWEET WLAN: credentials loaded security=wpa2-psk\n") == 0);
	CHECK(c.ssid_len == 11u && memcmp(c.ssid, "canary-ssid", 11) == 0 && all_zero(c.ssid + 11, 21u));
	CHECK(memcmp(c.pmk, PMK, 32) == 0);
	CHECK(c.ip[0] == 192 && c.ip[1] == 0 && c.ip[2] == 2 && c.ip[3] == 10 && c.gw[0] == 192 && c.gw[3] == 1);
	CHECK(all_zero(reg, 4096u));                                        /* C2: the whole page, not only the 128 bytes */
	CHECK(!log_has_secret((const uint8_t *)PSK, (uint32_t)strlen(PSK)) && !log_has_secret(PMK, 32u));    /* C3 */
	CHECK(!log_has_secret((const uint8_t *)"canary-ssid", 11u) && !log_has_secret(c.ip, 4u));           /* C5: no ssid, no ip */
	free(reg);
	r = run_load(BLOB, SWC_SIZE, &c, &reg);                             /* exactly 128 bytes available */
	CHECK(r == 0 && all_zero(reg, SWC_SIZE));
	free(reg);
}

static void expect_bad(const uint8_t *blob, uint32_t avail, const char *gap, const char *what)
{
	struct swc_creds c;
	uint8_t *reg;
	int r;

	memset(&c, 0xA5, sizeof c);
	r = run_load(blob, avail, &c, &reg);
	if (r != -1 || strstr(logbuf, gap) == NULL || strstr(logbuf, "credentials loaded") != NULL || !all_zero((const uint8_t *)&c, sizeof c) ||
	    (reg != NULL && !all_zero(reg, avail)) || log_has_secret((const uint8_t *)PSK, (uint32_t)strlen(PSK)) || log_has_secret(PMK, 32u)) {
		fprintf(stderr, "FAIL bad blob case: %s (r=%d log=%s)\n", what, r, logbuf);
		fails++;
	}
	free(reg);
}

static void test_bad(void)
{
	uint8_t b[SWC_SIZE];

	memcpy(b, BLOB, SWC_SIZE); b[0] ^= 1u;
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "magic");                  /* crc still the old one: both wrong, any field makes it invalid */
	memcpy(b, BLOB, SWC_SIZE); b[0x20] ^= 1u;
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "crc (a ssid byte flipped, crc not fixed)");
	memcpy(b, BLOB, SWC_SIZE); b[0x7c] ^= 1u;
	expect_bad(b, 4096u, SWC_GAP_INVALID, "crc field");
	memcpy(b, BLOB, SWC_SIZE); b[0x08] = 1u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "security != wpa2-psk, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x0c] = 0u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "ssid_len 0, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x0c] = 33u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "ssid_len 33, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x10] = 7u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "psk_len 7, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x10] = 64u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "psk_len 64, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x11] = 1u; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "psk_len high byte set, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x14 + 20] = 'x'; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "ssid padding not zero, crc valid");
	memcpy(b, BLOB, SWC_SIZE); b[0x34 + 40] = 'x'; fix_crc(b);
	expect_bad(b, SWC_SIZE, SWC_GAP_INVALID, "psk padding not zero, crc valid");
	expect_bad(BLOB, SWC_SIZE - 1u, SWC_GAP_MISSING, "region 127 bytes");
	expect_bad(BLOB, 0u, SWC_GAP_MISSING, "region absent (NULL)");
}

int main(void)
{
	test_good();
	test_bad();
	if (fails != 0u) {
		printf("RESULT: FAIL wlan_l25_cred_host_test %u checks failed\n", fails);
		return 1;
	}
	printf("RESULT: PASS wlan_l25_cred_host_test (credentials only; the data path is wlan_l25_host_test)\n");
	return 0;
}
