/*
 * Host test of the glue-side inputs of the L25/L26 engine (sweet_wlan_l25_glue.h, T-37d8 steps 2-3; SPEC-WIFI Part E TRANSPORT s.1 item 1, s.2 L25). Clauses (each has a mutant in tools/test_wlan_rsne_mutants.py):
 *   A1 armed: the DATA block is placed (struct swg_in data / data_len), struct swl25_cfg holds the PMK, IP, gateway and BSSID that were given (mac zero = take the firmware's, RSNE empty until the scan), x->l25c points
 *      at it, the L23 hook is installed, and the engine's own envelope audit (swl25_env, swdp_env_ok) accepts the placed block
 *   B1..B5 one missing input each (PMK zero, IP zero, gateway zero, BSSID zero, BSSID NULL) and B6 no credentials at all: exactly its WIFI GAP line(s), x->l25c NULL, no hook, DATA not placed, cfg zero
 *   D1..D9 DATA block refusals: missing, too small, unaligned, overlapping the G1 block (start inside / end inside / enclosing), wrapping, outside the alias window (low and high); the window edges and a block below
 *      4 GiB are accepted   X1 data and cfg both bad: both gaps print   H1 the scan hook copies the target's RSNE into cfg.rsne_assoc and rsne_beacon (22 B), a foreign BSSID leaves them empty
 *   H2 a failing bus read inside the hook (the length word, either ie_offset word) comes back negative through swl25g_note and nothing reaches the cfg   H3 swl25g_note copies only a target whose state is OK: a BAD or never-seen
 *      target that carries a stale rsne_len leaves cfg.rsne_* untouched (no bytes, no length)
 *   W1 swl25g_disarm clears the pointers and wipes the cfg   S1 the log holds no byte of the PMK
 *   T-43af: J1 swl25g_wl_layout fills the judge from the real G1 plan and DATA block (layout ok), a too small DATA block or a block overlapping G1 fails it   J2 swl25g_wl_bssid: a known BSSID sets bssid_set, zero leaves it free
 *   J4 (T-3784) swl25g_wl_arm: armed only when credentials, DATA block and judge layout are all accepted; a layout refusal alone takes the hook down and wipes the cfg with its own gap line, the credentials gap still prints; no DATA block skips the layout
 *   J3 swl25g_wl_flow sets flow_open only for a FLOW_CREATE_CMPLT (status 0, ring id = the flow id) among the items consumed, after a committed create; not for a refused one, another type, another ring id,
 *      a create that was never committed, an unreadable item, or items outside [old, new)
 * No hardware.
 */
#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static unsigned fails;
static char logbuf[1 << 20];
static unsigned loglen;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n--- log at the failure:\n%s---\n", __LINE__, #x, logbuf); fails++; } } while (0)
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
static void lg(const char *s) { cap_printf("%s\n", s); }
#define SWDP_LOG(s) lg(s)
#define SWDP_GAP(s) lg(s)
#define SWJ_LOG(s) lg(s)
#define SWJ_GAP(s) lg(s)
#define SWC_LOG(s) lg(s)
#define SWC_GAP(s) lg(s)
#define SWJ_RANDOM(b, n) memset((b), 0x42, (n))
#include "sweet_wlan_data.h"
#include "sweet_wlan_join.h"
#define printf cap_printf
#define swg_e_call real_swg_e_call
#include "sweet_wlan_g2.h"
#undef swg_e_call
#include "sweet_wlan_l25.h"
#include "sweet_wlan_l25_rsne.h"
#include "sweet_wlan_cred.h"
#include "sweet_wlan_l25_wl.h"
/* patch 0288: the ioctl transport is a scripted stub: it records the one WLC_GET_BSS_INFO request and answers with the record the case built */
static struct { unsigned n; uint32_t cmd, argn, wlen, nrsp; char name[16]; uint8_t arg[8]; int soft, repost; int rc; int32_t status; uint32_t resp_len; uint8_t ans[1100]; int setstop; } BI;
static int swg_e_call(struct swg_wx *xx, uint32_t cmd, const char *what, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, int soft, uint32_t nrsp, uint8_t *rsp, int repost, struct swd4b_ioctl_d2h *cmo, struct swg_ew *sc)
{
	(void)what; (void)sc;
	BI.n++;
	BI.cmd = cmd; BI.argn = argn; BI.wlen = wlen; BI.nrsp = nrsp; BI.soft = soft; BI.repost = repost;
	snprintf(BI.name, sizeof BI.name, "%s", name);
	memset(BI.arg, 0, sizeof BI.arg);
	if (arg != NULL && argn <= sizeof BI.arg) memcpy(BI.arg, arg, argn);
	memset(cmo, 0, sizeof *cmo);
	cmo->status = BI.status;
	cmo->resp_len = BI.resp_len;
	memcpy(rsp, BI.ans, nrsp < sizeof BI.ans ? nrsp : (uint32_t)sizeof BI.ans);
	if (BI.setstop) xx->d4dstop = 1u;
	return BI.rc;
}

#include "sweet_wlan_l25_glue.h"
#undef printf

/* the engine entry g2.h declares is not part of this test (wlan_l25_host_test.c runs it) */
static int swl25_run(struct swg_wx *xx, unsigned lv) { (void)xx; (void)lv; return 0; }

static int has(const char *s) { return strstr(logbuf, s) != NULL; }
/* how many times the glue recorded the judge layout gap (swl_gap area "l25", T-3784 J4): the gap table, not the log, holds the swl_gap call */
static uint32_t lgap(void)
{
	uint32_t i;

	for (i = 0; i < swl_gap_nd; i++)
		if (strcmp(swl_gap_tab[i].area, "l25") == 0 && strstr(swl_gap_tab[i].what, "store judge layout invalid") != NULL)
			return swl_gap_tab[i].n;
	return 0u;
}
static void clr(void) { loglen = 0; logbuf[0] = 0; }
static unsigned count(const char *s)
{
	unsigned n = 0;
	const char *p = logbuf;

	while ((p = strstr(p, s)) != NULL) { n++; p += strlen(s); }
	return n;
}

#define G1PA  0x880000000ull
#define G1LEN 0x100000u
#define DAPA  0x885000000ull
#define DALEN 0x14000u
#define ALO   0x822A0000u
#define AHI   0x86000000u
#define BLK   0x10000u
#define EVOFF 0x2000u

static uint32_t mem[BLK / 4u];
static unsigned nrd, failnth;           /* reads so far; the 1-based number of the ONE read call that fails (0 = none) */
static int b_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	(void)ctx;
	if (sp != SWG_SP_HOST || a >= BLK || (a & 3u) != 0u)
		return -1;
	nrd++;
	if (failnth != 0u && nrd == failnth)
		return -1;
	*v = mem[a / 4u];
	return 0;
}
static int b_wr(void *ctx, int sp, uint32_t a, uint32_t v) { (void)ctx; (void)sp; (void)a; (void)v; return -1; }
static void b_delay(void *ctx, unsigned ms) { (void)ctx; (void)ms; }
static const struct swg_bus BUS = { b_rd, b_wr, b_delay, NULL, NULL, NULL, NULL, NULL, NULL };

static uint32_t wl_mem[0x400000u / 4u];       /* the judge's view of the G1 block (T-43af J3) */
static int wl_failrd;
static int b_wl_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	(void)ctx;
	if (wl_failrd || sp != SWL25W_SP_HOST || a >= sizeof wl_mem || (a & 3u) != 0u)
		return -1;
	*v = wl_mem[a / 4u];
	return 0;
}

static const uint8_t TGT[6] = { 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x01 };
static const uint8_t OTHER[6] = { 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x02 };
static const uint8_t RSN22[22] = { 0x30, 0x14, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x02, 0x00, 0x00 };

static struct swg_wx x;
static struct swl25g G;
static struct swc_creds CR;

static void world(void)
{
	memset(&x, 0, sizeof x);
	nrd = failnth = 0u;
	x.b = &BUS;
	x.in.blk = G1PA;
	x.in.blk_len = G1LEN;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_W)] = G1PA + 0x1000u;
	x.in.idx[SWG_IDX(SWD_IDX_H2D_R)] = G1PA + 0x1400u;
	x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = G1PA + 0x1800u;
	x.in.idx[SWG_IDX(SWD_IDX_D2H_R)] = G1PA + 0x2000u;
	x.in.ring[0] = G1PA + 0x10000u;
	x.in.ring[1] = G1PA + 0x20000u;
	x.in.ring[2] = G1PA + 0x30000u;
	x.in.ring[3] = G1PA + 0x40000u;
	x.in.ring[4] = G1PA + 0x50000u;
	x.in.evbuf = G1PA + EVOFF;
	x.elv = 25u;
	x.emax = SWG_E_MAX;     /* the cap swg_e sets (L22c, T-0f36); the test calls swg_e_event directly */
	x.d4wr = 5u;
	x.d4rd = 70u;
	memset(&G, 0xA5, sizeof G);                     /* the arm must not depend on what the struct held */
	memset(&CR, 0, sizeof CR);
	memset(CR.pmk, 0xA1, 32);
	CR.pmk[1] = 0xA2; CR.pmk[2] = 0xA3; CR.pmk[3] = 0xA4;
	CR.ip[0] = 192; CR.ip[1] = 168; CR.ip[2] = 7; CR.ip[3] = 44;
	CR.gw[0] = 192; CR.gw[1] = 168; CR.gw[2] = 7; CR.gw[3] = 1;
	CR.ssid_len = 3; memcpy(CR.ssid, "net", 3);
	clr();
}

static int arm(uint64_t pa, uint32_t len)
{
	return swl25g_arm(&x, &G, &CR, TGT, pa, len, ALO, AHI);
}

/* nothing is armed: x->l25c NULL, no hook, no DATA block, a zero cfg */
static int refused(void)
{
	static const uint8_t Z[sizeof G];

	return x.l25c == NULL && x.bssn == NULL && x.bssc == NULL && x.in.data == 0u && x.in.data_len == 0u && memcmp(&G, Z, sizeof G) == 0;
}

/* one scripted ESCAN_RESULT event (q = 0, rx offset 0) of one wl_bss_info_v109 record carrying the 22 B RSNE after SSID and rates IEs */
static int event(const uint8_t *bssid)
{
	uint8_t b[512], in[SWD4B_D2H_LEN], *f = b, *e, *s;
	uint32_t n = 0, edl, rl, padded;
	uint8_t ies[64];

	ies[n++] = 0; ies[n++] = 3; memcpy(ies + n, "net", 3); n += 3;
	memcpy(ies + n, RSN22, 22); n += 22;                            /* 27 B */
	padded = (n + 3u) & ~3u;
	rl = 132u + padded;
	edl = 12u + rl;
	memset(b, 0, sizeof b);
	f[12] = 0x88; f[13] = 0x6c; f[19] = 0x00; f[20] = 0x10; f[21] = 0x18; f[23] = 0x01;
	f[31] = 69; f[35] = 8;
	f[44] = (uint8_t)(edl >> 24); f[45] = (uint8_t)(edl >> 16); f[46] = (uint8_t)(edl >> 8); f[47] = (uint8_t)edl;
	e = f + 72;
	e[8] = 0x34; e[9] = 0x12; e[10] = 1;
	s = e + 12;
	s[0] = 109;
	s[4] = (uint8_t)rl;
	memcpy(s + 8, bssid, 6);
	s[18] = 3; memcpy(s + 19, "net", 3);
	s[88] = 6;
	s[116] = 132;
	s[120] = (uint8_t)n;
	memcpy(s + 132, ies, n);
	memcpy(&mem[EVOFF / 4u], b, sizeof b);
	memset(in, 0, sizeof in);
	in[4] = (uint8_t)SWG_D4_REQID;
	in[12] = (uint8_t)(72u + edl); in[13] = (uint8_t)((72u + edl) >> 8);
	x.rxoff = 0;
	x.evn = x.evq = 0;
	x.epid[0] = SWG_D4_REQID;       /* the scripted event names the buffer posted in slot 0; a consumed slot is cleared (T-fad2), so every call re-arms it */
	x.d4dstop = 0;
	{
		struct swg_ev ev;

		return swg_e_event(&x, in, &ev);
	}
}

int main(void)
{
	struct swl25 l;
	static const uint8_t Z32[32];
	uint64_t pa;

	(void)swg_go; (void)swl25_flow; (void)swl25_rxposts; (void)swl25_rxrepost; (void)swl25r_status;

	/* A1 */
	world();
	CHECK(arm(DAPA, DALEN) == 1);
	CHECK(x.in.data == DAPA && x.in.data_len == DALEN && x.l25c == &G.cfg && x.bssn == swl25g_note && x.bssc == &G);
	CHECK(memcmp(G.cfg.pmk, CR.pmk, 32) == 0 && memcmp(G.cfg.ip, CR.ip, 4) == 0 && memcmp(G.cfg.gw, CR.gw, 4) == 0 && memcmp(G.cfg.bssid, TGT, 6) == 0 && memcmp(G.tgt.bssid, TGT, 6) == 0);
	CHECK(memcmp(G.cfg.mac, Z32, 6) == 0 && G.cfg.rsne_assoc_len == 0u && G.cfg.rsne_beacon_len == 0u && G.tgt.state == SWL25R_NONE && G.tgt.rsne_len == 0u);
	CHECK(!has("WIFI GAP") && loglen == 0u);
	memset(&l, 0, sizeof l);
	x.rxfw_state = SWDP_RXFW_OK; x.rxfw = 16u;                                /* patch 0357: what swg_e read at shared +0x22 (the pool choice logs a GAP when it was not read) */
	CHECK(swl25_env(&x, &l) == 0 && !x.d4dstop && !has("WIFI GAP"));       /* the engine's own audit accepts the placed block */

	/* B1..B6 */
	world(); CR.pmk[0] = 0; memset(CR.pmk, 0, 32);
	CHECK(arm(DAPA, DALEN) == 0 && refused() && has("WIFI GAP: L25 PMK missing\n") && count("WIFI GAP: L25 ") == 1u);
	world(); memset(CR.ip, 0, 4);
	CHECK(arm(DAPA, DALEN) == 0 && refused() && has("WIFI GAP: L25 station IP missing\n") && count("WIFI GAP: L25 ") == 1u);
	world(); memset(CR.gw, 0, 4);
	CHECK(arm(DAPA, DALEN) == 0 && refused() && has("WIFI GAP: L25 gateway missing\n") && count("WIFI GAP: L25 ") == 1u);
	world(); memset(CR.ip, 0, 4); memset(CR.gw, 0, 4);                      /* patch 0281: ip == 0 AND gw == 0 = DHCP credentials: armed, a log line, no GAP, the addresses stay zero */
	CHECK(arm(DAPA, DALEN) == 1 && !has("WIFI GAP") && has("SWEET WLAN: L25 glue: DHCP credentials (no static station IP or gateway)") && swl25g_zero(G.cfg.ip, 4u) && swl25g_zero(G.cfg.gw, 4u) && memcmp(G.cfg.pmk, CR.pmk, 32) == 0);
	world(); memset(CR.ip, 0, 4); memset(CR.gw, 0, 4); memset(CR.pmk, 0, 32);   /* DHCP does not excuse a missing PMK */
	CHECK(arm(DAPA, DALEN) == 0 && refused() && has("WIFI GAP: L25 PMK missing\n") && !has("station IP missing") && !has("gateway missing"));
	{
		static const uint8_t ZB[6];

		world(); CR.ssid_len = 0;
		CHECK(swl25g_arm(&x, &G, &CR, ZB, DAPA, DALEN, ALO, AHI) == 0 && refused() && has("WIFI GAP: L25 join target BSSID missing (wildcard)\n") && count("WIFI GAP: L25 ") == 1u);
		world(); CR.ssid_len = 0;
		CHECK(swl25g_arm(&x, &G, &CR, NULL, DAPA, DALEN, ALO, AHI) == 0 && refused() && has("L25 join target BSSID missing") && count("WIFI GAP: L25 ") == 1u);
		world(); CR.ssid_len = 33;
		CHECK(swl25g_arm(&x, &G, &CR, ZB, DAPA, DALEN, ALO, AHI) == 0 && refused() && has("L25 join target BSSID missing"));
		/* T-fc10 H2: a wildcard join with the credentials' SSID arms; the target is that SSID's first scan record, cfg.bssid stays zero until swl25g_note fills it */
		world();
		CHECK(swl25g_arm(&x, &G, &CR, ZB, DAPA, DALEN, ALO, AHI) == 1 && x.l25c == &G.cfg && x.bssn == swl25g_note && G.tgt.ssid_len == 3u && memcmp(G.tgt.ssid, "net", 3) == 0 && swl25g_zero(G.tgt.bssid, 6u) && swl25g_zero(G.cfg.bssid, 6u) && !has("WIFI GAP"));
		world();
		CHECK(swl25g_arm(&x, &G, &CR, NULL, DAPA, DALEN, ALO, AHI) == 1 && G.tgt.ssid_len == 3u && swl25g_zero(G.cfg.bssid, 6u));
		world();
		CHECK(arm(DAPA, DALEN) == 1 && G.tgt.ssid_len == 0u && memcmp(G.cfg.bssid, TGT, 6) == 0 && memcmp(G.tgt.bssid, TGT, 6) == 0);      /* a given BSSID: no SSID rule */
	}
	world();
	CHECK(swl25g_arm(&x, &G, NULL, TGT, DAPA, DALEN, ALO, AHI) == 0 && refused() && count("WIFI GAP: L25 ") == 3u && has("PMK missing") && has("station IP missing") && has("gateway missing"));

	/* D1..D9 */
	world(); CHECK(arm(0u, DALEN) == 0 && refused() && has("WIFI GAP: L25 DATA block missing\n") && count("WIFI GAP: L25 ") == 1u);
	world(); CHECK(arm(DAPA, 0u) == 0 && refused() && has("DATA block missing"));
	world(); CHECK(arm(DAPA, DALEN - 0x1000u) == 0 && refused() && has("WIFI GAP: L25 DATA block too small or not page aligned\n"));
	world(); CHECK(arm(DAPA + 0x800u, DALEN) == 0 && refused() && has("too small or not page aligned"));
	world(); CHECK(arm(G1PA + G1LEN - 0x1000u, DALEN) == 0 && refused() && has("WIFI GAP: L25 DATA block overlaps the G1 block\n"));      /* starts inside the G1 block */
	world(); CHECK(arm(G1PA - 0x4000u, DALEN) == 0 && refused() && has("overlaps the G1 block"));                                                /* ends inside it */
	world(); CHECK(arm(G1PA - 0x100000u, 0x300000u) == 0 && refused() && has("overlaps the G1 block"));                                          /* encloses it */
	world(); CHECK(arm(0xFFFFFFFFFFFFF000ull, DALEN) == 0 && refused() && has("WIFI GAP: L25 DATA block wraps\n"));
	world(); CHECK(arm(0x880000000ull | (ALO - 0x1000u), DALEN) == 0 && refused() && has("WIFI GAP: L25 DATA block outside the DMA alias window\n"));   /* low 32 bits below the window */
	world(); CHECK(arm(0x880000000ull | (AHI - DALEN + 0x1000u), DALEN) == 0 && refused() && has("outside the DMA alias window"));                          /* ends one page above it */
	world(); pa = 0x900000000ull | ALO; CHECK(arm(pa, DALEN) == 1 && x.in.data == pa && !has("WIFI GAP"));                                      /* the low edge */
	world(); pa = 0x900000000ull | (AHI - DALEN); CHECK(arm(pa, DALEN) == 1 && x.in.data == pa && !has("WIFI GAP"));                          /* the high edge: ends exactly at AHI */
	world(); CHECK(arm(0x20000000ull, DALEN) == 1 && x.in.data == 0x20000000ull && !has("WIFI GAP"));                                           /* below 4 GiB: no alias */

	/* D10: blocks that touch the G1 block without sharing a byte are fine (G1 at 0x20100000, below 4 GiB: no alias window involved) */
	world(); x.in.blk = 0x20100000ull; x.in.blk_len = 0x100000u;
	CHECK(arm(0x20100000ull - DALEN, DALEN) == 1 && x.in.data == 0x20100000ull - DALEN && !has("WIFI GAP"));
	world(); x.in.blk = 0x20100000ull; x.in.blk_len = 0x100000u;
	CHECK(arm(0x20200000ull, DALEN) == 1 && x.in.data == 0x20200000ull && !has("WIFI GAP"));
	world(); x.in.blk = 0x20100000ull; x.in.blk_len = 0x100000u;
	CHECK(arm(0x20200000ull - 0x1000u, DALEN) == 0 && refused() && has("overlaps the G1 block"));

	/* D11: a block longer than the whole window (and than alias_hi: the subtraction must not wrap) */
	world(); CHECK(arm(0x900000000ull | ALO, 0x90000000u) == 0 && refused() && has("outside the DMA alias window"));

	/* X1 */
	world(); memset(CR.ip, 0, 4);
	CHECK(arm(0u, 0u) == 0 && refused() && has("DATA block missing") && has("station IP missing") && count("WIFI GAP: L25 ") == 2u);

	/* H1 */
	world();
	CHECK(arm(DAPA, DALEN) == 1);
	CHECK(event(OTHER) == 0 && G.cfg.rsne_assoc_len == 0u && G.cfg.rsne_beacon_len == 0u && G.tgt.state == SWL25R_NONE);
	CHECK(event(TGT) == 0 && !x.d4dstop && G.tgt.state == SWL25R_OK);
	CHECK(G.cfg.rsne_assoc_len == 22u && G.cfg.rsne_beacon_len == 22u && memcmp(G.cfg.rsne_assoc, RSN22, 22) == 0 && memcmp(G.cfg.rsne_beacon, RSN22, 22) == 0);
	CHECK(has("L23 rsne of the join target: 22 B"));

	/* H1b (T-fc10 H2): a wildcard join arms with the SSID; the first record of the SSID ("net") names the AP and swl25g_note puts its BSSID into cfg.bssid together with the RSNE */
	{
		static const uint8_t ZB[6];

		world();
		CHECK(swl25g_arm(&x, &G, &CR, ZB, DAPA, DALEN, ALO, AHI) == 1 && swl25g_zero(G.cfg.bssid, 6u));
		CHECK(event(OTHER) == 0 && !x.d4dstop && G.tgt.state == SWL25R_OK && memcmp(G.tgt.bssid, OTHER, 6) == 0 && memcmp(G.cfg.bssid, OTHER, 6) == 0);
		CHECK(G.cfg.rsne_assoc_len == 22u && memcmp(G.cfg.rsne_assoc, RSN22, 22) == 0 && swl25r_status(&G.tgt) == 0);
	}

	/* H2: a failing bus read propagates through swl25g_note (the foreign-BSSID event costs `base` reads, the hook's own reads follow: 1 = length word, 2 and 3 = ie_offset/ie_length words) */
	world();
	CHECK(arm(DAPA, DALEN) == 1);
	nrd = 0u;
	CHECK(event(OTHER) == 0);
	{
		unsigned base = nrd, k;

		for (k = 1; k <= 3u; k++) {
			world();
			CHECK(arm(DAPA, DALEN) == 1);
			failnth = base + k;
			CHECK(event(TGT) < 0 && x.stopped && has("a d4e buffer read failed") && !has("RSNE not read"));
			CHECK(G.tgt.state == SWL25R_NONE && G.tgt.rsne_len == 0u && G.cfg.rsne_assoc_len == 0u && G.cfg.rsne_beacon_len == 0u);
			CHECK(swl25g_zero(G.cfg.rsne_assoc, (unsigned)sizeof G.cfg.rsne_assoc) && swl25g_zero(G.cfg.rsne_beacon, (unsigned)sizeof G.cfg.rsne_beacon));
		}
	}

	/* H3: swl25g_note called with a stale rsne_len in a target that is not OK (state NONE and BAD, no bus read happens for the NONE case: foreign BSSID): nothing is copied */
	{
		static const int st[2] = { SWL25R_NONE, SWL25R_BAD };
		unsigned k;

		for (k = 0; k < 2u; k++) {
			world();
			CHECK(arm(DAPA, DALEN) == 1);
			G.tgt.state = (uint8_t)st[k];
			G.tgt.rsne_len = 22u;
			memcpy(G.tgt.rsne, RSN22, 22);
			CHECK(event(OTHER) == 0);                  /* foreign BSSID: swl25r_note returns 0 without touching the target */
			CHECK(G.cfg.rsne_assoc_len == 0u && G.cfg.rsne_beacon_len == 0u);
			CHECK(swl25g_zero(G.cfg.rsne_assoc, (unsigned)sizeof G.cfg.rsne_assoc) && swl25g_zero(G.cfg.rsne_beacon, (unsigned)sizeof G.cfg.rsne_beacon));
		}
	}

	/* J1..J3 (T-43af): the pure parts of the L25 judge wiring */
	{
		struct swd_plan pl;
		struct swl25w w;
		uint32_t r2;
		int sv;
		uint32_t cm[6] = { 0 };

		CHECK(swd_plan_make(SWD_RW_INDEX_SZ, SWD_MAX_SUB, SWD_MAX_CPL, &pl) == 0);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, DALEN, b_wl_rd, NULL) == 0 && swl25w_layout_ok(&w));
		/* patch 0357: the judge's rx buffer count comes from the block the glue allocated: SWL25_DATA_MIN = 16, the largest block 255, never above 255 */
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, SWL25_DATA_MIN, b_wl_rd, NULL) == 0 && w.rx_n == 16u && swl25w_rxn(&w) == 16u);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, SWL25_DATA_MAX, b_wl_rd, NULL) == 0 && w.rx_n == 255u);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, SWL25_DATA_MAX + 3u * 2048u, b_wl_rd, NULL) == 0 && w.rx_n == 255u);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, SWL25_DATA_MIN + 2048u * 7u + 100u, b_wl_rd, NULL) == 0 && w.rx_n == 23u);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, SWL25_DATA_MAX - 4u, b_wl_rd, NULL) == 0 && w.rx_n == 254u);
		CHECK(w.flowid == SWL25_FLOWID && w.g1_len == pl.total && w.ring[2] == pl.b[SWD_H2D_CTRL + 2].off && w.depth[2] == swd_rings[SWD_H2D_CTRL + 2].items && w.d2h_r == pl.b[SWD_IDX_D2H_R].off);
		CHECK(w.ioctbuf == pl.b[SWD_IOCTBUF].off && w.respbuf == pl.b[SWD_RESPBUF].off && w.evbuf == pl.b[SWD_EVBUF].off && w.rd == b_wl_rd);
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, 0x1000u, b_wl_rd, NULL) != 0);              /* DATA block too small for the flow items and buffers */
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, G1PA + 0x1000u, DALEN, b_wl_rd, NULL) != 0);      /* DATA block inside the G1 block */
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, DALEN, NULL, NULL) != 0);                   /* no accessor: the judge's own layout check fails it */

		world();
		CHECK(arm(DAPA, DALEN) == 1);
		memset(&w, 0, sizeof w);
		swl25g_wl_bssid(&w, &G);
		CHECK(w.bssid_set == 1u && memcmp(w.bssid, TGT, 6) == 0);
		memset(&G.cfg.bssid, 0, 6);
		swl25g_wl_bssid(&w, &G);
		CHECK(w.bssid_set == 0u && swl25g_zero(w.bssid, 6u));
		swl25g_disarm(&x, &G);

		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, DALEN, b_wl_rd, NULL) == 0);
		r2 = w.ring[2];
		for (sv = 0; sv < 8; sv++) {
			memset(wl_mem, 0, sizeof wl_mem);
			w.flow_created = 1u; w.flow_open = 0u;
			cm[0] = sv == 1 ? 0x0Au : SWDP_MT_FLOW_CREATE_CMPLT;          /* type */
			cm[2] = sv == 2 ? 0xFFFE0000u | 0u : 0u;                       /* +8 status (i16), +10 ring id */
			cm[2] = (cm[2] & 0xFFFFu) | ((sv == 3 ? 3u : sv == 2 ? 2u : w.flowid) << 16);
			if (sv == 2) { cm[2] = (cm[2] & 0xFFFF0000u) | 0xFFF8u; cm[2] = (cm[2] & 0xFFFFu) | (w.flowid << 16); }       /* status -8, right ring */
			memcpy(&wl_mem[(r2 + 24u * 3u) / 4u], cm, sizeof cm);          /* the item sits at slot 3 */
			if (sv == 4) w.flow_created = 0u;                              /* no create was committed */
			swl25g_wl_flow(&w, sv == 5 ? 4u : 2u, sv == 5 ? 6u : 5u);     /* 5: slot 3 is outside [4, 6) */
			if (sv == 6) { wl_failrd = 1; }
			if (sv == 6) { w.flow_open = 0u; swl25g_wl_flow(&w, 2u, 5u); wl_failrd = 0; }
			if (sv == 7) { w.flow_open = 1u; cm[0] = 0x0Au; memcpy(&wl_mem[(r2 + 24u * 3u) / 4u], cm, sizeof cm); swl25g_wl_flow(&w, 2u, 5u); }
			CHECK(w.flow_open == (sv == 0 || sv == 7 ? 1u : 0u));
		}
		CHECK(swl25g_wl_layout(&w, &pl, G1PA, DAPA, DALEN, b_wl_rd, NULL) == 0);
		w.flow_created = 1u;                                                  /* a ring wrap: old 62 -> new 1 reaches slots 62, 63, 0 */
		memset(wl_mem, 0, sizeof wl_mem);
		cm[0] = SWDP_MT_FLOW_CREATE_CMPLT; cm[2] = w.flowid << 16;
		memcpy(&wl_mem[(w.ring[2] + 0u) / 4u], cm, sizeof cm);
		swl25g_wl_flow(&w, 62u, 1u);
		CHECK(w.flow_open == 1u);
	}

	/* J4 (T-3784, review T-98d9 r1 F1): swl25g_wl_arm, the arm decision of sw_g2_hook. Armed only when the credentials, the DATA block AND the judge layout are all accepted; every refusal leaves no hook, no cfg pointer and a wiped cfg
	 * (the PMK), and the layout refusal adds its own gap line; the credentials check always runs even when the layout is refused */
	{
		struct swd_plan pl;
		struct swl25w w;
		static const uint8_t Z[sizeof w];
		uint32_t g0;

		CHECK(swd_plan_make(SWD_RW_INDEX_SZ, SWD_MAX_SUB, SWD_MAX_CPL, &pl) == 0);
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* everything accepted */
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, DAPA, DALEN, ALO, AHI, b_wl_rd, NULL) == 1);
		CHECK(x.l25c == &G.cfg && x.bssn == swl25g_note && x.bssc == &G && x.in.data == DAPA && swl25w_layout_ok(&w) && w.rd == b_wl_rd && w.escan_sync == &x.escan_sync);       /* review 0292 F2: the judge reads the id of the escan START from the engine */
		CHECK(memcmp(G.cfg.pmk, CR.pmk, 32) == 0 && !has("WIFI GAP") && !has("store judge layout") && loglen == 0u && lgap() == g0);
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* the layout is refused (no accessor), the credentials and the DATA block are fine: not armed, the hook the arm installed is taken down again, the PMK is wiped */
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, DAPA, DALEN, ALO, AHI, NULL, NULL) == 0);
		CHECK(x.l25c == NULL && x.bssn == NULL && x.bssc == NULL && swl25g_zero((const uint8_t *)&G, (unsigned)sizeof G));
		CHECK(has("SWEET WLAN: L25 glue: store judge layout clause ") && has(" invalid, L25 not armed\n") && count("store judge layout clause ") == 1u && !has("WIFI GAP: L25 ") && lgap() == g0 + 1u && has("WIFI GAP: l25 glue: L25 store judge layout invalid, L25 not armed x"));
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* no DATA block: not armed, only the DATA gap (the layout is not even tried), nothing left */
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, 0u, DALEN, ALO, AHI, b_wl_rd, NULL) == 0);
		CHECK(refused() && has("WIFI GAP: L25 DATA block missing\n") && count("WIFI GAP: L25 ") == 1u && !has("store judge layout") && memcmp(&w, Z, sizeof w) == 0 && lgap() == g0);
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* the PMK is missing, the layout is fine: not armed, the credentials gap prints */
		memset(CR.pmk, 0, 32);
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, DAPA, DALEN, ALO, AHI, b_wl_rd, NULL) == 0);
		CHECK(refused() && has("WIFI GAP: L25 PMK missing\n") && count("WIFI GAP: L25 ") == 1u && !has("store judge layout") && lgap() == g0);
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* both refused: BOTH lines print (the credentials check runs although the layout failed first), not armed */
		memset(CR.pmk, 0, 32);
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, DAPA, DALEN, ALO, AHI, NULL, NULL) == 0);
		CHECK(refused() && has("WIFI GAP: L25 PMK missing\n") && count("WIFI GAP: L25 ") == 1u && count("store judge layout clause ") == 1u && lgap() == g0 + 1u);
		world(); memset(&w, 0, sizeof w); g0 = lgap();      /* the DATA block overlaps G1: the arm refuses it, the layout refuses it too (clause > 0): not armed, two lines */
		CHECK(swl25g_wl_arm(&x, &G, &w, &pl, G1PA, &CR, TGT, G1PA + 0x1000u, DALEN, ALO, AHI, b_wl_rd, NULL) == 0);
		CHECK(refused() && has("WIFI GAP: L25 DATA block overlaps the G1 block\n") && count("store judge layout clause ") == 1u && lgap() == g0 + 1u);
	}

	/* ---- patch 0288: the join target from WLC_GET_BSS_INFO (T-fca2). The answer is built from the cited layout: buf+0 echo of the length word, the record at buf+4: version 109 @0, length @4, BSSID @8, SSID_len @18, SSID @19,
	 * chanspec @72, RSSI @78 (i16), ctl_ch @88, ie_offset @116 (u16), ie_length @120 (u32), the IEs at ie_offset */
	{
		static const uint8_t ZB[6];
		static const uint8_t HOT[6] = { 0x0a, 0x11, 0x22, 0x33, 0x44, 0x55 };

#define BI_BUILD(IEO, IELPAD) do { \
		uint32_t n_ = 0u, rl_; \
		memset(&BI, 0, sizeof BI); \
		BI.ans[0] = 0x00; BI.ans[1] = 0x04; \
		BI.ans[4] = 109u; \
		memcpy(BI.ans + 4 + 8, HOT, 6); \
		BI.ans[4 + 18] = 3u; memcpy(BI.ans + 4 + 19, "net", 3); \
		BI.ans[4 + 72] = 0x06u; BI.ans[4 + 73] = 0x10u; \
		BI.ans[4 + 78] = 0xCEu; BI.ans[4 + 79] = 0xFFu; \
		BI.ans[4 + 88] = 6u; \
		BI.ans[4 + 116] = (uint8_t)(IEO); \
		memcpy(BI.ans + 4 + (IEO), "\x00\x03" "net", 5); n_ = 5u; \
		memcpy(BI.ans + 4 + (IEO) + n_, RSN22, 22); n_ += 22u; \
		BI.ans[4 + 120] = (uint8_t)n_; \
		rl_ = (IEO) + ((n_ + 3u) & ~3u); \
		BI.ans[4 + 4] = (uint8_t)rl_; BI.ans[4 + 5] = (uint8_t)(rl_ >> 8); \
		BI.resp_len = 4u + rl_ + (IELPAD); \
	} while (0)
#define BI_WILD() do { world(); CHECK(swl25g_arm(&x, &G, &CR, ZB, DAPA, DALEN, ALO, AHI) == 1 && x.bssget == swl25g_bss_info); clr(); } while (0)

		/* I1: the good record: ONE read-only GET (cmd 136, empty name, payload 1024 = the length word 00 04 00 00, soft, answer room 1024), the AP and its RSNE land in the cfg, one log line with channel and RSSI only */
		BI_BUILD(132u, 0u);
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && !x.d4dstop && !x.stopped);
		CHECK(BI.n == 1u && BI.cmd == 136u && BI.name[0] == '\0' && BI.argn == 3u && BI.arg[0] == 0x04u && BI.arg[1] == 0u && BI.arg[2] == 0u && BI.wlen == 1024u && BI.nrsp == 1024u && BI.soft == 1 && BI.repost == 1);
		CHECK(G.tgt.state == SWL25R_OK && memcmp(G.tgt.bssid, HOT, 6) == 0 && memcmp(G.cfg.bssid, HOT, 6) == 0 && G.cfg.rsne_assoc_len == 22u && G.cfg.rsne_beacon_len == 22u && memcmp(G.cfg.rsne_assoc, RSN22, 22) == 0 && memcmp(G.cfg.rsne_beacon, RSN22, 22) == 0);
		CHECK(has("SWEET WLAN: L24 target from WLC_GET_BSS_INFO (not in the scan): chan 6 rssi -50\n") && !has("WIFI GAP") && G.tgt.via_bss_info == 1u);
		CHECK(memmem(logbuf, loglen, HOT, 6) == NULL && strstr(logbuf, "0a:11") == NULL && strstr(logbuf, "0a1122") == NULL);       /* no BSSID in the log */
		/* I1b: ie_offset 124 (the head only) is as good */
		BI_BUILD(124u, 0u);
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && G.tgt.state == SWL25R_OK && G.cfg.rsne_assoc_len == 22u && !has("WIFI GAP"));
		/* I2: nothing to do (no call): a target the scan saw (OK), a scan BSSID of the SSID (wildcard resolved), a configured BSSID, a BAD record */
		BI_BUILD(132u, 0u);
		world(); CHECK(arm(DAPA, DALEN) == 1); clr();
		CHECK(x.bssget(&x) == 0 && BI.n == 0u && G.tgt.state == SWL25R_NONE);       /* configured BSSID */
		BI_BUILD(132u, 0u);
		BI_WILD(); memcpy(G.tgt.bssid, OTHER, 6);
		CHECK(x.bssget(&x) == 0 && BI.n == 0u);       /* the scan named the AP */
		BI_BUILD(132u, 0u);
		BI_WILD(); G.tgt.state = SWL25R_OK;
		CHECK(x.bssget(&x) == 0 && BI.n == 0u);
		BI_BUILD(132u, 0u);
		BI_WILD(); G.tgt.state = SWL25R_BAD;
		CHECK(x.bssget(&x) == 0 && BI.n == 0u);
		BI_BUILD(132u, 0u);
		BI_WILD(); G.tgt.ssid_len = 0u;
		CHECK(x.bssget(&x) == 0 && BI.n == 0u);       /* no credentials' SSID: nothing to match */
		/* I2b: swl25r_note_bss_info itself takes only a never-seen target (state NONE, zero BSSID, credentials' SSID): called directly with a good record, the other states change nothing and print nothing */
		BI_BUILD(132u, 0u);
		BI_WILD(); G.tgt.state = SWL25R_OK;
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 0 && !has("target from WLC_GET_BSS_INFO") && swl25g_zero(G.tgt.bssid, 6u) && G.tgt.via_bss_info == 0u);
		BI_WILD(); G.tgt.state = SWL25R_BAD;
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 0 && !has("target from WLC_GET_BSS_INFO") && G.tgt.state == SWL25R_BAD);
		BI_WILD(); memcpy(G.tgt.bssid, OTHER, 6);
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 0 && !has("target from WLC_GET_BSS_INFO") && memcmp(G.tgt.bssid, OTHER, 6) == 0 && G.tgt.state == SWL25R_NONE);
		BI_WILD(); G.tgt.ssid_len = 0u;
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 0 && !has("WIFI GAP") && !has("target from WLC_GET_BSS_INFO") && G.tgt.state == SWL25R_NONE);
		BI_WILD();
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 1 && G.tgt.state == SWL25R_OK);       /* 1 = taken */
		BI_BUILD(132u, 0u);
		BI.ans[4 + 132 + 5] = 0x2du;
		BI_WILD();
		CHECK(swl25r_note_bss_info(&x, BI.ans, BI.resp_len) == 0 && G.tgt.state == SWL25R_BAD);       /* a record without RSNE is not "taken" */
		/* I3: another SSID: refused with a GAP, no target, the cfg untouched */
		BI_BUILD(132u, 0u);
		BI.ans[4 + 19] = 'N';
		BI_WILD();
		{ const uint32_t g0_ = swl_gap_tot; CHECK(x.bssget(&x) == 0 && swl_gap_tot == g0_ + 1u); }
		BI_BUILD(132u, 0u);
		BI.ans[4 + 19] = 'N';
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && BI.n == 1u && has("WIFI GAP: L25 WLC_GET_BSS_INFO names another SSID than the credentials': refused, no target\n") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.tgt.bssid, 6u) && swl25g_zero(G.cfg.bssid, 6u) && G.cfg.rsne_beacon_len == 0u);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 18] = 2u;                     /* a prefix of the credentials' SSID is not the SSID */
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("names another SSID") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 18] = 4u; BI.ans[4 + 22] = 'x';
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("names another SSID") && G.tgt.state == SWL25R_NONE);
		/* I4: unusable answers, each one GAP and no target: truncated at every cut below the record, length above the bytes, ie_offset/ie_length mismatch, version 114, SSID length 0 / 33, zero and group BSSID, no RSNE */
		{
			static const uint32_t cut[] = { 0u, 3u, 4u, 100u, 127u };
			unsigned k;

			for (k = 0; k < sizeof cut / sizeof cut[0]; k++) {
				BI_BUILD(132u, 0u);
				BI.resp_len = cut[k];
				BI_WILD();
				CHECK(x.bssget(&x) == 0 && has("WIFI GAP: BSS_INFO record unusable: answer shorter than the 4 B length word plus the 124 B record head") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
			}
		}
		BI_BUILD(132u, 0u);
		BI.resp_len -= 4u;                          /* the record claims 4 bytes more than the answer carries */
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("WIFI GAP: BSS_INFO record unusable: record length above the event data") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
		BI_BUILD(132u, 0u);
		BI.ans[4 + 4] += 4u;                        /* length larger than ie_offset + ie_length: oversized record */
		BI.resp_len += 4u;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("ie_offset + ie_length does not match length") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 116] = 120u;                     /* ie_offset inside the head */
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("ie_offset/ie_length outside the record") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 120] = 0xF0u; BI.ans[4 + 121] = 0xFFu;       /* ie_length far above length */
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("ie_offset/ie_length outside the record") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4] = 114u;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("record version is not 109") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 18] = 0u;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("SSID length is not 1..32") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 18] = 33u;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("SSID length is not 1..32") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		memset(BI.ans + 4 + 8, 0, 6);
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("BSSID is zero or a group address") && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.ans[4 + 8] = 0x0bu;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("BSSID is zero or a group address") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
		BI_BUILD(132u, 0u);
		BI.ans[4 + 132 + 5] = 0x2du;                /* the RSNE (id 48) becomes id 45: no RSNE in the record */
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("RSNE not read: no RSNE (IE 48) in the target record") && G.tgt.state == SWL25R_BAD && G.cfg.rsne_beacon_len == 0u && G.tgt.via_bss_info == 0u);
		/* I5: the firmware refuses (status), or the transport fails: a GAP, no target; a soft stop is passed on, a dead bus is negative */
		BI_BUILD(132u, 0u);
		BI.status = -23;
		BI_WILD();
		{ const uint32_t g0_ = swl_gap_tot; CHECK(x.bssget(&x) == 0 && swl_gap_tot == g0_ + 1u); }
		BI_BUILD(132u, 0u);
		BI.status = -23;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("WIFI GAP: L25 WLC_GET_BSS_INFO refused status=-23: the join target stays unknown\n") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
		BI_BUILD(132u, 0u);
		BI.rc = -5;
		BI_WILD();
		CHECK(x.bssget(&x) == -5 && G.tgt.state == SWL25R_NONE);
		BI_BUILD(132u, 0u);
		BI.setstop = 1;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && x.d4dstop == 1u && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));       /* the record of a stopped call is never taken */
		/* I6: the pure parser keeps a 32-byte SSID and a 64-byte RSNE; disarm drops the hook */
		BI_BUILD(132u, 0u);
		BI_WILD();
		swl25g_disarm(&x, &G);
		CHECK(x.bssget == NULL && x.bssn == NULL);
		/* I7 (review 0288 L1): the firmware claims a 2000 B answer and a 1500 B record: the glue clamps to the 1024 B buffer, so the record is above the data (never read past the buffer) */
		BI_BUILD(132u, 0u);
		BI.ans[4 + 4] = 0xDCu; BI.ans[4 + 5] = 0x05u;
		BI.resp_len = 2000u;
		BI_WILD();
		CHECK(x.bssget(&x) == 0 && has("WIFI GAP: BSS_INFO record unusable: record length above the event data") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
		/* I8 (review 0288 L2): a 32-byte SSID is compared in full: equal is taken, a difference in the last byte is refused */
		{
			static const char S32[] = "abcdefghijklmnopqrstuvwxyz012345";
			int v;

			for (v = 0; v < 2; v++) {
				BI_BUILD(132u, 0u);
				BI.ans[4 + 18] = 32u; memcpy(BI.ans + 4 + 19, S32, 32);
				if (v == 1) BI.ans[4 + 19 + 31] = '6';
				BI_WILD();
				G.tgt.ssid_len = 32u; memcpy(G.tgt.ssid, S32, 32);
				if (v == 0) CHECK(x.bssget(&x) == 0 && G.tgt.state == SWL25R_OK && memcmp(G.cfg.bssid, HOT, 6) == 0 && !has("WIFI GAP"));
				else CHECK(x.bssget(&x) == 0 && has("names another SSID") && G.tgt.state == SWL25R_NONE && swl25g_zero(G.cfg.bssid, 6u));
			}
		}
#undef BI_BUILD
#undef BI_WILD
	}

	/* S1: nothing printed so far carries a byte of the PMK (0xA1 repeated; "a1a2a3a4" is its first four bytes in hex) */
	CHECK(!has("a1a2a3a4") && !has("A1A2A3A4") && !has("a1a1a1a1"));

	/* W1 */
	swl25g_disarm(&x, &G);
	CHECK(x.l25c == NULL && x.bssn == NULL && x.bssc == NULL);
	{
		static const uint8_t Z[sizeof G];

		CHECK(memcmp(&G, Z, sizeof G) == 0);
	}

	if (fails != 0u) {
		fprintf(stderr, "%s", logbuf);
		printf("RESULT: FAIL wlan_glue_l25_host_test %u checks failed\n", fails);
		return 1;
	}
	fprintf(stdout, "RESULT: PASS wlan_glue_l25_host_test\n");
	return 0;
}
