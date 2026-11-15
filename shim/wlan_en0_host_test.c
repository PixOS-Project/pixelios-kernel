/* wlan_en0_host_test.c - host test for wlan_en0.h (T-4d29, SPEC-WIFI Part E EN0 block, rows EN1-EN12).
 * The XNU ifnet / mbuf / bpf / kern_event KPI is replaced by stubs of the SAME names with the semantics the rows cite (small mbuf buffers so every
 * frame is a chain, ifnet_input that checks pkt_hdr / lengths / alignment / rcvif and consumes the chain, LEGACY enforced, NX_NOAUTO -> xflags).
 * The bus is a fake msgbuf transport recording every audited store plan. Anchors: item bytes are compared with swdp_build_txpost / _rxpost of
 * the same inputs. Every rule has a check that fails when the rule is removed (mutants: tools/test_wlan_en0_mutants.py). Build:
 * gcc -O1 -Wall -Wextra -Werror (ASan/UBSan clean). No hardware. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int errno_t;
static int fails;
#define CHECK(c) do { if (!(c)) { printf("CHECK FAILED line %d: %s\n", __LINE__, #c); fails++; } } while (0)

/* ------------------------------------------------------------------ stub KPI */
#pragma GCC diagnostic ignored "-Wunused-function"       /* a mutant may drop the only caller of a stub */
#ifndef EN0_HDR
#define EN0_HDR "wlan_en0.h"
#endif
#define SM_CAP 128u                            /* small buffers: every frame over 128 bytes is a chain */
struct stub_mbuf { uint8_t *data; size_t len, maxlen; struct stub_mbuf *next; size_t pkt_len; void *pkt_hdr; void *rcvif; uint8_t buf[SM_CAP] __attribute__((aligned(16))); };
typedef struct stub_mbuf *mbuf_t;
struct stub_ifnet { uint32_t flags, xflags, eflags, unit, attached, lladdr_after_attach, lladdr_set, bpf_attached, tap_in, tap_out;
	uint32_t init_flags, family, subfamily, type, pkts_out, bytes_out, errs_out; char name[16]; uint8_t lladdr[6]; };
typedef struct stub_ifnet *ifnet_t;
struct ifnet_stat_increment_param { uint32_t packets_in, bytes_in, errors_in, packets_out, bytes_out, errors_out, collisions, dropped; };
struct ifmediareq { char ifm_name[16]; int ifm_current, ifm_mask, ifm_status, ifm_active, ifm_count; int *ifm_ulist; };
struct kern_event_msg { uint32_t total_size, vendor_code, kev_class, kev_subclass, id, event_code, event_data[32]; };
struct net_event_data { uint32_t if_family, if_unit; char if_name[16]; };
struct ifnet_init_eparams {
	uint32_t ver, len, flags; const char *uniqueid; uint32_t uniqueid_len; const char *name; uint32_t unit, family, type, subfamily;
	errno_t (*output)(ifnet_t, mbuf_t); errno_t (*ioctl)(ifnet_t, unsigned long, void *); void (*detach)(ifnet_t);
	void (*demux)(void), (*add_proto)(void), (*del_proto)(void), (*check_multi)(void), (*framer_extended)(void);
	void (*start)(ifnet_t); void (*pre_enqueue)(void), (*input_poll)(void), (*output_ctl)(void), (*input_ctl)(void);
	uint32_t output_sched_model, sndq_maxlen;
	const void *broadcast_addr; uint32_t broadcast_len; };
#define MBUF_DONTWAIT 1
#define IFNET_INIT_CURRENT_VERSION 1u
#define IFNET_INIT_LEGACY 0x1u
#define IFNET_INIT_NX_NOAUTO 0x4u
#define IFNET_FAMILY_ETHERNET 3u
#define IFT_ETHER 6u
#define IFNET_SUBFAMILY_WIFI 3u   /* kpi_interface.h:143 */
#define IFF_UP 0x1u
#define IFF_BROADCAST 0x2u
#define IFF_RUNNING 0x40u
#define IFF_SIMPLEX 0x800u
#define IFF_MULTICAST 0x8000u
#define IFXF_NX_NOAUTO 0x4u
#define IFEF_ENQUEUE_MULTI 0x2u
#define DLT_EN10MB 1u
#define KEV_VENDOR_APPLE 1u
#define KEV_NETWORK_CLASS 1u
#define KEV_DL_SUBCLASS 2u
#define KEV_DL_LINK_OFF 12u
#define KEV_DL_LINK_ON 13u
#define KEV_MSG_HEADER_SIZE offsetof(struct kern_event_msg, event_data[0])
#define IFM_IEEE80211 0x80
#define IFM_AUTO 0
#define IFM_AVALID 1
#define IFM_ACTIVE 2
#define SIOCSIFADDR 0x8020690cul
#define SIOCAIFADDR 0x8040691aul
#define SIOCSIFFLAGS 0x80206910ul
#define SIOCADDMULTI 0x80206931ul
#define SIOCDELMULTI 0x80206932ul
#define SIOCGIFMEDIA 0xc02c6938ul
#define EN0_XFLAGS(ifp) ((ifp)->xflags)
#define EN0_XF_NOAUTO IFXF_NX_NOAUTO
#define EN0_EFLAGS(ifp) ((ifp)->eflags)
#define EN0_EF_ENQUEUE_MULTI IFEF_ENQUEUE_MULTI

static int sm_live, g_stub_fail;
static ifnet_t g_ifp;
static struct stub_ifnet g_ifnet;
static uint32_t g_expect_len, g_in_n, g_in_err, g_in_bytes;
static uint8_t g_in_frame[2048];
static uint32_t g_in_len, g_alloc_fail, g_alloc_maxchunks_seen;
static uint32_t g_ev_n, g_ev_class, g_ev_sub, g_ev_code;
static struct net_event_data g_ev_nd;
static int g_irq;

static void stub_fail(const char *why) { printf("STUB CHECK FAILED: %s\n", why); g_stub_fail++; }
static errno_t mbuf_allocpacket(int how, size_t n, unsigned int *maxchunks, mbuf_t *out)
{
	size_t k = (n + SM_CAP - 1u) / SM_CAP, i;
	mbuf_t head = NULL, *tail = &head;
	(void)how;
	if (maxchunks != NULL)
		g_alloc_maxchunks_seen++;
	if (g_alloc_fail || (maxchunks != NULL && k > *maxchunks))
		return ENOBUFS;
	for (i = 0; i < k; i++) {
		mbuf_t b = calloc(1, sizeof *b);
		b->data = b->buf; b->maxlen = SM_CAP;
		*tail = b; tail = &b->next; sm_live++;
	}
	*out = head;
	return 0;
}
static void mbuf_freem(mbuf_t m)
{
	while (m != NULL) {
		mbuf_t n = m->next;
		free(m); sm_live--;
		m = n;
	}
}
static void *mbuf_data(const mbuf_t m) { return m->data; }
static void *mbuf_datastart(const mbuf_t m) { return (void *)m->buf; }
static size_t mbuf_len(const mbuf_t m) { return m->len; }
static size_t mbuf_maxlen(const mbuf_t m) { return m->maxlen; }
static mbuf_t mbuf_next(const mbuf_t m) { return m->next; }
static errno_t mbuf_setdata(mbuf_t m, void *d, size_t len)
{
	if ((uint8_t *)d < m->buf || (uint8_t *)d + len > m->buf + SM_CAP)
		stub_fail("mbuf_setdata outside the buffer");
	m->data = d; m->len = len;
	return 0;
}
static void mbuf_pkthdr_setlen(mbuf_t m, size_t l) { m->pkt_len = l; }
static void mbuf_pkthdr_adjustlen(mbuf_t m, int a) { m->pkt_len = (size_t)((long)m->pkt_len + a); }
static size_t mbuf_pkthdr_len(const mbuf_t m) { return m->pkt_len; }
static void mbuf_pkthdr_setheader(mbuf_t m, void *h) { m->pkt_hdr = h; }
static errno_t mbuf_pkthdr_setrcvif(mbuf_t m, ifnet_t i) { m->rcvif = i; return 0; }
static errno_t mbuf_copydata(const mbuf_t m, size_t off, size_t len, void *dst)
{
	const struct stub_mbuf *b = m;
	while (b != NULL && off >= b->len) { off -= b->len; b = b->next; }
	while (len > 0 && b != NULL) {
		size_t n = b->len - off < len ? b->len - off : len;
		memcpy(dst, b->data + off, n);
		dst = (uint8_t *)dst + n; len -= n; off = 0; b = b->next;
	}
	return len == 0 ? 0 : EINVAL;
}
static errno_t ifnet_input(ifnet_t ifp, mbuf_t m, const struct ifnet_stat_increment_param *st)
{
	size_t sum = 0, got = 0;
	const struct stub_mbuf *b;
	if (m->rcvif != ifp) stub_fail("ifnet_input: rcvif is not en0");
	if ((uint8_t *)m->pkt_hdr != m->data - 14) stub_fail("ifnet_input: pkt_hdr != data-14");
	for (b = m; b != NULL; b = b->next) sum += b->len;
	if (m->pkt_len != g_expect_len - 14u || sum != m->pkt_len) stub_fail("ifnet_input: pkthdr.len / chain length != frame-14");
	if (((uintptr_t)m->data & 3u) != 0) stub_fail("ifnet_input: IP header not 4-byte aligned");
	if (st->packets_in != 1u || st->bytes_in != g_expect_len) stub_fail("ifnet_input: stats not packets_in 1 / bytes_in frame");
	if (g_in_err) { mbuf_freem(m); return EINVAL; }          /* the real one frees the chain on error (dlil.c:3794-3809) */
	memcpy(g_in_frame, m->pkt_hdr, 14);
	for (b = m; b != NULL; b = b->next) { memcpy(g_in_frame + 14 + got, b->data, b->len); got += b->len; }
	g_in_len = (uint32_t)(14 + got); g_in_n++; g_in_bytes += st->bytes_in;
	mbuf_freem(m);
	return 0;
}
/* Part A: the interface send queue DLIL keeps for a start-model ifnet (FIFO here; FQ-CoDel in the kernel), ifnet_dequeue, ifnet_start (runs the driver's start at once, as DLIL's starter thread would) */
#define SNDQ 64u
static mbuf_t g_sndq[SNDQ];
static uint32_t g_sndq_h, g_sndq_n, g_sndq_drop, g_starts;
static void (*g_start_fn)(ifnet_t);
static errno_t ifnet_dequeue(ifnet_t i, mbuf_t *m)
{
	(void)i;
	if (g_sndq_n == 0u) return 35;   /* EAGAIN */
	*m = g_sndq[g_sndq_h]; g_sndq_h = (g_sndq_h + 1u) % SNDQ; g_sndq_n--;
	return 0;
}
static void ifnet_start(ifnet_t i) { g_starts++; if (g_start_fn) g_start_fn(i); }
static void mbuf_freem(mbuf_t m);
/* what dlil_output does for a TXSTART interface: enqueue (a full queue is the stack's drop), then start */
static errno_t en0_output(ifnet_t i, mbuf_t m)
{
	if (g_sndq_n == SNDQ) { g_sndq_drop++; mbuf_freem(m); return 55; }
	g_sndq[(g_sndq_h + g_sndq_n) % SNDQ] = m; g_sndq_n++;
	ifnet_start(i);
	return 0;
}
static errno_t ifnet_allocate_extended(const struct ifnet_init_eparams *p, ifnet_t *out)
{
	memset(&g_ifnet, 0, sizeof g_ifnet);
	g_ifnet.init_flags = p->flags; g_ifnet.family = p->family; g_ifnet.subfamily = p->subfamily; g_ifnet.type = p->type; g_ifnet.unit = p->unit;
	snprintf(g_ifnet.name, sizeof g_ifnet.name, "%s", p->name);
	if ((p->flags & IFNET_INIT_LEGACY) || p->start == NULL || p->output || p->pre_enqueue || p->input_poll || p->output_ctl || p->input_ctl || p->output_sched_model != 0u)
		stub_fail("Part A: the start model: no LEGACY, a start callback, no output, sched model NORMAL (kpi_interface.c ifnet_allocate_extended)");
	g_start_fn = p->start;
	if (p->flags & IFNET_INIT_NX_NOAUTO) g_ifnet.xflags |= IFXF_NX_NOAUTO;
	*out = g_ifp = &g_ifnet;
	return 0;
}
static errno_t ifnet_attach(ifnet_t i, const void *ll) { (void)ll; i->attached = 1; return 0; }
static errno_t ifnet_set_lladdr(ifnet_t i, const void *a, size_t n) { i->lladdr_after_attach = i->attached; i->lladdr_set++; memcpy(i->lladdr, a, n); return 0; }
static errno_t ifnet_set_flags(ifnet_t i, uint32_t nw, uint32_t mask) { i->flags = (i->flags & ~mask) | (nw & mask); return 0; }
static errno_t ifnet_set_addrlen(ifnet_t i, size_t n) { (void)i; if (n != 6) stub_fail("addrlen"); return 0; }
static errno_t ifnet_set_hdrlen(ifnet_t i, size_t n) { (void)i; if (n != 14) stub_fail("hdrlen"); return 0; }
static errno_t ifnet_set_mtu(ifnet_t i, uint32_t n) { (void)i; if (n != 1500) stub_fail("mtu"); return 0; }
static errno_t ifnet_set_baudrate(ifnet_t i, uint64_t n) { (void)i; (void)n; return 0; }
static const char *ifnet_name(ifnet_t i) { return i->name; }
static uint32_t ifnet_unit(ifnet_t i) { return i->unit; }
static uint32_t ifnet_subfamily(ifnet_t i) { return i->subfamily; }
static errno_t ifnet_stat_increment_out(ifnet_t i, uint32_t p, uint32_t b, uint32_t e) { i->pkts_out += p; i->bytes_out += b; i->errs_out += e; return 0; }
static errno_t ifnet_event(ifnet_t i, struct kern_event_msg *ev)
{
	(void)i;
	g_ev_n++; g_ev_class = ev->kev_class; g_ev_sub = ev->kev_subclass; g_ev_code = ev->event_code;
	memcpy(&g_ev_nd, ev->event_data, sizeof g_ev_nd);
	if (ev->total_size != KEV_MSG_HEADER_SIZE + sizeof g_ev_nd || ev->vendor_code != KEV_VENDOR_APPLE) stub_fail("kern_event_msg header");
	return 0;
}
static errno_t bpf_attach(ifnet_t i, uint32_t dlt, uint32_t hl, void *send, void *tap)
{
	if (dlt != DLT_EN10MB || hl != 14 || send != NULL || tap != NULL || !i->attached) stub_fail("bpf_attach(DLT_EN10MB, 14, NULL, NULL) after attach");
	i->bpf_attached++;
	return 0;
}
static void bpf_tap_in(ifnet_t i, uint32_t dlt, mbuf_t m, void *h, size_t hl) { (void)m; (void)h; (void)hl; if (dlt != DLT_EN10MB) stub_fail("tap dlt"); i->tap_in++; }
static void bpf_tap_out(ifnet_t i, uint32_t dlt, mbuf_t m, void *h, size_t hl) { (void)m; (void)h; (void)hl; if (dlt != DLT_EN10MB) stub_fail("tap dlt"); i->tap_out++; }
static void ether_demux(void) {}
static void ether_add_proto(void) {}
static void ether_del_proto(void) {}
static void ether_check_multi(void) {}
static void ether_frameout_extended(void) {}
static const uint8_t etherbroadcastaddr[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
int sweet_register_interrupt(void) { g_irq++; return 0; }      /* any interrupt API linked into the module is a failure (EN7) */

/* ------------------------------------------------------------------ log capture */
static char g_log[262144];
static unsigned g_gaps, g_lines;
static void lg(const char *s) { size_t n = strlen(g_log); g_lines++; snprintf(g_log + n, sizeof g_log - n, "%s\n", s); }
#define SWDP_LOG(s) lg(s)
#define SWDP_GAP(s) do { g_gaps++; lg(s); } while (0)
static unsigned count_of(const char *sub)
{
	unsigned n = 0;
	const char *p = g_log;
	while ((p = strstr(p, sub)) != NULL) { n++; p += strlen(sub); }
	return n;
}
#include "sweet_wlan_data.h"
#include "sweet_wlan_data_plan.h"
#include EN0_HDR                              /* the mutant runner points this at its private copy */

/* ------------------------------------------------------------------ fake bus */
struct call { struct swdp_store st[SWDP_MAX_STORES_RXB]; uint32_t n; uint8_t item[64]; uint8_t pay[SWDP_ETH_MAXFRAME]; uint32_t ritem; uint8_t rid[SWDP_RXB_MAX][4]; };
static struct call g_calls[512];
static uint32_t g_ncalls, g_exec_fail;
static uint8_t g_txc[256][SWDP_ISZ_TX_CMPLT], g_rxc[256][SWDP_ISZ_RX_CMPLT], g_rxmem[SWDP_RX_MAX][2048];
static uint32_t g_txc_wr, g_rxc_wr, g_txc_seq = SWDP_EPOCH_INIT, g_rxc_seq = SWDP_EPOCH_INIT;

static int bus_exec(void *ctx, const struct swdp_store *s, uint32_t n)
{
	struct call *c = &g_calls[g_ncalls % 512u];
	uint32_t i;
	(void)ctx;
	if (g_exec_fail)
		return -1;
	memset(c, 0, sizeof *c); c->n = n;
	for (i = 0; i < n; i++) {
		c->st[i] = s[i];
		if (s[i].kind == SWDP_SK_ITEM) { memcpy(c->item, s[i].data, s[i].len); if (s[i].ring == SWDP_RING_RXPOST && c->ritem < SWDP_RXB_MAX) memcpy(c->rid[c->ritem++], s[i].data + 4, 4); }
		if (s[i].kind == SWDP_SK_PAYLOAD) memcpy(c->pay, s[i].data, s[i].len);
	}
	g_ncalls++;
	return 0;
}
static uint32_t bus_wr(void *ctx, uint32_t ring) { (void)ctx; return ring == SWDP_RING_TXCPL ? g_txc_wr % 256u : g_rxc_wr % 256u; }
static const uint8_t *bus_item(void *ctx, uint32_t ring, uint32_t idx) { (void)ctx; return ring == SWDP_RING_TXCPL ? g_txc[idx] : g_rxc[idx]; }
static const uint8_t *bus_rxbuf(void *ctx, uint32_t slot) { (void)ctx; return g_rxmem[slot]; }

static void seal(uint8_t *it, uint32_t isz, uint32_t *seq)       /* epoch byte and the XOR word (D2H audit) */
{
	uint32_t i, x = 0;
	it[3] = (uint8_t)(*seq % SWDP_EPOCH_MOD); (*seq)++;
	swdp_p32(it + isz - 4, 0);
	for (i = 0; i + 4 < isz; i += 4) x ^= swdp_g32(it + i);
	swdp_p32(it + isz - 4, x);
}
static void push_tx(uint32_t rid, int status)
{
	uint8_t *it = g_txc[g_txc_wr % 256u];
	memset(it, 0, SWDP_ISZ_TX_CMPLT);
	it[0] = SWDP_MT_TX_STATUS; swdp_p32(it + 4, rid); swdp_p16(it + 8, (uint32_t)status & 0xFFFFu); swdp_p16(it + 10, 2);
	seal(it, SWDP_ISZ_TX_CMPLT, &g_txc_seq); g_txc_wr++;
}
static void push_rx(uint32_t rid, const uint8_t *frame, uint32_t len, uint32_t off)
{
	uint8_t *it = g_rxc[g_rxc_wr % 256u];
	memset(it, 0, SWDP_ISZ_RX_CMPLT);
	it[0] = SWDP_MT_RX_CMPLT; swdp_p32(it + 4, rid); swdp_p16(it + 10, 4); swdp_p16(it + 14, len); swdp_p16(it + 16, off);
	if (frame != NULL) memcpy(g_rxmem[rid - SWDP_RX_RID] + off, frame, len);
	seal(it, SWDP_ISZ_RX_CMPLT, &g_rxc_seq); g_rxc_wr++;
}

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
	e.depth0 = 128; e.depth1 = 2048; e.depth_flow = 4; e.max_sub = 43; e.flowid = 2; e.flow_isz = 56;
	e.buf_sz = 2048; e.idx_sz = 4; e.flow_open = 1;
	e.wr0 = 5;
	e.depth_d2h[0] = 64; e.depth_d2h[1] = 256; e.depth_d2h[2] = 256;
	return e;
}

static struct en0 g_e;
static const uint8_t MAC[6] = { 0x02, 0x00, 0x5e, 0x10, 0x00, 0x01 };    /* documentation MAC, not a real one */

static void setup(void)
{
	uint8_t it[24] = { 0 };
	struct swdp_cmpl c;
	memset(&g_e, 0, sizeof g_e); en0_self = NULL;
	g_e.env = mkenv();
	g_e.bus = (struct en0_bus){ NULL, bus_exec, bus_wr, bus_item, bus_rxbuf };
	swdp_book_init(&g_e.rxb, SWDP_RX_RID); swdp_book_init(&g_e.txb, SWDP_TX_RID);
	swdp_ring_init(&g_e.rxpost, g_e.env.depth1);
	swdp_ring_init(&g_e.txcpl, g_e.env.depth_d2h[1]); swdp_ring_init(&g_e.rxcpl, g_e.env.depth_d2h[2]);
	g_txc_wr = g_rxc_wr = 0; g_txc_seq = g_rxc_seq = SWDP_EPOCH_INIT; g_ncalls = 0; g_exec_fail = 0; g_in_n = 0; g_in_err = 0; g_in_bytes = 0;
	g_alloc_fail = 0; g_log[0] = 0; g_gaps = 0; g_lines = 0;
	swdp_flow_begin(&g_e.flow, 2, 43, g_e.env.depth_flow, 56);
	it[0] = SWDP_MT_FLOW_CREATE_CMPLT; swdp_p16(it + 10, 2);
	swdp_parse_flow_cmpl(it, &c);
	swdp_flow_on_create_cmpl(&g_e.flow, &c);
}

static void frame_fill(uint8_t *f, uint32_t len, uint8_t seed)
{
	uint32_t i;
	for (i = 0; i < len; i++) f[i] = (uint8_t)(seed + i * 7u);
	f[12] = 0x08; f[13] = 0x00;
}

static mbuf_t mk_tx_mbuf(const uint8_t *f, uint32_t len)
{
	mbuf_t m = NULL, b;
	uint32_t off = 0;
	if (mbuf_allocpacket(MBUF_DONTWAIT, len, NULL, &m) != 0) return NULL;
	for (b = m; b != NULL && off < len; b = b->next) {
		uint32_t n = len - off < SM_CAP ? len - off : SM_CAP;
		memcpy(b->buf, f + off, n); b->data = b->buf; b->len = n; off += n;
	}
	m->pkt_len = len;
	return m;
}

/* ------------------------------------------------------------------ the tests */
static void test_attach(void)
{
	setup();
	CHECK(en0_attach(&g_e, NULL) == -1);                                  /* EN2: no MAC, no attach, GAP */
	CHECK(count_of("EN2 no Wi-Fi MAC") == 1 && g_ifp == NULL);
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	CHECK(g_ifnet.init_flags == IFNET_INIT_NX_NOAUTO);        /* EN1 */
	CHECK((g_ifnet.xflags & IFXF_NX_NOAUTO) != 0);
	CHECK((g_ifnet.eflags & IFEF_ENQUEUE_MULTI) == 0);                                /* EN11 */
	CHECK(strcmp(g_ifnet.name, "en") == 0 && g_ifnet.unit == 0 && g_ifnet.family == IFNET_FAMILY_ETHERNET && g_ifnet.type == IFT_ETHER);
	CHECK(g_ifnet.subfamily == IFNET_SUBFAMILY_WIFI);                                 /* T-28b: functional type WIFI_INFRA, not WIRED */
	CHECK(count_of("subfamily 3") == 1 && count_of("EN13 en0 subfamily is not WIFI") == 0);
	CHECK(g_ifnet.lladdr_set == 1 && g_ifnet.lladdr_after_attach == 1 && memcmp(g_ifnet.lladdr, MAC, 6) == 0);   /* EN2: after attach, the caller's MAC */
	CHECK(g_ifnet.bpf_attached == 1);                                                 /* EN5 */
	CHECK(count_of("en0 attached if_xflags=0x4") == 1);
	CHECK(count_of("EN1 en0 allocated without") == 0);
	CHECK(count_of("EN7 interrupts off") == 1 && count_of("EN12 no Apple80211") == 1);
	CHECK(g_irq == 0);
}

static void test_rx(void)
{
	static const uint32_t lens[] = { 60, 14, 1514, 129, 130, 1500 };
	uint8_t f[2048];
	uint32_t i, posted, before;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	en0_rx_fill(&g_e);
	CHECK(g_ncalls == SWDP_NSLOT && g_calls[0].n == 3u && g_calls[0].ritem == 1u && g_calls[SWDP_NSLOT - 1u].n == 3u && g_e.rxb.inflight == SWDP_NSLOT);       /* patch 0357: the 16 first posts are ONE plan: 16 items, one index, one doorbell */
	CHECK(g_calls[0].item[0] == SWDP_MT_RXBUF_POST && swdp_g16(g_calls[0].item + 10) == 2048);       /* EN7: rx buffers reposted by the poll code */
	posted = g_ncalls;
	for (i = 0; i < sizeof lens / sizeof lens[0]; i++) {
		frame_fill(f, lens[i], (uint8_t)(i + 1));
		push_rx(SWDP_RX_RID + i, f, lens[i], 4);
		g_expect_len = lens[i];
		before = g_in_n;
		CHECK(en0_poll(&g_e) == 1);
		CHECK(g_in_n == before + 1 && g_in_len == lens[i] && memcmp(g_in_frame, f, lens[i]) == 0);       /* EN3/EN4: chain lengths sum, bytes intact */
		CHECK(sm_live == 0);
	}
	CHECK(g_in_n == 6 && g_e.rx_frames == 6);
	CHECK(g_ifp->tap_in == 6);                                                                           /* EN5 */
	CHECK(g_ncalls == posted + 6u * 2u);                                                                 /* per completion: consume plan + repost plan */
	CHECK(g_e.rxb.inflight == SWDP_NSLOT);                                                               /* every buffer back on the ring */
	/* too long (> SWDP_ETH_MAXFRAME) and too short (< 14): dropped, counted, GAP, no mbuf left */
	push_rx(SWDP_RX_RID + 6, NULL, 1600, 4); push_rx(SWDP_RX_RID + 7, NULL, 13, 4);
	g_expect_len = 0;
	CHECK(en0_poll(&g_e) == 2);
	CHECK(g_e.rx_drop == 2 && count_of("EN3") >= 2 && sm_live == 0 && g_in_n == 6);
	/* mbuf allocation failure and an ifnet_input error: counted drops, nothing freed twice (ASan), no leak */
	frame_fill(f, 60, 9); push_rx(SWDP_RX_RID + 8, f, 60, 4); g_alloc_fail = 1; g_expect_len = 60;
	CHECK(en0_poll(&g_e) == 1 && g_e.rx_drop == 3 && sm_live == 0);
	g_alloc_fail = 0; g_in_err = 1; push_rx(SWDP_RX_RID + 9, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_e.rx_drop == 4 && count_of("EN4 ifnet_input returned an error") == 1 && sm_live == 0);
	g_in_err = 0;
	CHECK(g_alloc_maxchunks_seen == 0);                                                                  /* E-1: maxchunks NULL */
	/* a completion for an id that is not in flight is counted and dropped */
	push_rx(SWDP_RX_RID + 1, NULL, 60, 4); push_rx(0x1234, NULL, 60, 4);
	i = g_e.rxb.unknown;
	g_expect_len = 60;
	en0_poll(&g_e);
	CHECK(g_e.rxb.unknown >= i + 1);
}

static void test_tx(void)
{
	uint8_t f[SWDP_ETH_MAXFRAME + 8], exp[64];
	uint32_t i, rid, slot;
	mbuf_t m;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	frame_fill(f, 60, 3);
	m = mk_tx_mbuf(f, 60);
	CHECK(en0_output(g_ifp, m) == 0 && sm_live == 0 && g_ncalls == 1);                                   /* EN6: one tx item, mbuf freed */
	rid = SWDP_TX_RID;
	slot = (uint32_t)(g_calls[0].st[0].addr - g_e.env.txbuf.pa) / g_e.env.buf_sz;
	CHECK(slot == 0 && g_calls[0].n == 4 && g_calls[0].st[0].kind == SWDP_SK_PAYLOAD && g_calls[0].st[1].kind == SWDP_SK_ITEM);
	swdp_build_txpost(exp, 56, 0, rid, f, 60, 0, g_e.env.txbuf.pa);
	CHECK(memcmp(g_calls[0].item, exp, 56) == 0);                                                        /* item bytes == swdp_build_txpost of the same frame */
	CHECK(memcmp(g_calls[0].pay, f + 14, 46) == 0 && g_calls[0].st[0].len == 46);
	CHECK(g_ifp->tap_out == 1 && g_ifp->pkts_out == 1 && g_ifp->bytes_out == 60);
	frame_fill(f, 1514, 5);
	m = mk_tx_mbuf(f, 1514);                                                                             /* a chain of 12 buffers */
	CHECK(en0_output(g_ifp, m) == 0 && g_ncalls == 2 && sm_live == 0 && memcmp(g_calls[1].pay, f + 14, 1500) == 0);
	m = mk_tx_mbuf(f, 100);
	CHECK(en0_output(g_ifp, m) == 0 && g_ncalls == 3 && sm_live == 0);
	/* the flow ring (depth 4, 3 usable) is full: en0_start leaves the frame in the interface queue (no drop, en0 holds nothing) and asks for a restart */
	i = g_e.txb.inflight;
	{
		const uint32_t w0 = g_e.tx_waits;                                                               /* the third post already filled the ring: en0_start stopped once */
		m = mk_tx_mbuf(f, 100);
		CHECK(w0 >= 1u && en0_output(g_ifp, m) == 0 && g_ncalls == 3 && sm_live == 1 && g_sndq_n == 1 && g_e.tx_drop == 0 && g_e.tx_blocked == 1 && g_e.tx_waits == w0 + 1u && g_e.txb.inflight == i);
		m = mk_tx_mbuf(f, 101);
		CHECK(en0_output(g_ifp, m) == 0 && g_sndq_n == 2 && sm_live == 2 && g_e.tx_waits == w0 + 2u);
	}
	/* tx completions release the slots: the poll restarts en0_start (EN0_IFSTART), which posts the two queued frames in order */
	push_tx(SWDP_TX_RID + 0, 0); push_tx(SWDP_TX_RID + 1, 0); push_tx(SWDP_TX_RID + 2, 0);
	g_e.flow.ring.rd = 3;                                                                                /* the dongle read the three items (the H2D read index is DP15's) */
	CHECK(en0_poll(&g_e) == 3 && g_sndq_n == 0 && sm_live == 0 && g_ncalls == 6 && g_e.tx_frames == 5 && g_e.tx_blocked == 0);   /* ONE consume plan, then the 2 posts */
	CHECK(g_calls[4].st[0].len == 86 && g_calls[5].st[0].len == 87 && g_calls[3].n == 2 && g_calls[3].st[0].kind == SWDP_SK_RDIDX);
	g_e.flow.ring.rd = g_e.flow.ring.wr;
	/* bad lengths and a transport that refuses: drop + GAP + free */
	m = mk_tx_mbuf(f, 13); CHECK(en0_output(g_ifp, m) == 0 && g_e.tx_drop == 1 && sm_live == 0 && count_of("EN6 tx dropped (bad length") == 1);
	m = mk_tx_mbuf(f, 1514); m->pkt_len = 1515; CHECK(en0_output(g_ifp, m) == 0 && g_e.tx_drop == 2);
	mbuf_freem(NULL);
	CHECK(sm_live == 0);
	g_exec_fail = 1; m = mk_tx_mbuf(f, 100);
	i = g_e.txb.inflight;
	CHECK(en0_output(g_ifp, m) == 0 && g_e.tx_drop == 3 && g_e.txb.inflight == i && sm_live == 0 && g_sndq_n == 0 && count_of("DP15 transport") >= 1);
	g_exec_fail = 0;
	g_e.flow.st = SWDP_FLOW_FAILED;                                                                      /* flow ring not up: the queued frame is drained, counted, GAP */
	m = mk_tx_mbuf(f, 100); CHECK(en0_output(g_ifp, m) == 0 && g_e.tx_drop == 4 && sm_live == 0 && g_sndq_n == 0 && count_of("flow ring is down") == 1);
}

/* review perf-d F1: a refused batch consume commit stops the en0 poll: the completions it processed are never handled a second time */
static void test_commit_refused(void)
{
	uint32_t calls;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	push_tx(SWDP_TX_RID + 0, 0); push_tx(SWDP_TX_RID + 1, 0);
	g_exec_fail = 1;
	CHECK(en0_poll(&g_e) == 2 && g_e.cons_fail == 1u && count_of("EN7 consume commit refused") == 1);
	g_exec_fail = 0;
	calls = g_ncalls;
	CHECK(en0_poll(&g_e) == 0 && g_ncalls == calls);                     /* nothing handled twice, no further plan */
}

static void test_poll_edges(void)
{
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	CHECK(en0_poll(&g_e) == 0);                                                                          /* empty rings */
	CHECK(en0_poll(&g_e) == 0);
	push_tx(SWDP_TX_RID + 5, 0);
	g_txc[0][3] ^= 0x55;                                                                                 /* epoch wrong: not consumed, retried, GAP once */
	CHECK(en0_poll(&g_e) == 0 && en0_poll(&g_e) == 0 && count_of("EN7 D2H item epoch not current") == 1 && g_e.txcpl.rd == 0);
	g_txc[0][3] ^= 0x55; g_txc[0][12] ^= 1;                                                              /* XOR wrong: skipped with a GAP */
	CHECK(en0_poll(&g_e) == 0 && count_of("EN7 corrupt D2H item skipped") == 1 && g_e.txcpl.rd == 1);
	/* review perf-d F2: the rx ring skips a corrupt item the same way (counted into the batch commit), nothing delivered */
	push_rx(SWDP_RX_RID + 0, NULL, 60, 0);
	g_rxc[0][12] ^= 1;
	{
		const uint32_t rx0 = g_e.rx_frames, calls0 = g_ncalls;
		CHECK(en0_poll(&g_e) == 0 && count_of("EN7 corrupt D2H item skipped") == 2 && g_e.rxcpl.rd == 1 && g_e.rx_frames == rx0 && g_ncalls == calls0 + 1);
	}
	CHECK(g_irq == 0);
}

static void test_no_interrupt(void)
{
	static const char *const bad[] = { "IOInterruptEventSource", "registerInterrupt", "irq", "request_irq", "sweet_register_interrupt", "IRQ" };
	char buf[65536];
	size_t n;
	uint32_t i;
	char alt[512];
	const char *slash = strrchr(__FILE__, '/');
	FILE *fp = fopen(EN0_HDR, "r");
	if (fp == NULL && slash != NULL) {                    /* run from another directory: next to this source */
		snprintf(alt, sizeof alt, "%.*s/wlan_en0.h", (int)(slash - __FILE__), __FILE__);
		fp = fopen(alt, "r");
	}
	CHECK(fp != NULL);
	if (fp == NULL) return;
	n = fread(buf, 1, sizeof buf - 1, fp); buf[n] = 0; fclose(fp);
	CHECK(n > 1000 && n < sizeof buf - 1);
	for (i = 0; i < sizeof bad / sizeof bad[0]; i++)
		if (strstr(buf, bad[i]) != NULL) { printf("interrupt token '%s' in the module\n", bad[i]); fails++; }
	CHECK(g_irq == 0);
}

static int g_apply_n;
static int apply_ok(ifnet_t i, const struct en0_cfg *c) { (void)i; g_apply_n++; return c->ip[0] == 192 ? 0 : 1; }

static void test_link_and_address(void)
{
	struct ifmediareq mr;
	setup(); g_ifp = NULL; g_apply_n = 0; g_ev_n = 0;
	CHECK(en0_attach(&g_e, MAC) == 0);
	memset(&mr, 0, sizeof mr);
	CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_current == (IFM_IEEE80211 | IFM_AUTO) && mr.ifm_status == IFM_AVALID);   /* down */
	g_e.apply = apply_ok;
	g_e.bootarg = NULL;                                                                                  /* EN9: no configuration, no address, no default */
	en0_set_link(&g_e, 1);
	CHECK(g_apply_n == 0 && g_e.addr_done == 0 && count_of("EN9 no static IP") >= 1);
	CHECK((g_ifp->flags & IFF_RUNNING) && g_ev_n == 1 && g_ev_class == KEV_NETWORK_CLASS && g_ev_sub == KEV_DL_SUBCLASS && g_ev_code == KEV_DL_LINK_ON);   /* EN8 */
	CHECK(g_ev_nd.if_family == IFNET_FAMILY_ETHERNET && g_ev_nd.if_unit == 0 && strcmp(g_ev_nd.if_name, "en") == 0);
	CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_status == (IFM_AVALID | IFM_ACTIVE));
	{       /* W-UI: one media word; copied out when the caller passes a list */
		int ul[2] = { 0, 0 };
		mr.ifm_count = 0; mr.ifm_ulist = NULL;
		CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_count == 1);
		mr.ifm_count = 2; mr.ifm_ulist = ul;
		CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_count == 1 && ul[0] == (IFM_IEEE80211 | IFM_AUTO) && ul[1] == 0);
		mr.ifm_count = 0; mr.ifm_ulist = NULL;
	}
	g_e.bootarg = "192.0.2.2,255.255.255.0,192.0.2.1";
	en0_set_link(&g_e, 1);
	CHECK(g_apply_n == 1 && g_e.addr_done == 1);
	en0_set_link(&g_e, 1);
	CHECK(g_apply_n == 1);                                                                               /* applied once */
	en0_set_link(&g_e, 0);
	CHECK(!(g_ifp->flags & IFF_RUNNING) && g_ev_code == KEV_DL_LINK_OFF && g_ev_n == 4);
	CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_status == IFM_AVALID);
	{       /* patch 0281 (W-D6): en0_link_only = the ifnet side only: IFF_RUNNING, the kernel event, link_up (SIOCGIFMEDIA); the flow ring is NOT failed and the boot address is not applied again */
		const int ev0 = (int)g_ev_n, ap0 = (int)g_apply_n; const int fst = (int)g_e.flow.st;

		en0_set_link(&g_e, 1);
		CHECK((int)g_apply_n == ap0 && (g_ifp->flags & IFF_RUNNING) && g_e.link_up == 1u);
		g_e.flow.st = SWDP_FLOW_OPEN;
		en0_link_only(&g_e, 0);
		CHECK(!(g_ifp->flags & IFF_RUNNING) && g_ev_code == KEV_DL_LINK_OFF && (int)g_ev_n == ev0 + 2 && g_e.link_up == 0u && (int)g_e.flow.st == (int)SWDP_FLOW_OPEN);       
		CHECK(en0_ioctl(g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_status == IFM_AVALID);
		en0_link_only(&g_e, 1);
		CHECK((g_ifp->flags & IFF_RUNNING) && g_ev_code == KEV_DL_LINK_ON && (int)g_ev_n == ev0 + 3 && g_e.link_up == 1u && (int)g_apply_n == ap0 && (int)g_e.flow.st == (int)SWDP_FLOW_OPEN);
		g_e.flow.st = (typeof(g_e.flow.st))fst;
		g_e.attached = 0;
		en0_link_only(&g_e, 0);
		CHECK((g_ifp->flags & IFF_RUNNING) && g_e.link_up == 1u && (int)g_ev_n == ev0 + 3);       /* not attached: nothing */
		g_e.attached = 1;
	}
}

static void test_boot_cfg(void)
{
	static const char *const bad[] = { "", "192.0.2.2,255.255.255.0", "192.0.2.2,255.255.255.0,192.0.2.1x", "256.0.2.2,255.255.255.0,192.0.2.1",
	    "0.0.0.0,255.255.255.0,192.0.2.1", "192.0.2.2,255.0.255.0,192.0.2.1", "192.0.2.2,0.0.0.0,192.0.2.1", "192.0.2.02,255.255.255.0,192.0.2.1",
	    "1.2.3.4.5,255.255.255.0,192.0.2.1", "192.0.2.2;255.255.255.0;192.0.2.1", "192.0.2.2,255.255.255.0,", "a.b.c.d,255.255.255.0,192.0.2.1" };
	struct en0_cfg c;
	uint32_t i;
	setup();
	CHECK(en0_boot_cfg("192.0.2.2,255.255.255.0,192.0.2.1", &c) == 0);
	CHECK(c.ip[0] == 192 && c.ip[3] == 2 && c.mask[2] == 255 && c.mask[3] == 0 && c.gw[3] == 1);
	CHECK(en0_boot_cfg("198.51.100.20,255.255.255.128,198.51.100.1", &c) == 0 && c.mask[3] == 128);
	CHECK(en0_boot_cfg(NULL, &c) == -1 && c.ip[0] == 0 && c.mask[0] == 0 && c.gw[0] == 0);              /* absent: no address, never a default */
	for (i = 0; i < sizeof bad / sizeof bad[0]; i++) {
		memset(&c, 0xAA, sizeof c);
		if (en0_boot_cfg(bad[i], &c) != -1 || c.ip[0] != 0 || c.mask[0] != 0 || c.gw[0] != 0) { printf("bad config accepted/left set: '%s'\n", bad[i]); fails++; }
	}
	CHECK(count_of("EN9") >= 12);
}

static void test_ioctl(void)
{
	uint32_t before;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	CHECK(en0_ioctl(g_ifp, SIOCSIFADDR, NULL) == 0 && (g_ifp->flags & IFF_UP));                           /* EN10 */
	g_ifp->flags = 0;
	CHECK(en0_ioctl(g_ifp, SIOCAIFADDR, NULL) == 0 && (g_ifp->flags & IFF_UP));
	CHECK(en0_ioctl(g_ifp, SIOCSIFFLAGS, NULL) == 0);
	before = g_gaps;
	CHECK(en0_ioctl(g_ifp, SIOCADDMULTI, NULL) == 0 && en0_ioctl(g_ifp, SIOCDELMULTI, NULL) == 0 && g_gaps == before + 2);   /* accepted AND said so */
	before = g_gaps;
	CHECK(en0_ioctl(g_ifp, 0x80206999ul, NULL) == EOPNOTSUPP && g_gaps == before + 1 && count_of("EN10 en0 ioctl 0x80206999 unsupported") == 1);
	CHECK(en0_ioctl(g_ifp, 0x80206999ul, NULL) == EOPNOTSUPP && g_gaps == before + 1);                  /* once per command */
	CHECK(en0_ioctl(g_ifp, 0x8020699aul, NULL) == EOPNOTSUPP && g_gaps == before + 2);
}

/* patch 0282 (W-D6 boot B): the EAPOL receive hook. An ethertype 0x888E frame never reaches ifnet_input: with no hook (or a hook that says "no 4-way running") it is dropped with a counted GAP (the first and every 64th), with a hook that
 * consumes it the hook's reply is posted on the flow ring through the ordinary tx plan (en0_tx_frame: the slot is remembered, en0_tx_busy says when the completion came); anything else is delivered as before. */
static unsigned ep_calls, ep_rlen, ep_seen_len;
static int ep_ret;
static uint8_t ep_reply[100], ep_seen[100];
static int ep_hook(void *ctx, const uint8_t *f, uint32_t len, uint8_t *out, uint32_t cap, uint32_t *olen)
{
	(void)ctx;
	ep_calls++;
	ep_seen_len = len;
	memcpy(ep_seen, f, len < sizeof ep_seen ? len : sizeof ep_seen);
	*olen = 0;
	if (ep_ret != 0) return ep_ret;
	if (ep_rlen > cap) return -9;
	memcpy(out, ep_reply, ep_rlen);
	*olen = ep_rlen;
	return 0;
}
static void test_eapol(void)
{
	uint8_t f[2048], ip[60];
	uint32_t i, before, calls, k, rid = 0;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	en0_rx_fill(&g_e);
	frame_fill(f, 60, 11);
	f[12] = 0x88; f[13] = 0x8e;
	memcpy(ep_reply, f, 60);
	ep_reply[0] ^= 0x55; ep_reply[14] = 0x77;
	/* no hook: dropped, one GAP at the first, not at the 2nd..63rd, again at the 64th; never delivered up */
	before = g_in_n;
	for (i = 0; i < 64u; i++) {
		push_rx(SWDP_RX_RID + (i % 16u), f, 60, 4);
		g_expect_len = 60;
		CHECK(en0_poll(&g_e) == 1);
	}
	CHECK(g_in_n == before && g_e.rx_frames == 0 && g_e.rx_drop == 0 && g_e.eapol_drop == 64u && g_e.eapol_rx == 0 && count_of("EN4 EAPOL dropped (no 4-way running, rekey not handled), so far") == 2 && sm_live == 0);
	CHECK(count_of("so far 1\n") == 1 && count_of("so far 64\n") == 1);
	/* a hook that consumes the frame without a reply: counted, nothing posted, nothing delivered */
	g_e.eapol = ep_hook; g_e.eapol_ctx = &g_e;
	ep_ret = 0; ep_rlen = 0;
	calls = g_ncalls;
	push_rx(SWDP_RX_RID + 0u, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && ep_calls == 1 && ep_seen_len == 60 && memcmp(ep_seen, f, 60) == 0 && g_e.eapol_rx == 1 && g_e.eapol_tx == 0 && g_e.eapol_drop == 64u && g_in_n == before && g_e.tx_frames == 0u);
	CHECK(g_ncalls == calls + 2u);       /* the consume plan and the rx repost only: no tx plan */
	/* a reply: posted on the flow ring through the tx plan, the slot kept, busy until its completion */
	ep_rlen = 60;
	calls = g_ncalls;
	push_rx(SWDP_RX_RID + 1u, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && ep_calls == 2 && g_e.eapol_rx == 2 && g_e.eapol_tx == 1 && g_e.eapol_txfail == 0 && g_e.eapol_slot == 0u && g_in_n == before && g_e.eapol_last_ok == 1u);       /* review 0282 F6: the re-join counts its M4 as sent from this */
	CHECK(g_ncalls == calls + 3u);
	for (k = calls, i = 0; k < g_ncalls; k++) {
		if (g_calls[k].st[0].kind == SWDP_SK_PAYLOAD) {
			CHECK(g_calls[k].st[0].len == 46u && memcmp(g_calls[k].pay, ep_reply + 14, 46) == 0);       /* the reply's bytes after the Ethernet header */
			CHECK(memcmp(g_calls[k].item + 8, ep_reply, 12) == 0);       /* and its addresses in the item (swdp_build_txpost) */
			i++;
		}
	}
	CHECK(i == 1u && en0_tx_busy(&g_e, g_e.eapol_slot) == 1 && g_e.tx_frames == 0u);       /* not counted as a stack frame */
	push_tx(SWDP_TX_RID + g_e.eapol_slot, 0);
	g_e.flow.ring.rd = g_e.flow.ring.wr;
	CHECK(en0_poll(&g_e) == 1 && en0_tx_busy(&g_e, g_e.eapol_slot) == 0);       /* the completion frees it */
	/* a second reply takes the next flow slot (= tx buffer): the slot is the one en0_tx_busy answers for */
	push_rx(SWDP_RX_RID + 7u, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_e.eapol_tx == 2 && g_e.eapol_slot == 1u && en0_tx_busy(&g_e, 1u) == 1 && en0_tx_busy(&g_e, 0u) == 0);
	push_tx(SWDP_TX_RID + 1u, 0);
	g_e.flow.ring.rd = g_e.flow.ring.wr;
	CHECK(en0_poll(&g_e) == 1 && en0_tx_busy(&g_e, 1u) == 0);
	{       /* no free tx buffer at the next flow slot: -1, nothing posted */
		const uint32_t w = g_e.flow.ring.wr % SWDP_NSLOT, c0 = g_ncalls;

		g_e.txb.used[w] = 1u;
		CHECK(en0_tx_frame(&g_e, ep_reply, 60u, &i) == -1 && g_ncalls == c0);
		g_e.txb.used[w] = 0u;
	}
	/* a hook that says "not now": dropped with the counted GAP */
	ep_ret = -1;
	push_rx(SWDP_RX_RID + 2u, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_e.eapol_drop == 65u && g_e.eapol_rx == 3 && g_in_n == before && ep_calls == 4);
	/* a reply that cannot be posted (flow ring down): counted, one GAP, the frame is consumed all the same */
	ep_ret = 0;
	g_e.flow.st = SWDP_FLOW_FAILED;
	push_rx(SWDP_RX_RID + 3u, f, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_e.eapol_rx == 4 && g_e.eapol_tx == 2 && g_e.eapol_txfail == 1 && count_of("EN6 EAPOL reply not posted (the AP will retry), code 5") == 1 && g_e.eapol_last_ok == 0u);
	g_e.flow.st = SWDP_FLOW_OPEN;
	CHECK(en0_tx_frame(&g_e, ep_reply, 13u, &i) == -4 && en0_tx_frame(&g_e, ep_reply, SWDP_ETH_MAXFRAME + 1u, &i) == -4);
	/* a frame shorter than the Ethernet header never reaches the hook, even when the two bytes behind it look like the EAPOL ethertype (the 14-byte length check of en0_rx_deliver) */
	{
		const unsigned c0 = ep_calls;
		const uint32_t d0 = g_e.rx_drop;

		memset(ip, 0, sizeof ip);
		ip[12] = 0x88; ip[13] = 0x8e;
		push_rx(SWDP_RX_RID + 8u, ip, 13, 4);
		CHECK(en0_poll(&g_e) == 1 && ep_calls == c0 && g_e.rx_drop == d0 + 1u && g_e.eapol_drop == 65u);
		ep_calls = c0;
	}
	/* other frames are delivered as before with the hook installed (ARP, IPv4, a 0x888E-looking length-field frame is not one) */
	memset(ip, 0, sizeof ip);
	frame_fill(ip, 60, 3);
	push_rx(SWDP_RX_RID + 4u, ip, 60, 4);
	g_expect_len = 60;
	before = g_in_n;
	CHECK(en0_poll(&g_e) == 1 && g_in_n == before + 1u && ep_calls == 5);
	ip[12] = 0x88; ip[13] = 0x8f;       /* one off */
	push_rx(SWDP_RX_RID + 5u, ip, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_in_n == before + 2u && ep_calls == 5);
	ip[12] = 0x80; ip[13] = 0x8e;
	push_rx(SWDP_RX_RID + 6u, ip, 60, 4);
	CHECK(en0_poll(&g_e) == 1 && g_in_n == before + 3u && ep_calls == 5);
	(void)rid;
	CHECK(sm_live == 0);
}


/* ------------------------------------------------------------------ patch 0357: the rx pool and its batched refill */
static uint32_t rxplans(uint32_t from)      /* ring-1 post plans among calls [from, g_ncalls) */
{
	uint32_t k, n = 0;
	for (k = from; k < g_ncalls; k++)
		if (g_calls[k].st[0].kind == SWDP_SK_ITEM && g_calls[k].st[0].ring == SWDP_RING_RXPOST) n++;
	return n;
}
static uint32_t dbs_of(const struct call *c, uint32_t kind)
{
	uint32_t i, n = 0;
	for (i = 0; i < c->n; i++) if (c->st[i].kind == kind) n++;
	return n;
}
static void pool_setup(uint32_t pool, uint32_t depth1)
{
	setup(); g_ifp = NULL;
	g_e.env.data.len = 0x100000;
	g_e.env.rx_slots = pool;
	g_e.env.rxbuf.len = (uint64_t)(pool ? pool : SWDP_NSLOT) * 2048u;
	g_e.env.depth1 = depth1;
	g_e.env.ring1.len = (uint64_t)depth1 * 32u;
	swdp_ring_init(&g_e.rxpost, depth1);
	CHECK(en0_attach(&g_e, MAC) == 0);
}
static void pool_complete(uint32_t first, uint32_t n)       /* n completions of buffers first.. , delivered in polls of at most 16 */
{
	uint8_t f[128];
	uint32_t i;
	for (i = 0; i < n; i++) { frame_fill(f, 60, (uint8_t)(i + 1)); push_rx(SWDP_RX_RID + first + i, f, 60, 4); }
	g_expect_len = 60;
}
static void test_rx_pool(void)
{
	uint32_t i, k, c0, c1;
	/* A: a pool of 64: the first fill is two plans of 32 items, one index and ONE doorbell each; items in consecutive slots, distinct request ids */
	pool_setup(64, 2048);
	en0_rx_fill(&g_e);
	CHECK(g_ncalls == 2 && g_e.rxb.inflight == 64 && g_e.rx_batches == 2 && g_e.rx_batch_max == 32 && g_e.rx_batch_bufs == 64 && g_e.rx_posted_peak == 64);
	for (k = 0; k < 2; k++) {
		const struct call *c = &g_calls[k];
		CHECK(c->n == 34 && c->ritem == 32 && dbs_of(c, SWDP_SK_DB) == 1 && dbs_of(c, SWDP_SK_WRIDX) == 1 && dbs_of(c, SWDP_SK_ITEM) == 32);
		CHECK(c->st[32].kind == SWDP_SK_WRIDX && c->st[32].value == 32u * (k + 1u) && c->st[33].kind == SWDP_SK_DB && c->st[33].value == swdp_db_wr(SWDP_RING_RXPOST, 32u * (k + 1u)));
		for (i = 0; i < 32; i++) CHECK(c->st[i].slot == 32u * k + i && swdp_g32(c->rid[i]) == SWDP_RX_RID + 32u * k + i);       /* slot n posts buffer n on a fresh book */
	}
	/* 10 buffers back: 54 posted, 10 free is below the threshold min(32, 64/4) = 16: nothing is posted */
	c0 = g_ncalls;
	pool_complete(40, 10);                                              /* buffers 40..49: above SWDP_NSLOT, they must be accepted as completions of the 64-buffer pool */
	CHECK(en0_poll(&g_e) == 10 && g_e.rxb.inflight == 54 && rxplans(c0) == 0 && g_e.rx_batches == 2);
	/* 6 more: 16 free = the threshold: ONE batch of 16 items, one doorbell, the pool is whole again; the trough was 48 */
	c0 = g_ncalls;
	pool_complete(50, 6);
	CHECK(en0_poll(&g_e) == 6 && g_e.rxb.inflight == 64 && rxplans(c0) == 1 && g_e.rx_batches == 3 && g_e.rx_batch_max == 32 && g_e.rx_posted_min == 48u + 1u);
	for (k = c0; k < g_ncalls; k++)
		if (g_calls[k].st[0].kind == SWDP_SK_ITEM) CHECK(g_calls[k].n == 18 && g_calls[k].ritem == 16 && dbs_of(&g_calls[k], SWDP_SK_DB) == 1 && g_calls[k].st[16].value == 80u);
	/* B: the pool 255 on the 2048-item ring: 8 plans (7 x 32 + 31), no plan above 32 items, every request id once */
	pool_setup(255, 2048);
	en0_rx_fill(&g_e);
	CHECK(g_ncalls == 8 && g_e.rxb.inflight == 255 && g_e.rx_batches == 8 && g_e.rx_batch_max == 32 && g_e.rx_batch_bufs == 255 && g_calls[7].ritem == 31);
	{
		uint8_t seen[SWDP_RX_MAX] = { 0 };
		for (k = 0; k < 8; k++) {
			CHECK(g_calls[k].ritem <= SWDP_RXB_MAX && dbs_of(&g_calls[k], SWDP_SK_DB) == 1);
			for (i = 0; i < g_calls[k].ritem; i++) { uint32_t r = swdp_g32(g_calls[k].rid[i]) - SWDP_RX_RID; CHECK(r < SWDP_RX_MAX && !seen[r]); seen[r] = 1; }
		}
	}
	/* C: never more than the ring holds: ring of 20 items (19 usable), pool 64 forced: 19 posted, the rest is a counted GAP (1st, 2nd, 4th time), never a plan past the ring */
	pool_setup(64, 20);
	en0_rx_fill(&g_e);
	CHECK(g_e.rxb.inflight == 19 && g_e.rx_refill_fail == 1 && count_of("EN7 rx refill due but not done") == 1);
	for (k = 0; k < g_ncalls; k++) for (i = 0; i < g_calls[k].n; i++) if (g_calls[k].st[i].kind == SWDP_SK_ITEM || g_calls[k].st[i].kind == SWDP_SK_WRIDX) CHECK(g_calls[k].st[i].slot < 20 || g_calls[k].st[i].value < 20);
	en0_rx_refill(&g_e, 0u); en0_rx_refill(&g_e, 0u);
	CHECK(g_e.rxb.inflight == 19 && g_e.rx_refill_fail == 3 && count_of("EN7 rx refill due but not done") == 2);
	/* D: a batch stops at the ring end: ring of 50 items, pool 40 (threshold 10): after 20 completions the refill is 10 items to slot 49 (index 0), then 10 more from slot 0 (index 10) */
	pool_setup(40, 50);
	en0_rx_fill(&g_e);
	CHECK(g_e.rxb.inflight == 40 && g_e.rxpost.wr == 40 && g_ncalls == 2);
	g_e.rxpost.rd = 40;                                   /* the dongle took every posted item */
	c0 = g_ncalls;
	pool_complete(0, 16);
	CHECK(en0_poll(&g_e) == 16 && g_e.rxpost.wr == 0 && g_e.rxb.inflight == 40 - 16 + 10);
	pool_complete(16, 4);
	CHECK(en0_poll(&g_e) == 4 && g_e.rxb.inflight == 40);
	for (k = c0, c1 = 0; k < g_ncalls; k++)
		if (g_calls[k].st[0].kind == SWDP_SK_ITEM) {
			const struct call *c = &g_calls[k];
			if (c1 == 0) CHECK(c->ritem == 10 && c->st[0].slot == 40 && c->st[9].slot == 49 && c->st[10].value == 0u && c->st[11].value == swdp_db_wr(SWDP_RING_RXPOST, 0));
			if (c1 == 1) CHECK(c->st[0].slot == 0 && c->st[10].value == 10u);
			c1++;
		}
	CHECK(c1 == 2 && g_e.rxpost.wr == 10);
	/* E: a refused refill plan gives every buffer back and stores nothing; the next refill works */
	pool_setup(64, 2048);
	en0_rx_fill(&g_e);
	for (i = 0; i < 16; i++) swdp_book_done(&g_e.rxb, SWDP_RX_RID + i, NULL);       /* 16 buffers back in the book = the threshold min(32, 64/4) */
	g_exec_fail = 1; c0 = g_ncalls;
	en0_rx_refill(&g_e, 0u);
	CHECK(g_e.rxb.inflight == 48 && g_ncalls == c0 && g_e.rx_refill_fail == 1 && g_e.rxpost.wr == 64u && count_of("EN7 rx refill due but not done") == 1);
	g_exec_fail = 0;
	en0_rx_refill(&g_e, 0u);
	CHECK(g_e.rxb.inflight == 64 && g_e.rxpost.wr == 80u && rxplans(c0) == 1 && g_calls[c0].ritem == 16);
	/* F: a pool of 16 (sweetwlanrxpost=16): the same size as before, refilled as soon as a buffer is free */
	pool_setup(0, 2048);
	en0_rx_fill(&g_e);
	CHECK(g_e.rxb.inflight == 16 && g_ncalls == 16 && g_calls[0].ritem == 1 && g_calls[15].ritem == 1 && g_calls[0].n == 3u);       /* review 0357 F2: a pool of 16 = 16 plans of ONE item (the old shape) */
	c0 = g_ncalls; pool_complete(0, 1);
	CHECK(en0_poll(&g_e) == 1 && g_e.rxb.inflight == 16 && rxplans(c0) == 1);
	/* H: en0_rx_batch on its own: at most SWDP_RXB_MAX items whatever it is asked, and never past the ring end */
	pool_setup(255, 2048); swdp_book_set_cap(&g_e.rxb, 255);
	CHECK(en0_rx_batch(&g_e, 50) == 32 && g_e.rxpost.wr == 32 && g_calls[0].ritem == 32 && g_e.rxb.inflight == 32);
	pool_setup(255, 2048); swdp_book_set_cap(&g_e.rxb, 255);
	g_e.rxpost.wr = 2040; g_e.rxpost.rd = 2000; g_e.env.wr1 = 2040;
	CHECK(en0_rx_batch(&g_e, 20) == 8 && g_e.rxpost.wr == 0 && g_calls[0].st[7].slot == 2047 && g_calls[0].st[8].value == 0u);
	/* I: a batch asked for more than the ring has room for ends at the room: the buffer that found no slot goes back to the book */
	pool_setup(64, 20); swdp_book_set_cap(&g_e.rxb, 64);
	CHECK(en0_rx_batch(&g_e, 25) == 19 && g_e.rxb.inflight == 19 && g_e.rxpost.wr == 19);
	/* J: after a refused consume commit the data path is stopped: the poll hands nothing back to the dongle (no refill plan, no refill GAP) */
	pool_setup(64, 2048);
	en0_rx_fill(&g_e);
	pool_complete(0, 16);
	c0 = g_ncalls; g_exec_fail = 1;
	CHECK(en0_poll(&g_e) == 16 && g_e.cons_fail == 1u && g_ncalls == c0 && g_e.rx_refill_fail == 0u && count_of("EN7 rx refill due but not done") == 0 && g_e.rxb.inflight == 48);
	g_exec_fail = 0;
	/* G: a book that cannot hold the pool, and a request id above the pool, are refused loudly */
	pool_setup(64, 2048);
	CHECK(swdp_book_set_cap(&g_e.rxb, 64) == 0 && swdp_book_set_cap(&g_e.rxb, 16) == -1 && swdp_book_set_cap(&g_e.rxb, 256) == -1 && swdp_book_take_at(&g_e.rxb, 64, 1) == 0 && swdp_book_take_at(&g_e.rxb, 63, 1) == SWDP_RX_RID + 63u);
}

int main(void)
{
	test_attach(); test_rx(); test_tx(); test_poll_edges(); test_commit_refused(); test_no_interrupt(); test_link_and_address(); test_boot_cfg(); test_ioctl(); test_eapol(); test_rx_pool();
	if (g_stub_fail) { printf("%d stub check(s) failed\n", g_stub_fail); fails += g_stub_fail; }
	if (sm_live != 0) { printf("mbuf leak: %d buffers live\n", sm_live); fails++; }
	if (fails) { printf("RESULT: FAIL wlan_en0_host_test fails=%d\n", fails); return 1; }
	printf("RESULT: PASS wlan_en0_host_test\n");
	return 0;
}
