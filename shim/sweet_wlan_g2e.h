/* L21: the 7 further response buffers (ids 5..11, slots 1..7) and 7 further event buffers (ids 0x21..0x27, slots 1..7), then ONE read-only watch of ring 2: events are consumed (their re-post waits), anything else is a soft stop */
static int
swg_e_l21(struct swg_wx *x)
{
	struct swg_eio io;
	uint32_t j, w = 0, waited = 0, e = 0, r0 = x->d4rd;
	int ex;

	memset(&io, 0, sizeof io);
	swl_gap("scan", "8 event buffers posted (DHD posts 32)", "low");
	for (j = 1; j < SWG_E_RID_BUFS; j++) {
		ex = swg_e_post_resp(x, SWG_D4D_RID0 + 2u + j, j);
		if (ex != 0) {
			return ex;
		}
	}
	for (j = 1; j < SWG_E_EV_BUFS; j++) {
		ex = swg_e_post_ev(x, j, j);
		if (ex != 0) {
			return ex;
		}
	}
	x->evp = SWG_E_EV_BUFS;
	ex = swg_d4d_wait(x, 0u, &w, &waited);
	if (ex != 0) {
		return ex;
	}
	if (w < x->d4rd) {
		SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
		return 0;
	}
	if (w > x->d4rd) {
		ex = swg_e_items(x, w, &io, NULL, &e);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (e > x->d4rd) {
			ex = swg_d4d_consume(x, x->d4rd, e);
			if (ex != 0) {
				return ex;
			}
		}
	}
	printf("SWEET WLAN: L21 posted 7 respbuf + 7 evbuf, ring-0 WR %u, ring-2 new items %u\n", x->d4wr, x->d4rd - r0);
	return 0;
}

/* the answer bytes of the L22 GET_VAR: 6 B scan_ver, 4 B sup_wpa, 30 B event_msgs_ext */
#define SWG_E_RSPMAX 48u

static int swg_e_l22c(struct swg_wx *x);

static int
swg_e_l22(struct swg_wx *x, int clm)
{
	static const char *const nm[3] = { "scan_ver", "sup_wpa", "event_msgs_ext" };
	uint8_t rsp[SWG_E_RSPMAX], arg[30];
	struct swd4b_ioctl_d2h cm;
	int ex;
	uint32_t mj;
	uint32_t i;

	memset(rsp, 0, sizeof rsp);
	/* scan_ver: GET_VAR, in = out = name + 1 + 6 (wl_scan_version_t: u16 version, u16 length, u16 scan_ver_major) [src WI:18425-18431; length rule BW bwfm.c:1815-1828] */
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR scan_ver", nm[0], NULL, 6u, 9u + 6u, 0, 6u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.resp_len < 6u) {
		SWG_E_STOP(x, "scan_ver answer is %u B, not 6", cm.resp_len);
		return 0;
	}
	mj = swg_e_le16(rsp + 4);       /* review 0174-w-d4e-scan24-l21-hwwrite-r1 finding 3: the whole 16 bits are checked, not narrowed to a byte first (0x0102 is not 2) */
	printf("SWEET WLAN: L22 scan_ver version %u length %u major %u\n", swg_e_le16(rsp), swg_e_le16(rsp + 2), mj);
	if (mj != 2u && mj != 3u) {
		SWG_E_STOP(x, "scan_ver major %u is not 2 or 3, no escan", mj);
		return 0;
	}
	x->emajor = (uint8_t)mj;
	/* sup_wpa: a read-only probe (W-D5 s.4): a non-zero status is recorded */
	memset(rsp, 0, sizeof rsp);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR sup_wpa", nm[1], NULL, 4u, 8u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L22 sup_wpa get: status %d value %d\n", (int)cm.status, cm.resp_len >= 4u ? (int)swd4b_get32(rsp) : 0);
	if (cm.status != 0 || cm.resp_len < 4u) {
		swl_gap("scan", "sup_wpa get status non-zero or answer short", "medium");
	}
	/* event_msgs_ext GET: ver 1, len 26 [src LX:12708-12722 (msglen, bzero, eventmask_msg->ver / ->len = ROUNDUP(WLC_E_LAST 207, 8) / 8 = 26, the dhd_iovar GET); BE:319]: in = out = name 15 + 4 + 26 */
	memset(arg, 0, sizeof arg);
	arg[0] = 1u;
	arg[2] = 26u;
	memset(rsp, 0, sizeof rsp);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR event_msgs_ext", nm[2], arg, 30u, 15u + 30u, 0, 30u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	if (cm.resp_len < 30u || rsp[2] != 26u) {
		SWG_E_STOP(x, "event_msgs_ext answer %u B len %u, expected 30 B len 26", cm.resp_len, rsp[2]);
		return 0;
	}
	printf("SWEET WLAN: L22 event_msgs_ext get: len %u status %d\n", rsp[2], (int)cm.status);
	{
		char line[2u * 26u + 1u];

		for (i = 0; i < 26u; i++) {
			snprintf(line + 2u * i, 3u, "%02x", rsp[4u + i]);
		}
		printf("SWEET WLAN: L22 event_msgs_ext mask (default, ver %u command %u) %s\n", rsp[0], rsp[1], line);
	}
	/* event_msgs_ext SET: command 3 (EVENTMSGS_SET_MASK) with ONLY bit 69 (WLC_E_ESCAN_RESULT) set [src WI:9665-9670 (command enum, SET_MASK 3 at 9669), 9672 (EVENTMSGS_VER 1), 9679-9686 (struct: ver, command, len, maxgetsize, mask at +4), BE:180; setbit: byte 69 / 8 = 8, bit 69 % 8 = 5] */
	memset(arg, 0, sizeof arg);
	arg[0] = 1u;
	arg[1] = 3u;
	arg[2] = 26u;
	arg[4u + SWG_E_EV_ESCAN / 8u] = (uint8_t)(1u << (SWG_E_EV_ESCAN % 8u));
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR event_msgs_ext", nm[2], arg, 30u, 15u + 30u, 0, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L22 event_msgs_ext set: bit 69 on, status %d\n", (int)cm.status);
	if (clm) {      /* L22c (sweetwlan=24): the CLM and the country while the radio is still down, then WLC_UP [src 108 s.9.4: stock preinit order clmload -> country, no UP before them] */
		ex = swg_e_l22c(x);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
	}
	/* WLC_UP: cmd 2, no payload, length 0 [src LX:10622 dhd_wl_ioctl_cmd(dhd, WLC_UP, NULL, 0, TRUE, 0); BW bwfm.c:554 sends an int, the Android driver is the closer reference] */
	ex = swg_e_call(x, SWD4B_WLC_UP, "WLC_UP", NULL, NULL, 0u, 0u, 0, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L22 WLC_UP status %d\n", (int)cm.status);
	return 0;
}

/* L22c (sweetwlan=24, T-0f36; layout and order 108 s.9): the packed CLM blob as clmload chunks, then clmload_status, then country XZ, all inside L22 before its WLC_UP. Each request is SET_VAR/GET_VAR through swg_e_call (soft: a non-zero completion status is judged here).
 * clmload chunk = name "clmload" NUL (8 B) + wl_dload_data { u16 flag = 0x1000 | DL_BEGIN 2 (first) | DL_END 4 (last), u16 dload_type 2 (CLM), u32 len, u32 crc 0, data[len] } with len 1400 for all but the last [src 108 s.9.1: WI:719-750, LC common.c:108-129, BW bwfm.c:3161-3175].
 * Every refusal is a WIFI GAP and a soft stop (SWG_E_STOP): a missing blob, more than SWG_E_CLMMAX chunks, a chunk whose completion status is non-zero (its status comes from its own completion; clmload_status is read only after the last chunk, the glue list has no request after a refused one), clmload_status non-zero or short, country refused. 0 = done or soft stop; negative = stopped */
static int
swg_e_l22c(struct swg_wx *x)
{
	static const uint8_t xz[12] = { 'X', 'Z', 0, 0, 0, 0, 0, 0, 'X', 'Z', 0, 0 };    /* wl_country_t: country_abbrev "XZ", rev 0, ccode "XZ" [src 108 s.9.3: WI:1817-1830, S1 bcm4389-dhd_linux.c:12278-12290; rev 0 is U-CL2] */
	static uint8_t ch[12u + SWG_E_CLMCHUNK];  /* static, not on the kernel stack (review T-4adb-hwwrite-r1 C2): G2 is single-threaded and runs once per boot (sw_hw is static for the same reason) */
	uint8_t rsp[4];
	struct swd4b_ioctl_d2h cm;
	uint32_t n, nch, len, flag, st;
	int ex;

	if (x->in.clm == NULL || x->in.clmlen == 0u) {
		SWG_E_STOP(x, "no CLM blob (absent or failed the glue CRC)");
		return 0;
	}
	if (x->in.clmlen > SWG_E_CLMMAX * SWG_E_CLMCHUNK) {       /* before the division: a length near 2^32 must not wrap nch to 0 */
		SWG_E_STOP(x, "CLM blob %u B is more than %u chunks", x->in.clmlen, SWG_E_CLMMAX);
		return 0;
	}
	nch = (x->in.clmlen + SWG_E_CLMCHUNK - 1u) / SWG_E_CLMCHUNK;
	for (n = 0; n < nch; n++) {
		len = x->in.clmlen - n * SWG_E_CLMCHUNK < SWG_E_CLMCHUNK ? x->in.clmlen - n * SWG_E_CLMCHUNK : SWG_E_CLMCHUNK;
		flag = 0x1000u | (n == 0u ? 0x2u : 0u) | (n == nch - 1u ? 0x4u : 0u);
		memset(ch, 0, sizeof ch);
		ch[0] = (uint8_t)flag;
		ch[1] = (uint8_t)(flag >> 8);
		ch[2] = 2u;                             /* DL_TYPE_CLM */
		swd4b_put32(ch + 4, len);
		memcpy(ch + 12, x->in.clm + n * SWG_E_CLMCHUNK, len);     /* crc (ch + 8) stays 0: DL_CRC_NOT_INUSE is not set by LC or BW */
		ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR clmload", "clmload", ch, 12u + len, 8u + 12u + len, 1, 0u, NULL, 1, &cm, NULL);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		printf("SWEET WLAN: L22c clmload chunk %u of %u: flag %#x len %u, status %d\n", n + 1u, nch, flag, len, (int)cm.status);
		if (cm.status != 0) {
			SWG_E_STOP(x, "clmload chunk %u refused, status %d", n + 1u, (int)cm.status);
			return 0;
		}
	}
	memset(rsp, 0, sizeof rsp);
	ex = swg_e_call(x, SWD4B_WLC_GET_VAR, "WLC_GET_VAR clmload_status", "clmload_status", NULL, 4u, 15u + 4u, 1, 4u, rsp, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	st = cm.resp_len >= 4u ? swd4b_get32(rsp) : 0xFFFFFFFFu;
	printf("SWEET WLAN: L22c clmload_status %u (answer %u B, status %d)\n", st, cm.resp_len, (int)cm.status);
	if (cm.status != 0 || cm.resp_len < 4u || st != 0u) {
		SWG_E_STOP(x, "clmload_status %u (answer %u B, status %d)", st, cm.resp_len, (int)cm.status);
		return 0;
	}
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR country", "country", xz, 12u, 8u + 12u, 1, 0u, NULL, 1, &cm, NULL);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L22c country XZ set: status %d\n", (int)cm.status);
	if (cm.status != 0) {
		SWG_E_STOP(x, "country XZ refused, status %d", (int)cm.status);
	}
	return 0;
}

/* T-0fdd: the 20 MHz chanspecs of the seven bring-up channels 1 6 11 36 40 44 48 of the sweetwlan=24 escan: channel | 0x1000 (20 MHz) | band (2.4 GHz 0, 5 GHz 0xc000) [src refs/wlan-stage5/bcmwifi_channels.h.new:141,154,185; obs boot 1 of T-35bd records 0x1001 0x1006 0x100b]; stock wl_do_escan stores channel_num = nchan | nssid << 16 at params +68 and the list at params +72 with `length` 72 unchanged [obs golden-wifi-trace-h1b.md s.3]; the list (14 B) is zero padded to 16 B [src BW bwfm.c:2201-2240] */
#define SWG_E_NCS 7u
static const uint16_t swg_e_cs[SWG_E_NCS] = { 0x1001u, 0x1006u, 0x100Bu, 0xD024u, 0xD028u, 0xD02Cu, 0xD030u };

/* patch 0292 (WIFI-COVERAGE D3/D4): the 96 B (or, with chans == 0, 80 B) argument of the passive escan START, built in ONE place for the boot scan (swg_e_l23) and the keep-phase scan (swl25_sc_step), so the two cannot drift.
 * Layout (wl_escan_params_v2/v3, see the comment of swg_e_l23): version u32 = emajor, action u16 = 1 (WL_ESCAN_ACTION_START), sync_id u16 = sync (SWG_E_SYNC for the boot scan, one per keep scan, review 0292 F2), params: version u16 = emajor, length u16 = 72, ssid_len 0, bssid ff x6, bss_type 2 (ANY),
 * scan_type 1 (passive), nprobes / active / passive / home all -1 (0xff x16: the firmware's default home time, as the boot scan), channel_num = 7 and the seven chanspecs of swg_e_cs at +80 when chans != 0. All 96 bytes are written here. */
static void
swg_e_escan_arg(uint8_t arg[96], uint32_t emajor, int chans, uint32_t sync)
{
	uint32_t i;

	memset(arg, 0, 96u);
	swd4b_put32(arg, emajor);
	arg[4] = 1u;
	arg[6] = (uint8_t)sync;
	arg[7] = (uint8_t)(sync >> 8);
	arg[8] = (uint8_t)emajor;
	arg[10] = 72u;
	memset(arg + 48, 0xFF, 6);
	arg[54] = 2u;
	arg[56] = 1u;
	memset(arg + 60, 0xFF, 16);
	if (chans) {
		arg[76] = (uint8_t)SWG_E_NCS;      /* channel_num = 7 (nssid 0 in the upper half), params +68 */
		for (i = 0; i < SWG_E_NCS; i++) {
			arg[80u + 2u * i] = (uint8_t)swg_e_cs[i];
			arg[81u + 2u * i] = (uint8_t)(swg_e_cs[i] >> 8);
		}
	}
}

/* L23: the passive escan START (version 2 or 3 as scan_ver said, action 1, sync 0x1234, all channels: channel_num 0; at lv >= 24 (T-0fdd) the 7 channels of swg_e_cs: channel_num 7, list at arg + 80, payload 6 + 96 B, `length` still 72) and a poll of ring 2 for its events, at most SWG_E_BUDGET_MS.
 * wl_escan_params_v2/v3 (80 B, offsets measured on a host compile of WI:915-960, 1089-1094: version u32 0, action u16 4, sync_id u16 6, params at 8: version u16 +0, length u16 +2, ssid {u32 len, 32 B} +4, bssid +40, bss_type +46, pad / ssid_type +47, scan_type +48,
 * nprobes +52, active +56, passive +60, home +64, channel_num +68) [src BW bwfm.c:2196-2262: length = 72 = sizeof the wl_scan_params without the channel list [obs: the stock bcm4389 wl_do_escan stores `length` 0x48 at params+2, docs/research/golden-wifi-trace-h1b.md s.3 (wl_do_escan 0xd8104, w28 = 0x48); WI:919-921 "without implicit pad"; the 80 of the first cut was BW bwfm.c:2217-2218 `length = params_size`, the whole escan struct, and boot 5 (T-af77) got completion status -26 = BCME_EPERM (bcmerror.h:85) for it; the total payload length stays 80 below level 24, 96 with the channel list from level 24 (T-0fdd)], bssid ff x6, bss_type 2 (DOT11_BSSTYPE_ANY, bwfmreg.h:519), scan_type 1 (BWFM_SCANTYPE_PASSIVE, WD:239), nprobes / times -1] */
static int
swg_e_l23(struct swg_wx *x, unsigned lv)
{
	uint8_t arg[96], rsp[4] = { 0 };
	struct swd4b_ioctl_d2h cm;
	struct swg_ew sc;
	struct swg_eio io;
	uint32_t i, spent = 0, polls, w = 0, waited = 0, e = 0, nch = 0;
	const uint32_t argn = lv >= 24u ? 96u : 80u;      /* T-0fdd: sweetwlan=24 scans 7 channels (list at arg + 80), lower levels all channels */
	int ex;

	memset(&sc, 0, sizeof sc);
	memset(&io, 0, sizeof io);
	memset(&swg_rec, 0, sizeof swg_rec);       /* T-08fd: the replay record of this scan starts empty */
	x->escan_sync = (uint16_t)SWG_E_SYNC;       /* review 0292 F2: the judge accepts exactly this id */
	swg_e_escan_arg(arg, x->emajor, lv >= 24u, SWG_E_SYNC);       /* patch 0292: one builder for the boot and the keep-phase scan */
	if (lv >= 24u) {
		swl_gap("scan", "escan limited to 7 bring-up channels", "low");
	}
	ex = swg_e_call(x, SWD4B_WLC_SET_VAR, "WLC_SET_VAR escan", "escan", arg, argn, 6u + argn, 0, 0u, rsp, 0, &cm, &sc);
	if (ex != 0 || x->d4dstop) {
		return ex;
	}
	printf("SWEET WLAN: L23 escan start: version %u action 1 sync %#x passive %s, status %d\n", (unsigned)x->emajor, SWG_E_SYNC, lv >= 24u ? "7 bring-up channels (1 6 11 36 40 44 48)" : "all channels", (int)cm.status);
	while (sc.done == 0 && spent < SWG_E_BUDGET_MS) {
		polls = (SWG_E_BUDGET_MS - spent) / SWG_POLL_MS;
		ex = swg_d4d_waitn(x, 1u, polls, &w, &waited);
		if (ex != 0) {
			return ex;
		}
		spent += waited + SWG_POLL_MS;
		if (w < x->d4rd) {
			SWG_E_STOP(x, "d2h write index %u went below the read count %u", w, x->d4rd);
			return 0;
		}
		if (w == x->d4rd) {
			break;
		}
		ex = swg_e_items(x, w, &io, &sc, &e);
		if (ex != 0 || x->d4dstop) {
			return ex;
		}
		if (e > x->d4rd) {
			ex = swg_d4d_consume(x, x->d4rd, e);
			if (ex != 0) {
				return ex;
			}
			ex = swg_e_evflush(x);
			if (ex != 0) {
				return ex;
			}
		}
	}
	for (i = 0; i < 8u; i++) {
		uint32_t m = x->nchan[i];

		for (; m != 0u; m &= m - 1u) {
			nch++;
		}
	}
	if (sc.done == 1) {
		printf("SWEET WLAN: L23 scan done status 0 bss %u channels %u\n", x->nbss, nch);
		swg_d4d_done(x, 23u, lv, x->d4wr, x->d4rd);
	} else if (sc.done == 2) {
		printf("SWEET WLAN: L23 scan ended by another status after %u PARTIAL results, %u bss\n", sc.nres, x->nbss);
	} else if (sc.done == 0) {
		printf("SWEET WLAN: L23 scan not done after %u ms: %u PARTIAL results, %u bss\n", spent, sc.nres, x->nbss);
		swl_gap("scan", sc.nres == 0u ? "no ESCAN_RESULT in 8000 ms" : "no SUCCESS in 8000 ms", "high");
	}
	return 0;
}
