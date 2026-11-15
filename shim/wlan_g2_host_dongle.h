/* Part of shim/wlan_g2_host_test.c (included in place, moved out for R29 by T-4adb): the generic fake dongle gd_* and the D4D fake d4d_db. Not a standalone header. */
/* ---- 22b: the generic fake dongle (gd_*): from the doorbell FF000002 on it reads what the host posted on ring 0 (host memory) and answers on ring 2, so a DS round, an ioctl and an event can come at ANY ring-0 / ring-2 index. It replaces d4d_db and the W-D4c doorbell branch while gd.on.
 * Script: gd.inj[k] = { after the n-th ring-0 doorbell (n counts from FF000002), put a type-0x24 item with this mailbox data on ring 2 }; gd.nonote = the n-th wake assert (0x40) gets no exit note; gd.reinj = after each DS deassert (0x20) put another DS_ENTER_REQ, that many times; gd.straggle = the n-th wake assert gets one more DS_ENTER_REQ right behind its exit note. ---- */
static struct {
	int on;
	uint32_t h2dr, w, ndb;          /* ring-0 items consumed by the dongle; ring-2 items written (absolute); ring-0 doorbells seen */
	uint32_t rq[32], rqn, rqh;      /* posted response buffers (id), FIFO */
	uint64_t rqpa[32];
	struct { uint32_t n, mb; } inj[8];
	unsigned ninj;
	uint32_t nonote, nwake, reinj, straggle;      /* straggle = the n-th wake assert gets, behind its exit note, one more DS_ENTER_REQ (the firmware ignores the DS hold) */
	uint32_t nreq, nrsp;            /* ioctl requests answered / response buffers consumed */
} gd;

/* ---- W-D4e: the scan24 part of the fake dongle: event buffers posted on ring 0 (type 0x0D, FIFO), the answers of scan_ver / sup_wpa / event_msgs_ext / WLC_UP / escan, the events of a scan, and one fault at a time (ge.fault) ---- */
enum { EF_NONE, EF_PKTID, EF_ETYPE, EF_DLEN, EF_EDLEN, EF_EPOCH, EF_STATUS, EF_ACKSTATUS, EF_CMD, EF_ACKRID, EF_DUPACK, EF_TRANS, EF_CRID, EF_BIGLEN, EF_DUPCPL, EF_TYPE, EF_NOANS, EF_NOCPL, EF_LOWW, EF_SCANVER_SHORT,
       EF_MAJOR, EF_MAJOR1, EF_MAJOR4, EF_MAJORHI, EF_MAJOR0, EF_EMS_LEN, EF_EMS_SHORT, EF_CAP, EF_BSSVER, EF_STATUS4, EF_SYNC, EF_ACK_NOREQ, EF_FLIP, EF_DLENMAX, EF_CRIDUSED, EF_ESCAN26, EF_STALEID, EF_ID0, EF_N };
static struct {
	uint32_t evid[96];
	uint64_t evpa[96];
	unsigned evn, evh, lost;
	uint32_t major, supwpa, rxoff;
	int supst;
	uint8_t ems[30], esc[96], emsget[30];
	uint32_t escin;                 /* T-0fdd: the request length (payload bytes: name + NUL + arg) of the last escan */
	unsigned up, nescan, npartial, nreq0;
	struct { uint32_t n, type, status; int pre; } einj[8];
	uint32_t reclen, bssn;          /* the bss record bytes the PARTIAL results carry (0 = 96) and their bss_count (0 = 1) */
	int noctl, hexssid, sl33, ssid2, ssid3, sdlen11, bssn0;
	uint32_t rxbufs;                /* patch 0357: max_host_rxbufs, the u16 at shared +0x22 (0 = the word is 0, as before) */
	uint32_t rxtcm;                 /* dma_rxoffset as the TCM word holds it, when it is not the offset the frames are written at (0 = ge.rxoff) */
	unsigned fb;                    /* EF_FLIP: the frame byte whose low bit is flipped */
	unsigned neinj;
	uint32_t clmref;                /* T-0f36 L22c: the n-th clmload chunk (1-based) is refused with completion status -23, 0 = none */
	uint32_t clmst;                 /* the clmload_status answer */
	int clmstshort, ctryref;        /* clmload_status answers 2 B / country completes with status -23 */
	int clmstbad;                   /* clmload_status completes with status -23 and a 4 B zero answer */
	uint32_t clmstin, ctryin;       /* the input_buf_len of the clmload_status / country request, as sent */
	unsigned nclm, nclmst, nctry, clmoff;
	uint32_t cflag[16], ctype[16], clen[16], ccrc[16];
	uint8_t clm[20000], ctry[12];
	int fault;
	int crid_bad;                   /* T-bd94: with EF_STATUS the completion also carries a request_id that is not a posted buffer */
	unsigned badpost, lastid;       /* T-fad2: event-buffer posts whose address is outside the event block, not 8 KiB aligned, or still live (posted twice); the pktid of the previous event */
	int evlifo;                     /* T-fad2: the dongle delivers each event into the NEWEST posted unconsumed event buffer, not the oldest (boot 8 of T-fad2: pktid 0x27 first when 1, 0x21..0x27 were posted) */
	int lifo;                       /* T-af77: the dongle answers with the NEWEST posted response buffer (boot 3 of T-382c: id 11 when 4..11 were posted), not the oldest */
	uint32_t fault_n;               /* the fault applies to the n-th ioctl request of the run (gd.nreq counts L18 = 1, L20 = 2, scan_ver = 3 ...), or the n-th event delivered */
	unsigned nev;                   /* events delivered */
	unsigned pp, pi;                /* scan events still to deliver: PARTIAL results left, the next bss index */
	int seeded;                     /* the L14 event buffer (id 1, slot 0) is in the FIFO: the fake starts at the doorbell FF000002 and never saw it */
	int pfin;                       /* the final ESCAN_RESULT status to deliver after them, -1 = none */
	uint32_t eflags;                /* the wl_event_msg flags of the next event frame (LINK up = 1) */
	int jmode;                      /* L24: events after the join ioctl: 0 = SET_SSID status 0 + LINK up, 1 = SET_SSID status jst, 2 = none, 3 = SET_SSID 0 + LINK not up, 4 = LINK up then SET_SSID 0, 5 = LINK up alone, 6 = SET_SSID 0 + LINK with flags 2 (bit 0 clear) */
	uint32_t emslen;                /* L24: the length word of the last event_msgs_ext SET item (T-4e39) */
	unsigned jlow;                  /* L24: at this host delay (after the join ioctl) the D2H-WR slot 0 is set far below the read count (0 = never) */
	uint32_t jst;                   /* the SET_SSID event status of jmode 1 */
	unsigned qlate[2];              /* L24 W-A: after the quiet wait started (the first "L24 hb poll" line), at these host delays a LINK event arrives (0 = never): the quiet time restarts */
	unsigned jlate;                 /* delivery of the join events waits for this many host delays (0 = inside the ioctl) */
	unsigned jn;                    /* L24: join-class SET_VARs seen (sup_wpa, wpa_auth, wsec, auth, mfp, join, wsec_key) */
	char jname[8][16];              /* their names in order */
	uint8_t jbuf[8][4u * SWG_E_PAYW];       /* their payloads as the dongle read them from the ioctl buffer (name NUL value) */
	char jfail[16];                 /* the SET_VAR with this name completes with status jfst */
	int jfst;
	/* 2026-11-11 scan records: rich.on = the PARTIAL results carry a real-shaped 132 B wl_bss_info_v109 + the IE block (ieoff 0 = 132; rlen 0 = ieoff + ielen; fld = the ie_length field, 0 = ielen) */
	struct { int on; uint8_t ie[1100]; uint32_t ielen, ieoff, rlen, fld, chsp, nrates, beacon; uint8_t rates[16]; int noise, snr; } rich;
} ge;

static void gd_put(uint32_t type, uint32_t rid, int status, uint32_t w3, uint32_t w4);
static void ge_join_events(void);
static void ge_escan(uint32_t status, unsigned i);
static void ge_seed(void);

/* deliver the scan events that are due: as long as a posted event buffer is free */
static void
ge_pump(void)
{
	ge_seed();
	while ((ge.pp > 0u || ge.pfin >= 0) && ge.evh < ge.evn) {
		if (ge.pp > 0u) {
			ge.pp--;
			ge_escan(8u, ge.pi++ % 8u);
		} else {
			ge_escan((uint32_t)ge.pfin, 0u);
			ge.pfin = -1;
		}
	}
}

static void
ge_seed(void)
{
	if (!ge.seeded) {
		ge.seeded = 1;
		ge.evid[0] = 1u;
		ge.evpa[0] = PA + fplan->b[SWD_EVBUF].off;
		ge.evn = 1u;
	}
}

/* a BCM event frame into the next posted event buffer, then the ring-2 item; datalen bytes of data (NULL = zeros) */
static void
ge_event(uint32_t type, uint32_t status, const uint8_t *data, uint32_t datalen)
{
	uint32_t id, dlen = 72u + datalen;
	uint8_t *fr;

	ge_seed();
	if (ge.evh >= ge.evn) {
		ge.lost++;
		return;
	}
	if (ge.evlifo && ge.evn - 1u > ge.evh) {       /* rotate the newest unconsumed buffer to the head, the others keep their order */
		const uint32_t ti = ge.evid[ge.evn - 1u];
		const uint64_t tp = ge.evpa[ge.evn - 1u];
		unsigned k;

		for (k = ge.evn - 1u; k > ge.evh; k--) {
			ge.evid[k] = ge.evid[k - 1u];
			ge.evpa[k] = ge.evpa[k - 1u];
		}
		ge.evid[ge.evh] = ti;
		ge.evpa[ge.evh] = tp;
	}
	id = ge.evid[ge.evh];
	fr = (uint8_t *)&f.hb[(ge.evpa[ge.evh] - PA) / 4u] + ge.rxoff;
	ge.evh++;
	ge.nev++;
	memset(fr, 0, dlen);
	fr[12] = ge.fault == EF_ETYPE && ge.nev == ge.fault_n ? 0x08u : 0x88u;       /* ge.nev is already this event's number */
	fr[13] = 0x6Cu;
	fr[14] = 0x80u;
	fr[15] = 0x01u;
	fr[19] = 0x00u;
	fr[20] = 0x10u;
	fr[21] = 0x18u;
	fr[23] = 1u;
	fr[25] = 2u;
	fr[26] = (uint8_t)(ge.eflags >> 8); fr[27] = (uint8_t)ge.eflags;
	fr[28] = (uint8_t)(type >> 24); fr[29] = (uint8_t)(type >> 16); fr[30] = (uint8_t)(type >> 8); fr[31] = (uint8_t)type;
	fr[32] = (uint8_t)(status >> 24); fr[33] = (uint8_t)(status >> 16); fr[34] = (uint8_t)(status >> 8); fr[35] = (uint8_t)status;
	datalen += ge.fault == EF_EDLEN && ge.nev == ge.fault_n ? 4000u : 0u;
	fr[44] = (uint8_t)(datalen >> 24); fr[45] = (uint8_t)(datalen >> 16); fr[46] = (uint8_t)(datalen >> 8); fr[47] = (uint8_t)datalen;
	datalen -= ge.fault == EF_EDLEN && ge.nev == ge.fault_n ? 4000u : 0u;
	if (data != NULL) {
		memcpy(fr + 72, data, datalen);
	}
	if (ge.fault == EF_DLEN && ge.nev == ge.fault_n) {
		dlen = 40u;
	}
	if (ge.fault == EF_DLENMAX && ge.nev == ge.fault_n) {
		dlen = 8192u;
	}
	if (ge.fault == EF_FLIP && ge.nev == ge.fault_n) {
		fr[ge.fb] ^= 1u;
	}
	if (ge.fault == EF_PKTID && ge.nev == ge.fault_n) {
		id += 1u;
	}
	if (ge.fault == EF_STALEID && ge.nev == ge.fault_n) {       /* the pktid of the previous event again: its buffer is consumed and not re-posted yet */
		id = ge.lastid;
	}
	if (ge.fault == EF_ID0 && ge.nev == ge.fault_n) {           /* DHD_PKTID_INVALID */
		id = 0u;
	}
	if (ge.fault != EF_STALEID || ge.nev != ge.fault_n) {
		ge.lastid = ge.evid[ge.evh - 1u];
	}
	gd_put(0xEu, id, 0, dlen | (ge.nev & 0xFFFFu) << 16, 0u);
}

/* one ESCAN_RESULT event: status 8 carries one wl_bss_info_v109 (the offsets of WI:355-381, measured), status 0 none */
static void
ge_escan(uint32_t status, unsigned i)
{
	uint8_t d[12 + 128];
	const uint32_t rl = ge.reclen != 0u ? ge.reclen : 96u;
	static const char ssid[] = "net0";
	static const uint8_t chs[8] = { 1, 6, 36, 4, 132, 20, 149, 250 };       /* the control channels of the bss records; 4 / 132 and 4 / 20 collide in the wrong bitmap layouts, 250 is in the last bitmap word */
	uint32_t sync = ge.fault == EF_SYNC ? 0x4321u : ge.nescan != 0u && ge.esc[6] != 0xEEu ? (uint32_t)ge.esc[6] | (uint32_t)ge.esc[7] << 8 : 0x1234u;       /* review 0292 F2: the events carry the sync_id of the request the dongle got (0x1234 before any) */

	memset(d, 0, sizeof d);
	d[4] = 109u;
	d[8] = (uint8_t)sync;
	d[9] = (uint8_t)(sync >> 8);
	if (status == 8u) {
		uint8_t *r = d + 12;

		d[10] = ge.bssn0 ? 0u : (uint8_t)(ge.bssn != 0u ? ge.bssn : 1u);
		r[0] = ge.fault == EF_BSSVER ? 110u : 109u;
		r[4] = 96u;
		r[8] = 0x02u; r[9] = 0x11u; r[10] = 0x22u; r[11] = 0x33u; r[12] = 0x44u; r[13] = (uint8_t)(0x50u + i);
		r[18] = 4u;
		memcpy(r + 19, ssid, 4);
		r[19 + 3] = (uint8_t)('0' + i);
		if (ge.hexssid) {
			r[19] = 0x01u; r[20] = 0x02u; r[21] = 'A'; r[22] = 'B';
		}
		if (ge.ssid2) {
			r[18] = 2u; r[19] = ' '; r[20] = 'a';
		}
		if (ge.ssid3) {
			r[18] = 1u; r[19] = 0x7Fu;
		}
		if (ge.sl33) {
			r[18] = 33u;
			memset(r + 19, 'a', 33);
		}
		r[72] = (uint8_t)(chs[i % 8u] + 2u);      /* chanspec low byte = the center channel, 2 above the control channel */
		r[73] = 0x10u;
		r[78] = (uint8_t)(0x100u - 40u - i); /* RSSI int16 -40 - i */
		r[79] = 0xFFu;
		r[88] = ge.noctl ? 0u : chs[i % 8u];
	}
	if (status == 8u && ge.rich.on) {
		static uint8_t rd[12 + 132 + 1100 + 8];
		uint8_t *r = rd + 12;
		const uint32_t ieoff = ge.rich.ieoff != 0u ? ge.rich.ieoff : 132u, total = ieoff + ge.rich.ielen, rlf = ge.rich.rlen != 0u ? ge.rich.rlen : total, il = ge.rich.fld != 0u ? ge.rich.fld : ge.rich.ielen;
		uint32_t k;

		memset(rd, 0, sizeof rd);
		memcpy(rd, d, 12);
		r[0] = 109u; r[4] = (uint8_t)rlf; r[5] = (uint8_t)(rlf >> 8);
		memcpy(r + 8, d + 12 + 8, 11);       /* BSSID, beacon, capability, SSID_len, SSID of the generic record */
		memcpy(r + 18, d + 12 + 18, 1u + 32u);
		r[14] = (uint8_t)ge.rich.beacon; r[15] = (uint8_t)(ge.rich.beacon >> 8);
		r[16] = 0x11u; r[17] = 0x04u;
		r[52] = (uint8_t)ge.rich.nrates;
		for (k = 0; k < 16u; k++) {
			r[56 + k] = ge.rich.rates[k];
		}
		r[72] = (uint8_t)ge.rich.chsp; r[73] = (uint8_t)(ge.rich.chsp >> 8);
		r[78] = d[12 + 78]; r[79] = d[12 + 79];
		r[80] = (uint8_t)(int8_t)ge.rich.noise;
		r[88] = d[12 + 88];
		r[116] = (uint8_t)ieoff; r[117] = (uint8_t)(ieoff >> 8);
		r[120] = (uint8_t)il; r[121] = (uint8_t)(il >> 8);
		r[124] = (uint8_t)(int16_t)ge.rich.snr; r[125] = (uint8_t)((uint16_t)(int16_t)ge.rich.snr >> 8);
		memcpy(r + ieoff, ge.rich.ie, ge.rich.ielen);
		ge_event(69u, status, rd, 12u + total);
		return;
	}
	ge_event(69u, status, d, status == 8u ? 12u + rl : ge.sdlen11 ? 11u : 12u);
}

static void
gd_put(uint32_t type, uint32_t rid, int status, uint32_t w3, uint32_t w4)
{
	uint32_t k = gd.w, *r = &f.hb[fplan->b[SWD_D2H_CTRL].off / 4u + 6u * (k % 64u)];

	r[0] = ((254u + k + (ge.fault == EF_EPOCH && k == ge.fault_n ? 1u : 0u)) % 253u) << 24 | 0x80u << 16 | type;
	r[1] = rid;
	r[2] = (uint32_t)(uint16_t)status | 2u << 16;
	r[3] = w3;
	r[4] = w4;
	r[5] = r[0] ^ r[1] ^ r[2] ^ r[3] ^ r[4];
	gd.w++;
	f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = gd.w % 64u;
}

/* the join events of ge.jmode (L24); SET_SSID is event 0, LINK event 16, flags bit 0 = link up */
static void
ge_join_events(void)
{
	if (ge.jmode == 2) {
		return;
	}
	if (ge.jmode == 5) {
		ge.eflags = 1u;
		ge_event(16u, 0u, NULL, 0u);
		ge.eflags = 0u;
		return;
	}
	if (ge.jmode == 4) {
		ge.eflags = 1u;
		ge_event(16u, 0u, NULL, 0u);
		ge.eflags = 0u;
		ge_event(0u, 0u, NULL, 0u);
		return;
	}
	ge_event(0u, ge.jmode == 1 ? ge.jst : 0u, NULL, 0u);
	if (ge.jmode != 1) {
		ge.eflags = ge.jmode == 3 ? 0u : ge.jmode == 6 ? 2u : 1u;
		ge_event(16u, 0u, NULL, 0u);
		ge.eflags = 0u;
	}
}

static void
gd_ioctl(const uint32_t *it)
{
	const uint32_t cmd = it[2], trans = it[3] & 0xFFFFu;
	const uint64_t ppa = (uint64_t)it[6] | (uint64_t)it[7] << 32;
	const char *name = ppa >= PA ? (const char *)&f.hb[(ppa - PA) / 4u] : "";
	uint32_t id = 0, len = 0;
	uint8_t *rsp = NULL;
	int st = 0, ast = 0;
	const int fn = ge.fault_n == gd.nreq + 1u;      /* the fault is for THIS request */

	if (gd.rqn > gd.rqh && ge.lifo) {
		gd.rqn--;
		id = gd.rq[gd.rqn % 32u];
		rsp = (uint8_t *)&f.hb[(gd.rqpa[gd.rqn % 32u] - PA) / 4u];
	} else if (gd.rqn > gd.rqh) {
		id = gd.rq[gd.rqh % 32u];
		rsp = (uint8_t *)&f.hb[(gd.rqpa[gd.rqh % 32u] - PA) / 4u];
		gd.rqh++;
	}
	gd.nreq++;
	if (cmd == 1u) {
		memcpy(rsp, &io_ver, 4);
		len = 4u;
	} else if (cmd == 262u && strcmp(name, "cap") == 0) {
		len = (uint32_t)strlen(io_cap) + 1u;
		memcpy(rsp, io_cap, len);
	} else if (cmd == 262u && strcmp(name, "scan_ver") == 0) {
		memset(rsp, 0, 8);
		rsp[0] = 1u; rsp[2] = 6u; rsp[4] = (uint8_t)(ge.fault == EF_MAJOR ? 5u : ge.fault == EF_MAJOR1 ? 1u : ge.fault == EF_MAJOR4 ? 4u : ge.fault == EF_MAJOR0 ? 0u : ge.major);
		if (ge.fault == EF_MAJORHI) { rsp[4] = 2u; rsp[5] = 1u; }       /* major 0x0102: the low byte is 2 (review l21 finding 3) */
		len = ge.fault == EF_SCANVER_SHORT ? 4u : 6u;
	} else if (cmd == 262u && strcmp(name, "counters") == 0) {       /* T-924f: the L25 counters GET: wl_cnt_info_t v30 + a 4 B slice tuple (8 B with its header) + the 864 B WLC tuple, txframe 7 at its offset 0 */
		memset(rsp, 0, 880);
		rsp[0] = 30u; rsp[2] = (uint8_t)(876u & 0xFFu); rsp[3] = (uint8_t)(876u >> 8);
		rsp[4] = 1u; rsp[6] = 4u;
		rsp[12] = 0x00u; rsp[13] = 0x01u; rsp[14] = (uint8_t)(864u & 0xFFu); rsp[15] = (uint8_t)(864u >> 8);
		rsp[16] = 7u;
		len = 880u;
	} else if (cmd == 262u && strcmp(name, "sup_wpa") == 0) {
		memcpy(rsp, &ge.supwpa, 4);
		len = 4u;
		st = ge.supst;
	} else if (cmd == 262u && strcmp(name, "event_msgs_ext") == 0) {
		unsigned i;

		memset(rsp, 0, 32);
		rsp[0] = 1u; rsp[2] = ge.fault == EF_EMS_LEN ? 25u : 26u;
		for (i = 0; i < 26u; i++) {
			rsp[4u + i] = (uint8_t)(0x10u + i);
		}
		memcpy(ge.emsget, name + 15, 30);
		len = ge.fault == EF_EMS_SHORT ? 20u : 30u;
	} else if (cmd == 263u && strcmp(name, "event_msgs_ext") == 0) {
		memcpy(ge.ems, name + 15, 30);
		ge.emslen = it[4];
	} else if (cmd == 263u && strcmp(name, "clmload") == 0) {
		const unsigned n = ge.nclm++;
		uint32_t dl;

		memcpy(&dl, name + 8 + 4, 4);
		if (n < 16u) {
			ge.cflag[n] = (uint32_t)(uint8_t)name[8] | ((uint32_t)(uint8_t)name[9] << 8);
			ge.ctype[n] = (uint32_t)(uint8_t)name[10] | ((uint32_t)(uint8_t)name[11] << 8);
			ge.clen[n] = dl;
			memcpy(&ge.ccrc[n], name + 8 + 8, 4);
		}
		if (dl <= 1400u && ge.clmoff + dl <= sizeof ge.clm) {
			memcpy(ge.clm + ge.clmoff, name + 8 + 12, dl);
			ge.clmoff += dl;
		}
		st = ge.clmref == n + 1u ? -23 : 0;
	} else if (cmd == 262u && strcmp(name, "clmload_status") == 0) {
		ge.nclmst++;
		ge.clmstin = it[3] >> 16;
		memcpy(rsp, &ge.clmst, 4);
		len = ge.clmstshort ? 2u : 4u;
		st = ge.clmstbad ? -23 : 0;
	} else if (cmd == 263u && strcmp(name, "country") == 0) {
		memcpy(ge.ctry, name + 8, 12);
		ge.nctry++;
		ge.ctryin = it[3] >> 16;
		st = ge.ctryref ? -23 : 0;
	} else if (cmd == 2u) {
		ge.up++;
	} else if (cmd == 263u && strcmp(name, "escan") == 0) {
		memset(ge.esc, 0xEE, sizeof ge.esc);      /* T-0fdd: bytes past the request length stay 0xEE: a request length shorter than the compared 96 B shows (a stray store past it belongs to the store-stream checks) */
		ge.escin = it[3] >> 16;
		memcpy(ge.esc, name + 6, ge.escin > 6u && ge.escin - 6u <= sizeof ge.esc ? ge.escin - 6u : 0u);
		ge.nescan++;
		if (ge.fault == EF_ESCAN26) {   /* T-bd94: boot 6 of T-a0aa: status -26 (BCME_EPERM) with 86 response bytes (the request length) */
			unsigned i;

			for (i = 0; i < 86u; i++) {
				rsp[i] = (uint8_t)(0xA0u + i);
			}
			len = 86u;
			st = -26;
		}
	} else if (cmd == 263u && (strcmp(name, "sup_wpa") == 0 || strcmp(name, "wpa_auth") == 0 || strcmp(name, "wsec") == 0 || strcmp(name, "auth") == 0 || strcmp(name, "mfp") == 0 || strcmp(name, "join") == 0 || strcmp(name, "wsec_key") == 0 || strcmp(name, "cur_etheraddr") == 0)) {
		if (ge.jn < 8u) {
			snprintf(ge.jname[ge.jn], sizeof ge.jname[0], "%s", name);
			memcpy(ge.jbuf[ge.jn], name, sizeof ge.jbuf[0]);
		}
		ge.jn++;
		if (strcmp(name, ge.jfail) == 0) {
			st = ge.jfst;
		}
	}
	if (ge.fault == EF_STATUS && fn) {
		st = -5;
	}
	if (ge.fault == EF_ACKSTATUS && fn) {
		ast = -1;
	}
	if (ge.fault == EF_NOANS && fn) {
		return;
	}
	if (ge.fault == EF_ACK_NOREQ && fn) {
		gd_put(0xAu, 0xFFFEu, 0, cmd, 0u);
		gd_put(0xAu, 0xFFFEu, 0, cmd, 0u);
		return;
	}
	gd_put(0xAu, ge.fault == EF_ACKRID && fn ? 0xFFFDu : 0xFFFEu, ast, ge.fault == EF_CMD && fn ? cmd + 1u : cmd, 0u);
	if (ge.fault == EF_DUPACK && fn) {
		gd_put(0xAu, 0xFFFEu, 0, cmd, 0u);
	}
	if (ge.fault == EF_NOCPL && fn) {
		return;
	}
	if (ge.fault == EF_LOWW && fn) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;
		return;
	}
	if (ge.fault == EF_TYPE && fn) {
		gd_put(0x10u, 0u, 0, 0u, 0u);
		return;
	}
	gd_put(0xCu, (ge.fault == EF_CRID || (ge.fault == EF_STATUS && ge.crid_bad)) && fn ? id + 100u : ge.fault == EF_CRIDUSED && fn ? 3u : id, st, (ge.fault == EF_BIGLEN && fn ? 8193u : len) | (ge.fault == EF_TRANS && fn ? trans + 1u : trans) << 16, cmd);
	if (ge.fault == EF_DUPCPL && fn) {
		gd_put(0xCu, id, st, len | trans << 16, cmd);
	}
	if (cmd == 263u && strcmp(name, "join") == 0 && st == 0) {
		if (ge.jlate == 0u) {
			ge_join_events();
		}
	}
	if (cmd == 263u && strcmp(name, "escan") == 0) {
		ge.pp = ge.fault == EF_CAP ? 70u : ge.npartial;
		ge.pi = 0u;
		ge.pfin = ge.fault == EF_STATUS4 ? 4 : ge.pfin == -2 ? -1 : 0;
		ge_pump();
	}
}

static void
gd_ring0(uint32_t wr)
{
	while (gd.h2dr < wr) {
		const uint32_t *it = &f.hb[fplan->b[SWD_H2D_CTRL].off / 4u + 10u * gd.h2dr];
		const uint32_t ty = it[0] & 0xFFu;

		if (ty == 0x0Bu && gd.rqn - gd.rqh < 32u) {
			gd.rq[gd.rqn % 32u] = it[1];
			gd.rqpa[gd.rqn % 32u] = (uint64_t)it[4] | (uint64_t)it[5] << 32;
			gd.rqn++;
		} else if (ty == 0x0Du && (ge_seed(), ge.evn < 96u)) {
			const uint64_t bpa = (uint64_t)it[4] | (uint64_t)it[5] << 32, b0 = PA + fplan->b[SWD_EVBUF].off;
			unsigned k;

			if (bpa < b0 || bpa >= b0 + 8u * SWD_EVBUF_LEN || (bpa - b0) % SWD_EVBUF_LEN != 0u) {
				ge.badpost++;
			}
			for (k = ge.evh; k < ge.evn; k++) {
				if (ge.evpa[k] == bpa) {
					ge.badpost++;       /* the same buffer posted while it is still posted */
				}
			}
			ge.evid[ge.evn] = it[1];
			ge.evpa[ge.evn] = (uint64_t)it[4] | (uint64_t)it[5] << 32;
			ge.evn++;
			ge_pump();
		} else if (ty == 0x09u) {
			gd_ioctl(it);
		} else if (ty == 0x23u && it[2] == 0x40u) {
			gd.nwake++;
			if (gd.nwake != gd.nonote) {
				gd_put(0x24u, 0u, 0, 0x4u, 0u);
				if (gd.nwake == gd.straggle) {
					gd_put(0x24u, 0u, 0, 0x2u, 0u);
				}
			}
		} else if (ty == 0x23u && it[2] == 0x20u && gd.reinj > 0u) {
			gd.reinj--;
			gd_put(0x24u, 0u, 0, 0x2u, 0u);
		}
		gd.h2dr++;
		f.hb[fplan->b[SWD_IDX_H2D_R].off / 4u] = gd.h2dr;
	}
}

/* an injected item: type 0xA = an ioctl ack nobody asked for, any other = an event of that type */
static void
ge_inj(unsigned i)
{
	if (ge.einj[i].type == 0xAu) {
		gd_put(0xAu, 0xFFFEu, 0, 1u, 0u);
	} else if (ge.einj[i].type == 0xFFu) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;       /* the D2H-WR slot far behind the read count */
	} else {
		ge_event(ge.einj[i].type, ge.einj[i].status, NULL, 0u);
	}
}

/* a doorbell value v (0xFF00xxxx = ring-0 write index, 0xDD02xxxx = ring-2 read index); 1 = handled */
static int
gd_db(uint32_t v)
{
	unsigned i;

	if (!gd.on || (v >> 16) != 0xFF00u) {
		return (v >> 16) == 0xDD02u && gd.on;
	}
	if ((v & 0xFFFFu) < 2u) {
		return 0;
	}
	if (ndb_d4d < sizeof io_db / sizeof io_db[0]) {
		io_db[ndb_d4d] = v;
	}
	ndb_d4d++;
	gd.ndb++;
	for (i = 0; i < ge.neinj; i++) {
		if (ge.einj[i].n == gd.ndb && ge.einj[i].pre) {
			ge_inj(i);
		}
	}
	gd_ring0(v & 0xFFFFu);
	for (i = 0; i < ge.neinj; i++) {
		if (ge.einj[i].n == gd.ndb && !ge.einj[i].pre) {
			ge_inj(i);
		}
	}
	for (i = 0; i < gd.ninj; i++) {
		if (gd.inj[i].n == gd.ndb) {
			gd_put(0x24u, 0u, 0, gd.inj[i].mb, 0u);
		}
	}
	return 1;
}

/* the doorbell hook of sweetwlan >= 17: 1 = a W-D4d doorbell (recorded, the dongle reacts), 0 = not one */
static int
d4d_db(uint32_t v)
{
	if (!(v >= 0xFF000005u && v <= 0xFF000009u) && v != 0xDD020004u && v != 0xDD020006u) {
		return 0;
	}
	if (ndb_d4d < sizeof io_db / sizeof io_db[0]) {
		io_db[ndb_d4d] = v;
	}
	ndb_d4d++;
	if (v == 0xFF000005u && io_l17 == 1) {
		io_put(2u, 0xAu, 0xFFFEu, 0, 1u, 0u, 0u, 3u, 0u);
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 3u;
	} else if (v == 0xFF000005u && io_l17 == 3) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 1u;
	} else if (v == 0xFF000005u && io_l17 == 4) {
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 0xFFFFFFFFu;
	} else if (v == 0xFF000005u && io_l17 == 5) {
		io_put(2u, 0x10u, 0u, 0, 0u, 0u, 0u, 3u, 0u);
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 3u;
	} else if (v == 0xFF000005u && io_l17 == 2) {
		io_put(2u, 0x24u, 0u, 0, 0u, 0u, 0u, 3u, 0u);
		f.hb[fplan->b[SWD_IDX_D2H_W].off / 4u] = 3u;
	} else if (v == 0xFF000006u) {
		io_answer(1u);
	} else if (v == 0xFF000008u) {
		io_answer(2u);
	}
	return 1;
}
