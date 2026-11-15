/* wlan_l25_host_sc.h - host cases of patch 0292 (WIFI-COVERAGE D3/D4, the live scan of the kept link), included by wlan_l25_host_test.c after wlan_l25_host_rj.h so the mutants of tools/test_wlan_l25_mutants.py
 * (rows of tools/wlan_keep_mutants.py SC_ROWS) are killed by the same plain + ASan runs. The ioctl transport is the recording stub of wlan_l25_host_test.c (rj_stub also records the "escan" SET_VAR and answers escan_st); ring 2 is the real
 * fake bus of the l25 world. The ESCAN_RESULT events themselves are covered with real ring items by wlan_g2_host_test.c (swg_ke_escan); here they are replaced by what they leave behind (x.sc_complete, x.sc_ev, x.sc_part). No hardware.
 * Cases: S1 request / start / complete (the one command, its bytes, the state flags, the take); S2 timeout; S3 link lost during the scan and the re-join afterwards; S4 refusals (link not UP, off, a race with a link loss, version unknown, ring 2 busy);
 * S5 the firmware refuses the start; S6 a soft stop and a hard stop in the ioctl, a stop raised by the drain; S7 abort (engine ends); S8 no secret or address in the log. */

/* the 80 B START (all channels) as the judge expects it, typed here byte by byte from the layout of swg_e_l23's comment (NOT from the builder): version vers, action 1, sync_id = the sync of THIS scan (0x1234 + its number, review 0292 F2), params version vers, length 72, bssid ff x6, bss_type 2, scan_type 1, five -1 words (nprobes, active,
 * passive, home = 16 x ff from +60), channel_num 0, no chanspecs (the 80 B ends at +79) */
static void sc_expect(uint8_t e[96], unsigned vers, unsigned sync)
{
	unsigned i;

	memset(e, 0, 96);
	e[0] = (uint8_t)vers; e[4] = 1u; e[6] = (uint8_t)sync; e[7] = (uint8_t)(sync >> 8); e[8] = (uint8_t)vers; e[10] = 72u;
	for (i = 48; i < 54u; i++) e[i] = 0xFFu;
	e[54] = 2u; e[56] = 1u;
	for (i = 60; i < 76u; i++) e[i] = 0xFFu;
	e[76] = 0u;       /* channel_num 0: all channels, the 80 B form; bytes 80..95 stay zero and are NOT sent */
}

static struct swl25_sc SC;
static void sc_world(int arm)
{
	rj_world(arm);          /* kept engine, stub transport on, the 0281 detector went DOWN at the end of it: put the link back UP */
	x.klink = 0u; x.kl_new = 0u; x.rj_state = SWG_RJ_UP; x.rj_ssid = x.rj_link = 0u; x.rj_lost = 0u;
	x.emajor = 3u;
	x.sc_on = 0u; x.sc_complete = 0u; x.sc_ev = x.sc_part = x.sc_other = 0u;
	x.sc_foreign = x.sc_term = 0u; x.sc_termd = x.sc_await = x.sc_await_done = 0u; x.escan_sync = 0u;
	memset(&SC, 0, sizeof SC);
	loglen = 0u;
	logbuf[0] = '\0';
	ngap = 0u;
}

/* the scan START at ms t: asserts the ONE command and its bytes; returns with the scan RUNning */
static void sc_start(uint64_t t, unsigned vers)
{
	uint8_t e[96];
	char l[160];

	nRC = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && SC.req == 1u && SC.n_req == 1u);
	CHECK(swl25_sc_step(&x, &SC, t) == 0);
	sc_expect(e, vers, 0x1235u);       /* the first scan of a world: 0x1234 + 1 */
	CHECK(nRC == 1u && RC[0].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[0].name, "escan") == 0 && RC[0].argn == 80u && RC[0].wlen == 86u && RC[0].secret == 0 && memcmp(RC[0].arg, e, 80) == 0);
	CHECK(l25_last_soft == 1 && l25_last_repost == 1 && l25_last_krf == 1);       /* soft: a refused completion is a GAP not a stop; the response buffer is re-posted; the refresh flag is set during the call (no DS round, 1 ms polls) */
	CHECK(SC.state == SWL25_SC_RUN && SC.req == 0u && SC.t0 == t && SC.n_start == 1u && x.sc_on == 1u && x.sc_complete == 0u && x.sc_ev == 0u && x.krefresh == 0u && !x.d4dstop && !x.stopped);
	snprintf(l, sizeof l, "SWEET WLAN: keep scan start 1: passive escan, all channels, version %u, sync 0x1235, budget 12000 ms\n", vers);
	CHECK(has(l) && rj_count_of("keep scan start") == 1u);
}

static void test_scan(void)
{
	uint32_t kind = 99u, ms = 99u, ev = 99u, part = 99u;
	unsigned g0;
	uint8_t e[96];

	rj_on = 1;
	CHECK(SWL25_SC_EBUSY == 16u && SWL25_SC_ENXIO == 6u && SWL25_SC_ENETDOWN == 50u && SWL25_SC_DEFER_MS == 100u);       /* the Darwin errnos of sweet_wlan_radio.h SWR_EBUSY / SWR_E_NXIO / SWR_E_NETDOWN, typed here */
	CHECK(SWL25_SCR_COMPLETE == 1u && SWL25_SCR_TIMEOUT == 2u && SWL25_SCR_LINKLOST == 3u && SWL25_SCR_FAILED == 4u && SWL25_SCR_REFUSED == 5u);       /* = SWR_SCAN_* */
	/* S1: request -> start -> events -> complete. One firmware command in all. The old flags of an earlier scan (sc_complete, counters) do not leak into the new one. */
	sc_world(1);
	x.sc_complete = 1u; x.sc_ev = 9u; x.sc_part = 8u; x.sc_other = 7u;
	CHECK(swl25_sc_idle(&SC) && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 0);
	CHECK(swl25_sc_step(&x, &SC, 100u) == 0 && nRC == 0u);       /* nothing requested: nothing sent */
	sc_start(1000u, 3u);
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_EBUSY && SC.n_req == 1u);       /* one at a time: running */
	nRC = 0u;
	CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && swl25_sc_step(&x, &SC, 3000u) == 0 && nRC == 0u && SC.state == SWL25_SC_RUN && !swl25_sc_idle(&SC) && SC.res_new == 0u && x.sc_on == 1u);       /* collecting: no second command */
	x.sc_ev = 5u; x.sc_part = 4u; x.sc_complete = 1u;       /* what swg_ke_escan leaves: four PARTIAL events and the status 0 one */
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_step(&x, &SC, 1234u) == 0);
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_EBUSY);       /* finished but not yet handed to iOS: still one at a time */
	CHECK(SC.state == SWL25_SC_IDLE && swl25_sc_idle(&SC) && x.sc_on == 0u && SC.res_new == 1u && SC.n_done == 1u && swl_gap_tot == g0 && !has("WIFI GAP: keep scan"));       /* complete: no GAP */
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_COMPLETE && ms == 234u && ev == 5u && part == 4u);
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 0 && swl25_sc_step(&x, &SC, 5000u) == 0 && nRC == 0u);       /* taken once; nothing more is sent */
	/* a second scan after the first one; version 2 payload; sc_complete from the first scan does not end it at once */
	x.emajor = 2u;
	nRC = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 6000u) == 0 && x.sc_complete == 0u && x.sc_ev == 0u && x.sc_part == 0u && x.sc_other == 0u && x.sc_on == 1u);
	sc_expect(e, 2u, 0x1236u);
	CHECK(nRC == 1u && memcmp(RC[0].arg, e, 96) == 0 && SC.n_start == 2u && x.escan_sync == 0x1236u);       /* F2: the second scan has its own id */
	CHECK(swl25_sc_step(&x, &SC, 6001u) == 0 && SC.state == SWL25_SC_RUN);       /* not complete yet: the old complete flag was cleared at the start */

	/* S2: the budget. 11999 ms: still collecting; 12000 ms: SCAN_DONE with what was collected (kind TIMEOUT) and a GAP; the escan is not aborted (no command) */
	sc_world(1);
	sc_start(2000u, 3u);
	x.sc_ev = 2u; x.sc_part = 2u;
	CHECK(swl25_sc_step(&x, &SC, 2000u + SWL25_SC_BUDGET_MS - 1u) == 0 && SC.state == SWL25_SC_RUN && x.sc_on == 1u && SC.res_new == 0u && !has("WIFI GAP: keep scan"));
	g0 = (unsigned)swl_gap_tot;
	nRC = 0u;
	CHECK(swl25_sc_step(&x, &SC, 2000u + SWL25_SC_BUDGET_MS) == 0 && nRC == 0u);
	CHECK(SC.state == SWL25_SC_IDLE && x.sc_on == 0u && SC.n_timeout == 1u && swl_gap_tot == g0 + 1u && has("WIFI GAP: keep scan not complete (timeout): no ESCAN_RESULT SUCCESS in the budget; 2 events of ours (2 partial), 12000 ms; what was collected is delivered\n"));
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_TIMEOUT && ms == SWL25_SC_BUDGET_MS && ev == 2u && part == 2u);
	CHECK(SWL25_SC_BUDGET_MS == 12000u);
	nRC = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 9000u) == 0 && nRC == 1u);       /* a timed-out scan does not block the next one */

	/* S3: the AP link is lost during the scan: the scan ENDS first (kind LINKLOST, GAP, nothing sent to abort it), the state is idle in the same call, so the re-join step that follows in the thread may start; a re-join never starts while the scan state is RUN */
	sc_world(1);
	sc_start(1000u, 3u);
	x.sc_await_done = 1u; x.sc_term = 7u;       /* stale values of an earlier cut-short scan: the link loss must clear them (F4) */
	rj_ev(SWG_E_EV_DEAUTH_IND, 0u, 0u);
	CHECK(x.klink == 1u && x.rj_state == SWG_RJ_DOWN && SC.state == SWL25_SC_RUN);       /* the detector only notes it: the scan is still RUN until its step runs */
	nRC = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_step(&x, &SC, 1500u) == 0 && nRC == 0u);
	CHECK(SC.state == SWL25_SC_IDLE && !swl25_sc_idle(&SC) && SC.hold == 1u && SC.hold_until == 1500u + SWL25_SC_HOLD_MS && x.sc_await == 1u && x.sc_on == 0u && SC.n_lost == 1u && swl_gap_tot == g0 + 1u && has("WIFI GAP: keep scan not complete (link lost): the link went down during the scan; the escan is not aborted (no abort command is sent), the re-join waits for its end status or a short bound;"));
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_LINKLOST && ms == 500u);
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENETDOWN);       /* DOWN: no new scan */
	/* F4: held until the firmware's end status of the cut-short scan ... */
	CHECK(swl25_sc_step(&x, &SC, 1600u) == 0 && !swl25_sc_idle(&SC) && SC.hold == 1u && !has("the re-join may start") && !has("no end status"));       /* neither the status nor the bound yet */
	x.sc_await_done = 1u; x.sc_term = 4u;       /* what swg_ke_escan leaves for an ABORT of our sync id */
	loglen = 0u; logbuf[0] = '\0';
	CHECK(swl25_sc_step(&x, &SC, 1700u) == 0 && swl25_sc_idle(&SC) && SC.hold == 0u && x.sc_await == 0u && has("SWEET WLAN: keep scan: the firmware ended the scan cut short by the link loss (status 4) after 200 ms, the re-join may start\n") && !has("WIFI GAP: keep scan: no end status"));
	CHECK(swl25_rj_step(&x, &RJX, 1800u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING && RJX.n == 1u);       /* the re-join runs normally after the scan ended */
	/* N1 (re-check of 7a2d5812): the firmware's end status was handled in the SAME drain as the DEAUTH (sc_termd set before the scan step): no hold wait, no false GAP */
	sc_world(1);
	sc_start(1000u, 3u);
	rj_ev(SWG_E_EV_DEAUTH_IND, 0u, 0u);
	x.sc_termd = 1u; x.sc_term = 4u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_step(&x, &SC, 1500u) == 0 && x.sc_await_done == 1u);
	loglen = 0u; logbuf[0] = '\0';
	CHECK(swl25_sc_step(&x, &SC, 1600u) == 0 && swl25_sc_idle(&SC) && SC.hold == 0u && has("the re-join may start") && !has("WIFI GAP: keep scan: no end status"));
	/* ... or until the bound: a GAP, then it may start */
	sc_world(1);
	sc_start(1000u, 3u);
	x.klink = 1u;
	CHECK(swl25_sc_step(&x, &SC, 1500u) == 0 && !swl25_sc_idle(&SC) && SC.hold_until == 1500u + SWL25_SC_HOLD_MS && SWL25_SC_HOLD_MS == 12000u && SWL25_SC_HOLD_MS == SWL25_SC_BUDGET_MS);
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_step(&x, &SC, 1500u + SWL25_SC_HOLD_MS - 1u) == 0 && !swl25_sc_idle(&SC) && swl_gap_tot == g0 && !has("no end status"));
	CHECK(swl25_sc_step(&x, &SC, 1500u + SWL25_SC_HOLD_MS) == 0 && swl25_sc_idle(&SC) && SC.hold == 0u && x.sc_await == 0u && swl_gap_tot == g0 + 1u && has("WIFI GAP: keep scan: no end status of the scan cut short by the link loss within 12000 ms; the re-join starts with the escan possibly still running in the firmware\n"));

	/* S3b: either flag alone ends a running scan (the detector flag klink, or a re-join state other than UP) */
	sc_world(1);
	sc_start(1000u, 3u);
	x.klink = 1u;
	CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && SC.state == SWL25_SC_IDLE && SC.n_lost == 1u && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_LINKLOST);
	sc_world(1);
	sc_start(1000u, 3u);
	x.rj_state = SWG_RJ_KEYING;
	CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && SC.state == SWL25_SC_IDLE && SC.n_lost == 1u && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_LINKLOST);

	/* S4: refusals. Link DOWN or a re-join state other than UP: ENETDOWN, no flag set; off: ENXIO; a request that raced with a link loss: refused by the step with a GAP and no command; the scan version unknown: refused */
	sc_world(1);
	x.klink = 1u; x.rj_state = SWG_RJ_DOWN;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENETDOWN && SC.req == 0u && SC.n_req == 0u);
	x.klink = 0u; x.rj_state = SWG_RJ_JOINING;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENETDOWN && SC.req == 0u);
	x.rj_state = SWG_RJ_KEYING;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENETDOWN && SC.req == 0u);
	x.klink = 1u; x.rj_state = SWG_RJ_UP;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENETDOWN && SC.req == 0u);       /* either flag alone refuses */
	x.klink = 0u; x.rj_state = SWG_RJ_UP;
	SC.off = 1u;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENXIO && SC.req == 0u);
	SC.off = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_request(&SC, &x) == (int)SWL25_SC_EBUSY && SC.n_req == 1u);       /* a second request before the keep thread took the first: busy (pending) */
	x.klink = 1u; x.rj_state = SWG_RJ_DOWN;       /* the link went down between the request and the step */
	nRC = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.state == SWL25_SC_IDLE && SC.req == 0u && SC.n_refused == 1u && x.sc_on == 0u && swl_gap_tot == g0 + 1u);
	CHECK(has("WIFI GAP: keep scan not complete (refused): requested while the link is not UP (the re-join owns the engine);") && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_REFUSED && ms == 0u);
	sc_world(1);
	CHECK(swl25_sc_request(&SC, &x) == 0);
	x.klink = 1u;       /* only the detector flag */
	CHECK(swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.n_refused == 1u && SC.state == SWL25_SC_IDLE);
	sc_world(1);
	CHECK(swl25_sc_request(&SC, &x) == 0);
	x.rj_state = SWG_RJ_JOINING;       /* only the machine state */
	CHECK(swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.n_refused == 1u && SC.state == SWL25_SC_IDLE);
	sc_world(1);
	x.emajor = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.n_refused == 1u && has("the scan version (scan_ver) was not read at boot") && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_REFUSED);
	sc_world(1);
	x.emajor = 4u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.n_refused == 1u);       /* only 2 and 3 are versions the judge and the layout know */
	sc_world(1);
	SC.off = 1u;
	SC.req = 1u;       /* a request that was accepted just before the scan went off */
	CHECK(swl25_sc_step(&x, &SC, 500u) == 0 && nRC == 0u && SC.n_refused == 1u && has("the scan is off for this boot") && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_REFUSED);
	/* ring 2 not readable / not idle: the start waits SWL25_SC_DEFER_MS (one line, no command), then goes; if it never gets idle for the budget it fails with a GAP */
	sc_world(1);
	CHECK(swl25_sc_request(&SC, &x) == 0);
	{
		const __typeof__(x.in.idx[0]) save = x.in.idx[SWG_IDX(SWD_IDX_D2H_W)];

		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;       /* the D2H write index cannot be read: ring 2 counts as not idle */
		CHECK(swl25_sc_step(&x, &SC, 1000u) == 0 && nRC == 0u && SC.state == SWL25_SC_IDLE && SC.req == 1u && SC.t_next == 1000u + SWL25_SC_DEFER_MS && has("SWEET WLAN: keep scan: ring 2 not idle, the start waits 100 ms (not counted)\n") && SC.n_start == 0u);
		CHECK(swl25_sc_step(&x, &SC, 1000u + SWL25_SC_DEFER_MS - 1u) == 0 && nRC == 0u && rj_count_of("ring 2 not idle") == 1u && SC.t_next == 1000u + SWL25_SC_DEFER_MS);       /* too early: nothing, the next try time is unchanged */
		CHECK(swl25_sc_step(&x, &SC, 1000u + SWL25_SC_DEFER_MS) == 0 && nRC == 0u && rj_count_of("ring 2 not idle") == 1u && SC.t_next == 1000u + 2u * SWL25_SC_DEFER_MS);       /* still busy: waits again, says nothing again */
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
		CHECK(swl25_sc_step(&x, &SC, 1000u + 2u * SWL25_SC_DEFER_MS) == 0 && nRC == 1u && SC.state == SWL25_SC_RUN && SC.t0 == 1000u + 2u * SWL25_SC_DEFER_MS && SC.n_start == 1u && SC.t_defer == 0u);       /* idle again: it starts, the clock starts now */
		sc_world(1);
		CHECK(swl25_sc_request(&SC, &x) == 0);
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;
		CHECK(swl25_sc_step(&x, &SC, 1000u) == 0 && SC.req == 1u);
		g0 = (unsigned)swl_gap_tot;
		CHECK(swl25_sc_step(&x, &SC, 1000u + SWL25_SC_BUDGET_MS) == 0 && nRC == 0u && SC.state == SWL25_SC_IDLE && SC.req == 0u && SC.n_failed == 1u && swl_gap_tot == g0 + 1u && has("failed): ring 2 never idle in the budget;"));
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
		CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && ms == 0u);
	}
	/* ring 2 readable but with an item the drain does not take (here: the engine is not in the keep phase, swg_keep_r2 is inert): the write index differs from the read count, so ring 2 is NOT idle: the start waits */
	sc_world(1);
	x.kept = 0u;
	g1[(D2HW - G1PA) / 4u] = 1u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == 0 && nRC == 0u && SC.req == 1u && SC.t_next == 1000u + SWL25_SC_DEFER_MS && SC.n_start == 0u);
	g1[(D2HW - G1PA) / 4u] = 0xFFFFFFFFu;       /* a dead link reads all ones: not idle either */
	CHECK(swl25_sc_step(&x, &SC, 1000u + SWL25_SC_DEFER_MS) == 0 && nRC == 0u && SC.req == 1u && SC.t_next == 1000u + 2u * SWL25_SC_DEFER_MS);
	g1[(D2HW - G1PA) / 4u] = 0u;
	x.kept = 1u;

	/* a first deferral late in the boot (now far above the budget): it waits, it is not failed (the deferral clock starts at the FIRST deferral, not at 0) */
	sc_world(1);
	{
		const __typeof__(x.in.idx[0]) save = x.in.idx[SWG_IDX(SWD_IDX_D2H_W)];

		CHECK(swl25_sc_request(&SC, &x) == 0);
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;
		CHECK(swl25_sc_step(&x, &SC, 100000u) == 0 && SC.req == 1u && SC.res_new == 0u && SC.n_failed == 0u && SC.t_defer == 100000u && SC.t_next == 100000u + SWL25_SC_DEFER_MS);
		/* the deferral line is told again for the NEXT scan (the flag is reset at a start) */
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
		CHECK(swl25_sc_step(&x, &SC, 100000u + SWL25_SC_DEFER_MS) == 0 && SC.state == SWL25_SC_RUN && SC.defer_gap == 0u && rj_count_of("ring 2 not idle") == 1u);
		x.sc_complete = 1u;
		CHECK(swl25_sc_step(&x, &SC, 100300u) == 0 && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_COMPLETE);
		CHECK(swl25_sc_request(&SC, &x) == 0);
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;
		CHECK(swl25_sc_step(&x, &SC, 100400u) == 0 && rj_count_of("ring 2 not idle") == 2u);
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
	}
	/* a HARD stop (the bus is gone) is returned as -1 before anything is sent; the request stays; a stop raised by the drain is not mistaken for a busy ring (no deferral) */
	sc_world(1);
	CHECK(swl25_sc_request(&SC, &x) == 0);
	x.stopped = 1;
	CHECK(swl25_sc_step(&x, &SC, 1000u) == -1 && nRC == 0u && SC.req == 1u && SC.t_next == 0u && !has("ring 2 not idle"));
	x.stopped = 0;

	/* S5: the firmware refuses the START (status != 0): GAP with the status, kind FAILED, nothing collected, the scan is NOT off (the next request goes) */
	sc_world(1);
	RJ.escan_st = -5;
	nRC = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == 0 && nRC == 1u);
	CHECK(SC.state == SWL25_SC_IDLE && x.sc_on == 0u && SC.n_failed == 1u && SC.off == 0u && x.krefresh == 0u && !x.d4dstop && has("WIFI GAP: keep scan: WLC_SET_VAR escan refused, status=-5\n") && has("failed): escan start refused by the firmware;") && swl_gap_tot == g0 + 1u);
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED);
	RJ.escan_st = 0;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 2000u) == 0 && SC.state == SWL25_SC_RUN);

	/* S6: a SOFT stop inside the escan ioctl: the flag is cleared (the data path goes on), the scan is OFF for the boot with a GAP, kind FAILED; a HARD stop (x.stopped) is the bus's: -1, nothing cleared; a stop raised by the DRAIN is the data path's: not cleared,
	 * no command, the request stays pending until swl25_sc_abort answers it */
	sc_world(1);
	RJ.stop_at = "escan";
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == 0);
	RJ.stop_at = NULL;
	CHECK(x.d4dstop == 0u && x.stopped == 0u && x.krefresh == 0u && SC.off == 1u && SC.state == SWL25_SC_IDLE && x.sc_on == 0u && SC.n_failed == 1u && has("failed): soft stop inside the escan ioctl; the scan is off for this boot;"));
	CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENXIO);
	sc_world(1);
	RJ.stop_at = "escan";
	RJ.stop_hard = 1u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == -1 && x.stopped == 1u && x.d4dstop == 1u && SC.off == 0u && SC.state == SWL25_SC_RUN);       /* the keep loop ends on it */
	RJ.stop_at = NULL; RJ.stop_hard = 0u;
	sc_world(1);
	g1[(D2HW - G1PA) / 4u] = (x.d4rd % 64u + 1u) % 64u;       /* ring 2 busy: one item pending, all zero = a failed epoch/xor check = a soft stop raised by the drain */
	nRC = 0u;
	CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == 0 && nRC == 0u && x.d4dstop == 1u && x.stopped == 0u && SC.req == 1u && SC.state == SWL25_SC_IDLE && SC.off == 0u);
	swl25_sc_abort(&x, &SC, 1100u, "the keep thread ended");
	CHECK(SC.off == 1u && SC.req == 0u && SC.n_failed == 1u && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && has("failed): the keep thread ended;"));
	CHECK(SC.t_next == 0u && SC.t_defer == 0u && !has("ring 2 not idle"));       /* the drain's stop was taken as a stop, not as a busy ring */
	g1[(D2HW - G1PA) / 4u] = 0u;
	x.d4dstop = 0u;

	/* S7: abort (the engine ends): a running scan is answered (kind FAILED, what was collected, GAP), a pending request too, an idle scan only goes off; later requests: ENXIO */
	sc_world(1);
	sc_start(1000u, 3u);
	x.sc_ev = 3u; x.sc_part = 3u;
	g0 = (unsigned)swl_gap_tot;
	swl25_sc_abort(&x, &SC, 1400u, "the keep thread ended");
	CHECK(SC.off == 1u && SC.state == SWL25_SC_IDLE && x.sc_on == 0u && swl_gap_tot == g0 + 1u && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && ms == 400u && ev == 3u && part == 3u);
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENXIO);
	sc_world(1);
	CHECK(swl25_sc_request(&SC, &x) == 0);
	swl25_sc_abort(&x, &SC, 100u, "the keep thread ended");
	CHECK(SC.off == 1u && SC.req == 0u && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && ms == 0u);
	sc_world(1);
	g0 = (unsigned)swl_gap_tot;
	swl25_sc_abort(&x, &SC, 100u, "the keep thread ended");
	CHECK(SC.off == 1u && swl_gap_tot == g0 && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 0);       /* nothing outstanding: no GAP, no result */

	/* S9 (review 0292 F5): a firmware end status of our sync id other than SUCCESS (the collector leaves sc_termd / sc_term) ends the scan at once, kind FAILED, a GAP naming the status, what was collected is delivered */
	{
		static const uint32_t sts[] = { 1u, 2u, 4u, 9u, 10u, 12u, 13u };
		unsigned k;
		char l[300];

		for (k = 0; k < sizeof sts / sizeof sts[0]; k++) {
			sc_world(1);
			sc_start(1000u, 3u);
			x.sc_ev = 3u; x.sc_part = 2u; x.sc_termd = 1u; x.sc_term = sts[k];
			nRC = 0u;
			g0 = (unsigned)swl_gap_tot;
			CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && nRC == 0u);
			snprintf(l, sizeof l, "WIFI GAP: keep scan not complete (failed): the firmware ended the scan with status %u (abort 4, new scan 9, new assoc 10, ...), not SUCCESS; 3 events of ours (2 partial), 100 ms; what was collected is delivered\n", (unsigned)sts[k]);
			CHECK(SC.state == SWL25_SC_IDLE && swl25_sc_idle(&SC) && SC.n_failed == 1u && x.sc_on == 0u && swl_gap_tot == g0 + 1u && has(l));
			CHECK(swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1 && kind == SWL25_SCR_FAILED && ms == 100u && ev == 3u && part == 2u);
		}
		/* a next scan does not inherit the end status */
		sc_world(1);
		sc_start(1000u, 3u);
		x.sc_termd = 1u; x.sc_term = 4u;
		CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && swl25_sc_take(&SC, &kind, &ms, &ev, &part) == 1);
		CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 2000u) == 0 && x.sc_termd == 0u && x.sc_term == 0u && SC.state == SWL25_SC_RUN && swl25_sc_step(&x, &SC, 2100u) == 0 && SC.state == SWL25_SC_RUN);
	}
	/* S10 (review 0292 F2): one sync id per scan, 0x1235 .. 0x1333 (never the boot scan's 0x1234), wrapping after 255 scans; the foreign-event count of a scan is reset at its start and printed in its end line (F1) */
	{
		static const struct { uint32_t before, sync; } ids[] = { { 0u, 0x1235u }, { 1u, 0x1236u }, { 254u, 0x1333u }, { 255u, 0x1235u }, { 256u, 0x1236u } };
		unsigned k;

		for (k = 0; k < sizeof ids / sizeof ids[0]; k++) {
			sc_world(1);
			SC.n_start = ids[k].before;
			x.sc_foreign = 9u;
			x.sc_await = 1u;       /* stale: a new start clears it */
			nRC = 0u;
			CHECK(swl25_sc_request(&SC, &x) == 0 && swl25_sc_step(&x, &SC, 1000u) == 0 && nRC == 1u);
			sc_expect(e, 3u, ids[k].sync);
			CHECK(memcmp(RC[0].arg, e, 96) == 0 && x.escan_sync == ids[k].sync && x.sc_foreign == 0u && x.sc_await == 0u);
		}
		sc_world(1);
		sc_start(1000u, 3u);
		x.sc_foreign = 70u; x.sc_ev = 1u; x.sc_part = 1u; x.sc_complete = 1u;
		CHECK(swl25_sc_step(&x, &SC, 1100u) == 0 && has("SWEET WLAN: keep scan end: complete, 1 events of ours (1 partial), 70 of another sync id, 100 ms\n"));
	}
	/* S11 (review 0292 F3): a request that sees the scan off after storing req returns ENXIO and leaves req set for the abort; the abort answers a request it sees (tested above) */
	sc_world(1);
	SC.off = 1u;
	CHECK(swl25_sc_request(&SC, &x) == (int)SWL25_SC_ENXIO && SC.req == 0u);

	/* S8: no SSID, BSSID or address in any line of a whole scan (the log carries counts only) */
	sc_world(1);
	sc_start(1000u, 3u);
	x.sc_ev = 3u; x.sc_part = 2u; x.sc_complete = 1u;
	CHECK(swl25_sc_step(&x, &SC, 1100u) == 0);
	CHECK(!log_has_bytes(V_AA, 6) && !log_has_bytes(V_SPA, 6) && !log_has_bytes(GWM, 6) && strstr(logbuf, V_SSID) == NULL);
	rj_on = 0;
}
