/* sweet_wlan_join_fw.h - firmware payload builders of the Wi-Fi join (H1-H5, H8-H10; split out of sweet_wlan_join.h, T-4115, R29; no code changed).
 * Included ONLY by sweet_wlan_join.h (at its end): it uses the SWJ_* constants, helpers and SWJ_GAP/SWJ_LOG defined there. */
#ifndef SWEET_WLAN_JOIN_FW_H
#define SWEET_WLAN_JOIN_FW_H

#ifndef SWEET_WLAN_JOIN_H
#error "sweet_wlan_join_fw.h: include the parent header, not this file"
#endif

/* ------------------------------------------------------------------ firmware payloads (H1-H5, H8-H10) */
/* wsec_key: BW struct bwfm_wsec_key (bwfmreg.h:629-648) = S5 wl_wsec_key_t (wlioctl.h.new:1993-2013). Both sum to 164 bytes:
 * index 4 + len 4 + data 32 + pad 72 + algo 4 + flags 4 + pad 12 + iv_initialized 4 + pad 4 + rxiv 8 + pad 8 + ea 6 + pad 2 = 164. */
#define SWJ_WSEC_KEY_SIZE   164u
#define SWJ_WSEC_OFF_INDEX  0u
#define SWJ_WSEC_OFF_LEN    4u
#define SWJ_WSEC_OFF_DATA   8u
#define SWJ_WSEC_OFF_ALGO   112u
#define SWJ_WSEC_OFF_FLAGS  116u
#define SWJ_WSEC_OFF_IVINIT 132u
#define SWJ_WSEC_OFF_EA     156u

static inline int swj_wsec_key(uint8_t out[SWJ_WSEC_KEY_SIZE], uint32_t index, const uint8_t *key, uint32_t klen, uint32_t algo,
    uint32_t flags, const uint8_t *ea)
{
	if (klen > 32u)
		return -1;
	memset(out, 0, SWJ_WSEC_KEY_SIZE);
	swj_put_le32(out + SWJ_WSEC_OFF_INDEX, index);
	swj_put_le32(out + SWJ_WSEC_OFF_LEN, klen);
	memcpy(out + SWJ_WSEC_OFF_DATA, key, klen);
	swj_put_le32(out + SWJ_WSEC_OFF_ALGO, algo);
	swj_put_le32(out + SWJ_WSEC_OFF_FLAGS, flags);
	if (ea)
		memcpy(out + SWJ_WSEC_OFF_EA, ea, 6);
	return 0;
}

/* H8: pairwise key = TK (PTK[32..48)), index 0, AES_CCM, carries the AP MAC. H9: group key, index = GTK id, PRIMARY flag, no ea. */
static inline int swj_wsec_key_pairwise(const struct swj_ctx *c, uint8_t out[SWJ_WSEC_KEY_SIZE])
{
	return swj_wsec_key(out, 0, c->tptk + 32, 16, SWJ_ALGO_AES_CCM, 0, c->aa);
}
static inline int swj_wsec_key_group(const struct swj_ctx *c, uint8_t out[SWJ_WSEC_KEY_SIZE])
{
	return swj_wsec_key(out, c->gtk_id, c->gtk, 16, SWJ_ALGO_AES_CCM, SWJ_WSEC_PRIMARY_KEY, NULL);
}

/* H10: GET wsec -> clear WEP|TKIP|AES -> set the key's cipher bit -> SET wsec [BW/bwfm.c:2956-2959]. */
static inline uint32_t swj_wsec_merge(uint32_t cur, uint32_t cipher_bit)
{
	return (cur & ~(SWJ_WSEC_WEP | SWJ_WSEC_TKIP | SWJ_WSEC_AES)) | cipher_bit;
}

/* SET_VAR / GET_VAR payload: "name\0" + data [R108 s.2.2]. Returns the length, or 0 (with a GAP) when it does not fit. */
static inline uint32_t swj_var_buf(uint8_t *out, uint32_t cap, const char *name, const void *data, uint32_t dlen)
{
	uint32_t nl = (uint32_t)strlen(name) + 1u;

	if (nl + dlen > cap) {
		SWJ_GAP("WIFI GAP: iovar payload does not fit the buffer");
		return 0;
	}
	memcpy(out, name, nl);
	if (dlen)
		memcpy(out + nl, data, dlen);
	return nl + dlen;
}
static inline uint32_t swj_var_u32(uint8_t *out, uint32_t cap, const char *name, uint32_t v)
{
	uint8_t d[4];

	swj_put_le32(d, v);
	return swj_var_buf(out, cap, name, d, 4);
}

/* H5: extended join params. TWO layouts exist (T-4ba0):
 *  v0 [BW bwfm_ext_join_params: bwfmreg.h ssid 36 + scan 20 + assoc 12 = 68] = S5 wl_extjoin_params_t (wlioctl.h.new:1505-1512). The BCM4389 answered it
 *  with status -18 = BCME_BADSSIDLEN (bcmerror.h:77) [obs run 20261107T085006Z-wlan-runG2-join24].
 *  v1 = S5 wl_extjoin_params_v1_t (wlioctl.h.new:1514-1523): u16 version, u16 pad, ssid 36, scan 20, assoc_v1 16 (u16 version, u16 flags, bssid 6, u16 bssid_cnt,
 *  i32 chanspec_num) = 76 B (WL_EXTJOIN_PARAMS_FIXED_SIZE_V1, :1527). The stock driver sends v1 when its firmware reports a new enough wlc_ver
 *  (wl_cfg80211.c.new:18512-18519, join_iovar_ver) and its v1 fill code (wl_fillup_assoc_params_v1, :6270-6330) never sets either version field (the buffer is
 *  zeroed, so both are 0) nor the assoc flags without a BSSID hint [read from source]. A v1 firmware reads SSID_len at byte 4 of a v0 payload = the first 4 SSID
 *  characters as a u32 (> 32) = BCME_BADSSIDLEN [model, consistent with the observed -18; NOT yet observed with v1].
 * and the SET_SSID fallback [BW bwfm_join_params = ssid 36 + assoc 12 = 48]. scan_type -1, nprobes/active/passive/home -1 (BW/bwfm.c:2054-2062;
 * DHD wl_cfg80211.c.new:6308-6318 with chan_cnt 0). */
#define SWJ_EXT_JOIN_SIZE    68u
#define SWJ_EXT_JOIN_V1_SIZE 76u
#define SWJ_JOIN_SIZE        48u
static inline void swj_assoc_params(uint8_t *o, const uint8_t bssid[6])
{
	memset(o, 0, 12);
	memcpy(o, bssid, 6);		/* pad 2, chanspec_num 0 */
}
static inline int swj_join_ext(uint8_t out[SWJ_EXT_JOIN_SIZE], const uint8_t *ssid, uint32_t slen, const uint8_t bssid[6])
{
	if (slen == 0 || slen > SWJ_MAX_SSID)
		return -1;
	memset(out, 0, SWJ_EXT_JOIN_SIZE);
	swj_put_le32(out, slen); memcpy(out + 4, ssid, slen);
	memset(out + 36, 0xff, 20);	/* scan_type -1 (0xff), nprobes/active/passive/home -1; the 3 pad bytes after scan_type are zero */
	out[37] = out[38] = out[39] = 0;
	swj_assoc_params(out + 56, bssid);
	return 0;
}
/* v1 (the form swj_join_cmds sends): version 0, pad 0, SSID_len u32 at 4, SSID at 8, scan at 40 (scan_type 0xff + 3 pad, four -1 words), assoc_v1 at 60:
 * version 0, flags 0, bssid, bssid_cnt 0, chanspec_num 0 (all channels), no chanspec_list */
static inline int swj_join_ext_v1(uint8_t out[SWJ_EXT_JOIN_V1_SIZE], const uint8_t *ssid, uint32_t slen, const uint8_t bssid[6])
{
	if (slen == 0 || slen > SWJ_MAX_SSID)
		return -1;
	memset(out, 0, SWJ_EXT_JOIN_V1_SIZE);
	swj_put_le32(out + 4, slen); memcpy(out + 8, ssid, slen);
	memset(out + 40, 0xff, 20);	/* scan_type -1 (0xff) and nprobes/active/passive/home -1 */
	out[41] = out[42] = out[43] = 0;	/* the 3 pad bytes after scan_type */
	memcpy(out + 64, bssid, 6);	/* assoc_v1.bssid; version, flags, bssid_cnt, chanspec_num stay 0 */
	return 0;
}
static inline int swj_join_plain(uint8_t out[SWJ_JOIN_SIZE], const uint8_t *ssid, uint32_t slen, const uint8_t bssid[6])
{
	if (slen == 0 || slen > SWJ_MAX_SSID)
		return -1;
	memset(out, 0, SWJ_JOIN_SIZE);
	swj_put_le32(out, slen); memcpy(out + 4, ssid, slen);
	swj_assoc_params(out + 36, bssid);
	return 0;
}

/* H1-H5 in order: sup_wpa 0, wpa_auth, wsec, auth open, mfp none, join (BW/bwfm.c:569, 2031-2062). Each command is SET_VAR (263)
 * with a "name\0"+u32 payload except the join (SET_VAR "join", the 76 B v1 params, T-4ba0). Returns the count (6), or 0 with a GAP. */
struct swj_cmd { uint32_t cmd; const char *row; uint8_t buf[96]; uint32_t len; };
static inline unsigned swj_join_cmds(struct swj_cmd out[6], const uint8_t *ssid, uint32_t slen, const uint8_t bssid[6], int group_tkip)
{
	static const char *const names[5] = { "sup_wpa", "wpa_auth", "wsec", "auth", "mfp" };
	static const char *const rows[6] = { "H1", "H2", "H3", "H4", "H4", "H5" };
	uint32_t vals[5];
	uint8_t ext[SWJ_EXT_JOIN_V1_SIZE];
	unsigned i;

	vals[0] = 0; vals[1] = SWJ_WPA2_AUTH_PSK; vals[2] = SWJ_WSEC_AES | (group_tkip ? SWJ_WSEC_TKIP : 0u); vals[3] = 0; vals[4] = 0;
	if (swj_join_ext_v1(ext, ssid, slen, bssid) != 0) {
		SWJ_GAP("WIFI GAP: join refused: ssid length not 1..32");
		return 0;
	}
	for (i = 0; i < 5; i++) {
		out[i].cmd = SWJ_WLC_SET_VAR; out[i].row = rows[i];
		out[i].len = swj_var_u32(out[i].buf, sizeof out[i].buf, names[i], vals[i]);
	}
	out[5].cmd = SWJ_WLC_SET_VAR; out[5].row = rows[5];
	out[5].len = swj_var_buf(out[5].buf, sizeof out[5].buf, "join", ext, sizeof ext);
	return out[5].len ? 6u : 0u;
}

/* ALL-ON T-32: the firmware keep-alive, SET_VAR "mkeep_alive" + the packed wl_mkeep_alive_pkt_v1_t header of 11 bytes, little endian: u16 version = 1, u16 length = 11 (WL_MKEEP_ALIVE_FIXED_LEN),
 * u32 period_msec, u16 len_bytes = 0 (0 = the firmware sends a NULL data frame), u8 keep_alive_id = 0; no data[] [src docs/research/138-wlan-keepalive.md s.1/s.2: bcmdhd bcm4389 dhd_common.c:7257-7295 (null packet, id 0, len_bytes 0),
 * wlioctl.h:7274-7290; mainline brcmfmac fwil_types.h:1221-1237 is the same layout; the Pixel build period is 30000 ms, Kbuild:480]. The period is a plain millisecond count (the high bit means "send now" and is refused here: it is
 * never wanted). Row "KA". Returns 1 with out filled, or 0 with a GAP (period 0 would STOP the keep-alive [guess, research 138 s.1], or has the high bit set). */
#define SWJ_MKEEP_ID       0u
#define SWJ_MKEEP_VERSION  1u
#define SWJ_MKEEP_HDR_LEN  11u
#define SWJ_MKEEP_PERIOD_MS 30000u
static inline unsigned swj_mkeep_alive_cmd(struct swj_cmd *out, uint32_t period_ms)
{
	uint8_t d[SWJ_MKEEP_HDR_LEN];

	if (period_ms == 0u || (period_ms & 0x80000000u) != 0u) {
		SWJ_GAP("WIFI GAP: mkeep_alive refused: period 0 or with the immediate bit");
		return 0;
	}
	memset(d, 0, sizeof d);
	d[0] = (uint8_t)SWJ_MKEEP_VERSION;		/* u16 version, the high byte stays 0 */
	d[2] = (uint8_t)SWJ_MKEEP_HDR_LEN;		/* u16 length, the high byte stays 0 */
	swj_put_le32(d + 4, period_ms);			/* u32 period_msec; len_bytes (u16 at 8) = 0, keep_alive_id (u8 at 10) = SWJ_MKEEP_ID = 0 */
	out->cmd = SWJ_WLC_SET_VAR; out->row = "KA";
	out->len = swj_var_buf(out->buf, sizeof out->buf, "mkeep_alive", d, sizeof d);
	return out->len ? 1u : 0u;
}

/* T-857c knob A (station address A/B): the boot-arg `sweetwlanmac=<12 hex>` makes the glue insert ONE SET_VAR "cur_etheraddr" (name 14 incl. NUL + 6 B) as command 1, right after sup_wpa and before the join
 * [src the stock driver sets the same iovar with no WLC_DOWN around it: bcm4389-dhd_linux.c:10588-10608, docs/reviews/T-b8c6-proof.md item 4; the firmware's answer is a guess]. The value is a private run parameter (rule 4): it is never printed
 * (the engine sends this command through the secret-quiet path) and never lands in git. Accepted: exactly 12 hex digits; unicast (bit 0 of octet 0 clear), globally administered (bit 1 clear: the baseline SPA has bit 1 SET [obs T-8d5f proof],
 * so this knob tests the opposite), not all zero. Anything else is refused loudly and the glue then runs no join at all (an A/B with a silently dropped knob would be a false baseline, rule 8). */
static inline int swj_parse_mac(const char *s, uint8_t mac[6])
{
	uint8_t m[6];
	unsigned i;

	if (s == NULL)
		return -1;
	for (i = 0; i < 12u; i++) {
		char c = s[i];
		unsigned v;

		if (c >= '0' && c <= '9') v = (unsigned)(c - '0');
		else if (c >= 'a' && c <= 'f') v = (unsigned)(c - 'a') + 10u;
		else if (c >= 'A' && c <= 'F') v = (unsigned)(c - 'A') + 10u;
		else return -1;
		if (i & 1u) m[i / 2u] |= (uint8_t)v; else m[i / 2u] = (uint8_t)(v << 4);
	}
	if (s[12] != '\0')
		return -1;
	memcpy(mac, m, 6);
	return 0;
}
static inline int swj_mac_ok(const uint8_t mac[6])
{
	return (mac[0] & 3u) == 0u && (mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5]) != 0u;
}
/* Inserts the cur_etheraddr SET_VAR (row "H1m") as out[1] of the n == 6 commands swj_join_cmds built (out has room for 7). Returns 7, or 0 with a GAP (the caller drops all commands). */
static inline unsigned swj_join_cmds_mac(struct swj_cmd out[7], unsigned n, const uint8_t mac[6])
{
	unsigned i;

	if (n != 6u) {
		SWJ_GAP("WIFI GAP: station MAC knob: needs the six base join commands");
		return 0;
	}
	if (!swj_mac_ok(mac)) {
		SWJ_GAP("WIFI GAP: station MAC knob refused: multicast, locally administered or zero");
		return 0;
	}
	for (i = 6u; i > 1u; i--)
		out[i] = out[i - 1u];
	memset(&out[1], 0, sizeof out[1]);
	out[1].cmd = SWJ_WLC_SET_VAR; out[1].row = "H1m";
	out[1].len = swj_var_buf(out[1].buf, sizeof out[1].buf, "cur_etheraddr", mac, 6);
	return out[1].len ? 7u : 0u;
}

#endif /* SWEET_WLAN_JOIN_FW_H */
