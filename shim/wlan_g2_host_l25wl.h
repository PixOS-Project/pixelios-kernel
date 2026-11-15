/*
 * The real ioctl stream of L25 judged by the store whitelist (T-5173 r1, gap 2 of T-d485; docs/research/T-3e93-l25-wiring-design.md s.2 item 4): the real swg_e_call / swg_e_call_secret against the fake dongle
 * of this test, on the real G1 plan, with the arguments of the five calls of sweet_wlan_l25_hs.h (cur_etheraddr, wsec_key twice, GET wsec, SET wsec). b_wr judges every store before it is made.
 * Demanded: no store refused, classes L2 L6 L7 L8 L9 seen, ring 2 consumed in a batch at least once, nothing else judged; an iovar outside the four is refused at its commit. No hardware.
 */
static unsigned lw_n, lw_ref, lw_cls[SWL25W_NCLASS + 1], lw_nf;
static struct swl25w lw;
static char lw_first[4][170];

static int
lw_rd(void *ctx, int space, uint32_t off, uint32_t *v)
{
	(void)ctx;
	if (space != SWL25W_SP_HOST || off >= HBW * 4u || (off & 3u) != 0u) {
		return -1;
	}
	*v = f.hb[off / 4u];
	return 0;
}

static void
lw_judge(int sp, uint32_t a, uint32_t v)
{
	const char *why = "";
	const int space = sp == SWG_SP_PCIE ? SWL25W_SP_PCIE : sp == SWG_SP_HOST ? SWL25W_SP_HOST : 0;
	const uint32_t old = space == SWL25W_SP_HOST && a < HBW * 4u ? f.hb[a / 4u] : 0u;
	const int c = swl25w_ok(&lw, space, a, v, old, &why);

	lw_n++;
	if (c < 0) {
		lw_ref++;
		if (lw_nf < 4u) {
			snprintf(lw_first[lw_nf++], sizeof lw_first[0], "sp %d off %#x v %#x old %#x: %s", sp, a, v, old, why);
		}
		return;
	}
	lw_cls[c]++;
	swl25w_done(&lw, space, a, v, old, c);
}

static void
lw_tests(struct swd_plan *p)
{
	struct swg_wx x;
	struct swd4b_ioctl_d2h cm;
	static uint8_t key[164];
	uint8_t rsp[8];
	unsigned i;
	int rc;

	memset(&lw, 0, sizeof lw);
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "l25wl");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u);
	lw.h2d_w = p->b[SWD_IDX_H2D_W].off; lw.d2h_w = p->b[SWD_IDX_D2H_W].off; lw.d2h_r = p->b[SWD_IDX_D2H_R].off; lw.h2d_r = p->b[SWD_IDX_H2D_R].off;
	for (i = 0; i < 5u; i++) {
		lw.ring[i] = p->b[i].off;
		lw.depth[i] = swd_rings[i].items;
	}
	lw.ioctbuf = p->b[SWD_IOCTBUF].off; lw.respbuf = p->b[SWD_RESPBUF].off; lw.evbuf = p->b[SWD_EVBUF].off;
	lw.g1_pa = PA; lw.g1_len = p->total;
	lw.data_pa = (PA + p->total + 0xFFFull) & ~0xFFFull; lw.data_len = 0x14000u;     /* no DATA store in this stream: only its place is judged */
	lw.flowid = 2u;
	lw.rd = lw_rd;
	CHECK(swl25w_layout_ok(&lw));   /* the real plan passes the layout check */
	memset(key, 0x5A, sizeof key);
	memset(&cm, 0, sizeof cm);
	gd.on = 1;
	lw_on = 1u;
	rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR cur_etheraddr", "cur_etheraddr", NULL, 6u, 14u + 6u, 1, 6u, rsp, 1, &cm, NULL);
	CHECK(rc == 0 && !x.d4dstop);
	for (i = 0; i < 2u; i++) {
		rc = swg_e_call_secret(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec_key", "wsec_key", key, 164u, 9u + 164u, 1, 0u, NULL, 1, &cm);
		CHECK(rc == 0 && !x.d4dstop);
	}
	rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR wsec", "wsec", NULL, 4u, 5u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	CHECK(rc == 0 && !x.d4dstop);
	{       /* T-924f: the read-only counters GET through the real transport and the judge: 1200 B in = out, a 892 B answer read back whole (the payload zeroing of the stale words is judged too) */
		static uint8_t cbuf[1204];

		memset(cbuf, 0xEE, sizeof cbuf);
		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR counters", "counters", NULL, 0u, 1200u, 1, 1200u, cbuf, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0 && cm.resp_len == 880u && cbuf[0] == 30u && cbuf[15] == 0x03u && cbuf[16] == 7u && cbuf[880] == 0xEEu);
		memset(&cm, 0, sizeof cm);
	}
	{       /* T-9d71: the two read-only association-request GETs through the real transport and the judge: 64 B and 512 B in = out (rows of swl25w_iovars) */
		static uint8_t abuf[516];

		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR assoc_info", "assoc_info", NULL, 0u, 64u, 1, 64u, abuf, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR assoc_req_ies", "assoc_req_ies", NULL, 0u, 512u, 1, 512u, abuf, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		memset(&cm, 0, sizeof cm);
	}
	rsp[0] = 4u; rsp[1] = rsp[2] = rsp[3] = 0u;
	rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec", "wsec", rsp, 4u, 5u + 4u, 1, 0u, NULL, 1, &cm, NULL);
	CHECK(rc == 0 && !x.d4dstop);
	CHECK(lw_ref == 0u && lw_n > 100u && lw_cls[SWL25W_L2] >= 50u && lw_cls[SWL25W_L6] >= 10u && lw_cls[SWL25W_L7] >= 5u && lw_cls[SWL25W_L8] >= 5u && lw_cls[SWL25W_L9] >= 10u);
	CHECK(lw_cls[SWL25W_L1] == 0u && lw_cls[SWL25W_L3] == 0u && lw_cls[SWL25W_L4] == 0u && lw_cls[SWL25W_L5] == 0u && !lw.ioct_busy && lw.pend_db == 0u);
	printf("L25WL real ioctl stream: stores=%u refused=%u L2=%u L6=%u L7=%u L8=%u L9=%u\n", lw_n, lw_ref, lw_cls[SWL25W_L2], lw_cls[SWL25W_L6], lw_cls[SWL25W_L7], lw_cls[SWL25W_L8], lw_cls[SWL25W_L9]);
	for (i = 0; i < lw_nf; i++) {
		fprintf(stderr, "L25WL refused: %s\n", lw_first[i]);
	}
	/* patch 0281 (W-D6 boot A): the keep phase decodes WL_EVENT items and re-posts their buffers; every store goes through the SAME L25 whitelist (L2 buffer post, L7 index, L9 doorbell), none refused.
	 * Sequence: LINK flags 0 (DOWN), SET_SSID status 0 (a link-up event while DOWN), DEAUTH_IND (DOWN already: no second line), then an unrelated event type. */
	{
		const unsigned l2 = lw_cls[SWL25W_L2], l7 = lw_cls[SWL25W_L7], l9 = lw_cls[SWL25W_L9], rp0 = x.kev_rp;
		const uint32_t evq0 = x.evq;

		x.kept = 1;
		x.klink = 0u;
		ge.eflags = 0u;
		ge_event(16u, 0u, NULL, 0u);
		ge.eflags = 1u;
		ge_event(0u, 0u, NULL, 0u);
		ge.eflags = 0u;
		ge_event(6u, 0u, NULL, 0u);
		ge_event(56u, 0u, NULL, 0u);
		loglen = 0;
		rc = swg_keep_r2(&x);
		logbuf[loglen] = 0;
		CHECK(rc == 0 && !x.d4dstop && !x.stopped && lw_ref == 0u && x.kev_n == 4u && x.kevq == 0u && x.evq == 0u && x.kev_rp == rp0 + 4u);
		CHECK(has("SWEET WLAN: keep event type 16 flags 0x0 status 0 reason 0 auth 0 datalen 0 item_len 72\n") && has("SWEET WLAN: keep event type 0 flags 0x1 status 0 reason 0 auth 0 datalen 0 item_len 72\n") &&
		      has("SWEET WLAN: keep event type 6 flags 0x0 status 0 reason 0 auth 0 datalen 0 item_len 72\n") && has("SWEET WLAN: keep event type 56 flags 0x0"));
		CHECK(has("SWEET WLAN: keep link DOWN cause 16 status 0 reason 0\n") && has("WIFI GAP: keep link UP event outside a re-join attempt ignored (type 0)\n") && x.rj_state == SWG_RJ_DOWN && x.klink == 1u && x.kl_new == 1u && x.kl_up_n == 1u && x.kl_cause == 16u);
		CHECK(!has("WIFI GAP: keep event") && strstr(logbuf, "keep link DOWN cause") != NULL && strstr(strstr(logbuf, "keep link DOWN cause") + 1, "keep link DOWN cause") == NULL);       /* DOWN exactly once */
		CHECK(lw_cls[SWL25W_L2] >= l2 + 4u + evq0 && lw_cls[SWL25W_L7] >= l7 + 1u && lw_cls[SWL25W_L9] >= l9 + 1u && lw.pend_db == 0u && ge.badpost == 0u);       /* the re-posts were judged as buffer posts, index stores and doorbells */
		for (i = 0; i < SWG_E_EV_BUFS; i++) {
			unsigned j;

			CHECK(x.epid[i] != 0u);       /* every event buffer is posted again */
			for (j = i + 1u; j < SWG_E_EV_BUFS; j++) {
				CHECK(x.epid[i] != x.epid[j]);       /* ... under a pktid of its own: the dongle's answer names the buffer by it */
			}
		}
		printf("L25WL keep event re-posts: decoded=%u reposted=%u (boot events re-posted late: %u) L2 +%u L7 +%u L9 +%u refused=%u\n", x.kev_n, x.kev_rp - rp0, evq0, lw_cls[SWL25W_L2] - l2, lw_cls[SWL25W_L7] - l7, lw_cls[SWL25W_L9] - l9, lw_ref);
	}
	/* patch 0281: an event that arrives INSIDE a keep ioctl (the RSSI refresh is such a call) goes to the keep decode, not to the L23 scan path: no evq / evn / emax, the buffer queued and re-posted by the next keep pass, every store judged */
	{
		const unsigned k0 = x.kev_n, rp0 = x.kev_rp, r2ev0 = x.kr2ev;
		const uint32_t evn0 = x.evn, evq0 = x.evq;

		x.kept = 1;
		x.klink = 0u;
		x.kl_new = 0u;
		ge.einj[0].n = gd.ndb + 1u; ge.einj[0].type = 5u; ge.einj[0].status = 0u; ge.einj[0].pre = 1;       /* DEAUTH, delivered before the ack of the next call */
		ge.neinj = 1u;
		loglen = 0;
		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR wsec", "wsec", NULL, 4u, 5u + 4u, 1, 4u, rsp, 1, &cm, NULL);
		ge.neinj = 0u;
		logbuf[loglen] = 0;
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0 && x.kev_n == k0 + 1u && x.kevq == 1u && x.evn == evn0 && x.evq == evq0 && x.klink == 1u && x.kl_new == 1u && x.kev_rp == rp0 && x.kr2ev == r2ev0 + 1u && lw_ref == 0u);
		CHECK(has("SWEET WLAN: keep event type 5 flags 0x") && has("SWEET WLAN: keep link DOWN cause 5 status 0 reason 0\n") && !has("event item"));
		rc = swg_keep_r2(&x);
		CHECK(rc == 0 && !x.d4dstop && x.kevq == 0u && x.kev_rp == rp0 + 1u && lw_ref == 0u && lw.pend_db == 0u && ge.badpost == 0u);
	}
	/* review 0281 F5: a boot event buffer whose re-post is still deferred (evq) goes back at the start of the next keep pass, judged like the others (kills: swg_ke_flush / swg_keep_r2 not flushing evq) */
	{
		const unsigned rp0 = x.kev_rp, l2 = lw_cls[SWL25W_L2];
		const uint32_t ws = fplan->b[SWD_IDX_D2H_W].off;
		uint32_t sl;

		x.kept = 1;
		ge.evlifo = 0;       /* the dongle takes the oldest posted buffer: the one this loop finds */
		for (sl = 0; sl < SWG_E_EV_BUFS && x.epid[sl] != ge.evid[ge.evh]; sl++) {
		}
		CHECK(x.kevq == 0u && sl < SWG_E_EV_BUFS);       /* the buffer the dongle uses next */
		ge.eflags = 0u;
		ge_event(56u, 0u, NULL, 0u);       /* the dongle consumes it (its pool is right for the re-post) ... */
		gd.w--;
		f.hb[ws / 4u] = gd.w % 64u;        /* ... but the host never sees the ring-2 item: only the deferred re-post (evq) is left */
		x.epid[sl] = 0u;
		x.evq = 1u;
		x.evqb[0] = sl;
		rc = swg_keep_r2(&x);
		CHECK(rc == 0 && !x.d4dstop && x.evq == 0u && x.epid[sl] != 0u && lw_cls[SWL25W_L2] == l2 + 10u && x.kev_rp == rp0 && lw_ref == 0u && lw.pend_db == 0u && ge.badpost == 0u);
	}
	/* review 0281 F1: swg_rssi_drain (swl25_rssi_drain) with three events pending on ring 2: it runs a keep pass (decode, consume, re-post), leaves ring 2 idle and returns 0 so the refresh runs (kills: no pass at all, always "skip") */
	{
		const unsigned k0 = x.kev_n, rp0 = x.kev_rp, r2n0 = x.kr2n;
		const uint32_t ws = fplan->b[SWD_IDX_D2H_W].off;

		x.kept = 1;
		ge.eflags = 0u;
		ge_event(56u, 0u, NULL, 0u);
		ge_event(56u, 0u, NULL, 0u);
		ge_event(56u, 0u, NULL, 0u);
		CHECK((f.hb[ws / 4u] & 0xFFFFu) != x.d4rd % SWG_R2_DEPTH);       /* ring 2 is busy */
		rc = swg_rssi_drain(&x);
		CHECK(rc == 0 && !x.d4dstop && !x.stopped && x.kev_n == k0 + 3u && x.kev_rp == rp0 + 3u && x.kr2n == r2n0 + 3u && x.kevq == 0u);
		CHECK((f.hb[ws / 4u] & 0xFFFFu) == x.d4rd % SWG_R2_DEPTH && lw_ref == 0u && lw.pend_db == 0u && ge.badpost == 0u);       /* ring 2 idle again, every store judged */
		rc = swg_rssi_drain(&x);       /* idle ring: nothing to do, 0, no pass */
		CHECK(rc == 0 && x.kr2n == r2n0 + 3u);
	}
	/* patch 0288: the WLC_GET_BSS_INFO GET (cmd 136, empty name, 1024 B = the length word 1024 and zeros) through the real transport and the real judge: accepted, nothing refused, the dongle answers status 0 */
	{
		static const uint8_t lenw[3] = { 0x04u, 0x00u, 0x00u };
		static uint8_t bibuf[1028];
		const unsigned l2b = lw_cls[SWL25W_L2];

		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, 136u, "WLC_GET_BSS_INFO", "", lenw, 3u, 1024u, 1, 1024u, bibuf, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0 && lw_ref == 0u && lw_cls[SWL25W_L2] > l2b && !lw.ioct_busy && lw.pend_db == 0u);
	}
	/* patch 0282 (W-D6 boot B): the re-join's ioctls through the real transport and the real judge: GET wpa_auth / auth / mfp (13, 9, 8 B), SET wpa_auth 0x80 / auth 0 / mfp 0, and the live SET join (81 B, the v1 payload with the wildcard BSSID,
	 * sent on the secret path). The dongle answers the join with SET_SSID 0 + LINK up INSIDE the ioctl: with the machine JOINING the keep branch records both (rj_ssid, rj_link) and no GAP is raised for them. Every store judged, none refused. */
	{
		static const uint8_t any[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
		uint8_t jp[SWJ_EXT_JOIN_V1_SIZE], v4[4] = { 0x80u, 0u, 0u, 0u };
		const unsigned k0 = x.kev_n, l2 = lw_cls[SWL25W_L2];
		const uint32_t jn0 = ge.jn;

		CHECK(swj_join_ext_v1(jp, (const uint8_t *)"net", 3u, any) == 0);
		x.kept = 1;
		x.klink = 1u;
		x.rj_state = SWG_RJ_JOINING;
		x.rj_ssid = x.rj_link = 0u;
		x.krefresh = 1u;
		memset(&cm, 0, sizeof cm);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR wpa_auth", "wpa_auth", NULL, 4u, 9u + 4u, 1, 4u, rsp, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR auth", "auth", NULL, 4u, 5u + 4u, 1, 4u, rsp, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		rc = swg_e_call(&x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR mfp", "mfp", NULL, 4u, 4u + 4u, 1, 4u, rsp, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wpa_auth", "wpa_auth", v4, 4u, 9u + 4u, 1, 0u, NULL, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		v4[0] = 0u;
		rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR auth", "auth", v4, 4u, 5u + 4u, 1, 0u, NULL, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR mfp", "mfp", v4, 4u, 4u + 4u, 1, 0u, NULL, 1, &cm, NULL);
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0);
		CHECK(lw_ref == 0u);
		ge.jmode = 0;
		memset(swg_e_b, 0xFF, sizeof swg_e_b);       /* a stale shadow: the wipe covers all of it, not only the payload's bytes */
		rc = swg_e_call_secret(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR join", "join", jp, SWJ_EXT_JOIN_V1_SIZE, 5u + SWJ_EXT_JOIN_V1_SIZE, 1, 0u, NULL, 1, &cm);
		x.krefresh = 0u;
		CHECK(rc == 0 && !x.d4dstop && cm.status == 0 && lw_ref == 0u && ge.jn == jn0 + 4u);
		{       /* review 0282 F5: the payload shadow of the secret call (the join SSID) is wiped on the way out, and the buffer scrub is not pending */
			unsigned bi, nz = 0u;

			for (bi = 0; bi < sizeof swg_e_b; bi++) {
				nz += swg_e_b[bi] != 0u;
			}
			CHECK(nz == 0u && x.esec_left == 0u && x.esec_done == 1u);
		}
		{       /* review 0282 F5: the scrub of the payload buffer after a soft stop (swg_e_scrub): a left secret word is zeroed in the buffer by the plain free-payload store once the completion is consumed, never before, never on a stopped engine */
			const unsigned stores0 = lw_n;

			x.esec_left = 1u; x.esec_done = 0u; x.epay[0] = 0x41414141u;
			CHECK(swg_e_scrub(&x) == 0 && x.esec_left == 1u && x.epay[0] == 0x41414141u && lw_n == stores0);       /* ioctl may be in transit: no store */
			x.esec_done = 1u; x.d4dstop = 1u;
			CHECK(swg_e_scrub(&x) == 0 && x.esec_left == 1u && lw_n == stores0);       /* stopped: no store */
			x.d4dstop = 0u;
			x.epay[0] = 0u;
			CHECK(swg_e_scrub(&x) == 0 && x.esec_left == 0u && lw_n == stores0);       /* nothing left in the buffer: no store needed */
			x.esec_left = 1u; x.epay[0] = 0u;
			CHECK(lw_ref == 0u);
			{       /* a word is really there: write it through the engine first, then scrub */
				uint8_t one[4] = { 0x41u, 0x41u, 0x41u, 0x41u };

				CHECK(swg_e_pay(&x, one, 4u) == 0 && x.epay[0] == 0x41414141u && f.hb[(fplan->b[SWD_IOCTBUF].off) / 4u] == 0x41414141u);
				x.esec_left = 1u;
				loglen = 0;
				CHECK(swg_e_scrub(&x) == 0 && x.esec_left == 0u && x.epay[0] == 0u && f.hb[(fplan->b[SWD_IOCTBUF].off) / 4u] == 0u && lw_ref == 0u);
				logbuf[loglen] = 0;
				CHECK(strstr(logbuf, "41414141") == NULL && strstr(logbuf, "d4e payload word") != NULL);       /* the scrub store is made and its line withholds the value */
			}
		}       /* join + the three SETs reached the dongle (the GETs are no join-class call) */
		CHECK(x.rj_ssid == 1u && x.rj_link == 1u && x.kev_n == k0 + 2u && !has("keep link UP event outside"));
		rc = swg_keep_r2(&x);
		CHECK(rc == 0 && !x.d4dstop && x.kevq == 0u && lw_ref == 0u && lw.pend_db == 0u && !lw.ioct_busy && lw_cls[SWL25W_L2] > l2 + 6u);
		x.klink = 0u;
		x.rj_state = SWG_RJ_UP;
		x.kl_new = 0u;
		printf("L25WL rejoin ioctls: stores=%u refused=%u\n", lw_n, lw_ref);
	}
	/* patch 0292 (WIFI-COVERAGE D3/D4): the keep-phase scan through the REAL transport, the REAL judge and the fake dongle's real escan events. The START (SET_VAR escan, 86 B = "escan\0" + the 80 B of swg_e_escan_arg (chans 0)) is judged by row 19 of swl25w_iovars
	 * (swl25w_chk_escan) with no refusal; the dongle must have received exactly the typed bytes (version 3, passive, all channels); its events (npartial PARTIAL records + status 0) then arrive as ring-2 items and swg_keep_r2 -> swg_ke_event -> swg_ke_escan hands every one to
	 * the sink: the records carry version 109 and the dongle's BSSIDs, the final status 0 sets sc_complete; every re-post of the consumed event buffers is judged as before; none refused. */
	{
		uint8_t want[96], arg[96];
		unsigned it, ok_records = 0u, l2 = lw_cls[SWL25W_L2];
		uint32_t k;

		x.kept = 1; x.klink = 0u; x.rj_state = SWG_RJ_UP; x.kl_new = 0u; x.emajor = 3u;
		memset(want, 0, sizeof want);
		want[0] = 3u; want[4] = 1u; want[6] = 0x35u; want[7] = 0x12u; want[8] = 3u; want[10] = 72u;
		memset(want + 48, 0xFF, 6); want[54] = 2u; want[56] = 1u; memset(want + 60, 0xFF, 16); want[76] = 0u;
		x.escan_sync = 0x1235u;       /* review 0292 F2: the first keep scan's id; the judge (lw.escan_sync is not wired in this world: it expects the boot id) is pointed at it */
		lw.escan_sync = &x.escan_sync;
		swg_e_escan_arg(arg, x.emajor, 0, x.escan_sync);
		CHECK(memcmp(arg, want, sizeof arg) == 0);
		memset(&g_sk, 0, sizeof g_sk);
		x.scansink = g_sink; x.scancx = NULL;
		x.sc_on = 1u; x.sc_complete = 0u; x.sc_ev = x.sc_part = x.sc_other = 0u;
		ge.npartial = 5u;
		ge.pfin = 0;
		ge.fault = EF_NONE;
		ge.nescan = 0u;
		x.krefresh = 1u;
		memset(&cm, 0, sizeof cm);
		loglen = 0;
		rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR escan", "escan", arg, 80u, 6u + 80u, 1, 0u, NULL, 1, &cm, NULL);
		x.krefresh = 0u;
		CHECK(rc == 0 && !x.d4dstop && !x.stopped && cm.status == 0 && lw_ref == 0u && ge.nescan == 1u && ge.escin == 86u && memcmp(ge.esc, want, 80) == 0 && !lw.ioct_busy);
		CHECK(x.kevq == 6u && x.sc_ev == 6u);       /* the dongle delivered the whole scan INSIDE the START's own wait: the keep branch of swg_e_items collected it, the six buffers wait for their re-post */
		for (it = 0; it < 4u && (it == 0u || x.kevq != 0u || !x.sc_complete) && !x.d4dstop; it++) {
			CHECK(swg_keep_r2(&x) == 0);
		}
		CHECK(!x.d4dstop && !x.stopped && lw_ref == 0u && lw.pend_db == 0u && ge.badpost == 0u && x.kevq == 0u);
		CHECK(x.sc_complete == 1u && x.sc_ev == 6u && x.sc_part == 5u && x.sc_other == 0u && g_sk.n == 6u);
		for (k = 0; k < g_sk.n && k < 8u; k++) {
			if (g_sk.st[k] == 8u && g_sk.len[k] == 12u + 96u && swd4b_get32(g_sk.b[k] + 12) == 109u && g_sk.b[k][12 + 8] == 0x02u && g_sk.b[k][12 + 9] == 0x11u && swg_e_le16(g_sk.b[k] + 8) == x.escan_sync) {
				ok_records++;
			}
		}
		CHECK(ok_records == 5u && g_sk.st[5] == 0u && g_sk.len[5] == 12u);       /* five PARTIAL records (version 109, the dongle's BSSIDs), then status 0 */
		CHECK(lw_cls[SWL25W_L2] > l2 && !has("net0") && !has("02:11:22"));       /* the re-posts were judged; no SSID or address in the log */
		x.sc_on = 0u; x.scansink = NULL;
	}
	/* 2026-11-11 scan records: the keep-phase read of a PARTIAL result is the WHOLE bss record (its length field, bounded by the event's data length and SWG_E_BSS_RMAX), so the radio gets the IEs. Cases: a real-shaped 132 B record + 46 B of IEs;
	 * the length field below the data (read stops at it), above the data (read stops at the data), below the 89 B (the old read), a block longer than the cap (read stops at SWG_E_BSS_RMAX), the dongle delivers the record in the slot bound of the buffer. */
	{
		static const uint8_t ies[46] = { 0, 5, 'r', 'i', 'c', 'h', '0', 1, 4, 0x82, 0x84, 0x8b, 0x96, 3, 1, 6, 48, 20, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 2, 0, 0, 221, 6, 0, 0x10, 0x18, 2, 0, 0x10 };
		static const struct { uint32_t ielen, ieoff, rlen, fld, want_rec; } cs[] = {
			{ 46u, 0u, 0u, 0u, 132u + 46u },            /* a whole record: all of it */
			{ 46u, 0u, 132u + 10u, 0u, 132u + 10u },    /* the record's length field is below the event data: the read stops at the field */
			{ 46u, 0u, 2000u, 0u, 132u + 46u },         /* the field is above the data (a lying firmware): the read stops at the event data */
			{ 46u, 0u, 60u, 0u, 89u },
			{ 46u, 0u, 90u, 0u, 90u },                  /* one byte above the 89 B every record has: exactly that byte is read more */                  /* the field below the 89 B every record has: the old 89 B read (the radio then ignores it: length shorter than the fields it reads) */
			{ 1100u, 0u, 0u, 0u, SWG_E_BSS_RMAX },      /* IE block 1100 B: record 1232 B, read cut at SWG_E_BSS_RMAX */
			{ 1000u, 0u, 0u, 0u, 132u + 1000u },        /* a record that is not cut: 1132 B */
			{ 1052u, 0u, 0u, 0u, SWG_E_BSS_RMAX },      /* exactly one byte above: 132 + 1052 = 1184 = the cap, not cut (boundary) */
		};
		unsigned ci, it, k;

		for (ci = 0; ci < sizeof cs / sizeof cs[0]; ci++) {
			uint8_t want2[80], arg2[96];
			uint32_t nrec = cs[ci].want_rec, exp_len = 12u + nrec;

			memset(&ge.rich, 0, sizeof ge.rich);
			ge.rich.on = 1; ge.rich.ielen = cs[ci].ielen; ge.rich.rlen = cs[ci].rlen; ge.rich.fld = cs[ci].fld;
			memcpy(ge.rich.ie, ies, 46); for (k = 46u; k < cs[ci].ielen; k++) { ge.rich.ie[k] = (uint8_t)(0xC0u + k); }
			ge.rich.chsp = 0x1006u; ge.rich.nrates = 4u; ge.rich.rates[0] = 0x82u; ge.rich.rates[1] = 0x04u; ge.rich.rates[2] = 0x0bu; ge.rich.rates[3] = 0x96u;
			ge.rich.noise = -90; ge.rich.snr = 40; ge.rich.beacon = 100u;
			x.escan_sync = 0x1235u; lw.escan_sync = &x.escan_sync;
			swg_e_escan_arg(arg2, x.emajor, 0, x.escan_sync);
			(void)want2;
			memset(&g_sk, 0, sizeof g_sk);
			x.scansink = g_sink; x.scancx = NULL;
			x.sc_on = 1u; x.sc_complete = 0u; x.sc_ev = x.sc_part = x.sc_other = 0u;
			ge.npartial = 1u; ge.pfin = 0; ge.fault = EF_NONE; ge.nescan = 0u; ge.pi = 0u; ge.pp = 0u;
			x.krefresh = 1u; memset(&cm, 0, sizeof cm);
			rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR escan", "escan", arg2, 80u, 6u + 80u, 1, 0u, NULL, 1, &cm, NULL);
			x.krefresh = 0u;
			CHECK(rc == 0 && !x.d4dstop && !x.stopped && cm.status == 0 && lw_ref == 0u);
			for (it = 0; it < 4u && (it == 0u || x.kevq != 0u || !x.sc_complete) && !x.d4dstop; it++) {
				CHECK(swg_keep_r2(&x) == 0);
			}
			CHECK(x.sc_complete == 1u && x.sc_part == 1u && g_sk.n == 2u && g_sk.st[0] == 8u && g_sk.st[1] == 0u && g_sk.len[1] == 12u);
			CHECK(g_sk.len[0] == exp_len);
			if (ci == 0u) {
				const uint8_t *r = g_sk.b[0] + 12;
				CHECK(swd4b_get32(r) == 109u && swd4b_get32(r + 4) == 132u + 46u && r[8] == 0x02u && r[9] == 0x11u && r[18] == 4u && memcmp(r + 19, "net0", 4) == 0);
				CHECK(r[14] == 100u && r[16] == 0x11u && r[17] == 0x04u && r[52] == 4u && r[56] == 0x82u && r[59] == 0x96u && r[72] == 0x06u && r[73] == 0x10u && (int8_t)r[80] == -90 && (int16_t)(r[124] | r[125] << 8) == 40);
				CHECK(r[116] == 132u && r[120] == 46u && memcmp(r + 132, ies, 46) == 0);
				CHECK(!has("net0") && !has("02:11:22"));       /* no SSID or address in the log */
			}
			if (cs[ci].ielen == 1100u) {
				CHECK(g_sk.b[0][12 + 132 + 1051] == (uint8_t)(0xC0u + 1051u) || nrec == SWG_E_BSS_RMAX);
			}
		}
		x.sc_on = 0u; x.scansink = NULL;
		memset(&ge.rich, 0, sizeof ge.rich);
	}
	/* 2026-11-11: swg_ke_escan called on an event written into slot 0: a host read that fails inside the EXTRA read of the record (the first 101 bytes read fine) is an error and nothing reaches the sink; without the fault the same event is handed on whole */
	{
		struct swg_ev ev3;
		uint8_t *fb = (uint8_t *)f.hb + (x.in.evbuf - PA) + x.rxoff + SWG_E_FRAME;
		int rc3;

		memset(fb, 0, 12u + 140u);
		fb[8] = 0x35u; fb[9] = 0x12u; fb[10] = 1u;
		fb[12] = 109u; fb[16] = 140u;
		memset(&ev3, 0, sizeof ev3);
		ev3.type = SWG_E_EV_ESCAN; ev3.status = SWG_E_ST_PARTIAL; ev3.edlen = 12u + 140u;
		x.escan_sync = 0x1235u; x.scansink = g_sink; x.scancx = NULL; x.sc_on = 1u;
		memset(&g_sk, 0, sizeof g_sk);
		rc3 = swg_ke_escan(&x, &ev3, 0u);
		CHECK(rc3 == 0 && g_sk.n == 1u && g_sk.len[0] == 12u + 140u);
		rd_fail_host_a[0] = (uint32_t)(x.in.evbuf - PA) + x.rxoff + SWG_E_FRAME + 12u + 100u + 4u - ((x.rxoff + SWG_E_FRAME + 12u + 100u) & 3u);
		rd_fail_host_n = 1u;
		memset(&g_sk, 0, sizeof g_sk);
		rc3 = swg_ke_escan(&x, &ev3, 0u);
		CHECK(rc3 != 0 && g_sk.n == 0u);       /* the failed read is an error; the half-read record is never handed on */
		rd_fail_host_n = 0u;
		x.sc_on = 0u; x.scansink = NULL; x.stopped = 0u; x.fail = NULL;
	}
	/* the same engine with an iovar outside the four (review r1 F1): refused at its commit, the reason names the payload */
	lw_nf = 0u;
	rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR pcie_bus_tput", "pcie_bus_tput", key, 16u, 14u + 16u, 1, 0u, NULL, 1, &cm, NULL);
	lw_on = 0u;
	gd.on = 0;
	CHECK(lw_ref > 0u && lw_nf > 0u && strstr(lw_first[0], "L2 ioctl payload: command") != NULL);
	/* review 0281 F1: an item whose epoch/xor check fails inside the drain is a soft stop: 1, d4dstop (the data path ends in the caller's loop), no store (last: the engine is stopped afterwards) */
	{
		const uint32_t ws = fplan->b[SWD_IDX_D2H_W].off, r2 = fplan->b[SWD_D2H_CTRL].off;
		const unsigned ref0 = lw_ref, ns0 = x.nstore;
		uint32_t slot;

		x.kept = 1;
		ge_event(56u, 0u, NULL, 0u);
		slot = ((f.hb[ws / 4u] & 0xFFFFu) + SWG_R2_DEPTH - 1u) % SWG_R2_DEPTH;
		f.hb[r2 / 4u + 6u * slot + 5u] ^= 1u;
		rc = swg_rssi_drain(&x);
		CHECK(rc == 1 && x.d4dstop == 1u && x.kr2c >= 1u && lw_ref == ref0 && x.nstore == ns0);
	}
}
