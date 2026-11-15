/* Host replay + fault-injection test of the stage-3 chip WRITER in xnu patch 0129 (sweet_wlan_wr.h, extracted from the patch by build.sh), docs/research/43.
 * The fake bus holds the values observed on the Pixel 6a (EP Command 0x00100002, BAR2 0x60400004, cfg 0x88 0x00080080, ELBI 0x0300a911, ATU r0/r1 of
 * run wd2-2, SRPWR 0x00030300, SYS_MEM coreinfo 0x115, wrapper IOCTL 0 / RESET_CTL 1 / RESET_ST 0); bank sizes and the two BAR2 words are chosen by the test.
 * Every guard rule of swl_wr() has a case that fails when the rule is removed: tools/test_wlan_wr_mutants.py removes each one from the header and requires this
 * test to fail (CR-02). Nothing here touches hardware. */
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static unsigned fails;
#ifndef SWW_FAILFAST
#define SWW_FAILFAST 0          /* tools/test_wlan_wr_mutants.py builds with -DSWW_FAILFAST=1: stop at the first failed check, one is enough to kill a mutant; build.sh runs every check */
#endif
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; if (SWW_FAILFAST) { fprintf(stderr, "RESULT: FAIL wlan_wr_host_test (fail-fast, first failed check)\n"); exit(1); } } } while (0)

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
	/* run E (patch 0133): a fake CA7. It RUNS from the store RESET_CTL := 0 with CPUHALT (0x20) clear in IOCTL until RESET_CTL := 1; while it runs, read k of the word boot_bp returns seq[min(k, nseq - 1)] (nseq 0: the firmware never comes up) */
	uint32_t mbint, mbmask;                         /* the dongle's PCIe core mailbox int / mask (BAR0 window PCIE, 0xC30 / 0xC34) */
	unsigned run, started;                          /* the fake CPU runs now; how many times it was started */
	const uint32_t *seq;
	unsigned nseq, seqi;
	uint32_t boot_bp;
	unsigned halt_mode;                             /* RESET_CTL := 1 reaching the CA7 while it runs: 0 taken, 1 refused by the bus, 2 accepted and ignored */
	unsigned st_busy;                               /* reads of the CA7 RESET_ST that return 1 (operations pending) while the CPU runs */
	unsigned st_at_assert, asserts_run;             /* st_busy left when RESET_CTL := 1 reached the CA7 while it ran; how many such stores */
	uint32_t cmd_on_run, ioctl_on_run;              /* what the firmware changes when it starts: EP Command bits set, XOR on its own IOCTL */
	unsigned st_after_io3, fail_c88, cmd_at_poll;   /* CA7 RESET_ST goes to 1 when IOCTL := 0x3 is written; reads of cfg 0x88 fail; EP Command bit 2 is set by the firmware after this many reads of the boot word */
	unsigned st_pre;                                /* the next reads of the CA7 RESET_ST return 1 (operations pending), CPU running or not */
	unsigned rc_clear_at_10;                        /* the CA7 reset releases itself at the first 10 us delay after it was asserted */
	uint32_t elbi_irq[7];                           /* host controller interrupt registers, ELBI 0x00..0x18 by offset / 4 (status 0..2, enable 4..6) */
	uint32_t cmd_clr_on_run;                        /* EP Command bits the firmware clears when it starts (after cmd_on_run is set) */
	unsigned any_pcie_read;                         /* every offset of the PCIe core page answers a read */
	unsigned flip_at_vector;                        /* the store of the reset vector at backplane address 0 flips a byte of the source image after it (the source changes between W14 and W16) */
	/* run F1 (patch 0134): the PCIe core registers 0xC10/0xC14/0xC18, what the running firmware changes, a read guard, a poke at the k-th BAR2 read */
	uint32_t fc10, fc14, fc18;                      /* PCIFunctionIntstatus / Intmask / PowerIntstatus, BAR0 window PCIE */
	uint32_t run_mbmask, run_en[3];                 /* ORed into the mailbox mask (once the CPU was released: the PCIe core keeps it) / the three RC enables (while the CPU runs) (the firmware or the chip sets them) */
	unsigned bar2_n, poke_at;                       /* BAR2 reads so far; at read number poke_at (1-based, 0 = never) ram[poke_bp / 4] |= poke_val before the read is answered */
	uint32_t poke_bp, poke_val, poke_cmd;           /* ... and f->cmd |= poke_cmd (the firmware turns on bus mastering in the middle of the dumps) */
	uint32_t cmd_at_c10;                            /* f->cmd |= cmd_at_c10 at the read of PCIe core 0xC10 */
	unsigned ones_run;                              /* ones_on answers all-ones only once the CPU was released (running or reset again: the PCIe core group of F1 reads after the re-halt) */
	uint32_t guard_lo, guard_hi;                    /* when guard_hi != 0: BAR2 reads (backplane address) outside [guard_lo, guard_hi) while the CPU runs and BAR0 is on the PCIe window (= the F1 dumps) are counted in oob */
	unsigned oob;
	uint32_t lo_lo, lo_hi;                          /* run F2 (patch 0135): when lo_hi != 0: BAR2 reads (backplane address) outside [lo_lo, lo_hi) after the CPU was released and reset again (= the F2 dumps) are counted in oob */
	/* run F3 (patch 0136): window 0 reads while the CPU runs, RAM below 4 MiB that reads all ones once the CPU is reset again (observed in run F2), a cfg 0x84 read that lies */
	uint32_t lv_lo, lv_hi;                          /* when lv_hi != 0: BAR2 reads in window 0 while the CPU runs outside [lv_lo, lv_hi) are counted in oob */
	unsigned dead_lo;                               /* RAM below 4 MiB reads all ones after the CPU was released and reset again */
	unsigned n84, lie84_at;                         /* reads of cfg 0x84 so far; the lie84_at-th (1-based, 0 = never) returns the window XOR 0x1000 */
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
	if (f->ones_on && (!f->ones_run || f->run || f->started) && f->ones_space == space && f->ones_win == win && f->ones_off == off) {
		*v = 0xFFFFFFFFu;
		return 0;
	}
	switch (space) {
	case SWW_CFG:
		if (f->fail_c88 && off == 0x88)
			return -1;
		if (off == 0x84) {
			f->n84++;
			if (f->lie84_at && f->n84 == f->lie84_at) {
				*v = f->win2 ^ 0x1000u;
				return 0;
			}
		}
		*v = off == 0x04 ? f->cmd : off == 0x10 ? f->bar0lo : off == 0x14 ? f->bar0hi : off == 0x18 ? f->bar2lo : off == 0x1C ? f->bar2hi :
		     off == 0x80 ? f->win0 : off == 0x84 ? f->win2 : off == 0x88 ? f->c88 : 0xDEAD0000u;
		return (off == 0x04 || off == 0x10 || off == 0x14 || off == 0x18 || off == 0x1C || off == 0x80 || off == 0x84 || off == 0x88) ? 0 : -1;
	case SWW_ATU:
		if (off < 0x20) { *v = f->a0[off / 4]; return 0; }
		if (off >= 0x200 && off < 0x220) { *v = f->a1[(off - 0x200) / 4]; return 0; }
		if (off >= SWW_R2(0) && off < SWW_R2(0x20)) { *v = f->a2[(off - SWW_R2(0)) / 4]; return 0; }
		return -1;
	case SWW_ELBI:
		if (off < 0x40) { *v = off / 4 < 7 ? (f->elbi_irq[off / 4] | (f->run && off / 4 >= 4 ? f->run_en[off / 4 - 4] : 0)) : 0x0BAD0000u | off; return 0; }      /* the interrupt block answers (and a little beyond it: the read allowlist alone must refuse that) */
		*v = f->ltssm;
		return off == 0x2C8 ? 0 : -1;
	case SWW_ISO:
		*v = f->iso;
		return off == 0 ? 0 : -1;
	case SWW_BAR0:
		if (!f->nowincheck && f->win0 != win)
			return -1;
		if (f->win0 == SWW_WIN_PCIE && f->run && (off == SWW_SRPWR || off == 0xC10u || off == 0xC14u || off == 0xC18u || off == 0xC30u || off == 0xC34u))
			f->oob++;                                               /* review 0134 F1: no PCIe core register is read while the CPU runs (literals on purpose) */
		if (f->win0 == SWW_WIN_PCIE && off == SWW_SRPWR) { *v = f->srpwr; return 0; }
		if (f->win0 == SWW_WIN_PCIE && off == 0xC30u) { *v = f->mbint; return 0; }       /* PCIMailBoxInt(rev >= 64), literal: the header's macro is under test */
		if (f->win0 == SWW_WIN_PCIE && off == 0xC34u) { *v = f->mbmask | (f->run || f->started ? f->run_mbmask : 0); return 0; }      /* PCIMailBoxMask */
		if (f->win0 == SWW_WIN_PCIE && off == 0xC10u) { f->cmd |= f->cmd_at_c10; *v = f->fc10; return 0; }        /* run F1: PCIFunctionIntstatus, literals on purpose */
		if (f->win0 == SWW_WIN_PCIE && off == 0xC14u) { *v = f->fc14; return 0; }
		if (f->win0 == SWW_WIN_PCIE && off == 0xC18u) { *v = f->fc18; return 0; }
		if (f->win0 == SWW_WIN_PCIE && f->any_pcie_read) { *v = 0x0BAD0000u | off; return 0; }   /* any other offset of the PCIe core page answers too (the read allowlist alone must refuse it) */
		if (f->win0 == SWW_WIN_CA7WRAP && off == SWL_AGENT_RESET_ST && f->run && f->st_busy) { f->st_busy--; *v = 1; return 0; }
		if (f->win0 == SWW_WIN_CA7WRAP && off == SWL_AGENT_RESET_ST && f->st_pre) { f->st_pre--; *v = 1; return 0; }
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
			f->bar2_n++;
			if (f->poke_at && f->bar2_n == f->poke_at) {
				f->ram[f->poke_bp / 4] |= f->poke_val;
				f->cmd |= f->poke_cmd;
			}
			if (f->guard_hi && f->run && f->win0 == SWW_WIN_PCIE && (win + off < f->guard_lo || win + off >= f->guard_hi))
				f->oob++;
			if (f->lo_hi && f->started && !f->run && (win + off < f->lo_lo || win + off >= f->lo_hi))
				f->oob++;
			if (f->lv_hi && f->run && win == 0 && (off < f->lv_lo || off >= f->lv_hi))
				f->oob++;
			if (f->dead_lo && f->started && !f->run && win + off < SWW_TCM_BYTES) {
				*v = 0xFFFFFFFFu;
				return 0;
			}
			if (f->run && f->nseq && win + off == f->boot_bp) {
				f->ram[f->boot_bp / 4] = f->seq[f->seqi < f->nseq ? f->seqi : f->nseq - 1];
				f->seqi++;
				if (f->cmd_at_poll && f->seqi == f->cmd_at_poll)
					f->cmd |= 4;
			}
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
	if (f->flip_at_vector && space == SWW_BAR2 && win == 0 && off == 0)
		imgbuf[imglen / 2] ^= 1;
	if (f->fail_wr_at == f->nw)
		return -1;
	if (f->ignore_writes || f->drop_at == f->nw)
		return 0;
	if (f->corrupt_at == f->nw)
		v ^= 1u;
	if (space == SWW_CFG && off == SWW_CFG_WIN) { if (!f->win_stuck) f->win0 = v; return 0; }
	if (space == SWW_CFG && off == SWW_CFG_WIN2) { if (!f->win2_stuck || f->nw < f->win2_stuck) f->win2 = v; return 0; }
	if (space == SWW_BAR2 && f->ram) {
		if (f->rom0 == 2 && win + off == 0 && f->win2 == win) {
			f->ram[0] = (f->ram[0] & 0xFFFF0000u) | (v & 0xFFFFu);   /* a partial stick: only the low 16 bits (review 0132 F3) */
			return 0;
		}
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
		if (f->win0 == SWW_WIN_CA7WRAP && v == 3 && f->st_after_io3)
			wp[2] = 1;
	}
	if (wp && off == SWL_AGENT_RESET_CTL) {
		if (v == 1 && f->win0 == SWW_WIN_CA7WRAP && f->run) {
			f->st_at_assert = f->st_busy;
			f->asserts_run++;
			if (f->halt_mode == 1)
				return -1;
			if (f->halt_mode == 2)
				return 0;
		}
		if (v == 1)
			f->assert_t[f->win0 == SWW_WIN_CA7WRAP] = f->clock_us;
		wp[1] = v;
		if (f->win0 == SWW_WIN_CA7WRAP && v == 1)
			f->run = 0;
		if (f->win0 == SWW_WIN_CA7WRAP && v == 0 && !(wp[0] & 0x20u)) {      /* the reset is released with CPUHALT clear: the CPU starts */
			f->run = 1;
			f->started++;
			f->cmd = (f->cmd | f->cmd_on_run) & ~f->cmd_clr_on_run;
			wp[0] ^= f->ioctl_on_run;
		}
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
	if (us == 10 && f->rc_clear_at_10 && f->win0 == SWW_WIN_CA7WRAP && f->ca.ctl == 1) {
		f->ca.ctl = 0;
		f->rc_clear_at_10 = 0;
	}
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

			for (q = 0; q < SWT_N; q++)         /* the one other value a register may take (RESET_CTL 1 and 0, IOCTL 3 and 1; patch 0133: the CA7 IOCTL has 0x23/0x21 and 0x3/0x1) */
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
	      swl_wr_ok(SWW_CFG, 0, SWW_CFG_WIN2, 0x400001) < 0 && swl_wr_ok(SWW_CFG, 0, 0x88, 0x00080380) == SWT_C88 && swl_wr_ok(SWW_CFG, 0, 0x88, 0x00080080) < 0 && swl_wr_ok(SWW_CFG, 0, 0x88, 0x00080780) < 0 && swl_wr_ok(SWW_CFG, 0, 0x88, 0) < 0 && swl_wr_ok(SWW_CFG, 0, 0x10, 0) < 0 && swl_wr_ok(SWW_CFG, 0, 0x18, 0) < 0);
	CHECK(swl_wr_ok(SWW_ATU, 0, 0x208, 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x210, 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x0, 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0), 0) < 0 &&
	      swl_wr_ok(SWW_ATU, 0, SWW_R2(0x0C), 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0x18), 0) < 0 && swl_wr_ok(SWW_ATU, 0, SWW_R2(0x20), 0) < 0 && swl_wr_ok(SWW_ATU, 0, 0x600, 0x80000000) < 0);
	CHECK(swl_wr_ok(SWW_BAR0, 0x18001000, SWW_SRPWR, 0x200) < 0 && swl_wr_ok(SWW_BAR0, 0x18001000, SWW_SRPWR, 0x00030300) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x400, 1) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18126000, 0x404, 1) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x808, 1) < 0 && swl_wr_ok(SWW_BAR0, 0x18120000, 0x80C, 1) < 0 &&
	      swl_wr_ok(SWW_BAR0, 0x18120000, 0x408, 0x13) < 0 && swl_wr_ok(SWW_BAR0, 0x18126000, 0x408, 0x23) < 0 && swl_wr_ok(SWW_BAR0, 0x18121000, 0x800, 1) < 0 &&
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
	CHECK(SWT_RESET0 == SWT_N_C && SWT_C88 == SWT_N_C + 1 && SWT_WIN2_TAIL_E == SWT_N_C + 2 && swl_wt[SWT_RESET0].run == 4 && swl_wt[SWT_RESET0].row == 14 && swl_wt[SWT_RESET0].space == SWW_BAR2 && swl_wt[SWT_RESET0].win == 0 && swl_wt[SWT_RESET0].off == 0 &&
	      swl_wt[SWT_RESET0].hi == 0 && swl_wt[SWT_RESET0].flags == (SWT_ONCE | SWT_RV) && swl_wt[SWT_RESET0].need == SWF_W13 && swl_wt[SWT_RESET0].pred1 == 0 && swl_wt[SWT_RESET0].rgrp == 0 &&
	      swl_wt[SWT_RESET0].vmask == 0xFFFFFFFFu && swl_wt[SWT_RESET0].omask == 0x2u && swl_wt[SWT_RESET0].oval == 0);   /* review 0132 F1 (secured bit) and F3 (whole word) */
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
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x84, 0x400000) == SWT_WIN2_TAIL && swl_wr_ok_from(SWT_WIN2_TAIL + 1, SWW_CFG, 0, 0x84, 0x400000) == SWT_WIN2_TAIL_C && swl_wr_ok_from(SWT_WIN2_TAIL_C + 1, SWW_CFG, 0, 0x84, 0x400000) == SWT_WIN2_TAIL_E && swl_wr_ok_from(SWT_WIN2_TAIL_E + 1, SWW_CFG, 0, 0x84, 0x400000) < 0);
	CHECK(swl_wr_ok(SWW_CFG, 0, 0x84, 0) == SWT_WIN2_ZERO && swl_wr_ok_from(SWT_WIN2_ZERO + 1, SWW_CFG, 0, 0x84, 0) == SWT_WIN2_ZERO_C && swl_wr_ok_from(SWT_WIN2_ZERO_C + 1, SWW_CFG, 0, 0x84, 0) == SWT_WIN2_ZERO_E && swl_wr_ok_from(SWT_WIN2_ZERO_E + 1, SWW_CFG, 0, 0x84, 0) < 0);
	{
		unsigned v;

		for (v = 0; v < SWT_N; v++)             /* a tail tuple names one of the three kinds (x->tn has a slot for each), no other tuple names one */
			CHECK(((swl_wt[v].flags & SWT_TW) != 0) == (v >= SWT_NV_VARS && v <= SWT_NV_RTLV) && (!(swl_wt[v].flags & SWT_TW) || (swl_wt[v].kind >= SWK_VARS && swl_wt[v].kind <= SWK_RTLV)) &&
			      (!!(swl_wt[v].flags & SWT_TW) || swl_wt[v].kind == 0));
	}
	CHECK(SWT_N == 35 && SWT_N_C == 27 && SWT_N_B == 22 && SWT_WIN2_TAIL_C == 22 && SWT_NV_VARS == 23 && SWT_NV_TOKEN == 24 && SWT_NV_RTLV == 25 && SWT_WIN2_ZERO_C == 26);
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
		CHECK(swl_w14(&x) < 0 && x.stopped && f.nw <= n5 + 1 && (f.nw == n5 || x.stop_code == SWW_E_READBACK || x.stop_code == SWW_E_RD) && !(x.flags & SWF_W14) && x.restore == SWW_RS_NONE && f.win2 == 0 && all_allowed(&f));
		/* review 0132 F4: a read-back read that fails is the stop "read" and prints no chip value */
		CHECK(!(f.nw == n5 + 1 && x.stop_code == SWW_E_RD) || strstr(logbuf, "read back 0x") == 0);
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
	/* review 0132 F3: a partial stick (only the low 16 bits of the word take) is a read-back stop that prints what was read */
	rd_put(&f, &x);
	f.rom0 = 2;
	CHECK(swl_w14(&x) == -SWW_E_READBACK && f.nw == n5 + 1 && ramarr[0] == 0xa5a50010u && x.restore == SWW_RS_NONE && !(x.flags & SWF_W14) && strstr(logbuf, "read back 0xa5a50010") != 0);
	/* review 0132 F4: the last read of W14 is the read-back; when it fails the stop is "read" (stop_code RD) and no read back value is printed */
	rd_put(&f, &x);
	f.fail_rd_at = f.nrd + nrd;
	CHECK(swl_w14(&x) == -SWW_E_RD && x.stop_code == SWW_E_RD && f.nw == n5 + 1 && x.rb == 0 && strstr(logbuf, "read back 0x") == 0 && strstr(logbuf, "W14 STOP: read (code") != 0);
	/* review 0132 F1: a vector word with the secured bit (DAR_SEC_SBOOT_MASK, bit 1) set is refused as DHD refuses it, before any store, at the old-value test */
	rd_put(&f, &x);
	ramarr[0] = 0xba3ef002u;
	CHECK(swl_w14(&x) == -SWW_E_OLD && x.stop_code == SWW_E_OLD && f.nw == n5 && ramarr[0] == 0xba3ef002u && x.restore == SWW_RS_NONE && !(x.flags & SWF_W14) && strstr(logbuf, "W14 STOP: old value") != 0);
	rd_put(&f, &x);
	ramarr[0] = 0xba3ef000u;
	CHECK(swl_w14(&x) == 0 && f.nw == n5 + 1 && ramarr[0] == w0);                          /* the observed word (bit 1 clear) still goes */
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

/* review 0132 F2: W14 runs only when stage 1 listed PCIe core 0x83c with rev < 74 or == 128 (DHD does not read the DAR security register there, dhd_pcie.c:11735-11745) */
static void rev_gate(void)
{
	static struct swl_chip c;
	static const unsigned revs[] = { 0, 72, 73, 74, 75, 127, 128, 129, 255 };
	static const int want[] = { 1, 1, 1, 0, 0, 0, 1, 0, 0 };
	unsigned i;

	CHECK(swl_w14_rev_ok(&c) == 0);                                 /* no cores listed */
	c.ncores = 2;
	c.cores[0].id = SWL_CORE_CC; c.cores[0].rev = 72;
	c.cores[1].id = 0x83d; c.cores[1].rev = 72;                     /* another core with a good rev does not count */
	CHECK(swl_w14_rev_ok(&c) == 0);
	c.cores[1].id = SWW_CORE_PCIE2;
	for (i = 0; i < sizeof revs / sizeof revs[0]; i++) {
		c.cores[1].rev = (uint8_t)revs[i];
		CHECK(swl_w14_rev_ok(&c) == want[i]);
	}
	/* run E (patch 0133): the mailbox registers are at 0xC30/0xC34 from PCIe core rev 64 (REV_GE_64) and W14's vector test is DHD's below rev 74: 64..73 only */
	{
		static const unsigned r7[] = { 0, 63, 64, 65, 72, 73, 74, 128, 129, 255 };
		static const int w7[] = { 0, 0, 1, 1, 1, 1, 0, 0, 0, 0 };

		c.ncores = 0;
		CHECK(swl_w16_rev_ok(&c) == 0);
		c.ncores = 2;
		c.cores[1].id = 0x83d; c.cores[1].rev = 72;
		CHECK(swl_w16_rev_ok(&c) == 0);
		c.cores[1].id = SWW_CORE_PCIE2;
		for (i = 0; i < sizeof r7 / sizeof r7[0]; i++) {
			c.cores[1].rev = (uint8_t)r7[i];
			CHECK(swl_w16_rev_ok(&c) == w7[i]);
		}
	}
}

/* ---------------------------------------------------------------- run E (patch 0133): W16 the CA7 release, W17 the shared-word poll, the stop action ---------------------------------------------------------------- */

static struct fake ef;                                  /* the state after run D (level 6) on the pinned image: every W16/W17 case starts from it */
static struct swl_wx ex;
static uint32_t eram[RAM_WORDS];
static uint32_t eseq[128];
#define E_END 0x4A0000u                                 /* ram_end of the 336 x 8 KiB RAM; the shared-area word is the last one */
#define E_BOOT (E_END - 4u)
#define E_BOTH (SWF_W16 | SWF_W17)

static void eseqset(struct fake *f, unsigned n, uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
	eseq[0] = a; eseq[1] = b; eseq[2] = c; eseq[3] = d;
	f->seq = eseq; f->nseq = n; f->seqi = 0; f->boot_bp = E_BOOT;
}

static void eseqlong(struct fake *f, unsigned polls, uint32_t tok, uint32_t last)      /* the token for polls - 1 reads, then `last` */
{
	unsigned i;

	for (i = 0; i + 1 < polls; i++)
		eseq[i] = tok;
	eseq[polls - 1u] = last;
	f->seq = eseq; f->nseq = polls; f->seqi = 0; f->boot_bp = E_BOOT;
}

static void e_put(struct fake *f, struct swl_wx *x)
{
	*f = ef;
	*x = ex;
	memcpy(ramarr, eram, sizeof ramarr);
	f->ram = ramarr;
	bus(&cbus, f);
	cbus.rng = t_rng;
	x->b = &cbus;
	x->level = 7;
	f->nw = 0; f->nrd = 0;                                  /* f->w[] records the first 512 stores: record the ones of run E */
	loglen = 0; logbuf[0] = 0;
}

static int e_steps(struct swl_wx *x)
{
	int rc = swl_w16(x);

	return rc ? rc : swl_w17(x);
}

static int e_fast(struct swl_wx *x)                    /* W16 without the image CRC (the sweeps run it a thousand times; swl_w16 itself is run by the cases above) + W17 */
{
	int rc = swl_w16_go(x);

	return rc ? rc : swl_w17(x);
}

static int e_stores(const struct fake *f, unsigned from, const struct exp *e, unsigned n)
{
	unsigned i;

	for (i = 0; i < n; i++)
		if (f->w[from + i].space != e[i].space || f->w[from + i].win != e[i].win || f->w[from + i].off != e[i].off || f->w[from + i].val != e[i].val)
			return 0;
	return 1;
}

/* the stores of W16 up to and including the release, restated from DHD (dhd_pcie.c:10343-10346, aiutils.c:1242-1325) and the poll window; literals on purpose */
static const struct exp wantE[] = {
	{ SWW_CFG, 0, 0x80, 0x18001000u, 0 }, { SWW_CFG, 0, 0x84, 0x400000u, SWW_RG_W84 }, { SWW_CFG, 0, 0x80, 0x18120000u, 0 },
	{ SWW_BAR0, 0x18120000u, 0x800, 1, SWW_RG_CA }, { SWW_BAR0, 0x18120000u, 0x408, 3, SWW_RG_CA }, { SWW_BAR0, 0x18120000u, 0x800, 0, SWW_RG_CA }, { SWW_BAR0, 0x18120000u, 0x408, 1, SWW_RG_CA },
};
#define NWANTE ((unsigned)(sizeof wantE / sizeof wantE[0]))

/* one fault of `kind` (0 read fails, 1 store refused, 2 store dropped, 3 store corrupted) at every read / store position of W16 + W17, and one position beyond: the writer never loses track of the CPU */
static void e_sweep(uint32_t sh, int kind)
{
	static struct fake f, h;
	static struct swl_wx x;
	unsigned k, n, benign = 0, bad_zero = 0, w84n = 0;
	int rc, w84;

	e_put(&h, &x);
	ramarr[sh / 4] = 0x00010006u;
	eseqset(&h, 2, x.tok, sh, 0, 0);
	rc = e_fast(&x);
	CHECK(rc == 0 && h.started == 1 && h.run == 0);
	n = kind == 0 ? h.nrd : h.nw;
	CHECK(kind == 0 ? n > 50 : n == (sh < 0x400000u ? 10u : 9u));
	for (k = 1; k <= n + 2; k++) {
		e_put(&f, &x);
		ramarr[sh / 4] = 0x00010006u;
		eseqset(&f, 2, x.tok, sh, 0, 0);
		if (kind == 0) f.fail_rd_at = k;
		if (kind == 1) f.fail_wr_at = k;
		if (kind == 2) f.drop_at = k;
		if (kind == 3) f.corrupt_at = k;
		rc = e_fast(&x);
		CHECK(all_allowed(&f));                                         /* every store that reached the bus is a tuple or a restore row */
		CHECK(f.started <= 1 || (kind == 3 && x.restore == SWW_RS_FAILED));    /* at most one store starts the CPU (a corrupted halting store may release it: the bus did that, and the read-back says so) */
		CHECK(!f.run || x.restore != SWW_RS_OK);                        /* a CPU that still runs is never reported as reset again */
		CHECK(!f.started || (x.flags & SWF_W16));                       /* a CPU that was started (whatever the read-back said) is a released one: the report says so (review 0133 F3) */
		CHECK(x.halted == (rc == 0));
		if (rc == 0) {
			/* review 0133 F3: "halted" is the CA7 row's own read-back; a poll window restore (cfg 0x84 := 0) that fails after a good re-halt is reported by its line and the restore state, not as an unconfirmed halt */
			w84 = strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 FAILED\n") != 0;
			CHECK(f.run == 0 && x.ca_ok && (x.restore == SWW_RS_OK || (w84 && x.restore == SWW_RS_FAILED)) && (w84 || f.win2 == 0) && x.polls == 2 && x.shared == sh && x.version == 6);
			if (w84) {
				w84n++;
				CHECK(strstr(logbuf, "restore space 3 win 0x18120000 off 0x800 := 0x1 ok\n") != 0);
			} else if (k <= n) {                                           /* benign: a read of the wait for pending operations (DHD goes on, aiutils.c:1257); or the last store, cfg 0x84 := 0 of the restore, dropped when the register is 0 already */
				benign++;
				CHECK((kind == 0 && strstr(logbuf, "CA7 RESET_ST 0 after 1 waits") != 0) || (kind == 2 && k == n && sh < 0x400000u));
			}
		} else {
			CHECK(x.stopped && k <= n);
			/* the stop code names the fault: a refused store is "store", a store that does not hold is "read-back" (nothing else may notice it first: a tuple that does not compare its read-back is seen here), the halt that does not take is "halt" */
			if (kind == 1)
				CHECK(x.stop_code == SWW_E_WR || x.stop_code == SWW_E_HALT);
			if (kind == 2 || kind == 3)
				CHECK(x.stop_code == SWW_E_READBACK || x.stop_code == SWW_E_HALT);
			if (f.run) {                                            /* the report names the state */
				CHECK(x.flags & SWF_W16);
			}
		}
		if (rc == 0 && k > n)
			bad_zero++;
	}
	CHECK(bad_zero == 2);                                                   /* the two positions beyond the last operation do not trigger */
	CHECK(benign == (kind == 0 || (kind == 2 && sh < 0x400000u) ? 1u : 0u));
	CHECK(w84n == (kind == 2 && sh < 0x400000u ? 0u : 1u));                  /* the last restore row (cfg 0x84): a read-back read that fails, a refused / corrupted store, a dropped one when the register was not 0 */
}

static void rune(void)
{
	static struct fake f, g;
	static struct swl_wx x, y;
	const uint32_t len = 1764018u, pin = 0x1153ccffu;
	uint32_t i, c0, tok, sv;
	unsigned nw1;
	int rc;
	char t[320];

	cx_init_rng();
	mkimg(len);
	imgbuf[0] = 0x10; imgbuf[1] = 0; imgbuf[2] = 0; imgbuf[3] = 0xEA;
	forge(imgbuf, len, pin);
	CHECK(tcrc(imgbuf, len) == pin);
	cx_hand(336);
	obsb(&g, 336);
	CHECK(runc(&g, &y, 6, len, pin) == 0 && y.flags == (CLEVEL5 | SWF_W14) && y.armed == 0 && g.run == 0 && g.started == 0);
	ef = g; ex = y; memcpy(eram, ramarr, sizeof ramarr);
	tok = ex.tok;
	CHECK(tok != 0 && tok != PAT && ramarr[E_BOOT / 4] == tok && E_END == SWW_RAM_BASE + ex.ram_bytes && sizeof ex.done == 8);

	/* the whole of W16 + W17, shared structure below the tail window: poll 1 token, 2 zero (changes are printed, repeats are not), 3 zero, 4 the shared address */
	e_put(&f, &x);
	ramarr[0x3AF000u / 4] = 0x00010006u;
	eseqset(&f, 4, tok, 0, 0, 0x3AF000u);
	c0 = (uint32_t)f.clock_us;
	CHECK(e_steps(&x) == 0);
	CHECK(x.stopped == 0 && x.halted == 1 && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1 && f.win2 == 0 && f.win0 == 0x18120000u && x.armed == 0 && f.asserts_run == 1);
	CHECK(x.polls == 4 && x.shared == 0x3AF000u && x.version == 6 && (x.flags & E_BOTH) == E_BOTH && all_allowed(&f) && f.nw == 10);
	CHECK(e_stores(&f, 0, wantE, NWANTE));
	CHECK(f.w[7].space == SWW_CFG && f.w[7].off == 0x84 && f.w[7].val == 0);                       /* the window back for the shared structure */
	CHECK(f.w[8].space == SWW_BAR0 && f.w[8].win == 0x18120000u && f.w[8].off == 0x800 && f.w[8].val == 1);   /* the stop action: CA7 RESET_CTL := 1 */
	CHECK(f.w[9].space == SWW_CFG && f.w[9].off == 0x84 && f.w[9].val == 0);                       /* and the poll window back */
	CHECK(f.clock_us - c0 >= 3u * 50000u && f.clock_us - c0 < 3u * 50000u + 100u && f.early == 0 && count_str("asserting the reset") == 1);
	snprintf(t, sizeof t, "W17 poll 1 (0 ms after the release) word 0x%08x\n", tok);
	CHECK(strstr(logbuf, t) != 0 && strstr(logbuf, "W17 poll 2 (50 ms after the release) word 0x00000000\n") != 0 && strstr(logbuf, "W17 poll 4 (150 ms after the release) word 0x003af000\n") != 0 && count_str("W17 poll ") == 3);
	CHECK(strstr(logbuf, "W17 shared structure bp 0x3af000 after 4 polls: flags 0x00010006 version 6\n") != 0 && strstr(logbuf, "outside 5..7") == 0);
	CHECK(strstr(logbuf, "W16 PCIe core mailbox int 0 mask 0\n") != 0 && strstr(logbuf, "restore: CA7 RESET_ST 0 after 0 waits, asserting the reset") != 0 && strstr(logbuf, "restore space 3 win 0x18120000 off 0x800 := 0x1 ok") != 0);
	nw1 = f.nw;
	/* a second try of any release store is a repeat: the tuples are consumed (done is a 64 bit mask: the last tuples are above bit 31) */
	CHECK(SWT_REL_IOCTL_END >= 32 && SWT_WIN2_ZERO_E >= 32 && (x.done & (1ull << SWT_REL_IOCTL_END)) && (x.done & (1ull << SWT_WIN2_ZERO_E)) && (x.done & (1ull << SWT_REL_ASSERT)));
	f.win0 = SWW_WIN_CA7WRAP;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_REPEAT && f.nw == nw1);

	/* the shared structure above 4 MiB: no window store in between, the version read in the poll window */
	e_put(&f, &x);
	ramarr[0x498000u / 4] = 0x00000007u;
	eseqset(&f, 1, 0x498000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.polls == 1 && x.shared == 0x498000u && x.version == 7 && x.halted == 1 && f.win2 == 0 && f.nw == 9 && f.run == 0 && f.started == 1 && x.restore == SWW_RS_OK);
	CHECK(e_stores(&f, 0, wantE, NWANTE) && f.w[7].off == 0x800 && f.w[7].val == 1 && f.w[8].space == SWW_CFG && f.w[8].off == 0x84 && f.w[8].val == 0 && all_allowed(&f));
	CHECK(strstr(logbuf, "W17 poll 1 (0 ms after the release) word 0x00498000\n") != 0 && strstr(logbuf, "flags 0x00000007 version 7\n") != 0);
	CHECK((x.done & (1ull << SWT_WIN2_ZERO_E)) == 0);

	/* the firmware never comes up: 100 polls 50 ms apart, the last word and the CA7 state are printed, the stop action runs (restore ok) */
	e_put(&f, &x);
	eseqset(&f, 0, 0, 0, 0, 0);
	c0 = (uint32_t)f.clock_us;
	rc = e_steps(&x);
	CHECK(rc == -SWW_E_UP && x.stop_code == SWW_E_UP && x.polls == 100 && x.shared == tok && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1 && x.halted == 0 && f.win2 == 0 && count_str("W17 poll ") == 1);
	CHECK(f.clock_us - c0 >= 99u * 50000u && f.clock_us - c0 < 99u * 50000u + 2000u && !(x.flags & SWF_W17) && all_allowed(&f));
	snprintf(t, sizeof t, "W17 no shared address after 100 polls (5000 ms): word 0x%08x (token 0x%08x), CA7 ioctl 0x1 reset_ctl 0 reset_st 0, cfg 0x88 0x80080\n", tok, tok);
	CHECK(strstr(logbuf, t) != 0 && strstr(logbuf, "W17 STOP: firmware did not come up") != 0);
	/* the firmware that answers at the 100th poll (4.95 s) is found; one that would answer at the 101st is not waited for */
	e_put(&f, &x);
	ramarr[0x3AF000u / 4] = 0x00010005u;
	eseqlong(&f, 100, tok, 0x3AF000u);
	c0 = (uint32_t)f.clock_us;
	CHECK(e_steps(&x) == 0 && x.polls == 100 && x.version == 5 && x.shared == 0x3AF000u && f.clock_us - c0 >= 99u * 50000u && strstr(logbuf, "W17 poll 100 (4950 ms after the release) word 0x003af000\n") != 0);
	e_put(&f, &x);
	eseqlong(&f, 101, tok, 0x3AF000u);
	CHECK(e_steps(&x) == -SWW_E_UP && x.polls == 100 && x.shared == tok && f.run == 0 && x.restore == SWW_RS_OK);

	/* the link dies during the poll (all-ones): a stop at once, no further polls, the stop action runs */
	e_put(&f, &x);
	eseqset(&f, 3, tok, tok, 0xFFFFFFFFu, 0);
	CHECK(e_steps(&x) == -SWW_E_DEAD && x.polls == 3 && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1 && f.win2 == 0);
	e_put(&f, &x);
	eseqset(&f, 1, 0xFFFFFFFFu, 0, 0, 0);
	CHECK(e_steps(&x) == -SWW_E_DEAD && x.polls == 1 && x.restore == SWW_RS_OK && f.run == 0);

	/* a shared address that is not a word inside the RAM below the last word, each alone */
	{
		static const uint32_t bad[] = { 0x3AF002u, 0x1FFFFCu, 0x200000u - 8u, E_END, E_BOOT, 0xFFFFFFFEu, 0x80000000u, 1u, 0x3AF001u };
		unsigned b;

		for (b = 0; b < sizeof bad / sizeof bad[0]; b++) {
			e_put(&f, &x);
			eseqset(&f, 1, bad[b], 0, 0, 0);
			CHECK(e_steps(&x) == -SWW_E_SHARED && x.shared == bad[b] && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1 && f.win2 == 0 && strstr(logbuf, "is not a word in [0x200000, 0x49fffc)") != 0);
		}
	}
	/* the two ends of the range are accepted; the version of word 0 (0x10) is outside 5..7 and only logged */
	e_put(&f, &x);
	eseqset(&f, 1, 0x200000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.shared == 0x200000u && x.version == 0x10 && x.halted == 1 && strstr(logbuf, "version 16 (outside 5..7, not accepted by DHD)") != 0);
	e_put(&f, &x);
	eseqset(&f, 1, E_BOOT - 4u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.shared == E_BOOT - 4u && x.halted == 1 && f.win2 == 0);
	/* versions: the low byte only, 5..7 are not remarked, 4 and 8 are */
	{
		static const uint32_t fl[] = { 0x12345604u, 0x00000005u, 0xFFFFFF06u, 0x00000107u, 0x00000008u, 0u };
		static const int rem[] = { 1, 0, 0, 0, 1, 1 };
		unsigned b;

		for (b = 0; b < sizeof fl / sizeof fl[0]; b++) {
			e_put(&f, &x);
			ramarr[0x3AF000u / 4] = fl[b];
			eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
			CHECK(e_steps(&x) == 0 && x.version == (fl[b] & 0xFFu) && x.halted == 1 && (strstr(logbuf, "(outside 5..7") != 0) == rem[b]);
		}
	}
	e_put(&f, &x);
	ramarr[0x3AF000u / 4] = 0xFFFFFFFFu;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(e_steps(&x) == -SWW_E_DEAD && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1);

	/* the stop action that does not take: refused by the bus, or accepted and ignored: reported, never "halted" */
	for (i = 1; i <= 2; i++) {
		e_put(&f, &x);
		ramarr[0x3AF000u / 4] = 0x00010006u;
		eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
		f.halt_mode = i;
		CHECK(e_steps(&x) == -SWW_E_HALT && x.halted == 0 && x.restore == SWW_RS_FAILED && f.run == 1 && x.stop_code == SWW_E_HALT && !(x.flags & SWF_W17) && strstr(logbuf, "W17 STOP: halt not confirmed (code") != 0);
		e_put(&f, &x);
		eseqset(&f, 0, 0, 0, 0, 0);
		f.halt_mode = i;
		CHECK(e_steps(&x) == -SWW_E_UP && x.halted == 0 && x.restore == SWW_RS_FAILED && f.run == 1 && f.win2 == 0);
	}
	/* the firmware sets bus mastering when it starts: the preconditions fail at the next poll (code 5); memory decode is still on and BAR0 and the link are as they were, so the CA7 is re-halted anyway (review 0133 F1) */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.cmd_on_run = 4;
	CHECK(e_steps(&x) == -SWW_E_PRE && x.stop_code == SWW_E_PRE && x.restore == SWW_RS_OK && f.run == 0 && x.ca_ok == 1 && x.halted == 0 && x.polls == 0 && f.win2 == 0 && all_allowed(&f));
	CHECK(strstr(logbuf, "restore: EP command 0x100006 changed, re-halting the CA7 anyway\n") != 0 && strstr(logbuf, "skipped") == 0 && strstr(logbuf, "restore space 3 win 0x18120000 off 0x800 := 0x1 ok\n") != 0);
	/* the firmware clears memory decode as well (Command 0x4): the re-halt stays skipped, the config row (the link is up) still runs */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.cmd_on_run = 4;
	f.cmd_clr_on_run = 2;
	CHECK(e_steps(&x) == -SWW_E_PRE && x.restore == SWW_RS_SKIPPED && f.run == 1 && x.ca_ok == 0 && strstr(logbuf, "restore of group 4 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting the CA7 anyway") == 0);
	CHECK(f.nw >= 1 && f.w[f.nw - 1].space == SWW_CFG && f.w[f.nw - 1].off == 0x84 && f.w[f.nw - 1].val == 0 && strstr(logbuf, "restore of group 8 skipped") == 0);
	/* bus mastering on and BAR0 moved: the BAR0 check of the exception fails, skipped (6 never shows through 5, swl_wpre returns at the first failure) */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1 && (x.armed & SWW_RG_CA));
	f.cmd = 0x00100006u;
	f.bar0lo = 0x60000008u;
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && x.ca_ok == 0 && strstr(logbuf, "restore of group 4 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting the CA7 anyway") == 0 && f.nw == nw1 + 1);
	/* BAR0 high moved (bus mastering on): skipped too */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.cmd = 0x00100006u;
	f.bar0hi = 1u;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && x.ca_ok == 0 && strstr(logbuf, "restore of group 4 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting") == 0);
	/* the exception is for the CA7 row of a run that released it: another group armed (the sysmem wrapper) stays skipped, and with SWF_W16 clear the CA7 row does too */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.cmd = 0x00100006u;
	x.armed |= SWW_RG_SM;
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 0 && x.ca_ok == 1 && strstr(logbuf, "restore of group 2 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting") != 0 && f.nw == nw1 + 2);
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.cmd = 0x00100006u;
	x.flags &= ~SWF_W16;
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && x.ca_ok == 0 && strstr(logbuf, "restore of group 4 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting") == 0 && f.nw == nw1 + 1);
	/* before the release the config row is stored whatever the link says (levels 1-6 as in 0132); from the release on, a failed read of the preconditions (code 8) skips it too (review 0133 R2-F3: a skipped cfg 0x84 is volatile EP config, reset at the next boot) */
	e_put(&f, &x);
	x.flags |= SWF_RELOK;
	x.armed = SWW_RG_W84;
	f.ltssm = 0;
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_OK && f.nw == nw1 + 1 && f.w[nw1].space == SWW_CFG && f.w[nw1].off == 0x84 && strstr(logbuf, "skipped") == 0);
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.fail_rd_at = f.nrd + 1u;
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && f.nw == nw1 && strstr(logbuf, "restore of group 4 skipped, precondition 8") != 0 && strstr(logbuf, "restore of group 8 skipped, precondition 8") != 0);
	/* a read that fails while the exception looks at the Command, at BAR0 low, at BAR0 high (reads 20, 21, 22 of the restore: 2 + 14 of the link, 3 of swl_wpre, then these): skipped, nothing stored */
	for (i = 0; i < 3; i++) {
		e_put(&f, &x);
		eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
		CHECK(swl_w16(&x) == 0 && f.run == 1);
		f.cmd = 0x00100006u;
		f.fail_rd_at = f.nrd + 20u + i;
		nw1 = f.nw;
		swl_restore(&x);
		CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && x.ca_ok == 0 && strstr(logbuf, "restore of group 4 skipped, precondition 5") != 0 && strstr(logbuf, "re-halting") == 0 && f.nw == nw1 + 1);
	}
	/* the firmware changes its own IOCTL at once: the release is not confirmed, the stop action halts it */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.ioctl_on_run = 4;
	rc = e_steps(&x);
	CHECK(rc < 0 && x.stopped && x.restore == SWW_RS_OK && f.run == 0 && f.started == 1 && f.win2 == 0);

	/* the interrupt gate: a mailbox mask that is not 0 stops W16 before any store of the release (one store, the window select) */
	{
		static const uint32_t m[] = { 1u, 0x100u, 0x80000000u, 0xFFFFFFFEu, 0x00FF0000u };
		unsigned b;

		for (b = 0; b < sizeof m / sizeof m[0]; b++) {
			e_put(&f, &x);
			f.mbmask = m[b];
			CHECK(swl_w16_go(&x) == -SWW_E_IRQ && x.stop_code == SWW_E_IRQ && f.nw == 1 && f.started == 0 && f.run == 0 && f.win2 == 0 && x.restore == SWW_RS_NONE && !(x.flags & (SWF_RELOK | SWF_W16)) && x.armed == 0);
			CHECK(strstr(logbuf, "W16 STOP: interrupt unmasked") != 0);
		}
	}
	/* review 0133 F2: the host controller's interrupt enables (ELBI IRQ0/1/2_EN, pcie-exynos-rc.h:31-43), read only: each enable bit alone stops W16 before the release; status bits and other enable bits do not */
	{
		static const struct { unsigned reg; uint32_t v; } en[] = {
			{ 4, 0x00004000u }, { 4, 0x00010000u }, { 4, 0x00040000u }, { 4, 0x00100000u }, { 4, 0x00154000u }, { 4, 0xFFFFFFFFu },
			{ 5, 0x00000400u }, { 5, 0xFFFFFFFFu },
			{ 6, 0x00010000u }, { 6, 0x00020000u }, { 6, 0x01000000u }, { 6, 0x01030000u }, { 6, 0xFFFFFFFFu },
		};
		unsigned b;

		for (b = 0; b < sizeof en / sizeof en[0]; b++) {
			e_put(&f, &x);
			f.elbi_irq[en[b].reg] = en[b].v;
			CHECK(swl_w16_go(&x) == -SWW_E_IRQ && x.stop_code == SWW_E_IRQ && f.nw == 1 && f.started == 0 && f.run == 0 && f.win2 == 0 && x.restore == SWW_RS_NONE && !(x.flags & (SWF_RELOK | SWF_W16)) && x.armed == 0);
			CHECK(strstr(logbuf, "W16 STOP: interrupt unmasked") != 0 && strstr(logbuf, "W16 PCIe core mailbox int 0 mask 0\n") != 0 && strstr(logbuf, "enable bits outside") == 0);
		}
		/* the line, with the status first and the enable second in each pair */
		e_put(&f, &x);
		f.elbi_irq[0] = 0x11u; f.elbi_irq[1] = 0x22u; f.elbi_irq[2] = 0x33u; f.elbi_irq[4] = 0x00004000u; f.elbi_irq[5] = 0x0u; f.elbi_irq[6] = 0x0u;
		CHECK(swl_w16_go(&x) == -SWW_E_IRQ && strstr(logbuf, "W16 RC irq0 0x11/0x4000 irq1 0x22/0 irq2 0x33/0\n") != 0);
		/* every status bit with the enables 0 is not a stop (status is informational) */
		e_put(&f, &x);
		f.elbi_irq[0] = f.elbi_irq[1] = f.elbi_irq[2] = 0xFFFFFFFFu;
		eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
		CHECK(e_steps(&x) == 0 && x.halted == 1 && strstr(logbuf, "W16 RC irq0 0xffffffff/0 irq1 0xffffffff/0 irq2 0xffffffff/0\n") != 0);
		/* review 0133 R2-F1: an enable bit outside the three masks (the complement of each) also stops W16 before the release, one register at a time, with its own line */
		{
			static const struct { unsigned reg; uint32_t v; } ex[] = { { 4, ~0x00154000u }, { 5, ~0x00000400u }, { 6, ~0x01030000u } };

			for (b = 0; b < sizeof ex / sizeof ex[0]; b++) {
				e_put(&f, &x);
				f.elbi_irq[ex[b].reg] = ex[b].v;
				CHECK(swl_w16_go(&x) == -SWW_E_IRQ && x.stop_code == SWW_E_IRQ && f.nw == 1 && f.started == 0 && f.run == 0 && f.win2 == 0 && x.restore == SWW_RS_NONE && !(x.flags & (SWF_RELOK | SWF_W16)) && x.armed == 0);
				CHECK(strstr(logbuf, "W16 RC interrupt enable bits outside the INTx/link-down/MSI masks are set\n") != 0);
			}
		}
		/* the ELBI rows are run 5 and 0x00..0x18 only, read only */
		e_put(&f, &x);
		x.level = 6;
		CHECK(swl_rdx(&x, SWW_ELBI, 0, 0x10u, &sv) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0x00u, &sv) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0x2C8u, &sv) == 0);
		x.level = 7;
		CHECK(swl_rdx(&x, SWW_ELBI, 0, 0x00u, &sv) == 0 && swl_rdx(&x, SWW_ELBI, 0, 0x18u, &sv) == 0 && swl_rdx(&x, SWW_ELBI, 0, 0x1Cu, &sv) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0x14u, &sv) == 0);
		CHECK(swl_wr_ok(SWW_ELBI, 0, 0x10u, 0) < 0 && swl_wr_ok(SWW_ELBI, 0, 0x14u, 0) < 0 && swl_wr_ok(SWW_ELBI, 0, 0x18u, 0) < 0);       /* never stored */
	}
	e_put(&f, &x);
	f.mbmask = 0xFFFFFFFFu;
	CHECK(swl_w16(&x) == -SWW_E_DEAD && f.nw == 1 && f.started == 0);
	e_put(&f, &x);
	f.mbint = 0xFFFFFFFFu;
	CHECK(swl_w16(&x) == -SWW_E_DEAD && f.nw == 1 && f.started == 0);
	e_put(&f, &x);
	f.mbint = 0x1234u;                                                  /* a pending status with the mask 0 is not an interrupt: the release goes ahead */
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && strstr(logbuf, "W16 PCIe core mailbox int 0x1234 mask 0\n") != 0);
	e_put(&f, &x);
	f.mbmask = 1;
	CHECK(swl_w16(&x) == -SWW_E_IRQ && f.win0 == 0x18001000u && strstr(logbuf, "W16 PCIe core mailbox int 0 mask 0x1\n") != 0);

	/* before the first store of W16: the image and the pin again (AP-74), the vector, the token baseline */
	e_put(&f, &x);
	imgbuf[len / 2] ^= 1;
	CHECK(swl_w16(&x) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "image CRC") != 0);
	imgbuf[len / 2] ^= 1;
	e_put(&f, &x);
	x.img_len = len - 4u;
	x.img_crc = tcrc(imgbuf, len - 4u);
	CHECK(swl_w16(&x) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "firmware identity 1764014 bytes crc") != 0);
	e_put(&f, &x);
	ramarr[0] ^= 1u;
	CHECK(swl_w16(&x) == -SWW_E_READBACK && f.nw == 0 && f.started == 0 && strstr(logbuf, "W16 backplane 0 reads 0xea000011, not the reset vector 0xea000010") != 0);
	e_put(&f, &x);
	ramarr[E_BOOT / 4] = 0;
	CHECK(swl_w16(&x) == -SWW_E_READBACK && f.started == 0 && f.run == 0 && f.win2 == 0 && x.restore == SWW_RS_OK && !(x.flags & SWF_RELOK) && strstr(logbuf, "before the release, not the token") != 0);
	e_put(&f, &x);
	ramarr[E_BOOT / 4] = tok ^ 1u;
	CHECK(swl_w16(&x) == -SWW_E_READBACK && f.started == 0);
	/* the CA7 is not in the state W6 left it (IOCTL 0x21, reset released): the first store of the release refuses */
	e_put(&f, &x);
	f.ca.ioctl = 0x23u;
	CHECK(swl_w16(&x) == -SWW_E_COMP && f.started == 0 && f.run == 0 && f.ca.ctl == 0);
	e_put(&f, &x);
	f.ca.ctl = 1u;
	CHECK(swl_w16(&x) == -SWW_E_OLD && f.started == 0 && f.run == 0);
	e_put(&f, &x);
	f.ca.ioctl = 0x1u;
	CHECK(swl_w16(&x) == -SWW_E_COMP && f.started == 0);
	/* the pending-operation wait when the CPU ran: bounded, then the reset is asserted anyway */
	e_put(&f, &x);
	eseqset(&f, 0, 0, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1 && (x.armed & SWW_RG_CA));
	f.st_busy = 5;
	c0 = (uint32_t)f.clock_us;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_OK && f.run == 0 && f.st_at_assert == 0 && f.asserts_run == 1 && f.clock_us - c0 >= 50u && strstr(logbuf, "restore: CA7 RESET_ST 0 after 5 waits, asserting the reset") != 0);
	e_put(&f, &x);
	eseqset(&f, 0, 0, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.st_busy = 1000;
	c0 = (uint32_t)f.clock_us;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_OK && f.run == 0 && f.st_at_assert == 970 && f.asserts_run == 1 && f.clock_us - c0 >= 29u * 10u && f.clock_us - c0 < 40u * 10u && strstr(logbuf, "restore: CA7 RESET_ST 0x1 after 30 waits, asserting the reset") != 0);
	/* a restore of group CA before the release (the CPU never ran) does not wait: the halted CA7 of run A */
	e_put(&f, &x);
	x.flags &= ~SWF_W16;
	x.armed = SWW_RG_CA;
	f.win0 = SWW_WIN_CA7WRAP;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_OK && f.asserts_run == 0 && strstr(logbuf, "asserting the reset") == 0 && f.ca.ctl == 1);

	/* the gates of the release tuples, each alone (direct swl_wr from the state after run D; a refusal latches STOPPED, so every refusal starts from a fresh state) */
#define EP() do { e_put(&f, &x); x.flags |= SWF_RELOK; f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 0; f.ca.ioctl = 0x21; } while (0)
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_PHASE && f.nw == 0);                  /* needs SWF_RELOK */
	EP();
	x.flags &= ~SWF_W14;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_PHASE && f.nw == 0);                  /* needs W14 */
	EP();
	x.level = 6;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_REPEAT && f.nw == 0);                 /* run 5: not at level 6 */
	EP();
	x.level = 6;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_LEVEL && f.nw == 0);
	EP();
	x.level = 5;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_REPEAT && f.nw == 0);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_ORDER && f.nw == 0);                  /* in order only */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_ORDER && f.nw == 0);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_ORDER && f.nw == 0);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 0x13) == -SWW_E_DENIED && f.nw == 0);               /* other values */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 2) == -SWW_E_DENIED && f.nw == 0);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 2) == -SWW_E_DENIED && f.nw == 0);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x404, 3) == -SWW_E_DENIED && f.nw == 0);                  /* ioctrlclear is not on the list */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && f.nw == 1 && (x.armed & SWW_RG_CA) && f.ca.ctl == 1);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_REPEAT && f.nw == 2);                 /* once (the second store is the restore of the armed group) */
	EP();
	f.ca.ioctl = 0x23u;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_COMP && f.nw == 0);                   /* IOCTL must read 0x21 */
	EP();
	f.ca.ctl = 1u;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_OLD && f.nw == 0);                    /* the reset must be released */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0);
	f.ca.ctl = 0;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_COMP && f.ca.ioctl == 0x21);          /* RESET_CTL must read 1 */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0 && f.ca.ioctl == 3 && f.run == 0 && f.started == 0);
	f.ca.ioctl = 0x23u;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_COMP && f.run == 0 && f.ca.ctl == 1);  /* the reset is released only while IOCTL reads 0x3 */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0 && f.run == 1 && f.started == 1 && f.ca.ctl == 0);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_REPEAT);
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0);
	f.ca.ioctl = 0x23u;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_OLD);                                 /* the last IOCTL needs the old value 0x3 */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0);
	f.ca.ctl = 1;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_COMP);                                /* and the reset released */
	EP();
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0 &&
	      swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == 0 && f.ca.ioctl == 1 && f.run == 1 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_REPEAT);
	/* a live window that is not the CA7 wrapper's */
	EP();
	f.win0 = SWW_WIN_SMWRAP;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 1) == -SWW_E_WIN && f.nw == 0);
#undef EP
	/* the poll window tuple: once, old 0, a RAM that ends at or below 4 MiB needs none */
	e_put(&f, &x);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == 0 && f.win2 == 0x400000u && (x.armed & SWW_RG_W84) && f.nw == 1);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == -SWW_E_REPEAT && f.nw == 2 && f.win2 == 0);       /* the second store is the restore of cfg 0x84 */
	e_put(&f, &x);
	f.win2 = 0x1000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == -SWW_E_OLD && f.nw == 0);
	e_put(&f, &x);
	x.level = 6;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == -SWW_E_REPEAT && f.nw == 0);                          /* run 5 only */
	e_put(&f, &x);
	x.ram_bytes = 0x1F0000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == -SWW_E_TAIL && f.nw == 0);
	e_put(&f, &x);
	x.flags &= ~SWF_W14;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0x400000u) == -SWW_E_PHASE && f.nw == 0);
	/* the window back for a shared structure below the tail: only after the poll window, only after the release started, old 0x400000 */
	e_put(&f, &x);
	f.win2 = 0x400000u;
	x.flags |= SWF_W16;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_ORDER && f.nw == 0);
	e_put(&f, &x);
	f.win2 = 0x400000u;
	x.done |= 1ull << SWT_WIN2_TAIL_E;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_PHASE && f.nw == 0);
	e_put(&f, &x);
	f.win2 = 0x1000u;
	x.done |= 1ull << SWT_WIN2_TAIL_E;
	x.flags |= SWF_W16;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_OLD && f.nw == 0);
	e_put(&f, &x);
	f.win2 = 0x400000u;
	x.done |= 1ull << SWT_WIN2_TAIL_E;
	x.flags |= SWF_W16;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == 0 && f.win2 == 0 && f.nw == 1);
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_REPEAT && f.nw == 1);
	e_put(&f, &x);
	f.win2 = 0x400000u;
	x.done |= 1ull << SWT_WIN2_TAIL_E;
	x.flags |= SWF_W16;
	x.level = 6;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_REPEAT && f.nw == 0);
	e_put(&f, &x);
	f.win2 = 0x400000u;
	x.done |= 1ull << SWT_WIN2_TAIL_E;
	x.flags |= SWF_W16;
	x.ram_bytes = 0x1F0000u;
	CHECK(swl_wr(&x, SWW_CFG, 0, 0x84, 0) == -SWW_E_TAIL && f.nw == 0);                                   /* a RAM that ends at 4 MiB or below has no tail window to leave */

	/* each tuple of the release chain on its own: the level (run 5), the phase flag, the group it arms, once (the earlier tuples of the chain are marked done by hand) */
#define ED(n) ((1ull << SWT_REL_ASSERT) * ((n) >= 1) | (1ull << SWT_REL_IOCTL_MID) * ((n) >= 2) | (1ull << SWT_REL_RELEASE) * ((n) >= 3))
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP;
	x.level = 6;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_REPEAT && f.nw == 0);                 /* the starting store is a repeat of W6's below run 5 */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP;
	x.level = 6;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_LEVEL && f.nw == 0);
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= ED(2);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_PHASE && f.nw == 0 && f.started == 0);        /* RELOK missing: no starting store */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= ED(2); x.flags |= SWF_RELOK; x.armed = 0;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0 && f.started == 1 && (x.armed & SWW_RG_CA));
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 0; f.ca.ioctl = 0x3;
	x.done |= ED(3);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_PHASE && f.nw == 0);
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 0; f.ca.ioctl = 0x3;
	x.done |= ED(3); x.flags |= SWF_RELOK; x.armed = 0;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == 0 && (x.armed & SWW_RG_CA));
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x21;
	x.done |= ED(1);
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_PHASE && f.nw == 0);
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x21;
	x.done |= ED(1); x.flags |= SWF_RELOK; x.armed = 0;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == 0 && (x.armed & SWW_RG_CA) && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_REPEAT);        /* once */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= ED(2); x.flags |= SWF_RELOK;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == 0 && swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_REPEAT);        /* once, by the tuple (the glue is not here) */
#undef ED

	/* the wait for pending operations before the release: a halted CA7 that has some stops the run before any store of the release (nothing started, IOCTL still 0x21) */
	e_put(&f, &x);
	f.ca.st = 1;
	CHECK(swl_w16_go(&x) == -SWW_E_WAIT && f.started == 0 && f.ca.ioctl == 0x21 && f.ca.ctl == 0 && x.restore == SWW_RS_OK && (x.flags & SWF_RELOK) != 0);
	CHECK(f.nw == 4 && f.w[2].off == 0x80 && f.w[3].off == 0x84 && f.w[3].val == 0);                       /* the window selects and the poll window and its restore only: no store to a wrapper */
	e_put(&f, &x);
	f.st_stuck_ca = 1;                                                                                      /* the status sticks when the reset is asserted: stop before IOCTL is written */
	CHECK(swl_w16_go(&x) == -SWW_E_WAIT && f.started == 0 && f.ca.ioctl == 0x21 && f.ca.ctl == 1 && f.run == 0 && x.restore == SWW_RS_OK);
	e_put(&f, &x);
	f.st_after_io3 = 1;                                                                                     /* the status goes to 1 when IOCTL := 0x3 is written: stop before the reset is released */
	CHECK(swl_w16_go(&x) == -SWW_E_WAIT && f.started == 0 && f.ca.ioctl == 3 && f.ca.ctl == 1 && f.run == 0 && x.restore == SWW_RS_OK);
	/* pending operations right after the CPU started are waited for, not a stop (DHD goes on, aiutils.c:1257) */
	e_put(&f, &x);
	f.st_busy = 5;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	c0 = (uint32_t)f.clock_us;
	CHECK(e_steps(&x) == 0 && strstr(logbuf, "W16 wrapper 0x18120000 after the release: ioctl 0x1 reset_ctl 0 reset_st 0\n") != 0 && f.st_busy == 0 && f.clock_us - c0 >= 60u);
	e_put(&f, &x);
	f.st_busy = 1000;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.halted == 1 && f.st_busy == 939 && strstr(logbuf, "reset_st 0x1\n") != 0);       /* the status stays: logged, no stop */
	/* ... and the status that is 1 at the 1 us read after the release is logged and no stop either; a reset that is asserted again, or an IOCTL that changed, is */
	for (i = 1; i <= 3; i++) {
		e_put(&f, &x);
		eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
		f.after1 = i;
		rc = e_steps(&x);
		CHECK(i == 1 ? (rc == 0 && x.halted == 1 && strstr(logbuf, "after the release: ioctl 0x1 reset_ctl 0 reset_st 0x1\n") != 0) : (rc == -SWW_E_STATE && x.restore == SWW_RS_OK && f.run == 0));
	}
	/* a stop inside the release (the CPU already started) waits for the pending operations before it asserts the reset */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.ioctl_on_run = 4;
	f.st_busy = 1000;
	CHECK(e_steps(&x) == -SWW_E_OLD && f.run == 0 && f.started == 1 && x.restore == SWW_RS_OK && f.st_at_assert == 940);                                 /* 30 reads of the spin after the start, 30 of the idle wait of the restore */
	/* a failing read of the state in the timeout line is reported in it */
	e_put(&f, &x);
	eseqset(&f, 0, 0, 0, 0, 0);
	f.fail_c88 = 1;
	CHECK(e_steps(&x) == -SWW_E_UP && strstr(logbuf, "cfg 0x88 0 (a state read failed)\n") != 0);
	/* the firmware sets bus mastering after the second poll: the next poll's preconditions stop the run (code 5); the CA7 is re-halted anyway (review 0133 F1) */
	e_put(&f, &x);
	eseqset(&f, 1, tok, 0, 0, 0);
	f.cmd_at_poll = 2;
	CHECK(e_steps(&x) == -SWW_E_PRE && x.polls == 2 && x.restore == SWW_RS_OK && f.run == 0 && x.ca_ok == 1 && strstr(logbuf, "restore: EP command 0x100006 changed, re-halting the CA7 anyway\n") != 0 && strstr(logbuf, "skipped") == 0);
	/* the link is not in L0 (the four link checks, each alone): no BAR0 re-halt and no config store (review 0133 F4); the Command check (5) and later ones (6..8) do not stop the config row */
	for (i = 0; i < 4; i++) {
		e_put(&f, &x);
		eseqset(&f, 1, tok, 0, 0, 0);
		CHECK(swl_w16(&x) == 0 && f.run == 1);
		if (i == 0) f.iso = 0;
		if (i == 1) f.ltssm = 0;
		if (i == 2) f.a0[1] ^= 1u;
		if (i == 3) f.a1[1] ^= 1u;
		nw1 = f.nw;
		swl_restore(&x);
		snprintf(t, sizeof t, "restore of group 4 skipped, precondition %u\n", i + 1);
		CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && f.nw == nw1 && x.ca_ok == 0 && strstr(logbuf, t) != 0 && strstr(logbuf, "restore of group 8 skipped") != 0 && strstr(logbuf, "restore space") == 0);
	}
	e_put(&f, &x);
	eseqset(&f, 1, tok, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.cmd = 0x00000004u;                                                /* code 5 and memory decode off: BAR0 skipped, the config row runs */
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && f.nw == nw1 + 1 && f.w[nw1].space == SWW_CFG && f.w[nw1].off == 0x84 && strstr(logbuf, "restore of group 8 skipped") == 0);
	e_put(&f, &x);
	eseqset(&f, 1, tok, 0, 0, 0);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	f.a2[1] ^= 1u;                                                      /* code 7 (ATU region 2) is behind the Command check: the CA7 row skipped (not 5), the config row runs */
	nw1 = f.nw;
	swl_restore(&x);
	CHECK(x.restore == SWW_RS_SKIPPED && f.run == 1 && f.nw == nw1 + 1 && f.w[nw1].space == SWW_CFG && strstr(logbuf, "restore of group 4 skipped, precondition 7") != 0);
	/* the two ends of the window split: the last word below the tail window and the first word of it */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3FFFFCu, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.shared == 0x3FFFFCu && f.nw == 10);
	e_put(&f, &x);
	eseqset(&f, 1, 0x400000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.shared == 0x400000u && f.nw == 9);
	/* the stop names */
	e_put(&f, &x);
	eseqset(&f, 1, 0x3AF002u, 0, 0, 0);
	CHECK(e_steps(&x) == -SWW_E_SHARED && strstr(logbuf, "W17 STOP: shared address (code 28)\n") != 0);

	/* the ordering of the chain: the starting store refuses to follow the assert alone, the last IOCTL refuses to follow fewer than the three before it */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= 1ull << SWT_REL_ASSERT; x.flags |= SWF_RELOK;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_ORDER && f.nw == 0 && f.started == 0);     /* the release needs IOCTL 0x3 stored */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= (1ull << SWT_REL_ASSERT) | (1ull << SWT_REL_IOCTL_MID); x.flags |= SWF_RELOK;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_ORDER && f.nw == 0);                  /* IOCTL 0x1 needs the release stored */
	/* W14 is a flag of every tuple of the chain */
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x21;
	x.done |= 1ull << SWT_REL_ASSERT; x.flags |= SWF_RELOK; x.flags &= ~SWF_W14;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 3) == -SWW_E_PHASE && f.nw == 0);
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 1; f.ca.ioctl = 0x3;
	x.done |= (1ull << SWT_REL_ASSERT) | (1ull << SWT_REL_IOCTL_MID); x.flags |= SWF_RELOK; x.flags &= ~SWF_W14;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x800, 0) == -SWW_E_PHASE && f.nw == 0 && f.started == 0);
	e_put(&f, &x);
	f.win0 = SWW_WIN_CA7WRAP; f.ca.ctl = 0; f.ca.ioctl = 0x3;
	x.done |= (1ull << SWT_REL_ASSERT) | (1ull << SWT_REL_IOCTL_MID) | (1ull << SWT_REL_RELEASE); x.flags |= SWF_RELOK; x.flags &= ~SWF_W14;
	CHECK(swl_wr(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0x408, 1) == -SWW_E_PHASE && f.nw == 0);
	/* bits above the IOCTL mask (the wrapper's IOCTL has more bits than the 14 the masks name): the release goes through, each read compares the masked word */
	e_put(&f, &x);
	f.ca.ioctl = 0x8021u;
	f.ioctl_noise = 0x8000u;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.halted == 1 && f.ca.ioctl == 0x8001u && strstr(logbuf, "after the release: ioctl 0x8001 reset_ctl 0 reset_st 0\n") != 0);

	/* the polls of RESET_ST before the release: pending operations that clear within 29 reads are waited for, 30 are not (DHD: SPINWAIT 300 us), and nothing is stored before the wait ended */
	e_put(&f, &x);
	f.st_pre = 29;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	CHECK(e_steps(&x) == 0 && x.halted == 1 && f.st_pre == 0);
	e_put(&f, &x);
	f.st_pre = 30;
	CHECK(swl_w16_go(&x) == -SWW_E_WAIT && f.started == 0 && f.ca.ioctl == 0x21 && f.ca.ctl == 0 && f.st_pre == 0);
	/* the reset that releases itself right after it was asserted: the stop is the wait for RESET_CTL, before IOCTL is written */
	e_put(&f, &x);
	f.rc_clear_at_10 = 1;
	CHECK(swl_w16_go(&x) == -SWW_E_WAIT && f.started == 0 && f.ca.ioctl == 0x21 && x.restore == SWW_RS_OK && f.run == 0);
	/* the first poll reads 0: that is printed too; a firmware that stops at 0 is a timeout, not a shared address */
	e_put(&f, &x);
	ramarr[0x3AF000u / 4] = 0x00010006u;
	eseqset(&f, 3, 0, 0, 0x3AF000u, 0);
	CHECK(e_steps(&x) == 0 && x.polls == 3 && count_str("W17 poll ") == 2 && strstr(logbuf, "W17 poll 1 (0 ms after the release) word 0x00000000\n") != 0);
	e_put(&f, &x);
	eseqset(&f, 1, 0, 0, 0, 0);
	CHECK(e_steps(&x) == -SWW_E_UP && x.polls == 100 && x.shared == 0 && x.restore == SWW_RS_OK);
	/* the poll counters start at 0 for W17: a precondition failure at its first poll leaves them there */
	e_put(&f, &x);
	CHECK(swl_w16(&x) == 0 && f.run == 1);
	x.polls = 77; x.shared = 55;
	f.cmd ^= 4u;
	CHECK(swl_w17(&x) == -SWW_E_PRE && x.polls == 0 && x.shared == 0);
	/* RAM that ends at 4 MiB or just above: the shared-area word is in window 0 or in the tail window, the poll window store is made only for the second */
	e_put(&f, &x);
	x.ram_bytes = 0x200000u;
	ramarr[0x3FFFFCu / 4] = tok;
	ramarr[0x3AF000u / 4] = 0x00010006u;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.boot_bp = 0x3FFFFCu;
	CHECK(e_steps(&x) == 0 && x.shared == 0x3AF000u && f.nw == 7 && f.win2 == 0 && f.w[1].off == 0x80 && f.w[1].val == 0x18120000u && x.restore == SWW_RS_OK);
	e_put(&f, &x);
	x.ram_bytes = 0x202000u;
	ramarr[0x401FFCu / 4] = tok;
	ramarr[0x3AF000u / 4] = 0x00010006u;
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.boot_bp = 0x401FFCu;
	CHECK(e_steps(&x) == 0 && x.shared == 0x3AF000u && f.nw == 10 && f.w[1].off == 0x84 && f.w[1].val == 0x400000u && f.win2 == 0);
	/* the read allowlist of run E: the mailbox int and mask of the PCIe core at run 5, nothing around them, no other window (the fake answers every offset of the page: only the allowlist refuses) */
	e_put(&f, &x);
	f.any_pcie_read = 1;
	f.win0 = SWW_WIN_PCIE;
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC30u, &sv) == 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, &sv) == 0);
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC2Cu, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC38u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC31u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC00u, &sv) < 0);
	f.win0 = SWW_WIN_SYSMEM;
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_SYSMEM, 0xC34u, &sv) < 0);
	x.level = 6;
	f.win0 = SWW_WIN_PCIE;
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC30u, &sv) < 0);

	/* stage level: the whole run from the observed entry state; level 6 is untouched and never starts the CPU */
	obsb(&f, 336);
	eseqset(&f, 0, 0, 0, 0, 0);
	CHECK(runc(&f, &x, 6, len, pin) == 0 && f.started == 0 && strstr(logbuf, "W16") == 0 && strstr(logbuf, "W17") == 0 && strstr(logbuf, "level 6 done w14") != 0 && f.nw == ef.nw);
	obsb(&f, 336);
	f.rom0 = 1;                                                         /* a level 6 stop (address 0 aliases a ROM bank) does not say anything about the CA7 */
	CHECK(runc(&f, &x, 6, len, pin) == -SWW_E_READBACK && strstr(logbuf, "stage 3 level 6 stopped at W14 restore=none") != 0 && strstr(logbuf, "never released") == 0 && strstr(logbuf, "CA7") == 0);
	obsb(&f, 336);
	ramarr[0x3AF000u / 4] = 0x00010006u;
	eseqset(&f, 4, tok, 0, 0, 0x3AF000u);
	CHECK(runc(&f, &x, 7, len, pin) == 0);
	CHECK(x.stopped == 0 && x.halted == 1 && x.polls == 4 && x.shared == 0x3AF000u && x.version == 6 && f.run == 0 && f.started == 1 && f.win2 == 0 && x.restore == SWW_RS_OK && all_allowed(&f) && f.nw == ef.nw + 10u);
	CHECK(strstr(logbuf, "SWEET WLAN: stage 3 level 7 done shared=0x3af000 version=6 polls=4 halted=1\n") != 0 && strstr(logbuf, "stopped") == 0 && strstr(logbuf, "level 6 done") == 0 && strstr(logbuf, "W16 CA7 released\n") != 0);
	/* the same run, the firmware never comes up */
	obsb(&f, 336);
	eseqset(&f, 0, 0, 0, 0, 0);
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_UP && x.polls == 100 && f.run == 0 && f.started == 1 && x.restore == SWW_RS_OK);
	CHECK(strstr(logbuf, "stage 3 level 7 stopped at W17 restore=ok\n") != 0 && strstr(logbuf, "stage 3 level 7 CA7 released and reset again (read back), halted=1\n") != 0 && strstr(logbuf, "level 7 done") == 0);
	/* the source image changes between W14 and W16: the identity is proven again by the stage, nothing is released */
	obsb(&f, 336);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.flip_at_vector = 1;
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_IMG && f.started == 0 && f.run == 0 && strstr(logbuf, "stage 3 level 7 stopped at W16") != 0 && strstr(logbuf, "image CRC") != 0);
	imgbuf[imglen / 2] ^= 1;
	CHECK(tcrc(imgbuf, len) == pin);
	/* ... and the stop action does not take */
	obsb(&f, 336);
	eseqset(&f, 0, 0, 0, 0, 0);
	f.halt_mode = 2;
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_UP && f.run == 1 && x.restore == SWW_RS_FAILED);
	CHECK(strstr(logbuf, "stage 3 level 7 stopped at W17 restore=failed\n") != 0 && strstr(logbuf, "stage 3 level 7 CA7 RELEASED AND NOT CONFIRMED IN RESET, halted=0\n") != 0 && strstr(logbuf, "halted=1") == 0);
	/* review 0133 F3: the stop is before the store that starts the CPU (IOCTL := 0x3 refused, the reset is asserted and restored): never released, no wait for pending operations, and the flag is not set */
	obsb(&f, 336);
	eseqset(&f, 1, 0x3AF000u, 0, 0, 0);
	f.fail_wr_at = ef.nw + 5u;
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_WR && f.started == 0 && f.run == 0 && x.restore == SWW_RS_OK && x.ca_ok == 1 && !(x.flags & SWF_W16) && strstr(logbuf, "asserting the reset") == 0);
	CHECK(strstr(logbuf, "stage 3 level 7 stopped at W16 restore=ok\n") != 0 && strstr(logbuf, "stage 3 level 7 CA7 never released, halted=1\n") != 0 && strstr(logbuf, "reset again") == 0);
	/* the CA7 re-halt holds and only the poll window restore (cfg 0x84 := 0) fails: the CPU is reported halted, the restore as failed */
	obsb(&f, 336);
	eseqset(&f, 0, 0, 0, 0, 0);
	f.fail_wr_at = ef.nw + 9u;
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_UP && f.run == 0 && f.started == 1 && x.restore == SWW_RS_FAILED && x.ca_ok == 1 && (x.flags & SWF_W16));
	CHECK(strstr(logbuf, "restore space 3 win 0x18120000 off 0x800 := 0x1 ok\n") != 0 && strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 FAILED\n") != 0);
	CHECK(strstr(logbuf, "stage 3 level 7 stopped at W17 restore=failed\n") != 0 && strstr(logbuf, "stage 3 level 7 CA7 released and reset again (read back), halted=1\n") != 0 && strstr(logbuf, "RELEASED AND NOT") == 0);
	/* a stop before the release says the CA7 was never released */
	obsb(&f, 336);
	f.mbmask = 0x100;
	CHECK(runc(&f, &x, 7, len, pin) == -SWW_E_IRQ && f.started == 0 && f.run == 0);
	CHECK(strstr(logbuf, "stage 3 level 7 stopped at W16 restore=none\n") != 0 && strstr(logbuf, "stage 3 level 7 CA7 never released, halted=1\n") != 0);
	/* the firmware identity before the first store at level 7 (review 0130 finding 8, kept by run E) */
	imgbuf[100] ^= 1;
	obsb(&f, 336);
	CHECK(runc(&f, &x, 7, len, tcrc(imgbuf, len)) == -SWW_E_IMG && f.nw == 0 && f.nrd == 0 && strstr(logbuf, "firmware identity") != 0 && strstr(logbuf, "stage 3 level 7 stopped at W14 restore=none") != 0);
	imgbuf[100] ^= 1;
	/* the run A stop at the CA7 halt (wrapper status stuck) is a halted CPU that is never waited for */
	obsb(&f, 336);
	f.st_stuck_ca = 1;
	CHECK(runc(&f, &x, 7, len, pin) < 0 && f.started == 0 && f.asserts_run == 0 && strstr(logbuf, "asserting the reset") == 0);

	/* every single fault, every position, both shared-structure placements */
	for (i = 0; i < 4; i++) {
		e_sweep(0x3AF000u, (int)i);
		e_sweep(0x498000u, (int)i);
	}
}

/* ---------------------------------------------------------------- run F1 (patch 0134): the read-only dumps between the poll and the re-halt ---------------------------------------------------------------- */

/* the layouts restated from the headers (wlan-stage5/bcmpcie.h:290-297, 305-339, 358-433), independent of the header's constants */
struct t_ringmem { uint16_t idx; uint8_t type, rsvd; uint16_t max_item, len_items; uint32_t lo, hi; };
struct t_ringinfo { uint32_t ringmem_ptr, h2d_w, h2d_r, d2h_w, d2h_r, hostaddr[8]; uint16_t max_tx, max_sub, max_cmpl, max_vdevs; uint32_t ifrm[2], hwa_h2d, hwa_d2h; };
struct t_shared {
	uint32_t flags, trap, assert_exp, assert_file, assert_line, console, msgtrace, fwid;
	uint16_t lfrag, max_rxbufs;
	uint32_t rxoffset, h2d_mb, d2h_mb, rings, scratch_len, scratch[2], scb[2], scb_size, buzz, flags2, host_cap, host_trap[2], fatal, hoffload[2], flags3, host_cap2, host_cap3, etd, txext, ewp;
	uint8_t aggr[8];
};

static void f1_layout_check(void)         /* run time, not _Static_assert: a wrong constant of the header must FAIL this test (the mutants build it), not stop the build */
{
	CHECK(sizeof(struct t_shared) == 4u * SWW_SH_WORDS && sizeof(struct t_ringinfo) == 4u * SWW_RI_WORDS && sizeof(struct t_ringmem) * SWW_RM_N == 4u * SWW_RM_WORDS && sizeof(struct t_ringmem) == 16u);
	CHECK(offsetof(struct t_shared, console) == 4u * SWW_SH_CONSOLE && offsetof(struct t_shared, h2d_mb) == 4u * SWW_SH_H2D_MB && offsetof(struct t_shared, d2h_mb) == 4u * SWW_SH_D2H_MB && offsetof(struct t_shared, rings) == 4u * SWW_SH_RINGINFO);
	CHECK(offsetof(struct t_ringinfo, ringmem_ptr) == 0 && offsetof(struct t_ringmem, max_item) == 4 && offsetof(struct t_ringmem, lo) == 8);
	CHECK(SWW_CON_BUF == 8u && SWW_CON_IDX == 16u && SWW_CON_MAX == 4096u && SWW_CON_LINE == 120u && SWW_PCI_FUNCINT == 0xC10u && SWW_PCI_PWRINT == 0xC18u && SWW_SH_ASSERT == 0x200u && SWW_SH_TRAP == 0x400u && SWW_F1_LEVEL == 8);
}

#define F1_SH   0x480000u                               /* in the tail window */
#define F1_CONS 0x480200u
#define F1_MB   0x480300u
#define F1_RI   0x480400u
#define F1_RM   0x480500u
#define F1_CBUF 0x482000u

static void f1_put(uint32_t bp, uint32_t v)
{
	ramarr[bp / 4u] = v;
}

static void f1_byte(uint32_t bp, uint8_t b)
{
	uint32_t s = 8u * (bp & 3u);

	ramarr[bp / 4u] = (ramarr[bp / 4u] & ~(0xFFu << s)) | ((uint32_t)b << s);
}

static void f1_text(uint32_t cbuf, uint32_t size, uint32_t pos, const char *t)      /* t at position pos of the cyclic buffer */
{
	for (; *t; t++, pos = (pos + 1u) % size)
		f1_byte(cbuf + pos, (uint8_t)*t);
}

/* a shared structure at sh with the pointers the caller names; word i of it is 0xA5000000 | i, word i of ring_info 0xB0000000 | i; the console buffer (size, write index idx) is empty */
static void f1_layout(uint32_t sh, uint32_t cons, uint32_t mb, uint32_t ri, uint32_t rm, uint32_t cbuf, uint32_t csize, uint32_t cidx)
{
	uint32_t i;

	for (i = 0; i < SWW_SH_WORDS; i++)
		f1_put(sh + 4u * i, 0xA5000000u | i);
	f1_put(sh, 0x70050107u);
	f1_put(sh + 0x14, cons);
	f1_put(sh + 0x28, mb);
	f1_put(sh + 0x2C, mb + 4u);
	f1_put(sh + 0x30, ri);
	f1_put(mb, 0x11111111u);
	f1_put(mb + 4u, 0x22222222u);
	for (i = 0; i < SWW_RI_WORDS; i++)
		f1_put(ri + 4u * i, 0xB0000000u | i);
	f1_put(ri, rm);
	for (i = 0; i < SWW_RM_N; i++) {
		f1_put(rm + 16u * i, i | ((0x10u + i) << 16));
		f1_put(rm + 16u * i + 4u, (64u + i) | ((40u + i) << 16));
		f1_put(rm + 16u * i + 8u, 0x80000000u | (i << 12));
		f1_put(rm + 16u * i + 12u, 8u + i);
	}
	for (i = 0; i < 5u; i++)
		f1_put(cons + 4u * i, 0);
	f1_put(cons + 8u, cbuf);
	f1_put(cons + 12u, csize);
	f1_put(cons + 16u, cidx);
	for (i = 0; i < csize / 4u && csize <= 0x4000u; i++)
		f1_put(cbuf + 4u * i, 0);
}

static void f1_new(struct fake *f, struct swl_wx *x)         /* a fresh level 8 run from the state after run D */
{
	e_put(f, x);
	x->level = SWW_F1_LEVEL;
	f->bar2_n = 0; f->poke_at = 0; f->oob = 0;
	f->fc10 = 0x11u; f->fc14 = 0x22u; f->fc18 = 0x33u; f->mbint = 5u;
	f->elbi_irq[0] = 0x900u; f->elbi_irq[1] = 0x3800a000u; f->elbi_irq[2] = 4u;
	f->guard_lo = 0x400000u; f->guard_hi = E_END;
}

static int f1_go(struct fake *f, struct swl_wx *x, uint32_t sh)       /* the poll finds sh at its second read: W16 + W17 + F1 */
{
	eseqset(f, 2, x->tok, sh, 0, 0);
	return e_fast(x);
}

#ifndef F1_NRD7
#define F1_NRD7 310                                    /* reads of the same scenario at level 7, measured with the header of patch 0133 (identical log and counts, level 7 is unchanged) */
#endif
#define F1_ALL (SWW_F1_SH | SWW_F1_RI | SWW_F1_RM | SWW_F1_MB | SWW_F1_CON | SWW_F1_PCI | SWW_F1_ELBI | SWW_F1_CFG)
#define F1_NOPCI (F1_ALL & ~SWW_F1_PCI)                  /* the dumps before the PCIe core group (which runs after the re-halt, review 0134 F1) */

struct f1_ptr { uint32_t slot, bytes, miss; };

/* FNV-1a 64 over the whole log text, the read / store / BAR2 read counts and every recorded store of the run in `f`: the byte-identity pin of levels 7 and 8 (patch 0135 must not change them) */
/* patch 0137: "WIFI GAP:" lines carry running counts of process-wide state; the byte-identity pins below compare the rest of the log */
static void gap_strip(char *s)
{
	char *w = s, *r = s;

	while (*r) {
		size_t l = strcspn(r, "\n");

		if (strncmp(r, "WIFI GAP", 8) == 0) {
			r += l + (r[l] ? 1 : 0);
		} else {
			memmove(w, r, l + (r[l] ? 1 : 0));
			w += l + (r[l] ? 1 : 0);
			r += l + (r[l] ? 1 : 0);
		}
	}
	*w = 0;
}

static uint64_t pin_hash(const struct fake *f)
{
	static char tmp[1 << 16];
	uint64_t h = 1469598103934665603ull;
	uint32_t v[7];
	unsigned i, j;
	const unsigned char *b;

	v[0] = f->nrd; v[1] = f->nw; v[2] = f->bar2_n; v[3] = f->nw < 512u ? f->nw : 512u; v[4] = f->win0; v[5] = f->win2; v[6] = f->run;
	memcpy(tmp, logbuf, loglen < sizeof tmp ? loglen + 1u : sizeof tmp);
	tmp[sizeof tmp - 1u] = 0;
	gap_strip(tmp);
	for (i = 0; tmp[i]; i++)
		h = (h ^ (unsigned char)tmp[i]) * 1099511628211ull;
	for (i = 0; i < 7; i++)
		for (j = 0, b = (const unsigned char *)&v[i]; j < 4; j++)
			h = (h ^ b[j]) * 1099511628211ull;
	for (i = 0; i < v[3]; i++) {
		uint32_t w[4];

		w[0] = (uint32_t)f->w[i].space; w[1] = f->w[i].win; w[2] = f->w[i].off; w[3] = f->w[i].val;
		for (j = 0, b = (const unsigned char *)w; j < 16; j++)
			h = (h ^ b[j]) * 1099511628211ull;
	}
	return h;
}

/* the full set of checks of one run that ended without a stop and left the CPU in reset */
static int f1_clean(const struct fake *f, const struct swl_wx *x)
{
	return x->stopped == 0 && x->halted == 1 && x->restore == SWW_RS_OK && f->run == 0 && f->started == 1 && f->oob == 0 && all_allowed(f);
}

/* one stop: the CPU is in reset again, the restore is ok, the stop code is the expected one */
static int f1_stopped(const struct fake *f, const struct swl_wx *x, int rc, int code)
{
	return rc == -code && x->stop_code == code && x->stopped == 1 && x->restore == SWW_RS_OK && x->ca_ok == 1 && f->run == 0 && f->started == 1 && f->oob == 0 && all_allowed(f);
}

static void f1_sweep(int kind)
{
	static struct fake f, h;
	static struct swl_wx x;
	unsigned k, n, benign = 0, zero_in = 0, w84n = 0;
	int rc, w84;

	f1_new(&h, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 0x8u);
	CHECK(f1_go(&h, &x, F1_SH) == 0 && x.f1 == F1_ALL && h.run == 0);
	n = kind == 0 ? h.nrd : h.nw;
	CHECK(kind == 0 ? n > 300 : n == 11u);
	for (k = 1; k <= n + 2; k++) {
		f1_new(&f, &x);
		f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 0x8u);
		if (kind == 0) f.fail_rd_at = k;
		if (kind == 1) f.fail_wr_at = k;
		if (kind == 2) f.drop_at = k;
		if (kind == 3) f.corrupt_at = k;
		rc = f1_go(&f, &x, F1_SH);
		CHECK(all_allowed(&f));
		CHECK(f.started <= 1 || (kind == 3 && x.restore == SWW_RS_FAILED));
		CHECK(!f.run || x.restore != SWW_RS_OK);                        /* a CPU that still runs is never reported as reset again */
		CHECK(!f.started || (x.flags & SWF_W16));
		CHECK(rc != 0 || x.halted);                                  /* a clean run halted the CPU; a stop after the re-halt (the PCIe core group) keeps halted */
		CHECK(!x.halted || (!f.run && x.ca_ok));
		CHECK(f.oob == 0);
		if (rc != 0 && strstr(logbuf, "W17 STOP:") != 0)
			CHECK(strstr(strstr(logbuf, "W17 STOP:"), "F1 ") == 0);       /* nothing of F1 is printed after the stop line */
		if (rc == 0) {
			w84 = strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 FAILED\n") != 0;
			CHECK(f.run == 0 && x.ca_ok && x.f1 == (w84 ? F1_NOPCI : F1_ALL) && (x.restore == SWW_RS_OK || (w84 && x.restore == SWW_RS_FAILED)));      /* a restore that failed on the last row skips the PCIe core group (swl_w17) */
			if (w84)
				w84n++;                                         /* the last restore row (cfg 0x84): its read-back read fails, or its store is refused, dropped or corrupted */
			else if (k <= n)
				benign++;                                       /* a read of the wait for pending operations (DHD goes on, aiutils.c:1257) */
			else
				zero_in++;
		} else {
			CHECK(x.stopped && k <= n);
			if (kind == 1)
				CHECK(x.stop_code == SWW_E_WR || x.stop_code == SWW_E_HALT);
			if (kind == 2 || kind == 3)
				CHECK(x.stop_code == SWW_E_READBACK || x.stop_code == SWW_E_HALT);
			if (f.run)
				CHECK(x.flags & SWF_W16);
		}
	}
	CHECK(zero_in == 2);                                                    /* the two positions beyond the last operation do not trigger */
	CHECK(benign == (kind == 0 ? 1u : 0u));
	CHECK(w84n == 1u);
}

static void runf1(void)
{
	static struct fake f;
	static struct swl_wx x;
	unsigned i, j, nread, nw7, nrd7, T;
	int rc;
	uint32_t sv, tok;
	char t[400], xs[132];

	tok = ex.tok;
	f1_layout_check();

	/* the happy case: level 8, the shared structure in the tail window, every dump made, the only stores are the window select to the PCIe core and back, then the stop action as before */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x2000u, 0x1235u);
	f1_text(F1_CBUF, 0x2000u, 0x235u - 7u, "BEFORE");                    /* outside the 4096 byte window ending at the write index; the byte right before the window is visible, in the word the window starts in */
	f1_text(F1_CBUF, 0x2000u, 0x235u - 1u, "Q");
	f1_text(F1_CBUF, 0x2000u, 0x235u, "WINSTART\n");
	memset(xs, 'x', 130); xs[130] = 0;
	f1_text(F1_CBUF, 0x2000u, 0x400u, xs);                        /* a line of 130 characters is wrapped at 120 */
	f1_text(F1_CBUF, 0x2000u, 0x500u, "\n");
	f1_text(F1_CBUF, 0x2000u, 0x600u, "mid\r\n");
	f1_text(F1_CBUF, 0x2000u, 0x700u, "a\x01\x7F\xFF" "b\n");
	f1_text(F1_CBUF, 0x2000u, 0x1235u - 10u, "last line\n");
	f1_text(F1_CBUF, 0x2000u, 0x1235u, "AFTER\n");                       /* the oldest bytes of the cyclic buffer, outside the window */
	f.bar2_n = 0;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && x.shared == F1_SH && x.version == 7 && x.polls == 2);
	CHECK(f.nw == 11 && e_stores(&f, 0, wantE, NWANTE));
	CHECK(f.w[9].space == SWW_CFG && f.w[9].off == 0x80 && f.w[9].val == 0x18001000u && f.w[10].space == SWW_CFG && f.w[10].off == 0x80 && f.w[10].val == 0x18120000u);        /* the two window selects come after the re-halt */
	CHECK(f.w[7].space == SWW_BAR0 && f.w[7].win == 0x18120000u && f.w[7].off == 0x800 && f.w[7].val == 1 && f.w[8].space == SWW_CFG && f.w[8].off == 0x84 && f.w[8].val == 0 && f.win0 == 0x18120000u && f.win2 == 0);
	CHECK(strstr(logbuf, "F1 shared structure 0x480000, 35 of 35 words\n") != 0 && strstr(logbuf, "F1 shared +0x000: 70050107 a5000001 a5000002 a5000003\n") != 0);
	CHECK(strstr(logbuf, "F1 shared +0x010: a5000004 00480200 a5000006 a5000007\n") != 0 && strstr(logbuf, "F1 shared +0x080: a5000020 a5000021 a5000022\n") != 0 && count_str("F1 shared +0x") == 9);
	CHECK(strstr(logbuf, "F1 shared +0x020: a5000008 a5000009 00480300 00480304\n") != 0 && strstr(logbuf, "F1 shared +0x030: 00480400 a500000d a500000e a500000f\n") != 0);
	CHECK(strstr(logbuf, "F1 mailbox data: host to dongle [0x480300] = 0x11111111, dongle to host [0x480304] = 0x22222222\n") != 0);
	CHECK(strstr(logbuf, "F1 ring_info 0x480400\n") != 0 && strstr(logbuf, "F1 ring_info +0x000: 00480500 b0000001 b0000002 b0000003\n") != 0 && strstr(logbuf, "F1 ring_info +0x040: b0000010 b0000011 b0000012\n") != 0 && count_str("F1 ring_info +0x") == 5);
	CHECK(strstr(logbuf, "F1 ring_mem 0: idx 0 type 16 max_item 64 len_items 40 base 0000000880000000\n") != 0 && strstr(logbuf, "F1 ring_mem 2: idx 2 type 18 max_item 66 len_items 42 base 0000000a80002000\n") != 0);
	CHECK(strstr(logbuf, "F1 ring_mem 4: idx 4 type 20 max_item 68 len_items 44 base 0000000c80004000\n") != 0 && count_str("F1 ring_mem ") == 5);
	CHECK(strstr(logbuf, "F1 console 0x480200: buffer 0x482000 size 8192 write index 4661\n") != 0);
	CHECK(strstr(logbuf, "F1 con: WINSTART\n") != 0 && strstr(logbuf, "F1 con: mid\n") != 0 && strstr(logbuf, "F1 con: a...b\n") != 0 && strstr(logbuf, "F1 con: last line\n") != 0);
	CHECK(strstr(logbuf, "BEFORE") == 0 && strstr(logbuf, "AFTER") == 0 && strstr(logbuf, "QWIN") == 0);
	CHECK(count_str("F1 con: ") == 6 && strstr(logbuf, "F1 console: 4096 bytes in 6 lines, 0 not printed\n") != 0);
	memset(xs, 'x', 120); xs[120] = 0;
	snprintf(t, sizeof t, "F1 con: %s\n", xs);
	CHECK(strstr(logbuf, t) != 0);
	memset(xs, 'x', 10); xs[10] = 0;
	snprintf(t, sizeof t, "F1 con: %s\n", xs);
	CHECK(strstr(logbuf, t) != 0);
	snprintf(t, sizeof t, "F1 PCIe core funcint 0x11 funcmask 0x22 powerint 0x33 mailbox int 0x5 mask 0 srpwr 0x%x (after the re-halt)\n", f.srpwr);
	CHECK(strstr(logbuf, t) != 0);
	CHECK(strstr(logbuf, "F1 RC irq0 0x900/0 irq1 0x3800a000/0 irq2 0x4/0\n") != 0);
	snprintf(t, sizeof t, "F1 EP command 0x%x\n", f.cmd);
	CHECK(strstr(logbuf, t) != 0);
	CHECK(strstr(logbuf, "F1 flags 0x70050107 before the dumps, 0x70050107 after\n") != 0 && strstr(logbuf, "F1 done mask 0xdf\n") != 0 && strstr(logbuf, "STOP") == 0);
	CHECK(strstr(logbuf, "F1 done mask 0xdf\n") < strstr(logbuf, "restore: CA7 RESET_ST") && strstr(logbuf, "restore: CA7 RESET_ST") < strstr(logbuf, "F1 PCIe core"));      /* review 0134 F1: the PCIe core group after the re-halt */
	T = f.bar2_n;
	nread = f.nrd;
	CHECK(pin_hash(&f) == 0xfbd6b7e4881bf7bdull);                    /* patch 0135 pin: the level 8 log, reads and stores are byte-identical to the ones of patch 0134 (hash measured with the header of 0134) */
	CHECK(T == 5u + 35u + 2u + 19u + 20u + 3u + 1025u + 1u);                 /* W16/W17: vector, token, 2 polls, flags; F1: shared, 2 mailbox words, ring_info, ring_mem, console header, the 1025 words that hold 4096 bytes from byte 1 of a word, the flags again: nothing is read that is not needed */

	/* levels 4..7 do not run F1: the same memory at level 7 makes the 9 stores of run E and no dump; the reads are the ones of run E (counts as measured with the header of patch 0133) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x2000u, 0x1235u);
	x.level = 7;
	rc = f1_go(&f, &x, F1_SH);
	nw7 = f.nw;
	nrd7 = f.nrd;
	CHECK(rc == 0 && x.halted == 1 && x.f1 == 0 && nw7 == 9 && strstr(logbuf, "F1 ") == 0 && f.win0 == 0x18120000u && f.oob == 0);
	CHECK(nrd7 == F1_NRD7 && nread > nrd7 + 1000u);
	CHECK(pin_hash(&f) == 0x699f104af38ef5c3ull);                    /* the same for level 7 */
	for (i = 4; i < 8; i++) {                                        /* swl_f1() itself called below level 8: the read tables refuse its first read (ELBI is run 5), at level 7 the block reader refuses before the first BAR2 read; no word of the shared structure is read */
		f1_new(&f, &x);
		f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x2000u, 0x1235u);
		eseqset(&f, 2, x.tok, F1_SH, 0, 0);
		CHECK(swl_w16_go(&x) == 0);
		x.level = (int)i;
		f.bar2_n = 0;
		rc = swl_f1(&x, F1_SH, SWW_WIN_TAIL, 0x70050107u);
		CHECK(rc == (i < 7 ? -SWW_E_RD : -SWW_E_LEVEL) && x.stopped && f.bar2_n == 0 && strstr(logbuf, "F1 shared") == 0 && f.run == 0 && x.restore == SWW_RS_OK);
	}
	/* the block reader: no more than 1024 words and not none, refused before a read; the window and the span are the caller's */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f.ram = ramarr; f.win2 = SWW_WIN_TAIL; f.win0 = SWW_WIN_PCIE;
	{
		static uint32_t big[2048];

		f.nrd = 0;
		CHECK(swl_f1_block(&x, SWW_WIN_TAIL, F1_SH, 1025, big) == 1 && swl_f1_block(&x, SWW_WIN_TAIL, F1_SH, 0, big) == 1 && f.nrd == 0 && !x.stopped);
		CHECK(swl_f1_block(&x, SWW_WIN_TAIL, F1_SH, 1024, big) == 0 && f.nrd == 1024 && big[0] == 0x70050107u && big[1] == 0xA5000001u);
	}
	/* the block reader refuses below level 8 before it reads (defence in depth: the table's BAR2 rows are run 2) */
	f1_new(&f, &x);
	x.level = 7;
	f.nrd = 0;
	{
		uint32_t w[4];

		CHECK(swl_f1_block(&x, SWW_WIN_TAIL, F1_SH, 4, w) == -SWW_E_LEVEL && x.stop_code == SWW_E_LEVEL && f.nrd == 0);
	}

	/* the span rule, by hand: the window, the RAM, the alignment, the end, no wrap */
	{
		static const struct { uint32_t end, sw, addr, bytes; int ok; } sp[] = {
			{ 0x4A0000u, 0x400000u, 0x400000u, 4u, 1 }, { 0x4A0000u, 0x400000u, 0x49FFFCu, 4u, 1 }, { 0x4A0000u, 0x400000u, 0x4A0000u, 4u, 0 }, { 0x4A0000u, 0x400000u, 0x49FFFCu, 8u, 0 },
			{ 0x4A0000u, 0x400000u, 0x400000u, 0xA0000u, 1 }, { 0x4A0000u, 0x400000u, 0x400000u, 0xA0004u, 0 }, { 0x4A0000u, 0x400000u, 0x3FFFFCu, 4u, 0 }, { 0x4A0000u, 0x400000u, 0x400002u, 4u, 0 },
			{ 0x4A0000u, 0x400000u, 0x400000u, 6u, 0 }, { 0x4A0000u, 0x400000u, 0x400000u, 0u, 0 }, { 0x4A0000u, 0x400000u, 0xFFFFFFFCu, 4u, 0 }, { 0x4A0000u, 0x400000u, 0x49FFFCu, 0xFFFFFFFCu, 0 },
			{ 0x4A0000u, 0, 0x200000u, 4u, 1 }, { 0x4A0000u, 0, 0x3FFFFCu, 4u, 1 }, { 0x4A0000u, 0, 0x400000u, 4u, 0 }, { 0x4A0000u, 0, 0x3FFFFCu, 8u, 0 }, { 0x4A0000u, 0, 0x1FFFFCu, 4u, 0 },
			{ 0x4A0000u, 0, 0x200000u, 0x200000u, 1 }, { 0x4A0000u, 0, 0x200000u, 0x200004u, 0 }, { 0x400000u, 0, 0x3FFFFCu, 4u, 1 }, { 0x300000u, 0, 0x2FFFFCu, 4u, 1 }, { 0x300000u, 0, 0x300000u, 4u, 0 },
			{ 0x600000u, 0x400000u, 0x5FFFFCu, 4u, 1 }, { 0x600000u, 0x400000u, 0x600000u, 4u, 0 }, { 0x600000u, 0x400000u, 0x400000u, 0x200000u, 1 }, { 0x600000u, 0, 0x3FFFFCu, 8u, 0 },
		};

		for (i = 0; i < sizeof sp / sizeof sp[0]; i++)
			CHECK(swl_f1_span_ok(sp[i].end, sp[i].sw, sp[i].addr, sp[i].bytes) == sp[i].ok);
		CHECK(swl_f1_lim(0x4A0000u, 0x400000u) == 0x4A0000u && swl_f1_lim(0x4A0000u, 0) == 0x400000u && swl_f1_lim(0x300000u, 0) == 0x300000u && swl_f1_lim(0x600000u, 0x400000u) == 0x600000u);
	}

	/* the read allowlist: PCIe core 0xC10..0xC18 only at level 8, only in the PCIe window; the mailbox words and ELBI rows (run 5) still at 7 */
	f1_new(&f, &x);
	f.any_pcie_read = 1;
	f.win0 = SWW_WIN_PCIE;
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC10u, &sv) == 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC14u, &sv) == 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC18u, &sv) == 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC30u, &sv) == 0);
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC0Cu, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC1Cu, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC11u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC20u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC38u, &sv) < 0);
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_SYSMEM, 0xC10u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_CA7WRAP, 0xC18u, &sv) < 0 && swl_rdx(&x, SWW_CFG, SWW_WIN_PCIE, 0xC10u, &sv) < 0 && swl_rdx(&x, SWW_ELBI, 0, 0xC10u, &sv) < 0);
	f.ram = ramarr; f.win2 = SWW_WIN_TAIL;
	CHECK(swl_rdx(&x, SWW_BAR2, SWW_WIN_TAIL, 0x80000u, &sv) == 0);
	f.win2 = 0;
	CHECK(swl_rdx(&x, SWW_BAR2, 0, 0x300000u, &sv) == 0);
	x.level = 7;
	CHECK(swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC10u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC14u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC18u, &sv) < 0 && swl_rdx(&x, SWW_BAR0, SWW_WIN_PCIE, 0xC30u, &sv) == 0);

	/* the firmware's own failure flags: a stop before any dump, the CPU in reset again (the flags are read in W17 and again after the dumps) */
	{
		static const uint32_t bad[] = { 0x70050307u, 0x70050507u, 0x70050707u, 0x00000200u | 7u, 0x00000400u | 7u };

		for (i = 0; i < sizeof bad / sizeof bad[0]; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			f1_put(F1_SH, bad[i]);
			rc = f1_go(&f, &x, F1_SH);
			CHECK(f1_stopped(&f, &x, rc, SWW_E_FWFAIL) && x.f1 == 0 && f.nw == 9 && f.bar2_n == 5 && strstr(logbuf, "F1 shared") == 0 && strstr(logbuf, "F1 done") == 0 && strstr(logbuf, "report assert/trap") != 0);
			CHECK(strstr(logbuf, "W17 STOP: firmware assert or trap (code") != 0 || strstr(logbuf, "STOP: firmware assert or trap") != 0);
		}
		/* the same bits in other flag positions are not the failure flags */
		static const uint32_t good[] = { 0x70050107u, 0x00000107u, 0xFFFFF9F7u, 0x00000100u | 6u, 0x00000800u | 7u, 0x00000100u };

		for (i = 0; i < sizeof good / sizeof good[0]; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			f1_put(F1_SH, good[i]);
			rc = f1_go(&f, &x, F1_SH);
			CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL);
		}
	}
	/* a flag that turns up during the dumps (the firmware traps while the host reads): found by the read after them, wherever in the dumps it appears */
	for (j = 0; j < 4; j++) {
		static const uint32_t pv[] = { 0x200u, 0x400u, 0x600u, 0xFFFFFFFFu };
		unsigned pos[6];

		pos[0] = 6; pos[1] = 7; pos[2] = T / 2u; pos[3] = T - 1u; pos[4] = T; pos[5] = T + 1u;
		for (i = 0; i < 6; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x2000u, 0x1235u);
			f.poke_at = pos[i]; f.poke_bp = F1_SH; f.poke_val = pv[j];
			rc = f1_go(&f, &x, F1_SH);
			if (i == 5) {
				CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL);                  /* the poke comes after the last read: nothing to see */
			} else {
				CHECK(f1_stopped(&f, &x, rc, j == 3 ? SWW_E_DEAD : SWW_E_FWFAIL) && x.f1 == F1_NOPCI && strstr(logbuf, "F1 done") == 0 && strstr(logbuf, "F1 PCIe core") == 0);
				CHECK(j == 3 || strstr(logbuf, "after\n") != 0);
			}
		}
	}

	/* the interrupt gates, again after the release: any RC enable bit, any mailbox mask bit (set while the CPU runs) */
	{
		static const uint32_t bits[] = { 1u, 0x4000u, 0x00154000u, 0x400u, 0x01030000u, 0x80000000u, 0xFFFFFFFFu };

		for (j = 0; j < 3; j++)
			for (i = 0; i < sizeof bits / sizeof bits[0]; i++) {
				f1_new(&f, &x);
				f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
				f.run_en[j] = bits[i];
				rc = f1_go(&f, &x, F1_SH);
				CHECK(f1_stopped(&f, &x, rc, SWW_E_IRQ) && x.f1 == 0 && strstr(logbuf, "F1 shared") == 0 && strstr(logbuf, "F1 RC irq") != 0);
			}
		for (i = 0; i < sizeof bits / sizeof bits[0]; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			f.run_mbmask = bits[i];
			rc = f1_go(&f, &x, F1_SH);
			CHECK(f1_stopped(&f, &x, rc, bits[i] == 0xFFFFFFFFu ? SWW_E_DEAD : SWW_E_IRQ) && x.f1 == F1_NOPCI && strstr(logbuf, "F1 done mask 0xdf\n") != 0 && strstr(logbuf, "(after the re-halt)") != 0);
			CHECK(f.win0 == 0x18120000u);                                   /* the restore moved the window to the CA7 wrapper and halted it */
		}
	}
	/* status bits that are not enables do not stop (they are printed) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f.elbi_irq[0] = 0xFFFFFFFFu; f.elbi_irq[1] = 0xFFFFFFFFu; f.elbi_irq[2] = 0xFFFFFFFFu; f.fc10 = 0xFFFFFFF0u; f.mbint = 0x00FF0000u;
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL);

	/* a dead link in the PCIe core registers (each alone, only once the CPU runs: W16 reads three of them before the release) */
	{
		static const uint32_t offs[] = { 0xC10u, 0xC14u, 0xC18u, 0xC30u, 0xC34u, 0x1E8u };

		for (i = 0; i < sizeof offs / sizeof offs[0]; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			f.ones_on = 1; f.ones_run = 1; f.ones_space = SWW_BAR0; f.ones_win = SWW_WIN_PCIE; f.ones_off = offs[i];
			rc = f1_go(&f, &x, F1_SH);
			CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.f1 == F1_NOPCI && strstr(logbuf, "F1 done mask 0xdf\n") != 0 && f.win0 == 0x18120000u);
		}
	}
	/* a precondition that changes (the firmware turns on bus mastering): right after the poll (before the first dump), in the middle of the dumps (found by the window select of the PCIe core group), at the first PCIe core read (found by the window select back) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f.cmd_at_poll = 2;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_PRE) && x.f1 == (SWW_F1_ELBI | SWW_F1_CFG) && strstr(logbuf, "F1 shared") == 0 && strstr(logbuf, "re-halting the CA7 anyway") != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f.poke_at = 8; f.poke_cmd = 4u;                                 /* the third BAR2 read of the dumps */
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_PRE) && x.f1 == (F1_NOPCI & ~SWW_F1_CON) && strstr(logbuf, "F1 done") == 0 && strstr(logbuf, "F1 console") == 0 && strstr(logbuf, "re-halting the CA7 anyway") != 0);        /* review 0134 F4: found before the console */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f.cmd_at_c10 = 4u;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_PRE) && x.f1 == F1_NOPCI && strstr(logbuf, "F1 done mask 0xdf\n") != 0 && strstr(logbuf, "re-halting") == 0);    /* found by the select back, after the re-halt: nothing armed, the restore result is kept */
	/* review 0134 F4: bus mastering turned on during the console dump: found before the last flags read, the CA7 is re-halted anyway */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x2000u, 0x1235u);
	f.poke_at = 100; f.poke_cmd = 4u;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_PRE) && x.f1 == F1_NOPCI && strstr(logbuf, "F1 flags") == 0 && strstr(logbuf, "F1 done") == 0 && strstr(logbuf, "F1 console: ") != 0 && strstr(logbuf, "re-halting the CA7 anyway") != 0 && f.bar2_n == T - 1u);

	/* the pointers the firmware hands over: each refused alone, nothing outside the window is read (oob), no stop, the rest of the dumps are made; a pointer to the last bytes that fit is accepted */
	{
		static const struct f1_ptr fld[] = {
			{ F1_SH + 0x14, 20, SWW_F1_CON }, { F1_SH + 0x28, 4, SWW_F1_MB }, { F1_SH + 0x2C, 4, SWW_F1_MB }, { F1_SH + 0x30, 76, SWW_F1_RI | SWW_F1_RM }, { F1_RI, 80, SWW_F1_RM }, { F1_CONS + 8, 0x100, SWW_F1_CON },
		};
		static const uint32_t common[] = { 0u, 1u, 2u, 3u, 0x1FFFFCu, 0x200001u, 0x3F0000u, 0x3FFFFCu, E_END, E_END + 4u, 0x7FFFFCu, 0x800000u, 0xFFFFFFFCu, 0xFFFFFFFFu, 0x80000000u };
		const unsigned nc = sizeof common / sizeof common[0];

		for (j = 0; j < sizeof fld / sizeof fld[0]; j++) {
			for (i = 0; i < nc + 2u; i++) {
				uint32_t v = i < nc ? common[i] : i == nc ? E_END - fld[j].bytes + 4u : E_END - fld[j].bytes;
				int refused = i <= nc;                           /* the last two: one word too far, the last bytes that fit */

				f1_new(&f, &x);
				f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
				f1_put(fld[j].slot, v);
				if (!refused && j == 0) {                       /* the console structure at the very end: buffer and size words, the index is the last RAM word */
					f1_put(v + 8u, F1_CBUF);
					f1_put(v + 12u, 0x100u);
				}
				if (!refused && j == 3)
					f1_put(v, F1_RM);
				rc = f1_go(&f, &x, F1_SH);
				CHECK(rc == 0 && f1_clean(&f, &x));
				if (!refused && j == 0) {                       /* the structure's index word is the last RAM word = the shared pointer (> size 0x100): refused as DHD does (review 0134 F5), after the span rule accepted the structure */
					CHECK(x.f1 == (F1_ALL & ~SWW_F1_CON) && strstr(logbuf, "F1 console write index 4718592 beyond size 256 (DHD refuses it), not read\n") != 0);
					continue;
				}
				CHECK(x.f1 == (refused ? (F1_ALL & ~fld[j].miss) : F1_ALL));
				CHECK(refused ? strstr(logbuf, "not read") != 0 : strstr(logbuf, "not read") == 0);
			}
		}
	}

	/* a refused host-to-dongle mailbox pointer: the dongle-to-host word is not read either (both or neither are printed); a refused dongle-to-host pointer alone costs no read */
	{
		unsigned n_both_bad = 0, n_h2d_bad = 0, n_d2h_bad = 0, n_ok = 0;

		for (i = 0; i < 4; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			if (i & 1) f1_put(F1_SH + 0x28, 0);
			if (i & 2) f1_put(F1_SH + 0x2C, 0);
			CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == (i ? F1_ALL & ~SWW_F1_MB : F1_ALL));
			if (i == 0) n_ok = f.bar2_n;
			if (i == 1) n_h2d_bad = f.bar2_n;
			if (i == 2) n_d2h_bad = f.bar2_n;
			if (i == 3) n_both_bad = f.bar2_n;
		}
		CHECK(n_h2d_bad + 2u == n_ok && n_d2h_bad + 1u == n_ok && n_both_bad + 2u == n_ok);
	}
	/* the row printer prints 4 words at most */
	{
		static const uint32_t w6[6] = { 1, 2, 3, 4, 5, 6 };

		loglen = 0; logbuf[0] = 0;
		swl_f1_row("t", 0x10u, w6, 6);
		CHECK(strcmp(logbuf, "SWEET WLAN: F1 t +0x010: 00000001 00000002 00000003 00000004\n") == 0);
	}
	/* the console buffer: its size, unusable values refused, a write index beyond the size is refused (review 0134 F5, DHD dhd_pcie.c:4732-4734), the index == size wraps to 0 */
	{
		static const struct { uint32_t size, idx; int ok; } cs[] = {
			{ 0u, 0u, 0 }, { 1u, 0u, 0 }, { 2u, 0u, 0 }, { 3u, 0u, 0 }, { 6u, 0u, 0 }, { 4u, 0u, 1 }, { 4u, 7u, 0 }, { 4u, 4u, 1 }, { 0x100u, 0xFFFFFFFFu, 0 }, { 0x100u, 0x104u, 0 }, { 0x100u, 0x100u, 1 }, { 0x100u, 0xFFu, 1 },
			{ 0x400000u, 0u, 0 }, { 0xFFFFFFFCu, 0u, 0 }, { 0xFFFFFFFFu, 0u, 0 }, { 0x1E000u, 0u, 1 }, { 0x1E004u, 0u, 0 }, { 0x1DFFCu, 0x100u, 1 }, { 0x1E000u, 0xFFFFu, 1 },
		};

		for (i = 0; i < sizeof cs / sizeof cs[0]; i++) {
			f1_new(&f, &x);
			f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
			f1_put(F1_CONS + 12u, cs[i].size);
			f1_put(F1_CONS + 16u, cs[i].idx);
			rc = f1_go(&f, &x, F1_SH);
			CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == (cs[i].ok ? F1_ALL : (F1_ALL & ~SWW_F1_CON)));
		}
	}
	/* the console text in order, around the end of the buffer (and a window that is the whole buffer starts at the write index) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f1_text(F1_CBUF, 0x100u, 8u, "oldest\n");
	f1_text(F1_CBUF, 0x100u, 0xFBu, "wrap-");
	f1_text(F1_CBUF, 0x100u, 0u, "around\nx");                            /* the last byte before the write index ends no line: it is printed at the end */
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL);
	CHECK(strstr(logbuf, "F1 con: oldest\n") != 0 && strstr(logbuf, "F1 con: wrap-around\n") != 0 && strstr(logbuf, "F1 con: x\n") != 0 && count_str("F1 con: ") == 3 && strstr(logbuf, "F1 console: 256 bytes in 3 lines, 0 not printed\n") != 0);
	CHECK(strstr(logbuf, "F1 con: oldest\n") < strstr(logbuf, "F1 con: wrap-around\n"));
	/* a size that is not a power of two and a write index == size (wraps to 0: the window is the last 4096 bytes of the buffer) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x200Cu, 0x200Cu);
	{
		uint32_t sz = 0x200Cu, p0 = (sz - 4096u) % sz, pe = 0;

		f1_text(F1_CBUF, sz, p0, "WIN\n");
		f1_text(F1_CBUF, sz, (pe + sz - 4u) % sz, "END\n");
		CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && strstr(logbuf, "F1 con: WIN\n") != 0 && strstr(logbuf, "F1 con: END\n") != 0 && count_str("F1 con: ") == 2);
		CHECK(strstr(logbuf, "F1 console: 4096 bytes in 2 lines, 0 not printed\n") != 0);
	}
	/* many short lines: at most 128 are printed (one printf each while the CPU runs), the rest is counted */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x400u, 0u);
	for (i = 0; i < 200u; i++) {
		f1_byte(F1_CBUF + 2u * i, (uint8_t)('A' + i % 26u));
		f1_byte(F1_CBUF + 2u * i + 1u, '\n');
	}
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && count_str("F1 con: ") == 128 && strstr(logbuf, "F1 console: 1024 bytes in 200 lines, 72 not printed\n") != 0);
	CHECK(strstr(logbuf, "F1 con: A\n") != 0 && strstr(logbuf, "F1 con: V\n") != 0);
	snprintf(t, sizeof t, "F1 con: %c\n", 'A' + 127 % 26);
	CHECK(strstr(logbuf, t) != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x400u, 0u);
	for (i = 0; i < 128u; i++) {
		f1_byte(F1_CBUF + 2u * i, (uint8_t)('A' + i % 26u));
		f1_byte(F1_CBUF + 2u * i + 1u, '\n');
	}
	CHECK(f1_go(&f, &x, F1_SH) == 0 && count_str("F1 con: ") == 128 && strstr(logbuf, "F1 console: 1024 bytes in 128 lines, 0 not printed\n") != 0);
	/* a size just over the 4096 bytes: only the last 4096 are read (the guard window is the whole RAM here, the count of reads is the check) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x1000u, 0u);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && strstr(logbuf, "F1 console: 4096 bytes in 0 lines, 0 not printed\n") != 0);
	nread = f.bar2_n;
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x1004u, 0u);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && strstr(logbuf, "F1 console: 4096 bytes in 0 lines, 0 not printed\n") != 0 && f.bar2_n == nread);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0xFFCu, 0u);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && strstr(logbuf, "F1 console: 4092 bytes in 0 lines, 0 not printed\n") != 0 && f.bar2_n + 1u == nread);

	/* blocks that are all ones are a dead link (2 words or more); a single all-ones word, or all-ones words inside a block, are data */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	for (i = 0; i < SWW_RI_WORDS; i++)
		f1_put(F1_RI + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.f1 == (SWW_F1_SH | SWW_F1_MB | SWW_F1_ELBI | SWW_F1_CFG) && strstr(logbuf, "F1 19 words at 0x480400 are all ones\n") != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	for (i = 0; i < SWW_RM_WORDS; i++)
		f1_put(F1_RM + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.f1 == (SWW_F1_SH | SWW_F1_MB | SWW_F1_RI | SWW_F1_ELBI | SWW_F1_CFG) && strstr(logbuf, "F1 20 words at 0x480500 are all ones\n") != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	for (i = 0; i < 64; i++)
		f1_put(F1_CBUF + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.f1 == (F1_ALL & ~SWW_F1_CON & ~SWW_F1_PCI) && strstr(logbuf, "F1 16 words at 0x482008 are all ones\n") != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	for (i = 1; i < SWW_SH_WORDS; i++)
		f1_put(F1_SH + 4u * i, 0xFFFFFFFFu);                    /* the flags word (the poll's) is fine, the rest of the structure is all ones: 34 words in a block of 35 */
	rc = f1_go(&f, &x, F1_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == (SWW_F1_SH | SWW_F1_PCI | SWW_F1_ELBI | SWW_F1_CFG) && strstr(logbuf, "F1 shared +0x010: ffffffff ffffffff ffffffff ffffffff\n") != 0);
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	f1_put(F1_MB, 0xFFFFFFFFu);
	f1_put(F1_MB + 4u, 0xFFFFFFFFu);
	f1_put(F1_RI + 4u, 0xFFFFFFFFu);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && strstr(logbuf, "dongle to host [0x480304] = 0xffffffff\n") != 0);

	/* the shared structure near the end of the RAM or of the window: fewer words, never past it, the pointers of a short structure are 0 and refused */
	{
		static const struct { uint32_t sh, ram_bytes; uint32_t n; } nr[] = {
			{ E_END - 8u, 0x2A0000u, 2 }, { E_END - 12u, 0x2A0000u, 3 }, { E_END - 140u, 0x2A0000u, 35 }, { E_END - 136u, 0x2A0000u, 34 }, { 0x3FFFF0u, 0x2A0000u, 4 }, { 0x3FFFFCu, 0x2A0000u, 1 },
		};

		for (i = 0; i < sizeof nr / sizeof nr[0]; i++) {
			f1_new(&f, &x);
			f1_put(nr[i].sh, 0x00000007u);
			if (nr[i].sh < 0x400000u) {
				f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
			}
			rc = f1_go(&f, &x, nr[i].sh);
			snprintf(t, sizeof t, "F1 shared structure 0x%x, %u of 35 words\n", nr[i].sh, nr[i].n);
			CHECK(rc == 0 && f1_clean(&f, &x) && strstr(logbuf, t) != 0 && (x.f1 & SWW_F1_SH) && x.f1 == (nr[i].n > 12u ? x.f1 : (SWW_F1_SH | SWW_F1_PCI | SWW_F1_ELBI | SWW_F1_CFG)));
		}
	}
	/* the shared structure below the tail window (window 0): pointers into the tail window are not reachable and are refused, pointers in window 0 are read */
	f1_new(&f, &x);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f1_put(0x3AF000u + 0x30, 0x400400u);                            /* ring_info in the other window */
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	rc = f1_go(&f, &x, 0x3AF000u);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == (F1_ALL & ~(SWW_F1_RI | SWW_F1_RM)) && f.win2 == 0 && strstr(logbuf, "F1 rings_info_ptr 0x400400 is not a readable range, not read\n") != 0 && strstr(logbuf, "F1 shared structure 0x3af000, 35 of 35 words\n") != 0);
	f1_new(&f, &x);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	rc = f1_go(&f, &x, 0x3AF000u);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && f.w[7].off == 0x84 && f.w[7].val == 0 && f.w[8].off == 0x800 && f.w[8].val == 1 && f.w[9].off == 0x84 && f.w[9].val == 0 && f.w[10].off == 0x80 && f.w[10].val == 0x18001000u && f.w[11].off == 0x80 && f.w[11].val == 0x18120000u && f.nw == 12);
	/* the shared structure in window 0 with a pointer back from the tail window's start: window 0 pointers below the RAM base */
	f1_new(&f, &x);
	f1_layout(0x3AF000u, 0x1FFFF0u, 0x1FFFFCu, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	rc = f1_go(&f, &x, 0x3AF000u);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == (F1_ALL & ~(SWW_F1_CON | SWW_F1_MB)));

	/* RAM that ends at 4 MiB: no tail window, every pointer must end at or below it; ring_info that ends exactly at the end is read, one word further is refused */
	for (i = 0; i < 2; i++) {
		f1_new(&f, &x);
		x.ram_bytes = 0x200000u;
		f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
		f1_put(0x3AF000u + 0x30, i == 0 ? 0x3FFFB4u : 0x3FFFB8u);
		ramarr[0x3FFFFCu / 4] = tok;
		f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
		eseqset(&f, 2, tok, 0x3AF000u, 0, 0);
		f.boot_bp = 0x3FFFFCu;
		rc = e_fast(&x);
		CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == (i == 0 ? (F1_ALL & ~SWW_F1_RM) : (F1_ALL & ~(SWW_F1_RI | SWW_F1_RM))) && f.nw == 9);
	}

	/* review 0134 F9 (M1): a console header (3 words at the buffer fields) that is all ones is a dead link: the stop of the block reader is returned, not swallowed (rc -SWW_E_DEAD, not -SWW_E_STOPPED), no console summary */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 8u);
	for (i = 0; i < 3; i++)
		f1_put(F1_CONS + 8u + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.stop_code == SWW_E_DEAD && x.f1 == (F1_NOPCI & ~SWW_F1_CON) && strstr(logbuf, "F1 3 words at 0x480208 are all ones\n") != 0 && strstr(logbuf, "F1 console") == 0 && strstr(logbuf, "F1 done") == 0);
	/* review 0134 F9 (M19): a console chunk of 2 words at the wrap (the last two words of the buffer, the write index 8 bytes before the end) that is all ones is a dead link too (the rule is 2 or more words) */
	f1_new(&f, &x);
	f1_layout(F1_SH, F1_CONS, F1_MB, F1_RI, F1_RM, F1_CBUF, 0x100u, 0xF8u);
	for (i = 0; i < 0x100u / 4u; i++)
		f1_put(F1_CBUF + 4u * i, 0x64636261u + i);                      /* text words */
	f1_put(F1_CBUF + 0xF8u, 0xFFFFFFFFu);
	f1_put(F1_CBUF + 0xFCu, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_DEAD) && x.f1 == (F1_NOPCI & ~SWW_F1_CON) && strstr(logbuf, "F1 2 words at 0x4820f8 are all ones\n") != 0 && strstr(logbuf, "F1 console:") == 0 && strstr(logbuf, "F1 done") == 0);

	/* one fault at every read and store position of W16 + W17 + F1, and two beyond: the CPU is never lost */
	for (i = 0; i < 4; i++)
		f1_sweep((int)i);
}

/* ---------------------------------------------------------------- run F2 (patch 0135): the dumps F1 had to refuse, through BAR2 window 0 after the re-halt ---------------------------------------------------------------- */

#define F2_CONS 0x3B19D8u                               /* the pointers observed on hardware in run F1 (wlan-runF1-0134, HANDOFF "RUN F1 DONE"): all below 0x400000 */
#define F2_MB   0x3ED8B4u                               /* h2d; the d2h word is at 0x3ED8B8 */
#define F2_RI   0x3ED578u
#define F2_RM   0x3ED600u                               /* not observed: chosen by the test */
#define F2_CBUF 0x3B2000u                               /* not observed: chosen by the test */
#define F2_ALL  (SWW_F2_LOW | SWW_F2_MB | SWW_F2_RI | SWW_F2_RM | SWW_F2_CON)
#define F2_F1   (SWW_F1_SH | SWW_F1_ELBI | SWW_F1_CFG | SWW_F1_PCI)      /* what F1 dumps when every pointer is below 0x400000: the shared structure (tail window) and the host side, nothing else */

static char l8[1 << 16];

static void f2_new(struct fake *f, struct swl_wx *x, int level)       /* a fresh run of `level` from the state after run D, shared structure in the tail window, pointers below it */
{
	f1_new(f, x);
	x->level = level;
	f->lo_lo = SWW_RAM_BASE; f->lo_hi = SWW_TCM_BYTES;
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x2000u, 0x1235u);
}

/* the log without the F2 block ("F2 low window" up to the end of the "F2 done mask" line) copied to out; 0 when the block is not there */
static int log_wo_f2(char *out, size_t n)
{
	const char *a = strstr(logbuf, "SWEET WLAN: F2 low window"), *b = a ? strstr(a, "SWEET WLAN: F2 done mask") : 0;

	if (!a || !b || !strchr(b, '\n') || strlen(logbuf) >= n)
		return 0;
	memcpy(out, logbuf, (size_t)(a - logbuf));
	strcpy(out + (a - logbuf), strchr(b, '\n') + 1);
	gap_strip(out);
	return 1;
}

/* one stop of F2: the CPU stays in reset, the restore result is kept, nothing was stored after the restore (9 stores: run E's 7, the re-halt, cfg 0x84), the PCIe core group did not run */
static int f2_stopped(const struct fake *f, const struct swl_wx *x, int rc, int code)
{
	return f1_stopped(f, x, rc, code) && f->nw == 9 && !(x->f1 & SWW_F1_PCI) && strstr(logbuf, "W17 STOP: ") != 0 && strstr(logbuf, "F2 done") == 0 && strstr(logbuf, "F1 PCIe core") == 0;
}

static void f2_sweep(int kind)
{
	static struct fake f, h;
	static struct swl_wx x;
	unsigned k, n, nclean = 0;
	int rc;
	const char *st;

	f2_new(&h, &x, SWW_F2_LEVEL);
	CHECK(f1_go(&h, &x, F1_SH) == 0 && x.f2 == F2_ALL && h.run == 0);
	n = kind == 0 ? h.nrd : h.nw;
	CHECK(kind == 0 ? n > 1500 : n == 11u);
	for (k = 1; k <= n + 2; k++) {
		f2_new(&f, &x, SWW_F2_LEVEL);
		if (kind == 0) f.fail_rd_at = k;
		if (kind == 1) f.fail_wr_at = k;
		if (kind == 2) f.drop_at = k;
		if (kind == 3) f.corrupt_at = k;
		rc = f1_go(&f, &x, F1_SH);
		CHECK(all_allowed(&f) && f.oob == 0 && f.nw <= 11u);
		CHECK(f.started <= 1 || (kind == 3 && x.restore == SWW_RS_FAILED));
		CHECK(!f.run || x.restore != SWW_RS_OK);                        /* a CPU that still runs is never reported as reset again */
		CHECK(rc != 0 || x.halted);
		CHECK(!x.halted || (!f.run && x.ca_ok));
		CHECK(rc != 0 || x.restore == SWW_RS_OK || x.f2 == 0);          /* F2 only after a restore that ended ok */
		st = strstr(logbuf, "W17 STOP:");
		if (st != 0)
			CHECK(strstr(st, "F2 ") == 0);                          /* nothing of F2 is printed after the stop line */
		if (rc == 0 && x.restore == SWW_RS_OK) {
			CHECK(x.f2 == F2_ALL);
			nclean++;
		}
	}
	CHECK(nclean >= 1u);
}

static void runf2(void)
{
	static struct fake f, g;
	static struct swl_wx x;
	uint32_t i, j, t8, w0, sv;
	int rc;
	char t[400];

	/* the constants this patch adds, restated */
	CHECK(SWW_F2_LEVEL == 9 && SWW_F2_LOW == 1u && SWW_F2_MB == 2u && SWW_F2_RI == 4u && SWW_F2_RM == 8u && SWW_F2_CON == 0x10u);

	/* the same memory at level 8 (F1 refuses the four low pointers) and at level 9: the log of level 9 is the one of level 8 plus the F2 block, the stores are the same, the BAR2 reads add the F2 ones */
	f2_new(&f, &x, SWW_F1_LEVEL);
	f1_text(F2_CBUF, 0x2000u, 0x235u - 7u, "BEFORE");
	f1_text(F2_CBUF, 0x2000u, 0x235u - 1u, "Q");
	f1_text(F2_CBUF, 0x2000u, 0x235u, "WINSTART\n");
	f1_text(F2_CBUF, 0x2000u, 0x600u, "mid\r\n");
	f1_text(F2_CBUF, 0x2000u, 0x1235u - 10u, "last line\n");
	f1_text(F2_CBUF, 0x2000u, 0x1235u, "AFTER\n");
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f1 == F2_F1 && x.f2 == 0);
	CHECK(strstr(logbuf, "F2 ") == 0 && strstr(logbuf, "F1 mailbox data pointers 0x3ed8b4 0x3ed8b8 are not readable words, not read\n") != 0 && strstr(logbuf, "F1 rings_info_ptr 0x3ed578 is not a readable range, not read\n") != 0);
	CHECK(strstr(logbuf, "F1 console pointer 0x3b19d8 is not a hnd_cons_t in the RAM window, not read\n") != 0);                /* review 0134 F3, as observed */
	t8 = f.bar2_n;
	strcpy(l8, logbuf);
	gap_strip(l8);
	CHECK(f.nw == 11 && e_stores(&f, 0, wantE, NWANTE));
	memcpy(&g, &f, sizeof g);
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_text(F2_CBUF, 0x2000u, 0x235u - 7u, "BEFORE");
	f1_text(F2_CBUF, 0x2000u, 0x235u - 1u, "Q");
	f1_text(F2_CBUF, 0x2000u, 0x235u, "WINSTART\n");
	f1_text(F2_CBUF, 0x2000u, 0x600u, "mid\r\n");
	f1_text(F2_CBUF, 0x2000u, 0x1235u - 10u, "last line\n");
	f1_text(F2_CBUF, 0x2000u, 0x1235u, "AFTER\n");
	w0 = ramarr[SWW_RAM_BASE / 4u];
	CHECK(w0 == swl_img_word(&x, 0) && w0 != 0 && w0 != 0xFFFFFFFFu);                                            /* W10 copied the image there: the guard compares with it */
	rc = f1_go(&f, &x, F1_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F2_F1 && x.f2 == F2_ALL && x.halted == 1);
	CHECK(f.nw == g.nw && memcmp(f.w, g.w, 11u * sizeof f.w[0]) == 0 && f.win0 == 0x18120000u && f.win2 == 0);       /* no store more than level 8 makes */
	CHECK(pin_hash(&f) == 0x7d900c785ac8071full);                    /* patch 0136 pin: the level 9 log, reads and stores are byte-identical to the ones of patch 0135 (hash measured with the header of patch 0135, same memory as above) */
	CHECK(f.bar2_n == t8 + 1u + 2u + 19u + 20u + 3u + 1025u);                                                      /* the guard word, mailbox, ring_info, ring_mem, console header, the 1025 words of 4096 bytes from byte 1 of a word */
	{
		static char l9[1 << 16];

		CHECK(log_wo_f2(l9, sizeof l9) == 1 && strcmp(l9, l8) == 0);
	}
	snprintf(t, sizeof t, "SWEET WLAN: F2 low window: cfg 0x84 0, [0x200000] 0x%08x, image word 0 0x%08x\n", w0, w0);
	CHECK(strstr(logbuf, t) != 0);
	CHECK(strstr(logbuf, "F2 mailbox data: host to dongle [0x3ed8b4] = 0x11111111, dongle to host [0x3ed8b8] = 0x22222222\n") != 0);
	CHECK(strstr(logbuf, "F2 ring_info 0x3ed578\n") != 0 && strstr(logbuf, "F1 lo ring_info +0x000: 003ed600 b0000001 b0000002 b0000003\n") != 0 && strstr(logbuf, "F1 lo ring_info +0x040: b0000010 b0000011 b0000012\n") != 0 && count_str("F1 lo ring_info +0x") == 5);
	CHECK(strstr(logbuf, "F2 ring_mem 0: idx 0 type 16 max_item 64 len_items 40 base 0000000880000000\n") != 0 && strstr(logbuf, "F2 ring_mem 4: idx 4 type 20 max_item 68 len_items 44 base 0000000c80004000\n") != 0 && count_str("F2 ring_mem ") == 5);
	CHECK(strstr(logbuf, "F1 console 0x3b19d8: buffer 0x3b2000 size 8192 write index 4661\n") != 0 && strstr(logbuf, "F1 con: WINSTART\n") != 0 && strstr(logbuf, "F1 con: mid\n") != 0 && strstr(logbuf, "F1 con: last line\n") != 0);
	CHECK(strstr(logbuf, "BEFORE") == 0 && strstr(logbuf, "AFTER") == 0 && strstr(logbuf, "QWIN") == 0 && count_str("F1 con: ") == 3 && strstr(logbuf, "F1 console: 4096 bytes in 3 lines, 0 not printed\n") != 0);
	CHECK(strstr(logbuf, "F2 done mask 0x1f\n") != 0);
	CHECK(strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 ok\n") < strstr(logbuf, "F2 low window") && strstr(logbuf, "F2 done mask") < strstr(logbuf, "F1 PCIe core"));        /* after the cfg 0x84 restore row, before the PCIe core group */
	CHECK(strstr(logbuf, "F1 done mask 0xc1\n") < strstr(logbuf, "restore: CA7 RESET_ST") && strstr(logbuf, "restore: CA7 RESET_ST") < strstr(logbuf, "F2 low window"));
	CHECK(x.f2p[0] == F2_MB && x.f2p[1] == F2_MB + 4u && x.f2p[2] == F2_RI && x.f2p[3] == F2_CONS);

	/* the same memory at level 7 and 8 again: byte-identical to the pins (the F2 code is not reached below level 9) */
	/* each pointer alone: not in window 0, unaligned, wrapping, the last bytes that fit; nothing outside [0x200000, 0x400000) is read (oob), no stop, the rest is dumped */
	{
		static const struct f1_ptr fld[] = {
			{ F1_SH + 0x14, 20, SWW_F2_CON }, { F1_SH + 0x28, 4, SWW_F2_MB }, { F1_SH + 0x2C, 4, SWW_F2_MB }, { F1_SH + 0x30, 76, SWW_F2_RI | SWW_F2_RM }, { F2_RI, 80, SWW_F2_RM }, { F2_CONS + 8, 0x100, SWW_F2_CON },
		};
		static const uint32_t common[] = { 0u, 1u, 2u, 3u, 0x1FFFFCu, 0x200001u, 0x3FFFFDu, 0x400000u, 0x400004u, 0x4A0000u, 0x7FFFFCu, 0x800000u, 0xFFFFFFFCu, 0xFFFFFFFFu, 0x80000000u, 0x3FFFFEu };
		const unsigned nc = sizeof common / sizeof common[0];

		for (j = 0; j < sizeof fld / sizeof fld[0]; j++) {
			for (i = 0; i < nc + 2u; i++) {
				uint32_t v = i < nc ? common[i] : i == nc ? SWW_TCM_BYTES - fld[j].bytes + 4u : SWW_TCM_BYTES - fld[j].bytes;
				int refused = i <= nc;                           /* the last two: one word too far, the last bytes that fit */
				unsigned m;

				f2_new(&f, &x, SWW_F2_LEVEL);
				f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
				for (m = 0; m < 0x80u; m++)
					f1_put(SWW_WIN_TAIL + 4u * m, 0);               /* what F1 reads at a pointer into the start of the tail window: zeros, no stop */
				f1_put(fld[j].slot, v);
				if (!refused) {                                  /* a structure that ends exactly at 4 MiB: its words exist and are not all ones */
					for (m = 0; m < fld[j].bytes / 4u; m++)
						f1_put(v + 4u * m, 0x1000u + m);
					if (j == 0) {
						f1_put(v + 8u, F2_CBUF);
						f1_put(v + 12u, 0x100u);
						f1_put(v + 16u, 8u);
					}
					if (j == 3)
						f1_put(v, F2_RM);
				}
				rc = f1_go(&f, &x, F1_SH);
				CHECK(rc == 0 && f1_clean(&f, &x) && (x.f1 & F2_F1) == F2_F1);       /* F1 may dump what lies in the tail window */
				CHECK(x.f2 == (refused ? (F2_ALL & ~fld[j].miss) : F2_ALL));
			}
		}
	}
	/* the console buffer: sizes and write indexes, in window 0 */
	{
		static const struct { uint32_t size, idx; int ok; } cs[] = {
			{ 0u, 0u, 0 }, { 1u, 0u, 0 }, { 2u, 0u, 0 }, { 3u, 0u, 0 }, { 6u, 0u, 0 }, { 4u, 0u, 1 }, { 4u, 7u, 0 }, { 4u, 4u, 1 }, { 0x100u, 0xFFFFFFFFu, 0 }, { 0x100u, 0x104u, 0 }, { 0x100u, 0x100u, 1 }, { 0x100u, 0xFFu, 1 },
			{ 0x400000u, 0u, 0 }, { 0xFFFFFFFCu, 0u, 0 }, { 0xFFFFFFFFu, 0u, 0 }, { 0x4A0000u, 0u, 0 }, { 0x1FFFFCu, 0u, 0 }, { 0x1000u, 0xFFFFu, 0 }, { 0x1000u, 0x1000u, 1 },
		};

		for (i = 0; i < sizeof cs / sizeof cs[0]; i++) {
			f2_new(&f, &x, SWW_F2_LEVEL);
			f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
			f1_put(F2_CONS + 12u, cs[i].size);
			f1_put(F2_CONS + 16u, cs[i].idx);
			rc = f1_go(&f, &x, F1_SH);
			CHECK(rc == 0 && f1_clean(&f, &x) && x.f2 == (cs[i].ok ? F2_ALL : (F2_ALL & ~SWW_F2_CON)));
		}
	}
	/* the console text around the end of the buffer, and more than 128 lines: the caps of F1 hold */
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
	f1_text(F2_CBUF, 0x100u, 8u, "oldest\n");
	f1_text(F2_CBUF, 0x100u, 0xFBu, "wrap-");
	f1_text(F2_CBUF, 0x100u, 0u, "around\nx");
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && count_str("F1 con: ") == 3 && strstr(logbuf, "F1 con: wrap-around\n") != 0 && strstr(logbuf, "F1 console: 256 bytes in 3 lines, 0 not printed\n") != 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x400u, 0u);
	for (i = 0; i < 200u; i++) {
		f1_byte(F2_CBUF + 2u * i, (uint8_t)('A' + i % 26u));
		f1_byte(F2_CBUF + 2u * i + 1u, '\n');
	}
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && count_str("F1 con: ") == 128 && strstr(logbuf, "F1 console: 1024 bytes in 200 lines, 72 not printed\n") != 0);
	/* console text is data: a conversion specification in it is printed as it is */
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 0x40u);
	f1_text(F2_CBUF, 0x100u, 0u, "%s%n%x\n");
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && strstr(logbuf, "F1 con: %s%n%x\n") != 0);

	/* blocks of 2 or more words that are all ones are a dead link: a stop after the restore, nothing armed; single all-ones words are data */
	f2_new(&f, &x, SWW_F2_LEVEL);
	for (i = 0; i < SWW_RI_WORDS; i++)
		f1_put(F2_RI + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (SWW_F2_LOW | SWW_F2_MB) && strstr(logbuf, "F1 19 words at 0x3ed578 are all ones\n") != 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	for (i = 0; i < SWW_RM_WORDS; i++)
		f1_put(F2_RM + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (SWW_F2_LOW | SWW_F2_MB | SWW_F2_RI) && strstr(logbuf, "F1 20 words at 0x3ed600 are all ones\n") != 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	for (i = 0; i < 3; i++)
		f1_put(F2_CONS + 8u + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (F2_ALL & ~SWW_F2_CON) && strstr(logbuf, "F1 3 words at 0x3b19e0 are all ones\n") != 0 && strstr(logbuf, "F1 console:") == 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 0xF8u);
	f1_put(F2_CBUF + 0xF8u, 0xFFFFFFFFu);
	f1_put(F2_CBUF + 0xFCu, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (F2_ALL & ~SWW_F2_CON) && strstr(logbuf, "F1 2 words at 0x3b20f8 are all ones\n") != 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_put(F2_MB, 0xFFFFFFFFu);
	f1_put(F2_MB + 4u, 0xFFFFFFFFu);
	f1_put(F2_RI + 4u, 0xFFFFFFFFu);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && strstr(logbuf, "dongle to host [0x3ed8b8] = 0xffffffff\n") != 0);

	/* a refused host-to-dongle mailbox pointer: the dongle-to-host word is not read either (both or neither are printed); a refused dongle-to-host pointer alone costs one read */
	{
		unsigned n_ok = 0, n_h2d_bad = 0, n_d2h_bad = 0, n_both_bad = 0;

		for (i = 0; i < 4; i++) {
			f2_new(&f, &x, SWW_F2_LEVEL);
			if (i & 1) f1_put(F1_SH + 0x28, 0);
			if (i & 2) f1_put(F1_SH + 0x2C, 0);
			CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == (i ? F2_ALL & ~SWW_F2_MB : F2_ALL));
			if (i == 0) n_ok = f.bar2_n;
			if (i == 1) n_h2d_bad = f.bar2_n;
			if (i == 2) n_d2h_bad = f.bar2_n;
			if (i == 3) n_both_bad = f.bar2_n;
		}
		CHECK(n_h2d_bad + 2u == n_ok && n_d2h_bad + 1u == n_ok && n_both_bad + 2u == n_ok);
	}
	/* the guard: the RAM word at 0x200000 must still be word 0 of the image */
	f2_new(&f, &x, SWW_F2_LEVEL);
	ramarr[SWW_RAM_BASE / 4u] ^= 1u;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f2 == 0 && x.f1 == F2_F1 && f.bar2_n == t8 + 1u && strstr(logbuf, "F2 the RAM changed while the CA7 ran, not read\n") != 0 && strstr(logbuf, "F2 done") == 0 && strstr(logbuf, "F1 PCIe core") != 0);
	snprintf(t, sizeof t, "SWEET WLAN: F2 low window: cfg 0x84 0, [0x200000] 0x%08x, image word 0 0x%08x\n", w0 ^ 1u, w0);
	CHECK(strstr(logbuf, t) != 0);                                                                               /* both values are printed */
	f2_new(&f, &x, SWW_F2_LEVEL);
	ramarr[SWW_RAM_BASE / 4u] = 0xFFFFFFFFu;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == 0 && f.bar2_n == t8 + 1u);
	/* a guard that cannot be evaluated is a refusal: no image, an image shorter than a word, an image whose word 0 is all ones over a RAM word that is all ones */
	f2_new(&f, &x, SWW_F2_LEVEL);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && x.f2 == F2_ALL && x.img == imgbuf && x.img_len >= 4u);
	sv = x.img_len;
	for (i = 0; i < 4; i++) {
		const uint8_t *im = x.img;
		unsigned char sb[4];

		memcpy(sb, imgbuf, 4);
		x.stopped = 0;
		f.bar2_n = 0;
		loglen = 0; logbuf[0] = 0;
		if (i == 0) x.img = NULL;
		if (i == 1) x.img_len = 3u;
		if (i == 2) { x.img_len = 0; ramarr[SWW_RAM_BASE / 4u] = 0; }
		if (i == 3) { memset(imgbuf, 0xFF, 4); ramarr[SWW_RAM_BASE / 4u] = 0xFFFFFFFFu; }
		rc = swl_f2(&x);
		CHECK(rc == 0 && x.f2 == 0 && f.bar2_n == 1u && strstr(logbuf, "F2 the RAM changed while the CA7 ran, not read\n") != 0);
		x.img = im; x.img_len = sv;
		memcpy(imgbuf, sb, 4);
		ramarr[SWW_RAM_BASE / 4u] = w0;
	}
	/* the live window: cfg 0x84 not 0 is refused before any BAR2 read; a level below 9 and a failed precondition stop */
	f.win2 = SWW_WIN_TAIL;
	x.stopped = 0; f.bar2_n = 0; loglen = 0; logbuf[0] = 0;
	rc = swl_f2(&x);
	CHECK(rc == 0 && x.f2 == 0 && f.bar2_n == 0 && strstr(logbuf, "F2 low window: cfg 0x84 is 0x400000, not 0, not read\n") != 0 && strstr(logbuf, "F2 low window: cfg 0x84 0,") == 0);
	f.win2 = 0;
	x.level = SWW_F1_LEVEL;
	rc = swl_f2(&x);
	CHECK(rc == -SWW_E_LEVEL && x.stop_code == SWW_E_LEVEL && f.bar2_n == 0);
	f2_new(&f, &x, SWW_F2_LEVEL);
	CHECK(f1_go(&f, &x, F1_SH) == 0);
	f.cmd |= 4u;                                                                                                 /* bus mastering on: the preconditions fail at the entry */
	f.bar2_n = 0; loglen = 0; logbuf[0] = 0;
	rc = swl_f2(&x);
	CHECK(rc == -SWW_E_PRE && x.stop_code == SWW_E_PRE && f.bar2_n == 0 && x.f2 == 0);

	/* bus mastering turned on during the dumps: found before the console, nothing armed */
	f2_new(&f, &x, SWW_F2_LEVEL);
	f.poke_at = t8 + 5u; f.poke_cmd = 4u;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_PRE) && x.f2 == (SWW_F2_LOW | SWW_F2_MB | SWW_F2_RI | SWW_F2_RM) && f.bar2_n == t8 + 1u + 2u + 19u + 20u && strstr(logbuf, "F1 console 0x") == 0 && strstr(logbuf, "re-halting the CA7 anyway") == 0);

	/* the masks of F1 and F2 stay apart: the shared structure is in window 0, F1 dumps everything; after F1's last read the console size changes, F2 refuses it, F1's mask keeps its bit */
	f2_new(&f, &x, SWW_F1_LEVEL);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	CHECK(f1_go(&f, &x, 0x3AF000u) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL);
	t8 = f.bar2_n;
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	f.poke_at = t8; f.poke_bp = 0x3AF200u + 12u; f.poke_val = 3u;          /* the last read of F1 (the flags word); the console size 0x400 | 3 is not a word multiple */
	rc = f1_go(&f, &x, 0x3AF000u);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && x.f2 == (F2_ALL & ~SWW_F2_CON));
	/* a refused shared structure leaves no pointers from an earlier run behind */
	f2_new(&f, &x, SWW_F2_LEVEL);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && x.f2p[3] == F2_CONS);
	x.stopped = 0;
	(void)swl_f1(&x, 1u, SWW_WIN_TAIL, 0x70050107u);
	CHECK(x.f2p[0] == 0 && x.f2p[1] == 0 && x.f2p[2] == 0 && x.f2p[3] == 0);

	/* a firmware that reports assert or trap: F1 stops, no F2 */
	f2_new(&f, &x, SWW_F2_LEVEL);
	f1_put(F1_SH, 0x70050107u | SWW_SH_ASSERT);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_FWFAIL) && x.f2 == 0 && strstr(logbuf, "F2 ") == 0);
	/* a restore that ended with the cfg 0x84 row failed: window 0 is not certain, no F2 reads (the line says why), the PCIe core group does not run either */
	f2_new(&f, &x, SWW_F1_LEVEL);
	f.win2_stuck = 9;
	CHECK(f1_go(&f, &x, F1_SH) == 0 && x.restore == SWW_RS_FAILED);
	t8 = f.bar2_n;
	f2_new(&f, &x, SWW_F2_LEVEL);
	f.win2_stuck = 9;
	rc = f1_go(&f, &x, F1_SH);
	CHECK(rc == 0 && x.restore == SWW_RS_FAILED && x.f2 == 0 && x.halted == 1 && f.run == 0 && f.bar2_n == t8 && strstr(logbuf, "F2 window 0 dumps not made, restore=failed\n") != 0 && strstr(logbuf, "F2 low window") == 0);
	f2_new(&f, &x, SWW_F1_LEVEL);
	f.win2_stuck = 9;
	CHECK(f1_go(&f, &x, F1_SH) == 0 && strstr(logbuf, "F2 ") == 0);

	/* one fault at every read and store position of W16 + W17 + F1 + F2, and two beyond */
	for (i = 0; i < 4; i++)
		f2_sweep((int)i);
}

/* ---------------------------------------------------------------- run F3 (patch 0136): the F2 dumps while the CA7 runs, through BAR2 window 0 after one cfg 0x84 := 0 ---------------------------------------------------------------- */

#define F3_SH   0x463118u                               /* the layout observed on hardware in run F2 (wlan-runF2-0135, HANDOFF "RUN F2 DONE"): the shared structure in the tail window, the three pointers in window 0 */
#define F3_CONS 0x3EBCECu
#define F3_MB   0x3ECD28u                               /* h2d; the d2h word is at 0x3ECD2C */
#define F3_RI   0x3EC9ECu
#define F3_RM   0x3ECA40u                               /* not observed: chosen by the test */
#define F3_CBUF 0x3E9000u                               /* not observed: chosen by the test */
#define F3_NW   12u                                     /* stores of a clean level 10 run on a RAM with a tail window: run E's 7, cfg 0x84 := 0 (F3), the re-halt, cfg 0x84 (restore), two BAR0 window selects */
#define F3_NRD  1070u                                   /* BAR2 reads F3 adds to level 8: the guard word, mailbox 2, ring_info 19, ring_mem 20, console header 3, console text 1025 words (4096 bytes from byte 1 of a word) */

static char l8f3[1 << 16];

/* a fresh level `level` run from the state after run D: the F2 layout (shared structure in the tail window, pointers below it), RAM below 4 MiB that reads all ones once the CPU is reset again (observed in run F2), no BAR2 read at all
 * after the re-halt, window 0 reads while the CPU runs inside [0x200000, 0x400000); every counter a fault hook compares is zeroed here (AP-91) */
static void f3_new(struct fake *f, struct swl_wx *x, int level)
{
	f2_new(f, x, level);
	f->lo_lo = 0xFFFFFFF0u; f->lo_hi = 0xFFFFFFF4u;
	f->lv_lo = SWW_RAM_BASE; f->lv_hi = SWW_TCM_BYTES;
	f->dead_lo = 1;
	f->n84 = 0; f->lie84_at = 0;
}

/* the log without the F3 block (from the first line that starts with `start` to the end of the "F3 done mask" line) copied to out; 0 when the block is not there */
static int log_wo_f3(char *out, size_t n, const char *start)
{
	const char *a = strstr(logbuf, start), *b = a ? strstr(a, "SWEET WLAN: F3 done mask") : 0;

	if (!a || !b || !strchr(b, '\n') || strlen(logbuf) >= n)
		return 0;
	memcpy(out, logbuf, (size_t)(a - logbuf));
	strcpy(out + (a - logbuf), strchr(b, '\n') + 1);
	gap_strip(out);
	return 1;
}

/* the text of the log after the first re-halt line ("restore: CA7 RESET_ST"), "" when there is none */
static const char *after_rehalt(void)
{
	const char *a = strstr(logbuf, "restore: CA7 RESET_ST");

	return a ? a : "";
}

/* one stop of F3: the CPU is in reset again (the restore of the stop), the restore is ok, the stop code is the expected one, the PCIe core group did not run, no "F3 done" */
static int f3_stopped(const struct fake *f, const struct swl_wx *x, int rc, int code)
{
	return f1_stopped(f, x, rc, code) && f->nw <= F3_NW - 2u && !(x->f1 & SWW_F1_PCI) && strstr(logbuf, "W17 STOP: ") != 0 && strstr(logbuf, "F3 done") == 0 && strstr(logbuf, "F1 PCIe core") == 0;
}

static void f3_layout(void)
{
	f1_layout(F3_SH, F3_CONS, F3_MB, F3_RI, F3_RM, F3_CBUF, 0x2000u, 0x1235u);
	f1_text(F3_CBUF, 0x2000u, 0x235u - 7u, "BEFORE");
	f1_text(F3_CBUF, 0x2000u, 0x235u - 1u, "Q");
	f1_text(F3_CBUF, 0x2000u, 0x235u, "WINSTART\n");
	f1_text(F3_CBUF, 0x2000u, 0x600u, "mid\r\n");
	f1_text(F3_CBUF, 0x2000u, 0x1235u - 10u, "last line\n");
	f1_text(F3_CBUF, 0x2000u, 0x1235u, "AFTER\n");
}

/* one fault of `kind` (0 read fails, 1 store refused, 2 store dropped, 3 store corrupted, 4 the k-th read of cfg 0x84 lies) at every read / store / cfg 0x84 read position of W16 + W17 + F1 + F3, and two beyond */
static void f3_sweep(int kind)
{
	static struct fake f, h;
	static struct swl_wx x;
	unsigned k, n, nclean = 0, nskip = 0, nstop_old = 0;
	int rc;

	f3_new(&h, &x, SWW_F3_LEVEL);
	f3_layout();
	CHECK(f1_go(&h, &x, F3_SH) == 0 && x.f2 == F2_ALL && h.run == 0);
	n = kind == 0 ? h.nrd : kind == 4 ? h.n84 : h.nw;
	CHECK(kind == 0 ? n > 1500 : kind == 4 ? n == 6u : n == F3_NW);
	for (k = 1; k <= n + 2; k++) {
		f3_new(&f, &x, SWW_F3_LEVEL);
		f3_layout();
		if (kind == 0) f.fail_rd_at = k;
		if (kind == 1) f.fail_wr_at = k;
		if (kind == 2) f.drop_at = k;
		if (kind == 3) f.corrupt_at = k;
		if (kind == 4) f.lie84_at = k;
		rc = f1_go(&f, &x, F3_SH);
		CHECK(all_allowed(&f) && f.oob == 0 && f.nw <= F3_NW);
		CHECK(f.started <= 1 || (kind == 3 && x.restore == SWW_RS_FAILED));
		CHECK(!f.run || x.restore != SWW_RS_OK);                        /* a CPU that still runs is never reported as reset again */
		CHECK(rc != 0 || x.halted);
		CHECK(!x.halted || (!f.run && x.ca_ok));
		CHECK(strstr(after_rehalt(), "F2 ") == 0 && strstr(after_rehalt(), "F3 ") == 0);       /* the dumps are made before the re-halt, never after it */
		CHECK(strstr(logbuf, "F2 window 0 dumps not made") == 0 && strstr(logbuf, "F2 low window") == 0);       /* the F2 block of level 9 does not exist at level 10, whatever the restore ended with */
		if (rc == 0 && x.restore == SWW_RS_OK && strstr(logbuf, "F3 done mask") != 0) {
			CHECK(x.f2 == F2_ALL);
			nclean++;
		}
		if (rc == 0 && x.f2 == 0 && strstr(logbuf, "F3 live window 0: cfg 0x84 is ") != 0) {      /* the lying read of the guard: dumps skipped, the run ends as usual */
			CHECK(kind == 4 && f1_clean(&f, &x) && f.nw == F3_NW && strstr(logbuf, "F2 ") == 0);
			nskip++;
		}
		if (kind == 1 && k == 8u) {                                       /* the store of F3 itself is refused */
			CHECK(f3_stopped(&f, &x, rc, SWW_E_WR) && f.nw == 10u && f.win2 == 0 && strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 ok\n") != 0);
		}
		if (kind == 2 && k == 8u) {                                       /* dropped: the window stays on the tail, the read-back sees it */
			CHECK(f3_stopped(&f, &x, rc, SWW_E_READBACK) && f.nw == 10u && f.win2 == 0 && strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 ok\n") != 0);
		}
		if (kind == 3 && k == 8u) {                                       /* corrupted: the window is 1, the read-back sees it, the restore puts it back */
			CHECK(f3_stopped(&f, &x, rc, SWW_E_READBACK) && f.nw == 10u && f.win2 == 0 && strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 ok\n") != 0);
		}
		if (kind == 4 && rc == -SWW_E_OLD)
			nstop_old++;
	}
	CHECK(nclean >= 1u);
	if (kind == 4) {
		CHECK(nskip == 1u && nstop_old >= 1u);                        /* exactly one cfg 0x84 read is the guard of F3; the old-value read of the store stops */
	}
}

static void runf3(void)
{
	static struct fake f, g8;
	static struct swl_wx x;
	static char l10[1 << 16];
	uint32_t i, j, w0, t8, sv, tok = ex.tok;
	int rc;
	char t[400];

	/* the constants this patch adds, restated */
	CHECK(SWW_F3_LEVEL == 10 && SWW_F2_LEVEL == 9 && SWW_F1_LEVEL == 8);

	/* the layout observed in run F2, level 8 as the reference, then level 10: the stores are the ones of level 8 plus ONE cfg 0x84 := 0 before the re-halt, nothing else is stored */
	f3_new(&f, &x, SWW_F1_LEVEL);
	f3_layout();
	CHECK(f1_go(&f, &x, F3_SH) == 0 && f1_clean(&f, &x) && x.f1 == F2_F1 && x.f2 == 0 && f.nw == 11);
	CHECK(strstr(logbuf, "F1 console pointer 0x3ebcec is not a hnd_cons_t in the RAM window, not read\n") != 0 && strstr(logbuf, "F2 ") == 0);
	t8 = f.bar2_n;
	strcpy(l8f3, logbuf);
	gap_strip(l8f3);
	memcpy(&g8, &f, sizeof g8);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	w0 = ramarr[SWW_RAM_BASE / 4u];
	CHECK(w0 == swl_img_word(&x, 0) && w0 != 0 && w0 != 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F3_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F2_F1 && x.f2 == F2_ALL && x.halted == 1 && (x.done & (1ull << SWT_WIN2_ZERO_E)) != 0);
	CHECK(f.nw == F3_NW && memcmp(f.w, g8.w, 7u * sizeof f.w[0]) == 0 && memcmp(&f.w[8], &g8.w[7], 4u * sizeof f.w[0]) == 0);        /* run E's 7 stores, [F3's], then the stores of level 8 */
	CHECK(f.w[7].space == SWW_CFG && f.w[7].win == 0 && f.w[7].off == 0x84 && f.w[7].val == 0 && f.w[8].space == SWW_BAR0 && f.w[8].off == 0x800 && f.w[8].val == 1);          /* the re-halt follows at once: no BAR0 window select while the CPU runs */
	CHECK(f.win0 == 0x18120000u && f.win2 == 0 && f.run == 0);
	CHECK(f.bar2_n == t8 + F3_NRD && f.n84 == g8.n84 + 3u);                                                       /* the read budget: the guard word and the dumps; cfg 0x84 read for the old value, the read-back and the guard */
	CHECK(log_wo_f3(l10, sizeof l10, "SWEET WLAN: F3 cfg 0x84 := 0") == 1 && strcmp(l10, l8f3) == 0);          /* the rest of the log is the one of level 8 */
	snprintf(t, sizeof t, "SWEET WLAN: F3 live window 0: cfg 0x84 0, [0x200000] 0x%08x, image word 0 0x%08x (not required to be equal: the firmware runs)\n", w0, w0);
	CHECK(strstr(logbuf, t) != 0);
	CHECK(strstr(logbuf, "F2 mailbox data: host to dongle [0x3ecd28] = 0x11111111, dongle to host [0x3ecd2c] = 0x22222222\n") != 0);
	CHECK(strstr(logbuf, "F2 ring_info 0x3ec9ec\n") != 0 && strstr(logbuf, "F1 lo ring_info +0x000: 003eca40 b0000001 b0000002 b0000003\n") != 0 && count_str("F1 lo ring_info +0x") == 5);
	CHECK(strstr(logbuf, "F2 ring_mem 0: idx 0 type 16 max_item 64 len_items 40 base 0000000880000000\n") != 0 && count_str("F2 ring_mem ") == 5);
	CHECK(strstr(logbuf, "F1 console 0x3ebcec: buffer 0x3e9000 size 8192 write index 4661\n") != 0 && strstr(logbuf, "F1 con: WINSTART\n") != 0 && strstr(logbuf, "F1 con: last line\n") != 0 && count_str("F1 con: ") == 3);
	CHECK(strstr(logbuf, "F2 done mask 0x1f\n") != 0 && strstr(logbuf, "F3 done mask 0x1f\n") != 0 && strstr(logbuf, "F2 low window") == 0 && strstr(logbuf, "F2 window 0 dumps not made") == 0);          /* the F2 block of level 9 does not run */
	CHECK(strstr(logbuf, "F1 done mask 0xc1\n") < strstr(logbuf, "F3 cfg 0x84 := 0 (BAR2 window 0) while the CA7 runs\n") && strstr(logbuf, "F3 cfg 0x84 := 0") < strstr(logbuf, "F3 live window 0") && strstr(logbuf, "F3 live window 0") < strstr(logbuf, "F2 done mask") &&
	      strstr(logbuf, "F2 done mask") < strstr(logbuf, "F3 done mask") && strstr(logbuf, "F3 done mask") < strstr(logbuf, "restore: CA7 RESET_ST") && strstr(logbuf, "restore: CA7 RESET_ST") < strstr(logbuf, "F1 PCIe core"));
	CHECK(x.f2p[0] == F3_MB && x.f2p[1] == F3_MB + 4u && x.f2p[2] == F3_RI && x.f2p[3] == F3_CONS);
	CHECK(strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 ok\n") > strstr(logbuf, "F3 done mask"));          /* the restore row still puts cfg 0x84 back to 0 */

	/* the RAM word is the firmware's to change: a different word, a zero word are printed and dumped; a second extra store is a REPEAT stop (the tuple is consumed), a level below 10 a LEVEL stop, a failed precondition a PRE stop, nothing stored */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	ramarr[SWW_RAM_BASE / 4u] ^= 1u;
	rc = f1_go(&f, &x, F3_SH);
	snprintf(t, sizeof t, "SWEET WLAN: F3 live window 0: cfg 0x84 0, [0x200000] 0x%08x, image word 0 0x%08x (not required to be equal: the firmware runs)\n", w0 ^ 1u, w0);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && strstr(logbuf, t) != 0 && f.bar2_n == t8 + F3_NRD);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	ramarr[SWW_RAM_BASE / 4u] = 0;
	CHECK(f1_go(&f, &x, F3_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL);
	ramarr[SWW_RAM_BASE / 4u] = w0;
	{
		unsigned nw = f.nw, nrd = f.nrd;

		x.stopped = 0;
		rc = swl_f3(&x, SWW_WIN_TAIL);
		CHECK(rc == -SWW_E_REPEAT && x.stop_code == SWW_E_REPEAT && f.nw == nw && f.nrd == nrd);
		x.stopped = 0; x.stop_code = 0;
		x.level = SWW_F2_LEVEL;
		rc = swl_f3(&x, 0);
		CHECK(rc == -SWW_E_LEVEL && x.stop_code == SWW_E_LEVEL && f.nw == nw && f.nrd == nrd);
	}
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	CHECK(f1_go(&f, &x, F3_SH) == 0 && x.f2 == F2_ALL);
	x.stopped = 0; x.stop_code = 0; x.done &= ~(1ull << SWT_WIN2_ZERO_E);        /* a fresh store is possible: the precondition (bus mastering turned on) is what stops it */
	f.cmd |= 4u;
	{
		unsigned nw = f.nw;

		rc = swl_f3(&x, SWW_WIN_TAIL);
		CHECK(rc == -SWW_E_PRE && x.stop_code == SWW_E_PRE && f.nw == nw && x.f2 == 0);
		x.stopped = 0; x.stop_code = 0;                                  /* no store is needed with window 0 already selected: the entry check of F3 itself stops it */
		rc = swl_f3(&x, 0);
		CHECK(rc == -SWW_E_PRE && x.stop_code == SWW_E_PRE && f.nw == nw && x.f2 == 0);
	}
	/* a word 0 of the image that cannot be compared (no image, an image shorter than a word) is printed as all ones and does not stop: equality is not required */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	CHECK(f1_go(&f, &x, F3_SH) == 0 && x.f2 == F2_ALL && x.img == imgbuf);
	f.dead_lo = 0;
	sv = x.img_len;
	for (i = 0; i < 2; i++) {
		const uint8_t *im = x.img;

		x.stopped = 0; x.stop_code = 0; loglen = 0; logbuf[0] = 0;
		if (i == 0) x.img = NULL;
		else x.img_len = 3u;
		rc = swl_f3(&x, 0);
		CHECK(rc == 0 && x.f2 == F2_ALL && strstr(logbuf, "image word 0 0xffffffff (not required to be equal") != 0);
		x.img = im; x.img_len = sv;
	}

	/* RAM that reads all ones with the CPU running: the dumps are skipped with a clear line, the run goes on to the unchanged re-halt (the F1 PCIe core group still runs) */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	ramarr[SWW_RAM_BASE / 4u] = 0xFFFFFFFFu;
	rc = f1_go(&f, &x, F3_SH);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f2 == 0 && x.halted == 1 && f.nw == F3_NW && f.bar2_n == t8 + 1u && strstr(logbuf, "F3 [0x200000] reads all ones with the CA7 running, window 0 dumps not made\n") != 0);
	CHECK(strstr(logbuf, "F2 ") == 0 && strstr(logbuf, "F3 done") == 0 && strstr(logbuf, "F1 PCIe core") != 0);
	/* the same RAM as the dead one of run F2 (RAM below 4 MiB all ones once the CPU is reset again) is never read: level 9 stops at its guard, as observed; level 10 made its dumps before the halt */
	f3_new(&f, &x, SWW_F2_LEVEL);
	f3_layout();
	f.lo_lo = SWW_RAM_BASE; f.lo_hi = SWW_TCM_BYTES;
	rc = f1_go(&f, &x, F3_SH);
	CHECK(f2_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == 0 && strstr(logbuf, "F2 low window: cfg 0x84 0, [0x200000] 0xffffffff, image word 0 0x") != 0);

	/* the shared structure below the tail window: W17 already stored cfg 0x84 := 0, F3 stores nothing and reads the guard word and the dumps in window 0 */
	f3_new(&f, &x, SWW_F1_LEVEL);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	CHECK(f1_go(&f, &x, 0x3AF000u) == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && f.nw == 12);
	memcpy(&g8, &f, sizeof g8);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	rc = f1_go(&f, &x, 0x3AF000u);
	CHECK(rc == 0 && f1_clean(&f, &x) && x.f1 == F1_ALL && x.f2 == F2_ALL && f.nw == 12 && memcmp(f.w, g8.w, 12u * sizeof f.w[0]) == 0 && f.n84 == g8.n84 + 1u);
	CHECK(strstr(logbuf, "F3 cfg 0x84 := 0") == 0 && strstr(logbuf, "F3 live window 0: cfg 0x84 0,") != 0 && strstr(logbuf, "F3 done mask 0x1f\n") != 0);

	/* RAM that ends at 4 MiB: no tail window at all, F3 stores nothing */
	f2_new(&f, &x, SWW_F1_LEVEL);
	x.ram_bytes = 0x200000u;
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	ramarr[0x3FFFFCu / 4] = tok;
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	eseqset(&f, 2, tok, 0x3AF000u, 0, 0);
	f.boot_bp = 0x3FFFFCu;
	CHECK(e_fast(&x) == 0 && f1_clean(&f, &x) && f.nw == 9);
	f3_new(&f, &x, SWW_F3_LEVEL);
	x.ram_bytes = 0x200000u;
	f1_layout(0x3AF000u, 0x3AF200u, 0x3AF300u, 0x3AF400u, 0x3AF500u, 0x3AF800u, 0x400u, 0x10u);
	ramarr[0x3FFFFCu / 4] = tok;
	f.guard_lo = SWW_RAM_BASE; f.guard_hi = 0x400000u;
	eseqset(&f, 2, tok, 0x3AF000u, 0, 0);
	f.boot_bp = 0x3FFFFCu;
	CHECK(e_fast(&x) == 0 && f1_clean(&f, &x) && f.nw == 9 && x.f2 == F2_ALL);

	/* each pointer alone: not in window 0, unaligned, wrapping, the last bytes that fit; nothing outside [0x200000, 0x400000) is read (oob), no stop, the rest is dumped */
	{
		static const struct f1_ptr fld[] = {
			{ F1_SH + 0x14, 20, SWW_F2_CON }, { F1_SH + 0x28, 4, SWW_F2_MB }, { F1_SH + 0x2C, 4, SWW_F2_MB }, { F1_SH + 0x30, 76, SWW_F2_RI | SWW_F2_RM }, { F2_RI, 80, SWW_F2_RM }, { F2_CONS + 8, 0x100, SWW_F2_CON },
		};
		static const uint32_t common[] = { 0u, 1u, 2u, 3u, 0x1FFFFCu, 0x200001u, 0x3FFFFDu, 0x400000u, 0x400004u, 0x4A0000u, 0x7FFFFCu, 0x800000u, 0xFFFFFFFCu, 0xFFFFFFFFu, 0x80000000u, 0x3FFFFEu };
		const unsigned nc = sizeof common / sizeof common[0];

		for (j = 0; j < sizeof fld / sizeof fld[0]; j++) {
			for (i = 0; i < nc + 2u; i++) {
				uint32_t v = i < nc ? common[i] : i == nc ? SWW_TCM_BYTES - fld[j].bytes + 4u : SWW_TCM_BYTES - fld[j].bytes;
				int refused = i <= nc;                           /* the last two: one word too far, the last bytes that fit */
				unsigned m;

				f3_new(&f, &x, SWW_F3_LEVEL);
				f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
				for (m = 0; m < 0x80u; m++)
					f1_put(SWW_WIN_TAIL + 4u * m, 0);               /* what F1 reads at a pointer into the start of the tail window: zeros, no stop */
				f1_put(fld[j].slot, v);
				if (!refused) {                                  /* a structure that ends exactly at 4 MiB: its words exist and are not all ones */
					for (m = 0; m < fld[j].bytes / 4u; m++)
						f1_put(v + 4u * m, 0x1000u + m);
					if (j == 0) {
						f1_put(v + 8u, F2_CBUF);
						f1_put(v + 12u, 0x100u);
						f1_put(v + 16u, 8u);
					}
					if (j == 3)
						f1_put(v, F2_RM);
				}
				rc = f1_go(&f, &x, F1_SH);
				CHECK(rc == 0 && f1_clean(&f, &x) && (x.f1 & F2_F1) == F2_F1);       /* F1 may dump what lies in the tail window */
				CHECK(x.f2 == (refused ? (F2_ALL & ~fld[j].miss) : F2_ALL));
			}
		}
	}
	/* the console buffer: sizes and write indexes, in window 0 */
	{
		static const struct { uint32_t size, idx; int ok; } cs[] = {
			{ 0u, 0u, 0 }, { 1u, 0u, 0 }, { 2u, 0u, 0 }, { 3u, 0u, 0 }, { 6u, 0u, 0 }, { 4u, 0u, 1 }, { 4u, 7u, 0 }, { 4u, 4u, 1 }, { 0x100u, 0xFFFFFFFFu, 0 }, { 0x100u, 0x104u, 0 }, { 0x100u, 0x100u, 1 }, { 0x100u, 0xFFu, 1 },
			{ 0x400000u, 0u, 0 }, { 0xFFFFFFFCu, 0u, 0 }, { 0xFFFFFFFFu, 0u, 0 }, { 0x4A0000u, 0u, 0 }, { 0x1FFFFCu, 0u, 0 }, { 0x1000u, 0xFFFFu, 0 }, { 0x1000u, 0x1000u, 1 },
		};

		for (i = 0; i < sizeof cs / sizeof cs[0]; i++) {
			f3_new(&f, &x, SWW_F3_LEVEL);
			f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
			f1_put(F2_CONS + 12u, cs[i].size);
			f1_put(F2_CONS + 16u, cs[i].idx);
			rc = f1_go(&f, &x, F1_SH);
			CHECK(rc == 0 && f1_clean(&f, &x) && x.f2 == (cs[i].ok ? F2_ALL : (F2_ALL & ~SWW_F2_CON)));
		}
	}
	/* the console text around the end of the buffer, and more than 128 lines: the caps of F1 hold */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 8u);
	f1_text(F2_CBUF, 0x100u, 8u, "oldest\n");
	f1_text(F2_CBUF, 0x100u, 0xFBu, "wrap-");
	f1_text(F2_CBUF, 0x100u, 0u, "around\nx");
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && count_str("F1 con: ") == 3 && strstr(logbuf, "F1 con: wrap-around\n") != 0 && strstr(logbuf, "F1 console: 256 bytes in 3 lines, 0 not printed\n") != 0);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x400u, 0u);
	for (i = 0; i < 200u; i++) {
		f1_byte(F2_CBUF + 2u * i, (uint8_t)('A' + i % 26u));
		f1_byte(F2_CBUF + 2u * i + 1u, '\n');
	}
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && count_str("F1 con: ") == 128 && strstr(logbuf, "F1 console: 1024 bytes in 200 lines, 72 not printed\n") != 0);
	/* console text is data: a conversion specification in it is printed as it is */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 0x40u);
	f1_text(F2_CBUF, 0x100u, 0u, "%s%n%x\n");
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && strstr(logbuf, "F1 con: %s%n%x\n") != 0);

	/* blocks of 2 or more words that are all ones are a dead link: a stop after the restore, nothing armed; single all-ones words are data */
	f3_new(&f, &x, SWW_F3_LEVEL);
	for (i = 0; i < SWW_RI_WORDS; i++)
		f1_put(F2_RI + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f3_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (SWW_F2_LOW | SWW_F2_MB) && strstr(logbuf, "F1 19 words at 0x3ed578 are all ones\n") != 0);
	f3_new(&f, &x, SWW_F3_LEVEL);
	for (i = 0; i < SWW_RM_WORDS; i++)
		f1_put(F2_RM + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f3_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (SWW_F2_LOW | SWW_F2_MB | SWW_F2_RI) && strstr(logbuf, "F1 20 words at 0x3ed600 are all ones\n") != 0);
	f3_new(&f, &x, SWW_F3_LEVEL);
	for (i = 0; i < 3; i++)
		f1_put(F2_CONS + 8u + 4u * i, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f3_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (F2_ALL & ~SWW_F2_CON) && strstr(logbuf, "F1 3 words at 0x3b19e0 are all ones\n") != 0 && strstr(logbuf, "F1 console:") == 0);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_layout(F1_SH, F2_CONS, F2_MB, F2_RI, F2_RM, F2_CBUF, 0x100u, 0xF8u);
	f1_put(F2_CBUF + 0xF8u, 0xFFFFFFFFu);
	f1_put(F2_CBUF + 0xFCu, 0xFFFFFFFFu);
	rc = f1_go(&f, &x, F1_SH);
	CHECK(f3_stopped(&f, &x, rc, SWW_E_DEAD) && x.f2 == (F2_ALL & ~SWW_F2_CON) && strstr(logbuf, "F1 2 words at 0x3b20f8 are all ones\n") != 0);
	f3_new(&f, &x, SWW_F3_LEVEL);
	f1_put(F2_MB, 0xFFFFFFFFu);
	f1_put(F2_MB + 4u, 0xFFFFFFFFu);
	f1_put(F2_RI + 4u, 0xFFFFFFFFu);
	CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == F2_ALL && strstr(logbuf, "dongle to host [0x3ed8b8] = 0xffffffff\n") != 0);

	/* a refused host-to-dongle mailbox pointer: the dongle-to-host word is not read either (both or neither are printed); a refused dongle-to-host pointer alone costs one read */
	{
		unsigned n_ok = 0, n_h2d_bad = 0, n_d2h_bad = 0, n_both_bad = 0;

		for (i = 0; i < 4; i++) {
			f3_new(&f, &x, SWW_F3_LEVEL);
			if (i & 1) f1_put(F1_SH + 0x28, 0);
			if (i & 2) f1_put(F1_SH + 0x2C, 0);
			CHECK(f1_go(&f, &x, F1_SH) == 0 && f1_clean(&f, &x) && x.f2 == (i ? F2_ALL & ~SWW_F2_MB : F2_ALL));
			if (i == 0) n_ok = f.bar2_n;
			if (i == 1) n_h2d_bad = f.bar2_n;
			if (i == 2) n_d2h_bad = f.bar2_n;
			if (i == 3) n_both_bad = f.bar2_n;
		}
		CHECK(n_h2d_bad + 2u == n_ok && n_d2h_bad + 1u == n_ok && n_both_bad + 2u == n_ok);
	}

	/* bus mastering turned on during the dumps (the CPU runs): found before the console, the stop re-halts the CA7 anyway (the memory decode is still on) and puts cfg 0x84 back */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	f.poke_at = t8 + 5u; f.poke_cmd = 4u;
	rc = f1_go(&f, &x, F3_SH);
	CHECK(f3_stopped(&f, &x, rc, SWW_E_PRE) && x.f2 == (SWW_F2_LOW | SWW_F2_MB | SWW_F2_RI | SWW_F2_RM) && f.bar2_n == t8 + 1u + 2u + 19u + 20u && strstr(logbuf, "F1 console 0x") == 0 && strstr(logbuf, "re-halting the CA7 anyway") != 0);
	CHECK(f.win2 == 0 && f.nw == 10u);

	/* a firmware that reports assert or trap: F1 stops before F3 */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	f1_put(F3_SH, 0x70050107u | SWW_SH_ASSERT);
	rc = f1_go(&f, &x, F3_SH);
	CHECK(f1_stopped(&f, &x, rc, SWW_E_FWFAIL) && x.f2 == 0 && strstr(logbuf, "F3 ") == 0);

	/* the window select of F3 is ignored by the chip (stores from number 8 on): the read-back stops, the restore of cfg 0x84 is ignored too and ends FAILED, the CA7 is re-halted all the same (its row comes first), no dump, no PCIe core group */
	f3_new(&f, &x, SWW_F3_LEVEL);
	f3_layout();
	f.win2_stuck = 8;
	rc = f1_go(&f, &x, F3_SH);
	CHECK(rc == -SWW_E_READBACK && x.stop_code == SWW_E_READBACK && x.restore == SWW_RS_FAILED && f.run == 0 && x.ca_ok == 1 && f.win2 == SWW_WIN_TAIL && f.nw == 10u && x.f2 == 0 && all_allowed(&f) && f.oob == 0);
	CHECK(strstr(logbuf, "restore space 1 win 0 off 0x84 := 0 FAILED\n") != 0 && strstr(logbuf, "F2 ") == 0 && strstr(logbuf, "F3 live window") == 0 && strstr(logbuf, "F1 PCIe core") == 0);

	/* one fault at every read, store and cfg 0x84 read position of W16 + W17 + F1 + F3, and two beyond */
	for (i = 0; i < 5; i++)
		f3_sweep((int)i);
}

/* patch 0137: swl_gap() dedups on (area, what), prints at counts 1, 2, 4 ..., and swl_gap_summary() lists every entry with its final count; a STOP is a gap */
static void gap_report(void)
{
	struct swl_wx x;
	unsigned i;

	loglen = 0; logbuf[0] = 0;
	swl_gap_summary();
	CHECK(loglen == 0);                                     /* no gap, no line */
	for (i = 0; i < 5; i++)
		swl_gap("f1", "mailbox data pointers not readable", "high");
	swl_gap("w15", "cfg 0x88 not implemented", "medium");
	CHECK(swl_gap_nd == 2 && swl_gap_tot == 6);
	CHECK(strstr(logbuf, "WIFI GAP: f1 mailbox data pointers not readable x1 [high]\n") != NULL);
	CHECK(strstr(logbuf, "x2 [high]") != NULL && strstr(logbuf, "x4 [high]") != NULL);
	CHECK(strstr(logbuf, "x3 [high]") == NULL && strstr(logbuf, "x5 [high]") == NULL);
	loglen = 0; logbuf[0] = 0;
	memset(&x, 0, sizeof x);
	x.row = 7;
	CHECK(swl_stop(&x, SWW_E_RD) < 0);
	CHECK(strstr(logbuf, "WIFI GAP: stop W7 STOP: ") != NULL);
	loglen = 0; logbuf[0] = 0;
	swl_gap_summary();
	CHECK(strstr(logbuf, "WIFI GAPS: 3 distinct, 7 total\n") != NULL);
	CHECK(strstr(logbuf, "WIFI GAP: f1 mailbox data pointers not readable x5 [high]\n") != NULL);
	for (i = 0; i < SWL_GAP_MAX + 2u; i++) {               /* a full table is itself loud */
		char w[16];
		snprintf(w, sizeof w, "w%u", i);
		swl_gap("t", w, "low");
	}
	CHECK(swl_gap_nd == SWL_GAP_MAX && swl_gap_over > 0 && strstr(logbuf, "gaps table full") != NULL);
	memset(swl_gap_tab, 0, sizeof swl_gap_tab); swl_gap_nd = swl_gap_tot = swl_gap_over = 0;
	loglen = 0; logbuf[0] = 0;
}

int main(void)
{
	/* Order: cheap phases and the ones that kill most mutants first, the long sweeps (runb_faults ~9 s, runc_faults ~4 s, runb_deny_sweep ~3 s at -O1) last,
	 * so a fail-fast mutant run ends early. Every phase passes alone except runf1/runf2, which continue from rune's state (ex.tok): keep rune, runf1, runf2 together, in this order. */
	gap_report();
	rev_gate();
	happy();
	fault_sweeps();
	deny_sweep();
	gates();
	steps();
	runb_happy();
	runb_full();
	runb_gates();
	runc_happy();
	runc_prep();
	runc_gates();
	runc_deny_sweep();
	rune();
	runf1();
	runf2();
	runf3();
	rund();
	runb_deny_sweep();
	runc_faults();
	runb_faults();
	if (fails) {
		fprintf(stderr, "RESULT: FAIL wlan_wr_host_test (%u checks)\n", fails);
		return 1;
	}
	fprintf(stderr, "RESULT: PASS wlan_wr_host_test\n");
	return 0;
}
