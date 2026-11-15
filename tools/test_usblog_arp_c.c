/*
 * Host test for patch 0184 (usb log ARP wait): tools/test_usblog_arp_c.py extracts the helpers of patch 0179 (gap.inc:
 * ud_log_send, ud_log_drain) and of 0184 (arp.inc: ud_log_arp_ready, ud_log_note, ud_log_round) and builds them against a
 * fake sweet_log_copy (data byte at absolute offset o is 'a' + o % 26), a scripted sock_send and a stub arp_lookup_ip.
 * Prints "RESULT: PASS n checks" / "RESULT: FAIL ..."; exit 0/1.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <netinet/in.h>

typedef int socket_t;
typedef int errno_t;
typedef void *ifnet_t;
#define EJUSTRETURN (-2)
struct sockaddr_dl { int x; };
#define RING 4096u

static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static struct { ifnet_t ifp; } ud;
static uint64_t total;
static int arp_ret, arp_calls, arp_seen_null_args = 1;
static int send_fail;                   /* when set, every sock_send fails */
static int fail_nth, attempts;          /* when set, only the fail_nth send attempt fails (review r1 F3) */
static int ndg;
static char text[64][200];              /* "\nSWEET " note texts, by order */
static int ntext;
static uint64_t data_bytes, exp_off;
static int content_ok = 1;
static int order[64], norder;           /* 1 = usblog line, 2 = gap line, 3 = data */

static errno_t
arp_lookup_ip(ifnet_t i, const struct sockaddr_in *d, struct sockaddr_dl *l, size_t ll, void *hint, void *pkt)
{
	arp_calls++;
	if (i == NULL || d == NULL || l == NULL || ll != sizeof(*l) || hint != NULL || pkt != NULL ||
	    d->sin_addr.s_addr != htonl(0xc0a80701)) {
		arp_seen_null_args = 0;
	}
	return arp_ret;
}

static uint32_t
sweet_log_copy(uint64_t *pos, char *out, uint32_t max)
{
	if (total - *pos > RING) {
		*pos = total - RING;
	}
	uint32_t n = (uint32_t)(total - *pos < max ? total - *pos : max);
	for (uint32_t k = 0; k < n; k++) {
		out[k] = (char)('a' + (*pos + k) % 26);
	}
	*pos += n;
	return n;
}

static int
sock_send(socket_t so, struct msghdr *m, int flags, size_t *sent)
{
	(void)so; (void)flags;
	attempts++;
	if (send_fail || (fail_nth && attempts == fail_nth)) {
		return 55;
	}
	size_t len = m->msg_iov[0].iov_len;
	const char *b = m->msg_iov[0].iov_base;
	*sent = len;
	ndg++;
	if (len >= 8 && memcmp(b, "\nSWEET ", 7) == 0 && len < 200) {
		memcpy(text[ntext], b, len);
		text[ntext++][len] = 0;
		order[norder++] = memcmp(b, "\nSWEET USBLOG", 13) == 0 ? 1 : 2;
		if (strstr(text[ntext - 1], "at least ")) {
			order[norder - 1] = 4;          /* ring-overrun GAP: not expected here */
		}
	} else {
		for (size_t k = 0; k < len; k++) {
			if (b[k] != (char)('a' + (exp_off + k) % 26)) {
				content_ok = 0;
				break;
			}
		}
		exp_off += len;
		data_bytes += len;
		order[norder++] = 3;
	}
	return 0;
}

static uint64_t
sweet_log_position(void)
{
	return total;
}

#include "gap.inc"
#include "arp.inc"

static struct ud_log_gap g;
static struct ud_log_arp a;
static uint64_t pos;
static char chunk[1400];
static struct sockaddr_in to;

static void
reset(uint64_t start)
{
	memset(&g, 0, sizeof(g));
	memset(&a, 0, sizeof(a));
	memset(text, 0, sizeof(text));
	to.sin_addr.s_addr = htonl(0xc0a80701);
	ud.ifp = &ud;
	pos = a.start = start;
	exp_off = start;
	ndg = ntext = norder = 0;
	data_bytes = 0;
	content_ok = 1;
	arp_ret = 0; arp_calls = 0; send_fail = 0;
}

static void
round_(void)
{
	ud_log_round(1, &to, &pos, &g, &a, chunk, sizeof(chunk));
}

int
main(void)
{
	/* a: ARP unresolved (EJUSTRETURN) for 3 rounds: nothing is sent, pos stays, arp is asked every round */
	reset(0); total = 3000; arp_ret = EJUSTRETURN;
	round_(); round_(); round_();
	CHECK(ndg == 0 && pos == 0 && arp_calls == 3 && a.rounds == 3 && a.last_err == EJUSTRETURN && arp_seen_null_args);

	/* b: resolves on round 4: one USBLOG line (300 ms, error -2), then the data from the original pos; no GAP (start 0) */
	arp_ret = 0; round_();
	CHECK(ntext == 1 && order[0] == 1 && strstr(text[0], "resolved after ~300 ms (last arp_lookup_ip error -2); replay starts now") != NULL);
	CHECK(data_bytes == 3000 && content_ok && pos == 3000 && ndg == 4 && norder == 4 && order[1] == 3);
	round_();                               /* later rounds: no second line */
	CHECK(ntext == 1 && ndg == 4);

	/* c: start moved (70000 older bytes... start 5000): GAP line exactly once, right after the USBLOG line, then data */
	reset(5000); total = 5000 + 2000; arp_ret = EJUSTRETURN; round_(); round_();
	CHECK(ndg == 0);
	arp_ret = 0; round_(); round_();
	CHECK(ntext == 2 && order[0] == 1 && order[1] == 2 && order[2] == 3);
	CHECK(strstr(text[1], "usb log replay starts at byte 5000; 5000 older boot bytes not sent (only the last 64 KiB is replayed)") != NULL);
	CHECK(data_bytes == 2000 && content_ok && pos == 7000);

	/* d: start 0 -> no GAP line at all */
	reset(0); total = 100; round_();
	CHECK(ntext == 1 && order[0] == 1 && data_bytes == 100);

	/* e: no ifp -> waits, never calls arp, records ENXIO */
	reset(0); total = 100; ud.ifp = NULL; round_();
	CHECK(ndg == 0 && arp_calls == 0 && a.last_err == ENXIO && a.rounds == 1);
	ud.ifp = &ud; round_();
	CHECK(ntext == 1 && strstr(text[0], "after ~100 ms") != NULL && data_bytes == 100);

	/* f: any error other than EJUSTRETURN also keeps waiting */
	reset(0); total = 100; arp_ret = 65; round_();
	CHECK(ndg == 0 && a.last_err == 65 && pos == 0);

	/* g: entry expires later: waits again, reports again after the wait, pos continues */
	reset(0); total = 100; round_();
	total = 200; arp_ret = EJUSTRETURN; round_(); round_();
	CHECK(data_bytes == 100 && pos == 100);
	arp_ret = 0; round_();
	CHECK(ntext == 2 && strstr(text[1], "after ~200 ms") != NULL && data_bytes == 200 && content_ok && pos == 200);

	/* h: the USBLOG send fails -> retried next round, GAP line not before it, no data lost */
	reset(5000); total = 5100; send_fail = 1; round_();
	CHECK(ndg == 0 && pos == 5000);
	send_fail = 0; round_();
	CHECK(order[0] == 1 && order[1] == 2 && order[2] == 3 && data_bytes == 100 && ntext == 2);

	/* i: idle (nothing pending): no arp_lookup_ip call at all, so an idle link sends no probes (review r1 F1) */
	reset(0); total = 100; round_();
	CHECK(data_bytes == 100 && arp_calls == 1);
	round_(); round_();
	CHECK(arp_calls == 1 && ndg == 2);

	/* j: USBLOG line sent, GAP send fails -> next round: no second USBLOG, GAP then data, nothing lost (review r1 F3) */
	reset(5000); total = 5100; attempts = 0; fail_nth = 2; round_();
	CHECK(ntext == 1 && order[0] == 1 && data_bytes == 0 && pos == 5000);
	fail_nth = 0; round_();
	CHECK(ntext == 2 && order[1] == 2 && order[2] == 3 && data_bytes == 100 && pos == 5100);

	printf("RESULT: %s %d checks%s\n", fails ? "FAIL" : "PASS", checks, fails ? " failed" : "");
	return fails ? 1 : 0;
}
