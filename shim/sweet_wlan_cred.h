/* sweet_wlan_cred.h - the shim side of the WLANCRED credential blob (SPEC-WIFI Part E TRANSPORT block s.3 items 1 and 4; T-f5f7 step 2).
 *
 * PURPOSE: validate the 128-byte RAM-only blob that tools/mkwlancred.py builds (layout there, the host twin `parse`), copy the fields the join needs,
 * derive the PMK (PBKDF2-HMAC-SHA1(psk, ssid, 4096, 32), swj_pmk_from_passphrase), then ZERO the DT-named region and the working copy of the
 * passphrase with a compiler-barrier wipe, and print exactly one line: `SWEET WLAN: credentials loaded security=wpa2-psk` (no length, ssid, ip).
 * Any failure prints `WIFI GAP: creds <missing|invalid>` (no value) and the region is still wiped (it may hold a passphrase); the caller stops before
 * any ioctl. Pure functions on byte buffers: the includer owns the mapping of the region (the glue reads the /chosen/memory-map property WLANCRED).
 * The includer defines SWC_LOG(str) and SWC_GAP(str) and includes sweet_wlan_join.h first (swj_pmk_from_passphrase). SECRETS (rule 4): nothing
 * here prints a byte of the blob, the passphrase, the PMK, the ssid or an address. No hardware access, no allocation.
 *
 * Blob (little endian, 128 bytes) [src tools/mkwlancred.py docstring, read 2026-11-03]:
 *   0x00 magic "WLANCRB1" | 0x08 u32 security (0 = wpa2-psk) | 0x0c u32 ssid_len 1..32 | 0x10 u32 psk_len 8..63 | 0x14 ssid[32] | 0x34 psk[64]
 *   | 0x74 ip[4] | 0x78 gw[4] | 0x7c u32 crc32 (zlib, reflected 0xEDB88320) over 0x00..0x7b; ssid/psk padding must be zero.
 */
#ifndef SWEET_WLAN_CRED_H
#define SWEET_WLAN_CRED_H

#include <stdint.h>
#include <string.h>

#if !defined(SWC_LOG) || !defined(SWC_GAP)
#error "sweet_wlan_cred.h: define SWC_LOG(str) and SWC_GAP(str) before including (rule 8)"
#endif
#ifndef SWEET_WLAN_JOIN_H
#error "sweet_wlan_cred.h: include sweet_wlan_join.h first (swj_pmk_from_passphrase)"
#endif

#define SWC_SIZE      128u
#define SWC_MAGIC     "WLANCRB1"
#define SWC_L_LOADED  "SWEET WLAN: credentials loaded security=wpa2-psk"
#define SWC_GAP_MISSING "WIFI GAP: creds missing"
#define SWC_GAP_INVALID "WIFI GAP: creds invalid"

/* what the join needs after the load: no passphrase (only the PMK) */
struct swc_creds {
	uint8_t ssid[32];
	uint32_t ssid_len;
	uint8_t pmk[32];
	uint8_t ip[4], gw[4];
};

/* zero n bytes in a way the compiler cannot drop (volatile stores plus a memory clobber) */
static inline void swc_wipe(void *p, uint32_t n)
{
	volatile uint8_t *v = (volatile uint8_t *)p;
	uint32_t i;

	for (i = 0; i < n; i++)
		v[i] = 0u;
	__asm__ volatile("" ::: "memory");
}

static inline uint32_t swc_crc32(const uint8_t *p, uint32_t n)
{
	uint32_t c = 0xFFFFFFFFu, i, k;

	for (i = 0; i < n; i++) {
		c ^= p[i];
		for (k = 0; k < 8u; k++)
			c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
	}
	return ~c;
}

static inline uint32_t swc_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* validate `w` (SWC_SIZE bytes): 0 = ok, -1 = invalid. Looks at no byte it has not bounds-checked; never prints. */
static inline int swc_check(const uint8_t w[SWC_SIZE])
{
	uint32_t sl, pl, i;

	if (memcmp(w, SWC_MAGIC, 8) != 0 || swc_crc32(w, SWC_SIZE - 4u) != swc_le32(w + 0x7c) || swc_le32(w + 0x08) != 0u)
		return -1;
	sl = swc_le32(w + 0x0c);
	pl = swc_le32(w + 0x10);
	if (sl < 1u || sl > 32u || pl < 8u || pl > 63u)
		return -1;
	for (i = sl; i < 32u; i++)
		if (w[0x14 + i] != 0u)
			return -1;
	for (i = pl; i < 64u; i++)
		if (w[0x34 + i] != 0u)
			return -1;
	return 0;
}

/* The L24 entry reads the blob ONCE. region = the DT-named region (`avail` bytes, may be NULL / short), work = the caller's SWC_SIZE-byte working copy
 * (the stack copy of the passphrase lives there; wiped before return). On every path the region (all `avail` bytes) and `work` are zero afterwards.
 * Returns 0 and fills *out (prints SWC_L_LOADED), or -1 after printing `WIFI GAP: creds missing|invalid` (*out is zero). */
static inline int swc_load(volatile uint8_t *region, uint32_t avail, uint8_t work[SWC_SIZE], struct swc_creds *out)
{
	uint32_t i, sl, pl;
	int bad;

	memset(out, 0, sizeof *out);
	if (region == NULL || avail < SWC_SIZE) {
		if (region != NULL)
			swc_wipe((void *)region, avail);
		swc_wipe(work, SWC_SIZE);
		SWC_GAP(SWC_GAP_MISSING);
		return -1;
	}
	for (i = 0; i < SWC_SIZE; i++)
		work[i] = region[i];
	swc_wipe((void *)region, avail);
	bad = swc_check(work);
	if (bad == 0) {
		sl = swc_le32(work + 0x0c);
		pl = swc_le32(work + 0x10);
		bad = swj_pmk_from_passphrase((const char *)(work + 0x34), pl, work + 0x14, sl, out->pmk);
		if (bad == 0) {
			memcpy(out->ssid, work + 0x14, sl);
			out->ssid_len = sl;
			memcpy(out->ip, work + 0x74, 4);
			memcpy(out->gw, work + 0x78, 4);
		}
	}
	swc_wipe(work, SWC_SIZE);
	if (bad != 0) {
		swc_wipe(out, (uint32_t)sizeof *out);
		SWC_GAP(SWC_GAP_INVALID);
		return -1;
	}
	SWC_LOG(SWC_L_LOADED);
	return 0;
}

#endif /* SWEET_WLAN_CRED_H */
