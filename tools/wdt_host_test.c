/* Host test of bsd/dev/sweet_wdt.c (xnu/patches/0278 (port of 0193) + 0333), compiled with -DSWEET_WDT_HOST -DWDT_SRC='"path"' (tools/test_wdt_build.py). T-5d2f.
 * One case per run: ./wdt_host_test CASE. Prints "CASE <name> ok" or "CASE <name> FAIL <why>"; exit 0/1. */
#define SWEET_WDT_HOST 1
#include WDT_SRC
#include RESET_SRC     /* patch 0333: the patched sweet_machine_reset_x of bsd/dev/sweet_restart.c (tools/test_wdt_build.py extract_reset_source) */

static int failed;
#define CHECK(c, ...) do { if (!(c)) { printf("CASE %s FAIL line %d: ", cname, __LINE__); printf(__VA_ARGS__); printf("\n"); failed = 1; return; } } while (0)
static const char *cname;

static void world_reset(const char *arg)
{
	wdt_mode = 0; wdt_arm_started = wdt_armed = wdt_sysip_ok = wdt_first_poll_done = wdt_stopped = wdt_stop_ignored = 0;
	wdt_arm_ms = wdt_last_poll_ms = wdt_last_pet_ms = wdt_last_report_ms = wdt_stop_at_ms = 0;
	wdt_pets = wdt_max_gap_ms = 0; wdt_stop = wdt_period = 0;
	memset(fk_ev, 0, sizeof(fk_ev)); fk_nev = 0; fk_now = 5000; memset(fk_wdt, 0, sizeof(fk_wdt));
	fk_pmu_mask = 0x1fc; fk_pmu_cnt = 0x7fd10; fk_pmu_rst = 0x80; fk_pmu_sysip = 0x100;
	fk_smc_fail = fk_wtcon_ignore = fk_cannot_stop = fk_map_fail = fk_node_missing = fk_wtcon_partial = 0;
	fk_mmio = fk_nprints = fk_smc_n = 0; fk_logbuf[0] = 0; fk_arg = arg; fk_real_period_ms = 0; fk_last_cnt_write = 0;
	/* patch 0333 */
	wdt_panicked = wdt_timer_started = wdt_timer_fired = wdt_timer_gap_said = 0; wdt_timer_ms = 0; wdt_timer_start_ms = 0;
	wdt_test_panic_at_ms = 0; wdt_test_panic_s = 0; wdt_threshold_ms = 0; wdt_parse_ms = 0;
	wdt_test_park_at_ms = 0; wdt_test_park_s = 0; fk_parkfail_arg = -1; fk_sw_mode = FK_SW_RESETS; fk_swreset_n = fk_modewrite_n = fk_spin_n = fk_hw_reset_n = fk_park_n = 0;
	fk_nstopped_at_write = fk_print_before_stop = 0; fk_testline_stopped = -1; fk_stopped_probe = fk_probe_always = NULL;
	fk_timer_alloc_fail = fk_timer_armed = fk_timer_n = fk_test_panics = 0; fk_panic_arg = -1; fk_timer_at = 0; fk_timer_last_ms = 0; fk_test_panic_s = 0;
}
static int nwr(int off)     /* writes to WDT register off (-1 = any) */
{
	int n = 0;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && (off < 0 || (int)fk_ev[i].off == off)) { n++; } }
	return n;
}
static int has(const char *s) { return strstr(fk_logbuf, s) != NULL; }
static int count_s(const char *s) { int n = 0; for (const char *p = fk_logbuf; (p = strstr(p, s)) != NULL; p += strlen(s)) { n++; } return n; }
static void boot_timer(const char *arg)   /* parse + setup like sweet_restart_start; since patch 0333 setup also starts the timer vehicle */
{
	world_reset(arg);
	sweet_wdt_parse();
	sweet_wdt_setup(0x17460000u, 0x810u);
}
static void boot(const char *arg)   /* the 0278-era cases: the USB-log drain loop is the only vehicle, so the timer is taken away (it would only add the silent-timer GAP) */
{
	boot_timer(arg);
	wdt_timer_started = 0; fk_timer_armed = 0;
}
static void run_polls(unsigned secs, int may_arm)
{
	for (uint64_t end = fk_now + secs * 1000u; fk_now < end; ) { fk_now += 100; sweet_wdt_poll(may_arm); }
}

/* patch 0333: simulate `secs` seconds in 100 ms steps. The timer fires when due (like the thread_call worker); usb != 0 also runs the drain loop poll
 * every step, restart != 0 the may_arm = 0 poll (sweet_restart_poll). Stops early when the model WDT would reset (returns the ms at which it did, else 0). */
static int fire_guarded(void)       /* the timer tick; the park-fail hook never returns (it spins in the worker): the longjmp of fk_spin lands here */
{
	int r = setjmp(fk_jb);
	if (r == 0) { wdt_timer_fire(); }
	return r;
}
static int fk_parked;
static uint64_t run_timer(unsigned secs, int usb, int restart)
{
	for (uint64_t end = fk_now + secs * 1000u; fk_now < end; ) {
		fk_now += 100;
		if (fk_timer_armed && fk_now >= fk_timer_at) { fk_timer_armed = 0; if (fire_guarded() != 0) { fk_parked = 1; } }
		if (usb) { sweet_wdt_poll(1); }
		if (restart) { sweet_wdt_poll(0); }
		if (fk_reset_now()) { return fk_now; }
	}
	return 0;
}
static int pet_writes_between(uint64_t from, uint64_t to)   /* WTCNT writes with from < t <= to */
{
	int n = 0;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8 && fk_ev[i].t > from && fk_ev[i].t <= to) { n++; } }
	return n;
}

static void c_arith(void)
{
	uint32_t ps, cnt, con;
	CHECK(wdt_period_calc(30, &ps, &cnt, &con) == 0 && ps == 0x57 && cnt == 0xFFAF && con == 0x00015739, "30 s -> %x %x %x (Android: 0x57 0xFFAF 0x15739, O R07)", ps, cnt, con);
	for (unsigned p = 10; p <= 60; p++) {
		CHECK(wdt_period_calc(p, &ps, &cnt, &con) == 0 && ps <= 0xFF && cnt <= 0xFFFF && cnt > 0, "period %u out of hw range", p);
		double real = (double)cnt * (ps + 1) / 192000.0;
		CHECK(real >= p && real < p * 1.0005 + 0.001, "period %u real %.4f", p, real);
		CHECK((con & 4) == 0 && (con & 0x39) == 0x39 && (con & 0x10000), "wtcon %x flags", con);
	}
	CHECK(wdt_period_calc(9, &ps, &cnt, &con) != 0 && wdt_period_calc(61, &ps, &cnt, &con) != 0 && wdt_period_calc(0, &ps, &cnt, &con) != 0, "range not enforced");
}
static void c_parse(void)
{
	static const struct { const char *a; int ok; unsigned p, s; } t[] = {
		{ "30", 1, 30, 0 }, { "30,stop=60", 1, 30, 60 }, { "10,stop=1", 1, 10, 1 }, { "60,stop=600", 1, 60, 600 },
		{ "9", 0, 0, 0 }, { "61", 0, 0, 0 }, { "", 0, 0, 0 }, { "abc", 0, 0, 0 }, { "30,", 0, 0, 0 }, { "30,stop=", 0, 0, 0 }, { "30,stop=0", 0, 0, 0 },
		{ "30,stop=601", 0, 0, 0 }, { "30,stop=60x", 0, 0, 0 }, { "30x", 0, 0, 0 }, { ",stop=60", 0, 0, 0 }, { "30,bootloader", 0, 0, 0 },
		{ "-5", 0, 0, 0 }, { "30 ", 0, 0, 0 }, { "9999999999", 0, 0, 0 }, { "30,stop=60,stop=9", 0, 0, 0 },
	};
	for (unsigned i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
		unsigned p = 0, s = 0;
		int r = wdt_parse_arg(t[i].a, &p, &s);
		CHECK((r == 0) == t[i].ok && (!t[i].ok || (p == t[i].p && s == t[i].s)), "arg '%s' -> %d %u %u", t[i].a, r, p, s);
	}
}
static void c_default_off(void)
{
	boot(NULL);
	sweet_wdt_poll(1); run_polls(120, 1); run_polls(120, 0);
	CHECK(fk_mmio == 0 && fk_smc_n == 0 && fk_nev == 0, "default-off touched hardware: mmio=%d smc=%d", fk_mmio, fk_smc_n);
	CHECK(fk_nprints == 1 && has("SWEET WDT: soc watchdog not armed (no sweetwdt)"), "default-off prints=%d log=%s", fk_nprints, fk_logbuf);
}
static void c_bad_arg(void)
{
	boot("abc"); run_polls(60, 1);
	CHECK(fk_mmio == 0 && has("WDT GAP: bad sweetwdt: abc") && nwr(-1) == 0, "bad arg armed or silent: %s", fk_logbuf);
}
static void c_missing(void)
{
	world_reset("30,stop=60"); sweet_wdt_parse(); sweet_wdt_missing("sweet-reset node"); run_polls(60, 1);
	CHECK(fk_mmio == 0 && has("WDT GAP: read fault: sweet-reset node"), "missing node: %s", fk_logbuf);
}
static void c_map_fail(void)
{
	world_reset("30"); fk_map_fail = 1; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_polls(60, 1);
	CHECK(has("WDT GAP: map failed: wdt") && nwr(-1) == 0 && !has("armed period"), "map failure armed: %s", fk_logbuf);
}
static void c_allones(void)
{
	world_reset("30"); memset(fk_wdt, 0xff, sizeof(fk_wdt)); sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_polls(60, 1);
	CHECK(has("WDT GAP: read fault: wtcon all-ones") && nwr(-1) == 0, "all-ones: %s", fk_logbuf);
}
static void c_setup_readonly(void)
{
	boot("30,stop=60");
	CHECK(nwr(-1) == 0 && fk_smc_n == 0, "setup wrote hardware (SQ2 is read-only)");
	CHECK(count_s("SWEET WDT: inherited wtcon=0x0 wtdat=0x0 wtcnt=0x0 wtcnt10=0x0 wtmincnt=0x0 pmu1244=0x1fc pmu1220=0x7fd10 rst_stat=0x80 sysip=0x100") == 1, "inherited line: %s", fk_logbuf);
	CHECK(has("cluster 1 left stopped by the shim (D5)") && has("sweetwdt=30 stop=60 parsed, arm follows the first poll of the timer (no USB needed) or of the USB-log drain loop") && !has("arm waits for the first USB-log poll"), "lines: %s", fk_logbuf);
	run_polls(120, 0);
	CHECK(nwr(-1) == 0 && fk_smc_n == 0, "pet-only callers armed the WDT");
}
static void c_full_run(void)
{
	boot("30,stop=60");
	run_polls(1, 1);
	CHECK(has("first poll at +") && has("SWEET WDT: armed period=30 threshold_ms=10000 wtcon=0x15739 count=0xffaf readback_wtcon=0x15739"), "arm lines: %s", fk_logbuf);
	/* write order: stop, WTDAT, WTCNT, WTCON last; then SMC after the arm */
	int seq[8], n = 0, smc_after = -1;
	for (int i = 0; i < fk_nev && n < 8; i++) {
		if (fk_ev[i].kind == EV_WR_WDT) { seq[n++] = (int)fk_ev[i].off; }
		if (fk_ev[i].kind == EV_SMC) { smc_after = n; }
	}
	CHECK(n >= 4 && seq[0] == 0 && seq[1] == 4 && seq[2] == 8 && seq[3] == 0, "arm write order %d %d %d %d", seq[0], seq[1], seq[2], seq[3]);
	CHECK(fk_smc_n == 1 && smc_after == 4 && fk_pmu_sysip == 0xfc && has("SYSIP_DAT0 0x100 -> 0xfc"), "SMC once after the arm: n=%d after=%d", fk_smc_n, smc_after);
	uint64_t armed_at = wdt_arm_ms, stop_seen = 0, reset_at = 0, last_pet_write = 0;
	int prints_before = fk_nprints, polls = 0;
	for (; fk_now < armed_at + 200000u; polls++) {
		fk_now += 100; sweet_wdt_poll(1);
		if (!stop_seen && has("test: petting stopped")) { stop_seen = fk_now; }
		if (fk_reset_now()) { reset_at = fk_now; break; }
		if (polls == 999) { CHECK(fk_nprints - prints_before <= 3, "%d prints in 1000 polls", fk_nprints - prints_before); }
	}
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8) { last_pet_write = fk_ev[i].t; } }
	CHECK(stop_seen != 0 && reset_at != 0, "no stop line or no reset (stop=%llu reset=%llu)", (unsigned long long)stop_seen, (unsigned long long)reset_at);
	CHECK(last_pet_write <= stop_seen, "WTCNT written after the stop line (%llu > %llu)", (unsigned long long)last_pet_write, (unsigned long long)stop_seen);
	double dt = (reset_at - armed_at) / 1000.0;
	CHECK(dt >= 88.9 && dt <= 90.5, "reset %.1f s after arm, spec window 88.9..90.0 (+tick)", dt);
	CHECK(has("SWEET WDT: pet n=1 max_gap_ms=") && has("wtcnt_before=0x") && has("ms_since_arm="), "first pet line: %s", fk_logbuf);
	CHECK(!has("WDT GAP:"), "unexpected GAP: %s", fk_logbuf);
	int pets = 0; uint64_t prev = 0;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8 && fk_ev[i].t > armed_at) { pets++; CHECK(prev == 0 || fk_ev[i].t - prev >= 1000, "two pets %llu ms apart", (unsigned long long)(fk_ev[i].t - prev)); prev = fk_ev[i].t; } }
	CHECK(pets >= 55 && pets <= 61, "pets=%d in 60 s", pets);
}
static void c_no_stop_never_resets(void)
{
	boot("30"); run_polls(1, 1);
	for (int i = 0; i < 3000; i++) { fk_now += 100; sweet_wdt_poll(i & 1); CHECK(!fk_reset_now(), "reset while petting at +%d ms", i * 100); }
	CHECK(count_s("SWEET WDT: pet n=") >= 4 && count_s("SWEET WDT: pet n=") <= 7, "pet lines %d over 300 s", count_s("SWEET WDT: pet n="));
}
static void c_gate_clear(void)
{
	for (int which = 0; which < 2; which++) {
		boot("30,stop=60");
		if (which == 0) { fk_pmu_mask = 0x1f8; } else { fk_pmu_cnt = 0x7fc10; }
		uint32_t con0 = fk_wdt[0];
		run_polls(30, 1);
		CHECK(has("WDT GAP: PMU gate not set (0x1244=0x") && has(", not armed") && nwr(-1) == 0 && fk_smc_n == 0 && fk_wdt[0] == con0, "gate %d: %s", which, fk_logbuf);
	}
}
static void c_arm_readback(void)
{
	boot("30"); fk_wtcon_ignore = 1; run_polls(5, 1);
	CHECK(has("WDT GAP: arm readback wtcon=0x0") && fk_smc_n == 0 && !has("armed period"), "readback: %s", fk_logbuf);
	CHECK((fk_wdt[0] & 0x21) == 0, "WDT left enabled after a failed arm");
}
static void c_gate_allones(void)       /* review pr4-wdt F1: an all-ones PMU read has every bit set and must not pass the gate */
{
	for (int which = 0; which < 2; which++) {
		boot("30,stop=60");
		if (which == 0) { fk_pmu_mask = 0xFFFFFFFFu; } else { fk_pmu_cnt = 0xFFFFFFFFu; }
		run_polls(30, 1);
		CHECK(has("WDT GAP: read fault: pmu gate all-ones") && has("not armed") && nwr(-1) == 0 && fk_smc_n == 0 && !has("armed period"), "allones %d: %s", which, fk_logbuf);
	}
}
static void c_arm_readback_partial(void)   /* review pr4-wdt F2/F4: ENABLE+RSTEN latch but DBGACK does not: not the value written */
{
	boot("30"); fk_wtcon_partial = 1; run_polls(5, 1);
	CHECK(has("WDT GAP: arm readback wtcon=0x") && fk_smc_n == 0 && !has("armed period"), "partial latch accepted: %s", fk_logbuf);
	CHECK((fk_wdt[0] & 0x21) == 0, "WDT left enabled after a partial arm (stop on mismatch skipped)");
	int w = nwr(8); run_polls(60, 1);
	CHECK(nwr(8) == w && !fk_reset_now(), "pets or reset after a stopped partial arm");
}
static void c_arm_readback_failstop(void)  /* review pr4-wdt F4: mismatch and the stop fails: WDT still running, KEEP PETTING */
{
	boot("30"); fk_wtcon_partial = 1; fk_cannot_stop = 1; run_polls(1, 1);
	CHECK(has("WDT GAP: stop readback, WDT still armed") && (fk_wdt[0] & 0x21) == 0x21, "lines: %s", fk_logbuf);
	for (int i = 0; i < 3000; i++) { fk_now += 100; sweet_wdt_poll(1); CHECK(!fk_reset_now(), "unannounced reset at +%d ms (a failed stop must keep petting)", i * 100); }
}
static void c_armed_after_sysip(void)  /* review pr4-wdt F3: the armed line is printed after the SYSIP_DAT0 write, never between the enable and it */
{
	boot("30,stop=60"); run_polls(5, 1);
	const char *a = strstr(fk_logbuf, "SWEET WDT: armed period="), *s = strstr(fk_logbuf, "SWEET WDT: SYSIP_DAT0 0x");
	CHECK(a && s && a < s && fk_smc_n == 1, "line order / smc writes: %s", fk_logbuf);
	int enable_i = -1, smc_i = -1;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 0 && (fk_ev[i].val & 0x21) == 0x21 && enable_i < 0) { enable_i = i; } if (fk_ev[i].kind == EV_SMC) { smc_i = i; } }
	CHECK(enable_i >= 0 && smc_i > enable_i, "smc not after enable");
}
static void c_sysip_fail(void)
{
	boot("30,stop=60"); fk_smc_fail = 1; run_polls(5, 1);
	CHECK(has("WDT GAP: SYSIP_DAT0 write/readback") && (fk_wdt[0] & 0x21) == 0 && !fk_reset_now(), "sysip fail left WDT running: %s", fk_logbuf);
	int w = nwr(8); run_polls(120, 1);
	CHECK(nwr(8) == w, "pets after a stopped arm");
}
static void c_sysip_fail_cannot_stop(void)
{
	boot("30,stop=60"); fk_smc_fail = 1; fk_cannot_stop = 1; run_polls(1, 1);
	CHECK(has("WDT GAP: SYSIP_DAT0 write/readback") && has("WDT GAP: stop readback, WDT still armed"), "lines: %s", fk_logbuf);
	for (int i = 0; i < 2000; i++) { fk_now += 100; sweet_wdt_poll(1); CHECK(!fk_reset_now(), "unannounced reset at +%d ms (a failed stop must keep petting)", i * 100); }
	CHECK(has("WDT GAP: stop test ignored (SYSIP_DAT0 not 0xFC)") && count_s("stop test ignored") == 1 && !has("petting stopped"), "stop honoured without SYSIP: %s", fk_logbuf);
}
static void c_pet_gap(void)
{
	boot("30"); run_polls(5, 1);
	fk_now += 15000; sweet_wdt_poll(1);
	CHECK(has("WDT GAP: pet gap 15000 ms (threshold 10000)") && count_s("WDT GAP: pet gap") == 1 && wdt_max_gap_ms == 15000, "gap: %s", fk_logbuf);
}
static void c_once_flag(void)
{
	boot("30"); sweet_wdt_poll(1);
	int w = nwr(-1); wdt_arm_started = 1; wdt_armed = 0;      /* a loser of the cmpxchg must not touch a register */
	sweet_wdt_poll(1); sweet_wdt_poll(1);
	CHECK(nwr(-1) == w, "second arm wrote registers");
}


/* ---- patch 0333 cases ---- */
static void c_timer_arms_no_usb(void)       /* the incident: no USB gadget, so no drain loop ever polls; the timer must arm and pet */
{
	boot_timer("60");
	CHECK(fk_timer_n == 1 && fk_timer_armed && fk_timer_last_ms == 0 && wdt_timer_ms == 5000 && wdt_timer_started, "timer not started by setup: n=%d last=%u ms=%u", fk_timer_n, fk_timer_last_ms, wdt_timer_ms);
	CHECK(nwr(-1) == 0 && fk_smc_n == 0, "setup wrote hardware");
	CHECK(has("SWEET WDT: timer pet source started: every 5000 ms, first tick now (no USB needed; patch 0333)"), "start line: %s", fk_logbuf);
	uint64_t t0 = fk_now;
	CHECK(run_timer(300, 0, 0) == 0, "model WDT reset while the timer petted (no USB poll at all)");
	CHECK(wdt_armed && wdt_sysip_ok && has("SWEET WDT: arm/pet source = timer (no USB needed)") && count_s("arm/pet source") == 1, "armed from the timer: %s", fk_logbuf);
	CHECK(has("SWEET WDT: armed period=60 threshold_ms=20000") && fk_smc_n == 1 && !has("WDT GAP:"), "arm lines / GAP: %s", fk_logbuf);
	int pets = pet_writes_between(t0, fk_now);
	CHECK(pets >= 56 && pets <= 61, "pets=%d in 300 s at a 5 s interval (60 expected)", pets);
	uint64_t prev = 0;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8 && fk_ev[i].t > t0 + 1000) { CHECK(prev == 0 || (fk_ev[i].t - prev >= 4900 && fk_ev[i].t - prev <= 5300), "pet spacing %llu ms", (unsigned long long)(fk_ev[i].t - prev)); prev = fk_ev[i].t; } }
}
static void c_timer_same_writes(void)       /* no new register / value / site: the timer arm writes exactly what the drain-loop arm writes */
{
	struct fk_event a[16]; int na = 0, nb = 0;
	boot_timer("30"); run_timer(2, 0, 0);
	for (int i = 0; i < fk_nev && na < 16; i++) { if (fk_ev[i].kind != EV_RD_WDT) { a[na++] = fk_ev[i]; } }
	boot_timer("30"); fk_timer_armed = 0;                         /* the timer exists but never fires: arm from the drain loop only */
	for (int i = 0; i < 20; i++) { fk_now += 100; sweet_wdt_poll(1); }
	for (int i = 0, j = 0; i < fk_nev && j < na; i++) {
		if (fk_ev[i].kind == EV_RD_WDT) { continue; }
		CHECK(fk_ev[i].kind == a[j].kind && fk_ev[i].off == a[j].off && fk_ev[i].val == a[j].val, "write %d differs: timer kind=%d off=%x val=%x, drain kind=%d off=%x val=%x", j, a[j].kind, a[j].off, a[j].val, fk_ev[i].kind, fk_ev[i].off, fk_ev[i].val);
		j++; nb++;
	}
	CHECK(na >= 5 && nb == na, "event counts %d %d", na, nb);
	for (int i = 0; i < fk_nev; i++) { CHECK(fk_ev[i].kind != EV_WR_WDT || fk_ev[i].off == 0 || fk_ev[i].off == 4 || fk_ev[i].off == 8, "write to WDT offset 0x%x", fk_ev[i].off); }
}
static void c_timer_cadence_all_periods(void)   /* every legal period: interval within [1 s, 5 s], below the period/3 gap threshold, no GAP, no reset */
{
	for (unsigned per = 10; per <= 60; per++) {
		char arg[16]; snprintf(arg, sizeof(arg), "%u", per);
		boot_timer(arg);
		CHECK(wdt_timer_ms >= 1000 && wdt_timer_ms <= 5000 && wdt_timer_ms < wdt_threshold_ms, "period %u: interval %u ms vs threshold %u", per, wdt_timer_ms, wdt_threshold_ms);
		CHECK(run_timer(per * 6, 0, 0) == 0, "period %u: model WDT reset while the timer petted", per);
		CHECK(!has("WDT GAP:") && wdt_armed, "period %u: GAP or not armed: %s", per, fk_logbuf);
	}
}
static void c_timer_then_hang(void)         /* the timer dies (CPU wedged): reset one period after the last pet */
{
	boot_timer("60"); run_timer(30, 0, 0);
	uint64_t last = 0;
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8) { last = fk_ev[i].t; } }
	fk_timer_armed = 0;                                    /* no more ticks */
	uint64_t r = run_timer(200, 0, 0);
	CHECK(r != 0 && r - last >= 60000 && r - last <= 61000, "reset %llu ms after the last pet, expected 60000..61000", (unsigned long long)(r - last));
}
static void c_panic_stops_pets(void)        /* panic note: no pet from the timer, the drain loop or the restart poll; reset <= one period after the last pet */
{
	for (int usb = 0; usb < 2; usb++) {
		boot_timer("30"); run_timer(40, usb, usb);
		CHECK(wdt_armed && !fk_reset_now(), "not armed or reset before the panic");
		uint64_t tp = fk_now; int w = nwr(8);
		sweet_wdt_panic_note();
		uint64_t r = run_timer(120, usb, usb);
		CHECK(nwr(8) == w, "usb=%d: %d WTCNT writes after the panic note", usb, nwr(8) - w);
		CHECK(!fk_timer_armed || fk_timer_n >= 1, "timer state");
		CHECK(r != 0 && r - tp >= 25000 - 1100 && r - tp <= 30100, "usb=%d: reset %llu ms after the panic, expected <= 30 s (period) and >= 30 s - interval - 1 s", usb, (unsigned long long)(r - tp));
		int n = fk_timer_n; fk_timer_armed = 0; fk_now += 10000; wdt_timer_fire();
		CHECK(fk_timer_n == n && !fk_timer_armed, "timer re-armed itself after the panic note");
	}
	boot_timer("30"); sweet_wdt_panic_note(); run_timer(60, 1, 1);        /* panic before the first arm: never armed afterwards */
	CHECK(nwr(-1) == 0 && fk_smc_n == 0 && !wdt_armed, "armed after a panic note: %s", fk_logbuf);
	sweet_wdt_panic_note(); sweet_wdt_panic_note();                  /* idempotent */
}
static void c_panic_text(void)
{
	world_reset(NULL); sweet_wdt_parse();
	CHECK(strstr(sweet_wdt_panic_text(), "SWEET WDT: off ") != NULL && strstr(sweet_wdt_panic_text(), "SWEET WDT GAP") == NULL, "off: %s", sweet_wdt_panic_text());
	world_reset("30"); sweet_wdt_parse();
	CHECK(strstr(sweet_wdt_panic_text(), "SWEET WDT GAP: panic before the watchdog setup") != NULL, "parsed: %s", sweet_wdt_panic_text());
	sweet_wdt_setup(0x17460000u, 0x810u);
	CHECK(strstr(sweet_wdt_panic_text(), "SWEET WDT GAP: NOT armed at this panic") != NULL, "ready, not armed: %s", sweet_wdt_panic_text());
	run_timer(2, 0, 0);
	CHECK(strstr(sweet_wdt_panic_text(), "SWEET WDT: armed, pets stopped by this panic") != NULL, "armed: %s", sweet_wdt_panic_text());
}
static void c_timer_silent_gap(void)        /* the thread_call never runs (cause unknown, cf. 0083): the drain loop arms, and says the timer is silent */
{
	boot_timer("30"); fk_timer_armed = 0;
	for (int i = 0; i < 300; i++) { fk_now += 100; sweet_wdt_poll(1); }
	CHECK(wdt_armed && has("SWEET WDT: arm/pet source = USB-log drain loop (the timer had not armed yet)") && !has("source = timer"), "drain arm line: %s", fk_logbuf);
	CHECK(count_s("WDT GAP: timer pet source has not run") == 1, "silent-timer GAP count %d: %s", count_s("WDT GAP: timer pet source has not run"), fk_logbuf);
	boot_timer("30"); for (int i = 0; i < 300; i++) { fk_now += 100; if (fk_timer_armed && fk_now >= fk_timer_at) { fk_timer_armed = 0; wdt_timer_fire(); } sweet_wdt_poll(1); }
	CHECK(!has("WDT GAP: timer pet source has not run"), "false silent-timer GAP: %s", fk_logbuf);
}
static void c_timer_alloc_fail(void)
{
	world_reset("30"); fk_timer_alloc_fail = 1; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u);
	CHECK(has("WDT GAP: timer pet source not started (thread_call_allocate failed)") && fk_timer_n == 0 && !wdt_timer_started, "alloc fail: %s", fk_logbuf);
	for (int i = 0; i < 50; i++) { fk_now += 100; sweet_wdt_poll(1); }
	CHECK(wdt_armed, "drain loop did not arm after a failed timer start");
}
static void c_timer_default_off(void)
{
	boot_timer(NULL); CHECK(fk_timer_n == 0 && !wdt_timer_started && fk_mmio == 0, "default-off started the timer or touched hardware");
	boot_timer("abc"); CHECK(fk_timer_n == 0, "bad arg started the timer");
	world_reset("30"); fk_map_fail = 1; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); CHECK(fk_timer_n == 0, "failed setup started the timer");
	world_reset("30"); fk_node_missing = 1; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); CHECK(fk_timer_n == 0, "node missing started the timer");
	world_reset("30"); sweet_wdt_parse(); sweet_wdt_missing("sweet-reset node"); sweet_wdt_setup(0x17460000u, 0x810u); CHECK(fk_timer_n == 0, "missing node started the timer");
}
static void c_timer_stop_test_still_works(void) /* sweetwdt=30,stop=60: the timer must not keep petting past the deliberate stop */
{
	boot_timer("30,stop=60"); uint64_t r = run_timer(200, 0, 0);
	double dt = (r - 5000) / 1000.0;     /* the last pet is up to one timer interval (5 s) before the stop line: 60 s + 30 s - 5 s .. 60 s + 30 s */
	CHECK(r != 0 && has("test: petting stopped") && dt >= 84.5 && dt <= 91.5, "stop test via the timer: reset after %.1f s: %s", dt, fk_logbuf);
}
static void c_test_panic(void)
{
	world_reset("60"); fk_panic_arg = 15; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u);
	CHECK(has("WDT GAP: sweetwdtabort=15 set (test only)"), "set line: %s", fk_logbuf);
	uint64_t t0 = fk_now;
	run_timer(14, 0, 0);
	CHECK(fk_test_panics == 0, "panic early");
	run_timer(3, 0, 0);
	CHECK(fk_test_panics == 1 && fk_test_panic_s == 15 && has("SWEET WDT TEST: sweetwdtabort=15: aborting now"), "panic missing: %s", fk_logbuf);
	CHECK(fk_now - t0 >= 15000 && fk_now - t0 <= 17000, "panic at +%llu ms", (unsigned long long)(fk_now - t0));
	run_timer(30, 0, 0);
	CHECK(fk_test_panics == 1, "test panic fired %d times", fk_test_panics);
	/* refused when the watchdog is not armed (gate clear) */
	world_reset("60"); fk_panic_arg = 5; fk_pmu_mask = 0x1f8; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u);
	run_timer(30, 0, 0);
	CHECK(fk_test_panics == 0 && has("WDT GAP: sweetwdtabort=5 refused: the watchdog is not armed"), "unarmed panic: %s", fk_logbuf);
	/* out of range / zero */
	world_reset("60"); fk_panic_arg = 601; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(700, 0, 0);
	CHECK(fk_test_panics == 0 && has("WDT GAP: sweetwdtabort=601 above 600, ignored"), "range: %s", fk_logbuf);
	world_reset("60"); fk_panic_arg = 0; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(30, 0, 0);
	CHECK(fk_test_panics == 0 && has("sweetwdtabort=0: no test abort"), "zero: %s", fk_logbuf);
	/* absent: nothing */
	boot_timer("60"); run_timer(100, 0, 0); CHECK(fk_test_panics == 0 && !has("sweetwdtabort"), "absent arg logged or fired");
}

/* ---- patch 0333: failed park (sweet_machine_reset stops the pets first; sweetwdtparkfail) ---- */
static int probe_stopped(void) { return ALOAD(&wdt_panicked); }
/* run the patched reset path: returns 1 = spun (the CPU spin, SMC refused or returned), 2 = the SMC reset the phone */
static int do_reset(int to_bl)
{
	int r = setjmp(fk_jb);
	if (r == 0) { fk_stopped_probe = probe_stopped; sweet_machine_reset(to_bl); }
	fk_stopped_probe = NULL;
	return r;
}
static void c_reset_stops_pets(void)        /* every software reset stops all three petters BEFORE its first write; a failed SMC leaves the watchdog as the backstop and nothing re-enables the pets */
{
	for (int to_bl = 0; to_bl < 2; to_bl++) {
		for (int mode = FK_SW_RESETS; mode <= FK_SW_SILENT; mode++) {
			for (int usb = 0; usb < 2; usb++) {
				boot_timer("30"); fk_probe_always = probe_stopped;
				CHECK(run_timer(40, usb, usb) == 0 && wdt_armed && !wdt_panicked, "to_bl=%d mode=%d usb=%d: not armed / stopped before the reset", to_bl, mode, usb);
				uint64_t tp = fk_now; int w = nwr(8);
				fk_sw_mode = mode;
				int r = do_reset(to_bl);
				CHECK(ALOAD(&wdt_panicked), "to_bl=%d mode=%d: reset path did not stop the pets", to_bl, mode);
				CHECK(fk_nstopped_at_write == 1 + to_bl, "to_bl=%d mode=%d: %d reset writes, expected %d (mode write only for bootloader + SWRESET)", to_bl, mode, fk_nstopped_at_write, 1 + to_bl);
				for (int i = 0; i < fk_nstopped_at_write; i++) { CHECK(fk_stopped_at_write[i] == 1, "to_bl=%d mode=%d: reset write %d issued BEFORE the pets were stopped", to_bl, mode, i); }
				CHECK(fk_print_before_stop == 0, "to_bl=%d mode=%d: %d print(s) before the stop store (could fault in the panic path)", to_bl, mode, fk_print_before_stop);
				CHECK(fk_swreset_n == 1 && fk_modewrite_n == to_bl, "to_bl=%d mode=%d: swreset %d mode writes %d", to_bl, mode, fk_swreset_n, fk_modewrite_n);
				if (mode == FK_SW_RESETS) {
					CHECK(r == 2 && fk_hw_reset_n == 1 && fk_spin_n == 0, "mode 0: r=%d resets=%d spins=%d", r, fk_hw_reset_n, fk_spin_n);
					continue;
				}
				CHECK(r == 1 && fk_spin_n == 1 && fk_hw_reset_n == 0, "mode %d: the failed SMC must fall into the spin: r=%d spins=%d", mode, r, fk_spin_n);
				CHECK(has("SWEET RESTART GAP: PMU SMC software reset returned x0=0x") && (mode == FK_SW_SILENT ? has("x0=0x0 ") : has("x0=0xfffffffffffffff7")), "GAP line missing/wrong: %s", fk_logbuf);
				/* the other CPUs keep running: the timer, the drain loop and the restart poll all try to pet; the flag must hold (including a late timer tick and a late arm attempt) */
				uint64_t r2 = run_timer(120, usb, usb);
				wdt_timer_fire(); sweet_wdt_poll(1); sweet_wdt_poll(0);
				CHECK(nwr(8) == w, "to_bl=%d mode=%d usb=%d: %d WTCNT write(s) after the failed reset", to_bl, mode, usb, nwr(8) - w);
				CHECK(ALOAD(&wdt_panicked) && !fk_timer_armed, "pets re-enabled or timer re-armed after the failed SMC");
				CHECK(r2 != 0 && r2 - tp >= 30000 - 5100 - 1100 && r2 - tp <= 30100, "to_bl=%d mode=%d usb=%d: watchdog bit %llu ms after the failed reset, expected <= one period (30 s)", to_bl, mode, usb, (unsigned long long)(r2 - tp));
			}
		}
	}
}
static void c_reset_before_arm_no_arm(void) /* a reset path before the first arm must not arm afterwards either (nothing to bite, but nothing may write) */
{
	boot_timer("30"); (void)do_reset(1);
	fk_sw_mode = FK_SW_REFUSED; (void)do_reset(1);
	run_timer(60, 1, 1);
	CHECK(nwr(-1) == 0 && fk_smc_n == 0 && !wdt_armed, "armed after a reset path: %s", fk_logbuf);
}
static void c_parkfail_hook(void)           /* sweetwdtparkfail=20: park path without the SWRESET SMC, then the watchdog resets */
{
	world_reset("60"); fk_parkfail_arg = 20; fk_probe_always = probe_stopped; fk_parked = 0; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u);
	CHECK(has("WDT GAP: sweetwdtparkfail=20 set (test only)"), "set line: %s", fk_logbuf);
	uint64_t t0 = fk_now;
	run_timer(19, 1, 1);
	CHECK(fk_park_n == 0 && !fk_parked && wdt_armed, "park early: n=%d", fk_park_n);
	run_timer(3, 1, 1);
	CHECK(fk_park_n == 1 && fk_parked && fk_swreset_n == 0 && fk_spin_n == 1, "park hook: n=%d parked=%d swreset=%d spins=%d", fk_park_n, fk_parked, fk_swreset_n, fk_spin_n);
	CHECK(has("SWEET WDT TEST: park SMC skipped, pets stopped, watchdog must reset within 60 s"), "test line: %s", fk_logbuf);
	CHECK(fk_testline_stopped == 1, "the test line was printed before the pets were stopped (%d)", fk_testline_stopped);
	CHECK(!has("SWEET RESTART GAP: PMU SMC software reset returned"), "a skipped SMC reported as a refusal: %s", fk_logbuf);
	CHECK(fk_modewrite_n == 1 && fk_nstopped_at_write == 1 && fk_stopped_at_write[0] == 1, "writes: mode=%d n=%d stopped=%d (only the bootloader-mode write, after the stop)", fk_modewrite_n, fk_nstopped_at_write, fk_stopped_at_write[0]);
	CHECK(fk_now - t0 >= 20000 && fk_now - t0 <= 22000, "park at +%llu ms", (unsigned long long)(fk_now - t0));
	uint64_t line_at = fk_now, last = 0; int w = nwr(8);
	uint64_t r = run_timer(200, 1, 1);               /* the other petters keep trying */
	for (int i = 0; i < fk_nev; i++) { if (fk_ev[i].kind == EV_WR_WDT && fk_ev[i].off == 8) { last = fk_ev[i].t; } }
	CHECK(nwr(8) == w && fk_park_n == 1 && fk_swreset_n == 0, "pets or a second park after the hook: %d", nwr(8) - w);
	CHECK(r != 0 && r - last >= 60000 && r - last <= 60100, "watchdog bit %llu ms after the last pet, expected one period (60 s)", (unsigned long long)(r - last));
	CHECK(r - line_at <= 60100 && r - line_at >= 60000 - 5100, "watchdog bit %llu ms after the park line, expected about one period", (unsigned long long)(r - line_at));
	/* refused when the watchdog is not armed (gate clear) */
	world_reset("60"); fk_parkfail_arg = 5; fk_pmu_mask = 0x1f8; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u);
	run_timer(30, 1, 1);
	CHECK(fk_park_n == 0 && !wdt_panicked && has("WDT GAP: sweetwdtparkfail=5 refused: the watchdog is not armed"), "unarmed park: %s", fk_logbuf);
	/* out of range / zero / absent */
	world_reset("60"); fk_parkfail_arg = 601; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(700, 0, 0);
	CHECK(fk_park_n == 0 && has("WDT GAP: sweetwdtparkfail=601 above 600, ignored"), "range: %s", fk_logbuf);
	world_reset("60"); fk_parkfail_arg = 0; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(30, 0, 0);
	CHECK(fk_park_n == 0 && has("sweetwdtparkfail=0: no failed-park test"), "zero: %s", fk_logbuf);
	boot_timer("60"); run_timer(100, 0, 0); CHECK(fk_park_n == 0 && !has("sweetwdtparkfail"), "absent arg logged or fired");
	/* both test hooks: one proof per boot, the panic wins and the park is reported ignored */
	world_reset("60"); fk_panic_arg = 10; fk_parkfail_arg = 5; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(30, 0, 0);
	CHECK(fk_park_n == 0 && fk_test_panics == 1 && has("WDT GAP: sweetwdtparkfail=5 ignored: sweetwdtabort is set too"), "both hooks: %s", fk_logbuf);
	/* with the watchdog off: no timer, no hook, nothing */
	world_reset(NULL); fk_parkfail_arg = 5; sweet_wdt_parse(); sweet_wdt_setup(0x17460000u, 0x810u); run_timer(30, 0, 0);
	CHECK(fk_park_n == 0 && fk_timer_n == 0, "park hook ran with the watchdog off");
}

int main(int argc, char **argv)
{
	static const struct { const char *n; void (*f)(void); } cases[] = {
		{ "arith", c_arith }, { "parse", c_parse }, { "default_off", c_default_off }, { "bad_arg", c_bad_arg }, { "missing", c_missing },
		{ "map_fail", c_map_fail }, { "allones", c_allones }, { "setup_readonly", c_setup_readonly }, { "full_run", c_full_run },
		{ "no_stop_never_resets", c_no_stop_never_resets }, { "gate_clear", c_gate_clear }, { "arm_readback", c_arm_readback },
		{ "gate_allones", c_gate_allones }, { "arm_readback_partial", c_arm_readback_partial }, { "arm_readback_failstop", c_arm_readback_failstop }, { "armed_after_sysip", c_armed_after_sysip }, { "sysip_fail", c_sysip_fail }, { "sysip_fail_cannot_stop", c_sysip_fail_cannot_stop }, { "pet_gap", c_pet_gap }, { "once_flag", c_once_flag },
		{ "timer_arms_no_usb", c_timer_arms_no_usb }, { "timer_same_writes", c_timer_same_writes }, { "timer_cadence_all_periods", c_timer_cadence_all_periods },
		{ "timer_then_hang", c_timer_then_hang }, { "panic_stops_pets", c_panic_stops_pets }, { "panic_text", c_panic_text }, { "timer_silent_gap", c_timer_silent_gap },
		{ "timer_alloc_fail", c_timer_alloc_fail }, { "timer_default_off", c_timer_default_off }, { "timer_stop_test", c_timer_stop_test_still_works }, { "test_panic", c_test_panic },
		{ "reset_stops_pets", c_reset_stops_pets }, { "reset_before_arm_no_arm", c_reset_before_arm_no_arm }, { "parkfail_hook", c_parkfail_hook },
	};
	int n = 0;
	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
		if (argc > 1 && strcmp(argv[1], cases[i].n) != 0) { continue; }
		cname = cases[i].n; cases[i].f(); n++;
		if (!failed) { printf("CASE %s ok\n", cname); }
		failed = 0;
	}
	return 0;
}
