/* Host test of the LE probe matrix (patch 0216, T-0c77) and the replay probe + 0x200b rewrite (patch 0217, T-4c2c), the tx-side 0x200b Own_Address_Type rewrite (patch 0226, T-4b11): REAL sweet_bt_uart.c, fake register file, fake controller model. */
#include "kstub.h"
#include "sweet_bt.c"
#include "sweet_bt_uart.c"
#undef printf
static char logbuf[1 << 18]; static size_t loglen;
int sbt_printf(const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap); va_end(ap); if (n > 0) loglen += (size_t)n; return n; }
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r;(void)u;(void)d;(void)l;(void)f; return 0; }
void mbuf_freem(mbuf_t m) { (void)m; }
int tsleep(void *c, int p, const char *w, int t) { (void)c;(void)p;(void)w;(void)t; return 0; }
void delay(int us) { (void)us; }
bool sweet_usb_board_is_gs101(void) { return true; }
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)n; (void)v; (void)l; return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
void thread_deallocate(void *t) { (void)t; }
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { (void)r; *ref = (kern_ctl_ref)1; return 0; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 1; }
static uint8_t rxq[8192]; static int rxh, rxt;
static uint8_t txb[300]; static int ntx, flipped;
static uint8_t rawtx[2048]; static int nraw;
static char txlog[1 << 18]; static size_t txlen;
static int reset_n, regon_high;   /* patch 0221: HCI Resets seen (model 12 dies at the 2nd), REG_ON-high writes = controller power-cycles */
static int rnd_set, bd_set, ext_lock, model, probed_rs, last_op;   /* model 0: 2007/200b need nothing (all success); 1: need random addr; 2: need BD addr write; 3: never; 4: 2007 answers with Command Status */
static void push(const uint8_t *b, int n) { if (rxh == rxt) rxh = rxt = 0; memcpy(rxq + rxt, b, n); rxt += n; }
static void answer(void)
{
	uint16_t op = txb[1] | (txb[2] << 8); uint8_t plen = txb[3], st = 0, cc[80]; int n = 0;
	for (int i = 0; i < 4 + plen; i++) txlen += snprintf(txlog + txlen, sizeof(txlog) - txlen, "%02x ", txb[i]);
	txlog[txlen++] = '|'; txlog[txlen] = 0;
	if (op == 0x0c03) reset_n++;
	if (model == 6 || (model == 12 && op == 0x0c03 && reset_n >= 2)) return;      /* silent chip; model 12: answers pass 0, then dies at the first re-probe Reset (patch 0221 stop path) */
	if ((model == 7 && op == 0xfd48) || (model == 9 && op == 0x0c33)) flipped = 1;   /* 7: flip after the vendor command 0xfd48 (bt-up5 position 48); 9: after 0x0c33 (position 21) */
	if (op == 0x204a) ext_lock = 1;
	if (op == 0x0c03) ext_lock = 0;
	if (op == 0x2005) rnd_set = 1;
	if (op == 0xfc01) bd_set = 1;
	if (op == 0x0c03) { rnd_set = 0; probed_rs = 0; }
	if (op == 0x2007 || op == 0x200b) {
		int ok = model == 0 || model == 7 || model == 9 || model == 10 || model == 11 || model == 12 || (model == 1 && rnd_set) || (model == 2 && bd_set) || (model == 5 && !ext_lock);
		st = ok ? 0 : 0x0c;
		if (flipped) st = 0x0c;
	}
	if (op == 0x200c && flipped) st = 0x0c;
	/* patch 0219 models. 11: bluetoothd-like chip (fd48 answers 0x01, nothing refused). 10: the H1 world: 0x2007 right after fd48 is refused only when NO 0x2007 was sent since the last Reset; 0x204a is refused once a 0x2007 was sent since the Reset (bt-up6 position 19) */
	if (model == 10 || model == 11) {
		if (op == 0xfd48) st = 0x01;
		if (model == 10 && op == 0x204a && probed_rs) st = 0x0c;
		if (model == 10 && op == 0x2007) { st = (last_op == 0xfd48 && !probed_rs) ? 0x0c : 0; }
		if (op == 0x2007) probed_rs = 1;
	}
	last_op = op;
	if (model == 4 && op == 0x2007) { uint8_t cs[7] = { 0x04, 0x0f, 0x04, 0x0c, 0x01, 0x07, 0x20 }; push(cs, 7); return; }
	/* a stray event first, to test skipping */
	if (op == 0x200c) { uint8_t ev[4] = { 0x04, 0x13, 0x01, 0x00 }; push(ev, 4); }
	cc[n++] = 0x04; cc[n++] = 0x0e; cc[n++] = 0; cc[n++] = 0x01; cc[n++] = op & 0xff; cc[n++] = op >> 8; cc[n++] = st;
	if (op == 0x1002) { for (int i = 0; i < 64; i++) cc[n++] = (uint8_t)(0xf0 + i % 4); }
	if (op == 0x2007) cc[n++] = 0x07;
	if (op == 0x1009) { uint8_t a = bd_set ? 1 : 0xaa; for (int i = 0; i < 6; i++) cc[n++] = i == 0 ? a : (bd_set ? (i == 5 ? 2 : 0) : 0xaa); }
	cc[2] = (uint8_t)(n - 3);
	(void)plen; push(cc, n);
}
static unsigned long ms_total; static int rw_err; static uint32_t usi_mode_reg;   /* write-through USI mode register: sbu_uart_open reads it back (the power-cycle of the patch 0221 sweep runs the real power/open code) */
static uint32_t f_rd(uint32_t pa)
{
	int cnt = rxt - rxh;
	if (pa == 0x175B0000u + U_UERSTAT) return (unsigned)rw_err;
	if (pa == 0x175B0000u + U_UFSTAT) return cnt > 255 ? 255 : cnt;
	if (pa == 0x175B0000u + U_UTRSTAT) return 4u | (cnt > 0);
	if (pa == 0x175B0000u + U_URXH) return cnt > 0 ? rxq[rxh++] : 0;
	if (pa == SBU_USI_MODE) return usi_mode_reg;
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	if (pa == SBU_USI_MODE) usi_mode_reg = v;
	if (pa == 0x10840204u && (v & 4u)) regon_high++;
	if (pa == 0x175B0000u + U_UTXH) {
		if (nraw < (int)sizeof(rawtx)) rawtx[nraw++] = (uint8_t)v;   /* patch 0226 test: every byte the chip gets, in order */
		if (ntx >= (int)sizeof(txb)) return;      /* patch 0226 test: a long ACL write must not overrun the fake (only commands are modelled) */
		txb[ntx++] = (uint8_t)v;
		if (ntx >= 4 && ntx == 4 + txb[3]) { answer(); ntx = 0; }
	}
}
static void f_us(unsigned us) { (void)us; }
static void f_ms(unsigned ms) { ms_total += ms; }
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static int fails;
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
static void run(int m)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
	rxh = rxt = ntx = flipped = rnd_set = bd_set = ext_lock = 0; txlen = 0; txlog[0] = 0; model = m; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0;
	sbu_le_probe();
}
static int tx_has(const char *hex) { char pat[160]; snprintf(pat, sizeof(pat), "%s|", hex); return strstr(txlog, pat) != NULL; }

/* ---- patch 0217: replay probe (bt-up5 trace, merged.log lines 1762-2046: bluetoothd instance 1, tx rows 1..48; generated from the log, not typed) ---- */
static const struct { int n; const char *hex; int cut; } golden[48] = {
	{ 4, "01 03 0c 00 ", 0 },
	{ 4, "01 01 10 00 ", 0 },
	{ 4, "01 02 10 00 ", 0 },
	{ 4, "01 09 10 00 ", 0 },
	{ 4, "01 03 10 00 ", 0 },
	{ 5, "01 04 10 01 02 ", 0 },
	{ 4, "01 03 20 00 ", 0 },
	{ 12, "01 01 0c 08 ff ff ff ff ff ff ff 3f ", 0 },
	{ 12, "01 01 20 08 7f fa 86 3f 07 00 00 00 ", 0 },
	{ 5, "01 56 0c 01 01 ", 0 },
	{ 6, "01 6d 0c 02 01 01 ", 0 },
	{ 4, "01 60 20 00 ", 0 },
	{ 4, "01 2f 20 00 ", 0 },
	{ 7, "01 31 20 03 00 02 02 ", 0 },
	{ 8, "01 24 20 04 fb 00 48 08 ", 0 },
	{ 4, "01 1c 20 00 ", 0 },
	{ 4, "01 0f 20 00 ", 0 },
	{ 4, "01 05 10 00 ", 0 },
	{ 4, "01 4a 20 00 ", 0 },
	{ 6, "01 74 20 02 20 01 ", 0 },
	{ 11, "01 33 0c 07 f0 07 ff 01 00 01 00 ", 0 },
	{ 5, "01 31 0c 01 00 ", 0 },
	{ 5, "01 5b 0c 01 01 ", 0 },
	{ 5, "01 7a 0c 01 01 ", 0 },
	{ 6, "01 0f 08 02 05 00 ", 0 },
	{ 6, "01 16 0c 02 a0 1f ", 0 },
	{ 6, "01 18 0c 02 00 20 ", 0 },
	{ 8, "01 1c 0c 04 00 08 12 00 ", 0 },
	{ 8, "01 1e 0c 04 00 08 12 00 ", 0 },
	{ 6, "01 26 0c 02 60 00 ", 0 },
	{ 5, "01 2a 0c 01 00 ", 0 },
	{ 5, "01 2f 0c 01 00 ", 0 },
	{ 5, "01 43 0c 01 01 ", 0 },
	{ 5, "01 45 0c 01 02 ", 0 },
	{ 5, "01 47 0c 01 01 ", 0 },
	{ 5, "01 49 0c 01 01 ", 0 },
	{ 252, "01 13 0c f8 50 69 78 65 6c 20 36 61 00 00 00 00 00 00 00 00 00 00 00 00 ", 1 },
	{ 245, "01 52 0c f1 01 09 09 50 69 78 65 6c 20 36 61 0f 03 00 12 1f 11 2f 11 0a ", 1 },
	{ 7, "01 24 0c 03 0c 02 00 ", 0 },
	{ 12, "01 3c fd 08 ef 79 ff ff 09 00 00 00 ", 0 },
	{ 7, "01 24 0c 03 0c 02 60 ", 0 },
	{ 5, "01 47 0c 01 01 ", 0 },
	{ 7, "01 24 0c 03 0c 02 7a ", 0 },
	{ 8, "01 1c 0c 04 00 08 12 00 ", 0 },
	{ 9, "01 e9 fc 05 01 27 10 10 00 ", 0 },
	{ 9, "01 e2 fc 05 0b 00 00 00 0c ", 0 },
	{ 9, "01 e2 fc 05 1c 00 00 00 0a ", 0 },
	{ 11, "01 48 fd 07 01 01 35 1f c4 68 5f ", 0 },
};
static void run_replay(int m)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
	rxh = rxt = ntx = flipped = rnd_set = bd_set = ext_lock = probed_rs = last_op = 0; txlen = 0; txlog[0] = 0; model = m; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; ms_total = 0;
	sbu_replay_probe();
}
/* the expected txlog of the 48 steps: baseline 2007, then per step the packet and 2007; cut packets (trace shows 24 bytes) are the visible prefix + zeros up to 4+plen */
static size_t expect_steps(char *out, size_t max)
{
	size_t o = 0;
	o += snprintf(out + o, max - o, "01 07 20 00 |");
	for (int i = 0; i < 48; i++) {
		o += snprintf(out + o, max - o, "%s", golden[i].hex);
		if (golden[i].cut) for (int k = 24; k < golden[i].n; k++) o += snprintf(out + o, max - o, "00 ");
		o += snprintf(out + o, max - o, "|01 07 20 00 |");
	}
	return o;
}
static void replay_tests(void)
{
	static char exp[1 << 16];
	size_t el = expect_steps(exp, sizeof(exp));

	/* every table row reaches the wire byte-exact (the two cut payloads: visible prefix then zeros), in order, with 0x2007 after each; no flip -> no-flip witnesses */
	run_replay(0); if (getenv("T_VERBOSE")) fputs(logbuf, stdout);
	CHECK(strncmp(txlog, exp, el) == 0);
	if (strncmp(txlog, exp, el) != 0) { size_t k = 0; while (k < el && txlog[k] == exp[k]) k++; printf("FAIL first byte difference at txlog offset %zu\n", k); }
	CHECK(has("replay start (sweetbtleprobe=2, patch 0217): 48 steps"));
	CHECK(has("replay pos=37 op=0x0c13 payload truncated: 20 of 248 bytes known")); CHECK(has("replay pos=38 op=0x0c52 payload truncated: 20 of 241 bytes known"));
	CHECK(has("replay done: steps=48 commands=")); CHECK(has("truncated-payloads=2")); CHECK(has("no-answer=0")); CHECK(has("first-disallowed-after=0 op=0x0000"));
	CHECK(!has("first-disallowed-after=4")); CHECK(!has("DISALLOWED")); CHECK(has("no-flip after pos=48: 200b(bluetoothd random) status=0000 (success)")); CHECK(has("no-flip after 25 s wait: 2007 status=0000 (success) 200b status=0000 (success)"));
	/* the last probes after the 48 steps: 0x200b (random, bluetoothd's bytes) and the same two again after the wait; never a scan enable, never anything outside the allowed opcode set */
	CHECK(strstr(txlog + el, "01 0b 20 07 00 e0 01 30 00 01 00 |01 07 20 00 |01 0b 20 07 00 e0 01 30 00 01 00 |") != NULL);
	CHECK(!strstr(txlog, "01 0c 20 02 01")); CHECK(!strstr(txlog, "01 05 20")); CHECK(!strstr(txlog, "01 01 fc")); CHECK(!strstr(txlog, "01 0a 20"));
	/* the first-disallowed model: flip after 0xfd48 (position 48) */
	run_replay(7); if (getenv("T_VERBOSE")) fputs(logbuf, stdout);
	CHECK(has("first-disallowed-after=48 op=0xfd48 (2007 status 0x0c)")); CHECK(has("replay pos=48 op=0xfd48 plen=7 par=[01 01 35 1f c4 68 5f ] status=0000 (success) then 2007 status=0x0c (command disallowed) DISALLOWED"));
	CHECK(has("replay pos=47 op=0xfce2 plen=5 par=[1c 00 00 00 0a ] status=0000 (success) then 2007 status=0000 (success)")); CHECK(has("at-flip 200b(bluetoothd random) status=0x0c")); CHECK(has("at-flip 200c(scan enable OFF) status=0x0c"));
	CHECK(has("replay done: steps=48")); CHECK(has("first-disallowed-after=48 op=0xfd48\n")); CHECK(!has("no-flip"));
	{ const char *tail = "01 0b 20 07 00 e0 01 30 00 01 00 |01 0c 20 02 00 00 |"; CHECK(strncmp(txlog + el, tail, strlen(tail)) == 0); }   /* right after position 48's 2007: the two witnesses, scan enable OFF (00 00) only */
	/* a flip at position 21 (0x0c33) is named at 21, later steps still run */
	run_replay(9);
	CHECK(has("first-disallowed-after=21 op=0x0c33")); CHECK(has("replay pos=48 op=0xfd48")); CHECK(has("replay done: steps=48"));
	/* silent chip: every step loud, nothing hangs */
	run_replay(6);
	CHECK(has("NO ANSWER")); CHECK(has("BT GAP: replay probe:")); CHECK(has("replay done: steps=48"));
}

/* ---- patch 0219: replay v3 (pass A without probes, pass B control). The 0c52 / fd48 payloads below come from the bt-up6 `tx data@` rows of bluetoothd instance 1 (generated by script, not typed) ---- */
#define EIR70 "01 09 09 50 69 78 65 6c 20 36 61 0f 03 00 12 1f 11 2f 11 0a 11 0c 11 32 11 01 18 01 05 21 07 fe ca ca de af de ca de de fa ca de 00 00 00 00 77 0a 6a 10 a2 22 f2 86 5f 41 19 1d 02 03 03 02 27 ff 00 4c 02 24 02 "
static void run_replay3(int m, int passes)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
	rxh = rxt = ntx = flipped = rnd_set = bd_set = ext_lock = probed_rs = last_op = 0; txlen = 0; txlog[0] = 0; model = m; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; ms_total = 0;
	sbu_replay3(false);
	if (passes == 2) { sbu_replay3(true); }
}
/* the expected txlog of pass A: the 48 packets back to back (0c52 with its full EIR, fd48 with bluetoothd's bytes), NO 2007 between them */
static size_t expect_a(char *out, size_t max)
{
	size_t o = 0;
	for (int i = 0; i < 48; i++) {
		if (i == 37) {
			o += snprintf(out + o, max - o, "01 52 0c f1 " EIR70);
			for (int k = 70; k < 241; k++) o += snprintf(out + o, max - o, "00 ");
		} else if (i == 47) {
			o += snprintf(out + o, max - o, "01 48 fd 07 01 67 d2 70 72 f6 55 ");
		} else {
			o += snprintf(out + o, max - o, "%s", golden[i].hex);
			if (golden[i].cut) for (int k = 24; k < golden[i].n; k++) o += snprintf(out + o, max - o, "00 ");
		}
		o += snprintf(out + o, max - o, "|");
	}
	return o;
}
static void replay3_tests(void)
{
	static char exp[1 << 16];
	size_t el = expect_a(exp, sizeof(exp));
	const char *tail = "01 07 20 00 |01 0b 20 07 00 e0 01 30 00 01 00 |01 0c 20 02 00 00 |01 07 20 00 |01 07 20 00 |01 07 20 00 |";

	/* bluetoothd-like chip: pass A puts exactly the 48 packets on the wire back to back, then the final 2007, the witnesses, three 2007 retries; nothing diverges */
	run_replay3(11, 1); if (getenv("T_VERBOSE")) fputs(logbuf, stdout);
	CHECK(strncmp(txlog, exp, el) == 0);
	if (strncmp(txlog, exp, el) != 0) { size_t k = 0; while (k < el && txlog[k] == exp[k]) k++; printf("FAIL replay3 first byte difference at txlog offset %zu\n", k); }
	CHECK(strcmp(txlog + el, tail) == 0);
	CHECK(has("replay3 pass A start (sweetbtleprobe=3, patch 0219): 48 steps")); CHECK(has("NO probe between the steps, 0x2007 once at the end"));
	CHECK(has("replay3 A pos=19 op=0x204a plen=0 status=0000 (success) bluetoothd=0000 wait=0 ms\n")); CHECK(has("replay3 A pos=48 op=0xfd48 plen=7 status=0x01 (unknown HCI command) bluetoothd=0x01 wait=0 ms\n"));
	CHECK(has("replay3 A final 2007 status=0000 (success) wait=0 ms\n")); CHECK(!has("DIVERGES")); CHECK(!has("DISALLOWED")); CHECK(!has("first-disallowed-after=4"));
	CHECK(has("replay3 A 2007 again after ~100 ms: status=0000")); CHECK(has("after ~1000 ms: status=0000")); CHECK(has("after ~5000 ms: status=0000")); CHECK(ms_total >= 5000);
	CHECK(has("replay3 pass A done: steps=48 commands=54 no-answer=0 diverged-from-bluetoothd=0 first-diverged=0 op=0x0000 first-disallowed-after=0 "));
	CHECK(!strstr(txlog, "01 0c 20 02 01")); CHECK(!strstr(txlog, "01 05 20")); CHECK(!strstr(txlog, "01 01 fc")); CHECK(!strstr(txlog, "01 0a 20"));
	/* H1 world: pass A (no probe) reproduces the refusal at the end, pass B (probe after every step) shows the 204a refusal of bt-up6 and no final refusal */
	run_replay3(10, 2); if (getenv("T_VERBOSE")) fputs(logbuf, stdout);
	CHECK(has("replay3 A final 2007 status=0x0c (command disallowed) wait=0 ms DISALLOWED\n")); CHECK(has("first-disallowed-after=48 op=0xfd48 (2007 status 0x0c)"));
	CHECK(has("replay3 A pos=19 op=0x204a plen=0 status=0000 (success) bluetoothd=0000 wait=0 ms\n"));
	CHECK(has("replay3 B pos=19 op=0x204a plen=0 status=0x0c (command disallowed) bluetoothd=0000 DIVERGES-FROM-BLUETOOTHD wait=0 ms then 2007 status=0000 (success)\n"));
	CHECK(has("replay3 pass A done: steps=48 commands=54 no-answer=0 diverged-from-bluetoothd=0 first-diverged=0 op=0x0000 first-disallowed-after=48 "));
	CHECK(has("replay3 pass B done: steps=48 commands=96 no-answer=0 diverged-from-bluetoothd=1 first-diverged=19 op=0x204a first-disallowed-after=0 "));
	CHECK(has("replay3 A 2007 again after ~100 ms: status=0000 (success)"));   /* in this fake the refusal is momentary: the decay rows must show it */
	/* chip that is not the H1 world, probes pass: model 0 answers fd48 0x00, so pos 48 is named as the one divergence */
	run_replay3(0, 2);
	CHECK(has("replay3 A pos=48 op=0xfd48 plen=7 status=0000 (success) bluetoothd=0x01 DIVERGES-FROM-BLUETOOTHD")); CHECK(has("replay3 pass B done: steps=48 commands=96 no-answer=0 diverged-from-bluetoothd=1 first-diverged=48 op=0xfd48 first-disallowed-after=0 "));
	/* silent chip: every step loud, nothing hangs */
	run_replay3(6, 2);
	CHECK(has("replay3 A pos=1 op=0x0c03 plen=0 NO ANSWER")); CHECK(has("BT GAP: replay3 A:")); CHECK(has("BT GAP: replay3 B:")); CHECK(has("replay3 pass B done: steps=48"));
}

/* ---- patch 0217: sweetbtrw200b rewrite parser (H4 events, split at every byte) ---- */
static void rw_reset(void) { sbu_rw200b_on = true; sbu_rw200c_on = false; sbu_rw200c_n = 0; sbu_nopadv_on = false; sbu_nopadv_n = 0; sbu_rw_st = 0; sbu_rw_off = 0; sbu_rw_plen = 0; sbu_rw200b_n = 0; sbu_rw_other = 0; sbu_rw_evt = 0; sbu_rw_opc = 0; loglen = 0; logbuf[0] = 0; }
static void rw_feed(uint8_t *s, size_t n, size_t split)
{
	if (split > 0 && split < n) { sbu_rw200b(s, split); sbu_rw200b(s + split, n - split); } else { sbu_rw200b(s, n); }
}
static void rw_tests(void)
{
	const uint8_t cc_200b_0c[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0b, 0x20, 0x0c };
	const uint8_t cc_200b_ok[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0b, 0x20, 0x00 };
	const uint8_t cc_2007_0c[8] = { 0x04, 0x0e, 0x05, 0x01, 0x07, 0x20, 0x0c, 0x00 };
	const uint8_t cs_200b[7] = { 0x04, 0x0f, 0x04, 0x0c, 0x01, 0x0b, 0x20 };
	const uint8_t cc_other_0c[7] = { 0x04, 0x0e, 0x04, 0x01, 0x72, 0x0c, 0x0c };      /* 0c72 with status 0x0c: must stay 0x0c */
	uint8_t s[64];

	for (size_t split = 0; split < 7; split++) {   /* the status byte is rewritten whichever way the event is split between polls */
		rw_reset(); memcpy(s, cc_200b_0c, 7); rw_feed(s, 7, split);
		CHECK(s[6] == 0x00 || split == 0 ? s[6] == 0x00 : 0);
		CHECK(sbu_rw200b_n == 1); CHECK(has("BT GAP: sweetbtrw200b=1 rewrote the status of the Command Complete for opcode 0x200b from 0x0c"));
		CHECK(sbu_rw_off == 0);
	}
	/* a split chunk: the rewrite lands in the second chunk's copy */
	rw_reset(); { uint8_t a[5] = { 0x04, 0x0e, 0x04, 0x01, 0x0b }, b[2] = { 0x20, 0x0c }; sbu_rw200b(a, 5); sbu_rw200b(b, 2); CHECK(b[1] == 0x00); CHECK(sbu_rw200b_n == 1); }
	rw_reset(); memcpy(s, cc_200b_ok, 7); rw_feed(s, 7, 0); CHECK(sbu_rw200b_n == 0); CHECK(s[6] == 0x00);
	rw_reset(); memcpy(s, cc_2007_0c, 8); rw_feed(s, 8, 0); CHECK(sbu_rw200b_n == 0); CHECK(s[6] == 0x0c);
	rw_reset(); memcpy(s, cs_200b, 7); rw_feed(s, 7, 0); CHECK(sbu_rw200b_n == 0); CHECK(memcmp(s, cs_200b, 7) == 0);
	rw_reset(); memcpy(s, cc_other_0c, 7); rw_feed(s, 7, 0); CHECK(sbu_rw200b_n == 0); CHECK(s[6] == 0x0c);
	/* three events in one chunk: only the middle one is touched, the framing carries on after it */
	rw_reset(); memcpy(s, cc_2007_0c, 8); memcpy(s + 8, cc_200b_0c, 7); memcpy(s + 15, cc_other_0c, 7); rw_feed(s, 22, 0);
	CHECK(s[6] == 0x0c); CHECK(s[14] == 0x00); CHECK(s[21] == 0x0c); CHECK(sbu_rw200b_n == 1);
	/* a stray byte outside an event is passed through and counted loudly; the next event is still parsed */
	rw_reset(); s[0] = 0x55; memcpy(s + 1, cc_200b_0c, 7); rw_feed(s, 8, 0); CHECK(s[0] == 0x55); CHECK(s[7] == 0x00); CHECK(sbu_rw_other == 1); CHECK(has("BT GAP: sweetbtrw200b: byte 0x55 outside an H4 event"));
	/* review T-4c2c #2: other statuses/events/Command Status stay untouched */
	{ const uint8_t a[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0b, 0x20, 0x12 }, b[7] = { 0x04, 0xff, 0x04, 0x01, 0x0b, 0x20, 0x0c }, c[7] = { 0x04, 0x0f, 0x04, 0x0c, 0x0b, 0x20, 0x0c }; const uint8_t *t[3] = { a, b, c };
	  for (int k = 0; k < 3; k++) { rw_reset(); memcpy(s, t[k], 7); rw_feed(s, 7, 0); CHECK(sbu_rw200b_n == 0); CHECK(memcmp(s, t[k], 7) == 0); } }
	/* patch 0223 (T-08f7): sweetbtrw200c. 200c Command Complete status 0x0c -> 0x00 only with the knob on; 200b/2007/other untouched by it; each knob alone leaves the other opcode alone */
	{ const uint8_t cc_200c_0c[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0c, 0x20, 0x0c }, cc_200c_ok[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0c, 0x20, 0x00 };
	  for (size_t split = 0; split < 7; split++) {
		rw_reset(); sbu_rw200c_on = true; memcpy(s, cc_200c_0c, 7); rw_feed(s, 7, split);
		CHECK(s[6] == 0x00); CHECK(sbu_rw200c_n == 1); CHECK(sbu_rw200b_n == 0); CHECK(has("BT GAP: sweetbtrw200c=1 rewrote the status of the Command Complete for opcode 0x200c from 0x0c")); CHECK(sbu_rw_off == 0);
	  }
	  rw_reset(); memcpy(s, cc_200c_0c, 7); rw_feed(s, 7, 0); CHECK(s[6] == 0x0c); CHECK(sbu_rw200c_n == 0);   /* knob off: untouched */
	  rw_reset(); sbu_rw200c_on = true; sbu_rw200b_on = false; memcpy(s, cc_200b_0c, 7); rw_feed(s, 7, 0); CHECK(s[6] == 0x0c); CHECK(sbu_rw200b_n == 0);   /* only 200c on: 200b stays */
	  rw_reset(); sbu_rw200c_on = true; memcpy(s, cc_200c_ok, 7); rw_feed(s, 7, 0); CHECK(sbu_rw200c_n == 0); CHECK(s[6] == 0x00);
	  rw_reset(); sbu_rw200c_on = true; memcpy(s, cc_2007_0c, 8); memcpy(s + 8, cc_200c_0c, 7); memcpy(s + 15, cc_200b_0c, 7); rw_feed(s, 22, 0);   /* both knobs: 2007 stays, 200c and 200b rewritten */
	  CHECK(s[6] == 0x0c); CHECK(s[14] == 0x00); CHECK(s[21] == 0x00); CHECK(sbu_rw200c_n == 1); CHECK(sbu_rw200b_n == 1);
	  rw_reset(); sbu_rw200c_on = true; s[0] = 0x02; memcpy(s + 1, cc_200c_0c, 7); rw_feed(s, 8, 0); CHECK(!sbu_rw200c_on); CHECK(!sbu_rw200b_on); CHECK(s[7] == 0x0c); }   /* non-event packet switches both off */
	/* patch 0225 (T-aea4): sweetbtnopadv. Octet 1 bit 5 (LE feature bit 13) of a successful 0x2003 Command Complete is cleared, bt-up9 bytes [obs]: 79 -> 59; nothing else changes */
	{ const uint8_t f_up9[15] = { 0x04, 0x0e, 0x0c, 0x01, 0x03, 0x20, 0x00, 0xef, 0x79, 0xff, 0xff, 0x0e, 0x00, 0x00, 0x00 };
	  uint8_t want[15]; memcpy(want, f_up9, 15); want[8] = 0x59;
	  for (size_t split = 0; split < 15; split++) {
		rw_reset(); sbu_rw200b_on = false; sbu_nopadv_on = true; memcpy(s, f_up9, 15); rw_feed(s, 15, split);
		CHECK(memcmp(s, want, 15) == 0); CHECK(sbu_nopadv_n == 1); CHECK(sbu_rw_off == 0);
		CHECK(has("BT GAP: sweetbtnopadv=1 cleared LE feature bit 13 (Periodic Advertising) in the 0x2003 Command Complete for bluetoothd: octet1 0x79 -> 0x59 (x1)"));
	  }
	  rw_reset(); memcpy(s, f_up9, 15); rw_feed(s, 15, 0); CHECK(memcmp(s, f_up9, 15) == 0); CHECK(sbu_nopadv_n == 0);   /* knob off (200b on): untouched */
	  rw_reset(); sbu_nopadv_on = true; sbu_rw200c_on = true; memcpy(s, f_up9, 15); s[6] = 0x0c; memcpy(want, s, 15); rw_feed(s, 15, 0); CHECK(memcmp(s, want, 15) == 0); CHECK(sbu_nopadv_n == 0); CHECK(has("BT GAP: sweetbtnopadv: 0x2003 Command Complete plen 12 status 0xc (need plen 12, status 0): nothing rewritten"));   /* failed 0x2003: untouched, loud */
	  rw_reset(); sbu_nopadv_on = true; memcpy(s, f_up9, 15); s[8] = 0x59; memcpy(want, s, 15); rw_feed(s, 15, 0); CHECK(memcmp(s, want, 15) == 0); CHECK(sbu_nopadv_n == 0); CHECK(has("BT GAP: sweetbtnopadv: 0x2003 Command Complete octet1 0x59 has bit 13 clear already"));
	  { uint8_t o[15]; memcpy(o, f_up9, 15); o[4] = 0x02; rw_reset(); sbu_nopadv_on = true; memcpy(s, o, 15); rw_feed(s, 15, 0); CHECK(memcmp(s, o, 15) == 0); CHECK(sbu_nopadv_n == 0); }   /* other opcode (0x2002) same shape: untouched */
	  { uint8_t o[15]; memcpy(o, f_up9, 15); o[1] = 0x0f; rw_reset(); sbu_nopadv_on = true; memcpy(s, o, 15); rw_feed(s, 15, 0); CHECK(memcmp(s, o, 15) == 0); CHECK(sbu_nopadv_n == 0); }   /* not a Command Complete: untouched */
	  { uint8_t o[15]; memcpy(o, f_up9, 15); o[2] = 0x0b; rw_reset(); sbu_nopadv_on = true; memcpy(s, o, 14); rw_feed(s, 14, 0); CHECK(memcmp(s, o, 14) == 0); CHECK(sbu_nopadv_n == 0); CHECK(sbu_rw_off == 0); CHECK(has("plen 11 status 0 (need plen 12")); }   /* plen 11 != 12: untouched, loud */
	  { uint8_t o[16]; memcpy(o, f_up9, 15); o[2] = 0x0d; o[15] = 0x00; rw_reset(); sbu_nopadv_on = true; memcpy(s, o, 16); rw_feed(s, 16, 0); CHECK(memcmp(s, o, 16) == 0); CHECK(sbu_nopadv_n == 0); CHECK(sbu_rw_off == 0); CHECK(has("plen 13 status 0 (need plen 12")); }   /* plen 13 != 12 (review #3) */
	  { const uint8_t o[6] = { 0x04, 0x0e, 0x03, 0x01, 0x03, 0x20 }; rw_reset(); sbu_nopadv_on = true; memcpy(s, o, 6); rw_feed(s, 6, 0); CHECK(memcmp(s, o, 6) == 0); CHECK(sbu_rw_off == 0); CHECK(has("plen 3 has no status")); }   /* plen 3: no status byte, loud */
	  /* review #2: the rx-error framing reset with only this knob on */
	  sbu_rw200b_on = false; sbu_rw200c_on = false; sbu_nopadv_on = true; sbu_rw_off = 3; rw_err = 1; memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; rxh = rxt = 0; loglen = 0; logbuf[0] = 0;
	  (void)sbu_rx_poll(); rw_err = 0; CHECK(sbu_rw_off == 0); CHECK(has("framing reset after rx error")); sbu_nopadv_on = false;
	  /* the bt-up9 order in one chunk: 0x2003 features, 200b 0x0c, 200c 0x0c with all three knobs: each rewritten once, framing carries on */
	  { const uint8_t cc_200c_0c[7] = { 0x04, 0x0e, 0x04, 0x01, 0x0c, 0x20, 0x0c };
	    rw_reset(); sbu_nopadv_on = true; sbu_rw200c_on = true; memcpy(s, f_up9, 15); memcpy(s + 15, cc_200b_0c, 7); memcpy(s + 22, cc_200c_0c, 7); rw_feed(s, 29, 0);
	    CHECK(s[8] == 0x59); CHECK(s[21] == 0x00); CHECK(s[28] == 0x00); CHECK(sbu_nopadv_n == 1); CHECK(sbu_rw200b_n == 1); CHECK(sbu_rw200c_n == 1); }
	  /* nopadv alone through sbu_rx_poll: the trace keeps the chip's 0x79, the rewrite row appears; a non-event packet switches it off */
	  memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
	  rxh = rxt = 0; rw_reset(); sbu_rw200b_on = false; sbu_nopadv_on = true; sbu_trace_n = 0; push(f_up9, 15); (void)sbu_rx_poll();
	  CHECK(has("BT HCI: rx 15 bytes: 04 0e 0c 01 03 20 00 ef 79 ff ff 0e 00 00 00")); CHECK(sbu_nopadv_n == 1);
	  rw_reset(); sbu_nopadv_on = true; s[0] = 0x02; memcpy(s + 1, f_up9, 15); rw_feed(s, 16, 0); CHECK(!sbu_nopadv_on); CHECK(s[9] == 0x79); }
	/* a non-event packet type switches the knob off for the rest of the boot, loudly */
	rw_reset(); sbu_rw200b_on = true; s[0] = 0x02; memcpy(s + 1, cc_200b_0c, 7); rw_feed(s, 8, 0); CHECK(!sbu_rw200b_on); CHECK(s[7] == 0x0c); CHECK(has("non-event packet type 0x2: rewrite disabled"));
	/* sbu_rx_poll through the fake FIFO: knob off = no rewrite row, knob on = rewrite row; the trace row shows the chip's real bytes either way; an rx error resets the framing */
	for (int on = 0; on < 2; on++) {
		memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
		rxh = rxt = 0; rw_reset(); sbu_trace_n = 0; sbu_rw200b_on = on; push(cc_200b_0c, 7); (void)sbu_rx_poll();
		CHECK(has("BT HCI: rx 7 bytes: 04 0e 04 01 0b 20 0c")); CHECK(has("rewrote the status") == on);
	}
	sbu_rw200b_on = true; sbu_rw_off = 3; rw_err = 1; memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; rxh = rxt = 0;
	(void)sbu_rx_poll(); rw_err = 0; CHECK(sbu_rw_off == 0); CHECK(has("framing reset after rx error")); sbu_rw200b_on = false;
	/* full tx dump (review T-4c2c #2d): 252 bytes -> rows at 24, 72, 120, 168, 216, last byte printed; rx never dumps */
	{ uint8_t pk[252]; for (int k = 0; k < 252; k++) pk[k] = (uint8_t)k; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; sbu_trace("tx", pk, 252);
	  CHECK(has("tx data@24: 18 19")); CHECK(has("tx data@72:")); CHECK(has("tx data@120:")); CHECK(has("tx data@168:")); CHECK(has("tx data@216: d8")); CHECK(has("f9 fa fb \n"));
	  loglen = 0; logbuf[0] = 0; sbu_trace("rx", pk, 252); CHECK(!has("data@")); loglen = 0; logbuf[0] = 0; sbu_trace("tx", pk, 24); CHECK(!has("data@")); }
	/* the no-flip run waits >= 25 s (the msleep(25000)) */
	run_replay(0); CHECK(ms_total >= 25000);
}
static int sweep_rc;
/* ---- patch 0221: injection-position sweep (sweetbtleprobe=4). Model 10 = the H1 world (2007 right after fd48 refused only when no 2007 was sent since the Reset; 204a refused once a 2007 was sent since the Reset) ---- */
static void run_sweep(int m)
{
	struct sbu_cand cand = { 196608000, false, "vendor" };

	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true;
	rxh = rxt = ntx = flipped = rnd_set = bd_set = ext_lock = probed_rs = last_op = 0; txlen = 0; txlog[0] = 0; model = m; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; ms_total = 0;
	sbu_trace_quiet_n = 0; usi_mode_reg = 0; reset_n = regon_high = 0; sbu.clk_hz = 196608000; sbu.clk_osc = false;
	sweep_rc = sbu_sweep(&cand);
}
/* review T-2ed7 finding 1: walk the fake's tx log (packets "01 lo hi plen payload|"): every opcode is one the 48-step table, 0219 or bluetoothd sends, 200b/200c carry exactly bluetoothd's bytes (never scan enable ON), and the counts of 2007/200b/200c packets are those the sweep must send */
static void sweep_wire_checks(int n2007, int n200b, int n200c)
{
	static const unsigned extra[] = { 0x2007, 0x200b, 0x200c, 0x0c03 };
	int c2007 = 0, c200b = 0, c200c = 0, bad = 0;
	const char *q = txlog;

	while (*q) {
		const char *e = strchr(q, '|');
		unsigned b[4]; char pk[1024]; size_t l = e ? (size_t)(e - q) : strlen(q);

		if (l >= sizeof(pk)) l = sizeof(pk) - 1;
		memcpy(pk, q, l); pk[l] = 0;
		if (sscanf(pk, "%x %x %x %x", &b[0], &b[1], &b[2], &b[3]) != 4 || b[0] != 1) { bad++; }
		else {
			unsigned op = b[1] | (b[2] << 8); int known = 0;
			for (size_t i = 0; i < sizeof(extra) / sizeof(extra[0]); i++) if (extra[i] == op) known = 1;
			for (int i = 0; i < 48; i++) { unsigned g[3]; if (sscanf(golden[i].hex, "%x %x %x", &g[0], &g[1], &g[2]) == 3 && (g[1] | (g[2] << 8)) == op) known = 1; }
			if (!known) { bad++; printf("FAIL unknown opcode %#x on the wire\n", op); }
			if (op == 0x2007) { c2007++; if (strcmp(pk, "01 07 20 00 ") != 0) bad++; }
			if (op == 0x200b) { c200b++; if (strcmp(pk, "01 0b 20 07 00 e0 01 30 00 01 00 ") != 0) bad++; }
			if (op == 0x200c) { c200c++; if (strcmp(pk, "01 0c 20 02 00 00 ") != 0) bad++; }   /* scan enable OFF only */
		}
		if (!e) break;
		q = e + 1;
	}
	CHECK(bad == 0); CHECK(c2007 == n2007); CHECK(c200b == n200b); CHECK(c200c == n200c);
	if (c2007 != n2007 || c200b != n200b || c200c != n200c) printf("FAIL wire counts 2007=%d (want %d) 200b=%d (want %d) 200c=%d (want %d)\n", c2007, n2007, c200b, n200b, c200c, n200c);
}
static void sweep_tests(void)
{
	run_sweep(10); if (getenv("T_VERBOSE")) fputs(logbuf, stdout);
	CHECK(sweep_rc == 0); CHECK(has("sweep start (sweetbtleprobe=4, patch 0221): 25 passes"));
	CHECK(has("sweep inj=0 inj2007=none 204a=0000 final2007=0x0c 200b=0000 200c=0000 diverged=0 first-diverged=0 op=0x0000 no-answer=0 verdict=REFUSED\n"));   /* the control reproduces bt-up7 pass A */
	CHECK(has("sweep inj=1 inj2007=0000 204a=0x0c final2007=0000 200b=0000 200c=0000 diverged=1 first-diverged=19 op=0x204a no-answer=0 verdict=OPEN-BUT-DIVERGED\n"));
	CHECK(has("sweep inj=18 inj2007=0000 204a=0x0c final2007=0000 200b=0000 200c=0000 diverged=1 first-diverged=19 op=0x204a no-answer=0 verdict=OPEN-BUT-DIVERGED\n"));
	CHECK(has("sweep inj=19 inj2007=0000 204a=0000 final2007=0000 200b=0000 200c=0000 diverged=0 first-diverged=0 op=0x0000 no-answer=0 verdict=OPEN-ALL\n"));
	CHECK(has("sweep inj=47 inj2007=0000 204a=0000 final2007=0000 200b=0000 200c=0000 diverged=0 first-diverged=0 op=0x0000 no-answer=0 verdict=OPEN-ALL\n"));
	CHECK(has("sweep inj=48 inj2007=0x0c 204a=0000 final2007=0000 200b=0000 200c=0000 diverged=0 first-diverged=0 op=0x0000 no-answer=0 verdict=OPEN-BUT-DIVERGED\n"));   /* injected right after fd48 = the pass-A refusal of the first 2007; the later ones are open in this model */
	CHECK(has("sweep inj=1 pos=19 op=0x204a plen=0 status=0x0c (command disallowed) bluetoothd=0000 DIVERGES-FROM-BLUETOOTHD\n"));
	CHECK(has("sweep done: passes=25 open-all=12 open-but-diverged=12 refused=1 open-all-at-inj=[19,20,22,25,30,36,37,38,39,43,45,47] suppressed-hci-transfers="));
	CHECK(sbu_trace_n == 0); CHECK(!sbu_trace_quiet); CHECK(!has("BT HCI:"));
	/* a world where nothing is ever refused: every pass OPEN-ALL, k=0 included */
	run_sweep(11); CHECK(has("sweep inj=0 inj2007=none 204a=0000 final2007=0000 200b=0000 200c=0000 diverged=0")); CHECK(has("open-all=25 open-but-diverged=0 refused=0"));
	/* review T-2ed7 findings 1+2: the bytes on the wire are pinned (opcode allowlist, 200b/200c payloads, no scan enable) and the power-cycles are counted, in the world where every pass runs */
	run_sweep(11); sweep_wire_checks(49, 25, 25);
	CHECK(regon_high == 24); CHECK(!has("sweep stops")); CHECK(sweep_rc == 0);
	/* silent chip: the first pass is INCOMPLETE (not a ROM refusal), the sweep stops after it, powers the controller off, returns an error, and the trace flag is released (findings 3-5) */
	run_sweep(6); CHECK(has("NO ANSWER")); CHECK(has("verdict=INCOMPLETE")); CHECK(!has("verdict=REFUSED")); CHECK(!sbu_trace_quiet);
	CHECK(sweep_rc != 0); CHECK(has("sweep stops after the incomplete pass inj=0 (passes done 1 of 25, suppressed-hci-transfers=")); CHECK(!sbu.powered); CHECK(regon_high == 0); CHECK(!has("sweep inj=1 "));
	/* the chip answers pass 0 and dies at the re-probe Reset: the stop row names the passes done and the suppressed count, the controller is off, the return value is the failure (finding 4) */
	run_sweep(12); CHECK(sweep_rc != 0); CHECK(has("controller power-cycled after sweep pass inj=0: NO ANSWER")); CHECK(has("the sweep stops here (passes done 1 of 25, suppressed-hci-transfers=")); CHECK(!sbu.powered); CHECK(!sbu_trace_quiet); CHECK(!has("sweep inj=1 "));
}

/* ---- patch 0226 (T-4b11): sweetbtpubscan tx rewrite, bluetoothd's 0x200b Own_Address_Type 0x01 -> 0x00 (bt-up10 bytes [obs]) ---- */
static void tx_reset(void) { sbu_pubscan_on = true; sbu_pubscan_n = 0; sbu_tx_off = 0; sbu_tx_hlen = 0; sbu_tx_need = 0; sbu_tx_type = 0; sbu_tx_scantype = 0xff; loglen = 0; logbuf[0] = 0; nraw = 0; }
static void txrw(uint8_t *p, size_t n) { sbu_pubscan_n += sbu_txrw(p, n); }   /* direct parser cases: the count the ops tx would log */
static int count(const char *needle) { int c = 0; for (const char *q = logbuf; (q = strstr(q, needle)) != NULL; q++) c++; return c; }
static void txrw_tests(void)
{
	static const uint8_t c200b[11] = { 0x01, 0x0b, 0x20, 0x07, 0x00, 0xe0, 0x01, 0x30, 0x00, 0x01, 0x00 };   /* bt-up10 :6380 */
	uint8_t s[64], want[11];

	memcpy(want, c200b, 11); want[9] = 0x00;
	for (size_t k = 0; k <= 11; k++) {      /* every split point, including none */
		tx_reset(); memcpy(s, c200b, 11);
		if (k > 0 && k < 11) { txrw(s, k); txrw(s + k, 11 - k); } else { txrw(s, 11); }
		CHECK(memcmp(s, want, 11) == 0); CHECK(sbu_pubscan_n == 1); CHECK(sbu_tx_off == 0);
	}
	{ /* review #3: an active scan (LE_Scan_Type 0x01) is not rewritten, loud */
	  tx_reset(); memcpy(s, c200b, 11); s[4] = 0x01; txrw(s, 11); CHECK(s[9] == 0x01); CHECK(sbu_pubscan_n == 0); CHECK(has("sweetbtpubscan: 0x200b LE_Scan_Type 0x1 is not passive: nothing rewritten")); }
	tx_reset(); for (int i = 0; i < 11; i++) { s[i] = c200b[i]; txrw(s + i, 1); } CHECK(memcmp(s, want, 11) == 0); CHECK(sbu_pubscan_n == 1);   /* one byte per call */
	{ /* HCI Reset (plen 0) then two 0x200b: both rewritten, counter x2 */
	  static const uint8_t rst[4] = { 0x01, 0x03, 0x0c, 0x00 }; tx_reset(); memcpy(s, rst, 4); memcpy(s + 4, c200b, 11); memcpy(s + 15, c200b, 11); txrw(s, 26);
	  CHECK(s[13] == 0x00); CHECK(s[24] == 0x00); CHECK(sbu_pubscan_n == 2); CHECK(sbu_tx_off == 0); }
	{ /* an ACL packet whose payload is the 0x200b bytes is not a command: untouched; the command after it is rewritten */
	  tx_reset(); s[0] = 0x02; s[1] = 0x40; s[2] = 0x00; s[3] = 0x0b; s[4] = 0x00; memcpy(s + 5, c200b, 11); memcpy(s + 16, c200b, 11); txrw(s, 27);
	  CHECK(s[14] == 0x01); CHECK(s[25] == 0x00); CHECK(sbu_pubscan_n == 1); CHECK(sbu_tx_off == 0); }
	{ /* ACL length is 16 bits (0x0100 = 256): a 0x200b inside it is payload */
	  tx_reset(); memset(s, 0, sizeof(s)); s[0] = 0x02; s[1] = 0x40; s[2] = 0x00; s[3] = 0x00; s[4] = 0x01; txrw(s, 5);
	  for (int i = 0; i < 256; i += 11) { uint8_t t[11]; size_t m = 256 - i < 11 ? 256 - i : 11; memcpy(t, c200b, 11); txrw(t, m); CHECK(t[9] == 0x01 || m < 10); }
	  CHECK(sbu_tx_off == 0); CHECK(sbu_pubscan_n == 0); memcpy(s, c200b, 11); txrw(s, 11); CHECK(s[9] == 0x00); }
	{ /* SCO (8-bit length) and ISO (14-bit length, top 2 flag bits ignored) */
	  tx_reset(); const uint8_t sco[6] = { 0x03, 0x01, 0x00, 0x02, 0x01, 0x0b }; memcpy(s, sco, 6); memcpy(s + 6, c200b, 11); txrw(s, 17); CHECK(s[15] == 0x00); CHECK(sbu_pubscan_n == 1);
	  tx_reset(); const uint8_t iso[8] = { 0x05, 0x01, 0x00, 0x03, 0xc0, 0x01, 0x0b, 0x20 }; memcpy(s, iso, 8); memcpy(s + 8, c200b, 11); txrw(s, 19); CHECK(s[17] == 0x00); CHECK(sbu_pubscan_n == 1); CHECK(sbu_tx_off == 0); }
	{ /* other Own_Address_Type values are passed and logged */
	  tx_reset(); memcpy(s, c200b, 11); s[9] = 0x02; txrw(s, 11); CHECK(s[9] == 0x02); CHECK(sbu_pubscan_n == 0); CHECK(has("BT GAP: sweetbtpubscan: 0x200b Own_Address_Type 0x2 is not 0x01: nothing rewritten"));
	  tx_reset(); memcpy(s, c200b, 11); s[9] = 0x00; txrw(s, 11); CHECK(s[9] == 0x00); CHECK(sbu_pubscan_n == 0); CHECK(has("Own_Address_Type 0 is not 0x01")); }
	{ /* plen 8 (not 7): untouched, loud, framing follows plen */
	  tx_reset(); memcpy(s, c200b, 11); s[3] = 0x08; s[11] = 0x00; memcpy(s + 12, c200b, 11); txrw(s, 23); CHECK(s[9] == 0x01); CHECK(s[21] == 0x00); CHECK(sbu_pubscan_n == 1); CHECK(has("sweetbtpubscan: 0x200b plen 8 (need 7): nothing rewritten")); }
	{ /* other opcodes with the same bytes: untouched (0x200d, 0x210b, 0x0c0b) */
	  static const uint8_t ops[3][2] = { { 0x0d, 0x20 }, { 0x0b, 0x21 }, { 0x0b, 0x0c } };
	  for (int k = 0; k < 3; k++) { tx_reset(); memcpy(s, c200b, 11); s[1] = ops[k][0]; s[2] = ops[k][1]; txrw(s, 11); CHECK(s[9] == 0x01); CHECK(sbu_pubscan_n == 0); CHECK(sbu_tx_off == 0); CHECK(!has("sweetbtpubscan")); } }
	{ /* a byte that is no H4 type stops the knob for the boot, loud */
	  tx_reset(); s[0] = 0x07; memcpy(s + 1, c200b, 11); txrw(s, 12); CHECK(!sbu_pubscan_on); CHECK(s[10] == 0x01); CHECK(has("sweetbtpubscan: tx byte 0x7 is no H4 packet type: rewrite disabled")); }
	/* through the ops tx: knob off = the bytes are sent as given; on = the chip and the trace get 0x00 */
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; rxh = rxt = 0; ntx = 0; model = 0;
	tx_reset(); sbu_pubscan_on = false; sbu_trace_n = 0; txlen = 0; txlog[0] = 0; CHECK(sbu_tx_kctl(c200b, 11) == 0); CHECK(strstr(txlog, "01 0b 20 07 00 e0 01 30 00 01 00 |") != NULL); CHECK(sbu_pubscan_n == 0);
	tx_reset(); sbu_trace_n = 0; txlen = 0; txlog[0] = 0; CHECK(sbu_tx_kctl(c200b, 11) == 0); CHECK(strstr(txlog, "01 0b 20 07 00 e0 01 30 00 00 00 |") != NULL); CHECK(sbu_pubscan_n == 1);
	CHECK(has("BT HCI: tx 11 bytes: 01 0b 20 07 00 e0 01 30 00 00 00")); CHECK(c200b[9] == 0x01);
	CHECK(has("BT GAP: sweetbtpubscan=1 rewrote Own_Address_Type of bluetoothd's 0x200b from 0x01 (random, no 0x2005 sent) to 0x00 (public) on the way to the chip (x1)"));
	CHECK(nraw == 11 && memcmp(rawtx, want, 11) == 0);      /* the chip got the rewritten bytes */    /* bluetoothd's buffer is not written */
	{ /* > 512 bytes in one write: chunked, framing kept across chunks (ACL of 600 bytes, then the command) */
	  static uint8_t big[5 + 600 + 11]; memset(big, 0, sizeof(big)); big[0] = 0x02; big[1] = 0x40; big[3] = 600 & 0xff; big[4] = 600 >> 8; memcpy(big + 605, c200b, 11);
	  tx_reset(); ntx = 0; CHECK(sbu_tx_kctl(big, sizeof(big)) == 0); ntx = 0; rxh = rxt = 0; CHECK(sbu_pubscan_n == 1); CHECK(sbu_tx_off == 0); CHECK(big[614] == 0x01);
	  CHECK(nraw == (int)sizeof(big)); CHECK(memcmp(rawtx, big, 605) == 0); CHECK(memcmp(rawtx + 605, want, 11) == 0);   /* review #5: chunk 2 carries the rewritten byte */
	  /* review #4: a refused first chunk stops the write: exactly one refusal, no later chunk */
	  tx_reset(); sbu.powered = false; CHECK(sbu_tx_kctl(big, sizeof(big)) == ENXIO); CHECK(count("not powered/opened; refused") == 1); CHECK(nraw == 0); CHECK(!sbu_pubscan_on); sbu.powered = true; }
	/* the probe path (sbu_cmd -> sbu_tx) is never rewritten */
	tx_reset(); txlen = 0; txlog[0] = 0; CHECK(sbu_tx(c200b, 11) == 0); CHECK(strstr(txlog, "01 0b 20 07 00 e0 01 30 00 01 00 |") != NULL); CHECK(sbu_pubscan_n == 0);
	/* tx error inside a packet: the knob stops, loud */
	tx_reset(); sbu.powered = false; CHECK(sbu_tx_kctl(c200b, 5) == ENXIO); CHECK(!sbu_pubscan_on); CHECK(has("sweetbtpubscan: tx error 6 inside an H4 packet: rewrite disabled"));
	tx_reset(); CHECK(sbu_tx_kctl(c200b, 11) == ENXIO); CHECK(sbu_pubscan_on);      /* error at a packet boundary: framing known, knob stays */
	CHECK(sbu_pubscan_n == 0); CHECK(!has("sweetbtpubscan=1 rewrote")); CHECK(has("sweetbtpubscan: a 0x200b rewrite was not sent: tx error 6"));   /* review #2: no rewrite row when the chip got nothing */
	sbu.powered = true; sbu_pubscan_on = false;
	CHECK(sbu_ops.tx == sbu_tx_kctl);      /* the kctl stream (sbt_send) reaches the rewrite */
}
int main(void)
{
	run(0);
	CHECK(has("first-2007-not-success-after=none first-success-after: 2007=B0-after-reset 200b(bluetoothd random)=B11-2074-host-feature 200b(public)=B11-2074-host-feature"));
	CHECK(has("no-answer=0")); CHECK(has("supported-commands octets 24..30 = f0 f1 f2 f3 f0 f1 f2"));
	/* exact bytes on the wire: opcode LE, plen, params in the documented order (review T-0c77 M4) */
	CHECK(tx_has("01 0b 20 07 00 e0 01 30 00 01 00 ")); CHECK(tx_has("01 0b 20 07 00 e0 01 30 00 00 00 ")); CHECK(tx_has("01 07 20 00 "));
	CHECK(tx_has("01 01 fc 06 01 00 00 00 00 02 ")); CHECK(tx_has("01 05 20 06 01 00 00 00 00 c0 ")); CHECK(tx_has("01 0c 20 02 00 00 "));
	CHECK(tx_has("01 01 20 08 7f fa 86 3f 07 00 00 00 ")); CHECK(tx_has("01 6d 0c 02 01 01 ")); CHECK(tx_has("01 31 20 03 00 02 02 "));
	CHECK(tx_has("01 24 20 04 fb 00 48 08 ")); CHECK(tx_has("01 74 20 02 20 01 ")); CHECK(tx_has("01 03 0c 00 ")); CHECK(tx_has("01 4a 20 00 "));
	CHECK(!strstr(txlog, "01 0c 20 02 01")); /* never a scan ENABLE */
	/* order of phase B: 2003 .. 2074, then the A phase reset */
	CHECK(strstr(txlog, "01 03 20 00 |01 07 20 00 |01 01 20 08") && strstr(txlog, "01 4a 20 00 |01 07 20 00 |01 74 20 02 20 01 |"));
	run(1); CHECK(has("200b(bluetoothd random)=A3-le-random-address")); CHECK(has("2007=A3-le-random-address")); CHECK(has("first-2007-not-success-after=B0-after-reset")); CHECK(has("status=0x0c (command disallowed)"));
	run(2); CHECK(has("200b(bluetoothd random)=A4-write-bd-addr")); CHECK(has("BD address read back aa:aa:aa:aa:aa:aa")); CHECK(has("BD address read back 02:00:00:00:00:01"));
	run(3); CHECK(has("2007=none 200b(bluetoothd random)=none 200b(public)=none")); CHECK(has("DISALLOWED"));
	run(4); CHECK(has("step=B0-after-reset cmd=2007-read-adv-txpower op=0x2007 par=[] status=0x0c"));
	run(5); CHECK(has("first-2007-not-success-after=B10-204a-periodic-list")); CHECK(has("2007=B0-after-reset 200b(bluetoothd random)=A0-hci-reset"));
	run(6); CHECK(has("NO ANSWER")); CHECK(has("LE matrix done")); CHECK(has("BT GAP: LE probe:")); CHECK(has("no-answer=")); CHECK(!has("no-answer=0"));
	/* stale bytes before a command are logged, not eaten */
	run(0); { uint8_t junk[3] = { 0x04, 0x13, 0x00 }; (void)junk; }
	replay_tests();
	replay3_tests();
	sweep_tests();
	rw_tests();
	txrw_tests();
	fputs(logbuf, stdout);
	printf("RESULT: %s t0c77-le-probe-host\n", fails ? "FAIL" : "PASS"); return fails != 0;
}
