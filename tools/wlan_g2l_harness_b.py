#!/usr/bin/env python3
"""wlan_g2l_harness_b: part B of the C text of the G2L harness of tools/test_wlan_wr_mutants.py (lay(), one(), check() and main(); moved out for R29 by T-0a9d; G2L_HARNESS = G2L_HARNESS_A + G2L_HARNESS_B, byte for byte the old text).

  tools/wlan_g2l_harness_b --self-test    checks the part is plain C text without a triple quote
Last line: RESULT: PASS|FAIL wlan_g2l_harness_b ..."""
import sys

G2L_HARNESS_B = r"""static void lay(uint32_t sh, uint32_t ri, uint32_t rm, const uint32_t *ra, uint32_t rc)
{
	for (uint32_t i = 0; i < BACK_BYTES / 4; i++) {
		back[i] = 0xA5000000u ^ (i * 0x9E3779B1u);
	}
	memset(cfg, 0, sizeof(cfg));
	memset(dbi, 0, sizeof(dbi));
	if (sh + 0x30u >= sh && sh + 0x30u < BACK_BYTES) {
		back[(sh + 0x30u) / 4] = ri;
	}
	for (unsigned k = 0; k < 5u; k++) {
		if (ri + 4u * k >= ri && ri + 4u * k < BACK_BYTES) {     /* a wrapped ring_info (review r2 F3 case) has no words here */
			back[(ri + 4u * k) / 4] = k == 0 ? rm : ra[k - 1u];
		}
	}
	dbi[1] = rc;
	cfg[0x04 / 4] = 0x0002u;        /* the EP Command and BAR0 swl_pre_ep wants */
	cfg[0x10 / 4] = 0x60000004u;
	link_up();
	hw.cfg = cfg;
	hw.atu = atu;
	hw.elbi = elbi;
	hw.iso = iso1;
	hw.mem = mem;
	hw.dbi = dbi;
	hw.g2 = 1;      /* what sw_g2_hook sets before sw_g2_list */
	hw.g2ok = 1;
	hw.g2ep = 0x0002u;
	hw.l3 = SWW_F3_LEVEL;
	lastwin = cfg[0x84 / 4];
	dsb_hook();
	nwin = 0;
	ndsb = 0;
	lastmsg[0] = 0;
	for (unsigned i = 0; i < SW_G2_MAX; i++) {
		hw.g2l[i].done = 1;     /* every built entry must come out done = 0 */
	}
}

/* one case: the layout, the expected result (1 = list built and equal to want(), 0 = refused with an empty list) */
static void one(const char *what, uint32_t sh, uint32_t ri, uint32_t rm, const uint32_t *ra, uint32_t rc, uint32_t end, int ok, int twice)
{
	struct ex e[1700];
	unsigned n = ok ? want(e, sh, ri, ra, rc) : 0, i;
	int r;
	lay(sh, ri, rm, ra, rc);
	r = sw_g2_list(&hw, sh, end, W80);
	if (twice && r == 0) {
		r = sw_g2_list(&hw, sh, end, W80);
	}
	if (ndsb != nwin) {     /* the only stores of the list build are window moves, each one a change */
		bad++;
		printf("BAD G2L %s: %u stores, %u window changes\n", what, ndsb, nwin);
	}
	if (!ok && expwhy != NULL) {
		char pat[64];
		snprintf(pat, sizeof(pat), "refused (%s)", expwhy);
		if (strstr(lastmsg, pat) == NULL) {
			bad++;
			printf("BAD G2L %s: refusal does not name \"%s\": %s\n", what, expwhy, lastmsg);
		}
	}
	if (cfg[0x84 / 4] != 0u && !allowwin) {      /* W-D3c: the hook hands over with cfg 0x84 = 0 and W17 expects it back, built or refused */
		bad++;
		printf("BAD G2L %s: cfg 0x84 is %#x on return\n", what, cfg[0x84 / 4]);
	}
	if ((r == 0) != ok || hw.g2n != n) {
		bad++;
		printf("BAD G2L %s: ret %d entries %u, want %s %u\n", what, r, hw.g2n, ok ? "built" : "refused", n);
		return;
	}
	for (i = 0; i < n; i++) {
		const struct sw_g2w *g = &hw.g2l[i];
		if (g->space != e[i].space || g->win != e[i].win || g->off != e[i].off || g->v != e[i].v || g->vmask != e[i].vmask || g->old != e[i].old || g->omask != e[i].omask ||
		    g->once != e[i].once || g->need != e[i].need || g->w16 != e[i].w16 || g->kind != e[i].kind || g->rep != e[i].rep || g->stride != e[i].stride || g->vrng != e[i].vrng || g->arg != e[i].arg || (g->once && g->done != 0)) {     /* a window entry (once 0) is marked done by sw3_wr when it is used */
			bad++;
			printf("BAD G2L %s: entry %u space %d win %#x off %#x v %#x/%#x old %#x/%#x once %u need %d done %u w16 %u kind %u rep %u stride %#x vrng %#x arg %u, want space %d win %#x off %#x v %#x/%#x old %#x/%#x once %d need %d w16 %u kind %u rep %u stride %#x vrng %#x arg %u\n", what, i,
			    g->space, g->win, g->off, g->v, g->vmask, g->old, g->omask, g->once, g->need, g->done, g->w16, g->kind, g->rep, g->stride, g->vrng, g->arg, e[i].space, e[i].win, e[i].off, e[i].v, e[i].vmask, e[i].old, e[i].omask, e[i].once, e[i].need, e[i].w16, e[i].kind, e[i].rep, e[i].stride, e[i].vrng, e[i].arg);     /* T-1142: kind/rep/stride/vrng/arg/w16 are compared above, so they are printed */
			return;
		}
	}
}

#define REF(w, why, ...) (expwhy = (why), one((w), __VA_ARGS__, 0, 0), expwhy = NULL)

static void check(const char *what, int got, int want_)
{
	if (got != want_) {
		bad++;
		printf("BAD G2L %s: got %d want %d\n", what, got, want_);
	}
}

static uint8_t clmblob[12 * 1400 + 8];
static void setclm(const uint8_t *p, uint32_t len, int goodcrc)
{
	sw_clm.p = p;
	sw_clm.len = len;
	sw_clm.crc = p != NULL ? swf_crc32(p, len) ^ (goodcrc ? 0u : 1u) : 0u;
}

int main(void)
{
	uint32_t ra[4];
	int r;
	for (unsigned i = 0; i < sizeof clmblob; i++) {
		clmblob[i] = (uint8_t)(i * 7u + (i >> 8) + 3u);
	}
	if (swd_plan_make(SWD_RW_INDEX_SZ, SWD_MAX_SUB, SWD_MAX_CPL, &hw.g2plan) != 0) {
		printf("FAIL swd_plan_make\n");
		return 1;
	}
	hw.g2pa = G2PA;
#define RAS(a, b, c, d) (ra[0] = (a), ra[1] = (b), ra[2] = (c), ra[3] = (d), ra)
	/* built */
	one("F3 layout", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	hw.d4b = 1;     /* sweetwlan=13 (read only): the list is the 45-store list, no D4b entry */
	one("F3 layout, d4b 1 (read-only observation)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	hw.d4b = 2;     /* sweetwlan=14: the 14 extra entries */
	one("F3 layout, d4b 2 (the ring-0 post)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 2, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	hw.d4b = 3;     /* sweetwlan=16: the 14 W-D4c entries after the unmask entry, chained to the L14 doorbell */
	one("F3 layout, d4b 3 (the DS_ACK)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 3, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	check("d4b 3 list size", (int)hw.g2n, 103);
	hw.d4b = 4;     /* sweetwlan=17: + the 12 W-D4d entries of the response buffer post */
	one("F3 layout, d4b 4 (the response buffer post)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 4 list size", (int)hw.g2n, 131);
	hw.d4b = 5;     /* sweetwlan=18: + the 12 entries of the GET_VERSION request */
	one("F3 layout, d4b 5 (GET_VERSION)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 5 list size", (int)hw.g2n, 143);
	hw.d4b = 6;     /* sweetwlan=19: + the re-post (12 entries; 22b: the read-index update is a masked entry) */
	one("F3 layout, d4b 6 (consume and re-post)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 6 list size", (int)hw.g2n, 155);
	hw.d4b = 7;     /* sweetwlan=20: + the payload word, GET_VAR, the read-index update and the re-post (25 entries) */
	one("F3 layout, d4b 7 (GET_VAR cap)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 7, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	check("d4b 7 list size", (int)hw.g2n, 180);
	hw.d4b = 8;     /* sweetwlan=21: + 7 response and 7 event buffer posts (14 x 12 entries) */
	one("F3 layout, d4b 8 (L21 posts)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 8 list size", (int)hw.g2n, 348);
	hw.d4b = 9;     /* sweetwlan=22: + 11 payload words and 5 requests with their response re-posts (10 x 12) */
	one("F3 layout, d4b 9 (L22 requests)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 9 list size", (int)hw.g2n, 479);
	hw.d4b = 10;    /* sweetwlan=23: + 14 payload words, the escan request (12) and the 11 repeatable event re-post entries (T-4a7f: 10 E_ITEM words + the E_WR commit replace the 64 x 12 fixed ones) */
	one("F3 layout, d4b 10 (L23 escan)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 10, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	check("d4b 10 list size", (int)hw.g2n, 516);
	setclm(clmblob, 3000u, 1);
	one("F3 layout, d4b 10 with a CLM blob (the CLM level is 11)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 10 with a blob: list size", (int)hw.g2n, 516);
	setclm(NULL, 0u, 1);       /* the L24 cases without a CLM blob: the level-23 list + the join entries */
	hw.d4b = 11;    /* T-4a7f, sweetwlan=24 without join commands (hw.jc NULL): no L24 entry, the list of d4b 10 plus the 4 payload words of the 7-channel escan (T-0fdd: 516 -> 520 for every d4b 11 list) */
	one("F3 layout, d4b 11 without join commands", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11 list size without join commands", (int)hw.g2n, 520);
	hw.jc = g2l_jc0;       /* T-8ede: sweetwlan=24 with the six join commands: + the L24 entries (7 calls: payload words, request, re-post) before the event re-post rows */
	hw.njc = 6u;
	one("F3 layout, d4b 11 with the join commands", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 11 with the join commands, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	g2l_n24 = hw.g2n;
	hw.jc = g2l_jc1;       /* the same with a 173 B wsec_key call before the join: + its payload, call and the zeroing of the 44 words */
	hw.njc = 7u;
	one("F3 layout, d4b 11 with a key call", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	g2l_n24k = hw.g2n;
	hw.jc = g2l_jc0;       /* T-8ede: five commands (the last is "mfp", not "join"): no L24 entry, the list of d4b 10 */
	hw.njc = 5u;
	one("F3 layout, d4b 11 with five commands (last not join)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11 list size with five commands", (int)hw.g2n, 520);
	hw.jc = g2l_jc0;       /* T-23da (review T-4a7f r1 F3b): a table with no command (njc 0, jc not NULL): no L24 entry (and no read of jc[njc - 1]) */
	hw.njc = 0u;
	one("F3 layout, d4b 11 with njc 0", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11 list size with njc 0", (int)hw.g2n, 520);
	hw.jc = g2l_jc1;       /* T-23da: six commands in the bounds whose last is "wsec_key", not "join": no L24 entry (kills the join-last mutant, which every in-bounds table ending in join let survive) */
	hw.njc = 6u;
	one("F3 layout, d4b 11 with six commands ending in wsec_key", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11 list size with six commands ending in wsec_key", (int)hw.g2n, 520);
	hw.jc = &g2l_jc0[1];   /* five commands ending in "join" (the header refuses njc < 6): no L24 entry */
	hw.njc = 5u;
	one("F3 layout, d4b 11 with five commands ending in join", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11 list size with five commands ending in join", (int)hw.g2n, 520);
	{               /* nine commands ending in "join" (the header refuses njc > 8): no L24 entry */
		static struct swg_jcmd j9[9];

		memcpy(j9, g2l_jc1, sizeof g2l_jc1);
		j9[7] = g2l_jc1[5];
		j9[8] = g2l_jc1[6];
		hw.jc = j9;
		hw.njc = 9u;
		one("F3 layout, d4b 11 with nine commands ending in join", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
		check("d4b 11 list size with nine commands ending in join", (int)hw.g2n, 520);
	}
	hw.jc = g2l_jc0;
	hw.njc = 6u;           /* the join table at d4b 10 (sweetwlan=23): no L24 entry */
	hw.d4b = 10;
	one("F3 layout, d4b 10 with the join commands", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 10 list size with the join commands", (int)hw.g2n, 516);
	hw.d4b = 11;
	check("d4b 11 list size with the join commands", (int)g2l_n24, 723);      /* 520 (516 + the 4 words of the 7-channel escan, T-0fdd) + 7 calls x 24 (request + re-post) + 35 payload words */
	check("d4b 11 list size with a key call", (int)g2l_n24k, 832);     /* + the wsec_key call (24), its 44 payload words and the zeroing */
	hw.jc = NULL;
	hw.njc = 0u;
	hw.d4b = 11;    /* sweetwlan=24 (L22c): + per CLM chunk a BLOB entry, the request and its re-post, then clmload_status and country; 48 event re-posts */
	setclm(clmblob, 3000u, 1);      /* T-1142: the setclm call was lost in the T-84b0 merge, so the "3000 B CLM" cases below built the level-23 list */
	one("F3 layout, d4b 11, 3000 B CLM (3 chunks, the last 200 B)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 11, 3000 B CLM, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	setclm(clmblob, 2800u, 1);
	one("F3 layout, d4b 11, 2800 B CLM (2 full chunks)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(clmblob, 1401u, 1);
	one("F3 layout, d4b 11, 1401 B CLM (the last chunk is 1 B)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(clmblob, 1u, 1);
	one("F3 layout, d4b 11, 1 B CLM (one chunk, BEGIN and END)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(clmblob, 12u * 1400u, 1);
	one("F3 layout, d4b 11, 16800 B CLM (the 12 chunks the table has)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11, 12 chunks: list size", (int)hw.g2n, 884); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */      /* T-1142: the count of want(), entry for entry equal to the glue list by one() above (the old 1445 predated the 11 repeatable event re-posts of T-4a7f); sizes: 598 at 1 chunk, 630 at 2, 655 at 3 (+25 per chunk from the 2nd on) */
	setclm(clmblob, 12u * 1400u + 1u, 1);
	one("F3 layout, d4b 11, 16801 B CLM (more than 12 chunks: no CLM entries, the level-23 list)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11, over 12 chunks: list size", (int)hw.g2n, 520); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */
	setclm(clmblob, 3000u, 0);
	one("F3 layout, d4b 11, CLM with a wrong CRC (the level-23 list)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11, bad CRC: list size", (int)hw.g2n, 520); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */
	setclm(clmblob, 0u, 1);
	one("F3 layout, d4b 11, empty CLM (the level-23 list)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(NULL, 0u, 1);
	one("F3 layout, d4b 11, no CLM entry (the level-23 list)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(clmblob, 3000u, 1);
	one("F3 layout, d4b 11, 3000 B CLM again after the refusals", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	setclm(NULL, 0u, 1);
	hw.jc = g2l_jc0;    /* T-84b0: sweetwlan=24 = the CLM blob AND the join commands */
	hw.njc = 6u;
	setclm(clmblob, 3000u, 1);
	one("F3 layout, d4b 11, 3000 B CLM with the join commands", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	one("F3 layout, d4b 11, 3000 B CLM with the join commands, built twice (list reset)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 1);
	check("d4b 11, 3000 B CLM + join: list size", (int)hw.g2n, 862); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */      /* T-1142: 655 (3000 B CLM) + 203 (the join calls, 719 - 516) */
	hw.jc = g2l_jc1;
	hw.njc = 7u;
	one("F3 layout, d4b 11, 3000 B CLM with a key call", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11, 3000 B CLM + key call: list size", (int)hw.g2n, 1282); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */      /* T-1142: 655 + the key call (312, as without CLM: 828 - 516) + 311 entries zeroing the CLM remnant beyond the 44 key words in the 355-word shadow (shim swg_e_call_secret) */
	setclm(clmblob, 12u * 1400u, 1);
	hw.jc = g2l_jc0;
	hw.njc = 6u;
	one("F3 layout, d4b 11, 12 chunks CLM with the join commands (the list capacity)", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	check("d4b 11, 12 chunks CLM + join: list size", (int)hw.g2n, 1087); /* T-2543: +4 = the 7-channel escan payload words (T-0fdd), as the d4b 11 checks above */      /* T-1142: 880 + 203 */
	setclm(NULL, 0u, 1);
	hw.jc = NULL;
	hw.njc = 0u;
	hw.d4b = 10;
	{       /* review l21 finding 5: a G1 plan whose response / event buffer block is shorter than the 8 x 0x2000 the glue posts into refuses the list */
		const uint32_t rl = hw.g2plan.b[SWD_RESPBUF].len, el = hw.g2plan.b[SWD_EVBUF].len;

		hw.g2plan.b[SWD_RESPBUF].len = 8u * 0x2000u - 8u;
		one("L21 response buffers shorter than the table", SH, RI, RI + 0x4Cu, RA, RC, END, 0, 0);
		hw.g2plan.b[SWD_RESPBUF].len = rl;
		hw.g2plan.b[SWD_EVBUF].len = 8u * 0x2000u - 8u;
		one("L21 event buffers shorter than the table", SH, RI, RI + 0x4Cu, RA, RC, END, 0, 0);
		hw.g2plan.b[SWD_EVBUF].len = el;
		one("F3 layout, d4b 10 after the plan was restored", SH, RI, RI + 0x4Cu, RA, RC, END, 1, 0);
	}
	hw.d4b = 0;
	one("shared at its top edge, ring_mem ends at shared", 0x3FFFA0u, 0x3FFFA0u - 0x9Cu, 0x3FFFA0u - 0x50u, RA, RC, END, 1, 0);
	one("ring_info at the RAM base", SH, 0x200000u, 0x20004Cu, RA, RC, END, 1, 0);
	one("index array at the RAM end edge", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x207f40u, 0x3e7d80u, END - 16u), RC, END, 1, 0);
	one("index arrays touching shared, ring_info, ring_mem", SH, RI, RI + 0x4Cu, RAS(SH + 0x60u, SH - 16u, RI - 16u, RI + 0x9Cu), RC, END, 1, 0);
	one("index arrays touching each other", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x300010u, 0x300020u, 0x300030u), RC, END, 1, 0);
	one("index arrays touching each other, reversed", SH, RI, RI + 0x4Cu, RAS(0x300030u, 0x300020u, 0x300010u, 0x300000u), RC, END, 1, 0);
	one("RAM end right after shared", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x300010u, 0x300020u, 0x300030u), RC, SH + 0x60u, 1, 0);
	one("RC Command 0", SH, RI, RI + 0x4Cu, RA, 0u, END, 1, 0);
	check("F3 layout: no window move", (int)nwin, 0);
	/* W-D3c: shared / ring_info anywhere in RAM, either order (DHD: dhd_pcie.c:13684-13713, 13872); the hardware layouts [obs] wlan-runG2-0174 a2/a3 and the Android golden (end 0x4a0000) */
	one("F3 layout, end 0x4a0000", SH, RI, RI + 0x4Cu, RA, RC, END2, 1, 0);
	one("a2: shared 0x3b2228 ring_info 0x4852e0 (ri > sh)", 0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RA, RC, END2, 1, 0);
	check("a2: moves to the tail window and back", nwin == 2 && winlog[0] == 0x400000u && winlog[1] == 0u, 1);
	one("a3: shared 0x40a67c ring_info 0x482884", 0x40a67cu, 0x482884u, 0x482884u + 0x4Cu, RA, RC, END2, 1, 0);
	one("golden: shared 0x408c74 ring_info 0x3b029c", 0x408c74u, 0x3b029cu, 0x3b029cu + 0x4Cu, RA, RC, END2, 1, 0);
	check("golden: tail window, window 0, no restore move", nwin == 2 && winlog[0] == 0x400000u && winlog[1] == 0u, 1);
	one("golden: shared 0x408c74 ring_info 0x482884", 0x408c74u, 0x482884u, 0x482884u + 0x4Cu, RA, RC, END2, 1, 0);
	one("shared straddles 0x400000 (sh + 0x30 is the first tail word)", 0x3fffd0u, RI, RI + 0x4Cu, RA, RC, END2, 1, 0);
	check("shared straddling: tail window, window 0", nwin == 2 && winlog[0] == 0x400000u && winlog[1] == 0u, 1);
	one("ring_info block straddles 0x400000 (stores only)", SH, 0x3fff80u, 0x3fff80u + 0x4Cu, RA, RC, END2, 1, 0);
	one("ring_info reads straddle 0x400000", SH, 0x3ffff8u, 0x3ffff8u + 0x4Cu, RA, RC, END2, 1, 0);
	check("ring_info reads straddling: tail window and back", nwin == 2 && winlog[0] == 0x400000u && winlog[1] == 0u, 1);
	one("ring_info at end - 0x9C", SH, END2 - 0x9Cu, END2 - 0x50u, RA, RC, END2, 1, 0);
	one("shared at end - 0x60", END2 - 0x60u, RI, RI + 0x4Cu, RA, RC, END2, 1, 0);
	one("shared and ring_info both at the top, apart", END2 - 0x60u, END2 - 0x60u - 0x9Cu, END2 - 0x60u - 0x50u, RAS(0x300000u, 0x300010u, 0x300020u, 0x300030u), RC, END2, 1, 0);
	one("index arrays above 0x400000, apart", SH, RI, RI + 0x4Cu, RAS(0x480000u, 0x480010u, 0x49fff0u, 0x400000u), RC, END2, 1, 0);
	/* refused */
	one("shared above the window-0 top (flipped: built, W-D3c)", 0x3FFFA4u, 0x3FFFA4u - 0x9Cu, 0x3FFFA4u - 0x50u, RA, RC, END, 1, 0);
	REF("shared at end - 0x5C", "shared range", END2 - 0x5Cu, RI, RI + 0x4Cu, RA, RC, END2);
	REF("ring_info at end - 0x98", "ring_info range", SH, END2 - 0x98u, END2 - 0x4Cu, RA, RC, END2);
	REF("ring_info block overlaps shared start by 4", "ring_info/shared overlap", SH, SH + 4u - 0x9Cu, SH + 4u - 0x50u, RA, RC, END2);
	REF("ring_info block overlaps shared end by 4", "ring_info/shared overlap", SH, SH + 0x60u - 4u, SH + 0x60u - 4u + 0x4Cu, RA, RC, END2);
	one("ring_info block touches shared start", SH, SH - 0x9Cu, SH - 0x50u, RA, RC, END2, 1, 0);
	one("ring_info block touches shared end", SH, SH + 0x60u, SH + 0x60u + 0x4Cu, RA, RC, END2, 1, 0);
	REF("unaligned shared", "shared range", SH + 2u, RI, RI + 0x4Cu, RA, RC, END2);
	REF("unaligned ring_info", "ring_info range", SH, RI + 2u, RI + 2u + 0x4Cu, RA, RC, END2);
	REF("index array 1 unaligned (review r4 LOW 1)", "index array range", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x300002u, 0x3e7d80u, 0x47cf40u), RC, END2);
	REF("index array 0 below the RAM base", "index array range", SH, RI, RI + 0x4Cu, RAS(0x1FFFF0u, 0x207f40u, 0x3e7d80u, 0x47cf40u), RC, END2);
	REF("index array 3 below the RAM base", "index array range", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x207f40u, 0x3e7d80u, 0x100000u), RC, END2);
	REF("shared below the RAM base, sh + 0x30 in RAM", "shared range", 0x1FFFE0u, RI, RI + 0x4Cu, RA, RC, END2);
	REF("RAM end 0x200010 (no room for the shared structure)", "shared range", 0x200000u, RI, RI + 0x4Cu, RA, RC, 0x200010u);
	REF("RAM end 0x10 (end - 0x60 would wrap)", "shared range", 0x200000u, RI, RI + 0x4Cu, RA, RC, 0x10u);
	REF("a2 layout, ringmem_ptr wrong (refused in the tail window)", "ring_mem ptr", 0x3b2228u, 0x4852e0u, 0x4852e0u + 0x50u, RA, RC, END2);
	REF("a2 layout, index array past the end (refused in the tail window)", "index array range", 0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RAS(0x407800u, 0x207f40u, 0x3e7d80u, END2 - 12u), RC, END2);
	REF("a2 layout, RC Command all-ones (refused in the tail window)", "rc command", 0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RA, 0xFFFFFFFFu, END2);
	REF("ring_info below the RAM base", "ring_info range", SH, 0x1FFFF0u, 0x20003Cu, RA, RC, END);
	REF("ring_mem overlaps shared", "ring_info/shared overlap", 0x3FFFA0u, 0x3FFFA0u - 0x98u, 0x3FFFA0u - 0x4Cu, RA, RC, END);
	REF("ring_info at shared", "ring_info/shared overlap", SH, SH, SH + 0x4Cu, RA, RC, END);
	REF("ring_info 0xfffffff0 (wrap, review r2 F3)", "ring_info range", SH, 0xFFFFFFF0u, 0xFFFFFFF0u + 0x4Cu, RA, RC, END);
	REF("ringmem_ptr not ring_info + 0x4C", "ring_mem ptr", SH, RI, RI + 0x50u, RA, RC, END);
	REF("index array past the RAM end", "index array range", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x207f40u, 0x3e7d80u, END - 12u), RC, END);
	REF("index array 0 past the RAM end", "index array range", SH, RI, RI + 0x4Cu, RAS(END - 12u, 0x207f40u, 0x3e7d80u, 0x407800u), RC, END);
	REF("index array into shared from below", "index array overlap", SH, RI, RI + 0x4Cu, RAS(SH - 12u, 0x207f40u, 0x3e7d80u, 0x47cf40u), RC, END);
	REF("index array into shared from above", "index array overlap", SH, RI, RI + 0x4Cu, RAS(SH + 0x5Cu, 0x207f40u, 0x3e7d80u, 0x47cf40u), RC, END);
	REF("index array into ring_info from below", "index array overlap", SH, RI, RI + 0x4Cu, RAS(RI - 12u, 0x207f40u, 0x3e7d80u, 0x47cf40u), RC, END);
	REF("index array into ring_info from above", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x407800u, RI + 0x48u, 0x3e7d80u, 0x47cf40u), RC, END);
	REF("index array into ring_mem from below", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x207f40u, RI + 0x4Cu - 12u, 0x47cf40u), RC, END);
	REF("index array into ring_mem from above", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x407800u, 0x207f40u, 0x3e7d80u, RI + 0x98u), RC, END);
	REF("index arrays 1 into 0", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x30000Cu, 0x300020u, 0x300030u), RC, END);
	REF("index arrays 0 into 1", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x30001Cu, 0x300010u, 0x300030u, 0x300040u), RC, END);
	REF("index arrays 3 into 2", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x300010u, 0x300020u, 0x300028u), RC, END);
	REF("index arrays 3 onto 0", "index array overlap", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x300010u, 0x300020u, 0x300004u), RC, END);
	REF("RC Command reads all-ones", "rc command", SH, RI, RI + 0x4Cu, RA, 0xFFFFFFFFu, END);
	REF("RAM end below the shared top", "shared range", SH, RI, RI + 0x4Cu, RAS(0x300000u, 0x300010u, 0x300020u, 0x300030u), RC, SH + 0x5Fu);
	lay(SH, RI, RI + 0x4Cu, RA, RC);
	hw.dbi = NULL;
	check("no DBI page refused", sw_g2_list(&hw, SH, END, W80), -1);
	check("no DBI page: empty list", (int)hw.g2n, 0);
	lay(0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RA, RC);
	cfg[0x04 / 4] = 0x0006u;        /* the EP Command changed: the window move is refused (sw3_wr, swl_pre_ep) */
	check("window move refused: list refused", sw_g2_list(&hw, 0x3b2228u, END2, W80), -1);
	check("window move refused: empty list", (int)hw.g2n, 0);
	check("window move refused: cfg 0x84 still 0", (int)cfg[0x84 / 4], 0);
	check("window move refused: names the clause", strstr(lastmsg, "refused (window move)") != NULL, 1);
	lay(0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RA, RC);
	iso1[0] = 0x40000000u;  /* link not known up: the window move is refused */
	check("link down: list refused", sw_g2_list(&hw, 0x3b2228u, END2, W80), -1);
	check("link down: empty list", (int)hw.g2n, 0);
	check("link down: names the clause", strstr(lastmsg, "refused (window move)") != NULL, 1);
	lay(0x3b2228u, 0x4852e0u, 0x4852e0u + 0x4Cu, RA, RC);
	breaklink = 1;  /* the link goes away after the move to the tail window: the move back is refused, loudly, and the list is dropped */
	check("restore refused: list refused", sw_g2_list(&hw, 0x3b2228u, END2, W80), -1);
	breaklink = 0;
	check("restore refused: empty list", (int)hw.g2n, 0);
	check("restore refused: cfg 0x84 left in the tail window (reported)", (int)cfg[0x84 / 4], 0x400000);
	check("restore refused: names the clause", strstr(lastmsg, "refused (window restore)") != NULL, 1);
	lay(SH, RI, RI + 0x4Cu, RA, RC);
	cfg[0x84 / 4] = 0x400000u;      /* entered with the tail window (the hook expects 0): handled, left at 0 */
	dsb_hook();
	check("BAR2 window 0x400000 at entry", sw_g2_list(&hw, SH, END, W80), 0);
	check("BAR2 window 0x400000 at entry: back to 0", (int)cfg[0x84 / 4], 0);
	/* sw_g2_add: full list */
	hw.g2n = SW_G2_MAX;
	check("add to a full list", sw_g2_add(&hw, SWW_CFG, 0, 0x80u, 1u, 0xFFFFFFFFu, 0, 0, 0, -1), -1);
	check("full list unchanged", (int)hw.g2n, (int)SW_G2_MAX);
	hw.g2n = SW_G2_MAX - 1u;
	check("add the last entry", sw_g2_add(&hw, SWW_CFG, 0, 0x80u, 1u, 0xFFFFFFFFu, 0, 0, 2, -1), (int)SW_G2_MAX - 1);
	check("last entry once normalised", hw.g2l[SW_G2_MAX - 1u].once, 1);
	/* sw_g2_tcm: alignment, RAM base, RAM end, the window split */
	hw.g2n = 0;
	check("tcm unaligned", sw_g2_tcm(&hw, END, 0x200002u, 0, 0, 0, 0), -1);
	check("tcm below RAM", sw_g2_tcm(&hw, END, 0x1FFFFCu, 0, 0, 0, 0), -1);
	check("tcm at RAM end", sw_g2_tcm(&hw, END, END, 0, 0, 0, 0), -1);
	check("tcm at an odd RAM end", sw_g2_tcm(&hw, END + 2u, END, 0, 0, 0, 0), -1);
	check("tcm last word", sw_g2_tcm(&hw, END, END - 4u, 0, 0, 0, 0), 0);
	r = sw_g2_tcm(&hw, END, 0x200000u, 0, 0, 0, 0);
	check("tcm RAM base", r >= 0 && hw.g2l[r].win == 0u && hw.g2l[r].off == 0x200000u && hw.g2l[r].once == 1 && hw.g2l[r].need == -1 && hw.g2l[r].space == SWW_BAR2, 1);
	r = sw_g2_tcm(&hw, END, 0x3FFFFCu, 0, 0, 0, 0);
	check("tcm window-0 top", r >= 0 && hw.g2l[r].win == 0u && hw.g2l[r].off == 0x3FFFFCu, 1);
	r = sw_g2_tcm(&hw, END, 0x400000u, 0, 0, 0, 0);
	check("tcm tail window base", r >= 0 && hw.g2l[r].win == 0x400000u && hw.g2l[r].off == 0u, 1);
	/* sw_g2_tcm16 (sweetwlan=14): a w16 entry, low half 0, upper half free (vmask 0xFFFF, omask 0); sw_g2_add clears w16 */
	hw.g2n = 0;
	r = sw_g2_tcm16(&hw, END, 0x200000u, 0u);
	check("tcm16 entry", r >= 0 && hw.g2l[r].w16 == 1 && hw.g2l[r].vmask == 0xFFFFu && hw.g2l[r].v == 0u && hw.g2l[r].omask == 0u && hw.g2l[r].old == 0u && hw.g2l[r].once == 1 && hw.g2l[r].need == -1 && hw.g2l[r].space == SWW_BAR2, 1);
	check("tcm16 tail window", (r = sw_g2_tcm16(&hw, END, 0x400004u, 0u)) >= 0 && hw.g2l[r].win == 0x400000u && hw.g2l[r].off == 4u && hw.g2l[r].w16 == 1, 1);
	check("tcm16 value kept", (r = sw_g2_tcm16(&hw, END, 0x200010u, 0x1234u)) >= 0 && hw.g2l[r].v == 0x1234u, 1);
	check("tcm16 unaligned", sw_g2_tcm16(&hw, END, 0x200002u, 0u), -1);
	check("tcm16 at RAM end", sw_g2_tcm16(&hw, END, END, 0u), -1);
	hw.g2l[0].w16 = 1;
	hw.g2n = 0;
	r = sw_g2_add(&hw, SWW_CFG, 0, 0x80u, 1u, 0xFFFFFFFFu, 0, 0, 0, -1);
	check("add clears w16", r == 0 && hw.g2l[0].w16 == 0, 1);
	/* sw_g2_alias_ok: below 4 GiB always; above, the low 32 bits of [pa, pa + total) inside [0x822A0000, 0x86000000) */
	check("alias below 4 GiB", sw_g2_alias_ok(0x1000ull, 0x10000u), 1);
	check("alias below 4 GiB, low half outside", sw_g2_alias_ok(0xF0000000ull, 0x10000u), 1);
	check("alias at LO", sw_g2_alias_ok(0x8822A0000ull, 0x10000u), 1);
	check("alias below LO", sw_g2_alias_ok(0x88229C000ull, 0x10000u), 0);
	check("alias ends at HI", sw_g2_alias_ok(0x885FF0000ull, 0x10000u), 1);
	check("alias ends past HI", sw_g2_alias_ok(0x885FF4000ull, 0x10000u), 0);
	check("alias whole window", sw_g2_alias_ok(0x8822A0000ull, 0x86000000u - 0x822A0000u), 1);
	check("alias larger than the window", sw_g2_alias_ok(0x8822A0000ull, 0x86000000u - 0x822A0000u + 0x4000u), 0);
	check("alias total above HI (wrap)", sw_g2_alias_ok(0x8822A0000ull, 0x90000000u), 0);
	check("alias near 4 GiB (wrap)", sw_g2_alias_ok(0x8FFFF0000ull, 0x20000u), 0);
	check("alias test block", sw_g2_alias_ok(G2PA, hw.g2plan.total), 1);
	printf("%s bad %d\n", bad ? "FAIL" : "PASS", bad);
	return bad != 0;
}
"""


def self_test():
    t = G2L_HARNESS_B
    ok = len(t.split("\n")) > 100 and "\"\"\"" not in t
    print("RESULT: %s wlan_g2l_harness_b self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_g2l_harness_b is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
