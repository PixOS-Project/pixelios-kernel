/*
 * acpm.c - read / update one S2MPG10 register through the ACPM IPC channel (see acpm.h). Written only from
 * docs/research/23-acpm-regulator-client.md ("doc 23"); GK = android.googlesource.com/kernel/gs android-gs-bluejay-5.10-android13-qpr3.
 * Tags as in doc 23: [src] read from source, [guess] not established. NEVER run on hardware.
 *
 * Transaction (doc 23 section 4, B), every step reads first and writes only through w32():
 *   1. acpm_describe: read INIT (SRAM + initdata_off), the channel table, the descriptor of channel ch_id, the queue pointers; validate.
 *   2. refuse (zero writes) unless the descriptor validated, layout_verified and elem_unit_verified are set, both queues are empty
 *      and the queue-full conditions of acpm_ipc.c:712/:723 hold.
 *   3. write the 4-word request into the slot at req_base + 16 * front (q_elem_size is in BYTES: acpm_ipc.c:762 passes it to
 *      memcpy_align_4 as a byte count; acpm_describe requires esz * elem_unit == 16), then front := front + 1 (the request is now
 *      PUBLISHED), then ring the doorbell.
 *   4. poll the reply queue front != rear for at most timeout_us (50 ms) and ACPM_MAX_POLLS reads; read the reply, check its sequence
 *      number, rear := front, clear the mailbox status. No retry.
 *   5. NO rollback, ever. Before the front write the APM cannot see the slot (it reads only [rear, front)), so a failure there just
 *      leaves an unpublished slot behind. From the front write on, the APM may be running the request (it reads the slot, runs the
 *      SPEEDY transaction, and only then advances rear): writing front back would make it see q_len - 1 pending stale commands.
 *      So a timeout / fault after that point records the queue state (xfer.state) and leaves the channel exactly as it is.
 *
 * Allowed writes (w32_allowed, one line each): a word of the one request slot and the request queue's front (the new value), both
 * only until the front write has been attempted; the reply queue's rear (the reply's front value only) and mailbox INTCR0 (only the
 * channel's bit), both only after a reply was read; mailbox INTGR1 (only the channel's bit). Nothing else, and nothing at all until
 * the guard is armed after step 2. The addresses come from SRAM words, validated by acpm_describe (bounded to the SRAM window,
 * aligned, non-overlapping), so the audit proves the write PATH, not the addresses: the host test checks the guard acpm_guard() builds
 * against independently computed addresses. tools/test-bluejay-boot.py audits this file like touchprobe.c.
 * No static state, no asm, no volatile, no pointer casts.
 */
#include <stdint.h>
#include "acpm.h"

#if SOC_GS101 && defined(ACPM_MBOX_BASE)

/* Layout of struct acpm_framework at SRAM + initdata_off: GK fw_header/framework.h:36-133 [src]; offsets hand-derived from the
 * struct (doc 23 section 2: "verify against a SRAM dump before trusting them") = [guess] until layout_verified. */
#define INIT_CH_TAB	0x08u	/* ipc_channels: SRAM offset of the descriptor array */
#define INIT_AP_MAX	0x18u	/* ipc_ap_max: number of AP descriptors; sanity 1..32 (doc 23 section 4 A) */
#define MAX_AP_CH	ACPM_MAX_AP_CH
/* struct ipc_channel, stride desc_stride (0x48 = 18 words, hand-derived [guess]); framework.h:97-133, acpm_ipc.c:945-999 */
#define D_ID		0u	/* 0x00 id: acpm-ipc-channel = <2>, GK gs101-pmic.dtsi:38-39 [src] */
#define D_RX_REAR	4u	/* 0x10: AP->APM queue "rx_*" (acpm_ipc.c:977-983 builds tx_ch from them) [src] */
#define D_RX_FRONT	5u	/* 0x14 */
#define D_RX_BASE	6u	/* 0x18 */
#define D_TX_REAR	10u	/* 0x28: APM->AP queue "tx_*" (acpm_ipc.c:970-975 builds rx_ch from them) [src] */
#define D_TX_FRONT	11u	/* 0x2c */
#define D_TX_BASE	12u	/* 0x30 */
#define D_Q_LEN		13u	/* 0x34, sanity >= 3: acpm_ipc.c:697 [src] */
#define D_TYPE		3u	/* 0x0c type: TYPE_QUEUE = 1, fw_header/framework.h:247 [src] */
#define D_Q_ELEM	14u	/* 0x38 q_elem_size, in BYTES (acpm_ipc.c:762 -> memcpy_align_4 n bytes); elem_unit 1, still [guess] until verified on a dump */
#define D_AP_POLL	17u	/* 0x44 */
#define QLEN_MAX	256u
/* PMIC command words (MFD protocol), doc 23 section 1.1; GK include/soc/google/acpm_mfd.h, acpm_mfd.c:209-255 [src] */
#define FUNC_READ	0u	/* cmd[1] FUNC [7:0] */
#define FUNC_UPDATE	2u
#define SEQ_SHIFT	16u	/* cmd[0] seq num [21:16]: acpm_ipc.c:754-759, ACPM_IPC_PROTOCOL_SEQ_NUM = 16 [src] */
#define RET_SHIFT	24u	/* reply cmd[1] RETURN [31:24], DEST [15:8]: acpm_mfd.c:40-41, :243-248 [src] */
#define UPD_VAL_MASK	0x80u	/* only bit 7 (enable) of the two rails, set never clear (doc 23 section 5) */

/* The ONLY writes: see the header comment. Zero guard = nothing allowed. */
struct wguard {
	int armed;
	int slot_ok;				/* request slot + front writes: open until the front write was attempted (published) */
	uintptr_t slot_lo, slot_hi;		/* the request slot, [lo, hi) */
	int front_ok;
	uintptr_t front_pa;			/* request queue front: acpm_ipc.c:770 */
	uint32_t front_new;
	int rear_ok;				/* set only once a reply was read (also opens INTCR0) */
	uintptr_t rear_pa;			/* reply queue rear := reply front: acpm_ipc.c:455 */
	uint32_t rear_val;
	uintptr_t bell_pa, intcr_pa;		/* mailbox INTGR1 (doorbell, acpm_ipc.c:410-413, :772) / INTCR0 (status clear, :458) */
	uint32_t bit;				/* 1 << ch_id */
};

static int w32_allowed(uintptr_t pa, uint32_t v, const struct wguard *g)
{
	if (!g->armed)
		return 0;
	if (g->slot_ok && pa >= g->slot_lo && pa < g->slot_hi)	/* one word of the request slot, before publishing only */
		return !(pa & 3u);
	if (g->front_ok && pa == g->front_pa)		/* request front: the new value, once; never rolled back */
		return v == g->front_new;
	if (g->rear_ok && pa == g->rear_pa)		/* reply rear: only the front value just read (queue emptied) */
		return v == g->rear_val;
	if (pa == g->bell_pa || (g->rear_ok && pa == g->intcr_pa))	/* doorbell / status clear: only this channel's bit */
		return v == g->bit;
	return 0;
}

/* noinline keeps the one try_write32 call site inside this symbol (the object audit checks it). */
__attribute__((noinline)) static int w32(uintptr_t pa, uint32_t v, const struct wguard *g)
{
	if (!w32_allowed(pa, v, g))
		return -2;
	return try_write32(pa, v) ? 0 : -1;
}

static int r32(uintptr_t pa, uint32_t *v)
{
	return try_read32(pa, v);
}

static int put(uintptr_t pa, uint32_t v, const struct wguard *g, struct acpm_xfer *x)
{
	int e = w32(pa, v, g);

	if (!e)
		x->n++;
	return e;
}

void acpm_cfg_init(struct acpm_cfg *c)
{
	c->mbox_base = ACPM_MBOX_BASE;
	c->sram_base = ACPM_SRAM_BASE;
	c->sram_size = ACPM_SRAM_SIZE;
	c->initdata_off = ACPM_INITDATA_OFF;
	c->ch_id = ACPM_CH_ID;
	c->speedy = ACPM_SPEEDY;
	c->pmic_addr = ACPM_PMIC_ADDR;
	c->intgr1 = ACPM_INTGR1;
	c->intcr0 = ACPM_INTCR0;
	c->intsr0 = ACPM_INTSR0;
	c->desc_stride = ACPM_DESC_STRIDE;
	c->layout_verified = ACPM_LAYOUT_VERIFIED;
	c->elem_unit = ACPM_ELEM_UNIT;
	c->elem_unit_verified = ACPM_ELEM_UNIT_VERIFIED;
	c->timeout_us = ACPM_TIMEOUT_US;
	c->rail[0].reg = ACPM_RAIL0_REG;
	c->rail[0].vsel_known = ACPM_RAIL0_VSEL_KNOWN;
	c->rail[0].vsel_min = ACPM_RAIL0_VSEL_MIN;
	c->rail[0].vsel_max = ACPM_RAIL0_VSEL_MAX;
	c->rail[1].reg = ACPM_RAIL1_REG;
	c->rail[1].vsel_known = ACPM_RAIL1_VSEL_KNOWN;
	c->rail[1].vsel_min = ACPM_RAIL1_VSEL_MIN;
	c->rail[1].vsel_max = ACPM_RAIL1_VSEL_MAX;
#ifdef ACPM_DVFS_CH
	c->dvfs_ch = ACPM_DVFS_CH;
#else
	c->dvfs_ch = 0;
#endif
}

#else	/* no ACPM on this SoC: an all-zero config; every operation refuses */

void acpm_cfg_init(struct acpm_cfg *c)
{
	c->mbox_base = 0;
	c->sram_base = 0;
	c->sram_size = 0;
	c->initdata_off = 0;
	c->ch_id = 0;
	c->speedy = 0;
	c->pmic_addr = 0;
	c->intgr1 = 0;
	c->intcr0 = 0;
	c->intsr0 = 0;
	c->desc_stride = 0;
	c->layout_verified = 0;
	c->elem_unit = 0;
	c->elem_unit_verified = 0;
	c->timeout_us = 0;
	c->rail[0].reg = 0;
	c->rail[0].vsel_known = 0;
	c->rail[0].vsel_min = 0;
	c->rail[0].vsel_max = 0;
	c->rail[1] = c->rail[0];
	c->dvfs_ch = 0;
}

#endif

const char *acpm_why(uint32_t why)
{
	switch (why) {
	case ACPM_W_NONE: return "ok";
	case ACPM_W_INITN: return "ipc_ap_max not in 1..32";
	case ACPM_W_TAB: return "channel table outside SRAM";
	case ACPM_W_NOCH: return "no descriptor with the PMIC channel id";
	case ACPM_W_PTR: return "a descriptor pointer is unaligned or outside SRAM";
	case ACPM_W_QLEN: return "q_len not in 3..256";
	case ACPM_W_ESZ: return "q_elem_size * elem_unit is not 16 bytes (4 words)";
	case ACPM_W_QBUF: return "queue buffer outside SRAM";
	case ACPM_W_IDX: return "a front/rear index is >= q_len";
	case ACPM_W_OVERLAP: return "queue buffers or pointer words overlap";
	case ACPM_W_BUSY: return "a queue is not empty";
	case ACPM_W_FULL: return "queue-full condition (acpm_ipc.c:712/:723)";
	case ACPM_W_LAYOUT: return "descriptor layout (u32 pointers, 0x48 stride, field offsets) not verified against a dump";
	case ACPM_W_UNIT: return "queue element unit of q_elem_size (words vs bytes) not verified";
	case ACPM_W_VSEL: return "rail voltage code range unknown (soc.json acpm rails vsel_min/vsel_max)";
	case ACPM_W_VSELOUT: return "rail voltage code outside the configured range";
	case ACPM_W_ON: return "already in the requested state, nothing to do";
	case ACPM_W_ARG: return "only reads of, and bit-7 sets on, the configured rail registers are allowed";
	case ACPM_W_SEQ: return "reply has another sequence number or entry count";
	case ACPM_W_CHIDX: return "descriptor table index is not the channel id";
	case ACPM_W_TYPE: return "descriptor type is not TYPE_QUEUE (1)";
	case ACPM_W_TIMER: return "counter frequency is 0, no bounded wait";
	case ACPM_W_DOM: return "DVFS: domain not configured, or rate not in the firmware table / above the ceiling";
	case ACPM_W_DREPLY: return "DVFS: reply word 3 (error code) is not 0";
	default: return "?";
	}
}

int acpm_unsure(uint32_t why)
{
	return why == ACPM_W_LAYOUT || why == ACPM_W_UNIT || why == ACPM_W_VSEL;
}

#if SOC_GS101 && defined(ACPM_MBOX_BASE)

static int overlap(uintptr_t a, uintptr_t an, uintptr_t b, uintptr_t bn)
{
	return a < b + bn && b < a + an;
}

static int bad(struct acpm_chan *h, uint32_t why)
{
	h->why = why;
	return ACPM_E_DESC;
}

/* Reads only. Fills *h; returns ACPM_OK when the descriptor validated (the queues may still be busy: acpm_xfer decides). */
int acpm_describe(const struct acpm_cfg *c, struct acpm_chan *h)
{
	uintptr_t init = c->sram_base + c->initdata_off, tab, desc;
	uintptr_t lo[6], len[6];
	uint32_t i, j, id = 0, off, bytes;

	h->n = h->tab = h->idx = h->have_desc = 0;
	for (i = 0; i < ACPM_DESC_WORDS; i++)
		h->d[i] = 0;
	h->qlen = h->esz = h->esz_w = h->esz_b = h->ap_poll = 0;
	h->req_rear_pa = h->req_front_pa = h->req_base_pa = 0;
	h->rsp_rear_pa = h->rsp_front_pa = h->rsp_base_pa = 0;
	h->rr = h->rf = h->sr = h->sf = h->intsr0 = 0;
	h->why = ACPM_W_NONE;
	for (i = 0; i < 8; i++)
		if (!r32(init + 4u * i, &h->init[i]))
			return ACPM_E_FAULT;
	h->n = h->init[INIT_AP_MAX / 4];
	h->tab = h->init[INIT_CH_TAB / 4];
	if (h->n < 1 || h->n > MAX_AP_CH)
		return bad(h, ACPM_W_INITN);
	if (c->desc_stride < 4 * ACPM_DESC_WORDS || (c->desc_stride & 3u) || (h->tab & 3u) ||
	    (uint64_t)h->tab + (uint64_t)h->n * c->desc_stride > c->sram_size)
		return bad(h, ACPM_W_TAB);
	tab = c->sram_base + h->tab;
	for (h->idx = 0; h->idx < h->n; h->idx++) {
		if (!r32(tab + (uintptr_t)h->idx * c->desc_stride + 4u * D_ID, &id))
			return ACPM_E_FAULT;
		if (id == c->ch_id)
			break;
	}
	if (h->idx == h->n)
		return bad(h, ACPM_W_NOCH);
	desc = tab + (uintptr_t)h->idx * c->desc_stride;
	for (i = 0; i < ACPM_DESC_WORDS; i++)
		if (!r32(desc + 4u * i, &h->d[i]))
			return ACPM_E_FAULT;
	h->have_desc = 1;	/* d[] is now dumpable, whatever the checks below say */
	h->ap_poll = h->d[D_AP_POLL];
	h->qlen = h->d[D_Q_LEN];
	h->esz = h->d[D_Q_ELEM];
	if (h->idx != c->ch_id)	/* Linux indexes channel[id] (acpm_ipc.c:363, :696): a firmware where it differs is not what Linux exercises */
		return bad(h, ACPM_W_CHIDX);
	if (h->d[D_TYPE] != 1)
		return bad(h, ACPM_W_TYPE);
	if (h->qlen < 3 || h->qlen > QLEN_MAX)
		return bad(h, ACPM_W_QLEN);
	if (h->esz <= 64 && c->elem_unit && c->elem_unit <= 4)	/* the bound keeps esz * unit from wrapping */
		h->esz_b = h->esz * c->elem_unit;
	h->esz_w = h->esz_b / 4;
	if (h->esz_b != ACPM_ELEM_BYTES)	/* only the 4-word MFD command; acpm_mfd.c: unsigned int command[4] */
		return bad(h, ACPM_W_ESZ);
	for (i = 0; i < 4; i++) {	/* the four pointer words (rear/front of both queues), SRAM offsets */
		off = h->d[i < 2 ? D_RX_REAR + i : D_TX_REAR + i - 2];
		if ((off & 3u) || off > c->sram_size - 4u)
			return bad(h, ACPM_W_PTR);
		lo[i] = c->sram_base + off;
		len[i] = 4;
	}
	h->req_rear_pa = lo[0];
	h->req_front_pa = lo[1];
	h->rsp_rear_pa = lo[2];
	h->rsp_front_pa = lo[3];
	bytes = h->qlen * h->esz_b;
	for (i = 0; i < 2; i++) {	/* the two queue buffers */
		off = h->d[i ? D_TX_BASE : D_RX_BASE];
		if ((off & 3u) || (uint64_t)off + bytes > c->sram_size)
			return bad(h, (off & 3u) ? ACPM_W_PTR : ACPM_W_QBUF);
		lo[4 + i] = c->sram_base + off;
		len[4 + i] = bytes;
	}
	h->req_base_pa = lo[4];
	h->rsp_base_pa = lo[5];
	for (i = 0; i < 6; i++)
		for (j = i + 1; j < 6; j++)
			if (overlap(lo[i], len[i], lo[j], len[j]))
				return bad(h, ACPM_W_OVERLAP);
	if (!r32(h->req_rear_pa, &h->rr) || !r32(h->req_front_pa, &h->rf) || !r32(h->rsp_rear_pa, &h->sr) ||
	    !r32(h->rsp_front_pa, &h->sf) || !r32(c->mbox_base + c->intsr0, &h->intsr0))
		return ACPM_E_FAULT;
	if (h->rr >= h->qlen || h->rf >= h->qlen || h->sr >= h->qlen || h->sf >= h->qlen)
		return bad(h, ACPM_W_IDX);
	return ACPM_OK;
}

/* Reads only: the id word of descriptor idx, for the "no descriptor with the PMIC channel id" report (the table was validated). */
int acpm_chan_id(const struct acpm_cfg *c, const struct acpm_chan *h, uint32_t idx, uint32_t *id)
{
	if (h->why != ACPM_W_NOCH || idx >= h->n)
		return 0;
	return r32(c->sram_base + h->tab + (uintptr_t)idx * c->desc_stride + 4u * D_ID, id);
}

int acpm_qword(const struct acpm_cfg *c, const struct acpm_chan *h, int rsp, uint32_t i, uint32_t *v)
{
	uint32_t off;

	if (!h->have_desc)
		return 0;
	off = h->d[rsp ? D_TX_BASE : D_RX_BASE];
	if ((off & 3u) || (uint64_t)off + 4u * (uint64_t)i + 4u > c->sram_size)
		return 0;
	return r32(c->sram_base + off + 4u * i, v);
}

static int fail(struct acpm_xfer *x, int e, uint32_t why)
{
	x->err = e;
	x->why = why;
	return e;
}

/* The guard of one transaction, from the validated channel and the slot it will use (the host test inspects it). */
static void acpm_guard(struct wguard *g, const struct acpm_cfg *c, const struct acpm_chan *h, uintptr_t slot)
{
	g->armed = 1;
	g->slot_ok = g->front_ok = 1;
	g->slot_lo = slot;
	g->slot_hi = slot + ACPM_ELEM_BYTES;
	g->front_pa = h->req_front_pa;
	g->front_new = (h->rf + 1) % h->qlen;
	g->rear_pa = h->rsp_rear_pa;
	g->rear_ok = 0;			/* the reply-rear write and INTCR0 stay refused until a reply is read */
	g->rear_val = 0;
	g->bell_pa = c->mbox_base + c->intgr1;
	g->intcr_pa = c->mbox_base + c->intcr0;
	g->bit = 1u << c->ch_id;
}

/* After a failure with the request published: record the four queue pointers (reads only), touch nothing. */
static void snap(const struct acpm_chan *h, struct acpm_xfer *x)
{
	x->state[0] = x->state[1] = x->state[2] = x->state[3] = ~0u;
	if (!r32(h->req_rear_pa, &x->state[0]))
		x->state[0] = ~0u;
	if (!r32(h->req_front_pa, &x->state[1]))
		x->state[1] = ~0u;
	if (!r32(h->rsp_rear_pa, &x->state[2]))
		x->state[2] = ~0u;
	if (!r32(h->rsp_front_pa, &x->state[3]))
		x->state[3] = ~0u;
}

/* Transaction kinds. XM_PMIC: cmd[0] = REG | TYPE (pmic_addr) << 8 | CHANNEL (speedy) << 12 | seq << 16, cmd[1] = w1, reply RETURN [31:24]
 * (doc 23). XM_DGET: DVFS FREQ_GET, value = reply word 1, reply word 3 must be 0 (acpm_dvfs.c:173-196 [src]). XM_DREQ (TOUCH_DVFS_WRITE or GPU_CLK
 * images only): DVFS FREQ_REQ. The APM replies to EVERY request, also the ones Linux sends with response=false: Linux sets the request's
 * sequence bit in send_data (acpm_ipc.c:756) and clears it for response=false only when that reply arrives (check_response, :446-448); a
 * firmware that did not reply would hang Linux after 63 requests (:747-750) [src, review dvfs-client-review-1 H2]. So all three wait for
 * the reply, like the PMIC path. cmd[0] = domain index | seq << 16 for both DVFS kinds. */
enum { XM_PMIC, XM_DGET, XM_DREQ };

static int xfer(const struct acpm_cfg *c, int mode, uint32_t w0, uint32_t w1, uint32_t w2, uint32_t w3, struct acpm_trace *t,
		struct acpm_xfer *x)
{
	struct acpm_chan h;
	struct wguard g;
	uint32_t i, nf, sf, sr, cnt;
	uintptr_t slot, ent;
	uint64_t t0, lim;
	unsigned polls = 0;
	int e;

	x->seq = t->seq;
	t->seq = t->seq % 63u + 1u;
	x->req[0] = w0 | (x->seq << SEQ_SHIFT);
	x->req[1] = w1;
	x->req[2] = w2;	/* PMIC: unused words 0; cmd[3] is only an AP timestamp (doc 23 section 1.1) [src acpm_mfd.c:221-224; guess it is not needed] */
	x->req[3] = w3;
	for (i = 0; i < 4; i++)
		x->rep[i] = x->state[i] = 0;
	x->ret = x->value = x->n = x->published = x->had_reply = 0;
	x->t_bell = x->t_end = 0;
	x->err = ACPM_OK;
	x->why = ACPM_W_NONE;
	if (c->ch_id >= 32)
		return fail(x, ACPM_E_ARG, ACPM_W_ARG);
	e = acpm_describe(c, &h);
	if (e)
		return fail(x, e, h.why);
	if (!c->layout_verified)
		return fail(x, ACPM_E_UNSURE, ACPM_W_LAYOUT);
	if (!c->elem_unit_verified)
		return fail(x, ACPM_E_UNSURE, ACPM_W_UNIT);
	if (h.rf != h.rr || h.sf != h.sr)	/* queues must be empty (we do not drain what the ABL / Linux left) */
		return fail(x, ACPM_E_BUSY, ACPM_W_BUSY);
	nf = (h.rf + 1) % h.qlen;
	if ((h.rf + 2) % h.qlen == h.sf || nf == h.rr)	/* acpm_ipc.c:712, :723 [src] */
		return fail(x, ACPM_E_BUSY, ACPM_W_FULL);
	if (!counter_freq())			/* no clock = no bounded wait: refuse before anything is published */
		return fail(x, ACPM_E_UNSURE, ACPM_W_TIMER);
	slot = h.req_base_pa + (uintptr_t)ACPM_ELEM_BYTES * h.rf;	/* acpm_ipc.c:762: base + size * front, size in bytes [src] */
	acpm_guard(&g, c, &h, slot);

	for (i = 0; i < ACPM_ELEM_WORDS; i++)	/* an unpublished slot: the APM only reads [rear, front), so a failure here needs no undo */
		if (put(slot + 4u * i, x->req[i], &g, x))
			return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
	x->published = 1;			/* from the front write on the APM may run the request: no write ever takes it back */
	e = put(h.req_front_pa, nf, &g, x);
	g.slot_ok = g.front_ok = 0;		/* the slot and the front are never written again, whatever happens next */
	if (e || !r32(h.req_front_pa, &cnt) || cnt != nf) {
		snap(&h, x);
		return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
	}
	t0 = touch_ticks();
	x->t_bell = t0;
	if (put(g.bell_pa, g.bit, &g, x)) {
		snap(&h, x);
		return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
	}
	x->published = 2;
	lim = counter_freq() * c->timeout_us / 1000000u;
	for (;;) {
		if (!r32(h.rsp_front_pa, &sf) || !r32(h.rsp_rear_pa, &sr)) {
			x->t_end = touch_ticks();
			snap(&h, x);
			return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
		}
		if (sf != sr)
			break;
		if (++polls > ACPM_MAX_POLLS || touch_ticks() - t0 > lim) {
			x->t_end = touch_ticks();
			snap(&h, x);		/* stop here: the request stays queued for the APM, the channel is left as it is */
			return fail(x, ACPM_E_TIMEOUT, ACPM_W_NONE);
		}
	}
	x->t_end = touch_ticks();
	if (sf >= h.qlen || sr >= h.qlen) {	/* garbage pointers from the APM: touch nothing more */
		snap(&h, x);
		return fail(x, ACPM_E_REPLY, ACPM_W_IDX);
	}
	ent = h.rsp_base_pa + (uintptr_t)ACPM_ELEM_BYTES * sr;
	for (i = 0; i < 4; i++)
		if (!r32(ent + 4u * i, &x->rep[i])) {
			snap(&h, x);
			return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
		}
	cnt = (sf + h.qlen - sr) % h.qlen;
	g.rear_val = sf;	/* now rear := front (acpm_ipc.c:455) is allowed, and only that */
	g.rear_ok = 1;
	if (put(h.rsp_rear_pa, sf, &g, x) || put(g.intcr_pa, g.bit, &g, x)) {	/* drain, then clear the status: :455-458 */
		snap(&h, x);
		return fail(x, ACPM_E_FAULT, ACPM_W_NONE);
	}
	x->had_reply = 1;
	if (mode != XM_PMIC) {			/* DVFS: value = reply word 1 (kHz), error code = reply word 3 */
		x->ret = x->rep[3];
		x->value = x->rep[1];
		if (((x->rep[0] >> SEQ_SHIFT) & 0x3fu) != x->seq || cnt != 1)
			return fail(x, ACPM_E_REPLY, ACPM_W_SEQ);	/* reply word 0 = seq << 16 only: the domain bits are cleared (observed run dvfsr1) */
		if (x->ret)
			return fail(x, ACPM_E_RET, ACPM_W_DREPLY);
		return ACPM_OK;
	}
	x->ret = x->rep[1] >> RET_SHIFT;
	x->value = (x->rep[1] >> 8) & 0xffu;
	if (((x->rep[0] >> SEQ_SHIFT) & 0x3fu) != x->seq || cnt != 1)
		return fail(x, ACPM_E_REPLY, ACPM_W_SEQ);
	if (x->ret)
		return fail(x, ACPM_E_RET, ACPM_W_NONE);
	return ACPM_OK;
}

static uint32_t pmic_w0(const struct acpm_cfg *c, uint32_t reg)
{
	return (reg & 0xffu) | ((c->pmic_addr & 0xfu) << 8) | ((c->speedy & 0xfu) << 12);
}

static int rail_of(const struct acpm_cfg *c, uint32_t reg)
{
	return reg == c->rail[0].reg ? 0 : reg == c->rail[1].reg ? 1 : -1;
}

static int refuse(struct acpm_xfer *x, int e, uint32_t why)
{
	acpm_xfer_zero(x);	/* a refusal wrote nothing: never leave stack garbage for the report to print */
	return fail(x, e, why);
}

static int read1(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t, struct acpm_xfer *x)
{
	int e;

	t->n++;
	if (rail_of(c, reg) < 0)
		return refuse(x, ACPM_E_ARG, ACPM_W_ARG);
	e = xfer(c, XM_PMIC, pmic_w0(c, reg), FUNC_READ, 0, 0, t, x);
	if (!e)
		*val = x->value;
	return e;
}

int acpm_pmic_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	t->n = 0;
	return read1(c, reg, val, t, &t->a);
}

/* S2MPG11 (sub PMIC, speedy channel 1, GPU rails) READ for --gpu-rails (docs/research/27): the same transaction as the S2MPG10 read above
 * (same IPC channel, same xfer(), FUNC_READ only, no PMIC write is expressible here), with CHANNEL = soc.json acpm.s2mpg11.speedy_ch and
 * TYPE = pmic_addr, and only for the registers listed there. Unknown (null) config: the macros are absent and every call refuses. */
#if defined(ACPM_S11_KNOWN) && ACPM_S11_KNOWN && defined(ACPM_S11_NREG)
static const uint32_t s11_regs[ACPM_S11_NREG] = { ACPM_S11_REGS };

int acpm_s11_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	unsigned k;
	int e;

	t->n = 1;
	for (k = 0; k < ACPM_S11_NREG; k++)
		if (s11_regs[k] == reg)
			break;
	if (k == ACPM_S11_NREG)
		return refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);
	e = xfer(c, XM_PMIC, (reg & 0xffu) | ((uint32_t)ACPM_S11_PMIC_ADDR << 8) | ((uint32_t)ACPM_S11_SPEEDY << 12), FUNC_READ, 0, 0, t, &t->a);
	if (!e)
		*val = t->a.value;
	return e;
}
#else
int acpm_s11_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val;
	t->n = 1;
	return refuse(&t->a, ACPM_E_UNSURE, ACPM_W_ARG);
}
#endif

/* S2MPG10 (main PMIC, speedy channel c->speedy) READ for --wlan-rails (docs/research/29 section 4): the same transaction as acpm_pmic_read above (same IPC channel, same
 * xfer(), FUNC_READ only, no PMIC write is expressible here), but for the four LDO control registers of soc.json wlan (s10_l16m..l19m_ctrl = 0x53..0x56, the PCIe0 / PCIe1
 * PHY supplies; s2mpg10-register.h:99-103) instead of the touch rails. A register not listed there, or a config without the macros, is refused. */
#if defined(WLAN_S10_L16M_CTRL) && defined(WLAN_S10_L17M_CTRL) && defined(WLAN_S10_L18M_CTRL) && defined(WLAN_S10_L19M_CTRL)
static const uint32_t s10w_regs[4] = { WLAN_S10_L16M_CTRL, WLAN_S10_L17M_CTRL, WLAN_S10_L18M_CTRL, WLAN_S10_L19M_CTRL };

int acpm_s10_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	unsigned k;
	int e;

	t->n = 1;
	for (k = 0; k < 4; k++)
		if (s10w_regs[k] == reg)
			break;
	if (k == 4)
		return refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);
	e = xfer(c, XM_PMIC, pmic_w0(c, reg), FUNC_READ, 0, 0, t, &t->a);
	if (!e)
		*val = t->a.value;
	return e;
}
#else
int acpm_s10_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val;
	return refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);
}
#endif

/* S2MPG11 BUCK2S_CTRL mode switch for --gpu-rail-on (docs/research/27): the ONLY S2MPG11 write expressible here. Twin of acpm_pmic_update above
 * (same xfer(), same FUNC_UPDATE framing cmd[1] = 2 | val << 8 | mask << 16, read-modify-write done by the APM); it exists only in GPU_RAIL_ON images
 * and only for the pair soc.json acpm.s2mpg11.rail_on: reg 0x15, mask 0xC0, val 0x40 (mode 1, as Linux) or 0x00 (off).
 *   on:  t->a = pre-read of the CTRL register, which must be EXACTLY mode 0 (off) under the mask, else ACPM_E_ON and nothing is written;
 *        value VAL_ON = 0x40 = mode 1 (SUSPEND), what Linux writes for this rail (soc.json note);
 *        then OUT1 is read (*out1) and must decode inside [VSEL_MIN, VSEL_MAX] (the voltage is never written), else ACPM_E_RAIL, nothing written;
 *        t->b = the update.
 *   off: t->a = pre-read; if the mode is already 0 there is nothing to restore (ACPM_E_ON, nothing written); else t->b = the update.
 * One request per write, no retry; a failure after the front write leaves the queue as it is (xfer's rule). */
#if defined(GPU_RAIL_ON) && defined(ACPM_S11_ON_KNOWN) && ACPM_S11_ON_KNOWN && defined(ACPM_S11_KNOWN) && ACPM_S11_KNOWN
static void xcopy(struct acpm_xfer *d, const struct acpm_xfer *s)
{
	unsigned i;

	d->seq = s->seq;
	d->err = s->err;
	d->why = s->why;
	d->ret = s->ret;
	d->value = s->value;
	d->n = s->n;
	d->published = s->published;
	d->had_reply = s->had_reply;
	d->t_bell = s->t_bell;
	d->t_end = s->t_end;
	for (i = 0; i < 4; i++) {
		d->req[i] = s->req[i];
		d->rep[i] = s->rep[i];
		d->state[i] = s->state[i];
	}
}

static uint32_t s11_w0(uint32_t reg)
{
	return (reg & 0xffu) | ((uint32_t)ACPM_S11_PMIC_ADDR << 8) | ((uint32_t)ACPM_S11_SPEEDY << 12);
}

int acpm_s11_rail_set(const struct acpm_cfg *c, int on, struct acpm_trace *t, uint32_t *out1)
{
	struct acpm_xfer xo;
	uint32_t cur, val = on ? ACPM_S11_ON_VAL_ON : ACPM_S11_ON_VAL_OFF;
	int e;

	t->n = 0;
	acpm_xfer_zero(&t->a);
	acpm_xfer_zero(&t->b);
	acpm_xfer_zero(&xo);
	t->n = 1;
	e = xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_REG), FUNC_READ, 0, 0, t, &t->a);	/* pre-read */
	if (e)
		return e;
	cur = t->a.value;
	t->n = 2;
	if (on) {
		if ((cur & ACPM_S11_ON_MASK) != 0)			/* not exactly "off": already on, or another mode: leave it alone */
			return refuse(&t->b, ACPM_E_ON, ACPM_W_ON);
		e = xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_OUT1), FUNC_READ, 0, 0, t, &xo);
		if (e) {
			xcopy(&t->b, &xo);
			return e;
		}
		if (out1)
			*out1 = xo.value;
		if (xo.value < ACPM_S11_ON_VSEL_MIN || xo.value > ACPM_S11_ON_VSEL_MAX)
			return refuse(&t->b, ACPM_E_RAIL, ACPM_W_VSELOUT);
	} else if ((cur & ACPM_S11_ON_MASK) == 0) {
		return refuse(&t->b, ACPM_E_ON, ACPM_W_ON);		/* already off: nothing to restore */
	}
	t->n = 3;							/* from here on an UPDATE is (about to be) published: callers tell it from the two reads by t->n == 3 */
	return xfer(c, XM_PMIC, s11_w0(ACPM_S11_ON_REG), FUNC_UPDATE | (val << 8) | ((uint32_t)ACPM_S11_ON_MASK << 16), 0, 0, t, &t->b);
}
#endif

#ifdef TOUCH_RAILS_WRITE	/* a railsr (read-only) image must not contain code that can build an UPDATE */
int acpm_pmic_update(const struct acpm_cfg *c, uint32_t reg, uint32_t val, uint32_t mask, struct acpm_trace *t)
{
	int r = rail_of(c, reg), e;
	uint32_t cur = 0;

	t->n = 0;
	if (r < 0 || val != UPD_VAL_MASK || mask != UPD_VAL_MASK) {
		t->n = 1;
		return refuse(&t->a, ACPM_E_ARG, ACPM_W_ARG);
	}
	if (!c->rail[r].vsel_known) {	/* before the pre-read: nothing is written for an unknown voltage */
		t->n = 1;
		return refuse(&t->a, ACPM_E_UNSURE, ACPM_W_VSEL);
	}
	e = read1(c, reg, &cur, t, &t->a);	/* pre-read */
	if (e)
		return e;
	t->n++;
	if (cur & 0x80u)			/* already on: doc 23 section 5, nothing to switch */
		return refuse(&t->b, ACPM_E_ON, ACPM_W_ON);
	if ((cur & 0x3fu) < c->rail[r].vsel_min || (cur & 0x3fu) > c->rail[r].vsel_max)
		return refuse(&t->b, ACPM_E_RAIL, ACPM_W_VSELOUT);
	/* cmd[1] = FUNC_UPDATE | UPDATE_VAL << 8 | UPDATE_MASK << 16 = 0x00808002 (doc 23 section 1.1) */
	return xfer(c, XM_PMIC, pmic_w0(c, reg), FUNC_UPDATE | (val << 8) | (mask << 16), 0, 0, t, &t->b);
}
#endif

#if defined(ACPM_DVFS_CH) && defined(ACPM_DVFS_N)
/* DVFS domains (docs/research/25): the rows are the firmware's own table, read from APM SRAM on the phone (observed run fvmap1,
 * 2026-10-22; generated from soc.json "acpm"."dvfs" by tools/boards.py, which also checks them); the ceilings are ours (no thermal
 * management exists). Plain const arrays, no pointer tables. */
#define FREQ_REQ	0u	/* acpm_dvfs.h:22 [src] */
#define FREQ_GET	1u	/* acpm_dvfs.h:23 [src] */
#ifndef ACPM_DVFS_NALL
#define ACPM_DVFS_NALL	ACPM_DVFS_N
#endif
/* ACPM_DVFS_N = the CPU clusters (what the touch probe iterates, acpm_dvfs_ndom/dom); the tables hold ALL configured domains (CPU + the two GPU
 * clocks G3D = 5 / G3DL2 = 6, whose ceiling is their init level: only --gpu-clk asks them for a rate). */
static const uint32_t dvfs_idx[ACPM_DVFS_NALL] = { ACPM_DVFS_IDXS };
#if defined(TOUCH_DVFS_WRITE) || defined(GPU_CLK)	/* only dvfs_rate_ok() reads these three */
static const uint32_t dvfs_ceil[ACPM_DVFS_NALL] = { ACPM_DVFS_CEILS };
static const uint32_t dvfs_nr[ACPM_DVFS_NALL] = { ACPM_DVFS_NRS };
static const uint32_t dvfs_rate[ACPM_DVFS_NALL][ACPM_DVFS_MAXLV] = { ACPM_DVFS_ROWS };
#endif

int acpm_dvfs_ndom(void)
{
	return ACPM_DVFS_N;
}

int acpm_dvfs_dom(int i, uint32_t *idx)
{
	if (i < 0 || i >= ACPM_DVFS_N)
		return 0;
	*idx = dvfs_idx[i];
	return 1;
}

static int dvfs_find(uint32_t dom)
{
	int i;

	for (i = 0; i < ACPM_DVFS_NALL; i++)
		if (dvfs_idx[i] == dom)
			return i;
	return -1;
}

/* the AP timestamp word (Linux: sched_clock() / 1e6, ms); only a log stamp as far as the source shows [src acpm_dvfs.c:112] */
static uint32_t dvfs_ms(void)
{
	uint64_t f = counter_freq();

	return f ? (uint32_t)(touch_ticks() * 1000u / f) : 0u;
}

_Static_assert(sizeof(struct acpm_cfg) == 2 * sizeof(uintptr_t) + 22 * sizeof(uint32_t), "struct acpm_cfg changed: update cfg_chan");
/* The config of another channel: same fields, ch_id replaced. By hand: a struct assignment compiles to a memcpy call and the shim has no libc. */
static void cfg_chan(const struct acpm_cfg *c, struct acpm_cfg *d, uint32_t ch)
{
	d->mbox_base = c->mbox_base;
	d->sram_base = c->sram_base;
	d->sram_size = c->sram_size;
	d->initdata_off = c->initdata_off;
	d->ch_id = ch;
	d->speedy = c->speedy;
	d->pmic_addr = c->pmic_addr;
	d->intgr1 = c->intgr1;
	d->intcr0 = c->intcr0;
	d->intsr0 = c->intsr0;
	d->desc_stride = c->desc_stride;
	d->layout_verified = c->layout_verified;
	d->elem_unit = c->elem_unit;
	d->elem_unit_verified = c->elem_unit_verified;
	d->timeout_us = c->timeout_us;
	d->rail[0].reg = c->rail[0].reg;
	d->rail[0].vsel_known = c->rail[0].vsel_known;
	d->rail[0].vsel_min = c->rail[0].vsel_min;
	d->rail[0].vsel_max = c->rail[0].vsel_max;
	d->rail[1].reg = c->rail[1].reg;
	d->rail[1].vsel_known = c->rail[1].vsel_known;
	d->rail[1].vsel_min = c->rail[1].vsel_min;
	d->rail[1].vsel_max = c->rail[1].vsel_max;
	d->dvfs_ch = c->dvfs_ch;
}

int acpm_dvfs_get(const struct acpm_cfg *c, uint32_t dom, uint32_t *khz, struct acpm_trace *t)
{
	struct acpm_cfg d;
	int i, e;

	t->n = 1;
	i = dvfs_find(dom);
	if (!c->dvfs_ch || i < 0)
		return refuse(&t->a, ACPM_E_ARG, ACPM_W_DOM);
	cfg_chan(c, &d, c->dvfs_ch);
	e = xfer(&d, XM_DGET, dvfs_idx[i], 0, FREQ_GET, dvfs_ms(), t, &t->a);
	if (!e)
		*khz = t->a.value;
	return e;
}

#if defined(TOUCH_DVFS_WRITE) || defined(GPU_CLK)	/* a read-only image must not contain code that can build a FREQ_REQ */
static int dvfs_rate_ok(int i, uint32_t khz)
{
	uint32_t k;

	if (khz == 0 || khz > dvfs_ceil[i])
		return 0;
	for (k = 0; k < dvfs_nr[i] && k < ACPM_DVFS_MAXLV; k++)
		if (dvfs_rate[i][k] == khz)
			return 1;
	return 0;
}

/* same_ok: send the request even when the pre-read already shows khz (GPU clocks only: Linux re-sends the init rate at every probe, and the
 * point of --gpu-clk is to make the firmware act on a rate it already reports). Everything else (table row, ceiling, pre-read, channel
 * gates, reply handling) is the same code path for both callers. */
/* OPEN (review L1): acpm_dvfs_set also finds the GPU domains (dvfs_find loops over NALL); only its caller keeps them out. */
static int dvfs_set(const struct acpm_cfg *c, uint32_t dom, uint32_t khz, struct acpm_trace *t, int same_ok)
{
	struct acpm_cfg d;
	uint32_t cur = 0;
	int i, e;

	t->n = 0;
	acpm_xfer_zero(&t->a);
	acpm_xfer_zero(&t->b);
	i = dvfs_find(dom);
	if (!c->dvfs_ch || i < 0 || !dvfs_rate_ok(i, khz)) {	/* before the pre-read: nothing is sent for a rate we do not know */
		t->n = 1;
		return refuse(&t->a, ACPM_E_ARG, ACPM_W_DOM);
	}
	e = acpm_dvfs_get(c, dom, &cur, t);	/* pre-read (t->a) */
	if (e)
		return e;
	t->n++;
	if (cur == khz && !same_ok)		/* already there: nothing to ask for */
		return refuse(&t->b, ACPM_E_ON, ACPM_W_ON);
	cfg_chan(c, &d, c->dvfs_ch);
	return xfer(&d, XM_DREQ, dvfs_idx[i], khz, FREQ_REQ, dvfs_ms(), t, &t->b);
}

#ifdef TOUCH_DVFS_WRITE
int acpm_dvfs_set(const struct acpm_cfg *c, uint32_t dom, uint32_t khz, struct acpm_trace *t)
{
	return dvfs_set(c, dom, khz, t, 0);
}
#endif

#ifdef GPU_CLK
/* --gpu-clk: FREQ_REQ for G3D / G3DL2 only (a CPU domain is refused before anything is sent), the same rate allowed as the current one. */
int acpm_dvfs_reassert(const struct acpm_cfg *c, uint32_t dom, uint32_t khz, struct acpm_trace *t)
{
	static const uint32_t gpu[] = { ACPM_DVFS_GPU_IDXS };
	unsigned k;

	for (k = 0; k < sizeof(gpu) / sizeof(gpu[0]); k++)
		if (gpu[k] == dom)
			return dvfs_set(c, dom, khz, t, 1);
	t->n = 1;
	acpm_xfer_zero(&t->a);
	return refuse(&t->a, ACPM_E_ARG, ACPM_W_DOM);
}
#endif
#endif

#else	/* no DVFS block in soc.json */

int acpm_dvfs_ndom(void) { return 0; }
int acpm_dvfs_dom(int i, uint32_t *idx) { (void)i; (void)idx; return 0; }
int acpm_dvfs_get(const struct acpm_cfg *c, uint32_t dom, uint32_t *khz, struct acpm_trace *t)
{
	(void)c; (void)dom; (void)khz;
	t->n = 1;
	return refuse(&t->a, ACPM_E_ARG, ACPM_W_DOM);
}

#endif

#else	/* no ACPM on this SoC */

int acpm_describe(const struct acpm_cfg *c, struct acpm_chan *h)
{
	(void)c;
	h->why = ACPM_W_LAYOUT;
	h->have_desc = 0;
	return ACPM_E_UNSURE;
}

int acpm_chan_id(const struct acpm_cfg *c, const struct acpm_chan *h, uint32_t idx, uint32_t *id)
{
	(void)c; (void)h; (void)idx; (void)id;
	return 0;
}

int acpm_qword(const struct acpm_cfg *c, const struct acpm_chan *h, int rsp, uint32_t i, uint32_t *v)
{
	(void)c; (void)h; (void)rsp; (void)i; (void)v;
	return 0;
}

int acpm_pmic_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val; (void)t;
	return ACPM_E_UNSURE;
}

int acpm_pmic_update(const struct acpm_cfg *c, uint32_t reg, uint32_t val, uint32_t mask, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val; (void)mask; (void)t;
	return ACPM_E_UNSURE;
}

int acpm_s11_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val; (void)t;
	return ACPM_E_UNSURE;
}

int acpm_s10_read(const struct acpm_cfg *c, uint32_t reg, uint32_t *val, struct acpm_trace *t)
{
	(void)c; (void)reg; (void)val; (void)t;
	return ACPM_E_UNSURE;
}

int acpm_dvfs_ndom(void) { return 0; }
int acpm_dvfs_dom(int i, uint32_t *idx) { (void)i; (void)idx; return 0; }
int acpm_dvfs_get(const struct acpm_cfg *c, uint32_t dom, uint32_t *khz, struct acpm_trace *t)
{
	(void)c; (void)dom; (void)khz; (void)t;
	return ACPM_E_UNSURE;
}

#endif
