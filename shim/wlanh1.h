#pragma once
#include <stdint.h>
#include "console.h"
#include "pstore.h"

/*
 * wlanh1.h - H1, STRICTLY read-only probe (docs/research/90) of the two gates between the
 * BCM4389 and host RAM before the first DMA (G2, docs/research/89 section 6 P2/P3): the HSI2
 * S2MPU (+ SSMT page, raw) and the root-complex DBI Command word (bit2 BUS MASTER).
 * Runs once in a --wlan-hold image, after wlan_link_hold() returned 0 and before loader_boot()
 * (shim.c calls wlan_h1_after_hold(), which gates on that result).
 * Every read is the fault-guarded try_read32; try_write32 is not referenced (object audited
 * like gpus2mpu.o). An unreadable word prints `unreadable` and forces the verdict `stop`
 * (rule 8).
 * Output (con_str "WLAN"): `H1 S2MPU <reg> pa=.. v=..`,
 * `H1 S2MPU vid<n> rdwr=.. l2=.. nonzero=.. bad=..`, `H1 S2MPU L1ATTR<n> gb=22 pa=.. v=..`,
 * `H1 SSMT+<off> pa=.. v=..`, `H1 DBI id ..`, `H1 RC cmd ..`,
 * `H1 SUMMARY s2mpu_en=N fs=.. v0=X v1=X rc_master=N verdict=go|s2mpu|dbi|stop`.
 * FAULT_STATUS is reported but does not change the verdict (docs/research/90 section 4 last
 * row). The verdict is advisory (docs/research/90 section 4 applies it).
 * Register map [src: ~/pixelios-artifacts/refs/s2mpu/s2mpu-regs.h:3-25, 47-70; bases
 * dts-gs101-gs101-s2mpu.dtsi:18-23]; the buffer granule m = 34 is PA 0x8_8010_0000 >> 30 (the
 * UFS bounce, the G1 PA is not known yet: the 64-granule masks cover any PA).
 */
/* 1 GiB granule of the reference host-RAM PA 0x880100000 (docs/research/90 section 2 row 7) */
#define WLAN_H1_GB	34u

enum wlan_h1_verdict { H1_STOP = 0, H1_GO, H1_S2MPU, H1_DBI };

/*
 * Pure decision (docs/research/90 section 4). rd_ok: every word read; ctrl0/cmd/id raw;
 * v0,v1 = L1 attr of VID 0/1 at WLAN_H1_GB.
 */
static inline enum wlan_h1_verdict wlan_h1_decide(int32_t rd_ok, uint32_t ctrl0, uint32_t id,
						  uint32_t cmd, uint32_t v0, uint32_t v1)
{
	int32_t master = (cmd >> 2) & 1u;

	if (!rd_ok || id == 0xFFFFFFFFu)
		return H1_STOP;
	if (ctrl0 & 1u) {
		/*
		 * enforcing: both VIDs must be a plain 1 GiB RD+WR entry (bit0 L2TABLE_EN clear,
		 * bits 2:1 = 3); an L2 table or a missing direction is not decidable from H1
		 */
		if ((v0 & 7u) != 6u || (v1 & 7u) != 6u)
			return H1_S2MPU;
	}
	return master ? H1_GO : H1_DBI;
}

void wlan_h1_run(void);

/*
 * The one call shim.c makes (T-191f; the call-site audit is tools/audit_wlanh1.py, the gate is
 * tested by shim/wlanh1_host_test.c): checkpoint the hold's log, then run H1 only when
 * wlan_link_hold() returned 0 (link held), else say so (rule 8). Lives here, not in wlanh1.c,
 * so wlanh1.o keeps its audited undefined-symbol surface (no pstore_save).
 */
static inline void wlan_h1_after_hold(int32_t hold_rc)
{
	pstore_save();
	if (hold_rc == 0) {
		con_str("SECTION", "WLAN h1");
		pstore_save();
		wlan_h1_run();
		pstore_save();
	} else {
		con_hex("WIFI GAP: h1 skipped, wlan_link_hold rc (0=held)", (uint32_t)hold_rc);
	}
}

/* Fault-guarded 32-bit MMIO read (shim.c; host tests substitute a fake window). */
int try_read32(uintptr_t address, uint32_t *value);
