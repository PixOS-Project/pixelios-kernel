#!/usr/bin/env python3
"""wlan_glue_harness_a: part A of the C text of the glue-limit harness of tools/test_wlan_wr_mutants.py (moved out for R29 by T-4adb; GLUE_HARNESS = GLUE_HARNESS_A + GLUE_HARNESS_B, byte for byte the old text).

  tools/wlan_glue_harness_a --self-test    checks the part is plain C text without a triple quote
Last line: RESULT: PASS|FAIL wlan_glue_harness_a ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import sys

GLUE_HARNESS_A = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "sweet_wlan_wr.h"
#include "sweet_wlan_dma.h"
typedef unsigned long vm_offset_t;
#define __builtin_arm_dsb(x) ((void)0)
#ifdef SWG_FAILFAST     /* test-harness only (never shim/): the sweep ends at its FIRST failed check; a failure is exactly "bad != 0", so the kill set is unchanged (PLAN-GATE-2X D1) */
#define SWG_BAD(...) (printf(__VA_ARGS__), fflush(stdout), exit(1))
#else
#define SWG_BAD printf
#endif
struct sw_g2w {
	int space, need;
	uint32_t win, off, v, vmask, old, omask;
	uint8_t once, done, w16;
	uint8_t kind;
	uint32_t rep, stride, vrng, arg;
	uint8_t l24;
};
#define SW_G2K_SH_ITEM  1
#define SW_G2K_SH_ITEM0 2
#define SW_G2K_SH_WR    3
#define SW_G2K_SH_DB    4
#define SW_G2K_R_ITEM   5
#define SW_G2K_R_ITEM0  6
#define SW_G2K_R_WR     7
#define SW_G2K_R_WDB    8
#define SW_G2K_R_RD     9
#define SW_G2K_R_RDB    10
#define SW_G2K_VER      11
#define SW_G2K_SH_RBUF  12
#define SW_G2K_BLOB     13
#define SW_G2_E_PAYW    355u
#define SW_G2_CLMCHUNK  1400u
#define SW_G2_CLMMAX    12u
#define SW_G2K_E_ITEM   14
#define SW_G2K_E_WR     15
#define SW_G2_MAX   1816u
#define SWG_E_MAX        64u    /* T-4a7f: the header constants the glue's L24 code names (sweet_wlan_g2.h; test_glue_l24_header_restated checks them) */
#define SWG_E_MAX_CLM    48u
#define SWG_E_PAYW       355u
#define SWG_J_NCMD       6u
#define SWG_J_NCMAX      8u
struct swg_jcmd {
	uint32_t cmd;
	const char *row;
	uint8_t buf[4u * SWG_E_PAYW];
	uint32_t len;
};
#define SW_G2_DS_MAX    4u       /* glue: the cap of DS rounds (asserted equal to SWG_D4_DS_MAX in the glue; the test class checks this literal against the glue) */
#define SW3_DBI     16
#define SW3_HOST    17
/* T-2543 (T-43af L25 wiring): what the glue text now names from the L25 headers. The real sweet_wlan_l25_wl.h (the judge) is included; the types of the rest are restated to the one member the glue text reads
 * (swg_wx.elv, swl25g.cfg.bssid); sw_g2_rd / swl25g_wl_bssid are stubs after the glue text (a level-25 engine is not run here: g2x stays NULL, so sw_l25w_live is 0 and the L25 route is not taken) */
#include "sweet_wlan_l25_wl.h"
#define SW3_DATA    18
#define SW_G2_L25   0x1000
enum { SWG_SP_TCM = 1, SWG_SP_EP, SWG_SP_RC, SWG_SP_PCIE, SWG_SP_WIN2, SWG_SP_HOST, SWG_SP_DATA };
struct swg_wx { uint32_t elv; };
struct swc_creds { uint32_t x; };
struct swl25g { struct { uint8_t bssid[6]; } cfg; };
struct sw_hw {
	volatile uint32_t *cfg, *atu, *elbi, *iso, *mem, *tcm;
	uint32_t win;
	int level;
	int l3;
	uint32_t rv;
	int rv_ok;
	int rv_done;
	uint32_t rel;
	volatile uint32_t *dbi;     /* patch 0174 */
	int g2, g2ok;
	unsigned g2n;
	uint32_t g2ep;
	const uint8_t *g2clm;
	uint32_t g2clmlen;
	uint32_t g2clmpre[SW_G2_E_PAYW];
	struct sw_g2w g2l[SW_G2_MAX];
	vm_offset_t g2va;
	struct swd_plan g2plan;
	int d4b;
	int c88;
	int w15;
	uint32_t g2sh;
	uint32_t g2ev;
	uint32_t g2ev0;
	int g2esc;
	uint8_t g2rd;
	const struct swg_jcmd *jc;
	uint32_t njc;
	uint64_t g2pa;	uint64_t dpa;
	vm_offset_t dva;
	uint32_t dlen;
	struct swl25w l25w;
	const struct swg_wx *g2x;
	uint32_t l25old;
	int l25sp, l25cls;
	uint8_t l25arm, l25on;
	uint8_t l25guard[1u << 19];     /* T-3784 harness only (not in the patch struct, which has no member after l25on): zero filled, checked by the L25 accept block; a store through g2l[SW_G2_L25 + class] lands in it, not in other statics */
};
static struct { const uint8_t *p; uint32_t len, crc; } sw_clm;
static uint32_t swf_crc32(const uint8_t *p, size_t n)
{
	uint32_t c = 0xFFFFFFFFu;
	size_t i;
	unsigned k;

	for (i = 0; i < n; i++) {
		c ^= p[i];
		for (k = 0; k < 8; k++) {
			c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
		}
	}
	return ~c;
}
#include <stdarg.h>
static char g_out[1u << 16];    /* T-23da (review T-4a7f r1 F4): what the glue printed since the last g_out[0] = 0, g_trunc = 0 (every line is also echoed to stdout) */
static unsigned g_l24_nj, g_l24_neg;   /* T-84b0: the L24 summary numbers, printed last (the output tail the test reads is 600 chars) */
static int g_trunc;             /* 1 when a line did not fit in g_out (a check on g_out then fails, r2 F3) */
static int g_printf(const char *f, ...)
{
	char line[1024];
	va_list ap;
	size_t n = strlen(g_out), m;
	int r;

	va_start(ap, f);
	r = vsnprintf(line, sizeof line, f, ap);
	va_end(ap);
	fputs(line, stdout);
	m = strlen(line);
	if (r < 0 || (size_t)r >= sizeof line || n + m >= sizeof g_out) {
		g_trunc = 1;
	} else {
		memcpy(g_out + n, line, m + 1u);
	}
	return r;
}
#define printf g_printf
static uint32_t g_l25_rdlen;     /* T-3784: 0 = every judge read fails (the L25 route block, T-98d9); else the host words [0, g_l25_rdlen) of h->g2va are readable (the L25 accept block) */
static int sw_g2_rd(void *ctx, int sp, uint32_t a, uint32_t *v)       /* harness stand-in of the glue's flushing read: the judge reads the very words sw3_wr stores to */
{
	const struct sw_hw *h = ctx;

	if (g_l25_rdlen == 0u || sp != SWG_SP_HOST || (a & 3u) != 0u || a >= g_l25_rdlen || h == NULL || h->g2va == 0) {
		return -1;
	}
	*v = ((const uint32_t *)h->g2va)[a / 4u];
	return 0;
}
static void swl25g_wl_bssid(struct swl25w *w, const struct swl25g *g) { (void)w; (void)g; }
static unsigned g_wlflow_n;      /* T-3784: the flow-open scans sw3_wr asked for since the last reset, and the last (old, new) */
static uint32_t g_wlflow_old, g_wlflow_nw;
static void swl25g_wl_flow(struct swl25w *w, uint32_t old, uint32_t nw) { (void)w; g_wlflow_n++; g_wlflow_old = old; g_wlflow_nw = nw; }
@GLUE@

static void swh_use_l25(void) { (void)&sw_cred; }
#undef printf
@STREAMS@
static uint32_t cfg[0x1000 / 4], atu[0x1000 / 4], elbi[0x2000 / 4], iso[1], mem[0x1000 / 4], tcm[0x400000 / 4];
static uint32_t *const arr[] = { cfg, atu, elbi, iso, mem, tcm };
static const size_t len[] = { 0x1000 / 4, 0x1000 / 4, 0x2000 / 4, 1, 0x1000 / 4, 0x2008 / 4 };
static uint32_t snap[6][0x2008 / 4];

/* the table's offsets, restated: cfg 0x80, cfg 0x84 only as 0 or 0x400000; ATU region 2 CR2/LOWER_BASE/LIMIT/LOWER_TARGET; BAR0 bankidx on SYS_MEM, IOCTL + RESET_CTL on the two wrappers;
 * BAR2 is swept separately below (its offsets are above 0x200000, the main sweep only reaches 0x2004) */
static int expect(int space, uint32_t off, uint32_t win, int eq, uint32_t v)
{
	if (space == SWW_CFG) return off == 0x80 || (off == 0x84 && (v == 0 || v == 0x400000u));
	if (space == SWW_ATU) return off == 0x404 || off == 0x408 || off == 0x410 || off == 0x414;
	if (space == SWW_BAR0 && eq) {
		if (win == SWW_WIN_SYSMEM) return off == 0x10;
		if (win == SWW_WIN_SMWRAP) return off == 0x408 || off == 0x800;
		if (win == SWW_WIN_CA7WRAP) return 0;       /* the main sweep stores 0xDEADBEEF: not a value the CA7 wrapper may take (the stores it may take are swept below) */
	}
	return 0;
}

/* patch 0174, review r1 F1: the link words swl_pre_link reads (iso bit 0, ELBI 0x2C8 LTSSM 0x11, ATU r0 0x000..0x018, r1 0x200..0x218: sweet_wlan_chip.h); down = 0 all up, 1..4 breaks one */
static void g2link(int down)
{
	static const uint32_t w0[7] = { 0x4u, 0x80000000u, 0x60FFE000u, 0u, 0x60FFFFFFu, 0x01000000u, 0u };
	static const uint32_t w1[7] = { 0u, 0x80000000u, 0x60000000u, 0u, 0x6000FFFFu, 0x60000000u, 0u };

	iso[0] = down == 1 ? 0x40000000u : 0x40000001u;
	elbi[0x2C8 / 4] = down == 2 ? 0x30000010u : 0x30000011u;
	for (unsigned i = 0; i < 7; i++) {
		atu[i] = w0[i] ^ (down == 3 && i == 2 ? 0x1000u : 0u);
		atu[0x200 / 4 + i] = w1[i] ^ (down == 4 && i == 4 ? 0x1u : 0u);
	}
}

static int g2link_word(int a, size_t i)
{
	return (a == 1 && (i < 7 || (i >= 0x200 / 4 && i < 0x200 / 4 + 7))) || (a == 2 && (i == 0x2C8 / 4 || (i >= 0x10 / 4 && i <= 0x18 / 4))) || a == 3;
}

/* review 0174-w-d4b-hwwrite-r1 F9: the RC irq enables ELBI 0x10/0x14/0x18 all 0 (k < 0) or enable k set */
static void g2irq(int k)
{
	for (int j = 0; j < 3; j++) elbi[(0x10 + 4 * j) / 4] = j == k ? 0x1u : 0u;
}

static int g2pre(const char *a, const char *b)
{
	while (*b) if (*a++ != *b++) return 0;
	return 1;
}

/* review 0174-w-d4e-scan24-hwwrite-r1 F2 + F4: the REAL list as sw_g2_d4 builds it at d4b 7 (L14..L20 exact entries, sw_g2_d4d, then the masked sw_g2_ds entries) on `h`; the entries up to the L16 C2 doorbell entry are marked done (a stream that got that far),
 * that one too when done_c2. Returns the index of the C2 doorbell entry, or -1 */
static int real_list_lv(struct sw_hw *h, int done_c2, int d4b)
{
	int ready, c2 = -1;
	unsigned i;

	h->g2n = 0;
	h->g2sh = 0;
	h->g2ev = 0;
	h->g2ev0 = 0;
	h->g2rd = 0;
	h->g2esc = -1;
	h->d4b = d4b;
	ready = sw_g2_add(h, SWW_CFG, 0, 0x80u, 1u, 0xFFFFFFFFu, 0, 0, 0, -1);
	if (ready < 0 || sw_g2_d4(h, ready) < 0) return -1;
	for (i = 0; i < h->g2n; i++) {
		if (h->g2l[i].kind == 0 && h->g2l[i].space == SWW_BAR0 && h->g2l[i].off == 0x140u && h->g2l[i].v == SW_G2_D4C_DB0_RD2) c2 = (int)i;
	}
	if (c2 < 0) return -1;
	for (i = 0; i < (unsigned)c2 + (done_c2 ? 1u : 0u); i++) h->g2l[i].done = 1;
	return c2;
}

static int real_list(struct sw_hw *h, int done_c2)
{
	return real_list_lv(h, done_c2, 7);
}

/* the G2 state of a sweep case: link up, EP memory-only with BAR0 at the ATU target, window cfg 0x80 on the PCIe core, host block = mem2 (zero), slot W = 4, R = 2 (after L16) */
static void real_env(struct sw_hw *h, uint32_t *mem2, size_t nmem)
{
	const uint32_t ws = h->g2plan.b[SWD_IDX_H2D_W].off, rs = h->g2plan.b[SWD_IDX_D2H_R].off;

	for (int a = 0; a < 6; a++)
		for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
	memset(mem2, 0, nmem);
	mem2[ws / 4u] = 4u;
	mem2[rs / 4u] = 2u;
	g2link(0);
	g2irq(-1);
	cfg[0x04 / 4] = 2u;
	cfg[0x10 / 4] = 0x60000004u;
	cfg[0x14 / 4] = 0u;
	cfg[0x80 / 4] = SWW_WIN_PCIE;
	cfg[0x84 / 4] = 0u;
	h->g2va = (vm_offset_t)mem2;
	h->g2 = 1;
	h->l3 = 10;
	h->g2ok = 1;
	h->g2ep = 2u;
	h->rel = 7u;
	h->g2sh = 0;
	h->g2ev = 0;
	h->g2ev0 = 0;
	h->g2rd = 0;
}

/* T-8ede: replay L24 stream t (its own join table, or none when nojc) with store `hit` changed to v (or skipped when skip, with host word `pa` preset to pv after real_env); returns the index of the first refused store, n when all accepted, -1 when the list was not built */
static int l24_neg(struct sw_hw *h, uint32_t *rmem, size_t nmem, unsigned t, unsigned hit, uint32_t v, int skip, uint32_t pa, uint32_t pv, int nojc)
{
	unsigned k;

	h->jc = nojc ? NULL : rsets[t].jc;
	h->njc = nojc ? 0u : rsets[t].njc;
	if (real_list_lv(h, 1, rsets[t].d4b) < 0) return -1;
	real_env(h, rmem, nmem);
	if (skip) rmem[pa / 4u] = pv;
	for (k = 0; k < rsets[t].n; k++) {
		const struct rs *q = &rsets[t].s[k];
		const uint32_t w = k == hit ? v : q->v;

		if (skip && k == hit) continue;
		if ((q->host ? sw3_wr(h, SW3_HOST, 0u, q->a, w) : sw3_wr(h, SWW_BAR0, SWW_WIN_PCIE, q->a, w)) != 0) return (int)k;
	}
	return (int)k;
}

/* L22c (T-4adb), restated with literals (docs/research/108 s.9; not the glue's helpers): the payload of clmload chunk c of a blob of L bytes in chunks of 1400 B */
static uint32_t mc_len(uint32_t c, uint32_t L)
{
	return (c + 1u) * 1400u < L ? 1400u : L - c * 1400u;
}
static uint32_t mc_words(uint32_t c, uint32_t L)
{
	return (20u + mc_len(c, L) + 3u) / 4u;
}
static uint32_t mc_byte(uint32_t c, uint32_t q, uint32_t L)
{
	const uint32_t n = mc_len(c, L), flag = 0x1000u | (c == 0u ? 2u : 0u) | ((c + 1u) * 1400u >= L ? 4u : 0u);
	static const char nm[8] = "clmload";

	if (q < 8u) return (uint8_t)nm[q];
	if (q == 8u) return flag & 0xFFu;
	if (q == 9u) return flag >> 8;
	if (q == 10u) return 2u;
	if (q >= 12u && q < 16u) return (n >> (8u * (q - 12u))) & 0xFFu;
	if (q >= 20u && q - 20u < n) return sw_clm.p[c * 1400u + (q - 20u)];
	return 0u;
}
static uint32_t mc_word(uint32_t c, uint32_t i, uint32_t L)
{
	return mc_byte(c, 4u * i, L) | mc_byte(c, 4u * i + 1u, L) << 8 | mc_byte(c, 4u * i + 2u, L) << 16 | mc_byte(c, 4u * i + 3u, L) << 24;
}

int main(void)
{
	static const uint32_t wins[] = { SWW_WIN_PCIE, SWW_WIN_SYSMEM, SWW_WIN_SMWRAP, SWW_WIN_CA7WRAP, 0x18004000u };
	static const uint32_t vals[] = { 0xDEADBEEFu, 0u, 0x400000u };
	static const uint32_t bwins[] = { 0u, 0x400000u, 0x200000u, 0x800000u, 0x18001000u };
	struct sw_hw hw = { .cfg = cfg, .atu = atu, .elbi = elbi, .iso = iso, .mem = mem, .tcm = tcm, .win = 0, .level = 4 };
	int bad = 0, acc = 0;
	unsigned nneg24 = 0;

	(void)sw3_rd;
	for (unsigned wi = 0; wi < 5; wi++) {
		for (int eq = 0; eq < 2; eq++) {
			for (int space = 0; space < 8; space++) {
				for (uint32_t off = 0; off < 0x2004; off++) {
					for (unsigned vi = 0; vi < (space == SWW_CFG ? 3u : 1u); vi++) {
						for (int a = 0; a < 6; a++) {
							for (size_t i = 0; i < len[a]; i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
							memcpy(snap[a], arr[a], len[a] * 4);
						}
						cfg[0x80 / 4] = eq ? wins[wi] : wins[wi] ^ 0x1000u;
						memcpy(snap[0] + 0x80 / 4, &cfg[0x80 / 4], 4);
						int r = sw3_wr(&hw, space, wins[wi], off, vals[vi]);
						int ch = 0;
						for (int a = 0; a < 6; a++)
							for (size_t i = 0; i < len[a]; i++) ch += arr[a][i] != snap[a][i];
						int want = expect(space, off, wins[wi], eq, vals[vi]);
						if ((r == 0) != want || ch != (r == 0 ? 1 : 0)) {
							if (bad++ < 20) SWG_BAD("BAD space %d off %#x win %#x live-equal %d val %#x: ret %d changed %d, want accept %d\n", space, off, wins[wi], eq, vals[vi], r, ch, want);
						}
						acc += r == 0;
					}
				}
			}
		}
	}
	/* BAR2 (patch 0130): window 0 from 0x200000 to the end of the 4 MiB, window 0x400000 up to offset 0x1FFFFC (the last word of a 6 MiB RAM), only while the live cfg 0x84 equals the window,
	 * only 4-aligned, and never a store anywhere else: every other word of every array keeps its value (the target word is restored after each call; the sweep ends with a full compare) */
	for (int a = 0; a < 6; a++)
		for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
	for (unsigned wi = 0; wi < 5; wi++) {
		for (int eq = 0; eq < 2; eq++) {
			cfg[0x84 / 4] = eq ? bwins[wi] : bwins[wi] ^ 0x1000u;
			for (uint32_t off = 0; off < 0x400000 + 0x40; off += 4) {
				for (uint32_t u = 0; u < (eq && wi < 2 && (off & 0xFFF) < 8 ? 4u : 1u); u++) {
					uint32_t o = off + u, keep = o < 0x400000 ? tcm[o / 4] : 0;
					int r = sw3_wr(&hw, SWW_BAR2, bwins[wi], o, 0xDEADBEEFu);
					int want = eq && u == 0 && off < 0x400000 && ((bwins[wi] == 0 && off >= 0x200000) || (bwins[wi] == 0x400000 && off <= 0x1FFFFC));
					if ((r == 0) != want || (r == 0 && tcm[o / 4] != 0xDEADBEEFu)) {
						if (bad++ < 20) SWG_BAD("BAD BAR2 win %#x off %#x live-equal %d: ret %d, want accept %d\n", bwins[wi], o, eq, r, want);
					}
					if (o < 0x400000) tcm[o / 4] = keep;
					acc += r == 0;
				}
			}
		}
	}
	cfg[0x84 / 4] = 0x10000000u + 0x84 / 4;
	for (int a = 0; a < 6; a++)
		for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++)
			if (arr[a][i] != 0x10000000u * (a + 1) + (uint32_t)i && bad++ < 20) SWG_BAD("BAD BAR2 sweep changed array %d word %zu\n", a, i);
	/* run D (patch 0132): backplane address 0 (BAR2 window 0, offset 0) only at stage 3 level 6 or above, only while the live cfg 0x84 is 0, only with the container's first dword;
	 * the words next to it, other windows and other values are refused (the tail window rules above stay as they are) */
	for (int l3 = 0; l3 < 9; l3++) {
		static const uint32_t rvv[3] = { 0xEA000010u, 0xEA000011u, 0u };

		for (int st = 0; st < 3; st++) {      /* review 0132 F5: pinned (rv_ok) and not yet stored (rv_done), each alone; st 0 = both fine, 1 = not pinned, 2 = already stored */
			hw.l3 = l3;
			hw.rv = 0xEA000010u;
			for (unsigned wi = 0; wi < 5; wi++) {
				for (int eq = 0; eq < 2; eq++) {
					for (unsigned vi = 0; vi < 3; vi++) {
						for (uint32_t off = 0; off < 16; off += 4) {
							hw.rv_ok = st != 1;
							hw.rv_done = st == 2;
							cfg[0x84 / 4] = eq ? bwins[wi] : bwins[wi] ^ 0x1000u;
							for (int i = 0; i < 4; i++) tcm[i] = 0x77777777u;
							int r = sw3_wr(&hw, SWW_BAR2, bwins[wi], off, rvv[vi]);
							int want = eq && ((bwins[wi] == 0x400000u) || (l3 >= 6 && st == 0 && bwins[wi] == 0 && off == 0 && vi == 0));
							int ch = 0;
							for (int i = 0; i < 4; i++) ch += tcm[i] != 0x77777777u;
							if ((r == 0) != want || (r == 0 && (ch != 1 || tcm[off / 4] != rvv[vi])) || (r != 0 && ch != 0)) {
								if (bad++ < 20) SWG_BAD("BAD run D l3 %d st %d win %#x off %#x live-equal %d val %#x: ret %d changed %d, want accept %d\n", l3, st, bwins[wi], off, eq, rvv[vi], r, ch, want);
							}
							/* rv_done is set by the one store at address 0 and by nothing else */
							if (hw.rv_done != (st == 2 || (r == 0 && bwins[wi] == 0 && off == 0))) {
								if (bad++ < 20) SWG_BAD("BAD run D rv_done st %d win %#x off %#x: ret %d, rv_done %d\n", st, bwins[wi], off, r, hw.rv_done);
							}
							acc += r == 0;
						}
					}
				}
			}
		}
	}
	for (int i = 0; i < 4; i++) tcm[i] = 0x10000000u * 6 + (uint32_t)i;
	/* run E (patch 0133): the stores to the CA7 wrapper. IOCTL 0x23 and 0x21 (CPUHALT set) always; 0x3 only at stage 3 level 7 and while the release has not begun (rel bit 0); 0x1 only at level 7, after the starting store (bit 2),
	 * once (bit 1). RESET_CTL: 1 always; 0 while IOCTL 0x3 has not been stored (bit 0 clear: the CPU is halted) or, at level 7, once after it (bit 2). The SYS_MEM wrapper is unchanged; any other window is refused.
	 * rel records what was stored; a refused store changes nothing. */
	for (int l3 = 0; l3 < 9; l3++) {
		for (uint32_t rel = 0; rel < 8; rel++) {
			static const uint32_t cw[3] = { SWW_WIN_CA7WRAP, SWW_WIN_SMWRAP, 0x18004000u };
			static const uint32_t cv[9] = { 0u, 1u, 2u, 3u, 0x13u, 0x21u, 0x23u, 0x7u, 0xDEADBEEFu };

			for (int wi = 0; wi < 3; wi++) {
				for (int oi = 0; oi < 2; oi++) {
					for (int vi = 0; vi < 9; vi++) {
						uint32_t off = oi ? 0x800u : 0x408u, v = cv[vi], bit = 0;
						int ok;

						if (wi == 0 && oi == 0) {
							ok = v == 0x23u || v == 0x21u || (l3 >= 7 && ((v == 3u && !(rel & 1u)) || (v == 1u && (rel & 5u) == 5u && !(rel & 2u))));
							bit = v == 3u ? 1u : v == 1u ? 2u : 0u;
						} else if (wi == 0) {
							ok = v == 1u || (v == 0u && (!(rel & 1u) || (l3 >= 7 && !(rel & 4u))));
							bit = v == 0u && (rel & 1u) ? 4u : 0u;
						} else {
							ok = wi == 1;
						}
						for (int a = 0; a < 6; a++) {
							for (size_t i = 0; i < len[a]; i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
							memcpy(snap[a], arr[a], len[a] * 4);
						}
						cfg[0x80 / 4] = cw[wi];
						memcpy(snap[0] + 0x80 / 4, &cfg[0x80 / 4], 4);
						hw.l3 = l3;
						hw.rel = rel;
						int r = sw3_wr(&hw, SWW_BAR0, cw[wi], off, v);
						int ch = 0;
						for (int a = 0; a < 6; a++)
							for (size_t i = 0; i < len[a]; i++) ch += arr[a][i] != snap[a][i];
						if ((r == 0) != ok || ch != (ok ? 1 : 0) || (ok && mem[off / 4] != v) || hw.rel != (rel | (ok ? bit : 0u))) {
							if (bad++ < 20) SWG_BAD("BAD CA7 l3 %d rel %u win %#x off %#x val %#x: ret %d changed %d rel %u, want accept %d\n", l3, rel, cw[wi], off, v, r, ch, hw.rel, ok);
						}
						acc += r == 0;
					}
				}
			}
		}
	}
	/* review 0136 F1: a BAR2 window 0 store the glue allows (RAM base) is refused once rel has bit 4 (the CA7 was released) and is allowed before it */
	for (uint32_t rel = 0; rel < 8; rel++) {
		hw.l3 = 8;
		hw.rel = rel;
		hw.rv_ok = 0;
		cfg[0x84 / 4] = 0;
		tcm[0x200000 / 4] = 0x55555555u;
		int r = sw3_wr(&hw, SWW_BAR2, 0, 0x200000u, 0xDEADBEEFu);
		int want = !(rel & 4u);
		if ((r == 0) != want || (r == 0) != (tcm[0x200000 / 4] == 0xDEADBEEFu) || hw.rel != rel) {
			if (bad++ < 20) SWG_BAD("BAD BAR2 after the release rel %u: ret %d rel %u, want accept %d\n", rel, r, hw.rel, want);
		}
		acc += r == 0;
		/* the tail window (W9/W11..W13 class) and the word at 0 (W14 class) are refused too (review 0136 R2-F1) */
		static const uint32_t pw[2] = { 0x400000u, 0u }, po[2] = { 0x1000u, 0u };
		for (int k = 0; k < 2; k++) {
			hw.rv_ok = k;
			hw.rv_done = 0;
			hw.rv = 0xEA000010u;
			cfg[0x84 / 4] = pw[k];
			tcm[po[k] / 4] = 0x55555555u;
			uint32_t v = k ? hw.rv : 0xDEADBEEFu;
			r = sw3_wr(&hw, SWW_BAR2, pw[k], po[k], v);
			if ((r == 0) != want || (r == 0) != (tcm[po[k] / 4] == v) || hw.rel != rel) {
				if (bad++ < 20) SWG_BAD("BAD BAR2 after the release rel %u win %#x off %#x: ret %d, want accept %d\n", rel, pw[k], po[k], r, want);
			}
			acc += r == 0;
		}
	}
	/* patch 0174 (run G2): with g2 = 0 the RC DBI space is refused like any unknown space; with g2 = 1 sw3_wr takes nothing but an entry of g2l[] (space, window, offset, the value under
	 * vmask with the other bits kept, the old value under omask, ONCE, need), a window move (cfg 0x80/0x84) only with swl_pre_ep(.., g2ep), never below level 10 or without g2ok; a refused store
	 * changes nothing, an accepted one changes exactly its word and marks its entry done */
	{
		static uint32_t dbi[0x1000 / 4], hostmem[0x4C000 / 4];      /* W-D4e: the plan is 307328 B (19 pages), the host memory covers it plus a word */
		static struct sw_g2w ge[] = {      /* not const: entries 9 and 10 take the plan end from swd_plan_make() below */
			{ SWW_BAR2, -1, 0u, 0x210050u, 0xC0943041u, 0xFFFFFFFFu, 0x40943041u, 0xFFFFFFFFu, 1, 0, 0 },      /* 0 flags2, old pinned */
			{ SWW_BAR2, -1, 0x400000u, 0x7CF40u, 0u, 0x0000FFFFu, 0u, 0u, 1, 0, 0 },                          /* 1 an index slot: low half 0, upper half kept */
			{ SW3_DBI, -1, 0u, 0x04u, 0x0507u, 0xFFFFFFFFu, 0u, 0xFFFFu, 1, 0, 0 },                           /* 2 RC Command, old low half 0 */
			{ SW3_DBI, 2, 0u, 0x04u, 0x0100u, 0xFFFFFFFFu, 0u, 0u, 1, 0, 0 },                                 /* 3 RC Command back, only after 2 */
			{ SWW_CFG, -1, 0u, 0x84u, 0x400000u, 0xFFFFFFFFu, 0u, 0u, 0, 0, 0 },                              /* 4 window move, not ONCE */
			{ SWW_CFG, -1, 0u, 0x04u, 0x0006u, 0xFFFFFFFFu, 0x0002u, 0xFFFFu, 1, 0, 0 },                      /* 5 EP Command 0x2 -> 0x6 */
			{ SWW_BAR0, -1, SWW_WIN_PCIE, 0x144u, 0x12345678u, 0xFFFFFFFFu, 0u, 0u, 1, 0, 0 },               /* 6 hostready (review r2 F2: the link check on a BAR0 store) */
			{ SW3_HOST, -1, 0u, 0x28u, 0x0180000Du, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, 0, 0 },                  /* 7 W-D4b: a host block word, old pinned 0 */
			{ SW3_HOST, 7, 0u, 0x100u, 1u, 0x0000FFFFu, 0u, 0xFFFFFFFFu, 1, 0, 0 },                           /* 8 W-D4b: a slot, low half only, after 7 */
			{ SW3_HOST, -1, 0u, 0u, 5u, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, 0, 0 },                             /* 9 W-D4b: the first word beyond the plan (off := the real plan total below) */
			{ SW3_HOST, -1, 0u, 0u, 5u, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, 0, 0 },                             /* 10 W-D4b: the last word of the plan (off := total - 4 below) */
			{ SWW_BAR0, 6, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, 0, 0 },          /* 11 W-D4b Q2: the mailbox mask, old pinned 0, only after hostready (6) (review r1 F7) */
			{ SWW_BAR2, -1, 0x400000u, 0x7CF44u, 0u, 0x0000FFFFu, 0u, 0u, 1, 0, 1 },                       /* 12 W-D4b: an index slot as a 16-bit store of the low half (sweetwlan=14) */
			{ SWW_CFG, -1, 0x400000u, 0x8Cu, 0u, 0x0000FFFFu, 0u, 0u, 1, 0, 1 },                              /* 13 a w16 entry that sw3_wr16 must refuse by its own BAR2-only rule (the list never builds one) */
			{ SWW_BAR0, 11, SWW_WIN_PCIE, 0x140u, 0xFF000001u, 0xFFFFFFFFu, 0u, 0u, 1, 0, 0 },              /* 14 W-D4b Q5: doorbell 0, omask 0 (the glue never reads its old value, review r1 F6), only after the mask (11) */
			{ SWW_BAR0, 11, SWW_WIN_PCIE, 0xC34u, 0u, 0xFFFFFFFFu, 0u, 0u, 1, 0, 0 },                      /* 15 W-D4b Q8: the unmask, only after the mask (11), never blocked by the RC irq check (review r2 F10) */
		};
		static struct { int space; uint32_t win, off, v, old; int g2, l3, ok, ep, want; const char *what; uint32_t gep; } gc[] = {
			{ SW3_DBI, 0, 0x04u, 0x0507u, 0u, 0, 10, 1, 2, -1, "DBI with g2 off", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943041u, 0x40943041u, 1, 9, 1, 2, -1, "below level 10", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943041u, 0x40943041u, 1, 10, 0, 2, -1, "without g2ok", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943041u, 0x40943040u, 1, 10, 1, 2, -1, "flags2 wrong old", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943040u, 0x40943041u, 1, 10, 1, 2, -1, "flags2 wrong value", 2u },
			{ SWW_BAR2, 0, 0x210054u, 0xC0943041u, 0x40943041u, 1, 10, 1, 2, -1, "flags2 wrong offset", 2u },
			{ SWW_BAR2, 0x400000u, 0x210050u, 0xC0943041u, 0x40943041u, 1, 10, 1, 2, -1, "flags2 wrong window", 2u },
			{ SWW_BAR0, 0, 0x210050u & 0xFFCu, 0xC0943041u, 0x40943041u, 1, 10, 1, 2, -1, "flags2 wrong space", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943041u, 0x40943041u, 1, 10, 1, 2, 0, "flags2", 2u },
			{ SWW_BAR2, 0, 0x210050u, 0xC0943041u, 0x40943041u, 1, 10, 1, 2, -1, "flags2 twice", 2u },
			{ SWW_BAR2, 0x400000u, 0x7CF40u, 0xABCD0001u, 0xABCD1234u, 1, 10, 1, 2, -1, "slot low half not 0", 2u },
			{ SWW_BAR2, 0x400000u, 0x7CF40u, 0xABCE0000u, 0xABCD1234u, 1, 10, 1, 2, -1, "slot upper half changed", 2u },
			{ SWW_BAR2, 0x400000u, 0x7CF40u, 0xABCD0000u, 0xABCD1234u, 1, 10, 1, 2, 1, "slot", 2u },
			{ SWW_CFG, 0, 0x04u, 0x0507u, 0u, 1, 10, 1, 2, -1, "the RC Command value on the EP Command (space)", 2u },
			{ SW3_DBI, 0, 0x04u, 0x0100u, 0x0507u, 1, 10, 1, 2, -1, "RC back before RC", 2u },
			{ SW3_DBI, 0, 0x04u, 0x0507u, 0x00100006u, 1, 10, 1, 2, -1, "RC old low half not 0", 2u },
			{ SW3_DBI, 0, 0x04u, 0x0507u, 0x00100000u, 1, 10, 1, 2, 2, "RC", 2u },
			{ SW3_DBI, 0, 0x04u, 0x0100u, 0x0507u, 1, 10, 1, 2, 3, "RC back", 2u },
			{ SWW_CFG, 0, 0x84u, 0x400000u, 0u, 1, 10, 1, 6, -1, "window move, EP Command not g2ep", 2u },
			{ SWW_CFG, 0, 0x84u, 0x400000u, 0u, 1, 10, 1, 2, 4, "window move", 2u },
			{ SWW_CFG, 0, 0x84u, 0x400000u, 0u, 1, 10, 1, 2, 4, "window move again (not ONCE)", 2u },
			{ SWW_CFG, 0, 0x04u, 0x0006u, 0x0003u, 1, 10, 1, 2, -1, "EP Command old not 0x2", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x88 in G2 mode before the EP Command", 2u },
			{ SWW_CFG, 0, 0x04u, 0x0006u, 0x0002u, 1, 10, 1, 2, 5, "EP Command", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80380u, 1, 10, 1, 2, -1, "cfg 0x88 old already has 0x300", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80081u, 1, 10, 1, 2, -1, "cfg 0x88 old not the W0 value", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80300u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x88 value drops bit 7", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80780u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x88 value sets more bits", 2u },
			{ SWW_CFG, 0, 0x8Cu, 0x80380u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x8C instead of 0x88", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80080u, 0, 10, 1, 2, -1, "cfg 0x88 with g2 off and no W15 state", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x88 |= 0x300 in G2 mode (no list entry: SPEC-WIFI D-12, W15 is stage 3)", 2u },
			{ SWW_CFG, 0, 0x88u, 0x80380u, 0x80080u, 1, 10, 1, 2, -1, "cfg 0x88 twice (ONCE)", 2u },
			{ SWW_CFG, 0, 0x88u, 0u, 0u, 1, 10, 1, 2, -1, "cfg 0x88 not listed", 2u },
			{ SWW_BAR2, 0x400000u, 0x7CF44u, 0xABCD0000u, 0xABCD1234u, 1, 10, 1, 2, -1, "w16 slot as a 32-bit store (a w16 entry never matches it)", 2u },
			{ SWW_CFG, 0, 0x84u, 0x400000u, 0u, 1, 10, 1, 6, 4, "window move after the EP store (g2ep 0x6)", 6u },
			{ SWW_CFG, 0, 0x84u, 0x400000u, 0u, 1, 10, 1, 2, -1, "window move, EP Command 0x2 but g2ep 0x6", 6u },
			/* W-D4b: the host block as a G2 space (memory, link check as for any non-DBI store) */
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0u, 0, 10, 1, 2, -1, "host word with g2 off", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0u, 1, 9, 1, 2, -1, "host word below level 10", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0u, 1, 10, 0, 2, -1, "host word without g2ok", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Eu, 0u, 1, 10, 1, 2, -1, "host word wrong value", 2u },
			{ SW3_HOST, 0, 0x2Cu, 0x0180000Du, 0u, 1, 10, 1, 2, -1, "host word wrong offset", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0x80u, 1, 10, 1, 2, -1, "host word old not 0", 2u },
			{ SWW_BAR2, 0, 0x28u, 0x0180000Du, 0u, 1, 10, 1, 2, -1, "host value on the BAR2 space", 2u },
			{ SW3_HOST, 0, 0x100u, 1u, 0u, 1, 10, 1, 2, -1, "host slot before its item", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0u, 1, 10, 1, 2, 7, "host word", 2u },
			{ SW3_HOST, 0, 0x28u, 0x0180000Du, 0u, 1, 10, 1, 2, -1, "host word twice", 2u },
			{ SW3_HOST, 0, 0x100u, 0x00010001u, 0u, 1, 10, 1, 2, -1, "host slot upper half changed", 2u },
			{ SW3_HOST, 0, 0x100u, 1u, 0x00010000u, 1, 10, 1, 2, -1, "host slot old not 0", 2u },
			{ SW3_HOST, 0, 0x100u, 1u, 0u, 1, 10, 1, 2, 8, "host slot", 2u },
			{ SW3_HOST, 0, 0u, 5u, 0u, 1, 10, 1, 2, -1, "host word at the plan end", 2u },      /* off := the plan total below */
			{ SW3_HOST, 0, 0u, 5u, 0u, 1, 10, 1, 2, 10, "host word, the plan's last word", 2u },      /* off := total - 4 */
			/* review 0174-w-d4b-hwwrite-r1 F7 / F6: the mask only after hostready, the doorbell only after the mask and whatever its old word is (omask 0) */
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, -1, "mask before hostready (need)", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0x144u, 0x12345678u, 0u, 1, 10, 1, 2, 6, "hostready", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0x1u, 1, 10, 1, 2, -1, "mask old not 0", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFEu, 0u, 1, 10, 1, 2, -1, "mask wrong value", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0x140u, 0xFF000001u, 0u, 1, 10, 1, 2, -1, "doorbell before the mask (need)", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, -1, "mask with RC irq0 enable set", 2u },     /* review r1 F9 */
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, -1, "mask with RC irq1 enable set", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, -1, "mask with RC irq2 enable set", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, 11, "mask", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0xFFFFu, 0u, 1, 10, 1, 2, -1, "mask twice (ONCE)", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0x140u, 0xFF000002u, 0u, 1, 10, 1, 2, -1, "doorbell wrong value", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0x140u, 0xFF000001u, 0xDEADBEEFu, 1, 10, 1, 2, 14, "doorbell, any old word", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0x140u, 0xFF000001u, 0u, 1, 10, 1, 2, -1, "doorbell twice (ONCE)", 2u },
			{ SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0u, 0xFFFFu, 1, 10, 1, 2, 15, "unmask with RC irq1 enable set (never blocked)", 2u },     /* review r2 F10 */
		};
		hw.dbi = dbi;
		hw.g2va = (vm_offset_t)hostmem;
		(void)swd_plan_make(4, 43, 4, &hw.g2plan);      /* W-D4e: total 307328; the plan-end cases below follow THIS total, no constant (gate d4d17g lesson) */
		if ((hw.g2plan.total % 4u != 0u || hw.g2plan.total + 4u > sizeof hostmem) && bad++ < 20) SWG_BAD("BAD G2 plan total %u does not fit the host memory of the sweep (%u B)\n", hw.g2plan.total, (unsigned)sizeof hostmem);
		ge[9].off = hw.g2plan.total;
		ge[10].off = hw.g2plan.total - 4u;
		for (unsigned c = 0; c < sizeof gc / sizeof gc[0]; c++) {
			if (strcmp(gc[c].what, "host word at the plan end") == 0) gc[c].off = hw.g2plan.total;
			if (strcmp(gc[c].what, "host word, the plan's last word") == 0) gc[c].off = hw.g2plan.total - 4u;
		}
		hw.c88 = 0;
		hw.w15 = 0;
		for (unsigned c = 0; c < sizeof gc / sizeof gc[0]; c++) {
			if (c == 0 || c == 8 || c == 13 || (c > 0 && gc[c].what[0] == 'h' && gc[c].what[1] == 'o' && gc[c].what[4] == ' ' && gc[c - 1].what[0] != 'h')) {      /* fresh list (done = 0) at the start, before the first accepted store, before the DBI cases, before the host cases */
				for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) hw.g2l[i] = ge[i];
				hw.g2n = sizeof ge / sizeof ge[0];
			}
			for (int a = 0; a < 6; a++)
				for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
			for (size_t i = 0; i < 0x1000 / 4; i++) dbi[i] = 0x70000000u + (uint32_t)i;
			g2link(0);
			g2irq(g2pre(gc[c].what, "mask with RC irq") ? gc[c].what[16] - '0' : g2pre(gc[c].what, "unmask with RC irq") ? gc[c].what[18] - '0' : -1);
			cfg[0x04 / 4] = (uint32_t)gc[c].ep;
			cfg[0x10 / 4] = 0x60000004u;
			cfg[0x14 / 4] = 0u;
			cfg[0x80 / 4] = gc[c].win;
			cfg[0x84 / 4] = gc[c].win;
			for (size_t i = 0; i < sizeof hostmem / 4; i++) hostmem[i] = 0x30000000u + (uint32_t)i;
			uint32_t *t = gc[c].space == SW3_DBI ? &dbi[gc[c].off / 4] : gc[c].space == SW3_HOST ? &hostmem[gc[c].off / 4] : gc[c].space == SWW_CFG ? &cfg[gc[c].off / 4] : gc[c].space == SWW_BAR0 ? &mem[gc[c].off / 4] : &tcm[gc[c].off / 4];
			*t = gc[c].old;
			hw.g2 = gc[c].g2;
			hw.l3 = gc[c].l3;
			hw.g2ok = gc[c].ok;
			hw.g2ep = gc[c].gep;
			hw.rel = 7u;
			uint8_t d0[sizeof ge / sizeof ge[0]];
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) d0[i] = hw.g2l[i].done;
			int r = sw3_wr(&hw, gc[c].space, gc[c].win, gc[c].off, gc[c].v);
			int ch = 0;
			for (int a = 0; a < 6; a++)
				for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) ch += arr[a][i] != 0x10000000u * (a + 1) + (uint32_t)i && &arr[a][i] != t && !(a == 0 && (i == 0x04 / 4 || i == 0x10 / 4 || i == 0x14 / 4 || i == 0x80 / 4 || i == 0x84 / 4)) && !g2link_word(a, i);
			for (size_t i = 0; i < 0x1000 / 4; i++) ch += dbi[i] != 0x70000000u + (uint32_t)i && &dbi[i] != t;
			for (size_t i = 0; i < sizeof hostmem / 4; i++) ch += hostmem[i] != 0x30000000u + (uint32_t)i && &hostmem[i] != t;
			ch += *t != (r == 0 ? gc[c].v : gc[c].old);     /* the target word: the value if accepted, untouched if refused */
			int dn = 0;
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) dn += hw.g2l[i].done != (d0[i] || (int)i == gc[c].want);
			if ((r == 0) != (gc[c].want >= 0) || ch != 0 || dn != 0 || hw.c88 != 0 || hw.w15 != 0) {      /* G2 mode never touches the W15/restore state */
				if (bad++ < 20) SWG_BAD("BAD G2 %s: ret %d changed %d done-diff %d c88 %d w15 %d, want entry %d\n", gc[c].what, r, ch, dn, hw.c88, hw.w15, gc[c].want);
			}
			acc += r == 0;
		}
		hw.c88 = 0;
		/* review 0174-g2-hwwrite-r1 F1: every G2 store but the RC DBI one needs the link up (swl_pre_link: iso bit 0, LTSSM 0x11, ATU r0/r1); a missing mapping refuses too */
		static const struct { int ld, space; uint32_t win, off, v, old; int want; const char *what; } lc[] = {
			{ 1, SWW_BAR2, 0u, 0x210050u, 0xC0943041u, 0x40943041u, -1, "flags2 with iso bit 0 clear" },
			{ 2, SWW_BAR2, 0u, 0x210050u, 0xC0943041u, 0x40943041u, -1, "flags2 with LTSSM not L0" },
			{ 3, SWW_BAR2, 0u, 0x210050u, 0xC0943041u, 0x40943041u, -1, "flags2 with ATU r0 changed" },
			{ 4, SWW_BAR2, 0u, 0x210050u, 0xC0943041u, 0x40943041u, -1, "flags2 with ATU r1 changed" },
			{ 5, SWW_BAR2, 0u, 0x210050u, 0xC0943041u, 0x40943041u, -1, "flags2 with the iso page not mapped" },
			{ 1, SW3_DBI, 0u, 0x04u, 0x0507u, 0x00100000u, 2, "RC with the link down (RC-local)" },
			{ 1, SWW_CFG, 0u, 0x04u, 0x0006u, 0x00100002u, -1, "EP Command with the link down" },
			{ 2, SWW_BAR0, SWW_WIN_PCIE, 0x144u, 0x12345678u, 0u, -1, "hostready with the link down" },
			{ 0, SWW_BAR0, SWW_WIN_PCIE, 0x144u, 0x12345678u, 0u, 6, "hostready with the link up" },
			{ 0, SWW_CFG, 0u, 0x04u, 0x0006u, 0x00100002u, 5, "EP Command with the link up" },
			{ 1, SW3_HOST, 0u, 0x28u, 0x0180000Du, 0u, -1, "host item word with the link down" },
			{ 0, SW3_HOST, 0u, 0x28u, 0x0180000Du, 0u, 7, "host item word with the link up" },
		};
		for (unsigned c = 0; c < sizeof lc / sizeof lc[0]; c++) {
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) hw.g2l[i] = ge[i];
			hw.g2n = sizeof ge / sizeof ge[0];
			g2link(lc[c].ld);
			cfg[0x04 / 4] = 2u;
			cfg[0x80 / 4] = lc[c].space == SWW_BAR0 ? lc[c].win : 0u;
			cfg[0x84 / 4] = 0u;
			uint32_t *t = lc[c].space == SW3_DBI ? &dbi[lc[c].off / 4] : lc[c].space == SW3_HOST ? &hostmem[lc[c].off / 4] : lc[c].space == SWW_CFG ? &cfg[lc[c].off / 4] : lc[c].space == SWW_BAR0 ? &mem[lc[c].off / 4] : &tcm[lc[c].off / 4];
			*t = lc[c].old;
			hw.g2 = 1;
			hw.l3 = 10;
			hw.g2ok = 1;
			hw.g2ep = 2u;
			if (lc[c].ld == 5) hw.iso = NULL;
			int r = sw3_wr(&hw, lc[c].space, lc[c].win, lc[c].off, lc[c].v);
			hw.iso = iso;
			int dn = 0;
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) dn += hw.g2l[i].done != ((int)i == lc[c].want);
			if ((r == 0) != (lc[c].want >= 0) || *t != (r == 0 ? lc[c].v : lc[c].old) || dn != 0) {
				if (bad++ < 20) SWG_BAD("BAD G2 %s: ret %d word %#x done-diff %d, want entry %d\n", lc[c].what, r, *t, dn, lc[c].want);
			}
			acc += r == 0;
		}
		/* W-D4b D-12 (sweetwlan=15): the two cfg 0x88 stores outside G2 (g2 off): W15 (SWW_CFG88 | 0x300 once, only while h->w15, CA7 not released, the word still SWW_CFG88) and its restore (exactly SWW_CFG88,
		 * only while h->c88, once); a refusal changes nothing (word, h->w15, h->c88) */
		static const struct { int w15, c88; uint32_t rel, v, old; int ok; const char *what; } rc88[] = {
			{ 0, 0, 0, 0x80380u, 0x80080u, 0, "W15 value without h->w15 (level < 15)" },
			{ 1, 0, 0, 0x80380u, 0x80081u, 0, "W15 with the old word not SWW_CFG88" },
			{ 1, 0, 0, 0x80380u, 0x80380u, 0, "W15 with 0x300 already set" },
			{ 1, 0, 1, 0x80380u, 0x80080u, 0, "W15 after the release started (rel 1)" },
			{ 1, 0, 4, 0x80380u, 0x80080u, 0, "W15 after the RESET_CTL release store (rel 4)" },
			{ 1, 0, 0, 0x80780u, 0x80080u, 0, "W15 with more bits" },
			{ 1, 0, 0, 0x80300u, 0x80080u, 0, "W15 dropping bit 7" },
			{ 1, 0, 0, 0u, 0x80080u, 0, "W15 value 0" },
			{ 1, 1, 0, 0x80380u, 0x80080u, 0, "W15 twice (c88 set)" },
			{ 1, 0, 0, 0x80080u, 0x80080u, 0, "W15 slot used for the restore value with c88 clear" },
			{ 1, 0, 0, 0x80380u, 0x80080u, 1, "W15 store" },
			{ 0, 0, 0, 0x80080u, 0x80380u, 0, "cfg 0x88 restore without the W15 store (c88 clear)" },
			{ 0, 1, 0, 0x80380u, 0x80380u, 0, "cfg 0x88 restore with the value 0x80380" },
			{ 0, 1, 0, 0x80081u, 0x80380u, 0, "cfg 0x88 restore with the value 0x80081" },
			{ 0, 1, 0, 0u, 0x80380u, 0, "cfg 0x88 restore to 0" },
			{ 0, 1, 4, 0x80080u, 0x80380u, 1, "cfg 0x88 restore (after the release: allowed, the CA7 is re-halted first by the table order)" },
			{ 0, 0, 4, 0x80080u, 0x80380u, 0, "cfg 0x88 restore twice (c88 cleared by the first)" },
		};
		for (unsigned c = 0; c < sizeof rc88 / sizeof rc88[0]; c++) {
			g2link(0);
			cfg[0x04 / 4] = 2u;
			cfg[0x88 / 4] = rc88[c].old;
			hw.g2 = 0;
			hw.g2ok = 1;
			hw.l3 = 10;
			hw.rel = rc88[c].rel;
			hw.w15 = rc88[c].w15;
			hw.c88 = rc88[c].c88;
			int r = sw3_wr(&hw, SWW_CFG, 0, 0x88u, rc88[c].v);
			int w15x = rc88[c].ok ? 0 : rc88[c].w15, c88x = rc88[c].ok ? rc88[c].v != 0x80080u : rc88[c].c88;
			if ((r == 0) != rc88[c].ok || cfg[0x88 / 4] != (rc88[c].ok ? rc88[c].v : rc88[c].old) || hw.w15 != w15x || hw.c88 != c88x) {
				if (bad++ < 20) SWG_BAD("BAD G2 %s: ret %d word %#x w15 %d c88 %d\n", rc88[c].what, r, cfg[0x88 / 4], hw.w15, hw.c88);
			}
			acc += r == 0;
		}
		hw.w15 = 0;
		hw.c88 = 0;
		hw.rel = 7u;
		/* W-D4b gaps (sweetwlan=14): the 16-bit store of the low half of an index slot (sw3_wr16), only through a w16 entry, only G2 running at d4b 2 on BAR2 */
		static const struct { int g2, d4b, space, ld; uint32_t win, off; uint16_t v; uint32_t old; int want; const char *what; } w16c[] = {
			{ 0, 2, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 0, 0xABCD1234u, -1, "16-bit store with g2 off" },
			{ 1, 1, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 0, 0xABCD1234u, -1, "16-bit store at d4b 1" },
			{ 1, 0, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 0, 0xABCD1234u, -1, "16-bit store at d4b 0" },
			{ 1, 2, SWW_CFG, 0, 0u, 0x88u, 0, 0x80380u, -1, "16-bit store to config space" },
			{ 1, 2, SWW_CFG, 0, 0x400000u, 0x8Cu, 0, 0x80380u, -1, "16-bit store to config space with a w16 entry for it (the BAR2-only rule)" },
			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 1, 0xABCD1234u, -1, "16-bit store of 1 (entry wants 0)" },
			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF48u, 0, 0xABCD1234u, -1, "16-bit store at the wrong offset" },
			{ 1, 2, SWW_BAR2, 0, 0u, 0x7CF44u, 0, 0xABCD1234u, -1, "16-bit store in the wrong window" },
			{ 1, 2, SWW_BAR2, 1, 0x400000u, 0x7CF44u, 0, 0xABCD1234u, -1, "16-bit store with the link down" },
"""


def self_test():
    t = GLUE_HARNESS_A
    ok = len(t.split("\n")) > 100 and "\"\"\"" not in t and ("a" == "a") == t.startswith("\n#include <stdint.h>")
    print("RESULT: %s wlan_glue_harness_a self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_glue_harness_a is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
