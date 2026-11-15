/*
 * pixnet: real-internet proof over en0 for PixOS (lowclk goal, "Wi-Fi must carry real internet")
 * ===============================================================================================
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixnet [-i iface] [-W wait_s] [-D delay_s] [-a IPv4] [-n NAME] [-T] [-R] [-P port] [-d seconds -u URL]
 *   -i IFNAME  interface every socket is bound to with IP_BOUND_IF (default en0), like tools/pixping.c
 *   -W SEC     wait up to SEC (default 120) for the interface to have a non-link-local IPv4 address AND a default route
 *   -D SEC     sleep first (a boot job starts before the link is up)
 *   -a ADDR    literal public IPv4 for the tcp-ip and http-date steps (default 1.1.1.1, port 443 / 80)
 *   -n NAME    host name for dns, tcp-name, tls, https-get and the Host: header (default one.one.one.one)
 *   -T         if the clock step failed, set the system clock from the Date: header of http-date (needs root)
 *   -R         skip the default-route wait (only the interface address is checked); also what happens, with a GAP line, when the route
 *              table cannot be read
 *   -P PORT    TCP port of the http-date step (default 80; host tests use a local server)
 *   -d SEC -u URL  download step: GET the http:// or https:// URL repeatedly for SEC seconds (default 0 = skipped with a GAP line;
 *                  no default URL: this tool never points at a third-party bulk file by itself)
 *
 * One line per step, flushed at once: "pixnet: PASS|FAIL|GAP <step> key=value ..."; last line
 * "pixnet: RESULT pass=N fail=M gap=K". Exit 0 only if fail=0; 2 usage; 3 interface never ready (also counted as a FAIL).
 * Steps: clock, iface, tcp-ip, dns, tcp-name, http-date (+clock2 with -T), tls, tls-noverify, https-get, download.
 *
 * TLS API: Security.framework SecureTransport (SSLCreateContext + SSLHandshake with our own socket callbacks), chosen because
 * it is the one candidate that compiles and links with the pixping-stage flags [read: iPhoneOS16.5.sdk has Security.tbd with
 * _SSLCreateContext/_SSLHandshake/_SSLCopyPeerTrust/_SecTrustEvaluateWithError for arm64; Network.framework ships only a .tbd in
 * this SDK, no headers, and it needs blocks + dispatch]. Cost [read SecureTransport.h]: SecureTransport is deprecated since iOS 13
 * and, to the best of our knowledge, negotiates at most TLS 1.2 [guess]; the line prints the protocol version actually negotiated.
 * Trust: the handshake stops at the server certificate (kSSLSessionOptionBreakOnServerAuth), the chain is evaluated with
 * SecTrustEvaluateWithError against an SSL policy for NAME (system trust store, current wall clock), then the handshake resumes.
 * tls-noverify does the same handshake but ignores the trust result (still prints it), so a clock/trust failure is told apart
 * from a transport failure.
 *
 * Default-route check on Darwin: sysctl NET_RT_DUMP (net.route AF_INET), a route with RTF_GATEWAY, destination 0.0.0.0 and a zero
 * netmask whose rtm_index is the interface [read XNU bsd/net/route.h, rtsock.c sysctl_dumpentry]. Host (Linux) build: /proc/net/route.
 * DNS: getaddrinfo runs in a worker thread that signals a pipe; the main thread polls the pipe for 10 s, so a stuck resolver never
 * hangs the tool. getaddrinfo itself cannot be bound to an interface: the dns line says so [read: no IP_BOUND_IF for the resolver].
 * No busy loops: every wait is a poll() with a timeout. stdout is unbuffered.
 */
#ifndef __APPLE__
#define _GNU_SOURCE /* strptime, timegm on glibc (host test build) */
#endif
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <Security/SecureTransport.h>
#include <sys/sysctl.h>
#endif

static int n_pass, n_fail, n_gap;
static const char *ifname = "en0";

static int64_t now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* verdict is PASS, FAIL or GAP; prints one flushed line */
static void res(const char *verdict, const char *step, const char *fmt, ...)
{
	va_list ap;
	char b[1024];
	va_start(ap, fmt);
	vsnprintf(b, sizeof b, fmt, ap);
	va_end(ap);
	if (!strcmp(verdict, "PASS"))
		n_pass++;
	else if (!strcmp(verdict, "FAIL"))
		n_fail++;
	else
		n_gap++;
	printf("pixnet: %s %s %s\n", verdict, step, b);
	fflush(stdout);
}

static void usage(void)
{
	printf("pixnet: GAP usage: pixnet [-i iface] [-W wait_s] [-D delay_s] [-a IPv4] [-n NAME] [-T] [-R] [-P port] [-d seconds -u URL]\n");
	fflush(stdout);
}

static int valid_name(const char *s)
{
	size_t n = strlen(s);
	if (n == 0 || n > 253)
		return 0;
	for (; *s; s++)
		if (!isalnum((unsigned char)*s) && *s != '.' && *s != '-')
			return 0;
	return 1;
}

/* ---- clock ---- */
static int clock_year(time_t *epoch)
{
	struct tm tm;
	*epoch = time(NULL);
	gmtime_r(epoch, &tm);
	return tm.tm_year + 1900;
}

static int step_clock(const char *step)
{
	time_t e;
	int y = clock_year(&e);
	if (y < 2024) {
		res("FAIL", step, "year=%d epoch=%lld (certificate checks will not be valid)", y, (long long)e);
		return 0;
	}
	res("PASS", step, "year=%d epoch=%lld", y, (long long)e);
	return 1;
}

/* ---- interface + default route ---- */
static int iface_addr(char *why, size_t wl)
{
	struct ifaddrs *ifa, *p;
	int exists = 0, up = 0, have4 = 0, ll = 0;
	if (getifaddrs(&ifa) != 0) {
		snprintf(why, wl, "getifaddrs failed: %s", strerror(errno));
		return 0;
	}
	for (p = ifa; p; p = p->ifa_next) {
		if (!p->ifa_name || strcmp(p->ifa_name, ifname) != 0)
			continue;
		exists = 1;
		if (p->ifa_flags & IFF_UP)
			up = 1;
		if (p->ifa_addr && p->ifa_addr->sa_family == AF_INET) {
			const struct sockaddr_in *sin = (const struct sockaddr_in *)(const void *)p->ifa_addr;
			uint32_t a = ntohl(sin->sin_addr.s_addr);
			if (a == 0)
				continue;
			if ((a >> 16) == 0xa9fe)
				ll = 1; /* 169.254/16 link-local */
			else
				have4 = 1;
		}
	}
	freeifaddrs(ifa);
	snprintf(why, wl, "%s", !exists ? "interface missing" : !up ? "interface down" : have4 ? "ready" : ll ? "only a link-local IPv4 address" : "no IPv4 address");
	return exists && up && have4;
}

#ifdef __APPLE__
/* Same layout as XNU bsd/net/route.h struct rt_msghdr (the SDK ships no net/route.h) */
struct pn_rt_msghdr {
	u_short rtm_msglen;
	u_char rtm_version;
	u_char rtm_type;
	u_short rtm_index;
	int rtm_flags;
	int rtm_addrs;
	pid_t rtm_pid;
	int rtm_seq;
	int rtm_errno;
	int rtm_use;
	uint32_t rtm_inits;
	uint32_t rtm_metrics[14]; /* struct rt_metrics: 14 x 32 bit */
};
#define PN_RTF_GATEWAY 0x2
#define PN_RTA_DST 0x1
#define PN_RTA_GATEWAY 0x2
#define PN_RTA_NETMASK 0x4
#define PN_ROUNDUP32(a) ((a) > 0 ? (1 + (((a)-1) | (sizeof(uint32_t) - 1))) : sizeof(uint32_t))

/* 1 = IPv4 default route via interface index idx, 0 = none, -1 = could not read (why filled) */
static int default_route(unsigned idx, char *why, size_t wl)
{
	int mib[6] = { CTL_NET, PF_ROUTE, 0, AF_INET, NET_RT_DUMP, 0 };
	size_t len = 0;
	if (sysctl(mib, 6, NULL, &len, NULL, 0) != 0) {
		snprintf(why, wl, "sysctl NET_RT_DUMP size: %s", strerror(errno));
		return -1;
	}
	len += len / 8 + 1024;
	char *buf = malloc(len);
	if (!buf) {
		snprintf(why, wl, "out of memory");
		return -1;
	}
	if (sysctl(mib, 6, buf, &len, NULL, 0) != 0) {
		snprintf(why, wl, "sysctl NET_RT_DUMP: %s", strerror(errno));
		free(buf);
		return -1;
	}
	int found = 0;
	for (size_t off = 0; off + sizeof(struct pn_rt_msghdr) <= len;) {
		const struct pn_rt_msghdr *h = (const void *)(buf + off);
		if (h->rtm_msglen < sizeof *h || off + h->rtm_msglen > len)
			break;
		const char *p = (const char *)h + sizeof *h, *end = (const char *)h + h->rtm_msglen;
		uint32_t dst = 1, mask = 1;
		int have_dst = 0;
		for (int bit = 0; bit < 8 && p < end; bit++) {
			if (!(h->rtm_addrs & (1 << bit)))
				continue;
			const struct sockaddr *sa = (const void *)p;
			uint32_t v = 0;
			if (sa->sa_len >= 8) {
				const struct sockaddr_in *sin = (const void *)p;
				v = sin->sin_addr.s_addr;
			}
			if ((1 << bit) == PN_RTA_DST) {
				have_dst = sa->sa_family == AF_INET;
				dst = v;
			} else if ((1 << bit) == PN_RTA_NETMASK) {
				mask = v;
			}
			p += PN_ROUNDUP32(sa->sa_len);
		}
		if (have_dst && dst == 0 && (h->rtm_flags & PN_RTF_GATEWAY) && ((h->rtm_addrs & PN_RTA_NETMASK) ? mask == 0 : 1) && h->rtm_index == idx)
			found = 1;
		off += h->rtm_msglen;
	}
	free(buf);
	return found;
}
#else
static int default_route(unsigned idx, char *why, size_t wl)
{
	char line[256], nm[32];
	unsigned long dst, flags;
	FILE *f = fopen("/proc/net/route", "r");
	char want[IF_NAMESIZE] = "";
	if (!f) {
		snprintf(why, wl, "open /proc/net/route: %s", strerror(errno));
		return -1;
	}
	if_indextoname(idx, want);
	int found = 0;
	if (!fgets(line, sizeof line, f)) {
		fclose(f);
		return 0;
	}
	while (fgets(line, sizeof line, f))
		if (sscanf(line, "%31s %lx %*x %lx", nm, &dst, &flags) == 3 && dst == 0 && (flags & 2) && !strcmp(nm, want))
			found = 1;
	fclose(f);
	return found;
}
#endif

/* ---- sockets ---- */
/* 0 = bound, -1 = not bound (err filled): a connection that is not bound to the interface is never used */
static int bind_iface(int fd, char *err, size_t el)
{
#ifdef IP_BOUND_IF
	int idx = (int)if_nametoindex(ifname);
	if (idx == 0) {
		snprintf(err, el, "if_nametoindex(%s) failed: %s (socket not bound to the interface)", ifname, strerror(errno));
		return -1;
	}
	if (setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &idx, sizeof idx) != 0) {
		snprintf(err, el, "IP_BOUND_IF %s=%d failed: %s", ifname, idx, strerror(errno));
		return -1;
	}
#else
	(void)fd; /* host test build: no IP_BOUND_IF; the iface step already says so once */
	(void)err;
	(void)el;
#endif
	return 0;
}

/* nonblocking TCP connect with timeout; returns fd >= 0 or -1 with err filled; *ms = connect time */
static int tcp_connect(uint32_t addr_be, int port, int timeout_ms, int64_t *ms, char *err, size_t el)
{
	int fd = socket(AF_INET, SOCK_STREAM, 0);
	if (fd < 0) {
		snprintf(err, el, "socket: %s", strerror(errno));
		return -1;
	}
	int one = 1;
#ifdef SO_NOSIGPIPE
	setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
	(void)one;
	if (bind_iface(fd, err, el) != 0) {
		close(fd);
		return -1;
	}
	fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
	struct sockaddr_in sa;
	memset(&sa, 0, sizeof sa);
	sa.sin_family = AF_INET;
	sa.sin_port = htons((uint16_t)port);
	sa.sin_addr.s_addr = addr_be;
	int64_t t0 = now_ms();
	if (connect(fd, (struct sockaddr *)&sa, sizeof sa) != 0 && errno != EINPROGRESS) {
		snprintf(err, el, "connect: %s", strerror(errno));
		close(fd);
		return -1;
	}
	struct pollfd p = { .fd = fd, .events = POLLOUT };
	int pr;
	do
		pr = poll(&p, 1, timeout_ms);
	while (pr < 0 && errno == EINTR);
	if (pr == 0) {
		snprintf(err, el, "connect timeout after %d ms", timeout_ms);
		close(fd);
		return -1;
	}
	int soerr = 0;
	socklen_t sl = sizeof soerr;
	if (pr < 0 || getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &sl) != 0 || soerr != 0) {
		snprintf(err, el, "connect: %s", strerror(pr < 0 ? errno : soerr));
		close(fd);
		return -1;
	}
	*ms = now_ms() - t0;
	return fd;
}

/* ---- connection with optional TLS ---- */
struct conn {
	int fd;
	int tls;
	char err[200];
#ifdef __APPLE__
	SSLContextRef ctx;
#endif
};

static int io_wait(int fd, short ev, int tmo_ms)
{
	struct pollfd p = { .fd = fd, .events = ev };
	int pr;
	do
		pr = poll(&p, 1, tmo_ms);
	while (pr < 0 && errno == EINTR);
	return pr;
}

#ifdef __APPLE__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"

#define IO_TMO_MS 8000

static OSStatus st_read(SSLConnectionRef cr, void *data, size_t *len)
{
	struct conn *c = (struct conn *)cr;
	size_t want = *len, got = 0;
	while (got < want) {
		int pr = io_wait(c->fd, POLLIN, got == 0 ? IO_TMO_MS : 0);
		if (pr == 0) {
			if (got == 0) {
				*len = 0;
				return errSSLNetworkTimeout;
			}
			break;
		}
		if (pr < 0) {
			*len = 0;
			return errSSLInternal;
		}
		ssize_t n = recv(c->fd, (char *)data + got, want - got, 0);
		if (n == 0) {
			*len = got;
			return got ? errSSLWouldBlock : errSSLClosedGraceful;
		}
		if (n < 0) {
			if (errno == EAGAIN || errno == EINTR)
				continue;
			*len = got;
			return errSSLClosedAbort;
		}
		got += (size_t)n;
	}
	*len = got;
	return got < want ? errSSLWouldBlock : noErr;
}

static OSStatus st_write(SSLConnectionRef cr, const void *data, size_t *len)
{
	struct conn *c = (struct conn *)cr;
	size_t want = *len, done = 0;
	while (done < want) {
		ssize_t n = send(c->fd, (const char *)data + done, want - done, 0);
		if (n < 0) {
			if (errno == EINTR)
				continue;
			if (errno == EAGAIN) {
				if (io_wait(c->fd, POLLOUT, IO_TMO_MS) <= 0) {
					*len = done;
					return errSSLNetworkTimeout;
				}
				continue;
			}
			*len = done;
			return errSSLClosedAbort;
		}
		done += (size_t)n;
	}
	*len = done;
	return noErr;
}

static void cf_to_buf(CFStringRef s, char *b, size_t n)
{
	if (!s || !CFStringGetCString(s, b, (CFIndex)n, kCFStringEncodingUTF8))
		snprintf(b, n, "(no text)");
	for (char *p = b; *p; p++)
		if (*p == '\n' || *p == '\r' || *p == '\t')
			*p = ' ';
}

static void ossta_text(OSStatus st, char *b, size_t n)
{
	CFStringRef s = SecCopyErrorMessageString(st, NULL);
	cf_to_buf(s, b, n);
	if (s)
		CFRelease(s);
}

static const char *proto_name(SSLProtocol p)
{
	switch (p) {
	case kTLSProtocol1:
		return "TLS1.0";
	case kTLSProtocol11:
		return "TLS1.1";
	case kTLSProtocol12:
		return "TLS1.2";
	case kTLSProtocol13:
		return "TLS1.3";
	default:
		return "other";
	}
}

/* evaluates the peer trust for host; returns 1 ok; fills a text like "trust=ok chain=3" or "trust=fail code=N result=R msg=..." */
static int eval_trust(SSLContextRef ctx, const char *host, char *out, size_t on)
{
	SecTrustRef trust = NULL;
	OSStatus st = SSLCopyPeerTrust(ctx, &trust);
	if (st != noErr || !trust) {
		snprintf(out, on, "trust=none SSLCopyPeerTrust=%d", (int)st);
		return 0;
	}
	CFStringRef h = CFStringCreateWithCString(kCFAllocatorDefault, host, kCFStringEncodingUTF8);
	SecPolicyRef pol = SecPolicyCreateSSL(true, h);
	SecTrustSetPolicies(trust, pol);
	CFErrorRef cerr = NULL;
	bool ok = SecTrustEvaluateWithError(trust, &cerr);
	SecTrustResultType tr = 0;
	SecTrustGetTrustResult(trust, &tr);
	long chain = SecTrustGetCertificateCount(trust);
	if (ok) {
		snprintf(out, on, "trust=ok chain=%ld result=%d", chain, (int)tr);
	} else {
		char m[160];
		CFStringRef d = cerr ? CFErrorCopyDescription(cerr) : NULL;
		cf_to_buf(d, m, sizeof m);
		snprintf(out, on, "trust=fail code=%ld result=%d chain=%ld msg=\"%s\"", cerr ? (long)CFErrorGetCode(cerr) : -1L, (int)tr, chain, m);
		if (d)
			CFRelease(d);
	}
	if (cerr)
		CFRelease(cerr);
	CFRelease(pol);
	CFRelease(h);
	CFRelease(trust);
	return ok;
}

/* TLS handshake on an open connection. verify: abort on a failed trust evaluation. 0 = ok. info = key=value text for the line. */
static int tls_handshake(struct conn *c, const char *host, int verify, char *info, size_t in, int *trust_ok)
{
	int evaluated = 0; /* the trust evaluation really ran (errSSLServerAuthCompleted seen) */
	char tinfo[400] = "trust=not-reached";
	*trust_ok = 0;
	c->ctx = SSLCreateContext(kCFAllocatorDefault, kSSLClientSide, kSSLStreamType);
	if (!c->ctx) {
		snprintf(info, in, "SSLCreateContext returned NULL");
		return -1;
	}
	OSStatus st = SSLSetIOFuncs(c->ctx, st_read, st_write);
	if (st == noErr)
		st = SSLSetConnection(c->ctx, c);
	if (st == noErr)
		st = SSLSetPeerDomainName(c->ctx, host, strlen(host));
	if (st == noErr)
		st = SSLSetSessionOption(c->ctx, kSSLSessionOptionBreakOnServerAuth, true);
	if (st == noErr)
		st = SSLSetProtocolVersionMin(c->ctx, kTLSProtocol12);
	if (st != noErr) {
		snprintf(info, in, "setup OSStatus=%d", (int)st);
		return -1;
	}
	int64_t t0 = now_ms();
	for (;;) {
		if (now_ms() - t0 > 20000) {
			snprintf(info, in, "handshake did not finish in 20000 ms %s", tinfo);
			return -1;
		}
		st = SSLHandshake(c->ctx);
		if (st == errSSLServerAuthCompleted) {
			evaluated = 1;
			*trust_ok = eval_trust(c->ctx, host, tinfo, sizeof tinfo);
			if (verify && !*trust_ok) {
				snprintf(info, in, "OSStatus=%d %s", (int)st, tinfo);
				return -1;
			}
			continue;
		}
		if (st == errSSLWouldBlock)
			continue; /* the read callback already waited up to IO_TMO_MS; the deadline above bounds the loop */
		break;
	}
	if (st != noErr) {
		char m[160];
		ossta_text(st, m, sizeof m);
		snprintf(info, in, "OSStatus=%d msg=\"%s\" %s", (int)st, m, tinfo);
		return -1;
	}
	if (verify && !evaluated) {
		snprintf(info, in, "handshake finished without trust evaluation (errSSLServerAuthCompleted never seen) %s", tinfo);
		return -1;
	}
	SSLProtocol pv = kSSLProtocolUnknown;
	SSLCipherSuite cs = 0;
	SSLGetNegotiatedProtocolVersion(c->ctx, &pv);
	SSLGetNegotiatedCipher(c->ctx, &cs);
	snprintf(info, in, "proto=%s cipher=0x%04x %s", proto_name(pv), (unsigned)cs, tinfo);
	c->tls = 1;
	return 0;
}
#pragma clang diagnostic pop
#endif /* __APPLE__ */

static void conn_close(struct conn *c)
{
#ifdef __APPLE__
	if (c->ctx) {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
		if (c->tls)
			SSLClose(c->ctx);
		CFRelease(c->ctx);
#pragma clang diagnostic pop
		c->ctx = NULL;
	}
#endif
	if (c->fd >= 0)
		close(c->fd);
	c->fd = -1;
	c->tls = 0;
}

static int conn_write(struct conn *c, const char *b, size_t n)
{
	size_t done = 0;
	while (done < n) {
#ifdef __APPLE__
		if (c->tls) {
			size_t pr = 0;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
			OSStatus st = SSLWrite(c->ctx, b + done, n - done, &pr);
#pragma clang diagnostic pop
			done += pr;
			if (st != noErr && st != errSSLWouldBlock) {
				snprintf(c->err, sizeof c->err, "SSLWrite OSStatus=%d", (int)st);
				return -1;
			}
			continue;
		}
#endif
		ssize_t w = send(c->fd, b + done, n - done, 0);
		if (w < 0) {
			if (errno == EINTR)
				continue;
			if (errno == EAGAIN && io_wait(c->fd, POLLOUT, 8000) > 0)
				continue;
			snprintf(c->err, sizeof c->err, "send: %s", errno == EAGAIN ? "timeout" : strerror(errno));
			return -1;
		}
		done += (size_t)w;
	}
	return 0;
}

/* >0 bytes, 0 clean EOF, -1 error/timeout (c->err) */
static ssize_t conn_read(struct conn *c, char *b, size_t n, int tmo_ms)
{
#ifdef __APPLE__
	if (c->tls) {
		int64_t t0 = now_ms();
		for (;;) {
			size_t pr = 0;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
			OSStatus st = SSLRead(c->ctx, b, n, &pr);
#pragma clang diagnostic pop
			if (pr > 0)
				return (ssize_t)pr;
			if (st == errSSLClosedGraceful || st == errSSLClosedNoNotify)
				return 0;
			if (st == errSSLWouldBlock && now_ms() - t0 < tmo_ms)
				continue;
			snprintf(c->err, sizeof c->err, "SSLRead OSStatus=%d", (int)st);
			return -1;
		}
	}
#endif
	for (;;) {
		int pr = io_wait(c->fd, POLLIN, tmo_ms);
		if (pr == 0) {
			snprintf(c->err, sizeof c->err, "read timeout after %d ms", tmo_ms);
			return -1;
		}
		if (pr < 0) {
			snprintf(c->err, sizeof c->err, "poll: %s", strerror(errno));
			return -1;
		}
		ssize_t r = recv(c->fd, b, n, 0);
		if (r < 0 && (errno == EAGAIN || errno == EINTR))
			continue;
		if (r < 0) {
			snprintf(c->err, sizeof c->err, "recv: %s", strerror(errno));
			return -1;
		}
		return r;
	}
}

/* ---- HTTP/1.x GET or HEAD, Connection: close; reads to EOF (or maxbody / deadline). 0 = got a status line. ---- */
struct http {
	int status;
	char date[64];
	int64_t body;
	int complete; /* 1 = EOF reached */
};

static int http_do(struct conn *c, const char *method, const char *host, int port, const char *path, struct http *h, int64_t maxbody, int64_t deadline_ms)
{
	char req[1024], hdr[8192];
	size_t hl = 0, rx = 0;
	int have_hdr = 0;
	memset(h, 0, sizeof *h);
	char hosthdr[300];
	if (port == 80 || port == 443)
		snprintf(hosthdr, sizeof hosthdr, "%s", host);
	else
		snprintf(hosthdr, sizeof hosthdr, "%s:%d", host, port);
	snprintf(req, sizeof req, "%s %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: pixnet\r\nAccept: */*\r\nConnection: close\r\n\r\n", method, path, hosthdr);
	if (conn_write(c, req, strlen(req)) != 0)
		return -1;
	static char buf[16384];
	for (;;) {
		int64_t left = deadline_ms - now_ms();
		if (left <= 0) {
			if (!have_hdr) {
				snprintf(c->err, sizeof c->err, "deadline reached before the response headers");
				return -1;
			}
			break;
		}
		ssize_t n = conn_read(c, buf, sizeof buf, left > 8000 ? 8000 : (int)left);
		if (n < 0)
			return -1; /* c->err is set: a read error or timeout, also after the headers, is never a normal end */
		if (n == 0) {
			h->complete = 1;
			break;
		}
		rx += (size_t)n;
		if (!have_hdr) {
			size_t take = (size_t)n < sizeof hdr - 1 - hl ? (size_t)n : sizeof hdr - 1 - hl;
			memcpy(hdr + hl, buf, take);
			hl += take;
			hdr[hl] = 0;
			char *e = strstr(hdr, "\r\n\r\n");
			if (!e) {
				if (hl >= sizeof hdr - 1) {
					snprintf(c->err, sizeof c->err, "response header longer than %zu bytes", sizeof hdr - 1);
					return -1;
				}
				continue;
			}
			have_hdr = 1;
			size_t hdrlen = (size_t)(e - hdr) + 4;
			if (sscanf(hdr, "HTTP/%*d.%*d %d", &h->status) != 1) {
				snprintf(c->err, sizeof c->err, "no HTTP status line");
				return -1;
			}
			for (char *l = strstr(hdr, "\r\n"); l && l < e; l = strstr(l + 2, "\r\n")) {
				if (strncasecmp(l + 2, "Date:", 5) == 0) {
					char *v = l + 7;
					while (*v == ' ')
						v++;
					size_t k = strcspn(v, "\r");
					if (k >= sizeof h->date)
						k = sizeof h->date - 1;
					memcpy(h->date, v, k);
					h->date[k] = 0;
					break;
				}
			}
			h->body = (int64_t)(rx - hdrlen); /* the header terminator was found inside the copied bytes, so rx >= hdrlen */
		} else {
			h->body += n;
		}
		if (strcmp(method, "HEAD") == 0 && have_hdr) {
			h->complete = 1;
			break;
		}
		if (h->body >= maxbody)
			break;
	}
	return have_hdr ? 0 : -1;
}

/* body verdict of a finished GET (http_do returned 0): 0 = ok, -1 = FAIL with why filled.
 * A status >= 400 (or none) fails; a body that ended before EOF, maxbody or the deadline is a truncated transfer. */
static int get_verdict(const struct http *h, int64_t maxbody, int64_t deadline_ms, char *why, size_t wn)
{
	if (h->status < 100 || h->status >= 400) {
		snprintf(why, wn, "HTTP status %d", h->status);
		return -1;
	}
	if (!h->complete && h->body < maxbody && now_ms() < deadline_ms) {
		snprintf(why, wn, "connection ended early after %lld body bytes", (long long)h->body);
		return -1;
	}
	return 0;
}

/* ---- DNS with a timeout ---- */
struct gai {
	int pipefd[2];
	char name[256];
	int rc;
	struct addrinfo *res;
	pthread_mutex_t m;
	int abandoned;
};

static void *gai_thread(void *arg)
{
	struct gai *g = arg;
	struct addrinfo hints;
	memset(&hints, 0, sizeof hints);
	hints.ai_family = AF_UNSPEC;
	hints.ai_socktype = SOCK_STREAM;
	struct addrinfo *r = NULL;
	int rc = getaddrinfo(g->name, NULL, &hints, &r);
	pthread_mutex_lock(&g->m);
	g->rc = rc;
	g->res = r;
	if (g->abandoned) {
		pthread_mutex_unlock(&g->m);
		if (r)
			freeaddrinfo(r);
		close(g->pipefd[0]);
		close(g->pipefd[1]);
		free(g);
		return NULL;
	}
	(void)!write(g->pipefd[1], "x", 1);
	pthread_mutex_unlock(&g->m);
	return NULL;
}

struct dnsres {
	int rc;
	char err[200];
	int n4, n6;
	uint32_t first4_be;
	int have4;
	int64_t ms;
};

static int resolve(const char *name, struct dnsres *d, int tmo_ms)
{
	memset(d, 0, sizeof *d);
	struct gai *g = calloc(1, sizeof *g);
	if (!g || pipe(g->pipefd) != 0) {
		snprintf(d->err, sizeof d->err, "resolver setup: %s", strerror(errno));
		free(g);
		return -1;
	}
	pthread_mutex_init(&g->m, NULL);
	snprintf(g->name, sizeof g->name, "%s", name);
	pthread_t th;
	int64_t t0 = now_ms();
	if (pthread_create(&th, NULL, gai_thread, g) != 0) {
		snprintf(d->err, sizeof d->err, "pthread_create failed");
		close(g->pipefd[0]);
		close(g->pipefd[1]);
		free(g);
		return -1;
	}
	pthread_detach(th);
	int pr = io_wait(g->pipefd[0], POLLIN, tmo_ms);
	d->ms = now_ms() - t0;
	pthread_mutex_lock(&g->m);
	if (pr <= 0 && !g->res && g->rc == 0) {
		g->abandoned = 1; /* the worker frees everything when getaddrinfo returns */
		pthread_mutex_unlock(&g->m);
		snprintf(d->err, sizeof d->err, "getaddrinfo timeout after %d ms (resolver thread abandoned)", tmo_ms);
		return -1;
	}
	pthread_mutex_unlock(&g->m);
	d->rc = g->rc;
	if (g->rc != 0) {
		snprintf(d->err, sizeof d->err, "getaddrinfo rc=%d: %s", g->rc, gai_strerror(g->rc));
	} else {
		for (struct addrinfo *a = g->res; a; a = a->ai_next) {
			if (a->ai_family == AF_INET) {
				if (!d->have4) {
					d->first4_be = ((struct sockaddr_in *)(void *)a->ai_addr)->sin_addr.s_addr;
					d->have4 = 1;
				}
				d->n4++;
			} else if (a->ai_family == AF_INET6) {
				d->n6++;
			}
		}
	}
	if (g->res)
		freeaddrinfo(g->res);
	close(g->pipefd[0]);
	close(g->pipefd[1]);
	free(g);
	return d->rc == 0 ? 0 : -1;
}

/* ---- URL ---- */
struct url {
	int https;
	char host[256];
	int port;
	char path[512];
};

static int parse_url(const char *u, struct url *o)
{
	const char *p;
	memset(o, 0, sizeof *o);
	if (!strncmp(u, "http://", 7)) {
		o->https = 0;
		p = u + 7;
		o->port = 80;
	} else if (!strncmp(u, "https://", 8)) {
		o->https = 1;
		p = u + 8;
		o->port = 443;
	} else
		return -1;
	size_t hl = strcspn(p, ":/");
	if (hl == 0 || hl >= sizeof o->host)
		return -1;
	memcpy(o->host, p, hl);
	if (!valid_name(o->host))
		return -1;
	p += hl;
	if (*p == ':') {
		char *e;
		long v = strtol(p + 1, &e, 10);
		if (e == p + 1 || v < 1 || v > 65535 || (*e != '/' && *e != 0))
			return -1;
		o->port = (int)v;
		p = e;
	}
	const char *path = *p ? p : "/";
	if (strlen(path) >= sizeof o->path)
		return -1;
	for (const char *q = path; *q; q++)
		if ((unsigned char)*q <= ' ' || *q == 0x7f)
			return -1;
	snprintf(o->path, sizeof o->path, "%s", path);
	return 0;
}

/* 0 = clock set, -1 = settimeofday failed, -2 = Date not parseable, -3 = refused (HTTP status not 2xx/3xx, or year outside 2024..2100):
 * settimeofday is not reached for -2/-3 */
static int set_clock_from_date(const char *date, int status, char *msg, size_t mn)
{
	struct tm tm;
	memset(&tm, 0, sizeof tm);
	char *e = strptime(date, "%a, %d %b %Y %H:%M:%S", &tm);
	if (!e)
		return -2;
	if (status < 200 || status > 399) {
		snprintf(msg, mn, "refused: HTTP status %d is not 2xx/3xx", status);
		return -3;
	}
	if (tm.tm_year + 1900 < 2024 || tm.tm_year + 1900 > 2100) {
		snprintf(msg, mn, "refused: server year %d is outside 2024..2100", tm.tm_year + 1900);
		return -3;
	}
	time_t t = timegm(&tm);
	time_t old = time(NULL);
	struct timeval tv = { .tv_sec = t, .tv_usec = 0 };
	if (settimeofday(&tv, NULL) != 0) {
		snprintf(msg, mn, "settimeofday errno=%d (%s)", errno, strerror(errno));
		return -1;
	}
	snprintf(msg, mn, "settimeofday old_epoch=%lld new_epoch=%lld", (long long)old, (long long)t);
	return 0;
}

static int parse_date(const char *date, time_t *out)
{
	struct tm tm;
	memset(&tm, 0, sizeof tm);
	if (!strptime(date, "%a, %d %b %Y %H:%M:%S", &tm))
		return -1;
	*out = timegm(&tm);
	return 0;
}

/* open a conn to addr:port; if tls, handshake (verify flag). Prints nothing; 0 = ok. */
static int open_conn(struct conn *c, uint32_t addr_be, int port, const char *host, int tls, int verify, char *info, size_t in)
{
	int64_t ms;
	memset(c, 0, sizeof *c);
	c->fd = tcp_connect(addr_be, port, 10000, &ms, info, in);
	if (c->fd < 0)
		return -1;
	if (!tls)
		return 0;
#ifdef __APPLE__
	int tok;
	if (tls_handshake(c, host, verify, info, in, &tok) != 0) {
		conn_close(c);
		return -1;
	}
	return 0;
#else
	(void)host;
	(void)verify;
	snprintf(info, in, "no TLS API on this build (host test build)");
	conn_close(c);
	return -2;
#endif
}

int main(int argc, char **argv)
{
	int wait_s = 120, delay_s = 0, set_clock = 0, skip_route = 0, http_port = 80, dl_s = 0, opt;
	const char *addr_s = "1.1.1.1", *name = "one.one.one.one", *url_s = NULL;
	setvbuf(stdout, NULL, _IONBF, 0);
	signal(SIGPIPE, SIG_IGN);
	while ((opt = getopt(argc, argv, "i:W:D:a:n:TRP:d:u:")) != -1) {
		char *end;
		long v;
		switch (opt) {
		case 'W':
		case 'D':
		case 'd':
			v = strtol(optarg, &end, 10);
			if (*optarg == '\0' || *end != '\0' || v < 0 || v > (opt == 'd' ? 3600 : 86400)) {
				printf("pixnet: GAP bad value for -%c: '%s'\n", opt, optarg);
				usage();
				return 2;
			}
			if (opt == 'W')
				wait_s = (int)v;
			else if (opt == 'D')
				delay_s = (int)v;
			else
				dl_s = (int)v;
			break;
		case 'i':
			ifname = optarg;
			break;
		case 'a':
			addr_s = optarg;
			break;
		case 'n':
			name = optarg;
			break;
		case 'u':
			url_s = optarg;
			break;
		case 'T':
			set_clock = 1;
			break;
		case 'R':
			skip_route = 1;
			break;
		case 'P':
			v = strtol(optarg, &end, 10);
			if (*optarg == '\0' || *end != '\0' || v < 1 || v > 65535) {
				printf("pixnet: GAP bad value for -P: '%s'\n", optarg);
				usage();
				return 2;
			}
			http_port = (int)v;
			break;
		default:
			usage();
			return 2;
		}
	}
	if (optind != argc) {
		printf("pixnet: GAP unexpected argument '%s'\n", argv[optind]);
		usage();
		return 2;
	}
	struct in_addr lit;
	if (inet_pton(AF_INET, addr_s, &lit) != 1) {
		printf("pixnet: GAP -a '%s' is not a dotted IPv4 address\n", addr_s);
		usage();
		return 2;
	}
	if (!valid_name(name)) {
		printf("pixnet: GAP -n '%s' is not a plain host name\n", name);
		usage();
		return 2;
	}
	if (strlen(ifname) >= IFNAMSIZ) {
		printf("pixnet: GAP interface name '%s' too long\n", ifname);
		return 2;
	}
	struct url url;
	if (url_s && parse_url(url_s, &url) != 0) {
		printf("pixnet: GAP -u is not a plain http:// or https:// URL\n");
		usage();
		return 2;
	}
	printf("pixnet: start iface=%s wait=%ds name=%s set_clock=%d download_s=%d\n", ifname, wait_s, name, set_clock, dl_s);
	if (delay_s > 0) {
		printf("pixnet: delay %d s before the start\n", delay_s);
		sleep((unsigned)delay_s);
	}

	/* a. clock */
	int clock_ok = step_clock("clock");

	/* b. iface + default route: wait */
	char why[96] = "", rwhy[96] = "";
	int64_t t0 = now_ms(), lastp = -5000;
	int ready = 0;
	const char *route_note = "yes";
	for (;;) {
		int a = iface_addr(why, sizeof why);
		int r = a && !skip_route ? default_route(if_nametoindex(ifname), rwhy, sizeof rwhy) : 0;
		if (a && skip_route) {
			ready = 1;
			route_note = "skipped(-R)";
			break;
		}
		if (a && r < 0) {
			/* the route table could not be read or parsed: do not block the run on an unverified parser, check the address only */
			res("GAP", "route", "unreadable: %s; proceeding with the address check only", rwhy);
			ready = 1;
			route_note = "unreadable";
			break;
		}
		if (a && r > 0) {
			ready = 1;
			break;
		}
		int64_t el = now_ms() - t0;
		if (el >= (int64_t)wait_s * 1000) {
			res("FAIL", "iface", "iface=%s addr=%s route=%s waited_s=%d (%s)", ifname, a ? "yes" : "no", r > 0 ? "yes" : "no", wait_s,
			    !a ? why : "no default route via this interface");
			break;
		}
		if (el - lastp >= 5000) {
			printf("pixnet: waiting for %s: %s (%lld/%d s)\n", ifname, !a ? why : "no default route yet", (long long)(el / 1000), wait_s);
			lastp = el;
		}
		poll(NULL, 0, 500);
	}
	if (!ready) {
		static const char *steps[] = { "tcp-ip", "dns", "tcp-name", "http-date", "tls", "tls-noverify", "https-get", "download" };
		for (unsigned i = 0; i < sizeof steps / sizeof *steps; i++)
			res("GAP", steps[i], "skipped: interface not ready");
		printf("pixnet: RESULT pass=%d fail=%d gap=%d\n", n_pass, n_fail, n_gap);
		return 3;
	}
	res("PASS", "iface", "iface=%s addr=yes route=%s after_ms=%lld", ifname, route_note, (long long)(now_ms() - t0));

	/* c. tcp-ip */
	{
		char err[200];
		int64_t ms;
		int fd = tcp_connect(lit.s_addr, 443, 10000, &ms, err, sizeof err);
		if (fd >= 0) {
			res("PASS", "tcp-ip", "dst=%s port=443 connect_ms=%lld", addr_s, (long long)ms);
			close(fd);
		} else
			res("FAIL", "tcp-ip", "dst=%s port=443 err=\"%s\"", addr_s, err);
	}

	/* d. dns */
	struct dnsres dr;
	int dns_ok = resolve(name, &dr, 10000) == 0;
	if (dns_ok)
		res("PASS", "dns", "name=%s a=%d aaaa=%d ms=%lld unbound=resolver-not-interface-bound", name, dr.n4, dr.n6, (long long)dr.ms);
	else
		res("FAIL", "dns", "name=%s ms=%lld err=\"%s\"", name, (long long)dr.ms, dr.err);

	/* e. tcp-name */
	uint32_t peer_be = lit.s_addr;
	const char *peer_via = "literal-ip";
	if (!dns_ok) {
		res("GAP", "tcp-name", "skipped: dns failed");
	} else if (!dr.have4) {
		res("GAP", "tcp-name", "skipped: no A record (aaaa=%d only; this tool binds IPv4)", dr.n6);
	} else {
		char err[200];
		int64_t ms;
		int fd = tcp_connect(dr.first4_be, 443, 10000, &ms, err, sizeof err);
		if (fd >= 0) {
			res("PASS", "tcp-name", "name=%s port=443 connect_ms=%lld", name, (long long)ms);
			close(fd);
			peer_be = dr.first4_be;
			peer_via = "resolved-ip";
		} else
			res("FAIL", "tcp-name", "name=%s port=443 err=\"%s\"", name, err);
	}

	/* f. http-date (+ clock2) */
	int clock_attempted = 0;
	{
		struct conn c;
		char info[300];
		struct http h;
		if (open_conn(&c, lit.s_addr, http_port, name, 0, 0, info, sizeof info) != 0) {
			res("FAIL", "http-date", "dst=%s port=%d err=\"%s\"", addr_s, http_port, info);
		} else if (http_do(&c, "HEAD", name, http_port, "/", &h, 0, now_ms() + 15000) != 0) {
			res("FAIL", "http-date", "dst=%s port=%d err=\"%s\"", addr_s, http_port, c.err);
		} else {
			time_t se;
			if (!h.date[0])
				res("FAIL", "http-date", "dst=%s port=%d status=%d err=\"no Date header\"", addr_s, http_port, h.status);
			else if (parse_date(h.date, &se) != 0)
				res("FAIL", "http-date", "dst=%s port=%d status=%d err=\"Date header not parseable\"", addr_s, http_port, h.status);
			else {
				time_t me = time(NULL);
				res("PASS", "http-date", "dst=%s port=%d status=%d server_epoch=%lld local_minus_server_s=%lld", addr_s, http_port, h.status, (long long)se,
				    (long long)(me - se));
				if (set_clock && !clock_ok) {
					char m[160];
					int sr = set_clock_from_date(h.date, h.status, m, sizeof m);
						clock_attempted = 1;
					if (sr == 0)
						res("PASS", "clock-set", "%s", m);
					else
						res("FAIL", "clock-set", "%s", sr == -2 ? "Date header not parseable" : m);
					clock_ok = step_clock("clock2");
				} else if (set_clock) {
					res("GAP", "clock-set", "not needed: the clock step passed");
				}
			}
		}
		conn_close(&c);
	}
	if (set_clock == 0 && !clock_ok)
		res("GAP", "clock-set", "not requested (-T): the clock stays wrong, tls trust checks will fail");
	else if (set_clock && !clock_ok && !clock_attempted)
		res("GAP", "clock-set", "skipped: http-date did not give a usable Date (the clock stays wrong, tls trust checks will fail)");

	/* g. tls + tls-noverify, h. https-get */
	(void)peer_be;
	(void)peer_via;
	struct conn vc, nc;
	int have_v = 0, have_n = 0;
	memset(&vc, 0, sizeof vc);
	memset(&nc, 0, sizeof nc);
	vc.fd = nc.fd = -1;
#ifdef __APPLE__
	{
		char info[600];
		int rc = open_conn(&vc, peer_be, 443, name, 1, 1, info, sizeof info);
		if (rc == 0) {
			res("PASS", "tls", "name=%s via=%s %s", name, peer_via, info);
			have_v = 1;
		} else
			res("FAIL", "tls", "name=%s via=%s err=\"%s\"", name, peer_via, info);
		rc = open_conn(&nc, peer_be, 443, name, 1, 0, info, sizeof info);
		if (rc == 0) {
			res("PASS", "tls-noverify", "name=%s via=%s %s", name, peer_via, info);
			have_n = 1;
		} else
			res("FAIL", "tls-noverify", "name=%s via=%s err=\"%s\"", name, peer_via, info);
	}
#else
	res("GAP", "tls", "no usable TLS API (host test build: SecureTransport exists only on Apple; Network.framework has no headers in the iOS SDK)");
	res("GAP", "tls-noverify", "no usable TLS API (host test build)");
#endif
	{
		struct conn *use = have_v ? &vc : have_n ? &nc : NULL;
		if (!use) {
			res("GAP", "https-get", "skipped: no TLS session");
		} else {
			struct http h;
			char vw[100];
			int64_t gdl = now_ms() + 20000;
			if (http_do(use, "GET", name, 443, "/", &h, 65536, gdl) != 0)
				res("FAIL", "https-get", "name=%s session=%s err=\"%s\"", name, have_v ? "verified" : "noverify", use->err);
			else if (get_verdict(&h, 65536, gdl, vw, sizeof vw) != 0)
				res("FAIL", "https-get", "name=%s session=%s status=%d bytes=%lld err=\"%s\"", name, have_v ? "verified" : "noverify", h.status,
				    (long long)h.body, vw);
			else
				res("PASS", "https-get", "name=%s session=%s status=%d bytes=%lld", name, have_v ? "verified" : "noverify (NOT trust-checked)",
				    h.status, (long long)h.body);
		}
		conn_close(&vc);
		conn_close(&nc);
	}

	/* i. download */
	if (dl_s == 0) {
		res("GAP", "download", "skipped: -d SECONDS not given");
	} else if (!url_s) {
		res("GAP", "download", "skipped: -u URL not given (no default third-party file)");
	} else {
		struct dnsres ur;
		if (resolve(url.host, &ur, 10000) != 0 || !ur.have4) {
			res("FAIL", "download", "host resolve failed or no A record: %s", ur.err[0] ? ur.err : "no A record");
		} else {
			int64_t start = now_ms(), end = start + (int64_t)dl_s * 1000, total = 0;
			int reqs = 0, bad = 0;
			char lasterr[640] = "";
			while (now_ms() < end && !bad) {
				struct conn c;
				char info[600];
				struct http h;
				int rc = open_conn(&c, ur.first4_be, url.port, url.host, url.https, 1, info, sizeof info);
				if (rc != 0) {
					snprintf(lasterr, sizeof lasterr, "%s", info);
					bad = 1;
					break;
				}
				char vw[100];
				int hr = http_do(&c, "GET", url.host, url.port, url.path, &h, INT64_MAX, end);
				if (hr != 0 && now_ms() >= end && !strncmp(c.err, "deadline reached", 16)) {
					conn_close(&c); /* the test window closed before this request started: not a failure, not counted */
					break;
				}
				if (hr != 0) {
					snprintf(lasterr, sizeof lasterr, "%s", c.err);
					bad = 1;
				} else if (h.status != 200) {
					snprintf(lasterr, sizeof lasterr, "HTTP status %d", h.status);
					bad = 1;
				} else if (get_verdict(&h, INT64_MAX, end, vw, sizeof vw) != 0) {
					snprintf(lasterr, sizeof lasterr, "%s", vw);
					bad = 1;
				}
				total += h.body;
				reqs++;
				conn_close(&c);
			}
			int64_t el = now_ms() - start;
			double mbit = el > 0 ? (double)total * 8.0 / 1000.0 / (double)el : 0.0; /* bytes*8 / ms / 1000 = Mbit/s */
			if (bad)
				res("FAIL", "download", "scheme=%s requests=%d bytes=%lld ms=%lld mbit_s=%.2f err=\"%s\"", url.https ? "https" : "http", reqs, (long long)total,
				    (long long)el, mbit, lasterr);
			else
				res("PASS", "download", "scheme=%s requests=%d bytes=%lld ms=%lld mbit_s=%.2f", url.https ? "https" : "http", reqs, (long long)total, (long long)el, mbit);
		}
	}

	printf("pixnet: RESULT pass=%d fail=%d gap=%d\n", n_pass, n_fail, n_gap);
	return n_fail == 0 ? 0 : 1;
}
