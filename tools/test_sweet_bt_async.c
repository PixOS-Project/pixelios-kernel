/* Host test of patch 0296 (bt/defaults): the Bluetooth bring-up runs in a kernel thread. Built on the lefeat harness (tools/test_sweet_bt_lefeat.c with T_NO_MAIN: the REAL
 * sweet_bt_uart.c and a fake BCM4389) plus the REAL sweet_bt.c, both patched through 0296. The stub kernel_thread_start only records the entry, so "attach returns" is the point
 * where sbu_bringup_start returned and the thread body has not run; the test then runs the body (sbu_bringup_run) the way the thread would.
 * Covers: attach returns before one controller command was sent; kctl connect (plain and sk control) refused with EBUSY + bounded GAP while it runs; the command sequence is
 * byte-identical to the synchronous one (sbu_patchram_attach + sbu_rfwit_knob); done row; rx not before done; failure path; thread start failure falls back to synchronous. */
#define T_NO_MAIN
#include "test_sweet_bt_lefeat.c"

int sweet_bt_sk_rx(const void *d, size_t l, int *e) { (void)d; (void)l; (void)e; return 0; }
int sweet_bt_sk_is_opt(int o) { (void)o; return 0; }
int sweet_bt_sk_setopt(int o, const void *d, size_t l) { (void)o; (void)d; (void)l; return 0; }
int sweet_bt_sk_getopt(int o, void *d, size_t *l) { (void)o; (void)d; (void)l; return 0; }
void sweet_bt_sk_kctl_disconnect(void) { }
void sweet_bt_sk_attach(void) { }
void proc_selfname(char *b, int n) { snprintf(b, (size_t)n, "BlueTool"); }

static int count_str(const char *s) { int n = 0; for (const char *p = logbuf; (p = strstr(p, s)) != NULL; p += strlen(s)) n++; return n; }
static int connect_as(int idx, void **ui) { struct sockaddr_ctl sac = { .sc_id = 1, .sc_unit = 1 }; return sbt_connect(sbt.ref[idx], &sac, ui); }
static void async_setup(int m) { run_setup(m, 0); knob_pr = -1; knob_lf = -1; knob_rr = 0; sbu_bringup_busy = 0; kts_n = 0; kts_f = NULL; kts_fail = 0; sbt.ops = NULL; sbt.uart_busy = 0; sbt.busy[0] = sbt.busy[1] = 0; sbt.ref[0] = (kern_ctl_ref)1; sbt.ref[1] = (kern_ctl_ref)2; memset(sbt.gap_n, 0, sizeof sbt.gap_n); }

int main(void)
{
	static uint8_t ref_pk[sizeof(pkdata)]; size_t ref_off; int ref_n, e;
	void *ui = NULL;

	synth_image(40);
	/* 1. reference: the synchronous sequence exactly as attach ran it before 0296 (sbu_patchram_attach, then the rfwit knob) */
	async_setup(0); e = sbu_patchram_attach(&cand); sbu_rfwit_knob();
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(count_op(0xfd53) == 1 && count_op(0xfd3c) == 1);
	memcpy(ref_pk, pkdata, pkoff); ref_off = pkoff; ref_n = npk; CHECK(ref_n > 40 && fc4c_seen > 0);
	/* 2. async: start returns before one controller command was sent */
	async_setup(0);
	sbu_bringup_start(&cand);
	CHECK(kts_n == 1); CHECK(kts_f == (void *)sbu_bringup_thread); CHECK(npk == 0 && pkoff == 0); CHECK(sweet_bt_uart_bringup_busy() == 1);
	CHECK(!sbu.rx_thread); CHECK(sbt.ops == NULL); CHECK(!has("bring-up done")); CHECK(!has("patchram download verified")); CHECK(has("BT UART: bring-up (patchram, lefeat, VSC caps) runs in a kernel thread"));
	/* 3. connect during the bring-up: both controls refused with EBUSY, nothing claimed, the GAP row is bounded */
	CHECK(connect_as(0, &ui) == EBUSY); CHECK(connect_as(1, &ui) == EBUSY);
	CHECK(sbt.uart_busy == 0 && sbt.busy[0] == 0 && sbt.busy[1] == 0); CHECK(has("BT GAP: kctl ") && has("controller bring-up (patchram download) still running; connect refused with EBUSY"));
	for (int i = 0; i < 40; i++) CHECK(connect_as(i & 1, &ui) == EBUSY);
	CHECK(count_str("controller bring-up (patchram download) still running") <= 10); CHECK(count_str("controller bring-up (patchram download) still running") >= 1); CHECK(sbt.uart_busy == 0);
	/* 4. the thread body: same bytes in the same order as the synchronous sequence; ops and rx state final before the flag drops; done row */
	{ bool ok = sbu_bringup_run(true);
	CHECK(ok); CHECK(npk == ref_n && pkoff == ref_off && memcmp(pkdata, ref_pk, ref_off) == 0); }
	CHECK(sweet_bt_uart_bringup_busy() == 0); CHECK(sbt.ops == &sbu_ops); CHECK(sbu.rx_thread); CHECK(kts_n == 1);      /* inline rx: no second thread */
	CHECK(has("BT UART: patchram download verified")); CHECK(has("BT LEFEAT: done")); CHECK(has("BT UART: backend up"));
	CHECK(has("BT UART: bring-up done in ") && has(" ms (async)")); CHECK(!has("BT GAP: bring-up ended without a backend"));
	{ const char *d = strstr(logbuf, "BT UART: bring-up done in "); unsigned long long ms = d ? strtoull(d + strlen("BT UART: bring-up done in "), NULL, 10) : 0; CHECK(d != NULL && ms > 0); }
	CHECK(strstr(logbuf, "BT UART: backend up") < strstr(logbuf, "BT UART: bring-up done in "));
	/* 5. after the bring-up a client connects normally and a second one is refused by the UART owner rule (unchanged) */
	CHECK(connect_as(0, &ui) == 0); CHECK(sbt.busy[0] == 1); CHECK(connect_as(1, &ui) == EBUSY); CHECK(sbt_disconnect(sbt.ref[0], 1, ui) == 0); CHECK(sbt.uart_busy == 0);
	/* 6. failure path unchanged: the controller dies after the failed-download power-cycle -> patchram step fails, GAP rows, flag down, no backend, no ops */
	async_setup(13); sbu_bringup_start(&cand);
	CHECK(sweet_bt_uart_bringup_busy() == 1); CHECK(connect_as(0, &ui) == EBUSY);
	CHECK(!sbu_bringup_run(true)); CHECK(sweet_bt_uart_bringup_busy() == 0); CHECK(sbt.ops == NULL); CHECK(!sbu.rx_thread);
	CHECK(has("BT GAP: sweetbtpatchram=1 FAILED")); CHECK(has("BT GAP: bring-up ended without a backend after ")); CHECK(has("BT GAP: sweetbtrfwit (on) ignored: the backend did not start after the patchram step") || !has("sweetbtrfwit"));
	CHECK(has("BT UART: bring-up done in "));
	/* 7. refused patchram (empty image): bluetoothd meets the ROM, the backend still comes up, bytes: nothing sent */
	hcdimg_set(img, 0, "none"); async_setup(0); sbu_bringup_start(&cand); CHECK(npk == 0); CHECK(sbu_bringup_run(true)); CHECK(npk == 0); CHECK(has("BT GAP: sweetbtpatchram=1 had no effect")); CHECK(sbt.ops == &sbu_ops);
	synth_image(40);
	/* 8. kernel_thread_start fails: the bring-up runs synchronously, loudly, and the flag is down when start returns */
	async_setup(0); kts_fail = 1; sbu_bringup_start(&cand);
	CHECK(has("BT GAP: bring-up thread not started (kernel_thread_start failed)")); CHECK(sweet_bt_uart_bringup_busy() == 0); CHECK(npk == ref_n && pkoff == ref_off && memcmp(pkdata, ref_pk, ref_off) == 0); CHECK(sbt.ops == &sbu_ops);
	CHECK(connect_as(0, &ui) == 0); CHECK(sbt_disconnect(sbt.ref[0], 1, ui) == 0);
	fputs(logbuf, stdout);
	printf("RESULT: %s sweet-bt-async-host cases=8\n", fails ? "FAIL" : "PASS");
	return fails != 0;
}
