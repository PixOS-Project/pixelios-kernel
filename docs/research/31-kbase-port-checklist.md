# 31: kbase port checklist for the XNU Mali-G78 driver (desk research, 2026-10-23)

Host-only read of Google kbase; nothing run on hardware. Tags: **[src F:L]** read from source, **[obs doc N]** observed on hardware
(docs 27, 28), **[guess]**. No GPL code is copied; every item cites file:line.
Source: `~/pixelios-artifacts/refs/gpu-full/mali_kbase` (branch `android-gs-bluejay-6.1-android16`, commit `696fc02`, doc 30 s.5).
Short names (the `mali_kbase_` prefix is dropped; line numbers are of THIS copy and differ by a few lines from the `refs/gpu-stage2` copies doc 28 cites):

| Short | Path under `mali_kbase/` |
|---|---|
| `pm_driver.c`, `pm_backend.c` | `backend/gpu/mali_kbase_pm_driver.c`, `backend/gpu/mali_kbase_pm_backend.c` |
| `jm_hw.c`, `js_backend.c` | `backend/gpu/mali_kbase_jm_hw.c`, `backend/gpu/mali_kbase_js_backend.c` |
| `cache_policy_backend.c`, `l2_mmu_config.c` | `backend/gpu/mali_kbase_cache_policy_backend.c`, `backend/gpu/mali_kbase_l2_mmu_config.c` |
| `mmu_hw_direct.c`, `mmu_mode_aarch64.c`, `mmu.c`, `mmu_jm.c` | `mmu/mali_kbase_mmu_hw_direct.c`, `mmu/mali_kbase_mmu_mode_aarch64.c`, `mmu/mali_kbase_mmu.c`, `mmu/backend/mali_kbase_mmu_jm.c` |
| `device_hw.c`, `device_hw_jm.c` | `device/mali_kbase_device_hw.c`, `device/backend/mali_kbase_device_hw_jm.c` |
| `js.c`, `jd.c`, `hw.c`, `core_linux.c`, `mem.c`, `dummy_job_wa.c` | `mali_kbase_{js,jd,hw,core_linux,mem,dummy_job_wa}.c` |
| `issues.h`, `features.h`, `defaults.h`, `defs.h`, `mem.h` | `mali_kbase_{hwconfig_issues,hwconfig_features,config_defaults,defs,mem}.h` |
| `jm_macros.h`, `jm_enums.h`, `hw_access_regmap.h` | `hw_access/regmap/mali_kbase_regmap_jm_{macros,enums}.h`, `hw_access/mali_kbase_hw_access_regmap.h` |
| `coherency.h`, `gpu_id.h` | `../common/include/uapi/gpu/arm/midgard/gpu/mali_kbase_gpu_{coherency,id}.h` |
| `pixel_gpu_power.c` | `platform/pixel/pixel_gpu_power.c` |

## 0. Ground facts that scope everything below
- [src Kbuild:83-93] `CONFIG_SOC_GS101` set => `CONFIG_MALI_CSF_SUPPORT` is NOT defaulted on => `MALI_USE_CSF=0`: the Pixel 6a runs the **JM** (job-manager) backend. Every `#if MALI_USE_CSF` block in the files below is dead for us.
- [src gpu_id.h:29-44 + hw.c:206-211] `GPU_ID 0x92020010`: version = low 16 bits `0x0010` => major 0 (b15:12), minor 1 (b11:4), status 0 => **r0p1**, product tBEx (arch 9). Table entry `{ GPU_ID_VERSION_MAKE(0,1,0), base_hw_issues_tBEx_r0p1 }`. This closes the "decode unverified" note of doc 27 s.45.
- [src hw.c:392-413] `base_hw_issues_model_tBEx` is used ONLY when `impl_tech == SOFTWARE` (the NO_MALI model); real silicon never takes it. r0p1 does NOT fall into it. The model list differs from r0p1 only by adding `5736` and dropping `TTRX_2968_TTRX_3162`, `TTRX_921` (issues.h:399-405).
- [src features.h:95-102] tBEx features: FLUSH_REDUCTION, PROTECTED_DEBUG_MODE, IDVS_GROUP_SIZE, L2_CONFIG, CLEAN_ONLY_SAFE, FLUSH_INV_SHADER_OTHER. NOT present: TLS_HASHING, GPU_SLEEP, PBHA_HWU.
- [src pm_driver.c:63-67, Kconfig:189-191] `corestack_driver_control` = false unless `CONFIG_MALI_CORESTACK=y` (default n); the Pixel build files do not set it (only `build.bp:66`, an unrelated Soong build). [obs doc 27 s.45, s.75] confirms: `STACK_PWRON` is useless here, `SHADER_PWRON` alone gave `SHADER_READY=0x779FFF`.
- [src hw.h:36, defs.h:1194] the issue mask is kernel-internal (`hw_issues_mask` is only read through `kbase_hw_has_issue`); it is not exported to user space. A userspace driver (Mesa) must derive its own workarounds from GPU_ID/revision.

## 1. Errata table, tBEx r0p1
List = `base_hw_issues_tBEx_r0p1` [src issues.h:345-361] (14 issues). Complete grep of every `KBASE_HW_ISSUE_<name>` outside `issues.h` over the whole tree was done; rows with "none" have **zero** other hits [src: grep over gpu-full, 2026-10-23].
Scope of the verdict: bare driver = power-up, one AS, JM submit (compute on JS1, fragment on JS0 via Mesa panfrost), polled completion.

| # | Issue | Workaround in kbase | Verdict | One-line justification |
|---|---|---|---|---|
| 1 | `9435` | `js_backend.c:49-68` (only changes WHEN the software soft-stop timer runs: needs >=2 compute ctx or >=1 non-compute ctx; comment says timeouts "would have to be 4x longer" for OpenCL) | NOT NEEDED | [src js_backend.c:49-68] pure scheduler policy; our hang timeout is our own: make the compute hang timeout generous (>= 4x the graphics one) [guess] |
| 2 | `TSIX_2033` | none | UNKNOWN | [src] no kernel code touches it; not exported to user space (see s.0), so any fix lives in Mesa/blob or is a no-op for a kernel; description is not in the tree [guess]. TODO: grep Mesa `src/panfrost` and the vendor blob (tools/rea) for revision-gated code |
| 3 | `TTRX_1337` | none | UNKNOWN | same as #2: no kernel hit [src] |
| 4 | `TTRX_2968_TTRX_3162` | `pm_driver.c:4058-4059` sets `SC_VAR_ALGORITHM` (1<<29, `hw_access_regmap.h:306`) in the `SHADER_CONFIG` value that `pm_driver.c:4171-4175` writes after every reset | NEEDED | [src] one register bit, zero cost, kbase does it on every reset; effect is on shader-core varying handling (graphics) [guess]; write `SHADER_CONFIG`(0xF04) = (read value) \| 0x20000000 |
| 5 | `TTRX_921` | (a) `pm_driver.c:2809-2813`: before `SHADER_PWROFF` issue `GPU_COMMAND` CLEAN_INV_CACHES (0x08, `jm_macros.h:261`) and wait `CLEAN_CACHES_COMPLETED`; (b) `device_hw_jm.c:97-99`: re-run the PM state machine on that IRQ | NEEDED iff we ever power shaders off while the L2 stays on | [src] comment `device_hw_jm.c:89-96`: "cache line evict messages can be lost when shader cores power down" => flush L2 first. If we only ever power the whole GPU (shaders+L2) down, still cheap to flush first (kbase's own whole-GPU path flushes anyway when `l2_always_on`, `pm_driver.c:2415-2421`) |
| 6 | `TTRX_3414` | none | UNKNOWN | no kernel hit [src] |
| 7 | `TTRX_3083` | none | UNKNOWN | no kernel hit [src] |
| 8 | `TTRX_3470` | none | UNKNOWN | no kernel hit [src] |
| 9 | `TTRX_3464` | none | UNKNOWN | no kernel hit [src] |
| 10 | `TITANHW_2710` | none | UNKNOWN | no kernel hit [src] |
| 11 | `GPU2022PRO_148` | none | UNKNOWN | no kernel hit [src] |
| 12 | `TITANHW_2938` | `pm_backend.c:212-214` computes `apply_hw_issue_TITANHW_2938_wa`; consumers `mmu_hw_direct.c:562-566, 606-610` wait for `L2_PWRTRANS == 0` after an MMU flush | NOT NEEDED | [src] consumers are inside `#if MALI_USE_CSF`, and the flag additionally needs `KBASE_GPU_SUPPORTS_GPU_SLEEP` (`pm_backend.c:212-214`, itself CSF-only `:198-215`); dead on JM (s.0) |
| 13 | `KRAKEHW_2321` | none | UNKNOWN | no kernel hit [src] |
| 14 | `KRAKEHW_2151` | none | UNKNOWN | no kernel hit [src]; also in tBEx r1p0 (`issues.h:363-368`), i.e. not a silicon-revision-specific list entry |

Related issues that are NOT in the r0p1 list and so need nothing: `TTRX_3485` (dummy-job workaround, gated at `dummy_job_wa.c:222`; [obs doc 27 s.45] shim already concluded this), `TTRX_3076` (`jm_hw.c:365-371`, bus-fault reset), `T76X_3953` (`pm_driver.c:4081-4083`, tiler clock gate), `TMIX_8463` (`jm_hw.c:~948`), `GPU2017_1336` (L2 clock slow-down states `KBASE_L2_RESTORE_CLOCKS`, `pm_driver.c`), `5736` (model only, `js_backend.c:103`).
Summary: 2 NEEDED (#4, #5-conditional), 3 NOT NEEDED (#1, #12 + model list), **10 UNKNOWN** (#2, 3, 6-11, 13, 14). The 10 are all "kbase has no code for it": the risk is not in the kernel but in the compiler/job descriptors Mesa generates on r0p1 silicon [guess]. TODO(close UNKNOWN): (a) Mesa tree grep for these names; (b) reverse `libGLES_mali.so` strings for the same names; (c) treat any non-reproducible GPU fault on r0p1 as a candidate for this list.

## 2. JM job submit (as kbase does it)
Register offsets from `GPU_MALI` 0x1C500000 [src jm_enums.h:112-140]: JOB_CONTROL 0x1000, JSn block = 0x1800 + n*0x80. For n=0: HEAD 0x1800, COMMAND 0x1820, STATUS 0x1824, HEAD_NEXT 0x1840, AFFINITY_NEXT 0x1850, CONFIG_NEXT 0x1858, COMMAND_NEXT 0x1860, FLUSH_ID_NEXT 0x1870 (JS1 = +0x80, JS2 = +0x100).

### 2.1 Slot selection
| Job type (core_req) | Slot | Cite |
|---|---|---|
| `BASE_JD_REQ_JOB_SLOT` set by user | `katom->jobslot` | [src js.c:2401-2402] |
| fragment (`BASE_JD_REQ_FS`) | **JS0** | [src js.c:2404-2405] |
| compute-only, `device_nr==1` and 2 core groups | JS2 | [src js.c:2407-2410]; never for G78: Bifrost+ has 1 coherent group [src jm_hw.c:58-62, gpuprops.c:135, :232] so `num_core_groups` = popcount(L2_PRESENT) = 1 [obs doc 27: L2_PRESENT=1] |
| everything else (vertex/tiler/compute chains) | **JS1** | [src js.c:2412] |
Matches Linux panfrost (doc 28 s.4). [obs doc 28 s.11] JS_PRESENT=7, JS0_FEATURES=0x20E (SET_VALUE bit 2), JS1=0x1C9E: a WRITE_VALUE job ran on JS0.

### 2.2 Submit sequence (`kbase_job_hw_submit`, jm_hw.c:119-236), exact order
| # | Action | Register | Value | Cite |
|---|---|---|---|---|
| 0 | poll until slot free | `JSn_COMMAND_NEXT` | read until 0; timeout `JM_DEFAULT_JS_FREE_TIMEOUT` = 100000 cycles of the GPU timeout clock | [src jm_hw.c:101-117, defaults.h:352] |
| 1 | head | `JSn_HEAD_NEXT` (64-bit) | GPU VA of the first job descriptor | [src jm_hw.c:138] |
| 2 | affinity | `JSn_AFFINITY_NEXT` (64-bit) | tiler-only atom (`(core_req & (FS\|CS\|T)) == T`): 0; coherent-group requests: `shaders_avail & debug_core_mask[js] & shader_present`; else `shaders_avail & debug_core_mask[js]`; if that is 0, fall back to `shaders_avail` | [src jm_hw.c:47-96] (write at :96). Bare driver: `SHADER_READY` mask [obs doc 28: 0x779FFF]; must be a subset of READY cores (`WARN_ON` at :80-82) |
| 3 | config | `JSn_CONFIG_NEXT` | see 2.3 | [src jm_hw.c:140-203] |
| 4 | flush id (only if FLUSH_REDUCTION feature, true for tBEx) | `JSn_FLUSH_ID_NEXT` | `katom->flush_id` = value of `LATEST_FLUSH` (0x38) read when the atoms were submitted from user space, 0 if GPU off | [src jm_hw.c:205-206, :700-711, jd.c:1097, :1152] |
| 5 | go | `JSn_COMMAND_NEXT` | 1 (`JS_COMMAND_START`) | [src jm_hw.c:234, jm_macros.h:149] |
No `START_MMU` (bit 10) anywhere in kbase [src jm_macros.h:164, doc 30 s.5].

### 2.3 `JS_CONFIG_NEXT` fields and kbase's choices
Fields [src jm_macros.h:159-171]: AS number b3:0 (`cfg = kctx->as_nr`, jm_hw.c:143); START_FLUSH b9:8 (0 none, 1 clean, 2 INV_SHADER_OTHER, 3 clean+invalidate); START_MMU b10 (never set); JOB_CHAIN_FLAG b11; END_FLUSH b13:12 (0 none, 1 clean, 3 clean+invalidate); ENABLE_FLUSH_REDUCTION b14; DISABLE_DESCRIPTOR_WR_BK b15 (protected only); THREAD_PRI b19:16.
| Choice | kbase behaviour | Cite |
|---|---|---|
| FLUSH_REDUCTION | set (`1<<14`) for every non-protected atom if the feature exists and `serialize_jobs & KBASE_SERIALIZE_RESET` is clear | [src jm_hw.c:147-150] |
| START flush | `CLEAN_INVALIDATE` (3<<8) by default; `NO_ACTION` / `INV_SHADER_OTHER` (tBEx has the feature) only when the atom has `SKIP_CACHE_START` AND the slot's last ctx is the same / different | [src jm_hw.c:152-173] |
| END flush | `NO_ACTION` if atom has `SKIP_CACHE_END`; else **`CLEAN` (1<<12) because tBEx has `CLEAN_ONLY_SAFE`**; else `CLEAN_INVALIDATE` (3<<12) | [src jm_hw.c:174-179, features.h:95-102] |
| THREAD_PRI | constant 8 | [src jm_hw.c:189] |
| JOB_CHAIN_FLAG | toggled per submit per slot (`slot_rb->job_chain_flag`): first job on a slot gets the flag, the next does not, and so on; remembered per atom (`KBASE_KATOM_FLAGS_JOBCHAIN`) because soft/hard stop must name the parity | [src jm_hw.c:191-200] |
Resulting typical values (computed from the fields above; [guess] = arithmetic by hand, not run): kbase-style compute atom = `0x00085300` (flush reduction + start CI + end clean + pri 8), with the chain flag `0x00085B00`. The value proven on hardware is the more conservative `0x00083B00` [obs doc 28 s.11] (no flush reduction, end clean+invalidate). Recommendation for the bare driver: start with `0x83B00` toggling bit 11 [obs], move to the kbase value only with a test that shows the cache-flush-id write is correct.
Flush reduction semantics (what `FLUSH_ID_NEXT` means) are not documented in the tree; the only text is `hw.c:108-113` ("unclear or ambiguous ARCH spec") => [guess] keep it off (the hardware then just performs the requested flushes).

### 2.4 Completion, fault, stop, hang
| Step | What kbase does | Cite |
|---|---|---|
| detect | `JOB_IRQ_RAWSTAT`/`STATUS` (0x1000/0x100C): bit n = slot n done, bit n+16 = slot n failed | [src jm_hw.c:287-300 (`kbase_job_done`), irq_linux.c:56] |
| failed | read `JSn_STATUS` (0x1824) as the completion code; `STOPPED`(3)=soft stop, `TERMINATED`(4)=hard stop | [src jm_hw.c:303-345, jm_macros.h:185-188] |
| ack | `JOB_IRQ_CLEAR` (0x1004) <- `done & (1<<i \| 1<<(i+16))`; then read `JOB_IRQ_JS_STATE` (0x1010) to see whether another job is already running on the slot; re-check RAWSTAT (race comment) | [src jm_hw.c:374-376, :387-420] |
| soft stop | `JSn_COMMAND` (0x1820) <- `SOFT_STOP_0` (4) or `SOFT_STOP_1` (6) by the atom's chain-flag parity; after 1 tick = 100 ms (compute: also 1) | [src jm_hw.c:509-531, :540, defaults.h:137,140] |
| hard stop | `JSn_COMMAND` <- `HARD_STOP_0` (5) / `HARD_STOP_1` (7); after 50 ticks = 5 s | [src jm_hw.c:532-540, defaults.h:143,146] |
| GPU reset on hang | after 55 ticks = 5.5 s on the GPU (`reset_needed`) | [src js_backend.c:179-189, defaults.h:161,166]; tick = 100 ms [src defaults.h:128-129] |
| faults that also reset | MMU `AS_ACTIVE` stuck (`mmu_hw_direct.c:188-190`), failed MMU flush (`mmu.c:~3070`), reset prep soft-stops every slot first (`jm_hw.c:1123-1150`) |
Bare-driver translation [guess]: poll RAWSTAT with a bound; on timeout write `HARD_STOP_<parity>`, wait 100 ms for the failed bit, then SOFT_RESET (s.5.3). Do not use SOFT_STOP: it exists to time-slice contexts and re-queue a job tail (`jm_hw.c:326-345`), which a single-context driver does not do. [obs doc 28 s.4, s.8] already uses HARD_STOP_1 after 100 ms.

## 3. MMU / address space (single AS)
### 3.1 Values kbase writes to program an AS (`kbase_mmu_hw_configure`, mmu_hw_direct.c:323-359; setup values from `mmu_jm.c:34-50`, `mmu_mode_aarch64.c:54-69`)
| Register | Value | Cite |
|---|---|---|
| `ASn_TRANSCFG` (64) | `MODE=AARCH64_4K (6)` \| `PTW_MEMATTR=WRITE_BACK (2)<<24` \| `R_ALLOCATE (1)<<30` = `0x42000006`; `PTW_SH` (b29:28) = OUTER_SHAREABLE (2) ONLY if `system_coherency != COHERENCY_NONE` | [src mmu_jm.c:49, mmu_hw_direct.c:335-346, hw_access_regmap.h:117-162] |
| `ASn_TRANSTAB` (64) | PA of the L0 table, `& AS_TRANSTAB_BASE_MASK` | [src mmu_jm.c:48, mmu_hw_direct.c:349] |
| `ASn_MEMATTR` (64) | byte i = attribute i: 0 `0x88` (impl-def, WB), 1 `0x8F` (force cache all), 2 `0x8D` (write-alloc), 3 `0x88` (outer impl-def), 4 `0x8D` (outer WA), 5 `0x4C` (non-cacheable) => `0x00004C8D888D8F88` | [src mmu_jm.c:39-46, mem.h:114-157, :161-175]; matches [obs doc 28 s.2] |
| then `ASn_COMMAND` (0x..18) | 1 = UPDATE (no wait after it on JM; CSF waits) | [src mmu_hw_direct.c:355-358, hw_access_regmap.h:173-174] |
| disable | TRANSTAB=0, TRANSCFG MODE=UNMAPPED (1) (PTW bits still ORed in), same UPDATE; before that kbase flushes the whole AS (`kbase_mmu_flush_noretain(kctx,0,~0)`) | [src mmu_mode_aarch64.c:71-81, mmu.c:3182-3201] |
ASn register addresses [src jm_enums.h:426-438]: AS0 TRANSTAB 0x2400, MEMATTR 0x2408, LOCKADDR 0x2410, COMMAND 0x2418, FAULTSTATUS 0x241C, FAULTADDRESS 0x2420, STATUS 0x2428, TRANSCFG 0x2430, FAULTEXTRA 0x2438 (ASn = +0x40*n); `MMU_IRQ_RAWSTAT` 0x2000. PTE formats: see doc 28 s.3 [src mmu_mode_aarch64.c:28-44, :108-149, :180-183]; memattr index sits in PTE bits 4:2 (`get_mmu_flags`, :113-115); RW = bit 6, RO = bits 7:6 (:38-39).
Default region memattr when GPU-cached and coherency NONE = index 0 (`KBASE_MEMATTR_INDEX_DEFAULT`), uncached = index 5 [src mem.c:1166-1190, mem.h:161-175]. [obs doc 28] used index 5 only.

### 3.2 Command sequence per page-table update (JM path, arch 9)
`mmu_flush_cache_on_gpu_ctrl()` is `arch_major > 11` (false for arch 9) [src mmu.c:345-348], so the AS-command path (`kbase_mmu_hw_do_flush`, mmu_hw_direct.c:506-569) is used, NOT GPU_COMMAND flushes. Order:
| # | Step | Register / command | Cite |
|---|---|---|---|
| 0 | CPU: write the PTEs, then clean them to memory (kbase uses `dma_sync_single_for_device` EVEN when coherent, Pixel bug b/200555454) | CPU cache maintenance | [src mmu.c:559-566, :588-594] |
| 1 | `wait_ready`: poll `ASn_STATUS` bit 0 (`AS_ACTIVE`) == 0 (timeout `MMU_AS_INACTIVE_WAIT_TIMEOUT` = 50*1024*1024 timeout-clock cycles; on expiry kbase flags `mmu_unresponsive` and resets the GPU) | `ASn_STATUS` | [src mmu_hw_direct.c:168-192, defaults.h:411, hw_access_regmap.h:74-77] |
| 2 | set lock region | `ASn_LOCKADDR` (64) = `(vpfn<<12) & ~((1<<L)-1) \| (L-1)` where L = fls64(start ^ end), clamped to >= 15 on arch 9 (32 KiB; 12 only for arch >= 12) | [src mmu_hw_direct.c:89-155, :386-397, defs.h:2192-2197] |
| 3 | lock | `ASn_COMMAND` = 2 (LOCK) (preceded by an AS_ACTIVE wait inside `write_cmd`) | [src mmu_hw_direct.c:194-210, :411-424, hw_access_regmap.h:175] |
| 4 | flush + implicit unlock | `ASn_COMMAND` = 4 (`FLUSH_PT`: only L2/MMU caches, used when mapping new pages) or 5 (`FLUSH_MEM`: also GPU L1/LSC, used on unmap/remap and for `mmu_flush_invalidate` of freed memory); the flush command also unlocks the region | [src mmu_hw_direct.c:506-545, :266-272, mmu.c:2125, :3049-3061] |
| 5 | wait | poll `AS_ACTIVE` == 0 again | [src mmu_hw_direct.c:548] |
kbase uses plain `UNLOCK` (3) only on the GPU-control flush path (arch > 11) [src mmu.c:394-410, mmu_hw_direct.c:464-486]; not needed here. When a table was newly created and not yet visible to the GPU, kbase skips the flush (`KBASE_MMU_OP_NONE`, mmu.c:2518, :2864) [src].
**Minimal single-AS rule [guess, derived from the above]:** after any change to a live AS's tables: CPU clean of the PTE lines, then `LOCKADDR` (range, min 32 KiB), `COMMAND=LOCK`, `COMMAND=FLUSH_MEM` (5), poll `AS_ACTIVE`. For the first configuration (AS idle, nothing cached): TRANSTAB/MEMATTR/TRANSCFG + `UPDATE` only [obs doc 28 s.11 ran this way: `target=MAGIC`].

### 3.3 Fault handling (JM)
| Step | Action | Cite |
|---|---|---|
| detect | `MMU_IRQ_RAWSTAT`: bit n page fault of AS n, bit n+16 bus error (`MMU_PAGE_FAULT_FLAGS` = 16) | [src hw_access_regmap.h:63-69, mmu_jm.c:301-390] |
| read | `ASn_FAULTADDRESS` (64), `ASn_FAULTSTATUS`, `ASn_FAULTEXTRA` (64); bus fault is processed before a page fault of the same AS | [src mmu_jm.c:301-390] |
| kill | stop submitting, hard-stop running jobs of the ctx (`kbase_backend_jm_kill_running_jobs_from_kctx`), flush + set AS to UNMAPPED (3.1 "disable") | [src mmu_jm.c:103-196, :52-100] |
| clear | `MMU_IRQ_CLEAR` <- `PAGE_FAULT(n)` (plus `BUS_ERROR(n)` for a bus fault), then re-enable that bit in `MMU_IRQ_MASK` | [src mmu_hw_direct.c:617-690] |
kbase's growable-memory page-fault handling (`mmu.c:1229-1410`) is not needed: no JIT/growable regions in a bare driver [guess].

## 4. Cache and coherency
- **Default of `kbdev->system_coherency`**: `COHERENCY_NONE` = **31** [src core_linux.c:5320 (assignment), coherency.h:25-27 (`ACE_LITE`=0, `ACE`=1, `NONE`=31)]. It stays NONE unless the DT node has `system-coherency` OR the node/parent chain has `dma-coherent` (treated as ACE-Lite) [src core_linux.c:5326-5348]. gs101 `mali@1C500000` has neither property [src `refs/kbase-bluejay/gs101-gpu.dtsi:17-` (whole node read)]. Whether a PARENT node (`soc`) sets `dma-coherent` is **not verifiable**: `of_dma_is_coherent()` walks parents and the parent dts is not in `refs/` [guess: not set]. TODO(verify): get `gs101.dtsi` soc node.
- What NONE implies, all [src]:
  | Item | Effect | Cite |
  |---|---|---|
  | `COHERENCY_ENABLE` (0x304) | kbase writes **31** on every reset/power-up | cache_policy_backend.c:25-42, pm_driver.c:4551 |
  | `AS_TRANSCFG.PTW_SH` | stays 0 (non-shareable) | mmu_hw_direct.c:343-346 |
  | PTE shareability bits (9:8) | not set for GPU regions (`KBASE_REG_SHARE_*` only matter if ACE) | mmu_mode_aarch64.c:127-135, mem.c:1182-1188 |
  | `L2_MMU_CONFIG.ALLOW_SNOOP_DISPARITY` (b23) | not set | l2_mmu_config.c:112-117, hw_access_regmap.h:286-287 |
  | snoop/CCI SMC | not used (`current_gpu_coherency_mode != ACE`) | pm_driver.c:~4207-4215 |
- [obs doc 27 s.51] hardware reset value of `COHERENCY_ENABLE` = 0, `COHERENCY_FEATURES` (0x300) = 1 (bit 0 = ACE-Lite supported). Since `COHERENCY_ACE_LITE == 0` [src coherency.h:25], a never-written register means **ACE-Lite protocol selected**, while kbase would put it in **31 (none)**. This corrects doc 28 s.5 ("0 may mean ACE-Lite" was marked from memory; now [src]). Because every leaf in doc 28 is non-shareable/non-cacheable and `PTW_SH`=0, the observed job still worked [obs doc 28 s.11]. Port rule: write `COHERENCY_ENABLE`=31 after power-up to match kbase, and keep GPU memory non-shareable [src] + explicit CPU cache maintenance.
- CPU side rules in kbase for NONE: GPU-visible CPU memory is `dma_sync_*`'d (page tables: mmu.c:559-566) and CSF-only code maps shared pages write-combine (mem_linux.c:3442 [src]); for JM the user buffers are synced by the DMA API from user-space syncs, not by hardware. XNU equivalent: `flush_dcache64`/`DC CIVAC` around jobs [doc 30 s.3 G3b].
- GPU side: START/END flushes in `JS_CONFIG` (s.2.3) and `GPU_COMMAND` CLEAN_INV_CACHES (0x08, jm_macros.h:261; wait `CLEAN_CACHES_COMPLETED` bit 17, jm_macros.h:281) via `kbase_gpu_cache_flush_and_busy_wait` [src device_hw.c:155-210: write `GPU_COMMAND`, poll `GPU_IRQ_RAWSTAT`, clear bit 17]. All JM cache commands alias to CLEAN_INV (jm_macros.h:265-268).
- `L2_CONFIG` (0x48) is written ONLY when DT/module params override L2 size/hash [src pm_driver.c:811-871, gpuprops.c:404-427]; gs101 dtsi has no `l2-size`/`l2-hash` [src gs101-gpu.dtsi, whole file]; `kbase_pbha_write_settings` is a no-op on JM [src pbha.c:225-254]. => no `L2_CONFIG` write needed [src]; [obs doc 27 s.51] reset value 0x07130000.

## 5. Power and reset ordering
### 5.1 Platform part (not kbase core, for reference)
`gpu_pm_power_on_top_nolock`: runtime-PM get of the "top" then "cores" power domains (S2MPU only with `CONFIG_MALI_PM_RUNTIME_S2MPU_CONTROL`, ZUMA only); returns `1` ("state lost") when the TOP domain had reached OFF [src pixel_gpu_power.c:252-300]. In our port this part is the shim/ACPM rail+PMU work (docs 17, 23, 27); XNU has no client for it (doc 30 s.1).

### 5.2 Power-up (kbase_pm_clock_on, pm_driver.c:3702-3830) -> register order
| # | Step | Register write | Cite |
|---|---|---|---|
| 1 | platform power on (rail, PD) returns STATE_LOST (1) when the domain was off | - | [src pm_driver.c:3746, pixel_gpu_power.c:285] |
| 2 | `kbase_pm_init_hw(PM_ENABLE_IRQS)`: disable+clear all IRQs | `GPU_IRQ_MASK`=0, `GPU_IRQ_CLEAR`=all, `JOB_IRQ_MASK`=0, `JOB_IRQ_CLEAR`=~0, `MMU_IRQ_MASK`=0, `MMU_IRQ_CLEAR`=~0 | [src pm_driver.c:3616-3647, :4490] |
| 3 | soft reset (section 5.3) | `GPU_IRQ_MASK`=RESET_COMPLETED(bit 8); `GPU_COMMAND`=1; wait | [src pm_driver.c:4286-4366, :4505] |
| 4 | quirks (once detected: `PM_HW_ISSUES_DETECT`) | `JM_CONFIG` 0xF00 = read \| (IDVS_GROUP_SIZE 0xF << 16) -> **0x000F0000** from reset 0; `SHADER_CONFIG` 0xF04 = read \| 1<<29 -> **0x20000000**; `TILER_CONFIG` 0xF08 = read (no change on tBEx); `L2_MMU_CONFIG` 0xF0C = read with b10:5 and b16:12 cleared -> **0** from reset 0 | [src pm_driver.c:3995-4047, :4048-4066, :4069-4083, :4171-4187, l2_mmu_config.c:66-67, :79-110]; [obs doc 27 s.51] all four read 0 at reset |
| 5 | coherency | `COHERENCY_ENABLE` 0x304 = 31 | [src pm_driver.c:4551, cache_policy_backend.c:42] |
| 6 | enable IRQs (we poll, so skip) | `GPU_IRQ_CLEAR`/`MASK`, `JOB_IRQ_CLEAR`/`MASK`=~0, `MMU_IRQ_CLEAR`/`MASK`=~0 | [src pm_driver.c:3569-3600, :4563] |
| 7 | restore the AS | s.3.1 | [src pm_driver.c:3777-3781, ctx_sched.c:266] |
| 8 | power on tiler (this also powers the L2 on JM) | `TILER_PWRON` 0x190 <- `TILER_PRESENT`; if `L2_PRESENT != 1` also `L2_PWRON` 0x1A0 <- `L2_PRESENT & ~1` | [src pm_driver.c:1840-1851 (`need_tiler_control` is true on JM), :2161-2203; [obs doc 27 s.33] L2_PRESENT=TILER_PRESENT=1] |
| 9 | wait | poll `L2_READY` (0x160) == `L2_PRESENT`, `L2_PWRTRANS` (0x220) == 0, `TILER_READY` (0x150) != 0 / `TILER_PWRTRANS` == 0; kbase takes `POWER_CHANGED_ALL` IRQ (bit 10) | [src pm_driver.c:2206-2220, :1896-1920, jm_macros.h:279] |
| 10 | (only if corestack control) `STACK_PWRON` | skipped | [src pm_driver.c:2646-2649]; [obs doc 27 s.75] harmful here |
| 11 | power on shaders | `SHADER_PWRON` 0x180 <- desired mask (`shaders_avail`, = `SHADER_PRESENT` unless the core mask restricts) | [src pm_driver.c:2656-2660; obs doc 27: 0x779FFF] |
| 12 | wait | poll `SHADER_READY` (0x140) == mask and `SHADER_PWRTRANS` (0x200) == 0 | [src pm_driver.c:2671-2676] |
No `GPU_COMMAND` is written after the power-on completes (all `GPU_COMMAND` writes in pm_driver.c: lines 503, 2420, 2811, 4305, 4421, 4455, 4602 = cache clean, soft/hard reset, protected mode, cycle counter) [src grep]. kbase also does `kbase_pm_l2_config_override` + `kbase_pbha_write_settings` just before step 8, both no-ops for us (s.4).

### 5.3 Reset (`GPU_COMMAND` SOFT_RESET)
- **Does:** value 0x01 at `GPU_COMMAND` (0x30): "Stop all external bus interfaces, and then reset the entire GPU" [src jm_macros.h:248-250]. Completion = `GPU_IRQ_RAWSTAT` bit 8 `RESET_COMPLETED` [src jm_macros.h:276]. kbase unmasks only that bit first (pm_driver.c:4331-4346), waits `RESET_TIMEOUT` = 500 ms (defs.h:84); if the bit is not set it writes `HARD_RESET` (0x02, pm_driver.c:4418-4425) for another 500 ms, then asks the platform for a power-cycle (`pixel_gpu_power.c:609-621`) [src].
- **Effects kbase assumes:** cores/L2 become unavailable ("The cores should be made unavailable due to the reset", pm_driver.c:4491-4497), AS settings must be restored (jm_hw.c:1000), config registers rewritten (pm_driver.c:4550-4551), IRQs re-enabled (jm_hw.c:1004), power state machine restarted to power L2+shaders again (jm_hw.c:1015-1020) [src]. [obs doc 27 s.24] `GPU_IRQ_RAWSTAT` = 0x100 after the rail came up: the platform power-up itself completed a reset; nobody acked it (the shim never issued SOFT_RESET).
- **When kbase issues it** [src]: (a) every power-up that lost state (`pm_driver.c:3746-3764, :4505`); (b) job-slot hang past 5.5 s or hard stop not draining (`js_backend.c:179-189`, `jm_hw.c:875-1020`, reset call at :992); (c) MMU `AS_ACTIVE` stuck (`mmu_hw_direct.c:188-190`); (d) MMU flush error (`mmu.c:~3070`); (e) protected-mode exit (`pm_driver.c:4459-4463`, n/a); (f) bus fault only with TTRX_3076 (n/a).
- Bare-driver rule [guess]: issue SOFT_RESET once at first XNU bring-up ONLY if power state is unknown (if the shim leaves cores ready, a reset throws the L2/cores away, then s.5.2 steps 4-12 must be redone); always after a hard-stop that does not drain the slot.

### 5.4 Power-down order
| # | Step | Register | Cite |
|---|---|---|---|
| 1 | no job running (`JSn_COMMAND_NEXT`==0, `JOB_IRQ_JS_STATE`==0) | - | [src jm_hw.c:101-117, :376] |
| 2 | (TTRX_921) `GPU_COMMAND`=0x08 (CLEAN_INV_CACHES), wait `GPU_IRQ_RAWSTAT` bit 17, clear it | `GPU_COMMAND` | [src pm_driver.c:2805-2822, device_hw.c:155-210] |
| 3 | shaders off | `SHADER_PWROFF` 0x1C0 <- `SHADER_READY` | [src pm_driver.c:2839-2847] |
| 4 | wait `SHADER_READY`==0 and `SHADER_PWRTRANS`==0 | - | [src pm_driver.c:2855-2860] |
| 5 | (corestack only) `STACK_PWROFF` | skipped | [src pm_driver.c:2858-2862] |
| 6 | L2 (and tiler) off, only after shaders are off: `L2_PWROFF` 0x1E0 <- `L2_PRESENT` ("Powering off the L2 will also power off the tiler") | `L2_PWROFF` | [src pm_driver.c:2397-2412 (the TILER_PWROFF branch is CSF-only), :2338-2345] |
| 7 | wait `L2_READY`==0 and `L2_PWRTRANS`==0 | - | [src pm_driver.c:2424-2435] |
| 8 | disable IRQs (mask + clear all three blocks), then platform removes domains | as step 2 above | [src pm_driver.c:3616-3647, :3840-3866] |
[obs doc 27] the shim's OFF sequence follows 3,6 and read `down=ok`; [obs doc 28 s.11b] a 9.5 s power-off was seen once (unexplained; doc 30 Q3).

## 6. Port checklist (ordered, minimal)
1. Read `GPU_ID`; require 0x92020010; derive r0p1 [src gpu_id.h, hw.c:209]. Refuse other revisions until their issue lists are read.
2. If power state is unknown: mask+clear IRQs, SOFT_RESET, wait bit 8 (500 ms), else skip.
3. Write `JM_CONFIG`=0x000F0000, `SHADER_CONFIG`=read\|0x20000000, `TILER_CONFIG`=read, `L2_MMU_CONFIG`=read&~(b10:5\|b16:12); `COHERENCY_ENABLE`=31.
4. `TILER_PWRON`, poll L2/TILER ready; `SHADER_PWRON`, poll `SHADER_READY`.
5. AS0: TRANSCFG `0x42000006`, TRANSTAB, MEMATTR `0x00004C8D888D8F88`, UPDATE; leaves tables at index-5 (non-cacheable) memory; CPU cache clean before every table change.
6. Submit per s.2.2 on JS1 (compute/vertex/tiler) and JS0 (fragment), `CONFIG_NEXT`=`0x83B00`-style with chain-flag toggle; poll `JOB_IRQ_RAWSTAT`; ack; on timeout hard-stop then reset.
7. After a table change: LOCKADDR, LOCK, FLUSH_MEM, wait (s.3.2).
8. Before powering shaders off: CLEAN_INV_CACHES (TTRX_921).

## 7. Open items (could not be resolved from the tree) -- `grep -n TODO` lists them
- TODO(errata-2033-1337-3414-3083-3470-3464-2710-148-2321-2151): ten issues with no kbase code; description and any userspace/compiler workaround unknown. Needs Mesa grep and blob strings (tools/rea).
- TODO(flush-reduction): semantics of `ENABLE_FLUSH_REDUCTION` + `FLUSH_ID_NEXT`/`LATEST_FLUSH` are not documented in the tree; recommended to stay off until measured.
- TODO(clean-only-safe): why `END_FLUSH_CLEAN` instead of `CLEAN_INVALIDATE` is safe on tBEx is only implied by the feature name; hardware proof exists only for CLEAN_INVALIDATE (doc 28 s.11).
- TODO(dma-coherent-parent): parent `soc` node of the gs101 GPU not in `refs/`, so `dma-coherent` inheritance is unverified (s.4).
- TODO(hw-reset-value-after-xnu): reset values of `JM_CONFIG`/`SHADER_CONFIG`/`TILER_CONFIG`/`L2_MMU_CONFIG` were read as 0 [obs doc 27 s.51] before any kbase write; whether the SHADER_CONFIG bit 29 (TTRX_2968) matters for compute is unknown.
- TODO(tick-clock): kbase timeouts are in GPU timeout-clock cycles (`kbase_get_timeout_ms`, time.c:361); the cycle->ms scaling (`mali_kbase_time.c`) was not followed to the actual frequency; use wall-clock bounds in the XNU driver.
- TODO(fragment-dependencies): kbase orders FS-after-tiler through atom dependencies (`jd.c`, not read in full); a bare driver must serialize JS1 then JS0 itself [guess].
- TODO(idvs-group-size): `JM_CONFIG` IDVS group size 0xF is kbase's default [src pm_driver.c:4025-4038]; effect on Mesa's IDVS jobs not checked.
