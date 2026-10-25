/* Host test for wlanlink.c (--wlan-link, docs/research/29 section 14) against a FAKE SoC: GPIO banks with Samsung semantics (a pin whose CON field is 1 reads its DAT latch, any other pin the external
 * level), plain register files for PMA / PCS / ELBI / I/A / DBI that read back what was written, lock bits that appear after k polls, the PMU isolation word behind a fake of the two EL3 steps
 * (wlan_link_iso_set / _clear), and fault / timeout / ignored-write injection. Built once per N (-DWLAN_LINK_N=7..36), see build.sh. N = 34 / 35 / 36 add the FIRST BAR0 writes (research 40, goal W-C3): S34 the SRPWR request (PCIe core page powerctl), S35 the SYS_MEM wrapper core reset + coreinfo / bankidx / bankinfo, S36 the ARM CA7 halt, against a register model of the three cores, with a fault, a swallowed write and a wrong stored value at EVERY BAR0 write, refusals from the exact write allowlist (memw_ok vs an independent spec grid), and the restore FIRST on every stop path. Checks: the default run (N = 19) never raises PERST,
 * never enables the LTSSM and ends with the pins, the isolation and the PHY back where they were; N = 20 / 21 add the PERST release and the options; N = 22 adds the documented S22 skip (one 0 us delay row, no write); N = 23 enables the LTSSM and waits for L0 (fake: ELBI 0x2C8 == 0x11
 * after k polls, never, or a read fault), with a fault injected at EVERY forward write row and the teardown checked against the table order; N = 33 adds S33 (READ-ONLY chip state: three more cfg 0x80 window moves, PMU / ARM CA7 wrapper / SYS_MEM wrapper reads at exact offsets, restore first on every stop path); N = 30 adds S30 (the first BAR0 read: ChipCommon chip id through MEM, read-only, offset 0 only, behind a fake of region 1 / BAR0 window / Command, all-ones, fault and wrong-id mutants); N = 29 adds S29 (iATU outbound region 1, BAR0 window select, Command := MEM only); N = 28 adds S28 (BAR sizing + assignment); N = 27 adds S27 (READ-ONLY endpoint config dump: 56 CFG window dwords + the 7 iATU region 1 registers, `LINK R27` lines, no write of any kind); N = 26 adds S26 (iATU region 0 programmed with the 7 allowlisted writes, each read back, then the CFG window probe + 4 reads; the ATU is torn down FIRST); N = 25 adds S25 (read-only iATU layout: DBI words + the ATU block probe + ATU region 0 + ELBI, `LINK R25` lines); N = 24 adds S24, read-only rows whose `log` flag prints `LINK R24 row=<i> rd=0x..` (the link report), no write, teardown unchanged; every failure branch (unreadable probe, lock timeout, write fault, refused / failed isolation, failed restore) stops, tears down in the table order with the isolation clear LAST and is
 * loud; the writer's independent allowlist refuses every other register. Uses the REAL generated board macros and out/wlan_seq.h. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -DWLAN_LINK_RUN -DWLAN_LINK_N=19 -I. -Iout -include out/board_config.h -o out/wlanlink_host_test19 wlanlink_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define FULL_MASK_T 0xffffffffu
#define WLAN_STR2(x) #x
#define WLAN_STR(x) WLAN_STR2(x)
#ifndef WLAN_LINK_N
#define WLAN_LINK_N 19
#endif
/* S24 and S25 each read ELBI 0x2C8 once more, after the L0 wait: one extra poll of the L0 fake each */
#if WLAN_LINK_N >= 25
#define L0X 2u
#elif WLAN_LINK_N >= 24
#define L0X 1u
#else
#define L0X 0u
#endif
/* S29 adds teardown rows at the head: region 1 disable, Command 0, BAR0 window, and (after the BAR rows) the region 1 resets */
#if WLAN_LINK_N >= 29
#define NTEAR29 6u
#else
#define NTEAR29 0u
#endif
/* S28 writes the endpoint BARs (sizing + assignment) through the CFG window: 4 teardown rows go FIRST */
#if WLAN_LINK_N >= 28
#define NCFGT 4u
#else
#define NCFGT 0u
#endif
/* S27 reads 56 dwords of the CFG window (and 7 ATU region 1 registers) */
#if WLAN_LINK_N >= 27
#define CFG27 56u
#else
#define CFG27 0u
#endif
#define WLAN_LINK_N_STR WLAN_STR(WLAN_LINK_N)
#include "wlanlink.c"

/* the state-free view of the allowlist (cmd only; BAR0 unreadable, region 1 not ready) and the full one */
static int walc(uintptr_t pa, uint32_t mask, uint32_t old, uint32_t v, uint32_t cmd, uint32_t bar0, int r1ok)
{
	struct w32_ctx c = { cmd, bar0, r1ok, 0, 0, 0 };

	return w32_allowed(pa, mask, old, v, &c);
}
#define wal(pa, mask, old, v, cmd) walc(pa, mask, old, v, cmd, 0xFFFFFFFFu, 0)

#if !(SOC_GS101 && defined(WLAN_LINK_RUN) && defined(WLAN_PMA))
#error "needs the bluejay board_config.h and -DWLAN_LINK_RUN"
#endif

/* ---- fake SoC ---- */
struct bank { uint32_t con, latch, ext, w[6]; };
static struct bank gp14, gh2;
static uint32_t pma[0x2000 / 4], pcs[0x1000 / 4], elbi[0x2000 / 4], ia[0x1000 / 4], dbi[0x1000 / 4], atu[0x1000 / 4], cfgspace[0x2000 / 4];
static unsigned cfg_reads, cfg_reads_unconfigured, cfg_writes_unconfigured, mem_reads, mem_reads_unready, master_writes;
static unsigned atu_reads, cfg_dev_reads;
static int mem_mode;		/* 0 = chip id, 1 = all ones (UR without a fault), 2 = the read faults, 3 = another id, 4 = eromptr not 4 KiB aligned, 5 = eromptr outside 0x18000000..0x1FFFFFFF, 6 = eromptr all ones only, 7 = clk_ctl_st / capabilities_ext all ones only */
static uint32_t bar_sz[2];	/* fake BAR0 / BAR2 size in bytes (power of two) */
static uint32_t iso1, iso0;
static unsigned pll_after, cdr_after, oc_after, pll_cnt, cdr_cnt, oc_cnt, l0_after, l0_cnt, l0_polls;
static int never_l0, fault_l0_read;
static uint64_t waited_at_en;
static int never_pll, unreadable_blk, iso_set_ret, iso_clear_ret, write_fault_n, ignore_pa_set, win_ignore, win_latch;	/* win_ignore: a cfg 0x80 write of 0x18105000 is swallowed; win_latch: it latches 0x18101000 instead */
static uintptr_t ignore_pa, fault_read_pa;
static int fault_cfg80_once, clobber_win;	/* review 13 L3: once the 8 MEM reads of S30/S31 are done, the next cfg 0x80 read (the S32 precondition) faults / finds the window moved to 0x18001000 */
static unsigned win_which, win_seen;	/* S34..: win_fault_v / win_ign_v / win_lat_v act on the win_which-th write of that value (0 = every write) */
static int s33_mode, win_fault_v, win_ign_v, win_lat_v, clobber33, fault33;	/* S33 (research 38): s33_mode 1 pmurev 0x27, 2 ARM RESET_CTL bit 8, 3 SYS_MEM in reset, 4 SYS_MEM clock off, 5 SYS_MEM RESET_CTL bit 8, 6 PMU all ones, 7 coreinfo all ones, 8 SYS_MEM IOCTL 3 (FGC), 9 SYS_MEM RESET_ST 1 (pending backplane ops); win_fault_v / win_ign_v / win_lat_v: a cfg 0x80 write of that value faults / is swallowed / latches value + 0x1000; clobber33 / fault33: the first cfg 0x80 read after the EROM dump (the S33 precondition) finds 0x18001000 / faults */
static unsigned mem_illegal, s33_reads, core_reads;	/* BAR0 reads that the test's own (independent) allowlist does not know / reads in the S33 windows / reads of the SYS_MEM core page */
/* S34..S36 (research 40): the register model of the three cores behind BAR0. Writes are checked by the TEST'S OWN spec (spec_memw, a data table, independent of memw_ok); mem_illegal_w counts writes it rejects (must stay 0) */
static uint32_t cp_powerctl, sm_io, sm_rc, sm_rs, arm_io, arm_rc, arm_rs, sm_coreinfo, sm_bankidx, sm_bankinfo;
static unsigned srpwr_cnt, srpwr_after, mem_wr_n, mw_n, mem_illegal_w, mem_wr_unready, mw_log[16];
static int chip_mode, mw_kind;	/* chip_mode (N >= 34): 1 SRPWR status never set, 2 SYS_MEM RESET_CTL release does not take, 3 SYS_MEM IOCTL write does not take, 4 RESET_ST stuck 1 after the assert, 5 coreinfo all ones, 6 coreinfo nrb = 1, 7 coreinfo nb = 0, 8 ARM RESET_CTL release does not take, 9 ARM IOCTL write does not take, 10 SYS_MEM IOCTL keeps FGC on the final write, 11 SYS_MEM RESET_ST stuck 1 from the start, 12 powerctl dead (all ones), 13 SRPWR already on, 14 powerctl PowerDomain id [31:28] = 0 (no SRPWR capability); mw_kind: 1 = the mw_n-th BAR0 write is swallowed, 2 = it stores a wrong value */
static unsigned link_dead_after;	/* review 13 L3: after this many EROM reads every config read answers all ones (link down mid-dump); 0 = off */
static uint64_t freq, tick, waited_us;
static unsigned nsave;
static char lines[1600][RAMLOG_LINE_MAX + 8];	/* S32 logs ~1030 R lines */
static unsigned nl;
enum { EV_W, EV_ISO_SET, EV_ISO_CLR };
static struct { int t; uintptr_t pa; uint32_t v; } ev[800];
static unsigned nev, nwrites;

uint64_t counter_freq(void) { return freq; }
void wait_ticks(uint64_t t) { tick += t; waited_us += freq ? t / (freq / 1000000u) : 0; }
uint64_t touch_ticks(void) { return tick += 3; }
void pstore_save(void) { nsave++; }
void con_str(const char *l, const char *s)
{
	assert(nl < 1600 && strlen(s) < RAMLOG_LINE_MAX);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", l, s);
}

static uint32_t *cell(uintptr_t pa)
{
	if (pa >= WLAN_PMA && pa < WLAN_PMA + 0x2000) return &pma[(pa - WLAN_PMA) / 4];
	if (pa >= WLAN_PCS && pa < WLAN_PCS + 0x1000) return &pcs[(pa - WLAN_PCS) / 4];
	if (pa >= WLAN_ELBI && pa < WLAN_ELBI + 0x2000) return &elbi[(pa - WLAN_ELBI) / 4];
	if (pa >= WLAN_IA && pa < WLAN_IA + 0x1000) return &ia[(pa - WLAN_IA) / 4];
	if (pa >= WLAN_DBI && pa < WLAN_DBI + 0x1000) return &dbi[(pa - WLAN_DBI) / 4];
	if (pa >= WLAN_ATU && pa < WLAN_ATU + 0x1000) return &atu[(pa - WLAN_ATU) / 4];
	return NULL;
}

static uint32_t dat_read(const struct bank *b)
{
	uint32_t v = 0;

	for (unsigned i = 0; i < 8; i++)
		v |= (((b->con >> (4 * i)) & 0xf) == 1 ? (b->latch >> i) & 1u : (b->ext >> i) & 1u) << i;
	return v;
}

static int blk_unreadable(uintptr_t pa)
{
	return (unreadable_blk == 1 && pa >= WLAN_PMA && pa < WLAN_PMA + 0x2000) || (unreadable_blk == 2 && pa >= WLAN_IA && pa < WLAN_IA + 0x1000) ||
	       (unreadable_blk == 3 && pa >= WLAN_ELBI && pa < WLAN_ELBI + 0x2000) || (unreadable_blk == 4 && pa >= WLAN_DBI && pa < WLAN_DBI + 0x1000) ||
	       (unreadable_blk == 5 && pa >= WLAN_ATU && pa < WLAN_ATU + 0x1000) || (unreadable_blk == 6 && pa >= WLAN_CFG && pa < WLAN_CFG + 0x2000) ||
	       (unreadable_blk == 7 && pa >= WLAN_MEM && pa < WLAN_MEM + 0x1000);
}

static int region0_ok(void)
{
	return atu[0] == 0x4u && atu[1] == 0x80000000u && atu[2] == 0x60FFE000u && atu[3] == 0u && atu[4] == 0x60FFFFFFu && atu[5] == 0x01000000u && atu[6] == 0u;
}

/* BAR0 is reachable from the CPU only while region 1 is the enabled MEM window over BAR0, the EP decodes MEM (Command.MEM), BAR0 holds the assigned address and the window select points at a page the model knows */
static int s33_win(uint32_t w)
{
	return w == 0x18012000u || w == 0x18120000u || w == 0x18126000u || w == 0x18026000u || w == 0x18001000u;
}

/* the test's own copy of the per-window read list (research 38 s.4, research 40 s.5; independent of mem_ok) */
static int t_mem_allowed(uint32_t win, uint32_t off)
{
	if (win == 0x18000000u) return off == 0u || off == 0xACu || off == 0xFCu || off == 0x1E0u;
	if (win == 0x18105000u) return off < 0xE00u;
	if (win == 0x18012000u) return off == 0x600u || off == 0x604u || off == 0x608u || off == 0x618u || off == 0x61Cu;
	if (win == 0x18120000u || win == 0x18126000u) return off == 0x408u || off == 0x800u || off == 0x804u;
	if (win == 0x18001000u) return off == 0x1E8u;
	if (win == 0x18026000u) return off == 0u || off == 0x10u || off == 0x40u;
	return 0;
}

static void chip_reset(void)
{
	cp_powerctl = 0; sm_io = arm_io = 0; sm_rc = arm_rc = 1; sm_rs = arm_rs = 0;	/* observed run w3a-n33: IOCTL 0, RESET_CTL 1, RESET_ST 0 */
	sm_coreinfo = 0x00000005u; sm_bankidx = 0x3u; sm_bankinfo = 0x00000007u;	/* FAKE: nb = 5, nrb = 0, bankidx not 0 at start, bank size (7 + 1) * 8 KiB */
	srpwr_cnt = 0; srpwr_after = 3; mem_wr_n = 0; mw_n = 0; mw_kind = 0; mem_illegal_w = mem_wr_unready = 0; chip_mode = 0;
	memset(mw_log, 0, sizeof mw_log);
}

static uint32_t chip_get(uint32_t win, uint32_t off)	/* the model's register values, no side effect */
{
	if (win == 0x18012000u) {
		if (s33_mode == 6) return 0xFFFFFFFFu;
		return off == 0x604u ? (s33_mode == 1 ? 0x20010027u : 0x20010028u) : off == 0x600u ? 0x00000100u : off == 0x608u ? 0x0000002Cu : off == 0x618u ? 0x0000FFFFu : 0x00FFFFFFu;
	}
	if (win == 0x18120000u)
		return off == 0x408u ? arm_io : off == 0x800u ? (s33_mode == 2 ? arm_rc | 0x100u : arm_rc) : arm_rs;
	if (win == 0x18126000u)
		return off == 0x408u ? sm_io : off == 0x800u ? (s33_mode == 5 ? sm_rc | 0x100u : sm_rc) : (chip_mode == 11 ? 1u : sm_rs);
	if (win == 0x18001000u)
		return chip_mode == 12 ? 0xFFFFFFFFu : chip_mode == 15 ? 0x00030300u : chip_mode == 16 ? 0x00020000u : chip_mode == 17 ? 0x00000200u : chip_mode == 18 ? 0x30030300u : (cp_powerctl & 0xF00u) | ((cp_powerctl & 0x200u) && (chip_mode == 13 || (chip_mode != 1 && srpwr_cnt >= srpwr_after)) ? 0x20000u : 0u) | (chip_mode == 14 ? 0u : 0x30000000u);	/* PowerDomain id [31:28] non-zero, ExtPwrStatus bit 17 follows the request after srpwr_after reads */
	return off == 0u ? sm_coreinfo : off == 0x10u ? sm_bankidx : sm_bankinfo;
}

/* the test's own write spec: a table of (window, offset, value, predecessor mask/value, old mask/value) plus the two rules that are not exact values; independent of memw_ok. pred = the companion register (spec_pred_off) */
struct wspec { uint32_t win, off, v, pm, pv, om, ov; };
static const struct wspec wspecs[] = {
	{ 0x18126000u, 0x800u, 1u, 0, 0, 0, 0 }, { 0x18126000u, 0x800u, 0u, 0x3FFFu, 0x3u, 0, 0 }, { 0x18126000u, 0x408u, 0x3u, 1u, 1u, 0, 0 }, { 0x18126000u, 0x408u, 0x1u, 1u, 0u, 0x3FFFu, 0x3u },
	{ 0x18120000u, 0x800u, 1u, 0, 0, 0, 0 }, { 0x18120000u, 0x800u, 0u, 0x3FFFu, 0x23u, 0, 0 }, { 0x18120000u, 0x408u, 0x23u, 1u, 1u, 0, 0 }, { 0x18120000u, 0x408u, 0x21u, 1u, 0u, 0x3FFFu, 0x23u },
};
static uint32_t spec_pred_off(uint32_t off) { return off == 0x800u ? 0x408u : off == 0x408u ? 0x800u : off == 0x10u ? 0u : 0xFFFFFFFFu; }
static int spec_memw(uint32_t win, uint32_t off, uint32_t old, uint32_t v, uint32_t pred)
{
	if (win == 0x18001000u) return off == 0x1E8u && old != 0xFFFFFFFFu && (old >> 28) != 0u && (v & ~0x200u) == (old & ~0x200u) && (v & 0x200u);
	if (win == 0x18026000u) return off == 0x10u && v == 0u && pred != 0xFFFFFFFFu && (pred & 0x3E0u) == 0u && (pred & 0x1Fu) >= 1u;
	for (unsigned i = 0; i < sizeof wspecs / sizeof wspecs[0]; i++)
		if (wspecs[i].win == win && wspecs[i].off == off && wspecs[i].v == v && (pred & wspecs[i].pm) == wspecs[i].pv && (old & wspecs[i].om) == wspecs[i].ov)
			return 1;
	return 0;
}

static uint32_t s33_val(uint32_t win, uint32_t off)	/* a BAR0 read: side effects (the SRPWR status follows the reads of powerctl) and the S35 coreinfo knobs */
{
	if (win == 0x18001000u && (cp_powerctl & 0x200u))
		srpwr_cnt++;
	if (win == 0x18026000u && off == 0u)
		return chip_mode == 5 ? 0xFFFFFFFFu : chip_mode == 6 ? 0x00000025u : chip_mode == 7 ? 0u : sm_coreinfo;
	return chip_get(win, off);
}

static void chip_write(uint32_t win, uint32_t off, uint32_t v)	/* a BAR0 write (the window and the guards were checked by the caller) */
{
	uint32_t old = s33_win(win) ? chip_get(win, off) : 0, po = spec_pred_off(off), pred = po == 0xFFFFFFFFu ? 0u : (win == 0x18026000u && po == 0u ? (chip_mode == 5 ? 0xFFFFFFFFu : chip_mode == 6 ? 0x25u : chip_mode == 7 ? 0u : sm_coreinfo) : chip_get(win, po));
	unsigned me = ++mem_wr_n;

	if (!spec_memw(win, off, old, v, pred))
		mem_illegal_w++;
	if (me < 16u)
		mw_log[me] = off;
	if (mw_n == me && mw_kind == 1)
		return;
	if (mw_n == me && mw_kind == 2)
		v ^= off == 0x1E8u ? 0x200u : 1u;
	if (win == 0x18001000u && off == 0x1E8u) {
		cp_powerctl = v & 0xF00u;			/* PowerOnRequest [11:8] is the only writable field; ExtPwrStatus and the domain id are read-only */
	} else if (win == 0x18126000u || win == 0x18120000u) {
		int sm = win == 0x18126000u;
		uint32_t *io = sm ? &sm_io : &arm_io, *rc = sm ? &sm_rc : &arm_rc, *rs = sm ? &sm_rs : &arm_rs;

		if (off == 0x800u) {
			if (v == 0u && ((sm && chip_mode == 2) || (!sm && chip_mode == 8)))
				return;			/* the release does not take */
			*rc = v;
			if (v == 1u && sm && chip_mode == 4)
				*rs = 1u;		/* RESET_ST sticks */
		} else if (off == 0x408u) {
			if ((sm && chip_mode == 3) || (!sm && chip_mode == 9))
				return;			/* the IOCTL write does not take */
			*io = (sm && chip_mode == 10 && v == 1u) ? 3u : v;
		} else {
			mem_illegal_w++;
		}
	} else if (win == 0x18026000u && off == 0x10u) {
		sm_bankidx = v;
	} else {
		mem_illegal_w++;
	}
}

static int mem_ready(void)
{
	return atu[0x200 / 4] == 0u && atu[0x204 / 4] == 0x80000000u && atu[0x208 / 4] == 0x60000000u && atu[0x20C / 4] == 0u && atu[0x210 / 4] == 0x6000FFFFu && atu[0x214 / 4] == 0x60000000u && atu[0x218 / 4] == 0u &&
	       (cfgspace[1] & 2u) != 0u && cfgspace[4] == 0x60000004u && (cfgspace[0x80 / 4] == 0x18000000u || cfgspace[0x80 / 4] == 0x18105000u || s33_win(cfgspace[0x80 / 4]));
}

/* FAKE EROM page (a synthetic dword per offset: dword 0 = a component CIA-like word with bit 0 set and bits 2:1 clear, the rest a pattern), seen only through the window 0x18105000 */
/* W-C1c: the EOT dword 0xF sits at index eot_idx (default 217 = the 0-based index observed in run w3a-n32, row 637, offset 0x364; the dump then read zeros up to dword 340 and the capture ended at the host --timeout 600; a phone hang is a guess); EOT_NEVER = no EOT in the page.
 * Every read past the EOT is counted in erom_past_eot and must stay 0 (the interpreter must never issue it). */
#define EOT_NEVER 0xFFFFFFFFu
static unsigned eot_idx = 217u, erom_past_eot;
static uint32_t erom_dw(unsigned i)
{
	if (i == eot_idx)
		return 0xFu;
	if (eot_idx != EOT_NEVER && i > eot_idx)
		return 0u;
	return i == 0 ? 0x4BF80001u : 0x11000000u + i;
}
static unsigned erom_reads;

int try_read32(uintptr_t pa, uint32_t *v)
{
	uint32_t *c;

	if (pa == fault_read_pa || blk_unreadable(pa))
		return 0;
	if (pa == WLAN_CFG + 0x80 && mem_reads >= 8u) {
		if (fault_cfg80_once) { fault_cfg80_once = 0; return 0; }
		if (clobber_win) { clobber_win = 0; cfgspace[0x80 / 4] = 0x18001000u; }
	}
	if (pa == WLAN_CFG + 0x80 && erom_reads >= 219u) {		/* the first read after the S32 dump = the S33 precondition */
		if (fault33) { fault33 = 0; return 0; }
		if (clobber33) { clobber33 = 0; cfgspace[0x80 / 4] = 0x18001000u; }
	}
	atu_reads += pa >= WLAN_ATU && pa < WLAN_ATU + 0x1000;
	cfg_dev_reads += pa >= WLAN_CFG && pa < WLAN_CFG + 0x2000;
	if (pa >= WLAN_MEM && pa < WLAN_MEM + 0x1000) {		/* fake BAR0: an unmapped window (not ready) = a bus abort model: unreadable */
		mem_reads++;
		if (!mem_ready()) {
			mem_reads_unready++;
			return 0;
		}
		if (mem_mode == 2)
			return 0;
		mem_illegal += !t_mem_allowed(cfgspace[0x80 / 4], (unsigned)(pa - WLAN_MEM));
		if (s33_win(cfgspace[0x80 / 4])) {
			s33_reads++;
			core_reads += cfgspace[0x80 / 4] == 0x18026000u;
			*v = s33_val(cfgspace[0x80 / 4], (uint32_t)(pa - WLAN_MEM));
			return 1;
		}
		if (cfgspace[0x80 / 4] == 0x18105000u) {		/* the EROM page: mode 8 = all ones (read failure); every dword offset answers */
			erom_reads++;
			erom_past_eot += eot_idx != EOT_NEVER && (unsigned)((pa - WLAN_MEM) / 4) > eot_idx;
			*v = mem_mode == 8 ? 0xFFFFFFFFu : erom_dw((unsigned)((pa - WLAN_MEM) / 4));
			return 1;
		}
		if (mem_mode == 1)
			*v = 0xFFFFFFFFu;
		else if (mem_mode == 3)
			*v = 0x00404390u;
		else if (pa == WLAN_MEM)
			*v = 0x10034389u;		/* the full word observed on hardware (run w3a-n30 R30, HO:255) */
		else if (pa == WLAN_MEM + 0xFC)
			*v = mem_mode == 4 ? 0x18105004u : mem_mode == 5 ? 0x20000000u : mem_mode == 6 ? 0xFFFFFFFFu : mem_mode == 9 ? 0x18109000u : 0x18105000u;	/* eromptr as observed on hardware (run w3a-n31); mode 9 = a different aligned value (S32 must refuse to move the window) */
		else if (pa == WLAN_MEM + 0x1E0)
			*v = mem_mode == 7 ? 0xFFFFFFFFu : 0x00050008u;			/* FAKE clk_ctl_st */
		else if (pa == WLAN_MEM + 0xAC)
			*v = mem_mode == 7 ? 0xFFFFFFFFu : 0x00000040u;			/* FAKE capabilities_ext */
		else
			*v = 0u;
		return 1;
	}
	if (pa >= WLAN_CFG && pa < WLAN_CFG + 0x2000) {		/* the CFG window answers only while outbound region 0 is the enabled CFG0 -> bus 1 window (Linux's values); otherwise a bus abort = unreadable */
		cfg_reads++;
		if (!region0_ok()) {
			cfg_reads_unconfigured++;
			return 0;
		}
		*v = cfgspace[(pa - WLAN_CFG) / 4];
		if (link_dead_after && erom_reads >= link_dead_after)
			*v = 0xFFFFFFFFu;
		return 1;
	}
	if (pa == WLAN_PMU_ALIVE + 0x3EC4) { *v = iso1; return 1; }
	if (pa == WLAN_PMU_ALIVE + 0x3EC0) { *v = iso0; return 1; }
	if (pa >= WLAN_GPP14 && pa < WLAN_GPP14 + 0x18) {
		unsigned o = (unsigned)(pa - WLAN_GPP14);
		*v = o == 0 ? gp14.con : o == 4 ? dat_read(&gp14) : gp14.w[o / 4];
		return 1;
	}
	if (pa >= WLAN_GPH2 && pa < WLAN_GPH2 + 0x18) {
		unsigned o = (unsigned)(pa - WLAN_GPH2);
		*v = o == 0 ? gh2.con : o == 4 ? dat_read(&gh2) : gh2.w[o / 4];
		return 1;
	}
	c = cell(pa);
	assert(c && "read outside every modelled block");
	*v = *c;
	if (pa == WLAN_ELBI + 0x2C8 && (elbi[0x54 / 4] & 1u)) {		/* RDLH_LINKUP / LTSSM state: only polled after the LTSSM enable */
		l0_polls++;
		if (fault_l0_read)
			return 0;
		*v = (*v & ~0x3fu) | (!never_l0 && ++l0_cnt >= l0_after ? 0x11u : 0x0u);
	}
	if (pa == WLAN_PMA + 0x3F0) { if (!never_pll && ++pll_cnt >= pll_after) *v |= 8; else *v &= ~8u; }
	if (pa == WLAN_PMA + 0xE0C) { if (++cdr_cnt >= cdr_after) *v |= 4; else *v &= ~4u; }
	if (pa == WLAN_PMA + 0xDE8) { if (++oc_cnt >= oc_after) *v |= 0xf; else *v &= ~0xfu; }
	return 1;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	uint32_t *c;

	assert(nev < 800);
	ev[nev].t = EV_W; ev[nev].pa = pa; ev[nev].v = v; nev++;
	nwrites++;
	if (pa == WLAN_ELBI + 0x54 && (v & 1u))
		waited_at_en = waited_us;
	if (write_fault_n && nwrites == (unsigned)write_fault_n)
		return 0;
	if (ignore_pa_set && pa == ignore_pa)
		return 1;
	if (pa == WLAN_CFG + 0x80 && v == 0x18105000u && win_ignore)
		return 1;
	if (pa == WLAN_CFG + 0x80 && v != 0 && (v == (uint32_t)win_fault_v || v == (uint32_t)win_ign_v || v == (uint32_t)win_lat_v))
		win_seen++;
	if (pa == WLAN_CFG + 0x80 && v != 0 && v == (uint32_t)win_fault_v && (!win_which || win_seen == win_which))
		return 0;
	if (pa == WLAN_CFG + 0x80 && v != 0 && v == (uint32_t)win_ign_v && (!win_which || win_seen == win_which))
		return 1;
	if (pa >= WLAN_GPP14 && pa < WLAN_GPP14 + 0x18) {
		unsigned o = (unsigned)(pa - WLAN_GPP14);
		if (o == 0) gp14.con = v; else if (o == 4) gp14.latch = v; else gp14.w[o / 4] = v;
		return 1;
	}
	if (pa >= WLAN_GPH2 && pa < WLAN_GPH2 + 0x18) {
		unsigned o = (unsigned)(pa - WLAN_GPH2);
		if (o == 0) gh2.con = v; else if (o == 4) gh2.latch = v; else gh2.w[o / 4] = v;
		return 1;
	}
	if (pa >= WLAN_MEM && pa < WLAN_MEM + 0x1000) {		/* BAR0 write (S34..): only while the window is ready, else the TLP is lost (a model of a dead window: no effect, counted) */
		if (!mem_ready()) {
			mem_wr_unready++;
			return 1;
		}
		chip_write(cfgspace[0x80 / 4], (uint32_t)(pa - WLAN_MEM), v);
		return 1;
	}
	if (pa >= WLAN_CFG && pa < WLAN_CFG + 0x2000) {		/* the CFG window: only through the configured outbound region 0; the BARs model sizing (low: size mask | type 0x4, high: plain) */
		unsigned o = (unsigned)(pa - WLAN_CFG);

		if (!region0_ok())
			cfg_writes_unconfigured++;
		assert(((o >= 0x10 && o < 0x20) || o == 0x04 || o == 0x80) && "CFG write outside the BARs / Command / BAR0 window");
		if (o == 0x04) {		/* Command low half is plain, the status half is RW1C (a zero write clears nothing); MASTER is counted */
			master_writes += (v & 4u) != 0;
			cfgspace[1] = (v & 0xFFFFu) | (cfgspace[1] & ~(v & 0xFFFF0000u) & 0xFFFF0000u);
		} else if (o == 0x80) {
			cfgspace[o / 4] = (v == 0x18105000u && win_latch) ? 0x18101000u : (v != 0 && v == (uint32_t)win_lat_v && (!win_which || win_seen == win_which)) ? v + 0x1000u : v;
		} else {
			cfgspace[o / 4] = (o & 4u) ? v : ((v & ~(bar_sz[o >= 0x18] - 1u) & 0xFFFFFFF0u) | 4u);
		}
		return 1;
	}
	c = cell(pa);
	assert(c && "write outside every modelled block");
	*c = v;
	return 1;
}

int wlan_link_iso_set(uint32_t *rd)
{
	ev[nev].t = EV_ISO_SET; ev[nev].pa = 0; ev[nev].v = 0; nev++;
	if (iso_set_ret == 0)
		iso1 |= 1;
	*rd = iso1;
	return iso_set_ret;
}

int wlan_link_iso_clear(uint32_t *rd)
{
	ev[nev].t = EV_ISO_CLR; ev[nev].pa = 0; ev[nev].v = 0; nev++;
	if (iso_clear_ret == 0)
		iso1 &= ~1u;
	*rd = iso1;
	return iso_clear_ret;
}

static void world(void)
{
	memset(pma, 0, sizeof(pma)); memset(pcs, 0, sizeof(pcs)); memset(elbi, 0, sizeof(elbi)); memset(ia, 0, sizeof(ia)); memset(dbi, 0, sizeof(dbi)); memset(atu, 0, sizeof(atu)); atu[0x10 / 4] = 0xFFFu; atu[0x210 / 4] = 0xFFFu;		/* ATU regions 0 / 1 at reset (runs w3a-n25 / w3a-n28c): all 0, LIMIT 0xFFF */
	memset(cfgspace, 0, sizeof(cfgspace)); cfgspace[0] = 0x43DC14E4u; cfgspace[1] = 0x00100000u; cfgspace[2] = 0x02800003u; cfgspace[3] = 0x00010000u; cfgspace[4] = 4u; cfgspace[6] = 4u; cfgspace[0x80 / 4] = 0x18001000u; cfgspace[0x88 / 4] = 0x00080080u; cfgspace[0xBC / 4] = 0x10110000u; cfgspace[0x100 / 4] = 0x13C10001u; bar_sz[0] = 0x10000u; bar_sz[1] = 0x400000u;
	cfg_reads = cfg_reads_unconfigured = cfg_writes_unconfigured = mem_reads = mem_reads_unready = master_writes = atu_reads = cfg_dev_reads = 0; mem_mode = 0;		/* BAR0 64 KiB, BAR2 4 MiB (run w3a-n28c R28 rows 355 / 365) */
	memset(&gp14, 0, sizeof(gp14)); memset(&gh2, 0, sizeof(gh2));
	gp14.con = 0x33; gp14.ext = 0x3;			/* run w1: CON 0x33, DAT 0x3 */
	gh2.con = 0; gh2.ext = 0x2;				/* PERST input low, CLKREQ high */
	iso1 = 0; iso0 = 0;
	pll_after = 3; cdr_after = 2; oc_after = 4; pll_cnt = cdr_cnt = oc_cnt = l0_cnt = l0_polls = 0;
	l0_after = 3; never_l0 = fault_l0_read = 0; waited_at_en = 0;
	never_pll = unreadable_blk = iso_set_ret = iso_clear_ret = write_fault_n = ignore_pa_set = win_ignore = win_latch = 0;
	win_which = win_seen = 0; s33_mode = win_fault_v = win_ign_v = win_lat_v = clobber33 = fault33 = 0; mem_illegal = s33_reads = core_reads = 0;
	chip_reset();
	erom_reads = 0; erom_past_eot = 0; eot_idx = 217u; fault_cfg80_once = clobber_win = 0; link_dead_after = 0;
	ignore_pa = fault_read_pa = 0;
	freq = 24576000ull; tick = 1000; waited_us = 0; nsave = 0;
	nl = nev = nwrites = 0;
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static int has_last_step_ok(void)	/* 2 = "LINK Snn ok" and the clean summary for this build's N, 1 = only the step line, 0 = neither */
{
	char b[64];

	snprintf(b, sizeof b, "LINK S%02d ok", WLAN_LINK_N);
	if (!has(b))
		return 0;
	snprintf(b, sizeof b, "LINK summary reached=S%02d stopped=none restore=ok", WLAN_LINK_N);
	return has(b) ? 2 : 1;
}

static void fail(int line, const char *e)
{
	fprintf(stderr, "FAIL line %d: %s\n", line, e);
	for (unsigned i = 0; i < nl; i++)
		fprintf(stderr, "  %s\n", lines[i]);
	assert(0);
}
#define EXPECT(c) do { if (!(c)) fail(__LINE__, #c); } while (0)

/* every write of a run is a row of the table (steps <= N, or teardown) with exactly its register; nothing else, ever */
static void writes_are_table_rows(void)
{
	for (unsigned i = 0; i < nev; i++) {
		unsigned k, ok = 0;

		if (ev[i].t != EV_W)
			continue;
		for (k = 0; k < WLAN_SEQ_NROWS + WLAN_SEQ_NTEAR && !ok; k++) {
			struct wlan_row r;

			if (!get_row(k < WLAN_SEQ_NROWS ? k : k - WLAN_SEQ_NROWS, k >= WLAN_SEQ_NROWS, &r))
				continue;
			if ((r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON || r.kind == WLK_MMIO_RMW || r.kind == WLK_MMIO_WRITE) && pa_of(r.blk, r.off) == ev[i].pa &&
			    (k >= WLAN_SEQ_NROWS || r.step <= WLAN_LINK_N))
				ok = 1;
		}
		EXPECT(ok);
	}
}

static int wrote(uintptr_t pa, uint32_t mask, uint32_t val)
{
	for (unsigned i = 0; i < nev; i++)
		if (ev[i].t == EV_W && ev[i].pa == pa && (ev[i].v & mask) == val)
			return 1;
	return 0;
}

static unsigned last_ev(int t, uintptr_t lo, uintptr_t hi)
{
	unsigned r = 0;

	for (unsigned i = 0; i < nev; i++)
		if (ev[i].t == t && (t != EV_W || (ev[i].pa >= lo && ev[i].pa < hi)))
			r = i + 1;
	return r;
}

static unsigned first_ev(uintptr_t pa, uint32_t v)	/* 1 + index of the first write of v to pa, 0 if none */
{
	for (unsigned i = 0; i < nev; i++)
		if (ev[i].t == EV_W && ev[i].pa == pa && ev[i].v == v)
			return i + 1;
	return 0;
}

static unsigned count_w(uintptr_t lo, uintptr_t hi)
{
	unsigned n = 0;

	for (unsigned i = 0; i < nev; i++)
		n += ev[i].t == EV_W && ev[i].pa >= lo && ev[i].pa < hi;
	return n;
}

static void baseline_restored(void)
{
	EXPECT(gp14.con == 0x33 && (dat_read(&gp14) & 4) == 0 && gh2.con == 0 && (dat_read(&gh2) & 1) == 0 && iso1 == 0);
}

static unsigned reached_step(void)	/* from the summary line: "LINK summary reached=Snn ..." */
{
	for (unsigned i = 0; i < nl; i++) {
		const char *p = strstr(lines[i], "LINK summary reached=S");

		if (p)
			return (unsigned)atoi(p + strlen("LINK summary reached=S"));
	}
	return 99;
}

/* PERST is never high at the end of a run: the last write to the GPH2 DAT clears bit 0 (when one raised it), the pin latch is low and the pin is an input again */
static void perst_low_at_end(void)
{
	unsigned last = 0, raised = 0;

	for (unsigned i = 0; i < nev; i++)
		if (ev[i].t == EV_W && ev[i].pa == WLAN_GPH2 + 4) {
			last = i + 1;
			raised |= ev[i].v & 1u;
		}
	EXPECT(!raised || (ev[last - 1].v & 1u) == 0u);
	EXPECT((gh2.latch & 1u) == 0 && (dat_read(&gh2) & 1u) == 0 && gh2.con == 0);
}

/* the teardown of a run that reached step hi (>= 15: every probed block has teardown rows) is, in order, exactly the write-like teardown rows with step <= hi: the tail of the event log
 * equals the table order (T01 PERST low, T02 LTSSM disable [only if S23 started], T03 WLAN_EN low, soft reset, PHY power-down, isolation clear, then the pins back to inputs) */
static void teardown_tail_is_table_order(unsigned hi)
{
	int et[64];
	uintptr_t epa[64];
	unsigned n = 0, first_perst_low = 0, skip = 0;
	struct wlan_row r;

	for (unsigned i = 0; i < WLAN_SEQ_NTEAR; i++) {
		if (!get_row(i, 1, &r) || r.step > hi)
			continue;
		if (r.kind == WLK_SMC_ISO) {
			EXPECT(n < 64);
			et[n] = EV_ISO_CLR; epa[n++] = 0;
		} else if (r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON || r.kind == WLK_MMIO_RMW || r.kind == WLK_MMIO_WRITE) {
			EXPECT(n < 64);
			et[n] = EV_W; epa[n++] = pa_of(r.blk, r.off);
		}
	}
	EXPECT(n > 8 && nev >= n);
	for (unsigned j = 0; j < n; j++) {
		EXPECT(ev[nev - n + j].t == et[j]);
		EXPECT(et[j] != EV_W || ev[nev - n + j].pa == epa[j]);
	}
	if (hi >= 26) {		/* S26 started: the head of the teardown is (S29: region 1 disable, Command 0, BAR0 window,) (S28: the 4 BAR rows, the CFG window dies with the ATU disable,) (S29: the 3 region 1 resets,) then the 7 region 0 rows
			 * with CR2 (region disable) FIRST, all before PERST low */
		unsigned p32 = hi >= 32 ? 1u : 0u, pre = hi >= 29 ? 3u : 0u, nc = hi >= 28 ? 4u : 0u, a1 = hi >= 29 ? 3u : 0u, b0 = (unsigned)(nev - n) + p32;

		if (p32)		/* S32: the window goes back to ChipCommon FIRST of all (LIFO), before the region 1 disable */
			EXPECT(ev[b0 - 1].t == EV_W && ev[b0 - 1].pa == WLAN_CFG + 0x80 && ev[b0 - 1].v == 0x18000000u);
		if (pre) {
			EXPECT(ev[b0].t == EV_W && ev[b0].pa == WLAN_ATU + 0x204 && ev[b0].v == 0);					/* region 1 disable FIRST (RC-local, cannot stall) */
			EXPECT(ev[b0 + 1].pa == WLAN_CFG + 4 && ev[b0 + 1].v == 0 && ev[b0 + 2].pa == WLAN_CFG + 0x80 && ev[b0 + 2].v == 0x18001000u);	/* Command := 0, then the window select back */
		}
		for (unsigned j = 0; j < nc; j++)
			EXPECT(ev[b0 + pre + j].t == EV_W && ev[b0 + pre + j].pa == WLAN_CFG + 0x10 + 4 * j && ev[b0 + pre + j].v == ((j & 1u) ? 0u : 4u));
		if (a1) {
			EXPECT(ev[b0 + pre + nc].pa == WLAN_ATU + 0x208 && ev[b0 + pre + nc].v == 0 && ev[b0 + pre + nc + 1].pa == WLAN_ATU + 0x210 && ev[b0 + pre + nc + 1].v == 0xFFFu);
			EXPECT(ev[b0 + pre + nc + 2].pa == WLAN_ATU + 0x214 && ev[b0 + pre + nc + 2].v == 0);
		}
		EXPECT(ev[b0 + pre + nc + a1].pa == WLAN_ATU + 4 && ev[b0 + pre + nc + a1].v == 0);
		for (unsigned j = 0; j < 7; j++)
			EXPECT(ev[b0 + pre + nc + a1 + j].t == EV_W && ev[b0 + pre + nc + a1 + j].pa >= WLAN_ATU && ev[b0 + pre + nc + a1 + j].pa < WLAN_ATU + 0x200);
		EXPECT(ev[b0 + pre + nc + a1 + 7].pa == WLAN_GPH2 + 4);
		EXPECT(atu[0] == 0 && atu[1] == 0 && atu[2] == 0 && atu[3] == 0 && atu[4] == 0xFFFu && atu[5] == 0 && atu[6] == 0);	/* the registers are back at their reset values */
		if (hi >= 29) {
			EXPECT(atu[0x200 / 4] == 0 && atu[0x204 / 4] == 0 && atu[0x208 / 4] == 0 && atu[0x20C / 4] == 0 && atu[0x210 / 4] == 0xFFFu && atu[0x214 / 4] == 0 && atu[0x218 / 4] == 0);	/* region 1 too */
			EXPECT(cfgspace[1] == 0x00100000u && cfgspace[0x80 / 4] == 0x18001000u);								/* Command and the window select are back */
		}
		skip = p32 + pre + nc + a1 + 7;
	}
	if (hi >= 20) {		/* T01: PERST low first; T02: LTSSM disable second if the S23 step started; then WLAN_EN low */
		first_perst_low = nev - n + skip;
		EXPECT(ev[first_perst_low].pa == WLAN_GPH2 + 4 && (ev[first_perst_low].v & 1u) == 0);
		if (hi >= 23)
			EXPECT(ev[first_perst_low + 1].pa == WLAN_ELBI + 0x54 && ev[first_perst_low + 1].v == 0 && ev[first_perst_low + 2].pa == WLAN_GPP14 + 4);
		else
			EXPECT(ev[first_perst_low + 1].pa == WLAN_GPP14 + 4);
	}
	EXPECT(last_ev(EV_ISO_CLR, 0, 0) > last_ev(EV_W, WLAN_PMA, WLAN_PMA + 0x2000));	/* the isolation clear after the last PHY write */
	perst_low_at_end();
}

int main(void)
{
	struct wlan_row r;

	EXPECT(WLAN_SEQ_LAST == 36 && WLAN_SEQ_DEFAULT_N == 19 && WLAN_SEQ_NROWS > 200 && WLAN_LINK_N <= WLAN_SEQ_LAST);
	/* the independent allowlist refuses every other register (a corrupted table / index cannot reach them) */
	EXPECT(!wal(WLAN_GPP14 + 4, 1u << 3, 0, 0, 0) && !wal(WLAN_GPP14 + 4, 1u << 2, 0, 1u << 3, 0) && wal(WLAN_GPP14 + 4, 1u << 2, 0, 1u << 2, 0));
	EXPECT(!wal(WLAN_GPH2 + 4, 1u << 2, 0, 0, 0) && wal(WLAN_GPH2 + 4, 3u, 0, 1, 0) && !wal(WLAN_GPH2, 0xf00u, 0, 0, 0) && !wal(WLAN_GPP14 + 0x18, 1, 0, 0, 0));
	EXPECT(!wal(WLAN_GPP14 + 0, 0, 0, 0, 0));		/* an empty mask is not a write */
	EXPECT(!wal(WLAN_PMA + 0x2000, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_PMA + 0x1ffe, FULL_MASK_T, 0, 0, 0) && wal(WLAN_PMA + 0x1ffc, FULL_MASK_T, 0, 0, 0));
	EXPECT(!wal(WLAN_ELBI + 0x500, FULL_MASK_T, 0, 0, 0) && wal(WLAN_ELBI + 0x54, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_ELBI + 0x2c8, FULL_MASK_T, 0, 0, 0));
	EXPECT(!wal(WLAN_DBI + 0x8bc, FULL_MASK_T, 0, 0, 0) && wal(WLAN_DBI + 0x890, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_IA + 0x180, FULL_MASK_T, 0, 0, 0));
	EXPECT(!wal(WLAN_PMU_ALIVE + 0x3EC4, FULL_MASK_T, 0, 0, 0) && !wal(0x17460810UL, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_PCS + 0x1000, FULL_MASK_T, 0, 0, 0));
	world();
	EXPECT(w32(WLAN_SEQ_NROWS, 0) == -2 && w32(WLAN_SEQ_NTEAR, 1) == -2 && w32(100000u, 0) == -2 && nwrites == 0);	/* index overflow */
	for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)		/* a row beyond N is refused by the writer itself */
		if (get_row(i, 0, &r) && r.step > WLAN_LINK_N && (r.kind == WLK_MMIO_WRITE || r.kind == WLK_GPIO_DAT))
			EXPECT(w32(i, 0) == -2);
	EXPECT(nwrites == 0);

	/* a table row of a non-write kind is never written */
	for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
		if (get_row(i, 0, &r) && (r.kind == WLK_DELAY || r.kind == WLK_READ_ONLY || r.kind == WLK_WAIT_EQ || r.kind == WLK_SMC_ISO))
			EXPECT(w32(i, 0) == -2);

	/* need_probe is checked by behaviour, not only by its sha pin (review 7 L3, review 6 info 5): a row of a probe-gated block is refused by run_row unless THAT block's probe ran, with no access */
	{
		unsigned allb = 0, nprobes[WLB_MEM + 1] = { 0 }, first_probe[WLB_MEM + 1] = { 0 }, first_row[WLB_MEM + 1] = { 0 };

		world();
		for (unsigned b = 0; b <= WLB_MEM; b++)
			allb |= need_probe(b);
		EXPECT(need_probe(WLB_MEM) == 1u << WLB_MEM && need_probe(WLB_CFG) == 1u << WLB_CFG && need_probe(WLB_ATU) == 1u << WLB_ATU && need_probe(WLB_NONE) == 0);
		for (unsigned t = 0; t < 2; t++)
			for (unsigned i = 0; i < (t ? WLAN_SEQ_NTEAR : WLAN_SEQ_NROWS); i++) {
				unsigned probed, rd = 0;

				EXPECT(get_row(i, t, &r));
				if (r.blk == WLB_NONE || !need_probe(r.blk) || (r.kind == WLK_READ_ONLY && r.a == 1u))
					continue;
				probed = 0;
				EXPECT(run_row(i, t, &probed, &rd) == ST_REFUSED);
				probed = allb & ~need_probe(r.blk);
				EXPECT(run_row(i, t, &probed, &rd) == ST_REFUSED);
			}
		EXPECT(nwrites == 0 && cfg_reads == 0 && mem_reads == 0);
		/* the table twin: per probe-gated block exactly one probe row, and it is the block's first row */
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++) {
			EXPECT(get_row(i, 0, &r));
			if (r.blk == WLB_NONE || !need_probe(r.blk))
				continue;
			if (!first_row[r.blk])
				first_row[r.blk] = i + 1u;
			if (r.kind == WLK_READ_ONLY && r.a == 1u) {
				nprobes[r.blk]++;
				first_probe[r.blk] = i + 1u;
			}
		}
		for (unsigned b = 0; b <= WLB_MEM; b++)
			EXPECT(!need_probe(b) || (nprobes[b] == 1 && first_probe[b] == first_row[b]));
	}

	/* ---- the happy path ---- */
	world();
	EXPECT(wlan_link_run() == 0);
	writes_are_table_rows();
	EXPECT(has("LINK table sha=") && has("LINK S00 ok") && has_last_step_ok() == 2 && !has("!!!"));
	EXPECT(ev[0].t == EV_W && ev[0].pa == WLAN_GPH2 + 4 && ev[1].pa == WLAN_GPH2 && ev[2].pa == WLAN_GPP14 + 4 && ev[3].pa == WLAN_GPP14);	/* S01 DAT, CON; S02 DAT, CON */
	EXPECT(waited_us >= 200000u + 1000u);
	EXPECT(nsave >= 2u * (WLAN_LINK_N + 1u));		/* a pstore checkpoint before and after every step */
	{
		unsigned iso_set = 0;

		for (unsigned i = 0; i < nev; i++)
			if (ev[i].t == EV_ISO_SET)
				iso_set = i + 1;
		EXPECT(iso_set && last_ev(EV_ISO_CLR, 0, 0) > last_ev(EV_W, WLAN_PMA, WLAN_PMA + 0x2000));		/* the isolation clear comes after the last PHY write */
		EXPECT(last_ev(EV_ISO_CLR, 0, 0) < last_ev(EV_W, WLAN_GPP14, WLAN_GPP14 + 1));				/* ... and before the pins go back to inputs */
	}
#if WLAN_LINK_N >= 8
	EXPECT(wrote(WLAN_PMA + 0xD8, 0xffffffff, 0x28) && wrote(WLAN_PMA + 0x400, 0x80, 0));
#endif
#if WLAN_LINK_N >= 19
	EXPECT(wrote(WLAN_DBI + 0x890, 0xffffffff, 0x12000));
#endif
	baseline_restored();
#if WLAN_LINK_N < 24
	EXPECT(!has("LINK R"));					/* the log flag exists only on the S24 / S25 rows */
#endif
	if (getenv("WLAN_DUMP")) {
		for (unsigned i = 0; i < nl; i++)
			printf("%s\n", lines[i]);
		printf("writes=%u ev=%u waited_us=%llu\n", nwrites, nev, (unsigned long long)waited_us);
	}
#if WLAN_LINK_N < 20
	EXPECT(!wrote(WLAN_GPH2 + 4, 1, 1));			/* the default run NEVER raises PERST */
	EXPECT(!wrote(WLAN_ELBI + 0x54, 0xffffffff, 1) && !wrote(WLAN_ELBI + 0x3bc, 0xffffffff, 0x11));	/* nor enables the LTSSM / the options */
	EXPECT(ev[nev - 1].pa == WLAN_GPH2 && ev[nev - 1].t == EV_W);
#else
	EXPECT(wrote(WLAN_GPH2 + 4, 1, 1));			/* S20: PERST high ... */
	{
		unsigned hi = 0, lo = 0;

		for (unsigned i = 0; i < nev; i++)
			if (ev[i].t == EV_W && ev[i].pa == WLAN_GPH2 + 4) {
				if (ev[i].v & 1u && !hi) hi = i + 1;
				if (hi && !(ev[i].v & 1u) && !lo) lo = i + 1;
			}
		EXPECT(hi && lo > hi);				/* ... and PERST low again, first thing of the teardown (T01) */
		EXPECT(ev[lo].t == EV_W || ev[lo].t == EV_ISO_CLR);
	}
#endif
#if WLAN_LINK_N >= 21
	EXPECT(wrote(WLAN_ELBI + 0x3bc, 0xffffffff, 0x11) && wrote(WLAN_ELBI + 0x3a0, 0xffffffff, 2));
#endif
#if WLAN_LINK_N >= 21 && WLAN_LINK_N < 23
	EXPECT(!wrote(WLAN_ELBI + 0x54, 0xffffffff, 1));		/* the LTSSM stays disabled before S23 */
#endif
#if WLAN_LINK_N >= 22
	{	/* S22 is the documented skip: exactly one row, a 0 us delay, so the step runs ("ok rows=1") and writes nothing */
		unsigned n22 = 0;

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 22) {
				n22++;
				EXPECT(r.kind == WLK_DELAY && r.a == 0);
			}
		EXPECT(n22 == 1 && has("LINK S22 start") && has("LINK S22 ok rows=1"));
	}
#endif
#if WLAN_LINK_N >= 23
	/* S23: LTSSM enable = ELBI 0x54 := 1, then the L0 wait polls ELBI 0x2C8 until (v & 0x3f) == 0x11 (fake: the 3rd poll), then the teardown disables the LTSSM */
	EXPECT(wrote(WLAN_ELBI + 0x54, 0xffffffff, 1) && l0_polls == 3u + L0X && has("LINK S23 ok rows=2") && wrote(WLAN_ELBI + 0x54, 0xffffffff, 0) && !has("!!!"));
	EXPECT(last_ev(EV_W, WLAN_ELBI + 0x54, WLAN_ELBI + 0x55) > 0 && ev[last_ev(EV_W, WLAN_ELBI + 0x54, WLAN_ELBI + 0x55) - 1].v == 0);	/* the LTSSM ends disabled */
	teardown_tail_is_table_order(23);
#endif
#if WLAN_LINK_N >= 24
	{	/* S24: 13 read-only log rows (DBI/ELBI, a == 2 = log, never a probe), no write; every one prints `LINK R24 row=<i> rd=0x<value>` with the value the fake holds */
		unsigned n24 = 0, nR = 0;
		uint32_t want[32];
		unsigned wid[32];
		char b[64];

		world();
		dbi[0x000 / 4] = 0xA5A5144Du; dbi[0x034 / 4] = 0x40u; dbi[0x070 / 4] = 0x00000010u; dbi[0x07C / 4] = 0x0000D123u; dbi[0x080 / 4] = 0x00220040u; dbi[0x0A0 / 4] = 0x00020002u;
		dbi[0x710 / 4] = 0x00010120u; dbi[0x80C / 4] = 0x00010000u | 0x0Cu; elbi[0x2C8 / 4] = 0x0300AA00u;
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 24) {
				EXPECT(r.kind == WLK_READ_ONLY && r.a == 2u && (r.blk == WLB_DBI || r.blk == WLB_ELBI) && n24 < 32);
				wid[n24++] = i;
			}
		EXPECT(n24 == 13);
		for (unsigned j = 0; j < n24; j++)
			(void)get_row(wid[j], 0, &r), want[j] = r.blk != WLB_DBI ? 0x0300AA11u : r.off == 0x890u ? 0x12000u : dbi[r.off / 4];	/* ELBI 0x2C8: L0 once the LTSSM is enabled and the fake counted 3 polls */
		EXPECT(wlan_link_run() == 0 && has("LINK S24 ok rows=13") && !has("!!!"));
		for (unsigned j = 0; j < n24; j++) {
			snprintf(b, sizeof b, "LINK R24 row=%u rd=0x%08X", wid[j], (unsigned)want[j]);
			EXPECT(has(b));
		}
		for (unsigned i = 0; i < nl; i++)
			if (strstr(lines[i], "LINK R24 "))
				nR++;
		EXPECT(nR == n24 && !has("LINK R23") && !has("LINK R19"));
		teardown_tail_is_table_order(WLAN_LINK_N);
		baseline_restored();
		world(); fault_read_pa = WLAN_DBI + 0x70;		/* an unreadable capability word stops S24 (fault at that row), teardown unchanged, restore ok */
		EXPECT(wlan_link_run() == 1 && has("LINK S24 fault row=244") && has("stopped=fault at=S24 row=244 restore=ok") && !has("!!!") && has("LINK R24 row=243"));
		teardown_tail_is_table_order(24);
		baseline_restored();
	}
#endif
#if WLAN_LINK_N >= 25
	{	/* S25: READ-ONLY iATU layout: 6 DBI words, the ATU block probe (a == 1, not logged), ATU 0x000..0x018, ELBI 0x2C8; 14 `LINK R25` lines; the ATU window is never written */
		unsigned n25 = 0, nR = 0, probe_row = 0, nprobe = 0, natu = 0;
		uint32_t want, atu_pre[7];
		unsigned wid[32];
		char b[64];

		world();
		dbi[0x018 / 4] = 0x00010100u; dbi[0x03C / 4] = 0x000001FFu; dbi[0x8BC / 4] = 1u; dbi[0x8F8 / 4] = 0x3532302Au; dbi[0x8FC / 4] = 0x45703032u; dbi[0x900 / 4] = 0xFFFFFFFFu;
		atu[0x00 / 4] = 0x4u; atu[0x04 / 4] = 0x80000000u; atu[0x08 / 4] = 0x60FFE000u; atu[0x10 / 4] = 0x60FFFFFFu; atu[0x14 / 4] = 0x01000000u; elbi[0x2C8 / 4] = 0x0300AA00u;
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 25) {
				EXPECT(r.kind == WLK_READ_ONLY && (r.blk == WLB_DBI || r.blk == WLB_ATU || r.blk == WLB_ELBI) && n25 < 32);
				EXPECT(r.a == (r.blk == WLB_ATU && r.off == 0u && !nprobe ? 1u : 2u));	/* the first ATU 0x000 row is the probe, every other one logs */
				if (r.a == 1u)
					nprobe++, probe_row = i;
				else
					wid[n25++] = i;
				natu += r.blk == WLB_ATU;
			}
		EXPECT(n25 == 14 && nprobe == 1 && natu == 8);
		memcpy(atu_pre, atu, sizeof atu_pre);		/* S26 (N = 26) rewrites and then restores the ATU: S25 logged what it found */
		EXPECT(wlan_link_run() == 0 && has("LINK S25 ok rows=15") && !has("!!!"));
		for (unsigned j = 0; j < n25; j++) {
			(void)get_row(wid[j], 0, &r);
			want = r.blk == WLB_ELBI ? 0x0300AA11u : r.blk == WLB_DBI ? dbi[r.off / 4] : atu_pre[r.off / 4];
			snprintf(b, sizeof b, "LINK R25 row=%u rd=0x%08X", wid[j], (unsigned)want);
			EXPECT(has(b));
		}
		for (unsigned i = 0; i < nl; i++)
			if (strstr(lines[i], "LINK R25 "))
				nR++;
		EXPECT(nR == n25 && has("LINK R24 row=") && (WLAN_LINK_N >= 26 || last_ev(EV_W, WLAN_ATU, WLAN_ATU + 0x1000) == 0));	/* S24 still logs its own rows; S25 itself wrote nothing to the ATU window (only S26 does) */
		teardown_tail_is_table_order(WLAN_LINK_N);
		baseline_restored();
		world(); unreadable_blk = 5;			/* the ATU probe fails: stop at that row, no ATU row ran, no R25 line from the ATU/ELBI rows, teardown unchanged, restore ok */
		snprintf(b, sizeof b, "LINK S25 fault row=%u", probe_row);
		EXPECT(wlan_link_run() == 1 && has(b) && has("stopped=fault at=S25") && has("restore=ok") && !has("!!!"));
		nR = 0;
		for (unsigned i = 0; i < nl; i++)
			nR += strstr(lines[i], "LINK R25 ") != NULL;
		EXPECT(nR == 6);				/* only the 6 DBI rows before the probe */
		teardown_tail_is_table_order(25);
		baseline_restored();
	}
#endif

#if WLAN_LINK_N >= 26
	{	/* S26: outbound region 0 = CFG0 -> bus 1: 7 ATU writes (CR2 enable last), each read back (`LINK R26`), then the CFG window probe (not logged) and 4 logged window reads; the ATU is torn down FIRST */
		static const struct { uint32_t off, v; } sw[7] = { { 0x008, 0x60FFE000u }, { 0x00C, 0 }, { 0x010, 0x60FFFFFFu }, { 0x014, 0x01000000u }, { 0x018, 0 }, { 0x000, 0x4u }, { 0x004, 0x80000000u } };
		static const struct { uint32_t off, v; } st[7] = { { 0x004, 0 }, { 0x000, 0 }, { 0x008, 0 }, { 0x00C, 0 }, { 0x010, 0xFFFu }, { 0x014, 0 }, { 0x018, 0 } };
		unsigned k = 0, nrow = 0, nR = 0, probe_row = 0, nprobe = 0, nwait = 0, iw = 0, wpos[16], perst_lo = 0, wrow[32];
		char b[96];

		world();
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)		/* the table rows of S26, in order */
			if (get_row(i, 0, &r) && r.step == 26) {
				EXPECT(nrow < 32);
				if (r.kind == WLK_MMIO_WRITE) {
					EXPECT(r.blk == WLB_ATU && r.mask == FULL_MASK_T && k < 7 && r.off == sw[k].off && r.val == sw[k].v);
					k++;
				} else if (r.kind == WLK_WAIT_EQ) {		/* review 7 finding 2: ONE-SHOT ATU CR2 == 0x80000000, after the CR2 read-back and before the CFG probe */
					EXPECT(r.blk == WLB_ATU && r.off == 4u && r.mask == FULL_MASK_T && r.val == 0x80000000u && r.a == 0u && r.b == 1u && k == 7 && !nprobe && !nwait);
					nwait++;
				} else {
					EXPECT(r.kind == WLK_READ_ONLY && (r.blk == WLB_ATU || r.blk == WLB_CFG));
					if (r.a == 1u)
						nprobe++, probe_row = i;
					else
						EXPECT(r.a == 2u);
				}
				wrow[nrow++] = i;
			}
		EXPECT(k == 7 && nrow == 20 && nprobe == 1 && nwait == 1);
		EXPECT(wlan_link_run() == 0 && has("LINK S26 ok rows=20") && !has("!!!") && (WLAN_LINK_N >= 28 || cfg_reads == 5u + CFG27) && cfg_reads_unconfigured == 0);	/* all CFG reads (S26: 5, S27: 56) after the region was enabled */
		for (unsigned i = 0; i < nev; i++)
			if (ev[i].t == EV_W && ev[i].pa >= WLAN_ATU && ev[i].pa < WLAN_ATU + 0x200) {		/* region 0 only (region 1 is S29's) */
				EXPECT(iw < 14);
				wpos[iw] = i;
				if (iw < 7)
					EXPECT(ev[i].pa == WLAN_ATU + sw[iw].off && ev[i].v == sw[iw].v);		/* Linux's order, enable last */
				else
					EXPECT(ev[i].pa == WLAN_ATU + st[iw - 7].off && ev[i].v == st[iw - 7].v);	/* disable first, then the reset values */
				iw++;
			}
		EXPECT(iw == 14);							/* no other ATU write; the CFG window is never written (the fake aborts a write outside every block) */
		for (unsigned i = 0; i < nev; i++)
			if (ev[i].t == EV_W && ev[i].pa == WLAN_GPH2 + 4 && !(ev[i].v & 1u) && i > wpos[6] && !perst_lo)
				perst_lo = i;
		EXPECT(perst_lo && wpos[7] < perst_lo && wpos[13] < perst_lo && wpos[13] > wpos[7]);	/* the ATU disable and restores all precede PERST low */
		for (unsigned i = 0; i < nrow; i++) {			/* the read-backs print what the previous write left; the CFG reads print the endpoint's words */
			(void)get_row(wrow[i], 0, &r);
			if (r.kind != WLK_READ_ONLY || r.a != 2u)
				continue;
			if (r.blk == WLB_ATU) {
				(void)get_row(wrow[i - 1], 0, &r);
				snprintf(b, sizeof b, "LINK R26 row=%u rd=0x%08X", wrow[i], (unsigned)r.val);
			} else {
				snprintf(b, sizeof b, "LINK R26 row=%u rd=0x%08X", wrow[i], (unsigned)cfgspace[r.off / 4]);
			}
			EXPECT(has(b));
		}
		EXPECT(has("rd=0x43DC14E4") && (cfgspace[0] & 0xFFFFu) == 0x14E4u);
		for (unsigned i = 0; i < nl; i++)
			nR += strstr(lines[i], "LINK R26 ") != NULL;
		EXPECT(nR == 11);
		teardown_tail_is_table_order(26);
		baseline_restored();
		/* the allowlist twin: exactly the 7 offsets with exactly the S26 / reset values, full-word writes only; no CFG address at all */
		{
			static const uint32_t vals[] = { 0, 1, 0x4u, 0x5u, 0x80000000u, 0x80000001u, 0x60FFE000u, 0x60FFE004u, 0x60FFFFFFu, 0x60FFFFFEu, 0x01000000u, 0x01000001u, 0xFFFu, 0x1000u, 0xFFFFFFFFu, 0x60000000u, 0x60000001u, 0x6000FFFFu, 0x6000FFFEu };

			for (uint32_t off = 0; off < 0x1000u; off += 4)
				for (unsigned j = 0; j < sizeof vals / sizeof vals[0]; j++) {
					int want_ok = 0;

					for (unsigned m = 0; m < 7; m++)
						want_ok |= off == sw[m].off && vals[j] == sw[m].v;
					for (unsigned m = 0; m < 7; m++)
						want_ok |= off == st[m].off && vals[j] == st[m].v;
					want_ok |= (off == 0x204u && vals[j] == 0u) || (off == 0x208u && (vals[j] == 0x60000000u || vals[j] == 0u)) || (off == 0x210u && (vals[j] == 0x6000FFFFu || vals[j] == 0xFFFu)) ||
						   (off == 0x214u && (vals[j] == 0x60000000u || vals[j] == 0u));		/* S29 region 1 (research 36 s.7): the S29 values and the resets; the ENABLE only with r1ok */
					EXPECT(wal(WLAN_ATU + off, FULL_MASK_T, 0, vals[j], 0) == want_ok);
					EXPECT(walc(WLAN_ATU + off, FULL_MASK_T, 0, vals[j], 0, 0xFFFFFFFFu, 1) == (want_ok || (off == 0x204u && vals[j] == 0x80000000u)));
					EXPECT(!wal(WLAN_ATU + off, 0x7FFFFFFFu, 0, vals[j] & 0x7FFFFFFFu, 0) && !wal(WLAN_ATU + off, 0, 0, 0, 0));	/* never a partial-mask or empty write */
				}
			for (uint32_t off = 0; off < 0x2000u; off += 4)
				for (unsigned j = 0; j < sizeof vals / sizeof vals[0]; j++)
					EXPECT(!wal(WLAN_CFG + off, FULL_MASK_T, 0, vals[j], 0) || (off >= 0x10 && off < 0x20) || (off == 0x04 && vals[j] == 0));	/* the BARs 0x10..0x1C are S28's, Command 0 and the window select are S29's (tested below); everything else in the window is never writable */
			EXPECT(!wal(WLAN_CFG - 4, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_ATU + 0x1000, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_ATU - 4, FULL_MASK_T, 0, 0, 0));
		}
		world(); unreadable_blk = 6;			/* the CFG probe faults: stop at that row, no CFG word printed, ATU torn down (CR2 first) back to reset, restore ok */
		snprintf(b, sizeof b, "LINK S26 fault row=%u", probe_row);
		EXPECT(wlan_link_run() == 1 && has(b) && has("stopped=fault at=S26") && has("restore=ok") && !has("!!!"));
		nR = 0;
		for (unsigned i = 0; i < nl; i++)
			nR += strstr(lines[i], "LINK R26 ") != NULL;
		EXPECT(nR == 7 && last_ev(EV_W, WLAN_ATU, WLAN_ATU + 0x1000) > 0);		/* only the 7 ATU read-backs */
		teardown_tail_is_table_order(26);
		baseline_restored();
		world(); ignore_pa_set = 1; ignore_pa = WLAN_ATU + 4;	/* the enable write does not take: the one-shot CR2 check REFUSES before any CFG access (review 7 finding 2), same teardown */
		EXPECT(wlan_link_run() == 1 && has("LINK S26 refused") && has("stopped=refused at=S26") && cfg_reads == 0u && cfg_reads_unconfigured == 0u && has("restore=ok") && !has("!!!"));
		teardown_tail_is_table_order(26);
		baseline_restored();
	}
#endif

#if WLAN_LINK_N == 27
	{	/* S27: READ-ONLY endpoint config dump: 56 CFG window dwords then ATU region 1 CR1..UPPER_TARGET (7), every row a logged read (a == 2, never a probe); not one write beyond S26's own */
		static const uint32_t cfg_offs[56] = { 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x34, 0x3C, 0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x60, 0x64, 0x68, 0x6C, 0x70, 0x74, 0x78, 0x7C,
						       0x80, 0x84, 0x88, 0xAC, 0xB0, 0xB4, 0xB8, 0xBC, 0xC0, 0xC4, 0xC8, 0xCC, 0xD0, 0xD4, 0xD8, 0xDC, 0xE0, 0xE4, 0xE8, 0xEC, 0xF0, 0xF4, 0xF8, 0xFC, 0x100, 0x1B4, 0x240, 0x244, 0x248, 0x24C };
		unsigned n27 = 0, nR = 0, wid[64];
		uint32_t want[64];
		char b[96];

		world();
		{	uint32_t v0;

			EXPECT(!r32(WLB_CFG, 0x1000u, &v0) && !r32(WLB_CFG, 0x1FFCu, &v0));	/* review 8 finding 4: no CFG read at or above 4 KiB */
		}
		for (unsigned i = 0; i < 56; i++)
			cfgspace[cfg_offs[i] / 4] = 0xC0DE0000u | cfg_offs[i];
		for (unsigned i = 0; i < 7; i++)
			atu[(0x200 + 4 * i) / 4] = 0xA7000000u | (0x200 + 4 * i);	/* region 1 "reset" words, distinguishable from region 0 */
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 27) {
				EXPECT(r.kind == WLK_READ_ONLY && r.a == 2u && r.mask == FULL_MASK_T && n27 < 63);
				EXPECT(n27 < 56 ? (r.blk == WLB_CFG && r.off == cfg_offs[n27]) : (r.blk == WLB_ATU && r.off == 0x200u + 4u * (n27 - 56u)));
				want[n27] = r.blk == WLB_CFG ? cfgspace[r.off / 4] : atu[r.off / 4];
				wid[n27++] = i;
			}
		EXPECT(n27 == 63);
		for (unsigned i = 0; i < n27; i++) {			/* nothing in S27 can be written: the writer refuses every one of its rows */
			EXPECT(w32(wid[i], 0) == -2);
			EXPECT(nwrites == 0);
		}
		EXPECT(wlan_link_run() == 0 && has("LINK S27 ok rows=63") && !has("!!!") && cfg_reads == 5u + 56u && cfg_reads_unconfigured == 0);
		for (unsigned j = 0; j < n27; j++) {
			snprintf(b, sizeof b, "LINK R27 row=%u rd=0x%08X", wid[j], (unsigned)want[j]);
			EXPECT(has(b));
		}
		for (unsigned i = 0; i < nl; i++)
			nR += strstr(lines[i], "LINK R27 ") != NULL;
		EXPECT(nR == n27);
		/* S27 adds no write: the ATU writes are S26's 7 + the 7 teardown rows, none at region 1 (+0x200..), none to the CFG window */
		EXPECT(last_ev(EV_W, WLAN_ATU + 0x200, WLAN_ATU + 0x1000) == 0 && last_ev(EV_W, WLAN_CFG, WLAN_CFG + 0x2000) == 0);
		for (unsigned i = 0, nat = 0; i < nev; i++)
			if (ev[i].t == EV_W && ev[i].pa >= WLAN_ATU && ev[i].pa < WLAN_ATU + 0x1000)
				EXPECT(++nat <= 14);
		EXPECT(atu[0x204 / 4] == 0xA7000204u && cfgspace[0x100 / 4] == 0xC0DE0100u);	/* the fake's region 1 / config words are untouched */
		teardown_tail_is_table_order(WLAN_LINK_N);
		baseline_restored();
		world(); fault_read_pa = WLAN_CFG + 0x100;		/* an unreadable config dword stops S27 at that row: the rows before it printed, the ones after did not, ATU torn down (CR2 first), restore ok */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S27") && has("restore=ok") && !has("!!!"));
		snprintf(b, sizeof b, "LINK R27 row=%u ", wid[49]);	/* 0xFC, the dword before 0x100 */
		EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R27 row=%u ", wid[52]);	/* 0x240, after it */
		EXPECT(!has(b));
		teardown_tail_is_table_order(27);
		baseline_restored();
		world(); unreadable_blk = 5;				/* the ATU window unreadable: S25 already stops at its probe, S27 never starts */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S25") && !has("LINK R27 "));
		baseline_restored();
	}
#endif

#if WLAN_LINK_N >= 28
	/* ---- S28: BAR0/BAR2 sizing + assignment through the CFG window (research 33), the first config WRITES ---- */
	{
		static const uint32_t bars_orig[4] = { 4u, 0u, 4u, 0u };
		struct wlan_row r28;
		unsigned n28 = 0, nw28 = 0, nR28 = 0;

		/* the independent check, state-free corners: offsets 0x10/0x14/0x18/0x1C only (never Command 0x04), full-word, per-offset values, transitions, Command decode off */
		for (uint32_t off = 0; off < 0x40u; off += 4) {
			int bar = off >= 0x10u && off < 0x20u;

			EXPECT(wal(WLAN_CFG + off, FULL_MASK_T, 0xFFFFFFFFu, bar ? bars_orig[(off - 0x10u) / 4] : 0u, 0xFFFFu) == (bar || off == 0x04u));		/* the restore passes even with Command != 0 and any old value (Command := 0 is S29's restore) */
			EXPECT(!wal(WLAN_CFG + off, FULL_MASK_T, bar ? bars_orig[(off - 0x10u) / 4] : 0u, 0xFFFFFFFFu, 2u) && !wal(WLAN_CFG + off, 0xFFFFFFF0u, 4u, 0xFFFFFFFFu, 0));	/* Command decode on, or a partial mask: never */
			EXPECT(wal(WLAN_CFG + off, FULL_MASK_T, bar ? bars_orig[(off - 0x10u) / 4] : 0u, 0xFFFFFFFFu, 0) == bar);	/* sizing from the original, Command 0 */
			EXPECT(!wal(WLAN_CFG + off, FULL_MASK_T, 0x12345678u, 0xFFFFFFFFu, 0));						/* sizing only from the original (not from some other state) */
		}
		EXPECT(wal(WLAN_CFG + 0x04, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_CFG + 0x04, FULL_MASK_T, 0, 2u, 0) && !wal(WLAN_CFG + 0x20, FULL_MASK_T, 0, 0, 0) && !wal(WLAN_CFG + 0x80, FULL_MASK_T, 0, 0x18000000u, 0));	/* Command takes only 0 without the S29 state; the window select only from 0x18001000 */
		EXPECT(wal(WLAN_CFG + 0x10, FULL_MASK_T, 4u, 0x60000004u, 0) && wal(WLAN_CFG + 0x18, FULL_MASK_T, 4u, 0x60400004u, 0));	/* the two assignments, only from the original */
		EXPECT(!wal(WLAN_CFG + 0x10, FULL_MASK_T, 4u, 0x60400004u, 0) && !wal(WLAN_CFG + 0x18, FULL_MASK_T, 4u, 0x60000004u, 0) && !wal(WLAN_CFG + 0x10, FULL_MASK_T, 4u, 0x60000000u, 0));
		EXPECT(!wal(WLAN_CFG + 0x10, FULL_MASK_T, 0xFFFF8004u, 0x60000004u, 0) && !wal(WLAN_CFG + 0x10, FULL_MASK_T, 4u, 0x60000004u, 1u));
		EXPECT(!wal(WLAN_CFG + 0x14, FULL_MASK_T, 0, 0x60000000u, 0) && !wal(WLAN_CFG + 0x1C, FULL_MASK_T, 0, 1u, 0));	/* the high halves take only all ones and 0 */
		EXPECT(!wal(WLAN_CFG + 0x1000, FULL_MASK_T, 0, 0, 0));
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r28) && r28.step == 28) {
				n28++;
				nw28 += r28.kind == WLK_MMIO_WRITE;
				EXPECT(r28.blk == WLB_CFG);
			}
		EXPECT(n28 == 27 && nw28 == 10);

		world();							/* the good run: BAR0 64 KiB (run w3a-n28c), BAR2 4 MiB */
		EXPECT(wlan_link_run() == 0 && has("LINK S28 ok rows=27") && !has("!!!") && has("restore=ok"));
		for (unsigned i = 0; i < nl; i++)
			nR28 += strstr(lines[i], "LINK R28 ") != NULL;
		EXPECT(nR28 == 4);									/* per BAR: low + high sizing logs, low + high assignment logs */
		{	static const char *const want[] = { "rd=0xFFFF0004", "rd=0xFFFFFFFF", "rd=0xFFC00004", "rd=0xFFFFFFFF" };
			unsigned k = 0;

			for (unsigned i = 0; i < nl && k < 4; i++)
				if (strstr(lines[i], "LINK R28 ") && strstr(lines[i], want[k]))
					k++;
			EXPECT(k == 4);
		}
		EXPECT(cfg_writes_unconfigured == 0 && (WLAN_LINK_N >= 29 || last_ev(EV_W, WLAN_CFG + 4, WLAN_CFG + 8) == 0));		/* S28 never writes Command (S29 does, tested below); every CFG write went through the enabled region 0 */
		for (unsigned j = 0; j < 4; j++)
			EXPECT(cfgspace[4 + j] == bars_orig[j]);						/* the teardown restored the originals */
#if WLAN_LINK_N == 28
		{	static const struct { uint32_t o, v; } wseq[] = { { 0x10, 0xFFFFFFFF }, { 0x10, 4 }, { 0x14, 0xFFFFFFFF }, { 0x14, 0 }, { 0x18, 0xFFFFFFFF }, { 0x18, 4 }, { 0x1C, 0xFFFFFFFF }, { 0x1C, 0 }, { 0x10, 0x60000004 }, { 0x18, 0x60400004 },
				{ 0x10, 4 }, { 0x14, 0 }, { 0x18, 4 }, { 0x1C, 0 } };
			unsigned k = 0;

			for (unsigned i = 0; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa >= WLAN_CFG && ev[i].pa < WLAN_CFG + 0x2000) {
					EXPECT(k < 14 && ev[i].pa == WLAN_CFG + wseq[k].o && ev[i].v == wseq[k].v);
					k++;
				}
			EXPECT(k == 14);
		}
#endif
		teardown_tail_is_table_order(WLAN_LINK_N);
		baseline_restored();

		world(); bar_sz[1] = 0x800000u;				/* BAR2 8 MiB: the size guard (bits [31:22] all ones) refuses, BAR2 low is restored, nothing is assigned */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S28") && has("restore=ok") && !has("!!!"));
		for (unsigned j = 0; j < 4; j++)
			EXPECT(cfgspace[4 + j] == bars_orig[j]);
		EXPECT(!wrote(WLAN_CFG + 0x10, 0xFFFFFFFFu, 0x60000004u) && cfg_writes_unconfigured == 0);
		for (unsigned i = 0; i < nev; i++)
			EXPECT(!(ev[i].t == EV_W && ev[i].pa >= WLAN_CFG && ev[i].pa < WLAN_CFG + 0x2000 && (ev[i].v == 0x60000004u || ev[i].v == 0x60400004u)));
		teardown_tail_is_table_order(28);
		baseline_restored();

		world(); cfgspace[1] |= 2u;				/* Command.MEM already set: the precondition refuses BEFORE any BAR write (only the teardown restores follow) */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S28 row=") && has("restore=ok") && !has("!!!"));
		for (unsigned i = 0; i < nev; i++)
			EXPECT(!(ev[i].t == EV_W && ev[i].pa >= WLAN_CFG && ev[i].pa < WLAN_CFG + 0x2000 && ev[i].v != bars_orig[(ev[i].pa - WLAN_CFG - 0x10) / 4 & 3u]));
		baseline_restored();

		world(); cfgspace[4] = 0x60000004u;			/* BAR0 not at its observed original: refused before any write */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S28") && !has("!!!"));
		baseline_restored();

		baseline_restored();
	}
#endif

#if WLAN_LINK_N >= 29
	/* ---- S29 (research 36): iATU outbound region 1 over BAR0, PCI_BAR0_WIN := ChipCommon, Command := MEM only; nothing reads BAR0 yet (S30) ---- */
	{
		static const struct { unsigned blk; uint32_t off, v; } w29[6] = { { WLB_ATU, 0x208, 0x60000000u }, { WLB_ATU, 0x210, 0x6000FFFFu }, { WLB_ATU, 0x214, 0x60000000u }, { WLB_ATU, 0x204, 0x80000000u },
										   { WLB_CFG, 0x80, 0x18000000u }, { WLB_CFG, 0x04, 0x2u } };
		static const uint32_t vals[] = { 0, 1, 2, 4, 6, 0x10000u, 0x00100002u, 0x18000000u, 0x18001000u, 0x18002000u, 0x18105000u, 0x60000004u, 0xFFFFFFFFu };
		unsigned nw = 0, n29 = 0, nlog = 0, lg[8], k;
		char b[96];

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 29) {
				n29++;
				if (r.kind == WLK_MMIO_WRITE) {
					EXPECT(nw < 6 && r.blk == w29[nw].blk && r.off == w29[nw].off && r.val == w29[nw].v && r.mask == FULL_MASK_T);
					nw++;
				}
				if (r.kind == WLK_READ_ONLY) {
					EXPECT(r.a == 2u && r.blk == WLB_CFG && nlog < 8);
					lg[nlog++] = i;
				}
				EXPECT(r.blk != WLB_MEM);			/* S29 never touches BAR0 */
			}
		EXPECT(n29 == 25 && nw == 6 && nlog == 3);
		/* the stateful allowlist, exhaustively over small state spaces: Command takes 0 from anywhere, 0x2 only from Command low 0 AND BAR0 low at the S28 assignment; never MASTER, never status bits */
		for (unsigned oi = 0; oi < sizeof vals / sizeof vals[0]; oi++)
			for (unsigned vi = 0; vi < sizeof vals / sizeof vals[0]; vi++)
				for (unsigned bi = 0; bi < sizeof vals / sizeof vals[0]; bi++) {
					uint32_t old = vals[oi], v = vals[vi], bar0 = vals[bi];

					EXPECT(walc(WLAN_CFG + 0x04, FULL_MASK_T, old, v, 0xFFFFu, bar0, 1) == (v == 0u || (v == 2u && (old & 0xFFFFu) == 0u && bar0 == 0x60000004u)));
					EXPECT(walc(WLAN_CFG + 0x80, FULL_MASK_T, old, v, 0xFFFFu, bar0, 1) == (v == 0x18000000u && old == 0x18001000u));	/* forward rows, Command not MEM-only: the S32 move needs Command 0x2, and 0x18001000 (S34 core page, or the teardown original) is no longer a free value in a forward row */
					EXPECT(!walc(WLAN_CFG + 0x88, FULL_MASK_T, old, v, 0, bar0, 1) && !walc(WLAN_CFG + 0x84, FULL_MASK_T, old, v, 0, bar0, 1) && !walc(WLAN_CFG + 0x70, FULL_MASK_T, old, v, 0, bar0, 1));
					EXPECT(!walc(WLAN_CFG + 0x04, 0xFFFFu, old, (old & 0xFFFF0000u) | (v & 0xFFFFu), 0, 0x60000004u, 1) || (v & 0xFFFFu) == 0u);	/* a partial-mask Command write is never allowed (v is only 0 below the mask) */
				}
		EXPECT(!walc(WLAN_CFG + 0x04, 0xFFFFu, 0, 2u, 0, 0x60000004u, 1) && !walc(WLAN_CFG + 0x80, 0xFFFFu, 0x18001000u, 0x18000000u, 0, 0, 1));
		/* the region 1 enable needs base/limit/target at the S29 values (the ctx_read result); 0x200/0x20C/0x218 and anything above are never writable */
		EXPECT(walc(WLAN_ATU + 0x204, FULL_MASK_T, 0, 0x80000000u, 0, 0, 1) && !walc(WLAN_ATU + 0x204, FULL_MASK_T, 0, 0x80000000u, 0, 0, 0) && walc(WLAN_ATU + 0x204, FULL_MASK_T, 0x80000000u, 0, 0, 0, 0));
		for (uint32_t off = 0x200; off < 0x1000u; off += 4)
			for (unsigned vi = 0; vi < sizeof vals / sizeof vals[0]; vi++)
				if (off == 0x200u || off == 0x20Cu || off >= 0x218u)
					EXPECT(!walc(WLAN_ATU + off, FULL_MASK_T, 0, vals[vi], 0, 0, 1));
		/* BAR0 (MEM) is read-only: never writable at any offset, mask or state; offset 0 only for reads */
		for (uint32_t off = 0; off <= 0x1000u; off += 4)
			for (unsigned vi = 0; vi < sizeof vals / sizeof vals[0]; vi++)
				EXPECT(!walc(WLAN_MEM + off, FULL_MASK_T, 0, vals[vi], 2u, 0x60000004u, 1) && !walc(WLAN_MEM + off, 0xFFu, 0, vals[vi] & 0xFFu, 2u, 0x60000004u, 1));
		world();
		{	uint32_t v = 0;

			EXPECT(pa_of(WLB_MEM, 0) == WLAN_MEM && pa_of(WLB_MEM, 4) == WLAN_MEM + 4 && pa_of(WLB_MEM, 0x1000) == 0 && !r32(WLB_MEM, 4, &v) && !r32(WLB_MEM, 0x1000, &v) && !r32(WLB_MEM, 0xFFC, &v) && mem_reads == 0);
			/* r32 refuses BAR0 itself (no TLP) unless region 1 reads enabled and Command reads exactly MEM */
			atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u;
			cfgspace[4] = 0x60000004u; cfgspace[0x80 / 4] = 0x18000000u;
			atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60000000u;
			cfgspace[1] = 0x00100002u;
			EXPECT(!r32(WLB_MEM, 0, &v) && mem_reads == 0);						/* region 1 not enabled (Command is MEM) */
			atu[0x204 / 4] = 0x80000000u;
			cfgspace[1] = 0x00100000u;
			EXPECT(!r32(WLB_MEM, 0, &v) && mem_reads == 0);						/* Command 0 (region 1 enabled) */
			cfgspace[1] = 0x00100006u;
			EXPECT(!r32(WLB_MEM, 0, &v) && mem_reads == 0);						/* MASTER also set: not the S29 state */
			cfgspace[1] = 0x00100003u;
			EXPECT(!r32(WLB_MEM, 0, &v) && mem_reads == 0);
			cfgspace[1] = 0x00100002u;
			EXPECT(r32(WLB_MEM, 0, &v) && v == 0x10034389u && mem_reads == 1 && mem_reads_unready == 0);
			EXPECT(r32(WLB_MEM, 0xFC, &v) && v == 0x18105000u && r32(WLB_MEM, 0x1E0, &v) && v == 0x00050008u && r32(WLB_MEM, 0xAC, &v) && v == 0x40u && mem_reads == 4 && mem_reads_unready == 0);	/* S31 run A: exactly these three more */
			EXPECT(!r32(WLB_MEM, 4, &v) && !r32(WLB_MEM, 0x800, &v) && !r32(WLB_MEM, 0xFFC, &v) && !r32(WLB_MEM, 0x1000, &v) && mem_reads == 4);	/* ready, but only offsets 0 / 0xAC / 0xFC / 0x1E0 are ever read */
			for (uint32_t o = 0; o <= 0x1004u; o += 4)							/* every other dword refused, no TLP */
				if (o != 0u && o != 0xACu && o != 0xFCu && o != 0x1E0u)
					EXPECT(!r32(WLB_MEM, o, &v));
			EXPECT(!r32(WLB_MEM, 0xFD, &v) && !r32(WLB_MEM, 0x1E1, &v) && !r32(WLB_MEM, 0xAE, &v) && mem_reads == 4);	/* unaligned: no TLP */
			atu[0x204 / 4] = 0u;
			EXPECT(!r32(WLB_MEM, 0, &v) && !r32(WLB_MEM, 0xFC, &v) && mem_reads == 4);
			atu[0x204 / 4] = 0x80000001u;
			EXPECT(!r32(WLB_MEM, 0, &v) && !r32(WLB_MEM, 0x1E0, &v) && mem_reads == 4);
			atu[0x204 / 4] = 0x80000000u; fault_read_pa = WLAN_CFG + 4;
			EXPECT(!r32(WLB_MEM, 0, &v) && !r32(WLB_MEM, 0xAC, &v) && mem_reads == 4);						/* an unreadable Command is "not ready" */
			EXPECT(nwrites == 0);
		}
		{	/* review 10 M1: the pre-allowlist refusal (no TLP before it); research 40: BAR0 passes w32_blk_ok since S34 and is decided by memw_ok, a MEM row in a world that is not ready (no region 1, no Command) is still refused with no BAR0 TLP */
			world();
			for (uint32_t o = 0; o < 0x1000u; o += 4)
				EXPECT(w32_blk_ok(WLB_MEM, o));
			for (uint32_t o = 0x1000u; o < 0x2000u; o += 4)
				EXPECT(!w32_blk_ok(WLB_CFG, o));
			EXPECT(w32_blk_ok(WLB_CFG, 0xFFCu) && w32_blk_ok(WLB_ATU, 0x204u) && w32_blk_ok(WLB_PMA, 0));
			for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
				if (get_row(i, 0, &r) && r.blk == WLB_MEM)
					EXPECT(w32(i, 0) == -2);
			EXPECT(mem_reads == 0 && nwrites == 0);
		}
		{	/* review 10 L1: the MEM guard re-checks the window itself, each register wrong with CR2 enabled and Command MEM = no read */
			uint32_t v = 0;
			static const struct { uint32_t o, bad; } bad[4] = { { 0x200, 4u }, { 0x208, 0x60000004u }, { 0x210, 0x6001FFFFu }, { 0x214, 0x60400000u } };

			for (unsigned j = 0; j < 4; j++) {
				world();
				atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u;
				cfgspace[4] = 0x60000004u; cfgspace[0x80 / 4] = 0x18000000u; cfgspace[1] = 0x00100002u;
				atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60000000u; atu[0x204 / 4] = 0x80000000u;
				EXPECT(r32(WLB_MEM, 0, &v) && mem_reads == 1);
				atu[bad[j].o / 4] = bad[j].bad;
				EXPECT(!r32(WLB_MEM, 0, &v) && mem_reads == 1);
			}
		}
		{	/* review 10 L4: ctx_read touches ATU / CFG only for its own blocks (a read of the DBI-ATU window before the controller is up is the research 30 hang class) */
			struct w32_ctx c;

			world();
			ctx_read(WLB_PMA, &c); ctx_read(WLB_GPP14, &c); ctx_read(WLB_GPH2, &c); ctx_read(WLB_ELBI, &c); ctx_read(WLB_DBI, &c); ctx_read(WLB_MEM, &c); ctx_read(WLB_NONE, &c);
			EXPECT(atu_reads == 0 && cfg_dev_reads == 0);
			ctx_read(WLB_CFG, &c);
			EXPECT(atu_reads == 0 && cfg_dev_reads == 2);
			ctx_read(WLB_ATU, &c);
			EXPECT(atu_reads == 3 && cfg_dev_reads == 2);
		}
		{	/* ctx_read: an unreadable word is "not ready" (never the value a rule wants), region 1 is ready only with base AND limit AND target */
			struct w32_ctx c;

			world();
			cfgspace[4] = 0x60000004u; cfgspace[1] = 0x00100000u;
			atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u;
			ctx_read(WLB_CFG, &c);
			EXPECT(c.cmd == 0x00100000u && c.bar0 == 0x60000004u && !c.r1ok);
			fault_read_pa = WLAN_CFG + 4;
			ctx_read(WLB_CFG, &c);
			EXPECT((c.cmd & 0xFFFFu) != 0u && c.bar0 == 0x60000004u);
			fault_read_pa = WLAN_CFG + 0x10;
			ctx_read(WLB_CFG, &c);
			EXPECT(c.bar0 != 0x60000004u && c.cmd == 0x00100000u);
			fault_read_pa = 0;
			ctx_read(WLB_PMA, &c);
			EXPECT(!c.r1ok && (c.cmd & 0xFFFFu) != 0u);					/* other blocks read nothing */
			atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60000000u;
			ctx_read(WLB_ATU, &c);
			EXPECT(c.r1ok);
			atu[0x208 / 4] = 0x60000004u; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
			atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x601FFFFFu; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
			atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60400000u; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
			atu[0x214 / 4] = 0x60000000u; fault_read_pa = WLAN_ATU + 0x210; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
			fault_read_pa = 0; ctx_read(WLB_ATU, &c); EXPECT(c.r1ok);
			{	/* review 10 L2: CR1 (TYPE), UPPER_BASE and UPPER_TARGET must read 0 too; an unreadable one is "not ready" */
				static const uint32_t o2[3] = { 0x200u, 0x20Cu, 0x218u };

				for (unsigned j = 0; j < 3; j++) {
					atu[o2[j] / 4] = 4u; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
					atu[o2[j] / 4] = 0u; fault_read_pa = WLAN_ATU + o2[j]; ctx_read(WLB_ATU, &c); EXPECT(!c.r1ok);
					fault_read_pa = 0; ctx_read(WLB_ATU, &c); EXPECT(c.r1ok);
				}
			}
		}
		{	/* the writer itself against the live state (not only w32_allowed): the enable only with base / limit / target, the window select only from its original, the arm only from Command 0 with BAR0 assigned */
			unsigned i_cr2 = 0, i_cmd = 0, i_win = 0;

			for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
				if (get_row(i, 0, &r) && r.step == 29 && r.kind == WLK_MMIO_WRITE) {
					if (r.blk == WLB_ATU && r.off == 0x204u) i_cr2 = i;
					if (r.blk == WLB_CFG && r.off == 0x04u) i_cmd = i;
					if (r.blk == WLB_CFG && r.off == 0x80u) i_win = i;
				}
			EXPECT(i_cr2 && i_cmd && i_win);
			world();
			atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u;
			EXPECT(w32(i_cr2, 0) == -2 && atu[0x204 / 4] == 0 && nwrites == 0);			/* nothing programmed yet */
			atu[0x208 / 4] = 0x60000000u; atu[0x214 / 4] = 0x60000000u;
			EXPECT(w32(i_cr2, 0) == -2);								/* limit still at its reset */
			atu[0x210 / 4] = 0x6000FFFFu; atu[0x208 / 4] = 0x60000001u;
			EXPECT(w32(i_cr2, 0) == -2);								/* base wrong */
			atu[0x208 / 4] = 0x60000000u; atu[0x214 / 4] = 0u;
			EXPECT(w32(i_cr2, 0) == -2 && nwrites == 0);						/* target wrong */
			atu[0x214 / 4] = 0x60000000u;
			atu[0x200 / 4] = 4u;
			EXPECT(w32(i_cr2, 0) == -2 && atu[0x204 / 4] == 0 && nwrites == 0);						/* review 10 L2: CR1 not MEM */
			atu[0x200 / 4] = 0u; atu[0x20C / 4] = 1u;
			EXPECT(w32(i_cr2, 0) == -2 && nwrites == 0);									/* UPPER_BASE not 0 */
			atu[0x20C / 4] = 0u; atu[0x218 / 4] = 1u;
			EXPECT(w32(i_cr2, 0) == -2 && nwrites == 0);									/* UPPER_TARGET not 0 */
			atu[0x218 / 4] = 0u;
			EXPECT(w32(i_cr2, 0) == 0 && atu[0x204 / 4] == 0x80000000u && nwrites == 1);
			cfgspace[0x80 / 4] = 0x18000000u;
			EXPECT(w32(i_win, 0) == -2);								/* not from the observed original */
			cfgspace[0x80 / 4] = 0u;
			EXPECT(w32(i_win, 0) == -2);
			cfgspace[0x80 / 4] = 0x18001000u;
			EXPECT(w32(i_win, 0) == 0 && cfgspace[0x80 / 4] == 0x18000000u);
			cfgspace[1] = 0x00100000u; cfgspace[4] = 0x4u;
			EXPECT(w32(i_cmd, 0) == -2 && cfgspace[1] == 0x00100000u);				/* BAR0 not assigned */
			cfgspace[4] = 0x60000004u; cfgspace[1] = 0x00100001u;
			EXPECT(w32(i_cmd, 0) == -2);								/* Command already holds a bit */
			cfgspace[1] = 0x00100000u; fault_read_pa = WLAN_CFG + 0x10;
			EXPECT(w32(i_cmd, 0) == -2);								/* BAR0 unreadable */
			fault_read_pa = 0;
			EXPECT(w32(i_cmd, 0) == 0 && (cfgspace[1] & 0xFFFFu) == 2u && master_writes == 0);
			EXPECT(nwrites == 3);
		}

		world();
		EXPECT(wlan_link_run() == 0 && has("LINK S29 ok rows=25") && !has("!!!") && has("restore=ok") && cfg_writes_unconfigured == 0 && master_writes == 0);
#if WLAN_LINK_N == 29
		EXPECT(mem_reads == 0 && !has("LINK S30"));						/* S29 alone never reads BAR0 */
#endif
		EXPECT(WLAN_LINK_N >= 34 || last_ev(EV_W, WLAN_MEM, WLAN_MEM + 0x1000) == 0);	/* BAR0 is never written before S34 (research 40: the first BAR0 writes) */
		k = 0;
		for (unsigned j = 0; j < 4; j++) {							/* the forward order: base, limit, target, ENABLE, window select, Command */
			unsigned e = first_ev(WLAN_ATU + w29[j].off, w29[j].v);

			EXPECT(e && e > k);
			k = e;
		}
		EXPECT(first_ev(WLAN_CFG + 0x80, 0x18000000u) > k && first_ev(WLAN_CFG + 4, 2u) > first_ev(WLAN_CFG + 0x80, 0x18000000u));
		EXPECT(count_w(WLAN_ATU + 0x200, WLAN_ATU + 0x1000) == 8 && count_w(WLAN_CFG + 4, WLAN_CFG + 8) == 2 && count_w(WLAN_CFG + 0x80, WLAN_CFG + 0x84) == (WLAN_LINK_N >= 36 ? 11u : WLAN_LINK_N >= 35 ? 10u : WLAN_LINK_N >= 34 ? 8u : WLAN_LINK_N >= 33 ? 7u : WLAN_LINK_N >= 32 ? 4u : 2u));	/* 4 forward + 4 teardown; Command, window: write + restore (S32: the EROM page move + its restore too; S33: four more moves) */
		EXPECT(first_ev(WLAN_CFG + 4, 0u) > first_ev(WLAN_CFG + 4, 2u) && first_ev(WLAN_ATU + 0x204, 0u) > first_ev(WLAN_ATU + 0x204, 0x80000000u));
		EXPECT(count_w(WLAN_CFG + 8, WLAN_CFG + 0x10) == 0 && count_w(WLAN_CFG + 0x20, WLAN_CFG + 0x80) == 0 && count_w(WLAN_CFG + 0x84, WLAN_CFG + 0x2000) == 0);	/* nothing else in the window (bar the BARs) */
		for (unsigned j = 0; j < nlog; j++) {
			(void)get_row(lg[j], 0, &r);
			snprintf(b, sizeof b, "LINK R29 row=%u rd=0x%08X", lg[j], (unsigned)(r.off == 0x88 ? 0x00080080u : r.off == 0xBC ? 0x10110000u : 0u));
			EXPECT(has(b));
		}
		teardown_tail_is_table_order(WLAN_LINK_N);
		baseline_restored();
		/* review 10 L3: the stop conditions are enforced: a changed PCI_SPROM_CONTROL (0x88, all ones = DHD aborts attach) or LNKSTA (0xBC high half) stops BEFORE the Command arm */
		world(); cfgspace[0x88 / 4] = 0xFFFFFFFFu;
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && !has("!!!") && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && mem_reads == 0);
		teardown_tail_is_table_order(29);
		baseline_restored();
		world(); cfgspace[0xBC / 4] = 0x00110000u;
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && !has("!!!") && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && mem_reads == 0);
		teardown_tail_is_table_order(29);
		baseline_restored();
		/* each latch check REFUSES before the next stage: the Command is never armed unless region 1, window and (own read-back) all latched */
		world(); cfgspace[0x80 / 4] = 0x18000000u;						/* the window select is not at its observed original: refused in the preconditions, no region 1 write at all */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && !has("!!!") && !wrote(WLAN_ATU + 0x208, FULL_MASK_T, 0x60000000u) && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && mem_reads == 0);
		teardown_tail_is_table_order(29);
		baseline_restored();
		world(); ignore_pa_set = 1; ignore_pa = WLAN_ATU + 0x204;				/* the region 1 enable does not take */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u) && mem_reads == 0);
		baseline_restored();
		world(); ignore_pa_set = 1; ignore_pa = WLAN_CFG + 0x80;				/* the window select does not take (the fake write is swallowed) */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && mem_reads == 0);
		baseline_restored();
		world(); ignore_pa_set = 1; ignore_pa = WLAN_CFG + 4;					/* the Command does not take: refused at the read-back, BAR0 never read */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S29") && has("restore=ok") && mem_reads == 0 && !has("LINK S30"));
		baseline_restored();
		world(); fault_read_pa = WLAN_CFG + 0x104;					/* an unreadable AER word (read only in S29) stops S29 (fault) before the arm */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S29") && !wrote(WLAN_CFG + 4, 0xFFFFu, 2u) && mem_reads == 0);
		baseline_restored();
	}
#endif

#if WLAN_LINK_N == 30
	/* ---- S30 (research 36): the first BAR0 read, behind a fake of region 1 / window / Command; read-only, offset 0 only ---- */
	{
		unsigned n30 = 0, probe_row = 0, wait_row = 0, logrow = 0;
		char b[96];

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 30) {
				EXPECT(r.kind == WLK_READ_ONLY || r.kind == WLK_WAIT_EQ);
				if (r.blk == WLB_MEM) {
					EXPECT(r.off == 0u && (r.kind == WLK_READ_ONLY || (r.kind == WLK_WAIT_EQ && r.mask == 0xFFFFu && r.val == 0x4389u && r.b == 1u && r.a == 0u)));
					if (r.kind == WLK_READ_ONLY && r.a == 1u)
						probe_row = i;
					else if (r.kind == WLK_READ_ONLY)
						logrow = i;
					else
						wait_row = i;
				} else {
					EXPECT(r.blk == WLB_CFG && r.kind == WLK_READ_ONLY && r.a == 2u && (r.off == 0xBC || r.off == 0x104));
				}
				n30++;
			}
		EXPECT(n30 == 5 && probe_row && logrow == probe_row + 1 && wait_row > logrow);
		world();
		EXPECT(wlan_link_run() == 0 && has("LINK S30 ok rows=5") && has("LINK summary reached=S30 stopped=none restore=ok") && !has("!!!"));
		snprintf(b, sizeof b, "LINK R30 row=%u rd=0x10034389", logrow);
		EXPECT(has(b) && mem_reads == 3 && mem_reads_unready == 0 && last_ev(EV_W, WLAN_MEM, WLAN_MEM + 0x1000) == 0 && master_writes == 0);	/* probe + log + one-shot id check, all behind the armed state */
		EXPECT(!mem_ready());									/* the teardown disarmed everything */
		teardown_tail_is_table_order(30);
		baseline_restored();
		world(); mem_mode = 1;									/* UR / completer abort answered with all ones, no fault: the one-shot id check REFUSES */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S30") && has("restore=ok") && !has("!!!") && mem_reads == 3 && master_writes == 0);
		snprintf(b, sizeof b, "LINK R30 row=%u rd=0xFFFFFFFF", logrow);
		EXPECT(has(b) && !mem_ready());
		teardown_tail_is_table_order(30);
		baseline_restored();
		world(); mem_mode = 2;									/* the read faults: stop at the probe row, nothing logged from BAR0, teardown unchanged */
		snprintf(b, sizeof b, "LINK S30 fault row=%u", probe_row);
		EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && mem_reads == 1 && !has("LINK R30 row="));
		teardown_tail_is_table_order(30);
		baseline_restored();
		world(); mem_mode = 3;									/* another chip id: refused */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S30") && has("restore=ok") && mem_reads == 3);
		teardown_tail_is_table_order(30);
		baseline_restored();
		world(); unreadable_blk = 7;								/* the MEM window unreadable (bus abort): fault at the probe */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S30") && has("restore=ok") && !has("LINK R30 row="));
		baseline_restored();
	}
#endif

#if WLAN_LINK_N == 31
	/* ---- S31 (research 37 s.9.1 run A): READ-ONLY ChipCommon reads eromptr 0xFC / clk_ctl_st 0x1E0 / capabilities_ext 0xAC; the window stays at 0x18000000, no write of any kind ---- */
	{
		unsigned n31 = 0, row_erom = 0, row_clk = 0, row_cap = 0, row_chk = 0;
		char b[96];

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 31) {
				EXPECT(r.kind == WLK_READ_ONLY || r.kind == WLK_WAIT_EQ);			/* no write kind in S31, by construction */
				if (r.blk == WLB_MEM && r.kind == WLK_READ_ONLY) {
					EXPECT(r.a == 2u && (r.off == 0xFCu || r.off == 0x1E0u || r.off == 0xACu));	/* logged, never a probe */
					if (r.off == 0xFCu) row_erom = i;
					if (r.off == 0x1E0u) row_clk = i;
					if (r.off == 0xACu) row_cap = i;
				} else if (r.blk == WLB_MEM) {
					EXPECT(r.kind == WLK_WAIT_EQ && r.b == 1u && r.a == 0u && ((r.off == 0xFCu && r.mask == 0xF8000FFFu && r.val == 0x18000000u) || (r.off == 0u && r.mask == 0xFFFFFFFFu && r.val == 0x10034389u)));
					if (r.off == 0xFCu) row_chk = i;
				} else {
					EXPECT(r.blk == WLB_CFG && ((r.kind == WLK_READ_ONLY && r.a == 2u && (r.off == 0xBC || r.off == 0x104)) || (r.kind == WLK_WAIT_EQ && r.off == 0x80u && r.mask == 0xFFFFFFFFu && r.val == 0x18000000u)));
				}
				n31++;
			}
		EXPECT(n31 == 8 && row_erom && row_clk && row_cap && row_chk > row_erom);
		world();
		EXPECT(wlan_link_run() == 0 && has("LINK S31 ok rows=8") && has("LINK summary reached=S31 stopped=none restore=ok") && !has("!!!"));
		snprintf(b, sizeof b, "LINK R31 row=%u rd=0x18105000", row_erom); EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R31 row=%u rd=0x00050008", row_clk); EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R31 row=%u rd=0x00000040", row_cap); EXPECT(has(b));
		EXPECT(mem_reads == 8 && mem_reads_unready == 0 && last_ev(EV_W, WLAN_MEM, WLAN_MEM + 0x1000) == 0 && master_writes == 0);	/* S30: 3, S31: 3 logs + 2 checks; BAR0 never written */
		{	unsigned nwin = 0;
			for (unsigned i = 0; i < nev; i++)
				nwin += ev[i].t == EV_W && ev[i].pa == WLAN_CFG + 0x80;
			EXPECT(nwin == 2 && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u) && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18001000u));	/* S29's select and the teardown restore only: S31 does not move the window */
		}
		EXPECT(!mem_ready());									/* the teardown disarmed everything */
		teardown_tail_is_table_order(31);
		baseline_restored();
		for (int m = 1; m <= 3; m += 2) {							/* an S30 stop (all ones / another chip id) keeps S31 off the bus: no S31 line, only S30's 3 reads (review 12 L2) */
			world(); mem_mode = m;
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S30") && !has("LINK S31 start") && mem_reads == 3 && has("restore=ok") && !has("!!!"));
			baseline_restored();
		}
		for (int m = 4; m <= 6; m++) {								/* eromptr unaligned / outside the enum range / all ones: the one-shot check REFUSES after the logs */
			world(); mem_mode = m;
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S31") && has("restore=ok") && !has("!!!") && mem_reads == 7 && master_writes == 0);
			EXPECT(!has("LINK S31 ok") && !mem_ready());
			teardown_tail_is_table_order(31);
			baseline_restored();
		}
		world(); mem_mode = 7;									/* clk_ctl_st / capabilities_ext all ones: only logged (the chip id and eromptr checks decide); the host decodes the log */
		EXPECT(wlan_link_run() == 0 && has("LINK S31 ok rows=8"));
		snprintf(b, sizeof b, "LINK R31 row=%u rd=0xFFFFFFFF", row_clk); EXPECT(has(b));
		baseline_restored();
		world(); fault_read_pa = WLAN_MEM + 0xFC;						/* the eromptr read faults: stop, nothing logged from S31, teardown unchanged */
		snprintf(b, sizeof b, "LINK S31 fault row=%u", row_erom);
		EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && !has("LINK R31 row=") && mem_reads == 3);	/* the faulting read itself is not counted by the fake */
		teardown_tail_is_table_order(31);
		baseline_restored();
		world(); fault_read_pa = WLAN_MEM + 0x1E0;						/* clk_ctl_st faults: eromptr was logged, the run stops there */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S31") && has("restore=ok") && !has("!!!") && mem_reads == 4);
		snprintf(b, sizeof b, "LINK R31 row=%u rd=0x18105000", row_erom); EXPECT(has(b));
		baseline_restored();
	}
#endif

#if WLAN_LINK_N >= 32
	/* ---- S32 (research 37 s.9.1 run B): the ONE new write cfg 0x80 := 0x18105000 (the EROM page, eromptr observed in run w3a-n31), 896 read-only EROM dwords (BAR0+0..0xDFC, below ER_REMAPCONTROL 0xE00), restore FIRST in the teardown on every stop path ---- */
	{
		static const uint32_t wv[] = { 0, 0x18000000u, 0x18001000u, 0x18101000u, 0x18105000u, 0x18105004u, 0x18106000u, 0x18104000u, 0x60000004u, 0xFFFFFFFFu, 0x18012000u, 0x18120000u, 0x18126000u, 0x18026000u, 0x18013000u, 0x18121000u };
		unsigned n32 = 0, nw32 = 0, i_pre = 0, i_e = 0, i_w = 0, i_rb = 0, i_chk = 0, nd = 0, ncfgl = 0, dump[896];
		char b[96];

		(void)b;

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 32) {
				n32++;
				if (r.kind == WLK_MMIO_WRITE || r.kind == WLK_MMIO_RMW || r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON) {
					EXPECT(r.kind == WLK_MMIO_WRITE && r.blk == WLB_CFG && r.off == 0x80u && r.mask == FULL_MASK_T && r.val == 0x18105000u && !nw32);	/* exactly ONE write row in S32 */
					nw32++; i_w = i;
				} else if (r.blk == WLB_MEM && r.kind == WLK_READ_ONLY) {
					EXPECT(r.a == (nd == 895u ? 14u : 6u) && nd < 896u && r.off == 4u * nd);	/* logged (2) + EOT stop (4), the last row also refuses without an EOT (8); ascending, 4-byte stride, never a probe, below 0xE00 */
					dump[nd++] = i;
				} else if (r.blk == WLB_MEM) {
					EXPECT(r.kind == WLK_WAIT_EQ && r.b == 1u && r.a == 0u);
					if (r.off == 0xFCu) { EXPECT(r.mask == FULL_MASK_T && r.val == 0x18105000u && !i_e); i_e = i; }
					else { EXPECT(r.off == 0u && r.mask == 0xFu && r.val == 0x1u && nd == 1u && !i_chk); i_chk = i; }
				} else {
					EXPECT(r.blk == WLB_CFG && r.off != 0x04u);
					if (r.kind == WLK_READ_ONLY) { EXPECT(r.a == 2u && (r.off == 0xBC || r.off == 0x104)); ncfgl++; }
					else { EXPECT(r.kind == WLK_WAIT_EQ && r.off == 0x80u && r.mask == FULL_MASK_T && r.b == 1u && r.a == 0u); if (r.val == 0x18000000u) i_pre = i; else { EXPECT(r.val == 0x18105000u); i_rb = i; } }
				}
			}
		EXPECT(n32 == 903u && nw32 == 1u && nd == 896u && ncfgl == 2u && i_pre && i_e > i_pre && i_w > i_e && i_rb > i_w && dump[0] > i_rb && i_chk == dump[0] + 1u);
		{	/* the teardown rows of S32: the window restore + its read-back FIRST, and no other row requires S32 */
			unsigned nt = 0;

			for (unsigned i = 0; i < WLAN_SEQ_NTEAR; i++)
				if (get_row(i, 1, &r) && r.step == 32) {
					EXPECT(i == nt && r.blk == WLB_CFG && r.off == 0x80u && r.mask == FULL_MASK_T && r.val == 0x18000000u && (nt == 0 ? r.kind == WLK_MMIO_WRITE : (r.kind == WLK_WAIT_EQ && r.b == 1u && r.a == 0u)));
					nt++;
				}
			EXPECT(nt == 2u);
		}
		/* the stateful allowlist for cfg 0x80, exhaustively: the forward move only from 0x18000000 with Command MEM-only and BAR0 at the S28 assignment; the teardown never moves, and may restore 0x18000000 from the S32 states */
		for (unsigned oi = 0; oi < sizeof wv / sizeof wv[0]; oi++)
			for (unsigned vi = 0; vi < sizeof wv / sizeof wv[0]; vi++)
				for (unsigned cm = 0; cm < 3; cm++)
					for (unsigned bi = 0; bi < 2; bi++)
						for (unsigned td = 0; td < 2; td++) {
							struct w32_ctx c = { cm == 0 ? 0x2u : cm == 1 ? 0x0u : 0x6u, bi ? 0x60000004u : 0x4u, 1, (int)td, 0, 0 };
							uint32_t old = wv[oi], v = wv[vi];
							int tw = old == 0x18105000u || old == 0x18000000u || old == 0x18012000u || old == 0x18120000u || old == 0x18126000u || old == 0x18026000u;	/* S33-S36: the restore takes any window of the chain */
							int fw = (v == 0x18105000u && old == 0x18000000u) || (v == 0x18012000u && old == 0x18105000u) || (v == 0x18120000u && old == 0x18012000u) || (v == 0x18126000u && old == 0x18120000u) || (v == 0x18026000u && old == 0x18126000u) ||
								 (v == 0x18001000u && old == 0x18126000u) || (v == 0x18126000u && old == 0x18001000u) || (v == 0x18120000u && old == 0x18026000u);	/* S34-S36 (research 40 s.5): 0x18126000 -> 0x18001000 -> 0x18126000 -> 0x18026000 -> 0x18120000 */
							int want = (v == 0x18001000u && (td || (old == 0x18126000u && cm == 0 && bi))) || (v == 0x18000000u && (old == 0x18001000u || (td && tw))) || (!td && fw && cm == 0 && bi && v != 0x18001000u);

							EXPECT(w32_allowed(WLAN_CFG + 0x80, FULL_MASK_T, old, v, &c) == want);
							EXPECT(!w32_allowed(WLAN_CFG + 0x80, 0xFFFFu, old, (old & 0xFFFF0000u) | (v & 0xFFFFu), &c) || ((old ^ ((old & 0xFFFF0000u) | (v & 0xFFFFu))) & 0xFFFFu) == 0u);	/* a partial mask is never a window write */
						}
		/* r32: the live window decides which BAR0 offsets may be read (0x18000000: the four ChipCommon words; 0x18105000: any dword below 0xE00 = ER_REMAPCONTROL; S33: the exact per-window lists of research 38; anything else: no TLP) */
		{
			uint32_t v = 0;

			world();
			atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u; cfgspace[4] = 0x60000004u; cfgspace[1] = 0x00100002u;
			atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60000000u; atu[0x204 / 4] = 0x80000000u;
			for (unsigned wi = 0; wi < sizeof wv / sizeof wv[0]; wi++) {
				unsigned before = mem_reads;

				cfgspace[0x80 / 4] = wv[wi];
				for (uint32_t o = 0; o <= 0x1004u; o += 4) {
					int want = t_mem_allowed(wv[wi], o);

					EXPECT(r32(WLB_MEM, o, &v) == want);
					before += (unsigned)want;
					EXPECT(mem_reads == before);				/* a refusal issues no BAR0 TLP */
				}
				EXPECT(!r32(WLB_MEM, 0xFD, &v) && !r32(WLB_MEM, 0x1E1, &v) && !r32(WLB_MEM, 0x601, &v) && mem_reads == before);
			}
			cfgspace[0x80 / 4] = 0x18105000u; fault_read_pa = WLAN_CFG + 0x80;
			EXPECT(!r32(WLB_MEM, 0, &v) && !r32(WLB_MEM, 0x400, &v));			/* an unreadable window select is "not ready" */
			fault_read_pa = 0;
			cfgspace[0x80 / 4] = 0x18105000u; unsigned before = mem_reads;
			EXPECT(r32(WLB_MEM, 0xDFC, &v) && !r32(WLB_MEM, 0xE00, &v) && !r32(WLB_MEM, 0xFFC, &v) && mem_reads == before + 1u);	/* review 13 M1: the last EROM dword, then nothing from ER_REMAPCONTROL on */
			EXPECT(nwrites == 0);
		}
#if WLAN_LINK_N == 32
		/* happy path: the EOT (dword 0xF) at index 217 ends the dump; 225 rows run, the 678 later dump rows are skipped, no read past the EOT */
		world();
		EXPECT(wlan_link_run() == 0 && has("LINK S32 ok rows=225") && has("LINK summary reached=S32 stopped=none restore=ok") && !has("!!!") && cfg_writes_unconfigured == 0 && master_writes == 0);
		EXPECT(mem_reads == 3u + 5u + 1u + 218u + 1u && mem_reads_unready == 0 && erom_reads == 219u && erom_past_eot == 0 && last_ev(EV_W, WLAN_MEM, WLAN_MEM + 0x1000) == 0);	/* S30 3, S31 5, eromptr pin 1, dwords 0..217, the first-dword check */
		for (unsigned k = 0; k <= 217u; k += (k < 8 ? 1u : 105u)) {
			snprintf(b, sizeof b, "LINK R32 row=%u rd=0x%08X", dump[k], (unsigned)erom_dw(k));
			EXPECT(has(b));
		}
		snprintf(b, sizeof b, "LINK R32 row=%u rd=0x0000000F", dump[217]); EXPECT(has(b));		/* the EOT dword itself is logged */
		snprintf(b, sizeof b, "LINK R32 row=%u rd=", dump[219]); EXPECT(!has(b));			/* nothing after it */
		{	/* the window writes, in order: S29 select, the move, the S32 restore, the S29 teardown restore; nothing else touches cfg 0x80 */
			uint32_t wseq[8];
			unsigned nwin = 0;

			for (unsigned i = 0; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa == WLAN_CFG + 0x80) {
					EXPECT(nwin < 8);
					wseq[nwin++] = ev[i].v;
				}
			EXPECT(nwin == 4 && wseq[0] == 0x18000000u && wseq[1] == 0x18105000u && wseq[2] == 0x18000000u && wseq[3] == 0x18001000u);
			EXPECT(first_ev(WLAN_CFG + 0x80, 0x18105000u) > first_ev(WLAN_CFG + 4, 2u));	/* the move comes after the MEM-only arm */
		}
		EXPECT(!mem_ready());
		teardown_tail_is_table_order(32);
		baseline_restored();
		for (int m = 1; m <= 3; m += 2) {							/* an S30 stop keeps S31 and S32 off the bus */
			world(); mem_mode = m;
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S30") && !has("LINK S31 start") && !has("LINK S32 start") && mem_reads == 3 && erom_reads == 0 && has("restore=ok") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u));
			baseline_restored();
		}
		for (int m = 4; m <= 6; m++) {								/* an S31 stop (eromptr unaligned / outside the range / all ones) keeps S32 off the bus: the window is never moved */
			world(); mem_mode = m;
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S31") && !has("LINK S32 start") && mem_reads == 7 && erom_reads == 0 && has("restore=ok") && !has("!!!") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u));
			teardown_tail_is_table_order(31);
			baseline_restored();
		}
		world(); mem_mode = 9;									/* eromptr is an aligned in-range value, but not the pinned one: refused BEFORE the window write */
		EXPECT(wlan_link_run() == 1 && has("LINK S31 ok") && has("stopped=refused at=S32") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u) && erom_reads == 0 && mem_reads == 9 && has("restore=ok") && !has("!!!"));
		teardown_tail_is_table_order(32);
		baseline_restored();
		world(); win_ignore = 1;								/* the window write does not take: the read-back refuses, no EROM read, the restore still runs */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S32") && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u) && erom_reads == 0 && mem_reads == 9 && has("restore=ok") && !has("!!!"));
		teardown_tail_is_table_order(32);
		baseline_restored();
		world(); win_latch = 1;									/* the window latches another page: the read-back refuses; the S32 restore is refused by the allowlist (loud), the final restore still puts the window back */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S32") && erom_reads == 0 && mem_reads == 9 && has("restore=FAILED") && has("!!! RESTORE FAILED") && cfgspace[0x80 / 4] == 0x18001000u);
		baseline_restored();
		world(); write_fault_n = 0; mem_mode = 8;						/* all-ones from the first EROM dword: logged, then the check stops the dump; the window is restored */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S32") && erom_reads == 2u && mem_reads == 11u && has("restore=ok") && !has("!!!") && !has("LINK S32 ok"));
		snprintf(b, sizeof b, "LINK R32 row=%u rd=0xFFFFFFFF", dump[0]); EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R32 row=%u rd=", dump[1]); EXPECT(!has(b));
		{	unsigned mv = first_ev(WLAN_CFG + 0x80, 0x18105000u), rs = 0;

			for (unsigned i = mv; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa == WLAN_CFG + 0x80 && ev[i].v == 0x18000000u && !rs)
					rs = i + 1;
			EXPECT(mv && rs > mv && cfgspace[0x80 / 4] == 0x18001000u);
		}
		teardown_tail_is_table_order(32);
		baseline_restored();
		/* W-C1c: the EOT at the first dword (not a COMPONENT: the first-dword check refuses), mid-dump, at the last dword, and never (the 896-dword cap refuses); never a read past the EOT, the window restored on every path */
		{
			static const unsigned eots[] = { 0u, 1u, 2u, 217u, 218u, 894u, 895u };

			for (unsigned ei = 0; ei < sizeof eots / sizeof eots[0]; ei++) {
				unsigned e = eots[ei];

				world(); eot_idx = e;
				if (e == 0u) {
					EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S32") && !has("LINK S32 ok") && erom_reads == 2u && mem_reads == 3u + 5u + 1u + 2u);	/* dword 0 and its re-check, nothing more */
					snprintf(b, sizeof b, "LINK R32 row=%u rd=0x0000000F", dump[0]); EXPECT(has(b));
				} else {
					snprintf(b, sizeof b, "LINK S32 ok rows=%u", 4u + (e + 1u) + 1u + 2u);
					EXPECT(wlan_link_run() == 0 && has(b) && has("stopped=none restore=ok") && erom_reads == e + 2u && mem_reads == 3u + 5u + 1u + (e + 1u) + 1u);
					snprintf(b, sizeof b, "LINK R32 row=%u rd=0x0000000F", dump[e]); EXPECT(has(b));
					if (e < 895u) { snprintf(b, sizeof b, "LINK R32 row=%u rd=", dump[e + 1u]); EXPECT(!has(b)); }
				}
				EXPECT(erom_past_eot == 0u && has("restore=ok") && !has("!!!") && cfgspace[0x80 / 4] == 0x18001000u && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u));
				teardown_tail_is_table_order(32);
				baseline_restored();
			}
		}
		world(); eot_idx = EOT_NEVER;								/* no EOT within 896 dwords: the last dump row is logged, then refused; the window is restored and reported */
		snprintf(b, sizeof b, "stopped=refused at=S32 row=%u", dump[895]);
		EXPECT(wlan_link_run() == 1 && has(b) && !has("LINK S32 ok") && has("restore=ok") && !has("!!!") && erom_reads == 897u && mem_reads == 3u + 5u + 1u + 896u + 1u && erom_past_eot == 0u);
		snprintf(b, sizeof b, "LINK R32 row=%u rd=0x%08X", dump[895], (unsigned)erom_dw(895)); EXPECT(has(b));
		EXPECT(cfgspace[0x80 / 4] == 0x18001000u);
		teardown_tail_is_table_order(32);
		baseline_restored();
		for (unsigned fi = 0; fi < 6u; fi++) {							/* a read fault anywhere in the dump (up to the EOT): stop at that row, the window is restored */
			static const uint32_t fos[] = { 0x4u, 0x8u, 0x1Cu, 0x100u, 0x360u, 0x364u };
			uint32_t fo = fos[fi];

			world(); fault_read_pa = WLAN_MEM + fo;
			snprintf(b, sizeof b, "LINK S32 fault row=%u", dump[fo / 4]);
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && erom_reads == 1u + 1u + fo / 4u - 1u + 0u + 0u + 0u && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u));
			teardown_tail_is_table_order(32);
			baseline_restored();
		}
		world(); fault_cfg80_once = 1;								/* review 13 L3: the S32 precondition read of cfg 0x80 faults (armed after the S30/S31 MEM reads): stop at S32, the window is never moved */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S32") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u) && erom_reads == 0 && mem_reads == 8 && has("restore=ok") && !has("!!!") && !has("LINK S32 ok"));
		teardown_tail_is_table_order(32);
		baseline_restored();
		world(); clobber_win = 1;								/* review 13 L3: the window was moved away (0x18001000) before the S32 precondition: refused, nothing moved, restore ok */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S32") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u) && erom_reads == 0 && mem_reads == 8 && has("restore=ok") && !has("!!!"));
		teardown_tail_is_table_order(32);
		baseline_restored();
		world(); link_dead_after = 100u;							/* review 13 L3: the link drops mid-dump (config reads answer all ones): the dump stops at the next dword (fault), the S32 restore is refused from the unknown window (loud), the final restore still writes 0x18001000 */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S32") && erom_reads == 100u && !has("LINK S32 ok") && has("restore=FAILED") && has("!!! RESTORE FAILED") && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18001000u) && cfgspace[0x80 / 4] == 0x18001000u);
		world(); fault_read_pa = WLAN_CFG + 0x80;						/* the window select unreadable from the start: S27 stops, S32 never starts, nothing is moved */
		EXPECT(wlan_link_run() == 1 && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18105000u) && !has("LINK S32 start"));
		world(); unreadable_blk = 7;
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S30") && erom_reads == 0);
		baseline_restored();
#endif
	}
#endif

#if WLAN_LINK_N == 33
	/* ---- S33 (research 38, W-C2; trimmed by research 40: no SYS_MEM gate, no core page): READ-ONLY chip state. Three window moves (each only from its predecessor, each read back), the exact BAR0 offsets per window, the restore FIRST on every stop path ---- */
	{
		static const uint32_t chain[3] = { 0x18012000u, 0x18120000u, 0x18126000u };
		unsigned n33 = 0, nw = 0, nlogs = 0, wrow[3] = { 0 }, rbrow[3] = { 0 }, pre = 0, pmug = 0, logrow[16], nlog = 0;
		uint32_t win = 0x18105000u;
		char b[96];

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == 33) {
				n33++;
				if (r.kind == WLK_MMIO_WRITE) {		/* exactly the three window moves, CFG 0x80 full word, in the chain order */
					EXPECT(nw < 3u && r.blk == WLB_CFG && r.off == 0x80u && r.mask == FULL_MASK_T && r.val == chain[nw]);
					wrow[nw++] = i;
					win = r.val;
				} else if (r.kind == WLK_MMIO_RMW || r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON || r.kind == WLK_SMC_ISO) {
					EXPECT(0 && "S33 has no other write kind");
				} else if (r.blk == WLB_MEM) {		/* every MEM row is inside the per-window list of the window the table has selected so far */
					EXPECT(r.kind == WLK_READ_ONLY || (r.kind == WLK_WAIT_EQ && r.b == 1u && r.a == 0u));
					EXPECT(t_mem_allowed(win, r.off));
					EXPECT(win != 0x18026000u && win != 0x18001000u);	/* the SYS_MEM core page and the PCIe core page are S34 / S35 */
					EXPECT(r.kind == WLK_WAIT_EQ || (r.a == 2u && r.mask == FULL_MASK_T));
					if (r.kind == WLK_READ_ONLY) { EXPECT(nlog < 16u); logrow[nlog++] = i; nlogs++; }
					if (win == 0x18012000u && r.kind == WLK_WAIT_EQ) { EXPECT(r.off == 0x604u && r.mask == 0xFFu && r.val == 0x28u); pmug = i; }
					if (r.kind == WLK_WAIT_EQ && win == 0x18120000u) EXPECT(r.off == 0x800u && r.mask == FULL_MASK_T && r.val == 1u);	/* the ARM CA7 must still be held in reset (RESET_CTL == 1; review 16 finding 3) */
					if (r.kind == WLK_WAIT_EQ && win != 0x18012000u && win != 0x18120000u) EXPECT(r.off == 0x800u && r.mask == 0xFFFFFFFEu && r.val == 0u);	/* the SYS_MEM RESET_CTL upper-bit guard; no SYS_MEM-up gate any more */
				} else if (r.blk == WLB_CFG && r.kind == WLK_WAIT_EQ) {
					EXPECT(r.off == 0x80u && r.mask == FULL_MASK_T && r.b == 1u && r.a == 0u);
					if (!nw) { EXPECT(r.val == 0x18105000u && !pre); pre = i; }
					else { EXPECT(r.val == chain[nw - 1u] && !rbrow[nw - 1u]); rbrow[nw - 1u] = i; }
				} else {
					EXPECT(r.blk == WLB_CFG && r.kind == WLK_READ_ONLY && r.a == 2u && (r.off == 0xBC || r.off == 0x104));
					nlogs++;
				}
			}
		EXPECT(n33 == 23u && nw == 3u && nlogs == 13u && pre && pmug);
		EXPECT(pre < wrow[0] && rbrow[0] == wrow[0] + 1u && rbrow[1] == wrow[1] + 1u && rbrow[2] == wrow[2] + 1u && wrow[0] < pmug && pmug < wrow[1] && wrow[1] < wrow[2]);
		{	/* no teardown row of its own: the S32 rows T01/T02 restore from every window of the chain */
			for (unsigned i = 0; i < WLAN_SEQ_NTEAR; i++)
				EXPECT(!(get_row(i, 1, &r) && r.step == 33));
		}
		/* the stateful allowlist: a forward move never skips a link of the chain, and never from the wrong state (the exhaustive grid is in the S32 block) */
		for (unsigned k = 0; k < 3; k++)
			for (unsigned f = 0; f < 3; f++) {
				struct w32_ctx c = { 0x2u, 0x60000004u, 1, 0, 0, 0 };
				uint32_t from = f == 0 ? 0x18105000u : chain[f - 1];

				EXPECT(w32_allowed(WLAN_CFG + 0x80, FULL_MASK_T, from, chain[k], &c) == (k == f));
			}

		/* happy path: three windows, 23 rows, the 11 MEM logs with the observed values, the window restored first, NO BAR0 write */
		world();
		EXPECT(wlan_link_run() == 0 && has("LINK S33 ok rows=23") && has("LINK summary reached=S33 stopped=none restore=ok") && !has("!!!") && cfg_writes_unconfigured == 0 && master_writes == 0);
		EXPECT(mem_illegal == 0u && s33_reads == 6u + 4u + 4u && core_reads == 0u && mem_reads_unready == 0 && last_ev(EV_W, WLAN_MEM, WLAN_MEM + 0x1000) == 0 && mem_wr_n == 0u);	/* PMU 5 logs + the pmurev guard, ARM 3 + guard, SYS_MEM 3 + guard */
		for (unsigned k = 0; k < 11u; k++) {
			(void)get_row(logrow[k], 0, &r);
			snprintf(b, sizeof b, "LINK R33 row=%u rd=0x%08X", logrow[k], (unsigned)(k < 5 ? (k == 0 ? 0x20010028u : k == 1 ? 0x100u : k == 2 ? 0x2Cu : k == 3 ? 0xFFFFu : 0xFFFFFFu) : k == 6 || k == 9 ? 1u : 0u));	/* observed w3a-n33: IOCTL 0, RESET_CTL 1, RESET_ST 0 in both wrappers */
			EXPECT(has(b));
		}
		{	/* the cfg 0x80 writes in order: S29 select, S32 move, the three S33 moves, the restore (FIRST in the teardown), the S29 original */
			uint32_t wseq[16];
			unsigned nwin = 0;

			for (unsigned i = 0; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa == WLAN_CFG + 0x80) {
					EXPECT(nwin < 16);
					wseq[nwin++] = ev[i].v;
				}
			EXPECT(nwin == 7 && wseq[0] == 0x18000000u && wseq[1] == 0x18105000u && wseq[2] == chain[0] && wseq[3] == chain[1] && wseq[4] == chain[2] && wseq[5] == 0x18000000u && wseq[6] == 0x18001000u);
		}
		teardown_tail_is_table_order(33);
		baseline_restored();

		/* stop rules: each one ends with restore=ok, the window back, and the later windows never touched */
		world(); s33_mode = 1;						/* pmurev != 0x28: refused AFTER the five PMU logs, no ARM wrapper move */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33") && has("restore=ok") && !has("!!!") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[1]) && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u) && s33_reads == 6u && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal == 0u);
		snprintf(b, sizeof b, "LINK R33 row=%u rd=0x20010027", logrow[0]); EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R33 row=%u rd=", logrow[5]); EXPECT(!has(b));
		teardown_tail_is_table_order(33);
		baseline_restored();
		world(); s33_mode = 6;						/* all ones in the PMU: logged, the pmurev guard refuses */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33") && has("restore=ok") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[1]));
		snprintf(b, sizeof b, "LINK R33 row=%u rd=0xFFFFFFFF", logrow[4]); EXPECT(has(b));
		baseline_restored();
		world(); s33_mode = 2;						/* ARM RESET_CTL upper bits: refused after its logs, SYS_MEM never reached */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33") && has("restore=ok") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[2]) && cfgspace[0x80 / 4] == 0x18001000u);
		snprintf(b, sizeof b, "LINK R33 row=%u rd=0x00000101", logrow[6]); EXPECT(has(b));
		baseline_restored();
		world(); arm_rc = 0u;						/* ARM CA7 RESET_CTL == 0 (out of reset, possibly running; review 16 finding 3): refused at S33 after its logs, SYS_MEM never reached, no chip write */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33") && has("restore=ok") && !has("!!!") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[2]) && cfgspace[0x80 / 4] == 0x18001000u && mem_wr_n == 0u && !has("LINK S33 ok"));
		snprintf(b, sizeof b, "LINK R33 row=%u rd=0x00000000", logrow[6]); EXPECT(has(b));
		baseline_restored();
		world(); s33_mode = 5;						/* SYS_MEM RESET_CTL upper bits: the guard refuses (the last guard of S33) */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33") && has("restore=ok") && !has("!!!") && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal == 0u && !has("LINK S33 ok"));
		teardown_tail_is_table_order(33);
		baseline_restored();
		for (int m = 0; m < 3; m++) {					/* the SYS_MEM state is only LOGGED now (research 40 finding 4): down, clock off, FGC set or RESET_ST pending do not stop S33 */
			world(); sm_io = m == 0 ? 0u : m == 1 ? 3u : 1u; sm_rc = m == 2 ? 0u : 1u; sm_rs = m == 2 ? 1u : 0u;
			EXPECT(wlan_link_run() == 0 && has("LINK S33 ok rows=23") && has("restore=ok") && core_reads == 0u && mem_wr_n == 0u);
			baseline_restored();
		}
		for (unsigned k = 0; k < 3; k++) {				/* each window move: the write faults / is swallowed / latches another page: stop, restore FIRST, nothing later touched */
			world(); win_fault_v = (int)chain[k];
			snprintf(b, sizeof b, "LINK S33 fault row=%u", wrow[k]);
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && (k == 2 || !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[k + 1])) && cfgspace[0x80 / 4] == 0x18001000u && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u) && mem_illegal == 0u);
			teardown_tail_is_table_order(33);
			baseline_restored();
			world(); win_ign_v = (int)chain[k];
			snprintf(b, sizeof b, "stopped=refused at=S33 row=%u", rbrow[k]);
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && (k == 2 || !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[k + 1])) && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal == 0u);
			teardown_tail_is_table_order(33);
			baseline_restored();
			world(); win_lat_v = (int)chain[k];				/* latches another page: the read-back refuses; the restore from the unknown page is refused by the allowlist (loud), the final S29 restore still writes 0x18001000 */
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=FAILED") && has("!!! RESTORE FAILED") && cfgspace[0x80 / 4] == 0x18001000u && (k == 2 || !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[k + 1])) && mem_illegal == 0u);
		}
		world(); clobber33 = 1;						/* the precondition: the window is not at the S32 end state: refused, nothing moved, restore ok */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S33 row=") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[0]) && s33_reads == 0u && has("restore=ok") && !has("!!!"));
		teardown_tail_is_table_order(33);
		baseline_restored();
		world(); fault33 = 1;						/* the precondition read faults: stop, nothing moved */
		EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S33") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[0]) && s33_reads == 0u && has("restore=ok") && !has("!!!"));
		baseline_restored();
		{	/* a BAR0 read fault in each window: stop at that row, restore first */
			static const uint32_t fo[] = { 0x61Cu, 0x804u };

			for (unsigned fi = 0; fi < 2u; fi++) {
				world(); fault_read_pa = WLAN_MEM + fo[fi];
				EXPECT(wlan_link_run() == 1 && has("stopped=fault at=S33") && has("restore=ok") && !has("!!!") && cfgspace[0x80 / 4] == 0x18001000u && core_reads == 0u);
				teardown_tail_is_table_order(33);
				baseline_restored();
			}
		}
		world(); unreadable_blk = 7;					/* BAR0 unreadable from the start: S30 stops, S33 never starts */
		EXPECT(wlan_link_run() == 1 && !has("LINK S33 start") && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, chain[0]));
		baseline_restored();
	}
#endif

#if WLAN_LINK_N >= 34
	/* ---- S34..S36 (research 40, goal W-C3): the FIRST BAR0 writes. S34 the SRPWR request (PCIe core page powerctl), S35 the SYS_MEM wrapper core reset + the up gate + coreinfo / bankidx / bankinfo, S36 the ARM CA7 halt. Each N is a strict prefix of the next.
	 * Every write has a read-back that stops the run; the restore (cfg 0x80 := 0x18000000) is FIRST in the teardown on every stop path; nothing is undone on the chip. ---- */
	{
		struct exp { unsigned step, blk, off, kind; uint32_t mask, val; };
		static const struct exp ex[] = {
			{ 34, WLB_CFG, 0x80u, WLK_MMIO_WRITE, FULL_MASK_T, 0x18001000u }, { 34, WLB_MEM, 0x1E8u, WLK_MMIO_RMW, 0x200u, 0x200u },
			{ 35, WLB_CFG, 0x80u, WLK_MMIO_WRITE, FULL_MASK_T, 0x18126000u }, { 35, WLB_MEM, 0x800u, WLK_MMIO_WRITE, FULL_MASK_T, 1u }, { 35, WLB_MEM, 0x408u, WLK_MMIO_WRITE, FULL_MASK_T, 0x3u }, { 35, WLB_MEM, 0x800u, WLK_MMIO_WRITE, FULL_MASK_T, 0u },
			{ 35, WLB_MEM, 0x408u, WLK_MMIO_WRITE, FULL_MASK_T, 0x1u }, { 35, WLB_CFG, 0x80u, WLK_MMIO_WRITE, FULL_MASK_T, 0x18026000u }, { 35, WLB_MEM, 0x10u, WLK_MMIO_WRITE, FULL_MASK_T, 0u },
			{ 36, WLB_CFG, 0x80u, WLK_MMIO_WRITE, FULL_MASK_T, 0x18120000u }, { 36, WLB_MEM, 0x800u, WLK_MMIO_WRITE, FULL_MASK_T, 1u }, { 36, WLB_MEM, 0x408u, WLK_MMIO_WRITE, FULL_MASK_T, 0x23u }, { 36, WLB_MEM, 0x800u, WLK_MMIO_WRITE, FULL_MASK_T, 0u },
			{ 36, WLB_MEM, 0x408u, WLK_MMIO_WRITE, FULL_MASK_T, 0x21u },
		};
		const unsigned nex = WLAN_LINK_N >= 36 ? 14u : WLAN_LINK_N >= 35 ? 9u : 2u, nmw = WLAN_LINK_N >= 36 ? 10u : WLAN_LINK_N >= 35 ? 6u : 1u;
		unsigned wr_rows[16], nwr = 0, mw_rows[16], nmwr = 0, lrow[3][12], nl3[3] = { 0, 0, 0 }, rb[16] = { 0 }, gidx[16] = { 0 };
		char b[160];

		/* ---- the table shape: the write rows of steps 34..N are exactly ex[0..nex) in order; every write has a later read-back wait on the same register before the next write (the window moves immediately) ---- */
		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++) {
			if (!get_row(i, 0, &r) || r.step < 34u || r.step > WLAN_LINK_N)
				continue;
			EXPECT(r.step <= 36u);
			if (r.kind == WLK_MMIO_WRITE || r.kind == WLK_MMIO_RMW || r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON || r.kind == WLK_SMC_ISO) {
				EXPECT(nwr < nex && r.step == ex[nwr].step && r.blk == ex[nwr].blk && r.off == ex[nwr].off && r.kind == ex[nwr].kind && r.mask == ex[nwr].mask && r.val == ex[nwr].val);
				EXPECT(r.a == (r.kind == WLK_MMIO_RMW ? 32u : 0u));	/* the S34 SRPWR request row carries the skip flag 32 (skipped once SRP_UP is recorded); no other write row has a flag */
				wr_rows[nwr++] = i;
				if (r.blk == WLB_MEM)
					mw_rows[nmwr++] = i;
			}
			if (r.blk == WLB_MEM && r.kind == WLK_READ_ONLY) {
				EXPECT(r.a == 2u || (r.step == 34u && r.off == 0x1E8u && r.a == 18u));	/* log flag; the S34 powerctl log row also carries the SRP_UP recorder flag 16 */
				EXPECT(nl3[r.step - 34u] < 12u);
				lrow[r.step - 34u][nl3[r.step - 34u]++] = i;
			}
			if (r.blk == WLB_MEM) {
				EXPECT(t_mem_allowed(r.step == 34u ? 0x18001000u : (r.step == 35u && (r.off == 0x10u || r.off == 0x40u || r.off == 0u)) ? 0x18026000u : r.step == 35u ? 0x18126000u : 0x18120000u, r.off));
				EXPECT(r.kind == WLK_READ_ONLY || r.kind == WLK_WAIT_EQ || r.kind == WLK_WAIT_MASK || r.kind == WLK_MMIO_WRITE || r.kind == WLK_MMIO_RMW);
			}
			if (r.blk == WLB_NONE)
				EXPECT(r.kind == WLK_DELAY && (r.a == 10u || r.a == 1u));
		}
		EXPECT(nwr == nex && nmwr == nmw);
		for (unsigned k = 0; k < nwr; k++) {		/* read-back: the next write is not reached before a wait on the same register */
			unsigned end = k + 1u < nwr ? wr_rows[k + 1] : WLAN_SEQ_NROWS, got = 0;
			uint32_t off = ex[k].off;

			for (unsigned i = wr_rows[k] + 1u; i < end; i++)
				if (get_row(i, 0, &r) && r.step <= WLAN_LINK_N && r.blk == ex[k].blk && r.off == off && (r.kind == WLK_WAIT_EQ || r.kind == WLK_WAIT_MASK) && r.step == ex[k].step) {
					got = 1;
					if (!rb[k]) rb[k] = i;
				}
			EXPECT(got);
			if (ex[k].blk == WLB_CFG) {			/* the window move: the read-back row follows IMMEDIATELY (one-shot) */
				struct wlan_row q;

				EXPECT(rb[k] == wr_rows[k] + 1u && get_row(rb[k], 0, &q) && q.b == 1u && q.a == 0u && q.mask == FULL_MASK_T && q.val == ex[k].val);
			}
		}
		/* the SRPWR wait: bit 17 within 150 x 100 us = 15 ms (DHD PMU_MAX_TRANSITION_DLY) */
		{
			unsigned got = 0;

			for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
				if (get_row(i, 0, &r) && r.step == 34u && r.kind == WLK_WAIT_MASK) {
					EXPECT(r.blk == WLB_MEM && r.off == 0x1E8u && r.mask == 0x20000u && r.a == 100u && r.b == 150u);
					got++;
				}
			EXPECT(got == 1u);
		}
		/* the S35 up gate is before the core page move and the core page is entered only after it */
		if (WLAN_LINK_N >= 35) {
			unsigned g[3] = { 0, 0, 0 }, ng = 0, mvc = 0;

			for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
				if (get_row(i, 0, &r) && r.step == 35u) {
					if (r.blk == WLB_MEM && r.kind == WLK_WAIT_EQ && r.b == 1u && ((r.off == 0x408u && r.mask == 3u && r.val == 1u) || (r.off == 0x800u && r.mask == 1u && r.val == 0u && ng == 1u) || (r.off == 0x804u && r.mask == FULL_MASK_T && r.val == 0u && ng == 2u))) {
						EXPECT(ng < 3u);
						g[ng++] = i;
					}
					if (r.kind == WLK_MMIO_WRITE && r.blk == WLB_CFG && r.val == 0x18026000u)
						mvc = i;
				}
			EXPECT(ng == 3u && g[0] < g[1] && g[1] < g[2] && g[2] < mvc);
		}
		/* the teardown has no row of its own for S34..S36 (the S32 rows T01/T02 restore the window FIRST) */
		for (unsigned i = 0; i < WLAN_SEQ_NTEAR; i++)
			EXPECT(!(get_row(i, 1, &r) && r.step >= 33u));

		/* ---- the allowlist: memw_ok against the test's own spec over a grid; memw_pred against the spec; w32_allowed's MEM branch (teardown never, mask rule) ---- */
		{
			static const uint32_t wins[] = { 0, 0x18000000u, 0x18001000u, 0x18012000u, 0x18105000u, 0x18120000u, 0x18126000u, 0x18026000u, 0x18027000u, 0x18125000u, 0x18121000u };
			static const uint32_t offs[] = { 0u, 4u, 0x10u, 0x14u, 0x40u, 0x1E8u, 0x1ECu, 0x400u, 0x404u, 0x408u, 0x40Cu, 0x800u, 0x804u, 0x808u, 0x80Cu };
			static const uint32_t vals[] = { 0u, 1u, 2u, 3u, 0x5u, 0x20u, 0x21u, 0x23u, 0x25u, 0x2Bu, 0x1Fu, 0x3E0u, 0x200u, 0x201u, 0x20000u, 0x30020200u, 0x30000000u, 0x10000000u, 0x10000200u, 0x0FFFFFFFu, 0x4000u, 0x8003u, 0xFFFFu, 0xFFFFFFFFu };
			unsigned allowed = 0;

			for (unsigned wi = 0; wi < sizeof wins / sizeof wins[0]; wi++)
				for (unsigned oi = 0; oi < sizeof offs / sizeof offs[0]; oi++)
					for (unsigned ai = 0; ai < sizeof vals / sizeof vals[0]; ai++)
						for (unsigned vi = 0; vi < sizeof vals / sizeof vals[0]; vi++)
							for (unsigned pi = 0; pi < sizeof vals / sizeof vals[0]; pi++) {
								int want = spec_memw(wins[wi], offs[oi], vals[ai], vals[vi], vals[pi]);

								EXPECT(memw_ok(wins[wi], offs[oi], vals[ai], vals[vi], vals[pi]) == want);
								allowed += (unsigned)want;
							}
			EXPECT(allowed > 100u && allowed < 20000u);
			for (unsigned oi = 0; oi < sizeof offs / sizeof offs[0]; oi++)
				EXPECT(memw_pred(offs[oi]) == spec_pred_off(offs[oi]));
			EXPECT(memw_pred(0x1E8u) == 0xFFFFFFFFu && memw_pred(0x804u) == 0xFFFFFFFFu && memw_pred(0x40u) == 0xFFFFFFFFu);
			/* the exact tuples, spelled out (not through the spec table): the windows and values nothing else may write */
			EXPECT(!memw_ok(0x18001000u, 0x1E8u, 0u, 0x200u, 0u) && memw_ok(0x18001000u, 0x1E8u, 0x30000000u, 0x30000200u, 0u) && !memw_ok(0x18001000u, 0x1E8u, 0x10000000u, 0x10000100u, 0u) && !memw_ok(0x18001000u, 0x1E8u, 0x10000200u, 0x10000000u, 0u) &&
			       !memw_ok(0x18001000u, 0x1E8u, 0xFFFFFFFFu, 0xFFFFFFFFu, 0u) && !memw_ok(0x18001000u, 0x1E8u, 0x0FFFFFFFu, 0x0FFFFFFFu | 0x200u, 0u) && memw_ok(0x18001000u, 0x1E8u, 0x10000000u, 0x10000200u, 0u) && !memw_ok(0x18001000u, 0x1ECu, 0u, 0x200u, 0u) && !memw_ok(0x18000000u, 0x1E8u, 0u, 0x200u, 0u));
			EXPECT(memw_ok(0x18126000u, 0x800u, 1u, 1u, 0u) && memw_ok(0x18126000u, 0x800u, 1u, 0u, 3u) && !memw_ok(0x18126000u, 0x800u, 1u, 0u, 1u) && !memw_ok(0x18126000u, 0x800u, 1u, 0u, 0x23u) && !memw_ok(0x18126000u, 0x800u, 1u, 2u, 3u));
			EXPECT(memw_ok(0x18126000u, 0x408u, 0u, 3u, 1u) && !memw_ok(0x18126000u, 0x408u, 0u, 3u, 0u) && memw_ok(0x18126000u, 0x408u, 3u, 1u, 0u) && !memw_ok(0x18126000u, 0x408u, 3u, 1u, 1u) && !memw_ok(0x18126000u, 0x408u, 1u, 1u, 0u) && !memw_ok(0x18126000u, 0x408u, 0u, 0x23u, 1u) &&
			       !memw_ok(0x18126000u, 0x408u, 0u, 0x20u, 1u) && !memw_ok(0x18126000u, 0x400u, 0u, 3u, 1u) && !memw_ok(0x18126000u, 0x404u, 0u, 3u, 1u));
			EXPECT(memw_ok(0x18120000u, 0x408u, 0u, 0x23u, 1u) && memw_ok(0x18120000u, 0x408u, 0x23u, 0x21u, 0u) && !memw_ok(0x18120000u, 0x408u, 0u, 0x3u, 1u) && !memw_ok(0x18120000u, 0x408u, 0x23u, 0x23u, 0u) && !memw_ok(0x18120000u, 0x408u, 0u, 0x21u, 0u) &&
			       !memw_ok(0x18120000u, 0x408u, 0u, 0x63u, 1u) && !memw_ok(0x18120000u, 0x408u, 0x3u, 0x21u, 0u) && memw_ok(0x18120000u, 0x800u, 0u, 1u, 0u) && memw_ok(0x18120000u, 0x800u, 1u, 0u, 0x23u) && !memw_ok(0x18120000u, 0x800u, 1u, 0u, 0x3u));
			EXPECT(memw_ok(0x18026000u, 0x10u, 3u, 0u, 5u) && !memw_ok(0x18026000u, 0x10u, 3u, 1u, 5u) && !memw_ok(0x18026000u, 0x10u, 3u, 0u, 0xFFFFFFFFu) && !memw_ok(0x18026000u, 0x10u, 3u, 0u, 0x25u) && !memw_ok(0x18026000u, 0x10u, 3u, 0u, 0u) && !memw_ok(0x18026000u, 0x14u, 3u, 0u, 5u) &&
			       !memw_ok(0x18026000u, 0x40u, 3u, 0u, 5u) && !memw_ok(0x18026000u, 0x0u, 3u, 0u, 5u));
		}
		{	/* the plumbing: w32() on every BAR0 write row, in every window and every guard state, writes iff the guards hold and the spec allows; the live old / predecessor come from the model */
			static const uint32_t wv2[] = { 0, 0x18000000u, 0x18001000u, 0x18012000u, 0x18105000u, 0x18120000u, 0x18126000u, 0x18026000u, 0x18027000u };

			for (unsigned ri = 0; ri < nmwr; ri++)
				for (unsigned wi = 0; wi < sizeof wv2 / sizeof wv2[0]; wi++)
					for (unsigned gs = 0; gs < 5; gs++)
						for (unsigned st = 0; st < 3; st++) {
							uint32_t old, pred = 0, po, v;
							unsigned w0, m0;
							int ready = gs == 0, exp_w;

							world();
							atu[0] = 0x4u; atu[1] = 0x80000000u; atu[2] = 0x60FFE000u; atu[4] = 0x60FFFFFFu; atu[5] = 0x01000000u; cfgspace[4] = 0x60000004u; cfgspace[1] = 0x00100002u;
							atu[0x208 / 4] = 0x60000000u; atu[0x210 / 4] = 0x6000FFFFu; atu[0x214 / 4] = 0x60000000u; atu[0x204 / 4] = 0x80000000u;
							if (gs == 1) atu[0x204 / 4] = 0u;				/* region 1 disabled */
							if (gs == 2) cfgspace[1] = 0x00100000u;				/* Command: MEM decode off */
							if (gs == 3) cfgspace[1] = 0x00100006u;				/* Command: MASTER on */
							if (gs == 4) atu[0x210 / 4] = 0x6001FFFFu;			/* region 1 limit changed */
							cfgspace[0x80 / 4] = wv2[wi];
							(void)get_row(mw_rows[ri], 0, &r);
							if (st == 1) { cp_powerctl = 0x200u; srpwr_cnt = 9u; }		/* SRPWR already requested */
							if (st == 2) { sm_io = arm_io = 3u; sm_rc = arm_rc = 0u; }	/* mid-sequence */
							old = s33_win(wv2[wi]) ? chip_get(wv2[wi], r.off) : 0u;
							po = spec_pred_off(r.off);
							if (po != 0xFFFFFFFFu && s33_win(wv2[wi]) && t_mem_allowed(wv2[wi], po))
								pred = wv2[wi] == 0x18026000u && po == 0u ? sm_coreinfo : chip_get(wv2[wi], po);
							v = (old & ~r.mask) | (r.val & r.mask);
							exp_w = ready && t_mem_allowed(wv2[wi], r.off) && (po == 0xFFFFFFFFu || t_mem_allowed(wv2[wi], po)) && spec_memw(wv2[wi], r.off, old, v, pred);
							w0 = nwrites; m0 = mem_wr_n;
							EXPECT(w32(mw_rows[ri], 0) == (exp_w ? 0 : -2));
							EXPECT(nwrites - w0 == (exp_w ? 1u : 0u) && mem_wr_n - m0 == (exp_w ? 1u : 0u) && mem_illegal_w == 0u);
						}
			{	/* w32_allowed's BAR0 branch: never in a teardown row, the predecessor decides, the mask rule holds */
				struct w32_ctx c = { 0x2u, 0x60000004u, 1, 1, 0x18126000u, 3u };

				EXPECT(!w32_allowed(WLAN_MEM + 0x800u, FULL_MASK_T, 1u, 0u, &c));
				c.tear = 0;
				EXPECT(w32_allowed(WLAN_MEM + 0x800u, FULL_MASK_T, 1u, 0u, &c));
				c.pred = 1u;
				EXPECT(!w32_allowed(WLAN_MEM + 0x800u, FULL_MASK_T, 1u, 0u, &c));
				c.pred = 3u;
				EXPECT(!w32_allowed(WLAN_MEM + 0x800u, 0xFFu, 0x100u, 0u, &c));		/* v differs from old outside the mask */
				EXPECT(!w32_allowed(WLAN_MEM + 0xFFCu, FULL_MASK_T, 1u, 0u, &c) && !w32_allowed(WLAN_MEM + 0x1000u, FULL_MASK_T, 1u, 0u, &c));
			}
		}
		/* the S32 grid above and this one share world(): leave it clean */
		world();

		/* ---- the happy path of this N: window moves, writes, logs, final chip state, restore FIRST ---- */
		world();
		snprintf(b, sizeof b, "LINK summary reached=S%02d stopped=none restore=ok", WLAN_LINK_N);
		EXPECT(wlan_link_run() == 0 && has(b) && !has("!!!") && cfg_writes_unconfigured == 0 && master_writes == 0 && mem_illegal == 0u && mem_illegal_w == 0u && mem_wr_unready == 0u && mem_wr_n == nmw && mem_reads_unready == 0u);
		EXPECT(cp_powerctl == 0x200u);
		EXPECT(WLAN_LINK_N < 35 || (sm_io == 1u && sm_rc == 0u && sm_bankidx == 0u && sm_rs == 0u));
		EXPECT(WLAN_LINK_N < 36 ? (arm_io == 0u && arm_rc == 1u) : (arm_io == 0x21u && arm_rc == 0u && arm_rs == 0u));
		snprintf(b, sizeof b, "LINK R34 row=%u rd=0x30000000", lrow[0][0]); EXPECT(has(b));
		snprintf(b, sizeof b, "LINK R34 row=%u rd=0x30020200", lrow[0][1]); EXPECT(has(b));
		if (WLAN_LINK_N >= 35) {
			static const uint32_t e35[] = { 0u, 1u, 0u, 1u, 0u, 0u, 5u, 0u, 7u };	/* IOCTL / RESET_CTL / RESET_ST before and after (observed pre-state, expected post-state), coreinfo, bankidx (read back), bankinfo */
			EXPECT(nl3[1] == 9u);
			for (unsigned k = 0; k < 9u; k++) { snprintf(b, sizeof b, "LINK R35 row=%u rd=0x%08X", lrow[1][k], (unsigned)e35[k]); EXPECT(has(b)); }
		}
		if (WLAN_LINK_N >= 36) {
			static const uint32_t e36[] = { 0u, 1u, 0u, 0x21u, 0u, 0u };
			EXPECT(nl3[2] == 6u);
			for (unsigned k = 0; k < 6u; k++) { snprintf(b, sizeof b, "LINK R36 row=%u rd=0x%08X", lrow[2][k], (unsigned)e36[k]); EXPECT(has(b)); }
		}
		{	/* cfg 0x80 writes in order: S29 select, S32 move, three S33 moves, S34 core page, (S35 wrapper, core page,) (S36 ARM,) the restore (FIRST in the teardown), the S29 original */
			uint32_t wseq[24];
			unsigned nwin = 0, want = 0;
			static const uint32_t exp[] = { 0x18000000u, 0x18105000u, 0x18012000u, 0x18120000u, 0x18126000u, 0x18001000u, 0x18126000u, 0x18026000u, 0x18120000u };
			const unsigned nfwd = WLAN_LINK_N >= 36 ? 9u : WLAN_LINK_N >= 35 ? 8u : 6u;

			for (unsigned i = 0; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa == WLAN_CFG + 0x80) {
					EXPECT(nwin < 24);
					wseq[nwin++] = ev[i].v;
				}
			EXPECT(nwin == nfwd + 2u);
			for (; want < nfwd; want++)
				EXPECT(wseq[want] == exp[want]);
			EXPECT(wseq[nfwd] == 0x18000000u && wseq[nfwd + 1u] == 0x18001000u);
		}
		{	/* the BAR0 writes in order (offsets and values), exactly the table's */
			unsigned k = 0;

			for (unsigned i = 0; i < nev; i++)
				if (ev[i].t == EV_W && ev[i].pa >= WLAN_MEM && ev[i].pa < WLAN_MEM + 0x1000u) {
					unsigned j = 0, seen = 0;

					for (; j < nwr; j++)
						if (ex[j].blk == WLB_MEM && seen++ == k)
							break;
					EXPECT(j < nwr && ev[i].pa == WLAN_MEM + ex[j].off && (ex[j].kind == WLK_MMIO_RMW ? (ev[i].v & 0x200u) == 0x200u : ev[i].v == ex[j].val));
					k++;
				}
			EXPECT(k == nmw);
		}
		teardown_tail_is_table_order(WLAN_LINK_N);
		EXPECT(cfgspace[0x80 / 4] == 0x18001000u);
		baseline_restored();

		/* ---- fault / swallowed write / wrong stored value at EVERY BAR0 write (restore FIRST each time, no later BAR0 write) ---- */
		world();					/* the global try_write32 index of every BAR0 write, from a clean run (the fault knob counts every write) */
		EXPECT(wlan_link_run() == 0);
		for (unsigned e = 0, gi = 0, seen = 0; e < nev; e++)
			if (ev[e].t == EV_W) {
				gi++;
				if (ev[e].pa >= WLAN_MEM && ev[e].pa < WLAN_MEM + 0x1000u) {
					EXPECT(seen < 16u);
					gidx[++seen] = gi;
				}
			}
		for (unsigned i = 1; i <= nmw; i++) {
			EXPECT(gidx[i] != 0u);
			for (int kind = 0; kind < 3; kind++) {		/* 0 = the write faults, 1 = swallowed, 2 = a wrong value is stored */
				int noop = kind == 1 && ((WLAN_LINK_N >= 35 && i == 2u) || (WLAN_LINK_N >= 36 && i == 7u));	/* RESET_CTL := 1 on a core that is already in reset (observed pre-state) changes nothing */
				int rc;

				world();
				if (kind == 0) { write_fault_n = (int)gidx[i]; } else { mw_n = i; mw_kind = kind; }
				rc = wlan_link_run();
				if (noop) {
					EXPECT(rc == 0 && has("restore=ok") && mem_wr_n == nmw);
				} else {
					EXPECT(rc == 1 && !has("stopped=none") && has("restore=ok") && !has("!!!") && mem_wr_n == (kind == 0 ? i - 1u : i) && mem_illegal_w == 0u);
					EXPECT(kind == 0 ? has("stopped=fault") : (has("stopped=refused") || has("stopped=timeout")));
					teardown_tail_is_table_order(reached_step());
				}
				EXPECT(cfgspace[0x80 / 4] == 0x18001000u && wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18000000u));
				baseline_restored();
			}
		}

		/* ---- chip-state refusals and timeouts ---- */
		world(); chip_mode = 1;						/* SRPWR status never set: S34 times out after <= 150 reads, no wrapper write follows (S35 never starts) */
		EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S34") && has("restore=ok") && !has("!!!") && mem_wr_n == 1u && srpwr_cnt >= 100u && srpwr_cnt <= 160u && !has("LINK S35 start") && sm_rc == 1u && sm_io == 0u && mem_illegal_w == 0u);
		teardown_tail_is_table_order(34);
		baseline_restored();
		world(); chip_mode = 13;					/* SRPWR already on: the status is up at the first poll, the run is clean */
		EXPECT(wlan_link_run() == 0 && has("restore=ok") && mem_wr_n == nmw);
		baseline_restored();
		world(); chip_mode = 14;					/* PowerDomain id [31:28] = 0: no SRPWR capability, the RMW is refused at row 1345 (review 16 finding 2), nothing written */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S34") && has("restore=ok") && mem_wr_n == 0u && !has("LINK S35 start") && mem_illegal_w == 0u);
		teardown_tail_is_table_order(34);
		baseline_restored();
		world(); chip_mode = 15;					/* S34 skip (the run w3a-n34 state 0x00030300: id 0, SRPWR domain 1 already requested and up): the request is skipped, NOT written, the read-back and the status wait pass, the run is clean */
		EXPECT(wlan_link_run() == 0 && has("skip: SRPWR request") && has("restore=ok") && mem_wr_n == nmw - 1u && mem_illegal_w == 0u);
		baseline_restored();
		world(); chip_mode = 18;					/* the same with PowerDomain id 3: skipped too */
		EXPECT(wlan_link_run() == 0 && has("skip: SRPWR request") && has("restore=ok") && mem_wr_n == nmw - 1u && mem_illegal_w == 0u);
		baseline_restored();
		world(); chip_mode = 16;					/* ExtPwrStatus bit 17 alone (PowerOnRequest bit 9 clear, id 0): no skip, the write is refused (id 0), nothing written */
		EXPECT(wlan_link_run() == 1 && !has("skip: SRPWR request") && has("stopped=refused at=S34") && has("restore=ok") && mem_wr_n == 0u && mem_illegal_w == 0u);
		baseline_restored();
		world(); chip_mode = 17;					/* PowerOnRequest bit 9 alone (status bit 17 clear, id 0): no skip, refused, nothing written */
		EXPECT(wlan_link_run() == 1 && !has("skip: SRPWR request") && has("stopped=refused at=S34") && has("restore=ok") && mem_wr_n == 0u && mem_illegal_w == 0u);
		baseline_restored();
		world(); chip_mode = 12;					/* powerctl reads all ones (dead): the RMW is refused (old != 0xFFFFFFFF), nothing written */
		EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S34") && has("restore=ok") && mem_wr_n == 0u && !has("LINK S35 start") && mem_illegal_w == 0u);
		teardown_tail_is_table_order(34);
		baseline_restored();
		if (WLAN_LINK_N >= 35) {
			world(); chip_mode = 11;				/* SYS_MEM RESET_ST stuck 1 from the start: the first wait times out BEFORE any wrapper write */
			EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S35") && has("restore=ok") && mem_wr_n == 1u && sm_rc == 1u && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18026000u));
			teardown_tail_is_table_order(35);
			baseline_restored();
			world(); chip_mode = 4;					/* RESET_ST sticks after the assert: timeout before the IOCTL write */
			EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S35") && mem_wr_n == 2u && sm_io == 0u && has("restore=ok"));
			teardown_tail_is_table_order(35);
			baseline_restored();
			world(); chip_mode = 3;					/* the IOCTL write does not take: refused at its read-back */
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S35") && mem_wr_n == 3u && has("restore=ok"));
			baseline_restored();
			world(); chip_mode = 2;					/* RESET_CTL release does not take: timeout (bounded), no FINAL IOCTL write, the core page never selected */
			EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S35") && mem_wr_n == 4u && sm_io == 3u && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18026000u) && has("restore=ok"));
			teardown_tail_is_table_order(35);
			baseline_restored();
			world(); chip_mode = 10;				/* the final IOCTL keeps FGC (reads 3): refused at the read-back, the up gate never passes */
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S35") && mem_wr_n == 5u && !wrote(WLAN_CFG + 0x80, FULL_MASK_T, 0x18026000u) && has("restore=ok"));
			baseline_restored();
			for (int m = 5; m <= 7; m++) {				/* coreinfo all ones / a ROM bank / no RAM bank: bankidx is REFUSED by the allowlist (nothing written), the run stops, restore ok */
				world(); chip_mode = m;
				EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S35") && has("restore=ok") && !has("!!!") && mem_wr_n == 5u && sm_bankidx == 3u && mem_illegal_w == 0u && !has("LINK S36 start"));
				teardown_tail_is_table_order(35);
				baseline_restored();
			}
			world(); sm_io = 1u; sm_rc = 0u; sm_bankidx = 0u;		/* SYS_MEM already up (not the observed state): the sequence is idempotent and the run is clean */
			EXPECT(wlan_link_run() == 0 && has("restore=ok") && mem_wr_n == nmw && sm_io == 1u && sm_rc == 0u);
			baseline_restored();
		}
		if (WLAN_LINK_N >= 36) {
			world(); chip_mode = 9;					/* ARM IOCTL write does not take */
			EXPECT(wlan_link_run() == 1 && has("stopped=refused at=S36") && mem_wr_n == 8u && has("restore=ok") && arm_io == 0u);
			teardown_tail_is_table_order(36);
			baseline_restored();
			world(); chip_mode = 8;					/* ARM RESET_CTL release does not take: timeout */
			EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S36") && mem_wr_n == 9u && has("restore=ok") && arm_io == 0x23u);
			baseline_restored();
		}
		for (unsigned wi = 0; wi < 4u; wi++) {				/* each NEW window move (S34 core page, S35 wrapper, S35 core page, S36 ARM): fault / swallowed / latches another page. win_which selects the S34..S36 write (S33 wrote 0x18126000 and 0x18120000 first) */
			static const uint32_t mvv[4] = { 0x18001000u, 0x18126000u, 0x18026000u, 0x18120000u };
			static const unsigned mvs[4] = { 34u, 35u, 35u, 36u }, mvn[4] = { 1u, 2u, 1u, 2u };
			unsigned wrow = 0;

			if (mvs[wi] > WLAN_LINK_N)
				continue;
			for (unsigned i = 0, nthv = 0; i < WLAN_SEQ_NROWS; i++)
				if (get_row(i, 0, &r) && r.kind == WLK_MMIO_WRITE && r.blk == WLB_CFG && r.off == 0x80u && r.val == mvv[wi] && r.step >= 33u && ++nthv == mvn[wi]) {
					wrow = i;
					EXPECT(r.step == mvs[wi]);
				}
			EXPECT(wrow);
			world(); win_fault_v = (int)mvv[wi]; win_which = mvn[wi];
			snprintf(b, sizeof b, "LINK S%02u fault row=%u", mvs[wi], wrow);
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal_w == 0u && mem_wr_n == (wi == 0 ? 0u : wi == 1 ? 1u : wi == 2 ? 5u : 6u));
			teardown_tail_is_table_order(mvs[wi]);
			baseline_restored();
			world(); win_ign_v = (int)mvv[wi]; win_which = mvn[wi];
			snprintf(b, sizeof b, "stopped=refused at=S%02u row=%u", mvs[wi], wrow + 1u);
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=ok") && !has("!!!") && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal_w == 0u && mem_wr_n == (wi == 0 ? 0u : wi == 1 ? 1u : wi == 2 ? 5u : 6u));
			teardown_tail_is_table_order(mvs[wi]);
			baseline_restored();
			world(); win_lat_v = (int)mvv[wi]; win_which = mvn[wi];	/* latches another page: the read-back refuses; the restore from the unknown page is refused by the allowlist (loud), the final S29 restore still runs */
			EXPECT(wlan_link_run() == 1 && has(b) && has("restore=FAILED") && has("!!! RESTORE FAILED") && cfgspace[0x80 / 4] == 0x18001000u && mem_illegal_w == 0u && mem_wr_n == (wi == 0 ? 0u : wi == 1 ? 1u : wi == 2 ? 5u : 6u));
		}
	}
#endif

	/* ---- failure branches: each stops, tears down, is loud where it must be ---- */
	world(); fault_read_pa = 0;
	gp14.ext |= 4;						/* WLAN_EN reads high: the precondition refuses, nothing written */
	EXPECT(wlan_link_run() == 1 && nwrites == 0 && has("LINK S00 refused row=0") && has("LINK summary reached=S00 stopped=refused at=S00 row=0 restore=ok"));
	world(); gh2.ext |= 1;
	EXPECT(wlan_link_run() == 1 && nwrites == 0 && has("LINK S00 refused"));
	world(); iso1 = 1;
	EXPECT(wlan_link_run() == 1 && nwrites == 0 && nev == 0 && has("LINK S00 refused"));
	world(); gp14.con = 0x133;
	EXPECT(wlan_link_run() == 1 && nwrites == 0 && has("LINK S00 refused"));

#if WLAN_LINK_N >= 8
	world(); unreadable_blk = 1;				/* the PMA probe fails: no PMA write, teardown touches no PMA */
	EXPECT(wlan_link_run() == 1 && has("LINK S08 fault row=23") && has("stopped=fault at=S08 row=23 restore=ok"));
	writes_are_table_rows();
	EXPECT(last_ev(EV_W, WLAN_PMA, WLAN_PMA + 0x2000) == 0 && last_ev(EV_ISO_CLR, 0, 0) && !wrote(WLAN_PMA, 0, 0));
	baseline_restored();

#endif
#if WLAN_LINK_N >= 15
	world(); never_pll = 1;					/* the PLL never locks: S15 timeout at its row, soft reset + PHY power-down + isolation clear LAST */
	EXPECT(wlan_link_run() == 1 && has("LINK S15 timeout row=214") && has("stopped=timeout at=S15 row=214 restore=ok"));
	EXPECT(wrote(WLAN_PMA + 0x400, 0x80, 0) && wrote(WLAN_ELBI + 0x3a4, 2, 0) && last_ev(EV_ISO_CLR, 0, 0) > last_ev(EV_W, WLAN_PMA, WLAN_PMA + 0x2000));
	EXPECT(!wrote(WLAN_ELBI + 0x80, 0xffffffff, 4));	/* S17 never ran */
	baseline_restored();

#endif
#if WLAN_LINK_N >= 14
	world(); unreadable_blk = 2;				/* the I/A probe fails (S14) */
	EXPECT(wlan_link_run() == 1 && has("LINK S14 fault") && wrote(WLAN_PMA + 0x400, 0x80, 0) && last_ev(EV_W, WLAN_IA, WLAN_IA + 0x1000) == 0);
	baseline_restored();
#endif
#if WLAN_LINK_N >= 19
	world(); unreadable_blk = 4;				/* the DBI probe fails (S19): no DBI write */
	EXPECT(wlan_link_run() == 1 && has("LINK S19 fault") && last_ev(EV_W, WLAN_DBI, WLAN_DBI + 0x1000) == 0);
	baseline_restored();

#endif
#if WLAN_LINK_N >= 19
	world(); write_fault_n = 40;				/* a write faults in the middle of the PHY tables */
	EXPECT(wlan_link_run() == 1 && has("fault row=") && has("restore=ok"));
	baseline_restored();

#endif
	world(); iso_set_ret = 2;				/* the isolation release fails (fake EL3 mismatch): stop at S05, the clear still runs */
	EXPECT(wlan_link_run() == 1 && has("LINK S05 fault") && last_ev(EV_ISO_CLR, 0, 0) && last_ev(EV_W, WLAN_PMA, WLAN_PMA + 0x2000) == 0);
	baseline_restored();
	world(); iso_set_ret = 1;				/* refused before any RMW */
	EXPECT(wlan_link_run() == 1 && has("LINK S05 refused"));
	baseline_restored();

	world(); ignore_pa_set = 1; ignore_pa = WLAN_GPP14 + 4;	/* WLAN_EN cannot be driven: S02/S03 read-back mismatch -> fault, teardown loud */
	EXPECT(wlan_link_run() == 1 && has("LINK S03 fault"));

	world(); iso_clear_ret = 2;				/* the isolation clear fails: LOUD */
	EXPECT(wlan_link_run() == 1 && has("restore=FAILED") && has("!!! RESTORE FAILED") && has("LINK T"));
#if WLAN_LINK_N >= 8
	world(); ignore_pa_set = 1; ignore_pa = WLAN_PMA + 0x400;	/* the PHY power-down RMW does not take: writes are not read back, but the run is still ok */
	EXPECT(wlan_link_run() == 0);
#endif
	world(); write_fault_n = 1;				/* the very first write faults */
	EXPECT(wlan_link_run() == 1 && has("LINK S01 fault"));
	world(); freq = 0;
	EXPECT(wlan_link_run() == 2 && nwrites == 0 && has("no timer"));
#if WLAN_LINK_N >= 19
	/* fault injection at EVERY forward write row (S00..N): the k-th try_write32 fails; the run stops there with `fault`, the teardown runs in table order, restore=ok, PERST ends low */
	{
		unsigned fwd = 0, ids[400], steps[400];

		for (unsigned i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step <= WLAN_LINK_N && (r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON || r.kind == WLK_MMIO_RMW || r.kind == WLK_MMIO_WRITE)) {
				EXPECT(fwd < 400);
				ids[fwd] = i; steps[fwd++] = r.step;
			}
		EXPECT(fwd > 200);
		for (unsigned k = 1; k <= fwd; k++) {
			char b[96];

			world(); write_fault_n = (int)k;
			EXPECT(wlan_link_run() == 1);
			snprintf(b, sizeof b, "stopped=fault at=S%02u row=%u restore=ok", steps[k - 1], ids[k - 1]);
			EXPECT(has(b) && !has("!!!") && reached_step() == steps[k - 1]);
			writes_are_table_rows();
			if (steps[k - 1] >= 15)
				teardown_tail_is_table_order(steps[k - 1]);
			baseline_restored();
			perst_low_at_end();
		}
	}
#endif
#if WLAN_LINK_N >= 20
	world(); ignore_pa_set = 1; ignore_pa = WLAN_GPH2 + 4;		/* PERST cannot be driven high: the S20 read-back mismatches -> fault at S20, teardown in order */
	EXPECT(wlan_link_run() == 1 && has("LINK S20 fault") && reached_step() == 20 && has("restore=ok"));
	teardown_tail_is_table_order(20);
	baseline_restored();
#endif
#if WLAN_LINK_N >= 23
	world(); never_l0 = 1;						/* the link never comes up: bounded wait -> `timeout` at S23, LTSSM disabled, PERST low, restore=ok, no hang */
	EXPECT(wlan_link_run() == 1 && has("LINK S23 timeout row=238") && has("stopped=timeout at=S23 row=238 restore=ok") && !has("!!!"));
	/* MAX_TIMEOUT (common.h:20) = 4800 polls is the cap; poll_row's tick guard (counter_freq / 1e6 truncates 24.576 MHz to 24 ticks/us) ends the wait ~2% earlier, never later */
	EXPECT(l0_polls <= 4800u && l0_polls >= 4700u);
	EXPECT(waited_us - waited_at_en >= 4800u * 10u && waited_us - waited_at_en <= 4800u * 13u);	/* ~MAX_TIMEOUT * usleep_range(10, 12): 48..58 ms, not the old guess of seconds */
	teardown_tail_is_table_order(23);
	baseline_restored();
	world(); fault_l0_read = 1;					/* the ELBI becomes unreadable while polling: fault at S23, same teardown */
	EXPECT(wlan_link_run() == 1 && has("LINK S23 fault row=238") && has("restore=ok") && l0_polls == 1u);
	teardown_tail_is_table_order(23);
	baseline_restored();
	world(); l0_after = 4000;					/* a late L0 (poll 4000, ~48 ms) is still ok; one that never comes is a timeout */
	EXPECT(wlan_link_run() == 0 && l0_polls == 4000u + L0X);
	world(); l0_after = 4801;
	EXPECT(wlan_link_run() == 1 && has("stopped=timeout at=S23"));
	world(); freq = 1000000;					/* a slow counter does not turn the wait into a hang: still <= 4800 polls */
	EXPECT(wlan_link_run() == 0 || l0_polls <= 4800u);
#endif
	puts("PASS wlanlink host test N=" WLAN_LINK_N_STR);
	return 0;
}
