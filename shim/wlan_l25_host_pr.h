/* wlan_l25_host_pr.h - host cases of the LOWCLK B3 probe (xnu patch 0363, docs/research/161 s.3; swl25_probe_*), included by wlan_l25_host_test.c after wlan_l25_host_ka.h. The stub pr_stub
 * answers and records the three read-only GETs (WLC_GET_PM 85, GET_VAR country, GET_VAR chanspecs) and keeps them out of K. Cases: P0 default off (no call, no line); P1 armed: P1 before the
 * 4-way and P2 after L26, each once, in the order pm -> country -> chanspecs, request shapes (cmd, name, no argument bytes, in = out length, reply room) checked per call; P3 the lines (pm text,
 * country text, the chanspecs decode: 20 MHz lists per band, n40/n80/n160, other); P4 every GAP (status, short reply, pm 3, country empty, count too big / not fitting, 6 GHz / 4 GHz entries);
 * P5 a second swl25_live (the re-join) prints nothing more; P6 no SSID / BSSID / address ever in a SWEET PROBE line. No hardware. */
static struct { unsigned n[3], order[8], nord, shape_bad; int32_t st[3]; uint32_t len[3]; uint32_t pm; uint8_t country[12]; uint32_t cnt, nent, ent[320]; } PR;
static void pr_reset(void)
{
	memset(&PR, 0, sizeof PR);
	PR.len[0] = 4u; PR.len[1] = 12u; PR.len[2] = 1200u;
	PR.pm = 2u;
	memcpy(PR.country, "US\0\0", 4); put32le(PR.country + 4, 0xFFFFFFFFu); memcpy(PR.country + 8, "US\0\0", 4);
	PR.nent = 4u; PR.cnt = 4u;
	PR.ent[0] = 0x1001u; PR.ent[1] = 0x1006u; PR.ent[2] = 0xD024u; PR.ent[3] = 0xD028u;
}
static int pr_stub(uint32_t cmd, const char *name, const uint8_t *arg, uint32_t argn, uint32_t wlen, uint32_t nrsp, uint8_t *rsp, struct swd4b_ioctl_d2h *cmo, int secret)
{
	unsigned which;
	uint32_t i;

	if (name[0] == '\0' && cmd == 85u) which = 0;
	else if (strcmp(name, "country") == 0) which = 1;
	else if (strcmp(name, "chanspecs") == 0) which = 2;
	else return 0;
	memset(cmo, 0, sizeof *cmo);
	PR.n[which]++;
	if (PR.nord < 8u) PR.order[PR.nord++] = which;
	/* the request shape: GET_PM cmd 85 name "" in = out = 4; the two GET_VARs name + zeros, no argument bytes; country 20 B in / 12 B room, chanspecs 1200 / 1200 */
	if ((which == 0 ? (cmd != 85u || wlen != 4u || nrsp != 4u) : (cmd != SWD4B_WLC_GET_VAR || wlen != (which == 1 ? 20u : 1200u) || nrsp != (which == 1 ? 12u : 1200u))) || arg != NULL || argn != 0u || secret) {
		PR.shape_bad++;
		cmo->status = -1;
		return 1;
	}
	cmo->status = PR.st[which];
	cmo->resp_len = PR.len[which];
	if (which == 0) { put32le(rsp, PR.pm); }
	else if (which == 1) { memcpy(rsp, PR.country, 12); }
	else {
		put32le(rsp, PR.cnt);
		for (i = 0; i < PR.nent && 4u + 4u * i + 4u <= nrsp; i++) put32le(rsp + 4u + 4u * i, PR.ent[i]);
	}
	return 1;
}
static unsigned pr_count(const char *needle)
{
	unsigned n = 0u;
	const char *p = logbuf;

	while ((p = strstr(p, needle)) != NULL) { n++; p += strlen(needle); }
	return n;
}
static void test_probe(void)
{
	int rc;
	unsigned i;

	/* review 0363 findings 3+4+5: the boot-arg arms ONLY P2 (the pre-4-way round could end the join on a stalled GET); a run that never reaches it prints a GAP */
	pr_reset(); scen(); swl25_probe_set(1u);
	CHECK(swl25_probe_arm == 2u);
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && PR.n[0] == 1u && PR.n[1] == 1u && PR.n[2] == 1u && swl25_probe_arm == 0u);
	CHECK(has("at=joined\n") && !has("at=l25start") && !has("at=bringup") && !has("SWEET PROBE: GAP"));
	pr_reset(); scen(); swl25_probe_set(1u);
	(void)swl25_probe_unreached(0);
	CHECK(has("SWEET PROBE: GAP wifi probes armed but not reached") && swl25_probe_arm == 0u);
	pr_reset(); scen(); swl25_probe_set(0u);
	(void)swl25_probe_unreached(0);
	CHECK(!has("SWEET PROBE"));
	/* P0: default off (the glue never armed it): nothing is asked, nothing is printed */
	swl25_probe_set(0u); pr_reset(); scen();
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && PR.n[0] == 0u && PR.n[1] == 0u && PR.n[2] == 0u && !has("SWEET PROBE"));
	/* P1: armed: two rounds (l25start after the MAC read, joined after L26), each pm -> country -> chanspecs */
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && PR.shape_bad == 0u && PR.n[0] == 2u && PR.n[1] == 2u && PR.n[2] == 2u && swl25_probe_arm == 0u);
	for (i = 0; i < 6u; i++) CHECK(PR.order[i] == i % 3u);
	CHECK(has("SWEET PROBE: wifi pm=2 (FAST) at=l25start\n") && has("SWEET PROBE: wifi pm=2 (FAST) at=joined\n"));
	CHECK(has("SWEET PROBE: wifi country abbrev=US rev=-1 ccode=US at=l25start\n") && has("SWEET PROBE: wifi country abbrev=US rev=-1 ccode=US at=joined\n"));
	CHECK(has("SWEET PROBE: wifi chanspecs count=4 ch20_2g=1,6 ch20_5g=36,40 ch20_6g=- n40=0 n80=0 n160=0 nother=0 at=l25start\n"));
	CHECK(has("SWEET PROBE: wifi chanspecs count=4 ch20_2g=1,6 ch20_5g=36,40 ch20_6g=- n40=0 n80=0 n160=0 nother=0 at=joined\n"));
	CHECK(!has("SWEET PROBE: GAP"));
	/* the first round comes before the 4-way (L25 done), the second after the live-link line */
	{
		const char *b = strstr(logbuf, "at=l25start"), *d = strstr(logbuf, "SWEET WLAN: L25 done"), *l = strstr(logbuf, "SWEET WLAN: L25 live link"), *j = strstr(logbuf, "at=joined");

		CHECK(b != NULL && d != NULL && l != NULL && j != NULL && b < d && d < l && l > b && j < l);     /* P2 runs after the three live reads, before the live-link summary line */
	}
	/* P5: a second swl25_live (the re-join path) asks nothing more: each point fires once per arming */
	{
		unsigned n0 = PR.n[0] + PR.n[1] + PR.n[2];

		rc = swl25_live(&x, &swl25_H);
		CHECK(rc == 0 && PR.n[0] + PR.n[1] + PR.n[2] == n0);
	}
	/* P6: no network name, address or MAC in any SWEET PROBE line */
	{
		const char *p = logbuf;
		unsigned lines = 0u;

		while ((p = strstr(p, "SWEET PROBE:")) != NULL) {
			const char *e = strchr(p, '\n');
			char ln[400];

			CHECK(e != NULL && (size_t)(e - p) < sizeof ln);
			memcpy(ln, p, (size_t)(e - p)); ln[e - p] = '\0';
			CHECK(strstr(ln, "ssid") == NULL && strstr(ln, "bssid") == NULL && strstr(ln, "02:") == NULL);
			lines++; p = e;
		}
		CHECK(lines == 6u);
	}
	/* P3: the decode of every band and width: 2.4 GHz 1, 6; 5 GHz 36; 40 MHz; 80 MHz; 160 MHz; 6 GHz 20 MHz ch 1; 4 GHz 20 MHz (other); a 320 MHz field (other) */
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.nent = PR.cnt = 9u;
	PR.ent[0] = 0x1001u; PR.ent[1] = 0x1006u; PR.ent[2] = 0xD024u; PR.ent[3] = 0xD826u; PR.ent[4] = 0xE02Au; PR.ent[5] = 0xE832u; PR.ent[6] = 0x5001u; PR.ent[7] = 0x9001u; PR.ent[8] = 0xC000u | 0x0000u | 0x24u;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: wifi chanspecs count=9 ch20_2g=1,6 ch20_5g=36 ch20_6g=1 n40=1 n80=1 n160=1 nother=2 at=l25start\n"));
	/* P4: every refusal is a GAP line with status / resp_len, never a stop, the other reads still run */
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.st[0] = -1; PR.st[1] = -2; PR.st[2] = -3;
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && PR.n[0] == 2u && PR.n[1] == 2u && PR.n[2] == 2u);
	CHECK(has("SWEET PROBE: GAP wifi pm status=-1 resp_len=4: value unread\n") && has("SWEET PROBE: GAP wifi country status=-2 resp_len=12: value unread\n") && has("SWEET PROBE: GAP wifi chanspecs status=-3 resp_len=1200: value unread\n"));
	CHECK(pr_count("SWEET PROBE: GAP wifi") == 6u && !has("SWEET PROBE: wifi "));
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.len[0] = 2u; PR.len[1] = 8u; PR.len[2] = 2u;                                                       /* short replies */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: GAP wifi pm status=0 resp_len=2: value unread\n") && has("SWEET PROBE: GAP wifi country status=0 resp_len=8: value unread\n") && has("SWEET PROBE: GAP wifi chanspecs status=0 resp_len=2: value unread\n"));
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.pm = 3u; memset(PR.country, 0, 12);                                                                /* pm out of range, country unset */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: GAP wifi pm value 3 is outside 0..2 (OFF/MAX/FAST): at=l25start\n") && has("SWEET PROBE: wifi country abbrev= rev=0 ccode= at=l25start\n"));
	CHECK(has("SWEET PROBE: GAP wifi country abbrev and ccode are both empty: no country set at=l25start\n"));
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.cnt = 300u; PR.nent = 300u;                                                                        /* more entries than the 299 the 1200 B reply holds */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: GAP wifi chanspecs count=300 does not fit the 1200 B reply (resp_len 1200, at most 299 entries): list unread at=l25start\n"));
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.cnt = 100u; PR.nent = 4u; PR.len[2] = 40u;                                                         /* count 100 but a 40 B reply: 9 entries at most */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: GAP wifi chanspecs count=100 does not fit the 1200 B reply (resp_len 40, at most 299 entries): list unread at=l25start\n"));
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.len[2] = 4u; PR.cnt = 0u; PR.nent = 0u;                                                            /* an empty list is a valid answer */
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && has("SWEET PROBE: wifi chanspecs count=0 ch20_2g=- ch20_5g=- ch20_6g=- n40=0 n80=0 n160=0 nother=0 at=l25start\n"));
	/* the lists are cut, never overrun: 150 distinct 2.4 GHz entries */
	pr_reset(); scen(); swl25_probe_set(1u); swl25_probe_arm = 3u;
	PR.cnt = PR.nent = 150u;
	for (i = 0; i < 150u; i++) PR.ent[i] = 0x1000u | (i + 100u);
	rc = swl25_run(&x, 26u);
	CHECK(rc == 0 && !x.d4dstop && has("ch20_2g=100,101,102") && has("+ ch20_5g=-") && PR.shape_bad == 0u);
	swl25_probe_set(0u);
}
