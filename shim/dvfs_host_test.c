/* Host test for the ACPM DVFS client of acpm.c (acpm_dvfs_get / acpm_dvfs_set) against a FAKE APM on channel 1 (docs/research/25).
 * The fake keeps one current rate per CPU cluster (domains 2,3,4), answers FREQ_GET with it in reply word 1, and applies FREQ_REQ
 * (only a rate that is in the table). It is asynchronous like the real one (the doorbell latches the request, the APM finishes after
 * a few polls and only then moves the request rear). Modes: reply to FREQ_REQ or not (unknown on the real firmware, both must work),
 * an error code in reply word 3, a wrong sequence number, never completing. Checks: the exact command words and the exact write
 * sequence, the channel's doorbell bit, that EVERY refusal (unknown domain, rate not in the table, rate above the ceiling, rate 0,
 * pre-read failure, rate already set, non-empty queue, wrong element size) performs ZERO writes (or only the pre-read's), that a
 * request which the APM consumed without a reply is success and leaves the channel usable, that a request that is never consumed is
 * a timeout with the request published (nothing rolled back), and that only channel 1's slot / pointers / doorbell bit are written.
 * It #includes acpm.c (so the statics are reachable) and uses the REAL generated board macros. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -DTOUCH_DVFS_WRITE -I. -Iout -include out/board_config.h -o out/dvfs_host_test dvfs_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "acpm.c"

#if !(SOC_GS101 && defined(ACPM_MBOX_BASE) && defined(ACPM_DVFS_CH))
#error "needs the bluejay board_config.h with the acpm dvfs block (shim/build.sh --board bluejay writes out/board_config.h)"
#endif

#define SRAM	ACPM_SRAM_BASE
#define SRAMSZ	ACPM_SRAM_SIZE
#define MBOX	ACPM_MBOX_BASE
#define MBOXSZ	0x1000u
#define INIT_O	ACPM_INITDATA_OFF
#define TAB_O	0xA100u
#define CH	ACPM_DVFS_CH
#define REQ_REAR_O	0xB000u
#define REQ_FRONT_O	0xB004u
#define RSP_REAR_O	0xB008u
#define RSP_FRONT_O	0xB00Cu
#define REQ_BASE_O	0xC000u
#define RSP_BASE_O	0xD000u
#define QLEN	5u
#define ESZ	16u
#define GARB(i)	(0xdead0000u + (i))

static uint32_t sram[SRAMSZ / 4];
static uint32_t mbox[MBOXSZ / 4];
static struct { uintptr_t pa; uint32_t v; } wlog[256];
static unsigned nw, stray, nbell, nreq, nget, busy_writes;
static uint64_t now;
/* what the fake APM does with a request. The real firmware replies to EVERY request (acpm_ipc.c:446-448, :747-756 [src], review H2). */
static int ignore_req;		/* 1: FREQ_REQ is accepted and answered but the rate does not change (a constraint, a powered-down cluster) */
static uint64_t delay_ticks, due, cur_delay;
static unsigned delay_from;	/* the delay applies from this doorbell on (1-based; 0 = all of them) */	/* >0: the reply comes this many fake ticks after the doorbell (time based; 0 = after apm_delay polls) */
static uint32_t get_err;	/* reply word 3 of FREQ_GET (error code) */
static int never;		/* the APM never takes the request */
static unsigned never_at;	/* the APM stops taking requests from this doorbell on (1-based; 0 = off) */
static int badseq;		/* replies carry another sequence number */
static unsigned apm_delay, apm_left, apm_busy;
static uint32_t apm_w[4], cur[8];	/* the current rate per domain index */

uint64_t counter_freq(void) { return 24576000ull; }
uint64_t touch_ticks(void) { return now += 200; }

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

static int in_table(uint32_t dom, uint32_t khz)
{
	static const uint32_t cl0[] = { 2196000, 2098000, 2024000, 1950000, 1803000, 1704000, 1598000, 1401000, 1328000, 1197000 };
	static const uint32_t cl1[] = { 2466000, 2393000, 1836000, 1197000 };
	static const uint32_t cl2[] = { 3195000, 1826000, 1106000 };
	static const uint32_t g3d[] = { 848000, 762000, 701000, 572000, 510000, 471000, 400000, 351000, 302000, 251000, 202000, 151000 };	/* run g0b */
	static const uint32_t g3dl2[] = { 996000, 885000, 750000, 603000, 434000, 302000, 151000 };
	const uint32_t *t = dom == 2 ? cl0 : dom == 3 ? cl1 : dom == 5 ? g3d : dom == 6 ? g3dl2 : cl2;
	unsigned n = dom == 2 ? 10 : dom == 3 ? 4 : dom == 5 ? 12 : dom == 6 ? 7 : 3, i;

	for (i = 0; i < n; i++)
		if (t[i] == khz)
			return 1;
	return 0;
}

static void apm_complete(void)
{
	uint32_t rear = S(REQ_REAR_O), sf = S(RSP_FRONT_O), dom = apm_w[0] & 0xffffu, fn = apm_w[2], r0, r1 = 0, r3 = 0;

	apm_busy = 0;
	setS(REQ_REAR_O, (rear + 1) % QLEN);	/* the request is consumed */
	r0 = (apm_w[0] & 0xffff0000u) + (badseq ? (1u << 16) : 0);	/* observed on the phone (run dvfsr1): word 0 = seq << 16, domain bits cleared */
	if (fn == 1) {			/* FREQ_GET */
		nget++;
		r1 = cur[dom & 7];
		r3 = get_err;
	} else {			/* FREQ_REQ */
		nreq++;
		if (!ignore_req && in_table(dom, apm_w[1]))
			cur[dom & 7] = apm_w[1];
		r1 = cur[dom & 7];
	}
	setS(RSP_BASE_O + ESZ * sf + 0, r0);
	setS(RSP_BASE_O + ESZ * sf + 4, r1);
	setS(RSP_BASE_O + ESZ * sf + 8, apm_w[2]);		/* observed: word 2 echoes the request's word 2 (1 for FREQ_GET) */
	setS(RSP_BASE_O + ESZ * sf + 12, r3);
	setS(RSP_FRONT_O, (sf + 1) % QLEN);
}

int try_read32(uintptr_t pa, uint32_t *v)
{
	uint32_t *c = cell(pa);

	if (!c) {
		stray++;
		return 0;
	}
	if (apm_busy && (pa == SRAM + RSP_FRONT_O || pa == SRAM + REQ_REAR_O)) {	/* a poll: the APM makes progress */
		if (cur_delay) {
			if (now >= due)
				apm_complete();
		} else if (apm_left) {
			apm_left--;
		} else {
			apm_complete();
		}
	}
	*v = *c;
	return 1;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	uint32_t *c = cell(pa);

	if (!c) {
		stray++;
		return 0;
	}
	assert(nw < 256);
	wlog[nw].pa = pa;
	wlog[nw++].v = v;
	*c = v;
	if (pa == MBOX + ACPM_INTGR1) {
		uint32_t rear = S(REQ_REAR_O), i;

		nbell++;
		assert(v == 1u << CH && "only this channel's doorbell bit");
		assert(rear != S(REQ_FRONT_O) && "doorbell rung without a request");
		for (i = 0; i < 4; i++)
			apm_w[i] = S(REQ_BASE_O + ESZ * rear + 4 * i);
		if (!never && !(never_at && nbell >= never_at)) {
			apm_busy = 1;
			apm_left = apm_delay;
			cur_delay = (delay_from && nbell < delay_from) ? 0 : delay_ticks;
			due = now + cur_delay;
		}
	}
	return 1;
}

static struct acpm_cfg cfg;

static void setup(uint32_t q_elem_size)
{
	uint32_t i, j, d;

	memset(mbox, 0, sizeof(mbox));
	memset(wlog, 0, sizeof(wlog));
	nw = stray = nbell = nreq = nget = busy_writes = 0;
	now = 1000;
	ignore_req = 0;
	delay_ticks = due = cur_delay = 0;
	delay_from = 0;
	get_err = 0;
	never = badseq = 0;
	never_at = 0;
	apm_delay = 3;
	apm_left = apm_busy = 0;
	for (i = 0; i < SRAMSZ / 4; i++)
		sram[i] = GARB(i);
	cur[2] = 1197000;
	cur[3] = 1197000;
	cur[4] = 1106000;
	cur[5] = cur[6] = 151000;	/* G3D / G3DL2 at the firmware's init level (run g0b) */
	setS(INIT_O + 0x08, TAB_O);
	setS(INIT_O + 0x0C, 3);
	setS(INIT_O + 0x18, 3);		/* ipc_ap_max: channels 0, 1, 2 */
	for (i = 0; i < 3; i++) {
		d = TAB_O + 0x48 * i;
		for (j = 0; j < 18; j++)
			setS(d + 4 * j, 0x11110000u + 0x100 * i + j);
		setS(d + 0x00, i);	/* table index == channel id */
	}
	d = TAB_O + 0x48 * CH;		/* the DVFS channel */
	setS(d + 0x0c, 1);
	setS(d + 0x10, REQ_REAR_O);
	setS(d + 0x14, REQ_FRONT_O);
	setS(d + 0x18, REQ_BASE_O);
	setS(d + 0x28, RSP_REAR_O);
	setS(d + 0x2c, RSP_FRONT_O);
	setS(d + 0x30, RSP_BASE_O);
	setS(d + 0x34, QLEN);
	setS(d + 0x38, q_elem_size);
	setS(d + 0x44, 1);
	setS(REQ_REAR_O, 4);
	setS(REQ_FRONT_O, 4);
	setS(RSP_REAR_O, 2);
	setS(RSP_FRONT_O, 2);
	acpm_cfg_init(&cfg);
	cfg.layout_verified = 1;
	cfg.elem_unit_verified = 1;
}

static struct acpm_trace trace(void)
{
	struct acpm_trace t;

	memset(&t, 0, sizeof(t));
	t.seq = 1;
	return t;
}

static void expect_w(unsigned i, uintptr_t pa, uint32_t v)
{
	if (!(i < nw && wlog[i].pa == pa && wlog[i].v == v))
		fprintf(stderr, "write %u: got pa=%#lx v=%#x, want pa=%#lx v=%#x (nw=%u)\n", i, i < nw ? (unsigned long)wlog[i].pa : 0ul,
			i < nw ? wlog[i].v : 0u, (unsigned long)pa, v, nw);
	assert(i < nw && wlog[i].pa == pa && wlog[i].v == v);
}

/* every write of the whole test must be one of channel 1's: its slot words, its two pointers, its doorbell bit */
static void only_channel_writes(void)
{
	unsigned i;

	for (i = 0; i < nw; i++) {
		uintptr_t pa = wlog[i].pa;
		int ok = (pa >= SRAM + REQ_BASE_O && pa < SRAM + REQ_BASE_O + QLEN * ESZ) || pa == SRAM + REQ_FRONT_O ||
			 pa == SRAM + RSP_REAR_O || pa == MBOX + ACPM_INTGR1 || pa == MBOX + ACPM_INTCR0;

		if (!ok)
			fprintf(stderr, "stray write %u: %#lx\n", i, (unsigned long)pa);
		assert(ok);
	}
}

static void test_config(void)
{
	uint32_t d;

	assert(CH == 1 && ACPM_DVFS_N == 3);
	setup(ESZ);
	assert(cfg.dvfs_ch == 1 && acpm_dvfs_ndom() == 3);
	assert(acpm_dvfs_dom(0, &d) && d == 2 && acpm_dvfs_dom(1, &d) && d == 3 && acpm_dvfs_dom(2, &d) && d == 4 && !acpm_dvfs_dom(3, &d));
	/* ceilings are table rows (also enforced by tools/boards.py); the very first rows are above them */
#if defined(TOUCH_DVFS_WRITE) || defined(GPU_CLK)	/* the tables exist only in images that can send a request (acpm.c); the rw run checks them */
	assert(dvfs_ceil[0] == 1803000 && dvfs_ceil[1] == 1836000 && dvfs_ceil[2] == 1826000);
	assert(dvfs_rate[0][0] == 2196000 && dvfs_rate[1][0] == 2466000 && dvfs_rate[2][0] == 3195000);
	assert(dvfs_nr[0] == 15 && dvfs_nr[1] == 17 && dvfs_nr[2] == 21);
#endif
}

static void test_get(void)
{
	struct acpm_trace t = trace();
	uint32_t khz = 0, slot = REQ_BASE_O + ESZ * 4, i;

	setup(ESZ);
	t = trace();
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_OK);
	assert(khz == 1197000 && t.n == 1 && t.a.err == ACPM_OK && t.a.published == 2 && t.a.had_reply == 1);
	/* Linux: cmd = { id, dbg, FREQ_GET (1), ms }, seq in bits 21:16 of cmd[0] */
	assert(t.a.req[0] == (2u | (1u << 16)) && t.a.req[1] == 0 && t.a.req[2] == 1);
	assert(t.a.rep[0] == (1u << 16) && t.a.rep[1] == 1197000 && t.a.rep[2] == 1 && t.a.rep[3] == 0 && t.a.ret == 0);	/* the reply of run dvfsr1 */
	assert(nw == 8);			/* slot x4, front, doorbell, reply rear, status clear: the same sequence as the PMIC path */
	for (i = 0; i < 4; i++)
		expect_w(i, SRAM + slot + 4 * i, t.a.req[i]);
	expect_w(4, SRAM + REQ_FRONT_O, 0);
	expect_w(5, MBOX + ACPM_INTGR1, 1u << CH);
	expect_w(6, SRAM + RSP_REAR_O, 3);
	expect_w(7, MBOX + ACPM_INTCR0, 1u << CH);
	assert(stray == 0 && nbell == 1 && nget == 1 && nreq == 0);
	/* the other two clusters */
	assert(acpm_dvfs_get(&cfg, 3, &khz, &t) == ACPM_OK && khz == 1197000);
	assert(acpm_dvfs_get(&cfg, 4, &khz, &t) == ACPM_OK && khz == 1106000);
	assert(t.a.req[0] == (4u | (3u << 16)));	/* the third transaction, seq 3 */
	only_channel_writes();
	/* an unknown domain: refused, nothing written */
	setup(ESZ);
	t = trace();
	assert(acpm_dvfs_get(&cfg, 7, &khz, &t) == ACPM_E_ARG && t.a.why == ACPM_W_DOM && nw == 0 && nbell == 0);
	assert(acpm_dvfs_get(&cfg, 0, &khz, &t) == ACPM_E_ARG && nw == 0);
	/* an error code in reply word 3 is a failure, and the channel is drained */
	setup(ESZ);
	t = trace();
	get_err = 7;
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_RET && t.a.why == ACPM_W_DREPLY && t.a.ret == 7);
	assert(S(RSP_REAR_O) == S(RSP_FRONT_O));
	/* another sequence number in the reply */
	setup(ESZ);
	t = trace();
	badseq = 1;
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_REPLY && t.a.why == ACPM_W_SEQ);
	/* wrong element size (descriptor says 64 bytes): refused before any write */
	setup(64);
	t = trace();
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_DESC && nw == 0 && nbell == 0);
	/* a non-empty request queue is never written to */
	setup(ESZ);
	t = trace();
	setS(REQ_FRONT_O, 0);
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_BUSY && nw == 0 && nbell == 0);
	/* no DVFS channel configured */
	setup(ESZ);
	t = trace();
	cfg.dvfs_ch = 0;
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_ARG && nw == 0);
	/* a get that the APM never answers: timeout, the request stays published, nothing rolled back */
	setup(ESZ);
	t = trace();
	never = 1;
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_TIMEOUT && t.a.published == 2 && t.a.state[1] == 0 && nbell == 1);
}

#ifdef TOUCH_DVFS_WRITE
static void test_set(void)
{
	struct acpm_trace t = trace();
	uint32_t khz = 0;

	/* ok, the APM replies to the request */
	setup(ESZ);
	t = trace();
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_OK);
	assert(t.n == 2 && t.a.err == ACPM_OK && t.a.value == 1197000);			/* pre-read */
	assert(t.b.req[0] == (2u | (2u << 16)) && t.b.req[1] == 1803000 && t.b.req[2] == 0);	/* FREQ_REQ = 0, seq 2 */
	assert(t.b.err == ACPM_OK && t.b.published == 2 && t.b.had_reply == 1 && t.b.rep[1] == 1803000);
	assert(cur[2] == 1803000 && cur[3] == 1197000 && cur[4] == 1106000 && nget == 1 && nreq == 1 && nbell == 2 && stray == 0);
	assert(S(REQ_FRONT_O) == S(REQ_REAR_O) && S(RSP_FRONT_O) == S(RSP_REAR_O));
	only_channel_writes();
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_OK && khz == 1803000);	/* the read-back sees it */

	/* ok, a SLOW reply (6 ms of fake time, longer than the 5 ms grace the first version of the client gave up after: review H2): the
	 * client waits for it, drains it, and the channel is clean afterwards */
	setup(ESZ);
	t = trace();
	delay_ticks = 150000;
	delay_from = 2;		/* the request (2nd doorbell), not the pre-read */
	assert(acpm_dvfs_set(&cfg, 4, 1826000, &t) == ACPM_OK);
	assert(t.b.err == ACPM_OK && t.b.had_reply == 1 && cur[4] == 1826000 && nreq == 1);
	assert(S(REQ_FRONT_O) == S(REQ_REAR_O) && S(RSP_FRONT_O) == S(RSP_REAR_O));
	assert(acpm_dvfs_get(&cfg, 4, &khz, &t) == ACPM_OK && khz == 1826000 && t.a.req[0] == (4u | (3u << 16)));
	only_channel_writes();

	/* a reply later than the 50 ms bound: a timeout, request published and consumed or not, nothing rolled back; the next call is
	 * refused (the reply queue is not empty once it lands) and writes nothing */
	setup(ESZ);
	t = trace();
	delay_ticks = 5000000;
	delay_from = 2;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_TIMEOUT);
	assert(t.b.published == 2 && t.b.err == ACPM_E_TIMEOUT && t.b.had_reply == 0);
	assert(S(REQ_FRONT_O) != S(REQ_REAR_O));	/* still pending: the pre-read's reply came in time, the request's did not */

	/* the firmware accepts and answers but keeps the rate: the client reports OK for the request (it did what was asked); the READ-BACK
	 * is what tells (touchprobe.c) */
	setup(ESZ);
	t = trace();
	ignore_req = 1;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_OK && t.b.rep[1] == 1197000 && cur[2] == 1197000);

	/* a firmware that never pushes a reply entry at all would trip the ported queue-full rule after a few requests; the shim then
	 * REFUSES with zero writes (it never overrides the rule). Here: request front wrapped to 0, reply front still 2 => (0 + 2) % 5 == 2. */
	setup(ESZ);
	t = trace();
	setS(REQ_REAR_O, 0);
	setS(REQ_FRONT_O, 0);
	setS(RSP_REAR_O, 2);
	setS(RSP_FRONT_O, 2);
	assert(acpm_dvfs_get(&cfg, 2, &khz, &t) == ACPM_E_BUSY && t.a.why == ACPM_W_FULL && nw == 0 && nbell == 0);

	/* the three clusters in a row, every one at its ceiling */
	setup(ESZ);
	t = trace();
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_OK);
	assert(acpm_dvfs_set(&cfg, 3, 1836000, &t) == ACPM_OK);
	assert(acpm_dvfs_set(&cfg, 4, 1826000, &t) == ACPM_OK);
	assert(cur[2] == 1803000 && cur[3] == 1836000 && cur[4] == 1826000 && nreq == 3);
	only_channel_writes();
}

static void test_set_refusals(void)
{
	struct acpm_trace t = trace();

	/* not a row of the table, above the ceiling (a real row), zero, an unknown domain: refused BEFORE the pre-read, zero writes */
	setup(ESZ);
	t = trace();
	assert(acpm_dvfs_set(&cfg, 2, 1900000, &t) == ACPM_E_ARG && t.a.why == ACPM_W_DOM && nw == 0 && nbell == 0);
	assert(acpm_dvfs_set(&cfg, 2, 1950000, &t) == ACPM_E_ARG && nw == 0 && nbell == 0);	/* a table row, but above 1803000 */
	assert(acpm_dvfs_set(&cfg, 2, 2196000, &t) == ACPM_E_ARG && nw == 0);
	assert(acpm_dvfs_set(&cfg, 3, 2466000, &t) == ACPM_E_ARG && nw == 0);
	assert(acpm_dvfs_set(&cfg, 4, 3195000, &t) == ACPM_E_ARG && nw == 0);
	assert(acpm_dvfs_set(&cfg, 2, 0, &t) == ACPM_E_ARG && nw == 0);
	assert(acpm_dvfs_set(&cfg, 7, 1197000, &t) == ACPM_E_ARG && nw == 0);
	assert(acpm_dvfs_set(&cfg, 3, 1803000, &t) == ACPM_E_ARG && nw == 0);	/* CL0's row asked of CL1 (1803000 is not in CL1's table) */
	assert(acpm_dvfs_set(&cfg, 2, 1826000, &t) == ACPM_E_ARG && nw == 0);	/* CL2's ceiling asked of CL0 */
	assert(nbell == 0 && nreq == 0 && cur[2] == 1197000);
	setup(ESZ);
	t = trace();
	cfg.dvfs_ch = 0;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_ARG && nw == 0);

	/* the pre-read fails (error code in the reply): no FREQ_REQ is sent */
	setup(ESZ);
	t = trace();
	get_err = 5;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_RET && nbell == 1 && nreq == 0 && cur[2] == 1197000);

	/* already at the rate: nothing to ask for */
	setup(ESZ);
	t = trace();
	cur[2] = 1803000;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_ON && t.b.why == ACPM_W_ON && nbell == 1 && nreq == 0);

	/* a non-empty queue: busy, nothing written, not even the pre-read */
	setup(ESZ);
	t = trace();
	setS(RSP_FRONT_O, 3);
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_BUSY && nw == 0 && nbell == 0);

	/* the request is never taken (2nd doorbell = the request, the 1st was the pre-read): a timeout, the request stays published
	 * (nothing rolled back: front is ahead of rear), the pre-read had completed, the rate did not change */
	setup(ESZ);
	t = trace();
	never_at = 2;
	assert(acpm_dvfs_set(&cfg, 2, 1803000, &t) == ACPM_E_TIMEOUT);
	assert(t.a.err == ACPM_OK && t.b.published == 2 && t.b.err == ACPM_E_TIMEOUT && cur[2] == 1197000 && nbell == 2 && nreq == 0);
	assert(S(REQ_FRONT_O) != S(REQ_REAR_O) && t.b.state[1] == S(REQ_FRONT_O));
	only_channel_writes();
}

#endif

int main(void)
{
	acpm_cfg_init(&cfg);
	test_config();
	test_get();
#ifdef TOUCH_DVFS_WRITE
	test_set();
	test_set_refusals();
#endif
	printf("PASS dvfs_host_test\n");
	return 0;
}
