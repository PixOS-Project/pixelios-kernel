/*
 * wlanlink.c - the table-driven BCM4389 + PCIe1 power-up interpreter (W3a), see wlanlink.h. Freestanding, no static state (the table is const).
 *
 * Every value it uses comes from out/wlan_seq.h = soc/google/gs101/wlan_seq.json (tools/gen_wlan_seq.py, cites per row in the JSON). This file adds an INDEPENDENT allowlist (w32_allowed: blocks, offsets,
 * pin masks), so a corrupted table or index cannot write anywhere else, and the runtime rules:
 *  - a block (PMA, PCS, ELBI, IA, DBI, ATU, CFG) is touched only after its probe row (a fault-guarded READ) succeeded in this run (docs/research/29 section 14: first unreadable => stop);
 *  - rows are executed in table order, the run stops at the first failing row, the teardown always runs for the steps that were started;
 *  - delays are bounded (<= 250 ms), polls are bounded by count AND by the counter.
 * Window sizes: GPP14/GPH2 banks = CON..PUDPDN (+0x00..+0x14, pinctrl-exynos-arm64.c:47-50), PMA 0x2000, PCS 0x1000, ELBI 0x2000, I/A 0x1000, DBI window 0x1000 (gs101-pcie.dtsi:57-63), ATU 0x1000 (DBI + 0x300000, pcie-designware.h:202, inside the 0x301000 DBI reg of gs101-pcie.dtsi:60), CFG 0x2000 (the 'config' reg, gs101-pcie.dtsi:62).
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "pstore.h"
#include "wlansmc.h"
#include "wlanlink.h"

#if SOC_GS101 && defined(WLAN_LINK_RUN) && defined(WLAN_GPP14) && defined(WLAN_GPH2) && defined(WLAN_PMA) && defined(WLAN_PCS) && defined(WLAN_ELBI) && defined(WLAN_IA) && defined(WLAN_DBI) && defined(WLAN_ATU) && defined(WLAN_CFG) && \
	defined(WLAN_PMU_ALIVE) && defined(WLAN_GPP14_EN_PIN) && defined(WLAN_GPH2_PERST_PIN)

#include "wlan_seq.h"

/* the two new S26 blocks: ATU is the DBI window's iATU part (pcie-designware.h:202 DEFAULT_DBI_ATU_OFFSET = 0x3 << 20), CFG is the pcie_1 'config' reg (gs101-pcie.dtsi:62), pinned apart from soc.json */
_Static_assert(WLAN_ATU == WLAN_DBI + 0x300000u, "WLAN_ATU must be WLAN_DBI + 0x300000");
_Static_assert(WLAN_CFG == 0x60FFE000UL, "WLAN_CFG must be the pcie_1 config window 0x60FFE000");

#ifndef WLAN_LINK_N
#define WLAN_LINK_N	WLAN_SEQ_DEFAULT_N
#endif
_Static_assert(WLAN_LINK_N <= WLAN_SEQ_LAST, "--wlan-link=N: N beyond the last step of the table");

#define DELAY_MAX_US	250000u
#define POLL_CAP	20000u
#define ST_OK		0
#define ST_TIMEOUT	1
#define ST_FAULT	2
#define ST_REFUSED	3

__attribute__((noipa)) static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char t[10];
	unsigned n = 0;

	t[n++] = (char)('0' + v % 10u);
	v /= 10u;
	for (unsigned i = 1; i < sizeof(t) && v; i++) {
		t[n++] = (char)('0' + v % 10u);
		v /= 10u;
	}
	while (n && pos + 1 < cap)
		dst[pos++] = t[--n];
	return pos;
}

__attribute__((noipa)) static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint32_t v, unsigned digits)
{
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++) {
		unsigned d = (v >> (4 * (digits - 1 - i))) & 0xfu;

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("WLAN", buf);
}

/* "LINK <pfx><nn> <what>" with an optional "row=<i>" and "rd=0x.." tail */
static void step_line(char pfx, unsigned n, const char *what, int have_row, unsigned row, int have_rd, uint32_t rd, int have_rows, unsigned rows)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "LINK ");

	line[p++] = pfx;
	p = put_dec(line, sizeof(line), p, n / 10u);
	p = put_dec(line, sizeof(line), p, n % 10u);
	if (what[0]) {
		p = put_txt(line, sizeof(line), p, " ");
		p = put_txt(line, sizeof(line), p, what);
	}
	if (have_rows) {
		p = put_txt(line, sizeof(line), p, " rows=");
		p = put_dec(line, sizeof(line), p, rows);
	}
	if (have_row) {
		p = put_txt(line, sizeof(line), p, " row=");
		p = put_dec(line, sizeof(line), p, row);
	}
	if (have_rd) {
		p = put_txt(line, sizeof(line), p, " rd=0x");
		p = put_hex(line, sizeof(line), p, rd, 8);
	}
	emit(line, p);
}

static const char *st_name(int st)
{
	if (st == ST_OK)
		return "ok";
	if (st == ST_TIMEOUT)
		return "timeout";
	if (st == ST_REFUSED)
		return "refused";
	return "fault";
}

/* ---- the table ---- */
static int get_row(unsigned idx, int tear, struct wlan_row *o)
{
	if (tear ? idx >= WLAN_SEQ_NTEAR : idx >= WLAN_SEQ_NROWS)
		return 0;
	o->step = tear ? wlan_tear[idx].step : wlan_rows[idx].step;
	o->kind = tear ? wlan_tear[idx].kind : wlan_rows[idx].kind;
	o->blk = tear ? wlan_tear[idx].blk : wlan_rows[idx].blk;
	o->off = tear ? wlan_tear[idx].off : wlan_rows[idx].off;
	o->mask = tear ? wlan_tear[idx].mask : wlan_rows[idx].mask;
	o->val = tear ? wlan_tear[idx].val : wlan_rows[idx].val;
	o->a = tear ? wlan_tear[idx].a : wlan_rows[idx].a;
	o->b = tear ? wlan_tear[idx].b : wlan_rows[idx].b;
	return 1;
}

/* ---- blocks ---- */
static uintptr_t base_of(unsigned blk)
{
	if (blk == WLB_GPP14)
		return WLAN_GPP14;
	if (blk == WLB_GPH2)
		return WLAN_GPH2;
	if (blk == WLB_PMA)
		return WLAN_PMA;
	if (blk == WLB_PCS)
		return WLAN_PCS;
	if (blk == WLB_ELBI)
		return WLAN_ELBI;
	if (blk == WLB_IA)
		return WLAN_IA;
	if (blk == WLB_DBI)
		return WLAN_DBI;
	if (blk == WLB_PMU)
		return WLAN_PMU_ALIVE;
	if (blk == WLB_ATU)
		return WLAN_ATU;
	if (blk == WLB_CFG)
		return WLAN_CFG;
	return 0;
}

static uint32_t size_of(unsigned blk)
{
	if (blk == WLB_GPP14 || blk == WLB_GPH2)
		return 0x18u;
	if (blk == WLB_PMA || blk == WLB_ELBI || blk == WLB_CFG)
		return 0x2000u;
	if (blk == WLB_PCS || blk == WLB_IA || blk == WLB_DBI || blk == WLB_ATU)
		return 0x1000u;
	if (blk == WLB_PMU)
		return 0x4000u;
	return 0u;
}

/* the blocks that may only be touched after their probe read worked */
static unsigned need_probe(unsigned blk)
{
	return (blk == WLB_PMA || blk == WLB_PCS || blk == WLB_ELBI || blk == WLB_IA || blk == WLB_DBI || blk == WLB_ATU || blk == WLB_CFG) ? 1u << blk : 0u;
}

static uintptr_t pa_of(unsigned blk, uint32_t off)
{
	uintptr_t b = base_of(blk);

	if (!b || (off & 3u) || off >= size_of(blk))
		return 0;
	return b + off;
}

static int r32(unsigned blk, uint32_t off, uint32_t *v)
{
	uintptr_t pa = pa_of(blk, off);

	if (!pa)
		return 0;
	if (blk == WLB_CFG && off >= 0x1000u)	/* PCIe config space is 4 KiB (linux/pci_regs.h:30 PCI_CFG_SPACE_EXP_SIZE); the window is 8 KiB, bit 12 is not a config register */
		return 0;
	if (blk == WLB_PMU && off != 0x3EC0u && off != 0x3EC4u)	/* the only PMU words this file reads */
		return 0;
	return try_read32(pa, v);
}

/* ---- the independent write allowlist ---- */
static uint32_t gpio_field_mask(uint32_t off, unsigned pins)
{
	uint32_t m = 0, w;

	if (off == 0x00u || off == 0x08u || off == 0x0cu || off == 0x14u)
		w = 4u;
	else if (off == 0x04u)
		w = 1u;
	else if (off == 0x10u)
		w = 2u;
	else
		return 0;
	for (unsigned pin = 0; pin < 8u; pin++)
		if (pins & (1u << pin))
			m |= ((1u << w) - 1u) << (pin * w);
	return m;
}

static int elbi_ok(uint32_t off)
{
	return off == 0x054u || off == 0x080u || off == 0x388u || off == 0x3a0u || off == 0x3a4u || off == 0x3a8u || off == 0x3bcu || off == 0x474u || off == 0x1078u ||
	       off == 0x1400u || off == 0x1404u || off == 0x1408u;
}

/* ATU outbound region 0 (rc.h:286-292): only the seven registers of S26 and only the values of S26 or its teardown (research 31 s.2.2; reset values observed in run w3a-n25); a full-word write only */
static int atu_ok(uint32_t off, uint32_t v)
{
	return (off == 0x000u && (v == 0x4u || v == 0u)) || (off == 0x004u && (v == 0x80000000u || v == 0u)) || (off == 0x008u && (v == (uint32_t)WLAN_CFG || v == 0u)) ||
	       (off == 0x00cu && v == 0u) || (off == 0x010u && (v == (uint32_t)WLAN_CFG + 0x1fffu || v == 0xfffu)) || (off == 0x014u && (v == 0x01000000u || v == 0u)) || (off == 0x018u && v == 0u);
}

/* Endpoint config BARs through the CFG window (S28, research 33): ONLY offsets 0x10/0x14/0x18/0x1C (never Command 0x04), a full-word write, and only these transitions: the observed original (low 0x4, high 0)
 * from ANY state (the restore, even if Command is not 0); otherwise Command (cmd) must have MEM/IO decode off, and all ones only from the original, the assigned address only for the low half and only from
 * the original. Linux sizes with decode off too (probe.c:188-191). */
static int cfg_ok(uint32_t off, uint32_t old, uint32_t v, uint32_t cmd)
{
	int lo = (off == 0x10u || off == 0x18u), hi = (off == 0x14u || off == 0x1cu);

	if (!lo && !hi)
		return 0;
	if (v == (lo ? 0x4u : 0u))
		return 1;
	if ((cmd & 0xFFFFu) != 0u)
		return 0;
	if (v == 0xFFFFFFFFu)
		return old == (lo ? 0x4u : 0u);
	return lo && old == 0x4u && v == (off == 0x10u ? 0x60000004u : 0x60400004u);
}

/* v may differ from old only inside mask; pa must be inside one of the writable windows, with the per-block rules. cmd = the EP Command dword (read by w32 for CFG rows only). */
static int w32_allowed(uintptr_t pa, uint32_t mask, uint32_t old, uint32_t v, uint32_t cmd)
{
	if (((old ^ v) & ~mask) != 0u)
		return 0;
	if (pa >= WLAN_GPP14 && pa < WLAN_GPP14 + 0x18u)
		return (mask & ~gpio_field_mask((uint32_t)(pa - WLAN_GPP14), 1u << (unsigned)WLAN_GPP14_EN_PIN)) == 0u && mask != 0u;
	if (pa >= WLAN_GPH2 && pa < WLAN_GPH2 + 0x18u)
		return (mask & ~gpio_field_mask((uint32_t)(pa - WLAN_GPH2), (1u << (unsigned)WLAN_GPH2_PERST_PIN) | 2u)) == 0u && mask != 0u;
	if (pa >= WLAN_PMA && pa < WLAN_PMA + 0x2000u)
		return !(pa & 3u);
	if (pa >= WLAN_PCS && pa < WLAN_PCS + 0x1000u)
		return !(pa & 3u);
	if (pa >= WLAN_IA && pa < WLAN_IA + 0x180u)
		return !(pa & 3u);
	if (pa >= WLAN_ELBI && pa < WLAN_ELBI + 0x2000u)
		return elbi_ok((uint32_t)(pa - WLAN_ELBI));
	if (pa == WLAN_DBI + 0x890u)
		return 1;
	if (pa >= WLAN_ATU && pa < WLAN_ATU + 0x1000u)
		return mask == 0xFFFFFFFFu && atu_ok((uint32_t)(pa - WLAN_ATU), v);
	if (pa >= WLAN_CFG && pa < WLAN_CFG + 0x1000u)
		return mask == 0xFFFFFFFFu && cfg_ok((uint32_t)(pa - WLAN_CFG), old, v, cmd);
	return 0;
}

/* The ONLY writer: the row (kind, block, offset, mask, value) comes from the const table by index; GPIO / RMW rows read-modify-write the masked bits, full writes just write. noipa +
 * noinline-by-symbol: the one try_write32 call site lives here and the object audit checks the compiled compare constants. 0 = written, -1 = fault, -2 = refused (nothing written). */
__attribute__((noipa)) static int w32(unsigned idx, int tear)
{
	struct wlan_row r;
	uintptr_t pa;
	uint32_t old = 0, v, cmd = 0;

	if (!get_row(idx, tear, &r))
		return -2;
	if (r.kind != WLK_GPIO_DAT && r.kind != WLK_GPIO_CON && r.kind != WLK_MMIO_RMW && r.kind != WLK_MMIO_WRITE)
		return -2;
	if (!tear && r.step > WLAN_LINK_N)
		return -2;
	if (r.kind == WLK_MMIO_WRITE && r.mask != 0xFFFFFFFFu)	/* a full write has no old value to check the mask rule against: only whole-word rows */
		return -2;
	pa = pa_of(r.blk, r.off);
	if (!pa)
		return -2;
	if (r.kind == WLK_MMIO_WRITE && r.blk != WLB_CFG) {
		old = r.val;
	} else if (!try_read32(pa, &old)) {		/* CFG writes check the real old value (BAR transitions) */
		return -1;
	}
	if (r.blk == WLB_CFG && !try_read32(WLAN_CFG + 4u, &cmd))
		cmd = 0xFFFFu;			/* Command unreadable: treat decode as on, only the restores pass */
	v = (old & ~r.mask) | (r.val & r.mask);
	if (!w32_allowed(pa, r.mask, old, v, cmd))
		return -2;
	return try_write32(pa, v) ? 0 : -1;
}

static void delay_us(uint32_t us)
{
	if (us)
		wait_ticks(((uint64_t)counter_freq() * us + 999999u) / 1000000u);	/* round up: delays are minimums (24.576 MHz counter is not a multiple of 1 MHz) */
}

/* poll a read-modify condition: wait_eq (v & mask) == val, wait_mask (v & mask) == mask; b polls (<= POLL_CAP) and the counter both bound it */
static int poll_row(const struct wlan_row *r, uint32_t *rd)
{
	uint64_t f = counter_freq() / 1000000u, t0 = touch_ticks();
	uint64_t lim = f * (uint64_t)r->b * (uint64_t)(r->a ? r->a : 1u) + f * 1000u;

	if (r->a > DELAY_MAX_US || (uint64_t)r->a * (r->b < POLL_CAP ? r->b : POLL_CAP) > 10u * DELAY_MAX_US)	/* total wait <= 2.5 s (watchdog is off) */
		return ST_REFUSED;
	for (unsigned i = 0; i < r->b && i < POLL_CAP; i++) {
		uint32_t v = 0;

		if (!r32(r->blk, r->off, &v))
			return ST_FAULT;
		*rd = v;
		if (r->kind == WLK_WAIT_MASK ? (v & r->mask) == r->mask : (v & r->mask) == r->val)
			return ST_OK;
		if (r->a)
			delay_us(r->a);
		if (touch_ticks() - t0 > lim)
			break;
	}
	return ST_TIMEOUT;
}

/* one row; *probed is the set of blocks whose probe worked */
static int run_row(unsigned idx, int tear, unsigned *probed, uint32_t *rd)
{
	struct wlan_row r;
	uint32_t v = 0;
	int e, st;

	if (!get_row(idx, tear, &r))
		return ST_REFUSED;
	if (r.blk != WLB_NONE && (need_probe(r.blk) & ~*probed) && !(r.kind == WLK_READ_ONLY && r.a == 1u))
		return ST_REFUSED;		/* a block is touched only after its probe read worked */
	if (r.kind == WLK_DELAY) {
		if (r.a > DELAY_MAX_US)
			return ST_REFUSED;
		delay_us(r.a);
		return ST_OK;
	}
	if (r.kind == WLK_READ_ONLY) {
		if (!r32(r.blk, r.off, &v))
			return ST_FAULT;
		*rd = v;
		if (r.a == 1u)
			*probed |= need_probe(r.blk);
		if (r.a == 2u)		/* log flag (a = 2: a non-probe row whose value is printed) */
			step_line('R', r.step, "", 1, idx, 1, v, 0, 0);
		return ST_OK;
	}
	if (r.kind == WLK_WAIT_EQ || r.kind == WLK_WAIT_MASK) {
		st = poll_row(&r, rd);
		return (st != ST_OK && r.b == 1u) ? (st == ST_TIMEOUT ? ST_REFUSED : st) : st;	/* a one-shot precondition that does not hold is a refusal */
	}
	if (r.kind == WLK_SMC_ISO) {
		if (r.blk != WLB_PMU || r.off != 0x3EC4u || r.mask != 1u)
			return ST_REFUSED;
		e = (r.val & 1u) ? wlan_link_iso_set(rd) : wlan_link_iso_clear(rd);
		return e == 0 ? ST_OK : (e == 1 ? ST_REFUSED : ST_FAULT);
	}
	e = w32(idx, tear);
	if (e == -2)
		return ST_REFUSED;
	if (e)
		return ST_FAULT;
	if (r.kind == WLK_GPIO_DAT || r.kind == WLK_GPIO_CON) {		/* the pin registers read back what was written (output DAT: the latch) */
		if (!r32(r.blk, r.off, &v))
			return ST_FAULT;
		*rd = v;
		if ((v & r.mask) != (r.val & r.mask))
			return ST_FAULT;
	}
	return ST_OK;
}

int wlan_link_run(void)
{
	char line[RAMLOG_LINE_MAX];
	unsigned probed = 0, hi = 0, started = 0, bad_row = 0, fail_step = 0, nrows = 0, s, i, p;
	uint32_t rd = 0;
	int stopped = ST_OK, st, tear_bad = 0;
	struct wlan_row r;

	p = put_txt(line, sizeof(line), 0, "LINK table sha=");
	for (i = 0; i < 16u; i++)
		line[p++] = (WLAN_SEQ_SHA256)[i];
	p = put_txt(line, sizeof(line), p, " rows=");
	p = put_dec(line, sizeof(line), p, WLAN_SEQ_NROWS);
	p = put_txt(line, sizeof(line), p, " N=");
	p = put_dec(line, sizeof(line), p, WLAN_LINK_N);
	emit(line, p);
	if (!counter_freq()) {
		con_str("WLAN", "LINK refused: no timer, nothing written");
		return 2;
	}
	for (s = 0; s <= WLAN_LINK_N; s++) {		/* the plan: every step number 0..N must have rows, else nothing runs */
		nrows = 0;
		for (i = 0; i < WLAN_SEQ_NROWS; i++)
			if (get_row(i, 0, &r) && r.step == s)
				nrows++;
		if (!nrows) {
			step_line('S', s, "refused: UNKNOWN step (no cited rows), nothing written", 0, 0, 0, 0, 0, 0);
			return 2;
		}
	}
	for (s = 0; s <= WLAN_LINK_N && stopped == ST_OK; s++) {
		unsigned done = 0;

		hi = s;
		started = 1;
		step_line('S', s, "start", 0, 0, 0, 0, 0, 0);
		pstore_save();			/* the last line of a hang names the step */
		for (i = 0; i < WLAN_SEQ_NROWS; i++) {
			if (!get_row(i, 0, &r) || r.step != s)
				continue;
			st = run_row(i, 0, &probed, &rd);
			if (st != ST_OK) {
				stopped = st;
				bad_row = i;
				fail_step = s;
				break;
			}
			done++;
		}
		if (stopped == ST_OK)
			step_line('S', s, "ok", 0, 0, 1, rd, 1, done);
		else
			step_line('S', s, st_name(stopped), 1, bad_row, 1, rd, 0, 0);
		pstore_save();
	}
	/* the teardown, always, for the steps that were started; best effort, loud */
	for (i = 0; started && i < WLAN_SEQ_NTEAR; i++) {
		if (!get_row(i, 1, &r) || r.step > hi)
			continue;
		if (need_probe(r.blk) & ~probed)
			continue;		/* a block that was never probed was never written (writes need the probe): nothing to undo there */
		st = run_row(i, 1, &probed, &rd);
		if (st != ST_OK) {
			tear_bad++;
			step_line('T', i + 1u, st_name(st), 0, 0, 1, rd, 0, 0);
		}
	}
	p = put_txt(line, sizeof(line), 0, "LINK summary reached=S");
	p = put_dec(line, sizeof(line), p, hi / 10u);
	p = put_dec(line, sizeof(line), p, hi % 10u);
	p = put_txt(line, sizeof(line), p, " stopped=");
	p = put_txt(line, sizeof(line), p, stopped == ST_OK ? "none" : st_name(stopped));
	if (stopped != ST_OK) {
		p = put_txt(line, sizeof(line), p, " at=S");
		p = put_dec(line, sizeof(line), p, fail_step / 10u);
		p = put_dec(line, sizeof(line), p, fail_step % 10u);
		p = put_txt(line, sizeof(line), p, " row=");
		p = put_dec(line, sizeof(line), p, bad_row);
	}
	p = put_txt(line, sizeof(line), p, tear_bad ? " restore=FAILED" : " restore=ok");
	emit(line, p);
	if (tear_bad)
		con_str("WLAN", "LINK !!! RESTORE FAILED: module/PHY may be LEFT on; see the T lines, reset the phone");
	pstore_save();
	return (stopped == ST_OK && !tear_bad) ? 0 : 1;
}

#else	/* not gs101 / --wlan-link not built: refuse, nothing written */

int wlan_link_run(void)
{
	con_str("WLAN", "LINK refused: --wlan-link not built or not gs101, nothing written");
	return 2;
}

#endif
