/* Host test for gpuclk.c (--gpu-clk) against the FAKE APM of dvfs_host_test.c (#included, its main renamed): G3DL2 / G3D GET, the two
 * FREQ_REQ in the order the GET values give (kbase gpu_dvfs_set_new_level), the read-back against each domain's own asked rate, the second CMU dump,
 * and every refusal with ZERO writes (ACPM gates, PMU off (review M1), non-empty queue, failing first GET, mixed direction, G3DL2 < G3D at the start);
 * a timeout of the first request stops the second; acpm_dvfs_reassert refuses CPU domains and every rate above the reviewed ceiling or off the table.
 * Built three ways by build.sh: with the real board macros (init rate 151000 for both), with -DGPU_CLK_LEVEL=3 (DT pair 750000 / 572000, T-a214), and
 * (-DGATE_TEST) with ACPM_LAYOUT_VERIFIED / ACPM_ELEM_UNIT_VERIFIED forced to 0 by a sed'ed copy of the header. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DGPU_CLK [-DGPU_CLK_LEVEL=3] -I. -Iout -include out/board_config.h -o out/gpuclk_host_test gpuclk_host_test.c */
#define main dvfs_host_main
#include "dvfs_host_test.c"
#undef main
#include "gpuclk.c"

#ifndef GPU_CLK
#error "build with -DGPU_CLK"
#endif

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl, cmu_calls, pmu_calls;
static int pmu_on = 1;		/* what the fake PMU says (PMU_G3D_STAT / PMU_EMB_STAT bit 0) */

#if defined(GPU_CLK_LEVEL) && GPU_CLK_LEVEL == 1
#define T_L2	996000		/* DT v2 pair 1 (gs101-gpu.dtsi) = the stock maximum (gpu_dvfs_max_freq 800000 caps G3D at 762000), sprint-gpu 2026-11-08 */
#define T_G3D	762000
#define S_L2	"996000"
#define S_PAIR	"g3dl2=996000 g3d=762000"
#define S_LVL	"1"
#elif defined(GPU_CLK_LEVEL)
#define T_L2	750000		/* DT v2 pair 3 (gs101-gpu.dtsi), the T-50eb one */
#define T_G3D	572000
#define S_L2	"750000"
#define S_PAIR	"g3dl2=750000 g3d=572000"
#define S_LVL	"3"
#else
#define T_L2	151000
#define T_G3D	151000
#endif

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}
void wait_ticks(uint64_t t) { now += t; }
void gpu_probe_cmu(void) { cmu_calls++; con_str("GPU", "CMU_FAKE_DUMP"); }
int gpu_probe_pmu_on(void) { pmu_calls++; return pmu_on; }
#ifdef GPU_CLK_LEVEL	/* gpu_clk_handover() (the shim.c handover step) calls these two */
static unsigned pstore_calls, rails_calls;
static int rails_rc;				/* what the fake gpu_rails_uv returns */
static uint32_t rails_b2s = 700000, rails_b8s = 800000;	/* the uV it hands back on rc 0 */
void pstore_save(void) { pstore_calls++; }
int gpu_rails_uv(uint32_t *b2s_uv, uint32_t *b8s_uv)
{
	rails_calls++;
	if (!rails_rc) {
		if (b2s_uv)
			*b2s_uv = rails_b2s;
		if (b8s_uv)
			*b8s_uv = rails_b8s;
	}
	return rails_rc;
}
#endif

static void fresh(void)		/* a clean fake APM + PMU on + empty log */
{
	setup(ESZ);
	nl = cmu_calls = pmu_calls = 0;
	pmu_on = 1;
}

static unsigned count(const char *s)
{
	unsigned n = 0, i;

	for (i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			n++;
	return n;
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static void dump_lines(void)
{
	for (unsigned i = 0; i < nl; i++)
		fprintf(stderr, "  %s\n", lines[i]);
}

#define EXPECT(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); dump_lines(); assert(0); } } while (0)

/* the (domain, word1, word2) of every request slot written, in order */
static unsigned reqs(uint32_t dom[], uint32_t w1[], uint32_t w2[])
{
	unsigned n = 0, i;

	for (i = 0; i + 3 < nw; i++)
		if (wlog[i].pa >= SRAM + REQ_BASE_O && wlog[i].pa < SRAM + REQ_BASE_O + QLEN * ESZ && (wlog[i].pa - SRAM - REQ_BASE_O) % ESZ == 0) {
			dom[n] = wlog[i].v & 0xffffu;
			w1[n] = wlog[i + 1].v;
			w2[n] = wlog[i + 2].v;
			n++;
		}
	return n;
}

int main(void)
{
#ifndef GATE_TEST
	uint32_t dom[16], w1[16], w2[16];
	unsigned n;
#endif

#ifdef GATE_TEST
	/* the board macros say layout_verified = 0 / elem_unit_verified = 0 (sed'ed header): refuse, say why, write nothing */
	fresh();
	EXPECT(gpu_clk() == 1 && nw == 0 && nbell == 0 && cmu_calls == 0 && pmu_calls == 0);
#ifdef GATE_UNIT
	EXPECT(has("refused: ") && has("element unit") && has("acpm.elem_unit_verified = 0") && has("elem_unit_verified=0"));
#else
	EXPECT(has("refused: ") && has("layout") && has("acpm.layout_verified = 0") && has("layout_verified=0"));
#endif
	puts("PASS gpuclk gate host test");
	return 0;
#else
	/* happy path: GET both, then G3DL2 then G3D (both start at the init level 151000 = "up or equal"), then GET both */
	fresh();
	EXPECT(gpu_clk() == 0);
	n = reqs(dom, w1, w2);
	EXPECT(n == 8);		/* GET6 GET5 | pre-GET6 REQ6 | pre-GET5 REQ5 | GET6 GET5 */
	EXPECT(dom[0] == 6 && w2[0] == 1 && dom[1] == 5 && w2[1] == 1);
	EXPECT(dom[2] == 6 && w2[2] == 1 && dom[3] == 6 && w2[3] == 0 && w1[3] == T_L2);	/* FREQ_REQ = 0 */
	EXPECT(dom[4] == 5 && w2[4] == 1 && dom[5] == 5 && w2[5] == 0 && w1[5] == T_G3D);
	EXPECT(dom[6] == 6 && w2[6] == 1 && dom[7] == 5 && w2[7] == 1);
	EXPECT(nreq == 2 && nget == 6 && cmu_calls == 1 && pmu_calls == 1 && cur[6] == T_L2 && cur[5] == T_G3D);
	EXPECT(has("GPU CLK order: up, G3DL2 then G3D") && has("GPU CLK idx g3dl2=6 g3d=5"));
	EXPECT(has("GPU CLK readback g3dl2=" "750000" " g3d=" "572000") == (T_L2 == 750000));
	EXPECT(has("GPU CLK asked g3dl2=") && has("GPU CLK result: ok") && has("GPU CLK cfg dvfs_ch=1 layout_verified=1 elem_unit_verified=1"));
	EXPECT(has("GPU CLK request G3DL2 req=") && has("GPU CLK request G3D seq="));
	only_channel_writes();
	{ unsigned a = 0, b = 0, i; for (i = 0; i < nl; i++) { if (strstr(lines[i], "request G3DL2 seq=")) a = i; if (strstr(lines[i], "request G3D seq=")) b = i; } EXPECT(a && b && a < b); }
#ifdef GPU_CLK_LEVEL
	EXPECT(has("GPU CLK target " S_PAIR " level=" S_LVL) && has("GPU CLK asked " S_PAIR));
	EXPECT(has("GPU CLK readback " S_PAIR));
	/* the known holes are named, once each, and nothing is "already at the asked rate" */
	EXPECT(count("GPU GAP: no thermal readout") == 1 && count("GPU GAP: BTS scenario g3d_performance") == 1 && !has("already at the asked rate"));
#else
	EXPECT(has("GPU CLK target g3dl2=151000 g3d=151000") && !has("level="));
	EXPECT(!has("GPU GAP: no thermal") && !has("GPU GAP: BTS"));
	EXPECT(count("already at the asked rate before the request") == 2);		/* review H1: both readbacks prove nothing */
#endif

	/* PMU says a domain is off or unreadable (review M1): nothing is read from the ACPM, nothing written */
	fresh(); pmu_on = 0;
	EXPECT(gpu_clk() == 1 && nw == 0 && nbell == 0 && nreq == 0 && nget == 0 && cmu_calls == 0 && pmu_calls == 1);
	EXPECT(has("refused: PMU_G3D_STAT / PMU_EMB_STAT do not both read on") && !has("GPU CLK request") && !has("GPU CLK get"));

	/* the firmware ignores the request and sits at another rate: the read-back says MISMATCH, both requests were still made */
	fresh(); cur[5] = 202000; cur[6] = 302000; ignore_req = 1;
	EXPECT(gpu_clk() == 1 && nreq == 2 && has("GPU CLK readback g3dl2=302000 g3d=202000") && has("MISMATCH") && cmu_calls == 1);
	/* one domain reads back its own asked rate, the other does not: still a MISMATCH (each domain is compared with its own rate) */
#ifdef GPU_CLK_LEVEL
	fresh(); cur[5] = 510000; cur[6] = T_L2;
	ignore_req = 1;
	EXPECT(gpu_clk() == 1 && has("GPU CLK readback g3dl2=" S_L2 " g3d=510000") && has("MISMATCH") && !has("GPU CLK result: ok"));
#endif

	/* a failing first GET (error code in reply word 3): nothing is requested */
	fresh(); get_err = 1;
	EXPECT(gpu_clk() == 1 && nreq == 0 && has("REFUSED-or-FAILED at the first GET") && cmu_calls == 0);
	fresh(); never = 1;
	EXPECT(gpu_clk() == 1 && nreq == 0 && has("REFUSED-or-FAILED at the first GET"));

	/* the first request is never taken: published, nothing rolled back, the second domain is not asked, no further doorbell */
	fresh(); never_at = 4;		/* doorbells: GET6 1, GET5 2, pre-GET6 3, REQ6 4 */
	EXPECT(gpu_clk() == 1 && nbell == 4 && nreq == 0 && has("REFUSED-or-FAILED") && has("the request stays queued"));
	n = reqs(dom, w1, w2);
	EXPECT(!(n >= 6 && dom[5] == 5 && w2[5] == 0));

	/* a start state with G3DL2 below G3D is not understood: the two GETs are the only traffic, no request */
	fresh(); cur[6] = 151000; cur[5] = 202000;
	EXPECT(gpu_clk() == 1 && nreq == 0 && nget == 2 && nbell == 2 && has("refused: G3DL2 reads below G3D") && cmu_calls == 0);
	n = reqs(dom, w1, w2);
	EXPECT(n == 2 && w2[0] == 1 && w2[1] == 1);
	only_channel_writes();

	/* queue not empty / wrong element size / a layout the descriptor check rejects: zero writes */
	fresh(); setS(REQ_FRONT_O, 0);
	EXPECT(gpu_clk() == 1 && nw == 0 && nbell == 0 && has("refused: "));
	setup(8);
	nl = 0;
	EXPECT(gpu_clk() == 1 && nw == 0 && nbell == 0);

#if defined(GPU_CLK_LEVEL) && GPU_CLK_LEVEL == 1
	/* level 1 (996000 / 762000): down from 996000 / 848000 = G3D first; G3DL2 already at its target -> unproven, never "ok" */
	fresh(); cur[6] = 996000; cur[5] = 848000;
	EXPECT(gpu_clk() == 1 && nreq == 2 && cur[6] == 996000 && cur[5] == 762000 && has("GPU CLK order: down, G3D then G3DL2") && count("already at the asked rate before the request") == 1);
	EXPECT(has("effect UNPROVEN") && !has("GPU CLK result: ok"));
	{ unsigned a = 0, b = 0, i; for (i = 0; i < nl; i++) { if (strstr(lines[i], "request G3DL2 seq=")) a = i; if (strstr(lines[i], "request G3D seq=")) b = i; } EXPECT(a && b && b < a); }
	only_channel_writes();
	/* up from the T-50eb pair 750000 / 572000: G3DL2 first, then G3D; the in-between state 996000 / 572000 keeps G3DL2 >= G3D */
	fresh(); cur[6] = 750000; cur[5] = 572000;
	EXPECT(gpu_clk() == 0 && has("GPU CLK order: up, G3DL2 then G3D") && cur[6] == 996000 && cur[5] == 762000 && has("GPU CLK result: ok"));
	n = reqs(dom, w1, w2);
	EXPECT(dom[3] == 6 && w1[3] == 996000 && dom[5] == 5 && w1[5] == 762000);
	/* one up, one down (G3DL2 996000 stays, G3D 848000 -> 762000 is down only: allowed above); G3DL2 885000 up while G3D 848000 down: refused */
	fresh(); cur[6] = 885000; cur[5] = 848000;
	EXPECT(gpu_clk() == 1 && nreq == 0 && nget == 2 && has("refused: one domain goes up and the other down") && cmu_calls == 0);
	/* undervolt compare against the fvmap rows of pair 1: 775000 uV (L2, BUCK8S) / 718750 uV (shaders, BUCK2S), observed run nav20 */
	nl = 0;
	EXPECT(gpu_clk_uv_check(718750, 775000) == 0 && has("GPU CLK uv: both rails read at or above their fvmap voltage") && !has("GPU GAP"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(700000, 775000) == 1 && has("GPU GAP: BUCK2S(shaders) OUT1 700000 uV < fvmap 718750 uV") && has("UNDERVOLT") && !has("BUCK8S"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(718750, 774999) == 1 && has("GPU GAP: BUCK8S(L2) OUT1 774999 uV < fvmap 775000 uV") && !has("BUCK2S"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(662500, 731250) == 2 && count("UNDERVOLT") == 2);	/* the pair-3 voltages are an undervolt at pair 1 */
	/* handover: happy with both rails at or above the pair-1 rows, GAP when the shader rail stayed at a pair-3 voltage */
	fresh(); pstore_calls = rails_calls = 0; rails_rc = 0; rails_b2s = 725000; rails_b8s = 800000;
	gpu_clk_handover(1);
	EXPECT(has("SECTION GPU handover (DVFS pair)") && nreq == 2 && rails_calls == 1 && pstore_calls == 2 && !has("GPU GAP: rail") && !has("GPU GAP: a GPU rail") && !has("refused or failed"));
	fresh(); pstore_calls = rails_calls = 0; rails_rc = 0; rails_b2s = 662500;
	gpu_clk_handover(1);
	EXPECT(has("GPU GAP: a GPU rail reads below its fvmap voltage") && has("UNDERVOLT") && has("BUCK2S(shaders)"));
	rails_b2s = 700000;
#elif defined(GPU_CLK_LEVEL)
	/* both domains going DOWN (e.g. a previous boot stage left 996000 / 848000): G3D first, then G3DL2, so G3DL2 >= G3D after every step */
	fresh(); cur[6] = 996000; cur[5] = 848000;
	EXPECT(gpu_clk() == 0);
	n = reqs(dom, w1, w2);
	EXPECT(n == 8 && dom[2] == 5 && w2[2] == 1 && dom[3] == 5 && w2[3] == 0 && w1[3] == 572000 && dom[4] == 6 && w2[4] == 1 && dom[5] == 6 && w2[5] == 0 && w1[5] == 750000);
	EXPECT(nreq == 2 && cur[6] == 750000 && cur[5] == 572000 && has("GPU CLK order: down, G3D then G3DL2") && has("GPU CLK readback g3dl2=750000 g3d=572000"));
	{ unsigned a = 0, b = 0, i; for (i = 0; i < nl; i++) { if (strstr(lines[i], "request G3DL2 seq=")) a = i; if (strstr(lines[i], "request G3D seq=")) b = i; } EXPECT(a && b && b < a); }
	only_channel_writes();
	/* every step of the up path keeps G3DL2 >= G3D: replay the two requests against the start state */
	fresh(); cur[6] = 603000; cur[5] = 471000;
	EXPECT(gpu_clk() == 0 && has("GPU CLK order: up, G3DL2 then G3D"));
	n = reqs(dom, w1, w2);
	EXPECT(dom[3] == 6 && dom[5] == 5 && cur[6] == 750000 && cur[5] == 572000);
	/* one domain up, the other down (G3DL2 996000 down to 750000 while G3D 400000 goes up to 572000): no order keeps the constraint -> refused, no request */
	fresh(); cur[6] = 996000; cur[5] = 400000;
	EXPECT(gpu_clk() == 1 && nreq == 0 && nget == 2 && has("refused: one domain goes up and the other down") && cmu_calls == 0);
	n = reqs(dom, w1, w2);
	EXPECT(n == 2);
	/* already at the asked pair: the requests are still made (the firmware must act) but both readbacks are named as proving nothing */
	fresh(); cur[6] = 750000; cur[5] = 572000;
	EXPECT(gpu_clk() == 1 && nreq == 2 && count("already at the asked rate before the request") == 2);
	EXPECT(has("GPU CLK result: reply ok, effect UNPROVEN") && !has("GPU CLK result: ok"));	/* T-50eb review F2: no "ok" on an H1 basis, the caller logs the GAP */
	/* one domain already at its target: also unproven */
	fresh(); cur[6] = 750000; cur[5] = 202000;
	EXPECT(gpu_clk() == 1 && count("already at the asked rate before the request") == 1 && has("effect UNPROVEN") && !has("GPU CLK result: ok"));
	/* T-50eb review F1: the undervolt compare. Rows 731250 uV (L2, BUCK8S) / 662500 uV (shaders, BUCK2S) observed run g0b */
	nl = 0;
	EXPECT(gpu_clk_uv_check(662500, 731250) == 0 && has("GPU CLK uv: both rails read at or above their fvmap voltage") && !has("GPU GAP"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(700000, 800000) == 0 && !has("GPU GAP"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(600000, 731250) == 1 && has("GPU GAP: BUCK2S(shaders) OUT1 600000 uV < fvmap 662500 uV") && has("UNDERVOLT") && !has("BUCK8S") && !has("both rails read"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(662500, 600000) == 1 && has("GPU GAP: BUCK8S(L2) OUT1 600000 uV < fvmap 731250 uV") && !has("BUCK2S"));
	nl = 0;
	EXPECT(gpu_clk_uv_check(600000, 600000) == 2 && count("UNDERVOLT") == 2);
	nl = 0;
	EXPECT(gpu_clk_uv_check(662499, 731250) == 1 && gpu_clk_uv_check(662500, 731249) == 1);	/* off by one uV is below */
	/* gpu_clk_handover (T-a214, moved out of shim.c): the request only for a verified rail, every failure a GPU GAP, pstore saved before and after */
	fresh(); pstore_calls = rails_calls = 0; rails_rc = 0; rails_b2s = 700000; rails_b8s = 800000;
	gpu_clk_handover(1);
	EXPECT(has("SECTION GPU handover (DVFS pair)") && nreq == 2 && rails_calls == 1 && pstore_calls == 2 && !has("GPU GAP: rail") && !has("GPU GAP: a GPU rail") && !has("refused or failed"));
	fresh(); pstore_calls = rails_calls = 0;
	gpu_clk_handover(0);
	EXPECT(has("GPU GAP: DVFS pair request skipped, the shader rail is not verified on") && nreq == 0 && nw == 0 && rails_calls == 0 && pmu_calls == 0 && pstore_calls == 2);
	fresh(); pstore_calls = rails_calls = 0; pmu_on = 0;
	gpu_clk_handover(1);
	EXPECT(has("GPU GAP: DVFS pair request refused or failed") && nreq == 0 && nw == 0 && rails_calls == 1 && pstore_calls == 2);	/* the rails are still read: they show the state XNU gets */
	fresh(); pstore_calls = rails_calls = 0; rails_rc = 1;
	gpu_clk_handover(1);
	EXPECT(has("GPU GAP: rail readback after the DVFS pair failed") && !has("a GPU rail reads below") && rails_calls == 1);
	fresh(); pstore_calls = rails_calls = 0; rails_rc = 0; rails_b2s = 600000;
	gpu_clk_handover(1);
	EXPECT(has("GPU GAP: a GPU rail reads below its fvmap voltage") && has("UNDERVOLT") && has("BUCK2S(shaders)"));
	rails_b2s = 700000;
#endif

	/* acpm_dvfs_reassert: only G3D / G3DL2, only table rows at or below the ceilings (G3D 762000 = stock max, G3DL2 996000, sprint-gpu 2026-11-08); everything else is refused before anything is sent */
	{
		struct acpm_trace t;

		fresh();
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 2, 1197000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 3, 1197000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 4, 1106000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 7, 151000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 848000, &t) == ACPM_E_ARG && acpm_dvfs_reassert(&cfg, 5, 996000, &t) == ACPM_E_ARG && nw == 0);	/* a G3D row above the ceiling (above stock), not a G3D row */
		EXPECT(acpm_dvfs_reassert(&cfg, 6, 997000, &t) == ACPM_E_ARG && acpm_dvfs_reassert(&cfg, 6, 1100000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 573000, &t) == ACPM_E_ARG && acpm_dvfs_reassert(&cfg, 5, 151001, &t) == ACPM_E_ARG && nw == 0);	/* not rows */
		EXPECT(acpm_dvfs_reassert(&cfg, 6, 751000, &t) == ACPM_E_ARG && acpm_dvfs_reassert(&cfg, 6, 848000, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 0, &t) == ACPM_E_ARG && nw == 0);
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 151000, &t) == ACPM_OK && nreq == 1);	/* same rate as the current one: still sent */
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 6, 151000, &t) == ACPM_OK && nreq == 2);
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 6, 750000, &t) == ACPM_OK && nreq == 3 && cur[6] == 750000);	/* the T-50eb pair is reachable */
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 572000, &t) == ACPM_OK && nreq == 4 && cur[5] == 572000);
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 6, 996000, &t) == ACPM_OK && nreq == 5 && cur[6] == 996000);	/* the ceiling pair (row 0) is reachable */
		t = trace();
		EXPECT(acpm_dvfs_reassert(&cfg, 5, 762000, &t) == ACPM_OK && nreq == 6 && cur[5] == 762000);
		only_channel_writes();
	}
	/* a CPU --dvfs table is untouched by the GPU rows: the CPU client still sees exactly three domains */
	EXPECT(acpm_dvfs_ndom() == 3 && ACPM_DVFS_NALL == 7);
	puts("PASS gpuclk host test");
	return 0;
#endif
}
