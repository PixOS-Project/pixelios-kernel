#pragma once
#include <stdint.h>

/*
 * gpuclk.h - opt-in GPU clock request through the audited ACPM DVFS client (build.sh --gpu-clk, implies --gpu-probe, gs101 only).
 *
 * gpu_clk(): refuse (writes nothing, prints why) unless the DVFS channel is configured, the ACPM gates layout_verified and
 * elem_unit_verified are set, the descriptor validates and both queues are empty; GET G3DL2 (ACPM idx 6) and G3D (idx 5); ask for the
 * firmware's own init rate (soc.json gpu.clk_khz = 151000 kHz) for G3DL2 and then G3D, even if GET already reports it (Linux sends
 * it at every probe; the point is to make the APM act on it); GET both back; print the CMU_G3D words again. Returns 0 only if both
 * read back the requested rate. The only writes are the ones acpm.c's audited writer makes for those two FREQ_REQ transactions;
 * this file has no MMIO writer of its own (audited: no try_write32 reference, stack-only stores). Output lines: GPU CLK ...
 * (docs/AUTOMATION.md).
 */
int gpu_clk(void);

#ifdef GPU_CLK_LEVEL
/* --gpu-clk-level only (T-50eb review F1): compare the BUCK2S / BUCK8S OUT1 uV that gpu_rails_uv() read after the
 * request with the observed fvmap uV of the asked pair; prints a `GPU GAP:` line per rail below its row (or
 * unpinned) and returns the number of gaps. No I/O but the log. */
uint32_t gpu_clk_uv_check(uint32_t b2s_uv, uint32_t b8s_uv);

/* The --boot-xnu --gpu-handover DVFS step (T-a214), called from the shim.c handover block right after the rail-on
 * result: nonzero rail_on_ok = RAIL_ON_OK asks DT pair N once and reads the rails back; zero logs a GAP. Saves
 * pstore before and after. */
void gpu_clk_handover(uint32_t rail_on_ok);
#else
#include "pstore.h"
/* No --gpu-clk-level: the handover step is only the pstore checkpoint the shim.c block always took. */
static inline void gpu_clk_handover(uint32_t rail_on_ok)
{
	(void)rail_on_ok;
	pstore_save();
}
#endif
