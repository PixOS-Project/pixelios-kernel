/* wlan_en0_bus.h - the `struct en0_bus` of wlan_en0.h on the live engine state after L25 (sprint 2026-11-08, W-IOSPING prerequisite).
 *
 * The XNU `en0` ifnet (wlan_en0.h, poll mode) names its transport as four callbacks; this file implements them on what the one-shot L25 hook leaves behind:
 *   exec    -> swg_dp_exec (sweet_wlan_g2d.h): every store of every tx post, rx re-post and D2H consume still goes through the DP15 audit (swdp_audit twice: en0 and the executor),
 *              the kind sequence, the old-word compare against the shadows and the read-back. Only three plan shapes are accepted (tx = PAYLOAD+ITEM+WRIDX+DB, rx post = ITEM ring 1
 *              +WRIDX+DB, consume = RDIDX+DB ring 3/4); anything else is a printed refusal. exec < 0 means "not executed" for the callers; after a store failed in the MIDDLE of a plan
 *              the engine is hard-stopped (d4dstop, swg_dp_exec) and every later exec is refused, so a half-written slot is never committed by an index.
 *   d2h_wr  -> the D2H write index slot of the ring in the G1 block (x->in.idx[SWG_IDX(SWD_IDX_D2H_W)] + 4 * (ring - 2), as swl25_d2h reads it).
 *   d2h_item-> the item words of ring 3 / ring 4 in the G1 block (x->in.ring[ring] + idx * isz, as swl25_item reads them), copied into a bus-owned buffer.
 *   rxbuf   -> the rx buffer of request slot `slot` in the DATA block, read word by word through the engine bus (x->b->rd, space SWG_SP_DATA, as swl25_dread does).
 * A read that fails or reads all ones (dead link) latches `dead`: one GAP, then d2h_wr answers 0xFFFFFFFF (en0 prints its own "write index outside the ring"), d2h_item answers
 * NULL and exec refuses (en0 prints the DP15 gap). The bus never calls swg_stop (its undo would restore EP/RC command registers under a live data path).
 *
 * Limits of L25 lifted HERE (none needs a change in a file this slice does not own):
 *  (1) SWL25_SHADOW_ITEMS = 16 (sweet_wlan_l25.h:34): the ring-1 and flow-ring shadows (struct swl25 sh_r1 / sh_fl) cover 16 items, a later slot stops the engine (swg_dp_shadow NULL
 *      -> SWG_E_ADDR). en0_live_up installs bus-owned shadows of the FULL rings (ring 1: 2048 items, patch 0167 swd_rings[1]; flow ring: SWL25_FLOW_DEPTH items) in x->dpsh[0..1],
 *      seeded from the L25 shadows (the first 16 items), the rest zero (the glue allocates the G1/DATA blocks zeroed; the L25 shadows assume the same).
 *  (2) rx buffers are posted once by L25 (16): wlan_en0.h en0_poll_rx refills the pool after the completions of a poll pass, in batches (patch 0357: request slots from the book, ring 1 wraps at its depth, epoch =
 *      absolute count, swdp_ring_post, one doorbell per batch); en0_live_up tops the pool up to env.rx_slots (the count swdp_rx_pick chose, 16..255) with en0_rx_fill. The H2D read cursors those posts check come from en0l_h2d_sync below (wlan_en0.h never moves them).
 *  (3) tx buffers are used once by L25 (swl25h.txb never released): en0_live_up releases the slots whose tx status L25 already consumed (h->txdone), en0_poll_tx frees a slot at its status.
 * Limits NOT lifted (listed in the sprint report): SWDP_NSLOT = 16 tx buffers (sweet_wlan_data.h, the audit's request-id range; the rx pool is larger since patch 0357), and the DP15 all-ones old word (below).
 *
 * The all-ones old word: swg_d4d_st (sweet_wlan_g2.h:1719) stops the engine with SWG_E_DEAD when the OLD word it reads is 0xFFFFFFFF, for item and payload words alike. A flow-ring item of
 * a BROADCAST frame holds the destination MAC in item bytes 8..11 (swdp_build_txpost), i.e. an all-ones word, so the lap that reuses that slot (every 64 posts) - or any tx buffer
 * whose last payload held an all-ones word (e.g. IP broadcast) - would stop the engine and run swg_undo. Until sweet_wlan_g2.h defines SWG_DP_SHADOW_ONES_OK (= the dead-link check is
 * skipped when the shadow word is all ones too, which the compare right behind it needs anyway) the bus keeps `ff_guard` on: such a plan is REFUSED before the first store with a GAP, the
 * engine stays alive and the ring does not advance. */
#ifndef WLAN_EN0_BUS_H
#define WLAN_EN0_BUS_H

#ifndef WLAN_EN0_H
#error "wlan_en0_bus.h needs wlan_en0.h (struct en0, struct en0_bus) first"
#endif
#ifndef SWEET_WLAN_L25_HS_H
#error "wlan_en0_bus.h needs sweet_wlan_l25_hs.h (struct swl25h) and the engine (sweet_wlan_g2.h) first"
#endif

#define EN0L_R1_MAX     2048u                       /* ring-1 items: swd_rings[1] { "h2d rxpost", 2048, 32 }, xnu patch 0167:185 */
#define EN0L_FLOW_MAX   SWL25_FLOW_DEPTH            /* flow ring items (sweet_wlan_l25.h:24) */
#define EN0L_ONES       0xFFFFFFFFu
#define EN0L_GAP_FF     "WIFI GAP: EN-L plan refused: a word it overwrites is all ones and the DP15 dead-link check (g2.h:1719) would stop the engine; needs SWG_DP_SHADOW_ONES_OK"

struct en0_live {
	struct swg_wx *x;
	struct swl25h *h;
	struct en0 *en;                     /* the ifnet state whose H2D read cursors the bus refreshes (see en0l_h2d_sync) */
	struct swdp_env *env;               /* = &en0.env: the envelope exec hands to swg_dp_exec */
	uint32_t rx_dataoffset;
	uint8_t dead, ff_guard;
	uint32_t execs, refused, failed, ff_refused, bad_reads, last_slot, last_need;
	uint32_t sh_r1[EN0L_R1_MAX * SWDP_ISZ_RXPOST / 4u];        /* old words of ring 1, the whole ring */
	uint32_t sh_fl[EN0L_FLOW_MAX * SWDP_ISZ_TX_EXT / 4u];      /* old words of the flow ring, the whole ring (large enough for the extended item too) */
	uint8_t it3[SWDP_ISZ_TX_CMPLT], it4[SWDP_ISZ_RX_CMPLT];    /* the last item handed out per ring */
	uint8_t rxb[SWL25_BUF_SZ + 4u];                            /* the last rx buffer handed out (+4: whole-word copy) */
	uint32_t blkw[SWL25_BUF_SZ / 4u + 1u];                     /* Part A: the words of one rd_blk call (rx buffer or D2H item) */
	uint32_t blk_reads;
};

static inline void en0l_gap(const char *what, uint32_t a)
{
	char b[160];

	snprintf(b, sizeof b, "%s %u", what, (unsigned)a);
	SWDP_GAP(b);
}

/* latch the bus dead with one GAP; later calls only count */
static inline void en0l_dead(struct en0_live *b, const char *why, uint32_t a)
{
	b->bad_reads++;
	if (!b->dead) {
		b->dead = 1u;
		en0l_gap(why, a);
	}
}

/* ---- H2D read cursors ----
 * wlan_en0.h posts with swdp_ring_post() on COPIES of e->rxpost / e->flow.ring, and that function refuses when (rd + depth - wr - 1) % depth == 0; nothing in wlan_en0.h ever moves
 * `rd` (the dongle's read index, H2D-RD slot of the ring in the G1 block, which swg_dp_room reads inside exec for its own full check). Left alone, the host books would call
 * the flow ring full after depth - 1 = 63 posts and ring 1 after 2047, and the wrap this bus exists for would never happen. The bus therefore refreshes both cursors from the
 * G1 block (swdp_h2d_rd_set: refuses an index outside the ring) at the start of every poll pass (en0_poll_tx's first d2h_wr) and at hand-over. A caller's post copies the cursor
 * before the plan and writes the copy back after it, so a refresh inside exec would be overwritten; the next poll pass is the sync point. A stale cursor can only make the host see
 * LESS room than there is (a burst of more than depth - 1 tx posts without a poll is dropped with the EN6 gap, which is the safe side). */
static inline void en0l_h2d_sync(struct en0_live *b)
{
	struct swg_wx *x = b->x;
	struct en0 *e = b->en;
	uint32_t v = 0u;

	if (b->dead || e == NULL)
		return;
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)] + 4ull * swdp_h2d_slot(SWDP_RING_RXPOST), &v) ||
	    (v == EN0L_ONES) || swdp_h2d_rd_set(&e->rxpost, v)) {
		en0l_dead(b, "WIFI GAP: EN-L H2D read index of ring 1 unreadable, all ones or outside the ring, value", v);
		return;
	}
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)] + 4ull * swdp_h2d_slot(swdp_ringid(e->env.flowid)), &v) ||
	    (v == EN0L_ONES) || swdp_h2d_rd_set(&e->flow.ring, v))
		en0l_dead(b, "WIFI GAP: EN-L H2D read index of the flow ring unreadable, all ones or outside the ring, value", v);
}

/* ---- exec ---- */

/* the accepted plan shapes (see the header comment); NULL = not one of them */
static inline const char *en0l_plan_name(const struct swdp_store *s, uint32_t n)
{
	if (n == 4u && s[0].kind == SWDP_SK_PAYLOAD)
		return "en0 tx";
	if (n >= 3u && n <= SWDP_MAX_STORES_RXB && s[0].kind == SWDP_SK_ITEM && s[0].ring == SWDP_RING_RXPOST) {     /* patch 0357: [ITEM ring 1 x (n - 2), WRIDX, DB]: one item (the old shape) or a batch */
		uint32_t i;

		for (i = 0; i + 2u < n; i++)
			if (s[i].kind != SWDP_SK_ITEM || s[i].ring != SWDP_RING_RXPOST)
				return NULL;
		return s[n - 2u].kind == SWDP_SK_WRIDX && s[n - 1u].kind == SWDP_SK_DB ? (n == 3u ? "en0 rxpost" : "en0 rxpost batch") : NULL;
	}
	if (n == 2u && s[0].kind == SWDP_SK_RDIDX && s[0].ring == SWDP_RING_TXCPL)
		return "en0 consume r3";
	if (n == 2u && s[0].kind == SWDP_SK_RDIDX && s[0].ring == SWDP_RING_RXCPL)
		return "en0 consume r4";
	return NULL;
}

/* 1 when any word the plan overwrites has an all-ones SHADOW (= old) word: swg_d4d_st would stop the engine on it */
static inline int en0l_ff_old(struct en0_live *b, const struct swdp_store *s, uint32_t n)
{
	uint32_t i, k, nw;
	const uint32_t *sh;

	for (i = 0; i < n; i++) {
		if (s[i].kind != SWDP_SK_ITEM && s[i].kind != SWDP_SK_PAYLOAD)
			continue;
		nw = (s[i].len + 3u) / 4u;         /* swg_dp_run: item len / 4 words, payload (len + 3) / 4 words */
		for (k = 0; k < nw; k++) {
			sh = swg_dp_shadow(b->x, b->env, s[i].addr + 4ull * k);
			if (sh != NULL && *sh == EN0L_ONES)
				return 1;
		}
	}
	return 0;
}

static inline int en0l_exec(void *ctx, const struct swdp_store *s, uint32_t n)
{
	struct en0_live *b = ctx;
	const char *nm = en0l_plan_name(s, n);
	int r;

	if (b->dead) {
		b->refused++;
		return -1;                          /* en0_run_plan prints SWDP_GAP_DP15 */
	}
	if (nm == NULL) {
		b->refused++;
		en0l_gap("WIFI GAP: EN-L plan shape not accepted by the en0 bus, stores", n);
		return -2;
	}
	if (b->ff_guard && en0l_ff_old(b, s, n)) {
		b->ff_refused++;
		SWDP_GAP(EN0L_GAP_FF);
		return -3;
	}
	r = swg_dp_exec(b->x, b->env, s, n, nm, 0);
	if (r != 0) {
		b->failed++;                        /* swg_dp_exec printed its WIFI GAP line; the engine soft/hard-stopped */
		return r < 0 ? r : -4;
	}
	b->execs++;
	return 0;
}

/* ---- D2H reads ---- */

static inline uint32_t en0l_d2h_wr(void *ctx, uint32_t ring_id)
{
	struct en0_live *b = ctx;
	struct swg_wx *x = b->x;
	uint32_t v = 0u;

	if (b->dead || ring_id < SWDP_RING_TXCPL || ring_id > SWDP_RING_RXCPL) {
		if (!b->dead)
			en0l_gap("WIFI GAP: EN-L d2h_wr for a ring that is not 3 or 4, ring", ring_id);
		return EN0L_ONES;
	}
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)] + 4ull * swdp_d2h_slot(ring_id), &v)) {
		en0l_dead(b, "WIFI GAP: EN-L D2H write index read failed (bus stopped or block), ring", ring_id);
		return EN0L_ONES;
	}
	if (v == EN0L_ONES)
		en0l_dead(b, "WIFI GAP: EN-L D2H write index reads all ones (dead link), ring", ring_id);
	else if (ring_id == SWDP_RING_TXCPL)
		en0l_h2d_sync(b);                       /* once per poll pass (the first call of en0_poll_tx) and per tx status */
	return v;
}

static inline const uint8_t *en0l_d2h_item(void *ctx, uint32_t ring_id, uint32_t idx)
{
	struct en0_live *b = ctx;
	struct swg_wx *x = b->x;
	uint32_t isz, i, v, depth;
	uint8_t *o;

	if (b->dead)
		return NULL;
	if (ring_id < SWDP_RING_TXCPL || ring_id > SWDP_RING_RXCPL) {
		en0l_gap("WIFI GAP: EN-L d2h_item for a ring that is not 3 or 4, ring", ring_id);
		return NULL;
	}
	isz = ring_id == SWDP_RING_TXCPL ? SWDP_ISZ_TX_CMPLT : SWDP_ISZ_RX_CMPLT;
	o = ring_id == SWDP_RING_TXCPL ? b->it3 : b->it4;
	depth = b->env->depth_d2h[ring_id - 2u];
	if (idx >= depth) {
		en0l_gap("WIFI GAP: EN-L d2h_item index outside the ring, index", idx);
		return NULL;
	}
	if (x->b->rd_blk != NULL) {             /* Part A: the item in one cache pass (= isz / 4 flushing swg_hrd reads) */
		uint32_t ho;

		if (x->stopped || swg_hoff(x, x->in.ring[ring_id] + (uint64_t)idx * isz, isz, &ho) || x->b->rd_blk(x->b->ctx, SWG_SP_HOST, ho, b->blkw, isz / 4u)) {
			en0l_dead(b, "WIFI GAP: EN-L D2H item read failed, ring", ring_id);
			return NULL;
		}
		for (i = 0; i < isz / 4u; i++)
			swdp_p32(o + 4u * i, b->blkw[i]);
		b->blk_reads++;
	} else {
		for (i = 0; i < isz / 4u; i++) {
			if (swg_hrd(x, x->in.ring[ring_id] + (uint64_t)idx * isz + 4ull * i, &v)) {
				en0l_dead(b, "WIFI GAP: EN-L D2H item read failed, ring", ring_id);
				return NULL;
			}
			swdp_p32(o + 4u * i, v);
		}
	}
	if (ring_id == SWDP_RING_RXCPL) {       /* remember how much of that rx buffer rxbuf() has to read: data_offset (+16, 0 = shared offset) + data_len (+14) */
		const uint32_t off = swdp_g16(o + 16) != 0u ? swdp_g16(o + 16) : b->rx_dataoffset;

		b->last_slot = swdp_g32(o + 4) - SWDP_RX_RID;
		b->last_need = off + swdp_g16(o + 14);
	}
	return o;
}

/* the rx buffer of `slot`: [0, need) read from the DATA block (need = the last rx completion's end for that slot, else the whole buffer). On a failed read: a GAP and a zeroed
 * buffer (wlan_en0.h en0_rx_one does not test for NULL; an all-zero frame has ethertype 0 and is dropped by any stack) */
static inline const uint8_t *en0l_rxbuf(void *ctx, uint32_t slot)
{
	struct en0_live *b = ctx;
	struct swg_wx *x = b->x;
	const struct swdp_env *e = b->env;
	uint32_t need = SWL25_BUF_SZ, k, v, base;

	memset(b->rxb, 0, sizeof b->rxb);
	if (b->dead || slot >= swdp_env_rxn(e) || e->buf_sz > SWL25_BUF_SZ) {
		en0l_gap("WIFI GAP: EN-L rx buffer unavailable (bus dead or slot out of range), a zeroed frame is returned, slot", slot);
		return b->rxb;
	}
	if (slot == b->last_slot && b->last_need != 0u && b->last_need <= e->buf_sz)
		need = b->last_need;
	base = (uint32_t)(e->rxbuf.pa - e->data.pa) + slot * e->buf_sz;
	if (x->b->rd_blk != NULL) {             /* Part A: the frame in one cache pass (= one flushing rd per word) */
		if (x->b->rd_blk(x->b->ctx, SWG_SP_DATA, base, b->blkw, (need + 3u) / 4u)) {
			en0l_dead(b, "WIFI GAP: EN-L rx buffer read failed, zeroed frame returned, slot", slot);
			memset(b->rxb, 0, sizeof b->rxb);
			return b->rxb;
		}
		for (k = 0; k < (need + 3u) / 4u; k++)
			swdp_p32(b->rxb + 4u * k, b->blkw[k]);
		b->blk_reads++;
		return b->rxb;
	}
	for (k = 0; k < (need + 3u) / 4u; k++) {
		if (x->b->rd(x->b->ctx, SWG_SP_DATA, base + 4u * k, &v)) {
			en0l_dead(b, "WIFI GAP: EN-L rx buffer read failed, zeroed frame returned, slot", slot);
			memset(b->rxb, 0, sizeof b->rxb);
			return b->rxb;
		}
		swdp_p32(b->rxb + 4u * k, v);
	}
	return b->rxb;
}

/* ---- hand-over from the L25 hook ---- */

static inline int en0l_precheck(struct swg_wx *x, const struct swl25h *h)
{
	if (x->stopped || x->d4dstop) {
		SWDP_GAP("WIFI GAP: EN-L hand-over refused: the engine is stopped");
		return -1;
	}
	if (!h->hs_done) {
		SWDP_GAP("WIFI GAP: EN-L hand-over refused: the 4-way handshake keys are not installed");
		return -1;
	}
	if (!h->l.env.flow_open || h->l.flow.st != SWDP_FLOW_OPEN) {
		SWDP_GAP("WIFI GAP: EN-L hand-over refused: the flow ring is not open");
		return -1;
	}
	if (h->l.env.depth1 > EN0L_R1_MAX || h->l.env.depth_flow > EN0L_FLOW_MAX || h->l.env.flow_isz > SWDP_ISZ_TX_EXT) {
		SWDP_GAP("WIFI GAP: EN-L hand-over refused: ring 1 or the flow ring is deeper than the bus shadow arrays");
		return -1;
	}
	return 0;
}

/* take over the books of L25 (copies: L25 never runs again), install the full-ring shadows, release the tx slots whose status L25 consumed */
static inline void en0l_take(struct en0 *e, struct en0_live *b, struct swg_wx *x, struct swl25h *h)
{
	const struct swdp_env *v = &h->l.env;
	uint32_t i;

	e->env = h->l.env;
	e->flow = h->l.flow;
	e->rxpost = h->l.r1;
	e->txcpl = h->r3;
	e->rxcpl = h->r4;
	e->rxb = h->l.rxb;
	e->txb = h->txb;
	e->rx_dataoffset = x->rxoff;
	for (i = 0; i < SWDP_NSLOT; i++)       /* tx slots whose status L25 already read are free; the others stay in flight until en0_poll_tx sees their status */
		if (e->txb.used[i] && (h->txdone & (1u << i)) != 0u)
			(void)swdp_book_done(&e->txb, SWDP_TX_RID + i, NULL);
	memcpy(b->sh_r1, h->l.sh_r1, sizeof h->l.sh_r1);
	memcpy(b->sh_fl, h->l.sh_fl, sizeof h->l.sh_fl);
	x->dpsh[0] = (struct swg_dp_sh){ v->ring1.pa, v->depth1 * SWDP_ISZ_RXPOST / 4u, b->sh_r1 };
	x->dpsh[1] = (struct swg_dp_sh){ v->flow.pa, v->depth_flow * v->flow_isz / 4u, b->sh_fl };
}

/* L25 has finished (keys installed, flow ring open) in `h` on engine `x`: build en0 on the live state. Order: books and shadows, the bus, ifnet attach (wlan_en0.h en0_attach), link up
 * (+ the optional boot address), rx pool top-up. 0 = en0 is up, -1 = refused (a GAP says why; nothing was stored) */
static inline int en0_live_up(struct en0 *e, struct en0_live *b, struct swg_wx *x, struct swl25h *h,
    const uint8_t mac[6], const char *bootarg, en0_apply_fn apply)
{
	char lb[160];

	if (en0l_precheck(x, h))
		return -1;
	memset(b, 0, sizeof *b);
	memset(e, 0, sizeof *e);
	b->x = x;
	b->h = h;
	b->en = e;
	b->env = &e->env;
	b->rx_dataoffset = x->rxoff;
	b->last_slot = ~0u;
#ifdef SWG_DP_SHADOW_ONES_OK
	b->ff_guard = 0u;
#else
	b->ff_guard = 1u;
	SWDP_GAP("WIFI GAP: EN-L ff_guard on: sweet_wlan_g2.h lacks SWG_DP_SHADOW_ONES_OK, broadcast frames stop at the first lap of their ring slot");
#endif
	en0l_take(e, b, x, h);
	e->bus = (struct en0_bus){ b, en0l_exec, en0l_d2h_wr, en0l_d2h_item, en0l_rxbuf };
	e->bootarg = bootarg;
	e->apply = apply;
	if (en0_attach(e, mac))
		return -1;
	EN0_LOCK();
	en0l_h2d_sync(b);                       /* the dongle's read indexes as of now, before the first post */
	EN0_UNLOCK();
	en0_set_link(e, 1u);                    /* outside the lock: the boot address (SIOCAIFADDR) may make the stack transmit (ARP) through en0_output, which takes the lock itself */
	EN0_LOCK();
	en0_rx_fill(e);
	EN0_UNLOCK();
	snprintf(lb, sizeof lb, "SWEET WLAN: en0 live bus up: rx in flight %u of pool %u, tx in flight %u, ring-1 wr %u, flow wr %u",
	    (unsigned)e->rxb.inflight, (unsigned)swdp_env_rxn(&e->env), (unsigned)e->txb.inflight, (unsigned)e->rxpost.wr, (unsigned)e->flow.ring.wr);
	SWDP_LOG(lb);
	return 0;
}

/* one poll pass with the engine watched: when the engine or the bus has died the link goes down once (GAP) and the flow ring is marked failed so en0_output drops loudly without
 * touching the bus. Returns the D2H items handled. */
static inline uint32_t en0_live_poll(struct en0 *e, struct en0_live *b)
{
	uint32_t n = 0u;

	if (!b->dead && !b->x->d4dstop && !b->x->stopped)
		n = en0_poll(e);
	if ((b->dead || b->x->d4dstop || b->x->stopped || e->cons_fail) && e->flow.st == SWDP_FLOW_OPEN) {
		SWDP_GAP("WIFI GAP: EN-L the engine or the bus stopped: en0 link down, tx refused from now on");
		e->flow.st = SWDP_FLOW_FAILED;
		EN0_IFSTART(e->ifp);                    /* Part A: en0_start sees the flow down and drops what the stack queued (counted, GAP) */
		en0_set_link(e, 0u);
	}
	return n;
}

#endif /* WLAN_EN0_BUS_H */
