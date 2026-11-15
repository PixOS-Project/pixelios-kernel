#!/usr/bin/env python3
"""wlan_glue_harness_c: part C of the C text of the glue-limit harness of tools/test_wlan_wr_mutants.py (the review-F4 block of part B, moved out for R29 by T-0a9d; tools/wlan_glue_harness_b.py puts it back in place of its @GLUE_HARNESS_C@ line, so GLUE_HARNESS stays byte for byte the same text).

  tools/wlan_glue_harness_c --self-test    checks the part is plain C text without a triple quote
Last line: RESULT: PASS|FAIL wlan_glue_harness_c ..."""
import sys

GLUE_HARNESS_C = r"""	/* T-98d9 (T-43af L25 wiring, review T-5173 r1-r2): the judge-alone route of sw3_g2_ok, run through the REAL list of every replay stream. With the engine at level >= 25 (hw.g2x->elv, hw.l25arm) every HOST / DATA / PCIe doorbell-0 store is judged by
	 * sweet_wlan_l25_wl.h and by nothing else: here sw_g2_rd is a stub that fails, so the judge refuses EVERY such store, even one that the wide g2l[] entry of the same stream accepts (no fall-through, nothing written). Latched on the first live call: the
	 * engine level falling back to 24 does not send a store back to g2l[]. Controls: level 24, or not armed, or no engine state: the same store is accepted and l25on stays 0. The stop path (PCIe 0xC34 := 0) stays on g2l[] and is accepted while the judge is live.
	 * A 16-bit store can only be a BAR2 TCM store (sw3_wr16), never HOST/DATA/doorbell, so the `!w16` clause of the route cannot be reached from a host store: named in docs/reviews/T-98d9-proof.md. The judge's own accept side is covered by wlan_l25_wl_host_test.c and the G2 host test. */
	{
		static uint32_t lmem[0x4C000 / 4], lsnap[0x4C000 / 4];
		struct swg_wx gx;
		unsigned t, nl25 = 0, nctl = 0, nstop = 0;
		static struct sw_hw hsave;
		const uint8_t *clm_p = sw_clm.p;       /* the CLM blob this block notes for the d4b >= 11 streams and the whole glue state are put back at its end: the F4 block below must see the state it had before */
		const uint32_t clm_len = sw_clm.len, clm_crc = sw_clm.crc;

		hsave = hw;
		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		hw.g2pa = rs_pa;
		memset(&gx, 0, sizeof gx);
		for (t = 0; t < nrsets; t++) {
			const uint32_t ws = hw.g2plan.b[SWD_IDX_H2D_W].off;
			unsigned fh = rsets[t].n, fd = rsets[t].n, fs = rsets[t].n, j;

			for (unsigned i = 0; i < rsets[t].n; i++) {
				if (rsets[t].s[i].host && fh == rsets[t].n) fh = i;
				if (!rsets[t].s[i].host && fd == rsets[t].n) fd = i;
				if (rsets[t].s[i].host && rsets[t].s[i].a == ws && fs == rsets[t].n) fs = i;
			}
			if (rsets[t].d4b >= 11) {
				static uint8_t lblob[15932];
				unsigned bi;

				for (bi = 0; bi < sizeof lblob; bi++) lblob[bi] = (uint8_t)(bi * 7u + 3u + (bi >> 8));
				sw_clm.p = lblob;
				sw_clm.len = sizeof lblob;
				sw_clm.crc = swf_crc32(lblob, sizeof lblob);
			}
			hw.jc = rsets[t].jc;
			hw.njc = rsets[t].njc;
			for (j = 0; j < 17u && rsets[t].n > 0u; j++) {
				const unsigned step = rsets[t].n > 12u ? rsets[t].n / 12u : 1u;
				const unsigned k = j < 12u ? (j * step < rsets[t].n ? j * step : rsets[t].n - 1u) : j == 12u ? fh : j == 13u ? fd : j == 14u ? fs : j == 15u ? rsets[t].n - 1u : 0u;
				const struct rs *q;
				unsigned i;
				int r = 0;

				if (k >= rsets[t].n) continue;      /* no host store / no doorbell / no slot store in this stream */
				q = &rsets[t].s[k];
				for (int ctl = 0; ctl < 4; ctl++) {      /* 0 = the route (level 25, armed), 1 = level 24, 2 = not armed, 3 = no engine state */
					if (ctl > 0 && j != 0u && j != 15u) continue;
					hw.g2x = ctl == 3 ? NULL : &gx;
					hw.l25arm = 0;
					hw.l25on = 0;
					gx.elv = ctl == 1 ? 24u : 25u;
					if (real_list_lv(&hw, 1, rsets[t].d4b) < 0) { if (bad++ < 20) SWG_BAD("BAD L25 route %s: the real list could not be built\n", rsets[t].label); break; }
					real_env(&hw, lmem, sizeof lmem);
					hw.g2x = NULL;
					for (i = 0; i < k; i++) {      /* the stream up to store k, before the engine reaches level 25 */
						const struct rs *p = &rsets[t].s[i];

						if ((p->host ? sw3_wr(&hw, SW3_HOST, 0u, p->a, p->v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, p->a, p->v)) != 0) break;
					}
					if (i < k) { if (bad++ < 20) SWG_BAD("BAD L25 route %s: store %u of the prefix refused with no engine state\n", rsets[t].label, i); continue; }
					hw.g2x = ctl == 3 ? NULL : &gx;
					hw.l25arm = ctl == 2 ? 0 : 1;
					for (int a = 0; a < 6; a++) memcpy(snap[a], arr[a], len[a] * 4);
					memcpy(lsnap, lmem, sizeof lmem);
					r = q->host ? sw3_wr(&hw, SW3_HOST, 0u, q->a, q->v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, q->a, q->v);
					if (ctl == 0) {
						int ch = memcmp(lsnap, lmem, sizeof lmem) != 0;

						for (int a = 0; a < 6; a++) ch += memcmp(snap[a], arr[a], len[a] * 4) != 0;
						if (r == 0 || ch != 0 || hw.l25on != 1) { if (bad++ < 20) SWG_BAD("BAD L25 route %s store %u (%s %#x): ret %d changed %d l25on %d, want refused, nothing written, latched\n", rsets[t].label, k, q->host ? "host" : "pcie", q->a, r, ch, hw.l25on); }
						gx.elv = 24u;      /* the engine state falls back: the latch keeps the judge */
						if (sw3_wr(&hw, q->host ? SW3_HOST : SWW_BAR0, q->host ? 0u : SWW_WIN_PCIE, q->a, q->v) == 0 && bad++ < 20) SWG_BAD("BAD L25 route %s store %u: accepted after the level fell back to 24 (latch lost)\n", rsets[t].label, k);
						nl25++;
					} else {
						if (r != 0 || hw.l25on != 0) { if (bad++ < 20) SWG_BAD("BAD L25 route control %d %s store %u (%s %#x): ret %d l25on %d, want accepted and no route\n", ctl, rsets[t].label, k, q->host ? "host" : "pcie", q->a, r, hw.l25on); }
						nctl++;
					}
				}
			}
			/* the whole stream done, then L25 goes live: the stop path (PCIe 0xC34 := 0) is not judged and is accepted */
			if (rsets[t].n > 0u && real_list_lv(&hw, 1, rsets[t].d4b) >= 0) {
				unsigned i;

				real_env(&hw, lmem, sizeof lmem);
				hw.g2x = NULL;
				hw.l25arm = 0;
				hw.l25on = 0;
				for (i = 0; i < rsets[t].n; i++) {
					const struct rs *p = &rsets[t].s[i];

					if ((p->host ? sw3_wr(&hw, SW3_HOST, 0u, p->a, p->v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, p->a, p->v)) != 0) break;
				}
				if (i == rsets[t].n) {
					int rs0, un = -1;

					for (i = 0; i < hw.g2n; i++) {      /* the unmask entry is `once` and the list builder marked it done (the stream runs after the L16 C2 doorbell): this case tries it as the stop path would, fresh */
						if (hw.g2l[i].space == SWW_BAR0 && hw.g2l[i].win == SWW_WIN_PCIE && hw.g2l[i].off == 0xC34u && hw.g2l[i].v == 0u) { un = (int)i; hw.g2l[i].done = 0; }
					}
					hw.g2x = &gx;
					gx.elv = 25u;
					hw.l25arm = 1;
					rs0 = sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, 0xC34u, 0u);
					if (un < 0 || rs0 != 0 || hw.l25on != 1) { if (bad++ < 20) SWG_BAD("BAD L25 route %s: 0xC34 := 0 (stop path) ret %d l25on %d unmask entry %d, want accepted while the judge is live\n", rsets[t].label, rs0, hw.l25on, un); }
					nstop++;
				}
			}
			hw.g2x = NULL;
			hw.l25arm = 0;
			hw.l25on = 0;
		}
		hw = hsave;
		sw_clm.p = clm_p;
		sw_clm.len = clm_len;
		sw_clm.crc = clm_crc;
		if (nl25 < 16u || nctl < 4u || nstop < 1u) {
			if (bad++ < 20) SWG_BAD("BAD L25 route: vacuous (routed %u controls %u stop %u)\n", nl25, nctl, nstop);
		}
	}
	/* T-3784 (review T-98d9 r1 F2): the ACCEPT side of the same route. The judge now reads a real host array (sw_g2_rd: h->g2va, the memory sw3_wr stores to), has a layout filled from the glue's own plan the way sw_g2_hook does (swl25g_wl_layout),
	 * and accepts stores that the dongle's state allows: sw3_wr must make the store (host word changed), keep the bookkeeping of the accepted class (swl25w_done: the doorbell due after a read-index store, cleared by the doorbell itself),
	 * ask for the flow-open scan after a ring-2 read-index store and ONLY then, and return 0 BEFORE the g2l[] / CA7 bookkeeping (g2i is SW_G2_L25 + class, not an index: a fall-through writes g2l[0x1000 + class], into the guard member).
	 * Cases: S1 ring-2 read index 0 -> 1 (class L8, one item written), S2 ring-3 read index 0 -> 1 (L8, no flow scan), S1d the doorbell of S1 (L9, clears the due doorbell; the judge refuses the next index store before it), S3 the doorbell of S2, S4 a doorbell with another value (refused, nothing written). */
	{
		static uint32_t amem[0x4C000 / 4];
		static struct sw_hw asave;
		struct swg_wx ax;
		const uint32_t dr = hw.g2plan.b[SWD_IDX_D2H_R].off, dw = hw.g2plan.b[SWD_IDX_D2H_W].off;
		unsigned i, ok = 0;
		uint32_t cm;
		int r;

		asave = hw;
		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		hw.g2pa = rs_pa;
		memset(&ax, 0, sizeof ax);
		real_env(&hw, amem, sizeof amem);
		{
			struct swl25w *w = &hw.l25w;

			memset(w, 0, sizeof *w);
			w->h2d_w = hw.g2plan.b[SWD_IDX_H2D_W].off;
			w->d2h_w = hw.g2plan.b[SWD_IDX_D2H_W].off;
			w->d2h_r = hw.g2plan.b[SWD_IDX_D2H_R].off;
			w->h2d_r = hw.g2plan.b[SWD_IDX_H2D_R].off;     /* as the kernel glue (patch 0206, ring-0 wrap e86af753) */
			for (i = 0; i < 5u; i++) {
				w->ring[i] = hw.g2plan.b[SWD_H2D_CTRL + i].off;
				w->depth[i] = swd_rings[SWD_H2D_CTRL + i].items;
			}
			w->ioctbuf = hw.g2plan.b[SWD_IOCTBUF].off;
			w->respbuf = hw.g2plan.b[SWD_RESPBUF].off;
			w->evbuf = hw.g2plan.b[SWD_EVBUF].off;
			w->g1_pa = rs_pa;
			w->g1_len = hw.g2plan.total;
			w->data_pa = (rs_pa + 0x10000000ull) & ~0xFFFull;
			w->data_len = 0x14000u;
			w->flowid = 2u;      /* SWL25_FLOWID = SWDP_FLOWID_FIRST (sweet_wlan_data.h:80) */
			w->rd = sw_l25w_rd;
			w->ctx = &hw;
			if (swl25w_layout_bad(w) != 0) { if (bad++ < 20) SWG_BAD("BAD L25 accept: the harness layout is not valid (clause %d)\n", swl25w_layout_bad(w)); }
		}
		amem[dr / 4u] = 0u;              /* ring 2 read index: 0 */
		amem[dw / 4u] = 1u;              /* ring 2 write index: the dongle wrote one item */
		amem[dr / 4u + 1u] = 0u;         /* ring 3 */
		amem[dw / 4u + 1u] = 1u;
		g_l25_rdlen = sizeof amem;
		memset(hw.l25guard, 0, sizeof hw.l25guard);
		hw.g2x = &ax;
		ax.elv = 25u;
		hw.l25arm = 1;
		hw.l25on = 0;
		hw.l25cls = 0;
		g_wlflow_n = 0u;
		for (int a = 0; a < 6; a++) memcpy(snap[a], arr[a], len[a] * 4);
		/* S1 */
		r = sw3_wr(&hw, SW3_HOST, 0u, dr, 1u);
		if (r != 0 || amem[dr / 4u] != 1u || hw.l25on != 1 || hw.l25cls != SWL25W_L8) { if (bad++ < 20) SWG_BAD("BAD L25 accept S1 (ring-2 read index 0 -> 1): ret %d word %u l25on %d class %d, want 0, 1, 1, %d\n", r, (unsigned)amem[dr / 4u], hw.l25on, hw.l25cls, SWL25W_L8); }
		if (hw.l25w.pend_db != 0xDD020001u) { if (bad++ < 20) SWG_BAD("BAD L25 accept S1: due doorbell %#x, want 0xdd020001 (swl25w_done not called with the accepted class)\n", (unsigned)hw.l25w.pend_db); }
		if (g_wlflow_n != 1u || g_wlflow_old != 0u || g_wlflow_nw != 1u) { if (bad++ < 20) SWG_BAD("BAD L25 accept S1: flow-open scans %u (old %u new %u), want exactly one for 0 -> 1\n", g_wlflow_n, (unsigned)g_wlflow_old, (unsigned)g_wlflow_nw); }
		/* S1d: the judge allows the next index store only after the doorbell of the last one (class L9, v = the due word): the store is made and the due word is cleared */
		r = sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, 0x140u, 0xDD020001u);
		if (r != 0 || hw.l25w.pend_db != 0u || hw.l25cls != SWL25W_L9) { if (bad++ < 20) SWG_BAD("BAD L25 accept S1d (the due doorbell of S1): ret %d due %#x class %d, want 0, 0, %d\n", r, (unsigned)hw.l25w.pend_db, hw.l25cls, SWL25W_L9); }
		/* S2 */
		r = sw3_wr(&hw, SW3_HOST, 0u, dr + 4u, 1u);
		if (r != 0 || amem[dr / 4u + 1u] != 1u || hw.l25w.pend_db != 0xDD030001u) { if (bad++ < 20) SWG_BAD("BAD L25 accept S2 (ring-3 read index 0 -> 1): ret %d word %u due doorbell %#x, want 0, 1, 0xdd030001\n", r, (unsigned)amem[dr / 4u + 1u], (unsigned)hw.l25w.pend_db); }
		if (g_wlflow_n != 1u) { if (bad++ < 20) SWG_BAD("BAD L25 accept S2: flow-open scans %u, a ring-3 read index must not ask for one (still 1)\n", g_wlflow_n); }
		/* S4 first (refused, nothing written), then S3 */
		for (int a = 0; a < 6; a++) memcpy(snap[a], arr[a], len[a] * 4);
		cm = hw.l25w.pend_db;
		r = sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, 0x140u, cm ^ 1u);
		{
			int ch = 0;

			for (int a = 0; a < 6; a++) ch += memcmp(snap[a], arr[a], len[a] * 4) != 0;
			if (r == 0 || ch != 0 || hw.l25w.pend_db != cm) { if (bad++ < 20) SWG_BAD("BAD L25 accept S4 (doorbell with a value that is not the due one): ret %d changed %d due %#x, want refused, nothing written, due unchanged\n", r, ch, (unsigned)hw.l25w.pend_db); }
		}
		r = sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, 0x140u, cm);
		if (r != 0 || hw.l25w.pend_db != 0u || hw.l25cls != SWL25W_L9) { if (bad++ < 20) SWG_BAD("BAD L25 accept S3 (the due doorbell): ret %d due %#x class %d, want 0, 0, %d\n", r, (unsigned)hw.l25w.pend_db, hw.l25cls, SWL25W_L9); }
		/* the accept path touches neither the list nor the CA7 release bookkeeping, and the guard behind the list stays zero */
		for (i = 0; i < sizeof hw.l25guard; i++) ok |= hw.l25guard[i];
		if (ok != 0u || hw.rel != 7u || hw.g2sh != 0u || hw.g2ev != 0u || hw.c88 != 0 || g_wlflow_n != 1u) { if (bad++ < 20) SWG_BAD("BAD L25 accept: bookkeeping outside the judge changed (guard %u rel %#x g2sh %u g2ev %u c88 %d)\n", ok, (unsigned)hw.rel, (unsigned)hw.g2sh, (unsigned)hw.g2ev, hw.c88); }
		g_l25_rdlen = 0u;
		hw = asave;
	}
	/* review F4: the (space, off, value) records of the header's ds_tests (i), (i-b), (iii) through the real list: every store accepted, g2sh == 2 x rounds (T-c2e2: the only extra round is the DS hold, ack + wake, no deassert); a round item with one word missing is refused at its slot store */
	{
		static uint32_t rmem[0x4C000 / 4];
		const uint32_t r0 = hw.g2plan.b[SWD_H2D_CTRL].off, ws = hw.g2plan.b[SWD_IDX_H2D_W].off;

		(void)swd_plan_make(4, 43, 4, &hw.g2plan);
		hw.g2pa = rs_pa;
		for (unsigned t = 0; t < nrsets; t++) {
			unsigned k;
			int r = 0;

			if (rsets[t].d4b >= 11) {       /* L22c: the CLM blob of the host test's clm_fill() (15,932 B), noted the way sw_fw_note does, with its container CRC */
				static uint8_t blob[15932];
				unsigned bi;

				for (bi = 0; bi < sizeof blob; bi++) {
					blob[bi] = (uint8_t)(bi * 7u + 3u + (bi >> 8));
				}
				sw_clm.p = blob;
				sw_clm.len = sizeof blob;
				sw_clm.crc = swf_crc32(blob, sizeof blob);
			}
			hw.jc = rsets[t].jc;        /* T-8ede: the L24 streams build sw_g2_d4j from the host test's own join table */
			hw.njc = rsets[t].njc;
			if (real_list_lv(&hw, 1, rsets[t].d4b) < 0) {
				if (bad++ < 20) SWG_BAD("BAD F4: the real list could not be built\n");
				break;
			}
			real_env(&hw, rmem, sizeof rmem);
			for (k = 0; k < rsets[t].n; k++) {
				const struct rs *q = &rsets[t].s[k];

				r = q->host ? sw3_wr(&hw, SW3_HOST, 0u, q->a, q->v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, q->a, q->v);
				if (r != 0) {
					if (bad++ < 20) SWG_BAD("BAD F4 stream %s: store %u (%s %#x := %#x) refused\n", rsets[t].label, k, q->host ? "host" : "pcie", q->a, q->v);
					break;
				}
				acc++;
			}
			if (r == 0 && hw.g2sh != 2u * rsets[t].rounds) {
				if (bad++ < 20) SWG_BAD("BAD F4 stream %s: g2sh %u, want %u\n", rsets[t].label, hw.g2sh, 2u * rsets[t].rounds);
			}
		}
		for (unsigned v = 0; v < 3u && nrsets > 0u; v++) {      /* round item 4 (the first round item of stream 0) loses word 0 / word 2 / word 4 (stale 1 left in memory) */
			const uint32_t drop = r0 + 160u + (v == 0u ? 0u : v == 1u ? 8u : 16u);
			unsigned k;
			int r = 0;

			if (real_list(&hw, 1) < 0) break;
			real_env(&hw, rmem, sizeof rmem);
			if (v == 2u) rmem[drop / 4u] = 1u;
			for (k = 0; k < rsets[0].n; k++) {
				const struct rs *q = &rsets[0].s[k];

				if (q->host && q->a == drop) continue;
				r = q->host ? sw3_wr(&hw, SW3_HOST, 0u, q->a, q->v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, q->a, q->v);
				if (r != 0) break;
			}
			if (k >= rsets[0].n || !rsets[0].s[k].host || rsets[0].s[k].a != ws || hw.g2sh != 0u) {
				if (bad++ < 20) SWG_BAD("BAD F4 negative %u (item word at %#x missing): not refused at the slot store (stopped at store %u, g2sh %u)\n", v, drop, k, hw.g2sh);
			}
		}
		/* review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 2: the six negatives of the reviewer's replay_neg.py on the "main" stream (L21-L23): the stored word changed in one place, the glue must refuse exactly that store; a word the stream
		 * never stores would make a negative vacuous (AP-371), so every match is required */
		{
			const uint32_t io = hw.g2plan.b[SWD_IOCTBUF].off;
			const struct { const char *what; uint32_t off, from, from2, to; int wild; } ng[9] = {
				{ "escan version word 1 := 4", io + 4u, 0x0002006Eu, 0x0003006Eu, 0x0004006Eu, 0 },
				{ "event mask bit 0 set too", io + 24u, 0x20000000u, 0x20000000u, 0x20000001u, 0 },
				{ "event mask bit 70 set too", io + 24u, 0x20000000u, 0x20000000u, 0x20000040u, 0 },
				{ "first event re-post PA + 64 KiB (kind SH_RBUF, buffer 8 = past the block; word 4 of index 34)", r0 + 34u * 40u + 16u, 0u, 0u, 0x10000u, 1 },
				{ "first event re-post PA + 4 KiB (kind SH_RBUF, misaligned; word 4 of index 34)", r0 + 34u * 40u + 16u, 0u, 0u, 0x1000u, 1 },
				{ "iovar name 'esca' -> 'jo'", io, 0x61637365u, 0x61637365u, 0x00006F6Au, 0 },
				{ "escan params length 86 -> 87", r0 + 33u * 40u + 16u, 86u, 86u, 87u, 0 },
				{ "first L22 response re-post PA + 4 KiB (kind SH_RBUF, misaligned; word 4 of index 24)", r0 + 24u * 40u + 16u, 0u, 0u, 0x1000u, 1 },
				{ "first L22 response re-post PA + 64 KiB (kind SH_RBUF, buffer 8; word 4 of index 24)", r0 + 24u * 40u + 16u, 0u, 0u, 0x10000u, 1 },
			};
			unsigned mt = nrsets;

			for (unsigned t = 0; t < nrsets; t++) {
				if (strcmp(rsets[t].label, "main") == 0) mt = t;
			}
			if (mt >= nrsets && bad++ < 20) SWG_BAD("BAD L21-23 negatives: no main stream\n");
			for (unsigned c = 0; c < 9u && mt < nrsets; c++) {
				unsigned k, hit = rsets[mt].n;
				int r = 0;

				for (k = 0; k < rsets[mt].n; k++) {
					const struct rs *q = &rsets[mt].s[k];

					if (q->host && q->a == ng[c].off && (ng[c].wild || q->v == ng[c].from || q->v == ng[c].from2)) { hit = k; break; }
				}
				if (hit >= rsets[mt].n) {
					if (bad++ < 20) SWG_BAD("BAD L21-23 negative %u (%s): the main stream never stores that word, the negative would be vacuous\n", c, ng[c].what);
					continue;
				}
				if (real_list_lv(&hw, 1, rsets[mt].d4b) < 0) break;
				real_env(&hw, rmem, sizeof rmem);
				for (k = 0; k <= hit && r == 0; k++) {
					const struct rs *q = &rsets[mt].s[k];
					const uint32_t v = k == hit ? (ng[c].wild ? q->v + ng[c].to : ng[c].to) : q->v;

					r = q->host ? sw3_wr(&hw, SW3_HOST, 0u, q->a, v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, q->a, v);
					if (r != 0 && k != hit && bad++ < 20) SWG_BAD("BAD L21-23 negative %u (%s): refused early at store %u of %u\n", c, ng[c].what, k, hit);
				}
				if (r == 0) {
					if (bad++ < 20) SWG_BAD("BAD L21-23 negative %u (%s): the changed word at store %u was ACCEPTED\n", c, ng[c].what, hit);
				} else {
					nneg24++;
				}
			}
		}
		/* T-8ede: the L24 negatives on the "j24" stream (and one on "j24key"), each refused at exactly the changed store (or, for the mixed item, at its commit); every target store must exist in the stream, else the negative is vacuous (AP-371) */
		{
			const uint32_t io = hw.g2plan.b[SWD_IOCTBUF].off;
			unsigned tj = nrsets, tk = nrsets, te62 = nrsets, ev = 0, ws1 = 0, ow = 0, cw = 0;
			int fev = 0, fow = 0, fcw = 0, fws = 0;

			for (unsigned t = 0; t < nrsets; t++) {
				if (strcmp(rsets[t].label, "j24") == 0) tj = t;
				if (strcmp(rsets[t].label, "j24key") == 0) tk = t;
				if (strcmp(rsets[t].label, "j24e62") == 0) te62 = t;
			}
			if ((tj >= nrsets || tk >= nrsets || te62 >= nrsets) && bad++ < 20) SWG_BAD("BAD L24 negatives: no j24 / j24key stream\n");
			for (unsigned k = 0; tj < nrsets && k < rsets[tj].n; k++) {
				const struct rs *q = &rsets[tj].s[k];

				if (q->host && q->a >= io && q->a < io + 176u && q->v == 0x742D736Fu && !fow) { ow = k; fow = 1; }      /* "os-t" of the join SSID */
				if (q->host && q->a >= r0 && q->a < r0 + 128u * 40u && (q->a - r0) % 40u == 0u && (q->v & 0xFFu) == 0x0Du) { ev = k; fev = 1; }      /* word 0 of the LAST event re-post (type 0x0D) */
			}
			for (unsigned k = ev + 1u; fev && k < rsets[tj].n; k++) {
				if (rsets[tj].s[k].host && rsets[tj].s[k].a == ws) { ws1 = k; fws = 1; break; }      /* that post's H2D-WR commit */
			}
			for (unsigned k = 0; tk < nrsets && k < rsets[tk].n; k++) {
				if (rsets[tk].s[k].host && rsets[tk].s[k].a >= io && rsets[tk].s[k].a < io + 176u && rsets[tk].s[k].v == 0x5A5A5A5Au) { cw = k; fcw = 1; break; }      /* the first all-canary word of the wsec_key payload */
			}
			if ((!fow || !fev || !fws || !fcw || ws1 != ev + 10u || rsets[tj].s[ev + 1u].a != rsets[tj].s[ev].a + 4u || rsets[tj].s[ev + 4u].a != rsets[tj].s[ev].a + 16u) && bad++ < 20) {
				SWG_BAD("BAD L24 negatives: a target store is missing (payload %d event %d commit %d key %d), the negatives would be vacuous\n", fow, fev, fws, fcw);
			} else if (tj < nrsets && tk < nrsets && te62 < nrsets) {
				const struct { const char *what; unsigned t, hit; uint32_t v; int skip; uint32_t pa, pv; int nojc; unsigned want; } ln[9] = {
					{ "join payload word 'os-t' -> 'os-u'", tj, ow, rsets[tj].s[ow].v + 0x01000000u, 0, 0, 0, 0, ow },
					{ "last event re-post request id + 1", tj, ev + 1u, rsets[tj].s[ev + 1u].v + 1u, 0, 0, 0, 0, ev + 1u },
					{ "last event re-post buffer = one past the 8th event buffer (T-84b0: word 4 accepts ANY of the 8, so +8 KiB of the last one can still be inside the 8)", tj, ev + 4u, (uint32_t)(rs_pa + hw.g2plan.b[SWD_EVBUF].off) + 8u * 0x2000u, 0, 0, 0, 0, ev + 4u },
					{ "mixed item (word 4 not stored, memory holds a buffer past the 8th): the E_ITEM prefix check refuses word 0", tj, ev + 4u, 0, 1, rsets[tj].s[ev + 4u].a, (uint32_t)(rs_pa + hw.g2plan.b[SWD_EVBUF].off) + 8u * 0x2000u, 0, ev },
					{ "event commit over an incomplete item (word 4 not stored, memory 0): the E_WR read-back refuses the commit", tj, ev + 4u, 0, 1, rsets[tj].s[ev + 4u].a, 0u, 0, ws1 },
					{ "wsec_key canary word changed", tk, cw, rsets[tk].s[cw].v ^ 1u, 0, 0, 0, 0, cw },
					{ "no join table: the L24 stores have no entry", tj, rsets[tj].n, 0, 0, 0, 0, 1, ow },
					{ "event commit by two items", tj, ws1, rsets[tj].s[ws1].v + 1u, 0, 0, 0, 0, ws1 },
					{ "event commit sets the slot's upper half", tj, ws1, rsets[tj].s[ws1].v | 0x10000u, 0, 0, 0, 0, ws1 },
				};
				unsigned nl24 = 0;

				for (unsigned c = 0; c < 9u; c++) {
					g_out[0] = 0;
					g_trunc = 0;
					const int got = l24_neg(&hw, rmem, sizeof rmem, ln[c].t, ln[c].hit, ln[c].v, ln[c].skip, ln[c].pa, ln[c].pv, ln[c].nojc);

					if (c == 6u ? (got < 0 || (unsigned)got >= rsets[tj].n || (unsigned)got > ow) : got != (int)ln[c].want) {
						if (bad++ < 20) SWG_BAD("BAD L24 negative %u (%s): refused at store %d, want %u\n", c, ln[c].what, got, ln[c].want);
					} else if (c == 5u && (g_trunc || strstr(g_out, "5a5a5a5") != NULL || strstr(g_out, "(ioctl payload, value withheld) not in the list") == NULL)) {      /* T-23da F4: the refused key word is named, never printed */
						if (bad++ < 20) SWG_BAD("BAD L24 negative 5: the refusal line of a wsec_key payload word prints its value (or no withheld line)\n");
					} else {
						nl24++;
					}
				}
				/* review T-857c F1: the cur_etheraddr payload word (host-test MAC bytes b4 0f a1 09 = 0x09a10fb4) on the j24mac stream is refused at its own store, and the refusal names it without printing the value: the glue's key-class list covers cur_etheraddr */
				{
					unsigned tm = nrsets, cm = 0, k;
					int fm = 0;

					for (unsigned t = 0; t < nrsets; t++) if (strcmp(rsets[t].label, "j24mac") == 0) tm = t;
					for (k = 0; tm < nrsets && k < rsets[tm].n; k++) if (rsets[tm].s[k].host && rsets[tm].s[k].a >= io && rsets[tm].s[k].a < io + 176u && rsets[tm].s[k].v == 0x09a10fb4u) { cm = k; fm = 1; break; }
					if (!fm) {
						if (bad++ < 20) SWG_BAD("BAD L24 negative cur_etheraddr: no j24mac stream or no MAC payload word, the negative would be vacuous\n");
					} else {
						g_out[0] = 0;
						g_trunc = 0;
						const int got = l24_neg(&hw, rmem, sizeof rmem, tm, cm, rsets[tm].s[cm].v ^ 1u, 0, 0, 0, 0);

						if (got != (int)cm || g_trunc || strstr(g_out, "9a10fb") != NULL || strstr(g_out, "(ioctl payload, value withheld) not in the list") == NULL) {
							if (bad++ < 20) SWG_BAD("BAD L24 negative cur_etheraddr: refused at store %d, want %u, or the refusal line prints the MAC word (or no withheld line)\n", got, cm);
						} else {
							nl24++;
						}
					}
				}
				/* T-23da (review T-4a7f r1 F1): the first L24 store (call 0's first payload word) moved to right after the escan request doorbell, before any event re-post: refused (every L24 entry needs g2ev >= 1) */
				{
					unsigned esp = rsets[tj].n, edb = rsets[tj].n, fl = rsets[tj].n, k;
					int got = -2;

					for (k = 0; k < rsets[tj].n && esp == rsets[tj].n; k++) if (rsets[tj].s[k].host && rsets[tj].s[k].a == io && rsets[tj].s[k].v == 0x61637365u) esp = k;      /* "esca": the escan payload word 0 */
					for (k = esp; k < rsets[tj].n && edb == rsets[tj].n; k++) if (!rsets[tj].s[k].host) edb = k;   /* the escan request doorbell */
					for (k = edb; k < rsets[tj].n && fl == rsets[tj].n; k++) if (rsets[tj].s[k].host && rsets[tj].s[k].a >= io && rsets[tj].s[k].a < io + 176u) fl = k;      /* the first L24 payload store */
					if (fl < rsets[tj].n && fl > edb + 1u) {
						hw.jc = rsets[tj].jc;
						hw.njc = rsets[tj].njc;
						if (real_list_lv(&hw, 1, rsets[tj].d4b) >= 0) {
							real_env(&hw, rmem, sizeof rmem);
							for (k = 0; k <= edb; k++) if ((rsets[tj].s[k].host ? sw3_wr(&hw, SW3_HOST, 0u, rsets[tj].s[k].a, rsets[tj].s[k].v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, rsets[tj].s[k].a, rsets[tj].s[k].v)) != 0) break;
							got = k == edb + 1u && hw.g2ev == 0u ? sw3_wr(&hw, SW3_HOST, 0u, rsets[tj].s[fl].a, rsets[tj].s[fl].v) : -3;
						}
					}
					if (got != -1) {
						if (bad++ < 20) SWG_BAD("BAD L24 negative early store (escan payload %u doorbell %u first L24 %u): got %d, want refused (-1)\n", esp, edb, fl, got);
					} else {
						nl24++;
					}
				}
				/* T-4459 (review T-48a0 r1 F2): the interim ring-2 witness (g2rd + g2ev0 + g2ev) behaviourally. The stream tj is replayed to a state the GLUE recorded (hw.g2rd / hw.g2ev), then the first L24 payload store is tried.
				 * In this stream the first store after the escan doorbell is the ring-2 consume (R_RD commit) and the first event flush follows it; the real header order is the reverse (the escan's own flush comes first), so g2ev is preset where needed.
				 * (0) doorbell done, the escan's own event flush counted (g2ev preset 1), no consume yet: refused (kills "the consume before the doorbell counts", "any commit counts");
				 * (1) the first consume after the doorbell committed, no event flush after it: refused (kills "the gate only needs g2rd");
				 * (2) as (1) with g2ev preset 1 before the consume (a flush made before it): refused (kills "g2ev0 not recorded");
				 * (3) control: the first event flush after the consume committed: accepted. A case whose state is never reached counts as a failure (vacuous). */
				{
					unsigned esp = rsets[tj].n, edb = rsets[tj].n, fl = rsets[tj].n, k, ps;

					for (k = 0; k < rsets[tj].n && esp == rsets[tj].n; k++) if (rsets[tj].s[k].host && rsets[tj].s[k].a == io && rsets[tj].s[k].v == 0x61637365u) esp = k;
					for (k = esp; k < rsets[tj].n && edb == rsets[tj].n; k++) if (!rsets[tj].s[k].host) edb = k;
					for (k = edb; k < rsets[tj].n && fl == rsets[tj].n; k++) if (rsets[tj].s[k].host && rsets[tj].s[k].a >= io && rsets[tj].s[k].a < io + 176u) fl = k;
					for (ps = 0; ps < 4u; ps++) {
						int got = -3;

						if (fl < rsets[tj].n && fl > edb + 1u) {
							hw.jc = rsets[tj].jc;
							hw.njc = rsets[tj].njc;
							if (real_list_lv(&hw, 1, rsets[tj].d4b) >= 0) {
								real_env(&hw, rmem, sizeof rmem);
								for (k = 0; k < fl; k++) {
									if ((rsets[tj].s[k].host ? sw3_wr(&hw, SW3_HOST, 0u, rsets[tj].s[k].a, rsets[tj].s[k].v) : sw3_wr(&hw, SWW_BAR0, SWW_WIN_PCIE, rsets[tj].s[k].a, rsets[tj].s[k].v)) != 0) break;
									if (k == edb && (ps == 0u || ps == 2u)) hw.g2ev = 1u;
									if ((ps == 0u && k == edb) || (k > edb && ((ps == 1u && hw.g2rd) || (ps == 2u && hw.g2rd) || (ps == 3u && hw.g2rd && hw.g2ev > hw.g2ev0)))) {
										got = sw3_wr(&hw, SW3_HOST, 0u, rsets[tj].s[fl].a, rsets[tj].s[fl].v);
										break;
									}
								}
							}
						}
						if (got != (ps == 3u ? 0 : -1)) {
							if (bad++ < 20) SWG_BAD("BAD L24 witness case %u (%s): first L24 store got %d, want %s (escan doorbell %u first L24 %u; -3 = the state was not reached)\n", ps,
							    ps == 0u ? "escan flush counted, no consume" : ps == 1u ? "consume, no event flush after it" : ps == 2u ? "flush before the consume, none after" : "control: event flush after the consume", got, ps == 3u ? "accepted (0)" : "refused (-1)", edb, fl);
						} else {
							nl24++;
						}
					}
				}
				/* after a whole stream (every entry done): a complete expected event post at ring-0 index 127 committed to 128 is refused (ring depth), the same at the real slot is accepted (control);
				 * after j24e62 (g2ev 48 = SW_G2_E_NREP_CLM, the L24 streams run under the CLM blob) the 49th post is refused at its first word and, with the item preset in memory, at its commit; the same on j24 (g2ev 6) is accepted (control) */
				for (unsigned c = 0; c < 4u; c++) {
					const unsigned t = c == 3u ? te62 : tj;
					uint32_t w[10], sl;
					int got = l24_neg(&hw, rmem, sizeof rmem, t, rsets[t].n, 0, 0, 0, 0, 0), r1, r2;

					if (got != (int)rsets[t].n || hw.g2ev != (c == 3u ? 48u : 6u)) {
						if (bad++ < 20) SWG_BAD("BAD L24 bound case %u: stream %s not accepted whole (stopped at %d of %u, g2ev %u)\n", c, rsets[t].label, got, rsets[t].n, hw.g2ev);
						continue;
					}
					sl = rmem[ws / 4u] & 0xFFFFu;
					if (c == 1u) {
						rmem[ws / 4u] = (rmem[ws / 4u] & 0xFFFF0000u) | 127u;
						sl = 127u;
					}
					sw_g2_e_evexp(&hw, w, sl);
					r1 = c == 1u ? 0 : sw3_wr(&hw, SW3_HOST, 0u, r0 + 40u * sl, w[0]);       /* the first word through the list (case 1: memory preset only) */
					for (unsigned k = 0; k < 10u; k++) rmem[(r0 + 40u * sl) / 4u + k] = w[k];
					r2 = sw3_wr(&hw, SW3_HOST, 0u, ws, (rmem[ws / 4u] & 0xFFFF0000u) | (sl + 1u));
					if (c == 0u ? (r1 != 0 || r2 != 0) : c == 1u ? r2 == 0 : c == 2u ? (r1 != 0 || r2 != 0) : (r1 == 0 || r2 == 0)) {
						if (bad++ < 20) SWG_BAD("BAD L24 bound case %u (%s): word 0 %d, commit %d\n", c, c == 1u ? "commit 127 -> 128" : c == 3u ? "49th event re-post" : "control", r1, r2);
					} else {
						nl24++;
					}
				}
				/* T-23da (review T-4a7f r1 F2): after a whole j24 stream, the slot preset to 127: word 0 of the expected event post at item 127 is refused (the header never builds item 127: idx + 1 >= 128 stops) */
				{
					uint32_t w[10];
					int got = l24_neg(&hw, rmem, sizeof rmem, tj, rsets[tj].n, 0, 0, 0, 0, 0), r1 = 0;

					if (got == (int)rsets[tj].n) {
						rmem[ws / 4u] = (rmem[ws / 4u] & 0xFFFF0000u) | 127u;
						sw_g2_e_evexp(&hw, w, 127u);
						r1 = sw3_wr(&hw, SW3_HOST, 0u, r0 + 40u * 127u, w[0]);
					}
					if (got != (int)rsets[tj].n || r1 == 0) {
						if (bad++ < 20) SWG_BAD("BAD L24 bound case slot 127: stream %d of %u, event word 0 at item 127 %s\n", got, rsets[tj].n, r1 == 0 ? "accepted" : "refused");
					} else {
						nl24++;
					}
				}
				{
					unsigned nj = 0;

					for (unsigned t = 0; t < nrsets; t++) if (rsets[t].jc != NULL) nj++;      /* the L24 streams are the ones with a join table (T-84b0: no longer nrsets - 9, the L21-L23 labels grew) */
					g_l24_nj = nj;
					g_l24_neg = nl24;
				}
			}
			hw.jc = NULL;
			hw.njc = 0u;
		}
		hw.g2 = 0;
		hw.g2va = 0;
		hw.g2sh = 0;
		hw.g2ev = 0;
		hw.g2ev0 = 0;
		hw.g2rd = 0;
		hw.g2n = 0;
	}"""


def self_test():
    t = GLUE_HARNESS_C
    ok = len(t.split("\n")) > 100 and "\"\"\"" not in t and t.startswith("\t/* T-98d9 (T-43af L25 wiring") and "\t/* review F4" in t
    print("RESULT: %s wlan_glue_harness_c self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL wlan_glue_harness_c is a module of tools/test_wlan_wr_mutants.py: run it with --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
