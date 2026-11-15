/*
 * Host test of the L25 store whitelist sweet_wlan_l25_wl.h (T-5173; docs/research/T-3e93-l25-wiring-design.md s.1 classes L1-L9).
 *   REPLAY: the whole wlan_l25_host_test.c (env, flow create + CREATE_CMPLT, 16 rx posts, rx poll, 4-way M1-M4, key installs, ARP + echo ping, the failure scenarios)
 *     runs unchanged; every bus write of the engine is judged by swl25w_ok BEFORE it is made, against the memory the engine sees (the accessor reads the host test's G1/DATA arrays).
 *     Demanded: every store of the scenarios the engine completes is accepted, with a class; the set of classes seen by the engine streams is printed (L2/L6 come from the ioctl
 *     battery below, the l25 host test stubs swg_e_call).
 *   The real ioctl stream (L2 + L6 from swg_e_call on the real G1 plan) is judged in wlan_g2_host_l25wl.h, a case of wlan_g2_host_test.
 *   BATTERY: a synthetic world in the middle of L25 (flow open, 5 rx posts, 3 tx posts, one pending consume), one legitimate store per class (accepted, right class) and the
 *     mutants of the class table, each derived from a legitimate store and demanded refused with the reason that names it.
 * No hardware. Build like wlan_l25_host_test (shim/build.sh).
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "sweet_wlan_l25_wl.h"

static void wl_hook(int sp, uint32_t a, uint32_t v);
#define L25_WR_HOOK wl_hook
/* every ioctl the real L25 handshake makes must be a row of the header's table (T-5173 r2 N5): a change in sweet_wlan_l25_hs.h fails here, not at run time */
static unsigned call_n, call_bad;
static void wl_call(uint32_t cmd, const char *name, uint32_t wlen)
{
	unsigned r;

	call_n++;
	for (r = 0; r < SWL25W_NIOVARS; r++)
		if (cmd == swl25w_iovars[r].cmd && wlen == swl25w_iovars[r].len && name != NULL && strcmp(name, swl25w_iovars[r].name) == 0)
			return;
	call_bad++;
	fprintf(stderr, "FAIL L25 call cmd %u %s len %u is no row of swl25w_iovars\n", cmd, name != NULL ? name : "(none)", wlen);
}
#define L25_CALL_HOOK wl_call
#define main l25_main
#include "wlan_l25_host_test.c"
#undef main

/* ---------------------------------------------------------------- replay judge over the engine's memory */
/* sprint 2026-11-08: the whitelist keeps its own constants (an independent judge), but they must equal the engine's: one source, checked copies */
_Static_assert(SWL25W_RX_N == SWL25_RXPOSTS && SWL25W_RX_N == SWL25_SHADOW_ITEMS && SWL25W_TX_N == SWL25_SHADOW_ITEMS, "L25 whitelist rx/tx counts != the engine's");
_Static_assert(SWL25W_FLOW_DEPTH == SWL25_FLOW_DEPTH, "L25 whitelist flow depth != the engine's");
_Static_assert(SWG_R0_DEPTH == SWD_R0_ITEMS && SWG_R2_DEPTH == SWD_R2_ITEMS, "engine ring depths != sweet_wlan_dma.h");
static struct swl25w W;
static unsigned rp_stores, rp_refused, rp_cls[SWL25W_NCLASS + 1];
static char rp_first[8][160];
static unsigned rp_nfirst;

static int rp_rd(void *ctx, int space, uint32_t off, uint32_t *v)
{
	const uint32_t *m;

	(void)ctx;
	m = mem(space == SWL25W_SP_HOST ? SWG_SP_HOST : SWG_SP_DATA, off);
	if (m == NULL)
		return -1;
	*v = *m;
	return 0;
}
static void wl_cfg(struct swl25w *w)
{
	memset(w, 0, sizeof *w);
	w->g1_pa = G1PA; w->data_pa = DAPA; w->g1_len = G1LEN; w->data_len = DALEN;
	w->ioctbuf = 0x60000u; w->respbuf = 0x70000u; w->evbuf = 0x80000u;             /* free offsets of this test's G1 (the replay stubs swg_e_call, the real plan is judged in wlan_g2_host_l25wl.h) */
	w->h2d_w = (uint32_t)(H2DW - G1PA); w->d2h_w = (uint32_t)(D2HW - G1PA); w->d2h_r = (uint32_t)(D2HR - G1PA); w->h2d_r = (uint32_t)(H2DR - G1PA);
	w->ring[0] = (uint32_t)(RING0 - G1PA); w->ring[1] = (uint32_t)(RING1 - G1PA); w->ring[2] = (uint32_t)(RING2 - G1PA);
	w->ring[3] = (uint32_t)(RING3 - G1PA); w->ring[4] = (uint32_t)(RING4 - G1PA);
	w->depth[0] = SWD_R0_ITEMS; w->depth[1] = SWD_R1_ITEMS; w->depth[2] = SWD_R2_ITEMS; w->depth[3] = SWD_R3_ITEMS; w->depth[4] = SWD_R4_ITEMS;      /* sprint 2026-11-08: from sweet_wlan_dma.h */      /* swd_rings of patch 0167, written out (checked below) */
	w->flowid = 2u;
	w->rd = rp_rd;
}
static void wl_hook(int sp, uint32_t a, uint32_t v)
{
	const char *why = "";
	uint32_t old = 0u;
	const uint32_t *m;
	int space = sp == SWG_SP_PCIE ? SWL25W_SP_PCIE : sp == SWG_SP_HOST ? SWL25W_SP_HOST : sp == SWG_SP_DATA ? SWL25W_SP_DATA : 0, cls;     /* any other space: judged as unknown, refused */

	if (J.nw == 1u) {                                   /* the first store of a fresh world (world_reset zeroes J) */
		wl_cfg(&W);
	}
	m = space == SWL25W_SP_HOST || space == SWL25W_SP_DATA ? mem(sp, a) : NULL;
	if (m != NULL)
		old = *m;
	if (sp == SWG_SP_DATA || sp == SWG_SP_HOST || sp == SWG_SP_PCIE) {
		/* the engine's flow_open, as the dongle script knows it: the create was answered with status 0 */
		W.flow_open = J.answer == 1 && J.status == 0 && J.ndb >= 1u ? 1u : W.flow_open;
	}
	cls = swl25w_ok(&W, space, a, v, old, &why);
	rp_stores++;
	if (cls < 0) {
		rp_refused++;
		if (rp_nfirst < 8u)
			snprintf(rp_first[rp_nfirst++], sizeof rp_first[0], "sp %d off %#x v %#x old %#x: %s", sp, a, v, old, why);
	} else {
		rp_cls[cls]++;
		swl25w_done(&W, space, a, v, old, cls);
	}
}


/* ---------------------------------------------------------------- the battery: a synthetic world, legitimate stores accepted, mutants refused */
enum { M_PTR_ITEM, M_PTR_OUT, M_WORD_FLIP, M_IDX2, M_IDX0, M_IDX_UPPER, M_DB_RING, M_DB_TWICE, M_DB_IDX, M_DB_NONE, M_RXREG, M_SLOT16, M_FREE_OUT, M_STALE, M_NOCOMMIT,
       M_NOTOPEN, M_OPEN_INIT, M_EPOCH, M_CONSUME2, M_NOPEND, M_OTHER_WORD, M_DB_OFF, M_MAC, M_IOCT_CMD, M_BUF_PTR, M_SPACE,
       M_IOVAR, M_BUSY, M_FLOW2, M_BATCH, M_READFAIL, M_LAYOUT, M_PEND, M_REWRITE, M_PARTIAL, M_CARRY, M_LEN, M_RID0, M_WRAP, M_OLD, M_DEPTH, M_NMUT };
static const char *const MNAME[M_NMUT] = {
	"pointer word off by one item", "rx pointer outside DATA", "item word flipped (exact field)", "index +2", "index +0", "index upper half moved", "doorbell of another ring",
	"doorbell rung twice", "doorbell with another index", "doorbell without an index store", "host write into the rx buffer region", "item/ring slot >= 16", "free bytes outside the buffer",
	"index commits a stale item", "item at a slot that is not the write slot", "tx before the flow ring is open", "flow index init after open", "epoch off by one",
	"consume +2", "consume with nothing pending", "host word in no class", "doorbell at another PCIe offset", "MAC bytes differ", "ioctl cmd not GET/SET_VAR", "buffer post pointer outside the 8 buffers", "unknown space",
	"ioctl is not one of the seven L25 calls", "payload or second ioctl while one is in transit", "second flow create", "consume batch above 16, or index at/above depth", "a read failed", "wrong layout",
	"store between an index store and its doorbell", "ring-0 word rewritten or at the wrong slot", "index commits a partial or corrupted item", "4 GB carry: hi word without the carry", "ioctl length above the buffer",
	"buffer post request id 0", "ring 0 index past the ring, or a lap slot the dongle has not read", "old word differs from memory, or upper half", "an index at or above the depth" };
/* each mutant dies only for the check it names: the reason must hold one of these substrings (T-5173 r1 F5) */
static const char *const MWANT[M_NMUT] = {
	"pointer", "pointer", "L1 |L2 |L3 |L4 |ring-0 item: type", "new is not old + 1|L3 rx post", "new is not old + 1|items ahead", "upper half moved", "L9 doorbell: not the ring", "L9 doorbell: not the ring", "L9 doorbell: not the ring", "L9 doorbell: not the ring",
	"DATA write outside", "< 16|rx buffers of the block|beyond the ring|DATA write outside|next flow commit|no L25 class", "L4 tx post: metadata length|DATA write outside|L5 tx payload|no L25 class", "ring-0 item: type", "is not at", "flow ring not open", "new is not old + 1", "word 0",
	"not within the items", "not within the items", "no L25 class|DATA write outside|unaligned", "not PCIe core", "L1 flow create", "cmd is not|type is not", "pointer", "unknown space",
	"L2 ioctl payload: command", "in transit", "already created", "exactly one|batch above", "unreadable|differs from memory|is not at|not at the write slot|next flow commit", "layout is invalid",
	"doorbell is still due", "already written|not at the write slot", "L1 |L3 |L4 |type is not", "pointer", "length above", "request id 0", "modulo its depth|beyond the ring|ring is full", "differs from memory or the upper half", "at or above the depth" };
static unsigned bm_app[M_NMUT], bm_ref[M_NMUT], bat_acc, bat_cls[SWL25W_NCLASS + 1];
static char bm_why[M_NMUT][12][96];
static unsigned bm_nwhy[M_NMUT];
static struct swl25w B;

static uint32_t *bptr(int space, uint32_t off) { return space == SWL25W_SP_PCIE ? NULL : mem(space == SWL25W_SP_HOST ? SWG_SP_HOST : SWG_SP_DATA, off); }
static int judge(int space, uint32_t off, uint32_t v, const char **why)
{
	const uint32_t *p = bptr(space, off);

	return swl25w_ok(&B, space, off, v, p != NULL ? *p : 0u, why);
}
/* a legitimate store: must be accepted with class `want`; it is then made and noted */
static void legit(int space, uint32_t off, uint32_t v, int want)
{
	const char *why = "";
	uint32_t *p = bptr(space, off);
	const uint32_t old = p != NULL ? *p : 0u;
	int c = judge(space, off, v, &why);

	if (c != want) {
		fprintf(stderr, "FAIL battery: legit store sp %d off %#x v %#x was class %d (want %d): %s\n", space, off, v, c, want, why);
		fails++;
		return;
	}
	bat_acc++; bat_cls[c]++;
	if (p != NULL)
		*p = v;
	swl25w_done(&B, space, off, v, old, c);
}
/* a mutant store: must be refused */
static int why_ok(const char *want, const char *why)
{
	char b[160], *t;

	snprintf(b, sizeof b, "%s", want);
	for (t = strtok(b, "|"); t != NULL; t = strtok(NULL, "|"))
		if (strstr(why, t) != NULL)
			return 1;
	return 0;
}
static void mut_res(int id, int space, uint32_t off, uint32_t v, int c, const char *why);
static void mut(int id, int space, uint32_t off, uint32_t v)
{
	const char *why = "";
	int c = judge(space, off, v, &why);

	bm_app[id]++;
	mut_res(id, space, off, v, c, why);
}
static void mut_res(int id, int space, uint32_t off, uint32_t v, int c, const char *why)
{
	if (c >= 0 || !why_ok(MWANT[id], why)) {
		fprintf(stderr, "FAIL battery: mutant '%s' %s: sp %d off %#x v %#x class %d: %s\n", MNAME[id], c >= 0 ? "accepted" : "killed by another check", space, off, v, c, why);
		fails++;
	} else {
		unsigned j;

		bm_ref[id]++;
		for (j = 0; j < bm_nwhy[id] && strncmp(bm_why[id][j], why, 95) != 0; j++)
			;
		if (j == bm_nwhy[id] && j < 12u) {
			snprintf(bm_why[id][j], 96, "%s", why);
			bm_nwhy[id]++;
		}
	}
}
/* a mutant of a word as it would be stored the first time: the word is 0 in memory while judged (the rest of the item stays) */
static void mutw(int id, int space, uint32_t off, uint32_t v)
{
	uint32_t *p = bptr(space, off), sv = *p;

	*p = 0u;
	mut(id, space, off, v);
	*p = sv;
}
/* a mutant judged with an explicit old word */
static void muto(int id, int space, uint32_t off, uint32_t v, uint32_t old)
{
	const char *why = "";
	int c = swl25w_ok(&B, space, off, v, old, &why);

	bm_app[id]++;
	mut_res(id, space, off, v, c, why);
}
/* a store that must be accepted, judged only (not made) */
static void acc(int space, uint32_t off, uint32_t v, int want)
{
	const char *why = "";
	int c = judge(space, off, v, &why);

	if (c != want) {
		fprintf(stderr, "FAIL battery: store sp %d off %#x v %#x was class %d (want %d): %s\n", space, off, v, c, want, why);
		fails++;
	}
}
/* Part A (sprint-wifi 2026-11-09): swl25w_ok_blk must give the verdict of the per-word sequence it replaces: the same words judged one by one with swl25w_ok, each accepted word MADE (memory
 * + swl25w_done) before the next is judged, as the per-word executor does. Run on the battery's own memory and state, both restored afterwards; any difference in accept/refuse or class fails. */
static unsigned beq_n, beq_acc, beq_ref;
static void blk_eq(int space, uint32_t off, const uint32_t *v, unsigned nw, const char *tag)
{
	static uint32_t old[1100], sv[1100];
	const struct swl25w save = B;
	const char *why = "", *wb = "";
	unsigned k;
	int per = 0, c, cb;
	uint32_t *p;

	for (k = 0; k < nw; k++) {
		p = bptr(space, off + 4u * k);
		old[k] = sv[k] = p != NULL ? *p : 0u;
	}
	cb = swl25w_ok_blk(&B, space, off, v, old, nw, &wb);
	for (k = 0; k < nw; k++) {
		p = bptr(space, off + 4u * k);
		c = swl25w_ok(&B, space, off + 4u * k, v[k], p != NULL ? *p : 0u, &why);
		if (c < 0) { per = -1; break; }
		if (k == 0u) per = c;
		else if (c != per) { per = -2; break; }
		if (p != NULL) *p = v[k];
		swl25w_done(&B, space, off + 4u * k, v[k], old[k], c);
	}
	for (k = 0; k < nw; k++) {
		p = bptr(space, off + 4u * k);
		if (p != NULL) *p = sv[k];
	}
	B = save;
	beq_n++;
	if ((per >= 0) != (cb >= 0) || (per >= 0 && per != cb)) {
		fprintf(stderr, "FAIL blk-eq %s: sp %d off %#x nw %u: per-word %d (%s) vs batch %d (%s)\n", tag, space, off, nw, per, per >= 0 ? "" : why, cb, wb);
		fails++;
	} else if (cb >= 0) {
		beq_acc++;
	} else {
		beq_ref++;
	}
}
static void blk_eq_item(int space, uint32_t base, const uint8_t *it, unsigned nw)
{
	static const unsigned bits[4] = { 0u, 7u, 16u, 31u };
	uint32_t w[16] = { 0 }, m[17] = { 0 };
	unsigned k, b;

	for (k = 0; k < nw; k++)
		w[k] = (uint32_t)it[4u * k] | (uint32_t)it[4u * k + 1u] << 8 | (uint32_t)it[4u * k + 2u] << 16 | (uint32_t)it[4u * k + 3u] << 24;
	blk_eq(space, base, w, nw, "item");
	for (k = 0; k < nw; k++)
		for (b = 0; b < 4u; b++) {
			memcpy(m, w, 4u * nw);
			m[k] ^= 1u << bits[b];
			blk_eq(space, base, m, nw, "item bit flip");
		}
	blk_eq(space, base + 4u, w + 1, nw - 1u, "item from word 1");
	memcpy(m, w, 4u * nw); m[nw] = w[0];          /* review perf-c F4: a plausible word 0 (type/epoch) for the next item, so only the same-slot rule can refuse it */
	blk_eq(space, base, m, nw + 1u, "item + one word of the next");
}
static uint32_t w32(const uint8_t *it, unsigned k) { return (uint32_t)it[4u * k] | (uint32_t)it[4u * k + 1u] << 8 | (uint32_t)it[4u * k + 2u] << 16 | (uint32_t)it[4u * k + 3u] << 24; }
/* one item: its words stored in order as class `cls`; `em[k]` = the bits of word k the class fixes exactly: a flipped bit there is a mutant (M_WORD_FLIP), a wrong epoch too */
static void put_item(int space, uint32_t base, const uint8_t *it, unsigned nw, int cls, const uint32_t *em, int epoch_word)
{
	unsigned k;

	if (cls == SWL25W_L3 || cls == SWL25W_L4)
		blk_eq_item(space, base, it, nw);                       /* Part A: the batched verdict of this item and its mutants == the per-word one */
	for (k = 0; k < nw; k++) {
		const uint32_t v = w32(it, k);

		if (em != NULL) {
			unsigned bit;

			for (bit = 0; bit < 32u; bit++)         /* every fixed bit, not only the lowest (T-5173 r1 F5) */
				if ((em[k] >> bit & 1u) != 0u)
					mut(M_WORD_FLIP, space, base + 4u * k, v ^ 1u << bit);
		}
		if (k == 0u && epoch_word)
			mut(M_EPOCH, space, base, v + (1u << 24));
		legit(space, base + 4u * k, v, cls);
	}
}
static void commit_h2d(unsigned slot_idx, uint32_t new16, unsigned ring_id)
{
	const uint32_t off = (uint32_t)(H2DW - G1PA) + 4u * slot_idx, old = *bptr(SWL25W_SP_HOST, off);
	const uint32_t nv = (old & 0xFFFF0000u) | new16;

	mut(M_IDX2, SWL25W_SP_HOST, off, (old & 0xFFFF0000u) | (new16 + 1u));
	mut(M_IDX0, SWL25W_SP_HOST, off, old);
	mut(M_IDX_UPPER, SWL25W_SP_HOST, off, nv | 0x10000u);
	legit(SWL25W_SP_HOST, off, nv, SWL25W_L7);
	mut(M_DB_RING, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | (ring_id ^ 1u) << 16 | new16);
	mut(M_DB_RING, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD000000u | ring_id << 16 | new16);
	mut(M_DB_IDX, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | ring_id << 16 | (new16 + 1u));
	mut(M_DB_OFF, SWL25W_SP_PCIE, SWL25W_DB0_OFF + 4u, 0xFF000000u | ring_id << 16 | new16);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | ring_id << 16 | new16, SWL25W_L9);
	mut(M_DB_TWICE, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | ring_id << 16 | new16);
}
static void commit_d2h(unsigned ring, uint32_t new16)
{
	const uint32_t off = (uint32_t)(D2HR - G1PA) + 4u * (ring - 2u), old = *bptr(SWL25W_SP_HOST, off);

	mut(M_CONSUME2, SWL25W_SP_HOST, off, (old & 0xFFFF0000u) | (new16 + 1u));
	legit(SWL25W_SP_HOST, off, (old & 0xFFFF0000u) | new16, SWL25W_L8);
	mut(M_DB_RING, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD000000u | (ring == 2u ? 3u : 2u) << 16 | new16);
	mut(M_DB_RING, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | ring << 16 | new16);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD000000u | ring << 16 | new16, SWL25W_L9);
}
/* the battery's accessor: the same memory, one word can be made to fail */
static int bt_fail_sp;
static uint32_t bt_fail_off;
static int bt_rd(void *ctx, int space, uint32_t off, uint32_t *v)
{
	if (space == bt_fail_sp && off == bt_fail_off)
		return -1;
	return rp_rd(ctx, space, off, v);
}
static void b_world(void)
{
	memset(g1, 0, sizeof g1);
	memset(dat, 0, sizeof dat);
	wl_cfg(&B);
	B.rd = bt_rd; bt_fail_sp = 0;
	memcpy(B.mac, MAC, 6); memcpy(B.bssid, GWM, 6); B.mac_set = B.bssid_set = 1u;
}
static uint32_t cur_wr(unsigned slot) { return *bptr(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u * slot) & 0xFFFFu; }
static void battery(void)
{
	static const uint32_t em_all[14] = { ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u };
	uint8_t it[56];
	unsigned n, k, ioct = 0x60000u;
	uint32_t off, em[14];

	b_world();
	mut(M_DB_NONE, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000001u);                                                         /* no index store yet */
	mut(M_DB_NONE, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD020001u);
	/* ---- L6 + L2: SET_VAR wsec_key (20 payload words), response buffer re-post, event buffer re-post, on ring 0 slots 0..2 */
	legit(SWL25W_SP_HOST, ioct, 0x63657377u, SWL25W_L6);      /* "wsec" */
	legit(SWL25W_SP_HOST, ioct + 4u, 0x79656B5Fu, SWL25W_L6); /* "_key" */
	legit(SWL25W_SP_HOST, ioct + 8u, 0x11111100u, SWL25W_L6); /* NUL + free key bytes */
	for (k = 3u; k < 5u; k++)
		legit(SWL25W_SP_HOST, ioct + 4u * k, 0x11111111u * (k + 1u), SWL25W_L6);
	mut(M_FREE_OUT, SWL25W_SP_HOST, ioct + SWL25W_IOCT_WORDS * 4u, 1u);                    /* word 355: the first word past the 1420 B payload buffer */
	mut(M_FREE_OUT, SWL25W_SP_HOST, ioct - 4u, 1u);
	legit(SWL25W_SP_HOST, ioct + (SWL25W_IOCT_WORDS - 1u) * 4u, 0xDEADBEEFu, SWL25W_L6);   /* the last allowed word */
	swd4b_item_ioctl(it, SWD4B_WLC_SET_VAR, 7u, 173u, 173u, G1PA + ioct, (uint8_t)((254u + 0u) % 253u));
	mut(M_IOCT_CMD, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 8u, 264u);                  /* before word 0 of the item exists: refused for the slot's type */
	memset(em, 0, sizeof em);
	em[0] = ~0xFFu; em[1] = ~0u; em[4] = 0xFFFF0000u; em[5] = ~0u;  /* word 0's type byte: another legal type is judged at the commit */
	 em[6] = ~0u; em[7] = ~0u; em[8] = ~0u; em[9] = ~0u;
	put_item(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA), it, 10u, SWL25W_L2, em, 1);
	mutw(M_IOCT_CMD, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 8u, 264u);
	mutw(M_IOCT_CMD, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 8u, 0u);
	mutw(M_PTR_OUT, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 24u, swl25w_lo(G1PA + 0x70000u));                 /* the payload pointer at RESPBUF */
	mutw(M_PTR_OUT, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 28u, swl25w_hi(G1PA) + 1u);
	mutw(M_PTR_ITEM, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 24u, swl25w_lo(G1PA + ioct) + 40u);
	mutw(M_LEN, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 12u, 1421u << 16);
	mutw(M_LEN, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 16u, 1421u);
	{
		uint32_t *wp3 = bptr(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 12u), *wp4 = bptr(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 16u), s3 = *wp3, s4 = *wp4;

		*wp3 = 0u; *wp4 = 0u;
		acc(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 12u, 1420u << 16, SWL25W_L2);      /* the bound itself */
		acc(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 16u, 1420u, SWL25W_L2);
		*wp3 = s3; *wp4 = s4;
	}
	commit_h2d(0u, 1u, 0u);
	CHECK(B.ioct_busy == 1u);
	mut(M_BUSY, SWL25W_SP_HOST, ioct, 0x41414141u);                                                             /* the payload is frozen while the dongle may read it */
	/* the ack alone does not free it, the completion does (one batch of two: case_done) */
	*bptr(SWL25W_SP_HOST, (uint32_t)(RING2 - G1PA)) = SWD4B_MSG_IOCTL_ACK;
	*bptr(SWL25W_SP_HOST, (uint32_t)(RING2 - G1PA) + 24u) = SWD4B_MSG_IOCTL_CMPLT;
	*bptr(SWL25W_SP_HOST, (uint32_t)(RING2 - G1PA) + 24u + 12u) = 7u << 16;           /* trans id at +14 */
	*bptr(SWL25W_SP_HOST, (uint32_t)(D2HW - G1PA)) = 2u;
	legit(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 1u, SWL25W_L8);
	mut(M_PEND, SWL25W_SP_HOST, ioct, 0u);                                                                      /* a store between the index store and its doorbell */
	mut(M_PEND, SWL25W_SP_DATA, SWL25W_TX_OFF, 0u);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD020001u, SWL25W_L9);
	CHECK(B.ioct_busy == 1u);
	mut(M_BUSY, SWL25W_SP_HOST, ioct, 0x41414141u);
	legit(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 2u, SWL25W_L8);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD020002u, SWL25W_L9);
	CHECK(B.ioct_busy == 0u);
	legit(SWL25W_SP_HOST, ioct + 12u, 0u, SWL25W_L6);                                                           /* free again: the scrub */
	*bptr(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA)) = 0u;
	*bptr(SWL25W_SP_HOST, (uint32_t)(D2HW - G1PA)) = 0u;
	mut(M_STALE, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA), cur_wr(0) + 1u);                                          /* commits slot 1 whose item does not exist yet */
	swd4b_item_respbuf(it, 0x100u, 8192u, G1PA + 0x70000u + 3u * 8192u, (uint8_t)((254u + 1u) % 253u));
	memset(em, 0, sizeof em);
	em[0] = ~0xFFu; em[2] = ~0u; em[3] = ~0u; em[5] = 0u; em[6] = ~0u; em[7] = ~0u; em[8] = ~0u; em[9] = ~0u;
	off = (uint32_t)(RING0 - G1PA) + 40u;
	put_item(SWL25W_SP_HOST, off, it, 10u, SWL25W_L2, em, 1);
	mutw(M_RID0, SWL25W_SP_HOST, off + 4u, 0u);
	mutw(M_BUF_PTR, SWL25W_SP_HOST, off + 16u, swl25w_lo(G1PA + 0x70000u) + 8u * 8192u);                          /* the 9th buffer */
	mutw(M_BUF_PTR, SWL25W_SP_HOST, off + 16u, swl25w_lo(G1PA + 0x70000u) + 100u);                                 /* inside a buffer, not at its start */
	mutw(M_BUF_PTR, SWL25W_SP_HOST, off + 20u, swl25w_hi(G1PA) + 1u);
	commit_h2d(0u, 2u, 0u);
	swd4b_item_evbuf(it, 0x200u, 8192u, G1PA + 0x80000u + 7u * 8192u, (uint8_t)((254u + 2u) % 253u));
	put_item(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 80u, it, 10u, SWL25W_L2, em, 1);
	commit_h2d(0u, 3u, 0u);
	/* ---- L7 init, L1 flow create (MAC and BSSID enforced), not open yet: tx is refused */
	mut(M_NOTOPEN, SWL25W_SP_DATA, 0u, 0x0Fu);
	mut(M_NOTOPEN, SWL25W_SP_DATA, SWL25W_TX_OFF, 0x55u);
	{
		const uint32_t foff = (uint32_t)(H2DW - G1PA) + 8u;

		mut(M_NOTOPEN, SWL25W_SP_HOST, foff, 1u);                                                                   /* flow index := 1 before the create */
		legit(SWL25W_SP_HOST, foff, 0u, SWL25W_L7);
		mut(M_DB_NONE, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | 5u << 16);                                         /* the init asks for no doorbell */
	}
	swdp_build_flow_create(it, (uint8_t)((254u + 3u) % 253u), 0u, 2u, 0u, GWM, MAC, DAPA, 64u, 48u);
	{
		uint32_t flowem[14];

		memcpy(flowem, em_all, sizeof flowem);
		put_item(SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 120u, it, 10u, SWL25W_L1, flowem, 1);
	}
	mutw(M_MAC, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 120u + 8u, w32(it, 2) ^ 1u);
	mutw(M_MAC, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 120u + 12u, w32(it, 3) ^ 0x10000u);
	mutw(M_MAC, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 120u + 16u, w32(it, 4) ^ 0x80000000u);
	mutw(M_MAC, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 120u + 32u, swl25w_lo(DAPA) + 0x4000u);                     /* flow ring pointer = the tx buffers */
	commit_h2d(0u, 4u, 0u);
	CHECK(B.flow_created == 1u);
	mut(M_NOTOPEN, SWL25W_SP_DATA, 0u, SWL25W_MSG_TXPOST);                                                           /* committed, not answered yet: tx stays shut */
	mut(M_NOTOPEN, SWL25W_SP_DATA, SWL25W_TX_OFF, 1u);
	/* a second create, also before the dongle answered the first */
	mut(M_FLOW2, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 160u, 0x03u | ((254u + 4u) % 253u) << 24);
	B.flow_open = 1u;
	mut(M_FLOW2, SWL25W_SP_HOST, (uint32_t)(RING0 - G1PA) + 160u, 0x03u | ((254u + 4u) % 253u) << 24);
	mut(M_OPEN_INIT, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 8u, 0u);                                              /* the init store, once the flow ring is open */
	/* ---- L3: five rx posts */
	for (n = 0; n < 5u; n++) {
		const uint32_t base = (uint32_t)(RING1 - G1PA) + 32u * n;

		swdp_build_rxpost(it, (uint8_t)((254u + n) % 253u), SWL25W_RX_RID + n, DAPA + SWL25W_RX_OFF + (uint64_t)n * 2048u, 2048u);
		mut(M_PTR_ITEM, SWL25W_SP_HOST, base + 24u, w32(it, 6) + 2048u);
		mut(M_PTR_ITEM, SWL25W_SP_HOST, base + 24u, w32(it, 6) - 2048u);
		mut(M_PTR_OUT, SWL25W_SP_HOST, base + 24u, swl25w_lo(DAPA + DALEN));
		mut(M_PTR_OUT, SWL25W_SP_HOST, base + 24u, swl25w_lo(DAPA) + SWL25W_RX_OFF + 16u * 2048u);
		mut(M_PTR_OUT, SWL25W_SP_HOST, base + 28u, w32(it, 7) + 1u);
		mut(M_NOCOMMIT, SWL25W_SP_HOST, (uint32_t)(RING1 - G1PA) + 32u * (n + SWL25W_RXB_MAX), SWL25W_MSG_RXPOST | SWL25W_EP(n + SWL25W_RXB_MAX) << 24);   /* a right word 0, 32 slots ahead of the write slot: outside the batch window (patch 0357; the next slot is a legal batch item now) */
		{
			uint32_t rem[8];

			for (k = 0; k < 8u; k++)
				rem[k] = ~0u;
			put_item(SWL25W_SP_HOST, base, it, 8u, SWL25W_L3, rem, 1);
		}
		commit_h2d(1u, n + 1u, 1u);
	}
	/* ---- L5 + L4: three tx posts */
	for (n = 0; n < 3u; n++) {
		uint8_t fr[74];
		const uint64_t txa = DAPA + SWL25W_TX_OFF + (uint64_t)n * 2048u;

		memset(fr, 0, sizeof fr);
		memcpy(fr, GWM, 6); memcpy(fr + 6, MAC, 6); fr[12] = 0x88; fr[13] = 0x8e;
		mut(M_FREE_OUT, SWL25W_SP_DATA, SWL25W_TX_OFF + (n + 1u) * 2048u, 0xAAu);                                       /* the next buffer */
		mut(M_FREE_OUT, SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u + 2048u, 0xAAu);
		if (n > 0u)
			mut(M_FREE_OUT, SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u - 4u, 0xAAu);                                     /* the last word of the previous buffer */
		mut(M_RXREG, SWL25W_SP_DATA, SWL25W_RX_OFF, 0u);
		mut(M_RXREG, SWL25W_SP_DATA, SWL25W_RX_OFF + 16u * 2048u - 4u, 0x1234u);
		mut(M_RXREG, SWL25W_SP_DATA, SWL25W_RX_OFF + n * 2048u + 8u, 0x1234u);
		{       /* Part A: the payload as one batch: 15 words, the whole buffer, one word into the next buffer, and a start inside the buffer */
			static uint32_t pw[513];
			unsigned q;

			for (q = 0; q < 513u; q++)
				pw[q] = 0x5A5A0000u + q;
			blk_eq(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u, pw, 15u, "payload 15");
			blk_eq(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u, pw, 512u, "payload whole buffer");
			blk_eq(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u, pw, 513u, "payload + next buffer");
			blk_eq(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u + 1000u, pw, 262u, "payload from inside");
			blk_eq(SWL25W_SP_DATA, SWL25W_TX_OFF + (n + 1u) * 2048u, pw, 8u, "payload, next buffer (not the commit's)");
		}
		for (k = 0; k < 15u; k++)
			legit(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u + 4u * k, 0x5A5A0000u + k, SWL25W_L5);
		legit(SWL25W_SP_DATA, SWL25W_TX_OFF + n * 2048u + 2048u - 4u, 0x7E7E7E7Eu, SWL25W_L5);                         /* last word of the buffer */
		swdp_build_txpost(it, 48u, 0u, SWL25W_TX_RID + n, fr, sizeof fr, 0u, txa);
		memset(em, 0, sizeof em);
		em[0] = ~0u; em[1] = ~0u; em[5] = 0xFFFF0000u; em[6] = ~0u; em[7] = ~0u; em[8] = ~0u; em[9] = ~0u; em[10] = 0xFFFFu; em[11] = ~0u; em[12] = ~0u; em[13] = ~0u;
		mut(M_PTR_ITEM, SWL25W_SP_DATA, n * 48u + 32u, w32(it, 8) + 2048u);
		mut(M_PTR_OUT, SWL25W_SP_DATA, n * 48u + 32u, swl25w_lo(DAPA) + SWL25W_RX_OFF);
		mut(M_PTR_OUT, SWL25W_SP_DATA, n * 48u + 36u, w32(it, 9) + 1u);
		mut(M_FREE_OUT, SWL25W_SP_DATA, n * 48u + 40u, (1501u << 16));                                                  /* data length 1501 > 1514 - 14 */
		mut(M_FREE_OUT, SWL25W_SP_DATA, n * 48u + 40u, 0u);
		mut(M_NOCOMMIT, SWL25W_SP_DATA, (n + 1u) * 48u, w32(it, 0));
		put_item(SWL25W_SP_DATA, n * 48u, it, 12u, SWL25W_L4, em, 0);
		commit_h2d(2u, n + 1u, 5u);
	}
	/* ---- L8: ring 2, 3, 4 consumes after the dongle wrote one item each */
	mut(M_NOPEND, SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 1u);
	for (k = 0; k < 3u; k++) {
		*bptr(SWL25W_SP_HOST, (uint32_t)(D2HW - G1PA) + 4u * k) = 1u;                                                   /* the dongle's write index */
		commit_d2h(2u + k, 1u);
		mut(M_NOPEND, SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA) + 4u * k, 2u);
	}
	*bptr(SWL25W_SP_HOST, (uint32_t)(D2HW - G1PA)) = 5u;                                                                /* ring 2 wrap: read index 63 -> 0, the dongle is at 5 */
	*bptr(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA)) = 63u;
	mut(M_CONSUME2, SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 6u);                                                     /* a batch past the dongle's index */
	acc(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 5u, SWL25W_L8);                                                       /* a batch up to it */
	legit(SWL25W_SP_HOST, (uint32_t)(D2HR - G1PA), 0u, SWL25W_L8);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD020000u, SWL25W_L9);
	/* ---- ring 1 (sprint 2026-11-08): a re-post at slot 16 of buffer 3 is legal; buffer 16 does not exist; the pointer must be the buffer the request id names; slot 256 has no shadow */
	*bptr(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u) = 16u;
	{
		const uint32_t base = (uint32_t)(RING1 - G1PA) + 32u * 16u, b256 = (uint32_t)(RING1 - G1PA) + 32u * SWD_R1_ITEMS;
		unsigned k;

		swdp_build_rxpost(it, (uint8_t)((254u + 16u) % 253u), SWL25W_RX_RID + 16u, DAPA + SWL25W_RX_OFF + 16u * 2048u, 2048u);
		mut(M_SLOT16, SWL25W_SP_HOST, base + 4u, w32(it, 1));                                                              /* request id of a 17th buffer */
		swdp_build_rxpost(it, (uint8_t)((254u + 16u) % 253u), SWL25W_RX_RID + 3u, DAPA + SWL25W_RX_OFF + 3u * 2048u, 2048u);
		blk_eq_item(SWL25W_SP_HOST, base, it, 8u);              /* review perf-c F3: a re-post (slot 16, buffer 3): the batch takes the request id from its own word 1 */
		for (k = 0; k < 6u; k++) {
			legit(SWL25W_SP_HOST, base + 4u * k, w32(it, k), SWL25W_L3);
		}
		mut(M_PTR_ITEM, SWL25W_SP_HOST, base + 24u, w32(it, 6) + 2048u);                                                 /* the pointer of buffer 4 under the request id of buffer 3 */
		legit(SWL25W_SP_HOST, base + 24u, w32(it, 6), SWL25W_L3);
		legit(SWL25W_SP_HOST, base + 28u, w32(it, 7), SWL25W_L3);
		legit(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u, 17u, SWL25W_L7);
		legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF010011u, SWL25W_L9);
		*bptr(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u) = SWD_R1_ITEMS;
		mut(M_SLOT16, SWL25W_SP_HOST, b256, w32(it, 0));                                                                 /* past the ring (the next ring's memory) */
		mut(M_SLOT16, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u, SWD_R1_ITEMS + 1u);
	}
	*bptr(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 8u) = 16u;                                                         /* sprint 2026-11-08: the flow ring wraps at 64, flow slot n = tx buffer n % 16 */
	mut(M_SLOT16, SWL25W_SP_DATA, SWL25W_TX_OFF + 16u * 2048u, 1u);                                                     /* a 17th tx buffer does not exist */
	mut(M_SLOT16, SWL25W_SP_DATA, SWL25W_TX_OFF + 1u * 2048u, 1u);                                                      /* flow slot 16 names buffer 0, not 1 */
	acc(SWL25W_SP_DATA, SWL25W_TX_OFF + 0u * 2048u, 1u, SWL25W_L5);
	*bptr(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 8u) = 64u;
	mut(M_SLOT16, SWL25W_SP_DATA, 64u * 48u, SWL25W_MSG_TXPOST);                                                        /* past the 64 flow items */
	mut(M_SLOT16, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 8u, 65u);
	/* ---- everything else */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(H2DR - G1PA), 1u);                                                     /* the dongle-written H2D read index */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(D2HW - G1PA), 1u);                                                     /* the dongle-written D2H write index */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(RING2 - G1PA), 1u);                                                    /* a D2H ring item */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(RING4 - G1PA) + 8u, 1u);
	mut(M_OTHER_WORD, SWL25W_SP_HOST, 0u, 1u);
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(RING1 - G1PA) + SWD_R1_ITEMS * 32u, 1u);     /* sprint 2026-11-08: past the 2048 ring-1 slots (was 16) */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 12u, 1u);                                               /* H2D write slot 3: not a ring of L25 */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u * 40u, 1u);
	mut(M_OTHER_WORD, SWL25W_SP_DATA, DALEN - 4u, 1u);
	mut(M_OTHER_WORD, SWL25W_SP_HOST, 0x60000u + 2u, 1u);                                                               /* unaligned */
	mut(M_SPACE, 0, 0u, 0u);
	mut(M_SPACE, 7, 0u, 0u);
	mut(M_SPACE, 7, 0x60000u, 1u);                                                                                      /* valid as a host word */
	mut(M_SPACE, 0, SWL25W_TX_OFF, 1u);
	mut(M_DB_NONE, SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0u);                                                                 /* nothing due: word 0 is not "the due word" */
}
#include "wlan_l25_wl_host_cases.h"
/* ---------------------------------------------------------------- patch 0282: the seven re-join rows of swl25w_iovars, judged at the commit of the ioctl item (swl25w_ioct_ok) on a small synthetic memory:
 * item words (cmd, in/out lengths) of ring-0 slot 0 and the payload buffer; each mutant is a legitimate payload with ONE field changed, demanded refused with the reason that names it */
static uint32_t rjm[1024];
static unsigned rj_acc, rj_ref, rj_bad;
static int rj_rd(void *ctx, int space, uint32_t off, uint32_t *v)
{
	(void)ctx;
	if (space != SWL25W_SP_HOST || off >= sizeof rjm || (off & 3u) != 0u)
		return -1;
	*v = rjm[off / 4u];
	return 0;
}
/* judges {cmd, name, value[vlen]} as the item at slot 0; returns the reason (NULL = accepted) */
static const volatile uint16_t *rj_esync;       /* review 0292 F2: what the judge is told the sync_id of the escan START is (NULL = the boot scan's 0x1234) */
static const char *rj_judge(uint32_t cmd, const char *name, const uint8_t *val, uint32_t vlen, uint32_t wlen_override, int bssid_set)
{
	struct swl25w w;
	uint8_t *pb = (uint8_t *)rjm + 0x400u;
	const uint32_t nl = (uint32_t)strlen(name) + 1u, wlen = wlen_override != 0u ? wlen_override : nl + vlen;

	memset(rjm, 0, sizeof rjm);
	memset(&w, 0, sizeof w);
	w.rd = rj_rd; w.ring[0] = 0u; w.depth[0] = 128u; w.ioctbuf = 0x400u;
	w.bssid_set = (uint8_t)bssid_set; memcpy(w.bssid, GWM, 6);
	w.escan_sync = rj_esync;
	rjm[2] = cmd; rjm[3] = wlen << 16; rjm[4] = wlen;
	memcpy(pb, name, nl);
	if (vlen != 0u)
		memcpy(pb + nl, val, vlen);
	return swl25w_ioct_ok(&w, 0);
}
static void rj_want(const char *what, const char *why, const char *want)
{
	if (want == NULL) {
		rj_acc++;
		if (why != NULL) {
			fprintf(stderr, "FAIL rejoin row: '%s' refused: %s\n", what, why);
			rj_bad++;
		}
		return;
	}
	rj_ref++;
	if (why == NULL || strstr(why, want) == NULL) {
		fprintf(stderr, "FAIL rejoin row: mutant '%s' %s (want a reason with '%s', got '%s')\n", what, why == NULL ? "ACCEPTED" : "refused for another reason", want, why == NULL ? "-" : why);
		rj_bad++;
	}
}
static void rj_rows(void)
{
	uint8_t j[76], z[1200], v[4];
	static const uint8_t any[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	unsigned i;

	CHECK(SWL25W_NIOVARS == 23u);
	CHECK(swj_join_ext_v1(j, (const uint8_t *)"net", 3u, any) == 0);
	rj_want("join wildcard", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), NULL);
	rj_want("join wildcard, AP known", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 1), NULL);
	memcpy(j + 64, GWM, 6);
	rj_want("join the known AP", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 1), NULL);
	rj_want("join an AP, none known", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "BSSID is neither");
	j[69] ^= 1u;
	rj_want("join another AP", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 1), "BSSID is neither");
	memcpy(j + 64, any, 6);
	j[0] = 1u;       rj_want("join version byte", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "version/pad"); j[0] = 0u;
	j[3] = 1u;       rj_want("join pad byte", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "version/pad"); j[3] = 0u;
	j[4] = 0u;       rj_want("join SSID_len 0", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "SSID_len is not 1..32"); j[4] = 3u;
	j[4] = 33u;      rj_want("join SSID_len 33", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "SSID_len is not 1..32"); j[4] = 3u;
	j[5] = 1u;       rj_want("join SSID_len above 255", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "SSID_len is not 1..32"); j[5] = 0u;
	j[7] = 0x80u;    rj_want("join SSID_len negative", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "SSID_len is not 1..32"); j[7] = 0u;
	j[8 + 3] = 'x';  rj_want("join SSID byte after the length", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "after SSID_len"); j[8 + 3] = 0u;
	j[8 + 31] = 'x'; rj_want("join SSID last byte after the length", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "after SSID_len"); j[8 + 31] = 0u;
	j[4] = 32u; memset(j + 8, 'a', 32);
	rj_want("join SSID_len 32", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), NULL);
	j[4] = 3u; memset(j + 11, 0, 29);
	for (i = 40; i < 60u; i++) {
		const uint8_t save = j[i];

		j[i] = (uint8_t)(save ^ 0x01u);
		rj_want("join scan parameter byte", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "scan parameters");
		j[i] = save;
	}
	for (i = 60; i < 64u; i++) {
		j[i] = 1u;
		rj_want("join assoc version/flags", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "assoc version/flags");
		j[i] = 0u;
	}
	for (i = 70; i < 76u; i++) {
		j[i] = 1u;
		rj_want("join bssid_cnt / chanspec_num", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 0u, 0), "bssid_cnt / chanspec_num");
		j[i] = 0u;
	}
	rj_want("join 80 bytes", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 76u, 85u, 0), "not one of the L25 calls");
	rj_want("join 75 bytes", rj_judge(SWL25W_WLC_SET_VAR, "join", j, 75u, 0u, 0), "not one of the L25 calls");
	rj_want("join as a GET", rj_judge(SWL25W_WLC_GET_VAR, "join", j, 76u, 0u, 0), "not one of the L25 calls");
	rj_want("joim", rj_judge(SWL25W_WLC_SET_VAR, "joim", j, 76u, 0u, 0), "not one of the L25 calls");
	/* wpa_auth / auth / mfp: ONE value each on SET, zeros on GET */
	v[0] = 0x80u; v[1] = v[2] = v[3] = 0u;
	rj_want("wpa_auth 0x80", rj_judge(SWL25W_WLC_SET_VAR, "wpa_auth", v, 4u, 0u, 0), NULL);
	v[0] = 0x81u; rj_want("wpa_auth 0x81", rj_judge(SWL25W_WLC_SET_VAR, "wpa_auth", v, 4u, 0u, 0), "not the one value"); v[0] = 0x80u;
	v[0] = 0x00u; rj_want("wpa_auth 0", rj_judge(SWL25W_WLC_SET_VAR, "wpa_auth", v, 4u, 0u, 0), "not the one value"); v[0] = 0x80u;
	v[1] = 1u;    rj_want("wpa_auth 0x180", rj_judge(SWL25W_WLC_SET_VAR, "wpa_auth", v, 4u, 0u, 0), "not the one value"); v[1] = 0u;
	v[3] = 0x80u; rj_want("wpa_auth 0x80000080", rj_judge(SWL25W_WLC_SET_VAR, "wpa_auth", v, 4u, 0u, 0), "not the one value"); v[3] = 0u;
	memset(v, 0, sizeof v);
	rj_want("auth 0", rj_judge(SWL25W_WLC_SET_VAR, "auth", v, 4u, 0u, 0), NULL);
	rj_want("mfp 0", rj_judge(SWL25W_WLC_SET_VAR, "mfp", v, 4u, 0u, 0), NULL);
	for (i = 0; i < 4u; i++) {
		v[i] = 1u;
		rj_want("auth nonzero", rj_judge(SWL25W_WLC_SET_VAR, "auth", v, 4u, 0u, 0), "not the one value");
		rj_want("mfp nonzero", rj_judge(SWL25W_WLC_SET_VAR, "mfp", v, 4u, 0u, 0), "not the one value");
		v[i] = 0u;
	}
	rj_want("auth 8 bytes", rj_judge(SWL25W_WLC_SET_VAR, "auth", z, 8u, 0u, 0), "not one of the L25 calls");
	memset(z, 0, sizeof z);
	rj_want("wpa_auth GET", rj_judge(SWL25W_WLC_GET_VAR, "wpa_auth", z, 4u, 0u, 0), NULL);
	rj_want("auth GET", rj_judge(SWL25W_WLC_GET_VAR, "auth", z, 4u, 0u, 0), NULL);
	rj_want("mfp GET", rj_judge(SWL25W_WLC_GET_VAR, "mfp", z, 4u, 0u, 0), NULL);
	for (i = 0; i < 4u; i++) {
		z[i] = 1u;
		rj_want("wpa_auth GET with data", rj_judge(SWL25W_WLC_GET_VAR, "wpa_auth", z, 4u, 0u, 0), "not one of the L25 calls");
		rj_want("auth GET with data", rj_judge(SWL25W_WLC_GET_VAR, "auth", z, 4u, 0u, 0), "not one of the L25 calls");
		rj_want("mfp GET with data", rj_judge(SWL25W_WLC_GET_VAR, "mfp", z, 4u, 0u, 0), "not one of the L25 calls");
		z[i] = 0u;
	}
	/* ALL-ON T-32: SET mkeep_alive, the 11 B null-frame keep-alive header, exactly one allowed content; bytes typed from research 138 (not from the builder) */
	{
		static const uint8_t ka[11] = { 0x01, 0x00, 0x0b, 0x00, 0x30, 0x75, 0x00, 0x00, 0x00, 0x00, 0x00 };
		uint8_t k[12] = { 0 };

		memcpy(k, ka, sizeof ka);
		rj_want("mkeep_alive 30000 ms id 0", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 11u, 0u, 0), NULL);
		for (i = 0; i < 11u; i++) {
			k[i] ^= 0x01u;
			rj_want("mkeep_alive one byte off", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 11u, 0u, 0), "30000 ms null-frame keep-alive");
			k[i] = ka[i];
		}
		memcpy(k, ka, sizeof ka); k[4] = 0u; k[5] = 0u;
		rj_want("mkeep_alive period 0 (stop)", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 11u, 0u, 0), "30000 ms null-frame keep-alive");
		memcpy(k, ka, sizeof ka); k[7] = 0x80u;
		rj_want("mkeep_alive immediate bit", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 11u, 0u, 0), "30000 ms null-frame keep-alive");
		memcpy(k, ka, sizeof ka);
		rj_want("mkeep_alive 10 bytes", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 10u, 0u, 0), "not one of the L25 calls");
		rj_want("mkeep_alive 12 bytes", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_alive", k, 12u, 0u, 0), "not one of the L25 calls");
		rj_want("mkeep_alive as a GET", rj_judge(SWL25W_WLC_GET_VAR, "mkeep_alive", k, 11u, 0u, 0), "not one of the L25 calls");
		rj_want("mkeep_aliv", rj_judge(SWL25W_WLC_SET_VAR, "mkeep_aliv", k, 11u, 0u, 0), "not one of the L25 calls");
	}
	/* patch 0363 (LOWCLK B3, research 161 s.3): three read-only GETs. WLC_GET_PM (85): no name, 4 zero bytes in = out (the helper adds the NUL of the empty name: vlen = 3); SET_PM (86) and every other
	 * length / data byte is refused. The ring-0 request word 2 (the command) admits 85 and refuses 86 and WLC_SET_COUNTRY (84) */
	rj_want("GET_PM 4 zero bytes", rj_judge(85u, "", z, 3u, 0u, 0), NULL);
	rj_want("SET_PM (86) has no row", rj_judge(86u, "", z, 3u, 0u, 0), "not one of the L25 calls");
	rj_want("GET_PM 8 bytes", rj_judge(85u, "", z, 7u, 0u, 0), "not one of the L25 calls");
	rj_want("GET_PM 1 byte", rj_judge(85u, "", z, 0u, 0u, 0), "not one of the L25 calls");
	rj_want("GET_PM with a name", rj_judge(85u, "x", z, 2u, 0u, 0), "not one of the L25 calls");
	rj_want("GET_PM out length differs", rj_judge(85u, "", z, 3u, 8u, 0), "not one of the L25 calls");
	for (i = 0; i < 3u; i++) {
		z[i] = 1u;
		rj_want("GET_PM with a data byte (SET semantics)", rj_judge(85u, "", z, 3u, 0u, 0), "not one of the L25 calls");
		z[i] = 0u;
	}
	{
		struct swl25w w85;
		int cls85 = 0;

		memset(rjm, 0, sizeof rjm);
		memset(&w85, 0, sizeof w85);
		w85.rd = rj_rd; w85.ring[0] = 0u; w85.depth[0] = 128u; w85.ioctbuf = 0x400u;
		rjm[0] = SWL25W_MSG_IOCTL_REQ | 0x80u << 16;
		CHECK(swl25w_ring0_word(&w85, 0u, 2u, 85u, &cls85) == NULL);
		CHECK(swl25w_ring0_word(&w85, 0u, 2u, 86u, &cls85) != NULL && strstr(swl25w_ring0_word(&w85, 0u, 2u, 86u, &cls85), "cmd is not GET_VAR") != NULL);
		CHECK(swl25w_ring0_word(&w85, 0u, 2u, 84u, &cls85) != NULL && swl25w_ring0_word(&w85, 0u, 2u, 84u, &cls85)[0] != '\0');
		CHECK(swl25w_ring0_word(&w85, 0u, 2u, SWL25W_WLC_GET_VAR, &cls85) == NULL && swl25w_ring0_word(&w85, 0u, 2u, 137u, &cls85) != NULL);
	}
	/* "country" GET_VAR: name 8 + 12 zeros = 20 B; the SET (a 20 B SET_VAR, or WLC_SET_COUNTRY 84) has no row */
	rj_want("country GET", rj_judge(SWL25W_WLC_GET_VAR, "country", z, 12u, 0u, 0), NULL);
	rj_want("country SET_VAR", rj_judge(SWL25W_WLC_SET_VAR, "country", z, 12u, 0u, 0), "not one of the L25 calls");
	rj_want("WLC_SET_COUNTRY (84)", rj_judge(84u, "", z, 3u, 0u, 0), "not one of the L25 calls");
	rj_want("country 11 bytes", rj_judge(SWL25W_WLC_GET_VAR, "country", z, 11u, 0u, 0), "not one of the L25 calls");
	rj_want("country 13 bytes", rj_judge(SWL25W_WLC_GET_VAR, "country", z, 13u, 0u, 0), "not one of the L25 calls");
	rj_want("countr", rj_judge(SWL25W_WLC_GET_VAR, "countr", z, 13u, 0u, 0), "not one of the L25 calls");
	for (i = 0; i < 12u; i++) {
		z[i] = 'U';
		rj_want("country GET with data (a country code to set)", rj_judge(SWL25W_WLC_GET_VAR, "country", z, 12u, 0u, 0), "not one of the L25 calls");
		z[i] = 0u;
	}
	/* "chanspecs" GET_VAR: name 10 + 1190 zeros = 1200 B (the all-channel form); the per-band input form (a chanspec in the first bytes) and every other length is refused */
	rj_want("chanspecs GET 1200", rj_judge(SWL25W_WLC_GET_VAR, "chanspecs", z, 1190u, 0u, 0), NULL);
	rj_want("chanspecs 1199", rj_judge(SWL25W_WLC_GET_VAR, "chanspecs", z, 1189u, 0u, 0), "not one of the L25 calls");
	rj_want("chanspecs 1201", rj_judge(SWL25W_WLC_GET_VAR, "chanspecs", z, 1190u, 1201u, 0), "not one of the L25 calls");
	rj_want("chanspecs SET_VAR", rj_judge(SWL25W_WLC_SET_VAR, "chanspecs", z, 1190u, 0u, 0), "not one of the L25 calls");
	rj_want("chanspec (singular) 1200 B", rj_judge(SWL25W_WLC_GET_VAR, "chanspec", z, 1191u, 0u, 0), "not one of the L25 calls");
	for (i = 0; i < 1190u; i += (i < 8u ? 1u : 131u)) {
		z[i] = 0x24u;
		rj_want("chanspecs GET with a chanspec input (per-band form)", rj_judge(SWL25W_WLC_GET_VAR, "chanspecs", z, 1190u, 0u, 0), "not one of the L25 calls");
		z[i] = 0u;
	}
	z[1189] = 1u; rj_want("chanspecs last byte", rj_judge(SWL25W_WLC_GET_VAR, "chanspecs", z, 1190u, 0u, 0), "not one of the L25 calls"); z[1189] = 0u;
	rj_want("other iovar", rj_judge(SWL25W_WLC_SET_VAR, "pcie_bus_tput", z, 16u, 0u, 0), "not one of the L25 calls");
	rj_want("wsec SET still free", rj_judge(SWL25W_WLC_SET_VAR, "wsec", (const uint8_t *)"\x04\x00\x00\x00", 4u, 0u, 0), NULL);
	rj_want("wsec GET still ok", rj_judge(SWL25W_WLC_GET_VAR, "wsec", z, 4u, 0u, 0), NULL);
	/* patch 0288: GET_BSS_INFO (136): 1024 B in = out, the length word 1024 (00 04 00 00) and zeros; read-only */
	{
		static uint8_t bi[1023];

		memset(bi, 0, sizeof bi);
		bi[0] = 0x04u;
		rj_want("bss_info 1024", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), NULL);
		bi[0] = 0x05u;
		rj_want("bss_info another length word", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), "not the length word 1024 and zeros");
		bi[0] = 0x04u; bi[1] = 1u;
		rj_want("bss_info length word high byte", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), "not the length word 1024 and zeros");
		bi[1] = 0u; bi[3] = 1u;       /* payload byte 4 = the first byte after the length word */
		rj_want("bss_info byte after the word", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), "not the length word 1024 and zeros");
		bi[3] = 0u; bi[1022] = 1u;    /* payload byte 1023 */
		rj_want("bss_info last byte", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), "not the length word 1024 and zeros");
		bi[1022] = 0u; bi[1019] = 1u; /* payload byte 1020: the last word */
		rj_want("bss_info last word", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 0u, 0), "not the length word 1024 and zeros");
		bi[1019] = 0u;
		rj_want("bss_info 1020 bytes", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1019u, 0u, 0), "not one of the L25 calls");
		rj_want("bss_info 1028 bytes", rj_judge(SWL25W_WLC_GET_BSS_INFO, "", bi, 1023u, 1028u, 0), "not one of the L25 calls");
		rj_want("bss_info as a SET_VAR", rj_judge(SWL25W_WLC_SET_VAR, "", bi, 1023u, 0u, 0), "not one of the L25 calls");
		rj_want("bss_info with a name", rj_judge(SWL25W_WLC_GET_BSS_INFO, "x", bi, 1022u, 0u, 0), "not one of the L25 calls");
		rj_want("GET_VAR with the length word", rj_judge(SWL25W_WLC_GET_VAR, "", bi, 1023u, 0u, 0), "not one of the L25 calls");
	}
	/* patch 0292: SET escan (86 B = "escan\0" + the 80 B passive all-channel START of swg_e_escan_arg, chans 0): the builder's output is accepted; EVERY single changed byte, every other length / command / version is refused with the reason that names it */
	{
		uint8_t e[96];
		unsigned vers;

		for (vers = 2u; vers <= 3u; vers++) {
			swg_e_escan_arg(e, vers, 0, 0x1234u);
			rj_want("escan builder output", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), NULL);
			for (i = 0; i < 80u; i++) {
				const uint8_t save = e[i];

				e[i] = (uint8_t)(save ^ 0x01u);
				rj_want("escan one byte changed", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
				e[i] = (uint8_t)(save ^ 0x80u);
				rj_want("escan one byte changed (high bit)", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
				e[i] = save;
			}
		}
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[0] = e[8] = 4u;    rj_want("escan version 4", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "scan version is not 2 or 3");
		e[0] = e[8] = 0u;    rj_want("escan version 0", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "scan version is not 2 or 3");
		e[0] = e[8] = 1u;    rj_want("escan version 1", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "scan version is not 2 or 3");
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[0] = 3u;           rj_want("escan versions differ", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[10] = 80u;         rj_want("escan params length 80 (the first cut of the boot scan, refused by the firmware: EPERM)", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		e[10] = 72u; e[1] = 1u; rj_want("escan version high byte", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[4] = 3u;           rj_want("escan action ABORT (3): not allowed", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		e[4] = 1u; e[56] = 0u; rj_want("escan ACTIVE scan", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		swg_e_escan_arg(e, 2u, 1, 0x1234u);       /* the 96 B boot form, kept as a negative case */
		rj_want("escan seven channels (the 96 B boot form) is refused by length", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 96u, 0u, 0), "not one of the L25 calls");
		rj_want("escan seven channels cut to 80 B (channel_num 7, no list)", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[76] = 1u; rj_want("escan channel_num 1 (list missing)", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		e[76] = 7u; rj_want("escan channel_num 7 (list missing)", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		e[76] = 0u; e[77] = 1u; rj_want("escan channel_num high byte", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		e[77] = 0u; e[79] = 1u; rj_want("escan last byte", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		swg_e_escan_arg(e, 2u, 0, 0x1234u);
		e[80] = 0xAAu; e[95] = 0x55u;       /* bytes behind the 80 B payload are not part of it: the judge must not look at them (a loop to 96 would) */
		rj_want("escan bytes behind the 80 B payload are ignored", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 96u, 86u, 0), NULL);       /* 96 B in the buffer, 86 B declared (name 6 + 80) */
		e[80] = e[95] = 0u;
		rj_want("escan 79 bytes", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 79u, 0u, 0), "not one of the L25 calls");
		rj_want("escan 81 bytes", rj_judge(SWL25W_WLC_SET_VAR, "escan", z, 81u, 0u, 0), "not one of the L25 calls");
		rj_want("escan as GET_VAR", rj_judge(SWL25W_WLC_GET_VAR, "escan", e, 80u, 0u, 0), "not one of the L25 calls");
		rj_want("escam", rj_judge(SWL25W_WLC_SET_VAR, "escam", e, 80u, 0u, 0), "not one of the L25 calls");
		rj_want("escan_abort", rj_judge(SWL25W_WLC_SET_VAR, "escan_abort", e, 80u, 0u, 0), "not one of the L25 calls");
		rj_want("escan with wlen 85", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 85u, 0), "not one of the L25 calls");
		/* review 0292 F2: the judge is told the sync_id of THIS scan and accepts exactly it (0x1234 .. 0x1333); a payload with the boot id or another scan's id is refused, an expected id outside the range refuses everything */
		{
			static volatile uint16_t es;
			static const uint16_t good[] = { 0x1234u, 0x1235u, 0x1236u, 0x1333u };
			unsigned a, b;

			rj_esync = &es;
			for (a = 0; a < 4u; a++) {
				for (b = 0; b < 4u; b++) {
					es = good[a];
					swg_e_escan_arg(e, 3u, 0, good[b]);
					rj_want("escan expected id vs payload id", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), a == b ? NULL : "L2 escan payload: not the passive");
				}
			}
			es = 0x1236u;
			swg_e_escan_arg(e, 3u, 0, 0x1236u);
			e[6] ^= 1u; rj_want("escan sync low byte", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload"); e[6] ^= 1u;
			e[7] ^= 1u; rj_want("escan sync high byte", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload"); e[7] ^= 1u;
			es = 0x1233u; swg_e_escan_arg(e, 3u, 0, 0x1233u);
			rj_want("expected id below the range", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "expected sync_id outside the range");
			es = 0x1334u; swg_e_escan_arg(e, 3u, 0, 0x1334u);
			rj_want("expected id above the range", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "expected sync_id outside the range");
			es = 0u; swg_e_escan_arg(e, 3u, 0, 0u);
			rj_want("expected id 0", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "expected sync_id outside the range");
			rj_esync = NULL;
			swg_e_escan_arg(e, 3u, 0, 0x1235u);
			rj_want("no expected id wired: a keep scan id is refused", rj_judge(SWL25W_WLC_SET_VAR, "escan", e, 80u, 0u, 0), "L2 escan payload");
		}
	}
	CHECK(rj_bad == 0u && rj_acc >= 14u && rj_ref >= 70u + 2u * 80u * 2u);
	printf("rejoin rows: accepted %u, refused %u (every refusal for the reason that names it)\n", rj_acc, rj_ref);
}


/* ---------------------------------------------------------------- patch 0357: the rx pool (rx_n buffers) and the batched rx post (ring-1 items before ONE index) */
static unsigned rxb_refused;
static void rxb_no(int space, uint32_t off, uint32_t v, const char *want)
{
	const char *why = "";
	const int c = judge(space, off, v, &why);

	if (c >= 0 || !why_ok(want, why)) {
		fprintf(stderr, "FAIL rxb: store sp %d off %#x v %#x was %s (class %d): %s (want %s)\n", space, off, v, c >= 0 ? "accepted" : "refused for another reason", c, why, want);
		fails++;
	} else {
		rxb_refused++;
	}
}
static void rxb_item(uint8_t it[32], uint32_t slot, uint32_t b, uint32_t wrap)
{
	swdp_build_rxpost(it, (uint8_t)((254u + wrap * SWD_R1_ITEMS + slot) % 253u), SWL25W_RX_RID + b, DAPA + SWL25W_RX_OFF + (uint64_t)b * 2048u, 2048u);
}
static void rxb_put(uint32_t slot, uint32_t b, uint32_t wrap)         /* the 8 words of the rx post of buffer b at ring-1 slot `slot`, stored in order; every word must be class L3 */
{
	uint8_t it[32];
	unsigned k;

	rxb_item(it, slot, b, wrap);
	for (k = 0; k < 8u; k++)
		legit(SWL25W_SP_HOST, (uint32_t)(RING1 - G1PA) + 32u * slot + 4u * k, w32(it, k), SWL25W_L3);
}
static void rxb_commit(uint32_t new16)                                  /* the one index store of the batch and its doorbell */
{
	legit(SWL25W_SP_HOST, (uint32_t)(H2DW - G1PA) + 4u, new16, SWL25W_L7);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF010000u | new16, SWL25W_L9);
	CHECK(B.pend_db == 0u);
}
static void rxb_cases(void)
{
	const uint32_t dep = SWD_R1_ITEMS, h2d1 = (uint32_t)(H2DW - G1PA) + 4u, rd1 = (uint32_t)(H2DR - G1PA) + 4u, base = (uint32_t)(RING1 - G1PA);
	uint32_t k;
	uint8_t it[32];

	/* ---- the layout clauses: rx_n 0 = 16 buffers (the old layout), 16..255 inside the block, else clause 14 */
	b_world();
	CHECK(swl25w_layout_bad(&B) == 0 && swl25w_rxn(&B) == 16u);
	B.data_len = SWL25W_RX_OFF + 255u * 2048u; B.rx_n = 255u;
	CHECK(swl25w_layout_bad(&B) == 0 && swl25w_rxn(&B) == 255u);
	B.data_len -= 4u; CHECK(swl25w_layout_bad(&B) == 14);                  /* the block is a word short of 255 buffers */
	B.data_len = SWL25W_RX_OFF + 255u * 2048u; B.rx_n = 256u; CHECK(swl25w_layout_bad(&B) == 14);
	B.rx_n = 15u; CHECK(swl25w_layout_bad(&B) == 14);
	B.rx_n = 16u; B.data_len = SWL25W_RX_OFF + 16u * 2048u; CHECK(swl25w_layout_bad(&B) == 0);
	B.data_len = SWL25W_RX_OFF + 16u * 2048u - 4u; CHECK(swl25w_layout_bad(&B) == 5);                      /* below even 16 buffers: the old clause */

	/* ---- the buffer cap: rx_n 0 = 16, rx_n 32 = 32: the request id word names the buffer, the pointer words must follow it */
	b_world();
	rxb_item(it, 20u, 20u, 0u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 20u + 4u, w32(it, 1), "rx buffers of the block");                 /* rx_n 0: buffer 20 does not exist (first lap, slot 20 >= 16: the request id decides) */
	B.rx_n = 32u; B.data_len = SWL25W_RX_OFF + 32u * 2048u;
	rxb_item(it, 20u, 31u, 0u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 20u + 4u, SWL25W_RX_RID + 32u, "rx buffers of the block");     /* buffer 32 of 32 */
	rxb_no(SWL25W_SP_HOST, base + 32u * 20u + 4u, SWL25W_RX_RID - 1u, "rx buffers of the block");       /* below RX_RID */

	/* ---- a batch: 16 first posts, then 32 more in ONE index store (slots 16..47, buffers out of order), then the single item as before */
	b_world(); B.rx_n = 32u; B.data_len = SWL25W_RX_OFF + 32u * 2048u;
	for (k = 0; k < 16u; k++)
		rxb_put(k, k, 0u);
	rxb_commit(16u);
	for (k = 0; k < 32u; k++)
		rxb_put(16u + k, (k * 5u) % 32u, 0u);
	rxb_no(SWL25W_SP_HOST, h2d1, 16u + 33u, "items ahead");                    /* 33 items ahead */
	rxb_no(SWL25W_SP_HOST, h2d1, 16u, "items ahead");                          /* no step */
	rxb_no(SWL25W_SP_HOST, h2d1, 8u, "items ahead");                           /* backwards */
	rxb_commit(48u);
	CHECK(cur_wr(1u) == 48u && B.wrap1 == 0u);
	/* the window of the item stores: slot cur .. cur + 31, nothing behind, nothing 32 ahead */
	rxb_item(it, 80u, 1u, 0u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 80u, w32(it, 0), "batch window");        /* 32 ahead of the write index */
	rxb_item(it, 47u, 1u, 0u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 47u + 0u, w32(it, 0) + 1u, "batch window|epoch");   /* behind it */
	rxb_item(it, 79u, 1u, 0u);
	acc(SWL25W_SP_HOST, base + 32u * 79u, w32(it, 0), SWL25W_L3);               /* 31 ahead: the last slot of the window */
	/* an index never publishes an item that is not there: two items written, the index asks for three; or the third has only word 0 */
	rxb_put(48u, 3u, 0u); rxb_put(49u, 4u, 0u);
	rxb_no(SWL25W_SP_HOST, h2d1, 51u, "L3 rx post");
	rxb_item(it, 50u, 5u, 0u);
	legit(SWL25W_SP_HOST, base + 32u * 50u, w32(it, 0), SWL25W_L3);
	rxb_no(SWL25W_SP_HOST, h2d1, 51u, "L3 rx post");
	rxb_no(SWL25W_SP_HOST, base + 32u * 50u + 4u, SWL25W_RX_RID + 32u, "rx buffers of the block");
	rxb_commit(50u);                                                          /* two items: 48, 49 */
	CHECK(cur_wr(1u) == 50u);
	/* ... and the single-item step of the old shape; a pointer that names another buffer than the request id is refused */
	legit(SWL25W_SP_HOST, base + 32u * 50u + 4u, w32(it, 1), SWL25W_L3);
	rxb_no(SWL25W_SP_HOST, base + 32u * 50u + 24u, w32(it, 6) + 2048u, "data pointer");
	for (k = 2u; k < 8u; k++)
		legit(SWL25W_SP_HOST, base + 32u * 50u + 4u * k, w32(it, k), SWL25W_L3);
	rxb_commit(51u);
	CHECK(cur_wr(1u) == 51u);
	/* the batched verdict of an item 3 slots ahead == the per-word verdict (blk_eq runs both on the same words, and on every bit flip) */
	rxb_item(it, 54u, 9u, 0u);
	blk_eq_item(SWL25W_SP_HOST, base + 32u * 54u, it, 8u);

	/* ---- the ring end: a batch may END at the end (index 0, a lap), never cross it */
	b_world(); B.rx_n = 16u;
	*bptr(SWL25W_SP_HOST, h2d1) = dep - 3u;                                    /* the host's write index, 3 slots from the end */
	for (k = 0; k < 3u; k++)
		rxb_put(dep - 3u + k, k, 0u);
	rxb_no(SWL25W_SP_HOST, h2d1, 2u, "items ahead");                           /* a batch of 5 across the end */
	rxb_no(SWL25W_SP_HOST, h2d1, 1u, "items ahead");
	rxb_commit(0u);                                                            /* the 3 items up to the end */
	CHECK(B.wrap1 == 1u && cur_wr(1u) == 0u);
	*bptr(SWL25W_SP_HOST, rd1) = 3u;                                           /* the dongle has read 3 items: slot 2 would make the ring full */
	rxb_item(it, 2u, 2u, 1u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 2u, w32(it, 0), "ring is full");
	rxb_put(0u, 5u, 1u); rxb_put(1u, 6u, 1u);                                 /* after the lap the epoch counts a whole ring more (wrap1 = 1) */
	rxb_item(it, 1u, 6u, 0u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 1u, w32(it, 0) ^ 1u << 24, "epoch");
	rxb_commit(2u);
	CHECK(B.wrap1 == 1u && cur_wr(1u) == 2u);
	/* review 0357 F1: after a lap, write index 2, dongle read index 4: only slot 2 is the host's (slots 4..2047, 0, 1 belong to the dongle); an item at slot 5 or 3 must be refused */
	*bptr(SWL25W_SP_HOST, rd1) = 4u;
	rxb_item(it, 5u, 7u, 1u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 5u, w32(it, 0), "ring is full");
	rxb_item(it, 3u, 7u, 1u);
	rxb_no(SWL25W_SP_HOST, base + 32u * 3u, w32(it, 0), "ring is full");
	rxb_item(it, 2u, 7u, 1u);
	acc(SWL25W_SP_HOST, base + 32u * 2u, w32(it, 0), SWL25W_L3);
	CHECK(rxb_refused == 18u);
	printf("rxpool judge rows: %u refusals, each for the reason that names it\n", rxb_refused);
}

int main(void)
{
	unsigned i, killed = 0u, total = 0u, seen = 0u;
	int rc;

	rc = l25_main();
	/* the depth literals above are the swd_rings of patch 0167 */
	CHECK(swd_rings[0].items == 128u && swd_rings[1].items == 2048u && swd_rings[2].items == 64u && swd_rings[3].items == 2048u && swd_rings[4].items == 1024u);
	CHECK(SWL25W_DB0_OFF == SWG_DB0_OFF && SWL25W_TX_OFF == SWL25_TX_OFF && SWL25W_RX_OFF == SWL25_RX_OFF && SWL25W_BUF_SZ == SWL25_BUF_SZ && SWL25W_FLOW_DEPTH == SWL25_FLOW_DEPTH);
	CHECK(SWL25W_RX_RID == SWDP_RX_RID && SWL25W_TX_RID == SWDP_TX_RID && SWL25W_RX_N == SWL25_RXPOSTS && SWL25W_RESP_LEN == SWD_RESPBUF_LEN && SWL25W_RESP_LEN == SWD_EVBUF_LEN);
	CHECK(call_n >= 50u && call_bad == 0u);
	CHECK(SWL25W_D2H_BATCH == SWG_E_BATCH);
	CHECK(SWL25W_BUFS == SWD_RESPBUF_N && SWL25W_BUFS == SWD_EVBUF_N && SWL25W_IOCTREQ_ID == SWD4B_IOCTL_REQID && SWL25W_MSG_RESPBUF == SWD4B_MSG_RESPBUF && SWL25W_MSG_EVBUF == SWD4B_MSG_EVBUF);
	CHECK(SWL25W_WLC_GET_VAR == SWD4B_WLC_GET_VAR && SWL25W_WLC_SET_VAR == SWD4B_WLC_SET_VAR && SWL25W_FLOW_ISZ == SWDP_ISZ_TX_BASE && SWL25W_MSG_FLOW_CREATE == SWDP_MT_FLOW_CREATE);
	battery();
	battery2();
	rj_rows();
	rxb_cases();
	for (i = 1; i <= SWL25W_NCLASS; i++)
		seen += rp_cls[i] != 0u || bat_cls[i] != 0u;
	for (i = 0; i < M_NMUT; i++) {
		total++;
		if (bm_app[i] != 0u && bm_ref[i] == bm_app[i])
			killed++;
		else
			fprintf(stderr, "FAIL battery: mutant '%s' applied %u times, refused %u\n", MNAME[i], bm_app[i], bm_ref[i]), fails++;
	}
	if (getenv("L25WL_REASONS") != NULL) {
		unsigned j;

		for (i = 0; i < M_NMUT; i++) {
			printf("mutant %s (%u/%u):\n", MNAME[i], bm_ref[i], bm_app[i]);
			for (j = 0; j < bm_nwhy[i]; j++)
				printf("    %s\n", bm_why[i][j]);
		}
	}
	for (i = 1; i <= SWL25W_NCLASS; i++)
		if (rp_cls[i] == 0u && bat_cls[i] == 0u)
			fprintf(stderr, "FAIL class L%u never accepted\n", i), fails++;
	CHECK(rp_refused == 0u && rp_stores > 1000u);
	printf("blk-eq: cases=%u accepted=%u refused=%u (batched verdict == per-word verdict)\n", beq_n, beq_acc, beq_ref);
	if (beq_n < 300u || beq_acc < 10u || beq_ref < 100u)
		fprintf(stderr, "FAIL blk-eq ran too few cases (%u, %u accepted, %u refused)\n", beq_n, beq_acc, beq_ref), fails++;
	printf("replay: stores=%u refused=%u classes:", rp_stores, rp_refused);
	for (i = 1; i <= SWL25W_NCLASS; i++)
		printf(" L%u=%u/%u", i, rp_cls[i], bat_cls[i]);
	printf("\n");
	for (i = 0; i < rp_nfirst; i++)
		printf("  refused: %s\n", rp_first[i]);
	if (rc != 0 || fails != 0u) {
		printf("RESULT: FAIL wlan_l25_wl_host_test fails=%u rc=%d\n", fails, rc);
		return 1;
	}
	printf("RESULT: PASS wlan_l25_wl_host_test classes=%u replay_stores=%u mutants_killed=%u/%u\n", seen, rp_stores, killed, total);
	return 0;
}
