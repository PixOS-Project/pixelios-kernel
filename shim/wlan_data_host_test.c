/* wlan_data_host_test.c - host test for sweet_wlan_data.h (T-71e2, SPEC-WIFI Part E rows DP1-DP15).
 * Anchors: (1) the msgbuf structs as bcmmsgbuf.h declares them, re-typed here with natural alignment: _Static_assert on sizeof/offsetof
 * (an independent derivation of the offsets the builders hard-code) and a byte compare of each builder against the struct filled by field;
 * (2) hand-typed hex items; (3) RFC 1071 / the textbook IPv4 header checksum vectors; (4) the D2H epoch/XOR rule and the ring/index/doorbell
 * numbers from dhd_msgbuf.c; (5) ARP/ICMP frames hand-built here and fed through swdp_net_rx. Build: gcc -O1 -Wall -Wextra -Werror (ASan/UBSan clean). */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char g_log[262144];
static unsigned g_gaps, g_lines;
static void lg(const char *s) { size_t n = strlen(g_log); g_lines++; snprintf(g_log + n, sizeof g_log - n, "%s\n", s); }
#define SWDP_LOG(s) lg(s)
#define SWDP_GAP(s) do { g_gaps++; lg(s); } while (0)
#include "sweet_wlan_data.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("CHECK FAILED line %d: %s\n", __LINE__, #c); fails++; } } while (0)

/* ---- bcmmsgbuf.h structs (R5 bcmmsgbuf.h:181-192, 524-540, 846-852, 993-1006, 1022-1060, 1062-1160, 1204-1230), natural alignment ---- */
typedef union { struct { uint32_t low_addr, high_addr; }; uint64_t u64; } addr64_t;
typedef struct { uint8_t msg_type, if_id, flags, epoch; uint32_t request_id; } cmn_t;
typedef struct { int16_t status; uint16_t ring_id; } cmpl_t;
typedef struct { cmn_t msg; uint8_t da[6], sa[6]; uint8_t tid, if_flags; uint16_t flow_ring_id; uint8_t tc, priority_ifrmmask; uint16_t int_vector, max_items, len_item; addr64_t flow_ring_ptr; } flow_create_t;
typedef struct { cmn_t cmn; uint16_t metadata_buf_len, data_buf_len; uint32_t rsvd; addr64_t metadata_buf_addr, data_buf_addr; } rxpost_t;
typedef struct { cmn_t cmn; uint8_t txhdr[14]; uint8_t flags, seg_cnt; addr64_t metadata_buf_addr, data_buf_addr; uint16_t metadata_buf_len, data_len; uint32_t marker; } txpost_v1_t;
typedef struct { cmn_t cmn; uint8_t txhdr[14]; uint8_t flags, seg_cnt; addr64_t metadata_buf_addr, data_buf_addr; uint16_t metadata_buf_len, data_len; uint8_t ext_flags, scale_factor, rate, exp_time; uint8_t pktinfo[4]; uint32_t pad; } txpost_v2_t;
typedef struct { cmn_t cmn; cmpl_t cmplt; uint32_t rsvd[2]; uint32_t marker; } flow_resp_t;
typedef struct { cmn_t cmn; cmpl_t compl_hdr; uint16_t metadata_len, tx_status; uint32_t ts_low, marker; } txstat_t;
typedef struct { cmn_t cmn; cmpl_t compl_hdr; uint16_t metadata_len, data_len, data_offset, flags; uint32_t rx_status_0, rx_status_1, w0, w1, marker; } rxcmpl_t;

_Static_assert(sizeof(flow_create_t) == 40 && offsetof(flow_create_t, da) == 8 && offsetof(flow_create_t, sa) == 14 && offsetof(flow_create_t, tid) == 20 &&
    offsetof(flow_create_t, flow_ring_id) == 22 && offsetof(flow_create_t, max_items) == 28 && offsetof(flow_create_t, len_item) == 30 &&
    offsetof(flow_create_t, flow_ring_ptr) == 32, "flow create layout");
_Static_assert(sizeof(rxpost_t) == 32 && offsetof(rxpost_t, data_buf_len) == 10 && offsetof(rxpost_t, metadata_buf_addr) == 16 && offsetof(rxpost_t, data_buf_addr) == 24, "rxpost layout");
_Static_assert(sizeof(txpost_v1_t) == 48 && offsetof(txpost_v1_t, txhdr) == 8 && offsetof(txpost_v1_t, flags) == 22 && offsetof(txpost_v1_t, data_buf_addr) == 32 &&
    offsetof(txpost_v1_t, data_len) == 42, "txpost v1 layout");
_Static_assert(sizeof(txpost_v2_t) == 56 && offsetof(txpost_v2_t, data_len) == 42 && offsetof(txpost_v2_t, ext_flags) == 44, "txpost v2 layout");
_Static_assert(sizeof(flow_resp_t) == 24 && offsetof(flow_resp_t, cmplt) == 8 && offsetof(flow_resp_t, marker) == 20, "flow response layout");
_Static_assert(sizeof(txstat_t) == 24 && offsetof(txstat_t, metadata_len) == 12 && offsetof(txstat_t, tx_status) == 14 && offsetof(txstat_t, marker) == 20, "tx status layout");
_Static_assert(sizeof(rxcmpl_t) == 40 && offsetof(rxcmpl_t, metadata_len) == 12 && offsetof(rxcmpl_t, data_len) == 14 && offsetof(rxcmpl_t, data_offset) == 16 &&
    offsetof(rxcmpl_t, flags) == 18 && offsetof(rxcmpl_t, rx_status_0) == 20 && offsetof(rxcmpl_t, marker) == 36, "rx complete layout");

static const uint8_t MAC[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };      /* locally administered test addresses */
static const uint8_t GWM[6] = { 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0xee };
static const uint8_t IPA[4] = { 10, 77, 0, 2 }, GWI[4] = { 10, 77, 0, 1 };

static uint32_t xor_fix(uint8_t *it, uint32_t isz)   /* make the XOR of all words 0 by setting the last word */
{
	uint32_t i, x = 0;
	swdp_p32(it + isz - 4, 0);
	for (i = 0; i + 4 <= isz; i += 4) x ^= swdp_g32(it + i);
	swdp_p32(it + isz - 4, x);
	return x;
}

static void test_builders(void)
{
	uint8_t it[64], frame[60];
	flow_create_t fc; rxpost_t rp; txpost_v1_t t1; txpost_v2_t t2;
	unsigned i;

	memset(&fc, 0, sizeof fc);
	fc.msg.msg_type = 0x3; fc.msg.if_id = 0; fc.msg.epoch = 1; fc.msg.request_id = 0;
	memcpy(fc.da, GWM, 6); memcpy(fc.sa, MAC, 6); fc.tid = 0; fc.flow_ring_id = 2; fc.max_items = 512; fc.len_item = 48;
	fc.flow_ring_ptr.u64 = 0x0000000881234000ull;
	memset(it, 0xEE, sizeof it);
	CHECK(swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, 0x0000000881234000ull, 512, 48) == 0);
	CHECK(memcmp(it, &fc, 40) == 0);
	CHECK(it[0] == 0x03 && it[2] == 0 && it[3] == 1 && it[22] == 2 && it[23] == 0 && swdp_g16(it + 28) == 512 && swdp_g16(it + 30) == 48);
	CHECK(swdp_g32(it + 32) == 0x81234000u && swdp_g32(it + 36) == 8u);
	CHECK(swdp_build_flow_create(it, 1, 0, 1, 0, GWM, MAC, 0x1000, 512, 48) < 0);          /* flowid below the first flow id */
	CHECK(swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, 0x1000, 0, 48) < 0);            /* no items */
	CHECK(swdp_build_flow_create(it, 1, 0, 2, 0, GWM, MAC, 0x1000, 512, 40) < 0);          /* item size is neither 48 nor 56 */

	memset(&rp, 0, sizeof rp);
	rp.cmn.msg_type = 0x11; rp.cmn.epoch = 7; rp.cmn.request_id = 0x52580003u; rp.data_buf_len = 2048; rp.data_buf_addr.u64 = 0x0000000880ABC000ull;
	memset(it, 0xEE, sizeof it);
	CHECK(swdp_build_rxpost(it, 7, 0x52580003u, 0x0000000880ABC000ull, 2048) == 0);
	CHECK(memcmp(it, &rp, 32) == 0);
	CHECK(swdp_build_rxpost(it, 7, 1, 0x1000, 0) < 0 && swdp_build_rxpost(it, 7, 1, 0x1000, 0x10000) < 0);

	for (i = 0; i < 60; i++) frame[i] = (uint8_t)(0x40 + i);
	memset(&t1, 0, sizeof t1);
	t1.cmn.msg_type = 0xF; t1.cmn.if_id = 0; t1.cmn.request_id = 0x54580001u; memcpy(t1.txhdr, frame, 14);
	t1.flags = 0x01 | (3 << 5); t1.seg_cnt = 1; t1.data_buf_addr.u64 = 0x0000000880DEF020ull; t1.data_len = 46;
	memset(it, 0xEE, sizeof it);
	CHECK(swdp_build_txpost(it, 48, 0, 0x54580001u, frame, 60, 3, 0x0000000880DEF020ull) == 0);
	CHECK(memcmp(it, &t1, 48) == 0 && it[3] == 0);
	memset(&t2, 0, sizeof t2);
	t2.cmn.msg_type = 0xF; t2.cmn.request_id = 0x54580001u; memcpy(t2.txhdr, frame, 14);
	t2.flags = 0x01 | (3 << 5); t2.seg_cnt = 1; t2.data_buf_addr.u64 = 0x0000000880DEF020ull; t2.data_len = 46;
	memset(it, 0xEE, sizeof it);
	CHECK(swdp_build_txpost(it, 56, 0, 0x54580001u, frame, 60, 3, 0x0000000880DEF020ull) == 0);
	CHECK(memcmp(it, &t2, 56) == 0);
	CHECK(swdp_build_txpost(it, 52, 0, 1, frame, 60, 0, 0x1000) < 0);            /* wrong item size */
	CHECK(swdp_build_txpost(it, 48, 0, 1, frame, 14, 0, 0x1000) < 0);            /* no payload */
	CHECK(swdp_build_txpost(it, 48, 0, 1, frame, 1515, 0, 0x1000) < 0);          /* above the Ethernet maximum */
	CHECK(swdp_build_txpost(it, 48, 0, 1, frame, 60, 8, 0x1000) < 0);            /* priority above 7 */

	/* hand-typed bytes: ARP-sized TX frame with prio 0: flags byte 0x01 only */
	CHECK(swdp_build_txpost(it, 48, 0, 0x54580000u, frame, 42, 0, 0x880000000ull) == 0);
	CHECK(it[22] == 0x01 && it[23] == 1 && it[42] == 28 && it[43] == 0 && it[0] == 0x0F && it[4] == 0 && it[7] == 0x54 && it[6] == 0x58);
}

static void test_d2h(void)
{
	flow_resp_t fr; txstat_t ts; rxcmpl_t rc;
	struct swdp_cmpl c;
	uint8_t it[40];
	uint32_t seq;

	memset(&fr, 0, sizeof fr);
	fr.cmn.msg_type = 0x4; fr.cmn.epoch = 1; fr.cmplt.status = 0; fr.cmplt.ring_id = 2;
	memcpy(it, &fr, 24); xor_fix(it, 24);
	CHECK(swdp_d2h_check(it, 24, 254) == 0);                       /* first epoch on the wire: 254 % 253 = 1 */
	CHECK(swdp_d2h_check(it, 24, 255) == SWDP_E_EPOCH);            /* the next one expects 2 */
	it[9] ^= 0x10;
	CHECK(swdp_d2h_check(it, 24, 254) == SWDP_E_XOR);              /* a flipped bit in the item */
	it[9] ^= 0x10;
	CHECK(swdp_d2h_check(it, 23, 254) == SWDP_E_SIZE && swdp_d2h_check(it, 0, 254) == SWDP_E_SIZE);
	CHECK(swdp_parse_flow_cmpl(it, &c) == 0 && c.type == 4 && c.ring_id == 2 && c.status == 0);
	it[0] = 0x10;
	CHECK(swdp_parse_flow_cmpl(it, &c) == SWDP_E_TYPE);
	/* epoch wraps: seq 253 + 252 = 505 -> epoch 252; seq 506 -> 0; 507 -> 1 */
	for (seq = 254; seq < 254 + 3 * 253; seq++) {
		fr.cmn.epoch = (uint8_t)(seq % 253);
		memcpy(it, &fr, 24); xor_fix(it, 24);
		CHECK(swdp_d2h_check(it, 24, seq) == 0);
	}

	memset(&ts, 0, sizeof ts);
	ts.cmn.msg_type = 0x10; ts.cmn.epoch = 1; ts.cmn.request_id = 0x54580002u; ts.compl_hdr.status = 0; ts.compl_hdr.ring_id = 2; ts.metadata_len = 0; ts.tx_status = 9;
	memcpy(it, &ts, 24); xor_fix(it, 24);
	CHECK(swdp_d2h_check(it, 24, 254) == 0 && swdp_parse_txstatus(it, &c) == 0 && c.rid == 0x54580002u && c.ring_id == 2 && c.tx_status == 9);

	memset(&rc, 0, sizeof rc);
	rc.cmn.msg_type = 0x12; rc.cmn.epoch = 1; rc.cmn.request_id = 0x52580001u; rc.compl_hdr.ring_id = 1; rc.data_len = 74; rc.data_offset = 4; rc.flags = 1;
	memcpy(it, &rc, 40); xor_fix(it, 40);
	CHECK(swdp_d2h_check(it, 40, 254) == 0 && swdp_parse_rxcmpl(it, 2048, 0, &c) == 0 && c.data_len == 74 && c.data_offset == 4 && c.rflags == 1 && c.rid == 0x52580001u);
	CHECK(swdp_parse_rxcmpl(it, 77, 0, &c) == SWDP_E_RANGE);          /* 4 + 74 > 77: the frame would leave its buffer */
	CHECK(swdp_parse_rxcmpl(it, 78, 0, &c) == 0);                     /* exactly fits */
	swdp_p16(it + 14, 0);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == SWDP_E_RANGE);        /* zero-length frame */
	swdp_p16(it + 14, 0xFFFF); swdp_p16(it + 16, 0xFFFF);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == SWDP_E_RANGE);        /* 0xFFFF + 0xFFFF would wrap in 16 bits: the sum is u32 */
	it[0] = 0x4;
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == SWDP_E_TYPE);

	/* F5: the shared rx_dataoffset applies when data_offset is 0; the completion status must be 0 */
	memcpy(it, &rc, 40); xor_fix(it, 40);
	rc.data_offset = 0; memcpy(it, &rc, 40); xor_fix(it, 40);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == 0 && c.data_offset == 0);
	CHECK(swdp_parse_rxcmpl(it, 2048, 4, &c) == 0 && c.data_offset == 4 && c.data_len == 74);   /* effective offset returned */
	CHECK(swdp_parse_rxcmpl(it, 77, 4, &c) == SWDP_E_RANGE);        /* 4 + 74 > 77 through the shared offset */
	CHECK(swdp_parse_rxcmpl(it, 78, 4, &c) == 0);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0xFFFFFFFFu, &c) == SWDP_E_RANGE);   /* the sum does not wrap */
	swdp_p16(it + 14, 2048);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == 0 && swdp_parse_rxcmpl(it, 2048, 1, &c) == SWDP_E_RANGE);   /* off 0, len 2048, buf 2048 fits; with a shared offset it does not */
	rc.data_offset = 4; swdp_p16(it + 14, 74); swdp_p16(it + 16, 4);
	CHECK(swdp_parse_rxcmpl(it, 78, 8, &c) == 0 && c.data_offset == 4);        /* a non-zero data_offset is used as it is, the shared one is not added */
	rc.compl_hdr.status = -1; memcpy(it, &rc, 40);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == SWDP_E_STATUS && c.status == 0xFFFFu);          /* probe L2 */
	rc.compl_hdr.status = 1; memcpy(it, &rc, 40);
	CHECK(swdp_parse_rxcmpl(it, 2048, 0, &c) == SWDP_E_STATUS);
}

static void test_rings(void)
{
	struct swdp_ring r;
	uint32_t slot, i, free0;
	uint8_t ep;

	swdp_ring_init(&r, 4);
	CHECK(swdp_ring_free(&r) == 3);
	CHECK(swdp_ring_post(&r, &slot, &ep) == 0 && slot == 0 && ep == 1 && r.wr == 1);
	CHECK(swdp_ring_post(&r, &slot, &ep) == 0 && slot == 1 && ep == 2);
	CHECK(swdp_ring_post(&r, &slot, &ep) == 0 && slot == 2 && ep == 3 && r.wr == 3);
	CHECK(swdp_ring_free(&r) == 0 && swdp_ring_post(&r, &slot, &ep) < 0 && r.wr == 3 && r.seq == 257);  /* full: nothing consumed, seq unchanged */
	r.rd = 2;                                                         /* the dongle read 2 items */
	CHECK(swdp_ring_free(&r) == 2 && swdp_ring_post(&r, &slot, &ep) == 0 && slot == 3 && r.wr == 0);   /* wrap */
	CHECK(swdp_ring_post(&r, &slot, &ep) == 0 && slot == 0 && r.wr == 1 && swdp_ring_free(&r) == 0);
	swdp_ring_init(&r, 1);
	CHECK(swdp_ring_free(&r) == 0);                                   /* depth 1 holds nothing */
	swdp_ring_init(&r, 0);
	CHECK(swdp_ring_free(&r) == 0 && swdp_ring_post(&r, &slot, &ep) < 0);

	swdp_ring_init(&r, 512);
	free0 = swdp_ring_free(&r);
	CHECK(free0 == 511);
	for (i = 0; i < 300; i++) { CHECK(swdp_ring_post(&r, &slot, &ep) == 0 && ep == (uint8_t)((254 + i) % 253)); r.rd = r.wr; }
	CHECK(r.seq == 554);

	/* D2H cursor */
	swdp_ring_init(&r, 8);
	CHECK(swdp_d2h_pending(&r, 0) == 0 && swdp_d2h_pending(&r, 3) == 3 && swdp_d2h_pending(&r, 8) == -1 && swdp_d2h_pending(&r, 0xFFFF) == -1);
	swdp_d2h_consume(&r); swdp_d2h_consume(&r);
	CHECK(r.rd == 2 && r.seq == 256 && swdp_d2h_pending(&r, 1) == 7 && swdp_d2h_pending(&r, 0x10001) == 7);   /* wrap; only the low 16 bits count */
	for (i = 0; i < 6; i++) CHECK(swdp_d2h_consume(&r) == 0);
	CHECK(r.rd == 0);
	{ struct swdp_ring z; swdp_ring_init(&z, 0); CHECK(swdp_d2h_consume(&z) == -1 && z.rd == 0 && z.seq == SWDP_EPOCH_INIT); }   /* F10c: no modulo by zero */
	/* F10b: the read index the dongle writes is bounds-checked before r->rd takes it */
	swdp_ring_init(&r, 8);
	CHECK(swdp_h2d_rd_set(&r, 7) == 0 && r.rd == 7 && swdp_h2d_rd_set(&r, 8) == -1 && r.rd == 7 && swdp_h2d_rd_set(&r, 0xFFFF) == -1 && r.rd == 7);
	CHECK(swdp_h2d_rd_set(&r, 0x10003) == 0 && r.rd == 3);        /* only the low 16 bits count */
	swdp_ring_init(&r, 0);
	CHECK(swdp_h2d_rd_set(&r, 0) == -1 && r.rd == 0);

	/* ring ids, index slots, doorbells (dhd_msgbuf.c:114-116, 306-343) */
	CHECK(swdp_ringid(2) == 5 && swdp_ringid(3) == 6 && swdp_ringid(42) == 45);
	CHECK(swdp_h2d_slot(0) == 0 && swdp_h2d_slot(1) == 1 && swdp_h2d_slot(5) == 2 && swdp_h2d_slot(6) == 3 && swdp_h2d_slot(45) == 42);
	CHECK(swdp_d2h_slot(2) == 0 && swdp_d2h_slot(3) == 1 && swdp_d2h_slot(4) == 2);
	CHECK(swdp_db_wr(5, 3) == 0xFF050003u && swdp_db_wr(1, 0x1ff) == 0xFF0101FFu && swdp_db_wr(0, 1) == 0xFF000001u);
	CHECK(swdp_db_rd(3, 7) == 0xDD030007u && swdp_db_rd(4, 0x10000) == 0xDD040000u);
}

static void test_books_and_flow(void)
{
	struct swdp_book b;
	struct swdp_flow f;
	struct swdp_cmpl c;
	uint32_t rid[SWDP_NSLOT + 1], len, i, g0;

	swdp_book_init(&b, SWDP_TX_RID);
	for (i = 0; i < SWDP_NSLOT; i++) { rid[i] = swdp_book_take(&b, 100 + i); CHECK(rid[i] == SWDP_TX_RID + i); }
	CHECK(swdp_book_take(&b, 1) == 0 && b.inflight == SWDP_NSLOT);
	g0 = g_gaps;
	CHECK(swdp_book_done(&b, rid[3], &len) == 0 && len == 103 && b.inflight == SWDP_NSLOT - 1);
	CHECK(swdp_book_done(&b, rid[3], &len) == SWDP_E_ID && g_gaps == g0 + 1 && b.unknown == 1);          /* completed twice */
	CHECK(swdp_book_done(&b, SWDP_RX_RID + 3, &len) == SWDP_E_ID && swdp_book_done(&b, SWDP_TX_RID + SWDP_NSLOT, &len) == SWDP_E_ID &&
	    swdp_book_done(&b, 0, &len) == SWDP_E_ID && g_gaps == g0 + 4);                                    /* foreign kind, out of range, zero */
	CHECK(swdp_book_take(&b, 5) == rid[3] && swdp_book_take(&b, 5) == 0);                                  /* the freed slot is reused */

	memset(&f, 0, sizeof f);
	g0 = g_gaps;
	CHECK(swdp_flow_begin(&f, 1, 43, 512, 48) < 0 && swdp_flow_begin(&f, 43, 43, 512, 48) < 0 && swdp_flow_begin(&f, 2, 43, 1, 48) < 0 &&
	    swdp_flow_begin(&f, 2, 43, 512, 40) < 0 && f.st == SWDP_FLOW_NONE && g_gaps == g0 + 4);
	CHECK(swdp_flow_begin(&f, 2, 43, 512, 48) == 0 && f.st == SWDP_FLOW_CREATING && f.ringid == 5 && f.ring.depth == 512 && g_gaps == g0 + 4 + 3);
	CHECK(swdp_flow_begin(&f, 3, 43, 512, 48) < 0);                                                         /* a second ring is refused */
	memset(&c, 0, sizeof c); c.status = 0; c.ring_id = 7;
	g0 = g_gaps;
	CHECK(swdp_flow_on_create_cmpl(&f, &c) < 0 && f.st == SWDP_FLOW_CREATING && g_gaps == g0 + 1);          /* wrong flow id */
	c.ring_id = 2; c.status = (uint16_t)(int16_t)5;                                                         /* BCMPCIE_RING_ID_INVALID */
	CHECK(swdp_flow_on_create_cmpl(&f, &c) < 0 && f.st == SWDP_FLOW_FAILED && strstr(g_log, "flow create failed, status 5"));
	CHECK(swdp_flow_on_create_cmpl(&f, &c) < 0);                                                            /* not in CREATING any more */
	memset(&f, 0, sizeof f);
	CHECK(swdp_flow_begin(&f, 2, 43, 512, 56) == 0);
	c.status = 0;
	CHECK(swdp_flow_on_create_cmpl(&f, &c) == 0 && f.st == SWDP_FLOW_OPEN && strstr(g_log, "SWEET WLAN: flow ring up flow=2 ring=5 items=512"));
}

static const uint8_t *hexb(const char *h, uint8_t *o, size_t *n)
{
	size_t i, l = strlen(h) / 2;
	for (i = 0; i < l; i++) { unsigned v; sscanf(h + 2 * i, "%2x", &v); o[i] = (uint8_t)v; }
	*n = l;
	return o;
}

/* F9 / AP-323: every frame goes through an exact-size heap copy so ASan sees a read past `len`; the reply buffer is exactly SWDP_OUT_MAX */
static enum swdp_rx_kind rxx(struct swdp_net *n, const uint8_t *f, uint32_t len, uint64_t now, uint8_t *out, uint32_t *ol, uint32_t *seq, uint32_t *rtt)
{
	uint8_t *h = malloc(len ? len : 1u);
	enum swdp_rx_kind k;
	memcpy(h, f, len);
	k = swdp_net_rx(n, h, len, now, out, SWDP_OUT_MAX, ol, seq, rtt);
	free(h);
	return k;
}

static void test_net(void)
{
	struct swdp_net n;
	uint8_t f[256], out[SWDP_OUT_MAX], exp[256];
	size_t el;
	uint32_t ol, len, seq, rtt, i;

	/* checksum vectors: RFC 1071 s.3 example, and the textbook IPv4 header (valid checksum b861) */
	{ static const uint8_t v[8] = { 0x00, 0x01, 0xf2, 0x03, 0xf4, 0xf5, 0xf6, 0xf7 }; CHECK(swdp_csum(v, 8) == 0x220d); }
	{ uint8_t h[20]; hexb("45000073000040004011b861c0a80001c0a800c7", h, &el); CHECK(el == 20 && swdp_csum(h, 20) == 0); }
	{ static const uint8_t odd[3] = { 0x12, 0x34, 0x56 }; CHECK(swdp_csum(odd, 3) == (uint16_t)~(0x1234 + 0x5600)); }

	swdp_net_init(&n, MAC, IPA, GWI);
	CHECK(swdp_build_echo_req(&n, f, 100) == 0 && n.sent == 0);                                  /* gateway MAC not known: no ping */

	/* ARP request: hand-typed bytes */
	len = swdp_build_arp_req(&n, f);
	hexb("ffffffffffff021122334455080600010800060400010211223344550a4d00020000000000000a4d0001", exp, &el);
	CHECK(len == 42 && el == 42 && memcmp(f, exp, 42) == 0);

	/* ARP reply from the gateway -> learned; one from a stranger or for another IP -> ignored */
	hexb("021122334455" "02aabbccddee" "0806" "0001080006040002" "02aabbccddee" "0a4d0001" "021122334455" "0a4d0002", f, &el);
	CHECK(el == 42 && rxx(&n, f, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_GW_LEARNED && n.gw_known && memcmp(n.gw_mac, GWM, 6) == 0);
	{
		struct swdp_net n2; swdp_net_init(&n2, MAC, IPA, GWI);
		f[28] = 9;                                                                               /* sender IP no longer the gateway's */
		CHECK(rxx(&n2, f, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && !n2.gw_known);
		f[28] = 0x0a; f[41] = 9;                                                                 /* target IP not ours */
		CHECK(rxx(&n2, f, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && !n2.gw_known);
		CHECK(rxx(&n2, f, 41, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD);                  /* truncated ARP */
	}

	/* ARP who-has OUR ip from the gateway -> a reply, hand-typed */
	hexb("ffffffffffff" "02aabbccddee" "0806" "0001080006040001" "02aabbccddee" "0a4d0001" "000000000000" "0a4d0002", f, &el);
	CHECK(rxx(&n, f, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_REPLY && ol == 42);
	hexb("02aabbccddee" "021122334455" "0806" "0001080006040002" "021122334455" "0a4d0002" "02aabbccddee" "0a4d0001", exp, &el);
	CHECK(el == 42 && memcmp(out, exp, 42) == 0 && n.arp_req_in == 1);
	f[41] = 9;                                                                                   /* who-has somebody else */
	CHECK(rxx(&n, f, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && ol == 0);

	/* echo request build: exact bytes (checksums by hand-checked RFC 1071 on the typed header) and verifies */
	len = swdp_build_echo_req(&n, f, 1000);
	CHECK(len == 14 + 20 + 8 + 32 && n.seq == 1 && n.sent == 1 && n.out_ok[1]);
	CHECK(memcmp(f, GWM, 6) == 0 && memcmp(f + 6, MAC, 6) == 0 && f[12] == 8 && f[13] == 0 && f[14] == 0x45 && f[22] == 64 && f[23] == 1 && f[20] == 0x40 && f[21] == 0);
	CHECK(swdp_csum(f + 14, 20) == 0 && swdp_csum(f + 34, 40) == 0 && f[34] == 8 && swdp_n16(f + 38) == SWDP_PING_ID && swdp_n16(f + 40) == 1 && f[42] == 0 && f[73] == 31);
	CHECK(swdp_n16(f + 16) == 60 && memcmp(f + 26, IPA, 4) == 0 && memcmp(f + 30, GWI, 4) == 0);

	/* the reply: built here from the request, fields swapped, checksums recomputed by the same RFC 1071 routine (verified against vectors above) */
	{
		uint8_t r[80];
		memcpy(r, f, len);
		memcpy(r, MAC, 6); memcpy(r + 6, GWM, 6);
		memcpy(r + 26, GWI, 4); memcpy(r + 30, IPA, 4); r[22] = 63;
		r[24] = r[25] = 0; swdp_b16(r + 24, swdp_csum(r + 14, 20));
		r[34] = 0; r[36] = r[37] = 0; swdp_b16(r + 36, swdp_csum(r + 34, 40));
		CHECK(rxx(&n, r, len, 1750, out, &ol, &seq, &rtt) == SWDP_RX_PING_REPLY && seq == 1 && rtt == 750 && n.got == 1 && !n.out_ok[1]);
		CHECK(n.rtt_min_us == 750 && n.rtt_max_us == 750);
		CHECK(rxx(&n, r, len, 2000, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && n.got == 1);          /* duplicate reply */
		/* a corrupted copy */
		len = swdp_build_echo_req(&n, f, 5000);
		memcpy(r, f, len); memcpy(r, MAC, 6); memcpy(r + 6, GWM, 6); memcpy(r + 26, GWI, 4); memcpy(r + 30, IPA, 4);
		r[24] = r[25] = 0; swdp_b16(r + 24, swdp_csum(r + 14, 20)); r[34] = 0; r[36] = r[37] = 0; swdp_b16(r + 36, swdp_csum(r + 34, 40));
		r[50] ^= 1;
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_BAD && n.bad >= 1 && strstr(g_log, "icmp checksum"));
		r[50] ^= 1; r[20] = 0x20;                                                                              /* MF set: a fragment */
		r[24] = r[25] = 0; swdp_b16(r + 24, swdp_csum(r + 14, 20));
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED);
		r[20] = 0x40; r[24] = r[25] = 0; swdp_b16(r + 24, swdp_csum(r + 14, 20));
		r[17] = 70;                                                                                            /* total length beyond the frame */
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_BAD);
		r[17] = 60; r[24] ^= 1;
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_BAD && strstr(g_log, "ipv4 header checksum"));
		r[24] ^= 1;
		/* wrong id */
		r[38] ^= 1; r[36] = r[37] = 0; swdp_b16(r + 36, swdp_csum(r + 34, 40));
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && n.got == 1);
		r[38] ^= 1; r[36] = r[37] = 0; swdp_b16(r + 36, swdp_csum(r + 34, 40));
		/* source MAC is not the gateway's */
		r[11] ^= 1;
		CHECK(rxx(&n, r, len, 5100, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && n.got == 1);
		r[11] ^= 1;
		CHECK(rxx(&n, r, len, 5400, out, &ol, &seq, &rtt) == SWDP_RX_PING_REPLY && seq == 2 && rtt == 400 && n.got == 2 && n.rtt_min_us == 400 && n.rtt_max_us == 750);
		/* M50 (F6): seq 17 is one past the 17-entry table; the padding byte after out_ok is set so a widened bound would see an outstanding request */
		_Static_assert(offsetof(struct swdp_net, out_ok) + SWDP_PING_MAX + 1u < offsetof(struct swdp_net, sent), "padding byte after out_ok");
		((uint8_t *)&n)[offsetof(struct swdp_net, out_ok) + SWDP_PING_MAX + 1u] = 1u;
		swdp_b16(r + 40, 17); r[36] = r[37] = 0; swdp_b16(r + 36, swdp_csum(r + 34, 40));
		CHECK(rxx(&n, r, len, 5500, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED && n.got == 2);
		((uint8_t *)&n)[offsetof(struct swdp_net, out_ok) + SWDP_PING_MAX + 1u] = 0u;
		CHECK(rxx(&n, r, 13, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD);                                  /* shorter than Ethernet */
		CHECK(rxx(&n, r, 40, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD);                                  /* ipv4 shorter than a header */
	}

	/* echo request TO us: answered with swapped addresses and valid checksums; payload echoed */
	{
		uint8_t q[80], *ic;
		memcpy(q, f, 74);
		memcpy(q, MAC, 6); memcpy(q + 6, GWM, 6); memcpy(q + 26, GWI, 4); memcpy(q + 30, IPA, 4);
		q[24] = q[25] = 0; swdp_b16(q + 24, swdp_csum(q + 14, 20));
		q[36] = q[37] = 0; swdp_b16(q + 36, swdp_csum(q + 34, 40));
		CHECK(rxx(&n, q, 74, 0, out, &ol, &seq, &rtt) == SWDP_RX_REPLY && ol == 74 && n.echo_req_in == 1);
		ic = out + 34;
		CHECK(memcmp(out, GWM, 6) == 0 && memcmp(out + 6, MAC, 6) == 0 && memcmp(out + 26, IPA, 4) == 0 && memcmp(out + 30, GWI, 4) == 0 && ic[0] == 0 && ic[1] == 0);
		CHECK(swdp_csum(out + 14, 20) == 0 && swdp_csum(ic, 40) == 0 && memcmp(ic + 4, q + 38, 4) == 0 && memcmp(ic + 8, q + 42, 32) == 0 && out[22] == 64);
		/* too large to answer in the reply buffer */
		{ uint8_t big[200]; memset(big, 0, sizeof big); memcpy(big, q, 74); swdp_b16(big + 16, 20 + 8 + 126); memcpy(big + 6, GWM, 6);
		  big[24] = big[25] = 0; swdp_b16(big + 24, swdp_csum(big + 14, 20)); big[36] = big[37] = 0; swdp_b16(big + 36, swdp_csum(big + 34, 134));
		  CHECK(rxx(&n, big, 14 + 154, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD && ol == 0); }
		/* out_cap is a real bound (Opus r1 note): a 74 B reply does not fit 73 B, and fits exactly 74 B (exact-size heap buffers) */
		{
			uint8_t *o = malloc(73), *h = malloc(74), *o2 = malloc(74);
			memcpy(h, q, 74);
			CHECK(swdp_net_rx(&n, h, 74, 0, o, 73, &ol, &seq, &rtt) == SWDP_RX_BAD && ol == 0);
			CHECK(swdp_net_rx(&n, h, 74, 0, o2, 74, &ol, &seq, &rtt) == SWDP_RX_REPLY && ol == 74 && swdp_csum(o2 + 14, 20) == 0);
			free(o); free(h); free(o2);
		}
	}

	/* M42 (F6): a total length beyond the frame is refused by the length check itself: the bytes after `len` below form a valid echo reply */
	{
		struct swdp_net m; uint8_t b[80]; uint32_t l2;
		swdp_net_init(&m, MAC, IPA, GWI); memcpy(m.gw_mac, GWM, 6); m.gw_known = 1;
		l2 = swdp_build_echo_req(&m, b, 100);
		memcpy(b, MAC, 6); memcpy(b + 6, GWM, 6); memcpy(b + 26, GWI, 4); memcpy(b + 30, IPA, 4);
		b[24] = b[25] = 0; swdp_b16(b + 24, swdp_csum(b + 14, 20)); b[34] = 0; b[36] = b[37] = 0; swdp_b16(b + 36, swdp_csum(b + 34, 40));
		CHECK(l2 == 74 && swdp_net_rx(&m, b, l2, 200, out, SWDP_OUT_MAX, &ol, &seq, &rtt) == SWDP_RX_PING_REPLY && m.got == 1);
		m.out_ok[1] = 1; m.got = 0;
		CHECK(swdp_net_rx(&m, b, l2 - 4, 300, out, SWDP_OUT_MAX, &ol, &seq, &rtt) == SWDP_RX_BAD && m.got == 0);
	}

	/* F6 (M45, M47) and the full text of a long GAP line (F8) */
	{
		uint8_t g[48], *o = malloc(41), *h;
		struct swdp_net m; swdp_net_init(&m, MAC, IPA, GWI);
		hexb("ffffffffffff" "02aabbccddee" "0806" "0001080006040001" "02aabbccddee" "0a4d0001" "000000000000" "0a4d0002", g, &el);
		CHECK(rxx(&m, g, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_REPLY);
		g[18] = 5; CHECK(rxx(&m, g, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD); g[18] = 6;        /* hardware address length 5 */
		g[19] = 5; CHECK(rxx(&m, g, 42, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD); g[19] = 4;        /* protocol address length 5 */
		h = malloc(42); memcpy(h, g, 42);
		CHECK(swdp_net_rx(&m, h, 42, 0, o, 41, &ol, &seq, &rtt) == SWDP_RX_BAD && ol == 0);          /* the 42 B ARP reply does not fit a 41 B buffer */
		free(h); free(o);
		memset(g, 0, sizeof g);                                                                      /* IPv4 header length 16 (IHL 4) with a valid 16 B checksum */
		memcpy(g, MAC, 6); memcpy(g + 6, GWM, 6); g[12] = 8; g[14] = 0x44; swdp_b16(g + 16, 24); g[22] = 64; g[23] = 1; memcpy(g + 26, GWI, 4);
		swdp_b16(g + 24, swdp_csum(g + 14, 16)); g[30] = 8;
		CHECK(swdp_csum(g + 14, 16) == 0 && rxx(&m, g, 38, 0, out, &ol, &seq, &rtt) == SWDP_RX_BAD);
		CHECK(strstr(g_log, "WIFI GAP: DP14 rx frame dropped: ipv4 header fields inconsistent with the frame length") != NULL);   /* 86 chars: not cut at 80 */
	}

	/* EAPOL passes to the join hook; frames to other stations are ignored; other ethertypes/protocols are ignored */
	hexb("021122334455" "02aabbccddee" "888e" "0103005f", f, &el);
	CHECK(rxx(&n, f, 18, 0, out, &ol, &seq, &rtt) == SWDP_RX_EAPOL);
	hexb("021122334466" "02aabbccddee" "0806", f, &el);
	CHECK(rxx(&n, f, 14, 0, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED);
	hexb("021122334455" "02aabbccddee" "86dd", f, &el);
	CHECK(rxx(&n, f, 14, 0, out, &ol, &seq, &rtt) == SWDP_RX_IGNORED);

	/* 16 pings at most */
	{
		struct swdp_net m; swdp_net_init(&m, MAC, IPA, GWI); memcpy(m.gw_mac, GWM, 6); m.gw_known = 1;
		for (i = 0; i < SWDP_PING_MAX; i++) CHECK(swdp_build_echo_req(&m, f, i) != 0);
		CHECK(swdp_build_echo_req(&m, f, 99) == 0 && m.sent == SWDP_PING_MAX);
	}

	/* summary line: counters only */
	swdp_ping_summary(&n);
	CHECK(strstr(g_log, "SWEET WLAN: ping summary sent=2 got=2 lost=0 rtt_us min/avg/max=400/575/750") != NULL);
}


/* store-plan / envelope-audit / R2 groups (mkenv .. test_r2): wlan_data_host_test_plans.h, split out for R29 (T-4115) */
#include "wlan_data_host_test_plans.h"


int main(void)
{
	test_builders();
	test_d2h();
	test_rings();
	test_books_and_flow();
	test_net();
	test_plans();
	test_rxpool();
	test_r2();
	test_privacy_and_text();
	printf("RESULT: %s wlan_data_host_test\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
