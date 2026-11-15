/*
 * mifclk.c - opt-in MIF/INT clock read and one MIF request (see mifclk.h).
 * Freestanding, stack-only, no MMIO of its own.
 *
 * Source: the DVFS client is Linux exynos_acpm_get_rate / exynos_acpm_set_rate
 * (soc/gs cal-if/acpm_dvfs.c), the same mailbox code the reviewed CPU --dvfs and GPU
 * --gpu-clk paths use (acpm.c). Domain indexes MIF = 0, INT = 1:
 * dt-bindings/clock/gs101.h:303-304 [read from source] and the observed
 * `SWEET FVMAP: dom 0 type=0 num_of_lv=13` / `dom 1 type=1 num_of_lv=7`
 * (run gputest-bbb3-mem) [observed]. Whether the firmware answers FREQ_GET for dom 0/1 and
 * acts on a FREQ_REQ for dom 0 is NOT known (guess): every refusal and every non-answer is a
 * console line and a token.
 *
 * The audited object (tools/test-bluejay-boot.py, check_mifclk_source_text): no
 * shim_assert_fail in this object, so each function carries a style-allow assert marker.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "acpm.h"
#include "mifclk.h"

#if defined(MIF_CLK) && defined(ACPM_DVFS_MIF_IDX) && defined(ACPM_DVFS_CH)

/* rc 5 (no room) is gone: the room is checked BEFORE any ACPM traffic (review r1 finding 2) */
enum { RC_SENT = 0, RC_REFUSED = 1, RC_AT_TOP = 2, RC_GET = 3, RC_GATE = 4 };
#define IDX_MIF		ACPM_DVFS_MIF_IDX
#define IDX_INT		(ACPM_DVFS_MIF_IDX + 1u)
/* 5 tokens " sweetmifXX=" (12 chars) + up to 10 digits each + the NUL, rounded up */
#define MIF_TOK_MAX	128u
__attribute__((noipa)) static uint32_t put_txt(char *dst, uint32_t cap, uint32_t pos,
					       const char *s)
{
	/* style-allow: assert audited object (mifclk.o undef set): no shim_assert_fail here;
	 * bounded by cap and the NUL of s */
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static uint32_t put_dec(char *dst, uint32_t cap, uint32_t pos, uint32_t v)
{
	char t[10];
	uint32_t n = 0;

	/* style-allow: assert audited object (mifclk.o undef set): no shim_assert_fail here;
	 * at most 10 digits (t[10]) and cap bounds the copy */
	t[n++] = (char)('0' + v % 10u);
	v /= 10u;
	for (uint32_t i = 1; i < sizeof(t) && v; i++) {
		t[n++] = (char)('0' + v % 10u);
		v /= 10u;
	}
	while (n && pos + 1 < cap)
		dst[pos++] = t[--n];
	return pos;
}

/* "MIF <a><b> <k>=<v>": one console line with up to one decimal */
static void say(const char *a, const char *b, const char *k, uint32_t v)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t p = put_txt(line, sizeof(line), 0, a);

	/* style-allow: assert audited object (mifclk.o undef set): console helper, nothing to assert */
	p = put_txt(line, sizeof(line), p, b);
	if (k) {
		p = put_txt(line, sizeof(line), p, " ");
		p = put_txt(line, sizeof(line), p, k);
		p = put_txt(line, sizeof(line), p, "=");
		p = put_dec(line, sizeof(line), p, v);
	}
	line[p] = 0;
	con_str("MIF", line);
}

/* one command-line byte: a separate noipa function so the store is `strb w, [x0, w1, uxtw]` on the
 * argument pointer, a form the object audit proves argument-based (no post-increment on a copy) */
__attribute__((noipa)) static void cmd_put(volatile char *cmd, uint32_t pos, char ch)
{
	/* style-allow: assert audited object (mifclk.o undef set): the caller checks pos < cap */
	cmd[pos] = ch;
}

/* append " name=value" to a field of the transaction line */
static uint32_t kv(char *line, uint32_t p, const char *k, uint32_t v)
{
	/* style-allow: assert audited object (mifclk.o undef set): formatting helper */
	p = put_txt(line, RAMLOG_LINE_MAX, p, k);
	return put_dec(line, RAMLOG_LINE_MAX, p, v);
}

/* one transaction as one line: seq err ret val writes published (+ the reason when it failed);
 * a loud GAP when it did */
static void xline(const char *what, uint32_t dom, const struct acpm_xfer *x)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t p;

	/* style-allow: assert audited object (mifclk.o undef set): console helper, a failed
	 * transaction is data to print, not an invariant */
	if (!x->seq && !x->err)
		return;
	p = put_txt(line, sizeof(line), 0, what);
	p = kv(line, p, " dom=", dom);
	p = kv(line, p, " seq=", x->seq);
	p = kv(line, p, " err=", (uint32_t)x->err);
	p = kv(line, p, " ret=", x->ret);
	p = kv(line, p, " val=", x->value);
	p = kv(line, p, " writes=", x->n);
	p = kv(line, p, " pub=", x->published);
	line[p] = 0;
	con_str("MIF", line);
	if (x->err) {
		say("GAP: ", what, 0, 0);
		say("GAP: refused/failed: ", acpm_why(x->why), 0, 0);
	}
}

/* GET one domain into *khz (0 stays 0 on failure); returns the acpm status */
static int get_dom(const struct acpm_cfg *c, uint32_t dom, uint32_t *khz, struct acpm_trace *t,
		   const char *what)
{
	/* style-allow: sized-types int is the status type of the acpm_* calls */
	int e;

	/* style-allow: assert audited object (mifclk.o undef set): a refused GET is handled */
	t->n = 0;
	*khz = 0;
	e = acpm_dvfs_get(c, dom, khz, t);
	xline(what, dom, &t->a);
	if (!e)
		say(what, " ok", "khz", *khz);
	return e;
}

/* the gates: a configured, verified DVFS channel with a descriptor that validates and two empty
 * queues; 0 = refused (printed) */
static uint32_t gates(struct acpm_cfg *c)
{
	struct acpm_chan h;
	/* style-allow: sized-types int is the status type of acpm_describe */
	int e;

	/* style-allow: assert audited object (mifclk.o undef set): every refusal is printed */
	if (!c->dvfs_ch || !c->layout_verified || !c->elem_unit_verified || !counter_freq()) {
		say("GAP: ", "gate: no DVFS channel / layout or elem unit unverified / no timer, "
		    "nothing sent", 0, 0);
		return 0;
	}
	c->ch_id = c->dvfs_ch;	/* the DVFS channel, not the PMIC one (as touchprobe.c, gpuclk.c) */
	e = acpm_describe(c, &h);
	if (e) {
		say("GAP: ", acpm_why(h.why), 0, 0);
		return 0;
	}
	if (h.rr != h.rf || h.sr != h.sf) {
		say("GAP: ", "gate: ACPM DVFS queues not empty, nothing sent", 0, 0);
		return 0;
	}
	return 1;
}

/* GET MIF and INT (both must answer; the INT value is recorded, not gated: Linux pairs
 * MIF 3172000 with INT 465000 (T-dd3f section 2) but a refusal would forfeit the one boot
 * [guess either way]),
 * ask MIF for the top row, wait, GET both again. v = { MIF before, INT before, MIF after,
 * INT after } (0 = not read). Returns RC_*. */
static uint32_t mif_run(struct acpm_cfg *c, uint32_t *v)
{
	struct acpm_trace t;
	/* style-allow: sized-types int is the status type of the acpm_* calls */
	int e;

	/* style-allow: assert audited object (mifclk.o undef set): every refusal is a token+line */
	if (!gates(c))
		return RC_GATE;
	acpm_xfer_zero(&t.a);
	acpm_xfer_zero(&t.b);
	t.seq = 1;
	t.n = 0;
	if (get_dom(c, IDX_MIF, &v[0], &t, "get MIF before"))
		return RC_GET;	/* the domain is not understood (or the channel failed) */
	if (get_dom(c, IDX_INT, &v[1], &t, "get INT before"))
		return RC_GET;	/* nothing is asked unless BOTH reads answered (finding 3) */
	e = acpm_dvfs_mif_top(c, &t);
	xline("MIF pre-read", IDX_MIF, &t.a);
	if (t.n > 1)
		xline("MIF request", IDX_MIF, &t.b);
	if (e == ACPM_E_ON) {
		say("MIF already at the requested row: ", "no request sent, the GET is the witness",
		    "khz", v[0]);
		return RC_AT_TOP;
	}
	wait_ticks(counter_freq() / 100);	/* ~10 ms for the firmware to act (guess) */
	(void)get_dom(c, IDX_MIF, &v[2], &t, "get MIF after");
	(void)get_dom(c, IDX_INT, &v[3], &t, "get INT after");
	return e ? RC_REFUSED : RC_SENT;
}

static uint32_t tok(char *dst, uint32_t cap, uint32_t p, const char *name, uint32_t v)
{
	/* style-allow: assert audited object (mifclk.o undef set): formatting helper */
	p = put_txt(dst, cap, p, " ");
	p = put_txt(dst, cap, p, name);
	p = put_txt(dst, cap, p, "=");
	return put_dec(dst, cap, p, v);
}

void mif_clk_cmdline(volatile char *cmd, uint32_t cap)
{
	struct acpm_cfg c;
	char s[MIF_TOK_MAX];
	uint32_t v[4] = { 0, 0, 0, 0 }, rc;
	uint32_t n = 0, p = 0, i = 0;

	/* style-allow: assert audited object (mifclk.o undef set): the no-room case is a printed
	 * GAP that runs BEFORE any ACPM traffic */
	while (n + 1 < cap && cmd[n])
		n++;
	if (n + MIF_TOK_MAX >= cap) {
		say("GAP: ", "no room in the XNU command line for the sweetmif tokens, nothing asked",
		    0, 0);
		return;
	}
	acpm_cfg_init(&c);
	rc = mif_run(&c, v);
	p = tok(s, sizeof(s), p, "sweetmif0b", v[0]);
	p = tok(s, sizeof(s), p, "sweetmif1b", v[1]);
	p = tok(s, sizeof(s), p, "sweetmif0a", v[2]);
	p = tok(s, sizeof(s), p, "sweetmif1a", v[3]);
	p = tok(s, sizeof(s), p, "sweetmifrc", rc);
	s[p] = 0;
	while (s[i] && n + 1 < cap)
		cmd_put(cmd, n++, s[i++]);
	cmd_put(cmd, n, 0);
	say("tokens appended: ", s, 0, 0);
}

#elif defined(MIF_CLK)	/* a MIF_CLK build without the soc.json MIF domain: never silent */

void mif_clk_cmdline(volatile char *cmd, uint32_t cap)
{
	/* style-allow: assert audited object (mifclk.o undef set): stub that only logs the gap */
	(void)cmd;
	(void)cap;
	con_str("MIF", "GAP: --mif-clk compiled without the ACPM DVFS MIF domain, nothing asked");
}

#endif
