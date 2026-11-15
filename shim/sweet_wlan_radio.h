/* sweet_wlan_radio.h - the swsta_radio backend over the escan result events of the Wi-Fi chip (P-W4, T-431f; goal W-D7).
 *
 * PURPOSE: fills struct swsta_radio (sweet_wlan_sta.h) so the Apple80211 handler can answer Set 10 (scan) and Get 11 (scan result) from a real
 * escan: power / scan_start / bss_next / join / leave / status. Header-only C99, no malloc, no libc beyond what sweet_wlan_sta.h includes
 * (SWSTA_MEMCPY / SWSTA_MEMSET / SWSTA_SNPRINTF are reused). Include sweet_wlan_sta.h first.
 *
 * LAYERS: this file owns NO bus access and makes NO store. The chip side is a struct swr_link: its escan() posts the escan request and the event
 * buffers over the msgbuf rings (the HW-WRITE part: every store goes through the deny-by-default list of the G2 glue, review 0174-*) and hands
 * every ESCAN_RESULT event it reads to the sink given to it as (status, wl_escan_result bytes). The radio parses those bytes into swsta_bss,
 * keeps the list for bss_next, and posts SWSTA_REV_SCAN_DONE.
 *
 * Tags: [src G2] = shim/sweet_wlan_g2.h swg_e_event / swg_e_items (the layout the boot-9 run parsed: obs docs/reviews/T-135a-proof.md, 8 BSS from
 * event 69 status 8 items). Record offsets are the ones G2 reads: version +0, length +4, bssid +8, capability +16 (standard 802.11 position after
 * beacon_period; not read by G2, NOT measured here), ssid_len +18, ssid +19, chanspec +72, rssi +78 (int16), ctl_ch +88.
 *
 * Rule 8 (no silent failure): every refusal, every dropped record, an unfinished scan (no status-0 event) and every unsupported verb (join,
 * leave) calls the gap callback ("WIFI GAP:" is added by the includer's gap function) and is counted; swr_summary() prints gaps=N.
 */
#ifndef SWEET_WLAN_RADIO_H
#define SWEET_WLAN_RADIO_H

#include <stdint.h>

#define SWR_EBUSY 16u			/* scan already running */
#define SWR_MAX_BSS 64u			/* list capacity; more distinct BSSIDs are dropped with a gap [decision] */
#define SWR_ST_OK 0u			/* ESCAN_RESULT status SUCCESS: the scan is complete [src G2 SWG_E_ST_OK] */
#define SWR_ST_PARTIAL 8u		/* ESCAN_RESULT status PARTIAL: carries bss records [src G2 SWG_E_ST_PARTIAL] */
#define SWR_BSS_VER 109u		/* wl_bss_info version we parse [src G2 SWG_E_BSS_VER] */
#define SWR_RES_HDR 12u			/* wl_escan_result header: buflen +0, version +4, sync_id +8 (u16), bss_count +10 (u16) [src G2] */
#define SWR_BSS_NEED 89u		/* record bytes read, through ctl_ch (+88) [src G2 SWG_E_BSS_NEED] */
#define SWR_IE_CAP SWSTA_IE_CAP		/* bytes of IEs kept per BSS: 512 [decision] */
/* 2026-11-11 scan records: offsets of wl_bss_info_v109 (host compile of the struct of refs/wlan-stage5/wlioctl.h.new:355-396 with offsetof, MCSSET_LEN 16: length 4, BSSID 8, beacon_period 14, capability 16, SSID_len 18, SSID 19,
 * rateset.count 52, rateset.rates 56, chanspec 72, RSSI 78, phy_noise 80, ctl_ch 88, ie_offset 116, ie_length 120, SNR 124, sizeof 132; the 16 rates are u8 in 500 kbps units, bit 7 = basic) */
#define SWR_BSS_SNR_END 126u		/* record bytes needed through SNR (+124, s16): the IE fields (ie_offset +116, ie_length +120) and SNR are read only from a record at least this long */
#define SWR_SG_CHW 1u			/* once-per-scan GAP bits (struct swr.sgap) */
#define SWR_SG_BAND 2u
#define SWR_SG_RATES 4u
#define SWR_SG_IEBAD 8u
#define SWR_SG_IEPOS 16u
#define SWR_SG_IECUT 32u
#define SWR_SG_IESEC 64u
#define SWR_SG_CHW2 128u
#define SWR_SG_FULL 256u
#define SWR_SYNC 0x1234u		/* the sync_id of our boot escan request [src G2 SWG_E_SYNC] */
#define SWR_SYNC_N 0xFFu		/* review 0292 F2: the keep scans use SWR_SYNC + 1 .. + 0xFF (one per scan; the collector swg_ke_escan already passes only the id of the running scan): events with an id outside SWR_SYNC .. SWR_SYNC + SWR_SYNC_N are ignored */
#define SWR_CAP_PRIVACY 0x0010u		/* 802.11 capability bit 4: some security is on (WEP/WPA/RSN: which one is open, so SWSTA_SEC_OTHER) */

typedef void (*swr_sink_fn)(void *sink_cx, uint32_t status, const uint8_t *res, uint32_t len);

/* patch 0292 (WIFI-COVERAGE D3/D4, Settings > Wi-Fi lists networks): the LIVE scan of the kept link. In keep mode the engine can scan again (WLC_SET_VAR escan through the keep thread, sweet_wlan_l25_hs.h swl25_sc_*), so Set 10 does not replay the boot-time listing but ASKS the keep
 * thread (ascan.request, no engine access here, no wait) and returns at once; the keep thread collects the ESCAN_RESULT events into a scratch `struct swr` (swr_event is its sink: the same parser, dedupe by BSSID with the stronger RSSI, SWR_MAX_BSS bound) and, when the scan
 * ended, calls swr_scan_finish under the radio lock, which swaps the list in and posts SCAN_DONE. The kinds below are the numbers of SWL25_SCR_* in sweet_wlan_l25_hs.h. errnos are the Darwin ones (ENXIO 6, ENETDOWN 50) as sweet_wlan_sta.h uses. */
#define SWR_SCAN_COMPLETE 1u		/* ESCAN_RESULT status 0 seen */
#define SWR_SCAN_TIMEOUT 2u		/* the keep thread's budget ran out */
#define SWR_SCAN_LINKLOST 3u		/* the AP link went down during the scan */
#define SWR_SCAN_FAILED 4u		/* the firmware refused the escan, a soft stop, or the engine ended */
#define SWR_SCAN_REFUSED 5u		/* the keep thread refused a request that raced with a link loss */
#define SWR_E_NXIO 6u			/* ascan.request: the live scan is off for this boot */
#define SWR_E_NETDOWN 50u		/* ascan.request: the link is not UP (a re-join is in progress) */

struct swr_ascan {
	int (*request)(void *cx);	/* 0 = the keep thread will scan and call swr_scan_finish; SWR_E_NXIO = no live scan (fall back to the replay, loud); else a positive errno = refused */
	void *cx;
};

struct swr_link {
	/* Post the escan request, then read the ESCAN_RESULT events and call sink(sink_cx, status, wl_escan_result bytes, len) for each, until the
	 * status-0 event or the link's own timeout. 0 = the sequence ran (a missing status-0 event is the radio's gap), else a positive errno.
	 * BOUNDED: the caller (the A80211 handler) holds its mutex for the whole call, so a wired link MUST return within its own budget (the boot-9
	 * escan used SWG_E_BUDGET_MS = 8000 ms) and never block unbounded; do not wire a link without that bound (review T-431f MEDIUM 4). */
	int (*escan)(void *cx, swr_sink_fn sink, void *sink_cx);
	void *cx;
};

/* T-08fd (goal W-D7 / P-W4, Settings > Wi-Fi): the escan link over the BOOT-TIME scan. The chip scan runs only inside the boot run (G2 L23, patch 0174; afterwards the bus master is
 * undone and the CA7 re-halted, so no later scan is possible), which copies every ESCAN_RESULT event of our sync id (status + the 12 B header + first 89 B record) into a fixed
 * record. This link replays them in order to the sink: no bus access, no store, no wait, so it is bounded by SWR_REPLAY_MAX events (<< the 8000 ms the contract above allows).
 * The listing is STALE (as of the boot scan) and says so in a gap line on every replay (with the count of events the record lost, if any); an empty record is a refused scan with a gap line, never a silent empty list. */
#define SWR_REPLAY_MAX 64u			/* events replayed at most = SWG_E_MAX, the capacity of the record [src sweet_wlan_g2.h SWG_E_MAX] */
#define SWR_REPLAY_BUF (SWR_RES_HDR + SWR_BSS_NEED)	/* bytes of one event [src sweet_wlan_g2.h SWG_REC_RES] */

/* get(i, &status, out, cap): the length (> 0) of event i, 0 = no more events, < 0 = error [src sweet_wlan_g2.h sweet_wlan_g2_escan_rec] */
typedef int (*swr_rec_fn)(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap);

struct swr_replay {
	swr_rec_fn get;
	uint32_t (*lost)(void);				/* events the record could not keep (NULL = none counted) [src sweet_wlan_g2.h sweet_wlan_g2_escan_lost] */
	void (*gap)(void *cx, const char *text);	/* text <= 79 chars */
	void *gap_cx;
};
/* the link the kernel glue and the host test both use: the exact wiring, so a test cannot pass over a different one */
#define SWR_REPLAY_LINK(rp) { swr_replay_escan, (void *)(uintptr_t)(rp) }

static inline int swr_replay_escan(void *cx, swr_sink_fn sink, void *sink_cx)
{
	const struct swr_replay *rp = cx;
	uint8_t buf[SWR_REPLAY_BUF];
	char g[80];
	uint32_t i, st, lost;
	int len;

	if (!rp || !rp->get || !sink) return SWSTA_EIO;
	for (i = 0; i < SWR_REPLAY_MAX; i++) {
		st = 0;
		len = rp->get(i, &st, buf, sizeof buf);
		if (len < 0) {
			SWSTA_SNPRINTF(g, sizeof g, "boot-time scan record unreadable at event %u (rc %d), replay stopped", (unsigned)i, len);
			if (rp->gap) rp->gap(rp->gap_cx, g);
			return SWSTA_EIO;
		}
		if (len == 0) break;
		sink(sink_cx, st, buf, (uint32_t)len);
	}
	if (i == 0) {
		if (rp->gap) rp->gap(rp->gap_cx, "no boot-time scan recorded (L23 did not run or gave no result): scan refused");
		return SWSTA_EIO;
	}
	lost = rp->lost ? rp->lost() : 0;
	if (lost) SWSTA_SNPRINTF(g, sizeof g, "boot-time scan list replayed (stale, %u events, %u LOST: record full)", (unsigned)i, (unsigned)lost);
	else SWSTA_SNPRINTF(g, sizeof g, "scan list replayed from the boot-time scan (stale, %u events)", (unsigned)i);
	if (rp->gap) rp->gap(rp->gap_cx, g);
	return 0;
}

struct swr_cb {
	void (*log)(void *cx, const char *line);
	void (*gap)(void *cx, const char *text);	/* text <= 79 chars */
	void *cx;
};

struct swr {
	struct swsta_radio radio;	/* hand &r.radio to swsta_init / a80211_init; radio.ctx points back here */
	struct swr_link link; struct swr_cb cb;
	int on, scanning, complete;
	struct swsta_bss bss[SWR_MAX_BSS]; uint32_t n, cur;
	uint32_t scans, events, parsed, dup, dropped, ignored, partial, gaps, replaced;	/* replaced = records that took the place of the weakest entry of a full table (review 0293 LOW 2) */
	uint16_t sgap;			/* SWR_SG_* bits: the record GAPs already told in this scan (reset with the table) */
	/* W-UI (sprint 2026-11-08, xnu patch 0268 keep mode): the boot run's link is still up. kept.joined = 1 makes status report it, join of the same SSID succeed with a note, any other join
	 * and leave refuse with a GAP, power off refuse; set by swr_kept_set only. */
	struct swsta_status kept; uint32_t kept_joins, kept_refused, kept_rssi_n;
	/* patch 0281 (W-D6): the keep thread saw the AP link go down (link-loss event): kept.joined is cleared by swr_kept_clear and stays so (UP again is patch 0282); a join while this is set is refused */
	uint8_t kept_down; uint32_t kept_down_n, kept_up_n;
	/* 2026-11-11 (W-D3-weak-security): the join target's own beacon/probe record (IEs, rates, beacon, noise, SNR, channel flags) as the boot scan read it (swr_kept_target); Get 103 answers it from the first request on, before any keep scan */
	struct swsta_bss kept_bss; uint8_t kept_bss_ok;
	/* patch 0292: the live scan (see swr_ascan). live = 1 while a scan requested from the keep thread is outstanding (scanning is then 1 too) */
	struct swr_ascan ascan; uint8_t live; uint32_t live_req, live_done, live_bad, live_refused, live_fallback;
};

static inline void swr_gap(struct swr *s, const char *text)
{
	s->gaps++;
	if (s->cb.gap) s->cb.gap(s->cb.cx, text);
}

static inline uint32_t swr_le16(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8; }
static inline uint32_t swr_le32(const uint8_t *p) { return swr_le16(p) | swr_le16(p + 2) << 16; }

static inline int swr_same6(const uint8_t *a, const uint8_t *b)
{
	uint32_t i;
	for (i = 0; i < 6; i++) if (a[i] != b[i]) return 0;
	return 1;
}

static inline int swr_eqn(const uint8_t *a, const uint8_t *b, uint32_t n)
{
	uint32_t i;
	for (i = 0; i < n; i++) if (a[i] != b[i]) return 0;
	return 1;
}

/* once per scan: a GAP for a record field the shim could not use (rule 7); the bit keeps one busy scan from flooding the log */
static inline void swr_gap1(struct swr *s, uint32_t bit, const char *text)
{
	if (s->sgap & bit) return;
	s->sgap |= (uint16_t)bit;
	swr_gap(s, text);
}

/* the record's channel flags from the chanspec [src refs/wlan-stage5/bcmwifi_channels.h.new: band mask 0xc000 at :149, 2G 0x0000 :151, 5G 0xc000 :154, bandwidth mask 0x3800 :137, BW_20 0x1000 :141, BW_40 0x1800 :142, BW_80 0x2000 :143]
 * to W-D3 s.3 [obs-RE] 0x2 = 20 MHz, 0x4 = 40 MHz, 0x8 = 2.4 GHz, 0x10 = 5 GHz. 80 / 160 MHz (and anything else): the band bit only, the width bit for them is open (GAP once per scan). */
static inline uint32_t swr_chflags(struct swr *s, uint32_t cs)
{
	uint32_t f = 0, band = cs & 0xC000u, bw = cs & 0x3800u;

	if (band == 0x0000u) f = 0x8u;
	else if (band == 0xC000u) f = 0x10u;
	else swr_gap1(s, SWR_SG_BAND, "channel band is not 2.4/5 GHz, channel flags 0");
	if (bw == 0x1000u) f |= 0x2u;
	else if (bw == 0x1800u) f |= 0x4u;
	else if (band == 0x0000u || band == 0xC000u) {
		if (bw == 0x2000u) swr_gap1(s, SWR_SG_CHW, "80 MHz channel flag bit open, band bit only");
		else swr_gap1(s, SWR_SG_CHW2, "channel width flag bit open (not 20/40 MHz), band bit only");
	}
	return f;
}

/* The IE block ie[0..n) of one record into b: whole IEs (id, len, len bytes) from the start while they fit SWR_IE_CAP; a walk whose length runs past the end stops at the last whole IE (GAP once per scan); a
 * block cut at the cap is a GAP once per scan, and a security IE that is NOT kept (RSN id 48, or the WPA vendor IE id 221 with 00:50:f2 type 1 [W-D3 s.4 (f)]; iOS reads security only from these) is a GAP of its own. */
static inline void swr_ie(struct swr *s, struct swsta_bss *b, const uint8_t *ie, uint32_t n)
{
	uint32_t o = 0, l, stored = 0, cut = 0, lost = 0, bad = 0, sec;

	while (o < n) {
		if (n - o < 2u || (uint32_t)ie[o + 1] > n - o - 2u) { bad = 1; break; }
		l = ie[o + 1];
		sec = ie[o] == 48u || (ie[o] == 221u && l >= 4u && ie[o + 2] == 0x00u && ie[o + 3] == 0x50u && ie[o + 4] == 0xF2u && ie[o + 5] == 0x01u);
		if (stored == o && o + 2u + l <= SWR_IE_CAP) {
			SWSTA_MEMCPY(b->ie + o, ie + o, 2u + l);
			stored = o + 2u + l;
		} else {
			cut = 1;
			if (sec) lost = 1;
		}
		o += 2u + l;
	}
	b->ie_len = (uint16_t)stored;
	if (bad) swr_gap1(s, SWR_SG_IEBAD, "bss_info IE walk runs past the block, cut at the last whole IE");
	if (cut) swr_gap1(s, SWR_SG_IECUT, "bss_info IEs above 512 B, cut at an IE boundary");
	if (lost) swr_gap1(s, SWR_SG_IESEC, "security IE beyond IE cap, network shows as WEP");
}

/* One ESCAN_RESULT event (a sink call). Never trusts len: every read is bounded by it. */
static inline void swr_event(void *cx, uint32_t status, const uint8_t *res, uint32_t len)
{
	struct swr *s = cx;
	struct swsta_bss b;
	const uint8_t *r;
	uint32_t i, ch, cs, cap, sl, avail, rlen, nr, ioff, ilen;
	char g[80];

	s->events++;
	if (!res || len < SWR_RES_HDR) { s->ignored++; swr_gap(s, "scan result event shorter than the 12-byte header, ignored"); return; }
	if (swr_le16(res + 8) < SWR_SYNC || swr_le16(res + 8) > SWR_SYNC + SWR_SYNC_N) { s->ignored++; swr_gap(s, "scan result sync_id differs from ours, ignored"); return; }
	if (status == SWR_ST_OK) { s->complete = 1; return; }
	if (status != SWR_ST_PARTIAL) {
		s->ignored++;
		SWSTA_SNPRINTF(g, sizeof g, "scan result status %u not handled, scan treated as unfinished", (unsigned)status);
		swr_gap(s, g);
		return;
	}
	s->partial++;
	if (swr_le16(res + 10) == 0 || len < SWR_RES_HDR + SWR_BSS_NEED) { s->ignored++; swr_gap(s, "partial scan result without a full bss record, ignored"); return; }
	if (swr_le16(res + 10) > 1) swr_gap(s, "scan result bss_count above 1, only the first record parsed");
	r = res + SWR_RES_HDR;
	if (swr_le32(r) != SWR_BSS_VER) { s->ignored++; swr_gap(s, "bss_info version is not 109, record not parsed"); return; }
	if (swr_le32(r + 4) < SWR_BSS_NEED) { s->ignored++; swr_gap(s, "bss_info length shorter than the fields read, record not parsed"); return; }
	sl = r[18];
	if (sl > 32u) { s->ignored++; swr_gap(s, "bss_info ssid_len above 32, record not parsed"); return; }
	SWSTA_MEMSET(&b, 0, sizeof b);
	SWSTA_MEMCPY(b.bssid, r + 8, 6);
	b.ssid_len = (uint8_t)sl;
	SWSTA_MEMCPY(b.ssid, r + 19, sl);
	cs = swr_le16(r + 72);
	ch = r[88] != 0 ? r[88] : (cs & 0xFFu);		/* the control channel, else the chanspec channel byte [src G2] */
	b.channel = ch;
	b.rssi = (int32_t)(int16_t)swr_le16(r + 78);
	cap = swr_le16(r + 16);
	b.sec = (cap & SWR_CAP_PRIVACY) ? SWSTA_SEC_OTHER : SWSTA_SEC_OPEN;
	/* 2026-11-11 scan records. The fields through ctl_ch (+88) are inside the SWR_BSS_NEED bytes every record has (the boot-time replay too); SNR and the IEs need a longer record. */
	rlen = swr_le32(r + 4);
	avail = len - SWR_RES_HDR;
	if (avail > rlen) avail = rlen;		/* the record's own length bounds what is read */
	b.beacon = (uint16_t)swr_le16(r + 14);
	b.noise = (int16_t)(int8_t)r[80];
	b.chflags = swr_chflags(s, cs);
	nr = swr_le32(r + 52);
	if (nr > SWSTA_RATE_MAX) { swr_gap1(s, SWR_SG_RATES, "bss_info rate count above 15, clamped"); nr = SWSTA_RATE_MAX; }
	b.nrates = (uint8_t)nr;
	for (i = 0; i < nr; i++) b.rates[i] = (uint8_t)((r[56 + i] & 0x7Fu) / 2u);
	if (avail >= SWR_BSS_SNR_END) {
		b.snr = (int16_t)swr_le16(r + 124);
		ioff = swr_le16(r + 116); ilen = swr_le32(r + 120);
		if (ilen != 0) {
			if (ioff < SWR_BSS_SNR_END || ioff > rlen || ilen > rlen - ioff) swr_gap1(s, SWR_SG_IEPOS, "bss_info ie_offset/ie_length outside the record, IEs dropped");
			else {
				if (ioff > avail || ilen > avail - ioff) { swr_gap1(s, SWR_SG_IEPOS, "bss_info IEs beyond the bytes read, partial IE block"); ilen = ioff > avail ? 0 : avail - ioff; }
				swr_ie(s, &b, r + ioff, ilen);
			}
		}
	}
	for (i = 0; i < s->n; i++) {
		if (!swr_same6(s->bss[i].bssid, b.bssid)) continue;
		s->dup++;
		if (b.rssi > s->bss[i].rssi) s->bss[i] = b;	/* the same BSSID again (another channel or a repeat): keep the stronger */
		return;
	}
	if (s->n >= SWR_MAX_BSS) {	/* review 0293 LOW 2: a full table keeps the strongest 64: a stronger record takes the place of the weakest entry (the first of equals), a weaker or equal one is dropped; one GAP per scan, the counts go into the scan-end line */
		uint32_t w = 0;
		for (i = 1; i < s->n; i++) if (s->bss[i].rssi < s->bss[w].rssi) w = i;
		swr_gap1(s, SWR_SG_FULL, "scan list full (64 BSSID): weakest replaced by a stronger record, else dropped");
		if (b.rssi > s->bss[w].rssi) { s->bss[w] = b; s->replaced++; } else s->dropped++;
		return;
	}
	s->bss[s->n++] = b;
	s->parsed++;
}

static inline int swr_power(void *c, int op, int *state)
{
	struct swr *s = c;
	if (op == SWSTA_PWR_OFF && s->kept.joined) {	/* W-UI: the chip cannot be taken down while the boot-run link is kept (no engine undo path in the running system) */
		s->kept_refused++;
		swr_gap(s, "power off refused: the boot-run link is kept and cannot be torn down");
		return SWSTA_EOPNOTSUPP;
	}
	if (op == SWSTA_PWR_ON || op == SWSTA_PWR_OFF) {
		if (op == SWSTA_PWR_ON && !s->link.escan) { swr_gap(s, "power on refused: no escan link attached"); return SWSTA_EIO; }
		s->on = op == SWSTA_PWR_ON;
		swr_gap(s, "power is a flag only: the chip is brought up by the boot sequence");
	} else if (op != SWSTA_PWR_GET) {
		swr_gap(s, "power op unknown, refused");
		return SWSTA_EINVAL;
	}
	*state = s->on;
	return 0;
}

static inline int swr_scan_start(void *c)
{
	struct swr *s = c;
	struct swsta_rev ev;
	int rc;

	if (!s->on) { swr_gap(s, "scan refused: radio is off"); return SWSTA_EIO; }
	if (s->scanning) { swr_gap(s, "scan refused: one is already running"); return SWR_EBUSY; }
	if (s->ascan.request != NULL) {	/* patch 0292: keep mode, the live scan */
		int rq;

		if (s->kept_down) {	/* the re-join machine (patch 0282) owns the engine until it is UP again: never scan, never start a second thing behind its back */
			s->live_refused++;
			if (s->cb.log) s->cb.log(s->cb.cx, "scan refused: the kept link is DOWN (AP lost), the driver is re-joining");
			swr_gap(s, "scan refused while the kept link is DOWN (AP lost): the re-join (patch 0282) runs first");
			return SWR_E_NETDOWN;
		}
		rq = s->ascan.request(s->ascan.cx);
		if (rq == 0) {
			s->scanning = 1; s->live = 1; s->scans++; s->live_req++;
			if (s->cb.log) s->cb.log(s->cb.cx, "scan requested: the keep thread runs a live passive escan (all channels), SCAN_DONE follows");
			return 0;
		}
		if (rq != (int)SWR_E_NXIO) {
			char g[80];

			s->live_refused++;
			SWSTA_SNPRINTF(g, sizeof g, "live scan refused by the keep thread (errno %d: %s)", rq, rq == (int)SWR_E_NETDOWN ? "link not UP" : rq == (int)SWR_EBUSY ? "one is already pending" : "see the log");
			swr_gap(s, g);
			return (uint32_t)rq == SWR_E_NETDOWN || (uint32_t)rq == SWR_EBUSY ? rq : (int)SWSTA_EIO;
		}
		s->live_fallback++;
		swr_gap(s, "live scan not available (off for this boot): the boot-time listing is replayed instead, STALE");
	}
	if (!s->link.escan) { swr_gap(s, "scan refused: no escan link attached"); return SWSTA_EIO; }
	s->scanning = 1; s->complete = 0; s->n = 0; s->cur = 0; s->sgap = 0; s->scans++;
	rc = s->link.escan(s->link.cx, swr_event, s);
	s->scanning = 0;
	if (rc != 0) {
		char g[80];
		SWSTA_SNPRINTF(g, sizeof g, "escan link failed rc %d, %u BSS kept as a partial list", rc, (unsigned)s->n);
		swr_gap(s, g);
		return SWSTA_EIO;
	}
	if (!s->complete) {
		char g[80];
		SWSTA_SNPRINTF(g, sizeof g, "scan-done status 0 not seen: listing %u BSS as a partial result", (unsigned)s->n);
		swr_gap(s, g);
	}
	s->cur = 0;
	if (s->radio.up) { ev.kind = SWSTA_REV_SCAN_DONE; ev.result = 0; ev.reason = 0; s->radio.up(s->radio.up_ctx, &ev); }
	else swr_gap(s, "scan done not posted: no event callback set (swsta_init not run)");
	return 0;
}

/* The scratch table the keep thread fills (its sink is swr_event on it): start empty. The per-scan counters restart, the gap counter stays. Called when the scan is requested, before the keep thread sees the request. */
static inline void swr_tab_reset(struct swr *t)
{
	t->n = 0; t->cur = 0; t->complete = 0; t->sgap = 0;
	t->events = t->parsed = t->dup = t->dropped = t->ignored = t->partial = t->replaced = 0;
}

static inline void swr_ascan_set(struct swr *s, int (*request)(void *cx), void *cx)
{
	s->ascan.request = request; s->ascan.cx = cx;
}

/* The keep thread's live scan ended (kind SWR_SCAN_*; `tab` = its scratch table, `ms` = how long, `ev` = ESCAN_RESULT events of our sync id it counted). The caller holds the radio mutex. Swaps the list in (whatever was collected, also after a timeout / link loss / failure: the
 * old listing would be stale, so it is replaced, and the kind says so in a GAP), rewinds Get 11 and posts SCAN_DONE so that wifid's pending Set 10 resolves. One log line, counts only (never an SSID or BSSID). 1 = delivered, 0 = no scan was outstanding (a GAP). */
static inline int swr_scan_finish(struct swr *s, const struct swr *tab, uint32_t kind, uint32_t ms, uint32_t ev)
{
	struct swsta_rev e;
	char l[160], g[80];
	uint32_t n;

	if (!s->scanning || !s->live) {
		s->live_bad++;
		swr_gap(s, "live scan result delivered but none was requested, dropped");
		return 0;
	}
	n = tab->n > SWR_MAX_BSS ? SWR_MAX_BSS : tab->n;
	SWSTA_MEMCPY(s->bss, tab->bss, n * sizeof s->bss[0]);
	s->n = n; s->cur = 0; s->complete = kind == SWR_SCAN_COMPLETE; s->scanning = 0; s->live = 0; s->live_done++;
	s->events += tab->events; s->parsed += tab->parsed; s->dup += tab->dup; s->dropped += tab->dropped; s->ignored += tab->ignored; s->partial += tab->partial; s->replaced += tab->replaced;
	SWSTA_SNPRINTF(l, sizeof l, "radio scan done: %s, records=%u events=%u partial=%u dup=%u dropped=%u ignored=%u ms=%u replaced=%u", kind == SWR_SCAN_COMPLETE ? "complete" : kind == SWR_SCAN_TIMEOUT ? "timeout" : kind == SWR_SCAN_LINKLOST ? "link lost" :
	    kind == SWR_SCAN_REFUSED ? "refused" : "failed", (unsigned)n, (unsigned)ev, (unsigned)tab->partial, (unsigned)tab->dup, (unsigned)tab->dropped, (unsigned)tab->ignored, (unsigned)ms, (unsigned)tab->replaced);
	if (s->cb.log) s->cb.log(s->cb.cx, l);
	if (kind != SWR_SCAN_COMPLETE) {
		SWSTA_SNPRINTF(g, sizeof g, "live scan ended unfinished (kind %u), %u BSS listed as a partial result", (unsigned)kind, (unsigned)n);
		swr_gap(s, g);
	}
	if (s->radio.up) { e.kind = SWSTA_REV_SCAN_DONE; e.result = 0; e.reason = 0; s->radio.up(s->radio.up_ctx, &e); }
	else swr_gap(s, "scan done not posted: no event callback set (swsta_init not run)");
	return 1;
}

/* optional radio verb: the record to answer Get 103 of the kept link with, for this BSSID. Order: the entry of the latest scan list if it has IEs (newer), else the join target's own record kept at the hand-over (swr_kept_target), else a scan-list entry without IEs.
 * 1 = found (the caller still sees ie_len 0 if no source had IEs) */
static inline int swr_bss_find(void *c, const uint8_t bssid[6], struct swsta_bss *out)
{
	struct swr *s = c;
	uint32_t i, hit = s->n;
	for (i = 0; i < s->n; i++) {
		if (swr_same6(s->bss[i].bssid, bssid)) { hit = i; break; }
	}
	if (hit < s->n && s->bss[hit].ie_len != 0) { *out = s->bss[hit]; return 1; }
	if (s->kept_bss_ok && swr_same6(s->kept_bss.bssid, bssid)) { *out = s->kept_bss; return 1; }
	if (hit < s->n) { *out = s->bss[hit]; return 1; }
	return 0;
}

static inline int swr_bss_next(void *c, struct swsta_bss *out)
{
	struct swr *s = c;
	if (s->cur >= s->n) return 0;
	*out = s->bss[s->cur++];
	return 1;
}

/* The kept link (W-UI; sprint 2026-11-08, xnu patch 0268): the boot run joined and the dongle was left running, so the truth is "joined" with the values the run knew. NO bus access and NO store:
 * the values are copied once by the glue from the firmware's own report after L26 (swr_kept_set), never refreshed (said so once at set time).
 * Join of the SAME SSID (and the same BSSID when the request names one) succeeds with a printed note and the ASSOC_DONE event (Set 20 -> Get 21 answers success); any other join, and leave,
 * are refused with a GAP (a leave would need the engine to disassociate: not built, W-JOIN). */
static inline int swr_join(void *c, const struct swsta_join *j)
{
	struct swr *s = c;

	if (s->kept_down) {	/* patch 0281: the AP link was lost; no join is answered success until the keep thread's own re-join (patch 0282, swr_kept_up) has a new 4-way done: wifid's join request must not start a second join behind the machine's back */
		s->kept_refused++;
		if (s->cb.log) s->cb.log(s->cb.cx, "join refused: the kept link is DOWN (AP lost), the driver re-joins by itself");
		swr_gap(s, "join refused while the kept link is DOWN (AP lost): the keep thread re-joins by itself (patch 0282)");
		return SWSTA_EOPNOTSUPP;
	}
	if (s->kept.joined) {
		if (j != NULL && j->ssid_len == s->kept.ssid_len && swr_eqn(j->ssid, s->kept.ssid, j->ssid_len) && (!j->has_bssid || swr_same6(j->bssid, s->kept.bssid))) {
			struct swsta_rev ev;

			s->kept_joins++;
			if (s->cb.log) s->cb.log(s->cb.cx, "join of the kept network: already joined by the boot run, answered success, no radio action");
			if (s->radio.up) { ev.kind = SWSTA_REV_ASSOC_DONE; ev.result = 0; ev.reason = 0; s->radio.up(s->radio.up_ctx, &ev); }
			else swr_gap(s, "kept join: ASSOC_DONE not posted, no event callback set (swsta_init not run)");
			return 0;
		}
		s->kept_refused++;
		swr_gap(s, "join of another network refused: only the boot-run link is kept");
		return SWSTA_EOPNOTSUPP;
	}
	swr_gap(s, "join not supported by the radio backend (W-JOIN)");
	return SWSTA_EOPNOTSUPP;
}

static inline int swr_leave(void *c)
{
	struct swr *s = c;

	if (s->kept.joined) s->kept_refused++;
	swr_gap(c, "leave not supported by the radio backend (W-JOIN)");
	return SWSTA_EOPNOTSUPP;
}

static inline int swr_status(void *c, struct swsta_status *out)
{
	struct swr *s = c;

	if (s->kept.joined) { *out = s->kept; return 0; }
	SWSTA_MEMSET(out, 0, sizeof *out);	/* never joined: the backend has no join, so "not joined" is the truth, not a stub */
	return 0;
}

/* Marks the radio as holding the boot-run link, with the values the FIRMWARE reported after L26 (sweet_wlan_l25_hs.h swl25_live: the SSID element of its association request, WLC_GET_BSSID,
 * "chanspec", WLC_GET_RSSI), never the join's credentials or the boot scan record. `ok` = swl25h.live_ok (1 ssid, 2 bssid, 4 channel, 8 rssi). SSID and BSSID are required (else not joined, a GAP);
 * a missing channel or RSSI answers 0 with a GAP. The values are read once at the hand-over and not refreshed (a GAP says so). Returns 1 = joined. Call before swsta_init so the power flag is read as ON.
 * One log line without the SSID or BSSID bytes. */
static inline int swr_kept_set(struct swr *s, const uint8_t *ssid, uint32_t ssid_len, const uint8_t bssid[6], uint32_t channel, int32_t rssi, uint32_t ok)
{
	char l[140];

	SWSTA_MEMSET(&s->kept, 0, sizeof s->kept);
	if ((ok & 3u) != 3u || ssid == NULL || ssid_len == 0 || ssid_len > 32u || bssid == NULL) {
		swr_gap(s, "kept link not set: the firmware's SSID or BSSID was not read");
		return 0;
	}
	s->kept.joined = 1;
	SWSTA_MEMCPY(s->kept.ssid, ssid, ssid_len);
	s->kept.ssid_len = (uint8_t)ssid_len;
	SWSTA_MEMCPY(s->kept.bssid, bssid, 6);
	s->kept.sec = SWSTA_SEC_WPA2_PSK;	/* the boot run joined with the WPA2-PSK credentials (swc_creds security 0) and the 4-way installed CCMP keys [src sweet_wlan_cred.h, sweet_wlan_l25_hs.h] */
	s->on = 1;
	s->kept.channel = (ok & 4u) ? channel : 0u;
	s->kept.rssi = (ok & 8u) ? rssi : 0;
	if (!(ok & 4u)) swr_gap(s, "kept link: channel not read from the firmware, answered 0");
	if (!(ok & 8u)) swr_gap(s, "kept link: RSSI not read from the firmware, answered 0");
	swr_gap(s, "kept link: firmware values read once at the hand-over; RSSI is refreshed only by swr_kept_rssi (patch 0273 keep thread), channel/BSSID/SSID are that snapshot");
	SWSTA_SNPRINTF(l, sizeof l, "radio kept link: joined (firmware values) ssid_len=%u chan=%u rssi=%d sec=wpa2-psk ok=%#x", (unsigned)ssid_len, (unsigned)s->kept.channel, (int)s->kept.rssi, (unsigned)ok);
	if (s->cb.log) s->cb.log(s->cb.cx, l);
	return 1;
}

/* Part A (sprint-wifi 2026-11-09): a newer RSSI the FIRMWARE reported (WLC_GET_RSSI, -127..-1) for the kept link; what Get 16 / the link status answer from now on. 1 = taken, 0 = refused (not joined / out of range, a GAP) */
static inline int swr_kept_rssi(struct swr *s, int32_t rssi)
{
	if (!s->kept.joined || rssi >= 0 || rssi < -127) {
		swr_gap(s, "kept link: RSSI refresh refused (not joined or value outside -127..-1)");
		return 0;
	}
	s->kept.rssi = rssi;
	s->kept_rssi_n++;
	return 1;
}

/* patch 0281 (W-D6): the AP link is gone (the keep thread's detector saw a link-loss event). Clears kept.joined (swr_status then reports not joined, so the LINK_CHANGED payload byte 0 is isLinkDown = 1), remembers it (kept_down: swr_join answers failure), and,
 * when `post` is non-zero, fires SWSTA_REV_LINK_DOWN so wifid gets LINK_CHANGED. The caller holds the radio mutex (sweet_wlan_a80211_kept_link). No firmware command, no bus access. 1 = cleared and posted/ready, 0 = nothing to clear (not kept-joined, or already down). */
static inline int swr_kept_clear(struct swr *s, int post)
{
	struct swsta_rev ev;

	if (s->kept_down || !s->kept.joined) {
		return 0;
	}
	s->kept.joined = 0;
	s->kept_down = 1;
	s->kept_down_n++;
	if (s->cb.log) s->cb.log(s->cb.cx, "radio kept link: DOWN (AP link lost), joined cleared, join requests are refused");
	if (post) {
		if (s->radio.up) { ev.kind = SWSTA_REV_LINK_DOWN; ev.result = 0; ev.reason = 0; s->radio.up(s->radio.up_ctx, &ev); }
		else swr_gap(s, "kept link DOWN: LINK_DOWN not posted, no event callback set (swsta_init not run)");
	}
	return 1;
}

/* 2026-11-11 (W-D3-weak-security): the join target's record as the boot scan (L23) or the BSS_INFO answer carried it, handed over with the kept link. ie = the IE block (ie_len bytes, ie_short = the record had more than the caller kept), rates = 500 kbps entries.
 * The IEs are cut to SWR_IE_CAP at an IE boundary with the same GAPs as a scan record (swr_ie). Only for the kept BSSID: another BSSID is refused with a GAP (an earlier record stays). No IEs at all = a GAP that says Settings will show Weak Security (the privacy bit is NOT dropped). 1 = taken */
static inline int swr_kept_target(struct swr *s, const uint8_t bssid[6], const uint8_t *ie, uint32_t ie_len, uint32_t ie_short, uint32_t chspec, uint32_t beacon, int32_t noise, int32_t snr, const uint8_t *rates, uint32_t nrates)
{
	struct swsta_bss b;
	uint32_t i;

	if (bssid == NULL || !s->kept.joined || !swr_same6(s->kept.bssid, bssid)) {
		swr_gap(s, "kept target record is not for the kept BSSID, ignored");
		return 0;
	}
	SWSTA_MEMSET(&b, 0, sizeof b);
	SWSTA_MEMCPY(b.bssid, bssid, 6);
	SWSTA_MEMCPY(b.ssid, s->kept.ssid, s->kept.ssid_len);
	b.ssid_len = s->kept.ssid_len;
	b.channel = s->kept.channel; b.rssi = s->kept.rssi; b.sec = s->kept.sec;
	s->sgap = 0;
	b.chflags = swr_chflags(s, chspec);
	b.beacon = (uint16_t)beacon;
	b.noise = (int16_t)noise; b.snr = (int16_t)snr;
	if (nrates > SWSTA_RATE_MAX) { swr_gap1(s, SWR_SG_RATES, "kept target rate count above 15, clamped"); nrates = SWSTA_RATE_MAX; }
	b.nrates = (uint8_t)nrates;
	for (i = 0; i < nrates; i++) b.rates[i] = (uint8_t)((rates[i] & 0x7Fu) / 2u);
	if (ie_len != 0 && ie != NULL) swr_ie(s, &b, ie, ie_len);
	if (ie_short) swr_gap1(s, SWR_SG_IEPOS, "kept target IE block longer than 1024 B, the tail is not kept");
	if (b.ie_len == 0) swr_gap(s, "kept target record has no IEs: Get 103 answers privacy without RSN IE, Settings will show Weak Security");
	s->kept_bss = b;
	s->kept_bss_ok = 1;
	if (s->cb.log) {
		char l[100];
		SWSTA_SNPRINTF(l, sizeof l, "radio kept target record: ie_len=%u rates=%u beacon=%u chflags=%#x", (unsigned)b.ie_len, (unsigned)b.nrates, (unsigned)b.beacon, (unsigned)b.chflags);
		s->cb.log(s->cb.cx, l);
	}
	return 1;
}

/* patch 0282 (W-D6 boot B): the keep thread's re-join is done (new SSID/LINK events, a new 4-way, keys installed). The kept SSID stays; the BSSID (a re-join may land on another AP), the channel and the RSSI are the values the FIRMWARE just reported (swl25h.live_*; `ok` bits as swr_kept_set:
 * 2 bssid, 4 channel, 8 rssi; the BSSID is required). Sets kept.joined, clears kept_down and fires SWSTA_REV_ASSOC_DONE result 0 (kev 9, 2, 3, 4 incl. LINK_CHANGED isLinkDown 0), as swr_kept_announce does at the hand-over. The caller holds the radio mutex. No bus access.
 * 1 = joined again and posted, 0 = refused (not DOWN, or no BSSID: a GAP) */
static inline int swr_kept_up(struct swr *s, const uint8_t bssid[6], uint32_t channel, int32_t rssi, uint32_t ok)
{
	struct swsta_rev ev;

	if (!s->kept_down || s->kept.ssid_len == 0 || bssid == NULL || (ok & 2u) == 0u) {
		swr_gap(s, "kept link UP refused: the link was not DOWN, the SSID is not kept or the BSSID was not read");
		return 0;
	}
	if (s->kept_bss_ok && !swr_same6(s->kept_bss.bssid, bssid)) { s->kept_bss_ok = 0; swr_gap(s, "kept link UP on another BSSID: the kept target record (IEs) is dropped"); }
	SWSTA_MEMCPY(s->kept.bssid, bssid, 6);
	if (ok & 4u) s->kept.channel = channel; else swr_gap(s, "kept link UP: channel not read from the firmware, the previous one stays");
	if ((ok & 8u) && rssi < 0 && rssi >= -127) s->kept.rssi = rssi; else swr_gap(s, "kept link UP: RSSI not read from the firmware, the previous one stays");
	s->kept.joined = 1;
	s->kept_down = 0;
	s->kept_up_n++;
	if (s->cb.log) s->cb.log(s->cb.cx, "radio kept link: UP again (re-joined by the keep thread), joined set, ASSOC_DONE posted");
	if (s->radio.up) { ev.kind = SWSTA_REV_ASSOC_DONE; ev.result = 0; ev.reason = 0; s->radio.up(s->radio.up_ctx, &ev); }
	else swr_gap(s, "kept link UP: ASSOC_DONE not posted, no event callback set (swsta_init not run)");
	return 1;
}

/* After swsta_init (the radio.up callback exists now): tell the station that the kept link is up, exactly as a finished join does (ASSOC_DONE result 0 -> kev 9, 2, 3, 4; Get 21 then answers success). 1 = posted. */
static inline int swr_kept_announce(struct swr *s)
{
	struct swsta_rev ev;

	if (!s->kept.joined) return 0;
	if (!s->radio.up) { swr_gap(s, "kept link: ASSOC_DONE not posted, no event callback set (swsta_init not run)"); return 0; }
	ev.kind = SWSTA_REV_ASSOC_DONE; ev.result = 0; ev.reason = 0;
	s->radio.up(s->radio.up_ctx, &ev);
	if (s->cb.log) s->cb.log(s->cb.cx, "radio kept link: ASSOC_DONE posted (wifid sees the boot-run link as joined; Get 21 answers success)");
	return 1;
}

static inline void swr_init(struct swr *s, const struct swr_link *link, const struct swr_cb *cb)
{
	SWSTA_MEMSET(s, 0, sizeof *s);
	if (link) s->link = *link;
	if (cb) s->cb = *cb;
	s->radio.power = swr_power; s->radio.scan_start = swr_scan_start; s->radio.bss_next = swr_bss_next; s->radio.bss_find = swr_bss_find;
	s->radio.join = swr_join; s->radio.leave = swr_leave; s->radio.status = swr_status; s->radio.ctx = s;
}

static inline void swr_summary(struct swr *s)
{
	char l[160];
	SWSTA_SNPRINTF(l, sizeof l, "radio scans=%u events=%u parsed=%u dup=%u dropped=%u ignored=%u partial=%u gaps=%u replaced=%u", (unsigned)s->scans, (unsigned)s->events,
	    (unsigned)s->parsed, (unsigned)s->dup, (unsigned)s->dropped, (unsigned)s->ignored, (unsigned)s->partial, (unsigned)s->gaps, (unsigned)s->replaced);
	if (s->cb.log) s->cb.log(s->cb.cx, l);
	if (s->kept.joined) {
		SWSTA_SNPRINTF(l, sizeof l, "radio kept joins_answered=%u refused=%u", (unsigned)s->kept_joins, (unsigned)s->kept_refused);
		if (s->cb.log) s->cb.log(s->cb.cx, l);
	}
	if (s->ascan.request != NULL) {
		SWSTA_SNPRINTF(l, sizeof l, "radio live scans requested=%u done=%u refused=%u fallback=%u bad_result=%u", (unsigned)s->live_req, (unsigned)s->live_done, (unsigned)s->live_refused, (unsigned)s->live_fallback, (unsigned)s->live_bad);
		if (s->cb.log) s->cb.log(s->cb.cx, l);
	}
}

#endif /* SWEET_WLAN_RADIO_H */
