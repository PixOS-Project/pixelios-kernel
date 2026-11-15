/* Host test of patch 0285 (BT VSC adapter, sprint-bt bt-proper; spec docs/research/bt-vsc-adapter-spec.md R5): the REAL sweet_bt_uart.c (patches 0189 .. 0277 + 0285) with a fake register file, a fake BCM4389 that
 * answers 0xfd53 (capabilities) and 0xfd54 (Broadcom multi-adv, replies status + sub-op as AOSP ble_advertiser_hci_interface.cc:60-68 expects) and a fake kctl client (bluetoothd) that records every byte it gets.
 * No phone, no network. Last line: RESULT: PASS sweet-bt-vsc cases=N */
#include "kstub.h"
#include "sweet_bt.c"
#include "sweet_bt_uart.c"
#undef printf
static char logbuf[1 << 20]; static size_t loglen;
int sbt_printf(const char *fmt, ...) { va_list ap; va_start(ap, fmt); int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap); va_end(ap); if (n > 0) loglen += (size_t)n; return n; }
static uint8_t app[1 << 16]; static size_t napp;        /* what bluetoothd got */
errno_t ctl_enqueuedata(kern_ctl_ref r, u_int32_t u, void *d, size_t l, u_int32_t f) { (void)r;(void)u;(void)f; memcpy(app + napp, d, l); napp += l; return 0; }
void mbuf_freem(mbuf_t m) { (void)m; }
int tsleep(void *c, int p, const char *w, int t) { (void)c;(void)p;(void)w;(void)t; return 0; }
void delay(int us) { (void)us; }
bool sweet_usb_board_is_gs101(void) { return true; }
static struct { const char *n; int v; } args[16]; static int nargs;      /* boot-args set by the test */
static void arg_clear(void) { nargs = 0; }
static void arg_set(const char *n, int v) { args[nargs].n = n; args[nargs++].v = v; }
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l;
	for (int i = 0; i < nargs; i++) if (!strcmp(args[i].n, n)) { *(int *)v = args[i].v; return 1; }
	return 0; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
uint64_t mach_absolute_time(void) { static uint64_t t; return t += 3000000u; }      /* patch 0296: 3 ms per call */
void absolutetime_to_nanoseconds(uint64_t a, uint64_t *n) { *n = a; }
void thread_terminate_self(void) { }
void thread_deallocate(void *t) { (void)t; }
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { (void)r; *ref = (kern_ctl_ref)1; return 0; }
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; (void)c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; (void)p; return 1; }
static uint8_t rom_buf_t[1 << 14];
uint8_t *sweet_bt_rom_buffer(uint32_t n) { return n <= sizeof(rom_buf_t) ? rom_buf_t : NULL; }      /* patch 0277 symbols the backend links against */
void sweet_bt_rom_publish(uint32_t n) { (void)n; }

#define CLK 196646400u
static uint8_t rxq[1 << 16]; static int rxh, rxt;
static unsigned rx_limit = 1u << 30;                    /* bytes the FIFO yields until the test raises it again (partial deliveries) */
static uint8_t chip[1 << 16]; static size_t nchip;      /* every byte the chip got */
static uint8_t txb[300 + 70000]; static int ntx;
static int rx_err_once;
static struct { uint16_t op; uint8_t sub; } chip_ops[256]; static int nchip_ops;      /* every command the chip got: opcode + first parameter byte */
static int chip_ncmd = 1, tx_full;      /* Num_HCI_Command_Packets in the chip's 0xfd54 replies; tx_full: the uart tx fifo never drains */
static int cap_mode, cap_max = 16, fd54_status, fd54_cs, hold_answers;      /* cap_mode 0 ok, 1 silent, 2 status 0x01, 3 status only, 4 error status; fd54_cs: answer 0xfd54 with a Command Status */
static void push(const uint8_t *b, int n) { memcpy(rxq + rxt, b, n); rxt += n; }
static void answer(void)
{
	uint16_t op = txb[1] | (txb[2] << 8);
	uint8_t e[20] = { 0x04, 0x0e, 0, 0x01, txb[1], txb[2] }; int k = 6;
	chip_ops[nchip_ops].op = op; chip_ops[nchip_ops++].sub = txb[3] ? txb[4] : 0;
	if (hold_answers) return;
	if (op == 0xfd53) {
		if (cap_mode == 1) return;
		e[k++] = cap_mode == 2 ? 0x01 : cap_mode == 4 ? 0x0c : 0x00;
		if (cap_mode == 0 || cap_mode == 4) { e[k++] = (uint8_t)cap_max; e[k++] = 0x01; e[k++] = 0x20; }
	} else if (op == 0xfd54) {
		if (fd54_cs) { uint8_t s[7] = { 0x04, 0x0f, 0x04, (uint8_t)fd54_status, (uint8_t)chip_ncmd, 0x54, 0xfd }; push(s, 7); return; }
		e[3] = (uint8_t)chip_ncmd; e[k++] = (uint8_t)fd54_status; e[k++] = txb[4];             /* AOSP: status, sub-op */
		if (txb[4] == 0x01) e[k++] = txb[4 + 23];                    /* set-param also returns the tx power it used */
	} else {
		e[k++] = 0x00;
	}
	e[2] = (uint8_t)(k - 3);
	push(e, k);
}
static uint32_t f_rd(uint32_t pa)
{
	int cnt = rxt - rxh; if ((unsigned)cnt > rx_limit) cnt = (int)rx_limit;
	if (pa == 0x175B0000u + U_UERSTAT) { int e = rx_err_once; rx_err_once = 0; return e ? 0x1u : 0; }
	if (pa == 0x175B0000u + U_UFSTAT) return (cnt > 255 ? 255 : (uint32_t)cnt) | (tx_full ? (1u << 24) : 0);
	if (pa == 0x175B0000u + U_UTRSTAT) return 4u | (cnt > 0);
	if (pa == 0x175B0000u + U_URXH) { if (cnt > 0) { rx_limit--; return rxq[rxh++]; } return 0; }
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	if (pa != 0x175B0000u + U_UTXH) return;
	chip[nchip++] = (uint8_t)v; txb[ntx++] = (uint8_t)v;
	if (txb[0] == 0x01 && ntx >= 4 && ntx == 4 + txb[3]) { answer(); ntx = 0; }
	else if (txb[0] == 0x02 && ntx >= 5 && ntx == 5 + (txb[3] | (txb[4] << 8))) ntx = 0;      /* ACL: no answer */
}
static void f_us(unsigned us) { (void)us; }
static void f_ms(unsigned ms) { (void)ms; }
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static int fails, cases;
static int has(const char *s) { return strstr(logbuf, s) != NULL; }
#define CASE(name) do { cases++; (void)(name); } while (0)

static const uint8_t A[6] = { 0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6 }, B[6] = { 0xb1, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6 };
static void txw(const void *p, size_t n) { CHECK(sbu_tx_kctl(p, n) == 0); }
static void poll(void) { (void)sbu_rx_poll(); }
static int app_is(size_t from, const void *e, size_t n) { return napp - from == n && memcmp(app + from, e, n) == 0; }
static int chip_is(size_t from, const void *e, size_t n) { return nchip - from == n && memcmp(chip + from, e, n) == 0; }

/* fresh world: sbu + adapter statics zeroed, the chip answers 0xfd53 with max_adv_inst = cap */
static void world(int cap)
{
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms }; sbu.powered = sbu.uart_ok = true; sbu.clk_hz = CLK; sbu.baud = 115200; sbu.rate = CLK;
	rxh = rxt = ntx = 0; nchip = napp = 0; nchip_ops = 0; loglen = 0; logbuf[0] = 0; sbu_trace_n = 0; rx_err_once = 0; rx_limit = 1u << 30; hold_answers = 0;
	cap_mode = 0; cap_max = cap; fd54_status = 0; fd54_cs = 0; chip_ncmd = 1; tx_full = 0; arg_clear();
	sbu_rw200b_on = sbu_rw200c_on = sbu_nopadv_on = sbu_nopcf_on = sbu_nopcf_rx = sbu_pubscan_on = false; sbu_rw_lost = false; sbu_rw_off = 0; sbu_tx_off = 0; sbu_pcf_nhold = 0; sbu_pcf_drop = false; sbu_pcf_qh = sbu_pcf_qt = 0;
	sbu_vsc_on = sbu_vsc_ok = false; sbu_vsc_max = 0; memset(sbu_vsc_in, 0, sizeof(sbu_vsc_in)); sbu_vsc_ph = sbu_vsc_pn = sbu_vsc_ah = sbu_vsc_an = 0; sbu_vsc_lk = 0;
	sbu_vsc_toff = sbu_vsc_thlen = sbu_vsc_tneed = 0; sbu_vsc_ttype = 0; sbu_vsc_rxn = sbu_vsc_rneed = 0; sbu_vsc_logn = 0;
	sbu_vsc_ncreate = sbu_vsc_nremove = sbu_vsc_nxlate = sbu_vsc_nok = sbu_vsc_nfail = sbu_vsc_nlocal = sbu_vsc_nrefuse = sbu_vsc_nstray = sbu_vsc_norphan = 0;
	sbt.ref[0] = (kern_ctl_ref)1; sbt.busy[0] = 1;
}
static void start(int cap) { world(cap); sbu_vsc_attach(); }

/* ---- command builders (Apple layouts [bin bt-platform-chipset-select.md s.6]) and expected bytes ---- */
static size_t b_create(uint8_t *o, const uint8_t *addr, uint8_t t) { uint8_t h[] = { 0x01, 0x48, 0xfd, 0x07, t }; memcpy(o, h, 5); memcpy(o + 5, addr, 6); return 11; }
static size_t b_remove(uint8_t *o, uint8_t id) { uint8_t h[] = { 0x01, 0x49, 0xfd, 0x01, id }; memcpy(o, h, 5); return 5; }
static size_t b_params(uint8_t *o, uint8_t id, const uint8_t *prm) { uint8_t h[] = { 0x01, 0x4a, 0xfd, 0x08, id }; memcpy(o, h, 5); memcpy(o + 5, prm, 7); return 12; }
static size_t b_data(uint8_t *o, unsigned op, uint8_t id, uint8_t len, uint8_t seed) { uint8_t h[] = { 0x01, (uint8_t)(op & 0xff), 0xfd, 33, id, len }; memcpy(o, h, 6); for (int i = 0; i < 31; i++) o[6 + i] = (uint8_t)(seed + i); return 37; }
static size_t b_enable(uint8_t *o, uint8_t id, uint8_t en, uint32_t d, uint8_t m) { uint8_t h[] = { 0x01, 0x4d, 0xfd, 0x07, id, en, (uint8_t)d, (uint8_t)(d >> 8), (uint8_t)(d >> 16), (uint8_t)(d >> 24), m }; memcpy(o, h, 11); return 11; }
static size_t b_upd(uint8_t *o, uint8_t id, uint8_t t, const uint8_t *addr) { uint8_t h[] = { 0x01, 0x4e, 0xfd, 0x08, id, t }; memcpy(o, h, 6); memcpy(o + 6, addr, 6); return 12; }
static const uint8_t PRM[7] = { 0x00, 0x08, 0x00, 0x08, 0x00, 0x07, 0x00 };      /* min 0x0800 max 0x0800 (1.28 s) type 0 (ADV_IND) chmap 7 filter 0 */
/* the translated Broadcom commands [src AOSP ble_advertiser_hci_interface.cc] */
static size_t x_sub1(uint8_t *o, uint8_t id, const uint8_t *prm, uint8_t t, const uint8_t *addr)
{
	uint8_t h[] = { 0x01, 0x54, 0xfd, 24, 0x01 }; memcpy(o, h, 5); memcpy(o + 5, prm, 4); o[9] = prm[4]; o[10] = t; memcpy(o + 11, addr, 6);
	memset(o + 17, 0, 7); o[24] = prm[5]; o[25] = prm[6]; o[26] = id; o[27] = 0xf9;      /* peer type 0, peer address 0, chmap, filter, id, tx power -7 dBm */
	return 28;
}
static size_t x_data(uint8_t *o, uint8_t sub, uint8_t id, uint8_t len, uint8_t seed) { uint8_t h[] = { 0x01, 0x54, 0xfd, 34, sub, len }; memcpy(o, h, 6); for (int i = 0; i < 31; i++) o[6 + i] = (uint8_t)(seed + i); o[37] = id; return 38; }
static size_t x_en(uint8_t *o, uint8_t id, uint8_t en) { uint8_t h[] = { 0x01, 0x54, 0xfd, 3, 0x05, en, id }; memcpy(o, h, 7); return 7; }
static size_t x_addr(uint8_t *o, uint8_t id, const uint8_t *addr) { uint8_t h[] = { 0x01, 0x54, 0xfd, 8, 0x04 }; memcpy(o, h, 5); memcpy(o + 5, addr, 6); o[11] = id; return 12; }
static size_t a_cc(uint8_t *o, uint16_t op, uint8_t st) { uint8_t e[] = { 0x04, 0x0e, 0x04, 0x01, (uint8_t)(op & 0xff), (uint8_t)(op >> 8), st }; memcpy(o, e, 7); return 7; }
/* a chip reply rewritten to an Apple opcode: Num_HCI_Command_Packets is the chip's */
static size_t a_rw(uint8_t *o, uint16_t op, uint8_t st) { a_cc(o, op, st); o[3] = (uint8_t)chip_ncmd; return 7; }
static size_t a_create(uint8_t *o, uint8_t st, uint8_t id) { uint8_t e[] = { 0x04, 0x0e, 0x05, 0x01, 0x48, 0xfd, st, id }; memcpy(o, e, 8); return 8; }

/* send one command (one write), poll, and compare the chip's bytes and bluetoothd's bytes; nc/na == 0 = nothing expected */
static void step(const void *cmd, size_t n, const void *ce, size_t nc, const void *ae, size_t na, int line)
{
	size_t c0 = nchip, a0 = napp;
	CHECK(sbu_tx_kctl(cmd, n) == 0); poll();
	if (!chip_is(c0, ce, nc)) { printf("FAIL line %d: chip bytes (%zu, want %zu)\n", line, nchip - c0, nc); fails++; }
	if (!app_is(a0, ae, na)) { printf("FAIL line %d: bluetoothd bytes (%zu, want %zu)\n", line, napp - a0, na); fails++; }
}
#define STEP(cmd, n, ce, nc, ae, na) step(cmd, n, ce, nc, ae, na, __LINE__)
static void snd(const void *c, size_t n) { txw(c, n); poll(); }

/* Create instance idx (1..) with address A, type 1: the id must be idx */
static void creates(int n)
{
	for (int i = 1; i <= n; i++) { uint8_t c[11], a[8]; STEP(c, b_create(c, A, 1), c, 0, a, a_create(a, 0, (uint8_t)i)); }
}
/* the lines of the BT VSC / sweetbtvsc rows must never carry an address byte (rule 4) */
static int rows_clean(void)
{
	char *l = logbuf; int ok = 1;
	while (l && *l) { char *e = strchr(l, '\n'); size_t m = e ? (size_t)(e - l) : strlen(l); char row[512]; if (m >= sizeof(row)) m = sizeof(row) - 1; memcpy(row, l, m); row[m] = 0;
		if ((!strncmp(row, "BT VSC:", 7) || strstr(row, "sweetbtvsc")) && (strstr(row, "a1 a2") || strstr(row, "b1 b2") || strstr(row, "a1a2") || strstr(row, "0xa1") || strstr(row, "0xb1"))) ok = 0;
		l = e ? e + 1 : NULL; }
	return ok;
}

int main(void)
{
	/* 1. R1 caps ok: exactly 01 53 fd 00 goes out, adapter on, rows */
	CASE("caps ok"); start(16);
	{ static const uint8_t q[] = { 0x01, 0x53, 0xfd, 0x00 }; CHECK(chip_is(0, q, 4)); }
	CHECK(sbu_vsc_on && sbu_vsc_max == 16); CHECK(has("BT VSC: caps max_adv_inst=16\n")); CHECK(has("BT VSC: adapter ON")); CHECK(!has("BT GAP"));
	CHECK(nchip_ops == 1 && chip_ops[0].op == 0xfd53);
	CASE("caps clamp"); start(255); CHECK(sbu_vsc_on && sbu_vsc_max == SBU_VSC_NID); CHECK(has("clamped to 32 instance ids"));
	CASE("caps minimum"); start(5); CHECK(sbu_vsc_on && sbu_vsc_max == 5);

	/* 2. caps too small / too few / error / silent: BT GAP naming the value, adapter off */
	CASE("caps 4"); start(4); CHECK(!sbu_vsc_on); CHECK(has("BT VSC: caps max_adv_inst=4\n")); CHECK(has("BT GAP: sweetbtvsc: max_adv_inst=4 < 5"));
	CASE("caps too few"); world(16); cap_mode = 3; sbu_vsc_attach(); CHECK(!sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc: 0xfd53 reply has 0 return bytes"));
	CASE("caps error status"); world(16); cap_mode = 2; sbu_vsc_attach(); CHECK(!sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc: 0xfd53 status 0x01"));
	world(16); cap_mode = 4; sbu_vsc_attach(); CHECK(!sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc: 0xfd53 status 0x0c"));
	CASE("caps silent"); world(16); cap_mode = 1; sbu_vsc_attach(); CHECK(!sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc: 0xfd53 (LE Get Vendor Capabilities) got no Command Complete"));
	CASE("off passes 0xfd48"); { uint8_t c[11]; size_t c0 = nchip; b_create(c, A, 1); txw(c, 11); CHECK(chip_is(c0, c, 11)); CHECK(sbu_vsc_pn == 0 && sbu_vsc_an == 0); poll(); }

	/* 3. sweetbtvsc=0, a bad value, and the unused paths stay loud */
	CASE("knob off"); world(16); arg_set("sweetbtvsc", 0); sbu_vsc_attach(); CHECK(!sbu_vsc_on && nchip == 0); CHECK(has("BT VSC: sweetbtvsc=0: adapter off by boot-arg"));
	CASE("knob bad value"); world(16); arg_set("sweetbtvsc", 7); sbu_vsc_attach(); CHECK(sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc=7 ignored"));
	CASE("unused is loud"); world(16); sbu_vsc_unused("sweetbtpatchram=1 is not set"); CHECK(has("BT GAP: sweetbtvsc adapter not started: sweetbtpatchram=1 is not set"));
	world(16); arg_set("sweetbtvsc", 0); sbu_vsc_unused("patchram refused"); CHECK(has("BT VSC: sweetbtvsc=0: adapter off by boot-arg (patchram refused)")); CHECK(!has("BT GAP"));

	/* 4. knob conflict: each diagnostic rewrite knob refuses the adapter by name; nothing is sent */
	CASE("knob conflict");
	{ static const struct { const char *n; int v; } k[] = { { "sweetbtrw200b", 1 }, { "sweetbtrw200c", 1 }, { "sweetbtnopadv", 1 }, { "sweetbtnopcf", 1 }, { "sweetbtpubscan", 1 }, { "sweetbtpubscan", 2 } };
	  for (unsigned i = 0; i < sizeof(k) / sizeof(k[0]); i++) { char row[96]; world(16); arg_set(k[i].n, k[i].v); sbu_vsc_attach();
	    snprintf(row, sizeof(row), "diagnostic knob %s=%d", k[i].n, k[i].v); CHECK(!sbu_vsc_on); CHECK(has(row)); CHECK(nchip == 0); } }
	world(16); arg_set("sweetbtpubscan", 3); arg_set("sweetbtnopcf", 0); sbu_vsc_attach(); CHECK(sbu_vsc_on);      /* a knob that is not on (0 / ignored value) does not conflict */

	/* 5. four Creates -> ids 1..4, answered locally, nothing reaches the chip */
	CASE("creates"); start(16); creates(4); CHECK(nchip == 4); CHECK(sbu_vsc_ncreate == 4); CHECK(has("BT VSC: 0xfd48 Create -> local id 1 (row 1)")); CHECK(has("local id 4"));
	{ int tot = 0; for (int i = 1; i < 32; i++) tot += sbu_vsc_in[i].used; CHECK(tot == 4); CHECK(sbu_vsc_in[1].atype == 1 && memcmp(sbu_vsc_in[1].addr, A, 6) == 0); }
	CASE("create order with ACL");
	{ uint8_t w[31], acl[] = { 0x02, 0x01, 0x20, 0x04, 0x00, 1, 2, 3, 4 }, r[16], e[16]; start(16); size_t c0 = nchip, a0 = napp;
	  b_create(w, A, 1); memcpy(w + 11, acl, 9); b_create(w + 20, B, 1); txw(w, 31); CHECK(chip_is(c0, acl, 9)); poll();
	  a_create(r, 0, 1); a_create(e, 0, 2); memcpy(r + 8, e, 8); CHECK(app_is(a0, r, 16)); }

	/* 6. full chain byte-exact: Create, SetParams, AdvData, ScanRsp, UpdateAddress (same type), Enable; chip replies rewritten byte-exact; every chip command is 0xfd53 once then 0xfd54 sub 1..5 */
	CASE("chain"); start(16); creates(1); chip_ncmd = 3;
	{ uint8_t c[40], x[40], r[8]; size_t n;
	  n = b_params(c, 1, PRM); STEP(c, n, x, x_sub1(x, 1, PRM, 1, A), r, a_rw(r, 0xfd4a, 0)); CHECK(sbu_vsc_in[1].params_set && !sbu_vsc_in[1].enabled);
	  n = b_data(c, 0xfd4b, 1, 11, 0x40); STEP(c, n, x, x_data(x, 0x02, 1, 11, 0x40), r, a_rw(r, 0xfd4b, 0));
	  n = b_data(c, 0xfd4c, 1, 7, 0x60); STEP(c, n, x, x_data(x, 0x03, 1, 7, 0x60), r, a_rw(r, 0xfd4c, 0));
	  n = b_upd(c, 1, 1, B); STEP(c, n, x, x_addr(x, 1, B), r, a_rw(r, 0xfd4e, 0)); CHECK(memcmp(sbu_vsc_in[1].addr, B, 6) == 0);
	  n = b_enable(c, 1, 1, 0, 0); STEP(c, n, x, x_en(x, 1, 1), r, a_rw(r, 0xfd4d, 0)); CHECK(sbu_vsc_in[1].enabled);
	  n = b_enable(c, 1, 0, 0, 0); STEP(c, n, x, x_en(x, 1, 0), r, a_rw(r, 0xfd4d, 0)); CHECK(!sbu_vsc_in[1].enabled);
	  n = b_remove(c, 1); STEP(c, n, x, 0, r, a_cc(r, 0xfd49, 0)); CHECK(!sbu_vsc_in[1].used);      /* review F7: the instance is disabled, so Remove is local (AOSP) */
	  CHECK(nchip_ops == 7 && chip_ops[0].op == 0xfd53); for (int i = 1; i < nchip_ops; i++) CHECK(chip_ops[i].op == 0xfd54 && chip_ops[i].sub >= 1 && chip_ops[i].sub <= 5);
	  CHECK(sbu_vsc_pn == 0); CHECK(has("BT HCI: rx-vsc 7 bytes: 04 0e 04 03 4a fd 00")); CHECK(has("BT HCI: rx-vsc 8 bytes: 04 0e 05 01 48 fd 00 01")); CHECK(has("BT VSC: 0xfd4a id 1 -> 0xfd54 sub 0x01 sent")); CHECK(has("BT VSC: 0xfd4a id 1 <- 0xfd54 sub 0x01 chip status 0x00")); CHECK(has("BT VSC: 0xfd49 Remove id 1: not enabled, freed locally (no chip command)")); CHECK(!has("BT VSC: 0xfd49 id 1 -> 0xfd54")); CHECK(rows_clean()); }
	CASE("remove frees, lowest id reused"); start(16); creates(3);
	{ uint8_t c[16], x[16], r[16]; size_t n = b_remove(c, 2); STEP(c, n, x, 0, r, a_cc(r, 0xfd49, 0)); CHECK(!sbu_vsc_in[2].used); STEP(c, b_create(c, B, 1), x, 0, r, a_create(r, 0, 2)); }
	/* review F7: only an ENABLED instance gets the [05][00][id] disable; one that is idle is freed with no chip command, and a second Remove is an unknown id */
	CASE("remove of an enabled instance disables on the chip"); start(16); creates(2);
	{ uint8_t c[16], x[16], r[16], rr[16]; size_t n, n0;
	  n = b_enable(c, 1, 1, 0, 0); STEP(c, n, x, x_en(x, 1, 1), r, a_rw(r, 0xfd4d, 0)); CHECK(sbu_vsc_in[1].enabled);
	  n = b_remove(c, 1); STEP(c, n, x, x_en(x, 1, 0), r, a_rw(r, 0xfd49, 0)); CHECK(!sbu_vsc_in[1].used); CHECK(has("BT VSC: 0xfd49 id 1 -> 0xfd54 sub 0x05 sent"));
	  n0 = nchip; n = b_remove(c, 2); STEP(c, n, x, 0, r, a_cc(r, 0xfd49, 0)); CHECK(nchip == n0); CHECK(!sbu_vsc_in[2].used); CHECK(sbu_vsc_nremove == 2); CHECK(has("BT VSC: 0xfd49 Remove id 2: not enabled, freed locally (no chip command)"));
	  n = b_remove(c, 2); STEP(c, n, x, 0, rr, a_cc(rr, 0xfd49, 0x12)); CHECK(has("unknown instance id 2")); }
	/* review F2: an adapter stopped (by the rx thread) after the caller's check translates nothing and leaves bluetoothd's bytes to the pass-through */
	CASE("cmd after stop passes"); start(16); creates(1);
	{ uint8_t c[40], sub[44]; unsigned sn = 0; size_t n = b_params(c, 1, PRM); size_t c0 = nchip; sbu_vsc_on = false; CHECK(sbu_vsc_cmd(c, sub, &sn) == SBU_VSC_PASS); CHECK(sbu_vsc_pn == 0 && sbu_vsc_nxlate == 0 && sn == 0); CHECK(nchip == c0); (void)n; }

	/* 7. refusals: each is a local status plus a BT GAP row, nothing reaches the chip */
	CASE("refusals"); start(5); creates(4);
	{ uint8_t c[40], r[8], bad[7]; size_t n; const char *g = "BT GAP: sweetbtvsc: 0x";
	  STEP(c, b_create(c, A, 1), c, 0, r, a_create(r, 0x07, 0xff)); CHECK(has("local status 0x07: no free instance id (1..4 all used)"));
	  { uint8_t s[] = { 0x01, 0x48, 0xfd, 0x06, 1, 1, 2, 3, 4, 5 }; STEP(s, sizeof(s), c, 0, r, a_cc(r, 0xfd48, 0x12)); CHECK(has("0xfd48 refused with local status 0x12: plen 6 (need 7)")); }
	  { uint8_t s[] = { 0x01, 0x49, 0xfd, 0x00 }; STEP(s, sizeof(s), c, 0, r, a_cc(r, 0xfd49, 0x12)); CHECK(has("0xfd49 refused with local status 0x12: plen 0 (need 1)")); }
	  STEP(c, b_remove(c, 9), c, 0, r, a_cc(r, 0xfd49, 0x12)); CHECK(has("unknown instance id 9"));
	  STEP(c, b_remove(c, 0), c, 0, r, a_cc(r, 0xfd49, 0x12));
	  n = b_params(c, 7, PRM); STEP(c, n, c, 0, r, a_cc(r, 0xfd4a, 0x12));
	  memcpy(bad, PRM, 7); bad[4] = 1; n = b_params(c, 1, bad); STEP(c, n, c, 0, r, a_cc(r, 0xfd4a, 0x12)); CHECK(has("directed advertising type 1 needs a peer address"));
	  { static const struct { int off; uint8_t v0, v1; } br[] = { { 0, 0x1f, 0x00 }, { 0, 0x01, 0x40 }, { 2, 0x1f, 0x00 }, { 2, 0x01, 0x40 }, { 4, 4, 0 }, { 5, 0, 0 }, { 5, 8, 0 }, { 6, 4, 0 } };      /* min lo/hi, max lo/hi, type 4, chmap 0 / 8, filter 4 */
	    for (unsigned i = 0; i < sizeof(br) / sizeof(br[0]); i++) { memcpy(bad, PRM, 7); bad[br[i].off] = br[i].v0; if (br[i].off < 4) bad[br[i].off + 1] = br[i].v1; n = b_params(c, 1, bad); STEP(c, n, c, 0, r, a_cc(r, 0xfd4a, 0x12)); } }
	  CHECK(has("parameters out of range (min 0x1f max 0x800 type 0 chmap 7 filter 0)")); CHECK(has("parameters out of range (min 0x4001")); CHECK(!sbu_vsc_in[1].params_set);
	  n = b_data(c, 0xfd4b, 1, 32, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4b, 0x12)); CHECK(has("data length 32 > 31"));
	  n = b_data(c, 0xfd4c, 1, 255, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4c, 0x12));
	  n = b_data(c, 0xfd4b, 6, 4, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4b, 0x12));
	  n = b_data(c, 0xfd4c, 6, 4, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4c, 0x12));
	  n = b_enable(c, 6, 1, 0, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4d, 0x12));
	  n = b_enable(c, 1, 2, 0, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4d, 0x12)); CHECK(has("enable value 2"));
	  n = b_enable(c, 1, 1, 5, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4d, 0x11)); CHECK(has("duration/max-events fields are not zero"));
	  n = b_enable(c, 1, 1, 0, 3); STEP(c, n, c, 0, r, a_cc(r, 0xfd4d, 0x11));
	  n = b_enable(c, 1, 1, 0x01000000, 0); STEP(c, n, c, 0, r, a_cc(r, 0xfd4d, 0x11));
	  n = b_upd(c, 9, 1, B); STEP(c, n, c, 0, r, a_cc(r, 0xfd4e, 0x12));
	  CHECK(nchip == 4); CHECK(rows_clean()); CHECK(strstr(logbuf, g) != NULL); CHECK(sbu_vsc_pn == 0); CHECK(sbu_vsc_nrefuse >= 20); }
	CASE("update address"); start(16); creates(2);
	{ uint8_t c[40], x[40], r[8], y[40]; size_t n;
	  n = b_upd(c, 1, 1, B); STEP(c, n, x, 0, r, a_cc(r, 0xfd4e, 0x00)); CHECK(has("0xfd4e UpdateAddress id 1 stored (no params yet)"));       /* no params yet: stored, local 00 */
	  n = b_params(c, 1, PRM); STEP(c, n, x, x_sub1(x, 1, PRM, 1, B), r, a_cc(r, 0xfd4a, 0));                                                          /* the stored address goes out with the first SetParams */
	  n = b_upd(c, 1, 0, A); STEP(c, n, y, x_sub1(y, 1, PRM, 0, A), r, a_cc(r, 0xfd4e, 0)); CHECK(sbu_vsc_in[1].atype == 0);                               /* type changed: SetParams re-sent with the new type + address */
	  n = b_upd(c, 1, 0, B); STEP(c, n, y, x_addr(y, 1, B), r, a_cc(r, 0xfd4e, 0));                                                                   /* same type: sub 04 */
	  n = b_enable(c, 1, 1, 0, 0); STEP(c, n, y, x_en(y, 1, 1), r, a_cc(r, 0xfd4d, 0));
	  n = b_upd(c, 1, 0, A); STEP(c, n, y, 0, r, a_cc(r, 0xfd4e, 0x0c)); CHECK(has("instance 1 is enabled; bluetoothd disables first")); CHECK(memcmp(sbu_vsc_in[1].addr, B, 6) == 0); }

	/* 8. the chip refuses a translated command: its status reaches bluetoothd under the Apple opcode, no state change; a Command Status form too */
	CASE("chip refuses"); start(16); creates(1); fd54_status = 0x0c; chip_ncmd = 2;
	{ uint8_t c[40], x[40], r[8]; size_t n;
	  n = b_params(c, 1, PRM); STEP(c, n, x, x_sub1(x, 1, PRM, 1, A), r, a_rw(r, 0xfd4a, 0x0c)); CHECK(!sbu_vsc_in[1].params_set);
	  n = b_enable(c, 1, 1, 0, 0); STEP(c, n, x, x_en(x, 1, 1), r, a_rw(r, 0xfd4d, 0x0c)); CHECK(!sbu_vsc_in[1].enabled);
	  fd54_status = 0; n = b_enable(c, 1, 1, 0, 0); STEP(c, n, x, x_en(x, 1, 1), r, a_rw(r, 0xfd4d, 0)); CHECK(sbu_vsc_in[1].enabled); fd54_status = 0x0c;      /* an enabled instance, so Remove goes to the chip */
	  n = b_remove(c, 1); STEP(c, n, x, x_en(x, 1, 0), r, a_rw(r, 0xfd49, 0x0c)); CHECK(sbu_vsc_in[1].used); CHECK(sbu_vsc_nfail == 3);
	  fd54_status = 0; n = b_enable(c, 1, 0, 0, 0); STEP(c, n, x, x_en(x, 1, 0), r, a_rw(r, 0xfd4d, 0)); CHECK(!sbu_vsc_in[1].enabled);
	  fd54_status = 0x0c; fd54_cs = 1; { uint8_t e[] = { 0x04, 0x0f, 0x04, 0x0c, 0x02, 0x4a, 0xfd }; n = b_params(c, 1, PRM); STEP(c, n, x, x_sub1(x, 1, PRM, 1, A), e, 7); CHECK(sbu_vsc_pn == 0); }
	  fd54_cs = 0; fd54_status = 0; n = b_params(c, 1, PRM); STEP(c, n, x, x_sub1(x, 1, PRM, 1, A), r, a_rw(r, 0xfd4a, 0)); CHECK(sbu_vsc_in[1].params_set); }
	CASE("subcode mismatch"); start(16); creates(1);
	{ uint8_t c[40], e[7] = { 0x04, 0x0e, 0x04, 0x01, 0x54, 0xfd, 0x00 }, r[8]; size_t n = b_params(c, 1, PRM), a0; hold_answers = 1; snd(c, n); a0 = napp;
	  push(e, 7); poll(); a_cc(r, 0xfd4a, 0); CHECK(app_is(a0, r, 7)); CHECK(has("does not echo sub-op 0x01")); }

	/* 9. HCI Reset frees all state (and drops pending translations loudly); an unrelated command passes byte for byte */
	CASE("reset"); start(16); creates(2); { uint8_t c[40]; size_t n = b_params(c, 1, PRM); snd(c, n); }
	{ uint8_t rs[] = { 0x01, 0x03, 0x0c, 0x00 }, e[7]; a_cc(e, 0x0c03, 0); STEP(rs, 4, rs, 4, e, 7); CHECK(sbu_vsc_in[1].used == false && sbu_vsc_in[2].used == false); CHECK(has("BT VSC: HCI Reset seen: all instance state freed")); creates(1); }
	CASE("reset drops pending"); start(16); creates(1); hold_answers = 1;
	{ uint8_t c[40]; size_t n = b_params(c, 1, PRM); snd(c, n); CHECK(sbu_vsc_pn == 1); uint8_t rs[] = { 0x01, 0x03, 0x0c, 0x00 }; txw(rs, 4); CHECK(sbu_vsc_pn == 0); CHECK(has("HCI Reset with 1 translated 0xfd54 commands unanswered"));
	  uint8_t late[] = { 0x04, 0x0e, 0x05, 0x01, 0x54, 0xfd, 0x00, 0x01 }; size_t a0 = napp; push(late, 8); poll(); CHECK(app_is(a0, late, 8)); CHECK(has("BT GAP: sweetbtvsc: 0xfd54 reply (status 0x00) with no pending translation")); CHECK(sbu_vsc_nstray == 1); }

	/* 10. other commands and events pass byte for byte, in both directions (the sweetbtlefeat/0x2005/0xfd3c family, ACL both ways) */
	CASE("passthrough"); start(16); creates(1);
	{ static const uint8_t cmds[][12] = { { 0x01, 0x05, 0x20, 0x06, 1, 2, 3, 4, 5, 6 }, { 0x01, 0x3c, 0xfd, 0x08, 0xef, 0x79, 0xff, 0xff, 0x09, 0, 0, 0 }, { 0x01, 0x47, 0xfd, 0x03, 0, 0, 1 }, { 0x01, 0x54, 0xfd, 0x00 }, { 0x01, 0x4f, 0xfd, 0x00 }, { 0x01, 0x47, 0xfd, 0x00 } };
	  static const size_t ln[] = { 10, 12, 7, 4, 4, 4 };
	  for (unsigned i = 0; i < 5; i++) { size_t c0 = nchip; if (i == 3) hold_answers = 1; txw(cmds[i], ln[i]); poll(); CHECK(chip_is(c0, cmds[i], ln[i])); } hold_answers = 0; CHECK(sbu_vsc_pn == 0 && sbu_vsc_an == 0);
	  uint8_t acl[] = { 0x02, 0x40, 0x20, 0x09, 0x00, 1, 2, 3, 4, 5, 6, 7, 8, 9 }, ev[] = { 0x04, 0x3e, 0x0c, 0x02, 0x01, 0, 0, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0x00, 0xd0 }; size_t c0 = nchip, a0 = napp;
	  uint8_t racl[] = { 0x02, 0x40, 0x20, 0x07, 0x00, 9, 8, 7, 6, 5, 4, 3 }, rsco[] = { 0x03, 0x01, 0x00, 0x03, 7, 7, 7 }, riso[] = { 0x05, 0x01, 0x40, 0x02, 0x00, 1, 2 }, rev0[] = { 0x04, 0x05, 0x00 };
	  txw(acl, sizeof(acl)); CHECK(chip_is(c0, acl, sizeof(acl))); push(ev, sizeof(ev)); push(racl, sizeof(racl)); push(rsco, sizeof(rsco)); push(riso, sizeof(riso)); push(rev0, 3); poll();
	  uint8_t all[64]; size_t m = 0; memcpy(all, ev, sizeof(ev)); m += sizeof(ev); memcpy(all + m, racl, sizeof(racl)); m += sizeof(racl); memcpy(all + m, rsco, sizeof(rsco)); m += sizeof(rsco); memcpy(all + m, riso, sizeof(riso)); m += sizeof(riso); memcpy(all + m, rev0, 3); m += 3;
	  CHECK(app_is(a0, all, m)); CHECK(!has("BT GAP")); }

	/* 11. framing across split writes and reads */
	CASE("split tx"); start(16); creates(1);
	{ uint8_t c[40], x[40], r[8], acl[] = { 0x02, 0x01, 0x20, 0x04, 0x00, 1, 2, 3, 4 }; size_t n = b_params(c, 1, PRM), cc0 = nchip, a0 = napp; x_sub1(x, 1, PRM, 1, A);
	  for (size_t i = 0; i < n; i++) { txw(c + i, 1); if (i + 1 < n) CHECK(nchip == cc0); }                      /* the command is held until the last byte */
	  poll(); CHECK(chip_is(cc0, x, 28)); a_cc(r, 0xfd4a, 0); CHECK(app_is(a0, r, 7));
	  cc0 = nchip; for (size_t i = 0; i < sizeof(acl); i++) txw(acl + i, 1); CHECK(chip_is(cc0, acl, sizeof(acl)));  /* non-command packets are forwarded as they come */
	  cc0 = nchip; n = b_enable(c, 1, 1, 0, 0); txw(c, 3); txw(c + 3, 5); txw(c + 8, n - 8); poll(); CHECK(chip_is(cc0, x, x_en(x, 1, 1))); }
	CASE("split rx"); start(16); creates(1);
	{ uint8_t c[40], x[40], e[9] = { 0x04, 0x0e, 0x06, 0x01, 0x54, 0xfd, 0x00, 0x01, 0xf9 }; size_t n = b_params(c, 1, PRM), a0; hold_answers = 1; txw(c, n); hold_answers = 0; a0 = napp;
	  push(e, 9);
	  for (int i = 0; i < 9; i++) { rx_limit = 1; poll(); if (i < 8) CHECK(napp == a0); } rx_limit = 1u << 30;               /* a rewrite is delivered only when the packet is complete */
	  uint8_t r[8]; a_cc(r, 0xfd4a, 0); CHECK(app_is(a0, r, 7)); (void)x; }
	CASE("local answer between packets"); start(16); creates(1);
	{ uint8_t ev[] = { 0x04, 0x3e, 0x0c, 0x02, 0x01, 0, 0, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0x00, 0xd0 }, c[11], r[8]; size_t a0 = napp;
	  push(ev, 15); rx_limit = 6; poll(); CHECK(napp == a0); b_create(c, B, 1); txw(c, 11); rx_limit = 1; poll(); CHECK(napp == a0);               /* mid-packet: nothing, the answer waits */
	  rx_limit = 1u << 30; poll(); CHECK(napp - a0 == 15 + 8); CHECK(memcmp(app + a0, ev, 15) == 0); a_create(r, 0, 2); CHECK(memcmp(app + a0 + 15, r, 8) == 0); }

	/* 12. framing loss: bad tx type, bad rx type, oversize rx, rx error inside a packet; pass-through from then on, adapter stays off, loud */
	CASE("bad tx byte"); start(16); creates(1);
	{ uint8_t g[] = { 0x77, 1, 2 }, c[11]; size_t c0 = nchip; txw(g, 3); CHECK(chip_is(c0, g, 3)); CHECK(!sbu_vsc_on); CHECK(has("BT GAP: sweetbtvsc: adapter OFF for the rest of the boot: tx framing lost at offset 0")); CHECK(has("BT VSC: summary (stopped)")); CHECK(!has("0x77")); /* F5: no stream byte in a row */
	  c0 = nchip; b_create(c, A, 1); txw(c, 11); CHECK(chip_is(c0, c, 11)); }
	CASE("bad rx byte"); start(16); creates(1);
	{ uint8_t g[] = { 0x66, 1, 2 }; size_t a0 = napp; push(g, 3); poll(); CHECK(app_is(a0, g, 3)); CHECK(!sbu_vsc_on); CHECK(has("rx framing lost at offset 0")); CHECK(!has("0x66")); }
	CASE("oversize rx"); start(16); creates(1);
	{ uint8_t g[] = { 0x02, 0x01, 0x20, 0x01, 0x08, 0xaa }; size_t a0 = napp; push(g, 6); poll(); CHECK(app_is(a0, g, 6)); CHECK(!sbu_vsc_on); CHECK(has("framing lost")); }
	CASE("largest rx packet"); start(16); creates(1);
	{ static uint8_t g[2048]; size_t a0 = napp; g[0] = 0x02; g[1] = 1; g[2] = 0x20; g[3] = (2048 - 5) & 0xff; g[4] = (2048 - 5) >> 8; for (int i = 5; i < 2048; i++) g[i] = (uint8_t)i; push(g, 2048); for (int i = 0; i < 10; i++) poll(); CHECK(app_is(a0, g, 2048)); CHECK(sbu_vsc_on); }
	CASE("rx error mid packet"); start(16); creates(1);
	{ uint8_t g[] = { 0x04, 0x3e, 0x0b, 0x02 }; size_t a0 = napp; push(g, 4); poll(); CHECK(napp == a0); rx_err_once = 1; poll(); CHECK(!sbu_vsc_on); CHECK(app_is(a0, g, 4)); CHECK(has("uart rx error inside a packet")); }
	CASE("held command at stop"); start(16); creates(1);
	{ uint8_t c[11], g[] = { 0x04, 0x3e, 0x0b, 0x02 }; b_create(c, A, 1); size_t c0 = nchip; txw(c, 7); CHECK(nchip == c0); push(g, 4); poll(); rx_err_once = 1; poll();      /* rx stops the adapter while 7 command bytes are held */
	  CHECK(!sbu_vsc_on); txw(c + 7, 4); CHECK(chip_is(c0, c, 11)); CHECK(has("held command bytes are sent unchanged")); }

	/* 13. disconnect: counters, held bytes and answers dropped, pending replies consumed */
	CASE("disconnect"); start(16); creates(1); hold_answers = 1;
	{ uint8_t c[40], held[3] = { 0x01, 0x4a, 0xfd }; size_t n = b_params(c, 1, PRM); snd(c, n); txw(held, 3); sweet_bt_uart_disc(); CHECK(has("bluetoothd disconnected with 3 held command bytes dropped, 0 undelivered local answers dropped, 1 translated commands unanswered"));
	  CHECK(has("BT VSC: summary (bluetoothd disconnected): create 1 remove 0 translated 1")); CHECK(sbu_vsc_toff == 0);
	  uint8_t rep[] = { 0x04, 0x0e, 0x06, 0x01, 0x54, 0xfd, 0x00, 0x01, 0xf9 }; size_t a0 = napp; push(rep, 9); poll(); CHECK(napp == a0); CHECK(sbu_vsc_norphan == 1); CHECK(sbu_vsc_in[1].params_set); CHECK(has("(client gone: not delivered)")); }
	CASE("disconnect with an unsent answer"); start(16);
	{ uint8_t c[11]; b_create(c, A, 1); txw(c, 11); sweet_bt_uart_disc(); CHECK(has("1 undelivered local answers dropped")); size_t a0 = napp; poll(); CHECK(napp == a0); }
	CASE("disconnect without bring-up is silent"); world(16); sweet_bt_uart_disc(); CHECK(loglen == 0);

	/* 14. log volume: the first 16 rows in full, then powers of two; no address or payload byte in any BT VSC row */
	CASE("throttle"); start(32);
	{ for (int i = 0; i < 12; i++) { uint8_t c[16], x[16], r[8]; STEP(c, b_create(c, A, 1), x, 0, r, a_create(r, 0, 1)); STEP(c, b_enable(c, 1, 1, 0, 0), x, x_en(x, 1, 1), r, a_cc(r, 0xfd4d, 0)); STEP(c, b_remove(c, 1), x, x_en(x, 1, 0), r, a_cc(r, 0xfd49, 0)); }
	  CHECK(sbu_vsc_logn == 60); CHECK(has("(row 16)\n")); CHECK(!has("(row 17)\n")); CHECK(has("(row 32)\n")); CHECK(!has("(row 33)\n")); CHECK(has("(row 64)\n") == 0); CHECK(rows_clean()); }

	/* 14b. the chip does not take the translated bytes (uart tx stuck): the error reaches bluetoothd, the unsent entry is forgotten, the adapter stops loudly */
	CASE("tx error"); start(16); creates(1);
	{ uint8_t c[40]; size_t n = b_params(c, 1, PRM); tx_full = 1; CHECK(sbu_tx_kctl(c, n) != 0); tx_full = 0; CHECK(!sbu_vsc_on); CHECK(sbu_vsc_nxlate == 0); CHECK(has("(0 pending translations dropped)")); CHECK(has("adapter OFF for the rest of the boot: tx error")); }

	/* 15. diagnostic knobs keep working with the adapter off (nopcf withhold) and the adapter on takes the stream (they cannot both be on) */
	CASE("knobs with adapter off"); world(16); sbu_nopcf_on = sbu_nopcf_rx = true;
	{ uint8_t pcf[] = { 0x01, 0xe9, 0xfc, 0x09, 0x05, 0x00, 0, 0, 0, 0, 0, 0, 0x02 }, cr[11]; size_t c0 = nchip; txw(pcf, sizeof(pcf)); CHECK(nchip == c0); CHECK(has("sweetbtnopcf=1 withheld"));
	  b_create(cr, A, 1); txw(cr, 11); CHECK(chip_is(c0, cr, 11)); }

	/* 16. every command the chip ever got from the adapter is 0xfd53 (once) or 0xfd54 sub 1..5 (R1 safety audit over a long session) */
	CASE("audit"); start(8);
	{ uint8_t c[40]; int n53 = 0, n54 = 0; creates(2); snd(c, b_params(c, 1, PRM)); snd(c, b_data(c, 0xfd4b, 2, 5, 1)); snd(c, b_data(c, 0xfd4c, 2, 5, 1)); snd(c, b_enable(c, 1, 1, 0, 0));
	  snd(c, b_upd(c, 2, 1, B)); snd(c, b_remove(c, 1)); snd(c, b_remove(c, 2));
	  for (int i = 0; i < nchip_ops; i++) { if (chip_ops[i].op == 0xfd53) n53++; else if (chip_ops[i].op == 0xfd54 && chip_ops[i].sub >= 1 && chip_ops[i].sub <= 5) n54++; else { printf("FAIL audit: op %04x\n", chip_ops[i].op); fails++; } }
	  CHECK(n53 == 1 && n54 == 5); }      /* remove of the idle instance 2 sends nothing (F7) */

	printf("RESULT: %s sweet-bt-vsc cases=%d\n", fails ? "FAIL" : "PASS", cases);
	return fails ? 1 : 0;
}
