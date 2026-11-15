/* wlan_traffic_host_test.c - host test for sweet_wlan_traffic.h (X-W4). Hand-built frames per the RFCs. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char g_last[256];
static int g_n, g_gaps;
#define SWTR_LOG(s) do { g_n++; snprintf(g_last, sizeof g_last, "%s", (s)); } while (0)
#define SWTR_GAP(s) do { g_gaps++; if (strlen(s) > 63) g_gaps += 1000; SWTR_LOG(s); } while (0)
#include "sweet_wlan_traffic.h"

/* every frame goes to swtr_note from an exact-size heap copy, so ASan flags any read past len (driver 21x) */
static void
note_x(struct swtr *t, int tx, const uint8_t *f, uint32_t len)
{
	uint8_t *g;

	if (f == NULL) {         /* the NULL-frame case goes through as is */
		swtr_note(t, tx, NULL, len);
		return;
	}
	g = malloc(len ? len : 1u);
	if (g == NULL) {
		printf("FAIL malloc %u\n", len);
		exit(1);
	}
	if (len) {
		memcpy(g, f, len);
	}
	swtr_note(t, tx, g, len);
	free(g);
}

static int fails;
#define CHECK(c) do { if (!(c)) { printf("CHECK FAILED line %d: %s\n", __LINE__, #c); fails++; } } while (0)

static const char *last(void) { return g_last; }

static void eth(uint8_t *f, uint16_t et)
{
	memset(f, 0xAA, 12);
	f[12] = et >> 8;
	f[13] = et & 0xFF;
}

/* IPv4+UDP+BOOTP frame; opts/olen appended after the cookie. Returns frame length. */
static uint32_t dhcp(uint8_t *f, int reply, uint32_t xid, const uint8_t *yi, const uint8_t *opts, uint32_t olen)
{
	uint8_t *ip = f + 14, *u = ip + 20, *b = u + 8;
	uint32_t blen = 240 + olen, ul = 8 + blen, tl = 20 + ul;

	memset(f, 0, 14 + tl);
	eth(f, 0x0800);
	ip[0] = 0x45; ip[2] = tl >> 8; ip[3] = tl & 0xFF; ip[9] = 17;
	u[0] = 0; u[1] = reply ? 67 : 68; u[2] = 0; u[3] = reply ? 68 : 67; u[4] = ul >> 8; u[5] = ul & 0xFF;
	b[0] = reply ? 2 : 1;
	b[4] = xid >> 24; b[5] = xid >> 16; b[6] = xid >> 8; b[7] = xid;
	if (yi) memcpy(b + 16, yi, 4);
	b[236] = 0x63; b[237] = 0x82; b[238] = 0x53; b[239] = 0x63;
	memcpy(b + 240, opts, olen);
	return 14 + tl;
}

int main(void)
{
	struct swtr t;
	uint8_t f[600], o[128];
	static const uint8_t yi[4] = { 198, 51, 100, 50 };
	uint32_t n, i, k;
	uint32_t bad0;

	memset(&t, 0, sizeof t);

	/* DISCOVER: opt 53=1, end */
	k = 0; o[k++] = 53; o[k++] = 1; o[k++] = 1; o[k++] = 255;
	n = dhcp(f, 0, 0xDEADBEEF, 0, o, k);
	note_x(&t, 1, f, n);
	CHECK(strcmp(last(), "WIFI TRAFFIC: tx dhcp DISCOVER xid 0xdeadbeef yiaddr 0.0.0.0") == 0);

	/* OFFER with subnet, router, dns, lease, server id, a pad before end */
	k = 0;
	o[k++] = 53; o[k++] = 1; o[k++] = 2;
	o[k++] = 54; o[k++] = 4; o[k++] = 198; o[k++] = 51; o[k++] = 100; o[k++] = 1;
	o[k++] = 1; o[k++] = 4; o[k++] = 255; o[k++] = 255; o[k++] = 255; o[k++] = 0;
	o[k++] = 3; o[k++] = 4; o[k++] = 198; o[k++] = 51; o[k++] = 100; o[k++] = 1;
	o[k++] = 6; o[k++] = 8; o[k++] = 8; o[k++] = 8; o[k++] = 8; o[k++] = 8; o[k++] = 1; o[k++] = 1; o[k++] = 1; o[k++] = 1;
	o[k++] = 51; o[k++] = 4; o[k++] = 0; o[k++] = 0; o[k++] = 0x0E; o[k++] = 0x10;	/* 3600 */
	o[k++] = 0; o[k++] = 255;
	n = dhcp(f, 1, 0xDEADBEEF, yi, o, k);
	note_x(&t, 0, f, n);
	CHECK(strcmp(last(), "WIFI TRAFFIC: rx dhcp OFFER xid 0xdeadbeef yiaddr 198.51.100.50 server 198.51.100.1 "
			      "mask 255.255.255.0 router 198.51.100.1 dns 8.8.8.8 lease 3600") == 0);
	{ /* the OFFER truncation sweep: every cut 0..n-1 must not crash and must count bad or other */
		uint32_t cut, base = t.bad + t.other + t.d + t.o + t.r + t.a + t.n;

		for (cut = 0; cut < n; cut++) {
			struct swtr u = t;
			uint32_t before = u.bad + u.other + u.d + u.o + u.r + u.a + u.n + u.ipv6 + u.eapol +
					  u.arp_req + u.arp_rep + u.icmp_req + u.icmp_rep;
			int saw_bad;

			uint8_t *g = malloc(cut ? cut : 1u);      /* exact-size heap copy: ASan then flags any read past `cut` (driver 21x) */

			CHECK(g != NULL);
			if (g == NULL) {
				break;
			}
			memcpy(g, f, cut);
			note_x(&u, 0, g, cut);
			free(g);
			saw_bad = (u.bad > t.bad);
			/* a cut that keeps a complete (IP-length-consistent) packet cannot exist: tot > avail => bad */
			CHECK(saw_bad || u.other > t.other);
			CHECK(u.bad + u.other + u.d + u.o + u.r + u.a + u.n + u.ipv6 + u.eapol + u.arp_req + u.arp_rep +
			      u.icmp_req + u.icmp_rep == before + 1);
		}
		(void)base;
	}
	/* option running past the end of an otherwise length-consistent packet */
	k = 0; o[k++] = 53; o[k++] = 1; o[k++] = 2; o[k++] = 3; o[k++] = 40; o[k++] = 1;
	n = dhcp(f, 1, 1, yi, o, k);
	bad0 = t.bad;
	note_x(&t, 0, f, n);
	CHECK(t.bad == bad0 + 1);
	CHECK(strncmp(last(), "WIFI GAP: traffic malformed dhcp len ", 37) == 0);
	/* no cookie */
	k = 0; o[k++] = 53; o[k++] = 1; o[k++] = 2; o[k++] = 255;
	n = dhcp(f, 1, 1, yi, o, k);
	f[14 + 20 + 8 + 236] = 0;
	bad0 = t.bad;
	note_x(&t, 0, f, n);
	CHECK(t.bad == bad0 + 1);
	/* no option 53 */
	k = 0; o[k++] = 255;
	n = dhcp(f, 1, 1, yi, o, k);
	bad0 = t.bad;
	note_x(&t, 0, f, n);
	CHECK(t.bad == bad0 + 1);
	/* UDP length larger than frame */
	k = 0; o[k++] = 53; o[k++] = 1; o[k++] = 1; o[k++] = 255;
	n = dhcp(f, 0, 1, 0, o, k);
	f[14 + 20 + 4] = 0x0F;
	bad0 = t.bad;
	note_x(&t, 1, f, n);
	CHECK(t.bad == bad0 + 1);
	CHECK(strncmp(last(), "WIFI GAP: traffic malformed udp", 31) == 0);
	/* IHL < 5 */
	n = dhcp(f, 0, 1, 0, o, k);
	f[14] = 0x44;
	bad0 = t.bad;
	note_x(&t, 1, f, n);
	CHECK(t.bad == bad0 + 1);
	CHECK(strncmp(last(), "WIFI GAP: traffic malformed ipv4", 32) == 0);
	/* IHL 6 (options in IP header) still parses a DISCOVER: shift UDP by 4 */
	{
		uint8_t g[600];

		memset(g, 0, sizeof g);
		n = dhcp(f, 0, 7, 0, o, k);
		memcpy(g, f, 14 + 20);
		memcpy(g + 14 + 24, f + 14 + 20, n - 14 - 20);
		g[14] = 0x46; g[17] += 4;
		note_x(&t, 1, g, n + 4);
		CHECK(strcmp(last(), "WIFI TRAFFIC: tx dhcp DISCOVER xid 0x00000007 yiaddr 0.0.0.0") == 0);
	}
	/* REQUEST, ACK, NAK, RELEASE(other) */
	k = 0; o[k++] = 53; o[k++] = 1; o[k++] = 3; o[k++] = 255;
	n = dhcp(f, 0, 9, 0, o, k); note_x(&t, 1, f, n);
	CHECK(strncmp(last(), "WIFI TRAFFIC: tx dhcp REQUEST xid 0x00000009", 44) == 0);
	o[2] = 5; n = dhcp(f, 1, 9, yi, o, k); note_x(&t, 0, f, n);
	CHECK(strncmp(last(), "WIFI TRAFFIC: rx dhcp ACK xid 0x00000009 yiaddr 198.51.100.50", 61) == 0);
	o[2] = 6; n = dhcp(f, 1, 9, 0, o, k); note_x(&t, 0, f, n);
	CHECK(strncmp(last(), "WIFI TRAFFIC: rx dhcp NAK", 25) == 0);
	o[2] = 7; n = dhcp(f, 0, 9, 0, o, k); note_x(&t, 1, f, n);
	CHECK(strncmp(last(), "WIFI TRAFFIC: tx dhcp RELEASE", 29) == 0);

	/* ARP request / reply */
	memset(f, 0, sizeof f);
	eth(f, 0x0806);
	f[15] = 1; f[16] = 0x08; f[18] = 6; f[19] = 4; f[21] = 1;
	memset(f + 22, 0x11, 6);
	f[28] = 198; f[29] = 51; f[30] = 100; f[31] = 50;
	f[38] = 198; f[39] = 51; f[40] = 100; f[41] = 1;
	note_x(&t, 1, f, 42);
	CHECK(strcmp(last(), "WIFI TRAFFIC: tx arp req who-has 198.51.100.1 tell 198.51.100.50 mac set") == 0);
	f[21] = 2;
	f[28] = 198; f[29] = 51; f[30] = 100; f[31] = 1;
	note_x(&t, 0, f, 60);	/* padded to the 60 B minimum */
	CHECK(strcmp(last(), "WIFI TRAFFIC: rx arp rep 198.51.100.1 is-at mac set") == 0);
	bad0 = t.bad;
	note_x(&t, 0, f, 41);
	CHECK(t.bad == bad0 + 1);
	f[19] = 5;	/* plen != 4 */
	note_x(&t, 0, f, 42);
	CHECK(t.bad == bad0 + 2);

	/* ICMP echo request / reply */
	memset(f, 0, sizeof f);
	eth(f, 0x0800);
	f[14] = 0x45; f[17] = 28; f[23] = 1;
	f[34] = 8; f[38] = 0x12; f[39] = 0x34; f[41] = 7;
	note_x(&t, 1, f, 42);
	CHECK(strcmp(last(), "WIFI TRAFFIC: tx icmp echo-request id 4660 seq 7") == 0);
	f[34] = 0;
	note_x(&t, 0, f, 42);
	CHECK(strcmp(last(), "WIFI TRAFFIC: rx icmp echo-reply id 4660 seq 7") == 0);
	f[34] = 3;	/* dest unreachable: other, no line */
	{ int n0 = g_n; uint32_t o0 = t.other; note_x(&t, 0, f, 42); CHECK(g_n == n0 && t.other == o0 + 1); }
	bad0 = t.bad;
	f[17] = 24;	/* ICMP part only 4 B */
	note_x(&t, 0, f, 42);
	CHECK(t.bad == bad0 + 1);
	CHECK(strncmp(last(), "WIFI GAP: traffic malformed icmp", 32) == 0);
	f[17] = 28;
	note_x(&t, 0, f, 41);	/* IP total length beyond frame */
	CHECK(t.bad == bad0 + 2);

	/* EAPOL-Key: 4-way msg 1 shape (body bytes must never reach the log) */
	memset(f, 0, sizeof f);
	eth(f, 0x888E);
	f[14] = 2; f[15] = 3; f[16] = 0; f[17] = 95;
	memset(f + 18, 0x5A, 95);
	note_x(&t, 0, f, 14 + 4 + 95);
	CHECK(strcmp(last(), "WIFI TRAFFIC: rx eapol key len 95") == 0);
	CHECK(strstr(last(), "5a") == 0 && strstr(last(), "90") == 0);
	f[15] = 1; f[17] = 0;
	note_x(&t, 1, f, 18);
	CHECK(strcmp(last(), "WIFI TRAFFIC: tx eapol type 1 len 0") == 0);
	bad0 = t.bad;
	f[17] = 95;
	note_x(&t, 0, f, 50);	/* body longer than frame */
	CHECK(t.bad == bad0 + 1);
	note_x(&t, 0, f, 16);
	CHECK(t.bad == bad0 + 2);

	/* IPv6, other ethertype, short ethernet, NULL */
	eth(f, 0x86DD);
	{ int n0 = g_n; note_x(&t, 0, f, 60); CHECK(g_n == n0 && t.ipv6 == 1); }
	eth(f, 0x1234);
	{ uint32_t o0 = t.other; note_x(&t, 0, f, 60); CHECK(t.other == o0 + 1); }
	bad0 = t.bad;
	note_x(&t, 0, f, 13);
	note_x(&t, 0, 0, 100);
	note_x(&t, 1, f, 0);
	CHECK(t.bad == bad0 + 3);
	CHECK(strcmp(last(), "WIFI GAP: traffic malformed eth len 0") == 0);
	/* UDP to a non-DHCP port: other */
	k = 0; o[k++] = 255;
	n = dhcp(f, 0, 1, 0, o, k);
	f[14 + 20 + 3] = 99;
	{ uint32_t o0 = t.other; note_x(&t, 0, f, n); CHECK(t.other == o0 + 1); }

	CHECK(g_gaps < 1000);	/* every gap line stayed <= 63 chars */
	CHECK(t.d == 2 && t.o == 1 && t.r == 1 && t.a == 1 && t.n == 1);
	CHECK(t.arp_req == 1 && t.arp_rep == 1 && t.icmp_req == 1 && t.icmp_rep == 1 && t.eapol == 2 && t.ipv6 == 1);

	/* every frame is counted exactly once in a kind or bad */
	swtr_summary(&t);
	CHECK(strncmp(last(), "WIFI TRAFFIC SUMMARY: rx ", 25) == 0);
	CHECK(strstr(last(), " dhcp 2/1/1/1/1 arp 1/1 icmp 1/1 eapol 2 ipv6 1 ") != 0);
	CHECK(t.rx + t.tx == t.bad + t.other + t.ipv6 + t.eapol + t.d + t.o + t.r + t.a + t.n + t.arp_req +
			     t.arp_rep + t.icmp_req + t.icmp_rep + (uint32_t)0);
	(void)i;
	printf("%s\n", last());
	printf("RESULT: %s wlan_traffic_host_test\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
