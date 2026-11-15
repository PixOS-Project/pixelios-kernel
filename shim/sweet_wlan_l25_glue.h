/*
 * Glue-side inputs of the L25/L26 engine (T-37d8 steps 2-3, the part a host can check): where the DATA block may sit and the struct swl25_cfg built from what the glue really has.
 * Pure functions on the structs of sweet_wlan_g2.h / sweet_wlan_l25.h; no allocation, no bus access, no kernel types. The kernel glue (patch 0174 sweet_wlan.c) is NOT wired to it yet
 * (open, ticket T-37d8 Log): the block allocation (kmem_alloc_contig + swd_contig + swd_audit_all like the G1 block), its S2MPU/DMA-window question (SPEC-WIFI U1/U2, open), the swc_load call
 * that fills struct swc_creds, and the sw_g2_list entries for every store class of L25 (DP15 plans, GET_VAR cur_etheraddr). Until that is reviewed (Opus, T-bb44) nothing calls swl25g_arm and the engine
 * keeps printing `WIFI GAP: L25 not run`.
 *
 * Order in time: swl25g_arm runs BEFORE the L23 scan (it needs the credentials, the join target and the DATA block); the RSNE of the target arrives during the scan through the hook
 * x->bssn = swl25g_note (sweet_wlan_l25_rsne.h swl25r_note, which then copies the element into cfg.rsne_assoc / rsne_beacon [guess: assoc == beacon, SPEC-WIFI U-g]). If the scan never delivers
 * it, cfg.rsne_*_len stay 0 and swl25_go refuses with `WIFI GAP: L25 not run: RSNE missing or too long` (rule 8); swl25r_status says which of the causes it was.
 * cfg.mac stays all zero: swl25_go then takes the firmware's cur_etheraddr (T-baed step 1). Secrets (rule 4): the PMK is copied once, never printed; swl25g_disarm wipes it.
 *
 * Include order in the includer: sweet_wlan_data.h, sweet_wlan_join.h, sweet_wlan_g2.h, sweet_wlan_l25.h, sweet_wlan_l25_rsne.h, sweet_wlan_cred.h, sweet_wlan_l25_wl.h, then this file.
 * T-43af: the last section holds the pure parts of the L25 store-judge wiring (layout fill, BSSID, flow-open scan); the kernel glue (patch 0174 sw3_g2_ok / sw3_wr) only calls them.
 */
#ifndef SWEET_WLAN_L25_GLUE_H
#define SWEET_WLAN_L25_GLUE_H

#if !defined(SWEET_WLAN_L25_H) || !defined(SWEET_WLAN_L25_RSNE_H) || !defined(SWEET_WLAN_CRED_H) || !defined(SWEET_WLAN_L25_WL_H)
#error "sweet_wlan_l25_glue.h needs sweet_wlan_l25.h, sweet_wlan_l25_rsne.h, sweet_wlan_cred.h and sweet_wlan_l25_wl.h first"
#endif

struct swl25g {
	struct swl25_tgt tgt;           /* FIRST member: the hook finds this struct through x->bssc, which points at tgt */
	struct swl25_cfg cfg;
};

static void
swl25g_gap(const char *what)
{
	char gb[64];

	snprintf(gb, sizeof gb, "L25 %.50s", what);
	swl_gap("l25", gb, "high");
	printf("WIFI GAP: %s\n", gb);
}

/* The DATA block [pa, pa + len) the glue allocated: accepted only when it is there, big enough, page aligned, does not wrap, is disjoint from the G1 block already in `in`, and (above 4 GiB)
 * its low 32 bits lie inside [alias_lo, alias_hi) like sw_g2_alias_ok demands of the G1 block (the dongle's 32-bit view of host memory; SPEC-WIFI D-11). Sets in->data / in->data_len; a refusal prints one
 * WIFI GAP line and leaves both 0. 0 = ok, -1 = refused. */
static int
swl25g_data(struct swg_in *in, uint64_t pa, uint32_t len, uint32_t alias_lo, uint32_t alias_hi)
{
	const uint64_t lo = pa & 0xFFFFFFFFull;
	const char *why = NULL;

	in->data = 0;
	in->data_len = 0;
	if (pa == 0u || len == 0u) {
		why = "DATA block missing";
	} else if (len < SWL25_DATA_MIN || (pa & 0xFFFu) != 0u) {
		why = "DATA block too small or not page aligned";
	} else if (pa + len < pa) {
		why = "DATA block wraps";
	} else if (pa < in->blk + in->blk_len && pa + len > in->blk) {
		why = "DATA block overlaps the G1 block";
	} else if ((pa >> 32) != 0u && !(lo >= alias_lo && len <= alias_hi - alias_lo && lo <= (uint64_t)(alias_hi - len))) {
		why = "DATA block outside the DMA alias window";
	}
	if (why != NULL) {
		swl25g_gap(why);
		return -1;
	}
	in->data = pa;
	in->data_len = len;
	return 0;
}

static int
swl25g_zero(const uint8_t *p, unsigned n)
{
	unsigned i;
	uint8_t o = 0;

	for (i = 0; i < n; i++) {
		o |= p[i];
	}
	return o == 0u;
}

/* g->cfg from the credentials (PMK, station address, gateway) and the join target's BSSID; g->tgt prepared for the scan hook. Every missing input prints its own WIFI GAP (all of them, not
 * only the first) and g is left all zero. 0 = ok, -1 = something missing. */
static int
swl25g_cfg(struct swl25g *g, const struct swc_creds *cr, const uint8_t bssid[6])
{
	int bad = 0;

	memset(g, 0, sizeof *g);
	if (cr == NULL || swl25g_zero(cr->pmk, 32u)) {
		swl25g_gap("PMK missing");
		bad = 1;
	}
	if (cr != NULL && swl25g_zero(cr->ip, 4u) && swl25g_zero(cr->gw, 4u)) {
		/* patch 0281: ip == 0 AND gw == 0 is a DHCP credential blob: no static address (swl25_go skips L26 and says so). Exactly one of the two zero is still a missing value below. */
		printf("SWEET WLAN: L25 glue: DHCP credentials (no static station IP or gateway): addresses stay zero, L26 will not run\n");
	} else {
		if (cr == NULL || swl25g_zero(cr->ip, 4u)) {
			swl25g_gap("station IP missing");
			bad = 1;
		}
		if (cr == NULL || swl25g_zero(cr->gw, 4u)) {
			swl25g_gap("gateway missing");
			bad = 1;
		}
	}
	if ((bssid == NULL || swl25g_zero(bssid, 6u)) && (cr == NULL || cr->ssid_len == 0u || cr->ssid_len > 32u)) {
		swl25g_gap("join target BSSID missing (wildcard)");
		bad = 1;
	}
	if (bad) {
		return -1;
	}
	memcpy(g->cfg.pmk, cr->pmk, 32);
	memcpy(g->cfg.ip, cr->ip, 4);
	memcpy(g->cfg.gw, cr->gw, 4);
	if (bssid != NULL && !swl25g_zero(bssid, 6u)) {
		memcpy(g->cfg.bssid, bssid, 6);
		memcpy(g->tgt.bssid, bssid, 6);
	} else {        /* T-fc10 H2: wildcard join: the target is the first scan record of the credentials' SSID; cfg.bssid is filled by swl25g_note once the record is read */
		g->tgt.ssid_len = (uint8_t)cr->ssid_len;
		memcpy(g->tgt.ssid, cr->ssid, cr->ssid_len);
	}
	return 0;
}

/* the L23 hook: swl25r_note, then the element it found goes to the cfg (assoc = beacon, the recorded guess) */
static void
swl25g_adopt(struct swl25g *g)
{
	if (g->tgt.state == SWL25R_OK && g->cfg.rsne_beacon_len != g->tgt.rsne_len) {
		memcpy(g->cfg.bssid, g->tgt.bssid, 6);      /* T-fc10 H2: a no-op for a given BSSID, the scan record's for a wildcard join */
		memcpy(g->cfg.rsne_assoc, g->tgt.rsne, g->tgt.rsne_len);
		memcpy(g->cfg.rsne_beacon, g->tgt.rsne, g->tgt.rsne_len);
		g->cfg.rsne_assoc_len = g->cfg.rsne_beacon_len = g->tgt.rsne_len;
	}
}

static int
swl25g_note(struct swg_wx *x, const struct swg_ev *ev, uint64_t bp, uint32_t roff, uint32_t avail)
{
	struct swl25g *g = (struct swl25g *)x->bssc;
	int ex = swl25r_note(x, ev, bp, roff, avail);

	if (ex == 0) {
		swl25g_adopt(g);
	}
	return ex;
}

/* patch 0288 (T-fca2 design, run wd6-hotspot-b: `L24 join done`, then `L25 not run: station MAC, BSSID, IP or gateway missing`: the hotspot was in none of the 20 scan records, the firmware found it during the join itself).
 * The L25 hook x->bssget: when the target record was never seen (state NONE, wildcard BSSID, the credentials' SSID known), ONE read-only WLC_GET_BSS_INFO (136) GET: payload 1024 B = the length word 1024 (LE, bytes 00 04 00 00) then zeros,
 * answer buf+0 echo, record wl_bss_info_v109_t at buf+4 [src docs/research/T-fca2-hotspot-bssid-design.md fact 5; wl_cfg80211.c.new:14450-14480]. The record goes through swl25r_note_bss_info (parse + bounds + SSID must equal the credentials' SSID),
 * then into the cfg. This is the ONLY new firmware command of patch 0288; the whitelist row is in sweet_wlan_l25_wl.h. A refused/short/other-SSID answer is a GAP and leaves the target unset (swl25_go then refuses as before).
 * 0 = done (taken or refused with a GAP) or soft stop (x->d4dstop); negative = the bus stopped */
#define SWL25G_BSSINFO_LEN 1024u
#define SWL25G_WLC_GET_BSS_INFO 136u    /* [src wlioctl_defs.h.new:819] */
static uint8_t swl25g_bi_rsp[SWL25G_BSSINFO_LEN + 4u];      /* +4: the whole-word copy of the last bytes */

static int
swl25g_bss_info(struct swg_wx *x)
{
	struct swl25g *g = (struct swl25g *)x->bssc;
	struct swd4b_ioctl_d2h cm;
	static const uint8_t lenw[3] = { 0x04u, 0x00u, 0x00u };      /* bytes 1..3 of the length word 1024 (byte 0 is the NUL of the empty name) */
	char gb[64];
	int ex;

	if (g == NULL || g->tgt.state != SWL25R_NONE || !swl25g_zero(g->tgt.bssid, 6u) || g->tgt.ssid_len == 0u) {
		return 0;
	}
	memset(swl25g_bi_rsp, 0, sizeof swl25g_bi_rsp);
	memset(&cm, 0, sizeof cm);
	ex = swg_e_call(x, SWL25G_WLC_GET_BSS_INFO, "WLC_GET_BSS_INFO", "", lenw, 3u, SWL25G_BSSINFO_LEN, 1, SWL25G_BSSINFO_LEN, swl25g_bi_rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.status != 0) {
		snprintf(gb, sizeof gb, "BSS_INFO refused status=%d", (int)cm.status);
		swl_gap("rsne", gb, "high");
		printf("WIFI GAP: L25 WLC_GET_BSS_INFO refused status=%d: the join target stays unknown\n", (int)cm.status);
		return 0;
	}
	ex = swl25r_note_bss_info(x, swl25g_bi_rsp, cm.resp_len < SWL25G_BSSINFO_LEN ? cm.resp_len : SWL25G_BSSINFO_LEN);
	if (ex < 0) {
		return ex;
	}
	swl25g_adopt(g);
	return 0;
}

/* sweetwlan >= 25: place the DATA block, build the cfg, install the hook and x->l25c. Both checks always run (every gap shows); if either fails x->l25c stays NULL, no hook is installed,
 * in->data is 0 and g is zero. 1 = armed, 0 = refused. */
static int
swl25g_arm(struct swg_wx *x, struct swl25g *g, const struct swc_creds *cr, const uint8_t bssid[6], uint64_t data_pa, uint32_t data_len, uint32_t alias_lo, uint32_t alias_hi)
{
	int bad;

	x->l25c = NULL;
	x->bssn = NULL;
	x->bssget = NULL;
	x->bssc = NULL;
	bad = swl25g_data(&x->in, data_pa, data_len, alias_lo, alias_hi) != 0;
	bad |= swl25g_cfg(g, cr, bssid) != 0;
	if (bad) {
		x->in.data = 0;
		x->in.data_len = 0;
		swc_wipe(g, (uint32_t)sizeof *g);
		return 0;
	}
	x->l25c = &g->cfg;
	x->bssn = swl25g_note;
	x->bssc = g;
	x->bssget = swl25g_bss_info;
	return 1;
}

/* after the run (every path): the hook and the cfg pointer are gone, the PMK and the RSNE copy are wiped */
static void
swl25g_disarm(struct swg_wx *x, struct swl25g *g)
{
	x->l25c = NULL;
	x->bssn = NULL;
	x->bssget = NULL;
	x->bssc = NULL;
	swc_wipe(g, (uint32_t)sizeof *g);
}

/* ------------------------------------------------------------------ T-43af: the L25 store judge (sweet_wlan_l25_wl.h) wired into the glue; the pure parts, host-tested */

/* Fill the judge's layout from the glue's own G1 plan and the DATA block (never from the engine's books). Returns swl25w_layout_bad(): 0 = ok, else the failing clause (the caller then does not arm L25). */
static int
swl25g_wl_layout(struct swl25w *w, const struct swd_plan *p, uint64_t g1_pa, uint64_t data_pa, uint32_t data_len, int (*rd)(void *ctx, int space, uint32_t off, uint32_t *v), void *ctx)
{
	unsigned i;

	memset(w, 0, sizeof *w);
	w->h2d_w = p->b[SWD_IDX_H2D_W].off;
	w->d2h_w = p->b[SWD_IDX_D2H_W].off;
	w->d2h_r = p->b[SWD_IDX_D2H_R].off;
	w->h2d_r = p->b[SWD_IDX_H2D_R].off;     /* sprint 2026-11-08: ring-0 wrap */
	for (i = 0; i < 5u; i++) {
		w->ring[i] = p->b[SWD_H2D_CTRL + i].off;
		w->depth[i] = swd_rings[SWD_H2D_CTRL + i].items;
	}
	w->ioctbuf = p->b[SWD_IOCTBUF].off;
	w->respbuf = p->b[SWD_RESPBUF].off;
	w->evbuf = p->b[SWD_EVBUF].off;
	w->g1_pa = g1_pa;
	w->g1_len = p->total;
	w->data_pa = data_pa;
	w->data_len = data_len;
	w->rx_n = data_len >= SWL25_RX_OFF + SWDP_NSLOT * SWL25_BUF_SZ ? (data_len - SWL25_RX_OFF) / SWL25_BUF_SZ : 0u;    /* patch 0357: the rx buffers the glue's own block holds (the judge's cap, never the engine's pool size) */
	if (w->rx_n > SWL25W_RX_MAX) {
		w->rx_n = SWL25W_RX_MAX;
	}
	w->flowid = SWL25_FLOWID;
	w->rd = rd;
	w->ctx = ctx;
	return swl25w_layout_bad(w);
}

/* T-3784 (review T-98d9 r1 F1): the arm decision of sw_g2_hook as one pure step. The judge layout is filled first (a missing DATA block = no layout call, -1), then swl25g_arm ALWAYS runs so every missing-input gap prints;
 * armed (return 1) only when both accepted. A refused layout (clause > 0) adds its own gap and log line, and any refusal runs swl25g_disarm: no hook, no cfg pointer, the cfg (PMK) wiped, so the engine finds no l25c and prints
 * `WIFI GAP: L25 not run` without an L25 store. The caller stores the result in hw->l25arm, which is the only thing that lets the store route judge at level 25. */
static int
swl25g_wl_arm(struct swg_wx *x, struct swl25g *g, struct swl25w *w, const struct swd_plan *p, uint64_t g1_pa, const struct swc_creds *cr, const uint8_t bssid[6], uint64_t data_pa, uint32_t data_len, uint32_t alias_lo, uint32_t alias_hi, int (*rd)(void *ctx, int space, uint32_t off, uint32_t *v), void *ctx)
{
	const int lb = data_pa == 0u ? -1 : swl25g_wl_layout(w, p, g1_pa, data_pa, data_len, rd, ctx);
	const int armed = swl25g_arm(x, g, cr, bssid, data_pa, data_len, alias_lo, alias_hi) == 1 && lb == 0;

	if (lb > 0) {
		swl_gap("l25", "glue: L25 store judge layout invalid, L25 not armed", "high");
		printf("SWEET WLAN: L25 glue: store judge layout clause %d invalid, L25 not armed\n", lb);
	}
	if (!armed) {
		swl25g_disarm(x, g);
	} else {
		w->escan_sync = &x->escan_sync;       /* review 0292 F2: the judge reads the id of the escan START that is being sent (only an armed judge carries it) */
	}
	return armed;
}

/* The AP MAC of the flow-create item: the BSSID the scan hook put in the cfg (a wildcard join learns it from the scan record, T-fc10 H2). All zero = not known: the judge then leaves those 6 bytes free (bssid_set 0). The MAC stays free
 * (cur_etheraddr arrives in the response buffer, after the first judged store). */
static void
swl25g_wl_bssid(struct swl25w *w, const struct swl25g *g)
{
	memcpy(w->bssid, g->cfg.bssid, 6);
	w->bssid_set = swl25g_zero(g->cfg.bssid, 6u) ? 0u : 1u;
}

/* After an accepted ring-2 read-index store (class L8, slot 0) of old -> nw: the dongle answered the flow create when one of the items consumed, read back through the judge's own flushing accessor, is a
 * FLOW_CREATE_CMPLT with status 0 for the flow id, and a flow create was committed (flow_created, set by swl25w_done). Reads at most the 16 items one batch can consume. */
static void
swl25g_wl_flow(struct swl25w *w, uint32_t old, uint32_t nw)
{
	uint32_t s, n, k, v;
	uint8_t it[24];
	struct swdp_cmpl c;

	if (w->depth[2] == 0u || !w->flow_created || w->flow_open) {
		return;
	}
	old &= 0xFFFFu;         /* the whole index words (upper half kept) may be passed */
	nw &= 0xFFFFu;
	for (s = old % w->depth[2], n = 0; s != nw % w->depth[2] && n < SWL25W_D2H_BATCH; s = (s + 1u) % w->depth[2], n++) {
		for (k = 0; k < 6u; k++) {
			if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[2] + s * 24u + 4u * k, &v)) {
				return;
			}
			it[4u * k] = (uint8_t)v;
			it[4u * k + 1u] = (uint8_t)(v >> 8);
			it[4u * k + 2u] = (uint8_t)(v >> 16);
			it[4u * k + 3u] = (uint8_t)(v >> 24);
		}
		if (swdp_parse_flow_cmpl(it, &c) == 0 && (int16_t)c.status == 0 && c.ring_id == w->flowid) {
			w->flow_open = 1u;
			return;
		}
	}
}

#endif /* SWEET_WLAN_L25_GLUE_H */
