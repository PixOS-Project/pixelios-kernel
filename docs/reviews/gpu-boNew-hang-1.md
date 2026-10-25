# Review: gputest2 "hang" after the SweetMali query (boNew -> mapMemory -> sweet_mali_map)

Reviewer: Claude Sonnet 5.5, adversarial, read-only. Run: `logs/runs/20261023T162838Z-gputest2` (kernel sha256 49d75a924561, git f82dfe6, i.e. before the 9d59d10 step printfs).
Code read: scratch tree `~/pixelios-artifacts/scratch/stack-nCqoRx` (`bsd/dev/sweet_mali.c`, `bsd/dev/sweet_mali_pt.h`, `iokit/Kernel/arm/SweetMali.cpp`, XNU `osfmk`/`iokit`), test `userland/tools/sweetmali-test/sweetmali_test.c`.
Tags: [obs] observed in the run artefacts, [src] read from source (file:line), [guess].

## 0. The central finding: it was not a silent hang, it was a kernel PANIC (observed on the camera frames)

[obs] `frames/f183603.jpg` (+4 s after the last log line) and every later frame (f183619, f183635, f183651, ... to the end of the run) show the iOS panic screen (Apple logo + progress bar) with text. Zoomed (ffmpeg crop/scale of f183619) it reads, line by line:

    panic(cpu 0 caller 0xfffffe00........): phystokv_range: illegal PA: 0x573d.....; phys base 0x........, size 0x1........ arm_vm_init.c:1486(?)
    Original panic string:  (the same line again)
    handshake structure not initialized
    Please go to https://panic.apple.com to report this panic

Camera resolution does not let me read the PA, the caller or the phys base digits (the PA starts "0x573d", the rest is unreadable; the line number is "...486"). Everything else in the text is legible. The luma series (`luma.json`) steps from 68.1 to 73.1 between t=359.3 s and t=363.3 s (the panic screen replacing the GUI), then stays flat: the machine is parked in the panic handler.

The earlier diagnosis "hard hang, no panic output" is therefore wrong: the panic is on the screen, only the USB log (and QR, which is off) did not carry it.

Why the run did not restart and the USB log is silent:
- [src] `osfmk/kern/debug.c:1408`: after a panic the kernel reboots only if `debug_boot_arg & DB_REBOOT_ALWAYS` (`osfmk/kern/debug.h:540` = 0x100000), else it goes on to the debugger wait / "Original panic string" print (`debug.c:1436-1440`, which is exactly the repeated line on the screen). The run's `debug=0xc` has neither DB_REBOOT_ALWAYS nor DB_REBOOT_POST_CORE (0x4000, `debug.h:527`).
- [src] `sweetrestart=600` is polled only from running threads (`xnu/patches/0083-sweet-restart.patch`, `sweet_restart_poll`, "Polled from threads that are known to run"); after a panic every other thread is stopped (cpus=1), so the timer never fires. `PE_halt_restart` (`sweet_halt_restart`) would reset the phone on `kPEPanicRestartCPU`, but with `debug=0xc` the panic path does not call it.
- [obs] control: gputest1 (same kernel sha, same boot-args except the test job never launched, `run.json`) was reset by sweetrestart at +756 s. So the restart mechanism works when no panic happens, and the panic is caused by what the test did.
- "handshake structure not initialized" is `osfmk/kdp/output_stages/out_shmem.c:499`: the core-dump stage is not configured; harmless noise, not a second panic.

## 1. Where the panic comes from

[src] The message is `phystokv_range` (`osfmk/arm64/arm_vm_init.c:405`, condition `pa < gPhysBase || pa - gPhysBase >= real_phys_size`, line 404). Its callers (grep over osfmk/iokit/bsd): `osfmk/arm/caches.c:75` (`flush_dcache`, which `flush_dcache64` branches to, `osfmk/arm64/caches_asm.s:447`, with `phys=TRUE`), `caches.c:109` (`clean_dcache`), `caches.c:168/228` (`dcache_incoherent_io_*`), `loose_ends.c:139/147/234` (bcopy_phys, ml_phys_read), `pmap.c:2313`. In this driver only `flush_dcache64(pa, len, TRUE)` takes a physical address, so the panic is a `flush_dcache64` of a PA that is outside `[gPhysBase, gPhysBase + real_phys_size)`. Nothing in the call is checked: `sweet_mali_map` only tests alignment and `< 1<<40` (`sweet_mali.c:348`).

The three call sites that can see a PA the selftest never saw:

| # | site | PA source |
|---|------|-----------|
| A | `sweet_mali.c:359` `flush_dcache64(pa16k[i], PAGE_SIZE, TRUE)` | `md->getPhysicalSegment(.., kIOMemoryMapperNone)` (`SweetMali.cpp:232`), a PA taken from an IOBufferMemoryDescriptor |
| B | `sweet_mali.c:154-163` `sm_pt_clean` | `kvtophys()` of `kmem_alloc_contig` memory (`sweet_mali.c:146`), same as in the selftest (mali1, malireset1 PASS) |
| C | `SweetMali.cpp:341` `boCacheSync` | same PAs as A; not reached (the test never calls selector 6) |

## 2. Hypotheses, ranked

### H1 (most likely): the PA that `getPhysicalSegment` returns for the BO is not a DRAM physical address (site A, `sweet_mali.c:359`)
Evidence:
- [obs] the panic is a `phystokv_range` panic, the only PA-consuming call that is new relative to the passing selftest is A. In the selftest `pa[0] = kvtophys(buf)` (`sweet_mali.c:649`) always lies in the aperture by construction; in boNew the PA is whatever `getPhysicalSegment` returns.
- [obs] timeline: query line at t=356.158, the log keeps running (exec lines at 356.763/356.864, FB swaps line at 359.295), no "step boNew ok" line (it would be printed by the unbuffered `printf` tee, `xnu/patches/0048-sweet-devnull-tee.patch`), the screen changes at 359.3-363.3 s. boNew entered at ~356.2 s, panicked a few seconds later (the process is starved on one CPU: its exec at 160.555 to its first line at 356.158 took 196 s, [obs] usbnet.log lines 1732 and 1867). That ordering matches "panic inside boNew before it returned".
- [obs] the PA starts with 0x573d: that is below any DRAM base a Pixel 6a has (DRAM starts at 0x80000000 on gs101 [guess: not verified here, the screen prints the real `phys base`]); i.e. an IOVA/"mapped" address or a `ppnum` unit mix-up rather than a DRAM address.
- [src] `iokit/Kernel/IOMemoryDescriptor.cpp:3407` `state->fMapped = !(options & kIOMemoryMapperNone) && ...` and `:3411-3425`: whether the returned address is physical depends on the descriptor type/mapper; `kIOMemoryMapperNone` is passed in the driver, so a system mapper translation should be skipped, but this was never checked on hardware for an IOBMD with `kIOMemoryKernelUserShared` (SweetSurface calls `flush_dcache64` on IOBMD pages? not verified: `SweetSurface.cpp` was only grepped for `memory->map`, `SweetSurface.cpp:101`).
Cheapest discriminator: the PA printed just before the flush. 9d59d10 already adds `printf("SWEET MALI: uc map n=%u pa0=%#llx")` (`SweetMali.cpp` step printf, 0105 patch hunk "uc map") before `sweet_mali_map`; run it, and also read the PA from the panic screen. If `pa0` is the panic PA: H1 confirmed and the `p == 0 || seg < page_size` check at `SweetMali.cpp:233` needs a range check against DRAM (`pa >= gPhysBase && pa - gPhysBase < real_phys_size`, or use `pmap_find_phys`/`kvtophys(md->getBytesNoCopy() + off)` as the selftest does).

### H2: the PA is a legal DRAM address but lies in a hole/above `real_phys_size` (site A)
[guess] 6 GB phones have DRAM split around the 4 GB boundary; a page from the upper bank may be outside `[gPhysBase, gPhysBase + real_phys_size)` if the ADT/aperture size covers only the first bank. Does not fit the visible "0x573d" prefix (the prefix is below 2 GB), so ranked below H1. Same discriminator: pa0 against the printed `phys base`/`size` on the panic screen. (Mark: not observed.)

### H3: site B (`sm_pt_clean`) flushing a bad `l*_pa` (low)
[src] `sweet_mali_pt.h` / `sweet_mali.c:146` take PAs from `kvtophys` of `kmem_alloc_contig`, identical to the selftest which passed. The only new thing is that in gputest2 the first-ever map (which allocates the L2/L3 tables, `sweet_mali_pt.h` `mali_pt_map`) happens in a user thread; allocation `kmem_alloc_contig(.., 0x10000, PAGE_MASK, 0, 0, ...)` (`sweet_mali.c:139`) returns memory from the same pool in both cases. Discriminator: the same pa0 printf plus a printf of `l3_pa` (see section 4).

### H4: everything else in the brief is not the cause (checked, with reasons)
- IOKit alloc `IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task, kIODirectionInOut|kIOMemoryKernelUserShared, 0x4000, 0x4000)`: [src] `IOBufferMemoryDescriptor.cpp:244-262` (alignment == page_size, not contiguous-forced), `:296-297` auto-prepare (wired, so `complete()` in `boClose` is balanced), allocation `IOMallocAligned_internal` (`:368-372`). SweetSurface uses the same call (`SweetSurface.cpp:208,692`) and runs in this very run (17000 FB swaps). Not the cause.
- AS0 flush wait under `lck_mtx`: [src] `as_idle` is bounded to 100 ms (`sweet_mali.c:171`, `WAITC` with `delay(10)`), the mutex is a sleepable `lck_mtx` and `sm_thread` releases it while sleeping (`sweet_mali.c:293`). A bounded spin would not stop the log for the whole run, and the panic screen shows the cause. Not the cause.
- `sm_thread`, IOLock: no lock-order problem seen (`fLock` then `sm_lock`; the thread takes only `sm_lock`). Not the cause.
- 16 KB page vs 4 KB leaf: [src] `mali_pt_map` per 4 KB (`sweet_mali.c:360-361`), table index `va >> 21 < 2048` matches `l3[4*512]` (`sweet_mali_pt.h` struct). Fine, and identical to the passing selftest.
- EXEC on a user-shared BO: only clears NX in the GPU PTE (`mali_pte_leaf`), no CPU effect. Not the cause.
- WriteCombine user mapping / `pmap_update_cache_attributes_locked` (`pmap.c:10632-10720`): changes the physical-aperture PTE and all mappings; SweetSurface already does `memory->map(kIOMapWriteCombineCache)` (`SweetSurface.cpp:101`) on a KernelUserShared IOBMD. It would be the next step after boNew, which never completed; not reached.
- "Spin with interrupts masked": contradicted by the camera frames (panic screen).

## 3. Defects found in the code under review (independent of the run)

1. HIGH, `sweet_mali.c:359` and `SweetMali.cpp:341`, `sweet_mali.c:154-163`: `flush_dcache64(..., TRUE)` panics on any PA outside the aperture (`arm_vm_init.c:404-406`) and `sweet_mali_map` does not validate the PA against DRAM. A user-reachable (selector 1/5/6) kernel panic. Fix: reject (EINVAL) any `pa16k[i]` with `pa < gPhysBase || pa - gPhysBase >= gPhysSize`-equivalent before flushing (or flush by KVA: `flush_dcache64((addr64_t)phystokv-free kva, len, FALSE)` using `md->getBytesNoCopy()` for BOs).
2. MEDIUM, `SweetMali.cpp:232-236`: validates only alignment/`seg`; add the DRAM range check here too (surface imports in selector 5 take the same path, `s->memory->getPhysicalSegment`).
3. MEDIUM (test infrastructure), boot-arg `debug=0xc`: a panic leaves the phone parked for ever and sweetrestart cannot fire (section 0). Fix (not applied): add `0x100000` (DB_REBOOT_ALWAYS) for GPU runs so a panic reboots; before that, a way to read the panic text is needed, otherwise the panic string is lost on reboot (QR takeover `--qr` or reading the screen photo; the camera is at low resolution). Without DB_REBOOT_ALWAYS keep the panic screen but zoom the camera on it.
4. LOW, `docs`/`xrun` report: gputest2's `report.txt` said "phone hung"; the screen frames show a panic. `xrun` could flag "panic screen" from the frames (proposal-worthy), cost of this misdiagnosis: one run + this review.

## 4. Single extra instrumentation for the next run

Print the exact physical addresses and the aperture before touching any cache by PA, with `kprintf` (serial/UART path, survives a later hang) AND `printf` (USB log). One block at the top of `sweet_mali_map` (`sweet_mali.c:352`, before the first `flush_dcache64`):

    for each i: printf("SWEET MALI: map pa[%u]=%#llx seg-ok gPhysBase=%#llx real=%#llx\n", i, pa16k[i], gPhysBase, real_phys_size)   /* kprintf too */

(`real_phys_size` is static in `arm_vm_init.c`; use `gPhysSize` if the symbol is not visible, or print `kvtophys(md->getBytesNoCopy())` next to the segment PA in `mapMemory` so that the two derivations are compared: `SweetMali.cpp:232`.) Together with the existing "uc map ... pa0" printf this answers H1/H2 with one run. The panic screen already gives the offending PA once the camera is closer; do not add per-flush printfs for the AS flush (not involved).

## 5. Discriminating observations per hypothesis

| Hyp | Observation that decides it |
|-----|-----------------------------|
| H1 | `pa0` printed < `phys base` (or equals the PA on the panic screen); `kvtophys(getBytesNoCopy())` differs from `getPhysicalSegment` |
| H2 | `pa0` >= `phys base` but `pa0 - phys base >= size` |
| H3 | `pa0` fine, panic PA equals a `l?_pa` (print them in `sm_table_alloc`, `sweet_mali.c:146`) |
| unrelated | no panic on a rerun with the printfs; then the panic string differs from "phystokv_range" |

## Verdict

NOT a silent hang: gputest2 ended in a kernel panic `phystokv_range: illegal PA` (observed on the camera frames from f183603 onward), raised by `flush_dcache64(.., TRUE)` on a PA outside the physical aperture, most likely the BO's PA from `getPhysicalSegment` at `sweet_mali.c:359` (H1, 'likely', digits partly unreadable). The kernel selftest passed because its PA came from `kvtophys`. The phone never restarted because `debug=0xc` lacks DB_REBOOT_ALWAYS (`debug.c:1408`) and sweetrestart is polled from a thread. Next run: add the PA printf block of section 4 (kprintf+printf), read the panic PA off the screen with the camera closer, and do not trust "hang" until the screen frames have been looked at (new antipattern entry added). The fixes in section 3 are not applied.
