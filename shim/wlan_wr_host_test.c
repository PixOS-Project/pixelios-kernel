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

static uint8_t imgbuf[SWW_IMG_MAX + 1];
static uint32_t imglen;
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
	unsigned src_flip_at;                           /* run B: after this store number one byte of the source image two words ahead flips (the source changes during the copy; 0 = never) */
	uint32_t *ram;                                  /* run B: backplane 0..0x5FFFFC as words (BAR2 offset + window = backplane address); NULL = the two words of run A */
	struct wrec lastw;                              /* run D: the last store that reached the bus (w[] keeps only the first 512) */
	unsigned rom0;                                  /* run D: a store to backplane address 0 is accepted and ignored (a ROM bank aliased at 0, unknown X4) */
	unsigned clob_at;                               /* run C: after this store number the RAM word at backplane address clob_bp flips a bit (a later store clobbering an earlier one; 0 = never) */
	uint32_t clob_bp;
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
	f->lastw.space = space; f->lastw.win = win; f->lastw.off = off; f->lastw.val = v;
	f->nw++;
	if (f->src_flip_at == f->nw && space == SWW_BAR2 && win == 0 && off >= SWW_RAM_BASE)
		imgbuf[off - SWW_RAM_BASE + 8] ^= 0x40;
	if (f->fail_wr_at == f->nw)
		return -1;
	if (f->ignore_writes || f->drop_at == f->nw)
		return 0;
	if (f->corrupt_at == f->nw)
		v ^= 1u;
	if (space == SWW_CFG && off == SWW_CFG_WIN) { if (!f->win_stuck) f->win0 = v; return 0; }
	if (space == SWW_CFG && off == SWW_CFG_WIN2) { if (!f->win2_stuck || f->nw < f->win2_stuck) f->win2 = v; return 0; }
	if (space == SWW_BAR2 && f->ram) {
		if (f->rom0 && win + off == 0 && f->win2 == win)
			return 0;
		if (f->win2 != win || (win + off) / 4 >= RAM_WORDS)
			return -1;
		f->ram[(win + off) / 4] = v;
		if (f->clob_at == f->nw)
			f->ram[f->clob_bp / 4] ^= 1u;
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
	      swl_wr_ok(SWW_BAR0, 0x18000000, 0x600, 0) < 0 && swl_wr_ok(SWW_BAR0, 0x18012000, 0x600, 0) < 0 && swl_wr_ok(SWW_BAR2, 0, 0x1FFFFC, 0) < 0 && swl_wr_ok(SWW_BAR2, 0, 4, 0) < 0 &&
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
	/* real firmware has zero words, and 0xFFFFFFFF is the dead-read value: word 0, the first word of a chunk and a mid-chunk word are 0, word 7 is 0xFFFFFFFF */
	for (i = 0; i < 4; i++) {
		static const uint32_t zw[3] = { 0, SWW_CHUNK_W, SWW_CHUNK_W + 5000 };

		if (i < 3 && 4 * zw[i] + 4 <= len)
			memset(imgbuf + 4 * zw[i], 0, 4);
		if (i == 3 && 4 * 7 + 4 <= len)
			memset(imgbuf + 4 * 7, 0xFF, 4);
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
	CHECK(runb(&f, &x, 4, SWW_IMG_MAX + 1, tcrc(imgbuf, SWW_IMG_MAX + 1)) < 0 && x.stop_code == SWW_E_IMG && f.nw == 0 && f.win2 == 0 && x.row == 8 && ram_ok(0, 0));       /* the CRC is right: only the size bound refuses it */
	CHECK(strstr(logbuf, "stopped at W8 restore=none") != 0);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, 0, 0) < 0 && x.stop_code == SWW_E_IMG && f.nw == 0);
	obsb(&f, 336);
	CHECK(runb(&f, &x, 4, SWW_IMG_MAX, crc ^ 1u) < 0 && x.stop_code == SWW_E_IMG && f.nw == 0 && x.img_next == 0 && ram_ok(0, 0));
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
	/* the source changes after swl_img_check: every chunk CRC still equals the (changed) source, only the final compare against the container's CRC catches it */
	obsb(&f, 336);
	f.src_flip_at = NWANT + NB_TAIL + 20000;
	CHECK(runb(&f, &x, 4, len, crc) < 0 && x.stop_code == SWW_E_READBACK && x.row == 10 && f.win2 == 0 && strstr(logbuf, "W10 read-back CRC ") != 0);
	mkimg(len);
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
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x9FFFCu, 1) == -SWW_E_LEVEL && swl_wr(&x, SWW_BAR2, 0x400000, 0x200000u, 0) == -SWW_E_STOPPED && f.nw == 0);       /* value 1: a run C tail tuple matches, level 4 refuses it */
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
					want = off == 0 || (off >= 0x200000u && off <= 0x3FFFFCu && (off < 0x3AEAB4u || v == 0));      /* offset 0: the run D reset vector, any value (swl_wr decides the value) */
				else if (w == 0x400000u)
					want = off <= 0x1FFFFCu;        /* v == 0: the last word of run B; any v: the run C tail tuples (their address and word are decided at run time) */
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
	/* every run B tuple: run 2, a row of W8..W10; every run C tuple: run 3, a row of W11..W13, and only in the window 0x400000 or on cfg 0x84 */
	for (v = SWT_N_A; v < SWT_N_B; v++)
		CHECK(swl_wt[v].run == 2 && swl_wt[v].row >= 8 && swl_wt[v].row <= 10 && (swl_wt[v].space == SWW_BAR2 || swl_wt[v].space == SWW_CFG));
	for (v = SWT_N_B; v < SWT_N_C; v++)
		CHECK(swl_wt[v].run == 3 && swl_wt[v].row >= 11 && swl_wt[v].row <= 13 && ((swl_wt[v].space == SWW_BAR2 && swl_wt[v].win == 0x400000u) || (swl_wt[v].space == SWW_CFG && swl_wt[v].off == 0x84)));
	/* the run D tuple: run 4, row 14, once, a reset-vector value decided at run time, BAR2 window 0 offset 0 exactly, after W13 */
	CHECK(SWT_N == SWT_N_C + 1 && SWT_RESET0 == SWT_N_C && swl_wt[SWT_RESET0].run == 4 && swl_wt[SWT_RESET0].row == 14 && swl_wt[SWT_RESET0].space == SWW_BAR2 && swl_wt[SWT_RESET0].win == 0 && swl_wt[SWT_RESET0].off == 0 &&
	      swl_wt[SWT_RESET0].hi == 0 && swl_wt[SWT_RESET0].flags == (SWT_ONCE | SWT_RV) && swl_wt[SWT_RESET0].need == SWF_W13 && swl_wt[SWT_RESET0].pred1 == 0 && swl_wt[SWT_RESET0].rgrp == 0);
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

/* ---- run C (patch 0131, docs/research/43 rows W11..W13): NVRAM text, length token, rTLV ---- */

#define NVMAXB (SWW_NV_VARS_MAX + 16u)
static uint8_t nvtext[NVMAXB];                  /* the container's NVRM entry: a synthetic text, never a real calibration file */
static uint32_t nvlen, nvcrc;
static uint8_t nvscr[SWW_NV_BUF];
static uint8_t nvbig[SWW_NV_BUF + 64];
static uint8_t rngb[SWW_RTLV_RNG];              /* what the injected generator returns */
static int rng_fail, rng_calls, rng_zero;

static int t_rng(void *c, uint8_t *buf, uint32_t n)
{
	uint32_t i;

	(void)c;
	rng_calls++;
	if (rng_fail)
		return -1;
	for (i = 0; i < n; i++)
		buf[i] = rng_zero ? 0 : rngb[i];
	return 0;
}

static uint32_t le32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* the oracle: what must end up in RAM, written from the DHD layout and not from the header (docs/research/43 s.2, note 43b): the text zero padded to varsize at ram_end - 4 - varsize, the token
 * (~w << 16 | w, w = varsize / 4) at ram_end - 4, the rTLV (128 random bytes, 0x80, 0xFEEDC0DE) 136 bytes below the text. Literals are used in the cases below. */
static struct {
	uint32_t units, end, varsize, rt, vars, tok, vw, token, crc;
	uint8_t txt[NVMAXB];
} cx;

static void cx_geo(uint32_t units)
{
	cx.units = units;
	cx.end = SWW_RAM_BASE + units * 8192u;
	cx.tok = cx.end - 4u;
	cx.vars = cx.tok - cx.varsize;
	cx.rt = cx.vars - 136u;
	cx.vw = cx.varsize / 4u;
	cx.token = ((~cx.vw) << 16) | (cx.vw & 0xFFFFu);
}

static void cx_init_rng(void)
{
	uint32_t i;

	for (i = 0; i < SWW_RTLV_RNG; i++)
		rngb[i] = (uint8_t)(i * 37u + 11u);
	for (i = 0; i < SWW_RTLV_RNG / 4; i++)
		CHECK(le32(rngb + 4 * i) != PAT && le32(rngb + 4 * i) != PATX && le32(rngb + 4 * i) != 0 && le32(rngb + 4 * i) != 0xFFFFFFFFu);
	rng_fail = rng_zero = 0;
}

/* a text of exactly n bytes of lines "kN=V" (no CR, comment or blank line): every byte is kept, each LF (and the forced last NUL) becomes a NUL, so varsize = roundup4(n) + 4 */
static void cx_plain(uint32_t n, uint32_t units)
{
	uint32_t o = 0, i, line = 0;

	memset(nvtext, 0, sizeof nvtext);
	while (o < n) {
		char t[48];
		int m = snprintf(t, sizeof t, "k%u=%u\n", line, line * 7919u % 1000u + 1000u);

		line++;
		for (i = 0; i < (uint32_t)m && o < n; i++)
			nvtext[o++] = (uint8_t)t[i];
	}
	nvtext[n - 1u] = '\n';
	if (n > 1 && nvtext[n - 2u] == '\n')
		nvtext[n - 2u] = 'x';
	nvlen = n;
	nvcrc = tcrc(nvtext, n);
	memset(cx.txt, 0, sizeof cx.txt);
	for (i = 0; i < n; i++)
		cx.txt[i] = nvtext[i] == '\n' ? 0 : nvtext[i];
	cx.varsize = ((n + 3u) & ~3u) + 4u;
	cx_geo(units);
	cx.crc = tcrc(cx.txt, cx.varsize);
}

/* a text with CR, a comment and a blank line; by hand from bcmutils.c:5101-5146 it becomes "k=v\0z=1\0" (the final LF is the forced NUL, kept) = 8 bytes, +1 NUL = varsz 9, varsize 12 */
static void cx_hand(uint32_t units)
{
	static const char t[] = "k=v\r\n# c\r\n\r\nz=1\r\n";

	memset(nvtext, 0, sizeof nvtext);
	memcpy(nvtext, t, sizeof t - 1u);
	nvlen = (uint32_t)sizeof t - 1u;
	CHECK(nvlen == 17);
	nvcrc = tcrc(nvtext, nvlen);
	memset(cx.txt, 0, sizeof cx.txt);
	memcpy(cx.txt, "k=v\0z=1\0", 8);
	cx.varsize = 12;
	cx_geo(units);
	cx.crc = tcrc(cx.txt, cx.varsize);
}

static int cx_tail(uint32_t bp, uint32_t *v)
{
	uint32_t i;

	if (bp == cx.tok) {
		*v = cx.token;
		return 1;
	}
	if (bp >= cx.vars && bp < cx.tok) {
		*v = le32(cx.txt + (bp - cx.vars));
		return 1;
	}
	if (bp >= cx.rt && bp < cx.vars) {
		i = (bp - cx.rt) / 4u;
		*v = i < 32 ? le32(rngb + 4u * i) : i == 32 ? 0x80u : 0xFEEDC0DEu;
		return 1;
	}
	return 0;
}

/* the whole RAM: the image words, the tail words (mode 0: all as expected; mode 1 after a fault: each untouched, expected, or expected with bit 0 flipped), everything else untouched */
static int ramc_ok(uint32_t words, int mode)
{
	uint32_t a, e;

	for (a = 0; a < RAM_WORDS; a++) {
		uint32_t i = a - SWW_RAM_BASE / 4u, v = ramarr[a];

		if (a >= SWW_RAM_BASE / 4u && i < words) {
			if (v != iw(i))
				return 0;
		} else if (cx_tail(4u * a, &e)) {
			uint32_t pre = 4u * a == cx.tok ? 0u : PAT;

			if (mode == 0 ? v != e : (v != e && v != pre && v != (e ^ 1u)))
				return 0;
		} else if (v != PAT) {
			return 0;
		}
	}
	return 1;
}

static struct swl_wbus cbus;

static void mkc(struct swl_wx *x, struct fake *f, int level, uint32_t len, uint32_t crc)
{
	bus(&cbus, f);
	cbus.rng = t_rng;
	mkx(x, &cbus, level);
	x->img = len ? imgbuf : 0;
	x->img_len = len;
	x->img_crc = crc;
	x->nv_src = nvlen ? nvtext : 0;
	x->nv_src_len = nvlen;
	x->nv_src_crc = nvcrc;
	x->nv = nvscr;
	x->nv_cap = sizeof nvscr;
	loglen = 0; logbuf[0] = 0; rng_calls = 0;
}

static int runc(struct fake *f, struct swl_wx *x, int level, uint32_t len, uint32_t crc)
{
	mkc(x, f, level, len, crc);
	return swl_stage3(x);
}

#define CLEVEL5 (0x1FFu | SWF_W8 | SWF_W9 | SWF_W10 | SWF_W11 | SWF_W12 | SWF_RTLV | SWF_W13)

static void runc_happy(void)
{
	static struct fake f, g;
	struct swl_wx x;
	uint32_t len = 66, words = (len + 3) / 4, crc, nw4, i, k, e;
	char t[128];

	cx_init_rng();
	mkimg(len);
	crc = tcrc(imgbuf, len);
	CHECK(words == 17);
	/* the hand-processed text, ram_end 0x4a0000 (the observed size); the literals are the layout worked out by hand */
	cx_hand(336);
	CHECK(cx.varsize == 12 && cx.tok == 0x49FFFCu && cx.vars == 0x49FFF0u && cx.rt == 0x49FF68u && cx.token == 0xFFFC0003u);
	obsb(&g, 336);
	CHECK(runc(&g, &x, 4, len, crc) == 0 && rng_calls == 0 && x.nv_varsize == 0 && x.tok == 0 && x.flags == (0x1FFu | SWF_W8 | SWF_W9 | SWF_W10));      /* level 4: nothing of run C */
	nw4 = g.nw;
	CHECK(nw4 == NWANT + NB_TAIL + words);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 5, len, crc) == 0);
	CHECK(x.stopped == 0 && x.restore == SWW_RS_NONE && rng_calls == 1 && x.flags == CLEVEL5 && x.armed == 0);
	CHECK(x.nv_varsize == 12 && x.tok == 0xFFFC0003u && x.nv_crc == cx.crc && x.nv_crc_rb == cx.crc && memcmp(x.rtlv, rngb, sizeof rngb) == 0 && memcmp(x.nv, cx.txt, 12) == 0);
	CHECK(f.nw == nw4 + 3 + 37 && x.nstore == f.nw && f.win2 == 0 && all_allowed(&f));
	/* the stores of run C, one by one: window, 3 text words, token, 34 rTLV words, window back */
	CHECK(f.w[nw4].space == SWW_CFG && f.w[nw4].off == 0x84 && f.w[nw4].val == 0x400000u);
	for (i = 0; i < 3; i++)
		CHECK(f.w[nw4 + 1 + i].space == SWW_BAR2 && f.w[nw4 + 1 + i].win == 0x400000u && f.w[nw4 + 1 + i].off == 0x9FFF0u + 4 * i && f.w[nw4 + 1 + i].val == le32(cx.txt + 4 * i));
	CHECK(f.w[nw4 + 4].space == SWW_BAR2 && f.w[nw4 + 4].win == 0x400000u && f.w[nw4 + 4].off == 0x9FFFCu && f.w[nw4 + 4].val == 0xFFFC0003u);
	for (i = 0; i < 34; i++) {
		k = nw4 + 5 + i;
		e = i < 32 ? le32(rngb + 4 * i) : i == 32 ? 0x80u : 0xFEEDC0DEu;
		CHECK(f.w[k].space == SWW_BAR2 && f.w[k].win == 0x400000u && f.w[k].off == 0x9FF68u + 4 * i && f.w[k].val == e);
	}
	CHECK(f.w[nw4 + 39].space == SWW_CFG && f.w[nw4 + 39].off == 0x84 && f.w[nw4 + 39].val == 0);
	CHECK(ramc_ok(words, 0) && ramarr[0x49FFFCu / 4] == 0xFFFC0003u && ramarr[0x49FF68u / 4 + 32] == 0x80u && ramarr[0x49FF68u / 4 + 33] == 0xFEEDC0DEu);
	CHECK(ramarr[0x49FFF0u / 4] == 0x00763D6Bu && ramarr[0x49FFF4u / 4] == 0x00313D7Au && ramarr[0x49FFF8u / 4] == 0);       /* "k=v\0", "z=1\0" and the zero pad */
	snprintf(t, sizeof t, "SWEET WLAN: stage 3 level 5 done vars=12 token=0xfffc0003 crc=%#010x\n", cx.crc);
	CHECK(strstr(logbuf, t) != 0 && strstr(logbuf, "level 4 done") == 0 && strstr(logbuf, "W11 NVRAM text bp 0x49fff0..0x49fff8 varsize 12 ") != 0 && strstr(logbuf, "W12 token 0xfffc0003 at bp 0x49fffc read back") != 0);
	CHECK(strstr(logbuf, "W13 rTLV bp 0x49ff68..0x49ffec (value 128 B, length word 0x80, type 0xfeedc0de) read back") != 0 && strstr(logbuf, "W13 cfg 0x84 := 0 (window restored)") != 0);
	CHECK(strstr(logbuf, "W11 NVRAM 17 bytes -> varsz 9 varsize 12 token 0xfffc0003") != 0);
	/* levels 3 and 4 with the NVRAM fields handed over: no entropy drawn, no store beyond their own, nothing prepared */
	obsb(&f, 336);
	CHECK(runc(&f, &x, 3, len, crc) == 0 && f.nw == NWANT && rng_calls == 0 && x.nv_varsize == 0 && ram_ok(0, 0));
	/* a plain text of several lengths (the pad variants), the tail at the top of a 6 MiB RAM, the real calibration file's size, the largest text */
	{
		static const uint32_t n[] = { 1, 2, 3, 4, 5, 150, 151, 152, 153, 4096, 8048, 23533, 27064 };
		static const uint32_t u[] = { 336, 336, 336, 336, 336, 336, 512, 336, 336, 400, 257, 336, 336 };
		unsigned j;

		for (j = 0; j < sizeof n / sizeof n[0]; j++) {
			cx_plain(n[j], u[j]);
			obsb(&f, u[j]);
			CHECK(runc(&f, &x, 5, len, crc) == 0 && f.nw == nw4 + cx.vw + 37 && x.nv_varsize == cx.varsize && x.nv_crc_rb == cx.crc && ramc_ok(words, 0) && f.win2 == 0);
			CHECK(x.tok == cx.token && ramarr[cx.tok / 4] == cx.token && x.tn[SWK_VARS] == cx.vw && x.tn[SWK_TOKEN] == 1 && x.tn[SWK_RTLV] == 34);
		}
	}
	/* the window boundary: the tail starts exactly at 0x400000 (text of 8048 bytes, ram_end 0x402000), one word lower is refused after W7 and before the first image word */
	cx_plain(8048, 257);
	CHECK(cx.rt == 0x400000u);
	obsb(&f, 257);
	CHECK(runc(&f, &x, 5, len, crc) == 0 && ramc_ok(words, 0));
	cx_plain(8052, 257);
	CHECK(cx.rt == 0x3FFFFCu);
	obsb(&f, 257);
	CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_GEO && f.nw == NWANT && x.row == 11 && strstr(logbuf, "W11 tail geometry refused: ram_end 0x402000 varsize 8056") != 0 && strstr(logbuf, "stopped at W11 restore=none") != 0);
	CHECK(f.win2 == 0 && ram_ok(0, 0) && x.img_next == 0);
	cx_plain(300, 256);                                     /* ram_end 0x400000 and below: the tail is not in the window */
	obsb(&f, 256);
	CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_GEO && f.nw == NWANT && ram_ok(0, 0));
	cx_plain(300, 224);
	obsb(&f, 224);
	CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_GEO && f.nw == NWANT && ram_ok(0, 0));
}

/* the entry checks, all before W0: nothing is stored or read when one fails, and the prepared state stays empty */
static void runc_prep(void)
{
	static struct fake f;
	struct swl_wx x;
	uint32_t len = 66, crc;
	unsigned step;
	/* step 0 is the good one; each other step breaks one thing: the text size, the code and a text of the log */
	static const struct { uint32_t n; int code; const char *text; } want[] = {
		{ 300, 0, "" },
		{ 300, SWW_E_NV, "no usable NVRAM entry" },                     /* 1: no entry */
		{ 300, SWW_E_NV, "no usable NVRAM entry" },                     /* 2: empty entry */
		{ 27069, SWW_E_NV, "no usable NVRAM entry" },                   /* 3: one byte above the input bound, right CRC: only the size refuses it */
		{ 300, SWW_E_NV, "no usable NVRAM entry" },                     /* 4: no scratch */
		{ 300, SWW_E_NV, "no usable NVRAM entry" },                     /* 5: scratch one byte short of len + 8 */
		{ 300, SWW_E_NV, "NVRAM CRC " },                                /* 6: wrong container CRC */
		{ 300, SWW_E_NV, "NVRAM CRC " },                                /* 7: a changed byte under the old CRC */
		{ 300, SWW_E_NV, "gives varsz 0" },                             /* 8: nothing left after processing */
		{ 27065, SWW_E_NV, "varsize 27072" },                           /* 9: processed 27065 -> varsz 27069 -> varsize 27072 > 27068 */
		{ 300, SWW_E_RNG, "no random bytes" },                          /* 10: no generator */
		{ 300, SWW_E_RNG, "no random bytes" },                          /* 11: the generator fails */
		{ 300, SWW_E_RNG, "all zero" },                                 /* 12: the generator returns zeros */
		{ 300, 0, "" },                                                 /* 13: scratch of exactly len + 8 */
		{ 27068, SWW_E_NV, "varsize 27072" },                           /* 14: exactly the input bound: passes the size check, refused by the varsize bound */
		{ 27064, 0, "" },                                               /* 15: the largest text that fits: varsize 27068 */
	};

	cx_init_rng();
	mkimg(len);
	crc = tcrc(imgbuf, len);
	for (step = 0; step < sizeof want / sizeof want[0]; step++) {
		int rc;

		cx_plain(want[step].n, 336);
		obsb(&f, 336);
		mkc(&x, &f, 5, len, crc);
		x.nv_varsize = 99;
		rng_fail = rng_zero = 0;
		switch (step) {
		case 1: x.nv_src = 0; break;
		case 2: x.nv_src_len = 0; break;
		case 3: x.nv = nvbig; x.nv_cap = sizeof nvbig; break;           /* a scratch that would hold it: only the input bound refuses */
		case 4: x.nv = 0; break;
		case 5: x.nv_cap = nvlen + 7; break;
		case 6: x.nv_src_crc ^= 1u; break;
		case 7: nvtext[5] ^= 0x20; break;
		case 8: memset(nvtext, 'x', nvlen); nvtext[0] = '#'; nvtext[nvlen - 1u] = '\n'; x.nv_src_crc = tcrc(nvtext, nvlen); break;       /* one comment line: its LF is the forced NUL, the comment never ends */
		case 10: cbus.rng = 0; break;
		case 11: rng_fail = 1; break;
		case 12: rng_zero = 1; break;
		case 13: x.nv_cap = nvlen + 8; break;
		default: break;
		}
		memset(nvscr, 0xEE, sizeof nvscr);
		rc = swl_stage3(&x);
		if (want[step].code == 0) {
			CHECK(rc == 0 && x.nv_varsize == cx.varsize && x.nv_crc == cx.crc && x.tok == cx.token && memcmp(x.nv, cx.txt, cx.varsize) == 0 && memcmp(x.rtlv, rngb, sizeof rngb) == 0 && rng_calls == 1);
			CHECK(ramc_ok(17, 0));                                  /* and the whole of run C was done on the fake */
		} else {
			CHECK(rc < 0 && x.stop_code == want[step].code && f.nw == 0 && f.nrd == 0 && x.row == 11 && x.nv_varsize == 0 && x.restore == SWW_RS_NONE && ram_ok(0, 0));
			CHECK(strstr(logbuf, want[step].text) != 0 && strstr(logbuf, "stopped at W11 restore=none") != 0);
			CHECK(rng_calls == (step == 11 || step == 12 ? 1 : 0));
			if (step == 14)
				CHECK(strstr(logbuf, "no usable NVRAM entry") == 0);
		}
	}
	/* the processed text of the hand case is what the scratch holds, byte for byte: zero padded up to varsize, untouched after len + 8 */
	cx_hand(336);
	obsb(&f, 336);
	mkc(&x, &f, 5, len, crc);
	memset(nvscr, 0xEE, sizeof nvscr);
	x.row = 11;
	CHECK(swl_nv_prep(&x) == 0 && x.nv_varsize == 12 && memcmp(nvscr, "k=v\0z=1\0\0\0\0\0", 12) == 0 && nvscr[16] == 0 && nvscr[24] == 0 && nvscr[25] == 0xEE);
	CHECK(x.nv_crc == tcrc(cx.txt, 12));
}

/* a dropped, corrupted or refused store at every position of run C, a failing read at every position, a window that does not go back, a later store that damages an earlier word */
static void runc_faults(void)
{
	static struct fake f, g;
	struct swl_wx x;
	uint32_t len = 66, words = 17, crc, nw4, nrd4, nrd5, p, k, vw, nst;
	const uint32_t B = 0x1FFu | SWF_W8 | SWF_W9 | SWF_W10;     /* run B done */
	unsigned mode, c;

	cx_init_rng();
	mkimg(len);
	crc = tcrc(imgbuf, len);
	cx_plain(150, 336);
	vw = cx.vw;
	CHECK(vw == 39 && cx.varsize == 156);
	obsb(&g, 336);
	CHECK(runc(&g, &x, 4, len, crc) == 0);
	nw4 = g.nw;
	nrd4 = g.nrd;
	obsb(&g, 336);
	CHECK(runc(&g, &x, 5, len, crc) == 0);
	nrd5 = g.nrd;
	nst = vw + 37;
	CHECK(g.nw == nw4 + nst && nrd5 > nrd4 + 3 * vw);
	for (p = 1; p <= nst; p++) {
		/* store p of run C: 1 window, 2..1+vw text, 2+vw token, 3+vw..36+vw rTLV, 37+vw window back */
		uint32_t j = nw4 + p, stored = p < 2 ? 0 : p - 2 > vw + 35 ? vw + 35 : p - 2;
		unsigned row = p <= 1 + vw ? 11 : p == 2 + vw ? 12 : 13;
		uint32_t fl = p <= 1 + vw ? B : p == 2 + vw ? B | SWF_W11 : p < nst ? B | SWF_W11 | SWF_W12 : B | SWF_W11 | SWF_W12 | SWF_RTLV;

		for (mode = 0; mode < 3; mode++) {
			obsb(&f, 336);
			if (mode == 0) f.drop_at = j; else if (mode == 1) f.corrupt_at = j; else f.fail_wr_at = j;
			CHECK(runc(&f, &x, 5, len, crc) < 0);
			CHECK(x.stopped == 1 && x.row == row && x.stop_code == (mode == 2 ? SWW_E_WR : SWW_E_READBACK) && x.restore == SWW_RS_OK && f.win2 == 0 && x.flags == fl);
			CHECK(f.nw == j + 1 && only_restore_after(&f, j) && all_allowed(&f) && x.tn[SWK_VARS] + x.tn[SWK_TOKEN] + x.tn[SWK_RTLV] == stored && ramc_ok(words, 1));
		}
	}
	/* a failing read at every position after run B */
	for (k = nrd4 + 1; k <= nrd5; k++) {
		obsb(&f, 336);
		f.fail_rd_at = k;
		CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stopped == 1 && x.stop_code != 0 && x.row >= 11 && f.win2 == 0 && all_allowed(&f) && ramc_ok(words, 1));
	}
	/* the window does not go back: the store of 0 and the restore both have no effect on the chip: reported, not hidden */
	obsb(&f, 336);
	f.win2_stuck = nw4 + nst;
	CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_READBACK && x.row == 13 && f.win2 == 0x400000u && x.restore == SWW_RS_FAILED && f.nw == nw4 + nst + 1 && ramc_ok(words, 0));
	CHECK(strstr(logbuf, "stopped at W13 restore=failed") != 0);
	/* the window does not move at all */
	obsb(&f, 336);
	f.win2_stuck = nw4 + 1;
	CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_READBACK && x.row == 11 && f.win2 == 0 && f.nw == nw4 + 2 && x.tn[SWK_VARS] == 0);
	/* a later store damages an earlier word (bit 0 flips after store number nw4 + after): found by the verify that comes next */
	{
		const struct { uint32_t after, bp; unsigned row; uint32_t fl; } cl[] = {
			{ 1 + vw, cx.vars, 11, B },                                     /* after the last text word: the text's own verify in W11 (W13 would be row 13) */
			{ 2 + vw, cx.tok, 12, B | SWF_W11 },                            /* after the token: the verify of W12 */
			{ 3 + vw, cx.tok, 13, B | SWF_W11 | SWF_W12 },                  /* after the first rTLV word: the token verify of W13 */
			{ 36 + vw, cx.vars, 13, B | SWF_W11 | SWF_W12 },                /* after the last rTLV word: the text, in W13 */
			{ 36 + vw, cx.rt + 40, 13, B | SWF_W11 | SWF_W12 },             /* ... an rTLV word */
			{ 36 + vw, cx.tok, 13, B | SWF_W11 | SWF_W12 },                 /* ... the token */
			{ 36 + vw, cx.vars + 4 * (vw - 1), 13, B | SWF_W11 | SWF_W12 }, /* ... the last text word (the CRC covers all of it) */
			{ 36 + vw, cx.rt, 13, B | SWF_W11 | SWF_W12 },                  /* ... the first rTLV word */
			{ 36 + vw, cx.rt + 128, 13, B | SWF_W11 | SWF_W12 },            /* ... the rTLV length word */
			{ 36 + vw, cx.rt + 132, 13, B | SWF_W11 | SWF_W12 },            /* ... the rTLV type word (the last one) */
		};

		for (c = 0; c < sizeof cl / sizeof cl[0]; c++) {
			obsb(&f, 336);
			f.clob_at = nw4 + cl[c].after;
			f.clob_bp = cl[c].bp;
			CHECK(runc(&f, &x, 5, len, crc) < 0 && x.stop_code == SWW_E_READBACK && x.row == cl[c].row && f.win2 == 0 && x.restore == SWW_RS_OK && x.flags == cl[c].fl && ramc_ok(words, 1));
		}
	}
}

/* the allowlist decision for the run C tuples: which tuple a (space, window, offset, value) belongs to, restated with literals (windows 0x400000 up to offset 0x1FFFFC, cfg 0x84) */
static void runc_deny_sweep(void)
{
	CHECK(swl_wr_ok(SWW_BAR2, 0x400000, 0, 5) == SWT_NV_VARS && swl_wr_ok(SWW_BAR2, 0x400000, 4, 5) == SWT_NV_VARS && swl_wr_ok(SWW_BAR2, 0x400000, 0x9FFF0u, 5) == SWT_NV_VARS);
	CHECK(swl_wr_ok(SWW_BAR2, 0x400000, 0x1FFFF8u, 5) == SWT_NV_VARS && swl_wr_ok(SWW_BAR2, 0x400000, 0x1FFFFCu, 5) == SWT_NV_TOKEN && swl_wr_ok(SWW_BAR2, 0x400000, 0x200000u, 5) < 0);
	CHECK(swl_wr_ok(SWW_BAR2, 0x400000, 0, 0) == SWT_LAST_HI && swl_wr_ok(SWW_BAR2, 0x400000, 0x1FFFFCu, 0) == SWT_LAST_HI);       /* a zero is the last word of run B first */
	CHECK(swl_wr_ok_from(SWT_LAST_HI + 1, SWW_BAR2, 0x400000, 0x9FFF0u, 0) == SWT_NV_VARS && swl_wr_ok_from(SWT_NV_VARS + 1, SWW_BAR2, 0x400000, 0x9FFF0u, 0) == SWT_NV_TOKEN && swl_wr_ok_from(SWT_NV_TOKEN + 1, SWW_BAR2, 0x400000, 0x9FFF0u, 0) == SWT_NV_RTLV);
	CHECK(swl_wr_ok_from(SWT_NV_VARS + 1, SWW_BAR2, 0x400000, 0x1FFFFCu, 5) == SWT_NV_TOKEN && swl_wr_ok_from(SWT_NV_TOKEN + 1, SWW_BAR2, 0x400000, 0x1FFFFCu, 5) < 0);
	CHECK(swl_wr_ok(SWW_BAR2, 0, 0x3B0000u, 5) < 0 && swl_wr_ok(SWW_BAR2, 0, 0, 5) == SWT_RESET0 && swl_wr_ok(SWW_BAR2, 0, 4, 5) < 0 && swl_wr_ok(SWW_BAR2, 0x200000u, 0, 5) < 0 && swl_wr_ok(SWW_BAR0, 0x400000, 0, 5) < 0 && swl_wr_ok(SWW_ATU, 0x400000, 0, 5) < 0);
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x84, 0x400000) == SWT_WIN2_TAIL && swl_wr_ok_from(SWT_WIN2_TAIL + 1, SWW_CFG, 0, 0x84, 0x400000) == SWT_WIN2_TAIL_C && swl_wr_ok_from(SWT_WIN2_TAIL_C + 1, SWW_CFG, 0, 0x84, 0x400000) < 0);
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x84, 0) == SWT_WIN2_ZERO && swl_wr_ok_from(SWT_WIN2_ZERO + 1, SWW_CFG, 0, 0x84, 0) == SWT_WIN2_ZERO_C && swl_wr_ok_from(SWT_WIN2_ZERO_C + 1, SWW_CFG, 0, 0x84, 0) < 0);
	{
		unsigned v;

		for (v = 0; v < SWT_N; v++)             /* a tail tuple names one of the three kinds (x->tn has a slot for each), no other tuple names one */
			CHECK(((swl_wt[v].flags & SWT_TW) != 0) == (v >= SWT_NV_VARS && v <= SWT_NV_RTLV) && (!(swl_wt[v].flags & SWT_TW) || (swl_wt[v].kind >= SWK_VARS && swl_wt[v].kind <= SWK_RTLV)) &&
			      (!!(swl_wt[v].flags & SWT_TW) || swl_wt[v].kind == 0));
	}
	CHECK(SWT_N == 28 && SWT_N_C == 27 && SWT_N_B == 22 && SWT_WIN2_TAIL_C == 22 && SWT_NV_VARS == 23 && SWT_NV_TOKEN == 24 && SWT_NV_RTLV == 25 && SWT_WIN2_ZERO_C == 26);
}

/* the stores that reached the bus other than the restore of cfg 0x84 (a refused store with the window moved runs that restore) */
static unsigned nonrestore(const struct fake *f)
{
	unsigned i, n = 0;

	for (i = 0; i < f->nw && i < sizeof f->w / sizeof f->w[0]; i++)
		n += !is_restore(&f->w[i]);
	return n;
}

/* the guard rules of swl_wr() for the run C tuples and the helpers they use, each in isolation. Geometry of the hand text at ram_end 0x4a0000: rTLV 0x49ff68..0x49ffef, text 0x49fff0..0x49fffb, token 0x49fffc */
static void runc_gates(void)
{
	static struct fake f;
	struct swl_wx x;
	uint32_t len = 66, crc, n, w0, w1, i;
	const uint32_t RB = 0x1FFu | SWF_W8 | SWF_W9 | SWF_W10;      /* run A and B done */
	const uint32_t OFV = 0x9FFF0u, OFT = 0x9FFFCu, OFR = 0x9FF68u;      /* BAR2 offsets in window 0x400000: text, token, rTLV */
	const uint32_t WU = 1u << SWT_WIN2_TAIL_C, B_ = (1u << SWT_WIN2_TAIL) | (1u << SWT_WIN2_ZERO);

	cx_init_rng();
	mkimg(len);
	crc = tcrc(imgbuf, len);
#define GCN(units, fl) do { obsb(&f, (units)); mkc(&x, &f, 5, len, crc); ready(&x, &f, (fl)); x.ram_bytes = (units) * 8192u; x.row = 11; CHECK(swl_nv_prep(&x) == 0); loglen = 0; logbuf[0] = 0; } while (0)
#define GC(units, fl) do { cx_hand(units); GCN(units, fl); } while (0)
#define GV(fl) do { GC(336, (fl)); f.win2 = 0x400000u; x.done = WU; x.armed = SWW_RG_W84; } while (0)       /* as the store of cfg 0x84 := 0x400000 leaves it */
	w0 = le32((const uint8_t *)"k=v\0");
	w1 = le32((const uint8_t *)"z=1\0");
	/* the geometry helper alone */
	GC(336, RB);
	{
		struct swl_geo g;

		CHECK(swl_geo_get(&x, &g) == 0 && g.tok == 0x49FFFCu && g.vars == 0x49FFF0u && g.rt == 0x49FF68u && g.vwords == 3);
		x.nv_varsize = 0;
		CHECK(swl_geo_get(&x, &g) == SWW_E_NV);
		x.nv_varsize = 27068u;
		CHECK(swl_geo_get(&x, &g) == 0 && g.vars == 0x49FFFCu - 27068u);
		x.nv_varsize = 27072u;
		CHECK(swl_geo_get(&x, &g) == SWW_E_NV);
		x.nv_varsize = 13u;
		CHECK(swl_geo_get(&x, &g) == SWW_E_NV);
		x.nv_varsize = 14u;
		CHECK(swl_geo_get(&x, &g) == SWW_E_NV);
		x.nv_varsize = 16u;
		CHECK(swl_geo_get(&x, &g) == 0 && g.vwords == 4 && g.vars == 0x49FFECu && g.rt == 0x49FFECu - 136u);
		x.nv_varsize = 12u;
		x.ram_bytes = 0x400000u;
		CHECK(swl_geo_get(&x, &g) == 0 && g.tok == 0x5FFFFCu);
		x.ram_bytes = 0x400001u;
		CHECK(swl_geo_get(&x, &g) == SWW_E_RAM);
		x.ram_bytes = 0x2000u;                                           /* ram_end 0x202000: far below the window */
		CHECK(swl_geo_get(&x, &g) == SWW_E_GEO);
		x.ram_bytes = 0x200000u + 12u + 140u;                            /* ram_end 0x400098: rt = 0x400098 - 4 - 12 - 136 = 0x400000, the lowest the tail may start */
		CHECK(swl_geo_get(&x, &g) == 0 && g.rt == 0x400000u);
		x.ram_bytes -= 4;
		CHECK(swl_geo_get(&x, &g) == SWW_E_GEO);
		/* the kind of every address around the regions */
		x.ram_bytes = 0x2A0000u;
		CHECK(swl_tw_kind(&x, 0x49FF64u) == 0 && swl_tw_kind(&x, 0x49FF68u) == SWK_RTLV && swl_tw_kind(&x, 0x49FFECu) == SWK_RTLV && swl_tw_kind(&x, 0x49FFF0u) == SWK_VARS);
		CHECK(swl_tw_kind(&x, 0x49FFF8u) == SWK_VARS && swl_tw_kind(&x, 0x49FFFCu) == SWK_TOKEN && swl_tw_kind(&x, 0x4A0000u) == 0 && swl_tw_kind(&x, 0) == 0);
		x.nv_varsize = 0;
		CHECK(swl_tw_kind(&x, 0x49FFFCu) == 0);
	}
	/* W11, cfg 0x84 := 0x400000 (the second time cfg 0x84 is written this way: the run B tuple is consumed and the run C tuple takes over) */
	GC(336, RB); x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == 0 && f.win2 == 0x400000u && f.nw == 1 && (x.armed & SWW_RG_W84) && (x.done & WU));
	GC(336, RB); x.done = (1u << SWT_WIN2_TAIL) | WU;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_REPEAT && f.nw == 0);
	GC(336, RB); x.done = 1u << SWT_WIN2_TAIL; x.level = 4;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_REPEAT && f.nw == 0);               /* level 4 reports the repeat, it does not reach for the run C tuple */
	GC(336, RB & ~SWF_W10); x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_PHASE && f.nw == 0);
	GC(224, RB); x.done = 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_TAIL && f.nw == 0);
	GC(336, RB); x.done = 1u << SWT_WIN2_TAIL; f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_OLD && f.nw == 0);
	GC(336, RB); x.done = 1u << SWT_WIN2_TAIL; f.cmd = 6;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000) == -SWW_E_PRE && f.nw == 0);
	/* W11 text words */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == 0 && x.tn[SWK_VARS] == 1 && f.nw == 1 && f.nrd > 0 && ramarr[0x49FFF0u / 4] == w0 && (x.armed & SWW_RG_W84));
	n = f.nrd;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 4, w1) == 0 && x.tn[SWK_VARS] == 2 && f.nrd == n + 1 && ramarr[0x49FFF4u / 4] == w1);     /* a later word: only its read-back */
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 4, w1) == -SWW_E_NV && x.stopped && f.nw == 3 && f.win2 == 0 && x.restore == SWW_RS_OK);   /* the same word again: STOP, the window restore is the third store */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 4, w1) == -SWW_E_NV && nonrestore(&f) == 0);                                                 /* must start at the first word */
	GV(RB); x.done |= 1u << SWT_LAST_HI;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 8, 0) == -SWW_E_NV && nonrestore(&f) == 0);                                                 /* ... also with a zero word, once the last word of run B is consumed */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0 ^ 1u) == -SWW_E_NV && nonrestore(&f) == 0);                                                /* not the text word */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w1) == -SWW_E_NV && nonrestore(&f) == 0);                                                    /* a text word at the wrong place */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV - 4, w0) == -SWW_E_PHASE && nonrestore(&f) == 0 && ramarr[0x49FFECu / 4] == PAT);             /* one word below the text is the rTLV: the rTLV tuple (not yet allowed) judges it */
	GV(RB | SWF_W11 | SWF_W12);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV - 4, w0) == -SWW_E_NV && nonrestore(&f) == 0 && ramarr[0x49FFECu / 4] == PAT);                /* ... and refuses a text word there */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 1, w0) == -SWW_E_DENIED && nonrestore(&f) == 0);
	GV(RB & ~SWF_W10);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_PHASE && nonrestore(&f) == 0);
	GV(RB); x.done = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_ORDER && nonrestore(&f) == 0);
	GV(RB); x.level = 4;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_LEVEL && nonrestore(&f) == 0);
	GV(RB); f.win2 = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_WIN && nonrestore(&f) == 0);
	GV(RB); f.cmd = 6;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_PRE && nonrestore(&f) == 0);
	GV(RB); ramarr[0x49FFF0u / 4] = 0xFFFFFFFFu;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_DEAD && nonrestore(&f) == 0);
	GV(RB); x.tn[SWK_VARS] = 1; f.cmd = 6; ramarr[0x49FFF4u / 4] = 0xFFFFFFFFu;                                                    /* a later word: no precondition, no old-value read */
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 4, w1) == 0 && f.nrd == 1 && f.nw == 1);
	GV(RB); f.fail_rd_at = 1;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) < 0 && nonrestore(&f) == 0);
	/* a zero word of the text: the last word of run B (value 0, ONCE, after the run B window store) is the first match until it is consumed */
	GV(RB); x.tn[SWK_VARS] = 2;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 8, 0) == -SWW_E_ORDER && nonrestore(&f) == 0);
	GV(RB); x.tn[SWK_VARS] = 2; x.done |= 1u << SWT_WIN2_TAIL;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 8, 0) == -SWW_E_RAM && nonrestore(&f) == 0);
	GV(RB); x.tn[SWK_VARS] = 2; x.done |= 1u << SWT_LAST_HI;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV + 8, 0) == 0 && x.tn[SWK_VARS] == 3 && f.nw == 1);
	/* the step refuses a bad geometry before it moves the window */
	GC(336, RB); x.nv_varsize = 0;
	CHECK(swl_w11(&x) == -SWW_E_NV && f.nw == 0 && f.win2 == 0);
	GC(336, RB); x.ram_bytes = 0x2000u;
	CHECK(swl_w11(&x) == -SWW_E_GEO && f.nw == 0 && f.win2 == 0);
	/* the geometry and the prepared state are checked by the writer itself */
	GV(RB); x.nv_varsize = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB); x.nv_varsize = 27072;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB); x.nv_varsize = 14;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB); x.ram_bytes = 0x400000u + 8192u;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFV, w0) == -SWW_E_RAM && nonrestore(&f) == 0);
	cx_plain(20000, 257);                                                                                                       /* a text that does not fit above 0x400000 at ram_end 0x402000 */
	GCN(257, RB); f.win2 = 0x400000u; x.done = WU;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0x1000u, 5) == -SWW_E_GEO && nonrestore(&f) == 0);
	/* one word past the end of RAM, whatever the kind counters say */
	{
		static const uint32_t val[] = { 0, 0xFFFC0003u, 0xFEEDC0DEu, 0x80u };
		unsigned v;

		for (v = 0; v < sizeof val / sizeof val[0]; v++) {
			GV(RB | SWF_W11 | SWF_W12); x.done |= 1u << SWT_LAST_HI; x.tn[SWK_VARS] = 4; x.tn[SWK_TOKEN] = 1; x.tn[SWK_RTLV] = 38;
			CHECK(swl_wr(&x, SWW_BAR2, 0x400000, 0xA0000u, val[v]) < 0 && nonrestore(&f) == 0 && ramarr[0x4A0000u / 4] == PAT);
		}
	}
	/* the index of a kind is bounded by its word count: a RAM that ends at 0x5fe000 and a repeated token counter aimed at the top table offset 0x5ffffc (first match there: the token tuple) */
	cx_hand(511); GCN(511, RB | SWF_W11); f.win2 = 0x400000u; x.done = WU; x.tn[SWK_TOKEN] = 2048;
	CHECK(cx.tok == 0x5FDFFCu && swl_wr(&x, SWW_BAR2, 0x400000, 0x1FFFFCu, x.tok) == -SWW_E_NV && nonrestore(&f) == 0 && ramarr[0x5FFFFCu / 4] == PAT);
	/* W12 the token at the last RAM word (its old value 0 from W9) */
	GV(RB | SWF_W11);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == 0 && x.tn[SWK_TOKEN] == 1 && f.nw == 1 && f.nrd > 0 && ramarr[0x49FFFCu / 4] == 0xFFFC0003u);
	GV(RB | SWF_W11);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok ^ 1u) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB | SWF_W11);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, (~2u << 16) | 2u) == -SWW_E_NV && nonrestore(&f) == 0);                                    /* the token of another size */
	GV(RB);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_PHASE && nonrestore(&f) == 0);
	GV(RB | SWF_W11); f.cmd = 6;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_PRE && nonrestore(&f) == 0);
	GV(RB | SWF_W11); ramarr[0x49FFFCu / 4] = 0xFFFFFFFFu;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_DEAD && nonrestore(&f) == 0);
	GV(RB | SWF_W11); f.win2 = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_WIN && nonrestore(&f) == 0);
	GV(RB | SWF_W11); x.done = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_ORDER && nonrestore(&f) == 0);
	GV(RB | SWF_W11); x.level = 4;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT, x.tok) == -SWW_E_LEVEL && nonrestore(&f) == 0);
	GV(RB | SWF_W11);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFT - 4, x.tok) == -SWW_E_NV && nonrestore(&f) == 0);                                            /* the token's value at the last text word's place */
	/* W13 the rTLV: value words, the length word, the type word */
	GV(RB | SWF_W11 | SWF_W12);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == 0 && x.tn[SWK_RTLV] == 1 && f.nrd > 0 && f.nw == 1);
	n = f.nrd;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 4, le32(rngb + 4)) == 0 && f.nrd == n + 1);
	GV(RB | SWF_W11 | SWF_W12);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb) ^ 1u) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 4, le32(rngb + 4)) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB | SWF_W11);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == -SWW_E_PHASE && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); x.tn[SWK_RTLV] = 32;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 128, 0x80u) == 0 && x.tn[SWK_RTLV] == 33 && f.nrd == 1);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 132, 0xFEEDC0DEu) == 0 && x.tn[SWK_RTLV] == 34 && ramarr[0x49FFECu / 4] == 0xFEEDC0DEu);
	GV(RB | SWF_W11 | SWF_W12); x.tn[SWK_RTLV] = 32;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 128, 0x81u) == -SWW_E_NV && nonrestore(&f) == 0);                                          /* the length word has no checksum: exactly 0x80 */
	GV(RB | SWF_W11 | SWF_W12); x.tn[SWK_RTLV] = 33;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 132, 0xFEEDC0DFu) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); x.tn[SWK_RTLV] = 33;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR + 128, 0xFEEDC0DEu) == -SWW_E_NV && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); f.cmd = 6;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == -SWW_E_PRE && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); x.done = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == -SWW_E_ORDER && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); x.level = 4;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == -SWW_E_LEVEL && nonrestore(&f) == 0);
	GV(RB | SWF_W11 | SWF_W12); ramarr[0x49FF68u / 4] = 0xFFFFFFFFu;
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000, OFR, le32(rngb)) == -SWW_E_DEAD && nonrestore(&f) == 0);
	/* W13 the window back to 0: after the rTLV only, from 0x400000 only */
	GC(336, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = B_ | WU; f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == 0 && f.win2 == 0 && f.nw == 1);
	GC(336, RB | SWF_W11 | SWF_W12); x.done = B_ | WU; f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_PHASE && f.nw == 0);
	GC(336, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = (1u << SWT_WIN2_TAIL) | (1u << SWT_WIN2_ZERO); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_ORDER && f.nw == 0);
	GC(336, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = B_ | WU;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_OLD && f.nw == 0);
	GC(224, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = B_ | WU; f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_TAIL && f.nw == 0);
	GC(336, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = B_ | WU | (1u << SWT_WIN2_ZERO_C); f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_REPEAT && f.nw == 0);
	GC(336, RB | SWF_W11 | SWF_W12 | SWF_RTLV); x.done = B_ | WU; f.win2 = 0x400000u; x.level = 4;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_REPEAT && f.nw == 0);
	/* an address that is no tail address, and values for the tail window other than the tail: nothing is stored */
	GV(RB | SWF_W11 | SWF_W12);
	for (i = 0; i < 40; i++) {
		static const uint32_t off[] = { 0, 4, 0x1000, 0x9FF60, 0x9FF64, 0xA0000, 0xA0004, 0x1FFFFC, 0x200000 };
		unsigned o;

		for (o = 0; o < sizeof off / sizeof off[0]; o++) {
			x.stopped = 0; x.stop_code = 0; f.nw = 0; x.nstore = 0; x.armed = 0;
			CHECK(swl_wr(&x, SWW_BAR2, 0x400000, off[o], i * 0x01010101u) < 0 && f.nw == 0);
		}
	}
#undef GV
#undef GC
#undef GCN
}


/* ---------------------------------------------------------------- run D (patch 0132): W14, the reset vector at backplane address 0 ---------------------------------------------------------------- */

static uint32_t cstep(uint32_t c, const uint8_t *p, unsigned n)         /* the CRC32 register after n more bytes (no final xor) */
{
	unsigned k;

	while (n--) {
		c ^= *p++;
		for (k = 0; k < 8; k++)
			c = (c & 1u) ? (c >> 1) ^ 0xEDB88320u : c >> 1;
	}
	return c;
}

/* make the CRC32 of buf[0..len) equal target by choosing its last four bytes (the CRC is affine over GF(2): solve the 32 columns): a synthetic blob with the CRC of the real firmware, no firmware byte */
static void forge(uint8_t *buf, uint32_t len, uint32_t target)
{
	uint32_t s = cstep(0xFFFFFFFFu, buf, len - 4u), f0, basis[32], bm[32], v, m, want, b = 0;
	uint8_t t[4];
	unsigned j;
	int bit;

	memset(basis, 0, sizeof basis);
	memset(t, 0, 4);
	f0 = cstep(s, t, 4);
	for (j = 0; j < 32; j++) {
		memset(t, 0, 4);
		t[j / 8] = (uint8_t)(1u << (j % 8));
		v = cstep(s, t, 4) ^ f0;
		m = 1u << j;
		for (bit = 31; bit >= 0 && v; bit--) {
			if (!((v >> bit) & 1u))
				continue;
			if (!basis[bit]) {
				basis[bit] = v; bm[bit] = m;
				break;
			}
			v ^= basis[bit]; m ^= bm[bit];
		}
	}
	want = ~target ^ f0;
	for (bit = 31; bit >= 0; bit--) {
		if (((want >> bit) & 1u) && basis[bit]) {
			want ^= basis[bit]; b ^= bm[bit];
		}
	}
	CHECK(want == 0);
	for (j = 0; j < 4; j++)
		buf[len - 4u + j] = (uint8_t)(b >> (8 * j));
}

static struct fake sf;                                  /* the state after run C on the pinned image: every W14 case starts from it */
static struct swl_wx sx;
static uint32_t sram[RAM_WORDS];

static void rd_put(struct fake *f, struct swl_wx *x)
{
	*f = sf;
	*x = sx;
	memcpy(ramarr, sram, sizeof ramarr);
	f->ram = ramarr;
	bus(&cbus, f);
	cbus.rng = t_rng;
	x->b = &cbus;
	x->level = 6;
	loglen = 0; logbuf[0] = 0;
}

static void rund(void)
{
	static struct fake f, g;
	struct swl_wx x;
	const uint32_t len = 1764018u, words = 441005u, pin = 0x1153ccffu;
	uint32_t w0, n5, k, nrd, keep;
	char t[256];

	cx_init_rng();
	mkimg(len);
	imgbuf[0] = 0x10; imgbuf[1] = 0; imgbuf[2] = 0; imgbuf[3] = 0xEA;
	forge(imgbuf, len, pin);
	CHECK(tcrc(imgbuf, len) == pin && SWW_FW_LEN == 1764018u && SWW_FW_CRC == 0x1153ccffu && SWW_FW_LEN == SWW_IMG_MAX);
	w0 = iw(0);
	CHECK(w0 == 0xEA000010u && w0 != PAT);
	cx_hand(336);
	/* run C on the pinned image (level 5): the reference; address 0 is untouched and nothing says w14 */
	obsb(&g, 336);
	CHECK(runc(&g, &x, 5, len, pin) == 0 && x.flags == CLEVEL5 && ramarr[0] == PAT && x.w14_old == 0 && strstr(logbuf, "w14") == 0 && strstr(logbuf, "W14") == 0 && strstr(logbuf, "level 5 done vars=12") != 0);
	n5 = g.nw;
	sf = g; sx = x; memcpy(sram, ramarr, sizeof ramarr);
	/* level 6, the whole run: one more store than level 5, the first dword of the image at address 0 */
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len, pin) == 0);
	CHECK(x.stopped == 0 && x.restore == SWW_RS_NONE && x.flags == (CLEVEL5 | SWF_W14) && x.armed == 0 && f.nw == n5 + 1 && x.nstore == f.nw && f.win2 == 0 && all_allowed(&f));
	CHECK(f.lastw.space == SWW_BAR2 && f.lastw.win == 0 && f.lastw.off == 0 && f.lastw.val == w0 && x.w14_old == PAT && x.rb == w0 && ramarr[0] == w0);
	snprintf(t, sizeof t, "SWEET WLAN: stage 3 level 6 done w14 old=0xa5a5a5a5 new=0xea000010 vars=12 token=0xfffc0003 crc=%#010x\n", cx.crc);
	CHECK(strstr(logbuf, t) != 0 && strstr(logbuf, "level 5 done") == 0 && strstr(logbuf, "W14 backplane 0 reads 0xa5a5a5a5 before the store") != 0);
	CHECK(strstr(logbuf, "W14 reset vector bp 0 old 0xa5a5a5a5 := 0xea000010 read back 0xea000010") != 0);
	ramarr[0] = PAT;
	CHECK(ramc_ok(words, 0));                                       /* everything else is as at level 5 */
	/* W14 alone from the level-5 state, and its read count */
	rd_put(&f, &x);
	nrd = f.nrd;
	CHECK(swl_w14(&x) == 0 && f.nw == n5 + 1 && ramarr[0] == w0 && x.flags == (CLEVEL5 | SWF_W14));
	nrd = f.nrd - nrd;
	CHECK(nrd > 10 && nrd < 60);
	/* the word read at 0 is all ones: a stop, the value is logged, nothing is stored (DHD dhd_pcie.c:10218) */
	rd_put(&f, &x);
	ramarr[0] = 0xFFFFFFFFu;
	CHECK(swl_w14(&x) == -SWW_E_DEAD && x.stop_code == SWW_E_DEAD && f.nw == n5 && x.restore == SWW_RS_NONE && f.win2 == 0 && ramarr[0] == 0xFFFFFFFFu && !(x.flags & SWF_W14));
	CHECK(strstr(logbuf, "W14 backplane 0 reads 0xffffffff before the store") != 0 && strstr(logbuf, "W14 STOP: dead read") != 0 && strstr(logbuf, "done") == 0);
	/* a failing read at every position of W14: never a success, at most the one store, and then the read-back is what stops it */
	for (k = 1; k <= nrd; k++) {
		rd_put(&f, &x);
		f.fail_rd_at = f.nrd + k;
		CHECK(swl_w14(&x) < 0 && x.stopped && f.nw <= n5 + 1 && (f.nw == n5 || x.stop_code == SWW_E_READBACK) && !(x.flags & SWF_W14) && x.restore == SWW_RS_NONE && f.win2 == 0 && all_allowed(&f));
	}
	/* a store the bus refuses */
	rd_put(&f, &x);
	f.fail_wr_at = f.nw + 1;
	CHECK(swl_w14(&x) == -SWW_E_WR && f.nw == n5 + 1 && ramarr[0] == PAT && x.restore == SWW_RS_NONE && !(x.flags & SWF_W14));
	/* X4: address 0 aliases a ROM bank: the store is accepted and ignored. A clean stop with the read-back printed, one store, nothing else, window 0 */
	rd_put(&f, &x);
	f.rom0 = 1;
	ramarr[0] = 0x12345678u;
	CHECK(swl_w14(&x) == -SWW_E_READBACK && x.stop_code == SWW_E_READBACK && f.nw == n5 + 1 && x.restore == SWW_RS_NONE && f.win2 == 0 && ramarr[0] == 0x12345678u && !(x.flags & SWF_W14) && x.armed == 0);
	CHECK(strstr(logbuf, "W14 backplane 0 wrote 0xea000010, old 0x12345678, read back 0x12345678") != 0 && strstr(logbuf, "W14 STOP: read-back") != 0 && strstr(logbuf, "done") == 0);
	/* a word that reads back with a bit flipped */
	rd_put(&f, &x);
	f.corrupt_at = f.nw + 1;
	CHECK(swl_w14(&x) == -SWW_E_READBACK && f.nw == n5 + 1 && ramarr[0] == (w0 ^ 1u) && strstr(logbuf, "read back 0xea000011") != 0);
	/* the whole stage on a ROM-like bank: stops at W14 with restore none, exactly one store more than level 5 */
	obsb(&f, 336);
	f.rom0 = 1;
	CHECK(runc(&f, &x, 6, len, pin) == -SWW_E_READBACK && x.stop_code == SWW_E_READBACK && f.nw == n5 + 1 && x.restore == SWW_RS_NONE && f.win2 == 0 && x.flags == CLEVEL5 && x.row == 14);
	CHECK(strstr(logbuf, "stage 3 level 6 stopped at W14 restore=none") != 0 && strstr(logbuf, "level 6 done") == 0 && strstr(logbuf, "read back 0xa5a5a5a5") != 0);
	/* the gates of swl_wr() for the run D tuple, each alone */
	rd_put(&f, &x);
	x.flags &= ~SWF_W13;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_PHASE && f.nw == n5);                    /* needs W13 */
	rd_put(&f, &x);
	x.level = 5;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_LEVEL && f.nw == n5);                    /* run 4 */
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == 0 && f.nw == n5 + 1 && swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_REPEAT && f.nw == n5 + 1);        /* once */
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0 ^ 1u) == -SWW_E_IMG && f.nw == n5);                 /* only image word 0 */
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, 0) == -SWW_E_IMG && f.nw == n5);
	rd_put(&f, &x);
	x.img_crc ^= 1u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_IMG && f.nw == n5);                      /* the pinned CRC */
	rd_put(&f, &x);
	x.img_len = len - 4u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_IMG && f.nw == n5);                      /* the pinned length */
	rd_put(&f, &x);
	x.img_len = len + 4u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_IMG && f.nw == n5);
	rd_put(&f, &x);
	x.img = 0;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_IMG && f.nw == n5);
	rd_put(&f, &x);
	f.win2 = 0x400000u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_WIN && f.nw == n5);                      /* the live BAR2 window is 0 */
	rd_put(&f, &x);
	f.win2 = 0x400000u;
	CHECK(swl_w14(&x) == -SWW_E_RD && f.nw == n5);
	rd_put(&f, &x);
	f.cmd ^= 4u;
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0, w0) == -SWW_E_PRE && f.nw == n5);                      /* the preconditions are read again */
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 4, w0) == -SWW_E_DENIED && f.nw == n5);                  /* no other word of window 0 below the RAM */
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0, 0x1FFFFCu, w0) == -SWW_E_DENIED && f.nw == n5);
	rd_put(&f, &x);
	CHECK(swl_wr(&x, SWW_BAR2, 0x400000u, 0, w0) < 0 && f.nw == n5);                       /* offset 0 of the tail window is not the reset vector */
	/* the image and its identity are checked again at W14 (AP-74): a byte of the source changed after W10, with the pinned CRC no longer true */
	rd_put(&f, &x);
	imgbuf[len / 2] ^= 1;
	CHECK(swl_w14(&x) == -SWW_E_IMG && f.nw == n5 && strstr(logbuf, "image CRC") != 0);
	imgbuf[len / 2] ^= 1;
	rd_put(&f, &x);
	imgbuf[0] ^= 1;
	CHECK(swl_w14(&x) == -SWW_E_IMG && f.nw == n5 && ramarr[0] == PAT);                     /* the vector itself */
	imgbuf[0] ^= 1;
	rd_put(&f, &x);
	x.img_len = len - 4u;
	x.img_crc = tcrc(imgbuf, len - 4u);                                                     /* a consistent but different image */
	CHECK(swl_w14(&x) == -SWW_E_IMG && f.nw == n5 && strstr(logbuf, "firmware identity 1764014 bytes crc") != 0);
	rd_put(&f, &x);
	imgbuf[100] ^= 1;
	x.img_crc = tcrc(imgbuf, len);
	CHECK(swl_w14(&x) == -SWW_E_IMG && f.nw == n5 && strstr(logbuf, "firmware identity 1764018 bytes crc") != 0 && strstr(logbuf, "is not the pinned 1764018 bytes crc 0x1153ccff") != 0);
	/* the pin before the first store of the run: a consistent image of another length or content stops at the early check, no read, no store, nothing armed (review 0130 finding 8) */
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len, tcrc(imgbuf, len)) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && x.row == 14 && x.restore == SWW_RS_NONE && x.armed == 0 && x.rb == 0);
	CHECK(strstr(logbuf, "firmware identity") != 0 && strstr(logbuf, "stage 3 level 6 stopped at W14 restore=none") != 0 && ramarr[0] == PAT);
	/* ... which level 5 does not do: the same bytes copy (the run is run C) */
	obsb(&f, 336);
	CHECK(runc(&f, &x, 5, len, tcrc(imgbuf, len)) == 0 && f.nw == n5 && strstr(logbuf, "firmware identity") == 0);
	imgbuf[100] ^= 1;
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len - 4u, tcrc(imgbuf, len - 4u)) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "firmware identity 1764014 bytes crc") != 0);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len, pin ^ 1u) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "image CRC") != 0);     /* a CRC that is not the bytes' CRC stops at the image check */
	/* a shorter image whose CRC is the pinned one: only the length tells it from the firmware */
	forge(imgbuf, len - 4u, pin);
	CHECK(tcrc(imgbuf, len - 4u) == pin);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len - 4u, pin) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "firmware identity 1764014 bytes crc 0x1153ccff is not the pinned") != 0);
	rd_put(&f, &x);
	x.img_len = len - 4u;
	x.img_crc = pin;
	CHECK(swl_w14(&x) == -SWW_E_IMG && f.nw == n5 && strstr(logbuf, "firmware identity 1764014 bytes crc 0x1153ccff is not the pinned") != 0);
	keep = imglen;
	mkimg(66);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, 66, tcrc(imgbuf, 66)) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "firmware identity 66 bytes crc") != 0);
	CHECK(keep == len);
	/* the NVRAM entry is not pinned: the 17 byte hand text above and the 27062 byte size of the real calibration file both run to the end (the NVRAM is only checked against its container record) */
	mkimg(len);
	imgbuf[0] = 0x10; imgbuf[1] = 0; imgbuf[2] = 0; imgbuf[3] = 0xEA;
	forge(imgbuf, len, pin);
	cx_plain(27062, 336);
	CHECK(nvlen == 27062);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len, pin) == 0 && x.nv_varsize == cx.varsize && x.nv_src_len == 27062 && x.flags == (CLEVEL5 | SWF_W14) && ramarr[0] == w0 && x.nv_crc_rb == cx.crc);
	cx_plain(3, 336);
	obsb(&f, 336);
	CHECK(runc(&f, &x, 6, len, pin) == 0 && x.nv_varsize == cx.varsize && ramarr[0] == w0);
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
	runc_happy();
	runc_prep();
	runc_faults();
	runc_gates();
	runc_deny_sweep();
	rund();
	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_wr_host_test (%u checks)\n", fails);
		return 1;
	}
	fprintf(stderr, "RESULT: PASS wlan_wr_host_test\n");
	return 0;
}
