/*
 * Milestone 2: one boot answers the questions that decide the XNU port.
 *   - CPU feature registers (16K granule, GIC sysreg interface)
 *   - PSCI/SMCCC version and which PSCI calls the Qualcomm TZ offers
 *   - SMP: PSCI CPU_ON for the 7 secondaries, each reports its MPIDR
 *   - GICv3 Group 1 IRQ from the virtual timer (INTID 19 on Qualcomm)
 * Any unexpected exception paints a blue panic screen.
 */
#include <stdint.h>
#include "console.h"
#include "mmu.h"
#include "loader.h"
#include "fdt.h"
#include "pstore.h"
#include "ufs.h"
#include "ufsinit.h"
#include "usbprobe.h"
#include "wlanprobe.h"
#include "wlanrails.h"
#include "wlanen.h"
#include "wlansmc.h"
#include "wlanlink.h"
#include "gpuprobe.h"
#include "gpupower.h"
#include "gpuclk.h"
#include "gpurails.h"
#include "gpurailon.h"
#include "gpus2mpu.h"
#include "gpujob.h"
#include "touchprobe.h"
#ifdef BOOT_XNU
#include "loader.h"
#endif
#include "ramlog.h"
#ifdef USB_GADGET
#include "usbgadget.h"
#include "usbpool.h"
#ifdef UDUMP
#if !defined(USB_GADGET)
#error "--udump requires the USB gadget (build.sh forces --usb-gadget)"
#endif
#include "udump.h"
#endif
#endif

#define IMEM_RESTART_REASON	0x146aa65cUL	/* qcom,msm-imem restart_reason@65c */
#define RESTART_BOOTLOADER	0x77665500U
#define PSHOLD			0x0c264000UL

#define GICR_TYPER		0x08
#define GICR_WAKER		0x14
#define GICR_SGI		0x10000
#define GICR_IGROUPR0		(GICR_SGI + 0x080)
#define GICR_ISENABLER0		(GICR_SGI + 0x100)
#define GICR_ICENABLER0		(GICR_SGI + 0x180)
#define GICR_IPRIORITYR		(GICR_SGI + 0x400)
#define GICR_ISPENDR0		(GICR_SGI + 0x200)
#define GICR_ISACTIVER0		(GICR_SGI + 0x300)
#define GICR_ICFGR1		(GICR_SGI + 0xc04)
#define PTIMER_INTID		18	/* DT: <GIC_PPI 2 ...> */
#define SGI_INTID		1

#define PSCI_VERSION		0x84000000UL
#define PSCI_CPU_SUSPEND	0xc4000001UL
#define PSCI_CPU_OFF		0x84000002UL
#define PSCI_CPU_ON		0xc4000003UL
#define PSCI_MIGRATE_INFO_TYPE	0x84000006UL
#define PSCI_FEATURES		0x8400000aUL
#define PSCI_SYSTEM_SUSPEND	0xc400000eUL
#define PSCI_SYSTEM_RESET2	0xc4000012UL
#define SMCCC_VERSION		0x80000000UL

#define rsr(r) ({ uint64_t _v; __asm__ volatile("mrs %0, " r : "=r"(_v)); _v; })
#define wsr(r, v) __asm__ volatile("msr " r ", %0" : : "r"((uint64_t)(v)))
#define isb() __asm__ volatile("isb" ::: "memory")

static inline uint32_t rd32(uintptr_t a) { return *(volatile uint32_t *)a; }
static inline uint64_t rd64(uintptr_t a) { return *(volatile uint64_t *)a; }
static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline void wr8(uintptr_t a, uint8_t v) { *(volatile uint8_t *)a = v; }

extern char secondary_entry[];
#if SOC_GS101
static const uint64_t board_mpidrs[NR_CPUS] = {
	BOARD_MPIDR_0, BOARD_MPIDR_1, BOARD_MPIDR_2, BOARD_MPIDR_3,
	BOARD_MPIDR_4, BOARD_MPIDR_5, BOARD_MPIDR_6, BOARD_MPIDR_7,
};
#endif

static const char *stage;	/* set at runtime: no absolute pointers in data */
volatile uint64_t probe_entry_el;
static volatile uint32_t probe_guard, probe_fault;
#if SOC_GS101
static volatile uintptr_t probe_fault_address;
#endif
static volatile uint64_t sec_mpidr[NR_CPUS];
static volatile uint64_t sec_probe[4];	/* CPU1 redistributor write test */
static volatile uint32_t irq_count, irq_other, irq_last;
static volatile int irq_src;
static volatile uint64_t cpu_report[NR_CPUS][7];
static uint64_t now(void);
static void delay_ms(unsigned ms);
static uintptr_t find_gicr(void);

/* ICC_SGI1R_EL1 value targeting the calling core only. */
static uint64_t sgi_self(unsigned id)
{
	uint64_t m = rsr("mpidr_el1");
	return ((m >> 16) & 0xff) << 32 |	/* Aff2 */
	       ((m >> 8) & 0xff) << 16 |	/* Aff1 */
	       (uint64_t)id << 24 |
	       (1U << (m & 0xf));		/* target list: Aff0 */
}

static uint64_t smc(uint64_t fn, uint64_t a1, uint64_t a2, uint64_t a3)
{
	register uint64_t x0 __asm__("x0") = fn;
	register uint64_t x1 __asm__("x1") = a1;
	register uint64_t x2 __asm__("x2") = a2;
	register uint64_t x3 __asm__("x3") = a3;
	__asm__ volatile("smc #0"
			 : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
			 :
			 : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
			   "x12", "x13", "x14", "x15", "x16", "x17", "memory");
	return x0;
}

/*
 * Fault-guarded MMIO read, shared with usbprobe.c (declared in
 * usbprobe.h): sets probe_guard around the load so a data abort or
 * SError from a gated clock/power domain lands in exc_handler's
 * probe_guard skip and the caller prints "unreadable" instead of
 * panicking. That skip is compiled in only for SOC_GS101, so on
 * other boards try_read32 is only as guarded as a raw read (and
 * nothing calls it there).
 */
int try_read32(uintptr_t address, uint32_t *value)
{
	probe_fault = 0;
	probe_guard = 1;
	uint32_t v = rd32(address);
	__asm__ volatile("dsb sy\n\tmsr daifclr, #4\n\tisb\n\tmsr daifset, #4" ::: "memory");
	probe_guard = 0;
	if (!probe_fault)
		*value = v;
	return !probe_fault;
}

#if SOC_GS101
/* Defined for SOC_GS101 only; declared (with try_read32) in
 * shim/usbgadget.h, whose only caller is usbgadget.c - linked into the
 * shim only when build.sh runs with --usb-gadget. */
int try_write32(uintptr_t address, uint32_t value)
{
	probe_fault = 0;
	probe_guard = 1;
	wr32(address, value);
	__asm__ volatile("dsb sy\n\tmsr daifclr, #4\n\tisb\n\tmsr daifset, #4" ::: "memory");
	probe_guard = 0;
	return !probe_fault;
}

struct wdt_state {
	uint32_t wtcon;
	uint32_t wtcnt;
	uint32_t after;
	int read_ok;
	int write_ok;
};

static void disable_bluejay_watchdog(uintptr_t base, struct wdt_state *s)
{
	s->read_ok = 0;
	s->write_ok = 0;
	if (!base)
		return;
	if (try_read32(base, &s->wtcon) && try_read32(base + 8, &s->wtcnt)) {
		s->read_ok = 1;
		uint32_t val = s->wtcon & ~((1U << 5) | (1U << 0)); /* Clear ENABLE (bit 5) & RSTEN (bit 0) */
		if (try_write32(base, val) && try_read32(base, &s->after))
			s->write_ok = 1;
	}
}

static void report_bluejay_watchdog(const char *label, const struct wdt_state *s)
{
	if (!s->read_ok) {
		con_str(label, "WTCON/WTCNT read fault");
		con_hex("WDT FAR", probe_fault_address);
		return;
	}
	con_hex2(label, "WTCON=", s->wtcon, "WTCNT=", s->wtcnt);
	if (!s->write_ok) {
		con_str(label, "WTCON write/read fault");
		con_hex("WDT FAR", probe_fault_address);
		return;
	}
	con_hex2(label, "stopped=", s->after, "disabled=", (s->after >> 5) & 1);
}

/* Bluejay exit: try every reset we know and print each result, so a phone
 * that does not reset stays on screen with the reason (never a silent hang).
 * PSCI SYSTEM_RESET, then SYSTEM_RESET2 (arch warm reset), then a 1-tick WDT
 * reset. ponytail: the WDT path assumes the s3c2410 layout and an unmasked
 * PMU reset source (UNSURE on gs101); it only runs after both PSCI calls
 * returned, i.e. already failed. */
#ifdef REBOOT_BL
/* Reset INTO THE BOOTLOADER without a button press (docs/research/10): ABL reads the volatile PMU register
 * SYSIP_DAT0 (0x17460810) after reset; 0xfc = "reboot bootloader". Only EL3 can write it: the PMU SMC
 * 0x82000504 (x1 = PA, x2 = 1 write, x3 = value) is what ABL itself uses. Then SYSTEM_CONFIGURATION.SWRESET_SYSTEM
 * (0x17463a00 bit 1) through the same SMC. No flash/misc write; ABL clears the register on the next boot.
 * Unverified on hardware until the first run: every step prints its result, and we never reset unless the
 * readback is 0xfc. */
static void bluejay_reset_bootloader(void)
{
	const uintptr_t sysip = 0x17460810UL, swreset = 0x17463a00UL;
	uint32_t before = 0xdead, after = 0xdead, rst = 0xdead;
	try_read32(sysip, &before);
	try_read32(0x17463b44UL, &rst);
	uint64_t r1 = smc(0x82000504UL, sysip, 1, 0xfc);
	try_read32(sysip, &after);
	con_hex2("REBOOT_BL SYSIP before", "", before, "RST_STAT", rst);
	con_hex2("REBOOT_BL SMC ret", "", (uint32_t)r1, "after", after);
	wait_ticks(counter_freq() * 8);		/* leave the numbers on screen for the camera */
	if (after != 0xfc) {
		con_str("REBOOT_BL", "register not 0xfc: no bootloader reset");
		wait_ticks(counter_freq() * 8);
		return;
	}
	con_hex("REBOOT_BL SWRESET SMC ret", (uint32_t)smc(0x82000504UL, swreset, 1, 2));
	wait_ticks(counter_freq() * 2);
	con_str("REBOOT_BL", "still running: falling back to the reset chain");
}
#endif

static void __attribute__((noreturn)) bluejay_reset_chain(void)
{
#ifdef REBOOT_BL
	bluejay_reset_bootloader();
#endif
	con_hex("RESET PSCI SYSTEM_RESET ret", smc(PSCI_SYSTEM_RESET, 0, 0, 0));
	con_hex("RESET PSCI SYSTEM_RESET2 ret", smc(PSCI_SYSTEM_RESET2, 0x80000000UL, 0, 0));
	if (WDT_CL0_BASE) {
		int ok = try_write32(WDT_CL0_BASE + 4, 1) && try_write32(WDT_CL0_BASE + 8, 1) &&
			 try_write32(WDT_CL0_BASE, (3U << 3) | (1U << 5) | 1U);
		con_hex("RESET WDT CL0 armed", ok);
		wait_ticks(counter_freq());
	}
	con_str("RESET", "all resets failed: hold Power+VolDown");
	for (;;) __asm__ volatile("wfi");
}

	/* WTCON/WTCNT layout and ENABLE/RSTEN bits: https://github.com/torvalds/linux/blob/master/drivers/watchdog/s3c2410_wdt.c */
/* Linaro reports ABL disables DECON HW trigger: https://patchew.org/linux/20241216-gs101-simplefb-v1-0-8ccad1830281%40linaro.org/ */
/* DECON0 is 0x1c300000 in GS101 DTS; TRIG_CON is 0x30 and HW_TRIG_EN is bit 0: https://android.googlesource.com/kernel/google-modules/display/+/661047c0c1c4939aa81fcc822bb21dc5a484f913/samsung/cal_9845/regs-decon.h */
/* Whole-word command-mode trigger value 0x3061: https://gitlab.com/postmarketOS/pmaports/-/blob/v26.06/device/testing/soc-google-gs101/00-enable-fb.sh */
static void decon_rearm_trigger(void)
{
	const uintptr_t trigger = DECON_BASE + 0x30;
	uint32_t before, after;
	if (!try_read32(trigger, &before)) {
		con_str("DECON", "trigger read fault");
		con_hex("DECON FAR", probe_fault_address);
		return;
	}
	if (!try_write32(trigger, 0x3061) || !try_read32(trigger, &after)) {
		con_str("DECON", "trigger write/read fault");
		con_hex("DECON FAR", probe_fault_address);
		return;
	}
	con_hex2("DECON TRIG", "before=", before, "after=", after);
}

static void report_cpu(unsigned cpu)
{
	uint64_t midr, mpidr, mmfr0, isar1;
	__asm__ volatile("mrs %0, midr_el1" : "=r"(midr));
	__asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
	__asm__ volatile("mrs %0, id_aa64mmfr0_el1" : "=r"(mmfr0));
	__asm__ volatile("mrs %0, id_aa64isar1_el1" : "=r"(isar1));
	con_hex2("CPU MIDR", "id=", cpu, "value=", midr);
	con_hex("MPIDR", mpidr);
	con_hex("MMFR0", mmfr0);
	con_hex("ISAR1", isar1);
	con_hex2("PAC APA/API", "APA=", (isar1 >> 4) & 0xf, "API=", (isar1 >> 8) & 0xf);
	con_hex2("PAC GPA/GPI", "GPA=", (isar1 >> 32) & 0xf, "GPI=", (isar1 >> 36) & 0xf);
	con_hex("CNTFRQ", rsr("cntfrq_el0"));
	con_hex("timer freq used", counter_freq());
}

static int prepare_bluejay_cpu(unsigned cpu)
{
	uint64_t caller = rsr("mpidr_el1");
	const uintptr_t inform = 0x17460000UL + 0x0860 + ((caller >> 8) & 7) * 4;
	const uintptr_t bid_enable = 0x17470200UL;
	const uintptr_t bid_upend = 0x17470208UL;
	const uintptr_t bid_clear = 0x1747020cUL;
	uint32_t pending = 0;
	/* GS101 SMC args and caller-indexed CPU_INFORM: https://android.googlesource.com/kernel/gs/+/ebc441967af0637888ccb51502855ce76671a726/drivers/soc/samsung/gs101-pmu.c and https://android.googlesource.com/kernel/gs/+/ebc441967af0637888ccb51502855ce76671a726/drivers/soc/samsung/exynos-pmu.c; see docs/research/05-fact-check.md section 5. */
	uint64_t smc_ret = smc(0x82000504UL, inform, 1, 0);
	int ok = (int64_t)smc_ret == 0;
	uint32_t enabled = 0;
	if (ok) ok = try_read32(bid_enable, &enabled);
	if (ok) ok = try_write32(bid_enable, enabled & ~(1U << cpu));
	if (ok) ok = try_read32(bid_upend, &pending);
	if (ok) ok = try_write32(bid_clear, pending & (1U << cpu));
	con_hex("PMU SMC ret", smc_ret);
	con_hex("PMU intr-gen success", ok);
	return ok;
}


#ifdef USB_GADGET
/* ---- stage-2 USB CDC-ACM log gadget (build flag --usb-gadget, default OFF) ----
 * Runs after the stage-1 probe and before the
 * pager. Fail closed at every step: a refused pool or a failed init
 * touches no register and falls through to the pager. */
#ifndef USB_GADGET_SECONDS
#define USB_GADGET_SECONDS 600u	/* window in which the host may attach */
#endif
extern char _head[], _end[];

static uint64_t usbg_ticks(void)
{
	return now();
}

/* Log streaming state. cursor is an ABSOLUTE ramlog line number
 * (ramlog_total() based), so it survives eviction. Every new host
 * session (ready 0 -> 1) replays the whole retained log first. */
static struct {
	uint32_t cursor;
	int was_ready;
	size_t len, off;
	char buf[4096];
} usbg;

static void usbg_pump(void)
{
	if (!usbgadget_ready()) {
		usbg.was_ready = 0;
		return;
	}
	uint32_t total = ramlog_total();
	uint32_t first;
	if (!usbg.was_ready) {
		usbg.was_ready = 1;
		usbg.cursor = total - ramlog_lines();	/* new session: replay all */
		usbg.len = usbg.off = 0;
	}
	if (usbg.off < usbg.len) {
		usbg.off += usbgadget_write(usbg.buf + usbg.off, usbg.len - usbg.off);
		return;
	}
	usbg.len = usbg.off = 0;
	if (usbg.cursor == total)
		return;	/* nothing new: skip the (uncached, O(n)) ring scan */
	first = total - ramlog_lines();
	if ((int32_t)(usbg.cursor - first) < 0)
		usbg.cursor = first;	/* those lines were evicted: skip them */
	/* A few lines per call keeps each poll iteration short (ramlog_copy_line scans). */
	for (unsigned n = 0; n < 8 && (int32_t)(total - usbg.cursor) > 0; n++) {
		char line[RAMLOG_LINE_MAX];
		unsigned l = ramlog_copy_line(usbg.cursor - first, line, sizeof line);
		if (usbg.len + l + 2 > sizeof usbg.buf)
			break;
		for (unsigned i = 0; i < l; i++)
			usbg.buf[usbg.len++] = line[i];
		usbg.buf[usbg.len++] = '\r';
		usbg.buf[usbg.len++] = '\n';
		usbg.cursor++;
	}
}

static uint32_t usbg_be32(uintptr_t p)
{
	const volatile uint8_t *b = (const volatile uint8_t *)p;
	return (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 | (uint32_t)b[2] << 8 | b[3];
}

static int usbg_on;
static uint64_t usbg_end;

/* Runs from every console line and wait loop once the gadget is up. */
static void usbg_service(void)
{
	if (!usbg_on)
		return;
	usbgadget_poll();
	usbg_pump();
	if (usbgadget_faulted()) {	/* dead for good: stop polling it */
		usbg_on = 0;
		con_service_hook = 0;
	}
}

/* Bring the gadget up EARLY (right after the display/watchdog setup) and hook
 * it into the console, so the host sees every later line live and a hang shows
 * up as "the last line before silence". Fails closed: on any refusal the probe
 * simply continues without USB. */
static void usb_gadget_start(uintptr_t dtb)
{
	static struct fdt_memmap mm;	/* ~2 KiB: keep it off the 32 KiB stack */
	struct usbpool_avoid avoid[5];
	uint32_t lo, hi;
	uint64_t pool = 0, initrd_lo = 0, initrd_hi = 0;
	const char *why = "";

	con_str("SECTION", "USB gadget (live log)");
	if (!fdt_collect_memmap(dtb, &mm)) {
		con_str("USB gadget", "memmap failed: skipped");
		return;
	}
	if (!try_read32(USBPROBE_DWC3_BASE + USBPROBE_GEVNTADRLO_OFF, &lo) ||
	    !try_read32(USBPROBE_DWC3_BASE + USBPROBE_GEVNTADRHI_OFF, &hi)) {
		con_str("USB gadget", "GEVNTADR unreadable: skipped");
		return;
	}
	int have_lo = fdt_chosen_u64(dtb, "linux,initrd-start", &initrd_lo);
	int have_hi = fdt_chosen_u64(dtb, "linux,initrd-end", &initrd_hi);
	if (have_lo != have_hi) {	/* half-present initrd: cannot be avoided */
		con_str("USB gadget refused", "initrd range incomplete");
		return;
	}
	avoid[0].lo = (uint64_t)(uintptr_t)_head;
	avoid[0].hi = (uint64_t)(uintptr_t)_end;
	avoid[0].what = "pool overlaps the shim image";
	avoid[1].lo = dtb;
	avoid[1].hi = dtb + usbg_be32(dtb + 4);
	avoid[1].what = "pool overlaps the FDT";
	avoid[2].lo = initrd_lo;
	avoid[2].hi = initrd_hi;
	avoid[2].what = "pool overlaps the initrd";
	avoid[3].lo = pstore_zone_addr();
	avoid[3].hi = avoid[3].lo + pstore_zone_size();
	avoid[3].what = "pool overlaps the pstore zone";
	avoid[4].lo = FB_BASE;
	avoid[4].hi = (uint64_t)FB_BASE + FB_SIZE;
	avoid[4].what = "pool overlaps the framebuffer";
#ifdef USB_DMA_HI_BASE
	con_hex("USBDMA hi base", USB_DMA_HI_BASE);
	if (usbpool_choose_hi(&mm, USB_DMA_HI_BASE, USBG_POOL_MIN, avoid, 5, &pool, &why)) {
#else
	if (usbpool_choose(&mm, (uint64_t)hi << 32 | lo, USBG_POOL_MIN, "abl",
			   avoid, 5, &pool, &why)) {
#endif
		con_str("USB gadget refused", why);
		return;
	}
	con_hex("USB gadget pool", pool);
	static struct usbgadget_hooks hooks;
	hooks.ticks = usbg_ticks;
	hooks.ticks_freq = counter_freq();
	if (usbgadget_init((uintptr_t)pool, USBG_POOL_MIN, &hooks)) {
		con_str("USB gadget", "init failed: pager only");
		return;
	}
	con_str("USB gadget", "run tools/usb-log.py capture on the PC");
	usbg_end = now() + counter_freq() * USB_GADGET_SECONDS;
	usbg_on = 1;
	con_service_hook = usbg_service;
}

/* End of the probe: keep serving the host for the rest of the window (at least
 * 8 s so the last lines are delivered), then halt the core before the pager /
 * reset chain. */
static void usb_gadget_finish(void)
{
	if (!usbg_on)
		return;
	uint64_t min_end = now() + counter_freq() * 8;
	uint64_t end = (int64_t)(usbg_end - min_end) > 0 ? usbg_end : min_end;
	while ((int64_t)(end - now()) > 0 && usbg_on)
		wait_ticks(counter_freq() / 1000);	/* polls the gadget via the console hook */
	con_str("USB gadget", usbgadget_faulted() ? "faulted: window ended" : "window over");
	con_service_hook = 0;
	usbg_on = 0;
	usbgadget_stop();
}

#ifdef UDUMP
/* Userdata dump (build flag --udump): stream the first N MiB of
 * the GPT 'userdata' partition as framed records over the gadget, read-
 * only, inside the REMAINING gadget window (udump_run stops at usbg_end
 * and every chunk is bounded by 5 s, so the reset chain is never
 * blocked). Runs after the UFS/GPT match was found (ufs_full_sequence)
 * and after the USB probe, before the window is handed back. */
static int udump_log_pending(void)
{
	return usbg.off < usbg.len || usbg.cursor != ramlog_total();
}

static void udump_gadget_poll(void)
{
	usbgadget_poll();
	usbg_pump();
}

static void udump_dump(void)
{
	uint64_t first, last;
	if (!ufs_userdata_found(&first, &last)) {
		con_str("UDUMP refused", "userdata not found in GPT");
		return;
	}
	static struct udump_hooks uh;	/* keep the hooks off the 32 KiB stack */
	uh.read = ufs_read_userdata;
	uh.poll = udump_gadget_poll;
	uh.log_pending = udump_log_pending;
	uh.ticks = usbg_ticks;
	uh.ticks_freq = counter_freq();
	int rc = udump_run(&uh, ufs_udump_stage, sizeof ufs_udump_stage,
			   first, last, UDUMP_MIB_BLOCKS, usbg_end);
	con_hex2("UDUMP result", "rc=", (uint32_t)rc, "mib=", UDUMP_MIB);
}
#endif
#endif

static void __attribute__((noreturn)) bluejay_probe(uintptr_t dtb)
{
	stage = "bluejay probe";
	struct wdt_state wdt_cl0 = {0}, wdt_cl1 = {0};
	disable_bluejay_watchdog(WDT_CL0_BASE, &wdt_cl0);
	disable_bluejay_watchdog(WDT_CL1_BASE, &wdt_cl1);

	struct fdt_video video = { FB_BASE, FB_SIZE, FB_W, FB_H, FB_STRIDE };
	int live_framebuffer = fdt_framebuffer(dtb, &video);
	if (live_framebuffer) {
		if (!video.width) video.width = FB_W;
		if (!video.height) video.height = FB_H;
		if (!video.stride) video.stride = FB_STRIDE;
		if (!video.size) video.size = FB_SIZE;
	} else {
		video = (struct fdt_video){ FB_BASE, FB_SIZE, FB_W, FB_H, FB_STRIDE };
	}
	con_set_video(video.base, video.width, video.height, video.stride);
	con_clear(0xff000000U);
	con_str("FB", live_framebuffer ? "from live FDT" : "board fallback");
	con_hex("ENTRY EL", probe_entry_el);
	/* Locate the ramoops console zone before anything can crash: from
	 * here on every checkpoint save (and the panic path) hands the log
	 * to Android through pstore. */
	pstore_init(dtb);
	pstore_save();			/* checkpoint 1: earliest log */
	report_bluejay_watchdog("WDT CL0", &wdt_cl0);
	report_bluejay_watchdog("WDT CL1", &wdt_cl1);
	decon_rearm_trigger();
	pstore_save();			/* checkpoint 2: watchdog + DECON */
#ifdef BOOT_XNU
	/* --boot-xnu: hand over to the SWEETXNU payload (found in the ABL
	 * ramdisk) right after the display/watchdog setup. loader_boot()
	 * returns only on error: print why and fall through to the normal
	 * probe (and the USB log, if built in), so a failure still yields
	 * data. The probe sections never run on the success path. */
	con_str("SECTION", "boot XNU");
#ifdef XNU_UFS
	/* ABL shut the UFS host down; XNU's sweet_ufs driver (patch 0080) expects HCE + link up. */
	con_hex("UFS cold init rc", (uint32_t)ufs_cold_init());
#endif
#if defined(TOUCH_PROBE) && defined(TOUCH_RAILS_WRITE)
	/* --boot-xnu --touch-probe=rails: switch the touch rails on (ACPM, volatile) BEFORE XNU starts: observed run touch1/rails2 that the FTM5 only
	 * answers with them on. Each step stops the next on a refusal; XNU boots either way. */
	{
		int rails_e = touch_probe_rails(0);

		if (!rails_e)
			rails_e = touch_probe_rails(1);
		if (!rails_e)
			(void)touch_probe_rails(2);
		pstore_save();
	}
#endif
#if defined(TOUCH_PROBE) && defined(TOUCH_DVFS)
	/* --boot-xnu --touch-probe=dvfsr: read the CPU cluster rates (ACPM FREQ_GET) before XNU starts. --touch-probe=dvfs --dvfs K0,K1,K2 also asks the
	 * firmware for those rates (volatile, table rows below the ceilings, docs/research/25), so XNU boots at them. Any refusal or failure is logged and
	 * XNU boots anyway. */
	{
		int dvfs_e = touch_probe_dvfs(0);

		if (!dvfs_e)
			dvfs_e = touch_probe_dvfs(1);
#ifdef TOUCH_DVFS_WRITE
		if (!dvfs_e)
			(void)touch_probe_dvfs(2);
#endif
		pstore_save();
	}
#endif
#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)
	/* --boot-xnu --gpu-handover: switch the shader rail BUCK2S on (audited ACPM writer, gpurailon.c: zero writes unless it reads exactly off with OUT1 in the
	 * window) and LEAVE it on for XNU, which has no ACPM client (docs/research/30; patch 0103 powers the cores through the GPU registers, 0102 only reads).
	 * A refusal is logged and XNU boots anyway; an UNVERIFIED or MISMATCH result is put back (gpurailon.h, review M1); a verified rail stays on. */
	int gpu_ho;

	con_str("SECTION", "GPU handover (rail on, left on)");
	gpu_ho = gpu_rail_enable();
	con_hex("GPU handover rail (0=on)", (uint32_t)gpu_ho);
	if (gpu_ho == RAIL_UNVERIFIED || gpu_ho == RAIL_MISMATCH)
		gpu_rail_restore(gpu_ho, RAIL_POWER_NONE);
	pstore_save();
#endif
	{
		const char *why = loader_boot(dtb);
		con_str("BOOT XNU failed", why ? why : "?");
		pstore_save();
#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)
		if (gpu_ho == RAIL_ON_OK)	/* loader_boot returned, XNU is not coming: do not leave the rail on (review L2) */
			gpu_rail_restore(gpu_ho, RAIL_POWER_NONE);
#endif
		delay_ms(3000);
	}
#endif
#ifdef USB_GADGET
	usb_gadget_start(dtb);		/* live log from here on */
#endif

	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "boot CPU");
	report_cpu(0);
	con_hex("GICD_TYPER", rd32(GICD_BASE + 4));
	con_hex("GICD_CTLR", rd32(GICD_BASE));
	uintptr_t redistributor = find_gicr();
	if (redistributor) con_hex("GICR_TYPER", rd64(redistributor + GICR_TYPER));
	else con_str("GICR", "not found");
	con_hex("PSCI_VERSION", smc(PSCI_VERSION, 0, 0, 0));
	con_hex2("PSCI CPU_ON", "fn=", smc(PSCI_FEATURES, PSCI_CPU_ON, 0, 0), "reset=",
		 smc(PSCI_FEATURES, PSCI_SYSTEM_RESET, 0, 0));
	pstore_save();			/* checkpoint 3: boot CPU section */

	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "secondary CPUs");
	for (unsigned cpu = 1; cpu < NR_CPUS; cpu++) {
		con_clear(0xff000000U);
		con_hex("SECTION secondary CPU", cpu);
		int32_t ret = (int32_t)smc(PSCI_CPU_ON, board_mpidrs[cpu],
					   (uintptr_t)secondary_entry, cpu);
		/* Print the first return value before any decision is taken. */
		con_hex2("CPU_ON 1st", "cpu=", cpu, "ret=", (uint32_t)ret);
		int retried = 0;
		if (ret == 0) {
			/* SUCCESS: a slow core is still powering up; never PMU-prep
			 * against it (clearing its BID state is the round-1 race). */
			con_str("CPU path", "PSCI CPU_ON success; wait");
		} else if (ret == -4 || ret == -5) {
			/* ALREADY_ON / ON_PENDING: the core is coming; just wait. */
			con_str("CPU path", "already on / on pending; wait");
		} else {
			con_str("CPU path", "error: PMU prep, then one retry");
			prepare_bluejay_cpu(cpu);
			ret = (int32_t)smc(PSCI_CPU_ON, board_mpidrs[cpu],
						   (uintptr_t)secondary_entry, cpu);
			con_hex2("CPU_ON 2nd", "cpu=", cpu, "ret=", (uint32_t)ret);
			retried = 1;
		}
		/* 2 s: A76s can take ~300 ms to reach secondary_entry. */
		uint64_t deadline = now() + counter_freq() * 2;
		while (!cpu_report[cpu][6] && now() < deadline)
			;
		if (cpu_report[cpu][6]) {
			con_str("CPU status", retried ? "reported after retry" : "reported");
		} else if (ret == 0 || ret == -4 || ret == -5) {
			/* PSCI accepted the core; the report is just slow, not failed. */
			con_str("CPU status", "late: no report yet");
		} else {
			con_str("CPU status", "failed");
		}
		if (cpu_report[cpu][6]) {
			con_hex("CPU MPIDR", cpu_report[cpu][0]);
			con_hex("CPU MIDR", cpu_report[cpu][1]);
			con_hex("CPU MMFR0", cpu_report[cpu][2]);
			con_hex("CPU ISAR1", cpu_report[cpu][3]);
			con_hex2("PAC APA/API", "APA=", (cpu_report[cpu][3] >> 4) & 0xf,
				 "API=", (cpu_report[cpu][3] >> 8) & 0xf);
			con_hex2("PAC GPA/GPI", "GPA=", (cpu_report[cpu][3] >> 32) & 0xf,
				 "GPI=", (cpu_report[cpu][3] >> 36) & 0xf);
			con_hex("CPU CNTFRQ", cpu_report[cpu][4]);
			con_hex("CPU GICR_TYPER", cpu_report[cpu][5]);
		}
		pstore_save();		/* checkpoint: per-CPU secondary section */
	}
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "FDT memory and reserved");
	fdt_probe_report(dtb);
	pstore_save();			/* checkpoint: FDT memory map section */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "UFS");
	test_ufs_bluejay(dtb);
	pstore_save();			/* checkpoint: UFS section (review M1) */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "USB");
	/* Stage-1 read-only DWC3 probe (see usbprobe.c):
	 * ABL-handoff USB state, after the other probes and before the
	 * final pstore save/pager so it reaches screen and pstore alike. */
	usbprobe_report(dtb);
	pstore_save();			/* checkpoint: USB section */
#if defined(WLAN_LINK_RUN) && (defined(WLAN_ISO_RUN) || defined(WLAN_EN_RUN) || defined(WLAN_SMC_READ))
#error "--wlan-link contains the SMC and GPIO steps itself: it must not be combined with --wlan-iso / --wlan-en / --wlan-smc-read (separate reviewed units)"
#endif
#if defined(WLAN_ISO_RUN) && defined(WLAN_EN_RUN)
#error "--wlan-iso and --wlan-en must not be combined: the WLAN_EN writer must never run on a PHY whose isolation may be left released (review 2 F2)"
#endif
#ifdef WLAN_PROBE
	/* Read-only BCM4389 Wi-Fi path probe (wlanprobe.c); l1 = PMU + pins, then l2 =
	 * PCIe wrapper (gated, see below), which may hang on a gated block, so checkpoints in between. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN pmu+pins");
	wlan_probe_l1();
	pstore_save();
#ifdef WLAN_RAILS
	/* READ-ONLY rail / CMU_HSI2 / PMU words (wlanrails.c, docs/research/29 section 4): the S2MPG10 PCIe PHY LDOs through the audited ACPM client, the CMU words only once the UFS HCI inside HSI2 answers */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN rails/cmu");
	(void)wlan_rails();
	pstore_save();
#endif
#ifdef WLAN_LINK_RUN
	/* W3a (wlanlink.c, docs/research/29 section 14): the table-driven power-up chain S00..N with the teardown. Writes only through the table-checked w32; level 2 is compiled out of this image. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN link");
	(void)wlan_link_run();
	pstore_save();
#endif
#ifdef WLAN_SMC_READ
	/* W2a (wlansmc.c, docs/research/29): two EL3 SMC_CMD_PRIV_REG READs of PMU 0x17463EC4 / 0x17463EC0, compared with the MMIO words; nothing is written */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN smc read");
	(void)wlan_smc_read();
	pstore_save();
#endif
#ifdef WLAN_ISO_RUN
	/* WRITER via EL3 (wlansmc.c, docs/research/29 W2c): release the PCIe1 PHY isolation with one RMW SMC and put it back; preconditions: isolation set, PERST and WLAN_EN low, the SMC READ accepted */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN iso");
	(void)wlan_iso_run();
	pstore_save();
#endif
#ifdef WLAN_EN_RUN
	/* WRITER (wlanen.c, docs/research/29 W2b): WLAN_EN gpp14-2 high for 200 ms, sampled, then ALWAYS restored and read back. Only the two GPP14 registers, only with preconditions met. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN en");
	(void)wlan_en_run();
	pstore_save();
#endif
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "WLAN pcie");
#if defined(WLAN_ISO_RUN) || defined(WLAN_LINK_RUN)
	/* review 2 F2: a --wlan-iso / --wlan-link image never reads the PCIe wrapper (SYSREG/ELBI/DBI/PMA): a bit left set by a failed restore would open the level-2 gate on an unconfigured block, and
	 * wlan_probe_l2_group(4) reads DBI/ELBI too. Level 2 belongs to its own reviewed flag. */
	con_str("WLAN", "l2 skipped: --wlan-iso / --wlan-link image");
#else
	/* level 2 is gated (wlan_gate, docs/research/20 section 3 D1): the wrapper is read only if the PCIe1 PHY is not isolated and the CMU_HSI2 PCIe mux is
	 * selected; a pstore checkpoint after every group so the last line of a hang names the block (PMA last) */
	if (wlan_probe_l2_open())
		for (unsigned g = 0; g < 4; g++) {
			wlan_probe_l2_group(g);
			pstore_save();
		}
	wlan_probe_l2_group(4);			/* SUMMARY, also when gated */
#endif	/* !(WLAN_ISO_RUN || WLAN_LINK_RUN) */
	pstore_save();
#endif
#ifdef GPU_PROBE
	/* Read-only Mali-G78 probe (gpuprobe.c): PMU/CMU state first; the GPU registers are
	 * read only if the PMU says the power domains are on (docs/research/17). */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "GPU");
	gpu_probe();
	pstore_save();
#ifdef GPU_S2MPU
	/* Opt-in READ-ONLY (--gpu-s2mpu, docs/research/28): the four G3D S2MPU register blocks; the granule of the shim image is shown for reference. */
	con_str("SECTION", "GPU s2mpu");
	gpu_s2mpu_dump((uintptr_t)&gpu_s2mpu_dump);
	pstore_save();
#endif
	{
	/* Every opt-in step below that talks to the ACPM can leave a request queued or refuse; if one did, the power cycle (which would start shader
	 * power-up while a request may still run in the APM, or without the rail) is skipped. The rail, if it was switched on, is restored afterwards. */
	int skip_power = 0, rail_state = 0;

	(void)skip_power;
	(void)rail_state;
#ifdef GPU_RAILS
	/* Opt-in READ-ONLY (--gpu-rails, docs/research/27): the S2MPG11 registers of the GPU rails through the ACPM PMIC read path (gpurails.c);
	 * refuses with zero ACPM traffic unless the soc.json block is known and the ACPM gates are set. Runs before anything that writes. */
	con_str("SECTION", "GPU rails");
	gpu_rails();
	pstore_save();
#endif
#ifdef GPU_CLK
	/* Opt-in (--gpu-clk, docs/research/27): ask the ACPM DVFS channel for the G3DL2 / G3D init rate (gpuclk.c), print the CMU words again;
	 * refuses with zero writes unless the ACPM gates are set. Runs before the power cycle. A failure (review M2) skips the power cycle. */
	con_str("SECTION", "GPU clk");
	if (gpu_clk())
		skip_power = 1;
	pstore_save();
#endif
#ifdef GPU_RAIL_ON
	/* Opt-in WRITER (--gpu-rail-on, docs/research/27): switch S2MPG11 BUCK2S (the shader rail, found OFF in run g1rails2) to mode on-normal through the audited
	 * ACPM update (gpurailon.c); zero writes unless it reads exactly "off" with OUT1 in the window. Anything but a verified "on" skips the power cycle. */
	con_str("SECTION", "GPU rail on");
	rail_state = gpu_rail_enable();
	if (rail_state != RAIL_ON_OK)
		skip_power = 1;
	pstore_save();
#endif
#ifdef GPU_POWER
	/* Opt-in (--gpu-power, docs/research/17 stage 1): after the read-only probe, power the tiler/L2 and shaders on, report,
	 * and power them off again (gpupower.c refuses with zero writes unless the probe's state is exactly as expected). */
	if (skip_power)
		con_str("GPU", "POWER skipped: an earlier ACPM step failed or was refused");
	else
		gpu_power_cycle();
	pstore_save();
#endif
#ifdef GPU_RAIL_ON
	/* ALWAYS, also after a timed-out power cycle: log the shader state (review L3), then put BUCK2S_CTRL back to off and say so loudly if that failed. */
	gpu_probe_shaders();
	gpu_rail_restore(rail_state,
#ifdef GPU_POWER
			 skip_power ? RAIL_POWER_SKIPPED : RAIL_POWER_RAN
#else
			 RAIL_POWER_NONE
#endif
			);
	pstore_save();
#endif
	}
#endif
#ifdef TOUCH_PROBE
	/* Touchscreen SPI11/USI11 bring-up probe (touchprobe.c): read-only dumps first and a checkpoint, then (only
	 * with --touch-probe=run) the writes of the XNU driver's sweettouchclk=3 sweettouchvar=1 sequence. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "TOUCH dump");
	touch_probe_dump();
	pstore_save();
#ifdef TOUCH_RAILS
	/* ACPM client (acpm.c, docs/research/23): describe the channel (reads only) and checkpoint BEFORE the first SRAM write, then read
	 * the two rail enable registers, then (--touch-probe=rails only) set bit 7 of both. Each step stops the next one on a refusal. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "TOUCH rails (ACPM)");
	{
		int rails_e = touch_probe_rails(0);

		pstore_save();
		if (!rails_e) {
			rails_e = touch_probe_rails(1);
			pstore_save();
		}
#ifdef TOUCH_RAILS_WRITE
		if (!rails_e) {
			(void)touch_probe_rails(2);
			pstore_save();
		}
#endif
	}
#endif
#if defined(TOUCH_DVFS) && !defined(BOOT_XNU)	/* with --boot-xnu the same steps ran above, before the jump */
	/* ACPM DVFS client (acpm.c, docs/research/25): describe channel 1 (reads only), FREQ_GET of the CPU clusters, then (dvfs only) FREQ_REQ. */
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "TOUCH dvfs (ACPM)");
	{
		int dvfs_e = touch_probe_dvfs(0);

		pstore_save();
		if (!dvfs_e) {
			dvfs_e = touch_probe_dvfs(1);
			pstore_save();
		}
#ifdef TOUCH_DVFS_WRITE
		if (!dvfs_e) {
			(void)touch_probe_dvfs(2);
			pstore_save();
		}
#endif
	}
#endif
#ifdef TOUCH_PROBE_RUN
	delay_ms(3000);
	con_clear(0xff000000U);
	con_str("SECTION", "TOUCH run");
	touch_probe_run(TOUCH_OPTS);
	pstore_save();
#endif
#endif
#ifdef UDUMP
	udump_dump();			/* framed userdata dump (read-only) */
	pstore_save();			/* checkpoint: dump result */
#endif
#ifdef USB_GADGET
	usb_gadget_finish();
	pstore_save();			/* checkpoint: gadget window over */
#endif
#ifdef PSTORE_FAST
	/* --pstore-fast: final checkpoint, then reset ~5 s later so the
	 * log can be pulled from Android right away; no pager loop. */
	con_str("PROBE", "complete; fast reset for pstore");
	pstore_save();
	wait_ticks(counter_freq() * 5);
	bluejay_reset_chain();
#else
	con_str("PROBE", "complete; replaying log");
	pstore_save();		/* final checkpoint: whole report */
	/* Brief live tail, then ~10 min of photographable pages, then reset. */
	delay_ms(8000);
	pager_replay();
	bluejay_reset_chain();
#endif
}
#endif

static uint64_t now(void)
{
	isb();
	return rsr("cntpct_el0");
}

static void delay_ms(unsigned ms)
{
	wait_ticks((counter_freq() / 1000) * ms);
}

#if defined(TOUCH_PROBE) || defined(GPU_POWER) || defined(GPU_CLK) || defined(GPU_RAILS) || defined(GPU_RAIL_ON) || defined(GPU_JOB) || defined(WLAN_RAILS) || defined(WLAN_LINK_RUN)
uint64_t touch_ticks(void)	/* touchprobe.c / gpupower.c: bounded waits (CNTPCT) */
{
	return now();
}
#endif

static void __attribute__((noreturn)) reset(unsigned after_s)
{
	(void)after_s;	/* unused under --pstore-fast */
#if SOC_GS101
#ifdef PSTORE_FAST
	con_str("RESET", "fast: system reset in ~5s");
	wait_ticks(counter_freq() * 5);
	bluejay_reset_chain();
#else
	con_str("RESET", "system reset; log replay");
	/* Keep the live panic screen up, then replay the log for photos. */
	wait_ticks(counter_freq() * after_s);
	pager_replay();
	bluejay_reset_chain();
#endif
#else
	con_skip();
	con_str("RESET", after_s > 20 ? "in 40s" : "in 20s");
	wait_ticks(counter_freq() * after_s);
	wr32(IMEM_RESTART_REASON, RESTART_BOOTLOADER);
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_SYSTEM_RESET, 0, 0, 0);
	wr32(PSHOLD, 0);
	for (;;)
		__asm__ volatile("wfe");
#endif
}

/* ---- exceptions ---- */

static void irq_handler(void)
{
	uint64_t iar = rsr("icc_iar1_el1");
	uint32_t id = iar & 0xffffff;

	if (id >= 1020 && id <= 1023)
		return;	/* spurious */
	irq_last = id;
	if (id == (uint32_t)irq_src)
		irq_count++;
	else
		irq_other++;
	if (id == VTIMER_INTID)
		wsr("cntv_tval_el0", rsr("cntfrq_el0") / 100);	/* 10 ms */
	else if (id == PTIMER_INTID)
		wsr("cntp_tval_el0", rsr("cntfrq_el0") / 100);
	wsr("icc_eoir1_el1", iar);
	isb();
}

void exc_handler(uint64_t slot, uint64_t *regs)
{
	if (slot == 5) {	/* IRQ from current EL */
		irq_handler();
		return;
	}
#if SOC_GS101
	if (probe_guard) {
		if (slot == 4) {
			uint64_t esr = rsr("esr_el1");
			if ((esr >> 26) == 0x25) {	/* Data Abort from current EL */
				probe_fault = 1;
				probe_fault_address = rsr("far_el1");
				probe_guard = 0;
				wsr("elr_el1", rsr("elr_el1") + 4);
				return;
			}
		} else if (slot == 7) {			/* SError from current EL */
			probe_fault = 1;
			probe_fault_address = 0;
			probe_guard = 0;
			wsr("spsr_el1", rsr("spsr_el1") | (1 << 8)); /* Keep PSTATE.A masked */
			return;
		}
	}
#endif
	con_clear(BLUE);
	switch (slot & 3) {
	case 0: con_str("PANIC", "SYNC"); break;
	case 1: con_str("PANIC", "IRQ"); break;
	case 2: con_str("PANIC", "FIQ"); break;
	default: con_str("PANIC", "SERROR"); break;
	}
	con_hex2("SLOT", "n=", slot, "cpu=", rsr("mpidr_el1"));
	con_str("STAGE", stage);
	con_hex("ESR", rsr("esr_el1"));
	con_hex("ELR", rsr("elr_el1"));
	con_hex("FAR", rsr("far_el1"));
	con_hex("SPSR", rsr("spsr_el1"));
	con_hex("X0", regs[0]);
	con_hex("X1", regs[1]);
	con_hex("X30", regs[31]);	/* vent stashed x29/x30 below the frame: regs[30]=x29, regs[31]=x30 */
	/* Save the log (pstore) BEFORE the panic-screen wait and pager so
	 * the pstore copy survives even if the rest never runs. The panic
	 * entry guards against recursion: a fault inside the save must not
	 * re-enter it on this CPU. */
	pstore_panic_save();
	reset(40);
}

/* ---- SMP ---- */

void secondary_main(uint64_t idx)
{
	sec_mpidr[idx] = rsr("mpidr_el1") | (1ULL << 63);
	if (idx < NR_CPUS) {
		__asm__ volatile("mrs %0, midr_el1" : "=r"(cpu_report[idx][1]));
		__asm__ volatile("mrs %0, id_aa64mmfr0_el1" : "=r"(cpu_report[idx][2]));
		__asm__ volatile("mrs %0, id_aa64isar1_el1" : "=r"(cpu_report[idx][3]));
		cpu_report[idx][0] = rsr("mpidr_el1");
		cpu_report[idx][4] = rsr("cntfrq_el0");
		uintptr_t redist = find_gicr();
		if (redist) cpu_report[idx][5] = rd64(redist + GICR_TYPER);
		cpu_report[idx][6] = 1;
	}
	if (idx == 1) {
#if !SOC_GS101
		uintptr_t r = find_gicr();
		if (r) {
			sec_probe[0] = rd32(r + GICR_IGROUPR0);
			wr32(r + GICR_IGROUPR0, 0xffffffff);
			sec_probe[1] = rd32(r + GICR_IGROUPR0);
			wr8(r + GICR_IPRIORITYR + 19, 0xa0);
			sec_probe[2] = rd32(r + GICR_IPRIORITYR + 16);
		}
		sec_probe[3] = r ? 1 : 0xdead;
#endif
	}
	__asm__ volatile("dsb sy" ::: "memory");
	smc(PSCI_CPU_OFF, 0, 0, 0);
}

static void __attribute__((unused)) test_smp(void)
{
	stage = "smp";
	uint64_t self = rsr("mpidr_el1") & 0xffffff;
	int64_t ret[NR_CPUS];

	for (int i = 1; i < NR_CPUS; i++)
		ret[i] = smc(PSCI_CPU_ON, (uint64_t)i << 8, (uintptr_t)secondary_entry, i);
	delay_ms(500);
	for (int i = 1; i < NR_CPUS; i++) {
		char label[] = "CPU0";
		label[3] = '0' + i;
		if (((uint64_t)i << 8) == self)
			con_str(label, "is boot cpu");
		else
			con_hex2(label, "ret=", (uint32_t)ret[i], "mpidr=",
				 (uint32_t)sec_mpidr[i] | (sec_mpidr[i] ? 0 : 0xdead0000));
	}
}

/* ---- GIC + timer ---- */

static uintptr_t find_gicr(void)
{
	uint64_t aff = rsr("mpidr_el1");
	uint32_t want = (aff & 0xffffff) | ((aff >> 8) & 0xff000000);

	for (uintptr_t off = 0; off < GICR_SIZE; off += GICR_PE_SIZE) {
		uint64_t typer = rd64(GICR_BASE + off + GICR_TYPER);
		if ((uint32_t)(typer >> 32) == want)
			return GICR_BASE + off;
		if (typer & (1 << 4))	/* Last */
			break;
	}
	return 0;
}

static void gicd_wait_rwp(void)
{
	for (int i = 0; i < 1000000 && (rd32(GICD_BASE) & (1U << 31)); i++)
		;
}

/* Run one interrupt source for ~300 ms and report what the GIC saw. */
static void irq_window(const char *label, uintptr_t r, int src)
{
	uint64_t f = rsr("cntfrq_el0");
	uint32_t pend = 0, act = 0, isr = 0, hppi = 0x3ff, ist = 0;

	irq_count = irq_other = 0;
	irq_last = 0x3ff;
	irq_src = src;
	if (src == VTIMER_INTID) {
		wsr("cntv_tval_el0", f / 100);
		wsr("cntv_ctl_el0", 1);
	} else if (src == PTIMER_INTID) {
		wsr("cntp_tval_el0", f / 100);
		wsr("cntp_ctl_el0", 1);
	}
	isb();
	/* sample state with IRQs still masked, after the first period */
	uint64_t t = now() + f / 50;
	while (now() < t)
		;
	if (src == SGI_INTID)
		wsr("icc_sgi1r_el1", sgi_self(SGI_INTID));
	isb();
	ist = src == PTIMER_INTID ? rsr("cntp_ctl_el0") : rsr("cntv_ctl_el0");
	pend = rd32(r + GICR_ISPENDR0);
	act = rd32(r + GICR_ISACTIVER0);
	isr = rsr("isr_el1");
	hppi = rsr("icc_hppir1_el1");

	__asm__ volatile("msr daifclr, #2" ::: "memory");
	t = now() + f * 3 / 10;
	/* SGI: one already sent above, send 9 more 10 ms apart -> expect 10 */
	uint64_t next = now() + f / 100;
	for (int sent = 1; now() < t;)
		if (src == SGI_INTID && sent < 10 && now() >= next) {
			wsr("icc_sgi1r_el1", sgi_self(SGI_INTID));
			isb();
			sent++;
			next += f / 100;
		}
	__asm__ volatile("msr daifset, #2" ::: "memory");
	wsr("cntv_ctl_el0", 0);
	wsr("cntp_ctl_el0", 0);
	isb();

	con_hex2(label, "irqs=", irq_count, "other=", irq_other);
	con_hex2(" pre", "pend=", pend, "act=", act);
	con_hex2(" pre", "isr=", isr, "hppi=", hppi);
	con_hex2(" pre", "tctl=", ist, "last=", irq_last);
}

static void test_gic(void)
{
	stage = "gic sre";
	uint64_t sre = rsr("icc_sre_el1");
	if (!(sre & 1)) {
		wsr("icc_sre_el1", sre | 1);
		isb();
	}
	if (!(rsr("icc_sre_el1") & 1)) {
		con_str("GIC", "no sysreg iface, skip");
		return;
	}

	stage = "gic dist";
	uint32_t dctlr = rd32(GICD_BASE);
	/* Non-secure view (DS=0): bit0 EnableGrp1, bit1 EnableGrp1A, bit4 ARE_NS; Linux writes 0x13 */
	wr32(GICD_BASE, dctlr | 0x13);
	gicd_wait_rwp();
	con_hex2("GICD", "was=", dctlr, "now=", rd32(GICD_BASE));

	stage = "gic redist";
	uintptr_t r = find_gicr();
	if (!r) {
		con_str("GICR", "not found");
		return;
	}
	uint32_t waker = rd32(r + GICR_WAKER);
	if (waker & 2) {
		wr32(r + GICR_WAKER, waker & ~2U);
		for (int i = 0; i < 100000 && (rd32(r + GICR_WAKER) & 4); i++)
			;
	}
	con_hex2("GICR", "base=", r, "waker=", rd32(r + GICR_WAKER));

	stage = "gic config";
	uint32_t grp = rd32(r + GICR_IGROUPR0);
	wr32(r + GICR_IGROUPR0, 0xffffffff);	/* NS view: secure bits are WI */
	uint32_t en_before = rd32(r + GICR_ISENABLER0);
	uint32_t pri_before = rd32(r + GICR_IPRIORITYR + 16);	/* INTIDs 16..19 */
	for (int id = 0; id < 32; id++)
		wr8(r + GICR_IPRIORITYR + id, 0xa0);
	wr32(r + GICR_ISENABLER0, (1U << VTIMER_INTID) | (1U << PTIMER_INTID) | (1U << SGI_INTID));
	con_hex2("ENABLE", "was=", en_before, "now=", rd32(r + GICR_ISENABLER0));
	(void)grp;
	(void)pri_before;

	uint64_t ctlr = rsr("icc_ctlr_el1");
	wsr("icc_ctlr_el1", ctlr & ~2ULL);	/* EOImode 0: EOIR also deactivates */
	wsr("icc_pmr_el1", 0xf0);
	wsr("icc_bpr1_el1", 0);
	wsr("icc_igrpen1_el1", 1);
	isb();
	con_hex2("ICC", "ctlr=", ctlr, "grpen1=", rsr("icc_igrpen1_el1"));

	stage = "gic vtimer";
	irq_window("VTIMER", r, VTIMER_INTID);
	stage = "gic sgi";
	irq_window("SGI1", r, SGI_INTID);
	wr32(r + GICR_ICENABLER0, 0xffffffff);
	isb();
}

/* ---- main ---- */

static void __attribute__((unused)) psci_line(const char *label, uint64_t fn)
{
	con_hex2(label, "feat=", smc(PSCI_FEATURES, fn, 0, 0), 0, 0);
}

#ifdef GPU_JOB
/* --gpu-job (docs/research/28): 5 pages of the shim image's own RAM for the GPU page tables, the job and the target word (6 pages reserved, the base is aligned up at
 * run time because the image load address is not known at link time). Used for nothing else. */
static uint8_t gpujob_pages[6 * 4096] __attribute__((aligned(4096)));

uintptr_t gpu_job_scratch(void)
{
	return ((uintptr_t)gpujob_pages + 4095u) & ~(uintptr_t)4095u;
}

/* clean + invalidate to the point of coherency, line by line (64-byte lines on the Cortex-A55/A76 cores); the shim runs MMU-off so VA == PA */
void gpu_job_cache_flush(uintptr_t pa, unsigned len)
{
	uintptr_t a = pa & ~(uintptr_t)63, end = pa + len;

	for (; a < end; a += 64)
		__asm__ volatile("dc civac, %0" :: "r"(a) : "memory");
	__asm__ volatile("dsb sy\n\tisb" ::: "memory");
}
#endif

static uint64_t scratch[8192];	/* 64KB, lives in our (later cacheable) image */

static uint64_t ticks_fill(uint32_t c)
{
	uint64_t t = now();
	con_clear(c);
	return now() - t;
}

static uint64_t ticks_mem(void)
{
	uint64_t t = now();
	for (int pass = 0; pass < 16; pass++)
		for (int i = 0; i < 8192; i++)
			scratch[i] = scratch[i] * 3 + i;
	return now() - t;
}

static uint32_t to_us(uint64_t ticks)
{
	return ticks * 10 / 192;	/* 19.2 MHz */
}

void shim_main(uint64_t dtb)
{
#if SOC_GS101
	bluejay_probe(dtb);
#endif
	struct mmu_info mi = { 0 };	/* mmu_enable can bail before n_l3/sctlr_* are set */

	/* An xnu payload in the ramdisk means: boot it (MMU stays off). */
	stage = "loader";
	con_clear(RED);
	con_str("SHIM", "sweet milestone 4: xnu loader");
	con_skip();
	const char *why = loader_boot(dtb);
	con_str("LOADER", why);
	con_skip();

	/* No payload: probe the internal UFS while the MMU is still off. */
	stage = "ufs";
	test_ufs();
	con_skip();
	/* Keep the probe results on screen (later tests clear it); reset after 3 min. */
	con_str("UFS", "probe done, reset in ~3 min");
	delay_ms(180000);
	reset(20);

	stage = "fill off";
	uint64_t fill_off = ticks_fill(RED);
	uint64_t mem_off = ticks_mem();

	stage = "mmu enable";
	int rc = mmu_enable(&mi, dtb);

	stage = "fill on";
	uint64_t fill_on = rc ? 0 : ticks_fill(RED);
	uint64_t mem_on = rc ? 0 : ticks_mem();
	if (rc)
		con_clear(RED);

	con_str("SHIM", "sweet milestone 3");
	con_skip();
	con_hex2("DTB", "at=", dtb, "magic=", dtb ? __builtin_bswap32(rd32(dtb)) : 0);
	con_hex2("IMAGE", "lo=", mi.img_lo, "hi=", mi.img_hi);
	con_hex2("MMU", "rc=", rc, "l3=", mi.n_l3);
	con_hex("IMAGE in live FDT RAM", mi.img_fdt_confirmed);
	con_hex2("TTBR0", "pa=", mi.ttbr, "pa_hi=", mi.ttbr >> 32);
	con_hex("SCTLR0", mi.sctlr_before);
	con_hex("SCTLR1", mi.sctlr_after);
	con_hex("TCR", rsr("tcr_el1"));
	con_hex2("PARANGE", "mmfr0=", rsr("id_aa64mmfr0_el1") & 0xf, "tg16=", (rsr("id_aa64mmfr0_el1") >> 20) & 0xf);
	con_skip();
	con_hex("PAR img", mmu_probe((uintptr_t)shim_main));
	con_hex("PAR fb", mmu_probe(FB_BASE + 0x1000));
	con_hex("PAR gic", mmu_probe(GICD_BASE));
	con_hex("PAR 12G", mmu_probe(0x300000000UL));
	con_skip();
	con_hex2("FILL us", "off=", to_us(fill_off), "on=", to_us(fill_on));
	con_hex2("MEM us", "off=", to_us(mem_off), "on=", to_us(mem_on));
	con_skip();

	stage = "psci";
	con_hex2("PSCI", "ver=", smc(PSCI_VERSION, 0, 0, 0), "smccc=", smc(SMCCC_VERSION, 0, 0, 0));
	con_skip();
	test_gic();

	stage = "done";
	reset(40);
}
