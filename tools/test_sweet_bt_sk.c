/* Host test of patch 0286 (BT Skywalk UART channel): the REAL bsd/dev/sweet_bt.c (patches 0189, 0213, 0286) and bsd/dev/sweet_bt_sk.c against stubs of the kernel control and the
 * kern_nexus/kern_channel/kern_packet API (tools/test_sweet_bt_sk_stub.h, modelled on channel_kern.c / os_packet.h). A fake bluetoothd drives the two sockopts, the provider
 * callbacks and the slot rings the way the kernel does (slot KPIs only inside a sync callback), a fake UART backend records TX bytes and feeds RX bytes.
 * Covers: sockopt answers, nexus creation order + failure teardown, TX bytes across slots in order, RX buffering + notify + wrap/overflow, disconnect/reconnect, the
 * notify-vs-pre_disconnect race (real threads), sweetbtsk=0. No phone, no network. */
#include <unistd.h>
#include "kstub.h"
#include "skstub.h"
#include "sweet_bt.c"
#include "sweet_bt_sk.c"
#undef printf

/* ---- log ---- */
static char logbuf[1 << 20]; static size_t loglen;
static pthread_mutex_t logm = PTHREAD_MUTEX_INITIALIZER;
int sbt_printf(const char *fmt, ...) { va_list ap; pthread_mutex_lock(&logm); va_start(ap, fmt); int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap); va_end(ap); if (n > 0) loglen += (size_t)n; pthread_mutex_unlock(&logm); return n; }
static void log_reset(void) { pthread_mutex_lock(&logm); loglen = 0; logbuf[0] = 0; pthread_mutex_unlock(&logm); }
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stdout, "FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
#define CHECKL(c, s) do { if (!(c)) { fprintf(stdout, "FAIL line %d: %s (log %s)\n", __LINE__, #c, s); fails++; } } while (0)

/* ---- kctl / kernel stubs ---- */
static struct kern_ctl_reg regs[4]; static int nreg;
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { regs[nreg] = *r; *ref = (kern_ctl_ref)(uintptr_t)(nreg + 1); nreg++; return 0; }
static int enq_n; static uint8_t enq_buf[1 << 16]; static size_t enq_len;
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r; (void)u; (void)f; enq_n++; if (enq_len + l <= sizeof(enq_buf)) memcpy(enq_buf + enq_len, d, l); enq_len += l; return 0; }
void mbuf_freem(mbuf_t m) { (void)m; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 0; }
static int knob_sk = -1;     /* -1 = boot-arg absent */
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l; if (!strcmp(n, "sweetbtsk") && knob_sk >= 0) { *(int *)v = knob_sk; return 1; } return 0; }
static int attach_calls; void sweet_bt_uart_attach(void) { attach_calls++; }
static int disc_calls; void sweet_bt_uart_disc(void) { disc_calls++; }
void proc_selfname(char *b, int n) { snprintf(b, (size_t)n, "bluetoothd"); }
int msleep(void *chan, lck_mtx_t *mtx, int pri, const char *w, void *ts);
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
int msleep(void *chan, lck_mtx_t *mtx, int pri, const char *w, void *ts) { (void)chan; (void)pri; (void)w; (void)ts; pthread_cond_wait(&cv, &mtx->m); return 0; }
void wakeup(void *chan) { (void)chan; pthread_cond_broadcast(&cv); }

/* ---- Skywalk model ---- */
#define NS 64
struct kbuflet { uint8_t data[1024]; uint16_t off, len; };
struct kpkt { struct kbuflet b; bool used, fin; };
struct kslot { uint64_t pkt; };
struct kring { int is_tx; unsigned lim, khead, ktail, rhead, ucur; struct kslot ks[NS]; };
struct kpp { int dummy; };
static struct kpkt pk[3 * NS];
static struct kring txr, rxr;
static struct kpp txpp, rxpp;
static int in_sync;
#define NEEDSYNC() do { if (!in_sync) { fprintf(stdout, "FAIL slot KPI %s outside a sync callback\n", __func__); fails++; } } while (0)
static unsigned nxt(unsigned i) { return (i + 1) % NS; }

static const char *calls[64]; static int ncalls; static int call_seq; static int fail_call; static errno_t fail_err = ENOMEM;
static uint64_t attr_val[16]; static int attr_destroyed, ctl_destroyed, dereg_n, create_n;
static struct kern_nexus_provider_init P; static int have_P; static const void *inst_ctx;
static int pbuf_fail, alloc_fail, notify_n[2]; static uint32_t notify_ret; static void (*notify_hook)(void);
static uint32_t stat_slots, stat_bytes; static int notify_with_lock;
static struct knexus { int d; } NX; static struct kchan { int d; } CH, CH2; static struct kncd { int d; } NCD; static struct knxa { int d; } NXA;
static int inject(const char *name) { calls[ncalls++] = name; call_seq++; return fail_call == call_seq; }

errno_t kern_nexus_controller_create(nexus_controller_t *c) { if (inject("ctl_create")) return fail_err; *c = &NCD; return 0; }
errno_t kern_nexus_get_default_domain_provider(nexus_type_t t, uuid_t *u) { if (inject("default_domain")) return fail_err; CHECK(t == NEXUS_TYPE_KERNEL_PIPE); memset(*u, 0xD0, 16); return 0; }
errno_t kern_nexus_attr_create(nexus_attr_t *a) { if (inject("attr_create")) return fail_err; *a = &NXA; return 0; }
errno_t kern_nexus_attr_set(nexus_attr_t a, nexus_attr_type_t t, uint64_t v) { (void)a; if (inject("attr_set")) return fail_err; if (t >= 0 && t < 16) attr_val[t] = v; return 0; }
void kern_nexus_attr_destroy(nexus_attr_t a) { (void)a; calls[ncalls++] = "attr_destroy"; attr_destroyed++; }
errno_t kern_nexus_controller_register_provider(nexus_controller_t c, const uuid_t d, const uint8_t *name, const struct kern_nexus_provider_init *i, uint32_t l, nexus_attr_t a, uuid_t *u) {
	(void)c; (void)a;
	if (inject("register")) return fail_err;
	CHECK(l == sizeof(*i)); CHECK(i->nxpi_version == KERN_NEXUS_DOMAIN_PROVIDER_CURRENT_VERSION); CHECK(d[0] == 0xD0);
	CHECK(!strcmp((const char *)name, "com.apple.uart.sk.bluetooth"));
	/* nexus_kern.c:867-890: required callbacks, symmetric pairs, no tx doorbell for kpipe */
	if (!i->nxpi_sync_tx || !i->nxpi_sync_rx || !i->nxpi_pre_connect || !i->nxpi_connected || !i->nxpi_pre_disconnect || !i->nxpi_disconnected ||
	    (!i->nxpi_ring_init != !i->nxpi_ring_fini) || (!i->nxpi_slot_init != !i->nxpi_slot_fini) || i->nxpi_tx_doorbell) { fprintf(stdout, "FAIL provider init rejected by nexus_kern.c rules\n"); fails++; return EINVAL; }
	P = *i; have_P = 1; memset(*u, 0xB0, 16); return 0; }
errno_t kern_nexus_controller_deregister_provider(nexus_controller_t c, const uuid_t u) { (void)c; CHECK(u[0] == 0xB0); calls[ncalls++] = "deregister"; dereg_n++; return 0; }
errno_t kern_nexus_controller_alloc_provider_instance(nexus_controller_t c, const uuid_t pu, const void *ctx, void *rel, uuid_t *u, const struct kern_nexus_init *ni) {
	(void)c; (void)rel;
	if (inject("alloc_instance")) return fail_err;
	CHECK(pu[0] == 0xB0); CHECK(ctx != NULL); CHECK(ni->nxi_version == KERN_NEXUS_CURRENT_VERSION); CHECK(ni->nxi_tx_pbufpool == NULL);
	inst_ctx = ctx; for (int i = 0; i < 16; i++) (*u)[i] = (unsigned char)(0xA0 + i); create_n++; return 0; }
void kern_nexus_controller_destroy(nexus_controller_t c) { (void)c; calls[ncalls++] = "ctl_destroy"; ctl_destroyed++; }
errno_t kern_nexus_get_pbufpool(const kern_nexus_t n, kern_pbufpool_t *t, kern_pbufpool_t *r) { (void)n; if (pbuf_fail) return ENOTSUP; if (t) *t = &txpp; if (r) *r = &rxpp; return 0; }

uint32_t kern_channel_notify(const kern_channel_ring_t r, uint32_t f) {
	(void)f; notify_n[r->is_tx]++;
	if (pthread_mutex_trylock(&sbsk_lock.m) == 0) pthread_mutex_unlock(&sbsk_lock.m); else notify_with_lock++;
	if (notify_hook) notify_hook();
	return notify_ret; }
uint32_t kern_channel_reclaim(const kern_channel_ring_t r) { if (!r->is_tx) r->khead = r->ucur; return 0; }
kern_channel_slot_t kern_channel_get_next_slot(const kern_channel_ring_t r, const kern_channel_slot_t s0, struct kern_slot_prop *p) {
	NEEDSYNC(); (void)p; unsigned idx = s0 == NULL ? (r->is_tx ? r->khead : r->ktail) : nxt((unsigned)(s0 - r->ks));
	if (r->is_tx) { if (r->rhead == idx) return NULL; } else { if (r->khead == nxt(idx)) return NULL; }
	return &r->ks[idx]; }
void kern_channel_advance_slot(const kern_channel_ring_t r, kern_channel_slot_t s) { NEEDSYNC(); unsigned idx = (unsigned)(s - r->ks); if (r->is_tx) r->khead = nxt(idx); else r->ktail = nxt(idx); }
void kern_channel_increment_ring_stats(kern_channel_ring_t r, struct kern_channel_ring_stat_increment *s) { (void)r; stat_slots += s->kcrsi_slots_transferred; stat_bytes += s->kcrsi_bytes_transferred; }
kern_packet_t kern_channel_slot_get_packet(const kern_channel_ring_t r, const kern_channel_slot_t s) { (void)r; NEEDSYNC(); return s->pkt; }
errno_t kern_channel_slot_attach_packet(const kern_channel_ring_t r, const kern_channel_slot_t s, kern_packet_t ph) { (void)r; NEEDSYNC(); s->pkt = ph; return 0; }
kern_buflet_t kern_packet_get_next_buflet(const kern_packet_t ph, const kern_buflet_t prev) { return prev != NULL ? NULL : &((struct kpkt *)(uintptr_t)ph)->b; }
uint8_t *kern_buflet_get_data_address(const kern_buflet_t b) { return b->data; }
uint16_t kern_buflet_get_data_length(const kern_buflet_t b) { return b->len; }
uint16_t kern_buflet_get_data_offset(const kern_buflet_t b) { return b->off; }
errno_t kern_buflet_set_data_length(const kern_buflet_t b, uint16_t l) { if (l > sizeof(b->data)) return ERANGE; b->len = l; return 0; }
errno_t kern_buflet_set_data_offset(const kern_buflet_t b, uint16_t o) { if (o > sizeof(b->data)) return ERANGE; b->off = o; return 0; }
errno_t kern_packet_finalize(const kern_packet_t ph) { ((struct kpkt *)(uintptr_t)ph)->fin = true; return 0; }
errno_t kern_pbufpool_alloc_nosleep(const kern_pbufpool_t pp, uint32_t n, kern_packet_t *ph) {
	CHECK(pp == &rxpp); CHECK(n == 1);
	if (alloc_fail > 0) { alloc_fail--; return ENOMEM; }
	for (size_t i = 0; i < sizeof(pk) / sizeof(pk[0]); i++) if (!pk[i].used) { memset(&pk[i], 0, sizeof(pk[i])); pk[i].used = true; *ph = (kern_packet_t)(uintptr_t)&pk[i]; return 0; }
	return ENOMEM; }
void kern_pbufpool_free(const kern_pbufpool_t pp, kern_packet_t ph) { (void)pp; ((struct kpkt *)(uintptr_t)ph)->used = false; }

/* ---- fake UART backend ---- */
static uint8_t txlog[1 << 20]; static size_t txlen; static int txcalls, tx_fail_on, termios_set_n;
static size_t txsizes[64];
static int f_tx(const void *d, size_t l) { txcalls++; if (tx_fail_on && txcalls == tx_fail_on) return EIO; if (txcalls <= 64) txsizes[txcalls - 1] = l; memcpy(txlog + txlen, d, l); txlen += l; return 0; }
static int f_tset(const void *b, size_t l) { (void)b; (void)l; termios_set_n++; return 0; }
static const struct sweet_bt_ops fops = { .tx = f_tx, .termios_set = f_tset };

/* ---- harness ---- */
static kern_ctl_ref REF_PLAIN = (kern_ctl_ref)1, REF_SK = (kern_ctl_ref)2;
static void *uinfo;
static errno_t ctl_connect(int i) { struct sockaddr_ctl sac = { .sc_unit = 7 }; return regs[i].ctl_connect(i ? REF_SK : REF_PLAIN, &sac, &uinfo); }
static errno_t ctl_disconnect(int i) { return regs[i].ctl_disconnect(i ? REF_SK : REF_PLAIN, 7, uinfo); }
static errno_t ksetopt(int i, u_int32_t opt, void *d, size_t l) { return regs[i].ctl_setopt(i ? REF_SK : REF_PLAIN, 7, uinfo, (int)opt, d, l); }
static errno_t kgetopt(int i, u_int32_t opt, void *d, size_t *l) { return regs[i].ctl_getopt(i ? REF_SK : REF_PLAIN, 7, uinfo, (int)opt, d, l); }
#define SETSPEC 0x800c5414u
#define GETUUID 0x40105412u

static void ring_reset(void) { memset(&txr, 0, sizeof(txr)); memset(&rxr, 0, sizeof(rxr)); txr.is_tx = 1; txr.lim = rxr.lim = NS - 1; memset(pk, 0, sizeof(pk)); }
static void sync_tx(errno_t *e) { in_sync = 1; *e = P.nxpi_sync_tx(NULL, &NX, &txr, 0); in_sync = 0; }
static void sync_rx(errno_t *e) { in_sync = 1; *e = P.nxpi_sync_rx(NULL, &NX, &rxr, 0); in_sync = 0; }
static errno_t chan_open(void) {
	void *cc; errno_t e;
	ring_reset();
	if ((e = P.nxpi_pre_connect(NULL, NULL, &NX, 0, &CH, &cc)) != 0) return e;
	CHECK(P.nxpi_ring_init(NULL, &NX, &CH, &txr, 1, &cc) == 0); CHECK(P.nxpi_ring_init(NULL, &NX, &CH, &rxr, 0, &cc) == 0);
	return P.nxpi_connected(NULL, &NX, &CH); }
static void chan_close(void) { P.nxpi_pre_disconnect(NULL, &NX, &CH); P.nxpi_ring_fini(NULL, &NX, &rxr); P.nxpi_ring_fini(NULL, &NX, &txr); P.nxpi_disconnected(NULL, &NX, &CH); }
static void tx_put(const void *d, size_t n, unsigned off) {      /* the user wrote a slot: buffer at offset `off`, n bytes (os_channel_set_slot_properties) */
	size_t i; for (i = 0; i < sizeof(pk) / sizeof(pk[0]) && pk[i].used; i++) {}
	pk[i].used = true; pk[i].b.off = (uint16_t)off; pk[i].b.len = (uint16_t)n; if (d) memcpy(pk[i].b.data + off, d, n);
	txr.ks[txr.rhead].pkt = (kern_packet_t)(uintptr_t)&pk[i]; txr.rhead = nxt(txr.rhead); }
static size_t rx_take(uint8_t *out, size_t cap, unsigned *nslots, size_t *maxlen) {       /* the user reads every filled slot and releases it */
	size_t n = 0; *nslots = 0; *maxlen = 0;
	for (unsigned i = rxr.ucur; i != rxr.ktail; i = nxt(i)) {
		struct kpkt *p = (struct kpkt *)(uintptr_t)rxr.ks[i].pkt;
		CHECK(p != NULL && p->fin && p->b.off == 0);
		if (p && n + p->b.len <= cap) memcpy(out + n, p->b.data, p->b.len);
		if (p) { n += p->b.len; if (p->b.len > *maxlen) *maxlen = p->b.len; p->used = false; }
		rxr.ks[i].pkt = 0; (*nslots)++; }
	rxr.ucur = rxr.ktail; return n; }
static void pat(uint8_t *b, size_t n, unsigned seed) { for (size_t i = 0; i < n; i++) b[i] = (uint8_t)(seed * 31u + i * 7u + (i >> 8)); }
static int calls_have(const char *s) { for (int i = 0; i < ncalls; i++) if (!strcmp(calls[i], s)) return 1; return 0; }
static int calls_idx(const char *s) { for (int i = 0; i < ncalls; i++) if (!strcmp(calls[i], s)) return i; return -1; }
static void nexus_reset(void) { memset(&sbsk, 0, sizeof(sbsk)); sbsk.enabled = true; ncalls = 0; call_seq = 0; fail_call = 0; attr_destroyed = ctl_destroyed = dereg_n = create_n = 0; have_P = 0; memset(attr_val, 0, sizeof(attr_val)); }

static pthread_t thr; static volatile int thr_done, thr_started;
static void *thr_main(void *a) { (void)a; thr_started = 1; P.nxpi_pre_disconnect(NULL, &NX, &CH); thr_done = 1; return NULL; }
static int hook_checked_blocked;
static void race_hook(void) {       /* inside kern_channel_notify: bluetoothd destroys the channel on another thread; pre_disconnect must wait for us */
	pthread_create(&thr, NULL, thr_main, NULL);
	for (int i = 0; i < 200 && !thr_started; i++) usleep(1000);
	usleep(80000);
	hook_checked_blocked = !thr_done; }

int main(void)
{
	static uint8_t in[1 << 17], out[1 << 17];
	errno_t e; unsigned ns; size_t ml, n;
	uint8_t spec[12]; uint32_t sp3[3] = { 1024, 63, 63 };

	/* ---- 1. init, boot-arg ---- */
	sweet_bt_init();
	CHECK(nreg == 2 && attach_calls == 1);
	CHECK(has("BT SK: skywalk channel on")); CHECK(!has("rows M12-M14 not implemented"));
	CHECK(sbsk.enabled);
	sweet_bt_set_ops(&fops);
	CHECK(ctl_connect(1) == 0);
	knob_sk = 0; log_reset(); sweet_bt_sk_attach();
	CHECK(!sbsk.enabled && has("BT GAP: skywalk rows M12-M14 off (sweetbtsk=0)"));
	memcpy(spec, sp3, 12);
	log_reset(); ncalls = 0;
	CHECK(ksetopt(1, SETSPEC, spec, 12) == ENOPROTOOPT && has("kctl setsockopt 0x800c5414 unknown"));
	{ size_t l = 16; uint8_t u[16]; CHECK(kgetopt(1, GETUUID, u, &l) == ENOPROTOOPT && has("kctl getsockopt 0x40105412 unknown")); }
	CHECK(ncalls == 0);
	knob_sk = 1; sweet_bt_sk_attach(); CHECK(sbsk.enabled);
	knob_sk = -1; sweet_bt_sk_attach(); CHECK(sbsk.enabled);
	CHECK(ctl_disconnect(1) == 0);
	CHECK(ctl_connect(0) == 0);
	log_reset(); CHECK(ksetopt(0, SETSPEC, spec, 12) == ENOPROTOOPT && has("kctl setsockopt 0x800c5414 unknown") && ncalls == 0);      /* BlueTool's plain control keeps today's answer */
	CHECK(ctl_disconnect(0) == 0);

	/* ---- 2. sockopts + creation ---- */
	nexus_reset(); CHECK(ctl_connect(1) == 0); log_reset();
	CHECK(ksetopt(1, SETSPEC, spec, 8) == EINVAL && has("length 8 is not 12") && ncalls == 0);
	CHECK(ksetopt(1, SETSPEC, spec, 12) == 0);
	CHECK(has("IOAOSSKYSETCHANNELSPEC 1024 63 63") && !has("differs from"));
	{ const char *want[] = { "ctl_create", "default_domain", "attr_create", "attr_set", "attr_set", "attr_set", "attr_set", "attr_set", "attr_set", "register", "alloc_instance", "attr_destroy" };
	  CHECK(ncalls == 12); for (int i = 0; i < 12 && i < ncalls; i++) CHECKL(!strcmp(calls[i], want[i]), calls[i]); }
	CHECK(attr_val[NEXUS_ATTR_TX_RINGS] == 1 && attr_val[NEXUS_ATTR_RX_RINGS] == 1 && attr_val[NEXUS_ATTR_TX_SLOTS] == 64 && attr_val[NEXUS_ATTR_RX_SLOTS] == 64 &&
	      attr_val[NEXUS_ATTR_SLOT_BUF_SIZE] == 1024 && attr_val[NEXUS_ATTR_ANONYMOUS] == 1);
	CHECK(has("BT SK: kernel-pipe nexus instance a0a1a2a3-a4a5-a6a7-a8a9-aaabacadaeaf ready"));
	CHECK(inst_ctx == &sbsk && create_n == 1);
	sp3[0] = 512; sp3[1] = 10; sp3[2] = 10; memcpy(spec, sp3, 12); log_reset();
	CHECK(ksetopt(1, SETSPEC, spec, 12) == 0 && has("BT GAP: IOAOSSKYSETCHANNELSPEC 512 10 10 differs from bluetoothd's 1024 63 63") && ncalls == 12 && create_n == 1);
	{ uint8_t u[32]; size_t l = 16; memset(u, 0, sizeof(u));
	  CHECK(kgetopt(1, GETUUID, u, &l) == 0 && l == 16); for (int i = 0; i < 16; i++) CHECK(u[i] == 0xA0 + i); CHECK(u[16] == 0);
	  l = 32; CHECK(kgetopt(1, GETUUID, u, &l) == 0 && l == 16);
	  l = 8; log_reset(); CHECK(kgetopt(1, GETUUID, u, &l) == EINVAL && has("IOAOSSKYGETCHANNELUUID buffer 8 is smaller than 16"));
	  l = 16; log_reset(); CHECK(kgetopt(1, GETUUID, NULL, &l) == EINVAL && has("buffer 16 (NULL) is smaller")); CHECK(ksetopt(1, SETSPEC, NULL, 12) == EINVAL); }
	CHECK(ncalls == 12 && create_n == 1);
	log_reset(); CHECK(ksetopt(1, 0x80045414u, spec, 4) == ENOPROTOOPT && has("kctl setsockopt 0x80045414 unknown"));       /* other options: today's path */
	{ struct termios t; memset(&t, 0, sizeof(t)); CHECK(ksetopt(1, TIOCSETA, &t, sizeof(t)) == 0 && termios_set_n == 1); }
	CHECK(ctl_disconnect(1) == 0);

	/* ---- 3. creation failures: teardown + memo ---- */
	for (int k = 1; k <= 11; k++) {
		nexus_reset(); fail_call = k; fail_err = ENOMEM; log_reset(); CHECK(ctl_connect(1) == 0);
		memcpy(spec, (uint32_t[3]){ 1024, 63, 63 }, 12);
		CHECK(ksetopt(1, SETSPEC, spec, 12) == ENOMEM);
		CHECKL(has("BT GAP: skywalk kern_nexus_"), "creation GAP");
		CHECKL(has("error 12"), "errno in GAP");
		CHECKL(has("skywalk channel unavailable"), "unavailable GAP");
		CHECK(ctl_destroyed == (k >= 2 ? 1 : 0)); CHECK(dereg_n == (k == 11 ? 1 : 0)); CHECK(attr_destroyed == (k >= 4 ? 1 : 0)); CHECK(create_n == 0);
		if (k == 11) CHECK(calls_idx("deregister") < calls_idx("ctl_destroy"));
		if (k >= 4) CHECK(calls_idx("attr_destroy") >= 0);
		int nc = ncalls; fail_call = 0; log_reset();
		{ uint8_t u[16]; size_t l = 16; CHECK(kgetopt(1, GETUUID, u, &l) == ENOMEM); }
		CHECK(ksetopt(1, SETSPEC, spec, 12) == ENOMEM && ncalls == nc);       /* memoised: no second creation attempt */
		CHECK(ctl_disconnect(1) == 0);
	}
	nexus_reset(); CHECK(ctl_connect(1) == 0);      /* order independence: GETCHANNELUUID alone creates the instance */
	{ uint8_t u[16]; size_t l = 16; CHECK(kgetopt(1, GETUUID, u, &l) == 0 && create_n == 1 && u[0] == 0xA0); }
	CHECK(have_P);

	/* ---- 4. channel: connect rules ---- */
	log_reset(); pbuf_fail = 1; { void *cc; ring_reset(); CHECK(P.nxpi_pre_connect(NULL, NULL, &NX, 0, &CH, &cc) == ENOTSUP && has("kern_nexus_get_pbufpool error")); CHECK(sbsk.chan == NULL); } pbuf_fail = 0;
	CHECK(chan_open() == 0 && sbsk.active && sbsk.chan == &CH && sbsk.rxring == &rxr && sbsk.rx_pp == &rxpp);
	{ void *cc; log_reset(); CHECK(P.nxpi_pre_connect(NULL, NULL, &NX, 0, &CH2, &cc) == EBUSY && has("already has a client") && sbsk.chan == &CH && sbsk.active); }
	P.nxpi_disconnected(NULL, &NX, &CH2);       /* a foreign channel's teardown does not touch ours */
	CHECK(sbsk.chan == &CH && sbsk.active);

	/* ---- 5. TX across slots ---- */
	{ uint8_t a[1024], b[1024], c[17], d[300], z[100]; size_t tot = 0; int calls0;
	  pat(a, 1024, 1); pat(b, 1024, 2); pat(c, 17, 3); pat(d, 300, 4); pat(z, 100, 5);
	  txlen = 0; txcalls = 0; stat_slots = stat_bytes = 0;
	  tx_put(a, 1024, 0); tx_put(b, 1024, 0); tx_put(c, 17, 0); tx_put(NULL, 0, 0); txr.ks[txr.rhead].pkt = 0; txr.rhead = nxt(txr.rhead); tx_put(z, 100, 5); tx_put(d, 300, 0);
	  CHECK(ctl_disconnect(1) == 0);
	  /* first with the control closed: refused, nothing sent, slots consumed */
	  log_reset(); sync_tx(&e);
	  CHECK(e == ENOTCONN && txcalls == 0 && has("with the sk control closed") && txr.khead == txr.rhead);
	  ring_reset(); sbsk.rxring = &rxr; tx_put(a, 1024, 0); tx_put(b, 1024, 0); tx_put(c, 17, 0); tx_put(NULL, 0, 0); txr.ks[txr.rhead].pkt = 0; txr.rhead = nxt(txr.rhead); tx_put(z, 100, 5); tx_put(d, 300, 0);
	  CHECK(ctl_connect(1) == 0); log_reset(); txlen = 0; txcalls = 0; calls0 = txcalls;
	  sync_tx(&e); (void)calls0;
	  uint8_t want[2048 + 17 + 100 + 300 + 8]; memcpy(want, a, 1024); memcpy(want + 1024, b, 1024); memcpy(want + 2048, c, 17); memcpy(want + 2065, z, 100); tot = 2065 + 100; memcpy(want + tot, d, 300); tot += 300;
	  CHECK(e == 0 && txcalls == 5 && txlen == tot && memcmp(txlog, want, tot) == 0);
	  CHECK(txsizes[0] == 1024 && txsizes[1] == 1024 && txsizes[2] == 17 && txsizes[3] == 100 && txsizes[4] == 300);     /* slot order; length from the buflet, data from offset 5 */
	  CHECK(txr.khead == txr.rhead && stat_slots == 5 && stat_bytes == tot);
	  CHECK(!has("BT GAP: skywalk TX") && !has("has no buffer"));
	  /* empty ring */
	  txcalls = 0; sync_tx(&e); CHECK(e == 0 && txcalls == 0);
	  /* backend error on the 2nd slot: error returned, rest dropped + counted, ring still consumed */
	  ring_reset(); sbsk.rxring = &rxr; tx_put(a, 1024, 0); tx_put(b, 1024, 0); tx_put(c, 17, 0); txlen = 0; txcalls = 0; tx_fail_on = 2; log_reset();
	  sync_tx(&e); tx_fail_on = 0;
	  CHECK(e == EIO && txcalls == 2 && txlen == 1024 && txr.khead == txr.rhead && has("BT GAP: skywalk TX to the controller failed (error 5): 1024 bytes sent, 1041 bytes dropped"));
	  /* no backend */
	  ring_reset(); sbsk.rxring = &rxr; tx_put(c, 17, 0); sweet_bt_set_ops(NULL); txcalls = 0; log_reset(); sync_tx(&e); sweet_bt_set_ops(&fops);
	  CHECK(e == ENXIO && txcalls == 0 && txr.khead == txr.rhead && has("skywalk channel TX path absent") && has("BT GAP: skywalk TX to the controller failed (error 6)")); }

	/* ---- 6. RX: buffering, notify, chunking ---- */
	CHECK(sbsk.active);
	enq_n = 0; notify_n[0] = notify_n[1] = 0; notify_with_lock = 0; ring_reset(); sbsk.rxring = &rxr;
	pat(in, 3000, 9); log_reset();
	CHECK(sweet_bt_rx(in, 3000) == 0 && enq_n == 0 && notify_n[0] == 1 && notify_n[1] == 0);
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml);
	CHECK(e == 0 && n == 3000 && ns == 3 && ml == 1024 && memcmp(in, out, 3000) == 0);
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(e == 0 && n == 0 && ns == 0);
	pat(in, 30, 3); CHECK(sweet_bt_rx(in, 10) == 0 && sweet_bt_rx(in + 10, 20) == 0 && notify_n[0] == 3);       /* byte stream: coalesced, order kept */
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(n == 30 && ns == 1 && memcmp(in, out, 30) == 0);
	CHECK(stat_slots > 0);
	/* wrap around the FIFO end */
	pat(in, 60000, 11); CHECK(sweet_bt_rx(in, 60000) == 0); sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(n == 60000 && ns == 59 && memcmp(in, out, 60000) == 0);
	pat(in, 10000, 12); CHECK(sbsk.fhead == (3000 + 30 + 60000) % 65536 && sweet_bt_rx(in, 10000) == 0);
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(n == 10000 && memcmp(in, out, 10000) == 0);
	CHECK(sbsk.fhead == (3000 + 30 + 60000 + 10000) % 65536);
	/* overflow: more than the FIFO, ring smaller than the FIFO */
	pat(in, 70000, 13); log_reset(); notify_n[0] = 0;
	CHECK(sweet_bt_rx(in, 70000) == ENOBUFS && notify_n[0] == 1 && has("BT GAP: skywalk RX FIFO full: 4464 of 70000 UART bytes dropped"));
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(e == 0 && ns == 63 && n == 63 * 1024 && memcmp(in, out, n) == 0 && sbsk.flen == 65536 - 63 * 1024);
	{ size_t n1 = n; sync_rx(&e); n = rx_take(out + n1, sizeof(out) - n1, &ns, &ml); CHECK(ns == 1 && n == 1024 && memcmp(in, out, 65536) == 0 && sbsk.flen == 0); }
	/* allocation failure: nothing lost, GAP, next sync delivers */
	pat(in, 100, 14); CHECK(sweet_bt_rx(in, 100) == 0); alloc_fail = 1; log_reset(); sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml);
	CHECK(e == 0 && ns == 0 && sbsk.flen == 100 && has("BT GAP: skywalk RX: packet allocation failed (error 12)"));
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(n == 100 && memcmp(in, out, 100) == 0);
	{ int used = 0; for (size_t i = 0; i < sizeof(pk) / sizeof(pk[0]); i++) used += pk[i].used; CHECK(used == 0); }       /* no leaked packets */
	/* notify failure is loud, bytes stay */
	notify_ret = ENXIO; log_reset(); CHECK(sweet_bt_rx(in, 5) == 0 && has("BT GAP: skywalk RX notify failed (error 6)")); notify_ret = 0;
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(n == 5);
	CHECK(notify_with_lock == 0);       /* kern_channel_notify never runs under sbsk_lock */

	/* ---- 7. close order: control first, channel after ---- */
	pat(in, 100, 15); CHECK(sweet_bt_rx(in, 100) == 0); log_reset();
	CHECK(ctl_disconnect(1) == 0);
	CHECK(sbsk.active && sbsk.flen == 0 && has("BT GAP: skywalk control closed with 100 unread RX bytes; dropped") && sbt.uart_busy == 0);
	chan_close();
	CHECK(!sbsk.active && sbsk.chan == NULL && sbsk.rxring == NULL && sbsk.rx_pp == NULL && sbsk.notifiers == 0);
	P.nxpi_disconnected(NULL, &NX, &CH); P.nxpi_pre_disconnect(NULL, &NX, &CH); P.nxpi_ring_fini(NULL, &NX, &rxr);      /* idempotent */
	log_reset(); enq_n = 0; CHECK(sweet_bt_rx(in, 8) == ENOTCONN && has("with no connected client")); CHECK(enq_n == 0);
	in_sync = 0; sync_rx(&e); CHECK(e == 0);       /* a late sync finds no channel: no slot KPI, no error */
	/* no channel: the plain path is unchanged */
	CHECK(ctl_connect(0) == 0); enq_n = 0; enq_len = 0; CHECK(sweet_bt_rx(in, 8) == 0 && enq_n == 1 && enq_len == 8 && memcmp(enq_buf, in, 8) == 0); CHECK(ctl_disconnect(0) == 0);
	/* sk control connected but no channel yet: ctl_enqueuedata */
	CHECK(ctl_connect(1) == 0); enq_n = 0; CHECK(sweet_bt_rx(in, 4) == 0 && enq_n == 1);
	/* reconnect (bluetoothd restart): a fresh channel, empty FIFO */
	CHECK(chan_open() == 0 && sbsk.active && sbsk.flen == 0); enq_n = 0; CHECK(sweet_bt_rx(in, 4) == 0 && enq_n == 0 && sbsk.flen == 4);
	chan_close(); CHECK(!sbsk.active);

	/* ---- 8. notify racing the channel teardown (real threads) ---- */
	CHECK(chan_open() == 0); notify_hook = race_hook; thr_done = thr_started = 0; hook_checked_blocked = 0;
	CHECK(sweet_bt_rx(in, 16) == 0);
	notify_hook = NULL; pthread_join(thr, NULL);
	CHECK(hook_checked_blocked == 1);       /* pre_disconnect waited for the notifier */
	CHECK(thr_done == 1 && sbsk.notifiers == 0 && !sbsk.active);
	P.nxpi_ring_fini(NULL, &NX, &rxr); P.nxpi_ring_fini(NULL, &NX, &txr); P.nxpi_disconnected(NULL, &NX, &CH);
	CHECK(ctl_disconnect(1) == 0);

	/* ---- 9. connect refused when the RX ring was never initialised ---- */
	{ void *cc; ring_reset(); log_reset(); CHECK(P.nxpi_pre_connect(NULL, NULL, &NX, 0, &CH, &cc) == 0); CHECK(P.nxpi_connected(NULL, &NX, &CH) == ENXIO && !sbsk.active && has("RX ring was not initialised")); P.nxpi_disconnected(NULL, &NX, &CH); CHECK(sbsk.chan == NULL); }

	/* ---- 10. RX between SETCHANNELSPEC and the channel connect: queued for the channel, never into the control's socket buffer ---- */
	memcpy(spec, sp3, 12);
	nexus_reset(); CHECK(ctl_connect(1) == 0); enq_n = 0; notify_n[0] = notify_n[1] = 0;
	{ uint8_t u[16]; size_t l = 16; CHECK(kgetopt(1, GETUUID, u, &l) == 0); }
	pat(in, 7, 20); CHECK(sweet_bt_rx(in, 7) == 0 && enq_n == 1 && !sbsk.armed);      /* GETCHANNELUUID alone does not arm */
	CHECK(ksetopt(1, SETSPEC, spec, 12) == 0 && sbsk.armed && !sbsk.active); enq_n = 0;
	pat(in, 100, 21); log_reset(); CHECK(sweet_bt_rx(in, 100) == 0 && enq_n == 0 && sbsk.flen == 100 && notify_n[0] == 0 && !has("BT GAP: skywalk RX"));
	CHECK(chan_open() == 0 && sbsk.active && !sbsk.armed && sbsk.flen == 100 && notify_n[0] == 1);       /* kept across connected, and the user ring is woken */
	sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); CHECK(e == 0 && n == 100 && ns == 1 && memcmp(in, out, 100) == 0 && enq_n == 0);
	chan_close(); CHECK(!sbsk.active && !sbsk.armed && sbsk.flen == 0); CHECK(ctl_disconnect(1) == 0);
	/* bounded: the oldest bytes go, with a GAP count; two writes, then one write larger than the FIFO */
	nexus_reset(); CHECK(ctl_connect(1) == 0); CHECK(ksetopt(1, SETSPEC, spec, 12) == 0 && sbsk.armed); enq_n = 0;
	{ static uint8_t all[70000]; pat(all, 70000, 22); log_reset();
	  CHECK(sweet_bt_rx(all, 60000) == 0 && sbsk.flen == 60000 && !has("oldest"));
	  CHECK(sweet_bt_rx(all + 60000, 10000) == 0 && sbsk.flen == 65536 && enq_n == 0 && has("BT GAP: skywalk RX before the channel connected: FIFO full, 4464 oldest bytes dropped"));
	  CHECK(chan_open() == 0 && sbsk.flen == 65536);
	  sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); { size_t n1 = n; sync_rx(&e); n = rx_take(out + n1, sizeof(out) - n1, &ns, &ml); n += n1; }
	  CHECK(n == 65536 && memcmp(all + 4464, out, 65536) == 0 && sbsk.flen == 0);
	  chan_close(); CHECK(ctl_disconnect(1) == 0);
	  CHECK(ctl_connect(1) == 0); CHECK(ksetopt(1, SETSPEC, spec, 12) == 0); log_reset(); pat(in, 70000, 23);
	  CHECK(sweet_bt_rx(in, 70000) == 0 && sbsk.flen == 65536 && has("4464 oldest bytes dropped"));
	  CHECK(chan_open() == 0); sync_rx(&e); n = rx_take(out, sizeof(out), &ns, &ml); { size_t n1 = n; sync_rx(&e); n = rx_take(out + n1, sizeof(out) - n1, &ns, &ml); n += n1; }
	  CHECK(n == 65536 && memcmp(in + 4464, out, 65536) == 0);
	  chan_close(); CHECK(ctl_disconnect(1) == 0); }
	/* the control closes before the channel exists: the queued bytes are dropped loudly and RX goes back to the controls */
	nexus_reset(); CHECK(ctl_connect(1) == 0); CHECK(ksetopt(1, SETSPEC, spec, 12) == 0 && sbsk.armed);
	pat(in, 10, 24); CHECK(sweet_bt_rx(in, 10) == 0 && sbsk.flen == 10); log_reset();
	CHECK(ctl_disconnect(1) == 0 && !sbsk.armed && sbsk.flen == 0 && has("BT GAP: skywalk control closed with 10 unread RX bytes; dropped"));
	CHECK(ctl_connect(0) == 0); enq_n = 0; CHECK(sweet_bt_rx(in, 10) == 0 && enq_n == 1); CHECK(ctl_disconnect(0) == 0);
	/* a failed SETCHANNELSPEC (nexus creation error) does not arm */
	nexus_reset(); fail_call = 1; fail_err = ENOMEM; CHECK(ctl_connect(1) == 0); CHECK(ksetopt(1, SETSPEC, spec, 12) == ENOMEM && !sbsk.armed); fail_call = 0;
	enq_n = 0; CHECK(sweet_bt_rx(in, 10) == 0 && enq_n == 1); CHECK(ctl_disconnect(1) == 0);

	if (fails) { printf("RESULT: FAIL sweet-bt-sk-host (%d checks)\n", fails); return 1; }
	printf("RESULT: PASS sweet-bt-sk-host\n");
	return 0;
}
