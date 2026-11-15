/* wlan_en0_live_host_test.c - host soak of wlan_en0.h over the LIVE engine state (wlan_en0_bus.h), sprint 2026-11-08 (W-IOSPING prerequisite).
 *
 * The REAL L25/L26 run (swl25_run of sweet_wlan_l25_hs.h on the scripted dongle of wlan_l25_host_test.c, included with main renamed) brings the link up: flow create, 16 rx posts,
 * 4-way, key installs, ARP + echo. The state it leaves (struct swg_wx x, struct swl25h swl25_H) is then handed to en0 over the bus of wlan_en0_bus.h (en0_live_up) and SOAKED:
 * ARP and ICMP echo frames of many sizes out through en0_output, the replies and unsolicited frames in through en0_poll, until ring 1, the flow ring and the D2H rings
 * 3 and 4 have wrapped several times. A second scripted dongle (SD below, armed through the L25_WR_HOOK of the included test) takes over from the first one at the hand-over: it
 * checks every H2D item the host writes (type, epoch, request id, buffer address, length), every read-index store and every doorbell, writes its own read indexes, completes
 * every tx post with a status, answers ARP and echo requests through the rx buffers the host posted (queueing them when none is free), and compares every frame it receives
 * and every frame en0 hands to ifnet_input byte for byte with what was sent. Every divergence is recorded (first one printed); every GAP line of the log is counted.
 * Layout facts used by the dongle model are cited at the use: sweet_wlan_data.h swdp_build_rxpost 194-205, swdp_build_txpost 208-221, swdp_parse_rxcmpl 274-289, d2h_item / tx_status
 * in wlan_l25_host_test.c, ring depths = swd_rings (xnu patch 0167:183-190).
 * Build (host only): gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE, plain and -fsanitize=address,undefined; see the sprint report for the exact command (the test and wlan_l25_host_test.c
 * are copied next to the g2 header under test). Without SWG_DP_SHADOW_ONES_OK in sweet_wlan_g2.h the soak cannot pass: the test then verifies the bus's ff_guard and says so. */
#include <errno.h>
#include <stdint.h>
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-variable"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
static void live_wr_hook(int sp, uint32_t a, uint32_t v);
#ifdef EN0_LIVE_RXN                     /* patch 0357: EN0_LIVE_RXN = the rx pool this run must end up with (16..255); EN0_LIVE_FW = what the scripted firmware advertises at shared +0x22 (default: the pool);
                                         * EN0_LIVE_ARG = boot-arg sweetwlanrxpost (default: absent); EN0_LIVE_BLKRX = rx buffers the DATA block holds (default 255) */
#ifndef EN0_LIVE_FW
#define EN0_LIVE_FW EN0_LIVE_RXN
#endif
#ifndef EN0_LIVE_BLKRX
#define EN0_LIVE_BLKRX 255u
#endif
#define DALEN (0xC000u + EN0_LIVE_BLKRX * 2048u)
#define L25_RXFW EN0_LIVE_FW
#ifdef EN0_LIVE_ARG
#define L25_RXARG EN0_LIVE_ARG
#endif
#define RXN EN0_LIVE_RXN
#define LJ_BLKRX EN0_LIVE_BLKRX
#else
#define RXN SWDP_NSLOT
#define LJ_BLKRX SWDP_NSLOT
#endif
#define L25_WR_HOOK live_wr_hook
#define main l25_main
#include "wlan_l25_host_test.c"
#undef main
#undef L25_WR_HOOK
#include "sweet_wlan_l25_wl.h"      /* sprint 2026-11-08: the store judge of the kernel glue */

typedef int errno_t;

/* ------------------------------------------------------------------ stub KPI (same names and semantics as wlan_en0_host_test.c; ifnet_input judges against the expected-frame queue) */
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

/* the first divergence of the run, and the expected-frame queues (tx: what en0 was asked to send; rx: what the dongle put on ring 4) */
static char div_msg[256];
static void diverge(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void diverge(const char *fmt, ...)
{
	va_list ap;

	if (div_msg[0] != '\0')
		return;
	va_start(ap, fmt);
	vsnprintf(div_msg, sizeof div_msg, fmt, ap);
	va_end(ap);
}
#define QN 1024u               /* expected-frame queue: >= 16 tx buffers + the interface queue SNDQ (512) + slack */
struct fq { uint8_t f[SWDP_ETH_MAXFRAME]; uint32_t len; };
static struct fq rxq[QN], txq[QN];
static uint32_t rxq_h, rxq_t, txq_h, txq_t;

static int sm_live;
static ifnet_t g_ifp;
static struct stub_ifnet g_ifnet;
static uint32_t g_in_n, g_in_bytes;
static errno_t mbuf_allocpacket(int how, size_t n, unsigned int *maxchunks, mbuf_t *out)
{
	size_t k = (n + SM_CAP - 1u) / SM_CAP, i;
	mbuf_t head = NULL, *tail = &head;
	(void)how; (void)maxchunks;
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
		diverge("stub: mbuf_setdata outside the buffer");
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
	uint8_t fr[SWDP_ETH_MAXFRAME + 64];
	size_t sum = 0, got = 0, i;
	const struct stub_mbuf *b;
	struct fq *q;

	if (m->rcvif != ifp) diverge("ifnet_input: rcvif is not en0");
	if ((uint8_t *)m->pkt_hdr != m->data - 14) diverge("ifnet_input: pkt_hdr != data-14");
	for (b = m; b != NULL; b = b->next) sum += b->len;
	if (sum != m->pkt_len || sum + 14u > SWDP_ETH_MAXFRAME) { diverge("ifnet_input: chain length %zu != pkthdr.len %zu", sum, m->pkt_len); mbuf_freem(m); return 0; }
	if (((uintptr_t)m->data & 3u) != 0) diverge("ifnet_input: IP header not 4-byte aligned");
	if (st->packets_in != 1u || st->bytes_in != sum + 14u) diverge("ifnet_input: stats not packets_in 1 / bytes_in frame");
	memcpy(fr, m->pkt_hdr, 14);
	for (b = m; b != NULL; b = b->next) { memcpy(fr + 14 + got, b->data, b->len); got += b->len; }
	if (rxq_h == rxq_t) {
		diverge("ifnet_input #%u: a frame nobody put on ring 4", g_in_n);
	} else {
		q = &rxq[rxq_h++ % QN];
		if (q->len != 14 + got) diverge("rx frame #%u: length %zu, expected %u", g_in_n, 14 + got, q->len);
		else for (i = 0; i < q->len; i++) if (fr[i] != q->f[i]) { diverge("rx frame #%u differs at byte %zu (len %u)", g_in_n, i, q->len); break; }
	}
	g_in_n++; g_in_bytes += (uint32_t)(14 + got);
	mbuf_freem(m);
	return 0;
}
/* Part A: DLIL's send queue of a start-model ifnet (a FIFO here, FQ-CoDel in the kernel), ifnet_dequeue, ifnet_start (runs en0_start at once, as DLIL's starter thread would) */
#define SNDQ 512u
static mbuf_t g_sndq[SNDQ];
static uint32_t g_sndq_h, g_sndq_n, g_sndq_drop;
static void (*g_start_fn)(ifnet_t);
static errno_t ifnet_dequeue(ifnet_t i, mbuf_t *m)
{
	(void)i;
	if (g_sndq_n == 0u) return 35;
	*m = g_sndq[g_sndq_h]; g_sndq_h = (g_sndq_h + 1u) % SNDQ; g_sndq_n--;
	return 0;
}
static void ifnet_start(ifnet_t i) { if (g_start_fn) g_start_fn(i); }
static void mbuf_freem(mbuf_t m);
static errno_t en0_output(ifnet_t i, mbuf_t m)             /* what dlil_output does for a TXSTART interface: enqueue (full = the stack's drop), then start */
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
		diverge("stub: Part A start model required: no LEGACY, a start callback, no output, sched model NORMAL");
	g_start_fn = p->start;
	if (p->flags & IFNET_INIT_NX_NOAUTO) g_ifnet.xflags |= IFXF_NX_NOAUTO;
	*out = g_ifp = &g_ifnet;
	return 0;
}
static errno_t ifnet_attach(ifnet_t i, const void *ll) { (void)ll; i->attached = 1; return 0; }
static errno_t ifnet_set_lladdr(ifnet_t i, const void *a, size_t n) { i->lladdr_after_attach = i->attached; i->lladdr_set++; memcpy(i->lladdr, a, n); return 0; }
static errno_t ifnet_set_flags(ifnet_t i, uint32_t nw, uint32_t mask) { i->flags = (i->flags & ~mask) | (nw & mask); return 0; }
static errno_t ifnet_set_addrlen(ifnet_t i, size_t n) { (void)i; if (n != 6) diverge("stub: addrlen"); return 0; }
static errno_t ifnet_set_hdrlen(ifnet_t i, size_t n) { (void)i; if (n != 14) diverge("stub: hdrlen"); return 0; }
static errno_t ifnet_set_mtu(ifnet_t i, uint32_t n) { (void)i; if (n != 1500) diverge("stub: mtu"); return 0; }
static errno_t ifnet_set_baudrate(ifnet_t i, uint64_t n) { (void)i; (void)n; return 0; }
static const char *ifnet_name(ifnet_t i) { return i->name; }
static uint32_t ifnet_unit(ifnet_t i) { return i->unit; }
static uint32_t ifnet_subfamily(ifnet_t i) { return i->subfamily; }
static errno_t ifnet_stat_increment_out(ifnet_t i, uint32_t p, uint32_t b, uint32_t e) { i->pkts_out += p; i->bytes_out += b; i->errs_out += e; return 0; }
static uint32_t g_ev_n, g_ev_code;
static errno_t ifnet_event(ifnet_t i, struct kern_event_msg *ev) { (void)i; g_ev_n++; g_ev_code = ev->event_code; return 0; }
static errno_t bpf_attach(ifnet_t i, uint32_t dlt, uint32_t hl, void *send, void *tap)
{
	if (dlt != DLT_EN10MB || hl != 14 || send != NULL || tap != NULL || !i->attached) diverge("stub: bpf_attach(DLT_EN10MB, 14, NULL, NULL) after attach");
	i->bpf_attached++;
	return 0;
}
static void bpf_tap_in(ifnet_t i, uint32_t dlt, mbuf_t m, void *h, size_t hl) { (void)m; (void)h; (void)hl; if (dlt != DLT_EN10MB) diverge("stub: tap dlt"); i->tap_in++; }
static void bpf_tap_out(ifnet_t i, uint32_t dlt, mbuf_t m, void *h, size_t hl) { (void)m; (void)h; (void)hl; if (dlt != DLT_EN10MB) diverge("stub: tap dlt"); i->tap_out++; }
static void ether_demux(void) {}
static void ether_add_proto(void) {}
static void ether_del_proto(void) {}
static void ether_check_multi(void) {}
static void ether_frameout_extended(void) {}
static const uint8_t etherbroadcastaddr[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };

#include "wlan_en0.h"
#include "wlan_en0_bus.h"

/* ------------------------------------------------------------------ GAP harvest: every GAP line of the log, by text, then the log is cleared */
struct gtab { char t[104]; unsigned n; };
static struct gtab gt[64];
static unsigned gt_n, gap_total, gap_unlisted;
static char gap_first[160];
static void gap_scan(struct gtab *tab, unsigned *ntab, unsigned *total, char *first, size_t firstn)
{
	const char *p = logbuf, *e;
	unsigned i;

	while (p != NULL && *p != '\0') {
		char ln[104];
		size_t n;

		e = strchr(p, '\n');
		n = e != NULL ? (size_t)(e - p) : strlen(p);
		if (memmem(p, n, "GAP", 3) != NULL && (memmem(p, n, "WIFI GAP", 8) != NULL || memmem(p, n, "WLAN GAP", 8) != NULL)) {
			snprintf(ln, sizeof ln, "%.*s", (int)(n < 103 ? n : 103), p);
			(*total)++;
			if (first[0] == '\0') snprintf(first, firstn, "%s", ln);
			for (i = 0; i < *ntab; i++) if (strcmp(tab[i].t, ln) == 0) break;
			if (i == *ntab && *ntab < 64u) { snprintf(tab[i].t, sizeof tab[i].t, "%s", ln); tab[i].n = 0; (*ntab)++; }
			if (i < *ntab) tab[i].n++; else gap_unlisted++;
		}
		p = e != NULL ? e + 1 : NULL;
	}
}
static void harvest(void)
{
	gap_scan(gt, &gt_n, &gap_total, gap_first, sizeof gap_first);
	loglen = 0u;
	logbuf[0] = '\0';
}

/* ------------------------------------------------------------------ the soak dongle SD (takes over from the dongle of wlan_l25_host_test.c at the hand-over) */
static struct {
	int on;
	uint32_t r1_seen, r1_abs, fl_seen, fl_abs, hrd3, hrd4;      /* items the dongle has read from ring 1 / the flow ring (slot, absolute); items the host has consumed from rings 3 / 4 (absolute) */
	uint8_t posted[SWDP_RX_MAX], txout[SWDP_NSLOT];
	uint32_t freeq[256], fq_h, fq_t;                              /* request ids of posted, unused rx buffers (FIFO) */
	uint32_t stat_rid[2048];                                     /* request id of the tx status at absolute ring-3 index mod 2048 */
	struct { uint8_t f[SWDP_ETH_MAXFRAME]; uint32_t len, off; } bl[128];
	uint32_t bl_h, bl_t, bl_max;                                 /* frames waiting for an rx buffer (FIFO) */
	unsigned tx_seen, tx_arp, tx_echo, tx_other, rx_deliv, bl_lost, db1, db5, db3, db4, db_other;
} SD;
#define RX_RID_BASE 0x52580000u     /* SWDP_RX_RID, sweet_wlan_data.h:291 */
#define TX_RID_BASE 0x54580000u     /* SWDP_TX_RID, sweet_wlan_data.h:292 */
#define DATA_RX_OFF 0xC000u         /* SWL25_RX_OFF, sweet_wlan_l25.h:26 */
#define DATA_TX_OFF 0x4000u         /* SWL25_TX_OFF, sweet_wlan_l25.h:25 */
#define BUFSZ       2048u

static uint8_t *dbytes(uint32_t off) { return (uint8_t *)dat + off; }

static int dg_try_rx(const uint8_t *fr, uint32_t len, uint32_t off)
{
	uint32_t rid, slot;
	struct fq *q;

	if (SD.fq_h == SD.fq_t || (D.n4 - SD.hrd4) >= swd_rings[4].items - 1u)
		return 0;
	rid = SD.freeq[SD.fq_h++ % 256u];
	slot = rid - RX_RID_BASE;
	memset(dbytes(DATA_RX_OFF + slot * BUFSZ), 0xA5, BUFSZ);            /* a wrong data_offset / data_len shows as 0xA5 bytes in the frame */
	memcpy(dbytes(DATA_RX_OFF + slot * BUFSZ + off), fr, len);
	d2h_item(RING4, 2u, 40u, swd_rings[4].items, &D.n4, 0x12u, rid, len << 16, off);   /* rx complete: data_len at +14, data_offset at +16 (swdp_parse_rxcmpl) */
	SD.posted[slot] = 0u;
	q = &rxq[rxq_t++ % QN];
	if (rxq_t - rxq_h > QN) diverge("test: rx expected-queue overflow");
	memcpy(q->f, fr, len); q->len = len;
	SD.rx_deliv++;
	return 1;
}
static void dg_flush(void)
{
	while (SD.bl_h != SD.bl_t) {
		const uint32_t i = SD.bl_h % 128u;

		if (!dg_try_rx(SD.bl[i].f, SD.bl[i].len, SD.bl[i].off))
			return;
		SD.bl_h++;
	}
}
static void dg_rx(const uint8_t *fr, uint32_t len, uint32_t off)
{
	if (SD.bl_h == SD.bl_t && dg_try_rx(fr, len, off))
		return;
	if (SD.bl_t - SD.bl_h >= 128u) { SD.bl_lost++; diverge("dongle: rx backlog full, frame lost"); return; }
	memcpy(SD.bl[SD.bl_t % 128u].f, fr, len);
	SD.bl[SD.bl_t % 128u].len = len; SD.bl[SD.bl_t % 128u].off = off;
	SD.bl_t++;
	if (SD.bl_t - SD.bl_h > SD.bl_max) SD.bl_max = SD.bl_t - SD.bl_h;
}
static uint32_t rx_off_pick(void)
{
	static const uint32_t pick[3] = { 0u, 2u, 14u };      /* 0 = the shared rx_dataoffset (x.rxoff is 0 here), explicit 2 and 14 */
	return pick[SD.rx_deliv % 3u];
}
static void dg_arp_reply(const uint8_t *rq)
{
	uint8_t f[42];

	memcpy(f, rq + 6, 6);
	memcpy(f + 6, V_AA, 6);
	f[12] = 0x08; f[13] = 0x06; f[14] = 0; f[15] = 1; f[16] = 0x08; f[17] = 0; f[18] = 6; f[19] = 4; f[20] = 0; f[21] = 2;
	memcpy(f + 22, V_AA, 6);                                    /* sha = the gateway */
	memcpy(f + 28, rq + 38, 4);                                 /* spa = the asked IP */
	memcpy(f + 32, rq + 22, 6);                                 /* tha = the asker */
	memcpy(f + 38, rq + 28, 4);
	dg_rx(f, 42u, rx_off_pick());
}
static void dg_echo_reply(const uint8_t *rq, uint32_t len)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	const uint8_t *ip = rq + 14;
	const uint32_t tot = (uint32_t)ip[2] << 8 | ip[3];
	uint16_t c;

	memcpy(f, rq, len);
	memcpy(f, rq + 6, 6);
	memcpy(f + 6, rq, 6);
	memcpy(f + 14 + 12, ip + 16, 4);
	memcpy(f + 14 + 16, ip + 12, 4);
	f[14 + 8] = 64u; f[14 + 10] = f[14 + 11] = 0u;
	c = isum(f + 14, 20u); f[24] = (uint8_t)(c >> 8); f[25] = (uint8_t)c;
	f[34] = 0u; f[36] = f[37] = 0u;
	c = isum(f + 34, tot - 20u); f[36] = (uint8_t)(c >> 8); f[37] = (uint8_t)c;
	dg_rx(f, len, rx_off_pick());
}
static void dg_react(const uint8_t *f, uint32_t len)
{
	if (len >= 42u && f[12] == 0x08 && f[13] == 0x06 && f[21] == 1u && memcmp(f + 38, CGW, 4) == 0) {
		SD.tx_arp++;
		dg_arp_reply(f);
	} else if (len >= 42u && f[12] == 0x08 && f[13] == 0x00 && f[23] == 1u && f[34] == 8u) {
		SD.tx_echo++;
		dg_echo_reply(f, len);
	} else {
		SD.tx_other++;
	}
}
/* a write-index doorbell of ring 1 (rx posts): items [r1_seen, wr). Item layout: swdp_build_rxpost (type 0x11 at +0, epoch +3, request id +4, data_buf_len +10, data addr +24) */
static void dg_rxpost(uint32_t wr)
{
	const uint32_t depth = swd_rings[1].items;
	uint32_t cnt = (wr + depth - SD.r1_seen) % depth;

	SD.db1++;
	if (wr >= depth || cnt == 0u) { diverge("ring-1 doorbell value %u with %u items seen", wr, SD.r1_seen); return; }
	while (cnt--) {
		uint8_t it[32];
		uint32_t rid, slot;
		uint64_t pa;

		memcpy(it, &g1[(RING1 - G1PA) / 4u + SD.r1_seen * 8u], 32);
		rid = swdp_g32(it + 4); slot = rid - RX_RID_BASE;
		pa = (uint64_t)swdp_g32(it + 24) | (uint64_t)swdp_g32(it + 28) << 32;
		if (it[0] != 0x11u || it[3] != (uint8_t)((254u + SD.r1_abs) % 253u) || slot >= RXN || swdp_g16(it + 10) != BUFSZ || pa != DAPA + DATA_RX_OFF + (uint64_t)slot * BUFSZ)
			diverge("ring-1 item %u (abs %u): type %02x epoch %u (want %u) rid %08x len %u pa %llx", SD.r1_seen, SD.r1_abs, it[0], it[3], (254u + SD.r1_abs) % 253u, rid, swdp_g16(it + 10), (unsigned long long)pa);
		else if (SD.posted[slot])
			diverge("rx buffer %u posted twice (ring-1 item abs %u)", slot, SD.r1_abs);
		else {
			SD.posted[slot] = 1u;
			SD.freeq[SD.fq_t++ % 256u] = rid;
		}
		SD.r1_seen = (SD.r1_seen + 1u) % depth;
		SD.r1_abs++;
	}
	g1[(H2DR - G1PA) / 4u + 1u] = SD.r1_seen;                    /* the dongle's read index of ring 1 (H2D-RD slot 1) */
	dg_flush();
}
/* a write-index doorbell of the flow ring (tx posts): items [fl_seen, wr). Item layout: swdp_build_txpost (type 0x0F, epoch byte 0 (DP4), rid +4, header 14 B at +8, seg_cnt +23, data addr +32, data_len +42) */
static void dg_flow(uint32_t wr)
{
	const uint32_t depth = SWL25_FLOW_DEPTH;
	uint32_t cnt = (wr + depth - SD.fl_seen) % depth;

	SD.db5++;
	if (wr >= depth || cnt == 0u) { diverge("flow doorbell value %u with %u items seen", wr, SD.fl_seen); return; }
	while (cnt--) {
		const uint8_t *it = dbytes(SD.fl_seen * 48u);
		static uint8_t f[SWDP_ETH_MAXFRAME];
		uint32_t rid = swdp_g32(it + 4), slot = rid - TX_RID_BASE, dl = swdp_g16(it + 42), i;
		uint64_t pa = (uint64_t)swdp_g32(it + 32) | (uint64_t)swdp_g32(it + 36) << 32;

		if (it[0] != 0x0Fu || it[3] != 0u || it[23] != 1u || slot >= SWDP_NSLOT || dl == 0u || dl > BUFSZ || pa != DAPA + DATA_TX_OFF + (uint64_t)slot * BUFSZ) {
			diverge("flow item %u (abs %u): type %02x epoch %u seg %u rid %08x len %u pa %llx", SD.fl_seen, SD.fl_abs, it[0], it[3], it[23], rid, dl, (unsigned long long)pa);
		} else if (SD.txout[slot]) {
			diverge("tx buffer %u reused before its status was consumed (flow item abs %u)", slot, SD.fl_abs);
		} else if ((D.n3 - SD.hrd3) >= swd_rings[3].items - 1u) {
			diverge("ring 3 would overrun (dongle model)");
		} else {
			SD.txout[slot] = 1u;
			memcpy(f, it + 8, 14);
			memcpy(f + 14, dbytes(DATA_TX_OFF + slot * BUFSZ), dl);
			if (txq_h == txq_t) {
				diverge("tx frame #%u on the flow ring that nobody sent", SD.tx_seen);
			} else {
				struct fq *q = &txq[txq_h++ % QN];

				if (q->len != 14u + dl) diverge("tx frame #%u: length %u on the wire, sent %u", SD.tx_seen, 14u + dl, q->len);
				else for (i = 0; i < q->len; i++) if (f[i] != q->f[i]) { diverge("tx frame #%u differs at byte %u (len %u)", SD.tx_seen, i, q->len); break; }
			}
			SD.stat_rid[D.n3 % 2048u] = rid;
			tx_status(rid, 0u);                                     /* the status goes on ring 3 at absolute index D.n3 (before the call advances it) */
			SD.tx_seen++;
			dg_react(f, 14u + dl);
		}
		SD.fl_seen = (SD.fl_seen + 1u) % depth;
		SD.fl_abs++;
	}
	g1[(H2DR - G1PA) / 4u + 2u] = SD.fl_seen;                    /* the dongle's read index of the flow ring (H2D-RD slot 2 = flow id) */
}
/* a read-index doorbell of ring 3 or 4: the host consumed 1..16 items (Part A: one commit per poll pass, at most the items this dongle wrote); its RD slot in the G1 block must carry the same value */
static unsigned dg_rd_batches, dg_rd_max;
static void dg_rd(uint32_t ring, uint32_t v)
{
	const uint32_t depth = swd_rings[ring].items, made = ring == 3u ? D.n3 : D.n4;
	uint32_t *hrd = ring == 3u ? &SD.hrd3 : &SD.hrd4;
	const uint32_t step = (v + depth - *hrd % depth) % depth;
	uint32_t k;

	if (ring == 3u) SD.db3++; else SD.db4++;
	if (step == 0u || step > 16u || step > made - *hrd)
		diverge("ring-%u read doorbell %u: a step of %u from %u (made %u; 1..16 and not past the items written)", ring, v, step, *hrd % depth, made);
	if ((g1[(D2HR - G1PA) / 4u + ring - 2u] & 0xFFFFu) != v)
		diverge("ring-%u RD slot %u != doorbell %u", ring, g1[(D2HR - G1PA) / 4u + ring - 2u] & 0xFFFFu, v);
	dg_rd_batches++;
	if (step > dg_rd_max) dg_rd_max = step;
	for (k = 0; k < step; k++) {
		if (ring == 3u)
			SD.txout[SD.stat_rid[*hrd % 2048u] - TX_RID_BASE] = 0u;     /* its request id may be posted again from now on */
		(*hrd)++;
	}
	dg_flush();
}
/* sprint 2026-11-08 (run wlan-iosping-b: the kernel's L25 store judge refused en0's first tx, buffer 0 at flow slot 14, which this soak never judged): every store of the main run (the L25
 * bring-up AND the en0 soak) goes through the same judge as the kernel glue (sweet_wlan_l25_wl.h swl25w_ok / swl25w_done, layout as wlan_l25_wl_host_test.c wl_cfg); a refusal fails the test */
static struct swl25w LJ;
static unsigned lj_on, lj_n, lj_ref;
static char lj_first[160];
static int lj_rd(void *ctx, int space, uint32_t off, uint32_t *v)
{
	const uint32_t *m = mem(space == SWL25W_SP_HOST ? SWG_SP_HOST : SWG_SP_DATA, off);

	(void)ctx;
	if (m == NULL)
		return -1;
	*v = *m;
	return 0;
}
static void lj_cfg(struct swl25w *w)
{
	memset(w, 0, sizeof *w);
	w->g1_pa = G1PA; w->data_pa = DAPA; w->g1_len = G1LEN; w->data_len = DALEN;
	w->ioctbuf = 0x60000u; w->respbuf = 0x70000u; w->evbuf = 0x80000u;
	w->h2d_w = (uint32_t)(H2DW - G1PA); w->d2h_w = (uint32_t)(D2HW - G1PA); w->d2h_r = (uint32_t)(D2HR - G1PA); w->h2d_r = (uint32_t)(H2DR - G1PA);
	w->ring[0] = (uint32_t)(RING0 - G1PA); w->ring[1] = (uint32_t)(RING1 - G1PA); w->ring[2] = (uint32_t)(RING2 - G1PA);
	w->ring[3] = (uint32_t)(RING3 - G1PA); w->ring[4] = (uint32_t)(RING4 - G1PA);
	w->depth[0] = SWD_R0_ITEMS; w->depth[1] = SWD_R1_ITEMS; w->depth[2] = SWD_R2_ITEMS; w->depth[3] = SWD_R3_ITEMS; w->depth[4] = SWD_R4_ITEMS;
	w->rx_n = LJ_BLKRX;  /* patch 0357: what the glue's layout computes from the block it allocated (16 buffers by default, EN0_LIVE_BLKRX with EN0_LIVE_RXN) */
	w->flowid = 2u;
	w->rd = lj_rd;
}
static void lj_judge(int sp, uint32_t a, uint32_t v)
{
	const char *why = "";
	uint32_t old = 0u;
	const uint32_t *m;
	const int space = sp == SWG_SP_PCIE ? SWL25W_SP_PCIE : sp == SWG_SP_HOST ? SWL25W_SP_HOST : sp == SWG_SP_DATA ? SWL25W_SP_DATA : 0;
	int cls;

	if (J.nw == 1u)
		lj_cfg(&LJ);
	m = space == SWL25W_SP_HOST || space == SWL25W_SP_DATA ? mem(sp, a) : NULL;
	if (m != NULL)
		old = *m;
	LJ.flow_open = J.answer == 1 && J.status == 0 && J.ndb >= 1u ? 1u : LJ.flow_open;
	cls = swl25w_ok(&LJ, space, a, v, old, &why);
	lj_n++;
	if (cls < 0) {
		if (lj_ref++ == 0u)
			snprintf(lj_first, sizeof lj_first, "store %u sp %d off %#x: %s", lj_n, sp, a, why);
		return;
	}
	swl25w_done(&LJ, space, a, v, old, cls);
}

static void live_wr_hook(int sp, uint32_t a, uint32_t v)
{
	const uint32_t ring = v >> 16 & 0xFFu, top = v >> 24;

	if (lj_on)
		lj_judge(sp, a, v);
	(void)a;
	if (!SD.on || sp != SWG_SP_PCIE)
		return;
	if (top == 0xFFu && ring == 1u) dg_rxpost(v & 0xFFFFu);
	else if (top == 0xFFu && ring == 5u) dg_flow(v & 0xFFFFu);
	else if (top == 0xDDu && (ring == 3u || ring == 4u)) dg_rd(ring, v & 0xFFFFu);
	else { SD.db_other++; diverge("unexpected doorbell %08x on the en0 data path", v); }
}

/* ------------------------------------------------------------------ frames and the driver */
static struct en0 EN;
static struct en0_live BU;
static unsigned apply_n;
static int apply_stub(ifnet_t ifp, const struct en0_cfg *c) { (void)ifp; apply_n++; return c->ip[0] == 192 && c->gw[3] == 1 ? 0 : -1; }

static uint32_t fr_arp(uint8_t *f)                              /* broadcast who-has CGW from the station: the item's header holds an all-ones word */
{
	static const uint8_t bc[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };

	memcpy(f, bc, 6); memcpy(f + 6, V_SPA, 6);
	f[12] = 0x08; f[13] = 0x06; f[14] = 0; f[15] = 1; f[16] = 0x08; f[17] = 0; f[18] = 6; f[19] = 4; f[20] = 0; f[21] = 1;
	memcpy(f + 22, V_SPA, 6); memcpy(f + 28, CIP, 4); memset(f + 32, 0, 6); memcpy(f + 38, CGW, 4);
	return 42u;
}
static uint32_t fr_echo(uint8_t *f, uint32_t i, uint32_t ndata)  /* ICMP echo request to the gateway, ndata data bytes (0..1472) */
{
	const uint32_t tot = 20u + 8u + ndata;
	uint16_t c;
	uint32_t k;

	memcpy(f, V_AA, 6); memcpy(f + 6, V_SPA, 6); f[12] = 0x08; f[13] = 0x00;
	f[14] = 0x45; f[15] = 0; f[16] = (uint8_t)(tot >> 8); f[17] = (uint8_t)tot; f[18] = (uint8_t)(i >> 8); f[19] = (uint8_t)i; f[20] = 0x40; f[21] = 0;
	f[22] = 64; f[23] = 1; f[24] = f[25] = 0; memcpy(f + 26, CIP, 4); memcpy(f + 30, CGW, 4);
	c = isum(f + 14, 20u); f[24] = (uint8_t)(c >> 8); f[25] = (uint8_t)c;
	f[34] = 8; f[35] = 0; f[36] = f[37] = 0; f[38] = 0x50; f[39] = 0x58; f[40] = (uint8_t)(i >> 8); f[41] = (uint8_t)i;
	for (k = 0; k < ndata; k++) f[42 + k] = (uint8_t)(i + k * 7u);
	if (i % 11u == 0u && ndata >= 4u) memset(f + 42, 0xFF, 4);       /* an all-ones payload word (4-aligned from the payload start): the buffer's next use sees it as the old word */
	c = isum(f + 34, 8u + ndata); f[36] = (uint8_t)(c >> 8); f[37] = (uint8_t)c;
	return 14u + tot;
}
static uint32_t fr_noise(uint8_t *f, uint32_t i)                 /* an unsolicited broadcast ARP who-has OUR address from the gateway side */
{
	static const uint8_t bc[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	uint32_t k;

	memcpy(f, bc, 6); memcpy(f + 6, V_AA, 6);
	f[12] = 0x08; f[13] = 0x06; f[14] = 0; f[15] = 1; f[16] = 0x08; f[17] = 0; f[18] = 6; f[19] = 4; f[20] = 0; f[21] = 1;
	memcpy(f + 22, V_AA, 6); memcpy(f + 28, CGW, 4); memset(f + 32, 0, 6); memcpy(f + 38, CIP, 4);
	for (k = 42; k < 60; k++) f[k] = (uint8_t)(i + k);          /* Ethernet padding with a pattern, so a short read shows */
	return 60u;
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
/* one frame out through en0_output; the expected-frame queue gets it first and loses it again when en0 counted a drop. 1 = accepted */
static int send_one(const uint8_t *f, uint32_t len)
{
	const uint32_t drops = EN.tx_drop, sdrops = g_sndq_drop;
	struct fq *q = &txq[txq_t++ % QN];
	mbuf_t m = mk_tx_mbuf(f, len);

	memcpy(q->f, f, len); q->len = len;
	en0_output(g_ifp, m);
	if (EN.tx_drop != drops || g_sndq_drop != sdrops) { txq_t--; return 0; }
	return 1;
}
static void pump(void)
{
	unsigned guard = 0;

	while (en0_live_poll(&EN, &BU) != 0u && ++guard < 200u) { }
}

static unsigned soak_tx_acc, soak_tx_drop_unexp;
#ifdef EN0_LIVE_BLK
/* Part A (sprint-wifi 2026-11-09): the soak again over a bus WITH rd_blk / wr_blk. Each block call is made of the per-word host bus calls (b_rd / b_wr, so every word still meets this test's
 * L25 judge hook and the scripted dongle), which tests the ENGINE side of the batch (sweet_wlan_g2.h swg_d4d_st_blk: row kinds, old words vs shadows, read-back; g2d.h: the run and shadow
 * mapping; wlan_en0_bus.h: rx buffer and D2H item reads). The glue's batched judge is tested against the per-word judge in wlan_l25_wl_host_test.c (blk-eq). */
static unsigned blk_rd_n, blk_wr_n, blk_wr_words;
static int blk_rb_flip, blk_rb_armed;                          /* review perf-c F5: corrupt the read-back of the next batch */
static int b_rd_blk(void *ctx, int sp, uint32_t a, uint32_t *v, uint32_t nw)
{
	uint32_t k;

	blk_rd_n++;
	for (k = 0; k < nw; k++)
		if (b_rd(ctx, sp, a + 4u * k, &v[k]))
			return -1;
	if (blk_rb_armed) {
		blk_rb_armed = 0;
		v[0] ^= 1u;
	}
	return 0;
}
static int b_wr_blk(void *ctx, int sp, uint32_t a, const uint32_t *v, uint32_t nw)
{
	uint32_t k;

	blk_wr_n++;
	blk_wr_words += nw;
	if (blk_rb_flip) {
		blk_rb_flip = 0;
		blk_rb_armed = 1;                                   /* the engine's next rd_blk is this batch's read-back */
	}
	for (k = 0; k < nw; k++)
		if (b_wr(ctx, sp, a + 4u * k, v[k]))
			return -1;
	return 0;
}
static unsigned plan_open_n, plan_bad;
static int plan_is_open;
static void b_plan(void *ctx, int open)               /* perf-d: the engine opens a plan only around an audited DP15 plan ending in a doorbell, never nested, always closed */
{
	(void)ctx;
	if (open) { if (plan_is_open) plan_bad++; plan_is_open = 1; plan_open_n++; }
	else plan_is_open = 0;
}
static const struct swg_bus BUS_BLK = { b_rd, b_wr, b_delay, NULL, NULL, NULL, b_rd_blk, b_wr_blk, b_plan };
#endif
/* the main soak: `total` tx frames in bursts of 1..12, a poll after each burst, per-round accounting. Stops at the first divergence. */
static void soak(unsigned total)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	unsigned i = 0, j, burst;

	while (i < total && div_msg[0] == '\0') {
		burst = 1u + (i * 7u + i / 5u) % 12u;
		if (i + burst > total) burst = total - i;
		for (j = 0; j < burst; j++, i++) {
			const uint32_t len = i % 3u == 0u ? fr_arp(f) : fr_echo(f, i, (i * 53u) % 1473u);

			if (send_one(f, len)) soak_tx_acc++; else soak_tx_drop_unexp++;
		}
		pump();
		harvest();
		if (soak_tx_drop_unexp != 0u) diverge("after %u frames: en0 dropped %u tx frames in a burst of at most 12", i, soak_tx_drop_unexp);
		if (gap_total != 0u && div_msg[0] == '\0') diverge("GAP during the soak after %u frames: %s", i, gap_first);
		if (EN.tx_frames != SD.tx_seen) diverge("after %u frames: en0 accepted %u tx, the dongle saw %u", i, EN.tx_frames, SD.tx_seen);
		if (EN.rx_frames != SD.rx_deliv || g_in_n != SD.rx_deliv) diverge("after %u frames: rx delivered by the dongle %u, en0 rx_frames %u, ifnet_input %u", i, SD.rx_deliv, EN.rx_frames, g_in_n);
		if (EN.txb.inflight != 0u) diverge("after %u frames: %u tx buffers still in flight", i, EN.txb.inflight);
		if (EN.rxb.inflight != SD.fq_t - SD.fq_h) diverge("after %u frames: en0 has %u rx buffers posted, the dongle holds %u", i, EN.rxb.inflight, SD.fq_t - SD.fq_h);
	}
}

/* ------------------------------------------------------------------ bring-up: the real L25/L26 run, then the hand-over */
static int bring_up(int wrap0)
{
	int rc;
	uint32_t i, n;

	SD.on = 0;                                                  /* a fresh world: the previous soak dongle must not see the new L25 run's doorbells */
	scen();
	D.drop_echo = 0u;
	if (wrap0) {                                                /* ring 0 laps once during the flow create (wlan_l25_host_test.c test_path_wrap) */
		x.d4wr = 127u;
		g1[(H2DW - G1PA) / 4u] = 127u;
		g1[(H2DR - G1PA) / 4u] = 100u;
	}
	rc = swl25_run(&x, 26u);
	if (rc != 0 || x.d4dstop || x.d4dlv != 26u || !swl25_H.hs_done) { diverge("L25/L26 run: rc %d d4dstop %u d4dlv %u hs_done %u", rc, x.d4dstop, x.d4dlv, swl25_H.hs_done); return -1; }
	if (wrap0 && x.d4wrap0 != 1u) { diverge("ring 0 did not lap during L25 (wrap0 %u)", x.d4wrap0); return -1; }
	{       /* patch 0357: the one pool line says what was chosen (swl25_env logs firmware value, ring, block, ceiling, count) */
		char want[64];

		const unsigned cap = (DALEN - 0xC000u) / 2048u > 255u ? 255u : (DALEN - 0xC000u) / 2048u;

		snprintf(want, sizeof want, "block holds %u, ceiling %u, chosen %u from ", cap, cap, (unsigned)RXN);
		if (!has("SWEET WLAN: rx pool: firmware max_rxbufpost read ") || !has(want)) { diverge("the L25 run did not log the rx pool choice (%s)", want); return -1; }
	}
	harvest();                                                  /* the L25 phase GAP lines are not the soak's */
	gt_n = 0; gap_total = 0; gap_first[0] = '\0';
	memset(&SD, 0, sizeof SD);
	memset(rxq, 0, sizeof rxq); memset(txq, 0, sizeof txq);
	rxq_h = rxq_t = txq_h = txq_t = 0u;
	g_in_n = g_in_bytes = 0u;
	apply_n = 0u;
	div_msg[0] = '\0';
	/* the second dongle takes over where the first stopped */
	SD.r1_seen = swl25_H.l.r1.wr; SD.r1_abs = swl25_H.l.r1.seq - SWDP_EPOCH_INIT;
	SD.fl_seen = swl25_H.l.flow.ring.wr; SD.fl_abs = swl25_H.l.flow.ring.seq - SWDP_EPOCH_INIT;
	SD.hrd3 = swl25_H.r3.seq - SWDP_EPOCH_INIT; SD.hrd4 = swl25_H.r4.seq - SWDP_EPOCH_INIT;
	if (SD.hrd3 != D.n3 || SD.hrd4 != D.n4 || SD.r1_seen != (g1[(H2DW - G1PA) / 4u + 1u] & 0xFFFFu) || SD.fl_seen != (g1[(H2DW - G1PA) / 4u + 2u] & 0xFFFFu)) {
		diverge("hand-over: host cursors (r3 %u r4 %u r1 %u flow %u) differ from the first dongle's (n3 %u n4 %u)", SD.hrd3, SD.hrd4, SD.r1_seen, SD.fl_seen, D.n3, D.n4);
		return -1;
	}
	g1[(H2DR - G1PA) / 4u + 1u] = D.rxn % swd_rings[1].items;     /* sprint 2026-11-08: L25 re-posts each consumed rx buffer, so the first dongle read ring-1 slots 0..rxn-1 and slots rxn..wr-1 are still posted */
	g1[(H2DR - G1PA) / 4u + 2u] = SD.fl_seen;
	for (i = 0, n = 0; i < SWDP_NSLOT; i++) {
		if (swl25_H.l.rxb.used[i]) { SD.posted[i] = 1u; n++; }
		if (swl25_H.txb.used[i] && !(swl25_H.txdone & (1u << i))) SD.txout[i] = 1u;
	}
	for (i = D.rxn; i < SD.r1_seen; i++) {                          /* the still-posted buffers in ring-1 order (request id = item word 1) */
		SD.freeq[SD.fq_t++ % 256u] = g1[(RING1 - G1PA) / 4u + (i % swd_rings[1].items) * 8u + 1u];
	}
	if (n != SWDP_NSLOT || SD.r1_seen - D.rxn != SWDP_NSLOT) { diverge("hand-over: %u rx buffers in flight in the books, %u posted on ring 1, %u expected", n, SD.r1_seen - D.rxn, SWDP_NSLOT); return -1; }
	D.on = 0;
	SD.on = 1;
	if (en0_live_up(&EN, &BU, &x, &swl25_H, V_SPA, "192.0.2.10,255.255.255.0,192.0.2.1", apply_stub)) { diverge("en0_live_up refused"); return -1; }
#ifdef EN0_LIVE_BLK
	x.b = &BUS_BLK;                                             /* Part A: the batched transport from here on, in quiet mode as the kernel's keep thread runs it */
	x.quiet = 1;
#endif
	harvest();
	return 0;
}

/* the negative cases print their own GAP lines; they run on fresh worlds after the soak */
static int g_neg_ok = 1;
#define NEG(c) do { if (!(c)) { fprintf(stderr, "NEG FAIL line %d: %s\n", __LINE__, #c); g_neg_ok = 0; } } while (0)

static unsigned count_of(const char *sub)
{
	unsigned n = 0;
	const char *p = logbuf;

	while ((p = strstr(p, sub)) != NULL) { n++; p += strlen(sub); }
	return n;
}
static void neg_overload(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	unsigned i, ok = 0, k;
	const uint32_t drop0 = EN.tx_drop, errs0 = g_ifnet.errs_out;

	loglen = 0u; logbuf[0] = '\0';
	for (i = 0; i < 20u; i++)                                   /* 16 tx buffers, 20 frames before any poll: Part A queues the 4 that find no buffer */
		ok += (unsigned)send_one(f, fr_echo(f, 5000u + i, 100u));
	NEG(ok == 20u && EN.tx_drop == drop0 && g_ifnet.errs_out == errs0 && g_sndq_n == 4u && EN.tx_blocked == 1u);   /* the 4 wait in the interface queue, en0 holds none */
	NEG(count_of("WIFI GAP: EN6 tx dropped") == 0u);            /* nothing dropped, nothing to report */
	pump();                                                     /* the completions free buffers, the poll posts the queued 4 in order */
	NEG(g_sndq_n == 0u && EN.txb.inflight == 0u && EN.tx_frames == SD.tx_seen && EN.rx_frames == SD.rx_deliv && g_in_n == SD.rx_deliv);
	harvest();
	/* more than the buffers AND the interface queue hold before a poll: the stack drops the excess at its enqueue (not en0); en0 drops nothing */
	loglen = 0u; logbuf[0] = '\0'; ok = 0;
	{
		const uint32_t sd0 = g_sndq_drop;
		for (i = 0; i < 16u + SNDQ + 4u; i++)
			ok += (unsigned)send_one(f, fr_echo(f, 6000u + i, 64u));
		k = count_of("WIFI GAP: EN6 tx dropped");
		NEG(ok == 16u + SNDQ && g_sndq_drop - sd0 == 4u && EN.tx_drop == drop0 && k == 0u && g_sndq_n == SNDQ);
	}
	pump();
	NEG(g_sndq_n == 0u && EN.txb.inflight == 0u && EN.tx_frames == SD.tx_seen && EN.rx_frames == SD.rx_deliv && g_in_n == SD.rx_deliv);
	harvest();
}
static void neg_backlog(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	unsigned i, d0 = SD.rx_deliv;
	const unsigned bl0 = SD.bl_max;

	for (i = 0; i < RXN + 84u; i++)                             /* RXN + 84 unsolicited frames against the rx pool (16 or 255 buffers): 84 wait in the dongle until en0 re-posts */
		dg_rx(f, fr_noise(f, i), rx_off_pick());
	NEG(SD.bl_t - SD.bl_h >= 84u);
	pump();
	NEG(SD.rx_deliv - d0 == RXN + 84u && SD.bl_h == SD.bl_t && SD.bl_lost == 0u && SD.bl_max >= 84u && SD.bl_max >= bl0);
	NEG(EN.rx_frames == SD.rx_deliv && g_in_n == SD.rx_deliv && EN.rxb.inflight == SD.fq_t - SD.fq_h);
	harvest();
}
static void neg_rxbuf_poison(void)
{
	const uint8_t *p;
	uint32_t i, nz = 0u;

	loglen = 0u; logbuf[0] = '\0';
	p = en0l_rxbuf(&BU, RXN + 83u);                             /* the first slot past the pool */
	for (i = 0; i < 64u; i++) nz |= p[i];
	NEG(nz == 0u && has("WIFI GAP: EN-L rx buffer unavailable"));
	loglen = 0u; logbuf[0] = '\0';
}

static void neg_store_fail(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	uint32_t wr0, nw0;
	unsigned db5;

	if (bring_up(0)) { NEG(0); return; }
	loglen = 0u; logbuf[0] = '\0';
	wr0 = EN.flow.ring.wr; db5 = SD.db5;
	J.fail_wr = J.nw + 3u;                                      /* the third store of the next plan: a payload word */
	NEG(!send_one(f, fr_echo(f, 1u, 60u)));
	NEG(x.d4dstop == 1u && EN.flow.ring.wr == wr0 && SD.db5 == db5 && EN.tx_drop == 1u && BU.failed == 1u);
	NEG(has("WIFI GAP: DP15 store 1/4 of plan en0 tx failed") && has("WIFI GAP: EN6 tx dropped"));
	nw0 = J.nw;
	NEG(!send_one(f, fr_echo(f, 2u, 60u)));                     /* the engine is stopped: refused with the DP15 gap, nothing written */
	NEG(J.nw == nw0 && has("WIFI GAP: DP15 plan en0 tx refused: engine stopped") && BU.failed == 2u);
	pump();
	NEG(J.nw == nw0 && EN.flow.st == SWDP_FLOW_FAILED && !(g_ifnet.flags & IFF_RUNNING) && has("WIFI GAP: EN-L the engine or the bus stopped"));
	NEG(!send_one(f, fr_echo(f, 3u, 60u)) && J.nw == nw0 && EN.tx_drop == 3u);
	harvest();
}
static void neg_dead_link(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	uint32_t nw0;

	if (bring_up(0)) { NEG(0); return; }
	loglen = 0u; logbuf[0] = '\0';
	dead4 = 1;                                                  /* ring-4 write index reads all ones */
	pump();
	NEG(BU.dead && has("WIFI GAP: EN-L D2H write index reads all ones (dead link), ring 4") && EN.flow.st == SWDP_FLOW_FAILED && !(g_ifnet.flags & IFF_RUNNING));
	nw0 = J.nw;
	NEG(!send_one(f, fr_arp(f)) && J.nw == nw0 && EN.tx_drop == 1u);
	NEG(en0l_d2h_item(&BU, 4u, 0u) == NULL && en0l_exec(&BU, NULL, 0u) == -1 && J.nw == nw0);
	dead4 = 0;
	harvest();
}
#ifdef EN0_LIVE_BLK
/* review perf-c F5: the engine side of the batch refuses what swg_d4d_st refuses. (1) an old word that differs from its shadow: SWG_E_OLD before ANY store of the batch; (2) a read-back
 * that differs: SWG_E_READBACK. Both stop the engine (DP15 GAP), the frame is a counted drop. Fresh worlds. */
static void neg_blk_old(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	uint32_t nw0, *w;

	if (bring_up(0)) { NEG(0); return; }
	loglen = 0u; logbuf[0] = '\0';
	w = mem(SWG_SP_DATA, SWL25_TX_OFF + (EN.flow.ring.wr % SWDP_NSLOT) * SWL25_BUF_SZ + 8u);    /* a word of the next frame's tx buffer, behind the engine's back */
	NEG(w != NULL);
	if (w == NULL) return;
	*w ^= 0x5Au;
	nw0 = J.nw;
	NEG(!send_one(f, fr_echo(f, 7u, 200u)));
	NEG(x.stopped && x.code == SWG_E_OLD && J.nw == nw0 && has("WIFI GAP: DP15 store 1/4 of plan en0 tx failed") && has("WIFI GAP: EN6 tx dropped"));
	harvest();
}
static void neg_blk_rb(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];

	if (bring_up(0)) { NEG(0); return; }
	loglen = 0u; logbuf[0] = '\0';
	blk_rb_flip = 1;
	NEG(!send_one(f, fr_echo(f, 8u, 200u)));
	NEG(x.stopped && x.code == SWG_E_READBACK && has("WIFI GAP: DP15 store 1/4 of plan en0 tx failed"));
	blk_rb_flip = blk_rb_armed = 0;
	harvest();
}
#endif
#ifndef SWG_DP_SHADOW_ONES_OK
/* sweet_wlan_g2.h without the shadow-aware dead-link check: the first lap of a flow-ring slot that held a broadcast frame is REFUSED by the bus (GAP), the engine lives, the ring stays put */
static void neg_guard(void)
{
	static uint8_t f[SWDP_ETH_MAXFRAME];
	unsigned i, refused_at = 0u;

	if (bring_up(0)) { NEG(0); return; }
	loglen = 0u; logbuf[0] = '\0';
	for (i = 0; i < 200u && refused_at == 0u; i++) {
		if (!send_one(f, fr_arp(f))) refused_at = i + 1u;
		pump();
	}
	NEG(refused_at != 0u && BU.ff_refused >= 1u && !x.d4dstop && !x.stopped && has(EN0L_GAP_FF));
	NEG(!send_one(f, fr_arp(f)) && BU.ff_refused >= 2u && !x.d4dstop);      /* stuck at that slot, loudly */
	printf("INFO: ff_guard: the flow-ring lap refused the %uth broadcast frame, engine alive\n", refused_at);
	harvest();
}
#endif

static void print_gaps(const char *tag)
{
	unsigned i;

	for (i = 0; i < gt_n; i++)
		printf("%s GAP x%u: %s\n", tag, gt[i].n, gt[i].t);
	gt_n = 0u; gap_total = 0u; gap_first[0] = '\0';
}

int main(void)
{
	unsigned wraps_r1, wraps_flow, wraps_r3, wraps_r4, frames_tx, frames_rx, soak_gaps;
	const unsigned total = 7000u;

	(void)l25_main;
	lj_on = 1u;
	if (bring_up(1)) {
		printf("RESULT: FAIL wlan_en0_live_host_test bring-up: %s\n", div_msg);
		return 1;
	}
	printf("INFO: hand-over: ff_guard=%u rx_in_flight=%u tx_in_flight=%u ring1_wr=%u flow_wr=%u wraps_r0=%u (L25 flow create at slot 127)\n", BU.ff_guard, EN.rxb.inflight, EN.txb.inflight, EN.rxpost.wr, EN.flow.ring.wr, x.d4wrap0);
	print_gaps("hand-over");
	soak(total);
	frames_tx = EN.tx_frames; frames_rx = EN.rx_frames; soak_gaps = gap_total;
	wraps_r1 = (EN.rxpost.seq - SWDP_EPOCH_INIT) / EN.env.depth1;
	wraps_flow = (EN.flow.ring.seq - SWDP_EPOCH_INIT) / EN.env.depth_flow;
	wraps_r3 = (EN.txcpl.seq - SWDP_EPOCH_INIT) / EN.env.depth_d2h[1];
	wraps_r4 = (EN.rxcpl.seq - SWDP_EPOCH_INIT) / EN.env.depth_d2h[2];
	print_gaps("soak");
	lj_on = 0u;
	printf("INFO: L25 store judge over the main run: %u stores, %u refused%s%s\n", lj_n, lj_ref, lj_ref ? ", first: " : "", lj_ref ? lj_first : "");
	if (div_msg[0] == '\0' && (lj_ref != 0u || lj_n == 0u))
		diverge("the L25 store judge refused %u of %u stores (first: %s)", lj_ref, lj_n, lj_first);
	if (div_msg[0] == '\0') {
		if (!(EN.rx_drop == 0u && EN.d2h_bad == 0u && sm_live == 0 && BU.ff_refused == 0u && BU.failed == 0u && BU.refused == 0u && !x.d4dstop && !x.stopped))
			diverge("final: rx_drop %u d2h_bad %u live mbufs %d ff_refused %u failed %u refused %u d4dstop %u", EN.rx_drop, EN.d2h_bad, sm_live, BU.ff_refused, BU.failed, BU.refused, x.d4dstop);
		else if (SD.tx_seen != EN.tx_frames || g_ifnet.pkts_out != EN.tx_frames || g_in_n != EN.rx_frames || SD.tx_arp + SD.tx_echo != SD.tx_seen || SD.tx_other != 0u)
			diverge("final accounting: dongle tx %u arp %u echo %u other %u, en0 tx %u, ifnet out %u, rx %u/%u", SD.tx_seen, SD.tx_arp, SD.tx_echo, SD.tx_other, EN.tx_frames, g_ifnet.pkts_out, EN.rx_frames, g_in_n);
		else if (wraps_r1 < 2u || wraps_flow < 2u || wraps_r3 < 2u || wraps_r4 < 2u || frames_tx < 2000u || frames_rx < 2000u)
			diverge("final: the rings did not wrap enough (r1 %u flow %u r3 %u r4 %u) or too few frames (tx %u rx %u)", wraps_r1, wraps_flow, wraps_r3, wraps_r4, frames_tx, frames_rx);
	}
	if (div_msg[0] == '\0') {      /* patch 0357: the pool the engine chose (swdp_rx_pick) is the one the dongle saw posted, refilled in batches: fewer ring-1 doorbells than frames, the pool whole at the end */
		const uint32_t pool = swdp_env_rxn(&EN.env), thr = swdp_rx_thr(pool, 0u);

		if (pool != RXN || EN.rxb.inflight > pool || EN.rxb.inflight + thr <= pool || EN.rx_posted_peak != pool || EN.rx_refill_fail != 0u)
			diverge("rx pool: chose %u (want %u), %u posted at the end, threshold %u, peak %u, refills not done %u", pool, (unsigned)RXN, EN.rxb.inflight, thr, EN.rx_posted_peak, EN.rx_refill_fail);
		else if (SD.db1 != EN.rx_batches || EN.rx_batch_max > SWDP_RXB_MAX || (RXN > SWDP_NSLOT && SD.db1 * 3u >= SD.rx_deliv))       /* one doorbell per batch, nothing else rings ring 1 */
			diverge("rx pool %u: %u ring-1 doorbells for %u frames, %u batches, largest %u", pool, SD.db1, SD.rx_deliv, EN.rx_batches, EN.rx_batch_max);
		printf("INFO: rx pool %u: ring-1 doorbells %u for %u rx frames; refill batches %u (largest %u, %u buffers); fewest posted at a refill %u\n", pool, SD.db1, SD.rx_deliv, EN.rx_batches, EN.rx_batch_max, EN.rx_batch_bufs, EN.rx_posted_min ? EN.rx_posted_min - 1u : 0u);
	}
	printf("INFO: wraps r0=%u r1=%u flow=%u r3=%u r4=%u; dongle: tx %u (arp %u echo %u) rx %u; en0: tx_drop %u rx_drop %u d2h_bad %u; bus: execs %u ff_refused %u\n",
	    x.d4wrap0, wraps_r1, wraps_flow, wraps_r3, wraps_r4, SD.tx_seen, SD.tx_arp, SD.tx_echo, SD.rx_deliv, EN.tx_drop, EN.rx_drop, EN.d2h_bad, BU.execs, BU.ff_refused);
	if (div_msg[0] == '\0') {                                   /* same world: rx backlog against a 16-buffer pool, tx overload, the poison buffer */
		neg_backlog();
		neg_overload();
		neg_rxbuf_poison();
		print_gaps("case");
	}
	{
		const char *first = div_msg[0] != '\0' ? div_msg : NULL;
		char keep[256];

		keep[0] = '\0';
		if (first != NULL) snprintf(keep, sizeof keep, "%s", first);
		neg_store_fail();                                   /* fresh worlds: a failing store, a dead link, (the ff guard) */
		neg_dead_link();
#ifdef EN0_LIVE_BLK
		neg_blk_old();
		neg_blk_rb();
#endif
#ifndef SWG_DP_SHADOW_ONES_OK
		neg_guard();
#endif
		div_msg[0] = '\0';
		if (keep[0] != '\0') snprintf(div_msg, sizeof div_msg, "%s", keep);
	}
	if (div_msg[0] == '\0' && !g_neg_ok)
		diverge("a negative case failed (see stderr)");
	if (div_msg[0] != '\0') {
		printf("RESULT: FAIL wlan_en0_live_host_test frames_tx=%u frames_rx=%u wraps_r1=%u wraps_flow=%u gaps=%u first divergence: %s\n", frames_tx, frames_rx, wraps_r1, wraps_flow, soak_gaps, div_msg);
		return 1;
	}
#ifdef EN0_LIVE_BLK
	if (plan_bad != 0u || plan_open_n < 5000u || plan_is_open) {
		printf("RESULT: FAIL wlan_en0_live_host_test (blk) plan hook: %u opens, %u nested, open at the end %d\n", plan_open_n, plan_bad, plan_is_open);
		return 1;
	}
	if (blk_wr_n < 5000u || blk_rd_n < 2u * blk_wr_n + 5000u) {         /* the engine makes 2 rd_blk per batched store (old words, read-back); the rest are the en0 bus reads (D2H items, rx buffers) */
		printf("RESULT: FAIL wlan_en0_live_host_test (blk) the batched path was not used enough: wr_blk %u calls (%u words), rd_blk %u\n", blk_wr_n, blk_wr_words, blk_rd_n);
		return 1;
	}
	printf("blk: wr_blk %u calls (%u words), rd_blk %u calls (%u of them the en0 bus reads)\n", blk_wr_n, blk_wr_words, blk_rd_n, blk_rd_n - 2u * blk_wr_n);
#endif
	printf("consume commits: %u, largest %u items\n", dg_rd_batches, dg_rd_max);
	printf("RESULT: PASS wlan_en0_live_host_test frames_tx=%u frames_rx=%u wraps_r1=%u wraps_flow=%u gaps=%u\n", frames_tx, frames_rx, wraps_r1, wraps_flow, soak_gaps);
	return 0;
}
