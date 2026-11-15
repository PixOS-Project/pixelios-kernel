/*
 * sweetprobe.c - opt-in READ-ONLY PSCI + SLC probes (see sweetprobe.h; xnu patch 0363,
 * docs/research/161 s.1, s.2). Freestanding, stack-only, no MMIO of its own.
 * Tags: [src] read from the cited source, [guess] not verified. Whether the SLC firmware answers
 * PT_VERSION at hand-over time, and its element size, are NOT known: every
 * refusal and every non-answer is a console line and a token.
 * The audited object (tools/sweetprobe_audit.py): no asm (the one smc is in sweetprobe_smc.c), the
 * fixed function-id table below, one acpm_slc_version call site, no retry, no other acpm_* client.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "acpm.h"
#include "sweetprobe.h"

#if defined(SWEET_PROBE) && defined(ACPM_SLC_CH)

/*
 * PSCI function ids [src Linux v6.6 include/uapi/linux/psci.h:33-35, :50]: VERSION 0x84000000,
 * CPU_SUSPEND 64-bit 0xC4000001 / 32-bit 0x84000001, CPU_OFF 0x84000002, FEATURES 0x8400000A.
 * The table is the ONLY source of ids: psci_call takes an index, never an id.
 */
#define PSCI_FN_VERSION		0x84000000u
#define PSCI_FN_FEATURES	0x8400000Au
#define PSCI_ID_SUSPEND64	0xC4000001u
#define PSCI_ID_OFF		0x84000002u
#define PSCI_ID_SUSPEND32	0x84000001u
enum { PQ_VERSION = 0, PQ_FEAT_SUSPEND64, PQ_FEAT_OFF, PQ_FEAT_SUSPEND32, PQ_N };
enum { RC_OK = 0, RC_FAILED = 1, RC_DESC = 2, RC_BUSY = 4 };
/* 12 tokens " sweetprobe_xxxx=" (<= 18 chars) + up to 10 digits each + the NUL, rounded up */
#define SP_TOK_MAX	448u

/*
 * the PSCI query itself lives in sweetprobe_smc.c (one `smc #0`, audited by
 * tools/sweetprobe_audit.py: the object audit of this file bans smc): x0 = function id, x1 =
 * argument; returns x0
 */
uint64_t sweetprobe_smc(uint64_t fn, uint64_t a1);

/*
 * one of the four fixed queries; the raw 32-bit result (w0). Any other index returns 0xFFFFFFFF (-1
 * = NOT_SUPPORTED) without a call.
 */
static uint32_t psci_call(uint32_t q)
{
	/* style-allow: assert audited object (sweetprobe.o): the index is a loop counter over PQ_N */
	switch (q) {
	case PQ_VERSION: return (uint32_t)sweetprobe_smc(PSCI_FN_VERSION, 0);
	case PQ_FEAT_SUSPEND64: return (uint32_t)sweetprobe_smc(PSCI_FN_FEATURES, PSCI_ID_SUSPEND64);
	case PQ_FEAT_OFF: return (uint32_t)sweetprobe_smc(PSCI_FN_FEATURES, PSCI_ID_OFF);
	case PQ_FEAT_SUSPEND32: return (uint32_t)sweetprobe_smc(PSCI_FN_FEATURES, PSCI_ID_SUSPEND32);
	default: return 0xFFFFFFFFu;
	}
}

__attribute__((noipa)) static uint32_t put_txt(char *dst, uint32_t cap, uint32_t pos, const char *s)
{
	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): bounded by cap and the NUL of s
	 */
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static uint32_t put_dec(char *dst, uint32_t cap, uint32_t pos, uint32_t v)
{
	char t[10];
	uint32_t n = 0;

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): at most 10 digits (t[10]) and
	 * cap bounds the copy
	 */
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

/* one console line "<a><b>[ k=v]" under the label SWEET PROBE */
static void say(const char *a, const char *b, const char *k, uint32_t v)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t p = put_txt(line, sizeof(line), 0, a);

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): console helper, nothing to
	 * assert
	 */
	p = put_txt(line, sizeof(line), p, b);
	if (k) {
		p = put_txt(line, sizeof(line), p, " ");
		p = put_txt(line, sizeof(line), p, k);
		p = put_txt(line, sizeof(line), p, "=");
		p = put_dec(line, sizeof(line), p, v);
	}
	line[p] = 0;
	con_str("SWEET PROBE", line);
}

/*
 * one command-line byte, a separate noipa function (the object audit sees an argument-based store,
 * as mifclk.c cmd_put)
 */
__attribute__((noipa)) static void cmd_put(volatile char *cmd, uint32_t pos, char ch)
{
	/* style-allow: assert audited object (sweetprobe.o undef set): the caller checks pos < cap */
	cmd[pos] = ch;
}

static uint32_t tok(char *dst, uint32_t cap, uint32_t p, const char *name, uint32_t v)
{
	/* style-allow: assert audited object (sweetprobe.o undef set): formatting helper */
	p = put_txt(dst, cap, p, " sweetprobe_");
	p = put_txt(dst, cap, p, name);
	p = put_txt(dst, cap, p, "=");
	return put_dec(dst, cap, p, v);
}

static uint32_t clamp(uint32_t v, uint32_t hi)
{
	/* style-allow: assert audited object (sweetprobe.o undef set): pure helper */
	return v > hi ? hi : v;
}

/*
 * a = qlen | esz raw << 9 | type << 16 | poll << 20 | why << 24 | present << 31; b = rr | rf << 8 |
 * sr << 16 | sf << 24 (each clamped); see sweetprobe.h
 */
static void pack_chan(const struct acpm_chan *h, uint32_t *a, uint32_t *b)
{
	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): pure packing of a descriptor
	 * already read
	 */
	*a = clamp(h->qlen, 511u) | clamp(h->esz, 127u) << 9 | clamp(h->d[3], 15u) << 16 |
	     (h->ap_poll ? 1u : 0u) << 20 | clamp(h->why, 127u) << 24 |
	     (h->have_desc ? 1u : 0u) << 31;
	*b = clamp(h->rr, 255u) | clamp(h->rf, 255u) << 8 | clamp(h->sr, 255u) << 16 |
	     clamp(h->sf, 255u) << 24;
}

struct slc_out {
	uint32_t c10a, c10b, c11a, c11b;	/* pack_chan of channel 10 / 11 */
	/*
	 * RC_*, PT_VERSION reply word 1, err | published << 8 | why << 16, the four queue pointers
	 * after a failure
	 */
	uint32_t rc, ver, err, st;
};

/*
 * step A for one channel: reads only; the console line is the GAP when it was refused. Returns the
 * acpm status.
 */
static int slc_desc(const struct acpm_cfg *c, uint32_t ch, struct acpm_chan *h, uint32_t *a,
		    uint32_t *b)
{
	/* style-allow: sized-types int is the status type of acpm_slc_describe */
	int e;

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): a refused describe is data to
	 * print
	 */
	e = acpm_slc_describe(c, ch, h);
	pack_chan(h, a, b);
	if (e) {
		say("GAP: slc descriptor refused: ", acpm_why(h->why), "ch", ch);
		/* review 0363 finding 8: a read fault leaves why "ok" */
		say("GAP: slc descriptor status ", "", "err", (uint32_t)e);
	} else {
		say("slc descriptor ok ", "", "ch", ch);
	}
	return e;
}

/*
 * the failed-request record: err | published << 8 | why << 16 and the four queue pointers (~0 =
 * unreadable, clamped like the descriptor indices)
 */
static void slc_fail(const struct acpm_xfer *x, struct slc_out *o)
{
	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): pure packing of a transaction
	 * trace
	 */
	o->err = clamp((uint32_t)x->err, 255u) | clamp(x->published, 255u) << 8 |
		 clamp(x->why, 255u) << 16;
	o->st = clamp(x->state[0], 255u) | clamp(x->state[1], 255u) << 8 |
		clamp(x->state[2], 255u) << 16 | clamp(x->state[3], 255u) << 24;
	say("GAP: slc PT_VERSION failed: ", acpm_why(x->why), "published", x->published);
	say("GAP: slc PT_VERSION no usable reply, channel left as is: ", "", "err", (uint32_t)x->err);
}

/*
 * steps A and B of the SLC probe. Step B (the one request) runs only after channel 10's descriptor
 * validated and both queues are empty.
 */
static void slc_run(const struct acpm_cfg *c, struct slc_out *o)
{
	struct acpm_chan h10, h11;
	struct acpm_trace t;
	/* style-allow: sized-types int is the status type of the acpm_* calls */
	int e;

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): every refusal is a token and a
	 * line
	 */
	e = slc_desc(c, ACPM_SLC_CH, &h10, &o->c10a, &o->c10b);
	/*
	 * channel 11 is a notification channel: its descriptor is read, nothing else
	 */
	(void)slc_desc(c, ACPM_SLC_ASYNC_CH, &h11, &o->c11a, &o->c11b);
	if (e) {
		o->rc = RC_DESC;
		return;
	}
	if (h10.rr != h10.rf || h10.sr != h10.sf) {
		o->rc = RC_BUSY;
		say("GAP: slc ch10 queue not empty: ", "nothing written", "rr", h10.rr);
		return;
	}
	acpm_xfer_zero(&t.a);
	acpm_xfer_zero(&t.b);
	t.seq = 1;
	t.n = 0;
	e = acpm_slc_version(c, &o->ver, &t);
	if (e) {
		o->rc = RC_FAILED;
		slc_fail(&t.a, o);
		return;
	}
	o->rc = RC_OK;
	say("slc PT_VERSION answered ", "", "version", o->ver);
}

static void psci_run(uint32_t v[PQ_N])
{
	uint32_t q;

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): a fixed loop over the four table
	 * indices
	 */
	for (q = 0; q < PQ_N; q++) {
		v[q] = psci_call(q);
		say("psci query ", "", "result", v[q]);
	}
	if ((int32_t)v[PQ_VERSION] < 0)
		say("GAP: psci PSCI_VERSION returned an error: ",
		    "no PSCI version, FEATURES results not trustworthy", "result", v[PQ_VERSION]);
	if ((int32_t)v[PQ_FEAT_SUSPEND64] < 0)
		say("GAP: psci FEATURES(CPU_SUSPEND 0xC4000001) is NOT_SUPPORTED: ",
		    "CPU_SUSPEND goes through another id or not at all", 0, 0);
	if ((int32_t)v[PQ_FEAT_OFF] < 0)
		say("GAP: psci FEATURES(CPU_OFF 0x84000002) is NOT_SUPPORTED: ",
		    "CPU_OFF is not available to the OS", 0, 0);
}

static uint32_t build_tokens(char *s, const uint32_t pv[PQ_N], const struct slc_out *o)
{
	uint32_t p = 0;

	/* style-allow: assert audited object (sweetprobe.o undef set): formatting only */
	p = tok(s, SP_TOK_MAX, p, "pv", pv[PQ_VERSION]);
	p = tok(s, SP_TOK_MAX, p, "pf1", pv[PQ_FEAT_SUSPEND64]);
	p = tok(s, SP_TOK_MAX, p, "pf2", pv[PQ_FEAT_OFF]);
	p = tok(s, SP_TOK_MAX, p, "pf3", pv[PQ_FEAT_SUSPEND32]);
	p = tok(s, SP_TOK_MAX, p, "c10a", o->c10a);
	p = tok(s, SP_TOK_MAX, p, "c10b", o->c10b);
	p = tok(s, SP_TOK_MAX, p, "c11a", o->c11a);
	p = tok(s, SP_TOK_MAX, p, "c11b", o->c11b);
	p = tok(s, SP_TOK_MAX, p, "slcrc", o->rc);
	p = tok(s, SP_TOK_MAX, p, "slcv", o->ver);
	p = tok(s, SP_TOK_MAX, p, "slce", o->err);
	p = tok(s, SP_TOK_MAX, p, "slcs", o->st);
	s[p] = 0;
	return p;
}

void sweet_probe_cmdline(volatile char *cmd, uint32_t cap)
{
	struct acpm_cfg c;
	struct slc_out o = { 0, 0, 0, 0, 0, 0, 0, 0 };
	uint32_t pv[PQ_N] = { 0, 0, 0, 0 };
	char s[SP_TOK_MAX];
	uint32_t n = 0, i = 0;

	/*
	 * style-allow: assert audited object (sweetprobe.o undef set): the no-room case is a printed
	 * GAP that runs BEFORE any query or ACPM traffic
	 */
	while (n + 1 < cap && cmd[n])
		n++;
	if (n + SP_TOK_MAX >= cap) {
		say("GAP: no room in the XNU command line for the sweetprobe tokens, ", "nothing asked",
		    0, 0);
		return;
	}
	acpm_cfg_init(&c);
	psci_run(pv);
	slc_run(&c, &o);
	(void)build_tokens(s, pv, &o);
	while (s[i] && n + 1 < cap)
		cmd_put(cmd, n++, s[i++]);
	cmd_put(cmd, n, 0);
	say("tokens appended: ", s, 0, 0);
}

#elif defined(SWEET_PROBE)	/* a SWEET_PROBE build without the soc.json slc block: never silent */

void sweet_probe_cmdline(volatile char *cmd, uint32_t cap)
{
	/* style-allow: assert audited object (sweetprobe.o undef set): stub that only logs the gap */
	(void)cmd;
	(void)cap;
	con_str("SWEET PROBE", "GAP: --sweet-probe compiled without the acpm.slc block, nothing asked");
}

#endif
