/*
 * gpupower.c - opt-in Mali-G78 power cycle, stage 1 of docs/research/17 (see gpupower.h). Freestanding, no static state.
 *
 * Sequence = Google's kbase JM power manager (branch android-gs-bluejay-6.1-android16, local copy
 * ~/pixelios-artifacts/refs/kbase-bluejay/pm_driver.c; register offsets: enums.h GPU_CONTROL__*). [read from source]
 *  ON   tiler first: need_tiler_control() is true on JM (pm_driver.c:1840-1851), so TILER_PWRON = tiler_present "will also power
 *       the L2 cache" (pm_driver.c:2167-2169, kbase_pm_invoke(KBASE_PM_CORE_TILER, tiler_present, ACTION_PWRON) at 2185-2186);
 *       no separate L2_PWRON because l2_present == 1 (the extra L2 write at 2202-2204 is only for l2_present != 1).
 *       Done when L2_READY == l2_present, no L2 transition, tiler up (pm_driver.c:2212-2218, wait on l2_trans/l2_ready/tiler).
 *       Then SHADER_PWRON = the shader mask, wait SHADER_READY == mask (pm_driver.c:2659-2660 write, 2671-2672 wait). kbase would
 *       run the "dummy job" workaround instead of this write only when kbase_dummy_job_wa_enabled() (pm_driver.c:644-647); for this
 *       GPU it is not [read from source; the GPU_ID bit layout behind "0x92020010 = r0p1" is UNVERIFIED, doc 27 addendum]:
 *       GPU_ID 0x92020010 = tBEx r0p1 (hw.c:206-211), base_hw_issues_tBEx_r0p1 (issues.h:345-361) has no
 *       KBASE_HW_ISSUE_TTRX_3485 and the blob loads only for that issue (mali_kbase_dummy_job_wa.c:217-226).
 *       Plain build: CORESTACK is not driven (STACK_* never written; 6 allowlist entries only with GPU_POWER_STACK): MALI_CORESTACK is `default n` in Mconfig:168-171, but what the Pixel build sets is UNKNOWN [guess];
 *       STACK_READY/STACK_PWRTRANS (0xE10/0xE40, enums.h) are dumped read-only before the run so a stack-controlled GPU shows up.
 *  OFF  per block, as kbase's PEND states: drain PWRTRANS, skip the write if READY == 0 (kbase never writes from there), refuse
 *       a partial READY (!= present), write PWROFF = READY (the allowlist only accepts the present mask, so READY == present), wait.
 *       Shaders first: SHADER_PWROFF = shaders_ready (pm_driver.c:2846-2847), wait !shaders_trans && !shaders_ready
 *       (pm_driver.c:2855-2856); only then L2_PWROFF = l2_present, which "will also power off the tiler" on JM
 *       (pm_driver.c:2395-2412, the `#if MALI_USE_CSF` TILER_PWROFF branch is CSF + host power interface only), wait
 *       !l2_trans && !l2_ready (pm_driver.c:2438). We also check TILER_READY == 0 at the end.
 * If the shaders do not power off, L2_PWROFF is NOT written (kbase never powers the L2 down under ready shaders).
 *
 * Variant --gpu-power=stack (-DGPU_POWER_STACK, kbase corestack_driver_control=true): STACK_PWRON = stack_present (+0xE20) after the L2 is up
 * and before SHADER_PWRON (pm_driver.c:2593-2598 stacks_avail = present, 2646-2649 write, 2656-2657 wait !stacks_trans && stacks_ready == avail,
 * then SHADER_PWRON 2658-2660); OFF: shaders off (2846-2847), wait (2856), STACK_PWROFF = stacks_avail (+0xE30, 2857-2859), wait
 * !stacks_trans && !stacks_ready (2866), then the L2. Registers GPU_CONTROL STACK_PWRON/PWROFF/READY/PWRTRANS 0xE20/0xE30/0xE10/0xE40 (enums.h:727-731,
 * pm_driver.c:408-424 reg selection). Deviation: our OFF write is skipped when STACK_READY == 0 (as for the other blocks); kbase writes stacks_avail
 * from the PEND_OFF_CORESTACK_ON state. Whether the Pixel build uses it was UNKNOWN; run g2stack (observed) shows STACK_PWRON accepted with no STACK_READY/PWRTRANS change and the
 * shaders still unreachable: the stack hypothesis for the stuck SHADER_PWRTRANS is dead (docs/research/27).
 *
 * Waits: GP_POLLS reads AND 1 s of CNTPCT each (kbase's own bound is PM_TIMEOUT_MS = 5000, pm_driver.c:3244); the summary prints the
 * elapsed ticks of the ON and OFF phases and the counter frequency.
 *
 * Differences from kbase, on purpose: (a) kbase writes the 64-bit registers with kbase_reg_write64 (pm_driver.c:661), i.e. LO and HI;
 * we write the LO word only (HI would be 0 on this 20-core part; precheck requires the HI words of PRESENT/READY/PWRTRANS to be 0; for STACK_* only
 * with GPU_POWER_STACK). That a LO-only write starts a transition is OBSERVED for TILER_PWRON and SHADER_PWRON (run g2power); for STACK_PWRON it is a [guess]
 * by analogy (run g2stack: the write was accepted but STACK_READY/STACK_PWRTRANS stayed 0, so either the stacks are not driver-controlled, the
 * likelier reading, or a LO-only write does not trigger them; not distinguished).
 * (b) kbase steps deliberately NOT done, each because it only matters for real jobs or the state is required to be as ABL left it:
 *   - GPU IRQ clear/mask and JOB/MMU IRQ mask (pm_driver.c:3587-3602): we poll, nothing is unmasked, and IRQs stay masked at EL1.
 *   - soft reset / reset-wait in kbase_pm_init_hw (pm_driver.c:3763-3778, PM_NO_RESET variants exist): a reset is a GPU_COMMAND write,
 *     out of scope for the first run; instead precheck requires the post-reset-like state ABL left (GPU_STATUS 0, FAULTSTATUS 0, RAWSTAT
 *     exactly 0x100 = RESET_COMPLETED, macros.h:276) so a stale fault or a half-configured GPU is refused.
 *   - hw-issue quirk registers JM/SHADER/TILER/L2_MMU_CONFIG 0xF00-0xF0C (kbase_pm_hw_issues_apply, pm_driver.c:4171-4187): [guess]
 *     they mostly affect job/MMU behaviour (SHADER_CONFIG/TILER_CONFIG quirks are core-side, so this is not certain); not written, so a
 *     quirk-dependent run would differ from kbase; dumped read-only (PRE_*) so the values show up in the log.
 *   - L2 config override / PBHA (kbase_pm_l2_config_override + kbase_pbha_write_settings, pm_driver.c:2161-2162): L2_CONFIG (0x48)
 *     is only written when the DT gives overrides (not checked); it is dumped read-only.
 *   - coherency mode (COHERENCY_ENABLE 0x304) and protected-mode disable (review 1, pm_driver.c:~4532-4560): no jobs, no snooping
 *     needed to power cores; COHERENCY_ENABLE is dumped read-only.
 *
 * Writes: only through w32 -> w32_allowed (plain build: exactly 4 LO words; 6 with GPU_POWER_STACK; each only with its soc.json present mask), never retried.
 * Note (info): after -O2 the shipped w32 may fold the allowlist using its constant call sites (the address test for the two value-1
 * registers can disappear, only the value is checked); the audit pins every w32 call site, so do not add a variable-address caller.
 * No GPU_COMMAND, reset, MMU/job registers, IRQ unmask, PMU/CMU/SMC/ACPM write. Audited by tools/test-bluejay-boot.py.
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "gpuprobe.h"
#include "gpupower.h"
#ifdef GPU_JOB
#include "gpujob.h"
#endif

#if SOC_GS101 && defined(GPU_MALI) && defined(GPU_TILER_PRESENT)

#define GP_POLLS	100000u		/* reads per wait; bounded, the watchdogs are off */
#define GP_WAIT_S	1u		/* and seconds of CNTPCT per wait */

/* GPU_CONTROL offsets [src: kbase hw_access/regmap/mali_kbase_regmap_jm_enums.h] */
#define G_ID		(GPU_MALI + 0x000u)
#define G_IRQ_MASK	(GPU_MALI + 0x028u)
#define G_L2_CONFIG	(GPU_MALI + 0x048u)
#define G_RAWSTAT	(GPU_MALI + 0x020u)
#define G_STATUS	(GPU_MALI + 0x034u)
#define G_FAULT		(GPU_MALI + 0x03cu)
#define G_SHADER_PRES	(GPU_MALI + 0x100u)
#define G_TILER_PRES	(GPU_MALI + 0x110u)
#define G_L2_PRES	(GPU_MALI + 0x120u)
#define G_SHADER_RDY	(GPU_MALI + 0x140u)
#define G_TILER_RDY	(GPU_MALI + 0x150u)
#define G_L2_RDY	(GPU_MALI + 0x160u)
#define G_SHADER_ON	(GPU_MALI + 0x180u)
#define G_TILER_ON	(GPU_MALI + 0x190u)
#define G_SHADER_OFF	(GPU_MALI + 0x1c0u)
#define G_L2_OFF	(GPU_MALI + 0x1e0u)
#define G_SHADER_TR	(GPU_MALI + 0x200u)
#define G_TILER_TR	(GPU_MALI + 0x210u)
#define G_L2_TR		(GPU_MALI + 0x220u)
#define G_COHERENCY	(GPU_MALI + 0x304u)
#define G_JM_CFG	(GPU_MALI + 0xf00u)	/* enums.h:108-111 */
#define G_SH_CFG	(GPU_MALI + 0xf04u)
#define G_TL_CFG	(GPU_MALI + 0xf08u)
#define G_L2MMU_CFG	(GPU_MALI + 0xf0cu)
#define G_STACK_PRES	(GPU_MALI + 0xe00u)
#define G_STACK_RDY	(GPU_MALI + 0xe10u)
#define G_STACK_ON	(GPU_MALI + 0xe20u)	/* enums.h:728-729; written only with GPU_POWER_STACK */
#define G_STACK_OFF	(GPU_MALI + 0xe30u)
#define G_STACK_TR	(GPU_MALI + 0xe40u)

static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++) {
		unsigned d = (unsigned)((v >> (4 * (digits - 1 - i))) & 0xf);

		dst[pos++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
	}
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("GPU", buf);
}

static void say(const char *a, const char *b, const char *c, const char *d)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, a);

	p = put_txt(line, sizeof(line), p, b);
	p = put_txt(line, sizeof(line), p, c);
	p = put_txt(line, sizeof(line), p, d);
	emit(line, p);
}

static int r32(uintptr_t pa, uint32_t *v)
{
	return try_read32(pa, v);
}

/* The allowlist: the four LO words of SHADER_PWRON / TILER_PWRON / SHADER_PWROFF / L2_PWROFF, each only with its present mask. */
static int w32_allowed(uintptr_t pa, uint32_t v)
{
	if (pa == G_SHADER_ON || pa == G_SHADER_OFF)
		return v == GPU_SHADER_PRESENT;
	if (pa == G_TILER_ON)
		return v == GPU_TILER_PRESENT;
	if (pa == G_L2_OFF)
		return v == GPU_L2_PRESENT;
#ifdef GPU_POWER_STACK
	if (pa == G_STACK_ON || pa == G_STACK_OFF)
		return v == GPU_STACK_PRESENT;
#endif
	return 0;
}

/* noinline keeps the one try_write32 call site inside this symbol (the object audit checks it). */
__attribute__((noinline)) static int w32(uintptr_t pa, uint32_t v)
{
	if (!w32_allowed(pa, v))
		return -2;
	return try_write32(pa, v) ? 0 : -1;
}

/* "<prefix><name> pa=XXXXXXXXX v=XXXXXXXX" (same style as gpuprobe) */
static void dump(const char *pre, const char *name, uintptr_t pa)
{
	char line[RAMLOG_LINE_MAX];
	uint32_t v;
	int ok = r32(pa, &v);
	unsigned p = put_txt(line, sizeof(line), 0, pre);

	p = put_txt(line, sizeof(line), p, name);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, pa, 9);
	if (ok) {
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, v, 8);
	} else {
		p = put_txt(line, sizeof(line), p, " unreadable");
	}
	emit(line, p);
}

static void dump_all(const char *pre)
{
	dump(pre, "SHADER_READY", G_SHADER_RDY);
	dump(pre, "TILER_READY", G_TILER_RDY);
	dump(pre, "L2_READY", G_L2_RDY);
	dump(pre, "SHADER_PWRTRANS", G_SHADER_TR);
	dump(pre, "TILER_PWRTRANS", G_TILER_TR);
	dump(pre, "L2_PWRTRANS", G_L2_TR);
	dump(pre, "GPU_IRQ_RAWSTAT", G_RAWSTAT);
	dump(pre, "GPU_FAULTSTATUS", G_FAULT);
	dump(pre, "GPU_STATUS", G_STATUS);
	dump(pre, "STACK_READY", G_STACK_RDY);
	dump(pre, "STACK_PWRTRANS", G_STACK_TR);
}

/* ready == want and no transition pending; an unreadable word counts as not settled. */
static int settled(uintptr_t rdy, uint32_t want, uintptr_t trans)
{
	uint32_t r, t;

	return r32(rdy, &r) && r32(trans, &t) && r == want && t == 0;
}

static int wait_for(uintptr_t rdy, uint32_t want, uintptr_t trans)
{
	uint64_t t0 = touch_ticks(), lim = counter_freq() * GP_WAIT_S;

	for (unsigned i = 0; i < GP_POLLS; i++) {
		if (settled(rdy, want, trans))
			return 1;
		if (touch_ticks() - t0 > lim)
			return 0;
	}
	return 0;
}

/* no transition pending */
static int wait_idle(uintptr_t trans)
{
	uint64_t t0 = touch_ticks(), lim = counter_freq() * GP_WAIT_S;
	uint32_t t;

	for (unsigned i = 0; i < GP_POLLS; i++) {
		if (r32(trans, &t) && t == 0)
			return 1;
		if (touch_ticks() - t0 > lim)
			return 0;
	}
	return 0;
}

/* Read-only. NULL = all preconditions hold, else the reason (nothing has been written). */
static const char *precheck(void)
{
	uint32_t top = 0, emb = 0, mt = 0, mc = 0, ms = 0, v, w;
	int top_ok = r32(GPU_PMU_G3D_STAT, &top);
	int emb_ok = r32(GPU_PMU_EMB_STAT, &emb);
	int mux_ok = 0;
	enum gpu_gate g;

	if (!counter_freq())
		return "no-timer";	/* no clock = no time-bounded wait */
	if (top_ok && (top & 1u)) {	/* CMU_G3D is inside the top domain: read it only when that is on (as gpu_probe) */
		int a = r32(GPU_CMU_G3D + 0x620u, &mt);
		int b = r32(GPU_CMU_G3D + 0x600u, &mc);
		int c = r32(GPU_CMU_G3D + 0x610u, &ms);

		mux_ok = a && b && c;
	}
	g = gpu_gate(top_ok, top, emb_ok, emb, mux_ok, mt, mc, ms);
	if (g != GPU_GATE_OPEN)
		return gpu_gate_name(g);
	if (!r32(G_ID, &v))
		return "id-unreadable";
	if (v != GPU_ID_EXPECT)
		return "id-mismatch";
	if (!r32(G_SHADER_PRES, &v) || !r32(G_TILER_PRES, &mt) || !r32(G_L2_PRES, &mc))
		return "present-unreadable";
	if (v != GPU_SHADER_PRESENT || mt != GPU_TILER_PRESENT || mc != GPU_L2_PRESENT)
		return "present-mismatch";
	for (unsigned i = 0; i < 3; i++) {	/* PRESENT HI words 0x104/114/124 */
		if (!r32(GPU_MALI + 0x104u + 0x10u * i, &v))
			return "present-unreadable";
		if (v)
			return "present-hi-nonzero";
	}
	for (unsigned i = 0; i < 3; i++) {	/* READY 0x140/150/160 and PWRTRANS 0x200/210/220, LO then HI word */
		for (unsigned h = 0; h < 2; h++) {
			if (!r32(GPU_MALI + 0x140u + 0x10u * i + 4u * h, &v) || !r32(GPU_MALI + 0x200u + 0x10u * i + 4u * h, &w))
				return "core-state-unreadable";
			if (v || w)
				return "cores-not-off";
		}
	}
	if (!r32(G_STACK_TR, &v))
		return "stack-unreadable";
	if (v)
		return "stack-busy";
#ifdef GPU_POWER_STACK
	if (!r32(G_STACK_PRES, &v) || !r32(G_STACK_PRES + 4u, &w) || !r32(G_STACK_RDY, &mt))
		return "stack-unreadable";
	if (v != GPU_STACK_PRESENT || w)
		return "stack-present-mismatch";
	if (mt)
		return "stack-not-off";
	if (!r32(G_STACK_RDY + 4u, &v) || !r32(G_STACK_TR + 4u, &w))	/* HI words 0xE14 / 0xE44, as for the other blocks */
		return "stack-unreadable";
	if (v || w)
		return "stack-not-off";
#endif
	if (!r32(G_STATUS, &v) || !r32(G_FAULT, &w) || !r32(G_RAWSTAT, &mt))
		return "gpu-state-unreadable";
	if (v)
		return "gpu-status-nonzero";
	if (w)
		return "gpu-fault";
	if (mt != 0x100u)	/* observed in G0: only RESET_COMPLETED (bit 8, macros.h:276) */
		return "rawstat-unexpected";
	return 0;
}

/* One block's power-down in kbase order. 0 = off (or already off, nothing written), 1 = timeout, 2 = READY partial (nothing written),
 * 3 = write fault, 4 = write refused by the allowlist. */
static int power_off(uintptr_t reg, uintptr_t rdy, uintptr_t trans, uint32_t present)
{
	uint32_t r;
	int e;

	if (!wait_idle(trans) || !r32(rdy, &r))
		return 1;
	if (r == 0)
		return 0;	/* kbase never writes PWROFF from here */
	if (r != present)
		return 2;
	e = w32(reg, r);	/* READY mask as kbase; equal to the present mask here */
	if (e)
		return e == -2 ? 4 : 3;
	return wait_for(rdy, 0, trans) ? 0 : 1;
}

static const char *dname(int code, unsigned blk)
{
	switch (code) {
	case 1: return blk == 0 ? "timeout:shader" : blk == 1 ? "timeout:l2" : blk == 2 ? "timeout:tiler" : "timeout:stack";
	case 2: return blk == 0 ? "partial:shader" : blk == 3 ? "partial:stack" : "partial:l2";
	case 3: return blk == 0 ? "fault:shader" : blk == 3 ? "fault:stack" : "fault:l2";
	case 4: return blk == 0 ? "refused:shader" : blk == 3 ? "refused:stack" : "refused:l2";
	}
	return "ok";
}

static const char *uname(int e, const char *refused, const char *fault)
{
	return e == -2 ? refused : fault;
}

int gpu_power_cycle(void)
{
	const char *up = "ok", *down, *why = precheck();
	char line[RAMLOG_LINE_MAX];
	uint64_t t0, t1, t2, tj = 0;
	unsigned p, blk = 0;
	int e, d;

	if (why) {
		say("POWER refused: ", why, "", "");
		return 1;
	}
	/* read-only pre-state a stack/quirk/coherency difference to kbase would show in */
	dump("PRE_", "STACK_READY", G_STACK_RDY);
	dump("PRE_", "STACK_PWRTRANS", G_STACK_TR);
	dump("PRE_", "L2_CONFIG", G_L2_CONFIG);
	dump("PRE_", "COHERENCY_ENABLE", G_COHERENCY);
	dump("PRE_", "GPU_IRQ_MASK", G_IRQ_MASK);
	dump("PRE_", "JM_CONFIG", G_JM_CFG);
	dump("PRE_", "SHADER_CONFIG", G_SH_CFG);
	dump("PRE_", "TILER_CONFIG", G_TL_CFG);
	dump("PRE_", "L2_MMU_CONFIG", G_L2MMU_CFG);
	t0 = touch_ticks();
	/* ON (never retried; a failure goes straight to the OFF sequence) */
	if ((e = w32(G_TILER_ON, GPU_TILER_PRESENT)))
		up = uname(e, "refused:tiler", "fault:tiler");
	else if (!wait_for(G_TILER_RDY, GPU_TILER_PRESENT, G_TILER_TR))
		up = "timeout:tiler";
	else if (!wait_for(G_L2_RDY, GPU_L2_PRESENT, G_L2_TR))
		up = "timeout:l2";
#ifdef GPU_POWER_STACK
	else if ((e = w32(G_STACK_ON, GPU_STACK_PRESENT)))	/* corestack_driver_control path: pm_driver.c:2646-2649, wait 2656-2657 */
		up = uname(e, "refused:stack", "fault:stack");
	else if (!wait_for(G_STACK_RDY, GPU_STACK_PRESENT, G_STACK_TR))
		up = "timeout:stack";
#endif
	else if ((e = w32(G_SHADER_ON, GPU_SHADER_PRESENT)))
		up = uname(e, "refused:shader", "fault:shader");
	else if (!wait_for(G_SHADER_RDY, GPU_SHADER_PRESENT, G_SHADER_TR))
		up = "timeout:shader";
	t1 = touch_ticks();
	dump_all("");
#ifdef GPU_JOB
	/* --gpu-job (docs/research/28): the one WRITE_VALUE job runs INSIDE the powered window, only if the shaders came up; the OFF sequence below follows in every case */
	tj = touch_ticks();
	if (up[0] == 'o')
		(void)gpu_job_run();
	tj = touch_ticks() - tj;
#endif
	/* OFF, always: shaders, then the L2 (which takes the tiler with it); L2 stays on if the shaders did not go off */
	d = power_off(G_SHADER_OFF, G_SHADER_RDY, G_SHADER_TR, GPU_SHADER_PRESENT);
#ifdef GPU_POWER_STACK
	if (!d) {	/* corestack path: stacks off after the shaders, before the L2 (pm_driver.c:2855-2859, wait 2866) */
		blk = 3;
		d = power_off(G_STACK_OFF, G_STACK_RDY, G_STACK_TR, GPU_STACK_PRESENT);
	}
#endif
	if (!d) {
		blk = 2;
		if (wait_idle(G_TILER_TR)) {	/* kbase reaches L2 power-down only with the tiler settled (pm_driver.c:2212-2218) */
			blk = 1;
			d = power_off(G_L2_OFF, G_L2_RDY, G_L2_TR, GPU_L2_PRESENT);
		} else {
			d = 1;
		}
	}
	if (!d && !wait_for(G_TILER_RDY, 0, G_TILER_TR)) {
		blk = 2;
		d = 1;
	}
	down = dname(d, blk);
	t2 = touch_ticks();
	dump_all("OFF_");
	p = put_txt(line, sizeof(line), 0, "POWER up=");
	p = put_txt(line, sizeof(line), p, up);
	p = put_txt(line, sizeof(line), p, " down=");
	p = put_txt(line, sizeof(line), p, down);
	p = put_txt(line, sizeof(line), p, " ticks=");
	p = put_hex(line, sizeof(line), p, t1 - t0, 8);
	p = put_txt(line, sizeof(line), p, "/");
	p = put_hex(line, sizeof(line), p, t2 - t1, 8);
	p = put_txt(line, sizeof(line), p, " job=");	/* ticks inside gpu_job_run: the OFF ticks above include it plus dump_all (G-4 item b) */
	p = put_hex(line, sizeof(line), p, tj, 8);
	p = put_txt(line, sizeof(line), p, " freq=");
	p = put_hex(line, sizeof(line), p, counter_freq(), 8);
	emit(line, p);
	return (up[0] == 'o' && down[0] == 'o') ? 0 : 1;
}

#else	/* not gs101 (or no GPU constants): empty stub */

int gpu_power_cycle(void) { return 1; }

#endif
