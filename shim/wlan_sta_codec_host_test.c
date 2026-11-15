/* wlan_sta_codec_host_test.c - host test of the 16.7 Apple80211 codec in shim/sweet_wlan_sta.h (T-853c, W-D7).
 * Own code. Every CHECK names the note row it proves: [A Ln/Rn/Sn] = docs/research/W-D7-a80211-get-layouts.md,
 * [B ...] = docs/research/W-D7-a80211-join-layouts.md. The radio is a fake; nothing here ran on hardware. */
#include <stdio.h>
#include <string.h>
#include "sweet_wlan_sta.h"

static int fails;
#define CHECK(c) do { if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

#define PASS_TXT "codecPass-77aa"
static char logbuf[32768], gapbuf[8192];
static uint32_t kev[64]; static int nkev;
static void t_log(void *cx, const char *l) { (void)cx; strncat(logbuf, l, sizeof logbuf - strlen(logbuf) - 2); strcat(logbuf, "\n"); }
static void t_gap(void *cx, const char *t) { (void)cx; CHECK(strlen(t) <= 63); strncat(gapbuf, t, sizeof gapbuf - strlen(gapbuf) - 2); strcat(gapbuf, "\n"); t_log(cx, t); }
static void t_post(void *cx, uint32_t c) { (void)cx; if (nkev < 64) kev[nkev++] = c; }

struct fake { int on, joined, cursor, defer; struct swsta_bss bss[3]; int cur; struct swsta_join last; int njoin, nleave; };
static struct fake fk; static struct swsta_radio rad;
static void fk_ev(int kind, int result, int reason) { struct swsta_rev e = { kind, result, reason }; rad.up(rad.up_ctx, &e); }
static int f_power(void *r, int op, int *st) { struct fake *f = r; if (op == SWSTA_PWR_ON) f->on = 1; else if (op == SWSTA_PWR_OFF) { f->on = 0; f->joined = 0; } *st = f->on; return 0; }
static int f_scan(void *r) { struct fake *f = r; f->cursor = 0; fk_ev(SWSTA_REV_SCAN_DONE, 0, 0); return 0; }
static int f_next(void *r, struct swsta_bss *o) { struct fake *f = r; if (f->cursor >= 3) return 0; *o = f->bss[f->cursor++]; return 1; }
static int f_join(void *r, const struct swsta_join *j)
{
	struct fake *f = r; int i;
	f->last = *j; f->njoin++;
	if (f->defer) return 0;
	for (i = 0; i < 3; i++) if (f->bss[i].ssid_len == j->ssid_len && !memcmp(f->bss[i].ssid, j->ssid, j->ssid_len)) {
		f->joined = 1; f->cur = i; fk_ev(SWSTA_REV_ASSOC_DONE, 0, 0); return 0; }
	fk_ev(SWSTA_REV_ASSOC_DONE, 9, 0); return 0;
}
static int f_leave(void *r) { struct fake *f = r; f->nleave++; if (f->joined) { f->joined = 0; fk_ev(SWSTA_REV_LINK_DOWN, 0, 3); } return 0; }
static int f_status(void *r, struct swsta_status *o)
{
	struct fake *f = r; memset(o, 0, sizeof *o);
	if (!f->joined) return 0;
	o->joined = 1; o->sec = f->bss[f->cur].sec; memcpy(o->ssid, f->bss[f->cur].ssid, 32); o->ssid_len = f->bss[f->cur].ssid_len;
	memcpy(o->bssid, f->bss[f->cur].bssid, 6); o->channel = f->bss[f->cur].channel; o->rssi = f->bss[f->cur].rssi; return 0;
}
static int f_find(void *r, const uint8_t bssid[6], struct swsta_bss *o) { struct fake *f = r; int i; for (i = 0; i < 3; i++) if (!memcmp(f->bss[i].bssid, bssid, 6)) { *o = f->bss[i]; return 1; } return 0; }
static void fk_setup(void)
{
	static const char *nm[3] = { "FakeNet-A", "FakeNet-B", "FakeNet-C" };
	int i; memset(&fk, 0, sizeof fk);
	for (i = 0; i < 3; i++) {
		fk.bss[i].ssid_len = (uint8_t)strlen(nm[i]); memcpy(fk.bss[i].ssid, nm[i], fk.bss[i].ssid_len);
		memset(fk.bss[i].bssid, 0x10 * (i + 1), 6); fk.bss[i].channel = 1 + 5 * i; fk.bss[i].rssi = -40 - 10 * i;
		fk.bss[i].sec = i == 2 ? SWSTA_SEC_OPEN : SWSTA_SEC_WPA2_PSK;
	}
	/* 2026-11-11 scan records: bss 1 carries the firmware extras (the others none: the old empty record) */
	fk.bss[1].chflags = 0x12; fk.bss[1].beacon = 100; fk.bss[1].noise = -90; fk.bss[1].snr = 40;
	fk.bss[1].nrates = 4; fk.bss[1].rates[0] = 1; fk.bss[1].rates[1] = 2; fk.bss[1].rates[2] = 5; fk.bss[1].rates[3] = 11;
	/* bss 2 (open) has more than the record can hold: 17 rates (15 fit, the ssid_len at +0x60 must stay), 600 IE bytes (512 are the radio's cap) */
	{ int k; fk.bss[2].nrates = 17; for (k = 0; k < 15; k++) fk.bss[2].rates[k] = (uint8_t)(k + 1); fk.bss[2].ie_len = 600; }
	fk.bss[1].ie_len = 8; memcpy(fk.bss[1].ie, "\x00\x03""abc""\x03\x01\x24", 8);
	memset(&rad, 0, sizeof rad);
	rad.power = f_power; rad.scan_start = f_scan; rad.bss_next = f_next; rad.bss_find = f_find; rad.join = f_join; rad.leave = f_leave; rad.status = f_status; rad.ctx = &fk;
}

static uint32_t w32(const uint8_t *b, uint32_t o) { return swsta_g32(b, o); }
static int allzero(const uint8_t *b, uint32_t from, uint32_t to) { uint32_t i; for (i = from; i < to; i++) if (b[i]) return 0; return 1; }
static int gaps_has(const char *t) { return strstr(gapbuf, t) != 0; }

/* the ASSOCIATE structure of [B s.2] */
static void mk_assoc(uint8_t *s, const char *ssid, uint32_t upper, uint32_t kc, const void *key, uint32_t kl)
{
	memset(s, 0, 592);
	swsta_p32(s, 0, 1); swsta_p16(s, 4, 2); swsta_p16(s, 6, 1); swsta_p16(s, 8, upper);
	swsta_p32(s, 0x0c, (uint32_t)strlen(ssid)); memcpy(s + 0x10, ssid, strlen(ssid));
	swsta_p32(s, 0x3c, kl); swsta_p32(s, 0x40, kc); if (kl) memcpy(s + 0x48, key, kl > 64 ? 64 : kl);
}

static void t_post_data(void *cx, uint32_t code, const void *d, uint32_t n) { (void)d; (void)n; t_post(cx, code); }	/* W-UI: payload events count like the others */

int main(void)
{
	static struct swsta s; struct swsta_cb cb = { t_log, t_gap, t_post, t_post_data, 0 };
	static uint8_t buf[10016], a[592]; uint32_t val = 0, g0; int n, rc; uint8_t pmk[32];

	memset(pmk, 0xab, sizeof pmk);
	fk_setup(); swsta_init(&s, &rad, 0, &cb);
	CHECK(s.codec == &swsta_codec_167);	/* no codec given -> the 16.7 codec */
	CHECK(SWSTA_E_SCAN_END == 5u && SWSTA_ENXIO == 6u && SWSTA_EOPNOTSUPP == 0x66u);	/* [A S3, R2, R5] */
	CHECK(swsta_len(0, 254) == 4824 && swsta_len(0, 27) == 4824 && swsta_len(0, 207) == 10016);	/* [A L9, L13] */

	/* 12 CARD_CAPABILITIES: 13 bytes, no bit set, result 0, gap line [A L6] */
	memset(buf, 0x55, 13); g0 = s.gaps;
	CHECK(swsta_a80211(&s, 0, 12, &val, buf, 13) == 0 && allzero(buf, 0, 13) && s.gaps == g0 + 1);
	CHECK(swsta_a80211(&s, 0, 12, &val, buf, 12) == (int)SWSTA_EOPNOTSUPP);	/* 12 bytes is wrong for this library [A L6] */

	/* 19 POWER: {1, 1, state} then zeros; Set 19 parses {version, N, state@+8} [A L8, B 4b] */
	CHECK(swsta_a80211(&s, 0, 19, &val, buf, 24) == 0);
	CHECK(w32(buf, 0) == 1 && w32(buf, 4) == 1 && w32(buf, 8) == 0 && allzero(buf, 12, 24));
	memset(a, 0, 24); swsta_p32(a, 0, 1); swsta_p32(a, 4, 1); swsta_p32(a, 8, 1); nkev = 0;
	CHECK(swsta_a80211(&s, 1, 19, &val, a, 24) == 0 && nkev == 1 && kev[0] == SWSTA_EV_POWER_CHANGED);
	CHECK(swsta_a80211(&s, 0, 19, &val, buf, 24) == 0 && w32(buf, 8) == 1 && w32(buf, 4) == 1);
	swsta_p32(a, 8, 0);
	CHECK(swsta_a80211(&s, 1, 19, &val, a, 24) == 0 && nkev == 2 && swsta_a80211(&s, 0, 19, &val, buf, 24) == 0 && w32(buf, 8) == 0);
	swsta_p32(a, 4, 0); CHECK(swsta_a80211(&s, 1, 19, &val, a, 24) == (int)SWSTA_EINVAL);	/* N = 0 */
	swsta_p32(a, 4, 5); CHECK(swsta_a80211(&s, 1, 19, &val, a, 24) == (int)SWSTA_EINVAL);	/* N > 4 */
	swsta_p32(a, 4, 1); swsta_p32(a, 8, 1); CHECK(swsta_a80211(&s, 1, 19, &val, a, 24) == 0 && fk.on == 1);

	/* 106 RSSI_BOUNDS: 16 bytes, result 0, values open (zeros + gap) [A L12] */
	memset(buf, 0x55, 16); g0 = s.gaps;
	CHECK(swsta_a80211(&s, 0, 106, &val, buf, 16) == 0 && allzero(buf, 0, 16) && s.gaps == g0 + 1);

	/* 207: layout open -> soft EOPNOTSUPP + gap line; 27 (2026-11-11): the 254 list, same layout [refused-types s.2] [A L9, L13] */
	g0 = s.gaps;
	memset(buf, 0xEE, 4824);
	CHECK(swsta_a80211(&s, 0, 27, &val, buf, 4824) == 0 && buf[4] == 7u && buf[8 + 4] == 1u && buf[8 + 12 * 3 + 4] == 36u && buf[8 + 12 * 6 + 4] == 48u && buf[8 + 12 * 3 + 8] == 0x12u && gaps_has("get 27: 7 scan channels, flags cloned"));
	CHECK(swsta_a80211(&s, 0, 27, &val, buf, 8u + 12u * 7u - 1u) != 0);	/* too short for 7 records: refused */
	CHECK(swsta_a80211(&s, 0, 207, &val, buf, 10016) == (int)SWSTA_EOPNOTSUPP);
	CHECK(s.gaps == g0 + 3 && gaps_has("a80211 type 27 get len 91 not handled") && gaps_has("a80211 type 207 get len 10016 not handled"));
	/* sprint 2026-11-08: 254 answers the driver's 7 scan channels (u32 count @+4, records stride 12 from +8, channel @+4, flags @+8) with a GAP for the cloned flags */
	g0 = s.gaps;
	CHECK(swsta_a80211(&s, 0, 254, &val, buf, 4824) == 0 && buf[4] == 7u && buf[8 + 12 * 3 + 4] == 36u && s.gaps == g0 + 1 && gaps_has("get 254: 7 scan channels, flags cloned"));

	/* not joined: every joined-only Get is ENXIO (soft), no gap [A R5] */
	g0 = s.gaps;
	CHECK(swsta_a80211(&s, 0, 1, &val, buf, 32) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 2, &val, buf, 16) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 4, &val, buf, 16) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 6, &val, buf, 12) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 9, &val, buf, 6) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 16, &val, buf, 52) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 17, &val, buf, 52) == (int)SWSTA_ENXIO);
	CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == (int)SWSTA_ENXIO);
	CHECK(s.gaps == g0);

	/* 21 before any join: 0 in req_val, req_len 0 [B s.3, A R1] */
	val = 99; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 0);
	val = 99; CHECK(swsta_a80211(&s, 0, 21, &val, buf, 4) == (int)SWSTA_EOPNOTSUPP);	/* the library sends len 0 */

	/* scan: Set 10 body ignored (gap), Get 11 record per [B s.4] until EIO [A S3] */
	nkev = 0; CHECK(swsta_a80211(&s, 1, 10, &val, buf, 5456) == 0 && nkev == 1 && kev[0] == SWSTA_EV_SCAN_DONE);
	for (n = 0; n < 4; n++) {
		const struct swsta_bss *b = &fk.bss[n < 3 ? n : 0];
		memset(buf, 0, 1164); swsta_p16(buf, 0x8a, 0x400);	/* the library pre-sets the IE capacity */
		rc = swsta_a80211(&s, 0, 11, &val, buf, 1164);
		if (n == 3) { CHECK(rc == (int)SWSTA_E_SCAN_END); break; }
		CHECK(rc == 0);
		CHECK(w32(buf, 0x08) == b->channel && w32(buf, 0x0c) == b->chflags);		/* CHANNEL, CHANNEL_FLAGS (0 when unknown) */
		CHECK(buf[0x10] == (n == 1 ? 1 : 0) && buf[0x11] == 0);	/* noise/snr gate: on only when the radio knew one */
		CHECK((int16_t)(buf[0x12] | (buf[0x13] << 8)) == b->noise && (int16_t)(buf[0x14] | (buf[0x15] << 8)) == b->snr && (buf[0x18] | (buf[0x19] << 8)) == b->beacon);
		CHECK((int16_t)(buf[0x16] | (buf[0x17] << 8)) == b->rssi);			/* RSSI s16 */
		CHECK((buf[0x1a] | (buf[0x1b] << 8)) == (b->sec == SWSTA_SEC_OPEN ? 0x0001 : 0x0011));	/* CAPABILITIES: ESS (+ privacy) */
		CHECK(!memcmp(buf + 0x1c, b->bssid, 6));					/* BSSID */
		CHECK(buf[0x22] == (b->nrates > 15u ? 15u : b->nrates));				/* rate count, at most the 15 the record holds */
		CHECK(n != 2 || (w32(buf, 0x24) == 1 && w32(buf, 0x24 + 4 * 14) == 15 && buf[0x60] == b->ssid_len));
		CHECK(n != 1 || (w32(buf, 0x24) == 1 && w32(buf, 0x28) == 2 && w32(buf, 0x2c) == 5 && w32(buf, 0x30) == 11 && w32(buf, 0x34) == 0));	/* rates, u32 Mbps */
		CHECK(buf[0x60] == b->ssid_len && !memcmp(buf + 0x61, b->ssid, b->ssid_len) && buf[0x61 + b->ssid_len] == 0);	/* SSID */
		CHECK(w32(buf, 0x84) == 0 && buf[0x88] == 0 && (uint32_t)(buf[0x8a] | (buf[0x8b] << 8)) == (b->ie_len > SWSTA_IE_CAP ? SWSTA_IE_CAP : b->ie_len));	/* AGE, flags, IE length (written over the caller's 0x400; at most the radio's cap) */
		CHECK(!memcmp(buf + 0x8c, b->ie, b->ie_len > SWSTA_IE_CAP ? SWSTA_IE_CAP : b->ie_len) && allzero(buf, 0x8c + (b->ie_len > SWSTA_IE_CAP ? SWSTA_IE_CAP : b->ie_len), 1164));	/* the IEs, nothing behind them */
	}
	CHECK(gaps_has("a80211 type 11 record IEs empty (no IEs known)"));

	/* Set 20 ASSOCIATE per [B s.2]: WPA2-PSK passphrase (cipher 10) */
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, (uint32_t)strlen(PASS_TXT));
	a[0x1cf] = 0x20; memset(a + 0x30, 0x20, 6);
	nkev = 0; g0 = s.gaps;
	CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0);
	CHECK(fk.last.ssid_len == 9 && !memcmp(fk.last.ssid, "FakeNet-B", 9) && fk.last.has_bssid && fk.last.bssid[0] == 0x20 && fk.last.bssid[5] == 0x20);
	CHECK(fk.last.key_kind == SWSTA_KEY_PASSPHRASE && fk.last.key_len == strlen(PASS_TXT) && !memcmp(fk.last.key, PASS_TXT, strlen(PASS_TXT)));
	CHECK(nkev == 4 && kev[0] == SWSTA_EV_ASSOC_DONE && kev[3] == SWSTA_EV_LINK_CHANGED && s.gaps == g0 + 1);	/* all fields cited; the one gap is the first link event's SNR/NOISE (W-UI) */
	/* Set 20: PMK (cipher 6, 32 bytes), no BSSID flag */
	mk_assoc(a, "FakeNet-A", 0x8, 6, pmk, 32);
	CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0 && fk.last.key_kind == SWSTA_KEY_PMK && fk.last.key_len == 32 && !memcmp(fk.last.key, pmk, 32) && !fk.last.has_bssid);
	/* Set 20: open (no key) */
	mk_assoc(a, "FakeNet-C", 0, 0, 0, 0);
	CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0 && fk.last.key_kind == SWSTA_KEY_NONE && fk.last.key_len == 0);
	/* refusals: version, ssid_len, passphrase length, unsupported AKM (loud), cipher */
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); swsta_p32(a, 0, 2); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EINVAL);
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); swsta_p32(a, 0x0c, 0); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EINVAL);
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); swsta_p32(a, 0x0c, 33); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EINVAL);
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 7); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EINVAL);
	mk_assoc(a, "FakeNet-B", 0x8, 7, PASS_TXT, 14); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EINVAL);
	mk_assoc(a, "FakeNet-B", 0x1000, 6, pmk, 32); n = fk.njoin;	/* SAE is not modelled: refused, gap, radio untouched */
	CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == (int)SWSTA_EOPNOTSUPP && fk.njoin == n && gaps_has("a80211 type 20 set len 592 not handled"));
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); a[0x1d0] = 0x08; g0 = s.gaps;	/* private MAC flag: ignored but reported */
	CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0 && s.gaps >= g0 + 1 && gaps_has("set 20 flags/blacklist/band ignored"));

	/* joined to FakeNet-B (idx 1, ch 6, rssi -50, secured) */
	mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0);
	CHECK(fk.joined && fk.cur == 1);
	/* 21 after success: 1 in req_val [B s.3] */
	val = 0; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 1);
	/* 1, 9 */
	CHECK(swsta_a80211(&s, 0, 1, &val, buf, 32) == 0 && !memcmp(buf, "FakeNet-B", 9) && allzero(buf, 9, 32));	/* [A L1] */
	CHECK(swsta_a80211(&s, 0, 9, &val, buf, 6) == 0 && buf[0] == 0x20 && buf[5] == 0x20);				/* [A L5] */
	/* 4 CHANNEL: 16 bytes, word0 1, +8 channel, +0xc 0 [A L3] */
	CHECK(swsta_a80211(&s, 0, 4, &val, buf, 16) == 0 && w32(buf, 0) == 1 && w32(buf, 4) == 0 && w32(buf, 8) == 6 && w32(buf, 12) == 0);
	/* 16 RSSI / 17 NOISE: 52 bytes, count@+4 == 1, value@+8 [A L7] */
	CHECK(swsta_a80211(&s, 0, 16, &val, buf, 52) == 0 && w32(buf, 4) == 1 && (int32_t)w32(buf, 8) == -50 && allzero(buf, 12, 52));
	CHECK(swsta_a80211(&s, 0, 17, &val, buf, 52) == 0 && w32(buf, 4) == 1 && w32(buf, 8) == 0 && allzero(buf, 12, 52));
	/* 2 AUTH_TYPE (16) and 6 PROTMODE (12): right length, values open [A L2, L4] */
	/* 2026-11-11: joined with WPA2-PSK the answer is what IO80211 stores at the join: lower 1 (+4), upper 8 (+8) [W-D3-weak-security s.6]; any other security stays zeros with the gap */
	memset(buf, 0x55, 16); CHECK(swsta_a80211(&s, 0, 2, &val, buf, 16) == 0 && w32(buf, 0) == 1 && w32(buf, 4) == 1 && w32(buf, 8) == 8 && w32(buf, 12) == 0 && !gaps_has("a80211 type 2 get 2 auth values open"));
	{ uint32_t keep = fk.bss[fk.cur].sec; fk.bss[fk.cur].sec = SWSTA_SEC_OTHER;
	  memset(buf, 0x55, 16); CHECK(swsta_a80211(&s, 0, 2, &val, buf, 16) == 0 && allzero(buf, 0, 16) && gaps_has("a80211 type 2 get 2 auth values open, zeros"));
	  fk.bss[fk.cur].sec = keep; }
	memset(buf, 0x55, 12); CHECK(swsta_a80211(&s, 0, 6, &val, buf, 12) == 0 && allzero(buf, 0, 12));
	CHECK(swsta_a80211(&s, 0, 2, &val, buf, 12) == (int)SWSTA_EOPNOTSUPP);	/* a 12-byte answer is wrong for type 2 [A L2] */
	/* 103 CURRENT_NETWORK: the scan record of the joined BSS [B s.4] */
	memset(buf, 0, 1164); swsta_p16(buf, 0x8a, 0x400);
	CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == 0);
	CHECK(w32(buf, 0x08) == 6 && (int16_t)(buf[0x16] | (buf[0x17] << 8)) == -50 && (buf[0x1a] | (buf[0x1b] << 8)) == 0x0011);
	CHECK(!memcmp(buf + 0x1c, fk.bss[1].bssid, 6) && buf[0x60] == 9 && !memcmp(buf + 0x61, "FakeNet-B", 9));
	/* the radio knows the joined BSSID from its scan list (bss_find): its IEs / rates / beacon / flags fill the record; ssid, channel and rssi stay the link's */
	CHECK(w32(buf, 0x0c) == 0x12 && buf[0x10] == 1 && (int16_t)(buf[0x12] | (buf[0x13] << 8)) == -90 && (int16_t)(buf[0x14] | (buf[0x15] << 8)) == 40 && (buf[0x18] | (buf[0x19] << 8)) == 100);
	CHECK(buf[0x22] == 4 && w32(buf, 0x30) == 11 && (buf[0x8a] | (buf[0x8b] << 8)) == 8 && !memcmp(buf + 0x8c, fk.bss[1].ie, 8) && allzero(buf, 0x94, 1164));
	CHECK(!gaps_has("a80211 type 103 record IEs empty"));
	/* no scan-list lookup (the radio has none) or no entry for the BSSID: the empty record and its gap, as before */
	rad.bss_find = 0;
	memset(buf, 0, 1164); swsta_p16(buf, 0x8a, 0x400);
	CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == 0 && w32(buf, 0x0c) == 0 && buf[0x10] == 0 && buf[0x22] == 0 && buf[0x8a] == 0 && buf[0x8b] == 0 && allzero(buf, 0x8c, 1164) && gaps_has("a80211 type 103 no IEs: Settings shows Weak Security"));
	/* joined to an OPEN network without IEs: nothing to warn about, the plain gap */
	{ uint32_t keep = fk.bss[fk.cur].sec; fk.bss[fk.cur].sec = SWSTA_SEC_OPEN;
	  gapbuf[0] = 0;
	  memset(buf, 0, 1164); swsta_p16(buf, 0x8a, 0x400);
	  CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == 0 && gaps_has("a80211 type 103 record IEs empty (no IEs known)") && !gaps_has("Weak Security") && (buf[0x1a] & 0x10) == 0);
	  fk.bss[fk.cur].sec = keep; }
	rad.bss_find = f_find;

	/* 21: failure codes. radio 9 -> 9 (looks like the library timeout), 40 -> 36 (generic), pending -> 0xffff [B s.3] */
	mk_assoc(a, "NoSuchNet", 0x8, 10, PASS_TXT, 14); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0);
	val = 0; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 9 && gaps_has("a80211 type 21 get 21 fail code passed through [guess]"));
	{ struct swsta_rev e = { SWSTA_REV_ASSOC_DONE, 40, 0 }; rad.up(rad.up_ctx, &e); }
	val = 0; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 36 && gaps_has("get 21 radio code unmapped -> 36"));
	fk.defer = 1; mk_assoc(a, "FakeNet-B", 0x8, 10, PASS_TXT, 14); CHECK(swsta_a80211(&s, 1, 20, &val, a, 592) == 0);
	val = 0; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 0xffffu && gaps_has("get 21 pending: 0xffff (async guess)"));
	fk.defer = 0;

	/* Set 22: len 0 and len 8 (reason ignored, reported) [B 4b] */
	fk.joined = 1; nkev = 0;
	CHECK(swsta_a80211(&s, 1, 22, &val, 0, 0) == 0 && fk.nleave == 1 && nkev == 1 && kev[0] == SWSTA_EV_LINK_CHANGED);
	memset(a, 0, 8); swsta_p32(a, 4, 3); g0 = s.gaps;
	CHECK(swsta_a80211(&s, 1, 22, &val, a, 8) == 0 && fk.nleave == 2 && s.gaps == g0 + 1 && gaps_has("set 22 disassoc reason ignored"));
	CHECK(swsta_a80211(&s, 1, 22, &val, a, 4) == (int)SWSTA_EOPNOTSUPP);

	/* rule 4/8: the passphrase never reaches the log; the summary carries gaps=N */
	swsta_summary(&s);
	CHECK(!strstr(logbuf, PASS_TXT) && !strstr(logbuf, "FakeNet"));
	CHECK(strstr(logbuf, "gaps=") != 0);
	printf("%s", logbuf);
	printf("RESULT: %s wlan_sta_codec_host_test\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
