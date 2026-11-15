/* Host test environment for bsd/dev/sweet_wdt.c (extracted from xnu/patches/0278 (port of 0193) + 0333): a fake CL0 register file, PMU, clock and SMC. T-5d2f. */
#ifndef WDT_HOST_ENV_H
#define WDT_HOST_ENV_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>

/* ---- fake hardware state, owned by the driver (wdt_host_test.c) ---- */
enum { EV_WR_WDT, EV_RD_WDT, EV_SMC };
struct fk_event { int kind; uint32_t off, val; uint64_t t; };
static struct fk_event fk_ev[8192];
static int fk_nev;
static uint64_t fk_now;
static uint32_t fk_wdt[8];                     /* WTCON WTDAT WTCNT WTCLRINT WTMINCNT */
static uint32_t fk_pmu_mask, fk_pmu_cnt, fk_pmu_rst, fk_pmu_sysip;
static int fk_smc_fail, fk_wtcon_ignore, fk_cannot_stop, fk_map_fail, fk_node_missing, fk_wtcon_partial;   /* partial: DBGACK (bit 16) does not latch, the period is unchanged (review pr4-wdt F4) */
static int fk_mmio, fk_nprints, fk_smc_n;
static char fk_logbuf[1 << 16];
static const char *fk_arg;                     /* the sweetwdt boot-arg, NULL = absent */
static uint64_t fk_last_cnt_write;  /* WDT model: reset when WTCNT is not written for one real period */
static uint32_t fk_real_period_ms;

/* patch 0333: the timer vehicle. The driver (wdt_host_test.c run_timer) calls wdt_timer_fire() when fk_timer_at is due, like the thread_call worker. */
static int fk_timer_alloc_fail, fk_timer_armed, fk_timer_n, fk_test_panics, fk_panic_arg = -1;   /* fk_panic_arg: sweetwdtabort value, -1 = absent */
static uint64_t fk_timer_at;
static unsigned fk_timer_last_ms, fk_test_panic_s;
static int wdt_timer_init(void) { return fk_timer_alloc_fail ? -1 : 0; }
static void wdt_timer_in(unsigned ms) { fk_timer_armed = 1; fk_timer_at = fk_now + ms; fk_timer_last_ms = ms; fk_timer_n++; }
static void wdt_test_panic_now(unsigned s) { fk_test_panics++; fk_test_panic_s = s; }
static int fk_boot_arg_panic(unsigned *v) { if (fk_panic_arg < 0) { return 0; } *v = (unsigned)fk_panic_arg; return 1; }
#define wdt_boot_arg_sweetwdtabort(v) fk_boot_arg_panic(v)

/* patch 0333, failed park: the patched sweet_machine_reset_x (tools/test_wdt_build.py extract_reset_source) is compiled into the same TU (-DRESET_SRC) against these stubs.
 * fk_sw_mode: 0 = the SWRESET SMC resets the phone (longjmp out), 1 = refused (returns x0 != 0), 2 = returns 0 and nothing resets. The CPU spin is fk_spin() = longjmp. */
enum { FK_SW_RESETS = 0, FK_SW_REFUSED = 1, FK_SW_SILENT = 2 };
static int fk_sw_mode, fk_swreset_n, fk_modewrite_n, fk_spin_n, fk_hw_reset_n, fk_park_n, fk_parkfail_arg = -1;   /* fk_parkfail_arg: sweetwdtparkfail value, -1 = absent */
static int fk_stopped_at_write[8], fk_nstopped_at_write, fk_print_before_stop, fk_testline_stopped = -1;
static int (*fk_stopped_probe)(void);          /* set by a case only AROUND a reset call: returns 1 once the pets are stopped (wdt_panicked); any print before the stop counts */
static int (*fk_probe_always)(void);           /* same probe, set for a whole case: sampled at every reset write and at the park-fail test line */
static jmp_buf fk_jb;
static uint64_t sweet_pmu_pa = 0x10000000ULL, sweet_mode_off = 0x810, sweet_swreset_off = 0x3a00;
#define SWEET_MODE_BOOTLOADER   0xfcULL
#define SWEET_SWRESET           2ULL
static void fk_spin(void) __attribute__((noreturn));
static void fk_spin(void) { fk_spin_n++; longjmp(fk_jb, 1); }
static uint64_t sweet_pmu_write(uint64_t pa, uint64_t val)
{
	int is_reset = (pa == sweet_pmu_pa + sweet_swreset_off);
	if (fk_nstopped_at_write < 8) { fk_stopped_at_write[fk_nstopped_at_write++] = fk_probe_always ? fk_probe_always() : -1; }
	if (!is_reset) { fk_modewrite_n++; return 0; }
	fk_swreset_n++; (void)val;
	if (fk_sw_mode == FK_SW_RESETS) { fk_hw_reset_n++; longjmp(fk_jb, 2); }
	return fk_sw_mode == FK_SW_REFUSED ? 0xfffffffffffffff7ULL : 0;
}
void sweet_restart_test_park_fail(void) __attribute__((noreturn));
void sweet_wdt_stop_pets(void);
static void __attribute__((unused)) wdt_test_park_fail_now(void) { fk_park_n++; sweet_restart_test_park_fail(); }
static int fk_boot_arg_parkfail(unsigned *v) { if (fk_parkfail_arg < 0) { return 0; } *v = (unsigned)fk_parkfail_arg; return 1; }
#define wdt_boot_arg_sweetwdtparkfail(v) fk_boot_arg_parkfail(v)

static void fk_model_arm(void);
#define wdt_log(...) fk_printf(__VA_ARGS__)
static void fk_printf(const char *fmt, ...)
{
	char tmp[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	if (fk_stopped_probe != NULL && !fk_stopped_probe()) {
		fk_print_before_stop++;                /* a print inside the reset path before the stop store (it could fault in the panic path) */
	}
	if (strncmp(tmp, "SWEET WDT TEST: park SMC skipped", 32) == 0) {
		fk_testline_stopped = fk_probe_always ? fk_probe_always() : -2;
	}
	if (strlen(fk_logbuf) + strlen(tmp) < sizeof(fk_logbuf) - 1) {
		strcat(fk_logbuf, tmp);
	}
	fk_nprints++;
}
static int fk_boot_arg(char *buf, int len)
{
	if (fk_arg == NULL) {
		return 0;
	}
	snprintf(buf, (size_t)len, "%s", fk_arg);
	return 1;
}
#define wdt_boot_arg_sweetwdt(buf, len) fk_boot_arg((buf), (len))
static uint64_t wdt_now_ms(void) { return fk_now; }
static void wdt_delay_ms(unsigned ms) { fk_now += ms; }
static uint32_t wdt_rd(uint32_t off)
{
	fk_mmio++;
	if (fk_nev < 8192) {
		fk_ev[fk_nev++] = (struct fk_event){ EV_RD_WDT, off, fk_wdt[off / 4], fk_now };
	}
	return fk_wdt[off / 4];
}
static void wdt_wr(uint32_t off, uint32_t v)
{
	fk_mmio++;
	if (fk_nev < 8192) {
		fk_ev[fk_nev++] = (struct fk_event){ EV_WR_WDT, off, v, fk_now };
	}
	if (off == 0 && fk_wtcon_ignore) {
		return;
	}
	if (off == 0 && fk_wtcon_partial) {
		v &= ~0x10000u;
	}
	if (off == 0 && fk_cannot_stop && (fk_wdt[0] & 0x21) == 0x21 && (v & 0x21) != 0x21) {
		return;
	}
	fk_wdt[off / 4] = v;
	if (off == 8) {
		fk_last_cnt_write = fk_now;
	}
	if (off == 0 || off == 4) {
		fk_model_arm();
	}
}
static uint32_t wdt_pmu_rd(uint32_t off)
{
	fk_mmio++;
	return off == 0x1244 ? fk_pmu_mask : off == 0x1220 ? fk_pmu_cnt : off == 0x3b44 ? fk_pmu_rst : off == 0x810 ? fk_pmu_sysip : 0xdeadbeef;
}
static void wdt_set_bootloader(void)
{
	fk_smc_n++;
	if (fk_nev < 8192) {
		fk_ev[fk_nev++] = (struct fk_event){ EV_SMC, 0x810, 0xfc, fk_now };
	}
	if (!fk_smc_fail) {
		fk_pmu_sysip = 0xfc;
	}
}
static int wdt_map(uint64_t pmu_pa, uint32_t sysip_off, const char **what)
{
	(void)pmu_pa;
	if (fk_node_missing) { *what = "read fault: sweet-wdt node"; return -1; }
	if (fk_map_fail) { *what = "map failed: wdt"; return -1; }
	wdt_off_mask = 0x1244; wdt_off_cnt = 0x1220; wdt_off_rst = 0x3b44; wdt_off_sysip = sysip_off;
	return 0;
}
/* WDT model: while ENABLE|RSTEN are set, the counter is reloaded by WTCNT writes; no reload for one real period = reset */
static void fk_model_arm(void)
{
	if ((fk_wdt[0] & 0x21) == 0x21) {
		uint32_t prescale = (fk_wdt[0] >> 8) & 0xff;
		fk_real_period_ms = (uint32_t)((uint64_t)fk_wdt[1] * (prescale + 1) * 1000u / 192000u);
		fk_last_cnt_write = fk_now;
	} else {
		fk_real_period_ms = 0;
	}
}
static int fk_reset_now(void)       /* 1 when the model WDT would have fired by fk_now */
{
	return fk_real_period_ms != 0 && fk_now - fk_last_cnt_write >= fk_real_period_ms;
}
#endif
