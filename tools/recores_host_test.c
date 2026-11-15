/*
 * Host model for xnu/patches/0344-sweet-sched-recores-late.patch, compiled and run by tools/test_recores.py with -DLOGIC_SRC="<file>" = the patch's
 * sweet_recores_point() (the text between its SWEET-RECORES-LOGIC marks). No kernel, no device.
 * The scheduler here is a MODEL transcribed from osfmk/kern/sched_prim.c of xnu-8796.141.3 [read]: sched_perfcontrol_update_recommended_cores_reason stores the
 * request and applies it unless the failsafe or the sleep override is active; sched_update_recommended_cores adds the boot CPU when no requested core is
 * online and then makes every pset's recommended_bitmask equal to the request within the registered CPUs. It proves the patch's decisions and text, not the kernel.
 * Prints `CASE <name> ok` or `CASE <name> FAIL <why>` per case.
 */
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ALL_CORES_RECOMMENDED (~(uint64_t)0)

static struct {
	unsigned int ncpu;              /* ml_get_cpu_count() */
	uint64_t registered;            /* pset->cpu_bitmask (processor_init ran) */
	uint64_t online;                /* sched_online_processors */
	uint64_t pset_rec;              /* pset->recommended_bitmask */
	uint64_t perf_req;              /* perfcontrol_requested_recommended_cores */
	uint32_t held;                  /* 1 failsafe active, 2 sleep override */
	int have_arg;
	uint32_t arg;
	unsigned int applies;           /* calls of sched_perfcontrol_update_recommended_cores */
} w;
static unsigned int bad_arg_name;      /* a boot-arg other than sweetrecores was parsed (kept across boot()) */
static char out[8192];
static size_t outn;

static int
m_printf(const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(out + outn, sizeof(out) - outn, fmt, ap);
	va_end(ap);
	if (n > 0 && outn + (size_t)n < sizeof(out)) {
		outn += (size_t)n;
	}
	return n;
}

static unsigned int
ml_get_cpu_count(void)
{
	return w.ncpu;
}

static bool
PE_parse_boot_argn(const char *name, void *p, int size)
{
	if (strcmp(name, "sweetrecores") != 0 || size != (int)sizeof(uint32_t)) {
		bad_arg_name++;
		return false;
	}
	if (!w.have_arg) {
		return false;
	}
	memcpy(p, &w.arg, sizeof(uint32_t));
	return true;
}

static void
m_update(uint64_t rec)
{
	if (__builtin_popcountll(rec & w.online) == 0) {
		rec |= 1u;      /* "add boot processor or we hang" (master_processor = cpu 0) */
	}
	w.pset_rec = rec & w.registered;
}

static void
sched_perfcontrol_update_recommended_cores(uint32_t m)
{
	w.applies++;
	w.perf_req = m;
	if (w.held == 0) {
		m_update(w.perf_req);
	}
}

static void
sweet_recores_read(uint64_t *req, uint64_t *eff, uint64_t *online, uint32_t *held)
{
	*req = w.perf_req;
	*eff = w.pset_rec;
	*online = w.online;
	*held = w.held;
}

void sweet_recores_point(int late);
#define printf m_printf
#include LOGIC_SRC
#undef printf

/* patch 0270's block in AppleCLCD::start (transcribed), then the early point as patch 0344 places it */
static void
early(void)
{
	uint32_t rc = 0;

	if (PE_parse_boot_argn("sweetrecores", &rc, sizeof(rc))) {
		uint32_t cpus = w.ncpu >= 32 ? 0xffffffffu : ((1u << w.ncpu) - 1);
		if (!((rc & cpus) == 0 || (rc & ~cpus) != 0)) {
			sched_perfcontrol_update_recommended_cores(rc);
		}
		sweet_recores_point(0);
	}
}

static void
boot(unsigned int ncpu, int have, uint32_t arg)
{
	memset(&w, 0, sizeof(w));
	w.ncpu = ncpu;
	w.registered = (1ull << ncpu) - 1;
	w.pset_rec = w.registered;
	w.online = 1;
	w.perf_req = ALL_CORES_RECOMMENDED;
	w.have_arg = have;
	w.arg = arg;
	out[0] = 0;
	outn = 0;
}

static void
cpus_up(void)
{
	w.online = w.registered;        /* sched_mark_processor_online_locked: the online bit only */
}

static void
clr(void)
{
	out[0] = 0;
	outn = 0;
}

static int fails;
#define CHECK(name, cond) do { if (cond) { printf("CASE %s ok\n", name); } else { printf("CASE %s FAIL line %d out=[%s]\n", name, __LINE__, out); fails++; } } while (0)

int
main(void)
{
	/* the defect: 5 CPUs, 0x1e, early call with only cpu0 online */
	boot(5, 1, 0x1e);
	early();
	CHECK("early_1e_keeps_boot_cpu", w.pset_rec == 0x1f && w.applies == 1);
	CHECK("early_1e_text", strcmp(out,
	    "SWEET SCHED: recommended cores early: requested 0x1e effective 0x1f online 0x1 (cpus 0x1f, sweetrecores, patch 0344)\n"
	    "SWEET GAP: SCHED recommended cores early: effective & online 0x1 is not requested & online 0x0 (held 0: 1 failsafe, 2 sleep): not in effect yet (the early call runs before the secondary CPUs are online), re-applied after the CPU start\n") == 0);
	cpus_up();
	CHECK("online_bit_alone_changes_nothing", w.pset_rec == 0x1f);
	clr();
	sweet_recores_point(1);
	CHECK("late_1e_applies", w.pset_rec == 0x1e && w.applies == 2);
	CHECK("late_1e_text", strcmp(out,
	    "SWEET SCHED: recommended cores late: requested 0x1e effective 0x1e online 0x1f (cpus 0x1f, sweetrecores, patch 0344)\n") == 0);

	/* no boot-arg: early silent, late reports, nothing applied */
	boot(5, 0, 0);
	early();
	CHECK("noarg_early_silent", out[0] == 0 && w.applies == 0);
	cpus_up();
	sweet_recores_point(1);
	CHECK("noarg_late_text", w.applies == 0 && strcmp(out,
	    "SWEET SCHED: recommended cores late: requested none effective 0x1f online 0x1f (cpus 0x1f, patch 0344)\n") == 0);

	/* no boot-arg but the scheduler state is not "every online core": a GAP */
	boot(5, 0, 0);
	cpus_up();
	w.pset_rec = 0x1d;
	sweet_recores_point(1);
	CHECK("noarg_mismatch_gap", strstr(out, "SWEET GAP: SCHED recommended cores late: effective & online 0x1d is not requested & online 0x1f (held 0: 1 failsafe, 2 sleep): the mask is NOT in effect\n") != NULL);

	/* refused masks (0270's rule): bit above the CPU count, empty mask */
	boot(5, 1, 0x3e);
	early();
	CHECK("refused_early_silent_no_apply", out[0] == 0 && w.applies == 0);
	cpus_up();
	sweet_recores_point(1);
	CHECK("refused_high_bit", w.applies == 0 && w.pset_rec == 0x1f && strcmp(out,
	    "SWEET GAP: SCHED recommended cores late: sweetrecores=0x3e refused (cpus 0x1f): not applied, every core stays recommended\n"
	    "SWEET SCHED: recommended cores late: requested none effective 0x1f online 0x1f (cpus 0x1f, patch 0344)\n") == 0);
	boot(5, 1, 0);
	cpus_up();
	sweet_recores_point(1);
	CHECK("refused_empty", w.applies == 0 && strstr(out, "sweetrecores=0x0 refused") != NULL);

	/* another caller (SWEET THERMAL X1 drop 0x18) changed the request before the late point: left alone, said */
	boot(5, 1, 0x1e);
	early();
	cpus_up();
	sched_perfcontrol_update_recommended_cores(0x18);
	clr();
	sweet_recores_point(1);
	CHECK("other_caller_left_alone", w.applies == 2 && w.pset_rec == 0x18 && strcmp(out,
	    "SWEET GAP: SCHED recommended cores late: the scheduler's requested mask is 0x18, not sweetrecores 0x1e (another caller changed it after the early call): not re-applied, effective 0x18 online 0x1f\n") == 0);

	/* the early call has not run yet at the late point (matching order): the late point applies */
	boot(5, 1, 0x1e);
	cpus_up();
	sweet_recores_point(1);
	CHECK("late_first_applies", w.applies == 1 && w.pset_rec == 0x1e && strstr(out, "SWEET GAP") == NULL);
	clr();
	early();
	CHECK("early_after_late_no_gap", w.pset_rec == 0x1e && strcmp(out,
	    "SWEET SCHED: recommended cores early: requested 0x1e effective 0x1e online 0x1f (cpus 0x1f, sweetrecores, patch 0344)\n") == 0);

	/* failsafe / sleep override: the scheduler stores the request without applying it */
	boot(5, 1, 0x1e);
	early();
	cpus_up();
	w.held = 1;
	clr();
	sweet_recores_point(1);
	CHECK("held_failsafe_gap", w.pset_rec == 0x1f && strcmp(out,
	    "SWEET SCHED: recommended cores late: requested 0x1e effective 0x1f online 0x1f (cpus 0x1f, sweetrecores, patch 0344)\n"
	    "SWEET GAP: SCHED recommended cores late: effective & online 0x1f is not requested & online 0x1e (held 1: 1 failsafe, 2 sleep): the mask is NOT in effect\n") == 0);
	boot(5, 1, 0x1e);
	early();
	cpus_up();
	w.held = 2;
	clr();
	sweet_recores_point(1);
	CHECK("held_sleep_gap", strstr(out, "(held 2: 1 failsafe, 2 sleep): the mask is NOT in effect\n") != NULL);

	/* 8 CPUs, 0xff: in effect at both points, no GAP */
	boot(8, 1, 0xff);
	early();
	CHECK("ff_early_no_gap", strcmp(out, "SWEET SCHED: recommended cores early: requested 0xff effective 0xff online 0x1 (cpus 0xff, sweetrecores, patch 0344)\n") == 0);
	cpus_up();
	clr();
	sweet_recores_point(1);
	CHECK("ff_late_no_gap", strcmp(out, "SWEET SCHED: recommended cores late: requested 0xff effective 0xff online 0xff (cpus 0xff, sweetrecores, patch 0344)\n") == 0);

	/* 8 CPUs, 0x1e: logical 5-7 really idle now, and the line shows it */
	boot(8, 1, 0x1e);
	early();
	cpus_up();
	clr();
	sweet_recores_point(1);
	CHECK("c8_1e_late", w.pset_rec == 0x1e && strcmp(out, "SWEET SCHED: recommended cores late: requested 0x1e effective 0x1e online 0xff (cpus 0xff, sweetrecores, patch 0344)\n") == 0);

	/* a secondary that never came online is not part of the comparison (startAll panics before the late point in that case anyway) */
	boot(5, 1, 0x1e);
	early();
	w.online = 0x0f;
	clr();
	sweet_recores_point(1);
	CHECK("partial_online_compare", strstr(out, "online 0xf ") != NULL && strstr(out, "SWEET GAP") == NULL);

	/* only online cores are compared: a CPU the scheduler never registered (no pset bit) is not a mismatch by itself */
	boot(5, 0, 0);
	w.registered = w.pset_rec = w.online = 0x0f;
	sweet_recores_point(1);
	CHECK("unregistered_cpu_not_compared", strcmp(out, "SWEET SCHED: recommended cores late: requested none effective 0xf online 0xf (cpus 0x1f, patch 0344)\n") == 0);

	CHECK("only_sweetrecores_parsed", bad_arg_name == 0);
	printf("CASES %s fails=%d\n", fails ? "FAIL" : "ok", fails);
	return fails ? 1 : 0;
}
