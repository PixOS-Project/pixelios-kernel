/* Host test of the BT UART backend (patch 0189: bsd/dev/sweet_bt.c + sweet_bt_uart.c, BT-S4a, T-e930). The REAL files are #included, kernel
 * headers are stubs and the registers are a fake file (tools/test_sweet_bt_c.py --uart). Oracle: docs/specs/SPEC-BT.md A1-A3 A8 A10 A24 A27 U3 U4. */
#include "kstub.h"
#include "sweet_bt.c"
#include "sweet_bt_uart.c"
#undef printf

/* ---- recorders ---- */
static char logbuf[1 << 17];
static size_t loglen;
int sbt_printf(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(logbuf + loglen, sizeof(logbuf) - loglen, fmt, ap);
	va_end(ap);
	if (n > 0) loglen += (size_t)n;
	return n;
}
static void log_reset(void) { logbuf[0] = 0; loglen = 0; }
static int count_in(const char *needle)
{
	int n = 0;
	for (const char *p = logbuf; (p = strstr(p, needle)) != NULL; p += strlen(needle)) n++;
	return n;
}
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; fprintf(stderr, "FAIL line %d: %s -- ", __LINE__, #c); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n  log: %s\n", logbuf); } } while (0)

/* ---- stubs the two files call ---- */
struct kern_ctl_reg regs[8];
int nregs;
errno_t ctl_register(struct kern_ctl_reg *r, kern_ctl_ref *ref) { regs[nregs] = *r; *ref = (kern_ctl_ref)(uintptr_t)(0x1000 + nregs); nregs++; return 0; }
uint8_t enq[4096]; size_t enq_len; int enq_calls, enq_unit;
errno_t ctl_enqueuedata(kern_ctl_ref ref, u_int32_t unit, void *d, size_t l, u_int32_t f) { (void)ref; (void)f; memcpy(enq + enq_len, d, l); enq_len += l; enq_calls++; enq_unit = (int)unit; return 0; }
int cdev_calls; const struct cdevsw *cdev_seen;
int cdevsw_add(int idx, const struct cdevsw *c) { (void)idx; cdev_calls++; cdev_seen = c; return 77; }
void *devfs_make_node(dev_t d, int t, uid_t u, gid_t g, int m, const char *f, ...) { (void)d; (void)t; (void)u; (void)g; (void)m; (void)f; return (void *)1; }
int SecureDTLookupEntry(void *a, const char *p, DTEntry *n) { (void)a; (void)n; return !strcmp(p, "/bluetooth") ? kSuccess : 1; }
void mbuf_freem(mbuf_t m) { (void)m; }
int bootarg_sweetbt = 1, board_gs101 = 1;
#include <stdbool.h>
bool sweet_usb_board_is_gs101(void) { return board_gs101; }
int PE_parse_boot_argn(const char *n, void *v, size_t l) { (void)l; if (strcmp(n, "sweetbt") || bootarg_sweetbt < 0) return 0; *(int *)v = bootarg_sweetbt; return 1; }
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t sz) { (void)pa; (void)sz; return 0; }   /* never reached: sbu.hw is preset */
int kernel_thread_start(void *f, void *a, void **t) { (void)f; (void)a; *t = (void *)1; return 0; }
void thread_deallocate(void *t) { (void)t; }
int tsleep(void *c, int p, const char *w, int t) { (void)c; (void)p; (void)w; (void)t; return 0; }
void delay(int us) { (void)us; }

/* ---- fake register file: events in order ---- */
enum { E_WR, E_US, E_MS };
struct ev { int kind; uint32_t pa, v; };
static struct ev evs[4096]; static int nev;
static int nrd, usi_stuck;
static uint32_t mem_gate[4] = { 1u << 21, 1u << 21, 1u << 21, 1u << 21 };   /* CMU_APM +0x2010 +0x2038 +0x203c +0x20a4 */
static uint32_t mem_usi, mem_cmu_f = 0x11, mem_cmu_s = 0, mem_cmu_d = 1, mem_g16c, mem_g16d, mem_g14c, mem_g14d, mem_ga3c, mem_ga3d, mem_ga3p;
static uint32_t u_ubrdiv, u_frac, u_umcon, u_ufstat_tx_full, u_trstat = 4, u_erstat, u_umstat;
static uint8_t txb[512]; static int ntx; static uint8_t rxq[1024]; static int rxh, rxt;
/* BT-S4e chip model: the BCM4389 answers an H4 HCI Reset with Command Complete only when the baud the host programmed (true parent clock / (16*(UBRDIV+1)+UFRACVAL))
 * is within 3 % of 115200; chip_hz = the clock UART16 really runs on (0 = silent chip), chip_junk = bytes sent before the event, chip_status = CC status */
static uint32_t chip_hz, chip_junk, chip_status, chip_delay_ms, vclock_us;   /* chip_delay_ms: latency of the answer on a virtual clock advanced by msleep/udelay (review T-5c3c F2) */
static int chip_answers, chip_wrongop, chip_wrongtype, chip_dup;
static struct { uint32_t at; uint8_t b[160]; int n; } pend[8]; static int npend;
static void chip_deliver(void)      /* move every answer whose time has come into the RX FIFO model */
{
	int k = 0;
	for (int i = 0; i < npend; i++) {
		if (pend[i].at <= vclock_us) { memcpy(&rxq[rxt], pend[i].b, (size_t)pend[i].n); rxt += pend[i].n; }
		else pend[k++] = pend[i];
	}
	npend = k;
}
static uint32_t f_rd(uint32_t pa)
{
	nrd++;
	chip_deliver();
	if (pa == 0x17402010u) return mem_gate[0]; if (pa == 0x17402038u) return mem_gate[1];
	if (pa == 0x1740203Cu) return mem_gate[2]; if (pa == 0x174020A4u) return mem_gate[3];
	if (pa == 0x174204E4u) return mem_usi;
	if (pa == 0x17401000u) return mem_cmu_f;
	if (pa == 0x17401004u) return mem_cmu_s;
	if (pa == 0x1740180Cu) return mem_cmu_d;
	if (pa == 0x10840200u) return mem_g16c; if (pa == 0x10840204u) return mem_g16d;
	if (pa == 0x108401C0u) return mem_g14c; if (pa == 0x108401C4u) return mem_g14d;
	if (pa == 0x174D0060u) return mem_ga3c; if (pa == 0x174D0064u) return mem_ga3d; if (pa == 0x174D0068u) return mem_ga3p;
	uint32_t o = pa - 0x175B0000u;
	if (o == U_UTRSTAT) return u_trstat | (rxh < rxt ? 1u : 0u);   /* RXDR */
	if (o == U_UFSTAT) { int c = rxt - rxh; return u_ufstat_tx_full | (c >= 256 ? (1u << 8) : (uint32_t)c); }   /* full: count field 0, bit 8 set (as on S5PV210) */
	if (o == U_UERSTAT) { uint32_t e = u_erstat; u_erstat = 0; return e; }
	if (o == U_UMSTAT) return u_umstat;
	if (o == U_URXH) return rxh < rxt ? rxq[rxh++] : 0;
	return 0;
}
static void f_wr(uint32_t pa, uint32_t v)
{
	evs[nev++] = (struct ev){ E_WR, pa, v };
	if (pa == 0x174204E4u) { if (!usi_stuck) mem_usi = v; }
	else if (pa == 0x10840200u) mem_g16c = v; else if (pa == 0x10840204u) { mem_g16d = v; if (!(v & 4u)) npend = 0; }      /* REG_ON low: the chip forgets anything it was about to say */
	else if (pa == 0x108401C0u) mem_g14c = v; else if (pa == 0x108401C4u) mem_g14d = v;
	else if (pa == 0x174D0060u) mem_ga3c = v;
	else if (pa == 0x175B0000u + U_UBRDIV) u_ubrdiv = v;
	else if (pa == 0x175B0000u + U_UFRACVAL) u_frac = v;
	else if (pa == 0x175B0000u + U_UMCON) u_umcon = v;
	else if (pa == 0x175B0000u + U_UTXH && ntx < 512) {
		txb[ntx++] = (uint8_t)v;
		if (chip_hz != 0 && ntx >= 4 && !memcmp(&txb[ntx - 4], "\x01\x03\x0c\x00", 4)) {
			uint64_t div = 16ull * (u_ubrdiv + 1) + u_frac, baud = chip_hz / div, d = baud > 115200 ? baud - 115200 : 115200 - baud;
			if (d * 100 <= 115200ull * 3) {
				uint8_t cc[7] = { 0x04, 0x0e, 0x04, 0x01, 0x03, 0x0c, 0x00 };
				if (chip_wrongtype) cc[0] = 0x02;      /* same bytes but not an HCI event packet (indicator 0x02 = ACL data) */
				if (chip_wrongop) { cc[4] = 0x14; cc[5] = 0x0c; }      /* a Command Complete for another opcode (Read Local Name) is not the answer */
				int n = 0;
				pend[npend].at = vclock_us + chip_delay_ms * 1000u;
				for (uint32_t j = 0; j < chip_junk; j++) pend[npend].b[n++] = 0xff;
				for (int j = 0; j < 7; j++) pend[npend].b[n++] = (j == 6) ? (uint8_t)chip_status : cc[j];
				pend[npend++].n = n;
				chip_answers++;
			}
		}
	}
}
static void f_us(unsigned us) { evs[nev++] = (struct ev){ E_US, 0, us }; vclock_us += us; }
static void f_ms(unsigned ms)
{
	evs[nev++] = (struct ev){ E_MS, 0, ms };
	vclock_us += ms * 1000u;
	if (chip_dup == 1 && chip_answers > 0 && ms == 10) {      /* a second Command Complete arrives after the host saw the first (the probe's 20 ms settle) */
		static const uint8_t cc[7] = { 0x04, 0x0e, 0x04, 0x01, 0x03, 0x0c, 0x00 };
		memcpy(&rxq[rxt], cc, 7); rxt += 7; chip_dup = 2;
	}
}
static int ev_find(int kind, uint32_t pa, uint32_t v, int from)
{
	for (int i = from; i < nev; i++) if (evs[i].kind == kind && (kind != E_WR || evs[i].pa == pa) && evs[i].v == v) return i;
	return -1;
}

/* index of the first store to `pa` whose bit `bit` equals `level` (DAT words carry other pins' bits now) */
static int ev_bit(uint32_t pa, unsigned bit, int level, int from)
{
	for (int i = from; i < nev; i++) if (evs[i].kind == E_WR && evs[i].pa == pa && ((evs[i].v >> bit) & 1u) == (unsigned)level) return i;
	return -1;
}

static int cmu_stores(void)   /* any store into CMU_APM (0x17400000-0x17402FFF): the backend must never make one */
{
	int n = 0;
	for (int i = 0; i < nev; i++) if (evs[i].kind == E_WR && evs[i].pa >= 0x17400000u && evs[i].pa < 0x17403000u) n++;
	return n;
}
static int before(const char *a, const char *b)   /* a appears in the log, and before b */
{
	const char *pa = strstr(logbuf, a), *pb = strstr(logbuf, b);
	return pa != NULL && pb != NULL && pa < pb;
}

static void reset_world(void)
{
	log_reset(); nev = 0; ntx = 0; rxh = rxt = 0; enq_len = 0; enq_calls = 0;
	memset(&sbu, 0, sizeof(sbu)); sbu.hw = (struct sbu_hw){ f_rd, f_wr, f_us, f_ms };
	memset(&sbt, 0, sizeof(sbt)); nregs = 0;
	/* shared registers start NON-zero (review r1 F4, AP-301): gpp14 holds WLAN_EN's CON/DAT bits, USI mode has other bits */
	mem_usi = 0xF0; mem_cmu_f = 0x11;   /* 0x11: vendor select 1 AND mainline bit 4 = funcsrc (BT-S4c s.4) */
	 mem_cmu_s = 0; mem_cmu_d = 1; mem_g16c = 0x2222; mem_g16d = 0xB; mem_g14c = 0x33; mem_g14d = 0x7; mem_ga3c = 0xAB000000; mem_ga3d = 0; mem_ga3p = 0;
	for (int i = 0; i < 4; i++) mem_gate[i] = 1u << 21;
	chip_hz = 0; chip_junk = 0; chip_status = 0; chip_delay_ms = 0; vclock_us = 0; npend = 0; chip_answers = 0; chip_wrongop = 0; chip_wrongtype = 0; chip_dup = 0;
	nrd = 0; usi_stuck = 0; u_ufstat_tx_full = 0; u_trstat = 4; u_erstat = 0; u_umstat = 0; bootarg_sweetbt = 1; board_gs101 = 1;
}
static errno_t kc_connect(int i) { void *ui; struct sockaddr_ctl sac = { 5 }; return regs[i].ctl_connect(sbt.ref[i], &sac, &ui); }
static errno_t kc_send(int i, const void *d, size_t l)
{
	struct mbuf m = { d, l, NULL };
	return regs[i].ctl_send(sbt.ref[i], 5, NULL, &m, 0);
}
static errno_t kc_seta(int i, speed_t sp, tcflag_t cf, speed_t isp)
{
	struct termios t; memset(&t, 0, sizeof(t)); t.c_cflag = cf; t.c_ispeed = isp; t.c_ospeed = sp;
	return regs[i].ctl_setopt(sbt.ref[i], 5, NULL, TIOCSETA, &t, sizeof(t));
}

int main(void)
{
	/* 0. BT-S4c case table (docs/research/BT-S4c-uart16-clock-parent0.md s.4): raw words logged first; a baud clock only where the vendor
	 * layout (select = raw & 3) and the mainline layout (bit 4 func / bit 3 funcsrc) name the same parent; no CMU store in any row. */
	{
		struct { uint32_t f, s, d; int ok115; uint32_t q, fr; const char *gap; } rows[] = {
			{ 0x00, 0x0, 0, 1, 12, 5, NULL },        /* both oscclk: 24576000 / 1, 115200 -> div 213 (s.3 row 1) */
			{ 0x00, 0x9, 0, 1, 12, 5, NULL },        /* funcsrc != 0 is not consulted when func = oscclk */
			{ 0x00, 0x0, 1, 1, 5, 11, NULL },        /* oscclk / 2 = 12288000: div 106 -> UBRDIV 5, round-up to 107 -> UFRACVAL 11 (review T-833a F2) */
			{ 0x01, 0x0, 0, 0, 0, 0, "parent conflict" },   /* vendor funcsrc, mainline oscclk */
			{ 0x10, 0x0, 0, 0, 0, 0, "parent conflict" },   /* mainline funcsrc, vendor oscclk */
			{ 0x02, 0x0, 0, 0, 0, 0, "parent conflict" },   /* vendor pad_clk_apm, mainline oscclk */
			{ 0x03, 0x0, 0, 0, 0, 0, "parent outside the pinned table" },   /* vendor idx 3 = oscclk agrees with mainline, but not in the s.4 table: refused (F5) */
			{ 0x13, 0x0, 0, 0, 0, 0, "parent conflict" },   /* vendor oscclk(3), mainline funcsrc */
			{ 0x12, 0x0, 0, 0, 0, 0, "parent conflict" },
			{ 0x11, 0x0, 1, 1, 105, 11, NULL },      /* both funcsrc, funcsrc idx 0 both: pll_alv_div2 393216000 / 2 */
			{ 0x11, 0x0, 0, 1, 212, 5, NULL },       /* pll_alv_div2 / 1: div 3413 (s.3 row 3) */
			{ 0x11, 0x1, 1, 0, 0, 0, "parent unknown" },    /* funcsrc vendor 1, mainline bit3 0 */
			{ 0x11, 0x8, 1, 0, 0, 0, "parent unknown" },    /* funcsrc mainline 1, vendor 0 */
			{ 0x11, 0x9, 1, 0, 0, 0, "parent unknown" },
			{ 0x11, 0x2, 1, 0, 0, 0, "parent unknown" },
		};   /* (the "baud N refused" line is not counted per row: its call-site GAP counter is past 8 here, so only the first refused row pins it; see first_refused) */
		int first_refused = 1;
		for (unsigned i = 0; i < sizeof(rows) / sizeof(rows[0]); i++) {
			reset_world();   /* no sweet_bt_init: the rows run first and alone, so every GAP call site stays under its 8-line limit */
			mem_cmu_f = rows[i].f; mem_cmu_s = rows[i].s; mem_cmu_d = rows[i].d; u_ubrdiv = 0xDEAD; u_frac = 0xBEEF; log_reset();
			char raw[96];
			snprintf(raw, sizeof(raw), "BT UART: cmu_apm mux_func raw %#x funcsrc raw %#x div raw %#x\n", rows[i].f, rows[i].s, rows[i].d);
			int e = sbu_set_baud(115200);
			CHECK(count_in(raw) == 1, "row %u: raw words logged once (%s)", i, raw);
			if (rows[i].ok115) {
				CHECK(e == 0 && u_ubrdiv == rows[i].q && u_frac == rows[i].fr, "row %u: 115200 accepted, UBRDIV %u UFRACVAL %u (got e %d %u %u)", i, rows[i].q, rows[i].fr, e, u_ubrdiv, u_frac);
				CHECK(before(raw, "NOT observed on hardware") && count_in("both mux layouts agree") == 1, "row %u: derived rate is a GAP, after the raw line", i);
			} else {
				CHECK(e == EIO && u_ubrdiv == 0xDEAD && u_frac == 0xBEEF, "row %u: no baud set (e %d)", i, e);
				CHECK(before(raw, rows[i].gap) && count_in("BT GAP: uart16 baud clock") == 1, "row %u: GAP '%s' after the raw line", i, rows[i].gap);
				if (first_refused) {
					first_refused = 0;
					CHECK(count_in("baud 115200 refused (error 5)") == 1, "row %u: the refusal itself is a GAP line too", i);
				}
			}
			log_reset();
			int e4 = rows[i].ok115 ? sbu_set_baud(4000000) : EIO;   /* refused rows: not re-called (each call is one more line on the rate-limited GAP site) */
			if (rows[i].ok115 && rows[i].f == 0) {   /* s.4 item 3: oscclk caps the baud at rate/16 = 1536000; 4M is an error with a GAP, never a downgrade */
				CHECK(e4 == EINVAL && u_ubrdiv == rows[i].q && count_in("4M unreachable") == 1 && count_in("baud 4000000 refused (error 22: above the baud clock limit rate/16)") == 1, "row %u: oscclk 4M refused loudly (e %d)", i, e4);
				log_reset();
				CHECK(sbu_set_baud(460800) == EINVAL && u_ubrdiv == rows[i].q && count_in("baud 460800 refused on oscclk") == 1, "row %u: oscclk allows 115200 only (s.4 item 3)", i);
			} else if (rows[i].ok115) {
				CHECK(e4 == 0 && count_in("4M unreachable") == 0, "row %u: 4M reachable on pll_alv_div2 (e %d)", i, e4);
			}
			CHECK(cmu_stores() == 0 && count_in("not on the store list") == 0, "row %u: no store to CMU_APM attempted (%d)", i, cmu_stores());
			CHECK(!sbu_store_ok(0x17401000u) && !sbu_store_ok(0x17401004u) && !sbu_store_ok(0x1740180Cu), "CMU mux/div never on the store list");
		}
	}

	/* 1. no boot-arg: registered but no backend, every transfer a GAP + error, ZERO register stores */
	reset_world(); bootarg_sweetbt = -1;
	sweet_bt_init();
	CHECK(nregs == 2 && sbt.ops == NULL, "backend must not attach without sweetbt=1");
	CHECK(count_in("backend present but not enabled") == 1, "one loud GAP line");
	CHECK(kc_connect(0) == 0 && kc_send(0, "\x01", 1) == ENXIO && nev == 0, "send without backend errors, no store (nev %d)", nev);

	/* 2. power-on order A9/A10, USI mode A8, open at 115200 (U3), baud math U4 */
	reset_world();
	sweet_bt_init();
	CHECK(sbt.ops != NULL && sbu.powered && sbu.uart_ok, "attached");
	int g16l = ev_bit(0x10840204u, 2, 0, 0), g14l = ev_bit(0x108401C4u, 3, 0, 0), m30 = ev_find(E_MS, 0, 30, 0);
	int g16h = ev_bit(0x10840204u, 2, 1, 0), g14h = ev_bit(0x108401C4u, 3, 1, 0), m20 = ev_find(E_MS, 0, 20, 0);
	CHECK(g16l >= 0 && g14l > g16l && m30 > g14l && g16h > m30 && g14h > g16h && m20 > g14h, "A10 order: regon low, wake low, 30 ms, regon high, wake high, 20 ms (%d %d %d %d %d %d)", g16l, g14l, m30, g16h, g14h, m20);
	CHECK(mem_g16c == 0x2122 && mem_g14c == 0x1033, "REG_ON/DEV_WAKE CON = output (func 1) in the pin nibble only, other pins kept: %#x %#x", mem_g16c, mem_g14c);
	CHECK(mem_g16d == 0xF && mem_g14d == 0xF, "DAT changes only the pin bit (WLAN_EN bits kept): %#x %#x", mem_g16d, mem_g14d);
	CHECK(mem_ga3c == 0xAB002222 && ev_find(E_WR, 0x174D0060u, 0xAB002222, 0) >= 0, "T-e3c9 H1: gpa3-0..3 := function 2, other nibbles kept, one store: %#x", mem_ga3c);
	CHECK(count_in("pads before gpa3 CON 0xab000000") >= 1 && count_in("pads after gpa3 CON 0xab002222") >= 1 && count_in("witness open regon 1 devwake 1") == 1, "pad readback and witnesses logged");
	{ int ga = 0; for (int i = 0; i < nev; i++) if (evs[i].kind == E_WR && evs[i].pa >= 0x174D0000u && evs[i].pa < 0x174D1000u && evs[i].pa != 0x174D0060u) ga++;
	  CHECK(ga == 0, "no store into the ALIVE pinctrl page except gpa3 CON (%d)", ga);
	  int gpa = ev_find(E_WR, 0x174D0060u, 0xAB002222u, 0);
	  CHECK(gpa >= 0 && gpa < g16l, "pads muxed before BT_REG_ON low (%d %d)", gpa, g16l); }
	CHECK(mem_usi == 0xF1, "USI mode writes bits [2:0] only: %#x", mem_usi);
	CHECK(count_in("cmu_apm gates func 0x200000") == 1, "gate state logged");
	CHECK(ev_find(E_WR, 0x174204E4u, 0xF1, 0) >= 0 && count_in("set usi mode - 0x1") == 1, "A8 usi mode write and read back");
	CHECK(u_ubrdiv == 105 && u_frac == 11, "115200 at 196.608 MHz: UBRDIV 105 UFRACVAL 11 (got %u %u)", u_ubrdiv, u_frac);
	CHECK(count_in("NOT observed on hardware") >= 1 && count_in("pad PUD/DRV (uart16_bus") == 1, "rate and pad gaps are logged");
	CHECK(u_umcon == (1u << 4), "AFC after open, trigger level left at reset (got %#x)", u_umcon);
	CHECK(count_in("RTS trigger level left at its reset value") == 1 && count_in("PUD not programmed") == 1, "trigger-level and PUD gaps are logged");

	/* T-e3c9 F8: CON already 0x2222 -> no store; 0x1122 -> one store to 0x2222 */
	reset_world(); mem_ga3c = 0x2222; sweet_bt_init();
	CHECK(ev_find(E_WR, 0x174D0060u, 0x2222u, 0) < 0 && count_in("pads after") == 0 && count_in("pads before gpa3 CON 0x2222") >= 1, "gpa3 CON already function 2: no store");
	reset_world(); mem_ga3c = 0x1122; sweet_bt_init();
	CHECK(ev_find(E_WR, 0x174D0060u, 0x2222u, 0) >= 0 && mem_ga3c == 0x2222, "gpa3 CON 0x1122 -> 0x2222");

	/* 3. kernel control: baud switch to 4 Mbaud, H4 send (A24: dev-wake stays high), RX relay */
	CHECK(kc_connect(0) == 0, "connect");
	CHECK(kc_seta(0, 4000000, CRTS_IFLOW | CCTS_OFLOW | CS8, 4000000) == 0 && u_ubrdiv == 2 && u_frac == 1, "4M: UBRDIV 2 UFRACVAL 1 (got %u %u)", u_ubrdiv, u_frac);
	const uint8_t cmd[] = { 0x01, 0x03, 0x0c, 0x00 };
	CHECK(kc_send(0, cmd, 4) == 0 && ntx == 4 && !memcmp(txb, cmd, 4), "H4 reset command on the wire, %d bytes", ntx);
	CHECK(mem_g14d & (1u << 3), "DEV_WAKE still high during traffic");
	const uint8_t evt[] = { 0x04, 0x0e, 0x04, 0x01, 0x03, 0x0c, 0x00 };
	memcpy(rxq, evt, sizeof(evt)); rxt = sizeof(evt);
	CHECK(sbu_rx_poll() == sizeof(evt) && enq_calls == 1 && enq_len == sizeof(evt) && !memcmp(enq, evt, sizeof(evt)) && enq_unit == 5, "RX bytes reach the control, unit 5");
	log_reset(); u_erstat = 1; memcpy(rxq, evt, 3); rxh = 0; rxt = 3; (void)sbu_rx_poll();
	CHECK(count_in("rx error flags 0x1") == 1, "RX error flags are logged, not swallowed");
	CHECK(kc_seta(0, 1000000, 0, 2000000) == EINVAL && u_ubrdiv == 2, "ispeed != ospeed refused, baud unchanged");
	CHECK(kc_seta(0, 4000001, 0, 4000001) == EINVAL, "baud above the 4 M the vendor driver allows refused");
	struct termios t; size_t l = sizeof(t);
	CHECK(regs[0].ctl_getopt(sbt.ref[0], 5, NULL, TIOCGETA, &t, &l) == 0 && t.c_ospeed == 4000000, "TIOCGETA returns the applied speed");
	int cts = 0; u_umstat = 1;
	CHECK(sweet_bt_kctl_ioctl(SBT_CTL_PLAIN, TIOCMGET, &cts) == 0 && cts == TIOCM_CTS, "TIOCMGET reflects CTS");

	/* 3b. TIOCSETA validation (review r1 F7): a refused request changes nothing */
	{
		uint32_t br = u_ubrdiv, um = u_umcon; log_reset();
		CHECK(kc_seta(0, 115200, CS8 | PARENB, 115200) == EINVAL && kc_seta(0, 115200, CS7, 115200) == EINVAL && kc_seta(0, 115200, CS8 | CSTOPB, 115200) == EINVAL, "not 8N1 refused");
		CHECK(kc_seta(0, 0, CS8, 0) == EINVAL && kc_seta(0, (speed_t)1 << 32 | 115200, CS8, (speed_t)1 << 32 | 115200) == EINVAL, "speed 0 / above 4 M (64-bit wrap) refused");
		CHECK(u_ubrdiv == br && u_umcon == um && count_in("only 8N1") == 3, "refused requests change no register and say why");
		CHECK(kc_seta(0, 4000000, CS8, 4000000) == 0 && u_umcon == (1u << 0), "no flow bits -> RTS asserted, AFC off");
		struct termios t2; size_t l2 = sizeof(t2);
		CHECK(regs[0].ctl_getopt(sbt.ref[0], 5, NULL, TIOCGETA, &t2, &l2) == 0 && (t2.c_cflag & (CS8 | CREAD | CLOCAL)) == (CS8 | CREAD | CLOCAL) && !(t2.c_cflag & CCTS_OFLOW), "TIOCGETA reports the effective state");
		CHECK(kc_seta(0, 4000000, CRTS_IFLOW | CCTS_OFLOW | CS8, 4000000) == 0, "restore AFC");
		/* one client across both controls (F6) */
		CHECK(kc_connect(1) == EBUSY, "second control refused while the first is connected");
		(void)regs[0].ctl_disconnect(sbt.ref[0], 5, NULL);
		CHECK(kc_connect(1) == 0 && kc_connect(0) == EBUSY, "after disconnect the other control can connect, the first is now refused");
		(void)regs[1].ctl_disconnect(sbt.ref[1], 5, NULL);
		(void)kc_connect(0);
	}
	/* 3c. a completely full RX FIFO (count field reads 0, UFSTAT bit 8) is still drained (F3) */
	{
		for (int i = 0; i < 256; i++) rxq[i] = (uint8_t)i;
		rxh = 0; rxt = 256; enq_len = 0; enq_calls = 0;
		CHECK(sbu_rx_poll() == 256 && enq_len == 256 && enq[255] == 255, "full FIFO drained (got %zu)", enq_len);
	}

	/* 4. RX with no client is a loud drop */
	reset_world(); sweet_bt_init();
	rxq[0] = 0x04; rxt = 1; log_reset(); (void)sbu_rx_poll();
	CHECK(count_in("no connected client; dropped") == 1 && enq_calls == 0, "rx without client dropped loudly");

	/* 5. every GAP op still errors with a log line */
	CHECK(cdev_seen != NULL && cdev_seen->d_open != NULL, "devnodes registered");
	log_reset();
	CHECK(sbt_open(makedev(77, 0), 0, 0, NULL) == ENXIO && sbt_open(makedev(77, 1), 0, 0, NULL) == ENXIO && sbt_open(makedev(77, 2), 0, 0, NULL) == ENXIO && count_in("no GPIO backend") == 3, "btwake/btpoweroff/btreset opens are GAP + ENXIO");
	CHECK(sbu_wr(0x10840208u, 1, "pud") == EPERM && sbu_wr(0x175B0000u + U_UERSTAT, 0, "x") == EPERM && sbu_wr(0x174E0000u, 0xF, "gpa6") == EPERM && count_in("not on the store list") == 3 && sbu_wr(0x174D0068u, 0, "pud") == EPERM && sbu_wr(0x174D0064u, 0, "dat") == EPERM && count_in("not on the store list") == 5, "stores outside the list refused");
	mem_ga3c = 0xAB002222; log_reset();
	CHECK(sbu_wr(0x174D0060u, 0xAB012222u, "x") == EPERM && sbu_wr(0x174D0060u, 0x2222u, "x") == EPERM && mem_ga3c == 0xAB002222 && count_in("outside 0xffff of the shared register") == 2, "gpa3 CON upper half audited");
	log_reset();
	CHECK(sbu_wr(0x108401C4u, 0x1, "x") == EPERM && sbu_wr(0x108401C0u, 0, "x") == EPERM && sbu_wr(0x174204E4u, 0x0, "x") == EPERM && count_in("outside") == 3, "a store that would change bits outside the pin/mode field refused");
	mem_cmu_s = 1u << 3; log_reset();
	CHECK(sbu_set_baud(4000000) == EIO && count_in("baud clock parent unknown") == 1, "unsourced clock parent: error, no divisor written");
	mem_cmu_s = 0; u_ufstat_tx_full = SBU_UFSTAT_TXFULL; log_reset();
	CHECK(sbu_tx("a", 1) == ETIMEDOUT && count_in("tx fifo stayed full") == 1, "stuck TX FIFO times out loudly");
	u_ufstat_tx_full = 0; u_trstat = 0; log_reset();
	CHECK(sbu_set_baud(115200) == ETIMEDOUT && count_in("transmitter not empty") == 1, "baud change waits for TX empty");
	{ uint32_t q, f; CHECK(sbu_baud_regs(0, 1, &q, &f) == EINVAL && sbu_baud_regs(196608000, 115200, &q, &f) == 0 && sbu_baud_regs(1000, 115200, &q, &f) == EINVAL && sbu_baud_regs(196608000, 100, &q, &f) == EINVAL, "divisor input checks (incl. UBRDIV > 16 bits)"); }

	/* 5c. review T-833a F6: a refused clock stops the start before ANY store (REG_ON, USI, UART); an accepted oscclk starts at 115200 */
	/* a refused clock (no candidate) still writes nothing: 0x10 is the other conflict, 0x01 with funcsrc != 0 is outside the BT-S4e case */
	reset_world(); mem_cmu_f = 0x10; chip_hz = 196608000u; sweet_bt_init();
	CHECK(sbt.ops == NULL && nev == 0 && !sbu.powered && ntx == 0 && count_in("nothing written") == 1 && count_in("baud probe") == 0, "refused conflict word 0x10: no store at all (nev %d)", nev);
	reset_world(); mem_cmu_f = 0x1; mem_cmu_s = 0x1; chip_hz = 196608000u; sweet_bt_init();
	CHECK(sbt.ops == NULL && nev == 0 && !sbu.powered && ntx == 0 && count_in("nothing written") == 1 && count_in("baud probe") == 0, "word 0x1 with funcsrc 1: not the BT-S4e case, no store at all (nev %d)", nev);

	{       /* only the observed word pair is probed: every other raw word with a disagreeing layout is refused before any store */
		static const uint32_t fw[] = { 0x02, 0x03, 0x11, 0x13 }, sw[] = { 0x1, 0x8, 0x9 };
		static const uint32_t xf[] = { 0x10001, 0x101, 0x1001 }, xd[] = { 1, 1, 1 };       /* BUSY/other bits set, or another DIVRATIO (review T-5c3c F1) */
		for (unsigned x = 0; x < 4; x++) {
			reset_world(); mem_cmu_f = x < 3 ? xf[x] : 0x1; mem_cmu_d = x < 3 ? xd[x] : 0; chip_hz = 196608000u; sweet_bt_init();
			CHECK(sbt.ops == NULL && nev == 0 && ntx == 0 && count_in("baud probe") == 0, "variant %u of the conflict word is not the observed word: refused, no store (nev %d)", x, nev);
		}
		reset_world(); mem_cmu_f = 0x1; mem_cmu_s = 0; mem_cmu_d = 1; chip_hz = 196608000u; sweet_bt_init();
		CHECK(sbt.ops == &sbu_ops, "the exact observed word is probed");
		for (unsigned a = 0; a < 4; a++) {
			reset_world(); mem_cmu_f = fw[a]; mem_cmu_s = 0; chip_hz = 196608000u; log_reset(); (void)sbu_set_baud(115200);
			CHECK(count_in("baud probe") == 0 && cmu_stores() == 0, "word %#x is not the BT-S4e case", fw[a]);
		}
		for (unsigned b = 0; b < 3; b++) {
			reset_world(); mem_cmu_f = 0x1; mem_cmu_s = sw[b]; chip_hz = 196608000u; sweet_bt_init();
			CHECK(sbt.ops == NULL && nev == 0 && ntx == 0 && count_in("baud probe") == 0, "funcsrc %#x with mux_func 0x1: not the BT-S4e case, no store (nev %d)", sw[b], nev);
		}
		for (unsigned a = 0; a < 2; a++) {      /* 0x02 / 0x03 go through attach too */
			reset_world(); mem_cmu_f = fw[a]; chip_hz = 196608000u; sweet_bt_init();
			CHECK(sbt.ops == NULL && nev == 0 && ntx == 0 && count_in("baud probe") == 0, "attach with mux_func %#x: refused, no store (nev %d)", fw[a], nev);
		}
	}

	/* 5d. BT-S4e: the observed word (mux_func 0x1, funcsrc 0, div 0x1) is decided by the chip's answer to HCI Reset, vendor divisor first */
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; sweet_bt_init();   /* the clock really is pll_alv_div2 / 2 */
	CHECK(sbt.ops == &sbu_ops && sbu.powered && sbu.uart_ok && sbu.baud == 115200 && sbu.rate == 196608000u && sbu.clk_hz == 196608000u && !sbu.clk_osc && cmu_stores() == 0,
	    "vendor layout answers: backend up on 196608000 Hz, no CMU store (rate %u)", sbu.rate);
	CHECK(u_ubrdiv == 105 && u_frac == 11 && ntx == 4 && !memcmp(txb, "\x01\x03\x0c\x00", 4) && chip_answers == 1, "vendor candidate: UBRDIV 105 UFRACVAL 11, exactly one HCI Reset sent (ntx %d answers %d)", ntx, chip_answers);
	CHECK(count_in("witness after-probe") >= 1 && count_in("baud probe layout=vendor uart16 clock 196608000 Hz UBRDIV 105 UFRACVAL 11: chip answered HCI Reset (rx 7 bytes, uart error flags 0)") == 1 && count_in("baud probe answered layout=vendor uart16 clock 196608000 Hz") == 1 &&
	    count_in("layout=mainline") == 0 && count_in("settled by the chip's answer (layout vendor), not by a source") == 1 && before("cmu_apm mux_func raw 0x1 funcsrc raw 0 div raw 0x1", "baud probe layout=vendor"),
	    "log names the answering layout, after the raw words, no mainline attempt");
	CHECK(rxh == rxt && sbu.rx_bytes == 7 && count_in("(BT_REG_ON low)") == 2, "the answer was consumed by the probe (stray event never reaches bluetoothd); one power-on only (%d)", count_in("(BT_REG_ON low)"));
	log_reset();
	CHECK(sbu_set_baud(4000000) == 0 && u_ubrdiv == 2 && u_frac == 1 && sbu.rate == 196608000u, "vendor layout: 4M reachable afterwards (UBRDIV %u UFRACVAL %u)", u_ubrdiv, u_frac);

	reset_world(); mem_cmu_f = 0x1; chip_hz = 12288000u; sweet_bt_init();   /* the clock really is oscclk / 2 */
	CHECK(sbt.ops == &sbu_ops && sbu.powered && sbu.uart_ok && sbu.rate == 12288000u && sbu.clk_hz == 12288000u && sbu.clk_osc && cmu_stores() == 0,
	    "mainline layout answers after the vendor one stayed silent: backend up on 12288000 Hz (rate %u)", sbu.rate);
	CHECK(u_ubrdiv == 5 && u_frac == 11 && chip_answers == 1 && ntx == 12, "mainline candidate: UBRDIV 5 UFRACVAL 11 (2 vendor rounds of 4 bytes, then 4) (ntx %d answers %d)", ntx, chip_answers);
	CHECK(count_in("layout=vendor uart16 clock 196608000 Hz UBRDIV 105 UFRACVAL 11: no answer (rx 0 bytes") == 1 && count_in("layout=mainline uart16 clock 12288000 Hz UBRDIV 5 UFRACVAL 11: chip answered HCI Reset") == 1 &&
	    count_in("baud probe answered layout=mainline uart16 clock 12288000 Hz") == 1 && count_in("baud probe answered layout=vendor") == 0, "log names the vendor failure and the mainline answer");
	{       /* power cycle between candidates: DEV_WAKE low + REG_ON low (A27) after the silent vendor try, then the A10 order again */
		int rg_low = ev_bit(0x10840204u, 2, 0, 0), rg_high_again = rg_low < 0 ? -1 : ev_bit(0x10840204u, 2, 1, rg_low);
		CHECK(rg_low >= 0 && rg_high_again > rg_low && count_in("BT UART: power off") == 1, "controller powered off between the candidates (A27) and on again (A10)");
	}
	log_reset();
	CHECK(sbu_set_baud(4000000) == EINVAL && sbu_set_baud(460800) == EINVAL && count_in("only 115200 is allowed there") == 1 && count_in("4M unreachable") == 0, "mainline layout: oscclk allows 115200 only, no downgrade");

	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_junk = 5; chip_status = 0x0c; sweet_bt_init();   /* junk before the event; a non-zero status still settles the clock, loudly */
	CHECK(sbt.ops == &sbu_ops && sbu.clk_hz == 196608000u && count_in("(rx 12 bytes") == 1 && count_in("Command Complete status 0xc") == 1, "junk before the event is skipped; a bad status is a GAP, the clock is settled");

	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_dup = 1; sweet_bt_init();   /* a duplicate Command Complete is drained, not left for bluetoothd */
	CHECK(sbt.ops == &sbu_ops && rxh == rxt && count_in("drained 7 late bytes after the answer") == 1, "a second Command Complete is drained by the probe (rxh %d rxt %d)", rxh, rxt);

	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_wrongop = 1; sweet_bt_init();   /* an event for another opcode never settles the clock */
	CHECK(sbt.ops == NULL && !sbu.powered && sbu.clk_hz == 0 && count_in("chip answered HCI Reset") == 0 && count_in("no candidate clock got an answer") == 1, "a Command Complete for another opcode is not an answer");

	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_wrongtype = 1; sweet_bt_init();   /* right opcode bytes in a non-event packet are not an answer */
	CHECK(sbt.ops == NULL && !sbu.powered && sbu.clk_hz == 0 && count_in("chip answered HCI Reset") == 0, "a packet that is not an HCI event is not an answer");

	/* review T-5c3c F2/F3/F6/F7: latency on a virtual clock, the 128-byte window, the error flags, the in-flight second Reset */
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_delay_ms = 250; sweet_bt_init();       /* answer inside round 1's 300 ms window */
	CHECK(sbt.ops == &sbu_ops && sbu.clk_hz == 196608000u && ntx == 4 && chip_answers == 1, "answer after 250 ms: round 1 (ntx %d)", ntx);
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_delay_ms = 400; sweet_bt_init();       /* answer arrives during round 2 */
	CHECK(sbt.ops == &sbu_ops && sbu.clk_hz == 196608000u && ntx == 8 && chip_answers == 2, "answer after 400 ms: found in round 2 (ntx %d)", ntx);
	CHECK(rxh == rxt && npend == 0 && count_in("drained 7 late bytes after the answer") == 1, "the answer to the round-2 Reset (due at 700 ms) is drained, not left for bluetoothd (rxh %d rxt %d pend %d)", rxh, rxt, npend);
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_delay_ms = 700; sweet_bt_init();       /* later than both windows: the same as a silent chip */
	CHECK(sbt.ops == NULL && !sbu.powered && count_in("chip answered HCI Reset") == 0, "answer after 700 ms is too late for both rounds");
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_junk = 121; sweet_bt_init();            /* event ends exactly at the 128-byte window edge */
	CHECK(sbt.ops == &sbu_ops && sbu.clk_hz == 196608000u, "event ending at byte 128 is found");
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; chip_junk = 122; sweet_bt_init();            /* one byte past the window: not examined, loud no-answer */
	CHECK(sbt.ops == NULL && count_in("chip answered HCI Reset") == 0 && count_in("no candidate clock got an answer") == 1, "event past the 128-byte window is not an answer");
	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; u_erstat = 0x4; sweet_bt_init();
	CHECK(sbt.ops == &sbu_ops && count_in("uart error flags 0x4)") == 1, "UART error flags seen while probing are logged");

	reset_world(); mem_cmu_f = 0x1; chip_hz = 0; sweet_bt_init();   /* a silent chip: both candidates fail */
	CHECK(sbt.ops == NULL && !sbu.powered && !sbu.uart_ok && sbu.clk_hz == 0 && cmu_stores() == 0 && ntx == 16, "silent chip: no ops, powered off, no CMU store (ntx %d)", ntx);
	CHECK(count_in("layout=vendor uart16 clock 196608000 Hz UBRDIV 105 UFRACVAL 11: no answer") == 1 && count_in("layout=mainline uart16 clock 12288000 Hz UBRDIV 5 UFRACVAL 11: no answer") == 1 &&
	    count_in("no candidate clock got an answer to HCI Reset") == 1 && count_in("baud probe answered") == 0 && count_in("backend up") == 0, "silent chip: both candidates and the failure are logged, nothing claims success");
	CHECK(nev >= 2 && evs[nev - 2].pa == 0x108401C4u && !(evs[nev - 2].v & 8u) && evs[nev - 1].pa == 0x10840204u && !(evs[nev - 1].v & 4u), "silent chip: last stores are DEV_WAKE low then REG_ON low");
	CHECK(sbu_set_baud(115200) == EIO, "after a failed probe the unproven conflict is still refused (clk_hz 0)");

	reset_world(); mem_cmu_f = 0x1; chip_hz = 196608000u; usi_stuck = 1; sweet_bt_init();   /* the start itself fails in the first candidate: no second try, powered off */
	CHECK(sbt.ops == NULL && !sbu.powered && count_in("layout=mainline") == 0 && count_in("usi mode read back") == 1, "a start error in the first candidate stops the probe (no mainline try)");

	reset_world(); mem_cmu_f = 0x0; mem_cmu_d = 0; sweet_bt_init();
	CHECK(sbt.ops == &sbu_ops && sbu.baud == 115200 && u_ubrdiv == 12 && u_frac == 5 && cmu_stores() == 0, "oscclk: backend up at 115200 (UBRDIV %u UFRACVAL %u)", u_ubrdiv, u_frac);

	/* 6. power off A27 order, tx refused afterwards */
	reset_world(); sweet_bt_init(); nev = 0; log_reset();
	CHECK(sbu_power_off() == 0, "power off");
	int dw = ev_bit(0x108401C4u, 3, 0, 0), rg = ev_bit(0x10840204u, 2, 0, 0);
	CHECK(dw >= 0 && rg > dw, "A27 order: DEV_WAKE low, then REG_ON low (%d %d)", dw, rg);
	CHECK(sbu_tx("a", 1) == ENXIO, "tx after power off refused");

	/* 7. nothing is touched when it must not be (F1, F2, boot-arg), and a failed start powers off (F4) */
	reset_world(); bootarg_sweetbt = 0; sweet_bt_init();
	CHECK(sbt.ops == NULL && nev == 0 && nrd == 0 && count_in("not enabled") == 1, "sweetbt=0: no register read or written (rd %d wr %d)", nrd, nev);
	reset_world(); board_gs101 = 0; sweet_bt_init();
	CHECK(sbt.ops == NULL && nev == 0 && nrd == 0 && count_in("gs101-only") == 1, "non-gs101 board: no register read or written (rd %d wr %d)", nrd, nev);
	for (int g = 0; g < 4; g++) {
		reset_world(); mem_gate[g] = 0; sweet_bt_init();
		CHECK(sbt.ops == NULL && nev == 0 && count_in("clock gate off") == 1 && !sbu.powered, "gate %d off: no store at all, backend not started", g);
	}
	reset_world(); usi_stuck = 1; sweet_bt_init();   /* a USI register that ignores the write: read-back != 1 */
	CHECK(sbt.ops == NULL && !sbu.powered && !sbu.uart_ok && count_in("usi mode read back") == 1, "USI read-back mismatch: error, ops never published");
	CHECK(nev >= 2 && evs[nev - 2].pa == 0x108401C4u && !(evs[nev - 2].v & 8u) && evs[nev - 1].pa == 0x10840204u && !(evs[nev - 1].v & 4u), "failed start powers off: DEV_WAKE low then REG_ON low (last stores)");
	reset_world(); sweet_bt_init(); log_reset();
	CHECK(sbt.ops == &sbu_ops, "ops published after a successful start");

	if (fails) { printf("RESULT: FAIL bt-s4a-uart-host (%d checks failed)\n", fails); return 1; }
	printf("RESULT: PASS bt-s4a-uart-host\n");
	return 0;
}
