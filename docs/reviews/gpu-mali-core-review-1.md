# Review 1: patch 0104 `sweet-mali-driver-core` (adversarial, 2026-10-23)

Scope: `xnu/patches/0104-sweet-mali-driver-core.patch`: new `bsd/dev/sweet_mali.c`, `sweet_mali.h`, `sweet_mali_pt.h`, plus the hooks in `sweet_gpu_job.c` and `sweet_gpu_gs101.c`. Line numbers are lines of the patch file.
References:
- docs/research/31 (kbase checklist), 30 s.7-8 (runs u1, gpujob1), 28 s.11 (job1), docs/reviews/gpu-xnu-job-review-1.md
- kbase `~/pixelios-artifacts/refs/gpu-full/mali_kbase` (696fc02)
- XNU `~/pixelios-artifacts/xnu-pristine`, and the patched and built tree `~/pixelios-artifacts/localbuild/work/main` (108 patches; its `bsd/dev/sweet_mali.c` is byte-identical to the patch)
- Built object `.../DEVELOPMENT_ARM64_SWEET/bsd/DEVELOPMENT/sweet_mali.o`, disassembled with `deps/host/cct/otool -tV`
- Patch 0105, read only to check how it uses the API

I did not touch the phone, adb or fastboot. I edited nothing except this file. Tags: [src] read from source, [obs] observed on hardware (cited doc), [guess].

## Verdict

**SAFE TO RUN ONCE ON HARDWARE** (`sweetmali=2`), under the conditions at the end. There is no blocker for that one run:
- Every new register write matches kbase in register, value and point in the sequence (cores off, before power-on).
- The self-test does not reach any of the HIGH defects: its buffer is `KMA_PERMANENT`, it calls unmap once with valid arguments, and no client exists yet.

The two HIGH findings are API defects that patch 0105 makes reachable from user space. Fix them before 0105 is run or leaves `wip/*`.

Two MED findings should be fixed before the run, because they decide what the run proves:
- M1: the self-test runs on JS1, not on the proven JS0.
- M3: the self-test unmaps while a hung job may still run.

## Findings

### H1 (HIGH): `sweet_mali_unmap` does not validate its input, and `sm_pt_clean` indexes `l3[]` out of bounds
- **Where:**
  - patch:432-452 (unmap): no check on `va` or `n`. The PTEs are cleared at :441-443 BEFORE `mali_va_free` checks ownership at :444.
  - patch:218-222 (`sm_pt_clean`): the loop runs to `va + len` with index `(uint32_t)(a >> 21)` and no bound.
- **Failure scenarios** (all reachable through 0105 `boClose` and a corrupted or hostile handle table; [src] 0105 forwards `b->va`/`b->n16k`):
  - (a) `unmap(0xFFFFC000, 4)`: `a` reaches 0x100000000, so the index is 2048. `sm.pt.l3[2048]` aliases `l0_pa` (nonzero), and `l3_pa[2048]` reads past `struct mali_pt` into `sm.pool[0]`, which is a kernel VA. That VA is passed to `flush_dcache64(..., TRUE)` as a physical address. `phystokv` of a non-RAM PA is a kernel data abort, a panic [src: caches.c `flush_dcache` phys path, as described in review gpu-xnu-job-review-1 s.3].
  - (b) A large `n` makes `n * 4u` wrap in 32 bits, so the loop at :441 runs up to 4 G iterations with `sm_lock` held. The clean loop at :218 then runs about 32 M iterations of OOB reads.
  - (c) A range that straddles two BOs, or that is only partly allocated: :441-443 unmaps the neighbour's pages first. `mali_va_free` then returns -1 and leaves the bits set. The result is EINVAL with someone else's mapping destroyed and the VA leaked.
  - (d) `n == 0` or `va == 0` reaches `as_flush` with `len == 0`, so `end = va - 1`. With `va == 0`, `l = 64` and `1ull << 64` is undefined behaviour (patch:241-246).
- **Fix:**
  1. At the top of unmap: `if (n == 0 || n > 0x10000 || (va & (PAGE_SIZE - 1)) || va < MALI_VA_START || va + (uint64_t)n * PAGE_SIZE > MALI_VA_END) return EINVAL;`.
  2. Under the lock, check that every bit is allocated (the same test `mali_va_free` makes) BEFORE any `mali_pt_unmap`.
  3. In `sm_pt_clean`, clamp the end to `MALI_VA_END` and skip indices `>= 4 * 512`.
  4. In `as_flush`, return early on `len == 0`, and refuse `l > 48` (kbase refuses above `KBASE_LOCK_REGION_MAX_SIZE_LOG2` = 48, mali_kbase_defs.h:118, mmu_hw_direct.c:131-133).

### H2 (HIGH): after an EIO, the GPU may still reach the pages, but neither the API nor 0105 says "do not free"
- **Where:**
  - patch:421-426: `map` returns EIO after `as_flush` fails, and leaves the PTEs live (AS0 stays enabled).
  - patch:446-449: `unmap` returns EIO the same way, with the TLB state unknown.
  - patch:437-439: `unmap` returns ENXIO once the device is dead, without touching the PTEs at all.
- **0105 [src]:** on any map error it calls `md->release()` (0105 patch:240-243). It also ignores the unmap return value and frees the memory (`boClose`, 0105 patch:269-273; `teardown`, :148-150).
- **Failure scenario:**
  1. An AS flush times out, or the device died in a hard-stop path.
  2. The pages go back to the VM while AS0 still translates to them, and a hung or still-running job (or a stale GPU TLB entry) can DMA into memory the kernel has reused.
  This is a memory-corruption path that a GPU hang can trigger [guess on likelihood; the code path is src].
- **Fix:**
  - In 0104, on a flush failure in `map`, clear the PTEs and run `sm_pt_clean` before returning EIO. Do not free the VA.
  - Document in `sweet_mali.h`: "any nonzero return from map/unmap, other than EINVAL from argument validation, means the pages may still be reachable by the GPU: the caller must leak them (never `complete()`/`release()` the descriptor)". On a dead device, unmap should still clear the PTEs (CPU only, no AS command), and return ENXIO.
  - 0105 must check the result of unmap. It already leaks on a failed wait.

### M1 (MED): the self-test runs on JS1, not on the observed JS0, so it is not "the same job as gpujob1"
- **Where:** patch:600 `sweet_mali_submit(va, 0, &seq)` uses `req = 0`, and patch:460 maps that to slot 1. The comment at patch:576 says "the same job as gpujob1".
- **Facts:**
  - [obs doc 28 s.11] JS1_FEATURES = 0x1C9E. Bit 2 is `JS_FEATURE_SET_VALUE_JOB` [src jm_macros.h:230], so WRITE_VALUE on JS1 is supported.
  - But job1 and gpujob1 ran only on JS0.
  - `sg_precheck` checks only JS0 (STATUS, COMMAND_NEXT, FEATURES). The state of JS1 (STATUS 0x18A4) is never read.
- **Failure scenario:** the self-test fails. You cannot tell whether the new config writes caused it (s.1: SHADER_CONFIG, COHERENCY) or the new slot did.
- **Fix:**
  - Run the proven job first with `SWEET_MALI_REQ_FS` (JS0). Then run a second, identical job on JS1 (new target word, e.g. +0x108), and print both results. This also covers "multiple jobs" (two seqnos, in order).
  - Add to the bring-up refusals: `JS_PRESENT & 2`, `JS1_FEATURES (0x0C4) & 4`, `JS1_STATUS == 0`, `JS1_COMMAND_NEXT == 0`.

### M2 (MED): config writes are accepted with the L2 and shaders already powered; kbase writes them only with the cores off, right after a reset
- **Where:** patch:541-553. `sweet_gpu_precheck` accepts the "fully on" power state (`sweet_gpu_job.c` `sg_precheck`, `on` branch). JM_CONFIG, SHADER_CONFIG, L2_MMU_CONFIG and COHERENCY_ENABLE are then written while the L2 is live.
- **kbase [src]:** `kbase_pm_hw_issues_apply` and `kbase_cache_set_coherency_mode` are called only from `kbase_pm_init_hw` (pm_driver.c:4550-4551). That runs after a soft reset, or on the `PM_NO_RESET` path when the platform reports the GPU is "in reset" (pm_driver.c:3757-3764). In both cases the cores and the L2 are off, and kbase calls `kbase_pm_cache_snoop_disable` first (:4489).
- **Reachable when:** `sweetgpujob=1` and `sweetmali=N` are given together. 0103 leaves the cores on, then 0104 runs, see `sweet_gpu_gs101.c` (built tree, lines 124-130). The same happens if a future shim hands over powered cores.
- **Failure scenario:** the coherency protocol changes from ACE-Lite (0) to none (31) while the L2 holds lines. The effect is unknown [guess].
- **Fix:** in `sm_bringup`, before any write, require `L2_READY == TILER_READY == SHADER_READY == 0` (0x160/0x150/0x140) and all PWRTRANS == 0. Refuse with zero writes otherwise ("config needs cores off; no soft reset in this stage").

### M3 (MED): the self-test waits 1 s, but the hard stop comes only at 3 s; on a hang it unmaps the VA of a running job
- **Where:** patch:602 `sweet_mali_wait(seq, 1000000)`, then :604-608 flush, read and `sweet_mali_unmap`. The timeouts are `JOB_TIMEOUT_US` 3 s and `STOP_GRACE_US` 0.5 s (patch:155-156).
- **Failure scenario:**
  1. A hung job makes wait return ETIMEDOUT at 1 s.
  2. Unmap clears the PTEs and issues LOCK/FLUSH_MEM on an AS a running job uses. `as_idle` may fail, which marks the device dead.
  3. At 3 s the thread hard-stops.
  The log then shows a timeout, a possible "flush did not complete", then a hard-stop line. The cause cannot be read from it. The memory is safe because the buffer is permanent.
- **Fix:** wait `JOB_TIMEOUT_US + 2 * STOP_GRACE_US` (4 s). Skip the unmap unless wait returned 0 or EIO, and print `stop=` from `sm.stopped`.

### M4 (MED): MMU-fault path. The grace timer is not restarted, and the AS is neither disabled nor recovered
- **Where:** patch:296-304 and :319-323.
- **(a) Grace timer.** The hard stop is issued at :301 but `sm.busy_start_us` is not reset; only the timeout path resets it (:318). On a fault more than 0.5 s after submit, the very next poll sees `stopped && el > STOP_GRACE_US`. The device is marked dead with no grace at all.
- **(b) AS left faulted.** kbase handles a JM page fault like this: hard-stop, switch the AS to UNMAPPED ("will abort all jobs", mmu_jm.c:177-185), and only then clear the fault (:189-191) [src]. Here AS0 stays mapped and in the fault state. The IRQ is cleared at :299, and the next `submit` only checks `MMU_RAW == 0` (:483). So a later job runs on a faulted AS [guess on the hardware effect]. Without UNMAPPED, a job stalled on the faulting translation may also never stop, and the device goes dead.
- **Fix:**
  - Set `sm.busy_start_us = now_us()` next to `sm.stopped = 1` at :302.
  - After a faulted job completes or is declared dead, disable AS0 as 0103 does (`TRANSCFG` 0x42000001, TRANSTAB 0, `UPDATE`), and mark the device dead until a soft reset exists. Do not clear MMU_RAW and carry on.
  - Also print `AS0_FAULTEXTRA` (0x2438).

### M5 (MED): COHERENCY_ENABLE = 31 is kbase's default, but whether it applies to gs101 is still an open TODO
- **Where:** patch:154, :549.
- **What is solid [src]:**
  - Value 31 = `COHERENCY_NONE` (gpu_coherency.h:27).
  - The default assignment is at core_linux.c:5320. kbase keeps it unless `system-coherency` or `dma-coherent` is set (:5326-5348).
  - The gs101 mali node has neither property.
- **What is open:** the parent `soc` node was not read (doc 31 s.4, `TODO(dma-coherent-parent)`). If the parent sets `dma-coherent`, Android runs with 0 (ACE-Lite): exactly the value gpujob1 used (u1 observed 0).
- **Safety:** either value is safe with non-shareable leaves, `PTW_SH = 0` and explicit cache maintenance [src mmu_hw_direct.c:343-346; guess for the hardware]. So it does not block the run. Rule 7 still requires it to be checked, and it is one of four variables that change between gpujob1 and this run.
- **Fix:**
  - Read the parent node (gs101 soc dtsi from the kernel tree for 696fc02), or dump the live Android DT with `tools/rea/rea-phone.sh`, `/proc/device-tree/.../dma-coherent`. Cite the result in the comment.
  - Add a boot-arg bit (e.g. `sweetmali` bit 4 = skip the four config writes), so that a failed self-test can be split into config vs. slot without a rebuild.

### L1 (LOW): the API contract is unwritten; 0105 needs it spelled out
The rules below are needed for memory safety, and none of them is enforced in 0104. All [src] from the code:
- (i) Every PA passed to `map` is wired RAM owned by the caller. `flush_dcache64` with phys=TRUE on a non-RAM PA panics.
- (ii) The `pa16k` array is kernel memory. It is read twice (patch:394-398 outside the lock, :405-408 inside), so it must not change in between.
- (iii) `unmap(va, n)` names exactly one earlier `map` result (H1 adds the bounds check, not boundary tracking).
- (iv) Unmap only after every job that referenced the BO has completed (`seq_done >= last use`). Otherwise the VA can be handed to a new BO while the old job still runs.
- (v) Only one client at a time. There is one AS0 for everybody, so any job can read and write any mapped BO.

0105 does (iv) with `fLastSeq` and (v) with the single client. It does not do H2. Put (i)-(v) in `sweet_mali.h`.

### L2 (LOW): the fail ring gives a false success for an old seqno
patch:282, :526. `fail_seq[seq % 32]`: once 32 more failures have happened, a failed seqno reads as success. Fix: document "status valid for the newest 32 seqnos", or return ETIMEDOUT/ESTALE when `seq + 32 <= sm.seq_done`.

### L3 (LOW): map uses FLUSH_MEM; kbase uses FLUSH_PT for an insert
patch:421. kbase uses `KBASE_MMU_OP_FLUSH_PT` (4) after inserting pages (mmu.c:2125) and FLUSH_MEM (5) only on teardown or remap (mmu.c:3049-3061) [src]. FLUSH_MEM is a superset (it also writes back and invalidates the GPU L1/LSC), so it is correct, just heavier. It is not harmful with a job running: kbase issues FLUSH_MEM on unmap while jobs run. Optional fix: `AS_CMD_FLUSH_PT 4u` for map.

### L4 (LOW): the config register values before the write are not logged or checked
patch:546-554 prints only the values after the write. [obs doc 27 s.51] all four read 0 at reset, and u1 read COHERENCY 0. Fix: print the before-values too, or refuse unless `JM_CONFIG == SHADER_CONFIG == L2_MMU_CONFIG == 0`. This makes "OR into an unknown value" impossible.

### L5 (LOW): no PA sanity check on page-table chunks
patch:196-203 has no `pa == 0 || pa + 0x10000 > 1ull << 40` check; 0103 has one (review gpu-xnu-job-review-1 L5). Fix: add the check and give the chunk back as unused (it is permanent, so just do not use it).

### L6 (LOW): small items
- The write order differs from kbase:
  - kbase: IRQ mask and clear first (pm_driver.c:4488), then SHADER, TILER, L2_MMU, JM (:4171-4187), then COHERENCY.
  - Patch: JM, SHADER, L2_MMU, COHERENCY, then the IRQ masks.
  - The masks were already 0 [obs u1], so nothing behaves differently. Reorder to the kbase order.
- `mali_pt_init` uses a compound literal of about 33 KB (patch:774). In this build clang emits `bzero` (sweet_mali.o, `bl _bzero`; the frame of `sweet_mali_init` is 0x90). A different optimisation level could put it on a 16 KB kernel stack. Use `memset(p, 0, sizeof *p); p->ops = ops;`.
- `sweet_mali_init` accepts any nonzero mode (3, 0xFF, ...) as "bring-up". Refuse anything other than 1 and 2.
- `JS_ST_ACTIVE` and `JS_HEAD` are unused (patch:121, 145).
- The pool holds 64 x 16 = 1024 tables. That covers about 2 GiB of distinct 2 MiB regions, not the 4 GiB window (2032 L3 tables). Map returns ENOMEM beyond that; document the ceiling.
- `map` holds `sm_lock` across up to 65536 `flush_dcache64` calls and `kmem_alloc_contig`, and the completion thread waits meanwhile. This is a latency issue only.
- The error path of `map` (patch:411-414) clears all n*4 leaves, including a leaf that returned -3 (already mapped, i.e. not ours). That cannot happen with a consistent bitmap.
- GPU_FAULTSTATUS and GPU_IRQ_RAWSTAT are never read while a job runs. A GPU-level bus fault shows up only as a job timeout.

## Checked and found correct

1. **New register writes against kbase** (all offsets are in jm_enums.h; the mapping of 0x5000 covers the highest offset used, 0x2434):

   | Write | Patch | kbase | Verdict |
   |---|---|---|---|
   | JM_CONFIG 0xF00 \|= 0xF<<16 | :546 | default `0xF` << `IDVS_GROUP_SIZE_SHIFT` 16, max 0x3F (pm_driver.c:4023-4037, regmap.h:519-520), applied by `hw_issues_apply` :4185 | correct |
   | SHADER_CONFIG 0xF04 \|= 1<<29 | :547 | `SC_VAR_ALGORITHM` (regmap.h:306) when TTRX_2968_TTRX_3162 is in the issue list (pm_driver.c:4058-4059; issues.h r0p1, doc 31 s.1 #4) | correct |
   | L2_MMU_CONFIG 0xF0C &= ~(0x3F<<5 \| 0x1F<<12) | :548 | tBEx limits `GENMASK(10,5)` / `GENMASK(16,12)` with value 0 (l2_mmu_config.c:66, :105-108); no SNOOP_DISPARITY because coherency is not ACE (:112-117) | correct |
   | COHERENCY_ENABLE 0x304 = 31 | :549 | `kbase_cache_set_coherency_mode` (cache_policy_backend.c:42) with the default NONE | correct value; applicability: M5 |
   | IRQ masks = 0 | :550-552 | `kbase_pm_disable_interrupts_nolock` (pm_driver.c:3632-3640) | correct (they were already 0) |
   | GPU_IRQ_CLEAR = raw | :553 | kbase clears all (`kbase_reg_gpu_irq_all`); raw is a subset that acks RESET_COMPLETED 0x100 [obs u1] | correct |
   | sequence position | :545-557 | `init_hw` (IRQ off, quirks, coherency) before the PM state machine powers L2/tiler/shaders (pm_driver.c:4488-4552 vs :2161-2203); with no soft reset this is the `PM_NO_RESET` path (:3757-3760) | correct, if the cores are off (M2) |

   None of these touches the WRITE_VALUE path:
   - IDVS and the varying algorithm are graphics-only [src names; guess on the effect].
   - L2_MMU_CONFIG stays 0.
   - COHERENCY: M5.

   No register is written that a cores-off GPU must not receive. The AS registers are written after power-on, as in the observed 0103.

2. **as_flush** (patch:238-258) equals `lock_region` plus `kbase_mmu_hw_do_flush`:
   - Lock region: L = fls64(start ^ end) with the minimum of 15 for model < 12 (mmu_hw_direct.c:118-153, defs.h:2192-2198). Base masked to L, size encoded as L-1.
   - Commands: LOCK = 2, FLUSH_MEM = 5 (regmap.h:175, 178).
   - Waits: AS_ACTIVE (STATUS bit 0, regmap.h:74-75) before each command and after the flush (mmu_hw_direct.c:194-210, :548).
   - CPU side: the clean (`flush_dcache64` = `dc civac` plus `dsb sy`, verified in review gpu-xnu-job-review-1 s.3) completes before the first MMIO write. Unmap updates the PTEs and then locks and flushes, the same order as kbase teardown.
   - Example: 16 KB at 0x2000000 gives LOCKADDR = 0x200000E (32 KiB).

3. **PTE formats** (sweet_mali_pt.h) equal mmu_mode_aarch64.c:
   - Table entry = PA | ACCESS | 3 (:180-183).
   - Leaf = PA | memattr<<2 | RW (bit 6) | NX (bit 54) | ACCESS | 3 (:108-149, :38-41).
   - Leaf with memattr 5 = 0x457, the value observed in gpujob1.
   - The index math (va>>30 < 4, va>>21 < 2048, &511) is correct for VA < 4 GiB with one L0 and one L1 entry range.
   - `MALI_VA_PAGES` = 0xFE000.

4. **Job submit** equals jm_hw.c:119-236 with the observed values:
   - Offsets: HEAD_NEXT, AFFINITY_NEXT, CONFIG_NEXT and COMMAND_NEXT are 0x18C0/D0/D8/E0 for JS1 (jm_enums.h:152-157).
   - Values: affinity 0x779FFF = SHADER_READY; START = 1.
   - The slot-free check reads COMMAND_NEXT == 0 (:101-117).
   - `JS_CONFIG` 0x83B00 gives AS 0, START/END clean+invalidate, chain flag 1 and priority 8.
   - The chain flag is constant, which is fine with one job in flight. HARD_STOP_1 (7) is the matching parity (jm_hw.c:532-536).
   - Without FLUSH_REDUCTION, no FLUSH_ID_NEXT is needed (jm_hw.c:147-150, 205).
   - The done bit is n and the fail bit is n+16 (jm_hw.c:300).
   - JS_STATUS is read before JOB_CLEAR (patch:306-307).
   - kbase treats the done bit as DONE without reading the status, so there is no "done with an exception" case to handle.
   - The timeouts fit kbase: hard stop at 5 s, reset 0.5 s later; here 3 s plus 0.5 s.

5. **Threading:**
   - No lost wakeup. `busy` is set and `thread_wakeup(&sm.thread)` is called under `sm_lock`. The thread tests `busy` and calls `lck_mtx_sleep_deadline` (which drops the lock atomically and takes it back with `LCK_SLEEP_DEFAULT`, locks.c:1155) under the same lock.
   - Every sleep has a deadline (200 us busy, 1 s idle, 1-2 ms for waiters). None runs without a bound and none busy-spins.
   - No deadlock: submit and wait release the mutex while they sleep. `finish` wakes `&sm.seq_done` with the lock held, and the waiters take it back after the thread unlocks.
   - Every path that marks the device dead either calls `finish` (the busy job) or leaves the waiters to see `sm.dead` on their next 1 ms deadline. Nobody sleeps forever.
   - `seq_done` only grows (one job in flight, in order).

6. **Self-test job layout** equals 0103 / gpujob1:
   - header word 4 = 0x10004 at +0x10
   - payload address = jc + 0x100 at +0x20
   - type 7 at +0x28
   - MAGIC at +0x30
   - target 0xA5.. at +0x100
   - The cache flush comes after the last CPU write (patch:599) and before submit; the clean+invalidate comes before the read (patch:604).
   - The jc VA is 16 KB aligned (VA alignment 4 pages).
   - `sm.up` is set only after the precheck, config, power, page tables, AS0 enable and the thread all succeeded (patch:571).
   - The refusal path writes nothing (the precheck comes before the first `wr`).
   - A repeated init is blocked by `started` (patch:617-620) and by the guard in 0102.

7. **Memory:**
   - `kmem_alloc_contig(..., 0x10000, PAGE_MASK, ...)` returns physically contiguous memory with 16 KB alignment, so every 4 KB sub-table has a 4 KB aligned PA through `kvtophys` (same call as 0103 and the UFS code).
   - `PAGE_SIZE` = 0x4000 in this object: the disassembly shows `tst x10, #0xffffff0000003fff` and `mov w1, #0x4000` before `flush_dcache64`.
   - `n <= 0x10000` keeps `n * 4u` and `(uint64_t)n * PAGE_SIZE` from overflowing in map.
   - The static `sm` is about 163 KB of zero-filled data: the bitmap 130048 B (`_sm+130064` = hint) plus the pt arrays of about 32 KB.

8. **Host test:**
   - `shim/mali_pt_host_test.c` was built from the patch-extracted header in /tmp (not in `shim/out`, AP-03) and prints `mali_pt_host_test: PASS`.
   - First-fit with hint wrap was checked by hand:
     - Pass 0 covers the aligned starts in [align(hint), total-n].
     - Pass 1 covers [0, hint+n), so every aligned start lies in one of them.
     - The skip `align_up(i+k+1)` is correct.
     - `mali_va_test` stays below `total`.
   - An extra randomized test (/tmp only, not committed) checked `mali_va_alloc` against a brute-force reference over 6000 alloc/free operations. Sizes were 1-60000 pages, alignment 1-32, and it ran up to 85% full. Result: `fuzz ok live=1785 pages=881995 bits=881995`. Success or failure always matched the reference, every result was aligned and inside the window, and the bitmap count equals the sum of the live allocations (no overlap).

## Conditions for the one run (`sweetmali=2`)

- Fix M1 and M3 first; they decide what the result proves. M2, M4 and M5 may wait for this one run if the conditions below hold. H1, H2 and L1 must be fixed before 0105 runs.
- Command: `tools/xrun --kernel K --gpu-handover --args "sweetmali=2" --label mali1 --expect 'SWEET MALI: selftest'`.
  - Run it as one background job with the USB UDP log, `--cpus 1`, `--qr off`, no `--dvfs`.
  - No `sweetgpujob=1` in the same boot (M2). `--gpu-handover` already adds `sweetgpu=`.
  - `fastboot boot` only.
  - Check the kernel sha256 against the built `kernel.development.sweet`.
- Before you trust the result:
  - The 0102 lines must match u1: PMU 0x1/0x1, muxes 0x10, GPU_ID 0x92020010, all READY/PWRTRANS 0, JOB/MMU RAW 0, GPU_FAULTSTATUS 0, S2MPU CTRL0 = 0.
  - The `SWEET MALI: config` line must read `JM=0xf0000 SHADER=0x20000000 L2_MMU=0 COHERENCY=0x1f`. Any other value means the reset value was not 0: void the run (L4).
- Pass criterion: `SWEET MALI: up ...` followed by `selftest submit/wait=0 ... target=0x504958454c494f53 (MAGIC) header_exc=0x1`, with no `MMU fault`, `timed out` or `dead` line. Afterwards the phone must reach `tools/phone goto fastboot` normally.
- If it fails, record the exact lines and do not retry blindly. With the M5 knob, re-run once with the config writes skipped, to separate the config change from the JS1 slot (or from JS0, once M1 is fixed).

## Driver response (2026-10-23)
Fixed: M1 (selftest runs JS0 first, then JS1 with a second descriptor; bring-up also refuses on a dirty JS1), M2 (bring-up refuses unless all READY regs are 0), M3 (selftest waits 4 s, unmaps only after a clean wait), M4 (MMU fault restarts the stop grace timer and marks the device dead when the job ends: no AS recovery yet), M5 (boot-arg bit 0x10 skips the config writes; config registers logged before writing; unknown modes ignored), H1 (unmap validates range, checks the VA allocator before touching tables, clamped clean loop), H2 (contract in sweet_mali.h; 0105 leaks pages on EIO/ENXIO or a failed unmap). Not done: M5 TODO of the parent dma-coherent node (open in research 31), LOW items. Kernel sha256 46f0a17b..., stack 105/105, local build clean.
