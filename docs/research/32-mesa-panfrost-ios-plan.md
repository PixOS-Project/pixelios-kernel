# 32: Mesa Panfrost for iOS on the Pixel 6a (Mali-G78): runtime interface, model table, build recipe, CI draft (desk research, 2026-10-23)

Desk research only. No phone, `adb` or `fastboot` was touched, nothing was committed or pushed, no Apple SDK/IPSW content is quoted.
Answers the host-only queue item 3 of doc 30 ("Mesa: Q5 CI job definition; verify `pan_kmod` panfrost-backend ioctls against our user-client
surface"). Builds on doc 18 (userland plan) and doc 30 (hand-over plan). Companion draft workflow (not active):
`userland/tools/ci/mesa-panfrost-ios.yml.draft`.

Tags: **[src file:line]** read from the cited file, **[obs]** observed (a command I ran or a measured value), **[guess]** not verified.
All Mesa `file:line` are in the tree `mesa-26.2.5` (`/tmp/gpu18/mesa-src`, `VERSION` = 26.2.5 [obs]; tarball sha256
`794b3d1729075dae7ebd47c7283b7ca0cdb5dec668f7f11cc0a5d82f6138a0a5` computed locally from the file fetched 2026-10-22, **not** compared with an
upstream checksum: archive.mesa3d.org returned an HTML error page for `.sha256`/`.sha256sum` [obs]). Paths are relative to `src/` in that tree
unless they start with `meson` or `include/`. The tree has no `.git`, so there is no commit id; doc 18's pin `f1fbde1c` (main) was re-read for
`pan_model.c` only (fetched raw from gitlab [obs]).

## 0. Summary and corrections to doc 18 / doc 30

1. **The Pixel 6a's GPU is NOT recognised by Mesa (neither 26.2.5 nor `f1fbde1c`).** `GPU_ID 0x92020010` (doc 30 s.5, kbase) decodes to
   `PAN_PROD_ID(9, 2, 2)`; Mesa's table has `(9,0,1)`, `(9,0,3)` (G57) and, on main only, `(9,2,4)` (G68), nothing at `(9,2,2)`. With no model,
   `panfrost_open_device` fails at `pan_device.c:47` and the screen is refused. Doc 18 s.1.7/s.2.1 ("`pan_model.c:93` G68/G78 row") is wrong for the G78.
   One-row fix in s.3.
2. **Panfrost cannot import by KMS handle**: `panfrost_resource_from_handle` asserts `WINSYS_HANDLE_TYPE_FD` (`gallium/drivers/panfrost/pan_resource.c:360`). Doc 18
   s.2.3 said a `WINSYS_HANDLE_TYPE_KMS` branch exists (`pan_resource.c:443-447`); in this tree the KMS branch is only in the *export* function (`pan_resource.c:490-492`).
   The IOSurface import therefore needs a fake **prime fd** (s.2.4), unlike sailfish/freedreno.
3. **Panfrost needs working DRM syncobjs**; sailfish returned `-ENOSYS` for all of them (`tools/sweetgl/sweet_drm.c:519-529`), which works for freedreno but
   makes `pan_context_create` fail here (`gallium/drivers/panfrost/pan_context.c:1111-1118`). Syncobj emulation can be 100% userland plus one kernel completion counter (s.2.5).
4. **Four Mesa patches are unavoidable** for Darwin, none exists upstream: fake `xf86drm.h` + meson gating, `os_mmap`/`os_munmap` redirect,
   `eventfd/timerfd` guard in `panfrost_kmod.c:12-13`, and the G78 model row (s.5).
5. **The host-tool stage must also build panfrost code on macOS**, so the libdrm shim must exist in stage 1 too (s.4.3). The Linux mesa_clc path of doc 18 s.3.2 (apt LLVM)
   cannot be used for a cross build run on a Mac runner: the tools must run on the machine that does the iOS cross build.
6. Minimum driver version to report from the emulated `drmGetVersion`: **"panfrost" 1.3** keeps the ioctl set to six calls (s.2.2) [guess: no gallium path needs >= 1.4].

## 1. Sources

| Short | What | Pin |
|---|---|---|
| `mesa` | Mesa release tarball | `mesa-26.2.5`, `/tmp/gpu18/mesa-src`, sha256 above |
| `mesa-main` | only `src/panfrost/model/pan_model.c` | gitlab raw at `f1fbde1c931802aa42b95df19a2e5f4a3e280c6b`, copy `/tmp/gpu32/pan_model_f1fb.c` [obs] |
| `sailfish` | `~/pixelios-artifacts/refs/ios-sailfish/tools/sweetgl/` | local clone (Adreno, freedreno) |
| `flame` | `~/pixelios-artifacts/refs/ios-flame/` | local clone (Turnip = Mesa Vulkan Adreno, "darwin" KMD) |
| `hackios` | `~/pixelios-artifacts/refs/hackios-xnu-userland/` | local clone (Mesa 25.0.7, freedreno + softpipe + OSMesa) |
| `kbase` | GPU_ID facts | doc 30 s.5 (`gs101` kbase `696fc02`) |

## 2. The `pan_kmod` panfrost backend at runtime (requirement 1)

### 2.1 Layering [src]

`panfrost_create_screen(fd, config, ro)` (`gallium/drivers/panfrost/pan_screen.c:1008`) -> `panfrost_open_device` (`pan_device.c:32`) ->
`pan_kmod_dev_create(fd, ...)` (`pan_device.c:36`) -> `drmGetVersion(fd)` (`panfrost/lib/kmod/pan_kmod.c:54`) -> table lookup **by driver name string**
(`pan_kmod.c:18-29` table with `"panfrost"` at :23, matched at :72) -> `panfrost_kmod_dev_create` (`panfrost_kmod.c:258`). Every kmod call goes through
`drmIoctl` (`pan_kmod.h:624-628`, macro `pan_kmod_ioctl`). The winsys wrapper `panfrost_drm_screen_create` does `os_dupfd_cloexec(fd)` first
(`gallium/winsys/panfrost/drm/panfrost_drm_winsys.c:124`), so the "device fd" must be a real, dup-able fd (sailfish used `/dev/null`: `sweet_drm.c:19,75` [src sailfish]).

### 2.2 Calls actually made on a JM (v9) GPU, in the order the first frame needs them

"Selector" = a proposed `SweetMali` IOKit user-client method (names are [guess]: sailfish's `SweetGPU` has the same shape, selectors 10-16, doc 18 s.1.6/s.2.3).
Structs are the `include/drm-uapi/panfrost_drm.h` layouts (Linux, packed by natural alignment; the dylib-side emulation decodes `request & 0xff - 0x40`
like `sweet_drm.c:436-449`). The user client takes plain C structs via `IOConnectCallStructMethod`; the DRM struct can be passed through unchanged.

| # | Mesa call (libdrm API / ioctl) | Mesa file:line | Struct (in -> out) | Proposed selector | Notes |
|---|---|---|---|---|---|
| 1 | `drmGetVersion` / `drmFreeVersion` | `pan_kmod.c:54,80` | `drmVersion{name,major,minor}` | none (userland constant) | name must be `"panfrost"`, version >= 1.1 (`panfrost_kmod.c:261`). 1.3 enables timestamps (`:210`), 1.4 labels (`:635`), 1.5 priorities, 1.6 WB-mmap/coherency/QUERY_BO_INFO (`:243,383`) |
| 2 | `DRM_IOCTL_PANFROST_GET_PARAM` x ~22 | `panfrost_kmod.c:87` (helper), 105-247 | `drm_panfrost_get_param{u32 param,u32 pad,u64 value}` | `getParams` (one struct-out call returning all values) | params: `GPU_PROD_ID`(=GPU_ID>>16 = 0x9202) `GPU_REVISION`(=GPU_ID&0xffff = 0x0010) (`:186-188`), `SHADER_PRESENT` `TILER_FEATURES` `MEM_FEATURES` `MMU_FEATURES` `L2_FEATURES` `TEXTURE_FEATURES0..3` `AFBC_FEATURES` (`:190-206`), `MAX_THREADS` `THREAD_MAX_WORKGROUP_SZ` `THREAD_FEATURES` `THREAD_TLS_ALLOC` (`:105,134,139,173`), `SYSTEM_TIMESTAMP_FREQUENCY` (`:213`), optional `SYSTEM_TIMESTAMP` (`:626`), `ALLOWED_JM_CTX_PRIORITIES` (`:227`, optional), `SELECTED_COHERENCY` (`:245`, only if >= 1.6). Values come from kbase register reads (doc 30); a failed required query silently returns 0 in a release build (`assert` is compiled out with `-Db_ndebug=true`) so supply all. |
| 3 | `DRM_IOCTL_PANFROST_CREATE_BO` | `panfrost_kmod.c:337` | `{u32 size,u32 flags,u32 handle,u32 pad,u64 offset}` flags: `NOEXEC=1`,`HEAP=2`,`WB_MMAP=4` | `boNew` | out: GEM-like handle and **`offset` = GPU VA chosen by the kernel**. Size is u32 (`:321`). `HEAP` = tiler heap (s.2.6). Non-`NOEXEC` BOs hold shader code: the Mali MMU entry must be executable. |
| 4 | `drmCloseBufferHandle` (= `DRM_IOCTL_GEM_CLOSE`) | `panfrost_kmod.c:356`, `pan_kmod.c:192` | `drm_gem_close{u32 handle}` | `boClose` | sailfish did the same (`sweet_drm.c:233,441`) |
| 5 | `DRM_IOCTL_PANFROST_MMAP_BO` | `panfrost_kmod.c:418` (via `bo_get_mmap_offset`, `pan_kmod.h:760`) | `{u32 handle,u32 flags,u64 offset}` | `boMap` (IOConnectMapMemory of the BO into the task) | Linux returns a fake mmap offset. **Return the mapped user address as `offset`** and redirect the `mmap` (s.5 patch B). CPU mapping must be write-combine/uncached (no cache ops needed at version 1.3: `pan_kmod_queue_bo_map_sync` returns early when `WB_MMAP` is not set, `pan_kmod.c:251`) |
| 6 | `os_munmap` of a BO mapping | `gallium/drivers/panfrost/pan_bo.c:346` | - | `boUnmap` (or implicit at `boClose`) | needs the redirect too (patch B) |
| 7 | `DRM_IOCTL_PANFROST_GET_BO_OFFSET` | `panfrost_kmod.c:373` (only on import, `bo_import`) | `{u32 handle,u32 pad,u64 offset}` | `boGetOffset` | the VA of an imported BO. For created BOs the VA arrives in CREATE_BO (`:342`) and `vm_bind` copies it (`:607`) |
| 8 | `vm_create` / `vm_bind` / `vm_destroy` | `panfrost_kmod.c:522,553,564` | none (userland objects) | none | pure bookkeeping: one VM per device, only AUTO_VA, whole-BO map, no sparse. `PANFROST_KMOD_VA_START 0x2000000` .. `VA_END 1<<32` (`:509-517`): **all GPU VAs must be < 4 GiB** |
| 9 | `DRM_IOCTL_PANFROST_SUBMIT` | `gallium/drivers/panfrost/pan_jm.c:156` (build `:71-153`) | `{u64 jc,u64 in_syncs,u32 in_sync_count,u32 out_sync,u64 bo_handles,u32 bo_handle_count,u32 requirements,u32 jm_ctx_handle,u32 pad}` requirements: `FS=1`,`CYCLE_COUNT=2` | `submit` (struct-in with trailing arrays, or two calls: `submit` + inline bo list) | one call per job chain; a batch does up to two (`jm_submit_batch`, `pan_jm.c:190-230`: vertex/tiler chain, then fragment chain with `PANFROST_JD_REQ_FS`) - the **fragment chain must run after the tiler chain**; Linux panfrost orders them through implicit BO fences (comment `pan_jm.c:180-188`). In one in-order queue this is free. out_sync/in_syncs are syncobj handles (s.2.5). `bo_handles` always contains the tiler heap and the sample-positions BO (`pan_jm.c:138-147`) |
| 10 | `DRM_IOCTL_PANFROST_WAIT_BO` | `panfrost_kmod.c:440` (called from `pan_bo.c:158`) | `{u32 handle,u32 pad,s64 timeout_ns}`: **absolute** ns | `boWait` (or wait on the BO's last-submit seqno) | clock = `os_time_get_nano()` = `timespec_get(TIME_MONOTONIC)` (`util/os_time.c:57-61`); the user client must use the same clock or waits return instantly/never. Return -1 + `errno=ETIMEDOUT` on timeout (`:443`) |
| 11 | `DRM_IOCTL_PANFROST_MADVISE` | `panfrost_kmod.c:488,499` | `{u32 handle,u32 madv,u32 retained}` | none: return 0/`retained=1` or ENOSYS | `make_unevictable` returns true unless `ioctl==0 && retained==0` (`:499-502`), so ENOSYS is safe |
| 12 | `DRM_IOCTL_PANFROST_SYNC_BO` | `panfrost_kmod.c:471` | `{u64 ops,u32 op_count,u32 pad}` -> `{u32 handle,u32 type,u32 offset,u32 size}[]` | `boCacheSync` | only reached for BOs with `WB_MMAP` (`pan_kmod.c:251`), gallium never asks for it (`grep WB_MMAP gallium/drivers/panfrost` = no hit [obs]); needed only if a later version >= 1.6 is reported |
| 13 | `DRM_IOCTL_PANFROST_QUERY_BO_INFO` | `panfrost_kmod.c:388` | `{u32 handle,u32 extra_flags,u32 create_flags,u32 pad}` | `boInfo` | only if version >= 1.6 |
| 14 | `DRM_IOCTL_PANFROST_SET_LABEL_BO` | `panfrost_kmod.c:651` | `{u32 handle,u32 pad,u64 label}` | none | skipped below version 1.4 (`:635`) |
| 15 | `DRM_IOCTL_PANFROST_JM_CTX_CREATE/DESTROY` | `pan_jm.c:1083,1106` | `{u32 handle,u32 priority}` / `{u32 handle,u32 pad}` | `jmCtxNew/Close` | only for LOW/HIGH priority contexts; default context uses handle 0 (`pan_jm.c:1060-1065`). Skip |
| 16 | `PERFCNT_ENABLE/DUMP` | `panfrost_kmod.c:740,906,950` | - | none | perf sessions only (use eventfd/timerfd, s.5) |
| 17 | `drmPrimeFDToHandle` + `lseek(fd,0,SEEK_END)` | `pan_kmod.c:159,172` | fd -> handle; size via `lseek` | `importSurface` (IOSurface id -> handle, size, GPU VA) | **dma-buf emulation needed**, s.2.4 |
| 18 | `drmPrimeHandleToFD` | `pan_kmod.h:709` (export), also winsys dumb buffers `panfrost_drm_winsys.c:99` | handle -> fd | none (return -ENOSYS) | only for `pipe_screen::resource_get_handle(FD)`; CA does not export |
| 19 | `drmSyncobjCreate/Destroy/Wait/ExportSyncFile/ImportSyncFile/FDToHandle`, (`Reset`, `Transfer`, `TimelineWait` only in CSF/u_sync_provider) | `pan_context.c:1111,1217,663,669`; `pan_fence.c:27,48,61,77,83,90,102,119`; `pan_jm.c:93,166` | libdrm API | userland table + `waitSeqno` | **syncobj emulation needed**, s.2.5 |

`drmGetCap` (`util/u_sync_provider.c:142`, `gallium/auxiliary/util/u_screen.c:139`) can return `-EINVAL`. Complete symbol set the fake `xf86drm.h` must declare
(grep of the files compiled for this build [obs]): `drmIoctl drmGetVersion drmFreeVersion drmGetCap drmCloseBufferHandle drmPrimeFDToHandle drmPrimeHandleToFD
drmSyncobjCreate drmSyncobjDestroy drmSyncobjHandleToFD drmSyncobjFDToHandle drmSyncobjImportSyncFile drmSyncobjExportSyncFile drmSyncobjWait drmSyncobjTimelineWait
drmSyncobjTransfer drmSyncobjReset drmSyncobjSignal drmSyncobjTimelineSignal drmSyncobjQuery2` plus `DRM_CLOEXEC`, `DRM_RDWR`, `drmVersion`; the last five
come from `util/u_sync_provider.c:15-44`, which is compiled whenever `dep_libdrm.found()` (`util/meson.build:317-319`), and the CSF files (`pan_csf.c:746,857`) are
always compiled, so declare them even though they are never called on a JM GPU.

### 2.3 `pan_kmod_ops` (option B, a native `xnu` backend instead of fake ioctls) [src `panfrost/lib/kmod/panfrost_kmod.c:969-991`]

The ops struct of the panfrost backend (`panfrost_kmod.c:969`) has: `dev_create dev_destroy dev_query_user_va_range bo_alloc bo_free bo_import bo_get_mmap_offset bo_wait flush_bo_map_syncs
bo_make_evictable bo_make_unevictable vm_create vm_destroy vm_bind query_timestamp bo_set_label perf_*`. Note `bo_get_mmap_offset`, not a `bo_mmap`
member (doc 18 s.2.3 table had that wrong); `pan_kmod_bo_mmap` is a header inline that calls `os_mmap` (`pan_kmod.h:750-770`). A native backend still needs the `submit`
path, which is **not** in the ops struct: gallium calls `pan_kmod_ioctl(DRM_IOCTL_PANFROST_SUBMIT)` directly (`pan_jm.c:156`) and the `drmSyncobj*` calls directly (`pan_context.c:1111`).
So option B does not remove the fake-libdrm layer; **option A (fake `xf86drm.h`) is the only practical path** [src for the call sites; guess for the conclusion].

### 2.4 dma-buf emulation (IOSurface import) [src + guess]

* Flow: CA's IOSurface -> `resource_from_handle(WINSYS_HANDLE_TYPE_FD)` (`pan_resource.c:360,425`) -> `panfrost_resource_import_bo` (`pan_resource.c:272-280`) ->
  `panfrost_bo_import` (`pan_bo.c:476-540`) -> `pan_kmod_bo_import` (`pan_kmod.c:150-197`) = `drmPrimeFDToHandle(fd)` then `lseek(fd, 0, SEEK_END)` = size (`pan_kmod.c:172`;
  size 0 or -1 is an error: "invalid dmabuf size") -> `panfrost_kmod_bo_import` (`panfrost_kmod.c:361`) = `GET_BO_OFFSET` (+ `QUERY_BO_INFO` if >= 1.6).
* Emulation [guess, no Mesa patch]: the EAGL shim creates a real fd for each surface: an unlinked temp file `ftruncate`d to the IOSurface's size (so `lseek(SEEK_END)`
  returns the size) and records `fd -> IOSurface id` in a table; `drmPrimeFDToHandle` looks the fd up and calls `importSurface` (the sailfish selector-12 pattern, doc 18 s.1.3, `sweet_drm.c:~466-490`).
  `/dev/null` (sailfish's trick for the device fd) does NOT work for the prime fd: its `lseek` size is 0.
* The kernel half must map the IOSurface's wired pages into the Mali address space below 4 GiB (VA limit, s.2.2 row 8). XNU pages are 16 KiB, Mali tables 4 KiB (doc 30 Q6).
* Flag: pitch/offset rules for linear imports are enforced by `pan_layout` (`explicit_layout.wsi_row_pitch_B = whandle->stride`, `strict = strict_import(...)`,
  `pan_resource.c:380-386`). CA's surface pitches may violate the v9 linear alignment: keep sailfish's copy fallback (doc 18 s.1.3 item 5) [guess].

### 2.5 Syncobj and fence emulation [src + guess]

* Created at context creation: `ctx->syncobj` (signalled, `pan_context.c:1111`) and `ctx->in_sync_obj` (`:1217`); failure of the first fails `panfrost_create_context`.
* Every submit puts `ctx->syncobj` as `out_sync` of the *last* chain of a batch (`pan_jm.c:199,214,222`).
* Fences: `panfrost_fence_create` calls `drmSyncobjExportSyncFile` (`pan_fence.c:119`), creates a new syncobj by `drmSyncobjImportSyncFile` (`:77-83`) and closes the fd (`:128`);
  `fence_finish` is `drmSyncobjWait(WAIT_ALL)` with an absolute `os_time_get_nano`-based timeout (`:44-48`); `glFinish`/`glFlush` with a fence take this path [guess: st/mesa].
* Emulation design [guess]: syncobj = userland table entry `{seqno}` (0 = signalled). The kernel needs only a **global completion counter**: `submit` returns a monotonically
  increasing job seqno, and `waitSeqno(seqno, abs_timeout_ns)` blocks until the counter reaches it. `out_sync` handle gets the returned seqno; `drmSyncobjWait` waits on the max of
  the entries; `ExportSyncFile` returns a fresh fd (any real fd, e.g. `open("/dev/null")`) recorded as `fd -> seqno`; `ImportSyncFile` copies the seqno (Mesa does export, import,
  close back to back, so a stale fd number is overwritten before reuse). `in_syncs` need no kernel support if all jobs share one in-order queue.
  `libsync.h`'s `sync_accumulate` (`pan_context.c:1070`, only for `fence_server_sync`) does `ioctl(SYNC_IOC_MERGE)` (`util/libsync.h:160`): not on the CA path.
* **Needs no dma-fence or kernel syncobj object at all** under this design. It does need the in-order queue and the fragment-after-tiler ordering of s.2.2 row 9.

### 2.6 Kernel-side requirements the Mesa code imposes (for the `SweetMali` driver; each is a decision, not yet verified on hardware)

1. **Tiler heap**: `panfrost_open_device` allocates a 128 MiB `PAN_BO_INVISIBLE | PAN_BO_GROWABLE` BO (`pan_device.c:101-103`); `GROWABLE` maps to `PANFROST_BO_HEAP`
   (alloc-on-fault, `panfrost_kmod.c:299`). The Linux driver grows it from the Mali MMU fault IRQ. A first light can back the whole 128 MiB eagerly [guess] (wired memory cost on a 6 GB phone).
2. **No CPU access to heap/INVISIBLE BOs** (never mapped, `pan_bo.c:400-410`).
3. **All GPU VAs < 4 GiB**, lowest 32 MiB reserved (`panfrost_kmod.c:509-517`).
4. Executable vs `NOEXEC` mapping per BO (`:296-304`, flags -> `to_panfrost_bo_flags`).
5. Cache coherency: report `is_io_coherent = false` (do not report version >= 1.6, or answer `SELECTED_COHERENCY = NONE (31)`); CPU mappings uncached/WC; the shim still needs
   CPU cache maintenance for imported IOSurfaces that CA wrote cacheable [guess] (doc 30 s.5: the G78 coherency on gs101 is `TODO(verify)`).
6. The G78 errata for r0p1 (doc 30 s.5) are not in Mesa; they live in the kernel driver.

## 3. G78 model recognition (requirement 2)

* `GPU_ID 0x92020010` [src doc 30 s.5 from kbase]. `gpu_id` in Mesa = `(GPU_PROD_ID << 16) | GPU_REVISION` (`panfrost_kmod.c:186-188`), so `GPU_PROD_ID = 0x9202`, `GPU_REVISION = 0x0010`.
* Decode with Mesa's own macros (`model/pan_model.h:25-39`, `PAN_ARCH_MAJOR` bits 28-31, `PAN_ARCH_MINOR` 24-27, `PAN_PRODUCT_MAJOR` 16-19):
  arch major 9, minor 2, product major **2** -> `pan_prod_id = PAN_PROD_ID(9, 2, 2)` (`pan_model.h:120-127`); `pan_arch() = 9` (`:100-117`); `gpu_variant = 0` (props are memset, `panfrost_kmod.c:~184`, and no code sets it).
  Cross-check: G57 (9,0,3) is nibble 3 of GPU_ID bits 16-19, consistent with the table.
* Table at `model/pan_model.c:84-87` (26.2.5) holds only `(9,0,1)` and `(9,0,3)` as v9 rows; `f1fbde1c` adds `(9,2,4)` = "G68"/"G78 counters" at line 93 [obs, raw fetch].
  **No row matches (9,2,2).** `pan_get_model()` (`pan_model.c:139`) returns NULL, `panfrost_open_device` fails (`pan_device.c:43-48`), and even without that
  `pan_screen.c:1043-1048` prints "Unsupported model". The compiler/perf side already knows the G78 (`compiler/bifrost/cmdline.c:80`, `perf/mali_perf.h:187`).
* **Exact change** (insert after the `(9, 0, 3)` row, `model/pan_model.c` after line 87 in 26.2.5; one `VALHALL_MODEL(...)` statement, two source lines):

  ```
  VALHALL_MODEL(PAN_PROD_ID(9, 2, 2), 0, "G78",    "G78", MODEL_ANISO(ALL),  MODEL_TB_SIZES(16384,  8192),
                                             MODEL_RATES_X(2, 4, 8,  32,  32, 8)),
  ```
  Fields are a **copy of the G57 row** [guess]: the tilebuffer sizes feed `pan_query_optimal_tib_size` (`pan_props.c:119-138`, asserts power of two >= 2048/1024) and the rates only
  perf heuristics; the counter set name `"G78"` exists in `perf/mali_perf.h:187`. Name becomes `Mali-G78` (`pan_model.c:19`). The real tilebuffer size of the G78 MP20 must come from an Arm
  source or the kbase headers before this is upstreamed (`TODO(verify)`).
* The compiler needs nothing else: arch 9 is built (`gallium/drivers/panfrost/meson.build:97` loops `4 5 6 7 9`, `src/panfrost/libpan/meson.build:12` includes 9).

## 4. Build recipe (requirement 3)

### 4.1 What sailfish / flame / hackios actually did (all cite local files)

| Project | Mesa | Method | Options / cross file | Cited |
|---|---|---|---|---|
| sailfish (freedreno, GLES) | unrecorded (`mesa-setup.sh` clones `research/mesa`; doc 18 s.1.4) | meson cross on a Mac, static libs, one hand-link into `sweetgl.dylib` | `-Dplatforms= -Degl=disabled -Dglx=disabled -Dgbm=disabled -Dgallium-drivers=freedreno -Dvulkan-drivers= -Dfreedreno-kmds=msm -Dllvm=disabled -Dgles1=disabled -Dgles2=enabled -Dopengl=true -Dzstd/-Dzlib/-Dexpat=disabled -Dxmlconfig=disabled -Dvalgrind/-Dlibunwind/-Dlmsensors=disabled -Dbuild-tests=false -Dshader-cache=disabled -Dperfetto=false -Dgallium-va=disabled -Dgallium-rusticl=false -Dtools= -Dhtml-docs=disabled -Ddisplay-info=disabled -Dteflon=false --buildtype=release -Db_ndebug=true -Ddefault_library=static` | `tools/sweetgl/mesa-setup.sh:10-19`; cross file `ios-cross.txt:1-18` (clang from Xcode 15, `iPhoneOS17.5.sdk`, `-arch arm64 -miphoneos-version-min=16.0`, `system='darwin' subsystem='ios'`, `pkg-config='false'`); link `build.sh:14-26,28-51` (24 `.a`, `xcrun -sdk iphoneos clang`, `-framework IOKit ...`, `codesign -f -s -` at :51) |
| hackios (freedreno + softpipe, OSMesa) | **25.0.7**, sha256 pinned (`mk-mesa.sh:47-48,63`) | meson cross with the kernel's clang wrapper and `--wrap-mode=nofallback`; patched source | `mk-mesa.sh:106-112`: same family plus `-Dosmesa=true -Dshared-glapi=enabled -Dglvnd=disabled -Dvideo-codecs= -Dgallium-vdpau/-xa=disabled`; cross file `:84-103` with `needs_exe_wrapper = true` | libdrm stand-in `tools/mesa/freedreno-darwin.patch:7-15` (declare_dependency with the fake `xf86drm.h` include dir + IOKit link args) |
| flame (Turnip = Vulkan, Adreno) | `85fda3b3` + UniXNU patches (`build-turnip-ios.sh:4`) | meson cross with `xcrun --sdk iphoneos --find ...` | `build-turnip-ios.sh:14-18` (`-Dfreedreno-kmds=darwin` = a real new KMD backend `tu_knl_darwin.cc` in Mesa, not a fake libdrm; `-Dllvm=disabled`), cross file `:24-46`, SDK from `xcrun --sdk iphoneos --show-sdk-path` (`:21`) | the Darwin-KMD precedent for option B; its patches are not in the local clone |

None of the three builds Panfrost, so none had to solve mesa_clc: freedreno's compiler needs no LLVM (doc 18 s.1.4). Panfrost does, hence the two stages.

### 4.2 Option set for the iOS stage (gallium panfrost + GLES only, no X, no real libdrm, no LLVM)

Verified against `meson.options` of 26.2.5 that each name exists [src `meson.options`: `platforms`, `egl`, `glx`, `gbm`, `gallium-drivers` (:83), `vulkan-drivers`, `llvm` (:432),
`gles1`, `gles2`, `opengl`, `zstd`, `zlib` (:625), `expat` (:75), `xmlconfig` (:802), `shader-cache`, `valgrind`, `libunwind`, `lmsensors`, `perfetto`, `build-tests`, `tools`,
`teflon`, `gallium-rusticl`, `gallium-va`, `video-codecs`, `html-docs`, `display-info` (:633), `mesa-clc`, `precomp-compiler`, `panfrost-rust` (:915, default false), `shared-glapi`];
`gallium-vdpau`, `gallium-xa`, `osmesa` do **not** exist any more in 26.2.5 (hackios's flags `mk-mesa.sh:111` would error).

```
meson setup build-ios mesa-26.2.5 --cross-file ios.cross --native-file host-tools.native --wrap-mode=nofallback \
  --buildtype=release -Db_ndebug=true -Ddefault_library=static \
  -Dplatforms= -Degl=disabled -Dglx=disabled -Dgbm=disabled \
  -Dgallium-drivers=panfrost -Dvulkan-drivers= -Dllvm=disabled \
  -Dmesa-clc=system -Dprecomp-compiler=system -Dpanfrost-rust=false \
  -Dopengl=true -Dgles1=disabled -Dgles2=enabled -Dshared-glapi=disabled \
  -Dzstd=disabled -Dzlib=disabled -Dexpat=disabled -Dxmlconfig=disabled -Dshader-cache=disabled \
  -Dvalgrind=disabled -Dlibunwind=disabled -Dlmsensors=disabled -Dperfetto=false -Dbuild-tests=false \
  -Dgallium-rusticl=false -Dtools= -Dteflon=false -Dgallium-va=disabled -Dvideo-codecs= -Dhtml-docs=disabled -Ddisplay-info=disabled
```

Facts behind the less obvious ones:
* `-Dmesa-clc=system -Dprecomp-compiler=system`: documented for cross builds without LLVM (`docs/drivers/panfrost.rst:62-68`). With `system`, meson calls
  `find_program('mesa_clc', native: true)` and `find_program('vtn_bindgen2', native: true)` (`meson.build:965-967`) and `find_program('panfrost_compile', native: true)`
  (`src/panfrost/clc/meson.build:6-7`): **three** host programs (doc 18 named two). `libpan` runs them for archs 4 5 6 7 9 10 12 13 14 (`src/panfrost/libpan/meson.build:12-46`), including 9.
* `with_clc` is then false (`meson.build:965-969`), so `LLVMSPIRVLib`/`SPIRV-Tools`/`libclang` are not looked up in the cross stage (`meson.build:2068-2090`).
* `-Dpanfrost-rust=false`: default is false already (`meson.options:915`), but it would add a Rust requirement and `meson >= 1.7` (`meson.build:856,866-868`); keep it explicit.
* `add_languages('objc', native: false)` runs on Darwin (`meson.build:20`): the cross file **must define `objc`** (sailfish `ios-cross.txt:4`, flame `build-turnip-ios.sh:28`).
* expat/xmlconfig disabled: on Darwin Mesa looks for a *library* named expat (`meson.build:1842`); sailfish disabled it and passed a hand-built `driconf` instead (`sweetgl.c:~108`, "no xmlconfig: defaults");
  panfrost reads driconf options in `panfrost_create_screen` (`pan_screen.c:1037`), so the frontend must provide the same hand-built `driOptionCache` [guess].
* `dep_libdrm` is `null_dep` on Darwin because `system_has_kms_drm` is false (`meson.build:162,1920-1934`); hackios/sailfish needed a patched `meson.build` to give it a fake dependency
  with the `xf86drm.h` include directory (patch A, s.5). Consequences of `dep_libdrm.found() == true`: `util/u_sync_provider.c` (`util/meson.build:317`), `renderonly.c`
  (`gallium/auxiliary/meson.build:287`), `winsys/panfrost/drm` (`gallium/meson.build:116-117`) are compiled; `-DHAVE_LIBDRM` is only added on the `elif` branch, so keep hackios's branch order.
* `with_gallium_drm` is false for Darwin (`meson.build:260`), so `kmsro` is not built (`meson.build:343`) - fine, we have no scanout through DRM.

### 4.3 Two-stage mesa-clc build (host stage must itself be Darwin)

Stage 1 (macOS arm64 runner, native): the documented command (`docs/drivers/panfrost.rst:65-67`)
`meson . build-host -Dtools=panfrost -Dmesa-clc=enabled -Dinstall-mesa-clc=true -Dprecomp-compiler=enabled -Dinstall-precomp-compiler=true` installs `mesa_clc`, `vtn_bindgen2`
(`src/compiler/spirv/meson.build:82-93`, installed because `install-mesa-clc`) and `panfrost_compile` (`src/panfrost/clc/meson.build:9-19`, `install-precomp-compiler`).
Add `-Dllvm=enabled` and the same "no X/EGL/GL" switches (the draft workflow has the full line).

**Gotcha that sailfish/hackios never met [src]:** `panfrost_compile` links `libpanfrost_dep`, which links `libpankmod_lib` (`src/panfrost/lib/meson.build:10,68`), i.e. `panfrost_kmod.c`/`panthor_kmod.c`,
which `#include <xf86drm.h>` unconditionally (`panfrost_kmod.c:11`, `panthor_kmod.c:~4`) and `<sys/eventfd.h>`/`<sys/timerfd.h>` (`panfrost_kmod.c:12-13`). A macOS host build therefore fails
without the same patches A and C of s.5 (the fake header + the eventfd guard); the stub functions can be weak no-ops in stage 1. This is why the host stage cannot be built on
`ubuntu-24.04`: the cross stage runs the host tools, and it runs on the Mac [src: `needs_exe_wrapper`; the iOS link needs Apple's ld].

Host tools needed (name -> role -> version [src]):

| Tool | Version | Why |
|---|---|---|
| meson | >= 1.4.0 (`meson.build:8`); 1.12.1 worked in the doc 18 dry run [obs doc 18] | build |
| ninja | any | build |
| python3 + `mako`, `pyyaml`, `packaging` | Python >= what `meson.build:1097` accepts; `packaging` or `distutils` required (`:1142`); `yaml` checked at `:1127` | code generators |
| bison, flex | any recent; macOS ships bison 2.3 (too old, `meson.build:2229` special-cases it) | GLSL/glcpp parsers (`needs_flex_bison`, `:2189`) |
| LLVM + clang libs | >= 15 (`meson.build:2001`) | mesa_clc (stage 1 only) |
| SPIRV-LLVM-Translator | `LLVMSPIRVLib >= 15.0.0.0`, `>= LLVM major.minor`, `< LLVM major.(minor+1)` (`meson.build:2074-2084`) | mesa_clc |
| SPIRV-Tools | `>= 2024.1` (`meson.build:2092-2095`) | mesa_clc |
| Xcode 15 with iPhoneOS SDK | the kernel workflow pins the oldest preinstalled Xcode (`kernel/.github/workflows/xnu.yml:72-74`) | cross compile/link |

How a `macos-14` runner installs them [guess for the exact brew formula versions at run time]: `brew install meson ninja bison flex llvm spirv-llvm-translator spirv-tools pkgconf`;
put `$(brew --prefix bison)/bin`, `flex`, `llvm/bin` first on `PATH` (Apple's bison is 2.3); `python3 -m venv` + `pip install mako pyyaml packaging`; set `PKG_CONFIG_PATH` to the three
`lib/pkgconfig` dirs. **Version coupling is the main risk**: brew's `spirv-llvm-translator` is built against brew's default `llvm`; the draft workflow compares `llvm-config --version`
with `pkg-config --modversion LLVMSPIRVLib` and fails early on a major.minor mismatch.

Stage 2 reads the stage-1 outputs through a native file (`[binaries] mesa_clc=... vtn_bindgen2=... panfrost_compile=...`); meson resolves `find_program(name, native: true)`
via `[binaries]` of the native file or `PATH` [guess: native file entry name = program name; fall back to `PATH=$HOST_TOOLS/bin:$PATH` if meson ignores it].

### 4.4 Link step (frontend, not written yet)

Same as sailfish `build.sh:14-51`: list the static libs (`libmesa.a libgallium.a libpanfrost.a libpanfrostwinsys.a libpanfrost_lib.a libpanfrost_compiler.a libpankmod_lib.a libpanfrost_decode.a`
`libpanfrost_shared.a` + `libnir.a libglsl.a libglcpp.a libvtn.a libcompiler.a libglapi*.a libmesa_util*.a libblake3.a libmesa_util_c11.a ...`; take the real list from `ninja -t targets all`),
the dylib sources (`eagl.c`, `sweetgl.c`, `dispatch.c`, DRM emulation), `-dead_strip -exported_symbols_list`, `-framework IOKit CoreFoundation IOSurface OpenGLES CoreGraphics -lobjc -lc++ -lz`,
`codesign -f -s -`. Drop the 64 MiB pad (we flash `userdata`, doc 18 s.1.4). The frontend calls `panfrost_drm_screen_create(fd, &cfg)` (`panfrost_drm_winsys.c:122-126`) or `panfrost_create_screen`
directly (`pan_screen.c:1008`, public via `panfrost/pan_public.h`) with `ro = NULL`; the freedreno call of `sweetgl.c:~106` is replaced and the `FD_MESA_DEBUG`/`IR3_SHADER_DEBUG`
env knobs are dropped (the `nofp16` equivalent for panfrost is `TODO(verify)`).

## 5. Linux assumptions that break on Darwin, and how sailfish / hackios worked around them (requirement 5)

| # | Linux-ism | Where in Mesa 26.2.5 (this build) | What happens on Darwin | Workaround (sailfish / hackios precedent) |
|---|---|---|---|---|
| A | **libdrm** (`xf86drm.h`, `drm*` calls) and `dep_libdrm == null_dep` | `panfrost_kmod.c:11`, `panthor_kmod.c`, `pan_kmod.c`, `u_screen.c:37`, `util/u_sync_provider.c:8`, `renderonly.c:32`; `meson.build:1920-1934` | header not found | fake `include/xf86drm.h` + implementation in the dylib. sailfish `include/xf86drm.h:1-51`, `sweet_drm.c`; hackios `freedreno-darwin.patch:7-15` makes `dep_libdrm` a `declare_dependency` with the include dir. Meson patch + symbol list of s.2.2 |
| B | **`mmap`/`munmap` of a DRM node** with a fake offset | `pan_kmod.h:765` (`os_mmap(..., dev->fd, mmap_offset)`), `pan_bo.c:346` (`os_munmap`) | `mmap` of `/dev/null` fails | sailfish `mesa.patch:9-14` redefines `os_mmap` to `sweet_drm_bo_map(fd, offset)` and `os_munmap` to 0 (`sweet_drm.c:457-465`); hackios calls `redwood_drm_map/unmap` (`freedreno-darwin.patch:54-60`). For Panfrost: the same two redefinitions, in `pan_kmod.h` (inline) and `pan_bo.c` |
| C | **`<sys/eventfd.h>`, `<sys/timerfd.h>`, `eventfd()`, `timerfd_*`, `sched_setscheduler(SCHED_FIFO)`** | `panfrost_kmod.c:12-13`, perf sessions `:703-949` (`:759-768` scheduler) | header not found: **build break**, even though perf is never used | no sailfish/hackios analogue (freedreno has none). Wrap lines 12-13 and the perf block in `#ifndef __APPLE__` and leave `.perf_*` ops NULL (they are optional in `pan_kmod_ops`). Same for any `#include <linux/...>` in `panthor_kmod.c` (it includes `drm-uapi/dma-buf.h` and `util/libsync.h`, both POSIX-only, so it should compile) [guess] |
| D | **`drm-uapi` headers** use `<linux/types.h>`/`<asm/ioctl.h>` | `include/drm-uapi/drm.h:38-51` | has a `__linux__` branch and falls back to `<sys/ioccom.h>`: compiles, and `_IOWR` on Darwin has a different encoding from Linux | sailfish `xf86drm.h:12-13` includes `sys/ioccom.h`. The emulation must decode `request` with the same Darwin macros that built it (as `sweet_drm.c:436-449`) |
| E | **sync_file fds / `SYNC_IOC_MERGE`** | `util/libsync.h:77-198` | compiles (only POSIX includes); ioctls fail at run time | only `fence_server_sync` uses it (`pan_context.c:1070`), not on the CA path |
| F | **syncobj** | `pan_context.c:1111`, `pan_fence.c`, `pan_jm.c:93,166` | no kernel object | s.2.5 (sailfish returned `-ENOSYS`, `sweet_drm.c:519-529`, insufficient for panfrost) |
| G | **dma-buf / prime fds** | `pan_kmod.c:159,172`, `pan_kmod.h:709` | no dma-buf | s.2.4 (sailfish used the IOSurface id as the "fd" for freedreno's import path, `sweet_drm_import_iosurface`, `sweet_drm.c:~466`) |
| H | **`os_same_file_description` via `kcmp`** | `util/os_file.c:238-264`, `gallium/auxiliary/util/u_screen.c:212-253` | kcmp branch `#ifdef SYS_kcmp`/`DETECT_OS_LINUX` skipped, falls back to `fstat` dev/ino comparison | none needed; dup of the `/dev/null` fd compares equal, which is the wanted result (one screen per process) [src + guess] |
| I | **futex** | `util/futex.h:26-41` | `UTIL_FUTEX_SUPPORTED 0` on Darwin: mutex fallback | none needed |
| J | **`CLOCK_MONOTONIC` meaning** | `util/os_time.c:57-61`; absolute timeouts in `pan_fence.c:44-48`, `pan_bo.c:158`, `WAIT_BO` | on Darwin `CLOCK_MONOTONIC` is not `mach_continuous_time` (hackios `freedreno-darwin.patch:92-98` found that its kernel driver used another clock than Mesa and nothing was ever waited for) | the user client must interpret absolute timeouts on the clock `os_time_get_nano` uses, or the dylib converts to a relative timeout. Observed trap, not a patch |
| K | **`/proc`, `sysinfo`, `getauxval`, `dl_iterate_phdr`/build-id** | `util/os_misc.c` has `DETECT_OS_APPLE` branches (`:68-70,371-376,467,507-548`); `util/build_id.c:34-96` has an Apple LC_UUID branch; panfrost grep for `<sys/sysinfo.h>`, `/proc`, `/sys/`, `prctl`, `memfd_create`, `getauxval`: **no hits** [obs] | fine | hackios patched freedreno's `sysinfo` and `ir3_disk_cache.c` build-id (`freedreno-darwin.patch:111-122,179-197`): those are freedreno-only, not needed for panfrost; `-Dshader-cache=disabled` keeps `pan_disk_cache` quiet |
| L | **`epoll`, `memfd`** | no hit in the panfrost, kmod, winsys or gallium-driver directories [obs grep] | - | not an issue for this build set |
| M | **Linker flags** `-Wl,--build-id=sha1` (`meson.build:1758`) | `get_supported_link_arguments` drops it on Apple ld | fine | - |
| N | **User-space cache maintenance** (`dc civac` / `dc cvac` from EL0) | `util/cache_ops_aarch64.c` (compiled for aarch64, `util/meson.build:189-190`), used when `util_has_cache_ops()` (`pan_kmod.h:773-777`) | EL0 cache maintenance is allowed on Linux; on XNU it depends on `SCTLR_EL1.UCI` [guess] | with version 1.3 and no `WB_MMAP`, Mesa never calls it for BOs; imported-surface flushes are the shim's business |

## 6. The draft workflow (requirement 4)

File: `userland/tools/ci/mesa-panfrost-ios.yml.draft` (YAML-valid: `python3 -c "import yaml; yaml.safe_load(...)"` printed OK [obs]; not active, not under `.github/`, nothing pushed).
Job on `macos-14`: select the oldest preinstalled Xcode (as `xnu.yml:72-74`) and check `xcrun -sdk iphoneos --show-sdk-path`; brew + venv installs; LLVM/SPIRV pairing check; fetch Mesa
from `archive.mesa3d.org` with a **required** `mesa_sha256` input (it fails if empty); apply `tools/mesa/*.patch` (none exist yet: the build is expected to fail on `xf86drm.h`
until patches A-D exist); stage 1 host tools; stage 2 cross build with `xcrun -sdk iphoneos` and the `[host_machine] system='darwin' subsystem='ios'` cross file; collect
every `.a`, `lipo -info` and `vtool -show-build` (platform must be iOS); placeholder step for `tools/sweetmali/build.sh` (the future dylib link) + `codesign -f -s -`; `upload-artifact`.
No secrets, `permissions: contents: read`, no SDK download (the runner's Xcode provides it, same as the kernel job); Mesa source and brew/pip packages are the only downloads.
Known weak spots of the draft [guess]: brew package versions at run time; `meson` honouring `[binaries] mesa_clc=` in the native file; `ninja -t targets all` regex for the `.a` list; `vtool`
availability on the runner (ships with Xcode CLT).

## 7. Unresolved items

1. **Real G78 tilebuffer sizes / rates** for the new model row (copied from G57, [guess]). Needs an Arm source or kbase constants; wrong values affect performance, not boot.
2. **Whether a stage-1 macOS build of `panfrost_compile` links** with weak no-op `drm*` stubs; I could not run it (no Mac, no LLVM/SPIRV-Translator here). The whole of s.4.3 is unrun.
3. **Brew formula pairing** (`llvm` vs `spirv-llvm-translator` vs `spirv-tools >= 2024.1`) on `macos-14` at run time; Xcode 15 SDK iPhoneOS 17.x with `-target arm64-apple-ios16.0` weak-import behaviour (flame's "strchrnul lesson", `build-turnip-ios.sh:66-67`).
4. **Patches A-D do not exist yet** (`tools/mesa/*.patch`): fake `xf86drm.h` + `meson.build`/`util`/`gallium` gating (A), `os_mmap`/`os_munmap` redirect (B), `panfrost_kmod.c` eventfd/timerfd/sched guard (C), `pan_model.c` G78 row (D).
   They should be written and tested on a Mac before the workflow is activated; stated only as a plan.
5. **Syncobj/fence emulation design** (s.2.5) and the "fake prime fd = temp file" trick (s.2.4) are [guess]; the fragment-after-tiler ordering requirement (s.2.2 row 9) needs to be confirmed against what the in-order queue
   gives and what the Mali hardware does with two job slots.
6. **Heap BO**: eager 128 MiB backing vs fault-driven growth (s.2.6 item 1); also whether the Pixel's tiler really needs `PANFROST_BO_HEAP` semantics (kbase "JIT"/"CSF" notes are for CSF).
7. **Mesa pin**: doc 18/30 say main `f1fbde1c`, the tree I read is 26.2.5. They differ at least in `pan_model.c` (G68 row). Decide one pin and re-read file:line for it (`TODO(verify)` for every line in this note if main is chosen).
8. **Frontend driconf**: `pan_screen.c:1037` calls `driQueryOptionb(config->options, ...)` with xmlconfig disabled; sailfish's hand-built option cache pattern (`sweetgl.c:~106-109`) must be re-derived for panfrost's option names (`driinfo_panfrost.h`).
9. **IOSurface linear import**: the pitch/offset constraints Panfrost enforces for v9 (`pan_layout`) versus CA's surfaces; CA's GLES shader behaviour on Panfrost (fbfetch, rect textures) - hardware questions of doc 18 s.5.
10. **Need (not a tool proposal)**: a way to run the stage-1/stage-2 Mesa build without a Mac would remove the CI-only loop (no Mach-O linker on this host: doc 18 s.3.2). Mentioned for the driver; nothing proposed or built.
