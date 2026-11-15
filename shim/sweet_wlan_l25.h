/*
 * L25 data-path steps of the W-D4e engine (T-38cc; SPEC-WIFI Part E TRANSPORT block s.1-s.2): the data block envelope (swl25_env), the flow-ring create and its
 * CREATE_CMPLT (swl25_flow), the 16 rx buffer posts (swl25_rxposts). Every store goes through swg_dp_exec (DP15); nothing here writes the bus itself.
 *
 * Include order in the includer: sweet_wlan_data.h (with SWDP_LOG/SWDP_GAP), then sweet_wlan_g2.h, then this file. NOT in this slice (open, ticket T-38cc Log): the rx
 * poll of rings 3/4 with swj_rx (M1..M4, H6/H7), the key installs H8-H10 and L26 (ARP/ping). The glue does not allocate the data block yet: struct swg_in.data stays 0 and
 * swl25_env then prints `WIFI GAP: L25 not run: ...` (rule 8), so a boot at sweetwlan=25 stops loudly instead of pretending.
 *
 * Data block layout [decision, TRANSPORT s.1 item 1 "buffer ranges from the data block the glue allocated"; the offsets are this file's, the DP15 host test uses the same]:
 *   +0x0000 flow ring (SWL25_FLOW_DEPTH items of the base tx item size 48 B), +0x4000 16 tx buffers of SWL25_BUF_SZ, +0xC000 the rx buffers of SWL25_BUF_SZ; total SWL25_DATA_MIN for 16 rx buffers.
 *   Patch 0357: the glue may allocate more (up to SWL25_DATA_MAX = 255 rx buffers); the rx pool the engine posts is chosen by swdp_rx_pick in swl25_env (<= the buffers the block holds).
 */
#ifndef SWEET_WLAN_L25_H
#define SWEET_WLAN_L25_H

#ifndef SWEET_WLAN_DATA_H
#error "sweet_wlan_l25.h needs sweet_wlan_data.h and sweet_wlan_g2.h first"
#endif

_Static_assert(SWDP_RXFW_UNREAD == 0 && SWDP_RXFW_OK == 1 && SWDP_RXFW_FAIL == 2, "sweet_wlan_g2.h swg_e writes the rx firmware state as 0/1/2");

#define SWL25_FLOW_DEPTH 64u            /* flow ring items (DHD default for a station ring is larger; one ring, 16 posts per run: DP10) */
#define SWL25_BUF_SZ     2048u          /* one rx or tx buffer (>= SWDP_ETH_MAXFRAME, swdp_env_ok) */
#define SWL25_TX_OFF     0x4000u
#define SWL25_RX_OFF     0xC000u
#define SWL25_DATA_MIN   0x14000u
#define SWL25_DATA_MAX   (SWL25_RX_OFF + SWDP_RX_MAX * SWL25_BUF_SZ)   /* patch 0357: the DATA block with the largest rx pool (255 buffers, Linux's default max_rxbufpost) */
#define SWL25_SHADOW_ITEMS 16u          /* the shadow covers the first 16 items of ring 1 and of the flow ring (= the post count of L25/L26); a later slot has no shadow and stops the engine (SWG_E_ADDR) */
#define SWL25_MAXSUB     43u            /* H2D index slots of the observed shared structure (max_submission_queues 0x2b, SWG_RI_QUEUES) */
#define SWL25_FLOWID     SWDP_FLOWID_FIRST
#define SWL25_RXPOSTS    16u
#define SWL25_R1_SHADOW  256u           /* sprint 2026-11-08: ring-1 items with a shadow: the 16 first posts + 240 re-posts of the same 16 buffers (each rx completion re-posts its buffer); the 257th post is a loud refusal */
#define SWL25_CREATE_MS  1000u          /* wait for CREATE_CMPLT [decision, no source] */

/* What the glue (or the host test) hands the L25/L26 engine through struct swg_wx.l25c; it is NOT built by this slice's glue yet (the pointer stays NULL there: swl25_run then prints `WIFI GAP: L25 not run`).
 * rsne_*: the RSNE of the target AP's beacon / probe response; rsne_assoc is the one M2 must carry. Source decision [guess, SPEC-WIFI U-g]: both come from the AP's scan record (bss_info IE 48); the firmware's
 * association request RSNE is NOT read, so rsne_assoc == rsne_beacon until the first run compares them; swj_ctx_init prints SWJ_GAP_X_RSNE for it. All-zero mac, bssid, ip or gw are refused (never a default). */
struct swl25_cfg {
	uint8_t pmk[32], mac[6], bssid[6], ip[4], gw[4];
	uint8_t rsne_assoc[64], rsne_beacon[64];
	uint32_t rsne_assoc_len, rsne_beacon_len;
};

struct swl25 {
	struct swdp_env env;
	struct swdp_flow flow;          /* the flow ring books (rings: write cursor, epoch counter) */
	struct swdp_ring r1;            /* ring 1 (rxpost) books */
	struct swdp_book rxb;           /* rx buffers in flight */
	uint32_t sh_r1[SWL25_R1_SHADOW * SWDP_ISZ_RXPOST / 4u];
	uint32_t sh_fl[SWL25_SHADOW_ITEMS * SWDP_ISZ_TX_BASE / 4u];
	uint32_t sh_tx[SWDP_NSLOT * SWL25_BUF_SZ / 4u];
	uint8_t mac[6], gwmac[6];       /* station and AP MAC (the glue fills them from the join result) */
};

/* A refusal before the first store: WIFI GAP + the engine soft-stops. -SWG_E_ORDER */
static int
swl25_refuse(struct swg_wx *x, const char *why)
{
	char gb[64];     /* swl_gap keeps 63 chars of `what`: 13 + 50 fit, the full text is in the printf below (gcc -Wformat-truncation saw the 80 B buffer flow into the 64 B table field once the test TU changed) */

	snprintf(gb, sizeof gb, "L25 not run: %.50s", why);
	swl_gap("l25", gb, "high");
	printf("WIFI GAP: L25 not run: %s\n", why);
	x->d4dstop = 1u;
	return -SWG_E_ORDER;
}

/* Build the envelope from what the glue handed in (struct swg_in: blk/idx/ring of the G1 block, data/data_len of the DATA block) and install the shadow regions.
 * Refuses (GAP, nothing stored) a missing or too small data block, a data block that overlaps the G1 block, and an env swdp_env_ok rejects. 0 = ok */
static int
swl25_env(struct swg_wx *x, struct swl25 *l)
{
	struct swdp_env *e = &l->env;
	const uint64_t d = x->in.data;
	int r;

	memset(e, 0, sizeof *e);
	if (d == 0u || x->in.data_len == 0u) {
		return swl25_refuse(x, "no DATA block was allocated by the glue");
	}
	if (x->in.data_len < SWL25_DATA_MIN || (d & 0xFFFu) != 0u) {
		return swl25_refuse(x, "DATA block too small or not page aligned");
	}
	if (d + x->in.data_len < d || (d < x->in.blk + x->in.blk_len && d + x->in.data_len > x->in.blk)) {
		return swl25_refuse(x, "DATA block overlaps the G1 block");
	}
	e->g1 = (struct swdp_range){ x->in.blk, x->in.blk_len };
	e->data = (struct swdp_range){ d, x->in.data_len };
	e->h2d_wr = (struct swdp_range){ x->in.idx[SWG_IDX(SWD_IDX_H2D_W)], (uint64_t)SWDP_IDX_SZ * SWL25_MAXSUB };
	e->d2h_rd = (struct swdp_range){ x->in.idx[SWG_IDX(SWD_IDX_D2H_R)], 3u * SWDP_IDX_SZ };
	e->ring0 = (struct swdp_range){ x->in.ring[0], (uint64_t)swd_rings[0].items * SWDP_ISZ_CTRL_SUB };
	e->ring1 = (struct swdp_range){ x->in.ring[1], (uint64_t)swd_rings[1].items * SWDP_ISZ_RXPOST };
	e->flow = (struct swdp_range){ d, (uint64_t)SWL25_FLOW_DEPTH * SWDP_ISZ_TX_BASE };
	e->txbuf = (struct swdp_range){ d + SWL25_TX_OFF, (uint64_t)SWDP_NSLOT * SWL25_BUF_SZ };
	e->depth0 = swd_rings[0].items;
	e->depth1 = swd_rings[1].items;
	{       /* patch 0357: the rx pool: firmware max_rxbufpost (read at the D4e entry, x->rxfw) or the boot-arg, cut to the ring and to what the DATA block holds */
		struct swdp_rxsel sel;
		const uint32_t cap = (x->in.data_len - SWL25_RX_OFF) / SWL25_BUF_SZ;
		char lb[320];

		swdp_rx_pick(&sel, x->rxfw_state, x->rxfw, x->in.rxarg_set, x->in.rxarg, e->depth1, cap);
		snprintf(lb, sizeof lb, "SWEET WLAN: rx pool: firmware max_rxbufpost %s %u, boot-arg %s, ring-1 depth %u, block holds %u, ceiling %u, chosen %u from %s",
		    x->rxfw_state == SWDP_RXFW_OK ? "read" : x->rxfw_state == SWDP_RXFW_FAIL ? "UNREADABLE" : "UNREAD", x->rxfw, x->in.rxarg_set ? "set" : "absent",
		    (unsigned)e->depth1, (unsigned)cap, (unsigned)sel.ceil, (unsigned)sel.n, sel.src == SWDP_RXSRC_ARG ? "sweetwlanrxpost" : sel.src == SWDP_RXSRC_FW ? "the firmware value" : "the default 16");
		SWDP_LOG(lb);
		if (sel.notes != 0u) {
			snprintf(lb, sizeof lb, "WIFI GAP: rx pool adjusted (notes %#x: 1 fw0->255, 2 fw>255, 4 fw<16 keeps 16, 8 firmware value unusable keeps 16, 0x10 boot-arg clamped 16..255, 0x20 ring, 0x40 block, 0x80 boot-arg above the firmware value): chosen %u",
			    (unsigned)sel.notes, (unsigned)sel.n);
			SWDP_GAP(lb);
		}
		e->rxbuf = (struct swdp_range){ d + SWL25_RX_OFF, (uint64_t)sel.n * SWL25_BUF_SZ };
		e->rx_slots = sel.n;
	}
	e->depth_flow = SWL25_FLOW_DEPTH;
	e->max_sub = SWL25_MAXSUB;
	e->flowid = SWL25_FLOWID;
	e->flow_isz = SWDP_ISZ_TX_BASE;
	e->buf_sz = SWL25_BUF_SZ;
	e->idx_sz = SWDP_IDX_SZ;
	e->depth_d2h[0] = SWG_R2_DEPTH;
	e->depth_d2h[1] = swd_rings[3].items;
	e->depth_d2h[2] = swd_rings[4].items;
	e->wr0 = x->d4wr;
	e->rd_d2h[0] = x->d4rd % SWG_R2_DEPTH;
	r = swdp_env_ok(e);
	if (r != 0) {
		char b[40];

		snprintf(b, sizeof b, "env refused by the audit code=%s", swg_dp_aname[r]);
		return swl25_refuse(x, b);
	}
	memset(l->sh_r1, 0, sizeof l->sh_r1);
	memset(l->sh_fl, 0, sizeof l->sh_fl);
	memset(l->sh_tx, 0, sizeof l->sh_tx);
	x->dpsh[0] = (struct swg_dp_sh){ e->ring1.pa, sizeof l->sh_r1 / 4u, l->sh_r1 };
	x->dpsh[1] = (struct swg_dp_sh){ e->flow.pa, sizeof l->sh_fl / 4u, l->sh_fl };
	x->dpsh[2] = (struct swg_dp_sh){ e->txbuf.pa, sizeof l->sh_tx / 4u, l->sh_tx };
	swdp_ring_init(&l->r1, e->depth1);
	memset(&l->flow, 0, sizeof l->flow);
	swdp_book_init(&l->rxb, SWDP_RX_RID);
	return 0;
}

/* read one ring-2 item (absolute k) and run the DP15 consume of it (index store + doorbell). 0 = ok, negative = stopped */
static int
swl25_r2_item(struct swg_wx *x, struct swl25 *l, uint8_t it[SWDP_ISZ_CTRL_CMPLT])
{
	struct swdp_store s[SWDP_MAX_STORES];
	uint32_t n;
	int ex;

	ex = swg_d4d_r2(x, x->d4rd, it);
	if (ex != 0) {
		return ex;
	}
	ex = swdp_d2h_check(it, SWDP_ISZ_CTRL_CMPLT, x->d4rd + SWDP_EPOCH_INIT);        /* ring-2 item k carries epoch (254 + k) % 253 (gd_put, swdp_ring seq) */
	if (ex != 0) {
		printf("WIFI GAP: L25 ring-2 item %u refused: check code %d (epoch/size/xor)\n", x->d4rd, ex);
		swl_gap("l25", "ring-2 item check failed", "high");
		x->d4dstop = 1u;
		return -SWG_E_ORDER;
	}
	l->env.wr0 = x->d4wr;
	n = swdp_plan_consume(&l->env, 2u, (x->d4rd + 1u) % SWG_R2_DEPTH, s);
	return swg_dp_exec(x, &l->env, s, n, "consume ring 2", 0);
}

/* DP1: flow-ring create (ring-0 post through DP15), wait for CREATE_CMPLT on ring 2 (<= SWL25_CREATE_MS), swdp_flow_on_create_cmpl. 0 = ok or soft stop (flow.st says which) */
static int
swl25_flow(struct swg_wx *x, struct swl25 *l, const uint8_t da[6])
{
	struct swdp_store s[SWDP_MAX_STORES];
	struct swdp_cmpl c;
	uint8_t it[SWDP_ISZ_CTRL_SUB], cmp[SWDP_ISZ_CTRL_CMPLT];
	uint32_t slot = x->d4wr, nwr = swg_r0_next(x), w = 0u, waited = 0u, n;
	uint8_t ep = swg_ep0(x);       /* sprint 2026-11-08: the absolute ring-0 count, the slot wraps */
	int ex;

	if (swdp_flow_begin(&l->flow, l->env.flowid, l->env.max_sub, l->env.depth_flow, l->env.flow_isz) != 0) {
		return swl25_refuse(x, "flow create refused by the books");
	}
	if (swdp_build_flow_create(it, ep, 0u, l->env.flowid, 0u, da, l->mac, l->env.flow.pa, l->env.depth_flow, l->env.flow_isz) != 0) {
		return swl25_refuse(x, "flow create item not buildable");
	}
	l->env.wr0 = x->d4wr;
	n = swdp_plan_flow_create(&l->env, slot, nwr, it, s);
	ex = swg_dp_exec(x, &l->env, s, n, "flow create", 0);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	ex = swg_d4d_waitn(x, 1u, SWL25_CREATE_MS / SWG_POLL_MS, &w, &waited);
	if (ex != 0) {
		return ex;
	}
	if (w <= x->d4rd) {
		printf("WIFI GAP: L25 flow create: no CREATE_CMPLT in %u ms\n", waited);
		swl_gap("l25", "flow create not answered", "high");
		x->d4dstop = 1u;
		return 0;
	}
	ex = swl25_r2_item(x, l, cmp);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (swdp_parse_flow_cmpl(cmp, &c) != 0 || swdp_flow_on_create_cmpl(&l->flow, &c) != 0) {
		printf("WIFI GAP: L25 flow create refused by the dongle (type %u status %d)\n", c.type, (int)(int16_t)c.status);
		swl_gap("l25", "flow create refused", "high");
		x->d4dstop = 1u;
		return 0;
	}
	l->env.flow_open = 1u;
	return 0;
}

/* DP3: SWL25_RXPOSTS rx buffer posts on ring 1 (one DP15 plan each; request id from the book). 0 = ok or soft stop */
static int
swl25_rxposts(struct swg_wx *x, struct swl25 *l)
{
	struct swdp_store s[SWDP_MAX_STORES];
	uint8_t it[SWDP_ISZ_RXPOST], ep;
	uint32_t i, slot, rid, n;
	int ex;
	char nm[24];

	for (i = 0; i < SWL25_RXPOSTS; i++) {
		if (l->r1.wr >= SWL25_R1_SHADOW) {
			return swl25_refuse(x, "rx post: the ring-1 shadow (256 posts) is used up");
		}
		rid = swdp_book_take(&l->rxb, SWL25_BUF_SZ);
		if (rid == 0u || swdp_ring_post(&l->r1, &slot, &ep) != 0) {
			return swl25_refuse(x, "rx post: no free request id or ring 1 full");
		}
		if (swdp_build_rxpost(it, ep, rid, l->env.rxbuf.pa + (uint64_t)(rid - SWDP_RX_RID) * SWL25_BUF_SZ, SWL25_BUF_SZ) != 0) {
			return swl25_refuse(x, "rx post item not buildable");
		}
		l->env.wr1 = slot;
		n = swdp_plan_post(&l->env, SWDP_RING_RXPOST, l->env.ring1.pa, SWDP_ISZ_RXPOST, slot, l->r1.wr, it, s);
		snprintf(nm, sizeof nm, "rxpost %u", i);
		ex = swg_dp_exec(x, &l->env, s, n, nm, 0);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		l->env.wr1 = l->r1.wr;
	}
	printf("SWEET WLAN: L25 rx posts done: %u buffers on ring 1\n", SWL25_RXPOSTS);
	return 0;
}

/* a refused rx re-post: loud gap, soft stop (0 with x->d4dstop) */
static int
swl25_rxgap(struct swg_wx *x, const char *why)
{
	swl_gap("l25", "L25 rx buffers: re-post refused", "high");
	printf("WIFI GAP: L25 rx buffers: re-post refused: %s\n", why);
	x->d4dstop = 1u;
	return 0;
}

/* sprint 2026-11-08: re-post ONE rx buffer (the one its completion just freed in the book) at the next ring-1 slot, the same DP15 plan as the first posts. 0 = ok or soft stop (a refusal is a loud gap) */
static int
swl25_rxrepost(struct swg_wx *x, struct swl25 *l)
{
	struct swdp_store s[SWDP_MAX_STORES];
	uint8_t it[SWDP_ISZ_RXPOST], ep;
	uint32_t slot, rid, n;
	int ex;

	if (l->r1.wr >= SWL25_R1_SHADOW) {
		return swl25_rxgap(x, "the ring-1 shadow (256 posts) is used up");
	}
	rid = swdp_book_take(&l->rxb, SWL25_BUF_SZ);
	if (rid == 0u || swdp_ring_post(&l->r1, &slot, &ep) != 0) {
		return swl25_rxgap(x, "no free request id or ring 1 full");
	}
	if (swdp_build_rxpost(it, ep, rid, l->env.rxbuf.pa + (uint64_t)(rid - SWDP_RX_RID) * SWL25_BUF_SZ, SWL25_BUF_SZ) != 0) {
		return swl25_rxgap(x, "item not buildable");
	}
	l->env.wr1 = slot;
	n = swdp_plan_post(&l->env, SWDP_RING_RXPOST, l->env.ring1.pa, SWDP_ISZ_RXPOST, slot, l->r1.wr, it, s);
	ex = swg_dp_exec(x, &l->env, s, n, "rx re-post", 0);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	l->env.wr1 = l->r1.wr;
	return 0;
}

#endif /* SWEET_WLAN_L25_H */
