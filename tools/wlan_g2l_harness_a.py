#!/usr/bin/env python3
"""wlan_g2l_harness_a: part A of the C text of the G2L harness of tools/test_wlan_wr_mutants.py (includes, helpers and the literal restatement want(); moved out for R29 by T-0a9d; G2L_HARNESS = G2L_HARNESS_A + G2L_HARNESS_B, byte for byte the old text).

  tools/wlan_g2l_harness_a --self-test    checks the part is plain C text without a triple quote
Last line: RESULT: PASS|FAIL wlan_g2l_harness_a ..."""
import sys

G2L_HARNESS_A = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "sweet_wlan_wr.h"
#include "sweet_wlan_dma.h"
static void dsb_hook(void);
#define __builtin_arm_dsb(x) dsb_hook()
static int g2p(const char *fmt, ...);
#define printf g2p
struct sw_g2w {
	int space, need;
	uint32_t win, off, v, vmask, old, omask;
	uint8_t once, done, w16;
	uint8_t kind;
	uint8_t l24;
	uint32_t rep, stride, vrng, arg;
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
#define SW_G2K_E_ITEM   14
#define SW_G2K_E_WR     15
#define SW_G2_E_PAYW    355u
#define SW_G2_CLMCHUNK  1400u
#define SW_G2_CLMMAX    12u
#define SW_G2_MAX   1816u
#define SWG_E_MAX        64u    /* T-4a7f: the header constants the glue's L24 code names (sweet_wlan_g2.h; test_g2l_header_event_caps_restated checks them) */
#define SWG_E_MAX_CLM    48u    /* T-4459: the glue asserts SW_G2_E_NREP_CLM == SWG_E_MAX_CLM (L22c); the harness restated only SWG_E_MAX, so its compile failed */
#define SWG_E_PAYW       44u
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
typedef unsigned long vm_offset_t;
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
	volatile uint32_t *dbi;
	int g2, g2ok;
	unsigned g2n;
	uint32_t g2ep;
	const uint8_t *g2clm;
	uint32_t g2clmlen;
	uint32_t g2clmpre[SW_G2_E_PAYW];
	struct sw_g2w g2l[SW_G2_MAX];
	uint64_t g2pa;
	struct swd_plan g2plan;
	vm_offset_t g2va;
	int d4b;
	int c88;
	int w15;
	uint32_t g2sh;
	uint32_t g2ev;
	uint32_t g2ev0;
	int g2esc;
	uint8_t g2rd;
	const struct swg_jcmd *jc;
	uint32_t njc;	uint64_t dpa;
	vm_offset_t dva;
	uint32_t dlen;
	struct swl25w l25w;
	const struct swg_wx *g2x;
	uint32_t l25old;
	int l25sp, l25cls;
	uint8_t l25arm, l25on;
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
static int sw_g2_rd(void *ctx, int sp, uint32_t a, uint32_t *v) { (void)ctx; (void)sp; (void)a; (void)v; return -1; }     /* T-2543 harness stub: no level-25 judge read happens here */
static void swl25g_wl_bssid(struct swl25w *w, const struct swl25g *g) { (void)w; (void)g; }
static void swl25g_wl_flow(struct swl25w *w, uint32_t old, uint32_t nw) { (void)w; (void)old; (void)nw; }
@GLUE@

static void swh_use_l25(void) { (void)&sw_cred; }
#undef printf
/* W-D3c: the BAR2 window is emulated. back[] is the backplane RAM [0, 0x600000); a read at window W, offset off returns back[(W + off) / 4] (hw.tcm is re-pointed after every store, which sw3_wr ends with
 * __builtin_arm_dsb); every word of back[] is poison unique to its index, so a read through the wrong window returns a different word. Every change of cfg 0x84 is logged. */
#define BACK_BYTES 0x600000u
static uint32_t cfg[0x1000 / 4], dbi[0x1000 / 4], mem[0x1000 / 4], atu[0x1000 / 4], elbi[0x2000 / 4], iso1[1], back[BACK_BYTES / 4];
static struct sw_hw hw;
static int bad;
static uint32_t winlog[64], lastwin;
static unsigned nwin, ndsb;
static int breaklink, allowwin;
static const char *expwhy;
static char lastmsg[1024];

#include <stdarg.h>
static int g2p(const char *fmt, ...)      /* the glue's printf: kept in lastmsg so a refusal can be checked for the clause it names (rule 8) */
{
	va_list ap;
	size_t n = strlen(lastmsg);
	va_start(ap, fmt);
	vsnprintf(lastmsg + n, sizeof(lastmsg) - n, fmt, ap);
	va_end(ap);
	return 0;
}

static void dsb_hook(void)
{
	ndsb++;
	if (cfg[0x84 / 4] != lastwin && nwin < 64u) {
		winlog[nwin++] = cfg[0x84 / 4];
	}
	if (breaklink && nwin == 1u) {      /* the link goes away right after the first window move: the move back must be refused */
		iso1[0] = 0x40000000u;
	}
	lastwin = cfg[0x84 / 4];
	hw.tcm = cfg[0x84 / 4] < BACK_BYTES ? &back[cfg[0x84 / 4] / 4] : NULL;
}

/* the link words swl_pre_link reads (sweet_wlan_chip.h), the EP Command and BAR0 swl_pre_ep reads */
static void link_up(void)
{
	static const uint32_t w0[7] = { 0x4u, 0x80000000u, 0x60FFE000u, 0u, 0x60FFFFFFu, 0x01000000u, 0u };
	static const uint32_t w1[7] = { 0u, 0x80000000u, 0x60000000u, 0u, 0x6000FFFFu, 0x60000000u, 0u };
	iso1[0] = 0x40000001u;
	elbi[0x2C8 / 4] = 0x30000011u;
	for (unsigned i = 0; i < 7; i++) {
		atu[i] = w0[i];
		atu[0x200 / 4 + i] = w1[i];
	}
}

/* the layout of run F3 [obs] (R89 s.1): shared 0x3ced34, ring_info 0x3b1b6c, index arrays 0x407800 0x207f40 0x3e7d80 0x47cf40; end 0x480000 and the RC Command 0x00100146 are test values */
#define SH   0x3ced34u
#define RI   0x3b1b6cu
#define END  0x480000u
#define END2 0x4a0000u
#define RC   0x00100146u
#define W80  0x18002000u
#define G2PA 0xA822A4000ull
static const uint32_t RA[4] = { 0x407800u, 0x207f40u, 0x3e7d80u, 0x47cf40u };

struct ex { int space; uint32_t win, off, v, vmask, old, omask; int once, need, w16; int kind; uint32_t rep, stride, vrng, arg; };      /* 22b: kind/rep/stride/vrng restate SW_G2K_*; L22c: arg = the clmload chunk of a BLOB entry */

/* L22c (T-4adb), restated with literals (docs/research/108 s.9): the CLM blob the glue was given, 12 chunks of 1400 B at most, its CRC32 checked; byte q of the payload of clmload chunk c of nch:
 * "clmload" NUL (8 B), wl_dload_data { u16 flag 0x1000 | BEGIN 2 (first) | END 4 (last), u16 dload_type 2, u32 length, u32 crc 0, data[length] } */
static int clmok(void)
{
	return sw_clm.p != NULL && sw_clm.len != 0u && sw_clm.len <= 12u * 1400u && swf_crc32(sw_clm.p, sw_clm.len) == sw_clm.crc;
}
static uint32_t clmlen(uint32_t c, uint32_t nch)
{
	return c + 1u < nch ? 1400u : sw_clm.len - c * 1400u;
}
static uint32_t clmwords(uint32_t c, uint32_t nch)
{
	return (20u + clmlen(c, nch) + 3u) / 4u;
}
static uint32_t clmb(uint32_t c, uint32_t q, uint32_t nch)
{
	const uint32_t len = clmlen(c, nch), flag = 0x1000u | (c == 0u ? 2u : 0u) | (c + 1u == nch ? 4u : 0u);
	static const char nm[8] = "clmload";
	if (q < 8u) {
		return (uint8_t)nm[q];
	}
	if (q == 8u) {
		return flag & 0xFFu;
	}
	if (q == 9u) {
		return flag >> 8;
	}
	if (q == 10u) {
		return 2u;
	}
	if (q >= 12u && q < 16u) {
		return (len >> (8u * (q - 12u))) & 0xFFu;
	}
	if (q >= 20u && q - 20u < len) {
		return sw_clm.p[c * 1400u + (q - 20u)];
	}
	return 0u;
}
static uint32_t clmw(uint32_t c, uint32_t i, uint32_t nch)
{
	return clmb(c, 4u * i, nch) | clmb(c, 4u * i + 1u, nch) << 8 | clmb(c, 4u * i + 2u, nch) << 16 | clmb(c, 4u * i + 3u, nch) << 24;
}
/* the words of a request payload: name NUL, then arg up to n bytes in all, zero filled; -> the word count */
static uint32_t paywords(uint32_t *out, const char *name, const uint8_t *arg, uint32_t n)
{
	uint8_t b[1500];
	uint32_t i, nl = (uint32_t)strlen(name);
	memset(b, 0, sizeof b);
	memcpy(b, name, nl);
	for (i = 0; arg != NULL && nl + 1u + i < n; i++) {
		b[nl + 1u + i] = arg[i];
	}
	for (i = 0; i < (n + 3u) / 4u; i++) {
		out[i] = (uint32_t)b[4u * i] | (uint32_t)b[4u * i + 1u] << 8 | (uint32_t)b[4u * i + 2u] << 16 | (uint32_t)b[4u * i + 3u] << 24;
	}
	return (n + 3u) / 4u;
}

/* the list restated from R94 s.2 / R106 s.2 / D-1 with literal values (not the glue's macros) */
/* T-8ede: the join tables of the host test (shim/wlan_g2_host_test.c jc_build(0) / jc_build(1): swj_join_cmds of sweet_wlan_join.h for the test SSID, and one 173 B wsec_key of canary bytes), byte for byte */
static const struct swg_jcmd g2l_jc0[6] = {
	{ 263u, "H?", { 0x73, 0x75, 0x70, 0x5f, 0x77, 0x70, 0x61, 0x00, 0x00, 0x00, 0x00, 0x00 }, 12u },
	{ 263u, "H?", { 0x77, 0x70, 0x61, 0x5f, 0x61, 0x75, 0x74, 0x68, 0x00, 0x80, 0x00, 0x00, 0x00 }, 13u },
	{ 263u, "H?", { 0x77, 0x73, 0x65, 0x63, 0x00, 0x04, 0x00, 0x00, 0x00 }, 9u },
	{ 263u, "H?", { 0x61, 0x75, 0x74, 0x68, 0x00, 0x00, 0x00, 0x00, 0x00 }, 9u },
	{ 263u, "H?", { 0x6d, 0x66, 0x70, 0x00, 0x00, 0x00, 0x00, 0x00 }, 8u },
	{ 263u, "H?", { 0x6a, 0x6f, 0x69, 0x6e, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x70, 0x69, 0x78, 0x6f, 0x73, 0x2d, 0x74, 0x65, 0x73, 0x74, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02, 0x11, 0x22, 0x33, 0x44, 0x55, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 }, 73u },
};
static const struct swg_jcmd g2l_jc1[7] = {
	{ 263u, "H?", { 0x73, 0x75, 0x70, 0x5f, 0x77, 0x70, 0x61, 0x00, 0x00, 0x00, 0x00, 0x00 }, 12u },
	{ 263u, "H?", { 0x77, 0x70, 0x61, 0x5f, 0x61, 0x75, 0x74, 0x68, 0x00, 0x80, 0x00, 0x00, 0x00 }, 13u },
	{ 263u, "H?", { 0x77, 0x73, 0x65, 0x63, 0x00, 0x04, 0x00, 0x00, 0x00 }, 9u },
	{ 263u, "H?", { 0x61, 0x75, 0x74, 0x68, 0x00, 0x00, 0x00, 0x00, 0x00 }, 9u },
	{ 263u, "H?", { 0x6d, 0x66, 0x70, 0x00, 0x00, 0x00, 0x00, 0x00 }, 8u },
	{ 263u, "H?", { 0x77, 0x73, 0x65, 0x63, 0x5f, 0x6b, 0x65, 0x79, 0x00, 0x5a, 0x5a, 0x5a, 0x5a, 0xc3, 0x3c, 0xc3, 0x3c, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a, 0x5a }, 173u },
	{ 263u, "H?", { 0x6a, 0x6f, 0x69, 0x6e, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x70, 0x69, 0x78, 0x6f, 0x73, 0x2d, 0x74, 0x65, 0x73, 0x74, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02, 0x11, 0x22, 0x33, 0x44, 0x55, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 }, 73u },
};
static unsigned g2l_n24, g2l_n24k;
static unsigned want(struct ex *e, uint32_t sh, uint32_t ri, const uint32_t *ra, uint32_t rc)
{
	unsigned n = 0, k, i;
	uint32_t rm = ri + 0x4Cu;
	uint64_t pa;
	int rci, epi, hri;
#define W(s, wi, o, val, vm, ol, om, on, nd) (e[n] = (struct ex){ s, wi, o, val, vm, ol, om, on, nd, 0, 0, 0, 0, 0, 0 }, n++)
#define WK(kd) (e[n - 1].kind = (kd))
#define T(bp, val, vm, ol, om) W(SWW_BAR2, (bp) >= 0x400000u ? 0x400000u : 0u, (bp) - ((bp) >= 0x400000u ? 0x400000u : 0u), val, vm, ol, om, 1, -1)
#define T16(bp) (T(bp, 0u, 0x0000FFFFu, 0, 0), e[n - 1].w16 = 1)
	W(SWW_CFG, 0, 0x80u, 0x18001000u, 0xFFFFFFFFu, 0, 0, 0, -1);
	W(SWW_CFG, 0, 0x80u, W80, 0xFFFFFFFFu, 0, 0, 0, -1);
	W(SWW_CFG, 0, 0x84u, 0u, 0xFFFFFFFFu, 0, 0, 0, -1);
	W(SWW_CFG, 0, 0x84u, 0x400000u, 0xFFFFFFFFu, 0, 0, 0, -1);
	T(sh + 0x50u, 0xC0943041u, 0xFFFFFFFFu, 0x40943041u, 0xFFFFFFFFu);
	T(sh + 0x54u, 0x10823407u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	pa = G2PA + hw.g2plan.b[SWD_TRAP].off;
	T(sh + 0x58u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	T(sh + 0x5Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	for (k = 0; k < 4u; k++) {
		pa = G2PA + hw.g2plan.b[SWD_IDX_H2D_W + k].off;
		T(ri + 0x14u + 8u * k, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
		T(ri + 0x18u + 8u * k, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	}
	for (i = 0; i < 5u; i++) {
		pa = G2PA + hw.g2plan.b[SWD_H2D_CTRL + i].off;
		T(rm + 16u * i + 0x08u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
		T(rm + 16u * i + 0x0Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
		T(rm + 16u * i + 0x04u, swd_rings[SWD_H2D_CTRL + i].items | swd_rings[SWD_H2D_CTRL + i].isz * 0x10000u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	}
	for (k = 0; k < 4u; k++) {
		for (i = 0; i < (k == 0 || k == 1 ? 2u : 3u); i++) {
			if (hw.d4b >= 2) {      /* sweetwlan=14: the slot zero is a 16-bit store of the low half */
				T16(ra[k] + 4u * i);
			} else {
				T(ra[k] + 4u * i, 0u, 0x0000FFFFu, 0, 0);
			}
		}
	}
	pa = G2PA + hw.g2plan.b[SWD_SCRATCH].off;
	T(sh + 0x38u, (uint32_t)pa, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	T(sh + 0x3Cu, (uint32_t)(pa >> 32), 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	T(sh + 0x34u, 8u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu);
	rci = (int)n;
	W(SW3_DBI, 0, 0x04u, 0x0507u, 0xFFFFFFFFu, 0, 0xFFFFu, 1, -1);
	W(SW3_DBI, 0, 0x04u, rc & 0xFFFFu, 0xFFFFFFFFu, 0, 0, 1, rci);
	epi = (int)n;
	W(SWW_CFG, 0, 0x04u, 0x0006u, 0xFFFFFFFFu, 0x0002u, 0xFFFFu, 1, -1);
	W(SWW_CFG, 0, 0x04u, 0x0002u, 0xFFFFFFFFu, 0, 0, 1, epi);
	/* SPEC-WIFI D-12: no cfg 0x88 entry at any level (it is stage-3 row W15, outside G2) */
	hri = (int)n;
	W(SWW_BAR0, 0x18001000u, 0x144u, 0x12345678u, 0xFFFFFFFFu, 0, 0, 1, -1);
	if (hw.d4b >= 2) {      /* W-D4b, restated from SPEC-WIFI Q2-Q5/Q8 with literals: mask, ten item words, slot, doorbell 0, unmask */
		int mi = (int)n;
		pa = G2PA + hw.g2plan.b[SWD_EVBUF].off;
		W(SWW_BAR0, 0x18001000u, 0xC34u, 0xFFFFu, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, hri);      /* review r1 F7: chained after hostready */
		for (k = 0; k < 10u; k++) {
			W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 4u * k, k == 0 ? 0x0180000Du : k == 1 ? 1u : k == 2 ? 0x2000u : k == 4 ? (uint32_t)pa : k == 5 ? (uint32_t)(pa >> 32) : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0 ? mi : (int)n - 1);
		}
		W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, 1u, 0xFFFFu, 0, 0xFFFFFFFFu, 1, (int)n - 1);
		W(SWW_BAR0, 0x18001000u, 0x140u, 0xFF000001u, 0xFFFFFFFFu, 0, 0, 1, (int)n - 1);
		W(SWW_BAR0, 0x18001000u, 0xC34u, 0u, 0xFFFFFFFFu, 0, 0, 1, mi);
		if (hw.d4b >= 3) {      /* W-D4c, restated with literals from docs/research/W-D4c-ds-ack.md s.6.5 (dhd_msgbuf.c:114, 116, 7524-7532, 12395-12415; dhd_pcie.c:12255-12310, 13036-13086; bcmpcie.h:485, 489, 490): chained after the L14 doorbell entry (n - 2, before the unmask), in the store order A1 ack, C1 read index, W wake, D deassert, C2 read index */
			int ci = (int)n - 2;
			static const struct { uint32_t w0, mb, idx, db; } it[3] = {
				{ 0x02800023u, 0x2u, 2u, 0xFF000002u },     /* ring-0 item 1: DS_ACK, epoch 2 */
				{ 0x03800023u, 0x40u, 3u, 0xFF000003u },    /* item 2: DEVICE_WAKE_ASSERT, epoch 3 */
				{ 0x04800023u, 0x20u, 4u, 0xFF000004u },    /* item 3: DEVICE_WAKE_DEASSERT, epoch 4 */
			};
			static const uint32_t order[5] = { 0, 10, 1, 2, 11 };      /* 0..2: an item post; 10, 11: a read-index post with idx 1, 2 */
			for (unsigned g = 0; g < 5u; g++) {
				if (order[g] >= 10u) {
					W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_D2H_R].off, order[g] - 9u, 0xFFFFu, order[g] - 10u, 0xFFFFFFFFu, 1, ci);
					W(SWW_BAR0, 0x18001000u, 0x140u, order[g] == 10u ? 0xDD020001u : 0xDD020002u, 0xFFFFFFFFu, 0, 0, 1, (int)n - 1);
				} else {
					unsigned itm = order[g];
					for (k = 0; k < 10u; k++) {
						W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 40u * (itm + 1u) + 4u * k, k == 0 ? it[itm].w0 : k == 2 ? it[itm].mb : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, k == 0 ? ci : (int)n - 1);
					}
					W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, it[itm].idx, 0xFFFFu, it[itm].idx - 1u, 0xFFFFFFFFu, 1, (int)n - 1);
					W(SWW_BAR0, 0x18001000u, 0x140u, it[itm].db, 0xFFFFFFFFu, 0, 0, 1, (int)n - 1);
				}
				ci = (int)n - 1;
			}
			if (hw.d4b >= 4) {      /* W-D4d (sweetwlan=17..20), restated with literals from docs/research/W-D4d-ioctl-get-version.md s.2.1, s.2.2, s.8 (dhd_msgbuf.c:116, 114, 6319-6388, 10879-10895; bcmmsgbuf.h:390-420): chained after the C2 doorbell, in the header's store order */
				const uint64_t rpa = G2PA + hw.g2plan.b[SWD_RESPBUF].off, ipa = G2PA + hw.g2plan.b[SWD_IOCTBUF].off;
				const struct { int kind; uint32_t idx, w[8]; } d4d[6] = {
					{ 0, 4u, { 0x0580000Bu, 2u, 0x2000u, 0u, (uint32_t)rpa, (uint32_t)(rpa >> 32), 0u, 0u } },                            /* L17: response buffer post, ring-0 index 4, epoch 5, request_id 2, host_buf_len 8192 */
					{ 0, 5u, { 0x06800009u, 0xFFFEu, 1u, 0x00040101u, 4u, 0u, (uint32_t)ipa, (uint32_t)(ipa >> 32) } },                  /* L18: WLC_GET_VERSION, index 5, epoch 6, cmd 1, trans_id 0x101 | input_buf_len 4 << 16, output_buf_len 4 */
					{ 0, 6u, { 0x0780000Bu, 3u, 0x2000u, 0u, (uint32_t)rpa, (uint32_t)(rpa >> 32), 0u, 0u } },                            /* L19 re-post: index 6, epoch 7, request_id 3 (the read-index update is a masked entry below, 22b) */
					{ 2, 0u, { 0x00706163u } },                                                                                           /* L20: "cap\0" at the start of the payload buffer */
					{ 0, 7u, { 0x08800009u, 0xFFFEu, 262u, 0x03FF0102u, 0x3FFu, 0u, (uint32_t)ipa, (uint32_t)(ipa >> 32) } },             /* GET_VAR: index 7, epoch 8, cmd 262, trans_id 0x102 | input_buf_len 1023 << 16, output_buf_len 1023 */
					{ 0, 8u, { 0x0980000Bu, 4u, 0x2000u, 0u, (uint32_t)rpa, (uint32_t)(rpa >> 32), 0u, 0u } },                            /* re-post: index 8, epoch 9, request_id 4 */
				};
				const unsigned nsteps = hw.d4b == 4 ? 1u : hw.d4b == 5 ? 2u : hw.d4b == 6 ? 3u : 6u;
				for (unsigned g = 0; g < nsteps; g++) {
					if (d4d[g].kind == 0) {
						for (k = 0; k < 10u; k++) {
							W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 40u * d4d[g].idx + 4u * k, k < 8u ? d4d[g].w[k] : 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, (int)n - 1);
							WK(k == 0u ? 2 : 1);     /* SW_G2K_SH_ITEM0 / SH_ITEM */
						}
						W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, d4d[g].idx + 1u, 0xFFFFu, d4d[g].idx, 0xFFFFFFFFu, 1, (int)n - 1);
						WK(3);
						W(SWW_BAR0, 0x18001000u, 0x140u, 0xFF000000u | (d4d[g].idx + 1u), 0xFFFFFFFFu, 0, 0, 1, (int)n - 1);
						WK(4);
					} else {
						W(SW3_HOST, 0, hw.g2plan.b[SWD_IOCTBUF].off, d4d[g].w[0], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, (int)n - 1);
					}
				}
				if (hw.d4b >= 8) {      /* W-D4e (sweetwlan=21..23), restated with literals (docs/research/W-D4e-scan24-design.md s.1; payload words counted by hand from the byte layouts, wlioctl.h.new:905-1000, 1089-1095, 9665-9686; ring-0 index from 9) */
					const uint64_t epa = G2PA + hw.g2plan.b[SWD_EVBUF].off;
					uint32_t ix = 9u, j, kd = 0, buf[355] = { 0x00706163u }, q;
					int usedclm = 0;
					static const struct { uint32_t cmd, trans, len; unsigned np; uint32_t pw[5][3]; } rq[5] = {
						{ 262u, 0x103u, 15u, 2, { { 0, 0x6e616373u, 0x00706163u }, { 1, 0x7265765fu, 0u } } },                                                                             /* GET_VAR "scan_ver\0" + 6 */
						{ 262u, 0x104u, 12u, 2, { { 0, 0x5f707573u, 0x6e616373u }, { 1, 0x00617077u, 0x7265765fu } } },                                                                     /* GET_VAR "sup_wpa\0" + 4 */
						{ 262u, 0x105u, 45u, 5, { { 0, 0x6e657665u, 0x5f707573u }, { 1, 0x736d5f74u, 0x00617077u }, { 2, 0x655f7367u, 0u }, { 3, 0x01007478u, 0u }, { 4, 0x00001A00u, 0u } } },   /* GET_VAR "event_msgs_ext\0" + ver 1 cmd 0 len 26 */
						{ 263u, 0x106u, 45u, 2, { { 4, 0x00001A03u, 0x00001A00u }, { 6, 0x20000000u, 0u } } },                                                                              /* SET_VAR: command 3, mask bit 69 = byte 27 bit 5 */
						{ 2u, 0x107u, 0u, 0, { { 0, 0, 0 } } },                                                                                                                             /* WLC_UP, no payload */
					};
					const uint32_t ne = hw.d4b >= 11 ? 18u : 14u, qn = hw.d4b >= 11 ? 26u : 22u, eln = hw.d4b >= 11 ? 102u : 86u, e20 = hw.d4b >= 11 ? 0x0007FFFFu : 0x0000FFFFu;      /* T-0fdd: sweetwlan=24 escan = 6 + 96 B: channel_num 7 (word 20 high half), the seven chanspecs in words 21..24 (restated here with literals, independent of the glue) */
					const uint32_t esc[18][3] = { { 0, 0x61637365u, 0x6e657665u }, { 1, 0x0002006eu, 0x736d5f74u }, { 2, 0x00010000u, 0x655f7367u }, { 3, 0x00021234u, 0x01007478u }, { 4, 0x00000048u, 0x00001A03u }, { 6, 0u, 0x20000000u },
					    { 13, 0xFFFF0000u, 0u }, { 14, 0xFFFFFFFFu, 0u }, { 15, 0x00010002u, 0u }, { 16, 0xFFFF0000u, 0u }, { 17, 0xFFFFFFFFu, 0u }, { 18, 0xFFFFFFFFu, 0u }, { 19, 0xFFFFFFFFu, 0u }, { 20, e20, 0u },
					    { 21, 0x10010000u, 0u }, { 22, 0x100B1006u, 0u }, { 23, 0xD028D024u, 0u }, { 24, 0xD030D02Cu, 0u } };
#define EPOCH(i) (((254u + (i)) % 253u) << 24)
#define PW(i, w0, w1, w2, w3, w4, w5, w6, w7) do { const uint32_t w_[10] = { (w0) | EPOCH(i), w1, w2, w3, w4, w5, w6, w7, 0u, 0u }; for (k = 0; k < 10u; k++) { \
	W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 40u * (i) + 4u * k, w_[k], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 1, (int)n - 1); WK(k == 0u ? 2 : 1); } \
	W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, (i) + 1u, 0xFFFFu, (i), 0xFFFFFFFFu, 1, (int)n - 1); WK(3); \
	W(SWW_BAR0, 0x18001000u, 0x140u, 0xFF000000u | ((i) + 1u), 0xFFFFFFFFu, 0, 0, 1, (int)n - 1); WK(4); } while (0)
#define PAYW(r, ver) do { W(SW3_HOST, 0, hw.g2plan.b[SWD_IOCTBUF].off + 4u * (r)[0], (r)[1], 0xFFFFFFFFu, (r)[2], 0xFFFFFFFFu, 1, (int)n - 1); if (ver) WK(11); } while (0)
#define LO(x) ((uint32_t)(x))
#define HI(x) ((uint32_t)((x) >> 32))
					for (j = 1; j < 8u; j++, ix++) {
						PW(ix, 0x0080000Bu, 4u + j, 0x2000u, 0u, LO(rpa + 0x2000u * j), HI(rpa + 0x2000u * j), 0u, 0u);        /* ioctl-response buffer post: ids 5..11, buffers 1..7 */
					}
					for (j = 1; j < 8u; j++, ix++) {
						PW(ix, 0x0080000Du, 0x20u + j, 0x2000u, 0u, LO(epa + 0x2000u * j), HI(epa + 0x2000u * j), 0u, 0u);    /* event buffer post: type 0x0D, ids 0x21..0x27, buffers 1..7 */
					}
					for (j = 0; hw.d4b >= 9 && j < 5u; j++) {
						for (k = 0; k < rq[j].np; k++) {
							PAYW(rq[j].pw[k], 0);
							buf[rq[j].pw[k][0]] = rq[j].pw[k][1];      /* the payload buffer as the host has it (the old words of the next payload) */
						}
						if (j == 4u && hw.d4b >= 11 && clmok()) {      /* L22c (sweetwlan=24, docs/research/108 s.9): between the event_msgs_ext SET and WLC_UP: per chunk the BLOB entry + request + re-post, clmload_status GET, country SET; 48 event re-posts */
							const uint32_t nch = (sw_clm.len + 1399u) / 1400u;
							uint32_t c, pw[355], nw;
							static const uint8_t xz[12] = { 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 };

#define RQ(cmd, len) do { PW(ix, 0x00800009u, 0xFFFEu, (cmd), (0x107u + kd) | (len) << 16, (len), 0u, LO(ipa), HI(ipa)); ix++; \
	PW(ix, 0x0080000Bu, 16u + kd, 0x2000u, 0u, LO(rpa), HI(rpa), 0u, 0u); e[n - 8].kind = SW_G2K_SH_RBUF; e[n - 8].v = LO(rpa); e[n - 8].stride = 0x2000u; e[n - 8].vrng = 7u * 0x2000u; ix++; kd++; } while (0)
#define PAYSTEP(name, argb, len) do { nw = paywords(pw, (name), (argb), (len)); for (q = 0; q < nw; q++) { if (pw[q] != buf[q]) { const uint32_t r3_[3] = { q, pw[q], buf[q] }; PAYW(r3_, 0); buf[q] = pw[q]; } } } while (0)
							usedclm = 1;
							for (c = 0; c < nch; c++) {
								W(SW3_HOST, 0, hw.g2plan.b[SWD_IOCTBUF].off, 0u, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 0, (int)n - 1);
								WK(SW_G2K_BLOB);
								e[n - 1].arg = c;
								e[n - 1].rep = clmwords(c, nch);
								RQ(263u, 20u + clmlen(c, nch));
								for (q = 0; q < clmwords(c, nch); q++) {
									buf[q] = clmw(c, q, nch);
								}
							}
							PAYSTEP("clmload_status", NULL, 19u);
							RQ(262u, 19u);
							PAYSTEP("country", xz, 20u);
							RQ(263u, 20u);
#undef RQ
#undef PAYSTEP
						}
						PW(ix, 0x00800009u, 0xFFFEu, rq[j].cmd, (rq[j].trans + kd) | rq[j].len << 16, rq[j].len, 0u, LO(ipa), HI(ipa));
						ix++;
						PW(ix, 0x0080000Bu, 12u + j + kd, 0x2000u, 0u, LO(rpa + 0x2000u * j), HI(rpa + 0x2000u * j), 0u, 0u);       /* re-post of the buffer the completion used */
						e[n - 8].kind = SW_G2K_SH_RBUF;      /* T-af77: word 4 of the re-post: the dongle chooses the buffer, the table value is the slot-0 address (any of the 8 strides accepted) */
						e[n - 8].v = LO(rpa);
						e[n - 8].stride = 0x2000u;
						e[n - 8].vrng = 7u * 0x2000u;
						ix++;
					}
					if (hw.d4b >= 10) {
						if (usedclm) {      /* the old words are the payload buffer after the country request, not the event_msgs_ext SET */
							uint32_t ew[26] = { 0 };
							for (k = 0; k < ne; k++) {
								ew[esc[k][0]] = esc[k][1];
							}
							for (q = 0; q < qn; q++) {
								if (ew[q] != buf[q]) {
									const uint32_t r3[3] = { q, ew[q], buf[q] };
									PAYW(r3, q == 1u || q == 3u);
								}
							}
						} else {
							for (k = 0; k < ne; k++) {
								PAYW(esc[k], k == 1u || k == 3u);
							}
						}
						PW(ix, 0x00800009u, 0xFFFEu, 263u, (0x108u + kd) | eln << 16, eln, 0u, LO(ipa), HI(ipa));
						ix++;
						{       /* T-4a7f + T-f1e2: the event re-posts are 10 repeatable E_ITEM words (value 0 in the table, computed at match time; word 4 = ANY of the 8 event buffers) and the E_WR commit, all after the escan doorbell; L24 (d4b 11) calls come first */
							const int esc_db = (int)n - 1;

							if (hw.d4b >= 11 && hw.jc != NULL && hw.njc >= 6u && hw.njc <= 8u && hw.jc[hw.njc - 1u].len >= 5u && memcmp(hw.jc[hw.njc - 1u].buf, "join", 5) == 0) {
								/* T-8ede, L24 restated with literals (patch 0174 sw_g2_d4j comment; T-4a7f Log step 1): call 0 = "event_msgs_ext" ver 1 cmd 3 len 26 with bits 69, 0, 16 (45 B), call c >= 1 = jc[c - 1]; each = its payload words that differ
								 * from the shadow (the payload buffer as the host has it after the escan; escan words 1 and 3 still unstored by L24: always an entry, old under ~0x10000), the request (SET_VAR 263, trans 0x109 + kd + c, in = out = n) at ix, the re-post (id 17 + kd + c, word 4 = ANY of the 8 response buffers, kind SH_RBUF: LIFO, T-f1e2) at ix + 1;
								 * after "wsec_key" the 44 words zeroed against the shadow */
								uint32_t jsh[355], jv = (1u << 1) | (1u << 3), c, i, nb, v;      /* T-1142: the whole 355-word shadow (shim swg_e_call_secret zeroes sizeof z = 4 * SWG_E_PAYW against it), so a CLM remnant beyond word 44 is zeroed too */
								uint8_t b[4u * 355u];
								int key;

								if (usedclm) {
									for (q = 0; q < qn; q++) {
										buf[q] = 0u;
									}
									for (k = 0; k < ne; k++) {
										buf[esc[k][0]] = esc[k][1];
									}
								} else {
									for (k = 0; k < ne; k++) {
										buf[esc[k][0]] = esc[k][1];
									}
								}
								for (i = 0; i < 355u; i++) {
									jsh[i] = buf[i];
								}
								for (c = 0; c <= hw.njc; c++) {
									memset(b, 0, sizeof b);
									if (c == 0u) {
										memcpy(b, "event_msgs_ext", 15);
										b[15] = 1u; b[16] = 3u; b[17] = 26u;
										b[19] = 0x01u; b[21] = 0x01u; b[27] = 0x20u;       /* bits 0 (byte 19 + 0), 16 (byte 19 + 2), 69 (byte 19 + 8, bit 5) */
										nb = 45u;
									} else {
										nb = hw.jc[c - 1u].len;
										memcpy(b, hw.jc[c - 1u].buf, nb);
									}
									key = c > 0u && nb >= 9u && memcmp(b, "wsec_key", 9) == 0;
									for (int z = 0; z < 1 + key; z++) {
										const uint32_t nw = z ? 355u : (nb + 3u) / 4u;

										if (z) memset(b, 0, sizeof b);
										for (i = 0; i < nw; i++) {
											v = (uint32_t)b[4u * i] | (uint32_t)b[4u * i + 1u] << 8 | (uint32_t)b[4u * i + 2u] << 16 | (uint32_t)b[4u * i + 3u] << 24;
											if (!((jv >> i) & 1u) && v == jsh[i]) continue;
											W(SW3_HOST, 0, hw.g2plan.b[SWD_IOCTBUF].off + 4u * i, v, 0xFFFFFFFFu, ((jv >> i) & 1u) ? jsh[i] & ~0x10000u : jsh[i], ((jv >> i) & 1u) ? ~0x10000u : 0xFFFFFFFFu, 1, (int)n - 1);
											jsh[i] = v;
											jv &= ~(1u << i);
										}
										if (z) break;
										PW(ix, 0x00800009u, 0xFFFEu, 263u, (0x109u + kd + c) | nb << 16, nb, 0u, LO(ipa), HI(ipa));
										ix++;
										PW(ix, 0x0080000Bu, 18u + kd + c, 0x2000u, 0u, LO(rpa), HI(rpa), 0u, 0u);
										e[n - 8].kind = SW_G2K_SH_RBUF;
										e[n - 8].v = LO(rpa);
										e[n - 8].stride = 0x2000u;
										e[n - 8].vrng = 7u * 0x2000u;
										ix++;
									}
								}
							}
							for (k = 0; k < 10u; k++) {
								W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 4u * k, 0u, 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, esc_db);
								WK(14);
								if (k == 4u) {      /* T-1142: word 4 = the buffer address, ANY of the 8 event buffers: slot 0 + a whole number of 0x2000 strides (glue sw_g2_e_rx, T-f1e2) */
									e[n - 1].stride = 0x2000u;
									e[n - 1].vrng = 7u * 0x2000u;
								}
							}
							W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, 0u, 0xFFFFu, 0u, 0u, 0, esc_db);
							WK(15);
						}
						(void)epa;
					}
#undef EPOCH
#undef PW
#undef PAYW
#undef LO
#undef HI
				}
				/* 22b: the masked, repeatable DS-round entries, LAST: ring-0 item words (127 items of 40 B from the ring base), the H2D-WR slot (+1), doorbell 0 = 0xFF000000 | the slot, the D2H-RD slot (< 64), doorbell 0 = 0xDD020000 | the slot; all chained after the L16 C2 doorbell entry (ci, review F2) */
				{
					static const uint32_t itemw[10] = { 0x00800023u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u };
					static const uint32_t mbv[3] = { 0x2u, 0x40u, 0x20u };
					for (k = 0; k < 10u; k++) {
						for (unsigned r = 0; r < (k == 2u ? 3u : 1u); r++) {
							W(SW3_HOST, 0, hw.g2plan.b[SWD_H2D_CTRL].off + 4u * k, k == 2u ? mbv[r] : itemw[k], 0xFFFFFFFFu, 0, 0xFFFFFFFFu, 0, ci);
							WK(k == 0u ? 6 : 5);
							e[n - 1].rep = 127u;
							e[n - 1].stride = 40u;
						}
					}
					W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_H2D_W].off, 0u, 0xFFFFu, 0u, 0u, 0, ci);
					WK(7);
					W(SWW_BAR0, 0x18001000u, 0x140u, 0xFF000000u, 0xFFFFFFFFu, 0, 0, 0, ci);
					WK(8);
					W(SW3_HOST, 0, hw.g2plan.b[SWD_IDX_D2H_R].off, 0u, 0xFFFFu, 0u, 0u, 0, ci);
					WK(9);
					e[n - 1].vrng = 63u;
					W(SWW_BAR0, 0x18001000u, 0x140u, 0xDD020000u, 0xFFFFFFFFu, 0, 0, 0, ci);
					WK(10);
				}
			}
		}
	}
#undef T
#undef W
	return n;
}

"""


def self_test():
    t = G2L_HARNESS_A
    ok = len(t.split("\n")) > 100 and "\"\"\"" not in t
    print("RESULT: %s wlan_g2l_harness_a self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_g2l_harness_a is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
