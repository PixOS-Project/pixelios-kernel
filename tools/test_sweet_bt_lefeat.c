/* Host test of patches 0274 + 0277 (sweetbtromread read-only witness) (sprint-bt, sweetbtlefeat=1: LE feature bit 13 cleared with 0xfd3c after a verified patchram download), built on the patch 0218 harness (T-86c9, W-BTUP): the REAL sweet_bt_uart.c (patches 0189..0218), a fake register file and a fake BCM4389 that models the ROM/patched
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
int (*ts_hook)(int); int ts_n; int tsleep(void *c, int p, const char *w, int t) { (void)c;(void)p;(void)w; ts_n++; if (ts_hook) ts_hook(t); return 0; }      /* patch 0360: the hook lets a test model time passing */
void delay(int us) { (void)us; }
bool sweet_usb_board_is_gs101(void) { return true; }
static uint8_t rom_buf_t[0x1a0400]; static uint32_t rom_pub = 0xffffffffu;
uint8_t *sweet_bt_rom_buffer(uint32_t n) { return n <= sizeof(rom_buf_t) ? rom_buf_t : NULL; }
void sweet_bt_rom_publish(uint32_t n) { rom_pub = n; }
static int knob_fast, knob_pr, knob_lf, knob_rr; static uint32_t knob_romoff;
static int knob_bt = -1, knob_kb = -1;      /* patch 0295: -1 = boot-arg absent (default on), 0 = "=0", 1 = "=1", other = a bad value; knob_pr/knob_lf use the same coding via -1 */
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l; if (strcmp(n, "sweetbtromoff") == 0) { if (!knob_romoff) return 0; *(uint32_t *)v = knob_romoff; return 1; } if ((strcmp(n, "sweetbtpatchrambaud") == 0 && knob_fast) || (strcmp(n, "sweetbtpatchram") == 0 && knob_pr >= 0) || (strcmp(n, "sweetbtlefeat") == 0 && knob_lf >= 0) || (strcmp(n, "sweetbtromread") == 0 && knob_rr)) { *(int *)v = !strcmp(n, "sweetbtpatchram") ? knob_pr : !strcmp(n, "sweetbtlefeat") ? knob_lf : 1; return 1; } if (!strcmp(n, "sweetbt") && knob_bt >= 0) { *(int *)v = knob_bt; return 1; } if (!strcmp(n, "sweetbtkeepbaud") && knob_kb >= 0) { *(int *)v = knob_kb; return 1; } return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
void *kts_f; int kts_n, kts_fail;      /* patch 0296: the last thread entry handed to kernel_thread_start (never run by the stub), kts_fail = 1 makes it fail */
int kernel_thread_start(void *f, void *a, void **t) { (void)a; kts_f = f; kts_n++; if (kts_fail) return 5; *t = (void *)1; return 0; }
uint64_t mach_absolute_time(void) { static uint64_t t; return t += 3000000u; }      /* patch 0296: 3 ms per call */
void absolutetime_to_nanoseconds(uint64_t a, uint64_t *n) { *n = a; }
void thread_terminate_self(void) { }
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
static uint8_t le_feat[8]; static const uint8_t le_factory[8] = { 0xef, 0x79, 0xff, 0xff, 0x0e, 0x00, 0x00, 0x00 };      /* bt-up9 patched answer [obs] */
static uint32_t regs_pa[64], regs_v[64]; static int nregs; static unsigned long ms_total; static int nus;
static void push(const uint8_t *b, int n) { memcpy(rxq + rxt, b, n); rxt += n; }
static uint32_t host_ubrdiv, host_frac;
static int host_baud(void) { return (int)(CLK / (16u * (host_ubrdiv + 1) + host_frac)); }
static int link_ok(void) { int h = host_baud(); return h > chip_baud * 97 / 100 && h < chip_baud * 103 / 100; }
static void cc(uint16_t op, const uint8_t *rp, int n, uint8_t st)
{
	uint8_t e[300]; int k = 0;
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
	case 0x0c03: if (model == 21) memcpy(le_feat, le_factory, 8); cc(op, NULL, 0, 0); break;      /* model 21: the 0xfd3c value does not survive a Reset */
	case 0xfc4d: {      /* Read_RAM: addr LE32 + len; fake ROM = byte (addr+i)*7, readable below 0x3000 (model 30: below 0x200) */
		uint32_t a = txb[4] | txb[5] << 8 | txb[6] << 16 | (uint32_t)txb[7] << 24; uint8_t l = txb[8], r[251];
		uint32_t lim = model == 30 ? 0x200 : 0x3000;
		if (a == 0x1a0400) { for (int i = 0; i < l; i++) r[i] = (uint8_t)(0xa0 + i); cc(op, r, l, 0); break; }
		if (a + l > lim) { cc(op, NULL, 0, 0x12); break; }
		for (int i = 0; i < l; i++) r[i] = (uint8_t)((a + i) * 7); cc(op, r, l, 0); break; }
	case 0xfd54: if (model == 32) break; cc(op, NULL, 0, model == 31 ? 0x12 : 0x01); break;
	case 0x2003: cc(op, le_feat, model == 23 ? 6 : 8, 0); break;      /* model 23: short reply */
	case 0xfd3c: if (model == 20) { cc(op, NULL, 0, 0x01); break; } if (plen == 8) memcpy(le_feat, txb + 4, 8); cc(op, NULL, 0, 0); break;      /* model 20: unknown command */
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
static const struct sbu_cand cand = { .hz = CLK, .osc = false, .layout = "test" };
static void run_setup(int m, int fast)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; sbu.clk_hz = CLK; sbu.baud = 115200; sbu.rate = CLK;
	rxh = rxt = ntx = 0; npk = 0; pkoff = 0; model = m; patched = 0; chip_baud = 115200; powered_cycles = 0; fc4c_seen = 0; regon = 1; bd_set = 0; memcpy(le_feat, le_factory, 8); if (m == 22) le_feat[1] &= (uint8_t)~0x20; nregs = 0; ms_total = 0; nus = 0;
	host_ubrdiv = 105; host_frac = 11; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; knob_fast = fast; rw_err = 0; knob_pr = 0;
}
static const uint8_t *pk_at(int i, int *len) { size_t o = 0; for (int k = 0; k < i; k++) o += pkidx[k].len; *len = pkidx[i].len; return pkdata + o; }
static uint16_t op_at(int i) { int l; const uint8_t *p = pk_at(i, &l); return (uint16_t)(p[1] | (p[2] << 8)); }
static int count_op(uint16_t op) { int c = 0; for (int i = 0; i < npk; i++) if (op_at(i) == op) c++; return c; }

#ifndef T_NO_MAIN
int main(void)
{
	int e, i0;
	synth_image(40);
/* patch 0285 (review F1): after a VERIFIED patchram download sbu_vsc_attach sends exactly one read-only 0xfd53 (01 53 fd 00) as the LAST packet. Assert that, then drop it so the "last packet" checks keep their meaning. */
#define VSC_STRIP() do { if (has("BT UART: patchram download verified")) { int l_ = 0; const uint8_t *p_ = npk > 0 ? pk_at(npk - 1, &l_) : NULL; static const uint8_t w_[4] = { 0x01, 0x53, 0xfd, 0x00 }; \
	CHECK(count_op(0xfd53) == 1); CHECK(p_ != NULL && l_ == 4 && memcmp(p_, w_, 4) == 0); if (p_ != NULL && l_ == 4 && memcmp(p_, w_, 4) == 0) npk--; } else { CHECK(count_op(0xfd53) == 0); } } while (0)
#define ATT(m, pr, lf) do { run_setup(m, 0); knob_pr = (pr); knob_lf = (lf); e = sbu_patchram_attach(&cand); VSC_STRIP(); } while (0)
#define FIRST_AFTER_BD() ({ int k = -1; for (int i = 0; i < npk; i++) if (op_at(i) == 0x1009) k = i + 1; k; })
	/* 1. knob on: read, write bit 13 cleared (every other byte unchanged), Reset, read back */
	ATT(0, 1, 1);
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified"));
	i0 = FIRST_AFTER_BD(); CHECK(i0 > 0 && npk == i0 + 4);
	CHECK(op_at(i0) == 0x2003 && op_at(i0 + 1) == 0xfd3c && op_at(i0 + 2) == 0x0c03 && op_at(i0 + 3) == 0x2003);
	{ int l; const uint8_t *p = pk_at(i0 + 1, &l); static const uint8_t want[12] = { 0x01, 0x3c, 0xfd, 0x08, 0xef, 0x59, 0xff, 0xff, 0x0e, 0x00, 0x00, 0x00 }; CHECK(l == 12 && memcmp(p, want, 12) == 0); }
	CHECK(le_feat[1] == 0x59); CHECK(memcmp(le_feat + 2, le_factory + 2, 6) == 0 && le_feat[0] == le_factory[0]);
	CHECK(has("BT LEFEAT: before ef 79 ff ff 0e 00 00 00 (bit 13 Periodic Advertising set)")); CHECK(has("BT LEFEAT: after reset ef 59 ff ff 0e 00 00 00 (bit 13 clear)"));
	CHECK(has("BT LEFEAT: done: LE feature bit 13 cleared in the controller and held across HCI Reset")); CHECK(!has("BT GAP: sweetbtlefeat")); CHECK(powered_cycles == 0);
	/* 2. knob off: not one LE command after the patchram sequence */
	ATT(0, 1, 0);
	CHECK(e == 0); CHECK(count_op(0x2003) == 0 && count_op(0xfd3c) == 0); CHECK(!has("BT LEFEAT")); CHECK(!has("sweetbtlefeat"));
	/* 3. bit already clear: read only, nothing written */
	ATT(22, 1, 1);
	CHECK(e == 0); CHECK(count_op(0x2003) == 1 && count_op(0xfd3c) == 0 && count_op(0x0c03) == 2); CHECK(has("BT LEFEAT: bit 13 already clear: nothing written"));
	/* 4. 0xfd3c refused: loud, no Reset/read after it, attach still lets bluetoothd start on the patched controller (no power-cycle) */
	ATT(20, 1, 1);
	CHECK(e == 0); CHECK(count_op(0xfd3c) == 1); CHECK(op_at(npk - 1) == 0xfd3c); CHECK(has("patchram step le-write-features op=0xfd3c: status 0x01")); CHECK(has("BT GAP: sweetbtlefeat FAILED")); CHECK(powered_cycles == 0); CHECK(!has("BT LEFEAT: done"));
	/* 5. the value does not hold across Reset: GAP names it */
	ATT(21, 1, 1);
	CHECK(e == 0); CHECK(has("bit 13 is set again after the Reset")); CHECK(has("BT GAP: sweetbtlefeat FAILED")); CHECK(!has("BT LEFEAT: done"));
	/* 6. short 0x2003 reply: nothing written */
	ATT(23, 1, 1);
	CHECK(e == 0); CHECK(count_op(0xfd3c) == 0); CHECK(has("0x2003 reply has 6 return bytes (need 8); nothing written")); CHECK(has("BT GAP: sweetbtlefeat FAILED"));
	/* 7. patchram fails (launch never answered): lefeat loud as ignored, nothing LE sent */
	ATT(3, 1, 1);
	CHECK(count_op(0x2003) == 0 && count_op(0xfd3c) == 0); CHECK(has("BT GAP: sweetbtlefeat (on) ignored: patchram failed"));
	/* 8. patchram refused (empty image): lefeat loud as ignored */
	hcdimg_set(img, 0, "none"); ATT(0, 1, 1);
	CHECK(e == 0); CHECK(npk == 0); CHECK(has("BT GAP: sweetbtlefeat (on) ignored: patchram refused"));
	/* 9. romread off: no 0xfc4d, no 0xfd54 */
	synth_image(40); ATT(0, 1, 1);
	CHECK(count_op(0xfc4d) == 0 && count_op(0xfd54) == 0); CHECK(!has("BT ROM"));
	/* 10. romread pass P on the patched controller: fd54 probe (no handler) + patch head read; no write command beyond the stock sequence */
	run_setup(0, 0); knob_pr = 1; knob_lf = 0; knob_rr = 1; e = sbu_patchram_attach(&cand); VSC_STRIP();
	CHECK(e == 0); CHECK(count_op(0xfd54) == 1); CHECK(has("BT ROM: fd54 probe sub=0xee: error 0 status 0x01 (Unknown HCI Command: no 0xfd54 handler)"));
	CHECK(has("BT ROM: patchhead 001a0400 a0a1a2a3")); CHECK(count_op(0xfc4d) == 1);
	{ int l; const uint8_t *p = pk_at(npk - 1, &l); CHECK(l == 9 && p[1] == 0x4d && p[2] == 0xfc && p[3] == 5 && p[4] == 0x00 && p[5] == 0x04 && p[6] == 0x1a && p[7] == 0x00 && p[8] == 32); }
	run_setup(31, 0); knob_pr = 1; knob_rr = 1; e = sbu_patchram_attach(&cand); VSC_STRIP();
	CHECK(has("status 0x12 (a 0xfd54 handler exists)"));
	/* 11. pass R: probes + dump until the fake ROM refuses; rows exact; trace quiet restored */
	run_setup(0, 0); knob_rr = 1; sbu_romread_rom();
	CHECK(has("BT ROM: probe 00000000 00070e15")); CHECK(has("BT GAP: sweetbtromread: probe 00010000 refused: error 0 status 0x12"));
	CHECK(has("BT ROM: d 00000000 00070e15")); CHECK(has("BT ROM: d 00002f80 ")); CHECK(!has("BT ROM: d 00003000"));
	CHECK(has("sweetbtromread: dump stopped at 00003000: error 0 status 0x12")); CHECK(has("BT ROM: dump done rows=96 bytes=12288 end=00003000 quiet_transfers=")); CHECK(has("BT ROM: window 00000000..00080000"));
	CHECK(!sbu_trace_quiet); CHECK(!has("BT HCI: tx 9 bytes: 01 4d fc"));
	{ int bad = 0; for (int i = 0; i < npk; i++) { uint16_t o = op_at(i); if (o != 0xfc4d) bad++; } CHECK(bad == 0); }      /* pass R sends only Read_RAM */
	run_setup(30, 0); knob_rr = 1; sbu_romread_rom();
	CHECK(has("BT ROM: dump done rows=4 bytes=512 end=00000200"));
	/* 12. window start from sweetbtromoff (aligned down) */
	run_setup(0, 0); knob_rr = 1; knob_romoff = 0x1010; sbu_romread_rom(); knob_romoff = 0;
	CHECK(has("BT ROM: window 00001000..00081000")); CHECK(has("BT ROM: d 00001000 ")); CHECK(!has("BT ROM: d 00000f80 ")); CHECK(has("dump done rows=64 "));
	/* 13. 0xfd54 without an answer: GAP + an HCI Reset check, no patch-head read */
	run_setup(32, 0); knob_pr = 1; knob_rr = 1; e = sbu_patchram_attach(&cand); VSC_STRIP();
	CHECK(has("BT GAP: sweetbtromread: 0xfd54 probe got no answer")); CHECK(op_at(npk - 1) == 0x0c03 || op_at(npk - 1) == 0x2003 || op_at(npk - 1) == 0xfd3c);
	CHECK(!has("BT ROM: patchhead"));
	/* 14. mode 2: whole range into the buffer until the fake ROM refuses; bytes exact; published length; no hex rows */
	run_setup(0, 0); rom_pub = 0xffffffffu; memset(rom_buf_t, 0, sizeof rom_buf_t); sbu_romread_buf();
	CHECK(rom_pub == 0x3000); CHECK(rom_buf_t[0] == 0 && rom_buf_t[1] == 7 && rom_buf_t[0x2fff] == (uint8_t)(0x2fff * 7));
	CHECK(has("sweetbtromread=2: read stopped at 00003000")); CHECK(has("BT ROM: buffer done bytes=12288 fnv1a=0x")); CHECK(!has("BT ROM: d ")); CHECK(!sbu_trace_quiet);
	/* 15. patch 0295: sweetbt / sweetbtkeepbaud / sweetbtpatchram / sweetbtlefeat default ON: absent = on, =1 = on, =0 = off + one BT GAP naming the knob, other value = on + BT GAP */
	{
		int *kn[4] = { &knob_bt, &knob_kb, &knob_pr, &knob_lf };
		static const char *nm[4] = { "sweetbt", "sweetbtkeepbaud", "sweetbtpatchram", "sweetbtlefeat" };
		for (int k = 0; k < 4; k++) {
			for (int j = 0; j < 4; j++) *kn[j] = -1;
			loglen = 0; logbuf[0] = 0; CHECK(sbu_knob_default_on(nm[k]) == 1); sbu_defaults_log(); CHECK(loglen == 0);      /* absent: on, silent */
			*kn[k] = 1; loglen = 0; logbuf[0] = 0; CHECK(sbu_knob_default_on(nm[k]) == 1); sbu_defaults_log(); CHECK(loglen == 0);
			*kn[k] = 0; loglen = 0; logbuf[0] = 0; CHECK(sbu_knob_default_on(nm[k]) == 0); sbu_defaults_log();
			{ char want[96]; snprintf(want, sizeof want, "BT GAP: %s=0: default-on knob switched off by boot-arg", nm[k]); CHECK(has(want)); CHECK(strstr(strstr(logbuf, want) + 1, "BT GAP:") == NULL); }      /* exactly one line */
			*kn[k] = 7; loglen = 0; logbuf[0] = 0; CHECK(sbu_knob_default_on(nm[k]) == 1); sbu_defaults_log();
			{ char want[96]; snprintf(want, sizeof want, "BT GAP: %s=7 is not 0 or 1: treated as on", nm[k]); CHECK(has(want)); }
		}
		for (int j = 0; j < 4; j++) *kn[j] = -1;
	}
	/* 16. patchram + lefeat with BOTH boot-args absent (the new default): patchram runs and the LE step follows; with sweetbtlefeat=0 only the LE step is gone; with sweetbtpatchram=0 attach is not called but lefeat_unused is loud */
	ATT(0, -1, -1);
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(has("BT LEFEAT: done: LE feature bit 13 cleared")); CHECK(le_feat[1] == 0x59);
	ATT(0, -1, 0);
	CHECK(e == 0); CHECK(has("BT UART: patchram download verified")); CHECK(count_op(0x2003) == 0 && count_op(0xfd3c) == 0); CHECK(!has("BT LEFEAT"));
	run_setup(0, 0); knob_pr = 0; knob_lf = -1; loglen = 0; logbuf[0] = 0; sbu_lefeat_unused("sweetbtpatchram is off"); CHECK(has("BT GAP: sweetbtlefeat (on) ignored: sweetbtpatchram is off"));
	run_setup(0, 0); knob_pr = 0; knob_lf = 0; loglen = 0; logbuf[0] = 0; sbu_lefeat_unused("sweetbtpatchram is off"); CHECK(loglen == 0);
	knob_pr = 0; knob_lf = 0;
	fputs(logbuf, stdout);
	printf("RESULT: %s sprintbt-lefeat-host cases=16\n", fails ? "FAIL" : "PASS");
	return fails != 0;
}
#endif
