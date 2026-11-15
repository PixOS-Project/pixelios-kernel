/*
 * BCM4389 scan results of run G2 (xnu patch 0174, T-08fd; goal W-D7 / P-W4, Settings > Wi-Fi).
 * Included by sweet_wlan_g2.h after struct swg_ev (R29: new code goes into a new module).
 * Host tested through the real swg_e_l23 by shim/wlan_radio_host_test.c (wlan_radio_g2_scan.c).
 *
 * The scan runs only inside the boot run (W17 hook): afterwards swg_undo restores the EP/RC
 * Command and mask 0xC34 := 0 and the CA7 is re-halted, so no later scan is possible. Every
 * ESCAN_RESULT event of OUR sync id that the run consumes is therefore copied into swg_rec, in
 * order: its status and the bytes read (the 12 B wl_escan_result header and, for a PARTIAL with
 * a full record, the first 89 B of the wl_bss_info). Host memory only: NO bus access, no store
 * of any kind. The radio's escan link (patch 0201, swr_replay_escan over sw_radio_replay)
 * replays them to its sink through sweet_wlan_g2_escan_rec() after the boot run ended; the
 * writer is the synchronous boot path (sweet_wlan_init), a reader must start after it returned.
 * The listing is the boot-time one (stale). One slot per event the run may consume at most
 * (SWG_E_MAX): an overflow is a counted loss (swg_rec.lost) and a gap line, never a write past
 * the buffer. swg_e_l23 clears the record at its start.
 */
#ifndef SWEET_WLAN_G2R_H
#define SWEET_WLAN_G2R_H

/* bytes of one recorded ESCAN_RESULT: the 12 B header + the 89 B record the radio parses */
#define SWG_REC_RES     (12u + SWG_E_BSS_NEED)

struct swg_rec {
	uint32_t n, lost;
	struct {
		uint32_t status, len;
		uint8_t b[SWG_REC_RES];
	} e[SWG_E_MAX];
};

static struct swg_rec swg_rec;

static void
swg_rec_add(uint32_t status, const uint8_t *res, uint32_t len)
{
	if (len == 0u || len > SWG_REC_RES) {
		swg_rec.lost++;
		swl_gap("scan", "recorded result has no valid length, not kept for the replay", "high");
	} else if (swg_rec.n >= SWG_E_MAX) {
		swg_rec.lost++;
		swl_gap("scan", "event record full, result not kept for the replay", "high");
	} else {
		swg_rec.e[swg_rec.n].status = status;
		swg_rec.e[swg_rec.n].len = len;
		memcpy(swg_rec.e[swg_rec.n].b, res, len);
		swg_rec.n++;
	}
}

/* The replay side, called from patch 0201's glue (a separate file): event `i` (0-based) of the
 * boot-time scan. Returns its length (> 0) and fills *status and out[0..len) when cap >= len;
 * 0 = no event i (the end); -1 = out is too small. */
int sweet_wlan_g2_escan_rec(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap);
int
sweet_wlan_g2_escan_rec(uint32_t i, uint32_t *status, uint8_t *out, uint32_t cap)
{
	if (i >= swg_rec.n) {
		return 0;
	}
	if (cap < swg_rec.e[i].len) {
		return -1;
	}
	*status = swg_rec.e[i].status;
	memcpy(out, swg_rec.e[i].b, swg_rec.e[i].len);
	return (int)swg_rec.e[i].len;
}

/* the events the record could not keep (full, or no valid length); the replay says so */
uint32_t sweet_wlan_g2_escan_lost(void);
uint32_t
sweet_wlan_g2_escan_lost(void)
{
	return swg_rec.lost;
}

/* the SSID of a bss line: printable ASCII as it is, anything else as hex (privacy: the run log only) */
static void
swg_e_ssid(const struct swg_ev *ev, char *out, size_t cap)
{
	uint32_t i, p = 0;
	int pr = ev->sl <= 32u;

	for (i = 0; i < ev->sl && i < 32u; i++) {
		pr = pr && ev->ssid[i] >= 0x20u && ev->ssid[i] < 0x7Fu;
	}
	p += (uint32_t)snprintf(out + p, cap - p, pr ? "\"" : "hex ");
	for (i = 0; i < ev->sl && i < 32u && p + 4u < cap; i++) {
		p += (uint32_t)snprintf(out + p, cap - p, pr ? "%c" : "%02x", pr ? (int)ev->ssid[i] : (int)ev->ssid[i]);
	}
	if (pr && p + 2u < cap) {
		snprintf(out + p, cap - p, "\"");
	}
}

#endif /* SWEET_WLAN_G2R_H */
