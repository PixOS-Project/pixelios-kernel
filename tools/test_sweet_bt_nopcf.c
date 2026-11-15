/* Host test of patch 0231 (sweetbtnopcf=1, T-b58f, W-BTSCAN): the REAL sweet_bt_uart.c (patches 0189..0231) with a fake register file and a fake BCM4389 that answers
 * every command with the Command Complete it gave in bt-up14 (fce9 05/07 included), and a fake kctl client (bluetoothd) that records every byte it gets.
 * bluetoothd's bt-up14 scan cycle (the 16-byte service UUID replaced by a dummy 0xa5 pattern) goes through sbu_tx_kctl; sbu_rx_poll is the rx thread. No phone, no network. */
#include "kstub.h"
#include "sweet_bt.c"
#include "sweet_bt_uart.c"
#undef printf
static char logbuf[1 << 19]; static size_t loglen;
int sbt_printf(const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap); va_end(ap); if (n > 0) loglen += (size_t)n; return n; }
static uint8_t app[1 << 14]; static size_t napp;        /* what bluetoothd got */
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r;(void)u;(void)f; memcpy(app + napp, d, l); napp += l; return 0; }
void mbuf_freem(mbuf_t m) { (void)m; }
int tsleep(void *c, int p, const char *w, int t) { (void)c;(void)p;(void)w;(void)t; return 0; }
void delay(int us) { (void)us; }
bool sweet_usb_board_is_gs101(void) { return true; }
static int knob_nopcf = -1;     /* -1 = boot-arg absent, else its value (review F2: the parse runs) */
static int knob_pubscan = -1;   /* patch 0232 (T-0936): sweetbtpubscan, -1 = absent */
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l;
	if (!strcmp(n, "sweetbtnopcf") && knob_nopcf >= 0) { *(int *)v = knob_nopcf; return 1; }
	if (!strcmp(n, "sweetbtpubscan") && knob_pubscan >= 0) { *(int *)v = knob_pubscan; return 1; }
	return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
void thread_deallocate(void *t) { (void)t; }
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { (void)r; *ref = (kern_ctl_ref)1; return 0; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 1; }

#define CLK 196646400u
static uint8_t rxq[1 << 16]; static int rxh, rxt;
static uint8_t chip[1 << 14]; static size_t nchip;      /* every byte the chip got */
static uint8_t txb[300]; static int ntx;
static int pcf_seen, hold_answers, rx_err_once;
static int own_addr = -1, scan_status = -1;    /* fake chip, Core v5.4 Vol 4 Part E s.7.8.11: Scan Enable ON with Own_Address_Type 0x01/0x03 and no 0x2005 -> 0x12 */
static void push(const uint8_t *b, int n) { memcpy(rxq + rxt, b, n); rxt += n; }
static void answer(void)
{
	uint16_t op = txb[1] | (txb[2] << 8);
	uint8_t e[20] = { 0x04, 0x0e, 0, 0x01, txb[1], txb[2] }; int k = 6;
	if (op == 0xfce9 && (txb[4] == 0x05 || txb[4] == 0x07)) pcf_seen++;
	if (op == 0xfce9) {             /* [obs bt-up14]: 05/07 status 0 + sub (+ 07 count), 01 status 0 + sub, others 0x01 + sub */
		int known = txb[4] == 0x05 || txb[4] == 0x07 || txb[4] == 0x01;
		e[k++] = known ? 0x00 : 0x01; e[k++] = txb[4];
		if (txb[4] == 0x05 || txb[4] == 0x07) e[k++] = txb[5];
		if (txb[4] == 0x07) e[k++] = txb[5] == 0x00 ? 0x07 : 0x08;
	} else if (op == 0x200b) {
		own_addr = txb[9]; e[k++] = 0x00;
	} else if (op == 0x200c && txb[4] == 0x01) {
		scan_status = (own_addr == 0x01 || own_addr == 0x03) ? 0x12 : 0x00; e[k++] = (uint8_t)scan_status;
	} else {
		e[k++] = 0x00;
	}
	e[2] = (uint8_t)(k - 3);
	if (!hold_answers) push(e, k);
}
static uint32_t f_rd(uint32_t pa)
{
	int cnt = rxt - rxh;
	if (pa == 0x175B0000u + U_UERSTAT) { int e = rx_err_once; rx_err_once = 0; return e ? 0x1u : 0; }   /* overrun once when armed */
	if (pa == 0x175B0000u + U_UFSTAT) return cnt > 255 ? 255 : cnt;
	if (pa == 0x175B0000u + U_UTRSTAT) return 4u | (cnt > 0);
	if (pa == 0x175B0000u + U_URXH) return cnt > 0 ? rxq[rxh++] : 0;
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	if (pa == 0x175B0000u + U_UTXH) {
		chip[nchip++] = (uint8_t)v;
		txb[ntx++] = (uint8_t)v;
		if (ntx >= 4 && txb[0] == 0x01 && ntx == 4 + txb[3]) { answer(); ntx = 0; }
	}
}
static void f_us(unsigned us) { (void)us; }
static void f_ms(unsigned ms) { (void)ms; }
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static int fails;
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static int count_str(const char *s) { int c = 0; for (const char *p = logbuf; (p = strstr(p, s)) != NULL; p += strlen(s)) c++; return c; }

/* bluetoothd's bt-up14 scan cycle [obs usbnet.log rows after exec bluetoothd-m49]; U = the 16-byte service UUID (dummy 0xa5 here, rule 4) */
static const uint8_t c_en[] = { 0x01, 0xe9, 0xfc, 0x05, 0x01, 0x27, 0x10, 0x15, 0x00 };
static const uint8_t c_e2a[] = { 0x01, 0xe2, 0xfc, 0x08, 0x1e, 0, 0, 0, 0, 0, 0, 0 };
static const uint8_t c_p5[] = { 0x01, 0xe9, 0xfc, 0x0e, 0x05, 0x00, 0, 0, 0, 0, 0, 0, 0x02, 0x04, 0, 0, 0, 0 };
static uint8_t c_p7[30] = { 0x01, 0xe9, 0xfc, 0x1a, 0x07, 0x00 };
static const uint8_t c_e2b[] = { 0x01, 0xe2, 0xfc, 0x05, 0x36, 0, 0, 0, 0 };
static const uint8_t c_sp[] = { 0x01, 0x0b, 0x20, 0x07, 0x00, 0xe0, 0x01, 0x30, 0x00, 0x01, 0x00 };   /* own address 0x01 as bluetoothd sends it (0226 rewrites it) */
static const uint8_t c_on[] = { 0x01, 0x0c, 0x20, 0x02, 0x01, 0x01 };
static const uint8_t c_off[] = { 0x01, 0x0c, 0x20, 0x02, 0x00, 0x01 };
static const uint8_t c_d5[] = { 0x01, 0xe9, 0xfc, 0x09, 0x05, 0x02, 0, 0, 0, 0, 0, 0, 0x02 };
static const uint8_t c_d7[] = { 0x01, 0xe9, 0xfc, 0x09, 0x07, 0x02, 0, 0, 0, 0, 0, 0, 0x02 };
static const uint8_t c_1e[] = { 0x01, 0xe9, 0xfc, 0x01, 0x1e };
static const uint8_t c_35[] = { 0x01, 0xe9, 0xfc, 0x02, 0x35, 0x02 };
static const struct { const uint8_t *p; size_t n; } cyc[] = {
	{ c_en, sizeof(c_en) }, { c_e2a, sizeof(c_e2a) }, { c_p5, sizeof(c_p5) }, { c_p7, sizeof(c_p7) }, { c_e2b, sizeof(c_e2b) }, { c_sp, sizeof(c_sp) }, { c_on, sizeof(c_on) },
	{ c_off, sizeof(c_off) }, { c_d5, sizeof(c_d5) }, { c_d7, sizeof(c_d7) }, { c_1e, sizeof(c_1e) }, { c_35, sizeof(c_35) },
	{ c_en, sizeof(c_en) }, { c_e2a, sizeof(c_e2a) }, { c_p5, sizeof(c_p5) }, { c_p7, sizeof(c_p7) }, { c_e2b, sizeof(c_e2b) }, { c_sp, sizeof(c_sp) }, { c_on, sizeof(c_on) },
};
#define NCYC (sizeof(cyc) / sizeof(cyc[0]))
static int is_pcf(const uint8_t *p) { return p[0] == 0x01 && p[1] == 0xe9 && p[2] == 0xfc && (p[4] == 0x05 || p[4] == 0x07); }
static const uint8_t adv[] = { 0x04, 0x3e, 0x0f, 0x02, 0x01, 0x00, 0x00, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0x03, 0x02, 0x01, 0x06, 0xc4 };

static void reset(int nopcf, int pubscan)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; sbu.clk_hz = CLK; sbu.baud = 115200; sbu.rate = CLK;
	rxh = rxt = ntx = 0; nchip = napp = 0; pcf_seen = 0; hold_answers = 0; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0;
	sbu_nopcf_on = sbu_nopcf_rx = nopcf; rx_err_once = 0; sbu_pubscan_on = pubscan != 0; sbu_pubscan_active = pubscan == 2; sbu_pubscan_nact = 0; own_addr = -1; scan_status = -1; sbu_nopcf_n = 0; sbu_pubscan_n = 0; sbu_rw_lost = false; sbu_rw_off = 0;
	sbu_rw200b_on = sbu_rw200c_on = false; sbu_nopadv_on = true;    /* the bt-up14 boot-args have sweetbtnopadv=1 */
	sbu_tx_off = 0; sbu_pcf_nhold = 0; sbu_pcf_drop = false; sbu_pcf_qh = sbu_pcf_qt = 0;
	sbt.ref[0] = (kern_ctl_ref)1; sbt.busy[0] = 1;
}
/* bluetoothd: one write per command, waits for its answer (the rx thread polls) */
static void run_cycle(void) { for (size_t i = 0; i < NCYC; i++) { CHECK(sbu_tx_kctl(cyc[i].p, cyc[i].n) == 0); (void)sbu_rx_poll(); } }

int main(void)
{
	static uint8_t ref_app[1 << 14]; size_t nref; static uint8_t exp_chip[1 << 14]; size_t nexp = 0;

	for (int i = 6; i < 22; i++) c_p7[i] = 0xa5;
	for (size_t i = 0; i < NCYC; i++) if (!is_pcf(cyc[i].p)) { memcpy(exp_chip + nexp, cyc[i].p, cyc[i].n); nexp += cyc[i].n; }

	/* 1. knob off (pubscan on as bt-up14): the reference, every command reaches the chip, bluetoothd gets the chip's answers */
	reset(0, 1); run_cycle();
	CHECK(pcf_seen == 6); CHECK(!has("sweetbtnopcf")); CHECK(count_str("sweetbtpubscan=1 rewrote") == 2); CHECK(!has("rx-synth"));
	memcpy(ref_app, app, napp); nref = napp;
	{       /* knob off and pubscan off: the tx bytes are byte-identical to the input */
		static uint8_t all[1 << 14]; size_t na = 0;
		for (size_t i = 0; i < NCYC; i++) { memcpy(all + na, cyc[i].p, cyc[i].n); na += cyc[i].n; }
		reset(0, 0); run_cycle();
		CHECK(nchip == na && memcmp(chip, all, na) == 0); CHECK(!has("BT GAP"));
	}

	/* 2. knob on: the chip gets no fce9 05/07 and every other command byte-exact (pubscan rewrite included), bluetoothd gets the same bytes as with the knob off */
	reset(1, 1); run_cycle();
	CHECK(pcf_seen == 0);
	exp_chip[0] = exp_chip[0];
	{
		static uint8_t e2[1 << 14]; memcpy(e2, exp_chip, nexp);
		for (size_t i = 0; i + 11 <= nexp; i++) if (memcmp(e2 + i, c_sp, 11) == 0) e2[i + 9] = 0x00;      /* the 0226 rewrite */
		CHECK(nchip == nexp && memcmp(chip, e2, nexp) == 0);
	}
	CHECK(napp == nref && memcmp(app, ref_app, nref) == 0);
	CHECK(count_str("BT GAP: sweetbtnopcf=1 withheld fce9 sub ") == 6);
	CHECK(has("withheld fce9 sub 0x05 action 0x00 plen 14 (x1)")); CHECK(has("withheld fce9 sub 0x07 action 0x00 plen 26 (x2)"));
	CHECK(has("withheld fce9 sub 0x05 action 0x02 plen 9 (x3)")); CHECK(has("withheld fce9 sub 0x07 action 0x02 plen 9 (x4)"));
	CHECK(count_str("BT HCI: rx-synth ") == 6); CHECK(has("BT HCI: rx-synth 10 bytes: 04 0e 07 01 e9 fc 00 07 02 08"));
	CHECK(!has("a5 a5")); CHECK(count_str("BT GAP:") == 6 + 2);        /* 6 withheld + 2 pubscan rows, nothing else */
	CHECK(!has("e9 fc 1a 07"));     /* the filter command is never traced as tx */

	/* 3. the answer never splits a chip event: half an LE report is in flight when fce9 05 is withheld; it is delivered after the report ends */
	reset(1, 0);
	push(adv, 9); (void)sbu_rx_poll(); CHECK(sbu_rw_off != 0);
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); (void)sbu_rx_poll();
	CHECK(napp == 9);               /* nothing injected inside the event */
	push(adv + 9, sizeof(adv) - 9); (void)sbu_rx_poll();
	CHECK(napp == sizeof(adv) + 9 && memcmp(app, adv, sizeof(adv)) == 0 && memcmp(app + sizeof(adv), sbu_pcf_ans[0].b, 9) == 0);
	CHECK(nchip == 0);
	/* 3b. the same with sweetbtnopadv off: the framer runs for this knob alone */
	reset(1, 0); sbu_nopadv_on = false;
	push(adv, 9); (void)sbu_rx_poll(); CHECK(sbu_rw_off == 9);
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); (void)sbu_rx_poll(); CHECK(napp == 9);
	push(adv + 9, sizeof(adv) - 9); (void)sbu_rx_poll(); CHECK(napp == sizeof(adv) + 9 && memcmp(app + sizeof(adv), sbu_pcf_ans[0].b, 9) == 0);

	/* 4. a command split over writes: fce9 05 00 in 1 + 4 + 13 bytes withheld; fce9 01 in 3 + 6 sent byte-exact; 0x200b split at the rewritten byte */
	reset(1, 1);
	CHECK(sbu_tx_kctl(c_p5, 1) == 0); CHECK(sbu_tx_kctl(c_p5 + 1, 4) == 0); CHECK(nchip == 0); CHECK(sbu_tx_kctl(c_p5 + 5, sizeof(c_p5) - 5) == 0);
	CHECK(nchip == 0); CHECK(count_str("withheld fce9 sub 0x05 action 0x00 plen 14 (x1)") == 1);
	CHECK(sbu_tx_kctl(c_en, 3) == 0); CHECK(nchip == 0); CHECK(sbu_tx_kctl(c_en + 3, sizeof(c_en) - 3) == 0);
	CHECK(nchip == sizeof(c_en) && memcmp(chip, c_en, sizeof(c_en)) == 0);
	CHECK(sbu_tx_kctl(c_sp, 9) == 0); CHECK(sbu_tx_kctl(c_sp + 9, 2) == 0);
	CHECK(nchip == sizeof(c_en) + 11 && chip[sizeof(c_en) + 9] == 0x00); CHECK(count_str("sweetbtpubscan=1 rewrote") == 1);
	(void)sbu_rx_poll(); CHECK(napp == 9 + 8 + 7);

	/* 5. unknown action, sub 05 with plen 1, other fce9 sub-ops, plen 0: sent unchanged; the first two loud */
	reset(1, 0);
	{
		static const uint8_t u1[] = { 0x01, 0xe9, 0xfc, 0x02, 0x05, 0x01 }, u2[] = { 0x01, 0xe9, 0xfc, 0x01, 0x07 }, u3[] = { 0x01, 0xe9, 0xfc, 0x00 }, u4[] = { 0x01, 0x03, 0x0c, 0x00 };
		CHECK(sbu_tx_kctl(u1, sizeof(u1)) == 0); CHECK(sbu_tx_kctl(u2, sizeof(u2)) == 0); CHECK(sbu_tx_kctl(u3, sizeof(u3)) == 0); CHECK(sbu_tx_kctl(u4, sizeof(u4)) == 0);
		CHECK(sbu_tx_kctl(c_1e, sizeof(c_1e)) == 0); CHECK(sbu_tx_kctl(c_35, sizeof(c_35)) == 0);
		CHECK(nchip == sizeof(u1) + sizeof(u2) + sizeof(u3) + sizeof(u4) + sizeof(c_1e) + sizeof(c_35));
		CHECK(memcmp(chip, u1, sizeof(u1)) == 0 && memcmp(chip + sizeof(u1), u2, sizeof(u2)) == 0);
		CHECK(has("BT GAP: sweetbtnopcf: fce9 sub 0x5 action 0x1 has no recorded answer: sent unchanged"));
		CHECK(has("BT GAP: sweetbtnopcf: fce9 sub 0x7 plen 1 has no action byte: sent unchanged"));
		CHECK(!has("withheld")); CHECK(count_str("BT GAP:") == 2);
	}

	/* 6. queue full: 9 withheld commands with no rx pass between them: the 9th is sent, loud; the 8 answers come in order at the next pass */
	reset(1, 0); hold_answers = 1;
	for (int i = 0; i < 9; i++) CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0);
	CHECK(nchip == sizeof(c_p5)); CHECK(has("BT GAP: sweetbtnopcf: answer queue full (8): fce9 sub 0x5 action 0 sent unchanged")); CHECK(count_str("withheld fce9") == 8);
	(void)sbu_rx_poll(); CHECK(napp == 8 * 9); CHECK(count_str("BT HCI: rx-synth ") == 8);

	/* 7. framer lost (an ACL packet on rx): later fce9 05/07 are sent unchanged, loud; a queued answer goes out only at a quiet pass, loud */
	reset(1, 0); hold_answers = 1;
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); CHECK(nchip == 0);
	{ static const uint8_t acl[] = { 0x02, 0x01, 0x20, 0x01, 0x00, 0xaa }; push(acl, sizeof(acl)); }
	(void)sbu_rx_poll(); CHECK(sbu_rw_lost); CHECK(napp == 6);       /* the pass with chip bytes does not inject */
	(void)sbu_rx_poll(); CHECK(napp == 6 + 9); CHECK(has("rx framer lost, a queued fce9 answer is delivered at a quiet rx pass"));
	CHECK(sbu_tx_kctl(c_p7, sizeof(c_p7)) == 0); CHECK(nchip == sizeof(c_p7)); CHECK(has("BT GAP: sweetbtnopcf: rx framer lost: fce9 sub 0x7 action 0 sent unchanged"));

	/* 8. a bad tx type byte stops both tx knobs, loud; later fce9 05 passes */
	reset(1, 1);
	{ static const uint8_t bad[] = { 0x09 }; CHECK(sbu_tx_kctl(bad, 1) == 0); }
	CHECK(has("BT GAP: sweetbtpubscan: tx byte 0x9 is no H4 packet type: rewrite disabled for the rest of the boot"));
	CHECK(has("BT GAP: sweetbtnopcf: tx byte 0x9 is no H4 packet type: fce9 05/07 pass unchanged"));
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); CHECK(nchip == 1 + sizeof(c_p5));

	/* 9. review F1: a withheld command's answer is queued, then a bad tx type byte stops the tx parser: the queued answer still reaches bluetoothd at the next pass */
	reset(1, 0); hold_answers = 1;
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); CHECK(nchip == 0);
	{ static const uint8_t bad[] = { 0x09 }; CHECK(sbu_tx_kctl(bad, 1) == 0); }
	CHECK(!sbu_nopcf_on); CHECK(has("BT GAP: sweetbtnopcf: tx byte 0x9 is no H4 packet type"));
	(void)sbu_rx_poll(); CHECK(napp == 9 && memcmp(app, sbu_pcf_ans[0].b, 9) == 0);

	/* 10. review F2/R5: a withheld command that ends on its action byte (plen 2) does not eat the next packet in the same write */
	reset(1, 0);
	{
		static const uint8_t w[] = { 0x01, 0xe9, 0xfc, 0x02, 0x05, 0x00, 0x01, 0x03, 0x0c, 0x00 };
		CHECK(sbu_tx_kctl(w, sizeof(w)) == 0);
		CHECK(nchip == 4 && memcmp(chip, w + 6, 4) == 0); CHECK(has("withheld fce9 sub 0x05 action 0x00 plen 2 (x1)"));
	}

	/* 11. review F2/R1: 5 held bytes, then a 517-byte write whose first byte (unknown action) flushes them: 522 chip bytes, exact, in bounds */
	reset(1, 0);
	{
		static uint8_t w[517]; size_t k = 0;
		w[k++] = 0x01; for (int i = 0; i < 12; i++) w[k++] = 0x00;      /* action 0x01 (no recorded answer) + 12 params of the plen-14 command */
		while (k + 4 <= sizeof(w)) { w[k++] = 0x01; w[k++] = 0x03; w[k++] = 0x0c; w[k++] = 0x00; }      /* HCI Reset x126 */
		CHECK(k == sizeof(w));
		CHECK(sbu_tx_kctl(c_p5, 5) == 0); CHECK(nchip == 0);
		CHECK(sbu_tx_kctl(w, sizeof(w)) == 0);
		CHECK(nchip == 5 + sizeof(w) && memcmp(chip, c_p5, 5) == 0 && memcmp(chip + 5, w, sizeof(w)) == 0);
		CHECK(has("BT GAP: sweetbtnopcf: fce9 sub 0x5 action 0x1 has no recorded answer: sent unchanged"));
	}

	/* 12. review F2: an rx error mid-event with only nopcf on resets the framer, so a queued answer is delivered at the next pass */
	reset(1, 0); sbu_nopadv_on = false;
	push(adv, 9); (void)sbu_rx_poll(); CHECK(sbu_rw_off == 9);
	rx_err_once = 1; (void)sbu_rx_poll(); CHECK(sbu_rw_off == 0); CHECK(has("framing reset after rx error"));
	CHECK(sbu_tx_kctl(c_p5, sizeof(c_p5)) == 0); (void)sbu_rx_poll(); CHECK(napp == 9 + 9);

	/* 13. review F2/F3: the boot-arg parse: absent / 0 = off, 1 = on (both flags), 2 = ignored loudly; an ACL packet on rx names sweetbtnopcf */
	sbu_nopcf_on = sbu_nopcf_rx = false; loglen = 0; logbuf[0] = 0;
	knob_nopcf = -1; sbu_nopcf_parse(); CHECK(!sbu_nopcf_on && !sbu_nopcf_rx && loglen == 0);
	knob_nopcf = 0; sbu_nopcf_parse(); CHECK(!sbu_nopcf_on && !sbu_nopcf_rx && loglen == 0);
	knob_nopcf = 2; sbu_nopcf_parse(); CHECK(!sbu_nopcf_on && !sbu_nopcf_rx); CHECK(has("BT GAP: sweetbtnopcf=2 ignored: only 1 is defined"));
	knob_nopcf = 1; sbu_nopcf_parse(); CHECK(sbu_nopcf_on && sbu_nopcf_rx); CHECK(has("BT GAP: sweetbtnopcf=1 active"));
	knob_nopcf = -1;
	reset(1, 0); sbu_nopadv_on = false;
	{ static const uint8_t acl[] = { 0x02, 0x01, 0x20, 0x01, 0x00, 0xaa }; push(acl, sizeof(acl)); }
	(void)sbu_rx_poll(); CHECK(has("BT GAP: sweetbtnopcf: rx framer lost at non-event packet type 0x2"));

	/* 14. patch 0232 (T-0936): the bt-up17 sequence [obs 20261107T155645Z usbnet :6153-6160]: passive 0x200b, scan on/off, then the ACTIVE 0x200b + scan on */
	{
		static const uint8_t c_pas[] = { 0x01, 0x0b, 0x20, 0x07, 0x00, 0x40, 0x00, 0x30, 0x00, 0x01, 0x00 };
		static const uint8_t c_act[] = { 0x01, 0x0b, 0x20, 0x07, 0x01, 0x40, 0x00, 0x30, 0x00, 0x01, 0x00 };
		static const uint8_t w_act[] = { 0x01, 0x0b, 0x20, 0x07, 0x01, 0x40, 0x00, 0x30, 0x00, 0x00, 0x00 };
		static const uint8_t ok_on[] = { 0x04, 0x0e, 0x04, 0x01, 0x0c, 0x20, 0x00 };
		/* 14a. =1 (bt-up17 kernel): active passed unchanged, the fake chip answers 0x12 as bt-up17 did */
		reset(1, 1); sbu_nopadv_on = false;
		CHECK(sbu_tx_kctl(c_pas, 11) == 0); (void)sbu_rx_poll(); CHECK(sbu_tx_kctl(c_on, 6) == 0); (void)sbu_rx_poll(); CHECK(scan_status == 0x00);
		CHECK(sbu_tx_kctl(c_off, 6) == 0); (void)sbu_rx_poll();
		size_t c0 = nchip; CHECK(sbu_tx_kctl(c_act, 11) == 0); (void)sbu_rx_poll(); CHECK(nchip - c0 == 11 && memcmp(chip + c0, c_act, 11) == 0);
		CHECK(sbu_tx_kctl(c_on, 6) == 0); (void)sbu_rx_poll(); CHECK(scan_status == 0x12);
		CHECK(has("sweetbtpubscan: 0x200b LE_Scan_Type 0x1 is not passive: nothing rewritten")); CHECK(!has("ACTIVE 0x200b")); CHECK(sbu_pubscan_n == 1);
		/* 14b. =2: the active 0x200b reaches the chip byte-exact with only octet 9 (Own_Address_Type) 0x01 -> 0x00, scan enable answers 0x00, bluetoothd gets that 0x00 */
		reset(1, 2); sbu_nopadv_on = false;
		CHECK(sbu_tx_kctl(c_pas, 11) == 0); (void)sbu_rx_poll(); CHECK(sbu_tx_kctl(c_on, 6) == 0); (void)sbu_rx_poll(); CHECK(scan_status == 0x00);
		CHECK(sbu_tx_kctl(c_off, 6) == 0); (void)sbu_rx_poll();
		c0 = nchip; CHECK(sbu_tx_kctl(c_act, 11) == 0); (void)sbu_rx_poll(); CHECK(nchip - c0 == 11 && memcmp(chip + c0, w_act, 11) == 0);
		size_t a0 = napp; CHECK(sbu_tx_kctl(c_on, 6) == 0); (void)sbu_rx_poll(); CHECK(scan_status == 0x00);
		CHECK(napp - a0 == sizeof(ok_on) && memcmp(app + a0, ok_on, sizeof(ok_on)) == 0);
		CHECK(!has("is not passive")); CHECK(sbu_pubscan_n == 2); CHECK(sbu_pubscan_nact == 1);
		CHECK(count_str("BT GAP: sweetbtpubscan=2 rewrote Own_Address_Type of bluetoothd's 0x200b") == 1);
		CHECK(has("BT GAP: sweetbtpubscan=2 rewrote Own_Address_Type of bluetoothd's ACTIVE 0x200b from 0x01 (random, no 0x2005 sent) to 0x00 (public) on the way to the chip (x2, active x1)"));
		/* 14c. =2, LE_Scan_Type 0x02 (reserved, s.7.8.10) is still passed and logged; one byte per write keeps the active rewrite */
		reset(1, 2); sbu_nopadv_on = false;
		{ uint8_t r[11]; memcpy(r, c_act, 11); r[4] = 0x02; c0 = nchip; CHECK(sbu_tx_kctl(r, 11) == 0); CHECK(chip[c0 + 9] == 0x01); CHECK(has("LE_Scan_Type 0x2 is not passive")); }
		(void)sbu_rx_poll();
		c0 = nchip; for (int i = 0; i < 11; i++) CHECK(sbu_tx_kctl(c_act + i, 1) == 0);
		CHECK(nchip - c0 == 11 && memcmp(chip + c0, w_act, 11) == 0); CHECK(sbu_pubscan_nact == 1);
		/* 14d. passive + active in ONE write: two rewrites, one ACTIVE row */
		reset(1, 2); sbu_nopadv_on = false;
		{ uint8_t two[22]; memcpy(two, c_pas, 11); memcpy(two + 11, c_act, 11); c0 = nchip; CHECK(sbu_tx_kctl(two, 22) == 0);
		  CHECK(chip[c0 + 9] == 0x00 && chip[c0 + 20] == 0x00); CHECK(sbu_pubscan_n == 2 && sbu_pubscan_nact == 1); CHECK(count_str("ACTIVE 0x200b") == 1); }
	}
	/* 15. patch 0232: sweetbtpubscan parse: absent / 0 = off silent, 1 = passive only, 2 = passive + active, 3 = ignored loudly */
	{
		struct { int v; bool on, act; const char *row; } pc[] = {
			{ -1, false, false, NULL }, { 0, false, false, NULL },
			{ 1, true, false, "BT GAP: sweetbtpubscan=1 active: Own_Address_Type 0x01 (random) in bluetoothd's passive 0x200b" },
			{ 2, true, true, "BT GAP: sweetbtpubscan=2 active: Own_Address_Type 0x01 (random) in bluetoothd's passive AND active 0x200b" },
			{ 3, false, false, "BT GAP: sweetbtpubscan=3 ignored: only 1 (passive scans) and 2 (passive + active scans) exist" },
		};
		for (size_t i = 0; i < sizeof(pc) / sizeof(pc[0]); i++) {
			sbu_pubscan_on = sbu_pubscan_active = false; loglen = 0; logbuf[0] = 0; knob_pubscan = pc[i].v;
			sbu_pubscan_parse();
			CHECK(sbu_pubscan_on == pc[i].on); CHECK(sbu_pubscan_active == pc[i].act);
			if (pc[i].row) CHECK(has(pc[i].row)); else CHECK(loglen == 0);
		}
		knob_pubscan = -1;
	}

	printf("RESULT: %s nopcf-host\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
