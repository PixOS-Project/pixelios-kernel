/* wlan_a80211_glue_host_test.c - host test of shim/sweet_wlan_a80211.h (P-W4, T-7241; goal W-D7; research 110 s.3 and s.5).
 * Replays the Apple80211 library's sequence against the glue with a fake radio, a fake user address space and a fake kev_post_msg:
 * bind probe (Get 12 len 13), startup Gets (19 / 106 / 207 / 27), Set 19 on/off, Set 10 -> SCAN_DONE -> Get 11 until the end errno, Set 20 join, Set 22,
 * and checks the event bytes at the cited offsets (total_size >= 0x28, vendor 1 / class 6 / subclass 1, event_code @0x14, ifname[16] @0x18, payload @0x28).
 * SIOCGIFMEDIA and the ioctl chain run through the REAL en0_ioctl of wlan_en0.h (stub KPI of wlan_en0_host_test.c, included below under a renamed main).
 * The Apple80211 byte layouts are still open (110:128): the codec here is TEST-LOCAL and not an Apple layout. No hardware. Build:
 * gcc -O1 -Wall -Wextra -Werror (ASan/UBSan clean). Mutants: tools/test_wlan_a80211_glue_mutants.py. */
#define main wlan_en0_suite_main
#include "wlan_en0_host_test.c"        /* the stub ifnet / mbuf / kern_event KPI, CHECK, fails, g_log, setup(), MAC, g_e, g_ifp */
#undef main
#include "sweet_wlan_a80211.h"

#ifndef A80211_HDR
#define A80211_HDR "sweet_wlan_a80211.h"
#endif

/* ---- _IOW / _IOWR from bsd/sys/ioccom.h (IOC_IN 0x80000000, IOC_OUT 0x40000000, len << 16, group << 8, number) ---- */
#define T_IOC(dir, g, n, len) ((unsigned long)(dir) | ((unsigned long)((len) & 0x1fff) << 16) | ((unsigned long)(g) << 8) | (unsigned long)(n))
#define T_IOW(g, n, len) T_IOC(0x80000000ul, g, n, len)
#define T_IOWR(g, n, len) T_IOC(0xc0000000ul, g, n, len)

/* ---- fake user address space ---- */
#define UBASE 0x16fdff000ull
static uint8_t umem[16384];
static uint32_t n_copyin, n_copyout, g_fault_in, g_fault_out;
static int u_in(void *ctx, uint64_t ua, void *dst, uint32_t n)
{
	(void)ctx; n_copyin++;
	if (g_fault_in || ua < UBASE || ua + n > UBASE + sizeof umem) return 14;
	memcpy(dst, umem + (ua - UBASE), n);
	return 0;
}
static int u_out(void *ctx, const void *src, uint64_t ua, uint32_t n)
{
	(void)ctx; n_copyout++;
	if (g_fault_out || ua < UBASE || ua + n > UBASE + sizeof umem) return 14;
	memcpy(umem + (ua - UBASE), src, n);
	return 0;
}
static int g_locked, g_lock_n, g_lock_bad;
static void u_lock(void *ctx) { (void)ctx; if (g_locked) g_lock_bad++; g_locked = 1; g_lock_n++; }
static void u_unlock(void *ctx) { (void)ctx; if (!g_locked) g_lock_bad++; g_locked = 0; }

/* ---- fake kev_post_msg_nowait: assembles the message like bsd/kern/kern_event.c kev_post_msg_internal (header 0x18 + the vectors up to the first empty one) ---- */
struct kmsg { uint8_t b[128]; uint32_t len; };
static struct kmsg g_k[32];
static uint32_t g_nk, g_kev_fail, g_kev_id;
static int u_kev(void *ctx, const struct a80211_kev *k)
{
	struct kmsg *m = &g_k[g_nk % 32u];
	uint32_t i, total = 24, w[6];
	(void)ctx;
	if (g_kev_fail) return 55;
	for (i = 0; i < 2 && i < k->n && k->dv[i].len != 0; i++) total += k->dv[i].len;
	if (total > sizeof m->b) return 90;
	memset(m, 0, sizeof *m);
	w[0] = total; w[1] = k->vendor; w[2] = k->kclass; w[3] = k->subclass; w[4] = ++g_kev_id; w[5] = k->code;
	memcpy(m->b, w, sizeof w);
	total = 24;
	for (i = 0; i < 2 && i < k->n && k->dv[i].len != 0; i++) { memcpy(m->b + total, k->dv[i].ptr, k->dv[i].len); total += k->dv[i].len; }
	m->len = total; g_nk++;
	return 0;
}
static uint32_t rd32(const struct kmsg *m, uint32_t off) { uint32_t v; memcpy(&v, m->b + off, 4); return v; }
static const struct kmsg *kev_at(uint32_t i) { return &g_k[i % 32u]; }

/* ---- log / gap capture (rule 4: nothing private may appear) ---- */
static char s_log[65536];
static uint32_t s_gaps;
static void u_log(void *ctx, const char *l) { size_t n = strlen(s_log); (void)ctx; snprintf(s_log + n, sizeof s_log - n, "%s\n", l); }
static void u_gap(void *ctx, const char *t) { size_t n = strlen(s_log); (void)ctx; s_gaps++; snprintf(s_log + n, sizeof s_log - n, "WIFI GAP: %s\n", t); }
static uint32_t s_count(const char *sub) { uint32_t n = 0; const char *p = s_log; while ((p = strstr(p, sub)) != NULL) { n++; p += strlen(sub); } return n; }

/* ---- fake radio (3 deterministic BSSes), as in wlan_sta_host_test.c ---- */
struct fake { int on, joined, cursor, cur; struct swsta_bss bss[3]; char key[64]; };
static struct fake fk;
static struct swsta_radio rad;
static void fk_ev(int kind, int result, int reason) { struct swsta_rev e; e.kind = kind; e.result = result; e.reason = reason; rad.up(rad.up_ctx, &e); }
static int f_power(void *r, int op, int *st) { struct fake *f = r; if (op == SWSTA_PWR_ON) f->on = 1; else if (op == SWSTA_PWR_OFF) { f->on = 0; f->joined = 0; } *st = f->on; return 0; }
static int f_scan(void *r) { struct fake *f = r; if (!f->on) return SWSTA_EIO; f->cursor = 0; fk_ev(SWSTA_REV_SCAN_DONE, 0, 0); return 0; }
static int f_next(void *r, struct swsta_bss *o) { struct fake *f = r; if (f->cursor >= 3) return 0; *o = f->bss[f->cursor++]; return 1; }
static int f_join(void *r, const struct swsta_join *j)
{
	struct fake *f = r; int i;
	if (j->key_kind == SWSTA_KEY_PASSPHRASE) memcpy(f->key, j->key, j->key_len < 63 ? j->key_len : 63);
	for (i = 0; i < 3; i++)
		if (f->bss[i].ssid_len == j->ssid_len && !memcmp(f->bss[i].ssid, j->ssid, j->ssid_len)) { f->joined = 1; f->cur = i; fk_ev(SWSTA_REV_ASSOC_DONE, 0, 0); return 0; }
	fk_ev(SWSTA_REV_ASSOC_DONE, 9, 0);
	return 0;
}
static int f_leave(void *r) { struct fake *f = r; if (f->joined) { f->joined = 0; fk_ev(SWSTA_REV_LINK_DOWN, 0, 3); } return 0; }
static int f_status(void *r, struct swsta_status *o)
{
	struct fake *f = r; memset(o, 0, sizeof *o);
	if (!f->joined) return 0;
	o->joined = 1; memcpy(o->ssid, f->bss[f->cur].ssid, 32); o->ssid_len = f->bss[f->cur].ssid_len;
	memcpy(o->bssid, f->bss[f->cur].bssid, 6); o->channel = f->bss[f->cur].channel; o->rssi = f->bss[f->cur].rssi;
	return 0;
}
static void fk_setup(void)
{
	static const char *nm[3] = { "FakeNet-A", "FakeNet-B", "FakeNet-C" };
	int i; memset(&fk, 0, sizeof fk);
	for (i = 0; i < 3; i++) {
		fk.bss[i].ssid_len = (uint8_t)strlen(nm[i]); memcpy(fk.bss[i].ssid, nm[i], fk.bss[i].ssid_len);
		memset(fk.bss[i].bssid, 0x10 * (i + 1), 6); fk.bss[i].channel = 1 + 5 * i; fk.bss[i].rssi = -40 - 10 * i; fk.bss[i].sec = SWSTA_SEC_WPA2_PSK;
	}
	memset(&rad, 0, sizeof rad);
	rad.power = f_power; rad.scan_start = f_scan; rad.bss_next = f_next; rad.join = f_join; rad.leave = f_leave; rad.status = f_status; rad.ctx = &fk;
}
static int g_partial;
/* TEST-LOCAL codec (not an Apple layout) */
static int tc_get(void *cx, uint32_t type, const struct swsta_view *v, void *buf, uint32_t len)
{
	uint8_t *b = buf; (void)cx;
	if (type == SWSTA_T_RSSI_BOUNDS && g_partial) { b[0] = 0x42; return 0; }       /* a codec that fills only what it knows */
	memset(b, 0, len);
	switch (type) {
	case SWSTA_T_POWER: b[0] = (uint8_t)v->power_on; return 0;
	case SWSTA_T_SCAN_RESULT: b[0] = v->bss->ssid_len; memcpy(b + 1, v->bss->ssid, 32); b[48] = (uint8_t)v->bss->channel; b[len - 1] = 0x5a; return 0;
	case SWSTA_T_CARD_CAPS: case SWSTA_T_RSSI_BOUNDS: case SWSTA_T_CHANNELS_INFO: case SWSTA_T_SUPP_CHANNELS:
		b[len - 1] = 0xa5; return 0;       /* last byte proves the whole req_len reaches user memory */
	case SWSTA_T_ASSOC_RESULT: b[0] = (uint8_t)v->assoc_result; return 0;
	default: return SWSTA_EOPNOTSUPP;
	}
}
static int tc_set(void *cx, uint32_t type, const void *buf, uint32_t len, struct swsta_setreq *o)
{
	const uint8_t *b = buf; (void)cx; (void)len;
	if (type == SWSTA_T_POWER) { o->power_on = b[0]; return 0; }
	if (type == SWSTA_T_ASSOCIATE) { o->join.ssid_len = b[0]; memcpy(o->join.ssid, b + 1, 32); o->join.key_len = b[40]; memcpy(o->join.key, b + 41, b[40]); o->join.key_kind = SWSTA_KEY_PASSPHRASE; return 0; }
	return SWSTA_EOPNOTSUPP;
}
static const struct swsta_codec tcodec = { tc_get, tc_set, 0 };

/* ---- the previous if_ioctl is the REAL en0_ioctl ---- */
static int chain_en0(void *ifp, unsigned long cmd, void *data) { return (int)en0_ioctl((ifnet_t)ifp, cmd, data); }

static struct a80211 G;
static const struct a80211_io IO = { NULL, u_in, u_out, u_lock, u_unlock, u_kev, u_log, u_gap };

static void reset(a80211_chain_fn chain)
{
	memset(umem, 0, sizeof umem); memset(s_log, 0, sizeof s_log); memset(g_k, 0, sizeof g_k);
	n_copyin = n_copyout = g_fault_in = g_fault_out = g_nk = g_kev_fail = g_kev_id = 0; s_gaps = 0; g_locked = g_lock_n = g_lock_bad = 0;
	fk_setup();
	a80211_init(&G, &rad, &tcodec, &IO, "en0", chain);
}
static void mkreq(struct a80211_req *r, uint32_t type, uint32_t len, uint64_t ua)
{
	memset(r, 0, sizeof *r); memcpy(r->ifname, "en0", 3); r->type = type; r->len = len; r->data = ua;
}
static int get(uint32_t type, uint32_t len, uint64_t ua) { struct a80211_req r; mkreq(&r, type, len, ua); return a80211_ioctl(&G, A80211_SIOCG, &r); }
static int set(uint32_t type, uint32_t len, uint64_t ua) { struct a80211_req r; mkreq(&r, type, len, ua); return a80211_ioctl(&G, A80211_SIOCS, &r); }

static void check_event(uint32_t i, uint32_t code, const char *why)
{
	const struct kmsg *m = kev_at(i);
	uint32_t k;
	if (rd32(m, 0) < 0x28u || rd32(m, 0) != m->len || rd32(m, 4) != 1u || rd32(m, 8) != 6u || rd32(m, 12) != 1u || rd32(m, 0x14) != code ||
	    memcmp(m->b + 0x18, "en0", 4) != 0) {
		printf("EVENT CHECK FAILED (%s): code %u total %u v/c/s %u/%u/%u got code %u\n", why, code, rd32(m, 0), rd32(m, 4), rd32(m, 8), rd32(m, 12), rd32(m, 0x14));
		fails++;
		return;
	}
	for (k = 0x1c; k < 0x28; k++)           /* ifname[16] is NUL padded */
		if (m->b[k] != 0) { printf("EVENT CHECK FAILED (%s): ifname not NUL padded\n", why); fails++; return; }
	if (code == SWSTA_EV_LINK_CHANGED) {   /* W-UI: LINK_CHANGED carries 0x20 B after the ifname (isLinkDown @+0, RSSI @+4) */
		CHECK(rd32(m, 0) == 0x48u && m->b[0x28] <= 1u);
	} else
		CHECK(rd32(m, 0) == 0x28u);    /* the other events: empty payload, total_size == header + ifname */
}

static void test_numbers(void)
{
	CHECK(A80211_SIOCS == T_IOW('i', 200, 40) && A80211_SIOCG == T_IOWR('i', 201, 40));      /* 110:68-69 */
	CHECK(A80211_SIOCS == 0x802869c8ul && A80211_SIOCG == 0xc02869c9ul);
	CHECK(sizeof(struct a80211_req) == 40 && offsetof(struct a80211_req, type) == 0x10 && offsetof(struct a80211_req, val) == 0x14 &&
	      offsetof(struct a80211_req, len) == 0x18 && offsetof(struct a80211_req, data) == 0x20);
	CHECK(A80211_KEV_VENDOR == 1 && A80211_KEV_CLASS == 6 && A80211_KEV_SUBCLASS == 1);                         /* 110:121 */
	CHECK(T_IOWR('i', 56, 44) == 0xc02c6938ul && SIOCGIFMEDIA == 0xc02c6938ul);                                  /* ifmediareq/64 are pack(4): 44 bytes */
}

/* the library sequence of 110 rows 4-11 */
static void test_sequence(void)
{
	uint32_t i, n;
	int rc;
	reset(NULL);
	CHECK(s_count("A80211 events carry an empty payload") == 1);                                             /* rule 8: said once at init */
	/* bind probe: Get 12 len 13 must return 0 */
	memset(umem, 0x77, 64);
	CHECK(get(SWSTA_T_CARD_CAPS, 13, UBASE) == 0 && umem[12] == 0xa5 && n_copyout == 1);                       /* all 13 bytes reached the user buffer */
	CHECK(get(SWSTA_T_POWER, 24, UBASE) == 0 && get(SWSTA_T_RSSI_BOUNDS, 16, UBASE) == 0 && get(SWSTA_T_CHANNELS_INFO, 10016, UBASE) == 0 &&
	      umem[10015] == 0xa5 && get(SWSTA_T_SUPP_CHANNELS, 4824, UBASE) == 0 && umem[4823] == 0xa5);
	/* Set 19 on: POWER_CHANGED (1); off: again */
	umem[0] = 1;
	CHECK(set(SWSTA_T_POWER, 24, UBASE) == 0 && g_nk == 1);
	check_event(0, SWSTA_EV_POWER_CHANGED, "power on");
	CHECK(get(SWSTA_T_POWER, 24, UBASE + 100) == 0 && umem[100] == 1);
	/* Set 10 SCAN_REQ (5456) -> SCAN_DONE (10) -> Get 11 (1164) until the end errno */
	CHECK(set(SWSTA_T_SCAN_REQ, 5456, UBASE) == 0 && g_nk == 2);
	check_event(1, SWSTA_EV_SCAN_DONE, "scan done");
	for (n = 0; (rc = get(SWSTA_T_SCAN_RESULT, 1164, UBASE + 2000 + n * 1164u)) == 0 && n < 8; n++)
		CHECK(umem[2000 + n * 1164u] == 9 && !memcmp(umem + 2000 + n * 1164u + 1, fk.bss[n].ssid, 9) && umem[2000 + n * 1164u + 1163] == 0x5a);
	CHECK(n == 3 && rc == (int)SWSTA_E_SCAN_END);                                                             /* 3 results, then the end errno */
	CHECK(get(SWSTA_T_SSID, 32, UBASE) == (int)SWSTA_ENXIO && get(SWSTA_T_BSSID, 6, UBASE) == (int)SWSTA_ENXIO);   /* not joined: soft */
	/* Set 19 off */
	umem[0] = 0;
	CHECK(set(SWSTA_T_POWER, 24, UBASE) == 0 && g_nk == 3);
	check_event(2, SWSTA_EV_POWER_CHANGED, "power off");
	CHECK(G.faults == 0 && G.bad == 0 && g_lock_bad == 0 && g_lock_n == (int)G.reqs);                        /* one lock pair per request */
	for (i = 0; i < 64; i++) CHECK(G.buf[i] == 0);
}

static void test_join_and_secrets(void)
{
	static const char PASS[] = "fakePassphrase-8d1c";
	uint32_t i;
	reset(NULL);
	umem[0] = 1; CHECK(set(SWSTA_T_POWER, 24, UBASE) == 0);
	memset(umem + 4000, 0, 592); umem[4000] = 9; memcpy(umem + 4001, "FakeNet-B", 9); umem[4040] = (uint8_t)strlen(PASS); memcpy(umem + 4041, PASS, strlen(PASS));
	g_nk = 0;
	CHECK(set(SWSTA_T_ASSOCIATE, 592, UBASE + 4000) == 0);
	CHECK(g_nk == 4);
	check_event(0, SWSTA_EV_ASSOC_DONE, "assoc"); check_event(1, SWSTA_EV_SSID_CHANGED, "ssid"); check_event(2, SWSTA_EV_BSSID_CHANGED, "bssid");
	check_event(3, SWSTA_EV_LINK_CHANGED, "link");
	CHECK(!strcmp(fk.key, PASS));                                                       /* the radio got the key */
	for (i = 0; i < sizeof G.buf; i++) if (G.buf[i] != 0) { printf("scratch not wiped at %u\n", i); fails++; break; }   /* the ASSOCIATE body is gone from kernel memory */
	CHECK(!strstr(s_log, PASS) && !strstr(s_log, "FakeNet") && !strstr(s_log, "en0 attached"));           /* rule 4 */
	CHECK(set(SWSTA_T_DISASSOCIATE, 0, 0) == 0 && g_nk == 5);                           /* len 0, no req_data: fine */
	check_event(4, SWSTA_EV_LINK_CHANGED, "leave");
}

static void test_refusals(void)
{
	uint32_t gaps;
	struct a80211_req r;
	reset(NULL);
	memset(umem, 0x33, 64);
	gaps = s_gaps;
	/* unknown Get type: 0x66 (EOPNOTSUPP, soft in the library 110:77) + a GAP line, user memory untouched */
	CHECK(get(300, 8, UBASE) == (int)SWSTA_EOPNOTSUPP && s_gaps == gaps + 1 && umem[0] == 0x33 && n_copyout == 0);
	CHECK(s_count("WIFI GAP: a80211 type 300 get len 8 not handled") == 1);
	/* wrong length for a known type */
	CHECK(get(SWSTA_T_POWER, 23, UBASE) == (int)SWSTA_EOPNOTSUPP && s_gaps == gaps + 2 && umem[0] == 0x33);
	/* longer than the scratch buffer: refused by the handler, never copied */
	n_copyin = 0;
	CHECK(set(SWSTA_T_ASSOCIATE, 200000, UBASE) == (int)SWSTA_EOPNOTSUPP && G.big == 1 && n_copyin == 0 && s_gaps == gaps + 3);
	CHECK(get(SWSTA_T_CHANNELS_INFO, 100000, UBASE) == (int)SWSTA_EOPNOTSUPP && G.big == 2 && n_copyout == 0);
	/* copyin / copyout faults: EFAULT, loud, the radio is not called */
	g_fault_in = 1; fk.on = 0;
	CHECK(set(SWSTA_T_POWER, 24, UBASE) == 14 && G.faults == 1 && fk.on == 0 && s_count("set copyin failed errno 14") == 1);
	g_fault_in = 0; g_fault_out = 1;
	CHECK(get(SWSTA_T_POWER, 24, UBASE) == 14 && G.faults == 2 && s_count("get copyout failed errno 14") == 1);
	g_fault_out = 0;
	CHECK(get(SWSTA_T_POWER, 24, UBASE + sizeof umem - 8) == 14 && G.faults == 3);                       /* buffer runs past the mapping */
	/* req_data NULL with a length */
	CHECK(get(SWSTA_T_POWER, 24, 0) == (int)A80211_EINVAL && G.bad == 1 && s_count("has no req_data") == 1);
	/* no request body at all */
	CHECK(a80211_ioctl(&G, A80211_SIOCG, NULL) == (int)A80211_EINVAL && G.bad == 2);
	/* a Get never leaks stale kernel bytes: the scratch is zeroed first even when the codec writes only part of it */
	memset(G.buf, 0xAA, sizeof G.buf); memset(umem + 300, 0x33, 16); g_partial = 1;
	CHECK(get(SWSTA_T_RSSI_BOUNDS, 16, UBASE + 300) == 0 && umem[300] == 0x42);
	{ uint32_t k; for (k = 1; k < 16; k++) CHECK(umem[300 + k] == 0); }
	g_partial = 0;
	/* a Get that fails never copies out */
	n_copyout = 0; mkreq(&r, SWSTA_T_SSID, 32, UBASE);
	CHECK(a80211_ioctl(&G, A80211_SIOCG, &r) == (int)SWSTA_ENXIO && n_copyout == 0);
	/* the request struct is not written back (the generic ioctl layer copies the 40 bytes out unchanged) */
	{ struct a80211_req before; mkreq(&before, SWSTA_T_POWER, 24, UBASE); r = before; (void)a80211_ioctl(&G, A80211_SIOCG, &r); CHECK(!memcmp(&r, &before, sizeof r)); }
	CHECK(g_lock_bad == 0 && !g_locked);                                                                    /* every refusal path unlocks */
	/* event delivery failing is a GAP, not silence */
	g_kev_fail = 1; gaps = s_gaps;
	umem[0] = 1; CHECK(set(SWSTA_T_POWER, 24, UBASE) == 0 && G.ev_failed == 1 && s_gaps == gaps + 1 && s_count("event 1 not posted") == 1);
	/* every swsta gap line is <= 63 chars (its contract) is checked by swsta's own test; here the summary has counters */
	swsta_summary(&G.sta);
	CHECK(s_count("a80211 summary requests=") == 1);
}

/* not an A80211 command: the previous if_ioctl (the real en0_ioctl) answers, SIOCGIFMEDIA is IEEE80211 */
static void test_chain_en0(void)
{
	struct ifmediareq mr;
	unsigned before;
	setup(); g_ifp = NULL;
	CHECK(en0_attach(&g_e, MAC) == 0);
	reset(chain_en0);
	memset(&mr, 0, sizeof mr);
	CHECK(a80211_if_ioctl(&G, g_ifp, SIOCGIFMEDIA, &mr) == 0);
	CHECK((mr.ifm_current & 0xe0) == 0x80 && (mr.ifm_active & 0xe0) == 0x80);                              /* IFM_TYPE == IFM_IEEE80211 (110:101) */
	CHECK(mr.ifm_status == IFM_AVALID);                                                                      /* link down: valid, not active; not EPWROFF */
	en0_set_link(&g_e, 1u);
	CHECK(a80211_if_ioctl(&G, g_ifp, SIOCGIFMEDIA, &mr) == 0 && mr.ifm_status == (IFM_AVALID | IFM_ACTIVE));
	CHECK(G.chained == 2 && G.reqs == 0);                                                                    /* the chain never touched the handler */
	before = g_gaps;
	CHECK(a80211_if_ioctl(&G, g_ifp, 0x80206999ul, NULL) == EOPNOTSUPP && g_gaps == before + 1);              /* en0's own EN10 GAP line stays */
	/* an A80211 command never reaches en0_ioctl */
	before = g_gaps;
	CHECK(a80211_if_ioctl(&G, g_ifp, A80211_SIOCG, NULL) == (int)A80211_EINVAL && g_gaps == before);
	CHECK(G.chained == 3);
	/* no previous handler: refused loudly */
	reset(NULL);
	CHECK(a80211_if_ioctl(&G, g_ifp, SIOCGIFMEDIA, &mr) == (int)A80211_EOPNOTSUPP && s_count("no previous if_ioctl") == 1);
}

#ifndef A80211_GLUE_NO_MAIN        /* wlan_radio_host_test.c reuses the fake user memory / kev / log of this file */
int main(void)
{
	test_numbers(); test_sequence(); test_join_and_secrets(); test_refusals(); test_chain_en0();
	if (g_stub_fail) { printf("%d stub check(s) failed\n", g_stub_fail); fails += g_stub_fail; }
	if (fails) { printf("RESULT: FAIL wlan_a80211_glue_host_test fails=%d\n", fails); return 1; }
	printf("RESULT: PASS wlan_a80211_glue_host_test\n");
	return 0;
}
#endif
