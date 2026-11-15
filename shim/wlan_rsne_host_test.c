/*
 * Host test of the L23 hook that reads the join target's RSNE out of its scan record (sweet_wlan_l25_rsne.h, T-37d8 step 1; SPEC-WIFI Part E TRANSPORT s.2 L25, U-g).
 * A scripted escan event (type 69, PARTIAL, one wl_bss_info_v109 record with information elements) sits in the event buffer of a G1-block image; swg_e_event (sweet_wlan_g2.h)
 * parses it and calls the hook. The record bytes are written out here literally (offsets 8/18/19/72/78/88/116/120 of wl_bss_info_v109), and the layout itself is pinned by a mirror of the
 * ref struct compiled here (MCSSET_LEN = 16 is a [guess]: not defined in the ref set; the length invariant of the hook is the guard against a wrong value). Clauses (each has a mutant
 * in tools/test_wlan_rsne_mutants.py):
 *   L1 the mirrored struct puts ie_offset at 116, ie_length at 120, size 132, ssid at 19, ctl_ch at 88
 *   P1 target present: the whole first RSNE (id, length, body) is copied, state OK, one `rsne of the join target` line, no store on the bus   P2 same at an odd rx offset
 *   P3 another BSSID: not looked at (no extra bus read), state NONE, swl25r_status prints `WIFI GAP: RSNE missing: target BSSID not in the scan`
 *   P4 zero (wildcard) target: not looked at, status prints the wildcard GAP   P5 no RSNE in the record: GAP, state BAD   P6 RSNE of 65 B: GAP   P7 RSNE of exactly 64 B: accepted
 *   P8 an IE runs past ie_length: GAP   P9 ie_offset + ROUNDUP4(ie_length) != length: GAP   P10 ie_offset below 124: GAP   P11 record length above the event bytes: GAP
 *   P12 record version 114: not read   P13 two RSNEs: the first is kept   P14 a bad record then a good one of the same BSSID: recovers; a later record never replaces an OK one
 *   P4b zero target + zero record BSSID: still not looked at   P11b event shorter than the 124 B head: GAP   P17 128 IEs walked at most   P18 ie_length above length: GAP (no wrap)
 *   P15 a failing bus read inside the IE walk stops swg_e_event (negative); P15b the same for each of the three reads of the record head (length word, ie_offset/ie_length words): negative, stopped, no `RSNE not read` GAP, no state change   P16 no hook installed: swg_e_event reads exactly what it read before (the bss record) and is unchanged
 * No hardware.
 */
#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static unsigned fails;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)

static char logbuf[1 << 20];
static unsigned loglen;
static int cap_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int cap_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(logbuf + loglen, sizeof logbuf - loglen, fmt, ap);
	va_end(ap);
	if (n > 0 && loglen + (unsigned)n < sizeof logbuf)
		loglen += (unsigned)n;
	return n;
}
#define printf cap_printf
#include "sweet_wlan_g2.h"
#include "sweet_wlan_l25_rsne.h"
#undef printf

static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static void clr(void) { loglen = 0; logbuf[0] = 0; }

/* the ref struct, mirrored (wlioctl.h.new:355-397); MCSSET_LEN [guess 16] */
#define MCSSET_LEN 16
typedef struct {
	uint32_t version, length;
	uint8_t BSSID[6];
	uint16_t beacon_period, capability;
	uint8_t SSID_len, SSID[32], bcnflags;
	struct { uint32_t count; uint8_t rates[16]; } rateset;
	uint16_t chanspec, atim_window;
	uint8_t dtim_period, accessnet;
	int16_t RSSI;
	int8_t phy_noise;
	uint8_t n_cap, freespace1, load;
	uint32_t nbss_cap;
	uint8_t ctl_ch, PAD[3];
	uint16_t vht_rxmcsmap, vht_txmcsmap;
	uint8_t flags, vht_cap, flags2, reserved, basic_mcs[MCSSET_LEN];
	uint16_t ie_offset, freespace2;
	uint32_t ie_length;
	int16_t SNR;
	uint16_t vht_mcsmap, vht_mcsmap_prop, vht_txmcsmap_prop;
} bssinfo109;

#define PA    0x880000000ull
#define BLK   0x10000u
#define EVOFF 0x2000u
static uint32_t mem[BLK / 4u];
static unsigned nrd, nwr, failat, failnth;      /* reads, writes; the word offset from which reads fail (0 = none); the 1-based number of the ONE read call that fails (0 = none) */
static int b_rd(void *ctx, int sp, uint32_t a, uint32_t *v)
{
	(void)ctx;
	if (sp != SWG_SP_HOST || a >= BLK || (a & 3u) != 0u || (failat != 0u && a >= failat))
		return -1;
	nrd++;
	if (failnth != 0u && nrd == failnth)
		return -1;
	*v = mem[a / 4u];
	return 0;
}
static int b_wr(void *ctx, int sp, uint32_t a, uint32_t v) { (void)ctx; (void)sp; (void)a; (void)v; nwr++; return -1; }
static void b_delay(void *ctx, unsigned ms) { (void)ctx; (void)ms; }
static const struct swg_bus BUS = { b_rd, b_wr, b_delay, NULL, NULL, NULL, NULL, NULL, NULL };

static const uint8_t TGT[6] = { 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x01 };
static const uint8_t ZERO[6] = { 0, 0, 0, 0, 0, 0 };
static const uint8_t OTHER[6] = { 0x02, 0xaa, 0xbb, 0xcc, 0xdd, 0x02 };
/* an RSNE: WPA2-PSK CCMP, 22 B */
static const uint8_t RSN22[22] = { 0x30, 0x14, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x02, 0x00, 0x00 };
static const uint8_t RSN22B[22] = { 0x30, 0x14, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x04, 0x01, 0x00, 0x00, 0x0f, 0xac, 0x06, 0x00, 0x00 };

/* knobs of one scripted record */
struct rec {
	const uint8_t *bssid;
	uint32_t ver;
	const uint8_t *ies;     /* the IE bytes, ie_len long */
	uint32_t ie_len;        /* ie_length written */
	int ieoff_delta;        /* ie_offset = 132 + delta */
	int length_delta;       /* length = 132 + ROUNDUP4(ie_len) + delta */
	int event_trim;         /* the event carries this many bytes less than the record */
	uint32_t iel_written;   /* nonzero: written to ie_length instead of ie_len */
	uint32_t rcount;        /* nonzero: rateset.count written, and 16 rate bytes 0x81..0x90 (else count 3: 0x82 0x04 0x96) */
	int neg_snr;            /* nonzero: SNR -5 instead of 40 */
};

static void put32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }

/* write one ESCAN_RESULT event (q = 0, rx offset rxoff) into the event buffer; fills the ring-2 item `in`. */
static void script(const struct rec *r, uint32_t rxoff, uint8_t in[SWD4B_D2H_LEN])
{
	uint8_t b[8192];
	uint32_t padded = (r->ie_len + 3u) & ~3u, rl = 132u + padded, dl, edl;
	uint8_t *f, *e, *s;

	memset(b, 0, sizeof b);
	f = b + rxoff;
	f[12] = 0x88; f[13] = 0x6c; f[19] = 0x00; f[20] = 0x10; f[21] = 0x18; f[22] = 0x00; f[23] = 0x01;
	edl = 12u + rl - (uint32_t)r->event_trim;
	f[28] = 0; f[29] = 0; f[30] = 0; f[31] = 69;                    /* event type, BE */
	f[32] = 0; f[33] = 0; f[34] = 0; f[35] = 8;                     /* status PARTIAL, BE */
	f[44] = (uint8_t)(edl >> 24); f[45] = (uint8_t)(edl >> 16); f[46] = (uint8_t)(edl >> 8); f[47] = (uint8_t)edl;
	e = f + 72;
	e[8] = 0x34; e[9] = 0x12;                                       /* sync_id 0x1234 */
	e[10] = 1; e[11] = 0;                                           /* bss_count 1 */
	s = e + 12;
	put32(s, r->ver);
	put32(s + 4, 132u + padded + (uint32_t)r->length_delta);
	memcpy(s + 8, r->bssid, 6);
	s[18] = 3; memcpy(s + 19, "net", 3);
	s[14] = 100;                                                    /* beacon_period */
	s[52] = 3; s[56] = 0x82; s[57] = 0x04; s[58] = 0x96;            /* rateset: count 3, 1 (basic), 2, 11 (basic) Mbps */
	if (r->rcount != 0u) {
		uint32_t q;
		s[52] = (uint8_t)r->rcount;
		for (q = 0; q < 16u; q++) s[56 + q] = (uint8_t)(0x81u + q);
	}
	s[80] = 0xA6;                                                   /* phy_noise -90 */
	s[124] = (uint8_t)(r->neg_snr ? -5 : 40); s[125] = (uint8_t)(r->neg_snr ? 0xFF : 0);   /* SNR */
	s[72] = 0x6a; s[73] = 0x10;                                     /* chanspec */
	s[78] = 0xc4; s[79] = 0xff;                                     /* rssi -60 */
	s[88] = 6;                                                      /* ctl_ch */
	s[116] = (uint8_t)(132 + r->ieoff_delta); s[117] = (uint8_t)((132 + r->ieoff_delta) >> 8);
	put32(s + 120, r->iel_written != 0u ? r->iel_written : r->ie_len);
	memcpy(s + 132, r->ies, r->ie_len);
	dl = 72u + edl;
	memset(in, 0, SWD4B_D2H_LEN);
	put32(in + 4, SWG_D4_REQID);
	in[12] = (uint8_t)dl; in[13] = (uint8_t)(dl >> 8);
	memcpy(&mem[EVOFF / 4u], b, 8192u > BLK - EVOFF ? BLK - EVOFF : 8192u);
}

static struct swl25_tgt T;
static struct swg_wx x;
static struct swg_ev ev;

static void setup(const uint8_t *bssid, int hook)
{
	memset(&x, 0, sizeof x);
	x.b = &BUS;
	x.in.blk = PA; x.in.blk_len = BLK; x.in.evbuf = PA + EVOFF;
	x.elv = 23u;
	x.emax = SWG_E_MAX;     /* the cap swg_e sets (L22c, T-0f36); the test calls swg_e_event directly, a zero cap stops every event */
	memset(&T, 0, sizeof T);
	if (bssid != NULL)
		memcpy(T.bssid, bssid, 6);
	if (hook) {
		x.bssn = swl25r_note;
		x.bssc = &T;
	}
	nrd = nwr = failat = failnth = 0u;
	clr();
}

static int run(const struct rec *r, uint32_t rxoff)
{
	uint8_t in[SWD4B_D2H_LEN];

	script(r, rxoff, in);
	x.rxoff = rxoff;
	x.evn = 0u;
	x.evq = 0u;
	x.epid[0] = SWG_D4_REQID;       /* the buffer the scripted event names is posted in slot 0; a consumed slot is cleared (T-fad2), so every run re-arms it */
	x.d4dstop = 0u;
	return swg_e_event(&x, in, &ev);
}

static struct rec good(const uint8_t *bssid, const uint8_t *ies, uint32_t n)
{
	struct rec r = { bssid, 109u, ies, n, 0, 0, 0, 0, 0, 0 };
	return r;
}

int main(void)
{
	uint8_t ies[512], ies2[512];
	struct rec r;
	unsigned n_head, nrd_other, nrd_off;
	uint32_t n;

	(void)swg_go;   /* g2.h entry points this test does not drive */
	/* L1 */
	CHECK(offsetof(bssinfo109, ie_offset) == 116u && offsetof(bssinfo109, ie_length) == 120u && sizeof(bssinfo109) == 132u);
	CHECK(offsetof(bssinfo109, SSID) == 19u && offsetof(bssinfo109, ctl_ch) == 88u && offsetof(bssinfo109, RSSI) == 78u && offsetof(bssinfo109, BSSID) == 8u);
	CHECK(SWL25R_OFF_IEOFF == offsetof(bssinfo109, ie_offset) && SWL25R_OFF_IELEN == offsetof(bssinfo109, ie_length) && SWL25R_OFF_LEN == offsetof(bssinfo109, length));
	CHECK(SWL25R_HEAD == offsetof(bssinfo109, ie_length) + 4u && SWL25R_RSNE_MAX == 64u);

	/* IEs: SSID "net", rates (8), RSN */
	n = 0;
	ies[n++] = 0; ies[n++] = 3; memcpy(ies + n, "net", 3); n += 3;
	ies[n++] = 1; ies[n++] = 8; memset(ies + n, 0x82, 8); n += 8;
	memcpy(ies + n, RSN22, 22); n += 22;                            /* 5 + 10 + 22 = 37 B: ie_length not a multiple of 4 on purpose */

	/* P1 */
	setup(TGT, 1);
	r = good(TGT, ies, n);
	CHECK(run(&r, 0u) == 0 && !x.d4dstop);
	CHECK(T.state == SWL25R_OK && T.rsne_len == 22u && memcmp(T.rsne, RSN22, 22) == 0 && T.seen == 1u);
	CHECK(has("SWEET WLAN: L23 rsne of the join target: 22 B (IE 48 at record offset 147)\n") && has("L23 rsne record length 172 ie_offset 132 ie_length 37 event bytes 172"));
	CHECK(!has("WIFI GAP") && nwr == 0u && swl25r_status(&T) == 0);
	/* 2026-11-11: the target record's IE block and plain fields are kept for the Get 103 of the kept link (offsets of wl_bss_info_v109, L1) */
	CHECK(T.xf == 1u && T.ie_len == n && T.ie_short == 0u && memcmp(T.ie, ies, n) == 0 && T.ie[n] == 0u);
	CHECK(T.beacon == 100u && T.nrates == 3u && T.rates[0] == 0x82 && T.rates[1] == 0x04 && T.rates[2] == 0x96 && T.rates[3] == 0u && T.chspec == 0x106au && T.noise == -90 && T.snr == 40);
	n_head = nrd;

	/* P2: the same event at an odd rx offset (the record is not word aligned) */
	setup(TGT, 1);
	CHECK(run(&r, 2u) == 0 && T.state == SWL25R_OK && T.rsne_len == 22u && memcmp(T.rsne, RSN22, 22) == 0 && !has("WIFI GAP"));
	setup(TGT, 1);
	CHECK(run(&r, 3u) == 0 && T.state == SWL25R_OK && memcmp(T.rsne, RSN22, 22) == 0);

	/* P3 */
	setup(TGT, 1);
	r = good(OTHER, ies, n);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_NONE && T.seen == 0u && T.rsne_len == 0u && !has("rsne"));
	nrd_other = nrd;
	CHECK(swl25r_status(&T) == -1 && has("WIFI GAP: RSNE missing: target BSSID not in the scan\n"));

	/* P16: without the hook the event costs the same reads as a foreign BSSID with the hook */
	setup(TGT, 0);
	r = good(TGT, ies, n);
	CHECK(run(&r, 0u) == 0 && nrd == nrd_other && T.state == SWL25R_NONE && ev.has_bss == 1 && memcmp(ev.bssid, TGT, 6) == 0 && ev.ctl == 6u && !has("rsne"));
	CHECK(nrd_other < n_head);
	nrd_off = nrd;
	(void)nrd_off;

	/* P4 */
	setup(NULL, 1);
	r = good(TGT, ies, n);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_NONE && T.seen == 0u && nrd == nrd_other);
	CHECK(swl25r_status(&T) == -1 && has("WIFI GAP: RSNE missing: join target BSSID is zero (wildcard)\n"));

	/* P20 (T-fc10 H2): a wildcard join (BSSID zero, SSID "net"): the first record of that SSID names the target and its RSNE is read; a foreign SSID is ignored; a second AP with the SSID is counted, the first wins */
	setup(NULL, 1);
	T.ssid_len = 3u; memcpy(T.ssid, "net", 3);
	r = good(OTHER, ies, n);
	CHECK(run(&r, 0u) == 0 && !x.d4dstop && memcmp(T.bssid, OTHER, 6) == 0 && T.state == SWL25R_OK && T.rsne_len == 22u && memcmp(T.rsne, RSN22, 22) == 0 && T.other == 0u);
	CHECK(has("SWEET WLAN: L23 join target BSSID taken from the first scan record of its SSID\n") && !has("WIFI GAP") && swl25r_status(&T) == 0);
	r = good(TGT, ies, n);
	CHECK(run(&r, 0u) == 0 && memcmp(T.bssid, OTHER, 6) == 0 && T.state == SWL25R_OK && T.other == 1u && has("WIFI GAP: L25 a second AP carries the join SSID; the first record wins (skipped 1)\n"));
	setup(NULL, 1);
	T.ssid_len = 4u; memcpy(T.ssid, "net2", 4);
	r = good(OTHER, ies, n);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_NONE && T.seen == 0u && swl25r_zero(T.bssid, 6u) && !has("taken from") && nrd == nrd_other);
	CHECK(swl25r_status(&T) == -1 && has("WIFI GAP: RSNE missing: target SSID not in the scan\n"));
	setup(TGT, 1);
	T.ssid_len = 3u; memcpy(T.ssid, "net", 3);
	r = good(OTHER, ies, n);
	CHECK(run(&r, 0u) == 0 && memcmp(T.bssid, TGT, 6) == 0 && T.state == SWL25R_NONE && T.other == 1u && T.seen == 0u);      /* a given BSSID is never replaced by the SSID rule */

	/* P5: no RSNE */
	memcpy(ies2, ies, 15); n = 15;
	setup(TGT, 1);
	r = good(TGT, ies2, n);
	CHECK(run(&r, 0u) == 0 && !x.d4dstop && T.state == SWL25R_BAD && T.rsne_len == 0u && has("WIFI GAP: RSNE not read: no RSNE (IE 48) in the target record\n"));
	CHECK(swl25r_status(&T) == -1 && has("RSNE missing: target record unusable"));

	/* P6: RSNE of 65 B (length byte 63); P7: exactly 64 B (length byte 62) */
	memset(ies2, 0, sizeof ies2);
	ies2[0] = 48; ies2[1] = 63; ies2[2] = 1;
	setup(TGT, 1);
	r = good(TGT, ies2, 65u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && T.rsne_len == 0u && has("RSNE shorter than 2 B or longer than 64 B"));
	ies2[1] = 62;
	setup(TGT, 1);
	r = good(TGT, ies2, 64u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && T.rsne_len == 64u && memcmp(T.rsne, ies2, 64) == 0);
	ies2[1] = 1;                                                    /* a 1 B body is no RSNE */
	setup(TGT, 1);
	r = good(TGT, ies2, 3u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("RSNE shorter than 2 B"));

	/* P8: the last IE claims more bytes than ie_length holds */
	memcpy(ies2, ies, 15); ies2[15] = 221; ies2[16] = 40; n = 17;
	setup(TGT, 1);
	r = good(TGT, ies2, n);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("an IE runs past ie_length"));

	/* P9: ie_offset + ROUNDUP4(ie_length) differs from length (each of the three rejections has its own message) */
	n = 37;
	setup(TGT, 1);
	r = good(TGT, ies, n); r.length_delta = 4;                      /* length 176 above the 172 event bytes */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("record length above the event data") && T.rsne_len == 0u);
	setup(TGT, 1);
	r = good(TGT, ies, n); r.length_delta = -4;                     /* length 168: the IEs (132 + 37) end behind it */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset/ie_length outside the record") && T.rsne_len == 0u);
	setup(TGT, 1);
	r = good(TGT, ies, n); r.length_delta = -2;                     /* length 170: 132 + 37 fits, 132 + ROUNDUP4(37) = 172 does not equal it */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset + ie_length does not match length") && T.rsne_len == 0u);
	setup(TGT, 1);
	r = good(TGT, ies, n); r.ieoff_delta = 2;                       /* ie_offset 134: 134 + 37 fits, 134 + 40 = 174 != 172 */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset + ie_length does not match length") && T.rsne_len == 0u);
	setup(TGT, 1);
	r = good(TGT, ies, n); r.ieoff_delta = 4;                       /* ie_offset 136: the IEs end behind the record */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset/ie_length outside the record") && T.rsne_len == 0u);

	/* P10: ie_offset below the head */
	setup(TGT, 1);
	r = good(TGT, ies, n); r.ieoff_delta = -9;                      /* 123 */
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset/ie_length outside the record"));

	/* P11: the record is longer than the event bytes (rb > 12 holds: the head is there) */
	setup(TGT, 1);
	r = good(TGT, ies, n); r.event_trim = 8;
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("record length above the event data") && T.rsne_len == 0u);

	/* 2026-11-11 keep: an IE block above the 1024 B kept is cut there and flagged; exactly 1024 is not; a block with no IEs keeps ie_len 0; the extras are read even when the RSNE walk then fails */
	{
		static uint8_t big[1200];
		uint32_t bn = 0, k;
		uint8_t fill[255];

		memset(fill, 0xA5, sizeof fill);
		memcpy(big, RSN22, 22); bn = 22;
		while (bn + 255u <= 1100u) { big[bn] = 221; big[bn + 1] = 253; memcpy(big + bn + 2, fill, 253); bn += 255u; }
		big[bn] = 221; big[bn + 1] = (uint8_t)(1100u - bn - 2u); memcpy(big + bn + 2, fill, 1100u - bn - 2u); bn = 1100u;
		setup(TGT, 1);
		r = good(TGT, big, bn);
		CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && T.xf == 1u && T.ie_len == 1024u && T.ie_short == 1u && memcmp(T.ie, big, 1024) == 0);
		bn = 22; while (bn + 255u <= 1024u) { big[bn] = 221; big[bn + 1] = 253; memcpy(big + bn + 2, fill, 253); bn += 255u; }
		big[bn] = 221; big[bn + 1] = (uint8_t)(1024u - bn - 2u); memcpy(big + bn + 2, fill, 1024u - bn - 2u); bn = 1024u;
		setup(TGT, 1);
		r = good(TGT, big, bn);
		CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && T.ie_len == 1024u && T.ie_short == 0u && memcmp(T.ie, big, 1024) == 0);
		setup(TGT, 1);
		r = good(TGT, big, 0u);
		CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && T.xf == 1u && T.ie_len == 0u && T.beacon == 100u);
		/* ie_offset 124/125: the two SNR bytes (+124) would be IE bytes, so SNR is not read (0), and a value from an earlier record does not stay */
		setup(TGT, 1);
		{ static const uint8_t norsn[5] = { 0, 3, 'n', 'e', 't' };
		  r = good(TGT, norsn, 5u);                                      /* a good head, no RSNE: BAD, but the extras are read (SNR 40) */
		  CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && T.xf == 1u && T.snr == 40);
		  r = good(TGT, norsn, 5u); r.ieoff_delta = -8; r.length_delta = -8;      /* ie_offset 124 */
		  CHECK(run(&r, 0u) == 0 && T.xf == 1u && T.snr == 0 && T.beacon == 100u);
		  r = good(TGT, norsn, 5u); r.ieoff_delta = -7; r.length_delta = -7;      /* 125 */
		  CHECK(run(&r, 0u) == 0 && T.xf == 1u && T.snr == 0);
		  r = good(TGT, norsn, 5u); r.ieoff_delta = -6; r.length_delta = -6;      /* 126: the first offset behind the SNR bytes */
		  CHECK(run(&r, 0u) == 0 && T.xf == 1u && T.snr == 40); }
		/* every failing read of the event is a stop (none is swallowed by the extras' reads) */
		setup(TGT, 1);
		r = good(TGT, ies, 37u);
		CHECK(run(&r, 0u) == 0);
		n_head = nrd;
		for (k = 1; k <= n_head; k++) {
			setup(TGT, 1);
			failnth = k;
			CHECK(run(&r, 0u) != 0 && T.state != SWL25R_OK);
			CHECK(T.xf == 0u || (T.beacon == 100u && T.nrates == 3u && T.rates[2] == 0x96 && T.chspec == 0x106au && T.noise == -90 && T.snr == 40 && T.ie_len == 37u && memcmp(T.ie, ies, 37) == 0));      /* xf set = every field is there */
		}
		/* 16 and more rates: all 16 bytes are read, a count above 16 is 16; a negative SNR keeps its sign */
		setup(TGT, 1);
		r = good(TGT, ies, 37u); r.rcount = 16u;
		CHECK(run(&r, 0u) == 0 && T.nrates == 16u && T.rates[0] == 0x81 && T.rates[15] == 0x90);
		setup(TGT, 1);
		r = good(TGT, ies, 37u); r.rcount = 20u;
		CHECK(run(&r, 0u) == 0 && T.nrates == 16u && T.rates[15] == 0x90 && T.snr == 40);
		setup(TGT, 1);
		r = good(TGT, ies, 37u); r.neg_snr = 1;
		CHECK(run(&r, 0u) == 0 && T.snr == -5);
	}

	/* P12: version 114 is not parsed here (the scan prints its own GAP) */
	setup(TGT, 1);
	r = good(TGT, ies, n); r.ver = 114u;
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_NONE && T.seen == 0u && nrd == nrd_other);

	/* P13: two RSNEs, the first wins */
	n = 0;
	memcpy(ies2 + n, RSN22, 22); n += 22;
	memcpy(ies2 + n, RSN22B, 22); n += 22;
	setup(TGT, 1);
	r = good(TGT, ies2, n);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && memcmp(T.rsne, RSN22, 22) == 0);

	/* P14: bad then good recovers; a later record never replaces an OK RSNE */
	setup(TGT, 1);
	r = good(TGT, ies, 37u); r.length_delta = 4;
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD);
	r = good(TGT, ies2, 44u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && memcmp(T.rsne, RSN22, 22) == 0 && T.seen == 2u);
	memcpy(ies2, RSN22B, 22);
	r = good(TGT, ies2, 22u);
	clr();
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && memcmp(T.rsne, RSN22, 22) == 0 && T.seen == 2u && !has("rsne"));

	/* P4b: a zero target stays a wildcard even when the record's BSSID is zero too */
	setup(NULL, 1);
	r = good(ZERO, ies, 37u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_NONE && T.seen == 0u && nrd == nrd_other);

	/* P11b: the event is shorter than the 124 B record head (rb > 12 still holds: 89 B of the record are there) */
	setup(TGT, 1);
	r = good(TGT, ies, 37u); r.event_trim = 72;
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("record shorter than the 124 B head") && T.rsne_len == 0u);

	/* P18: ie_length above the record length (the subtraction must not wrap) */
	setup(TGT, 1);
	r = good(TGT, ies, 37u); r.iel_written = 0x10000u;
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("ie_offset/ie_length outside the record") && T.rsne_len == 0u);

	/* P17: the walk looks at 128 IEs: the RSNE behind 127 empty IEs is found, behind 128 it is not (GAP) */
	memset(ies2, 0, sizeof ies2);
	for (n = 0; n < 128u; n++) {
		ies2[2u * n] = 221; ies2[2u * n + 1u] = 0;
	}
	memcpy(ies2 + 256, RSN22, 22);
	setup(TGT, 1);
	r = good(TGT, ies2, 278u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_BAD && has("no RSNE in the first 128 IEs") && T.rsne_len == 0u);
	memcpy(ies2 + 254, RSN22, 22);
	setup(TGT, 1);
	r = good(TGT, ies2, 276u);
	CHECK(run(&r, 0u) == 0 && T.state == SWL25R_OK && T.rsne_len == 22u && memcmp(T.rsne, RSN22, 22) == 0);

	/* P15: a bus read that fails inside the IE walk (the IEs lie behind the 12 + 89 B the event parse reads) */
	setup(TGT, 1);
	r = good(TGT, ies, 37u);
	failat = EVOFF + 72u + 12u + 132u;
	CHECK(run(&r, 0u) < 0 && T.state != SWL25R_OK && T.rsne_len == 0u);

	/* P15b: the reads of the record head fail one by one. The event parse itself costs nrd_other reads; the hook's reads follow: 1 = the length word, 2 and 3 = the two words with ie_offset/ie_length.
	 * A failure must propagate (negative, x->stopped, the read's own STOP line) and must not be taken for a bad record (no `RSNE not read` GAP, state still NONE). */
	for (n = 1; n <= 3u; n++) {
		setup(TGT, 1);
		r = good(TGT, ies, 37u);
		failnth = nrd_other + n;
		CHECK(run(&r, 0u) < 0 && x.stopped && has("a d4e buffer read failed") && !has("RSNE not read") && T.state == SWL25R_NONE && T.rsne_len == 0u && nrd == nrd_other + n);
	}
	setup(TGT, 1);                                                  /* control: the same script without a failure reads nrd_other + 4 or more words and succeeds */
	r = good(TGT, ies, 37u);
	failnth = nrd_other + 1000u;
	CHECK(run(&r, 0u) == 0 && !x.stopped && T.state == SWL25R_OK && nrd > nrd_other + 3u);

	if (fails != 0u) {
		fprintf(stderr, "%s", logbuf);
		printf("RESULT: FAIL wlan_rsne_host_test %u checks failed\n", fails);
		return 1;
	}
	fprintf(stdout, "RESULT: PASS wlan_rsne_host_test\n");
	return 0;
}
