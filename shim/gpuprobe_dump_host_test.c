/* Host test for the read-only dumps of gpuprobe.c (extra CMU_G3D words behind the gate, ACPM fvmap of the G3D / G3DL2 domains):
 * fake PMU/CMU/GPU/SRAM; try_write32 is deliberately NOT defined, so linking fails if gpuprobe.c ever references a writer.
 * Uses the REAL generated board macros. Build (also run by build.sh):
 *   gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpuprobe_dump_host_test gpuprobe_dump_host_test.c */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "gpuprobe.c"

#if !(SOC_GS101 && defined(GPU_FVMAP_SRAM))
#error "needs the bluejay board_config.h (shim/build.sh --board bluejay writes out/board_config.h)"
#endif

static char lines[512][RAMLOG_LINE_MAX + 8];
static unsigned nl;
static uint32_t top_stat, emb_stat = 1, sram[0x2000 / 4];
static uintptr_t bad_pa;
static unsigned reads_outside;

void con_str(const char *label, const char *s)
{
	assert(nl < 512);
	snprintf(lines[nl++], sizeof(lines[0]), "%s %s", label, s);
}

int try_read32(uintptr_t pa, uint32_t *v)
{
	if (pa == bad_pa)
		return 0;
	if (pa == GPU_PMU_G3D_STAT) { *v = top_stat; return 1; }
	if (pa == GPU_PMU_EMB_STAT) { *v = emb_stat; return 1; }
	if (pa == GPU_PMU_G3D_CFG || pa == GPU_PMU_EMB_CFG) { *v = 1; return 1; }
	if (pa >= GPU_CMU_G3D && pa < GPU_CMU_G3D + 0x2000) {
		*v = (pa - GPU_CMU_G3D) == 0x600 || (pa - GPU_CMU_G3D) == 0x610 || (pa - GPU_CMU_G3D) == 0x620 ? 0x10 : (uint32_t)(0xC0000000u + (pa - GPU_CMU_G3D));
		return 1;
	}
	if (pa >= GPU_FVMAP_SRAM && pa < GPU_FVMAP_SRAM + 0x2000) { *v = sram[(pa - GPU_FVMAP_SRAM) / 4]; return 1; }
	if (pa >= GPU_MALI && pa < GPU_MALI + 0x5000) { *v = 0; return 1; }
	reads_outside++;
	*v = 0;
	return 1;
}

static int has(const char *s)
{
	for (unsigned i = 0; i < nl; i++)
		if (strstr(lines[i], s))
			return 1;
	return 0;
}

static void put_hdr(unsigned idx, unsigned type, unsigned nlv, unsigned mem, unsigned init, unsigned orv)
{
	sram[(24 * idx) / 4] = type | (nlv << 8) | (mem << 16);
	sram[(24 * idx + 8) / 4] = init;
	sram[(24 * idx + 20) / 4] = orv;
}

int main(void)
{
	/* G3D: 3 levels at 0x400, G3DL2: 2 levels at 0x500 */
	memset(sram, 0, sizeof(sram));
	put_hdr(GPU_FVMAP_IDX_G3D, GPU_FVMAP_IDX_G3D, 3, 1, 2, 0x400);
	put_hdr(GPU_FVMAP_IDX_G3DL2, GPU_FVMAP_IDX_G3DL2, 2, 1, 0, 0x500);
	uint32_t g3d[3][2] = { { 848000, 750000 }, { 500000, 650000 }, { 151000, 575000 } };
	for (unsigned l = 0; l < 3; l++) { sram[(0x400 + 8 * l) / 4] = g3d[l][0]; sram[(0x400 + 8 * l + 4) / 4] = g3d[l][1]; }
	sram[0x500 / 4] = 996000; sram[0x504 / 4] = 800000; sram[0x508 / 4] = 100000; sram[0x50c / 4] = 500000;

	top_stat = 1;
	gpu_probe();
	assert(has("GPU FVMAP5 type=5 nlv=3 members=1 init_lv=2 o_ratevolt=0x0400"));
	assert(has("GPU FVMAP5 lv=0 kHz=848000 uV=750000") && has("GPU FVMAP5 lv=2 kHz=151000 uV=575000 <- init_lv"));
	assert(!has("lv=0 kHz=848000 uV=750000 <- init_lv"));
	assert(has("GPU FVMAP6 type=6 nlv=2 members=1 init_lv=0 o_ratevolt=0x0500") && has("GPU FVMAP6 lv=0 kHz=996000 uV=800000 <- init_lv"));
	/* every extra CMU word is printed with its address, behind the open gate */
	static const struct { const char *n; unsigned off; } cmu[] = {
		{ "CMU_PLL_LOCKTIME", 0x004 }, { "CMU_PLL_G3D_CON1", 0x144 }, { "CMU_PLL_G3D_CON2", 0x148 }, { "CMU_PLL_G3D_CON3", 0x14c },
		{ "CMU_PLL_G3D_CON4", 0x150 }, { "CMU_PLL_G3D_L2_CON1", 0x184 }, { "CMU_PLL_G3D_L2_CON2", 0x188 }, { "CMU_PLL_G3D_L2_CON3", 0x18c },
		{ "CMU_PLL_G3D_L2_CON4", 0x190 }, { "CMU_MUX_BUSD", 0x630 }, { "CMU_MUX_GLB", 0x640 }, { "CMU_MUX_SWITCH", 0x650 },
		{ "CMU_DIV_ADD_CH", 0x1800 }, { "CMU_DIV_BUSP", 0x1804 }, { "CMU_DIV_L2_GLB", 0x1808 }, { "CMU_DIV_STACKS", 0x180c }, { "CMU_DIV_TOP", 0x1810 },
	};
	for (unsigned i = 0; i < sizeof(cmu) / sizeof(cmu[0]); i++) {
		char want[96];

		snprintf(want, sizeof(want), "GPU %s pa=%09lX v=%08X", cmu[i].n, (unsigned long)(GPU_CMU_G3D + cmu[i].off), (unsigned)(0xC0000000u + cmu[i].off));
		if (!has(want)) { fprintf(stderr, "missing: %s\n", want); assert(0); }
	}
	assert(has("GPU CMU_PLL_G3D pa=01C400140") && has("GPU CMU_MUX_TOP pa=01C400620 v=00000010") && has("GPU SUMMARY gate=open"));
	assert(reads_outside == 0);

	/* top domain off: no CMU word at all (as before), but the fvmap (APM SRAM) is still dumped */
	nl = 0; top_stat = 0;
	gpu_probe();
	assert(!has("CMU_") && has("GPU SUMMARY gate=pd_g3d-off") && has("GPU FVMAP5 lv=1") && has("GPU FVMAP6 lv=1"));

	/* faulting reads print unreadable and the dump goes on */
	nl = 0; top_stat = 1; bad_pa = GPU_CMU_G3D + 0x1808;
	gpu_probe();
	assert(has("GPU CMU_DIV_L2_GLB pa=01C401808 unreadable") && has("GPU CMU_DIV_STACKS pa=01C40180C v="));
	bad_pa = GPU_FVMAP_SRAM + 0x400 + 8 * 1 + 4;
	nl = 0; gpu_probe();
	assert(has("GPU FVMAP5 lv=1 unreadable") && has("GPU FVMAP5 lv=2 kHz=151000"));
	bad_pa = GPU_FVMAP_SRAM + 24 * GPU_FVMAP_IDX_G3DL2 + 8;
	nl = 0; gpu_probe();
	assert(has("GPU FVMAP6 header unreadable") && has("GPU FVMAP5 lv=0"));
	bad_pa = 0;

	/* garbage tables are rejected, not walked */
	nl = 0; put_hdr(GPU_FVMAP_IDX_G3D, 5, 0, 0, 0, 0x400);
	put_hdr(GPU_FVMAP_IDX_G3DL2, 6, 200, 1, 0, 0x500);
	gpu_probe();
	assert(has("GPU FVMAP5 type=5 nlv=0") && has("REJECTED") && !has("FVMAP5 lv=") && !has("FVMAP6 lv="));
	nl = 0; put_hdr(GPU_FVMAP_IDX_G3D, 5, 4, 1, 0, 0x1ff0);	/* table would leave the 0x2000 window */
	put_hdr(GPU_FVMAP_IDX_G3DL2, 6, 2, 1, 0, 0x502);	/* misaligned */
	gpu_probe();
	assert(!has("FVMAP5 lv=") && !has("FVMAP6 lv="));
	/* gpu_probe_pmu_on (T-50eb review F3, the M1 safety predicate of gpu_clk): 1 only when BOTH words read and BOTH have bit 0 set; every other case is 0 */
	bad_pa = 0; top_stat = 1; emb_stat = 1;
	assert(gpu_probe_pmu_on() == 1);
	top_stat = 0; emb_stat = 1;
	assert(gpu_probe_pmu_on() == 0);
	top_stat = 1; emb_stat = 0;
	assert(gpu_probe_pmu_on() == 0);
	top_stat = 0; emb_stat = 0;
	assert(gpu_probe_pmu_on() == 0);
	top_stat = 0xFFFFFFFEu; emb_stat = 1;	/* only bit 0 counts */
	assert(gpu_probe_pmu_on() == 0);
	top_stat = 1; emb_stat = 0xFFFFFFFEu;
	assert(gpu_probe_pmu_on() == 0);
	top_stat = 1; emb_stat = 1; bad_pa = GPU_PMU_G3D_STAT;	/* top unreadable */
	assert(gpu_probe_pmu_on() == 0);
	bad_pa = GPU_PMU_EMB_STAT;				/* emb unreadable */
	assert(gpu_probe_pmu_on() == 0);
	bad_pa = 0; top_stat = 1; emb_stat = 1;
	/* gpu_probe_shaders (printed before the --gpu-rail-on restore): only with both PMU domains on */
	nl = 0; top_stat = 1; bad_pa = 0;
	gpu_probe_shaders();
	assert(has("GPU RESTORE_SHADER_READY pa=01C500140") && has("GPU RESTORE_SHADER_PWRTRANS pa=01C500200") && has("GPU RESTORE_L2_READY pa=01C500160"));
	nl = 0; top_stat = 0;
	gpu_probe_shaders();
	assert(!has("RESTORE_"));
	puts("PASS gpuprobe dump host test");
	return 0;
}
