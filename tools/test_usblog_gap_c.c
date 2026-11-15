/*
 * Host test for patch 0179 (usb log GAP, review usblog-gap-0179-r1 F1): tools/test_usblog_gap_c.py extracts the added
 * helpers (ud_log_send, struct ud_log_gap, ud_log_drain) from the patch into gap.inc and builds them against the fakes
 * below: sweet_log_copy (same arithmetic as bsd/dev/sweet_ufs.c:868-880: jump to total - SIZE when the reader is more
 * than SIZE behind, then copy, pos += n) and a scripted sock_send. Data byte at absolute offset o is 'a' + o % 26, so the
 * receiver checks content and order: a GAP datagram advances the expected offset by the lost count, a data datagram must
 * continue exactly there (no duplicate, no hole). snprintf can be overridden to test the length clamp (F4).
 * Prints "RESULT: PASS n checks" / "RESULT: FAIL ..."; exit 0/1.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <netinet/in.h>

typedef int socket_t;
#define RING 4096u

static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static uint64_t total;                  /* console ring write position (monotonic) */
static int send_no;                     /* sock_send call counter */
static int fail_at[8], nfail;           /* 1-based call numbers that return an error */
static int snprintf_ret = -2;           /* -2: real value; else forced return */

struct dg { int is_gap; size_t len; };
static struct dg dgs[64];
static int ndg;
static char gaptext[8][200];
static int ngaptext;
static uint64_t exp_off, lost_sum;      /* receiver model */
static int order_ok = 1, content_ok = 1;
static uint64_t data_bytes;

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
	send_no++;
	for (int i = 0; i < nfail; i++) {
		if (fail_at[i] == send_no) {
			return 55;
		}
	}
	size_t len = m->msg_iov[0].iov_len;
	const char *b = m->msg_iov[0].iov_base;
	*sent = len;
	struct dg *d = &dgs[ndg++];
	d->len = len;
	d->is_gap = len >= 11 && len < 1000 && memcmp(b, "\nSWEET GAP:", 11) == 0;
	if (d->is_gap) {
		if (len < 200 && ngaptext < 8) {
			memcpy(gaptext[ngaptext], b, len);
			gaptext[ngaptext++][len] = 0;
			unsigned long long n = 0;
			const char *p = strstr(gaptext[ngaptext - 1], "at least ");
			if (p) {
				n = strtoull(p + 9, NULL, 10);
			}
			exp_off += n;
			lost_sum += n;
		} else {
			order_ok = 0;
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
	}
	return 0;
}

static int
fake_snprintf(char *buf, size_t sz, const char *f, ...)
{
	va_list ap;
	va_start(ap, f);
	int n = vsnprintf(buf, sz, f, ap);
	va_end(ap);
	return snprintf_ret != -2 ? snprintf_ret : n;
}
#define snprintf fake_snprintf

#include "gap.inc"

static struct ud_log_gap g;
static uint64_t pos;
static char chunk[1400];
static struct sockaddr_in to;

static void
reset(void)
{
	memset(&g, 0, sizeof(g));
	memset(dgs, 0, sizeof(dgs));
	memset(gaptext, 0, sizeof(gaptext));
	pos = total = 0;
	send_no = nfail = ndg = ngaptext = 0;
	exp_off = lost_sum = data_bytes = 0;
	order_ok = content_ok = 1;
	snprintf_ret = -2;
}

static void
drain(void)
{
	ud_log_drain(1, &to, &pos, &g, chunk, sizeof(chunk));
}

int
main(void)
{
	/* a: no jump -> no GAP, all bytes, in order */
	reset(); total = 3000; drain();
	CHECK(ngaptext == 0 && ndg == 3 && data_bytes == 3000 && content_ok && pos == 3000 && g.events == 0);

	/* b: jump of 5904 (10000 written, ring 4096): one GAP first, then the 4096 kept bytes */
	reset(); total = 10000; drain();
	CHECK(ngaptext == 1 && dgs[0].is_gap && !dgs[1].is_gap);
	CHECK(strstr(gaptext[0], "at least 5904 bytes lost (event 1, 5904 bytes lost so far)") != NULL);
	CHECK(data_bytes == 4096 && content_ok && exp_off == 10000 && g.skipped == 0 && g.skipped_total == 5904 && g.events == 1);

	/* c: GAP send fails -> nothing lost, retried next round, not double-counted, chunk not skipped */
	reset(); total = 10000; fail_at[nfail++] = 1; drain();
	CHECK(ndg == 0 && g.skipped == 5904 && g.skipped_total == 0 && g.events == 0 && pos == 10000 - RING);
	drain();
	CHECK(ngaptext == 1 && strstr(gaptext[0], "at least 5904 bytes lost (event 1, 5904 bytes lost so far)") != NULL);
	CHECK(data_bytes == 4096 && content_ok && exp_off == 10000 && g.skipped_total == 5904 && g.events == 1 && pos == 10000);

	/* d: first data chunk send fails after a good GAP -> same chunk resent, no second GAP, no byte duplicated or lost */
	reset(); total = 10000; fail_at[nfail++] = 2; drain();
	CHECK(ngaptext == 1 && data_bytes == 0 && g.skipped == 0 && g.events == 1 && pos == 10000 - RING);
	drain();
	CHECK(ngaptext == 1 && data_bytes == 4096 && content_ok && exp_off == 10000 && g.skipped_total == 5904 && g.events == 1);

	/* d2: a later chunk fails mid-round (plain data, no jump) -> resumed exactly there */
	reset(); total = 3000; fail_at[nfail++] = 2; drain(); drain();
	CHECK(ngaptext == 0 && data_bytes == 3000 && content_ok && pos == 3000);

	/* e: two jumps -> event 2 carries the running total */
	reset(); total = 10000; drain();
	total += 9000; drain();                 /* lag 9000 > 4096: jump 4904 */
	CHECK(ngaptext == 2 && g.events == 2 && g.skipped_total == 5904 + 4904);
	CHECK(strstr(gaptext[1], "at least 4904 bytes lost (event 2, 10808 bytes lost so far)") != NULL);
	CHECK(data_bytes == 8192 && content_ok && exp_off == 19000 && order_ok);

	/* f: snprintf clamp (F4): negative -> zero-length send; >= sizeof(gap) -> 159 */
	reset(); total = 10000; snprintf_ret = -1; drain();
	CHECK(dgs[0].len == 0);
	reset(); total = 10000; snprintf_ret = 500; drain();
	CHECK(dgs[0].len == 159);

	printf("RESULT: %s %d checks%s\n", fails ? "FAIL" : "PASS", checks, fails ? " failed" : "");
	return fails ? 1 : 0;
}
