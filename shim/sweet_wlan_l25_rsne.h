/*
 * L23 -> L25 hand-off of the target AP's RSNE (T-37d8 step 1 = steps 2-4 of T-baed; SPEC-WIFI Part E TRANSPORT s.1 item 1, s.2 L25, U-g).
 *
 * The L23 escan hands every parsed bss record to the hook x->bssn (sweet_wlan_g2.h swg_e_event). swl25r_note, installed there by the glue (or the host test) with
 * x->bssc = &struct swl25_tgt, looks at the record whose BSSID equals the join target and copies the FIRST RSN element (id 48) out of the record's information elements
 * into tgt->rsne (the whole element: id, length, body; at most SWL25R_RSNE_MAX = SWJ_RSNE_MAX bytes). The engine (swl25_go) reads it from there through the cfg the glue builds (T-37d8 step 3).
 * Everything it reads is a read of the event buffer (host memory the dongle wrote, G1 block, through swg_e_rdb); it stores nothing on any bus.
 *
 * Record layout [src refs/wlan-stage5/wlioctl.h.new:355-397 wl_bss_info_v109_t, offsets computed by a host compile in wlan_rsne_host_test.c with MCSSET_LEN = 16]:
 *   +4 length (u32: bytes of the record incl. IEs) | +116 ie_offset (u16) | +120 ie_length (u32) | the IEs start ie_offset bytes into the record.
 * MCSSET_LEN is NOT defined in the ref set [guess: 16, the Broadcom value]. A wrong value moves ie_offset/ie_length, so they are never trusted alone: the record must satisfy
 *   ie_offset >= 124, ie_offset + ROUNDUP4(ie_length) == length, length <= bytes the event carries
 * [src wl_cfg80211.c.new:14374-14400 wl_validate_bss_length: length = sizeof(struct) + ROUNDUP(ie_length, 4)], and the IE chain must tile ie_length bytes up to the RSNE; any failure is a
 * `WIFI GAP: RSNE ...` line (rule 8), tgt->state = SWL25R_BAD, no RSNE. The first run's log line `L23 rsne record` (length, ie_offset, ie_length) settles the guess [guess until observed].
 * Open: the scan sees the target only if the escan delivers it (a PARTIAL event carries bss_count records, only the first is parsed, g2.h GAP "bss_count above 1"); a target never seen is a GAP in swl25r_status.
 *
 * Include order in the includer: sweet_wlan_data.h (SWDP_LOG/SWDP_GAP), sweet_wlan_join.h, sweet_wlan_g2.h, then this file.
 */
#ifndef SWEET_WLAN_L25_RSNE_H
#define SWEET_WLAN_L25_RSNE_H

#ifndef SWEET_WLAN_G2_H
#error "sweet_wlan_l25_rsne.h needs sweet_wlan_g2.h first"
#endif

#define SWL25R_OFF_LEN   4u             /* wl_bss_info_v109 length */
#define SWL25R_OFF_IEOFF 116u           /* ie_offset (u16) */
#define SWL25R_OFF_IELEN 120u           /* ie_length (u32) */
#define SWL25R_HEAD      124u           /* bytes up to and including ie_length: the least a record can have */
#define SWL25R_IE_RSN    48u            /* WLAN_EID_RSN */
#define SWL25R_RSNE_MAX  64u            /* = struct swl25_cfg.rsne_*[64] = SWJ_RSNE_MAX */
#define SWL25R_MAXIE     128u           /* IEs walked per record at most */
#define SWL25R_IE_KEEP   1024u          /* 2026-11-11 (Get 103 of the kept link): bytes of the target record's IE block kept whole (the iOS record's IE capacity); the radio cuts it to its own cap at an IE boundary and tells every loss */

enum { SWL25R_NONE = 0, SWL25R_OK, SWL25R_BAD };

struct swl25_tgt {
	uint8_t bssid[6];               /* the join target (from the join payload); all zero = wildcard: nothing is looked for, swl25r_status prints the GAP */
	uint8_t state;                  /* SWL25R_NONE = target record not seen yet, OK = rsne holds the element, BAD = a record was seen but unusable (GAP printed) */
	uint8_t seen;                   /* records of the target looked at */
	uint8_t ssid_len, other;        /* T-fc10 H2: wildcard join: the SSID (<= 32 B) the first scan record of which names the target (its BSSID is then filled into `bssid`), and how many records of the same SSID but another BSSID were skipped; ssid_len 0 = BSSID given by the caller */
	uint8_t ssid[32];
	uint8_t rsne[SWL25R_RSNE_MAX];
	uint32_t rsne_len;
	uint8_t via_bss_info;           /* patch 0288: the record came from WLC_GET_BSS_INFO (the scan never saw the AP), for the re-join's log line */
	/* 2026-11-11 (W-D3-weak-security: Settings snapshots the Get 103 record of the joined network ONCE, so it must carry the real IEs from the first answer): what the target's record carried besides the RSNE, kept for the Apple80211 radio.
	 * ie = the record's IE block as the firmware sent it (at most SWL25R_IE_KEEP bytes; ie_short = it was longer), rates = the 500 kbps rateset entries, the rest the plain fields; xf = the extras were read (state OK). */
	uint8_t xf, ie_short, nrates;
	uint8_t rates[16];
	uint16_t beacon, chspec;
	int16_t noise, snr;
	uint32_t ie_len;
	uint8_t ie[SWL25R_IE_KEEP];
};

static int
swl25r_zero(const uint8_t *p, unsigned n)
{
	unsigned i;
	uint8_t o = 0;

	for (i = 0; i < n; i++) {
		o |= p[i];
	}
	return o == 0u;
}

static int
swl25r_bad(struct swl25_tgt *t, const char *why)
{
	char gb[64];

	t->state = SWL25R_BAD;
	snprintf(gb, sizeof gb, "RSNE not read: %.44s", why);
	swl_gap("rsne", gb, "high");
	printf("WIFI GAP: %s\n", gb);
	return 0;
}

/* pure: the record head (the 8 bytes at +116..+123 as `h`, the u32 `length` as len) against the bytes the event carries. 0 = ok and ieo/iel set; else the reason */
static const char *
swl25r_head(uint32_t len, const uint8_t h[8], uint32_t avail, uint32_t *ieo, uint32_t *iel)
{
	*ieo = swg_e_le16(h);
	*iel = (uint32_t)h[4] | (uint32_t)h[5] << 8 | (uint32_t)h[6] << 16 | (uint32_t)h[7] << 24;
	if (avail < SWL25R_HEAD || len > avail) {
		return "record length above the event data";
	}
	if (*ieo < SWL25R_HEAD || *iel > len || *ieo > len - *iel) {
		return "ie_offset/ie_length outside the record";
	}
	if (*ieo + ((*iel + 3u) & ~3u) != len) {
		return "ie_offset + ie_length does not match length";
	}
	return NULL;
}

/* where the record bytes come from: the escan event buffer in the G1 block (bp + roff, read through the engine bus) or, patch 0288 (WLC_GET_BSS_INFO), a host copy of the ioctl answer (mem, `avail` bytes) */
struct swl25r_src {
	uint64_t bp;
	uint32_t roff;
	const uint8_t *mem;             /* non-NULL: read from here, off is relative to mem */
	uint32_t memlen;
};

static int
swl25r_read(struct swg_wx *x, const struct swl25r_src *s, uint32_t off, uint32_t n, uint8_t *out)
{
	if (s->mem != NULL) {
		if (off > s->memlen || n > s->memlen - off) {
			return -1;      /* callers check their offsets against `avail` first: never reached with a valid record (a stop, not a silent short read) */
		}
		memcpy(out, s->mem + off, n);
		return 0;
	}
	return swg_e_rdb(x, s->bp, s->roff + off, n, out);
}

/* 2026-11-11: copy the target record's IE block and the plain fields the Get 103 record needs (offsets of wl_bss_info_v109, [src refs/wlan-stage5/wlioctl.h.new:355-396, host compile in wlan_rsne_host_test.c]: beacon_period +14, rateset.count +52, rateset.rates +56 (u8, 500 kbps),
 * chanspec +72, phy_noise +80 (s8), SNR +124 (s16, read only when ie_offset >= 126 so that the two bytes are not IE bytes)). Every read is of the event buffer (or the BSS_INFO answer copy). The head was checked by swl25r_head (ie_offset + ie_length inside len, len inside the bytes carried).
 * At most SWL25R_IE_KEEP bytes of IEs; a longer block sets ie_short (the radio GAPs). A failed read is the stop code. 0 = done */
static int
swl25r_keep(struct swg_wx *x, const struct swl25r_src *src, struct swl25_tgt *t, uint32_t len, uint32_t ieo, uint32_t iel)
{
	uint8_t b2[2], rs[20], nz;
	uint32_t n, k;
	int ex;

	t->xf = 0u;
	t->ie_len = 0u;
	t->ie_short = 0u;
	t->snr = 0;
	ex = swl25r_read(x, src, 14u, 2u, b2);
	if (ex != 0) {
		return ex;
	}
	t->beacon = (uint16_t)swg_e_le16(b2);
	ex = swl25r_read(x, src, 52u, 20u, rs);
	if (ex != 0) {
		return ex;
	}
	n = (uint32_t)rs[0] | (uint32_t)rs[1] << 8 | (uint32_t)rs[2] << 16 | (uint32_t)rs[3] << 24;
	t->nrates = (uint8_t)(n > 16u ? 16u : n);
	for (k = 0; k < t->nrates; k++) {
		t->rates[k] = rs[4u + k];
	}
	ex = swl25r_read(x, src, 72u, 2u, b2);
	if (ex != 0) {
		return ex;
	}
	t->chspec = (uint16_t)swg_e_le16(b2);
	ex = swl25r_read(x, src, 80u, 1u, &nz);
	if (ex != 0) {
		return ex;
	}
	t->noise = (int16_t)(int8_t)nz;
	if (ieo >= 126u && len >= 126u) {
		ex = swl25r_read(x, src, 124u, 2u, b2);
		if (ex != 0) {
			return ex;
		}
		t->snr = (int16_t)swg_e_le16(b2);
	}
	n = iel > SWL25R_IE_KEEP ? SWL25R_IE_KEEP : iel;
	if (n != 0u) {
		ex = swl25r_read(x, src, ieo, n, t->ie);
		if (ex != 0) {
			return ex;
		}
	}
	t->ie_len = n;
	t->ie_short = iel > SWL25R_IE_KEEP ? 1u : 0u;
	t->xf = 1u;
	return 0;
}

/* the L23 hook: x->bssn = swl25r_note, x->bssc = &struct swl25_tgt. Returns 0 (also after a GAP: the scan goes on) or a negative stop from a failed buffer read */
static int
swl25r_note_src(struct swg_wx *x, const struct swg_ev *ev, const struct swl25r_src *src, uint32_t avail)
{
	struct swl25_tgt *t = (struct swl25_tgt *)x->bssc;
	uint8_t h[8], lb[4], eh[2];
	uint32_t len, ieo, iel, pos, k;
	const char *why;
	int ex;

	if (t != NULL && t->ssid_len != 0u && t->ssid_len <= 32u && ev->sl == t->ssid_len && memcmp(ev->ssid, t->ssid, t->ssid_len) == 0) {
		if (swl25r_zero(t->bssid, 6u)) {        /* T-fc10 H2 [read from source: the join is a wildcard, swl_cfg.bssid is zero]: the first record with the target SSID names the AP */
			memcpy(t->bssid, ev->bssid, 6);
			printf("SWEET WLAN: L23 join target BSSID taken from the first scan record of its SSID\n");
		} else if (memcmp(ev->bssid, t->bssid, 6) != 0 && t->other < 255u) {
			t->other++;
			printf("WIFI GAP: L25 a second AP carries the join SSID; the first record wins (skipped %u)\n", (unsigned)t->other);
		}
	}
	if (t == NULL || t->state == SWL25R_OK || swl25r_zero(t->bssid, 6u) || memcmp(ev->bssid, t->bssid, 6) != 0) {
		return 0;
	}
	if (ev->bver != SWG_E_BSS_VER) {
		return 0;       /* the version GAP is printed by the scan */
	}
	t->seen++;
	if (avail < SWL25R_HEAD) {
		return swl25r_bad(t, "record shorter than the 124 B head");
	}
	ex = swl25r_read(x, src, SWL25R_OFF_LEN, 4u, lb);
	if (ex == 0) {
		ex = swl25r_read(x, src, SWL25R_OFF_IEOFF, 8u, h);
	}
	if (ex != 0) {
		return ex;
	}
	len = (uint32_t)lb[0] | (uint32_t)lb[1] << 8 | (uint32_t)lb[2] << 16 | (uint32_t)lb[3] << 24;
	why = swl25r_head(len, h, avail, &ieo, &iel);
	printf("SWEET WLAN: L23 rsne record length %u ie_offset %u ie_length %u event bytes %u\n", len, ieo, iel, avail);
	if (why != NULL) {
		return swl25r_bad(t, why);
	}
	ex = swl25r_keep(x, src, t, len, ieo, iel);
	if (ex != 0) {
		return ex;
	}
	for (pos = ieo, k = 0; k < SWL25R_MAXIE && pos + 2u <= ieo + iel; k++) {
		ex = swl25r_read(x, src, pos, 2u, eh);
		if (ex != 0) {
			return ex;
		}
		if (pos + 2u + eh[1] > ieo + iel) {
			return swl25r_bad(t, "an IE runs past ie_length");
		}
		if (eh[0] == SWL25R_IE_RSN) {
			if (eh[1] < 2u || 2u + eh[1] > SWL25R_RSNE_MAX) {
				return swl25r_bad(t, "RSNE shorter than 2 B or longer than 64 B");
			}
			ex = swl25r_read(x, src, pos, 2u + eh[1], t->rsne);
			if (ex != 0) {
				return ex;
			}
			t->rsne_len = 2u + eh[1];
			t->state = SWL25R_OK;
			printf("SWEET WLAN: L23 rsne of the join target: %u B (IE 48 at record offset %u)\n", t->rsne_len, pos);
			return 0;
		}
		pos += 2u + eh[1];
	}
	return swl25r_bad(t, k >= SWL25R_MAXIE ? "no RSNE in the first 128 IEs" : "no RSNE (IE 48) in the target record");
}

static int
swl25r_note(struct swg_wx *x, const struct swg_ev *ev, uint64_t bp, uint32_t roff, uint32_t avail)
{
	struct swl25r_src src;

	src.bp = bp;
	src.roff = roff;
	src.mem = NULL;
	src.memlen = 0u;
	return swl25r_note_src(x, ev, &src, avail);
}

/* ---- patch 0288 (T-fca2 design "smallest design that gives the second AP"): the answer of WLC_GET_BSS_INFO (136) as the target record.
 * Answer layout [src wl_cfg80211.c.new:14450-14480 + wlioctl_defs.h.new:819 as quoted in docs/research/T-fca2-hotspot-bssid-design.md fact 5]: buf+0 u32 (the length word the request carried, echoed), the record wl_bss_info_v109_t at buf+4.
 * Record offsets (relative to the record; the same the escan parser uses, sweet_wlan_g2.h swg_e_event, [src WI:355-397, measured on hardware: wlan_rsne_host_test.c L1]): +0 version u32 (109), +4 length u32 (record bytes incl. IEs),
 * +8 BSSID[6], +18 SSID_len u8, +19 SSID[32], +72 chanspec u16, +78 RSSI i16, +88 ctl_ch u8, +116 ie_offset u16, +120 ie_length u32 (the IEs start ie_offset bytes into the record).
 * Every field is bounds-checked against the bytes the answer carries (n), as wl_validate_bss_length does [src wl_cfg80211.c.new:14374-14400]: length >= 124 and <= n - 4, ie_offset >= 124, ie_offset + ROUNDUP4(ie_length) == length.
 * Pure. 0 = ok and *ev filled (has_bss, bver, bssid, sl, ssid, cs, rssi, ctl); else the reason (a GAP is printed by the caller). */
#define SWL25R_BI_REC 4u                /* the record starts 4 bytes into the answer */
#define SWL25R_OFF_BSSID 8u
#define SWL25R_OFF_SSIDLEN 18u
#define SWL25R_OFF_SSID 19u
#define SWL25R_OFF_CHSPEC 72u
#define SWL25R_OFF_RSSI 78u
#define SWL25R_OFF_CTL 88u
static inline const char *
swl25r_bss_parse(const uint8_t *buf, uint32_t n, struct swg_ev *ev)
{
	const uint8_t *r;
	uint32_t len, ieo, iel, ver;
	const char *why;

	memset(ev, 0, sizeof *ev);
	if (buf == NULL || n < SWL25R_BI_REC + SWL25R_HEAD) {
		return "answer shorter than the 4 B length word plus the 124 B record head";
	}
	r = buf + SWL25R_BI_REC;
	ver = (uint32_t)r[0] | (uint32_t)r[1] << 8 | (uint32_t)r[2] << 16 | (uint32_t)r[3] << 24;
	if (ver != SWG_E_BSS_VER) {
		return "record version is not 109";
	}
	len = (uint32_t)r[4] | (uint32_t)r[5] << 8 | (uint32_t)r[6] << 16 | (uint32_t)r[7] << 24;
	why = swl25r_head(len, r + SWL25R_OFF_IEOFF, n - SWL25R_BI_REC, &ieo, &iel);
	if (why != NULL) {
		return why;
	}
	if (r[SWL25R_OFF_SSIDLEN] == 0u || r[SWL25R_OFF_SSIDLEN] > 32u) {
		return "SSID length is not 1..32";
	}
	if (swl25r_zero(r + SWL25R_OFF_BSSID, 6u) || (r[SWL25R_OFF_BSSID] & 1u) != 0u) {
		return "BSSID is zero or a group address";
	}
	ev->has_bss = 1;
	ev->bver = ver;
	memcpy(ev->bssid, r + SWL25R_OFF_BSSID, 6);
	ev->sl = r[SWL25R_OFF_SSIDLEN];
	memcpy(ev->ssid, r + SWL25R_OFF_SSID, 32);
	ev->cs = swg_e_le16(r + SWL25R_OFF_CHSPEC);
	ev->rssi = (int)(int16_t)swg_e_le16(r + SWL25R_OFF_RSSI);
	ev->ctl = r[SWL25R_OFF_CTL];
	return NULL;
}

/* the answer of WLC_GET_BSS_INFO as the join target's record: only for a target never seen (state NONE, wildcard BSSID, a credentials' SSID); the record's SSID must be the credentials' SSID, else a GAP and the target stays unset.
 * On success t->bssid and (via swl25r_note_src) t->rsne are set. 1 = the record was taken (state OK), 0 = refused or nothing to do (every refusal printed as a GAP); negative = a failed read */
static inline int
swl25r_note_bss_info(struct swg_wx *x, const uint8_t *buf, uint32_t n)
{
	struct swl25_tgt *t = (struct swl25_tgt *)x->bssc;
	struct swg_ev ev;
	struct swl25r_src src;
	const char *why;
	char gb[64];
	int ex;

	if (t == NULL || t->state != SWL25R_NONE || !swl25r_zero(t->bssid, 6u) || t->ssid_len == 0u || t->ssid_len > 32u) {
		return 0;
	}
	why = swl25r_bss_parse(buf, n, &ev);
	if (why != NULL) {
		snprintf(gb, sizeof gb, "BSS_INFO record unusable: %.36s", why);
		swl_gap("rsne", gb, "high");
		printf("WIFI GAP: BSS_INFO record unusable: %s\n", why);
		return 0;
	}
	if (ev.sl != t->ssid_len || memcmp(ev.ssid, t->ssid, t->ssid_len) != 0) {
		swl_gap("rsne", "BSS_INFO record is another SSID, refused", "high");
		printf("WIFI GAP: L25 WLC_GET_BSS_INFO names another SSID than the credentials': refused, no target\n");
		return 0;
	}
	printf("SWEET WLAN: L24 target from WLC_GET_BSS_INFO (not in the scan): chan %u rssi %d\n", (unsigned)ev.ctl, ev.rssi);
	src.bp = 0u;
	src.roff = 0u;
	src.mem = buf + SWL25R_BI_REC;
	src.memlen = n - SWL25R_BI_REC;
	ex = swl25r_note_src(x, &ev, &src, n - SWL25R_BI_REC);
	if (ex != 0) {
		return ex;
	}
	if (t->state == SWL25R_OK) {
		t->via_bss_info = 1u;
	}
	return t->state == SWL25R_OK;
}

/* after the scan: 0 = the RSNE is there; otherwise prints the one GAP that says why (wildcard target, never seen, unusable) and returns -1. Never partial. */
static int
swl25r_status(const struct swl25_tgt *t)
{
	const char *why = NULL;
	char gb[64];

	if (swl25r_zero(t->bssid, 6u)) {
		why = t->ssid_len != 0u ? "target SSID not in the scan" : "join target BSSID is zero (wildcard)";
	} else if (t->state == SWL25R_NONE) {
		why = "target BSSID not in the scan";
	} else if (t->state != SWL25R_OK) {
		why = "target record unusable (see above)";
	}
	if (why == NULL) {
		return 0;
	}
	snprintf(gb, sizeof gb, "RSNE missing: %.46s", why);
	swl_gap("rsne", gb, "high");
	printf("WIFI GAP: %s\n", gb);
	return -1;
}

#endif /* SWEET_WLAN_L25_RSNE_H */
