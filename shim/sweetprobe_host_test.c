/* Host test for sweetprobe.c (--sweet-probe, xnu patch 0363, docs/research/161 s.1/s.2) and the SLC client of acpm.c (acpm_slc_describe, acpm_slc_version) against a FAKE APM with the SLC
 * channels 10 (sync) and 11 (async notification) and a FAKE PSCI conduit. The fake answers
 * PT_VERSION (0xd005) with a version in reply word 1 and replies to nothing else (a request with
 * another opcode fails the test). Checks: the exact request words and write sequence on a 32-byte
 * and on a 16-byte element, that channel 11 is never written, that only channel 10's slot /
 * pointers / doorbell bit are written, every refusal (descriptor refused, queues not empty, no room
 * in the command line) with ZERO writes, the timeout / wrong-sequence GAPs with the
 * request published and nothing rolled back, the PSCI queries (fixed id table, NOT_SUPPORTED GAPs),
 * the token line, and that no other id or opcode is ever sent.
 * Built by build.sh with the real board macros: gcc -O1 -Wall -Wextra -Werror -DSWEET_PROBE -I.
 * -Iout -include out/board_config.h -o out/sweetprobe_host_test sweetprobe_host_test.c
 * (-DGATE_TEST with ACPM_LAYOUT_VERIFIED forced to 0 by a sed'ed header: the gate refuses before
 * anything is sent). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "acpm.c"
#include "sweetprobe.c"

#define SRAM	ACPM_SRAM_BASE
#define SRAMSZ	ACPM_SRAM_SIZE
#define MBOX	ACPM_MBOX_BASE
#define MBOXSZ	0x1000u
#define INIT_O	ACPM_INITDATA_OFF
#define TAB_O	0xA100u
#define NCH	12u
#define QLEN	5u
#define PT_VERSION_FAKE	0x00010008u	/* 1.8: the first version with the async channel (PT_VERSION_ASYNC 0x10008, slc_acpm.c:35) */
/* per-channel queue areas (the fake keeps 10 and 11 apart) */
#define REQ_REAR_O(ch)	(0xB000u + 0x100u * ((ch) - 10u))
#define REQ_FRONT_O(ch)	(REQ_REAR_O(ch) + 4u)
#define RSP_REAR_O(ch)	(REQ_REAR_O(ch) + 8u)
#define RSP_FRONT_O(ch)	(REQ_REAR_O(ch) + 12u)
#define REQ_BASE_O(ch)	(0xC000u + 0x1000u * ((ch) - 10u))
#define RSP_BASE_O(ch)	(0xE000u + 0x1000u * ((ch) - 10u))
#define GARB(i)	(0xdead0000u + (i))

static uint32_t sram[SRAMSZ / 4];
static uint32_t mbox[MBOXSZ / 4];
static struct { uintptr_t pa; uint32_t v; } wlog[256];
static unsigned nw, nbell, nreq, bad_op, nsmc;
static uint64_t now;
/*
 * element bytes of channels 10 / 11 (as written into q_elem_size)
 */
static unsigned esz10, esz11;
/*
 * the APM never takes the request / replies with another sequence number
 */
static int never, badseq, two;
static unsigned apm_busy, apm_left;
static uint32_t apm_w[8];
/*
 * fake PSCI results: VERSION, FEAT(suspend64), FEAT(off), FEAT(suspend32)
 */
static uint32_t psci_ret[4];
static struct { uint64_t fn, a1; } smc_log[16];

uint64_t counter_freq(void) { return 24576000ull; }
uint64_t touch_ticks(void) { return now += 200; }

uint64_t sweetprobe_smc(uint64_t fn, uint64_t a1)
{
	assert(nsmc < 16);
	smc_log[nsmc].fn = fn;
	smc_log[nsmc++].a1 = a1;
	if (fn == 0x84000000u)
		return psci_ret[0];
	if (fn == 0x8400000Au)
		return a1 == 0xC4000001u ? psci_ret[1] : a1 == 0x84000002u ? psci_ret[2] : a1 == 0x84000001u ? psci_ret[3] : 0xFFFFFFFFull;
	assert(0 && "a PSCI function id outside the fixed table");
	return 0;
}

static char lines[512][RAMLOG_LINE_MAX + 24];
static unsigned nl;
static volatile char cmd[2048];

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}

static uint32_t *cell(uintptr_t pa)
{
	if (pa >= SRAM && pa < SRAM + SRAMSZ)
		return &sram[(pa - SRAM) / 4];
	if (pa >= MBOX && pa < MBOX + MBOXSZ)
		return &mbox[(pa - MBOX) / 4];
	return NULL;
}

static uint32_t S(uint32_t off) { return sram[off / 4]; }
static void setS(uint32_t off, uint32_t v) { sram[off / 4] = v; }

static void apm_complete(void)
{
	uint32_t rear = S(REQ_REAR_O(10)), sf = S(RSP_FRONT_O(10)), esz = esz10;

	apm_busy = 0;
	setS(REQ_REAR_O(10), (rear + 1) % QLEN);	/* the request is consumed */
	if (apm_w[2] != ACPM_SLC_PT_VERSION) {
		bad_op++;
		return;
	}
	nreq++;
	setS(RSP_BASE_O(10) + esz * sf + 0, (apm_w[0] & 0xffff0000u) + (badseq ? (1u << 16) : 0));
	setS(RSP_BASE_O(10) + esz * sf + 4, PT_VERSION_FAKE);
	/*
	 * reply words 2/3 carry whatever: the client must not read them as an error
	 */
	setS(RSP_BASE_O(10) + esz * sf + 8, 0x77u);
	setS(RSP_BASE_O(10) + esz * sf + 12, 0x99u);
	setS(RSP_FRONT_O(10), (sf + (two ? 2u : 1u)) % QLEN);	/* two: two reply entries for one request */
}

int try_read32(uintptr_t pa, uint32_t *v)
{
	uint32_t *c = cell(pa);

	if (!c)
		return 0;
	if (apm_busy && (pa == SRAM + RSP_FRONT_O(10) || pa == SRAM + REQ_REAR_O(10))) {
		if (apm_left)
			apm_left--;
		else
			apm_complete();
	}
	*v = *c;
	return 1;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	uint32_t *c = cell(pa);

	if (!c)
		return 0;
	assert(nw < 256);
	wlog[nw].pa = pa;
	wlog[nw++].v = v;
	*c = v;
	if (pa == MBOX + ACPM_INTGR1) {
		uint32_t rear = S(REQ_REAR_O(10)), i;

		nbell++;
		assert(v == 1u << ACPM_SLC_CH && "only channel 10's doorbell bit");
		assert(rear != S(REQ_FRONT_O(10)) && "doorbell rung without a request");
		for (i = 0; i < 8; i++)
			apm_w[i] = i < esz10 / 4u ? S(REQ_BASE_O(10) + esz10 * rear + 4 * i) : 0xFFFFFFFFu;
		if (!never) {
			apm_busy = 1;
			apm_left = 3;
		}
	}
	return 1;
}

static void chan_desc(uint32_t ch, uint32_t esz)
{
	uint32_t d = TAB_O + 0x48u * ch;

	setS(d + 0x0c, 1);
	setS(d + 0x10, REQ_REAR_O(ch));
	setS(d + 0x14, REQ_FRONT_O(ch));
	setS(d + 0x18, REQ_BASE_O(ch));
	setS(d + 0x28, RSP_REAR_O(ch));
	setS(d + 0x2c, RSP_FRONT_O(ch));
	setS(d + 0x30, RSP_BASE_O(ch));
	setS(d + 0x34, QLEN);
	setS(d + 0x38, esz);
	setS(d + 0x44, ch == 10 ? 1u : 0u);
	setS(REQ_REAR_O(ch), 4);	/* both queues empty (rear == front) */
	setS(REQ_FRONT_O(ch), 4);
	setS(RSP_REAR_O(ch), 2);
	setS(RSP_FRONT_O(ch), 2);
}

static void fresh(uint32_t e10, uint32_t e11, const char *start)
{
	uint32_t i, j, d;

	memset(mbox, 0, sizeof(mbox));
	memset(wlog, 0, sizeof(wlog));
	nw = nbell = nreq = bad_op = nsmc = nl = 0;
	now = 1000;
	never = badseq = two = 0;
	apm_busy = apm_left = 0;
	esz10 = e10;
	esz11 = e11;
	psci_ret[0] = 0x00010001u;	/* PSCI 1.1 */
	psci_ret[1] = 0x2u;		/* FEATURES(CPU_SUSPEND): extended format, no OS-initiated mode */
	psci_ret[2] = 0u;
	psci_ret[3] = 0xFFFFFFFFu;
	for (i = 0; i < SRAMSZ / 4; i++)
		sram[i] = GARB(i);
	setS(INIT_O + 0x08, TAB_O);
	setS(INIT_O + 0x0C, 3);
	setS(INIT_O + 0x18, NCH);
	for (i = 0; i < NCH; i++) {
		d = TAB_O + 0x48u * i;
		for (j = 0; j < 18; j++)
			setS(d + 4 * j, 0x11110000u + 0x100 * i + j);
		setS(d + 0x00, i);	/* table index == channel id */
	}
	chan_desc(10, e10);
	chan_desc(11, e11);
	memset((void *)cmd, 0, sizeof(cmd));
	for (i = 0; start[i]; i++)
		cmd[i] = start[i];
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static void dump_lines(void)
{
	for (unsigned i = 0; i < nl; i++)
		fprintf(stderr, "  %s\n", lines[i]);
	fprintf(stderr, "  cmd=%s\n", (const char *)cmd);
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); dump_lines(); assert(0); } } while (0)

static int cmd_has(const char *s) { return strstr((const char *)cmd, s) != NULL; }

/* the value of token " sweetprobe_<name>=" (UINT32_MAX + 1 when absent) */
static uint64_t token(const char *name)
{
	char key[64];
	const char *p;

	snprintf(key, sizeof key, " sweetprobe_%s=", name);
	p = strstr((const char *)cmd, key);
	return p ? strtoull(p + strlen(key), NULL, 10) : 0x100000000ull;
}

/*
 * every write of the whole test is one of channel 10's: its slot words, its two pointers, its
 * doorbell bit, the status clear
 */
static void only_ch10_writes(void)
{
	unsigned i;

	for (i = 0; i < nw; i++) {
		uintptr_t pa = wlog[i].pa;
		int ok = 0;

		ok |= pa >= SRAM + REQ_BASE_O(10) && pa < SRAM + REQ_BASE_O(10) + QLEN * 32u;
		ok |= pa == SRAM + REQ_FRONT_O(10) || pa == SRAM + RSP_REAR_O(10);
		ok |= pa == MBOX + ACPM_INTGR1 || pa == MBOX + ACPM_INTCR0;
		if (!ok || (pa >= SRAM + REQ_REAR_O(11) && pa < SRAM + REQ_REAR_O(11) + 0x100u) || (pa >= SRAM + REQ_BASE_O(11) && pa < SRAM + REQ_BASE_O(11) + 0x1000u)) {
			fprintf(stderr, "stray write %u: pa=%#lx v=%#x\n", i, (unsigned long)pa, wlog[i].v);
			assert(0);
		}
		if (pa == MBOX + ACPM_INTGR1 || pa == MBOX + ACPM_INTCR0)
			assert(wlog[i].v == 1u << ACPM_SLC_CH);
	}
	assert(bad_op == 0 && "an opcode other than PT_VERSION reached the APM");
}

static void psci_table_ok(void)
{
	EXPECT(nsmc == 4);
	EXPECT(smc_log[0].fn == 0x84000000u && smc_log[0].a1 == 0);
	EXPECT(smc_log[1].fn == 0x8400000Au && smc_log[1].a1 == 0xC4000001u);
	EXPECT(smc_log[2].fn == 0x8400000Au && smc_log[2].a1 == 0x84000002u);
	EXPECT(smc_log[3].fn == 0x8400000Au && smc_log[3].a1 == 0x84000001u);
}

static void test_happy(uint32_t esz)
{
	uint32_t w = esz / 4u, i, slot = REQ_BASE_O(10) + esz * 4u;

	fresh(esz, esz == 32 ? 16 : 32, "debug=0xc cpus=5");
	sweet_probe_cmdline(cmd, sizeof(cmd));
	psci_table_ok();
	EXPECT(nreq == 1 && nbell == 1 && !has("GAP: slc"));
	/*
	 * writes: the w request words (seq 1: cmd[0] = 1 << 16, cmd[1] = 0, cmd[2] = 0xd005, cmd[3] =
	 * 0, words 4..7 = 0), front := 0, doorbell, reply rear, status clear
	 */
	EXPECT(nw == w + 4u);
	for (i = 0; i < w; i++)
		EXPECT(wlog[i].pa == SRAM + slot + 4u * i && wlog[i].v == (i == 0 ? 1u << 16 : i == 2 ? 0xd005u : 0u));
	EXPECT(wlog[w].pa == SRAM + REQ_FRONT_O(10) && wlog[w].v == 0);
	EXPECT(wlog[w + 1].pa == MBOX + ACPM_INTGR1 && wlog[w + 1].v == 1u << 10);
	EXPECT(wlog[w + 2].pa == SRAM + RSP_REAR_O(10) && wlog[w + 3].pa == MBOX + ACPM_INTCR0);
	only_ch10_writes();
	EXPECT(token("slcrc") == 0 && token("slcv") == PT_VERSION_FAKE && token("slce") == 0);
	EXPECT(token("pv") == 0x00010001u && token("pf1") == 2 && token("pf2") == 0 && token("pf3") == 0xFFFFFFFFu);
	/*
	 * descriptors: a = qlen 5 | esz << 9 | type 1 << 16 | poll (10 only) << 20 | why 0 | present <<
	 * 31
	 */
	EXPECT(token("c10a") == (5u | esz << 9 | 1u << 16 | 1u << 20 | 1u << 31));
	EXPECT(token("c11a") == (5u | (esz == 32 ? 16u : 32u) << 9 | 1u << 16 | 1u << 31));
	EXPECT(token("c10b") == (4u | 4u << 8 | 2u << 16 | 2u << 24));
	EXPECT(cmd_has("debug=0xc cpus=5 sweetprobe_pv="));	/* appended after the existing tokens */
	/* review 0363 finding 7: the slot guard ends exactly at the element end (slot + eb), not later */
	{
		struct wguard g;
		struct acpm_cfg c;
		struct acpm_chan h;
		uintptr_t sl = SRAM + slot;

		memset(&g, 0, sizeof g); memset(&c, 0, sizeof c); memset(&h, 0, sizeof h); h.qlen = QLEN;
		acpm_guard(&g, &c, &h, sl, esz);
		EXPECT(w32_allowed(sl + esz - 4u, 0, &g) == 1 && w32_allowed(sl + esz, 0, &g) == 0 && w32_allowed(sl + esz + 4u, 0, &g) == 0);
	}
}

static void test_refusals(void)
{
	/*
	 * a 48-byte element on channel 10: the descriptor is refused, nothing is written, the PSCI part
	 * still runs
	 */
	fresh(48, 16, "a=1");
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nbell == 0 && nreq == 0 && has("GAP: slc descriptor refused") && has("GAP: slc descriptor status") && token("slcrc") == 2 && token("slcv") == 0);
	EXPECT(nsmc == 4);
	/* ch 10 absent from the table (3 channels only): NOCH */
	fresh(32, 16, "a=1");
	setS(INIT_O + 0x18, 3);
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && token("slcrc") == 2 && has("GAP: slc descriptor refused"));
	EXPECT(token("c10a") >> 31 == 0);
	/* a queue that is not empty: nothing is written */
	fresh(32, 16, "a=1");
	setS(REQ_FRONT_O(10), 0);
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nbell == 0 && token("slcrc") == 4 && has("GAP: slc ch10 queue not empty"));
	fresh(32, 16, "a=1");
	setS(RSP_FRONT_O(10), 3);
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && token("slcrc") == 4);
	/* an index outside the queue: the descriptor check refuses */
	fresh(32, 16, "a=1");
	setS(REQ_FRONT_O(10), 9);
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && token("slcrc") == 2);
	/* the client refuses any channel but 10 / 11 for the describe */
	{
		struct acpm_chan h;
		struct acpm_cfg c;

		fresh(32, 16, "");
		acpm_cfg_init(&c);
		EXPECT(acpm_slc_describe(&c, 2, &h) == ACPM_E_ARG && acpm_slc_describe(&c, 12, &h) == ACPM_E_ARG);
		EXPECT(acpm_slc_describe(&c, 11, &h) == ACPM_OK && h.esz_b == 16 && h.qlen == QLEN && nw == 0);
	}
	/* review 0363 finding 7: the element size the request is built for must equal the descriptor's (16 vs a 32 B channel and 32 vs a 16 B channel are refused, nothing written) */
	{
		struct acpm_cfg c, d;
		struct acpm_trace t;
		int e;

		acpm_cfg_init(&c);
		cfg_chan(&c, &d, ACPM_SLC_CH);
		fresh(32, 16, "");
		memset(&t, 0, sizeof t); acpm_xfer_zero(&t.a); acpm_xfer_zero(&t.b); t.seq = 1;
		e = xfer_eb(&d, XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u, &t, &t.a, 16u);
		EXPECT(e == ACPM_E_DESC && t.a.why == ACPM_W_ESZ && nw == 0 && nbell == 0);
		fresh(16, 16, "");
		memset(&t, 0, sizeof t); acpm_xfer_zero(&t.a); acpm_xfer_zero(&t.b); t.seq = 1;
		e = xfer_eb(&d, XM_SLCV, 0u, 0u, ACPM_SLC_PT_VERSION, 0u, &t, &t.a, 32u);
		EXPECT(e == ACPM_E_DESC && t.a.why == ACPM_W_ESZ && nw == 0 && nbell == 0);
	}
}

static void test_failures(void)
{
	/* the APM never answers: the request stays published, nothing is rolled back, a GAP names it */
	fresh(32, 16, "a=1");
	never = 1;
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(token("slcrc") == 1 && token("slcv") == 0 && has("GAP: slc PT_VERSION"));
	EXPECT((token("slce") & 0xff) == ACPM_E_TIMEOUT && (token("slce") >> 8 & 0xff) == 2);
	/*
	 * request rear 4 (not consumed), request front 0 (published), reply pointers 2/2
	 */
	EXPECT(token("slcs") == (4u | 0u << 8 | 2u << 16 | 2u << 24));
	EXPECT(nbell == 1 && nreq == 0);
	only_ch10_writes();
	/* a reply with another sequence number: the client reports it, the channel is drained */
	fresh(32, 16, "a=1");
	badseq = 1;
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(token("slcrc") == 1 && (token("slce") & 0xff) == ACPM_E_REPLY && has("GAP: slc PT_VERSION"));
	only_ch10_writes();
	/* two reply entries for the one request (review 0363 Opus finding 3): refused as a bad reply, drained: reply rear := the new front */
	fresh(32, 16, "a=1");
	two = 1;
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(token("slcrc") == 1 && (token("slce") & 0xff) == ACPM_E_REPLY && has("GAP: slc PT_VERSION failed: reply has another sequence number or entry count"));
	EXPECT(S(RSP_REAR_O(10)) == S(RSP_FRONT_O(10)) && S(RSP_FRONT_O(10)) == 4u);
	only_ch10_writes();
	/* no room in the command line: nothing is asked, nothing appended, one GAP */
	{
		char big[2048];

		memset(big, 'x', sizeof big);
		big[sizeof big - 1 - 100] = 0;	/* 1947 chars: less than SP_TOK_MAX free */
		fresh(32, 16, big);
	}
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nsmc == 0 && !cmd_has("sweetprobe_") && has("GAP: no room in the XNU command line"));
}

static void test_psci_gaps(void)
{
	fresh(32, 16, "a=1");
	psci_ret[1] = 0xFFFFFFFFu;	/* NOT_SUPPORTED */
	psci_ret[2] = 0xFFFFFFFFu;
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(has("GAP: psci FEATURES(CPU_SUSPEND 0xC4000001) is NOT_SUPPORTED") && has("GAP: psci FEATURES(CPU_OFF 0x84000002) is NOT_SUPPORTED"));
	EXPECT(token("pf1") == 0xFFFFFFFFu && token("pf2") == 0xFFFFFFFFu);
	/* the unsupported index reaches no smc */
	EXPECT(psci_call(9) == 0xFFFFFFFFu && nsmc == 4);
}

static void test_psci_version_gap(void)
{
	/* a negative PSCI_VERSION is a GAP (rule 7, review 0363 Opus finding 2), not a printed version */
	fresh(32, 16, "a=1");
	psci_ret[0] = 0xFFFFFFFFu;	/* NOT_SUPPORTED */
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(has("GAP: psci PSCI_VERSION returned an error") && token("pv") == 0xFFFFFFFFu);
	/* a good version prints no such GAP */
	fresh(32, 16, "a=1");
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(!has("GAP: psci PSCI_VERSION"));
}

int main(void)
{
#ifdef GATE_TEST
	/*
	 * the board macros say layout_verified = 0 (sed'ed header): the PT_VERSION request is refused,
	 * nothing written, the token says so
	 */
	fresh(32, 16, "a=1");
	sweet_probe_cmdline(cmd, sizeof(cmd));
	EXPECT(nw == 0 && nbell == 0 && nreq == 0 && token("slcrc") == 1 && has("GAP: slc PT_VERSION"));
	puts("PASS sweetprobe gate host test");
	return 0;
#else
	test_happy(32);
	test_happy(16);
	test_refusals();
	test_failures();
	test_psci_gaps();
	test_psci_version_gap();
	puts("PASS sweetprobe host test");
	return 0;
#endif
}
