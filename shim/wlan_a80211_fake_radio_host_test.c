/* wlan_a80211_fake_radio_host_test.c - end-to-end host test of the Apple80211 handler on the X-W1 station boundary (T-1435, goal W-D7 / P-W4).
 * Replays the sequence the iOS 16.7 userland issues (wifid start-up reads, Settings > Wi-Fi scan, join, state/RSSI, leave) as raw
 * SIOCGA80211/SIOCSA80211 requests through the REAL glue (sweet_wlan_a80211.h) and the REAL 16.7 codec (swsta_codec_167 of sweet_wlan_sta.h),
 * on a deterministic fake radio, a fake user address space and a fake kev_post_msg. Every selector is one row of g_sel[]: type, name, direction,
 * request length, layout source, expected answer. Rows with no cited layout must answer the soft errno AND print a GAP (rule 8).
 * Own code. Nothing here is an observation on hardware: the oracle is the layout bytes of docs/research/110 + W-D7-a80211-{get,join}-layouts.md.
 * Output: one `SEL ...` line per selector (the proof table), then `RESULT: PASS a80211-fake-radio scan=N join=N state=N gaps=N`.
 * Build: gcc -O1 -Wall -Wextra -Werror (also ASan/UBSan). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sweet_wlan_a80211.h"
#include "sweet_wlan_radio.h"

static int fails;
#define CHECK(c) do { if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

/* ---- fake user address space ---- */
#define UBASE 0x16fdff000ull
static uint8_t umem[16384];
static int u_in(void *ctx, uint64_t ua, void *dst, uint32_t n)
{
	(void)ctx;
	if (ua < UBASE || ua + n > UBASE + sizeof umem) return 14;
	memcpy(dst, umem + (ua - UBASE), n);
	return 0;
}
static int u_out(void *ctx, const void *src, uint64_t ua, uint32_t n)
{
	(void)ctx;
	if (ua < UBASE || ua + n > UBASE + sizeof umem) return 14;
	memcpy(umem + (ua - UBASE), src, n);
	return 0;
}
static void u_lock(void *ctx) { (void)ctx; }
static void u_unlock(void *ctx) { (void)ctx; }

/* ---- fake kev_post_msg: records (code, ifname ok) in order ---- */
static uint32_t g_kc[64]; static int g_kn;
static int u_kev(void *ctx, const struct a80211_kev *k)
{
	(void)ctx;
	if (k->vendor != 1 || k->kclass != 6 || k->subclass != 1 || k->n < 1 || k->dv[0].len != 16 || memcmp(k->dv[0].ptr, "en0", 4) != 0) { fails++; printf("FAIL kev envelope\n"); }
	if (g_kn < 64) g_kc[g_kn++] = k->code;
	return 0;
}

/* ---- log / gap capture ---- */
static char s_log[65536];
static void u_log(void *ctx, const char *l) { size_t n = strlen(s_log); (void)ctx; snprintf(s_log + n, sizeof s_log - n, "%s\n", l); }
static void u_gap(void *ctx, const char *t) { size_t n = strlen(s_log); (void)ctx; snprintf(s_log + n, sizeof s_log - n, "WIFI GAP: %s\n", t); }

/* ---- fake radio: 3 deterministic BSSes, join succeeds for a known SSID, else radio code 9 ---- */
struct fake { int on, joined, cursor, cur, nscan, njoin, nleave; struct swsta_bss bss[3]; };
static struct fake fk;
static struct swsta_radio rad;
static void fk_ev(int kind, int result, int reason) { struct swsta_rev e; e.kind = kind; e.result = result; e.reason = reason; rad.up(rad.up_ctx, &e); }
static int f_power(void *r, int op, int *st) { struct fake *f = r; if (op == SWSTA_PWR_ON) f->on = 1; else if (op == SWSTA_PWR_OFF) { f->on = 0; f->joined = 0; } *st = f->on; return 0; }
static int f_scan(void *r) { struct fake *f = r; if (!f->on) return SWSTA_EIO; f->nscan++; f->cursor = 0; fk_ev(SWSTA_REV_SCAN_DONE, 0, 0); return 0; }
static int f_next(void *r, struct swsta_bss *o) { struct fake *f = r; if (f->cursor >= 3) return 0; *o = f->bss[f->cursor++]; return 1; }
static int f_join(void *r, const struct swsta_join *j)
{
	struct fake *f = r; int i;
	f->njoin++;
	for (i = 0; i < 3; i++)
		if (f->bss[i].ssid_len == j->ssid_len && !memcmp(f->bss[i].ssid, j->ssid, j->ssid_len)) { f->joined = 1; f->cur = i; fk_ev(SWSTA_REV_ASSOC_DONE, 0, 0); return 0; }
	fk_ev(SWSTA_REV_ASSOC_DONE, 9, 0);
	return 0;
}
static int f_leave(void *r) { struct fake *f = r; f->nleave++; if (f->joined) { f->joined = 0; fk_ev(SWSTA_REV_LINK_DOWN, 0, 3); } return 0; }
static int f_status(void *r, struct swsta_status *o)
{
	struct fake *f = r; memset(o, 0, sizeof *o);
	if (!f->joined) return 0;
	o->joined = 1; memcpy(o->ssid, f->bss[f->cur].ssid, 32); o->ssid_len = f->bss[f->cur].ssid_len;
	memcpy(o->bssid, f->bss[f->cur].bssid, 6); o->channel = f->bss[f->cur].channel; o->rssi = f->bss[f->cur].rssi; o->sec = f->bss[f->cur].sec;
	return 0;
}
static void fk_setup(void)
{
	static const char *nm[3] = { "FakeNet-A", "FakeNet-B", "FakeNet-C" };
	int i; memset(&fk, 0, sizeof fk);
	for (i = 0; i < 3; i++) {
		fk.bss[i].ssid_len = (uint8_t)strlen(nm[i]); memcpy(fk.bss[i].ssid, nm[i], fk.bss[i].ssid_len);
		memset(fk.bss[i].bssid, 0x10 * (i + 1), 6); fk.bss[i].channel = 1u + 5u * (uint32_t)i; fk.bss[i].rssi = -40 - 10 * i; fk.bss[i].sec = SWSTA_SEC_WPA2_PSK;
	}
	memset(&rad, 0, sizeof rad);
	rad.power = f_power; rad.scan_start = f_scan; rad.bss_next = f_next; rad.join = f_join; rad.leave = f_leave; rad.status = f_status; rad.ctx = &fk;
}

static struct a80211 G;
static const struct a80211_io IO = { NULL, u_in, u_out, u_lock, u_unlock, u_kev, u_log, u_gap };

/* ---- selector table: the proof table. exp: A = answered (0), Z = answered 0 with zero-filled open fields + GAP, S = soft errno
 * (EOPNOTSUPP) + GAP, N = ENXIO while not joined (soft, no GAP), I = 0 with value in req_val ---- */
struct sel { uint32_t type; const char *name; char dir; uint32_t len; const char *src; char exp; int seen; };
static struct sel g_sel[] = {
	{ 12,  "CARD_CAPABILITIES", 'G', 13,    "110:137 W-D7 L6",  'Z', 0 },
	{ 19,  "POWER",             'G', 24,    "110:140 W-D7 L8",  'A', 0 },
	{ 19,  "POWER",             'S', 24,    "110:140 W-D7 L8",  'A', 0 },
	{ 106, "RSSI_BOUNDS",       'G', 16,    "110:141 W-D7 L12", 'Z', 0 },
	{ 207, "CHANNELS_INFO",     'G', 10016, "110:142 W-D7 L13 (layout open)", 'S', 0 },
	{ 27,  "SUPPORTED_CHANNELS",'G', 4824,  "W-UI-a80211-refused-types s.2 (= 254 body, 2026-11-11)", 'Z', 0 },
	{ 254, "HW_SUPPORTED_CHANNELS",'G', 4824, "W-UI-a80211-refused-types s.2 (flags cloned)", 'Z', 0 },
	{ 15,  "OP_MODE",           'G', 0,     "W-D7 L15 4-byte class (value open)", 'S', 0 },
	{ 5,   "POWERSAVE",         'G', 0,     "W-D7 L15 4-byte class (value open)", 'S', 0 },
	{ 13,  "STATE",             'G', 0,     "W-D7 L15 4-byte class (value open)", 'S', 0 },
	{ 43,  "DRIVER_VERSION",    'G', 256,   "W-D7 L14 (layout open)", 'S', 0 },
	{ 44,  "HARDWARE_VERSION",  'G', 256,   "W-D7 L14 (layout open)", 'S', 0 },
	{ 51,  "COUNTRY_CODE",      'G', 3,     "W-UI-a80211-refused-types row 51 (3 B)", 'Z', 0 },
	{ 10,  "SCAN_REQ",          'S', 5456,  "110:146 W-D7 S5 (body ignored: GAP)", 'Z', 0 },
	{ 11,  "SCAN_RESULT",       'G', 1164,  "110:147 W-D7 S1-S4 join-layouts s.4", 'Z', 0 },
	{ 20,  "ASSOCIATE",         'S', 592,   "110:148 join-layouts s.2", 'I', 0 },	/* W-UI: only the station's first link event carries the one SNR/NOISE GAP, so the GAP count depends on the call (rc checked) */
	{ 21,  "ASSOCIATE_RESULT",  'G', 0,     "W-D7 R1 join-layouts s.3 (4-byte class)", 'I', 0 },
	{ 22,  "DISASSOCIATE",      'S', 0,     "110:150", 'A', 0 },
	{ 103, "CURRENT_NETWORK",   'G', 1164,  "110:151 join-layouts s.4", 'Z', 0 },
	{ 1,   "SSID",              'G', 32,    "110:152", 'A', 0 },
	{ 9,   "BSSID",             'G', 6,     "110:153", 'A', 0 },
	{ 4,   "CHANNEL",           'G', 16,    "W-D7 L3", 'Z', 0 },
	{ 2,   "AUTH_TYPE",         'G', 16,    "W-D7 L2; 2026-11-11 W-D3-weak-security s.6: joined WPA2 personal: lower 1, upper 8 (no gap), the gap count is checked at the call site", 'I', 0 },
	{ 6,   "PROTMODE",          'G', 12,    "W-D7 L4", 'Z', 0 },
	{ 16,  "RSSI",              'G', 52,    "W-D7 L7", 'Z', 0 },
	{ 17,  "NOISE",             'G', 52,    "W-D7 L7", 'Z', 0 },
};
#define NSEL (sizeof g_sel / sizeof g_sel[0])
static uint32_t s_answered, s_soft;

static uint32_t gaps_now(void) { return G.sta.gaps; }
static void req(char dir, uint32_t type, uint32_t len, uint64_t ua, uint32_t *val, int *rc_out)
{
	struct a80211_req r;
	memset(&r, 0, sizeof r); memcpy(r.ifname, "en0", 3); r.type = type; r.len = len; r.data = len ? ua : 0;
	*rc_out = a80211_ioctl(&G, dir == 'G' ? A80211_SIOCG : A80211_SIOCS, &r);
	if (val) *val = r.val;
}
/* issue one table row; exp 'N' (not joined) rows are issued via reqx() */
static int row(char dir, uint32_t type, uint32_t len, uint64_t ua, uint32_t *val)
{
	uint32_t g0 = gaps_now(), i; int rc, ok = 0;
	char want = 0;
	req(dir, type, len, ua, val, &rc);
	for (i = 0; i < NSEL; i++)
		if (g_sel[i].type == type && g_sel[i].dir == dir) { g_sel[i].seen++; want = g_sel[i].exp; break; }
	if (want == 'A') ok = (rc == 0 && gaps_now() == g0);
	else if (want == 'I') ok = (rc == 0);                  /* Get 21: the GAP count depends on the join state, checked at the call sites */
	else if (want == 'Z') ok = (rc == 0 && gaps_now() > g0);
	else if (want == 'S') ok = (rc == (int)SWSTA_EOPNOTSUPP && gaps_now() == g0 + 1);
	if (!ok) { fails++; printf("FAIL row type=%u %c len=%u rc=%d exp=%c gaps+%u\n", (unsigned)type, dir, (unsigned)len, rc, want, (unsigned)(gaps_now() - g0)); }
	if (rc == 0) s_answered++; else if (rc == (int)SWSTA_EOPNOTSUPP) s_soft++;
	return rc;
}
static uint32_t rw32(uint32_t off) { return swsta_g32(umem, off); }
static int16_t rs16(uint32_t off) { return (int16_t)(umem[off] | (umem[off + 1] << 8)); }

/* the ASSOCIATE structure of join-layouts s.2: WPA2-PSK passphrase (cipher 10) */
#define PASS_TXT "fake-passphrase-for-test"
static void mk_assoc(uint8_t *s, const char *ssid)
{
	memset(s, 0, 592);
	swsta_p32(s, 0, 1); swsta_p16(s, 4, 2); swsta_p16(s, 6, 1); swsta_p16(s, 8, 0x8);
	swsta_p32(s, 0x0c, (uint32_t)strlen(ssid)); memcpy(s + 0x10, ssid, strlen(ssid));
	swsta_p32(s, 0x3c, (uint32_t)strlen(PASS_TXT)); swsta_p32(s, 0x40, 10); memcpy(s + 0x48, PASS_TXT, strlen(PASS_TXT));
}

static uint32_t n_scan, n_join, n_state;

static void phase_startup(void)
{
	uint32_t val = 0;
	/* bind probe + wifid start-up reads [110 s.8.1 rows 5, 7], then the 4-byte-class reads of the wifid CopyValue loop [W-D7 G3] */
	CHECK(row('G', 12, 13, UBASE, &val) == 0);
	CHECK(row('G', 19, 24, UBASE, &val) == 0 && rw32(0) == 1 && rw32(4) == 1 && rw32(8) == 0);         /* {version, N=1, state off} */
	CHECK(row('G', 106, 16, UBASE, &val) == 0);
	CHECK(row('G', 207, 10016, UBASE, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 27, 4824, UBASE, &val) == 0 && rw32(4) == 7u && rw32(8 + 12 * 3 + 4) == 36u && rw32(8 + 12 * 3 + 8) == 0x12u && rw32(8 + 4) == 1u && rw32(8 + 8) == 0xAu);     /* 2026-11-11: 27 = the 254 list */
	CHECK(row('G', 254, 4824, UBASE, &val) == 0 && rw32(4) == 7u && rw32(8 + 12 * 3 + 4) == 36u && rw32(8 + 12 * 3 + 8) == 0x12u && rw32(8 + 4) == 1u && rw32(8 + 8) == 0xAu);     /* sprint 2026-11-08: the driver's 7 scan channels, ch 36 = 20 MHz | 5 GHz */
	CHECK(row('G', 15, 0, 0, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 5, 0, 0, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 13, 0, 0, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 43, 256, UBASE, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 44, 256, UBASE, &val) == (int)SWSTA_EOPNOTSUPP);
	CHECK(row('G', 51, 3, UBASE, &val) == 0 && (rw32(0) & 0xFFFFFFu) == ('X' | 'Z' << 8));      /* sprint 2026-11-08: the CLM country of this boot, NUL-terminated */
}

/* GetInfoCopy while not joined: all ENXIO (soft, no GAP) [110 s.8.1 row 11, W-D7 R5] */
static void phase_not_joined(int *state_nj)
{
	static const struct { uint32_t t, l; } q[] = { { 1, 32 }, { 9, 6 }, { 4, 16 }, { 16, 52 }, { 17, 52 }, { 2, 16 }, { 6, 12 }, { 103, 1164 } };
	uint32_t i, g0 = gaps_now(); int rc;
	for (i = 0; i < sizeof q / sizeof q[0]; i++) {
		req('G', q[i].t, q[i].l, UBASE, NULL, &rc);
		CHECK(rc == (int)SWSTA_ENXIO);
		(*state_nj)++;
	}
	CHECK(gaps_now() == g0);
}

static void phase_scan(void)
{
	uint32_t i, n = 0; int rc;
	uint32_t val = 0;
	/* power on: POWER_CHANGED (1) */
	memset(umem, 0, 64); swsta_p32(umem, 0, 1); swsta_p32(umem, 4, 1); swsta_p32(umem, 8, 1);
	g_kn = 0;
	CHECK(row('S', 19, 24, UBASE, &val) == 0 && g_kn == 1 && g_kc[0] == SWSTA_EV_POWER_CHANGED);
	CHECK(row('G', 19, 24, UBASE + 100, &val) == 0 && swsta_g32(umem, 100 + 8) == 1);
	/* Set 10 -> SCAN_DONE (10) -> Get 11 until the end errno */
	g_kn = 0;
	CHECK(row('S', 10, 5456, UBASE, &val) == 0 && g_kn == 1 && g_kc[0] == SWSTA_EV_SCAN_DONE);
	for (i = 0; i < 10; i++) {
		uint32_t off = 2000u + i * 1164u;
		memset(umem + off, 0, 1164); swsta_p16(umem, off + 0x8a, 0x400);          /* the library pre-sets the IE capacity */
		if (i == 3) {                                                                  /* the end of the list: soft EIO, no GAP */
			uint32_t g0 = gaps_now();
			req('G', 11, 1164, UBASE + off, &val, &rc);
			CHECK(rc == (int)SWSTA_E_SCAN_END && gaps_now() == g0);
			break;
		}
		rc = row('G', 11, 1164, UBASE + off, &val);
		CHECK(rc == 0);
		CHECK(rw32(off + 0x08) == fk.bss[i].channel);
		CHECK(rs16(off + 0x16) == fk.bss[i].rssi);
		CHECK(umem[off + 0x60] == fk.bss[i].ssid_len && !memcmp(umem + off + 0x61, fk.bss[i].ssid, fk.bss[i].ssid_len));
		CHECK(!memcmp(umem + off + 0x1c, fk.bss[i].bssid, 6) && (umem[off + 0x1a] & 0x11) == 0x11);   /* ESS + privacy */
		n++;
	}
	CHECK(n == 3);
	n_scan = n;
}

static void phase_join(void)
{
	uint32_t val = 0, k = 0, n = 0; int rc;
	/* join FakeNet-B: Set 20 -> ASSOC_DONE 9, SSID 2, BSSID 3, LINK 4 [110 s.8.2 row 13] */
	mk_assoc(umem, "FakeNet-B");
	g_kn = 0;
	CHECK(row('S', 20, 592, UBASE, &val) == 0 && fk.njoin == 1);
	CHECK(g_kn == 4 && g_kc[0] == SWSTA_EV_ASSOC_DONE && g_kc[1] == SWSTA_EV_SSID_CHANGED && g_kc[2] == SWSTA_EV_BSSID_CHANGED && g_kc[3] == SWSTA_EV_LINK_CHANGED);
	n++;
	/* Get 21: 4-byte class, req_len 0, answer in req_val: 1 = success */
	{ uint32_t g1 = gaps_now(); val = 99; CHECK(row('G', 21, 0, 0, &val) == 0 && val == 1 && gaps_now() == g1); n++; }
	/* state: SSID, BSSID, CHANNEL, RSSI, CURRENT_NETWORK after the join [110 s.8.2 row 15] */
	memset(umem, 0, 4000);
	CHECK(row('G', 1, 32, UBASE, &val) == 0 && !memcmp(umem, "FakeNet-B", 9) && umem[9] == 0); k++;
	CHECK(row('G', 9, 6, UBASE + 100, &val) == 0 && !memcmp(umem + 100, fk.bss[1].bssid, 6)); k++;
	CHECK(row('G', 4, 16, UBASE + 200, &val) == 0 && rw32(200 + 8) == 6 && rw32(200) == 1); k++;               /* word0 = 1, channel @+8 */
	CHECK(row('G', 16, 52, UBASE + 300, &val) == 0 && rw32(300 + 4) == 1 && (int32_t)rw32(300 + 8) == -50); k++;   /* count@+4 == 1, rssi@+8 */
	CHECK(row('G', 17, 52, UBASE + 400, &val) == 0 && rw32(400 + 4) == 1); k++;
	{ uint32_t g1 = gaps_now(); CHECK(row('G', 2, 16, UBASE + 500, &val) == 0 && rw32(500) == 1 && rw32(500 + 4) == 1 && rw32(500 + 8) == 8 && rw32(500 + 12) == 0 && gaps_now() == g1); k++; }       /* the kept/joined WPA2-PSK link: what IO80211 stores at the join */
	CHECK(row('G', 6, 12, UBASE + 600, &val) == 0); k++;
	CHECK(row('G', 103, 1164, UBASE + 2000, &val) == 0 && rw32(2000 + 0x08) == 6 && rs16(2000 + 0x16) == -50 &&
	      umem[2000 + 0x60] == 9 && !memcmp(umem + 2000 + 0x61, "FakeNet-B", 9) && !memcmp(umem + 2000 + 0x1c, fk.bss[1].bssid, 6)); k++;
	n_state = k;
	/* a failed join: unknown SSID -> radio code 9 -> ASSOC_DONE only (no 2/3/4), Get 21 passes 9 through with a GAP */
	{
		uint32_t g0 = gaps_now();
		mk_assoc(umem, "NoSuchNet"); g_kn = 0;
		CHECK(row('S', 20, 592, UBASE, &val) == 0 && g_kn == 1 && g_kc[0] == SWSTA_EV_ASSOC_DONE);
		val = 0; rc = row('G', 21, 0, 0, &val);
		CHECK(rc == 0 && val == 9 && gaps_now() == g0 + 1);                       /* "fail code passed through [guess]" */
		n += 2;
	}
	/* rejoin the good one, then leave: Set 22 -> LINK_CHANGED, Gets answer ENXIO again [110 s.8.2 row 16] */
	mk_assoc(umem, "FakeNet-B"); CHECK(row('S', 20, 592, UBASE, &val) == 0); n++;
	g_kn = 0;
	CHECK(row('S', 22, 0, 0, &val) == 0 && fk.nleave == 1 && g_kn == 1 && g_kc[0] == SWSTA_EV_LINK_CHANGED); n++;
	req('G', 1, 32, UBASE, NULL, &rc); CHECK(rc == (int)SWSTA_ENXIO);
	n_join = n;
}

/* ---- W-UI (sprint 2026-11-08): the KEPT backend. The boot run joined and the dongle keeps running (xnu patch 0268): the real swr backend (sweet_wlan_radio.h) over a fake boot-time scan record, behind the
 * real glue and the 16.7 codec. Get 1/9/4/16/103/21 must answer the joined values, join of the same SSID succeeds, any other join / leave / power off is refused with a GAP. The SSID/BSSID never appear in a log line. */
#define KSSID "KeptNet-W"
static const uint8_t KBSSID[6] = { 0xa2, 0xb3, 0xc4, 0xd5, 0xe6, 0xf7 };
static uint8_t krec[4][SWR_REPLAY_BUF]; static uint32_t kst[4], klen[4]; static uint32_t knrec;
static void mk_rec(const uint8_t bssid[6], const char *ssid, uint32_t ch, int rssi)
{
	uint8_t *b = krec[knrec]; uint8_t *r = b + SWR_RES_HDR;
	memset(b, 0, SWR_REPLAY_BUF);
	swsta_p32(b, 0, SWR_REPLAY_BUF); swsta_p16(b, 8, SWR_SYNC); swsta_p16(b, 10, 1);
	swsta_p32(r, 0, SWR_BSS_VER); swsta_p32(r, 4, SWR_BSS_NEED); memcpy(r + 8, bssid, 6); swsta_p16(r, 16, 0x11);
	r[18] = (uint8_t)strlen(ssid); memcpy(r + 19, ssid, strlen(ssid)); swsta_p16(r, 72, 0x1000u | ch); swsta_p16(r, 78, (uint32_t)(uint16_t)(int16_t)rssi); r[88] = (uint8_t)ch;
	kst[knrec] = SWR_ST_PARTIAL; klen[knrec] = SWR_REPLAY_BUF; knrec++;
}
static int k_get(uint32_t i, uint32_t *st, uint8_t *out, uint32_t cap)
{
	if (i >= knrec + 1) return 0;
	if (i == knrec) { memset(out, 0, SWR_RES_HDR); swsta_p16(out, 8, SWR_SYNC); *st = SWR_ST_OK; return (int)SWR_RES_HDR; }   /* the status-0 end event */
	if (cap < klen[i]) return -1;
	memcpy(out, krec[i], klen[i]); *st = kst[i]; return (int)klen[i];
}
static void k_gap(void *cx, const char *t) { (void)cx; u_gap(NULL, t); }
static struct swr RK;
static struct a80211 GK;
static void req_on(struct a80211 *g, char dir, uint32_t type, uint32_t len, uint64_t ua, uint32_t *val, int *rc_out)
{
	struct a80211_req r;
	memset(&r, 0, sizeof r); memcpy(r.ifname, "en0", 3); r.type = type; r.len = len; r.data = len ? ua : 0;
	*rc_out = a80211_ioctl(g, dir == 'G' ? A80211_SIOCG : A80211_SIOCS, &r);
	if (val) *val = r.val;
}
static uint32_t n_kept;
static void phase_kept(void)
{
	static const uint8_t other[6] = { 1, 2, 3, 4, 5, 6 };
	const struct swr_replay rp = { k_get, NULL, k_gap, NULL };
	const struct swr_link link = SWR_REPLAY_LINK(&rp);
	const struct swr_cb cb = { u_log, u_gap, NULL };
	uint32_t val = 0, g0, k = 0, i; int rc;

	knrec = 0;
	mk_rec(other, "OtherNet", 1, -70);
	mk_rec(KBSSID, KSSID, 11, -47);
	mk_rec(other, "OtherNet", 6, -60);                                     /* heard twice: the stronger kept, not our BSSID */
	memset(umem, 0, sizeof umem);
	swr_init(&RK, &link, &cb);
	CHECK(swr_kept_set(&RK, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), KBSSID, 11u, -47, 0xFu) == 1 && strstr(s_log, "RSSI is refreshed only by swr_kept_rssi") != NULL);   /* the firmware's values (swl25_live) */
	CHECK(RK.kept.joined && RK.kept.channel == 11 && RK.kept.rssi == -47 && RK.on == 1);
	a80211_init(&GK, &RK.radio, NULL, &IO, "en0", NULL);
	CHECK(GK.sta.power_on == 1);                                           /* the kept chip is on: Get 19 reads ON without a Set */
	g_kn = 0;
	CHECK(swr_kept_announce(&RK) == 1 && g_kn == 4 && g_kc[0] == SWSTA_EV_ASSOC_DONE && g_kc[1] == SWSTA_EV_SSID_CHANGED && g_kc[2] == SWSTA_EV_BSSID_CHANGED && g_kc[3] == SWSTA_EV_LINK_CHANGED);
	/* Get 1 / 9 / 4 / 16 / 103 / 21 answer the joined values, no ENXIO */
	memset(umem, 0, 4000);
	req_on(&GK, 'G', 1, 32, UBASE, &val, &rc);            CHECK(rc == 0 && !memcmp(umem, KSSID, strlen(KSSID)) && umem[strlen(KSSID)] == 0); k++;
	req_on(&GK, 'G', 9, 6, UBASE + 100, &val, &rc);       CHECK(rc == 0 && !memcmp(umem + 100, KBSSID, 6)); k++;
	req_on(&GK, 'G', 4, 16, UBASE + 200, &val, &rc);      CHECK(rc == 0 && rw32(200) == 1 && rw32(200 + 8) == 11); k++;
	req_on(&GK, 'G', 16, 52, UBASE + 300, &val, &rc);     CHECK(rc == 0 && rw32(300 + 4) == 1 && (int32_t)rw32(300 + 8) == -47); k++;
	req_on(&GK, 'G', 103, 1164, UBASE + 2000, &val, &rc); CHECK(rc == 0 && rw32(2000 + 0x08) == 11 && rs16(2000 + 0x16) == -47 && umem[2000 + 0x60] == strlen(KSSID) &&
	                                                            !memcmp(umem + 2000 + 0x61, KSSID, strlen(KSSID)) && !memcmp(umem + 2000 + 0x1c, KBSSID, 6)); k++;
	/* Part A: the keep thread's RSSI refresh (swr_kept_rssi): Get 16 and Get 103 answer the new firmware value; outside -127..-1 is refused with a GAP and the value stays */
	CHECK(swr_kept_rssi(&RK, -58) == 1 && RK.kept.rssi == -58 && RK.kept_rssi_n == 1);
	req_on(&GK, 'G', 16, 52, UBASE + 300, &val, &rc);     CHECK(rc == 0 && (int32_t)rw32(300 + 8) == -58); k++;
	req_on(&GK, 'G', 103, 1164, UBASE + 2000, &val, &rc); CHECK(rc == 0 && rs16(2000 + 0x16) == -58); k++;
	CHECK(swr_kept_rssi(&RK, 0) == 0 && swr_kept_rssi(&RK, -128) == 0 && RK.kept.rssi == -58 && RK.kept_rssi_n == 1 && strstr(s_log, "RSSI refresh refused") != NULL);
	CHECK(swr_kept_rssi(&RK, -47) == 1);                    /* back to the value the rest of this phase expects */
	g0 = GK.sta.gaps; val = 99;
	req_on(&GK, 'G', 21, 0, 0, &val, &rc);               CHECK(rc == 0 && val == 1 && GK.sta.gaps == g0); k++;     /* ASSOCIATE_RESULT 1 = success, no "asked with no join" gap */
	req_on(&GK, 'G', 19, 24, UBASE + 700, &val, &rc);     CHECK(rc == 0 && rw32(700 + 8) == 1); k++;                /* power on */
	/* scan: the boot-time record, stale (a GAP says so), 2 distinct BSSIDs */
	g0 = GK.sta.gaps;
	req_on(&GK, 'S', 10, 5456, UBASE, &val, &rc);         CHECK(rc == 0 && strstr(s_log, "boot-time scan (stale, 4 events)") != NULL && RK.n == 2); k++;
	for (i = 0; i < 3; i++) { memset(umem + 3000, 0, 1164); swsta_p16(umem, 3000 + 0x8a, 0x400); req_on(&GK, 'G', 11, 1164, UBASE + 3000, &val, &rc); CHECK((i < 2) == (rc == 0)); }
	/* join of the same SSID: success, ASSOC_DONE re-posted (4 kev), Get 21 still 1 */
	mk_assoc(umem, KSSID); g_kn = 0; g0 = RK.gaps;
	req_on(&GK, 'S', 20, 592, UBASE, &val, &rc);          CHECK(rc == 0 && RK.kept_joins == 1 && g_kn == 4 && g_kc[0] == SWSTA_EV_ASSOC_DONE && RK.gaps == g0 &&
	                                                            strstr(s_log, "already joined by the boot run") != NULL); k++;
	req_on(&GK, 'G', 21, 0, 0, &val, &rc);               CHECK(rc == 0 && val == 1); k++;
	/* join of another SSID: refused + GAP, nothing posted */
	mk_assoc(umem, "NoSuchNet"); g_kn = 0; g0 = RK.gaps;
	req_on(&GK, 'S', 20, 592, UBASE, &val, &rc);          CHECK(rc == (int)SWSTA_EOPNOTSUPP && RK.kept_refused == 1 && RK.gaps == g0 + 1 && g_kn == 0 && strstr(s_log, "join of another network refused") != NULL); k++;
	/* the link is still reported joined after the refusal */
	req_on(&GK, 'G', 9, 6, UBASE + 100, &val, &rc);       CHECK(rc == 0 && !memcmp(umem + 100, KBSSID, 6)); k++;
	/* leave and power off: refused + GAP, still joined */
	g0 = RK.gaps;
	req_on(&GK, 'S', 22, 0, 0, &val, &rc);                CHECK(rc == (int)SWSTA_EOPNOTSUPP && RK.gaps == g0 + 1); k++;
	memset(umem, 0, 64); swsta_p32(umem, 0, 1); swsta_p32(umem, 4, 1); swsta_p32(umem, 8, 0);
	req_on(&GK, 'S', 19, 24, UBASE, &val, &rc);           CHECK(rc == (int)SWSTA_EOPNOTSUPP && RK.gaps == g0 + 2 && RK.on == 1 && RK.kept_refused == 3); k++;
	req_on(&GK, 'G', 1, 32, UBASE + 1000, &val, &rc);     CHECK(rc == 0 && !memcmp(umem + 1000, KSSID, strlen(KSSID))); k++;
	/* a join request that names another BSSID for the same SSID is "another network" */
	{
		struct swsta_join j; memset(&j, 0, sizeof j); memcpy(j.ssid, KSSID, strlen(KSSID)); j.ssid_len = (uint8_t)strlen(KSSID); memcpy(j.bssid, other, 6); j.has_bssid = 1;
		CHECK(swr_join(&RK, &j) == (int)SWSTA_EOPNOTSUPP);
	}
	/* channel and RSSI not read from the firmware: joined, both 0, a GAP each (+ the snapshot GAP) */
	{
		struct swr R2; g0 = 0;
		swr_init(&R2, &link, &cb);
		CHECK(swr_kept_set(&R2, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), (const uint8_t[6]){ 9, 9, 9, 9, 9, 9 }, 11u, -47, 0x3u) == 1);
		CHECK(R2.kept.joined && R2.kept.channel == 0 && R2.kept.rssi == 0 && R2.gaps == 3 && strstr(s_log, "RSSI not read from the firmware") != NULL);
		swr_init(&R2, &link, &cb);
		CHECK(swr_kept_set(&R2, (const uint8_t *)KSSID, 0, KBSSID, 11u, -47, 0xFu) == 0 && !R2.kept.joined && R2.gaps == 1);   /* no SSID: not kept, a GAP */
		swr_init(&R2, &link, &cb);
		CHECK(swr_kept_set(&R2, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), KBSSID, 11u, -47, 0xDu) == 0 && !R2.kept.joined && R2.gaps == 1);   /* BSSID not read: not kept */
	}
	swr_summary(&RK);
	CHECK(strstr(s_log, "radio kept joins_answered=1 refused=4") != NULL);
	n_kept = k;
}

/* patch 0281 (W-D6 boot A): the AP link is lost. swr_kept_clear clears kept.joined (status = not joined, so LINK_CHANGED carries isLinkDown = 1), fires LINK_DOWN (one LINK_CHANGED kev), is once-only, and a join of the SAME ssid is
 * refused with a log line and a GAP instead of the "already joined" success; nothing else is touched (no firmware command exists in this layer). */
static void phase_kept_down(void)
{
	const struct swr_replay rp = { k_get, NULL, k_gap, NULL };
	const struct swr_link link = SWR_REPLAY_LINK(&rp);
	const struct swr_cb cb = { u_log, u_gap, NULL };
	struct swr RD; struct a80211 GD;
	struct swsta_status st; struct swsta_join j;
	uint32_t val = 0, g0; int rc;

	memset(umem, 0, sizeof umem);
	swr_init(&RD, &link, &cb);
	CHECK(swr_kept_set(&RD, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), KBSSID, 11u, -47, 0xFu) == 1);
	a80211_init(&GD, &RD.radio, NULL, &IO, "en0", NULL);
	CHECK(swr_kept_announce(&RD) == 1);
	memset(&j, 0, sizeof j); memcpy(j.ssid, KSSID, strlen(KSSID)); j.ssid_len = (uint8_t)strlen(KSSID);
	g_kn = 0;
	CHECK(swr_kept_clear(&RD, 0) == 1 && g_kn == 0 && !RD.kept.joined && RD.kept_down == 1 && RD.kept_down_n == 1);   /* post == 0: state only, nothing posted */
	RD.kept.joined = 1; RD.kept_down = 0;                                  /* back to joined to see the posted event */
	g_kn = 0;
	CHECK(swr_kept_clear(&RD, 1) == 1 && g_kn == 1 && g_kc[0] == SWSTA_EV_LINK_CHANGED && !RD.kept.joined && RD.kept_down == 1 && strstr(s_log, "radio kept link: DOWN (AP link lost)") != NULL);
	memset(&st, 0xEE, sizeof st);
	CHECK(RD.radio.status(RD.radio.ctx, &st) == 0 && !st.joined);        /* swsta_post_link builds byte 0 = isLinkDown from this: 1 */
	g_kn = 0;
	CHECK(swr_kept_clear(&RD, 1) == 0 && g_kn == 0 && RD.kept_down_n == 2u);   /* once only: a second call posts nothing */
	g0 = RD.gaps;
	CHECK(swr_join(&RD, &j) == (int)SWSTA_EOPNOTSUPP && RD.kept_refused == 1 && RD.kept_joins == 0 && RD.gaps == g0 + 1 && g_kn == 0 && strstr(s_log, "join refused: the kept link is DOWN") != NULL);
	mk_assoc(umem, KSSID);
	req_on(&GD, 'S', 20, 592, UBASE, &val, &rc);
	CHECK(rc == (int)SWSTA_EOPNOTSUPP && RD.kept_joins == 0);              /* the same SSID through Set 20: failure, no ASSOC_DONE */
	CHECK(swr_kept_rssi(&RD, -50) == 0);                                   /* a refresh while DOWN is refused (GAP), the cleared state keeps */
	{       /* a radio that never held the link, or whose link is already down, has nothing to clear */
		struct swr R3;

		swr_init(&R3, &link, &cb);
		CHECK(swr_kept_clear(&R3, 1) == 0 && R3.kept_down == 0);
	}
}

static void phase_unknown(void)
{
	uint32_t g0 = gaps_now(); int rc;
	/* selectors nobody modelled: soft errno + one GAP each, never plain success (rule 8) */
	req('G', 300, 4, UBASE, NULL, &rc);  CHECK(rc == (int)SWSTA_EOPNOTSUPP && gaps_now() == g0 + 1);
	req('S', 107, 0, 0, NULL, &rc);      CHECK(rc == (int)SWSTA_EOPNOTSUPP && gaps_now() == g0 + 2);     /* ROAM */
	req('G', 0, 0, 0, NULL, &rc);        CHECK(rc == (int)SWSTA_EOPNOTSUPP && gaps_now() == g0 + 3);     /* type 0: out of range */
	req('G', 500, 0, 0, NULL, &rc);      CHECK(rc == (int)SWSTA_EOPNOTSUPP && gaps_now() == g0 + 4);     /* > 425 */
	CHECK(s_log[0] != 0 && strstr(s_log, "WIFI GAP: a80211 type 300 get len 4 not handled") != NULL);
}

/* patch 0282 (W-D6 boot B): the keep thread's re-join is done. swr_kept_up takes the BSSID / channel / RSSI the firmware reported (a re-join may land on another AP), keeps the SSID, sets joined, clears kept_down and fires ASSOC_DONE result 0
 * (kev 9, 2, 3, 4: the station posts SSID/BSSID changed and LINK_CHANGED with isLinkDown 0); a join of the same SSID is answered success again; refused when the link was not DOWN or the BSSID was not read. */
static void phase_kept_up(void)
{
	const struct swr_replay rp = { k_get, NULL, k_gap, NULL };
	const struct swr_link link = SWR_REPLAY_LINK(&rp);
	const struct swr_cb cb = { u_log, u_gap, NULL };
	static const uint8_t NB[6] = { 0x02, 0x66, 0x77, 0x88, 0x99, 0xAA };
	struct swr RU; struct a80211 GU;
	struct swsta_status st; uint32_t g0;

	memset(umem, 0, sizeof umem);
	swr_init(&RU, &link, &cb);
	CHECK(swr_kept_up(&RU, NB, 36u, -50, 0xEu) == 0 && !RU.kept.joined);                  /* never held a link: refused */
	CHECK(swr_kept_set(&RU, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), KBSSID, 11u, -47, 0xFu) == 1);
	a80211_init(&GU, &RU.radio, NULL, &IO, "en0", NULL);
	CHECK(swr_kept_announce(&RU) == 1);
	g_kn = 0;
	CHECK(swr_kept_up(&RU, NB, 36u, -50, 0xEu) == 0 && g_kn == 0 && RU.kept.joined && RU.kept_up_n == 0u);   /* joined already: not DOWN, refused, nothing posted */
	CHECK(swr_kept_clear(&RU, 1) == 1 && !RU.kept.joined);
	g_kn = 0;
	g0 = RU.gaps;
	CHECK(swr_kept_up(&RU, NB, 36u, -50, 0xCu) == 0 && !RU.kept.joined && RU.kept_down == 1 && g_kn == 0 && RU.gaps == g0 + 1u);   /* no BSSID read (ok bit 2 clear): refused, stays DOWN */
	CHECK(swr_kept_up(&RU, NULL, 36u, -50, 0xEu) == 0 && !RU.kept.joined && RU.kept_down == 1);
	g_kn = 0;
	CHECK(swr_kept_up(&RU, NB, 36u, -50, 0xEu) == 1 && RU.kept.joined && RU.kept_down == 0 && RU.kept_up_n == 1u);
	CHECK(g_kn == 4 && g_kc[0] == SWSTA_EV_ASSOC_DONE && g_kc[1] == SWSTA_EV_SSID_CHANGED && g_kc[2] == SWSTA_EV_BSSID_CHANGED && g_kc[3] == SWSTA_EV_LINK_CHANGED);
	memset(&st, 0xEE, sizeof st);
	CHECK(RU.radio.status(RU.radio.ctx, &st) == 0 && st.joined && st.ssid_len == strlen(KSSID) && memcmp(st.ssid, KSSID, st.ssid_len) == 0 && memcmp(st.bssid, NB, 6) == 0 && st.channel == 36u && st.rssi == -50);
	CHECK(strstr(s_log, "radio kept link: UP again (re-joined by the keep thread)") != NULL);
	CHECK(swr_kept_up(&RU, NB, 36u, -50, 0xEu) == 0 && RU.kept_up_n == 1u);                  /* once: a second call (not DOWN) posts nothing */
	/* a re-join with the channel and RSSI unread keeps the previous values and says so; an RSSI outside -127..-1 is not taken */
	CHECK(swr_kept_clear(&RU, 0) == 1);
	g0 = RU.gaps;
	g_kn = 0;
	CHECK(swr_kept_up(&RU, KBSSID, 0u, 5, 0xAu) == 1 && RU.kept.channel == 36u && RU.kept.rssi == -50 && memcmp(RU.kept.bssid, KBSSID, 6) == 0 && RU.gaps == g0 + 2u && g_kn == 4);       /* channel unread, RSSI +5 out of range: both keep the old value, one GAP each */
	CHECK(swr_kept_clear(&RU, 0) == 1);
	CHECK(swr_kept_up(&RU, NB, 0u, 0, 0x2u) == 1 && RU.kept.channel == 36u && RU.kept.rssi == -50 && RU.gaps == g0 + 4u);
}

/* ---- patch 0292 (WIFI-COVERAGE D3/D4): the LIVE scan of the kept link. Set 10 asks the keep thread (ascan.request: here a fake that counts and answers a chosen errno) and returns at once; the "keep thread" below feeds ESCAN_RESULT events into a scratch
 * table with swr_event (the sink the glue uses) and ends the scan with swr_scan_finish, which swaps the list in and posts SCAN_DONE; wifid's Get 11 loop then iterates it and ends with the soft EIO. */
static int lv_nreq, lv_rc;
static int lv_req(void *cx) { (void)cx; lv_nreq++; return lv_rc; }
static struct swr LT;       /* the scratch table (the glue's sw_scan_tab) */
static uint32_t lv_cs;       /* 2026-11-11: when set, the chanspec of the next lv_ev record (else 20 MHz, 0x1000 | ch) */
static void lv_ev(const uint8_t bssid[6], const char *ssid, uint32_t ch, int rssi)
{
	uint8_t b[SWR_REPLAY_BUF], *r = b + SWR_RES_HDR;
	memset(b, 0, sizeof b);
	swsta_p32(b, 0, SWR_REPLAY_BUF); swsta_p16(b, 8, SWR_SYNC); swsta_p16(b, 10, 1);
	swsta_p32(r, 0, SWR_BSS_VER); swsta_p32(r, 4, SWR_BSS_NEED); memcpy(r + 8, bssid, 6); swsta_p16(r, 16, 0x11);
	r[18] = (uint8_t)strlen(ssid); memcpy(r + 19, ssid, strlen(ssid)); swsta_p16(r, 72, lv_cs != 0u ? lv_cs : 0x1000u | ch); swsta_p16(r, 78, (uint32_t)(uint16_t)(int16_t)rssi); r[88] = (uint8_t)ch;
	swr_event(&LT, SWR_ST_PARTIAL, b, sizeof b);
}
static void lv_done_ev(void)
{
	uint8_t b[SWR_RES_HDR];
	memset(b, 0, sizeof b); swsta_p16(b, 8, SWR_SYNC);
	swr_event(&LT, SWR_ST_OK, b, sizeof b);
}
static uint32_t n_live;
static void phase_live_scan(void)
{
	const struct swr_replay rp = { k_get, NULL, k_gap, NULL };
	const struct swr_link link = SWR_REPLAY_LINK(&rp);
	const struct swr_cb cb = { u_log, u_gap, NULL };
	static const uint8_t A[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x01 }, B[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x02 };
	struct swr RL; struct a80211 GL;
	uint32_t val = 0, g0, i, off; int rc;
	uint8_t bb[6];
	char nm[8];

	memset(umem, 0, sizeof umem);
	swr_init(&RL, &link, &cb);
	swr_init(&LT, NULL, &cb);
	swr_ascan_set(&RL, lv_req, NULL);
	CHECK(swr_kept_set(&RL, (const uint8_t *)KSSID, (uint32_t)strlen(KSSID), KBSSID, 11u, -47, 0xFu) == 1);
	a80211_init(&GL, &RL.radio, NULL, &IO, "en0", NULL);
	CHECK(swr_kept_announce(&RL) == 1);
	/* 1: Set 10 asks the keep thread exactly once, answers 0, posts NO SCAN_DONE yet; the second Set 10 is refused (busy) with a GAP and does not ask again */
	lv_nreq = 0; lv_rc = 0; g_kn = 0; g0 = RL.gaps;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == 0 && lv_nreq == 1 && g_kn == 0 && RL.scanning == 1 && RL.live == 1 && RL.live_req == 1u && RL.scans == 1u);
	CHECK(strstr(s_log, "scan requested: the keep thread runs a live passive escan (all channels), SCAN_DONE follows") != NULL);
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == (int)SWR_EBUSY && lv_nreq == 1 && g_kn == 0 && RL.gaps > g0 && strstr(s_log, "scan refused: one is already running") != NULL);
	/* the old list stays readable until the result arrives: no records yet (the radio never scanned), Get 11 ends at once */
	memset(umem + 2000, 0, 1164); swsta_p16(umem, 2000 + 0x8a, 0x400);
	req_on(&GL, 'G', 11, 1164, UBASE + 2000, &val, &rc);
	CHECK(rc == (int)SWSTA_E_SCAN_END);
	/* 2: the keep thread's events: A twice (weaker, then stronger on another channel), B once, then status 0 */
	lv_ev(A, "LiveNet-1", 1, -80);
	lv_ev(B, "LiveNet-2", 36, -61);
	lv_ev(A, "LiveNet-1", 6, -55);
	lv_done_ev();
	CHECK(LT.n == 2u && LT.dup == 1u && LT.complete == 1 && LT.partial == 3u && LT.events == 4u);
	g_kn = 0;
	CHECK(swr_scan_finish(&RL, &LT, SWR_SCAN_COMPLETE, 1234u, 4u) == 1);
	CHECK(g_kn == 1 && g_kc[0] == SWSTA_EV_SCAN_DONE && RL.scanning == 0 && RL.live == 0 && RL.n == 2u && RL.complete == 1 && RL.live_done == 1u);
	CHECK(strstr(s_log, "radio scan done: complete, records=2 events=4 partial=3 dup=1 dropped=0 ignored=0 ms=1234") != NULL);
	/* 3: Get 11 iterates both records (A keeps the stronger RSSI and its channel), then ends with the soft EIO and NO GAP */
	for (i = 0; i < 3u; i++) {
		off = 2000u + i * 1164u;
		memset(umem + off, 0, 1164); swsta_p16(umem, off + 0x8a, 0x400);
		g0 = gaps_now();
		req_on(&GL, 'G', 11, 1164, UBASE + off, &val, &rc);
		if (i < 2u) {
			CHECK(rc == 0 && rw32(off + 0x08) == (i == 0 ? 6u : 36u) && rs16(off + 0x16) == (i == 0 ? -55 : -61) && memcmp(umem + off + 0x1c, i == 0 ? A : B, 6) == 0 && umem[off + 0x60] == 9u &&
			      memcmp(umem + off + 0x61, i == 0 ? "LiveNet-1" : "LiveNet-2", 9) == 0);
		} else {
			CHECK(rc == (int)SWSTA_E_SCAN_END && gaps_now() == g0);
		}
	}
	/* 4: a second scan after the first: Set 10 asks again (the old list is replaced, not appended); a result delivered with none outstanding is dropped with a GAP and posts nothing */
	g_kn = 0; g0 = RL.gaps;
	CHECK(swr_scan_finish(&RL, &LT, SWR_SCAN_COMPLETE, 1u, 1u) == 0 && g_kn == 0 && RL.live_bad == 1u && RL.gaps == g0 + 1u && RL.n == 2u);
	lv_nreq = 0;
	swr_tab_reset(&LT);
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == 0 && lv_nreq == 1 && RL.live_req == 2u);
	lv_ev(B, "LiveNet-2", 36, -70);       /* only B this time; no status 0: the keep thread's budget ran out */
	g_kn = 0; g0 = RL.gaps;
	CHECK(swr_scan_finish(&RL, &LT, SWR_SCAN_TIMEOUT, 6000u, 1u) == 1 && g_kn == 1 && g_kc[0] == SWSTA_EV_SCAN_DONE && RL.n == 1u && RL.complete == 0 && RL.gaps == g0 + 1u);
	CHECK(strstr(s_log, "radio scan done: timeout, records=1 events=1 partial=1 dup=0 dropped=0 ignored=0 ms=6000") != NULL && strstr(s_log, "live scan ended unfinished (kind 2), 1 BSS listed as a partial result") != NULL);
	memset(umem + 2000, 0, 1164); swsta_p16(umem, 2000 + 0x8a, 0x400);
	req_on(&GL, 'G', 11, 1164, UBASE + 2000, &val, &rc);
	CHECK(rc == 0 && memcmp(umem + 2000 + 0x1c, B, 6) == 0 && rs16(2000 + 0x16) == -70);
	/* the other unfinished kinds deliver too (link lost, failed, refused): SCAN_DONE posted once each, an empty list, one GAP each */
	for (i = SWR_SCAN_LINKLOST; i <= SWR_SCAN_REFUSED; i++) {
		swr_tab_reset(&LT);
		req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
		CHECK(rc == 0);
		g_kn = 0; g0 = RL.gaps;
		CHECK(swr_scan_finish(&RL, &LT, i, 0u, 0u) == 1 && g_kn == 1 && RL.n == 0u && RL.scanning == 0 && RL.gaps == g0 + 1u);
	}
	CHECK(strstr(s_log, "radio scan done: link lost, records=0") != NULL && strstr(s_log, "radio scan done: failed, records=0") != NULL && strstr(s_log, "radio scan done: refused, records=0") != NULL);
	/* 5: the table bound: 70 distinct BSSIDs -> 64 kept, 6 dropped each with a GAP, the list is full but valid */
	swr_tab_reset(&LT);
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == 0);
	g0 = LT.gaps;
	for (i = 0; i < 70u; i++) {
		memcpy(bb, A, 6); bb[4] = (uint8_t)(i >> 4); bb[5] = (uint8_t)(0x10u + (i & 15u));
		snprintf(nm, sizeof nm, "N%02u", (unsigned)i);
		lv_ev(bb, nm, 1u + (i % 11u), -90 + (int)(i % 7u));
	}
	CHECK(LT.n == SWR_MAX_BSS && LT.dropped == 0u && LT.replaced == 6u && LT.gaps == g0 + 1u);       /* review 0293 LOW 2: rssi -90 + i % 7: the 6 late records (-89..-84) replace 6 of the -90 entries; ONE GAP for the scan */
	g_kn = 0;
	CHECK(swr_scan_finish(&RL, &LT, SWR_SCAN_COMPLETE, 10u, 70u) == 1 && RL.n == SWR_MAX_BSS && RL.replaced >= 6u && strstr(s_log, "dropped=0 ignored=0 ms=10 replaced=6") != NULL);
	/* 5b: 2026-11-11 the once-per-scan record GAP is per scan: swr_tab_reset starts a new one (two 80 MHz records in a scan: one GAP; the next scan tells it again) */
	lv_cs = 0xC000u | 0x2000u | 42u;
	CHECK(LT.replaced == 6u);
	swr_tab_reset(&LT); g0 = LT.gaps;
	CHECK(LT.replaced == 0u && LT.sgap == 0u);       /* a new scan in the scratch table starts its counts and its once-per-scan GAPs */
	lv_ev(A, "W80-1", 42, -60); lv_ev(B, "W80-2", 42, -61);
	CHECK(LT.gaps == g0 + 1u && strstr(s_log, "80 MHz channel flag bit open") != NULL);
	swr_tab_reset(&LT); g0 = LT.gaps;
	lv_ev(A, "W80-1", 42, -60); lv_ev(B, "W80-2", 42, -61);
	CHECK(LT.gaps == g0 + 1u && LT.n == 2u && LT.bss[0].chflags == 0x10u);
	lv_cs = 0;
	swr_tab_reset(&LT);
	/* 6: refused while the kept link is DOWN (the re-join machine owns the engine): errno 50, a GAP, the keep thread is not asked, nothing is left pending */
	CHECK(swr_kept_clear(&RL, 0) == 1);
	lv_nreq = 0; g_kn = 0; g0 = RL.gaps;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == (int)SWR_E_NETDOWN && lv_nreq == 0 && g_kn == 0 && RL.scanning == 0 && RL.live_refused == 1u && RL.gaps == g0 + 1u && strstr(s_log, "scan refused while the kept link is DOWN (AP lost)") != NULL);
	CHECK(swr_kept_up(&RL, KBSSID, 11u, -50, 0xEu) == 1);
	/* 7: the keep thread refuses (errno 50 from the engine, busy 16): passed on, scanning stays 0; any other errno is a plain EIO; every refusal a GAP */
	lv_rc = (int)SWR_E_NETDOWN; g0 = RL.gaps;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == (int)SWR_E_NETDOWN && RL.scanning == 0 && RL.gaps == g0 + 1u);
	lv_rc = (int)SWR_EBUSY;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == (int)SWR_EBUSY && RL.scanning == 0);
	lv_rc = 99;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == (int)SWSTA_EIO && RL.scanning == 0 && strstr(s_log, "live scan refused by the keep thread (errno 99") != NULL);
	/* 8: the live scan is off for this boot (errno 6): Set 10 falls back to the boot-time replay, loudly (STALE), SCAN_DONE posted at once */
	knrec = 0;
	mk_rec(KBSSID, KSSID, 11, -47);
	lv_rc = (int)SWR_E_NXIO; g_kn = 0; g0 = RL.gaps;
	req_on(&GL, 'S', 10, 5456, UBASE, &val, &rc);
	CHECK(rc == 0 && RL.live_fallback == 1u && g_kn == 1 && g_kc[0] == SWSTA_EV_SCAN_DONE && RL.n == 1u && RL.scanning == 0 && RL.live == 0);
	CHECK(strstr(s_log, "live scan not available (off for this boot): the boot-time listing is replayed instead, STALE") != NULL);
	/* 9: the summary line carries the live counters; no SSID or BSSID text in any line of this phase (rule 4) */
	swr_summary(&RL);
	CHECK(strstr(s_log, "radio live scans requested=") != NULL);
	CHECK(strstr(s_log, "LiveNet") == NULL && strstr(s_log, "N00") == NULL && strstr(s_log, "02:11") == NULL);
	n_live = RL.live_done;
}

int main(int argc, char **argv)
{
	uint32_t i, nj = 0; int table = (argc > 1 && !strcmp(argv[1], "--table"));
	fk_setup();
	a80211_init(&G, &rad, NULL, &IO, "en0", NULL);                       /* NULL codec: the 16.7 codec */
	CHECK(G.sta.codec == &swsta_codec_167);
	memset(umem, 0, sizeof umem);
	phase_startup();
	{ int s_nj = 0; phase_not_joined(&s_nj); nj = (uint32_t)s_nj; }
	phase_scan();
	phase_join();
	phase_unknown();
	swsta_summary(&G.sta);
	{ uint32_t keep_gaps = G.sta.gaps; phase_kept(); CHECK(G.sta.gaps == keep_gaps); }
	phase_kept_down();
	phase_kept_up();
	phase_live_scan();
	/* every table row was exercised */
	for (i = 0; i < NSEL; i++)
		if (!g_sel[i].seen) { fails++; printf("FAIL selector %u %c never issued\n", (unsigned)g_sel[i].type, g_sel[i].dir); }
	/* rule 4: no SSID, passphrase or BSSID byte pattern in any log or gap line */
	CHECK(strstr(s_log, PASS_TXT) == NULL && strstr(s_log, "FakeNet") == NULL && strstr(s_log, "NoSuchNet") == NULL && strstr(s_log, KSSID) == NULL && strstr(s_log, "KeptNet") == NULL);
	/* rule 8: summary line present and gaps counted */
	CHECK(strstr(s_log, "a80211 summary requests=") != NULL);
	for (i = 0; i < NSEL; i++) {
		const char *a = g_sel[i].exp == 'A' ? "answered" : g_sel[i].exp == 'I' ? "answered(val)" : g_sel[i].exp == 'Z' ? "answered+GAP(open fields zero)" : "GAP(soft 0x66)";
		if (table || 1) printf("SEL | %u | %s | %c | %u | %s | %s\n", (unsigned)g_sel[i].type, g_sel[i].name, g_sel[i].dir, (unsigned)g_sel[i].len, g_sel[i].src, a);
	}
	printf("SEL not-joined Gets answered ENXIO: %u (types 1 9 4 16 17 2 6 103)\n", (unsigned)nj);
	printf("SEL answered=%u soft=%u gaps=%u\n", (unsigned)s_answered, (unsigned)s_soft, (unsigned)G.sta.gaps);
	if (table) printf("%s", s_log);
	if (fails) { printf("RESULT: FAIL a80211-fake-radio fails=%d\n", fails); return 1; }
	printf("RESULT: PASS a80211-fake-radio scan=%u join=%u state=%u kept=%u live=%u gaps=%u\n", (unsigned)n_scan, (unsigned)n_join, (unsigned)n_state, (unsigned)n_kept, (unsigned)n_live, (unsigned)G.sta.gaps);
	return 0;
}
