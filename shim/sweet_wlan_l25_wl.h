/*
 * sweet_wlan_l25_wl.h - the L25 store whitelist (T-5173, classes L1-L9 of docs/research/T-3e93-l25-wiring-design.md s.1; W-JOIN P-W2/W-D5).
 *
 * PURE: no bus access, no engine header. The glue (patch 0174 `sw3_g2_ok`, later ticket) calls ONE function before every host/doorbell store the L25 engine
 * makes: swl25w_ok(w, space, off, v, old, &why) returns the class (SWL25W_L1..L9) or -1 and a reason. Everything the judgment needs about the ring state is
 * READ through the accessor `w->rd` (the glue's cache-flushing G1/DATA word read), never taken from the engine's books, so a wrong engine cursor cannot widen
 * the list. The classes are written down HERE from the cited layouts, independently of swdp_plan / swdp_audit (R107 #5: a copy of the engine's own audit would
 * accept whatever the engine does). Tags: [src] read from source (the cited file), [guess] not verified; no hardware was involved.
 *
 * Space ids are this header's own (SWL25W_SP_*); the glue maps SWG_SP_PCIE / SWG_SP_HOST / SWG_SP_DATA onto them. Offsets are bytes from the start of the G1 block
 * (SWL25W_SP_HOST) or of the DATA block (SWL25W_SP_DATA) or the PCIe core offset (SWL25W_SP_PCIE). Every word is 4 byte aligned.
 *
 * What is free (the only unknowable bytes, everything else is derived from the ring state): the 12 MAC bytes of the flow-create item (exact when w->mac_set /
 * w->bssid_set), the 14 B Ethernet header and the length of a tx item, the frame bytes of a tx buffer, the 164 key bytes of `wsec_key` and the 4 value bytes of SET `wsec`,
 * the transaction id of an ioctl request and the (non-zero) request id of a buffer post. Each is bounded to its place (buffer, field, count). The ioctl payload buffer is free to
 * WRITE while no ioctl is in transit, but an ioctl only commits if its command, lengths and iovar name are one of the L25 calls (23 rows since patch 0363: 0282 added seven, 0288 the BSS_INFO GET, 0292 the keep-phase SET escan, ALL-ON T-32 the SET mkeep_alive, 0363 the three read-only LOWCLK B3 GETs WLC_GET_PM, country and chanspecs) (four until T-924f added the read-only counters GET, T-9d71 added the two read-only association-request GETs, W-UI 2026-11-08 the read-only chanspec, GET_BSSID and GET_RSSI) (T-5173 r1 F1: a free iovar can carry a host PA).
 * Not covered here (own ticket): the L24 stores (join commands, escan), the S2MPU/granule guard of the DATA block, arm/disarm, and the deep-sleep round (swg_ds_round: ring-0 type 0x23
 * items, their index and doorbell, the BAR0 window store). A DS request during L25 is REFUSED here, fail closed (T-5173 r1 F6); the glue wiring ticket names the list that covers it.
 * The stop path is outside this judge too (T-5173 r2 N3): swg_undo (PCIe 0xC34 := 0, EP and RC Command) and W17 must reach the bus without swl25w_ok; the glue keeps its own exact entries for them, and they stay
 * allowed after a refusal, because a refusal here is what starts that path.
 * The struct's layout is validated on every call (swl25w_layout_ok); a wrong layout refuses everything.
 */
#ifndef SWEET_WLAN_L25_WL_H
#define SWEET_WLAN_L25_WL_H

#include <stdint.h>

enum { SWL25W_SP_PCIE = 1, SWL25W_SP_HOST = 2, SWL25W_SP_DATA = 3 };
enum { SWL25W_L1 = 1, SWL25W_L2, SWL25W_L3, SWL25W_L4, SWL25W_L5, SWL25W_L6, SWL25W_L7, SWL25W_L8, SWL25W_L9, SWL25W_NCLASS = 9 };

/* constants, each with its source [src] */
#define SWL25W_DB0_OFF      0x140u      /* PCIe core +0x140, H2D doorbell 0 (sweet_wlan_g2.h SWG_DB0_OFF) */
#define SWL25W_RX_N         16u         /* rx posts of L25 = items of ring 1 with a shadow (sweet_wlan_l25.h SWL25_RXPOSTS, SWL25_SHADOW_ITEMS) */
#define SWL25W_RX_MAX       255u        /* patch 0357: the most rx buffers the DATA block may hold and the pool may post (SWDP_RX_MAX = Linux BRCMF_DEF_MAX_RXBUFPOST, pcie.c:246); the glue's layout sets rx_n from the block it allocated, the judge never from the engine's books */
#define SWL25W_RXB_MAX      32u         /* patch 0357: most ring-1 items one batched rx post writes before its ONE index store (SWDP_RXB_MAX = BRCMF_MSGBUF_RXBUFPOST_THRESHOLD, msgbuf.c:58) */
#define SWL25W_TX_N         16u         /* flow-ring items with a shadow (SWL25_SHADOW_ITEMS) */
#define SWL25W_R1_N         256u        /* sprint 2026-11-08: the ring-1 posts the ONE-SHOT L25 engine can make (its shadow, sweet_wlan_l25.h SWL25_R1_SHADOW); the judge itself accepts every ring-1 slot and laps (en0) */
#define SWL25W_BUF_SZ       2048u       /* one rx or tx buffer (SWL25_BUF_SZ) */
#define SWL25W_TX_OFF       0x4000u     /* DATA: 16 tx buffers (SWL25_TX_OFF) */
#define SWL25W_RX_OFF       0xC000u     /* DATA: 16 rx buffers, dongle-written, never host-written (SWL25_RX_OFF) */
#define SWL25W_FLOW_DEPTH   64u         /* flow ring items (SWL25_FLOW_DEPTH) */
#define SWL25W_FLOW_ISZ     48u         /* base tx item (SWDP_ISZ_TX_BASE, bcmmsgbuf.h:42): the device flags2 0x40943041 lacks PCIE_SHARED2_TXPOST_EXT 0x400000 (bcmpcie.h:154) */
#define SWL25W_RX_RID       0x52580000u /* 'RX' << 16 + buffer (SWDP_RX_RID, sweet_wlan_data.h:291) */
#define SWL25W_TX_RID       0x54580000u /* 'TX' << 16 + buffer (SWDP_TX_RID) */
#define SWL25W_MAXFRAME     1514u       /* SWDP_ETH_MAXFRAME */
#define SWL25W_ETH_HLEN     14u
#define SWL25W_IOCT_WORDS   355u        /* ioctl payload words: the 355-word buffer of docs/research/T-3e93 s.1 L6 (1420 B) */
#define SWL25W_IOCTREQ_ID   0xFFFEu     /* SWD4B_IOCTL_REQID */
#define SWL25W_RESP_LEN     8192u       /* SWD_RESPBUF_LEN = SWD_EVBUF_LEN */
#define SWL25W_BUFS         8u          /* SWD_RESPBUF_N = SWD_EVBUF_N */
#define SWL25W_MSG_FLOW_CREATE 0x03u
#define SWL25W_MSG_IOCTL_REQ   0x09u
#define SWL25W_MSG_RESPBUF     0x0Bu
#define SWL25W_MSG_EVBUF       0x0Du
#define SWL25W_MSG_RXPOST      0x11u
#define SWL25W_MSG_TXPOST      0x0Fu
#define SWL25W_WLC_GET_VAR     262u
#define SWL25W_WLC_GET_BSSID   23u     /* W-UI sprint 2026-11-08: read-only GETs after L26 [src refs/wlan-stage5/wlioctl_defs.h.new:703, :810] */
#define SWL25W_WLC_GET_RSSI    127u
#define SWL25W_WLC_GET_BSS_INFO 136u   /* patch 0288 [src wlioctl_defs.h.new:819]: read-only, the BSS the firmware joined */
#define SWL25W_WLC_GET_PM      85u     /* patch 0363 (LOWCLK B3, docs/research/161 s.3.2) [src Linux v6.6 brcmfmac fwil.h:49 BRCMF_C_GET_PM; OpenBSD bwfmreg.h:284 BWFM_C_GET_PM]: read-only, 4-byte int. SET_PM (86) stays refused */
#define SWL25W_WLC_SET_VAR     263u
#define SWL25W_D2H_BATCH    16u         /* most ring-2 items consumed at once (SWG_E_BATCH, sweet_wlan_g2.h:103, 2118) */
#define SWL25W_MSG_IOCTL_CMPLT 0x0Cu    /* the ioctl's completion on ring 2 (SWD4B_MSG_IOCTL_CMPLT, bcmmsgbuf.h) */
#define SWL25W_EP(n)        (((254u + (n)) % 253u) & 0xFFu)     /* epoch of the n-th item of a ring, n counted from the ring's start over every lap (SWDP_EPOCH_INIT, SWDP_EPOCH_MOD; DHD's seqnum never resets at a wrap) */

struct swl25w {
	/* layout, filled once by the glue from its own G1/DATA plan (byte offsets from the block start) */
	uint32_t h2d_w, d2h_w, d2h_r;           /* index arrays, 4 B per slot: H2D write (ring 0, ring 1, flow slot = flowid), D2H write (dongle-written) and read (rings 2, 3, 4 = slots 0, 1, 2) */
	uint32_t h2d_r;                         /* sprint 2026-11-08: the H2D read index array (dongle-written; slot 0 = ring 0): the ring-full check of a ring-0 slot after the first lap */
	uint32_t ring[5];                       /* rings 0..4 */
	uint32_t depth[5];                      /* items of rings 0..4 */
	uint32_t ioctbuf, respbuf, evbuf;       /* the ioctl payload buffer, 8 response buffers, 8 event buffers */
	uint64_t g1_pa, data_pa;                /* the PA the dongle sees for offset 0 of each block */
	uint32_t g1_len, data_len;              /* block lengths; every region above must lie inside g1_len, the DATA regions inside data_len (swl25w_layout_ok) */
	uint32_t rx_n;                          /* patch 0357: rx buffers in the DATA block (buffer b at SWL25W_RX_OFF + b * 2048, b < rx_n); the glue's layout sets it from its own block length; 0 = SWL25W_RX_N (16, the old layout) */
	uint32_t flowid;                        /* SWL25_FLOWID (2); its H2D slot is flowid, its ring id flowid + 3 (swdp_ringid) */
	uint8_t mac[6], bssid[6];               /* station MAC / AP MAC of the flow-create item, enforced when the _set flag is 1 */
	uint8_t mac_set, bssid_set;
	uint8_t flow_open;                      /* the dongle answered the flow create: tx posts and tx payload need it */
	uint32_t pend_db;                       /* the doorbell word the last accepted index store (L7/L8) asks for, 0 = none; set and cleared ONLY by swl25w_done() */
	uint8_t flow_created;                   /* a flow-create item was committed (one per L25); set ONLY by swl25w_done() */
	uint8_t ioct_busy;                      /* an ioctl request is committed and its completion not yet consumed; set and cleared ONLY by swl25w_done() */
	uint32_t ioct_trans;                    /* the transaction id of that request; ~0 = unknown (a read failed), which no completion matches. Set ONLY by swl25w_done() */
	uint32_t wrap1;                         /* sprint 2026-11-08: ring-1 laps completed (an accepted ring-1 index store depth-1 -> 0); the rx-post epoch counts wrap1 * depth + slot. Set ONLY by swl25w_done() */
	uint32_t wrap0;                         /* sprint 2026-11-08: ring-0 laps completed (an accepted ring-0 index store depth-1 -> 0); the ring-0 epoch counts wrap0 * depth + slot. Set ONLY by swl25w_done() */
	int (*rd)(void *ctx, int space, uint32_t off, uint32_t *v);     /* 0 = ok; the glue's flushing read */
	void *ctx;
	const volatile uint16_t *escan_sync;    /* review 0292 F2: the sync_id of the escan START that is being sent (the engine's x->escan_sync, set by swl25g_wl_arm); NULL = only the boot scan's SWL25W_ESCAN_SYNC0 */
};

static inline uint32_t swl25w_rxn(const struct swl25w *w) { return w->rx_n != 0u ? w->rx_n : SWL25W_RX_N; }
static inline uint32_t swl25w_lo(uint64_t a) { return (uint32_t)a; }
static inline uint32_t swl25w_hi(uint64_t a) { return (uint32_t)(a >> 32); }
static inline uint32_t swl25w_ring_id(const struct swl25w *w) { return w->flowid + 3u; }     /* swdp_ringid(2) = 5 */

static inline int
swl25w_rdw(const struct swl25w *w, int space, uint32_t off, uint32_t *v)
{
	return w->rd == 0 || w->rd(w->ctx, space, off, v) != 0;
}

/* tx needs both: the create was committed (the stream shows it) and the dongle answered it (the glue says so). T-5173 r2 N6 */
static inline int swl25w_flow_up(const struct swl25w *w) { return w->flow_open && w->flow_created; }

/* The layout the glue filled: nothing below trusts it unchecked. 0 = ok, else the number of the clause it fails (the host test names it). Every G1 region non-empty, aligned, inside g1_len and disjoint from the others
 * (64-bit arithmetic, no wrap); the DATA block holds the flow items, tx and rx buffers; the flow slot is the one L25 uses; the two blocks exist, do not wrap and do not overlap. Item sizes of the rings: sweet_wlan_dma.h swd_rings (40, 32, 24, 24, 40). */
static inline int
swl25w_layout_bad(const struct swl25w *w)
{
	const uint64_t o[12] = { w->h2d_w, w->d2h_w, w->d2h_r, w->ring[0], w->ring[1], w->ring[2], w->ring[3], w->ring[4], w->ioctbuf, w->respbuf, w->evbuf, w->h2d_r };
	const uint64_t n[12] = { 12u, 12u, 12u, (uint64_t)w->depth[0] * 40u, (uint64_t)w->depth[1] * 32u, (uint64_t)w->depth[2] * 24u, (uint64_t)w->depth[3] * 24u, (uint64_t)w->depth[4] * 40u,
				 SWL25W_IOCT_WORDS * 4u, (uint64_t)SWL25W_BUFS * SWL25W_RESP_LEN, (uint64_t)SWL25W_BUFS * SWL25W_RESP_LEN, 4u };
	unsigned a, b;

	if (w->rd == 0) {
		return 1;
	}
	if (w->flowid != 2u) {
		return 2;
	}
	if (w->g1_pa == 0u || w->data_pa == 0u) {
		return 3;
	}
	if ((w->g1_pa & 0xFFFu) != 0u || (w->data_pa & 0xFFFu) != 0u) {
		return 4;
	}
	if (w->data_len < SWL25W_RX_OFF + SWL25W_RX_N * SWL25W_BUF_SZ) {
		return 5;
	}
	if (w->rx_n != 0u && (w->rx_n < SWL25W_RX_N || w->rx_n > SWL25W_RX_MAX || w->data_len < SWL25W_RX_OFF + w->rx_n * SWL25W_BUF_SZ)) {
		return 14;      /* patch 0357: the rx buffer count must fit the block (16..255) */
	}
	if (w->g1_pa + w->g1_len < w->g1_pa || w->data_pa + w->data_len < w->data_pa) {
		return 6;
	}
	if (w->g1_pa < w->data_pa + w->data_len && w->data_pa < w->g1_pa + w->g1_len) {
		return 7;
	}
	if (w->depth[0] < 2u || w->depth[1] < SWL25W_RX_N) {
		return 8;
	}
	for (a = 0; a < 5u; a++) {
		if (w->depth[a] == 0u || w->depth[a] > 0x10000u) {
			return 9;
		}
	}
	for (a = 0; a < 12u; a++) {
		if ((o[a] & 3u) != 0u) {
			return 10;
		}
		if (o[a] + n[a] > w->g1_len) {
			return 11;
		}
		if (o[a] == 0u && a != 3u) {    /* only ring 0 may sit at G1+0 (the plan's first block) */
			return 12;
		}
	}
	for (a = 0; a < 12u; a++) {
		for (b = a + 1u; b < 12u; b++) {
			if (o[a] < o[b] + n[b] && o[b] < o[a] + n[a]) {
				return 13;
			}
		}
	}
	return 0;
}
static inline int swl25w_layout_ok(const struct swl25w *w) { return swl25w_layout_bad(w) == 0; }

/* ------------------------------------------------------------------ item words (the shape of one item, word by word) */

/* one word of a ring-0 item at ring slot `slot`; NULL = ok, else the reason. Words after word 0 take their type from word 0 of the same item, read back. */
static inline const char *
swl25w_ring0_word(const struct swl25w *w, uint32_t slot, uint32_t k, uint32_t v, int *cls)
{
	uint32_t ty, t0;
	const uint32_t ep = SWL25W_EP(w->wrap0 * w->depth[0] + slot);
	uint64_t pa;

	if (k >= 10u) {
		return "ring-0 item word index";
	}
	if (k == 0u) {
		ty = v & 0xFFu;
	} else {
		if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u, &t0)) {
			return "ring-0 item word 0 unreadable";
		}
		ty = t0 & 0xFFu;
	}
	if (ty == SWL25W_MSG_FLOW_CREATE) {     /* L1 [src bcmmsgbuf.h:524-540 via sweet_wlan_data.h:176-191] */
		*cls = SWL25W_L1;
		if (w->flow_created || w->flow_open) {
			return "L1 flow create: one per L25, the flow ring was already created";
		}
		switch (k) {
		case 0: return v == (SWL25W_MSG_FLOW_CREATE | ep << 24) ? 0 : "L1 flow create: word 0 (type 0x03, flags 0, epoch)";
		case 1: return v == 0u ? 0 : "L1 flow create: request id is 0";
		case 2: return !w->bssid_set || ((v & 0xFFu) == w->bssid[0] && (v >> 8 & 0xFFu) == w->bssid[1] && (v >> 16 & 0xFFu) == w->bssid[2] && (v >> 24) == w->bssid[3]) ? 0 : "L1 flow create: da[0..3] is not the BSSID";
		case 3: return (!w->bssid_set || ((v & 0xFFu) == w->bssid[4] && (v >> 8 & 0xFFu) == w->bssid[5])) &&
			    (!w->mac_set || ((v >> 16 & 0xFFu) == w->mac[0] && (v >> 24) == w->mac[1])) ? 0 : "L1 flow create: da[4..5] / sa[0..1]";
		case 4: return !w->mac_set || ((v & 0xFFu) == w->mac[2] && (v >> 8 & 0xFFu) == w->mac[3] && (v >> 16 & 0xFFu) == w->mac[4] && (v >> 24) == w->mac[5]) ? 0 : "L1 flow create: sa[2..5] is not the station MAC";
		case 5: return v == w->flowid << 16 ? 0 : "L1 flow create: tid/if_flags 0, flow ring id is ours";
		case 6: return v == 0u ? 0 : "L1 flow create: tc/priority/int_vector 0";
		case 7: return v == (SWL25W_FLOW_DEPTH | SWL25W_FLOW_ISZ << 16) ? 0 : "L1 flow create: max_items 64, item size 48";
		case 8: return v == swl25w_lo(w->data_pa) ? 0 : "L1 flow create: flow ring pointer lo is not DATA+0";
		default: return v == swl25w_hi(w->data_pa) ? 0 : "L1 flow create: flow ring pointer hi is not DATA";
		}
	}
	*cls = SWL25W_L2;
	if (ty == SWL25W_MSG_IOCTL_REQ) {       /* ioctl request, swd4b_item_ioctl [src sweet_wlan_d4b.h:91-110] */
		switch (k) {
		case 0: return v == (SWL25W_MSG_IOCTL_REQ | 0x80u << 16 | ep << 24) ? 0 : "L2 ioctl request: word 0 (type 9, phase 0x80, epoch)";
		case 1: return v == SWL25W_IOCTREQ_ID ? 0 : "L2 ioctl request: request id 0xFFFE";
		case 2: return v == SWL25W_WLC_GET_VAR || v == SWL25W_WLC_SET_VAR || v == SWL25W_WLC_GET_BSSID || v == SWL25W_WLC_GET_RSSI || v == SWL25W_WLC_GET_BSS_INFO || v == SWL25W_WLC_GET_PM ? 0 : "L2 ioctl request: cmd is not GET_VAR/SET_VAR/GET_BSSID/GET_RSSI/GET_BSS_INFO/GET_PM";
		case 3: return (v >> 16) <= SWL25W_IOCT_WORDS * 4u ? 0 : "L2 ioctl request: input length above the payload buffer";
		case 4: return (v >> 16) == 0u && (v & 0xFFFFu) <= SWL25W_IOCT_WORDS * 4u ? 0 : "L2 ioctl request: output length above the payload buffer / reserved";
		case 5: return v == 0u ? 0 : "L2 ioctl request: reserved";
		case 6: return v == swl25w_lo(w->g1_pa + w->ioctbuf) ? 0 : "L2 ioctl request: payload pointer lo is not IOCTBUF";
		case 7: return v == swl25w_hi(w->g1_pa + w->ioctbuf) ? 0 : "L2 ioctl request: payload pointer hi is not IOCTBUF";
		default: return v == 0u ? 0 : "L2 ioctl request: reserved1";
		}
	}
	if (ty == SWL25W_MSG_RESPBUF || ty == SWL25W_MSG_EVBUF) {       /* buffer posts, swd4b_item_respbuf / _evbuf [src sweet_wlan_d4b.h:49-88] */
		const uint64_t base = w->g1_pa + (ty == SWL25W_MSG_RESPBUF ? w->respbuf : w->evbuf);
		uint32_t lo, b;

		switch (k) {
		case 0: return v == (ty | 0x80u << 16 | ep << 24) ? 0 : "L2 buffer post: word 0 (type, phase 0x80, epoch)";
		case 1: return v != 0u ? 0 : "L2 buffer post: request id 0 is DHD_PKTID_INVALID";   /* our own request id of the buffer (free, not 0: sweet_wlan_g2.h:1534) */
		case 2: return v == SWL25W_RESP_LEN ? 0 : "L2 buffer post: host_buf_len is not 8192";
		case 3: case 6: case 7: case 8: case 9: return v == 0u ? 0 : "L2 buffer post: reserved words are 0";
		case 4: b = (v - swl25w_lo(base)) / SWL25W_RESP_LEN; return (v - swl25w_lo(base)) % SWL25W_RESP_LEN == 0u && b < SWL25W_BUFS ? 0 : "L2 buffer post: pointer lo is not one of the 8 buffers";
		default:
			if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u + 16u, &lo)) {
				return "L2 buffer post: pointer lo unreadable";
			}
			b = (lo - swl25w_lo(base)) / SWL25W_RESP_LEN;
			pa = base + (uint64_t)b * SWL25W_RESP_LEN;
			return (lo - swl25w_lo(base)) % SWL25W_RESP_LEN == 0u && b < SWL25W_BUFS && v == swl25w_hi(pa) ? 0 : "L2 buffer post: pointer hi";
		}
	}
	return "ring-0 item: type is not flow create / ioctl request / buffer post";
}

/* one word of an rx post, ring 1 slot `slot` [src sweet_wlan_data.h:192-203 swdp_build_rxpost]: request id RX_RID+b for one of the 16 rx buffers b, length 2048, no metadata, the data pointer is buffer b.
 * Sprint 2026-11-08: buffers are re-posted after their rx completion, so slot n no longer equals buffer n; b is read from word 1 (the word itself when k == 1, else memory, as ring 0 reads its word 0) */
/* Part A (batched stores): have_rid != 0 hands in word 1 of the SAME post (the batch stores it before words 6/7, as the per-word plan order does); 0 = read it from memory (the per-word path) */
static inline const char *
swl25w_rx_word2(const struct swl25w *w, uint32_t slot, uint32_t k, uint32_t v, int have_rid, uint32_t rid1)
{
	uint32_t rid = v, b;
	uint64_t pa;

	if (slot < SWL25W_RX_N && w->wrap1 == 0u) {
		rid = SWL25W_RX_RID + slot;     /* the first 16 posts: slot n = buffer n exactly (the old rule) */
		if (k == 1u && v != rid) {
			return "L3 rx post: request id is not RX_RID + slot (first 16 posts)";
		}
	} else if (k != 1u && (k == 6u || k == 7u)) {     /* a re-post: the pointer words follow word 1 in the plan's order */
		if (have_rid) {
			rid = rid1;
		} else if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[1] + slot * 32u + 4u, &rid)) {
			return "L3 rx post: request id word unreadable";
		}
	}
	b = rid - SWL25W_RX_RID;
	if ((k == 1u || k == 6u || k == 7u) && (rid < SWL25W_RX_RID || b >= swl25w_rxn(w))) {
		return "L3 rx post: request id is not RX_RID + one of the rx buffers of the block (< rx_n, 16 unless the glue allocated more)";     /* GAP: whether the re-posted buffer is free is the engine's book, not checked here */
	}
	pa = w->data_pa + SWL25W_RX_OFF + (uint64_t)b * SWL25W_BUF_SZ;
	switch (k) {
	case 0: return v == (SWL25W_MSG_RXPOST | SWL25W_EP(w->wrap1 * w->depth[1] + slot) << 24) ? 0 : "L3 rx post: word 0 (type 0x11, epoch)";
	case 1: return 0;
	case 2: return v == SWL25W_BUF_SZ << 16 ? 0 : "L3 rx post: lengths (no metadata, 2048 data)";
	case 3: case 4: case 5: return v == 0u ? 0 : "L3 rx post: reserved / metadata address words are 0";
	case 6: return v == swl25w_lo(pa) ? 0 : "L3 rx post: data pointer lo is not the rx buffer its request id names";
	case 7: return v == swl25w_hi(pa) ? 0 : "L3 rx post: data pointer hi";
	default: return "L3 rx post: word index";
	}
}

static inline const char *
swl25w_rx_word(const struct swl25w *w, uint32_t slot, uint32_t k, uint32_t v)
{
	return swl25w_rx_word2(w, slot, k, v, 0, 0u);
}

/* one word of a tx post on the flow ring [src sweet_wlan_data.h:208-225 swdp_build_txpost, isz 48 (v1)]: epoch 0, request id TX_RID+n, Ethernet header free, flags 0x01 (802.3, prio 0), seg_cnt 1, no metadata */
static inline const char *
swl25w_tx_word(const struct swl25w *w, uint32_t slot, uint32_t k, uint32_t v)
{
	const uint64_t pa = w->data_pa + SWL25W_TX_OFF + (uint64_t)(slot % SWL25W_TX_N) * SWL25W_BUF_SZ;     /* sprint 2026-11-08: the flow ring (64) wraps; flow slot n uses tx buffer n % 16 */
	const uint32_t dl = v >> 16;

	switch (k) {
	case 0: return v == SWL25W_MSG_TXPOST ? 0 : "L4 tx post: word 0 (type 0x0F, ifid 0, flags 0, epoch 0)";
	case 1: return v == SWL25W_TX_RID + slot % SWL25W_TX_N ? 0 : "L4 tx post: request id is not TX_RID + slot % 16";
	case 2: case 3: case 4: return 0;       /* Ethernet header bytes 0..11 (free) */
	case 5: return (v >> 16) == 0x0101u ? 0 : "L4 tx post: packet flags 0x01 / seg_cnt 1";
	case 6: case 7: return v == 0u ? 0 : "L4 tx post: metadata address is 0";
	case 8: return v == swl25w_lo(pa) ? 0 : "L4 tx post: data pointer lo is not the tx buffer of the slot";
	case 9: return v == swl25w_hi(pa) ? 0 : "L4 tx post: data pointer hi";
	case 10: return (v & 0xFFFFu) == 0u && dl >= 1u && dl <= SWL25W_MAXFRAME - SWL25W_ETH_HLEN ? 0 : "L4 tx post: metadata length 0, data length 1..1500";
	case 11: return v == 0u ? 0 : "L4 tx post: ext_flags/marker word is 0";
	default: return "L4 tx post: word index";
	}
}

/* The ioctls of L25 [src sweet_wlan_l25_hs.h cur_etheraddr, wsec_key, wsec GET, wsec SET, and the read-only GETs counters (T-924f), assoc_info and assoc_req_ies (T-9d71), chanspec, GET_BSSID, GET_RSSI (W-UI swl25_live)]; the builder passes in = out = the payload length (swg_d4d_request, sweet_w
 * Patch 0282 (W-D6 boot B, the re-join of the keep thread, docs/research/W-D6-rejoin-dhcp.md s.3, 6): seven more rows. READ-ONLY: GET wpa_auth, auth, mfp (read back before deciding what a re-join must re-send; wsec GET is row 3). WRITES: SET join (the 76 B v1 ext-join params, content bound by chk 1)
 * and SET wpa_auth / auth / mfp with ONE value each (chk 2-4: the values the boot join set, so a re-send can only restore them). The SET wsec row is the boot one (4 value bytes free). Lengths: name + NUL + value [src sweet_wlan_join_fw.h swj_var_u32, swj_var_buf]. */
#define SWL25W_ESCAN_SYNC0 0x1234u      /* the boot scan's sync_id [src SWG_E_SYNC]; the keep scans use SWL25W_ESCAN_SYNC0 + 1 .. + 0xFF (one per scan), never more */
#define SWL25W_ESCAN_SYNCN 0xFFu
#define SWL25W_NIOVARS 23u
#define SWL25W_CHK_NONE 0u
#define SWL25W_CHK_JOIN 1u      /* SET join: the 76 B ext-join v1 payload, see swl25w_chk_join */
#define SWL25W_CHK_WPA  2u      /* SET wpa_auth: value 0x0080 (WPA2-PSK, SWJ_WPA2_AUTH_PSK, sweet_wlan_join.h:97), the other three bytes 0 */
#define SWL25W_CHK_BSSINFO 4u   /* GET_BSS_INFO: word 0 = 1024 (the length of the answer buffer the firmware may fill), every other payload byte 0 */
#define SWL25W_CHK_ESCAN 5u     /* SET escan: the 80 B passive all-channel escan START of swg_e_escan_arg (chans 0), byte for byte (version 2 or 3), see swl25w_chk_escan */
#define SWL25W_CHK_MKEEP 6u     /* SET mkeep_alive: the 11 B null-frame keep-alive header, 30000 ms, id 0, see swl25w_chk_mkeep */
#define SWL25W_CHK_ZERO 3u      /* SET auth / mfp: value 0 (open system, no MFP: swj_join_cmds vals[3], vals[4]) */
static const struct { const char *name; uint32_t cmd, len, chk; } swl25w_iovars[SWL25W_NIOVARS] = {
	{ "cur_etheraddr", SWL25W_WLC_GET_VAR, 20u, 0u },   /* name 14 + 6 */
	{ "wsec_key", SWL25W_WLC_SET_VAR, 173u, 0u },       /* name 9 + 164 key bytes (free) */
	{ "wsec", SWL25W_WLC_GET_VAR, 9u, 0u },             /* name 5 + 4 */
	{ "wsec", SWL25W_WLC_SET_VAR, 9u, 0u },             /* name 5 + 4 value bytes (free) */
	{ "counters", SWL25W_WLC_GET_VAR, 1200u, 0u },      /* T-924f: name 9 + zeros, reply room 1200 B (SWL25_CNT_LEN in sweet_wlan_l25_hs.h); a GET: every byte after the name is zero */
	{ "assoc_info", SWL25W_WLC_GET_VAR, 64u, 0u },      /* T-9d71: name 11 + zeros, reply room 64 B (SWL25_AINFO_LEN): the firmware's wl_assoc_info_t (32 B), read-only */
	{ "assoc_req_ies", SWL25W_WLC_GET_VAR, 512u, 0u },  /* T-9d71: name 14 + zeros, reply room 512 B (SWL25_AIES_LEN): the IEs of the association request the firmware sent, read-only */
	{ "chanspec", SWL25W_WLC_GET_VAR, 13u, 0u },        /* W-UI: name 9 + 4 zeros, the current chanspec, read-only (swl25_live) */
	{ "", SWL25W_WLC_GET_BSSID, 6u, 0u },              /* W-UI: no name, 6 zero bytes; the BSSID the firmware is associated to, read-only */
	{ "", SWL25W_WLC_GET_RSSI, 12u, 0u },              /* W-UI: no name, scb_val_t 12 zero bytes (val 0, ea zero = the associated AP), read-only */
	{ "join", SWL25W_WLC_SET_VAR, 81u, SWL25W_CHK_JOIN },   /* 0282: name 5 + 76 B wl_extjoin_params_v1_t; SSID_len 1..32, BSSID wildcard or the known AP */
	{ "wpa_auth", SWL25W_WLC_GET_VAR, 13u, 0u },        /* 0282: name 9 + 4 zeros, read-only */
	{ "auth", SWL25W_WLC_GET_VAR, 9u, 0u },             /* 0282: name 5 + 4 zeros, read-only */
	{ "mfp", SWL25W_WLC_GET_VAR, 8u, 0u },              /* 0282: name 4 + 4 zeros, read-only */
	{ "wpa_auth", SWL25W_WLC_SET_VAR, 13u, SWL25W_CHK_WPA },    /* 0282: name 9 + value 0x80 */
	{ "auth", SWL25W_WLC_SET_VAR, 9u, SWL25W_CHK_ZERO },        /* 0282: name 5 + value 0 */
	{ "mfp", SWL25W_WLC_SET_VAR, 8u, SWL25W_CHK_ZERO },         /* 0282: name 4 + value 0 */
	{ "", SWL25W_WLC_GET_BSS_INFO, 1024u, SWL25W_CHK_BSSINFO },  /* 0288: no name; 1024 B in = out: the length word 1024 and zeros; read-only (the answer is the AP the join found, T-fca2) */
	{ "escan", SWL25W_WLC_SET_VAR, 86u, SWL25W_CHK_ESCAN },     /* 0292: name 6 + the 80 B passive escan START (wl_escan_params v2/v3, channel_num 0 = all channels, no channel list), content bound by chk 5; the keep-phase scan (WIFI-COVERAGE D3/D4) */
	{ "", SWL25W_WLC_GET_PM, 4u, 0u },                 /* 0363 (LOWCLK B3): no name, 4 zero bytes in = out; the firmware's power-save mode (0 OFF, 1 MAX, 2 FAST), read-only; SET_PM (86) has no row */
	{ "country", SWL25W_WLC_GET_VAR, 20u, 0u },        /* 0363: name 8 + 12 zeros (brcmf_fil_country_le: abbrev[4], rev, ccode[4]), read-only; the SET and WLC_SET_COUNTRY (84) have no row */
	{ "chanspecs", SWL25W_WLC_GET_VAR, 1200u, 0u },    /* 0363: name 10 + zeros, reply room 1200 B (299 entries; brcmf_chanspec_list: count + u32 elements), read-only; a GET: every byte after the name is zero, so no per-band input form */
	{ "mkeep_alive", SWL25W_WLC_SET_VAR, 23u, SWL25W_CHK_MKEEP },   /* ALL-ON T-32: name 12 + the 11 B wl_mkeep_alive_pkt_v1_t header, content bound by chk 6; the firmware NULL-frame keep-alive (research 138) */
};

/* byte `off` of the ioctl payload buffer, read back through the judge's accessor; 0 = ok */
static inline int
swl25w_pbyte(const struct swl25w *w, uint32_t off, uint32_t *b)
{
	uint32_t v = 0u;

	if (off >= SWL25W_IOCT_WORDS * 4u || swl25w_rdw(w, SWL25W_SP_HOST, w->ioctbuf + (off & ~3u), &v)) {
		return 1;
	}
	*b = v >> (8u * (off & 3u)) & 0xFFu;
	return 0;
}

/* SET join (patch 0282): the 76 B wl_extjoin_params_v1_t that follows "join\0" (value offset 5) [src sweet_wlan_join_fw.h swj_join_ext_v1 and its comment: version u16 + pad u16 = 0 at +0, SSID_len u32 at +4, SSID[32] at +8, scan params at +40 (scan_type 0xff,
 * 3 zero bytes, four -1 words = 0xff x16 at +44), assoc_v1 at +60: version u16, flags u16 = 0, bssid[6] at +64, bssid_cnt u16 = 0 at +70, chanspec_num i32 = 0 at +72]. Bounded: SSID_len 1..32, SSID bytes after SSID_len zero, every reserved field exactly the value above, BSSID the wildcard
 * ff:ff:ff:ff:ff:ff or (w->bssid_set) the known AP. The SSID bytes themselves are free (the credentials). NULL = ok, else the reason. */
static inline const char *
swl25w_chk_join(const struct swl25w *w)
{
	static const uint32_t jv = 5u;       /* value offset: "join" + NUL */
	uint32_t i, b, sl, wild = 1u, known = w->bssid_set;

	for (i = 0; i < 4u; i++) {
		if (swl25w_pbyte(w, jv + i, &b)) { return "L2 join payload: unreadable"; }
		if (b != 0u) { return "L2 join payload: version/pad bytes are not 0"; }
	}
	for (i = 0, sl = 0u; i < 4u; i++) {
		if (swl25w_pbyte(w, jv + 4u + i, &b)) { return "L2 join payload: unreadable"; }
		sl |= b << (8u * i);
	}
	if (sl < 1u || sl > 32u) {
		return "L2 join payload: SSID_len is not 1..32";
	}
	for (i = sl; i < 32u; i++) {
		if (swl25w_pbyte(w, jv + 8u + i, &b)) { return "L2 join payload: unreadable"; }
		if (b != 0u) { return "L2 join payload: SSID bytes after SSID_len are not 0"; }
	}
	for (i = 0; i < 20u; i++) {
		if (swl25w_pbyte(w, jv + 40u + i, &b)) { return "L2 join payload: unreadable"; }
		if (b != ((i == 0u || i >= 4u) ? 0xFFu : 0u)) { return "L2 join payload: scan parameters are not scan_type -1 and four -1 words"; }
	}
	for (i = 0; i < 4u; i++) {
		if (swl25w_pbyte(w, jv + 60u + i, &b)) { return "L2 join payload: unreadable"; }
		if (b != 0u) { return "L2 join payload: assoc version/flags are not 0"; }
	}
	for (i = 0; i < 6u; i++) {
		if (swl25w_pbyte(w, jv + 64u + i, &b)) { return "L2 join payload: unreadable"; }
		wild &= b == 0xFFu;
		known &= b == w->bssid[i];
	}
	if (!wild && !known) {
		return "L2 join payload: BSSID is neither the wildcard nor the known AP";
	}
	for (i = 0; i < 6u; i++) {
		if (swl25w_pbyte(w, jv + 70u + i, &b)) { return "L2 join payload: unreadable"; }
		if (b != 0u) { return "L2 join payload: bssid_cnt / chanspec_num are not 0 (no channel list)"; }
	}
	return 0;
}

/* GET_BSS_INFO (patch 0288): the payload is the u32 length word 1024 (little endian: 00 04 00 00) and zeros up to the 1024 B; anything else is refused. NULL = ok */
static inline const char *
swl25w_chk_bssinfo(const struct swl25w *w)
{
	uint32_t i, v = 0u;

	for (i = 0; i < 1024u; i += 4u) {
		if (swl25w_rdw(w, SWL25W_SP_HOST, w->ioctbuf + i, &v)) {
			return "L2 bss_info payload: unreadable";
		}
		if (v != (i == 0u ? 1024u : 0u)) {
			return "L2 bss_info payload: not the length word 1024 and zeros";
		}
	}
	return 0;
}

/* SET escan (patch 0292): the 80 B that follow "escan\0" (value offset 6) are the passive escan START and nothing else. Expected bytes, written out here on purpose (not taken from the builder swg_e_escan_arg), EXCEPT the sync id: that one comes from the audited code (x->escan_sync via w->escan_sync, review 0292 re-check N3) and only its range 0x1234..0x1333 is checked independently
 * [src the layout comment of swg_e_l23 in sweet_wlan_g2e.h: wl_escan_params_v2/v3 offsets measured on a host compile of WI:915-960, 1089-1094; values BW bwfm.c:2196-2262, bwfmreg.h:519; obs boot 1 of T-35bd for the three chanspecs 0x1001 0x1006 0x100b]:
 * +0 version u32 = 2 or 3 (scan_ver major), +4 action u16 = 1 (START), +6 sync_id u16 = the expected id of this scan (w->escan_sync, 0x1234 for the boot scan, 0x1235..0x1333 for the keep scans), +8 params.version u16 = the same 2 or 3, +10 params.length u16 = 72, +12..47 ssid_len u32 = 0 and ssid[32] = 0, +48 bssid = ff x6, +54 bss_type = 2 (ANY),
 * +55 ssid_type/pad = 0, +56 scan_type = 1 (PASSIVE), +57..59 = 0, +60..75 nprobes / active / passive / home = -1 (0xff x16), +76 channel_num = 0 (u16: the firmware uses its own channel list) and nssid = 0 (u16); the payload ends at +79, no channel list.
 * NULL = ok, else the reason. */
static inline const char *
swl25w_chk_escan(const struct swl25w *w)
{
	static const uint32_t vo = 6u;      /* value offset: "escan" + NUL */
	uint32_t i, b, want, major = 0u;
	const uint32_t sync = w->escan_sync == NULL ? SWL25W_ESCAN_SYNC0 : (uint32_t)*w->escan_sync;       /* review 0292 F2: exactly the id of THIS scan, inside the range of the ids the code may use */

	if (sync < SWL25W_ESCAN_SYNC0 || sync > SWL25W_ESCAN_SYNC0 + SWL25W_ESCAN_SYNCN) { return "L2 escan payload: expected sync_id outside the range of the scan ids"; }
	if (swl25w_pbyte(w, vo, &major)) { return "L2 escan payload: unreadable"; }
	if (major != 2u && major != 3u) { return "L2 escan payload: scan version is not 2 or 3"; }
	for (i = 0; i < 80u; i++) {
		if (swl25w_pbyte(w, vo + i, &b)) { return "L2 escan payload: unreadable"; }
		if (i < 14u) {
			want = i == 0u ? major : i == 4u ? 1u : i == 6u ? (sync & 0xFFu) : i == 7u ? (sync >> 8) : i == 8u ? major : i == 10u ? 72u : 0u;
		} else if (i >= 48u && i < 54u) {
			want = 0xFFu;
		} else if (i >= 60u && i < 76u) {
			want = 0xFFu;
		} else if (i == 54u) {
			want = 2u;
		} else if (i == 56u) {
			want = 1u;
		} else if (i == 76u) {
			want = 0u;      /* channel_num 0: all channels; a non-zero count would need a list behind the 80 B */
		} else {
			want = 0u;
		}
		if (b != want) { return "L2 escan payload: not the passive all-channel escan START of the keep scan"; }
	}
	return 0;
}

/* SET mkeep_alive (ALL-ON T-32): the 11 B that follow "mkeep_alive\0" (value offset 12) are the null-frame keep-alive header and nothing else. Expected bytes written out here on purpose (not taken from the builder swj_mkeep_alive_cmd)
 * [src docs/research/138-wlan-keepalive.md s.1/s.2: wl_mkeep_alive_pkt_v1_t, bcmdhd wlioctl.h:7274-7290 and mainline fwil_types.h:1221-1237, little endian]: +0 version u16 = 1, +2 length u16 = 11, +4 period_msec u32 = 30000 (0x7530; no immediate bit, never 0 = stop),
 * +8 len_bytes u16 = 0 (null frame, no data[]), +10 keep_alive_id u8 = 0. NULL = ok, else the reason. */
static inline const char *
swl25w_chk_mkeep(const struct swl25w *w)
{
	static const uint8_t want[11] = { 0x01u, 0x00u, 0x0Bu, 0x00u, 0x30u, 0x75u, 0x00u, 0x00u, 0x00u, 0x00u, 0x00u };
	uint32_t i, b;

	for (i = 0; i < 11u; i++) {
		if (swl25w_pbyte(w, 12u + i, &b)) { return "L2 mkeep_alive payload: unreadable"; }
		if (b != want[i]) { return "L2 mkeep_alive payload: not the 30000 ms null-frame keep-alive of id 0"; }
	}
	return 0;
}

/* the 4 value bytes of a SET wpa_auth / auth / mfp at value offset nl + 1: chk WPA = 0x80 0 0 0, chk ZERO = 0 0 0 0. NULL = ok */
static inline const char *
swl25w_chk_val4(const struct swl25w *w, uint32_t vo, uint32_t want)
{
	uint32_t i, b;

	for (i = 0; i < 4u; i++) {
		if (swl25w_pbyte(w, vo + i, &b)) { return "L2 value payload: unreadable"; }
		if (b != (want >> (8u * i) & 0xFFu)) { return "L2 value payload: not the one value this row allows"; }
	}
	return 0;
}

/* An ioctl item at ring-0 slot `slot` may commit only as one of the rows above, by its command, lengths and the bytes of the payload buffer read back; a GET's bytes after the name are zero. Nothing else
 * is in transit (one ioctl at a time, DHD: dhd_msgbuf.c:9450). 0 = ok */
static inline const char *
swl25w_ioct_ok(const struct swl25w *w, uint32_t slot)
{
	uint32_t cmd, wi, wo, in, r, i, v = 0u;

	if (w->ioct_busy) {
		return "L2 ioctl payload: another ioctl is in transit";
	}
	if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u + 8u, &cmd) || swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u + 12u, &wi) ||
	    swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u + 16u, &wo)) {
		return "L2 ioctl payload: item unreadable";
	}
	in = wi >> 16;
	for (r = 0; r < SWL25W_NIOVARS; r++) {
		const char *const nm = swl25w_iovars[r].name;
		uint32_t nl = 0u, ok = 1u;

		if (cmd != swl25w_iovars[r].cmd || in != swl25w_iovars[r].len || (wo & 0xFFFFu) != in) {
			continue;
		}
		while (nm[nl] != '\0') {
			nl++;
		}
		for (i = 0; i < in && (i <= nl || (cmd != SWL25W_WLC_SET_VAR && swl25w_iovars[r].chk != SWL25W_CHK_BSSINFO)); i++) {       /* every GET (GET_VAR, GET_BSSID, GET_RSSI): all bytes after the name are zero */
			const uint32_t want = i < nl ? (uint8_t)nm[i] : 0u;

			if ((i & 3u) == 0u && swl25w_rdw(w, SWL25W_SP_HOST, w->ioctbuf + i, &v)) {
				return "L2 ioctl payload: unreadable";
			}
			if ((v >> (8u * (i & 3u)) & 0xFFu) != want) {
				ok = 0u;
				break;
			}
		}
		if (ok) {
			switch (swl25w_iovars[r].chk) {
			case SWL25W_CHK_JOIN: return swl25w_chk_join(w);
			case SWL25W_CHK_WPA: return swl25w_chk_val4(w, nl + 1u, 0x80u);
			case SWL25W_CHK_ZERO: return swl25w_chk_val4(w, nl + 1u, 0u);
			case SWL25W_CHK_BSSINFO: return swl25w_chk_bssinfo(w);
			case SWL25W_CHK_ESCAN: return swl25w_chk_escan(w);
			case SWL25W_CHK_MKEEP: return swl25w_chk_mkeep(w);
			default: return 0;
			}
		}
	}
	return "L2 ioctl payload: command, lengths and iovar name are not one of the L25 calls";
}

/* the item that a write-index store commits must be complete: every word, read back, passes its rule; an ioctl must also be one of the ten calls. 0 = ok */
static inline const char *
swl25w_item_ok(const struct swl25w *w, int ring, uint32_t slot)
{
	uint32_t k, v, n = ring == 0 ? 10u : ring == 1 ? 8u : SWL25W_FLOW_ISZ / 4u, o;
	const char *r;
	int cls;

	for (k = 0; k < n; k++) {
		if (ring == 2) {
			o = slot * SWL25W_FLOW_ISZ + 4u * k;
			if (swl25w_rdw(w, SWL25W_SP_DATA, o, &v)) {
				return "committed item: unreadable";
			}
			r = swl25w_tx_word(w, slot, k, v);
		} else {
			o = w->ring[ring] + slot * (ring == 0 ? 40u : 32u) + 4u * k;
			if (swl25w_rdw(w, SWL25W_SP_HOST, o, &v)) {
				return "committed item: unreadable";
			}
			r = ring == 0 ? swl25w_ring0_word(w, slot, k, v, &cls) : swl25w_rx_word(w, slot, k, v);
		}
		if (r != 0) {
			return r;
		}
	}
	if (ring == 0) {
		if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + slot * 40u, &v)) {
			return "committed item: unreadable";
		}
		if ((v & 0xFFu) == SWL25W_MSG_IOCTL_REQ) {
			return swl25w_ioct_ok(w, slot);
		}
	}
	return 0;
}

/* ------------------------------------------------------------------ the entry */

/* The current index of an H2D ring (low 16 bits of its write-index slot): ring 0, 1 or 2 = the flow ring. 0 = ok */
static inline int
swl25w_h2d_cur(const struct swl25w *w, int ring, uint32_t *idx)
{
	uint32_t v;

	if (swl25w_rdw(w, SWL25W_SP_HOST, w->h2d_w + 4u * (ring == 2 ? w->flowid : (uint32_t)ring), &v)) {
		return 1;
	}
	*idx = v & 0xFFFFu;
	return 0;
}

#define SWL25W_NO(m) do { if (why != 0) { *why = (m); } return -1; } while (0)

/* Judge one store. space/off: where; v: the word to be written (a 16-bit index store passes the whole word with the upper half kept); old: the word now there.
 * Returns the class SWL25W_L1..L9 or -1 (and *why). Does not write or change anything. */
static inline int
swl25w_ok(const struct swl25w *w, int space, uint32_t off, uint32_t v, uint32_t old, const char **why)
{
	const char *r;
	uint32_t cur, slot, k, i, now, wp;
	int cls = 0, ring;

	if (why != 0) {
		*why = "";
	}
	if (w == 0 || (off & 3u) != 0u) {
		SWL25W_NO("L25 store: no whitelist or unaligned offset");
	}
	if (!swl25w_layout_ok(w)) {
		SWL25W_NO("L25 store: the layout is invalid");
	}
	if (space != SWL25W_SP_PCIE && w->pend_db != 0u) {
		SWL25W_NO("L25 store: an index store's doorbell is still due");
	}
	if (space == SWL25W_SP_PCIE) {          /* L9 doorbell [src sweet_wlan_data.h:109-110 swdp_db_wr/db_rd; data_plan.h:20]: exactly the word the index store just made asks for, once */
		if (off != SWL25W_DB0_OFF) {
			SWL25W_NO("L9 doorbell: not PCIe core +0x140");
		}
		if (w->pend_db == 0u || v != w->pend_db) {
			SWL25W_NO("L9 doorbell: not the ring and index of the index store just made (or already rung)");
		}
		return SWL25W_L9;
	}
	if (space == SWL25W_SP_DATA) {
		if (off >= SWL25W_TX_OFF && off < SWL25W_TX_OFF + SWL25W_TX_N * SWL25W_BUF_SZ) {      /* L5 tx payload word [src sweet_wlan_data_plan.h:127 swdp_audit_store] */
			slot = (off - SWL25W_TX_OFF) / SWL25W_BUF_SZ;
			if (!swl25w_flow_up(w)) {
				SWL25W_NO("L5 tx payload: flow ring not open");
			}
			if (swl25w_h2d_cur(w, 2, &cur) || cur >= SWL25W_FLOW_DEPTH || slot != cur % SWL25W_TX_N) {
				SWL25W_NO("L5 tx payload: not the tx buffer the next flow commit names");
			}
			return SWL25W_L5;
		}
		if (off < SWL25W_FLOW_DEPTH * SWL25W_FLOW_ISZ) {                                            /* L4 (sprint 2026-11-08: all 64 flow items, the ring wraps) */
			slot = off / SWL25W_FLOW_ISZ; k = off % SWL25W_FLOW_ISZ / 4u;
			if (!swl25w_flow_up(w)) {
				SWL25W_NO("L4 tx post: flow ring not open");
			}
			if (swl25w_h2d_cur(w, 2, &cur) || cur != slot || slot >= SWL25W_FLOW_DEPTH) {
				SWL25W_NO("L4 tx post: item is not at the flow ring's write slot (< 64)");
			}
			r = swl25w_tx_word(w, slot, k, v);
			if (r != 0) {
				SWL25W_NO(r);
			}
			return SWL25W_L4;
		}
		SWL25W_NO("DATA write outside the flow items and tx buffers (rx buffers and the rest are dongle-written)");
	}
	if (space != SWL25W_SP_HOST) {
		SWL25W_NO("L25 store: unknown space");
	}
	/* ---- G1 host words */
	if (off >= w->ioctbuf && off < w->ioctbuf + SWL25W_IOCT_WORDS * 4u) {                               /* L6 [src docs/research/T-3e93 s.1]: frozen while the dongle may read it; the commit judges the bytes */
		if (w->ioct_busy) {
			SWL25W_NO("L6 ioctl payload: an ioctl is in transit");
		}
		return SWL25W_L6;
	}
	if (off >= w->ring[0] && off < w->ring[0] + w->depth[0] * 40u) {                                    /* L1 / L2: ring-0 item words */
		slot = (off - w->ring[0]) / 40u; k = (off - w->ring[0]) % 40u / 4u;
		if (swl25w_h2d_cur(w, 0, &cur) || cur != slot || slot >= w->depth[0]) {
			SWL25W_NO("ring-0 item: not at the write slot");
		}
		if (w->wrap0 == 0u ? (old != 0u || swl25w_rdw(w, SWL25W_SP_HOST, off, &now) || now != 0u) : (swl25w_rdw(w, SWL25W_SP_HOST, off, &now) || now != old)) {
			SWL25W_NO("ring-0 item: the word was already written on the first lap, differs from memory, or is unreadable");
		}
		if (w->wrap0 != 0u && (swl25w_rdw(w, SWL25W_SP_HOST, w->h2d_r, &now) || now == 0xFFFFFFFFu || (now & 0xFFFFu) >= w->depth[0] || (slot + 1u) % w->depth[0] == (now & 0xFFFFu))) {
			SWL25W_NO("ring-0 item: after a lap the dongle's ring-0 read index says the ring is full (or is unreadable)");
		}
		r = swl25w_ring0_word(w, slot, k, v, &cls);
		if (r != 0) {
			SWL25W_NO(r);
		}
		return cls;
	}
	if (off >= w->ring[1] && off < w->ring[1] + w->depth[1] * 32u) {                                     /* L3 (sprint 2026-11-08: every ring-1 slot; the ring wraps, the en0 data path re-posts for ever) */
		slot = (off - w->ring[1]) / 32u; k = (off - w->ring[1]) % 32u / 4u;
		if (swl25w_h2d_cur(w, 1, &cur) || slot < cur || slot - cur >= SWL25W_RXB_MAX || slot >= w->depth[1]) {
			SWL25W_NO("L3 rx post: item is not at the ring-1 write slot or within the batch window (SWL25W_RXB_MAX slots) after it");      /* patch 0357: a batch writes its items at cur, cur + 1, ... before the one index store; one item = slot == cur as before */
		}
		if (w->wrap1 != 0u && (swl25w_rdw(w, SWL25W_SP_HOST, w->h2d_r + 4u, &now) || now == 0xFFFFFFFFu || (now & 0xFFFFu) >= w->depth[1] ||
		    slot - cur >= ((now & 0xFFFFu) + w->depth[1] - cur - 1u) % w->depth[1])) {      /* review 0357 F1: the free slots from the write index are (rd - cur - 1) mod depth: a slot of the batch window may only land inside them (slot == cur: the old test, refuse when rd == cur + 1) */
			SWL25W_NO("L3 rx post: after a lap the dongle's ring-1 read index says the ring is full (or is unreadable)");
		}
		r = swl25w_rx_word(w, slot, k, v);
		if (r != 0) {
			SWL25W_NO(r);
		}
		return SWL25W_L3;
	}
	if (off >= w->h2d_w && off < w->h2d_w + 4u * (w->flowid + 1u)) {                                    /* L7 H2D write index */
		i = (off - w->h2d_w) / 4u;
		ring = i == 0u ? 0 : i == 1u ? 1 : i == w->flowid ? 2 : -1;
		if (ring < 0) {
			SWL25W_NO("L7 write index: not ring 0, ring 1 or the flow ring's slot");
		}
		if (swl25w_rdw(w, SWL25W_SP_HOST, off, &now) || now != old || (v >> 16) != (old >> 16)) {
			SWL25W_NO("L7 write index: old word differs from memory or the upper half moved");
		}
		wp = old & 0xFFFFu;
		if (ring == 2 && !w->flow_open && (v & 0xFFFFu) == 0u && wp == 0u) {
			return SWL25W_L7;               /* the flow ring's own index := 0 before the create (dhd_msgbuf.c:12523-12527) */
		}
		if (ring == 0 ? ((v & 0xFFFFu) >= w->depth[0] || wp >= w->depth[0]) : ring == 2 ? ((v & 0xFFFFu) >= SWL25W_FLOW_DEPTH || wp >= SWL25W_FLOW_DEPTH) : ((v & 0xFFFFu) >= w->depth[1] || wp >= w->depth[1])) {
			SWL25W_NO("L7 write index: beyond the ring (ring 0/1: depth, flow: 64)");      /* sprint 2026-11-08: checked before old + 1, so an old index past a wrapping ring is named as such */
		}
		if (ring != 1 && (v & 0xFFFFu) != (ring == 0 ? (wp + 1u) % w->depth[0] : (wp + 1u) % SWL25W_FLOW_DEPTH)) {
			SWL25W_NO("L7 write index: new is not old + 1 (ring 0: modulo its depth)");
		}
		if (ring == 1) {
			/* patch 0357: ring 1 moves by a batch: 1..SWL25W_RXB_MAX items, all written at wp .. end - 1 with end = the new index, or the ring end when the new index is 0; never across the ring end */
			const uint32_t end = (v & 0xFFFFu) == 0u ? w->depth[1] : (v & 0xFFFFu);

			if (end <= wp || end - wp > SWL25W_RXB_MAX) {
				SWL25W_NO("L7 write index (ring 1): new index is not 1..SWL25W_RXB_MAX items ahead of the old one within the ring");
			}
			for (i = wp; i < end; i++) {
				r = swl25w_item_ok(w, 1, i);       /* every item the new index publishes passes its word rules, read back */
				if (r != 0) {
					SWL25W_NO(r);
				}
			}
			return SWL25W_L7;
		}
		if (ring == 2 && !swl25w_flow_up(w)) {
			SWL25W_NO("L7 write index: flow ring not open");
		}
		r = swl25w_item_ok(w, ring, wp);
		if (r != 0) {
			SWL25W_NO(r);
		}
		return SWL25W_L7;
	}
	if (off >= w->d2h_r && off < w->d2h_r + 12u) {                                                      /* L8 D2H read index */
		i = (off - w->d2h_r) / 4u;
		if (swl25w_rdw(w, SWL25W_SP_HOST, off, &now) || now != old || (v >> 16) != (old >> 16)) {
			SWL25W_NO("L8 read index: old word differs from memory or the upper half moved");
		}
		if (swl25w_rdw(w, SWL25W_SP_HOST, w->d2h_w + 4u * i, &wp)) {
			SWL25W_NO("L8 read index: write index unreadable");
		}
		{
			const uint32_t dep = w->depth[i + 2u], ol = old & 0xFFFFu, nw = v & 0xFFFFu, wl = wp & 0xFFFFu;
			uint32_t step, pend;

			if (ol >= dep || nw >= dep || wl >= dep) {
				SWL25W_NO("L8 read index: an index is at or above the depth");
			}
			step = (nw + dep - ol) % dep;
			pend = (wl + dep - ol) % dep;
			if (step == 0u || step > pend) {
				SWL25W_NO("L8 read index: not within the items the dongle wrote");
			}
			/* ring 2 is consumed in batches (swg_d4d_consume, sweet_wlan_g2.h:1723; T-5173 r1 F2); Part A (sprint-wifi 2026-11-09): rings 3 and 4 too, after the poll processed them (DHD updates
			 * the read index once per processed batch). The property is the same for every ring: only items the dongle wrote (step <= pend, above), at most 16 at once */
			if (step > SWL25W_D2H_BATCH) {
				SWL25W_NO("L8 read index: a batch above 16");
			}
		}
		return SWL25W_L8;
	}
	SWL25W_NO("host word is in no L25 class");
}

/* Part A (sprint-wifi 2026-11-09): judge nw consecutive words [off, off + 4 nw) that the DP15 executor stores in ONE batch (one item of ring 1 or of the flow ring, or the payload of one tx buffer),
 * in the order of the per-word plan. Same verdict as nw calls of swl25w_ok, one per word in that order with each earlier word already stored:
 *  - the first word gets the full swl25w_ok (every common and class check, the ring-state reads: write slot, flow open, ring-1 room after a lap);
 *  - every word must lie in the same class region and slot as the first (else refused: the per-word plan never crosses a slot either);
 *  - the per-word value rules run for EVERY word (L3 swl25w_rx_word2 with word 1 of the batch, which the per-word path reads back from memory after its own store; L4 swl25w_tx_word; L5 has none);
 *  - the state the first word read cannot change inside the batch: the write slots and pend_db move only with index stores (never in a batch), flow_open/wrap1 only in swl25w_done of L7/L8; the
 *    dongle-written ring-1 read index only advances (more room), so the first word's room check holds for the rest.
 * Only L3, L4 and L5 are batchable (swl25w_done keeps no state for them); anything else is refused here and goes through the per-word path. Returns the class or -1 (and *why: the first failing word's reason). */
static inline int
swl25w_ok_blk(const struct swl25w *w, int space, uint32_t off, const uint32_t *v, const uint32_t *old, uint32_t nw, const char **why)
{
	uint32_t k, slot, s2, kw, rid1 = 0u;
	int cls, have_rid = 0;
	const char *r;

	if (why != 0) {
		*why = "";
	}
	if (w == 0 || nw == 0u || nw > 1024u || (off & 3u) != 0u) {
		SWL25W_NO("L25 batch: no whitelist, empty or oversized batch, or unaligned offset");
	}
	cls = swl25w_ok(w, space, off, v[0], old[0], why);
	if (cls < 0) {
		return -1;
	}
	if (nw == 1u) {
		return cls;
	}
	if (cls == SWL25W_L5) {
		slot = (off - SWL25W_TX_OFF) / SWL25W_BUF_SZ;
		s2 = (off + 4u * (nw - 1u) - SWL25W_TX_OFF) / SWL25W_BUF_SZ;
		if (off + 4u * (nw - 1u) >= SWL25W_TX_OFF + SWL25W_TX_N * SWL25W_BUF_SZ || s2 != slot) {
			SWL25W_NO("L5 batch: the words leave the tx buffer the first word is in");
		}
		return cls;                     /* L5 has no per-word value rule */
	}
	if (cls == SWL25W_L4) {
		slot = off / SWL25W_FLOW_ISZ;
		for (k = 1u; k < nw; k++) {
			if ((off + 4u * k) / SWL25W_FLOW_ISZ != slot || off + 4u * k >= SWL25W_FLOW_DEPTH * SWL25W_FLOW_ISZ) {
				SWL25W_NO("L4 batch: the words leave the flow item the first word is in");
			}
			r = swl25w_tx_word(w, slot, (off + 4u * k) % SWL25W_FLOW_ISZ / 4u, v[k]);
			if (r != 0) {
				SWL25W_NO(r);
			}
		}
		return cls;
	}
	if (cls == SWL25W_L3) {
		slot = (off - w->ring[1]) / 32u;
		for (k = 0u; k < nw; k++) {
			if (off + 4u * k >= w->ring[1] + w->depth[1] * 32u || (off + 4u * k - w->ring[1]) / 32u != slot) {
				SWL25W_NO("L3 batch: the words leave the ring-1 item the first word is in");
			}
			kw = (off + 4u * k - w->ring[1]) % 32u / 4u;
			if (kw == 1u) {
				have_rid = 1;
				rid1 = v[k];
			}
			if (k == 0u) {
				continue;               /* judged in full above (its word-1 read, if any, is the per-word path's own) */
			}
			r = swl25w_rx_word2(w, slot, kw, v[k], have_rid, rid1);
			if (r != 0) {
				SWL25W_NO(r);
			}
		}
		return cls;
	}
	SWL25W_NO("L25 batch: only rx posts (L3), tx posts (L4) and tx payload (L5) are batched");
}

/* Call right after the store that swl25w_ok accepted was MADE (not before; a failed store stops the engine, so a stale state is never used), with the same space, off, v, old. Records the doorbell an
 * index store asks for (L7: 0xFF, L8: 0xDD, ring id and the new index) and clears it at the doorbell; the flow ring's index := 0 before the create asks for none. Also tracks the two facts only the committed
 * stream knows: a type-3 commit sets flow_created, a type-9 commit sets ioct_busy, a ring-2 consume across a type 0x0C completion of THAT request's transaction id clears it (real order: consume, return, scrub or next call, sweet_wlan_g2.h
 * swg_e_call). A failed read leaves the stricter state: both set at a commit, busy still set at a consume. */
static inline void
swl25w_done(struct swl25w *w, int space, uint32_t off, uint32_t v, uint32_t old, int cls)
{
	uint32_t i, t;

	(void)space;
	if (cls == SWL25W_L9) {
		w->pend_db = 0u;
	} else if (cls == SWL25W_L7) {
		i = (off - w->h2d_w) / 4u;
		w->pend_db = (v & 0xFFFFu) == 0u && i == w->flowid && !w->flow_open ? 0u : 0xFF000000u | (i == 0u ? 0u : i == 1u ? 1u : swl25w_ring_id(w)) << 16 | (v & 0xFFFFu);
		if (i == 0u && (v & 0xFFFFu) == 0u && (old & 0xFFFFu) == w->depth[0] - 1u) {
			w->wrap0++;     /* sprint 2026-11-08: ring 0 completed a lap */
		}
		if (i == 1u && (v & 0xFFFFu) == 0u && (old & 0xFFFFu) != 0u) {
			w->wrap1++;     /* sprint 2026-11-08: ring 1 completed a lap (patch 0357: a batch may end at the ring end, so the old index is any non-zero one: swl25w_ok accepted only forward steps inside the ring) */
		}
		if (i == 0u) {
			if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + (old & 0xFFFFu) * 40u, &t)) {
				w->flow_created = 1u;
				w->ioct_busy = 1u;
				w->ioct_trans = ~0u;
			} else if ((t & 0xFFu) == SWL25W_MSG_FLOW_CREATE) {
				w->flow_created = 1u;
			} else if ((t & 0xFFu) == SWL25W_MSG_IOCTL_REQ) {
				w->ioct_busy = 1u;      /* trans id: request word 3 low [src swd4b_item_ioctl] */
				w->ioct_trans = swl25w_rdw(w, SWL25W_SP_HOST, w->ring[0] + (old & 0xFFFFu) * 40u + 12u, &t) ? ~0u : (t & 0xFFFFu);
			}
		}
	} else if (cls == SWL25W_L8) {
		i = (off - w->d2h_r) / 4u;
		w->pend_db = 0xDD000000u | (2u + i) << 16 | (v & 0xFFFFu);
		if (i == 0u && w->depth[2] != 0u) {
			uint32_t s;

			for (s = old & 0xFFFFu; s != (v & 0xFFFFu) && s < w->depth[2]; s = (s + 1u) % w->depth[2]) {
				if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[2] + s * 24u, &t)) {
					break;
				}
				if ((t & 0xFFu) == SWL25W_MSG_IOCTL_CMPLT) {    /* trans id: completion +14 [src sweet_wlan_d4b.h:120] */
					if (swl25w_rdw(w, SWL25W_SP_HOST, w->ring[2] + s * 24u + 12u, &t)) {
						break;
					}
					if ((t >> 16) == w->ioct_trans) {
						w->ioct_busy = 0u;
					}
				}
			}
		}
	}
}

#endif /* SWEET_WLAN_L25_WL_H */
