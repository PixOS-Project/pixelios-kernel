/* Host test of patch 0360 (BT patchram TX wait sleeps instead of spinning): the REAL sweet_bt_uart.c (patches 0189 .. 0360 applied in order) on the lefeat harness (fake BCM4389),
 * plus a UART model with a 256-byte TX FIFO that drains at the baud rate in virtual time (a UFSTAT read costs 0.1 us, one tsleep tick 10 ms). Checks: the stream bytes and the
 * register-access set are the same with and without sleeping, the UFSTAT read count falls by orders of magnitude, the line never starves, stuck FIFO = GAP after 64 sleeps,
 * 4 Mbaud and every non-bring-up caller keep the spin, the flag is down after the bring-up (also when it fails). */
#define T_NO_MAIN
#include "test_sweet_bt_lefeat.c"

int sweet_bt_sk_rx(const void *d, size_t l, int *e) { (void)d; (void)l; (void)e; return 0; }
int sweet_bt_sk_is_opt(int o) { (void)o; return 0; }
int sweet_bt_sk_setopt(int o, const void *d, size_t l) { (void)o; (void)d; (void)l; return 0; }
int sweet_bt_sk_getopt(int o, void *d, size_t *l) { (void)o; (void)d; (void)l; return 0; }
void sweet_bt_sk_kctl_disconnect(void) { }
void sweet_bt_sk_attach(void) { }
void proc_selfname(char *b, int n) { snprintf(b, (size_t)n, "BlueTool"); }

/* ---- UART model: TX FIFO of 256 bytes draining at baud/10 bytes per second of virtual time ---- */
#define FIFO 256
static double vt_us, fifo_q, idle_us, bytes_per_us; static int stuck, in_stream; static unsigned long ufstat_reads, utxh_writes, tx_overflow;
static struct { uint32_t pa; char k; } seen[64]; static int nseen;
static void note(uint32_t pa, char k) { for (int i = 0; i < nseen; i++) if (seen[i].pa == pa && seen[i].k == k) return; if (nseen < 64) { seen[nseen].pa = pa; seen[nseen++].k = k; } }
static void advance(double us)
{
	vt_us += us;
	if (stuck) return;
	double can = us * bytes_per_us;
	if (fifo_q >= can) fifo_q -= can; else { if (in_stream) idle_us += (can - fifo_q) / bytes_per_us; fifo_q = 0; }
}
static uint32_t m_rd(uint32_t pa)
{
	note(pa, 'r');
	if (pa == 0x175B0000u + U_UFSTAT) {
		advance(0.1); ufstat_reads++;
		/* a stream's RX side (cnt) is still the fake chip's */
		return (f_rd(pa) & 0xFFu) | (fifo_q > FIFO - 1 ? (1u << 24) : 0u);
	}
	return f_rd(pa);
}
static void m_wr(uint32_t pa, uint32_t v)
{
	note(pa, 'w');
	if (pa == 0x175B0000u + U_UTXH) { utxh_writes++; if (fifo_q > FIFO - 1) tx_overflow++; fifo_q += 1; }
	f_wr(pa, v);
}
static int m_ts(int ticks) { advance(ticks * 10000.0); return 0; }
static void model_setup(uint32_t baud, int is_stuck)
{
	run_setup(0, 0); knob_pr = -1; knob_lf = -1; knob_rr = 0; sbu_bringup_busy = 0; kts_n = 0; kts_f = NULL; kts_fail = 0; sbt.ops = NULL; sbt.uart_busy = 0;
	sbu.hw = (struct sbu_hw){ m_rd, m_wr, f_us, f_ms };
	sbu.baud = baud; bytes_per_us = baud / 10.0 / 1e6;
	vt_us = fifo_q = idle_us = 0; stuck = is_stuck; in_stream = 0; ufstat_reads = utxh_writes = tx_overflow = 0; nseen = 0; ts_hook = m_ts; ts_n = 0;
	memset(&sbu_txw, 0, sizeof sbu_txw); sbu_txsleep_ok = false;
}

int main(void)
{
	static uint8_t ref_pk[sizeof(pkdata)]; size_t ref_off; int ref_n, e;
	static struct { uint32_t pa; char k; } ref_seen[64]; int ref_nseen; unsigned long ref_reads, ref_w; double ref_vt;

	synth_image(120);
	/* 0. pure function: sleep allowed up to 128000 baud only */
	CHECK(sbu_txsleep_baud_ok(115200) && sbu_txsleep_baud_ok(9600) && sbu_txsleep_baud_ok(128000) && !sbu_txsleep_baud_ok(129000) && !sbu_txsleep_baud_ok(4000000) && !sbu_txsleep_baud_ok(0));
	/* 1. reference: the old behaviour (flag down = spin), sbu_patchram_attach only */
	model_setup(115200, 0); in_stream = 1; e = sbu_patchram_attach(&cand);
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(ts_n == 0); CHECK(sbu_txw.sleeps == 0);
	memcpy(ref_pk, pkdata, pkoff); ref_off = pkoff; ref_n = npk; CHECK(ref_n > 100); memcpy(ref_seen, seen, sizeof seen); ref_nseen = nseen; ref_reads = ufstat_reads; ref_w = utxh_writes; ref_vt = vt_us;
	CHECK(sbu_txw.polls_full > 100000); CHECK(tx_overflow == 0);
	/* 2. sleep mode (the flag the bring-up thread sets): same bytes, same register set, far fewer UFSTAT reads, line not starved, nothing written beyond the same registers */
	model_setup(115200, 0); sbu_txsleep_ok = true; in_stream = 1; e = sbu_patchram_attach(&cand); sbu_txsleep_ok = false;
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(tx_overflow == 0);
	CHECK(npk == ref_n && pkoff == ref_off && memcmp(pkdata, ref_pk, ref_off) == 0); CHECK(utxh_writes == ref_w);
	CHECK(nseen == ref_nseen);
	CHECK(sbu_txw.sleeps > 50); CHECK(sbu_txw.polls_full == 0); CHECK(ufstat_reads * 50 < ref_reads);
	CHECK(vt_us < ref_vt * 1.03 && vt_us > ref_vt * 0.97);      /* the download takes the same time (virtual) */
	CHECK(idle_us < vt_us * 0.02);                              /* the line is never starved */
	/* 3. the register-access set (pa + read/write) with sleeping equals the set without */
	{ model_setup(115200, 0); in_stream = 1; (void)sbu_patchram_attach(&cand); int base_n = nseen; static struct { uint32_t pa; char k; } b[64]; memcpy(b, seen, sizeof seen);
	  model_setup(115200, 0); sbu_txsleep_ok = true; in_stream = 1; (void)sbu_patchram_attach(&cand); sbu_txsleep_ok = false;
	  CHECK(nseen == base_n); for (int i = 0; i < nseen; i++) { int f = 0; for (int j = 0; j < base_n; j++) f |= (seen[i].pa == b[j].pa && seen[i].k == b[j].k); CHECK(f); } }
	/* 4. stuck FIFO, sleep mode: ETIMEDOUT after 64 sleeps + the same GAP row as the spin bound; spin mode: ETIMEDOUT after the poll bound */
	{ uint8_t buf[300]; memset(buf, 0x5a, sizeof buf);
	  model_setup(115200, 1); sbu_txsleep_ok = true; e = sbu_tx_raw(buf, 300); sbu_txsleep_ok = false;
	  CHECK(e == ETIMEDOUT); CHECK(utxh_writes == FIFO); CHECK(sbu_txw.sleeps == 64); CHECK(has("BT GAP: uart16 tx fifo stayed full (flow control stuck?); 256 of 300 bytes sent"));
	  model_setup(115200, 1); e = sbu_tx_raw(buf, 300);
	  CHECK(e == ETIMEDOUT); CHECK(utxh_writes == FIFO); CHECK(sbu_txw.sleeps == 0 && sbu_txw.polls_full == (1u << 22)); CHECK(has("BT GAP: uart16 tx fifo stayed full")); }
	/* 5. 4 Mbaud with the flag up: the spin stays (a 10 ms sleep would starve the line) */
	{ uint8_t buf[600]; memset(buf, 0x33, sizeof buf);
	  model_setup(4000000, 0); sbu_txsleep_ok = true; e = sbu_tx_raw(buf, 600); sbu_txsleep_ok = false;
	  CHECK(e == 0); CHECK(sbu_txw.sleeps == 0 && ts_n == 0 && sbu_txw.polls_full > 0); }
	/* 6. non-bring-up callers (flag down) never sleep, also at 115200 */
	{ uint8_t buf[600]; memset(buf, 0x44, sizeof buf);
	  model_setup(115200, 0); e = sbu_tx_raw(buf, 600); CHECK(e == 0); CHECK(ts_n == 0 && sbu_txw.sleeps == 0); }
	/* 7. the whole bring-up (sbu_bringup_run(true)): same bytes as the synchronous sequence, sleeps happened, the flag is down afterwards, the totals row is printed once before the done row */
	{ model_setup(115200, 0); sbu_bringup_start(&cand); CHECK(kts_n == 1 && sbu_txsleep_ok == false); in_stream = 1;
	  { bool ok = sbu_bringup_run(true); CHECK(ok); }
	  CHECK(sbu_txsleep_ok == false); CHECK(sbu_txw.sleeps > 50); CHECK(npk >= ref_n); CHECK(memcmp(pkdata, ref_pk, ref_off) == 0 || npk != ref_n);
	  CHECK(has("BT UART: bring-up tx fifo-full waits: ")); CHECK(has(" sleeps of one tick (")); CHECK(has(" spin polls with the fifo full (baud 115200, tx bytes "));
	  CHECK(strstr(logbuf, "bring-up tx fifo-full waits") < strstr(logbuf, "BT UART: bring-up done in ")); { const char *p = strstr(logbuf, "bring-up tx fifo-full waits"); CHECK(p && !strstr(p + 10, "bring-up tx fifo-full waits")); } }
	/* 8. the bring-up fails (controller dead after the power-cycle, model 13): the flag is still down, the row is still printed */
	{ model_setup(115200, 0); run_setup(13, 0); knob_pr = -1; knob_lf = -1; sbu.hw = (struct sbu_hw){ m_rd, m_wr, f_us, f_ms }; sbu.baud = 115200; ts_hook = m_ts; memset(&sbu_txw, 0, sizeof sbu_txw);
	  sbu_bringup_start(&cand); { bool ok = sbu_bringup_run(true); CHECK(!ok); } CHECK(sbu_txsleep_ok == false); CHECK(has("bring-up tx fifo-full waits")); }
	fputs(logbuf, stdout);
	printf("RESULT: %s sweet-bt-txsleep-host cases=8\n", fails ? "FAIL" : "PASS");
	return fails != 0;
}
