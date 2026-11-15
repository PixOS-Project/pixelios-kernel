/* wlan_sta_host_test.c - host test of the station boundary shim/sweet_wlan_sta.h (X-W1, prep for W-D7).
 * Own code. Fake radio + a TEST-LOCAL codec: the byte layouts in tcodec_* below are NOT Apple layouts
 * (110 gives none, see the header), they only let the handler logic run on the host. */
#include <stdio.h>
#include <string.h>
static char probebuf[16384];
static void t_probe_log(const char *l) { strncat(probebuf, l, sizeof probebuf - strlen(probebuf) - 2); strcat(probebuf, "\n"); }
#define SWSTA_PROBE_LOG(l) t_probe_log(l)	/* patch 0363 (LOWCLK B3): the Set 383 key log lines, captured here */
#include "sweet_wlan_sta.h"

static int fails;
#define CHECK(c) do { if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

#define PASSPHRASE "fakePassphrase-8d1c"
static char logbuf[16384]; static char gapbuf[4096]; static int ngap;
static uint32_t kev[64]; static int nkev;

static void t_log(void *cx, const char *l) { (void)cx; strncat(logbuf, l, sizeof logbuf - strlen(logbuf) - 2); strcat(logbuf, "\n"); }
static void t_gap(void *cx, const char *t) { (void)cx; CHECK(strlen(t) <= 63); ngap++; strncat(gapbuf, t, sizeof gapbuf - strlen(gapbuf) - 2); strcat(gapbuf, "\n"); t_log(cx, t); }
static void t_post(void *cx, uint32_t c) { (void)cx; if (nkev < 64) kev[nkev++] = c; }

/* ---- fake radio: 3 deterministic BSSes ---- */
struct fake { int on, joined, cursor, scanning; struct swsta_bss bss[3]; int cur; int join_ok; char key[64]; };
static struct fake fk;
static struct swsta_radio rad;
static void fk_ev(int kind, int result, int reason) { struct swsta_rev e = { kind, result, reason }; rad.up(rad.up_ctx, &e); }
static int f_power(void *r, int op, int *st) { struct fake *f = r; if (op == SWSTA_PWR_ON) f->on = 1; else if (op == SWSTA_PWR_OFF) { f->on = 0; f->joined = 0; } *st = f->on; return 0; }
static int f_scan(void *r) { struct fake *f = r; if (!f->on) return SWSTA_EIO; f->cursor = 0; fk_ev(SWSTA_REV_SCAN_DONE, 0, 0); return 0; }
static int f_next(void *r, struct swsta_bss *o) { struct fake *f = r; if (f->cursor >= 3) return 0; *o = f->bss[f->cursor++]; return 1; }
static int f_join(void *r, const struct swsta_join *j)
{
	struct fake *f = r; int i;
	if (j->key_kind == SWSTA_KEY_PASSPHRASE) memcpy(f->key, j->key, j->key_len < 63 ? j->key_len : 63);
	for (i = 0; i < 3; i++) if (f->bss[i].ssid_len == j->ssid_len && !memcmp(f->bss[i].ssid, j->ssid, j->ssid_len)) {
		f->joined = 1; f->cur = i; fk_ev(SWSTA_REV_ASSOC_DONE, 0, 0); return 0; }
	fk_ev(SWSTA_REV_ASSOC_DONE, 9, 0); return 0;
}
static int f_leave(void *r) { struct fake *f = r; if (f->joined) { f->joined = 0; fk_ev(SWSTA_REV_LINK_DOWN, 0, 3); } return 0; }
static int f_status(void *r, struct swsta_status *o)
{
	struct fake *f = r; memset(o, 0, sizeof *o);
	if (!f->joined) return 0;
	o->joined = 1; o->sec = SWSTA_SEC_WPA2_PSK; memcpy(o->ssid, f->bss[f->cur].ssid, 32); o->ssid_len = f->bss[f->cur].ssid_len;
	memcpy(o->bssid, f->bss[f->cur].bssid, 6); o->channel = f->bss[f->cur].channel; o->rssi = f->bss[f->cur].rssi; return 0;
}
static void fk_setup(void)
{
	static const char *nm[3] = { "FakeNet-A", "FakeNet-B", "FakeNet-C" };
	int i; memset(&fk, 0, sizeof fk);
	for (i = 0; i < 3; i++) {
		fk.bss[i].ssid_len = (uint8_t)strlen(nm[i]); memcpy(fk.bss[i].ssid, nm[i], fk.bss[i].ssid_len);
		memset(fk.bss[i].bssid, 0x10 * (i + 1), 6); fk.bss[i].channel = 1 + 5 * i; fk.bss[i].rssi = -40 - 10 * i;
		fk.bss[i].sec = i == 2 ? SWSTA_SEC_OPEN : SWSTA_SEC_WPA2_PSK;
	}
	memset(&rad, 0, sizeof rad);
	rad.power = f_power; rad.scan_start = f_scan; rad.bss_next = f_next; rad.join = f_join; rad.leave = f_leave; rad.status = f_status; rad.ctx = &fk;
}

/* ---- TEST-LOCAL codec (not an Apple layout) ---- */
static int tcodec_get(void *cx, uint32_t type, const struct swsta_view *v, void *buf, uint32_t len)
{
	uint8_t *b = buf; (void)cx; memset(b, 0, len);
	switch (type) {
	case SWSTA_T_POWER: b[0] = (uint8_t)v->power_on; return 0;
	case SWSTA_T_SCAN_RESULT: b[0] = v->bss->ssid_len; memcpy(b + 1, v->bss->ssid, 32); memcpy(b + 40, v->bss->bssid, 6); b[48] = (uint8_t)v->bss->channel; return 0;
	case SWSTA_T_CHANNEL: b[0] = (uint8_t)v->st.channel; return 0;
	case SWSTA_T_RSSI: b[0] = (uint8_t)(-v->st.rssi); return 0;
	case SWSTA_T_ASSOC_RESULT: b[0] = (uint8_t)v->assoc_result; return 0;
	case SWSTA_T_CARD_CAPS: case SWSTA_T_RSSI_BOUNDS: case SWSTA_T_CHANNELS_INFO: case SWSTA_T_SUPP_CHANNELS:
	case SWSTA_T_AUTH_TYPE: case SWSTA_T_CURRENT_NETWORK: return 0;
	default: return SWSTA_EOPNOTSUPP;
	}
}
static int tcodec_set(void *cx, uint32_t type, const void *buf, uint32_t len, struct swsta_setreq *o)
{
	const uint8_t *b = buf; (void)cx; (void)len;
	if (type == SWSTA_T_POWER) { o->power_on = b[0]; return 0; }
	if (type == SWSTA_T_ASSOCIATE) {	/* b[0]=ssid_len, b[1..32]=ssid, b[40]=key_len, b[41..]=key */
		o->join.ssid_len = b[0]; memcpy(o->join.ssid, b + 1, 32); o->join.key_len = b[40]; memcpy(o->join.key, b + 41, b[40]);
		o->join.key_kind = SWSTA_KEY_PASSPHRASE; return 0; }
	return SWSTA_EOPNOTSUPP;
}

static void t_post_data(void *cx, uint32_t code, const void *d, uint32_t n) { (void)d; (void)n; t_post(cx, code); }	/* W-UI: payload events count like the others */

/* ---- patch 0363 (LOWCLK B3, research 161 s.3.6): the Set 383 key log. Own plist bodies: the layout is u32 1, u32 L+1, L bytes of XML, NUL (research W-UI-a80211-refused-types.md:14,:24,:44). ---- */
static uint32_t p383_body(uint8_t *b, uint32_t cap, const char *xml)
{
	uint32_t l = (uint32_t)strlen(xml);

	memset(b, 0, cap);
	swsta_p32(b, 0, 1u);
	swsta_p32(b, 4, l + 1u);
	memcpy(b + 8, xml, l);
	return 4104u;	/* the real call is always 4104 bytes: the plist then NUL padding */
}
static int p383_lines(const char *needle)
{
	int n = 0;
	const char *p = probebuf;

	while ((p = strstr(p, needle))) { n++; p += strlen(needle); }
	return n;
}
static void test_p383(const struct swsta_codec *cod, const struct swsta_cb *cb)
{
	static struct swsta s;
	static uint8_t b[4104];
	char xml[600], k[80];
	uint32_t val = 0, i, len;
	int rc, g0;

	/* P0: default off (probe 0): a valid Set 383 is answered "not handled" as before, nothing is scanned, nothing is logged */
	fk_setup(); swsta_init(&s, &rad, cod, cb);
	probebuf[0] = 0; ngap = 0; memset(gapbuf, 0, sizeof gapbuf);
	len = p383_body(b, sizeof b, "<?xml version=\"1.0\"?><plist><dict><key>PowerSave</key><true/></dict></plist>");
	rc = swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(rc == (int)SWSTA_EOPNOTSUPP && probebuf[0] == 0 && s.p383_used == 0 && ngap == 1 && strstr(gapbuf, "a80211 type 383 set len 4104 not handled"));
	/* P1: on: the key name and L, first sight once, the answer unchanged, the plist value never logged */
	s.probe = 1;
	len = p383_body(b, sizeof b, "<?xml version=\"1.0\"?><plist><dict><key>PowerSave</key><string>SECRETVALUE-hunter2</string></dict></plist>");
	g0 = ngap;
	rc = swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(rc == (int)SWSTA_EOPNOTSUPP && ngap == g0 + 1 && s.p383_used == 1 && s.p383[0].n == 1);
	CHECK(p383_lines("set383 key=PowerSave len=105 count=1 (first seen)") == 1);
	CHECK(!strstr(probebuf, "SECRETVALUE") && !strstr(probebuf, "hunter2") && !strstr(logbuf, "SECRETVALUE"));
	for (i = 2; i <= 12; i++) (void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383[0].n == 12 && p383_lines("set383 key=PowerSave") == 2 && p383_lines("set383 key=PowerSave len=105 count=10\n") == 1);	/* first seen + the 10th only */
	/* a Get 383 is not observed; another type is not observed */
	g0 = s.p383[0].n;
	(void)swsta_a80211(&s, 0, 383, &val, b, len); (void)swsta_a80211(&s, 1, 384, &val, b, len);
	CHECK(s.p383[0].n == (uint32_t)g0);
	/* P2: a second key, a key with non-printable bytes, a 60-char key (cut to 47) */
	len = p383_body(b, sizeof b, "<plist><dict><key>Roam</key><integer>1</integer></dict></plist>");
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383_used == 2 && p383_lines("set383 key=Roam len=") == 1);
	snprintf(xml, sizeof xml, "<dict><key>A\x01\x7f" "B</key><true/></dict>");
	len = p383_body(b, sizeof b, xml);
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(p383_lines("set383 key=A??B ") == 1);
	memset(k, 'k', 60); k[60] = 0;
	snprintf(xml, sizeof xml, "<dict><key>%s</key></dict>", k);
	len = p383_body(b, sizeof b, xml);
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383_used == 4 && strlen(s.p383[3].key) == SWSTA_P383_KEY - 1 && p383_lines("set383 key=kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk len=") == 1);
	/* the key length bound: 64 bytes between the tags is read, 65 is an unexpected shape */
	memset(k, 'q', 64); k[64] = 0;
	snprintf(xml, sizeof xml, "<dict><key>%s</key></dict>", k);
	len = p383_body(b, sizeof b, xml);
	g0 = (int)s.p383_shape;
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383_shape == (uint32_t)g0 && s.p383_used == 5);
	memset(k, 'r', 65); k[65] = 0;
	snprintf(xml, sizeof xml, "<dict><key>%s</key></dict>", k);
	len = p383_body(b, sizeof b, xml);
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383_shape == (uint32_t)g0 + 1 && s.p383_used == 5);
	/* P3: unexpected shapes: ONE GAP line (the first 16 bytes), the rest counted; the answer is unchanged. */
	fk_setup(); swsta_init(&s, &rad, cod, cb); s.probe = 1; probebuf[0] = 0;
	len = p383_body(b, sizeof b, "<dict><true/></dict>");                                          /* no <key> */
	CHECK(swsta_a80211(&s, 1, 383, &val, b, len) == (int)SWSTA_EOPNOTSUPP && s.p383_shape == 1 && p383_lines("GAP set383 shape unexpected: len 4104 first bytes 00000001 00000015 ") == 1);
	CHECK(p383_lines("SECRET") == 0);
	len = p383_body(b, sizeof b, "<dict><key>x</dict>");                                           /* no </key> */
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	len = p383_body(b, sizeof b, "<dict><key></key></dict>");                                      /* empty key */
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	len = p383_body(b, sizeof b, "<dict><key>ok</key></dict>");
	swsta_p32(b, 0, 2u); (void)swsta_a80211(&s, 1, 383, &val, b, len);                            /* first word not 1 */
	swsta_p32(b, 0, 1u); swsta_p32(b, 4, 0u); (void)swsta_a80211(&s, 1, 383, &val, b, len);        /* L+1 = 0 */
	swsta_p32(b, 4, 0x1001u); (void)swsta_a80211(&s, 1, 383, &val, b, len);                       /* L+1 above 0x1000 */
	swsta_p32(b, 4, 4104u); (void)swsta_a80211(&s, 1, 383, &val, b, len);                         /* L+1 above len - 8 */
	swsta_p32(b, 4, 5u); (void)swsta_a80211(&s, 1, 383, &val, b, len);                            /* the text is cut before the key ends */
	swsta_p32(b, 0, 0u); swsta_p32(b, 4, 28u); (void)swsta_a80211(&s, 1, 383, &val, b, len);        /* first word 0: also not the shape */
	len = p383_body(b, sizeof b, "<dict><key>late</key></dict>");
	swsta_p32(b, 4, 12u);                                                                           /* L+1 = 12: the key lies beyond the L bytes the length names */
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	{
		char pad[300];

		memset(pad, ' ', 200); strcpy(pad + 200, "<key>beyond</key>");
		len = p383_body(b, sizeof b, pad);                                                           /* the key sits at byte 200 of a body the caller says is 100 bytes long */
		(void)swsta_a80211(&s, 1, 383, &val, b, 100u);
	}
	CHECK(s.p383_shape == 11 && p383_lines("GAP set383 shape unexpected") == 1 && s.p383_used == 0);
	CHECK(swsta_a80211(&s, 1, 383, &val, 0, 4104u) == (int)SWSTA_EOPNOTSUPP && s.p383_shape == 12);   /* no buffer (the glue gives none above A80211_BUF) */
	CHECK(swsta_a80211(&s, 1, 383, &val, b, 11u) == (int)SWSTA_EOPNOTSUPP && s.p383_shape == 13);       /* len below 12: only the sizes the buffer has are read */
	/* the exact bound: L+1 == len - 8 is read, L+1 == len - 7 is not (the text must end inside the body) */
	len = p383_body(b, sizeof b, "<dict><key>edge</key></dict>");
	swsta_p32(b, 4, 4096u); CHECK(swsta_a80211(&s, 1, 383, &val, b, 4104u) == (int)SWSTA_EOPNOTSUPP && s.p383_used == 1);
	swsta_p32(b, 4, 4097u); (void)swsta_a80211(&s, 1, 383, &val, b, 4104u);
	CHECK(s.p383_used == 1 && s.p383_shape == 14);
	/* P4: the table of 32 distinct keys: key 33 and 34 are counted, one GAP line */
	fk_setup(); swsta_init(&s, &rad, cod, cb); s.probe = 1; probebuf[0] = 0;
	for (i = 0; i < 34; i++) {
		snprintf(xml, sizeof xml, "<dict><key>K%02u</key></dict>", (unsigned)i);
		len = p383_body(b, sizeof b, xml);
		(void)swsta_a80211(&s, 1, 383, &val, b, len);
	}
	CHECK(s.p383_used == SWSTA_P383_MAX && s.p383_over == 2 && p383_lines("GAP set383 key table full (32 distinct keys)") == 1 && p383_lines("(first seen)") == 32);
	len = p383_body(b, sizeof b, "<dict><key>K05</key></dict>");
	(void)swsta_a80211(&s, 1, 383, &val, b, len);
	CHECK(s.p383[5].n == 2);	/* a known key is still counted when the table is full */
	/* P5: the summary lines (probe on): every key, then the totals; probe off prints none */
	probebuf[0] = 0; logbuf[0] = 0;
	swsta_summary(&s);
	CHECK(p383_lines("set383 summary key=") == 32 && p383_lines("set383 summary key=K05 n=2 len=") == 1 && p383_lines("set383 summary keys=32 table_full_new=2 shape_unexpected=0") == 1);
	s.probe = 0; probebuf[0] = 0;
	swsta_summary(&s);
	CHECK(probebuf[0] == 0);
}

int main(void)
{
	static struct swsta s, s2; static const struct swsta_codec cod = { tcodec_get, tcodec_set, 0 };
	struct swsta_cb cb = { t_log, t_gap, t_post, t_post_data, 0 };
	static uint8_t buf[10016]; uint32_t val = 0; int n, rc; char *p;

	fk_setup(); swsta_init(&s, &rad, &cod, &cb);
	/* open probe */
	CHECK(swsta_a80211(&s, 0, 12, &val, buf, 13) == 0);
	/* power on / off with POWER_CHANGED */
	buf[0] = 1; memset(buf + 1, 0, 23); nkev = 0;
	CHECK(swsta_a80211(&s, 1, 19, &val, buf, 24) == 0); CHECK(nkev == 1 && kev[0] == SWSTA_EV_POWER_CHANGED);
	CHECK(swsta_a80211(&s, 0, 19, &val, buf, 24) == 0 && buf[0] == 1);
	buf[0] = 0; CHECK(swsta_a80211(&s, 1, 19, &val, buf, 24) == 0); CHECK(nkev == 2 && kev[1] == SWSTA_EV_POWER_CHANGED);
	CHECK(swsta_a80211(&s, 0, 19, &val, buf, 24) == 0 && buf[0] == 0);
	buf[0] = 1; nkev = 0; CHECK(swsta_a80211(&s, 1, 19, &val, buf, 24) == 0 && nkev == 1);
	CHECK(swsta_a80211(&s, 0, 106, &val, buf, 16) == 0);
	CHECK(swsta_a80211(&s, 0, 207, &val, buf, 10016) == 0);
	CHECK(swsta_a80211(&s, 0, 27, &val, buf, 4824) == 0);
	/* scan: Set 10 -> SCAN_DONE -> Get 11 until the end answer */
	nkev = 0; CHECK(swsta_a80211(&s, 1, 10, &val, buf, 5456) == 0); CHECK(nkev == 1 && kev[0] == SWSTA_EV_SCAN_DONE);
	for (n = 0; (rc = swsta_a80211(&s, 0, 11, &val, buf, 1164)) == 0 && n < 10; n++) CHECK(!memcmp(buf + 1, fk.bss[n].ssid, fk.bss[n].ssid_len));
	CHECK(n == 3); CHECK(rc == (int)SWSTA_E_SCAN_END);
	/* not joined: soft ENXIO, no gap */
	CHECK(swsta_a80211(&s, 0, 1, &val, buf, 32) == (int)SWSTA_ENXIO);
	/* join: Set 20 -> ASSOC_DONE + 2/3/4 */
	memset(buf, 0, 592); buf[0] = 9; memcpy(buf + 1, "FakeNet-B", 9); buf[40] = (uint8_t)strlen(PASSPHRASE); memcpy(buf + 41, PASSPHRASE, strlen(PASSPHRASE));
	nkev = 0; CHECK(swsta_a80211(&s, 1, 20, &val, buf, 592) == 0);
	CHECK(nkev == 4 && kev[0] == SWSTA_EV_ASSOC_DONE && kev[1] == SWSTA_EV_SSID_CHANGED && kev[2] == SWSTA_EV_BSSID_CHANGED && kev[3] == SWSTA_EV_LINK_CHANGED);
	CHECK(!strcmp(fk.key, PASSPHRASE));	/* the radio got the key; the log must not */
	val = 77; CHECK(swsta_a80211(&s, 0, 21, &val, 0, 0) == 0 && val == 0);	/* 4-byte class: req_len 0, answer in req_val [RE-A R1] */
	nkev = 0; CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == 0);
	CHECK(nkev == 1 && kev[0] == SWSTA_EV_LINK_CHANGED && s.relinks == 1 && !s.relink_armed);	/* W-UI: Get 207 armed it, this first Get 103 re-posts the link state */
	nkev = 0; CHECK(swsta_a80211(&s, 0, 103, &val, buf, 1164) == 0 && nkev == 0);			/* once */
	CHECK(swsta_a80211(&s, 0, 1, &val, buf, 32) == 0 && !memcmp(buf, "FakeNet-B", 9) && buf[9] == 0);
	CHECK(swsta_a80211(&s, 0, 9, &val, buf, 6) == 0 && buf[0] == 0x20 && buf[5] == 0x20);
	CHECK(swsta_a80211(&s, 0, 4, &val, buf, 16) == 0 && buf[0] == 6);
	CHECK(swsta_a80211(&s, 0, 16, &val, buf, 52) == 0 && buf[0] == 50);
	CHECK(swsta_a80211(&s, 0, 17, &val, buf, 52) == (int)SWSTA_EOPNOTSUPP);	/* test codec has no 17: soft + gap */
	CHECK(swsta_a80211(&s, 0, 2, &val, buf, 16) == 0);
	CHECK(swsta_a80211(&s, 0, 6, &val, buf, 12) == (int)SWSTA_EOPNOTSUPP);
	/* leave: link down */
	nkev = 0; CHECK(swsta_a80211(&s, 1, 22, &val, buf, 0) == 0); CHECK(nkev == 1 && kev[0] == SWSTA_EV_LINK_CHANGED);
	CHECK(swsta_a80211(&s, 0, 9, &val, buf, 6) == (int)SWSTA_ENXIO);
	CHECK(ngap == 4);	/* scan-body gap + type 17 and 6 refused by the codec + the one SNR/NOISE gap of the first link event (W-UI) */
	/* unknown type, wrong length: soft error + gap line */
	ngap = 0; memset(gapbuf, 0, sizeof gapbuf);
	CHECK(swsta_a80211(&s, 0, 300, &val, buf, 8) == (int)SWSTA_EOPNOTSUPP);
	CHECK(swsta_a80211(&s, 0, 19, &val, buf, 23) == (int)SWSTA_EOPNOTSUPP);
	CHECK(swsta_a80211(&s, 1, 20, &val, buf, 100) == (int)SWSTA_EOPNOTSUPP);
	CHECK(swsta_a80211(&s, 0, 254, &val, buf, 4824) == (int)SWSTA_EOPNOTSUPP);	/* len 4824 [RE-A L9]; the test codec has no 254 */
	CHECK(swsta_a80211(&s, 0, 9999, &val, buf, 4) == (int)SWSTA_EOPNOTSUPP);	/* out of range */
	CHECK(ngap == 5);
	CHECK(strstr(gapbuf, "a80211 type 300 get len 8 not handled"));
	CHECK(strstr(gapbuf, "a80211 type 19 get len 23 not handled"));
	CHECK(strstr(gapbuf, "a80211 type 20 set len 100 not handled"));
	/* no codec: layout-less types soft-fail with a gap, 12 answers 0 with a gap, 1/9 still work */
	{ struct fake keep = fk; struct swsta_radio r2 = rad; int g0;
	  r2.ctx = &keep; swsta_init(&s2, &r2, 0, &cb); CHECK(s2.codec == &swsta_codec_167); s2.codec = 0; g0 = ngap;
	  CHECK(swsta_a80211(&s2, 0, 12, &val, buf, 13) == 0 && ngap == g0 + 1);
	  CHECK(swsta_a80211(&s2, 0, 19, &val, buf, 24) == (int)SWSTA_EOPNOTSUPP && ngap == g0 + 2);
	  CHECK(swsta_a80211(&s2, 1, 20, &val, buf, 592) == (int)SWSTA_EOPNOTSUPP && ngap == g0 + 3);
	  CHECK(swsta_a80211(&s2, 0, 11, &val, buf, 1164) == (int)SWSTA_E_SCAN_END);
	}
	swsta_summary(&s);
	CHECK(strstr(logbuf, "gaps=") != 0);
	/* the passphrase and SSID bytes never reach the log */
	CHECK(!strstr(logbuf, PASSPHRASE)); CHECK(!strstr(logbuf, "hunter")); CHECK(!strstr(logbuf, "FakeNet"));
	CHECK(strstr(logbuf, "a80211 req type=20 set len=592 -> 0"));
	for (n = 0, p = logbuf; (p = strstr(p, "a80211 type ")); p++) n++;
	CHECK(n >= 2);
	test_p383(&cod, &cb);
	printf("%s", logbuf);
	printf("RESULT: %s wlan_sta_host_test\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
