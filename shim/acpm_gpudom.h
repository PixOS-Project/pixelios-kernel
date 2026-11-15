#pragma once
#include <stdint.h>

/*
 * acpm_gpudom.h - which ACPM DVFS domains are the GPU clocks (G3D / G3DL2, soc.json acpm.dvfs -> ACPM_DVFS_GPU_IDXS). Included by acpm.c only.
 * dvfs_set() (acpm.c) lets a GPU domain through only for acpm_dvfs_reassert (GPU_CLK images) and refuses it for acpm_dvfs_set, which is the CPU client
 * (review gpu-power-review-1 L1). Pure function, no I/O.
 */
#ifdef ACPM_DVFS_GPU_IDXS
static inline uint32_t dvfs_is_gpu(uint32_t dom)
{
	static const uint32_t gpu[] = { ACPM_DVFS_GPU_IDXS };

	for (uint32_t k = 0; k < sizeof(gpu) / sizeof(gpu[0]); k++)
		if (gpu[k] == dom)
			return 1;
	return 0;
}
#else
static inline uint32_t dvfs_is_gpu(uint32_t dom)
{
	(void)dom;
	return 0;
}
#endif
/* The memory-path domains MIF (0) / INT (1) (T-5030): requested only by acpm_dvfs_mif_top (MIF_CLK images), never by the CPU or GPU callers. */
#ifdef ACPM_DVFS_MEM_IDXS
static inline uint32_t dvfs_is_mem(uint32_t dom)
{
	static const uint32_t mem[] = { ACPM_DVFS_MEM_IDXS };

	for (uint32_t k = 0; k < sizeof(mem) / sizeof(mem[0]); k++)
		if (mem[k] == dom)
			return 1;
	return 0;
}
#else
static inline uint32_t dvfs_is_mem(uint32_t dom)
{
	(void)dom;
	return 0;
}
#endif
/* The ONE rate a MIF_CLK image may ask MIF for (class 2): the table top row, or with --mif-clk-level the row MIF_CLK_LEVEL_KHZ that tools/boards.py mif_clk_level
 * validated (build.sh passes only its output). The generated ACPM_DVFS_MIF_LEVEL_OK() lists the gs101.dtsi mif_int_map rows, so a rate that is not a row is a compile
 * error here, on top of build.sh, dvfs_cls_ok (exact match) and dvfs_rate_ok (the firmware table of acpm.c). */
#ifdef MIF_CLK_LEVEL_KHZ
#ifndef MIF_CLK
#error "MIF_CLK_LEVEL_KHZ needs MIF_CLK"
#endif
#define ACPM_DVFS_MIF_REQ_KHZ	MIF_CLK_LEVEL_KHZ
_Static_assert(ACPM_DVFS_MIF_LEVEL_OK(MIF_CLK_LEVEL_KHZ), "MIF_CLK_LEVEL_KHZ must be a MIF table row (gs101.dtsi mif_int_map)");
#else
#define ACPM_DVFS_MIF_REQ_KHZ	ACPM_DVFS_MIF_KHZ
#endif
/* Who may ask what (cls: 0 = CPU client acpm_dvfs_set, 1 = GPU acpm_dvfs_reassert, 2 = memory path acpm_dvfs_mif_top: MIF at ACPM_DVFS_MIF_REQ_KHZ only (the table top row, or the --mif-clk-level row)). */
static inline int dvfs_cls_ok(uint32_t dom, uint32_t khz, int cls)
{
	if (cls < 0 || cls > 2 || dvfs_is_gpu(dom) != (cls == 1) || dvfs_is_mem(dom) != (cls == 2))
		return 0;
	return cls != 2 || (dom == ACPM_DVFS_MIF_IDX && khz == ACPM_DVFS_MIF_REQ_KHZ);
}
