# GPU hand-over review 1 (2026-10-23)

Scope: `git log main..HEAD` on `wip/gpu-accel` (worktree `pixelios-gpu/kernel`), focus c638f7a (`shim: --boot-xnu --gpu-handover ...`) and
a2f29f9 (`xnu 0102`). Adversarial, host only: no phone, adb or fastboot was touched, no source edited, nothing committed.
Tags: [src] read from source, [obs] observed on hardware (from the cited doc/run), [test] host test I ran, [guess].

## Findings

### M1 (MED): the hand-over leaves the rail on also after MISMATCH / UNVERIFIED, which breaks the gpurailon.h contract
- Where: `shim/shim.c:589-596`. The audit enforces it: `tools/test-bluejay-boot.py:2059-2064` rejects any `gpu_rail_restore` in the block or a second call.
- What goes wrong: `gpu_rail_enable()` returns `RAIL_UNVERIFIED` when the UPDATE was published but failed (timeout, or the read-back failed) and
  `RAIL_MISMATCH` when the mode is not 1 or OUT1 moved. `shim/gpurailon.h` says "for both ... the rail MUST be restored", and the probe flow does that
  (`shim.c:808-818`). The hand-over prints the code and jumps to XNU, so BUCK2S stays in a state nobody verified for the whole XNU run. Example: a
  50 ms ACPM timeout because the APM is still busy with the `--dvfs` request on its own channel (`touch_probe_dvfs`, `shim.c:573-588`; the DVFS
  channel is not checked by the PMIC preflight). The queued UPDATE runs later, unseen. Realistic damage is low: the only writable bits are reg 0x15
  mask 0xC0 (audited, `acpm.c:615-648`) and every mode is one Linux uses [src]. Still, the "leave it on" decision has to be made on a verified state.
- Fix (shim.c, inside the block):
  ```c
  int ho = gpu_rail_enable();
  con_hex("GPU handover rail (0=on)", (uint32_t)ho);
  if (ho == RAIL_UNVERIFIED || ho == RAIL_MISMATCH)
  	gpu_rail_restore(ho, RAIL_POWER_NONE);	/* only a verified "on" is handed to XNU */
  pstore_save();
  ```
  Then change the audit so that the hand-over block may contain exactly one `gpu_rail_restore`, under `if(ho==RAIL_UNVERIFIED||ho==RAIL_MISMATCH)`,
  and the global count becomes 2.

### M2 (MED): the XNU gate is weaker than the shim gate (no CMU mux check), and XNU cannot catch the fault
- Where: `xnu/patches/0102-sweet-gpu-probe-xnu.patch:23` ("exactly as the shim does") and `:82`.
- The shim gate is `gpu_gate()` (`shim/gpuprobe.h:87-101`): PMU top on, embedded on, **and the three CMU_G3D user muxes readable and selected**
  (`CMU 0x1C400000 + 0x600/0x610/0x620`, bit 4 `GPU_MUX_SELECT`, `gpuprobe.h:85`). On top of that, every shim read goes through `try_read32` (it
  catches synchronous aborts). Patch 0102 checks only the two PMU bit0 and then reads with plain loads. If the block is powered but its clock is not
  selected, a read can stall the interconnect. With `cpus=1` that stops the only CPU inside `sweet_ufs_init`, before the root mount; an SError
  panics XNU. Either way the human has to do a Power+VolDown reset. That is recoverable, not a brick, but it is the "bus hang cannot be caught" case
  the brief asks about. On this exact path the risk is small: the shim gate was open on every run so far [obs doc 27], and nothing between the shim
  and `sweet_ufs_init` writes CMU_G3D [src: no XNU patch maps 0x1C40xxxx]. The claim in the header is still false (rule 7).
- Fix: after the PMU check, map `0x1C400000` (16 KB) and require `(mux_top & mux_core & mux_stack & (1u << 4))` on words `+0x600/+0x610/+0x620`.
  Read CMU only when top is on, as the shim does. Print the three words. Then fix the comment.

### M3 (MED): the boot-arg is a physical base address that is not checked
- Where: patch 0102 `:72` and `:86`. Any nonzero `sweetgpu=` value is mapped (0x5000 bytes) and read at 21 offsets. That includes a typo such as
  `0x1C50000`, a value for another SoC, or `sweetgpu=1`. Reading unknown MMIO behind a passed PMU gate is exactly the uncatchable bus-hang case.
  Nothing else validates the value: `tools/build-xnu-image.sh` does not add it (see L4), so it is typed by hand.
- Fix: treat the boot-arg as an enable flag and compare: `if (mali != 0x1C500000ull) { printf("SWEET GPU: sweetgpu=%#llx is not the gs101 Mali base, not touched\n", mali); return; }`.
  The base comes from `gs101-gpu.dtsi:23` `reg = <0x0 0x1C500000 0x5000>` [src, `refs/kbase-bluejay`]. Better still, take it from an ADT node made by
  `mkadt.py` from soc.json (AGENTS: prefer the table). That also makes the patch a no-op on sweet (SM7150), where `0x17461E04` is not a PMU [guess
  about what lives there].

### L1 (LOW): "inside BOOT_XNU" is not enforced by the new audit; the self-test has no hand-over case
- Where: `tools/test-bluejay-boot.py:2063`. `code.find("#ifdef BOOT_XNU")` finds the **include guard at `shim.c:30`**, so the only position check
  left is "before the first `loader_boot(dtb)`". `he = code.find("#endif", h)` takes the first `#endif`, which a nested conditional defeats. The
  global counts at 2059 catch that, though.
- Mutations I ran against `check_shim_rail_sequence` [test, script `/tmp/gpuho-rev/mut.py`, sources unchanged]:
  - Caught: block moved after `loader_boot`; `GPU_HANDOVER` dropped from the guard; a restore added in the block; block deleted.
  - Not caught: block moved before `pstore_init` (outside `#ifdef BOOT_XNU` at :549); a third enable through a function pointer;
    `if (gpu_rail_enable()) for (;;);` (XNU never boots); the call wrapped in `#if 0` inside the block.
  - No hardware risk follows from these: build.sh:198-201 forbids `GPU_HANDOVER` without `BOOT_XNU`, and the only writer is still the audited one.
    The audit just checks less than its message says.
- Fix: anchor on the hand-over section itself. `bx = code.find('con_str("SECTION", "boot XNU");')`, `lb = code.find("loader_boot(dtb)", bx)`, and
  require `0 <= bx < h < lb`. Require the enable line (in normalised form) to be exactly
  `con_hex("GPUhandoverrail(0=on)",(uint32_t)gpu_rail_enable());`, or the M1 form. Reject `#if 0`, `for(;;)` and `while` inside `code[h:he]`.
- Self-test cases to add to `gpurailon_selftest()` (each must raise):
  1. Hand-over block moved after `con_str("BOOT XNU failed"`.
  2. Guard `#if defined(GPU_HANDOVER) && defined(GPU_RAIL_ON)` changed to `#if defined(GPU_RAIL_ON)`.
  3. `gpu_rail_restore(0, 0);` added inside the block (unconditional). After M1: an unconditional restore, or one under `ho != RAIL_REFUSED`, must
     still fail, and the M1 form must pass.
  4. Block deleted.
  5. Block moved before `\tpstore_init(dtb);`. This fails to raise today, so fix the anchor first.
  6. A third `gpu_rail_enable();` before `loader_boot`.
  7. `for (;;) ;` inside the block.

### L2 (LOW): if `loader_boot` fails, the probe fall-through misreports the rail
- Where: `shim.c:597-602` and then `:790-818`. When XNU does not start, the image (it also has `GPU_PROBE`/`GPU_RAILS`/`GPU_RAIL_ON`) runs the
  probe flow. Its `gpu_rail_enable()` sees mode 1 and refuses (`acpm.c:632`), and `gpu_rail_restore(RAIL_REFUSED)` prints `restore=none`. The rail
  stays on until reset, while the log suggests nothing was written. That is harmless: 0x40 is the stock state (see "checked" below).
- Fix: in the hand-over path, after `loader_boot` returns, `if (ho == RAIL_ON_OK) gpu_rail_restore(ho, RAIL_POWER_NONE);`. The rail is then left
  on only if XNU really got control.

### L3 (LOW): the comments describe a different experiment from the code
- Where: `shim.c:591` and `build.sh:50` say "XNU powers the cores itself". Patch 0102 is read-only and powers nothing, and doc 30 Q1 says "leaves
  rail+cores on". With this commit `SHADER_READY` from XNU will be 0 by design. Q1 can therefore only show that the rail, PMU domains and GPU
  registers survive the jump, not that powered cores do.
- Fix: reword the comments to "rail only; cores stay off; 0102 reads", and narrow Q1 in doc 30 to match.

### L4 (LOW): the flag plumbing can produce a run that tests nothing
- Where: `tools/build-xnu-image.sh:15,34`, `tools/xrun:26,55`. `--gpu-handover` builds the rail writer but adds no `sweetgpu=`. The default
  `--kernel` is `xnu-0085`, which has no 0102. xrun does not `mark` hand-over in the run dir. A run without `--args "sweetgpu=0x1C500000"` and a
  0102 kernel switches the rail on and reads nothing back: a wasted hardware cycle.
- Fix: with `--gpu-handover`, build-xnu-image.sh appends `sweetgpu=0x1C500000` unless `--args` already has `sweetgpu=` (the "first match wins"
  rule, line 27). xrun runs `$R mark "$D" gpu_handover=1` and suggests `--expect 'SWEET GPU: probe done'`.
- Bash correctness checked: `"${hoarg[@]}"` and `"${dvfsarg[@]}"` with empty arrays under `set -u` are fine on the host's bash 5.2.37 (>= 4.4).
  `[ "$handover" = 0 ] || hoarg=(...)` returns 0 in both branches, so it is `set -e` safe. `$handover` unquoted and empty expands to nothing.
  There is no word-splitting hazard, because the value is a fixed token.

### L5 (LOW): minor points in patch 0102
- `if (va == 0)` (`:49`) is dead code. `io_map` allocates with `KMA_NOFAIL` and panics instead of returning 0 (`xnu-pristine/osfmk/arm/io_map.c:120-130`) [src].
- The PMU 16 KB page (0x17460000) is mapped twice and nothing is unmapped: about 128 KB of permanent KVA (`KMA_PERMANENT`). This is harmless once
  per boot. Reuse `pmu_g` for the EMB word (same page).
- The probe runs synchronously in the root-mount path (`sweet_ufs_rootdev` -> `sweet_ufs_init`, patch 0028:572). A hang leaves only what the UDP
  log already sent. Put a short marker line before the first GPU read, which already exists (the `PMU_G3D_STAT=` line). [guess: the UDP log is
  asynchronous, so the last lines before a hang may be lost.]

## Checked and found correct
- **Writer unchanged**: the hand-over calls the same `gpu_rail_enable()` (preflight with layout/unit/timer/descriptor/empty-queue gates,
  `gpurailon.c` preflight; pre-read "exactly off"; OUT1 window; one UPDATE of 0x15 mask 0xC0 value 0x40). No new MMIO, no new ACPM framing.
  `gpurailon_selftest` passes 22/22 [test].
- **Leaving BUCK2S at 0x40 is the stock Android state** [src]. DT `BUCK2S regulator-always-on; regulator-initial-mode = <SEC_OPMODE_SUSPEND>`
  (`gs101-pmic.dtsi:766-779`). `s2mpg11_of_map_mode(SUSPEND)=1` (`s2mpg11-regulator.c:35-46`), and `s2m_enable` writes `opmode<<6` under 0xC0
  (:75-86).
- **The next Android boot copes with either state** [src]. On probe, `s2m_is_enabled` -> `need_sync=1` (:729-730), then always-on keeps it on,
  with the same value Linux writes itself. Whether S2MPG11 keeps the value over a PSCI/watchdog reset is still a [guess] (doc 27:144).
  Indirect hint: run g1rails2 found 0x38 (off) after ABL, although Android keeps this rail always on. So something on the reboot path clears it
  [obs + inference].
- **Persistence matters operationally**: if the value survives a warm reset, the next `--gpu-rail-on` or `--gpu-handover` run refuses ("not exactly
  off"). That is safe, but the driver should read the `RAIL on before B2S_CTRL=` line of the next run. That line answers the persistence question
  for free.
- **Thermal/voltage**: the voltage is never written. OUT1 was 600 mV [obs railon1/2]. Shader cores stay power-gated inside the Mali (no
  `SHADER_PWRON` in this path), so the only new load is leakage. The L2 rail BUCK8S and LDO1S were already on after ABL [obs g1rails2].
- **ACPM ordering (b)**: `ufs_cold_init` uses no ACPM. The touch rails share the PMIC channel: a request they leave queued makes the GPU preflight
  refuse with zero writes (`gpurailon.c` preflight, `acpm.c:404`). On success `xfer` leaves the reply queue drained and INTCR0 cleared
  (`acpm.c:457-463`), so XNU (no ACPM client; 0097 only reads the fvmap) inherits an empty channel. After a timeout the request stays queued by
  design. XNU registers no handler for the APM mailbox SPI [guess: no XNU patch enables it].
- **Register offsets in 0102** [src `refs/gpu-full/mali_kbase/hw_access/regmap/mali_kbase_regmap_jm_enums.h:35-112,426`,
  `mali_kbase_regmap_jm.c:1430,1500,1814`]: GPU_ID 0x0, L2_FEATURES 0x4, MEM_FEATURES 0x10, MMU_FEATURES 0x14, AS_PRESENT 0x18, JS_PRESENT 0x1C,
  GPU_IRQ_RAWSTAT 0x20, GPU_STATUS 0x34, GPU_FAULTSTATUS 0x3C, SHADER/TILER/L2_PRESENT 0x100/0x110/0x120, *_READY 0x140/0x150/0x160,
  *_PWRTRANS 0x200/0x210/0x220, COHERENCY_ENABLE 0x304, JOB_IRQ_RAWSTAT 0x1000, MMU IRQ_RAWSTAT 0x2000: all match.
  The block size is 0x5000 (`gs101-gpu.dtsi:23`).
- **No read side effects**:
  - Mali RAWSTAT registers are cleared by writing `*_IRQ_CLEAR`. kbase polls `GPU_IRQ_RAWSTAT` in loops (`backend/gpu/mali_kbase_pm_driver.c:508`,
    `device/mali_kbase_device_hw.c:74`), so a read is non-destructive.
  - GPU_FAULTSTATUS is RO (enums.h:49).
  - The S2MPU registers are CTRL0 0x0 and FAULT_STATUS 0x2000 (`refs/s2mpu/s2mpu-regs.h:3,22`). The fault is cleared by a write to
    INTERRUPT_CLEAR 0x2C (`s2mpu-lib.c:196,212`), not by the read.
  - Bases 0x20080000..0x200B0000 match `gs101-b0.dts:58-69`.
  - The S2MPU reads are behind both PMU bits, stricter than the shim's top-only gate (`gpus2mpu.c:98`).
  - Nothing reads 0x1000 (ALL_INVALIDATION) or any trigger register.
- **32-bit reads of 64-bit registers**: kbase itself reads 64-bit registers as two 32-bit reads, LO first
  (`common/include/linux/mali_hw_access.h:40`), so a LO-only read is what the driver does. All values of interest fit in 32 bits (SHADER 0x779FFF).
- **Mapping**: `ml_io_map` -> `io_map(..., VM_WIMG_IO)` maps Device memory (`osfmk/arm64/machine_routines.c:1540-1545`). Mapping without access
  has no side effect. Accesses are aligned 32-bit `volatile` loads. The 16 KB rounding maps but never reads past 0x5000 (GPU) or 0x4000 (S2MPU).
- **Boot-arg parse**: `PE_parse_boot_argn(..., sizeof(uint64_t))` stores 64 bits (`argnumcpy` case 8, `pexpert/gen/bootargs.c:381-382`) and parses
  `0x` hex. mkpayload refuses boot-args over 607 bytes instead of truncating (`tools/mkpayload.py:143-147`).
- **Call context**: `sweet_ufs_init` is reached from `sweet_ufs_rootdev` (patch 0028) after VM init, so `ml_io_map` uses the kmem path. The init
  runs once (`started`) and only on the UFS success path.
- `tools/stack-apply --only-new 0102`: `RESULT: PASS ... applies cleanly (whole stack: 103/103 patches + 3 stagers)` [test]. I did **not** compile
  0102 (no CI or local build run); the CI build is still needed.
- build.sh: `--gpu-handover` without `--boot-xnu` exits 2 (:198-201). The image carries `acpm_s11_rail_set` only with `gpu_rail_on=1` (:467-471).

## Verdict
**SAFE TO RUN ONCE ON HARDWARE**: no blockers. Nothing here writes anything but the already-audited BUCK2S mode field, and leaving it at 0x40 is
the stock Android state. The worst outcome found is a boot hang or panic needing a button reset (M2/M3), not damage.

Conditions for that one run:
1. Use a CI kernel that contains 0102, and pass exactly `sweetgpu=0x1C500000` (or apply the L4 fix).
2. Do not combine with `--dvfs` (M1 timeout path).
3. Afterwards, read `GPU handover rail (0=on)` in pstore. If it is not 0, treat the XNU result as invalid.

Fix M1-M3 and the L1 self-test cases before the hand-over goes beyond this one diagnostic run.

## Driver response (2026-10-23)
Fixed: M1 (UNVERIFIED/MISMATCH restored, shim.c), M2 (CMU_G3D mux gate in 0102), M3 (only 0x1C500000 accepted), L1 (audit anchored to 'boot XNU'..loader_boot, 4 new mutants, selftest 26/26), L2 (restore if loader_boot returns), L4 (`--gpu-handover` in build-xnu-image.sh adds `sweetgpu=0x1C500000`; `sweetgpujob=1` only via --args). L3: patch 0103 now powers the cores and runs a job when `sweetgpujob=1`. Gate PASS 2afc76e+dirty; kernel sha256 f0990090... Not run on hardware. Patch 0103 itself (the writer) has NOT had an Opus review yet.
