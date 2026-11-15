/* Host test for xnu patch 0336 (SWEET THERMAL). One binary, two parts, compiled by tools/test_sweet_thermal.py:
 *   A. the policy of bsd/dev/sweet_thermal.h (state machine, every threshold edge, hysteresis, sensor failure, stale, log cadence), with recording fake actuators;
 *   B. the charger side of bsd/dev/sweet_chg_i2c.c AS PATCHED (CHG_I2C_SRC = the full source): sweet_chg_fg_sample() and sweet_chg_icl_thermal() against the HSI2C12 + chip model
 *      of tools/chg_icl_host_test.c (every chip byte is logged; the model flags any transfer that breaks the protocol).
 * Prints `CASE <name> ok` or `CASE <name> FAIL <why>`; exit 1 when one failed. */
#define SWEET_THERMAL_HOST 1
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

static char tlog[1 << 16];
static void
th_cap(const char *fmt, ...)
{
	char tmp[1024];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	if (strlen(tlog) + strlen(tmp) < sizeof(tlog) - 1) {
		strcat(tlog, tmp);
	}
}
#define TH_LOG(...) th_cap(__VA_ARGS__)
#include TH_HDR   /* -DTH_HDR="<tree>/bsd/dev/sweet_thermal.h" */

/* trunk's sweet_chg_i2c.c (patches 0318/0319, sweetpsclr) calls PE_parse_boot_argn; the 0284-0314 harness has no such stub: none of the 0336 cases uses that arg */
static int PE_parse_boot_argn(const char *n, void *p, unsigned sz) { (void)n; (void)p; (void)sz; return 0; }
#define main icl_main
#include "chg_icl_host_test.c"
#undef main

/* ---- stubs for the 0336 externs of the charger source ---- */
static int th_enabled = 1, enter_ok = 1, gate_est = 1;
int sweet_thermal_enabled(void) { return th_enabled; }
int sweet_chg_enter(void) { return enter_ok; }
int sweet_chg_gate_established(void) { return gate_est; }   /* patch 0336 F5: sweet_chg.c flag (phase 1 opened the gate and finished) */
void sweet_chg_leave(void) { }

#define TCASE(name, cond, why) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL %s\n", name, why); fails++; } } while (0)
#define THAS(s) (strstr(tlog, (s)) != NULL)

/* ---------------- part A: policy ---------------- */
static int x1_drop_n, x1_restore_n, chg_hold_n, chg_release_n, crit_n_calls, chg_ret, crit_ret;
static void fx1(int drop, int32_t dC) { (void)dC; if (drop) { x1_drop_n++; } else { x1_restore_n++; } }
static int fchg(int hold, int32_t dC) { (void)dC; if (hold) { chg_hold_n++; } else { chg_release_n++; } return chg_ret; }
static int fcrit(int32_t dC) { (void)dC; crit_n_calls++; return crit_ret; }
static const struct th_ops fops = { fx1, fchg, fcrit };
static struct th_ctl C;
static uint64_t now_ms;

static void
pinit(void)
{
	th_init(&C);
	tlog[0] = 0;
	x1_drop_n = x1_restore_n = chg_hold_n = chg_release_n = crit_n_calls = 0;
	chg_ret = 0;
	crit_ret = 0;
	now_ms = 1000;
}

static void
ptick(int rd, int32_t dC, int32_t cur)
{
	now_ms += TH_PERIOD_MS;
	th_tick(&C, &fops, rd, dC, cur, now_ms);
}

static int
pcount(const char *needle)
{
	int n = 0;
	const char *p = tlog;

	while ((p = strstr(p, needle)) != NULL) { n++; p += strlen(needle); }
	return n;
}

/* ---------------- part A2: the TEST-ONLY ramp (boot-arg sweetthermalramp) ---------------- */
/* Number of the good sample (1-based) at which a counter first became >= 1, for a constant REAL temperature, charging, ramp r. */
struct ramp_walk { int n_chg, n_x1, n_crit, n_total; int32_t max_bias; int unbiased_ok, order_ok; };

static struct ramp_walk
ramp_walk(uint32_t r, int32_t real_dC, int32_t cur, int max_samples)
{
	struct ramp_walk w = { 0, 0, 0, 0, 0, 1, 1 };
	int i;

	for (i = 1; i <= max_samples && crit_n_calls == 0; i++) {
		ptick(TH_RD_OK, real_dC, cur);
		w.n_total = i;
		if (!w.n_chg && chg_hold_n) { w.n_chg = i; }
		if (!w.n_x1 && x1_drop_n) { w.n_x1 = i; }
		if (!w.n_crit && crit_n_calls) { w.n_crit = i; }
		if (C.last_real_dC != real_dC) { w.unbiased_ok = 0; }
		if (C.bias_dC > w.max_bias) { w.max_bias = C.bias_dC; }
	}
	(void)r;
	return w;
}

static void
part_ramp(void)
{
	struct ramp_walk w;
	int i;

	/* argument range 1..100 */
	pinit();
	TCASE("ramp_set_0_refused", th_ramp_set(&C, 0) == -1 && C.ramp_dC == 0, "0");
	TCASE("ramp_set_101_refused", th_ramp_set(&C, 101) == -1 && C.ramp_dC == 0, "101");
	TCASE("ramp_set_huge_refused", th_ramp_set(&C, 0xffffffffu) == -1 && C.ramp_dC == 0, "huge");
	TCASE("ramp_set_1_ok", th_ramp_set(&C, 1) == 0 && C.ramp_dC == 1, "1");
	TCASE("ramp_set_100_ok", th_ramp_set(&C, 100) == 0 && C.ramp_dC == 100, "100");
	TCASE("ramp_range_constants", TH_RAMP_MIN_DC == 1 && TH_RAMP_MAX_DC == 100 && TH_RAMP_START_N == 3, "consts");
	pinit();
	ptick(TH_RD_OK, 480, 5000);
	TCASE("ramp_off_by_default_no_bias_text", !THAS("test bias") && !THAS("(real ") && !THAS("sweetthermalramp") && C.bias_dC == 0 && C.last_dC == C.last_real_dC, tlog);

	/* sweetthermalramp=10 from 35.0 C real, charging: first two samples unbiased, the 3rd is the first biased one */
	pinit();
	th_ramp_set(&C, 10);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp10_sample1_bias_zero_line", THAS("SWEET THERMAL: batt 35.0 C (real 35.0 C, test bias +0.0 C) state NORMAL (0)") && C.bias_dC == 0 && !THAS("starts at good sample"), tlog);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp10_sample2_bias_zero_line", pcount("test bias +0.0 C") == 2 && C.last_dC == 350, tlog);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp10_sample3_first_biased", THAS("SWEET THERMAL: batt 36.0 C (real 35.0 C, test bias +1.0 C) state NORMAL (0)") && C.bias_dC == 10 && C.last_dC == 360 && C.last_real_dC == 350, tlog);
	TCASE("ramp10_start_line_once", pcount("SWEET THERMAL GAP: sweetthermalramp (test only) starts at good sample 3 (real 35.0 C): from now the backstop sees real + 1.0 C per sample, iOS keeps the real value") == 1, tlog);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp10_sample4_bias_2C", THAS("(real 35.0 C, test bias +2.0 C)") && C.last_dC == 370 && pcount("starts at good sample") == 1, tlog);
	TCASE("ramp10_every_sample_has_a_line", pcount("SWEET THERMAL: batt ") == 4 && pcount("test bias +") == 4, tlog);

	/* the walk: every state in order; charging */
	pinit();
	th_ramp_set(&C, 10);
	w = ramp_walk(10, 350, 5000, 100);
	TCASE("ramp10_walk_charge_hold_at_sample_12", w.n_chg == 12 && THAS("SWEET THERMAL ACTION: batt 45.0 C (real 35.0 C, test bias +10.0 C) >= 45.0 C and charging (5000 uA)"), tlog);
	TCASE("ramp10_walk_x1_drop_at_sample_15", w.n_x1 == 15 && THAS("SWEET THERMAL ACTION: batt 48.0 C (real 35.0 C, test bias +13.0 C) >= 48.0 C: drop the X1 cores (recommended mask 0x18)"), tlog);
	TCASE("ramp10_walk_crit_x2_at_sample_23", w.n_crit == 23 && crit_n_calls == 1 && THAS("SWEET THERMAL CRIT: batt 56.0 C (real 35.0 C, test bias +21.0 C) >= 55.0 C on 2 samples in a row") && C.crit_req == 1 &&
	    th_state(&C) == 4, tlog);
	TCASE("ramp10_walk_crit_not_at_first_55", C.samples == 23 && pcount("SWEET THERMAL CRIT") == 1 && THAS("batt 55.0 C (real 35.0 C, test bias +20.0 C) state X1-DROP+CHG-HOLD (3)"), tlog);
	TCASE("ramp10_walk_order_chg_x1_crit", w.n_chg < w.n_x1 && w.n_x1 < w.n_crit && x1_drop_n == 1 && chg_hold_n == 1 && x1_restore_n == 0 && chg_release_n == 0, tlog);
	TCASE("ramp10_walk_real_value_never_biased", w.unbiased_ok == 1 && C.last_real_dC == 350 && w.max_bias == 210, tlog);
	TCASE("ramp10_walk_states_in_order", THAS("state NORMAL (0)") && THAS("state X1-DROP+CHG-HOLD (3)") && THAS("state CHG-HOLD (1)") && THAS("state CRIT (4)") &&
	    strstr(tlog, "state NORMAL (0)") < strstr(tlog, "state CHG-HOLD (1)") && strstr(tlog, "state CHG-HOLD (1)") < strstr(tlog, "state X1-DROP+CHG-HOLD (3)") &&
	    strstr(tlog, "state X1-DROP+CHG-HOLD (3)") < strstr(tlog, "state CRIT (4)"), tlog);
	TCASE("ramp10_walk_time_about_3_5_min", w.n_crit * TH_PERIOD_MS / 1000 == 230 && w.n_x1 * TH_PERIOD_MS / 1000 == 150, "time");

	/* same walk, NOT charging: no hold, and the line says why */
	pinit();
	th_ramp_set(&C, 10);
	w = ramp_walk(10, 350, -400000, 100);
	TCASE("ramp10_not_charging_no_hold_line_says_so", chg_hold_n == 0 && chg_release_n == 0 && pcount("not charging, nothing to hold") == 2 &&
	    THAS("SWEET THERMAL ACTION: batt 45.0 C (real 35.0 C, test bias +10.0 C) >= 45.0 C but not charging (current -400000 uA): not charging, nothing to hold"), tlog);
	TCASE("ramp10_not_charging_x1_and_crit_still_happen", w.n_x1 == 15 && w.n_crit == 23 && crit_n_calls == 1 && th_state(&C) == 4, tlog);

	/* faster ramp and the cap */
	pinit();
	th_ramp_set(&C, 100);
	w = ramp_walk(100, 350, 5000, 100);
	TCASE("ramp100_walk", w.n_chg == 3 && w.n_x1 == 4 && w.n_crit == 5, tlog);
	pinit();
	th_ramp_set(&C, 1);
	w = ramp_walk(1, 350, 5000, 400);
	TCASE("ramp1_walk_slow", w.n_chg == 102 && w.n_x1 == 132 && w.n_crit == 203, tlog);
	pinit();
	th_ramp_set(&C, 100);
	crit_ret = -1;
	for (i = 0; i < 40; i++) { ptick(TH_RD_OK, 350, 5000); }
	TCASE("ramp_bias_capped_at_100C_crit_retried", C.bias_dC == TH_RAMP_BIAS_CAP_DC && C.last_dC == 1350 && crit_n_calls >= 30 && C.last_real_dC == 350, tlog);

	/* refusals: a failing sensor gets no bias and the ramp does not advance */
	pinit();
	th_ramp_set(&C, 10);
	ptick(TH_RD_FAIL, 0, 0);
	ptick(TH_RD_NOTREADY, 0, 0);
	ptick(TH_RD_DEAD, 0, 0);
	TCASE("ramp_refused_while_sensor_failing_one_line", pcount("SWEET THERMAL GAP: sweetthermalramp (test only) refused on this sample: the sensor read is failing (rc -1)") == 1 && pcount("sweetthermalramp") == 1 &&
	    C.samples == 0 && C.bias_dC == 0 && x1_drop_n == 0 && chg_hold_n == 0 && crit_n_calls == 0, tlog);
	ptick(TH_RD_OK, 350, 5000);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp_not_started_by_failed_reads", C.samples == 2 && C.bias_dC == 0 && !THAS("starts at good sample"), tlog);
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp_starts_at_third_GOOD_sample", C.samples == 3 && C.bias_dC == 10 && pcount("starts at good sample 3") == 1, tlog);
	/* mid-ramp failure: no action, no bias on the failed sample, the ramp resumes where it stopped */
	for (i = 0; i < 6; i++) { ptick(TH_RD_OK, 350, 5000); }
	TCASE("ramp_midwalk_before_failure", C.samples == 9 && C.bias_dC == 70 && x1_drop_n == 0, tlog);
	{
		int hn = chg_hold_n;

		ptick(TH_RD_FAIL, 0, 0);
		ptick(TH_RD_FAIL, 0, 0);
		TCASE("ramp_midwalk_failure_refused_no_action", C.samples == 9 && chg_hold_n == hn && x1_drop_n == 0 && crit_n_calls == 0 && pcount("refused on this sample: the sensor read is failing") == 2, tlog);
	}
	ptick(TH_RD_OK, 350, 5000);
	TCASE("ramp_resumes_after_failure_without_skipping", C.samples == 10 && C.bias_dC == 80 && C.last_dC == 430 && C.last_real_dC == 350, tlog);
	ptick(TH_RD_OK, 950, 5000);
	TCASE("ramp_implausible_real_value_refused", C.samples == 10 && C.bias_dC == 80 && C.last_real_dC == 350 && THAS("sweetthermalramp (test only) refused on this sample: the sensor value is implausible") && crit_n_calls == 0, tlog);
	ptick(TH_RD_BUSY, 0, 0);
	TCASE("ramp_busy_is_not_a_sample", C.samples == 10, tlog);
	/* a 95 C real read is still not acted on because the ramp cannot make an implausible read plausible */
	pinit();
	th_ramp_set(&C, 100);
	for (i = 0; i < 2; i++) { ptick(TH_RD_OK, 350, 5000); }
	ptick(TH_RD_OK, -250, 5000);
	TCASE("ramp_cold_implausible_real_not_biased_into_range", C.samples == 2 && x1_drop_n == 0 && C.bias_dC == 0, tlog);

	/* the STALE line names the real sensor value, never the biased one */
	pinit();
	th_ramp_set(&C, 10);
	for (i = 0; i < 6; i++) { ptick(TH_RD_OK, 350, 5000); }
	for (i = 0; i < 8; i++) { ptick(TH_RD_FAIL, 0, 0); }
	TCASE("ramp_stale_line_shows_real_value", THAS("battery temperature STALE, last good 35.0 C") && !THAS("last good 39.0 C"), tlog);

	/* CRIT is the real path: the crit actuator is called with the biased value only for its argument; refusals are retried like without a ramp */
	pinit();
	th_ramp_set(&C, 50);
	crit_ret = -1;
	for (i = 0; i < 10; i++) { ptick(TH_RD_OK, 350, 5000); }
	TCASE("ramp_crit_refused_asked_again_loud", crit_n_calls >= 2 && pcount("SWEET THERMAL CRIT") == crit_n_calls, tlog);
}

static void
part_a(void)
{
	int i;

	/* thresholds are the user's numbers */
	TCASE("table_values", TH_X1_DROP_DC == 480 && TH_X1_RESTORE_DC == 440 && TH_CHG_HOLD_DC == 450 && TH_CHG_RELEASE_DC == 420 && TH_CRIT_DC == 550 && TH_X1_DROP_MASK == 0x18 && TH_PERIOD_MS == 10000 && TH_LOG_EVERY_MS == 60000, "table");

	/* X1: edge below, edge at, no repeat, hysteresis edges */
	pinit();
	ptick(TH_RD_OK, 479, -1000);
	TCASE("x1_47_9_no_drop", x1_drop_n == 0 && th_state(&C) == 0, tlog);
	ptick(TH_RD_OK, 480, -1000);
	TCASE("x1_48_0_drops", x1_drop_n == 1 && th_state(&C) == 2 && THAS("SWEET THERMAL ACTION: batt 48.0 C >= 48.0 C: drop the X1 cores (recommended mask 0x18)"), tlog);
	ptick(TH_RD_OK, 520, -1000);
	ptick(TH_RD_OK, 481, -1000);
	TCASE("x1_no_repeat", x1_drop_n == 1 && x1_restore_n == 0, "repeat");
	ptick(TH_RD_OK, 441, -1000);
	TCASE("x1_44_1_stays_dropped", x1_restore_n == 0 && th_state(&C) == 2, tlog);
	ptick(TH_RD_OK, 440, -1000);
	TCASE("x1_44_0_restores", x1_restore_n == 1 && th_state(&C) == 0 && THAS("batt 44.0 C <= 44.0 C: restore the recommended cores"), tlog);
	ptick(TH_RD_OK, 470, -1000);
	TCASE("x1_47_0_after_restore_no_drop", x1_drop_n == 1, "dropped below 48");
	ptick(TH_RD_OK, 480, -1000);
	TCASE("x1_drops_again", x1_drop_n == 2, "second drop");

	/* charge: needs >= 45.0 AND current > 0 */
	pinit();
	ptick(TH_RD_OK, 449, 5000);
	TCASE("chg_44_9_charging_no_hold", chg_hold_n == 0, tlog);
	ptick(TH_RD_OK, 450, 0);
	TCASE("chg_45_0_current_zero_no_hold", chg_hold_n == 0, tlog);
	ptick(TH_RD_OK, 470, -900000);
	TCASE("chg_hot_discharging_no_hold", chg_hold_n == 0 && x1_drop_n == 0, tlog);
	ptick(TH_RD_OK, 450, 1);
	TCASE("chg_45_0_charging_holds", chg_hold_n == 1 && th_state(&C) == 1 && THAS("batt 45.0 C >= 45.0 C and charging (1 uA): charge input limit back to the original value (CNFG_09)") &&
	    THAS("charge-hold request at 45.0 C -> done"), tlog);
	ptick(TH_RD_OK, 440, -500000);
	TCASE("chg_stays_held_when_current_goes_negative", chg_release_n == 0 && chg_hold_n == 1 && th_state(&C) == 1, tlog);
	ptick(TH_RD_OK, 421, 5000);
	TCASE("chg_42_1_stays_held", chg_release_n == 0, tlog);
	ptick(TH_RD_OK, 420, 5000);
	TCASE("chg_42_0_releases", chg_release_n == 1 && th_state(&C) == 0 && THAS("batt 42.0 C <= 42.0 C: charge input limit back to 875 mA") && THAS("charge-release request at 42.0 C -> done"), tlog);
	ptick(TH_RD_OK, 449, 5000);
	TCASE("chg_44_9_after_release_no_hold", chg_hold_n == 1, "re-held below 45");
	ptick(TH_RD_OK, 450, 5000);
	TCASE("chg_holds_again", chg_hold_n == 2, "second hold");

	/* lever results */
	pinit();
	chg_ret = 1;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_nothing_to_do_logged_not_retried", chg_hold_n == 1 && THAS("-> nothing to do (CNFG_09 is not raised on this boot)") && (ptick(TH_RD_OK, 460, 5000), chg_hold_n == 1), tlog);
	/* review 0336 F3: a hold that failed (-1) is asked again at the next sample while the battery stays hot, at most TH_CHG_RETRY_MAX times */
	pinit();
	chg_ret = -1;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_failed_logged_and_retried", chg_hold_n == 1 && THAS("-> FAILED (see the GAP above), asked again at the next sample (retry 1 of 4)") && th_state(&C) == 0, tlog);
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_failed_asked_again_next_sample", chg_hold_n == 2, tlog);
	ptick(TH_RD_OK, 460, 5000);
	ptick(TH_RD_OK, 460, 5000);
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_failed_retry_bounded", chg_hold_n == 1 + TH_CHG_RETRY_MAX && THAS("-> FAILED (see the GAP above), 4 retries used: not asked again until <= 42.0 C") && th_state(&C) == 1, tlog);
	ptick(TH_RD_OK, 470, 5000);
	ptick(TH_RD_OK, 470, 5000);
	TCASE("chg_failed_no_more_calls_after_bound", chg_hold_n == 1 + TH_CHG_RETRY_MAX, tlog);
	chg_ret = 1;
	ptick(TH_RD_OK, 410, 5000);
	TCASE("chg_failed_release_asked_once", chg_release_n == 1 && th_state(&C) == 0, tlog);
	chg_ret = -1;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_retry_counter_reset_by_release", chg_hold_n == 2 + TH_CHG_RETRY_MAX && pcount("(retry 1 of 4)") == 2, tlog);
	pinit();
	chg_ret = -1;
	ptick(TH_RD_OK, 460, 5000);
	chg_ret = 0;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_failed_once_then_done", chg_hold_n == 2 && th_state(&C) == 1 && (ptick(TH_RD_OK, 460, 5000), chg_hold_n == 2), tlog);
	pinit();
	chg_ret = -1;
	ptick(TH_RD_OK, 460, 5000);
	chg_ret = 2;
	ptick(TH_RD_OK, 460, 5000);
	ptick(TH_RD_OK, 460, 5000);
	chg_ret = 0;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_busy_does_not_use_the_failure_bound", chg_hold_n == 4 && th_state(&C) == 1 && C.chg_retry == 0, tlog);
	pinit();
	chg_ret = 2;
	ptick(TH_RD_OK, 460, 5000);
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_busy_retried_next_sample", chg_hold_n == 2 && pcount("engine busy, retried at the next sample") == 2, tlog);
	chg_ret = 0;
	ptick(TH_RD_OK, 460, 5000);
	TCASE("chg_busy_then_done", chg_hold_n == 3 && th_state(&C) & 1 && (ptick(TH_RD_OK, 460, 5000), chg_hold_n == 3), tlog);
	pinit();
	ptick(TH_RD_OK, 460, 5000);
	chg_ret = 2;
	ptick(TH_RD_OK, 410, 5000);
	ptick(TH_RD_OK, 410, 5000);
	TCASE("chg_release_busy_retried", chg_release_n == 2 && (th_state(&C) & 1) == 1, tlog);

	/* CRIT */
	pinit();
	ptick(TH_RD_OK, 549, 5000);
	ptick(TH_RD_OK, 550, 5000);
	TCASE("crit_55_0_first_sample_waits", crit_n_calls == 0 && !THAS("SWEET THERMAL CRIT"), tlog);
	ptick(TH_RD_OK, 551, 5000);
	TCASE("crit_second_sample_requests", crit_n_calls == 1 && THAS("SWEET THERMAL CRIT: batt 55.1 C >= 55.0 C on 2 samples in a row") && th_state(&C) == 4, tlog);
	ptick(TH_RD_OK, 600, 5000);
	TCASE("crit_requested_once", crit_n_calls == 1, "repeat");
	pinit();
	ptick(TH_RD_OK, 560, 5000);
	ptick(TH_RD_OK, 549, 5000);
	ptick(TH_RD_OK, 560, 5000);
	TCASE("crit_counter_reset_by_549", crit_n_calls == 0, tlog);
	ptick(TH_RD_OK, 560, 5000);
	TCASE("crit_after_reset_two_more", crit_n_calls == 1, tlog);
	pinit();
	crit_ret = -1;
	ptick(TH_RD_OK, 560, 5000);
	ptick(TH_RD_OK, 560, 5000);
	ptick(TH_RD_OK, 560, 5000);
	TCASE("crit_refused_asked_again_loud", crit_n_calls == 2 && pcount("SWEET THERMAL CRIT") == 2, tlog);
	pinit();
	ptick(TH_RD_OK, 560, 5000);
	ptick(TH_RD_OK, 560, 5000);
	TCASE("crit_also_drops_x1_and_holds", x1_drop_n == 1 && chg_hold_n == 1 && crit_n_calls == 1, tlog);

	/* sensor failure / implausible / not ready / dead: GAP, no action, state kept */
	pinit();
	ptick(TH_RD_OK, 490, 5000);
	{
		int d = x1_drop_n, h = chg_hold_n;

		ptick(TH_RD_FAIL, 300, -5000);
		TCASE("fail_gap_no_action", pcount("SWEET THERMAL GAP: fuel-gauge temperature read failed (read rc -1, 1 in a row): no thermal action taken on this sample, state X1-DROP+CHG-HOLD kept") == 1 &&
		    x1_drop_n == d && x1_restore_n == 0 && chg_hold_n == h && chg_release_n == 0 && crit_n_calls == 0, tlog);
		ptick(TH_RD_FAIL, 300, -5000);
		TCASE("fail_gap_rate_limited", pcount("fuel-gauge temperature read failed") == 1, tlog);
		for (i = 0; i < 6; i++) { ptick(TH_RD_FAIL, 300, -5000); }
		TCASE("fail_gap_repeats_after_60s", pcount("fuel-gauge temperature read failed") == 2, tlog);
		TCASE("fail_does_not_release_or_restore", x1_restore_n == 0 && chg_release_n == 0 && th_state(&C) == 3, tlog);
		TCASE("fail_stale_line_once", pcount("battery temperature STALE, last good 49.0 C") == 1, tlog);
		ptick(TH_RD_OK, 430, 5000);
		TCASE("fail_recovery_restores_x1", x1_restore_n == 1 && chg_release_n == 0 && pcount("STALE") == 1, tlog);
		ptick(TH_RD_FAIL, 0, 0);
		TCASE("fail_gap_again_after_recovery", pcount("fuel-gauge temperature read failed") == 3, tlog);
	}
	pinit();
	ptick(TH_RD_OK, 950, 5000);
	TCASE("implausible_hot_95_no_action", x1_drop_n == 0 && chg_hold_n == 0 && crit_n_calls == 0 && THAS("outside the plausible range -20.0..90.0 C") && !THAS("SWEET THERMAL ACTION"), tlog);
	ptick(TH_RD_OK, 901, 5000);
	ptick(TH_RD_OK, -201, 5000);
	TCASE("implausible_edges_901_m201_rejected", x1_drop_n == 0 && crit_n_calls == 0, tlog);
	ptick(TH_RD_OK, 900, 5000);
	TCASE("plausible_edge_90_0_acts", x1_drop_n == 1, tlog);
	pinit();
	ptick(TH_RD_OK, -201, 5000);
	TCASE("implausible_cold_rejected_not_stored", C.have_temp == 0 && THAS("outside the plausible range -20.0..90.0 C") && !THAS("SWEET THERMAL: batt"), tlog);
	pinit();
	ptick(TH_RD_OK, -200, 5000);
	TCASE("plausible_edge_m20_0_ok", C.have_temp == 1 && THAS("batt -20.0 C state NORMAL"), tlog);
	pinit();
	ptick(TH_RD_NOTREADY, 0, 0);
	TCASE("notready_gap_text", THAS("no fuel-gauge temperature yet (charger I2C engine not up)") && x1_drop_n == 0, tlog);
	pinit();
	ptick(TH_RD_NOENGINE, 0, 0);
	TCASE("noengine_gap_text", THAS("no fuel-gauge temperature source on this boot") && x1_drop_n == 0, tlog);
	pinit();
	ptick(TH_RD_DEAD, 0, 0);
	TCASE("dead_gap_text", THAS("stopped by an earlier transfer error"), tlog);
	pinit();
	ptick(TH_RD_BUSY, 0, 0);
	ptick(TH_RD_BUSY, 0, 0);
	TCASE("busy_silent", tlog[0] == 0 && C.fail_n == 0, tlog);
	pinit();
	ptick(TH_RD_FAIL, 0, 0);
	ptick(TH_RD_NOTREADY, 0, 0);
	TCASE("fail_counter_in_a_row", THAS("1 in a row") && C.fail_n == 2, tlog);

	/* review 0336 F7: the order check (physical core by logical cpu): tools/xrun order 0,6,7,4,5 with cpus=5 is the only silent one */
	{
		static const uint32_t ok5[5] = { 0, 6, 7, 4, 5 }, dt5[5] = { 0, 1, 2, 3, 4 }, ok8[8] = { 0, 6, 7, 4, 5, 1, 2, 3 }, a76first[5] = { 0, 4, 5, 6, 7 }, dt8[8] = { 0, 1, 2, 3, 4, 5, 6, 7 };

		TCASE("order_xrun_5cpus_silent", th_order_gaps(5, ok5, 5, 0x1f) == 0, "ok");
		TCASE("order_dt_5cpus_differs", th_order_gaps(5, dt5, 5, 0x1f) == 2, "dt");
		TCASE("order_dt_8cpus_both", th_order_gaps(8, dt8, 8, 0xff) == 3, "dt8");
		TCASE("order_xrun_8cpus_only_cpus_gap", th_order_gaps(8, ok8, 8, 0xff) == 1, "ok8");
		TCASE("order_a76_first_differs", th_order_gaps(5, a76first, 5, 0x1f) == 2, "a76first");
		TCASE("order_unknown_topology", th_order_gaps(5, 0, 0, 0x1f) == 4 && th_order_gaps(8, 0, 0, 0xff) == 5, "unknown");
		TCASE("order_cpus3_mask_empty_only_cpus_gap", th_order_gaps(3, ok5, 3, 0x07) == 1, "cpus3");
		TCASE("order_cpus4_one_a76_kept", th_order_gaps(4, ok5, 4, 0x0f) == 1, "cpus4");
		TCASE("order_short_topology_flags_missing_cpu", th_order_gaps(5, ok5, 4, 0x1f) == 2, "short");
		TCASE("order_a55_in_mask", ({ static const uint32_t m[5] = { 0, 6, 7, 3, 5 }; th_order_gaps(5, m, 5, 0x1f) == 2; }), "a55");
	}

	/* log cadence */
	pinit();
	for (i = 0; i < 13; i++) { ptick(TH_RD_OK, 300, 5000); }
	TCASE("log_once_per_60s_steady", pcount("SWEET THERMAL: batt 30.0 C state NORMAL (0)") == 3, tlog);
	pinit();
	ptick(TH_RD_OK, 300, -5000);
	ptick(TH_RD_OK, 480, -5000);
	ptick(TH_RD_OK, 440, -5000);
	TCASE("log_on_every_state_change", pcount("SWEET THERMAL: batt ") == 3 && THAS("batt 48.0 C state X1-DROP (2)") && THAS("batt 44.0 C state NORMAL (0)"), tlog);
	pinit();
	ptick(TH_RD_OK, -53, 5000);
	TCASE("negative_format", THAS("batt -5.3 C"), tlog);
	pinit();
	ptick(TH_RD_OK, 0, 5000);
	TCASE("zero_format", THAS("batt 0.0 C state NORMAL"), tlog);

	/* hot but not charging: never a silent skip (user 2026-11-13: "not charging, nothing to hold") */
	pinit();
	ptick(TH_RD_OK, 470, -900000);
	TCASE("nochg_hot_discharging_says_nothing_to_hold", chg_hold_n == 0 && pcount("SWEET THERMAL ACTION: batt 47.0 C >= 45.0 C but not charging (current -900000 uA): not charging, nothing to hold") == 1, tlog);
	ptick(TH_RD_OK, 470, -900000);
	ptick(TH_RD_OK, 470, 0);
	TCASE("nochg_line_not_repeated_within_60s", pcount("nothing to hold") == 1, tlog);
	for (i = 0; i < 6; i++) { ptick(TH_RD_OK, 470, -900000); }
	TCASE("nochg_line_repeats_every_60s", pcount("nothing to hold") == 2, tlog);
	ptick(TH_RD_OK, 470, 5000);
	TCASE("nochg_then_charging_holds", chg_hold_n == 1 && th_state(&C) == 1, tlog);
	pinit();
	ptick(TH_RD_OK, 470, -5000);
	ptick(TH_RD_OK, 440, -5000);
	ptick(TH_RD_OK, 460, -5000);
	TCASE("nochg_cooled_then_hot_again_says_it_again", pcount("nothing to hold") == 2, tlog);
	pinit();
	ptick(TH_RD_OK, 440, -5000);
	TCASE("nochg_below_45_silent", pcount("nothing to hold") == 0, tlog);

	part_ramp();
}

/* ---------------- part B: charger side ---------------- */
static void
treset(void)
{
	reset();
	i2c_loaded = 0;
	p3_therm_arm = p3_therm_hold = 0;
	p3_twrites = 0;
	i2c_xfer_seen = 0;
	th_enabled = 1;
	enter_ok = 1;
	gate_est = 1;
	i2c_group = 0;
}

static void
ticks(int n)
{
	int t;

	for (t = 0; t < n; t++) { fk_ms += 1000; sweet_chg_i2c_tick(0); }
}

static void
setfg(int32_t dC10, int cur_raw)
{
	/* raw = dC/10 * 256: 48.0 C = 0x3000 */
	fgw[0x08] = (uint16_t)(int16_t)((dC10 * 256) / 10);
	fgw[0x0a] = (uint16_t)cur_raw;
}

static int integ_gate_closed_n;
static int integ_lever(int hold, int32_t dC)
{
	(void)dC;
	if (hold && integ_gate_closed_n > 0) {
		integ_gate_closed_n--;
		chg_gate_fail_after = 0; gate_calls = 0;
	} else {
		chg_gate_fail_after = 1000; gate_calls = 0;
	}
	return sweet_chg_icl_thermal(hold);
}

static void
part_b(void)
{
	int32_t t = 0, c = 0;
	int rc, i, w;

	/* sample: engine states */
	treset();
	arg_on = 0; icl_arg = 0;
	setfg(480, 0x0010);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_before_engine_is_not_ready", rc == 1 && nw == 0, "rc");
	ticks(3);
	TCASE("engine_loaded_for_thermal_without_p2", i2c_group == 9 && i2c_loaded == 1 && HAS("the HSI2C12 engine is loaded for the thermal sampler (patch 0336)") && i2c_p2_ok == 0 && nw == 0, fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_reads_temp_and_current", rc == 0 && t == 480 && c == 5000, "values");
	TCASE("sample_reads_only", ncw == 0 && viol == 0 && wr_offsets_allowed() && hw[OFF_INTEN / 4] == 0, viol_why);
	TCASE("sample_reads_exactly_two_regs", ({ int a = 0, b = 0, k; for (k = 0; k + 0 < ntx; k++) { a += txlog[k] == 0x0a; b += txlog[k] == 0x08; } a == 1 && b == 1 && ntx == 2; }), "tx");
	setfg(-60, 0xfe00);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_negative_values", rc == 0 && t == -60 && c == -160000, "values");
	setfg(451, 0x0010);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_conversion_truncates_like_mon_row", rc == 0 && t == 450, "conv");

	treset();
	arg_on = 0; icl_arg = 0; th_enabled = 0;
	ticks(3);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_off_p2_off_no_engine", rc == -3 && i2c_loaded == 0 && nw == 0 && HAS("SWEET CHG P2: off (default; sweetchgi2c=1 enables it"), fk_log);

	treset();
	arg_on = 0; icl_arg = 0; load_fail = 1;
	ticks(3);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("load_fail_no_engine_gap", rc == -3 && HAS("SWEET CHG GAP: P2 /sweet-chg hsi2c12-reg missing"), fk_log);

	treset();
	arg_on = 0; icl_arg = 0;
	ticks(3);
	enter_ok = 0;
	nw = 0;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_busy_touches_nothing", rc == 2 && nw == 0, "busy");
	enter_ok = 1;

	treset();
	arg_on = 0; icl_arg = 0;
	ticks(3);
	chg_gate_fail_after = 0;
	gate_calls = 0;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_gate_open_fail_transient", rc == -1 && i2c_dead == 0 && ntx == 0 && HAS("SWEET CHG GAP: P2 phase-1 gate state is not the open pattern"), fk_log);
	chg_gate_fail_after = 1000;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_recovers_after_gate_returns", rc == 0, "rc");

	treset();
	arg_on = 0; icl_arg = 0;
	ticks(3);
	nack_slave = 0x36;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_read_fail_gap_and_bus_stopped", rc == -1 && i2c_dead == 1 && HAS("SWEET CHG GAP: THERMAL fuel-gauge read failed: bus stopped") && ncw == 0, fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_after_dead_is_dead_no_transfer", rc == -2 && ({ int before = ntx; (void)sweet_chg_fg_sample(&t, &c); before == ntx; }), "dead");

	/* P2 on (sweetchgi2c=1): the engine is ready only after the six P2 groups */
	treset();
	icl_arg = 0;
	ticks(2);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("p2_on_not_ready_during_groups", rc == 1, "rc");
	ticks(20);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("p2_on_ready_after_groups", rc == 0 && i2c_loaded == 1 && i2c_p2_ok == 1, fk_log);

	/* ---- the charge lever, against the 0287 flow ---- */
	treset();
	stop_at3 = 1;
	run_p3(600);
	TCASE("lever_setup_raise_in_place", ncw == 1 && cwlog[0].val == 0xa2 && chip[0x69][0xc2] == 0xa2 && p3_stage == 3 && p3_nwrites == 1, fk_log);
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_writes_original", rc == 0 && ncw == 2 && cwlog[1].slave == 0x69 && cwlog[1].reg == 0xc2 && cwlog[1].val == 0x93 && chip[0x69][0xc2] == 0x93 && p3_nwrites == 1 && p3_twrites == 1 &&
	    HAS("SWEET CHG P3: thermal hold: CNFG_09 before=0xa2 (875 mA) wrote=0x93 read back=0x93 (500 mA)"), fk_log);
	TCASE("lever_hold_viol_free_and_inten_off", viol == 0 && wr_offsets_allowed() && only_cnfg09_chip_writes() && hw[OFF_INTEN / 4] == 0 && p3_therm_arm == 0, viol_why);
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_twice_no_second_write", rc == 1 && ncw == w && HAS("thermal hold: CNFG_09 already 0x93"), fk_log);
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_writes_875", rc == 0 && ncw == 3 && cwlog[2].val == 0xa2 && chip[0x69][0xc2] == 0xa2 && p3_nwrites == 1 && p3_twrites == 2 && HAS("thermal release: CNFG_09 before=0x93 (500 mA) wrote=0xa2 read back=0xa2 (875 mA)"), fk_log);
	w = ncw;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_twice_no_second_write", rc == 1 && ncw == w, fk_log);
	/* P3 watcher ended with the raise still standing (gate lost mid-sampling): hold still lowers it, release does not raise it again */
	{
		int save_w;

		treset();
		chg_gate_fail_after = 9;
		run_p3(600);
		TCASE("lever_setup_p3_done_raise_standing", p3_stage == 9 && ncw == 1 && chip[0x69][0xc2] == 0xa2 && p3_nwrites == 1, fk_log);
		chg_gate_fail_after = 1000; gate_calls = 0;
		rc = sweet_chg_icl_thermal(1);
		TCASE("lever_hold_after_p3_done_lowers", rc == 0 && ncw == 2 && cwlog[1].val == 0x93 && chip[0x69][0xc2] == 0x93, fk_log);
		save_w = ncw;
		rc = sweet_chg_icl_thermal(0);
		TCASE("lever_release_after_p3_done_stays_low", rc == 1 && ncw == save_w && chip[0x69][0xc2] == 0x93 && HAS("the P3 watcher has ended (stage 9)"), fk_log);
	}
	treset();
	stop_at3 = 1;
	run_p3(600);
	(void)sweet_chg_icl_thermal(1);
	(void)sweet_chg_icl_thermal(0);
	/* the watcher keeps running afterwards: still quiet while the link is ok */
	run_p3(100);
	TCASE("lever_watcher_unaffected", ncw == 3 && p3_stage == 3 && !HAS("lost"), fk_log);
	/* link lost after a release: the 0287 restore still works exactly once (p3_nwrites stayed 1) */
	usb_off_ms = fk_ms + 5000;
	stop_at3 = 0;
	run_p3(200);
	TCASE("lever_link_lost_after_release_restores_once", ncw == 4 && cwlog[3].val == 0x93 && chip[0x69][0xc2] == 0x93 && p3_stage == 9 && HAS("done (link lost, restored)"), fk_log);
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_after_done_nothing_to_do", rc == 1 && ncw == 4, fk_log);

	/* link lost while held: CNFG_09 is the original already, nothing is written, 0287 says so */
	treset();
	run_p3(600);
	(void)sweet_chg_icl_thermal(1);
	stop_at3 = 0;
	usb_off_ms = fk_ms + 5000;
	run_p3(200);
	TCASE("lever_link_lost_while_held_no_extra_write", ncw == 2 && chip[0x69][0xc2] == 0x93 && p3_stage == 9 && HAS("is 0x93, not the 0xa2 we wrote: not touched"), fk_log);

	/* release only while the raise preconditions still hold */
	treset();
	run_p3(600);
	(void)sweet_chg_icl_thermal(1);
	w = ncw;
	usb_off_ms = fk_ms;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_refused_without_ss_link", rc == 1 && ncw == w && chip[0x69][0xc2] == 0x93 && HAS("thermal release: the SuperSpeed link or CHGTYP SDP/CDP is gone"), fk_log);
	treset();
	run_p3(600);
	(void)sweet_chg_icl_thermal(1);
	w = ncw;
	chip[0x25][0x87] = 0x03;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_refused_with_dcp", rc == 1 && ncw == w && chip[0x69][0xc2] == 0x93, fk_log);

	/* someone else changed CNFG_09: not touched */
	treset();
	run_p3(600);
	chip[0x69][0xc2] = 0x91;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_foreign_value_not_touched", rc == -1 && ncw == 1 && chip[0x69][0xc2] == 0x91 && HAS("thermal hold: CNFG_09 is 0x91, not the 0x93 / 0xa2 we know: not touched"), fk_log);
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_foreign_value_not_touched", rc == -1 && ncw == 1, fk_log);

	/* no raise on this boot */
	treset();
	icl_arg = 0;
	run_p3(600);
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_no_sweetchgicl_nothing_to_do", rc == 1 && ncw == 0 && nw >= 0 && HAS("nothing to do (CNFG_09 is not raised by sweetchgicl=900 now"), fk_log);
	treset();
	icl_arg = 500; usb_speed = 0; chip[0x69][0xc2] = 0xa2;
	run_p3(600);
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	rc += sweet_chg_icl_thermal(0);
	TCASE("lever_mode_500_nothing_to_do", rc == 2 && ncw == w, fk_log);

	/* hold requested before the raise: the raise is refused while it lasts */
	treset();
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_before_raise_remembered", rc == 1 && p3_therm_hold == 1 && HAS("the raise will be refused while the hold lasts") && ncw == 0, fk_log);
	icl_arg = 900;
	run_p3(600);
	TCASE("lever_raise_refused_while_hold", ncw == 0 && chip[0x69][0xc2] == 0x93 && HAS("thermal hold active (patch 0336)") && p3_stage >= 9 /* 9, or 10 with patch 0339: rows go on */, fk_log);
	/* hold released before the raise: the raise proceeds as in 0287 */
	treset();
	(void)sweet_chg_icl_thermal(1);
	(void)sweet_chg_icl_thermal(0);
	run_p3(600);
	TCASE("lever_release_before_raise_raise_proceeds", ncw == 1 && cwlog[0].val == 0xa2 && p3_therm_hold == 0, fk_log);

	/* busy engine */
	treset();
	run_p3(600);
	enter_ok = 0;
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_busy_returns_2_no_write", rc == 2 && ncw == w, "busy");
	enter_ok = 1;

	/* gate lost / bus dead / write failures */
	treset();
	run_p3(600);
	chg_gate_fail_after = 0; gate_calls = 0;
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_gate_lost_no_write_gap", rc == 2 && ncw == w && i2c_dead == 0 && HAS("thermal hold: charger gate closed for now"), fk_log);
	/* review 0336 F3: a closed gate is transient (2 = asked again), it must not leave the hold undone: the next call with the gate back holds */
	chg_gate_fail_after = 1000; gate_calls = 0;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_gate_closed_then_open_holds", rc == 0 && chip[0x69][0xc2] == 0x93 && ncw == w + 1, fk_log);
	treset();
	run_p3(600);
	nack_slave = 0x69;
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_cnfg09_read_failed_minus1_bus_stopped", rc == -1 && i2c_dead == 1 && ncw == w && HAS("thermal hold: CNFG_09 read failed (bus stopped)"), fk_log);
	treset();
	run_p3(600);
	i2c_dead = 1;
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_bus_dead_no_write_gap", rc == -1 && ncw == w && HAS("charger bus not usable"), fk_log);
	treset();
	run_p3(600);
	wr_garble = 1;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_garbled_write_stops_bus", rc == -1 && i2c_dead == 1 && HAS("write 0x93 or read-back failed"), fk_log);
	treset();
	run_p3(600);
	wr_nack = 1;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_nacked_write_stops_bus", rc == -1 && i2c_dead == 1, fk_log);

	/* ---- review 0336 F1: the 17th lever call must not kill the bus, and a hold is never refused ---- */
	treset();
	stop_at3 = 1;
	run_p3(600);
	setfg(300, 0x0010);
	for (i = 0, rc = 0; i < 17; i++) {
		rc = sweet_chg_icl_thermal((i & 1) == 0);       /* hold, release, hold, ... : the 17th call is a hold */
	}
	TCASE("lever_17th_call_hold_lowers_bus_alive", rc == 0 && i2c_dead == 0 && chip[0x69][0xc2] == 0x93 && p3_twrites == 17 && p3_nwrites == 1 && viol == 0 && only_cnfg09_chip_writes() &&
	    !HAS("all further transfers stopped"), fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("lever_17th_call_sampling_continues", rc == 0 && t == 300, "fg_sample after the 17 lever calls");
	w = ncw;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_at_bound_refused_bus_alive", rc == 1 && i2c_dead == 0 && ncw == w && chip[0x69][0xc2] == 0x93 && p3_twrites == 17 &&
	    HAS("SWEET CHG GAP: P3 thermal release refused: 17 thermal writes this boot (bound 16): CNFG_09 stays at the original 0x93 (500 mA), bus kept"), fk_log);
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_after_refused_release_nothing_to_do", rc == 1 && i2c_dead == 0 && HAS("thermal hold: CNFG_09 already 0x93"), fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("lever_release_at_bound_sampling_continues", rc == 0 && i2c_dead == 0, "fg_sample");
	treset();
	stop_at3 = 1;
	run_p3(600);
	for (i = 0; i < 16; i++) {
		(void)sweet_chg_icl_thermal((i & 1) == 0);      /* 16 writes, the 16th a release: CNFG_09 = 0xa2 at the bound */
	}
	w = ncw;
	rc = sweet_chg_icl_thermal(1);
	TCASE("lever_hold_at_bound_still_lowers", rc == 0 && chip[0x69][0xc2] == 0x93 && p3_twrites == 17 && i2c_dead == 0 && viol == 0 && only_cnfg09_chip_writes(), fk_log);
	treset();
	stop_at3 = 1;
	run_p3(600);
	for (i = 0, w = 0; i < 80; i++) {
		rc = sweet_chg_icl_thermal((i & 1) == 0);
		w += rc == -1;
	}
	TCASE("lever_80_calls_total_thermal_writes_bounded_17_bus_alive", p3_twrites == 17 && w == 0 && i2c_dead == 0 && chip[0x69][0xc2] == 0x93 && only_cnfg09_chip_writes(), ({ static char b[96]; snprintf(b, sizeof(b), "tw=%u fails=%d dead=%d", p3_twrites, w, i2c_dead); b; }));
	/* the bound itself: exactly THERM_MAX_WRITES thermal writes used (state forced: the chip at the original, as after a refused release), a release is refused with the bus alive */
	treset();
	stop_at3 = 1;
	run_p3(600);
	(void)sweet_chg_icl_thermal(1);
	p3_twrites = THERM_MAX_WRITES;
	w = ncw;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_refused_exactly_at_16_bus_alive", rc == 1 && ncw == w && i2c_dead == 0 && chip[0x69][0xc2] == 0x93 && HAS("release refused: 16 thermal writes this boot (bound 16)"), fk_log);
	p3_twrites = THERM_MAX_WRITES - 1;
	rc = sweet_chg_icl_thermal(0);
	TCASE("lever_release_allowed_at_15", rc == 0 && chip[0x69][0xc2] == 0xa2 && p3_twrites == 16, fk_log);
	/* the policy and the real lever together: a hot battery whose first hold meets a closed gate is held at a later sample (review 0336 F3) */
	treset();
	stop_at3 = 1;
	run_p3(600);
	{
		static const struct th_ops iops = { fx1, integ_lever, fcrit };
		struct th_ctl ic;
		int k;

		th_init(&ic);
		tlog[0] = 0;
		integ_gate_closed_n = 2;
		for (k = 0; k < 4; k++) {
			now_ms += TH_PERIOD_MS;
			th_tick(&ic, &iops, TH_RD_OK, 460, 5000, now_ms);
		}
		TCASE("integ_hot_hold_gate_closed_twice_then_held", chip[0x69][0xc2] == 0x93 && ic.chg_held == 1 && i2c_dead == 0 && pcount("charge-hold request at 46.0 C -> engine busy, retried at the next sample") == 2 &&
		    pcount("charge-hold request at 46.0 C -> done") == 1, tlog);
	}

	/* ---- review 0336 F2: the first transfer of the boot on the thermal path runs the pending-transfer checks of the P2 path's first group ---- */
	treset();
	arg_on = 0; icl_arg = 0;
	ticks(3);
	hw[OFF_AUTO / 4] = 1u << 31;    /* AUTO_CONF.MASTER_RUN set: a transfer is still pending that our AUTO_CONF/ADDR stores would retarget */
	hw[OFF_INTST / 4] = 0x80;
	nw = 0; ntx = 0;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_first_transfer_pending_refused", rc == -1 && nw == 0 && ntx == 0 && i2c_dead == 0 && HAS("AUTO_CONF.MASTER_RUN set at the first group"), fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_first_transfer_pending_refused_again_transient", rc == -1 && nw == 0 && i2c_dead == 0, fk_log);
	hw[OFF_AUTO / 4] = 0;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_first_transfer_int_status_refused", rc == -1 && nw == 0 && HAS("INT_STATUS not 0 at the first group"), fk_log);
	hw[OFF_INTST / 4] = 0;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_first_transfer_idle_goes", rc == 0 && nw > 0 && i2c_xfer_seen == 1, fk_log);
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("thermal_later_samples_do_not_recheck_pending", rc == 0, fk_log);
	treset();
	icl_arg = 0;
	ticks(1);
	hw[OFF_AUTO / 4] = 1u << 31;
	hw[OFF_INTST / 4] = 0x80;
	nw = 0;
	ticks(20);
	TCASE("p2_path_first_group_pending_refused_same_state", i2c_dead == 1 && HAS("AUTO_CONF.MASTER_RUN set at the first group") && sweet_chg_fg_sample(&t, &c) == -2, fk_log);
	treset();
	icl_arg = 0;
	ticks(22);
	TCASE("p2_path_marks_first_transfer_done", i2c_xfer_seen == 1 && i2c_p2_ok == 1, fk_log);

	/* ---- review 0336 F5: phase 1 never opened the gate: NOENGINE (rate-limited by the policy), no i2c_gate call and no GAP line per sample ---- */
	treset();
	arg_on = 0; icl_arg = 0;
	ticks(3);
	gate_est = 0;
	gate_calls = 0; nw = 0; ntx = 0;
	for (i = 0, rc = 0; i < 6; i++) {
		rc = sweet_chg_fg_sample(&t, &c);
	}
	TCASE("sample_phase1_never_opened_noengine_no_gate_calls", rc == -3 && gate_calls == 0 && nw == 0 && ntx == 0 && !HAS("P2 phase-1 gate state is not the open pattern") && !HAS("P2 controller not idle"), fk_log);
	gate_est = 1;
	rc = sweet_chg_fg_sample(&t, &c);
	TCASE("sample_phase1_established_reads", rc == 0 && gate_calls >= 1, fk_log);

	/* the guard of the single chip-write function */
	treset();
	i2c_load(); p3_mode = 900;
	(void)i2c_cnfg09_write(0x93, 0xa2);
	w = ncw;
	TCASE("guard_thermal_pair_refused_without_arm", i2c_cnfg09_write(0xa2, 0x93) == 0 && ncw == w + 1 && p3_nwrites == 2 && p3_twrites == 0, "plain 0287 restore must still be a restore");
	treset();
	i2c_load(); p3_mode = 900;
	(void)i2c_cnfg09_write(0x93, 0xa2);
	w = ncw;
	TCASE("guard_release_pair_refused_without_arm", i2c_cnfg09_write(0x93, 0xa2) != 0 && ncw == w, "release pair without arm");
	{
		struct { const char *n; uint8_t cur, val; } g[] = {
			{ "guard_arm_other_value", 0xa2, 0x91 }, { "guard_arm_zero", 0xa2, 0x00 }, { "guard_arm_max", 0xa2, 0xff }, { "guard_arm_flip_autoibus", 0xa2, 0x13 }, { "guard_arm_wrong_cur", 0x91, 0x93 },
			{ "guard_arm_same_cur_val", 0xa2, 0xa2 }, { "guard_arm_orig_to_other", 0x93, 0x13 }, { "guard_arm_orig_to_max", 0x93, 0xff },
		};
		for (i = 0; i < 8; i++) {
			treset();
			i2c_load(); p3_mode = 900;
			(void)i2c_cnfg09_write(0x93, 0xa2);
			w = ncw;
			p3_therm_arm = 1;
			TCASE(g[i].n, i2c_cnfg09_write(g[i].cur, g[i].val) != 0 && ncw == w && p3_twrites == 0, "armed write not refused");
		}
	}
	treset();
	i2c_load(); p3_mode = 900;
	p3_therm_arm = 1;
	TCASE("guard_arm_before_first_write_refused", i2c_cnfg09_write(0xa2, 0x93) != 0 && i2c_cnfg09_write(0x93, 0xa2) == 0 && ncw == 1 && p3_nwrites == 1 && p3_twrites == 0, "arm must not replace the first write");
	treset();
	i2c_load(); p3_mode = 500;
	p3_therm_arm = 1;
	TCASE("guard_arm_mode_500_refused", i2c_cnfg09_write(0xa2, 0x93) != 0 || ncw == 1, "mode 500 with arm");
	treset();
	i2c_load(); p3_mode = 900;
	(void)i2c_cnfg09_write(0x93, 0xa2);
	p3_therm_arm = 1;
	{
		int ok = 1, k;

		/* review 0336 F1: the bound is on the RAISE direction (the release, 0x93 -> 0xa2); a hold (0xa2 -> 0x93) is a lowering and is never refused by it */
		for (k = 0; k < 40; k++) {
			int rel = k & 1;
			int r = i2c_cnfg09_write(rel ? 0x93 : 0xa2, rel ? 0xa2 : 0x93);

			ok &= (rel && k >= 16) ? r != 0 : r == 0;
		}
		TCASE("guard_thermal_release_bounded_at_16_hold_never", ok && p3_nwrites == 1, ({ static char b[128]; snprintf(b, sizeof(b), "ok=%d tw=%u nw=%d", ok, p3_twrites, p3_nwrites); b; }));
		TCASE("guard_thermal_release_count", p3_twrites == 16 + 12, ({ static char b[64]; snprintf(b, sizeof(b), "tw=%u", p3_twrites); b; }));
	}
	treset();
	i2c_load(); p3_mode = 900;
	(void)i2c_cnfg09_write(0x93, 0xa2);
	p3_therm_arm = 1;
	p3_twrites = THERM_MAX_WRITES;
	TCASE("guard_hold_at_bound_allowed_release_refused", i2c_cnfg09_write(0xa2, 0x93) == 0 && i2c_cnfg09_write(0x93, 0xa2) != 0 && i2c_dead == 0, "bound");
	treset();
	i2c_load(); p3_mode = 900;
	(void)i2c_cnfg09_write(0x93, 0xa2);
	p3_therm_arm = 1;
	i2c_dead = 1;
	TCASE("guard_dead_no_thermal_write", i2c_cnfg09_write(0xa2, 0x93) != 0 && ncw == 1, "write after dead");
}

int
main(void)
{
	part_a();
	part_b();
	printf("RESULT: %s sweet-thermal-host fails=%d\n", fails ? "FAIL" : "PASS", fails);
	return fails ? 1 : 0;
}
