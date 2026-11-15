/*
 * The cases of the L25 store whitelist test added by the Opus review of T-5173 (docs/reviews/T-5173-l25wl-opus-r1.md F1-F9, the survivors of F5): the four iovars and everything near them,
 * ring-0 rewrite and wrap, partial commits, index corners, failed reads, the 4 GB carry, wrong layouts. Included by wlan_l25_wl_host_test.c (R29: file length).
 */
#define HO(x) ((uint32_t)((x) - G1PA))

/* payload bytes into memory (the L6 stores are judged elsewhere) */
static void pay_poke(const char *name, const uint8_t *tail, uint32_t tailn)
{
	uint8_t b[64];
	uint32_t i, nl = (uint32_t)strlen(name);

	memset(b, 0, sizeof b);
	memcpy(b, name, nl);
	if (tail != NULL)
		memcpy(b + nl + 1u, tail, tailn);
	for (i = 0; i < 16u; i++)
		*bptr(SWL25W_SP_HOST, B.ioctbuf + 4u * i) = w32(b, i);
}
/* an item in memory at the ring-0 write slot, bypassing the word rules (the commit is what is judged) */
static void ioct_poke(uint32_t cmd, uint32_t in, uint32_t outn)
{
	uint8_t it[56];
	const uint32_t sl = cur_wr(0), base = HO(RING0) + sl * 40u;
	unsigned k;

	swd4b_item_ioctl(it, cmd, 7u, (uint16_t)in, (uint16_t)outn, G1PA + B.ioctbuf, (uint8_t)SWL25W_EP(sl));
	for (k = 0; k < 10u; k++)
		*bptr(SWL25W_SP_HOST, base + 4u * k) = w32(it, k);
}
static void ioct_scrub(void)
{
	const uint32_t base = HO(RING0) + cur_wr(0) * 40u;
	unsigned k;

	for (k = 0; k < 10u; k++)
		*bptr(SWL25W_SP_HOST, base + 4u * k) = 0u;
}
/* a consume of a completion of transaction `tr`: ring 2 slot rd, dongle index rd + 1 */
static void cmplt(uint32_t tr)
{
	const uint32_t rd = *bptr(SWL25W_SP_HOST, HO(D2HR)) & 0xFFFFu;

	*bptr(SWL25W_SP_HOST, HO(RING2) + rd * 24u) = SWD4B_MSG_IOCTL_CMPLT;
	*bptr(SWL25W_SP_HOST, HO(RING2) + rd * 24u + 12u) = tr << 16;
	*bptr(SWL25W_SP_HOST, HO(D2HW)) = rd + 1u;
	legit(SWL25W_SP_HOST, HO(D2HR), rd + 1u, SWL25W_L8);
	legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xDD020000u | (rd + 1u), SWL25W_L9);
}
static void case_iovars(void)
{
	static const uint8_t key[20] = { 1, 2, 3 }, val[4] = { 4 }, lastw[4] = { 0, 0, 0, 1 }, laste[6] = { 0, 0, 0, 0, 0, 1 };
	static const struct { const char *n; uint32_t cmd, len; const uint8_t *t; uint32_t tn; } good[7] = {
		{ "cur_etheraddr", 262u, 20u, NULL, 0u }, { "wsec_key", 263u, 173u, key, sizeof key }, { "wsec", 262u, 9u, NULL, 0u }, { "wsec", 263u, 9u, val, sizeof val },
		{ "counters", 262u, 1200u, NULL, 0u },      /* T-924f: the read-only counters GET */
		{ "assoc_info", 262u, 64u, NULL, 0u }, { "assoc_req_ies", 262u, 512u, NULL, 0u } };      /* T-9d71: the two read-only association-request GETs */
	static const struct { const char *n; uint32_t cmd, in, outn; const uint8_t *t; uint32_t tn; } bad[] = {
		{ "pcie_bus_tput", 263u, 26u, 26u, key, sizeof key },   /* review r1 F1: upstream passes a host PA in this one */
		{ "bus:hmap", 263u, 173u, 173u, key, sizeof key },
		{ "wsec_key", 262u, 173u, 173u, NULL, 0u },             /* command of another row */
		{ "cur_etheraddr", 263u, 20u, 20u, NULL, 0u },
		{ "wsec", 263u, 173u, 173u, val, sizeof val },          /* length of another row */
		{ "wsec_key", 263u, 173u, 172u, key, sizeof key },      /* in != out */
		{ "wsec_key", 263u, 172u, 173u, key, sizeof key },
		{ "wsec_key", 263u, 1420u, 1420u, key, sizeof key },
		{ "wsec_kex", 263u, 173u, 173u, key, sizeof key },      /* last name byte */
		{ "xsec_key", 263u, 173u, 173u, key, sizeof key },      /* first name byte */
		{ "wsec_keyX", 263u, 173u, 173u, key, sizeof key },     /* no NUL after the name */
		{ "wsecX", 263u, 9u, 9u, val, sizeof val },
		{ "wsec", 262u, 9u, 9u, val, sizeof val },              /* a GET with bytes after the name */
		{ "wsec", 262u, 9u, 9u, lastw, sizeof lastw },          /* only its last byte (8) */
		{ "cur_etheraddr", 262u, 20u, 20u, laste, sizeof laste },       /* only its last byte (19) */
		{ "cur_etheraddr", 262u, 20u, 20u, key, 6u },           /* a GET with bytes after the name */
		{ "counters", 263u, 1200u, 1200u, NULL, 0u },           /* T-924f: a SET of the counters name (a write) */
		{ "counters", 262u, 1199u, 1199u, NULL, 0u },           /* length of no row */
		{ "counters", 262u, 1420u, 1420u, NULL, 0u },
		{ "counters", 262u, 1200u, 1199u, NULL, 0u },           /* in != out */
		{ "counters", 262u, 1200u, 1200u, key, 6u },            /* a GET with bytes after the name */
		{ "counterx", 262u, 1200u, 1200u, NULL, 0u },           /* last name byte */
		{ "counters", 262u, 20u, 20u, NULL, 0u },               /* the length of another row */
		{ "assoc_info", 263u, 64u, 64u, NULL, 0u },             /* T-9d71: a SET of the assoc_info name (a write) */
		{ "assoc_info", 262u, 63u, 63u, NULL, 0u },             /* length of no row */
		{ "assoc_info", 262u, 512u, 512u, NULL, 0u },           /* the length of the other new row */
		{ "assoc_info", 262u, 64u, 63u, NULL, 0u },             /* in != out */
		{ "assoc_info", 262u, 64u, 64u, key, 6u },              /* a GET with bytes after the name */
		{ "assoc_infx", 262u, 64u, 64u, NULL, 0u },             /* last name byte */
		{ "assoc_req_ies", 263u, 512u, 512u, NULL, 0u },        /* a SET (a write) */
		{ "assoc_req_ies", 262u, 511u, 511u, NULL, 0u },        /* length of no row */
		{ "assoc_req_ies", 262u, 1420u, 1420u, NULL, 0u },
		{ "assoc_req_ies", 262u, 64u, 64u, NULL, 0u },          /* the length of the other new row */
		{ "assoc_req_ies", 262u, 512u, 511u, NULL, 0u },        /* in != out */
		{ "assoc_req_ies", 262u, 512u, 512u, key, 6u },         /* a GET with bytes after the name */
		{ "assoc_req_iex", 262u, 512u, 512u, NULL, 0u },        /* last name byte */
	};
	unsigned i;

	b_world();
	for (i = 0; i < 7u; i++) {
		uint8_t it[56];
		const uint32_t sl = cur_wr(0);

		pay_poke(good[i].n, good[i].t, good[i].tn);
		swd4b_item_ioctl(it, good[i].cmd, 9u, (uint16_t)good[i].len, (uint16_t)good[i].len, G1PA + B.ioctbuf, (uint8_t)SWL25W_EP(sl));
		put_item(SWL25W_SP_HOST, HO(RING0) + sl * 40u, it, 10u, SWL25W_L2, NULL, 0);
		legit(SWL25W_SP_HOST, HO(H2DW), sl + 1u, SWL25W_L7);
		legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u | (sl + 1u), SWL25W_L9);
		CHECK(B.ioct_busy == 1u);
		/* a second one while this one is in transit: its payload is refused, its commit too */
		ioct_poke(good[i].cmd, good[i].len, good[i].len);
		mut(M_BUSY, SWL25W_SP_HOST, HO(H2DW), sl + 2u);
		ioct_scrub();
		mut(M_BUSY, SWL25W_SP_HOST, B.ioctbuf, 0u);
		cmplt(9u);
		CHECK(B.ioct_busy == 0u);
	}
	for (i = 0; i < sizeof bad / sizeof bad[0]; i++) {
		const uint32_t sl = cur_wr(0);

		pay_poke(bad[i].n, bad[i].t, bad[i].tn);
		ioct_poke(bad[i].cmd, bad[i].in, bad[i].outn);
		mut(M_IOVAR, SWL25W_SP_HOST, HO(H2DW), sl + 1u);
		ioct_scrub();
	}
}
/* swl25w_done: only THIS request's completion, on ring 2, read in full, frees the payload (T-5173 r2 N1, N4) */
static void busy_world(void)
{
	static const uint8_t val[4] = { 4 };

	b_world();
	pay_poke("wsec", val, sizeof val);
	ioct_poke(263u, 9u, 9u);        /* trans id 7 */
	swl25w_done(&B, SWL25W_SP_HOST, HO(H2DW), 1u, 0u, SWL25W_L7);
	B.pend_db = 0u;
	CHECK(B.ioct_busy == 1u && B.ioct_trans == 7u);
}
static void r2_put(uint32_t slot, uint32_t type, uint32_t tr)
{
	*bptr(SWL25W_SP_HOST, HO(RING2) + slot * 24u) = type;
	*bptr(SWL25W_SP_HOST, HO(RING2) + slot * 24u + 12u) = tr << 16;
}
static void case_done(void)
{
	/* a completion of another transaction frees nothing: the payload stays frozen (r2 N1's sequence) */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 0xFFu);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 1u, 0u, SWL25W_L8);
	B.pend_db = 0u;
	CHECK(B.ioct_busy == 1u);
	mut(M_BUSY, SWL25W_SP_HOST, B.ioctbuf, 0x41414141u);
	/* ack + completion in one batch */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_ACK, 7u);
	r2_put(1u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 2u, 0u, SWL25W_L8);
	CHECK(B.ioct_busy == 0u);
	/* the ack alone; a batch that stops before the completion */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_ACK, 7u);
	r2_put(1u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 1u, 0u, SWL25W_L8);
	CHECK(B.ioct_busy == 1u);
	/* the scan wraps at the depth: 63 -> 1 with the completion at slot 0 */
	busy_world();
	r2_put(63u, SWD4B_MSG_IOCTL_ACK, 7u);
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 1u, 63u, SWL25W_L8);
	CHECK(B.ioct_busy == 0u);
	/* a consume of ring 3 or 4 frees nothing, even over ring 2's slot of the same number */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR) + 4u, 1u, 0u, SWL25W_L8);
	CHECK(B.ioct_busy == 1u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR) + 8u, 1u, 0u, SWL25W_L8);
	CHECK(B.ioct_busy == 1u);
	/* a failed read stops the scan and leaves it busy: the type of slot 0, the trans id of the completion */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_ACK, 7u);
	r2_put(1u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	bt_fail_sp = SWL25W_SP_HOST; bt_fail_off = HO(RING2);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 2u, 0u, SWL25W_L8);
	bt_fail_sp = 0;
	CHECK(B.ioct_busy == 1u);
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	bt_fail_sp = SWL25W_SP_HOST; bt_fail_off = HO(RING2) + 12u;
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 1u, 0u, SWL25W_L8);
	bt_fail_sp = 0;
	CHECK(B.ioct_busy == 1u);
	/* ... and stops it: a later completion in the same batch does not count */
	busy_world();
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	r2_put(1u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	bt_fail_sp = SWL25W_SP_HOST; bt_fail_off = HO(RING2) + 12u;
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 2u, 0u, SWL25W_L8);
	bt_fail_sp = 0;
	CHECK(B.ioct_busy == 1u);
	/* a commit whose type or trans id cannot be read: stricter state, and no completion frees it */
	b_world();
	ioct_poke(263u, 9u, 9u);
	bt_fail_sp = SWL25W_SP_HOST; bt_fail_off = HO(RING0);
	swl25w_done(&B, SWL25W_SP_HOST, HO(H2DW), 1u, 0u, SWL25W_L7);
	bt_fail_sp = 0;
	B.pend_db = 0u;
	CHECK(B.ioct_busy == 1u && B.flow_created == 1u && B.ioct_trans == ~0u);
	r2_put(0u, SWD4B_MSG_IOCTL_CMPLT, 7u);
	swl25w_done(&B, SWL25W_SP_HOST, HO(D2HR), 1u, 0u, SWL25W_L8);
	CHECK(B.ioct_busy == 1u);
	b_world();
	ioct_poke(263u, 9u, 9u);
	bt_fail_sp = SWL25W_SP_HOST; bt_fail_off = HO(RING0) + 12u;
	swl25w_done(&B, SWL25W_SP_HOST, HO(H2DW), 1u, 0u, SWL25W_L7);
	bt_fail_sp = 0;
	CHECK(B.ioct_busy == 1u && B.flow_created == 0u && B.ioct_trans == ~0u);
}
static void case_ring0(void)
{
	const uint32_t r0 = HO(RING0);
	const uint32_t w00 = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24, w01 = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(1) << 24;

	b_world();
	mut(M_IOCT_CMD, SWL25W_SP_HOST, r0, 0x23u | 0x80u << 16 | SWL25W_EP(0) << 24);  /* a deep-sleep item: no class (r1 F6, the glue ticket names its list) */
	mut(M_REWRITE, SWL25W_SP_HOST, r0 + 40u, w01);                                  /* not the write slot */
	legit(SWL25W_SP_HOST, r0, w00, SWL25W_L2);
	mut(M_REWRITE, SWL25W_SP_HOST, r0, w00);                                        /* the same word again */
	muto(M_REWRITE, SWL25W_SP_HOST, r0, w00, 0u);                                   /* old says 0, memory does not */
	muto(M_REWRITE, SWL25W_SP_HOST, r0 + 8u, SWL25W_RESP_LEN, 5u);                  /* old is not 0 */
	*bptr(SWL25W_SP_HOST, HO(H2DW)) = 1u;
	mut(M_REWRITE, SWL25W_SP_HOST, r0, w00);                                        /* a slot already committed */
	/* sprint 2026-11-08: ring 0 wraps (DHD's ring write pointer does). Slot 127 is a write slot; its commit moves the index 127 -> 0 (a lap, doorbell 0xFF000000); on the next lap
	 * an item word's old value must equal memory (the lap before's word), the epoch counts the laps (EP(128) at slot 0), and the dongle's ring-0 read index must leave room */
	b_world();
	*bptr(SWL25W_SP_HOST, HO(H2DW)) = 127u;
	*bptr(SWL25W_SP_HOST, HO(H2DR)) = 120u;
	mut(M_WRAP, SWL25W_SP_HOST, HO(H2DW), 128u);                                    /* an index past the ring */
	{
		const uint32_t s127 = r0 + 127u * 40u, lo = (uint32_t)(G1PA + B.respbuf), hi = (uint32_t)((G1PA + B.respbuf) >> 32);
		const uint32_t wv[10] = { SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(127) << 24, 0x5u, SWL25W_RESP_LEN, 0u, lo, hi, 0u, 0u, 0u, 0u };
		const uint32_t lap1 = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24, lap2 = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(128) << 24;
		unsigned k;

		for (k = 0; k < 10u; k++) {
			legit(SWL25W_SP_HOST, s127 + 4u * k, wv[k], SWL25W_L2);
		}
		legit(SWL25W_SP_HOST, HO(H2DW), 0u, SWL25W_L7);                         /* the lap: 127 -> 0 */
		CHECK(B.wrap0 == 1u && B.pend_db == 0xFF000000u);
		legit(SWL25W_SP_PCIE, SWL25W_DB0_OFF, 0xFF000000u, SWL25W_L9);
		*bptr(SWL25W_SP_HOST, r0) = lap1;                                       /* slot 0 still holds the first lap's item */
		*bptr(SWL25W_SP_HOST, HO(H2DR)) = 1u;
		mut(M_WRAP, SWL25W_SP_HOST, r0, lap2);                                  /* the dongle has not read past slot 0: the ring is full */
		*bptr(SWL25W_SP_HOST, HO(H2DR)) = 120u;
		mut(M_EPOCH, SWL25W_SP_HOST, r0, lap1);                                 /* the first lap's epoch again */
		muto(M_REWRITE, SWL25W_SP_HOST, r0, lap2, 0u);                          /* old says 0, memory holds the first lap's word */
		acc(SWL25W_SP_HOST, r0, lap2, SWL25W_L2);                               /* the second lap's item word */
		*bptr(SWL25W_SP_HOST, HO(H2DW)) = 126u;
		mut(M_WRAP, SWL25W_SP_HOST, HO(H2DW), 128u);
	}
}
static void case_partial(void)
{
	uint8_t it[56];
	unsigned k;

	/* rx post: word 7 (pointer hi) never stored; word 0 corrupted in memory */
	b_world();
	swdp_build_rxpost(it, (uint8_t)SWL25W_EP(0), SWL25W_RX_RID, DAPA + SWL25W_RX_OFF, 2048u);
	for (k = 0; k < 8u; k++)
		*bptr(SWL25W_SP_HOST, HO(RING1) + 4u * k) = k == 7u ? 0u : w32(it, k);
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 4u, 1u);
	*bptr(SWL25W_SP_HOST, HO(RING1) + 28u) = w32(it, 7);
	acc(SWL25W_SP_HOST, HO(H2DW) + 4u, 1u, SWL25W_L7);
	*bptr(SWL25W_SP_HOST, HO(RING1)) ^= 0x100u;
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 4u, 1u);
	/* tx post: word 9 missing; flow not to be taken from the store rules alone */
	b_world();
	B.flow_open = 1u; B.flow_created = 1u;
	{
		uint8_t fr[74];

		memset(fr, 0, sizeof fr);
		swdp_build_txpost(it, 48u, 0u, SWL25W_TX_RID, fr, sizeof fr, 0u, DAPA + SWL25W_TX_OFF);
	}
	for (k = 0; k < 12u; k++)
		*bptr(SWL25W_SP_DATA, 4u * k) = k == 9u ? 0u : w32(it, k);
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 8u, 1u);
	*bptr(SWL25W_SP_DATA, 36u) = w32(it, 9);
	acc(SWL25W_SP_HOST, HO(H2DW) + 8u, 1u, SWL25W_L7);
	*bptr(SWL25W_SP_DATA, 44u) = 1u;
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 8u, 1u);
	*bptr(SWL25W_SP_DATA, 44u) = 0u;
	*bptr(SWL25W_SP_DATA, 0u) = 0u;
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW) + 8u, 1u);
	/* flow create: word 9 missing, then a ring-0 word 0 gone */
	b_world();
	swdp_build_flow_create(it, (uint8_t)SWL25W_EP(0), 0u, 2u, 0u, GWM, MAC, DAPA, 64u, 48u);
	for (k = 0; k < 10u; k++)
		*bptr(SWL25W_SP_HOST, HO(RING0) + 4u * k) = k == 9u ? 0u : w32(it, k);
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW), 1u);
	*bptr(SWL25W_SP_HOST, HO(RING0) + 36u) = w32(it, 9);
	acc(SWL25W_SP_HOST, HO(H2DW), 1u, SWL25W_L7);
	*bptr(SWL25W_SP_HOST, HO(RING0)) = 0u;
	mut(M_PARTIAL, SWL25W_SP_HOST, HO(H2DW), 1u);
}
static void case_index(void)
{
	const uint32_t hw = HO(H2DW), dr = HO(D2HR), dw = HO(D2HW);

	b_world();
	*bptr(SWL25W_SP_HOST, hw) = 3u;
	muto(M_OLD, SWL25W_SP_HOST, hw, 4u, 2u);                                        /* L7: old is not what memory holds */
	*bptr(SWL25W_SP_HOST, dr) = 3u;
	*bptr(SWL25W_SP_HOST, dw) = 9u;
	muto(M_OLD, SWL25W_SP_HOST, dr, 4u, 2u);
	mut(M_OLD, SWL25W_SP_HOST, dr, 4u | 0x10000u);                                  /* L8: upper half */
	/* Part A (sprint-wifi 2026-11-09): every ring consumes in batches of at most 16 items the dongle wrote; each ring's own depth */
	acc(SWL25W_SP_HOST, dr, 9u, SWL25W_L8);
	*bptr(SWL25W_SP_HOST, dw + 4u) = 5u;
	acc(SWL25W_SP_HOST, dr + 4u, 2u, SWL25W_L8);                                    /* ring 3: 2 of 5 written */
	acc(SWL25W_SP_HOST, dr + 4u, 5u, SWL25W_L8);                                    /* all 5 */
	mut(M_NOPEND, SWL25W_SP_HOST, dr + 4u, 6u);                                     /* one past the dongle's index */
	acc(SWL25W_SP_HOST, dr + 4u, 1u, SWL25W_L8);
	*bptr(SWL25W_SP_HOST, dw + 8u) = 5u;
	acc(SWL25W_SP_HOST, dr + 8u, 2u, SWL25W_L8);
	mut(M_NOPEND, SWL25W_SP_HOST, dr + 8u, 7u);
	*bptr(SWL25W_SP_HOST, dw + 4u) = 40u;
	acc(SWL25W_SP_HOST, dr + 4u, 16u, SWL25W_L8);                                   /* ring 3: the cap */
	mut(M_BATCH, SWL25W_SP_HOST, dr + 4u, 17u);
	*bptr(SWL25W_SP_HOST, dw + 8u) = 40u;
	mut(M_BATCH, SWL25W_SP_HOST, dr + 8u, 17u);
	*bptr(SWL25W_SP_HOST, dr + 4u) = 100u; *bptr(SWL25W_SP_HOST, dw + 4u) = 200u;
	acc(SWL25W_SP_HOST, dr + 4u, 101u, SWL25W_L8);                                  /* above ring 2's depth 64: ring 3 has 2048 */
	*bptr(SWL25W_SP_HOST, dr + 8u) = 1023u; *bptr(SWL25W_SP_HOST, dw + 8u) = 5u;
	acc(SWL25W_SP_HOST, dr + 8u, 0u, SWL25W_L8);                                    /* ring 4 wraps at 1024 */
	mut(M_OTHER_WORD, SWL25W_SP_HOST, dr + 12u, 1u);
	/* an index at or above the depth, whichever one */
	*bptr(SWL25W_SP_HOST, dr) = 64u; *bptr(SWL25W_SP_HOST, dw) = 3u;
	mut(M_DEPTH, SWL25W_SP_HOST, dr, 1u);                                           /* old */
	*bptr(SWL25W_SP_HOST, dr) = 0u;
	mut(M_DEPTH, SWL25W_SP_HOST, dr, 65u);                                          /* new */
	mut(M_DEPTH, SWL25W_SP_HOST, dr, 64u);
	*bptr(SWL25W_SP_HOST, dw) = 65u;
	mut(M_DEPTH, SWL25W_SP_HOST, dr, 1u);                                           /* the dongle's */
	/* no move at all, with items pending; the batch cap */
	*bptr(SWL25W_SP_HOST, dr) = 2u; *bptr(SWL25W_SP_HOST, dw) = 5u;
	mut(M_CONSUME2, SWL25W_SP_HOST, dr, 2u);
	*bptr(SWL25W_SP_HOST, dr) = 0u; *bptr(SWL25W_SP_HOST, dw) = 20u;
	acc(SWL25W_SP_HOST, dr, 16u, SWL25W_L8);
	mut(M_BATCH, SWL25W_SP_HOST, dr, 17u);
	/* the glue says open, no create was committed: tx stays shut (r2 N6), a create is refused too (r1 F4) */
	b_world();
	B.flow_open = 1u;
	mut(M_NOTOPEN, SWL25W_SP_DATA, 0u, SWL25W_MSG_TXPOST);
	mut(M_NOTOPEN, SWL25W_SP_DATA, SWL25W_TX_OFF, 1u);
	mut(M_FLOW2, SWL25W_SP_HOST, HO(RING0), SWL25W_MSG_FLOW_CREATE | SWL25W_EP(0) << 24);
}
/* one failed read at (sp, off): the store is refused; nothing is ever taken as ok from a read that failed */
static void rdfail(int sp, uint32_t off, int space, uint32_t soff, uint32_t v)
{
	bt_fail_sp = sp; bt_fail_off = off;
	mut(M_READFAIL, space, soff, v);
	bt_fail_sp = 0;
}
static void case_readfail(void)
{
	const uint32_t r0 = HO(RING0), hw = HO(H2DW), dr = HO(D2HR), dw = HO(D2HW);
	uint8_t it[56];
	unsigned k;
	const char *why = "";

	b_world();
	B.flow_open = 1u; B.flow_created = 1u;
	/* the write-slot read of each class */
	rdfail(SWL25W_SP_HOST, hw, SWL25W_SP_HOST, r0, SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24);
	rdfail(SWL25W_SP_HOST, hw + 4u, SWL25W_SP_HOST, HO(RING1), SWL25W_MSG_RXPOST | SWL25W_EP(0) << 24);
	rdfail(SWL25W_SP_HOST, hw + 8u, SWL25W_SP_DATA, 0u, SWL25W_MSG_TXPOST);
	rdfail(SWL25W_SP_HOST, hw + 8u, SWL25W_SP_DATA, SWL25W_TX_OFF, 1u);
	/* ring-0: the word itself, word 0, the buffer pointer lo */
	rdfail(SWL25W_SP_HOST, r0, SWL25W_SP_HOST, r0, SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24);
	*bptr(SWL25W_SP_HOST, r0) = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24;
	rdfail(SWL25W_SP_HOST, r0, SWL25W_SP_HOST, r0 + 8u, SWL25W_RESP_LEN);
	*bptr(SWL25W_SP_HOST, r0 + 16u) = swl25w_lo(G1PA + B.respbuf);
	rdfail(SWL25W_SP_HOST, r0 + 16u, SWL25W_SP_HOST, r0 + 20u, swl25w_hi(G1PA + B.respbuf));
	/* the index words */
	rdfail(SWL25W_SP_HOST, hw + 4u, SWL25W_SP_HOST, hw + 4u, 1u);
	*bptr(SWL25W_SP_HOST, dw) = 1u;
	rdfail(SWL25W_SP_HOST, dr, SWL25W_SP_HOST, dr, 1u);
	rdfail(SWL25W_SP_HOST, dw, SWL25W_SP_HOST, dr, 1u);
	/* the commit re-reads: ring 0, ring 1, the flow ring, the ioctl's words and its payload */
	b_world();
	B.flow_open = 1u; B.flow_created = 1u;
	swdp_build_rxpost(it, (uint8_t)SWL25W_EP(0), SWL25W_RX_RID, DAPA + SWL25W_RX_OFF, 2048u);
	for (k = 0; k < 8u; k++)
		*bptr(SWL25W_SP_HOST, HO(RING1) + 4u * k) = w32(it, k);
	rdfail(SWL25W_SP_HOST, HO(RING1) + 12u, SWL25W_SP_HOST, hw + 4u, 1u);
	{
		uint8_t fr[74];

		memset(fr, 0, sizeof fr);
		swdp_build_txpost(it, 48u, 0u, SWL25W_TX_RID, fr, sizeof fr, 0u, DAPA + SWL25W_TX_OFF);
	}
	for (k = 0; k < 12u; k++)
		*bptr(SWL25W_SP_DATA, 4u * k) = w32(it, k);
	rdfail(SWL25W_SP_DATA, 12u, SWL25W_SP_HOST, hw + 8u, 1u);
	pay_poke("wsec", NULL, 0u);
	ioct_poke(262u, 9u, 9u);
	rdfail(SWL25W_SP_HOST, r0 + 24u, SWL25W_SP_HOST, hw, 1u);
	rdfail(SWL25W_SP_HOST, B.ioctbuf, SWL25W_SP_HOST, hw, 1u);
	rdfail(SWL25W_SP_HOST, B.ioctbuf + 4u, SWL25W_SP_HOST, hw, 1u);
	acc(SWL25W_SP_HOST, hw, 1u, SWL25W_L7);
	/* no accessor, no whitelist */
	CHECK(swl25w_ok(NULL, SWL25W_SP_HOST, 0u, 0u, 0u, &why) < 0 && strstr(why, "no whitelist") != NULL);
	B.rd = NULL;
	mut(M_LAYOUT, SWL25W_SP_HOST, B.ioctbuf, 0u);
	B.rd = bt_rd;
}
/* a block straddling the 4 GB line: every hi word is the hi of THAT address */
static void case_carry(void)
{
	const uint64_t g = 0x8FFFF0000ull;
	const uint32_t r0 = HO(RING0), rr = HO(RING1);
	uint32_t *w0;

	b_world();
	B.g1_pa = g; B.data_pa = g + 2ull * G1LEN;          /* DATA past G1 */
	B.flow_open = 1u; B.flow_created = 1u;
	/* ioctl payload: G1 + 0x60000 is above the line, G1 is below */
	w0 = bptr(SWL25W_SP_HOST, r0);
	*w0 = SWL25W_MSG_IOCTL_REQ | 0x80u << 16 | SWL25W_EP(0) << 24;
	acc(SWL25W_SP_HOST, r0 + 28u, 9u, SWL25W_L2);
	mut(M_CARRY, SWL25W_SP_HOST, r0 + 28u, 8u);
	/* a response buffer whose PA crosses the line: G1 near the line, the 3rd buffer is above it, the 1st below */
	B.g1_pa = 0x900000000ull - 0x70000u - 2u * 8192u; B.data_pa = 0x900000000ull + 0x100000ull;
	*w0 = SWL25W_MSG_RESPBUF | 0x80u << 16 | SWL25W_EP(0) << 24;
	*bptr(SWL25W_SP_HOST, r0 + 16u) = 0u;              /* buffer 2 starts exactly at lo 0 */
	acc(SWL25W_SP_HOST, r0 + 20u, 9u, SWL25W_L2);
	mut(M_CARRY, SWL25W_SP_HOST, r0 + 20u, 8u);
	acc(SWL25W_SP_HOST, r0 + 16u, swl25w_lo(B.g1_pa + 0x70000u), SWL25W_L2);
	*bptr(SWL25W_SP_HOST, r0 + 16u) = swl25w_lo(B.g1_pa + 0x70000u);
	acc(SWL25W_SP_HOST, r0 + 20u, 8u, SWL25W_L2);
	mut(M_CARRY, SWL25W_SP_HOST, r0 + 20u, 9u);
	*w0 = 0u; *bptr(SWL25W_SP_HOST, r0 + 16u) = 0u;
	/* DATA crossing the line: rx buffers above, flow items below; tx buffer 8 is the first above */
	B.g1_pa = G1PA; B.data_pa = 0x900000000ull - SWL25W_RX_OFF;
	*bptr(SWL25W_SP_HOST, rr) = SWL25W_MSG_RXPOST | SWL25W_EP(0) << 24;
	acc(SWL25W_SP_HOST, rr + 28u, 9u, SWL25W_L3);
	mut(M_CARRY, SWL25W_SP_HOST, rr + 28u, 8u);
	acc(SWL25W_SP_DATA, 36u, 8u, SWL25W_L4);           /* tx buffer 0: 0xC000 - 0x4000 below the line */
	mut(M_CARRY, SWL25W_SP_DATA, 36u, 9u);
	B.data_pa = 0x900000000ull - SWL25W_TX_OFF;
	acc(SWL25W_SP_DATA, 36u, 9u, SWL25W_L4);
	mut(M_CARRY, SWL25W_SP_DATA, 36u, 8u);
	/* the flow ring pointer hi is DATA's */
	B.data_pa = 0x900000000ull;
	*w0 = SWL25W_MSG_FLOW_CREATE | SWL25W_EP(0) << 24;
	B.flow_open = 0u; B.flow_created = 0u;
	acc(SWL25W_SP_HOST, r0 + 36u, 9u, SWL25W_L1);
	mut(M_CARRY, SWL25W_SP_HOST, r0 + 36u, 8u);
	*w0 = 0u;
}
/* every clause of swl25w_layout_bad: one wrong field, THAT clause fires, the same legitimate L6 store refused */
static void lay(int want)
{
	const int got = swl25w_layout_bad(&B);

	if (got != want) {
		fprintf(stderr, "FAIL layout: clause %d, want %d\n", got, want);
		fails++;
	}
	mut(M_LAYOUT, SWL25W_SP_HOST, 0x60000u, 0u);
}
static void case_layout(void)
{
	static const struct { unsigned field; uint32_t mode; int want; } one[] = {
		/* field: 0 flowid, 1..2 g1_pa data_pa OR-ed, 3..4 g1_pa data_pa =, 5 g1_len, 6 data_len, 7..11 depth[n] */
		{ 0, 1u, 2 }, { 0, 3u, 2 }, { 1, 0x10u, 4 }, { 2, 0x10u, 4 }, { 5, 0u, 11 }, { 6, SWL25W_RX_OFF + SWL25W_RX_N * SWL25W_BUF_SZ - 4u, 5 },
		{ 7, 1u, 8 }, { 8, 15u, 8 }, { 9, 0u, 9 }, { 10, 0x10001u, 9 }, { 7, 0x40000000u, 9 }, { 11, 0u, 9 },
	};
	unsigned i, r;

	b_world();
	CHECK(swl25w_layout_bad(&B) == 0);
	for (i = 0; i < sizeof one / sizeof one[0]; i++) {
		b_world();
		switch (one[i].field) {
		case 0: B.flowid = one[i].mode; break;
		case 1: B.g1_pa = B.data_pa + B.data_len + one[i].mode; break;     /* not by overlap: past DATA */
		case 2: B.data_pa |= one[i].mode; break;
		case 5: B.g1_len = one[i].mode; break;
		case 6: B.data_len = one[i].mode; break;
		default: B.depth[one[i].field - 7u] = one[i].mode; break;
		}
		lay(one[i].want);
	}
	b_world(); B.rd = NULL; lay(1);
	b_world(); B.data_pa = 0u; lay(3);                                          /* r2 N2 */
	b_world(); B.g1_pa = 0u; lay(3);
	b_world(); B.data_pa = 0xFFFFFFFFFFFFF000ull; lay(6);                        /* the block wraps 2^64 */
	b_world(); B.g1_pa = 0xFFFFFFFFFFFE0000ull; lay(6);
	b_world(); B.data_pa = B.g1_pa + 0x1000u; lay(7);                            /* the two blocks overlap */
	b_world(); B.g1_pa = B.data_pa + 0x1000u; lay(7);
	b_world(); B.h2d_w |= 2u; lay(10);
	b_world(); B.g1_len = B.evbuf + 8u * 8192u - 4u; lay(11);                    /* the last region runs past the block */
	b_world(); B.evbuf = 0xFFFFF000u; lay(11);                                   /* an offset that would wrap in 32 bits */
	b_world(); B.ring[4] = 0xFFFFFF00u; lay(11);
	b_world(); B.ioctbuf = 0u; lay(12);                                          /* a region at G1+0: only ring 0 may sit there */
	b_world(); B.respbuf = 0u; lay(12);
	b_world(); B.evbuf = 0u; lay(12);
	b_world(); B.h2d_w = 0u; lay(12);
	b_world(); B.ioctbuf = B.h2d_w; lay(13);                                     /* a free write to an index */
	b_world(); B.ring[1] = B.ring[0]; lay(13);
	/* each region's SIZE: moved so that only its last word lies in a neighbour; a shorter size in the check would miss it */
	for (r = 0; r < 11u; r++) {
		static const uint64_t cnt[11] = { 12u, 12u, 12u, 128u * 40u, 2048u * 32u, 64u * 24u, 2048u * 24u, 1024u * 40u, 1420u, 65536u, 65536u };
		uint32_t *const f[11] = { &B.h2d_w, &B.d2h_w, &B.d2h_r, &B.ring[0], &B.ring[1], &B.ring[2], &B.ring[3], &B.ring[4], &B.ioctbuf, &B.respbuf, &B.evbuf };

		b_world();
		B.ioctbuf = 0x90000u;   /* leaves 0x5A000..0x70000 free below the neighbour */
		if (r == 8u) {
			B.ioctbuf = B.respbuf - 1420u + 4u;
		} else if (r == 9u) {
			B.respbuf = B.evbuf - 65536u + 4u;
		} else {
			*f[r] = B.respbuf - (uint32_t)cnt[r] + 4u;
		}
		lay(13);
	}
}
static void battery2(void)
{
	case_iovars();
	case_done();
	case_ring0();
	case_partial();
	case_index();
	case_readfail();
	case_carry();
	case_layout();
}
