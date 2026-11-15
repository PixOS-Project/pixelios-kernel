#pragma once
#include <stdint.h>

/*
 * gpuprobe.h - STRICTLY read-only probe of the Mali-G78 MP20 GPU of the
 * Pixel 6a (docs/research/17-gpu-kernel-gs101.md, stage 0). Built and
 * linked only with build.sh --gpu-probe on a gs101 board; other boards get
 * an empty stub. Addresses come from soc/google/gs101/soc.json (the "gpu"
 * object) through the generated GPU_* board macros.
 *
 * Safety rule: the GPU register file (0x1C500000) is read ONLY if the
 * always-on PMU says both GPU power domains are on and the CMU_G3D user
 * muxes are selected, i.e. the state Linux's own power-on sequence leaves.
 * Otherwise the probe prints the PMU/CMU words it could read and stops.
 * Nothing is ever written; the audit in tools/test-bluejay-boot.py checks it.
 *
 * Pure decoders live here so shim/gpuprobe_host_test.c can test them.
 */

/* GPU_ID layout [src: kbase mali_kbase_gpu_id.h:29-44, GPU_ID2_*]. */
#define GPUID_MODEL_MASK	0xF00F0000u	/* arch_major [31:28] | product_major [19:16] */

static inline uint32_t gpu_id_arch_major(uint32_t id)	{ return id >> 28; }
static inline uint32_t gpu_id_arch_minor(uint32_t id)	{ return (id >> 24) & 0xfu; }
static inline uint32_t gpu_id_product(uint32_t id)	{ return (id >> 16) & 0xfu; }
static inline uint32_t gpu_id_ver_major(uint32_t id)	{ return (id >> 12) & 0xfu; }
static inline uint32_t gpu_id_ver_minor(uint32_t id)	{ return (id >> 4) & 0xffu; }
static inline uint32_t gpu_id_ver_status(uint32_t id)	{ return id & 0xfu; }

/* Model name from the kbase table (mali_kbase_core_linux.c:3321-3340); "?" if unknown. */
static inline const char *gpu_model_name(uint32_t id)
{
	switch (id & GPUID_MODEL_MASK) {
	case 0x90000000u: return "Mali-G77";
	case 0x90010000u: return "Mali-G57";
	case 0x90020000u: return "Mali-G78";	/* kbase TBEX: the Pixel 6a */
	case 0x90040000u: return "Mali-G68";
	case 0x90050000u: return "Mali-G78AE";
	case 0xA0020000u: return "Mali-G710";
	case 0xA0030000u: return "Mali-G510";
	case 0xA0040000u: return "Mali-G310";
	case 0xA0070000u: return "Mali-G610";
	default: return "?";
	}
}

/* Arch 10 and up is the CSF (command stream frontend) generation; arch 9 is job-manager (JM).
 * [src: kbase hw_access/regmap/mali_kbase_regmap_jm.c:2301-2307 stops at arch 9.14;
 * G710/G510/G310/G610 are arch 10 in mali_kbase_gpu_id.h]. */
static inline int gpu_is_jm(uint32_t id)
{
	return gpu_id_arch_major(id) < 10;
}

/* Number of set bits (shader core count from SHADER_PRESENT). No builtin: with
 * -mgeneral-regs-only gcc would call libgcc. */
static inline unsigned gpu_popcount32(uint32_t v)
{
	unsigned n = 0;
	while (v) {
		v &= v - 1;
		n++;
	}
	return n;
}

/*
 * Precondition for touching the GPU register file. Inputs are the raw words
 * (and whether each read succeeded). Returns GPU_GATE_OPEN or the first reason
 * not to read. Status bit 0 of the PMU STATUS words = domain powered
 * [src: soc-gs cal-if/gs101/flexpmu_cal_local_gs101.h g3d_status/embedded_g3d_status,
 * bit (1<<0)]; bit 4 of PLL_CON0_MUX_CLKCMU_*_USER = the user-mux select the kernel's
 * own embedded_g3d_on sequence sets [src: same file, embedded_g3d_on]. The mux bit is a
 * conservative extra [guess: that bit 4 is the mux select].
 */
enum gpu_gate {
	GPU_GATE_OPEN = 0,
	GPU_GATE_PMU_UNREADABLE,
	GPU_GATE_TOP_OFF,
	GPU_GATE_CORES_OFF,
	GPU_GATE_CMU_UNREADABLE,
	GPU_GATE_MUX_OFF,
};

#define GPU_MUX_SELECT	(1u << 4)

static inline enum gpu_gate gpu_gate(int top_ok, uint32_t top_status, int emb_ok, uint32_t emb_status,
				     int mux_ok, uint32_t mux_top, uint32_t mux_core, uint32_t mux_stack)
{
	if (!top_ok || !emb_ok)
		return GPU_GATE_PMU_UNREADABLE;
	if (!(top_status & 1u))
		return GPU_GATE_TOP_OFF;	/* CMU_G3D is inside the top domain: do not touch it */
	if (!(emb_status & 1u))
		return GPU_GATE_CORES_OFF;
	if (!mux_ok)
		return GPU_GATE_CMU_UNREADABLE;
	if (!(mux_top & mux_core & mux_stack & GPU_MUX_SELECT))
		return GPU_GATE_MUX_OFF;
	return GPU_GATE_OPEN;
}

static inline const char *gpu_gate_name(enum gpu_gate g)
{
	switch (g) {
	case GPU_GATE_OPEN: return "open";
	case GPU_GATE_PMU_UNREADABLE: return "pmu-unreadable";
	case GPU_GATE_TOP_OFF: return "pd_g3d-off";
	case GPU_GATE_CORES_OFF: return "pd_embedded_g3d-off";
	case GPU_GATE_CMU_UNREADABLE: return "cmu-unreadable";
	case GPU_GATE_MUX_OFF: return "cmu-mux-off";
	}
	return "?";
}

/* Fault-guarded 32-bit MMIO read (shim.c; host tests substitute a fake window). */
int try_read32(uintptr_t address, uint32_t *value);

void gpu_probe_cmu(void);	/* the CMU_G3D part again (read-only; prints nothing but PMU_G3D_STAT if the top domain is off) */
int gpu_probe_pmu_on(void);	/* read-only: 1 only if PMU_G3D_STAT and PMU_EMB_STAT both read with bit 0 set (printed); 0 otherwise and in non-GPU builds (gpuclk.c, review M1) */
void gpu_probe_shaders(void);	/* read-only: SHADER/TILER/L2 READY + SHADER_PWRTRANS, only if both PMU domains are on (--gpu-rail-on, before the restore) */
void gpu_probe(void);	/* prints PMU/CMU state, then GPU registers only if the gate is open */
