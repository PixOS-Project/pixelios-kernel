/* T-0a9d (R29): the DP15 executing transport and the L16-L20 ring-2 helpers of sweet_wlan_g2.h, moved out so the file does not grow; included by sweet_wlan_g2.h at the place the text used to stand (no guard, one include) */
#ifdef SWEET_WLAN_DATA_H
/* ---- DP15 (T-75c7, SPEC-WIFI Part E TRANSPORT block s.1): the executing transport. swg_dp_exec turns ONE plan that swdp_audit accepted into engine stores, through swg_d4d_st only
 * (kind sequence swg_dp_kind, old value, read-back, the printed line). Compiled only when the includer defined SWDP_LOG / SWDP_GAP and included sweet_wlan_data.h first. Not wired to the glue yet (SWDP_GAP_DP15). ---- */
#define SWG_DP_ITEMW_MAX 14u            /* the longest item: the extended tx post, 56 B */
#define SWG_DP_ITEMW_ALL (SWDP_RXB_MAX * (SWDP_ISZ_RXPOST / 4u) > SWG_DP_ITEMW_MAX ? SWDP_RXB_MAX * (SWDP_ISZ_RXPOST / 4u) : SWG_DP_ITEMW_MAX)   /* patch 0357: the item words of a plan: one item, or a ring-1 batch of SWDP_RXB_MAX x 8 words */
#define SWG_DP_PAYW_MAX  ((SWDP_ETH_MAXFRAME - SWDP_ETH_HLEN + 3u) / 4u)        /* payload words: 1500 B */

static const char *const swg_dp_aname[] = { "ok", "space", "range", "align", "value", "ring", "order" };    /* SWDP_A_* */

/* a refusal before the first store: WIFI GAP line (rule 8), the engine soft-stops (d4dstop, the caller's undo runs), nothing was stored. -SWG_E_ORDER */
static int
swg_dp_refuse(struct swg_wx *x, const char *name, const char *why)
{
	char gb[64];

	snprintf(gb, sizeof gb, "plan %.20s refused: %.24s", name, why);
	swl_gap("dp15", gb, "high");
	printf("WIFI GAP: DP15 plan %s refused: %s\n", name, why);
	x->d4dstop = 1u;
	return -SWG_E_ORDER;
}

/* the env fields the engine owns, never the caller (TRANSPORT s.1 item 1): wr0 = d4wr (the single ring-0 cursor), the ring-2 read cursor = d4rd, and the env must name the engine's own blocks, rings and index arrays. 0 = ok */
static int
swg_dp_env(const struct swg_wx *x, struct swdp_env *e)
{
	e->wr0 = x->d4wr;
	if (e->depth_d2h[0] != SWG_R2_DEPTH) {
		return -1;
	}
	e->rd_d2h[0] = x->d4rd % SWG_R2_DEPTH;
	return e->g1.pa == x->in.blk && e->g1.len == x->in.blk_len && e->h2d_wr.pa == x->in.idx[SWG_IDX(SWD_IDX_H2D_W)] &&
	       e->d2h_rd.pa == x->in.idx[SWG_IDX(SWD_IDX_D2H_R)] && e->ring0.pa == x->in.ring[0] && e->ring1.pa == x->in.ring[1] &&
	       e->depth0 == swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items ? 0 : -1;
}

/* the ring-full check the audit cannot make (r2 N1): the H2D-RD slot of the ring the plan posts to, read with the engine's host read. 0 = room, -1 = refused (text in *why) */
static int
swg_dp_room(struct swg_wx *x, const struct swdp_env *e, const struct swdp_store *it, const char **why)
{
	uint32_t depth = swdp_h2d_depth(e, it->ring), rd;

	/* sprint 2026-11-08: ring 0 wraps like every ring; the ring-full check below covers it (its old words come from the ring-0 shadow, swg_dp_shadow) */
	if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_H2D_R)] + 4ull * swdp_h2d_slot(it->ring), &rd) || rd == 0xFFFFFFFFu) {
		*why = "H2D read index unreadable";
		return -1;
	}
	rd &= 0xFFFFu;
	if (rd >= depth) {
		*why = "H2D read index outside the ring";
		return -1;
	}
	if ((it->slot + 1u) % depth == rd) {
		*why = "ring full";
		return -1;
	}
	return 0;
}

/* the bus space and offset of the host word at pa: the G1 block (SWG_SP_HOST) or the DATA block (SWG_SP_DATA). 0 = ok */
static int
swg_dp_map(const struct swg_wx *x, const struct swdp_env *e, uint64_t pa, int *sp, uint32_t *off)
{
	if (swg_hoff(x, pa, 4u, off) == 0) {
		*sp = SWG_SP_HOST;
		return 0;
	}
	if ((pa & 3u) != 0u || !swdp_in(&e->data, pa, 4u)) {
		return -1;
	}
	*sp = SWG_SP_DATA;
	*off = (uint32_t)(pa - e->data.pa);
	return 0;
}

/* the shadow word of the host word at pa (the old value of the next store); ring 0 has none (always fresh, old 0). NULL = no shadow region covers it */
static uint32_t *
swg_dp_shadow(struct swg_wx *x, const struct swdp_env *e, uint64_t pa)
{
	uint32_t i;

	if (swdp_in(&e->ring0, pa, 4u)) {
		uint32_t o;
		uint32_t *r0;

		if (swg_hoff(x, pa, 4u, &o) != 0 || (r0 = swg_r0_shadow(x, SWG_SP_HOST, o)) == NULL) {
			return NULL;
		}
		x->dpzero = *r0;        /* a copy: swg_dp_words writes *sh after the store, swg_put already updated the real shadow */
		return &x->dpzero;
	}
	for (i = 0; i < SWG_DP_NSH; i++) {
		if (x->dpsh[i].w != NULL && pa >= x->dpsh[i].pa && (pa - x->dpsh[i].pa) / 4u < x->dpsh[i].nw) {
			return &x->dpsh[i].w[(pa - x->dpsh[i].pa) / 4u];
		}
	}
	return NULL;
}

/* nw words of one ITEM or PAYLOAD store, from the transport's private copy: old = the shadow, one store row each (the kind sequence fixes their order) */
static int
swg_dp_words(struct swg_wx *x, const struct swdp_env *e, int kind, uint64_t pa, const uint32_t *w, uint32_t nw, const char *nm)
{
	uint32_t k, off, *sh;
	int sp, ex;

	if (nw > 1u && x->quiet && x->dpx != 0u && x->b->rd_blk != NULL && x->b->wr_blk != NULL) {        /* Part A: the batch (swg_d4d_st_blk), when the range is one run of one block and one shadow region */
		uint32_t off2, i;
		int sp2;

		for (i = 0; i < SWG_DP_NSH; i++) {
			if (x->dpsh[i].w != NULL && pa >= x->dpsh[i].pa && (pa - x->dpsh[i].pa) / 4u + nw <= x->dpsh[i].nw) {
				break;
			}
		}
		if (i < SWG_DP_NSH && !swdp_in(&e->ring0, pa, 4u) && swg_dp_map(x, e, pa, &sp, &off) == 0 && swg_dp_map(x, e, pa + 4ull * (nw - 1u), &sp2, &off2) == 0 && sp2 == sp &&
		    off2 == off + 4u * (nw - 1u)) {
			sh = &x->dpsh[i].w[(pa - x->dpsh[i].pa) / 4u];
			ex = swg_d4d_st_blk(x, kind, sp, off, sh, w, nw, nm);
			if (ex == 0) {
				memcpy(sh, w, 4u * nw);
			}
			return ex;
		}
	}
	for (k = 0; k < nw; k++) {
		x->fail = "a DP15 host word is outside both host blocks or has no shadow";
		sh = swg_dp_shadow(x, e, pa + 4ull * k);
		if (swg_dp_map(x, e, pa + 4ull * k, &sp, &off) || sh == NULL) {
			return swg_stop(x, SWG_E_ADDR);
		}
		ex = swg_d4d_st(x, kind, sp, off, *sh, w[k], 0, nm);
		if (ex != 0) {
			return ex;
		}
		*sh = w[k];
	}
	return 0;
}

/* one index or doorbell store of the plan; oldv = the cursor before (ring-0 / ring-2 book updates follow a made index store) */
static int
swg_dp_one(struct swg_wx *x, const struct swdp_env *e, const struct swdp_store *s)
{
	uint32_t off;
	int sp, ex;

	if (s->kind == SWDP_SK_DB) {
		return swg_d4d_st(x, SWG_K_DB, SWG_SP_PCIE, (uint32_t)s->addr, 0u, s->value, 0, "dp15 doorbell");
	}
	x->fail = "a DP15 index slot is outside the G1 block";
	if (swg_dp_map(x, e, s->addr, &sp, &off) || sp != SWG_SP_HOST) {
		return swg_stop(x, SWG_E_ADDR);
	}
	if (s->kind == SWDP_SK_WRIDX) {
		ex = swg_d4d_st(x, SWG_K_H2DW, sp, off, swdp_h2d_cur(e, s->ring), s->value, 1, "dp15 h2d_w slot");
		if (ex == 0 && s->ring == 0u) {
			x->d4wrap0 += (s->value & 0xFFFFu) == 0u && x->d4wr != 0u ? 1u : 0u;     /* sprint 2026-11-08: a ring-0 index store back to slot 0 is a lap */
			x->d4wr = s->value & 0xFFFFu;   /* ring 0: the engine's single cursor moves with its index store */
		}
		return ex;
	}
	ex = swg_d4d_st(x, SWG_K_D2HR, sp, off, e->rd_d2h[s->ring - 2u], s->value, 1, "dp15 d2h_r slot");
	x->d4rd += ex == 0 && s->ring == 2u ? 1u : 0u;
	return ex;
}

/* copy the item / payload bytes of the plan ONCE (after the audit read them) into transport-owned words, zero padded to a whole word; returns the operation and the word counts */
static unsigned
swg_dp_copy(const struct swdp_store *s, uint32_t n, uint32_t *iw, uint32_t *pw, uint32_t itemw[SWG_DP_ITEMW_ALL], uint32_t payw[SWG_DP_PAYW_MAX])
{
	uint8_t b[4];
	uint32_t i, k, nw;

	*iw = *pw = 0u;
	for (i = 0; i < n; i++) {
		if (s[i].kind != SWDP_SK_ITEM && s[i].kind != SWDP_SK_PAYLOAD) {
			continue;
		}
		nw = (s[i].len + 3u) / 4u;
		if (s[i].kind == SWDP_SK_ITEM && *iw + nw > SWG_DP_ITEMW_ALL) {
			return SWG_OP_NONE;            /* patch 0357: more item words than the private copy holds: the caller refuses (the audit allows at most SWDP_RXB_MAX ring-1 items, so this is a second line) */
		}
		for (k = 0; k < nw; k++) {
			memset(b, 0, sizeof b);
			memcpy(b, s[i].data + 4u * k, s[i].len - 4u * k < 4u ? s[i].len - 4u * k : 4u);
			(s[i].kind == SWDP_SK_ITEM ? itemw + *iw : payw)[k] = swd4b_get32(b);       /* patch 0357: the items of a batch follow each other in itemw */
		}
		*(s[i].kind == SWDP_SK_ITEM ? iw : pw) = s[i].kind == SWDP_SK_ITEM ? *iw + nw : nw;
	}
	if (s[0].kind == SWDP_SK_RDIDX) {
		return SWG_OP_DPCONS;
	}
	return s[0].kind == SWDP_SK_WRIDX ? SWG_OP_DPFC : s[0].kind == SWDP_SK_PAYLOAD ? SWG_OP_DPTX : SWG_OP_DPPOST;
}

/* run the stores of an audited, room-checked plan from the private copy, in the audited order. 0 = all made, else the failing store (1-based) in *at and the engine's negative code */
static int
swg_dp_run(struct swg_wx *x, const struct swdp_env *e, const struct swdp_store *s, uint32_t n, const uint32_t *itemw, const uint32_t *payw, uint32_t *at)
{
	uint32_t i, io = 0u;
	int ex = 0;

	for (i = 0; i < n && ex == 0; i++) {
		*at = i + 1u;
		if (s[i].kind == SWDP_SK_ITEM) {
			ex = swg_dp_words(x, e, SWG_K_ITEM, s[i].addr, itemw + io, s[i].len / 4u, "dp15 item word");
			io += s[i].len / 4u;           /* patch 0357: the next item of a batch starts where this one ended */
		} else if (s[i].kind == SWDP_SK_PAYLOAD) {
			ex = swg_dp_words(x, e, SWG_K_PAY, s[i].addr, payw, (s[i].len + 3u) / 4u, "dp15 payload word");
			if (!x->quiet) {
				printf("SWEET WLAN: DP15 payload %u B\n", s[i].len);
			}
		} else {
			ex = swg_dp_one(x, e, &s[i]);
		}
	}
	return ex;
}

/* T-73fc (W-JOIN L25: the 5769bb23 build died of `Invalid kernel stack pointer` right after `DP15 payload 121 B`, 2 of 2 boots [obs camera]): the payload words (1500 B) are file-scope BSS, not a stack array, like T-eabd's swg_wx. One DP15 plan runs at a time (swg_dp_exec is not re-entered: x->dpx is set for the whole run); a secret plan wipes the words after it. */
static uint32_t swg_dp_payw[SWG_DP_PAYW_MAX];
static uint32_t swg_dp_itemw[SWG_DP_ITEMW_ALL];       /* patch 0357: file scope too (a batch of 32 items is 1 KB; never on the kernel stack) */

/* TRANSPORT s.1: the only place a swdp_store becomes a bus store. env is filled from the engine (wr0, rd_d2h[0]); the other cursors are the caller's books (swdp_ring), checked by the audit. secret != 0: no store line prints a value.
 * 0 = every store made; negative: refused before the first store (soft stop) or the first failed store (hard stop, WIFI GAP DP15 store i/n ..., no retry, no doorbell after it) */
static int
swg_dp_exec(struct swg_wx *x, struct swdp_env *env, const struct swdp_store *s, uint32_t n, const char *name, int secret)
{
	uint32_t *itemw = swg_dp_itemw, *payw = swg_dp_payw, iw, pw, at = 0u, i;
	const char *why = "";
	int r, ex;
	unsigned op;

	if (x->stopped || x->d4dstop || !x->entered) {
		return swg_dp_refuse(x, name, "engine stopped");
	}
	if (x->dpx) {
		return swg_dp_refuse(x, name, "a DP15 plan is already running");
	}
	swg_hr(x, SWG_HR_DPX, "dp_exec");
	if (swg_dp_env(x, env) != 0) {
		return swg_dp_refuse(x, name, "env is not the engine's");
	}
	r = swdp_env_ok(env);
	r = r != 0 ? r : swdp_audit(env, s, n);
	if (r != 0) {
		return swg_dp_refuse(x, name, swg_dp_aname[r]);
	}
	op = swg_dp_copy(s, n, &iw, &pw, itemw, payw);
	if (op == SWG_OP_NONE) {
		return swg_dp_refuse(x, name, "more item words than the transport copy holds");
	}
	if (op == SWG_OP_DPFC && (env->wr0 != x->d4wr || (uint8_t)(itemw[0] >> 24) != swg_ep0(x))) {
		return swg_dp_refuse(x, name, "ring-0 epoch is not swg_ep0 (the absolute ring-0 count)");      /* the epoch swg_d4d_post's callers use, laps included (sprint 2026-11-08) */
	}
	for (i = 0; i < n; i++) {
		if (s[i].kind == SWDP_SK_ITEM && swg_dp_room(x, env, &s[i], &why) != 0) {
			return swg_dp_refuse(x, name, why);
		}
	}
	x->eop = (uint8_t)op;
	x->erow = 0u;
	x->dppay = (uint16_t)pw;
	x->dpitem = (uint16_t)iw;
	x->dpx = 1u;
	x->dpsec = secret != 0 ? 1u : 0u;
	if (x->b->plan != NULL && n > 0u && s[n - 1u].kind == SWDP_SK_DB) {
		x->b->plan(x->b->ctx, 1);
	}
	ex = swg_dp_run(x, env, s, n, itemw, payw, &at);
	if (x->b->plan != NULL) {
		x->b->plan(x->b->ctx, 0);
	}
	if (secret != 0) {
		for (i = 0; i < SWG_DP_PAYW_MAX; i++) {
			((volatile uint32_t *)payw)[i] = 0u;
		}
	}
	x->dpx = x->dpsec = 0u;
	x->eop = SWG_OP_NONE;
	if (ex != 0) {
		x->d4dstop = 1u;
		swl_gap("dp15", "store failed, plan stopped", "high");
		printf("WIFI GAP: DP15 store %u/%u of plan %s failed code=%s\n", at, n, name, swg_ename[-ex]);
		return ex;
	}
	if (x->quiet) {
		x->nquiet++;
	} else {
		printf("SWEET WLAN: DP15 plan %s done: %u stores\n", name, n);
	}
	return 0;
}
#endif /* SWEET_WLAN_DATA_H */

/* the six words of ring-2 item k (absolute count, slot k % 64) as bytes; 0 = read, else stopped */
static int
swg_d4d_r2(struct swg_wx *x, uint32_t k, uint8_t in[SWD4B_D2H_LEN])
{
	uint32_t i, v;

	for (i = 0; i < SWD4B_D2H_LEN / 4u; i++) {
		x->fail = "a d4d ring-2 item read failed";
		if (swg_hrd(x, x->in.ring[SWD_D2H_CTRL - SWD_H2D_CTRL] + (uint64_t)(k % SWG_R2_DEPTH) * swd_rings[SWD_D2H_CTRL - SWD_H2D_CTRL].isz + 4u * i, &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		in[4u * i] = (uint8_t)v;
		in[4u * i + 1u] = (uint8_t)(v >> 8);
		in[4u * i + 2u] = (uint8_t)(v >> 16);
		in[4u * i + 3u] = (uint8_t)(v >> 24);
	}
	return 0;
}

/* 22b: the FRONT ring-2 item (absolute index x->d4rd, everything before it consumed) is looked at BEFORE any consumer judges it. A type-0x24 D2H_MAILBOX_DATA item whose mailbox data (u32 at byte 12, bcmmsgbuf.h:900-907) is exactly D2H_DEV_DS_ENTER_REQ 0x2 (bcmpcie.h:511; dhd_pcie.c:12942ff: the host acks it) is answered with one DS round (swg_ds_round; the first extra round is the DS hold, a request after it is a STOP). Every other bit is a loud STOP with no store: D3_ACK 0x1 (dhd_pcie.c:13103ff, 13175: no D3 was requested), EXIT_NOTE 0x4 without a pending assert (dhd_pcie.c:13020ff, 13171: the host only expects it inside a round), HOST_SLEEP_EXIT_ACK 0x8 / IDMA_INITED 0x10 (bcmpcie.h:508-514: nothing in this port asked for them), FWHALT and the trap codes (bcmpcie.h:527-533; dhd_pcie.c:12901, 13156). The caller guarantees an item is pending. 1 = a round was made (state changed), 0 = the front item is not type 0x24, negative = stopped (swg_stop ran swg_undo) */
static int
swg_ds_front(struct swg_wx *x)
{
	uint8_t in[SWD4B_D2H_LEN], type = 0;
	const uint32_t k = x->d4rd;
	uint32_t mb;
	const char *why;
	int ex, chk;

	ex = swg_d4d_r2(x, k, in);
	if (ex != 0) {
		return ex;
	}
	if (in[0] != SWD4B_MSG_D2H_MBDATA) {
		return 0;
	}
	chk = swd4b_d2h_check(in, swg_ep(k), &type);
	mb = swd4b_get32(in + 12);
	if (chk != 0) {
		printf("SWEET WLAN: D4 DS STOP, no store: ring-2 item %u is type 0x24 but fails the epoch/xor check (%d), mailbox data 0x%x\n", k, chk, mb);
		x->fail = "a type-0x24 ring-2 item fails the epoch/xor check";
		return swg_stop(x, SWG_E_FATAL);
	}
	if (mb != SWD4B_D2H_DS_ENTER_REQ) {
		why = (mb & (SWG_D2H_FWHALT | 0xE1000000u)) != 0u ? "FWHALT/trap code (bcmpcie.h:527-533; dhd_pcie.c:12901, 13156)" :
		      (mb & 1u) != 0u ? "D2H_DEV_D3_ACK without a D3 request (dhd_pcie.c:13103, 13175)" :
		      (mb & 4u) != 0u ? "D2H_DEV_DS_EXIT_NOTE without a pending wake assert (dhd_pcie.c:13020, 13171)" :
		      (mb & 8u) != 0u ? "D2HMB_DS_HOST_SLEEP_EXIT_ACK, nothing asked for it (bcmpcie.h:510)" :
		      (mb & 0x10u) != 0u ? "D2H_DEV_IDMA_INITED, not handled (bcmpcie.h:511)" : "unknown mailbox data";
		printf("SWEET WLAN: D4 DS STOP, no store: ring-2 item %u mailbox data 0x%x is not DS_ENTER_REQ only: %s\n", k, mb, why);
		swl_gap("d4ds", "d2h mailbox data other than DS_ENTER_REQ, stopped", "high");
		x->fail = "ring-2 D2H_MAILBOX_DATA other than DS_ENTER_REQ only: not answered";
		return swg_stop(x, SWG_E_FATAL);
	}
	if (x->dshold) {
		printf("SWEET WLAN GAP: D4 DS_ENTER_REQ at ring-2 item %u while the DS hold is on (device_wake asserted, no deassert sent, research 124 s.4): the firmware does not stop requesting deep sleep or a request was in flight (U1/U2), STOP, no store\n", k);
		swl_gap("d4ds", "DS_ENTER_REQ after the DS hold, stopped", "high");
		x->fail = "DS_ENTER_REQ after the DS hold round";
		return swg_stop(x, SWG_E_FATAL);
	}
	if (x->d4wr + 4u >= swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items) {
		printf("SWEET WLAN: D4 DS STOP, no store: ring-0 index %u leaves no room for a DS round (depth %u)\n", x->d4wr, swd_rings[SWD_H2D_CTRL - SWD_H2D_CTRL].items);
		x->fail = "no ring-0 room for a DS round";
		return swg_stop(x, SWG_E_ADDR);
	}
	x->d4c_wr = x->d4wr;
	x->d4c_rd = x->d4rd;
	printf("SWEET WLAN: D4 DS round %u: ring-2 item %u is D2H_MAILBOX_DATA 0x2 (DS_ENTER_REQ), answering at ring-0 idx %u\n", x->d4ds + 1u, k, x->d4wr);
	ex = swg_ds_round(x, (unsigned)x->d4ds + 1u);
	return ex < 0 ? ex : 1;
}

/* T-c3ac H2 (W5): the first ring-2 poll delay after the first L24 doorbell (SWG_POLL_MS = 100 ms) as SWG_POLL_MS steps of 1 ms, each re-reading the host word (host RAM only, no bus access) and printing a line for ms 1..10 and every 10th after: if the wedge is a stuck timer
 * the lines stop at ms N; if they reach 100 the timer is fine and the wedge comes later (a bus fault delivered after the poll). Same total time as the plain delay. 0 = done; negative = stopped */
static int
swg_l24_spin(struct swg_wx *x)
{
	uint32_t ms, v = 0;

	for (ms = 1u; ms <= SWG_POLL_MS; ms++) {
		x->b->delay_ms(x->b->ctx, 1u);
		x->fail = "the L24 spin host-word read failed";
		if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (ms <= 10u || ms % 10u == 0u) {
			printf("SWEET WLAN: L24 spin ms %u: d2h_w %#x\n", ms, v);
		}
		if (v == 0xFFFFFFFFu) {
			x->fail = "host d2h_w[0] reads all ones (dead link) in the L24 spin";
			return swg_stop(x, SWG_E_DEAD);
		}
	}
	printf("SWEET WLAN: L24 spin done: %u ms, timer and host word alive\n", SWG_POLL_MS);
	return 0;
}

/* poll the D2H-WR slot 0 (host memory only, like the L16 exit-note poll: no TCM, the dongle may be asleep) for up to SWG_POLLS x SWG_POLL_MS: until at least `want` new items are pending (want != 0) or any is (want == 0); new = beyond the host's read count x->d4rd.
 * 22b: whenever an item is pending, the front one is first offered to swg_ds_front (a DS request is answered, then the poll goes on without a delay). *w = the ABSOLUTE write count (d4rd + pending; the raw slot when the slot is far below the read count),
 * *waited = ms waited. 0 = read; negative = stopped (dead link, a DS stop) */
static int
swg_d4d_waitn(struct swg_wx *x, uint32_t want, uint32_t polls, uint32_t *w, uint32_t *waited)
{
	uint32_t n = 0, v = 0, pend = 0;
	int ex, behind = 0;

	*waited = 0;
	while (n < polls) {
		x->fail = "a d4d ring-2 write-index read failed";
		if (swg_hrd(x, x->in.idx[SWG_IDX(SWD_IDX_D2H_W)], &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		if (v == 0xFFFFFFFFu) {
			x->fail = "host d2h_w[0] reads all ones (dead link) in the d4d poll";
			return swg_stop(x, SWG_E_DEAD);
		}
		pend = ((v & 0xFFFFu) + SWG_R2_DEPTH - x->d4rd % SWG_R2_DEPTH) % SWG_R2_DEPTH;
		behind = pend >= SWG_R2_DEPTH / 2u;     /* the slot is far behind the read count: not a plausible backlog; the poll runs its budget as before and the caller reports "went below" */
		if (behind) {
			if (want == 0u) {
				break;      /* a watch for any change: the slot differs from the read count (as before) */
			}
			pend = 0u;
		}
		if (pend != 0u && !x->krefresh) {      /* Part A: no DS round inside a keep refresh (its ring-0 items are refused by the L25 judge -> hard stop); such an item reaches swg_e_items: a soft stop */
			ex = swg_ds_front(x);
			if (ex < 0) {
				return ex;
			}
			if (ex == 1) {
				continue;       /* a DS round moved the read count: read the write slot again at once */
			}
		}
		if (x->elv == 24u && n < SWG_HB_POLLS) {
			printf("SWEET WLAN: L24 hb poll %u: d2h_w %#x pending %u read %u\n", n, v, pend, x->d4rd);       /* W-C: printed BEFORE the delay, after the read of the host word; the last such line tells where a wedge sits */
		}
		if (want != 0u ? pend >= want : pend != 0u) {
			break;
		}
		if (n + 1u < polls) {
			if (x->l24spin != 0u) {
				x->l24spin = 0u;
				ex = swg_l24_spin(x);
				if (ex < 0) {
					return ex;
				}
			} else {
				x->b->delay_ms(x->b->ctx, x->krefresh ? 1u : SWG_POLL_MS);
			}
			*waited += x->krefresh ? 1u : SWG_POLL_MS;
		}
		n++;
	}
	*w = behind ? v & 0xFFFFu : x->d4rd + pend;
	return 0;
}

static int
swg_d4d_wait(struct swg_wx *x, uint32_t want, uint32_t *w, uint32_t *waited)
{
	return swg_d4d_waitn(x, want, x->krefresh ? SWG_POLLS * SWG_POLL_MS : SWG_POLLS, w, waited);     /* Part A: a keep refresh polls every 1 ms over the same budget */
}

/* print and judge the ring-2 items base..w-1 (at most SWG_D4D_MAXIT): raw words, the epoch/XOR check, the parsed ack / completion fields. pending: an ioctl request is in flight (cmd, trans, rid: what the items must echo);
 * 0 = no request was sent (L17: every item is a gap). The first problem of each item is a gap and sets d4dstop; *cm = the completion's fields, *got = bit 0 ack seen, bit 1 completion seen. 0 = scanned; negative = stopped */
static int
swg_d4d_scan(struct swg_wx *x, uint32_t base, uint32_t w, int pending, uint32_t cmd, uint32_t trans, uint32_t rid, struct swd4b_ioctl_d2h *cm, unsigned *got)
{
	uint8_t in[SWD4B_D2H_LEN], type = 0;
	struct swd4b_ioctl_d2h f;
	uint32_t k, lim = w - base > SWG_D4D_MAXIT ? base + SWG_D4D_MAXIT : w;
	int chk, pr, ex;

	if (w < base) {
		SWG_D4D_GAP(x, "d2h write index %u below the read index %u", w, base);
		return 0;
	}
	if (w - base > (pending ? 2u : 0u)) {
		SWG_D4D_GAP(x, "ring 2 holds %u new items, expected %u", w - base, pending ? 2u : 0u);
	}
	for (k = base; k < lim; k++) {
		ex = swg_d4d_r2(x, k, in);
		if (ex != 0) {
			return ex;
		}
		chk = swd4b_d2h_check(in, swg_ep(k), &type);
		pr = swd4b_ioctl_d2h(in, &f);
		swg_hr(x, SWG_HR_ITEM, "d4d_item");
		printf("SWEET WLAN: D4d ring2 item %u raw 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x 0x%08x epoch/xor check %d\n", k, swd4b_get32(in), swd4b_get32(in + 4), swd4b_get32(in + 8), swd4b_get32(in + 12), swd4b_get32(in + 16), swd4b_get32(in + 20), chk);
		printf("SWEET WLAN: D4d ring2 item %u type 0x%x epoch 0x%x request_id 0x%x status %d ring_id %u cmd %u trans_id 0x%x resp_len %u\n", k, f.type, f.epoch, f.request_id, f.status, f.ring_id, f.cmd, f.trans_id, f.resp_len);
		if (type == SWD4B_MSG_D2H_MBDATA) {
			SWG_D4D_GAP(x, "ring-2 item %u: 0x24 behind an unconsumed item, not answered", k);
		} else if (pr != 0) {
			SWG_D4D_GAP(x, "unexpected ring-2 type 0x%x", type);
		} else if (chk != 0) {
			SWG_D4D_GAP(x, "ring-2 item %u epoch/xor check failed (%d)", k, chk);
		} else if (!pending) {
			SWG_D4D_GAP(x, "ring-2 item %u (type 0x%x) but no request was sent", k, type);
		} else if (f.status != 0) {
			SWG_D4D_GAP(x, "ring-2 item %u type 0x%x status %d != 0", k, type, f.status);
		} else if (f.cmd != cmd) {      /* completion: DHD checks the cmd too, a mismatch is fatal [src M:7912]; ack: [guess: inferred from MB:785-793 and M:7793-7794, DHD does not check it]; the STOP text names "cmd" */
			SWG_D4D_GAP(x, "ring-2 item %u cmd %u, expected %u", k, f.cmd, cmd);
		} else if (type == SWD4B_MSG_IOCTL_ACK) {
			if (f.request_id != SWD4B_IOCTL_REQID) {        /* [guess: inferred from MB:785-793 and M:7793-7794; DHD does not check, it only skips the audit for 0xFFFE]; the STOP text names "request_id" */
				SWG_D4D_GAP(x, "ack request_id 0x%x, expected 0xfffe", f.request_id);
			} else if ((*got & 1u) != 0u) {
				SWG_D4D_GAP(x, "second ioctl ack at ring-2 item %u", k);
			}
			*got |= 1u;
		} else {
			if (f.trans_id != trans) {
				SWG_D4D_GAP(x, "completion trans_id 0x%x, expected 0x%x", f.trans_id, trans);
			} else if (f.request_id != rid) {
				SWG_D4D_GAP(x, "completion request_id %u, expected %u", f.request_id, rid);
			} else if (f.resp_len > SWD_RESPBUF_LEN) {
				SWG_D4D_GAP(x, "completion resp_len %u above the %u B buffer", f.resp_len, SWD_RESPBUF_LEN);
			} else if ((*got & 2u) != 0u) {
				SWG_D4D_GAP(x, "second ioctl completion at ring-2 item %u", k);
			}
			*got |= 2u;
			*cm = f;
		}
	}
	return 0;
}

/* one request's answer: wait for the ack and the completion (ring-2 write index base + 2), scan them. d4dstop is set when anything is missing or wrong. 0 = done; negative = stopped. *w = the observed write index */
static int
swg_d4d_answer(struct swg_wx *x, uint32_t base, uint32_t cmd, uint32_t trans, uint32_t rid, struct swd4b_ioctl_d2h *cm, uint32_t *w)
{
	uint32_t waited;
	unsigned got = 0;
	int ex;

	ex = swg_d4d_wait(x, 2u, w, &waited);
	if (ex != 0) {
		return ex;
	}
	printf("SWEET WLAN: D4d answer poll after %u ms: d2h_w0 %u (base %u, want %u)\n", waited, *w, base, base + 2u);      /* 22b: counts are absolute */
	if (*w < base) {
		SWG_D4D_GAP(x, "d2h write index %u went below %u", *w, base);
		return 0;
	}
	ex = swg_d4d_scan(x, base, *w, 1, cmd, trans, rid, cm, &got);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (*w == base) {
		SWG_D4D_GAP(x, "no ioctl answer in %u ms", waited);
	} else if (got != 3u) {
		SWG_D4D_GAP(x, "ioctl %s missing after %u ms", (got & 1u) != 0u ? "completion" : "ack", waited);
	}
	return 0;
}

/* read min(n, SWG_D4D_RSP_MAX) response bytes from the START of the response buffer (M:7946-7950), whole words (little endian): out holds the bytes rounded up to a word, the caller uses only the first n; the glue invalidates the cache line
 * before every host read (sw_g2_rd). 0 = read; negative = stopped */
static int
swg_d4d_rsp(struct swg_wx *x, uint32_t n, uint8_t *out)
{
	uint32_t i, v, m = n > SWG_D4D_RSP_MAX ? SWG_D4D_RSP_MAX : n;
	uint32_t off;

	x->fail = "the d4d response buffer is outside the G1 block";
	if (swg_hoff(x, x->in.respbuf, SWD_RESPBUF_LEN, &off)) {
		return swg_stop(x, SWG_E_ADDR);
	}
	for (i = 0; i < m; i += 4u) {
		x->fail = "a d4d response buffer read failed";
		if (swg_hrd(x, x->in.respbuf + i, &v)) {
			return swg_stop(x, SWG_E_ADDR);
		}
		out[i] = (uint8_t)v;
		out[i + 1u] = (uint8_t)(v >> 8);
		out[i + 2u] = (uint8_t)(v >> 16);
		out[i + 3u] = (uint8_t)(v >> 24);
	}
	return 0;
}

static void
swg_d4d_hex(const uint8_t *b, uint32_t n)
{
	uint32_t i, j;

	for (i = 0; i < n; i += 32u) {
		char line[80];
		uint32_t p = 0;

		for (j = i; j < n && j < i + 32u; j++) {
			p += (uint32_t)snprintf(line + p, sizeof line - p, "%02x", b[j]);
		}
		printf("SWEET WLAN: D4d response bytes %u..%u hex %s\n", i, j - 1u, line);
	}
}

/* a whitespace delimited token of the NUL terminated text t of n bytes */
static int
swg_d4d_tok(const char *t, uint32_t n, const char *tok)
{
	uint32_t i, j, tl = 0;

	while (tok[tl] != '\0') {
		tl++;
	}
	for (i = 0; i + tl <= n; i++) {
		for (j = 0; j < tl && t[i + j] == tok[j]; j++) {
		}
		if (j == tl && (i == 0u || t[i - 1u] == ' ') && (i + tl == n || t[i + tl] == ' ')) {
			return 1;
		}
	}
	return 0;
}

/* the cap string of n response bytes: printable text (a NUL ends it, anything else not printable is '.') in lines of SWG_D4D_LINE characters, then the supplicant tokens */
static void
swg_d4d_cap(const uint8_t *b, uint32_t n)
{
	char t[SWG_D4D_RSP_MAX + 1u], line[SWG_D4D_LINE + 1u];
	uint32_t i, j, tn = 0;
	int wpa, idsup;

	while (tn < n && b[tn] != 0u) {
		t[tn] = b[tn] >= 0x20u && b[tn] < 0x7Fu ? (char)b[tn] : '.';
		tn++;
	}
	t[tn] = '\0';
	printf("SWEET WLAN: D4d cap %u bytes read, text %u chars up to the first NUL\n", n, tn);
	for (i = 0; i < tn; i += SWG_D4D_LINE) {
		for (j = 0; j < SWG_D4D_LINE && i + j < tn; j++) {
			line[j] = t[i + j];
		}
		line[j] = '\0';
		printf("SWEET WLAN: D4d cap[%u..%u] %s\n", i, i + j - 1u, line);
	}
	wpa = swg_d4d_tok(t, tn, "sup_wpa");
	idsup = swg_d4d_tok(t, tn, "idsup");
	if (!wpa && !idsup) {
		swl_gap("d4d", "cap has neither sup_wpa nor idsup", "high");
	} else {
		printf("SWEET WLAN: D4d cap supplicant tokens: sup_wpa %s idsup %s\n", wpa ? "present" : "absent", idsup ? "present" : "absent");
	}
}

/* the consume + re-post of one answered request: the ring-2 read index rd_old -> rd (the observed write index) and doorbell 0xDD020000 | rd, then ONE response buffer (id rid) at ring-0 index *idx.
 * DHD re-posts inside the completion handler, before its batch's read-index update (M:7970-7976 vs 7524-7532); this order is the brief's. 0 = made; negative = stopped */
static int
swg_d4d_repost(struct swg_wx *x, uint32_t rd_old, uint32_t rd, uint32_t rid)
{
	uint8_t it[SWD4B_ITEM_LEN];
	int ex;

	printf("SWEET WLAN: D4d consume: D2H-RD %u -> %u, doorbell %#x\n", rd_old, rd, SWG_D4D_DB_RD | (rd % SWG_R2_DEPTH));
	ex = swg_d4d_consume(x, rd_old, rd);
	if (ex != 0) {
		return ex;
	}
	swd4b_item_respbuf(it, rid, (uint16_t)SWD_RESPBUF_LEN, x->in.respbuf, swg_ep0(x));
	printf("SWEET WLAN: D4d re-post: ioctl-response buffer id %u, ring-0 idx %u epoch %u, H2D-WR %u, doorbell %#x\n", rid, x->d4wr, swg_ep0(x), swg_r0_next(x), SWG_D4D_DB_WR | swg_r0_next(x));
	return swg_d4d_post(x, it);
}

/* the request of one level: build the item, post it. 0 = made; negative = stopped */
static int
swg_d4d_request(struct swg_wx *x, uint32_t cmd, uint32_t trans, uint32_t len, const char *what)
{
	uint8_t it[SWD4B_ITEM_LEN];
	int ex;

	swd4b_item_ioctl(it, cmd, (uint16_t)trans, (uint16_t)len, (uint16_t)len, x->in.ioctbuf, swg_ep0(x));
	printf("SWEET WLAN: D4d request %s: cmd %u trans_id 0x%x in/out len %u payload pa %#llx, ring-0 idx %u epoch %u, H2D-WR %u, doorbell %#x\n", what, cmd, trans, len, (unsigned long long)x->in.ioctbuf, x->d4wr, swg_ep0(x), swg_r0_next(x), SWG_D4D_DB_WR | swg_r0_next(x));
	ex = swg_d4d_post(x, it);
	return ex;
}
