/*
 * pixperf: Wi-Fi throughput client for PixOS (sprint-wifi Part A); the server is tools/pixperf-server on a LAN host.
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixperf [-t secs] [-u udp_kbit_s] [-l udp_len] [-W wait_iface_s] [-D delay_s] [-i iface] [-p port] [-T tests] HOST
 *   tests: comma list of down,up,udpup,udpdown (default all four, in that order)
 * The server address is never in git: it comes from the job plist/hot command line (PIXOS_PERF_HOST in the gitignored .env).
 * Every socket is bound to the interface (IP_BOUND_IF on Darwin, default en0) so test traffic cannot take the USB network path.
 * The server's numbers are the ones that count; this client prints its own view next to them (`pixperf: RESULT ...`).
 * While each test runs, a thread sends ICMP echo to HOST every 200 ms (unprivileged SOCK_DGRAM/IPPROTO_ICMP, as tools/pixping.c)
 * and the summary has the p50/p90/max RTT.
 * Exit: 0 all tests ran, 1 a test failed, 2 usage, 3 interface never ready.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netinet/ip_icmp.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define MAGIC 0x50585046u /* "PXPF" */
#define MAXRTT 4096

static const char *ifn = "en0";
static unsigned ifidx;
static struct sockaddr_in srv;

static int64_t now_us(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static int bind_if(int s)
{
#ifdef IP_BOUND_IF
	if (setsockopt(s, IPPROTO_IP, IP_BOUND_IF, &ifidx, sizeof ifidx) != 0) {
		printf("pixperf: GAP IP_BOUND_IF %s failed: %s\n", ifn, strerror(errno));
		return -1;
	}
#else
	(void)s;
#endif
	return 0;
}

static int iface_ready(void)
{
	struct ifaddrs *ia, *p;
	int ok = 0;
	if (getifaddrs(&ia) != 0) return 0;
	for (p = ia; p; p = p->ifa_next)
		if (p->ifa_addr && p->ifa_addr->sa_family == AF_INET && strcmp(p->ifa_name, ifn) == 0 && (p->ifa_flags & IFF_UP)) ok = 1;
	freeifaddrs(ia);
	return ok;
}

/* ---- RTT thread */
static volatile int ping_run;
static double rtts[MAXRTT];
static int nrtt, nping;

static void *pinger(void *arg)
{
	int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP);
	uint16_t seq = 0;
	(void)arg;
	if (s < 0) { printf("pixperf: GAP icmp socket: %s\n", strerror(errno)); return NULL; }
	bind_if(s);
	while (ping_run) {
		unsigned char pkt[64], rb[256];
		struct icmp *ic = (struct icmp *)pkt;
		int64_t t0 = now_us(), dl;
		memset(pkt, 0, sizeof pkt);
		ic->icmp_type = ICMP_ECHO; ic->icmp_id = htons(0x5050); ic->icmp_seq = htons(++seq);
		memcpy(pkt + 8, &t0, sizeof t0);
		{ uint32_t sum = 0; int i; for (i = 0; i < (int)sizeof pkt; i += 2) sum += (uint32_t)(pkt[i] << 8 | pkt[i + 1]); while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16); ic->icmp_cksum = htons((uint16_t)~sum); }
		nping++;
		if (sendto(s, pkt, sizeof pkt, 0, (struct sockaddr *)&srv, sizeof srv) < 0) { usleep(200000); continue; }
		dl = t0 + 1000000;
		while (now_us() < dl) {
			struct pollfd pf = { s, POLLIN, 0 };
			int to = (int)((dl - now_us()) / 1000), n, off;
			if (poll(&pf, 1, to < 0 ? 0 : to) <= 0) break;
			n = (int)recv(s, rb, sizeof rb, 0);
			if (n < 8) continue;
			off = (rb[0] >> 4) == 4 ? (rb[0] & 15) * 4 : 0;   /* Darwin keeps the IP header, Linux strips it */
			if (n - off >= 16 && rb[off] == ICMP_ECHOREPLY && rb[off + 6] == (seq >> 8) && rb[off + 7] == (seq & 255)) {
				if (nrtt < MAXRTT) rtts[nrtt++] = (now_us() - t0) / 1000.0;
				break;
			}
		}
		{ int64_t left = t0 + 200000 - now_us(); if (left > 0) usleep((useconds_t)left); }
	}
	close(s);
	return NULL;
}

static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return x < y ? -1 : x > y; }

static void rtt_summary(char *o, size_t n)
{
	if (nrtt == 0) { snprintf(o, n, "rtt_p50_ms=none sent=%d got=0", nping); return; }
	qsort(rtts, (size_t)nrtt, sizeof rtts[0], cmpd);
	snprintf(o, n, "rtt_p50_ms=%.1f rtt_p90_ms=%.1f rtt_max_ms=%.1f pings=%d/%d", rtts[nrtt / 2], rtts[nrtt * 9 / 10], rtts[nrtt - 1], nrtt, nping);
}

/* ---- one test */
static int ctl_open(const char *test, int secs, int kbit, int len)
{
	char req[128];
	int c = socket(AF_INET, SOCK_STREAM, 0), big = 4 << 20, one = 1;
	if (c < 0) return -1;
	bind_if(c);
	setsockopt(c, SOL_SOCKET, SO_RCVBUF, &big, sizeof big);
	setsockopt(c, SOL_SOCKET, SO_SNDBUF, &big, sizeof big);
	setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
	if (connect(c, (struct sockaddr *)&srv, sizeof srv) != 0) { printf("pixperf: GAP %s: connect: %s\n", test, strerror(errno)); close(c); return -1; }
	snprintf(req, sizeof req, "PIXPERF1 %s %d %d %d\n", test, secs, kbit, len);
	if (send(c, req, strlen(req), 0) < 0) { close(c); return -1; }
	return c;
}

/* UDPDOWN-ACCT-BEGIN (lowclk A3 prep: one row per second during udpdown; tools/test_pixperf_udpdown_acct.py compiles exactly this block)
 * got = unique datagrams received this second; lost = growth this second of the gap-based loss (maxseq - got_total, the quantity the final lost_upto_max reports),
 * clamped at 0: a late datagram that fills an older gap shrinks the cumulative gap, and the clamp keeps every row a loss count (rows can then sum to more than the
 * final lost_upto_max). bytes = payload bytes of the unique datagrams this second. The final RESULT line and the server's GOT line do not use this block. */
typedef struct { uint32_t t, got_prev, lost_prev; long long bytes_prev; } udd_acct_t;

static void udd_tick(udd_acct_t *a, uint32_t got, uint32_t maxs, long long bytes, char *o, size_t n)
{
	uint32_t lost_cum = maxs - got;                          /* got counts unique seqs, each < maxs, so maxs >= got */
	int64_t d = (int64_t)lost_cum - (int64_t)a->lost_prev;
	a->t++;
	snprintf(o, n, "PIXPERF UDPDOWN t=%u got=%u lost=%u bytes=%lld", a->t, got - a->got_prev, (unsigned)(d > 0 ? d : 0), bytes - a->bytes_prev);
	a->got_prev = got; a->lost_prev = lost_cum; a->bytes_prev = bytes;
}
/* UDPDOWN-ACCT-END */

static int run_test(const char *test, int secs, int kbit, int len)
{
	static unsigned char buf[65536];
	pthread_t th;
	int64_t t0, el;
	long long bytes = 0;
	char extra[200] = "", rt[160];
	int c = ctl_open(test, secs, kbit, len), rc = 0;
	if (c < 0) return 1;
	nrtt = nping = 0; ping_run = 1;
	pthread_create(&th, NULL, pinger, NULL);
	t0 = now_us();
	if (strcmp(test, "down") == 0) {
		ssize_t n;
		while ((n = recv(c, buf, sizeof buf, 0)) > 0) bytes += n;
		if (n < 0) { printf("pixperf: GAP down recv: %s\n", strerror(errno)); rc = 1; }
	} else if (strcmp(test, "up") == 0) {
		memset(buf, 0x5a, sizeof buf);
		while (now_us() - t0 < (int64_t)secs * 1000000) {
			ssize_t n = send(c, buf, sizeof buf, 0);
			if (n < 0) { printf("pixperf: GAP up send: %s\n", strerror(errno)); rc = 1; break; }
			bytes += n;
		}
		shutdown(c, SHUT_WR);
		while (recv(c, buf, sizeof buf, 0) > 0) { }
	} else {
		int u = socket(AF_INET, SOCK_DGRAM, 0), big = 8 << 20;
		if (u < 0) { close(c); ping_run = 0; pthread_join(th, NULL); return 1; }
		bind_if(u);
		setsockopt(u, SOL_SOCKET, SO_RCVBUF, &big, sizeof big);
		if (len < 16) len = 16;
		if (len > 1472) len = 1472;
		if (strcmp(test, "udpup") == 0) {
			double per_s = kbit * 1000.0 / 8 / len;
			uint32_t seq = 0, err = 0;
			memset(buf, 0, sizeof buf);
			while ((el = now_us() - t0) < (int64_t)secs * 1000000) {
				while (seq < el / 1e6 * per_s) {
					uint32_t m = htonl(MAGIC), s = htonl(seq);
					uint64_t ts = (uint64_t)now_us();
					memcpy(buf, &m, 4); memcpy(buf + 4, &s, 4); memcpy(buf + 8, &ts, 8);
					if (sendto(u, buf, (size_t)len, 0, (struct sockaddr *)&srv, sizeof srv) < 0) err++;
					seq++;
				}
				usleep(500);
			}
			snprintf(extra, sizeof extra, " sent=%u send_errors=%u", seq, err);
			{ char m[64]; snprintf(m, sizeof m, "SENT %u\n", seq); sleep(4); send(c, m, strlen(m), 0); }
			bytes = (long long)seq * len;
		} else {
			uint32_t got = 0, reord = 0, dup = 0, maxs = 0, lastmax = 0;
			static unsigned char seen[1 << 20];
			memset(seen, 0, sizeof seen);
			udd_acct_t acct = { 0, 0, 0, 0 };
			int64_t next_tick = t0 + 1000000, last_rx = t0, now;
			sendto(u, "HELO", 4, 0, (struct sockaddr *)&srv, sizeof srv);
			for (;;) {
				struct pollfd pf = { u, POLLIN, 0 };
				ssize_t n;
				int64_t wait_us;
				char row[128];
				now = now_us();
				while (now >= next_tick) {                /* one row per second, also while nothing arrives */
					udd_tick(&acct, got, maxs, bytes, row, sizeof row);
					printf("%s\n", row);
					next_tick += 1000000;
				}
				if (now - last_rx >= 3000000) break;      /* 3 s without a datagram ends the test, as before */
				wait_us = next_tick - now;
				if (wait_us > last_rx + 3000000 - now) wait_us = last_rx + 3000000 - now;
				if (poll(&pf, 1, (int)((wait_us + 999) / 1000)) <= 0) continue;
				n = recv(u, buf, sizeof buf, 0);
				last_rx = now_us();
				if (n >= 16) {
					uint32_t m, s;
					memcpy(&m, buf, 4); memcpy(&s, buf + 4, 4); m = ntohl(m); s = ntohl(s);
					if (m != MAGIC || s >= (1u << 23)) continue;
					if (seen[s >> 3] & (1u << (s & 7))) { dup++; continue; }
					seen[s >> 3] |= (unsigned char)(1u << (s & 7));
					got++; bytes += n;
					if (s < lastmax) reord++;
					if (s > lastmax) lastmax = s;
					if (s + 1 > maxs) maxs = s + 1;
				}
			}
			{ char m[96]; snprintf(m, sizeof m, "GOT %u %u %u %u %u\n", got, maxs - got, reord, dup, maxs); send(c, m, strlen(m), 0); }
			snprintf(extra, sizeof extra, " got=%u lost_upto_max=%u reord=%u dup=%u maxseq=%u", got, maxs - got, reord, dup, maxs);
		}
		close(u);
	}
	el = now_us() - t0;
	ping_run = 0; pthread_join(th, NULL);
	rtt_summary(rt, sizeof rt);
	printf("pixperf: RESULT test=%s secs=%.1f bytes=%lld mbit=%.1f%s %s (client view; the server line counts)\n", test, el / 1e6, bytes, bytes * 8.0 / (el > 0 ? el : 1), extra, rt);
	close(c);
	sleep(1);
	return rc;
}

int main(int argc, char **argv)
{
	int secs = 30, kbit = 20000, len = 1200, wait = 180, delay = 0, port = 5299, o, i, bad = 0;
	char tests[64] = "down,up,udpup,udpdown", *t, *sv;
	setvbuf(stdout, NULL, _IONBF, 0);
	while ((o = getopt(argc, argv, "t:u:l:W:D:i:p:T:")) != -1) switch (o) {
		case 't': secs = atoi(optarg); break; case 'u': kbit = atoi(optarg); break; case 'l': len = atoi(optarg); break;
		case 'W': wait = atoi(optarg); break; case 'D': delay = atoi(optarg); break; case 'i': ifn = optarg; break;
		case 'p': port = atoi(optarg); break; case 'T': snprintf(tests, sizeof tests, "%s", optarg); break;
		default: printf("usage: pixperf [-t s] [-u kbit] [-l len] [-W s] [-D s] [-i if] [-p port] [-T tests] HOST\n"); return 2;
	}
	if (optind != argc - 1 || inet_pton(AF_INET, argv[optind], &srv.sin_addr) != 1 || secs < 1 || secs > 600) {
		printf("pixperf: GAP usage: one IPv4 HOST and -t 1..600 needed\n"); return 2;
	}
	srv.sin_family = AF_INET; srv.sin_port = htons((uint16_t)port);
	if (delay > 0) sleep((unsigned)delay);
	for (i = 0; !iface_ready(); i++) {
		if (i >= wait) { printf("pixperf: GAP %s not ready after %d s\n", ifn, wait); return 3; }
		if (i % 5 == 0) printf("pixperf: waiting for %s (%d s)\n", ifn, i);
		sleep(1);
	}
	ifidx = if_nametoindex(ifn);
	printf("pixperf: start tests=%s secs=%d udp_kbit=%d len=%d iface=%s (server address not printed)\n", tests, secs, kbit, len, ifn);
	for (t = strtok_r(tests, ",", &sv); t; t = strtok_r(NULL, ",", &sv)) bad |= run_test(t, secs, kbit, len);
	printf("pixperf: done rc=%d\n", bad);
	return bad;
}
