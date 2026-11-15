/* sweet_wlan_sta.h - PixOS station boundary for the Apple80211 handler (X-W1).
 *
 * PURPOSE: one host-testable seam between the iOS-facing Apple80211 request handler (upper) and a Wi-Fi
 * radio (lower: the future brcmfmac/bwfm port, or the fake radio in wlan_sta_host_test.c).
 * CONSUMER: W-D7 (the kernel-side Apple80211 ioctl + kev glue). Header-only C99, no malloc, no libc beyond
 * memcpy/memset/snprintf (override SWSTA_MEMCPY / SWSTA_MEMSET / SWSTA_SNPRINTF before including).
 * OWN CODE: written from the facts in docs/research/110-apple80211-io80211-ios167.md (iOS 16.7 reverse) only;
 * no external code (HakiOS/UniXNU/Ath12k) opened or copied. Nothing here is an observation on our hardware.
 *
 * Tags: [src 110:N] = research 110 line N. [open] = 110 does not settle it; nothing is invented for it.
 *  [RE-A Ln/Rn/Sn/En] = docs/research/W-D7-a80211-get-layouts.md row (iOS 16.7 disassembly, T-a15a); [RE-B ...] =
 *      W-D7-a80211-join-layouts.md (T-101c/T-e0f8). Both are reverse reads, not observations on our hardware.
 *  CODEC (T-853c): swsta_codec_167 below carries the 16.7 byte layouts of those two notes; swsta_init uses it when
 *      the includer passes no codec. Values the notes do not give are zero-filled and reported as a gap (rule 8).
 *  [src 110:74-78] req_len bytes are copied to/from req_data; errno 0x66 (EOPNOTSUPP) or 6 (ENXIO) is the SOFT
 *      "unsupported" answer to a Get, any other errno is a hard error  (3.3).
 *  [src 110:118-129] kev vendor 1 / class 6 / subclass 1, codes POWER_CHANGED 1, SSID 2, BSSID 3, LINK 4,
 *      ASSOC_DONE 9, SCAN_DONE 10 (s.5 :118-129); payload layouts not decoded [open], an empty payload may do [guess].
 *  [src 110:134-163] type numbers and request lengths (table swsta_len below, one comment per row).
 *  Layouts: 110 gives the byte layout of only Get 1 SSID (32 plain bytes, :152) and Get 9 BSSID (6 bytes, :153).
 *      Every other payload (12, 19, 106, 207, 27, 11, 4, 16/17, 2, 6, 21, 103, Set 19, Set 20) has NO layout in 110
 *      [open]: the handler checks the length and delegates the bytes to an includer codec (struct swsta_codec);
 *      without a codec it answers EOPNOTSUPP and reports a gap (type 12 answers 0 with zero bytes plus a gap,
 *      because the bind probe needs ioctl result 0 [src 110:61]).
 *  Still open / [guess] in 110, kept open here: ASSOCIATE_RESULT code mapping (:199, 35-entry table), the errno
 *      that ends the Get-11 loop (:190, EIO is a guess; macro SWSTA_E_SCAN_END), the errno for Gets while not
 *      joined (ENXIO chosen from the two soft errnos of :77), Get 4 length 16 (:154 guess), the length of
 *      Get 254 (110 gives 4824 for 27 only, :143: 254 is a gap), event payloads, the 592-byte ASSOCIATE offsets (:197),
 *      the 5456-byte SCAN_REQ body (ignored, reported as a gap on every Set 10).
 *
 * Rule 8: every request is logged (type, Get/Set, len, result - never contents, no SSID/key bytes); every
 * unsupported/unhandled type/length calls the gap callback with "a80211 type N <get|set> len L not handled";
 * swsta_summary() prints per-type counters and gaps=N.
 */
#ifndef SWEET_WLAN_STA_H
#define SWEET_WLAN_STA_H

#include <stdint.h>
#ifndef SWSTA_MEMCPY
#include <string.h>
#define SWSTA_MEMCPY memcpy
#define SWSTA_MEMSET memset
#endif
#ifndef SWSTA_SNPRINTF
#include <stdio.h>
#define SWSTA_SNPRINTF snprintf
#endif
#ifndef SWSTA_MEMCMP
#include <string.h>
#define SWSTA_MEMCMP memcmp	/* LOWCLK B3 (patch 0363): the Set 383 key table; the kernel glue defines it before including */
#endif

/* soft errnos [src 110:77], not-joined and scan-end answers (see "still open" above) */
#define SWSTA_ENXIO 6u			/* not joined: soft path of the library [RE-A R2, R5] (ENOENT would be a hard failure) */
#define SWSTA_EOPNOTSUPP 0x66u
#define SWSTA_EINVAL 22u
#define SWSTA_EIO 5u
#ifndef SWSTA_E_SCAN_END
#define SWSTA_E_SCAN_END 5u		/* EIO after the last record ends the Get-11 loop normally [RE-A S3] */
#endif

/* type numbers [src 110:134-163] */
enum {
	SWSTA_T_SSID = 1, SWSTA_T_AUTH_TYPE = 2, SWSTA_T_CHANNEL = 4, SWSTA_T_PROTMODE = 6, SWSTA_T_BSSID = 9,
	SWSTA_T_SCAN_REQ = 10, SWSTA_T_SCAN_RESULT = 11, SWSTA_T_CARD_CAPS = 12, SWSTA_T_RSSI = 16, SWSTA_T_NOISE = 17,
	SWSTA_T_POWER = 19, SWSTA_T_ASSOCIATE = 20, SWSTA_T_ASSOC_RESULT = 21, SWSTA_T_DISASSOCIATE = 22,
	SWSTA_T_SUPP_CHANNELS = 27, SWSTA_T_CURRENT_NETWORK = 103, SWSTA_T_RSSI_BOUNDS = 106,
	SWSTA_T_CHANNELS_INFO = 207, SWSTA_T_HW_SUPP_CHANNELS = 254, SWSTA_T_COUNTRY_CODE = 51,
	SWSTA_T_SET_PROPERTY = 383,	/* wifid WiFiDeviceSetProperty -> Apple80211SetProperty: Set, len 4104 [research 159 s.3 row 1; RM-00870 / RM-00812]; only OBSERVED here (swsta_p383), still answered "not handled" */
	SWSTA_MAXTYPE = 425		/* valid range 1..425 [src 110:134-135] */
};
/* kev event codes [src 110:118-129] */
enum { SWSTA_EV_POWER_CHANGED = 1, SWSTA_EV_SSID_CHANGED = 2, SWSTA_EV_BSSID_CHANGED = 3, SWSTA_EV_LINK_CHANGED = 4,
       SWSTA_EV_ASSOC_DONE = 9, SWSTA_EV_SCAN_DONE = 10 };

/* ---- lower boundary: neutral structures (our own, not an Apple layout) ---- */
#define SWSTA_SEC_OPEN 0u
#define SWSTA_SEC_WPA2_PSK 1u
#define SWSTA_SEC_OTHER 0x80000000u	/* any security the radio saw and we do not model */
#define SWSTA_IE_CAP 512u	/* bytes of IEs kept per BSS [decision, 2026-11-11 scan records; the record holds 1024 at most, W-D3 s.1 +0x8a]; 64 entries x 2 tables (the list and the keep thread's scratch table) = ~75 KB of static tables */
#define SWSTA_RATE_MAX 15u	/* the record's rate array is +0x24..+0x5f = 15 entries [W-D3 s.1] */
struct swsta_bss {
	uint8_t ssid[32]; uint8_t ssid_len; uint8_t bssid[6]; uint32_t channel; int32_t rssi; uint32_t sec;
	/* 2026-11-11 scan records (W-D3): what the firmware's bss_info carries beyond the first fields; all zero = not known (the boot-time replay has no IEs) */
	uint32_t chflags;		/* record +0x0c: 0x2 20 MHz, 0x4 40 MHz, 0x8 2.4 GHz, 0x10 5 GHz [W-D3 s.3] */
	uint16_t beacon;		/* beacon period, TU, record +0x18 */
	int16_t noise, snr;		/* dBm / dB, 0 = not reported (record +0x12 / +0x14, gate +0x10) */
	uint8_t nrates; uint8_t rates[SWSTA_RATE_MAX];	/* Mbps integers, (fw & 0x7f) / 2 (5.5 -> 5) [W-D3 s.2 R] */
	uint16_t ie_len; uint8_t ie[SWSTA_IE_CAP];	/* whole IEs only */
};
enum { SWSTA_KEY_NONE = 0, SWSTA_KEY_PASSPHRASE = 1, SWSTA_KEY_PMK = 2 };
struct swsta_join {
	uint8_t ssid[32]; uint8_t ssid_len; uint8_t bssid[6]; int has_bssid;
	uint8_t key[64]; uint32_t key_len; int key_kind;	/* secret: never logged */
};
struct swsta_status { int joined; uint8_t ssid[32]; uint8_t ssid_len; uint8_t bssid[6]; uint32_t channel; int32_t rssi; uint32_t sec; /* SWSTA_SEC_*, 0 = open */ };
enum { SWSTA_PWR_OFF = 0, SWSTA_PWR_ON = 1, SWSTA_PWR_GET = 2 };
/* radio -> station events */
enum { SWSTA_REV_SCAN_DONE = 1, SWSTA_REV_ASSOC_DONE = 2, SWSTA_REV_LINK_UP = 3, SWSTA_REV_LINK_DOWN = 4 };
struct swsta_rev { int kind; int result; /* ASSOC_DONE: 0 ok, else radio code; raw, mapping open */ int reason; /* LINK_DOWN */ };

struct swsta_radio {
	/* the 6 verbs; every one returns 0 or a positive errno-like code */
	int (*power)(void *r, int op, int *state);	/* SWSTA_PWR_*; *state = resulting power (0/1) */
	int (*scan_start)(void *r);			/* async; completes with SWSTA_REV_SCAN_DONE; rewinds bss_next */
	int (*bss_next)(void *r, struct swsta_bss *out);/* 1 = produced, 0 = end of list */
	int (*join)(void *r, const struct swsta_join *j);	/* async; completes with SWSTA_REV_ASSOC_DONE */
	int (*leave)(void *r);
	int (*status)(void *r, struct swsta_status *out);
	void *ctx;
	/* event callback up, set by swsta_init; the radio calls up(up_ctx, &ev), possibly from inside a verb */
	void (*up)(void *up_ctx, const struct swsta_rev *ev);
	void *up_ctx;
	/* optional (NULL = none): the entry of the latest scan list with this BSSID (Get 103 takes the IEs / rates / flags of the joined AP from it); 1 = found */
	int (*bss_find)(void *r, const uint8_t bssid[6], struct swsta_bss *out);
};

/* ---- optional codec: byte layouts that 110 does not give (owned by the includer, filled once reversed) ---- */
enum { SWSTA_AS_NONE = 0, SWSTA_AS_PENDING = 1, SWSTA_AS_DONE = 2 };	/* join state behind Get 21 */
struct swsta_view {
	int power_on; struct swsta_status st; const struct swsta_bss *bss; /* bss set for Get 11, and for Get 103 when the latest scan list holds the joined BSSID */
	uint32_t assoc_result;	/* raw radio code of the last ASSOC_DONE (0 = ok); Apple code via swsta_assoc_code */
	int assoc_state;	/* SWSTA_AS_* */
};
struct swsta_setreq { int power_on; struct swsta_join join; const char *gap; /* codec: static text (<= 40 chars) to report as a gap */ };
struct swsta_codec {
	/* return 0 = filled buf[0..len), else a positive errno */
	int (*get)(void *cx, uint32_t type, const struct swsta_view *v, void *buf, uint32_t len);
	/* Set 19 fills power_on, Set 20 fills join; return 0 or a positive errno */
	int (*set)(void *cx, uint32_t type, const void *buf, uint32_t len, struct swsta_setreq *out);
	void *cx;
};

/* ---- includer callbacks ---- */
struct swsta_cb {
	void (*log)(void *cx, const char *line);	/* one line per request / summary */
	void (*gap)(void *cx, const char *text);	/* text <= 63 chars + NUL */
	void (*post)(void *cx, uint32_t code);		/* kev_post_msg(vendor 1, class 6, subclass 1, code); empty payload [guess] */
	void (*post_data)(void *cx, uint32_t code, const void *d, uint32_t n);	/* W-UI: the same with a payload after ifname[16]; NULL = only `post` (payload-less) */
	void *cx;
};

/* LOWCLK B3 (xnu patch 0363, research 161 s.3.6): the property keys iOS sets with Set 383. The call: u32 1 at 0, u32 L+1 at 4 (L+1 <= 0x1000), an XML plist of L bytes at 8, NUL terminated, ONE `<key>name</key>`
 * per call [research W-UI-a80211-refused-types.md:14, :24, :44]. Logged: the KEY NAME and L only, never a value; de-duplicated in a table of SWSTA_P383_MAX distinct keys. swsta.probe = 0 (default) = no scan, no line. */
#define SWSTA_P383_MAX 32u
#define SWSTA_P383_KEY 48u	/* bytes kept of a key name, NUL included */
#ifndef SWSTA_PROBE_LOG
#define SWSTA_PROBE_LOG(l) ((void)(l))	/* the includer prints `SWEET PROBE: <l>` (the xnu glue); a host test captures it */
#endif
struct swsta_p383 { char key[SWSTA_P383_KEY]; uint32_t len, n; };
struct swsta_cnt { uint32_t get, set, ok, soft, gap; };
struct swsta {
	struct swsta_radio *radio; const struct swsta_codec *codec; struct swsta_cb cb;
	int power_on; uint32_t assoc_result; int assoc_state; uint32_t gaps, requests;
	uint32_t relink_armed, relinks, snr_gapped;	/* W-UI: LINK_CHANGED re-posted after wifid's device open (armed by Get 207, fired by the next Get 103) */
	struct swsta_cnt cnt[SWSTA_MAXTYPE + 1]; struct swsta_cnt oob;	/* oob = type 0 or > 425 */
	uint32_t probe;		/* LOWCLK B3 (patch 0363): 1 = log the Set 383 keys; set by the glue from boot-arg sweetprobe=1, 0 after swsta_init */
	struct swsta_p383 p383[SWSTA_P383_MAX]; uint32_t p383_used, p383_over, p383_shape;	/* distinct keys, new keys that found the table full, calls of an unexpected shape */
};

/* request length per [src 110] (0 = not cited -> gap). dir: 0 Get, 1 Set. */
static inline uint32_t swsta_len(int set, uint32_t type)
{
	if (!set) switch (type) {
	case SWSTA_T_CARD_CAPS: return 13;		/* :137 */
	case SWSTA_T_POWER: return 24;			/* :140 */
	case SWSTA_T_RSSI_BOUNDS: return 16;		/* :141 */
	case SWSTA_T_CHANNELS_INFO: return 10016;	/* :142 */
	case SWSTA_T_SUPP_CHANNELS: case SWSTA_T_HW_SUPP_CHANNELS: return 4824;	/* :143; 254 = same body [RE-A L9] */
	case SWSTA_T_SCAN_RESULT: return 1164;		/* :147 */
	case SWSTA_T_ASSOC_RESULT: return 0;		/* 4-byte class: req_len 0, answer in req_val [RE-A R1, RE-B s.3]; handled in swsta_a80211 */
	case SWSTA_T_CURRENT_NETWORK: return 1164;	/* :151 */
	case SWSTA_T_SSID: return 32;			/* :152 */
	case SWSTA_T_BSSID: return 6;			/* :153 */
	case SWSTA_T_CHANNEL: return 16;		/* :154; confirmed [RE-A L3] */
	case SWSTA_T_AUTH_TYPE: return 16;		/* :155 */
	case SWSTA_T_PROTMODE: return 12;		/* :156 */
	case SWSTA_T_RSSI: case SWSTA_T_NOISE: return 52;	/* :157 */
	case SWSTA_T_COUNTRY_CODE: return 3;		/* NUL-terminated 2-letter code, 3 B (not 24) [docs/research/W-UI-a80211-refused-types.md row 51] */
	default: return 0;
	} else switch (type) {
	case SWSTA_T_POWER: return 24;			/* :140 */
	case SWSTA_T_SCAN_REQ: return 5456;		/* :146 */
	case SWSTA_T_ASSOCIATE: return 592;		/* :148 */
	case SWSTA_T_DISASSOCIATE: return 0;		/* :150: no data (8 bytes with a reason [RE-B 4b]); handled in swsta_a80211 */
	default: return 0;
	}
}

/* ---- the 16.7 codec (T-853c): byte layouts from the two reverser notes, own code, no Apple bytes ---- */
static inline void swsta_p16(uint8_t *b, uint32_t off, uint32_t v) { b[off] = (uint8_t)v; b[off + 1] = (uint8_t)(v >> 8); }
static inline void swsta_p32(uint8_t *b, uint32_t off, uint32_t v) { swsta_p16(b, off, v); swsta_p16(b, off + 2, v >> 16); }
static inline uint32_t swsta_g32(const uint8_t *b, uint32_t off)
{
	return (uint32_t)b[off] | ((uint32_t)b[off + 1] << 8) | ((uint32_t)b[off + 2] << 16) | ((uint32_t)b[off + 3] << 24);
}

/* Get 21 value [RE-B s.3]: 0 = no result yet (sync poller), 0xffff = still joining (async poller), 1 = success,
 * 2..35 = _errorForAssociateResult table index, 36+ = generic failure 0xfffff0c2. Which Apple code means "wrong key"
 * is not readable from the table [open 3a]; a radio failure code 2..35 is passed through [guess], else 36.
 * *why = static gap text (<= 40 chars) or 0. */
static inline uint32_t swsta_assoc_code(const struct swsta_view *v, const char **why)
{
	*why = 0;
	if (v->assoc_state == SWSTA_AS_NONE) { *why = "get 21 asked with no join, answered 0"; return 0; }
	if (v->assoc_state == SWSTA_AS_PENDING) { *why = "get 21 pending: 0xffff (async guess)"; return 0xffffu; }
	if (v->assoc_result == 0) return 1;
	if (v->assoc_result >= 2 && v->assoc_result <= 35) { *why = "get 21 fail code passed through [guess]"; return v->assoc_result; }
	*why = "get 21 radio code unmapped -> 36"; return 36;
}

/* One 1164-byte scan-result record, shared by Get 11 and Get 103 [RE-B s.4, RE-A S1/S4; W-D3-scan-record-layout.md s.1]. Offsets from the record start:
 * +0x08 channel s32, +0x0c channel flags s32, +0x10 u16 noise/snr gate (0 = both skipped; 1 only when noise or snr is known), +0x12 noise s16, +0x14 snr s16,
 * +0x16 rssi s16, +0x18 beacon interval s16 (TU), +0x1a capabilities u16 (ESS bit 0; privacy bit 4 is the 802.11 standard bit), +0x1c bssid[6], +0x22 rate count u8 (<= 15), +0x24 rates u32 each (Mbps),
 * +0x60 ssid_len u8, +0x61 ssid, +0x84 age s32 (0), +0x88 flag byte (0), +0x8a IE length u16 (ALWAYS written: the caller pre-set the 0x400 capacity, 0 = no IE), +0x8c IEs (<= 0x400; ours <= SWSTA_IE_CAP).
 * x = the radio's entry for extra fields (NULL = none known: the extras stay 0 and the IE length is written 0). */
static inline void swsta_rec(uint8_t *b, const uint8_t *ssid, uint32_t ssid_len, const uint8_t *bssid, uint32_t channel,
			     int32_t rssi, uint32_t sec, const struct swsta_bss *x)
{
	uint32_t i, n;
	if (ssid_len > 32) ssid_len = 32;
	swsta_p32(b, 0x08, channel);
	swsta_p16(b, 0x16, (uint32_t)rssi);
	swsta_p16(b, 0x1a, 0x0001u | (sec == SWSTA_SEC_OPEN ? 0u : 0x0010u));
	SWSTA_MEMCPY(b + 0x1c, bssid, 6);
	b[0x60] = (uint8_t)ssid_len;
	SWSTA_MEMCPY(b + 0x61, ssid, ssid_len);
	if (x) {
		swsta_p32(b, 0x0c, x->chflags);
		if (x->noise != 0 || x->snr != 0) {
			swsta_p16(b, 0x10, 1u);
			swsta_p16(b, 0x12, (uint32_t)(uint16_t)x->noise);
			swsta_p16(b, 0x14, (uint32_t)(uint16_t)x->snr);
		}
		swsta_p16(b, 0x18, x->beacon);
		n = x->nrates > SWSTA_RATE_MAX ? SWSTA_RATE_MAX : x->nrates;
		b[0x22] = (uint8_t)n;
		for (i = 0; i < n; i++) swsta_p32(b, 0x24 + 4 * i, x->rates[i]);
		n = x->ie_len > SWSTA_IE_CAP ? SWSTA_IE_CAP : x->ie_len;
		SWSTA_MEMCPY(b + 0x8c, x->ie, n);
		swsta_p16(b, 0x8a, n);
	} else {
		swsta_p16(b, 0x8a, 0);
	}
}

/* Gap text per codec answer that is only partly decoded (<= 40 chars; emitted by swsta_a80211 for this codec only) */
static inline const char *swsta_cd_gap(uint32_t type, const struct swsta_view *v)
{
	switch (type) {
	case SWSTA_T_CARD_CAPS: return "get 12 caps bits open, zero-filled";
	case SWSTA_T_RSSI_BOUNDS: return "get 106 bounds values open, zeros";
	case SWSTA_T_AUTH_TYPE: return v && v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK ? 0 : "get 2 auth values open, zeros";	/* WPA2-PSK is answered (lower 1, upper 8); any other security stays zeros */
	case SWSTA_T_PROTMODE: return "get 6 protmode values open, zeros";
	case SWSTA_T_CHANNEL: return "get 4 channel flags open, zero";
	case SWSTA_T_RSSI: return "get 16 unit block open, zeros";
	case SWSTA_T_NOISE: return "get 17 noise value open, zero";
	case SWSTA_T_SCAN_RESULT: return v && v->bss && v->bss->ie_len ? 0 : "record IEs empty (no IEs known)";
	case SWSTA_T_CURRENT_NETWORK: return v && v->bss && v->bss->ie_len ? 0 : v && v->st.sec != SWSTA_SEC_OPEN ? "no IEs: Settings shows Weak Security" : "record IEs empty (no IEs known)";	/* privacy set + no IEs = IO80211 marks WEP [W-D3-weak-security s.1] */
	case SWSTA_T_HW_SUPP_CHANNELS: return "get 254: 7 scan channels, flags cloned";
	case SWSTA_T_SUPP_CHANNELS: return "get 27: 7 scan channels, flags cloned";
	case SWSTA_T_COUNTRY_CODE: return "get 51: the CLM country this boot set";
	default: return 0;
	}
}

static inline int swsta_cd_get(void *cx, uint32_t type, const struct swsta_view *v, void *buf, uint32_t len)
{
	uint8_t *b = (uint8_t *)buf;
	(void)cx;
	SWSTA_MEMSET(b, 0, len);	/* every byte the notes do not give is zero */
	switch (type) {
	case SWSTA_T_CARD_CAPS: return 0;				/* 13 B bitmap, bits 0..102 [RE-A L6]; no bit set (open) */
	case SWSTA_T_POWER:						/* 24 B {version 1, N radios 1, state} [RE-A L8] */
		swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, v->power_on ? 1u : 0u); return 0;
	case SWSTA_T_RSSI_BOUNDS: return 0;				/* 16 B, words +4/+8/+0xc published [RE-A L12]; values open */
	case SWSTA_T_CHANNEL:						/* 16 B, library reads +8 and +0xc, word0 pre-set to 1 [RE-A L3] */
		swsta_p32(b, 0, 1); swsta_p32(b, 8, v->st.channel); return 0;
	case SWSTA_T_AUTH_TYPE:						/* 16 B (+4 always, +8/+0xc if non-zero) [RE-A L2]. Joined with WPA2-PSK: lower 1, upper 8, the values IO80211 stores itself at the join [W-D3-weak-security s.6, obs-RE]; else zeros (gap) */
		if (v->st.joined && v->st.sec == SWSTA_SEC_WPA2_PSK) { swsta_p32(b, 0, 1); swsta_p32(b, 4, 1); swsta_p32(b, 8, 8); }
		return 0;
	case SWSTA_T_PROTMODE: return 0;				/* 12 B (+4,+8) [RE-A L4]; values open */
	case SWSTA_T_RSSI: case SWSTA_T_NOISE:				/* 52 B, success needs word +4 == 1, value at +8 [RE-A L7] */
		swsta_p32(b, 0, 1); swsta_p32(b, 4, 1);			/* word0 = 1: version [guess] */
		swsta_p32(b, 8, type == SWSTA_T_RSSI ? (uint32_t)v->st.rssi : 0u); return 0;
	case SWSTA_T_SCAN_RESULT:
		if (!v->bss) return SWSTA_EINVAL;
		swsta_rec(b, v->bss->ssid, v->bss->ssid_len, v->bss->bssid, v->bss->channel, v->bss->rssi, v->bss->sec, v->bss); return 0;
	case SWSTA_T_CURRENT_NETWORK:					/* same record as Get 11 [RE-B s.4] */
		swsta_rec(b, v->st.ssid, v->st.ssid_len, v->st.bssid, v->st.channel, v->st.rssi, v->st.sec, v->bss); return 0;	/* v->bss = the latest scan list's entry for this BSSID (swsta_a80211 looks it up), else NULL */
	case SWSTA_T_ASSOC_RESULT: { const char *why; swsta_p32(b, 0, swsta_assoc_code(v, &why)); return 0; }
	case SWSTA_T_SUPP_CHANNELS:				/* 2026-11-11: 27 has the same IO80211 case body and layout as 254 [W-UI-a80211-refused-types.md s.2 "254 (also 27, same case body 0x1d3ec45e4)"]; wifid read it 27x per boot and got EOPNOTSUPP (wd6-dhcp) */
	case SWSTA_T_HW_SUPP_CHANNELS: {			/* sprint 2026-11-08 [docs/research/W-UI-a80211-refused-types.md s.2 254, IO80211 0x1d3ec45e8-0x1d3ec46ec]: u32 count @+4, records from +8,
								 * stride 12, channel @rec+4, flags @rec+8. wifid's auto-join refuses an error or an empty list. The list is the channel set this driver
								 * really scans (the seven bring-up chanspecs of the L23 escan, sweet_wlan_g2e.h:201), not the radio's full set (no chanspecs iovar yet: GAP);
								 * flags 20 MHz 0x2 | 2 GHz 0x8 / 5 GHz 0x10 are the X-W2 s.1 clone values [guess, GAP] */
		static const uint8_t ch[7] = { 1, 6, 11, 36, 40, 44, 48 };
		uint32_t i;
		if (len < 8u + 12u * 7u) return SWSTA_EINVAL;
		swsta_p32(b, 4, 7u);
		for (i = 0; i < 7u; i++) {
			swsta_p32(b, 8u + 12u * i + 4u, ch[i]);
			swsta_p32(b, 8u + 12u * i + 8u, ch[i] <= 14u ? 0x2u | 0x8u : 0x2u | 0x10u);
		}
		return 0;
	}
	case SWSTA_T_COUNTRY_CODE:				/* 3 B NUL-terminated [row 51]: the country the L22c step of this boot set in the firmware (sweet_wlan_g2e.h:144 wl_country_t "XZ", printed `L22c country XZ set: status 0`), not a locale guess */
		if (len < 3u) return SWSTA_EINVAL;
		b[0] = 'X'; b[1] = 'Z'; b[2] = 0; return 0;
	default: return SWSTA_EOPNOTSUPP;	/* 207 and the rest: record layouts open or not needed [RE-A L9, L13; W-UI-a80211-refused-types.md] */
	}
}

/* Set 19 [RE-A / RE-B 4b]: {version, count N <= 4, N x s32 from +8}; state word 1 = on. Set 20: the 592-byte structure
 * of RE-B s.2: +0 version 1, +0xc ssid_len 1..32, +0x10 ssid, +0x30 bssid when byte +0x1cf bit 0x20, key block
 * +0x3c length, +0x40 cipher (10 passphrase / 6 PMK), +0x48 key bytes, +0x1d4/+0x1d8 library-derived PMK (unused). */
static inline int swsta_cd_set(void *cx, uint32_t type, const void *buf, uint32_t len, struct swsta_setreq *o)
{
	const uint8_t *b = (const uint8_t *)buf;
	uint32_t n, kl, kc;
	(void)cx;
	if (type == SWSTA_T_POWER) {
		if (len != 24) return SWSTA_EINVAL;
		n = swsta_g32(b, 4);
		if (n < 1 || n > 4) return SWSTA_EINVAL;
		o->power_on = swsta_g32(b, 8) == 1;
		return 0;
	}
	if (type != SWSTA_T_ASSOCIATE) return SWSTA_EOPNOTSUPP;
	if (len != 592 || swsta_g32(b, 0) != 1) return SWSTA_EINVAL;
	n = swsta_g32(b, 0x0c);
	if (n < 1 || n > 32) return SWSTA_EINVAL;
	o->join.ssid_len = (uint8_t)n; SWSTA_MEMCPY(o->join.ssid, b + 0x10, n);
	if (b[0x1cf] & 0x20) { SWSTA_MEMCPY(o->join.bssid, b + 0x30, 6); o->join.has_bssid = 1; }
	kl = swsta_g32(b, 0x3c); kc = swsta_g32(b, 0x40);
	if (kl == 0 && kc == 0) {
		o->join.key_kind = SWSTA_KEY_NONE;
	} else if ((uint32_t)(b[8] | (b[9] << 8)) != 0x8u) {
		return SWSTA_EOPNOTSUPP;	/* only WPA2-PSK (S+8 == 0x8) is modelled; FT/SHA256/SAE/802.1X refused loudly */
	} else if (kc == 10 && kl >= 8 && kl <= 64) {
		o->join.key_kind = SWSTA_KEY_PASSPHRASE; o->join.key_len = kl; SWSTA_MEMCPY(o->join.key, b + 0x48, kl);
	} else if (kc == 6 && kl == 32) {
		o->join.key_kind = SWSTA_KEY_PMK; o->join.key_len = kl; SWSTA_MEMCPY(o->join.key, b + 0x48, kl);
	} else return SWSTA_EINVAL;
	if (b[0x1d0] || b[0x202] || swsta_g32(b, 0x24c) || swsta_g32(b, 0x248)) o->gap = "set 20 flags/blacklist/band ignored";
	return 0;
}

static const struct swsta_codec swsta_codec_167 = { swsta_cd_get, swsta_cd_set, 0 };

static inline void swsta_log(struct swsta *s, const char *line) { if (s->cb.log) s->cb.log(s->cb.cx, line); }

static inline void swsta_gap(struct swsta *s, uint32_t type, int set, uint32_t len, const char *why)
{
	char t[64];
	s->gaps++;
	if (why) SWSTA_SNPRINTF(t, sizeof t, "a80211 type %u %s", (unsigned)type, why);
	else SWSTA_SNPRINTF(t, sizeof t, "a80211 type %u %s len %u not handled", (unsigned)type, set ? "set" : "get", (unsigned)len);
	if (s->cb.gap) s->cb.gap(s->cb.cx, t);
	else swsta_log(s, t);	/* no gap callback: still loud (rule 8) */
}

static inline void swsta_post(struct swsta *s, uint32_t code) { if (s->cb.post) s->cb.post(s->cb.cx, code); }

/* W-UI: LINK_CHANGED (4) with its 0x20-byte payload: u8 isLinkDown @+0, s32 RSSI @+4, u16 SNR @+8 and NOISE @+0xa (0 = key omitted), debounce bytes @+0x1c/+0x1d (0);
 * IO80211 __dispatchLinkChangedEvent drops a payload shorter than 0x20 and wifid needs isLinkDown + RSSI [obs-RE docs/research/W-UI-wifid-current-network.md row 3, 0x1d3eda824, 0x10005bc80/0x10005bc94].
 * Link state and RSSI come from the radio's status (the kept link: the firmware's values). SNR/NOISE are not known: 0, with a GAP. */
static inline void swsta_post_link(struct swsta *s)
{
	struct swsta_status st;
	uint8_t p[0x20];
	SWSTA_MEMSET(&st, 0, sizeof st);
	SWSTA_MEMSET(p, 0, sizeof p);
	if (s->radio && s->radio->status) s->radio->status(s->radio->ctx, &st);
	p[0] = st.joined ? 0u : 1u;
	swsta_p32(p, 4, (uint32_t)st.rssi);
	if (s->cb.post_data) {
		s->cb.post_data(s->cb.cx, SWSTA_EV_LINK_CHANGED, p, sizeof p);
		if (st.joined && !s->snr_gapped) { s->snr_gapped = 1u; swsta_gap(s, SWSTA_EV_LINK_CHANGED, 0, 0, "link event SNR/NOISE unknown, sent as 0"); }	/* once per station */
	} else {
		swsta_gap(s, SWSTA_EV_LINK_CHANGED, 0, 0, "link event without payload");
		swsta_post(s, SWSTA_EV_LINK_CHANGED);
	}
	SWSTA_MEMSET(&st, 0, sizeof st);
}

/* radio -> station events (installed as radio->up) */
static inline void swsta_radio_up(void *cx, const struct swsta_rev *ev)
{
	struct swsta *s = (struct swsta *)cx;
	switch (ev->kind) {
	case SWSTA_REV_SCAN_DONE: swsta_post(s, SWSTA_EV_SCAN_DONE); break;
	case SWSTA_REV_ASSOC_DONE:
		s->assoc_result = (uint32_t)ev->result; s->assoc_state = SWSTA_AS_DONE;
		swsta_post(s, SWSTA_EV_ASSOC_DONE);
		if (ev->result == 0) {	/* 9, then 2/3/4 [src 110:198] */
			swsta_post(s, SWSTA_EV_SSID_CHANGED); swsta_post(s, SWSTA_EV_BSSID_CHANGED); swsta_post_link(s);
		}
		break;
	case SWSTA_REV_LINK_UP: case SWSTA_REV_LINK_DOWN: swsta_post_link(s); break;
	default: {
		char t[64];
		SWSTA_SNPRINTF(t, sizeof t, "a80211 radio event %d unknown", ev->kind);
		s->gaps++;
		if (s->cb.gap) s->cb.gap(s->cb.cx, t);
	}
	}
}

static inline void swsta_init(struct swsta *s, struct swsta_radio *r, const struct swsta_codec *codec, const struct swsta_cb *cb)
{
	SWSTA_MEMSET(s, 0, sizeof *s);
	s->radio = r; s->codec = codec ? codec : &swsta_codec_167; s->cb = *cb;	/* no codec given: the 16.7 one */
	r->up = swsta_radio_up; r->up_ctx = s;
	{ int st = 0; if (r->power && r->power(r->ctx, SWSTA_PWR_GET, &st) == 0) s->power_on = st; }
}

/* Set 383 observer (LOWCLK B3, patch 0363). b / len = the request body iOS handed us (b NULL or len < 12 = an unexpected shape). Reads only inside [b, b + len); the first `<key>` ... `</key>` of the L bytes
 * the length word names is the key (at most 64 bytes between the tags, 47 kept, non-printable bytes shown as '?'). Prints the first sight of a key and the counts 10, 100, 1000, 10000; an unexpected shape is
 * ONE GAP line (the first 16 bytes as four hex words) and then only counted; a full table counts the new key in p383_over with one GAP line. Never a value, never a return value: observation only. */
static inline uint32_t swsta_p383_find(const uint8_t *t, uint32_t n, const char *tag, uint32_t tl, uint32_t from, uint32_t span)
{
	uint32_t i, k;
	for (i = from; i + tl <= n && i - from <= span; i++) {
		for (k = 0; k < tl && t[i + k] == (uint8_t)tag[k]; k++) { }
		if (k == tl) return i;
	}
	return 0xFFFFFFFFu;
}

static inline void swsta_p383_bad(struct swsta *s, const uint8_t *b, uint32_t len)
{
	char line[96]; uint32_t w[4], i, k;
	if (s->p383_shape++) return;	/* one GAP per boot, the rest is counted (summary) */
	for (i = 0; i < 4; i++) { w[i] = 0; for (k = 0; k < 4; k++) if (b && 4 * i + k < len) w[i] |= (uint32_t)b[4 * i + k] << (8 * k); }
	SWSTA_SNPRINTF(line, sizeof line, "GAP set383 shape unexpected: len %u first bytes %08x %08x %08x %08x", (unsigned)len, (unsigned)w[0], (unsigned)w[1], (unsigned)w[2], (unsigned)w[3]);
	SWSTA_PROBE_LOG(line);
}

static inline void swsta_p383(struct swsta *s, const uint8_t *b, uint32_t len)
{
	char line[128], name[SWSTA_P383_KEY]; uint32_t l1, n, ks, ke, i, k;
	struct swsta_p383 *e = 0;
	if (!b || len < 12 || swsta_g32(b, 0) != 1u) { swsta_p383_bad(s, b, len); return; }
	l1 = swsta_g32(b, 4);
	if (l1 < 1u || l1 > 0x1000u || l1 > len - 8u) { swsta_p383_bad(s, b, len); return; }
	n = l1 - 1u;	/* the plist text bytes; the NUL is the last counted byte */
	ks = swsta_p383_find(b + 8, n, "<key>", 5, 0, n);
	if (ks == 0xFFFFFFFFu) { swsta_p383_bad(s, b, len); return; }
	ks += 5u;
	ke = swsta_p383_find(b + 8, n, "</key>", 6, ks, 64u);
	if (ke == 0xFFFFFFFFu || ke == ks) { swsta_p383_bad(s, b, len); return; }
	for (i = 0; ks + i < ke && i + 1u < SWSTA_P383_KEY; i++) { uint8_t c = b[8 + ks + i]; name[i] = (c >= 0x20 && c < 0x7f) ? (char)c : '?'; }
	name[i] = 0;
	for (k = 0; k < s->p383_used; k++) if (!SWSTA_MEMCMP(s->p383[k].key, name, i + 1)) { e = &s->p383[k]; break; }
	if (!e) {
		if (s->p383_used >= SWSTA_P383_MAX) {
			if (!s->p383_over++) { SWSTA_SNPRINTF(line, sizeof line, "GAP set383 key table full (%u distinct keys): new keys are only counted", (unsigned)SWSTA_P383_MAX); SWSTA_PROBE_LOG(line); }
			return;
		}
		e = &s->p383[s->p383_used++];
		SWSTA_MEMCPY(e->key, name, i + 1); e->len = n; e->n = 0;
	}
	e->n++;
	if (e->n == 1u || e->n == 10u || e->n == 100u || e->n == 1000u || e->n == 10000u) {
		SWSTA_SNPRINTF(line, sizeof line, "set383 key=%s len=%u count=%u%s", e->key, (unsigned)n, (unsigned)e->n, e->n == 1u ? " (first seen)" : "");
		SWSTA_PROBE_LOG(line);
	}
}

/* Answer one SIOCGA80211 (set=0) / SIOCSA80211 (set=1). *val is req_val (unused so far: its convention is open).
 * Returns 0 or a positive errno. Never logs contents. */
static inline int swsta_a80211(struct swsta *s, int set, uint32_t type, uint32_t *val, void *buf, uint32_t len)
{
	struct swsta_cnt *c = (type >= 1 && type <= SWSTA_MAXTYPE) ? &s->cnt[type] : &s->oob;
	uint32_t want = swsta_len(set, type);
	struct swsta_status st; struct swsta_view v; struct swsta_setreq rq; struct swsta_bss bss;
	int rc = 0, handled = 1;
	char line[96];
	s->requests++;
	if (set) c->set++; else c->get++;
	if (s->probe && set && type == SWSTA_T_SET_PROPERTY) swsta_p383(s, (const uint8_t *)buf, len);	/* LOWCLK B3: observation only, the request is answered as before */
	SWSTA_MEMSET(&st, 0, sizeof st);
	SWSTA_MEMSET(&v, 0, sizeof v);

	if (set && type == SWSTA_T_DISASSOCIATE && (len == 0 || (len == 8 && buf))) {
		rc = s->radio->leave(s->radio->ctx);
		if (len) swsta_gap(s, type, 1, len, "set 22 disassoc reason ignored");	/* s32 reason at +4 [RE-B 4b] */
	} else if (!set && type == SWSTA_T_ASSOC_RESULT) {
		/* 4-byte class: req_len 0, req_data NULL, the answer is req_val [RE-A R1, RE-B s.3] */
		uint8_t w[4]; const char *why = 0;
		if (len != 0 || !val || !s->codec || !s->codec->get) { handled = 0; rc = SWSTA_EOPNOTSUPP; }
		else {
			v.power_on = s->power_on; v.assoc_result = s->assoc_result; v.assoc_state = s->assoc_state;
			rc = s->codec->get(s->codec->cx, type, &v, w, 4);
			if (rc == 0) {
				*val = swsta_g32(w, 0);
				if (s->codec == &swsta_codec_167) { (void)swsta_assoc_code(&v, &why); if (why) swsta_gap(s, type, 0, len, why); }
			}
		}
	} else if (want == 0 || len != want || (len && !buf)) {
		handled = 0; rc = SWSTA_EOPNOTSUPP;
	} else if (set) {
		if (type == SWSTA_T_SCAN_REQ) {
			swsta_gap(s, type, 1, len, "set scan req body ignored (layout open)");
			rc = s->radio->scan_start(s->radio->ctx);
		} else if (!s->codec || !s->codec->set) {
			handled = 0; rc = SWSTA_EOPNOTSUPP;
		} else {
			SWSTA_MEMSET(&rq, 0, sizeof rq);
			rc = s->codec->set(s->codec->cx, type, buf, len, &rq);
			if (rc == 0 && rq.gap) swsta_gap(s, type, 1, len, rq.gap);
			if (rc == 0 && type == SWSTA_T_POWER) {
				int now = 0;
				rc = s->radio->power(s->radio->ctx, rq.power_on ? SWSTA_PWR_ON : SWSTA_PWR_OFF, &now);
				if (rc == 0) {
					int changed = (now != s->power_on);
					s->power_on = now;
					if (changed) swsta_post(s, SWSTA_EV_POWER_CHANGED);
				}
			} else if (rc == 0 && type == SWSTA_T_ASSOCIATE) {
				s->assoc_state = SWSTA_AS_PENDING;	/* before join(): the radio may complete inside the verb */
				rc = s->radio->join(s->radio->ctx, &rq.join);
				if (rc != 0) s->assoc_state = SWSTA_AS_NONE;
			}
			SWSTA_MEMSET(&rq, 0, sizeof rq);	/* wipe the copy of the key */
		}
	} else {
		/* Get */
		int joined_needed = (type == SWSTA_T_SSID || type == SWSTA_T_BSSID || type == SWSTA_T_CHANNEL ||
				     type == SWSTA_T_RSSI || type == SWSTA_T_NOISE || type == SWSTA_T_AUTH_TYPE ||
				     type == SWSTA_T_PROTMODE || type == SWSTA_T_CURRENT_NETWORK);
		s->radio->status(s->radio->ctx, &st);
		v.power_on = s->power_on; v.st = st; v.assoc_result = s->assoc_result; v.assoc_state = s->assoc_state;
		if (joined_needed && !st.joined) {
			rc = SWSTA_ENXIO;	/* soft path of the library [RE-A R2, R5] */
		} else if (type == SWSTA_T_SSID) {
			SWSTA_MEMSET(buf, 0, len);	/* 32 plain bytes [src 110:152] */
			SWSTA_MEMCPY(buf, st.ssid, st.ssid_len > 32 ? 32 : st.ssid_len);
		} else if (type == SWSTA_T_BSSID) {
			SWSTA_MEMCPY(buf, st.bssid, 6);	/* 6 bytes [src 110:153] */
		} else if (type == SWSTA_T_SCAN_RESULT) {
			if (s->radio->bss_next(s->radio->ctx, &bss) == 1) {
				v.bss = &bss;
				if (!s->codec || !s->codec->get) { handled = 0; rc = SWSTA_EOPNOTSUPP; }
				else rc = s->codec->get(s->codec->cx, type, &v, buf, len);
				if (rc == 0 && s->codec == &swsta_codec_167 && swsta_cd_gap(type, &v)) swsta_gap(s, type, 0, len, swsta_cd_gap(type, &v));
			} else rc = SWSTA_E_SCAN_END;	/* end of list: EIO ends the loop normally [RE-A S3] */
		} else if (!s->codec || !s->codec->get) {
			if (type == SWSTA_T_CARD_CAPS) {	/* bind probe must succeed [src 110:61] */
				SWSTA_MEMSET(buf, 0, len);
				swsta_gap(s, type, 0, len, "get 12 caps bits open, zero-filled");
			} else { handled = 0; rc = SWSTA_EOPNOTSUPP; }
		} else {
			if (type == SWSTA_T_CURRENT_NETWORK && s->radio->bss_find && s->radio->bss_find(s->radio->ctx, st.bssid, &bss) == 1) v.bss = &bss;	/* the scan list's entry of the joined AP: its IEs / rates / beacon / flags fill the record (ssid, channel, rssi stay the link's) */
			rc = s->codec->get(s->codec->cx, type, &v, buf, len);
			if (rc == 0 && s->codec == &swsta_codec_167 && swsta_cd_gap(type, &v)) swsta_gap(s, type, 0, len, swsta_cd_gap(type, &v));
		}
	}

	if (rc == (int)SWSTA_EOPNOTSUPP) handled = 0;	/* a codec refusing a type is a gap too (rule 8) */
	/* W-UI: wifid starts its event monitors in WiFiDeviceScheduleWithQueue, AFTER WiFiDeviceOpen (whose last request is Get 207) and right before its own Get 103
	 * (0x10004c13c-0x10004c524 monitors, 0x10004c864 CopyCurrentNetwork) [obs-RE W-UI-wifid-current-network.md]: a link event posted before that is never read.
	 * So the first successful Get 103 after a Get 207 re-posts the current link state once (the link is up: a state report, not a new association). */
	if (!set && type == SWSTA_T_CHANNELS_INFO) s->relink_armed = 1u;
	if (!set && type == SWSTA_T_CURRENT_NETWORK && rc == 0 && s->relink_armed) {
		s->relink_armed = 0u; s->relinks++;
		SWSTA_SNPRINTF(line, sizeof line, "a80211 link state re-posted after the device open (LINK_CHANGED, %u)", (unsigned)s->relinks);
		swsta_log(s, line);
		swsta_post_link(s);
	}
	if (!handled) { c->gap++; swsta_gap(s, type, set, len, 0); }
	if (rc == 0) c->ok++; else if (rc == SWSTA_ENXIO || rc == SWSTA_EOPNOTSUPP || rc == SWSTA_E_SCAN_END) c->soft++;
	SWSTA_SNPRINTF(line, sizeof line, "a80211 req type=%u %s len=%u -> %d", (unsigned)type, set ? "set" : "get", (unsigned)len, rc);
	swsta_log(s, line);
	SWSTA_MEMSET(&bss, 0, sizeof bss); SWSTA_MEMSET(&v, 0, sizeof v);
	return rc;
}

static inline void swsta_summary(struct swsta *s)
{
	char line[96]; uint32_t t;
	for (t = 0; t <= SWSTA_MAXTYPE + 1; t++) {
		const struct swsta_cnt *c = t <= SWSTA_MAXTYPE ? &s->cnt[t] : &s->oob;
		if (!c->get && !c->set) continue;
		SWSTA_SNPRINTF(line, sizeof line, "a80211 sum type=%s%u get=%u set=%u ok=%u soft=%u gap=%u", t > SWSTA_MAXTYPE ? "oob" : "",
			       (unsigned)(t > SWSTA_MAXTYPE ? 0 : t), (unsigned)c->get, (unsigned)c->set, (unsigned)c->ok, (unsigned)c->soft, (unsigned)c->gap);
		swsta_log(s, line);
	}
	SWSTA_SNPRINTF(line, sizeof line, "a80211 summary requests=%u gaps=%u", (unsigned)s->requests, (unsigned)s->gaps);
	swsta_log(s, line);
	if (s->probe) {	/* LOWCLK B3 (patch 0363): the Set 383 keys seen, one line each, then the overflow and shape counts */
		for (t = 0; t < s->p383_used; t++) {
			SWSTA_SNPRINTF(line, sizeof line, "set383 summary key=%s n=%u len=%u", s->p383[t].key, (unsigned)s->p383[t].n, (unsigned)s->p383[t].len);
			SWSTA_PROBE_LOG(line);
		}
		SWSTA_SNPRINTF(line, sizeof line, "set383 summary keys=%u table_full_new=%u shape_unexpected=%u", (unsigned)s->p383_used, (unsigned)s->p383_over, (unsigned)s->p383_shape);
		SWSTA_PROBE_LOG(line);
	}
}

#endif /* SWEET_WLAN_STA_H */
