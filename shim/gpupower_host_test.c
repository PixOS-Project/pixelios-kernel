/* Host test for gpupower.c against a FAKE Mali-G78 + PMU/CMU: the exact write sequence, every precondition refused with ZERO
 * writes, the timeouts (the OFF sequence still runs), the OFF timeouts, write faults, and that w32_allowed accepts exactly the four
 * sanctioned (register, value) pairs. #includes the .c so the statics are reachable and uses the REAL generated board macros.
 * Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpupower_host_test gpupower_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "gpupower.c"

#if !(SOC_GS101 && defined(GPU_TILER_PRESENT))
#error "needs the bluejay board_config.h (shim/build.sh --board bluejay writes out/board_config.h)"
#endif

#define SH	GPU_SHADER_PRESENT
#define TL	GPU_TILER_PRESENT
#define L2	GPU_L2_PRESENT

/* ---- fake hardware ---- */
static uint32_t top_stat, emb_stat, mux, gid, shp, tlp, l2p;
static uint32_t rdy[3], trans[3];		/* shader, tiler, l2 */
static uintptr_t unreadable, fail_wpa;		/* reads of / writes to this address fault */
static int stuck_tiler_on, stuck_l2_on, stuck_sh_on, stuck_sh_off, stuck_l2_off, stuck_tl_off;
static int sh_partial, sh_trans_on, tl_trans_on;
static uint32_t stk_rdy, stk_trans, stk_pres;	/* fake core stack (STACK_*) */
static int stuck_stk_on, stuck_stk_off, stk_trans_on;		/* SHADER_PWRON brings up all but core 0 / leaves a transition pending forever */
static uint32_t gstat, gfault, graw;
static uintptr_t force_pa;			/* a read of this address returns force_v (HI words, STACK_*, ...) */
static uint32_t force_v;
static uint64_t fake_freq, now, tick_step;
static unsigned long nreads;
static struct { uintptr_t pa; uint32_t v; } wlog[64];
static unsigned nw;
static char lines[256][RAMLOG_LINE_MAX + 8];
static unsigned nlines;

void con_str(const char *label, const char *s)
{
	assert(nlines < 256);
	snprintf(lines[nlines++], sizeof(lines[0]), "%s %s", label, s);
}

uint64_t counter_freq(void) { return fake_freq; }
uint64_t touch_ticks(void) { return now += tick_step; }

int try_read32(uintptr_t pa, uint32_t *v)
{
	nreads++;
	if (pa == unreadable)
		return 0;
	if (pa == force_pa) {
		*v = force_v;
		return 1;
	}
	if (pa == GPU_PMU_G3D_STAT) *v = top_stat;
	else if (pa == GPU_PMU_EMB_STAT) *v = emb_stat;
	else if (pa == GPU_CMU_G3D + 0x600 || pa == GPU_CMU_G3D + 0x610 || pa == GPU_CMU_G3D + 0x620) *v = mux;
	else if (pa == G_ID) *v = gid;
	else if (pa == G_SHADER_PRES) *v = shp;
	else if (pa == G_TILER_PRES) *v = tlp;
	else if (pa == G_L2_PRES) *v = l2p;
	else if (pa == G_SHADER_RDY) *v = rdy[0];
	else if (pa == G_TILER_RDY) *v = rdy[1];
	else if (pa == G_L2_RDY) *v = rdy[2];
	else if (pa == G_SHADER_TR) *v = trans[0];
	else if (pa == G_TILER_TR) *v = trans[1];
	else if (pa == G_L2_TR) *v = trans[2];
	else if (pa == G_STACK_RDY) *v = stk_rdy;
	else if (pa == G_STACK_TR) *v = stk_trans;
	else if (pa == G_STACK_PRES) *v = stk_pres;
	else if (pa == G_RAWSTAT) *v = graw;
	else if (pa == G_STATUS) *v = gstat;
	else if (pa == G_FAULT) *v = gfault;
	else *v = 0;
	return 1;
}

int try_write32(uintptr_t pa, uint32_t v)
{
	assert(nw < 64);
	wlog[nw].pa = pa;
	wlog[nw++].v = v;
	if (pa == fail_wpa)
		return 0;
	if (pa == G_TILER_ON) {
		if (!stuck_tiler_on) rdy[1] = v;
		if (tl_trans_on) trans[1] = 1;
		if (!stuck_tiler_on && !stuck_l2_on) rdy[2] = l2p;	/* the tiler takes the L2 up with it */
	} else if (pa == G_STACK_ON) {
		if (!stuck_stk_on) stk_rdy = v;
		if (stk_trans_on) stk_trans = 1;
	} else if (pa == G_STACK_OFF) {
		if (!stuck_stk_off) stk_rdy = 0;
	} else if (pa == G_SHADER_ON) {
		if (!stuck_sh_on) rdy[0] = sh_partial ? (v & ~1u) : v;
		if (sh_trans_on) trans[0] = 1;
	} else if (pa == G_SHADER_OFF) {
		if (!stuck_sh_off) rdy[0] = 0;
	} else if (pa == G_L2_OFF) {
		if (!stuck_l2_off) rdy[2] = 0;
		if (!stuck_tl_off) rdy[1] = 0;			/* L2 off takes the tiler with it (JM) */
	}
	return 1;
}

static void reset(void)
{
	top_stat = emb_stat = 1;
	mux = 0x10;
	gid = GPU_ID_EXPECT; shp = SH; tlp = TL; l2p = L2;
	memset(rdy, 0, sizeof(rdy)); memset(trans, 0, sizeof(trans));
	unreadable = fail_wpa = 0;
	stuck_tiler_on = stuck_l2_on = stuck_sh_on = stuck_sh_off = stuck_l2_off = stuck_tl_off = 0;
	sh_partial = sh_trans_on = tl_trans_on = 0;
	stk_rdy = stk_trans = 0; stk_pres = GPU_STACK_PRESENT; stuck_stk_on = stuck_stk_off = stk_trans_on = 0;
	gstat = gfault = 0; graw = 0x100;
	force_pa = 0; force_v = 0;
	fake_freq = 24000000u; tick_step = 200; now = 0; nreads = 0;
	nw = nlines = 0;
}

static int has_line(const char *s)
{
	for (unsigned i = 0; i < nlines; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static void wexp(unsigned i, uintptr_t pa, uint32_t v)
{
	assert(i < nw && wlog[i].pa == pa && wlog[i].v == v);
}

static void refused(const char *what, const char *reason)
{
	int r = gpu_power_cycle();

	if (r != 1 || nw != 0 || !has_line("POWER refused: ") || (reason && !has_line(reason)) || has_line("POWER up=")) {
		fprintf(stderr, "FAIL precondition '%s': ret=%d writes=%u\n", what, r, nw);
		for (unsigned i = 0; i < nlines; i++)
			fprintf(stderr, "  %s\n", lines[i]);
		assert(0);
	}
}

static int run(const char *summary, unsigned writes)
{
	int r = gpu_power_cycle();

	if (!has_line(summary) || nw != writes) {
		fprintf(stderr, "FAIL expected '%s' with %u writes, got %u writes\n", summary, writes, nw);
		for (unsigned i = 0; i < nlines; i++)
			fprintf(stderr, "  %s\n", lines[i]);
		assert(0);
	}
	for (unsigned i = 0; i < nw; i++) {		/* nothing outside the allowlist, nothing twice (never retried) */
		assert(w32_allowed(wlog[i].pa, wlog[i].v));
		for (unsigned j = 0; j < i; j++)
			assert(wlog[j].pa != wlog[i].pa);
	}
	return r;
}

int main(void)
{
#ifndef GPU_POWER_STACK
	/* happy path: exactly 2 ON + 2 OFF writes, in kbase order; ends with everything off */
	reset();
	assert(run("GPU POWER up=ok down=ok", 4) == 0);
	wexp(0, G_TILER_ON, TL); wexp(1, G_SHADER_ON, SH); wexp(2, G_SHADER_OFF, SH); wexp(3, G_L2_OFF, L2);
	assert(!rdy[0] && !rdy[1] && !rdy[2]);
	assert(has_line("GPU SHADER_READY pa=01C500140 v=00779FFF") && has_line("GPU L2_READY pa=01C500160 v=00000001"));
	assert(has_line("GPU OFF_SHADER_READY pa=01C500140 v=00000000") && has_line("GPU OFF_GPU_STATUS pa="));
	assert(has_line("GPU GPU_IRQ_RAWSTAT pa=01C500020 v=00000100") && has_line("GPU GPU_FAULTSTATUS pa="));

	/* every precondition on its own: refused, zero writes */
	reset(); top_stat = 0; refused("pd_g3d off", "pd_g3d-off");
	reset(); emb_stat = 0; refused("pd_embedded off", "pd_embedded_g3d-off");
	reset(); mux = 0; refused("mux off", "cmu-mux-off");
	reset(); unreadable = GPU_PMU_G3D_STAT; refused("pmu unreadable", "pmu-unreadable");
	reset(); unreadable = GPU_CMU_G3D + 0x610; refused("cmu unreadable", "cmu-unreadable");
	reset(); gid ^= 1; refused("id mismatch", "id-mismatch");
	reset(); unreadable = G_ID; refused("id unreadable", "id-unreadable");
	reset(); shp = 0xFFFFF; refused("shader present", "present-mismatch");
	reset(); tlp = 3; refused("tiler present", "present-mismatch");
	reset(); l2p = 3; refused("l2 present", "present-mismatch");
	reset(); unreadable = G_L2_PRES; refused("present unreadable", "present-unreadable");
	for (unsigned i = 0; i < 3; i++) {
		reset(); rdy[i] = 1; refused("ready nonzero", "cores-not-off");
		reset(); trans[i] = 1; refused("pwrtrans nonzero", "cores-not-off");
	}
	reset(); unreadable = G_TILER_TR; refused("pwrtrans unreadable", "core-state-unreadable");

	/* more preconditions: GPU state words, HI words, timer */
	reset(); gstat = 1; refused("gpu status", "gpu-status-nonzero");
	reset(); gfault = 1; refused("gpu fault", "gpu-fault");
	reset(); graw = 0; refused("rawstat 0", "rawstat-unexpected");
	reset(); graw = 0x300; refused("rawstat power-changed", "rawstat-unexpected");
	reset(); unreadable = G_RAWSTAT; refused("rawstat unreadable", "gpu-state-unreadable");
	reset(); fake_freq = 0; refused("no timer", "no-timer");
	{
		static const uint32_t his[] = { 0x104, 0x114, 0x124, 0x144, 0x154, 0x164, 0x204, 0x214, 0x224 };
		for (unsigned i = 0; i < sizeof(his) / sizeof(his[0]); i++) {
			reset(); force_pa = GPU_MALI + his[i]; force_v = 1;
			refused("HI word nonzero", his[i] < 0x130 ? "present-hi-nonzero" : "cores-not-off");
		}
	}

	/* the pre-state dump is read-only and printed before any write */
	reset(); force_pa = G_COHERENCY; force_v = 0x7f;
	assert(run("GPU POWER up=ok down=ok", 4) == 0);
	assert(has_line("GPU PRE_COHERENCY_ENABLE pa=01C500304 v=0000007F") && has_line("GPU PRE_STACK_READY pa=01C500E10 v=00000000") && has_line("GPU OFF_STACK_PWRTRANS pa=01C500E40") && has_line("GPU PRE_STACK_PWRTRANS pa=01C500E40") &&
	       has_line("GPU PRE_L2_CONFIG pa=01C500048") && has_line("GPU PRE_COHERENCY_ENABLE pa=01C500304") &&
	       has_line("GPU PRE_GPU_IRQ_MASK pa=01C500028"));
	assert(has_line(" ticks=") && has_line(" job=00000000") && has_line(" freq=016E3600"));

	/* ON failures: PWROFF is written only where READY is nonzero */
	reset(); stuck_tiler_on = 1;
	assert(run("GPU POWER up=timeout:tiler down=ok", 1) == 1);	/* nothing came on, so nothing is switched off */
	wexp(0, G_TILER_ON, TL);
	reset(); stuck_l2_on = 1;
	assert(run("GPU POWER up=timeout:l2 down=timeout:tiler", 1) == 1);	/* tiler on without the L2: reported, not "fixed" */
	reset(); stuck_sh_on = 1;
	assert(run("GPU POWER up=timeout:shader down=ok", 3) == 1);
	wexp(1, G_SHADER_ON, SH); wexp(2, G_L2_OFF, L2);
	reset(); fail_wpa = G_TILER_ON;
	assert(run("GPU POWER up=fault:tiler down=ok", 1) == 1);
	reset(); fail_wpa = G_SHADER_ON;
	assert(run("GPU POWER up=fault:shader down=ok", 3) == 1);
	wexp(2, G_L2_OFF, L2);
	reset(); sh_partial = 1;					/* partial READY: the OFF write is refused, nothing else is powered down */
	assert(run("GPU POWER up=timeout:shader down=partial:shader", 2) == 1);
	assert(rdy[0] == (SH & ~1u) && rdy[2] == L2);
	reset(); sh_trans_on = 1;					/* PWRTRANS never drains: no OFF write into a transition */
	assert(run("GPU POWER up=timeout:shader down=timeout:shader", 2) == 1);
	wexp(1, G_SHADER_ON, SH);

	/* N1: tiler PWRTRANS stuck while the L2 is up: L2_PWROFF is NOT written into the transition */
	reset(); tl_trans_on = 1;
	assert(run("GPU POWER up=timeout:tiler down=timeout:tiler", 1) == 1);
	wexp(0, G_TILER_ON, TL);
	assert(rdy[2] == L2);
	/* N3: a busy core stack refuses, zero writes */
	reset(); force_pa = G_STACK_TR; force_v = 1; refused("stack busy", "stack-busy");
	reset(); unreadable = G_STACK_TR; refused("stack unreadable", "stack-unreadable");
	reset(); force_pa = G_JM_CFG; force_v = 0x1234;
	assert(run("GPU POWER up=ok down=ok", 4) == 0 && has_line("GPU PRE_JM_CONFIG pa=01C500F00 v=00001234") && has_line("GPU PRE_L2_MMU_CONFIG pa=01C500F0C"));

	/* OFF failures are reported; no L2 power-down under ready shaders */
	reset(); stuck_sh_off = 1;
	assert(run("GPU POWER up=ok down=timeout:shader", 3) == 1);
	assert(wlog[2].pa == G_SHADER_OFF && wlog[2].v == SH && rdy[2] == L2);
	reset(); stuck_l2_off = 1;
	assert(run("GPU POWER up=ok down=timeout:l2", 4) == 1);
	reset(); stuck_tl_off = 1;
	assert(run("GPU POWER up=ok down=timeout:tiler", 4) == 1);
	reset(); fail_wpa = G_L2_OFF;
	assert(run("GPU POWER up=ok down=fault:l2", 4) == 1);
	reset(); fail_wpa = G_SHADER_OFF;
	assert(run("GPU POWER up=ok down=fault:shader", 3) == 1);

	/* a pair the allowlist refuses is reported as such, never written, never a "fault" */
	reset();
	assert(w32(G_L2_RDY, 1) == -2 && w32(G_SHADER_OFF, 0x1234) == -2 && nw == 0);
	assert(!strcmp(uname(-2, "refused:x", "fault:x"), "refused:x") && !strcmp(uname(-1, "refused:x", "fault:x"), "fault:x"));
	assert(!strcmp(dname(4, 0), "refused:shader") && !strcmp(dname(4, 1), "refused:l2"));

	/* waits are bounded by time too: one second per tick call, a stuck tiler returns after a handful of reads, not 200000 */
	reset(); stuck_tiler_on = 1; tick_step = 24000000u;
	assert(run("GPU POWER up=timeout:tiler down=ok", 1) == 1);
	assert(nreads < 500);
	reset(); stuck_tiler_on = 1;				/* and by polls when time stands still */
	tick_step = 0;
	assert(run("GPU POWER up=timeout:tiler down=ok", 1) == 1);
	assert(nreads > 100000 && nreads < 2000000);

	/* the allowlist: exactly four (register, value) pairs across the whole GPU window and the neighbouring blocks */
	{
		static const uint32_t vals[] = { 0, 1, 2, 3, 0xFFFFF, 0x779FFF, 0x77A000, 0x7FFFFF, 0xFFFFFFFFu };
		unsigned ok = 0;

		for (uintptr_t pa = GPU_MALI - 0x1000; pa < GPU_MALI + 0x5000; pa += 4)
			for (unsigned i = 0; i < sizeof(vals) / sizeof(vals[0]); i++)
				if (w32_allowed(pa, vals[i])) {
					ok++;
					assert((pa == G_SHADER_ON || pa == G_SHADER_OFF) ? vals[i] == SH : pa == G_TILER_ON ? vals[i] == TL : pa == G_L2_OFF && vals[i] == L2);
				}
		assert(ok == 4);
		assert(!w32_allowed(GPU_MALI + 0x1a0u, 1) && !w32_allowed(GPU_MALI + 0x1d0u, 1));	/* no L2_PWRON, no TILER_PWROFF */
		assert(!w32_allowed(GPU_MALI + 0x184u, SH) && !w32_allowed(GPU_MALI + 0x030u, 1));	/* no HI words, no GPU_COMMAND */
		assert(!w32_allowed(GPU_PMU_G3D_CFG, 1) && !w32_allowed(GPU_CMU_G3D + 0x620u, 0x10));
	}
	/* the plain build must not know the core-stack registers: refused, never written */
	reset();
	assert(!w32_allowed(G_STACK_ON, GPU_STACK_PRESENT) && !w32_allowed(G_STACK_OFF, GPU_STACK_PRESENT));
	assert(w32(G_STACK_ON, GPU_STACK_PRESENT) == -2 && w32(G_STACK_OFF, GPU_STACK_PRESENT) == -2 && nw == 0);
#else
	/* --gpu-power=stack: kbase corestack_driver_control path. Writes: TILER_ON, STACK_ON, SHADER_ON, SHADER_OFF, STACK_OFF, L2_OFF */
	#define SK GPU_STACK_PRESENT
	reset();
	assert(run("GPU POWER up=ok down=ok", 6) == 0);
	wexp(0, G_TILER_ON, TL); wexp(1, G_STACK_ON, SK); wexp(2, G_SHADER_ON, SH); wexp(3, G_SHADER_OFF, SH); wexp(4, G_STACK_OFF, SK); wexp(5, G_L2_OFF, L2);
	assert(!rdy[0] && !rdy[1] && !rdy[2] && !stk_rdy);
	assert(has_line("GPU STACK_READY pa=01C500E10 v=0000007F") && has_line("GPU OFF_STACK_READY pa=01C500E10 v=00000000") && has_line("GPU OFF_STACK_PWRTRANS pa=01C500E40"));

	/* preconditions of the variant: zero writes */
	reset(); stk_pres = 0x3f; refused("stack present", "stack-present-mismatch");
	reset(); force_pa = G_STACK_PRES + 4; force_v = 1; refused("stack present hi", "stack-present-mismatch");
	reset(); unreadable = G_STACK_PRES; refused("stack present unreadable", "stack-unreadable");
	reset(); stk_rdy = 1; refused("stack not off", "stack-not-off");
	reset(); stk_trans = 1; refused("stack busy", "stack-busy");
	reset(); gid ^= 1; refused("id", "id-mismatch");

	/* S1: a tiler / L2 power-up failure must not continue to STACK_PWRON / SHADER_PWRON */
	reset(); stuck_tiler_on = 1;
	assert(run("GPU POWER up=timeout:tiler down=ok", 1) == 1);
	wexp(0, G_TILER_ON, TL);
	reset(); stuck_l2_on = 1;
	assert(run("GPU POWER up=timeout:l2 down=timeout:tiler", 1) == 1);
	reset(); fail_wpa = G_TILER_ON;
	assert(run("GPU POWER up=fault:tiler down=ok", 1) == 1);
	reset(); force_pa = G_STACK_RDY + 4; force_v = 1; refused("stack ready hi", "stack-not-off");
	reset(); force_pa = G_STACK_TR + 4; force_v = 1; refused("stack pwrtrans hi", "stack-not-off");
	reset(); unreadable = G_STACK_TR + 4; refused("stack pwrtrans hi unreadable", "stack-unreadable");

	/* the stack comes up between the L2 and the shaders; its failures still run the OFF sequence */
	reset(); stuck_stk_on = 1;
	assert(run("GPU POWER up=timeout:stack down=ok", 3) == 1);	/* nothing else came on: only the L2 is switched off */
	wexp(0, G_TILER_ON, TL); wexp(1, G_STACK_ON, SK); wexp(2, G_L2_OFF, L2);
	reset(); fail_wpa = G_STACK_ON;
	assert(run("GPU POWER up=fault:stack down=ok", 3) == 1);
	reset(); stk_trans_on = 1;					/* STACK_PWRTRANS never drains: no STACK_PWROFF, no L2_PWROFF into it */
	assert(run("GPU POWER up=timeout:stack down=timeout:stack", 2) == 1);
	wexp(1, G_STACK_ON, SK);
	assert(rdy[2] == L2);
	reset(); stuck_sh_on = 1;					/* shaders never ready: stacks are still powered off, in order */
	assert(run("GPU POWER up=timeout:shader down=ok", 5) == 1);
	wexp(2, G_SHADER_ON, SH); wexp(3, G_STACK_OFF, SK); wexp(4, G_L2_OFF, L2);

	/* OFF ordering and failures: shaders, then stacks, then the L2; nothing later is written after a failure */
	reset(); stuck_sh_off = 1;
	assert(run("GPU POWER up=ok down=timeout:shader", 4) == 1);	/* no STACK_PWROFF under ready shaders, no L2_PWROFF */
	assert(stk_rdy == SK && rdy[2] == L2);
	reset(); stuck_stk_off = 1;
	assert(run("GPU POWER up=ok down=timeout:stack", 5) == 1);	/* L2 stays on */
	assert(rdy[2] == L2);
	reset(); fail_wpa = G_STACK_OFF;
	assert(run("GPU POWER up=ok down=fault:stack", 5) == 1);
	reset(); stuck_l2_off = 1;
	assert(run("GPU POWER up=ok down=timeout:l2", 6) == 1);
	assert(!strcmp(dname(4, 3), "refused:stack") && !strcmp(dname(1, 3), "timeout:stack") && !strcmp(dname(2, 3), "partial:stack") && !strcmp(dname(3, 3), "fault:stack"));
	reset(); assert(w32(G_STACK_ON, 0x3f) == -2 && w32(G_STACK_OFF, 0) == -2 && nw == 0);	/* only the stack-present mask */

	/* the allowlist: exactly six (register, value) pairs, the two new ones only with the stack-present mask */
	{
		static const uint32_t vals[] = { 0, 1, 2, 3, 0x3f, 0x7f, 0xff, 0xFFFFF, 0x779FFF, 0xFFFFFFFFu };
		unsigned ok = 0;

		for (uintptr_t pa = GPU_MALI - 0x1000; pa < GPU_MALI + 0x5000; pa += 4)
			for (unsigned i = 0; i < sizeof(vals) / sizeof(vals[0]); i++)
				if (w32_allowed(pa, vals[i])) {
					ok++;
					assert((pa == G_SHADER_ON || pa == G_SHADER_OFF) ? vals[i] == SH : pa == G_TILER_ON ? vals[i] == TL :
					       (pa == G_STACK_ON || pa == G_STACK_OFF) ? vals[i] == SK : pa == G_L2_OFF && vals[i] == L2);
				}
		assert(ok == 6);
		assert(!w32_allowed(GPU_MALI + 0xe24u, SK) && !w32_allowed(GPU_MALI + 0xe34u, SK) && !w32_allowed(GPU_MALI + 0xe10u, SK));
		assert(!w32_allowed(GPU_MALI + 0x1a0u, 1) && !w32_allowed(GPU_MALI + 0x1d0u, 1) && !w32_allowed(GPU_PMU_G3D_CFG, 1));
	}
#endif
	puts("PASS gpupower host test"
#ifdef GPU_POWER_STACK
	     " (stack variant)"
#endif
	);
	return 0;
}
