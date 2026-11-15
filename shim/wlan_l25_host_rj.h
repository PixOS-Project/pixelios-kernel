/* wlan_l25_host_rj.h - host cases of patch 0282 (W-D6 boot B, the re-join after an AP loss), included by wlan_l25_host_test.c before its main() so the mutants of tools/test_wlan_l25_mutants.py
 * (rows of tools/wlan_keep_mutants.py L25_ROWS) are killed by the same plain + ASan runs. The ioctl transport is the recording stub of wlan_l25_host_test.c; rj_stub below answers and records the calls of the
 * re-join (the four security iovars, join, wsec_key), the assoc_info / assoc_req_ies / BSSID / chanspec / RSSI GETs are answered by the stub's own AI / LV tables. Events are fed to the real detector (swg_ke_note), EAPOL to the real
 * hook (swl25_rj_eapol) with the python 4-way vectors (M1 -> M2, M3 -> M4 byte for byte). No hardware.
 * Cases: R1 backoff 2,4,8,16,30,30; R2 arm refusals and the kept secrets; R3 not armed; R4 the whole path DOWN -> JOINING -> KEYING -> UP (calls, order, bytes, notifications, wipes); R5 repeated loss; R6 failures and the backoff clock
 * (join refused, no link events, no EAPOL, key refused, security iovar refused); R7 read-back / re-send of the security iovars; R8 EAPOL gating; R9 engine death wipes; R10 deferral while ring 2 is not idle. */
struct rjcall { uint32_t cmd, argn, wlen; char name[16]; int secret; uint8_t arg[SWJ_WSEC_KEY_SIZE]; };
static struct rjcall RC[64];
static int rj_on;
static unsigned nRC;
static struct { uint32_t val[4]; int32_t get_st[4], set_st[4], join_st, key_st, wsecset_st, escan_st, ka_st; unsigned short_get, bssid_fail_from, bssid_calls, stop_hard; const char *stop_at, *lost_at; } RJ;
static unsigned swj_wn_kd, swj_wn_pdu, swj_wn_mic, swj_wn_bad;
static void swj_wipe_seen(void *p, uint32_t n)
{
	const uint8_t *b = p;
	uint32_t i;

	for (i = 0; i < n; i++) {
		if (b[i] != 0u) {
			swj_wn_bad++;
		}
	}
	if (n == SWJ_KD_MAX) swj_wn_kd++;
	else if (n == SWJ_TX_MAX) swj_wn_pdu++;
	else if (n == 16u) swj_wn_mic++;
}
static const char *const RJN[4] = { "wpa_auth", "wsec", "auth", "mfp" };

static void rj_ev(uint32_t type, uint32_t flags, uint32_t status);
static int rj_stub(uint32_t cmd, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, uint32_t nrsp, uint8_t *rsp, struct swd4b_ioctl_d2h *cmo, int secret)
{
	unsigned i, v = 4u;
	struct rjcall *c;

	if (!rj_on) {
		return 0;       /* the other cases of wlan_l25_host_test.c record wsec_key / wsec in K[] */
	}

	if (RJ.stop_at != NULL && ((name[0] != '\0' && strcmp(name, RJ.stop_at) == 0) || (name[0] == '\0' && cmd == SWL25_WLC_GET_BSSID && strcmp(RJ.stop_at, "bssid") == 0))) {
		x.d4dstop = 1u;
		x.stopped = RJ.stop_hard;       /* a SOFT stop inside this ioctl (SWG_E_STOP "no ioctl answer", a DS item met with krefresh set): the call returns normally with the flag set */
	}
	if (RJ.lost_at != NULL && name[0] != '\0' && strcmp(name, RJ.lost_at) == 0) {
		rj_ev(SWG_E_EV_DEAUTH_IND, 0u, 0u);       /* the AP drops us while this ioctl is in flight (decoded inside the call by the keep branch) */
	}
	if (cmd == SWL25_WLC_GET_BSSID && name[0] == '\0' && RJ.bssid_fail_from != 0u && ++RJ.bssid_calls >= RJ.bssid_fail_from) {       /* the n-th WLC_GET_BSSID of the world and the ones after it are refused */
		memset(cmo, 0, sizeof *cmo);
		cmo->status = -1;
		return 1;
	}
	for (i = 0; i < 4u; i++) {
		if (strcmp(name, RJN[i]) == 0) {
			v = i;
		}
	}
	if (v == 4u && strcmp(name, "join") != 0 && strcmp(name, "wsec_key") != 0 && strcmp(name, "escan") != 0 && strcmp(name, "mkeep_alive") != 0) {       /* patch 0292: the keep-phase scan START is recorded too */
		return 0;
	}
	c = &RC[nRC < 63u ? nRC : 63u];
	nRC++;
	memset(c, 0, sizeof *c);
	c->cmd = cmd; c->argn = argn; c->wlen = wlen; c->secret = secret;
	snprintf(c->name, sizeof c->name, "%s", name);
	if (arg != NULL && argn <= sizeof c->arg) {
		memcpy(c->arg, arg, argn);
	}
	memset(cmo, 0, sizeof *cmo);
	if (cmd == SWD4B_WLC_GET_VAR && v < 4u) {
		if (nrsp >= 4u) {
			put32le(rsp, RJ.val[v]);
		}
		cmo->status = RJ.get_st[v];
		cmo->resp_len = RJ.short_get ? 2u : 4u;
	} else if (cmd == SWD4B_WLC_SET_VAR && v < 4u) {
		cmo->status = v == 1u ? RJ.wsecset_st : RJ.set_st[v];
		if (cmo->status == 0 && argn == 4u) {
			RJ.val[v] = (uint32_t)arg[0] | (uint32_t)arg[1] << 8 | (uint32_t)arg[2] << 16 | (uint32_t)arg[3] << 24;
		}
	} else if (strcmp(name, "join") == 0) {
		cmo->status = RJ.join_st;
	} else if (strcmp(name, "escan") == 0) {
		cmo->status = RJ.escan_st;
	} else if (strcmp(name, "mkeep_alive") == 0) {
		cmo->status = RJ.ka_st;
	} else {
		cmo->status = RJ.key_st;
	}
	return 1;
}

static unsigned rj_count_of(const char *needle)
{
	unsigned n = 0u;
	const char *p = logbuf;

	while ((p = strstr(p, needle)) != NULL) {
		n++;
		p += strlen(needle);
	}
	return n;
}

static struct swl25_rj RJX;
static struct swg_jcmd JC[6];
static struct swj_cmd JCS[6];
static const uint8_t RJ_ANY[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };

static void rj_ev(uint32_t type, uint32_t flags, uint32_t status)
{
	struct swg_ev ev;

	memset(&ev, 0, sizeof ev);
	ev.type = type; ev.flags = flags; ev.status = status;
	swg_ke_note(&x, &ev);
}

static void rj_ev_r(uint32_t type, uint32_t flags, uint32_t status, uint32_t reason)
{
	struct swg_ev ev;

	memset(&ev, 0, sizeof ev);
	ev.type = type; ev.flags = flags; ev.status = status; ev.reason = reason;
	swg_ke_note(&x, &ev);
}

static void rj_world(int arm)
{
	unsigned i;

	scen();
	rj_on = 1;
	D.on = 0;
	x.l25c = NULL;
	x.kept = 1;
	x.elv = 25u;
	memset(&swl25_H, 0, sizeof swl25_H);
	memcpy(swl25_H.l.mac, V_SPA, 6);
	memset(&RJX, 0, sizeof RJX);
	memset(RC, 0, sizeof RC);
	nRC = 0u;
	memset(&RJ, 0, sizeof RJ);
	RJ.val[0] = SWJ_WPA2_AUTH_PSK; RJ.val[1] = SWJ_WSEC_AES;
	CHECK(swj_join_cmds(JCS, (const uint8_t *)V_SSID, (uint32_t)strlen(V_SSID), RJ_ANY, 0) == 6u);
	for (i = 0; i < 6u; i++) {
		memset(&JC[i], 0, sizeof JC[i]);
		JC[i].cmd = JCS[i].cmd; JC[i].row = JCS[i].row; memcpy(JC[i].buf, JCS[i].buf, JCS[i].len); JC[i].len = JCS[i].len;
	}
	if (arm) {
		CHECK(swl25_rj_arm(&RJX, &swl25_H, V_PMK, JC, 6u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == 0 && RJX.armed == 1u);
	}
	loglen = 0u;
	logbuf[0] = '\0';
	ngap = 0u;
	rj_ev(SWG_E_EV_DEAUTH_IND, 0u, 0u);       /* the 0281 detector: UP -> DOWN */
}

/* one successful pass through the whole machine starting at ms t0 (state DOWN, armed): returns the ms of the UP */
static uint64_t rj_pass(uint64_t t0, unsigned attempts_before)
{
	static uint8_t out[SWJ_TX_MAX];
	uint32_t olen = 0u;
	uint64_t t = t0;

	CHECK(x.klink == 1u && x.rj_state == SWG_RJ_DOWN);
	nRC = 0u;
	x.rj_ssid = x.rj_link = 1u;       /* stale answers of an earlier attempt: the new attempt starts clean */
	x.rj_lost = 1u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	CHECK(x.rj_state == SWG_RJ_JOINING && RJX.n == attempts_before + 1u && x.krefresh == 0u && x.rj_ssid == 0u && x.rj_link == 0u && x.rj_lost == 0u);
	CHECK(nRC == 5u && RC[0].cmd == SWD4B_WLC_GET_VAR && strcmp(RC[0].name, "wpa_auth") == 0 && RC[0].wlen == 13u && RC[0].argn == 4u && RC[0].secret == 0);
	CHECK(strcmp(RC[1].name, "wsec") == 0 && RC[1].wlen == 9u && strcmp(RC[2].name, "auth") == 0 && RC[2].wlen == 9u && strcmp(RC[3].name, "mfp") == 0 && RC[3].wlen == 8u);
	CHECK(RC[4].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[4].name, "join") == 0 && RC[4].secret == 1 && RC[4].argn == SWJ_EXT_JOIN_V1_SIZE && RC[4].wlen == 5u + SWJ_EXT_JOIN_V1_SIZE &&
	      memcmp(RC[4].arg, JC[5].buf + 5, SWJ_EXT_JOIN_V1_SIZE) == 0);       /* the kept boot payload, byte for byte (wildcard BSSID) */
	{
		char l[48];

		snprintf(l, sizeof l, "SWEET WLAN: keep rejoin attempt %u\n", attempts_before + 1u);
		CHECK(has(l));
	}
	t += 100u;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_JOINING);       /* no events yet: waiting */
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	CHECK(x.rj_ssid == 1u && x.rj_link == 0u);
	t += 50u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);       /* SET_SSID alone is not enough */
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(x.rj_link == 1u && !has("keep link UP event outside"));       /* the expected answer: no GAP */
	t += 50u;
	AI.n_info = AI.n_ies = LV.n = 0u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	CHECK(x.rj_state == SWG_RJ_KEYING && RJX.eapol_on == 1u && AI.n_info == 1u && AI.n_ies == 1u && has("keep rejoin: joined (SET_SSID 0 + LINK up), host 4-way starts (assoc RSNE read from the firmware)"));
	CHECK(memcmp(RJX.bssid, V_AA, 6) == 0 && memcmp(swl25_H.sup.aa, V_AA, 6) == 0 && memcmp(swl25_H.sup.spa, V_SPA, 6) == 0 && memcmp(swl25_H.sup.pmk, V_PMK, 32) == 0);
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && olen == sizeof V_M2 && memcmp(out, V_M2, sizeof V_M2) == 0 && RJX.m4_seen == 0u);       /* M1 -> M2 */
	t += 20u;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_KEYING);       /* M3 not yet */
	CHECK(swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0 && olen == sizeof V_M4 && memcmp(out, V_M4, sizeof V_M4) == 0 && RJX.m4_pending == 1u && RJX.m4_seen == 0u);       /* M3 -> M4: produced, not yet posted (review 0282 F6) */
	t += 10u;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 1), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_KEYING);       /* en0 posted it: it counts as sent; the tx buffer is still busy: wait (< 200 ms) */
	CHECK(RJX.m4_seen == 1u && RJX.m4_pending == 0u && RJX.m4_ms == t);
	CHECK(swl25_rj_step(&x, &RJX, RJX.m4_ms + SWL25_RJ_TXWAIT_MS - 1u, SWL25_RJ_BUSY_ARG(&RJX, 1), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_KEYING);       /* 199 ms after the M4: still waiting */
	t = RJX.m4_ms + SWL25_RJ_TXWAIT_MS;
	CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 1), 1) == 0);       /* the wait ran out: GAP, keys go in */
	CHECK(has("WIFI GAP: keep rejoin: M4 tx status not seen in 200 ms, keys installed anyway"));
	CHECK(nRC == 4u && strcmp(RC[0].name, "wsec_key") == 0 && RC[0].secret == 1 && RC[0].argn == SWJ_WSEC_KEY_SIZE && RC[0].wlen == 9u + SWJ_WSEC_KEY_SIZE &&
	      strcmp(RC[1].name, "wsec_key") == 0 && RC[1].secret == 1 && RC[2].cmd == SWD4B_WLC_GET_VAR && strcmp(RC[2].name, "wsec") == 0 && RC[3].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[3].name, "wsec") == 0);
	CHECK(RC[0].arg[0] == 0u && RC[0].arg[4] == 16u && memcmp(RC[0].arg + 8, V_PTK + 32, 16) == 0 && RC[0].arg[112] == SWJ_ALGO_AES_CCM && memcmp(RC[0].arg + 156, V_AA, 6) == 0);       /* pairwise: index 0, the TK, CCMP, the AP */
	CHECK(RC[1].arg[0] == V_GTK_ID && RC[1].arg[4] == 16u && memcmp(RC[1].arg + 8, V_GTK, 16) == 0 && RC[1].arg[112] == SWJ_ALGO_AES_CCM);       /* group: its id and key */
	CHECK(RC[3].argn == 4u && RC[3].arg[0] == (uint8_t)SWJ_WSEC_AES && RC[3].wlen == 9u);
	CHECK(x.klink == 0u && x.rj_state == SWG_RJ_UP && x.kl_new == 0u && RJX.eapol_on == 0u && RJX.n == 0u && x.krefresh == 0u && !x.d4dstop);
	CHECK(all_zero(swl25_H.sup.tptk, sizeof swl25_H.sup.tptk) && all_zero(swl25_H.sup.gtk, sizeof swl25_H.sup.gtk) && all_zero(swl25_H.sup.pmk, 32) && swl25_H.hs_done == 1u);       /* the keys are in the dongle: the host copies are gone */
	return t;
}

/* R12 (review 0282 F1, probe P3): a SOFT stop inside any ioctl of the re-join does not end the keep engine. The flag is cleared, the re-join is OFF for the rest of the boot, the kept secrets AND the supplicant's copy are wiped, one GAP
 * names the step, the link stays DOWN for iOS and later steps do nothing */
static void rj_soft_stops(void)
{
	static const struct { const char *at; const char *step; } sc[] = {
		{ "wpa_auth", "attempt" }, { "join", "attempt" }, { "bssid", "to_keying" }, { "assoc_info", "to_keying" }, { "assoc_req_ies", "to_keying" },
		{ "wsec_key", "finish" }, { "wsec", "finish" }, { "chanspec", "finish" },
	};
	static uint8_t out[SWJ_TX_MAX];
	uint32_t olen = 0u;
	unsigned k;

	for (k = 0; k < sizeof sc / sizeof sc[0]; k++) {
		const unsigned g0 = (unsigned)swl_gap_tot;
		const int at_attempt = strcmp(sc[k].step, "attempt") == 0, at_keying = strcmp(sc[k].step, "to_keying") == 0;
		char l[96];

		rj_world(1);
		if (at_attempt) {
			RJ.stop_at = sc[k].at;
		}
		CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
		if (!at_attempt) {
			rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
			rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
			if (at_keying) {
				RJ.stop_at = sc[k].at;
			}
			CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
		}
		if (!at_attempt && !at_keying) {
			CHECK(x.rj_state == SWG_RJ_KEYING && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
			CHECK(!all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup));       /* the supplicant holds the PMK and the PTK now */
			RJ.stop_at = sc[k].at;
			CHECK(swl25_rj_step(&x, &RJX, 210u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
		}
		RJ.stop_at = NULL;
		snprintf(l, sizeof l, "WIFI GAP: keep rejoin disabled: soft stop in %s\n", sc[k].step);
		CHECK(x.d4dstop == 0u && x.stopped == 0u && x.rj_off == 1u && x.krefresh == 0u);       /* the keep loop goes on */
		CHECK(x.rj_state == SWG_RJ_DOWN && x.klink == 1u && x.rj_ssid == 0u && x.rj_link == 0u);       /* DOWN stays DOWN for iOS */
		CHECK(all_zero((const uint8_t *)&RJX, sizeof RJX) && all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup));       /* the kept secrets and the supplicant's copy are gone */
		CHECK(has(l) && rj_count_of("keep rejoin disabled") == 1u && swl_gap_tot == g0 + 1u && has("kept join payload and PMK wiped (soft stop in re-join)") == (sc[k].at != NULL));
		nRC = 0u;
		CHECK(swl25_rj_step(&x, &RJX, 99999u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_step(&x, &RJX, 200000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_DOWN && rj_count_of("keep rejoin disabled") == 1u);       /* off for the boot: no ioctl, no second line */
		CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == -1);
		CHECK(!has("the join payload and PMK are not kept"));       /* switched off, not "lost and nothing kept" */
	}
	/* a HARD stop (the bus is gone: x->stopped) is not a soft stop: nothing is cleared or switched off here, the keep loop ends on it and sw_keep_end wipes */
	rj_world(1);
	RJ.stop_hard = 1u;
	RJ.stop_at = "join";
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.stopped == 1u && x.d4dstop == 1u && x.rj_off == 0u && RJX.armed == 1u && !has("keep rejoin disabled"));
	RJ.stop_hard = 0u;
	RJ.stop_at = NULL;
	/* N2 (review 0282 round 2): a soft stop raised by the ring-2 DRAIN at the start of an attempt (here: a ring-2 item with a wrong epoch, as a firmware trap or a corrupt item gives) is the data path's stop: it is NOT cleared, the re-join stays on and keeps its secrets, the keep loop ends on it as before */
	rj_world(1);
	g1[(D2HW - G1PA) / 4u] = (x.d4rd % 64u + 1u) % 64u;       /* ring 2 busy: one item pending, all zero = a failed epoch/xor check */
	nRC = 0u;
	loglen = 0u; logbuf[0] = '\0';
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	CHECK(x.d4dstop == 1u && x.stopped == 0u && x.rj_off == 0u && RJX.armed == 1u && nRC == 0u && !has("keep rejoin disabled") && x.rj_state == SWG_RJ_DOWN);
	/* F5 leftover (round 2): a soft stop before the ioctl completion leaves the secret in the DMA buffer: one GAP says so, nothing is written */
	rj_world(1);
	x.esec_left = 1u;
	x.esec_done = 0u;
	RJ.stop_at = "join";
	{
		const unsigned g0 = (unsigned)swl_gap_tot;

		CHECK(swl25_rj_step(&x, &RJX, 100u, 0, 1) == 0 && has("WIFI GAP: keep rejoin: the stop came before the ioctl completion, the DMA ioctl buffer may hold secret bytes until the next ioctl") && swl_gap_tot == g0 + 2u);
	}
	RJ.stop_at = NULL;
	rj_world(1);
	x.esec_left = 1u;
	x.esec_done = 1u;
	RJ.stop_at = "join";
	CHECK(swl25_rj_step(&x, &RJX, 100u, 0, 1) == 0 && !has("may hold secret bytes"));       /* completion consumed: scrubbed, no GAP */
	RJ.stop_at = NULL;
	/* the payload buffer scrub (swg_e_scrub, store covered in wlan_g2_host_l25wl.h): after a soft stop it runs only when the completion was consumed; here the shadow is already zero, so no store is needed and the pending flag is cleared; without the completion it stays */
	rj_world(1);
	x.esec_left = 1u;
	x.esec_done = 1u;
	RJ.stop_at = "join";
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_off == 1u && x.esec_left == 0u && !x.d4dstop && !x.stopped);
	RJ.stop_at = NULL;
	rj_world(1);
	x.esec_left = 1u;
	x.esec_done = 0u;
	RJ.stop_at = "join";
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_off == 1u && x.esec_left == 1u);
	RJ.stop_at = NULL;
}

/* patch 0288: swl25_run calls the x->bssget hook after the association RSNE read and before swl25_go (the cfg is read there); a stop or a negative result ends the run without swl25_go; no cfg, no hook */
static int bg_n, bg_ret, bg_stop;
static unsigned bg_nmac, bg_ies;
static int bg_hook(struct swg_wx *xx)
{
	bg_n++;
	bg_nmac = KS.nmac;
	bg_ies = AI.n_ies;
	if (bg_stop) {
		xx->d4dstop = 1u;
	}
	return bg_ret;
}
static void test_bssget_hook(void)
{
	int rc;

	bg_n = bg_ret = bg_stop = 0;
	scen();
	x.bssget = bg_hook;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && bg_n == 1 && bg_nmac == 0u && bg_ies == 1u && KS.nmac == 1u && swl25_H.hs_done == 1u);       /* after the RSNE reads, before swl25_go's first read (cur_etheraddr) */
	bg_n = 0; bg_stop = 1;
	scen();
	x.bssget = bg_hook;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && bg_n == 1 && x.d4dstop == 1u && KS.nmac == 0u && swl25_H.hs_done == 0u);
	bg_n = 0; bg_stop = 0; bg_ret = -3;
	scen();
	x.bssget = bg_hook;
	rc = swl25_run(&x, 25u);
	CHECK(rc == -3 && bg_n == 1 && KS.nmac == 0u);
	bg_n = 0; bg_ret = 0;
	scen();
	x.bssget = bg_hook;
	x.l25c = NULL;
	rc = swl25_run(&x, 25u);
	CHECK(rc == 0 && bg_n == 0);
	scen();
	rc = swl25_run(&x, 25u);       /* no hook: as before */
	CHECK(rc == 0 && swl25_H.hs_done == 1u);
}

static void test_rejoin(void)
{
	static uint8_t out[SWJ_TX_MAX], pm[32], bssid[6];
	uint32_t olen = 0u, chan = 0u, ok = 0u, i;
	int32_t rssi = 0;
	uint64_t t;
	unsigned a;

	/* R1: the backoff after attempt n: 2, 4, 8, 16, 30 s and 30 s for ever */
	CHECK(swg_rj_backoff_ms(0) == 2000u && swg_rj_backoff_ms(1) == 2000u && swg_rj_backoff_ms(2) == 4000u && swg_rj_backoff_ms(3) == 8000u && swg_rj_backoff_ms(4) == 16000u);
	CHECK(swg_rj_backoff_ms(5) == 30000u && swg_rj_backoff_ms(6) == 30000u && swg_rj_backoff_ms(100) == 30000u && swg_rj_backoff_ms(0xFFFFFFFFu) == 30000u);

	/* R2: arming. Missing inputs are a GAP and arm nothing (the kept bytes are zeroed); a good call keeps the PMK, the join payload and the four values */
	rj_world(0);
	CHECK(swl25_rj_arm(&RJX, &swl25_H, V_PMK, JC, 5u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == -1 && RJX.armed == 0u && all_zero(RJX.pmk, 32) && has("WIFI GAP: keep rejoin: not armed (inputs missing, got mask 0xf)"));
	memset(pm, 0, sizeof pm);
	CHECK(swl25_rj_arm(&RJX, &swl25_H, pm, JC, 6u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == -1 && RJX.armed == 0u);
	CHECK(swl25_rj_arm(&RJX, &swl25_H, V_PMK, JC, 6u, V_RSNE, 0u, V_RSNE, sizeof V_RSNE) == -1 && RJX.armed == 0u && all_zero(RJX.rsne_beacon, 64));
	CHECK(swl25_rj_arm(&RJX, &swl25_H, NULL, JC, 6u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == -1 && RJX.armed == 0u);
	JC[2].len = 5u;       /* "auth" with no value: the table is not what swj_join_cmds builds */
	CHECK(swl25_rj_arm(&RJX, &swl25_H, V_PMK, JC, 6u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == -1 && RJX.armed == 0u);
	rj_world(0);
	loglen = 0u; logbuf[0] = '\0';
	CHECK(swl25_rj_arm(&RJX, &swl25_H, V_PMK, JC, 6u, V_RSNE, sizeof V_RSNE, V_RSNE, sizeof V_RSNE) == 0);
	CHECK(RJX.armed == 1u && memcmp(RJX.pmk, V_PMK, 32) == 0 && memcmp(RJX.join, JC[5].buf + 5, SWJ_EXT_JOIN_V1_SIZE) == 0 && RJX.v_wpa_auth == SWJ_WPA2_AUTH_PSK && RJX.v_wsec == SWJ_WSEC_AES && RJX.v_auth == 0u && RJX.v_mfp == 0u);
	CHECK(RJX.rsne_beacon_len == sizeof V_RSNE && memcmp(RJX.rsne_beacon, V_RSNE, sizeof V_RSNE) == 0 && RJX.rsne_assoc_len == sizeof V_RSNE && RJX.h == &swl25_H);
	CHECK(has("SWEET WLAN: keep rejoin: join payload and PMK kept in RAM for re-joins (never printed, wiped when the engine dies)") && !log_has_bytes(V_PMK, 32) && strstr(logbuf, V_SSID) == NULL);       /* kept, never printed */

	/* R3: the link is lost and nothing was kept: one loud GAP, no engine call, the state stays DOWN */
	rj_world(0);
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 1000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_step(&x, &RJX, 2000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_DOWN && rj_count_of("WIFI GAP: keep rejoin: the link is DOWN but the join payload and PMK are not kept") == 1u);
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == -1 && olen == 0u);

	/* R4: the whole path, one attempt, then the result for iOS */
	rj_world(1);
	CHECK(x.rj_state == SWG_RJ_DOWN && x.kl_new == 1u);
	t = rj_pass(5000u, 0u);
	CHECK(has("SWEET WLAN: keep rejoin UP after 1 attempts, 430 ms\n") && RJX.n_up == 1u && RJX.armed == 1u && memcmp(RJX.pmk, V_PMK, 32) == 0);       /* T = DOWN entry (first step) to UP; the PMK is kept for the next loss */
	CHECK(swl25_rj_take_up(&RJX, bssid, &chan, &rssi, &ok) == 1 && memcmp(bssid, V_AA, 6) == 0 && chan == 40u && rssi == -41 && (ok & 0xEu) == 0xEu);       /* the firmware's values after the keys: chanspec 0xE12A -> primary 40, RSSI -41 */
	CHECK(swl25_rj_take_up(&RJX, bssid, &chan, &rssi, &ok) == 0);       /* once */
	CHECK(!log_has_bytes(V_PMK, 32) && !log_has_bytes(V_PTK + 32, 16) && !log_has_bytes(V_GTK, 16) && strstr(logbuf, V_SSID) == NULL);
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, t + 1000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_UP);       /* UP: the machine is idle */
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == -1);       /* after UP a rekey M1 is not answered (en0 drops it with a GAP) */

	/* R5: lost again: the same path from attempt 1 (the counter restarts), with the kept PMK */
	rj_ev(SWG_E_EV_LINK, 0u, 0u);
	CHECK(x.klink == 1u && x.rj_state == SWG_RJ_DOWN && x.kl_new == 1u);
	x.kl_new = 0u;
	loglen = 0u; logbuf[0] = '\0';       /* only the second pass's lines count */
	t = rj_pass(t + 60000u, 0u);
	CHECK(RJX.n_up == 2u && has("keep rejoin UP after 1 attempts, 430 ms"));

	loglen = 0u; logbuf[0] = '\0';
	/* R5b: the values for iOS come from THIS read: a RSSI that is out of range is not reported (bit 8 clear) although the first re-join had it; a BSSID the firmware refuses to report after the keys is still known from the join (bit 2 stays set) */
	rj_ev(SWG_E_EV_DISASSOC, 0u, 0u);
	x.kl_new = 0u;
	LV.rssi = 5;
	RJ.bssid_fail_from = 2u; RJ.bssid_calls = 0u;       /* the first WLC_GET_BSSID (the join's) answers, the one after the keys is refused */
	t = rj_pass(t + 60000u, 0u);
	CHECK(swl25_rj_take_up(&RJX, bssid, &chan, &rssi, &ok) == 1 && (ok & 8u) == 0u && (ok & 2u) == 2u && (ok & 4u) == 4u && memcmp(bssid, V_AA, 6) == 0 && has("WIFI GAP: L25 live link: WLC_GET_RSSI"));
	LV.rssi = -41; RJ.bssid_fail_from = 0u;

	/* R5c: the association-request RSNE the firmware reports is the one M2 carries (it can differ from the boot one); the beacon RSNE for the M3 check stays the kept one. Not readable: the boot one, said so */
	{
		uint8_t alt[sizeof V_RSNE - 2u];

		memcpy(alt, V_RSNE, sizeof alt);
		alt[1] = (uint8_t)(sizeof alt - 2u);       /* the same RSNE without its capabilities field: another LENGTH (20), another content */
		rj_world(1);
		ai_reset(alt, (uint32_t)sizeof alt);
		CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
		rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
		rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
		CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_KEYING && swl25_H.sup.rsne_assoc_len == sizeof alt && memcmp(swl25_H.sup.rsne_assoc, alt, sizeof alt) == 0 &&
		      swl25_H.sup.rsne_beacon_len == sizeof V_RSNE && memcmp(swl25_H.sup.rsne_beacon, V_RSNE, sizeof V_RSNE) == 0);
		rj_world(1);
		ai_reset(alt, (uint32_t)sizeof alt);
		AI.st_ies = -3;
		CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
		rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
		rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
		CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_KEYING && memcmp(swl25_H.sup.rsne_assoc, V_RSNE, sizeof V_RSNE) == 0 && has("host 4-way starts (assoc RSNE from the boot run)"));
		ai_reset(V_RSNE, (uint32_t)sizeof V_RSNE);
	}

	/* R6: a refused join: attempt 1 fails at once, the next after 2 s; then 4, 8, 16, 30, 30 s. The count in the line, the GAP, the keys of the half-done 4-way are gone */
	{
		static const uint32_t want[7] = { 2000u, 4000u, 8000u, 16000u, 30000u, 30000u, 30000u };

		rj_world(1);
		RJ.join_st = -5;
		t = 10000u;
		for (a = 0; a < 7u; a++) {
			char l[96];

			nRC = 0u;
			loglen = 0u;
			logbuf[0] = '\0';
			CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
			CHECK(nRC == 5u && x.rj_state == SWG_RJ_DOWN && RJX.n == a + 1u && RJX.t_next == t + want[a] && RJX.n_fail == a + 1u);
			snprintf(l, sizeof l, "WIFI GAP: keep rejoin attempt %u failed (join refused), next attempt in %u ms\n", a + 1u, want[a]);
			CHECK(has(l) && has("WIFI GAP: keep rejoin: join refused status=-5"));
			nRC = 0u;
			CHECK(swl25_rj_step(&x, &RJX, t + want[a] - 1u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u);       /* one ms early: nothing */
			t += want[a];
		}
		CHECK(!x.d4dstop && !x.stopped);       /* a failed attempt never stops the engine */
		RJ.join_st = 0;
		nRC = 0u;
		CHECK(swl25_rj_step(&x, &RJX, t, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING && RJX.n == 8u && nRC == 5u);       /* attempt 8 goes out and is accepted */
	}

	/* R6b: the join is accepted but no link events come: after SWG_J_BUDGET_MS the attempt fails */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 3u);       /* an unrelated SET_SSID (status 3: the firmware's own retry) is no answer and no verdict */
	CHECK(has("keep rejoin: event type 0 status 3 noted while joining") && x.rj_ssid == 0u && x.rj_state == SWG_RJ_JOINING);
	CHECK(swl25_rj_step(&x, &RJX, 100u + SWG_J_BUDGET_MS - 1u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	CHECK(swl25_rj_step(&x, &RJX, 100u + SWG_J_BUDGET_MS, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && has("keep rejoin attempt 1 failed (no SET_SSID 0 + LINK up in the join budget), next attempt in 2000 ms") && RJX.t_next == 100u + SWG_J_BUDGET_MS + 2000u);

	/* R6c: the events come, the AP never sends M1/M3: the 4-way times out, the half-done supplicant is wiped, EAPOL is dropped again */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_KEYING);
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && olen > 0u);       /* M1 answered, M3 never comes */
	CHECK(swl25_rj_step(&x, &RJX, 200u + SWG_J_BUDGET_MS - 1u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_KEYING);
	CHECK(swl25_rj_step(&x, &RJX, 200u + SWG_J_BUDGET_MS, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && RJX.eapol_on == 0u && all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup) && has("keep rejoin attempt 1 failed (4-way not complete in the budget)"));
	CHECK(swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == -1 && olen == 0u);

	/* R6c2 (review 0282 F3, probe P4): a DEAUTH_IND during the 4-way fails the attempt at the next step, even after M3/M4: never UP on a dead link, no key goes in, the AP is tried again after the backoff */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	rj_ev(SWG_E_EV_DEAUTH_IND, 0u, 0u);
	CHECK(x.rj_lost == 1u && has("noted during the 4-way, the attempt fails"));
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 210u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && x.rj_state == SWG_RJ_DOWN && x.klink == 1u && RJX.up_new == 0u && RJX.n_up == 0u && x.rj_lost == 0u && RJX.m4_pending == 0u && RJX.m4_seen == 0u && has("attempt 1 failed (link lost during the 4-way)") && RJX.t_next == 210u + 2000u);
	CHECK(all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup) && RJX.eapol_on == 0u);
	/* ... and while JOINING: the loss fails the attempt, a firmware retry's SET_SSID status does not */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 3u);
	CHECK(x.rj_lost == 0u);
	rj_ev(SWG_E_EV_DISASSOC, 0u, 0u);
	CHECK(x.rj_lost == 1u && swl25_rj_step(&x, &RJX, 150u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && has("attempt 1 failed (link lost while joining)") && x.rj_lost == 0u);
	/* T-32 [obs allon-stress3]: DEAUTH_IND (the rj_world cause), join sent, then the firmware's trailing LINK down reason 2 (WLC_E_LINK_DISASSOC) of the SAME lost association: noted, the attempt
	 * goes on and comes up on the FIRST attempt; a second LINK down, another reason, or a new DEAUTH still fail it */
	rj_world(1);
	CHECK(x.kl_tail == 0u && swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING && RJX.n == 1u);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.rj_lost == 0u && x.kl_tail == 1u && has("trailing LINK down (reason 2 = disassoc) of the lost association (cause 6) noted while joining, the attempt goes on"));
	CHECK(!has("noted while joining, not a link-up answer"));
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_KEYING && RJX.n == 1u && !has("failed (link lost while joining)"));
	/* the tail is accepted once: a second LINK down reason 2 is a real loss */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.rj_lost == 0u);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.rj_lost == 1u && swl25_rj_step(&x, &RJX, 150u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && has("attempt 1 failed (link lost while joining)"));
	/* another reason (1 = beacon loss) is never the tail */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, 1u);
	CHECK(x.rj_lost == 1u && x.kl_tail == 0u);
	/* a new DEAUTH while joining still fails the attempt even with no tail seen */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev_r(SWG_E_EV_DEAUTH_IND, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.rj_lost == 1u);
	/* the tail before the first attempt is noted the same way; then a LINK down while joining is a real loss */
	rj_world(1);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.kl_tail == 1u && x.rj_state == SWG_RJ_DOWN && has("noted before the attempt, not a verdict"));
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	rj_ev_r(SWG_E_EV_LINK, 0u, 0u, SWG_E_LINK_R_DISASSOC);
	CHECK(x.rj_lost == 1u);
	/* a DOWN episode caused by a LINK down has no tail: swg_ke_tail is 0 */
	{
		struct swg_wx t;
		struct swg_ev e;
		memset(&t, 0, sizeof t); memset(&e, 0, sizeof e);
		t.kl_cause = SWG_E_EV_LINK; e.type = SWG_E_EV_LINK; e.reason = SWG_E_LINK_R_DISASSOC;
		CHECK(swg_ke_tail(&t, &e) == 0);
		t.kl_cause = SWG_E_EV_DISASSOC;
		CHECK(swg_ke_tail(&t, &e) == 1);
		e.flags = SWG_E_LINK_UP;
		CHECK(swg_ke_tail(&t, &e) == 0);
	}
	/* ... and a loss decoded INSIDE the key-install ioctls: the keys went in, the link is dead: no UP */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	CHECK(swl25_rj_step(&x, &RJX, 210u, SWL25_RJ_BUSY_ARG(&RJX, 1), 1) == 0 && x.rj_state == SWG_RJ_KEYING && RJX.m4_seen == 1u);
	RJ.lost_at = "wsec";
	CHECK(swl25_rj_step(&x, &RJX, 210u + SWL25_RJ_TXWAIT_MS, SWL25_RJ_BUSY_ARG(&RJX, 1), 1) == 0 && x.rj_state == SWG_RJ_DOWN && RJX.up_new == 0u && x.klink == 1u && has("attempt 1 failed (link lost while the keys went in)"));
	RJ.lost_at = NULL;

	/* R6f (review 0282 F6): the M4 reply could not be posted by en0: the attempt fails, the keys do not go in */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && RJX.m4_pending == 0u);       /* an M2 is no M4 */
	CHECK(swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0 && RJX.m4_pending == 1u);
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 210u, 0, 0) == 0 && nRC == 0u && x.rj_state == SWG_RJ_DOWN && RJX.m4_seen == 0u && RJX.m4_pending == 0u && has("WIFI GAP: keep rejoin: the M4 reply could not be posted") && has("attempt 1 failed (M4 not posted)"));

	/* R6g (review 0282 F4, probe P2): an M1 after the accepted M3 (forged: M1 has no MIC; or the AP restarted) is dropped with a GAP, the PTK stays the confirmed one, the keys installed are the M3's; the M3 retransmit with the same replay counter is dropped by swj_rx as before */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	{
		uint8_t m1b[sizeof V_M1];
		const unsigned g0 = (unsigned)swl_gap_tot;

		memcpy(m1b, V_M1, sizeof m1b);
		m1b[SWJ_ETH_HLEN + SWJ_OFF_NONCE + 3] ^= 0x5Au;       /* another ANonce */
		CHECK(swl25_rj_eapol(&RJX, m1b, sizeof m1b, out, sizeof out, &olen) == 0 && olen == 0u && RJX.eapol_m1_late == 1u && swl25_H.sup.state == SWJ_PTK_DONE && swl25_H.sup.install_ptk != 0 && swl_gap_tot == g0 + 1u &&
		      has("WIFI GAP: keep rejoin: M1 after M3 dropped"));
	}
	CHECK(swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0 && RJX.eapol_m1_late == 1u);       /* only the M1 is the late frame: an M3 retransmit goes to swj_rx (which drops the repeated counter itself) */
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 210u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_step(&x, &RJX, 211u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_UP && nRC == 4u && memcmp(RC[0].arg + 8, V_PTK + 32, 16) == 0);       /* the TK of the M3 the AP confirmed */

	/* R6h (review 0282 F5): the stack leftovers of swj_rx are wiped on every path: an M3 wipes the key data (256 B), the MIC copies and the built PDU; an M1 only the last two */
	rj_world(1);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	swj_wn_kd = swj_wn_pdu = swj_wn_mic = swj_wn_bad = 0u;
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swj_wn_kd == 0u && swj_wn_pdu == 1u && swj_wn_mic == 1u);
	CHECK(swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0 && swj_wn_kd == 1u && swj_wn_pdu == 2u && swj_wn_mic == 3u && swj_wn_bad == 0u);       /* M3: pdu + mic of the function, kd + mk of the branch */
	{       /* a dropped M3 (bad MIC) leaves the branch through swj_drop: the key data is not there yet, the wipes still run */
		uint8_t m3b[sizeof V_M3];

		memcpy(m3b, V_M3, sizeof m3b);
		m3b[SWJ_ETH_HLEN + SWJ_OFF_MIC] ^= 1u;
		swj_wn_kd = 0u;
		RJX.h->sup.install_ptk = RJX.h->sup.install_gtk = 0;
		CHECK(swl25_rj_eapol(&RJX, m3b, sizeof m3b, out, sizeof out, &olen) == 0 && swj_wn_kd == 1u && swj_wn_bad == 0u);
	}

	/* R6d: the AP's BSSID cannot be read after the events: the attempt fails and no supplicant is set up; the key install refused: the attempt fails, the engine is NOT stopped and nothing else is sent */
	rj_world(1);
	LV.st_bssid = -1;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && RJX.eapol_on == 0u && has("WIFI GAP: keep rejoin: WLC_GET_BSSID status=-1") && has("attempt 1 failed (BSSID not read)") && AI.n_info == 0u);
	rj_world(1);                            /* an all-zero BSSID answer is no BSSID */
	memset(LV.bssid, 0, 6);
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && has("attempt 1 failed (BSSID not read)") && AI.n_info == 0u);
	memcpy(LV.bssid, V_AA, 6);
	rj_world(1);
	RJ.key_st = -5;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 210u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && nRC == 1u && strcmp(RC[0].name, "wsec_key") == 0 && has("WIFI GAP: keep rejoin: wsec_key pairwise refused status=-5") && has("attempt 1 failed (key install refused)"));
	CHECK(!x.d4dstop && !x.stopped && all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup) && swl25_H.hs_done == 0u && x.klink == 1u && RJX.up_new == 0u);
	rj_world(1);
	RJ.wsecset_st = -5;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	CHECK(swl25_rj_step(&x, &RJX, 210u + SWL25_RJ_TXWAIT_MS, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && has("attempt 1 failed (wsec merge refused)") && RJX.up_new == 0u && !x.d4dstop);

	/* R6e: the wsec read after the keys is refused: the attempt fails (the keys are in, the machine does not call it UP), nothing is merged */
	rj_world(1);
	RJ.get_st[1] = -5;       /* the read-back at the start is refused too: wsec is re-sent, which the stub accepts */
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && swl25_rj_eapol(&RJX, V_M3, sizeof V_M3, out, sizeof out, &olen) == 0);
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 210u + SWL25_RJ_TXWAIT_MS, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && nRC == 3u && has("attempt 1 failed (wsec get refused)") && RJX.up_new == 0u && !x.d4dstop);

	/* R7: the security iovars are read back and re-sent only when the firmware answers something else than the boot join set */
	rj_world(1);
	RJ.val[0] = 0u;                         /* wpa_auth lost */
	RJ.val[1] = SWJ_WSEC_AES | SWJ_WSEC_TKIP;   /* wsec: the cipher bits are not AES only */
	RJ.val[2] = 1u;                         /* auth lost */
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_JOINING);
	CHECK(nRC == 8u && strcmp(RC[0].name, "wpa_auth") == 0 && RC[1].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[1].name, "wpa_auth") == 0 && RC[1].argn == 4u && RC[1].wlen == 13u && RC[1].arg[0] == 0x80u && RC[1].arg[1] == 0u);
	CHECK(strcmp(RC[2].name, "wsec") == 0 && RC[2].cmd == SWD4B_WLC_GET_VAR && RC[3].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[3].name, "wsec") == 0 && RC[3].arg[0] == (uint8_t)SWJ_WSEC_AES && RC[3].wlen == 9u);
	CHECK(strcmp(RC[4].name, "auth") == 0 && RC[4].cmd == SWD4B_WLC_GET_VAR && RC[5].cmd == SWD4B_WLC_SET_VAR && strcmp(RC[5].name, "auth") == 0 && RC[5].arg[0] == 0u && RC[5].wlen == 9u);
	CHECK(strcmp(RC[6].name, "mfp") == 0 && RC[6].cmd == SWD4B_WLC_GET_VAR && strcmp(RC[7].name, "join") == 0 && RC[7].cmd == SWD4B_WLC_SET_VAR);       /* mfp survived: no SET */
	CHECK(has("keep rejoin: wpa_auth read back 0, the boot join set 0x80: re-sent") && has("keep rejoin: mfp survived (read back 0)") && has("keep rejoin: wsec read back 0x6, the boot join set 0x4: re-sent") && has("keep rejoin: auth read back 0x1, the boot join set 0: re-sent"));
	rj_world(1);                            /* a read-back that is refused or short is treated as lost: re-sent */
	RJ.get_st[2] = -5; RJ.short_get = 0;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 6u && strcmp(RC[3].name, "auth") == 0 && RC[3].cmd == SWD4B_WLC_SET_VAR && has("keep rejoin: auth read back refused (status -5, 4 B): re-sent"));
	rj_world(1);
	RJ.short_get = 1u;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 9u && has("keep rejoin: wpa_auth read back refused (status 0, 2 B): re-sent"));       /* all four re-sent (short answers), then join */
	rj_world(1);                            /* a re-send the firmware refuses: the attempt fails before the join, the join is never sent */
	RJ.val[3] = 1u; RJ.set_st[3] = -5;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && x.rj_state == SWG_RJ_DOWN && nRC == 5u && strcmp(RC[4].name, "mfp") == 0 && RC[4].cmd == SWD4B_WLC_SET_VAR && has("WIFI GAP: keep rejoin: mfp re-send refused status=-5") && has("attempt 1 failed (security iovars)"));
	for (i = 0; i < nRC; i++) {
		CHECK(strcmp(RC[i].name, "join") != 0);
	}

	/* R8: EAPOL gating. Only KEYING answers: DOWN, JOINING, UP and a wiped struct drop (-1, no reply); a frame that is not an EAPOL-Key is consumed without a reply */
	rj_world(1);
	CHECK(RJX.eapol_on == 0u && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == -1 && olen == 0u && RJX.eapol_rx == 0u);       /* DOWN */
	CHECK(swl25_rj_step(&x, &RJX, 100u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == -1 && olen == 0u);       /* JOINING */
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	rj_ev(SWG_E_EV_LINK, SWG_E_LINK_UP, 0u);
	CHECK(swl25_rj_step(&x, &RJX, 200u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && RJX.eapol_on == 1u);
	{
		uint8_t bad[60];

		memcpy(bad, V_M1, sizeof bad);
		bad[6] ^= 0x55u;                    /* not from the AP */
		CHECK(swl25_rj_eapol(&RJX, bad, sizeof bad, out, sizeof out, &olen) == 0 && olen == 0u && RJX.eapol_tx == 0u && RJX.eapol_rx == 1u && RJX.m4_seen == 0u);
	}
	CHECK(swl25_rj_eapol(&RJX, V_M1, sizeof V_M1, out, sizeof out, &olen) == 0 && olen == sizeof V_M2 && RJX.eapol_tx == 1u && RJX.m4_seen == 0u);       /* M2 is no M4 */

	/* R9: the engine dies: the kept secrets are zeroed, a later loss is a GAP with no engine call */
	CHECK(!all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup));       /* review 0282 F2 (probe P1): in KEYING after M1 the supplicant holds the PMK and the PTK */
	swl25_rj_wipe(&RJX, "test");
	CHECK(all_zero((const uint8_t *)&swl25_H.sup, sizeof swl25_H.sup) && all_zero((const uint8_t *)&RJX, sizeof RJX) && has("SWEET WLAN: keep rejoin: kept join payload and PMK wiped (test)"));
	loglen = 0u; logbuf[0] = '\0';
	swl25_rj_wipe(&RJX, "again");           /* nothing kept: no second line */
	CHECK(!has("kept join payload and PMK wiped"));
	x.rj_state = SWG_RJ_DOWN;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 1000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && has("the join payload and PMK are not kept"));

	/* R10: ring 2 not idle (here: the engine bus is down, swg_hrd fails): the attempt waits SWL25_RJ_DEFER_MS and is not counted */
	rj_world(1);
	x.stopped = 1;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 1000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u && RJX.n == 0u && RJX.t_next == 1000u + SWL25_RJ_DEFER_MS && has("keep rejoin: ring 2 not idle, the attempt waits 100 ms (not counted)"));
	x.stopped = 0;
	CHECK(swl25_rj_step(&x, &RJX, 1000u + SWL25_RJ_DEFER_MS - 1u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u);
	CHECK(swl25_rj_step(&x, &RJX, 1000u + SWL25_RJ_DEFER_MS, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && RJX.n == 1u && x.rj_state == SWG_RJ_JOINING);

	/* R11: the machine is idle while the link is UP, and a link-up event outside an attempt is a counted GAP that changes nothing (the 0281 behaviour for the case no attempt handles) */
	rj_world(1);
	x.rj_state = SWG_RJ_DOWN;
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);
	CHECK(x.rj_state == SWG_RJ_DOWN && x.kl_up_n == 1u && has("WIFI GAP: keep link UP event outside a re-join attempt ignored (type 0)") && x.rj_ssid == 0u);
	x.klink = 0u; x.rj_state = SWG_RJ_UP;
	nRC = 0u;
	CHECK(swl25_rj_step(&x, &RJX, 5000u, SWL25_RJ_BUSY_ARG(&RJX, 0), 1) == 0 && nRC == 0u);
	rj_ev(SWG_E_EV_SET_SSID, 0u, 0u);       /* UP and SET_SSID 0: nothing to say, no GAP */
	CHECK(x.kl_up_n == 1u && x.rj_state == SWG_RJ_UP);
	rj_soft_stops();
	rj_on = 0;
	test_bssget_hook();
}
