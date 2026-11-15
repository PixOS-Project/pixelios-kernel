/*
 * gpus2mpu.c - read-only G3D S2MPU dump (see gpus2mpu.h). Freestanding, no static state, no MMIO store of any kind (the object is audited like gpuprobe.o).
 *
 * Offsets [src: soc-gs drivers/soc/google/s2mpu/s2mpu-regs.h, branch android-gs-bluejay-6.1-android16, local copy ~/pixelios-artifacts/refs/s2mpu/s2mpu-regs.h]:
 *   CTRL0 0x00 (ENABLE bit0, INTERRUPT_ENABLE bit1, FAULT_RESP_TYPE bit2), CTRL1 0x04, CFG 0x10, MPC_CTRL 0x40, PAGE_AWARE_DECODING_0/1 0x44/0x48,
 *   FAULT_STATUS 0x2000, FAULT_PA_LOW(n) 0x2004 + n*0x20, FAULT_PA_HIGH(n) 0x2008 + n*0x20, FAULT_INFO(n) 0x2010 + n*0x20,
 *   L1ENTRY_L2TABLE_ADDR(n, m) 0x4000 + n*0x200 + m*8, L1ENTRY_ATTR(n, m) 0x4004 + n*0x200 + m*8 (n = VID, m = gigabyte index);
 *   L1 attr bits: L2TABLE_EN bit0, RD_ACCESS bit1, WR_ACCESS bit2, L2TABLE_GRANULE bits 5:4.
 * 64 one-gigabyte granules per VID (WHI_MAX_GB_GRANULES = 64, s2mpu-lib.h; gb_index = PA >> 30, s2mpu-lib.c), VID 0 or 1 only (s2mpu-lib.c:227).
 * Bases: soc.json gpu.s2mpu_g3d0..3 (gs101-b0.dts:58-69).
 */
#include <stdint.h>
#include "console.h"
#include "ramlog.h"
#include "gpuprobe.h"
#include "gpus2mpu.h"

#if SOC_GS101 && defined(GPU_S2MPU_G3D0)

#define S_CTRL0		0x000u
#define S_CTRL1		0x004u
#define S_CFG		0x010u
#define S_MPC_CTRL	0x040u
#define S_PAGE_AWARE0	0x044u
#define S_PAGE_AWARE1	0x048u
#define S_FAULT_STATUS	0x2000u
#define S_FAULT_PA_LOW(n)	(0x2004u + (n) * 0x20u)
#define S_FAULT_PA_HIGH(n)	(0x2008u + (n) * 0x20u)
#define S_FAULT_INFO(n)		(0x2010u + (n) * 0x20u)
#define S_L1_L2ADDR(n, m)	(0x4000u + (n) * 0x200u + (m) * 8u)
#define S_L1_ATTR(n, m)		(0x4004u + (n) * 0x200u + (m) * 8u)
#define S_GRANULES	64u

__attribute__((noipa)) static unsigned put_txt(char *dst, unsigned cap, unsigned pos, const char *s)
{
	while (*s && pos + 1 < cap)
		dst[pos++] = *s++;
	return pos;
}

__attribute__((noipa)) static unsigned put_hex(char *dst, unsigned cap, unsigned pos, uint64_t v, unsigned digits)
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

/* "S2MPU<i> <name> pa=XXXXXXXXX v=XXXXXXXX" or "... unreadable"; returns whether it was read */
static int rd(unsigned inst, const char *name, uintptr_t pa, uint32_t *v)
{
	char line[RAMLOG_LINE_MAX];
	unsigned p = put_txt(line, sizeof(line), 0, "S2MPU");
	int ok = try_read32(pa, v);

	p = put_hex(line, sizeof(line), p, inst, 1);
	p = put_txt(line, sizeof(line), p, " ");
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

static uintptr_t base_of(unsigned i)
{
	switch (i) {
	case 0: return GPU_S2MPU_G3D0;
	case 1: return GPU_S2MPU_G3D1;
	case 2: return GPU_S2MPU_G3D2;
	case 3: return GPU_S2MPU_G3D3;
	default:	/* the only caller loops i < 4: unreachable, say so if that ever changes (AGENTS rule 8); the read stays a fault-guarded read */
		con_str("GPU", "GPU GAP: S2MPU base_of: index beyond G3D3, reading G3D3");
		return GPU_S2MPU_G3D3;
	}
}

void gpu_s2mpu_dump(uintptr_t probe_pa)
{
	uint32_t top = 0, v;
	unsigned gb = (unsigned)(probe_pa >> 30);
	char line[RAMLOG_LINE_MAX];
	unsigned p;

	if (!try_read32(GPU_PMU_G3D_STAT, &top) || !(top & 1u)) {
		con_str("GPU", "S2MPU skipped: pd_g3d is not on (or its PMU word is unreadable)");
		return;
	}
	p = put_txt(line, sizeof(line), 0, "S2MPU probe_pa=");
	p = put_hex(line, sizeof(line), p, probe_pa, 9);
	p = put_txt(line, sizeof(line), p, " gb_index=");
	p = put_hex(line, sizeof(line), p, gb, 2);
	emit(line, p);
	for (unsigned i = 0; i < 4; i++) {
		uintptr_t b = base_of(i);

		rd(i, "CTRL0", b + S_CTRL0, &v);
		rd(i, "CTRL1", b + S_CTRL1, &v);
		rd(i, "CFG", b + S_CFG, &v);
		rd(i, "MPC_CTRL", b + S_MPC_CTRL, &v);
		rd(i, "PAGE_AWARE0", b + S_PAGE_AWARE0, &v);
		rd(i, "PAGE_AWARE1", b + S_PAGE_AWARE1, &v);
		rd(i, "FAULT_STATUS", b + S_FAULT_STATUS, &v);
		for (unsigned vid = 0; vid < 2; vid++) {
			uint64_t rdwr = 0, l2 = 0;
			unsigned nonzero = 0, bad = 0;

			rd(i, vid ? "FAULT_INFO1" : "FAULT_INFO0", b + S_FAULT_INFO(vid), &v);
			rd(i, vid ? "FAULT_PA_LOW1" : "FAULT_PA_LOW0", b + S_FAULT_PA_LOW(vid), &v);
			for (unsigned m = 0; m < S_GRANULES; m++) {
				if (!try_read32(b + S_L1_ATTR(vid, m), &v)) {
					bad++;
					continue;
				}
				if (v)
					nonzero++;
				if ((v & 6u) == 6u)		/* RD_ACCESS | WR_ACCESS */
					rdwr |= 1ull << m;
				if (v & 1u)			/* L2TABLE_EN: a 2nd-level table decides, the granule is not simply open */
					l2 |= 1ull << m;
			}
			p = put_txt(line, sizeof(line), 0, "S2MPU");
			p = put_hex(line, sizeof(line), p, i, 1);
			p = put_txt(line, sizeof(line), p, vid ? " vid1 rdwr=" : " vid0 rdwr=");
			p = put_hex(line, sizeof(line), p, rdwr, 16);
			p = put_txt(line, sizeof(line), p, " l2=");
			p = put_hex(line, sizeof(line), p, l2, 16);
			p = put_txt(line, sizeof(line), p, " nonzero=");
			p = put_hex(line, sizeof(line), p, nonzero, 2);
			p = put_txt(line, sizeof(line), p, " bad=");
			p = put_hex(line, sizeof(line), p, bad, 2);
			emit(line, p);
			if (gb < S_GRANULES) {			/* the exact entries for the granule of probe_pa */
				rd(i, vid ? "L1ATTR1" : "L1ATTR0", b + S_L1_ATTR(vid, gb), &v);
				rd(i, vid ? "L1L2ADDR1" : "L1L2ADDR0", b + S_L1_L2ADDR(vid, gb), &v);
			}
		}
	}
}

#else	/* not gs101 (or no S2MPU constants): empty stub */

void gpu_s2mpu_dump(uintptr_t probe_pa) { (void)probe_pa; }

#endif
