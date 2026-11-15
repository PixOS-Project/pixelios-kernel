/* sweet_wlan_join.h - host-side WPA2-PSK join for the BCM4389 (P-W2 / W-D5, SPEC-WIFI Part E rows H1-H12).
 *
 * PURPOSE: the HOST runs the 4-way handshake (stock Android does, X-W6 s.0), so the shim needs: the join command payloads (H1-H5),
 * the EAPOL-Key handshake with its crypto (H6, H7: PBKDF2-SHA1, PRF-SHA1, HMAC-SHA1 MIC, RFC 3394 key unwrap), the key-install
 * payloads (H8-H10) and the success marker (H12). Header-only C99, no malloc, no I/O: the includer owns the transport.
 * Own code from the standards below; the layouts of the firmware structs are READ from the sources named at each struct.
 * The includer defines, before including:
 *     SWJ_LOG(str)         - one log line (const char *, no newline)
 *     SWJ_GAP(str)         - one "WIFI GAP: ..." line (rule 8)
 *     SWJ_RANDOM(buf,len)  - fill buf with len cryptographically strong bytes (SNonce); returns 0 on success, else non-zero.
 *                            No default: a weak SNonce would make PTK reuse possible, so the build fails without it.
 * SECRETS (rule 4): no function here logs, formats or copies the passphrase, PSK, PTK, KCK/KEK/TK, GTK or a MAC address into a
 * log line. The caller derives the PSK from the run tool's config with swj_pmk_from_passphrase() and never prints it.
 *
 * Standards used (read from the standard / the OpenBSD reference, not observed on our hardware):
 *  - IEEE 802.11i-2004 Annex H.4: PSK = PBKDF2(HMAC-SHA1, passphrase, ssid, 4096, 256 bit). PRF: IEEE 802.11-2016 12.7.1.2
 *    (HMAC-SHA1(K, label || 0x00 || data || counter byte)); PTK(CCMP) = PRF-384(PMK, "Pairwise key expansion",
 *    min(AA,SPA) || max(AA,SPA) || min(ANonce,SNonce) || max(ANonce,SNonce)); KCK = PTK[0..16), KEK = [16..32), TK = [32..48).
 *  - EAPOL-Key frame (IEEE 802.11-2016 12.7.2; OpenBSD net80211 ieee80211_pae_output.c msg2/msg4, pae_input.c msg3): offsets from
 *    the EAPOL header: +0 version +1 type(3) +2 body len(2) +4 descriptor(2 = RSN) +5 key info(2) +7 key length(2) +9 replay(8)
 *    +17 nonce(32) +49 IV(16) +65 RSC(8) +73 reserved(8) +81 MIC(16) +97 key data len(2) +99 key data.
 *    Key info bits: version 0-2 (2 = HMAC-SHA1 MIC + AES key wrap), pairwise 3, install 6, ack 7, MIC 8, secure 9, error 10,
 *    request 11, encrypted key data 12.  M2 = v2|pairwise|MIC, key data = the association RSNE, key length 0, replay = M1's.
 *    M4 = v2|pairwise|MIC|secure, empty key data, key length 0, replay = M3's.
 *  - RFC 3394 AES key wrap; FIPS-197 AES-128; FIPS 180-4 SHA-1; RFC 2202 HMAC-SHA1.
 *  - Firmware structs (OpenBSD bwfmreg.h = BW, DHD wlioctl.h.new = S5; BOTH give sizeof(wsec_key) = 164, see SWJ_WSEC_KEY_SIZE).
 *
 * Log lines (no secrets, no MACs): "SWEET WLAN: eapol rx msg=N", "SWEET WLAN: eapol tx msg=N",
 *  SWJ_L_COMPLETE (H12) and the SWJ_GAP_* "WIFI GAP:" constants below, one per unimplemented/partial spec row.
 */
#ifndef SWEET_WLAN_JOIN_H
#define SWEET_WLAN_JOIN_H

#include <stdint.h>
#ifndef KERNEL
#include <stdio.h>      /* snprintf: the XNU build (KERNEL) gets it from <sys/systm.h> via the includer (T-48a0) */
#endif
#include <string.h>

#if !defined(SWJ_LOG) || !defined(SWJ_GAP)
#error "sweet_wlan_join.h: define SWJ_LOG(str) and SWJ_GAP(str) before including (rule 8)"
#endif
#ifndef SWJ_RANDOM
#error "sweet_wlan_join.h: define SWJ_RANDOM(buf,len) (strong SNonce source) before including"
#endif

/* ------------------------------------------------------------------ GAP lines (rule 8): one per partial/missing matrix row */
#define SWJ_GAP_H5  "WIFI GAP: H5 join: payloads built and host-tested, ioctl submit and SET_SSID/LINK event wait are not wired to the msgbuf engine"
#define SWJ_GAP_H6  "WIFI GAP: H6 eapol rx: handler host-tested, the RX-ring hook ahead of ifnet_input (ethertype 0x888e) is not wired"
#define SWJ_GAP_H7  "WIFI GAP: H7 eapol tx: M2/M4 frames built and host-tested, send through the normal TX data ring is not wired (X-W6 U3 open)"
#define SWJ_GAP_H11 "WIFI GAP: H11 WLC_SCB_AUTHORIZE (121) not issued: need unknown until the first host 4-way run (U-c / X-W6 U2)"
#define SWJ_GAP_F1  "WIFI GAP: F1 fw supplicant probe GET sup_wpa: fallback closed until U-a (cap + GET answer on device)"
#define SWJ_GAP_F2  "WIFI GAP: F2 fw supplicant enable sup_wpa=1: fallback closed until U-a"
#define SWJ_GAP_F3  "WIFI GAP: F3 WLC_SET_WSEC_PMK (268): fallback closed until U-a; flags semantics U-f unread"
#define SWJ_GAP_F4  "WIFI GAP: F4 fw supplicant auth/cipher set: fallback closed until U-a"
#define SWJ_GAP_F5  "WIFI GAP: F5 fw supplicant join: fallback closed until U-a"
#define SWJ_GAP_F6  "WIFI GAP: F6 wait WLC_E_PSK_SUP (46) status 6: fallback closed until U-a"
#define SWJ_GAP_F7  "WIFI GAP: F7 teardown SET_WSEC_PMK len 0: fallback closed until U-a"
/* not matrix rows, but behaviour this header does NOT cover; logged by the handler when hit */
#define SWJ_GAP_X_REKEY "WIFI GAP: eapol group rekey / PTK rekey after join (msg 1/2 of the group handshake) not handled"
#define SWJ_GAP_X_V1    "WIFI GAP: eapol key descriptor version != 2 (TKIP / WPA1 / SHA256 AKM) not supported"
#define SWJ_GAP_X_RSNE  "WIFI GAP: M2 RSNE is the caller's copy; it must equal the RSNE the firmware put in the association request (unverified, U-g)"
#define SWJ_L_COMPLETE  "SWEET WLAN: 4way complete PTK=CCMP GTK=CCMP"

/* ------------------------------------------------------------------ constants */
#define SWJ_ETH_HLEN   14u
#define SWJ_ETH_EAPOL  0x888eu
#define SWJ_EAPOL_HLEN 4u
#define SWJ_KEY_FIXED  99u     /* EAPOL header 4 + key descriptor body up to and including the key data length field */
#define SWJ_OFF_DESC   4u
#define SWJ_OFF_INFO   5u
#define SWJ_OFF_KLEN   7u
#define SWJ_OFF_REPLAY 9u
#define SWJ_OFF_NONCE  17u
#define SWJ_OFF_MIC    81u
#define SWJ_OFF_DLEN   97u
#define SWJ_KI_VER_MASK 0x0007u
#define SWJ_KI_PAIRWISE 0x0008u
#define SWJ_KI_INSTALL  0x0040u
#define SWJ_KI_ACK      0x0080u
#define SWJ_KI_MIC      0x0100u
#define SWJ_KI_SECURE   0x0200u
#define SWJ_KI_ERROR    0x0400u
#define SWJ_KI_REQUEST  0x0800u
#define SWJ_KI_ENC      0x1000u
#define SWJ_KI_FLAGS    0x1fc8u /* every flag bit above (not version, not key index bits 4-5) */
#define SWJ_RSNE_MAX    64u
#define SWJ_KD_MAX      256u    /* largest key data we unwrap (RSNE + GTK KDE + pad is ~64) */
#define SWJ_TX_MAX      (SWJ_ETH_HLEN + SWJ_KEY_FIXED + SWJ_RSNE_MAX)

/* firmware ioctl numbers [S5/wlioctl_defs.h.new:706,804,946-947,952] and values [S5/wlioctl_defs.h.new:477,530,545-546,633] */
#define SWJ_WLC_SET_SSID       26u
#define SWJ_WLC_SCB_AUTHORIZE  121u
#define SWJ_WLC_GET_VAR        262u
#define SWJ_WLC_SET_VAR        263u
#define SWJ_WLC_SET_WSEC_PMK   268u
#define SWJ_WPA2_AUTH_PSK      0x0080u
#define SWJ_WSEC_WEP           0x0001u
#define SWJ_WSEC_TKIP          0x0002u
#define SWJ_WSEC_AES           0x0004u
#define SWJ_ALGO_AES_CCM       4u
#define SWJ_WSEC_PRIMARY_KEY   (1u << 1)
#define SWJ_MAX_SSID           32u

/* zero n bytes in a way the compiler cannot drop (volatile stores plus a memory clobber); the stack temporaries of the key paths (passphrase copy, HMAC pads, PBKDF2 blocks) are wiped with it (review T-48a0 r1 F4, T-4459) */
static inline void swj_wipe(void *p, uint32_t n)
{
	volatile uint8_t *v = (volatile uint8_t *)p;
	uint32_t i;

	for (i = 0; i < n; i++)
		v[i] = 0u;
	__asm__ volatile("" ::: "memory");
}

/* ------------------------------------------------------------------ SHA-1 (FIPS 180-4) */
struct swj_sha1 { uint32_t h[5]; uint64_t n; uint8_t buf[64]; uint32_t fill; };

static inline uint32_t swj_rotl32(uint32_t x, unsigned s) { return (x << s) | (x >> (32u - s)); }

static inline void swj_sha1_block(struct swj_sha1 *c, const uint8_t *p)
{
	uint32_t w[80], a, b, d, e, f, k, t, cc;
	unsigned i;

	for (i = 0; i < 16; i++)
		w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) | ((uint32_t)p[4 * i + 2] << 8) | p[4 * i + 3];
	for (i = 16; i < 80; i++)
		w[i] = swj_rotl32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
	a = c->h[0]; b = c->h[1]; cc = c->h[2]; d = c->h[3]; e = c->h[4];
	for (i = 0; i < 80; i++) {
		if (i < 20)      { f = (b & cc) | (~b & d);           k = 0x5a827999u; }
		else if (i < 40) { f = b ^ cc ^ d;                    k = 0x6ed9eba1u; }
		else if (i < 60) { f = (b & cc) | (b & d) | (cc & d); k = 0x8f1bbcdcu; }
		else             { f = b ^ cc ^ d;                    k = 0xca62c1d6u; }
		t = swj_rotl32(a, 5) + f + e + k + w[i];
		e = d; d = cc; cc = swj_rotl32(b, 30); b = a; a = t;
	}
	c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d; c->h[4] += e;
	swj_wipe(w, (uint32_t)sizeof w);        /* the first 16 words are the (HMAC pad) input block */
}

static inline void swj_sha1_init(struct swj_sha1 *c)
{
	c->h[0] = 0x67452301u; c->h[1] = 0xefcdab89u; c->h[2] = 0x98badcfeu; c->h[3] = 0x10325476u; c->h[4] = 0xc3d2e1f0u;
	c->n = 0; c->fill = 0;
}

static inline void swj_sha1_update(struct swj_sha1 *c, const uint8_t *d, uint32_t len)
{
	c->n += len;
	while (len) {
		uint32_t take = 64u - c->fill;

		if (take > len)
			take = len;
		memcpy(c->buf + c->fill, d, take);
		c->fill += take; d += take; len -= take;
		if (c->fill == 64u) {
			swj_sha1_block(c, c->buf);
			c->fill = 0;
		}
	}
}

static inline void swj_sha1_final(struct swj_sha1 *c, uint8_t out[20])
{
	uint64_t bits = c->n * 8u;
	uint8_t pad = 0x80, z = 0, lenb[8];
	unsigned i;

	swj_sha1_update(c, &pad, 1);
	while (c->fill != 56u)
		swj_sha1_update(c, &z, 1);
	for (i = 0; i < 8; i++)
		lenb[i] = (uint8_t)(bits >> (56 - 8 * i));
	swj_sha1_update(c, lenb, 8);
	for (i = 0; i < 20; i++)
		out[i] = (uint8_t)(c->h[i / 4] >> (24 - 8 * (i % 4)));
	swj_wipe(lenb, (uint32_t)sizeof lenb);
}

/* HMAC-SHA1 over up to 4 message parts (RFC 2104 / 2202) */
static inline void swj_hmac_sha1(const uint8_t *key, uint32_t klen, const uint8_t *const parts[], const uint32_t lens[],
    unsigned nparts, uint8_t out[20])
{
	struct swj_sha1 c;
	uint8_t k[64], pad[64], inner[20];
	unsigned i;

	memset(k, 0, sizeof k);
	if (klen > 64u) {
		swj_sha1_init(&c); swj_sha1_update(&c, key, klen); swj_sha1_final(&c, k);
	} else if (klen) {
		memcpy(k, key, klen);
	}
	for (i = 0; i < 64; i++)
		pad[i] = k[i] ^ 0x36;
	swj_sha1_init(&c); swj_sha1_update(&c, pad, 64);
	for (i = 0; i < nparts; i++)
		swj_sha1_update(&c, parts[i], lens[i]);
	swj_sha1_final(&c, inner);
	for (i = 0; i < 64; i++)
		pad[i] = k[i] ^ 0x5c;
	swj_sha1_init(&c); swj_sha1_update(&c, pad, 64); swj_sha1_update(&c, inner, 20); swj_sha1_final(&c, out);
	swj_wipe(k, (uint32_t)sizeof k);        /* the passphrase (or its hash), the pads, the inner digest and the hash state */
	swj_wipe(pad, (uint32_t)sizeof pad);
	swj_wipe(inner, (uint32_t)sizeof inner);
	swj_wipe(&c, (uint32_t)sizeof c);
}

/* PBKDF2-HMAC-SHA1 (RFC 8018 s.5.2; RFC 6070 vectors). salt <= 36 bytes (ssid 32 + 4 block-index bytes). Returns 0 or -1. */
static inline int swj_pbkdf2_sha1(const uint8_t *pass, uint32_t plen, const uint8_t *salt, uint32_t slen, uint32_t iter,
    uint8_t *out, uint32_t outlen)
{
	uint8_t s[40], u[20], t[20];
	uint32_t blk, i, j, take;

	if (slen > 36u || iter == 0)
		return -1;
	memcpy(s, salt, slen);
	for (blk = 1; outlen; blk++) {
		const uint8_t *parts[1]; uint32_t lens[1];

		s[slen] = (uint8_t)(blk >> 24); s[slen + 1] = (uint8_t)(blk >> 16);
		s[slen + 2] = (uint8_t)(blk >> 8); s[slen + 3] = (uint8_t)blk;
		parts[0] = s; lens[0] = slen + 4u;
		swj_hmac_sha1(pass, plen, parts, lens, 1, u);
		memcpy(t, u, 20);
		for (i = 1; i < iter; i++) {
			parts[0] = u; lens[0] = 20u;
			swj_hmac_sha1(pass, plen, parts, lens, 1, u);
			for (j = 0; j < 20; j++)
				t[j] ^= u[j];
		}
		take = outlen < 20u ? outlen : 20u;
		memcpy(out, t, take);
		out += take; outlen -= take;
	}
	swj_wipe(s, (uint32_t)sizeof s);        /* the salt, the last U and the accumulated T (PMK bytes) */
	swj_wipe(u, (uint32_t)sizeof u);
	swj_wipe(t, (uint32_t)sizeof t);
	return 0;
}

/* PSK from passphrase (802.11i H.4): passphrase 8..63 bytes, ssid 1..32 bytes. Returns 0, or -1 with a GAP (never prints either). */
static inline int swj_pmk_from_passphrase(const char *pass, uint32_t plen, const uint8_t *ssid, uint32_t slen, uint8_t pmk[32])
{
	if (plen < 8u || plen > 63u || slen < 1u || slen > SWJ_MAX_SSID) {
		SWJ_GAP("WIFI GAP: psk derive refused: passphrase length not 8..63 or ssid length not 1..32");
		return -1;
	}
	return swj_pbkdf2_sha1((const uint8_t *)pass, plen, ssid, slen, 4096u, pmk, 32u);
}

/* PRF-SHA1 (802.11-2016 12.7.1.2): out_len bytes. label is NUL-terminated text, the NUL is part of the hashed input. */
static inline void swj_prf_sha1(const uint8_t *key, uint32_t klen, const char *label, const uint8_t *data, uint32_t dlen,
    uint8_t *out, uint32_t out_len)
{
	uint8_t ctr, h[20];
	uint32_t pos = 0, take;

	for (ctr = 0; pos < out_len; ctr++) {
		const uint8_t *parts[3]; uint32_t lens[3];
		uint8_t c1[1];

		c1[0] = ctr;
		parts[0] = (const uint8_t *)label; lens[0] = (uint32_t)strlen(label) + 1u;
		parts[1] = data; lens[1] = dlen;
		parts[2] = c1; lens[2] = 1;
		swj_hmac_sha1(key, klen, parts, lens, 3, h);
		take = out_len - pos < 20u ? out_len - pos : 20u;
		memcpy(out + pos, h, take);
		pos += take;
	}
}

static inline void swj_ptk_derive(const uint8_t pmk[32], const uint8_t aa[6], const uint8_t spa[6], const uint8_t anonce[32],
    const uint8_t snonce[32], uint8_t ptk[48])
{
	uint8_t d[76];
	int a_lt = memcmp(aa, spa, 6) < 0, n_lt = memcmp(anonce, snonce, 32) < 0;

	memcpy(d, a_lt ? aa : spa, 6); memcpy(d + 6, a_lt ? spa : aa, 6);
	memcpy(d + 12, n_lt ? anonce : snonce, 32); memcpy(d + 44, n_lt ? snonce : anonce, 32);
	swj_prf_sha1(pmk, 32, "Pairwise key expansion", d, sizeof d, ptk, 48);
}

/* ------------------------------------------------------------------ AES-128 + RFC 3394 (FIPS-197; tables computed, not typed) */
struct swj_aes { uint8_t sb[256], isb[256], rk[176]; };

static inline uint8_t swj_xt(uint8_t x) { return (uint8_t)((x << 1) ^ ((x & 0x80) ? 0x1b : 0)); }
static inline uint8_t swj_gm(uint8_t a, uint8_t b)
{
	uint8_t p = 0;

	while (b) {
		if (b & 1)
			p ^= a;
		a = swj_xt(a); b >>= 1;
	}
	return p;
}
static inline uint8_t swj_rotl8(uint8_t x, unsigned s) { return (uint8_t)((x << s) | (x >> (8u - s))); }

static inline void swj_aes_init(struct swj_aes *a, const uint8_t key[16])
{
	uint8_t p = 1, q = 1, rc = 1;
	unsigned i;

	do {	/* the standard GF(2^8) generator walk: p *= 3, q /= 3, then the affine map */
		p = (uint8_t)(p ^ (p << 1) ^ ((p & 0x80) ? 0x1b : 0));
		q ^= (uint8_t)(q << 1); q ^= (uint8_t)(q << 2); q ^= (uint8_t)(q << 4);
		if (q & 0x80)
			q ^= 0x09;
		a->sb[p] = (uint8_t)(q ^ swj_rotl8(q, 1) ^ swj_rotl8(q, 2) ^ swj_rotl8(q, 3) ^ swj_rotl8(q, 4) ^ 0x63);
	} while (p != 1);
	a->sb[0] = 0x63;
	for (i = 0; i < 256; i++)
		a->isb[a->sb[i]] = (uint8_t)i;
	memcpy(a->rk, key, 16);
	for (i = 16; i < 176; i += 4) {
		uint8_t t[4];

		memcpy(t, a->rk + i - 4, 4);
		if (i % 16 == 0) {
			uint8_t t0 = t[0];

			t[0] = (uint8_t)(a->sb[t[1]] ^ rc); t[1] = a->sb[t[2]]; t[2] = a->sb[t[3]]; t[3] = a->sb[t0];
			rc = swj_xt(rc);
		}
		a->rk[i] = a->rk[i - 16] ^ t[0]; a->rk[i + 1] = a->rk[i - 15] ^ t[1];
		a->rk[i + 2] = a->rk[i - 14] ^ t[2]; a->rk[i + 3] = a->rk[i - 13] ^ t[3];
	}
}

static inline void swj_aes_addrk(uint8_t s[16], const uint8_t *rk)
{
	unsigned i;

	for (i = 0; i < 16; i++)
		s[i] ^= rk[i];
}

static inline void swj_aes_enc(const struct swj_aes *a, const uint8_t in[16], uint8_t out[16])
{
	uint8_t s[16], t[16];
	unsigned r, i, c;

	memcpy(s, in, 16);
	swj_aes_addrk(s, a->rk);
	for (r = 1; r <= 10; r++) {
		for (i = 0; i < 16; i++)
			s[i] = a->sb[s[i]];
		for (c = 0; c < 4; c++)
			for (i = 0; i < 4; i++)
				t[i + 4 * c] = s[i + 4 * ((c + i) % 4)];
		if (r != 10) {
			for (c = 0; c < 4; c++) {
				const uint8_t *x = t + 4 * c;

				s[4 * c]     = (uint8_t)(swj_xt(x[0]) ^ (swj_xt(x[1]) ^ x[1]) ^ x[2] ^ x[3]);
				s[4 * c + 1] = (uint8_t)(x[0] ^ swj_xt(x[1]) ^ (swj_xt(x[2]) ^ x[2]) ^ x[3]);
				s[4 * c + 2] = (uint8_t)(x[0] ^ x[1] ^ swj_xt(x[2]) ^ (swj_xt(x[3]) ^ x[3]));
				s[4 * c + 3] = (uint8_t)((swj_xt(x[0]) ^ x[0]) ^ x[1] ^ x[2] ^ swj_xt(x[3]));
			}
		} else {
			memcpy(s, t, 16);
		}
		swj_aes_addrk(s, a->rk + 16 * r);
	}
	memcpy(out, s, 16);
}

static inline void swj_aes_dec(const struct swj_aes *a, const uint8_t in[16], uint8_t out[16])
{
	uint8_t s[16], t[16];
	int r;
	unsigned i, c;

	memcpy(s, in, 16);
	swj_aes_addrk(s, a->rk + 160);
	for (r = 9; r >= 0; r--) {
		for (c = 0; c < 4; c++)
			for (i = 0; i < 4; i++)
				t[i + 4 * ((c + i) % 4)] = s[i + 4 * c];
		for (i = 0; i < 16; i++)
			s[i] = a->isb[t[i]];
		swj_aes_addrk(s, a->rk + 16 * r);
		if (r != 0) {
			for (c = 0; c < 4; c++) {
				const uint8_t *x = s + 4 * c;

				t[4 * c]     = (uint8_t)(swj_gm(x[0], 14) ^ swj_gm(x[1], 11) ^ swj_gm(x[2], 13) ^ swj_gm(x[3], 9));
				t[4 * c + 1] = (uint8_t)(swj_gm(x[0], 9) ^ swj_gm(x[1], 14) ^ swj_gm(x[2], 11) ^ swj_gm(x[3], 13));
				t[4 * c + 2] = (uint8_t)(swj_gm(x[0], 13) ^ swj_gm(x[1], 9) ^ swj_gm(x[2], 14) ^ swj_gm(x[3], 11));
				t[4 * c + 3] = (uint8_t)(swj_gm(x[0], 11) ^ swj_gm(x[1], 13) ^ swj_gm(x[2], 9) ^ swj_gm(x[3], 14));
			}
			memcpy(s, t, 16);
		}
	}
	memcpy(out, s, 16);
}

/* RFC 3394 s.2.2.1 wrap: plain n*8 bytes (n >= 2) -> (n+1)*8 bytes. Returns 0 or -1. */
static inline int swj_aes_wrap(const uint8_t kek[16], const uint8_t *plain, uint32_t plen, uint8_t *out)
{
	struct swj_aes a;
	uint8_t b[16];
	uint32_t n = plen / 8u, i, j, t;
	unsigned k;

	if (plen % 8u || n < 2u)
		return -1;
	swj_aes_init(&a, kek);
	memset(out, 0xa6, 8);
	memcpy(out + 8, plain, plen);
	for (j = 0; j < 6; j++)
		for (i = 1; i <= n; i++) {
			memcpy(b, out, 8); memcpy(b + 8, out + 8 * i, 8);
			swj_aes_enc(&a, b, b);
			t = n * j + i;
			memcpy(out, b, 8);
			for (k = 0; k < 4; k++)
				out[7 - k] ^= (uint8_t)(t >> (8 * k));
			memcpy(out + 8 * i, b + 8, 8);
		}
	return 0;
}

/* RFC 3394 s.2.2.2 unwrap: (n+1)*8 bytes -> n*8 bytes in `out`. Returns 0, or -1 when the integrity register is not A6A6A6A6A6A6A6A6. */
static inline int swj_aes_unwrap(const uint8_t kek[16], const uint8_t *cipher, uint32_t clen, uint8_t *out)
{
	struct swj_aes a;
	uint8_t A[8], b[16];
	uint32_t n, i, t;
	int j;
	unsigned k;

	if (clen % 8u || clen < 24u)
		return -1;
	n = clen / 8u - 1u;
	swj_aes_init(&a, kek);
	memcpy(A, cipher, 8);
	memcpy(out, cipher + 8, 8u * n);
	for (j = 5; j >= 0; j--)
		for (i = n; i >= 1; i--) {
			t = n * (uint32_t)j + i;
			memcpy(b, A, 8);
			for (k = 0; k < 4; k++)
				b[7 - k] ^= (uint8_t)(t >> (8 * k));
			memcpy(b + 8, out + 8 * (i - 1), 8);
			swj_aes_dec(&a, b, b);
			memcpy(A, b, 8);
			memcpy(out + 8 * (i - 1), b + 8, 8);
		}
	for (k = 0; k < 8; k++)
		if (A[k] != 0xa6)
			return -1;
	return 0;
}

/* ------------------------------------------------------------------ EAPOL-Key MIC (version 2: HMAC-SHA1, first 16 bytes) */
static inline void swj_eapol_mic(const uint8_t kck[16], const uint8_t *pdu, uint32_t len, uint8_t mic[16])
{
	static const uint8_t zero16[16];
	const uint8_t *parts[3];
	uint32_t lens[3];
	uint8_t h[20];

	parts[0] = pdu; lens[0] = SWJ_OFF_MIC;
	parts[1] = zero16; lens[1] = 16;
	parts[2] = pdu + SWJ_OFF_MIC + 16; lens[2] = len - SWJ_OFF_MIC - 16;
	swj_hmac_sha1(kck, 16, parts, lens, 3, h);
	memcpy(mic, h, 16);
}

static inline uint32_t swj_be16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | p[1]; }
static inline uint64_t swj_be64(const uint8_t *p)
{
	uint64_t v = 0;
	unsigned i;

	for (i = 0; i < 8; i++)
		v = (v << 8) | p[i];
	return v;
}
static inline void swj_put_be16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static inline void swj_put_be64(uint8_t *p, uint64_t v)
{
	unsigned i;

	for (i = 0; i < 8; i++)
		p[i] = (uint8_t)(v >> (56 - 8 * i));
}
static inline void swj_put_le32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

/* Build an EAPOL-Key PDU (no Ethernet header) into pdu; returns its length. mic is filled from kck when info has the MIC bit. */
static inline uint32_t swj_eapol_build(uint8_t *pdu, uint32_t info, uint64_t replay, const uint8_t nonce[32],
    const uint8_t *kd, uint32_t kdlen, const uint8_t kck[16])
{
	uint32_t len = SWJ_KEY_FIXED + kdlen;

	memset(pdu, 0, SWJ_KEY_FIXED);
	pdu[0] = 1; pdu[1] = 3;		/* EAPOL version 1 (OpenBSD EAPOL_VERSION, ieee80211.h:1124), type EAPOL-Key */
	swj_put_be16(pdu + 2, len - SWJ_EAPOL_HLEN);
	pdu[SWJ_OFF_DESC] = 2;		/* RSN key descriptor */
	swj_put_be16(pdu + SWJ_OFF_INFO, info);
	swj_put_be64(pdu + SWJ_OFF_REPLAY, replay);
	if (nonce)
		memcpy(pdu + SWJ_OFF_NONCE, nonce, 32);
	swj_put_be16(pdu + SWJ_OFF_DLEN, kdlen);
	if (kdlen)
		memcpy(pdu + SWJ_KEY_FIXED, kd, kdlen);
	if (info & SWJ_KI_MIC)
		swj_eapol_mic(kck, pdu, len, pdu + SWJ_OFF_MIC);
	return len;
}

/* ------------------------------------------------------------------ the supplicant (H6, H7) */
enum swj_state { SWJ_IDLE = 0, SWJ_M2_SENT, SWJ_PTK_DONE };
enum swj_rx_result { SWJ_RX_PASS = 0, SWJ_RX_CONSUMED, SWJ_RX_REPLY };

struct swj_ctx {
	uint8_t pmk[32], spa[6], aa[6];
	uint8_t rsne_assoc[SWJ_RSNE_MAX]; uint32_t rsne_assoc_len;	/* RSNE to put in M2 (the one in the association request) */
	uint8_t rsne_beacon[SWJ_RSNE_MAX]; uint32_t rsne_beacon_len;	/* RSNE of the beacon/probe response; M3 must repeat it */
	uint8_t snonce[32], anonce[32], tptk[48];
	uint64_t replay; int replay_ok;
	enum swj_state state;
	int new_ptk_expected;			/* KRACK guard: install a PTK only once per accepted M1 */
	int install_ptk, install_gtk;		/* set by swj_rx; the caller issues the wsec_key writes, then clears them */
	int complete_logged;
	uint8_t gtk[16]; uint8_t gtk_id;
	uint32_t n_rx, n_tx, n_bad;
	uint8_t last_m4[SWJ_TX_MAX]; uint32_t last_m4_len;
};

/* assoc_read: 1 = rsne_assoc is the RSNE the firmware put in the association request, read back from it (T-9d71): the U-g gap is then not printed (it would be false); 0 = the caller's copy, the gap is printed */
static inline int swj_ctx_init_ex(struct swj_ctx *c, const uint8_t pmk[32], const uint8_t spa[6], const uint8_t aa[6],
    const uint8_t *rsne_assoc, uint32_t rsne_assoc_len, const uint8_t *rsne_beacon, uint32_t rsne_beacon_len, int assoc_read)
{
	memset(c, 0, sizeof *c);
	if (rsne_assoc_len == 0 || rsne_assoc_len > SWJ_RSNE_MAX || rsne_beacon_len == 0 || rsne_beacon_len > SWJ_RSNE_MAX) {
		SWJ_GAP("WIFI GAP: eapol ctx refused: RSNE missing or longer than 64 bytes");
		return -1;
	}
	memcpy(c->pmk, pmk, 32); memcpy(c->spa, spa, 6); memcpy(c->aa, aa, 6);
	memcpy(c->rsne_assoc, rsne_assoc, rsne_assoc_len); c->rsne_assoc_len = rsne_assoc_len;
	memcpy(c->rsne_beacon, rsne_beacon, rsne_beacon_len); c->rsne_beacon_len = rsne_beacon_len;
	if (!assoc_read)
		SWJ_GAP(SWJ_GAP_X_RSNE);
	return 0;
}

static inline int swj_ctx_init(struct swj_ctx *c, const uint8_t pmk[32], const uint8_t spa[6], const uint8_t aa[6],
    const uint8_t *rsne_assoc, uint32_t rsne_assoc_len, const uint8_t *rsne_beacon, uint32_t rsne_beacon_len)
{
	return swj_ctx_init_ex(c, pmk, spa, aa, rsne_assoc, rsne_assoc_len, rsne_beacon, rsne_beacon_len, 0);
}

static inline uint32_t swj_eth_wrap(struct swj_ctx *c, uint8_t *out, const uint8_t *pdu, uint32_t plen)
{
	memcpy(out, c->aa, 6); memcpy(out + 6, c->spa, 6);
	out[12] = (uint8_t)(SWJ_ETH_EAPOL >> 8); out[13] = (uint8_t)SWJ_ETH_EAPOL;
	memcpy(out + SWJ_ETH_HLEN, pdu, plen);
	return SWJ_ETH_HLEN + plen;
}

static inline int swj_drop(struct swj_ctx *c, const char *why)
{
	char b[96];

	c->n_bad++;
	snprintf(b, sizeof b, "WIFI GAP: eapol rx dropped: %s", why);
	SWJ_GAP(b);
	return SWJ_RX_CONSUMED;
}

/* Find GTK KDE (dd len 00-0f-ac 01 flags rsvd key) and the first RSNE in unwrapped key data. Returns 0 when both are well formed. */
static inline int swj_parse_kd(const uint8_t *kd, uint32_t len, const uint8_t **rsne, const uint8_t **gtk, uint32_t *gtk_len,
    uint8_t *gtk_id)
{
	uint32_t p = 0;

	*rsne = NULL; *gtk = NULL; *gtk_len = 0; *gtk_id = 0;
	while (p + 2 <= len) {
		uint32_t l = kd[p + 1];

		if (kd[p] == 0xdd && l == 0)
			break;			/* padding after the elements (RFC 3394 pad: dd 00 ...) */
		if (p + 2 + l > len)
			return -1;
		if (kd[p] == 0x30 && *rsne == NULL) {
			*rsne = kd + p;
		} else if (kd[p] == 0xdd && l >= 6 && kd[p + 2] == 0x00 && kd[p + 3] == 0x0f && kd[p + 4] == 0xac && kd[p + 5] == 0x01) {
			*gtk = kd + p + 8; *gtk_len = l - 6u; *gtk_id = kd[p + 6] & 3u;
		}
		p += 2 + l;
	}
	return *rsne ? 0 : -1;
}

/* review 0282 F5: the stack leftovers of swj_rx (the unwrapped key data with the GTK, the MIC, the built PDU) are zeroed when the function leaves, on every path (cleanup attribute; volatile stores + a compiler barrier).
 * A host test may define SWJ_WIPE_HOOK(p, n) to see each wipe after it was made. */
static inline void swj_wipe_mem(void *p, uint32_t n)
{
	volatile uint8_t *v = (volatile uint8_t *)p;
	uint32_t i;

	for (i = 0; i < n; i++)
		v[i] = 0u;
	__asm__ volatile("" ::: "memory");
#ifdef SWJ_WIPE_HOOK
	SWJ_WIPE_HOOK(p, n);
#endif
}
static inline void swj_wipe_kd(uint8_t (*p)[SWJ_KD_MAX]) { swj_wipe_mem(*p, SWJ_KD_MAX); }
static inline void swj_wipe_pdu(uint8_t (*p)[SWJ_TX_MAX]) { swj_wipe_mem(*p, SWJ_TX_MAX); }
static inline void swj_wipe_mic(uint8_t (*p)[16]) { swj_wipe_mem(*p, 16u); }

/* RX hook (H6): called for EVERY received Ethernet frame before ifnet_input. SWJ_RX_PASS = not ours (hand to ifnet);
 * CONSUMED = EAPOL handled/dropped (do not pass up); REPLY = send out[0..*outlen) through the TX data ring (H7). */
static inline int swj_rx(struct swj_ctx *c, const uint8_t *eth, uint32_t len, uint8_t *out, uint32_t outcap, uint32_t *outlen)
{
	const uint8_t *k;
	uint8_t pdu[SWJ_TX_MAX] __attribute__((cleanup(swj_wipe_pdu))), mic[16] __attribute__((cleanup(swj_wipe_mic)));
	uint32_t info, klen, dlen, plen;
	uint64_t rc;
	int msg;
	char b[48];

	*outlen = 0;
	if (len < SWJ_ETH_HLEN || ((uint32_t)eth[12] << 8 | eth[13]) != SWJ_ETH_EAPOL)
		return SWJ_RX_PASS;
	c->n_rx++;
	if (memcmp(eth + 6, c->aa, 6) != 0)
		return swj_drop(c, "source is not the AP");
	k = eth + SWJ_ETH_HLEN; klen = len - SWJ_ETH_HLEN;
	if (klen < SWJ_KEY_FIXED || k[1] != 3 || SWJ_EAPOL_HLEN + swj_be16(k + 2) > klen)
		return swj_drop(c, "not a complete EAPOL-Key");
	klen = SWJ_EAPOL_HLEN + swj_be16(k + 2);	/* trim Ethernet padding */
	dlen = swj_be16(k + SWJ_OFF_DLEN);
	if (k[SWJ_OFF_DESC] != 2 || SWJ_KEY_FIXED + dlen != klen)
		return swj_drop(c, "descriptor not RSN or key data length inconsistent");
	info = swj_be16(k + SWJ_OFF_INFO);
	if ((info & SWJ_KI_VER_MASK) != 2) {
		SWJ_GAP(SWJ_GAP_X_V1);
		c->n_bad++;
		return SWJ_RX_CONSUMED;
	}
	rc = swj_be64(k + SWJ_OFF_REPLAY);

	if ((info & SWJ_KI_FLAGS) == (SWJ_KI_PAIRWISE | SWJ_KI_ACK)) {			/* M1 */
		uint8_t m2kd[SWJ_RSNE_MAX];

		msg = 1;
		snprintf(b, sizeof b, "SWEET WLAN: eapol rx msg=%d", msg); SWJ_LOG(b);
		if (c->replay_ok && c->state == SWJ_PTK_DONE) { /* a new M1 after completion starts a PTK rekey: allowed, logged */
			SWJ_GAP(SWJ_GAP_X_REKEY);
		}
		if (SWJ_RANDOM(c->snonce, 32) != 0) {
			SWJ_GAP("WIFI GAP: eapol M1 ignored: SWJ_RANDOM failed, no SNonce, no M2 sent");
			c->n_bad++;
			return SWJ_RX_CONSUMED;
		}
		memcpy(c->anonce, k + SWJ_OFF_NONCE, 32);
		swj_ptk_derive(c->pmk, c->aa, c->spa, c->anonce, c->snonce, c->tptk);
		memcpy(m2kd, c->rsne_assoc, c->rsne_assoc_len);
		plen = swj_eapol_build(pdu, 0x0002u | SWJ_KI_PAIRWISE | SWJ_KI_MIC, rc, c->snonce, m2kd, c->rsne_assoc_len, c->tptk);
		if (SWJ_ETH_HLEN + plen > outcap) {
			SWJ_GAP("WIFI GAP: eapol M2 not sent: output buffer too small");
			return SWJ_RX_CONSUMED;
		}
		c->replay = rc; c->replay_ok = 1; c->state = SWJ_M2_SENT; c->new_ptk_expected = 1;
		*outlen = swj_eth_wrap(c, out, pdu, plen);
		c->n_tx++;
		SWJ_LOG("SWEET WLAN: eapol tx msg=2");
		return SWJ_RX_REPLY;
	}

	if ((info & SWJ_KI_FLAGS) == (SWJ_KI_PAIRWISE | SWJ_KI_INSTALL | SWJ_KI_ACK | SWJ_KI_MIC | SWJ_KI_SECURE | SWJ_KI_ENC)) {
		uint8_t kd[SWJ_KD_MAX] __attribute__((cleanup(swj_wipe_kd))), mk[16] __attribute__((cleanup(swj_wipe_mic)));
		const uint8_t *rsne, *gtk;
		uint32_t gtk_len, kdlen;
		uint8_t gtk_id;

		msg = 3;
		snprintf(b, sizeof b, "SWEET WLAN: eapol rx msg=%d", msg); SWJ_LOG(b);
		if (c->state == SWJ_IDLE)
			return swj_drop(c, "M3 without M1");
		if (rc <= c->replay)
			return swj_drop(c, "M3 replay counter not above the last accepted");
		if (memcmp(k + SWJ_OFF_NONCE, c->anonce, 32) != 0)
			return swj_drop(c, "M3 ANonce differs from M1");
		swj_eapol_mic(c->tptk, k, klen, mic);
		memcpy(mk, k + SWJ_OFF_MIC, 16);
		{	/* constant-time compare */
			uint8_t d = 0; unsigned i;
			for (i = 0; i < 16; i++)
				d |= (uint8_t)(mic[i] ^ mk[i]);
			if (d)
				return swj_drop(c, "M3 MIC invalid");
		}
		if (dlen > SWJ_KD_MAX + 8u || dlen < 24u || (dlen % 8u) != 0 ||
		    swj_aes_unwrap(c->tptk + 16, k + SWJ_KEY_FIXED, dlen, kd) != 0)
			return swj_drop(c, "M3 key data unwrap failed");
		kdlen = dlen - 8u;
		if (swj_parse_kd(kd, kdlen, &rsne, &gtk, &gtk_len, &gtk_id) != 0)
			return swj_drop(c, "M3 key data malformed or RSNE missing");
		if ((uint32_t)rsne[1] + 2u != c->rsne_beacon_len || memcmp(rsne, c->rsne_beacon, c->rsne_beacon_len) != 0)
			return swj_drop(c, "M3 RSNE differs from the beacon RSNE");
		if (gtk == NULL || gtk_len != 16u)
			return swj_drop(c, "M3 GTK KDE missing or not 16 bytes (CCMP only)");
		plen = swj_eapol_build(pdu, 0x0002u | SWJ_KI_PAIRWISE | SWJ_KI_MIC | SWJ_KI_SECURE, rc, NULL, NULL, 0, c->tptk);
		if (SWJ_ETH_HLEN + plen > outcap) {
			SWJ_GAP("WIFI GAP: eapol M4 not sent: output buffer too small");
			return SWJ_RX_CONSUMED;
		}
		c->replay = rc;
		*outlen = swj_eth_wrap(c, out, pdu, plen);
		c->n_tx++;
		SWJ_LOG("SWEET WLAN: eapol tx msg=4");
		if (c->new_ptk_expected) {	/* first accepted M3 of this M1: install exactly once (KRACK guard) */
			memcpy(c->gtk, gtk, 16); c->gtk_id = gtk_id;
			c->install_ptk = 1; c->install_gtk = 1; c->new_ptk_expected = 0;
			c->state = SWJ_PTK_DONE;
			memcpy(c->last_m4, out, *outlen); c->last_m4_len = *outlen;
			if (!c->complete_logged) {
				SWJ_LOG(SWJ_L_COMPLETE);
				c->complete_logged = 1;
			}
		}
		return SWJ_RX_REPLY;
	}

	if (info & SWJ_KI_ACK) {	/* group-key handshake msg 1 (pairwise bit clear) or anything else with ack */
		SWJ_GAP(SWJ_GAP_X_REKEY);
		c->n_bad++;
		return SWJ_RX_CONSUMED;
	}
	return swj_drop(c, "unexpected EAPOL-Key flags");
}

/* firmware payloads (H1-H5, H8-H10): sweet_wlan_join_fw.h, split out for R29 (T-4115) */
#include "sweet_wlan_join_fw.h"

#endif /* SWEET_WLAN_JOIN_H */
