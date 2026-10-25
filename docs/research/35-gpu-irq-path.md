# 35: taking the Mali-G78 JOB/MMU/GPU interrupts in XNU instead of polling (desk research, 2026-10-23)

Host-only read; nothing run on hardware by this note. Tags: **[src F:L]** read from source (file opened by me), **[obs]** observed on hardware (log/handoff
cited), **[guess]** not checked. Paths:

- `T` = `~/pixelios-artifacts/localbuild/work/main/xnu` (the patched xnu-8796 tree of the local build; the line numbers of `pe_fiq.c`/`sleh.c` are identical in
  `work/7a7e487806ab/xnu`). It is the stack of `xnu/patches/` 0004/0017/0018/0063, so `T/...` lines are what runs.
- `K` = the kernel worktree `~/.t3/worktrees/pixelios-gpu/kernel`; `R` = `~/pixelios-artifacts/refs`.
- Answers the M2 finding of `K/docs/reviews/gpu-mali-reset-review-1.md:58-77`.

## 1. Summary

1. GPU lines: SPI 420 JOB, 421 MMU, 419 GPU, all level-high, so INTID 452, 453, 451. The names and trigger type are confirmed in the DTS I opened; the numeric
   values come from doc 17 (header `gs101.h:435-438`), which I could NOT open (see s.2 and Unknown U1).
2. The review's M2 description is right in outcome and slightly wrong in mechanism: `sleh_irq` does NOT just EOI an unknown INTID, it calls
   `cdp->interrupt_handler` for every INTID >= 16 except the timer, but on this port that pointer is NULL (nobody installs it), so the effect is the same:
   EOI and return. A level SPI that stays asserted re-enters immediately (s.4).
3. A safe path needs two things before any SPI is enabled: (a) a safety net in `sleh_irq` that MASKS (GICD_ICENABLER) any SPI it has no handler for and counts it,
   which removes the M2 hazard for every SPI forever, and (b) a read-only probe of the distributor state (s.7) to learn what ABL left.
4. The safest GPU design never lets the line assert unless a handler is installed: the Mali line is `RAWSTAT & MASK`, the masks are 0 today [obs], so the GIC
   enable can even stay set without a storm. Handler = read STATUS, write that source's MASK to 0 (quiets the level line, keeps RAWSTAT), set a flag, `thread_wakeup`;
   the existing `check_job()` logic in `sm_thread` then does the clearing and re-arms the mask.
5. Do not use SPI 419 (GPU) at all at first: reset completion is polled and passed on hardware with the GPU mask at 0 [obs].

## 2. The GIC and the GPU SPIs

| Fact | Value | Tag / source |
|---|---|---|
| GICD base / size | `0x10400000` / `0x10000` | [src `K/soc/google/gs101/soc.json:4-5`] |
| GICR base / size / stride | `0x10440000` / `0x100000` / `0x20000` | [src `soc.json:6-8`] |
| DT node given to XNU | `/arm-io/gic`, `reg` = GICD off,size, GICR off,size (offsets from SOC_BASE), `virtual-timer-intid` | [src `K/tools/mkadt.py:178-183`] |
| XNU maps GICD with | `ml_io_map(soc_base_phys + gicd_base_prop, gicd_size_prop)`, static `gicd_base` in pe_fiq.c, so the whole 64 KiB incl. IROUTER 0x6000.. is mapped | [src `T/pexpert/arm/pe_fiq.c:25,130`] |
| GICD_TYPER | `0x79041E`: ITLinesNumber = 0x1E = 30, so (30+1)*32 = 992 INTIDs; 451-453 are in range. SecurityExtn bit 10 = 1 (two security states) | [obs: shim log `logs/usb-20261021T070620Z.txt` "GICD_TYPER 000000000079041E"; decode by me, [guess] on the field layout from the GICv3 spec, not opened] |
| GICD_CTLR as ABL leaves it | `0x10` (ARE_NS only, no Group enable) | [obs: same log "GICD_CTLR 0000000000000010"; also `T/pexpert/arm/pe_fiq.c:207` comment] |
| GPU node | `mali@1C500000`, `interrupts = <GIC_SPI IRQ_G3D_IRQJOB_G3D LEVEL_HIGH>, <.. IRQMMU ..>, <.. IRQGPU ..>`, `interrupt-names = "JOB","MMU","GPU"` | [src `R/kbase-bluejay/gs101-gpu.dtsi:22-28`] |
| Numeric values JOB 420, MMU 421, GPU 419, EVENT 418 | from `gs101.h:435-438` | [src, SECOND HAND: `K/docs/research/17-gpu-kernel-gs101.md:104`; I could not open the header: the gitiles URLs I tried returned 404 and no local copy exists] |
| INTIDs | SPI n = INTID n+32: JOB 452, MMU 453, GPU 451 (EVENT 450, unused by the DT node) | [src doc 17:104] |

Mali IRQ registers (offsets from the GPU base 0x1C500000), checked in the kbase copy `R/gpu-full/mali_kbase`:

- GPU: RAWSTAT 0x20, CLEAR 0x24, MASK 0x28, STATUS 0x2C [src `hw_access/regmap/mali_kbase_regmap_jm_enums.h:45` (STATUS 0x2C); RAW/CLEAR/MASK 0x20/0x24/0x28 from `K/xnu/patches/0104-sweet-mali-driver-core.patch:103-109`, observed working].
- JOB: RAWSTAT 0x1000, CLEAR 0x1004, MASK 0x1008, STATUS 0x100C [src `..._jm_enums.h:112-115`].
- MMU: RAWSTAT 0x2000, CLEAR 0x2004, MASK 0x2008, STATUS 0x200C [src `..._jm_enums.h:429` (STATUS 0x200C); the others `0104.patch:135-137`].
- kbase's handlers read `*_IRQ_STATUS` (not RAWSTAT) and treat 0 as "not ours": `R/gpu-full/mali_kbase/backend/gpu/mali_kbase_irq_linux.c:56,93,159` (lines 56 JOB, 93 MMU, 159 GPU). It also returns IRQ_NONE when the GPU is not powered (`:46-52`): reading a Mali register with the domain off is unsafe, so the handler must check a "powered" flag [src].
- STATUS = RAWSTAT & MASK is the Mali convention [guess: implied by kbase reading STATUS and by u1/malireset1 showing RAWSTAT bit 8 set with mask 0; the AND itself is not in a file I opened]. malireset1 [obs, `super/docs/HANDOFF.md:217`]: "GPU irq mask kept 0, RESET_COMPLETED raw bit still sets".

## 3. How XNU on this port handles interrupts (read from the patched tree)

1. There is NO interrupt-controller driver. Everything is in `pexpert/arm/pe_fiq.c` + `osfmk/arm64/sleh.c` from patches 0004/0017.
2. `pe_init_fiq` (per CPU) [src `T/pexpert/arm/pe_fiq.c`]:
   - finds this CPU's redistributor by MPIDR affinity (`:54-73`), wakes it (`:150-165`);
   - `GICR_IGROUPR0 = 0xFFFFFFFF`, priorities 0xA0, `GICR_ISENABLER0 = vtimer PPI | SGI1` (`:174-178`);
   - `ICC_SRE`, `ICC_BPR1 = 0`, `ICC_PMR = 0xFF`, EOImode 0 (`:187-203`);
   - GICD_CTLR |= ARE_NS | EnableGrp1A | EnableGrp1, wait RWP, `ICC_IGRPEN1_EL1 = 1` (`:206-215`). This is the point where Group 1 NS forwarding starts (ABL left it off: CTLR 0x10).
   - The only GICD write is GICD_CTLR. No `GICD_ISENABLER/ICENABLER/IGROUPR/IROUTER/ICFGR/IPRIORITYR` is written anywhere (grep of `T/pexpert/arm/pe_fiq.c`, `T/osfmk/arm64/sleh.c`, `K/xnu/patches/*.patch` for GICD_: only `GICD_CTLR` in 0004's SWEET.h). [src]
3. The shim also touches no SPI state: only GICR registers and GICD_CTLR (`K/shim/shim.c:49-59,619-625,1174-1176`). [src]
4. `sleh_irq` SWEET branch [src `T/osfmk/arm64/sleh.c:1851-1900`]:
   - reads `ICC_IAR1_EL1`; INTID 1020-1023: return, no EOI;
   - `sweet_vtimer_intid`: `rtclock_intr`, EOIR1;
   - INTID < 16: EOIR1 then `cpu_signal_handler()`;
   - else (PPIs other than the timer, ALL SPIs): `if (cdp->interrupt_handler) cdp->interrupt_handler(target, refCon, nub, intid)`, then EOIR1.
5. `cdp->interrupt_handler` is set only by `ml_install_interrupt_handler` (`T/osfmk/arm64/machine_routines.c:797-815`), whose only caller in the tree is `IOCPUInterruptController::enableCPUInterrupt` (`T/iokit/Kernel/IOCPU.cpp:627-634`), which nothing in `K/xnu/patches/` calls (grep `IOCPUInterruptController|enableCPUInterrupt|ml_install_interrupt_handler` over the patches: no hit). Therefore the pointer is NULL on this port [src; the closed Apple kext that would call it is absent, [guess] that nothing else does]. Hazard if it were ever set: that handler indexes `vectors[source]` with the INTID (`IOCPU.cpp:738-751`), out of bounds for an SPI. So an SPI branch must come before that hook.
6. Other drivers: touch (patch 0088), keys (0087), USB (0070-0082), battery, UFS are all polled; the touch IRQ pin is only read as a pad (`0088.patch:273,526,571`). No driver in the stack takes a GIC interrupt. [src grep]. The shim/Linux-side `IRQ` use is irrelevant here.
7. Security-state consequence [obs + guess]: `pe_fiq.c:170-173` records that `GICR_IGROUPR0` "reads as zero from the non-secure side even after this write; delivery still works". So NS reads of group state are not trustworthy; only an interrupt that actually reaches `ICC_IAR1_EL1` is proof it is NS Group 1. [guess, GICv3 two-security-state rule, spec not opened]: a Group 0 or Group 1 Secure SPI is never presented as an NS IRQ; it cannot storm `sleh_irq`, and NS writes to its enable/group bits are ignored.

### What is left enabled in GICD_ISENABLERn

Unknown. No log or doc has ever read GICD_ISENABLER/ISPENDR/ISACTIVER (grep of `K/docs`, `super/docs`, the shim and `logs/` for ISENABLER: only the GICR variant is used in code, the review quotes `GICD_ISENABLER14` as a suggestion). Reset value is 0 for all SPIs [guess, architecture], ABL/TrustZone may enable what they own (ABL uses UFS/USB/display/UART). The distributor does not forward Group 1 NS until XNU's `GICD_CTLR` write, so any NS SPI enabled by ABL becomes live exactly at `pe_init_fiq`. This means the hazard is general (every SPI ABL left enabled and asserted), not specific to the GPU, and it already existed since 0004: a stuck level SPI would have hung every boot, which has not happened [obs: many runs]. That makes the GPU lines (domain powered off by ABL; masks 0) very probably clean, but only the probe proves it.

## 4. Why an unmasked level SPI hangs the only CPU (confirms review M2)

Level-high line asserted, enabled, Group 1 NS, routed to this CPU: take IAR1 -> no handler -> EOIR1 (EOImode 0 = priority drop + deactivate) -> the line is still high -> pending again -> IRQ re-enters at once. The interrupted thread never gets enough time to write `*_IRQ_CLEAR`/`MASK`. [src sleh.c:1881-1890 + pe_fiq.c:198-203; the re-entry is architecture, [guess]]. Note the SPURIOUS path (`sleh.c:1867`) returns without `sleh_interrupt_handler_epilogue`, harmless here.

## 5. The sibling port solved the same problem (idea reference only; AGENTS: no code copying)

`R/hackios-xnu-kernel/xnu/files/osfmk/arm64/gic_v3_irq.c` (header comment `:1-27`): a table of attached SPIs (`:92-110`), `gic_irq_handler` loop (`:~330-360`) that for an unowned INTID does `gic_mask(intid)` + counts it ("keep it from storming"), and the attach recipe (`:~245-285`): mask, clear pending and active, set Group 1 (IGROUPR |= bit, IGRPMODR &= ~bit), priority 0xA0, ICFGR level/edge, `IROUTER = MPIDR affinity of the attaching CPU (Aff3 in [39:32], Aff2.1.0 in [23:0], bit 31 clear)`, settle RWP, then ISENABLER. Register offset macros `:60-71` match the GICv3 layout I use below. This is a design reference; the register values must still be verified on gs101 by the probe.

## 6. Register map for INTIDs 451/452/453 (distributor, byte offsets from GICD base)

Computed by me from the standard GICv3 layout; the macros in the sibling file confirm the bases (`gic_v3_irq.c:60-69`: IGROUPR 0x80, ISENABLER 0x100, ICENABLER 0x180, ICPENDR 0x280, ICACTIVER 0x380, IPRIORITYR 0x400, ICFGR 0xC00, IGRPMODR 0xD00, IROUTER 0x6000) [src, sibling]. The layout itself is [guess: GICv3 spec IHI0069, not opened here].

| Register | Offset | Bits for 451 / 452 / 453 |
|---|---|---|
| GICD_TYPER | 0x004 | whole word |
| GICD_CTLR | 0x000 | whole word |
| IGROUPR14 | 0x0B8 | bit 3 / 4 / 5 |
| ISENABLER14 | 0x138 | bit 3 / 4 / 5 |
| ICENABLER14 | 0x1B8 | bit 3 / 4 / 5 |
| ISPENDR14 | 0x238 | bit 3 / 4 / 5 |
| ISACTIVER14 | 0x338 | bit 3 / 4 / 5 |
| IPRIORITYR (byte) | 0x5C3 / 0x5C4 / 0x5C5 | 8 bits each |
| ICFGR28 | 0xC70 | [7:6] / [9:8] / [11:10]; bit1 of each pair 1 = edge, 0 = level |
| IGRPMODR14 | 0xD38 | bit 3 / 4 / 5 |
| IROUTER (64-bit) | 0x6E18 / 0x6E20 / 0x6E28 | Aff3 [39:32], Aff2.Aff1.Aff0 [23:0], bit 31 = any-CPU |

The review's `GICD_ISENABLER14 = GICD+0x138, bit 3` (`gpu-mali-reset-review-1.md:75`) matches. All offsets < 0x10000, inside the mapped GICD.

## 7. The hardware probe (read-only, no GIC write)

Purpose: learn what firmware left in the distributor before any enable. Do it from the driver, not from `pe_fiq.c`: `kprintf` in `pe_init_fiq` goes to the serial path and no `SWEET GIC:` line appears in the USB logs I grepped [obs: `grep -a "SWEET GIC" logs/usb-*.txt` empty], whereas `printf` from `sweet_mali.c` reaches the USB UDP log [obs: `SWEET MALI:` lines in runs]. XNU never touches SPI enables, so the state read in the driver equals what ABL/the shim left.

Probe (add to `sweet_mali.c` init, once, before the existing `sweetmali` stages; reads only):

1. Map `gicd_base 0x10400000`, size `0x10000` (from `soc.json`) with `ml_io_map` (the same range is already mapped by pe_fiq.c; a second read-only mapping is fine [guess]; or expose an accessor in pe_fiq.c).
2. Print one line `SWEET GICPROBE`: `TYPER`, `CTLR`, then for the three INTIDs: `IGROUPR14`, `ISENABLER14`, `ISPENDR14`, `ISACTIVER14`, `ICFGR28`, `IPRIORITYR[0x5C3..0x5C5]`, `IROUTER 0x6E18/20/28`.
3. Scan words `ISENABLER0..30` (0x104..0x178; word 0 is SGI/PPI banked in the redistributor) and print only the non-zero ones together with the matching `ISPENDR` and `ISACTIVER` words: this answers "what does firmware leave enabled" for ALL SPIs, the general hazard of s.3.
4. Print, at the same time, `JOB_IRQ_MASK`, `MMU_IRQ_MASK`, `GPU_IRQ_MASK` and `*_IRQ_STATUS` (needs the GPU domain on; the driver's init already powers it) to prove the line is low.
5. Run condition: same as malireset1 (`--gpu-handover`, `--cpus 1`, `--qr off`, USB log, no `sweetgpujob`), `fastboot boot` only. Pass = the line is printed and the boot goes on. Decision table:
   - `ISENABLER14` bits 3/4/5 all 0 and no other word non-zero: nothing enabled; stage B may proceed.
   - Any of bits 3/4/5 = 1: do NOT enable anything; record IGROUPR/ICFGR/IROUTER and stop (a firmware-owned enable; ask what ABL does with SPI 451-453).
   - Other words non-zero: list them; they are not ours, but write down INTID -> owner, because the safety net of s.8 step 1 will mask any that fire.
   - The value of an NS read cannot separate "disabled" from "secure, hidden from NS" [guess]; that is acceptable for the hazard question (a hidden interrupt is not delivered to our IRQ), and stage B settles ownership by readback.

## 8. Proposed design (for the implementer; not code)

Order matters; each step is its own patch/run.

1. **Safety net (patch 0106 or next free number; the other thread has 0105 modified).** In `sleh_irq` SWEET branch, handle `intid >= 32 && intid < 1020` BEFORE the `cdp->interrupt_handler` hook (s.3 item 5: its OOB `vectors[]` index). Look up a tiny static table `{intid, fn, arg, count}` (4 slots; no lock if filled before the enable); no entry -> write `GICD_ICENABLER[intid/32] = 1<<(intid%32)` (needs a `gicd_base` accessor/exported helper from `pe_fiq.c`), increment a global `unhandled` counter and remember `last_intid`, then EOIR1. Do not printf in the handler. This makes M2 harmless for every SPI, including whatever ABL left. Provide `sweet_spi_attach()`/`detach()` mirroring the sibling recipe of s.5. Host-testable (table logic) and QEMU-runnable ([guess] QEMU virt has a GICv3, `mkadt.py:35` already targets it).
2. **Probe run** (s.7). Gate everything below on its result.
3. **Attach SPI 452 (JOB) and 453 (MMU) only**, from the driver after `sm_hw_config_and_power`, with all three Mali masks still 0 (line low). The attach (thread context, GPU masks 0): ICENABLER, ICPENDR, ICACTIVER, IGROUPR |= bit, IGRPMODR &= ~bit, priority 0xA0, ICFGR = level (0b00), IROUTER = boot-CPU affinity (`MPIDR_EL1` Aff3 and Aff2:0, bit 31 clear), wait `GICD_CTLR.RWP`, ISENABLER. Read back ISENABLER and IROUTER and print them: bit not sticking = NS cannot own this SPI; then fall back to polling and do not retry. [obs of results needed]
4. **Handler body** (interrupt context, no locks, no printf, no sleeping):
   - if `!sm.gpu_on` return (do not touch registers with the domain off: kbase does the same, `mali_kbase_irq_linux.c:46-52`);
   - `st = JOB_IRQ_STATUS` (or `MMU_IRQ_STATUS`); if 0 count a spurious and return;
   - write that source's `*_IRQ_MASK = 0` (this drops the level line and keeps RAWSTAT, so `check_job()` still sees the bits through `R_JOB_RAW`/`R_MMU_RAW`);
   - `os_atomic_or(&sm.irq_seen, bit)`, `thread_wakeup((event_t)&sm.thread)` (the event `sm_thread` already sleeps on, `0104.patch:358`; `thread_wakeup` from IRQ context is the usual path [guess; `T`-pristine `osfmk/kern/sched_prim.c:1744` shows it takes only the global waitq]).
   - Do NOT write `*_IRQ_CLEAR` in the ISR; clearing stays in `check_job()` (`0104.patch:309-325`) so the fault/stop logic is unchanged.
5. **sm_thread change.** While busy: sleep with a fallback deadline of a few ms (not 200 us) instead of polling; after `check_job()` finishes a job or handled a fault, re-arm: `JOB_IRQ_MASK = (1<<slot)|(0x10000<<slot)`, `MMU_IRQ_MASK = 0xFFFFFFFF`, when a job is submitted (before `JS_COMMAND_NEXT`), and `= 0` when idle. Race to document: the ISR may fire between `check_job()`'s register read and `lck_mtx_sleep_deadline` (which does `assert_wait` first, `T`-pristine `osfmk/kern/locks.c:1179-1182`, then unlocks); the wakeup then finds no waiter and the thread sleeps its full deadline, so the fallback deadline is the correctness bound and also the old timeout logic (`JOB_TIMEOUT_US`, `STOP_GRACE_US`) keeps working unchanged. [src for assert_wait order; the race itself is analysis]
6. **Reset interplay** (`sm_reset_do`, `0104.patch:~587-620`): it already writes JOB/MMU/GPU masks to 0 and clears all RAW bits before `GPU_COMMAND = SOFT_RESET` and polls `GPU_IRQ_RAWSTAT` bit 8 with the GPU mask 0 (works [obs malireset1]). Keep that: with the masks at 0 the lines are low through the reset, so the SPIs can stay attached; do not take SPI 419 at all (no benefit, it is the line the reset wiggles). After reset `sm_hw_config_and_power` writes masks 0 again; re-arming happens only on the next submit (step 5). Nothing to change in the reset body except that the reset must run with `irq_seen` cleared after it. About b/290409680: all I know is the kbase comment "unmask irq before the soft reset" (`R/gpu-full/mali_kbase/backend/gpu/mali_kbase_pm_driver.c:4333`) and that kbase waits on an ISR-driven completion; the bug text is not available, so [guess] it is a lost-completion workaround that does not apply to a RAWSTAT poll. malireset1 passed with the mask at 0 [obs]; leave it so. If a later run shows RESET_COMPLETED never sets without the unmask, only then attach SPI 451 with the same net and unmask bit 8 for the reset window alone.
7. **Detach/dead path:** `sm.dead` or any teardown: masks to 0, `ICENABLER` for 452/453, then detach, then stop touching registers. Never leave the SPI enabled with the GPU domain off.
8. **First hardware run of the handler (after steps 1 and 3):** `--cpus 1`, USB log, no other change; success = `irq_seen` set for slot 0 at the self-test job, same `RESULT js0=PASS js1=PASS`, counters printed at the end (JOB irqs >= 2, spurious 0, `unhandled` 0, no `MMU` irq). Poll-only comparison: run once with `sweetmali=` bit that keeps the poll to compare latency. Fail-safe: if `unhandled` != 0 or the CPU stalls, the net masks it; a stall with no log after `selftest RESULT` is the old M2 signature.

## 9. Unknown / to settle (nothing below was measured or opened)

- U1. Numeric SPI values 419/420/421: the DTS names and LEVEL_HIGH are confirmed, the numbers rest on doc 17 citing `gs101.h:435-438`; I could not open the header (404 on the gitiles guesses; no local copy). Settle: fetch `include/dt-bindings/interrupt-controller/gs101.h` from the soc-gs repo (the repo/branch doc 17 used), or have the probe show that INTIDs 452/453 respond (ISPENDR toggles when the job IRQ is unmasked in stage B).
- U2. What ABL/TrustZone left in GICD_ISENABLERn/IGROUPRn: no observation exists. Settle: s.7 probe.
- U3. Whether NS can set group/enable/route for these SPIs (security configuration): settle by the stage B readback of s.8 step 3.
- U4. `STATUS = RAWSTAT & MASK` and the exact timing/glitching of the lines during reset: assumed from kbase usage and the u1/malireset1 observations, not shown. Settle: the probe step 4 prints STATUS and MASK/RAW together; the first handler run counts spurious ISRs.
- U5. Content of Pixel bug b/290409680: not available offline.
- U6. Whether `thread_wakeup` and an `os_atomic` flag are all that is needed under `MACH_ASSERT` rules for IRQ-context wakeups on this build: stock XNU IOKit does it from filter/handler contexts [guess]. Settle: host build + first run with `unhandled`/panic check.
- U7. IROUTER value format on gs101: taken from the sibling port and the standard affinity encoding; the boot CPU is the `GICR_TYPER` affinity 0 core per the shim log ("CPU GICR_TYPER 0000010000000120" for CPU1 shows Aff1 = 1 for the next cores) [obs partly]. Settle: print `IROUTER` after write and `MPIDR_EL1`.

## 10. Implementation steps (drafted, in order)

1. Read-only probe (s.7) in `sweet_mali.c`, controlled by a `sweetmali` bit; build with the standard gate; one batched hardware run `--cpus 1`; record the printed words in `super/docs/HANDOFF.md` as [obs].
2. If the decision table allows: patch "sleh SPI safety net + SPI attach table" (new numbered patch, next free after the other thread's 0105 work): pe_fiq.c accessor for `gicd_base`, `sleh_irq` SPI branch ahead of the `interrupt_handler` hook with mask-and-count for unowned SPIs, `sweet_spi_attach/detach`; host test of the table; update `AGENTS`-listed docs; gate.
3. Run with nothing attached (net only) to prove no regressions (timer, IPI, USB log).
4. Driver: attach 452/453 with masks at 0, print readbacks (ISENABLER, IROUTER, IGROUPR), run, read results.
5. Driver: ISR body and `sm_thread` rework per s.8 steps 4-5, keeping the 3 s job timeout and hard-stop logic; keep a bit in `sweetmali=` to force polling.
6. Reset-path check: re-run the malireset1 recipe (`sweetmali=0x22`) with IRQ mode on; same pass criteria as the review's run conditions (second `config before` all zeros, `selftest reset=PASS job-after-reset=PASS`) plus `unhandled=0`.
7. Write the antipattern/handoff entries; tick the plan task with the run label.

## Addendum (2026-10-24): SPI numbers cross-checked [src]
A second desk source (postmarketOS gs101 device tree, reported by the driver session) has `gpu@1c500000`, size `0x5000`, interrupts SPI 420 (JOB), 421 (MMU), 419 (GPU), Panfrost enabled. This matches our measured base/size (0x1C500000 / 0x5000, run u1) and the numbers taken from doc 17, so the "numeric SPI values second-hand" open point is reduced to: the INTID = SPI + 32 mapping (451/452/453) is still to be confirmed by the read-only GIC probe on hardware (ISENABLER/ICFGR rows).
