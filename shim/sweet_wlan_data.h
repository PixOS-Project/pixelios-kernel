/* sweet_wlan_data.h - Wi-Fi data path for the BCM4389 msgbuf protocol (P-W2 / W-D5, SPEC-WIFI Part E rows DP1-DP15).
 *
 * PURPOSE: everything the shim needs to move Ethernet frames after the join, as PURE functions on byte buffers: the flow-ring create
 * request, the RX-buffer post, the TX post, the D2H completion parsers (flow-ring create response, TX status, RX complete) with the
 * DHD epoch/XOR audit, the ring cursors, the flow-ring and packet-id books, and a small ARP/ICMP engine (answers ARP requests and
 * echo requests for our address, resolves the gateway, sends echo requests, measures the RTT). Header-only C99, no malloc, no I/O,
 * no MMIO: the includer (the msgbuf engine, T-71e2 transport half) owns every store, flush and doorbell. Every constant is cited
 * to a source; what no source settles is a printed `WIFI GAP:` (rule 8), never a silent default.
 *
 * The includer defines, before including:
 *     SWDP_LOG(str)  - one log line (const char *, no newline)
 *     SWDP_GAP(str)  - one "WIFI GAP: ..." line (rule 8)
 * SECRETS / PRIVACY (rule 4): no function here logs a MAC or an IP address; log lines carry counters, ids and RTT only.
 *
 * Sources (R5 = ~/pixelios-artifacts/refs/wlan-stage5/, DHD of the Google 4389 tree; BW = OpenBSD bwfm, ~/pixelios-artifacts/bwfm-host/):
 *  - cmn_msg_hdr R5 bcmmsgbuf.h:181-192 (type, if_id, flags, epoch, request_id u32 LE); H2D flags byte = ring current_phase = 0 and
 *    H2D epoch = seqnum % 253 with seqnum starting at 254 [R5 dhd_msgbuf.c:1819-1823, 5900, 12485; bcmmsgbuf.h:35-39].
 *  - tx_flowring_create_request (40 B, rides the ctrl submit ring) R5 bcmmsgbuf.h:524-540; DHD fills it at dhd_msgbuf.c:12437-12520
 *    (tid, flow_ring_id = flowid, sa, da, ring PA, max_items, len_item = H2DRING_TXPOST_ITEMSIZE, if_flags 0, int_vector/tc unset).
 *  - host_rxbuf_post (32 B) R5 bcmmsgbuf.h:993-1006, filled at dhd_msgbuf.c:5898-5925; BW if_bwfm_pci.h:254-261 agrees.
 *  - host_txbuf_post v1 (48 B) / v2 (56 B, flags2 PCIE_SHARED2_TXPOST_EXT 0x400000) R5 bcmmsgbuf.h:1062-1146, bcmpcie.h:154, filled at
 *    dhd_msgbuf.c:8590-8730: eth header copied to txhdr, data pointer AFTER the 14 B header, data_len = frame - 14, seg_cnt 1,
 *    flags = 802_3 (0x01) | prio << 5, ext_flags 0; the epoch byte is never written by the TX path (stays 0) [R5 dhd_msgbuf.c grep].
 *  - D2H items: epoch byte == ring seqnum % 253 (seqnum starts at 254) AND the XOR of all u32 words of the item == 0
 *    [R5 dhd_msgbuf.c:1485-1535 dhd_prot_d2h_sync_xorcsum; bcmmsgbuf.h:35-39]. ring_create_response / tx status / rx complete layouts
 *    R5 bcmmsgbuf.h:846-852, 1023-1054, 1204-1236 (24, 24, 40 B).
 *  - Ring ids and index slots: flowid starts at 2 (DHD_FLOWRING_START_FLOWID), ringid = 5 + (flowid - 2); the H2D WR/RD index
 *    slot of a flow ring is its flowid; D2H slots are ringid - 2 [R5 dhd_msgbuf.c:306-343]. Doorbell word write 0xFF000000 |
 *    ringid << 16 | wr, read 0xDD000000 | ringid << 16 | rd [R5 dhd_msgbuf.c:114-116].
 *  - Ethernet II / ARP RFC 826 / IPv4 RFC 791 / ICMP RFC 792 / Internet checksum RFC 1071.
 */
#ifndef SWEET_WLAN_DATA_H
#define SWEET_WLAN_DATA_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if !defined(SWDP_LOG) || !defined(SWDP_GAP)
#error "sweet_wlan_data.h: define SWDP_LOG(str) and SWDP_GAP(str) before including (rule 8)"
#endif

/* ------------------------------------------------------------------ GAP lines (rule 8): one per open point */
#define SWDP_GAP_DP4   "WIFI GAP: DP4 txpost epoch byte left 0 (DHD never writes it on the TX path); whether the dongle checks it is unobserved"
#define SWDP_GAP_DP1 "WIFI GAP: DP1 flow create: tc, priority_ifrmmask and int_vector are 0 (DHD leaves them unset); effect on the dongle is unobserved"
#define SWDP_GAP_DP2       "WIFI GAP: DP2 txpost item size taken from the caller (48 base / 56 ext); the shared flags2 TXPOST_EXT bit decides it and is read at run time"
#define SWDP_GAP_DP9      "WIFI GAP: DP9 data path is poll-only: no interrupts, completions are found by reading the D2H write index"
#define SWDP_GAP_DP10    "WIFI GAP: DP10 one flow ring only (AC_BE tid 0 to the gateway): no per-peer or per-priority rings, no flow flush/delete"
#define SWDP_GAP_DP11 "WIFI GAP: DP11 flow ring delete/flush is not sent: the ring lives until the dongle is reset"
#define SWDP_GAP_DP12     "WIFI GAP: DP12 rx checksum offload, rx timestamps and metadata buffers are ignored (metadata_len 0); the shared rx_dataoffset (+0x24) is unread (R108 U3), the caller passes its value"
#define SWDP_GAP_X_ARPOWN    "WIFI GAP: DP13 this ARP/ICMP engine is the shim's own (P-W2 ping); under XNU ifnet (P-W3) the stack answers instead"
#define SWDP_GAP_DP15 "WIFI GAP: DP15 transport: swg_dp_exec (sweet_wlan_g2.h) executes the audited plans on the msgbuf engine bus and is host-tested; the glue does not call it yet (no SWG_SP_DATA bus space, no shadow arrays, no hardware run)"

/* rule 8: prints every defined GAP line that no other function prints on its own (DP1, DP10, DP11 come from swdp_flow_begin, DP13 from
 * swdp_net_init). The includer calls it once at data-path bring-up. */
static inline void swdp_gaps_announce(void)
{
	SWDP_GAP(SWDP_GAP_DP2); SWDP_GAP(SWDP_GAP_DP4); SWDP_GAP(SWDP_GAP_DP9); SWDP_GAP(SWDP_GAP_DP12); SWDP_GAP(SWDP_GAP_DP15);
}

/* ------------------------------------------------------------------ constants */
#define SWDP_MT_FLOW_CREATE       0x03u
#define SWDP_MT_FLOW_CREATE_CMPLT 0x04u
#define SWDP_MT_TX_POST           0x0Fu
#define SWDP_MT_TX_STATUS         0x10u
#define SWDP_MT_RXBUF_POST        0x11u
#define SWDP_MT_RX_CMPLT          0x12u

#define SWDP_ISZ_CTRL_SUB   40u   /* H2DRING_CTRL_SUB_ITEMSIZE, bcmmsgbuf.h:60 */
#define SWDP_ISZ_RXPOST     32u   /* H2DRING_RXPOST_ITEMSIZE, bcmmsgbuf.h:59 */
#define SWDP_ISZ_TX_BASE    48u   /* H2DRING_TXPOST_BASE_ITEMSIZE, bcmmsgbuf.h:42 */
#define SWDP_ISZ_TX_EXT     56u   /* H2DRING_TXPOST_EXT_ITEMSIZE, bcmmsgbuf.h:53 */
#define SWDP_ISZ_CTRL_CMPLT 24u   /* D2HRING_CTRL_CMPLT_ITEMSIZE, bcmmsgbuf.h:70 */
#define SWDP_ISZ_TX_CMPLT   24u   /* D2HRING_TXCMPLT_ITEMSIZE, bcmmsgbuf.h:62 */
#define SWDP_ISZ_RX_CMPLT   40u   /* D2HRING_RXCMPLT_ITEMSIZE, bcmmsgbuf.h:63 */

#define SWDP_EPOCH_MOD      253u  /* H2D_EPOCH_MODULO / D2H_EPOCH_MODULO, bcmmsgbuf.h:35-39 */
#define SWDP_EPOCH_INIT     254u  /* H2D_EPOCH_INIT_VAL / D2H_EPOCH_INIT_VAL: first epoch on the wire is 254 % 253 = 1 */

#define SWDP_FLOWID_FIRST   2u    /* DHD_FLOWRING_START_FLOWID = BCMPCIE_H2D_COMMON_MSGRINGS, dhd_msgbuf.c:306 */
#define SWDP_RINGID_FLOW0   5u    /* BCMPCIE_COMMON_MSGRINGS, bcmpcie.h:188 */
#define SWDP_RING_RXPOST    1u    /* H2D common ring 1 (ring 0 = control submit) */
#define SWDP_RING_TXCPL     3u    /* D2H common rings: 2 control, 3 tx complete, 4 rx complete */
#define SWDP_RING_RXCPL     4u

#define SWDP_PKT_802_3      0x01u /* BCMPCIE_PKT_FLAGS_FRAME_802_3, bcmmsgbuf.h:1151 */
#define SWDP_PKT_802_11     0x02u
#define SWDP_PRIO_SHIFT     5u    /* BCMPCIE_PKT_FLAGS_PRIO_SHIFT, used at dhd_msgbuf.c:8725 */

#define SWDP_ETH_HLEN       14u
#define SWDP_ETH_MAXFRAME   1514u
#define SWDP_ETHT_IP        0x0800u
#define SWDP_ETHT_ARP       0x0806u
#define SWDP_ETHT_EAPOL     0x888eu

static inline void swdp_p16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void swdp_p32(uint8_t *p, uint32_t v) { swdp_p16(p, v); swdp_p16(p + 2, v >> 16); }
static inline uint32_t swdp_g16(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }
static inline uint32_t swdp_g32(const uint8_t *p) { return swdp_g16(p) | (swdp_g16(p + 2) << 16); }
static inline void swdp_b16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static inline uint32_t swdp_n16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | p[1]; }

/* ------------------------------------------------------------------ ring numbers, index slots, doorbells */
static inline uint32_t swdp_ringid(uint32_t flowid) { return SWDP_RINGID_FLOW0 + (flowid - SWDP_FLOWID_FIRST); }
/* H2D WR/RD index slot of a ring: the ring id for the two common rings, the flowid for a flow ring [dhd_msgbuf.c:325-326] */
static inline uint32_t swdp_h2d_slot(uint32_t ringid) { return ringid >= SWDP_RINGID_FLOW0 ? ringid - SWDP_RINGID_FLOW0 + SWDP_FLOWID_FIRST : ringid; }
/* D2H RD/WR index slot of a common D2H ring (ringid 2..4 -> 0..2) [dhd_msgbuf.c:340-343] */
static inline uint32_t swdp_d2h_slot(uint32_t ringid) { return ringid - 2u; }
static inline uint32_t swdp_db_wr(uint32_t ringid, uint32_t wr) { return 0xFF000000u | (ringid << 16) | (wr & 0xFFFFu); }
static inline uint32_t swdp_db_rd(uint32_t ringid, uint32_t rd) { return 0xDD000000u | (ringid << 16) | (rd & 0xFFFFu); }

/* ------------------------------------------------------------------ ring cursor (host side of one ring) */
struct swdp_ring {
	uint32_t depth;     /* items */
	uint32_t wr, rd;    /* item indices, < depth */
	uint32_t seq;       /* epoch counter: starts at SWDP_EPOCH_INIT, +1 per item posted (H2D) or consumed (D2H) */
};

static inline void swdp_ring_init(struct swdp_ring *r, uint32_t depth)
{
	r->depth = depth; r->wr = 0; r->rd = 0; r->seq = SWDP_EPOCH_INIT;
}
/* free slots on an H2D ring: one slot always stays empty so wr == rd means empty (bcmpcie.h:559 style) */
static inline uint32_t swdp_ring_free(const struct swdp_ring *r)
{
	return r->depth ? (r->rd + r->depth - r->wr - 1u) % r->depth : 0u;
}
/* H2D: take the next slot. 0 ok (*slot, *epoch set; wr and seq advanced), -1 ring full or bad depth. The caller fills the item,
 * flushes it, stores the new wr in the H2D-WR slot, flushes that, and rings swdp_db_wr(): none of that is here. */
static inline int swdp_ring_post(struct swdp_ring *r, uint32_t *slot, uint8_t *epoch)
{
	if (swdp_ring_free(r) == 0u)
		return -1;
	*slot = r->wr;
	*epoch = (uint8_t)(r->seq % SWDP_EPOCH_MOD);
	r->seq++;
	r->wr = (r->wr + 1u) % r->depth;
	return 0;
}
/* D2H: items the dongle has produced, from its write index (low 16 bits of the slot). Returns -1 when the write index is outside the
 * ring (a corrupt slot is never walked). */
static inline int swdp_d2h_pending(const struct swdp_ring *r, uint32_t wr_slot)
{
	uint32_t w = wr_slot & 0xFFFFu;
	if (r->depth == 0u || w >= r->depth)
		return -1;
	return (int)((w + r->depth - r->rd) % r->depth);
}
/* D2H: the read cursor passes one item (epoch counter + read index advance); -1 for a depth-0 ring, nothing changed. The caller stores rd in the
 * D2H-RD slot and rings swdp_db_rd(). */
static inline int swdp_d2h_consume(struct swdp_ring *r)
{
	if (r->depth == 0u)
		return -1;
	r->seq++;
	r->rd = (r->rd + 1u) % r->depth;
	return 0;
}
/* H2D: the read index the dongle DMA-writes into host memory (low 16 bits of the slot). 0 and r->rd set, or -1 when it is outside the
 * ring (r->rd is then untouched: swdp_ring_free trusts it, so nothing may reach it unchecked). */
static inline int swdp_h2d_rd_set(struct swdp_ring *r, uint32_t rd_slot)
{
	uint32_t v = rd_slot & 0xFFFFu;
	if (r->depth == 0u || v >= r->depth)
		return -1;
	r->rd = v;
	return 0;
}

/* ------------------------------------------------------------------ H2D item builders (explicit little-endian stores, no struct padding) */
static inline void swdp_hdr(uint8_t *it, uint32_t type, uint32_t ifid, uint32_t epoch, uint32_t rid)
{
	it[0] = (uint8_t)type; it[1] = (uint8_t)ifid; it[2] = 0u /* flags: H2D phase 0 */; it[3] = (uint8_t)epoch; swdp_p32(it + 4, rid);
}

/* tx_flowring_create_request, 40 B: hdr 0-7, da 8, sa 14, tid 20, if_flags 21, flow_ring_id 22, tc 24, priority_ifrmmask 25,
 * int_vector 26, max_items 28, len_item 30, flow_ring_ptr lo 32 hi 36 [R5 bcmmsgbuf.h:524-540]. */
static inline int swdp_build_flow_create(uint8_t *it, uint32_t epoch, uint32_t ifid, uint32_t flowid, uint32_t tid, const uint8_t da[6],
    const uint8_t sa[6], uint64_t ring_pa, uint32_t max_items, uint32_t len_item)
{
	if (flowid < SWDP_FLOWID_FIRST || max_items == 0u || max_items > 0xFFFFu || (len_item != SWDP_ISZ_TX_BASE && len_item != SWDP_ISZ_TX_EXT))
		return -1;
	memset(it, 0, SWDP_ISZ_CTRL_SUB);
	swdp_hdr(it, SWDP_MT_FLOW_CREATE, ifid, epoch, 0u /* request_id: DHD sends 0, dhd_msgbuf.c:12482 */);
	memcpy(it + 8, da, 6); memcpy(it + 14, sa, 6);
	it[20] = (uint8_t)tid; it[21] = 0u; swdp_p16(it + 22, flowid);
	swdp_p16(it + 28, max_items); swdp_p16(it + 30, len_item);
	swdp_p32(it + 32, (uint32_t)ring_pa); swdp_p32(it + 36, (uint32_t)(ring_pa >> 32));
	return 0;
}

/* host_rxbuf_post, 32 B: hdr 0-7, metadata_buf_len 8, data_buf_len 10, rsvd 12, metadata addr 16, data addr 24 [R5 bcmmsgbuf.h:993-1006].
 * No metadata buffer: lens and address 0 (DHD with rx_metadata_offset 0, dhd_msgbuf.c:5908-5917). */
static inline int swdp_build_rxpost(uint8_t *it, uint32_t epoch, uint32_t rid, uint64_t buf_pa, uint32_t buf_len)
{
	if (buf_len == 0u || buf_len > 0xFFFFu)
		return -1;
	memset(it, 0, SWDP_ISZ_RXPOST);
	swdp_hdr(it, SWDP_MT_RXBUF_POST, 0u, epoch, rid);
	swdp_p16(it + 10, buf_len);
	swdp_p32(it + 24, (uint32_t)buf_pa); swdp_p32(it + 28, (uint32_t)(buf_pa >> 32));
	return 0;
}

/* host_txbuf_post, isz 48 (v1) or 56 (v2): hdr 0-7 (epoch 0, see SWDP_GAP_DP4), txhdr 8 (14 B), flags 22, seg_cnt 23, metadata addr 24,
 * data addr 32, metadata_buf_len 40, data_len 42, v1: marker/ext 44-47; v2: ext_flags 44, pktinfo 48, PAD 52 [R5 bcmmsgbuf.h:1062-1160].
 * `frame` is the whole Ethernet frame (14 B header + payload); the payload is what data_pa points at. */
static inline int swdp_build_txpost(uint8_t *it, uint32_t isz, uint32_t ifid, uint32_t rid, const uint8_t *frame, uint32_t frame_len,
    uint32_t prio, uint64_t payload_pa)
{
	if ((isz != SWDP_ISZ_TX_BASE && isz != SWDP_ISZ_TX_EXT) || frame_len <= SWDP_ETH_HLEN || frame_len > SWDP_ETH_MAXFRAME || prio > 7u)
		return -1;
	memset(it, 0, isz);
	swdp_hdr(it, SWDP_MT_TX_POST, ifid, 0u, rid);
	memcpy(it + 8, frame, SWDP_ETH_HLEN);
	it[22] = (uint8_t)(SWDP_PKT_802_3 | (prio << SWDP_PRIO_SHIFT)); it[23] = 1u;
	swdp_p32(it + 32, (uint32_t)payload_pa); swdp_p32(it + 36, (uint32_t)(payload_pa >> 32));
	swdp_p16(it + 42, frame_len - SWDP_ETH_HLEN);
	return 0;
}

/* ------------------------------------------------------------------ D2H audit and parsers */
#define SWDP_E_EPOCH  1
#define SWDP_E_XOR    2
#define SWDP_E_SIZE   3
#define SWDP_E_TYPE   4
#define SWDP_E_RANGE  5
#define SWDP_E_ID     6
#define SWDP_E_STATUS 7

/* epoch byte == the ring's expected epoch and the XOR of the item's u32 words == 0. 0 ok, SWDP_E_EPOCH, SWDP_E_XOR, SWDP_E_SIZE. */
static inline int swdp_d2h_check(const uint8_t *it, uint32_t isz, uint32_t ring_seq)
{
	uint32_t i, x = 0u;
	if (isz == 0u || (isz & 3u))
		return SWDP_E_SIZE;
	if (it[3] != (uint8_t)(ring_seq % SWDP_EPOCH_MOD))
		return SWDP_E_EPOCH;
	for (i = 0; i < isz; i += 4u)
		x ^= swdp_g32(it + i);
	return x == 0u ? 0 : SWDP_E_XOR;
}

struct swdp_cmpl {                  /* the fields all three D2H item kinds share, plus the per-kind ones */
	uint32_t type, rid, status, ring_id;
	uint32_t data_len, data_offset, rflags;  /* rx complete */
	uint32_t metadata_len, tx_status;        /* tx status: metadata_len +12, tx_status +14 */
};

/* common header of a checked D2H item: type +0, request_id +4, compl status +8 (i16), ring/flow id +10 */
static inline void swdp_cmpl_common(const uint8_t *it, struct swdp_cmpl *c)
{
	memset(c, 0, sizeof *c);
	c->type = it[0]; c->rid = swdp_g32(it + 4); c->status = swdp_g16(it + 8); c->ring_id = swdp_g16(it + 10);
}
/* flow-ring create response (24 B): the common part is all there is (rsvd 12-19, marker 20) [R5 bcmmsgbuf.h:846-852]. */
static inline int swdp_parse_flow_cmpl(const uint8_t *it, struct swdp_cmpl *c)
{
	swdp_cmpl_common(it, c);
	return c->type == SWDP_MT_FLOW_CREATE_CMPLT ? 0 : SWDP_E_TYPE;
}
/* tx status (24 B): metadata_len 12, tx_status 14, timestamp 16-23 [R5 bcmmsgbuf.h:1204-1236]. */
static inline int swdp_parse_txstatus(const uint8_t *it, struct swdp_cmpl *c)
{
	swdp_cmpl_common(it, c);
	c->metadata_len = swdp_g16(it + 12); c->tx_status = swdp_g16(it + 14);
	return c->type == SWDP_MT_TX_STATUS ? 0 : SWDP_E_TYPE;
}
/* rx complete (40 B): metadata_len 12, data_len 14, data_offset 16, flags 18, rx_status_0 20, rx_status_1 24 [R5 bcmmsgbuf.h:1023-1054].
 * buf_len = the length posted for this request_id: the frame [data_offset, data_offset + data_len) must lie inside it. A data_offset of 0 means
 * "the frame starts at the shared rx_dataoffset" (DHD and OpenBSD do this) [R5 dhd_msgbuf.c:7175-7182; BW if_bwfm_pci.c:1779-1782], so
 * rx_dataoffset (shared +0x24, unread on this firmware: SWDP_GAP_DP12) is a parameter and c->data_offset returns the EFFECTIVE offset.
 * A non-zero completion status (int16) is SWDP_E_STATUS. */
static inline int swdp_parse_rxcmpl(const uint8_t *it, uint32_t buf_len, uint32_t rx_dataoffset, struct swdp_cmpl *c)
{
	uint64_t off;
	swdp_cmpl_common(it, c);
	c->metadata_len = swdp_g16(it + 12); c->data_len = swdp_g16(it + 14); c->data_offset = swdp_g16(it + 16); c->rflags = swdp_g16(it + 18);
	if (c->type != SWDP_MT_RX_CMPLT)
		return SWDP_E_TYPE;
	if ((int16_t)c->status != 0)
		return SWDP_E_STATUS;
	off = c->data_offset ? (uint64_t)c->data_offset : (uint64_t)rx_dataoffset;
	if (c->data_len == 0u || off + c->data_len > buf_len)
		return SWDP_E_RANGE;
	c->data_offset = (uint32_t)off;
	return 0;
}

/* ------------------------------------------------------------------ request-id books (rx buffers and tx packets in flight) */
#define SWDP_RX_RID  0x52580000u   /* 'RX' << 16, plus the slot */
#define SWDP_TX_RID  0x54580000u   /* 'TX' << 16, plus the slot */
#define SWDP_NSLOT   16u
/* patch 0357: the rx pool may be larger than the 16 tx buffers. SWDP_RX_MAX = Linux brcmfmac BRCMF_DEF_MAX_RXBUFPOST 255 [LNX v6.6 pcie.c:246] (and the u16 max_rxbufpost it reads from the shared RAM
 * is clamped to it); SWDP_RXB_MAX = the most items one batched rx post carries = BRCMF_MSGBUF_RXBUFPOST_THRESHOLD 32 [LNX msgbuf.c:58] (DHD: a burst of 32 or 64, dhd_msgbuf.c:531,545).
 * A book holds SWDP_RX_MAX slots; `cap` is how many of them are usable (the tx book 16, the rx book the chosen pool; 0 = SWDP_NSLOT, the old behaviour). */
#define SWDP_RX_MAX  255u
#define SWDP_RXB_MAX 32u

struct swdp_book {
	uint32_t base;                 /* SWDP_RX_RID or SWDP_TX_RID */
	uint8_t used[SWDP_RX_MAX];
	uint32_t len[SWDP_RX_MAX];     /* posted length (rx: buffer, tx: payload) */
	uint32_t inflight, unknown;    /* unknown = completions with an id that is not in flight (counted, each a GAP line) */
	uint32_t cap;                  /* usable slots (patch 0357); 0 = SWDP_NSLOT */
};

static inline uint32_t swdp_book_cap(const struct swdp_book *b) { return b->cap != 0u ? b->cap : SWDP_NSLOT; }
static inline void swdp_book_init(struct swdp_book *b, uint32_t base) { memset(b, 0, sizeof *b); b->base = base; b->cap = SWDP_NSLOT; }
/* patch 0357: widen the book to n slots (SWDP_NSLOT <= n <= SWDP_RX_MAX, never below what is in flight); 0 = done, -1 = refused, nothing changed */
static inline int swdp_book_set_cap(struct swdp_book *b, uint32_t n)
{
	if (n < SWDP_NSLOT || n > SWDP_RX_MAX || n < swdp_book_cap(b))
		return -1;
	b->cap = n;
	return 0;
}
/* sprint 2026-11-08: take slot i exactly (the en0 flow slot % 16 rule of the L25 judge): the request_id, 0 when that slot is busy or out of range */
static inline uint32_t swdp_book_take_at(struct swdp_book *b, uint32_t i, uint32_t len)
{
	if (i >= swdp_book_cap(b) || b->used[i]) {
		return 0u;
	}
	b->used[i] = 1u; b->len[i] = len; b->inflight++;
	return b->base + i;
}
/* take a free slot: returns the request_id, 0 when full (0 is never a valid id) */
static inline uint32_t swdp_book_take(struct swdp_book *b, uint32_t len)
{
	uint32_t i;
	for (i = 0; i < swdp_book_cap(b); i++) {
		if (!b->used[i]) {
			b->used[i] = 1u; b->len[i] = len; b->inflight++;
			return b->base + i;
		}
	}
	return 0u;
}
/* a completion for `rid`: 0 and the posted length when it was in flight; SWDP_E_ID for a foreign, out-of-range or already completed id */
static inline int swdp_book_done(struct swdp_book *b, uint32_t rid, uint32_t *len)
{
	uint32_t i = rid - b->base;
	if (rid < b->base || i >= swdp_book_cap(b) || !b->used[i]) {
		b->unknown++;
		SWDP_GAP("WIFI GAP: DP8 completion for a request_id that is not in flight (dropped, counted)");
		return SWDP_E_ID;
	}
	b->used[i] = 0u; b->inflight--;
	if (len)
		*len = b->len[i];
	return 0;
}

/* ------------------------------------------------------------------ patch 0357: the rx pool (how many buffers stay posted) and its batched refill
 * Today's driver posted 16 (SWDP_NSLOT) and reposted one per rx completion with a doorbell each [obs docs/research/156 s.4]. Linux brcmfmac posts max_rxbufpost buffers, the u16 at shared RAM +34
 * (BRCMF_SHARED_MAX_RXBUFPOST_OFFSET [LNX v6.6 pcie.c:223], read by brcmf_pcie_read_tcm16 at sharedram_addr + 34, 0 -> BRCMF_DEF_MAX_RXBUFPOST 255 [pcie.c:246,1644-1647]; the Pixel bcmdhd names the
 * same field max_host_rxbufs, the u16 after total_lfrag_pkt_cnt in pciedev_shared_t [bcmpcie.h:373-374]; DHD uses 256 when it is 0, dhd_msgbuf.c:109-110,4268-4272) and refills only when the
 * posted count fell BRCMF_MSGBUF_RXBUFPOST_THRESHOLD (32) below it, as one batch with one write-index update + doorbell [LNX msgbuf.c:58,987-1006, write_complete after the loop :974-975]. */
enum { SWDP_RXFW_UNREAD = 0, SWDP_RXFW_OK, SWDP_RXFW_FAIL };          /* what the shared-RAM read of max_rxbufpost gave */
enum { SWDP_RXSRC_DEFAULT = 0, SWDP_RXSRC_FW, SWDP_RXSRC_ARG };       /* where the chosen count came from */
#define SWDP_RXN_FWZERO   0x01u   /* firmware value 0: Linux default 255 used (pcie.c:1646-1647) */
#define SWDP_RXN_FWCLAMP  0x02u   /* firmware value above 255: clamped */
#define SWDP_RXN_FWLOW    0x04u   /* firmware value below our 16: 16 kept (today's proven pool) */
#define SWDP_RXN_FWFAIL   0x08u   /* the read failed or read all ones: 16 kept */
#define SWDP_RXN_ARGCLAMP 0x10u   /* the boot-arg was outside 16..255: clamped */
#define SWDP_RXN_RING     0x20u   /* the count was cut to the ring (depth - 1) */
#define SWDP_RXN_BLOCK    0x40u   /* the count was cut to what the DATA block holds */
#define SWDP_RXN_ARGFW    0x80u   /* the boot-arg asked for more than the firmware advertises: cut to the firmware value (review 0357 F3) */
struct swdp_rxsel { uint32_t n, src, notes, fw, ceil; };

/* The pool size. fw_state/fw: the shared-RAM read; has_arg/arg: boot-arg sweetwlanrxpost (present with any value, 0 included); ring_depth: items of the rx post ring; cap: rx buffers the DATA block holds.
 * ceiling = min(255, cap, ring_depth - 1): never more buffers than the block holds or than the ring can carry (a ring keeps one slot empty). arg overrides the firmware value (clamped 16..255);
 * else the firmware value (0 -> 255, > 255 -> 255, < 16 -> 16); a failed read keeps 16. Never below SWDP_NSLOT. */
static inline void swdp_rx_pick(struct swdp_rxsel *s, uint32_t fw_state, uint32_t fw, uint32_t has_arg, uint32_t arg, uint32_t ring_depth, uint32_t cap)
{
	const uint32_t ringlim = ring_depth != 0u ? ring_depth - 1u : 0u;
	uint32_t n, ceil = SWDP_RX_MAX;

	memset(s, 0, sizeof *s);
	s->fw = fw;
	if (cap < ceil) { ceil = cap; }
	if (ringlim < ceil) { ceil = ringlim; }
	s->ceil = ceil;
	if (has_arg) {
		s->src = SWDP_RXSRC_ARG;
		n = arg;
		if (n < SWDP_NSLOT || n > SWDP_RX_MAX) { s->notes |= SWDP_RXN_ARGCLAMP; n = n < SWDP_NSLOT ? SWDP_NSLOT : SWDP_RX_MAX; }
	} else if (fw_state == SWDP_RXFW_OK && fw != 0xFFFFu) {
		s->src = SWDP_RXSRC_FW;
		n = fw;
		if (n == 0u) { s->notes |= SWDP_RXN_FWZERO; n = SWDP_RX_MAX; }
		if (n > SWDP_RX_MAX) { s->notes |= SWDP_RXN_FWCLAMP; n = SWDP_RX_MAX; }
		if (n < SWDP_NSLOT) { s->notes |= SWDP_RXN_FWLOW; n = SWDP_NSLOT; }
	} else {
		s->src = SWDP_RXSRC_DEFAULT;
		s->notes |= SWDP_RXN_FWFAIL;
		n = SWDP_NSLOT;
	}
	if (has_arg && fw_state == SWDP_RXFW_OK && fw != 0u && fw != 0xFFFFu && n > fw) {      /* never post above what the firmware advertises (Linux msgbuf.c:987); a firmware value below 16 keeps 16 */
		s->notes |= SWDP_RXN_ARGFW;
		n = fw < SWDP_NSLOT ? SWDP_NSLOT : fw;
	}
	if (n > ceil) {
		s->notes |= ringlim <= cap ? SWDP_RXN_RING : SWDP_RXN_BLOCK;
		n = ceil;
	}
	if (n < SWDP_NSLOT) { n = SWDP_NSLOT; }
	s->n = n;
}

/* The refill rule. pool = the chosen count, posted = buffers the book holds in flight (posted and not yet taken back), ring_free = free slots of the rx post ring (swdp_ring_free), wr / depth = its write
 * slot and size, all = 1 for the initial fill (any free buffer), 0 for the steady state (Linux rule: post when at least the threshold below the pool). Returns how many items the NEXT batch carries, 0 = none:
 * at most SWDP_RXB_MAX, at most the free buffers, at most the free ring slots, and never past the ring end (the batch's items and its one write index stay in one lap, so the audit and the judge never see a wrap
 * inside a batch). Threshold = min(32, pool / 4): 32 for a pool of 128 and up (BRCMF_MSGBUF_RXBUFPOST_THRESHOLD), a quarter of a smaller pool (64 -> 16), 1 for a pool of 16 (today's immediate refill; the batching
 * is then per poll pass, not per completion). The posted count below `pool - threshold` IS the low mark: the refill runs when it is crossed, in batches of at most 32. */
static inline uint32_t swdp_rx_thr(uint32_t pool, uint32_t all)
{
	uint32_t thr = pool / 4u;

	if (thr > SWDP_RXB_MAX) { thr = SWDP_RXB_MAX; }
	if (thr == 0u || all || pool <= SWDP_NSLOT) { thr = 1u; }       /* a pool of 16 (sweetwlanrxpost=16) refills as soon as a buffer is free, as before this patch */
	return thr;
}
static inline uint32_t swdp_rx_refill_n(uint32_t pool, uint32_t posted, uint32_t ring_free, uint32_t wr, uint32_t depth, uint32_t all)
{
	uint32_t freeb = pool > posted ? pool - posted : 0u, thr = swdp_rx_thr(pool, all), n;

	if (freeb == 0u || freeb < thr || depth == 0u || wr >= depth) {
		return 0u;
	}
	n = freeb < SWDP_RXB_MAX ? freeb : SWDP_RXB_MAX;
	if (n > ring_free) { n = ring_free; }
	if (n > depth - wr) { n = depth - wr; }
	if (pool <= SWDP_NSLOT) { n = 1u; }      /* review 0357 F2: a pool of 16 posts one item per plan = the write shape before this patch, so sweetwlanrxpost=16 is a true A/B */
	return n;
}

/* ------------------------------------------------------------------ flow ring state */
enum swdp_flow_state { SWDP_FLOW_NONE = 0, SWDP_FLOW_CREATING, SWDP_FLOW_OPEN, SWDP_FLOW_FAILED };

struct swdp_flow {
	enum swdp_flow_state st;
	uint32_t flowid, ringid, isz;
	struct swdp_ring ring;         /* the TX ring: depth = max_items */
};

/* flowid must index the H2D index arrays (max_sub entries, 43 in the observed shared structure) and not collide with the common rings */
static inline int swdp_flow_begin(struct swdp_flow *f, uint32_t flowid, uint32_t max_sub, uint32_t depth, uint32_t isz)
{
	if (flowid < SWDP_FLOWID_FIRST || flowid >= max_sub || depth < 2u || (isz != SWDP_ISZ_TX_BASE && isz != SWDP_ISZ_TX_EXT) || f->st != SWDP_FLOW_NONE) {
		SWDP_GAP("WIFI GAP: DP1 flow create refused: bad flowid/depth/item size or a flow ring already exists");
		return -1;
	}
	f->st = SWDP_FLOW_CREATING; f->flowid = flowid; f->ringid = swdp_ringid(flowid); f->isz = isz;
	swdp_ring_init(&f->ring, depth);
	SWDP_GAP(SWDP_GAP_DP1); SWDP_GAP(SWDP_GAP_DP10); SWDP_GAP(SWDP_GAP_DP11);
	return 0;
}
/* the dongle's answer (status 0 = BCMPCIE_SUCCESS, bcmmsgbuf.h:740); 0 = ring open, <0 = refused */
static inline int swdp_flow_on_create_cmpl(struct swdp_flow *f, const struct swdp_cmpl *c)
{
	char b[96];
	if (f->st != SWDP_FLOW_CREATING || c->ring_id != f->flowid) {
		snprintf(b, sizeof b, "WIFI GAP: DP1 unexpected flow create completion (state %d)", (int)f->st);
		SWDP_GAP(b);
		return -1;
	}
	if ((int16_t)c->status != 0) {
		f->st = SWDP_FLOW_FAILED;
		snprintf(b, sizeof b, "WIFI GAP: DP1 flow create failed, status %d", (int)(int16_t)c->status);
		SWDP_GAP(b);
		return -1;
	}
	f->st = SWDP_FLOW_OPEN;
	snprintf(b, sizeof b, "SWEET WLAN: flow ring up flow=%u ring=%u items=%u", (unsigned)f->flowid, (unsigned)f->ringid, (unsigned)f->ring.depth);
	SWDP_LOG(b);
	return 0;
}

/* ------------------------------------------------------------------ Internet checksum (RFC 1071) */
static inline uint16_t swdp_csum(const uint8_t *p, uint32_t n)
{
	uint32_t s = 0u, i;
	for (i = 0; i + 1u < n; i += 2u)
		s += ((uint32_t)p[i] << 8) | p[i + 1u];
	if (n & 1u)
		s += (uint32_t)p[n - 1u] << 8;
	while (s >> 16)
		s = (s & 0xFFFFu) + (s >> 16);
	return (uint16_t)~s;
}

/* ------------------------------------------------------------------ ARP / ICMP engine */
#define SWDP_OUT_MAX    128u           /* the reply buffer size the shim passes as out_cap to swdp_net_rx (a 94 B echo payload fits) */
#define SWDP_PING_MAX   16u
#define SWDP_PING_DATA  32u            /* echo payload bytes (the Linux/OpenBSD ping default is 56; 32 keeps the frame small) */
#define SWDP_PING_ID    0x5058u        /* 'PX' */

enum swdp_rx_kind {
	SWDP_RX_IGNORED = 0,   /* not for us / not understood and harmless */
	SWDP_RX_REPLY,         /* a reply frame was built into `out` (out_len set): ARP reply or echo reply to a request for us */
	SWDP_RX_GW_LEARNED,    /* the gateway's MAC was learned from an ARP reply */
	SWDP_RX_PING_REPLY,    /* an echo reply matched an outstanding request: seq/rtt_us set */
	SWDP_RX_EAPOL,         /* ethertype 0x888e: the caller hands the frame to the join hook (swj_rx) */
	SWDP_RX_BAD            /* malformed frame (a GAP line was printed) */
};

struct swdp_net {
	uint8_t mac[6], ip[4], gw_ip[4], gw_mac[6];
	int gw_known;
	uint16_t ip_id;                          /* IPv4 identification counter */
	uint32_t seq;                            /* last echo sequence sent */
	uint64_t tx_us[SWDP_PING_MAX + 1u];      /* send time per seq (index = seq, 1..SWDP_PING_MAX) */
	uint8_t out_ok[SWDP_PING_MAX + 1u];      /* seq outstanding */
	uint32_t sent, got, bad, ignored, arp_req_in, echo_req_in, rtt_min_us, rtt_max_us;
	uint64_t rtt_sum_us;
};

static inline void swdp_net_init(struct swdp_net *n, const uint8_t mac[6], const uint8_t ip[4], const uint8_t gw_ip[4])
{
	memset(n, 0, sizeof *n);
	memcpy(n->mac, mac, 6); memcpy(n->ip, ip, 4); memcpy(n->gw_ip, gw_ip, 4);
	SWDP_GAP(SWDP_GAP_X_ARPOWN);
}

static inline void swdp_eth(uint8_t *f, const uint8_t dst[6], const uint8_t src[6], uint32_t type)
{
	memcpy(f, dst, 6); memcpy(f + 6, src, 6); swdp_b16(f + 12, type);
}

/* ARP request who-has gw_ip tell ip (RFC 826; Ethernet/IPv4: 42 B). Returns the frame length. */
static inline uint32_t swdp_build_arp_req(const struct swdp_net *n, uint8_t *f)
{
	static const uint8_t bc[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	swdp_eth(f, bc, n->mac, SWDP_ETHT_ARP);
	swdp_b16(f + 14, 1u); swdp_b16(f + 16, SWDP_ETHT_IP); f[18] = 6u; f[19] = 4u; swdp_b16(f + 20, 1u);
	memcpy(f + 22, n->mac, 6); memcpy(f + 28, n->ip, 4); memset(f + 32, 0, 6); memcpy(f + 38, n->gw_ip, 4);
	return 42u;
}

/* ICMP echo request to the gateway: IPv4 20 B (DF, TTL 64) + ICMP 8 B + SWDP_PING_DATA. 0 if the gateway MAC is not known yet. */
static inline uint32_t swdp_build_echo_req(struct swdp_net *n, uint8_t *f, uint64_t now_us)
{
	uint32_t i, tot = 20u + 8u + SWDP_PING_DATA;
	uint8_t *ip = f + SWDP_ETH_HLEN, *ic = ip + 20;
	if (!n->gw_known || n->seq >= SWDP_PING_MAX)
		return 0u;
	n->seq++;
	swdp_eth(f, n->gw_mac, n->mac, SWDP_ETHT_IP);
	ip[0] = 0x45u; ip[1] = 0u; swdp_b16(ip + 2, tot); swdp_b16(ip + 4, ++n->ip_id); swdp_b16(ip + 6, 0x4000u);
	ip[8] = 64u; ip[9] = 1u; ip[10] = ip[11] = 0u; memcpy(ip + 12, n->ip, 4); memcpy(ip + 16, n->gw_ip, 4);
	swdp_b16(ip + 10, swdp_csum(ip, 20u));
	ic[0] = 8u; ic[1] = 0u; ic[2] = ic[3] = 0u; swdp_b16(ic + 4, SWDP_PING_ID); swdp_b16(ic + 6, n->seq);
	for (i = 0; i < SWDP_PING_DATA; i++)
		ic[8u + i] = (uint8_t)i;
	swdp_b16(ic + 2, swdp_csum(ic, 8u + SWDP_PING_DATA));
	n->tx_us[n->seq] = now_us; n->out_ok[n->seq] = 1u; n->sent++;
	return SWDP_ETH_HLEN + tot;
}

static inline void swdp_net_bad(struct swdp_net *n, const char *what)
{
	char b[128];
	n->bad++;
	snprintf(b, sizeof b, "WIFI GAP: DP14 rx frame dropped: %s", what);
	SWDP_GAP(b);
}

/* One received Ethernet frame. `out` (out_cap bytes) receives a reply when SWDP_RX_REPLY is returned; a reply that would not fit in out_cap is
 * not built (the frame is SWDP_RX_BAD). For SWDP_RX_PING_REPLY, *seq and *rtt_us are set. Frames are bounds-checked against `len` before any
 * field is read; checksums are verified. */
static inline enum swdp_rx_kind swdp_net_rx(struct swdp_net *n, const uint8_t *f, uint32_t len, uint64_t now_us, uint8_t *out, uint32_t out_cap,
    uint32_t *out_len, uint32_t *seq, uint32_t *rtt_us)
{
	static const uint8_t bc[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	uint32_t et, ihl, tot, plen;
	const uint8_t *ip, *ic;

	*out_len = 0u;
	if (len < SWDP_ETH_HLEN) {
		swdp_net_bad(n, "shorter than an Ethernet header");
		return SWDP_RX_BAD;
	}
	if (memcmp(f, n->mac, 6) != 0 && memcmp(f, bc, 6) != 0) {
		n->ignored++;
		return SWDP_RX_IGNORED;
	}
	et = swdp_n16(f + 12);
	if (et == SWDP_ETHT_EAPOL)
		return SWDP_RX_EAPOL;
	if (et == SWDP_ETHT_ARP) {
		const uint8_t *a = f + SWDP_ETH_HLEN;
		if (len < 42u || swdp_n16(a) != 1u || swdp_n16(a + 2) != SWDP_ETHT_IP || a[4] != 6u || a[5] != 4u) {
			swdp_net_bad(n, "arp too short or not Ethernet/IPv4");
			return SWDP_RX_BAD;
		}
		if (swdp_n16(a + 6) == 1u && memcmp(a + 24, n->ip, 4) == 0) {      /* who-has our IP */
			if (out_cap < 42u) {
				swdp_net_bad(n, "arp reply does not fit the reply buffer");
				return SWDP_RX_BAD;
			}
			n->arp_req_in++;
			swdp_eth(out, a + 8, n->mac, SWDP_ETHT_ARP);
			memcpy(out + 14, a, 6); swdp_b16(out + 20, 2u);
			memcpy(out + 22, n->mac, 6); memcpy(out + 28, n->ip, 4); memcpy(out + 32, a + 8, 6); memcpy(out + 38, a + 14, 4);
			*out_len = 42u;
			return SWDP_RX_REPLY;
		}
		if (swdp_n16(a + 6) == 2u && memcmp(a + 14, n->gw_ip, 4) == 0 && memcmp(a + 24, n->ip, 4) == 0) {   /* is-at from the gateway */
			memcpy(n->gw_mac, a + 8, 6); n->gw_known = 1;
			SWDP_LOG("SWEET WLAN: arp gateway resolved");
			return SWDP_RX_GW_LEARNED;
		}
		n->ignored++;
		return SWDP_RX_IGNORED;
	}
	if (et != SWDP_ETHT_IP) {
		n->ignored++;
		return SWDP_RX_IGNORED;
	}
	if (len < SWDP_ETH_HLEN + 20u) {
		swdp_net_bad(n, "ipv4 shorter than a header");
		return SWDP_RX_BAD;
	}
	ip = f + SWDP_ETH_HLEN;
	ihl = (uint32_t)(ip[0] & 0x0Fu) * 4u; tot = swdp_n16(ip + 2);
	if ((ip[0] >> 4) != 4u || ihl < 20u || tot < ihl + 8u || SWDP_ETH_HLEN + tot > len) {
		swdp_net_bad(n, "ipv4 header fields inconsistent with the frame length");
		return SWDP_RX_BAD;
	}
	if (swdp_csum(ip, ihl) != 0u) {
		swdp_net_bad(n, "ipv4 header checksum");
		return SWDP_RX_BAD;
	}
	if (ip[9] != 1u || memcmp(ip + 16, n->ip, 4) != 0 || (swdp_n16(ip + 6) & 0x3FFFu) != 0u) {   /* not ICMP to us, or a fragment */
		n->ignored++;
		return SWDP_RX_IGNORED;
	}
	ic = ip + ihl; plen = tot - ihl;
	if (swdp_csum(ic, plen) != 0u) {
		swdp_net_bad(n, "icmp checksum");
		return SWDP_RX_BAD;
	}
	if (ic[0] == 8u && ic[1] == 0u) {                                         /* echo request to us: answer it */
		if (SWDP_ETH_HLEN + 20u + plen > out_cap) {
			swdp_net_bad(n, "echo request too large to answer");
			return SWDP_RX_BAD;
		}
		n->echo_req_in++;
		swdp_eth(out, f + 6, n->mac, SWDP_ETHT_IP);
		out[14] = 0x45u; out[15] = 0u; swdp_b16(out + 16, 20u + plen); swdp_b16(out + 18, ++n->ip_id); swdp_b16(out + 20, 0x4000u);
		out[22] = 64u; out[23] = 1u; out[24] = out[25] = 0u; memcpy(out + 26, n->ip, 4); memcpy(out + 30, ip + 12, 4);
		swdp_b16(out + 24, swdp_csum(out + 14, 20u));
		memcpy(out + 34, ic, plen); out[34] = 0u; out[36] = out[37] = 0u;
		swdp_b16(out + 36, swdp_csum(out + 34, plen));
		*out_len = SWDP_ETH_HLEN + 20u + plen;
		return SWDP_RX_REPLY;
	}
	if (ic[0] == 0u && ic[1] == 0u && swdp_n16(ic + 4) == SWDP_PING_ID) {   /* echo reply to one of ours */
		uint32_t s = swdp_n16(ic + 6), rtt;
		if (s == 0u || s > SWDP_PING_MAX || !n->out_ok[s] || memcmp(f + 6, n->gw_mac, 6) != 0 || memcmp(ip + 12, n->gw_ip, 4) != 0) {
			n->ignored++;
			SWDP_GAP("WIFI GAP: DP14 echo reply that matches no outstanding request (id, seq or source); ignored");
			return SWDP_RX_IGNORED;
		}
		n->out_ok[s] = 0u;
		rtt = now_us >= n->tx_us[s] ? (uint32_t)(now_us - n->tx_us[s]) : 0u;
		if (n->got == 0u || rtt < n->rtt_min_us) n->rtt_min_us = rtt;
		if (rtt > n->rtt_max_us) n->rtt_max_us = rtt;
		n->rtt_sum_us += rtt; n->got++;
		*seq = s; *rtt_us = rtt;
		return SWDP_RX_PING_REPLY;
	}
	n->ignored++;
	return SWDP_RX_IGNORED;
}

/* the run-summary line the ping-run plan expects (--expect "SWEET WLAN: ping summary"); no addresses */
static inline void swdp_ping_summary(const struct swdp_net *n)
{
	char b[160];
	snprintf(b, sizeof b, "SWEET WLAN: ping summary sent=%u got=%u lost=%u rtt_us min/avg/max=%u/%u/%u bad=%u ignored=%u", (unsigned)n->sent,
	    (unsigned)n->got, (unsigned)(n->sent - n->got), (unsigned)n->rtt_min_us, n->got ? (unsigned)(n->rtt_sum_us / n->got) : 0u,
	    (unsigned)n->rtt_max_us, (unsigned)n->bad, (unsigned)n->ignored);
	SWDP_LOG(b);
}


/* store plan and envelope audit (swdp_audit, swdp_plan_*): sweet_wlan_data_plan.h, split out for R29 (T-4115) */
#include "sweet_wlan_data_plan.h"

#endif /* SWEET_WLAN_DATA_H */
