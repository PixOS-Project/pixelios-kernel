# Review 1: patch 0103 `sweet-gpu-job-xnu` (adversarial, 2026-10-23)

Scope: `xnu/patches/0103-sweet-gpu-job-xnu.patch` (new `bsd/dev/sweet_gpu_job.c`, hook in `bsd/dev/sweet_gpu_gs101.c`). Line numbers are lines of the patch file.
References: `shim/gpujob.c`, `shim/gpupower.c`, `shim/out/board_config.h`, `tools/boards.py _gpu_job_values()`, docs/research/27, 28 s.2-11, 30 s.7, 31;
kbase `~/pixelios-artifacts/refs/gpu-full/mali_kbase`; XNU `~/pixelios-artifacts/xnu-pristine` (xnu-8796) plus `xnu/patches/0004`, `0028`.
Built kernel checked: `~/pixelios-artifacts/localbuild/out/kernel.development.sweet` (13:40) contains the `SWEET GPUJOB` strings (7).
Reviewer did not touch the phone, adb or fastboot. Nothing edited except this file and one antipattern entry (AP-17).

## Verdict

**SAFE TO RUN ONCE ON HARDWARE** (no blockers). Two MED findings (M1, M2) are missing refusal checks the shim has. The state that u1 observed (doc 30 s.7) already satisfies both checks, so they do not block one run when the conditions at the end are met. Fix them before the code goes past `wip/*` or runs again without these conditions.

## Findings

### M1 (MED): fewer read-only refusals than the shim. GPU_ID, the PRESENT masks, faults, the JS0 capability and the S2MPU are never checked before writing
- Where: patch:172 (the only stale-state check), patch:134-138 (only SHADER_PRESENT is checked, and only when the shaders are not already ready). Patch 0102 (`sweet_gpu_gs101.c`) prints GPU_ID but never compares it. So patch 0103 writes to a block whose identity nothing has checked.
- Shim refuses with zero writes on: `id-mismatch` (GPU_ID != 0x92020010), `present-mismatch` for SHADER/TILER/L2 (and HI words != 0), `gpu-status-nonzero`, `gpu-fault`, `rawstat-unexpected` (gpupower.c:261-306); `js0-absent`, `js0-no-set-value`, `gpu-fault` (gpujob.c:302-317).
- Failure scenario: a different handover state (shim refused the rail, or a future ABL/shim change) leaves a faulted or half-reset GPU, e.g. `GPU_FAULTSTATUS != 0` or `GPU_STATUS` busy. Patch 0103 then powers the cores and starts a job anyway. The result is unclear and no longer comparable to job1 [guess on the effect; the missing checks are read from source].
- Fix: at the top of `sweet_gpu_job_run`, refuse (print and return, nothing written) unless all of these hold:
  - `rr(0x000) == 0x92020010`
  - `rr(0x100) == 0x779FFF`, `rr(0x110) == 1`, `rr(0x120) == 1`, and `rr(0x104) == rr(0x114) == rr(0x124) == 0`
  - `rr(0x034) == 0` (GPU_STATUS) and `rr(0x03C) == 0` (GPU_FAULTSTATUS)
  - `rr(0x01C) & 1` (JS_PRESENT) and `rr(0x0C0) & (1u << 2)` (JS0_FEATURES SET_VALUE, observed 0x20E in job1)
  - optionally the four S2MPU `CTRL0 == 0`: 0102 already reads them, so pass a flag from 0102. The XNU-bank DMA claim in doc 28 s.10 depends on it.

  All values are observed: doc 30 s.7 (u1) and doc 28 s.11 (job1).

### M2 (MED): PWRON is written without checking that no power transition is pending, and partial states are accepted
- Where: patch:127-128 (TILER_PWRON when `L2_READY != 1 || TILER_READY != 1`) and patch:134-139 (SHADER_PWRON when `SHADER_READY != mask`). `*_PWRTRANS` is never read before the write.
- Shim: the precheck requires every READY and PWRTRANS word (LO and HI) of shader, tiler and L2 to be 0 before any write (`cores-not-off`, gpupower.c:275-282). kbase writes PWRON only from a settled state (pm_driver.c:2212-2218, 2659-2672).
- Failure scenario: a stuck SHADER_PWRTRANS is a state the hardware really produces (0x779FFF with the rail off: doc 27 run g2power, observed). Any partial or transitioning state is then written over. The write is not harmful in itself [guess], but the run would be outside the observed sequence and the log could not tell "the rail is off" apart from "the state was already wrong".
- Fix: before any power write, read SHADER/TILER/L2 READY and PWRTRANS (0x140/150/160 and 0x200/210/220, LO and HI). Go ahead only if all are 0 (cold, as in u1) or all are fully up (shaders 0x779FFF, tiler 1, L2 1, all PWRTRANS 0: skip the power-up). Refuse anything else with zero writes.

### L1 (LOW): a second HARD_STOP is possible, so "at most once" does not hold
- Where: patch:210-213 (HARD_STOP after the first timeout) and patch:218-221 (a second HARD_STOP if `JS0_STATUS == 0x08`, without checking whether one was already sent).
- Shim: `if (sub && !stopped && jsst == JS_STATUS_ACTIVE)` (gpujob.c:407). Doc 28 s.7 row 10 says "once".
- Scenario: the job hangs and is still ACTIVE after HARD_STOP plus 100 ms. A second HARD_STOP_1 is then written. That is legal (kbase can repeat stops) but outside the reviewed allowlist.
- Fix: add `int stopped = 0;`, set it to 1 at patch:211, and use `if (!stopped && jst == 0x08u)` at patch:218. Print `hardstop=yes|no` in the summary.

### L2 (LOW): the tiler/L2 wait does not check TILER_PWRTRANS (0x210)
- Where: patch:129. The shim waits `TILER_READY == present && TILER_PWRTRANS == 0` (gpupower.c:371, `wait_for(G_TILER_RDY, ..., G_TILER_TR)`), as kbase does (pm_driver.c:2212-2218).
- Fix: add `&& rr(0x210u) == 0` to the WAIT condition and a `R_TILER_TRANS` define.

### L3 (LOW): the stale-state check is weaker than the shim's (operator precedence itself is correct)
- Where: patch:172. `rr(R_JOB_RAW) & SLOT0_BITS || ...` parses as `(JOB_RAW & 0x10001) || ...` because `&` binds tighter than `||` (C precedence). It is correct and clang does not warn on `&` inside `||`.
- The shim refuses on any `JOB_RAW != 0` (gpujob.c:308-309). The patch ignores bits of slots 1/2. Harmless for detecting completion, because the wait masks slot-0 bits, but it is a deviation. Fix: use `rr(R_JOB_RAW) != 0` (u1 observed 0). The MMU_RAW check already uses the whole word.

### L4 (LOW): the report has less than the shim's, and `sub` is dead
- `sub` is always 1 when it is printed (patch:208, 230), so "no" can never appear.
- Missing compared with gpujob.c:389-396:
  - `JS0_COMMAND_NEXT` (0x1860): a START still pending is not cancelled by HARD_STOP
  - `JS0_HEAD_LO` (0x1800)
  - `AS0_FAULTADDRESS` (0x2420)
  - `GPU_FAULTSTATUS` (0x03C)
  - the hard-stop flag
  - `JOB/MMU_RAW` after the AS0 disable
- These are the reads that tell a failure apart. Fix: print them (read-only) and drop `sub`.

### L5 (LOW): no sanity check on the physical address
- Where: patch:184. `kvtophys` returns `pmap_paddr_t` (osfmk/arm/pmap/pmap.h:525). The patch declares it as returning `vm_offset_t`; both are 64-bit on arm64, so the ABI is fine. 0 is not handled.
- Fix: refuse if `pa == 0 || (pa & 0xFFF) || (pa + 0x5000) > (1ull << 40)`. MMU_FEATURES 0x2830 gives 40 PA bits (doc 28 s.3, observed).

### L6 (LOW, note): the AS0-ON failure path returns without unmapping AS0. This is safe only while the RAM is never freed
- Where: patch:198-201.
- If the AS_ACTIVE wait before the UPDATE timed out, the UPDATE was never sent and only the staged registers were written. If the wait after the UPDATE timed out, AS0 may become live later with our tables, but no job was submitted.
- Either way the GPU can only reach the 5 pages, and they are `KMA_PERMANENT` and never freed (patch:180). Sending a second AS command onto a stuck AS is what kbase avoids (it resets instead, mmu_hw_direct.c:168-186). Leaving it is the right call.
- Fix: none. Add a comment saying the allocation must never be freed while AS0 may still point at it. This also covers the `as0_off=FAILED` path.

### L7 (LOW, info): no fault catching on MMIO
- The shim writes through `try_write32`. In XNU, an abort on a write to the GPU block arrives as an SError and panics, and the root mount does not happen in that boot.
- The same writes, values and order were observed fault-free in job1 (doc 28 s.11). With cpus=1 a panic can leave the phone at a panic screen, and the human resets it. This is not a hang loop in this code. Accepted.

## Checked and found correct

1. **Constants equal the shim's observed values** (board_config.h:60-72, 89; `_gpu_job_values()` derivation; doc 28 s.2-4, 7):

   | Constant | Value | Notes |
   |---|---|---|
   | TRANSCFG_ON / TRANSCFG_OFF | 0x42000006 / 0x42000001 | `AS_TRANSCFG_MODE_AARCH64_4K` 6, `MODE_UNMAPPED` 1 (not IDENTITY 2): hw_access_regmap.h:124-126 |
   | MEMATTR LO / HI | 0x888D8F88 / 0x4C8D | |
   | JS_CONFIG | 0x83B00 | = 0x300 \| 0x3000 \| 0x800 \| 0x80000 |
   | PTE_TABLE / PTE_LEAF | 0x403 / 0x457 | leaf = 3 \| 0x40 \| 5<<2 \| 0x400 |
   | HDR_W4 | 0x10004 at data+0x10 | |
   | payload address | 0x1100 at +0x20 | |
   | WV_IMM64 | 7 at +0x28 | |
   | MAGIC | 0x504958454C494F53 at +0x30 | = MAGIC_HI:MAGIC_LO |
   | target pattern | 0xA5.. at +0x100 | |
   | JOB_VA / TARGET_VA | 0x1000 / 0x1100 | |
   | AFFINITY | 0x779FFF | |
   | TILER_PWRON | 1 | = GPU_TILER_PRESENT |
   | SHADER_PWRON | 0x779FFF | |
   | AS UPDATE | 1 | regmap.h:174 |
   | START | 1 | jm_macros.h:149 |
   | HARD_STOP_1 | 7 | jm_macros.h:155 |
   | JS STATUS ACTIVE | 0x08 | mali_base_jm_kernel.h:729 |
   | IRQ clear | 0x00010001 | only when the raw bits are set, as gpujob.c:416-419 |

   Every register offset matches gpujob.c:28-56 and gpupower.c:83-92. All are inside the 0x5000 mapping of 0102 (highest used offset 0x2434). The patch adds no constant that the shim did not use. Diffs from the shim are only the missing checks in M1/M2/L2/L3 and the extra stop in L1.

2. **Rail off (shaders never ready)**:
   - What happens: SHADER_PWRON is written, the 100 ms wait fails, and the code returns. No PWROFF is written, L2 and tiler stay on, and SHADER_PWRTRANS is presumably left at 0x779FFF as in g2power. No job is sent and no RAM is allocated (patch:177-179).
   - This matches the shim's behaviour on a stuck transition: the shim writes PWROFF only after PWRTRANS drains (gpupower.c:317-326), and L2_PWROFF never under unsettled shaders (:23, :400-408). In g2power it wrote nothing more either.
   - Next boot: in g2power a reset cleared the stuck state. The next run's precheck (g2stack) saw READY 0 and PWRTRANS 0 (doc 27 l.53, 75, observed).
   - Android: kbase resets the GPU at init anyway (`kbase_pm_init_hw` without PM_NO_RESET, pm_driver.c:3763-3778; soft reset 4286-4306, hard-reset fallback 4312). The leftover GPU_IRQ_RAWSTAT power bits and JOB/MMU raw bits are cleared by that reset [read from source].
   - Leaving the cores powered after a successful job is consistent with the handover design (doc 30). Nothing else in XNU touches the GPU. GPU/JOB/MMU IRQ masks are 0 (observed PRE_GPU_IRQ_MASK=0, doc 27 l.51) and XNU has no handler for SPIs 419-421 [read from source]. The cost is idle power on BUCK2S/BUCK8S at 600 mV [guess: small].

3. **Memory**:
   - `kmem_alloc_contig(kernel_map, &va, 0x8000, PAGE_MASK, 0, 0, KMA_DATA|KMA_ZERO|KMA_PERMANENT, ...)` gives 2 physically contiguous 16 KB pages in ascending order (`cpm_allocate`, vm_resident.c:6538-6595, "ordered by ascending physical address", zero-filled with KMA_ZERO). The VA is 16 KB aligned.
   - The same call with the same flags is the UFS DMA buffer (patch 0028:519), observed working on hardware.
   - `kvtophys(va)` is the PA of the first page. pa is 16 KB aligned, so all four tables are 4 KB aligned. The data page is pa+0x4000.
   - `flush_dcache64(addr64_t, unsigned, int)` (caches_internal.h:43) branches to `flush_dcache(addr, len, phys)` (caches_asm.s:424-430). With phys=TRUE it walks PA ranges through `phystokv_range` and runs `FlushPoC_DcacheRegion`: `dmb sy`, then `dc civac` per 64 B line, then `dsb sy` (caches.c:61-91, caches_asm.s:394-418).
   - That path is real on this target: SWEET.h (patch 0004) defines neither `__ARM_COHERENT_IO__` (only apple_arm64_common.h:36 does) nor `APPLE_ARM64_ARCH_FAMILY`, so the no-op variants are not built. The built kernel contains `dc civac, x0` (0xD50B7E20) once, and UFS uses the same call (0028:156).
   - Cleaning through the physmap alias cleans the kernel-VA alias too, because the D-cache is PIPT [read from source, ARM arch].
   - Write-back hazard: the CPU writes nothing to the data page after the first flush (patch:197). Speculative refills are clean lines, so the clean+invalidate at patch:215 cannot write back over the GPU's result. The read at patch:216 comes after the invalidate, so it fetches from PoC.
   - GPU side: the leaf uses MEMATTR index 5 (non-cacheable) and the walker is non-shareable (PTW_SH 0). COHERENCY_ENABLE=0 does not matter because nothing is shareable. START/END_FLUSH_CLEAN_INVALIDATE handle the GPU L2. Same reasoning as doc 28 s.5, which was observed working in job1. That the gs101 SLC lies beyond PoC is a [guess].

4. **Ordering and timing**:
   - All MMIO goes through one `ml_io_map` Device mapping of the same peripheral, as volatile 32-bit accesses, so it stays in program order. The `dsb sy` at the end of `flush_dcache` orders the table writes and the clean before the first AS0 write. The flush call is also a compiler barrier.
   - 64-bit RAM stores are 8-byte aligned, and their little-endian layout is identical to the shim's two 32-bit words. AS/JS 64-bit registers are written LO then HI, as in the shim (observed).
   - `delay(int usec)` takes microseconds (clock.c:1632-1636). Early in boot `cpu_idle_latency` is 0, so `ml_delay_should_spin` returns FALSE (machine_routines.c:2332-2344) and each delay blocks with `assert_wait_deadline`. That is fine on the bsd_init thread (preemption on, no lock held: `sweet_ufs_lock` is released before the hook, 0028:533-541).
   - Each WAIT is 5000 polls and at least 100 ms. Worst case for the whole function: about 9 waits, roughly 1 s plus scheduling overhead. No unbounded loop.

5. **Hang paths**:
   - Every wait is bounded. A job that never completes gets HARD_STOP, then AS0 is set to UNMAPPED, so any later GPU access faults instead of reaching RAM.
   - After `as0_update(ON)` succeeds there is no early return: patch:202-231 is straight-line code and always reaches `as0_update(TRANSCFG_OFF)`.
   - The only uncaught hang is a bus stall on an MMIO access, the same risk 0102 already took (u1 observed no hang).
   - With cpus=1 the root mount is delayed by at most about 1 s.

6. **Repeated calls**: `sweet_gpu_gs101_init` has the static `started` guard (0102) and is called only on `sweet_ufs_init` success, so the job runs at most once per boot. The hook is behind the 0102 PMU/CMU gate and boot-arg `sweetgpujob=1` exactly (patch:38-43). No unused defines.

## Conditions for the one run

- Kernel built from this patch stack, checked by sha256 against what `tools/xrun` boots. `tools/fboot.sh` / `fastboot boot` only; nothing flashed except userdata, if needed.
- Shim with `--boot-xnu --gpu-handover`. Before reading the XNU result, check the shim's pstore line `GPU handover rail (0=on)` = 0. If it is not 0, expect "shaders did not come up"; that is a safe, expected outcome.
- Command: `tools/xrun --kernel K --gpu-handover --args "sweetgpujob=1" --label gpujob1 --expect 'SWEET GPUJOB: '`. Run it as one background job with the USB UDP log on, `--cpus 1` (default), no `--dvfs`, `--qr off`.
  `--gpu-handover` already adds `sweetgpu=0x1C500000` (build-xnu-image.sh:33). Do not add it twice, and use no other GPU boot-arg.
- Before relying on the result, check that the same boot's 0102 lines match u1: PMU 0x1/0x1, muxes 0x10, GPU_ID 0x92020010, all READY and PWRTRANS 0, JOB/MMU RAW 0, GPU_FAULTSTATUS 0, S2MPU CTRL0=0. If any differs, treat the run as void (M1/M2 would have refused) and fix M1/M2 before re-running.
- Pass criterion: `SWEET GPUJOB: submit=ok wait=done ... target=... (MAGIC) as0_off=ok`. Afterwards the phone must reach `tools/phone goto fastboot` / android normally; a cold reboot is enough to clear the GPU.

## Driver response (2026-10-23)
Fixed in 0103: M1 (`sg_precheck`: GPU_ID, present masks incl. HI words, GPU_STATUS/FAULT, JS_PRESENT bit0, JS0_FEATURES bit2, any JOB/MMU raw bit, AS0 idle, CMD_NEXT/JS0_STATUS 0, S2MPU CTRL0 all 0 passed from 0102; zero writes on any mismatch), M2 (power only from fully off or fully on), L1 (`stopped` guards the second hard stop), L2 (TILER_PWRTRANS in the wait), L5 (physical address sanity), L6 (comment), once-per-boot guard. Not done: L3 partly (refuses on any JOB/MMU raw bit now), L4 summary fields, L7 (accepted). Kernel sha256 d2aed0f9..., stack 103/103, local build clean.
