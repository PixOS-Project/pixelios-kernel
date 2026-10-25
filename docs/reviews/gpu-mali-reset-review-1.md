# Review 1: GPU reset and recovery (0104), surface import and cache sync (0105) (adversarial, 2026-10-23)

Scope: the code added since gpu-mali-core-review-1 (commits 560c895, c2b036c, 9b1bacb):
- `bsd/dev/sweet_mali.c`: `sm_reset`, `sm_hw_config_and_power`, the `finish()`/`check_job()` recovery paths, and the selftest reset step (boot-arg bit 0x20, `sweetmali=0x22`).
- `iokit/Kernel/arm/SweetMali.cpp`: selector 5 `importSurface`, selector 6 `boCacheSync`, the `mapMemory` refactor, and `SweetMali::start` registering early.

Line numbers refer to the built tree `~/pixelios-artifacts/localbuild/work/main/xnu`. Its three files are byte-identical (`cmp`) to what patches 0104 and 0105 produce.

References:
- kbase `~/pixelios-artifacts/refs/gpu-full/mali_kbase`.
- `refs/kbase-bluejay/gs101-gpu.dtsi`.
- docs/research/17 (GPU SPI numbers), 30 s.7-9, 31 s.5.3.
- XNU sources in the built tree.

Tags: [src] read from source, [obs] observed on hardware (cited doc), [guess].

I did not touch the phone, adb or fastboot, and I edited nothing except this file.

## Verdict

**SAFE WITH CONDITIONS: run `sweetmali=0x22` once.**
- The reset sequence matches kbase in register, value, order and timeout.
- The run does not reach the defects in the IOKit import code, because no user client exists yet.

Conditions:
- Fix M1 (one line) before the run.
- Handle M2 before the run: drop the IRQ unmask, or confirm the GPU SPI is disabled.
- Then follow the run conditions at the end.

H1 is a use-after-free in `importSurface`/`boClose`. It must be fixed before any userland client runs.

## Findings

### H1 (HIGH, not reachable in the 0x22 run): an imported surface's memory descriptor is released without having been retained (use-after-free and double free)
- **Where:**
  - `importSurface` (SweetMali.cpp:286-320) passes `s->memory` to `mapMemory`.
  - On success, `mapMemory` stores it as `fBo[slot].md` (:253) without `retain()`.
  - `boClose` (:356-358) and `teardown` (:157-159) then call `md->complete(); md->release(); OSSafeReleaseNULL(surf)`.
- **Why it is a bug:**
  - The surface owns its memory: `SweetSurface::free` does `OSSafeReleaseNULL(memory)` (SweetSurface.cpp:87) [src].
  - So `boClose` drops the surface's own reference.
    - If that was the only one, the descriptor is freed while `s->memory` still points at it.
    - The next user of the surface (a framebuffer swap, `kernelAddress()`, a CPU map) then touches freed memory.
    - When the surface itself is freed, the descriptor is released a second time, which is an over-release panic or heap corruption.
  - The EIO/ENXIO path of `mapMemory` (:246) retains `md`, so it is safe. The success path is the broken one.
- **Fix:** in `importSurface`, call `s->memory->retain()` before `mapMemory` and `s->memory->release()` on every failure path except EIO/ENXIO. Alternatively, keep a flag per BO and call `complete()` without `release()` for imports. The first fix is simpler: every `fBo[i].md` then owns one reference.

### M1 (MED, reachable in the 0x22 run): a failed explicit reset in the selftest does not mark the device dead
- **Where:** `sm_selftest` (sweet_mali.c:646-651) calls `sm_reset()` and keeps the result only in `re`. `sm_reset` itself never sets `sm.dead`; only `finish()` does (:231-233).
- **Failure scenario:**
  1. Neither the soft nor the hard reset completes, or the cores still read as ready, or the AS0 re-enable fails.
  2. `sweet_mali_ready()` stays 1. The selftest goes on to `sweet_mali_unmap(va, 1)` (:655), because e0 and e1 were 0.
  3. `as_flush` writes LOCKADDR/LOCK/FLUSH_MEM into a GPU whose reset did not complete, and waits up to 3 x 100 ms.
  4. Only then is the device marked dead. Later IOKit opens would also see a "ready" device.
  - [guess] MMIO to a Mali that is stuck in reset normally just returns stale values, but the code path is real [src].
- **Fix:** in `sm_reset`, set `sm.dead = 1` on every `return 1` (and drop the duplicate in `finish`). In the selftest, skip the unmap when `re != 0`.

### M2 (MED, reachable in the 0x22 run): unmasking RESET_COMPLETED is the first time a GPU interrupt line can assert, and XNU has no handler for it
- **Where:** sweet_mali.c:534 writes `GPU_IRQ_MASK = 0x100` before the reset. The mask goes back to 0 only after the poll sees the bit (:546-547).
- **Facts:**
  - kbase does this, with the comment "b/290409680 - unmask irq before the soft reset" (pm_driver.c:4333-4344) [src].
  - The GPU line is SPI 419 (INTID 451), level-high (gs101-gpu.dtsi:24-27; doc 17 table) [src].
  - XNU on SWEET:
    - It never writes any `GICD_ISENABLER`/`ICENABLER` (grep: only `GICR_ISENABLER0` in pe_fiq.c:178).
    - It enables Group 1 at the distributor (pe_fiq.c:208-210).
    - `sleh_irq` only EOIs an unknown INTID (sleh.c:1883-1891). Nothing on SWEET installs a handler for SPIs.
  - The shim does not touch SPI enables either (shim.c: only GICR registers).
- **Failure scenario:** suppose ABL (or a firmware leftover) left SPI 451 enabled as Group 1 NS.
  1. Once the reset completes, the level line stays high and the only CPU re-enters `sleh_irq` immediately after every EOI.
  2. The thread that would write `GPU_IRQ_CLEAR` gets little or no forward progress.
  3. The result is a hung phone (recoverable with a button reset; nothing is persisted). The likelihood is low [guess: SPI enables reset to 0 and ABL has no GPU use], but rule 7 says check it.
- **Evidence that the unmask is not needed for polling:** RAWSTAT is pre-mask. The observed `GPU_IRQ_RAWSTAT=0x100` with all masks 0 (doc 30 s.7, run u1) shows that bit 8 is set while it is masked [obs]. Upstream Panfrost also polls RAWSTAT with the mask at 0 [guess: recalled from `panfrost_gpu_soft_reset`, not checked locally].
- **Fix (pick one before the run):**
  - (a) Keep the mask at 0 during the reset. Write a `ponytail:` note that it deviates from kbase b/290409680 because we poll. If the soft reset then times out, that is the evidence the unmask matters.
  - (b) Keep the kbase order, but first read `GICD_ISENABLER14` (GICD+0x138) bit 3 and refuse the unmask if it is set. The GICD mapping lives in pe_fiq.c and would need a small accessor.

### M3 (MED): the MMU-fault path prints on every 200 us poll while the fault re-asserts
- **Where:** check_job (:247-257) prints and clears whenever `MMU_RAW != 0`. The hard stop and grace timer are set only once (guarded by `stopped`).
- **Failure scenario:** a stalled AS, or several cores faulting, raise MMU_RAW again after each clear. In 0.5 s of grace that is up to about 2500 printf lines over the USB UDP log: the log is flooded and the useful lines are lost [guess on the hardware repeating it; the code path is src].
- **Fix:** print only when `!sm.faulted` (and keep a counter, printed once in `finish`).

### M4 (MED, before an untrusted or second client): importSurface maps ANY surface, including the panel framebuffer, with no ownership check, and a physical surface can panic the cache maintenance
- **Ownership:**
  - `sweet_surface_copy(id)` looks up the global table (SweetSurface.cpp:171-184). Any opener can therefore import another process's surfaces, or the framebuffer surface (`sweet_surface_create_physical`, SweetFramebuffer.cpp:235).
  - The client can then read and write them through the GPU, and also through the CPU: `clientMemoryForType(handle)` hands out the import's descriptor (:368-381).
- **Physical surface:**
  - The framebuffer surface is `IOMemoryDescriptor::withPhysicalAddress(v_baseAddr)` (SweetSurface.cpp:242).
  - `sweet_mali_map` (:359) and `boCacheSync` (:333) call `flush_dcache64(pa, ..., TRUE)`, which calls `phystokv_range`. That panics with "illegal PA" for any PA outside `[gPhysBase, gPhysBase + real_phys_size)` and outside the ptov table (arm_vm_init.c:387-408) [src].
  - Whether the panel carve-out lies inside that range is not checked [guess].
  - If it does lie inside, the flush works through a cacheable physmap alias of memory the display scans out as write-combine. That is harmless for clean+invalidate, but the GPU writing the scan-out buffer directly is a design decision nobody has made yet.
- **Fix:**
  - For now, accept only `OSDynamicCast(IOBufferMemoryDescriptor, s->memory) != NULL` (buffer surfaces) in `importSurface`; return `kIOReturnUnsupported` otherwise.
  - Before an untrusted client exists, also require the surface to belong to the calling task, or to have been passed to it (the creator pid or a per-task grant in SweetSurface).

### M5 (MED, before any process other than a test tool can open it): nothing gates who opens the user client
- **Facts [src]:**
  - `SweetMaliUserClient` is a plain `IOUserClient`, not `IOUserClient2022`, so `is_io_service_open_extended` does not require `IOUserClientEntitlements` (IOUserClient.cpp:4496-4523).
  - The property is not set. MACF is the only gate (:4524).
  - The iOS app sandbox normally denies unknown user-client classes [guess, not checked on this port's sandbox profiles]. Unsandboxed daemons and root tools can open it.
- **Exposure today:**
  - The first opener holds the single-client token, so any process can deny the GPU to everyone else.
  - With M4 unfixed, the opener can read and write every surface in the system.
  - It cannot reach memory outside AS0: `JS_CONFIG` (AS 0) and `TRANSCFG` are kernel-set, and the page tables are not mapped into the GPU VA.
  - A hostile job can force a 3.5 s + reset cycle, repeatedly.
- **Fix:** in `initWithTask`, `setProperty(kIOUserClientEntitlementsKey, "com.pixelios.sweetmali")` (the open path checks it with `IOTaskHasEntitlement`, :4515-4521), and sign the test tool with it.

### L1 (LOW): the selftest unmap ignores the result of the job after the reset
- **Where:** sweet_mali.c:654. The unmap condition looks only at e0 and e1.
- **Failure scenario:** if e2 is ETIMEDOUT or ENXIO, the buffer is unmapped under a job that may still run. The timeline makes this unlikely:
  - The hard stop comes at 3.0 s and the reset in `finish` at about 3.5 s, both under `sm_lock`, while `wait` gives up at 4.0 s.
  - The memory is `KMA_PERMANENT`, so this cannot corrupt anything. The flush may still fail and mark the device dead, which muddles the log.
- **Fix:** add `&& (e2 == 0 || e2 == EIO || no reset step)` to the condition. Hoist `e2` to function scope, initialised to -1.

### L2 (LOW): the post-reset check looks at READY only, not PWRTRANS
- **Where:** sweet_mali.c:548, then `sg_power` (sweet_gpu_job.c:115-129).
- **Issue:** a core still in transition (`*_PWRTRANS != 0`) passes the check, and `TILER_PWRON` is written during the transition. Bring-up uses `sg_precheck`, which checks both (:104-107).
- **Fix:** also require `rr(0x200) == rr(0x210) == rr(0x220) == 0` (for both the low and the high word of SHADER).

### L3 (LOW): the hard-reset fallback aborts bus transactions
- **Facts:** `HARD_RESET` "Immediately reset the entire GPU", while SOFT "Stop[s] all external bus interfaces, and then reset[s]" (jm_macros.h:249-251) [src].
- **Risk:** a hard reset with AXI transactions in flight may leave an interconnect transaction dangling [guess]. kbase does the same as its fallback (pm_driver.c:4406-4447), so the sequence is faithful.
- **Fix:** none needed; just know that the hard-reset path is the only step here with a theoretical bus-hang risk.

### L4 (LOW): small items in 0105
- The comment in `mapMemory` (:245) says "the slot stays used", but `fBo[slot]` is not set, so the slot stays free. That is harmless; fix the comment.
- `boClose` decrements `fWired` (:354) even when it leaks the memory, so the 1 GiB budget does not count leaked memory. That only happens on a dead device.
- `wait` (selector 4) runs under the IOKit default read lock (`defaultLocking`, IOUserClient.cpp:4481-4486, 6445-6458), not "no client lock". For up to 60 s it blocks `IOConnectMapMemory` and close, which take the write lock (:4686, :4595). Its sleeps are `THREAD_UNINT`, so the caller cannot be killed for 60 s. Fix: clamp to 5 s, or use `THREAD_ABORTSAFE` and handle `THREAD_INTERRUPTED`.
- `boCacheSync` skips a page silently when `seg < page_size`. `mapMemory` refused such memory at import, so this is unreachable. Fine.

## Checked and found correct

1. **Reset command values.** `GPU_COMMAND` (0x30): SOFT_RESET = 1 and HARD_RESET = 2 (jm_macros.h:249-251). RESET_COMPLETED is bit 8 (:276). GPU_IRQ_RAWSTAT/CLEAR/MASK are 0x20/0x24/0x28 (jm_enums.h:42-46).
2. **Sequence against `kbase_pm_do_reset`/`init_hw`/`reset_timeout_worker`:**
   - Disable and clear IRQs first (jm_hw.c:929, pm_driver.c:4490).
   - Unmask only RESET_COMPLETED (:4333-4344).
   - Write SOFT_RESET (:4305).
   - Wait 500 ms (`RESET_TIMEOUT`, defs.h:84, :4359).
   - On timeout write HARD_RESET and wait 500 ms more (:4406-4438).
   - kbase's last resort is a platform power cycle (`gpu_pm_hw_reset`, pixel_gpu_power.c:673). Here it is "dead", which is acceptable.
   - Pixel's `soft_reset_callback` is NULL (pixel_gpu_power.c:672), so a plain `GPU_COMMAND` is the Pixel path.
   - `kbase_pm_cache_snoop_disable` (:4208-4222) only acts when `cci_snoop_enabled`, which needs an SMC id from the DT. The gs101 node has none, so it is a no-op here.
   - kbase's TMIX_8463 L2-transition wait (jm_hw.c:945-954) is not in the tBEx r0p1 issue list (issues.h:345-361), so it is not needed.
   - kbase does not flush the GPU caches before a soft reset, so none is required.
3. **No stale completion bit.** At boot RAWSTAT is 0x100 [obs u1]. Bring-up acks it (`GPU_IRQ_CLEAR = raw`), and `sm_reset` clears all bits again before the command (:533), so the poll cannot see an old bit.
4. **Restored state after reset.**
   - Config: `sm_hw_config_and_power` re-runs the kbase quirk writes on the reset values (OR into 0). It prints the before and after values and rewrites COHERENCY_ENABLE = 31 (unless bit 0x10 is set, in which case the reset value 0 stays, as before the reset).
   - IRQ masks: back to 0.
   - JS registers: reset to 0. `submit` re-checks COMMAND_NEXT, JOB_RAW and MMU_RAW.
   - AS0: `as0_enable` rewrites TRANSCFG, TRANSTAB and MEMATTR, then UPDATE. That is all kbase restores per AS (`kbase_ctx_sched_restore_all_as`, jm_hw.c:1000).
   - Page tables: they are in RAM, unchanged and already cleaned at map time. The GPU TLB and walker caches are empty after the reset, so no flush is needed.
   - Power: the reset turns the cores off ("The cores should be made unavailable due to the reset", pm_driver.c:4497). The `READY == 0` check therefore matches what kbase assumes. `sg_power` then takes the cold path (`SHADER_READY != mask`, then TILER_PWRON then SHADER_PWRON), which is the path bring-up proved in mali1 [obs doc 30 s.9].
   - Rails, PMU and S2MPU: only GPU-internal registers are written. The BUCK2S rail, the PMU domain and the S2MPU blocks are outside the GPU and are not touched by a GPU_COMMAND reset (kbase writes nothing else on this path) [src].
5. **Recovery bookkeeping.**
   - `finish()` is the only place that clears `busy`. It runs once per job: `check_job` returns right after it on the completion branch, and the grace branch calls it as its last statement.
   - `seq_done` is set after the reset, and the waiters are woken once under the lock.
   - A failed reset leaves `seq_done` advanced and `fail_seq` set, so `wait` returns EIO. The 0105 teardown then gets ENXIO from unmap and leaks, as the H2 contract requires.
   - A job that stops within the grace period finishes without a reset, as in kbase (a hard-stopped job leaves the slot usable).
   - When a fault and a completion arrive in the same poll, the reset runs once.
   - Repeated resets are fine: every submit clears `stopped` and `faulted`.
6. **Locking and timing.**
   - `sm_reset` holds `sm_lock` (a mutex) for up to about 1.4 s: 0.5 + 0.5 for the resets, plus 2 x 100 ms for power and 2 x 100 ms for AS. `delay(10)` spins preemptibly, so this is legal in a kernel thread.
   - Submitters (6 s limit) and waiters block on the mutex and see the result.
   - The selftest wait of 4 s exceeds the hang-to-reset time of about 3.5 s.
   - `sweet_mali_init` runs from `sweet_ufs_init` at root mount (doc 30 s.9), a thread context that may sleep.
7. **Selftest reset step.**
   - It runs only after JS0 and JS1 passed, with the thread idle (`busy == 0` once `wait` returned 0) and under `sm_lock`.
   - The third descriptor is at 0x400 and its target at 0x500. They do not overlap the earlier ones (0x000/0x100, 0x200/0x300) and stay inside the 16 KB page.
   - It uses its own magic (`MAGIC0 ^ 0xFF`) and its target is set to 0xA5.. before the submit, so a stale value cannot pass.
   - It runs on JS0, the proven slot.
8. **0105 `boNew` reference counting.**
   - The `IOBufferMemoryDescriptor` is auto-prepared (IOBufferMemoryDescriptor.cpp:296-298, IOMemoryDescriptor.cpp:2340-2342), so `complete()` at close is balanced (no `assert(_wireCount)` panic on DEVELOPMENT, MASTER.arm64:21 has mach_assert).
   - On success the creation reference goes into `fBo`, and it is released once at close.
   - On EIO/ENXIO, `mapMemory` takes an extra reference and `boNew` drops one, so the buffer is leaked alive.
   - Imports on EIO/ENXIO: `importSurface` does not call `complete()`, and the extra surface reference keeps the surface and its memory alive. Only the success path is wrong (H1).
9. **Cache sync arguments.** `flush_dcache64(addr64_t pa, unsigned len, TRUE)` reaches `flush_dcache(..., phys = TRUE)`, which does `phystokv_range` and then `FlushPoC_DcacheRegion` (clean+invalidate to PoC) per contiguous range (caches.c:61-91). Passing the PA, a 16 KB length and TRUE is correct for RAM.
10. **Physical segment checks.** `getPhysicalSegment(i * 16K, kIOMemoryMapperNone)` must give `p != 0`, `seg >= 16K` and 16 KB alignment for every page. Discontiguous memory is accepted at 16 KB granularity, which is what `sweet_mali_map` needs. The length must be a multiple of 16 KB and at most 256 MiB, so `n <= 0x4000 < 0x10000`.
11. **Scalar inputs.**
    - Handles are bounded to 1..4096 and their slot must be non-NULL.
    - Surface ids are bounded in `sweet_surface_copy`.
    - BO size is bounded at 256 MiB, and the per-client wired memory at 1 GiB.
    - `flags` is masked to EXEC, and `req` to FS.
    - `wait` is clamped to 60 s (see L4).
    - The submit `jc` must lie inside one of the client's BOs, and the core checks that it is in the window and 8-aligned.
    - A job chain can still reference any VA in AS0. With one client and one AS, that VA space contains only that client's BOs and imports, plus a selftest buffer if a selftest failed (permanent memory). This is acceptable for one trusted test client. It must change before a second or untrusted client: per-client AS1..7, M4 and M5.
12. **Service registration.** The personality is `IOProviderClass = IOResources` with its own `IOMatchCategory = SweetMali` (KernelConfigTables.cpp:86-88), so it does not compete with other IOResources drivers. `start` returns true once, and readiness is checked on every open (`initWithTask`) and every call (`externalMethod`). The client token is released only in `free()` and only by its owner (`fOwner`). `clientClose` runs under the IOKit write lock (IOUserClient.cpp:4594-4597), so teardown cannot race `externalMethod`.

## Conditions for the run (`sweetmali=0x22`)

1. Fix M1. Then fix M2 one way or the other: (a) mask 0, or (b) a read-only GICD check. Fix L1 too if convenient; M3 is cheap as well. Rebuild, run `tools/stack-apply`, and run the gate.
2. Run this as one background job, with `--qr off`, the USB UDP log, `--cpus 1`, no `--dvfs`, no `sweetgpujob=1`, `fastboot boot` only, and the kernel sha256 checked against the built kernel:

   `tools/xrun --kernel K --gpu-handover --args "sweetmali=0x22" --label malireset1 --expect 'SWEET MALI: selftest reset='`
3. Before you trust the result, check these lines (the same as mali1, doc 30 s.9):
   - The 0102 lines are identical to u1.
   - `config before JM=0 SHADER=0 L2_MMU=0 COHERENCY=0`.
   - `RESULT js0=PASS js1=PASS`.
4. Pass criterion:
   - `GPU reset done, cores back, AS0 re-enabled`.
   - The second `config before` line after the reset reads all zeros again. That proves the reset really reset the config registers; nonzero values mean the reset did not happen: void the run.
   - `selftest reset=PASS job-after-reset=PASS`.
   - No `hard reset`, `MMU fault`, `timed out` or `dead` line.
   - Afterwards `tools/phone goto fastboot` succeeds.
5. Do not retry blindly on failure:
   - A soft-reset timeout with the mask at 0 (option a) is itself the result: it is evidence for the b/290409680 unmask.
   - A hung phone with no log after `selftest RESULT` points to M2's interrupt storm. Record that before you change anything.
6. Fix H1 before any userland client (tools/sweetmali-test) is run. Fix M4 and M5 before any process other than that signed test tool can open the user client.
