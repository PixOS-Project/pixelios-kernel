/* wlan_4way_host_test.c - host test for sweet_wlan_join.h (T-5dc1, SPEC-WIFI Part E rows H1-H12).
 * Anchors: FIPS 180 / RFC 2202 / RFC 6070 / FIPS-197 / RFC 3394 / IEEE 802.11i H.4 vectors typed from the standards (each cross-checked
 * with python hashlib/openssl when written), plus an EAPOL M1-M4 replay whose expected bytes come from the INDEPENDENT python
 * reference tools/gen_wlan_4way_vectors.py (shim/wlan_4way_vectors.h). Build: gcc -O1 -Wall -Wextra -Werror (ASan/UBSan also clean). */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char g_log[8192];
static unsigned g_gaps, g_lines;
static int g_rand_fail;
static const uint8_t *g_snonce;
static void lg(const char *s) { size_t n = strlen(g_log); g_lines++; snprintf(g_log + n, sizeof g_log - n, "%s\n", s); }
#define SWJ_LOG(s) lg(s)
#define SWJ_GAP(s) do { g_gaps++; lg(s); } while (0)
#define SWJ_RANDOM(b, n) (g_rand_fail ? -1 : (memcpy((b), g_snonce, (n)), 0))
#include "sweet_wlan_join.h"
#include "wlan_4way_vectors.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("CHECK FAILED line %d: %s\n", __LINE__, #c); fails++; } } while (0)

static int hexeq(const uint8_t *b, const char *hex, size_t n)
{
	size_t i;
	for (i = 0; i < n; i++) {
		unsigned v;
		if (sscanf(hex + 2 * i, "%2x", &v) != 1 || v != b[i])
			return 0;
	}
	return hex[2 * n] == 0;
}

/* the two firmware structs as the sources declare them: sizeof must be 164 and the offsets must be our constants */
struct bw_wsec_key {	/* OpenBSD bwfmreg.h:629-648 */
	uint32_t index, len; uint8_t data[32]; uint32_t pad_1[18]; uint32_t algo, flags; uint32_t pad_2[3];
	uint32_t iv_initialized, pad_3; struct { uint32_t hi; uint16_t lo, pad_4; } rxiv; uint32_t pad_5[2]; uint8_t ea[6];
};
struct s5_wsec_key {	/* DHD wlioctl.h.new:1993-2013 */
	uint32_t index, len; uint8_t data[32]; uint32_t PAD1[18]; uint32_t algo, flags; uint32_t PAD2[2]; int32_t PAD3;
	int32_t iv_initialized; int32_t PAD4; struct { uint32_t hi; uint16_t lo, PAD; } rxiv; uint32_t PAD5[2];
	uint8_t ea[6]; uint16_t PAD6;
};

static void test_primitives(void)
{
	struct swj_sha1 c; uint8_t d[20], o[64], pmk[32], kek[16], plain[64], w[72], u[64];
	const uint8_t *parts[1]; uint32_t lens[1];
	static const char two[] = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
	uint8_t k[80]; unsigned i; struct swj_aes a; uint8_t in[16], ct[16];

	swj_sha1_init(&c); swj_sha1_update(&c, (const uint8_t *)"abc", 3); swj_sha1_final(&c, d);
	CHECK(hexeq(d, "a9993e364706816aba3e25717850c26c9cd0d89d", 20));
	swj_sha1_init(&c); swj_sha1_update(&c, (const uint8_t *)two, (uint32_t)strlen(two)); swj_sha1_final(&c, d);
	CHECK(hexeq(d, "84983e441c3bd26ebaae4aa1f95129e5e54670f1", 20));

	memset(k, 0x0b, 20); parts[0] = (const uint8_t *)"Hi There"; lens[0] = 8;	/* RFC 2202 case 1 */
	swj_hmac_sha1(k, 20, parts, lens, 1, d); CHECK(hexeq(d, "b6173186" "55057264e28bc0b6fb378c8ef146be00", 20));
	parts[0] = (const uint8_t *)"what do ya want for nothing?"; lens[0] = 28;	/* case 2 */
	swj_hmac_sha1((const uint8_t *)"Jefe", 4, parts, lens, 1, d); CHECK(hexeq(d, "effcdf6ae5eb2fa2d27416d5f184df9c259a7c79", 20));
	memset(k, 0xaa, 80); parts[0] = (const uint8_t *)"Test Using Larger Than Block-Size Key - Hash Key First"; lens[0] = 54;	/* case 6: key > block */
	swj_hmac_sha1(k, 80, parts, lens, 1, d); CHECK(hexeq(d, "aa4ae5e15272d00e95705637ce8a3b55ed402112", 20));

	/* RFC 6070 PBKDF2-HMAC-SHA1 */
	CHECK(swj_pbkdf2_sha1((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 1, o, 20) == 0 &&
	      hexeq(o, "0c60c80f961f0e71f3a9b524af6012062fe037a6", 20));
	CHECK(swj_pbkdf2_sha1((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 2, o, 20) == 0 &&
	      hexeq(o, "ea6c014dc72d6f8ccd1ed92ace1d41f0d8de8957", 20));
	CHECK(swj_pbkdf2_sha1((const uint8_t *)"password", 8, (const uint8_t *)"salt", 4, 4096, o, 20) == 0 &&
	      hexeq(o, "4b007901b765489abead49d926f721d065a429c1", 20));
	CHECK(swj_pbkdf2_sha1((const uint8_t *)"pass", 4, (const uint8_t *)"s", 1, 1, o, 40) == 0);	/* 2 blocks, no overrun (ASan) */
	CHECK(swj_pbkdf2_sha1((const uint8_t *)"p", 1, (const uint8_t *)"s", 1, 0, o, 20) == -1);

	/* IEEE 802.11i-2004 H.4 PSK test cases 1 and 2 */
	CHECK(swj_pmk_from_passphrase("password", 8, (const uint8_t *)"IEEE", 4, pmk) == 0 &&
	      hexeq(pmk, "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", 32));
	CHECK(swj_pmk_from_passphrase("ThisIsAPassword", 15, (const uint8_t *)"ThisIsASSID", 11, pmk) == 0 &&
	      hexeq(pmk, "0dc0d6eb90555ed6419756b9a15ec3e3209b63df707dd508d14581f8982721af", 32));
	g_gaps = 0;
	CHECK(swj_pmk_from_passphrase("short", 5, (const uint8_t *)"IEEE", 4, pmk) == -1 && g_gaps == 1);
	CHECK(swj_pmk_from_passphrase("password", 8, (const uint8_t *)"", 0, pmk) == -1 && g_gaps == 2);

	/* PRF-SHA1 / PTK: bytes from the python reference (V_PTK) */
	{
		uint8_t ptk[48];
		swj_ptk_derive(V_PMK, V_AA, V_SPA, V_ANONCE, V_SNONCE, ptk);
		CHECK(memcmp(ptk, V_PTK, 48) == 0);
		swj_ptk_derive(V_PMK, V_SPA, V_AA, V_SNONCE, V_ANONCE, ptk);	/* min/max ordering: argument order must not matter */
		CHECK(memcmp(ptk, V_PTK, 48) == 0);
	}

	/* FIPS-197 C.1 */
	for (i = 0; i < 16; i++) { k[i] = (uint8_t)i; in[i] = (uint8_t)(i * 0x11); }
	swj_aes_init(&a, k); swj_aes_enc(&a, in, ct);
	CHECK(hexeq(ct, "69c4e0d86a7b0430d8cdb78070b4c55a", 16));
	swj_aes_dec(&a, ct, d); CHECK(memcmp(d, in, 16) == 0);

	/* RFC 3394 4.1 */
	for (i = 0; i < 16; i++) { kek[i] = (uint8_t)i; plain[i] = (uint8_t)(i * 0x11); }
	CHECK(swj_aes_wrap(kek, plain, 16, w) == 0 && hexeq(w, "1fa68b0a8112b447aef34bd8fb5a7b829d3e862371d2cfe5", 24));
	memset(u, 0, sizeof u);
	CHECK(swj_aes_unwrap(kek, w, 24, u) == 0 && memcmp(u, plain, 16) == 0);
	w[3] ^= 1; CHECK(swj_aes_unwrap(kek, w, 24, u) == -1);	/* integrity register */
	CHECK(swj_aes_unwrap(kek, w, 16, u) == -1 && swj_aes_unwrap(kek, w, 23, u) == -1 && swj_aes_wrap(kek, plain, 8, w) == -1);
	for (i = 0; i < 48; i++) plain[i] = (uint8_t)(i * 7 + 1);	/* n = 6 round trip (the M3 size) */
	CHECK(swj_aes_wrap(kek, plain, 48, w) == 0 && swj_aes_unwrap(kek, w, 56, u) == 0 && memcmp(u, plain, 48) == 0);
}

static void test_structs(void)
{
	uint8_t kb[SWJ_WSEC_KEY_SIZE]; uint8_t key[16];

	CHECK(sizeof(struct bw_wsec_key) == 164 && sizeof(struct s5_wsec_key) == 164 && SWJ_WSEC_KEY_SIZE == 164);
	CHECK(offsetof(struct bw_wsec_key, algo) == SWJ_WSEC_OFF_ALGO && offsetof(struct s5_wsec_key, algo) == SWJ_WSEC_OFF_ALGO);
	CHECK(offsetof(struct bw_wsec_key, flags) == SWJ_WSEC_OFF_FLAGS && offsetof(struct s5_wsec_key, flags) == SWJ_WSEC_OFF_FLAGS);
	CHECK(offsetof(struct bw_wsec_key, data) == SWJ_WSEC_OFF_DATA && offsetof(struct s5_wsec_key, data) == SWJ_WSEC_OFF_DATA);
	CHECK(offsetof(struct bw_wsec_key, iv_initialized) == SWJ_WSEC_OFF_IVINIT && offsetof(struct s5_wsec_key, iv_initialized) == SWJ_WSEC_OFF_IVINIT);
	CHECK(offsetof(struct bw_wsec_key, ea) == SWJ_WSEC_OFF_EA && offsetof(struct s5_wsec_key, ea) == SWJ_WSEC_OFF_EA);
	CHECK(offsetof(struct bw_wsec_key, len) == SWJ_WSEC_OFF_LEN && offsetof(struct bw_wsec_key, index) == SWJ_WSEC_OFF_INDEX);
	memset(key, 0x42, 16);
	CHECK(swj_wsec_key(kb, 3, key, 33, 4, 0, NULL) == -1);
	CHECK(swj_wsec_key(kb, 3, key, 16, 4, 2, V_AA) == 0);
	CHECK(kb[0] == 3 && kb[4] == 16 && kb[8] == 0x42 && kb[23] == 0x42 && kb[24] == 0 && kb[112] == 4 && kb[116] == 2);
	CHECK(memcmp(kb + 156, V_AA, 6) == 0 && kb[162] == 0 && kb[163] == 0);
	CHECK(swj_wsec_merge(SWJ_WSEC_WEP | SWJ_WSEC_TKIP | 0x8u, SWJ_WSEC_AES) == (0x8u | SWJ_WSEC_AES));
}

/* T-857c knob A: the boot-arg value parser, the validity rule and the cur_etheraddr insertion */
static void test_join_mac(void)
{
	struct swj_cmd c[7]; uint8_t bssid[6] = { 2, 0, 0, 0xaa, 0, 1 }, m[6], keep[6];
	const uint8_t ssid[] = "TestNet"; static const uint8_t want[6] = { 0x3c, 0x5a, 0xb4, 0x0f, 0xA1, 0x09 };
	unsigned i;

	/* parser: exactly 12 hex digits, both cases; nothing else, and a failed parse leaves the output alone */
	memset(m, 0x77, 6); memcpy(keep, m, 6);
	CHECK(swj_parse_mac("3c5ab40fa109", m) == 0 && memcmp(m, want, 6) == 0);
	CHECK(swj_parse_mac("3C5AB40FA109", m) == 0 && memcmp(m, want, 6) == 0);
	memcpy(m, keep, 6);
	CHECK(swj_parse_mac("", m) == -1 && swj_parse_mac("3c5ab40fa10", m) == -1 && swj_parse_mac("3c5ab40fa1090", m) == -1 && swj_parse_mac("3c:5a:b4:0f:a1:09", m) == -1);
	CHECK(swj_parse_mac("3c5ab40fa10g", m) == -1 && swj_parse_mac("3c5ab40fa10 ", m) == -1 && swj_parse_mac(" 3c5ab40fa10", m) == -1 && swj_parse_mac(NULL, m) == -1);
	CHECK(memcmp(m, keep, 6) == 0);
	/* validity: unicast, globally administered (bit 1 clear), not zero */
	CHECK(swj_mac_ok(want) == 1);
	{
		uint8_t b[6]; memcpy(b, want, 6);
		b[0] = 0x3d; CHECK(swj_mac_ok(b) == 0);		/* multicast bit */
		b[0] = 0x3e; CHECK(swj_mac_ok(b) == 0);		/* locally administered bit (the baseline SPA has it set) */
		b[0] = 0x00; CHECK(swj_mac_ok(b) == 1);		/* octet 0 zero is fine while another octet is not */
		memset(b, 0, 6); CHECK(swj_mac_ok(b) == 0);
	}
	/* insertion: command 1, the six base commands shifted up by one and untouched, the join still last */
	g_gaps = 0;
	CHECK(swj_join_cmds(c, ssid, 7, bssid, 0) == 6u);
	{
		struct swj_cmd base[6]; memcpy(base, c, sizeof base);
		CHECK(swj_join_cmds_mac(c, 6u, want) == 7u && g_gaps == 0);
		CHECK(c[0].len == base[0].len && memcmp(&c[0], &base[0], sizeof c[0]) == 0);
		for (i = 1; i < 6u; i++) CHECK(c[i + 1u].len == base[i].len && memcmp(c[i + 1u].buf, base[i].buf, base[i].len) == 0 && strcmp(c[i + 1u].row, base[i].row) == 0);
	}
	CHECK(c[1].cmd == SWJ_WLC_SET_VAR && strcmp(c[1].row, "H1m") == 0 && c[1].len == 14u + 6u && strcmp((const char *)c[1].buf, "cur_etheraddr") == 0 && memcmp(c[1].buf + 14, want, 6) == 0);
	CHECK(strcmp((const char *)c[6].buf, "join") == 0 && strcmp((const char *)c[0].buf, "sup_wpa") == 0 && strcmp((const char *)c[2].buf, "wpa_auth") == 0);
	/* refusals: a loud gap, nothing inserted as success */
	{
		uint8_t b[6]; memcpy(b, want, 6);
		g_gaps = 0;
		CHECK(swj_join_cmds(c, ssid, 7, bssid, 0) == 6u);
		b[0] = 0x3e; CHECK(swj_join_cmds_mac(c, 6u, b) == 0u && g_gaps == 1);
		b[0] = 0x3d; CHECK(swj_join_cmds_mac(c, 6u, b) == 0u && g_gaps == 2);
		memset(b, 0, 6); CHECK(swj_join_cmds_mac(c, 6u, b) == 0u && g_gaps == 3);
		CHECK(swj_join_cmds_mac(c, 5u, want) == 0u && g_gaps == 4);		/* not the six base commands */
	}
}

static void test_join_cmds(void)
{
	struct swj_cmd c[6]; uint8_t bssid[6] = { 2, 0, 0, 0xaa, 0, 1 };
	const uint8_t ssid[] = "TestNet"; unsigned n, i;
	static const char *const nm[5] = { "sup_wpa", "wpa_auth", "wsec", "auth", "mfp" };
	static const uint8_t val[5] = { 0, 0x80, 4, 0, 0 };

	g_gaps = 0;
	n = swj_join_cmds(c, ssid, 7, bssid, 0);
	CHECK(n == 6 && g_gaps == 0);
	for (i = 0; i < 5; i++) {
		size_t nl = strlen(nm[i]) + 1;
		CHECK(c[i].cmd == SWJ_WLC_SET_VAR && c[i].len == nl + 4 && strcmp((const char *)c[i].buf, nm[i]) == 0);
		CHECK(c[i].buf[nl] == val[i] && c[i].buf[nl + 1] == 0 && c[i].buf[nl + 2] == 0 && c[i].buf[nl + 3] == 0);
	}
	CHECK(strcmp(c[0].row, "H1") == 0 && strcmp(c[1].row, "H2") == 0 && strcmp(c[2].row, "H3") == 0 && strcmp(c[5].row, "H5") == 0);
	CHECK(c[5].cmd == SWJ_WLC_SET_VAR && c[5].len == 5 + SWJ_EXT_JOIN_V1_SIZE && SWJ_EXT_JOIN_V1_SIZE == 76 && strcmp((const char *)c[5].buf, "join") == 0);
	{
		/* T-4ba0: the v1 layout of S5 wl_extjoin_params_v1_t (wlioctl.h.new:1514-1523) byte by byte: version 0, pad 0, SSID_len at 4, SSID at 8, scan at 40, assoc_v1 at 60 */
		const uint8_t *j = c[5].buf + 5;
		CHECK(j[0] == 0 && j[1] == 0 && j[2] == 0 && j[3] == 0);				/* version, pad (DHD never sets them) */
		CHECK(j[4] == 7 && j[5] == 0 && j[6] == 0 && j[7] == 0 && memcmp(j + 8, "TestNet", 7) == 0 && j[15] == 0 && j[39] == 0);	/* SSID_len u32, SSID[32] zero padded */
		CHECK(j[40] == 0xff && j[41] == 0 && j[42] == 0 && j[43] == 0);			/* scan_type -1, pad[3] */
		for (i = 44; i < 60; i++) CHECK(j[i] == 0xff);					/* nprobes, active, passive, home = -1 */
		CHECK(j[60] == 0 && j[61] == 0 && j[62] == 0 && j[63] == 0);				/* assoc_v1 version, flags (no BSSID hint) */
		CHECK(memcmp(j + 64, bssid, 6) == 0 && j[70] == 0 && j[71] == 0);			/* bssid, bssid_cnt 0 */
		for (i = 72; i < 76; i++) CHECK(j[i] == 0);					/* chanspec_num 0 = all channels, no chanspec_list */
	}
	{
		/* the v0 form (the one the firmware refused with -18) is kept as swj_join_ext, byte layout unchanged */
		uint8_t o[SWJ_EXT_JOIN_SIZE];
		CHECK(swj_join_ext(o, ssid, 7, bssid) == 0 && SWJ_EXT_JOIN_SIZE == 68 && o[0] == 7 && memcmp(o + 4, "TestNet", 7) == 0 && o[36] == 0xff && memcmp(o + 56, bssid, 6) == 0);
	}
	n = swj_join_cmds(c, ssid, 7, bssid, 1);
	CHECK(n == 6 && c[2].buf[5] == 6);	/* AES | TKIP when the group cipher is TKIP */
	CHECK(swj_join_cmds(c, ssid, 0, bssid, 0) == 0 && g_gaps == 1);
	{	/* ALL-ON T-32: SET_VAR "mkeep_alive" + the 11-byte packed v1 header, bytes typed from research 138 s.1 (version 1, length 11, period 30000 = 0x7530 LE, len_bytes 0, id 0) */
		static const uint8_t want_ka[23] = { 'm','k','e','e','p','_','a','l','i','v','e', 0x00, 0x01,0x00, 0x0b,0x00, 0x30,0x75,0x00,0x00, 0x00,0x00, 0x00 };
		struct swj_cmd k;
		unsigned g0 = g_gaps;

		memset(&k, 0xAA, sizeof k);
		CHECK(swj_mkeep_alive_cmd(&k, 30000u) == 1u && g_gaps == g0);
		CHECK(k.cmd == 263u && strcmp(k.row, "KA") == 0 && k.len == 23u && memcmp(k.buf, want_ka, 23) == 0);
		CHECK(SWJ_MKEEP_PERIOD_MS == 30000u && SWJ_MKEEP_HDR_LEN == 11u && SWJ_MKEEP_ID == 0u && SWJ_MKEEP_VERSION == 1u);
		CHECK(swj_mkeep_alive_cmd(&k, 0x01020304u) == 1u && k.len == 23u && k.buf[16] == 4 && k.buf[17] == 3 && k.buf[18] == 2 && k.buf[19] == 1);	/* period is little endian */
		CHECK(swj_mkeep_alive_cmd(&k, 0u) == 0u && g_gaps == g0 + 1u);					/* period 0 would stop it: refused with a GAP */
		CHECK(swj_mkeep_alive_cmd(&k, 0x80007530u) == 0u && g_gaps == g0 + 2u);				/* the immediate bit is refused */
		g_gaps = g0;
	}
	{
		uint8_t o[SWJ_JOIN_SIZE];
		CHECK(swj_join_plain(o, ssid, 7, bssid) == 0 && o[0] == 7 && memcmp(o + 4, "TestNet", 7) == 0 && memcmp(o + 36, bssid, 6) == 0);
		CHECK(swj_join_plain(o, ssid, 33, bssid) == -1 && SWJ_JOIN_SIZE == 48);
	}
	{
		uint8_t tiny[4];
		g_gaps = 0;
		CHECK(swj_var_u32(tiny, sizeof tiny, "wsec", 4) == 0 && g_gaps == 1);
	}
}

static int rx(struct swj_ctx *c, const uint8_t *f, uint32_t len, uint8_t *out, uint32_t *ol)
{
	uint8_t *g = malloc(len ? len : 1u);	/* exact-size heap copy: ASan flags any read past len */
	int r;
	if (len) memcpy(g, f, len);
	r = swj_rx(c, g, len, out, SWJ_TX_MAX, ol);
	free(g);
	return r;
}

static void test_handshake(void)
{
	struct swj_ctx c; uint8_t out[SWJ_TX_MAX], kb[SWJ_WSEC_KEY_SIZE], m3b[sizeof V_M3], pdu[SWJ_TX_MAX];
	uint32_t ol, i, pl; unsigned gaps0;
	uint8_t pmk[32];

	g_snonce = V_SNONCE;
	CHECK(swj_pmk_from_passphrase(V_PASS, (uint32_t)strlen(V_PASS), (const uint8_t *)V_SSID, (uint32_t)strlen(V_SSID), pmk) == 0 &&
	      memcmp(pmk, V_PMK, 32) == 0);
	CHECK(swj_ctx_init(&c, pmk, V_SPA, V_AA, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == 0);
	CHECK(swj_ctx_init(&c, pmk, V_SPA, V_AA, V_RSNE, 0, V_RSNE, sizeof V_RSNE) == -1);
	CHECK(swj_ctx_init(&c, pmk, V_SPA, V_AA, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == 0);

	/* not EAPOL / not ours */
	{
		uint8_t ip[60]; memset(ip, 0, sizeof ip); ip[12] = 0x08; ip[13] = 0x00;
		CHECK(rx(&c, ip, sizeof ip, out, &ol) == SWJ_RX_PASS && ol == 0);
		CHECK(rx(&c, ip, 13, out, &ol) == SWJ_RX_PASS);
	}
	/* M3 before M1 is dropped, nothing installed */
	CHECK(rx(&c, V_M3, sizeof V_M3, out, &ol) == SWJ_RX_CONSUMED && ol == 0 && !c.install_ptk);
	/* frame from a different source */
	memcpy(m3b, V_M1, sizeof V_M1); m3b[6] ^= 1;
	CHECK(rx(&c, m3b, sizeof V_M1, out, &ol) == SWJ_RX_CONSUMED && ol == 0 && c.state == SWJ_IDLE);

	/* M1 -> M2 exactly the python reference bytes */
	g_log[0] = 0;
	CHECK(rx(&c, V_M1, sizeof V_M1, out, &ol) == SWJ_RX_REPLY);
	CHECK(ol == sizeof V_M2 && memcmp(out, V_M2, sizeof V_M2) == 0);
	CHECK(c.state == SWJ_M2_SENT && !c.install_ptk && memcmp(c.tptk, V_PTK, 48) == 0);
	CHECK(strstr(g_log, "SWEET WLAN: eapol rx msg=1") && strstr(g_log, "SWEET WLAN: eapol tx msg=2"));

	/* corrupt M3 variants: each dropped with a GAP, no M4, no install */
	for (i = 0; i < 5; i++) {
		memcpy(m3b, V_M3, sizeof V_M3);
		if (i == 0) m3b[14 + 81 + 3] ^= 1;			/* MIC */
		else if (i == 1) m3b[14 + 17] ^= 1;			/* ANonce */
		else if (i == 2) m3b[14 + 99 + 5] ^= 1;			/* wrapped key data (MIC covers it too) */
		else if (i == 3) m3b[14 + 9 + 7] = 1;			/* replay counter 1 = not above M1's */
		else m3b[14 + 5 + 1] &= (uint8_t)~0x40u;		/* install bit cleared */
		gaps0 = g_gaps;
		CHECK(rx(&c, m3b, sizeof m3b, out, &ol) == SWJ_RX_CONSUMED && ol == 0 && !c.install_ptk && !c.install_gtk && g_gaps == gaps0 + 1);
	}
	/* truncated M3 at every length: never crashes (ASan), never installs */
	for (i = 0; i < sizeof V_M3; i++)
		(void)rx(&c, V_M3, i, out, &ol);
	CHECK(!c.install_ptk && c.state == SWJ_M2_SENT);

	/* the good M3 -> M4 exactly the python bytes, keys pending exactly once */
	g_log[0] = 0;
	CHECK(rx(&c, V_M3, sizeof V_M3, out, &ol) == SWJ_RX_REPLY);
	CHECK(ol == sizeof V_M4 && memcmp(out, V_M4, sizeof V_M4) == 0);
	CHECK(c.install_ptk == 1 && c.install_gtk == 1 && c.state == SWJ_PTK_DONE && c.gtk_id == V_GTK_ID);
	CHECK(strstr(g_log, "SWEET WLAN: eapol rx msg=3") && strstr(g_log, "SWEET WLAN: eapol tx msg=4") && strstr(g_log, SWJ_L_COMPLETE));
	CHECK(swj_wsec_key_pairwise(&c, kb) == 0 && kb[0] == 0 && kb[4] == 16 && memcmp(kb + 8, V_PTK + 32, 16) == 0 &&
	      kb[24] == 0 && kb[112] == 4 && kb[116] == 0 && memcmp(kb + 156, V_AA, 6) == 0);
	CHECK(swj_wsec_key_group(&c, kb) == 0 && kb[0] == V_GTK_ID && kb[4] == 16 && memcmp(kb + 8, V_GTK, 16) == 0 &&
	      kb[112] == 4 && kb[116] == 2 && memcmp(kb + 156, "\0\0\0\0\0\0", 6) == 0);
	c.install_ptk = c.install_gtk = 0;

	/* the same M3 again: replay counter not above -> dropped; no reinstall (KRACK guard) */
	CHECK(rx(&c, V_M3, sizeof V_M3, out, &ol) == SWJ_RX_CONSUMED && !c.install_ptk);
	/* a genuine M3 retransmission with a higher counter (built with the PTK, as the AP would): M4 again, still no reinstall */
	{
		const uint8_t kde[8] = { 0xdd, 0x16, 0x00, 0x0f, 0xac, 0x01, V_GTK_ID, 0 };
		uint8_t plain[48], wr[56];
		memset(plain, 0, sizeof plain); memcpy(plain, V_RSNE, sizeof V_RSNE); memcpy(plain + sizeof V_RSNE, kde, 8);
		memcpy(plain + sizeof V_RSNE + 8, V_GTK, 16); plain[sizeof V_RSNE + 24] = 0xdd;
		CHECK(swj_aes_wrap(c.tptk + 16, plain, 48, wr) == 0);
		pl = swj_eapol_build(pdu, 0x0002u | SWJ_KI_PAIRWISE | SWJ_KI_INSTALL | SWJ_KI_ACK | SWJ_KI_MIC | SWJ_KI_SECURE | SWJ_KI_ENC,
		    3, V_ANONCE, wr, 56, c.tptk);
		{
			uint8_t f[14 + SWJ_TX_MAX]; memcpy(f, V_M3, 14); memcpy(f + 14, pdu, pl);
			CHECK(rx(&c, f, 14 + pl, out, &ol) == SWJ_RX_REPLY && ol == sizeof V_M4 && !c.install_ptk && !c.install_gtk);
			CHECK(out[14 + 9 + 7] == 3 && ol == 14 + 99);	/* M4 echoes the new replay counter */
			/* same frame with a different RSNE byte in the key data: rejected (RSNE must equal the beacon's) */
			plain[10] ^= 1;
			CHECK(swj_aes_wrap(c.tptk + 16, plain, 48, wr) == 0);
			pl = swj_eapol_build(pdu, 0x13cau, 4, V_ANONCE, wr, 56, c.tptk);
			memcpy(f + 14, pdu, pl);
			gaps0 = g_gaps;
			CHECK(rx(&c, f, 14 + pl, out, &ol) == SWJ_RX_CONSUMED && ol == 0 && g_gaps == gaps0 + 1);
		}
	}

	/* a fresh M1 asks for a new PTK (rekey): reported as a GAP, answered, install flag armed again for exactly one M3 */
	gaps0 = g_gaps;
	CHECK(rx(&c, V_M1, sizeof V_M1, out, &ol) == SWJ_RX_REPLY && g_gaps == gaps0 + 1 && c.new_ptk_expected == 1);

	/* RNG failure: loud, no M2 */
	g_rand_fail = 1; gaps0 = g_gaps;
	CHECK(rx(&c, V_M1, sizeof V_M1, out, &ol) == SWJ_RX_CONSUMED && ol == 0 && g_gaps == gaps0 + 1);
	g_rand_fail = 0;

	/* descriptor version 1 (TKIP / WPA1): unsupported, loud */
	memcpy(m3b, V_M1, sizeof V_M1); m3b[14 + 5 + 1] = (m3b[14 + 6] & ~7u) | 1u;
	gaps0 = g_gaps;
	CHECK(rx(&c, m3b, sizeof V_M1, out, &ol) == SWJ_RX_CONSUMED && g_gaps == gaps0 + 1 && strstr(g_log, "version != 2"));

	/* secrets never reach the log: passphrase, PMK, PTK bytes, nonces, MACs are absent from every line */
	CHECK(strstr(g_log, V_PASS) == NULL);
	for (i = 0; i + 6 <= sizeof g_log - 1 && g_log[i + 5]; i++) {
		CHECK(memcmp(g_log + i, V_PMK, 6) != 0 && memcmp(g_log + i, V_PTK, 6) != 0 && memcmp(g_log + i, V_AA, 6) != 0 &&
		      memcmp(g_log + i, V_SPA, 6) != 0 && memcmp(g_log + i, V_GTK, 6) != 0);
	}
}

static void test_gap_text(void)
{
	static const char *const g[] = { SWJ_GAP_H5, SWJ_GAP_H6, SWJ_GAP_H7, SWJ_GAP_H11, SWJ_GAP_F1, SWJ_GAP_F2, SWJ_GAP_F3,
	    SWJ_GAP_F4, SWJ_GAP_F5, SWJ_GAP_F6, SWJ_GAP_F7, SWJ_GAP_X_REKEY, SWJ_GAP_X_V1, SWJ_GAP_X_RSNE };
	unsigned i;
	for (i = 0; i < sizeof g / sizeof g[0]; i++)
		CHECK(strncmp(g[i], "WIFI GAP: ", 10) == 0);
	CHECK(strncmp(SWJ_L_COMPLETE, "SWEET WLAN: 4way complete", 25) == 0);
}

/* review T-48a0 r1 F4 (T-4459): the PBKDF2/HMAC/SHA-1 stack temporaries hold the passphrase (HMAC key block and pads); after swj_pmk_from_passphrase returns, no byte run of it may remain on the stack
 * below the caller. The probe reads the dead stack region the call just used (an uninitialised local array read through a volatile pointer, the usual stack-residue probe); mutant: remove the swj_wipe calls
 * in sweet_wlan_join.h and this check fails (checked by hand, see the ticket Log). Run at -O0/-O1/-O2 by the gate of this test file. */
#define PROBE_BYTES 16384u
static const char RES_PASS[] = "Zq7-residue-probe-passphrase";

static __attribute__((noinline)) unsigned residue_scan(const uint8_t *needle, unsigned nlen)
{
	uint8_t probe[PROBE_BYTES];
	const volatile uint8_t *p = probe;

	__asm__ volatile("" : "+r"(p));       /* hide that the array is uninitialised: the read is the point */
	unsigned i, j, hits = 0;

	for (i = 0; i + nlen <= PROBE_BYTES; i++) {
		for (j = 0; j < nlen && p[i + j] == needle[j]; j++)
			;
		if (j == nlen)
			hits++;
	}
	return hits;
}

static __attribute__((noinline)) int derive_pmk(uint8_t pmk[32])
{
	return swj_pmk_from_passphrase(RES_PASS, (uint32_t)strlen(RES_PASS), (const uint8_t *)"residue-ssid", 12, pmk);
}

static void test_wipe(void)
{
	uint8_t pmk[32];
	unsigned n = (unsigned)strlen(RES_PASS), hits;

	CHECK(derive_pmk(pmk) == 0);
	hits = residue_scan((const uint8_t *)RES_PASS, n > 16 ? 16 : n);        /* any 16-byte run of the passphrase */
	CHECK(hits == 0);
}

int main(void)
{
	test_primitives();
	test_structs();
	test_join_cmds();
	test_join_mac();
	test_handshake();
	test_gap_text();
	test_wipe();
	printf("RESULT: %s wlan_4way_host_test\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
