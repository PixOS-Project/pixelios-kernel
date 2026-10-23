# 30: GPU hand-over to XNU and the road to a GPU-rendered frame (plan, 2026-10-23)

Desk plan, nothing run. Written on branch `wip/gpu-accel` (own worktree, the Wi-Fi session keeps `wip/w3a-wlan-link`).
Tags: **[obs]** observed on hardware (docs 27, 28), **[src]** read from source, **[guess]**.

## 1. Where we are
- [obs] Shim (pre-XNU, MMU off) can enable the shader rail (ACPM, S2MPG11 BUCK2S), power the cores (SHADER_READY 0x779FFF), program AS0
  and run a Valhall WRITE_VALUE job; S2MPU is disabled after ABL, so GPU DMA to RAM is not blocked (doc 28 s.10-11).
- [src] No XNU patch has an ACPM client (only `0097` reads the fvmap), so **XNU cannot switch the rail or change clocks**; only the shim can.
  XNU maps MMIO with `ml_io_map` (as the UFS and framebuffer patches do) and could drive the Mali registers (0x1C500000, 0x5000) itself.
- [src] IRQs: SPI JOB 420, MMU 421, GPU 419 (doc 17 s.2). Polling avoids them for the first stages.

## 2. Open questions that decide the design (each is one experiment or one reversing job)
| # | Question | How to answer | Gate |
|---|---|---|---|
| Q1 | Does the powered GPU (rail on, cores ready) survive the shim -> XNU jump? | shim option leaves rail+cores on (no restore); XNU early patch reads `GPU_ID` + `SHADER_READY` via `ml_io_map`, logs over the USB UDP log | new shim writer = `wip/*` branch + Opus review before first run |
| Q2 | Is the S2MPU still open when XNU runs (ABL state vs. later EL3 code)? | same patch: read S2MPU CTRL0 of the four blocks, then re-run the WRITE_VALUE job from XNU on an XNU-owned page | Q1 |
| Q3 | The 9.5 s power-off in run job1 (doc 28 s.11b): idle slot wait, or a hung state? | re-run `--gpu-job` once and time each phase; only if it recurs does it matter for Q1 (we stop powering off) | hardware |
| Q4 | Which renderer does CoreAnimation use today? (doc 18 s.0 item 1 is a guess) | one backboardd sample (`CA::OGL::SW::*` frames) in a batched run (AP-05) | hardware |
| Q5 | Can Mesa panfrost (v9) be cross-built for iOS with what this host has? | doc 18 s.3.2: blocked on no Mach-O linker (lld) and mesa-clc (LLVM + SPIRV-Translator); reverse the sailfish `build` scripts for the exact flags | host only |
| Q6 | Page size: XNU pages are 16 KB, Mali tables are 4 KB-granule | sailfish solved it by mapping 16 KB kernel pages as 4 x 4 KB leaves (`sweet_gpu_vm_map`, patch 0138) [src]; same here, the shim job already uses 4 KB tables [obs] | none |

## 3. Staged path (each stage ends with a device result, tagged with run label)
- **G3a** Q1+Q2 as above: `GPU_ID` read from inside XNU. Smallest possible XNU patch (bsd/dev, `ARM64_BOARD_CONFIG_SWEET` guard).
- **G3b** WRITE_VALUE job from XNU on kernel memory (cache: XNU maps RAM cacheable, so `flush_dcache64` around the job, as sailfish `vm_cache` does [src]).
- **G3c** `SweetGPU` IOService + user client (copy the structure of sailfish `SweetGPU.cpp`/`sweet_gpu.c`, patches 0138/0140/0150; the Adreno core, ~2.6k lines, does not carry over). Job-manager ioctl surface = the 11 `DRM_IOCTL_PANFROST_*` set that `pan_kmod` needs (doc 18 s.2.2-2.3).
- **G3d** Mesa panfrost gallium + EAGL shim into backboardd (doc 18 s.1-2); first compute job from a Mesa-compiled blob, then a GLES clear.
- **G3e** CA picks its GLES renderer and presents to the framebuffer; measure fps (X1) against the CPU-rendered baseline.

## 4. Order of work now
1. Q5 and the userland build (host only, no phone, no shim writer): this is the long pole and can run while the phone belongs to the Wi-Fi session.
2. Write the G3a shim option and XNU patch on `wip/gpu-accel`; do not run until the Opus review, and not while the Wi-Fi session holds the phone.
3. Q3/Q4 ride along on the next batched hardware run.

## 5. Full Google kbase read (2026-10-23, host only)
Source: `android.googlesource.com/kernel/google-modules/gpu`, branch `android-gs-bluejay-6.1-android16`, commit `696fc02` (local, not in git: `~/pixelios-artifacts/refs/gpu-full`). Closes or narrows doc 28 s.9:
- [src] `JS_CONFIG_START_MMU` (bit 10) is defined (`hw_access/regmap/mali_kbase_regmap_jm_macros.h:164`) and **never used** anywhere else in kbase: not setting it is what Linux does (doc 28 s.9 item 1 closed).
- [src] `CONFIG_MALI_PM_RUNTIME_S2MPU_CONTROL` is only added for `CONFIG_SOC_ZUMA` (Pixel 8; `mali_kbase/Kbuild:95-96`), not gs101: Linux does not manage the G3D S2MPU through kbase on the Pixel 6a; with the observed disabled state (doc 28 s.10) DMA is open (item 4 narrowed; whether another driver programs it later is not known).
- [src] gs101 DT node has no `system-coherency` property (`gs101-gpu.dtsi`), so kbase keeps its default coherency (not read to the default's definition yet: `TODO(verify)` before ever enabling shareable attributes). Keeping non-shareable/non-cacheable GPU mappings plus explicit CPU cache maintenance stays correct either way.
- [src] GPU_ID 0x92020010 = arch 9.2, product (9,2) = `GPU_ID_PRODUCT_TBEX`, version r0p1 (`mali_kbase_hw.c:206-211`). Features for tBEx: FLUSH_REDUCTION, PROTECTED_DEBUG_MODE, IDVS_GROUP_SIZE, L2_CONFIG, CLEAN_ONLY_SAFE, FLUSH_INV_SHADER_OTHER (`mali_kbase_hwconfig_features.h:95-102`). Errata for r0p1 (`mali_kbase_hwconfig_issues.h:345-`): 9435, TSIX_2033, TTRX_1337, TTRX_2968_TTRX_3162, TTRX_921, TTRX_3414, TTRX_3083, TTRX_3470, TTRX_3464, TITANHW_2710, GPU2022PRO_148, TITANHW_2938 (+ list continues; read the rest before the driver is written). Each needs its workaround located in `mali_kbase*` before the XNU driver can claim to be complete (rule 7).
- Tooling note: kbase JM job submit/cache-flush/reset paths to port are `backend/gpu/mali_kbase_jm_hw.c` (config bits at :148-205, flush reduction :704), `mmu/mali_kbase_mmu_hw_direct.c`, `mmu/mali_kbase_mmu_mode_aarch64.c`.

## 6. Host-only work queue (no phone)
1. Finish the r0p1 errata table: for each issue, the kbase file:line of the workaround and whether a bare WRITE_VALUE/compute driver needs it.
2. Write the G3a shim option and XNU patch (stay local, unrun).
3. Mesa: Q5 CI job definition; verify `pan_kmod` panfrost-backend ioctls against our user-client surface.
4. Vendor blob (Pixel `libGLES_mali.so` from the factory image) is not usable on iOS but can be used as a behavioural reference with `tools/rea`: not started, only if Mesa lacks something for tBEx.
