/*
 * Host test of the L25 data-path steps swl25_env / swl25_flow / swl25_rxposts (sweet_wlan_l25.h, T-38cc; SPEC-WIFI Part E TRANSPORT block s.2). A scripted dongle sits on the
 * doorbell of the bus: a ring-0 flow-create item is answered with a CREATE_CMPLT item on ring 2 (D2H write index moved), the rx posts on ring 1 are recorded. The expected bytes
 * of every item are written out here literally (not taken from the header builders). Clauses (each has a mutant in tools/test_wlan_l25_mutants.py):
 *   E1 env accepted and the shadows installed   E2 no DATA block / too small / unaligned / overlapping the G1 block: WIFI GAP line, nothing stored, soft stop
 *   F1 flow create: ring-0 item bytes, H2D-WR slot, doorbell, ring-2 CREATE_CMPLT consumed (RD slot + doorbell), `flow ring up` line   F2 dongle refuses (status != 0): GAP + stop
 *   F3 no answer: GAP + stop   F4 a ring-2 item with a wrong epoch is refused   R1 16 rx posts: item bytes, slots, doorbells in order   R2 a failing store stops the posts
 * The ASan/UBSan lane runs the same file. Open (ticket Log): rx poll + swj_rx 4-way (H6/H7), key installs (H8-H10), L26. No hardware.
 */
#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static char logbuf[1 << 22];
static unsigned loglen, ngap;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf)
		loglen += (unsigned)n;
	return n;
}
static void lg(const char *s) { cap_printf("%s\n", s); }
#define SWDP_LOG(s) lg(s)
#define SWDP_GAP(s) do { ngap++; lg(s); } while (0)
#define SWJ_LOG(s) lg(s)
#define SWJ_GAP(s) do { ngap++; lg(s); } while (0)
#define SWJ_RANDOM(b, n) (memcpy((b), V_SNONCE, (n)) == NULL)
#include "sweet_wlan_data.h"
#include "wlan_4way_vectors.h"
static void swj_wipe_seen(void *p, uint32_t n);      /* wlan_l25_host_rj.h: review 0282 F5 */
#define SWJ_WIPE_HOOK(p, n) swj_wipe_seen((p), (n))
#include "sweet_wlan_join.h"
#define printf cap_printf
/* the ioctl transport (swg_e_call, swg_e_call_secret) is replaced by a recording stub below: its own bytes are covered by wlan_g2_host_test.c (L24 join, wsec_key secret-quiet); here only what
 * the L25 code SENDS through it is judged (name, payload bytes, order, secret flag, status handling). The real functions keep their g2.h text under another name. */
#define swg_e_call real_swg_e_call
#define swg_e_call_secret real_swg_e_call_secret
#include "sweet_wlan_g2.h"
#undef swg_e_call
#undef swg_e_call_secret
#include "sweet_wlan_l25.h"

static int rj_stub(uint32_t cmd, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, uint32_t nrsp, uint8_t *rsp, struct swd4b_ioctl_d2h *cmo, int secret);     /* wlan_l25_host_rj.h */
#define L25_STUB_HOOK rj_stub
static int pr_stub(uint32_t cmd, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, uint32_t nrsp, uint8_t *rsp, struct swd4b_ioctl_d2h *cmo, int secret);     /* wlan_l25_host_pr.h */
#define L25_PROBE_STUB pr_stub
struct kcall { uint32_t cmd; char name[16]; uint8_t arg[SWJ_WSEC_KEY_SIZE]; uint32_t argn, wlen; int secret; };
static struct kcall K[8];
static unsigned nK;
static struct { int32_t status[8]; uint32_t wsec_cur; uint8_t mac[6]; uint32_t maclen; int32_t macst; unsigned nmac; } KS;     /* + the cur_etheraddr answer (T-baed): not recorded in K, counted in nmac */     /* the stub's answers: completion status per call number, the GET wsec value */

/* T-924f: the counters GET. Field offsets typed here from the generator output of the header structs (wl_cnt_wlc_t / wl_cnt_ver_11_t compiled with NFIFO 6, see sweet_wlan_l25_hs.h), not from the code under test */
static const uint16_t T_OFF_WLC[10] = { 0, 28, 32, 200, 204, 224, 60, 272, 4, 64 };      /* txframe txnobuf txnoassoc txfail txretry txnoack rxframe fourwayfail txbyte rxbyte (T-8d5f: offsetof of the header structs compiled with NFIFO 6, NREINITREASONCOUNT 8) */
static const uint16_t T_OFF_V11[10] = { 4, 32, 36, 432, 436, 456, 64, 504, 8, 68 };
static struct { unsigned n; int mode; int32_t status; uint32_t tab[3][10]; uint32_t resp_len; } KC;     /* call n answers tab[min(n, 2)]: 0 = base, 1 = first-m2, 2 = end; mode 0 xtlv (slice tuple first), 1 xtlv (wlc tuple first), 2 version 7, 3 legacy v11, 4 resp_len 3 */
/* T-9d71: the firmware's association-request RSNE. assoc_info answers {req_len, resp_len, flags}, assoc_req_ies the IEs; seq numbers every L25 GET so the order assoc_info -> assoc_req_ies -> cur_etheraddr is judged. Default (ai_reset): a request whose RSNE is the beacon RSNE */
static struct { unsigned n_info, n_ies, shape_bad, seq, s_info, s_ies, s_mac; int32_t st_info, st_ies; uint32_t req_len, flags, info_resp, ies_resp, ies_n; uint8_t ies[512]; } AI;
static struct { unsigned n, shape_bad; uint8_t bssid[6]; uint32_t chanspec; int32_t rssi; int32_t st_bssid, st_cs, st_rssi; } LV;       /* W-UI: answers of the live-link GETs (swl25_live) */
#define SWL25_WLC_GET_BSSID 23u
#define SWL25_WLC_GET_RSSI  127u
static void put32le(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static void put16le(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static uint32_t counters_build(uint8_t *r, const uint32_t v[10], int mode)
{
	unsigned i;
	uint32_t o;

	memset(r, 0, 1200);
	if (mode == 3) {
		put16le(r, 11); put16le(r + 2, 1004);
		for (i = 0; i < 10u; i++) put32le(r + T_OFF_V11[i], v[i]);
		return 1004u;
	}
	put16le(r, mode == 2 ? 7u : 30u);
	o = 4u;
	if (mode == 0) { put16le(r + o, 1); put16le(r + o + 2, 5); o += 4u + 8u; }          /* a slice-index tuple of 5 B, padded to 8 */
	put16le(r + o, 0x100); put16le(r + o + 2, 864);
	for (i = 0; i < 10u; i++) put32le(r + o + 4u + T_OFF_WLC[i], v[i]);
	o += 4u + 864u;
	put16le(r + o, 0x101); put16le(r + o + 2, 256); o += 4u + 256u;                       /* the mcst tuple behind it */
	put16le(r + 2, o - 4u);
	return o;
}
static int
stub_call(struct swg_wx *xx, uint32_t cmd, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, uint32_t nrsp, uint8_t *rsp, struct swd4b_ioctl_d2h *cmo, int secret)
{
#ifdef L25_CALL_HOOK
	L25_CALL_HOOK(cmd, name, wlen);
#endif
	struct kcall *k = &K[nK < 8u ? nK : 7u];

	(void)xx;
#ifdef L25_STUB_HOOK
	if (L25_STUB_HOOK(cmd, name, arg, argn, wlen, nrsp, rsp, cmo, secret)) {      /* patch 0282: wlan_l25_host_rj.h answers and records the calls of the re-join (wpa_auth, wsec, auth, mfp, join, wsec_key) */
		return 0;
	}
#endif
	if (strcmp(name, "counters") == 0) {
		memset(cmo, 0, sizeof *cmo);
		if (cmd != SWD4B_WLC_GET_VAR || arg != NULL || argn != 0u || wlen != 1200u || nrsp != 1200u) {
			cmo->status = -1;       /* a wrong request shape */
			return 0;
		}
		cmo->resp_len = counters_build(rsp, KC.tab[KC.n < 2u ? KC.n : 2u], KC.mode);
		if (KC.mode == 4) cmo->resp_len = 3u;
		KC.n++;
		cmo->status = KC.status;
		return 0;
	}
	if (strcmp(name, "assoc_info") == 0 || strcmp(name, "assoc_req_ies") == 0) {      /* T-9d71: read-only GETs of fixed length, answered from AI and kept out of K */
		const int info = name[10] == '\0';

		memset(cmo, 0, sizeof *cmo);
		AI.seq++;
		if (info) { AI.n_info++; AI.s_info = AI.seq; } else { AI.n_ies++; AI.s_ies = AI.seq; }
		if (cmd != SWD4B_WLC_GET_VAR || arg != NULL || argn != 0u || wlen != (info ? 64u : 512u) || nrsp != wlen || secret) {
			AI.shape_bad++;
			cmo->status = -1;
			return 0;
		}
		if (info) {
			memset(rsp, 0, 64);
			put32le(rsp, AI.req_len); put32le(rsp + 4, 40u); put32le(rsp + 8, AI.flags);
			cmo->status = AI.st_info;
			cmo->resp_len = AI.info_resp;
		} else {
			memcpy(rsp, AI.ies, AI.ies_n < 512u ? AI.ies_n : 512u);
			cmo->status = AI.st_ies;
			cmo->resp_len = AI.ies_resp;
		}
		return 0;
	}
	if (strcmp(name, "chanspec") == 0 || (name[0] == '\0' && (cmd == SWL25_WLC_GET_BSSID || cmd == SWL25_WLC_GET_RSSI))) {      /* W-UI: the live-link GETs after L26, answered from LV, kept out of K */
		memset(cmo, 0, sizeof *cmo);
		LV.n++;
		if (name[0] != '\0' ? (cmd != SWD4B_WLC_GET_VAR || wlen != 13u || nrsp != 4u) : cmd == SWL25_WLC_GET_BSSID ? (wlen != 6u || nrsp != 6u) : (wlen != 12u || nrsp != 12u)) {
			LV.shape_bad++;
			cmo->status = -1;
			return 0;
		}
		if (arg != NULL || argn != 0u || secret) LV.shape_bad++;
		if (name[0] != '\0') { put32le(rsp, LV.chanspec); cmo->resp_len = 4u; cmo->status = LV.st_cs; }
		else if (cmd == SWL25_WLC_GET_BSSID) { memcpy(rsp, LV.bssid, 6); cmo->resp_len = 6u; cmo->status = LV.st_bssid; }
		else { memset(rsp, 0, 12); put32le(rsp, (uint32_t)LV.rssi); cmo->resp_len = 12u; cmo->status = LV.st_rssi; }
		return 0;
	}
#ifdef L25_PROBE_STUB
	if (L25_PROBE_STUB(cmd, name, arg, argn, wlen, nrsp, rsp, cmo, secret)) {      /* patch 0363: wlan_l25_host_pr.h answers and records the three LOWCLK B3 GETs (WLC_GET_PM, country, chanspecs) */
		return 0;
	}
#endif
	if (strcmp(name, "cur_etheraddr") == 0) {       /* T-baed step 1: a read-only GET_VAR of 6 bytes, answered from KS and kept out of K so the key-install indexes stay */
		KS.nmac++;
		AI.seq++; AI.s_mac = AI.seq;
		memset(cmo, 0, sizeof *cmo);
		if (cmd != SWD4B_WLC_GET_VAR || arg != NULL || argn != 6u || wlen != 20u || nrsp != 6u) {
			cmo->status = -1;       /* a wrong request shape fails the run */
			return 0;
		}
		memcpy(rsp, KS.mac, 6);
		cmo->status = KS.macst;
		cmo->resp_len = KS.maclen;
		return 0;
	}
	memset(k, 0, sizeof *k);
	k->cmd = cmd; snprintf(k->name, sizeof k->name, "%s", name); k->argn = argn; k->wlen = wlen; k->secret = secret;
	if (arg != NULL && argn <= sizeof k->arg)
		memcpy(k->arg, arg, argn);
	if (nrsp >= 4u && rsp != NULL) {
		rsp[0] = (uint8_t)KS.wsec_cur; rsp[1] = (uint8_t)(KS.wsec_cur >> 8); rsp[2] = (uint8_t)(KS.wsec_cur >> 16); rsp[3] = (uint8_t)(KS.wsec_cur >> 24);
	}
	memset(cmo, 0, sizeof *cmo);
	cmo->status = KS.status[nK < 8u ? nK : 7u];
	nK++;
	return 0;
}
static int l25_last_soft, l25_last_repost, l25_last_krf;
static int __attribute__((unused))
swg_e_call(struct swg_wx *xx, uint32_t cmd, const char *what, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, int soft, uint32_t nrsp, uint8_t *rsp, int repost, struct swd4b_ioctl_d2h *cmo, struct swg_ew *sc)
{
	(void)what; (void)sc;
	l25_last_soft = soft; l25_last_repost = repost; l25_last_krf = xx->krefresh;       /* patch 0292: how the keep-phase scan START calls the transport (soft completion status, response buffer re-posted, refresh flag set) */
	return stub_call(xx, cmd, name, arg, argn, wlen, nrsp, rsp, cmo, 0);
}
static int __attribute__((unused))
swg_e_call_secret(struct swg_wx *xx, uint32_t cmd, const char *what, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, int soft, uint32_t nrsp, uint8_t *rsp, int repost, struct swd4b_ioctl_d2h *cmo)
{
	(void)what; (void)soft; (void)repost;
	return stub_call(xx, cmd, name, arg, argn, wlen, nrsp, rsp, cmo, 1);
}
#include "sweet_wlan_l25_hs.h"
#undef printf

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

#define G1PA   0x880000000ull
#define G1LEN  0x100000u
#define DAPA   0x880100000ull
#ifndef DALEN
#define DALEN  0x20000u         /* patch 0357: -DDALEN=.. for a block with a larger rx pool (wlan_en0_live_host_test.c EN0_LIVE_RXN) */
#endif
#ifndef L25_RXFW
#define L25_RXFW 16u            /* patch 0357: max_host_rxbufs the scripted dongle's shared RAM holds */
#endif
#define H2DW   0x880001000ull
#define H2DR   0x880001400ull
#define D2HW   0x880001800ull
#define D2HR   0x880002000ull
#define RING0  0x880010000ull
#define RING1  0x880020000ull
#define RING2  0x880030000ull
#define RING3  0x880040000ull
#define RING4  0x880050000ull

static uint32_t g1[G1LEN / 4u], dat[DALEN / 4u];
static struct swg_wx x;
struct acc { char k; int sp; uint32_t off, val; };
static struct acc A[8192];
static unsigned nA;
static struct { unsigned nw, fail_wr; int answer; int status; unsigned badep, db1, ndb; uint32_t db[64]; } J;
/* the scripted dongle of the data path (D.on = 1): M1 at poll step D.m1_tick, M3 after it saw M2, tx status for every post, ARP and echo answers */
static struct {
	int on; unsigned tick, m1_tick, n3, n4, rxn, ntx, ndb3, ndb4, drop_echo, no_status, no_arp, no_m3, wrong_m3_off, badrx, bad_ep4, flood, inj_tick, inj_kind, txerr;
	uint8_t txf[24][1514]; uint32_t txl[24], txrid[24];
	uint32_t db_rd3, db_rd4;
} D;
/* the AP side of M2 (T-9d71): the RSNE it stored from the association request (default the vector's) and the check it makes: the M2 must be the vector M2 with THAT key data, MIC recomputed from the vector PTK. Anything else gets no M3 (the real AP logs `RSN_IE Different in msg 2`) */
static uint8_t AP_RSNE[64];
static uint32_t AP_N;
static int ap_m2_ok(const uint8_t *f, uint32_t len)
{
	uint8_t e[1514];
	const uint32_t el = SWJ_KEY_FIXED + AP_N, body = el - 4u;

	if (len != SWJ_ETH_HLEN + el || memcmp(f, V_M2, SWJ_ETH_HLEN) != 0)
		return 0;
	memcpy(e, V_M2 + SWJ_ETH_HLEN, SWJ_KEY_FIXED);
	e[2] = (uint8_t)(body >> 8); e[3] = (uint8_t)body;
	e[SWJ_OFF_DLEN] = (uint8_t)(AP_N >> 8); e[SWJ_OFF_DLEN + 1u] = (uint8_t)AP_N;
	memcpy(e + SWJ_KEY_FIXED, AP_RSNE, AP_N);
	swj_eapol_mic(V_PTK, e, el, e + SWJ_OFF_MIC);
	return memcmp(f + SWJ_ETH_HLEN, e, el) == 0;
}
static const uint8_t GWM[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };     /* the gateway / AP MAC of the flow-ring tests (the 4-way scenario uses V_AA) */

static uint32_t *mem(int sp, uint32_t off)
{
	if (sp == SWG_SP_HOST && off < G1LEN && (off & 3u) == 0u)
		return &g1[off / 4u];
	if (sp == SWG_SP_DATA && off < DALEN && (off & 3u) == 0u)
		return &dat[off / 4u];
	return NULL;
}
static int dead4;
static uint32_t corrupt4;
static int b_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	uint32_t *m = mem(sp, a);

	(void)ctx;
	if (m == NULL)
		return -1;
	*v = *m;
	if (sp == SWG_SP_HOST && a == (uint32_t)(D2HW - G1PA) + 8u && (dead4 || corrupt4 != 0u))
		*v = dead4 ? 0xFFFFFFFFu : corrupt4;
	if (nA < 8192u)
		A[nA++] = (struct acc){ 'R', sp, a, *v };
	return 0;
}
/* the scripted dongle: a ring-0 doorbell with a flow-create item in the slot below the new write index is answered on ring 2 */
static void dongle_db0(uint32_t wr)
{
	const uint32_t slot = (wr + 127u) % 128u, *it = &g1[(RING0 - G1PA) / 4u + slot * 10u];
	uint32_t k = x.d4rd, *r = &g1[(RING2 - G1PA) / 4u + 6u * (k % 64u)];

	if (J.answer != 1 || (it[0] & 0xFFu) != SWDP_MT_FLOW_CREATE)
		return;
	r[0] = (((254u + k + J.badep) % 253u) << 24) | SWDP_MT_FLOW_CREATE_CMPLT;
	r[1] = it[1];
	r[2] = (uint32_t)(uint16_t)J.status | ((it[5] >> 16) & 0xFFFFu) << 16;    /* ring/flow id = the flow id of the request (item +22) */
	r[3] = r[4] = 0u;
	r[5] = r[0] ^ r[1] ^ r[2] ^ r[3] ^ r[4];
	g1[(D2HW - G1PA) / 4u] = (k + 1u) % 64u;
}
/* ---- the scripted dongle of the data path. All bytes are written out here, not taken from the header builders. */
static uint16_t isum(const uint8_t *p, unsigned n)
{
	uint32_t sum = 0u;
	unsigned i;

	for (i = 0; i + 1u < n; i += 2u)
		sum += (uint32_t)p[i] << 8 | p[i + 1u];
	if (n & 1u)
		sum += (uint32_t)p[n - 1u] << 8;
	while (sum >> 16)
		sum = (sum & 0xFFFFu) + (sum >> 16);
	return (uint16_t)~sum;
}
/* one D2H item of ring ri (3 or 4) at absolute count *n; the last word is the XOR fix, the write index slot (ri - 2) follows */
static void d2h_item(uint64_t ringpa, uint32_t ri, uint32_t isz, uint32_t depth, unsigned *n, uint32_t type, uint32_t rid, uint32_t w3, uint32_t w4)
{
	const uint32_t k = *n;
	uint32_t *r = &g1[(ringpa - G1PA) / 4u + (k % depth) * (isz / 4u)], xr = 0u, i;

	memset(r, 0, isz);
	r[0] = ((254u + k) % 253u) << 24 | 0x80u << 16 | type;
	r[1] = rid;
	r[2] = (uint32_t)(ri + 2u) << 16;
	r[3] = w3;
	r[4] = w4;
	for (i = 0; i + 1u < isz / 4u; i++)
		xr ^= r[i];
	r[isz / 4u - 1u] = xr;
	*n = k + 1u;
	g1[(D2HW - G1PA) / 4u + ri] = *n % depth;
}
static void tx_status(uint32_t rid, uint32_t status)
{
	d2h_item(RING3, 1u, 24u, swd_rings[3].items, &D.n3, 0x10u, rid, 0u | status << 16, 0u);
}
/* a frame into the next rx buffer at byte offset `off`; off != 0 is also the completion's data_offset (0 = the shared rx offset, which is 0 here) */
static void deliver_rx(const uint8_t *fr, uint32_t len, uint32_t off)
{
	const uint32_t i = D.rxn, wr1 = g1[(H2DW - G1PA) / 4u + 1u] & 0xFFFFu;     /* sprint 2026-11-08: the dongle takes the i-th POSTED ring-1 item (rid + pointer from the item), as the firmware does */
	const uint32_t *it = &g1[(RING1 - G1PA) / 4u + (i % swd_rings[1].items) * 8u];
	const uint32_t b = it[1] - 0x52580000u;

	if (i >= wr1 || b >= 16u || it[6] != (uint32_t)(DAPA + 0xC000u + b * 2048u) || it[7] != (uint32_t)((DAPA + 0xC000u + b * 2048u) >> 32)) {
		D.badrx++;
		return;
	}
	D.rxn++;
	memcpy((uint8_t *)dat + 0xC000u + b * 2048u + off, fr, len);
	d2h_item(RING4, 2u, 40u, swd_rings[4].items, &D.n4, 0x12u, it[1], len << 16, off);
	if (D.bad_ep4 && i == 0u)
		g1[(RING4 - G1PA) / 4u] += 1u << 24;                       /* the epoch of ring-4 item 0 is wrong */
}
static void deliver_echo_reply(const uint8_t *rq, uint32_t len)
{
	uint8_t f[200];
	const uint8_t *ip = rq + 14;
	const uint32_t tot = (uint32_t)ip[2] << 8 | ip[3];

	if (len > sizeof f)
		return;
	memcpy(f, rq, len);
	memcpy(f, rq + 6, 6);
	memcpy(f + 6, rq, 6);                                       /* swap the MACs */
	memcpy(f + 14 + 12, ip + 16, 4);
	memcpy(f + 14 + 16, ip + 12, 4);                            /* swap the IPs */
	f[14 + 8] = 64u; f[14 + 10] = f[14 + 11] = 0u;
	{ uint16_t c = isum(f + 14, 20u); f[24] = (uint8_t)(c >> 8); f[25] = (uint8_t)c; }
	f[34] = 0u; f[36] = f[37] = 0u;                             /* type 0 (reply), checksum cleared */
	{ uint16_t c = isum(f + 34, tot - 20u); f[36] = (uint8_t)(c >> 8); f[37] = (uint8_t)c; }
	deliver_rx(f, 14u + tot, 0u);
}
static void deliver_arp_reply(const uint8_t *rq)
{
	uint8_t f[42];

	memcpy(f, rq + 6, 6);                                       /* to the asker */
	memcpy(f + 6, V_AA, 6);
	f[12] = 0x08; f[13] = 0x06; f[14] = 0; f[15] = 1; f[16] = 0x08; f[17] = 0; f[18] = 6; f[19] = 4; f[20] = 0; f[21] = 2;
	memcpy(f + 22, V_AA, 6);                                    /* sha = the gateway */
	memcpy(f + 28, rq + 38, 4);                                 /* spa = the asked IP */
	memcpy(f + 32, rq + 22, 6);                                 /* tha = the asker */
	memcpy(f + 38, rq + 28, 4);
	deliver_rx(f, 42u, 0u);
}
/* a tx post on the flow ring (ring id 5) rang the doorbell: slot wr-1 */
static void dongle_flow(uint32_t wr)
{
	const uint32_t slot = (wr + 63u) % 64u;
	const uint8_t *it = (const uint8_t *)dat + slot * 48u;
	uint32_t rid, dl, pa_lo, n;
	uint8_t *f;

	memcpy(&rid, it + 4, 4);
	memcpy(&pa_lo, it + 32, 4);
	dl = (uint32_t)it[42] | (uint32_t)it[43] << 8;
	if (D.ntx >= 24u || pa_lo < (uint32_t)DAPA || pa_lo - (uint32_t)DAPA + dl > DALEN)
		return;
	n = D.ntx++;
	f = D.txf[n];
	memcpy(f, it + 8, 14);
	memcpy(f + 14, (const uint8_t *)dat + (pa_lo - (uint32_t)DAPA), dl);
	D.txl[n] = 14u + dl;
	D.txrid[n] = rid;
	if (!D.no_status)
		tx_status(rid, D.txerr && n == 0u ? 3u : 0u);
	if (f[12] == 0x88 && f[13] == 0x8e && ap_m2_ok(f, D.txl[n]) && !D.no_m3)
		deliver_rx(V_M3, sizeof V_M3, D.wrong_m3_off ? 0u : 2u);
	else if (f[12] == 0x08 && f[13] == 0x06 && f[21] == 1u && !D.no_arp)
		deliver_arp_reply(f);
	else if (f[12] == 0x08 && f[13] == 0x00 && f[23] == 1u && f[34] == 8u) {
		const uint32_t seq = (uint32_t)f[40] << 8 | f[41];

		if (seq >= 1u && seq <= 16u && ((D.drop_echo >> (seq - 1u)) & 1u) == 0u)
			deliver_echo_reply(f, D.txl[n]);
	}
}
static int b_wr(void *ctx, int sp, uint32_t a, uint32_t v)
{
	uint32_t *m = mem(sp, a);

	(void)ctx;
	J.nw++;
#ifdef L25_WR_HOOK
	L25_WR_HOOK(sp, a, v);          /* T-5173: wlan_l25_wl_host_test.c judges every store before it is made (sweet_wlan_l25_wl.h) */
#endif
	if (J.fail_wr == J.nw) {
		if (nA < 8192u)
			A[nA++] = (struct acc){ 'F', sp, a, v };
		return -1;
	}
	if (nA < 8192u)
		A[nA++] = (struct acc){ 'W', sp, a, v };
	if (sp == SWG_SP_PCIE) {
		const uint32_t ring = v >> 16 & 0xFFu;

		if (a != SWG_DB0_OFF)
			return -1;
		if (J.ndb < 64u)
			J.db[J.ndb++] = v;
		if (ring == 0u && (v >> 24) == 0xFFu)
			dongle_db0(v & 0xFFFFu);
		J.db1 += ring == 1u && (v >> 24) == 0xFFu;
		if (D.on && ring == 5u && (v >> 24) == 0xFFu)
			dongle_flow(v & 0xFFFFu);
		D.ndb3 += ring == 3u && (v >> 24) == 0xDDu;
		D.ndb4 += ring == 4u && (v >> 24) == 0xDDu;
		return 0;
	}
	if (m == NULL)
		return -1;
	*m = v;
	return 0;
}
static void b_delay(void *ctx, unsigned ms)
{
	(void)ctx;
	(void)ms;
	D.tick++;
	if (D.on && D.tick == D.m1_tick)
		deliver_rx(V_M1, sizeof V_M1, 0u);
	if (D.on && D.inj_tick != 0u && D.tick == D.inj_tick) {
		if (D.inj_kind == 1u)
			tx_status(0x5458000Fu, 0u);                         /* a tx status for a request id nobody posted */
		else
			deliver_rx(V_M1, sizeof V_M1, 0u);                  /* a second M1 after the keys are in */
	}
	if (D.on && D.flood > 0u) {
		static const uint8_t junk[20] = { 0x02, 0x00, 0x00, 0xbb, 0x00, 0x02, 0x02, 0x00, 0x00, 0xaa, 0x00, 0x01, 0x12, 0x34 };

		D.flood--;
		deliver_rx(junk, sizeof junk, 0u);
	}
}
static const struct swg_bus BUS = { b_rd, b_wr, b_delay, NULL, NULL, NULL, NULL, NULL, NULL };

static struct swl25 L;
static const uint8_t MAC[6] = { 0x02, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE };

static void world_reset(void)
{
	memset(g1, 0, sizeof g1);
	memset(dat, 0, sizeof dat);
	memset(&J, 0, sizeof J);
	memset(&D, 0, sizeof D);
	memset(&K, 0, sizeof K);
	memset(&KS, 0, sizeof KS);
	nK = 0u;
	nA = 0u;
	memset(&x, 0, sizeof x);
	memset(&L, 0, sizeof L);
	memcpy(L.mac, MAC, 6);
	x.b = &BUS;
	x.in.blk = G1PA;
	x.in.blk_len = G1LEN;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_W)] = H2DW;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_R)] = H2DR;
	x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = D2HW;
	x.in.idx[SWG_IDX(SWD_IDX_D2H_R)] = D2HR;
	x.in.ring[0] = RING0;
	x.in.ring[1] = RING1;
	x.in.ring[2] = RING2;
	x.in.ring[3] = RING3;
	x.in.ring[4] = RING4;
	x.in.data = DAPA;
	x.in.data_len = DALEN;
#ifdef L25_RXARG
	x.in.rxarg_set = 1u; x.in.rxarg = L25_RXARG;          /* patch 0357: boot-arg sweetwlanrxpost */
#endif
	x.rxfw_state = SWDP_RXFW_OK; x.rxfw = L25_RXFW;       /* patch 0357: what swg_e read at shared +0x22 (a test may set it; default 16 = the old pool, no GAP) */
	x.entered = 1;
	x.d4b = 8;
	x.d4dn = 65u;
	x.d4wr = 5u;
	x.d4rd = 70u;
	g1[(H2DW - G1PA) / 4u] = 5u;
	g1[(D2HR - G1PA) / 4u] = 6u;
	g1[(D2HW - G1PA) / 4u] = 6u;           /* d4rd 70 = slot 6: nothing pending */
	loglen = 0u;
	logbuf[0] = '\0';
	ngap = 0u;
}
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static uint32_t gw(uint64_t pa) { return g1[(pa - G1PA) / 4u]; }
static unsigned nwrites(void) { unsigned i, n = 0; for (i = 0; i < nA; i++) n += A[i].k == 'W'; return n; }

static void test_env(void)
{
	world_reset();
	CHECK(swl25_env(&x, &L) == 0 && !x.d4dstop && nwrites() == 0u);                                      /* E1 */
	CHECK(L.env.flow.len == 64u * 48u && L.env.flow_isz == 48u);
	CHECK(L.env.flow.pa == DAPA && L.env.txbuf.pa == DAPA + 0x4000u && L.env.rxbuf.pa == DAPA + 0xC000u && L.env.flowid == 2u);
	CHECK(x.dpsh[0].pa == RING1 && x.dpsh[0].w == L.sh_r1 && x.dpsh[1].pa == DAPA && x.dpsh[2].pa == DAPA + 0x4000u && x.dpsh[2].nw == 16u * 512u);
	world_reset(); x.in.data = 0u; x.in.data_len = 0u;                                                    /* E2 */
	CHECK(swl25_env(&x, &L) < 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: no DATA block was allocated by the glue\n"));
	world_reset(); x.in.data_len = 0x13000u;
	CHECK(swl25_env(&x, &L) < 0 && x.d4dstop && has("WIFI GAP: L25 not run: DATA block too small"));
	world_reset(); x.in.data += 0x100u;
	CHECK(swl25_env(&x, &L) < 0 && has("WIFI GAP: L25 not run: DATA block too small or not page aligned"));
	world_reset(); x.in.data = G1PA + 0x80000u;
	CHECK(swl25_env(&x, &L) < 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: DATA block overlaps the G1 block\n"));
	world_reset(); x.in.data = G1PA - 0x8000u; x.in.data_len = 0x20000u;                                  /* straddles the start of the G1 block */
	CHECK(swl25_env(&x, &L) < 0 && has("overlaps the G1 block"));
	world_reset(); x.in.ring[1] = G1PA + G1LEN - 0x20u;                                                   /* ring 1 runs out of the G1 block: the audit refuses the envelope */
	CHECK(swl25_env(&x, &L) < 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: env refused by the audit code="));
}

static void flow_prep(void)
{
	world_reset();
	CHECK(swl25_env(&x, &L) == 0);
}
static void test_flow(void)
{
	const uint32_t r0 = (uint32_t)(RING0 - G1PA) / 4u + 5u * 10u;
	int rc;

	flow_prep();                                                                                          /* F1 */
	J.answer = 1;
	rc = swl25_flow(&x, &L, GWM);
	CHECK(rc == 0 && !x.d4dstop && L.flow.st == SWDP_FLOW_OPEN && L.env.flow_open == 1u);
	CHECK((g1[r0] & 0xFFu) == 0x03u && (g1[r0] >> 24) == 6u);                                              /* type 3 at byte 0; epoch (254 + 5) % 253 = 6 */
	CHECK(g1[r0 + 5] >> 16 == 2u && (g1[r0 + 7] & 0xFFFFu) == 64u && (g1[r0 + 7] >> 16) == 48u);          /* flow id 2 (+22), max_items 64 (+28), item size 48 (+30) */
	CHECK(g1[r0 + 8] == (uint32_t)DAPA && g1[r0 + 9] == (uint32_t)(DAPA >> 32));                         /* ring PA */
	CHECK(memcmp(&g1[r0 + 2], GWM, 4) == 0 && memcmp((const uint8_t *)&g1[r0 + 3], GWM + 4, 2) == 0);     /* DA */
	CHECK(gw(H2DW) == 6u && gw(H2DW + 8u) == 0u);                                                         /* ring-0 WR 5 -> 6; the flow ring's own WR slot 2 stored 0 first */
	CHECK(J.ndb == 2u && J.db[0] == 0xFF000006u && J.db[1] == 0xDD020007u);        /* the flow-create doorbell, then the consume of the CREATE_CMPLT */
	CHECK(x.d4wr == 6u && x.d4rd == 71u && gw(D2HR) == 7u);                                               /* the CREATE_CMPLT was consumed: RD slot 7 */
	CHECK(has("SWEET WLAN: flow ring up flow=2 ring=5 items=64\n") && has("SWEET WLAN: DP15 plan flow create done: 4 stores\n") && has("SWEET WLAN: DP15 plan consume ring 2 done: 2 stores\n"));
	CHECK(nA > 0u && A[nA - 1u].sp == SWG_SP_PCIE && A[nA - 1u].val == 0xDD020007u);                      /* the last access: the D2H read doorbell */
	flow_prep();                                                                                          /* F2 */
	J.answer = 1; J.status = 4;
	rc = swl25_flow(&x, &L, GWM);
	CHECK(rc == 0 && x.d4dstop && L.flow.st == SWDP_FLOW_FAILED && L.env.flow_open == 0u && has("WIFI GAP: L25 flow create refused by the dongle (type 4 status 4)\n"));
	flow_prep();                                                                                          /* F3 */
	rc = swl25_flow(&x, &L, GWM);
	CHECK(rc == 0 && x.d4dstop && L.flow.st == SWDP_FLOW_CREATING && has("WIFI GAP: L25 flow create: no CREATE_CMPLT in "));
	flow_prep();                                                                                          /* F4 */
	J.answer = 1; J.badep = 1u;
	rc = swl25_flow(&x, &L, GWM);
	CHECK(rc < 0 && x.d4dstop && L.env.flow_open == 0u && has("WIFI GAP: L25 ring-2 item 70 refused: check code 1") && x.d4rd == 70u);
	flow_prep();                                                                                          /* a failing store of the plan: no doorbell, no further plan */
	J.answer = 1; J.fail_wr = 2u;
	rc = swl25_flow(&x, &L, GWM);
	CHECK(rc < 0 && x.d4dstop && J.ndb == 0u && has("WIFI GAP: DP15 store 2/4 of plan flow create failed"));
}

static void test_rx(void)
{
	unsigned i;
	int rc;

	flow_prep();                                                                                          /* R1 */
	rc = swl25_rxposts(&x, &L);
	CHECK(rc == 0 && !x.d4dstop && gw(H2DW + 4u) == 16u && J.ndb == 16u && J.db1 == 16u);
	for (i = 0; i < 16u; i++) {
		const uint32_t *it = &g1[(RING1 - G1PA) / 4u + i * 8u];
		const uint64_t buf = DAPA + 0xC000u + (uint64_t)i * 2048u;

		CHECK((it[0] & 0xFFu) == 0x11u && (it[0] >> 24) == (uint32_t)((254u + i) % 253u) && it[1] == 0x52580000u + i);
		CHECK((it[2] >> 16) == 2048u && it[4] == 0u && it[5] == 0u && it[6] == (uint32_t)buf && it[7] == (uint32_t)(buf >> 32));
		CHECK(J.db[i] == 0xFF010000u + i + 1u);
	}
	CHECK(has("SWEET WLAN: L25 rx posts done: 16 buffers on ring 1\n") && L.rxb.inflight == 16u && !has("WIFI GAP"));
	flow_prep();                                                                                          /* R2: the 3rd post's first store fails: posts 0 and 1 are made, nothing after */
	J.fail_wr = 2u * 10u + 1u;          /* a post = 8 item words + WR slot + doorbell = 10 bus writes */
	rc = swl25_rxposts(&x, &L);
	CHECK(rc < 0 && x.d4dstop && J.db1 == 2u && gw(H2DW + 4u) == 2u && has("WIFI GAP: DP15 store 1/3 of plan rxpost 2 failed"));
	flow_prep();                                                                                          /* R3: a 17th post is refused by the book (16 slots) */
	CHECK(swl25_rxposts(&x, &L) == 0 && swl25_rxposts(&x, &L) < 0 && x.d4dstop && has("WIFI GAP: L25 not run: rx post: no free request id or ring 1 full"));
}

/* ---- the data path end to end: flow create, 16 rx posts, M1..M4 from the python vectors, key installs, ARP + 4 echoes */
static const uint8_t CIP[4] = { 192, 0, 2, 10 }, CGW[4] = { 192, 0, 2, 1 };
static struct swl25_cfg CFG;
static void cfg_init(void)
{
	memset(&CFG, 0, sizeof CFG);
	memcpy(CFG.pmk, V_PMK, 32); memcpy(CFG.mac, V_SPA, 6); memcpy(CFG.bssid, V_AA, 6); memcpy(CFG.ip, CIP, 4); memcpy(CFG.gw, CGW, 4);
	memcpy(CFG.rsne_assoc, V_RSNE, sizeof V_RSNE); CFG.rsne_assoc_len = sizeof V_RSNE;
	memcpy(CFG.rsne_beacon, V_RSNE, sizeof V_RSNE); CFG.rsne_beacon_len = sizeof V_RSNE;
}
/* the association-request IEs: SSID, rates, the RSNE (ra, ral; ral 0 = none), extended capabilities; the answers are valid until a test changes one field */
static void ai_reset(const uint8_t *ra, uint32_t ral)
{
	static const uint8_t pre[] = { 0, 3, 'n', 'e', 't', 1, 4, 0x82, 0x84, 0x8b, 0x96 }, post[] = { 127, 1, 0 };
	uint32_t n = 0u;

	memset(&AI, 0, sizeof AI);
	memcpy(AI.ies, pre, sizeof pre); n = sizeof pre;
	if (ral != 0u) { memcpy(AI.ies + n, ra, ral); n += ral; }
	memcpy(AI.ies + n, post, sizeof post); n += sizeof post;
	AI.ies_n = n; AI.ies_resp = n;
	AI.req_len = 4u + n; AI.info_resp = 40u;
}
static void scen(void)
{
	world_reset();
	cfg_init();
	x.l25c = &CFG;
	x.elv = 25u;
	D.on = 1;
	D.m1_tick = 3u;
	J.answer = 1;
	KS.wsec_cur = 0xBu;
	memcpy(KS.mac, V_SPA, 6); KS.maclen = 20u; KS.macst = 0; KS.nmac = 0u;
	memset(&KC, 0, sizeof KC);      /* T-924f: counters all zero, answered as xtlv */
	ai_reset(V_RSNE, (uint32_t)sizeof V_RSNE);
	memset(&LV, 0, sizeof LV); memcpy(LV.bssid, V_AA, 6); LV.chanspec = 0xE02Au | 0x0100u; LV.rssi = -41;     /* W-UI: 5 GHz 80 MHz center 42, sideband LLU = primary 40 */
	memcpy(AP_RSNE, V_RSNE, sizeof V_RSNE); AP_N = sizeof V_RSNE;
}
static int log_has_bytes(const uint8_t *needle, uint32_t n)
{
	char hx[200], hu[200];
	uint32_t i;

	for (i = 0; i < n; i++) {
		snprintf(hx + 2u * i, 3, "%02x", needle[i]);
		snprintf(hu + 2u * i, 3, "%02X", needle[i]);
	}
	return memmem(logbuf, loglen, needle, n) != NULL || strstr(logbuf, hx) != NULL || strstr(logbuf, hu) != NULL;
}
static int all_zero(const uint8_t *p, uint32_t n) { uint32_t i; for (i = 0; i < n; i++) if (p[i]) return 0; return 1; }

static void test_path(void)
{
	static const uint8_t arp[42] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02, 0x00, 0x00, 0xbb, 0x00, 0x02, 0x08, 0x06, 0, 1, 8, 0, 6, 4, 0, 1,
	    0x02, 0x00, 0x00, 0xbb, 0x00, 0x02, 192, 0, 2, 10, 0, 0, 0, 0, 0, 0, 192, 0, 2, 1 };
	unsigned i, j;
	int rc;

	scen();                                                                                               /* P1: the whole path */
	D.drop_echo = 0x4u;                                                                                   /* the 3rd echo request is not answered */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && D.ntx == 3u + SWL25_PINGS && D.badrx == 0u && x.d4dlv == 26u);
	CHECK(LV.n == 3u && LV.shape_bad == 0u && swl25_H.live_ok == 0xFu && swl25_H.live_ssid_len == 3u && memcmp(swl25_H.live_ssid, "net", 3) == 0);    /* W-UI: the live link from the firmware */
	CHECK(memcmp(swl25_H.live_bssid, V_AA, 6) == 0 && swl25_H.live_chanspec == 0xE12Au && swl25_H.live_chan == 40u && swl25_H.live_rssi == -41);
	CHECK(D.txl[0] == sizeof V_M2 && memcmp(D.txf[0], V_M2, sizeof V_M2) == 0);                           /* H7: M2 byte for byte (python reference) */
	CHECK(D.txl[1] == sizeof V_M4 && memcmp(D.txf[1], V_M4, sizeof V_M4) == 0);                           /* M4 */
	CHECK(D.txl[2] == 42u && memcmp(D.txf[2], arp, 42u) == 0);                                            /* the ARP request */
	for (i = 0; i < SWL25_PINGS; i++) {                                                                   /* the echo requests, field by field */
		const uint8_t *f = D.txf[3u + i], *ip = f + 14, *ic = f + 34;

		CHECK(D.txl[3u + i] == 14u + 20u + 8u + 32u && memcmp(f, V_AA, 6) == 0 && memcmp(f + 6, V_SPA, 6) == 0 && f[12] == 8u && f[13] == 0u);
		CHECK(ip[0] == 0x45u && ip[2] == 0u && ip[3] == 60u && ip[6] == 0x40u && ip[7] == 0u && ip[8] == 64u && ip[9] == 1u && isum(ip, 20u) == 0u);
		CHECK(memcmp(ip + 12, CIP, 4) == 0 && memcmp(ip + 16, CGW, 4) == 0);
		CHECK(ic[0] == 8u && ic[1] == 0u && isum(ic, 40u) == 0u && ic[4] == 0x50u && ic[5] == 0x58u && ic[6] == 0u && ic[7] == i + 1u);
		for (j = 0; j < 32u; j++)
			CHECK(ic[8u + j] == j);
	}
	CHECK(nK == 4u && K[0].secret == 1 && K[1].secret == 1 && K[2].secret == 0 && K[3].secret == 0);      /* H8, H9, H10 get/set, in this order */
	CHECK(K[0].cmd == SWD4B_WLC_SET_VAR && strcmp(K[0].name, "wsec_key") == 0 && K[0].argn == 164u && K[0].wlen == 173u);
	CHECK(memcmp(K[0].arg, "\0\0\0\0\x10\0\0\0", 8) == 0 && memcmp(K[0].arg + 8, V_PTK + 32, 16) == 0 && all_zero(K[0].arg + 24, 88u));   /* index 0, len 16, the TK (PTK[32..48)) */
	CHECK(K[0].arg[112] == 4u && all_zero(K[0].arg + 113, 3u) && all_zero(K[0].arg + 116, 4u) && memcmp(K[0].arg + 156, V_AA, 6) == 0);     /* AES_CCM, flags 0, ea = AP */
	CHECK(K[1].arg[0] == V_GTK_ID && K[1].arg[4] == 16u && memcmp(K[1].arg + 8, V_GTK, 16) == 0 && K[1].arg[112] == 4u && K[1].arg[116] == 2u && all_zero(K[1].arg + 156, 8u));
	CHECK(K[2].cmd == SWD4B_WLC_GET_VAR && strcmp(K[2].name, "wsec") == 0 && K[2].wlen == 9u);
	CHECK(K[3].cmd == SWD4B_WLC_SET_VAR && strcmp(K[3].name, "wsec") == 0 && K[3].argn == 4u && K[3].arg[0] == 0xCu && K[3].arg[1] == 0u && K[3].wlen == 9u);       /* 0xB -> WEP/TKIP/AES cleared, AES set */
	CHECK(has("SWEET WLAN: eapol rx msg=1\n") && has("SWEET WLAN: eapol tx msg=2\n") && has("SWEET WLAN: eapol rx msg=3\n") && has("SWEET WLAN: eapol tx msg=4\n"));
	CHECK(has("SWEET WLAN: 4way complete PTK=CCMP GTK=CCMP\n") && has("SWEET WLAN: wsec_key pairwise set ok\n") && has("SWEET WLAN: wsec_key group set ok idx=1\n"));
	CHECK(has("SWEET WLAN: wsec merged ok\n") && has("SWEET WLAN: L25 done: keys installed, rx frames 2, tx posts 2, ring-0 WR "));
	CHECK(has("SWEET WLAN: arp gateway resolved\n") && has("SWEET WLAN: ping summary sent=10 got=9 lost=1 rtt_us min/avg/max=") && has(" bad=0 ignored=0\n"));
	CHECK(has("WIFI GAP: H11 WLC_SCB_AUTHORIZE (121) not issued") && has("WIFI GAP: eapol ctx") == 0);                    /* H11 stays a printed gap */
	CHECK(!has("WIFI GAP: M2 RSNE is the caller's copy") && has("SWEET WLAN: L25 rsne assoc=22 m2=22 equal=1 hash_assoc="));      /* T-9d71: the RSNE was read from the firmware: no U-g gap, the lengths + hashes line instead */
	CHECK(D.n4 == 2u + SWL25_PINGS && D.ndb4 == 2u + SWL25_PINGS && D.n3 == 3u + SWL25_PINGS && D.ndb3 == 3u + SWL25_PINGS);                                    /* ring 4: M1, M3, the ARP reply, 3 echo replies; ring 3: a status per post; each item has its read-index store + doorbell */
	CHECK(gw(D2HR + 8u) == (2u + SWL25_PINGS) % swd_rings[4].items && gw(D2HR + 4u) == (3u + SWL25_PINGS) % swd_rings[3].items);
	CHECK(!log_has_bytes(V_PMK, 32u) && !log_has_bytes(V_PTK, 48u) && !log_has_bytes(V_PTK + 32, 16u) && !log_has_bytes(V_PTK + 16, 16u) && !log_has_bytes(V_GTK, 16u));      /* canaries: PMK, PTK (KCK, KEK, TK), GTK */
	if (getenv("L25_DUMP") != NULL) {                                                                     /* debugging aid: the P1 log to a file */
		FILE *fp = fopen(getenv("L25_DUMP"), "w");

		if (fp != NULL) {
			fwrite(logbuf, 1u, loglen, fp);
			fclose(fp);
		}
	}
	CHECK(all_zero(swl25_H.sup.pmk, 32u) && all_zero(swl25_H.sup.tptk, 48u) && all_zero(swl25_H.sup.gtk, 16u) && swl25_H.hs_done == 1u);      /* the key material is gone from the engine state */
}

/* sprint 2026-11-08: the same whole path with the ring-0 cursor at the last slot: the flow create is item 127 and moves ring 0's write index 127 -> 0 (a lap), the dongle's read index 100 leaves room */
static void test_path_wrap(void)
{
	int rc;

	scen();
	x.d4wr = 127u;
	g1[(H2DW - G1PA) / 4u] = 127u;
	g1[(H2DR - G1PA) / 4u] = 100u;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 26u && x.d4wr == 0u && x.d4wrap0 == 1u && gw(H2DW) == 0u);
	CHECK(has("SWEET WLAN: ping summary sent=10 got=10 lost=0"));
	scen();                                                                                               /* the same with the dongle's read index at 0: slot 127 would make the ring look empty, refused */
	x.d4wr = 127u;
	g1[(H2DW - G1PA) / 4u] = 127u;
	g1[(H2DR - G1PA) / 4u] = 0u;
	rc = swl25_run(&x, 26u);
	CHECK(x.d4dstop && x.d4wr == 127u && gw(H2DW) == 127u && has("ring full"));
}

/* sprint 2026-11-08 (run join26c: 5 LAN broadcasts exhausted the 16 buffers after echo 7): the whole path with 40 junk frames mixed in; every buffer comes back, 10/10 */
static void test_path_noise(void)
{
	int rc;

	scen();
	D.flood = 40u;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 26u && D.badrx == 0u && D.rxn >= 40u + 2u + 1u + SWL25_PINGS);
	CHECK(has("SWEET WLAN: ping summary sent=10 got=10 lost=0") && !has("rx buffers exhausted") && !has("re-post refused"));
}

/* sprint 2026-11-08: quiet mode: the same path prints no per-store success line, counts them, and still prints its results and gaps */
static void test_quiet(void)
{
	int rc;

	scen();
	x.quiet = 1u;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 26u && x.nquiet > 100u);
	CHECK(!has("SWEET WLAN: G2 store ") && !has("SWEET WLAN: DP15 plan ") && has("SWEET WLAN: ping summary sent=10 got=10 lost=0") && has("WIFI GAP: H11 "));
}

static void test_fail(void)
{
	int rc;

	scen(); D.no_m3 = 1;                                                                                  /* P2: M3 never comes */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nK == 0u && D.ntx == 1u && has("WIFI GAP: H12 4way not complete after 10000 ms (last message seen: M1, M2 sent, no M3)\n") && x.d4dlv != 25u);
	CHECK(all_zero(swl25_H.sup.pmk, 32u) && all_zero(swl25_H.sup.tptk, 48u));      /* a failed run wipes the PMK and the derived PTK too (the keys never reached the dongle) */
	scen(); D.m1_tick = 0u;                                                                               /* P3: no M1 at all */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && D.ntx == 0u && has("WIFI GAP: H12 4way not complete after 10000 ms (last message seen: none, no M1)\n"));
	scen(); KS.status[0] = 3;                                                                             /* P4: the firmware refuses the pairwise key */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nK == 1u && has("STOP, no further store: H8 wsec_key pairwise refused status=3\n") && !has("wsec_key group set ok") && x.d4dlv != 25u);
	scen(); KS.status[1] = -2;                                                                            /* P5: ... the group key */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nK == 2u && has("wsec_key pairwise set ok\n") && has("STOP, no further store: H9 wsec_key group refused status=-2\n"));
	scen(); KS.status[3] = 5;                                                                             /* P6: ... the wsec set */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nK == 4u && has("STOP, no further store: H10 wsec set refused status=5\n") && !has("wsec merged ok"));
	scen(); D.no_status = 1;                                                                              /* P7: no tx status for M4: the keys follow after the wait, with a GAP */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && nK == 4u && has("WIFI GAP: L25 M4 tx status not seen in 200 ms, keys installed anyway\n"));
	scen(); D.no_arp = 1;                                                                                 /* P8: the gateway never answers the ARP request */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && x.d4dstop && D.ntx == 5u && has("WIFI GAP: DP13 gateway did not answer 3 ARP requests\n") && has("SWEET WLAN: ping summary sent=0 got=0 lost=0 rtt_us min/avg/max=0/0/0 bad=0 ignored=0\n"));
	scen(); D.bad_ep4 = 1;                                                                                /* P9: a ring-4 item with a wrong epoch */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && D.ntx == 0u && has("WIFI GAP: L25 ring 4 item 0 refused: check code 1 (epoch/size/xor)\n"));
	scen(); D.m1_tick = 99999u; D.flood = 300u;                                                           /* P10 (sprint 2026-11-08): each rx re-posts its buffer; the 257th ring-1 post (the shadow) is the loud stop */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: L25 rx buffers: re-post refused: the ring-1 shadow (256 posts) is used up\n") && D.rxn == 241u && D.badrx == 0u);       /* 16 first posts + 240 re-posts fill the 256 shadowed slots; rx 241 cannot re-post */
	scen(); x.l25c = NULL;                                                                                /* P11: no join context from the glue */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: no join context"));
	scen(); CFG.bssid[0] = CFG.bssid[1] = CFG.bssid[2] = CFG.bssid[3] = CFG.bssid[4] = CFG.bssid[5] = 0u; /* P12: no AP address */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: station MAC, BSSID, IP or gateway missing or invalid\n"));
	scen(); CFG.mac[0] = 0x03u;                                                                           /* P13: a multicast station MAC */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: station MAC, BSSID, IP or gateway missing or invalid\n"));
	scen(); CFG.rsne_beacon_len = 0u;                                                                     /* P14: no beacon RSNE */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: RSNE missing or too long\n"));
	scen(); D.inj_tick = 100u; D.inj_kind = 2u;                                                           /* P15: a second M1 after the keys: a gap, no second M2 */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && D.ntx == 3u + SWL25_PINGS && has("WIFI GAP: eapol group rekey / PTK rekey after join") && has("SWEET WLAN: ping summary sent=10 got=10 lost=0"));
	scen(); KS.status[2] = 7;                                                                             /* P16: the wsec get is refused */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nK == 3u && has("STOP, no further store: H10 wsec get refused status=7\n") && !has("wsec merged ok"));
	scen(); CFG.ip[0] = CFG.ip[1] = CFG.ip[2] = CFG.ip[3] = 0u;                                           /* P17: no static IP / no gateway */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("missing or invalid"));
	scen(); CFG.gw[0] = CFG.gw[1] = CFG.gw[2] = CFG.gw[3] = 0u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("missing or invalid"));
	scen(); CFG.gw[0] = CFG.gw[1] = CFG.gw[2] = CFG.gw[3] = 0u;                                           /* P17b (patch 0281): exactly one of ip / gw zero stays refused whatever the level */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("missing or invalid") && !has("L26 not run: DHCP"));
	scen(); memset(CFG.ip, 0, 4); memset(CFG.gw, 0, 4);                                                   /* P17c (patch 0281): ip == 0 AND gw == 0 = DHCP credentials: the join and the 4-way run, L26 (ARP/ping) does not */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && nK == 4u && has("SWEET WLAN: L25 done") && has("SWEET WLAN: L26 not run: DHCP credentials (no static address)\n") && !has("WIFI GAP: L25 not run") && !has("did not answer") && !has("ping summary") &&
	      D.ntx == 2u && x.d4dlv == 26u && has("SWEET WLAN: L25 live link (firmware)"));        /* only the 4-way's two transmissions (M2, M4); no ARP request, no echo request */
	scen(); memset(CFG.ip, 0, 4); memset(CFG.gw, 0, 4);                                                   /* P17d: the same at level 25 is the old L25-only run (no L26 line at all) */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && nK == 4u && has("SWEET WLAN: L25 done") && !has("L26 not run"));
	scen(); memset(CFG.mac, 0, 6);                                                                        /* P18 (T-baed step 1): no MAC from the glue, the firmware's cur_etheraddr is used */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && KS.nmac == 1u && memcmp(swl25_H.l.mac, V_SPA, 6) == 0 && has("SWEET WLAN: L25 station MAC read (cur_etheraddr, 6 bytes)\n") && has("SWEET WLAN: L25 done"));
	scen(); KS.mac[5] ^= 0x10u;                                                                           /* P19: the glue's MAC differs from the firmware's */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && KS.nmac == 1u && has("WIFI GAP: L25 not run: the station MAC from the glue differs from cur_etheraddr\n") && !has("station MAC read"));
	scen(); KS.maclen = 4u;                                                                               /* P20: a short answer */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("WIFI GAP: L25 not run: cur_etheraddr get refused or the answer is not 6 bytes\n"));
	scen(); KS.macst = 5;                                                                                 /* P21: the firmware refuses the get */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("cur_etheraddr get refused or the answer is not 6 bytes\n"));
	scen(); memset(CFG.mac, 0, 6); memset(KS.mac, 0, 6);                                                  /* P22: an all-zero firmware answer is no address */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("cur_etheraddr answer is zero or a multicast address\n"));
	scen(); memset(CFG.mac, 0, 6); KS.mac[0] = 0x01u;                                                     /* P23: a multicast firmware answer */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && nwrites() == 0u && has("cur_etheraddr answer is zero or a multicast address\n"));
	scen(); D.m1_tick = 99999u;                                                                           /* P18: the ring-4 write slot reads all ones (dead link) */
	g1[(D2HW - G1PA) / 4u + 2u] = 0u;
	dead4 = 1;
	rc = swl25_run(&x, 25u);
	dead4 = 0;
	CHECK(rc < 0 && x.stopped);
	scen(); D.m1_tick = 99999u; corrupt4 = 9999u;                                                         /* P19: the ring-4 write slot is outside the ring */
	rc = swl25_run(&x, 25u);
	corrupt4 = 0u;
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: L25 ring 4 write index 9999 outside the ring\n"));
	scen(); D.inj_tick = 2u; D.inj_kind = 1u;                                                             /* P20: a tx status for an id not in flight */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: L25 tx status for an unknown or repeated request id (code 0)\n"));
	scen(); D.txerr = 1;                                                                                  /* P21: a tx status with an error: a gap, the run goes on */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && has("WIFI GAP: L25 tx status error status=0 tx_status=3\n") && nK == 4u);
}

/* T-924f: the counters witness around the first M2 (M3 never comes, so the run reaches the end read) and the parser on malformed replies */
static void test_counters(void)
{
	uint8_t r[1200];
	uint32_t o[10], ver = 0;
	unsigned i;
	int rc;

	/* acked, AP silent: txframe +1 after the first M2, +6 at the end; no failures */
	scen(); D.no_m3 = 1;
	KC.tab[1][0] = 1u; KC.tab[2][0] = 6u; KC.tab[0][6] = 40u; KC.tab[1][6] = 41u; KC.tab[2][6] = 46u;
	KC.tab[0][8] = 1000u; KC.tab[1][8] = 1121u; KC.tab[2][8] = 1726u; KC.tab[0][9] = 500u; KC.tab[1][9] = 500u; KC.tab[2][9] = 995u;      /* T-8d5f: M2 121 B on air, 5 more M1s of 99 B */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && KC.n == 3u && has("WIFI GAP: H12 4way not complete after 10000 ms"));
	CHECK(has("SWEET WLAN: L25 witness counters base txframe=0 txnobuf=0 txnoassoc=0 txfail=0 txretry=0 txnoack=0 rxframe=40 fourwayfail=0\n"));
	CHECK(has("SWEET WLAN: L25 witness counters first-m2 d_txframe=1 d_txnobuf=0 d_txnoassoc=0 d_txfail=0 d_txretry=0 d_txnoack=0 d_rxframe=1 d_fourwayfail=0 (eapol_rx=1 m2_tx=1) cause=acked-ap-silent\n"));
	CHECK(has("SWEET WLAN: L25 witness counters end d_txframe=6 d_txnobuf=0 d_txnoassoc=0 d_txfail=0 d_txretry=0 d_txnoack=0 d_rxframe=6 d_fourwayfail=0 (eapol_rx=1 m2_tx=1) cause=acked-ap-silent\n"));
	/* T-8d5f: the Ethernet header of the first rx M1 and tx M2 with the address verdict (rx: DA = SPA, SA = AA; tx the reverse); full lines so swapped arguments cannot pass */
	CHECK(has("SWEET WLAN: L25 witness eth rx da=02:00:00:bb:00:02 sa=02:00:00:aa:00:01 type=888e dst_ok=1 src_ok=1 (frame 1)\n"));
	CHECK(has("SWEET WLAN: L25 witness eth tx da=02:00:00:aa:00:01 sa=02:00:00:bb:00:02 type=888e dst_ok=1 src_ok=1 (frame 1)\n"));
	CHECK(strstr(logbuf, "witness eth rx da=") < strstr(logbuf, "witness eth tx da="));
	CHECK(has("SWEET WLAN: L25 witness bytes base txbyte=1000 rxbyte=500\n") && has("SWEET WLAN: L25 witness bytes first-m2 d_txbyte=121 d_rxbyte=0 (m2_tx=1 eapol_rx=1)\n") && has("SWEET WLAN: L25 witness bytes end d_txbyte=726 d_rxbyte=495 (m2_tx=1 eapol_rx=1)\n"));
	CHECK(strstr(logbuf, "first-m2 d_txframe") < strstr(logbuf, "H12 4way not complete") && strstr(logbuf, "counters end") < strstr(logbuf, "H12 4way not complete"));      /* the end read comes before the gap */
	/* T-8d5f H-sa [read from source: swdp_net_rx drops a frame whose DA is neither our MAC nor broadcast before the EAPOL hook, sweet_wlan_data.h:476]: an M1 addressed to another station MAC is never seen, so the Ethernet witness lines only ever
	 * show dst_ok=1 (or a broadcast DA); the firmware's MAC differing from the M1 destination is a "no M1" run, not a witness hit */
	scen(); D.no_m3 = 1; memset(CFG.mac, 0, 6); KS.mac[5] ^= 0x10u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !has("witness eth rx") && !has("witness eth tx") && has("last message seen: none, no M1"));
	/* the dongle dropped it: txframe +0 */
	scen(); D.no_m3 = 1; KC.mode = 1;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && has("first-m2 d_txframe=0 ") && has("(eapol_rx=1 m2_tx=1) cause=dongle-drop\n"));
	/* txnoassoc +1 with txframe +1: still a dongle drop */
	scen(); D.no_m3 = 1; KC.tab[1][0] = 1u; KC.tab[1][2] = 1u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && has("first-m2 d_txframe=1 d_txnobuf=0 d_txnoassoc=1 ") && has("cause=dongle-drop\n"));
	/* on air, not acked */
	scen(); D.no_m3 = 1; KC.tab[1][0] = 1u; KC.tab[1][5] = 2u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && has("first-m2 d_txframe=1 d_txnobuf=0 d_txnoassoc=0 d_txfail=0 d_txretry=0 d_txnoack=2 ") && has("cause=air-no-ack\n"));
	/* legacy v11 layout */
	scen(); D.no_m3 = 1; KC.mode = 3; KC.tab[1][0] = 1u; KC.tab[1][3] = 3u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && has("first-m2 d_txframe=1 d_txnobuf=0 d_txnoassoc=0 d_txfail=3 ") && has("cause=air-no-ack\n"));
	/* a refused read is a loud GAP and the run goes on to the 4-way */
	scen(); D.no_m3 = 1; KC.status = -5;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && has("WIFI GAP: L25 counters read (base) refused status=-5 resp_len ") && !has("L25 witness counters") && has("H12 4way not complete") && D.ntx == 1u);
	/* an unknown version, a short reply: GAP, no numbers */
	scen(); D.no_m3 = 1; KC.mode = 2;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && has("WIFI GAP: L25 counters read (base) not parsed: code -2 version 7 ") && !has("L25 witness counters") && D.ntx == 1u);
	scen(); D.no_m3 = 1; KC.mode = 4;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && has("WIFI GAP: L25 counters read (base) not parsed: code -1 ") && D.ntx == 1u);
	/* the success path of the whole handshake still gets its two reads and ends at score 3 */
	scen();
	KC.tab[1][0] = 1u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && has("SWEET WLAN: L25 done: keys installed") && KC.n >= 1u && has("L25 witness counters base "));
	/* the parser on its own */
	{
		static const uint32_t v[10] = { 11, 22, 33, 44, 55, 66, 77, 88, 99, 110 };
		uint32_t n = counters_build(r, v, 0);

		CHECK(swl25_cnt_parse(r, n, o, &ver) == 0 && ver == 30u);
		for (i = 0; i < 10u; i++) CHECK(o[i] == v[i]);
		CHECK(swl25_cnt_parse(r, 3u, o, &ver) == -1);
		CHECK(swl25_cnt_parse(r, n - 4u, o, &ver) == -3);                      /* datalen above the reply */
		put16le(r + 2, 8u);                                                  /* datalen cuts the walk before the WLC tuple */
		CHECK(swl25_cnt_parse(r, n, o, &ver) == -4);
		n = counters_build(r, v, 0);
		put16le(r + 16 + 2, 200u);                                           /* the WLC tuple shorter than the last field */
		CHECK(swl25_cnt_parse(r, n, o, &ver) == -4);
		n = counters_build(r, v, 0);
		put16le(r + 16, 0x7u);                                               /* no tuple 0x100 */
		CHECK(swl25_cnt_parse(r, n, o, &ver) == -4);
		n = counters_build(r, v, 3);
		CHECK(swl25_cnt_parse(r, n, o, &ver) == 0 && ver == 11u && o[3] == 44u && o[7] == 88u && o[8] == 99u && o[9] == 110u);
		CHECK(swl25_cnt_parse(r, 400u, o, &ver) == -4);                        /* v11 shorter than fourwayfail */
		put16le(r, 6u);
		CHECK(swl25_cnt_parse(r, n, o, &ver) == -2);
	}
}

/* T-9d71: the RSNE of the firmware's association request goes into M2's key data (the AP compares it with the one it stored: `RSN_IE Different in msg 2`). The MIC of the changed M2 is recomputed here from the vector PTK */
static int m2_kd_is(const uint8_t *rsne, uint32_t n)
{
	const uint8_t *f = D.txf[0], *e = f + SWJ_ETH_HLEN;
	uint32_t el = D.txl[0] - SWJ_ETH_HLEN;
	uint8_t mic[16];

	swj_eapol_mic(V_PTK, e, el, mic);
	return D.ntx >= 1u && el == SWJ_KEY_FIXED + n && swj_be16(e + SWJ_OFF_DLEN) == n && memcmp(e + SWJ_KEY_FIXED, rsne, n) == 0 && memcmp(mic, e + SWJ_OFF_MIC, 16) == 0;
}
static void ai_gapcase(const char *why)
{
	char want[160];
	int rc = swl25_run(&x, 25u);

	snprintf(want, sizeof want, "WIFI GAP: L25 assoc RSNE not read, M2 keeps the beacon RSNE: %s\n", why);
	CHECK(rc == 0 && !x.d4dstop && D.ntx >= 1u && D.txl[0] == sizeof V_M2 && memcmp(D.txf[0], V_M2, sizeof V_M2) == 0 && has(want) && has("L25 rsne assoc=unread m2=22") && has("WIFI GAP: M2 RSNE is the caller's copy") && swl25_H.arsne_len == 0u);
}
static void test_assoc(void)
{
	uint8_t alt[22], rs[70], big[64];
	int rc;

	memcpy(alt, V_RSNE, 22); alt[20] = 0x0cu;                                                             /* the firmware's RSNE: RSN capabilities differ from the beacon's */
	scen();                                                                                               /* A1: equal RSNEs: M2 is the vector, the order of the GETs is judged */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && AI.n_info == 1u && AI.n_ies == 1u && AI.shape_bad == 0u && AI.s_info == 1u && AI.s_ies == 2u && AI.s_mac == 3u);
	CHECK(D.txl[0] == sizeof V_M2 && memcmp(D.txf[0], V_M2, sizeof V_M2) == 0 && swl25_H.arsne_len == 22u && m2_kd_is(V_RSNE, 22u));
	CHECK(has("SWEET WLAN: L25 assoc_info: req_len 40 resp_len 40 flags 0 (answer 40 B)\n") && has("SWEET WLAN: L25 assoc_req_ies: 36 IE bytes expected, answer 36 B\n"));
	CHECK(has("SWEET WLAN: L25 rsne assoc=22 m2=22 equal=1 hash_assoc=") && has("SWEET WLAN: L25 rsne assoc vs beacon: beacon=22 ") && has(" equal=1 bytes_differ=0 first_diff=-1\n"));
	CHECK(!has("WIFI GAP: L25 assoc RSNE") && !has("M2 RSNE is the caller's copy"));                          /* a read RSNE is no U-g gap any more */
	scen(); ai_reset(alt, 22u); memcpy(AP_RSNE, alt, 22);                                                 /* A2: the firmware's RSNE differs from the beacon's: M2 carries the firmware's */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && m2_kd_is(alt, 22u) && memcmp(D.txf[0], V_M2, sizeof V_M2) != 0);
	CHECK(has("SWEET WLAN: L25 rsne assoc=22 m2=22 equal=1 hash_assoc=") && has(" equal=0 bytes_differ=1 first_diff=20\n") && !has("WIFI GAP: L25 assoc RSNE"));
	CHECK(!has("hash_assoc=00000000") && swl25_fnv(alt, 22u) != swl25_fnv(V_RSNE, 22u));
	CHECK(has("SWEET WLAN: eapol rx msg=3\n") && has("SWEET WLAN: eapol tx msg=4\n"));                       /* the AP stored the firmware's RSNE: it accepts this M2 and answers M3 */
	scen(); ai_reset(alt, 22u); memcpy(AP_RSNE, alt, 22); rc = swl25_run(&x, 25u);                        /* A3: a 4-way that finishes with the firmware's RSNE in M2 (M3 carries the beacon's) */
	CHECK(rc == 0 && has("SWEET WLAN: 4way complete PTK=CCMP GTK=CCMP\n"));
	scen(); ai_reset(alt, 22u); memcpy(AP_RSNE, alt, 22); AI.flags = 1u; AI.req_len += 6u;      /* A4: a reassociation: 6 B more fixed header, same IEs */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && m2_kd_is(alt, 22u) && has("flags 0x1 (answer 40 B)\n") && has("36 IE bytes expected"));
	scen(); ai_reset(V_RSNE, 22u); AI.flags = 1u; AI.req_len += 6u; AI.ies_resp = 35u;                    /* A5: reassociation, the answer is one byte short of the IEs */
	ai_gapcase("answer 35 B < 36 IE bytes");
	scen(); ai_reset(alt, 22u);                                                                           /* A6: the answer is longer than the IEs: only req_len - 4 B are parsed (the RSNE after them is not taken) */
	AI.req_len = 4u + 11u; AI.ies_resp = 512u;
	ai_gapcase("no RSNE (IE 48) in the request");
	scen(); AI.st_info = 5;                                                                               /* A7: assoc_info refused */
	ai_gapcase("assoc_info refused status=5");
	CHECK(AI.n_ies == 0u && KS.nmac == 1u);                                                               /* the second GET is not asked, the run goes on */
	scen(); AI.info_resp = 8u;                                                                            /* A8: assoc_info answer shorter than 12 B */
	ai_gapcase("assoc_info answer 8 B < 12");
	scen(); AI.req_len = 0u;                                                                              /* A9: no request recorded */
	ai_gapcase("req_len 0 outside 5..516");
	scen(); AI.req_len = 4u;                                                                              /* A10: a request without IEs */
	ai_gapcase("req_len 4 outside 5..516");
	scen(); AI.req_len = 4u + 513u;                                                                       /* A11: more IEs than the 512 B reply room */
	ai_gapcase("req_len 517 outside 5..516");
	scen(); AI.st_ies = -3;                                                                               /* A12: assoc_req_ies refused */
	ai_gapcase("assoc_req_ies refused status=-3");
	scen(); AI.ies_resp = 35u;                                                                            /* A13: assoc_req_ies answer short */
	ai_gapcase("answer 35 B < 36 IE bytes");
	scen(); ai_reset(NULL, 0u);                                                                           /* A14: no RSNE in the request */
	ai_gapcase("no RSNE (IE 48) in the request");
	scen(); AI.ies[12] = 40u;                                                                             /* A15: an IE length that overruns the request */
	ai_gapcase("an IE overruns the request");
	memset(rs, 0, sizeof rs); rs[0] = 48u; rs[1] = 0u;                                                    /* A16: an RSNE element of length 0 (2 B) */
	scen(); ai_reset(rs, 2u);
	ai_gapcase("RSNE length 2 outside 4..64");
	memset(rs, 0xAB, sizeof rs); rs[0] = 48u; rs[1] = 66u;                                                /* A17: an RSNE of 68 B does not fit the M2 copy */
	scen(); ai_reset(rs, 68u);
	ai_gapcase("RSNE length 68 outside 4..64");
	memset(big, 0xCD, sizeof big); big[0] = 48u; big[1] = 62u;                                            /* A18: 64 B, the largest that is taken */
	scen(); ai_reset(big, 64u); memcpy(AP_RSNE, big, 64); AP_N = 64u;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !x.d4dstop && swl25_H.arsne_len == 64u && m2_kd_is(big, 64u));
	scen(); ai_reset(alt, 22u); AI.st_info = 0; KS.macst = 5;                                             /* A19: a later stop (cur_etheraddr refused) still comes after both GETs */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && x.d4dstop && AI.s_info == 1u && AI.s_ies == 2u && AI.s_mac == 3u && D.ntx == 0u);
	scen(); memcpy(AP_RSNE, alt, 22); AI.st_ies = -3;                                                     /* A21: the model of the failure this ticket fixes: the AP stored the firmware's RSNE, the read failed, M2 carries the beacon's: no M3 */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && !has("SWEET WLAN: eapol rx msg=3\n") && has("WIFI GAP: L25 assoc RSNE not read, M2 keeps the beacon RSNE: assoc_req_ies refused status=-3\n"));
	{       /* the report on its own: the M2 source (sup.rsne_assoc) differing from the read RSNE prints equal=0 (a copy that lost a byte must not be reported as equal) */
		static struct swl25h hh;

		world_reset();
		memset(&hh, 0, sizeof hh);
		memcpy(hh.arsne, V_RSNE, 22); hh.arsne_len = 22u;
		memcpy(hh.sup.rsne_assoc, V_RSNE, 22); hh.sup.rsne_assoc_len = 21u;
		memcpy(hh.sup.rsne_beacon, V_RSNE, 22); hh.sup.rsne_beacon_len = 22u;
		swl25_rsne_report(&hh);
		CHECK(has("SWEET WLAN: L25 rsne assoc=22 m2=21 equal=0 hash_assoc="));
		world_reset();
		hh.sup.rsne_assoc_len = 22u; hh.sup.rsne_assoc[21] ^= 1u;
		swl25_rsne_report(&hh);
		CHECK(has("SWEET WLAN: L25 rsne assoc=22 m2=22 equal=0 hash_assoc="));
	}
	scen(); x.l25c = NULL;                                                                                /* A20: no join context: no GET is made */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && AI.n_info == 0u && AI.n_ies == 0u && has("WIFI GAP: L25 not run: no join context"));
}

/* W-UI: the live-link reads after L26 (swl25_live) and the chanspec decoder */
static void test_live(void)
{
	int rc;

	CHECK(swl25_chspec_ctl(0xD024u) == 36u && swl25_chspec_ctl(0x1006u) == 6u);                           /* 20 MHz: the channel field (5 GHz 36, 2.4 GHz 6) */
	CHECK(swl25_chspec_ctl(0xD826u) == 36u && swl25_chspec_ctl(0xD926u) == 40u);                           /* 40 MHz center 38: lower 36, upper 40 */
	CHECK(swl25_chspec_ctl(0xE02Au) == 36u && swl25_chspec_ctl(0xE32Au) == 48u);                           /* 80 MHz center 42: LL 36 .. UU 48 */
	CHECK(swl25_chspec_ctl(0xE832u) == 36u && swl25_chspec_ctl(0xEF32u) == 64u);                           /* 160 MHz center 50: 36 .. 64 */
	CHECK(swl25_chspec_ctl(0xD926u & ~0x3800u) == 0u && swl25_chspec_ctl(0xDA26u) == 0u);                  /* bw 320 field / sideband 2 on 40 MHz: unknown = 0 */
	scen(); LV.st_bssid = -1; LV.rssi = 5;                                                                /* L1: BSSID refused, RSSI out of range: GAPs, no stop, the rest read */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && swl25_H.live_ok == 0x5u && has("WIFI GAP: L25 live link: WLC_GET_BSSID status=-1") && has("WIFI GAP: L25 live link: WLC_GET_RSSI"));
	scen(); ai_reset(NULL, 0u); AI.ies[1] = 0u; AI.ies_n = AI.ies_resp = 3u; AI.req_len = 4u + 3u;      /* L2: an empty SSID element */
	rc = swl25_run(&x, 26u);
	CHECK((swl25_H.live_ok & 1u) == 0u && has("SSID element of the association request is empty"));
	scen();                                                                                               /* L3: lv 25 reads nothing live */
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && LV.n == 0u && (swl25_H.live_ok & 0xEu) == 0u);
}

#include "wlan_l25_host_rj.h"        /* patch 0282: the re-join state machine, the EAPOL hook and the kept secrets */
#include "wlan_l25_host_sc.h"        /* patch 0292: the keep-phase scan (WIFI-COVERAGE D3/D4) */
#include "wlan_l25_host_ka.h"        /* ALL-ON T-32: the firmware keep-alive (mkeep_alive) */
#include "wlan_l25_host_pr.h"        /* patch 0363 (LOWCLK B3): the read-only GET_PM / country / chanspecs probe */

int main(void)
{
	(void)swg_go;
	test_env();
	test_flow();
	test_rx();
	test_path();
	test_path_wrap();
	test_path_noise();
	test_quiet();
	test_fail();
	test_counters();
	test_assoc();
	test_live();
	test_rejoin();
	test_scan();
	test_ka();
	test_probe();
	if (fails != 0u) {
		printf("RESULT: FAIL wlan_l25_host_test %u checks failed\n", fails);
		return 1;
	}
	printf("RESULT: PASS wlan_l25_host_test (env, flow create, rx posts, rx poll, host 4-way M1-M4, key installs, ARP + echo ping)\n");
	return 0;
}
