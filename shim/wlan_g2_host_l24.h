/* T-0a9d (R29): the scan24 helpers (count_of, scan_save_log, scan_run) and the L24 join cases (join_tests) of wlan_g2_host_test.c, moved out so the file does not grow; included by it at the place the text used to stand (one include, no guard) */
/* W-D4e (sweetwlan=21..23): the scan24 cases. Every case runs the generic fake dongle (gd) with the scan part (ge). The expected ring-0 words are restated with literals (epoch = (254 + idx) % 253). */
static unsigned
count_of(const char *needle)
{
	unsigned n = 0;
	const char *c;

	for (c = logbuf; (c = strstr(c, needle)) != NULL; c += strlen(needle)) {
		n++;
	}
	return n;
}

static void
scan_save_log(const char *name)
{
	const char *path = getenv("E_LOG");
	FILE *fh;

	if (path != NULL && strcmp(name, getenv("E_NAME") != NULL ? getenv("E_NAME") : "main") == 0 && (fh = fopen(path, "w")) != NULL) {
		fputs(logbuf, fh);
		fclose(fh);
	}
}

static void
scan_run(struct swd_plan *p, struct swg_wx *x, int lv, int *rc, const char *name)
{
	if (strcmp(name, "main") == 0 || strcmp(name, "rxoff") == 0 || strcmp(name, "l22") == 0 || strcmp(name, "ds") == 0 || strcmp(name, "ds0") == 0 || strcmp(name, "cap") == 0 || strcmp(name, "lifo") == 0 || strcmp(name, "evlifo") == 0 || strcmp(name, "clm") == 0 || strcmp(name, "clmref") == 0 ||
	    strcmp(name, "j24") == 0 || strcmp(name, "j24rev") == 0 || strcmp(name, "j24e1") == 0 || strcmp(name, "j24e62") == 0 || strcmp(name, "j24mac") == 0 || strcmp(name, "j24key") == 0) {      /* T-8ede: the L24 join streams (tools/test_wlan_wr_mutants.py L24_LABELS) */
		dump_label = name;      /* with G2_DUMP_STORES=1 the stores of these cases are printed for the glue replay (tools/test_wlan_wr_mutants.py SCAN24_LABELS) */
	}
	d4d_run(p, x, lv, IO_BOTH, 0, 0, rc);
	gd.on = 0;
	CHECK(ge.badpost == 0u);        /* T-fad2: no event buffer is posted outside the block, misaligned or twice */
	scan_save_log(name);
	if (getenv("E_DBG")) { fprintf(stderr, "DBG %s rc %d stop %d lv %u wr %u rd %u ds %u nbss %u evn %u evq %u evp %u ncpl %u restore %d\n", name, *rc, x->d4dstop, x->d4dlv, x->d4wr, x->d4rd, x->d4ds, x->nbss, x->evn, x->evq, x->evp, x->ncpl, x->restore); }
}

/* ---- L24 (sweetwlan=24, TRANSPORT block s.2): H1-H5 as ring-0 ioctls, the join event mask, the SET_SSID / LINK wait ---- */
static struct swg_jcmd jcs[12];       /* more than SWG_J_NCMAX in every mutant of it (T-4e39) */
static uint32_t jcn;

/* the commands the glue hands the engine: swj_join_cmds' six (sweet_wlan_join.h), copied; `key` inserts that many 173 B wsec_key SET_VARs (name 9 B + 164 B, canary bytes) before the join */
static void
jc_build(int key)
{
	static const uint8_t ssid[] = "pixos-test", bssid[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };
	struct swj_cmd c[6];
	uint32_t i, k = 0;
	int n;

	memset(jcs, 0, sizeof jcs);
	CHECK(swj_join_cmds(c, ssid, 10u, bssid, 0) == 6u);
	for (i = 0; i < 6u; i++) {
		for (n = 0; i == 5u && n < key; n++) {
			CHECK(k < 11u);
			memset(&jcs[k], 0, sizeof jcs[k]);
			jcs[k].cmd = c[i].cmd;
			jcs[k].row = "H8";
			memcpy(jcs[k].buf, "wsec_key", 9);
			memset(jcs[k].buf + 9, 0x5A, 164);     /* the canary: every word reads 0x5a5a5a5a */
			jcs[k].buf[9 + 4] = 0xC3; jcs[k].buf[9 + 5] = 0x3C; jcs[k].buf[9 + 6] = 0xC3; jcs[k].buf[9 + 7] = 0x3C;      /* word 0x3cc33cc3 */
			jcs[k].len = 9u + 164u;
			k++;
		}
		CHECK(c[i].len <= sizeof jcs[k].buf);
		jcs[k].cmd = c[i].cmd;
		jcs[k].row = c[i].row;
		memcpy(jcs[k].buf, c[i].buf, c[i].len);
		jcs[k].len = c[i].len;
		k++;
	}
	jcn = k;
}

/* T-857c knob A: swj_join_cmds' six plus the cur_etheraddr command the glue inserts as command 1 (swj_join_cmds_mac): seven commands, copied the way jc_build does; `name_x` renames the command to a non-secret name of the same length (the positive control for the withheld payload) */
static void
jc_build_mac(const uint8_t mac[6], int name_x)
{
	static const uint8_t ssid[] = "pixos-test", bssid[6] = { 0x02, 0x11, 0x22, 0x33, 0x44, 0x55 };
	struct swj_cmd c[7];
	uint32_t i;

	memset(jcs, 0, sizeof jcs);
	CHECK(swj_join_cmds(c, ssid, 10u, bssid, 0) == 6u);
	CHECK(swj_join_cmds_mac(c, 6u, mac) == 7u);
	if (name_x) {
		c[1].buf[12] = 'X';
	}
	for (i = 0; i < 7u; i++) {
		CHECK(c[i].len <= sizeof jcs[i].buf);
		jcs[i].cmd = c[i].cmd;
		jcs[i].row = c[i].row;
		memcpy(jcs[i].buf, c[i].buf, c[i].len);
		jcs[i].len = c[i].len;
	}
	jcn = 7u;
}

static void
l24_run(struct swd_plan *p, struct swg_wx *x, int *rc, const struct swg_jcmd *jc, uint32_t njc, const char *name)
{
	t_jc = jc;
	t_njc = njc;
	scan_run(p, x, 24, rc, name);
	t_jc = NULL;
	t_njc = 0u;
}

static uint8_t clm_blob[16900];      /* defined with the L22c cases below */
static void clm_fill(void);

static void
l24_arm(int jmode)
{
	gd_arm();
	clm_fill();             /* T-4632: sweetwlan=24 is L22c + L23 + L24, so every L24 case hands the shim the CLM blob (15,932 B, 12 chunks) */
	tclm = clm_blob;
	tclmlen = 15932u;
	ge.lifo = 1;            /* T-4632: the dongle answers with the NEWEST posted response buffer and delivers into the NEWEST posted event buffer (T-af77 boot 3, T-fad2 boot 8): a FIFO glue entry fails every L24 case; "j24fifo" keeps the oldest-first order too */
	ge.evlifo = 1;
	ge.jmode = jmode;
	tpr[0] = 0x00110010u;   /* LNKCTL/LNKSTA: link up (a test value, not an observation) */
	tpr[1] = 0x13C10001u;   /* AER header as observed (research 36 HO:218) */
	tpr[2] = 0u;            /* AER UESta clean */
	tpr_fail = 0u;
	tspin_ones = tspin_on = 0;
	tdb_defer = tdb_left = tdb_pend = 0u;
	tdb_seen = 0;
	memset(npr_rd, 0, sizeof npr_rd);
}

static int
l24_sent(uint32_t i, const struct swg_jcmd *c)
{
	return i < ge.jn && memcmp(ge.jbuf[i], c->buf, c->len) == 0;
}

static void
join_tests(struct swd_plan *p)
{
	struct swg_wx x;
	uint32_t base_wr, i, ms_b;
	int rc;
	static const char *const nm[6] = { "sup_wpa", "wpa_auth", "wsec", "auth", "mfp", "join" };

	jc_build(0);
	/* (A) the scan of L23 alone ends at ring-0 index base_wr; L24 adds 7 requests + 7 response re-posts + 2 event re-posts */
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "j23");
	base_wr = x.d4wr + 28u;       /* + the 14 CLM requests (12 chunks, clmload_status, country) x (request + response re-post) of sweetwlan=24 */
	CHECK(rc == 0 && x.d4dlv == 23u && ge.jn == 0u && !has("L24"));
	/* T-eabd: constant 9000 B of room: the first sample of each of the three engine sites prints once, no other line, the minimum stays 9000 */
	CHECK(has("SWEET WLAN: stack headroom d4d 9000\n") && has("SWEET WLAN: stack headroom e_items 9000\n") && has("SWEET WLAN: stack headroom d4d_item 9000\n") && count_of("SWEET WLAN: stack headroom ") == 3u && x.hr_n > 3u && x.hr_min == 9000u && !has("headroom d4d unknown"));
	/* shrinking room (8 B per call): every sample is a new minimum and prints, the minimum is the last sample */
	room_dec = 8u;
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "j23");
	room_dec = 0u;
	CHECK(rc == 0 && x.hr_n > 3u && count_of("SWEET WLAN: stack headroom ") == x.hr_n && x.hr_min == 9000u - 8u * (x.hr_n - 1u));
	/* not readable: `unknown` once per site (Rule 8), a gap, no sample taken, the run itself is unchanged */
	room_off = 1;
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "j23");
	room_off = 0;
	CHECK(rc == 0 && x.d4dlv == 23u && x.hr_n == 0u && has("SWEET WLAN: stack headroom d4d unknown\n") && has("SWEET WLAN: stack headroom e_items unknown\n") && has("SWEET WLAN: stack headroom d4d_item unknown\n") && count_of("SWEET WLAN: stack headroom ") == 3u && swl_gap_tot >= 3u);
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "j23");
	/* (B) the whole sequence, events inside the join ioctl: six join-class SET_VARs in order with the reviewed payloads byte for byte, the mask with bits 69, 0, 16 */
	l24_arm(0);
	l24_run(p, &x, &rc, jcs, jcn, "j24");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.restore == 1 && x.ej == NULL && x.esec == 0);
	CHECK(ge.jn == 6u && ge.nescan == 1u && ge.lost == 0u && x.evq == 0u);
	/* T-0fdd: at level 24 the escan carries the 7 bring-up channels: channel_num 7 at params +68, the chanspecs at params +72 (14 B, zero padded to 16), params length still 72, payload 6 + 96 = 102 B */
	{
		static const uint8_t esc24[96] = { 2, 0, 0, 0, 1, 0, 0x34, 0x12, 2, 0, 72, 0, [48] = 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 2, 0, 1, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
			7, 0, 0, 0, 0x01, 0x10, 0x06, 0x10, 0x0B, 0x10, 0x24, 0xD0, 0x28, 0xD0, 0x2C, 0xD0, 0x30, 0xD0, 0, 0 };

		CHECK(ge.escin == 102u && memcmp(ge.esc, esc24, 96) == 0);
		CHECK(has("SWEET WLAN: L23 escan start: version 2 action 1 sync 0x1234 passive 7 bring-up channels (1 6 11 36 40 44 48), status 0\n") && has("WIFI GAP: scan escan limited to 7 bring-up channels x1 [low]"));
	}
	for (i = 0; i < 6u; i++) {
		CHECK(strcmp(ge.jname[i], nm[i]) == 0 && l24_sent(i, &jcs[i]));
	}
	CHECK(ge.ems[0] == 1u && ge.ems[1] == 3u && ge.ems[2] == 26u && ge.ems[4] == 0x01u && ge.ems[4 + 2] == 0x01u && ge.ems[4 + 8] == 0x20u);
	{
		unsigned nz = 0;

		for (i = 4; i < 30u; i++) {
			nz += ge.ems[i] != 0u;
		}
		CHECK(nz == 3u);       /* nothing but the three bits */
	}
	CHECK(x.d4wr == base_wr + 14u + 2u && x.d4rd > 23u);
	CHECK(has("SWEET WLAN: L24 event_msgs_ext set: bits 69, 0, 16 on, status 0\n") && has("SWEET WLAN: sup_wpa set 0 ok\n") && has("SWEET WLAN: L24 H1 sup_wpa status 0\n") && has("SWEET WLAN: L24 H4 mfp status 0\n") && has("SWEET WLAN: L24 H5 join status 0\n"));
	CHECK(has("SWEET WLAN: join set_ssid status=0\n") && has("SWEET WLAN: join link up\n") && has("SWEET WLAN: L24 join done: SET_SSID status 0, link up, ring-0 WR ") && has("SWEET WLAN: D4d L24 done (ring-0 WR "));
	CHECK(strstr(logbuf, "join set_ssid status=0\n") < strstr(logbuf, "join link up\n") && !has("WIFI GAP: H") && !has("pixos-test") && !has("02:11:22:33:44:55") && !has("STOP"));
	/* T-4e39: the mask request is 15 B name + 30 B value; no payload of a run without a key iovar is withheld; the sup_wpa line comes once; the events are told at level 24 */
	CHECK(ge.emslen == 45u && !has("(value withheld)") && count_of("SWEET WLAN: sup_wpa set 0 ok\n") == 1u && count_of("SWEET WLAN: L24 event item ") == 2u);
	/* T-5323 W-A + W-C: the quiet wait comes BEFORE the mask request and ends after 500 ms of an idle ring 2 (5 polls); the heartbeat lines come after the doorbell and per poll */
	CHECK(has("SWEET WLAN: L24 quiet wait 500 ms (0 busy polls, ring-2 RD ") && has("): ring 2 quiet\n") && !has("quiet wait cap"));
	{
		const char *q = strstr(logbuf, "L24 quiet wait"), *r = q != NULL ? strstr(q, "D4d request WLC_SET_VAR event_msgs_ext") : NULL;      /* the L22 SET came earlier: search from the quiet line on */

		CHECK(q != NULL && r != NULL && strstr(r, "L24 hb: WLC_SET_VAR event_msgs_ext posted, doorbell written, polling ring 2") != NULL && strstr(q, "G2 store d4d db0 (h2d wr)") > r);
	}
	CHECK(has("SWEET WLAN: L24 hb poll 0: d2h_w ") && count_of("SWEET WLAN: L24 hb: WLC_SET_VAR ") == 7u && count_of("SWEET WLAN: L24 hb: WLC_SET_VAR join posted") == 1u);
	CHECK(!has("SWEET WLAN: L23 hb") && !has("SWEET WLAN: L22 hb") && !has("hb poll 40:"));       /* L24 only; at most SWG_HB_POLLS lines per wait */
	/* T-c3ac H1 + H2: the PCIe probe pair around the FIRST L24 doorbell only (reads, four registers each) and the per-ms spin in the first poll delay */
	{
		const char *q0 = strstr(logbuf, "L24 quiet wait"), *pre = strstr(logbuf, "L24 probe pre: begin"), *rq = q0 != NULL ? strstr(q0, "D4d request WLC_SET_VAR event_msgs_ext") : NULL, *db = rq != NULL ? strstr(rq, "G2 store d4d db0 (h2d wr)") : NULL;
		const char *hb = rq != NULL ? strstr(rq, "L24 hb: WLC_SET_VAR event_msgs_ext posted") : NULL, *po = strstr(logbuf, "L24 probe post: begin"), *hp = po != NULL ? strstr(po, "L24 hb poll 0:") : NULL;

		CHECK(q0 != NULL && pre != NULL && rq != NULL && db != NULL && hb != NULL && po != NULL && hp != NULL);
		CHECK(q0 < pre && pre < rq && rq < db && db < hb && hb < po && po < hp);        /* pre before the request, post after the doorbell and its hb line, hb poll 0 after both */
	}
	CHECK(count_of("SWEET WLAN: L24 probe pre: begin") == 1u && count_of("SWEET WLAN: L24 probe post: begin") == 1u && count_of("SWEET WLAN: L24 probe ") == 10u);       /* 2 begin lines + 2 x 4 registers; no later doorbell is probed */
	CHECK(has("SWEET WLAN: L24 probe pre: ep cfg 0x4 (Command/Status) = ") && has("SWEET WLAN: L24 probe post: ep cfg 0xbc (LNKCTL/LNKSTA) = 0x110010\n") && has("SWEET WLAN: L24 probe post: ep cfg 0x100 (AER header) = 0x13c10001\n") && has("SWEET WLAN: L24 probe post: ep cfg 0x104 (AER UESta) = 0\n"));
	CHECK(npr_rd[0] == 2u && npr_rd[1] == 2u && npr_rd[2] == 2u && !has("READ FAILED") && !has("WIFI GAP: l24pr") && !has("AER UESta 0x"));
	CHECK(count_of("SWEET WLAN: L24 spin ") == 0u);     /* the instant dongle answers inside the first poll: no delay, so no spin, and the flag does not leak to a later wait */
	/* the dongle answers 37 host delays after the doorbell, as the real one took ~107 ms: the first poll delay is the spin, the answer shows in its lines from ms 40 on, the run goes on */
	l24_arm(0);
	tdb_defer = 37u;
	l24_run(p, &x, &rc, jcs, jcn, "j24spin");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: join link up\n") && !has("WIFI GAP: l24pr"));
	{
		const char *po2 = strstr(logbuf, "L24 probe post: begin"), *hp = po2 != NULL ? strstr(po2, "L24 hb poll 0:") : NULL, *sp = strstr(logbuf, "L24 spin ms 1:"), *sd = strstr(logbuf, "L24 spin done"), *h1 = hp != NULL ? strstr(hp + 1, "L24 hb poll 1:") : NULL;

		CHECK(hp != NULL && sp != NULL && sd != NULL && h1 != NULL && hp < sp && sp < sd && sd < h1);       /* the spin replaces the delay between hb poll 0 and hb poll 1 */
	}
	CHECK(count_of("SWEET WLAN: L24 spin ms ") == 19u && has("SWEET WLAN: L24 spin ms 10: d2h_w ") && has("SWEET WLAN: L24 spin ms 100: d2h_w ") && !has("L24 spin ms 11:") && !has("L24 spin ms 101:") && count_of("SWEET WLAN: L24 spin done: 100 ms") == 1u);
	{
		const char *a30 = strstr(logbuf, "L24 spin ms 30: d2h_w "), *a40 = strstr(logbuf, "L24 spin ms 40: d2h_w ");
		unsigned v30 = 0, v40 = 0;

		CHECK(a30 != NULL && a40 != NULL && sscanf(a30, "L24 spin ms 30: d2h_w %x", &v30) == 1 && sscanf(a40, "L24 spin ms 40: d2h_w %x", &v40) == 1 && v30 != v40);       /* the host word moved between ms 30 and 40: the spin reads it live */
	}
	CHECK(count_of("SWEET WLAN: L24 probe ") == 10u);
	ms_b = f.msdelay;
	/* a late event inside the quiet wait (at the 2nd and the 4th delay after it started): the quiet time restarts, the wait ends 500 ms after the last one, no gap; the join still passes */
	l24_arm(0);
	ge.qlate[0] = 2u;
	ge.qlate[1] = 4u;
	l24_run(p, &x, &rc, jcs, jcn, "j24busy");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: L24 quiet wait ") && !has("L24 quiet wait 500 ms") && has(" busy polls, ring-2 RD ") && !has("(0 busy polls") && !has("quiet wait cap") && !has("WIFI GAP: H0") && has("SWEET WLAN: join link up\n"));
	ge.qlate[0] = 0u;
	ge.qlate[1] = 0u;
	/* T-c3ac: a set AER uncorrectable status (completion timeout, bit 14) after the doorbell is told and a loud gap; the probe stores nothing and stops nothing, the join still passes */
	l24_arm(0);
	tpr[2] = 1u << 14;
	l24_run(p, &x, &rc, jcs, jcn, "j24aer");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: L24 probe post: AER UESta 0x4000: completion timeout (bit 14) 1, unsupported request (bit 20) 0\n") && has("WIFI GAP: l24pr L24 probe: AER uncorrectable status is set x2 [high]") && has("SWEET WLAN: join link up\n"));
	/* an endpoint config read that fails, and one that reads all ones: told, a gap, no stop */
	l24_arm(0);
	tpr_fail = 1u + 0xBCu;
	l24_run(p, &x, &rc, jcs, jcn, "j24prfail");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: L24 probe pre: ep cfg 0xbc (LNKCTL/LNKSTA) READ FAILED\n") && has("SWEET WLAN: L24 probe post: ep cfg 0xbc (LNKCTL/LNKSTA) READ FAILED\n") && has("WIFI GAP: l24pr L24 probe: an endpoint config read failed x2 [high]") && has("SWEET WLAN: join link up\n"));
	l24_arm(0);
	tpr[0] = 0xFFFFFFFFu;
	l24_run(p, &x, &rc, jcs, jcn, "j24prones");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: L24 probe post: ep cfg 0xbc reads all ones: the endpoint does not answer") && has("WIFI GAP: l24pr L24 probe: endpoint config reads all ones x2 [high]") && has("SWEET WLAN: join link up\n"));
	/* the spin reads the host word: all ones there is a dead link and a stop (no store follows) */
	l24_arm(0);
	tspin_ones = 1;
	tdb_defer = 37u;
	l24_run(p, &x, &rc, jcs, jcn, "j24spinones");
	CHECK(rc == -SWG_E_DEAD && x.stopped && has("SWEET WLAN: L24 spin ms 1: d2h_w 0xffffffff\n") && has("host d2h_w[0] reads all ones (dead link) in the L24 spin") && !has("L24 spin done") && !has("join link up"));
	/* T-c3ac: the dongle answers 105 host delays after the doorbell (100 spin steps + 5 plain poll delays): the spin runs ONCE (the first poll delay only), the later poll delays are plain */
	l24_arm(0);
	tdb_defer = 105u;
	l24_run(p, &x, &rc, jcs, jcn, "j24spin2");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && count_of("SWEET WLAN: L24 spin done: 100 ms") == 1u && count_of("SWEET WLAN: L24 spin ms 1:") == 1u && has("SWEET WLAN: join link up\n"));
	/* (C) the same, the events come after the completion (the wait loop judges them) */
	l24_arm(0);
	ge.jlate = 3u;
	l24_run(p, &x, &rc, jcs, jcn, "j24late");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.d4wr == base_wr + 14u + 2u && has("SWEET WLAN: join set_ssid status=0\n") && has("SWEET WLAN: join link up\n") && !has("WIFI GAP: H5") && count_of("SWEET WLAN: L24 spin ") == 0u);       /* T-c3ac: an instant first answer leaves no spin armed for the later waits */
	/* LINK before SET_SSID is still both events: done */
	l24_arm(4);
	l24_run(p, &x, &rc, jcs, jcn, "j24rev");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("SWEET WLAN: join link up\n") && has("SWEET WLAN: join set_ssid status=0\n"));
	/* T-8ede: the fewest and the most L23 event re-posts before the join (npartial 0: 1 + 2 re-posts; npartial 61: 62 + 2 = the glue's cap SW_G2_E_NREP 64), dumped for the glue replay */
	l24_arm(0);
	ge.npartial = 0u;
	l24_run(p, &x, &rc, jcs, jcn, "j24e1");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.nbss == 0u && has("SWEET WLAN: join link up\n") && !has("WIFI GAP: H5"));
	l24_arm(0);
	ge.npartial = 45u;      /* the most partial results the event cap of sweetwlan=24 (SWG_E_MAX_CLM 48 events consumed) lets through; the label keeps the 62 events of the sweetwlan=24-without-CLM stream (T-4632) */
	l24_run(p, &x, &rc, jcs, jcn, "j24e62");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.nbss == 45u && has("SWEET WLAN: join link up\n") && !has("WIFI GAP: H5"));
	l24_arm(0);
	ge.npartial = 46u;      /* one more: the cap of 48 is reached, a loud stop, no join */
	l24_run(p, &x, &rc, jcs, jcn, "j24cap");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("WIFI GAP: scan event cap 48 reached") && !has("join link up"));
	l24_arm(0);
	ge.lifo = 0;            /* oldest first: the shim and the list accept either order */
	ge.evlifo = 0;
	l24_run(p, &x, &rc, jcs, jcn, "j24fifo");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.d4wr == base_wr + 14u + 2u && has("SWEET WLAN: join link up\n") && !has("WIFI GAP: H5"));
	/* (D) SET_SSID status != 0: the fixed GAP, a stop, the level stays 23 */
	l24_arm(1);
	ge.jst = 3u;
	l24_run(p, &x, &rc, jcs, jcn, "j24ssid");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && x.restore == 1 && has("SWEET WLAN: join set_ssid status=3\n") && has("WIFI GAP: H5 join set_ssid status=3 x1 [high]") && !has("join link up") && !has("L24 join done"));
	CHECK(has("SWEET WLAN: L24 STOP, no further store: H5 join set_ssid status=3\n") && f.msdelay < ms_b + 5000u);       /* T-4e39: a bad status ends the wait at once, the level is told as 24 */
	/* (E) no event at all: the fixed GAP with the milliseconds waited (10 s budget) */
	l24_arm(2);
	l24_run(p, &x, &rc, jcs, jcn, "j24none");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("WIFI GAP: H5 join no SET_SSID/LINK event in 10000 ms x1 [high]") && !has("L24 join done"));
	/* a LINK event with the up flag clear is not LINK up */
	l24_arm(3);
	l24_run(p, &x, &rc, jcs, jcn, "j24down");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("SWEET WLAN: join link event, link not up (flags 0)\n") && has("WIFI GAP: H5 join no SET_SSID/LINK event in 10000 ms x1 [high]") && !has("join link up"));
	/* SET_SSID alone (no LINK) is not done either */
	l24_arm(1);
	ge.jst = 0u;
	l24_run(p, &x, &rc, jcs, jcn, "j24ssidonly");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("SWEET WLAN: join set_ssid status=0\n") && has("WIFI GAP: H5 join no SET_SSID/LINK event in 10000 ms x1 [high]"));
	/* (F) every join ioctl can be refused: a stop at that command, the later ones are not sent, the fixed text */
	{
		static const struct { const char *name; uint32_t sent; const char *gap; } fl[6] = {
			{ "sup_wpa", 1u, "WIFI GAP: H1 sup_wpa set 0 refused status=-5 x1 [high]" },
			{ "wpa_auth", 2u, "WIFI GAP: H2 wpa_auth refused status=-5 x1 [high]" },
			{ "wsec", 3u, "WIFI GAP: H3 wsec refused status=-5 x1 [high]" },
			{ "auth", 4u, "WIFI GAP: H4 auth refused status=-5 x1 [high]" },
			{ "mfp", 5u, "WIFI GAP: H4 mfp refused status=-5 x1 [high]" },
			{ "join", 6u, "WIFI GAP: H5 join ioctl refused status=-5 x1 [high]" },
		};

		for (i = 0; i < 6u; i++) {
			l24_arm(0);
			snprintf(ge.jfail, sizeof ge.jfail, "%s", fl[i].name);
			ge.jfst = -5;
			l24_run(p, &x, &rc, jcs, jcn, "j24refused");
			CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && ge.jn == fl[i].sent && has(fl[i].gap) && !has("L24 join done") && !has("join link up") && x.ej == NULL);
		}
	}
	l24_arm(0);
	snprintf(ge.jfail, sizeof ge.jfail, "sup_wpa");
	ge.jfst = -5;
	l24_run(p, &x, &rc, jcs, jcn, "j24h1");
	CHECK(!has("SWEET WLAN: sup_wpa set 0 ok\n") && has("SWEET WLAN: L24 H1 sup_wpa status -5\n"));
	/* T-9c82: sup_wpa answers -23 (BCME_UNSUPPORTED, the iovar is absent): a medium gap + a line, no stop, H2..H5 are all sent in order and the join passes; other H2..H5 refusals (-23 included) still stop */
	l24_arm(0);
	snprintf(ge.jfail, sizeof ge.jfail, "sup_wpa");
	ge.jfst = -23;
	l24_run(p, &x, &rc, jcs, jcn, "j24h1unsupp");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && ge.jn == 6u && !has("STOP") && !has("SWEET WLAN: sup_wpa set 0 ok\n") && has("SWEET WLAN: L24 H1 sup_wpa status -23\n") && has("SWEET WLAN: L24 H1 sup_wpa not present, continuing\n") && has("WIFI GAP: H1 sup_wpa iovar not present (status -23), skipped x1 [medium]"));
	for (i = 0; i < 4u; i++) {
		l24_arm(0);
		snprintf(ge.jfail, sizeof ge.jfail, "%s", nm[i + 1u]);
		ge.jfst = -23;
		l24_run(p, &x, &rc, jcs, jcn, "j24h2unsupp");
		CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && ge.jn == i + 2u && has("L24 STOP, no further store: H") && !has("sup_wpa not present") && !has("L24 join done"));
	}
	/* (G) no commands, too few, too many, a malformed one, a join that is not last: nothing is sent, a GAP / STOP, the scan result stays */
	l24_arm(0);
	l24_run(p, &x, &rc, NULL, 0u, "j24nojc");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && ge.jn == 0u && has("WIFI GAP: H5 join not run: no join commands (SSID/credentials not delivered) x1 [high]") && has("SWEET WLAN: L24 not run: no join commands (0 of 6)\n") && !has("L24 event_msgs_ext") && ge.ems[4] != 0x01u);
	l24_arm(0);
	l24_run(p, &x, &rc, jcs, 5u, "j24five");
	CHECK(rc == 0 && x.d4dlv == 23u && ge.jn == 0u && has("WIFI GAP: H5 join not run") && has("(5 of 6)"));
	l24_arm(0);
	jcs[2].cmd = 262u;
	l24_run(p, &x, &rc, jcs, jcn, "j24badcmd");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && ge.jn == 0u && has("WIFI GAP: H5 join command 2 malformed, nothing sent") && !has("L24 event_msgs_ext"));
	jc_build(0);
	l24_arm(0);
	memcpy(jcs[5].buf, "joiN", 5);
	l24_run(p, &x, &rc, jcs, jcn, "j24nojoin");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("WIFI GAP: H5 join command 5 malformed, nothing sent"));
	jc_build(0);
	l24_arm(0);
	jcs[1].len = 0u;
	l24_run(p, &x, &rc, jcs, jcn, "j24len0");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("join command 1 malformed"));
	/* ---- T-4e39: the survivors of the mutant run t8bee-g2 ---- */
	jc_build(0);
	/* LINK up alone (no SET_SSID) is not done, inside the ioctl and late (events judged by the wait loop): the wait runs its whole 10 s budget, once (not 10 s per round), then the fixed gap */
	for (i = 0; i < 2u; i++) {
		l24_arm(5);
		ge.jlate = i == 0u ? 0u : 3u;
		l24_run(p, &x, &rc, jcs, jcn, "j24linkonly");
		CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("SWEET WLAN: join link up\n") && has("WIFI GAP: H5 join no SET_SSID/LINK event in 10000 ms x1 [high]") && !has("L24 join done") && !has("join set_ssid"));
	}
	/* a LINK event with flag bit 0 clear but another bit set is not LINK up */
	l24_arm(6);
	l24_run(p, &x, &rc, jcs, jcn, "j24flag2");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("SWEET WLAN: join link event, link not up (flags 0x2)\n") && has("WIFI GAP: H5 join no SET_SSID/LINK event in 10000 ms x1 [high]") && !has("join link up") && !has("L24 join done"));
	/* the write index falls far below the read count while the join waits: the fixed stop, not a silent wait */
	l24_arm(2);
	ge.jlow = 2u;
	l24_run(p, &x, &rc, jcs, jcn, "j24low");
	CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && has("WIFI GAP: scan d2h write index ") && has(" went below the read count ") && !has("no SET_SSID/LINK event") && !has("L24 join done"));
	/* the scan of level 23 did not finish (no SUCCESS): level 24 is not run, nothing is sent */
	l24_arm(0);
	ge.pfin = -2;
	l24_run(p, &x, &rc, jcs, jcn, "j24nodone");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 22u && ge.jn == 0u && has("WIFI GAP: scan no SUCCESS in 8000 ms") && !has("L24 event_msgs_ext") && !has("L24 H") && !has("L24 not run") && !has("WIFI GAP: H"));
	/* the command count: 8 (the maximum: two key iovars) runs, 9 are refused with nothing sent; a NULL list is refused whatever the count says */
	jc_build(2);
	CHECK(jcn == 8u);
	l24_arm(0);
	l24_run(p, &x, &rc, jcs, jcn, "j24eight");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && ge.jn == 8u && strcmp(ge.jname[7], "join") == 0 && has("(value withheld)\n"));
	jc_build(3);
	CHECK(jcn == 9u);
	l24_arm(0);
	l24_run(p, &x, &rc, jcs, jcn, "j24nine");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && ge.jn == 0u && has("WIFI GAP: H5 join not run") && has("(9 of 6)"));
	jc_build(0);
	l24_arm(0);
	l24_run(p, &x, &rc, NULL, 7u, "j24nullcmds");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 23u && ge.jn == 0u && has("WIFI GAP: H5 join not run") && has("(0 of 6)"));
	/* a malformed command: an empty name, a length above the buffer, a name without its NUL, the names "joinX" for the last one: refused before the first send */
	l24_arm(0);
	jcs[1].buf[0] = 0u;
	l24_run(p, &x, &rc, jcs, jcn, "j24empty");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("WIFI GAP: H5 join command 1 malformed, nothing sent"));
	jc_build(0);
	l24_arm(0);
	jcs[1].len = (uint32_t)sizeof jcs[1].buf + 4u;
	l24_run(p, &x, &rc, jcs, jcn, "j24toolong");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("WIFI GAP: H5 join command 1 malformed, nothing sent"));
	jc_build(0);
	l24_arm(0);
	memset(jcs[1].buf, 'A', sizeof jcs[1].buf);
	jcs[1].len = (uint32_t)sizeof jcs[1].buf;
	l24_run(p, &x, &rc, jcs, jcn, "j24nonul");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("WIFI GAP: H5 join command 1 malformed, nothing sent"));
	jc_build(0);
	l24_arm(0);
	memcpy(jcs[5].buf, "joinX", 6);
	l24_run(p, &x, &rc, jcs, jcn, "j24joinx");
	CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("WIFI GAP: H5 join command 5 malformed, nothing sent"));
	/* a command named "wsec_keyX" is not a key iovar: its payload words are printed */
	jc_build(1);
	l24_arm(0);
	jcs[5].buf[8] = 'X';
	jcs[5].buf[9] = 0u;
	l24_run(p, &x, &rc, jcs, jcn, "j24keyx");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("0x5a5a5a5a") && !has("(value withheld)"));
	/* T-857c knob A: seven commands, cur_etheraddr as command 1 between sup_wpa and wpa_auth: all seven are sent in order with the exact payload, the MAC bytes are withheld from the log (a key-class payload), the engine
	 * stops on a refused SET (non-zero status) with the row H1m, and the same command under a non-secret name prints its words (positive control) */
	{
		static const uint8_t mac[6] = { 0x3c, 0x5a, 0xb4, 0x0f, 0xa1, 0x09 };
		static const char *const nm7[7] = { "sup_wpa", "cur_etheraddr", "wpa_auth", "wsec", "auth", "mfp", "join" };

		jc_build_mac(mac, 0);
		l24_arm(0);
		l24_run(p, &x, &rc, jcs, jcn, "j24mac");
		CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.restore == 1 && ge.jn == 7u && ge.lost == 0u);
		for (i = 0; i < 7u; i++) {
			CHECK(strcmp(ge.jname[i], nm7[i]) == 0 && l24_sent(i, &jcs[i]));
		}
		CHECK(memcmp(ge.jbuf[1] + 14, mac, 6) == 0);
		CHECK(has("SWEET WLAN: L24 H1m cur_etheraddr status 0\n") && has("SWEET WLAN: L24 join done: ") && has("(value withheld)\n") && !has("0x9a10fb4") && !has("0x5a3c0072") && !has("WIFI GAP: H") && !has("STOP"));
		CHECK(count_of("SWEET WLAN: L24 hb: WLC_SET_VAR ") == 8u && x.d4wr == base_wr + 16u + 2u);     /* one more request + response re-post than the six-command run */
		jc_build_mac(mac, 1);
		l24_arm(0);
		l24_run(p, &x, &rc, jcs, jcn, "j24macx");
		CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && has("0x9a10fb4") && !has("(value withheld)"));      /* the fake dongle does not know the renamed command, so ge.jn is not checked */
		jc_build_mac(mac, 0);
		l24_arm(0);
		snprintf(ge.jfail, sizeof ge.jfail, "cur_etheraddr");
		ge.jfst = -5;     /* the firmware refuses the SET */
		l24_run(p, &x, &rc, jcs, jcn, "j24macrefused");
		CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && ge.jn == 2u && has("WIFI GAP: H1m cur_etheraddr refused status=-5 x1 [high]") && !has("L24 join done") && !has("0x9a10fb4"));
		/* review T-857c F4: seven commands with the event cap at its maximum still end below ring-0 depth 128 (measured 126) */
		jc_build_mac(mac, 0);
		l24_arm(0);
		ge.npartial = 45u;
		l24_run(p, &x, &rc, jcs, jcn, "j24macmax");
		CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && x.d4wr <= 126u && has("SWEET WLAN: join link up\n"));
	}
	/* the command is checked before anything is sent and before the ring is looked at again: with a deep-sleep request pending, a refused list answers nothing (no store after a stop) */
	jc_build(0);
	jcs[2].cmd = 262u;
	l24_arm(0);
	scan_run(p, &x, 23, &rc, "j23pend");
	CHECK(rc == 0 && x.d4dlv == 23u && x.d4rd > 0u);
	{
		const uint32_t rd0 = x.d4rd, wr0 = x.d4wr;

		gd_put(0x24u, 0u, 0, 2u, 0u);       /* a DS_ENTER_REQ is pending on ring 2 */
		x.jc = jcs;
		x.njc = jcn;
		x.elv = 24u;
		gd.on = 1;
		rc = swg_e_l24(&x, 24u);
		gd.on = 0;
		CHECK(rc == 0 && x.d4dstop && x.d4dlv == 23u && x.d4rd == rd0 && x.d4wr == wr0 && ge.jn == 0u && has("WIFI GAP: H5 join command 2 malformed, nothing sent") && !has("D4 DS round"));
	}
	/* the payload bound of one call: 1420 B (the shadow, 355 words: a clmload chunk) go out whole, 1421 B and 1424 B are refused with nothing sent */
	{
		static uint8_t big[1500];
		struct swd4b_ioctl_d2h cm;
		static const uint32_t wl[3] = { 1420u, 1421u, 1424u };

		memset(big, 0x11, sizeof big);
		for (i = 0; i < 3u; i++) {
			l24_arm(0);
			scan_run(p, &x, 23, &rc, "j23bound");
			CHECK(rc == 0 && x.d4dlv == 23u);
			gd.on = 1;
			rc = swg_e_call(&x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR wsec_key", "wsec_key", big, wl[i] - 9u, wl[i], 1, 0u, NULL, 1, &cm, NULL);
			gd.on = 0;
			if (i == 0u) {
				CHECK(rc == 0 && !x.d4dstop && cm.status == 0 && ge.jn == 1u && ge.jbuf[0][1419] == 0x11u && !has("above the 1420 B shadow"));
			} else {
				CHECK(rc == 0 && x.d4dstop && ge.jn == 0u && has("payload 1421 B above the 1420 B shadow") + has("payload 1424 B above the 1420 B shadow") == 1);
			}
		}
	}
	/* T-924f: at sweetwlan >= 25 the mask also carries DEAUTH 5, DEAUTH_IND 6, DISASSOC 11, DISASSOC_IND 12 and PSK_SUP 46 (a deauth after the M2 was invisible with only 69, 0, 16); level 24 keeps its three bits (checked above) */
	{
		unsigned nz = 0;

		l24_arm(0);
		scan_run(p, &x, 23, &rc, "j23mask25");
		CHECK(rc == 0 && x.d4dlv == 23u);
		gd.on = 1;
		rc = swg_j_mask(&x, 25u);
		gd.on = 0;
		for (i = 4; i < 30u; i++) {
			nz += ge.ems[i] != 0u;
		}
		CHECK(rc == 0 && !x.d4dstop && ge.ems[0] == 1u && ge.ems[1] == 3u && ge.ems[2] == 26u && ge.ems[4] == 0x61u && ge.ems[5] == 0x18u && ge.ems[6] == 0x01u && ge.ems[9] == 0x40u && ge.ems[12] == 0x20u && nz == 5u);
		CHECK(has("SWEET WLAN: L24 event_msgs_ext set: bits 69, 0, 16, 5, 6, 11, 12, 46 on, status 0\n"));
	}
	/* the scrub of a secret payload fails (the store of the zero word is refused): the call reports it, the join stops there and says nothing about the key's completion */
	{
		const uint32_t ib = p->b[SWD_IOCTBUF].off;

		jc_build(1);
		l24_arm(0);
		t_jc = jcs;
		t_njc = jcn;
		d4d_cfg(24, IO_BOTH, 0, 0);
		wr_fail_zero_a = ib + 4u * 40u;
		d4d_go(p, &x, &rc);
		wr_fail_zero_a = 0u;
		gd.on = 0;
		t_jc = NULL;
		t_njc = 0u;
		CHECK(rc != 0 && x.d4dlv == 23u && has("SWEET WLAN: L24 H4 mfp status 0\n") && !has("SWEET WLAN: L24 H8 wsec_key status") && !has("join status") && x.esec == 0);
	}
	/* (H) a key iovar: the 173 B payload reaches the dongle whole (44 shadow words), its store lines print no value, the shadow is zeroed again, neither the canary nor the SSID is in the log */
	jc_build(1);
	CHECK(jcn == 7u && jcs[5].len == 173u);
	l24_arm(0);
	l24_run(p, &x, &rc, jcs, jcn, "j24key");
	CHECK(rc == 0 && !x.d4dstop && x.d4dlv == 24u && ge.jn == 7u && strcmp(ge.jname[5], "wsec_key") == 0 && strcmp(ge.jname[6], "join") == 0);
	CHECK(l24_sent(5, &jcs[5]) && l24_sent(6, &jcs[6]) && ge.jbuf[5][9 + 163] == 0x5Au && ge.jbuf[5][9 + 4] == 0xC3);
	CHECK(!has("5a5a5a5a") && !has("3cc33cc3") && !has("c33cc33c") && !has("0x5a5a") && !has("pixos-test"));
	CHECK(has("SWEET WLAN: G2 store d4e payload word host ") && has(" (value withheld)\n"));
	for (i = 0; i < SWG_E_PAYW; i++) {
		if (i >= 19u) {
			CHECK(x.epay[i] == 0u);     /* the 44 words of the key payload are zero again; the join (19 words) wrote the rest */
		}
	}
	CHECK(x.esec == 0 && x.d4wr == base_wr + 16u + 2u);
}

#include "wlan_g2_host_l25wl.h"   /* T-5173: the real ioctl stream of L25 through the store whitelist */
