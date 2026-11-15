/*
 * pixping: minimal ICMP echo client for PixOS (sprint-wifi, rung W-IOSPING)
 * ==========================================================================
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 PixOS-Project contributors.
 *
 * Usage: pixping [-c N] [-s interval_ms] [-W wait_for_iface_s] [-D delay_s] [-i iface] A.B.C.D
 *
 * Stock iOS ships no ping binary [guess: no root listing was available to check], so the
 * Wi-Fi rung "ping the gateway from iOS userland over en0" needs our own.
 *
 * 1. Waits (up to -W seconds, default 120) until interface -i (default en0) exists, is UP
 *    and has an IPv4 address (getifaddrs); one line per 5 s while waiting.
 * 2. Sends N ICMP echo requests (default 4), 1 s apart, prints one line per reply and a
 *    final summary. Every failure prints a "pixping: GAP ..." line (no silent failure).
 *
 * Socket: unprivileged socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP).
 *   Darwin [read from source, XNU bsd/netinet/in_proto.c:182-192]: protosw entry SOCK_DGRAM/
 *   IPPROTO_ICMP uses icmp_dgram_usrreqs; ip_icmp.c:1126 icmp_dgram_attach is "like
 *   rip_attach but without root privilege enforcement"; icmp_dgram_send (ip_icmp.c:1200)
 *   only lets ICMP_ECHO/TSTAMP/MASKREQ with code 0 through. Replies arrive WITH the IPv4
 *   header (raw_ip.c STRIPHDR is off by default); we strip it ourselves.
 *   Linux host test build: same socket type (ping socket, net.ipv4.ping_group_range); the
 *   kernel strips the IP header and rewrites the id, so the id check is skipped there.
 * On Darwin the socket is bound to the interface with IP_BOUND_IF (allowed by
 * icmp_dgram_ctloutput, ip_icmp.c:1156-1190) so the echo really leaves over en0.
 *
 * Exit: 0 only if got == sent; 1 lost replies; 2 usage; 3 interface never ready; 4 socket error.
 * stdout is unbuffered so every line reaches the console tee at once.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netinet/ip_icmp.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define PAYLOAD 56
#define MAX_COUNT 1000
#define ICMP_HDR 8
#define MAGIC 0x50495850u /* "PXIP" */

static int64_t now_us(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static void usage(void)
{
	printf("pixping: GAP usage: pixping [-c N] [-s interval_ms] [-W wait_for_iface_s] [-D delay_s] [-i iface] A.B.C.D\n");
}

static uint16_t cksum(const void *buf, size_t len)
{
	const uint8_t *p = buf;
	uint32_t sum = 0;
	for (; len > 1; len -= 2, p += 2) {
		sum += (uint32_t)(p[0] << 8 | p[1]);
	}
	if (len) {
		sum += (uint32_t)(p[0] << 8);
	}
	while (sum >> 16) {
		sum = (sum & 0xffff) + (sum >> 16);
	}
	return (uint16_t)~sum;
}

/* 1 = UP + IPv4 address (addr filled), 0 = not yet; *why says what is missing. */
static int iface_ready(const char *name, char *why, size_t whylen, char *addr, size_t addrlen)
{
	struct ifaddrs *ifa, *p;
	int exists = 0, up = 0, have4 = 0;
	if (getifaddrs(&ifa) != 0) {
		snprintf(why, whylen, "getifaddrs failed: %s", strerror(errno));
		return 0;
	}
	for (p = ifa; p; p = p->ifa_next) {
		if (!p->ifa_name || strcmp(p->ifa_name, name) != 0) {
			continue;
		}
		exists = 1;
		if (p->ifa_flags & IFF_UP) {
			up = 1;
		}
		if (p->ifa_addr && p->ifa_addr->sa_family == AF_INET) {
			const struct sockaddr_in *sin = (const struct sockaddr_in *)(const void *)p->ifa_addr;
			if (sin->sin_addr.s_addr != 0) {
				have4 = 1;
				inet_ntop(AF_INET, &sin->sin_addr, addr, (socklen_t)addrlen);
			}
		}
	}
	freeifaddrs(ifa);
	snprintf(why, whylen, "%s", !exists ? "interface missing" : !up ? "interface down" : !have4 ? "no IPv4 address" : "ready");
	return exists && up && have4;
}

/* Returns the ICMP header offset inside a received datagram, or -1 if it is not IPv4/ICMP. */
static int icmp_offset(const uint8_t *b, ssize_t n, int *had_ip)
{
	*had_ip = 0;
	if (n >= 20 && (b[0] >> 4) == 4) {
		int hl = (b[0] & 0xf) * 4;
		if (hl < 20 || n < hl + ICMP_HDR || b[9] != IPPROTO_ICMP) {
			return -1;
		}
		*had_ip = 1;
		return hl;
	}
	return n >= ICMP_HDR ? 0 : -1; /* kernel already stripped the IP header (Linux ping socket) */
}

int main(int argc, char **argv)
{
	int count = 4, waitiface = 120, interval_ms = 1000, delay_s = 0, opt;
	const char *ifname = "en0";
	setvbuf(stdout, NULL, _IONBF, 0);
	while ((opt = getopt(argc, argv, "c:W:i:s:D:")) != -1) {
		char *end;
		long v;
		switch (opt) {
		case 'c':
		case 'W':
		case 's':           /* sprint 2026-11-08: -s ms between requests (default 1000; run wlan-iosping-b: configd removed the static address ~5 s after the start) */
		case 'D':           /* W-UI 2026-11-08: -D seconds to sleep before anything else (a second job pings while Settings > Wi-Fi is open) */
			v = strtol(optarg, &end, 10);
			if (*optarg == '\0' || *end != '\0' || v < (opt == 'c' ? 1 : opt == 's' ? 50 : 0) || v > (opt == 'c' ? MAX_COUNT : opt == 's' ? 10000 : 86400)) {
				printf("pixping: GAP bad value for -%c: '%s'\n", opt, optarg);
				usage();
				return 2;
			}
			if (opt == 'c') {
				count = (int)v;
			} else if (opt == 's') {
				interval_ms = (int)v;
			} else if (opt == 'D') {
				delay_s = (int)v;
			} else {
				waitiface = (int)v;
			}
			break;
		case 'i':
			ifname = optarg;
			break;
		default:
			usage();
			return 2;
		}
	}
	if (optind + 1 != argc) {
		printf("pixping: GAP expected exactly one IPv4 address argument\n");
		usage();
		return 2;
	}
	struct sockaddr_in dst;
	memset(&dst, 0, sizeof dst);
	dst.sin_family = AF_INET;
	if (inet_pton(AF_INET, argv[optind], &dst.sin_addr) != 1) {
		printf("pixping: GAP '%s' is not a dotted IPv4 address\n", argv[optind]);
		usage();
		return 2;
	}
	if (strlen(ifname) >= IFNAMSIZ) {
		printf("pixping: GAP interface name '%s' too long\n", ifname);
		return 2;
	}

	if (delay_s > 0) {
		printf("pixping: delay %d s before the start\n", delay_s);
		sleep((unsigned)delay_s);
	}
	/* phase 1: wait for the interface */
	char why[64] = "", laddr[INET_ADDRSTRLEN] = "";
	int64_t t0 = now_us(), lastprint = -5000000;
	printf("pixping: start iface=%s count=%d wait=%ds dst=%s\n", ifname, count, waitiface, argv[optind]);
	for (;;) {
		if (iface_ready(ifname, why, sizeof why, laddr, sizeof laddr)) {
			printf("pixping: iface %s ready addr=%s after %lld ms\n", ifname, laddr, (long long)((now_us() - t0) / 1000));
			break;
		}
		int64_t el = now_us() - t0;
		if (el >= (int64_t)waitiface * 1000000) {
			printf("pixping: GAP interface %s not ready after %ds (%s)\n", ifname, waitiface, why);
			printf("pixping: summary sent=0 got=0 lost=0\n");
			return 3;
		}
		if (el - lastprint >= 5000000) {
			printf("pixping: waiting for %s: %s (%lld/%d s)\n", ifname, why, (long long)(el / 1000000), waitiface);
			lastprint = el;
		}
		usleep(500000);
	}

	/* phase 2: ping */
	int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP);
	if (fd < 0) {
		printf("pixping: GAP socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP) failed: %s\n", strerror(errno));
		printf("pixping: summary sent=0 got=0 lost=%d\n", count);
		return 4;
	}
#ifdef IP_BOUND_IF
	{
		int idx = (int)if_nametoindex(ifname);
		if (idx == 0) {
			printf("pixping: GAP if_nametoindex(%s) failed: %s (socket not bound to the interface)\n", ifname, strerror(errno));
		} else if (setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &idx, sizeof idx) != 0) {
			printf("pixping: GAP setsockopt(IP_BOUND_IF %s=%d) failed: %s (route decides the interface)\n", ifname, idx, strerror(errno));
		}
	}
#else
	printf("pixping: note IP_BOUND_IF does not exist on this OS (host test build): socket not bound to %s\n", ifname);
#endif
	uint16_t ident = (uint16_t)(getpid() & 0xffff);
	int sent = 0, got = 0, other = 0;
	char *seen = calloc((size_t)count, 1);
	int64_t *tsend = calloc((size_t)count, sizeof *tsend);
	if (!seen || !tsend) {
		printf("pixping: GAP out of memory\n");
		return 4;
	}
	for (int seq = 0; seq < count; seq++) {
		uint8_t pkt[ICMP_HDR + PAYLOAD];
		memset(pkt, 0, sizeof pkt);
		pkt[0] = ICMP_ECHO;
		pkt[4] = (uint8_t)(ident >> 8);
		pkt[5] = (uint8_t)ident;
		pkt[6] = (uint8_t)((unsigned)seq >> 8);
		pkt[7] = (uint8_t)seq;
		uint32_t magic = MAGIC;
		memcpy(pkt + ICMP_HDR, &magic, sizeof magic);
		for (int i = 4; i < PAYLOAD; i++) {
			pkt[ICMP_HDR + i] = (uint8_t)i;
		}
		uint16_t ck = cksum(pkt, sizeof pkt);
		pkt[2] = (uint8_t)(ck >> 8);
		pkt[3] = (uint8_t)ck;
		tsend[seq] = now_us();
		if (sendto(fd, pkt, sizeof pkt, 0, (struct sockaddr *)&dst, sizeof dst) != (ssize_t)sizeof pkt) {
			printf("pixping: GAP sendto seq=%d failed: %s\n", seq, strerror(errno));
		} else {
			sent++;
		}
		/* listen until 1 s after this send (and 1 s after the last one) */
		int64_t until = tsend[seq] + (seq == count - 1 ? 1000000 : (int64_t)interval_ms * 1000);     /* the last request: 1 s for its reply */
		for (;;) {
			int64_t left = until - now_us();
			if (left <= 0) {
				break;
			}
			struct pollfd pfd = { .fd = fd, .events = POLLIN };
			int pr = poll(&pfd, 1, (int)((left + 999) / 1000));
			if (pr < 0) {
				if (errno != EINTR) {
					printf("pixping: GAP poll failed: %s\n", strerror(errno));
					break;
				}
				continue;
			}
			if (pr == 0) {
				continue;
			}
			uint8_t rb[1500];
			struct sockaddr_in from;
			socklen_t fl = sizeof from;
			ssize_t n = recvfrom(fd, rb, sizeof rb, 0, (struct sockaddr *)&from, &fl);
			int64_t tr = now_us();
			if (n < 0) {
				if (errno != EINTR) {
					printf("pixping: GAP recvfrom failed: %s\n", strerror(errno));
					usleep(10000);
				}
				continue;
			}
			int had_ip, off = icmp_offset(rb, n, &had_ip);
			if (off < 0 || rb[off] != ICMP_ECHOREPLY) {
				other++;
				continue;
			}
			uint16_t rid = (uint16_t)(rb[off + 4] << 8 | rb[off + 5]);
			int rseq = rb[off + 6] << 8 | rb[off + 7];
			if (from.sin_addr.s_addr != dst.sin_addr.s_addr || (had_ip && rid != ident)) {
				other++;
				continue;
			}
			if (rseq >= count || !tsend[rseq]) {
				printf("pixping: GAP reply with unknown seq=%d ignored\n", rseq);
				continue;
			}
			if (seen[rseq]) {
				printf("pixping: GAP duplicate reply seq=%d ignored\n", rseq);
				continue;
			}
			seen[rseq] = 1;
			got++;
			printf("pixping: reply seq=%d bytes=%zd rtt_us=%lld\n", rseq, n - off, (long long)(tr - tsend[rseq]));
		}
		if (!seen[seq]) {
			printf("pixping: timeout seq=%d\n", seq);
		}
	}
	if (other > 0) {
		printf("pixping: note ignored %d unrelated ICMP packet(s)\n", other);
	}
	printf("pixping: summary sent=%d got=%d lost=%d\n", sent, got, sent - got);
	if (sent != count) {
		printf("pixping: GAP only %d of %d requests could be sent\n", sent, count);
	}
	close(fd);
	free(seen);
	free(tsend);
	return (got == sent && sent == count) ? 0 : 1;
}
