/* wlan_en0.h - XNU `en0` ifnet in poll mode over the reviewed Wi-Fi data path (P-W3, T-4d29; SPEC-WIFI Part E EN0 block, rows EN1-EN12).
 *
 * One ifnet (`en0`, legacy output, no start/pre_enqueue/input_poll, no interrupt anywhere) whose TX output turns an mbuf chain into one msgbuf
 * tx item (swdp_build_txpost + swdp_plan_tx, audited by swdp_audit) and whose en0_poll() consumes the D2H tx-complete and rx-complete rings by
 * reading the write index, builds the RX mbuf chain (2-byte pad, per-buffer lengths, pkt_hdr handoff, BPF tap) and reposts rx buffers.
 * The executing transport (DP15, swg_bus) is NOT wired: it is the `struct en0_bus` the includer supplies; exec() < 0 means "nothing was written".
 *
 * The includer provides, BEFORE including this file: the XNU KPI declarations (mbuf, ifnet, bpf, kern_event, if_media) or the host-test stubs of
 * the same names; SWDP_LOG / SWDP_GAP (as for sweet_wlan_data.h); EN0_XFLAGS(ifp) / EN0_XF_NOAUTO (if_xflags and IFXF_NX_NOAUTO, which are
 * kernel-private) and EN0_EFLAGS(ifp) / EN0_EF_ENQUEUE_MULTI; and then includes sweet_wlan_data.h and sweet_wlan_data_plan.h.
 * No function logs a MAC or an IP address (rule 4); log lines carry counters, ids and flag words only.
 * Sources: XNU 22.6.0 bsd/net/kpi_interface.c:458-462,511-513 (E-3), dlil.c:3794-3809,5908-5909 (E-2), bsd/sys/kpi_mbuf.h (signatures),
 * bsd/net/bpf.h:1557-1587, bsd/net/net_kev.h:50,66-67, bsd/net/if_media.h:262,313-314; pattern: xnu patch 0073 (ud_if_create/output/ioctl).
 */
#ifndef WLAN_EN0_H
#define WLAN_EN0_H

#if !defined(EN0_XFLAGS) || !defined(EN0_XF_NOAUTO) || !defined(EN0_EFLAGS) || !defined(EN0_EF_ENQUEUE_MULTI)
#error "wlan_en0.h: define EN0_XFLAGS/EN0_XF_NOAUTO/EN0_EFLAGS/EN0_EF_ENQUEUE_MULTI before including (EN1, EN11)"
#endif

/* sprint 2026-11-08 (keep): the includer may serialise the poll thread (en0_poll and the bus it drives) against the stack's transmit (en0_output): EN0_LOCK()/EN0_UNLOCK() wrap the one section of en0_output that touches the shared state
 * (e->scratch, e->item, the flow ring copy, the tx book, the bus). Default: nothing (host tests, the single-threaded one-shot hook). A locking includer must make the lock recursive: the poll thread can reach en0_output on its own thread. */
#ifndef EN0_COPYOUT
#ifdef KERNEL
#define EN0_COPYOUT(src, udst, n) copyout((src), (user_addr_t)(uintptr_t)(udst), (n))   /* SIOCGIFMEDIA64: ifm_ulist is a user address (if.c ifioctl_get_media passes the request through) */
#else
#define EN0_COPYOUT(src, udst, n) (memcpy((udst), (src), (n)), 0)
#endif
#endif
#ifndef EN0_LOCK
#define EN0_LOCK() ((void)0)
#define EN0_UNLOCK() ((void)0)
#endif

#define EN0_RX_PAD      2u      /* E-1: the IP header lands 4-byte aligned */
#ifndef EN0_IFSTART
#define EN0_IFSTART(ifp) ifnet_start(ifp)   /* Part A (start model): ask DLIL's starter thread to run en0_start again (tx slots were freed) */
#endif
#ifndef EN0_KICK
#define EN0_KICK()      ((void)0)   /* the includer wakes its poll thread here (a frame was posted or queued); the host tests poll by hand */
#endif
#define EN0_ALEN        6u
#define EN0_MTU         1500u
#define EN0_BAUD        100000000u   /* guess: a nominal value, nothing reads it yet */
#define EN0_IOCTL_SEEN  8u
#define EN0_BOOTARG     "pixos_en0_ip"   /* "a.b.c.d,m.m.m.m,g.g.g.g", value from the gitignored kernel .env via the run tool (rule 4) */

#define EN0_GAP_EN1 "WIFI GAP: EN1 en0 allocated without IFXF_NX_NOAUTO (compat nexus would be plumbed)"
#define EN0_GAP_EN2 "WIFI GAP: EN2 no Wi-Fi MAC available, en0 not attached"
#define EN0_GAP_EN5 "WIFI GAP: EN5 bpf_attach not done: raw (DHCP) clients cannot open en0"
#define EN0_GAP_EN7 "WIFI GAP: EN7 interrupts off: en0 latency is the poll period (SWDP_GAP_DP9)"
#define EN0_GAP_EN8 "WIFI GAP: EN8 link event posted with no consumer proof (W17 not traced in iOS)"
#define EN0_GAP_EN9 "WIFI GAP: EN9 no static IP in the boot configuration: en0 stays unaddressed"
#define EN0_GAP_EN10M "WIFI GAP: EN10 multicast list not programmed into the dongle (add/del multicast accepted and ignored)"
#define EN0_GAP_EN12 "WIFI GAP: EN12 no Apple80211 on en0: scans/ioctls from wifid are not served by this ifnet (Part F W3/W4)"

struct en0_bus {                /* DP15, supplied by the includer */
	void *ctx;
	int (*exec)(void *ctx, const struct swdp_store *s, uint32_t n);          /* <0: not executed, nothing written */
	uint32_t (*d2h_wr)(void *ctx, uint32_t ring_id);                         /* the dongle's write index of D2H ring 2..4 */
	const uint8_t *(*d2h_item)(void *ctx, uint32_t ring_id, uint32_t idx);   /* item idx of that ring, NULL when unreadable */
	const uint8_t *(*rxbuf)(void *ctx, uint32_t slot);                       /* rx buffer `slot` (env.buf_sz bytes) */
};

struct en0_cfg { uint8_t ip[4], mask[4], gw[4]; };
/* patch 0282 (W-D6 boot B): the receive hook for EAPOL (ethertype 0x888E). The includer installs it while a host 4-way can run (the keep thread's re-join); en0_rx_deliver then hands every EAPOL frame to it instead of ifnet_input. It returns 0 when it consumed the frame
 * (a reply, if any, is left in out[0..*olen) and en0 posts it on the flow ring), non-zero when no 4-way is in progress (the frame is dropped with a counted GAP: nothing above en0 answers EAPOL, and a rekey is not handled) */
#define EN0_ETH_EAPOL   0x888Eu
#define EN0_EAPOL_OUT   192u    /* the longest reply: 14 B Ethernet + 95 B EAPOL-Key + 64 B RSNE (SWJ_TX_MAX, sweet_wlan_join.h:89) */
typedef int (*en0_eapol_fn)(void *ctx, const uint8_t *f, uint32_t len, uint8_t *out, uint32_t outcap, uint32_t *olen);
typedef int (*en0_apply_fn)(ifnet_t ifp, const struct en0_cfg *c);        /* SIOCAIFADDR on a kernel socket (0073 pattern) */

struct en0 {
	ifnet_t ifp;
	struct en0_bus bus;
	struct swdp_env env;                          /* the audited envelope; en0_sync() refreshes its cursors before every plan */
	struct swdp_flow flow;
	struct swdp_ring rxpost, txcpl, rxcpl;       /* ring 1 (H2D), rings 3 and 4 (D2H) */
	struct swdp_book rxb, txb;
	uint32_t rx_dataoffset;
	uint32_t attached, link_up, addr_done, stale_gap;
	uint32_t rx_frames, rx_drop, tx_frames, tx_drop, d2h_bad;
	uint32_t tx_blocked, tx_waits;                /* Part A (start model): en0_start stopped for want of a tx buffer / flow slot (the poll restarts it), and how often */
	uint32_t cons_fail;
	uint32_t rx_posted_min, rx_batches, rx_batch_max, rx_batch_bufs, rx_refill_fail;   /* patch 0357, per 10 s window (the includer resets the first four): fewest buffers posted at a refill + 1 (0 = not measured), refill batches, most buffers in one batch, buffers posted by them; rx_refill_fail counts for the run */
	uint32_t rx_posted_peak;                      /* patch 0357: most buffers posted at once (the pool size reached) */
	struct swdp_store rxst[SWDP_MAX_STORES_RXB];  /* patch 0357: the batched rx post plan and its items, in the (static) en0 state, not on the stack */
	uint8_t rxit[SWDP_RXB_MAX][SWDP_ISZ_RXPOST];
	uint32_t rx_starve, rx_avail_min;             /* Part A (read-only): polls that found completions pending while the dongle held NO empty rx buffer, and the fewest empty buffers seen */                           /* review perf-d F1: a refused consume commit stops the data path (else the same completions would be handled twice) */
	const char *bootarg;                          /* value of EN0_BOOTARG or NULL */
	en0_apply_fn apply;
	unsigned long ioctl_seen[EN0_IOCTL_SEEN];
	uint32_t ioctl_n;
	en0_eapol_fn eapol;                           /* patch 0282: the EAPOL receive hook (NULL = every EAPOL frame is dropped with a GAP) */
	void *eapol_ctx;
	uint32_t eapol_rx, eapol_tx, eapol_drop, eapol_txfail, eapol_slot, eapol_last_ok;    /* frames consumed by the hook / replies posted / frames dropped / replies not posted / the flow slot (= tx buffer) of the last reply */
	uint8_t eapol_out[EN0_EAPOL_OUT];
	uint8_t scratch[SWDP_ETH_MAXFRAME];          /* tx flatten buffer */
	uint8_t item[64];                             /* the one item being built */
};
static struct en0 *en0_self;                      /* the ifnet callbacks carry no context */

static inline void en0_gapf(const char *what, uint32_t a)
{
	char b[112];
	snprintf(b, sizeof b, "%s %u", what, (unsigned)a);
	SWDP_GAP(b);
}

static inline void en0_sync(struct en0 *e)
{
	e->env.wrf = e->flow.ring.wr; e->env.wr1 = e->rxpost.wr;
	e->env.rd_d2h[1] = e->txcpl.rd; e->env.rd_d2h[2] = e->rxcpl.rd;
	e->env.flow_open = e->flow.st == SWDP_FLOW_OPEN;
}

/* audit then execute one plan; 0 ok, <0 refused (nothing written) */
static inline int en0_run_plan(struct en0 *e, const struct swdp_store *s, uint32_t n)
{
	int r = swdp_audit(&e->env, s, n);
	if (r) {
		en0_gapf("WIFI GAP: DP14 en0 plan refused by the audit, reason", (uint32_t)r);
		return -1;
	}
	if (e->bus.exec == NULL || e->bus.exec(e->bus.ctx, s, n) < 0) {
		SWDP_GAP(SWDP_GAP_DP15);
		return -2;
	}
	return 0;
}

/* ------------------------------------------------------------------ EN9: boot configuration */
static inline int en0_quad(const char **p, uint8_t q[4], char end)
{
	uint32_t i, v, digits;
	const char *s = *p;
	for (i = 0; i < 4u; i++) {
		v = 0u; digits = 0u;
		while (digits < 4u && *s >= '0' && *s <= '9') { v = v * 10u + (uint32_t)(*s - '0'); s++; digits++; }
		if (digits == 0u || digits > 3u || v > 255u || (digits > 1u && s[-(int)digits] == '0'))
			return -1;
		q[i] = (uint8_t)v;
		if (i < 3u) { if (*s != '.') return -1; s++; }
	}
	if (*s != end)
		return -1;
	*p = (end != '\0') ? s + 1 : s;
	return 0;
}

/* The only source of the address. Absent or invalid: no address, a GAP line, NEVER a default (EN9). 0 ok, -1 refused. */
static inline int en0_boot_cfg(const char *s, struct en0_cfg *c)
{
	uint32_t m, i;
	memset(c, 0, sizeof *c);
	if (s == NULL || *s == '\0') {
		SWDP_GAP(EN0_GAP_EN9);
		return -1;
	}
	if (en0_quad(&s, c->ip, ',') || en0_quad(&s, c->mask, ',') || en0_quad(&s, c->gw, '\0')) {
		memset(c, 0, sizeof *c);
		SWDP_GAP("WIFI GAP: EN9 boot configuration is not <ip>,<mask>,<gateway> in dotted quads: en0 stays unaddressed");
		return -1;
	}
	m = 0u;
	for (i = 0; i < 4u; i++) m = (m << 8) | c->mask[i];
	if ((c->ip[0] | c->ip[1] | c->ip[2] | c->ip[3]) == 0u || m == 0u || (~m & (~m + 1u)) != 0u) {
		memset(c, 0, sizeof *c);
		SWDP_GAP("WIFI GAP: EN9 boot configuration has a zero address or a non-contiguous/zero mask: en0 stays unaddressed");
		return -1;
	}
	return 0;
}

/* ------------------------------------------------------------------ EN3/EN4: RX frame into the stack */
static inline int en0_rx_chain(const uint8_t *f, uint32_t len, mbuf_t *out)
{
	mbuf_t m = NULL, b;
	uint32_t off = 0u, n, hops = 0u;
	uint8_t *d;
	size_t cap;
	if (len < SWDP_ETH_HLEN || len > SWDP_ETH_MAXFRAME) {
		en0_gapf("WIFI GAP: EN3 rx frame length out of range, dropped, length", len);
		return -1;
	}
	if (mbuf_allocpacket(MBUF_DONTWAIT, len + EN0_RX_PAD, NULL, &m) != 0 || m == NULL) {
		SWDP_GAP("WIFI GAP: EN3 rx mbuf allocation failed: frame dropped and counted");
		return -1;
	}
	for (b = m; b != NULL && off < len && hops < 64u; b = mbuf_next(b), hops++) {
		d = (uint8_t *)mbuf_datastart(b); cap = mbuf_maxlen(b);
		if (b == m) { d += EN0_RX_PAD; cap = cap > EN0_RX_PAD ? cap - EN0_RX_PAD : 0u; }
		n = (len - off) < cap ? (len - off) : (uint32_t)cap;
		memcpy(d, f + off, n);
		mbuf_setdata(b, d, n);
		off += n;
	}
	if (off != len || mbuf_len(m) < SWDP_ETH_HLEN) {
		mbuf_freem(m);
		en0_gapf("WIFI GAP: EN3 frame longer than the mbuf chain can hold or first buffer under 14 bytes: dropped, length", len);
		return -1;
	}
	mbuf_pkthdr_setlen(m, len);
	*out = m;
	return 0;
}

static inline int en0_tx_frame(struct en0 *e, const uint8_t *f, uint32_t len, uint32_t *slot);

/* patch 0282: one received EAPOL frame. Called with the en0 lock held (the poll thread's rx path), so the reply goes out through the same tx path the stack's frames use. */
static inline void en0_eapol_in(struct en0 *e, const uint8_t *f, uint32_t len)
{
	uint32_t olen = 0u, slot = 0u;
	int rc;
	if (e->eapol != NULL && e->eapol(e->eapol_ctx, f, len, e->eapol_out, sizeof e->eapol_out, &olen) == 0) {
		e->eapol_rx++;
		if (olen > 0u) {
			rc = en0_tx_frame(e, e->eapol_out, olen, &slot);
			e->eapol_last_ok = rc == 0;       /* review 0282 F6: the re-join counts its M4 as sent only when this is 1 */
			if (rc == 0) {
				e->eapol_tx++;
				e->eapol_slot = slot;
			} else {
				e->eapol_txfail++;
				en0_gapf("WIFI GAP: EN6 EAPOL reply not posted (the AP will retry), code", (uint32_t)(-rc));
			}
		}
		return;
	}
	e->eapol_drop++;
	if (e->eapol_drop == 1u || (e->eapol_drop & 63u) == 0u)
		en0_gapf("WIFI GAP: EN4 EAPOL dropped (no 4-way running, rekey not handled), so far", e->eapol_drop);
}

static inline void en0_rx_deliver(struct en0 *e, const uint8_t *f, uint32_t len)
{
	mbuf_t m;
	struct ifnet_stat_increment_param st;
	if (len >= SWDP_ETH_HLEN && f[12] == (uint8_t)(EN0_ETH_EAPOL >> 8) && f[13] == (uint8_t)EN0_ETH_EAPOL) {
		en0_eapol_in(e, f, len);
		return;
	}
	if (en0_rx_chain(f, len, &m)) { e->rx_drop++; return; }
	mbuf_pkthdr_setrcvif(m, e->ifp);
	bpf_tap_in(e->ifp, DLT_EN10MB, m, NULL, 0);
	mbuf_pkthdr_setheader(m, mbuf_data(m));
	mbuf_pkthdr_adjustlen(m, -(int)SWDP_ETH_HLEN);
	mbuf_setdata(m, (uint8_t *)mbuf_data(m) + SWDP_ETH_HLEN, mbuf_len(m) - SWDP_ETH_HLEN);
	memset(&st, 0, sizeof st);
	st.packets_in = 1u; st.bytes_in = len;
	if (ifnet_input(e->ifp, m, &st) != 0) {     /* ifnet_input frees the chain on error (dlil.c:3794-3809): no free here */
		SWDP_GAP("WIFI GAP: EN4 ifnet_input returned an error, frame counted as rx drop");
		e->rx_drop++;
		return;
	}
	e->rx_frames++;
}

/* ------------------------------------------------------------------ EN7: poll (no interrupt anywhere) */
/* patch 0357: post up to n rx buffers (n <= SWDP_RXB_MAX) as ONE plan: [ITEM x got, WRIDX, DB] on ring 1 (swdp_plan_rxpost_n): the items go into consecutive slots, the write index moves once and the doorbell rings once.
 * Never past the ring end (the batch is cut at depth - wr; the next batch starts at slot 0). A buffer that cannot be taken, built or planned ends the batch there (what was taken is posted); a refused plan gives every
 * buffer back and nothing is stored. Returns the buffers posted (0 = none, a GAP says why). */
static inline uint32_t en0_rx_batch(struct en0 *e, uint32_t n)
{
	struct swdp_ring r = e->rxpost;
	uint32_t rid[SWDP_RXB_MAX], got = 0u, j, slot = 0u, slot0 = 0u, ns;
	uint8_t epoch;

	if (n > SWDP_RXB_MAX)
		n = SWDP_RXB_MAX;
	if (r.depth != 0u && n > r.depth - r.wr)
		n = r.depth - r.wr;
	while (got < n) {
		rid[got] = swdp_book_take(&e->rxb, e->env.buf_sz);
		if (rid[got] == 0u)
			break;
		if (swdp_ring_post(&r, &slot, &epoch) ||
		    swdp_build_rxpost(e->rxit[got], epoch, rid[got], e->env.rxbuf.pa + (uint64_t)(rid[got] - SWDP_RX_RID) * e->env.buf_sz, e->env.buf_sz)) {
			swdp_book_done(&e->rxb, rid[got], NULL);
			break;
		}
		if (got == 0u)
			slot0 = slot;
		got++;
	}
	if (got == 0u)
		return 0u;
	en0_sync(e);
	ns = swdp_plan_rxpost_n(&e->env, slot0, got, r.wr, &e->rxit[0][0], e->rxst);
	if (en0_run_plan(e, e->rxst, ns)) {
		for (j = 0; j < got; j++)
			swdp_book_done(&e->rxb, rid[j], NULL);
		return 0u;
	}
	e->rxpost = r;
	return got;
}

/* patch 0357: top the rx pool up. The pool is env.rx_slots (the count swdp_rx_pick chose); the rule is swdp_rx_refill_n (Linux: post when the posted count is at least the threshold below the pool, in one batch
 * with one doorbell). all = 1: the first fill, every free buffer; 0: the steady state, called once per rx poll after the completions were handled. The measures of the window are kept here (posted at the
 * trough, batches, buffers per batch); a refill that was due and could not be done is a GAP (the 1st, 2nd, 4th ... time), never silent. */
static inline void en0_rx_refill(struct en0 *e, uint32_t all)
{
	const uint32_t pool = swdp_env_rxn(&e->env);
	uint32_t n, got;

	if (swdp_book_set_cap(&e->rxb, pool) != 0 && swdp_book_cap(&e->rxb) != pool) {
		en0_gapf("WIFI GAP: EN7 rx pool larger than the book allows, pool", pool);
		return;
	}
	if (!all && (e->rx_posted_min == 0u || e->rxb.inflight + 1u < e->rx_posted_min))
		e->rx_posted_min = e->rxb.inflight + 1u;
	for (;;) {
		n = swdp_rx_refill_n(pool, e->rxb.inflight, swdp_ring_free(&e->rxpost), e->rxpost.wr, e->rxpost.depth, all);
		if (n == 0u)
			break;
		got = en0_rx_batch(e, n);
		if (got == 0u)
			break;
		e->rx_batches++;
		e->rx_batch_bufs += got;
		if (got > e->rx_batch_max)
			e->rx_batch_max = got;
	}
	if (e->rxb.inflight > e->rx_posted_peak)
		e->rx_posted_peak = e->rxb.inflight;
	if (pool - (e->rxb.inflight < pool ? e->rxb.inflight : pool) >= swdp_rx_thr(pool, all)) {      /* still due after the loop: it stopped on a ring that is full or a refused plan */
		e->rx_refill_fail++;
		if ((e->rx_refill_fail & (e->rx_refill_fail - 1u)) == 0u)
			en0_gapf("WIFI GAP: EN7 rx refill due but not done (ring full or plan refused), pool below size, posted", e->rxb.inflight);
	}
}

static inline void en0_rx_fill(struct en0 *e)
{
	en0_rx_refill(e, 1u);
}

/* the next readable item of D2H ring `ring_id` at the cursor `r` (a COPY the caller advances; the ring's own book moves at the batch commit), or NULL (nothing pending / not yet visible).
 * A corrupt item sets *skip: the caller counts it into the batch and stops there (the commit then passes it, as the single-item skip did). */
static inline const uint8_t *en0_d2h_take(struct en0 *e, uint32_t ring_id, const struct swdp_ring *r, uint32_t isz, int *skip)
{
	int pend = swdp_d2h_pending(r, e->bus.d2h_wr(e->bus.ctx, ring_id)), rc;
	const uint8_t *it;
	*skip = 0;
	if (pend < 0) {
		en0_gapf("WIFI GAP: EN7 D2H write index outside the ring, ring left alone, ring", ring_id);
		return NULL;
	}
	it = pend > 0 ? e->bus.d2h_item(e->bus.ctx, ring_id, r->rd) : NULL;
	if (it == NULL)
		return NULL;
	rc = swdp_d2h_check(it, isz, r->seq);
	if (rc == 0)
		return it;
	e->d2h_bad++;
	if (rc == SWDP_E_EPOCH) {                       /* not DMA'd yet: retry next poll, say so once */
		if (!e->stale_gap) { e->stale_gap = 1u; en0_gapf("WIFI GAP: EN7 D2H item epoch not current, retried on the next poll, ring", ring_id); }
		return NULL;
	}
	en0_gapf("WIFI GAP: EN7 corrupt D2H item skipped, check", (uint32_t)rc);
	*skip = 1;
	return NULL;
}

/* Part A: commit the batch: the read cursor of ring `ring_id` from r->rd to c->rd (1..SWDP_CONS_MAX items) in ONE audited plan (RDIDX + doorbell) */
static inline void en0_d2h_commit(struct en0 *e, uint32_t ring_id, struct swdp_ring *r, const struct swdp_ring *c)
{
	struct swdp_store st[SWDP_MAX_STORES];
	uint32_t n;
	en0_sync(e);
	n = swdp_plan_consume(&e->env, ring_id, c->rd, st);
	if (en0_run_plan(e, st, n) == 0) {
		*r = *c;
		return;
	}
	e->cons_fail = 1u;                       /* review perf-d F1: the items are processed but not consumed; handling them again would deliver frames twice and free busy tx buffers */
	en0_gapf("WIFI GAP: EN7 consume commit refused: the en0 data path stops (no item is handled twice), ring", ring_id);
}

static inline uint32_t en0_poll_tx(struct en0 *e)
{
	const uint8_t *it;
	struct swdp_cmpl c;
	struct swdp_ring cur = e->txcpl;
	uint32_t n = 0u;
	int skip = 0;
	while (n < SWDP_CONS_MAX && (it = en0_d2h_take(e, SWDP_RING_TXCPL, &cur, SWDP_ISZ_TX_CMPLT, &skip)) != NULL) {
		if (swdp_parse_txstatus(it, &c) == 0 && swdp_book_done(&e->txb, c.rid, NULL) == 0 && (int16_t)c.status != 0)
			en0_gapf("WIFI GAP: EN6 tx completion with a non-zero status, status", c.status);
		swdp_d2h_consume(&cur);
		n++;
	}
	if (skip && n < SWDP_CONS_MAX)
		swdp_d2h_consume(&cur);                 /* the corrupt item goes with the batch */
	if (n > 0u || skip)
		en0_d2h_commit(e, SWDP_RING_TXCPL, &e->txcpl, &cur);
	return n;
}

static inline void en0_rx_one(struct en0 *e, const uint8_t *it)
{
	struct swdp_cmpl c;
	uint32_t slot;
	int rc;
	swdp_cmpl_common(it, &c);
	slot = c.rid - SWDP_RX_RID;
	if (c.rid < SWDP_RX_RID || slot >= swdp_book_cap(&e->rxb) || !e->rxb.used[slot]) {
		swdp_book_done(&e->rxb, c.rid, NULL);       /* counted + GAP DP8 */
		return;
	}
	rc = swdp_parse_rxcmpl(it, e->rxb.len[slot], e->rx_dataoffset, &c);
	swdp_book_done(&e->rxb, c.rid, NULL);
	if (rc == 0)
		en0_rx_deliver(e, e->bus.rxbuf(e->bus.ctx, slot) + c.data_offset, c.data_len);
	else {
		en0_gapf("WIFI GAP: EN3 rx completion refused (status/range), frame dropped, check", (uint32_t)rc);
		e->rx_drop++;
	}
	/* patch 0357: no repost here any more: the buffer is back in the book and en0_poll_rx refills the pool once per poll, in batches (en0_rx_refill) */
}

static inline uint32_t en0_poll_rx(struct en0 *e)
{
	const uint8_t *it;
	struct swdp_ring cur = e->rxcpl;
	uint32_t n = 0u;
	int skip = 0;
	{       /* Part A (read-only bookkeeping): buffers still empty at the dongle = posted - completed-but-unprocessed */
		const int pend = swdp_d2h_pending(&cur, e->bus.d2h_wr(e->bus.ctx, SWDP_RING_RXCPL));
		if (pend > 0) {
			const uint32_t avail = e->rxb.inflight > (uint32_t)pend ? e->rxb.inflight - (uint32_t)pend : 0u;
			if (avail == 0u) e->rx_starve++;
			if (e->rx_avail_min == 0u || avail + 1u < e->rx_avail_min) e->rx_avail_min = avail + 1u;   /* stored + 1: 0 = not measured yet */
		}
	}
	while (n < SWDP_CONS_MAX && (it = en0_d2h_take(e, SWDP_RING_RXCPL, &cur, SWDP_ISZ_RX_CMPLT, &skip)) != NULL) {
		en0_rx_one(e, it);
		swdp_d2h_consume(&cur);
		n++;
	}
	if (skip && n < SWDP_CONS_MAX)
		swdp_d2h_consume(&cur);
	if (n > 0u || skip)
		en0_d2h_commit(e, SWDP_RING_RXCPL, &e->rxcpl, &cur);
	if (n > 0u && !e->cons_fail)
		en0_rx_refill(e, 0u);                  /* patch 0357: the handled buffers are free in the book: post them back in batches (one doorbell each) */
	return n;
}

/* one poll pass over rings 3 and 4; returns the items handled. The caller (a kernel thread or the test) owns the period. */
/* Part A (start model): a tx buffer and a flow slot are free for the next frame (book slot flow_wr % 16 unused, the flow ring not full) */
static inline int en0_tx_room(const struct en0 *e)
{
	const struct swdp_ring *r = &e->flow.ring;
	if (r->depth == 0u || e->txb.used[r->wr % SWDP_NSLOT])
		return 0;
	return (r->rd + r->depth - r->wr - 1u) % r->depth != 0u;
}
static inline void en0_start(ifnet_t ifp);
static inline uint32_t en0_poll(struct en0 *e)
{
	uint32_t n, nrx;
	if (e->cons_fail)
		return 0u;
	n = en0_poll_tx(e);
	if (e->tx_blocked && en0_tx_room(e)) {   /* Part A: en0_start stopped for room; the completions freed some: run it here, in this pass (the frames wait in the interface's FQ-CoDel queue, not in en0) */
		e->tx_blocked = 0u;
		en0_start(e->ifp);
	}
	nrx = en0_poll_rx(e);
	if (nrx > 0u && e->ifp != NULL)
		en0_start(e->ifp);               /* perf-f2: the TCP ACKs the delivered frames produced are in the send queue now: post them in this pass instead of waiting for DLIL's starter thread (wlan-perf-f: download 95.8 Mbit/s with every ACK handed to that thread) */
	return n + nrx;
}

/* ------------------------------------------------------------------ EN6: TX output */
static inline int en0_tx_post(struct en0 *e, uint32_t len)
{
	struct swdp_ring r = e->flow.ring;
	struct swdp_store st[SWDP_MAX_STORES];
	uint32_t rid = swdp_book_take_at(&e->txb, e->flow.ring.wr % SWDP_NSLOT, len - SWDP_ETH_HLEN), slot, n;     /* sprint 2026-11-08: flow slot n carries tx buffer n % 16 (the L25 judge's rule; run wlan-iosping-b: buffer 0 at flow slot 14 was refused); a busy buffer = a counted drop */
	uint64_t txa;
	uint8_t epoch;
	if (rid == 0u)
		return -1;
	txa = e->env.txbuf.pa + (uint64_t)(rid - SWDP_TX_RID) * e->env.buf_sz;
	if (swdp_ring_post(&r, &slot, &epoch)) {
		swdp_book_done(&e->txb, rid, NULL);
		return -2;                              /* flow ring full: the caller queues the frame */
	}
	if (swdp_build_txpost(e->item, e->env.flow_isz, 0u, rid, e->scratch, len, 0u, txa)) {
		swdp_book_done(&e->txb, rid, NULL);
		return -4;
	}
	en0_sync(e);
	n = swdp_plan_tx(&e->env, txa, e->scratch + SWDP_ETH_HLEN, len - SWDP_ETH_HLEN, slot, r.wr, e->item, st);
	if (en0_run_plan(e, st, n)) {
		swdp_book_done(&e->txb, rid, NULL);
		return -3;
	}
	e->flow.ring = r;
	return 0;
}

/* one frame of mbuf m onto the flow ring (lock held): 0 posted; -1 no free tx buffer / -2 flow ring full (both: retry later); -3 plan refused, -4 bad frame, -8 copy failed */
static inline int en0_tx_try(struct en0 *e, mbuf_t m, uint32_t len)
{
	if (mbuf_copydata(m, 0, len, e->scratch) != 0)
		return -8;
	return en0_tx_post(e, len);
}

/* patch 0282: one raw Ethernet frame f[0..len) on the flow ring (the EAPOL reply of the re-join), lock held: 0 posted, -1 no free tx buffer, -2 flow ring full, -4 bad length, -5 flow ring not open, else en0_tx_post's code.
 * *slot = the flow slot it took (= its tx buffer, en0_tx_post's rule): en0_tx_busy(e, slot) is 1 until its tx completion was seen. */
static inline int en0_tx_frame(struct en0 *e, const uint8_t *f, uint32_t len, uint32_t *slot)
{
	if (len < SWDP_ETH_HLEN || len > SWDP_ETH_MAXFRAME)
		return -4;
	if (e->flow.st != SWDP_FLOW_OPEN)
		return -5;
	if (!en0_tx_room(e))
		return -1;
	*slot = e->flow.ring.wr % SWDP_NSLOT;
	memcpy(e->scratch, f, len);
	return en0_tx_post(e, len);
}
static inline int en0_tx_busy(const struct en0 *e, uint32_t slot)
{
	return e->txb.used[slot % SWDP_NSLOT] != 0;
}

/* Part A (sprint-wifi 2026-11-09): the ifnet START model. The stack queues en0's frames in the interface send queue, which DLIL runs with FQ-CoDel for an IFEF_TXSTART interface
 * [src xnu-8796 bsd/net/classq/classq_subr.c ifclassq_pktsched_setup: pktsched_setup(ifq, PKTSCHEDT_FQ_CODEL, ...)]: one flow (a bulk TCP upload) no longer delays another (a ping reply) and the queue
 * delay is kept low (CoDel). en0_start (DLIL's starter thread, or the poll after tx completions) takes a frame only while a tx buffer and a flow slot are free, so en0 holds no frame of
 * its own; every frame is posted through the same audited plan (en0_tx_post) as before. bpf: DLIL does not tap a TXSTART interface's output, en0 taps it here. */
static inline void en0_start(ifnet_t ifp)
{
	struct en0 *e = en0_self;
	mbuf_t m;
	uint32_t len, nd = 0u, posted = 0u;
	int rc;
	if (e == NULL)
		return;
	EN0_LOCK();
	for (;;) {
		if (e->flow.st != SWDP_FLOW_OPEN) {       /* the flow ring is down: what the stack queued can never go out: freed and counted */
			while (ifnet_dequeue(ifp, &m) == 0) {
				mbuf_freem(m);
				nd++;
			}
			e->tx_drop += nd;
			if (nd > 0u) {
				ifnet_stat_increment_out(ifp, 0, 0, nd);
				en0_gapf("WIFI GAP: EN6 tx frames dropped because the flow ring is down, frames", nd);
			}
			break;
		}
		if (!en0_tx_room(e)) {
			e->tx_blocked = 1u;               /* the poll calls EN0_IFSTART after the next tx completion */
			e->tx_waits++;
			break;
		}
		if (ifnet_dequeue(ifp, &m) != 0)
			break;
		len = (uint32_t)mbuf_pkthdr_len(m);
		if (len < SWDP_ETH_HLEN || len > SWDP_ETH_MAXFRAME) {
			rc = -4;
		} else {
			bpf_tap_out(ifp, DLT_EN10MB, m, NULL, 0);
			rc = en0_tx_try(e, m, len);
		}
		mbuf_freem(m);                            /* always, exactly once (EN6) */
		if (rc == 0) {
			ifnet_stat_increment_out(ifp, 1, len, 0);
			e->tx_frames++;
			posted++;
		} else {
			ifnet_stat_increment_out(ifp, 0, 0, 1);
			e->tx_drop++;
			en0_gapf("WIFI GAP: EN6 tx dropped (bad length / audit / copy), code", (uint32_t)(-rc));
		}
	}
	EN0_UNLOCK();
	if (posted > 0u)
		EN0_KICK();
}

/* ------------------------------------------------------------------ EN8/EN9: link state and the static address */
static inline void en0_post_link(struct en0 *e, uint32_t code)
{
	/* sprint 2026-11-08 (run wlan-iosping-a: __memcpy_chk panic "0 < 24"): in the kernel build event_data is a VLA (XNU_KERN_EVENT_DATA_IS_VLA, bsd/sys/kern_event.h:121-157), so the data words
	 * go into a char buffer right after the header, the pattern of XNU's own interface_link_event (bsd/net/if_vlan.c:2047-2049) */
	_Alignas(struct kern_event_msg) char msg[sizeof(struct kern_event_msg) + sizeof(struct net_event_data)];
	struct kern_event_msg *ev = (struct kern_event_msg *)(void *)msg;
	struct net_event_data nd;
	memset(msg, 0, sizeof msg); memset(&nd, 0, sizeof nd);
	nd.if_family = IFNET_FAMILY_ETHERNET; nd.if_unit = ifnet_unit(e->ifp);
	snprintf(nd.if_name, sizeof nd.if_name, "%s", ifnet_name(e->ifp));
	ev->total_size = KEV_MSG_HEADER_SIZE + sizeof nd;
	ev->vendor_code = KEV_VENDOR_APPLE; ev->kev_class = KEV_NETWORK_CLASS; ev->kev_subclass = KEV_DL_SUBCLASS; ev->event_code = code;
	memcpy(msg + KEV_MSG_HEADER_SIZE, &nd, sizeof nd);
	if (ifnet_event(e->ifp, ev) != 0)
		SWDP_GAP("WIFI GAP: EN8 ifnet_event failed: link state not delivered to the protocols");
}

/* patch 0281 (W-D6): the AP link is lost (or back) but the bus, the engine and the flow ring are fine: only the ifnet side changes, IFF_RUNNING, KEV_DL_LINK_OFF / ON and link_up (what SIOCGIFMEDIA answers). The flow ring is NOT failed (en0_live_poll does that
 * for a dead engine) and the boot address is not applied again (en0_set_link does it once at the hand-over). Call outside the en0 lock like the hand-over's en0_set_link: the kernel event may make the stack transmit. */
static inline void en0_link_only(struct en0 *e, uint32_t up)
{
	if (!e->attached)
		return;
	e->link_up = up;
	ifnet_set_flags(e->ifp, up ? IFF_RUNNING : 0, IFF_RUNNING);
	en0_post_link(e, up ? KEV_DL_LINK_ON : KEV_DL_LINK_OFF);
	SWDP_GAP(EN0_GAP_EN8);
}

static inline void en0_set_link(struct en0 *e, uint32_t up)
{
	struct en0_cfg c;
	if (!e->attached)
		return;
	e->link_up = up;
	ifnet_set_flags(e->ifp, up ? IFF_RUNNING : 0, IFF_RUNNING);
	en0_post_link(e, up ? KEV_DL_LINK_ON : KEV_DL_LINK_OFF);
	SWDP_GAP(EN0_GAP_EN8);
	if (up && !e->addr_done && en0_boot_cfg(e->bootarg, &c) == 0) {
		if (e->apply != NULL && e->apply(e->ifp, &c) == 0) e->addr_done = 1u;
		else SWDP_GAP("WIFI GAP: EN9 static address not applied (SIOCAIFADDR failed or no apply hook): en0 stays unaddressed");
	}
}

/* ------------------------------------------------------------------ EN10: ioctls */
static inline errno_t en0_ioctl(ifnet_t ifp, unsigned long cmd, void *data)
{
	struct en0 *e = en0_self;
	struct ifmediareq *mr = (struct ifmediareq *)data;
	char b[96];
	uint32_t i;
	switch (cmd) {
	case SIOCSIFADDR: case SIOCAIFADDR:
		ifnet_set_flags(ifp, IFF_UP, IFF_UP);
		return 0;
	case SIOCSIFFLAGS:
		return 0;
	case SIOCADDMULTI: case SIOCDELMULTI:
		SWDP_GAP(EN0_GAP_EN10M);
		return 0;
	case SIOCGIFMEDIA: {
		/* W-UI (sprint 2026-11-08): one supported media word, as ifmedia_ioctl answers (count = number of words; the words are copied out when the caller passes a list).
		 * count 0 made configd skip the interface's link state [obs-RE iOS 16.7 usr/libexec/configd sub_100012f44: ioctl SIOCGIFXMEDIA64 0x100012fc4, -1 -> no Active
		 * 0x100012fd0, `ldr w8,[sp,#0x30]` = ifm_count, cbz -> no Active 0x100012fd4-0x100012fd8, then IFM_AVALID bit 0 / IFM_ACTIVE bit 1 0x100012fdc-0x100013004;
		 * kSCEntNetLink key 0x1000130d4] [obs run wlan-wui-e: no State:/Network/Interface/en0/Link key while IPv4/AirPort exist; IPConfiguration sent no DHCP]. */
		int w = IFM_IEEE80211 | IFM_AUTO, rc;
		mr->ifm_current = mr->ifm_active = w;
		mr->ifm_status = IFM_AVALID | ((e != NULL && e->link_up) ? IFM_ACTIVE : 0);
		if (mr->ifm_count > 0 && mr->ifm_ulist != NULL) {
			rc = EN0_COPYOUT(&w, mr->ifm_ulist, sizeof w);
			if (rc != 0) {
				snprintf(b, sizeof b, "WIFI GAP: EN10 SIOCGIFMEDIA media list copyout failed (%d)", rc);
				SWDP_GAP(b);
				return rc;
			}
		}
		mr->ifm_count = 1;
		return 0;
	}
	default:
		for (i = 0; e != NULL && i < e->ioctl_n; i++)
			if (e->ioctl_seen[i] == cmd)
				return EOPNOTSUPP;                   /* its GAP line was printed the first time */
		if (e != NULL && e->ioctl_n < EN0_IOCTL_SEEN)
			e->ioctl_seen[e->ioctl_n++] = cmd;
		snprintf(b, sizeof b, "WIFI GAP: EN10 en0 ioctl 0x%lx unsupported", cmd);
		SWDP_GAP(b);
		return EOPNOTSUPP;
	}
	return EOPNOTSUPP;
}

static inline void en0_detach(ifnet_t ifp) { (void)ifp; }

/* ------------------------------------------------------------------ EN1/EN2/EN5/EN11: allocate, attach */
static inline int en0_attach(struct en0 *e, const uint8_t *mac)
{
	struct ifnet_init_eparams init;
	if (mac == NULL) {
		SWDP_GAP(EN0_GAP_EN2);
		return -1;
	}
	en0_self = e;
	memset(&init, 0, sizeof init);
	init.ver = IFNET_INIT_CURRENT_VERSION; init.len = sizeof init;
	init.flags = IFNET_INIT_NX_NOAUTO;                          /* Part A: the start model (no IFNET_INIT_LEGACY): DLIL's FQ-CoDel send queue + en0_start */
	init.uniqueid = "sweet-wlan-en0"; init.uniqueid_len = sizeof("sweet-wlan-en0") - 1u;
	init.name = "en"; init.unit = 0; init.family = IFNET_FAMILY_ETHERNET; init.type = IFT_ETHER;
	init.subfamily = IFNET_SUBFAMILY_WIFI;   /* T-28b: if_functional_type() = WIFI_INFRA only for ETHERNET + subfamily WIFI [src xnu-8796 bsd/net/if.c:884-891];
	                                          * NECP hands it to libnetwork (necp_client.c:8937), nw_interface type 1 = wifi, and
	                                          * NWSystemPathMonitor wifiPrimary = default path usesInterfaceType:1 (RE map RM-01045..01050).
	                                          * Subfamily 0 made en0 WIRED: wifiPrimary NO, SpringBoard status bar had no Wi-Fi item. */
	init.start = en0_start; init.output_sched_model = 0u /* IFNET_SCHED_MODEL_NORMAL [src xnu-8796 bsd/net/if_private.h:494] */; init.sndq_maxlen = 0u;   /* 0 = if_sndq_maxlen (ifclassq_set_maxlen) */
	init.ioctl = en0_ioctl; init.detach = en0_detach;
	init.demux = ether_demux; init.add_proto = ether_add_proto; init.del_proto = ether_del_proto;
	init.check_multi = ether_check_multi; init.framer_extended = ether_frameout_extended;
	init.broadcast_addr = etherbroadcastaddr; init.broadcast_len = EN0_ALEN;
	if (ifnet_allocate_extended(&init, &e->ifp) != 0) {
		SWDP_GAP("WIFI GAP: EN1 ifnet_allocate_extended failed: en0 not created");
		return -1;
	}
	ifnet_set_addrlen(e->ifp, EN0_ALEN); ifnet_set_hdrlen(e->ifp, SWDP_ETH_HLEN); ifnet_set_mtu(e->ifp, EN0_MTU);
	ifnet_set_baudrate(e->ifp, EN0_BAUD);
	ifnet_set_flags(e->ifp, IFF_BROADCAST | IFF_SIMPLEX | IFF_MULTICAST, 0xffff);
	if (ifnet_attach(e->ifp, NULL) != 0) {
		SWDP_GAP("WIFI GAP: EN2 ifnet_attach failed: en0 not attached");
		return -1;
	}
	ifnet_set_lladdr(e->ifp, mac, EN0_ALEN);          /* after attach, from the caller; never a default (EN2) */
	if (bpf_attach(e->ifp, DLT_EN10MB, SWDP_ETH_HLEN, NULL, NULL) != 0)
		SWDP_GAP(EN0_GAP_EN5);
	e->attached = 1u;
	{
		char b[96];
		snprintf(b, sizeof b, "SWEET WLAN: en0 attached if_xflags=0x%x if_eflags=0x%x subfamily %u", (unsigned)EN0_XFLAGS(e->ifp), (unsigned)EN0_EFLAGS(e->ifp), (unsigned)ifnet_subfamily(e->ifp));
		SWDP_LOG(b);
	}
	if (ifnet_subfamily(e->ifp) != IFNET_SUBFAMILY_WIFI)
		SWDP_GAP("WIFI GAP: EN13 en0 subfamily is not WIFI: the kernel reports en0 as WIRED, NWPath wifiPrimary stays NO, no status-bar Wi-Fi item (T-28b)");
	if (!(EN0_XFLAGS(e->ifp) & EN0_XF_NOAUTO))
		SWDP_GAP(EN0_GAP_EN1);
	if (EN0_EFLAGS(e->ifp) & EN0_EF_ENQUEUE_MULTI)
		SWDP_GAP("WIFI GAP: EN11 IFEF_ENQUEUE_MULTI is set on en0 (E-4 says do not set it)");
	SWDP_GAP(EN0_GAP_EN7); SWDP_GAP(EN0_GAP_EN12);
	return 0;
}

#endif /* WLAN_EN0_H */
