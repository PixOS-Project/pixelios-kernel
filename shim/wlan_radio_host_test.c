/* wlan_radio_host_test.c - host test of shim/sweet_wlan_radio.h, the swsta_radio backend over escan result events (P-W4, T-431f; goal W-D7).
 * A fake dongle (struct swr_link) delivers wl_escan_result events at the layout the boot-9 run parsed (obs docs/reviews/T-135a-proof.md: event 69,
 * status 8, wl_bss_info_v109; offsets [src sweet_wlan_g2.h swg_e_event]); the radio parses them; the REAL Apple80211 handler (sweet_wlan_a80211.h over
 * sweet_wlan_sta.h with its 16.7 codec, no test codec) answers Set 19 / Set 10 / Get 11 against it through the fake user memory and kev post of
 * wlan_a80211_glue_host_test.c. Rows: events > 8 accepted (boot-9 H8/H9), cap, duplicates, every refusal with its GAP line, unsupported verbs, a
 * link failure, an unfinished scan. No hardware, no stores: the radio makes none. Build: gcc -O1 -Wall -Wextra -Werror (ASan/UBSan clean).
 * Last line: RESULT: PASS|FAIL wlan_radio_host_test. */
#define A80211_GLUE_NO_MAIN 1
#include "wlan_a80211_glue_host_test.c"
#include "sweet_wlan_radio.h"

/* ---- the fake dongle: the events of one scan ---- */
struct dongle {
	uint32_t n_partial;	/* ESCAN_RESULT status-8 events, one bss each */
	int final_ok;		/* deliver the status-0 event after them */
	int link_rc;		/* the link's own return value */
	uint32_t base_ch;
	int tweak;		/* one fault at a time, see T_* */
	uint32_t calls;
};
enum { T_NONE, T_VER, T_SYNC, T_SHORT, T_SSID33, T_BSSCNT0, T_LEN, T_STATUS4, T_SAME_BSSID, T_NOCH, T_HIDDEN, T_COUNT2, T_SYNC_HI, T_WEAKLATE, T_TIE, T_LASTWEAK, T_EQWEAK, T_WIDE80 };

#define RES_LEN (SWR_RES_HDR + 96u)
static void mk_res(uint8_t *d, const struct dongle *dg, uint32_t i)
{
	uint8_t *r = d + SWR_RES_HDR;
	char nm[8];
	memset(d, 0, RES_LEN);
	d[4] = 109;
	d[8] = (uint8_t)SWR_SYNC; d[9] = (uint8_t)(SWR_SYNC >> 8);
	d[10] = dg->tweak == T_BSSCNT0 ? 0 : dg->tweak == T_COUNT2 ? 2 : 1;
	r[0] = dg->tweak == T_VER ? 110 : 109;
	r[4] = dg->tweak == T_LEN ? 80 : 96;
	r[8] = 0x02; r[9] = 0x11; r[10] = 0x22; r[11] = 0x33;
	if (dg->tweak == T_SAME_BSSID) { r[12] = 0x44; r[13] = 0x50; }
	else { r[12] = (uint8_t)(i >> 8); r[13] = (uint8_t)i; }
	r[16] = (i & 1u) ? 0x11 : 0x01;						/* capability: ESS, +privacy on odd records */
	snprintf(nm, sizeof nm, "net%u", (unsigned)(i % 100u));
	r[18] = dg->tweak == T_SSID33 ? 33 : dg->tweak == T_HIDDEN ? 0 : (uint8_t)strlen(nm);
	if (dg->tweak == T_SSID33) memset(r + 19, 'a', 32); else memcpy(r + 19, nm, strlen(nm));
	r[72] = (uint8_t)(0x03u + i); r[73] = dg->tweak == T_WIDE80 ? 0x20 : 0x10;				/* chanspec: low byte 3 + i */
	{ int16_t rssi = (int16_t)(-40 - (int)(dg->tweak == T_SAME_BSSID ? i * 5u : i % 50u)); if (dg->tweak == T_WEAKLATE && i >= 64u) rssi = -100; if (dg->tweak == T_TIE) rssi = -60; if (dg->tweak == T_LASTWEAK) rssi = i == 63u ? -100 : i >= 64u ? -20 : (int16_t)(-40 - (int)(i % 50u)); if (dg->tweak == T_EQWEAK) rssi = (i == 10u || i == 20u || i == 63u) ? -100 : i >= 64u ? -20 : (int16_t)(-40 - (int)(i % 50u)); r[78] = (uint8_t)rssi; r[79] = (uint8_t)((uint16_t)rssi >> 8); }
	r[88] = dg->tweak == T_NOCH ? 0 : (uint8_t)(dg->base_ch + (i % 8u) * 5u);
}

static struct dongle DG;
static int link_escan(void *cx, swr_sink_fn sink, void *sink_cx)
{
	struct dongle *dg = cx;
	uint8_t d[RES_LEN];
	uint32_t i;
	dg->calls++;
	if (dg->link_rc != 0) { mk_res(d, dg, 0); sink(sink_cx, SWR_ST_PARTIAL, d, RES_LEN); return dg->link_rc; }	/* one event, then the failure */
	for (i = 0; i < dg->n_partial; i++) {
		mk_res(d, dg, i);
		if (dg->tweak == T_SYNC) { d[8] = 0x21; d[9] = 0x43; }
		if (dg->tweak == T_SYNC_HI) { d[8] = 0x34; d[9] = 0x13; }       /* review 0292 F2: 0x1334, one above the last keep scan id */
		if (dg->tweak == T_STATUS4) { sink(sink_cx, 4u, d, RES_LEN); continue; }
		sink(sink_cx, SWR_ST_PARTIAL, d, dg->tweak == T_SHORT ? SWR_RES_HDR + 40u : dg->tweak == T_HIDDEN ? RES_LEN : RES_LEN);
	}
	if (dg->final_ok) { mk_res(d, dg, 0); d[10] = 0; sink(sink_cx, SWR_ST_OK, d, SWR_RES_HDR); }
	return 0;
}

static struct swr R;
static void radio_reset(uint32_t n, int final_ok, int tweak)
{
	static const struct swr_cb cb = { u_log, u_gap, NULL };
	struct swr_link lk;
	memset(umem, 0, sizeof umem); memset(s_log, 0, sizeof s_log); memset(g_k, 0, sizeof g_k);
	n_copyin = n_copyout = g_fault_in = g_fault_out = g_nk = g_kev_fail = g_kev_id = 0; s_gaps = 0; g_locked = g_lock_n = g_lock_bad = 0;
	memset(&DG, 0, sizeof DG); DG.n_partial = n; DG.final_ok = final_ok; DG.tweak = tweak; DG.base_ch = 1;
	lk.escan = link_escan; lk.cx = &DG;
	swr_init(&R, &lk, &cb);
	a80211_init(&G, &R.radio, NULL, &IO, "en0", NULL);      /* NULL codec = the 16.7 codec of T-853c */
}

static int power_on(void) { memset(umem, 0, 24); umem[0] = 1; umem[4] = 1; umem[8] = 1; return set(SWSTA_T_POWER, 24, UBASE); }  /* 16.7 codec: {version 1, N 1, state 1} [RE-A] */
/* Set 10 then Get 11 until the end errno; returns the number of records, *end = the errno that stopped the loop */
static uint32_t scan_and_read(int *start_rc, int *end)
{
	uint32_t n = 0;
	int rc;
	*start_rc = set(SWSTA_T_SCAN_REQ, 5456, UBASE);
	while ((rc = get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000)) == 0 && n < 200) n++;
	*end = rc;
	return n;
}
static uint32_t gaps_of(const char *what) { return s_count(what); }

static void test_verbs(void)
{
	int st = 7, rc;
	struct swsta_bss b;
	struct swsta_status ss;
	struct swsta_join j;
	radio_reset(0, 1, T_NONE);
	CHECK(R.radio.power(R.radio.ctx, SWSTA_PWR_GET, &st) == 0 && st == 0);
	CHECK(R.radio.scan_start(R.radio.ctx) == (int)SWSTA_EIO && DG.calls == 0 && gaps_of("scan refused: radio is off") == 1);        /* off: refused loudly, no link call */
	CHECK(R.radio.power(R.radio.ctx, SWSTA_PWR_ON, &st) == 0 && st == 1 && gaps_of("power is a flag only") == 1);                   /* the flag nature is said, not hidden */
	CHECK(R.radio.power(R.radio.ctx, 9, &st) == (int)SWSTA_EINVAL && gaps_of("power op unknown") == 1);
	CHECK(R.radio.power(R.radio.ctx, SWSTA_PWR_OFF, &st) == 0 && st == 0);
	CHECK(R.radio.bss_next(R.radio.ctx, &b) == 0);                                                                                    /* empty list: end */
	memset(&j, 0, sizeof j);
	CHECK(R.radio.join(R.radio.ctx, &j) == (int)SWSTA_EOPNOTSUPP && gaps_of("join not supported") == 1);                              /* rule 8: EOPNOTSUPP + gap */
	CHECK(R.radio.leave(R.radio.ctx) == (int)SWSTA_EOPNOTSUPP && gaps_of("leave not supported") == 1);
	memset(&ss, 0xff, sizeof ss);
	CHECK(R.radio.status(R.radio.ctx, &ss) == 0 && ss.joined == 0 && ss.ssid_len == 0);
	/* no link: power on and scan refused */
	swr_init(&R, NULL, &(struct swr_cb){ u_log, u_gap, NULL });
	CHECK(R.radio.power(R.radio.ctx, SWSTA_PWR_ON, &st) == (int)SWSTA_EIO && gaps_of("no escan link attached") == 1);
	R.on = 1;
	rc = R.radio.scan_start(R.radio.ctx);
	CHECK(rc == (int)SWSTA_EIO && gaps_of("scan refused: no escan link attached") == 1);
}

/* the whole chain: Set 19 on -> Set 10 -> SCAN_DONE kev -> Get 11 x N with the 16.7 record layout -> end errno */
static void test_end_to_end(void)
{
	uint32_t n, i, done_ev = 0;
	int sr, end;
	radio_reset(3, 1, T_NONE);
	CHECK(power_on() == 0 && g_nk == 1);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 3 && end == (int)SWSTA_E_SCAN_END);
	for (i = 0; i < g_nk; i++) if (rd32(kev_at(i), 0x14) == SWSTA_EV_SCAN_DONE) done_ev++;
	CHECK(done_ev == 1);
	CHECK(R.scans == 1 && R.events == 4 && R.parsed == 3 && R.dup == 0 && R.dropped == 0 && R.ignored == 0 && R.partial == 3);
	CHECK(gaps_of("scan-done status 0 not seen") == 0);                                                                              /* complete: no gap for it */
	/* decode the records at the T-853c offsets */
	radio_reset(3, 1, T_NONE);
	CHECK(power_on() == 0 && set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	for (i = 0; i < 3; i++) {
		const uint8_t *rec;
		char nm[8];
		uint32_t ch, bs;
		CHECK(get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 3000 + i * 1164u) == 0);
		rec = umem + 3000 + i * 1164u;
		snprintf(nm, sizeof nm, "net%u", (unsigned)i);
		memcpy(&ch, rec + 0x08, 4);
		CHECK(rec[0x60] == strlen(nm) && memcmp(rec + 0x61, nm, strlen(nm)) == 0);                                                /* ssid_len +0x60, ssid +0x61 */
		CHECK(ch == 1u + (i % 8u) * 5u);                                                                                          /* channel s32 +0x08 = ctl_ch */
		CHECK((int16_t)(rec[0x16] | rec[0x17] << 8) == (int16_t)(-40 - (int)i));                                                    /* rssi s16 +0x16 */
		bs = rec[0x1c];
		CHECK(bs == 0x02 && rec[0x1d] == 0x11 && rec[0x1f] == 0x33 && rec[0x20] == 0 && rec[0x21] == i);                               /* bssid +0x1c */
		CHECK((rec[0x1a] & 0x10) == ((i & 1u) ? 0x10 : 0));                                                                       /* privacy bit follows the capability */
	}
	CHECK(get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 9000) == (int)SWSTA_E_SCAN_END);
	CHECK(G.faults == 0 && G.bad == 0 && g_lock_bad == 0);
	/* a second scan replaces the list and rewinds */
	DG.n_partial = 2; memset(s_log, 0, sizeof s_log);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	for (i = 0; get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 9000) == 0 && i < 10; i++) { }
	CHECK(i == 2 && R.scans == 2);
	/* rule 4: the run log carries no SSID or BSSID byte of the records */
	CHECK(strstr(s_log, "net0") == NULL && strstr(s_log, "net1") == NULL);
	swr_summary(&R);
	CHECK(s_count("radio scans=2 events=") == 1 && s_count("gaps=") >= 1);
}

static void test_many_events(void)
{
	int sr, end;
	uint32_t n;
	/* boot-9 H8/H9: the 9th and later events must be accepted; DHD posts 32 event buffers */
	radio_reset(20, 1, T_NONE);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 20 && R.parsed == 20 && R.events == 21 && R.ignored == 0 && R.dropped == 0);       /* events > 8 accepted */
	/* the cap (review 0293 LOW 2): 70 distinct BSSIDs -> the strongest 64 are listed: the 6 late records (rssi -54..-59) replace the 6 weakest entries (-84..-89, i = 44..49), one GAP for the scan, nothing dropped */
	radio_reset(70, 1, T_NONE);
	CHECK(power_on() == 0);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	{
		uint32_t seen[70], k, nseen = 0;
		memset(seen, 0, sizeof seen);
		while (get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000) == 0 && nseen < 100) { seen[umem[2000 + 0x21] % 70u] += 1u; nseen++; }
		CHECK(nseen == 64);
		for (k = 0; k < 70; k++) CHECK(seen[k] == ((k >= 44 && k <= 49) ? 0u : 1u));
	}
	CHECK(R.parsed == 64 && R.replaced == 6 && R.dropped == 0 && gaps_of("scan list full (64 BSSID)") == 1);
	/* weaker late records are dropped (still one GAP); equal ones too (a tie does not replace) */
	radio_reset(70, 1, T_WEAKLATE);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 64 && R.parsed == 64 && R.replaced == 0 && R.dropped == 6 && gaps_of("scan list full (64 BSSID)") == 1);
	radio_reset(70, 1, T_TIE);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 64 && R.replaced == 0 && R.dropped == 6 && gaps_of("scan list full (64 BSSID)") == 1);
	/* the weakest entry is the LAST one (index 63): the late strong records replace it first; with equal weakest entries the first of them goes */
	radio_reset(65, 1, T_LASTWEAK);
	CHECK(power_on() == 0);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	{
		uint32_t k, nseen = 0, seen[65];
		memset(seen, 0, sizeof seen);
		while (get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000) == 0 && nseen < 100) { seen[umem[2000 + 0x21] % 65u] += 1u; nseen++; }
		CHECK(nseen == 64 && seen[63] == 0u && seen[64] == 1u);
		for (k = 0; k < 63; k++) CHECK(seen[k] == 1u);
	}
	radio_reset(65, 1, T_EQWEAK);
	CHECK(power_on() == 0);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	{
		uint32_t nseen = 0, seen[65];
		memset(seen, 0, sizeof seen);
		while (get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000) == 0 && nseen < 100) { seen[umem[2000 + 0x21] % 65u] += 1u; nseen++; }
		CHECK(nseen == 64 && seen[10] == 0u && seen[20] == 1u && seen[63] == 1u && seen[64] == 1u);
	}
	/* the list-full GAP and the 80 MHz GAP are two GAPs, each once (they do not share a bit) */
	radio_reset(70, 1, T_WIDE80);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 64 && gaps_of("scan list full (64 BSSID)") == 1 && gaps_of("80 MHz channel flag bit open") == 1);
	/* the GAP is per scan: a second scan tells it again */
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0 && gaps_of("scan list full (64 BSSID)") == 2);
}

static void test_dups_and_faults(void)
{
	int sr, end;
	uint32_t n;
	struct swsta_bss b;
	/* the same BSSID 5 times (rssi -40, -45, ...): one entry, the strongest */
	radio_reset(5, 1, T_SAME_BSSID);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(n == 1 && R.parsed == 1 && R.dup == 4);
	radio_reset(5, 1, T_SAME_BSSID);
	CHECK(power_on() == 0 && R.radio.scan_start(R.radio.ctx) == 0 && R.radio.bss_next(R.radio.ctx, &b) == 1 && b.rssi == -40 && b.bssid[4] == 0x44);
	/* no ctl_ch: the chanspec low byte is the channel */
	radio_reset(1, 1, T_NOCH);
	CHECK(power_on() == 0 && R.radio.scan_start(R.radio.ctx) == 0 && R.radio.bss_next(R.radio.ctx, &b) == 1 && b.channel == 3u);
	/* hidden SSID: length 0 is a record, not an error */
	radio_reset(1, 1, T_HIDDEN);
	CHECK(power_on() == 0 && R.radio.scan_start(R.radio.ctx) == 0 && R.radio.bss_next(R.radio.ctx, &b) == 1 && b.ssid_len == 0);
	/* bss_count 2: only the first record parsed, said */
	radio_reset(1, 1, T_COUNT2);
	CHECK(power_on() == 0 && R.radio.scan_start(R.radio.ctx) == 0 && R.parsed == 1 && gaps_of("bss_count above 1") == 1);
	/* every bad record is ignored, counted and has a gap line */
	{
		static const struct { int tweak; const char *gap; } f[] = {
			{ T_VER, "bss_info version is not 109" }, { T_SYNC, "sync_id differs from ours" }, { T_SYNC_HI, "sync_id differs from ours" }, { T_SHORT, "without a full bss record" },
			{ T_SSID33, "ssid_len above 32" }, { T_BSSCNT0, "without a full bss record" }, { T_LEN, "bss_info length shorter" }, { T_STATUS4, "status 4 not handled" },
		};
		uint32_t k;
		for (k = 0; k < sizeof f / sizeof f[0]; k++) {
			radio_reset(2, 1, f[k].tweak);
			CHECK(power_on() == 0 && R.radio.scan_start(R.radio.ctx) == 0);
			CHECK(R.parsed == 0 && R.ignored == 2 && gaps_of(f[k].gap) == 2);
			CHECK(R.radio.bss_next(R.radio.ctx, &b) == 0);
			CHECK(R.gaps >= 2);
		}
	}
	/* a result event shorter than the 12-byte header, and a NULL one */
	radio_reset(0, 0, T_NONE);
	swr_event(&R, SWR_ST_PARTIAL, (const uint8_t *)"x", 1);
	swr_event(&R, SWR_ST_PARTIAL, NULL, 200);
	CHECK(R.ignored == 2 && gaps_of("shorter than the 12-byte header") == 2);
}

static void test_unfinished_and_link_failure(void)
{
	int sr, end;
	uint32_t n, i, done_ev = 0;
	/* the boot-9 state: 8 partial results, no status-0 event: the list is delivered, SCAN_DONE posted, and the gap says it is partial */
	radio_reset(8, 0, T_NONE);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 8 && gaps_of("scan-done status 0 not seen: listing 8 BSS as a partial result") == 1);
	for (i = 0; i < g_nk; i++) if (rd32(kev_at(i), 0x14) == SWSTA_EV_SCAN_DONE) done_ev++;
	CHECK(done_ev == 1);
	/* a link failure: EIO to the library, no SCAN_DONE, the gap names the code, the partial list is not served as complete */
	radio_reset(4, 1, T_NONE);
	DG.link_rc = 110;
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr != 0 && n <= 1u && gaps_of("escan link failed rc 110") == 1);
	for (done_ev = 0, i = 0; i < g_nk; i++) if (rd32(kev_at(i), 0x14) == SWSTA_EV_SCAN_DONE) done_ev++;
	CHECK(done_ev == 0);
	/* a scan already running is refused (a sink that re-enters scan_start) */
	radio_reset(0, 0, T_NONE);
	R.on = 1; R.scanning = 1;
	CHECK(R.radio.scan_start(R.radio.ctx) == 16 && gaps_of("already running") == 1 && DG.calls == 0);
}

/* review T-431f MEDIUM 3: exact-size heap buffers so ASan sees any over-read; the bounds are 101 (12 + 89) bytes and bss length 89 */
static void test_bounds_and_picks(void)
{
	struct dongle dg;
	uint32_t k, lens[4] = { 100, 101, 101, 101 };
	struct swsta_bss b;
	memset(&dg, 0, sizeof dg); dg.base_ch = 1;
	radio_reset(0, 0, T_NONE);
	for (k = 0; k < 4; k++) {
		uint8_t full[RES_LEN];
		uint8_t *e = malloc(lens[k]);
		uint32_t before = R.parsed, ig = R.ignored;
		mk_res(full, &dg, k);
		if (k == 2) full[SWR_RES_HDR + 4] = 88;       /* bss length 88 < 89 */
		if (k == 3) full[SWR_RES_HDR + 4] = 89;
		memcpy(e, full, lens[k]);
		swr_event(&R, SWR_ST_PARTIAL, e, lens[k]);
		free(e);
		CHECK(k == 1 || k == 3 ? (R.parsed == before + 1 && R.ignored == ig) : (R.parsed == before && R.ignored == ig + 1));
	}
	/* review 0292 re-check N2: the sync range of the keep scans (0x1235 .. 0x1333) is taken, 0x1233 and 0x1334 are ignored with a GAP */
	radio_reset(0, 0, T_NONE);
	{
		uint8_t e[RES_LEN];
		static const uint16_t ids[4] = { 0x1235u, 0x1333u, 0x1334u, 0x1233u };
		static const unsigned want_n[4] = { 1u, 2u, 2u, 2u };
		unsigned k;
		for (k = 0; k < 4u; k++) {
			mk_res(e, &dg, 4u + k); e[8] = (uint8_t)(ids[k] & 0xFFu); e[9] = (uint8_t)(ids[k] >> 8);
			swr_event(&R, SWR_ST_PARTIAL, e, RES_LEN);
			CHECK(R.n == want_n[k]);
		}
		CHECK(R.ignored == 2u);
	}
	/* keep-stronger: weaker first, stronger second, same BSSID -> the stronger rssi; a 32-byte SSID is a record */
	radio_reset(0, 0, T_NONE);
	{
		uint8_t e[RES_LEN];
		dg.tweak = T_SAME_BSSID;
		mk_res(e, &dg, 4); swr_event(&R, SWR_ST_PARTIAL, e, RES_LEN);       /* rssi -60 */
		mk_res(e, &dg, 0); swr_event(&R, SWR_ST_PARTIAL, e, RES_LEN);       /* rssi -40 */
		mk_res(e, &dg, 2); swr_event(&R, SWR_ST_PARTIAL, e, RES_LEN);       /* rssi -50: weaker, ignored as an update */
		CHECK(R.n == 1 && R.dup == 2 && R.bss[0].rssi == -40);
		dg.tweak = T_NONE;
		mk_res(e, &dg, 9); e[SWR_RES_HDR + 18] = 32; memset(e + SWR_RES_HDR + 19, 'z', 32);
		swr_event(&R, SWR_ST_PARTIAL, e, RES_LEN);
		CHECK(R.n == 2 && R.bss[1].ssid_len == 32 && R.bss[1].ssid[31] == 'z');
	}
	/* no event callback: SCAN_DONE is not silently dropped */
	radio_reset(1, 1, T_NONE);
	R.on = 1; R.radio.up = NULL;
	CHECK(R.radio.scan_start(R.radio.ctx) == 0 && gaps_of("no event callback set") == 1);
	(void)b;
}

/* ---- T-08fd: the escan link over the boot-time scan. The REAL G2 L23 code (sweet_wlan_g2e.h / sweet_wlan_g2.h, the bytes of xnu patch 0174, compiled in wlan_radio_g2_scan.c) runs against the fake
 * dongle of wlan_g2_host_test.c and records the ESCAN_RESULT events; sweet_wlan_g2_escan_rec() is the getter the kernel glue (patch 0201) hands to swr_replay_escan, used here unchanged. ---- */
extern int g2l_scan(int mode, unsigned *nbss, unsigned *lv, const char **logp);
extern unsigned g2l_fails(void);
extern int sweet_wlan_g2_escan_rec(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap);
extern uint32_t sweet_wlan_g2_escan_lost(void);
extern unsigned g2l_fill(unsigned n);

static uint32_t rec_calls, rec_max_i;
static int rec_get_counting(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap)
{
	rec_calls++;
	if (i > rec_max_i) rec_max_i = i;
	return sweet_wlan_g2_escan_rec(i, status, out, cap);
}
static int rec_get_fail(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap)
{
	(void)cap;
	if (i >= 2) return -1;
	*status = SWR_ST_PARTIAL; memset(out, 0, SWR_REPLAY_BUF);
	return (int)SWR_REPLAY_BUF;
}
static int rec_get_endless(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap)
{
	(void)i; (void)cap;
	*status = SWR_ST_PARTIAL; memset(out, 0, SWR_REPLAY_BUF);
	return (int)SWR_REPLAY_BUF;
}

/* a radio whose escan link is swr_replay_escan over `get`, the gaps and logs into the glue test's log */
static void replay_reset(swr_rec_fn get)
{
	static struct swr_replay rp;
	static const struct swr_cb cb = { u_log, u_gap, NULL };
	struct swr_link lk;
	memset(umem, 0, sizeof umem); memset(s_log, 0, sizeof s_log); memset(g_k, 0, sizeof g_k);
	n_copyin = n_copyout = g_fault_in = g_fault_out = g_nk = g_kev_fail = g_kev_id = 0; s_gaps = 0; g_locked = g_lock_n = g_lock_bad = 0;
	rp.get = get; rp.lost = sweet_wlan_g2_escan_lost; rp.gap = u_gap; rp.gap_cx = NULL; rec_calls = rec_max_i = 0;
	lk = (struct swr_link)SWR_REPLAY_LINK(&rp);       /* the kernel glue's wiring (wlan_a80211_glue.c.in) */
	swr_init(&R, &lk, &cb);
	a80211_init(&G, &R.radio, NULL, &IO, "en0", NULL);
}

static void test_g2_link(void)
{
	static const uint8_t chs[8] = { 1, 6, 36, 4, 132, 20, 149, 250 };       /* the control channels of the fake dongle's records (wlan_g2_host_dongle.h ge_escan) */
	unsigned nbss = 0, lv = 0, i, k;
	const char *lg = "";
	int sr, end;
	uint32_t n, done_ev;

	/* (1) 3 PARTIAL + SUCCESS: the G2 run reaches level 23, the radio lists the same 3 BSS through the link, scan-done status 0 */
	CHECK(g2l_scan(0, &nbss, &lv, &lg) == 0 && lv == 23u && nbss == 3u && strstr(lg, "L23 scan done status 0 bss 3") != NULL);
	replay_reset(rec_get_counting);
	CHECK(power_on() == 0 && g_nk == 1);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 3 && end == (int)SWSTA_E_SCAN_END);
	CHECK(R.scans == 1 && R.events == 4 && R.parsed == 3 && R.dup == 0 && R.dropped == 0 && R.ignored == 0 && R.partial == 3 && R.complete == 1);
	CHECK(gaps_of("scan-done status 0 not seen") == 0 && gaps_of("scan list replayed from the boot-time scan (stale, 4 events)") == 1);       /* stale, said once per scan */
	CHECK(rec_calls == 5 && rec_max_i == 4);                                                                                         /* 4 events + the end marker, nothing more */
	for (done_ev = 0, i = 0; i < g_nk; i++) if (rd32(kev_at(i), 0x14) == SWSTA_EV_SCAN_DONE) done_ev++;
	CHECK(done_ev == 1);
	for (k = 0; k < 3; k++) {
		unsigned lc = 0, lr = 0;
		int rs = 0;
		char key[32];
		const char *l;
		snprintf(key, sizeof key, "L23 bss %u chan ", k);
		l = strstr(lg, key);
		CHECK(l != NULL && sscanf(l + strlen(key), "%u rssi %d", &lc, &rs) == 2);
		(void)lr;
		CHECK(R.bss[k].channel == lc && R.bss[k].channel == chs[k % 8] && R.bss[k].rssi == rs);                                  /* what G2 printed for the BSS is what the radio lists */
		CHECK(R.bss[k].bssid[0] == 0x02 && R.bss[k].bssid[4] == 0x44 && R.bss[k].bssid[5] == 0x50 + k && R.bss[k].ssid_len == 4 && memcmp(R.bss[k].ssid, "net0", 3) == 0 && R.bss[k].ssid[3] == '0' + k);
	}
	/* a second scan replays the same record (no second chip scan: the boot-time list, stale) */
	memset(g_k, 0, sizeof g_k); g_nk = 0; s_gaps = 0; memset(s_log, 0, sizeof s_log); R.on = 1;
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 3 && R.scans == 2 && gaps_of("(stale, 4 events)") == 1);

	/* (2) ten PARTIAL results over 8 distinct BSSIDs */
	CHECK(g2l_scan(1, &nbss, &lv, &lg) == 0 && lv == 23u && nbss == 10u);
	replay_reset(sweet_wlan_g2_escan_rec);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 8 && end == (int)SWSTA_E_SCAN_END && R.events == 11 && R.parsed == 8 && R.dup == 2 && R.complete == 1 && gaps_of("(stale, 11 events)") == 1);       /* the fake dongle repeats 8 BSSIDs: 10 results = 8 BSS + 2 duplicates, as the 8 BSS of boot 9 */

	/* (3) no result at all in the 8000 ms budget: the record is empty, the scan is refused loudly (never a silent empty list) */
	CHECK(g2l_scan(2, &nbss, &lv, &lg) == 0 && lv == 22u && nbss == 0u);
	replay_reset(sweet_wlan_g2_escan_rec);
	CHECK(power_on() == 0);
	sr = set(SWSTA_T_SCAN_REQ, 5456, UBASE);
	CHECK(sr != 0 && R.n == 0 && gaps_of("no boot-time scan recorded") == 1 && gaps_of("escan link failed rc") == 1 && R.events == 0);

	/* (4) 2 PARTIAL but no SUCCESS: the 2 BSS are listed, the radio says the done status was not seen */
	CHECK(g2l_scan(3, &nbss, &lv, &lg) == 0 && lv == 22u && nbss == 2u);
	replay_reset(sweet_wlan_g2_escan_rec);
	CHECK(power_on() == 0);
	n = scan_and_read(&sr, &end);
	CHECK(sr == 0 && n == 2 && R.complete == 0 && R.events == 2 && gaps_of("scan-done status 0 not seen") == 1 && gaps_of("(stale, 2 events)") == 1);

	/* (5) the link's own faults: a getter error mid-record stops the replay with a gap, a getter that never ends is cut at SWR_REPLAY_MAX events */
	replay_reset(rec_get_fail);
	CHECK(power_on() == 0);
	sr = set(SWSTA_T_SCAN_REQ, 5456, UBASE);
	CHECK(sr != 0 && gaps_of("record unreadable at event 2 (rc -1), replay stopped") == 1);
	replay_reset(rec_get_endless);
	CHECK(power_on() == 0);
	sr = set(SWSTA_T_SCAN_REQ, 5456, UBASE);
	CHECK(sr == 0 && R.events == SWR_REPLAY_MAX && gaps_of("(stale, 64 events)") == 1);
	CHECK(swr_replay_escan(NULL, swr_event, &R) == (int)SWSTA_EIO);
	{
		uint8_t small[SWR_REPLAY_BUF - 1];
		uint32_t st = 0;
		CHECK(g2l_scan(0, &nbss, &lv, &lg) == 0 && sweet_wlan_g2_escan_rec(0, &st, small, sizeof small) == -1 && sweet_wlan_g2_escan_rec(4, &st, small, sizeof small) == 0);       /* too small: refused, not truncated */
	}
	/* (6) the record bounds: 65 events into the 64 slots keep 64, lose 1 and say so (a full record is never replayed as if complete); the 64th is readable, the 65th is not there */
	CHECK(g2l_fill(65u) == 1u);       /* exactly one gap: the 65th result */
	{
		uint8_t b[SWR_REPLAY_BUF];
		uint32_t st = 0;
		CHECK(sweet_wlan_g2_escan_lost() == 1u && sweet_wlan_g2_escan_rec(63, &st, b, sizeof b) == (int)SWR_REPLAY_BUF && st == 63u && sweet_wlan_g2_escan_rec(64, &st, b, sizeof b) == 0);
	}
	replay_reset(sweet_wlan_g2_escan_rec);
	CHECK(power_on() == 0);
	sr = set(SWSTA_T_SCAN_REQ, 5456, UBASE);
	CHECK(sr == 0 && R.events == SWR_REPLAY_MAX && gaps_of("(stale, 64 events, 1 LOST: record full)") == 1);
	CHECK(g2l_fails() == 0);
}

/* ---- 2026-11-11 scan records (W-D3): the keep-scan events carry the whole wl_bss_info_v109 (fixed part 132 B + IEs); the radio fills rates / beacon / noise / SNR / channel flags / IEs and the Apple80211 record shows them at the W-D3 offsets ---- */
struct rich {
	uint8_t ie[1200]; uint32_t ielen;	/* the IE block as the firmware sends it */
	uint32_t ieoff;				/* ie_offset field (0 = 132) */
	uint32_t ielen_fld;			/* ie_length field (0 = ielen) */
	uint32_t rlen;				/* record length field (0 = ieoff + ielen) */
	uint32_t evrec;				/* record bytes actually in the event (0 = all of ieoff + ielen) */
	uint32_t chsp, nrates; uint8_t rates[20];
	int noise, snr; uint32_t beacon;
};
static struct rich RS[10];
static uint32_t n_rs;
static int link_rich(void *cx, swr_sink_fn sink, void *sink_cx)
{
	static uint8_t d[SWR_RES_HDR + 132 + 1200 + 64];
	uint32_t i, k;
	(void)cx;
	for (i = 0; i < n_rs; i++) {
		const struct rich *q = &RS[i];
		uint8_t *r = d + SWR_RES_HDR;
		const uint32_t ieoff = q->ieoff ? q->ieoff : 132u, total = ieoff + q->ielen, rl = q->rlen ? q->rlen : total, il = q->ielen_fld ? q->ielen_fld : q->ielen;
		memset(d, 0, sizeof d);
		d[4] = 109; d[8] = (uint8_t)SWR_SYNC; d[9] = (uint8_t)(SWR_SYNC >> 8); d[10] = 1;
		r[0] = 109;
		r[4] = (uint8_t)rl; r[5] = (uint8_t)(rl >> 8);
		r[8] = 0x02; r[9] = 0x11; r[10] = 0x22; r[11] = 0x33; r[12] = 0x44; r[13] = (uint8_t)(0x60u + i);
		r[14] = (uint8_t)q->beacon; r[15] = (uint8_t)(q->beacon >> 8);
		r[16] = 0x11; r[17] = 0x04;		/* capability: ESS + privacy + short slot */
		r[18] = 5; memcpy(r + 19, "rich0", 5); r[19 + 4] = (uint8_t)('0' + i);
		r[52] = (uint8_t)q->nrates;
		for (k = 0; k < 16 && k < q->nrates; k++) r[56 + k] = q->rates[k];
		r[72] = (uint8_t)q->chsp; r[73] = (uint8_t)(q->chsp >> 8);
		r[78] = (uint8_t)(0x100u - 50u); r[79] = 0xFF;
		r[80] = (uint8_t)(int8_t)q->noise;
		r[88] = (uint8_t)(q->chsp & 0xFFu);
		r[116] = (uint8_t)ieoff; r[117] = (uint8_t)(ieoff >> 8);
		r[120] = (uint8_t)il; r[121] = (uint8_t)(il >> 8);
		r[124] = (uint8_t)(int16_t)q->snr; r[125] = (uint8_t)((uint16_t)(int16_t)q->snr >> 8);
		memcpy(r + ieoff, q->ie, q->ielen);
		sink(sink_cx, SWR_ST_PARTIAL, d, SWR_RES_HDR + (q->evrec ? q->evrec : total));
	}
	memset(d, 0, SWR_RES_HDR); d[8] = (uint8_t)SWR_SYNC; d[9] = (uint8_t)(SWR_SYNC >> 8);
	sink(sink_cx, SWR_ST_OK, d, SWR_RES_HDR);
	return 0;
}

static uint32_t ie_put(uint8_t *p, uint8_t id, const void *dat, uint32_t n) { p[0] = id; p[1] = (uint8_t)n; memcpy(p + 2, dat, n); return n + 2u; }
/* a real-shaped block: SSID, 4 rates, DS, RSN (CCMP group + pairwise, AKM 2 = PSK), a vendor IE; 46 bytes */
#define IE_REAL_LEN 46u
#define IE_REAL_HEAD 16u	/* SSID + rates + DS */
static uint32_t ie_real(uint8_t *p)
{
	static const uint8_t rates[4] = { 0x82, 0x84, 0x8b, 0x96 };
	static const uint8_t ds[1] = { 6 };
	static const uint8_t rsn[20] = { 1, 0, 0x00, 0x0f, 0xac, 4, 1, 0, 0x00, 0x0f, 0xac, 4, 1, 0, 0x00, 0x0f, 0xac, 2, 0, 0 };
	static const uint8_t ven[6] = { 0x00, 0x10, 0x18, 0x02, 0x00, 0x10 };
	uint32_t o = 0;
	o += ie_put(p + o, 0, "rich0", 5); o += ie_put(p + o, 1, rates, 4); o += ie_put(p + o, 3, ds, 1);
	o += ie_put(p + o, 48, rsn, 20); o += ie_put(p + o, 221, ven, 6);
	return o;
}
static uint32_t ie_fill(uint8_t *p, uint32_t n)       /* a vendor IE of n bytes in total, OUI 00:10:18 (not a security IE) */
{
	uint8_t v[255];
	memset(v, 0xA5, sizeof v); v[0] = 0x00; v[1] = 0x10; v[2] = 0x18;
	return ie_put(p, 221, v, n - 2u);
}
static uint8_t REC[10][1164];
static uint32_t run_rich(uint32_t n)
{
	uint32_t i, got = 0;
	n_rs = n;
	radio_reset(0, 1, T_NONE);
	R.link.escan = link_rich; R.link.cx = NULL;
	CHECK(power_on() == 0);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	for (i = 0; i < 10; i++) {
		if (get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000) != 0) break;
		memcpy(REC[i], umem + 2000, 1164); got++;
	}
	return got;
}
static uint32_t r16(const uint8_t *r, uint32_t o) { return (uint32_t)r[o] | (uint32_t)r[o + 1] << 8; }
static uint32_t r32(const uint8_t *r, uint32_t o) { return r16(r, o) | r16(r, o + 2) << 16; }
static void rich_reset(void) { memset(RS, 0, sizeof RS); }
static void test_scan_records(void)
{
	static const uint8_t rates4[4] = { 0x82, 0x04, 0x0b, 0x96 };
	uint8_t want[600];
	uint32_t i;
	/* (1) a real-shaped record: every field at its W-D3 offset */
	rich_reset();
	RS[0].ielen = ie_real(RS[0].ie); memcpy(want, RS[0].ie, RS[0].ielen);
	CHECK(RS[0].ielen == IE_REAL_LEN);
	RS[0].chsp = 0x1000u | 6u; RS[0].nrates = 4; memcpy(RS[0].rates, rates4, 4); RS[0].noise = -90; RS[0].snr = 40; RS[0].beacon = 100;
	CHECK(run_rich(1) == 1u);
	CHECK(r32(REC[0], 0x08) == 6u && r32(REC[0], 0x0c) == 0x0au);                      /* channel, flags 2.4 GHz | 20 MHz */
	CHECK(r16(REC[0], 0x10) == 1u && (int16_t)r16(REC[0], 0x12) == -90 && (int16_t)r16(REC[0], 0x14) == 40);    /* gate, noise, snr */
	CHECK((int16_t)r16(REC[0], 0x16) == -50 && r16(REC[0], 0x18) == 100u && r16(REC[0], 0x1a) == 0x11u);
	CHECK(REC[0][0x22] == 4u && r32(REC[0], 0x24) == 1u && r32(REC[0], 0x28) == 2u && r32(REC[0], 0x2c) == 5u && r32(REC[0], 0x30) == 11u && r32(REC[0], 0x34) == 0u);   /* Mbps: 1, 2, 5.5 -> 5, 11 */
	CHECK(r16(REC[0], 0x8a) == IE_REAL_LEN && memcmp(REC[0] + 0x8c, want, IE_REAL_LEN) == 0 && REC[0][0x8c + IE_REAL_LEN] == 0);
	CHECK(REC[0][0x88] == 0 && REC[0][0x60] == 5u && memcmp(REC[0] + 0x61, "rich0", 5) == 0);
	CHECK(s_count("record IEs empty") == 0 && s_count("WIFI GAP: bss_info") == 0 && s_count("channel flag") == 0);   /* nothing lost, no gap */
	CHECK(R.parsed == 1u && R.ignored == 0u);
	/* (2) 5 GHz 40 MHz, no IEs, no noise/snr: the gate stays 0, IE length 0 is written over the caller's 0x400 */
	rich_reset();
	RS[0].chsp = 0xC000u | 0x1800u | 38u; RS[0].nrates = 2; RS[0].rates[0] = 0x8c; RS[0].rates[1] = 0x12; RS[0].beacon = 102;
	memset(umem, 0xEE, sizeof umem);
	CHECK(run_rich(1) == 1u);
	CHECK(r32(REC[0], 0x0c) == 0x14u && r16(REC[0], 0x10) == 0u && r16(REC[0], 0x12) == 0u && r16(REC[0], 0x14) == 0u);
	CHECK(r32(REC[0], 0x24) == 6u && r32(REC[0], 0x28) == 9u && REC[0][0x22] == 2u && r16(REC[0], 0x18) == 102u && r16(REC[0], 0x8a) == 0u);
	CHECK(s_count("record IEs empty") == 1);                                                         /* the Get 11 gap line says it for a record without IEs */
	/* noise alone opens the gate; snr alone too */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].noise = -95;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x10) == 1u && (int16_t)r16(REC[0], 0x12) == -95 && r16(REC[0], 0x14) == 0u);
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].snr = 33;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x10) == 1u && r16(REC[0], 0x12) == 0u && r16(REC[0], 0x14) == 33u);
	/* (3) 80 MHz: band bit only and ONE gap per scan, however many records; 160 MHz has its own text */
	rich_reset();
	RS[0].chsp = 0xC000u | 0x2000u | 42u; RS[1].chsp = 0xC000u | 0x2000u | 58u; RS[2].chsp = 0xC000u | 0x2800u | 50u;
	CHECK(run_rich(3) == 3u);
	CHECK(r32(REC[0], 0x0c) == 0x10u && r32(REC[1], 0x0c) == 0x10u && r32(REC[2], 0x0c) == 0x10u);
	CHECK(s_count("80 MHz channel flag bit open") == 1 && s_count("not 20/40 MHz") == 1);
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0 && s_count("80 MHz channel flag bit open") == 2 && s_count("not 20/40 MHz") == 2);       /* the next scan tells them again (once per scan) */
	rich_reset(); RS[0].chsp = 0x4000u | 0x2000u | 5u;                                                    /* 6 GHz with an 80 MHz width: its own band gap only, not the width gap */
	CHECK(run_rich(1) == 1u && r32(REC[0], 0x0c) == 0u && s_count("channel band is not 2.4/5 GHz") == 1 && s_count("80 MHz channel flag bit open") == 0 && s_count("not 20/40 MHz") == 0);
	rich_reset(); RS[0].chsp = 0x2000u | 5u;                                                              /* 2.4 GHz band with 80 MHz */
	CHECK(run_rich(1) == 1u && r32(REC[0], 0x0c) == 0x8u && s_count("80 MHz channel flag bit open") == 1);
	rich_reset(); RS[0].chsp = 0xC000u | 0x1000u | 36u;                                                   /* 5 GHz 20 MHz */
	CHECK(run_rich(1) == 1u && r32(REC[0], 0x0c) == 0x12u && s_count("channel flag") == 0 && s_count("80 MHz") == 0);
	rich_reset(); RS[0].chsp = 0x1800u | 6u;                                                              /* 2.4 GHz 40 MHz */
	CHECK(run_rich(1) == 1u && r32(REC[0], 0x0c) == 0x0cu);
	/* 6 GHz: no band bit, own gap */
	rich_reset(); RS[0].chsp = 0x4000u | 0x1000u | 5u;
	CHECK(run_rich(1) == 1u && r32(REC[0], 0x0c) == 0x2u && s_count("channel band is not 2.4/5 GHz") == 1);
	/* (4) rates: a count above 15 is clamped to 15 with a gap (the 16th entry is lost); 15 is exact */
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].nrates = 20; for (i = 0; i < 16; i++) RS[0].rates[i] = (uint8_t)(2u * (i + 1u));
	CHECK(run_rich(1) == 1u && REC[0][0x22] == 15u && r32(REC[0], 0x24) == 1u && r32(REC[0], 0x24 + 4 * 14) == 15u && REC[0][0x60] == 5u && s_count("rate count above 15") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].nrates = 16; for (i = 0; i < 16; i++) RS[0].rates[i] = (uint8_t)(2u * (i + 1u));       /* 16 = the firmware's full rate set: the 16th does not fit the record */
	CHECK(run_rich(1) == 1u && REC[0][0x22] == 15u && s_count("rate count above 15") == 1 && r32(REC[0], 0x24 + 4 * 14) == 15u && REC[0][0x60] == 5u);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].nrates = 15; for (i = 0; i < 16; i++) RS[0].rates[i] = (uint8_t)(2u * (i + 1u));
	CHECK(run_rich(1) == 1u && REC[0][0x22] == 15u && s_count("rate count above 15") == 0 && r32(REC[0], 0x24 + 4 * 14) == 15u && REC[0][0x60] == 5u);
	/* (5) a malformed IE walk: the last IE claims more bytes than the block has -> cut at the last whole IE + a gap */
	rich_reset(); RS[0].chsp = 0x1001u;
	RS[0].ielen = ie_put(RS[0].ie, 0, "ab", 2); RS[0].ie[RS[0].ielen] = 3; RS[0].ie[RS[0].ielen + 1] = 5; RS[0].ie[RS[0].ielen + 2] = 6; RS[0].ielen += 3u;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 4u && REC[0][0x8c] == 0 && REC[0][0x8d] == 2 && REC[0][0x90] == 0 && s_count("IE walk runs past the block") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ie[0] = 0; RS[0].ielen = 1;            /* a single stray byte (no length byte) */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("IE walk runs past the block") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ie[0] = 0; RS[0].ie[1] = 1; RS[0].ielen = 3;      /* an IE of exactly the remaining length is whole (boundary of the length test) */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 3u && s_count("IE walk runs past the block") == 0);
	/* (6) ie_offset / ie_length outside the record: IEs dropped, gap; the record itself is kept */
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ieoff = 100u;                       /* inside the fixed part (< 126) */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("outside the record, IEs dropped") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ielen_fld = RS[0].ielen + 1u;       /* ie_length one above the record */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("outside the record, IEs dropped") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ieoff = 132u + 400u;               /* ie_offset beyond the record length */
	RS[0].rlen = 132u + RS[0].ielen;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("outside the record, IEs dropped") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ieoff = 126u;                      /* the lowest accepted ie_offset */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == IE_REAL_LEN && s_count("outside the record") == 0);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ieoff = 125u;                      /* one below: the SNR bytes would be IE bytes */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("outside the record, IEs dropped") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].ielen_fld = 0; RS[0].ieoff = 132u;   /* ie_offset + ie_length exactly the record length: accepted */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == IE_REAL_LEN && s_count("outside the record") == 0);
	/* (7) the event holds fewer record bytes than the record claims: the IEs that are there are used, a gap says the block is partial */
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 132u + 25u;           /* SSID 7 + rates 6 + DS 3 + 9 B of the RSN IE */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == IE_REAL_HEAD && s_count("beyond the bytes read") == 1 && s_count("IE walk runs past the block") == 1);
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 132u;                   /* no IE byte at all in the event */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("beyond the bytes read") == 1);
	/* (8) the cap: 512 B exactly fit; one IE more is cut (gap), and an RSN / WPA IE behind the cut is told by its own gap */
	rich_reset(); RS[0].chsp = 0x1001u;
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 256u);
	CHECK(RS[0].ielen == 512u && run_rich(1) == 1u && r16(REC[0], 0x8a) == 512u && memcmp(REC[0] + 0x8c, RS[0].ie, 512) == 0 && s_count("above 512 B") == 0 && s_count("security IE") == 0);
	rich_reset(); RS[0].chsp = 0x1001u;
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 257u);
	CHECK(RS[0].ielen == 513u && run_rich(1) == 1u && r16(REC[0], 0x8a) == 256u && memcmp(REC[0] + 0x8c, RS[0].ie, 256) == 0 && REC[0][0x8c + 256] == 0 && s_count("cut at an IE boundary") == 1 && s_count("security IE") == 0);
	rich_reset(); RS[0].chsp = 0x1001u;
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 256u);
	{ uint8_t tmp[64]; uint32_t n = ie_real(tmp); memcpy(RS[0].ie + RS[0].ielen, tmp + IE_REAL_HEAD, n - IE_REAL_HEAD); RS[0].ielen += n - IE_REAL_HEAD; }   /* + the RSN and vendor IEs of the real block */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 512u && s_count("cut at an IE boundary") == 1 && s_count("security IE beyond IE cap") == 1);
	rich_reset(); RS[0].chsp = 0x1001u;               /* a WPA vendor IE (00:50:f2 type 1) behind the cut */
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 256u);
	{ static const uint8_t wpa[8] = { 0x00, 0x50, 0xf2, 0x01, 0x01, 0x00, 0x00, 0x50 }; RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, wpa, 8); }
	CHECK(run_rich(1) == 1u && s_count("security IE beyond IE cap") == 1);
	rich_reset(); RS[0].chsp = 0x1001u;               /* a WPA IE that is the 4-byte minimum (OUI + type only) */
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 256u);
	{ static const uint8_t wpa[4] = { 0x00, 0x50, 0xf2, 0x01 }; RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, wpa, 4); }
	CHECK(run_rich(1) == 1u && s_count("security IE beyond IE cap") == 1);
	rich_reset(); RS[0].chsp = 0x1001u;               /* other IEs behind the cut are not security IEs: WMM (type 2), another OUI with type 1, a 3 byte vendor IE, element id 47 */
	RS[0].ielen = ie_fill(RS[0].ie, 256u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 256u);
	{ static const uint8_t a[8] = { 0x00, 0x50, 0xf2, 0x02, 0x01, 0x00, 0x00, 0x50 }, b[8] = { 0x00, 0x50, 0xf3, 0x01, 0x01, 0x00, 0x00, 0x50 }, c[8] = { 0x01, 0x50, 0xf2, 0x01, 0x01, 0x00, 0x00, 0x50 },
	    d[8] = { 0x00, 0x51, 0xf2, 0x01, 0x01, 0x00, 0x00, 0x50 }, e[3] = { 0x00, 0x50, 0xf2 };
	  RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, a, 8); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, b, 8); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, c, 8);
	  RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, d, 8); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 47, a, 8); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, e, 3); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 1, "\x01", 1); }   /* the byte after the 3 byte IE is 1: a length test of 3 would read it as the WPA type */
	CHECK(run_rich(1) == 1u && s_count("cut at an IE boundary") == 1 && s_count("security IE") == 0);
	/* the RSN IE that FITS is kept and no security gap is told even though the block is later cut */
	rich_reset(); RS[0].chsp = 0x1001u;
	RS[0].ielen = ie_real(RS[0].ie); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 255u); RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 255u);
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == IE_REAL_LEN + 255u && s_count("cut at an IE boundary") == 1 && s_count("security IE") == 0 && memcmp(REC[0] + 0x8c, RS[0].ie, IE_REAL_LEN + 255u) == 0);
	/* (9) a block at the iOS record's 1 KiB IE capacity: cut at the radio's cap, the 1164 byte record is never overrun (ASan) */
	rich_reset(); RS[0].chsp = 0x1001u;
	for (RS[0].ielen = 0; RS[0].ielen + 255u <= 1024u; ) RS[0].ielen += ie_fill(RS[0].ie + RS[0].ielen, 255u);
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 510u && REC[0][0x8c + 510] == 0 && REC[0][1163] == 0);
	/* (10) the bytes of the boot-time replay (89 B records): no IEs, the first-89 fields still fill rates / beacon / flags; SNR stays 0 */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].nrates = 2; RS[0].rates[0] = 0x82; RS[0].rates[1] = 0x0c; RS[0].noise = -80; RS[0].beacon = 100; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 89u;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && r32(REC[0], 0x0c) == 0x0au && REC[0][0x22] == 2u && r32(REC[0], 0x28) == 6u && r16(REC[0], 0x18) == 100u && r16(REC[0], 0x10) == 1u && r16(REC[0], 0x14) == 0u);
	CHECK(s_count("WIFI GAP: bss_info") == 0);
	/* a negative SNR keeps its sign */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].snr = -5; RS[0].noise = -3;
	CHECK(run_rich(1) == 1u && (int16_t)r16(REC[0], 0x14) == -5 && (int16_t)r16(REC[0], 0x12) == -3 && r16(REC[0], 0x10) == 1u);
	/* the event ends in the middle of the IE block: the partial block is walked as far as the event goes (not one byte further) */
	rich_reset(); RS[0].chsp = 0x1001u; RS[0].ielen = ie_put(RS[0].ie, 0, "rich0", 5); RS[0].ielen += ie_put(RS[0].ie + RS[0].ielen, 221, "", 0); RS[0].evrec = 132u + 8u;
	CHECK(RS[0].ielen == 9u && run_rich(1) == 1u && r16(REC[0], 0x8a) == 7u && s_count("beyond the bytes read") == 1);
	/* a record of 125 bytes (one short of SNR): no SNR, no IEs */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].snr = 33; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 125u;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x14) == 0u && r16(REC[0], 0x8a) == 0u);
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].snr = 33; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 126u;        /* just enough for SNR; the IEs are beyond the bytes read */
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x14) == 33u && r16(REC[0], 0x8a) == 0u);
	/* the record's own length field bounds the read: a record that claims 100 bytes has no SNR and no IEs even if the event holds more */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].snr = 33; RS[0].ielen = ie_real(RS[0].ie); RS[0].rlen = 100u;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x14) == 0u && r16(REC[0], 0x8a) == 0u && s_count("WIFI GAP: bss_info") == 0);
	/* no IEs (ie_length 0): whatever ie_offset says is not looked at, no gap */
	rich_reset(); RS[0].chsp = 0x1006u; RS[0].ieoff = 1u; RS[0].rlen = 132u; RS[0].evrec = 132u;
	CHECK(run_rich(1) == 1u && r16(REC[0], 0x8a) == 0u && s_count("WIFI GAP: bss_info") == 0 && s_count("outside the record") == 0);
}

/* Get 103 takes the IEs / rates / beacon / flags of the joined AP from the latest scan list (same BSSID); no such entry: the record is as before (no IEs + the gap) */
static void test_current_network(void)
{
	struct swsta_bss b;
	uint8_t bssid[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x60 }, want[64];
	rich_reset();
	RS[0].ielen = ie_real(RS[0].ie); memcpy(want, RS[0].ie, RS[0].ielen);
	RS[0].chsp = 0x1000u | 6u; RS[0].nrates = 1; RS[0].rates[0] = 0x16; RS[0].beacon = 100; RS[0].noise = -90; RS[0].snr = 40;
	CHECK(run_rich(1) == 1u);
	CHECK(R.radio.bss_find != NULL && R.radio.bss_find(R.radio.ctx, bssid, &b) == 1 && b.ie_len == RS[0].ielen && b.beacon == 100u && b.chflags == 0x0au);
	bssid[5] ^= 1;
	CHECK(R.radio.bss_find(R.radio.ctx, bssid, &b) == 0);
	bssid[5] ^= 1;
	/* the kept link is that AP */
	CHECK(swr_kept_set(&R, (const uint8_t *)"rich0", 5, bssid, 6, -48, 0xFu) == 1);
	memset(s_log, 0, sizeof s_log);
	CHECK(get(SWSTA_T_CURRENT_NETWORK, 1164, UBASE + 2000) == 0);
	CHECK(r32(umem + 2000, 0x08) == 6u && r32(umem + 2000, 0x0c) == 0x0au && r16(umem + 2000, 0x10) == 1u && (int16_t)r16(umem + 2000, 0x12) == -90 && (int16_t)r16(umem + 2000, 0x14) == 40);
	CHECK((int16_t)r16(umem + 2000, 0x16) == -48 && r16(umem + 2000, 0x18) == 100u && umem[2000 + 0x22] == 1u && r32(umem + 2000, 0x24) == 11u);
	CHECK(r16(umem + 2000, 0x8a) == IE_REAL_LEN && memcmp(umem + 2000 + 0x8c, want, IE_REAL_LEN) == 0 && memcmp(umem + 2000 + 0x1c, bssid, 6) == 0 && s_count("record IEs empty") == 0);
	/* the kept link is another AP: empty IEs, 0 gate, and the gap says so */
	bssid[5] ^= 1;
	CHECK(swr_kept_set(&R, (const uint8_t *)"other", 5, bssid, 6, -48, 0xFu) == 1);
	memset(s_log, 0, sizeof s_log); memset(umem + 2000, 0xEE, 1164);
	CHECK(get(SWSTA_T_CURRENT_NETWORK, 1164, UBASE + 2000) == 0);
	CHECK(r16(umem + 2000, 0x8a) == 0u && r16(umem + 2000, 0x10) == 0u && umem[2000 + 0x22] == 0u && r32(umem + 2000, 0x0c) == 0u && r16(umem + 2000, 0x18) == 0u && s_count("no IEs: Settings shows Weak Security") == 1);
	/* an empty list: same */
	radio_reset(0, 1, T_NONE);
	CHECK(swr_kept_set(&R, (const uint8_t *)"rich0", 5, bssid, 6, -48, 0xFu) == 1);
	CHECK(get(SWSTA_T_CURRENT_NETWORK, 1164, UBASE + 2000) == 0 && r16(umem + 2000, 0x8a) == 0u);
}

/* ---- 2026-11-11 (W-D3-weak-security): the join target's own record, handed over with the kept link, answers the FIRST Get 103 ---- */
static const uint8_t KB[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x60 };      /* = the BSSID run_rich() gives record 0 */
static void kept_reset(void)
{
	radio_reset(0, 1, T_NONE);
	R.link.escan = link_rich; R.link.cx = NULL;
	CHECK(swr_kept_set(&R, (const uint8_t *)"rich0", 5, KB, 6, -48, 0xFu) == 1);
	memset(s_log, 0, sizeof s_log);
}
static uint32_t get103(uint8_t *out)
{
	CHECK(get(SWSTA_T_CURRENT_NETWORK, 1164, UBASE + 2000) == 0);
	memcpy(out, umem + 2000, 1164);
	return r16(out, 0x8a);
}
static void test_kept_target(void)
{
	static const uint8_t rates3[3] = { 0x82, 0x04, 0x96 };
	uint8_t ie[600], rec[1164], want[64];
	uint32_t n, i;
	/* (1) before any scan: the first answer carries the target's IEs, rates, beacon, noise/SNR and flags; the privacy bit stays; no gap */
	kept_reset();
	n = ie_real(ie); memcpy(want, ie, n);
	CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0x1000u | 6u, 100u, -90, 40, rates3, 3u) == 1 && s_count("radio kept target record: ie_len=46 rates=3 beacon=100 chflags=0xa") == 1);
	CHECK(power_on() == 0);
	CHECK(get103(rec) == IE_REAL_LEN && memcmp(rec + 0x8c, want, IE_REAL_LEN) == 0 && rec[0x8c + 16] == 48u && (rec[0x1a] & 0x10) == 0x10);      /* the RSN IE (id 48) is in the record */
	CHECK(r32(rec, 0x0c) == 0x0au && r16(rec, 0x18) == 100u && rec[0x22] == 3u && r32(rec, 0x24) == 1u && r32(rec, 0x28) == 2u && r32(rec, 0x2c) == 11u);
	CHECK(r16(rec, 0x10) == 1u && (int16_t)r16(rec, 0x12) == -90 && (int16_t)r16(rec, 0x14) == 40);
	CHECK(r32(rec, 0x08) == 6u && (int16_t)r16(rec, 0x16) == -48 && rec[0x60] == 5u && memcmp(rec + 0x61, "rich0", 5) == 0 && memcmp(rec + 0x1c, KB, 6) == 0);       /* ssid, channel, rssi stay the link's */
	CHECK(s_count("Weak Security") == 0 && s_count("no IEs") == 0);
	/* (2) a scan list entry of the joined BSSID WITH IEs is newer and wins */
	rich_reset(); RS[0].chsp = 0x1000u | 11u; RS[0].beacon = 200; RS[0].ielen = ie_real(RS[0].ie); RS[0].ie[2] = 'R';
	n_rs = 1;
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	CHECK(get103(rec) == IE_REAL_LEN && rec[0x8c + 2] == 'R' && r16(rec, 0x18) == 200u && r32(rec, 0x0c) == 0x0au);
	/* a list entry without IEs (the boot-time replay shape, 89 B) does not hide the kept record */
	rich_reset(); RS[0].chsp = 0x1000u | 11u; RS[0].beacon = 300; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 89u;
	n_rs = 1;
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	CHECK(get103(rec) == IE_REAL_LEN && rec[0x8c + 2] == 'r' && r16(rec, 0x18) == 100u);
	/* lookups: a list entry of another BSSID is never returned; the list entry without IEs is returned when no kept record exists */
	{ struct swsta_bss fb; uint8_t ob[6]; memcpy(ob, KB, 6); ob[3] ^= 1;
	  CHECK(R.radio.bss_find(R.radio.ctx, ob, &fb) == 0);
	  kept_reset();
	  rich_reset(); RS[0].chsp = 0x1000u | 11u; RS[0].beacon = 300; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 89u;
	  n_rs = 1;
	  CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	  memset(&fb, 0, sizeof fb);
	  CHECK(R.radio.bss_find(R.radio.ctx, KB, &fb) == 1 && fb.ie_len == 0u && fb.beacon == 300u && R.radio.bss_find(R.radio.ctx, ob, &fb) == 0);
	  /* restore: the state the next block expects */
	  kept_reset();
	  n = ie_real(ie);
	  CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0x1000u | 6u, 100u, -90, 40, rates3, 3u) == 1);
	  rich_reset(); RS[0].chsp = 0x1000u | 11u; RS[0].beacon = 300; RS[0].ielen = ie_real(RS[0].ie); RS[0].evrec = 89u;
	  n_rs = 1;
	  CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0); }
	/* (3) the re-join to the same BSSID keeps the record; to another BSSID it is dropped with a GAP, and the answer is the empty record with the Weak Security gap */
	CHECK(swr_kept_clear(&R, 0) == 1);
	CHECK(swr_kept_up(&R, KB, 6u, -50, 0xFu) == 1);
	CHECK(get103(rec) == IE_REAL_LEN && s_count("kept target record (IEs) is dropped") == 0);
	CHECK(swr_kept_clear(&R, 0) == 1);
	{ uint8_t nb[6]; memcpy(nb, KB, 6); nb[5] ^= 1; CHECK(swr_kept_up(&R, nb, 6u, -50, 0xFu) == 1); }
	CHECK(s_count("kept target record (IEs) is dropped") == 1 && R.kept_bss_ok == 0u);       /* the re-join on another BSSID cleared it ... */
	{ struct swsta_bss fb; CHECK(R.radio.bss_find(R.radio.ctx, KB, &fb) == 1 && fb.ie_len == 0u); }       /* ... and the old BSSID finds only the scan-list entry (no IEs) */
	memset(s_log, 0, sizeof s_log);
	CHECK(get103(rec) == 0u && (rec[0x1a] & 0x10) == 0x10 && s_count("no IEs: Settings shows Weak Security") == 1);
	/* a second list entry is found by its own BSSID, with its IEs; the kept record stays for the kept BSSID */
	{ struct swsta_bss fb; uint8_t kb2[6]; memcpy(kb2, KB, 6); kb2[5] = 0x61;
	  kept_reset();
	  rich_reset(); RS[0].chsp = 0x1006u; RS[0].beacon = 111; RS[1].chsp = 0x1006u; RS[1].beacon = 222; RS[1].ielen = ie_real(RS[1].ie); n_rs = 2;
	  CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0);
	  CHECK(R.radio.bss_find(R.radio.ctx, kb2, &fb) == 1 && fb.beacon == 222u && fb.ie_len == IE_REAL_LEN && memcmp(fb.bssid, kb2, 6) == 0);
	  /* the found entry carries the ssid of the kept record (the target record is for the kept link) */
	  kept_reset();
	  CHECK(swr_kept_target(&R, KB, ie, ie_real(ie), 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && R.radio.bss_find(R.radio.ctx, KB, &fb) == 1 && fb.ssid_len == 5u && memcmp(fb.ssid, "rich0", 5) == 0); }
	/* the hand-over needs the kept link: not joined and a zero BSSID is refused; 16 rates are 15 */
	radio_reset(0, 1, T_NONE);
	{ static const uint8_t z6[6] = { 0, 0, 0, 0, 0, 0 };
	  CHECK(swr_kept_target(&R, z6, ie, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 0 && R.kept_bss_ok == 0u); }
	kept_reset();
	{ uint8_t rr[16]; for (i = 0; i < 16; i++) rr[i] = (uint8_t)(2u * (i + 1u));
	  CHECK(swr_kept_target(&R, KB, ie, 46u, 0u, 0x1006u, 100u, 0, 0, rr, 16u) == 1 && R.kept_bss.nrates == 15u && R.kept_bss.rates[14] == 15u && s_count("kept target rate count above 15") == 1); }
	/* (4) no kept record at all: privacy bit stays, ie_len 0, and the gap says what Settings will show */
	kept_reset();
	CHECK(get103(rec) == 0u && (rec[0x1a] & 0x10) == 0x10 && s_count("no IEs: Settings shows Weak Security") == 1);
	/* a lookup for another BSSID never gets the kept record */
	kept_reset();
	CHECK(swr_kept_target(&R, KB, ie, ie_real(ie), 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && R.kept_bss_ok == 1u);
	{ struct swsta_bss fb; uint8_t ob[6]; memcpy(ob, KB, 6); ob[2] ^= 1;
	  CHECK(R.radio.bss_find(R.radio.ctx, KB, &fb) == 1 && fb.ie_len == IE_REAL_LEN && R.radio.bss_find(R.radio.ctx, ob, &fb) == 0);
	  /* a refused newer record (another BSSID) leaves the earlier one in place; a record before the link is kept is refused */
	  ob[2] ^= 1; ob[0] ^= 4;
	  CHECK(swr_kept_target(&R, ob, ie, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 0 && R.kept_bss_ok == 1u && R.kept_bss.ie_len == IE_REAL_LEN); }
	radio_reset(0, 1, T_NONE);
	CHECK(swr_kept_target(&R, KB, ie, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 0 && R.kept_bss_ok == 0u && s_count("not for the kept BSSID") == 1);
	/* IEs announced but no pointer: no copy from NULL, the record has no IEs */
	kept_reset();
	CHECK(swr_kept_target(&R, KB, NULL, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && R.kept_bss.ie_len == 0u && s_count("Weak Security") == 1);
	/* (5) a record for another BSSID is refused; one without IEs / with a long tail / too many rates is told */
	kept_reset();
	{ uint8_t ob[6]; memcpy(ob, KB, 6); ob[0] ^= 4; CHECK(swr_kept_target(&R, ob, ie, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 0 && s_count("not for the kept BSSID") == 1 && R.kept_bss_ok == 0); }
	CHECK(swr_kept_target(&R, NULL, ie, 46u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 0);
	kept_reset();
	CHECK(swr_kept_target(&R, KB, NULL, 0u, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && s_count("kept target record has no IEs: Get 103 answers privacy without RSN IE, Settings will show Weak Security") == 1);
	kept_reset();
	n = ie_real(ie);
	CHECK(swr_kept_target(&R, KB, ie, n, 1u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && s_count("IE block longer than 1024 B") == 1 && s_count("Weak Security") == 0);
	kept_reset();
	{ uint8_t rr[20]; for (i = 0; i < 20; i++) rr[i] = (uint8_t)(2u * (i + 1u));
	  CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0x1006u, 100u, 0, 0, rr, 20u) == 1 && R.kept_bss.nrates == 15u && R.kept_bss.rates[14] == 15u && s_count("kept target rate count above 15") == 1); }
	/* 80 MHz target: the same flags rule as a scan record, told once */
	kept_reset();
	CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0xC000u | 0x2000u | 42u, 100u, 0, 0, NULL, 0u) == 1 && R.kept_bss.chflags == 0x10u && s_count("80 MHz channel flag bit open") == 1);
	/* the IE block above the radio's 512 B is cut at an IE boundary and a security IE behind the cut is told */
	kept_reset();
	n = ie_fill(ie, 256u); n += ie_fill(ie + n, 256u); n += ie_real(ie + n) - IE_REAL_HEAD > 0 ? 0 : 0;
	{ uint8_t tmp[64]; uint32_t m = ie_real(tmp); memcpy(ie + n, tmp + IE_REAL_HEAD, m - IE_REAL_HEAD); n += m - IE_REAL_HEAD; }
	CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0x1006u, 100u, 0, 0, NULL, 0u) == 1 && R.kept_bss.ie_len == 512u && s_count("cut at an IE boundary") == 1 && s_count("security IE beyond IE cap") == 1);
	/* once-per-scan bits do not leak from an earlier scan into the hand-over */
	kept_reset();
	R.sgap = 0xFFu;
	n = ie_real(ie);
	CHECK(swr_kept_target(&R, KB, ie, n, 0u, 0xC000u | 0x2000u | 42u, 100u, 0, 0, NULL, 0u) == 1 && s_count("80 MHz channel flag bit open") == 1);
}

int main(void)
{
	test_bounds_and_picks();
	test_verbs(); test_scan_records(); test_current_network(); test_kept_target(); test_end_to_end(); test_many_events(); test_dups_and_faults(); test_unfinished_and_link_failure(); test_g2_link();
	if (fails) { printf("RESULT: FAIL wlan_radio_host_test fails=%d\n", fails); return 1; }
	printf("RESULT: PASS wlan_radio_host_test\n");
	return 0;
}
