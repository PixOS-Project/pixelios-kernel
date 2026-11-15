#!/usr/bin/env python3
"""wlan_glue_harness_b: part B of the C text of the glue-limit harness of tools/test_wlan_wr_mutants.py (moved out for R29 by T-4adb; GLUE_HARNESS = GLUE_HARNESS_A + GLUE_HARNESS_B, byte for byte the old text; the review-F4 block is in wlan_glue_harness_c.py since T-0a9d).

  tools/wlan_glue_harness_b --self-test    checks the part is plain C text without a triple quote
Last line: RESULT: PASS|FAIL wlan_glue_harness_b ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import sys

from wlan_glue_harness_c import GLUE_HARNESS_C

_B_TEXT = r"""			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF40u, 0, 0xABCD1234u, -1, "16-bit store to a 32-bit entry (no w16 match)" },
			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF42u, 0, 0xABCD1234u, -1, "16-bit store at a half-word offset" },
			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 0, 0xABCD1234u, 12, "16-bit store of the slot" },
			{ 1, 2, SWW_BAR2, 0, 0x400000u, 0x7CF44u, 0, 0xABCD0000u, -1, "16-bit store twice (ONCE)" },
		};
		for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) hw.g2l[i] = ge[i];
		hw.g2n = sizeof ge / sizeof ge[0];
		for (unsigned c = 0; c < sizeof w16c / sizeof w16c[0]; c++) {
			static uint32_t before[0x400000 / 4];
			g2link(w16c[c].ld);
			cfg[0x04 / 4] = 2u;
			cfg[0x10 / 4] = 0x60000004u;
			cfg[0x14 / 4] = 0u;
			cfg[0x80 / 4] = 0u;
			cfg[0x84 / 4] = 0x400000u;
			cfg[0x88 / 4] = 0x80380u;
			if (w16c[c].space == SWW_BAR2) tcm[w16c[c].off / 4] = w16c[c].old;
			memcpy(before, tcm, sizeof before);
			hw.g2 = w16c[c].g2;
			hw.d4b = w16c[c].d4b;
			hw.l3 = 10;
			hw.g2ok = 1;
			hw.g2ep = 2u;
			uint8_t d0[sizeof ge / sizeof ge[0]];
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) d0[i] = hw.g2l[i].done;
			int r = sw3_wr16(&hw, w16c[c].space, w16c[c].win, w16c[c].off, w16c[c].v);
			int ch = 0, dn = 0;
			for (size_t i = 0; i < 0x400000 / 4; i++) ch += tcm[i] != before[i] && !(r == 0 && i == w16c[c].off / 4);
			if (r == 0) ch += tcm[w16c[c].off / 4] != ((w16c[c].old & 0xFFFF0000u) | w16c[c].v);     /* only the low half changed */
			ch += cfg[0x88 / 4] != 0x80380u;
			for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) dn += hw.g2l[i].done != (d0[i] || (int)i == w16c[c].want);
			if ((r == 0) != (w16c[c].want >= 0) || ch != 0 || dn != 0) {
				if (bad++ < 20) SWG_BAD("BAD G2 %s: ret %d changed %d done-diff %d, want entry %d\n", w16c[c].what, r, ch, dn, w16c[c].want);
			}
			acc += r == 0;
		}
		hw.g2 = 0;
		hw.d4b = 0;
		g2link(0);
		/* W-D4b: a block without its VA (g2va == 0) has no host word at all */
		for (unsigned i = 0; i < sizeof ge / sizeof ge[0]; i++) hw.g2l[i] = ge[i];
		hw.g2n = sizeof ge / sizeof ge[0];
		g2link(0);
		cfg[0x04 / 4] = 2u;
		cfg[0x80 / 4] = 0u;
		cfg[0x84 / 4] = 0u;
		hw.g2 = 1;
		hw.l3 = 10;
		hw.g2ok = 1;
		hw.g2ep = 2u;
		hw.g2va = 0;
		hostmem[0x28 / 4] = 0u;
		if (sw3_wr(&hw, SW3_HOST, 0u, 0x28u, 0x0180000Du) == 0 || hostmem[0x28 / 4] != 0u || hw.g2l[7].done) {
			if (bad++ < 20) SWG_BAD("BAD G2 host word without a VA was stored or accepted\n");
		}
		hw.g2 = 0;
		hw.g2va = 0;
		hw.dbi = NULL;
	}
	/* 22b: the masked DS-round entries (SW_G2K_R_*) and the exact D4d entries that move with the rounds (SW_G2K_SH_*), restated with literals from patch 0174 sw_g2_ds / sw_g2_d4d_post. Each case runs on a fresh list: one store, accepted or refused, and
	 * a refused store changes nothing. g2sh is set per case; an accepted R_WR store raises it by one, nothing else does */
	{
		static uint32_t dsmem[0x4C000 / 4];
		static const struct { int space; int host; uint32_t off, v, old, wsl, rsl, sh; int want; const char *what; unsigned it; } dc[] = {
			/* host = 1: off is a host block offset relative to ring 0 / the slots (the *_R0 forms below), 0: BAR0 doorbell 0x140 */
			{ 1, 1, 200u, 0x06800023u, 0u, 5, 0, 0, 1, "round item 5 word 0, epoch of index 5" },
			{ 1, 1, 200u, 0x07800023u, 0u, 5, 0, 0, 0, "round item 5 word 0, wrong epoch" },
			{ 1, 1, 200u, 0x06800022u, 0u, 5, 0, 0, 0, "round item 5 word 0, wrong type" },
			{ 1, 1, 200u, 0x06800023u, 1u, 5, 0, 0, 0, "round item 5 word 0, old not 0" },
			{ 1, 1, 40u * 126u, 0x7F800023u, 0u, 126, 0, 0, 1, "round item 126 word 0 (the last item)" },
			{ 1, 1, 40u * 127u, 0x80800023u, 0u, 127, 0, 0, 0, "round item 127 word 0 (past ring 0)" },
			{ 1, 1, 5120u, 0x00800023u, 0u, 128, 0, 0, 0, "the first word of the buffer after ring 0 (item outside ring 0)" },
			{ 1, 1, 40u * 9u + 8u, 0x40u, 0u, 9, 0, 0, 1, "round item 9 mb 0x40" },
			{ 1, 1, 40u * 9u + 8u, 0x02u, 0u, 9, 0, 0, 1, "round item 9 mb 0x2" },
			{ 1, 1, 40u * 9u + 8u, 0x20u, 0u, 9, 0, 0, 1, "round item 9 mb 0x20" },
			{ 1, 1, 40u * 9u + 8u, 0x80u, 0u, 9, 0, 0, 0, "round item 9 mb 0x80" },
			{ 1, 1, 40u * 9u + 8u, 0x42u, 0u, 9, 0, 0, 0, "round item 9 mb 0x42" },
			{ 1, 1, 40u * 9u + 4u, 1u, 0u, 9, 0, 0, 0, "round item 9 word 1 not 0" },
			{ 1, 1, 40u * 9u + 8u, 0u, 0u, 9, 0, 0, 0, "round item 9 mb word 2 zero (an aligned offset that is not a multiple of the stride from the word 1 entry)" },
			{ 1, 1, 40u * 9u + 36u, 0u, 0u, 9, 0, 0, 1, "round item 9 word 9 zero" },
			{ 1, 1, 40u * 9u + 36u, 0u, 7u, 9, 0, 0, 0, "round item 9 word 9, old not 0" },
			{ 1, 1, 40u * 9u + 38u, 0u, 0u, 9, 0, 0, 0, "round item word at an odd offset" },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 1, "H2D-WR slot 9 -> 10", 2 },
			{ 2, 1, 0u, 11u, 9u, 0, 0, 0, 0, "H2D-WR slot 9 -> 11", 2 },
			{ 2, 1, 0u, 9u, 9u, 0, 0, 0, 0, "H2D-WR slot 9 -> 9", 2 },
			{ 2, 1, 0u, 128u, 127u, 0, 0, 0, 0, "H2D-WR slot 127 -> 128 (the ring-0 depth)", 2 },
			{ 2, 1, 0u, 127u, 126u, 0, 0, 0, 1, "H2D-WR slot 126 -> 127", 2 },
			{ 2, 1, 0u, 0x00010000u | 10u, 9u, 0, 0, 0, 0, "H2D-WR slot, upper half changed", 2 },
			{ 3, 0, 0x140u, 0xFF00000Au, 0u, 10, 0, 0, 1, "doorbell 0xFF00000A, the slot holds 10" },
			{ 3, 0, 0x140u, 0xFF00000Bu, 0u, 10, 0, 0, 0, "doorbell 0xFF00000B, the slot holds 10" },
			{ 3, 0, 0x140u, 0xFF000080u, 0u, 128, 0, 0, 0, "doorbell 0xFF000080, the slot holds 128" },
			{ 3, 0, 0x140u, 0xEE00000Au, 0u, 10, 0, 0, 0, "doorbell 0xEE00000A (not FF00xxxx)" },
			{ 3, 0, 0x140u, 0xFF01000Au, 0u, 10, 0, 0, 0, "doorbell 0xFF01000A (ring 1)" },
			{ 4, 1, 0u, 63u, 62u, 0, 0, 0, 1, "D2H-RD slot := 63" },
			{ 4, 1, 0u, 0u, 63u, 0, 0, 0, 1, "D2H-RD slot := 0 (wrap)" },
			{ 4, 1, 0u, 64u, 63u, 0, 0, 0, 0, "D2H-RD slot := 64 (the ring-2 depth)" },
			{ 4, 1, 0u, 0x00010003u, 2u, 0, 0, 0, 0, "D2H-RD slot, upper half changed" },
			{ 5, 0, 0x140u, 0xDD020003u, 0u, 0, 3, 0, 1, "doorbell 0xDD020003, the slot holds 3" },
			{ 5, 0, 0x140u, 0xDD020004u, 0u, 0, 3, 0, 0, "doorbell 0xDD020004, the slot holds 3" },
			{ 5, 0, 0x140u, 0xDD020040u, 0u, 0, 64, 0, 0, "doorbell 0xDD020040, the slot holds 64" },
			{ 5, 0, 0x140u, 0xAA020003u, 0u, 0, 3, 0, 0, "doorbell 0xAA020003 (not DD02xxxx)" },
			{ 5, 0, 0x140u, 0xDD030003u, 0u, 0, 3, 0, 0, "doorbell 0xDD030003 (ring 3)" },
			/* the exact D4d entries (item idx 4: word 0 0x0580000B, word 3 0, slot 4 -> 5, doorbell 0xFF000005) after g2sh DS-round items */
			{ 6, 1, 160u, 0x0580000Bu, 0u, 0, 0, 0, 1, "exact item 4 word 0, g2sh 0" },
			{ 6, 1, 280u, 0x0880000Bu, 0u, 0, 0, 3, 1, "exact item 4 word 0 moved by g2sh 3: item 7, epoch 8" },
			{ 6, 1, 160u, 0x0580000Bu, 0u, 0, 0, 3, 0, "exact item 4 word 0, g2sh 3 (not moved)" },
			{ 6, 1, 280u, 0x0580000Bu, 0u, 0, 0, 3, 0, "exact item 4 word 0 at item 7 with the old epoch" },
			{ 7, 1, 172u, 0u, 0u, 0, 0, 0, 1, "exact item 4 word 3, g2sh 0" },
			{ 7, 1, 292u, 0u, 0u, 0, 0, 3, 1, "exact item 4 word 3 moved by g2sh 3" },
			{ 7, 1, 172u, 0u, 0u, 4, 0, 3, 1, "item 4 word 3 := 0 at g2sh 3: not the exact entry, but a zero word is what the masked item entry takes at the slot's index (the slot holds 4, review F3b)" },
			{ 8, 1, 0u, 5u, 4u, 0, 0, 0, 1, "exact slot 4 -> 5, g2sh 0" },
			{ 8, 1, 0u, 8u, 7u, 0, 0, 3, 1, "exact slot moved by g2sh 3: 7 -> 8" },
			{ 2, 1, 0u, 5u, 4u, 0, 0, 3, 1, "slot 4 -> 5 at g2sh 3: not the exact entry, taken by the masked R_WR entry (g2sh 4); review F1: the complete 0x23 item the glue reads at index 4 is there (it was accepted over arbitrary words before)", 2 },
			{ 9, 0, 0x140u, 0xFF000005u, 0u, 0, 0, 0, 1, "exact doorbell 0xFF000005, g2sh 0" },
			{ 9, 0, 0x140u, 0xFF000008u, 0u, 0, 0, 3, 1, "exact doorbell moved by g2sh 3: 0xFF000008" },
			{ 9, 0, 0x140u, 0xFF000005u, 0u, 0, 0, 3, 0, "exact doorbell 0xFF000005, g2sh 3 (not moved)" },
			/* review 0174-w-d4e-scan24-hwwrite-r1 F1: the slot store only over a complete DS-round item at the old slot (the ten host words are read through sw3_ptr); `it` = what the item at index old holds: 9 / 10 complete with word 9 / word 1 nonzero, 1 zero, 2 complete 0x23 mb 0x2, 3 half-written 0x0B response post, 4 complete + stale word 4, 5 complete with the epoch of the next index, 6 complete with mb 0x80, 7 mb 0x40, 8 mb 0x20 */
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over a zero item", 1 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 1, "F1 slot 9 -> 10 over a complete 0x23 item", 2 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over a half-written 0x0B item", 3 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over a complete item with a stale word 4", 4 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over an item with the epoch of index 10", 5 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over an item with mb 0x80", 6 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 1, "F1 slot 9 -> 10 over an item with mb 0x40", 7 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 1, "F1 slot 9 -> 10 over an item with mb 0x20", 8 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over a complete item with a stale word 9", 9 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 0, 0, "F1 slot 9 -> 10 over a complete item with a stale word 1 (request id)", 10 },
			/* F3a: the glue's own cap, 3 items per DS round x SW_G2_DS_MAX rounds */
			{ 2, 1, 0u, 10u, 9u, 0, 0, 11, 1, "F3a slot 9 -> 10 at g2sh 11 (the last item of the cap)", 2 },
			{ 2, 1, 0u, 10u, 9u, 0, 0, 12, 0, "F3a slot 9 -> 10 at g2sh 12 (the cap is used up)", 2 },
			/* F3b: a round item word only at the index the next commit names (the slot) */
			{ 1, 1, 200u, 0x06800023u, 0u, 4, 0, 0, 0, "F3b item 5 word 0 while the slot holds 4" },
			{ 1, 1, 200u, 0x06800023u, 0u, 6, 0, 0, 0, "F3b item 5 word 0 while the slot holds 6" },
			{ 1, 1, 40u * 9u + 8u, 0x02u, 0u, 8, 0, 0, 0, "F3b item 9 mb 0x2 while the slot holds 8" },
			/* gate d4d17g survivor "item stride not checked": an item word at an offset that is no multiple of the 40 B stride from its word base (word 0 entry, off 20, index 0 = the slot) */
			{ 1, 1, 20u, 0x01800023u, 0u, 0, 0, 0, 0, "item word 0 value at ring0 + 20 (not on the 40 B stride)" },
			{ 1, 1, 44u, 0x02800023u, 0u, 1, 0, 0, 0, "item word 0 value at ring0 + 44 (stride 44 from the base)" },
			/* F3c: the exact (moved) slot entry stays inside ring 0 */
			{ 8, 1, 0u, 127u, 126u, 0, 0, 122, 1, "F3c exact slot moved by g2sh 122: 126 -> 127" },
			{ 8, 1, 0u, 128u, 127u, 0, 0, 123, 0, "F3c exact slot moved by g2sh 123: 127 -> 128 (the ring-0 depth)" },
		};
		const uint32_t r0 = hw.g2plan.b[SWD_H2D_CTRL].off, ws = hw.g2plan.b[SWD_IDX_H2D_W].off, rs = hw.g2plan.b[SWD_IDX_D2H_R].off;

		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		for (unsigned c = 0; c < sizeof dc / sizeof dc[0]; c++) {
			const uint32_t off = dc[c].host ? (dc[c].space == 2 || dc[c].space == 8 ? ws : dc[c].space == 4 ? rs : r0 + dc[c].off) : dc[c].off;
			const uint32_t k = dc[c].space;      /* 1 item word, 2 WR slot, 3 WDB, 4 RD slot, 5 RDB, 6..9 exact item0 / item / slot / doorbell */
			uint32_t g2sh0 = dc[c].sh;
			struct sw_g2w *e;

			hw.g2n = 0;
#define DSADD(sp, o, vl, vm, ol, om, on, kd, rp, st, vr) do { e = &hw.g2l[hw.g2n++]; memset(e, 0, sizeof *e); e->space = (sp); e->need = -1; e->win = (sp) == SWW_BAR0 ? SWW_WIN_PCIE : 0u; e->off = (o); e->v = (vl); e->vmask = (vm); e->old = (ol); e->omask = (om); e->once = (on); e->kind = (kd); e->rep = (rp); e->stride = (st); e->vrng = (vr); } while (0)
			DSADD(SW3_HOST, r0 + 160u, 0x0580000Bu, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, SW_G2K_SH_ITEM0, 0, 0, 0);
			DSADD(SW3_HOST, r0 + 172u, 0u, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 1, SW_G2K_SH_ITEM, 0, 0, 0);
			DSADD(SW3_HOST, ws, 5u, 0xFFFFu, 4u, 0xFFFFFFFFu, 1, SW_G2K_SH_WR, 0, 0, 0);
			DSADD(SWW_BAR0, 0x140u, 0xFF000005u, 0xFFFFFFFFu, 0u, 0u, 1, SW_G2K_SH_DB, 0, 0, 0);
			for (unsigned w = 0; w < 10u; w++) {
				static const uint32_t mbv[3] = { 0x2u, 0x40u, 0x20u };

				for (unsigned m = 0; m < (w == 2u ? 3u : 1u); m++) {
					DSADD(SW3_HOST, r0 + 4u * w, w == 0u ? 0x00800023u : w == 2u ? mbv[m] : 0u, 0xFFFFFFFFu, 0u, 0xFFFFFFFFu, 0, w == 0u ? SW_G2K_R_ITEM0 : SW_G2K_R_ITEM, 127u, 40u, 0);
				}
			}
			DSADD(SW3_HOST, ws, 0u, 0xFFFFu, 0u, 0u, 0, SW_G2K_R_WR, 0, 0, 0);
			DSADD(SWW_BAR0, 0x140u, 0xFF000000u, 0xFFFFFFFFu, 0u, 0u, 0, SW_G2K_R_WDB, 0, 0, 0);
			DSADD(SW3_HOST, rs, 0u, 0xFFFFu, 0u, 0u, 0, SW_G2K_R_RD, 0, 0, 63u);
			DSADD(SWW_BAR0, 0x140u, 0xDD020000u, 0xFFFFFFFFu, 0u, 0u, 0, SW_G2K_R_RDB, 0, 0, 0);
#undef DSADD
			for (size_t i = 0; i < sizeof dsmem / 4; i++) dsmem[i] = 0x30000000u + (uint32_t)i;
			for (int a = 0; a < 6; a++)
				for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
			g2link(0);
			g2irq(-1);
			cfg[0x04 / 4] = 2u;
			cfg[0x10 / 4] = 0x60000004u;
			cfg[0x14 / 4] = 0u;
			cfg[0x80 / 4] = SWW_WIN_PCIE;
			cfg[0x84 / 4] = 0u;
			hw.g2va = (vm_offset_t)dsmem;
			hw.g2 = 1;
			hw.l3 = 10;
			hw.g2ok = 1;
			hw.g2ep = 2u;
			hw.rel = 7u;
			hw.g2sh = g2sh0;
			if (dc[c].host) {
				dsmem[off / 4] = dc[c].old;
			}
			dsmem[ws / 4] = dc[c].space == 3 ? dc[c].wsl : dc[c].space == 2 || dc[c].space == 8 ? dc[c].old : dc[c].space == 1 || (dc[c].space == 7 && dc[c].wsl != 0) ? dc[c].wsl : dsmem[ws / 4];
			if (dc[c].space == 2 && dc[c].it != 0) {      /* F1: the ten words of the item at the old slot */
				const uint32_t j = dc[c].old & 0xFFFFu;
				uint32_t *iw = &dsmem[(r0 + 40u * j) / 4u];

				memset(iw, 0, 40);
				if (dc[c].it >= 2u) {
					iw[0] = 0x00800023u | SW_G2_D4D_EP(dc[c].it == 5u ? j + 1u : j);
					iw[2] = dc[c].it == 6u ? 0x80u : dc[c].it == 7u ? 0x40u : dc[c].it == 8u ? 0x20u : 0x2u;
				}
				if (dc[c].it == 3u) {
					iw[0] = 0x0080000Bu | SW_G2_D4D_EP(j);
					iw[1] = 2u;
					iw[2] = 0x2000u;
				}
				if (dc[c].it == 4u) {
					iw[4] = 0x84ebd000u;
				}
				if (dc[c].it == 9u) {
					iw[9] = 1u;
				}
				if (dc[c].it == 10u) {
					iw[1] = 1u;
				}
			}
			dsmem[rs / 4] = dc[c].space == 5 ? dc[c].rsl : dc[c].space == 4 ? dc[c].old : dsmem[rs / 4];
			if (dc[c].space == 3) dsmem[ws / 4] = dc[c].wsl;
			if (dc[c].space == 5) dsmem[rs / 4] = dc[c].rsl;
			int r = dc[c].host ? sw3_wr(&hw, SW3_HOST, 0u, off, dc[c].v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, off, dc[c].v);
			int want = dc[c].want;

			if ((r == 0) != want) {
				if (bad++ < 20) SWG_BAD("BAD DS case %u (%s): ret %d, want accept %d\n", c, dc[c].what, r, want);
			} else if (r == 0 && (dc[c].host ? dsmem[off / 4] != dc[c].v : mem[off / 4] != dc[c].v)) {
				if (bad++ < 20) SWG_BAD("BAD DS case %u (%s): accepted but the word was not stored\n", c, dc[c].what);
			} else if (r != 0 && (dc[c].host ? dsmem[off / 4] != (dc[c].space == 2 || dc[c].space == 4 || (dc[c].space == 1 || dc[c].space >= 6) ? dc[c].old : 0x30000000u + off / 4u) : mem[off / 4] != 0x10000000u * 5u + off / 4u)) {
				if (bad++ < 20) SWG_BAD("BAD DS case %u (%s): refused but the word changed\n", c, dc[c].what);
			}
			if (hw.g2sh != g2sh0 + (r == 0 && k == 2u ? 1u : 0u)) {
				if (bad++ < 20) SWG_BAD("BAD DS case %u (%s): g2sh %u, want %u\n", c, dc[c].what, hw.g2sh, g2sh0 + (r == 0 && k == 2u ? 1u : 0u));
			}
			acc += r == 0;
		}
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2sh = 0;
		hw.g2ev = 0;
		hw.g2ev0 = 0;
		hw.g2rd = 0;
		hw.g2n = 0;
	}
	/* W-D4e: the escan version word (kind SW_G2K_VER): the table value with bit 16 free (scan_ver 2 or 3), every other bit exact, the old word exact, once. Each case runs on a fresh one-entry list. */
	{
		static uint32_t vmem[0x4C000 / 4];
		static const struct { uint32_t v, old; int want; const char *what; } vc[] = {
			{ 0x0002006Eu, 0x736D5F74u, 1, "VER word, version 2" },
			{ 0x0003006Eu, 0x736D5F74u, 1, "VER word, version 3 (bit 16 set)" },
			{ 0x0006006Eu, 0x736D5F74u, 0, "VER word, bit 18 differs" },
			{ 0x0000006Eu, 0x736D5F74u, 0, "VER word, bit 17 cleared" },
			{ 0x0002006Fu, 0x736D5F74u, 0, "VER word, bit 0 differs" },
			{ 0x0002006Eu, 0x736D5F75u, 0, "VER word, old word differs" },
			{ 0x0003006Eu, 0x736D5F74u, 2, "VER word stored twice (once)" },
		};
		const uint32_t voff = 0x180u;

		for (unsigned c = 0; c < sizeof vc / sizeof vc[0]; c++) {
			struct sw_g2w *e;
			int r, r2 = -1;

			hw.g2n = 0;
			e = &hw.g2l[hw.g2n++];
			memset(e, 0, sizeof *e);
			e->space = SW3_HOST; e->need = -1; e->off = voff; e->v = 0x0002006Eu; e->vmask = 0xFFFFFFFFu; e->old = 0x736D5F74u; e->omask = 0xFFFFFFFFu; e->once = 1; e->kind = SW_G2K_VER;
			for (size_t i = 0; i < sizeof vmem / 4; i++) vmem[i] = 0x30000000u + (uint32_t)i;
			for (int a = 0; a < 6; a++)
				for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
			g2link(0);
			g2irq(-1);
			cfg[0x04 / 4] = 2u;
			cfg[0x10 / 4] = 0x60000004u;
			cfg[0x14 / 4] = 0u;
			cfg[0x80 / 4] = SWW_WIN_PCIE;
			cfg[0x84 / 4] = 0u;
			hw.g2va = (vm_offset_t)vmem;
			hw.g2 = 1;
			hw.l3 = 10;
			hw.g2ok = 1;
			hw.g2ep = 2u;
			hw.rel = 7u;
			vmem[voff / 4] = vc[c].old;
			r = sw3_wr(&hw, SW3_HOST, 0u, voff, vc[c].v);
			if (vc[c].want == 2) {
				r2 = sw3_wr(&hw, SW3_HOST, 0u, voff, vc[c].v);
			}
			if ((r == 0) != (vc[c].want != 0) || (vc[c].want == 2 && r2 == 0) || (r == 0 ? vmem[voff / 4] != vc[c].v : vmem[voff / 4] != vc[c].old) || hw.g2sh != 0u) {
				if (bad++ < 20) printf("BAD VER case %u (%s): ret %d / %d, word %#x\n", c, vc[c].what, r, r2, vmem[voff / 4]);
			}
			acc += r == 0;
		}
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2n = 0;
	}
	/* T-af77: the L22 response re-post's buffer address (kind SW_G2K_SH_RBUF): the table value (slot 0) plus 0..7 whole strides of 0x2000, the old word exact 0, once, moved by g2sh like an item word. Each case runs on a fresh one-entry list. */
	{
		static uint32_t bmem[0x4C000 / 4];
		const uint32_t base = 0x84DF0000u, boff = 0x180u;
		static const struct { uint32_t v, old, sh; int want; const char *what; } bc[] = {
			{ 0x84DF0000u, 0u, 0u, 1, "RBUF slot 0" },
			{ 0x84DF2000u, 0u, 0u, 1, "RBUF slot 1" },
			{ 0x84DFE000u, 0u, 0u, 1, "RBUF slot 7" },
			{ 0x84E00000u, 0u, 0u, 0, "RBUF slot 8 (outside)" },
			{ 0x84DF1000u, 0u, 0u, 0, "RBUF misaligned (+4 KiB)" },
			{ 0x84DF0004u, 0u, 0u, 0, "RBUF misaligned (+4 B)" },
			{ 0x84DEE000u, 0u, 0u, 0, "RBUF below slot 0" },
			{ 0x84DF2000u, 1u, 0u, 0, "RBUF old word not 0" },
			{ 0x84DF2000u, 0u, 0u, 2, "RBUF stored twice (once)" },
			{ 0x84DF2000u, 0u, 1u, 3, "RBUF at the unshifted offset after a DS round (g2sh 1)" },
		};

		for (unsigned c = 0; c < sizeof bc / sizeof bc[0]; c++) {
			struct sw_g2w *e;
			int r, r2 = -1;

			hw.g2n = 0;
			e = &hw.g2l[hw.g2n++];
			memset(e, 0, sizeof *e);
			e->space = SW3_HOST; e->need = -1; e->off = boff; e->v = base; e->vmask = 0xFFFFFFFFu; e->old = 0u; e->omask = 0xFFFFFFFFu; e->once = 1; e->kind = SW_G2K_SH_RBUF; e->stride = 0x2000u; e->vrng = 7u * 0x2000u;
			for (size_t i = 0; i < sizeof bmem / 4; i++) bmem[i] = 0x30000000u + (uint32_t)i;
			for (int a = 0; a < 6; a++)
				for (size_t i = 0; i < (a == 5 ? 0x400000 / 4 : len[a]); i++) arr[a][i] = 0x10000000u * (a + 1) + (uint32_t)i;
			g2link(0);
			g2irq(-1);
			cfg[0x04 / 4] = 2u;
			cfg[0x10 / 4] = 0x60000004u;
			cfg[0x14 / 4] = 0u;
			cfg[0x80 / 4] = SWW_WIN_PCIE;
			cfg[0x84 / 4] = 0u;
			hw.g2va = (vm_offset_t)bmem;
			hw.g2 = 1;
			hw.l3 = 10;
			hw.g2ok = 1;
			hw.g2ep = 2u;
			hw.rel = 7u;
			hw.g2sh = bc[c].sh;
			bmem[(boff + 40u * bc[c].sh) / 4] = bc[c].old;
			r = sw3_wr(&hw, SW3_HOST, 0u, boff, bc[c].v);       /* case 3: the entry sits at boff + 40, boff itself is no entry */
			if (bc[c].want == 2) {
				r2 = sw3_wr(&hw, SW3_HOST, 0u, boff, bc[c].v);
			}
			if (bc[c].want == 3) {
				r2 = sw3_wr(&hw, SW3_HOST, 0u, boff + 40u, bc[c].v);
			}
			if (bc[c].want == 3 ? (r == 0 || r2 != 0) : ((r == 0) != (bc[c].want != 0) || (bc[c].want == 2 && r2 == 0))) {
				if (bad++ < 20) printf("BAD RBUF case %u (%s): ret %d / %d, word %#x\n", c, bc[c].what, r, r2, bmem[boff / 4]);
			}
			acc += r == 0;
		}
		hw.g2sh = 0;
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2n = 0;
	}
	/* review F2: with the L16 C2 doorbell entry NOT done, every store of a masked DS kind is refused (the 16 entries are chained after it) */
	{
		static uint32_t rmem[0x4C000 / 4];
		const uint32_t r0 = hw.g2plan.b[SWD_H2D_CTRL].off, ws = hw.g2plan.b[SWD_IDX_H2D_W].off, rs = hw.g2plan.b[SWD_IDX_D2H_R].off;
		const struct { int host; uint32_t off, v, rdslot; const char *what; } f2[] = {
			{ 1, r0 + 160u, 0x05800023u, 2u, "R_ITEM0: item word 0 at the slot's index" },
			{ 1, r0 + 168u, 0x2u, 2u, "R_ITEM: item mb word at the slot's index" },
			{ 1, r0 + 172u, 0u, 2u, "R_ITEM: item zero word at the slot's index" },
			{ 1, ws, 5u, 2u, "R_WR: H2D-WR slot 4 -> 5 over a complete item" },
			{ 0, 0x140u, 0xFF000004u, 2u, "R_WDB: doorbell 0xFF000004 with the slot at 4" },
			{ 1, rs, 3u, 2u, "R_RD: D2H-RD slot 2 -> 3" },
			{ 0, 0x140u, 0xDD020003u, 3u, "R_RDB: doorbell 0xDD020003 with the slot at 3" },
		};

		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		for (unsigned c = 0; c < sizeof f2 / sizeof f2[0]; c++) {
			int r, c2 = real_list(&hw, 0);

			if (c2 < 0) {
				if (bad++ < 20) SWG_BAD("BAD F2: the real list could not be built\n");
				break;
			}
			real_env(&hw, rmem, sizeof rmem);
			rmem[r0 / 4u + 40u] = 0x05800023u;       /* item 4 complete: only the chain can refuse the slot store */
			rmem[r0 / 4u + 42u] = 0x2u;
			if (c < 3u) rmem[r0 / 4u + 40u] = rmem[r0 / 4u + 42u] = 0u;
			rmem[rs / 4u] = f2[c].rdslot;
			r = f2[c].host ? sw3_wr(&hw, SW3_HOST, 0u, f2[c].off, f2[c].v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, f2[c].off, f2[c].v);
			if (r == 0 && bad++ < 20) SWG_BAD("BAD F2: %s accepted before the L16 C2 doorbell entry was done\n", f2[c].what);
			acc += r == 0;
		}
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2n = 0;
	}
@GLUE_HARNESS_C@
	/* L22c (T-4adb): the BLOB entry of every clmload chunk probed directly, on blobs the host test's clm stream does not have: 1401 B (the last chunk is 1 B, its payload 21 B = 6 words with a partial word) and 2803 B (last chunk 3 B, 23 B); each
	 * word of each chunk is stored through sw3_wr in order with the host memory as the model says (accepted iff it differs from the old word), then the negatives: a wrong word, a misaligned offset, one word past the chunk, rep above the shadow, no blob, the
	 * request doorbell stored. The old words the list builder put in the payload entries after the chunks (the shadow) are checked against the model */
	{
		static uint32_t rmem[0x4C000 / 4];
		static uint8_t cb[2820];
		static const uint32_t cl[2] = { 1401u, 2803u };
		uint32_t io;
		unsigned bi, t;

		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		hw.g2pa = rs_pa;
		io = hw.g2plan.b[SWD_IOCTBUF].off;
		for (bi = 0; bi < sizeof cb; bi++) {
			cb[bi] = (uint8_t)(bi * 13u + 5u + (bi >> 7));
		}
		for (t = 0; t < 2u; t++) {
			const uint32_t L = cl[t], nch = (L + 1399u) / 1400u;
			uint32_t mb[355], c, i, k, b, nb = 0, bl[12];
			unsigned nbad = 0;

			sw_clm.p = cb;
			sw_clm.len = L;
			sw_clm.crc = swf_crc32(cb, L);
			if (real_list_lv(&hw, 1, 11) < 0) {
				if (bad++ < 20) SWG_BAD("BAD L22c: the real list could not be built (blob %u B)\n", L);
				continue;
			}
			for (k = 0; k < hw.g2n && nb < 12u; k++) {
				if (hw.g2l[k].kind == SW_G2K_BLOB) bl[nb++] = k;
			}
			if (nb != nch) {
				if (bad++ < 20) SWG_BAD("BAD L22c: %u BLOB entries, want %u (blob %u B)\n", nb, nch, L);
				continue;
			}
			for (i = 0; i < 355u; i++) mb[i] = hw.g2clmpre[i];
			for (c = 0; c < nch; c++) {
				uint32_t w, o0 = 0, nw = mc_words(c, L);
				int r;

				b = bl[c];
				if (hw.g2l[b].arg != c || hw.g2l[b].rep != nw) {
					if (bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: entry arg %u rep %u, want %u words\n", L, c, hw.g2l[b].arg, hw.g2l[b].rep, nw);
					continue;
				}
#define L22C_STATE(dbell) do { for (k = 0; k < hw.g2n; k++) hw.g2l[k].done = k < b || (k > b && k <= b + 11u) ? 1 : 0; hw.g2l[b + 12u].done = (dbell); } while (0)
				real_env(&hw, rmem, sizeof rmem);
				L22C_STATE(0);
				for (i = 0; i < nw; i++) {
					w = mc_word(c, i, L);
					rmem[(io + 4u * i) / 4u] = mb[i];
					r = sw3_wr(&hw, SW3_HOST, 0u, io + 4u * i, w);
					if ((r == 0) != (w != mb[i]) || (r == 0 && rmem[(io + 4u * i) / 4u] != w)) {
						if (bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u word %u: store %#x over %#x ret %d, want %s\n", L, c, i, w, mb[i], r, w != mb[i] ? "accepted" : "refused (equal word)");
						nbad++;
					}
					acc += r == 0;
					if (o0 == 0u && i >= 3u && w != mb[i]) o0 = i;
				}
				if (nbad == 0u && o0 != 0u) {
					const uint32_t wv = mc_word(c, o0, L);

					rmem[(io + 4u * o0) / 4u] = mb[o0];
					if (sw3_wr(&hw, SW3_HOST, 0u, io + 4u * o0, wv ^ 1u) == 0 && bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: a wrong word accepted\n", L, c);
					memcpy((char *)rmem + io + 4u * o0 + 2u, &mb[o0], 4);      /* the old word at the misaligned address is the model's, so only the alignment check refuses it */
					if (sw3_wr(&hw, SW3_HOST, 0u, io + 4u * o0 + 2u, wv) == 0 && bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: a misaligned offset accepted\n", L, c);
					if (nw < 355u && mb[nw] != 0u) {        /* the word after the chunk: the model's old word is the memory, the expected word is 0 (beyond the data), so only the rep bound refuses it */
						rmem[(io + 4u * nw) / 4u] = mb[nw];
						if (sw3_wr(&hw, SW3_HOST, 0u, io + 4u * nw, mc_word(c, nw, L)) == 0 && bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: one word past the chunk accepted\n", L, c);
					}
					{
						const uint8_t *keep = hw.g2clm;

						hw.g2clm = NULL;
						rmem[(io + 4u * o0) / 4u] = mb[o0];
						if (sw3_wr(&hw, SW3_HOST, 0u, io + 4u * o0, wv) == 0 && bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: a store accepted with no blob\n", L, c);
						hw.g2clm = keep;
					}
					L22C_STATE(1);
					rmem[(io + 4u * o0) / 4u] = mb[o0];
					if (sw3_wr(&hw, SW3_HOST, 0u, io + 4u * o0, wv) == 0 && bad++ < 20) SWG_BAD("BAD L22c blob %u chunk %u: a store accepted after the request doorbell was stored\n", L, c);
					L22C_STATE(0);
				}
#undef L22C_STATE
				for (i = 0; i < nw; i++) mb[i] = mc_word(c, i, L);
			}
			{       /* the payload entries after the last chunk: their old words are the shadow, i.e. mb[] (model) updated by the entries before them */
				unsigned seen = 0;

				for (k = bl[nch - 1u] + 13u; k < hw.g2n; k++) {
					const struct sw_g2w *e = &hw.g2l[k];

					if (e->kind != 0 || e->space != SW3_HOST || e->off < io || e->off >= io + 4u * 355u) continue;
					i = (e->off - io) / 4u;
					if (e->old != mb[i]) {
						if (bad++ < 20) SWG_BAD("BAD L22c blob %u: payload entry %u (word %u) old %#x, the shadow after the chunks has %#x\n", L, k, i, e->old, mb[i]);
						break;
					}
					mb[i] = e->v;
					seen++;
				}
				if (seen < 8u && bad++ < 20) SWG_BAD("BAD L22c blob %u: only %u payload entries after the last chunk\n", L, seen);
			}
		}
		sw_clm.p = NULL;
		sw_clm.len = 0;
		sw_clm.crc = 0;
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2sh = 0;
		hw.g2n = 0;
	}
	printf("%s accepted %d bad %d\n", bad ? "FAIL" : "PASS", acc, bad);
	printf("L24 streams %u negatives %u\n", g_l24_nj, g_l24_neg);
	printf("REPLAY24 streams %u negatives %u\n", nrsets, nneg24);      /* the L21-L23 store streams replayed through the glue list and the refused negatives (review l21 finding 2) */
	return bad != 0;
}
"""
GLUE_HARNESS_B = _B_TEXT.replace("@GLUE_HARNESS_C@", GLUE_HARNESS_C)      # T-0a9d (R29): the review-F4 block lives in tools/wlan_glue_harness_c.py


def self_test():
    t = GLUE_HARNESS_B
    ok = len(t.split("\n")) > 100 and "\"\"\"" not in t and ("b" == "a") == t.startswith("\n#include <stdint.h>")
    print("RESULT: %s wlan_glue_harness_b self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_glue_harness_b is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
