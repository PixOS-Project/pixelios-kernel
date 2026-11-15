/* Host test of the RF witness (patches 0229 + 0230, T-b0b4 / T-4ee1, W-BTSCAN): the REAL sweet_bt_uart.c (patches 0189..0230), a fake register file and a fake BCM4389 that
 * answers the witness commands, sends LE Advertising Reports while the scan is on and Inquiry Result / Inquiry Complete after the inquiry length (model 0), stays silent
 * (model 1), refuses 0x200b (model 2), reports only when window == interval (3), sends 0x02 / 0x2f results, a stray byte, an rx error and Inquiry Complete status 0x0c (4),
 * floods reports during the scan-off and cuts an event at the window end (5), never answers a scan-off (6) or a scan-on (7) or an Inquiry Cancel (8). No phone, no network. */
#include "kstub.h"
#include "sweet_bt.c"
#include "sweet_bt_uart.c"
#undef printf
static char logbuf[1 << 19]; static size_t loglen;
int sbt_printf(const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap); va_end(ap); if (n > 0) loglen += (size_t)n; return n; }
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r;(void)u;(void)d;(void)l;(void)f; return 0; }
void mbuf_freem(mbuf_t m) { (void)m; }
int tsleep(void *c, int p, const char *w, int t) { (void)c;(void)p;(void)w;(void)t; return 0; }
void delay(int us) { (void)us; }
bool sweet_usb_board_is_gs101(void) { return true; }
static int knob_rf;
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l; if (strcmp(n, "sweetbtrfwit") == 0 && knob_rf) { *(int *)v = 1; return 1; } return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
void thread_deallocate(void *t) { (void)t; }
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { (void)r; *ref = (kern_ctl_ref)1; return 0; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 1; }

#define CLK 196646400u
static uint8_t rxq[1 << 16]; static int rxh, rxt;
static uint8_t txb[300]; static int ntx;
static uint16_t ops[64]; static int nops;
static int model, scan_on, inq_due, inq_sent, win_eq, err_once; static unsigned long ms_total, scan_t0; static unsigned n_uerstat, n_urxh;
static void push(const uint8_t *b, int n) { memcpy(rxq + rxt, b, n); rxt += n; }
static void cc(uint16_t op, const uint8_t *rp, int n, uint8_t st)
{
	uint8_t e[80]; int k = 0;
	e[k++] = 0x04; e[k++] = 0x0e; e[k++] = 0; e[k++] = 0x01; e[k++] = op & 0xff; e[k++] = op >> 8; e[k++] = st;
	for (int i = 0; i < n; i++) e[k++] = rp[i];
	e[2] = (uint8_t)(k - 3); push(e, k);
}
/* LE Advertising Report, one report: evtype 0 addrtype 0 addr c1..c6, 3 data bytes, rssi -60 */
static const uint8_t adv[] = { 0x04, 0x3e, 0x0f, 0x02, 0x01, 0x00, 0x00, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0x03, 0x02, 0x01, 0x06, 0xc4 };
/* Inquiry Result with RSSI, one response, rssi -70; Inquiry Complete status 0 */
static const uint8_t inqr[] = { 0x04, 0x22, 0x0f, 0x01, 0xd1, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0x01, 0x00, 0x0c, 0x02, 0x5a, 0x00, 0x00, 0xba };
static const uint8_t inqc[] = { 0x04, 0x01, 0x01, 0x00 };
static const uint8_t inqc12[] = { 0x04, 0x01, 0x01, 0x0c };
/* Inquiry Result (0x02, s.7.7.2) one response; Extended Inquiry Result (0x2f, s.7.7.38) one response with RSSI 0xb0 = -80 at parameter offset 14 and 0x00 at offset 13 */
static const uint8_t inqr02[] = { 0x04, 0x02, 0x0f, 0x01, 0xe1, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0x01, 0x00, 0x00, 0x0c, 0x02, 0x5a, 0x00, 0x00 };
static void push_eir(void)
{
	uint8_t e[258]; memset(e, 0, sizeof(e));
	e[0] = 0x04; e[1] = 0x2f; e[2] = 255; e[3] = 1; for (int i = 0; i < 6; i++) e[4 + i] = 0xe1 + i;
	e[3 + 7] = 1; e[3 + 13] = 0x00; e[3 + 14] = 0xb0; push(e, sizeof(e));
}
static int silent(void) { return model == 1 || model == 8; }
static void answer(void)
{
	uint16_t op = txb[1] | (txb[2] << 8);
	if (nops < 64) ops[nops++] = op;
	switch (op) {
	case 0xfc79: { uint8_t r[6] = { 0xaa, 0xfe, 0x0f, 0x27, 0xcb, 0x00 }; cc(op, r, model == 9 ? 2 : 6, 0); break; }
	case 0x200b: win_eq = txb[5] == txb[7] && txb[6] == txb[8]; cc(op, NULL, 0, model == 2 ? 0x12 : 0); break;
	case 0x200c:
		scan_on = txb[4]; scan_t0 = ms_total;
		if (model == 6 && txb[4] == 0) break;                  /* scan-off never answered */
		if (model == 7 && txb[4] == 1) break;                  /* scan-on never answered */
		if (model == 5 && txb[4] == 0) { push(adv, sizeof(adv)); push(adv, sizeof(adv)); }      /* reports keep arriving ahead of the scan-off answer */
		cc(op, NULL, 0, 0); break;      /* reports come later than the Command Complete (s.7.8.11: scanning starts after it) */
	case 0x0401: { uint8_t cs[] = { 0x04, 0x0f, 0x04, 0x00, 0x01, 0x01, 0x04 }; push(cs, sizeof(cs)); inq_due = silent() ? 0 : (int)(ms_total + 10240); inq_sent = 0; break; }
	case 0x0402: if (model == 8) break; cc(op, NULL, 0, 0); break;
	default: cc(op, NULL, 0, 0); break;
	}
}
static uint32_t f_rd(uint32_t pa)
{
	int cnt = rxt - rxh;
	if (pa == 0x175B0000u + U_UERSTAT) { n_uerstat++; if (err_once == 1) { err_once = 2; return 1; } return 0; }
	if (pa == 0x175B0000u + U_UFSTAT) return cnt > 255 ? 255 : cnt;
	if (pa == 0x175B0000u + U_UTRSTAT) return 4u | (cnt > 0);
	if (pa == 0x175B0000u + U_URXH) { n_urxh++; return cnt > 0 ? rxq[rxh++] : 0; }
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	if (pa == 0x175B0000u + U_UTXH) {
		txb[ntx++] = (uint8_t)v;
		if (ntx >= 4 && ntx == 4 + txb[3]) { answer(); ntx = 0; }
	}
}
static void f_us(unsigned us) { (void)us; }
static void f_ms(unsigned ms)
{
	ms_total += ms;
	unsigned long dt = ms_total - scan_t0;
	int rep = model == 0 || model == 5 || (model == 3 && win_eq);
	if (rep && scan_on && (dt == 1 || (model != 5 && dt == 5000))) push(adv, sizeof(adv));
	if (model == 5 && scan_on && dt == 20000) push(adv, 9);                         /* an event cut in the middle at the window end ... */
	if (model == 5 && scan_on && dt == 20001) push(adv + 9, sizeof(adv) - 9);       /* ... finished 1 ms later */
	if ((model == 0 || model == 3 || model == 5) && inq_due && !inq_sent && ms_total == (unsigned long)inq_due - 5000) push(inqr, sizeof(inqr));
	if (model == 4 && inq_due && !inq_sent && ms_total == (unsigned long)inq_due - 5000) { uint8_t z = 0x00; push(&z, 1); err_once = 1; push(inqr02, sizeof(inqr02)); push_eir(); }
	if (model == 4 && inq_due && !inq_sent && ms_total >= (unsigned long)inq_due) { push(inqc12, sizeof(inqc12)); inq_sent = 1; }
	if (!silent() && model != 4 && inq_due && !inq_sent && ms_total >= (unsigned long)inq_due) { push(inqc, sizeof(inqc)); inq_sent = 1; }
}
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static int fails;
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static int count_str(const char *s) { int c = 0; for (const char *p = logbuf; (p = strstr(p, s)) != NULL; p += strlen(s)) c++; return c; }
static int count_op(uint16_t op) { int c = 0; for (int i = 0; i < nops; i++) if (ops[i] == op) c++; return c; }
static void run(int m)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; sbu.clk_hz = CLK; sbu.baud = 115200; sbu.rate = CLK;
	rxh = rxt = ntx = 0; nops = 0; win_eq = 0; err_once = 0; scan_t0 = 0; model = m; scan_on = 0; inq_due = 0; inq_sent = 0; ms_total = 0; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0;
	sbu_rfwit();
}
int main(void)
{
	/* 0. reports during every scan window, inquiry result + complete: every count, the order of the commands, no address in the log */
	run(0);
	CHECK(nops == 13);
	CHECK(ops[0] == 0xfc79 && ops[1] == 0x0c01 && ops[2] == 0x2001 && ops[3] == 0x200b && ops[4] == 0x200c && ops[5] == 0x200c && ops[6] == 0x200b && ops[7] == 0x200c && ops[8] == 0x200c &&
	      ops[9] == 0x200b && ops[10] == 0x200c && ops[11] == 0x200c && ops[12] == 0x0401);       /* 0230: W1, W1b, W1c, then W2 */
	CHECK(count_op(0x0402) == 0); CHECK(count_op(0x0c03) == 0);
	CHECK(has("BT RFWIT: start (sweetbtrfwit=1, patch 0229)")); CHECK(has("BT RFWIT: controller firmware build 203 (patched)")); CHECK(has("BT RFWIT: patch 0230 (T-4ee1)"));
	CHECK(has("BT RFWIT: step le-scan-params-100pct op=0x200b status=0x00"));
	CHECK(has("BT RFWIT: W1 LE scan 100% duty 20000 ms: events=2 adv_report_events=2 adv_reports=2 le_other=0 other=0 stray=0 rxerr=0 rssi_max=-60\n"));
	CHECK(has("BT RFWIT: W1b LE scan bluetoothd params (interval 0x01e0 window 0x0030 dup 01) 20000 ms: events=2 adv_report_events=2 adv_reports=2 le_other=0 other=0 stray=0 rxerr=0 rssi_max=-60\n"));
	CHECK(has("BT RFWIT: W1c LE scan window 0x0030 dup 00 (interval 0x01e0) 20000 ms: events=2 adv_report_events=2 adv_reports=2 le_other=0 other=0 stray=0 rxerr=0 rssi_max=-60\n"));
	CHECK(has("BT RFWIT: le-scan t=1 ms event 0x3e plen 15 sub 0x02 rssi -60 dBm (address not logged)"));
	CHECK(has("BT RFWIT: W2 inquiry accepted=1: events=2 results=1 complete=1 complete_status=0 other=0 stray=0 rxerr=0 rssi_max=-70\n"));
	CHECK(has("BT RFWIT: done build=203 controller_events=8 adv_reports=2 inquiry_results=1 inquiry_complete=1 rf_witness=yes async_witness=yes w1b_adv_reports=2 w1c_adv_reports=2\n"));
	CHECK(!has("c1 c2") && !has("C1") && !has("d1 d2") && !has("c6") && !has("d6"));      /* rule 4: no address bytes */
	CHECK(has("BT HCI: tx 11 bytes: 01 0b 20 07 00 e0 01 e0 01 00 00")); CHECK(has("BT HCI: tx 9 bytes: 01 01 04 05 33 8b 9e 08 00"));
	CHECK(has("BT HCI: tx 11 bytes: 01 0b 20 07 00 e0 01 30 00 00 00"));          /* bluetoothd's 0x200b [obs bt-up13] */
	CHECK(has("BT HCI: tx 6 bytes: 01 0c 20 02 01 01")); CHECK(has("BT HCI: tx 6 bytes: 01 0c 20 02 00 01"));       /* W1b: on / off with the duplicate filter byte */
	CHECK(!has("BT GAP:")); CHECK(ms_total < 3 * 20000 + 13000 + 100); CHECK(ms_total < 3 * 20000 + 10240 + 600);     /* inquiry stops at Inquiry Complete */

	/* 1. silent chip: commands answered, no event; loud missing Inquiry Complete, Inquiry Cancel sent */
	run(1);
	CHECK(has("events=0 adv_report_events=0 adv_reports=0")); CHECK(has("rssi_max=0 (none)"));
	CHECK(has("BT GAP: sweetbtrfwit: no Inquiry Complete within 13000 ms of an accepted 0x0401")); CHECK(count_op(0x0402) == 1); CHECK(count_op(0x0c03) == 0);
	CHECK(has("BT RFWIT: done build=203 controller_events=0 adv_reports=0 inquiry_results=0 inquiry_complete=0 rf_witness=no async_witness=no w1b_adv_reports=0 w1c_adv_reports=0"));

	/* 2. 0x200b refused: no scan enable, loud, the inquiry still runs (F3: the text says "step failed") */
	run(2);
	CHECK(has("BT GAP: sweetbtrfwit step le-scan-params-100pct op=0x200b: status 0x12; step failed")); CHECK(!has("witness stopped")); CHECK(count_op(0x200c) == 0); CHECK(count_op(0x0401) == 1);
	CHECK(count_op(0x200b) == 3); CHECK(count_op(0x0c03) == 0);
	CHECK(has("inquiry_complete=1"));

	/* 3. the unused knob is loud only when set */
	loglen = 0; logbuf[0] = 0; knob_rf = 0; sbu_rfwit_unused("why-x"); CHECK(!has("why-x"));
	knob_rf = 1; sbu_rfwit_unused("why-x"); CHECK(has("BT GAP: sweetbtrfwit=1 ignored: why-x"));

	/* 4. a chip that reports only when window == interval (the bt-up13 hypothesis): W1 > 0, W1b == 0, W1c == 0, read from the rows */
	run(3);
	CHECK(has("BT RFWIT: W1 LE scan 100% duty 20000 ms: events=2 adv_report_events=2 adv_reports=2 "));
	CHECK(has("dup 01) 20000 ms: events=0 adv_report_events=0 adv_reports=0 ")); CHECK(has("dup 00 (interval 0x01e0) 20000 ms: events=0 adv_report_events=0 adv_reports=0 "));
	CHECK(has("rf_witness=yes async_witness=yes w1b_adv_reports=0 w1c_adv_reports=0")); CHECK(count_op(0x0c03) == 0);

	/* 5. F4: Inquiry Result 0x02 + Extended Inquiry Result 0x2f (RSSI 0xb0 = -80), a stray byte, an rx error flag once, Inquiry Complete status 0x0c, the wait ends at Complete */
	run(4);
	CHECK(has("BT RFWIT: W2 inquiry accepted=1: events=3 results=2 complete=1 complete_status=12 other=0 stray=1 rxerr=1 rssi_max=-80\n"));
	CHECK(has("BT GAP: sweetbtrfwit: dropped stray rx byte 0 during inquiry")); CHECK(has("BT GAP: uart16 rx error flags 0x1 during the RF witness (inquiry)"));
	CHECK(has("inquiry_results=2 inquiry_complete=1")); CHECK(ms_total < 3 * 20000 + 10240 + 600); CHECK(count_op(0x0402) == 0); CHECK(!has("e1 e2"));

	/* 6. F1: reports keep arriving ahead of every scan-off answer, an event is cut at the window end: the scan-off is still matched, the event is finished, no reset */
	run(5);
	CHECK(count_op(0x200c) == 6); CHECK(count_op(0x0c03) == 0); CHECK(count_op(0x0401) == 1);
	CHECK(has("BT PROBE: skipped event 0x3e len 15 while waiting for opcode 0x200c"));
	CHECK(has("BT RFWIT: W1 LE scan 100% duty 20000 ms: events=2 adv_report_events=2 adv_reports=2 le_other=0 other=0 stray=0 rxerr=0 rssi_max=-60\n"));
	CHECK(has("dup 01) 20000 ms: events=2 adv_report_events=2 adv_reports=2 ")); CHECK(has("dup 00 (interval 0x01e0) 20000 ms: events=2 adv_report_events=2 adv_reports=2 "));
	CHECK(!has("unfinished event left")); CHECK(!has("BT GAP:"));

	/* 7. F1: scan-off never answered: HCI Reset sent, W1b / W1c / W2 skipped, loud */
	run(6);
	CHECK(count_op(0x0c03) == 1); CHECK(count_op(0x200b) == 1); CHECK(count_op(0x0401) == 0); CHECK(count_op(0x200c) == 2);
	CHECK(has("BT GAP: sweetbtrfwit step le-scan-off op=0x200c: no answer (error")); CHECK(has("BT GAP: sweetbtrfwit: step le-scan-off failed, the controller may still scan or inquire: HCI Reset sent, the rest of the witness skipped"));
	CHECK(has("BT RFWIT: step reset-after-fail op=0x0c03 status=0x00")); CHECK(!has("W1b LE scan bluetoothd")); CHECK(!has("W1c LE scan window 0x0030 dup"));
	CHECK(has("BT RFWIT: W2 inquiry accepted=0: events=0")); CHECK(has("w1b_adv_reports=0 w1c_adv_reports=0"));

	/* 8. F1: scan-on never answered: the scan-off is still sent (any outcome), answered, so no reset; the later windows and W2 run */
	run(7);
	CHECK(count_op(0x200c) == 6); CHECK(count_op(0x0c03) == 0); CHECK(count_op(0x0401) == 1);
	CHECK(has("BT GAP: sweetbtrfwit step le-scan-on op=0x200c: no answer (error"));

	/* 9. F1: Inquiry Cancel never answered: HCI Reset sent */
	run(8);
	CHECK(count_op(0x0402) == 1); CHECK(count_op(0x0c03) == 1);
	CHECK(has("BT GAP: sweetbtrfwit step inquiry-cancel op=0x0402: no answer (error")); CHECK(has("BT GAP: sweetbtrfwit: step inquiry-cancel failed, the controller may still scan or inquire: HCI Reset sent"));

	/* 10. F3: a chip-id answer shorter than 6 octets is loud */
	run(9);
	CHECK(has("BT GAP: sweetbtrfwit: chip-id answer 2 octets < 6: build unknown")); CHECK(has("controller firmware build 0 (ROM or unknown)"));

	/* 11. heartbeat: off without the knob; 2 rows per 6000 passes; 40 rows + one cap row per 3000*41 passes; never UERSTAT / URXH */
	{
		unsigned e0, r0;

		sbu.uart_ok = true; sbu_hb_on = false; sbu_hb_polls = sbu_hb_rows = 0; loglen = 0; logbuf[0] = 0;
		for (int i = 0; i < 9000; i++) sbu_rx_heartbeat();
		CHECK(!has("BT RX:"));
		sbu_hb_on = true; sbu_hb_polls = sbu_hb_rows = 0; e0 = n_uerstat; r0 = n_urxh;
		for (int i = 0; i < 6000; i++) sbu_rx_heartbeat();
		CHECK(count_str("BT RX: heartbeat polls=") == 2); CHECK(has("BT RX: heartbeat polls=3000 rx_bytes=")); CHECK(has("BT RX: heartbeat polls=6000 rx_bytes="));
		CHECK(has(" umcon=0 umstat=0 ufstat=0 utrstat=0x4")); CHECK(!has("cap reached"));
		CHECK(n_uerstat == e0 && n_urxh == r0);
		loglen = 0; logbuf[0] = 0; sbu_hb_polls = sbu_hb_rows = 0;
		for (int i = 0; i < 3000 * 41; i++) sbu_rx_heartbeat();
		CHECK(count_str("BT RX: heartbeat polls=") == 40); CHECK(count_str("BT RX: heartbeat cap reached (40 rows)") == 1);
		for (int i = 0; i < 3000 * 5; i++) sbu_rx_heartbeat();
		CHECK(count_str("BT RX: heartbeat polls=") == 40); CHECK(count_str("BT RX: heartbeat cap reached (40 rows)") == 1);
		CHECK(n_uerstat == e0 && n_urxh == r0);
		sbu.uart_ok = false; loglen = 0; logbuf[0] = 0; sbu_hb_polls = sbu_hb_rows = 0;
		for (int i = 0; i < 3000; i++) sbu_rx_heartbeat();
		CHECK(!has("BT RX:"));
	}

	printf("RESULT: %s tb0b4-rfwit-host\n", fails ? "FAIL" : "PASS");
	return fails ? 1 : 0;
}
