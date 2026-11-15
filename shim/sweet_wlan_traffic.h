/* sweet_wlan_traffic.h - host-only Ethernet II frame summariser for the Wi-Fi run log (X-W4).
 *
 * PURPOSE: prove DHCP/ARP/ICMP-echo/EAPOL traffic from the run log without a sniffer (feeds W-D5 ping/DHCP
 * evidence). Header-only C99, no malloc. The includer defines, before including:
 *     SWTR_LOG(str)  - one log line (const char *, no newline)
 *     SWTR_GAP(str)  - one "WIFI GAP: ..." line (rule 8)
 * Own code from the standards below only; nothing is an observation on our hardware.
 *
 * Standards used (read from the RFCs, not observed):
 *  Ethernet II: 14 B (dst 6, src 6, ethertype 2); 0x0800 IPv4, 0x0806 ARP, 0x86DD IPv6, 0x888E EAPOL
 *      (IEEE 802.1X-2010 s.11.3: version 1 B, type 1 B [3 = EAPOL-Key], body length 2 B big-endian).
 *  IPv4 (RFC 791 s.3.1): version/IHL byte (IHL in 32-bit words, min 5), total length at +2, flags/frag at +6,
 *      protocol at +9 (1 ICMP, 17 UDP).  UDP (RFC 768): 8 B header, length at +4 includes the header.
 *  DHCP/BOOTP (RFC 2131 s.2): op +0, xid +4, yiaddr +16, magic cookie 0x63825363 at +236, options from +240;
 *      ports 67/68 (s.4.1). Options (RFC 2132): 0 pad, 255 end, else code,len,data; 53 message type 1..8 (s.9.6),
 *      1 subnet (s.3.3), 3 router (s.3.5), 6 DNS (s.3.8), 51 lease (s.9.2), 54 server id (s.9.7).
 *  ARP (RFC 826): htype 2, ptype 2, hlen 1, plen 1, op 2 (1 request, 2 reply), sha, spa, tha, tpa (28 B, Ethernet/IPv4).
 *  ICMP echo (RFC 792): type 8 request / 0 reply, code, checksum, id +4, seq +6.
 *
 * Log line formats (rx/tx = direction as seen by the NIC):
 *  WIFI TRAFFIC: rx dhcp OFFER xid 0xXXXXXXXX yiaddr a.b.c.d server a.b.c.d router a.b.c.d dns a.b.c.d lease N
 *      (fields absent from the options are omitted; the type name is DISCOVER OFFER REQUEST DECLINE ACK NAK RELEASE INFORM)
 *  WIFI TRAFFIC: tx arp req who-has a.b.c.d tell a.b.c.d [mac set]
 *  WIFI TRAFFIC: rx arp rep a.b.c.d is-at [mac set]   (sender IP; MAC addresses are never logged)
 *  WIFI TRAFFIC: rx icmp echo-reply id N seq N   |  tx icmp echo-request id N seq N
 *  WIFI TRAFFIC: rx eapol key len N              (EAPOL: type + length only, NEVER key bytes)
 *  WIFI GAP: traffic malformed <eth|ipv4|udp|dhcp|arp|icmp|eapol> len N
 * Summary (swtr_summary), one line:
 *  WIFI TRAFFIC SUMMARY: rx N tx N dhcp D/O/R/A/N arp req/rep icmp req/rep eapol N ipv6 N other N bad N
 *  (dhcp = discover/offer/request/ack/nak counts; other DHCP types and non-echo ICMP/ARP ops count as other)
 * Every frame is counted in rx or tx; a malformed one also bumps bad (and no kind counter).
 */
#ifndef SWEET_WLAN_TRAFFIC_H
#define SWEET_WLAN_TRAFFIC_H

#include <stdint.h>
#include <stdio.h>

struct swtr {
	uint32_t rx, tx, bad, other, ipv6, eapol;
	uint32_t d, o, r, a, n;		/* dhcp discover/offer/request/ack/nak */
	uint32_t arp_req, arp_rep, icmp_req, icmp_rep;
};

static inline uint32_t swtr_be16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | p[1]; }
static inline uint32_t swtr_be32(const uint8_t *p)
{
	return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

static inline void swtr_bad(struct swtr *t, const char *kind, uint32_t len)
{
	char b[64];	/* "WIFI GAP: traffic malformed " (29) + kind (<=6) + " len " + up to 10 digits < 64 */

	t->bad++;
	snprintf(b, sizeof b, "WIFI GAP: traffic malformed %s len %u", kind, (unsigned)len);
	SWTR_GAP(b);
}

static inline int swtr_ip(char *o, size_t n, const uint8_t *p)
{
	return snprintf(o, n, "%u.%u.%u.%u", p[0], p[1], p[2], p[3]);
}

static inline int swtr_nz(const uint8_t *p, int n)
{
	int i, v = 0;

	for (i = 0; i < n; i++)
		v |= p[i];
	return v != 0;
}

/* d = BOOTP message (RFC 2131 s.2), n = bytes available. */
static inline void swtr_dhcp(struct swtr *t, const char *dir, const uint8_t *d, uint32_t n, uint32_t flen)
{
	static const char *const nm[9] = { "?", "DISCOVER", "OFFER", "REQUEST", "DECLINE", "ACK", "NAK", "RELEASE", "INFORM" };
	const uint8_t *router = 0, *dns = 0, *srv = 0, *subnet = 0;
	uint32_t lease = 0, i = 240, mt = 0;
	int have_lease = 0;
	char line[256], a[16], b[16], c[16], e[16];
	size_t k;

	if (n < 240 || swtr_be32(d + 236) != 0x63825363u) {
		swtr_bad(t, "dhcp", flen);
		return;
	}
	while (i < n) {
		uint32_t code = d[i], ol;

		if (code == 255)
			break;
		if (code == 0) {
			i++;
			continue;
		}
		if (i + 2 > n) {
			swtr_bad(t, "dhcp", flen);
			return;
		}
		ol = d[i + 1];
		if (i + 2 + ol > n) {
			swtr_bad(t, "dhcp", flen);
			return;
		}
		if (code == 53 && ol == 1)
			mt = d[i + 2];
		else if (code == 1 && ol >= 4)
			subnet = d + i + 2;
		else if (code == 3 && ol >= 4)
			router = d + i + 2;
		else if (code == 6 && ol >= 4)
			dns = d + i + 2;
		else if (code == 51 && ol == 4) {
			lease = swtr_be32(d + i + 2);
			have_lease = 1;
		} else if (code == 54 && ol >= 4)
			srv = d + i + 2;
		i += 2 + ol;
	}
	if (mt < 1 || mt > 8) {	/* no/invalid option 53: not a DHCP message we can classify */
		swtr_bad(t, "dhcp", flen);
		return;
	}
	switch (mt) {
	case 1: t->d++; break;
	case 2: t->o++; break;
	case 3: t->r++; break;
	case 5: t->a++; break;
	case 6: t->n++; break;
	default: t->other++; break;
	}
	swtr_ip(a, sizeof a, d + 16);
	k = (size_t)snprintf(line, sizeof line, "WIFI TRAFFIC: %s dhcp %s xid 0x%08x yiaddr %s", dir, nm[mt],
			     (unsigned)swtr_be32(d + 4), a);
	if (srv) {
		swtr_ip(b, sizeof b, srv);
		k += (size_t)snprintf(line + k, sizeof line - k, " server %s", b);
	}
	if (subnet) {
		swtr_ip(e, sizeof e, subnet);
		k += (size_t)snprintf(line + k, sizeof line - k, " mask %s", e);
	}
	if (router) {
		swtr_ip(b, sizeof b, router);
		k += (size_t)snprintf(line + k, sizeof line - k, " router %s", b);
	}
	if (dns) {
		swtr_ip(c, sizeof c, dns);
		k += (size_t)snprintf(line + k, sizeof line - k, " dns %s", c);
	}
	if (have_lease)
		snprintf(line + k, sizeof line - k, " lease %u", (unsigned)lease);
	SWTR_LOG(line);
}

static inline void swtr_note(struct swtr *t, int dir_tx, const uint8_t *f, uint32_t len)
{
	const char *dir = dir_tx ? "tx" : "rx";
	uint32_t et;
	char line[160], a[16], b[16];

	if (dir_tx)
		t->tx++;
	else
		t->rx++;
	if (!f || len < 14) {
		swtr_bad(t, "eth", len);
		return;
	}
	et = swtr_be16(f + 12);
	if (et == 0x86DD) {		/* IPv6: counted only */
		t->ipv6++;
	} else if (et == 0x888E) {	/* EAPOL, IEEE 802.1X-2010 s.11.3 */
		uint32_t bl;

		if (len < 14 + 4) {
			swtr_bad(t, "eapol", len);
			return;
		}
		bl = swtr_be16(f + 14 + 2);
		if (14 + 4 + bl > len) {
			swtr_bad(t, "eapol", len);
			return;
		}
		t->eapol++;
		if (f[15] == 3)
			snprintf(line, sizeof line, "WIFI TRAFFIC: %s eapol key len %u", dir, (unsigned)bl);
		else
			snprintf(line, sizeof line, "WIFI TRAFFIC: %s eapol type %u len %u", dir, f[15], (unsigned)bl);
		SWTR_LOG(line);
	} else if (et == 0x0806) {	/* ARP, RFC 826 */
		const uint8_t *p = f + 14;
		uint32_t op;

		if (len < 14 + 28 || swtr_be16(p) != 1 || swtr_be16(p + 2) != 0x0800 || p[4] != 6 || p[5] != 4) {
			swtr_bad(t, "arp", len);
			return;
		}
		op = swtr_be16(p + 6);
		swtr_ip(a, sizeof a, p + 14);
		swtr_ip(b, sizeof b, p + 24);
		if (op == 1) {
			t->arp_req++;
			snprintf(line, sizeof line, "WIFI TRAFFIC: %s arp req who-has %s tell %s%s", dir, b, a,
				 swtr_nz(p + 8, 6) ? " mac set" : "");
		} else if (op == 2) {
			t->arp_rep++;
			snprintf(line, sizeof line, "WIFI TRAFFIC: %s arp rep %s is-at%s", dir, a,
				 swtr_nz(p + 8, 6) ? " mac set" : "");
		} else {
			t->other++;
			return;
		}
		SWTR_LOG(line);
	} else if (et == 0x0800) {	/* IPv4, RFC 791 s.3.1 */
		const uint8_t *ip = f + 14;
		uint32_t avail = len - 14, ihl, tot, proto;

		if (avail < 20 || (ip[0] >> 4) != 4 || (ip[0] & 0xF) < 5) {
			swtr_bad(t, "ipv4", len);
			return;
		}
		ihl = (uint32_t)(ip[0] & 0xF) * 4;
		tot = swtr_be16(ip + 2);
		if (tot < ihl || tot > avail) {
			swtr_bad(t, "ipv4", len);
			return;
		}
		proto = ip[9];
		if ((swtr_be16(ip + 6) & 0x1FFF) != 0) {	/* non-first fragment: no L4 header */
			t->other++;
			return;
		}
		if (proto == 1) {		/* ICMP, RFC 792 */
			const uint8_t *ic = ip + ihl;
			uint32_t il = tot - ihl;

			if (il < 8) {
				swtr_bad(t, "icmp", len);
				return;
			}
			if (ic[0] == 8 || ic[0] == 0) {
				int req = ic[0] == 8;

				if (req)
					t->icmp_req++;
				else
					t->icmp_rep++;
				snprintf(line, sizeof line, "WIFI TRAFFIC: %s icmp echo-%s id %u seq %u", dir,
					 req ? "request" : "reply", (unsigned)swtr_be16(ic + 4), (unsigned)swtr_be16(ic + 6));
				SWTR_LOG(line);
			} else {
				t->other++;
			}
		} else if (proto == 17) {	/* UDP, RFC 768 */
			const uint8_t *u = ip + ihl;
			uint32_t ul, sp, dp;

			if (tot - ihl < 8) {
				swtr_bad(t, "udp", len);
				return;
			}
			ul = swtr_be16(u + 4);
			if (ul < 8 || ul > tot - ihl) {
				swtr_bad(t, "udp", len);
				return;
			}
			sp = swtr_be16(u);
			dp = swtr_be16(u + 2);
			if ((sp == 67 || sp == 68) && (dp == 67 || dp == 68))
				swtr_dhcp(t, dir, u + 8, ul - 8, len);
			else
				t->other++;
		} else {
			t->other++;
		}
	} else {
		t->other++;
	}
}

static inline void swtr_summary(const struct swtr *t)
{
	char l[200];

	snprintf(l, sizeof l,
		 "WIFI TRAFFIC SUMMARY: rx %u tx %u dhcp %u/%u/%u/%u/%u arp %u/%u icmp %u/%u eapol %u ipv6 %u other %u bad %u",
		 (unsigned)t->rx, (unsigned)t->tx, (unsigned)t->d, (unsigned)t->o, (unsigned)t->r, (unsigned)t->a,
		 (unsigned)t->n, (unsigned)t->arp_req, (unsigned)t->arp_rep, (unsigned)t->icmp_req,
		 (unsigned)t->icmp_rep, (unsigned)t->eapol, (unsigned)t->ipv6, (unsigned)t->other, (unsigned)t->bad);
	SWTR_LOG(l);
}

#endif
