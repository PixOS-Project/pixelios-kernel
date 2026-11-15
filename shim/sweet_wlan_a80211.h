/* sweet_wlan_a80211.h - kernel glue between the en0 ifnet and the Apple80211 station handler (P-W4, T-7241; goal W-D7).
 *
 * What it does (all KPI-free, host-tested by wlan_a80211_glue_host_test.c; the kernel file wlan_a80211_glue.c.in only supplies copyin/copyout, the lock and
 * kev_post_msg_nowait):
 *  - A80211 ioctls: SIOCSA80211 (_IOW('i',200,40 B)) / SIOCGA80211 (_IOWR('i',201,40 B)) carry a 40-byte `apple80211req` [src 110:65-77]. The handler copies
 *    req_len bytes in (Set) or out (Get) through req_data (a USER address: the ifioctl `data` is only the 40-byte kernel copy) and calls swsta_a80211().
 *    Any other command is NOT MINE and goes to the previous if_ioctl of the ifnet (en0_ioctl: SIOCGIFMEDIA IEEE80211|AUTO, link status, rule-8 GAP lines).
 *  - events: swsta's post callback becomes a kern_event_msg with vendor 1 (KEV_VENDOR_APPLE), class 6 (KEV_IEEE80211_CLASS), subclass 1, event_code = the
 *    APPLE80211_M_* number, event_data = ifname[16] (NUL padded) + payload [src 110:118-124]. Payload is empty today (layouts open, 110:128).
 * Reachability of the ioctl, read from the built XNU 22.6.0 tree (the 110 open item 9 [guess] is closed): bsd/net/if.c ifioctl() (:3184) has no case for
 * 'i' 200/201, so the first switch's `default:` (:3441-3449) copies `ifr_name` (= req_if_name, offset 0) and does ifunit_ref(); the second switch's `default:`
 * (:3535-3553) calls pru_control (in_control(), bsd/netinet/in.c, default -> EOPNOTSUPP) and, on EOPNOTSUPP, ifnet_ioctl(ifp, ..., cmd, data)
 * (bsd/net/dlil.c:7503) which calls ifp->if_ioctl. The 0x28 size is never compared with sizeof(ifreq) on that path [read from source; not run on hardware].
 * SIOCGIFMEDIA: ifioctl_get_media() (if.c:3148) forwards SIOCGIFMEDIA64 to the driver; both structs are pack(4) so SIOCGIFMEDIA64 == SIOCGIFMEDIA == 0xc02c6938
 * (struct ifmediareq64 compiled with clang on the host: 44 bytes) [measured on the host compiler, not the kernel build].
 * Rules: 4 (no ssid/key/MAC in a log line: lines carry type, length, errno, counters), 8 (every refusal and every ignored value prints WIFI GAP:), 7.
 */
#ifndef WLAN_A80211_GLUE_H
#define WLAN_A80211_GLUE_H

#include <stdint.h>
#include "sweet_wlan_sta.h"      /* the includer defines SWSTA_MEMCPY/MEMSET/SNPRINTF first (the kernel has no <string.h>/<stdio.h>), else the host libc */

#define A80211_EINVAL 22u        /* same value on XNU and the host */
#define A80211_EFAULT 14u
#define A80211_EOPNOTSUPP SWSTA_EOPNOTSUPP   /* XNU 102, not the host's 95 */

#define A80211_SIOCS 0x802869c8ul   /* _IOW('i', 200, 40 bytes) [src 110:68] */
#define A80211_SIOCG 0xc02869c9ul   /* _IOWR('i', 201, 40 bytes) [src 110:69] */
#define A80211_ENOTMINE (-1)        /* not an A80211 command: the caller chains to the previous if_ioctl */
#define A80211_BUF 10016u           /* the longest cited request (CHANNELS_INFO, 110:142); swsta_len() never wants more */
#define A80211_KEV_VENDOR 1u        /* KEV_VENDOR_APPLE */
#define A80211_KEV_CLASS 6u         /* KEV_IEEE80211_CLASS [src bsd/sys/kern_event.h:100] */
#define A80211_KEV_SUBCLASS 1u      /* [src 110:121] */
#define A80211_IFNAMSIZ 16u
#define A80211_GAP_EV "WIFI GAP: A80211 events carry an empty payload (per-event layouts open, 110:128)"

struct a80211_req {                 /* apple80211req [obs-RE 110:75] */
	char ifname[A80211_IFNAMSIZ];   /* @0x00 */
	uint32_t type;                  /* @0x10 */
	uint32_t val;                   /* @0x14 */
	uint32_t len;                   /* @0x18 */
	uint32_t pad;                   /* @0x1c */
	uint64_t data;                  /* @0x20 user address of the buffer (64-bit process) */
};
_Static_assert(sizeof(struct a80211_req) == 40, "apple80211req is 40 bytes (110:68)");
_Static_assert(__builtin_offsetof(struct a80211_req, type) == 0x10 && __builtin_offsetof(struct a80211_req, len) == 0x18 && __builtin_offsetof(struct a80211_req, data) == 0x20, "apple80211req layout (110:75)");

/* what kev_post_msg_nowait gets: struct kev_msg with up to 2 vectors; the kernel glue copies it field by field */
struct a80211_kev {
	uint32_t vendor, kclass, subclass, code;
	uint32_t n;
	struct { uint32_t len; const void *ptr; } dv[2];
};

struct a80211_io {                  /* supplied by the includer; no member is named copyin/copyout: XNU defines those as function-like macros (offload gate build error, T-7241) */
	void *ctx;
	int (*uin)(void *ctx, uint64_t uaddr, void *dst, uint32_t n);          /* 0 or a positive errno (EFAULT) */
	int (*uout)(void *ctx, const void *src, uint64_t uaddr, uint32_t n);
	void (*lock)(void *ctx);
	void (*unlock)(void *ctx);
	int (*kev)(void *ctx, const struct a80211_kev *ev);                         /* 0 = posted */
	void (*log)(void *ctx, const char *line);
	void (*gap)(void *ctx, const char *text);                                   /* printed as "WIFI GAP: <text>" by the includer */
};

typedef int (*a80211_chain_fn)(void *ifp, unsigned long cmd, void *data);

struct a80211 {
	struct swsta sta;
	struct a80211_io io;
	a80211_chain_fn chain;          /* the ifnet's previous if_ioctl */
	char ifname[A80211_IFNAMSIZ];
	uint32_t reqs, faults, big, bad, chained, ev_posted, ev_failed;
	uint8_t buf[A80211_BUF];        /* one request at a time (io.lock held) */
};

static inline void a80211_wipe(void *p, size_t n)
{
	volatile uint8_t *q = (volatile uint8_t *)p;
	while (n-- > 0)
		*q++ = 0;
}

static inline void a80211_gap(struct a80211 *g, const char *text)
{
	if (g->io.gap != NULL)
		g->io.gap(g->io.ctx, text);
}

static inline void a80211_log_cb(void *cx, const char *line)
{
	struct a80211 *g = (struct a80211 *)cx;
	if (g->io.log != NULL)
		g->io.log(g->io.ctx, line);
}

static inline void a80211_gap_cb(void *cx, const char *text)
{
	a80211_gap((struct a80211 *)cx, text);
}

/* swsta post callback -> kern_event_msg(vendor 1, class 6, subclass 1, code, ifname[16] + payload) */
static inline void a80211_post_cb(void *cx, uint32_t code)
{
	struct a80211 *g = (struct a80211 *)cx;
	struct a80211_kev ev;
	char name[A80211_IFNAMSIZ];
	char t[64];
	SWSTA_MEMSET(name, 0, sizeof name);
	SWSTA_MEMCPY(name, g->ifname, sizeof name - 1u);
	SWSTA_MEMSET(&ev, 0, sizeof ev);
	ev.vendor = A80211_KEV_VENDOR; ev.kclass = A80211_KEV_CLASS; ev.subclass = A80211_KEV_SUBCLASS; ev.code = code;
	ev.n = 1u; ev.dv[0].len = A80211_IFNAMSIZ; ev.dv[0].ptr = name;
	if (g->io.kev == NULL || g->io.kev(g->io.ctx, &ev) != 0) {
		g->ev_failed++;
		SWSTA_SNPRINTF(t, sizeof t, "A80211 event %u not posted (kev failed)", (unsigned)code);
		a80211_gap(g, t);
		return;
	}
	g->ev_posted++;
}

/* W-UI: the same with a payload behind ifname[16] (kern_event_msg event_data = ifname + payload [src 110:118-124]; IO80211 reads the payload at kev +0x28 [obs-RE W-UI-wifid-current-network.md]) */
static inline void a80211_post_data_cb(void *cx, uint32_t code, const void *d, uint32_t n)
{
	struct a80211 *g = (struct a80211 *)cx;
	struct a80211_kev ev;
	char name[A80211_IFNAMSIZ];
	char t[64];
	SWSTA_MEMSET(name, 0, sizeof name);
	SWSTA_MEMCPY(name, g->ifname, sizeof name - 1u);
	SWSTA_MEMSET(&ev, 0, sizeof ev);
	ev.vendor = A80211_KEV_VENDOR; ev.kclass = A80211_KEV_CLASS; ev.subclass = A80211_KEV_SUBCLASS; ev.code = code;
	ev.n = 2u; ev.dv[0].len = A80211_IFNAMSIZ; ev.dv[0].ptr = name; ev.dv[1].len = n; ev.dv[1].ptr = d;
	if (g->io.kev == NULL || g->io.kev(g->io.ctx, &ev) != 0) {
		g->ev_failed++;
		SWSTA_SNPRINTF(t, sizeof t, "A80211 event %u (+%u B) not posted (kev failed)", (unsigned)code, (unsigned)n);
		a80211_gap(g, t);
		return;
	}
	g->ev_posted++;
}

/* The ifname is copied (NUL padded) at init; the glue never reads it back from the user's request. */
static inline void a80211_init(struct a80211 *g, struct swsta_radio *r, const struct swsta_codec *codec, const struct a80211_io *io,
			       const char *ifname, a80211_chain_fn chain)
{
	struct swsta_cb cb;
	SWSTA_MEMSET(g, 0, sizeof *g);
	g->io = *io; g->chain = chain;
	{ uint32_t i; for (i = 0; i + 1u < sizeof g->ifname && ifname[i] != 0; i++) g->ifname[i] = ifname[i]; }
	cb.log = a80211_log_cb; cb.gap = a80211_gap_cb; cb.post = a80211_post_cb; cb.post_data = a80211_post_data_cb; cb.cx = g;
	swsta_init(&g->sta, r, codec, &cb);
	a80211_gap(g, A80211_GAP_EV);
}

/* Returns 0 or a positive errno for SIOCSA80211 / SIOCGA80211, A80211_ENOTMINE for every other command.
 * `data` is the 40-byte kernel copy of the request (ifioctl). Never logs the bytes. */
static inline int a80211_ioctl(struct a80211 *g, unsigned long cmd, void *data)
{
	struct a80211_req rq;
	int set, rc;
	uint32_t len;
	char t[96];
	if (cmd != A80211_SIOCS && cmd != A80211_SIOCG)
		return A80211_ENOTMINE;
	if (data == NULL) {
		g->bad++;
		a80211_gap(g, "A80211 ioctl with no request body");
		return A80211_EINVAL;
	}
	set = (cmd == A80211_SIOCS);
	SWSTA_MEMCPY(&rq, data, sizeof rq);
	len = rq.len;
	if (g->io.lock != NULL) g->io.lock(g->io.ctx);
	g->reqs++;
	if (len > A80211_BUF) {
		/* too long to buffer: the handler refuses it (swsta_len never wants more) and counts the GAP */
		g->big++;
		rc = swsta_a80211(&g->sta, set, rq.type, &rq.val, NULL, len);
	} else if (len != 0 && rq.data == 0) {
		g->bad++;
		SWSTA_SNPRINTF(t, sizeof t, "A80211 type %u %s len %u has no req_data", (unsigned)rq.type, set ? "set" : "get", (unsigned)len);
		a80211_gap(g, t);
		rc = A80211_EINVAL;
	} else {
		void *buf = len != 0 ? (void *)g->buf : NULL;
		rc = 0;
		if (set && len != 0) {
			rc = g->io.uin(g->io.ctx, rq.data, g->buf, len);
			if (rc != 0) {
				g->faults++;
				SWSTA_SNPRINTF(t, sizeof t, "A80211 type %u set copyin failed errno %d", (unsigned)rq.type, rc);
				a80211_gap(g, t);
			}
		} else if (len != 0) {
			SWSTA_MEMSET(g->buf, 0, len);          /* a Get never leaks the previous request's bytes */
		}
		if (rc == 0) {
			rc = swsta_a80211(&g->sta, set, rq.type, &rq.val, buf, len);
			if (rc == 0 && !set && len != 0) {
				rc = g->io.uout(g->io.ctx, g->buf, rq.data, len);
				if (rc != 0) {
					g->faults++;
					SWSTA_SNPRINTF(t, sizeof t, "A80211 type %u get copyout failed errno %d", (unsigned)rq.type, rc);
					a80211_gap(g, t);
				}
			}
		}
	}
	if (!set && rc == 0)
		((struct a80211_req *)data)->val = rq.val;      /* the 4-byte class answers in req_val (Get 21 [src 110:W-D7 R1]); the generic ioctl path copies `data` out for IOC_OUT [guess: BSD convention, sys_generic.c not in this clone] */
	a80211_wipe(g->buf, len <= A80211_BUF ? len : A80211_BUF);      /* the ASSOCIATE body carries the key */
	a80211_wipe(&rq, sizeof rq);
	if (g->io.unlock != NULL) g->io.unlock(g->io.ctx);
	return rc;
}

/* The if_ioctl the kernel glue installs: A80211 here, everything else to the previous handler (rule 8: no chain = EOPNOTSUPP + GAP). */
static inline int a80211_if_ioctl(struct a80211 *g, void *ifp, unsigned long cmd, void *data)
{
	int rc = a80211_ioctl(g, cmd, data);
	if (rc != A80211_ENOTMINE)
		return rc;
	g->chained++;
	if (g->chain == NULL) {
		a80211_gap(g, "A80211 glue has no previous if_ioctl: command refused");
		return A80211_EOPNOTSUPP;
	}
	return g->chain(ifp, cmd, data);
}

#endif /* WLAN_A80211_GLUE_H */
