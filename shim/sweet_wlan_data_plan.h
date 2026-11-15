/* sweet_wlan_data_plan.h - store plan and envelope audit of the Wi-Fi data path (split out of sweet_wlan_data.h, T-4115, R29; no code changed).
 * Included ONLY by sweet_wlan_data.h (at its end): it uses swdp_* helpers, constants and SWDP_GAP/SWDP_LOG defined there. */
#ifndef SWEET_WLAN_DATA_PLAN_H
#define SWEET_WLAN_DATA_PLAN_H

#ifndef SWEET_WLAN_DATA_H
#error "sweet_wlan_data_plan.h: include the parent header, not this file"
#endif

/* ------------------------------------------------------------------ store plan and envelope audit (what the Opus envelope review reads)
 * Every store the data path makes is one `struct swdp_store`; the msgbuf engine (transport, WIFI GAP D15) executes a plan ONLY after
 * swdp_audit() accepted every store against the envelope built from the G1 host block and the data block. New store classes versus
 * W-D4: (N1) item writes into ring 1 (rxpost) and into the flow ring, (N2) u16 stores into the H2D-WR slots 1 and flowid, (N3) u16 stores
 * into the D2H-RD slots 1 and 2, (N4) doorbell words for ring ids 1, 3, 4 and the flow ring, (N5) writes of rx/tx frame bytes into the
 * data block. Same classes as W-D4 (kept inside the same envelope): the ring-0 item, H2D-WR slot 0, DB0 0xFF00xxxx, D2H-RD slot 0.
 * The audit is STATEFUL (T-d3b0, Opus r1 F1): the envelope carries the host cursors (wr0, wr1, wrf, rd_d2h[]) as they are before the plan, an
 * item may only be written at the ring's current write slot, the index store may only be that slot + 1, and the doorbell only that index.
 * DP15 contract (Opus r2 N1, N5): ring 0 uses the W-D4 engine's single cursor (d4wr, sweet_wlan_g2.h:220) for env.wr0; the transport writes exactly the
 * s.data bytes that were audited, copied once after the audit; the H2D read indexes and D2H write indexes the dongle writes are not in the env yet. */
#define SWDP_DB0_OFF   0x140u          /* PCIe core +0x140: H2D doorbell 0 = PCIH2D_MailBox [R5 pcie_core.h:1030, written at dhd_pcie.c:659-665 while DAR is off; the word format is dhd_msgbuf.c:114-116; SPEC-WIFI W-D4b Q5] */
#define SWDP_MAX_STORES 6u
#define SWDP_MAX_STORES_RXB (SWDP_RXB_MAX + 2u)   /* patch 0357: a batched rx post = up to SWDP_RXB_MAX ring-1 items + the one write index + the one doorbell (every other plan stays <= SWDP_MAX_STORES) */
#define SWDP_CONS_MAX   16u            /* Part A (sprint-wifi 2026-11-09): most D2H items one consume plan moves the read cursor over (DHD updates the read index once per processed batch; ring 2 already consumes up to 16, sweet_wlan_l25_wl.h SWL25W_D2H_BATCH) */
#define SWDP_IDX_SZ    4u              /* rw_index_sz: stride of the DMA index arrays (observed 4, SPEC-WIFI Part E; DHD carries it in rw_index_sz, dhd_msgbuf.c:11914) */

enum swdp_space { SWDP_SP_HOST = 1, SWDP_SP_PCIE = 2 };
enum swdp_sk { SWDP_SK_ITEM = 1, SWDP_SK_PAYLOAD, SWDP_SK_WRIDX, SWDP_SK_RDIDX, SWDP_SK_DB };

struct swdp_store {
	uint8_t space, kind;
	uint16_t len;                  /* bytes written (item size, payload length, 2 for an index, 4 for a doorbell) */
	uint16_t ring;                 /* ring id the store belongs to */
	uint16_t slot;                 /* item slot (ITEM) or index slot (WRIDX/RDIDX) */
	uint64_t addr;                 /* host PA, or PCIe core offset for SWDP_SP_PCIE */
	uint32_t value;                /* index value / doorbell word (not set for ITEM / PAYLOAD) */
	const uint8_t *data;           /* ITEM: the `len` item bytes the transport will write; PAYLOAD: the `len` frame bytes; else unused */
};

struct swdp_range { uint64_t pa; uint64_t len; };
struct swdp_env {
	struct swdp_range g1, data;            /* the two host blocks (G1 block, data block); every range below must lie inside its block */
	struct swdp_range h2d_wr, d2h_rd;      /* the index arrays inside the G1 block */
	struct swdp_range ring0, ring1;        /* ctrl submit (depth0 x 40) and rxpost (depth1 x 32) rings, inside the G1 block */
	struct swdp_range flow, txbuf, rxbuf;  /* the data block: flow ring, tx payload buffers, rx buffers */
	uint32_t depth0, depth1, depth_flow, max_sub;
	uint32_t flowid, flow_isz;
	uint32_t buf_sz;                       /* one rx or tx buffer; txbuf holds SWDP_NSLOT of them, rxbuf rx_slots (patch 0357), slot i at base + i*buf_sz */
	uint32_t idx_sz;                       /* DMA index stride, must be SWDP_IDX_SZ */
	uint32_t flow_open;                    /* the dongle answered the flow-ring create with status 0 (SWDP_FLOW_OPEN): flow-ring posts need it */
	uint32_t wr0, wr1, wrf;                /* host H2D write cursors (item indices) before the plan: ring 0, ring 1, the flow ring */
	uint32_t rd_d2h[3], depth_d2h[3];      /* host D2H read cursors and depths of rings 2..4 */
	uint32_t rx_slots;                     /* patch 0357: rx buffers the rx pool may post (SWDP_NSLOT..SWDP_RX_MAX); rxbuf.len must hold that many; 0 = SWDP_NSLOT (the old envelope) */
};
static inline uint32_t swdp_env_rxn(const struct swdp_env *e) { return e->rx_slots != 0u ? e->rx_slots : SWDP_NSLOT; }

static inline int swdp_in(const struct swdp_range *r, uint64_t a, uint64_t n)
{
	return n != 0u && a >= r->pa && a - r->pa <= r->len && n <= r->len - (a - r->pa);
}

/* 0 = the store is inside the envelope; else a positive reason code (SWDP_A_*) */
#define SWDP_A_SPACE  1
#define SWDP_A_RANGE  2
#define SWDP_A_ALIGN  3
#define SWDP_A_VALUE  4
#define SWDP_A_RING   5
#define SWDP_A_ORDER  6

/* The envelope itself must be self-consistent before anything is compared with it (T-d3b0, Opus r1 F2). 0 = consistent, else SWDP_A_RANGE. */
static inline int swdp_env_ok(const struct swdp_env *e)
{
	const struct swdp_range *rg[7] = { &e->h2d_wr, &e->d2h_rd, &e->ring0, &e->ring1, &e->flow, &e->txbuf, &e->rxbuf };
	uint32_t i, j;
	for (i = 0; i < 7u; i++)
		if (rg[i]->len == 0u || rg[i]->len > ~0ull - rg[i]->pa)
			return SWDP_A_RANGE;
	/* the G1 block and the data block are two host allocations: neither may be empty or wrap, and they must not overlap (Opus r2 C4) */
	if (e->g1.len == 0u || e->g1.len > ~0ull - e->g1.pa || e->data.len == 0u || e->data.len > ~0ull - e->data.pa ||
	    (e->g1.pa + e->g1.len > e->data.pa && e->data.pa + e->data.len > e->g1.pa))
		return SWDP_A_RANGE;
	if (e->flowid < SWDP_FLOWID_FIRST || e->flowid >= e->max_sub || e->max_sub < 4u || swdp_ringid(e->flowid) > 0xFFu)
		return SWDP_A_RANGE;
	if (e->flow_isz != SWDP_ISZ_TX_BASE && e->flow_isz != SWDP_ISZ_TX_EXT)
		return SWDP_A_RANGE;
	if (e->idx_sz != SWDP_IDX_SZ)
		return SWDP_A_RANGE;
	if (e->depth0 < 2u || e->depth0 > 0xFFFFu || e->depth1 < 2u || e->depth1 > 0xFFFFu || e->depth_flow < 2u || e->depth_flow > 0xFFFFu)
		return SWDP_A_RANGE;
	if (e->ring0.len < (uint64_t)e->depth0 * SWDP_ISZ_CTRL_SUB || e->ring1.len < (uint64_t)e->depth1 * SWDP_ISZ_RXPOST ||
	    e->flow.len < (uint64_t)e->depth_flow * e->flow_isz)
		return SWDP_A_RANGE;
	if (e->h2d_wr.len < (uint64_t)SWDP_IDX_SZ * e->max_sub || e->d2h_rd.len < 3u * SWDP_IDX_SZ)
		return SWDP_A_RANGE;
	if (e->buf_sz < SWDP_ETH_MAXFRAME || e->buf_sz > 0xFFFFu || e->txbuf.len < (uint64_t)SWDP_NSLOT * e->buf_sz || e->rxbuf.len < (uint64_t)swdp_env_rxn(e) * e->buf_sz)
		return SWDP_A_RANGE;
	if (e->rx_slots != 0u && (e->rx_slots < SWDP_NSLOT || e->rx_slots > SWDP_RX_MAX))
		return SWDP_A_RANGE;
	if (e->wr0 >= e->depth0 || e->wr1 >= e->depth1 || e->wrf >= e->depth_flow)
		return SWDP_A_RANGE;
	for (i = 0; i < 3u; i++)
		if (e->depth_d2h[i] < 2u || e->depth_d2h[i] > 0xFFFFu || e->rd_d2h[i] >= e->depth_d2h[i])
			return SWDP_A_RANGE;
	if (!swdp_in(&e->g1, e->h2d_wr.pa, e->h2d_wr.len) || !swdp_in(&e->g1, e->d2h_rd.pa, e->d2h_rd.len) ||
	    !swdp_in(&e->g1, e->ring0.pa, e->ring0.len) || !swdp_in(&e->g1, e->ring1.pa, e->ring1.len))
		return SWDP_A_RANGE;
	if (!swdp_in(&e->data, e->flow.pa, e->flow.len) || !swdp_in(&e->data, e->txbuf.pa, e->txbuf.len) || !swdp_in(&e->data, e->rxbuf.pa, e->rxbuf.len))
		return SWDP_A_RANGE;
	for (i = 0; i < 7u; i++)
		for (j = i + 1u; j < 7u; j++)
			if (rg[i]->pa + rg[i]->len > rg[j]->pa && rg[j]->pa + rg[j]->len > rg[i]->pa)
				return SWDP_A_RANGE;
	return 0;
}

static inline uint64_t swdp_item_pa(const uint8_t *p) { return (uint64_t)swdp_g32(p) | ((uint64_t)swdp_g32(p + 4) << 32); }

/* One store against the envelope (location, size, slot, value range). Order and binding to the other stores of the plan are swdp_audit's job. */
static inline int swdp_audit_store(const struct swdp_env *e, const struct swdp_store *s)
{
	uint32_t depth;
	switch (s->kind) {
	case SWDP_SK_ITEM: {
		const struct swdp_range *r; uint32_t isz;
		if (s->space != SWDP_SP_HOST) return SWDP_A_SPACE;
		if (s->ring == 0u) { r = &e->ring0; isz = SWDP_ISZ_CTRL_SUB; depth = e->depth0; }
		else if (s->ring == SWDP_RING_RXPOST) { r = &e->ring1; isz = SWDP_ISZ_RXPOST; depth = e->depth1; }
		else if (s->ring == swdp_ringid(e->flowid)) { r = &e->flow; isz = e->flow_isz; depth = e->depth_flow; }
		else return SWDP_A_RING;
		if (s->len != isz || s->slot >= depth) return SWDP_A_VALUE;
		if (s->addr != r->pa + (uint64_t)s->slot * isz || !swdp_in(r, s->addr, isz)) return SWDP_A_RANGE;
		return 0;
	}
	case SWDP_SK_PAYLOAD:
		if (s->space != SWDP_SP_HOST) return SWDP_A_SPACE;
		if (s->len == 0u || s->len > SWDP_ETH_MAXFRAME - SWDP_ETH_HLEN) return SWDP_A_VALUE;
		if (!swdp_in(&e->txbuf, s->addr, s->len) || e->buf_sz == 0u) return SWDP_A_RANGE;
		/* one payload per tx buffer: it starts at a buffer boundary and stays inside that buffer (no straddling into the next one) */
		return (s->addr - e->txbuf.pa) % e->buf_sz == 0u && s->len <= e->buf_sz ? 0 : SWDP_A_RANGE;
	case SWDP_SK_WRIDX:
		if (s->space != SWDP_SP_HOST) return SWDP_A_SPACE;
		if (s->ring == 0u) depth = e->depth0; else if (s->ring == SWDP_RING_RXPOST) depth = e->depth1;
		else if (s->ring == swdp_ringid(e->flowid)) depth = e->depth_flow; else return SWDP_A_RING;
		if (s->len != 2u || s->slot != swdp_h2d_slot(s->ring) || s->slot >= e->max_sub || s->value >= depth) return SWDP_A_VALUE;
		return s->addr == e->h2d_wr.pa + 4ull * s->slot && swdp_in(&e->h2d_wr, s->addr, 2u) ? 0 : SWDP_A_RANGE;
	case SWDP_SK_RDIDX:
		if (s->space != SWDP_SP_HOST) return SWDP_A_SPACE;
		if (s->ring < 2u || s->ring > SWDP_RING_RXCPL) return SWDP_A_RING;
		if (s->len != 2u || s->slot != swdp_d2h_slot(s->ring) || s->value >= e->depth_d2h[s->ring - 2u]) return SWDP_A_VALUE;
		return s->addr == e->d2h_rd.pa + 4ull * s->slot && swdp_in(&e->d2h_rd, s->addr, 2u) ? 0 : SWDP_A_RANGE;
	case SWDP_SK_DB: {
		uint32_t top = s->value >> 24, rid = (s->value >> 16) & 0xFFu, idx = s->value & 0xFFFFu;
		if (s->space != SWDP_SP_PCIE) return SWDP_A_SPACE;
		if (s->addr != SWDP_DB0_OFF || s->len != 4u) return SWDP_A_RANGE;
		if (top == 0xFFu) {          /* write-index doorbell: H2D rings only */
			if (rid == 0u) depth = e->depth0; else if (rid == SWDP_RING_RXPOST) depth = e->depth1;
			else if (rid == swdp_ringid(e->flowid)) depth = e->depth_flow; else return SWDP_A_RING;
		} else if (top == 0xDDu) {   /* read-index doorbell: D2H rings 2..4, bounded by their depth */
			if (rid < 2u || rid > SWDP_RING_RXCPL) return SWDP_A_RING;
			depth = e->depth_d2h[rid - 2u];
		} else return SWDP_A_VALUE;
		return idx < depth ? 0 : SWDP_A_VALUE;
	}
	default:
		return SWDP_A_VALUE;
	}
}

/* CONTENT audit of an item before it is stored: the buffer addresses in an item are where the dongle's DMA engine reads and WRITES host
 * memory, so they are part of the envelope. ring_id 0: only a flow-create naming OUR flow ring; ring 1: an rxpost whose data buffer is
 * EXACTLY the rx buffer of its request_id slot (SWDP_RX_RID + i -> rxbuf + i*buf_sz) and has no metadata buffer; the flow ring: a txpost whose
 * payload is EXACTLY the tx buffer of its request_id slot (SWDP_TX_RID + i), seg_cnt 1, no metadata. A buffer never straddles a slot and two
 * request_ids never name one buffer (T-d3b0, Opus r1 F3). */
static inline int swdp_audit_item(const struct swdp_env *e, uint32_t ring_id, const uint8_t *it)
{
	uint64_t a;
	uint32_t i, n;
	int r = swdp_env_ok(e);
	if (r) return r;
	if (ring_id == 0u) {
		a = swdp_item_pa(it + 32);
		if (it[0] != SWDP_MT_FLOW_CREATE) return SWDP_A_VALUE;
		/* depth_flow * flow_isz <= flow.len holds by swdp_env_ok */
		return a != e->flow.pa || swdp_g16(it + 22) != e->flowid || swdp_g16(it + 28) != e->depth_flow || swdp_g16(it + 30) != e->flow_isz ? SWDP_A_RANGE : 0;
	}
	if (ring_id == SWDP_RING_RXPOST) {
		a = swdp_item_pa(it + 24); n = swdp_g16(it + 10); i = swdp_g32(it + 4) - SWDP_RX_RID;
		if (it[0] != SWDP_MT_RXBUF_POST) return SWDP_A_VALUE;
		if (swdp_g16(it + 8) != 0u || swdp_g32(it + 16) != 0u || swdp_g32(it + 20) != 0u || i >= swdp_env_rxn(e)) return SWDP_A_VALUE;
		if (a != e->rxbuf.pa + (uint64_t)i * e->buf_sz || n > e->buf_sz) return SWDP_A_RANGE;
		return swdp_in(&e->rxbuf, a, n) ? 0 : SWDP_A_RANGE;
	}
	if (ring_id == swdp_ringid(e->flowid)) {
		a = swdp_item_pa(it + 32); n = swdp_g16(it + 42); i = swdp_g32(it + 4) - SWDP_TX_RID;
		if (it[0] != SWDP_MT_TX_POST || it[23] != 1u || swdp_g16(it + 40) != 0u || swdp_g32(it + 24) != 0u || swdp_g32(it + 28) != 0u || i >= SWDP_NSLOT) return SWDP_A_VALUE;
		if (a != e->txbuf.pa + (uint64_t)i * e->buf_sz || n > e->buf_sz) return SWDP_A_RANGE;
		return swdp_in(&e->txbuf, a, n) ? 0 : SWDP_A_RANGE;
	}
	return SWDP_A_RING;
}

static inline uint32_t swdp_h2d_depth(const struct swdp_env *e, uint32_t ring)
{
	return ring == 0u ? e->depth0 : ring == SWDP_RING_RXPOST ? e->depth1 : ring == swdp_ringid(e->flowid) ? e->depth_flow : 0u;
}
static inline uint32_t swdp_h2d_cur(const struct swdp_env *e, uint32_t ring)
{
	return ring == 0u ? e->wr0 : ring == SWDP_RING_RXPOST ? e->wr1 : e->wrf;
}

/* A plan is ONE operation and is accepted only when ALL of this holds (T-d3b0, Opus r1 F1/F4; the order spec: item/payload -> index -> doorbell):
 *  - the envelope is consistent (swdp_env_ok) and every store passes swdp_audit_store;
 *  - H2D post: [PAYLOAD (flow ring only)], ITEM, WRIDX, DB. The ITEM sits at the ring's current write slot, its bytes pass swdp_audit_item, the
 *    WRIDX is on the same ring with value slot + 1 (mod depth), the DB names that ring and value; the flow-ring item carries the PAYLOAD's address
 *    and length and needs flow_open. Exactly one item, at most one payload, one index;
 *  - flow create: [WRIDX flow ring := 0 (cursor must be 0), ITEM ring 0, WRIDX ring 0, DB], 4 stores, the ITEM a flow-create naming our ring;
 *  - D2H consume: RDIDX (value rd + 1 mod depth), DB; no item or payload;
 *  - nothing after the doorbell; the plan ends with a doorbell. */
static inline int swdp_audit(const struct swdp_env *e, const struct swdp_store *s, uint32_t n)
{
	uint32_t i, k, flowring, item_ring = 0xFFFFu, item_slot = 0u, nitem = 0u, npay = 0u, nidx = 0u;
	const struct swdp_store *pay = 0;
	int r, seen_db = 0, init = 0;
	if (n == 0u || n > SWDP_MAX_STORES_RXB || (n > SWDP_MAX_STORES && !(s[0].kind == SWDP_SK_ITEM && s[0].ring == SWDP_RING_RXPOST))) return SWDP_A_ORDER;   /* only a ring-1 batch may exceed 6 stores */
	r = swdp_env_ok(e);
	if (r) return r;
	flowring = swdp_ringid(e->flowid);
	for (i = 0; i < n; i++) {
		r = swdp_audit_store(e, &s[i]);
		if (r) return r;
		if (seen_db) return SWDP_A_ORDER;                 /* nothing after the doorbell of an operation */
		switch (s[i].kind) {
		case SWDP_SK_PAYLOAD:
			if (nitem || npay++) return SWDP_A_ORDER;
			if (!s[i].data) return SWDP_A_VALUE;
			pay = &s[i];
			break;
		case SWDP_SK_ITEM:
			/* patch 0357: ring 1 (rxpost) may carry a batch: items at the consecutive slots cur, cur + 1, ... (never past the ring end: the slot test below), all before the one index; every other ring exactly one item */
			if (nidx || (nitem && !(s[i].ring == SWDP_RING_RXPOST && item_ring == SWDP_RING_RXPOST))) return SWDP_A_ORDER;
			if (s[i].ring == 0u && (!init || e->flow_open)) return SWDP_A_ORDER;                /* ring 0 carries only a flow create: the 4-store shape, for a ring that is not open (Opus r2 C1) */
			if (s[i].slot != swdp_h2d_cur(e, s[i].ring) + nitem) return SWDP_A_ORDER;          /* the item is written at the ring's write slot (a batch: the n-th item at slot + n), no other */
			nitem++;
			if ((s[i].ring == flowring) != (npay == 1u)) return SWDP_A_ORDER;                   /* a payload goes with a flow-ring item and only with it */
			if (!s[i].data) return SWDP_A_VALUE;
			r = swdp_audit_item(e, s[i].ring, s[i].data);
			if (r) return r;
			for (k = 0; k < i; k++)                                                              /* a batch: no two items name one request id (= one buffer) */
				if (s[k].kind == SWDP_SK_ITEM && s[i].ring == SWDP_RING_RXPOST && swdp_g32(s[k].data + 4) == swdp_g32(s[i].data + 4)) return SWDP_A_VALUE;
			if (s[i].ring == flowring) {
				if (!e->flow_open) return SWDP_A_ORDER;                                           /* the dongle has not accepted the flow ring */
				if (swdp_item_pa(s[i].data + 32) != pay->addr || swdp_g16(s[i].data + 42) != pay->len) return SWDP_A_VALUE;   /* item names this payload */
			}
			item_ring = s[i].ring; item_slot = s[i].slot;
			break;
		case SWDP_SK_WRIDX:
			if (i == 0u && n == 4u && s[0].ring == flowring && s[0].value == 0u && e->wrf == 0u && s[1].kind == SWDP_SK_ITEM && s[1].ring == 0u) {
				init = 1;
				break;
			}   /* flow create: the new ring's own write index := 0 first */
			if (nitem == 0u || nidx++ || s[i].ring != item_ring || s[i].value != (item_slot + 1u) % swdp_h2d_depth(e, s[i].ring))
				return SWDP_A_ORDER;                                                            /* the index names the item just written, nothing else */
			break;
		case SWDP_SK_RDIDX:
			if (nitem || npay || nidx++) return SWDP_A_ORDER;
			k = s[i].ring - 2u;
			if (e->depth_d2h[k] == 0u || s[i].value >= e->depth_d2h[k]) return SWDP_A_VALUE;
			{       /* Part A: the read cursor of ring 3 / 4 moves by 1..SWDP_CONS_MAX items, ring 2 by exactly one (that they were all written by the dongle is the judge's L8 check, against the dongle's write index) */
				const uint32_t step = (s[i].value + e->depth_d2h[k] - e->rd_d2h[k]) % e->depth_d2h[k];

				if (step == 0u || step > (k == 0u ? 1u : SWDP_CONS_MAX)) return SWDP_A_VALUE;       /* ring 2 (k 0) is the engine's control ring: a DP15 plan moves it by exactly one (the engine batches its own, swg_d4d_consume) */
			}
			break;
		case SWDP_SK_DB:
			seen_db = 1;
			if (i == 0u || (s[i - 1u].kind != SWDP_SK_WRIDX && s[i - 1u].kind != SWDP_SK_RDIDX)) return SWDP_A_ORDER;
			if ((s[i].value & 0xFFFFu) != s[i - 1u].value || ((s[i].value >> 16) & 0xFFu) != s[i - 1u].ring) return SWDP_A_VALUE;   /* the doorbell names the ring and index just stored */
			if ((s[i].value >> 24) != (s[i - 1u].kind == SWDP_SK_WRIDX ? 0xFFu : 0xDDu)) return SWDP_A_VALUE;
			break;
		default:
			return SWDP_A_VALUE;
		}
	}
	return seen_db ? 0 : SWDP_A_ORDER;
}

static inline void swdp_st(struct swdp_store *s, int space, int kind, uint32_t len, uint32_t ring, uint32_t slot, uint64_t addr, uint32_t value,
    const uint8_t *data)
{
	s->space = (uint8_t)space; s->kind = (uint8_t)kind; s->len = (uint16_t)len; s->ring = (uint16_t)ring; s->slot = (uint16_t)slot;
	s->addr = addr; s->value = value; s->data = data;
}

/* The H2D post of one item that the caller already built (`item`, isz bytes): item store, write-index store, doorbell. `slot` / `new_wr` come
 * from swdp_ring_post() on the ring `ring_id`; `base` and `isz` describe that ring in host memory. Returns the store count (3). */
static inline uint32_t swdp_plan_post(const struct swdp_env *e, uint32_t ring_id, uint64_t base, uint32_t isz, uint32_t slot, uint32_t new_wr,
    const uint8_t *item, struct swdp_store *o)
{
	swdp_st(&o[0], SWDP_SP_HOST, SWDP_SK_ITEM, isz, ring_id, slot, base + (uint64_t)slot * isz, 0u, item);
	swdp_st(&o[1], SWDP_SP_HOST, SWDP_SK_WRIDX, 2u, ring_id, swdp_h2d_slot(ring_id), e->h2d_wr.pa + 4ull * swdp_h2d_slot(ring_id), new_wr, 0);
	swdp_st(&o[2], SWDP_SP_PCIE, SWDP_SK_DB, 4u, ring_id, 0u, SWDP_DB0_OFF, swdp_db_wr(ring_id, new_wr), 0);
	return 3u;
}
/* patch 0357: n (1..SWDP_RXB_MAX) rx posts as ONE plan, [ITEM x n, WRIDX, DB]: `items` = n consecutive SWDP_ISZ_RXPOST-byte items for the ring-1 slots slot0 .. slot0 + n - 1 (never past the ring end), new_wr =
 * slot0 + n. One index store and ONE doorbell for the batch (Linux: brcmf_commonring_write_complete after the loop, msgbuf.c:974-975). Returns the store count (n + 2). */
static inline uint32_t swdp_plan_rxpost_n(const struct swdp_env *e, uint32_t slot0, uint32_t n, uint32_t new_wr, const uint8_t *items, struct swdp_store *o)
{
	uint32_t j;

	for (j = 0; j < n; j++)
		swdp_st(&o[j], SWDP_SP_HOST, SWDP_SK_ITEM, SWDP_ISZ_RXPOST, SWDP_RING_RXPOST, slot0 + j, e->ring1.pa + (uint64_t)(slot0 + j) * SWDP_ISZ_RXPOST, 0u, items + (size_t)j * SWDP_ISZ_RXPOST);
	swdp_st(&o[n], SWDP_SP_HOST, SWDP_SK_WRIDX, 2u, SWDP_RING_RXPOST, swdp_h2d_slot(SWDP_RING_RXPOST), e->h2d_wr.pa + 4ull * swdp_h2d_slot(SWDP_RING_RXPOST), new_wr, 0);
	swdp_st(&o[n + 1u], SWDP_SP_PCIE, SWDP_SK_DB, 4u, SWDP_RING_RXPOST, 0u, SWDP_DB0_OFF, swdp_db_wr(SWDP_RING_RXPOST, new_wr), 0);
	return n + 2u;
}
/* Flow create: the new flow ring's own write index is stored 0 first (DHD does, dhd_msgbuf.c:12523-12527), then the ring-0 post of `item`. 4 stores. */
static inline uint32_t swdp_plan_flow_create(const struct swdp_env *e, uint32_t slot0, uint32_t new_wr0, const uint8_t *item, struct swdp_store *o)
{
	swdp_st(&o[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2u, swdp_ringid(e->flowid), e->flowid, e->h2d_wr.pa + 4ull * e->flowid, 0u, 0);
	return 1u + swdp_plan_post(e, 0u, e->ring0.pa, SWDP_ISZ_CTRL_SUB, slot0, new_wr0, item, o + 1);
}
/* TX: the `payload` bytes into the tx buffer `txa`, then the flow-ring post of `item`. 4 stores. */
static inline uint32_t swdp_plan_tx(const struct swdp_env *e, uint64_t txa, const uint8_t *payload, uint32_t payload_len, uint32_t slot, uint32_t new_wr,
    const uint8_t *item, struct swdp_store *o)
{
	swdp_st(&o[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, payload_len, swdp_ringid(e->flowid), 0u, txa, 0u, payload);
	return 1u + swdp_plan_post(e, swdp_ringid(e->flowid), e->flow.pa, e->flow_isz, slot, new_wr, item, o + 1);
}
/* D2H consume of one item on ring 2..4: read-index store then doorbell. 2 stores. */
static inline uint32_t swdp_plan_consume(const struct swdp_env *e, uint32_t ring_id, uint32_t new_rd, struct swdp_store *o)
{
	swdp_st(&o[0], SWDP_SP_HOST, SWDP_SK_RDIDX, 2u, ring_id, swdp_d2h_slot(ring_id), e->d2h_rd.pa + 4ull * swdp_d2h_slot(ring_id), new_rd, 0);
	swdp_st(&o[1], SWDP_SP_PCIE, SWDP_SK_DB, 4u, ring_id, 0u, SWDP_DB0_OFF, swdp_db_rd(ring_id, new_rd), 0);
	return 2u;
}


#endif /* SWEET_WLAN_DATA_PLAN_H */
