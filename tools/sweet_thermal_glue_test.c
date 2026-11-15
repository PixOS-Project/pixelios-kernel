/* Host test for bsd/dev/sweet_thermal.c (xnu patch 0336): the kernel glue around the policy of sweet_thermal.h. The source is #included with stubs for the kernel API
 * (compiled by tools/test_sweet_thermal.py with -DTH_C="<tree>/bsd/dev/sweet_thermal.c" -I <empty kernel header dir>). Covers: the boot-arg (default on, sweetthermal=0 off with its GAP),
 * the boot lines (table + the three GAPs of note 150), the thread (first sample at 20 s, 10 s period, charger phase 1 driven from the thread only after 60 s), the actuators
 * (recommended cores 0x18 / the boot mask, charge lever, CRIT reboot), the published value (sweet_thermal_get). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <setjmp.h>
#include <stdarg.h>

#define __unused __attribute__((unused))
typedef int kern_return_t;
typedef int wait_result_t;
typedef void *thread_t;
typedef void (*thread_continue_t)(void *, wait_result_t);
#define KERN_SUCCESS 0

static char klog[1 << 16];
static int
kprintf(const char *fmt, ...)
{
	char tmp[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	if (strlen(klog) + strlen(tmp) < sizeof(klog) - 1) {
		strcat(klog, tmp);
	}
	return 0;
}
#define printf kprintf

static uint64_t fake_ns;
static void absolutetime_to_nanoseconds(uint64_t a, uint64_t *ns) { *ns = a; }
static uint64_t mach_absolute_time(void) { return fake_ns; }
static int arg_thermal = -1;            /* -1 = absent */
static int64_t arg_ramp = -1;           /* sweetthermalramp, -1 = absent */
static uint32_t arg_recores;
static int have_recores;
static int PE_parse_boot_argn(const char *n, void *p, unsigned sz)
{
	(void)sz;
	if (strcmp(n, "sweetthermal") == 0 && arg_thermal >= 0) { *(uint32_t *)p = (uint32_t)arg_thermal; return 1; }
	if (strcmp(n, "sweetthermalramp") == 0 && arg_ramp >= 0) { *(uint32_t *)p = (uint32_t)arg_ramp; return 1; }
	if (strcmp(n, "sweetrecores") == 0 && have_recores) { *(uint32_t *)p = arg_recores; return 1; }
	return 0;
}
static int thr_started;
static thread_continue_t thr_fn;
static kern_return_t kernel_thread_start(thread_continue_t f, void *a, thread_t *t) { (void)a; thr_started++; thr_fn = f; *t = (thread_t)1; return KERN_SUCCESS; }
static void thread_deallocate(thread_t t) { (void)t; }
static unsigned int ncpu = 5;
/* the topology of <machine/machine_routines.h> (osfmk/arm/machine_routines.h), only the fields the glue reads: logical cpu order = array order, phys_id = the ADT cpu node 'reg' (MPIDR) */
typedef struct { uint32_t phys_id; } ml_topology_cpu_t;
typedef struct { unsigned int num_cpus; ml_topology_cpu_t *cpus; } ml_topology_info_t;
static ml_topology_cpu_t topo_cpus[32];
static ml_topology_info_t topo_info;
static int topo_null;
static void
topo_set(const uint32_t *phys, unsigned n)
{
	unsigned i;

	for (i = 0; i < n; i++) { topo_cpus[i].phys_id = phys[i] << 8; }
	topo_info.num_cpus = n;
	topo_info.cpus = topo_cpus;
	topo_null = 0;
}
static const ml_topology_info_t *ml_get_topology_info(void) { return topo_null ? NULL : &topo_info; }
static unsigned int ml_get_cpu_count(void) { return ncpu; }
static uint32_t sched_calls[16]; static int nsched;
static void sched_perfcontrol_update_recommended_cores(uint32_t m) { if (nsched < 16) { sched_calls[nsched] = m; } nsched++; }

/* scripted fuel-gauge samples */
struct samp { int rc; int32_t dC, cur; };
static struct samp script[256]; static int nscript, iscript;
static int chg_tick_lp, chg_tick_lp0, chg_tick_lp1;
static int chg_tick_calls, chg_hold_calls, chg_release_calls, chg_ret, crit_calls, crit_ret;
static int
sweet_chg_fg_sample(int32_t *d, int32_t *c)
{
	struct samp s = iscript < nscript ? script[iscript++] : (struct samp){ -1, 0, 0 };

	*d = s.dC; *c = s.cur;
	return s.rc;
}
static int sweet_chg_icl_thermal(int hold) { if (hold) { chg_hold_calls++; } else { chg_release_calls++; } return chg_ret; }
static void sweet_chg_tick(int lp) { chg_tick_calls++; chg_tick_lp = lp; if (lp) { chg_tick_lp1++; } else { chg_tick_lp0++; } }
static int sweet_thermal_reboot_bootloader(void) { crit_calls++; return crit_ret; }

static jmp_buf out;
static int delays, stop_after;
static void
delay_for_interval(uint32_t n, uint32_t scale)
{
	(void)scale;
	fake_ns += (uint64_t)n * 1000000u;      /* n ms */
	if (++delays >= stop_after) {
		longjmp(out, 1);
	}
}

/* the source declares these extern; our static definitions above would clash, so the prototypes are neutralised */
#define extern static
#include TH_C
#undef extern

static int fails;
#define TCASE(name, cond, why) do { if (cond) { printf_real("CASE %s ok\n", name); } else { printf_real("CASE %s FAIL %s\n", name, why); fails++; } } while (0)
#undef printf
#define printf_real printf
#define KHAS(s) (strstr(klog, (s)) != NULL)

static int
kcount(const char *needle)
{
	int n = 0;
	const char *p = klog;

	while ((p = strstr(p, needle)) != NULL) { n++; p += strlen(needle); }
	return n;
}

static void
greset(void)
{
	klog[0] = 0;
	fake_ns = 0;
	arg_thermal = -1; arg_ramp = -1; have_recores = 0; ncpu = 5;
	thr_started = 0; thr_fn = NULL; nsched = 0; nscript = iscript = 0;
	chg_tick_lp = chg_tick_lp0 = chg_tick_lp1 = 0;
	{ static const uint32_t xrun5[5] = { 0, 6, 7, 4, 5 }; topo_set(xrun5, 5); }
	chg_tick_calls = chg_hold_calls = chg_release_calls = chg_ret = crit_calls = crit_ret = 0;
	delays = 0; stop_after = 1 << 30;
	th_on = -1; th_g_ms = 0; th_g_cc = 0; th_g_state = 0; th_boot_mask = 0;
	memset(&th_c, 0, sizeof(th_c));
}

static void
add(int rc, int32_t dC, int32_t cur)
{
	script[nscript].rc = rc; script[nscript].dC = dC; script[nscript].cur = cur; nscript++;
}

static void
run_thread(int max_delays)
{
	stop_after = max_delays;
	delays = 0;
	if (setjmp(out) == 0) {
		thr_fn(NULL, 0);
	}
}

int
main(void)
{
	int32_t cc = 0;
	uint32_t st = 0, age = 0;

	/* review 0336 F7: the X1-drop mask 0x18 is the A76 pair only under tools/xrun's /cpus order with cpus=5 */
	greset();
	sweet_thermal_start();
	TCASE("order_xrun_cpus5_one_ok_line_no_gap", KHAS("SWEET THERMAL: X1 drop mask 0x18 = logical cpu3,4 = physical A76 pair 4,5 (cpus=5, tools/xrun /cpus order 0,6,7,4,5: the order patch 0270 sweetrecores=0x1e assumes)") &&
	    !KHAS("order differs") && !KHAS("topology unavailable") && !KHAS("is made for cpus=5"), klog);
	greset();
	{ static const uint32_t dt5[5] = { 0, 1, 2, 3, 4 }; topo_set(dt5, 5); }
	sweet_thermal_start();
	TCASE("order_dt_cpus5_gap_lists_physical_cores", KHAS("SWEET THERMAL GAP: /cpus order differs from tools/xrun's 0,6,7,4,5: X1 drop mask 0x18 keeps logical cpus whose physical cores are NOT the A76 pair 4,5 (physical cores by logical cpu: 0 1 2 3 4)") &&
	    !KHAS("= logical cpu3,4 = physical A76 pair") && thr_started == 1, klog);
	greset(); ncpu = 8;
	{ static const uint32_t dt8[8] = { 0, 1, 2, 3, 4, 5, 6, 7 }; topo_set(dt8, 8); }
	sweet_thermal_start();
	TCASE("order_dt_cpus8_both_gaps", KHAS("SWEET THERMAL GAP: cpus=8, the X1 drop mask 0x18 is made for cpus=5") && KHAS("/cpus order differs"), klog);
	greset(); ncpu = 8;
	{ static const uint32_t x8[8] = { 0, 6, 7, 4, 5, 1, 2, 3 }; topo_set(x8, 8); }
	sweet_thermal_start();
	TCASE("order_xrun_cpus8_only_cpus_gap", KHAS("SWEET THERMAL GAP: cpus=8, the X1 drop mask 0x18 is made for cpus=5") && !KHAS("/cpus order differs"), klog);
	greset(); topo_null = 1;
	sweet_thermal_start();
	TCASE("order_topology_unavailable_gap_and_thread_still_starts", KHAS("SWEET THERMAL GAP: cpu topology unavailable: cannot tell which physical cores") && thr_started == 1, klog);
	greset(); ncpu = 3;
	sweet_thermal_start();
	TCASE("order_cpus3_gap", KHAS("SWEET THERMAL GAP: cpus=3, the X1 drop mask 0x18 is made for cpus=5") && thr_started == 1, klog);

	/* default on */
	greset();
	TCASE("enabled_by_default", sweet_thermal_enabled() == 1, "default");
	greset(); arg_thermal = 1;
	TCASE("enabled_with_1", sweet_thermal_enabled() == 1, "=1");
	greset(); arg_thermal = 7;
	TCASE("enabled_with_other_nonzero", sweet_thermal_enabled() == 1, "=7");

	/* disabled path */
	greset(); arg_thermal = 0;
	TCASE("disabled_reports_off", sweet_thermal_enabled() == 0, "=0");
	sweet_thermal_start();
	TCASE("disabled_gap_line_and_no_thread", thr_started == 0 && KHAS("SWEET THERMAL GAP: disabled by sweetthermal=0: no thermal backstop") && !KHAS("SWEET THERMAL: on"), klog);
	TCASE("disabled_get_has_nothing", sweet_thermal_get(&cc, &st, &age) == 0 && nsched == 0, "get");

	/* enabled start */
	greset();
	sweet_thermal_start();
	TCASE("start_thread_and_table_line", thr_started == 1 && KHAS("SWEET THERMAL: on (patch 0336, sweetthermal=0 disables): sample 10 s | X1 drop >= 48.0 C restore <= 44.0 C (mask 0x18 of boot 0x1f) | charge hold >= 45.0 C while charging, release <= 42.0 C | CRIT >= 55.0 C x2 samples -> reboot to bootloader"), klog);
	TCASE("start_prints_the_three_note_gaps", KHAS("sensor->hotspot mapping of thermalmonitord (D22AP plist) is unresolved") && KHAS("iOS CPU/GPU load clamps") && KHAS("no ACPM client in XNU") && KHAS("AppleChargeRateLimitIndex"), klog);
	TCASE("start_nothing_acted", nsched == 0 && chg_hold_calls == 0 && crit_calls == 0, "action at start");
	greset(); have_recores = 1; arg_recores = 0x1e;
	sweet_thermal_start();
	TCASE("start_boot_mask_from_sweetrecores", th_boot_mask == 0x1e && KHAS("(mask 0x18 of boot 0x1e)"), klog);
	greset(); have_recores = 1; arg_recores = 0x100;
	sweet_thermal_start();
	TCASE("start_bad_sweetrecores_falls_back_to_all_online", th_boot_mask == 0x1f, klog);
	greset(); have_recores = 1; arg_recores = 0;
	sweet_thermal_start();
	TCASE("start_zero_sweetrecores_falls_back", th_boot_mask == 0x1f, klog);

	/* thread: first sample after 20 s, then every 10 s; value published */
	greset(); have_recores = 1; arg_recores = 0x1e;
	sweet_thermal_start();
	add(TH_RD_OK, 300, -100000);
	add(TH_RD_OK, 320, -100000);
	run_thread(20 + 10 + 1);
	TCASE("thread_first_sample_at_20s_period_10s", iscript == 2 && sweet_thermal_get(&cc, &st, &age) == 1 && cc == 3200 && st == 0, klog);
	TCASE("thread_published_age_small", age <= 10000u, "age");
	TCASE("thread_logs_state_line", KHAS("SWEET THERMAL: batt 30.0 C state NORMAL (0)"), klog);

	/* X1 drop and restore through the 0270 call, with the boot mask */
	greset(); have_recores = 1; arg_recores = 0x1e;
	sweet_thermal_start();
	add(TH_RD_OK, 470, -100000);
	add(TH_RD_OK, 480, -100000);
	add(TH_RD_OK, 440, -100000);
	run_thread(20 + 30 + 1);
	TCASE("x1_drop_then_restore_masks", nsched == 2 && sched_calls[0] == 0x18 && sched_calls[1] == 0x1e && KHAS("recommended cores now 0x18 (X1 dropped; boot value 0x1e, patch 0270 call)") &&
	    KHAS("recommended cores now 0x1e (restored; boot value 0x1e"), klog);
	TCASE("x1_state_published", sweet_thermal_get(&cc, &st, &age) == 1 && cc == 4400 && st == 0, "state");
	greset();
	sweet_thermal_start();
	add(TH_RD_OK, 500, -100000);
	run_thread(20 + 10 + 1);
	TCASE("x1_drop_without_sweetrecores_uses_online_mask", nsched == 1 && sched_calls[0] == 0x18, klog);
	greset(); ncpu = 2;
	sweet_thermal_start();
	add(TH_RD_OK, 500, -100000);
	run_thread(20 + 10 + 1);
	TCASE("x1_drop_with_empty_result_mask_is_a_gap_not_a_call", nsched == 0 && KHAS("SWEET THERMAL GAP: X1 drop mask 0x18 & boot mask 0x3 is empty"), klog);

	/* charge lever through the thread */
	greset();
	sweet_thermal_start();
	add(TH_RD_OK, 460, 5000);
	add(TH_RD_OK, 420, 5000);
	run_thread(20 + 20 + 1);
	TCASE("chg_hold_and_release_reach_the_lever", chg_hold_calls == 1 && chg_release_calls == 1 && nsched == 0, klog);

	/* CRIT */
	greset();
	sweet_thermal_start();
	add(TH_RD_OK, 560, -100000);
	add(TH_RD_OK, 560, -100000);
	run_thread(20 + 20 + 1);
	TCASE("crit_calls_the_reboot_once", crit_calls == 1 && KHAS("SWEET THERMAL CRIT"), klog);
	greset(); crit_ret = -1;
	sweet_thermal_start();
	add(TH_RD_OK, 560, -100000);
	add(TH_RD_OK, 560, -100000);
	add(TH_RD_OK, 560, -100000);
	run_thread(20 + 30 + 1);
	TCASE("crit_refused_is_retried", crit_calls == 2 && kcount("SWEET THERMAL CRIT") == 2, klog);

	/* sensor failures through the thread: GAP, no action, nothing published */
	greset();
	sweet_thermal_start();
	add(TH_RD_FAIL, 0, 0);
	add(TH_RD_DEAD, 0, 0);
	run_thread(20 + 20 + 1);
	TCASE("thread_fail_gap_no_action_nothing_published", nsched == 0 && chg_hold_calls == 0 && crit_calls == 0 && sweet_thermal_get(&cc, &st, &age) == 0 && KHAS("SWEET THERMAL GAP: fuel-gauge temperature read failed"), klog);
	greset();
	sweet_thermal_start();
	add(TH_RD_OK, 480, -100000);
	add(TH_RD_FAIL, 0, 0);
	run_thread(20 + 20 + 1);
	TCASE("thread_fail_keeps_last_published_value", sweet_thermal_get(&cc, &st, &age) == 1 && cc == 4800 && st == 2 && nsched == 1, klog);
	greset();
	sweet_thermal_start();
	add(TH_RD_OK, 950, -100000);
	run_thread(20 + 10 + 1);
	TCASE("thread_implausible_not_published", sweet_thermal_get(&cc, &st, &age) == 0 && nsched == 0, klog);

	/* charger phase 1 is driven from the thread only after 60 s */
	greset();
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 10; i++) { add(TH_RD_NOTREADY, 0, 0); }
		run_thread(20 + 30 + 1);        /* samples at 20, 30, 40, 50 s */
	}
	TCASE("no_kick_before_60s", chg_tick_calls == 0 && KHAS("no fuel-gauge temperature yet"), klog);
	greset();
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 12; i++) { add(TH_RD_NOTREADY, 0, 0); }
		run_thread(20 + 60 + 1);        /* samples up to 80 s: kicks at 60, 70, 80 */
	}
	TCASE("kick_after_60s", chg_tick_calls == 3, klog);
	/* review 0336 F6: the kick passes log_pending=1, so the stage A -> B and P2 group pacing (force after 10 s + GAP) is kept */
	TCASE("kick_keeps_log_pacing_log_pending_1", chg_tick_lp1 == 3 && chg_tick_lp0 == 0, klog);
	greset();
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 12; i++) { add(TH_RD_NOENGINE, 0, 0); }
		run_thread(20 + 90 + 1);
	}
	TCASE("noengine_never_kicks", chg_tick_calls == 0 && KHAS("no fuel-gauge temperature source on this boot"), klog);
	greset();
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 12; i++) { add(TH_RD_BUSY, 0, 0); }
		run_thread(20 + 90 + 1);
	}
	TCASE("busy_never_kicks_and_is_silent", chg_tick_calls == 0 && !KHAS("SWEET THERMAL GAP: fuel-gauge") && nsched == 0, klog);

	/* TEST-ONLY ramp (boot-arg sweetthermalramp, user 2026-11-13): the state machine sees real + ramp, iOS (sweet_thermal_get) keeps the real value */
	greset(); arg_ramp = 10;
	sweet_thermal_start();
	TCASE("ramp_loud_line_at_start", KHAS("SWEET THERMAL GAP: sweetthermalramp=10 (test only): the backstop sees real + 1.0 C per sample; a CRIT park is expected") && th_c.ramp_dC == 10 && thr_started == 1, klog);
	greset();
	sweet_thermal_start();
	TCASE("ramp_absent_no_line_no_ramp", !KHAS("sweetthermalramp") && th_c.ramp_dC == 0, klog);
	greset(); arg_ramp = 0;
	sweet_thermal_start();
	TCASE("ramp_0_refused", KHAS("SWEET THERMAL GAP: sweetthermalramp=0 (test only) refused: outside 1..100 (deci-C per sample): no ramp, the backstop sees the real temperature") && th_c.ramp_dC == 0 && !KHAS("a CRIT park is expected") && thr_started == 1, klog);
	greset(); arg_ramp = 101;
	sweet_thermal_start();
	TCASE("ramp_101_refused", KHAS("sweetthermalramp=101 (test only) refused: outside 1..100") && th_c.ramp_dC == 0, klog);
	greset(); arg_ramp = 100;
	sweet_thermal_start();
	TCASE("ramp_100_accepted", KHAS("sweetthermalramp=100 (test only): the backstop sees real + 10.0 C per sample") && th_c.ramp_dC == 100, klog);
	greset(); arg_ramp = 1;
	sweet_thermal_start();
	TCASE("ramp_1_accepted", KHAS("sweetthermalramp=1 (test only): the backstop sees real + 0.1 C per sample") && th_c.ramp_dC == 1, klog);
	greset(); arg_thermal = 0; arg_ramp = 10;
	sweet_thermal_start();
	TCASE("ramp_refused_when_sweetthermal_0", thr_started == 0 && th_c.ramp_dC == 0 && KHAS("SWEET THERMAL GAP: sweetthermalramp=10 (test only) refused: sweetthermal=0, no thermal backstop is running to test: no ramp") &&
	    !KHAS("a CRIT park is expected") && KHAS("disabled by sweetthermal=0"), klog);
	greset(); arg_thermal = 0;
	sweet_thermal_start();
	TCASE("sweetthermal_0_without_ramp_no_ramp_line", !KHAS("sweetthermalramp"), klog);

	/* the walk through the thread: real 35.0 C constant, charging. Sample n at 20 + 10 (n-1) s. */
	greset(); arg_ramp = 10; have_recores = 1; arg_recores = 0x1e;
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 16; i++) { add(TH_RD_OK, 350, 5000); }
		run_thread(20 + 150 + 1);       /* samples 1..16 */
	}
	TCASE("ramp_walk_x1_drop_and_charge_hold_reached_the_actuators", nsched == 1 && sched_calls[0] == 0x18 && chg_hold_calls == 1 && chg_release_calls == 0 && crit_calls == 0, klog);
	TCASE("ramp_walk_published_value_is_real", sweet_thermal_get(&cc, &st, &age) == 1 && cc == 3500 && st == 3, klog);
	TCASE("ramp_walk_lines_show_real_and_bias", KHAS("batt 49.0 C (real 35.0 C, test bias +14.0 C) state X1-DROP+CHG-HOLD (3)") && KHAS("recommended cores now 0x18 (X1 dropped; boot value 0x1e"), klog);
	greset(); arg_ramp = 10;
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 30; i++) { add(TH_RD_OK, 350, 5000); }
		run_thread(20 + 220 + 1);       /* samples 1..23: CRIT on the 23rd */
	}
	TCASE("ramp_walk_crit_calls_the_real_reboot_path_once", crit_calls == 1 && kcount("SWEET THERMAL CRIT") == 1 && KHAS("SWEET THERMAL CRIT: batt 56.0 C (real 35.0 C, test bias +21.0 C)"), klog);
	TCASE("ramp_walk_published_value_still_real_at_crit", sweet_thermal_get(&cc, &st, &age) == 1 && cc == 3500 && st == 4, klog);
	greset(); arg_ramp = 10;
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 30; i++) { add(TH_RD_OK, 350, -300000); }
		run_thread(20 + 220 + 1);
	}
	TCASE("ramp_walk_not_charging_no_hold_call_line_says_so", chg_hold_calls == 0 && KHAS("not charging, nothing to hold") && crit_calls == 1 && nsched == 1, klog);
	greset(); arg_ramp = 10;
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 10; i++) { add(TH_RD_FAIL, 0, 0); }
		run_thread(20 + 90 + 1);
	}
	TCASE("ramp_failing_sensor_no_bias_no_action_nothing_published", nsched == 0 && chg_hold_calls == 0 && crit_calls == 0 && th_c.bias_dC == 0 && sweet_thermal_get(&cc, &st, &age) == 0 &&
	    KHAS("sweetthermalramp (test only) refused on this sample: the sensor read is failing"), klog);
	greset();
	sweet_thermal_start();
	{
		int i;

		for (i = 0; i < 30; i++) { add(TH_RD_OK, 350, 5000); }
		run_thread(20 + 220 + 1);
	}
	TCASE("no_ramp_same_samples_no_action_and_no_bias_text", nsched == 0 && chg_hold_calls == 0 && crit_calls == 0 && !KHAS("test bias") && sweet_thermal_get(&cc, &st, &age) == 1 && cc == 3500 && st == 0, klog);

	printf_real("RESULT: %s sweet-thermal-glue fails=%d\n", fails ? "FAIL" : "PASS", fails);
	return fails ? 1 : 0;
}
