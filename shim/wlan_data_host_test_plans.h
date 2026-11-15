/* wlan_data_host_test_plans.h - store-plan, envelope-audit and R2 test groups of wlan_data_host_test.c (split out, T-4115, R29; no code changed).
 * Included ONLY by wlan_data_host_test.c, after its shared helpers (CHECK, fails, MAC/GWM/IPA/GWI, g_log, lg): it is not a standalone translation unit. */
#ifndef WLAN_DATA_HOST_TEST_PLANS_H
#define WLAN_DATA_HOST_TEST_PLANS_H

static struct swdp_env mkenv(void)
{
	struct swdp_env e;
	memset(&e, 0, sizeof e);
	e.g1 = (struct swdp_range){ 0x880000000ull, 0x100000 };
	e.data = (struct swdp_range){ 0x880100000ull, 0x20000 };
	e.h2d_wr = (struct swdp_range){ 0x880001000ull, 43 * 4 };
	e.d2h_rd = (struct swdp_range){ 0x880002000ull, 3 * 4 };
	e.ring0 = (struct swdp_range){ 0x880010000ull, 128 * 40 };
	e.ring1 = (struct swdp_range){ 0x880020000ull, 2048 * 32 };
	e.flow = (struct swdp_range){ 0x880100000ull, 64 * 56 };
	e.txbuf = (struct swdp_range){ 0x880104000ull, 16 * 2048 };
	e.rxbuf = (struct swdp_range){ 0x88010C000ull, 16 * 2048 };
	e.depth0 = 128; e.depth1 = 2048; e.depth_flow = 64; e.max_sub = 43; e.flowid = 2; e.flow_isz = 56;
	e.buf_sz = 2048; e.idx_sz = 4; e.flow_open = 1;
	e.wr0 = 5; e.wr1 = 9; e.wrf = 0;
	e.depth_d2h[0] = 64; e.depth_d2h[1] = 256; e.depth_d2h[2] = 256;
	e.rd_d2h[0] = 0; e.rd_d2h[1] = 6; e.rd_d2h[2] = 8;
	return e;
}

/* item and plan builders: the item bytes live in statics, a plan points at them (swdp_store.data) */
static uint8_t g_it[64], g_fr[1514], g_pay[1514];
static void mk_fc(const struct swdp_env *e, uint8_t *it) { swdp_build_flow_create(it, 1, 0, e->flowid, 0, GWM, MAC, e->flow.pa, e->depth_flow, e->flow_isz); }
static void mk_rx(const struct swdp_env *e, uint8_t *it, uint32_t slot) { swdp_build_rxpost(it, 1, SWDP_RX_RID + slot, e->rxbuf.pa + (uint64_t)slot * e->buf_sz, e->buf_sz); }
static void mk_tx(const struct swdp_env *e, uint8_t *it, uint32_t slot, uint32_t frame_len)
{
	swdp_build_txpost(it, e->flow_isz, 0, SWDP_TX_RID + slot, g_fr, frame_len, 0, e->txbuf.pa + (uint64_t)slot * e->buf_sz);
}
static uint32_t pl_fc(const struct swdp_env *e, struct swdp_store *o) { mk_fc(e, g_it); return swdp_plan_flow_create(e, e->wr0, (e->wr0 + 1) % e->depth0, g_it, o); }
static uint32_t pl_rx(const struct swdp_env *e, struct swdp_store *o, uint32_t buf)
{
	mk_rx(e, g_it, buf);
	return swdp_plan_post(e, 1, e->ring1.pa, 32, e->wr1, (e->wr1 + 1) % e->depth1, g_it, o);
}
static uint32_t pl_tx(const struct swdp_env *e, struct swdp_store *o, uint32_t buf)
{
	mk_tx(e, g_it, buf, 60);
	return swdp_plan_tx(e, e->txbuf.pa + (uint64_t)buf * e->buf_sz, g_pay, 46, e->wrf, (e->wrf + 1) % e->depth_flow, g_it, o);
}

/* ---- patch 0357: the rx pool choice, the refill rule, and the batched rx post plan (audit) ---- */
static uint8_t g_rb[SWDP_RXB_MAX + 4u][SWDP_ISZ_RXPOST];
static uint32_t pl_rxb(const struct swdp_env *e, struct swdp_store *o, uint32_t n, uint32_t first_buf)       /* n items at wr1 .., buffers first_buf .. */
{
	uint32_t j;
	for (j = 0; j < n; j++)
		swdp_build_rxpost(g_rb[j], (uint8_t)((254u + e->wr1 + j) % 253u), SWDP_RX_RID + first_buf + j, e->rxbuf.pa + (uint64_t)(first_buf + j) * e->buf_sz, e->buf_sz);
	return swdp_plan_rxpost_n(e, e->wr1, n, (e->wr1 + n) % e->depth1, &g_rb[0][0], o);
}
static void test_rxpool(void)
{
	struct swdp_rxsel s;
	struct swdp_env e = mkenv();
	struct swdp_store st[SWDP_MAX_STORES_RXB + 4u], t[SWDP_MAX_STORES_RXB + 4u];
	uint32_t n, i, ok;

	/* ---- swdp_rx_pick: (fw state, fw value, has_arg, arg, ring depth, block capacity) -> count and notes ---- */
#define PICK(fs, fw, ha, ar, rd, cp) swdp_rx_pick(&s, (fs), (fw), (ha), (ar), (rd), (cp))
	PICK(SWDP_RXFW_OK, 255, 0, 0, 2048, 255); CHECK(s.n == 255 && s.src == SWDP_RXSRC_FW && s.notes == 0 && s.ceil == 255);
	PICK(SWDP_RXFW_OK, 64, 0, 0, 2048, 255);  CHECK(s.n == 64 && s.notes == 0);
	PICK(SWDP_RXFW_OK, 16, 0, 0, 2048, 255);  CHECK(s.n == 16 && s.notes == 0);
	PICK(SWDP_RXFW_OK, 0, 0, 0, 2048, 255);   CHECK(s.n == 255 && s.notes == SWDP_RXN_FWZERO);                         /* Linux: 0 -> 255 */
	PICK(SWDP_RXFW_OK, 1000, 0, 0, 2048, 255); CHECK(s.n == 255 && s.notes == SWDP_RXN_FWCLAMP);
	PICK(SWDP_RXFW_OK, 8, 0, 0, 2048, 255);   CHECK(s.n == 16 && s.notes == SWDP_RXN_FWLOW);                           /* below today's 16: 16 kept */
	PICK(SWDP_RXFW_FAIL, 0, 0, 0, 2048, 255); CHECK(s.n == 16 && s.src == SWDP_RXSRC_DEFAULT && s.notes == SWDP_RXN_FWFAIL);
	PICK(SWDP_RXFW_UNREAD, 77, 0, 0, 2048, 255); CHECK(s.n == 16 && s.notes == SWDP_RXN_FWFAIL);                       /* never read: 16, loud */
	PICK(SWDP_RXFW_OK, 0xFFFF, 0, 0, 2048, 255); CHECK(s.n == 16 && s.notes == SWDP_RXN_FWFAIL);                       /* all ones = a dead link, not a count */
	PICK(SWDP_RXFW_OK, 255, 1, 16, 2048, 255); CHECK(s.n == 16 && s.src == SWDP_RXSRC_ARG && s.notes == 0);            /* sweetwlanrxpost=16 = today's pool */
	PICK(SWDP_RXFW_OK, 255, 1, 100, 2048, 255); CHECK(s.n == 100 && s.src == SWDP_RXSRC_ARG);                          /* the boot-arg beats the firmware value */
	PICK(SWDP_RXFW_FAIL, 0, 1, 100, 2048, 255); CHECK(s.n == 100 && s.notes == 0);                                      /* ... and a failed read */
	PICK(SWDP_RXFW_OK, 255, 1, 0, 2048, 255); CHECK(s.n == 16 && s.notes == SWDP_RXN_ARGCLAMP);                        /* present with 0: clamped, loud */
	PICK(SWDP_RXFW_OK, 255, 1, 15, 2048, 255); CHECK(s.n == 16 && s.notes == SWDP_RXN_ARGCLAMP);
	PICK(SWDP_RXFW_OK, 255, 1, 300, 2048, 255); CHECK(s.n == 255 && s.notes == SWDP_RXN_ARGCLAMP);
	PICK(SWDP_RXFW_OK, 255, 0, 0, 2048, 16);  CHECK(s.n == 16 && s.notes == SWDP_RXN_BLOCK && s.ceil == 16);           /* the block fell back to 16 buffers */
	PICK(SWDP_RXFW_OK, 255, 1, 200, 2048, 100); CHECK(s.n == 100 && s.notes == SWDP_RXN_BLOCK);
	PICK(SWDP_RXFW_OK, 255, 0, 0, 100, 255);  CHECK(s.n == 99 && s.notes == SWDP_RXN_RING && s.ceil == 99);            /* a ring holds depth - 1 */
	PICK(SWDP_RXFW_OK, 255, 0, 0, 256, 255);  CHECK(s.n == 255 && s.notes == 0);                                       /* 255 <= 256 - 1 */
	PICK(SWDP_RXFW_OK, 255, 0, 0, 255, 255);  CHECK(s.n == 254 && s.notes == SWDP_RXN_RING);
	PICK(SWDP_RXFW_OK, 64, 1, 255, 2048, 255); CHECK(s.n == 64 && s.notes == SWDP_RXN_ARGFW);                          /* F3: the boot-arg never goes above the firmware value */
	PICK(SWDP_RXFW_OK, 8, 1, 100, 2048, 255); CHECK(s.n == 16 && s.notes == SWDP_RXN_ARGFW);
	PICK(SWDP_RXFW_OK, 64, 1, 64, 2048, 255); CHECK(s.n == 64 && s.notes == 0);
	PICK(SWDP_RXFW_OK, 0, 1, 100, 2048, 255); CHECK(s.n == 100 && s.notes == 0);                                       /* firmware 0 = Linux default 255: no cap */
	PICK(SWDP_RXFW_OK, 0xFFFF, 1, 100, 2048, 255); CHECK(s.n == 100 && s.notes == 0);
	PICK(SWDP_RXFW_OK, 255, 1, 64, 40, 255);  CHECK(s.n == 39 && s.notes == SWDP_RXN_RING);
	PICK(SWDP_RXFW_OK, 255, 0, 0, 10, 255);   CHECK(s.n == 16 && (s.notes & SWDP_RXN_RING));                           /* never below 16 (the L25 posts are 16) */
#undef PICK

	/* ---- swdp_rx_refill_n: (pool, posted, ring free, wr, depth, all) -> items of the next batch ---- */
	CHECK(swdp_rx_thr(255, 0) == 32 && swdp_rx_thr(128, 0) == 32 && swdp_rx_thr(127, 0) == 31 && swdp_rx_thr(64, 0) == 16 && swdp_rx_thr(17, 0) == 4 && swdp_rx_thr(16, 0) == 1 && swdp_rx_thr(255, 1) == 1);
	CHECK(swdp_rx_refill_n(255, 255, 2047, 100, 2048, 0) == 0);                       /* full pool */
	CHECK(swdp_rx_refill_n(255, 224, 2047, 100, 2048, 0) == 0);                       /* 31 free < 32: not yet (Linux: rxbufpost <= max - 32) */
	CHECK(swdp_rx_refill_n(255, 223, 2047, 100, 2048, 0) == 32);                      /* 32 free: one batch of 32 */
	CHECK(swdp_rx_refill_n(255, 100, 2047, 100, 2048, 0) == 32);                      /* never above 32 per batch */
	CHECK(swdp_rx_refill_n(255, 0, 2047, 100, 2048, 1) == 32 && swdp_rx_refill_n(255, 250, 2047, 100, 2048, 1) == 5);   /* the first fill: any free buffer */
	CHECK(swdp_rx_refill_n(255, 250, 2047, 100, 2048, 0) == 0);                       /* steady state with 5 free: below the threshold */
	CHECK(swdp_rx_refill_n(255, 100, 7, 100, 2048, 0) == 7);                          /* cut to the ring's free slots */
	CHECK(swdp_rx_refill_n(255, 100, 0, 100, 2048, 0) == 0);                          /* ring full: nothing */
	CHECK(swdp_rx_refill_n(255, 100, 2047, 2040, 2048, 0) == 8);                      /* never past the ring end: 8 slots left */
	CHECK(swdp_rx_refill_n(255, 100, 2047, 2047, 2048, 0) == 1 && swdp_rx_refill_n(255, 100, 2047, 2048, 2048, 0) == 0 && swdp_rx_refill_n(255, 100, 2047, 0, 0, 0) == 0);
	CHECK(swdp_rx_refill_n(255, 100, 2047, 2049, 2048, 0) == 0 && swdp_rx_refill_n(255, 100, 2047, 0xFFFFFFFFu, 2048, 0) == 0);       /* a corrupt write cursor (past the ring) posts nothing */
	CHECK(swdp_rx_refill_n(16, 15, 2047, 5, 2048, 0) == 1 && swdp_rx_refill_n(16, 16, 2047, 5, 2048, 0) == 0);
	CHECK(swdp_rx_refill_n(16, 0, 2047, 5, 2048, 1) == 1 && swdp_rx_refill_n(16, 8, 2047, 5, 2048, 0) == 1 && swdp_rx_refill_n(17, 0, 2047, 5, 2048, 1) == 17);   /* F2: a pool of 16 posts ONE item per plan, 17 batches */   /* a pool of 16 refills at once */
	CHECK(swdp_rx_refill_n(64, 49, 2047, 5, 2048, 0) == 0 && swdp_rx_refill_n(64, 48, 2047, 5, 2048, 0) == 16);   /* pool 64: threshold 16 */

	/* ---- a batched rx plan: [ITEM x n, WRIDX, DB] at consecutive slots from wr1 ---- */
	e.wr1 = 9;
	e.rx_slots = 64; e.rxbuf.len = 64u * 2048u; e.data.len = 0x40000;                                              /* a pool of 64 buffers (the plans below use buffers 0..40) */
	CHECK(swdp_env_ok(&e) == 0);
	n = pl_rxb(&e, st, 1, 0);
	CHECK(n == 3 && swdp_audit(&e, st, n) == 0);                                                                   /* one item: the old shape */
	for (ok = 1; ok <= SWDP_RXB_MAX; ok++) {
		n = pl_rxb(&e, st, ok, 3);
		CHECK(n == ok + 2u && swdp_audit(&e, st, n) == 0 && st[ok].kind == SWDP_SK_WRIDX && st[ok].value == 9u + ok && st[ok + 1].kind == SWDP_SK_DB && st[ok + 1].value == swdp_db_wr(1, 9u + ok));
	}
	n = pl_rxb(&e, st, 33, 3);
	CHECK(n == 35 && swdp_audit(&e, st, n) == SWDP_A_ORDER);                                                        /* 33 items: above SWDP_RXB_MAX */
	n = pl_rxb(&e, st, 6, 3);                                                                                        /* 8 stores: more than SWDP_MAX_STORES, still accepted: only ring 1 may */
	CHECK(n == 8 && swdp_audit(&e, st, n) == 0);
	for (i = 0; i < 10; i++) {
		memcpy(t, st, sizeof t);
		switch (i) {
		case 0: t[2].slot = 12; t[2].addr = e.ring1.pa + 12 * 32; break;     /* a hole in the slots (9, 10, 12 ..) */
		case 1: t[1].slot = 9; t[1].addr = e.ring1.pa + 9 * 32; break;       /* two items for slot 9 */
		case 2: t[6].value = 9u + 7u; break;                                 /* the index is one past the last slot + 1 */
		case 3: t[6].value = 9u + 5u; break;                                 /* the index is behind the last item */
		case 4: t[7].value = swdp_db_wr(1, 9u + 5u); break;                  /* the doorbell names another index */
		case 5: { struct swdp_store y = t[5]; t[5] = t[6]; t[6] = y; break; }  /* an item after the index */
		case 6: t[2].len = 31; break;                                        /* a wrong item size inside the batch */
		case 7: t[3].ring = 5; break;                                        /* an item of another ring in the batch */
		case 8: t[7].kind = SWDP_SK_ITEM; break;                             /* no doorbell */
		default: t[0].slot = 10; t[0].addr = e.ring1.pa + 10 * 32; break;    /* the first item is not at the write slot */
		}
		CHECK(swdp_audit(&e, t, n) != 0);
	}
	/* the duplicate request id is its own case: items 3 and 4 carry the same rid, the rest valid */
	n = pl_rxb(&e, st, 6, 3);
	swdp_build_rxpost(g_rb[4], (uint8_t)((254u + e.wr1 + 4) % 253u), SWDP_RX_RID + 3u + 3u, e.rxbuf.pa + 6u * e.buf_sz, e.buf_sz);
	CHECK(swdp_audit(&e, st, n) == SWDP_A_VALUE);
	/* a batch that crosses the ring end is refused (the slots would be depth, ...) */
	e.wr1 = e.depth1 - 3u;
	n = swdp_plan_rxpost_n(&e, e.wr1, 5, 2u, &g_rb[0][0], st);
	for (i = 0; i < 5; i++) swdp_build_rxpost(g_rb[i], (uint8_t)((254u + e.wr1 + i) % 253u), SWDP_RX_RID + i, e.rxbuf.pa + (uint64_t)i * e.buf_sz, e.buf_sz);
	CHECK(swdp_audit(&e, st, n) != 0);
	n = swdp_plan_rxpost_n(&e, e.wr1, 3, 0u, &g_rb[0][0], st);                                                       /* a batch ending exactly at the ring end: index 0 */
	CHECK(n == 5 && swdp_audit(&e, st, n) == 0 && st[3].value == 0u);
	e.wr1 = 9;
	e.rx_slots = 0; e.rxbuf.len = 16u * 2048u; e.data.len = 0x20000;
	/* the request id must be one of the pool's buffers: rx_slots 0 = 16, rx_slots 64 holds 64 */
	mk_rx(&e, g_it, 20); n = swdp_plan_post(&e, 1, e.ring1.pa, 32, e.wr1, e.wr1 + 1, g_it, st);
	CHECK(swdp_audit(&e, st, n) != 0);                                                                               /* buffer 20 of a 16-buffer envelope */
	e.rx_slots = 64; e.rxbuf.len = 64u * 2048u; e.data.len = 0x40000;
	CHECK(swdp_env_ok(&e) == 0 && swdp_audit(&e, st, n) == 0);
	mk_rx(&e, g_it, 64); n = swdp_plan_post(&e, 1, e.ring1.pa, 32, e.wr1, e.wr1 + 1, g_it, st);
	CHECK(swdp_audit(&e, st, n) != 0);                                                                               /* buffer 64 of 64 */
	e.rx_slots = 15; CHECK(swdp_env_ok(&e) == SWDP_A_RANGE);                                                         /* below 16 */
	e.rx_slots = 256; e.rxbuf.len = 256u * 2048u; e.data.len = 0x100000; CHECK(swdp_env_ok(&e) == SWDP_A_RANGE);       /* above 255 (the range itself is big enough: only the bound refuses) */
	e.rxbuf.len = 64u * 2048u; e.data.len = 0x40000;
	e.rx_slots = 64; e.rxbuf.len = 63u * 2048u; CHECK(swdp_env_ok(&e) == SWDP_A_RANGE);                              /* the range holds fewer buffers than the pool */
	/* tx and flow plans keep their exact shape: no second item, even one that is otherwise perfect (slot + 1, the payload's buffer, index and doorbell for it) */
	e = mkenv(); e.wrf = 3;
	n = pl_tx(&e, st, 3);
	CHECK(n == 4 && swdp_audit(&e, st, n) == 0);
	memcpy(t, st, sizeof t);
	t[4] = t[3]; t[3] = t[2];                                                                                       /* [PAY, ITEM, ?, WRIDX, DB] */
	t[2] = st[1]; t[2].slot = 4; t[2].addr = e.flow.pa + 4u * 56u;                                                  /* the same item bytes at the next slot */
	t[3].value = 5u;                                                                                                /* index = last slot + 1 */
	t[4].value = swdp_db_wr(5, 5);
	CHECK(swdp_audit(&e, t, 5) == SWDP_A_ORDER);                                                                    /* a flow-ring plan with two items */
}

static void test_plans(void)
{
	struct swdp_env e = mkenv(), x;
	struct swdp_store s[SWDP_MAX_STORES], t[SWDP_MAX_STORES];
	uint8_t it[64];
	uint32_t n, i, j;

	/* ---- valid plans: flow create (WR[flowid]:=0, ring-0 item at wr0, WR[0]:=wr0+1, DB0 0xFF000000|wr), rxpost, tx, consume ---- */
	e.flow_open = 0;                                                                          /* a create is only for a ring that is not open (r2 C1) */
	n = pl_fc(&e, s);
	CHECK(n == 4 && swdp_audit(&e, s, n) == 0);
	e.flow_open = 1;
	CHECK(s[0].kind == SWDP_SK_WRIDX && s[0].addr == 0x880001008ull && s[0].value == 0 && s[0].ring == 5 && s[0].slot == 2);
	CHECK(s[1].kind == SWDP_SK_ITEM && s[1].addr == 0x880010000ull + 5 * 40 && s[1].len == 40 && s[1].data == g_it);
	CHECK(s[2].kind == SWDP_SK_WRIDX && s[2].addr == 0x880001000ull && s[2].value == 6 && s[3].kind == SWDP_SK_DB && s[3].addr == 0x140 && s[3].value == 0xFF000006u);
	n = pl_rx(&e, s, 1);
	CHECK(n == 3 && swdp_audit(&e, s, n) == 0 && s[1].addr == 0x880001004ull && s[2].value == 0xFF01000Au);
	n = pl_tx(&e, s, 0);
	CHECK(n == 4 && swdp_audit(&e, s, n) == 0 && s[1].addr == 0x880100000ull && s[1].len == 56 && s[2].addr == 0x880001008ull && s[3].value == 0xFF050001u);
	n = swdp_plan_consume(&e, 3, 7, s);
	CHECK(n == 2 && swdp_audit(&e, s, n) == 0 && s[0].addr == 0x880002004ull && s[1].value == 0xDD030007u);
	n = swdp_plan_consume(&e, 4, 9, s);
	CHECK(n == 2 && swdp_audit(&e, s, n) == 0 && s[0].addr == 0x880002008ull && s[1].value == 0xDD040009u);
	CHECK(swdp_env_ok(&e) == 0);

	/* ---- the mutations of a valid tx plan (cursor wrf 3, buffer 3) must all be refused ---- */
	e.wrf = 3;
	n = pl_tx(&e, s, 3);
	CHECK(swdp_audit(&e, s, n) == 0);
	for (i = 0; i < 12; i++) {
		memcpy(t, s, sizeof t);
		switch (i) {
		case 0: t[0].addr = e.rxbuf.pa; break;                        /* payload into the rx buffers */
		case 1: t[0].addr = e.txbuf.pa + e.txbuf.len - 10; break;     /* payload runs past the tx buffers */
		case 2: t[0].len = 1501; break;                               /* payload larger than a frame */
		case 3: t[1].addr += 56; break;                               /* item not at slot*isz */
		case 4: t[1].len = 48; break;                                 /* wrong item size for this ring */
		case 5: t[2].addr = e.h2d_wr.pa + 4 * 3; break;               /* index slot of another ring */
		case 6: t[2].value = 64; break;                               /* index == depth */
		case 7: t[3].addr = 0x144; break;                             /* wrong doorbell register */
		case 8: t[3].value = 0xFF060004u; break;                      /* doorbell names another ring */
		case 9: t[3].value = 0xDD050004u; break;                      /* read doorbell after a write index */
		case 10: t[3].space = SWDP_SP_HOST; break;                    /* doorbell in host memory */
		default: { struct swdp_store y = t[2]; t[2] = t[3]; t[3] = y; break; }   /* doorbell before the index */
		}
		CHECK(swdp_audit(&e, t, n) != 0);
	}
	memcpy(t, s, sizeof t); t[2].value = 4; t[3].value = 0xFF050009u;
	CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);                              /* M19c: the doorbell index is not the stored index (4) */
	memcpy(t, s, sizeof t); t[2].value = 64; t[3].value = swdp_db_wr(5, 64);
	CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);                              /* the write index itself is the depth, doorbell consistent */
	memcpy(t, s, sizeof t); t[3] = t[0];
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);                              /* no doorbell at all */
	n = pl_rx(&e, s, 1); t[0] = s[0]; t[1] = s[1]; t[2] = s[2]; t[3] = s[2];
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);                              /* a second doorbell after the doorbell */
	n = pl_tx(&e, s, 3);
	e.wrf = 0;

	/* ---- consume rows ---- */
	n = swdp_plan_consume(&e, 4, 9, s);
	memcpy(t, s, sizeof t); t[1].value = 0xDD020009u; CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);          /* ring 2 doorbell for a ring-4 index */
	memcpy(t, s, sizeof t); t[0].ring = 5; CHECK(swdp_audit(&e, t, n) == SWDP_A_RING);                       /* a flow ring has no D2H read slot */
	memcpy(t, s, sizeof t); t[0].addr += 4; CHECK(swdp_audit(&e, t, n) == SWDP_A_RANGE);
	n = swdp_plan_consume(&e, 3, 7, s);
	memcpy(t, s, sizeof t); t[0].addr = e.d2h_rd.pa + 8; CHECK(swdp_audit(&e, t, n) == SWDP_A_RANGE);        /* M11: ring 3 with ring 4's slot, in range */
	n = swdp_plan_consume(&e, 4, 0xFFFE, s);
	CHECK(swdp_audit(&e, s, n) == SWDP_A_VALUE);                                                             /* probe G: a read index far beyond the ring */
	n = swdp_plan_consume(&e, 4, 10, s);
	CHECK(swdp_audit(&e, s, n) == 0);                                                                        /* Part A: rd 8 -> 10, a batch of 2 on ring 4 */
	n = swdp_plan_consume(&e, 4, 8 + SWDP_CONS_MAX, s);
	CHECK(swdp_audit(&e, s, n) == 0);                                                                        /* the batch cap: 16 */
	n = swdp_plan_consume(&e, 4, 8 + SWDP_CONS_MAX + 1u, s);
	CHECK(swdp_audit(&e, s, n) == SWDP_A_VALUE);                                                             /* 17 items in one commit */
	{
		const uint32_t r2 = e.rd_d2h[0];

		n = swdp_plan_consume(&e, 2, (r2 + 2u) % e.depth_d2h[0], s);
		CHECK(swdp_audit(&e, s, n) == SWDP_A_VALUE);                                                         /* ring 2 (the engine's control ring): exactly one in a DP15 plan */
	}
	n = swdp_plan_consume(&e, 4, 8, s);
	CHECK(swdp_audit(&e, s, n) == SWDP_A_VALUE);                                                             /* the read cursor does not stand still or go back */
	n = swdp_plan_consume(&e, 4, 256, s);
	CHECK(swdp_audit(&e, s, n) == SWDP_A_VALUE && swdp_audit_store(&e, &s[0]) == SWDP_A_VALUE);              /* == depth_d2h */
	n = swdp_plan_consume(&e, 2, 1, s);
	CHECK(swdp_audit(&e, s, n) == 0);
	memcpy(t, s, sizeof t); t[0].data = g_it;                                                                /* an item has no place in a consume: ITEM kind */
	t[0].kind = SWDP_SK_ITEM; CHECK(swdp_audit(&e, t, n) != 0);

	/* ---- shape rows ---- */
	CHECK(swdp_audit(&e, s, 0) == SWDP_A_ORDER && swdp_audit(&e, s, 1) == SWDP_A_ORDER);                      /* empty, or no doorbell */
	n = pl_rx(&e, s, 1);
	{ struct swdp_store d = s[2]; CHECK(swdp_audit(&e, &d, 1) == SWDP_A_ORDER); }                              /* M22: store 0 is a doorbell */
	{ struct swdp_store *h = malloc(4 * sizeof *h); memset(h, 0, 4 * sizeof *h); pl_tx(&e, h, 0);
	  CHECK(swdp_audit(&e, h, 7) == SWDP_A_ORDER); free(h); }                                                  /* M24: n above the store maximum, exact-size heap */
	n = pl_rx(&e, s, 1); mk_rx(&e, it, 1);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_ITEM, 24, 2, 0, e.ring0.pa, 0, it);                                  /* ring 2 is D2H: never an item target */
	CHECK(swdp_audit(&e, t, 1) == SWDP_A_RING);

	/* ---- F1 probes A-G of the Opus r1 review: the index must be bound to the item, the cursor and the doorbell ---- */
	n = pl_rx(&e, s, 1);                                                                     /* ITEM r1 slot 9, WRIDX r1 value 10, DB */
	CHECK(swdp_audit(&e, s, n) == 0);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 1, 1, e.h2d_wr.pa + 4, 100, 0);          /* A: an index that jumps, no item */
	swdp_st(&t[1], SWDP_SP_PCIE, SWDP_SK_DB, 4, 1, 0, SWDP_DB0_OFF, swdp_db_wr(1, 100), 0);
	CHECK(swdp_audit(&e, t, 2) == SWDP_A_ORDER);
	t[0] = s[0];                                                                              /* B: item on ring 1, index and doorbell on the flow ring */
	swdp_st(&t[1], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 5, 2, e.h2d_wr.pa + 8, 3, 0);
	swdp_st(&t[2], SWDP_SP_PCIE, SWDP_SK_DB, 4, 5, 0, SWDP_DB0_OFF, swdp_db_wr(5, 3), 0);
	CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	mk_rx(&e, it, 1);                                                                         /* C: the index is not slot + 1 */
	n = swdp_plan_post(&e, 1, e.ring1.pa, 32, 9, 100, it, t);
	CHECK(swdp_audit(&e, t, n) == SWDP_A_ORDER);
	n = swdp_plan_post(&e, 1, e.ring1.pa, 32, 4, 5, it, t);                                   /* C2: the item is not at the write cursor (wr1 = 9) */
	CHECK(swdp_audit(&e, t, n) == SWDP_A_ORDER);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 1, 1, e.h2d_wr.pa + 4, 10, 0);            /* D2: the index before its item */
	t[1] = s[0]; t[2] = t[0]; t[3] = s[2];
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
	e.flow_open = 0;
	n = pl_fc(&e, t);                                                                         /* E: the new flow ring's index := 63, not 0 */
	t[0].value = 63; CHECK(swdp_audit(&e, t, n) == SWDP_A_ORDER);
	t[0].value = 64; CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);                            /* M06/M53: the index is the depth of the flow ring */
	t[0].value = 0; { struct swdp_env e2 = e; e2.wrf = 1; CHECK(swdp_audit(&e2, t, n) == SWDP_A_ORDER); }   /* the flow cursor is not 0 */
	CHECK(swdp_audit(&e, t, n) == 0);
	e.flow_open = 1;
	n = pl_rx(&e, s, 1);                                                                      /* (pl_fc reused the item bytes: rebuild the rx plan) */
	memcpy(t, s, sizeof t);                                                                   /* F: two items, one index */
	t[3] = t[2]; t[2] = t[1]; t[1] = t[0]; t[1].slot = 14; t[1].addr = e.ring1.pa + 14 * 32;
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
	t[0] = s[0]; t[1] = s[1]; t[1].slot = 2; t[1].addr = e.h2d_wr.pa + 8; t[2] = s[2];       /* M07: slot 2 is the flow ring's slot, not ring 1's (address consistent) */
	CHECK(swdp_audit(&e, t, 3) == SWDP_A_VALUE);
	t[0] = s[1]; t[1] = s[0]; t[2] = s[2];                                                   /* [WRIDX, ITEM, DB]: the index stored before its item */
	CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	t[0] = s[0]; t[1] = s[1]; t[2] = s[1];                                                   /* [ITEM, WRIDX, WRIDX]: a second index and no doorbell */
	CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	t[0] = s[0]; t[1] = s[1]; t[2] = s[1]; t[3] = s[2];                                      /* [ITEM, WRIDX, WRIDX, DB]: a second index */
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);

	/* ---- F4: the item bytes are part of the plan ---- */
	n = pl_rx(&e, s, 1);
	memcpy(t, s, sizeof t); t[0].data = 0; CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);        /* no item bytes */
	mk_rx(&e, it, 1); swdp_p32(it + 24, (uint32_t)e.txbuf.pa); swdp_p32(it + 28, (uint32_t)(e.txbuf.pa >> 32));
	memcpy(t, s, sizeof t); t[0].data = it; CHECK(swdp_audit(&e, t, n) == SWDP_A_RANGE);      /* rx DMA into the tx buffers, refused by swdp_audit itself */
	e.flow_open = 0; n = pl_fc(&e, s);
	mk_fc(&e, it); swdp_p32(it + 32, (uint32_t)(e.flow.pa + 16384));
	memcpy(t, s, sizeof t); t[1].data = it; CHECK(swdp_audit(&e, t, n) == SWDP_A_RANGE);      /* flow create naming another ring address */
	e.flow_open = 1;
	n = pl_tx(&e, s, 2);
	memcpy(t, s, sizeof t); t[0].data = 0; CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);        /* no payload bytes */
	mk_tx(&e, it, 2, 60); swdp_p32(it + 32, (uint32_t)(e.txbuf.pa + 3 * 2048));                /* item points at buffer 3, the payload sits in buffer 2 */
	memcpy(t, s, sizeof t); t[1].data = it; CHECK(swdp_audit(&e, t, n) == SWDP_A_RANGE);       /* (item slot 2 owns buffer 2: RANGE already by F3) */
	mk_tx(&e, it, 2, 74);                                                                       /* item length 60 != payload length 46 */
	memcpy(t, s, sizeof t); t[1].data = it; CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE);
	{ struct swdp_store y;                                                                     /* payload and item agree on a buffer they do not own: payload 3, item 2 */
	  mk_tx(&e, it, 3, 60); y = s[0]; y.addr = e.txbuf.pa + 3 * 2048; memcpy(t, s, sizeof t); t[0] = y;
	  mk_tx(&e, g_it, 2, 60); CHECK(swdp_audit(&e, t, n) == SWDP_A_VALUE); }                  /* item names buffer 2, payload store buffer 3 */
	n = pl_tx(&e, s, 2);
	memcpy(t, s, sizeof t); t[0].kind = SWDP_SK_PAYLOAD; t[1] = s[0]; t[2] = s[1]; t[3] = s[2];   /* two payloads, then item: second payload */
	CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
	memcpy(t, s, sizeof t); t[0] = s[1]; t[1] = s[0]; CHECK(swdp_audit(&e, t, n) == SWDP_A_ORDER);   /* payload after its item */
	n = pl_rx(&e, s, 1);
	{ struct swdp_store q[4]; swdp_st(&q[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 46, 5, 0, e.txbuf.pa, 0, g_pay); q[1] = s[0]; q[2] = s[1]; q[3] = s[2];
	  CHECK(swdp_audit(&e, q, 4) == SWDP_A_ORDER); }                                           /* a payload with a ring-1 item */
	e.flow_open = 0; n = pl_tx(&e, s, 2); CHECK(swdp_audit(&e, s, n) == SWDP_A_ORDER);        /* F10d: flow ring not open: no flow-ring post */
	n = pl_fc(&e, s); CHECK(swdp_audit(&e, s, n) == 0);                                        /* ... but the create itself goes through */
	e.flow_open = 1;

	/* ---- F2: the envelope itself, one row per clause of swdp_env_ok ---- */
	n = pl_tx(&e, s, 2);
	CHECK(swdp_audit(&e, s, n) == 0);
#define ENV_BAD(mut) do { x = e; mut; CHECK(swdp_env_ok(&x) == SWDP_A_RANGE && swdp_audit(&x, s, n) == SWDP_A_RANGE && swdp_audit_item(&x, 0, g_it) == SWDP_A_RANGE); } while (0)
	ENV_BAD(x.flowid = 0);                        /* probe H: ringid(0) wraps to 3, a D2H ring */
	ENV_BAD(x.flowid = 1);
	ENV_BAD(x.flowid = x.max_sub);
	ENV_BAD(x.max_sub = 300; x.h2d_wr.len = 1200; x.flowid = 253);   /* ring id above 0xFF */
	ENV_BAD(x.flow_isz = 40);
	ENV_BAD(x.idx_sz = 8);
	ENV_BAD(x.depth0 = 1); ENV_BAD(x.depth0 = 0x10000);
	ENV_BAD(x.depth1 = 1); ENV_BAD(x.depth1 = 0x10000);
	ENV_BAD(x.depth_flow = 1); ENV_BAD(x.depth_flow = 0x10000);
	ENV_BAD(x.depth_flow = 65);                   /* M34: 65 items x 56 B do not fit flow.len */
	ENV_BAD(x.depth0 = 129); ENV_BAD(x.depth1 = 2049);
	ENV_BAD(x.h2d_wr.len = 4 * x.max_sub - 1); ENV_BAD(x.d2h_rd.len = 11);
	ENV_BAD(x.buf_sz = 1513); ENV_BAD(x.buf_sz = 0x10000); ENV_BAD(x.buf_sz = 0);
	ENV_BAD(x.txbuf.len = 16 * 2048 - 1); ENV_BAD(x.rxbuf.len = 16 * 2048 - 1);
	ENV_BAD(x.wr0 = x.depth0); ENV_BAD(x.wr1 = x.depth1); ENV_BAD(x.wrf = x.depth_flow);
	ENV_BAD(x.depth_d2h[0] = 1); ENV_BAD(x.depth_d2h[1] = 0x10000); ENV_BAD(x.depth_d2h[2] = 0);
	ENV_BAD(x.rd_d2h[0] = x.depth_d2h[0]); ENV_BAD(x.rd_d2h[1] = x.depth_d2h[1]); ENV_BAD(x.rd_d2h[2] = x.depth_d2h[2]);
	ENV_BAD(x.h2d_wr.pa = x.g1.pa + x.g1.len);    /* outside the G1 block */
	ENV_BAD(x.d2h_rd.pa = x.g1.pa - 0x1000); ENV_BAD(x.ring0.pa = x.g1.pa + x.g1.len - 8); ENV_BAD(x.ring1.pa = x.data.pa);
	ENV_BAD(x.flow.pa = x.g1.pa); ENV_BAD(x.txbuf.pa = x.data.pa + x.data.len); ENV_BAD(x.rxbuf.pa = x.data.pa + x.data.len - 8);
	ENV_BAD(x.g1.len = 0); ENV_BAD(x.data.len = 0);
	ENV_BAD(x.txbuf.pa = ~0ull - 100);            /* wraps */
	ENV_BAD(x.rxbuf = e.ring0);                   /* probe I: the rx buffers are the control ring */
	ENV_BAD(x.rxbuf = e.txbuf); ENV_BAD(x.flow = e.txbuf); ENV_BAD(x.flow = e.rxbuf);
	ENV_BAD(x.ring1 = e.ring0); ENV_BAD(x.d2h_rd = e.h2d_wr); ENV_BAD(x.ring0 = e.h2d_wr); ENV_BAD(x.ring1 = e.d2h_rd); ENV_BAD(x.ring0 = e.d2h_rd); ENV_BAD(x.ring1 = e.h2d_wr);
	ENV_BAD(x.txbuf.len = 0);
	/* partial overlap (not equal ranges) */
	ENV_BAD(x.txbuf.pa = e.rxbuf.pa - 100);
	ENV_BAD(x.ring0.pa = e.h2d_wr.pa + 4);
	/* swdp_in itself */
	{ struct swdp_range r = { 100, 50 };
	  CHECK(!swdp_in(&r, 100, 0) && swdp_in(&r, 100, 50) && !swdp_in(&r, 100, 51) && swdp_in(&r, 149, 1) && !swdp_in(&r, 150, 1) && !swdp_in(&r, 99, 2) &&
	      !swdp_in(&r, ~0ull, 2) && !swdp_in(&r, 120, ~0ull)); }
	/* an item slot beyond the ring that still lies inside slack range, and a flow slot past max_sub (audit_store alone, M01/M08) */
	x = e; x.ring0.len = 200 * 40;
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_ITEM, 40, 0, 128, x.ring0.pa + 128 * 40, 0, g_it);
	CHECK(swdp_audit_store(&x, &t[0]) == SWDP_A_VALUE);
	x = e; x.flowid = 43; x.max_sub = 43; x.h2d_wr.len = 4 * 50;
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, swdp_ringid(43), 43, x.h2d_wr.pa + 4 * 43, 1, 0);
	CHECK(swdp_audit_store(&x, &t[0]) == SWDP_A_VALUE);

	/* ---- content audit (swdp_audit_item): where the dongle's DMA goes ---- */
	mk_fc(&e, it);                                                                             CHECK(swdp_audit_item(&e, 0, it) == 0);
	swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, e.flow.pa + 16384, 64, 56);                CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);   /* ring PA elsewhere */
	swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, e.flow.pa, 63, 56);                        CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);   /* another item count */
	swdp_build_flow_create(it, 1, 0, 3, 0, GWM, MAC, e.flow.pa, 64, 56);                        CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);   /* another flow id */
	swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, e.flow.pa, 64, 48);                        CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);   /* M35: item size 48, env 56 */
	mk_fc(&e, it); it[0] = 0x11;                                                                CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_VALUE);   /* M36: not a flow create */
	mk_rx(&e, it, 1);                                                                           CHECK(swdp_audit_item(&e, 1, it) == 0);
	mk_rx(&e, it, 15);                                                                          CHECK(swdp_audit_item(&e, 1, it) == 0);              /* J3: the last slot, exact */
	swdp_build_rxpost(it, 1, SWDP_RX_RID + 16, e.rxbuf.pa + 16 * 2048, 2048);                   CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);   /* rid slot 16 */
	swdp_build_rxpost(it, 1, SWDP_TX_RID, e.rxbuf.pa, 2048);                                    CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);   /* a tx request id */
	swdp_build_rxpost(it, 1, SWDP_RX_RID, e.rxbuf.pa + 1024, 2048);                             CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* J: straddles buffers 0 and 1 */
	swdp_build_rxpost(it, 1, SWDP_RX_RID + 3, e.rxbuf.pa, 2048);                                CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* J2: rid slot 3 names buffer 0 */
	swdp_build_rxpost(it, 1, SWDP_RX_RID, e.rxbuf.pa, 2049);                                    CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* longer than one buffer */
	swdp_build_rxpost(it, 1, SWDP_RX_RID + 15, e.rxbuf.pa + 15 * 2048, 2048); it[10] = 0; it[11] = 8;   /* len 2048 spelled out */
	                                                                                            CHECK(swdp_audit_item(&e, 1, it) == 0);
	mk_rx(&e, it, 1); swdp_p16(it + 10, 0);                                                     CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* M26: zero-length buffer */
	swdp_build_rxpost(it, 1, SWDP_RX_RID + 2, e.txbuf.pa, 2048);                                CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* rx DMA into the tx buffers */
	mk_rx(&e, it, 1); swdp_p32(it + 28, 0);                                                     CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* M29: high word 0 */
	mk_rx(&e, it, 1); swdp_p32(it + 28, 9);                                                     CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_RANGE);   /* M29: high word 9 */
	mk_rx(&e, it, 1); it[8] = 4;                                                                CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);   /* a metadata buffer we never provided */
	mk_rx(&e, it, 1); it[16] = 1;                                                               CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);   /* M31: rx metadata address alone */
	mk_rx(&e, it, 1); it[20] = 1;                                                               CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);
	mk_rx(&e, it, 1); it[0] = 0x0F;                                                             CHECK(swdp_audit_item(&e, 1, it) == SWDP_A_VALUE);
	mk_tx(&e, it, 0, 60);                                                                       CHECK(swdp_audit_item(&e, 5, it) == 0);
	mk_tx(&e, it, 15, 1514);                                                                    CHECK(swdp_audit_item(&e, 5, it) == 0);
	swdp_build_txpost(it, 56, 0, SWDP_TX_RID, g_fr, 60, 0, e.rxbuf.pa);                         CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_RANGE);   /* tx payload read from the rx buffers */
	swdp_build_txpost(it, 56, 0, SWDP_TX_RID, g_fr, 60, 0, e.txbuf.pa + 2040);                  CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_RANGE);   /* O: not the buffer of its rid */
	swdp_build_txpost(it, 56, 0, SWDP_TX_RID + 3, g_fr, 60, 0, e.txbuf.pa);                     CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_RANGE);   /* two request ids, one buffer */
	swdp_build_txpost(it, 56, 0, SWDP_TX_RID + 16, g_fr, 60, 0, e.txbuf.pa);                    CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);
	swdp_build_txpost(it, 56, 0, SWDP_RX_RID, g_fr, 60, 0, e.txbuf.pa);                         CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);   /* an rx request id */
	mk_tx(&e, it, 0, 60); it[23] = 2;                                                           CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);   /* two segments */
	mk_tx(&e, it, 0, 60); it[0] = 0x11;                                                         CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);
	mk_tx(&e, it, 0, 60); it[40] = 4;                                                           CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);   /* M31: tx status metadata length */
	mk_tx(&e, it, 0, 60); it[24] = 1;                                                           CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);   /* tx metadata address, low word */
	mk_tx(&e, it, 0, 60); it[28] = 1;                                                           CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_VALUE);   /* ... high word */
	mk_tx(&e, it, 0, 60); swdp_p16(it + 42, 2049);                                              CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_RANGE);   /* data_len above one buffer */
	CHECK(swdp_audit_item(&e, 2, it) == SWDP_A_RING);

	/* ---- payload store alone: one buffer per payload (O) ---- */
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1500, 5, 0, e.txbuf.pa + 2040, 0, g_pay);  CHECK(swdp_audit_store(&e, &t[0]) == SWDP_A_RANGE);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1500, 5, 0, e.txbuf.pa + 3 * 2048, 0, g_pay); CHECK(swdp_audit_store(&e, &t[0]) == 0);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1500, 5, 0, e.txbuf.pa + 15 * 2048, 0, g_pay); CHECK(swdp_audit_store(&e, &t[0]) == 0);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 0, 5, 0, e.txbuf.pa, 0, g_pay);              CHECK(swdp_audit_store(&e, &t[0]) == SWDP_A_VALUE);

	/* ---- shapes that only the explicit clauses of swdp_audit refuse (mutants N12-N14, M32) ---- */
	mk_tx(&e, it, 0, 60); swdp_p16(it + 42, 0);                                                 CHECK(swdp_audit_item(&e, 5, it) == SWDP_A_RANGE);   /* M32: data_len 0 */
	{
		struct swdp_store c[4], d3[2], d4[2], pl;
		uint8_t fi[64];
		n = pl_rx(&e, s, 1);                                                                     /* s = [ITEM r1 slot 9, WRIDX r1 10, DB] */
		mk_fc(&e, fi); j = swdp_plan_flow_create(&e, e.wr0, (e.wr0 + 1) % e.depth0, fi, c);      /* c = [WRIDX flow := 0, ITEM r0, WRIDX r0, DB] */
		e.flow_open = 0;
		CHECK(j == 4 && swdp_audit(&e, c, 4) == 0);
		memcpy(t, c, sizeof c);                                                                   /* the init exception is store 0 only: a jumping index at store 2 */
		swdp_st(&t[2], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 1, 1, e.h2d_wr.pa + 4, 100, 0); swdp_st(&t[3], SWDP_SP_PCIE, SWDP_SK_DB, 4, 1, 0, SWDP_DB0_OFF, swdp_db_wr(1, 100), 0);
		CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
		memcpy(t, c, sizeof c); swdp_st(&t[1], SWDP_SP_PCIE, SWDP_SK_DB, 4, 5, 0, SWDP_DB0_OFF, swdp_db_wr(5, 0), 0);   /* [WRIDX flow := 0, DB]: only the 4-store shape */
		CHECK(swdp_audit(&e, t, 2) == SWDP_A_ORDER);
		memcpy(t, c, sizeof c); swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 1, 1, e.h2d_wr.pa + 4, 0, 0);            /* "init" store aimed at the rx ring's index */
		CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
		memcpy(t, c, sizeof c); swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 0, 0, e.h2d_wr.pa, 0, 0);                /* ... or at the control ring's */
		CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
		t[0] = c[0]; t[1] = s[0]; t[2] = s[1]; t[3] = s[2];                                       /* init store + an rxpost: the init belongs to a ring-0 create */
		CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
		swdp_plan_consume(&e, 3, 7, d3); swdp_plan_consume(&e, 4, 9, d4);
		t[0] = s[0]; t[1] = d3[0]; t[2] = d3[1];                                                  /* an item in a consume */
		CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
		swdp_st(&pl, SWDP_SP_HOST, SWDP_SK_PAYLOAD, 46, 5, 0, e.txbuf.pa, 0, g_pay);
		t[0] = pl; t[1] = d3[0]; t[2] = d3[1];                                                    /* a payload in a consume */
		CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
		t[0] = d3[0]; t[1] = d4[0]; t[2] = d4[1];                                                 /* two read indexes, one doorbell */
		CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
		t[0] = s[0]; t[1] = pl; t[2] = s[1]; t[3] = s[2];                                         /* a payload AFTER the item */
		CHECK(swdp_audit(&e, t, 4) == SWDP_A_ORDER);
		t[0] = s[0];                                                                              /* item on ring 1 slot 9, index on the flow ring with the SAME value 10 */
		swdp_st(&t[1], SWDP_SP_HOST, SWDP_SK_WRIDX, 2, 5, 2, e.h2d_wr.pa + 8, 10, 0);
		swdp_st(&t[2], SWDP_SP_PCIE, SWDP_SK_DB, 4, 5, 0, SWDP_DB0_OFF, swdp_db_wr(5, 10), 0);
		CHECK(swdp_audit_store(&e, &t[1]) == 0 && swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	}
	/* envelope wrap: ranges that cross 2^64 are refused before the sum-based overlap test can mistake them for disjoint */
	x = e; x.g1 = (struct swdp_range){ ~0ull - 0xFFFFF, 0x200000 }; x.h2d_wr.pa = x.g1.pa + 0x1000; x.d2h_rd.pa = x.g1.pa + 0x2000;
	x.ring1 = (struct swdp_range){ ~0ull - 0x7FFF, 0x10000 }; x.ring0 = (struct swdp_range){ ~0ull - 0x3FFF, 128 * 40 };
	CHECK(swdp_env_ok(&x) == SWDP_A_RANGE);

	/* ---- audit_store alone: every clause separated from the plan-level ones (mutants M04, M05, M05c, M14-M17) ---- */
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1501, 5, 0, e.txbuf.pa, 0, g_pay);              CHECK(swdp_audit_store(&e, &t[0]) == SWDP_A_VALUE);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1500, 5, 0, e.txbuf.pa, 0, g_pay);              CHECK(swdp_audit_store(&e, &t[0]) == 0);
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 100, 5, 0, e.txbuf.pa - 2048, 0, g_pay);        CHECK(swdp_audit_store(&e, &t[0]) == SWDP_A_RANGE);   /* below the tx buffers, on a buffer boundary */
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 100, 5, 0, e.txbuf.pa + e.txbuf.len, 0, g_pay); CHECK(swdp_audit_store(&e, &t[0]) == SWDP_A_RANGE);   /* above them */
	x = e; x.buf_sz = 1000;
	swdp_st(&t[0], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 1500, 5, 0, x.txbuf.pa, 0, g_pay);              CHECK(swdp_audit_store(&x, &t[0]) == SWDP_A_RANGE);   /* a payload longer than one buffer */
	{
		static const struct { uint32_t word; int want; } db[] = {
			{ 0xAB050001u, SWDP_A_VALUE }, { 0x00050001u, SWDP_A_VALUE }, { 0xFF050001u, 0 }, { 0xFF050040u, SWDP_A_VALUE }, { 0xFF05003Fu, 0 },
			{ 0xFF0107FFu, 0 }, { 0xFF010800u, SWDP_A_VALUE }, { 0xFF000080u, SWDP_A_VALUE }, { 0xFF00007Fu, 0 },
			{ 0xDD02003Fu, 0 }, { 0xDD020040u, SWDP_A_VALUE }, { 0xDD0400C8u, 0 }, { 0xDD0300FFu, 0 }, { 0xDD030100u, SWDP_A_VALUE },
			{ 0xFF020001u, SWDP_A_RING }, { 0xFF060001u, SWDP_A_RING }, { 0xFF030001u, SWDP_A_RING }, { 0xDD010001u, SWDP_A_RING }, { 0xDD050001u, SWDP_A_RING },
		};
		for (i = 0; i < sizeof db / sizeof db[0]; i++) {
			swdp_st(&t[0], SWDP_SP_PCIE, SWDP_SK_DB, 4, (db[i].word >> 16) & 0xFF, 0, SWDP_DB0_OFF, db[i].word, 0);
			CHECK(swdp_audit_store(&e, &t[0]) == db[i].want);
		}
	}
}


static void test_privacy_and_text(void)
{
	static const char *const g[] = { SWDP_GAP_DP4, SWDP_GAP_DP1, SWDP_GAP_DP2, SWDP_GAP_DP9, SWDP_GAP_DP10, SWDP_GAP_DP11, SWDP_GAP_DP12, SWDP_GAP_X_ARPOWN,
	    SWDP_GAP_DP15 };
	unsigned i, g0 = g_gaps;
	for (i = 0; i < sizeof g / sizeof g[0]; i++) CHECK(strncmp(g[i], "WIFI GAP: ", 10) == 0);
	swdp_gaps_announce();
	CHECK(g_gaps == g0 + 5);                                                  /* DP2 DP4 DP9 DP12 DP15 */
	CHECK(strlen(g_log) < sizeof g_log - 512);                                /* the capture did not truncate */
	for (i = 0; i < sizeof g / sizeof g[0]; i++) CHECK(strstr(g_log, g[i]) != NULL);   /* rule 8 (Opus r1 F7): every defined GAP line is printed */
	/* no MAC byte string and no dotted IP in any line */
	for (i = 0; g_log[i]; i++) {
		CHECK(memcmp(g_log + i, MAC, 6) != 0 && memcmp(g_log + i, GWM, 6) != 0);
		CHECK(strncmp(g_log + i, "10.77.", 6) != 0);
	}
	CHECK(g_lines > 20 && g_gaps > 10);
}


/* Opus r2 conditions C1-C4 and notes N2, N4: rows (each names the expected code) */
static void test_r2(void)
{
	struct swdp_env e = mkenv(), x;
	struct swdp_store s[SWDP_MAX_STORES], t[SWDP_MAX_STORES], *h;
	uint8_t it[64];
	uint32_t n;

	/* C1: a flow create is exactly the 4-store shape, for a ring that is not open */
	e.flow_open = 0;
	mk_fc(&e, it); n = swdp_plan_post(&e, 0, e.ring0.pa, SWDP_ISZ_CTRL_SUB, e.wr0, e.wr0 + 1, it, t);
	CHECK(n == 3 && swdp_audit(&e, t, n) == SWDP_A_ORDER);                                    /* plain 3-store ring-0 post, no WR[flowid] := 0 */
	x = e; x.wrf = 7; CHECK(swdp_audit(&x, t, n) == SWDP_A_ORDER);                            /* ... with a stale flow cursor */
	e.flow_open = 1; n = pl_fc(&e, s); CHECK(swdp_audit(&e, s, n) == SWDP_A_ORDER);           /* re-create of an open ring */
	e.flow_open = 0; n = pl_fc(&e, s); CHECK(swdp_audit(&e, s, n) == 0);
	e.flow_open = 1;

	/* C2 / M21: nothing after a consume doorbell */
	swdp_plan_consume(&e, 3, 7, t);
	swdp_st(&t[2], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 46, 5, 0, e.txbuf.pa, 0, g_pay); CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	mk_rx(&e, it, 1); swdp_plan_consume(&e, 3, 7, t);
	swdp_st(&t[2], SWDP_SP_HOST, SWDP_SK_ITEM, 32, 1, e.wr1, e.ring1.pa + 32ull * e.wr1, 0, it); CHECK(swdp_audit(&e, t, 3) == SWDP_A_ORDER);
	/* C2 / N12b: the init shape needs n == 4; a 1-store plan must not read s[1] (exact-size heap copy, ASan) */
	e.flow_open = 0; n = pl_fc(&e, s);
	h = malloc(sizeof *h); *h = s[0]; CHECK(swdp_audit(&e, h, 1) == SWDP_A_ORDER); free(h);
	memcpy(t, s, 4 * sizeof *s);
	swdp_st(&t[3], SWDP_SP_PCIE, SWDP_SK_DB, 4, 1, 0, SWDP_DB0_OFF, swdp_db_wr(1, (e.wr1 + 1) % e.depth1), 0);
	swdp_st(&t[4], SWDP_SP_HOST, SWDP_SK_PAYLOAD, 46, 5, 0, e.txbuf.pa, 0, g_pay);
	CHECK(swdp_audit(&e, t, 5) == SWDP_A_ORDER);
	e.flow_open = 1;
	/* N4 / M22: a lone doorbell, exact-size heap */
	n = pl_tx(&e, s, 2);
	h = malloc(sizeof *h); *h = s[2]; CHECK(swdp_audit(&e, h, 1) == SWDP_A_ORDER); free(h);

	/* C3: high words of the addresses the dongle reads (tx payload, flow ring) */
	mk_tx(&e, it, 0, 60); swdp_p32(it + 36, 0); CHECK(swdp_audit_item(&e, swdp_ringid(e.flowid), it) == SWDP_A_RANGE);
	mk_tx(&e, it, 0, 60); swdp_p32(it + 36, 9); CHECK(swdp_audit_item(&e, swdp_ringid(e.flowid), it) == SWDP_A_RANGE);
	mk_fc(&e, it); swdp_p32(it + 36, 0); CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);
	mk_fc(&e, it); swdp_p32(it + 36, 9); CHECK(swdp_audit_item(&e, 0, it) == SWDP_A_RANGE);
	/* C3: ring wrap (X03, X04) and the depth-2 edge */
	x = e; x.wr1 = x.depth1 - 1; mk_rx(&x, it, 1);
	n = swdp_plan_post(&x, 1, x.ring1.pa, SWDP_ISZ_RXPOST, x.wr1, 0, it, t); CHECK(swdp_audit(&x, t, n) == 0);
	x = e; x.rd_d2h[2] = x.depth_d2h[2] - 1; n = swdp_plan_consume(&x, 4, 0, t); CHECK(swdp_audit(&x, t, n) == 0);
	x = e; x.depth1 = 2; x.ring1.len = 64; x.wr1 = 1; mk_rx(&x, it, 1);
	n = swdp_plan_post(&x, 1, x.ring1.pa, SWDP_ISZ_RXPOST, 1, 0, it, t); CHECK(swdp_audit(&x, t, n) == 0);

	/* C4: the G1 block and the data block are separate allocations */
	x = e; x.data = (struct swdp_range){ 0x880000000ull, 0x120000 }; CHECK(swdp_env_ok(&x) == SWDP_A_RANGE);
	x = e; x.g1 = (struct swdp_range){ 0x880000000ull, 0x120000 };   CHECK(swdp_env_ok(&x) == SWDP_A_RANGE);
	x = e; x.g1 = e.data; x.data = e.g1;                              CHECK(swdp_env_ok(&x) == SWDP_A_RANGE);   /* swapped: the sub-ranges are in the wrong blocks */
	/* N2: ring ids 2..4 need max_sub >= 4 for the D2H index slots */
	x = e; x.max_sub = 3; x.flowid = 2; x.h2d_wr.len = 12; CHECK(swdp_env_ok(&x) == SWDP_A_RANGE);
	CHECK(swdp_env_ok(&e) == 0);
}

#endif /* WLAN_DATA_HOST_TEST_PLANS_H */
