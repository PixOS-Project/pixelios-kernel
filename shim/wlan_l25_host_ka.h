/* wlan_l25_host_ka.h - host cases of ALL-ON T-32 (the firmware NULL-frame keep-alive, swl25_ka_*), included by wlan_l25_host_test.c after wlan_l25_host_sc.h. Same recording stub (rj_stub records SET_VAR "mkeep_alive", answers RJ.ka_st)
 * and the same fake ring 2. Cases: K1 boot-arg (absent, 1, 0, other); K2 one send when due: the ONE command, its bytes, flags, success line; K3 not before the link is UP / re-join idle / scan idle, not twice, armed again after a re-join UP;
 * K4 the firmware refuses (status N): GAP line, nothing else changes; K5 soft and hard stop in the ioctl; K6 ring 2 busy defers; K7 off sends nothing; K8 no SSID, BSSID or address in the log. No hardware. */
static struct swl25_ka KA;
static void ka_world(void)
{
	sc_world(1);        /* kept engine, stub on, link UP, re-join machine idle in UP */
	memset(&KA, 0, sizeof KA);
	loglen = 0u;
	logbuf[0] = '\0';
	ngap = 0u;
}

static void test_ka(void)
{
	static const uint8_t want[23] = { 'm','k','e','e','p','_','a','l','i','v','e', 0x00, 0x01,0x00, 0x0b,0x00, 0x30,0x75,0x00,0x00, 0x00,0x00, 0x00 };
	unsigned g0;

	rj_on = 1;
	/* K1: the boot-arg sweetwlanka */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1 && KA.due == 1u && KA.off == 0u && ngap == 0u && !has("keep-alive"));       /* absent: on, silent */
	CHECK(swl25_ka_init(&KA, "1") == 1 && KA.due == 1u && KA.off == 0u && ngap == 0u && !has("keep-alive"));       /* =1: on, silent */
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_ka_init(&KA, "0") == 0 && KA.due == 0u && KA.off == 1u && swl_gap_tot == g0 + 1u && has("SWEET WLAN: GAP keep-alive off (sweetwlanka=0)\n"));
	loglen = 0u; logbuf[0] = '\0';
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_ka_init(&KA, "7") == 1 && KA.due == 1u && KA.off == 0u && swl_gap_tot == g0 + 1u && has("SWEET WLAN: GAP keep-alive sweetwlanka=\"7\" ignored (only \"0\" or \"1\" are valid), keep-alive stays on\n"));
	{       /* F4: only the exact strings "0" and "1" count; the raw string is named in the GAP */
		static const char *const bad[] = { "", "on", "00", "01", "0x1", " 1", "10", "-1" };
		unsigned bi;

		for (bi = 0; bi < sizeof bad / sizeof bad[0]; bi++) {
			char want[160];

			loglen = 0u; logbuf[0] = '\0';
			g0 = (unsigned)swl_gap_tot;
			CHECK(swl25_ka_init(&KA, bad[bi]) == 1 && KA.due == 1u && KA.off == 0u && swl_gap_tot == g0 + 1u);
			snprintf(want, sizeof want, "SWEET WLAN: GAP keep-alive sweetwlanka=\"%s\" ignored", bad[bi]);
			CHECK(has(want) && !has("keep-alive off"));
		}
	}
	/* K7: off sends nothing, even armed */
	ka_world();
	CHECK(swl25_ka_init(&KA, "0") == 0);
	swl25_ka_arm(&KA);
	nRC = 0u;
	CHECK(KA.due == 0u && swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 0u);
	KA.due = 1u;        /* even forced due, off wins (defence in depth) */
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 0u);
	/* K2: due -> exactly one command with exactly the bytes, success line, flags */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	nRC = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0);
	CHECK(nRC == 1u && RC[0].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[0].name, "mkeep_alive") == 0 && RC[0].argn == 11u && RC[0].wlen == 23u && RC[0].secret == 0 && memcmp(RC[0].arg, want + 12, 11) == 0);
	CHECK(l25_last_soft == 1 && l25_last_repost == 1 && l25_last_krf == 1);
	CHECK(KA.due == 0u && KA.n_set == 1u && KA.n_fail == 0u && x.krefresh == 0u && !x.d4dstop && !x.stopped && swl_gap_tot == g0);
	CHECK(has("SWEET WLAN: keep-alive set period 30000 ms id 0 (null frame)\n") && rj_count_of("keep-alive set") == 1u && !has("GAP keep-alive"));
	CHECK(swl25_ka_step(&x, &KA, 2000u, 0) == 0 && nRC == 1u);       /* once per arm */
	/* K3: re-armed after a re-join UP; waits while the link is DOWN, the re-join is not idle, or a scan runs */
	swl25_ka_arm(&KA);
	CHECK(KA.due == 1u && KA.n_arm == 2u);
	x.klink = 1u;
	CHECK(swl25_ka_step(&x, &KA, 3000u, 0) == 0 && nRC == 1u && KA.due == 1u);
	x.klink = 0u; x.rj_state = SWG_RJ_KEYING;
	CHECK(swl25_ka_step(&x, &KA, 3000u, 0) == 0 && nRC == 1u && KA.due == 1u);
	x.rj_state = SWG_RJ_UP;
	CHECK(swl25_ka_step(&x, &KA, 3000u, 1) == 0 && nRC == 1u && KA.due == 1u);       /* a scan holds the engine */
	CHECK(swl25_ka_step(&x, &KA, 3000u, 0) == 0 && nRC == 2u && KA.n_set == 2u && KA.due == 0u);
	/* K4: the firmware refuses: one GAP line with the status, the link/re-join state is untouched, no retry */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	RJ.ka_st = -23;
	nRC = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 1u);
	CHECK(has("SWEET WLAN: GAP keep-alive mkeep_alive rejected status -23\n") && !has("keep-alive set") && swl_gap_tot == g0 + 1u && KA.n_fail == 1u && KA.n_set == 0u && KA.due == 0u && KA.off == 0u);
	CHECK(x.klink == 0u && x.rj_state == SWG_RJ_UP && !x.d4dstop && !x.stopped && x.rj_off == 0u && x.krefresh == 0u);
	CHECK(swl25_ka_step(&x, &KA, 2000u, 0) == 0 && nRC == 1u);
	swl25_ka_arm(&KA);       /* the next rejoin UP tries again */
	RJ.ka_st = 0;
	CHECK(swl25_ka_step(&x, &KA, 3000u, 0) == 0 && nRC == 2u && KA.n_set == 1u && has("keep-alive set period 30000 ms id 0"));
	/* K5: a soft stop inside the ioctl is cleared and turns the keep-alive off with a GAP; a hard stop is returned */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	RJ.stop_at = "mkeep_alive"; RJ.stop_hard = 0u;
	g0 = (unsigned)swl_gap_tot;
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && x.d4dstop == 0u && !x.stopped && KA.off == 1u && KA.n_fail == 1u && has("SWEET WLAN: GAP keep-alive off: soft stop inside the mkeep_alive ioctl (no completion") && swl_gap_tot == g0 + 1u && !has("keep-alive set"));
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	RJ.stop_at = "mkeep_alive"; RJ.stop_hard = 1u;
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) < 0 && x.stopped == 1u && !has("keep-alive set"));
	x.stopped = 0u; x.d4dstop = 0u;
	/* K6: ring 2 not idle defers the send by SWL25_SC_DEFER_MS (one line), then it goes */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	nRC = 0u;
	{
		const __typeof__(x.in.idx[0]) save = x.in.idx[SWG_IDX(SWD_IDX_D2H_W)];

		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;       /* the D2H write index cannot be read: ring 2 counts as not idle */
		CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 0u && KA.due == 1u && KA.t_next == 1000u + SWL25_SC_DEFER_MS && has("SWEET WLAN: keep-alive: ring 2 not idle, the send waits 100 ms (not counted)\n"));
		CHECK(swl25_ka_step(&x, &KA, 1000u + SWL25_SC_DEFER_MS - 1u, 0) == 0 && nRC == 0u && rj_count_of("keep-alive: ring 2 not idle") == 1u);
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
		CHECK(swl25_ka_step(&x, &KA, 1000u + SWL25_SC_DEFER_MS, 0) == 0 && nRC == 1u && KA.n_set == 1u && KA.defer_gap == 0u);
	}
	/* F2a: a pending ring-2 item is drained first (consumed, counted), ring 2 is then idle and the send happens in the SAME call */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	{
		const uint32_t k = x.d4rd, n0 = x.kr2n;
		uint32_t *r = &g1[(RING2 - G1PA) / 4u + 6u * (k % 64u)];

		memset(r, 0, 24);
		r[0] = (((254u + k) % 253u) << 24) | SWD4B_MSG_D2H_MBDATA;       /* a mailbox word 0: valid epoch/xor, consumed with a GAP */
		r[5] = r[0] ^ r[1] ^ r[2] ^ r[3] ^ r[4];
		g1[(D2HW - G1PA) / 4u] = (k + 1u) % 64u;
		nRC = 0u;
		CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && x.kr2n == n0 + 1u && x.d4rd == k + 1u && nRC == 1u && KA.n_set == 1u && !has("keep-alive: ring 2 not idle"));
	}
	/* F2b: re-arming clears a pending back-off (t_next) and the defer clock */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	KA.due = 0u; KA.t_next = 5000u; KA.t_defer = 4000u; KA.defer_gap = 1u;
	swl25_ka_arm(&KA);
	nRC = 0u;
	CHECK(KA.due == 1u && KA.t_next == 0u && KA.t_defer == 0u && KA.defer_gap == 0u && swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 1u);
	/* F2c: a stop raised by the drain (a bad ring-2 item) is the data path's: NOT cleared, nothing sent, no busy-ring deferral */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	g1[(D2HW - G1PA) / 4u] = (x.d4rd % 64u + 1u) % 64u;       /* one item pending, all zero = a failed epoch/xor check = a soft stop raised by the drain */
	nRC = 0u;
	CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && nRC == 0u && x.d4dstop == 1u && x.stopped == 0u && KA.due == 1u && KA.t_next == 0u && KA.t_defer == 0u && !has("keep-alive: ring 2 not idle"));
	g1[(D2HW - G1PA) / 4u] = 0u;
	x.d4dstop = 0u;
	/* F4c: a ring 2 that never goes idle ends the wait with ONE GAP after SWL25_SC_BUDGET_MS, nothing sent, not retried until re-armed */
	ka_world();
	CHECK(swl25_ka_init(&KA, NULL) == 1);
	{
		const __typeof__(x.in.idx[0]) save = x.in.idx[SWG_IDX(SWD_IDX_D2H_W)];

		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = 0u;
		nRC = 0u;
		g0 = (unsigned)swl_gap_tot;
		CHECK(swl25_ka_step(&x, &KA, 1000u, 0) == 0 && KA.t_defer == 1000u && KA.due == 1u);
		KA.t_next = 0u;       /* the passes of the real loop come every ms; the 100 ms back-off is not what is tested here */
		CHECK(swl25_ka_step(&x, &KA, 1000u + SWL25_SC_BUDGET_MS - 1u, 0) == 0 && KA.due == 1u && KA.n_fail == 0u && swl_gap_tot == g0 && !has("never idle"));       /* one ms before the cap */
		KA.t_next = 0u;
		CHECK(swl25_ka_step(&x, &KA, 1000u + SWL25_SC_BUDGET_MS, 0) == 0 && KA.due == 0u && KA.n_fail == 1u && nRC == 0u && swl_gap_tot == g0 + 1u && has("SWEET WLAN: GAP keep-alive not sent: ring 2 never idle in 12000 ms\n"));
		x.in.idx[SWG_IDX(SWD_IDX_D2H_W)] = save;
		CHECK(swl25_ka_step(&x, &KA, 20000u, 0) == 0 && nRC == 0u);       /* due is 0: no retry */
		swl25_ka_arm(&KA);
		CHECK(swl25_ka_step(&x, &KA, 30000u, 0) == 0 && nRC == 1u && KA.n_set == 1u);
	}
	/* K8: nothing private in the log */
	CHECK(!log_has_bytes(V_AA, 6) && !log_has_bytes(V_SPA, 6) && !log_has_bytes(GWM, 6) && strstr(logbuf, V_SSID) == NULL);
	rj_on = 0;
}
