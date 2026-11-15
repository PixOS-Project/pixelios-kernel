/* Host test for acpm.c against a FAKE APM: SRAM image + mailbox + a firmware stand-in that is ASYNCHRONOUS like the real one: the
 * doorbell latches the request, the APM answers only after apm_delay polls of the reply queue (and, mid-transaction, advances rear
 * only at the end). The queue element size is in BYTES (q_elem_size = 16, acpm_ipc.c:762). The fake flags every write to the request
 * slot / front while the APM is busy (busy_writes) and every completion that leaves stale entries pending (replay_hazard): the two
 * ways a rolled-back front would corrupt the real queue. Checks the exact command words of the READ and UPDATE examples of
 * docs/research/23 section 1.1, the full write sequence, wrap-around, the refusal paths (bad descriptor, wrong element size, non-empty
 * queue, queue full, unverified assumptions, bad arguments, unknown / wrong voltage code, rail already on, faulting reads) which must
 * perform ZERO writes, a timeout with a published request (nothing rolled back, queue state recorded), a nonzero return code, a wrong
 * reply, write faults, the generated constants against the cited ones (pins), and that the guard acpm_guard() builds accepts exactly
 * the sanctioned addresses and nothing else. It #includes acpm.c so the statics are reachable and uses the REAL generated board
 * macros. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/acpm_host_test acpm_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "acpm.c"

#if !(SOC_GS101 && defined(ACPM_MBOX_BASE))
#error "needs the bluejay board_config.h (shim/build.sh --board bluejay writes out/board_config.h)"
#endif

#define SRAM	ACPM_SRAM_BASE
#define SRAMSZ	ACPM_SRAM_SIZE
#define MBOX	ACPM_MBOX_BASE
#define MBOXSZ	0x1000u
#define INIT_O	ACPM_INITDATA_OFF
#define TAB_O	0xA100u
#define REQ_REAR_O	0xB000u
#define REQ_FRONT_O	0xB004u
#define RSP_REAR_O	0xB008u
#define RSP_FRONT_O	0xB00Cu
#define REQ_BASE_O	0xC000u
#define RSP_BASE_O	0xD000u
#define QLEN	5u
#define ESZ	16u	/* bytes per queue element: q_elem_size, acpm_ipc.c:762 */
#define GARB(i)	(0xdead0000u + (i))

/* ---- fake hardware ---- */
static uint32_t sram[SRAMSZ / 4];
static uint32_t mbox[MBOXSZ / 4];
static uint32_t pmic[256];
static int fail_upd, ignore_upd, bump_out1, fail_r16;	/* gpurailon_host_test: UPDATE answers with an error (not applied) / is acknowledged but not applied / also changes register 0x16 */
static struct { uintptr_t pa; uint32_t v; } wlog[256];
static unsigned nw, stray, nbell;
static uintptr_t bad_rpa, bad_wpa;	/* a read of / the next write to this address faults */
static int wfaults;			/* faults left for bad_wpa */
static int poll_fault, time_frozen;
static uint64_t now, fake_freq;
/* M_OK answers after apm_delay polls; M_NEVER never completes (the request stays pending); M_NOREPLY takes the request but sends no reply */
enum { M_OK, M_NEVER, M_NOREPLY, M_RET, M_BADSEQ } mode;
static unsigned fail_rd_n, rd_seen;	/* pmicsrc_host_test: the fail_rd_n-th FUNC_READ (1-based) answers with an APM RETURN error, 0 = none */
/* pmicsrc_clear_host_test: FUNC_WRITE (fn 1, cmd[1] WRITE_VAL [15:8]) is applied to pmic[reg] and logged as (w0 without the seq, w1); the fail_wr_n-th write answers with an APM RETURN error and is
 * not applied; ignore_wr acknowledges every write without applying it (a latch that does not clear). */
static struct { uint32_t w0, w1; } wrlog[16];
static unsigned nwr, fail_wr_n, nfn;
static char fnseq[64];	/* the FUNC of every request the APM completed, in order: 'R' read, 'W' write, 'U' update */
static int ignore_wr;
static unsigned apm_delay, apm_left, apm_busy;
static unsigned busy_writes, replay_hazard;	/* the fake's verdicts: writes the real APM would be hurt by */
static uint32_t apm_w[4], apm_slot_o;

uint64_t counter_freq(void) { return fake_freq; }
uint64_t touch_ticks(void) { return time_frozen ? now : (now += 200); }

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

static void apm_complete(void)	/* the APM finishes its transaction: only now does rear move (like the real firmware) */
{
	uint32_t rear = S(REQ_REAR_O), sf = S(RSP_FRONT_O), reg, val, mask, fn, r0, r1;

	apm_busy = 0;
	setS(REQ_REAR_O, (rear + 1) % QLEN);	/* consumed */
	if (S(REQ_FRONT_O) != S(REQ_REAR_O))	/* entries left pending = stale slots it would now replay */
		replay_hazard++;
	if (mode == M_NOREPLY)
		return;
	reg = apm_w[0] & 0xff;
	fn = apm_w[1] & 0xff;
	val = (apm_w[1] >> 8) & 0xff;
	mask = (apm_w[1] >> 16) & 0xff;
	r0 = apm_w[0] + (mode == M_BADSEQ ? (1u << 16) : 0);
	if (fn == 2 && !fail_upd && !ignore_upd)
		pmic[reg] = (pmic[reg] & ~mask) | (val & mask);
	if (nfn < sizeof(fnseq) - 1)
		fnseq[nfn++] = fn == 0 ? 'R' : fn == 1 ? 'W' : fn == 2 ? 'U' : '?';
	if (fn == 1) {
		assert(nwr < 16);
		wrlog[nwr].w0 = apm_w[0] & 0xffffu;
		wrlog[nwr].w1 = apm_w[1];
		nwr++;
		if (!ignore_wr && !(fail_wr_n && nwr == fail_wr_n))
			pmic[reg] = val;
	}
	if (fn == 2 && bump_out1)
		pmic[0x16]++;
	if (fn == 0)
		rd_seen++;
	r1 = (fn == 0 ? pmic[reg] << 8 : 0) | (mode == M_RET || (fn == 0 && fail_rd_n && rd_seen == fail_rd_n) || (fn == 2 && fail_upd) || (fn == 1 && fail_wr_n && nwr == fail_wr_n) || (fn == 0 && reg == 0x16 && fail_r16) ? 2u << 24 : 0);
	setS(RSP_BASE_O + ESZ * sf + 0, r0);
	setS(RSP_BASE_O + ESZ * sf + 4, r1);
	setS(RSP_FRONT_O, (sf + 1) % QLEN);
	mbox[ACPM_INTSR0 / 4] |= 1u << ACPM_CH_ID;
}

static void apm_flush(void) { if (apm_busy) apm_complete(); }	/* "some time later" */

int try_read32(uintptr_t pa, uint32_t *v)
{
	uint32_t *c = cell(pa);

	if (!c) {
		stray++;
		return 0;
	}
	if (pa == bad_rpa)
		return 0;
	if (pa == SRAM + RSP_FRONT_O && apm_busy) {	/* a poll: the APM makes progress between the AP's reads */
		if (apm_left)
			apm_left--;
		else
			apm_complete();
	}
	*v = *c;
	return 1;
}

static void apm_doorbell(void)	/* the doorbell was rung: the APM reads front, latches the slot at rear, starts working */
{
	uint32_t rear = S(REQ_REAR_O), front = S(REQ_FRONT_O), i;

	assert(rear != front && "doorbell rung without a request");
	if (poll_fault)
		bad_rpa = SRAM + RSP_FRONT_O;	/* the reply queue becomes unreadable once the doorbell rang */
	apm_slot_o = REQ_BASE_O + ESZ * rear;
	for (i = 0; i < 4; i++)
		apm_w[i] = S(apm_slot_o + 4 * i);
	if (mode == M_NEVER)
		return;
	apm_busy = 1;
	apm_left = apm_delay;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	uint32_t *c = cell(pa);

	if (!c) {
		stray++;
		return 0;
	}
	if (pa == bad_wpa && wfaults > 0) {
		wfaults--;
		return 0;
	}
	if (apm_busy && (pa == SRAM + REQ_FRONT_O || (pa >= SRAM + apm_slot_o && pa < SRAM + apm_slot_o + ESZ)))
		busy_writes++;	/* the APM is working on that request: nothing may touch the slot or the front now */
	assert(nw < 256);
	wlog[nw].pa = pa;
	wlog[nw++].v = v;
	*c = v;
	if (pa == MBOX + ACPM_INTGR1) {
		nbell++;
		assert(v == 1u << ACPM_CH_ID);
		apm_doorbell();
	}
	return 1;
}

/* ---- fixtures ---- */
static struct acpm_cfg cfg;

static void setup(uint32_t q_elem_size)	/* the descriptor's q_elem_size as the APM firmware publishes it (bytes) */
{
	uint32_t i, ids[3] = { 0, 1, ACPM_CH_ID }, d;

	memset(mbox, 0, sizeof(mbox));
	memset(wlog, 0, sizeof(wlog));
	nw = stray = nbell = 0;
	fail_upd = ignore_upd = bump_out1 = fail_r16 = 0;
	fail_rd_n = rd_seen = 0;
	nwr = fail_wr_n = nfn = 0;
	memset(fnseq, 0, sizeof(fnseq));
	ignore_wr = 0;
	bad_rpa = bad_wpa = 0;
	wfaults = poll_fault = time_frozen = 0;
	mode = M_OK;
	now = 1000;
	fake_freq = 24576000ull;
	apm_delay = 3;
	apm_left = apm_busy = busy_writes = replay_hazard = 0;
	for (i = 0; i < SRAMSZ / 4; i++)
		sram[i] = GARB(i);
	for (i = 0; i < 256; i++)
		pmic[i] = 0;
	pmic[0x5C] = 0x23;	/* off, vsel 0x23 */
	pmic[0x5D] = 0x31;	/* off, vsel 0x31 */
	setS(INIT_O + 0x08, TAB_O);
	setS(INIT_O + 0x0C, 3);
	setS(INIT_O + 0x18, 3);
	for (i = 0; i < 3; i++) {
		uint32_t j;

		d = TAB_O + 0x48 * i;
		for (j = 0; j < 18; j++)
			setS(d + 4 * j, 0x11110000u + 0x100 * i + j);
		setS(d + 0x00, ids[i]);
	}
	d = TAB_O + 0x48 * ACPM_CH_ID;	/* the PMIC channel: table index == channel id (Linux indexes channel[id]) */
	setS(d + 0x0c, 1);		/* TYPE_QUEUE */
	setS(d + 0x10, REQ_REAR_O);
	setS(d + 0x14, REQ_FRONT_O);
	setS(d + 0x18, REQ_BASE_O);
	setS(d + 0x28, RSP_REAR_O);
	setS(d + 0x2c, RSP_FRONT_O);
	setS(d + 0x30, RSP_BASE_O);
	setS(d + 0x34, QLEN);
	setS(d + 0x38, q_elem_size);
	setS(d + 0x44, 1);
	setS(REQ_REAR_O, 4);	/* front 4 -> the request wraps to slot 0 ... */
	setS(REQ_FRONT_O, 4);
	setS(RSP_REAR_O, 2);
	setS(RSP_FRONT_O, 2);
	acpm_cfg_init(&cfg);
	cfg.layout_verified = 1;
	cfg.elem_unit_verified = 1;
	cfg.rail[0].vsel_known = cfg.rail[1].vsel_known = 1;
	cfg.rail[0].vsel_min = 0x10;
	cfg.rail[0].vsel_max = 0x30;
	cfg.rail[1].vsel_min = 0x20;
	cfg.rail[1].vsel_max = 0x3f;
}

static uint32_t desc_off(void) { return TAB_O + 0x48 * ACPM_CH_ID; }

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

/* ---- the examples of docs/research/23 section 1.1 ---- */
static void test_words_and_sequence(void)
{
	struct acpm_trace t = trace();
	uint32_t v = 0, i, slot = REQ_BASE_O + ESZ * 4;

	setup(ESZ);
	/* READ reg 0x5C, speedy 0, seq 1: cmd[0] = 0x0100 | 0x5C | seq << 16 = 0x0001015C, cmd[1] = 0 */
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_OK);
	assert(v == 0x23 && t.n == 1);
	assert(t.a.req[0] == 0x0001015Cu && t.a.req[1] == 0 && t.a.req[2] == 0 && t.a.req[3] == 0);
	assert(t.a.rep[0] == 0x0001015Cu && t.a.rep[1] == 0x2300u && t.a.ret == 0 && t.a.err == ACPM_OK);
	assert(t.a.t_end > t.a.t_bell && t.a.n == nw && t.a.published == 2);
	/* the whole write sequence, in order: slot words, front, doorbell, reply rear := reply front, status clear */
	assert(nw == 8);
	for (i = 0; i < 4; i++)
		expect_w(i, SRAM + slot + 4 * i, i == 0 ? 0x0001015Cu : 0u);	/* slot 4 (front was 4), 16 bytes at a 16-byte stride */
	expect_w(4, SRAM + REQ_FRONT_O, 0);		/* (4 + 1) % 5 wraps to 0 */
	expect_w(5, MBOX + ACPM_INTGR1, 1u << 2);
	expect_w(6, SRAM + RSP_REAR_O, 3);
	expect_w(7, MBOX + ACPM_INTCR0, 1u << 2);
	assert(S(REQ_FRONT_O) == 0 && S(REQ_REAR_O) == 0 && S(RSP_REAR_O) == 3 && S(RSP_FRONT_O) == 3);
	assert(stray == 0 && nbell == 1 && busy_writes == 0 && replay_hazard == 0);

	/* UPDATE reg 0x5C bit 7 := 1, seq 2 (pre-read seq 1): cmd[1] = 2 | 0x80 << 8 | 0x80 << 16 = 0x00808002 */
	t = trace();
	setup(ESZ);
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_OK);
	assert(t.n == 2 && t.a.req[1] == 0 && t.a.req[0] == 0x0001015Cu && t.a.value == 0x23);
	assert(t.b.req[0] == 0x0002015Cu && t.b.req[1] == 0x00808002u && t.b.err == ACPM_OK && t.b.ret == 0);
	assert(pmic[0x5C] == 0xA3 && pmic[0x5D] == 0x31);	/* only bit 7 of 0x5C changed */
	assert(nw == 16 && stray == 0 && nbell == 2 && busy_writes == 0 && replay_hazard == 0);
	/* reg 0x5D: cmd[0] = 0x0100 | 0x5D | seq << 16 */
	t = trace();
	t.seq = 3;
	assert(acpm_pmic_update(&cfg, 0x5D, 0x80, 0x80, &t) == ACPM_OK);
	assert(t.b.req[0] == 0x0004015Du && t.b.req[1] == 0x00808002u && pmic[0x5D] == 0xB1);
	assert(t.seq == 5);
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5D, &v, &t) == ACPM_OK && v == 0xB1 && (v & 0x80));
	assert(stray == 0 && busy_writes == 0 && replay_hazard == 0);
	/* a slow APM (many polls) is waited for, within the bound */
	setup(ESZ);
	apm_delay = 2000;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_OK && v == 0x23 && replay_hazard == 0 && busy_writes == 0);
}

/* ---- refusals: nothing may be written ---- */
static void refused(const char *what, int err, uint32_t why, struct acpm_trace *t)
{
	if (t->a.err != err || t->a.why != why || nw != 0)
		fprintf(stderr, "refusal '%s': err=%d (want %d) why=%u (want %u) writes=%u\n", what, t->a.err, err, why, t->a.why, nw);
	assert(t->a.err == err && t->a.why == why);
	assert(nw == 0 && nbell == 0 && "a refusal performed a write");
	assert(stray == 0);
}

static void rd_refused(const char *what, int err, uint32_t why)
{
	struct acpm_trace t = trace();
	uint32_t v = 0x77;
	int e = acpm_pmic_read(&cfg, 0x5C, &v, &t);

	assert(e == err && v == 0x77);
	refused(what, err, why, &t);
}

static void test_refusals(void)
{
	struct acpm_trace t;
	uint32_t d;

	setup(ESZ);
	d = desc_off();
	setS(d, 8);					/* the PMIC channel id vanished */
	setS(TAB_O, 9);
	setS(TAB_O + 0x48, 10);
	rd_refused("no PMIC channel", ACPM_E_DESC, ACPM_W_NOCH);
	setup(ESZ); setS(INIT_O + 0x18, 0);	rd_refused("ipc_ap_max 0", ACPM_E_DESC, ACPM_W_INITN);
	setup(ESZ); setS(INIT_O + 0x18, 33);	rd_refused("ipc_ap_max 33", ACPM_E_DESC, ACPM_W_INITN);
	setup(ESZ); setS(INIT_O + 0x08, SRAMSZ - 0x40); rd_refused("table outside SRAM", ACPM_E_DESC, ACPM_W_TAB);
	setup(ESZ); setS(INIT_O + 0x08, TAB_O + 2); rd_refused("table unaligned", ACPM_E_DESC, ACPM_W_TAB);
	/* the PMIC descriptor is not at index == channel id (Linux indexes channel[id]) */
	setup(ESZ); setS(TAB_O + 0x48, ACPM_CH_ID); setS(d, 1); rd_refused("descriptor index != channel id", ACPM_E_DESC, ACPM_W_CHIDX);
	setup(ESZ); setS(d + 0x0c, 2); rd_refused("descriptor type TYPE_BUFFER", ACPM_E_DESC, ACPM_W_TYPE);
	setup(ESZ); setS(d + 0x0c, 0); rd_refused("descriptor type 0", ACPM_E_DESC, ACPM_W_TYPE);
	setup(ESZ); setS(d + 0x10, SRAMSZ); rd_refused("rear pointer outside SRAM", ACPM_E_DESC, ACPM_W_PTR);
	setup(ESZ); setS(d + 0x14, REQ_FRONT_O + 2); rd_refused("front pointer unaligned", ACPM_E_DESC, ACPM_W_PTR);
	setup(ESZ); setS(d + 0x28, 0xffffffffu); rd_refused("reply rear garbage", ACPM_E_DESC, ACPM_W_PTR);
	setup(ESZ); setS(d + 0x34, 2); rd_refused("q_len 2", ACPM_E_DESC, ACPM_W_QLEN);
	setup(ESZ); setS(d + 0x34, 300); rd_refused("q_len 300", ACPM_E_DESC, ACPM_W_QLEN);
	/* WRONG ELEMENT SIZE: q_elem_size is bytes and only 16 (4 words, the MFD command) is spoken. 64 is what elem_unit 4 made of a real 16. */
	setup(0);  rd_refused("q_elem_size 0", ACPM_E_DESC, ACPM_W_ESZ);
	setup(3);  rd_refused("q_elem_size 3", ACPM_E_DESC, ACPM_W_ESZ);
	setup(4);  rd_refused("q_elem_size 4 (words value of the old assumption)", ACPM_E_DESC, ACPM_W_ESZ);
	setup(12); rd_refused("q_elem_size 12", ACPM_E_DESC, ACPM_W_ESZ);
	setup(17); rd_refused("q_elem_size 17", ACPM_E_DESC, ACPM_W_ESZ);
	setup(20); rd_refused("q_elem_size 20", ACPM_E_DESC, ACPM_W_ESZ);
	setup(32); rd_refused("q_elem_size 32", ACPM_E_DESC, ACPM_W_ESZ);
	setup(64); rd_refused("q_elem_size 64", ACPM_E_DESC, ACPM_W_ESZ);
	setup(0x40000004u); cfg.elem_unit = 4; rd_refused("q_elem_size * 4 wraps to 16", ACPM_E_DESC, ACPM_W_ESZ);
	setup(ESZ); cfg.elem_unit = 4; rd_refused("elem_unit 4 on a real 16 = 64 bytes (the old shipped config)", ACPM_E_DESC, ACPM_W_ESZ);
	setup(ESZ); cfg.elem_unit = 0; rd_refused("element unit 0", ACPM_E_DESC, ACPM_W_ESZ);
	setup(ESZ); cfg.elem_unit = 5; rd_refused("element unit 5", ACPM_E_DESC, ACPM_W_ESZ);
	setup(4); cfg.elem_unit = 4; { struct acpm_trace t4 = trace(); uint32_t v4 = 0; /* the other consistent reading: 4 words = 16 bytes */
		assert(acpm_pmic_read(&cfg, 0x5C, &v4, &t4) == ACPM_OK && v4 == 0x23); }
	setup(ESZ); setS(d + 0x18, SRAMSZ - 16); rd_refused("queue buffer outside SRAM", ACPM_E_DESC, ACPM_W_QBUF);
	setup(ESZ); setS(d + 0x30, REQ_BASE_O + 16); rd_refused("queue buffers overlap", ACPM_E_DESC, ACPM_W_OVERLAP);
	setup(ESZ); setS(d + 0x14, REQ_BASE_O + 4); rd_refused("front pointer inside the request buffer", ACPM_E_DESC, ACPM_W_OVERLAP);
	setup(ESZ); setS(d + 0x28, REQ_FRONT_O); rd_refused("two pointers alias", ACPM_E_DESC, ACPM_W_OVERLAP);
	setup(ESZ); setS(REQ_FRONT_O, 5); rd_refused("front index >= q_len", ACPM_E_DESC, ACPM_W_IDX);
	setup(ESZ); setS(RSP_REAR_O, 9); rd_refused("reply rear index >= q_len", ACPM_E_DESC, ACPM_W_IDX);
	setup(ESZ); setS(REQ_FRONT_O, 0); rd_refused("request queue not empty", ACPM_E_BUSY, ACPM_W_BUSY);
	setup(ESZ); setS(RSP_FRONT_O, 3); rd_refused("reply queue not empty (stale reply)", ACPM_E_BUSY, ACPM_W_BUSY);
	setup(ESZ); setS(REQ_REAR_O, 1); setS(REQ_FRONT_O, 1); setS(RSP_REAR_O, 3); setS(RSP_FRONT_O, 3);
	rd_refused("queue full condition (front + 2 == reply front)", ACPM_E_BUSY, ACPM_W_FULL);
	setup(ESZ); cfg.layout_verified = 0; rd_refused("layout not verified", ACPM_E_UNSURE, ACPM_W_LAYOUT);
	setup(ESZ); cfg.elem_unit_verified = 0; rd_refused("element unit not verified", ACPM_E_UNSURE, ACPM_W_UNIT);
	setup(ESZ); fake_freq = 0; rd_refused("counter frequency 0", ACPM_E_UNSURE, ACPM_W_TIMER);
	setup(ESZ); cfg.ch_id = 32; rd_refused("channel id >= 32", ACPM_E_ARG, ACPM_W_ARG);
	setup(ESZ); bad_rpa = SRAM + INIT_O + 0x18; rd_refused("INIT read faults", ACPM_E_FAULT, ACPM_W_NONE);
	setup(ESZ); bad_rpa = SRAM + REQ_FRONT_O; rd_refused("queue pointer read faults", ACPM_E_FAULT, ACPM_W_NONE);
	setup(ESZ); bad_rpa = MBOX + ACPM_INTSR0; rd_refused("INTSR0 read faults", ACPM_E_FAULT, ACPM_W_NONE);

	/* arguments: only the two rail registers, only bit 7 set */
	setup(ESZ);
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x00, &d, &t) == ACPM_E_ARG);
	refused("read of another register", ACPM_E_ARG, ACPM_W_ARG, &t);
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x00, 0x80, 0x80, &t) == ACPM_E_ARG);
	refused("update of another register", ACPM_E_ARG, ACPM_W_ARG, &t);
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x00, 0x80, &t) == ACPM_E_ARG);
	refused("clearing bit 7", ACPM_E_ARG, ACPM_W_ARG, &t);
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0xff, &t) == ACPM_E_ARG);
	refused("mask 0xff", ACPM_E_ARG, ACPM_W_ARG, &t);
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x81, 0x81, &t) == ACPM_E_ARG);
	refused("another bit", ACPM_E_ARG, ACPM_W_ARG, &t);
	/* unknown voltage code range: not even the pre-read (no write at all) */
	cfg.rail[0].vsel_known = 0;
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_E_UNSURE);
	refused("rail vsel range unknown", ACPM_E_UNSURE, ACPM_W_VSEL, &t);
	assert(pmic[0x5C] == 0x23);
	assert(acpm_unsure(ACPM_W_LAYOUT) && acpm_unsure(ACPM_W_UNIT) && acpm_unsure(ACPM_W_VSEL) && !acpm_unsure(ACPM_W_BUSY));
}

/* the update itself is refused after its pre-read: the READ wrote, the UPDATE did not */
static void test_update_gates(void)
{
	struct acpm_trace t = trace();

	setup(ESZ);
	pmic[0x5C] = 0x05;		/* below vsel_min 0x10 */
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_E_RAIL);
	assert(t.n == 2 && t.a.err == ACPM_OK && t.b.err == ACPM_E_RAIL && t.b.why == ACPM_W_VSELOUT && t.b.n == 0);
	assert(pmic[0x5C] == 0x05 && nbell == 1);
	assert(nw == 8);		/* exactly the pre-read's writes */
	setup(ESZ);
	pmic[0x5C] = 0x31;		/* above vsel_max 0x30 */
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_E_RAIL && pmic[0x5C] == 0x31);
	setup(ESZ);
	pmic[0x5C] = 0xA3;		/* already on */
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_E_ON && t.b.why == ACPM_W_ON && t.b.n == 0);
	assert(pmic[0x5C] == 0xA3 && nbell == 1 && nw == 8);
	setup(ESZ);			/* the pre-read itself fails: no update */
	mode = M_RET;
	t = trace();
	assert(acpm_pmic_update(&cfg, 0x5C, 0x80, 0x80, &t) == ACPM_E_RET && t.n == 1 && nbell == 1 && pmic[0x5C] == 0x23);
}

/* ---- failures after the first write ---- */
static unsigned front_writes(void)
{
	unsigned i, n = 0;

	for (i = 0; i < nw; i++)
		n += wlog[i].pa == SRAM + REQ_FRONT_O;
	return n;
}

static void test_failures(void)
{
	struct acpm_trace t = trace();
	uint32_t v = 0;
	uint64_t lim = 24576000ull * 50000 / 1000000;

	/* The APM never answers: bounded by 50 ms. The request is published, so NOTHING is rolled back: one front write ever, the
	 * queue left as the APM sees it, its state recorded for the human. */
	setup(ESZ);
	mode = M_NEVER;
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_TIMEOUT);
	assert(t.a.err == ACPM_E_TIMEOUT && t.a.published == 2);
	assert(t.a.t_end - t.a.t_bell >= lim && t.a.t_end - t.a.t_bell < lim + 2000);	/* 50 ms +- a poll */
	assert(S(REQ_FRONT_O) == 0 && S(REQ_REAR_O) == 4 && S(RSP_REAR_O) == 2 && S(RSP_FRONT_O) == 2);	/* front stays published */
	assert(t.a.state[0] == 4 && t.a.state[1] == 0 && t.a.state[2] == 2 && t.a.state[3] == 2);
	assert(nw == 6 && front_writes() == 1 && stray == 0 && nbell == 1);	/* 4 slot words, front, bell: no write after the bell */
	expect_w(4, SRAM + REQ_FRONT_O, 0);
	expect_w(5, MBOX + ACPM_INTGR1, 4);
	/* the same, then the channel is refused until a human looks (queue not empty), with zero writes */
	nw = nbell = 0;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_BUSY && nw == 0 && nbell == 0);

	/* THE RACE: the APM is slow (mid-transaction when the AP gives up) and finishes later. A rolled-back front would make it see
	 * q_len - 1 stale commands (replay_hazard); a write to the slot / front while it works is busy_writes. Both must stay 0. */
	setup(ESZ);
	apm_delay = 1000000000u;
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_TIMEOUT && t.a.published == 2);
	assert(apm_busy && S(REQ_REAR_O) == 4 && S(REQ_FRONT_O) == 0);	/* rear unmoved: "the APM did not take it" would be the wrong guess */
	assert(nw == 6 && front_writes() == 1 && busy_writes == 0);
	apm_flush();					/* the APM finishes the request it owns */
	assert(replay_hazard == 0 && S(REQ_REAR_O) == 0 && S(REQ_FRONT_O) == 0 && S(RSP_FRONT_O) == 3 && pmic[0x5C] == 0x23);
	assert(nw == 6 && busy_writes == 0);
	/* ... and the late reply is a stale entry: the next run refuses (BUSY) instead of trusting it */
	nw = nbell = 0;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_BUSY && nw == 0 && nbell == 0);

	/* the APM took the request but never answered: leave the channel to it */
	setup(ESZ);
	mode = M_NOREPLY;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_TIMEOUT && t.a.published == 2);
	assert(nw == 6 && S(REQ_FRONT_O) == 0 && S(REQ_REAR_O) == 0 && stray == 0 && busy_writes == 0 && replay_hazard == 0);
	assert(t.a.state[0] == 0 && t.a.state[1] == 0);
	/* nonzero RETURN field: reported, channel drained */
	setup(ESZ);
	mode = M_RET;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_RET && t.a.ret == 2 && v == 0);
	assert(S(RSP_REAR_O) == S(RSP_FRONT_O) && S(RSP_REAR_O) == 3 && nw == 8);
	/* reply for another sequence number: reported, drained */
	setup(ESZ);
	mode = M_BADSEQ;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_REPLY && t.a.why == ACPM_W_SEQ);
	assert(S(RSP_REAR_O) == S(RSP_FRONT_O) && nw == 8);
	/* a write to the request slot faults once: nothing was published (the APM never sees the slot), no front write, no doorbell */
	setup(ESZ);
	bad_wpa = SRAM + REQ_BASE_O + ESZ * 4 + 8;
	wfaults = 1;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_FAULT && t.a.published == 0);
	assert(S(REQ_FRONT_O) == 4 && S(REQ_REAR_O) == 4 && nbell == 0 && stray == 0 && front_writes() == 0 && nw == 2);
	/* the front write faults once: it may or may not have landed, so no further write of any kind (no undo of the slot either) */
	setup(ESZ);
	bad_wpa = SRAM + REQ_FRONT_O;
	wfaults = 1;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_FAULT && t.a.published == 1 && nbell == 0);
	assert(nw == 4 && front_writes() == 0 && t.a.state[1] == 4);	/* only the 4 slot words, then silence */
	/* the doorbell faults: the front stays published, nothing is put back */
	setup(ESZ);
	bad_wpa = MBOX + ACPM_INTGR1;
	wfaults = 1;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_FAULT && t.a.published == 1 && nbell == 0);
	assert(S(REQ_FRONT_O) == 0 && nw == 5 && front_writes() == 1 && t.a.state[1] == 0);
	/* the reply queue becomes unreadable after the doorbell: reported with the unreadable pointer marked, front untouched */
	setup(ESZ);
	mode = M_NEVER;
	poll_fault = 1;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_FAULT && t.a.published == 2 && stray == 0);
	assert(S(REQ_FRONT_O) == 0 && nw == 6 && t.a.state[3] == ~0u && t.a.state[1] == 0);
	/* a dead timer: the poll budget still ends the loop, and still nothing is rolled back */
	setup(ESZ);
	mode = M_NEVER;
	time_frozen = 1;
	t = trace();
	assert(acpm_pmic_read(&cfg, 0x5C, &v, &t) == ACPM_E_TIMEOUT && S(REQ_FRONT_O) == 0 && nw == 6);
}

/* ---- the constants the generator emitted are the cited ones (an edit of soc.json or board_config.h alone is caught) ---- */
static void test_pins(void)
{
	struct acpm_cfg c;

	acpm_cfg_init(&c);
	assert(c.mbox_base == 0x17610000UL && c.sram_base == 0x02039000UL && c.sram_size == 0x40000u && c.initdata_off == 0xA000u);	/* gs101.dtsi:496-499 */
	assert(c.ch_id == 2 && c.speedy == 0 && c.pmic_addr == 1);			/* gs101-pmic.dtsi:38, s2mpg10-core.c:30,58 */
	assert(c.intgr1 == 0x40 && c.intcr0 == 0x24 && c.intsr0 == 0x2c);		/* acpm_ipc.h:106-116 */
	assert(c.desc_stride == 0x48 && c.elem_unit == 1);			/* framework.h:97-133; acpm_ipc.c:762 bytes */
	assert(c.layout_verified == 1 && c.elem_unit_verified == 1);		/* flipped after run acpm3 dump (observed 2026-10-22) */
	assert(c.rail[0].reg == 0x5C && c.rail[1].reg == 0x5D);			/* gk_s2mpg10-register.h:108-109 */
	assert(c.rail[0].vsel_known && c.rail[0].vsel_min == 0x24 && c.rail[0].vsel_max == 0x32);	/* L25M 1.60-1.95 V: 700 mV + 25 mV * code */
	assert(c.rail[1].vsel_known && c.rail[1].vsel_min == 0x1C && c.rail[1].vsel_max == 0x3C);	/* L26M 2.5-3.3 V: 1800 mV + 25 mV * code */
	assert(1600000 == 700000 + 25000 * 0x24 && 1950000 == 700000 + 25000 * 0x32);
	assert(2500000 == 1800000 + 25000 * 0x1C && 3300000 == 1800000 + 25000 * 0x3C);
}

/* ---- the guard of one transaction: built by acpm_guard() and checked against independently computed addresses ---- */
static void test_guard(void)
{
	static const uint32_t vals[] = { 0, 1, 4, 2, 3, 5, 0x80, 0x00808002u, 0xffffffffu };
	struct acpm_chan h;
	struct wguard g;
	uintptr_t slot, pa, want_slot = SRAM + REQ_BASE_O + 16 * 4;
	unsigned stage, k, n[3] = { 0, 0, 0 };

	setup(ESZ);
	assert(acpm_describe(&cfg, &h) == ACPM_OK);
	slot = h.req_base_pa + 16 * h.rf;
	acpm_guard(&g, &cfg, &h, slot, ACPM_ELEM_BYTES);
	assert(g.armed && g.slot_ok && g.front_ok && !g.rear_ok);
	assert(g.slot_lo == want_slot && g.slot_hi == want_slot + 16);			/* exactly one 16-byte slot */
	assert(g.front_pa == SRAM + REQ_FRONT_O && g.front_new == 0);
	assert(g.rear_pa == SRAM + RSP_REAR_O);
	assert(g.bell_pa == 0x17610000UL + 0x40 && g.intcr_pa == 0x17610000UL + 0x24 && g.bit == 4);	/* the cited constants, not the macros */
	for (stage = 0; stage < 3; stage++) {
		if (stage == 1)			/* the front write was attempted: the request is published */
			g.slot_ok = g.front_ok = 0;
		if (stage == 2) {		/* a reply was read */
			g.rear_ok = 1;
			g.rear_val = 3;
		}
		for (pa = SRAM - 0x100; pa < MBOX + MBOXSZ + 0x100; pa++) {
			if (pa == SRAM + SRAMSZ + 0x100)
				pa = MBOX - 0x100;
			for (k = 0; k < sizeof(vals) / sizeof(vals[0]); k++) {
				uint32_t v = vals[k];
				int slot_word = stage == 0 && pa >= want_slot && pa < want_slot + 16 && !(pa & 3);
				int want = slot_word || (stage == 0 && pa == SRAM + REQ_FRONT_O && v == 0) ||
					   (stage == 2 && pa == SRAM + RSP_REAR_O && v == 3) ||
					   (pa == MBOX + 0x40 && v == 4) || (stage == 2 && pa == MBOX + 0x24 && v == 4);
				if (w32_allowed(pa, v, &g) != want)
					fprintf(stderr, "stage %u: pa=%#lx v=%#x allowed=%d want %d\n", stage, (unsigned long)pa, v,
						w32_allowed(pa, v, &g), want);
				assert(w32_allowed(pa, v, &g) == want);
				n[stage] += want;
			}
		}
	}
	assert(n[0] == 4 * 9 + 1 + 1 && n[1] == 1 && n[2] == 1 + 1 + 1);	/* slot words + front + bell; bell; rear + bell + status clear */
	g.armed = 0;
	assert(!w32_allowed(g.bell_pa, 4, &g) && !w32_allowed(g.slot_lo, 1, &g));
	nw = stray = 0;
	g.armed = 1;
	assert(w32(g.front_pa + 4, 0, &g) == -2 && w32(0, 0, &g) == -2 && nw == 0 && stray == 0);	/* refused before any write */
}

static void test_describe_and_text(void)
{
	struct acpm_chan h;
	uint32_t w, v;

	setup(ESZ);
	assert(acpm_describe(&cfg, &h) == ACPM_OK && h.why == ACPM_W_NONE && h.have_desc);
	assert(h.n == 3 && h.tab == TAB_O && h.idx == 2 && h.qlen == QLEN && h.esz_w == 4 && h.esz_b == 16 && h.ap_poll == 1);
	assert(h.req_rear_pa == SRAM + REQ_REAR_O && h.rsp_base_pa == SRAM + RSP_BASE_O && h.rr == 4 && h.sf == 2);
	assert(h.d[0] == ACPM_CH_ID && h.d[3] == 1 && h.init[2] == TAB_O && h.init[6] == 3); 
	assert(nw == 0 && stray == 0);	/* describe only reads */
	assert(acpm_qword(&cfg, &h, 0, 0, &v) && v == GARB(REQ_BASE_O / 4) && acpm_qword(&cfg, &h, 1, 1, &v) && v == GARB(RSP_BASE_O / 4 + 1));
	assert(!acpm_qword(&cfg, &h, 0, SRAMSZ / 4, &v));	/* outside the SRAM window */
	/* a refused describe still has the raw descriptor and can still dump the buffers */
	setup(64);
	assert(acpm_describe(&cfg, &h) == ACPM_E_DESC && h.why == ACPM_W_ESZ && h.have_desc && h.d[14] == 64 && h.esz_b == 64 && h.qlen == QLEN);
	assert(acpm_qword(&cfg, &h, 0, 0, &v) && v == GARB(REQ_BASE_O / 4));
	/* review 0363 finding 7: acpm_describe is the 16-byte-only client: a 32-byte channel (the SLC element size) is refused here */
	setup(32);
	assert(acpm_describe(&cfg, &h) == ACPM_E_DESC && h.why == ACPM_W_ESZ && h.esz_b == 32);
	setup(ESZ);
	setS(desc_off() + 0x18, SRAMSZ + 4);	/* a base outside SRAM: no dump */
	assert(acpm_describe(&cfg, &h) == ACPM_E_DESC && !acpm_qword(&cfg, &h, 0, 0, &v));
	setup(ESZ);
	setS(INIT_O + 0x18, 0);
	assert(acpm_describe(&cfg, &h) == ACPM_E_DESC && !h.have_desc && !acpm_qword(&cfg, &h, 0, 0, &v));
	assert(nw == 0);
	for (w = 0; w < ACPM_W_MAX; w++)
		assert(acpm_why(w)[0] && strcmp(acpm_why(w), "?") != 0);
}

int main(void)
{
	test_words_and_sequence();
	test_refusals();
	test_update_gates();
	test_failures();
	test_pins();
	test_guard();
	test_describe_and_text();
	printf("PASS acpm host test\n");
	return 0;
}
