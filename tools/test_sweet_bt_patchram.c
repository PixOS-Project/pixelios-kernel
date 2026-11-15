/* Host test of the stock BCM.hcd download (patch 0218, T-86c9, W-BTUP): the REAL sweet_bt_uart.c (patches 0189..0218), a fake register file and a fake BCM4389 that models the ROM/patched
 * state, the chip baud (a host/chip baud mismatch drops every byte) and the REG_ON power-cycle. The image is synthetic unless T_HCD names the real BCM.hcd (gitignored, never in
 * git); with T_HCD and T_VND (the golden btsnoop_hci_vnd.log) the whole host->chip stream is compared byte for byte with the golden vendor snoop. */
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
static int knob_fast, knob_pr;
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l; if ((strcmp(n, "sweetbtpatchrambaud") == 0 && knob_fast) || (strcmp(n, "sweetbtpatchram") == 0 && knob_pr)) { *(int *)v = 1; return 1; } return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
void thread_deallocate(void *t) { (void)t; }
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { (void)r; *ref = (kern_ctl_ref)1; return 0; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 1; }

/* the image slot lives in test_sweet_bt_hcdimg.c (non-const there, so each case can load its own image) */
void hcdimg_set(const uint8_t *p, size_t n, const char *sha);

#define CLK 196646400u
#define MAXPK 4000
static uint8_t rxq[1 << 16]; static int rxh, rxt;
static uint8_t txb[300]; static int ntx;
static struct { uint16_t len; } pkidx[MAXPK]; static uint8_t pkdata[MAXPK * 262]; static int npk; static size_t pkoff;
static int rw_err, model, patched, chip_baud, powered_cycles, fc4c_seen, regon, bd_set; static uint8_t bd[6];
static uint32_t regs_pa[64], regs_v[64]; static int nregs; static unsigned long ms_total; static int nus;
static void push(const uint8_t *b, int n) { memcpy(rxq + rxt, b, n); rxt += n; }
static uint32_t host_ubrdiv, host_frac;
static int host_baud(void) { return (int)(CLK / (16u * (host_ubrdiv + 1) + host_frac)); }
static int link_ok(void) { int h = host_baud(); return h > chip_baud * 97 / 100 && h < chip_baud * 103 / 100; }
static void cc(uint16_t op, const uint8_t *rp, int n, uint8_t st)
{
	uint8_t e[80]; int k = 0;
	e[k++] = 0x04; e[k++] = 0x0e; e[k++] = 0; e[k++] = 0x01; e[k++] = op & 0xff; e[k++] = op >> 8; e[k++] = st;
	for (int i = 0; i < n; i++) e[k++] = rp[i];
	e[2] = (uint8_t)(k - 3); push(e, k);
}
static void answer(void)
{
	uint16_t op = txb[1] | (txb[2] << 8); uint8_t plen = txb[3];
	pkidx[npk].len = (uint16_t)(4 + plen);
	if (npk < MAXPK && pkoff + 4 + plen <= sizeof(pkdata)) { memcpy(pkdata + pkoff, txb, 4 + plen); pkoff += 4 + plen; npk++; }
	if (!link_ok() || model == 8 || (model == 13 && powered_cycles > 0)) return;      /* model 13: dead after the power-cycle */          /* wrong baud or a silent chip: every byte is lost */
	switch (op) {
	case 0x0c03: cc(op, NULL, 0, 0); break;
	case 0xfc79: { uint8_t r[6] = { model == 5 ? 0x55 : 0xaa, 0xfe, 0x0f, 0x27, patched ? (model == 10 ? 0xca : 0xcb) : 0x00, 0x00 }; cc(op, r, 6, 0); break; }
	case 0xfc18: {
		cc(op, NULL, 0, 0); if (model != 2) chip_baud = 4000000; break; }   /* the payload is checked byte-exact by the test (0x003d0900); model 2: the chip ignores the change */
	case 0xfc2e: cc(op, NULL, 0, model == 7 ? 1 : 0); break;
	case 0xfc4c:
		fc4c_seen++;
		if (model == 9 && fc4c_seen == 5) rw_err = 1;      /* a line error flag mid-stream */
		if (model == 6 && fc4c_seen == 10) { uint8_t ev[4] = { 0x04, 0x13, 0x01, 0x00 }; push(ev, 4); }
		break;                                   /* no Command Complete for a Write RAM record (golden vnd rec 8-3045 shows none) */
	case 0xfc4e: if (model == 3 || model == 13) break; cc(op, NULL, 0, 0); if (model != 4) patched = 1; chip_baud = 115200; break;
	case 0x1001: { uint8_t r[8] = { 0x0a, 0x00, 0x20, 0x0a, 0x0f, 0x00, 0x09, 0x62 }; if (patched) { r[0] = 0x0b; r[1] = model == 10 ? 0xca : 0xcb; r[3] = 0x0b; } cc(op, r, 8, 0); break; }
	case 0xfc01: memcpy(bd, txb + 4, 6); bd_set = 1; cc(op, NULL, 0, 0); break;
	case 0x1009: { uint8_t r[6]; if (bd_set) memcpy(r, bd, 6); else memset(r, 0xaa, 6); if (model == 11) r[5] ^= 1; cc(op, r, model == 12 ? 5 : 6, 0); break; }
	default: cc(op, NULL, 0, 0); break;
	}
}
static uint32_t f_rd(uint32_t pa)
{
	int cnt = rxt - rxh;
	if (pa == 0x175B0000u + U_UERSTAT) return (unsigned)rw_err;
	if (pa == 0x175B0000u + U_UFSTAT) return cnt > 255 ? 255 : cnt;
	if (pa == 0x175B0000u + U_UTRSTAT) return 4u | (cnt > 0);
	if (pa == 0x175B0000u + U_URXH) return cnt > 0 ? rxq[rxh++] : 0;
	if (pa == 0x175B0000u + U_UBRDIV) return host_ubrdiv;
	if (pa == 0x175B0000u + U_UFRACVAL) return host_frac;
	for (int i = 0; i < nregs; i++) if (regs_pa[i] == pa) return regs_v[i];
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	if (pa == 0x175B0000u + U_UTXH) {
		txb[ntx++] = (uint8_t)v;
		if (ntx >= 4 && ntx == 4 + txb[3]) { answer(); ntx = 0; }
		return;
	}
	if (pa == 0x175B0000u + U_UBRDIV) { host_ubrdiv = v; return; }
	if (pa == 0x175B0000u + U_UFRACVAL) { host_frac = v; return; }
	if (pa == 0x10840204u) {                        /* gpp16 DAT: BT_REG_ON is bit 2; falling edge = the REG_ON power-cycle that clears the controller RAM */
		if (regon && !(v & 4u)) { patched = 0; chip_baud = 115200; powered_cycles++; bd_set = 0; rxh = rxt = 0; }
		regon = (v >> 2) & 1;
	}
	for (int i = 0; i < nregs; i++) if (regs_pa[i] == pa) { regs_v[i] = v; return; }
	if (nregs < 64) { regs_pa[nregs] = pa; regs_v[nregs++] = v; }
}
static void f_us(unsigned us) { (void)us; nus++; }
static void f_ms(unsigned ms) { ms_total += ms; }
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static int fails;
static int has(const char *s) { return strstr(logbuf, s) != NULL; }

/* ---- the image: synthetic records or the real BCM.hcd ---- */
static uint8_t img[700000]; static size_t imglen; static unsigned img_nw;
static void put_rec(uint16_t op, const uint8_t *d, size_t n) { img[imglen++] = op & 0xff; img[imglen++] = op >> 8; img[imglen++] = (uint8_t)n; memcpy(img + imglen, d, n); imglen += n; }
static void synth_image(unsigned n)
{
	uint8_t d[255]; static const uint8_t lens[] = { 8, 255, 255, 70, 255, 1, 255, 32 };
	imglen = 0; img_nw = n;
	for (unsigned i = 0; i < n; i++) { size_t l = lens[i % sizeof(lens)]; for (size_t k = 0; k < l; k++) d[k] = (uint8_t)(i * 7 + k * 13 + 1); put_rec(0xfc4c, d, l); }
	{ static const uint8_t ln[4] = { 0xff, 0xff, 0xff, 0xff }; put_rec(0xfc4e, ln, 4); }
	hcdimg_set(img, imglen, "synthetic-image-sha256");
}
static int real_image;
static const struct sbu_cand cand = { .hz = CLK, .osc = false, .layout = "test" };
static void run_setup(int m, int fast)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; sbu.clk_hz = CLK; sbu.baud = 115200; sbu.rate = CLK;
	rxh = rxt = ntx = 0; npk = 0; pkoff = 0; model = m; patched = 0; chip_baud = 115200; powered_cycles = 0; fc4c_seen = 0; regon = 1; bd_set = 0; nregs = 0; ms_total = 0; nus = 0;
	host_ubrdiv = 105; host_frac = 11; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; knob_fast = fast; rw_err = 0; knob_pr = 0;
}
static int run(int m, int fast) { run_setup(m, fast); return sbu_patchram(&cand); }
static int run_attach(int m) { run_setup(m, 0); return sbu_patchram_attach(&cand); }
static const uint8_t *pk_at(int i, int *len) { size_t o = 0; for (int k = 0; k < i; k++) o += pkidx[k].len; *len = pkidx[i].len; return pkdata + o; }
static uint16_t op_at(int i) { int l; const uint8_t *p = pk_at(i, &l); return (uint16_t)(p[1] | (p[2] << 8)); }
static int count_op(uint16_t op) { int c = 0; for (int i = 0; i < npk; i++) if (op_at(i) == op) c++; return c; }

/* every host->chip packet of the run equals the image record (fc4c/fc4e) in order */
static void check_stream_vs_image(void)
{
	size_t off = 0; int idx = -1; unsigned n = 0;
	for (int i = 0; i < npk; i++) if (op_at(i) == 0xfc4c) { idx = i; break; }
	for (int i = idx; i >= 0 && i < npk; i++) {
		int l; const uint8_t *p = pk_at(i, &l);
		if (op_at(i) != 0xfc4c && op_at(i) != 0xfc4e) break;
		CHECK(off + 3 <= imglen); if (off + 3 > imglen) break;
		CHECK((size_t)l == (size_t)(1 + 3 + img[off + 2])); CHECK(p[0] == 0x01); CHECK(memcmp(p + 1, img + off, (size_t)l - 1) == 0);
		off += 3 + img[off + 2]; n++;
	}
	CHECK(off == imglen); CHECK(n == img_nw + 1);
}

/* golden: btsnoop_hci_vnd.log host->chip commands (flags 2) up to the second 0xfc79, minus 0xfc18 when the 4 Mbaud step is off, vs the packets of this run */
static void golden_compare(const char *vnd, int fast)
{
	FILE *f = fopen(vnd, "rb"); static uint8_t d[1 << 20]; size_t n, o = 16; int g = 0, fc79 = 0, ok = 1; static uint8_t gold[MAXPK][262]; static int glen[MAXPK];
	CHECK(f != NULL); if (!f) return;
	n = fread(d, 1, sizeof(d), f); fclose(f);
	CHECK(n > 16 && memcmp(d, "btsnoop\0", 8) == 0);
	while (o + 24 <= n && fc79 < 2) {
		uint32_t il = (uint32_t)d[o + 4] << 24 | d[o + 5] << 16 | d[o + 6] << 8 | d[o + 7], fl = (uint32_t)d[o + 8] << 24 | d[o + 9] << 16 | d[o + 10] << 8 | d[o + 11];
		const uint8_t *p = d + o + 24; o += 24 + il;
		if (fl != 2) continue;                      /* host->controller command */
		if (p[1] == 0x79 && p[2] == 0xfc) fc79++;
		if (p[1] == 0x18 && p[2] == 0xfc && !fast) continue;
		if (g < MAXPK) { memcpy(gold[g], p, il); glen[g++] = (int)il; }
	}
	printf("NOTE golden: %d host->chip commands up to the second 0xfc79\n", g);
	CHECK(g > 3000); CHECK(g <= npk);
	for (int i = 0; i < g && i < npk; i++) { int l; const uint8_t *p = pk_at(i, &l); if (l != glen[i] || memcmp(p, gold[i], (size_t)l) != 0) { printf("FAIL golden mismatch at packet %d (op %04x, run len %d, golden len %d)\n", i, op_at(i), l, glen[i]); ok = 0; fails++; break; } }
	if (ok) printf("NOTE golden: %d packets byte-identical with the vendor snoop (fast=%d)\n", g, fast);
}

int main(void)
{
	const char *thcd = getenv("T_HCD"), *tvnd = getenv("T_VND");
	int e;
	if (thcd != NULL && *thcd) {
		FILE *f = fopen(thcd, "rb"); size_t n;
		if (!f) { printf("FAIL cannot open T_HCD\n"); return 2; }
		n = fread(img, 1, sizeof(img), f); fclose(f); imglen = n; real_image = 1; img_nw = 0;
		for (size_t o = 0; o + 3 <= n; o += 3 + img[o + 2]) if ((img[o] | img[o + 1] << 8) == 0xfc4c) img_nw++;
		hcdimg_set(img, imglen, getenv("T_HCD_SHA") ? getenv("T_HCD_SHA") : "real-image");
	} else {
		synth_image(120);
	}

	/* 1. default: 115200, no 0xfc18; the exact order of the HAL sequence minus the 4 Mbaud steps and LPM */
	e = run(0, 0);
	CHECK(e == 0);
	CHECK(count_op(0xfc4c) == (int)img_nw); CHECK(count_op(0xfc4e) == 1); CHECK(count_op(0xfc18) == 0); CHECK(count_op(0xfc27) == 0); CHECK(count_op(0x2007) == 0);
	CHECK(op_at(0) == 0x0c03 && op_at(1) == 0xfc79 && op_at(2) == 0xfc2e && op_at(3) == 0xfc4c);
	CHECK(npk == (int)img_nw + 1 + 3 + 5);                   /* reset, id, download mode, N writes, launch, reset, id, version, write bd, read bd */
	CHECK(op_at(npk - 5) == 0x0c03 && op_at(npk - 4) == 0xfc79 && op_at(npk - 3) == 0x1001 && op_at(npk - 2) == 0xfc01 && op_at(npk - 1) == 0x1009);
	check_stream_vs_image();
	CHECK(has("BT PROBE: patchram start (sweetbtpatchram=1, patch 0218)")); CHECK(has("download baud 115200 (default; the HAL's 4 Mbaud step is off)"));
	CHECK(has("BT PROBE: patchram before: chip id=0xaa build=0")); CHECK(has("BT PROBE: patchram after launch: chip id=0xaa build=203 (rev word cb 00)"));
	CHECK(has("BT PROBE: patchram build=203 hci_rev=0x20cb records=")); CHECK(has("hci_ver=0x0b lmp_ver=0x0b")); CHECK(has("BT PROBE: patchram stream done:")); CHECK(has("unexpected-rx=0"));
	CHECK(has("BT PROBE: patchram bd-addr readback matches the synthetic address written (6 bytes)")); CHECK(has("0xfc27 LPM not sent"));
	CHECK(ms_total >= 300);                                 /* 50 ms after download mode + 250 ms after the launch */
	CHECK(sbu_trace_n < 40);                                /* the 3038 records must not use up the 3000-transfer HCI trace cap of bluetoothd */
	CHECK(!has("BT GAP: patchram step"));
	CHECK(!has("tx data@"));                                /* review T-60ed F4: no stream record payload in the log (rule 4) */
	CHECK(has("BT HCI: tx ") && has("patchram record 1/") && has("(payload not logged)"));
	CHECK(real_image || has("stock=no")); CHECK(real_image || has("BT GAP: patchram: the embedded image sha256 synthetic-image-sha256 is not the stock BCM.hcd"));
	{ int l; const uint8_t *p = pk_at(npk - 2, &l); CHECK(l == 10 && memcmp(p, "\x01\x01\xfc\x06\x01\x00\x00\x00\x00\x02", 10) == 0); }
	if (tvnd && *tvnd && real_image) golden_compare(tvnd, 0);
	else printf("NOTE golden oracle NOT run (T_HCD and T_VND not both set); the image is %s\n", real_image ? "real" : "synthetic");

	/* 2. the HAL's 4 Mbaud step (sub-knob): fc18 right after the first fc79, host follows, back to 115200 after the launch */
	e = run(1, 1);
	CHECK(e == 0); CHECK(count_op(0xfc18) == 1); CHECK(op_at(2) == 0xfc18); CHECK(op_at(3) == 0xfc2e); CHECK(host_baud() > 115200 * 97 / 100 && host_baud() < 115200 * 103 / 100);
	{ int l; const uint8_t *p = pk_at(2, &l); CHECK(l == 10 && memcmp(p, "\x01\x18\xfc\x06\x00\x00\x00\x09\x3d\x00", 10) == 0); }
	CHECK(has("BT UART: baud 4000000 UBRDIV 2 UFRACVAL 1")); CHECK(has("download baud 4000000 (sweetbtpatchrambaud=1)")); CHECK(has("patchram build=203 hci_rev=0x20cb"));
	check_stream_vs_image(); CHECK(powered_cycles == 0);
	if (tvnd && *tvnd && real_image) golden_compare(tvnd, 1);

	/* 3. the chip ignores the baud change: the host at 4 M gets no answer -> power-cycle, start over at 115200, download once */
	e = run(2, 1);
	CHECK(e == 0); CHECK(has("the 4 Mbaud download start failed")); CHECK(has("starting over at 115200")); CHECK(powered_cycles >= 1); CHECK(count_op(0xfc4c) == (int)img_nw); CHECK(has("patchram build=203"));
	CHECK(has("BT GAP: patchram step download-mode op=0xfc2e: no Command Complete"));

	/* 4. launch gets no Command Complete: loud, the caller power-cycles back to the ROM */
	e = run(3, 0); CHECK(e == ETIMEDOUT); CHECK(has("BT GAP: patchram step launch-ram op=0xfc4e: no Command Complete (error")); CHECK(!has("patchram build="));
	/* 5. the download had no effect (rev word unchanged) */
	e = run(4, 0); CHECK(e == EIO); CHECK(has("did not take effect")); CHECK(has("patchram build=0 hci_rev=0x2000"));
	/* 6. wrong chip id: refused before any download record */
	e = run(5, 0); CHECK(e == EINVAL); CHECK(has("chip id 0x55 is not 0xaa")); CHECK(count_op(0xfc4c) == 0); CHECK(count_op(0xfc4e) == 0);
	/* 7. unexpected rx during the stream: reported and the sequence stops (review T-60ed F5) */
	e = run(6, 0); CHECK(e == EIO); CHECK(has("unexpected rx bytes from the controller during the stream after record")); CHECK(!has("unexpected-rx=0")); CHECK(has("the controller state is unknown")); CHECK(count_op(0xfc4e) == 0);
	/* 8. download mode refused */
	e = run(7, 0); CHECK(e == EIO); CHECK(has("patchram step download-mode op=0xfc2e: status 0x01")); CHECK(count_op(0xfc4c) == 0);
	/* 9. silent chip: stops at the first command */
	e = run(8, 0); CHECK(e == ETIMEDOUT); CHECK(has("BT GAP: patchram step reset-1 op=0x0c03: no Command Complete")); CHECK(npk == 1);

	/* 10. F5: a line error flag mid-stream, a launch that answers build 202, a BD read-back that differs or is short: each stops the sequence */
	e = run(9, 0); CHECK(e == EIO); CHECK(has("rx error flags")); CHECK(has("the controller state is unknown")); CHECK(count_op(0xfc4e) == 0);
	e = run(10, 0); CHECK(e == EIO); CHECK(has("not accepted as patched")); CHECK(has("patchram build=202 hci_rev=0x20ca"));
	e = run(11, 0); CHECK(e == EIO); CHECK(has("bd-addr readback DIFFERS")); CHECK(has("is not the 0xfc01 address just written"));
	e = run(12, 0); CHECK(e == EIO); CHECK(has("bd-addr readback DIFFERS from the synthetic address written (5 bytes)"));
	/* chip id 0x55 with the 4 Mbaud sub-knob: no 0xfc18, no download, no retry */
	e = run(5, 1); CHECK(e == EINVAL); CHECK(count_op(0xfc18) == 0); CHECK(count_op(0xfc4c) == 0); CHECK(powered_cycles == 0);

	/* 10b. attach side (review T-60ed F7): sbu_patchram_attach() decides what bluetoothd meets */
	e = run_attach(0); CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(patched == 1); CHECK(powered_cycles == 0);
	e = run_attach(3); CHECK(e == 0); CHECK(has("sweetbtpatchram=1 FAILED")); CHECK(powered_cycles >= 1); CHECK(patched == 0); CHECK(sbu.powered && sbu.uart_ok); CHECK(op_at(npk - 1) == 0x0c03);
	e = run_attach(13); CHECK(e != 0); CHECK(has("backend not started after the failed patchram power-cycle")); CHECK(powered_cycles >= 1);
	{ static uint8_t keep[700000]; size_t kl = imglen; memcpy(keep, img, kl);
	  hcdimg_set(img, 0, "none"); e = run_attach(0); CHECK(e == 0); CHECK(has("sweetbtpatchram=1 had no effect (refused, nothing sent)")); CHECK(npk == 0); CHECK(powered_cycles == 0);
	  hcdimg_set(keep, kl, real_image && getenv("T_HCD_SHA") ? getenv("T_HCD_SHA") : "synthetic-image-sha256"); }
	run_setup(0, 0); knob_pr = 1; sbu_patchram_unused("why-x"); CHECK(has("BT GAP: sweetbtpatchram=1 ignored: why-x"));
	run_setup(0, 0); sbu_patchram_unused("why-y"); CHECK(!has("why-y"));      /* knob off: silent */
	/* sbu_tx keeps its powered/open guard (the split from sbu_tx_raw) */
	run_setup(0, 0); sbu.powered = false; { uint8_t t[4] = { 1, 3, 0x0c, 0 }; e = sbu_tx(t, sizeof(t)); CHECK(e == ENXIO); CHECK(ntx == 0 && npk == 0); }

	/* 11. image checks: nothing at all is sent for an empty or malformed image */
	hcdimg_set(img, 0, "none"); e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0); CHECK(has("sweetbtpatchram=1 refused: this kernel was built without BCM_HCD"));
	{
		static uint8_t bad[2000]; size_t n;
		/* an opcode outside {0xfc4c, 0xfc4e} (e.g. a flash/NVRAM style write) anywhere in the stream */
		n = 0; imglen = 0; synth_image(5);
		memcpy(bad, img, imglen); n = imglen; bad[3 + img[2]] = 0x1e; bad[3 + img[2] + 1] = 0xfc;      /* second record: opcode 0xfc1e */
		hcdimg_set(bad, n, "mut1"); e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0); CHECK(has("patchram image refused: record at offset"));
		/* truncated: a record claims more payload than the file holds */
		synth_image(5); hcdimg_set(img, imglen - 2, "mut2"); e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0); CHECK(has("patchram image"));
		/* launch not last (a write follows it) */
		synth_image(5); { uint8_t t[4] = { 1, 2, 3, 4 }; put_rec(0xfc4c, t, 4); hcdimg_set(img, imglen, "mut3"); } e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0);
		/* two launch records */
		synth_image(5); { static const uint8_t ln[4] = { 0xff, 0xff, 0xff, 0xff }; put_rec(0xfc4e, ln, 4); hcdimg_set(img, imglen, "mut4"); } e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0);
		/* launch with a payload other than 4 bytes */
		synth_image(5); imglen -= 7; { static const uint8_t ln[2] = { 0xff, 0xff }; put_rec(0xfc4e, ln, 2); hcdimg_set(img, imglen, "mut5"); } e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0);
		/* no launch record at all */
		synth_image(5); imglen -= 7; hcdimg_set(img, imglen, "mut6"); e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0);
		/* header-only trailing bytes */
		synth_image(5); img[imglen++] = 0x4c; hcdimg_set(img, imglen, "mut7"); e = run(0, 0); CHECK(e == SBU_PR_REFUSED); CHECK(npk == 0);
	}
	fputs(logbuf, stdout);
	printf("RESULT: %s t86c9-patchram-host (golden oracle: %s)\n", fails ? "FAIL" : "PASS", (tvnd && *tvnd && real_image) ? "yes" : "no");
	return fails != 0;
}
