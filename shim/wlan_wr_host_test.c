/* Host replay + fault-injection test of the stage-3 chip WRITER in xnu patch 0129 (sweet_wlan_wr.h, extracted from the patch by build.sh), docs/research/43.
 * The fake bus holds the values observed on the Pixel 6a (EP Command 0x00100002, BAR2 0x60400004, cfg 0x88 0x00080080, ELBI 0x0300a911, ATU r0/r1 of
 * run wd2-2, SRPWR 0x00030300, SYS_MEM coreinfo 0x115, wrapper IOCTL 0 / RESET_CTL 1 / RESET_ST 0); bank sizes and the two BAR2 words are chosen by the test.
 * Every guard rule of swl_wr() has a case that fails when the rule is removed: tools/test_wlan_wr_mutants.py removes each one from the header and requires this
 * test to fail (CR-02). Nothing here touches hardware. */
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdint.h>

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

static char logbuf[1 << 16];
static unsigned loglen;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf)
		loglen += (unsigned)n;
	return n;
}
#define printf cap_printf
#include "sweet_wlan_chip.h"
#include "sweet_wlan_wr.h"
#undef printf

#define RAM_WORDS (0x600000u / 4u)

struct wrec { int space; uint32_t win, off, val; };
struct wrap { uint32_t ioctl, ctl, st; };
struct fake {
	uint32_t cmd, bar0lo, bar0hi, bar2lo, bar2hi, win0, win2, c88, iso, ltssm, a0[7], a1[7], a2[7];
	uint32_t after1, ioctl_noise, srpwr, coreinfo, bankidx, bankinfo[SWW_BANK_ROM], bar2_ram, bar2_0, ones_off, early;
	struct wrap sm, ca;
	int has_r2, nowincheck, map_fail, ignore_writes, ones_space, ones_on, st_stuck_sm, st_stuck_ca, win_stuck;
	unsigned nrd, nw, nmap, fail_rd_at, drop_at, fail_wr_at, corrupt_at;
	unsigned long clock_us, bankidx_t, assert_t[2];
	struct wrec w[512];
	uint32_t ones_win;
	unsigned win2_stuck;                            /* run B: from this store number on, writes to cfg 0x84 are ignored (0 = never) */
	uint32_t *ram;                                  /* run B: backplane 0..0x5FFFFC as words (BAR2 offset + window = backplane address); NULL = the two words of run A */
};

static uint32_t *f_wrap(struct fake *f, uint32_t win)
{
	return win == SWW_WIN_SMWRAP ? &f->sm.ioctl : win == SWW_WIN_CA7WRAP ? &f->ca.ioctl : 0;
}

static int f_rd(void *c, int space, uint32_t win, uint32_t off, uint32_t *v)
{
	struct fake *f = c;
	uint32_t *wp;

	f->nrd++;
	if (f->fail_rd_at && f->nrd == f->fail_rd_at)
		return -1;
	if (f->ones_on && f->ones_space == space && f->ones_win == win && f->ones_off == off) {
		*v = 0xFFFFFFFFu;
		return 0;
	}
	switch (space) {
	case SWW_CFG:
		*v = off == 0x04 ? f->cmd : off == 0x10 ? f->bar0lo : off == 0x14 ? f->bar0hi : off == 0x18 ? f->bar2lo : off == 0x1C ? f->bar2hi :
		     off == 0x80 ? f->win0 : off == 0x84 ? f->win2 : off == 0x88 ? f->c88 : 0xDEAD0000u;
		return (off == 0x04 || off == 0x10 || off == 0x14 || off == 0x18 || off == 0x1C || off == 0x80 || off == 0x84 || off == 0x88) ? 0 : -1;
	case SWW_ATU:
		if (off < 0x20) { *v = f->a0[off / 4]; return 0; }
		if (off >= 0x200 && off < 0x220) { *v = f->a1[(off - 0x200) / 4]; return 0; }
		if (off >= SWW_R2(0) && off < SWW_R2(0x20)) { *v = f->a2[(off - SWW_R2(0)) / 4]; return 0; }
		return -1;
	case SWW_ELBI:
		*v = f->ltssm;
		return off == 0x2C8 ? 0 : -1;
	case SWW_ISO:
		*v = f->iso;
		return off == 0 ? 0 : -1;
	case SWW_BAR0:
		if (!f->nowincheck && f->win0 != win)
			return -1;
		if (f->win0 == SWW_WIN_PCIE && off == SWW_SRPWR) { *v = f->srpwr; return 0; }
		if (f->win0 == SWW_WIN_SYSMEM) {
			if (off == SWW_SM_COREINFO) { *v = f->coreinfo; return 0; }
			if (off == SWW_SM_BANKIDX) { *v = f->bankidx; return 0; }
			if (off == SWW_SM_BANKINFO) {
				if (f->clock_us - f->bankidx_t < 10)
					f->early++;
				*v = f->bankinfo[f->bankidx & 0x7F];
				return 0;
			}
			return -1;
		}
		wp = f_wrap(f, f->win0);
		if (wp && off == SWL_AGENT_IOCTL) { *v = wp[0]; return 0; }
		if (wp && off == SWL_AGENT_RESET_CTL) { *v = wp[1]; return 0; }
		if (wp && off == SWL_AGENT_RESET_ST) { *v = wp[2]; return 0; }
		return -1;
	case SWW_BAR2:
		if (f->win2 != win)
			return -1;
		if (f->ram) {
			if ((win + off) / 4 >= RAM_WORDS)
				return -1;
			*v = f->ram[(win + off) / 4];
			return 0;
		}
		if (off == SWW_RAM_BASE) { *v = f->bar2_ram; return 0; }
		if (off == 0) { *v = f->bar2_0; return 0; }
		return -1;
	}
	return -1;
}

static int f_wr(void *c, int space, uint32_t win, uint32_t off, uint32_t v)
{
	struct fake *f = c;
	uint32_t *wp;

	if (f->nw < sizeof f->w / sizeof f->w[0]) {
		f->w[f->nw].space = space; f->w[f->nw].win = win; f->w[f->nw].off = off; f->w[f->nw].val = v;
	}
	f->nw++;
	if (f->fail_wr_at == f->nw)
		return -1;
	if (f->ignore_writes || f->drop_at == f->nw)
		return 0;
	if (f->corrupt_at == f->nw)
		v ^= 1u;
	if (space == SWW_CFG && off == SWW_CFG_WIN) { if (!f->win_stuck) f->win0 = v; return 0; }
	if (space == SWW_CFG && off == SWW_CFG_WIN2) { if (!f->win2_stuck || f->nw < f->win2_stuck) f->win2 = v; return 0; }
	if (space == SWW_BAR2 && f->ram) {
		if (f->win2 != win || (win + off) / 4 >= RAM_WORDS)
			return -1;
		f->ram[(win + off) / 4] = v;
		return 0;
	}
	if (space == SWW_ATU && off >= SWW_R2(0) && off < SWW_R2(0x20)) {
		if (f->has_r2)
			f->a2[(off - SWW_R2(0)) / 4] = v;
		return 0;
	}
	if (space == SWW_BAR0 && f->win0 == SWW_WIN_SYSMEM && off == SWW_SM_BANKIDX) {
		f->bankidx = v;
		f->bankidx_t = f->clock_us;
		return 0;
	}
	wp = space == SWW_BAR0 ? f_wrap(f, f->win0) : 0;
	if (wp && off == SWL_AGENT_IOCTL) {
		if ((wp[1] & 1u) && f->clock_us - f->assert_t[f->win0 == SWW_WIN_CA7WRAP] < 10)      /* aiutils.c:1263 OSL_DELAY(10) after the assert */
			f->early++;
		wp[0] = v | f->ioctl_noise;
	}
	if (wp && off == SWL_AGENT_RESET_CTL) {
		if (v == 1)
			f->assert_t[f->win0 == SWW_WIN_CA7WRAP] = f->clock_us;
		wp[1] = v;
		if (v == 1 && ((f->win0 == SWW_WIN_SMWRAP && f->st_stuck_sm) || (f->win0 == SWW_WIN_CA7WRAP && f->st_stuck_ca)))
			wp[2] = 1;
	}
	return 0;
}

static int f_map2(void *c)
{
	struct fake *f = c;

	f->nmap++;
	return f->map_fail;
}

static void f_delay(void *c, unsigned us)
{
	struct fake *f = c;
	uint32_t *wp = f_wrap(f, f->win0);

	f->clock_us += us;
	if (us == 1 && wp && f->after1) {              /* the 1 us after the last IOCTL write (aiutils.c:1320): the wrapper changes behind the sequence's back */
		if (f->after1 == 1) wp[2] = 1;
		else if (f->after1 == 2) wp[1] |= 1;
		else wp[0] ^= 4;
	}
}

/* the observed entry state of run A (stage 1 at level 2 leaves cfg 0x80 on the last D11 wrapper window) */
static void obs(struct fake *f)
{
	static const uint32_t a0[7] = { 0x4, 0x80000000, 0x60ffe000, 0, 0x60ffffff, 0x01000000, 0 };
	static const uint32_t a1[7] = { 0, 0x80000000, 0x60000000, 0, 0x6000ffff, 0x60000000, 0 };
	unsigned i;

	memset(f, 0, sizeof *f);
	f->cmd = 0x00100002; f->bar0lo = 0x60000004; f->bar2lo = SWW_BAR2_CFG; f->win0 = 0x18123000; f->c88 = 0x00080080;
	f->iso = 1; f->ltssm = 0x0300a911;
	memcpy(f->a0, a0, sizeof a0);
	memcpy(f->a1, a1, sizeof a1);
	f->has_r2 = 1;
	f->srpwr = 0x00030300;
	f->sm.ctl = 1; f->ca.ctl = 1;
	f->coreinfo = 0x115;
	for (i = 0; i < SWW_BANK_ROM; i++)
		f->bankinfo[i] = 0x0f000380u | 0x0fu;     /* upper bits as bank 0 read 0x0f00039f in run w3a-n36; low 7 bits 0x0f = 16 x 8 KiB = 128 KiB per bank: 21 banks = 0x2A0000 */
}

static void bus(struct swl_wbus *b, struct fake *f)
{
	b->rd = f_rd; b->wr = f_wr; b->map2 = f_map2; b->delay_us = f_delay; b->ctx = f;
}

static void mkx(struct swl_wx *x, struct swl_wbus *b, int level)
{
	memset(x, 0, sizeof *x);
	x->b = b;
	x->level = level;
}

static int run(struct fake *f, struct swl_wx *x, int level)
{
	static struct swl_wbus b;

	bus(&b, f);
	mkx(x, &b, level);
	loglen = 0; logbuf[0] = 0;
	return swl_stage3(x);
}

struct exp { int space; uint32_t win, off, val; int grp; };
static const struct exp want[] = {
	{ SWW_ATU, 0, SWW_R2(8), 0x60400000, 0 }, { SWW_ATU, 0, SWW_R2(0x10), 0x607FFFFF, 0 }, { SWW_ATU, 0, SWW_R2(0x14), 0x60400000, 0 }, { SWW_ATU, 0, SWW_R2(4), 0x80000000, SWW_RG_ATU2 },
	{ SWW_CFG, 0, 0x80, 0x18001000, 0 }, { SWW_CFG, 0, 0x80, 0x18126000, 0 },
	{ SWW_BAR0, 0x18126000, 0x800, 1, SWW_RG_SM }, { SWW_BAR0, 0x18126000, 0x408, 3, SWW_RG_SM }, { SWW_BAR0, 0x18126000, 0x800, 0, SWW_RG_SM }, { SWW_BAR0, 0x18126000, 0x408, 1, SWW_RG_SM },
	{ SWW_CFG, 0, 0x80, 0x18026000, 0 },
#define B(n) { SWW_BAR0, 0x18026000, 0x10, n, 0 }
	B(8), B(9), B(10), B(11), B(12), B(13), B(14), B(15), B(16), B(17), B(18), B(19), B(20), B(21), B(22), B(23), B(24), B(25), B(26), B(27), B(28),
#undef B
	{ SWW_CFG, 0, 0x80, 0x18120000, 0 },
	{ SWW_BAR0, 0x18120000, 0x800, 1, SWW_RG_CA }, { SWW_BAR0, 0x18120000, 0x408, 0x23, SWW_RG_CA }, { SWW_BAR0, 0x18120000, 0x800, 0, SWW_RG_CA }, { SWW_BAR0, 0x18120000, 0x408, 0x21, SWW_RG_CA },
};
#define NWANT ((unsigned)(sizeof want / sizeof want[0]))

static int is_restore(const struct wrec *w)
{
	unsigned i;

	for (i = 0; i < sizeof swl_rs / sizeof swl_rs[0]; i++) {
		if (w->space == swl_rs[i].space && w->win == swl_rs[i].win && w->off == swl_rs[i].off && w->val == swl_rs[i].val)
			return 1;
		if (w->space == SWW_CFG && w->off == SWW_CFG_WIN && swl_rs[i].space == SWW_BAR0 && w->val == swl_rs[i].win)
			return 1;
	}
	return 0;
}

/* the invariant of the whole writer: every store that reached the bus is a tuple or a restore row */
static int all_allowed(const struct fake *f)
{
	unsigned i;

	for (i = 0; i < f->nw && i < sizeof f->w / sizeof f->w[0]; i++)
		if (swl_wr_ok(f->w[i].space, f->w[i].win, f->w[i].off, f->w[i].val) < 0 && !is_restore(&f->w[i]))
			return 0;
	return 1;
}

static int only_restore_after(const struct fake *f, unsigned from)
{
	unsigned i;

	for (i = from; i < f->nw; i++)
		if (!is_restore(&f->w[i]))
			return 0;
	return 1;
}

/* a state with the given phase flags; once region 2 is part of the preconditions (SWF_W2) the fake holds it as W2 leaves it */
static void ready(struct swl_wx *x, struct fake *f, uint32_t flags)
{
	x->flags = flags;
	if (flags & SWF_W2) {
		f->a2[0] = 0; f->a2[1] = SWW_ATU_ENABLE; f->a2[2] = SWW_TCM_PA; f->a2[3] = 0; f->a2[4] = SWW_TCM_PA + (SWW_TCM_BYTES - 1u); f->a2[5] = SWW_TCM_PA; f->a2[6] = 0;
	}
}

#define F_ATU (SWF_W0 | SWF_W1)
#define F_ALL 0x1FFu

static void units(struct fake *f, unsigned total)         /* bank sizes summing to total x 8 KiB over the 21 banks, each 1..128 */
{
	unsigned i, left = total;

	for (i = 8; i < 8 + SWW_NB; i++) {
		unsigned u = left / (8 + SWW_NB - i);

		if (u < 1) u = 1;
		if (u > 128) u = 128;
		f->bankinfo[i] = 0x0f000380u | (u - 1);
		left -= u;
	}
}

static void happy(void)
{
	struct fake f;
	struct swl_wx x;
	unsigned i;

	obs(&f);
	CHECK(run(&f, &x, 3) == 0);
	CHECK(x.stopped == 0 && x.restore == SWW_RS_NONE && x.nbank == 21 && x.ram_bytes == 0x2A0000u && x.flags == 0x1FFu);
	CHECK(f.nw == NWANT && f.nw == x.nstore);
	for (i = 0; i < NWANT && i < f.nw; i++)
		CHECK(f.w[i].space == want[i].space && f.w[i].win == want[i].win && f.w[i].off == want[i].off && f.w[i].val == want[i].val);
	CHECK(all_allowed(&f));
	CHECK(f.nmap == 1 && f.early == 0);
	CHECK(f.a2[1] == 0x80000000u && f.a2[2] == 0x60400000u && f.a2[4] == 0x607FFFFFu && f.a2[5] == 0x60400000u && f.a2[0] == 0 && f.a2[3] == 0 && f.a2[6] == 0);
	CHECK(f.sm.ioctl == 1 && f.sm.ctl == 0 && f.ca.ioctl == 0x21 && f.ca.ctl == 0 && f.win0 == 0x18120000u && f.win2 == 0);
	CHECK(strstr(logbuf, "SWEET WLAN: stage 3 level 3 done ram_end=0x4a0000 banks=21\n") != 0);
	for (i = 0; i <= 7; i++) {
		char t[16];

		snprintf(t, sizeof t, "SWEET WLAN: W%u ", i);
		CHECK(strstr(logbuf, t) != 0);
	}
	CHECK(strstr(logbuf, "W3 SRPWR powerctl 0x30300") != 0 && strstr(logbuf, "W5 SYS_MEM coreinfo 0x115 nrb 8 nb 21") != 0 && strstr(logbuf, "W5 bank 28 bankinfo 0xf00038f size 0x20000") != 0);
	CHECK(strstr(logbuf, "W7 BAR2 [0x200000] 0 [0] 0") != 0);
	/* level 2 and level 1: nothing at all (not one read of the stage-3 tables, not one write) */
	for (i = 0; i < 3; i++) {
		obs(&f);
		CHECK(run(&f, &x, (int)i) != 0 && f.nw == 0 && f.nrd == 0 && x.stopped == 0);
	}
	obs(&f);
	CHECK(run(&f, &x, 2) != 0 && f.nw == 0 && f.nrd == 0);
}

static void fault_sweeps(void)
{
	struct fake f, g;
	struct swl_wx x;
	unsigned j, k, nrd;

	obs(&g);
	run(&g, &x, 3);
	nrd = g.nrd;
	CHECK(nrd > 100);
	/* a read that fails at any position stops the run, and only table rows ever reach the bus */
	for (k = 1; k <= nrd; k++) {
		obs(&f);
		f.fail_rd_at = k;
		CHECK(run(&f, &x, 3) < 0);
		CHECK(x.stopped == 1 && all_allowed(&f) && f.nw < NWANT + 4);
	}
	/* a write the chip drops / corrupts / the bus refuses, at every position: STOP with the read-back (or store) code, and the restore of the armed group only */
	for (j = 1; j <= NWANT; j++) {
		unsigned mode;

		for (mode = 0; mode < 3; mode++) {
			unsigned a;

			obs(&f);
			if (mode == 0) f.drop_at = j; else if (mode == 1) f.corrupt_at = j; else f.fail_wr_at = j;
			if ((j == 7 || j == 34) && mode == 0) {         /* a dropped RESET_CTL := 1 on a wrapper that already is in reset is not observable (idempotent) */
				CHECK(run(&f, &x, 3) == 0 && f.nw == NWANT);
				continue;
			}
			CHECK(run(&f, &x, 3) < 0);
			CHECK(x.stopped == 1 && x.stop_code == (mode == 2 ? SWW_E_WR : SWW_E_READBACK));
			CHECK(all_allowed(&f) && f.nw >= j);
			CHECK(only_restore_after(&f, j));
			a = want[j - 1].grp;
			if (a == 0) {
				CHECK(f.nw == j && x.restore == SWW_RS_NONE);
			} else {
				const struct wrec *last = &f.w[f.nw - 1];

				CHECK(f.nw > j && x.restore == SWW_RS_OK);
				CHECK(is_restore(last) && (a == SWW_RG_ATU2 ? (last->space == SWW_ATU && last->off == SWW_R2(4) && last->val == 0) :
				      (last->space == SWW_BAR0 && last->off == 0x800 && last->val == 1 && last->win == (a == SWW_RG_SM ? 0x18126000u : 0x18120000u))));
				CHECK(strstr(logbuf, "stopped at W") != 0 && strstr(logbuf, "restore=ok") != 0);
			}
		}
	}
	/* a chip that takes no write at all stops at the very first one */
	obs(&f);
	f.ignore_writes = 1;
	CHECK(run(&f, &x, 3) < 0 && f.nw == 1 && x.stop_code == SWW_E_READBACK && strstr(logbuf, "restore=none") != 0);
}

static void deny_sweep(void)
{
	struct fake f;
	struct swl_wx x;
	struct swl_wbus b;
	unsigned i, bit;
	int bound = 0;

	for (i = 0; i < SWT_N_A; i++) {
		const struct swl_wt *p = &swl_wt[i];

		CHECK(p->run == 1 && p->row >= 2 && p->row <= 6 && p->space >= SWW_CFG && p->space <= SWW_BAR0);
		if (p->flags & SWT_BANK) {
			unsigned v;

			bound++;
			for (v = 0; v < 0x200; v++)
				CHECK((swl_wr_ok(p->space, p->win, p->off, v) == (int)i) == (v < 0x80));
			continue;
		}
		CHECK(swl_wr_ok(p->space, p->win, p->off, p->val) == (int)i);
		for (bit = 0; bit < 32; bit++) {
			unsigned q, other = 0;

			for (q = 0; q < SWT_N_A; q++)       /* the one other value a register may take (RESET_CTL 1 and 0, IOCTL 3 and 1) */
				other |= swl_wt[q].space == p->space && swl_wt[q].win == p->win && swl_wt[q].off == p->off && swl_wt[q].val == (p->val ^ (1u << bit));
			CHECK((swl_wr_ok(p->space, p->win, p->off, p->val ^ (1u << bit)) >= 0) == (other != 0));
		}
		CHECK(swl_wr_ok(p->space, p->win, p->off ^ 4u, p->val) < 0 && swl_wr_ok(p->space, p->win, p->off + 0x400u, p->val) < 0);
		CHECK(swl_wr_ok(p->space, p->win ^ 0x1000u, p->off, p->val) < 0 && swl_wr_ok(p->space, p->win + 1u, p->off, p->val) < 0);
		CHECK(swl_wr_ok(p->space == SWW_BAR0 ? SWW_CFG : SWW_BAR0, p->win, p->off, p->val) < 0 || p->space == SWW_CFG);
		CHECK(swl_wr_ok(SWW_BAR2, p->win, p->off, p->val) < 0 && swl_wr_ok(SWW_ELBI, p->win, p->off, p->val) < 0 && swl_wr_ok(SWW_ISO, p->win, p->off, p->val) < 0 && swl_wr_ok(0, p->win, p->off, p->val) < 0);
	}
	CHECK(bound == 1);
	/* the things that must never be writable, by name (docs/research/43 s.3 "Never") */
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x04, 0x6) < 0 && swl_wr_ok(SWW_CFG, 0, 0x04, 0x2) < 0 && swl_wr_ok(SWW_CFG, 0, 0x70, 0) < 0 && swl_wr_ok(SWW_CFG, 0, SWW_CFG_WIN2, 0x200000) < 0 &&
	      swl_wr_ok(SWW_CFG, 0, SWW_CFG_WIN2, 0x400001) < 0 && swl_wr_ok(SWW_CFG, 0, 0x88, 0x00080380) < 0 && swl_wr_ok(SWW_CFG, 0, 0x10, 0) < 0 && swl_wr_ok(SWW_CFG, 0, 0x18, 0) < 0);
	CHECK(swl_wr_ok(SWW_ATU, 0, 0x208, 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x210, 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x0, 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0), 0) < 0 &&
	      swl_wr_ok(SWW_ATU, 0, SWW_R2(0x0C), 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0x18), 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0x20), 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x600, 0x80000000) < 0);
	CHECK(swl_wr_ok(SWW_BAR0, 0x18001000, SWW_SRPWR, 0x200) < 0 && swl_wr_ok(SWW_BAR0, 0x18001000, SWW_SRPWR, 0x00030300) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x400, 1) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18126000, 0x404, 1) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x808, 1) < 0 && swl_wr_ok(SWW_BAR0, 0x18120000, 0x80C, 1) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18120000, 0x408, 0x03) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x408, 0x23) < 0 && swl_wr_ok(SWW_BAR0, 0x18121000, 0x800, 1) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18000000, 0x600, 0) < 0 && swl_wr_ok(SWW_BAR0, 0x18012000, 0x600, 0) < 0 && swl_wr_ok(SWW_BAR2, 0, 0x1FFFFC, 0) < 0 && swl_wr_ok(SWW_BAR2, 0, 0, 0) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18026000, 0x40, 0) < 0 && swl_wr_ok(SWW_BAR0, 0x18026000, 0, 0) < 0);
	/* a store that matches no row never reaches the bus and latches STOPPED */
	obs(&f);
	bus(&b, &f);
	mkx(&x, &b, 3);
	ready(&x, &f, F_ALL);
	CHECK(swl_wr(&x, SWW_BAR0, 0x18001000, SWW_SRPWR, 0x00030700) == -SWW_E_DENIED && x.stopped && f.nw == 0);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_STOPPED && f.nw == 0);
	/* the read side is deny-by-default too (not one read of an unlisted register reaches the bus) */
	obs(&f);
	mkx(&x, &b, 3);
	{
		uint32_t v = 0;

		CHECK(swl_rdx(&x, SWW_BAR0, 0x18001000, 0x1EC, &v) < 0 && swl_rdx(&x, SWW_BAR0, 0x18001000, 0x1E9, &v) < 0 && swl_rdx(&x, SWW_BAR0, 0x18000000, 0x1E8, &v) < 0 &&
		      swl_rdx(&x, SWW_BAR0, 0x18126000, 0x400, &v) < 0 && swl_rdx(&x, SWW_BAR2, 0, 4, &v) < 0 && swl_rdx(&x, SWW_BAR2, 0x400000, 0, &v) < 0 && swl_rdx(&x, SWW_CFG, 0, 0x70, &v) < 0 &&
		      swl_rdx(&x, SWW_ATU, 0, 0x300, &v) < 0 && swl_rdx(&x, SWW_ATU, 0, SWW_R2(0x1C), &v) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0x2C4, &v) < 0 && swl_rdx(&x, SWW_CFG, 0, 0x08, &v) < 0 &&
		      swl_rdx(&x, SWW_BAR0, 0x18001000, 0x1E4, &v) < 0 && swl_rdx(&x, SWW_ATU, 0, 0x2, &v) < 0 && swl_rdx(&x, SWW_CFG, 0, 0x11, &v) < 0 && swl_rdx(&x, SWW_CFG, 0x18001000, 0x1E8, &v) < 0 &&
		      swl_rdx(&x, SWW_ATU, 0, 0x1E8, &v) < 0 && swl_rdx(&x, SWW_BAR0, 0x18001000, 0x04, &v) < 0 && swl_rdx(&x, SWW_BAR2, 0, 0x1000, &v) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0x2C8 + 4, &v) < 0);
		CHECK(f.nrd == 0);
		CHECK(swl_rdx(&x, SWW_BAR2, 0, SWW_RAM_BASE, &v) == 0 && swl_rdx(&x, SWW_BAR2, 0, 0, &v) == 0 && f.nrd == 2);
		mkx(&x, &b, 2);
		CHECK(swl_rdx(&x, SWW_BAR2, 0, 0, &v) < 0 && f.nrd == 2);
	}
}

static void gates(void)
{
	struct fake f;
	struct swl_wx x;
	struct swl_wbus b;
	unsigned i, bit;

	/* level gate: below level 3 not one store, even with every other gate open */
	obs(&f); bus(&b, &f); mkx(&x, &b, 2); ready(&x, &f, F_ATU);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_LEVEL && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == 0 && f.nw == 1);
	/* each tuple is consumed once */
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_REPEAT && f.nw == 1 && x.stopped);
	/* predecessor order */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(0x10), SWW_TCM_PA + SWW_TCM_BYTES - 1) == -SWW_E_ORDER && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == -SWW_E_ORDER && f.nw == 0 && f.a2[1] == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x408, 3) == -SWW_E_ORDER && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_CA7WRAP;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18120000, 0x408, 0x21) == -SWW_E_ORDER && f.nw == 0);
	/* phase flags: the store of a step whose predecessor step did not finish is refused */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, SWF_W0);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PHASE && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, SWF_W1);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PHASE && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, SWF_W0 | SWF_W1 | SWF_W2);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == -SWW_E_PHASE && f.nw == 0);                    /* needs the BAR2 mapping too */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL & ~SWF_W3); f.win0 = SWW_WIN_SMWRAP;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x800, 1) == -SWW_E_PHASE && f.nw == 0);                    /* SRPWR not proven */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL & ~SWF_W4); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_PHASE && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL & ~SWF_W5); f.win0 = SWW_WIN_CA7WRAP;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18120000, 0x800, 1) == -SWW_E_PHASE && f.nw == 0);
	/* the bank rule: state and value */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_BANK && f.nw == 0);                      /* nrb/nb never validated */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 7; x.nb = 21;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 7) == -SWW_E_BANK && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 22;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_BANK && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 9) == -SWW_E_BANK && f.nw == 0);                      /* out of ascending order */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == 0 && swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_BANK && f.nw == 1);   /* a bank twice */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21; x.nbank = 21;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 29) == -SWW_E_BANK && f.nw == 0);                     /* a 22nd bank */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 0x7F; x.nb = 21; x.nbank = 1;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 0x80) == -SWW_E_DENIED && f.nw == 0);                 /* index >= 0x80 is a ROM bank */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21; f.coreinfo = 0x114;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_COMP && f.nw == 0);                      /* the live coreinfo is checked at the write */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SYSMEM; x.nrb = 8; x.nb = 21; f.coreinfo = 0x0F5;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18026000, 0x10, 8) == -SWW_E_COMP && f.nw == 0);
	/* window: the live BAR0 window must be the tuple's, even when the bus itself does not check */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_CA7WRAP; f.nowincheck = 1;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x800, 1) == -SWW_E_WIN && f.nw == 0 && f.sm.ctl == 1 && f.ca.ctl == 1);
	/* dead read */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.ones_on = 1; f.ones_space = SWW_ATU; f.ones_win = 0; f.ones_off = SWW_R2(8);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_DEAD && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; f.ones_on = 1; f.ones_space = SWW_BAR0; f.ones_win = SWW_WIN_SMWRAP; f.ones_off = 0x800;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x800, 1) == -SWW_E_DEAD && f.nw == 0);
	/* old-value conditions: region 2 already enabled; a window select from an unknown window; the final IOCTL only from the middle value */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.a2[1] = 0x80000000u; x.done = (1u << SWT_ATU_BASE) | (1u << SWT_ATU_LIMIT) | (1u << SWT_ATU_TARGET);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == -SWW_E_OLD && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = 0x12345000;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == -SWW_E_OLD && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; f.sm.ioctl = 0; f.sm.ctl = 0;
	x.done = (1u << SWT_SM_ASSERT) | (1u << SWT_SM_IOCTL_MID) | (1u << SWT_SM_RELEASE);
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x408, 1) == -SWW_E_OLD && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_CA7WRAP; f.ca.ioctl = 0x03; f.ca.ctl = 0;
	x.done = (1u << SWT_CA_ASSERT) | (1u << SWT_CA_IOCTL_MID) | (1u << SWT_CA_RELEASE);
	CHECK(swl_wr(&x, SWW_BAR0, 0x18120000, 0x408, 0x21) == -SWW_E_OLD && f.nw == 0);
	/* companion conditions: the IOCTL middle value only while in reset, the release only after the middle value, the final value only after the release */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; f.sm.ctl = 0;
	x.done = 1u << SWT_SM_ASSERT;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x408, 3) == -SWW_E_COMP && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; f.sm.ioctl = 0x1;
	x.done = (1u << SWT_SM_ASSERT) | (1u << SWT_SM_IOCTL_MID);
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x800, 0) == -SWW_E_COMP && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; f.sm.ioctl = 0x3; f.sm.ctl = 1;
	x.done = (1u << SWT_SM_ASSERT) | (1u << SWT_SM_IOCTL_MID) | (1u << SWT_SM_RELEASE);
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x408, 1) == -SWW_E_COMP && f.nw == 0);
	/* the preconditions are re-read at every store: one flipped word in each, ends of the word */
	for (i = 0; i < 7; i++) {
		for (bit = 0; bit < 32; bit += 31) {
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.a0[i] ^= 1u << bit;
			CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.a1[i] ^= 1u << bit;
			CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		}
	}
	{
		static const uint32_t cmd_bad[] = { 0x00100006, 0x00100402, 0x00100000, 0x00100003, 0x00100102, 0x00000000 };
		static const uint32_t ltssm_bad[] = { 0x0300a900, 0x0300a910, 0x0300a912, 0x0300a931, 0 };

		for (i = 0; i < sizeof cmd_bad / sizeof cmd_bad[0]; i++) {
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.cmd = cmd_bad[i];
			CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		}
		for (i = 0; i < sizeof ltssm_bad / sizeof ltssm_bad[0]; i++) {
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.ltssm = ltssm_bad[i];
			CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		}
		obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.bar0lo = 0x60000000;
		CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.bar0hi = 1;
		CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.iso = 0;
		CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_PRE && f.nw == 0);
		obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.cmd = 0xFFFF0002;      /* Status half is not part of the check */
		CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == 0 && f.nw == 1);
		/* once region 2 is enabled its words are part of the precondition */
		for (i = 0; i < 7; i++) {
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL);
			CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == 0 && f.nw == 1);
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.a2[i] ^= 0x10u;
			CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == -SWW_E_PRE && f.nw == 0);
		}
	}
	/* a bus that takes no store / refuses it */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.ignore_writes = 1;
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_READBACK && f.nw == 1 && x.restore == SWW_RS_NONE);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.fail_wr_at = 1;
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(8), SWW_TCM_PA) == -SWW_E_WR && f.nw == 1);
	/* a consumed tuple is not written twice */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL); f.win0 = SWW_WIN_SMWRAP; x.done = 1u << SWT_SM_ASSERT;
	CHECK(swl_wr(&x, SWW_BAR0, 0x18126000, 0x800, 1) == -SWW_E_REPEAT && x.stopped && f.nw == 0);
	/* the order inside region 2: base, limit, target, CR2 last */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); x.done = 1u << SWT_ATU_BASE;
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(0x14), SWW_TCM_PA) == -SWW_E_ORDER && f.nw == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); x.done = 1u << SWT_ATU_BASE;
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == -SWW_E_ORDER && f.nw == 0 && f.a2[1] == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); x.done = (1u << SWT_ATU_BASE) | (1u << SWT_ATU_LIMIT);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == -SWW_E_ORDER && f.nw == 0 && f.a2[1] == 0);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); x.done = (1u << SWT_ATU_BASE) | (1u << SWT_ATU_LIMIT) | (1u << SWT_ATU_TARGET);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == 0 && f.nw == 1 && f.a2[1] == SWW_ATU_ENABLE && x.armed == SWW_RG_ATU2);
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ATU); f.a2[1] = 0x40000000u; x.done = (1u << SWT_ATU_BASE) | (1u << SWT_ATU_LIMIT) | (1u << SWT_ATU_TARGET);
	CHECK(swl_wr(&x, SWW_ATU, 0, SWW_R2(4), SWW_ATU_ENABLE) == -SWW_E_OLD && f.nw == 0);
	/* each BAR0 window select is gated by the step before it */
	{
		static const struct { uint32_t win, miss; } ws[3] = { { SWW_WIN_SMWRAP, SWF_W3 }, { SWW_WIN_SYSMEM, SWF_W4 }, { SWW_WIN_CA7WRAP, SWF_W5 } };
		unsigned k;

		for (k = 0; k < 3; k++) {
			obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL & ~ws[k].miss);
			CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, ws[k].win) == -SWW_E_PHASE && f.nw == 0);
		}
	}
	/* a window select is reusable and does not arm a restore */
	obs(&f); mkx(&x, &b, 3); ready(&x, &f, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == 0 && swl_wr(&x, SWW_CFG, 0, 0x80, SWW_WIN_PCIE) == 0 && f.nw == 2 && x.armed == 0);
}

static void steps(void)
{
	struct fake f;
	struct swl_wx x;
	unsigned i;

	/* W0: the entry state is pinned */
	for (i = 0; i < 5; i++) {
		obs(&f);
		if (i == 0) f.bar2lo = 0x60400000; else if (i == 1) f.bar2hi = 1; else if (i == 2) f.win2 = 0x400000; else if (i == 3) f.c88 = 0x00080380; else f.bar2lo = 0x60800004;
		CHECK(run(&f, &x, 3) < 0 && x.row == 0 && f.nw == 0 && x.stop_code == SWW_E_STATE && strstr(logbuf, "stopped at W0 restore=none") != 0);
	}
	obs(&f); f.cmd = 0x00100006;
	CHECK(run(&f, &x, 3) < 0 && x.row == 0 && f.nw == 0 && x.stop_code == SWW_E_PRE);
	obs(&f); f.ltssm = 0;
	CHECK(run(&f, &x, 3) < 0 && x.row == 0 && f.nw == 0 && x.stop_code == SWW_E_PRE);
	/* W1: region 2 must be untouched (CR1, UPPER_BASE, UPPER_TARGET zero, CR2 bit 31 clear); the other words are free */
	for (i = 0; i < 4; i++) {
		static const unsigned idx[4] = { 0, 3, 6, 1 };

		obs(&f);
		f.a2[idx[i]] = idx[i] == 1 ? 0x80000000u : 1u;
		CHECK(run(&f, &x, 3) < 0 && x.row == 1 && f.nw == 0 && x.stop_code == SWW_E_STATE);
	}
	obs(&f); f.a2[2] = 0x12345678u; f.a2[4] = 1; f.a2[5] = 2;
	CHECK(run(&f, &x, 3) == 0);
	obs(&f); f.a2[1] = 0x40000000u;                  /* CR2 must be exactly 0 (BAR_MODE set is not 'untouched') */
	CHECK(run(&f, &x, 3) < 0 && x.row == 1 && f.nw == 0 && x.stop_code == SWW_E_STATE);
	/* W2: no region 2 in silicon (reads back 0): STOP at the first store, nothing armed; mapping refused: STOP with the CR2 restore */
	obs(&f); f.has_r2 = 0;
	CHECK(run(&f, &x, 3) < 0 && x.row == 2 && f.nw == 1 && x.stop_code == SWW_E_READBACK && x.restore == SWW_RS_NONE);
	obs(&f); f.map_fail = 1;
	CHECK(run(&f, &x, 3) < 0 && x.row == 2 && x.stop_code == SWW_E_MAP && x.restore == SWW_RS_OK && f.nw == 5 && f.a2[1] == 0 && f.w[4].space == SWW_ATU && f.w[4].off == SWW_R2(4) && f.w[4].val == 0);
	/* W3: SRPWR is read only; bit 9 and bit 17 both required; no store of it exists */
	{
		static const uint32_t bad[] = { 0, 0x200, 0x20000, 0x00030100, 0x00010300, 0xFFFFFFFFu };
		for (i = 0; i < sizeof bad / sizeof bad[0]; i++) {
			obs(&f); f.srpwr = bad[i];
			CHECK(run(&f, &x, 3) < 0 && x.row == 3 && x.stop_code == SWW_E_SRPWR && f.nw == 5 && x.restore == SWW_RS_NONE);
			CHECK(f.w[4].space == SWW_CFG && f.w[4].val == SWW_WIN_PCIE && all_allowed(&f));
		}
		obs(&f); f.srpwr = 0x00020200;                  /* request and status of domain 1 only */
		CHECK(run(&f, &x, 3) == 0);
	}
	/* W4: RESET_ST stuck busy before the assert (no store yet) and after it (restore RESET_CTL := 1) */
	obs(&f); f.sm.st = 1;
	CHECK(run(&f, &x, 3) < 0 && x.row == 4 && x.stop_code == SWW_E_WAIT && f.nw == 6 && x.restore == SWW_RS_NONE && f.clock_us == 290);
	obs(&f); f.st_stuck_sm = 1;
	CHECK(run(&f, &x, 3) < 0 && x.row == 4 && x.stop_code == SWW_E_WAIT && x.restore == SWW_RS_OK && f.sm.ctl == 1 && f.nw == 8 && only_restore_after(&f, 7));
	/* W5 coreinfo: only the observed bank layout; every other decode stops before one bankidx store (the executor's own pins, E_BANK) */
	{
		static const uint32_t ci[] = { 0x114, 0x116, 0x0F5, 0x135, 0x115 ^ 0x20, 0x1F5, 0x000, 0x3FF, 0xFFFFFFFFu, 0x100, 0x015 };
		for (i = 0; i < sizeof ci / sizeof ci[0]; i++) {
			obs(&f); f.coreinfo = ci[i];
			CHECK(run(&f, &x, 3) < 0 && x.row == 5 && f.nw == 11 && x.nbank == 0 && x.restore == SWW_RS_NONE && x.stop_code == (ci[i] == 0xFFFFFFFFu ? SWW_E_DEAD : SWW_E_BANK));
		}
		obs(&f); f.coreinfo = 0x03000115u;     /* fields above bit 9 are not part of the DHD decode (rev < 12) */
		CHECK(run(&f, &x, 3) == 0);
	}
	/* W5 bank values: all-ones stops at that bank; the RAM size bounds are exact */
	obs(&f); f.bankinfo[10] = 0xFFFFFFFFu;
	CHECK(run(&f, &x, 3) < 0 && x.row == 5 && x.stop_code == SWW_E_DEAD && x.nbank == 3 && f.nw == 14);
	obs(&f); units(&f, 512);
	CHECK(run(&f, &x, 3) == 0 && x.ram_bytes == SWW_RAM_MAX);
	obs(&f); units(&f, 513);
	CHECK(run(&f, &x, 3) < 0 && x.row == 5 && x.stop_code == SWW_E_RAM && x.nbank == 21 && x.restore == SWW_RS_NONE);
	obs(&f); units(&f, 219);
	CHECK(run(&f, &x, 3) == 0 && x.ram_bytes == 219u * 8192u && x.ram_bytes >= SWW_RAM_MIN);
	obs(&f); units(&f, 218);
	CHECK(run(&f, &x, 3) < 0 && x.row == 5 && x.stop_code == SWW_E_RAM && x.ram_bytes < SWW_RAM_MIN);
	obs(&f); units(&f, 21);
	CHECK(run(&f, &x, 3) < 0 && x.stop_code == SWW_E_RAM);
	/* W5: the 10 us between bankidx and bankinfo (siutils.c:6962) */
	obs(&f);
	CHECK(run(&f, &x, 3) == 0 && f.early == 0 && f.clock_us >= 21 * 10);
	/* read-back masks: the IOCTL compares 14 bits (the upper bits of the register are not ours), RESET_CTL one bit */
	obs(&f); f.ioctl_noise = 0x80008000u; f.sm.ctl = 0xFFFFFFFEu; f.ca.ctl = 0xFFFFFFFEu;
	CHECK(run(&f, &x, 3) == 0 && (f.sm.ioctl & 0x3FFF) == 1 && (f.ca.ioctl & 0x3FFF) == 0x21 && (f.sm.ctl & 1) == 0);
	/* W6: CA7 halt not latching or RESET_ST stuck: STOP with RESET_CTL := 1 on the CA7 wrapper only */
	obs(&f); f.st_stuck_ca = 1;
	CHECK(run(&f, &x, 3) < 0 && x.row == 6 && x.stop_code == SWW_E_WAIT && x.restore == SWW_RS_OK && f.ca.ctl == 1 && f.sm.ioctl == 1 && f.sm.ctl == 0 && is_restore(&f.w[f.nw - 1]) && f.w[f.nw - 1].win == 0x18120000u);
	/* W7: all-ones at the RAM base or at address 0 stops; cfg 0x84 must still be 0 (BAR2 offset == backplane address); no store, nothing armed */
	obs(&f); f.bar2_ram = 0xFFFFFFFFu;
	CHECK(run(&f, &x, 3) < 0 && x.row == 7 && x.stop_code == SWW_E_DEAD && f.nw == NWANT && x.restore == SWW_RS_NONE);
	obs(&f); f.bar2_0 = 0xFFFFFFFFu;
	CHECK(run(&f, &x, 3) < 0 && x.row == 7 && x.stop_code == SWW_E_DEAD && f.nw == NWANT);
	obs(&f); f.bar2_ram = 0x12345678u; f.bar2_0 = 0xE59FF018u;
	CHECK(run(&f, &x, 3) == 0 && strstr(logbuf, "W7 BAR2 [0x200000] 0x12345678 [0] 0xe59ff018") != 0);
	/* W4/W6: the wrapper read after the sequence is checked again (RESET_ST, RESET_CTL, IOCTL changed by the chip after the last store) */
	{
		unsigned a;

		for (a = 1; a <= 3; a++) {
			obs(&f); f.after1 = a;
			CHECK(run(&f, &x, 3) < 0 && x.row == 4 && x.stop_code == SWW_E_STATE && x.restore == SWW_RS_OK && f.nw > 10 && is_restore(&f.w[f.nw - 1]) && f.w[f.nw - 1].win == 0x18126000u);
			obs(&f); f.after1 = a;
			{
				static struct swl_wbus b;
				bus(&b, &f); mkx(&x, &b, 3);
				ready(&x, &f, F_ALL & ~(SWF_W6 | SWF_W7));
				f.win0 = SWW_WIN_CA7WRAP;
				CHECK(swl_w6(&x) < 0 && x.row == 6 && x.stop_code == SWW_E_STATE && x.restore == SWW_RS_OK && f.ca.ctl == 1);
			}
		}
	}
	/* W7 re-reads the preconditions: the BAR2 reads are not stores, so this is the only check before them */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		CHECK(swl_stage3(&x) == 0);
		f.ltssm = 0;
		x.stopped = 0;
		CHECK(swl_w7(&x) < 0 && x.stop_code == SWW_E_PRE);
	}
	/* the BAR2 reads follow the live cfg 0x84 check: a window that moved stops the run */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		CHECK(swl_stage3(&x) == 0);
		f.win2 = 0x400000;
		x.stopped = 0;
		CHECK(swl_w7(&x) < 0 && x.stop_code == SWW_E_STATE);
	}
	/* link not in L0 / region 1 changed after the run began: the next store refuses (the 0124 preconditions are live, not cached) */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		CHECK(swl_w0(&x) == 0 && swl_w1(&x) == 0);
		f.ltssm = 0x0300a900;
		CHECK(swl_w2(&x) < 0 && x.stop_code == SWW_E_PRE && f.nw == 0);
	}
	/* the restore itself: skipped (not attempted) when the preconditions are gone, reported as skipped */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		x.flags = 0x1FFu;
		x.armed = SWW_RG_SM;
		f.ltssm = 0;
		CHECK(swl_stop(&x, SWW_E_WAIT) == -SWW_E_WAIT && f.nw == 0 && x.restore == SWW_RS_SKIPPED);
	}
	/* the restore moves cfg 0x80 to its own window first (the failing step may have left another one), and reports a window that does not latch */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		x.flags = 0x1FFu;
		x.armed = SWW_RG_SM;
		ready(&x, &f, 0x1FFu);
		f.win0 = 0x18123000u;
		CHECK(swl_stop(&x, SWW_E_WAIT) == -SWW_E_WAIT && f.nw == 2 && x.restore == SWW_RS_OK);
		CHECK(f.w[0].space == SWW_CFG && f.w[0].off == 0x80 && f.w[0].val == 0x18126000u && f.w[1].space == SWW_BAR0 && f.w[1].win == 0x18126000u && f.w[1].off == 0x800 && f.w[1].val == 1);
		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		x.armed = SWW_RG_CA;
		ready(&x, &f, 0x1FFu);
		f.win0 = 0x18123000u;
		f.win_stuck = 1;
		CHECK(swl_stop(&x, SWW_E_WAIT) == -SWW_E_WAIT && f.nw == 1 && x.restore == SWW_RS_FAILED && f.ca.ctl == 1);
	}
	/* a restore store that does not take is reported as failed */
	{
		struct swl_wbus b;

		obs(&f); bus(&b, &f); mkx(&x, &b, 3);
		x.flags = 0x1FFu;
		x.armed = SWW_RG_ATU2;
		f.drop_at = 1;
		f.a2[1] = 0x80000000u;
		CHECK(swl_stop(&x, SWW_E_WAIT) == -SWW_E_WAIT && f.nw == 1 && x.restore == SWW_RS_FAILED);
	}
}


/* ---------------------------------------------------------------- run B (patch 0130): W8 window, W9 last word, W10 image copy ---------------------------------------------------------------- */
#define PAT 0xA5A5A5A5u
#define PATX 0x5A5A5A5Au            /* the RAM word under the image before the copy = image word ^ PATX: never equal, so a dropped store is always visible */
static uint32_t ramarr[RAM_WORDS];
static uint8_t imgbuf[SWW_IMG_MAX + 1];
static uint32_t imglen;

static uint32_t tcrc(const uint8_t *p, uint32_t n)         /* zlib CRC32, written independently of the header (known answer below) */
{
	uint32_t c = 0xFFFFFFFFu, k;

	while (n--) {
		c ^= *p++;
		for (k = 0; k < 8; k++)
			c = (c & 1u) ? (c >> 1) ^ 0xEDB88320u : c >> 1;
	}
	return ~c;
}

static uint32_t iw(uint32_t w)                              /* the image word as a store value (bytes beyond the length are 0) */
{
	uint32_t v = 0, k;

	for (k = 0; k < 4; k++)
		if (4 * w + k < imglen)
			v |= (uint32_t)imgbuf[4 * w + k] << (8 * k);
	return v;
}

static void mkimg(uint32_t len)                             /* a synthetic blob (no firmware byte) */
{
	uint32_t i, r = 0x12345678u;

	imglen = len;
	for (i = 0; i < len; i++) {
		r = r * 1664525u + 1013904223u;
		imgbuf[i] = (uint8_t)(r >> 24);
	}
}

#define NB_TAIL 3u          /* stores between run A and the image when a window is needed: cfg 0x84 := TAIL, the last word, cfg 0x84 := 0 */
#define NB_FLAT 1u

static void obsb(struct fake *f, unsigned units_total)     /* RAM = units_total x 8 KiB; the words under the image hold image ^ PATX, all others PAT */
{
	uint32_t a, words = (imglen + 3) / 4;

	obs(f);
	f->ram = ramarr;
	units(f, units_total);
	for (a = 0; a < RAM_WORDS; a++)
		ramarr[a] = PAT;
	for (a = 0; a < words; a++)
		ramarr[SWW_RAM_BASE / 4 + a] = iw(a) ^ PATX;
}

static int runb(struct fake *f, struct swl_wx *x, int level, uint32_t len, uint32_t crc)
{
	static struct swl_wbus b;

	bus(&b, f);
	mkx(x, &b, level);
	x->img = len ? imgbuf : 0;
	x->img_len = len;
	x->img_crc = crc;
	loglen = 0; logbuf[0] = 0;
	return swl_stage3(x);
}

/* every RAM word is either untouched, an image word, or the (zeroed) last word `last` (backplane address, 0 = none), and image words are a prefix: words [0, nimg) hold the image */
static int ram_ok(uint32_t nimg, uint32_t last)
{
	uint32_t a, words = (imglen + 3) / 4;

	for (a = 0; a < RAM_WORDS; a++) {
		uint32_t i = a - SWW_RAM_BASE / 4;

		if (a >= SWW_RAM_BASE / 4 && i < words) {
			if (ramarr[a] != (i < nimg ? iw(i) : (iw(i) ^ PATX)))
				return 0;
		} else if (last && a == last / 4) {
			if (ramarr[a] != 0 && ramarr[a] != PAT && ramarr[a] != 1)
				return 0;
		} else if (ramarr[a] != PAT) {
			return 0;
		}
	}
	return 1;
}

static unsigned count_str(const char *needle)
{
	unsigned n = 0;
	const char *p = logbuf;

	while ((p = strstr(p, needle)) != 0) { n++; p += strlen(needle); }
	return n;
}

static void runb_happy(void)
{
	static struct fake f;
	struct swl_wx x;
	uint32_t len = 3 * 65536 + 4098, words = (len + 3) / 4, crc, nch = (words + SWW_CHUNK_W - 1) / SWW_CHUNK_W;
	char t[96];
	unsigned j;

	CHECK(tcrc((const uint8_t *)"123456789", 9) == 0xCBF43926u);          /* the zlib check value */
	mkimg(len);
	crc = tcrc(imgbuf, len);
	CHECK(words == 50177 && nch == 4);
	/* ram_end 0x4a0000 (the observed size): the last word needs the window */
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, len, crc) == 0);
	CHECK(x.stopped == 0 && x.restore == SWW_RS_NONE && x.img_next == words && x.img_crc_rb == crc && x.flags == (0x1FFu | SWF_W8 | SWF_W9 | SWF_W10));
	CHECK(f.nw == NWANT + NB_TAIL + words && x.nstore == f.nw);
	CHECK(f.w[NWANT].space == SWW_CFG && f.w[NWANT].off == 0x84 && f.w[NWANT].val == 0x400000u);
	CHECK(f.w[NWANT + 1].space == SWW_BAR2 && f.w[NWANT + 1].win == 0x400000u && f.w[NWANT + 1].off == 0x9FFFCu && f.w[NWANT + 1].val == 0);
	CHECK(f.w[NWANT + 2].space == SWW_CFG && f.w[NWANT + 2].off == 0x84 && f.w[NWANT + 2].val == 0);
	CHECK(f.w[NWANT + 3].space == SWW_BAR2 && f.w[NWANT + 3].win == 0 && f.w[NWANT + 3].off == SWW_RAM_BASE && f.w[NWANT + 3].val == iw(0));
	CHECK(all_allowed(&f) && f.win2 == 0 && f.win0 == 0x18120000u && f.sm.ioctl == 1 && f.ca.ioctl == 0x21);
	CHECK(ram_ok(words, 0x49FFFCu) && ramarr[0x49FFFCu / 4] == 0);
	CHECK(ramarr[SWW_RAM_BASE / 4 + words - 1] == iw(words - 1) && (ramarr[SWW_RAM_BASE / 4 + words - 1] >> 16) == 0);     /* the 2 bytes beyond the image are stored as 0 */
	snprintf(t, sizeof t, "SWEET WLAN: stage 3 level 4 done crc=%#010x words=%u\n", crc, words);
	CHECK(strstr(logbuf, t) != 0 && strstr(logbuf, "level 3 done") == 0 && count_str("W10 chunk ") == nch);
	CHECK(strstr(logbuf, "W8 cfg 0x84 := 0x400000 (ram_end 0x4a0000 > 0x400000)") != 0 && strstr(logbuf, "W9 last RAM word bp 0x49fffc (BAR2 window 0x400000 offset 0x9fffc) := 0") != 0);
	CHECK(strstr(logbuf, "W8 cfg 0x84 := 0 (window restored)") != 0);
	for (j = 1; j <= nch; j++) {
		snprintf(t, sizeof t, "W10 chunk %u/%u words ", j, nch);
		CHECK(strstr(logbuf, t) != 0);
	}
	CHECK(strstr(logbuf, "W10 chunk 4/4 words 49152..50176 ") != 0);
	/* the log of a level-3 run is untouched, even with an image handed over: no store beyond run A */
	obsb(&f, 336);
	CHECK(runb(&f, &x, 3, len, crc) == 0 && f.nw == NWANT && strstr(logbuf, "SWEET WLAN: stage 3 level 3 done ram_end=0x4a0000 banks=21\n") != 0 && ram_ok(0, 0));
	/* ram_end <= 0x400000: no window at all */
	obsb(&f, 224);
	CHECK(runb(&f, &x, 4, len, crc) == 0 && f.nw == NWANT + NB_FLAT + words && f.win2 == 0);
	CHECK(f.w[NWANT].space == SWW_BAR2 && f.w[NWANT].win == 0 && f.w[NWANT].off == 0x3BFFFCu && f.w[NWANT].val == 0 && f.w[NWANT + 1].off == SWW_RAM_BASE);
	for (j = 0; j < 48; j++)
		CHECK(!(f.w[j].space == SWW_CFG && f.w[j].off == 0x84));
	CHECK(ram_ok(words, 0x3BFFFCu) && strstr(logbuf, "W8 no BAR2 window change (ram_end 0x3c0000 <= 0x400000)") != 0 && strstr(logbuf, "(window restored)") == 0);
	/* the boundary: ram_end == 0x400000 needs no window, one bank unit more does (offset 0x1ffc), the 4 MiB maximum ends at the table's last offset */
	obsb(&f, 256);
	CHECK(runb(&f, &x, 4, len, crc) == 0 && f.nw == NWANT + NB_FLAT + words && f.w[NWANT].win == 0 && f.w[NWANT].off == 0x3FFFFCu && ram_ok(words, 0x3FFFFCu));
	obsb(&f, 257);
	CHECK(runb(&f, &x, 4, len, crc) == 0 && f.nw == NWANT + NB_TAIL + words && f.w[NWANT + 1].win == 0x400000u && f.w[NWANT + 1].off == 0x1FFCu && ram_ok(words, 0x401FFCu));
	obsb(&f, 512);
	CHECK(runb(&f, &x, 4, len, crc) == 0 && f.w[NWANT + 1].win == 0x400000u && f.w[NWANT + 1].off == 0x1FFFFCu && ram_ok(words, 0x5FFFFCu) && f.win2 == 0);
}

static void runb_full(void)
{
	static struct fake f;
	struct swl_wx x;
	uint32_t crc;

	mkimg(SWW_IMG_MAX);
	crc = tcrc(imgbuf, SWW_IMG_MAX);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, SWW_IMG_MAX, crc) == 0 && x.img_next == 441005u && f.nw == NWANT + NB_TAIL + 441005u && x.img_crc_rb == crc && count_str("W10 chunk ") == 27);
	CHECK(f.w[NWANT + 3].off == SWW_RAM_BASE && ramarr[0x3AEAB0u / 4] == iw(441004u) && (ramarr[0x3AEAB0u / 4] >> 16) == 0 && ramarr[0x3AEAB4u / 4] == PAT && ram_ok(441005u, 0x49FFFCu));
	/* one byte more than the bound, an empty image, a wrong container CRC, no image: refused at W8, before the first run B store */
	obsb(&f, 336);
	mkimg(SWW_IMG_MAX + 1);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, SWW_IMG_MAX + 1, tcrc(imgbuf, SWW_IMG_MAX + 1)) < 0 && x.stop_code == SWW_E_IMG && f.nw == NWANT && f.win2 == 0 && x.row == 8 && ram_ok(0, 0));       /* the CRC is right: only the size bound refuses it */
	CHECK(strstr(logbuf, "stopped at W8 restore=none") != 0);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, 0, 0) < 0 && x.stop_code == SWW_E_IMG && f.nw == NWANT);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, SWW_IMG_MAX, crc ^ 1u) < 0 && x.stop_code == SWW_E_IMG && f.nw == NWANT && x.img_next == 0 && ram_ok(0, 0));
	CHECK(strstr(logbuf, "W8 image CRC ") != 0);
}

static void runb_faults(void)
{
	static struct fake f, g;
	struct swl_wx x;
	uint32_t len = 3 * 65536 + 4098, words = (len + 3) / 4, crc, k, nrd0, nrd1;
	static const uint32_t pos[] = { 0, 1, 2, SWW_CHUNK_W - 1, SWW_CHUNK_W, SWW_CHUNK_W + 1, 2 * SWW_CHUNK_W - 1, 2 * SWW_CHUNK_W, 3 * SWW_CHUNK_W - 1, 3 * SWW_CHUNK_W, 50175, 50176 };
	unsigned pi, mode;

	mkimg(len);
	crc = tcrc(imgbuf, len);
	/* a store of the image that the chip drops, corrupts or the bus refuses, at every position class (first/last word of a chunk, the partial last word, ...) */
	for (pi = 0; pi < sizeof pos / sizeof pos[0]; pi++) {
		uint32_t p = pos[pi], j = NWANT + NB_TAIL + p + 1, cend = (p / SWW_CHUNK_W + 1) * SWW_CHUNK_W < words ? (p / SWW_CHUNK_W + 1) * SWW_CHUNK_W : words;

		CHECK(p < words);
		for (mode = 0; mode < 3; mode++) {
			obsb(&f, 336);
			if (mode == 0) f.drop_at = j; else if (mode == 1) f.corrupt_at = j; else f.fail_wr_at = j;
			CHECK(runb(&f, &x, 4, len, crc) < 0);
			CHECK(x.stopped == 1 && x.row == 10 && x.restore == SWW_RS_NONE && f.win2 == 0 && all_allowed(&f));
			if (mode == 2) {
				CHECK(x.stop_code == SWW_E_WR && f.nw == j && ramarr[SWW_RAM_BASE / 4 + p] == (iw(p) ^ PATX) && x.img_next == p);
			} else {
				/* caught by the CRC of the chunk it is in, which is the last chunk written: no later chunk starts */
				CHECK(x.stop_code == SWW_E_READBACK && f.nw == NWANT + NB_TAIL + cend && x.img_next == cend);
				CHECK(count_str("W10 chunk ") == p / SWW_CHUNK_W + 1 && strstr(logbuf, "stopped at W10 restore=none") != 0);
				CHECK(cend == words || ramarr[SWW_RAM_BASE / 4 + cend] == (iw(cend) ^ PATX));
			}
		}
	}
	/* the restore of cfg 0x84 stays armed from the first window store until W9 is done, whatever fails in between */
	for (mode = 0; mode < 3; mode++) {
		for (k = NWANT + 1; k <= NWANT + 3; k++) {
			obsb(&f, 336);
			if (mode == 0) f.drop_at = k; else if (mode == 1) f.corrupt_at = k; else f.fail_wr_at = k;
			CHECK(runb(&f, &x, 4, len, crc) < 0 && x.stopped == 1 && f.win2 == 0 && x.restore == SWW_RS_OK && all_allowed(&f));
			CHECK(x.row == (k == NWANT + 2 ? 9u : 8u) && f.w[f.nw - 1].space == SWW_CFG && f.w[f.nw - 1].off == 0x84 && f.w[f.nw - 1].val == 0 && strstr(logbuf, "restore=ok") != 0);
			CHECK(x.img_next == 0 && ram_ok(0, 0x49FFFCu));
		}
	}
	/* the restore itself failing is reported: the window sticks at 0x400000 from the store that should set it back */
	obsb(&f, 336);
	f.win2_stuck = NWANT + 3;
	CHECK(runb(&f, &x, 4, len, crc) < 0 && x.stop_code == SWW_E_READBACK && x.row == 8 && f.win2 == 0x400000u && x.restore == SWW_RS_FAILED && strstr(logbuf, "stopped at W8 restore=failed") != 0);
	CHECK(f.nw == NWANT + 4);
	/* a failing read at every position of W8..W10's setup, then strided over the copy and its read-back: STOP, window back at 0, only allowed stores, RAM untouched or an image prefix */
	obsb(&g, 336);
	CHECK(runb(&g, &x, 3, len, crc) == 0);
	nrd0 = g.nrd;
	obsb(&g, 336);
	CHECK(runb(&g, &x, 4, len, crc) == 0);
	nrd1 = g.nrd;
	CHECK(nrd1 > nrd0 + words);
	for (k = nrd0 + 1; k <= nrd1; k += (k < nrd0 + 400 ? 1u : 4093u)) {
		obsb(&f, 336);
		f.fail_rd_at = k;
		CHECK(runb(&f, &x, 4, len, crc) < 0 && x.stopped == 1 && f.win2 == 0 && all_allowed(&f) && x.img_next <= words);
		CHECK(ram_ok(x.img_next, 0x49FFFCu));
	}
	for (k = nrd1 - 2; k <= nrd1; k++) {
		obsb(&f, 336);
		f.fail_rd_at = k;
		CHECK(runb(&f, &x, 4, len, crc) < 0 && x.stopped == 1 && f.win2 == 0 && ram_ok(words, 0x49FFFCu));
	}
}

/* the guard rules of swl_wr() for the run B tuples, each in isolation */
static void runb_gates(void)
{
	static struct fake f;
	struct swl_wx x;
	static struct swl_wbus b;
	uint32_t len = 3 * 65536 + 4098, words = (len + 3) / 4, crc, n;
	const uint32_t RB = F_ALL;          /* run A done; W8..W10 flags added per case */

	mkimg(len);
	crc = tcrc(imgbuf, len);
#define GB(units, fl) do { obsb(&f, (units)); bus(&b, &f); mkx(&x, &b, 4); ready(&x, &f, (fl)); x.ram_bytes = (units) * 8192u; x.img = imgbuf; x.img_len = len; x.img_crc = crc; loglen = 0; logbuf[0] = 0; } while (0)
	/* the image check of W8 and of W10 (each alone: the second does not rely on the first) */
	GB(336, F_ALL | SWF_W8 | SWF_W9); x.img_crc ^= 1u;
	CHECK(swl_w10(&x) == -SWW_E_IMG && f.nw == 0 && x.row == 10 && strstr(logbuf, "W10 image CRC ") != 0);
	GB(336, F_ALL); x.img_crc ^= 1u;
	CHECK(swl_w8(&x) == -SWW_E_IMG && f.nw == 0 && x.row == 8);
	GB(336, F_ALL | SWF_W8 | SWF_W9); x.img_len = SWW_IMG_MAX + 1;
	CHECK(swl_w10(&x) == -SWW_E_IMG && f.nw == 0);
	GB(336, F_ALL); x.img = 0;
	CHECK(swl_w8(&x) == -SWW_E_IMG && f.nw == 0);
	/* W8: only when ram_end > 0x400000, only from window 0, only with W7 done, only at level 4 */
	GB(336, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == 0 && f.win2 == 0x400000u && f.nw == 1 && (x.armed & SWW_RG_W84));
	GB(224, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_TAIL && f.nw == 0 && f.win2 == 0);
	GB(256, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_TAIL && f.nw == 0);
	GB(257, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == 0);
	GB(336, F_ALL); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_OLD && f.nw == 0);
	GB(336, F_ALL); x.level = 3;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_LEVEL && f.nw == 0);
	GB(336, F_ALL & ~SWF_W7);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_PHASE && f.nw == 0);
	GB(336, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == 0 && swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_REPEAT && f.nw == 2 && f.win2 == 0);      /* the STOP restored the window */
	GB(336, F_ALL); f.cmd = 0x6;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_PRE && f.nw == 0);
	/* the cfg 0x84 value other than 0 and 0x400000 has no tuple */
	GB(336, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x200000) == -SWW_E_DENIED && f.nw == 0);
	GB(336, F_ALL);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400001) == -SWW_E_DENIED && f.nw == 0);
	/* the restore-to-0 write: after the last word, only from 0x400000, only when a window was needed */
	GB(336, RB | SWF_W8); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_ORDER && f.nw == 0);
	GB(336, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_LAST_HI;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == 0 && f.win2 == 0);
	GB(336, RB | SWF_W8); x.done = 1u << SWT_LAST_HI;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_OLD && f.nw == 0);
	GB(224, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_LAST_HI;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_TAIL && f.nw == 0);
	/* W9, window 0: only at ram_end - 4, only with value 0, only with the window at 0 */
	GB(224, RB | SWF_W8);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 0) == 0 && ramarr[0x3BFFFCu / 4] == 0 && f.nw == 1);
	GB(224, RB | SWF_W8);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFF8u, 0) == -SWW_E_RAM && f.nw == 0);
	GB(224, RB | SWF_W8);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3FFFFCu, 0) == -SWW_E_RAM && f.nw == 0);
	GB(224, RB | SWF_W8);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 1) == -SWW_E_DENIED && f.nw == 0);
	GB(224, RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 0) == -SWW_E_PHASE && f.nw == 0);
	GB(224, RB | SWF_W8); x.level = 3;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 0) == -SWW_E_LEVEL && f.nw == 0);
	GB(224, RB | SWF_W8); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 0) == -SWW_E_WIN && f.nw == 0);
	GB(224, RB | SWF_W8); ramarr[0x3BFFFCu / 4] = 0xFFFFFFFFu;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x3BFFFCu, 0) == -SWW_E_DEAD && f.nw == 0);
	GB(336, RB | SWF_W8);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x49FFFCu, 0) == -SWW_E_DENIED && f.nw == 0);     /* beyond window 0: no tuple */
	/* W9, window 0x400000 */
	GB(336, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFFCu, 0) == 0 && ramarr[0x49FFFCu / 4] == 0 && f.nw == 1 && (x.armed & SWW_RG_W84));
	GB(336, RB | SWF_W8); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFFCu, 0) == -SWW_E_ORDER && f.nw == 0);
	GB(336, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFF8u, 0) == -SWW_E_RAM && f.nw == 0);
	GB(336, RB | SWF_W8); x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFFCu, 0) == -SWW_E_WIN && f.nw == 0);
	GB(224, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x1FFFCu, 0) == -SWW_E_TAIL && f.nw == 0);
	GB(336, RB | SWF_W8); f.win2 = 0x400000u; x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFFCu, 1) == -SWW_E_DENIED && swl_wr(&x, SWW_BAR2, 0x400000, 0x200000u, 0) == -SWW_E_STOPPED && f.nw == 0);
	/* W10: ascending, the image word, inside the image, per-chunk checks only at a chunk start */
	GB(336, RB | SWF_W9);
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) == 0 && x.img_next == 1 && f.nw == 1 && f.nrd > 0 && x.done == 0);
	n = f.nrd;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4, iw(1)) == 0 && x.img_next == 2 && f.nrd == n && ramarr[SWW_RAM_BASE / 4 + 1] == iw(1));     /* inside a chunk: no read at all */
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 12, iw(3)) == -SWW_E_IMG && x.stopped && f.nw == 2);                                  /* skipped a word */
	GB(336, RB | SWF_W9);
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0) ^ 1u) == -SWW_E_IMG && f.nw == 0);                                              /* not the image word */
	GB(336, RB | SWF_W9);
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4, iw(1)) == -SWW_E_IMG && f.nw == 0);                                                /* must start at word 0 */
	GB(336, RB | SWF_W9);
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4, iw(0)) == -SWW_E_IMG && f.nw == 0 && ramarr[SWW_RAM_BASE / 4 + 1] == (iw(1) ^ PATX));  /* the right word at the wrong address */
	GB(336, RB | SWF_W9); x.img = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, 0) == -SWW_E_IMG && f.nw == 0);
	GB(336, RB | SWF_W9); x.img_len = SWW_IMG_MAX + 1;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) == -SWW_E_IMG && f.nw == 0);
	GB(336, RB | SWF_W9); x.img_len = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, 0) == -SWW_E_IMG && f.nw == 0);
	GB(336, RB | SWF_W9); x.img_next = words;                                           /* one past the image */
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4 * words, iw(words)) == -SWW_E_IMG && f.nw == 0);
	GB(336, RB | SWF_W9); x.img_next = words; x.img_len = SWW_IMG_MAX;                  /* ... and at the table's own end: no tuple or not the last word */
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_IMG_END, 0) < 0 && f.nw == 0);
	GB(336, RB); 
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) == -SWW_E_PHASE && f.nw == 0);
	GB(336, RB | SWF_W9); x.level = 3;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) == -SWW_E_LEVEL && f.nw == 0);
	GB(336, RB | SWF_W9); x.img_next = SWW_CHUNK_W; f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4 * SWW_CHUNK_W, iw(SWW_CHUNK_W)) == -SWW_E_WIN && f.nw == 0);                       /* a chunk start re-checks the window */
	GB(336, RB | SWF_W9); x.img_next = SWW_CHUNK_W; f.cmd = 0x6;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4 * SWW_CHUNK_W, iw(SWW_CHUNK_W)) == -SWW_E_PRE && f.nw == 0);                       /* ... and the preconditions */
	GB(336, RB | SWF_W9); ramarr[SWW_RAM_BASE / 4] = 0xFFFFFFFFu;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) == -SWW_E_DEAD && f.nw == 0);                                                   /* ... and a dead read of the old word */
	GB(336, RB | SWF_W9); x.img_next = SWW_CHUNK_W + 1;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE + 4 * (SWW_CHUNK_W + 1), iw(SWW_CHUNK_W + 1)) == 0 && f.nrd == 0);                      /* the first word after a chunk start: no reads */
	GB(336, RB | SWF_W9); f.fail_rd_at = 1;
	CHECK(swl_wr(&x, SWW_BAR2, 0, SWW_RAM_BASE, iw(0)) < 0 && f.nw == 0);
#undef GB
}

/* the allowlist decision for BAR2 and cfg 0x84, over every offset: restated here with literals (docs/research/43 rows W8..W10; image end 0x200000 + 441005 x 4 = 0x3AEAB4) */
static void runb_deny_sweep(void)
{
	uint32_t off, v, w;
	static const uint32_t wins[] = { 0, 0x400000u, 0x200000u, 0x800000u, 0x18001000u };
	unsigned wi;

	for (wi = 0; wi < sizeof wins / sizeof wins[0]; wi++) {
		for (off = 0; off < 0x7FFFFCu + 0x40u; off += 4) {
			for (v = 0; v < 3; v++) {
				int want;

				w = wins[wi];
				if (w == 0)
					want = off >= 0x200000u && off <= 0x3FFFFCu && (off < 0x3AEAB4u || v == 0);
				else if (w == 0x400000u)
					want = off <= 0x1FFFFCu && v == 0;
				else
					want = 0;
				CHECK((swl_wr_ok(SWW_BAR2, w, off, v) >= 0) == want);
				CHECK(swl_wr_ok(SWW_BAR2, w, off + 1, v) < 0 && swl_wr_ok(SWW_BAR2, w, off + 2, v) < 0 && swl_wr_ok(SWW_BAR2, w, off + 3, v) < 0);
			}
		}
	}
	for (v = 0; v < 0x100; v++)
		CHECK((swl_wr_ok(SWW_CFG, 0, 0x84, v << 20) >= 0) == (v == 0 || v == 4));
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x84, 0x400004) < 0 && swl_wr_ok(SWW_CFG, 0, 0x84, 0x80000000u) < 0 && swl_wr_ok(SWW_CFG, 0, 0x88, 0) < 0 && swl_wr_ok(SWW_CFG, 0, 0x8C, 0) < 0 && swl_wr_ok(SWW_CFG, 0x400000, 0x84, 0) < 0);
	CHECK(swl_wr_ok(SWW_ATU, 0, 0x84, 0) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x84, 0) < 0 && swl_wr_ok(SWW_ELBI, 0, 0x84, 0) < 0 && swl_wr_ok(SWW_ISO, 0, 0x84, 0) < 0);
	/* every run B tuple: run 2, a row of W8..W10 */
	for (v = SWT_N_A; v < SWT_N; v++)
		CHECK(swl_wt[v].run == 2 && swl_wt[v].row >= 8 && swl_wt[v].row <= 10 && (swl_wt[v].space == SWW_BAR2 || swl_wt[v].space == SWW_CFG));
	/* reads: the BAR2 words of run B only at level 4, windows 0 and 0x400000, inside the table's ranges */
	{
		static struct fake f;
		static struct swl_wbus b;
		struct swl_wx x;
		uint32_t r = 0;

		obsb(&f, 336);
		bus(&b, &f);
		mkx(&x, &b, 3);
		CHECK(swl_rdx(&x, SWW_BAR2, 0, 0x200004, &r) < 0 && swl_rdx(&x, SWW_BAR2, 0x400000, 0, &r) < 0 && f.nrd == 0);
		mkx(&x, &b, 4);
		CHECK(swl_rdx(&x, SWW_BAR2, 0, 0x200004, &r) == 0 && swl_rdx(&x, SWW_BAR2, 0, 0x3FFFFC, &r) == 0 && swl_rdx(&x, SWW_BAR2, 0, 0x400000, &r) < 0 && swl_rdx(&x, SWW_BAR2, 0, 0x1FFFFC, &r) < 0);
		f.win2 = 0x400000u;
		CHECK(swl_rdx(&x, SWW_BAR2, 0x400000, 0, &r) == 0 && swl_rdx(&x, SWW_BAR2, 0x400000, 0x1FFFFC, &r) == 0 && swl_rdx(&x, SWW_BAR2, 0x400000, 0x200000, &r) < 0 && swl_rdx(&x, SWW_BAR2, 0x200000, 0, &r) < 0);
	}
}

int main(void)
{
	happy();
	fault_sweeps();
	deny_sweep();
	gates();
	steps();
	runb_happy();
	runb_full();
	runb_faults();
	runb_gates();
	runb_deny_sweep();
	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_wr_host_test (%u checks)\n", fails);
		return 1;
	}
	fprintf(stderr, "RESULT: PASS wlan_wr_host_test\n");
	return 0;
}
