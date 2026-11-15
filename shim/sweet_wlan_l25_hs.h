/*
 * L25 host 4-way and L26 ping of the W-D4e engine (T-38cc; SPEC-WIFI Part E TRANSPORT block s.2): the poll loop over rings 2/3/4, rx completions -> swdp_net_rx -> swj_rx (H6),
 * M2/M4 as tx posts (H7), the key installs H8-H10 after M4 has left, then the static-IP ARP + ICMP echo run (DP13). Every bus store goes through swg_dp_exec (DP15) or swg_e_call(_secret).
 *
 * Include order in the includer: sweet_wlan_data.h, sweet_wlan_join.h, sweet_wlan_g2.h, sweet_wlan_l25.h, then this file (g2.h's swg_e calls swl25_run, declared there).
 * Limits of this slice, each printed when hit (rule 8): (1) the ring-1 shadow and the flow-ring shadow cover SWL25_SHADOW_ITEMS = 16 items, so the 16 rx buffers posted at L25 are NOT re-posted
 * (an rx frame count above 16 ends the run with `WIFI GAP: L25 rx buffers exhausted`) and the 16 tx buffers are each used ONCE (TRANSPORT s.1 item 5 / ticket watch item: an all-ones old word
 * would be a false DEAD stop); (2) time is the poll step counter (1 ms per step plus the bus delays), so an RTT is as coarse as the loop; (3) a second M1 / group rekey after the keys are
 * installed is not handled (SWJ_GAP_X_REKEY) and the key material is wiped when the install is done. The run state is the file-scope swl25_H (BSS in the kernel, ~36 KB, not the stack).
 */
#ifndef SWEET_WLAN_L25_HS_H
#define SWEET_WLAN_L25_HS_H

#ifndef SWEET_WLAN_L25_H
#error "sweet_wlan_l25_hs.h needs sweet_wlan_l25.h first"
#endif
#ifndef SWEET_WLAN_JOIN_H
#error "sweet_wlan_l25_hs.h needs sweet_wlan_join.h"
#endif

#define SWL25_POLL_MS     1u            /* one poll step [TRANSPORT s.2: 1 ms steps] */
#define SWL25_TXWAIT_MS   200u          /* wait for the M4 tx status before the key installs [decision, no source] */
#define SWL25_PINGS       10u           /* echo requests of L26: W-PING asks 10/10 (sprint 2026-11-08, was 4); tx buffers: M2 + M4 + up to 3 ARP + 10 = 15 <= 16 (each used once in L25) */
#define SWL25_PING_GAP_MS 1000u         /* between two echo requests [decision] */
#define SWL25_ARP_TRIES   3u            /* ARP requests before the gateway is declared silent [decision] */
#define SWL25_ARP_MS      1000u         /* wait after each ARP request [decision] */
#define SWL25_TAIL_MS     2000u         /* wait for the last echo reply [decision] */
#define SWL25_RXD_PER_PASS 8u           /* D2H items handled per ring per poll pass */

/* ---- T-924f witness (W-JOIN L25, score 2: M1 x6, M2 x6, no M3): the dongle's own tx counters around the first M2 tell "frame dropped in the dongle" from "frame sent, AP did not answer".
 * Request: WLC_GET_VAR "counters" (read-only, no argument; the bytes after the name are zero). Reply: wl_cnt_info_t {u16 version, u16 datalen} then, for version 30 (WL_CNT_VERSION_XTLV), XTLV tuples
 * {u16 id, u16 len, data padded to 4 B}; tuple id 0x100 (WL_CNT_XTLV_WLC) holds wl_cnt_wlc_t [src refs wlan-stage5 wlioctl.h.new:4833-4841 (header), 4496-4508 (ids), 4896-5138 (struct), 6415-6716 (wl_cnt_ver_11_t)].
 * Field offsets are NOT typed by hand: they are the offsetof() values of those two structs compiled with NFIFO 6 and NREINITREASONCOUNT 8 (wlioctl.h.new:4381, 4468) [obs 2026-11-07 host compile, T-924f]:
 *   wl_cnt_wlc_t   (sizeof 864):  txframe 0, txnobuf 28, txnoassoc 32, txfail 200, txretry 204, txnoack 224, rxframe 60, fourwayfail 272
 *   wl_cnt_ver_11_t (sizeof 1004, own layout after txfunfl[6]; the data starts after the 4 B header): txframe 4, txnobuf 32, txnoassoc 36, txfail 432, txretry 436, txnoack 456, rxframe 64, fourwayfail 504
 * The longest reply is WL_XTLV_CNTBUF_MAX_SIZE = 4 + (4+864) + (4+256) + (4+24) = 1160 B [src wlioctl.h.new:4825-4828, mcst 64 u32, mcxst 6 u32]; the request asks for SWL25_CNT_LEN = 1200 B (in = out), inside the 1420 B payload
 * shadow and the 8192 B response buffer (SWD_RESPBUF_LEN). DHD asks for WLC_IOCTL_MEDLEN 1936 [src wl_cfg80211.c.new:13975] which the shadow cannot carry; a reply the firmware cuts or refuses is a loud GAP below. */
#define SWL25_CNT_LEN       1200u
#define SWL25_CNT_SETTLE_MS 100u      /* wait after the first M2's tx status before the second read: the 802.11 retries of one frame end within it [decision, no source] */
#define SWL25_CNT_VER_XTLV  30u
#define SWL25_CNT_VER_11    11u
#define SWL25_CNT_XTLV_WLC  0x100u
enum { SWL25_CNT_TXFRAME, SWL25_CNT_TXNOBUF, SWL25_CNT_TXNOASSOC, SWL25_CNT_TXFAIL, SWL25_CNT_TXRETRY, SWL25_CNT_TXNOACK, SWL25_CNT_RXFRAME, SWL25_CNT_FOURWAYFAIL, SWL25_CNT_TXBYTE, SWL25_CNT_RXBYTE, SWL25_CNT_N };
#define SWL25_CNT_PR SWL25_CNT_TXBYTE   /* the first 8 counters keep their T-924f log lines; the byte counters (T-8d5f) get their own line */
static const uint16_t swl25_cnt_off_wlc[SWL25_CNT_N] = { 0u, 28u, 32u, 200u, 204u, 224u, 60u, 272u, 4u, 64u };   /* txbyte 4, rxbyte 64 [src wlioctl.h.new wl_cnt_wlc_t: txframe 0 then txbyte; rxbyte follows rxframe 60] */
static const uint16_t swl25_cnt_off_v11[SWL25_CNT_N] = { 4u, 32u, 36u, 432u, 436u, 456u, 64u, 504u, 8u, 68u };   /* v11: txbyte follows txframe 4, rxbyte follows rxframe 64 [src wlioctl.h.new:6415-6441] */
static const char *const swl25_cnt_name[SWL25_CNT_N] = { "txframe", "txnobuf", "txnoassoc", "txfail", "txretry", "txnoack", "rxframe", "fourwayfail", "txbyte", "rxbyte" };

struct swl25h {
	struct swl25 l;
	struct swj_ctx sup;
	struct swdp_net net;
	struct swdp_ring r3, r4;        /* the D2H cursors of ring 3 (tx status) and ring 4 (rx complete) */
	struct swdp_book txb;           /* tx buffers: taken once each, never released (see the header) */
	uint32_t ms;                    /* poll-step clock */
	uint32_t m4_rid, txdone;        /* request id of the M4 tx post (0 = none yet) and the bit mask of tx slots whose status was seen */
	uint8_t want_keys, hs_done, last_msg;
	uint32_t rx_frames, tx_posts, tx_status_bad;
	uint32_t wit_rx, wit_tx;        /* T-73fc: EAPOL hex witnesses printed so far (swl25_witness) */
	uint32_t wit_eth_rx, wit_eth_tx;  /* T-8d5f: Ethernet-header witnesses printed so far (swl25_witness_eth) */
	uint32_t m2_rid;                /* T-924f: request id of the FIRST M2 tx post (0 = none yet), and the poll-step time its tx status was seen (m2_stat set) */
	uint32_t m2_stat_ms;
	uint32_t cnt0[SWL25_CNT_N];     /* T-924f: firmware tx counters read before the first M2 (valid when cnt0_ok) */
	uint8_t cnt0_ok, m2_stat, cnt1_done;
	uint32_t n_eapol_rx, n_m2;      /* EAPOL frames received / M2 posted so far (the counters lines name them) */
	uint8_t arsne[64];              /* T-9d71: the RSNE of the association request the FIRMWARE sent (assoc_req_ies); arsne_len 0 = not read, M2 then carries the beacon RSNE (a printed gap) */
	uint32_t arsne_len;
	/* W-UI (sprint 2026-11-08): the live link as the FIRMWARE reports it after L26, for the Apple80211 kept link (never the join's .env SSID or the boot scan record). live_ok bits: 1 ssid (SSID element of the
	 * association request the firmware sent, assoc_req_ies), 2 bssid (WLC_GET_BSSID), 4 chanspec ("chanspec" GET_VAR), 8 rssi (WLC_GET_RSSI). A bit clear = not read, a GAP was printed */
	uint8_t live_ssid[32];
	uint32_t live_ssid_len;
	uint8_t live_bssid[6];
	uint32_t live_chanspec, live_chan, live_ok;
	int32_t live_rssi;
};

static void
swl25_wipe(void *p, uint32_t n)
{
	volatile uint8_t *v = (volatile uint8_t *)p;
	uint32_t i;

	for (i = 0; i < n; i++) {
		v[i] = 0u;
	}
	__asm__ volatile("" ::: "memory");
}

static int
swl25_zero(const uint8_t *p, uint32_t n)
{
	uint32_t i, o = 0u;

	for (i = 0; i < n; i++) {
		o |= p[i];
	}
	return o == 0u;
}

/* soft stop with one GAP line: engine flag + `WIFI GAP: <what>` (area l25). Returns 0 so a caller can `return swl25_gap(...)`. */
static int
swl25_gap(struct swg_wx *x, const char *what)
{
	char gb[64];

	snprintf(gb, sizeof gb, "%.60s", what);
	swl_gap("l25", gb, "high");
	printf("WIFI GAP: %s\n", what);
	x->d4dstop = 1u;
	return 0;
}

/* T-baed step 1: the station MAC from the firmware, a read-only GET_VAR cur_etheraddr (6 B) [src docs/research/108 s.2, BW bwfm.c:261]; the cfg MAC, when the glue gives one, must equal it.
 * Needs the same response-buffer budget as one L22 GET_VAR (swg_e_call repost = 1). 0 with x->d4dstop clear = ok, mac filled; a GAP is printed and x->d4dstop set (return 0), or the bus stop code is returned */
static int
swl25_getmac(struct swg_wx *x, const uint8_t want[6], uint8_t mac[6])
{
	uint8_t rsp[8];
	struct swd4b_ioctl_d2h cm;
	int ex;

	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR cur_etheraddr", "cur_etheraddr", NULL, 6u, 14u + 6u, 1, 6u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 6u) {
		return swl25_gap(x, "L25 not run: cur_etheraddr get refused or the answer is not 6 bytes");
	}
	memcpy(mac, rsp, 6);
	if (swl25_zero(mac, 6u) || (mac[0] & 1u) != 0u) {
		return swl25_gap(x, "L25 not run: cur_etheraddr answer is zero or a multicast address");
	}
	if (!swl25_zero(want, 6u) && memcmp(want, mac, 6) != 0) {
		return swl25_gap(x, "L25 not run: the station MAC from the glue differs from cur_etheraddr");
	}
	printf("SWEET WLAN: L25 station MAC read (cur_etheraddr, 6 bytes)\n");
	return 0;
}

/* parse a counters reply of n bytes: 0 = filled out[] and *ver; -1 shorter than the 4 B header, -2 version neither 30 nor 11, -3 datalen above the reply, -4 no complete WLC tuple (xtlv) or reply shorter than the last field (v11) */
static int
swl25_cnt_parse(const uint8_t *r, uint32_t n, uint32_t out[SWL25_CNT_N], uint32_t *ver)
{
	const uint32_t v = n >= 4u ? (uint32_t)r[0] | (uint32_t)r[1] << 8 : 0u;
	uint32_t dl, off, i, base = 0u, len = 0u;
	const uint16_t *tab = swl25_cnt_off_wlc;

	if (n < 4u) {
		return -1;
	}
	*ver = v;
	if (v == SWL25_CNT_VER_11) {
		tab = swl25_cnt_off_v11;
		base = 0u;
		len = n;
	} else if (v == SWL25_CNT_VER_XTLV) {
		dl = (uint32_t)r[2] | (uint32_t)r[3] << 8;
		if (dl > n - 4u) {
			return -3;
		}
		for (off = 4u; off + 4u <= 4u + dl; ) {
			const uint32_t id = (uint32_t)r[off] | (uint32_t)r[off + 1u] << 8, tl = (uint32_t)r[off + 2u] | (uint32_t)r[off + 3u] << 8;

			if (off + 4u + tl > 4u + dl) {
				return -4;
			}
			if (id == SWL25_CNT_XTLV_WLC) {
				base = off + 4u;
				len = tl;
				break;
			}
			off += 4u + ((tl + 3u) & ~3u);
		}
		if (len == 0u) {
			return -4;
		}
	} else {
		return -2;
	}
	for (i = 0; i < SWL25_CNT_N; i++) {
		const uint32_t o = base + tab[i];

		if (v == SWL25_CNT_VER_11 ? o + 4u > len : (uint32_t)tab[i] + 4u > len) {
			return -4;
		}
		out[i] = (uint32_t)r[o] | (uint32_t)r[o + 1u] << 8 | (uint32_t)r[o + 2u] << 16 | (uint32_t)r[o + 3u] << 24;
	}
	return 0;
}

/* the reading of the first-M2 deltas (docs/research/T-86b6-m2-cause.md s.4), all [guess] until the boot: the dongle dropped the frame (txframe +0, or txnobuf / txnoassoc +1) = "dongle-drop";
 * on air but not acked (txnoack or txfail > 0) = "air-no-ack"; sent and acked, still no M3 = "acked-ap-silent" (the AP got it and rejected or ignored it) */
static const char *
swl25_cnt_cause(const uint32_t d[SWL25_CNT_N])
{
	if (d[SWL25_CNT_TXNOBUF] != 0u || d[SWL25_CNT_TXNOASSOC] != 0u || d[SWL25_CNT_TXFRAME] == 0u) {
		return "dongle-drop";
	}
	if (d[SWL25_CNT_TXNOACK] != 0u || d[SWL25_CNT_TXFAIL] != 0u) {
		return "air-no-ack";
	}
	return "acked-ap-silent";
}

static uint8_t swl25_cnt_rsp[SWL25_CNT_LEN + 4u];        /* file scope: no stack growth (the T-73fc overflow); +4 for the word-wise copy of the last bytes */

/* one counters GET. Returns like swg_e_call (0 = done or soft stop with x->d4dstop, negative = the bus stopped); *ok = 1 when out[] is filled, 0 after a refused or unparsable reply (a GAP line, the run goes on: this is a witness) */
static int
swl25_cnt_read(struct swg_wx *x, uint32_t out[SWL25_CNT_N], const char *tag, int *ok)
{
	struct swd4b_ioctl_d2h cm;
	uint32_t ver = 0u;
	int ex, pr;

	*ok = 0;
	memset(swl25_cnt_rsp, 0, sizeof swl25_cnt_rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR counters", "counters", NULL, 0u, SWL25_CNT_LEN, 1, SWL25_CNT_LEN, swl25_cnt_rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		printf("WIFI GAP: L25 counters read (%s) refused status=%d resp_len %u\n", tag, (int)cm.status, cm.resp_len);
		swl_gap("l25", "counters read refused", "medium");
		return 0;
	}
	pr = swl25_cnt_parse(swl25_cnt_rsp, cm.resp_len < SWL25_CNT_LEN ? cm.resp_len : SWL25_CNT_LEN, out, &ver);
	if (pr != 0) {
		printf("WIFI GAP: L25 counters read (%s) not parsed: code %d version %u resp_len %u (30 = xtlv, 11 = legacy)\n", tag, pr, ver, cm.resp_len);
		swl_gap("l25", "counters reply not parsed", "medium");
		return 0;
	}
	*ok = 1;
	return 0;
}

/* log the named deltas of cur against base: one line, the numbers are the evidence, cause= is the [guess] reading */
static void
swl25_cnt_report(const char *tag, const uint32_t base[SWL25_CNT_N], const uint32_t cur[SWL25_CNT_N], uint32_t rx, uint32_t m2)
{
	uint32_t d[SWL25_CNT_N], i;

	for (i = 0; i < SWL25_CNT_N; i++) {
		d[i] = cur[i] - base[i];
	}
	printf("SWEET WLAN: L25 witness counters %s", tag);
	for (i = 0; i < SWL25_CNT_PR; i++) {
		printf(" d_%s=%u", swl25_cnt_name[i], d[i]);
	}
	printf(" (eapol_rx=%u m2_tx=%u) cause=%s\n", rx, m2, swl25_cnt_cause(d));
	/* T-8d5f: on-air size witness. rxbyte over the M1s and txbyte over the M2s use the same counting convention, so tx_per_m2 - rx_per_m1 should equal the EAPOL size difference (M2 121 B - M1 99 B = 22) if the dongle sent every M2 whole [guess]; the absolute numbers are not interpreted alone */
	printf("SWEET WLAN: L25 witness bytes %s d_txbyte=%u d_rxbyte=%u (m2_tx=%u eapol_rx=%u)\n", tag, d[SWL25_CNT_TXBYTE], d[SWL25_CNT_RXBYTE], m2, rx);
}

/* n bytes at pa of the DATA block (word reads through the bus, space SWG_SP_DATA). 0 = read, negative = stopped */
static int
swl25_dread(struct swg_wx *x, const struct swl25 *l, uint64_t pa, uint32_t n, uint8_t *out)
{
	const uint64_t a = pa & ~3ull;
	const uint32_t lead = (uint32_t)(pa - a), nw = (lead + n + 3u) / 4u;
	uint32_t k, b, v;

	if (n == 0u || !swdp_in(&l->env.data, a, 4ull * nw)) {
		x->fail = "an L25 data-block read outside the block";
		return swg_stop(x, SWG_E_ADDR);
	}
	for (k = 0; k < nw; k++) {
		if (x->b->rd(x->b->ctx, SWG_SP_DATA, (uint32_t)(a - l->env.data.pa) + 4u * k, &v)) {
			x->fail = "an L25 data-block read failed";
			return swg_stop(x, SWG_E_ADDR);
		}
		for (b = 0; b < 4u; b++) {
			const uint32_t i = 4u * k + b;

			if (i >= lead && i - lead < n) {
				out[i - lead] = (uint8_t)(v >> (8u * b));
			}
		}
	}
	return 0;
}

/* item k of common ring ri (3 or 4) from the G1 block as bytes. 0 = read, negative = stopped */
static int
swl25_item(struct swg_wx *x, uint32_t ri, uint32_t k, uint32_t isz, uint8_t *out)
{
	uint32_t i, v;

	for (i = 0; i < isz / 4u; i++) {
		x->fail = "an L25 D2H item read failed";
		if (swg_hrd(x, x->in.ring[ri] + (uint64_t)k * isz + 4u * i, &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		out[4u * i] = (uint8_t)v;
		out[4u * i + 1u] = (uint8_t)(v >> 8);
		out[4u * i + 2u] = (uint8_t)(v >> 16);
		out[4u * i + 3u] = (uint8_t)(v >> 24);
	}
	return 0;
}

/* DP4: one frame (14 B Ethernet header + payload) as a tx post on the flow ring: payload store, item, WR slot, doorbell (swg_dp_exec). *rid_out = the request id. 0 = ok or soft stop */
static int
swl25_send(struct swg_wx *x, struct swl25h *h, const uint8_t *fr, uint32_t len, const char *what, uint32_t *rid_out)
{
	struct swdp_store s[SWDP_MAX_STORES];
	uint8_t it[SWDP_ISZ_TX_EXT], ep;
	char nm[24];
	uint32_t rid, slot, n;
	uint64_t txa;
	int ex;

	if (!h->l.env.flow_open) {
		return swl25_gap(x, "L25 tx refused: flow ring not open");
	}
	rid = swdp_book_take(&h->txb, len);
	if (rid == 0u || h->l.flow.ring.wr >= SWL25_SHADOW_ITEMS) {
		return swl25_gap(x, "L25 tx buffers used up (each is used once per run; no shadow beyond 16 flow items)");
	}
	if (swdp_ring_post(&h->l.flow.ring, &slot, &ep) != 0) {
		return swl25_gap(x, "L25 flow ring full");
	}
	txa = h->l.env.txbuf.pa + (uint64_t)(rid - SWDP_TX_RID) * SWL25_BUF_SZ;
	if (swdp_build_txpost(it, h->l.env.flow_isz, 0u, rid, fr, len, 0u, txa) != 0) {
		return swl25_gap(x, "L25 tx item not buildable (frame length)");
	}
	h->l.env.wrf = slot;
	swg_hr(x, SWG_HR_L25S, "l25_send");
	n = swdp_plan_tx(&h->l.env, txa, fr + SWDP_ETH_HLEN, len - SWDP_ETH_HLEN, slot, h->l.flow.ring.wr, it, s);
	snprintf(nm, sizeof nm, "tx %.16s", what);
	ex = swg_dp_exec(x, &h->l.env, s, n, nm, 0);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	h->l.env.wrf = h->l.flow.ring.wr;
	h->tx_posts++;
	if (rid_out != NULL) {
		*rid_out = rid;
	}
	printf("SWEET WLAN: tx %s payload %u B\n", what, len - SWDP_ETH_HLEN);
	return 0;
}

/* consume the front item of D2H ring `ring` (3 or 4): the DP15 plan (RD slot + doorbell), then the host cursor. 0 = ok or soft stop */
static int
swl25_consume(struct swg_wx *x, struct swl25h *h, uint32_t ring)
{
	struct swdp_store s[SWDP_MAX_STORES];
	struct swdp_ring *r = ring == 3u ? &h->r3 : &h->r4;
	uint32_t n;
	int ex;

	h->l.env.rd_d2h[ring - 2u] = r->rd;
	n = swdp_plan_consume(&h->l.env, ring, (r->rd + 1u) % r->depth, s);
	ex = swg_dp_exec(x, &h->l.env, s, n, ring == 3u ? "consume ring 3" : "consume ring 4", 0);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	(void)swdp_d2h_consume(r);
	return 0;
}

/* T-73fc witness (W-JOIN L25, the AP never answers M2 with M3): the first SWL25_WIT_MAX EAPOL frames each way are logged as hex from the EAPOL header on (no Ethernet header, so no MAC address). On the air
 * these bytes are public (nonces, replay counter, MIC, RSNE); they hold no PSK, PMK, PTK or MAC [read from source: swj_rx/swj_ctx fields]. An offline recompute of the M2 MIC from the PSK on the host
 * (never in the repo) tells a MIC bug from an RSNE or PSK mismatch. File-scope buffer: no stack growth on the eapol path (the T-73fc stack overflow). */
#define SWL25_WIT_MAX 8u      /* T-924f: 3 -> 8 so the 2nd M1 batch (boot 20261107T213158Z: 3 + 3 after a 5 s gap) shows its replay counter and nonce; 8 x (header + 2 hex lines) per direction */
#define SWL25_WIT_BYTES 160u
#define SWL25_WIT_CHUNK 56u   /* bytes per log line: the USB log cuts a line near 250 chars (boot 20261107T201747Z: the 121 B tx frame was cut at 107 B) */
static char swl25_wit_buf[2u * SWL25_WIT_CHUNK + 1u];

/* hex of buf[0..len) in lines of SWL25_WIT_CHUNK bytes: "SWEET WLAN: L25 witness <what> part <k>: <hex>" */
static void
swl25_hexlines(const char *what, const uint8_t *buf, uint32_t len)
{
	uint32_t i, off, k = 0u, n;

	for (off = 0u; off < len; off += n, k++) {
		n = len - off < SWL25_WIT_CHUNK ? len - off : SWL25_WIT_CHUNK;
		for (i = 0; i < n; i++) {
			snprintf(swl25_wit_buf + 2u * i, 3u, "%02x", buf[off + i]);
		}
		printf("SWEET WLAN: L25 witness %s part %u: %s\n", what, k, swl25_wit_buf);
	}
}

static void
swl25_witness(const char *what, uint32_t *cnt, const uint8_t *eapol, uint32_t len)
{
	uint32_t n = len < SWL25_WIT_BYTES ? len : SWL25_WIT_BYTES;

	if (*cnt >= SWL25_WIT_MAX) {
		return;
	}
	(*cnt)++;
	printf("SWEET WLAN: L25 witness %s %u B (frame %u)%s\n", what, len, *cnt, len > n ? " (cut)" : "");
	swl25_hexlines(what, eapol, n);
}

/* once per boot: station MAC (SPA) and BSSID (AA) used for the PTK, the RSNE sent in M2 (assoc) and the beacon RSNE; all public on the air, no key material [read from source: swl25_cfg, swj_ctx_init] */
static void
swl25_witness_ctx(const struct swl25_cfg *c, const uint8_t mac[6])
{
	printf("SWEET WLAN: L25 witness addr spa=%02x:%02x:%02x:%02x:%02x:%02x aa=%02x:%02x:%02x:%02x:%02x:%02x\n",
	    mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], c->bssid[0], c->bssid[1], c->bssid[2], c->bssid[3], c->bssid[4], c->bssid[5]);
	printf("SWEET WLAN: L25 witness rsne_assoc %u B\n", (unsigned)c->rsne_assoc_len);
	swl25_hexlines("rsne_assoc", c->rsne_assoc, c->rsne_assoc_len);
	printf("SWEET WLAN: L25 witness rsne_beacon %u B\n", (unsigned)c->rsne_beacon_len);
	swl25_hexlines("rsne_beacon", c->rsne_beacon, c->rsne_beacon_len);
}

/* T-8d5f witness (W-JOIN H-sa, the AP acks every M2 and never answers): the 14 B Ethernet header of the first SWL25_WIT_MAX EAPOL frames each way, with the verdict against the addresses the
 * PTK was derived from. An rx M1 must carry DA = our SPA (the address the AP used for us, = the cur_etheraddr the shim read) and SA = the BSSID (AA); a tx M2 the reverse. A DA that is not our SPA
 * means the AP derived the PTK from another station address (H-sa) [read from source: swj_eth_wrap, swj_rx]. Log only, no key material; the addresses are on the air, the same as the `witness addr` line. */
static void
swl25_witness_eth(const char *what, uint32_t *cnt, const uint8_t *fr, uint32_t len, const uint8_t dst[6], const uint8_t src[6])
{
	if (*cnt >= SWL25_WIT_MAX || len < SWDP_ETH_HLEN) {
		return;
	}
	(*cnt)++;
	printf("SWEET WLAN: L25 witness eth %s da=%02x:%02x:%02x:%02x:%02x:%02x sa=%02x:%02x:%02x:%02x:%02x:%02x type=%02x%02x dst_ok=%u src_ok=%u (frame %u)\n", what,
	    fr[0], fr[1], fr[2], fr[3], fr[4], fr[5], fr[6], fr[7], fr[8], fr[9], fr[10], fr[11], fr[12], fr[13],
	    (unsigned)(memcmp(fr, dst, 6) == 0), (unsigned)(memcmp(fr + 6, src, 6) == 0), *cnt);
}

/* H6/H7/DP14 for one received Ethernet frame. 0 = ok or soft stop */
static int
swl25_frame(struct swg_wx *x, struct swl25h *h, const uint8_t *f, uint32_t len)
{
	uint8_t out[SWDP_OUT_MAX], eo[SWJ_TX_MAX];
	uint32_t olen = 0u, seq = 0u, rtt = 0u, rid = 0u;
	int r, ex;

	switch (swdp_net_rx(&h->net, f, len, (uint64_t)h->ms * 1000u, out, sizeof out, &olen, &seq, &rtt)) {
	case SWDP_RX_REPLY:
		return swl25_send(x, h, out, olen, "reply", NULL);
	case SWDP_RX_PING_REPLY:
		printf("SWEET WLAN: ping reply seq=%u rtt_us=%u\n", seq, rtt);
		return 0;
	case SWDP_RX_EAPOL:
		if (h->hs_done) {
			printf("%s\n", SWJ_GAP_X_REKEY);
			swl_gap("l25", "eapol after the keys were installed, ignored", "medium");
			return 0;
		}
		r = swj_rx(&h->sup, f, len, eo, sizeof eo, &olen);
		h->n_eapol_rx++;       /* T-924f: EAPOL frames received (M1 until M3 arrives; the end line names it) */
		swl25_witness_eth("rx", &h->wit_eth_rx, f, len, h->sup.spa, h->sup.aa);
		if (len > SWDP_ETH_HLEN) {
			swl25_witness("eapol rx", &h->wit_rx, f + SWDP_ETH_HLEN, len - SWDP_ETH_HLEN);
		}
		if (r != SWJ_RX_REPLY) {
			return 0;
		}
		swl25_witness_eth("tx", &h->wit_eth_tx, eo, olen, h->sup.aa, h->sup.spa);
		if (olen > SWDP_ETH_HLEN) {
			swl25_witness("eapol tx", &h->wit_tx, eo + SWDP_ETH_HLEN, olen - SWDP_ETH_HLEN);
		}
		h->last_msg = h->sup.install_ptk ? 4u : 2u;
		ex = swl25_send(x, h, eo, olen, h->last_msg == 4u ? "eapol msg 4" : "eapol msg 2", &rid);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (h->last_msg == 2u) {
			h->n_m2++;
			if (h->m2_rid == 0u) {
				h->m2_rid = rid;       /* T-924f: the FIRST M2; its tx status starts the settle clock of the counters read */
			}
		}
		if (h->sup.install_ptk && h->sup.install_gtk) {
			h->m4_rid = rid;
			h->want_keys = 1u;
		}
		return 0;
	default:
		return 0;
	}
}

/* T-73fc (W-JOIN L25: stack overflow on the eapol tx path): the received-frame copy (2048 B) is file-scope BSS, not a stack array; one swl25_d2h runs at a time (single hook thread) */
static uint8_t swl25_fr[SWL25_BUF_SZ];

/* ring 3 (tx status) or 4 (rx complete): up to SWL25_RXD_PER_PASS items. 0 = ok or soft stop, negative = stopped */
static int
swl25_d2h(struct swg_wx *x, struct swl25h *h, uint32_t ring)
{
	const uint32_t isz = ring == 3u ? SWDP_ISZ_TX_CMPLT : SWDP_ISZ_RX_CMPLT;
	struct swdp_ring *r = ring == 3u ? &h->r3 : &h->r4;
	uint8_t it[SWDP_ISZ_RX_CMPLT], *fr = swl25_fr;
	struct swdp_cmpl c;
	uint32_t v = 0u, done = 0u, len, idx;
	int pend, ex;

	swg_hr(x, SWG_HR_L25D, "l25_d2h");
	x->fail = "an L25 D2H write-index read failed";
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)] + 4ull * (ring - 2u), &v)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (v == 0xFFFFFFFFu) {
		x->fail = "an L25 D2H write index reads all ones (dead link)";
		return swg_stop(x, SWG_E_DEAD);
	}
	while (done < SWL25_RXD_PER_PASS) {
		pend = swdp_d2h_pending(r, v);
		if (pend < 0) {
			printf("WIFI GAP: L25 ring %u write index %u outside the ring\n", ring, v & 0xFFFFu);
			return swl25_gap(x, "L25 D2H write index corrupt");
		}
		if (pend == 0) {
			return 0;
		}
		ex = swl25_item(x, ring, r->rd, isz, it);
		if (ex != 0) {
			return ex;
		}
		ex = swdp_d2h_check(it, isz, r->seq);
		if (ex != 0) {
			printf("WIFI GAP: L25 ring %u item %u refused: check code %d (epoch/size/xor)\n", ring, r->rd, ex);
			return swl25_gap(x, "L25 D2H item check failed");
		}
		if (ring == 3u) {
			ex = swdp_parse_txstatus(it, &c);
			idx = c.rid - SWDP_TX_RID;
			if (ex != 0 || c.rid < SWDP_TX_RID || idx >= SWDP_NSLOT || !h->txb.used[idx] || (h->txdone & (1u << idx)) != 0u) {
				printf("WIFI GAP: L25 tx status for an unknown or repeated request id (code %d)\n", ex);
				return swl25_gap(x, "L25 tx status id not in flight");
			}
			h->txdone |= 1u << idx;
			if (c.rid == h->m2_rid && !h->m2_stat) {
				h->m2_stat = 1u;
				h->m2_stat_ms = h->ms;
			}
			if ((int16_t)c.status != 0 || c.tx_status != 0u) {
				h->tx_status_bad++;
				printf("WIFI GAP: L25 tx status error status=%d tx_status=%u\n", (int)(int16_t)c.status, c.tx_status);
				swl_gap("l25", "tx status error", "medium");
			}
		} else {
			ex = swdp_parse_rxcmpl(it, SWL25_BUF_SZ, x->rxoff, &c);
			idx = c.rid - SWDP_RX_RID;
			if (ex != 0 || c.rid < SWDP_RX_RID || idx >= SWDP_NSLOT || swdp_book_done(&h->l.rxb, c.rid, &len) != 0) {
				printf("WIFI GAP: L25 rx completion refused: code %d\n", ex);
				return swl25_gap(x, "L25 rx completion refused");
			}
			ex = swl25_dread(x, &h->l, h->l.env.rxbuf.pa + (uint64_t)idx * SWL25_BUF_SZ + c.data_offset, c.data_len, fr);
			if (ex != 0) {
				return ex;
			}
			h->rx_frames++;
			ex = swl25_frame(x, h, fr, c.data_len);
			if (ex != 0 || x->d4dstop) {
				return ex;
			}
		}
		ex = swl25_consume(x, h, ring);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (ring == 4u) {
			ex = swl25_rxrepost(x, &h->l);         /* sprint 2026-11-08: the buffer of this completion goes back on ring 1 (run join26c: 16 buffers, 5 LAN broadcasts, exhausted after echo 7) */
			if (ex != 0 || x->d4dstop) {
				return ex;
			}
		}
		done++;
		if (ring == 4u && h->l.rxb.inflight == 0u) {
			return swl25_gap(x, "L25 rx buffers exhausted (no re-post in this slice)");
		}
	}
	return 0;
}

/* one poll pass: ring 2 (events, DS), ring 4, ring 3, then one 1 ms step. 0 = ok or soft stop, negative = stopped */
static int
swl25_pass(struct swg_wx *x, struct swl25h *h)
{
	int ex = swg_e_idle(x);

	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swl25_d2h(x, h, 4u);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swl25_d2h(x, h, 3u);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (h->cnt0_ok && h->m2_stat && !h->cnt1_done && h->ms - h->m2_stat_ms >= SWL25_CNT_SETTLE_MS) {
		uint32_t cur[SWL25_CNT_N];
		int ok;

		h->cnt1_done = 1u;      /* once: T-924f, the counters SETTLE ms after the first M2's tx status (its retries are over), top level of the poll, not inside a D2H item */
		ex = swl25_cnt_read(x, cur, "first-m2", &ok);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (ok) {
			swl25_cnt_report("first-m2", h->cnt0, cur, h->n_eapol_rx, h->n_m2);
		}
	}
	x->b->delay_ms(x->b->ctx, SWL25_POLL_MS);
	h->ms += SWL25_POLL_MS;
	return 0;
}

/* poll for `ms` steps; stops early when *flag becomes non-zero (flag may be NULL). */
static int
swl25_poll(struct swg_wx *x, struct swl25h *h, uint32_t ms, const uint8_t *flag)
{
	const uint32_t t0 = h->ms;
	int ex;

	while (h->ms - t0 < ms && (flag == NULL || *flag == 0u)) {
		ex = swl25_pass(x, h);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
	}
	return 0;
}

/* one SET_VAR wsec_key of kb (secret): 0 = done or soft stop (a refused status stops the engine with the GAP, x->d4dstop), negative = stopped */
static int
swl25_setkey(struct swg_wx *x, uint8_t kb[SWJ_WSEC_KEY_SIZE], const char *row, const char *kind)
{
	struct swd4b_ioctl_d2h cm;
	char what[48];
	int ex;

	ex = swg_e_call_secret(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec_key", "wsec_key", kb, SWJ_WSEC_KEY_SIZE, 9u + SWJ_WSEC_KEY_SIZE, 1, 0u, NULL, 1, &cm);
	swl25_wipe(kb, SWJ_WSEC_KEY_SIZE);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		snprintf(what, sizeof what, "wsec_key %s refused status=%d", kind, (int)cm.status);
		SWG_J_STOP(x, row, "%.60s", what);
		return 0;
	}
	return 0;
}

/* H8 pairwise, H9 group, H10 wsec merge, after the M4 tx status (or the SWL25_TXWAIT_MS timeout, a GAP). 0 = done or soft stop, negative = stopped */
static int
swl25_keys(struct swg_wx *x, struct swl25h *h)
{
	uint8_t kb[SWJ_WSEC_KEY_SIZE], rsp[4], buf[16];
	struct swd4b_ioctl_d2h cm;
	uint32_t cur, nl, idx = h->m4_rid - SWDP_TX_RID;
	int ex;

	if (idx < SWDP_NSLOT && (h->txdone & (1u << idx)) == 0u) {
		uint8_t seen = 0u;
		uint32_t t0 = h->ms;

		while (h->ms - t0 < SWL25_TXWAIT_MS && !seen) {
			ex = swl25_pass(x, h);
			if (ex != 0 || x->d4dstop) {
				return ex;
			}
			seen = (h->txdone & (1u << idx)) != 0u;
		}
		if (!seen) {
			printf("WIFI GAP: L25 M4 tx status not seen in %u ms, keys installed anyway\n", SWL25_TXWAIT_MS);
			swl_gap("l25", "M4 tx status not seen", "medium");
		}
	}
	if (swj_wsec_key_pairwise(&h->sup, kb) != 0) {
		return swl25_gap(x, "H8 pairwise key not buildable");
	}
	ex = swl25_setkey(x, kb, "H8", "pairwise");
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: wsec_key pairwise set ok\n");
	if (swj_wsec_key_group(&h->sup, kb) != 0) {
		return swl25_gap(x, "H9 group key not buildable");
	}
	ex = swl25_setkey(x, kb, "H9", "group");
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: wsec_key group set ok idx=%u\n", (unsigned)h->sup.gtk_id);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR wsec", "wsec", NULL, 4u, 5u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		SWG_J_STOP(x, "H10", "wsec get refused status=%d", (int)cm.status);
		return 0;
	}
	cur = (uint32_t)rsp[0] | (uint32_t)rsp[1] << 8 | (uint32_t)rsp[2] << 16 | (uint32_t)rsp[3] << 24;
	nl = swj_var_u32(buf, sizeof buf, "wsec", swj_wsec_merge(cur, SWJ_WSEC_AES));
	if (nl == 0u) {
		return swl25_gap(x, "H10 wsec payload not buildable");
	}
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec", "wsec", buf + 5, 4u, nl, 1, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		SWG_J_STOP(x, "H10", "wsec set refused status=%d", (int)cm.status);
		return 0;
	}
	printf("SWEET WLAN: wsec merged ok\n");
	printf("%s\n", SWJ_GAP_H11);
	swl_gap("H11", "WLC_SCB_AUTHORIZE not issued", "medium");
	swl25_wipe(h->sup.tptk, sizeof h->sup.tptk);       /* the keys are in the dongle now; rekeys are not handled (SWJ_GAP_X_REKEY) */
	swl25_wipe(h->sup.gtk, sizeof h->sup.gtk);
	swl25_wipe(h->sup.pmk, sizeof h->sup.pmk);
	h->sup.install_ptk = h->sup.install_gtk = 0;
	h->hs_done = 1u;
	return 0;
}

/* L25 phase: poll until the keys are installed, at most SWG_J_BUDGET_MS. 0 = done or soft stop, negative = stopped */
static int
swl25_4way(struct swg_wx *x, struct swl25h *h)
{
	const uint32_t t0 = h->ms;
	uint32_t cur[SWL25_CNT_N];
	int ex, ok;

	ex = swl25_cnt_read(x, h->cnt0, "base", &ok);     /* T-924f: before the first M1 can arrive, so the deltas below start at the M2 (no frame of ours went out since the flow create) */
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	h->cnt0_ok = (uint8_t)ok;
	if (ok) {
		printf("SWEET WLAN: L25 witness counters base %s=%u %s=%u %s=%u %s=%u %s=%u %s=%u %s=%u %s=%u\n", swl25_cnt_name[0], h->cnt0[0], swl25_cnt_name[1], h->cnt0[1], swl25_cnt_name[2], h->cnt0[2],
		    swl25_cnt_name[3], h->cnt0[3], swl25_cnt_name[4], h->cnt0[4], swl25_cnt_name[5], h->cnt0[5], swl25_cnt_name[6], h->cnt0[6], swl25_cnt_name[7], h->cnt0[7]);
		printf("SWEET WLAN: L25 witness bytes base txbyte=%u rxbyte=%u\n", h->cnt0[SWL25_CNT_TXBYTE], h->cnt0[SWL25_CNT_RXBYTE]);
	}
	while (h->ms - t0 < SWG_J_BUDGET_MS) {
		ex = swl25_pass(x, h);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (h->want_keys && !h->hs_done) {
			ex = swl25_keys(x, h);
			if (ex != 0 || x->d4dstop) {
				return ex;
			}
		}
		if (h->hs_done) {
			return 0;
		}
	}
	if (h->cnt0_ok) {      /* T-924f: the cumulative deltas over every M2 of the run, before the gap stops the engine */
		ex = swl25_cnt_read(x, cur, "end", &ok);
		if (ex != 0) {
			return ex;
		}
		if (ok && !x->d4dstop) {
			swl25_cnt_report("end", h->cnt0, cur, h->n_eapol_rx, h->n_m2);
		}
	}
	printf("WIFI GAP: H12 4way not complete after %u ms (last message seen: %s)\n", h->ms - t0,
	    h->sup.state == SWJ_IDLE ? "none, no M1" : (h->sup.state == SWJ_M2_SENT ? "M1, M2 sent, no M3" : "M3"));
	swl_gap("H12", "4way not complete", "high");
	x->d4dstop = 1u;
	return 0;
}

/* L26 (DP13): ARP the gateway, SWL25_PINGS echo requests, the summary line. 0 = done or soft stop (the summary is printed in every case), negative = stopped */
static int
swl25_ping(struct swg_wx *x, struct swl25h *h)
{
	uint8_t f[SWDP_ETH_MAXFRAME];
	uint32_t i, len, t0;
	int ex = 0;

	for (i = 0; i < SWL25_ARP_TRIES && !h->net.gw_known; i++) {
		len = swdp_build_arp_req(&h->net, f);
		ex = swl25_send(x, h, f, len, "arp request", NULL);
		if (ex != 0 || x->d4dstop) {
			break;
		}
		for (t0 = h->ms; h->ms - t0 < SWL25_ARP_MS && !h->net.gw_known && ex == 0 && !x->d4dstop;) {
			ex = swl25_pass(x, h);
		}
		if (ex != 0 || x->d4dstop) {
			break;
		}
	}
	if (ex == 0 && !x->d4dstop && !h->net.gw_known) {
		printf("WIFI GAP: DP13 gateway did not answer %u ARP requests\n", SWL25_ARP_TRIES);
		swl_gap("DP13", "gateway ARP unanswered", "high");
		x->d4dstop = 1u;
	}
	for (i = 0; ex == 0 && !x->d4dstop && i < SWL25_PINGS; i++) {
		len = swdp_build_echo_req(&h->net, f, (uint64_t)h->ms * 1000u);
		if (len == 0u) {
			ex = swl25_gap(x, "L26 echo request not buildable");
			break;
		}
		ex = swl25_send(x, h, f, len, "echo request", NULL);
		if (ex != 0 || x->d4dstop) {
			break;
		}
		ex = swl25_poll(x, h, SWL25_PING_GAP_MS, NULL);
	}
	if (ex == 0 && !x->d4dstop) {
		for (t0 = h->ms; h->ms - t0 < SWL25_TAIL_MS && h->net.got < h->net.sent && ex == 0 && !x->d4dstop;) {
			ex = swl25_pass(x, h);
		}
	}
	swdp_ping_summary(&h->net);
	return ex;
}

static void swl25_rsne_report(const struct swl25h *h);

/* the run: called by swg_e after a finished L24 (link up). 0 = done or soft stop; negative = stopped */
static int swl25_live(struct swg_wx *x, struct swl25h *h);   /* W-UI, below: the live link after L26 */
static int swl25_probe_wifi(struct swg_wx *x, const char *at);   /* LOWCLK B3 (patch 0363), below */
static uint32_t swl25_probe_arm;                                /* ditto: bit 0 P1 due, bit 1 P2 due */

static int
swl25_go(struct swg_wx *x, unsigned lv, struct swl25h *h)
{
	const struct swl25_cfg *c = x->l25c;
	uint8_t mac[6];
	int ex;

	if (c == NULL) {
		return swl25_gap(x, "L25 not run: no join context (the glue delivers no station MAC, BSSID, PMK, RSNE or addresses yet)");
	}
	/* patch 0281: ip == 0 AND gw == 0 is a DHCP credential blob (mkwlancred --net 2 without ip/gw, docs/research/W-D6-rejoin-dhcp.md s.5): the join and the 4-way run as before, only L26 (the static ARP/ping) is skipped.
	 * Exactly one of the two zero stays refused: that is a half-filled blob, not DHCP. */
	if ((c->mac[0] & 1u) != 0u || swl25_zero(c->bssid, 6u) || swl25_zero(c->ip, 4u) != swl25_zero(c->gw, 4u)) {
		return swl25_gap(x, "L25 not run: station MAC, BSSID, IP or gateway missing or invalid");
	}
	ex = swl25_getmac(x, c->mac, mac);        /* T-baed step 1: cfg.mac all zero = take the firmware's; non-zero must match */
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if ((swl25_probe_arm & 1u) != 0u) {       /* LOWCLK B3 P1 (patch 0363): sweetprobe=1 only, once */
		swl25_probe_arm &= ~1u;
		ex = swl25_probe_wifi(x, "l25start");
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
	}
	memcpy(h->l.mac, mac, 6);
	memcpy(h->l.gwmac, c->bssid, 6);
	if (swd_rings[3].isz != SWDP_ISZ_TX_CMPLT || swd_rings[4].isz != SWDP_ISZ_RX_CMPLT) {
		return swl25_gap(x, "L25 not run: ring 3/4 item sizes differ from the tx status / rx complete sizes");
	}
	ex = swl25_env(x, &h->l);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	swdp_ring_init(&h->r3, h->l.env.depth_d2h[1]);
	swdp_ring_init(&h->r4, h->l.env.depth_d2h[2]);
	swdp_book_init(&h->txb, SWDP_TX_RID);
	/* T-9d71: the AP compares the RSNE in M2 with the one it stored from the association request (`RSN_IE Different in msg 2`); the association request was built by the firmware, so M2 carries what the firmware sent when it was read */
	if (swj_ctx_init_ex(&h->sup, c->pmk, mac, c->bssid, h->arsne_len != 0u ? h->arsne : c->rsne_assoc, h->arsne_len != 0u ? h->arsne_len : c->rsne_assoc_len, c->rsne_beacon, c->rsne_beacon_len, h->arsne_len != 0u) != 0) {
		return swl25_gap(x, "L25 not run: RSNE missing or too long");
	}
	swl25_rsne_report(h);
	swdp_net_init(&h->net, mac, c->ip, c->gw);
	swl25_witness_ctx(c, mac);
	ex = swl25_flow(x, &h->l, c->bssid);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swl25_rxposts(x, &h->l);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swl25_4way(x, h);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L25 done: keys installed, rx frames %u, tx posts %u, ring-0 WR %u, ring-2 RD %u\n", h->rx_frames, h->tx_posts, x->d4wr, x->d4rd);
	swg_d4d_done(x, 25u, lv, x->d4wr, x->d4rd);
	if (lv < 26u) {
		return 0;
	}
	x->elv = 26u;
	if (swl25_zero(c->ip, 4u) && swl25_zero(c->gw, 4u)) {
		/* patch 0281: DHCP credentials: no static address to ARP or ping. The evidence of the join is LINK + the 4-way + swl25_live; the level counts as finished so the engine is kept (en0 gets its address from iOS's DHCP) */
		swl_gap("l25", "L26 not run: DHCP credentials (no static address)", "medium");
		printf("SWEET WLAN: L26 not run: DHCP credentials (no static address)\n");
		swg_d4d_done(x, 26u, lv, x->d4wr, x->d4rd);
		return swl25_live(x, h);
	}
	ex = swl25_ping(x, h);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	swg_d4d_done(x, 26u, lv, x->d4wr, x->d4rd);
	return swl25_live(x, h);       /* W-UI: what the Apple80211 kept link reports comes from here */
}

/* T-9d71 (W-JOIN, cause named from the AP's own log: `RSN_IE Different in msg 2 of 4-way handshake!`, no `MIC Different`): the RSNE of the firmware's association request, read after link up the way the Linux/Broadcom driver does it
 * [src wl_cfg80211.c.new:14253-14320 wl_get_assoc_ies]: GET_VAR `assoc_info` (wl_assoc_info_t: u32 req_len, u32 resp_len, u32 flags, then dot11_assoc_req 4 B ...), then GET_VAR `assoc_req_ies` whose reply starts with the request's
 * IEs, req_len - 4 B (minus 6 B more for a reassociation, flags & 1). Both are read-only GETs of fixed length (rows of the L25 ioctl table, sweet_wlan_l25_wl.h). The first RSNE (IE 48) of the IEs goes to h->arsne. Every failure is a printed gap
 * and M2 then carries the beacon RSNE as before. The reply copy is file scope (no stack growth, the T-73fc class). */
#define SWL25_AINFO_LEN 64u
#define SWL25_AIES_LEN  512u
static uint8_t swl25_ai_rsp[SWL25_AIES_LEN + 4u];     /* +4 for the word-wise copy of the last bytes */

static void
swl25_ai_gap(const char *why)
{
	char gb[96];

	snprintf(gb, sizeof gb, "L25 assoc RSNE not read, M2 keeps the beacon RSNE: %.32s", why);
	swl_gap("l25", "L25 assoc RSNE not read, M2 keeps the beacon RSNE", "high");
	printf("WIFI GAP: %s\n", gb);
}

/* FNV-1a 32: a short fingerprint so the log can show two RSNEs are equal or not without the bytes in a committed file (the witness lines carry the bytes in the gitignored run log) */
static uint32_t
swl25_fnv(const uint8_t *p, uint32_t n)
{
	uint32_t h = 2166136261u, i;

	for (i = 0; i < n; i++) {
		h = (h ^ p[i]) * 16777619u;
	}
	return h;
}

/* the first RSNE (IE 48) in ies[0..n): 0 = found (*off = offset of its id byte, *len = 2 + length), -1 = an element overruns the buffer, -2 = none */
static int
swl25_find_rsne(const uint8_t *ies, uint32_t n, uint32_t *off, uint32_t *len)
{
	uint32_t pos = 0u;

	while (pos + 2u <= n) {
		const uint32_t el = ies[pos + 1u];

		if (pos + 2u + el > n) {
			return -1;
		}
		if (ies[pos] == 48u) {
			*off = pos;
			*len = 2u + el;
			return 0;
		}
		pos += 2u + el;
	}
	return -2;
}

/* ---- W-UI (sprint 2026-11-08): the live link from the firmware, for the Apple80211 kept link ---- */
#define SWL25_WLC_GET_BSSID 23u      /* [src refs/wlan-stage5/wlioctl_defs.h.new:703] */
#define SWL25_WLC_GET_RSSI  127u     /* [src wlioctl_defs.h.new:810] */
static void
swl25_live_gap(const char *what)
{
	swl_gap("l25", "live link value not read from the firmware", "high");
	printf("WIFI GAP: L25 live link: %s\n", what);
}

/* the SSID element (IE 0) of ies[0..n) into h->live_ssid; a missing or overlong one is a GAP */
static void
swl25_live_ssid(struct swl25h *h, const uint8_t *ies, uint32_t n)
{
	uint32_t pos = 0u;

	while (pos + 2u <= n) {
		const uint32_t el = ies[pos + 1u];

		if (pos + 2u + el > n) {
			break;
		}
		if (ies[pos] == 0u) {
			if (el == 0u || el > sizeof h->live_ssid) {
				swl25_live_gap("SSID element of the association request is empty or longer than 32");
				return;
			}
			memcpy(h->live_ssid, ies + pos + 2u, el);
			h->live_ssid_len = el;
			h->live_ok |= 1u;
			return;
		}
		pos += 2u + el;
	}
	swl25_live_gap("no SSID element in the association request the firmware sent");
}

/* the 20 MHz primary channel of a chanspec [src refs/wlan-stage5/bcmwifi_channels.h.new:98-146 (CHAN_MASK 0xff, CTL_SB 0x0700 >> 8, BW 0x3800: 20 0x1000, 40 0x1800, 80 0x2000, 160 0x2800),
 * :166-176 LOWER_20_SB = center - 2, LL_20_SB = center - 6: primary = center - (n20 - 1) * 2 + sb * 4]. 0 = a bandwidth this decoder does not know (a GAP) */
static uint32_t
swl25_chspec_ctl(uint32_t cs)
{
	const uint32_t c = cs & 0xFFu, sb = (cs & 0x0700u) >> 8, bw = cs & 0x3800u;
	uint32_t n20;

	switch (bw) {
	case 0x1000u: return c;
	case 0x1800u: n20 = 2u; break;
	case 0x2000u: n20 = 4u; break;
	case 0x2800u: n20 = 8u; break;
	default: return 0u;
	}
	if (sb >= n20 || c < (n20 - 1u) * 2u) {
		return 0u;
	}
	return c - (n20 - 1u) * 2u + sb * 4u;
}

/* ---- LOWCLK B3 (xnu patch 0363, docs/research/161 s.3): three read-only firmware GETs, logged once at two points of the boot ----
 * WLC_GET_PM (85): 4-byte int, 0 = OFF/CAM, 1 = MAX/PS, 2 = FAST [src Linux v6.6 brcmfmac fwil.h:49, include/defs.h:76-78; OpenBSD bwfmreg.h:242-244, :284]. GET_VAR "country": brcmf_fil_country_le
 * { char abbrev[4]; s32 rev; char ccode[4] } = 12 B [src brcmfmac fwil_types.h:764-768, cfg80211.c:8206]. GET_VAR "chanspecs": brcmf_chanspec_list { u32 count; u32 element[] }, each element's low 16 bits
 * is the chanspec [src fwil_types.h:708-711, cfg80211.c:6957, :6981]; asked at 1200 B (299 entries); band bits 0xC000 (2G 0x0000, 6G 0x4000, 5G 0xC000) and width bits 0x3800 (20 0x1000, 40 0x1800, 80 0x2000,
 * 160 0x2800) [src refs/wlan-stage5/bcmwifi_channels.h.new:137-154]. Gate: the glue arms it (swl25_probe_set) from boot-arg sweetprobe=1; default off = no call, no line. P1 ("l25start", not armed by default) runs once after the station MAC
 * read of swl25_go (L25 start: the join of L24 is done, the 4-way is not), P2 ("joined") once at the first swl25_live (after L26). One ioctl at a time, no retry; a refused, short or odd reply is a
 * `SWEET PROBE: GAP wifi ...` line and the value stays unread. The three rows are in the L25 whitelist (sweet_wlan_l25_wl.h). */
#define SWL25_WLC_GET_PM    85u
#define SWL25_CHSP_LEN      1200u
#define SWL25_CHSP_MAX      ((SWL25_CHSP_LEN - 4u) / 4u)      /* 299 entries */
static uint8_t swl25_chsp_rsp[SWL25_CHSP_LEN + 4u];           /* file scope: no stack growth (the T-73fc overflow); +4 for the word-wise copy of the last bytes */
/* review 0363 finding 3 + Opus finding 1: only P2 (after L26) is armed; P1 runs before the 4-way and would also lose it. A probe GET without an answer at P2 still sets d4dstop (g2.h SWG_E_STOP) and the G2 undo drops the link (keep needs !d4dstop): Wi-Fi is off for that boot, `L25 STOP` is logged. Bit 0 (P1) stays compiled for the host tests. */
#define SWL25_PROBE_ARM_ON  2u
static inline void
swl25_probe_set(uint32_t on)
{
	swl25_probe_arm = on != 0u ? SWL25_PROBE_ARM_ON : 0u;
}

static void
swl25_probe_gap(const char *what, const struct swd4b_ioctl_d2h *cm)
{
	swl_gap("l25", "wifi probe value not read from the firmware", "medium");
	printf("SWEET PROBE: GAP wifi %s status=%d resp_len=%u: value unread\n", what, (int)cm->status, (unsigned)cm->resp_len);
}

static const char *
swl25_pm_name(int32_t v)
{
	return v == 0 ? "OFF/CAM" : v == 1 ? "MAX/PS" : v == 2 ? "FAST" : "?";
}

/* one NUL-terminated field of at most n bytes as printable text (anything else is '?'), into out[n + 1] */
static void
swl25_probe_str(const uint8_t *p, uint32_t n, char *out)
{
	uint32_t i;

	for (i = 0; i < n && p[i] != 0u; i++) {
		out[i] = (p[i] >= 0x20u && p[i] < 0x7Fu) ? (char)p[i] : '?';
	}
	out[i] = '\0';
}

static int
swl25_probe_pm(struct swg_wx *x, const char *at)
{
	uint8_t rsp[8];
	struct swd4b_ioctl_d2h cm;
	int ex;

	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWL25_WLC_GET_PM, "WLC_GET_PM", "", NULL, 0u, 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 4u) {
		swl25_probe_gap("pm", &cm);
	} else if (swd4b_get32(rsp) > 2u) {
		printf("SWEET PROBE: GAP wifi pm value %u is outside 0..2 (OFF/MAX/FAST): at=%s\n", (unsigned)swd4b_get32(rsp), at);
	} else {
		printf("SWEET PROBE: wifi pm=%d (%s) at=%s\n", (int)swd4b_get32(rsp), swl25_pm_name((int32_t)swd4b_get32(rsp)), at);
	}
	return 0;
}

static int
swl25_probe_country(struct swg_wx *x, const char *at)
{
	uint8_t rsp[16];
	struct swd4b_ioctl_d2h cm;
	char ab[5], cc[5];
	int ex;

	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR country", "country", NULL, 0u, 8u + 12u, 1, 12u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 12u) {
		swl25_probe_gap("country", &cm);
		return 0;
	}
	swl25_probe_str(rsp, 4u, ab);
	swl25_probe_str(rsp + 8u, 4u, cc);
	printf("SWEET PROBE: wifi country abbrev=%s rev=%d ccode=%s at=%s\n", ab, (int)swd4b_get32(rsp + 4u), cc, at);
	if (ab[0] == '\0' && cc[0] == '\0') {
		printf("SWEET PROBE: GAP wifi country abbrev and ccode are both empty: no country set at=%s\n", at);
	}
	return 0;
}

/* a list of 20 MHz channel numbers as "a,b,c" into out[cap]; '+' marks a cut list */
static void
swl25_probe_list_add(char *out, uint32_t cap, uint32_t ch)
{
	uint32_t n = 0u;

	while (out[n] != '\0') {
		n++;
	}
	if (n + 6u >= cap) {
		if (n + 2u < cap) {
			out[n] = '+';
			out[n + 1u] = '\0';
		}
		return;
	}
	snprintf(out + n, cap - n, "%s%u", n != 0u ? "," : "", (unsigned)ch);
}

static void
swl25_probe_chsp_report(const uint8_t *r, uint32_t count, const char *at)
{
	char l2[200], l5[300], l6[300];
	uint32_t i, n40 = 0u, n80 = 0u, n160 = 0u, other = 0u;

	l2[0] = l5[0] = l6[0] = '\0';
	for (i = 0; i < count; i++) {
		const uint32_t cs = swd4b_get32(r + 4u + 4u * i) & 0xFFFFu, bw = cs & 0x3800u, band = cs & 0xC000u;

		if (bw == 0x1000u && band == 0x0000u) {
			swl25_probe_list_add(l2, sizeof l2, cs & 0xFFu);
		} else if (bw == 0x1000u && band == 0xC000u) {
			swl25_probe_list_add(l5, sizeof l5, cs & 0xFFu);
		} else if (bw == 0x1000u && band == 0x4000u) {
			swl25_probe_list_add(l6, sizeof l6, cs & 0xFFu);
		} else if (bw == 0x1800u) {
			n40++;
		} else if (bw == 0x2000u) {
			n80++;
		} else if (bw == 0x2800u) {
			n160++;
		} else {
			other++;
		}
	}
	printf("SWEET PROBE: wifi chanspecs count=%u ch20_2g=%s ch20_5g=%s ch20_6g=%s n40=%u n80=%u n160=%u nother=%u at=%s\n", (unsigned)count, l2[0] != '\0' ? l2 : "-",
	    l5[0] != '\0' ? l5 : "-", l6[0] != '\0' ? l6 : "-", (unsigned)n40, (unsigned)n80, (unsigned)n160, (unsigned)other, at);
}

static int
swl25_probe_chanspecs(struct swg_wx *x, const char *at)
{
	struct swd4b_ioctl_d2h cm;
	uint32_t count;
	int ex;

	memset(swl25_chsp_rsp, 0, sizeof swl25_chsp_rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR chanspecs", "chanspecs", NULL, 0u, SWL25_CHSP_LEN, 1, SWL25_CHSP_LEN, swl25_chsp_rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 4u) {
		swl25_probe_gap("chanspecs", &cm);
		return 0;
	}
	count = swd4b_get32(swl25_chsp_rsp);
	if (count > SWL25_CHSP_MAX || count > (cm.resp_len < SWL25_CHSP_LEN ? cm.resp_len : SWL25_CHSP_LEN) / 4u - 1u) {
		printf("SWEET PROBE: GAP wifi chanspecs count=%u does not fit the %u B reply (resp_len %u, at most %u entries): list unread at=%s\n", (unsigned)count, (unsigned)SWL25_CHSP_LEN,
		    (unsigned)cm.resp_len, (unsigned)SWL25_CHSP_MAX, at);
		return 0;
	}
	swl25_probe_chsp_report(swl25_chsp_rsp, count, at);
	return 0;
}

/* the three reads, in this order, one ioctl each. 0 = done (also after a GAP); negative = the bus stopped; x->d4dstop = a soft stop (the caller returns) */
static int
swl25_probe_wifi(struct swg_wx *x, const char *at)
{
	int ex;

	ex = swl25_probe_pm(x, at);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swl25_probe_country(x, at);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	return swl25_probe_chanspecs(x, at);
}

/* After L26 (the link carried 10 pings): BSSID, chanspec and RSSI as the firmware reports them now. Read-only commands, one at a time, judged by the L25 whitelist (sweet_wlan_l25_wl.h rows 8-10):
 * WLC_GET_BSSID 23 (6 B) [src refs/wlan-stage5/wl_cfg80211.c.new:2063], GET_VAR "chanspec" (int) [src :5726, :12314], WLC_GET_RSSI 127 (scb_val_t 12 B, val 0 = the associated AP) [src :7867-7875,
 * wlioctl.h.new:2621-2625]. A refused or short answer is a GAP and the value stays unread (no stop: the link itself is fine). 0 = done; negative = the bus stopped */
static int
swl25_live(struct swg_wx *x, struct swl25h *h)
{
	uint8_t rsp[16];
	struct swd4b_ioctl_d2h cm;
	char gb[96];
	int ex;

	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWL25_WLC_GET_BSSID, "WLC_GET_BSSID", "", NULL, 0u, 6u, 1, 6u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 6u || swl25_zero(rsp, 6u)) {
		snprintf(gb, sizeof gb, "WLC_GET_BSSID status=%d resp_len %u (or all zero)", (int)cm.status, cm.resp_len);
		swl25_live_gap(gb);
	} else {
		memcpy(h->live_bssid, rsp, 6);
		h->live_ok |= 2u;
	}
	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR chanspec", "chanspec", NULL, 0u, 9u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 4u) {
		snprintf(gb, sizeof gb, "chanspec status=%d resp_len %u", (int)cm.status, cm.resp_len);
		swl25_live_gap(gb);
	} else {
		h->live_chanspec = swd4b_get32(rsp) & 0xFFFFu;
		h->live_chan = swl25_chspec_ctl(h->live_chanspec);
		if (h->live_chan == 0u) {
			snprintf(gb, sizeof gb, "chanspec %#x: bandwidth/sideband not decoded", (unsigned)h->live_chanspec);
			swl25_live_gap(gb);
		} else {
			h->live_ok |= 4u;
		}
	}
	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWL25_WLC_GET_RSSI, "WLC_GET_RSSI", "", NULL, 0u, 12u, 1, 12u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0 || cm.resp_len < 4u || (int32_t)swd4b_get32(rsp) >= 0 || (int32_t)swd4b_get32(rsp) < -127) {
		snprintf(gb, sizeof gb, "WLC_GET_RSSI status=%d resp_len %u value %d (outside -127..-1)", (int)cm.status, cm.resp_len, (int)swd4b_get32(rsp));
		swl25_live_gap(gb);
	} else {
		h->live_rssi = (int32_t)swd4b_get32(rsp);
		h->live_ok |= 8u;
	}
	if ((swl25_probe_arm & 2u) != 0u) {       /* LOWCLK B3 P2 (patch 0363): sweetprobe=1 only, once, after the three live reads */
		swl25_probe_arm &= ~2u;
		ex = swl25_probe_wifi(x, "joined");
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
	}
	printf("SWEET WLAN: L25 live link (firmware): ok=%#x ssid_len=%u bssid=%s chanspec=%#x chan=%u rssi=%d\n", (unsigned)h->live_ok, (unsigned)h->live_ssid_len,
	    (h->live_ok & 2u) ? "read" : "unread", (unsigned)h->live_chanspec, (unsigned)h->live_chan, (int)h->live_rssi);
	return 0;
}

/* patch 0281 (checklist item 3): ring 2 carries events now (an AP loss makes several), so the idle check of swl25_live_rssi could skip every refresh. The keep thread calls this first: while D2H-W != the read count it runs one more keep pass
 * (swg_keep_r2: decode, consume, re-post), at most 4 extra passes of SWG_E_BATCH items. 0 = ring 2 idle or the passes ran (the refresh makes its own idle check and says so when it still skips), 1 = a soft stop (x->d4dstop, the data path ends in the
 * caller's loop), negative = the bus stopped */
static inline int
swl25_rssi_drain(struct swg_wx *x)
{
	return swg_rssi_drain(x);       /* the loop lives in sweet_wlan_g2.h next to swg_keep_r2 so the g2 host test runs it (review 0281 F1) */
}

/* Part A (sprint-wifi 2026-11-09): only the WLC_GET_RSSI step of swl25_live, for the keep thread's refresh (xnu patch 0273, every 5 s under its lock; the same judged read-only ioctl, L25 whitelist row 10).
 * 0 = read (*rssi, h->live_rssi), 1 = refused or out of range (a GAP, nothing stopped), negative = the bus stopped */
static inline int
swl25_live_rssi(struct swg_wx *x, struct swl25h *h, int32_t *rssi)
{
	uint8_t rsp[16];
	struct swd4b_ioctl_d2h cm;
	char gb[96];
	int ex;

	uint32_t v = 0u;

	/* review perf-c F1: only with ring 2 idle (D2H-W slot == the read count), so the call meets no item it did not ask for (patch 0281: the keep thread drains ring 2 first, swl25_rssi_drain) */
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v) || v == 0xFFFFFFFFu || (v & 0xFFFFu) != x->d4rd % SWG_R2_DEPTH) {
		swl25_live_gap("RSSI refresh skipped: ring 2 not idle (or its write index unreadable)");
		return 1;
	}
	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	x->krefresh = 1u;
	ex = swg_e_call(x, SWL25_WLC_GET_RSSI, "WLC_GET_RSSI", "", NULL, 0u, 12u, 1, 12u, rsp, 1, &cm, NULL);
	x->krefresh = 0u;
	if (ex != 0 || x->d4dstop) {
		return ex != 0 ? ex : -1;
	}
	if (cm.status == 0 && cm.resp_len >= 4u && swd4b_get32(rsp) == 0u) {
		/* patch 0281: WLC_GET_RSSI = 0 is only a hint (observed after the AP loss, W-D6 aploss2; its meaning is [guess]): logged, never drives the link state (the events do). No GAP while the detector says DOWN, a GAP while UP (unexpected). */
		printf("SWEET WLAN: keep RSSI refresh value 0 (hint only, the link state comes from the events; detector state %s)\n", x->klink != 0u ? "DOWN" : "UP");
		if (x->klink == 0u) {
			swl25_live_gap("RSSI refresh: WLC_GET_RSSI value 0 while the detector says UP");
		}
		return 1;
	}
	if (cm.status != 0 || cm.resp_len < 4u || (int32_t)swd4b_get32(rsp) >= 0 || (int32_t)swd4b_get32(rsp) < -127) {
		snprintf(gb, sizeof gb, "RSSI refresh: WLC_GET_RSSI status=%d resp_len %u value %d (outside -127..-1)", (int)cm.status, cm.resp_len, (int)swd4b_get32(rsp));
		swl25_live_gap(gb);
		return 1;
	}
	h->live_rssi = *rssi = (int32_t)swd4b_get32(rsp);
	return 0;
}

/* 0 = done or soft stop with x->d4dstop; negative = the bus stopped. A refused or unparsable reply is a gap and returns 0 (h->arsne_len stays 0). Patch 0282: no l25c gate here, the re-join (swl25_rj_*) reads the same two GETs in the keep phase */
static int
swl25_assoc_rsne_read(struct swg_wx *x, struct swl25h *h)
{
	struct swd4b_ioctl_d2h cm;
	const uint8_t *r = swl25_ai_rsp;
	uint32_t req_len, flags, hdr, iel, off = 0u, len = 0u, avail;
	char gb[48];
	int ex, fr;

	h->arsne_len = 0u;
	memset(swl25_ai_rsp, 0, sizeof swl25_ai_rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR assoc_info", "assoc_info", NULL, 0u, SWL25_AINFO_LEN, 1, SWL25_AINFO_LEN, swl25_ai_rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		snprintf(gb, sizeof gb, "assoc_info refused status=%d", (int)cm.status);
		swl25_ai_gap(gb);
		return 0;
	}
	if (cm.resp_len < 12u) {
		snprintf(gb, sizeof gb, "assoc_info answer %u B < 12", cm.resp_len);
		swl25_ai_gap(gb);
		return 0;
	}
	req_len = swd4b_get32(r);
	flags = swd4b_get32(r + 8);
	printf("SWEET WLAN: L25 assoc_info: req_len %u resp_len %u flags %#x (answer %u B)\n", req_len, swd4b_get32(r + 4), flags, cm.resp_len);
	hdr = 4u + ((flags & 1u) != 0u ? 6u : 0u);
	if (req_len <= hdr || req_len - hdr > SWL25_AIES_LEN) {
		snprintf(gb, sizeof gb, "req_len %u outside %u..%u", req_len, hdr + 1u, hdr + SWL25_AIES_LEN);
		swl25_ai_gap(gb);
		return 0;
	}
	iel = req_len - hdr;
	memset(swl25_ai_rsp, 0, sizeof swl25_ai_rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR assoc_req_ies", "assoc_req_ies", NULL, 0u, SWL25_AIES_LEN, 1, SWL25_AIES_LEN, swl25_ai_rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		snprintf(gb, sizeof gb, "assoc_req_ies refused status=%d", (int)cm.status);
		swl25_ai_gap(gb);
		return 0;
	}
	avail = cm.resp_len < SWL25_AIES_LEN ? cm.resp_len : SWL25_AIES_LEN;
	printf("SWEET WLAN: L25 assoc_req_ies: %u IE bytes expected, answer %u B\n", iel, cm.resp_len);
	if (avail < iel) {
		snprintf(gb, sizeof gb, "answer %u B < %u IE bytes", avail, iel);
		swl25_ai_gap(gb);
		return 0;
	}
	swl25_live_ssid(h, swl25_ai_rsp, iel);       /* W-UI: the SSID element of the firmware's own association request (before the RSNE checks, which may return) */
	fr = swl25_find_rsne(swl25_ai_rsp, iel, &off, &len);
	if (fr != 0) {
		swl25_ai_gap(fr == -1 ? "an IE overruns the request" : "no RSNE (IE 48) in the request");
		return 0;
	}
	if (len < 4u || len > sizeof h->arsne) {
		snprintf(gb, sizeof gb, "RSNE length %u outside 4..%u", len, (unsigned)sizeof h->arsne);
		swl25_ai_gap(gb);
		return 0;
	}
	memcpy(h->arsne, swl25_ai_rsp + off, len);
	h->arsne_len = len;
	return 0;
}

static int
swl25_assoc_rsne(struct swg_wx *x, struct swl25h *h)
{
	if (x->l25c == NULL) {
		h->arsne_len = 0u;
		return 0;       /* swl25_go prints its own refusal */
	}
	return swl25_assoc_rsne_read(x, h);
}

/* once M2's source is fixed (swj_ctx_init done): the RSNE of the firmware's association request (a), the RSNE the 4-way takes for M2 key data (m2) and the beacon one (b), as lengths, fingerprints and the first differing offset; no bytes */
static void
swl25_rsne_report(const struct swl25h *h)
{
	const struct swj_ctx *s = &h->sup;
	uint32_t i, d = 0u, first = 0u, n;

	if (h->arsne_len == 0u) {
		printf("SWEET WLAN: L25 rsne assoc=unread m2=%u (beacon RSNE, the firmware's association-request RSNE was not read)\n", (unsigned)s->rsne_assoc_len);
		return;
	}
	n = h->arsne_len < s->rsne_beacon_len ? h->arsne_len : s->rsne_beacon_len;
	for (i = 0; i < n; i++) {
		if (h->arsne[i] != s->rsne_beacon[i]) {
			if (d++ == 0u) {
				first = i;
			}
		}
	}
	printf("SWEET WLAN: L25 rsne assoc=%u m2=%u equal=%d hash_assoc=%08x hash_m2=%08x\n", h->arsne_len, (unsigned)s->rsne_assoc_len,
	    (h->arsne_len == s->rsne_assoc_len && memcmp(h->arsne, s->rsne_assoc, h->arsne_len) == 0) ? 1 : 0, swl25_fnv(h->arsne, h->arsne_len), swl25_fnv(s->rsne_assoc, s->rsne_assoc_len));
	printf("SWEET WLAN: L25 rsne assoc vs beacon: beacon=%u hash_beacon=%08x equal=%d bytes_differ=%u first_diff=%d\n", (unsigned)s->rsne_beacon_len, swl25_fnv(s->rsne_beacon, s->rsne_beacon_len),
	    (h->arsne_len == s->rsne_beacon_len && d == 0u) ? 1 : 0, d + (h->arsne_len > s->rsne_beacon_len ? h->arsne_len - s->rsne_beacon_len : s->rsne_beacon_len - h->arsne_len), d != 0u ? (int)first : -1);
}

/* ---- patch 0282 (W-D6 boot B, docs/research/W-D6-rejoin-dhcp.md s.3, 6): the RE-JOIN after an AP loss, driven by the keep thread (the only user of the engine in the keep phase; the RSSI refresh is the precedent for an ioctl inside the thread's pass).
 * States (x->rj_state, sweet_wlan_g2.h): UP -> DOWN (the 0281 detector) -> JOINING (the join SET_VAR sent, waiting for SET_SSID 0 + LINK up) -> KEYING (the host 4-way: EAPOL frames reach swl25_rj_eapol through the en0 receive path, wlan_en0.h en0_rx_deliver)
 * -> UP (keys installed, link values read, iOS told by the thread). A failed attempt returns to DOWN and waits 2, 4, 8, 16, then 30 s (swg_rj_backoff_ms) for ever. Nothing here runs unless the detector went DOWN.
 * Secrets: the PMK and the join payload are kept in struct swl25_rj for this purpose only (user decision 2026-11-10: kept in RAM, never printed or written elsewhere, wiped when the engine dies: swl25_rj_wipe). No log line carries a key, the PMK, the SSID or an address. */
#define SWL25_RJ_TXWAIT_MS 200u          /* wait for the M4 tx completion before the key install, as the boot path (SWL25_TXWAIT_MS) [decision, no source] */
/* review 0282 N1: the m4_busy argument of swl25_rj_step as the glue computes it: the tx buffer of the last EAPOL reply is still in flight. NOT gated on m4_seen: the step sets m4_seen itself in the pass that sees m4_pending,
 * so a gate here would read 0 exactly when the wait must start. The host case and the glue use this one macro. */
#define SWL25_RJ_BUSY_ARG(rj, buf_busy) ((void)(rj), (buf_busy))
#define SWL25_RJ_DEFER_MS  100u          /* an attempt that finds ring 2 busy is retried this much later (not counted as an attempt) */

struct swl25_rj {
	uint8_t armed;                   /* the payload and the PMK below are present */
	uint8_t pmk[32];
	uint8_t join[SWJ_EXT_JOIN_V1_SIZE];       /* the boot join value (wl_extjoin_params_v1_t, wildcard BSSID), the bytes after "join\0" of sw_jc */
	uint32_t v_wpa_auth, v_wsec, v_auth, v_mfp;       /* the values the boot join set (sw_jc); a re-join re-sends one only when the firmware reads back something else */
	uint8_t rsne_beacon[SWJ_RSNE_MAX], rsne_assoc[SWJ_RSNE_MAX];     /* public: the RSNE the boot scan record gave (M3 must repeat the AP's RSNE) and the one M2 carried */
	uint32_t rsne_beacon_len, rsne_assoc_len;
	struct swl25h *h;                /* the supplicant (h->sup), the station MAC (h->l.mac) and the live values live in the engine's run state */
	/* the machine */
	uint64_t now;                    /* ms, set by swl25_rj_step each pass (the EAPOL reply stamps the M4 with it) */
	uint64_t t_down, t_next, t_phase;
	uint32_t n, n_up, n_fail;
	uint8_t down_seen, eapol_on, m4_seen, up_new, noarm_gap, defer_gap;
	uint8_t m4_pending;              /* review 0282 F6: swj_rx produced an M4 reply; m4_seen follows only when en0 posted it (swl25_rj_step tx_ok) */
	const char *where;               /* review 0282 F1: the step a soft stop happened in (a literal) */
	uint32_t eapol_m1_late;          /* review 0282 F4: M1 frames dropped after M3 was accepted */
	uint64_t m4_ms;
	uint8_t bssid[6];                /* the AP of the last join (WLC_GET_BSSID), may differ from the boot one */
	uint32_t chan, ok;               /* the values for iOS after UP: swl25h.live_chan / live_ok */
	int32_t rssi;
	uint32_t eapol_rx, eapol_tx;
};

static inline void
swl25_rj_wipe(struct swl25_rj *rj, const char *why)
{
	const uint8_t was = rj->armed;

	if (rj->h != NULL) {
		swl25_wipe(&rj->h->sup, sizeof rj->h->sup);       /* review 0282 F2: the supplicant's copy (PMK, PTK, GTK, nonces) goes with the kept one */
	}
	swl25_wipe(rj, sizeof *rj);
	if (was) {
		printf("SWEET WLAN: keep rejoin: kept join payload and PMK wiped (%s)\n", why);
	}
}

/* copy what a re-join needs out of the boot run's tables, before the glue wipes them: the PMK, the join table jc[0..njc) (names wpa_auth, wsec, auth, mfp, join; "H1".."H5" rows of swj_join_cmds) and the boot RSNEs. 0 = armed, -1 = something missing (a GAP; the machine then
 * cannot re-join and says so when the link is lost) */
static inline int
swl25_rj_arm(struct swl25_rj *rj, struct swl25h *h, const uint8_t pmk[32], const struct swg_jcmd *jc, uint32_t njc, const uint8_t *rsne_beacon, uint32_t rbl, const uint8_t *rsne_assoc, uint32_t ral)
{
	static const char *const nm[5] = { "wpa_auth", "wsec", "auth", "mfp", "join" };
	uint32_t i, k, got = 0u, val[4] = { 0u, 0u, 0u, 0u };
	int bad = 0;

	swl25_wipe(rj, sizeof *rj);
	if (pmk == NULL || swl25_zero(pmk, 32u) || jc == NULL || rsne_beacon == NULL || rbl == 0u || rbl > SWJ_RSNE_MAX) {
		bad = 1;
	}
	for (i = 0; !bad && i < njc; i++) {
		for (k = 0; k < 5u; k++) {
			const uint32_t nl = (uint32_t)strlen(nm[k]) + 1u;

			if (jc[i].cmd != SWJ_WLC_SET_VAR || jc[i].len <= nl || memcmp(jc[i].buf, nm[k], nl) != 0) {
				continue;
			}
			if (k < 4u && jc[i].len == nl + 4u) {
				val[k] = swd4b_get32(jc[i].buf + nl);
				got |= 1u << k;
			} else if (k == 4u && jc[i].len == nl + SWJ_EXT_JOIN_V1_SIZE) {
				memcpy(rj->join, jc[i].buf + nl, SWJ_EXT_JOIN_V1_SIZE);
				got |= 1u << k;
			}
		}
	}
	if (bad || got != 0x1Fu) {
		swl_gap("keep", "re-join not armed: PMK, join payload or RSNE missing", "high");
		printf("WIFI GAP: keep rejoin: not armed (inputs missing, got mask %#x): the link will stay DOWN after an AP loss\n", got);
		swl25_wipe(rj, sizeof *rj);
		return -1;
	}
	memcpy(rj->pmk, pmk, 32);
	rj->v_wpa_auth = val[0]; rj->v_wsec = val[1]; rj->v_auth = val[2]; rj->v_mfp = val[3];
	memcpy(rj->rsne_beacon, rsne_beacon, rbl);
	rj->rsne_beacon_len = rbl;
	if (rsne_assoc != NULL && ral > 0u && ral <= SWJ_RSNE_MAX) {
		memcpy(rj->rsne_assoc, rsne_assoc, ral);
		rj->rsne_assoc_len = ral;
	}
	rj->h = h;
	rj->armed = 1u;
	printf("SWEET WLAN: keep rejoin: join payload and PMK kept in RAM for re-joins (never printed, wiped when the engine dies)\n");
	return 0;
}

/* one attempt failed (or the 4-way timed out): back to DOWN, keys of the half-done 4-way wiped, the next attempt after the backoff for the attempt just made */
static inline int
swl25_rj_fail(struct swg_wx *x, struct swl25_rj *rj, const char *why)
{
	char gb[64];
	const uint32_t wait = swg_rj_backoff_ms(rj->n);

	rj->eapol_on = 0u;
	rj->m4_seen = 0u;
	rj->m4_pending = 0u;
	if (rj->h != NULL) {
		swl25_wipe(&rj->h->sup, sizeof rj->h->sup);
	}
	x->rj_state = SWG_RJ_DOWN;
	x->rj_ssid = x->rj_link = 0u;
	x->rj_lost = 0u;
	rj->t_next = rj->now + wait;
	rj->n_fail++;
	snprintf(gb, sizeof gb, "re-join attempt %u failed: %.30s", rj->n, why);
	swl_gap("keep", gb, "medium");
	printf("WIFI GAP: keep rejoin attempt %u failed (%s), next attempt in %u ms\n", rj->n, why, wait);
	return 0;
}

/* the four security iovars: read back (GET_VAR, whitelist rows wpa_auth/auth/mfp read-only, wsec row 3) and re-sent (SET_VAR, rows with ONE allowed value each) only when the firmware answers something else than the boot join set. [guess, docs/research/W-D6 open unknown 2:
 * whether they survive a firmware disassoc] 0 = all in order (possibly after a re-send), 1 = a call was refused (a GAP; the attempt fails), negative = the bus stopped */
static inline int
swl25_rj_vars(struct swg_wx *x, struct swl25_rj *rj)
{
	static const struct { const char *name, *gw, *sw; } t[4] = {
		{ "wpa_auth", "WLC_GET_VAR wpa_auth", "WLC_SET_VAR wpa_auth" }, { "wsec", "WLC_GET_VAR wsec", "WLC_SET_VAR wsec" },
		{ "auth", "WLC_GET_VAR auth", "WLC_SET_VAR auth" }, { "mfp", "WLC_GET_VAR mfp", "WLC_SET_VAR mfp" },
	};
	const uint32_t want[4] = { rj->v_wpa_auth, rj->v_wsec, rj->v_auth, rj->v_mfp };
	uint32_t i, cur, nl;
	uint8_t rsp[4], buf[16];
	struct swd4b_ioctl_d2h cm;
	int ex, same;

	for (i = 0; i < 4u; i++) {
		nl = (uint32_t)strlen(t[i].name) + 1u;
		memset(rsp, 0, sizeof rsp);
		memset(&cm, 0, sizeof cm);
		ex = swg_e_call(x, SWD4B_WLC_GET_VAR, t[i].gw, t[i].name, NULL, 4u, nl + 4u, 1, 4u, rsp, 1, &cm, NULL);
		if (ex != 0 || x->d4dstop) {
			return ex != 0 ? ex : -1;
		}
		cur = swd4b_get32(rsp);
		if (cm.status != 0 || cm.resp_len < 4u) {
			printf("SWEET WLAN: keep rejoin: %s read back refused (status %d, %u B): re-sent\n", t[i].name, (int)cm.status, cm.resp_len);
			same = 0;
		} else if (i == 1u) {
			same = swj_wsec_merge(cur, SWJ_WSEC_AES) == cur;       /* the cipher bits are what matter (the boot path merges AES in after the keys) */
		} else {
			same = cur == want[i];
		}
		if (same) {
			printf("SWEET WLAN: keep rejoin: %s survived (read back %#x)\n", t[i].name, cur);
			continue;
		}
		printf("SWEET WLAN: keep rejoin: %s read back %#x, the boot join set %#x: re-sent\n", t[i].name, cur, want[i]);
		nl = swj_var_u32(buf, sizeof buf, t[i].name, want[i]);
		if (nl == 0u) {
			return 1;
		}
		memset(&cm, 0, sizeof cm);
		ex = swg_e_call(x, SWD4B_WLC_SET_VAR, t[i].sw, t[i].name, buf + strlen(t[i].name) + 1u, 4u, nl, 1, 0u, NULL, 1, &cm, NULL);
		if (ex != 0 || x->d4dstop) {
			return ex != 0 ? ex : -1;
		}
		if (cm.status != 0) {
			printf("WIFI GAP: keep rejoin: %s re-send refused status=%d\n", t[i].name, (int)cm.status);
			return 1;
		}
	}
	return 0;
}

/* a DOWN link: one attempt. Ring 2 is drained first (events of the loss are decoded and their buffers re-posted: swg_rssi_drain) and must be idle, as for the RSSI refresh; busy = try again in SWL25_RJ_DEFER_MS without counting. 0 = done (JOINING or back to DOWN),
 * negative = the bus stopped */
static inline int
swl25_rj_attempt(struct swg_wx *x, struct swl25_rj *rj)
{
	struct swd4b_ioctl_d2h cm;
	uint32_t v = 0u;
	int ex;

	rj->where = "drain";       /* review 0282 N2: a stop raised by the ring-2 drain (a firmware trap, a bad item) is the data path's stop, never cleared by swl25_rj_step */
	ex = swg_rssi_drain(x);
	if (!x->d4dstop) {
		rj->where = "attempt";       /* a stop raised by the drain keeps "drain" */
	}
	if (ex != 0 || x->d4dstop) {
		return ex < 0 ? ex : 0;
	}
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v) || v == 0xFFFFFFFFu || (v & 0xFFFFu) != x->d4rd % SWG_R2_DEPTH) {
		rj->t_next = rj->now + SWL25_RJ_DEFER_MS;
		if (!rj->defer_gap) {
			rj->defer_gap = 1u;
			printf("SWEET WLAN: keep rejoin: ring 2 not idle, the attempt waits %u ms (not counted)\n", SWL25_RJ_DEFER_MS);
		}
		return 0;
	}
	rj->defer_gap = 0u;
	rj->n++;
	printf("SWEET WLAN: keep rejoin attempt %u\n", rj->n);
	x->rj_ssid = x->rj_link = 0u;
	x->rj_lost = 0u;
	x->krefresh = 1u;
	ex = swl25_rj_vars(x, rj);
	if (ex < 0 || x->d4dstop) {
		x->krefresh = 0u;
		return ex < 0 ? ex : 0;
	}
	if (ex == 1) {
		x->krefresh = 0u;
		return swl25_rj_fail(x, rj, "security iovars");
	}
	x->rj_state = SWG_RJ_JOINING;       /* before the call: the events that arrive inside the ioctl count */
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call_secret(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR join", "join", rj->join, SWJ_EXT_JOIN_V1_SIZE, 5u + SWJ_EXT_JOIN_V1_SIZE, 1, 0u, NULL, 1, &cm);
	x->krefresh = 0u;
	if (ex != 0 || x->d4dstop) {
		return ex < 0 ? ex : 0;
	}
	if (cm.status != 0) {
		printf("WIFI GAP: keep rejoin: join refused status=%d\n", (int)cm.status);
		return swl25_rj_fail(x, rj, "join refused");
	}
	rj->t_phase = rj->now;
	return 0;
}

/* SET_SSID 0 and LINK up were seen: the AP of the join (WLC_GET_BSSID) and the RSNE of the association request the firmware sent are read, the supplicant is set up with the kept PMK and the EAPOL divert is switched on. 0 = done, negative = stopped */
static inline int
swl25_rj_to_keying(struct swg_wx *x, struct swl25_rj *rj)
{
	struct swl25h *h = rj->h;
	struct swd4b_ioctl_d2h cm;
	uint8_t rsp[8];
	const uint8_t *ra;
	uint32_t ral;
	int ex;

	rj->where = "to_keying";
	x->krefresh = 1u;
	memset(rsp, 0, sizeof rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWL25_WLC_GET_BSSID, "WLC_GET_BSSID", "", NULL, 0u, 6u, 1, 6u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		x->krefresh = 0u;
		return ex < 0 ? ex : 0;
	}
	if (cm.status != 0 || cm.resp_len < 6u || swl25_zero(rsp, 6u)) {
		x->krefresh = 0u;
		printf("WIFI GAP: keep rejoin: WLC_GET_BSSID status=%d resp_len %u (or all zero)\n", (int)cm.status, cm.resp_len);
		return swl25_rj_fail(x, rj, "BSSID not read");
	}
	memcpy(rj->bssid, rsp, 6);
	ex = swl25_assoc_rsne_read(x, h);
	x->krefresh = 0u;
	if (ex != 0 || x->d4dstop) {
		return ex < 0 ? ex : 0;
	}
	ra = h->arsne_len != 0u ? h->arsne : rj->rsne_assoc;
	ral = h->arsne_len != 0u ? h->arsne_len : rj->rsne_assoc_len;
	if (swj_ctx_init_ex(&h->sup, rj->pmk, h->l.mac, rj->bssid, ra, ral, rj->rsne_beacon, rj->rsne_beacon_len, h->arsne_len != 0u) != 0) {
		return swl25_rj_fail(x, rj, "supplicant not set up (RSNE)");
	}
	h->hs_done = 0u;
	h->want_keys = 0u;
	rj->m4_seen = 0u;
	rj->eapol_on = 1u;
	x->rj_state = SWG_RJ_KEYING;
	rj->t_phase = rj->now;
	printf("SWEET WLAN: keep rejoin: joined (SET_SSID 0 + LINK up), host 4-way starts (assoc RSNE %s)\n", h->arsne_len != 0u ? "read from the firmware" : "from the boot run");
	return 0;
}

/* wsec_key for the re-join: as swl25_setkey, but a refused status fails the ATTEMPT (*bad), it does not stop the engine: the data path of an up-again link is worth more than the optional re-join. 0 = done, negative = stopped */
static inline int
swl25_rj_setkey(struct swg_wx *x, uint8_t kb[SWJ_WSEC_KEY_SIZE], const char *kind, int *bad)
{
	struct swd4b_ioctl_d2h cm;
	int ex;

	memset(&cm, 0, sizeof cm);
	ex = swg_e_call_secret(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec_key", "wsec_key", kb, SWJ_WSEC_KEY_SIZE, 9u + SWJ_WSEC_KEY_SIZE, 1, 0u, NULL, 1, &cm);
	swl25_wipe(kb, SWJ_WSEC_KEY_SIZE);
	if (ex != 0 || x->d4dstop) {
		return ex != 0 ? ex : -1;
	}
	if (cm.status != 0) {
		printf("WIFI GAP: keep rejoin: wsec_key %s refused status=%d\n", kind, (int)cm.status);
		*bad = 1;
	}
	return 0;
}

/* M4 has left (or its wait ran out): wsec_key pairwise and group, the wsec merge (the whitelisted calls of the boot path, new use after the hand-over), then the live link values, then UP. 0 = done (UP or back to DOWN), negative = stopped */
static inline int
swl25_rj_finish(struct swg_wx *x, struct swl25_rj *rj)
{
	struct swl25h *h = rj->h;
	uint8_t kb[SWJ_WSEC_KEY_SIZE], rsp[4], buf[16];
	struct swd4b_ioctl_d2h cm;
	uint32_t cur, nl;
	int ex, bad = 0;

	rj->where = "finish";
	rj->eapol_on = 0u;       /* a rekey or a late M3 is not answered from here on */
	x->krefresh = 1u;
	if (swj_wsec_key_pairwise(&h->sup, kb) != 0) {
		x->krefresh = 0u;
		swl25_wipe(kb, sizeof kb);
		return swl25_rj_fail(x, rj, "pairwise key not buildable");
	}
	ex = swl25_rj_setkey(x, kb, "pairwise", &bad);
	if (ex == 0 && !x->d4dstop && !bad) {
		if (swj_wsec_key_group(&h->sup, kb) != 0) {
			swl25_wipe(kb, sizeof kb);
			bad = 1;
			printf("WIFI GAP: keep rejoin: group key not buildable\n");
		} else {
			ex = swl25_rj_setkey(x, kb, "group", &bad);
		}
	}
	if (ex != 0 || x->d4dstop) {
		x->krefresh = 0u;
		return ex < 0 ? ex : 0;
	}
	if (bad) {
		x->krefresh = 0u;
		return swl25_rj_fail(x, rj, "key install refused");
	}
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR wsec", "wsec", NULL, 4u, 5u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		x->krefresh = 0u;
		return ex < 0 ? ex : 0;
	}
	if (cm.status != 0 || cm.resp_len < 4u) {
		x->krefresh = 0u;
		return swl25_rj_fail(x, rj, "wsec get refused");
	}
	cur = swd4b_get32(rsp);
	nl = swj_var_u32(buf, sizeof buf, "wsec", swj_wsec_merge(cur, SWJ_WSEC_AES));
	memset(&cm, 0, sizeof cm);
	ex = nl != 0u ? swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec", "wsec", buf + 5, 4u, nl, 1, 0u, NULL, 1, &cm, NULL) : 0;
	if (ex != 0 || x->d4dstop) {
		x->krefresh = 0u;
		return ex < 0 ? ex : 0;
	}
	if (nl == 0u || cm.status != 0) {
		x->krefresh = 0u;
		return swl25_rj_fail(x, rj, "wsec merge refused");
	}
	swl25_wipe(h->sup.tptk, sizeof h->sup.tptk);       /* the keys are in the dongle now */
	swl25_wipe(h->sup.gtk, sizeof h->sup.gtk);
	swl25_wipe(h->sup.pmk, sizeof h->sup.pmk);
	h->sup.install_ptk = h->sup.install_gtk = 0;
	h->hs_done = 1u;
	h->live_ok = 0u;
	ex = swl25_live(x, h);       /* BSSID, chanspec, RSSI (read-only GETs, whitelist rows 8-10) */
	x->krefresh = 0u;
	if (ex != 0 || x->d4dstop) {
		return ex < 0 ? ex : 0;
	}
	if (x->rj_lost) {       /* review 0282 F3: a loss event decoded inside one of the ioctls above: the keys are in a dead association, no UP */
		return swl25_rj_fail(x, rj, "link lost while the keys went in");
	}
	if ((h->live_ok & 2u) != 0u) {
		memcpy(rj->bssid, h->live_bssid, 6);
	}
	rj->chan = h->live_chan;
	rj->rssi = h->live_rssi;
	rj->ok = h->live_ok | 3u;       /* ssid: unchanged (the kept SSID), bssid: rj->bssid always holds the joined AP */
	rj->up_new = 1u;
	rj->n_up++;
	x->klink = 0u;
	x->kl_new = 0u;
	x->rj_state = SWG_RJ_UP;
	x->rj_ssid = x->rj_link = 0u;
	printf("SWEET WLAN: keep rejoin UP after %u attempts, %llu ms\n", rj->n, (unsigned long long)(rj->now - rj->t_down));
	rj->n = 0u;
	rj->down_seen = 0u;
	return 0;
}

/* the keep thread's call, once per pass under the en0 lock, after the ring-2 and en0 polls. `now` in ms; `m4_busy` = the tx buffer of the M4 reply is not yet completed (the thread asks en0). 0 = done, negative = the bus stopped */
static inline int
swl25_rj_step_core(struct swg_wx *x, struct swl25_rj *rj, uint64_t now, int m4_busy, int tx_ok)
{
	struct swl25h *h = rj->h;

	rj->now = now;
	if (x->klink == 0u || x->rj_state == SWG_RJ_UP) {
		return 0;
	}
	if (!rj->armed || h == NULL) {
		if (!rj->noarm_gap) {
			rj->noarm_gap = 1u;
			swl_gap("keep", "link lost, no kept join payload / PMK: no re-join", "high");
			printf("WIFI GAP: keep rejoin: the link is DOWN but the join payload and PMK are not kept: no re-join\n");
		}
		return 0;
	}
	if (!rj->down_seen) {
		rj->down_seen = 1u;
		rj->t_down = now;
		rj->t_next = now;
		rj->n = 0u;
	}
	switch (x->rj_state) {
	case SWG_RJ_DOWN:
		return now >= rj->t_next ? swl25_rj_attempt(x, rj) : 0;
	case SWG_RJ_JOINING:
		if (x->rj_lost) {       /* review 0282 F3 */
			return swl25_rj_fail(x, rj, "link lost while joining");
		}
		if (x->rj_ssid && x->rj_link) {
			return swl25_rj_to_keying(x, rj);
		}
		if (now - rj->t_phase >= SWG_J_BUDGET_MS) {
			return swl25_rj_fail(x, rj, "no SET_SSID 0 + LINK up in the join budget");
		}
		return 0;
	case SWG_RJ_KEYING:
		if (x->rj_lost) {       /* review 0282 F3: never finish on a dead link */
			return swl25_rj_fail(x, rj, "link lost during the 4-way");
		}
		if (rj->m4_pending) {   /* review 0282 F6: the M4 counts as sent only when en0 posted it */
			rj->m4_pending = 0u;
			if (!tx_ok) {
				printf("WIFI GAP: keep rejoin: the M4 reply could not be posted\n");
				return swl25_rj_fail(x, rj, "M4 not posted");
			}
			rj->m4_seen = 1u;
			rj->m4_ms = now;
		}
		if (h->sup.install_ptk && h->sup.install_gtk && rj->m4_seen) {
			if (m4_busy && now - rj->m4_ms < SWL25_RJ_TXWAIT_MS) {
				return 0;
			}
			if (m4_busy) {
				printf("WIFI GAP: keep rejoin: M4 tx status not seen in %u ms, keys installed anyway\n", SWL25_RJ_TXWAIT_MS);
				swl_gap("keep", "re-join: M4 tx status not seen", "medium");
			}
			return swl25_rj_finish(x, rj);
		}
		if (now - rj->t_phase >= SWG_J_BUDGET_MS) {
			return swl25_rj_fail(x, rj, "4-way not complete in the budget");
		}
		return 0;
	default:
		return 0;
	}
}

/* the keep thread's call (review 0282 F1). A soft stop (x->d4dstop with the bus alive) inside any ioctl of the re-join must not end the keep engine: it is cleared as the RSSI refresh does, the re-join is OFF for the rest of the boot
 * (a late completion of the stopped ioctl must not meet a new request), the kept PMK / join payload AND the supplicant's copy are wiped, the payload buffer is scrubbed when its completion was consumed, one GAP names the step. The link stays
 * DOWN for iOS. A hard stop (negative, x->stopped) is the data path's. `tx_ok` = en0 posted the last EAPOL reply. 0 = done, negative = the bus stopped */
static inline int
swl25_rj_step(struct swg_wx *x, struct swl25_rj *rj, uint64_t now, int m4_busy, int tx_ok)
{
	char gb[64];
	const char *where;
	int ex;

	if (x->rj_off) {
		return 0;
	}
	rj->where = "step";
	ex = swl25_rj_step_core(x, rj, now, m4_busy, tx_ok);
	if (x->d4dstop && !x->stopped && !(rj->where != NULL && strcmp(rj->where, "drain") == 0)) {       /* some inner paths return -1 for a soft stop: the engine flag decides, not the return value (ex is the scrub's result below) */
		where = rj->where != NULL ? rj->where : "step";
		x->d4dstop = 0u;
		x->rj_off = 1u;
		x->krefresh = 0u;
		x->rj_state = SWG_RJ_DOWN;
		x->rj_ssid = x->rj_link = 0u;
		swg_e_wipe_b();
		swl25_rj_wipe(rj, "soft stop in re-join");
		snprintf(gb, sizeof gb, "re-join off: soft stop in %.30s", where);
		swl_gap("keep", gb, "high");
		printf("WIFI GAP: keep rejoin disabled: soft stop in %s\n", where);
		if (x->esec_left && !x->esec_done) {       /* review 0282 F5: the stop came before the ioctl completion: nothing may be written to the buffer */
			printf("WIFI GAP: keep rejoin: the stop came before the ioctl completion, the DMA ioctl buffer may hold secret bytes until the next ioctl\n");
			swl_gap("keep", "re-join: ioctl buffer may hold secret bytes", "low");
		}
		ex = swg_e_scrub(x);       /* the payload buffer, when the completion was consumed (no ioctl in transit); a dead bus here is the data path's stop (negative) */
	}
	return ex;
}

/* the en0 receive hook for EAPOL (wlan_en0.h en0_eapol_fn): while KEYING the frame goes to the host supplicant (swj_rx: M1 -> M2, M3 -> M4, the same code as the boot loop) and the reply, if any, is left in out[0..*olen) for en0 to send. 0 = the frame is consumed;
 * -1 = no 4-way in progress, the caller drops it with a GAP */
static inline int
swl25_rj_eapol(void *ctx, const uint8_t *f, uint32_t len, uint8_t *out, uint32_t outcap, uint32_t *olen)
{
	struct swl25_rj *rj = ctx;
	struct swl25h *h = rj->h;
	int r;

	*olen = 0u;
	if (!rj->eapol_on || h == NULL) {
		return -1;
	}
	rj->eapol_rx++;
	if (h->sup.install_ptk != 0 && h->sup.install_gtk != 0 && len >= SWJ_ETH_HLEN + SWJ_KEY_FIXED &&
	    (swj_be16(f + SWJ_ETH_HLEN + SWJ_OFF_INFO) & SWJ_KI_FLAGS) == (SWJ_KI_PAIRWISE | SWJ_KI_ACK)) {
		/* review 0282 F4: an M1 after an accepted M3 (the AP restarted, or a spoof: M1 has no MIC) would re-derive the PTK the AP never confirmed; dropped, loudly (the boot path drops all EAPOL after its keys, SWJ_GAP_X_REKEY) */
		rj->eapol_m1_late++;
		printf("WIFI GAP: keep rejoin: M1 after M3 dropped (the AP restarted its 4-way or the frame is forged)\n");
		swl_gap("keep", "re-join: M1 after M3 dropped", "medium");
		return 0;
	}
	r = swj_rx(&h->sup, f, len, out, outcap, olen);
	if (r == SWJ_RX_REPLY && *olen > 0u) {
		rj->eapol_tx++;
		if (h->sup.state == SWJ_PTK_DONE && h->sup.install_ptk != 0 && h->sup.install_gtk != 0) {       /* the reply to M3 (M4): the keys are due once en0 has posted it (review 0282 F6) */
			rj->m4_pending = 1u;
		}
	} else {
		*olen = 0u;
	}
	return 0;
}

/* the thread takes the UP result once, outside the lock, to tell iOS: 1 = fresh (bssid, channel, rssi and the ok bits for sweet_wlan_a80211_kept_up) */
static inline int
swl25_rj_take_up(struct swl25_rj *rj, uint8_t bssid[6], uint32_t *chan, int32_t *rssi, uint32_t *ok)
{
	if (!rj->up_new) {
		return 0;
	}
	rj->up_new = 0u;
	memcpy(bssid, rj->bssid, 6);
	*chan = rj->chan;
	*rssi = rj->rssi;
	*ok = rj->ok;
	return 1;
}

/* ---- patch 0292 (WIFI-COVERAGE D3/D4: Settings > Wi-Fi lists the networks around): the keep-phase SCAN, driven by the keep thread like the re-join (swl25_rj_step), under the same en0 lock; the engine has no other user.
 * Flow [src docs/research/110-apple80211-io80211-ios167.md s.8.1 row 10 and s.4/s.5; docs/research/W-D7-a80211-get-layouts.md S5, E6]: iOS Set 10 SCAN_REQ -> sweet_wlan_radio.h swr_scan_start -> swl25_sc_request (any thread, no engine access: one flag) ->
 * the keep thread's swl25_sc_step sends ONE firmware command, WLC_SET_VAR "escan" (the passive START of swg_e_escan_arg with chans == 0: the 80 B form, channel_num 0 = every channel the firmware's regulatory list allows [src brcmfmac/bwfm: n_channels 0 = all channels], judged by the L25 whitelist row 19 / swl25w_chk_escan) and collects the ESCAN_RESULT events that follow
 * (ring 2 -> swg_ke_event -> swg_ke_escan -> x->scansink: status 8 = PARTIAL carries one bss record, status 0 = SUCCESS ends the scan [src brcmfmac fweh.h WLC_E_STATUS_PARTIAL / SUCCESS, OpenBSD bwfmreg.h; SWG_E_ST_*]) -> when status 0 is seen, or the budget runs out, or the link is lost,
 * the thread hands the result to iOS outside the en0 lock (sweet_wlan_a80211_scan_done -> SCAN_DONE kev 10, then wifid's Get 11 loop).
 * Rules: ONE scan at a time (the radio's `scanning` flag and state/req here); a scan is started only while the link is UP (x->klink == 0 and x->rj_state == SWG_RJ_UP) and the re-join machine (swl25_rj_step) is not called while a scan runs: a link loss ENDS the scan first (kind LINKLOST,
 * what was collected is delivered), the re-join starts in the same or the next pass; a scan is never started while a re-join runs. Budget SWL25_SC_BUDGET_MS, then SCAN_DONE with what was collected and a GAP. A soft stop inside the escan ioctl turns the scan off for the rest of the boot
 * (GAP), the data path goes on. The scan is a passive scan of ALL channels the firmware allows (channel_num 0; the BOOT scan keeps its seven bring-up channels at lv >= 24) with the firmware's default home time. [guess, not observed: that it keeps the association; the boot scan ran BEFORE the join (L23 < L24), no scan has been seen with the association up; bcmdhd scans while connected and cancels a running scan only before a connect, wl_cfg80211.c wl_do_preassoc_ops]
 * The 12 s budget is a decision of this patch (no source): a passive scan of ~38 channels with home-channel returns while associated may need ~6 s [guess: ~110 ms dwell + ~45 ms home per channel, bcmdhd defaults]; on timeout the scan delivers what it has anyway. Review 0292 F2: every keep scan has its own sync_id (SWG_E_SYNC + 1 + (n - 1) % 255, x->escan_sync; the boot scan keeps 0x1234), so the late events of an old scan are events of another sync id (counted, GAP on the first and every 64th, F1) and can not end a new scan.
 * F4: a link loss that ends a running scan holds the first re-join attempt (swl25_sc_idle is 0 while sc->hold) until the firmware reports the scan's end status or SWL25_SC_HOLD_MS pass (GAP if the bound expired); no abort command is sent (it would be a second new firmware command, another rule-8 review).
 * F5: a firmware end status of our scan other than SUCCESS / PARTIAL (FAIL 1, TIMEOUT 2, ABORT 4, NEWSCAN 9, NEWASSOC 10, SUPPRESS 12, NOCHANS 13 [src refs/wlan-stage5/bcmevent.h.new:412-424]) ends it at once with a GAP naming the status.
 * No SSID, BSSID or address is ever printed here. */
#define SWL25_SC_BUDGET_MS 12000u        /* the scan ends after this long without SUCCESS; 12 s because an all-channel passive scan (~38 channels, home-channel returns while associated) may need ~6 s [guess: ~110 ms dwell + ~45 ms home per channel, bcmdhd defaults] [decision; the boot scan allows 8000: SWG_E_BUDGET_MS] */
#define SWL25_SC_HOLD_MS   SWL25_SC_BUDGET_MS      /* review 0292 F4 + review 0293 LOW 1: after a link loss that ended a running scan the re-join waits at most the scan budget (12 s: the all-channel scan may run ~6 s [guess]) for the firmware's end status of that scan [decision, no source] */
#define SWL25_SC_DEFER_MS  100u          /* a start that finds ring 2 busy is retried this much later */
#define SWL25_SC_EBUSY     16u           /* errno values returned by swl25_sc_request (the radio maps them to its own refusal text) */
#define SWL25_SC_ENXIO     6u
#define SWL25_SC_ENETDOWN  50u
enum { SWL25_SC_IDLE = 0, SWL25_SC_RUN = 1 };
enum { SWL25_SCR_NONE = 0, SWL25_SCR_COMPLETE = 1, SWL25_SCR_TIMEOUT = 2, SWL25_SCR_LINKLOST = 3, SWL25_SCR_FAILED = 4, SWL25_SCR_REFUSED = 5 };    /* the numbers sweet_wlan_radio.h SWR_SCAN_* use */

struct swl25_sc {
	volatile uint8_t req;            /* set by swl25_sc_request (iOS thread), cleared by the keep thread when it starts or refuses the scan */
	volatile uint8_t state;          /* SWL25_SC_IDLE / SWL25_SC_RUN; written by the keep thread only */
	volatile uint8_t off;            /* a soft stop in the scan ioctl, or the engine ended: no further scan this boot */
	uint8_t res_new, res;            /* a finished scan waits for swl25_sc_take (kind SWL25_SCR_*) */
	uint8_t defer_gap;
	volatile uint8_t hold;           /* review 0292 F4: a link loss ended a running scan; the re-join waits (swl25_sc_idle is 0) until the firmware's end status or hold_until */
	uint64_t hold_until;             /* ms */
	uint64_t t0, t_next, t_defer;    /* ms: scan start, next start try, first deferral */
	uint32_t ms, ev, part;           /* of the finished scan: duration, ESCAN_RESULT events of our sync id, of those PARTIAL */
	uint32_t foreign;                /* of the finished scan: events of another sync id (review 0292 F1) */
	uint32_t n_req, n_start, n_done, n_timeout, n_lost, n_failed, n_refused;
};

/* the radio asks for a scan (any thread, no engine access, no lock): 0 = accepted, SWL25_SC_ENXIO = the scan is off for this boot (the radio falls back to the boot-time listing, loudly), SWL25_SC_ENETDOWN = the link is not UP (the re-join owns the engine), SWL25_SC_EBUSY = one is pending or running */
static inline int
swl25_sc_request(struct swl25_sc *sc, const struct swg_wx *x)
{
	if (__atomic_load_n(&sc->off, __ATOMIC_ACQUIRE)) {
		return (int)SWL25_SC_ENXIO;
	}
	if (x->klink != 0u || x->rj_state != SWG_RJ_UP) {
		return (int)SWL25_SC_ENETDOWN;
	}
	if (__atomic_load_n(&sc->state, __ATOMIC_ACQUIRE) != SWL25_SC_IDLE || __atomic_load_n(&sc->req, __ATOMIC_ACQUIRE) != 0u || sc->res_new) {
		return (int)SWL25_SC_EBUSY;
	}
	sc->n_req++;
	__atomic_store_n(&sc->req, 1u, __ATOMIC_SEQ_CST);
	if (__atomic_load_n(&sc->off, __ATOMIC_SEQ_CST)) {       /* review 0292 F3: store req, then load off (swl25_sc_abort stores off, then loads req): one of the two sees the other, so a request is never accepted after the end unseen. req stays set: if abort saw it, it answers it (the radio then has no scan to finish and prints its own GAP) */
		return (int)SWL25_SC_ENXIO;
	}
	return 0;
}

static inline int
swl25_sc_idle(const struct swl25_sc *sc)
{
	return __atomic_load_n(&sc->state, __ATOMIC_ACQUIRE) == SWL25_SC_IDLE && !__atomic_load_n(&sc->hold, __ATOMIC_ACQUIRE);       /* review 0292 F4: not idle while the re-join is held back after a link loss in a scan */
}

/* a scan ended (or was refused after the request): the result waits for swl25_sc_take. Counts and one GAP for everything but COMPLETE; no address, no name */
static inline void
swl25_sc_end(struct swg_wx *x, struct swl25_sc *sc, uint64_t now, uint8_t kind, const char *why)
{
	char gb[64];

	x->sc_on = 0u;
	sc->ms = sc->state == SWL25_SC_RUN && now >= sc->t0 ? (uint32_t)(now - sc->t0) : 0u;
	sc->ev = x->sc_ev;
	sc->part = x->sc_part;
	sc->foreign = x->sc_foreign;
	switch (kind) {
	case SWL25_SCR_COMPLETE: sc->n_done++; break;
	case SWL25_SCR_TIMEOUT: sc->n_timeout++; break;
	case SWL25_SCR_LINKLOST: sc->n_lost++; break;
	case SWL25_SCR_REFUSED: sc->n_refused++; break;
	default: sc->n_failed++; break;
	}
	printf("SWEET WLAN: keep scan end: %s, %u events of ours (%u partial), %u of another sync id, %u ms\n",
	    kind == SWL25_SCR_COMPLETE ? "complete" : kind == SWL25_SCR_TIMEOUT ? "timeout" : kind == SWL25_SCR_LINKLOST ? "link lost" : kind == SWL25_SCR_REFUSED ? "refused" : "failed", sc->ev, sc->part, sc->foreign, sc->ms);
	if (kind != SWL25_SCR_COMPLETE) {
		printf("WIFI GAP: keep scan not complete (%s): %s; %u events of ours (%u partial), %u ms; what was collected is delivered\n",
		    kind == SWL25_SCR_TIMEOUT ? "timeout" : kind == SWL25_SCR_LINKLOST ? "link lost" : kind == SWL25_SCR_REFUSED ? "refused" : "failed", why, sc->ev, sc->part, sc->ms);
		snprintf(gb, sizeof gb, "keep scan %s", kind == SWL25_SCR_TIMEOUT ? "timed out" : kind == SWL25_SCR_LINKLOST ? "ended: link lost" : kind == SWL25_SCR_REFUSED ? "refused" : "failed");
		swl_gap("scan", gb, "medium");
	}
	if (kind == SWL25_SCR_LINKLOST) {       /* review 0292 F4: the escan may still run in the firmware: hold the re-join until its end status or the bound */
		x->sc_await = 1u;
		x->sc_await_done = (x->sc_termd || x->sc_complete) ? 1u : 0u;    /* review 0292 N1: an end status already seen in the same drain as the DEAUTH counts (no false 2 s wait) */
		sc->hold_until = now + SWL25_SC_HOLD_MS;
		__atomic_store_n(&sc->hold, 1u, __ATOMIC_RELEASE);
	}
	sc->res = kind;
	sc->res_new = 1u;
	sc->req = 0u;
	__atomic_store_n(&sc->state, (uint8_t)SWL25_SC_IDLE, __ATOMIC_RELEASE);
}

/* one call per keep pass, under the en0 lock, BEFORE swl25_rj_step. 0 = done, negative = the bus stopped (a soft stop inside the escan ioctl is cleared here, the scan goes off). `now` = ms */
static inline int
swl25_sc_step(struct swg_wx *x, struct swl25_sc *sc, uint64_t now)
{
	uint8_t arg[96];
	struct swd4b_ioctl_d2h cm;
	uint32_t v = 0u;
	int ex;
	char why[128];

	if (sc->hold) {         /* review 0292 F4 */
		if (x->sc_await_done) {
			printf("SWEET WLAN: keep scan: the firmware ended the scan cut short by the link loss (status %u) after %u ms, the re-join may start\n", x->sc_term, now >= sc->hold_until - SWL25_SC_HOLD_MS ? (uint32_t)(now - (sc->hold_until - SWL25_SC_HOLD_MS)) : 0u);
			x->sc_await = 0u;
			__atomic_store_n(&sc->hold, 0u, __ATOMIC_RELEASE);
		} else if (now >= sc->hold_until) {
			printf("WIFI GAP: keep scan: no end status of the scan cut short by the link loss within %u ms; the re-join starts with the escan possibly still running in the firmware\n", SWL25_SC_HOLD_MS);
			swl_gap("scan", "re-join started without the end status of the cut-short scan", "low");
			x->sc_await = 0u;
			__atomic_store_n(&sc->hold, 0u, __ATOMIC_RELEASE);
		}
	}
	if (sc->state == SWL25_SC_RUN) {
		if (x->klink != 0u || x->rj_state != SWG_RJ_UP) {
			swl25_sc_end(x, sc, now, SWL25_SCR_LINKLOST, "the link went down during the scan; the escan is not aborted (no abort command is sent), the re-join waits for its end status or a short bound");
		} else if (x->sc_termd) {       /* review 0292 F5: the firmware ended our scan with a status other than SUCCESS */
			snprintf(why, sizeof why, "the firmware ended the scan with status %u (abort 4, new scan 9, new assoc 10, ...), not SUCCESS", x->sc_term);
			swl25_sc_end(x, sc, now, SWL25_SCR_FAILED, why);
		} else if (x->sc_complete) {
			swl25_sc_end(x, sc, now, SWL25_SCR_COMPLETE, "");
		} else if (now - sc->t0 >= SWL25_SC_BUDGET_MS) {
			swl25_sc_end(x, sc, now, SWL25_SCR_TIMEOUT, "no ESCAN_RESULT SUCCESS in the budget");
		}
		return 0;
	}
	if (sc->req == 0u) {
		return 0;
	}
	if (sc->off) {
		swl25_sc_end(x, sc, now, SWL25_SCR_REFUSED, "the scan is off for this boot");
		return 0;
	}
	if (x->klink != 0u || x->rj_state != SWG_RJ_UP) {
		swl25_sc_end(x, sc, now, SWL25_SCR_REFUSED, "requested while the link is not UP (the re-join owns the engine)");
		return 0;
	}
	if (x->emajor != 2u && x->emajor != 3u) {
		swl25_sc_end(x, sc, now, SWL25_SCR_REFUSED, "the scan version (scan_ver) was not read at boot");
		return 0;
	}
	if (now < sc->t_next) {
		return 0;
	}
	ex = swg_rssi_drain(x);       /* events of the keep phase are decoded and consumed first, as before the RSSI refresh and a re-join attempt */
	if (ex < 0 || x->stopped) {
		return -1;
	}
	if (x->d4dstop) {
		return 0;       /* a stop raised by the drain is the data path's: the keep loop ends on it, the request stays unanswered (swl25_sc_abort answers it) */
	}
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v) || v == 0xFFFFFFFFu || (v & 0xFFFFu) != x->d4rd % SWG_R2_DEPTH) {
		if (sc->t_defer == 0u) {
			sc->t_defer = now;
		}
		if (now - sc->t_defer >= SWL25_SC_BUDGET_MS) {
			swl25_sc_end(x, sc, now, SWL25_SCR_FAILED, "ring 2 never idle in the budget");
			sc->t_defer = 0u;
			return 0;
		}
		sc->t_next = now + SWL25_SC_DEFER_MS;
		if (!sc->defer_gap) {
			sc->defer_gap = 1u;
			printf("SWEET WLAN: keep scan: ring 2 not idle, the start waits %u ms (not counted)\n", SWL25_SC_DEFER_MS);
		}
		return 0;
	}
	sc->t_defer = 0u;
	sc->defer_gap = 0u;
	sc->n_start++;
	sc->t0 = now;
	x->sc_complete = 0u;
	x->sc_ev = x->sc_part = x->sc_other = x->sc_foreign = 0u;
	x->sc_termd = 0u;
	x->sc_term = 0u;
	x->sc_await = 0u;
	x->escan_sync = (uint16_t)(SWG_E_SYNC + 1u + (sc->n_start - 1u) % 255u);       /* review 0292 F2: one id per scan, 0x1235..0x1333; the judge accepts exactly this one */
	x->sc_on = 1u;        /* before the call: the events that arrive inside the ioctl count */
	__atomic_store_n(&sc->state, (uint8_t)SWL25_SC_RUN, __ATOMIC_RELEASE);
	sc->req = 0u;
	swg_e_escan_arg(arg, x->emajor, 0, x->escan_sync);       /* chans == 0: the 80 B form, channel_num 0 = the firmware's own channel list */
	printf("SWEET WLAN: keep scan start %u: passive escan, all channels, version %u, sync %#x, budget %u ms\n", sc->n_start, (unsigned)x->emajor, (unsigned)x->escan_sync, SWL25_SC_BUDGET_MS);
	memset(&cm, 0, sizeof cm);
	x->krefresh = 1u;
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR escan", "escan", arg, 80u, 6u + 80u, 1, 0u, NULL, 1, &cm, NULL);
	x->krefresh = 0u;
	if (ex < 0 || x->stopped) {
		return ex < 0 ? ex : -1;
	}
	if (x->d4dstop) {
		x->d4dstop = 0u;       /* a SOFT stop inside the optional scan ioctl: the data path is kept, the scan is off for this boot (as the RSSI refresh and the re-join do) */
		sc->off = 1u;
		swl25_sc_end(x, sc, now, SWL25_SCR_FAILED, "soft stop inside the escan ioctl; the scan is off for this boot");
		return 0;
	}
	if (cm.status != 0) {
		printf("WIFI GAP: keep scan: WLC_SET_VAR escan refused, status=%d\n", (int)cm.status);
		swl25_sc_end(x, sc, now, SWL25_SCR_FAILED, "escan start refused by the firmware");
	}
	return 0;
}

/* the engine is going away (the keep thread ends) or a hard stop came: answer a pending or running scan so iOS is not left waiting, and refuse new ones. Runs on the keep thread without the en0 lock (the request side takes none either); the thread then delivers with swl25_sc_take. Review 0292 F3: store off, then load req, both seq_cst (the pair of swl25_sc_request) */
static inline void
swl25_sc_abort(struct swg_wx *x, struct swl25_sc *sc, uint64_t now, const char *why)
{
	__atomic_store_n(&sc->off, 1u, __ATOMIC_SEQ_CST);
	if (sc->state == SWL25_SC_RUN || __atomic_load_n(&sc->req, __ATOMIC_SEQ_CST) != 0u) {
		swl25_sc_end(x, sc, now, SWL25_SCR_FAILED, why);
	}
}

/* the thread takes a finished scan once, outside the lock, to tell iOS: 1 = fresh (kind SWL25_SCR_*, duration, events of ours, partial ones) */
static inline int
swl25_sc_take(struct swl25_sc *sc, uint32_t *kind, uint32_t *ms, uint32_t *ev, uint32_t *part)
{
	if (!sc->res_new) {
		return 0;
	}
	sc->res_new = 0u;
	*kind = sc->res;
	*ms = sc->ms;
	*ev = sc->ev;
	*part = sc->part;
	return 1;
}

/* ---- ALL-ON T-32 (docs/research/138-wlan-keepalive.md): the firmware NULL-frame keep-alive. The house AP aged the idle phone out after ~22 min [obs 2026-11-11]; PixOS never configured a keep-alive, bcmdhd does (mkeep_alive, 30 s, id 0, null frame).
 * ONE firmware command, WLC_SET_VAR "mkeep_alive" (swj_mkeep_alive_cmd, 23 B, judged by L25 whitelist row 20 / chk 6), sent by the keep thread under the en0 lock: the first point where the engine can send a plain SET_VAR is the keep phase, after the join,
 * the host 4-way and the key install are done (the same thread owns the engine alone there, as for the RSSI refresh, the re-join and the scan); it is armed at the start of the keep loop and again after every `keep rejoin UP` (swl25_ka_arm).
 * A firmware STATUS error (a completion with a non-zero status) changes nothing: one GAP line and the link goes on; one attempt per arm. A firmware TIMEOUT (no completion) is different [read from source, review 0313 F3: sweet_wlan_g2.h SWG_E_STOP is a soft stop, cleared here; the L25 judge's
 * ioct_busy (sweet_wlan_l25_wl.h) is cleared only when a completion of that transaction is consumed, so the next keep ioctl, the RSSI refresh within 5 s, is refused by the judge; whether that refusal is a hard stop is read only from a comment, sweet_wlan_g2d.h:412): the same class as the RSSI refresh and the scan, not new. The setting is not read back: the reply layout of a mkeep_alive GET is not cited in
 * research 138 (only the request id byte), so no GET row is added. Boot-arg sweetwlanka: exactly the string "0" turns it off (a GAP line says so), exactly "1" or absent = on, any other string is ignored with a GAP naming it (on). */
struct swl25_ka {
	uint8_t due, off;                /* an attempt is due; the feature is off for this boot (sweetwlanka=0, or a soft stop in its ioctl) */
	uint8_t defer_gap;
	uint32_t n_set, n_fail, n_arm;   /* accepted by the firmware, refused by the firmware, armed */
	uint64_t t_next;                 /* ms: do not try before */
	uint64_t t_defer;                /* ms: when ring 2 was first found busy for this send (0 = not busy); the wait ends in a GAP after SWL25_SC_BUDGET_MS */
};

/* sweetwlanka: `str` = the boot-arg's string (PE_parse_boot_arg_str), NULL = absent. Absent or exactly "1" = on; exactly "0" = off with a GAP; any other string (empty, "on", "2", ...) = on with a GAP naming the raw string. Returns 1 when on */
static inline int
swl25_ka_init(struct swl25_ka *ka, const char *str)
{
	memset(ka, 0, sizeof *ka);
	if (str != NULL && strcmp(str, "0") == 0) {
		ka->off = 1u;
		printf("SWEET WLAN: GAP keep-alive off (sweetwlanka=0)\n");
		swl_gap("keepalive", "keep-alive off (sweetwlanka=0)", "medium");
		return 0;
	}
	if (str != NULL && strcmp(str, "1") != 0) {
		printf("SWEET WLAN: GAP keep-alive sweetwlanka=\"%.8s\" ignored (only \"0\" or \"1\" are valid), keep-alive stays on\n", str);
		swl_gap("keepalive", "sweetwlanka value ignored, keep-alive on", "low");
	}
	ka->due = 1u;        /* armed for the start of the keep phase */
	ka->n_arm = 1u;
	return 1;
}

/* the link came (back) UP: send once more (the setting may not survive a new association [guess]; setting it twice is harmless in bcmdhd, research 138 s.0 step 2) */
static inline void
swl25_ka_arm(struct swl25_ka *ka)
{
	if (!ka->off) {
		ka->due = 1u;
		ka->n_arm++;
		ka->t_next = 0u;
		ka->t_defer = 0u;
		ka->defer_gap = 0u;
	}
}

/* one call per keep pass, under the en0 lock. `busy` = a scan runs or holds the engine. Sends only while the link is UP and the re-join machine idle (UP), after a ring-2 drain and with ring 2 idle (as swl25_live_rssi). 0 = done / nothing to do, negative = the bus stopped
 * (a soft stop inside the ioctl is cleared here and turns the keep-alive off, like the scan) */
static inline int
swl25_ka_step(struct swg_wx *x, struct swl25_ka *ka, uint64_t now, int busy)
{
	struct swj_cmd c;
	struct swd4b_ioctl_d2h cm;
	uint32_t v = 0u, nl;
	int ex;

	if (!ka->due || ka->off || busy || x->klink != 0u || x->rj_state != SWG_RJ_UP || now < ka->t_next) {
		return 0;
	}
	ex = swg_rssi_drain(x);
	if (ex < 0 || x->stopped) {
		return -1;
	}
	if (x->d4dstop) {
		return 0;        /* a stop raised by the drain is the data path's */
	}
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v) || v == 0xFFFFFFFFu || (v & 0xFFFFu) != x->d4rd % SWG_R2_DEPTH) {
		if (ka->t_defer == 0u) {
			ka->t_defer = now;
		}
		if (now - ka->t_defer >= SWL25_SC_BUDGET_MS) {        /* the scan's cap (swl25_sc_step): a ring that never goes idle ends the wait loudly */
			ka->due = 0u;
			ka->n_fail++;
			ka->t_defer = 0u;
			ka->defer_gap = 0u;
			printf("SWEET WLAN: GAP keep-alive not sent: ring 2 never idle in %u ms\n", SWL25_SC_BUDGET_MS);
			swl_gap("keepalive", "mkeep_alive not sent, ring 2 never idle in the budget", "high");
			return 0;
		}
		ka->t_next = now + SWL25_SC_DEFER_MS;
		if (!ka->defer_gap) {
			ka->defer_gap = 1u;
			printf("SWEET WLAN: keep-alive: ring 2 not idle, the send waits %u ms (not counted)\n", SWL25_SC_DEFER_MS);
		}
		return 0;
	}
	ka->defer_gap = 0u;
	ka->t_defer = 0u;
	ka->due = 0u;        /* one attempt per arm, whatever follows */
	if (swj_mkeep_alive_cmd(&c, SWJ_MKEEP_PERIOD_MS) != 1u) {
		ka->n_fail++;
		printf("SWEET WLAN: GAP keep-alive mkeep_alive command not built\n");
		swl_gap("keepalive", "mkeep_alive command not built", "high");
		return 0;
	}
	nl = (uint32_t)strlen("mkeep_alive");
	memset(&cm, 0, sizeof cm);
	x->krefresh = 1u;
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR mkeep_alive", "mkeep_alive", c.buf + nl + 1u, c.len - nl - 1u, c.len, 1, 0u, NULL, 1, &cm, NULL);
	x->krefresh = 0u;
	if (x->stopped || ex < 0) {
		return ex < 0 ? ex : -1;
	}
	if (x->d4dstop) {
		x->d4dstop = 0u;        /* a SOFT stop inside this optional ioctl: the data path is kept, the keep-alive is off for this boot */
		ka->off = 1u;
		ka->n_fail++;
		printf("SWEET WLAN: GAP keep-alive off: soft stop inside the mkeep_alive ioctl (no completion: the ioctl stays in transit for the L25 judge, the next keep ioctl, e.g. the RSSI refresh, is refused)\n");
		swl_gap("keepalive", "soft stop inside the mkeep_alive ioctl (no completion), keep-alive off, next keep ioctl refused by the judge", "high");
		return 0;
	}
	if (cm.status != 0) {
		ka->n_fail++;
		printf("SWEET WLAN: GAP keep-alive mkeep_alive rejected status %d\n", (int)cm.status);
		swl_gap("keepalive", "mkeep_alive rejected by the firmware, the link goes on without it", "medium");
		return 0;
	}
	ka->n_set++;
	printf("SWEET WLAN: keep-alive set period %u ms id %u (null frame)\n", (unsigned)SWJ_MKEEP_PERIOD_MS, (unsigned)SWJ_MKEEP_ID);
	return 0;
}

/* review 0363 finding 4 (rule 7): sweetprobe=1 armed the Wi-Fi probes but swl25_run ended without reaching them: say so, once per arming */
static int
swl25_probe_unreached(int ex)
{
	if (swl25_probe_arm != 0u) {
		printf("SWEET PROBE: GAP wifi probes armed but not reached in this L25 run (mask %u, ex %d)\n", (unsigned)swl25_probe_arm, ex);
		swl25_probe_arm = 0u;
	}
	return ex;
}

static struct swl25h swl25_H;        /* file scope on purpose: ~36 KB of BSS, not stack; the host test inspects the wiped key fields */

static int
swl25_run_inner(struct swg_wx *x, unsigned lv)
{
	int ex;

	memset(&swl25_H, 0, sizeof swl25_H);
	ex = swl25_assoc_rsne(x, &swl25_H);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (x->bssget != NULL && x->l25c != NULL) {       /* patch 0288: a join target the scan never saw gets its BSSID and RSNE from WLC_GET_BSS_INFO (one read-only GET) before swl25_go reads the cfg */
		ex = x->bssget(x);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
	}
	ex = swl25_go(x, lv, &swl25_H);
	swl25_wipe(&swl25_H.sup, sizeof swl25_H.sup);       /* pmk, tptk, gtk: on every path */
	return ex;
}

static int
swl25_run(struct swg_wx *x, unsigned lv)
{
	return swl25_probe_unreached(swl25_run_inner(x, lv));       /* review 0363 finding 4: on every exit path of the run */
}

#endif /* SWEET_WLAN_L25_HS_H */
