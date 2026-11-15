/*
 * gpuprobe.c - read-only Mali-G78 probe (see gpuprobe.h, docs/research/17).
 *
 * Every access is try_read32(); a bus fault prints "unreadable" and the probe
 * continues. No MMIO stores of any kind, no SMC, no clock/power change; the
 * object is audited by tools/test-bluejay-boot.py with the same
 * stack-only-stores policy as usbprobe.o and wlanprobe.o. No static state.
 *
 * Register sources ([src] = Android kernel, branch android-gs-bluejay-6.1-android16):
 *  - PMU (0x17460000) G3D_CONFIGURATION +0x1E00 / G3D_STATUS +0x1E04 and
 *    EMBEDDED_G3D_CONFIGURATION +0x2000 / _STATUS +0x2004: soc-gs
 *    drivers/soc/google/cal-if/gs101/flexpmu_cal_local_gs101.h (g3d_on, g3d_status,
 *    embedded_g3d_on, embedded_g3d_status); the DT also gives 0x2004 as
 *    gpu_pmu_status_reg_offset (kernel/devices/google/gs101 dts/gs101-gpu.dtsi).
 *    Linux reads these with readl (pmucal_rae.c:131,145), so AP reads are allowed.
 *  - CMU_G3D (0x1C400000) PLL_CON0_MUX_CLKCMU_EMBEDDED_G3D_{COREGROUP,STACKS,TOP}_USER at
 *    +0x600/+0x610/+0x620, PLL_CON0_PLL_G3D +0x140, PLL_CON0_PLL_G3D_L2 +0x180: same file.
 *  - Extra CMU_G3D words (read-only, same gate as the CMU reads above): the sequence tables embedded_g3d_on / embedded_g3d_off / g3d_save of
 *    ~/pixelios-artifacts/refs/kbase-bluejay/flexpmu_local.h (copy of soc-gs flexpmu_cal_local_gs101.h): PLL_LOCKTIME 0x4 (:494-495),
 *    PLL_CON0..CON4 of PLL_G3D 0x140/144/148/14C/150 (:449,448,496,500,498) and of PLL_G3D_L2 0x180/184/188/18C/190 (:451,450,497,501,499),
 *    MUX_CLKCMU_EMBEDDED_G3D_{COREGROUP,STACKS,TOP}_USER 0x600/610/620 (:452-454), MUX_CLKCMU_G3D_{BUSD,GLB,SWITCH}_USER 0x630/640/650 (:493,492,491),
 *    CLK_CON_DIV ADD_CH_CLK/BUSP/L2_GLB/STACKS/TOP 0x1800/1804/1808/180C/1810 (:490,489,488,487,486). Table: docs/research/27.
 *  - ACPM fvmap (APM SRAM, read-only, not gated: the APM SRAM is always on, acpm.c reads it too): layout from xnu/patches/0097-sweet-fvmap-dump.patch
 *    (struct fvmap_header, 24 bytes per ACPM_DVFS index: type @0, num_of_lv @1, num_of_members @2, init_lv @8, o_ratevolt u16 @20; the rate/volt
 *    table at fvmap base + o_ratevolt, 8 bytes per level: u32 rate kHz, u32 volt uV), base PA and the G3D/G3DL2 indices from soc.json "gpu"
 *    (observed: run fvmap1, docs/research/25 and 27).
 *  - GPU registers (0x1C500000, JM layout): gpu/mali_kbase hw_access/regmap/
 *    mali_kbase_regmap_jm_enums.h (GPU_CONTROL__* offsets in the comments),
 *    mali_kbase_regmap_jm.c:1814-1825 (MMU at 0x2000).
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "gpuprobe.h"

#if SOC_GS101 && defined(GPU_MALI)

#define CMU_MUX_COREGROUP	0x600u
#define CMU_MUX_STACKS		0x610u
#define CMU_MUX_TOP		0x620u
#define CMU_PLL_G3D		0x140u
#define CMU_PLL_G3D_L2		0x180u

static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (unsigned i = 0; i < digits && pos + 1 < cap; i++)
		dst[pos++] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	return pos;
}

static unsigned put_dec(char *dst, unsigned cap, unsigned pos, uint32_t v)
{
	char t[10];
	unsigned n = 0;

	do {
		t[n++] = (char)('0' + v % 10u);
		v /= 10u;
	} while (v && n < sizeof(t));
	while (n && pos + 1 < cap)
		dst[pos++] = t[--n];
	return pos;
}

static void emit(char *buf, unsigned pos)
{
	if (pos >= RAMLOG_LINE_MAX)
		pos = RAMLOG_LINE_MAX - 1;
	buf[pos] = 0;
	con_str("GPU", buf);
}

/* "<name> pa=XXXXXXXXX v=XXXXXXXX" or "... unreadable"; returns 1 and *v when read. */
static int rd(const char *name, uintptr_t pa, uint32_t *v)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = 0;
	int ok = try_read32(pa, v);
	p = put_txt(line, sizeof(line), p, name);
	p = put_txt(line, sizeof(line), p, " pa=");
	p = put_hex(line, sizeof(line), p, pa, 9);
	if (ok) {
		p = put_txt(line, sizeof(line), p, " v=");
		p = put_hex(line, sizeof(line), p, *v, 8);
	} else {
		p = put_txt(line, sizeof(line), p, " unreadable");
	}
	emit(line, p);
	return ok;
}

static void summary(enum gpu_gate gate, int id_ok, uint32_t id, unsigned cores)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "SUMMARY gate=");
	p = put_txt(line, sizeof(line), p, gpu_gate_name(gate));
	if (id_ok) {
		p = put_txt(line, sizeof(line), p, " id=");
		p = put_hex(line, sizeof(line), p, id, 8);
		p = put_txt(line, sizeof(line), p, " ");
		p = put_txt(line, sizeof(line), p, gpu_model_name(id));
		p = put_txt(line, sizeof(line), p, gpu_is_jm(id) ? " JM cores=" : " CSF cores=");
		p = put_hex(line, sizeof(line), p, cores / 10, 1);	/* decimal, cores < 100 */
		p = put_hex(line, sizeof(line), p, cores % 10, 1);
	}
	emit(line, p);
}

/* All CMU_G3D words of the probe (muxes, PLLs, dividers); returns whether the three user muxes were readable (outputs for the gate).
 * Caller has checked that the top domain is on. */
static int cmu_dump(uint32_t *mt, uint32_t *mc, uint32_t *ms)
{
	uint32_t v;
	int mux_ok;

	int a = rd("CMU_MUX_TOP", GPU_CMU_G3D + CMU_MUX_TOP, mt);
	int b = rd("CMU_MUX_CORE", GPU_CMU_G3D + CMU_MUX_COREGROUP, mc);
	int c = rd("CMU_MUX_STACK", GPU_CMU_G3D + CMU_MUX_STACKS, ms);
	mux_ok = a && b && c;
	rd("CMU_PLL_G3D", GPU_CMU_G3D + CMU_PLL_G3D, &v);
	rd("CMU_PLL_G3D_L2", GPU_CMU_G3D + CMU_PLL_G3D_L2, &v);
	/* more CMU_G3D words (same gate): PLL lock time + CON1..CON4 of both PLLs, the other user muxes, the dividers */
	rd("CMU_PLL_LOCKTIME", GPU_CMU_G3D + 0x004u, &v);
	for (unsigned k = 1; k < 5; k++) {
		char nm[] = "CMU_PLL_G3D_CON0";
		char n2[] = "CMU_PLL_G3D_L2_CON0";

		nm[15] = (char)('0' + k);
		n2[18] = (char)('0' + k);
		rd(nm, GPU_CMU_G3D + CMU_PLL_G3D + (uintptr_t)4u * k, &v);
		rd(n2, GPU_CMU_G3D + CMU_PLL_G3D_L2 + (uintptr_t)4u * k, &v);
	}
	rd("CMU_MUX_BUSD", GPU_CMU_G3D + 0x630u, &v);
	rd("CMU_MUX_GLB", GPU_CMU_G3D + 0x640u, &v);
	rd("CMU_MUX_SWITCH", GPU_CMU_G3D + 0x650u, &v);
	rd("CMU_DIV_ADD_CH", GPU_CMU_G3D + 0x1800u, &v);
	rd("CMU_DIV_BUSP", GPU_CMU_G3D + 0x1804u, &v);
	rd("CMU_DIV_L2_GLB", GPU_CMU_G3D + 0x1808u, &v);
	rd("CMU_DIV_STACKS", GPU_CMU_G3D + 0x180cu, &v);
	rd("CMU_DIV_TOP", GPU_CMU_G3D + 0x1810u, &v);

	return mux_ok;
}

#ifdef GPU_FVMAP_SRAM
#define FVMAP_HDR	24u		/* bytes per domain header (0097 patch) */
#define FVMAP_BYTES	0x2000u		/* the patch's bound for o_ratevolt + levels */
#define FVMAP_MAX_LV	32u

/* "FVMAP<idx> ..." lines for one ACPM_DVFS domain; every read is fault-guarded and an unreadable word is printed, not guessed. */
static void fvmap_dom(unsigned idx)
{
	char line[RAMLOG_LINE_MAX];
	uintptr_t h = GPU_FVMAP_SRAM + (uintptr_t)FVMAP_HDR * idx;
	uint32_t w0, w2, w5, rate, volt;
	unsigned p, nlv, init, orv;

	p = put_txt(line, sizeof(line), 0, "FVMAP");
	p = put_dec(line, sizeof(line), p, idx);
	if (!try_read32(h, &w0) || !try_read32(h + 8u, &w2) || !try_read32(h + 20u, &w5)) {
		p = put_txt(line, sizeof(line), p, " header unreadable");
		emit(line, p);
		return;
	}
	nlv = (w0 >> 8) & 0xffu;
	init = w2 & 0xffu;
	orv = w5 & 0xffffu;
	p = put_txt(line, sizeof(line), p, " type=");
	p = put_dec(line, sizeof(line), p, w0 & 0xffu);
	p = put_txt(line, sizeof(line), p, " nlv=");
	p = put_dec(line, sizeof(line), p, nlv);
	p = put_txt(line, sizeof(line), p, " members=");
	p = put_dec(line, sizeof(line), p, (w0 >> 16) & 0xffu);
	p = put_txt(line, sizeof(line), p, " init_lv=");
	p = put_dec(line, sizeof(line), p, init);
	p = put_txt(line, sizeof(line), p, " o_ratevolt=0x");
	p = put_hex(line, sizeof(line), p, orv, 4);
	if (nlv == 0 || nlv > FVMAP_MAX_LV || orv + 8u * nlv > FVMAP_BYTES || (orv & 3u)) {
		p = put_txt(line, sizeof(line), p, " REJECTED");	/* the 0097 patch's own range check */
		emit(line, p);
		return;
	}
	emit(line, p);
	for (unsigned l = 0; l < nlv; l++) {
		uintptr_t e = GPU_FVMAP_SRAM + orv + (uintptr_t)8u * l;

		p = put_txt(line, sizeof(line), 0, "FVMAP");
		p = put_dec(line, sizeof(line), p, idx);
		p = put_txt(line, sizeof(line), p, " lv=");
		p = put_dec(line, sizeof(line), p, l);
		if (!try_read32(e, &rate) || !try_read32(e + 4u, &volt)) {
			p = put_txt(line, sizeof(line), p, " unreadable");
		} else {
			p = put_txt(line, sizeof(line), p, " kHz=");
			p = put_dec(line, sizeof(line), p, rate);
			p = put_txt(line, sizeof(line), p, " uV=");
			p = put_dec(line, sizeof(line), p, volt);
		}
		if (l == init)
			p = put_txt(line, sizeof(line), p, " <- init_lv");
		emit(line, p);
	}
}
#endif

/* Re-reads the always-on PMU word and, if the top domain is on, prints the whole CMU_G3D dump again (gpuclk.c: before / after a clock request). */
void gpu_probe_cmu(void)
{
	uint32_t top = 0, mt = 0, mc = 0, ms = 0;

	if (rd("PMU_G3D_STAT", GPU_PMU_G3D_STAT, &top) && (top & 1u))
		(void)cmu_dump(&mt, &mc, &ms);
}

/* The always-on PMU says both GPU power domains are on (status bit 0 of PMU_G3D_STAT and
 * PMU_EMB_STAT); both words are printed. Two reads, no write. */
int gpu_probe_pmu_on(void)
{
	uint32_t top = 0, emb = 0;
	/* style-allow: sized-types int is rd()'s boolean and this module's API type (byte-pinned) */
	/* style-allow: assert audited object, byte-pinned predicate: a failed read is a plain 0 */
	int a = rd("PMU_G3D_STAT", GPU_PMU_G3D_STAT, &top);
	int b = rd("PMU_EMB_STAT", GPU_PMU_EMB_STAT, &emb);

	return a && b && (top & 1u) && (emb & 1u);
}

/* Read-only state of the shader cores for --gpu-rail-on, printed right before the rail is restored:
 * only when both PMU domains read as on. */
void gpu_probe_shaders(void)
{
	uint32_t top = 0, emb = 0, v;

	if (!rd("PMU_G3D_STAT", GPU_PMU_G3D_STAT, &top) || !rd("PMU_EMB_STAT", GPU_PMU_EMB_STAT, &emb) || !(top & 1u) || !(emb & 1u))
		return;
	rd("RESTORE_SHADER_READY", GPU_MALI + 0x140, &v);
	rd("RESTORE_SHADER_PWRTRANS", GPU_MALI + 0x200, &v);
	rd("RESTORE_TILER_READY", GPU_MALI + 0x150, &v);
	rd("RESTORE_L2_READY", GPU_MALI + 0x160, &v);
}

void gpu_probe(void)
{
	uint32_t v, top = 0, emb = 0, mt = 0, mc = 0, ms = 0, id = 0, sh = 0;
	int top_ok, emb_ok, mux_ok = 0, id_ok = 0;
	enum gpu_gate gate;

	/* Always-on PMU: the only thing read unconditionally. */
	rd("PMU_G3D_CFG", GPU_PMU_G3D_CFG, &v);
	top_ok = rd("PMU_G3D_STAT", GPU_PMU_G3D_STAT, &top);
	rd("PMU_EMB_CFG", GPU_PMU_EMB_CFG, &v);
	emb_ok = rd("PMU_EMB_STAT", GPU_PMU_EMB_STAT, &emb);

	/* CMU_G3D lives in the top domain: touch it only when that is powered. */
	if (top_ok && (top & 1u))
		mux_ok = cmu_dump(&mt, &mc, &ms);
#ifdef GPU_FVMAP_SRAM
	fvmap_dom(GPU_FVMAP_IDX_G3D);
	fvmap_dom(GPU_FVMAP_IDX_G3DL2);
#endif

	gate = gpu_gate(top_ok, top, emb_ok, emb, mux_ok, mt, mc, ms);
	if (gate != GPU_GATE_OPEN) {
		summary(gate, 0, 0, 0);
		return;
	}

	/* Gate open: GPU_CONTROL registers (all RO or RAWSTAT), then JOB/MMU RAWSTAT. */
	id_ok = rd("GPU_ID", GPU_MALI + 0x000, &id);
	rd("L2_FEATURES", GPU_MALI + 0x004, &v);
	rd("TILER_FEATURES", GPU_MALI + 0x00c, &v);
	rd("MEM_FEATURES", GPU_MALI + 0x010, &v);
	rd("MMU_FEATURES", GPU_MALI + 0x014, &v);
	rd("AS_PRESENT", GPU_MALI + 0x018, &v);
	rd("JS_PRESENT", GPU_MALI + 0x01c, &v);
	rd("GPU_IRQ_RAWSTAT", GPU_MALI + 0x020, &v);
	rd("GPU_STATUS", GPU_MALI + 0x034, &v);
	rd("GPU_FAULTSTATUS", GPU_MALI + 0x03c, &v);
	rd("REVIDR", GPU_MALI + 0x280, &v);
	rd("THREAD_MAX_THREADS", GPU_MALI + 0x0a0, &v);
	rd("THREAD_FEATURES", GPU_MALI + 0x0ac, &v);
	rd("SHADER_PRESENT_LO", GPU_MALI + 0x100, &sh);
	rd("SHADER_PRESENT_HI", GPU_MALI + 0x104, &v);
	rd("TILER_PRESENT", GPU_MALI + 0x110, &v);
	rd("L2_PRESENT", GPU_MALI + 0x120, &v);
	rd("STACK_PRESENT", GPU_MALI + 0xe00, &v);
	rd("SHADER_READY", GPU_MALI + 0x140, &v);
	rd("L2_READY", GPU_MALI + 0x160, &v);
	rd("COHERENCY_FEATURES", GPU_MALI + 0x300, &v);
	rd("JOB_IRQ_RAWSTAT", GPU_MALI + 0x1000, &v);
	rd("MMU_IRQ_RAWSTAT", GPU_MALI + 0x2000, &v);
	summary(gate, id_ok, id, gpu_popcount32(sh));
}

#else	/* not gs101 (or no GPU addresses): empty stub */

void gpu_probe(void) {}
void gpu_probe_cmu(void) {}
int gpu_probe_pmu_on(void) { return 0; }
void gpu_probe_shaders(void) {}

#endif
