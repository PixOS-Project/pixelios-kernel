# Automation scripts (xrun, ci-kernel, stack-apply)

Three scripts replace the by-hand test loop. Each prints ONE report at the end (about 40 lines, no progress spam).
Line 1 is always `RESULT: <PASS|FAIL|PARTIAL|ERROR> <one sentence>`; read that first. Progress goes to a log file whose
path the report prints. Exit codes: **0** PASS, **1** FAIL or PARTIAL, **2** ERROR (the tool could not do its job:
no device, no network, bad arguments). `tools/pix xrun|ci|stack` are wrappers.

Evidence tags: everything in these reports is *observed* by the script on this run (camera, USB, log text, CI);
what a result *means* for the port is still the driver's judgement.

## tools/xrun: one hardware cycle

```
tools/xrun --kernel K [--adt A | --board bluejay] [--cpus N] [--qr S|off] [--restart S] [--args "boot-args"] [--rw] [--gui] [--usbnet [--usbdma]]
           [--expect REGEX]... [--expect-absent REGEX]... [--duration S] [--label NAME] [--no-flash-check]
           [--early] [--stop-on REGEX]... [--hard-timeout S] [--reader "qr-log.py options"]
tools/xrun --dry-run --from-dir logs/runs/<dir> [--stop-on REGEX]...   # rebuild the report of an old run, no phone (copy the dir first: report.txt/merged.log are rewritten)
```

Early stop (*read from source*, not yet run on hardware): `--stop-on REGEX` (repeatable; `--early` = all `--expect` patterns) ends the capture as
soon as every pattern is in the merged QR/usbnet log (checked every 10 s), instead of waiting for the self-reset. Then, if the phone is on USB (18d1)
xrun runs `to-fastboot.sh 60`; if not, the report gets ONE line `PHONE: still running; self-reset due in ~Ns / Power+VolDown hold`. RESULT is PASS when the
expects matched, with `EARLY: stopped at +Ns (full run would have waited Ms)`. A dry-run replay with `--stop-on` truncates the log at the moment the last
pattern first appeared and prints `EARLY: NOT reached ...` when it never would have. `--hard-timeout S` caps the whole run from xrun start (default: none, `--duration` still
applies); a timeout FAIL says `HARD TIMEOUT` and carries `PHONE: usb ids [...] adb [...] fastboot product [...]` from a bounded probe (lsusb 5 s, adb 8 s, fastboot 8 s).

Cycle: `to-fastboot.sh` (ERROR `needs Power+VolDown hold` if the phone is not reachable, ERROR if it is not bluejay) ->
`mkadt.py --board` -> `build-xnu-image.sh` -> camera/QR capture for the whole run -> `fboot.sh --expect xnu` -> waits on
its own until the phone re-appears on USB (vendor 18d1; the self-reset worked) or `--duration` (default restart+120 s)
runs out -> report. `--no-flash-check` skips the to-fastboot step (phone already in fastboot).
`--usbnet`: boot-args `sweetusb=2` (`--usbdma` adds `sweetusbdma=1`), xrun starts `tools/usbnet-log.py` (UDP 5140, no root) and prints the one sudo line
(`tools/sweet-usbnet.sh --stage`) the user runs once the phone enumerates; the report merges `usbnet.log` with the QR log (docs/research/09).
Everything lands in `logs/runs/<UTC>-<label>/` (gitignored): `report.txt`, `report.json`, `merged.log`, `snaps/` (raw QR
snapshots), `frames/` (every 4th camera frame + the last 20), `run.json`, `A.adt`, `image.meta.json`, `xrun.log` (progress).

Sections, fixed order:

| Section | Read it as |
|---|---|
| `RESULT` | PASS = every `--expect` found, every `--expect-absent` absent, no panic marker, reset observed (if `--restart`). FAIL = one of those broke, with a log that starts at boot and has no gaps. PARTIAL = an `--expect` is missing but the log is incomplete (the line may be outside the snapshots) or there is no QR log at all |
| `RUN` | kernel sha256, git head, boot-args, cpus/qr/restart; boot -> first QR snapshot; `reset observed at +Ns` or `NOT observed`; **log coverage** (snapshots, gaps, first/last line) |
| `EXPECT` | per regex: FOUND with the first matching line and its time (the snapshot that first contained it, seconds after the boot command), or MISSING |
| `MARKERS` | built-in counters (restart armed, UFS errors, display on, panic, lockscreen, SpringBoard/backboardd lines, check-in timeouts), FB swap count, `SWEET TOUCH/KEYS` lines (max 12), `SWEET CPU` lines |
| `CRASHES` | per process: count, signal, first pc, reason (from `CRASH <proc>[pid] sig N`) |
| `SCREEN` | mean camera luma per frame as segments (`bright 0-95 s, black from 97 s`) and `black-at`. No ML: mean luma < 25 = black |
| `DIFF` | markers/crashes that appeared or disappeared since the previous run with the same `--label` |

Limits you must know:
- **QL2** (kernel default, `sweetqr_fmt=2`, docs/research/24): the whole log from its first byte, compressed, loss-tolerant; the reader keeps
  `snaps/qr2-<sid>.txt` + `.json` (the log so far, `[...gap...]` where parts have not arrived yet). The report's "log coverage" line says
  `QL2 1 session(s): N of M reduced bytes recovered, blocks, frames, gaps`; with gaps an `--expect` that is missing is PARTIAL, not FAIL (the line may be in a gap). Wait longer
  (`--duration`) or use `--stop-on` to let more repair frames arrive. Camera/decoder knobs go in `--reader "--workers 3 --tiles 2 --average 3"`, phone-side
  knobs in `--args "sweetqr_ver=12 sweetqr_ms=500 ..."` (docs/QR_LOG.md). QL1 (`--args sweetqr_fmt=1`): only the **tail** (last 12 kB, digit-only-different
  repeats dropped), one snapshot per ~48 s; early/mid-boot lines are lost there.
  A run that must see lines while the phone is still alive has to start the QR takeover before that moment (`--qr SECONDS` counts from the root
  mount; QL2 reads the log from the start in the background, so a late takeover does not lose early lines). With `--qr off` there is no log: only reset and screen
  evidence, so `--expect` gives PARTIAL. Crash counts are lower bounds when the log has gaps.
- QR takes over the screen, so SCREEN `black`/`bright` during a QR run says little about the GUI.
- Times are relative to the `fastboot boot` command; snapshot time = when the camera finished decoding it (a few seconds
  after the phone drew it).

## tools/ci-kernel: the kernel for a commit

```
tools/ci-kernel [--commit SHA|HEAD] [--dispatch] [--expect-string STR]... [--out DIR] [--wait MIN]
```
Finds the `xnu.yml` run for the commit (waits up to 25 min), downloads `xnu-kernel` to `~/pixelios-artifacts/xnu-<short>/`
(reuses an intact earlier download), checks `SHA256SUMS`, runs `strings -a` for each `--expect-string`, prunes
`xnu-<7 hex>` dirs beyond the 3 newest (not with `--out`; dirs named `xnu-0089` are never touched). A failed CI run is
FAIL plus the last 25 lines of the failing step. Report: RESULT, RUN (id, url), KERNEL (path, sha, size), one line per string, PRUNED.

## tools/stack-apply: do the patches apply?

```
tools/stack-apply [--only-new PATCH] [--keep]
```
Applies the three stagers and every `xnu/patches/*.patch` in order on a pristine `xnu-8796.141.3` (shallow clone cached in
`~/pixelios-artifacts/xnu-pristine`, scratch copy under `~/pixelios-artifacts/scratch/`, deleted afterwards). Takes about 10 s plus the
stager downloads. FAIL names the first failing patch and prints 10 lines of the `git apply` error. `--only-new 0088`
still applies everything but states the verdict for that patch. It does not build; CI does.

## tools/phone-state: what state is the Pixel in?

```
tools/phone-state [--expect android|recovery|fastboot|none] [--wait S]   (also: tools/pix phone)
tools/phone-state --self-test     # stub lsusb/adb/fastboot on a fake PATH, incl. a hanging adb; run by check-all.sh
```
Every probe runs under `timeout` (8 s max; a bare `fastboot getvar` against a phone in recovery hangs 120 s). Order: `lsusb` for
18d1; `adb devices` + `getprop ro.product.device`/`sys.boot_completed`; `fastboot devices` + `getvar product` only when the usb id
is 18d1:d001 or 18d1:4ee0 (guess: other tools only assume vendor 18d1). Any non-bluejay device is `state=other`
(`OTHER-DEVICE (not touched)`). Last line: `RESULT: PASS|FAIL|ERROR state=fastboot|recovery|android|booting|absent|other product=bluejay|unknown`,
exit 0/1/2; ERROR = a probe timed out. Without `--expect`, PASS means "bluejay present". `--wait S` re-probes every 2 s (cap 300).
Serials are never printed. `to-fastboot.sh`, `fboot.sh` and `xrun` do not use it yet (own refusal/loop logic; swapping is not trivial).
Verified with stubs only (self-test), not on hardware.
## tools/tool-proposals: the proposal register

```
tools/tool-proposals list [--index]            # id, tool, status; --index = the table in docs/TOOL-PROPOSALS.md
tools/tool-proposals check NAME_OR_KEYWORD     # also: tools/pix proposals ...
tools/tool-proposals new NAME [--extends X] [--related TP-NNN] [--force]
tools/tool-proposals validate
```
`check` searches `tools/` file names, `tools/pix` subcommands, the headings of this file and the `tool`/`usage` fields of every proposal; one line
per hit (`EXISTS tools/x`, `PROPOSED TP-NNN (status)`), last line `RESULT: EXISTS|ALREADY-PROPOSED|NONE ...`, exit 0 NONE / 1 hit / 2 error.
`new` refuses on a hit unless `--force`, writes the next free `docs/tool-proposals/TP-NNN-slug.md` from the skill template and prints the
path; it does not edit the index, and the placeholders make `validate` fail until they are filled. `validate` checks fields, status values,
ids (unique, contiguous, match the file name), `extends`/`related` targets, the five sections, and that the README index equals
`list --index` (an `(amended)` suffix on the status is generated from a non-empty `## amendments`). Last line `RESULT: PASS|FAIL ...`, up to 10
problems, exit 0/1/2. `tools/validate-repo.py` runs it, so the gate fails on a broken register. Unit-tested (`tools/test_tool_proposals.py`).

## tools/hang-recover: wait for the phone after a reboot, bounded (TP-006)

```
tools/hang-recover [--wait S] [--expect fastboot|recovery]   (default 90 s, cap 300; also: tools/pix recover)
tools/hang-recover --self-test    # stub lsusb/adb/fastboot; run by check-all.sh (hang-recover step)
```
*Observed*: after a shim probe or an XNU run the phone leaves USB for seconds to a minute while it reboots, then shows up as
recovery; an early "hang" verdict was wrong. So: loop until the deadline, `tools/phone-state` every 3 s (never a bare adb/fastboot),
one line per state change (`+12s recovery`). Seeing recovery/android with `--expect fastboot` runs `tools/to-fastboot.sh` once
(bounded 60 s). Last line `RESULT: PASS state=fastboot after Ns` (exit 0), or `RESULT: FAIL still absent after Ns` (exit 1) followed by
"Hold Power+VolDown ~10 s until the bootloader screen / fastboot shows, then rerun tools/hang-recover" and "if the screen shows a
bootloader menu but USB is absent, replug the cable". A non-bluejay device = exit 2, not touched. Software only: the relay/hardware
reset half of TP-006 is not built (open for the user's decision). Verified with stubs only (self-test), not on hardware.

## tools/check-all.sh: the quality gate

`tools/check-all.sh` (full) is what must print `GATE PASS <hash>` before a push. Output is one line; `shim/out/gate.log` has the
full log plus a per-step timing table. Two lanes run in parallel (cap of 2 jobs): lane A = the seven bluejay shim builds (they share
`shim/out`, so they stay serial) + host-tests + boot-safety, lane B = unit, rootless, validate. The log is merged A then B, so it is
deterministic. Measured on the dev machine (*observed*, 2026-10-22, 01a978f): 50 s serial -> 37 s with lanes; the builds
(6 x ~6 s) dominate.

`tools/check-all.sh --quick` is for iterating: it diffs the tree against the commit in `shim/out/gate.stamp` (written only by a
full PASS on a clean tree) and runs only the needed steps, selected by `tools/gate_select.py` (tested by `tools/test_gate_select.py`):

| Changed paths | Steps |
|---|---|
| `shim/`, `soc/`, `device/`, `tools/imgmeta*`, `tools/mk*`, `tools/test-bluejay-boot.py`, `tools/check-all.sh`, xnu patches 0087/0088 (the host tests extract headers from them) | 6 builds, host-tests, boot-safety |
| `tools/`, `soc/`, `device/` | unit, phone-state, hang-recover, rootless |
| `tools/`, `soc/`, `device/` | unit, phone-state, probe-run, rootless |
| anything (docs, other `xnu/` patches) | validate (patch numbering/header format, script syntax), always |

No stamp, or a stamp that is not an ancestor of HEAD: the full gate runs. A quick PASS line says `quick: ran N of 17 steps`;
it is not a substitute for the full gate before a push.

## shim --gpu-power: Mali-G78 power on/off cycle (G2 stage 1, opt-in)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-power     # --gpu-power implies --gpu-probe
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-power" --expect "GPU POWER up=" --label g2   # only with the user's go-ahead (writes hardware)
```
Source `shim/gpupower.c/.h`, host test `shim/gpupower_host_test.c` (run by `build.sh`), constants from `soc.json` key `gpu` (`mali`, `id_expect`, `shader_present`,
`tiler_present`, `l2_present`, pinned by `tools/boards.py _GPU_PINS`). `gpupower.o` is compiled and audited on every build (source audit with 26 mutation self-tests, object audit with 3, in
`tools/test-bluejay-boot.py`) and linked only with the flag; the default image is unchanged. The call runs after the read-only probe output (section `GPU`). *Read from source*: sequence = Google kbase JM
(`docs/research/27-gpu-g0-result.md` addendum has the file:line table and the steps deliberately omitted). *Not yet run on hardware.*
- **Refusal (zero writes)** unless the gate is open, the timer works, `GPU_ID` and `SHADER/TILER/L2_PRESENT` equal the soc.json values, their HI words and all `*_READY` / `*_PWRTRANS` (LO and HI) are 0,
  `GPU_STATUS` = 0, `GPU_FAULTSTATUS` = 0 and `GPU_IRQ_RAWSTAT` = 0x100 exactly:
  `GPU POWER refused: <pd_g3d-off|pd_embedded_g3d-off|cmu-mux-off|pmu-unreadable|cmu-unreadable|no-timer|id-unreadable|id-mismatch|present-unreadable|present-mismatch|present-hi-nonzero|core-state-unreadable|cores-not-off|gpu-state-unreadable|gpu-status-nonzero|gpu-fault|rawstat-unexpected>`.
- Read-only pre-state lines `GPU PRE_<STACK_READY|STACK_PWRTRANS|L2_CONFIG|COHERENCY_ENABLE|GPU_IRQ_MASK> pa=... v=...`.
- ON: `TILER_PWRON` (also powers the L2), wait tiler+L2 ready, `SHADER_PWRON`, wait shaders ready. Then `GPU <NAME> pa=... v=...` for `SHADER/TILER/L2_READY`, the three
  `*_PWRTRANS`, `GPU_IRQ_RAWSTAT`, `GPU_FAULTSTATUS`, `GPU_STATUS`. OFF, always (also after an ON failure), per block as kbase: wait PWRTRANS 0, **write only if READY != 0**, refuse a partial READY, wait;
  shaders (`SHADER_PWROFF`) then L2 (`L2_PWROFF`, takes the tiler too); the same dump with the prefix `OFF_`. Each wait is bounded by 100000 reads and 1 s of CNTPCT; no write is retried.
  If the shaders do not power off, `L2_PWROFF` is not written.
- Summary: `GPU POWER up=<what> down=<what> ticks=<ON phase>/<OFF phase> freq=<Hz>` (hex, CNTPCT ticks). `up`: `ok|timeout:tiler|timeout:l2|timeout:shader|fault:tiler|fault:shader|refused:tiler|refused:shader`;
  `down`: `ok|timeout:shader|timeout:l2|timeout:tiler|partial:shader|partial:l2|fault:shader|fault:l2|refused:shader|refused:l2` (`fault:` = the write faulted on the bus, `refused:` = our allowlist refused it,
  `partial:` = READY is not the full present mask, nothing written). Success means `up=ok down=ok` and every `OFF_*_READY` = 00000000.
- Writes (the whole allowlist, LO words only): `0x1C500180` SHADER_PWRON, `0x1C500190` TILER_PWRON, `0x1C5001C0` SHADER_PWROFF, `0x1C5001E0` L2_PWROFF, each only with its present mask.
  Never: `L2_PWRON`, `TILER_PWROFF`, `STACK_*`, `GPU_COMMAND`, reset, MMU/job registers, IRQ masks, PMU/CMU/SMC/ACPM.

### `--gpu-power=stack`: corestack variant (opt-in experiment, after the first run)

`./build.sh ... --gpu-probe --gpu-power=stack` (implies `--gpu-power`; plain `--gpu-power` is unchanged). Reason: in the first hardware run `SHADER_PWRON` left `SHADER_PWRTRANS` stuck
(docs/research/27 last section); kbase's `corestack_driver_control=true` path powers the core stacks first. *Read from source*: pm_driver.c:2593-2597 (`stacks_avail` = present),
2646-2649 (`STACK_PWRON`), 2656-2657 (wait `!stacks_trans && stacks_ready == avail`), 2658-2660 (then `SHADER_PWRON`); off 2846-2847, 2855-2859 (`STACK_PWROFF` after the shaders are off), 2866 (wait); registers
enums.h:727-731, selection pm_driver.c:408-424. Whether the Pixel build enables it is unknown (*guess*); this variant is the test.
- Object `shim/out/gpupower_stack.o` (`-DGPU_POWER_STACK`) is built and audited on every build; only this flag links it. Both objects have their own exact allowlist audit; the plain one must not contain the stack registers.
- Extra precheck (refusals, zero writes): `stack-present-mismatch` (`STACK_PRESENT` LO != soc.json `stack_present` 0x7F or HI != 0), `stack-not-off` (`STACK_READY` != 0), `stack-unreadable`.
- Order: `TILER_PWRON` -> wait tiler+L2 -> **`STACK_PWRON` (0x1C500E20) = 0x7F** -> wait `STACK_READY` (0xE10) == 0x7F and `STACK_PWRTRANS` (0xE40) == 0 -> `SHADER_PWRON` -> wait. OFF: `SHADER_PWROFF` -> **`STACK_PWROFF` (0x1C500E30) = 0x7F** (only if `STACK_READY` != 0) -> drain tiler PWRTRANS -> `L2_PWROFF`. A failure at any OFF step stops the later ones (no stack-off under ready shaders, no L2-off under a ready stack).
- New result words: `up=timeout:stack|fault:stack|refused:stack`, `down=timeout:stack|partial:stack|fault:stack|refused:stack`. The dump (`dump_all`, both variants) now includes `STACK_READY` / `STACK_PWRTRANS` (also `OFF_`).
- Allowlist of this variant: the four of plain `--gpu-power` plus exactly `0x1C500E20` and `0x1C500E30`, each only with 0x7F (6 pairs).

## shim --gpu-clk: ask the ACPM for the GPU clocks (G3DL2 / G3D), opt-in, before the power cycle (BUILT, UNRUN; hypothesis refuted by the PLL_CON3 decode, docs/research/27)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-clk [--gpu-power]     # implies --gpu-probe; works without --gpu-power
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-clk --gpu-power" --expect "GPU CLK result" --label g2clk    # only with the user's go-ahead (writes hardware)
```
Status: **built, unrun, premise refuted.** It was written because run `g0b` seemed to show both GPU PLLs off; the correct decode of `PLL_CON3` = 0xA0620402 (ENABLE + LOCK set, 150.5 MHz = the 151000 kHz init level, docs/research/27) says they are already running, so a clock request is not expected to change anything. Kept as an opt-in experiment; open review findings (rate 302000/151000 as Linux, a meaningful result check, PMU gate, no power cycle after a failed request) are listed in doc 27. Original idea: the ACPM starts the PLLs when the kernel calls `exynos_acpm_set_rate(G3DL2/G3D)` (*guess*). Source `shim/gpuclk.c/.h`, host test `shim/gpuclk_host_test.c` (reuses the fake APM of `dvfs_host_test.c`; two more
builds force one ACPM gate to 0), constants from soc.json (`acpm.dvfs.domains` G3D idx 5 / G3DL2 idx 6 with the fvmap rows of run g0b and `ceiling_khz` = 151000 = the firmware's own init level; `gpu.clk_khz` = 151000),
checked by `tools/boards.py` (`tools/test_boards_gpuclk.py`). The CPU `--dvfs` requests and the touch probe still see only the three CPU clusters (`ACPM_DVFS_N` = 3; the acpm.c tables hold 5).
- **No new writer.** `gpuclk.c` has no MMIO access of its own (audited: no `try_write32`/`try_read32` reference, stack-only stores, 14 source mutations + 3 object mutations). Its only requests go through `acpm_dvfs_reassert()` in
  `acpm.c` (compiled only in `--gpu-clk` images), a thin wrapper of the existing DVFS path (`dvfs_set`, the same `xfer()` and writer allowlist as the CPU client): it refuses every domain but 5/6 and every rate but a table row at or
  below the ceiling, i.e. only 151000 kHz, before anything is sent. One difference from `acpm_dvfs_set`: it also sends the request when GET already reports that rate (Linux re-sends the init rate at every probe; the whole point).
- **Gates.** All existing ones apply and are checked first, with the reason printed and zero writes: DVFS channel configured, `acpm.layout_verified`, `acpm.elem_unit_verified` (both **1** in soc.json since 2026-10-22: descriptor fields and 16-byte slots
  observed on the PMIC channel, run `acpm3`; the DVFS channel 1 GET was observed on 3/3 CPU clusters in run `dvfsr2`), a validating descriptor, empty queues, a working timer.
  The unverified fact that remains: a FREQ_REQ for the GPU domains (idx 5/6) has never run, nor any GET for them (a CPU FREQ_REQ through this client did run: `dvfs1..4`, effect observed, reply words not logged) and nothing is known about the reply for the GPU domains
  (Linux sets `response=true` for them, acpm_dvfs.c:97-135; our client waits for the reply for every request anyway).
- **Order** (kbase `gpu_dvfs_set_initial_level`, pixel_gpu_dvfs.c:859-896; domains from gs101-gpu.dtsi:46-47): GET G3DL2, GET G3D, then FREQ_REQ G3DL2 = 151000, FREQ_REQ G3D = 151000 (a failure of the first stops the second, no retry), ~10 ms, GET both back,
  `GPU CLK after: ...` and the CMU_G3D dump again (the `--gpu-probe` dump is the "before").
- **Output** (`GPU CLK ...`): `cfg dvfs_ch=.. layout_verified=.. elem_unit_verified=..`, `target kHz=151000 g3dl2_idx=6 g3d_idx=5`, `desc/queues` lines, per transaction `<get|pre-read|request> <G3DL2|G3D> req=.. / rep=.. / seq= err= ret= val= writes= pub=`,
  `G3DL2 kHz=<n>`, `readback g3dl2=<n> g3d=<n> asked=151000`, `result: ok|MISMATCH|REFUSED-or-FAILED`, refusals as `refused: <reason>` (+ `flag: soc.json acpm.<flag> = 0` for a gate). A request that was published and failed prints `STATE` and `the request stays queued`.
- **What can be written** (exactly): a run is 8 ACPM transactions on channel 1 (2 GETs, per domain a pre-read GET and a FREQ_REQ, 2 read-back GETs = 6 GETs + 2 REQs); each one makes the same 8 writes of acpm.c's audited writer (4 request-slot words, the request-front pointer, the doorbell bit, and after the reply the response rear pointer and INTCR0); whatever the firmware then does to its PLLs/PMIC is outside our code. Nothing else.

## shim --gpu-rails: READ-ONLY S2MPG11 registers of the GPU rails (opt-in, implies --gpu-probe)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-rails
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-rails" --expect "GPU RAIL result" --label g0rails
```
Why (*guess*, doc 27): the tiler/L2 (rail BUCK8S = S8S_VDD_G3D_L2) came up but the shaders (rail BUCK2S = S2S_VDD_G3D) never leave PWRTRANS; the DT initial mode of all three GPU rails is SEC_OPMODE_SUSPEND ("ON in standby"). This reads what the PMIC says.
Source `shim/gpurails.c/.h`, host test `shim/gpurails_host_test.c` (fake APM of `acpm_host_test.c`, plus gate-0 and null-config builds), config `soc.json` `acpm.s2mpg11` (pins in `tools/boards.py _ACPM_S11_PINS/_ACPM_S11_REGS`, tests `tools/test_boards_gpuclk.py`).
- **Derived the same way as S2MPG10's speedy_ch/pmic_addr** (doc 23 section 1): channel = 1 (every `s2mpg11_*_reg` passes `u8 channel = 1`, s2mpg11-core.c:55,73,91,109,127; DT `acpm-speedy-channel = <1>`, gs101-pmic.dtsi:710-718), TYPE = pmic_addr = 1 (`I2C_ADDR_PMIC`, s2mpg11-core.c:29), IPC channel unchanged (2). Request word 0 = `reg | 1 << 8 | 1 << 12 | seq << 16`, word 1 = 0 (FUNC_READ).
  The registers (s2mpg11-register.h:26-100, regulator descriptors s2mpg11-regulator.c:226-275) are listed with their cites in the soc.json `s2mpg11` note. Any null value in the block => `GPU RAIL refused: ... null/unknown value, no ACPM request sent` and zero ACPM traffic.
- **Reuse, no fork**: `acpm_s11_read()` in acpm.c is a 15-line twin of the S2MPG10 `read1` and calls the same `xfer()`/writer allowlist; `gpurails.c` has no MMIO access of its own (audited: no `try_write32`/`try_read32`, stack-only stores; 13 source + 3 object mutations).
- **Gates / refusals (zero ACPM traffic)**: `acpm.layout_verified`, `acpm.elem_unit_verified` (both 1), a working timer, a validating PMIC-channel descriptor, empty queues, a fully known s2mpg11 block.
- **Output**: `GPU RAIL cfg speedy ch=1 pmic_addr=1`, then per register `GPU RAIL <STATUS1|B2S_CTRL|B2S_OUT1|B2S_OUT2|B8S_CTRL|B8S_OUT1|B8S_OUT2|L1S_CTRL1|L1S_CTRL2|LDO_CTRL1> reg=0x.. v=0x..`, then `GPU RAIL decode <rail> mode=<0-3>(name) enabled=<0|1> uV=<n>` for BUCK2S/BUCK8S/LDO1S and `decode note:` (mode field and uV formulas *read from source*; mode 1 = SUSPEND is the DT default of the CPU rails too, so it is not evidence of standby; that OUT1 is the live output is a *guess*).
- **Exactly the ACPM traffic**: one descriptor read (reads only), then at most **ten** FUNC_READ transactions on IPC channel 2 (S2MPG11 registers 0x0C, 0x15, 0x16, 0x17, 0x25, 0x26, 0x27, 0x41, 0x42, 0x52, in that order), each making the audited IPC writes of acpm.c (4 request-slot words, the request-front pointer, the AP->APM doorbell bit, the reply-rear pointer, INTCR0). The first failure stops the rest, no retry, nothing rolled back. No PMIC register is written (no FUNC_WRITE/UPDATE is expressible in this path).

## shim --gpu-rail-on: switch the shader rail on for the power cycle, and back (opt-in WRITER, implies --gpu-rails)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-rails --gpu-rail-on --gpu-power
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-rails --gpu-rail-on --gpu-power" --expect "GPU RAILON" --label g2rail    # only with the user's go-ahead and after the Opus review
```
Why: run `g1rails2` (doc 27, *observed*) found S2MPG11 BUCK2S (S2S_VDD_G3D, the shader rail) OFF (CTRL 0x15 = 0x38, mode 0, OUT1 0x40 = 600000 uV) while BUCK8S (L2) and LDO1S were on: the shaders cannot power without their rail.
Source `shim/gpurailon.c/.h` (orchestration, no MMIO), the writer `acpm_s11_rail_set()` in `acpm.c` (a twin of `acpm_pmic_update`, same `xfer()` and IPC writer allowlist), host test `shim/gpurailon_host_test.c` (fake APM, gate-0 and null builds), config `soc.json acpm.s2mpg11.rail_on`
(pins `tools/boards.py _ACPM_S11_ON_PINS`, cites in the soc.json note, table of the write in doc 27).
- **Sequence** (shim.c, after `gpu_rails`, before the power cycle): (1) pre-read BUCK2S_CTRL and read OUT1; refuse with ZERO writes unless the mode field (bits 7:6) is exactly 0 and OUT1 decodes inside 550000..700000 uV (codes 56..80); (2) ONE FUNC_UPDATE of 0x15, mask 0xC0, value **0x40** (mode 1 = SUSPEND, what Linux writes for this rail, *read from source*: gs101-pmic.dtsi:766-779 initial mode + always-on, s2mpg11-regulator.c:35-91,718),
  never the voltage registers; (3) wait 8 x 130 us (the driver's `ENABLE_TIME_BUCK`), read CTRL, OUT1 and OUT2 back once, accept only if mode == 1 and OUT1 unchanged (`GPU RAIL on after ...`, `on result: rail is on, voltage unchanged`); (4) the power cycle runs only then (also `--gpu-clk` now skips it after a failed request);
  (5) ALWAYS afterwards: pre-read, ONE FUNC_UPDATE of 0x15, mask 0xC0, value 0x00 (only if the mode is not already 0), read back, `GPU RAILON enable=<ok|unverified|refused> readback=<ok|mismatch|n/a> restore=<ok|FAILED|none> power=<ran|skipped|not-requested>`; before the restore the shader state is logged (`GPU RESTORE_SHADER_READY` ...). A failed restore prints `GPU RAIL !!! RESTORE FAILED: ... may be LEFT ON` and `restore=FAILED`.
  After a refusal (nothing written) there is nothing to restore; after an enable that was published but not verified (error reply, mismatch) the power cycle is skipped and the restore still runs.
- **Gates (zero ACPM traffic, `GPU RAIL on refused: ...`)**: `acpm.layout_verified`, `acpm.elem_unit_verified` (both 1), a working timer, a validating PMIC-channel descriptor, empty queues, and a fully known `acpm.s2mpg11` + `rail_on` block (a null value refuses, `S11_ON_KNOWN` 0).
- **Exact writes of the option** (besides the IPC words every ACPM request makes, 8 per transaction): two PMIC register updates, listed with cites in doc 27. Transactions: enable = pre-read 0x15, read 0x16, update, read-back 0x15, 0x16 (5); restore = pre-read 0x15, update, read-back 0x15, 0x16 (4); one request per write, never retried.
- **Run order** (Opus review): (1) FIRST `--gpu-probe --gpu-rails --gpu-rail-on` ALONE (no `--gpu-power`): enable, read back, restore; expect `GPU RAILON enable=ok readback=ok restore=ok power=not-requested` and `on after ... mode=1`. (2) Only then add `--gpu-power`. Never combine with `--gpu-clk`.
  A rail left ON (hang mid-cycle, `restore=FAILED`) cannot be cleared by the shim: the next run's enable refuses (mode != 0) and does no restore; a SoC reset (PSCI) is not a PMIC reset, so S2MPG11 probably keeps the value (*guess*); since the value is mode 1 = 0x40 this equals the stock Android state (idle, gated cores: leakage only, *guess*).
- **Guess**: the soft-start margin (8x the driver's 130 us) is ours. B2S_CTRL bits 5:0 (0x38, vs 0x18 on BUCK8S) have no name in the header; the update preserves them, OUT2 is printed before/after.

## shim --gpu-s2mpu and --gpu-job: G2 stage 2 (address space 0 + the first job), design in docs/research/28-gpu-stage2-job.md

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-s2mpu          # READ-ONLY: the four G3D S2MPU blocks. Safe to run first, alone.
cd shim && ./build.sh --board bluejay --avb --pstore-fast --gpu-probe --gpu-job            # implies --gpu-rails --gpu-rail-on --gpu-power --gpu-s2mpu; a WRITER (needs the Opus review and the user's go-ahead)
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-s2mpu" --expect "GPU S2MPU3 vid1" --label g2s2mpu
tools/probe-run --build-args "--pstore-fast --gpu-probe --gpu-job" --expect "GPU JOB SUMMARY" --label g2job
```
- **`--gpu-s2mpu`** (`shim/gpus2mpu.c`, host test `gpus2mpu_host_test.c`, object audited like `gpuprobe.o`): prints `GPU S2MPU probe_pa=.. gb_index=..`, per instance `CTRL0/CTRL1/CFG/MPC_CTRL/PAGE_AWARE0/1/FAULT_STATUS`, per VID `FAULT_INFO/FAULT_PA_LOW`, `vidN rdwr=<mask> l2=<mask> nonzero= bad=` (64 granules of 1 GiB: which are open RD+WR, which use an L2 table) and the L1 entry of the granule of `probe_pa`.
  Only if `PMU_G3D_STAT` says `pd_g3d` is on; never writes. Config: soc.json `gpu.s2mpu_g3d0..3` (pinned in `tools/boards.py`).
- **`--gpu-job`** (`shim/gpujob.c`, hook in `gpupower.c`, host test `gpujob_host_test.c` with a fake Mali: AS0, page-table walker, WRITE_VALUE executor, fault / hang / S2MPU-block injections): inside the powered window it builds the page tables and one Valhall WRITE_VALUE job in 5 pages of the shim image (`gpujob_pages` in shim.c), programs AS0, submits on JS0, waits 100 ms
  (one `HARD_STOP_1` after a timeout), prints everything and always disables AS0 and clears the IRQ bits it caused; then the usual OFF sequence and rail restore. Output: `GPU JOB cfg`, the S2MPU dump, `GPU JOB <REG> pa= v=` lines, `GPU JOB target=<64-bit> header_exc=`, and
  `GPU JOB SUMMARY submit=<ok|no> wait=<done|failed|mmu-fault|timeout|n/a> hardstop=<yes|no> target=<MAGIC|unchanged|other> as0_off=<ok|FAILED>`; refusals `GPU JOB refused: <scratch-bad|no-timer|js0-absent|js0-no-set-value|shaders-not-ready|job-irq-pending|mmu-irq-pending|as0-active|js0-busy|gpu-fault>` with zero writes (`GPU JOB JS_PRESENT` and `JS0..2_FEATURES` are printed first, refused or not; slot 0 needs `JS_PRESENT` bit 0 and `JS0_FEATURES` bit 2 = SET_VALUE_JOB; panfrost uses slot 1 for non-fragment jobs, doc 28 section 4). Cleanup runs in kbase's kill order: hard stop (only if still ACTIVE), AS0 off, IRQ clear.
- **Exact writes**: the scratch window plus the register/value pairs of doc 28 section 7 (AS0 TRANSCFG/TRANSTAB/MEMATTR/COMMAND, JS0 HEAD/AFFINITY/CONFIG/COMMAND_NEXT, JS0 COMMAND = 7 once, JOB/MMU_IRQ_CLEAR = 0x00010001); no GPU_COMMAND, no soft reset, no S2MPU/PMU write. 21 w32 sites are pinned by the source audit (21 mutations); the object audit is the same as `gpupower.o`'s.
- Image size: `--gpu-job` adds 24 KiB of `.bss` (`gpujob_pages`). `build.sh` fails if the default image contains `gpu_job_run`.

## shim --wlan-probe: read-only Wi-Fi path probe (W1; review items D1-D5 fixed, docs/research/20 section 3)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --wlan-probe
tools/probe-run --build-args "--pstore-fast --wlan-probe" --expect "WLAN SUMMARY" --label w1       # read-only, safe to run; never run on hardware yet
```
Source `shim/wlanprobe.c/.h`, host test `shim/wlanprobe_host_test.c` (builds the real file against a fake register window + a faulting address), addresses `soc.json` key `wlan` (generated `WLAN_*` macros). Strictly read-only (object audit: stack-only stores, no `try_write32`).
- **Level 1** (`WLAN pmu+pins` section): `PMU_PCIE1_ISO` / `PMU_PCIE0_ISO` (PMU 0x17463EC4 / EC0, bit 0 = PHY not isolated), `PD_HSI0_CFG/STAT` (0x17462080/84, the HSI0 domain) and `PD_HSI2_A`, `+4` (0x17462180/84, the disabled `pd_hsi2` node, never a gate), the pin banks `GPP14/GPH2/GPA7 CON/DAT` and `WLAN_EN gpp14-2`, `PERST gph2-0`, `CLKREQ gph2-1`, `DEVWAKE gph2-5`, `HOSTWAKE gpa7-0` as `func=X lvl=Y`.
- **Level 2 is gated.** `WLAN CMU_HSI2_PCIE_MUX pa=14400620 v=..` is read only if `PMU_PCIE1_ISO` bit 0 is set; the verdict line is `WLAN l2 gate=open` or `WLAN l2 gated: <pcie1-phy-isolated|pmu-iso-unreadable|cmu-hsi2-pcie-mux-off|cmu-hsi2-unreadable>`, and then **nothing** of the PCIe wrapper is read.
  When open, the groups run in this order with a pstore checkpoint after each: `SYSREG+000..01C`, `ELBI+000..040, 054, 080, 2C8, 2E8, 3A4`, `DBI+000/004/008/00C`, then (last, the riskiest) the PMA lock words `PMA+3F0, E0C, DE8, 5FC, E18` and `WLAN PHY pll_lock= cdr_lock= oc_done= oc_after_rst=`.
- **Always**: `WLAN SUMMARY gate=<open|reason> en=<0|1|?> perst=<..> dbi_id=<8 hex|unreadable> link=<2 hex|?>` (`link` = `ELBI+2C8 & 0x3F`, `11` = L0). All register lines are `WLAN <name> pa=<8 hex> v=<8 hex>` or `... unreadable`.
- On a normal ABL boot the PCIe1 PHY is expected to be isolated (nobody has brought the link up), so the expected first result is `l2 gated: pcie1-phy-isolated` (*guess*); that is itself the baseline for the PCIe bring-up stage.

## shim --wlan-rails / --wlan-en: rail state (read-only) and the first WLAN_EN write (W2; docs/research/29)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --wlan-probe --wlan-rails          # READ-ONLY
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --wlan-probe --wlan-rails --wlan-en  # + the WRITER, only after the rails run and the review
tools/probe-run --build-args "--pstore-fast --wlan-rails" --expect "WLAN RAILS" --label w2r             # never run on hardware yet
```
`--wlan-rails` (`shim/wlanrails.c`, host test `wlanrails_host_test.c`) runs after the level-1 section and before the level-2 gate. Lines (`WLAN ...`):
- `CMU gate ufs_ver=XXXXXXXX open=<0|1>`: the CMU_HSI2 words are read only if the UFS HCI VER (UFS_BASE+0x08, observed 0x300) is readable and nonzero. Then `CMU PCIE_MUX|OSC|QCH_GPIO|QCH_PCIE_A|QCH_PCIE_B|QCH_UFS|QCH_SYSREG pa= v=`
  (offsets +0x620, +0x800, +0x7018, +0x7060, +0x7064, +0x7068, +0x707C of 0x14400000) and `CMU decode ...` lines tagged `(guess)` (bit 4 of the mux = selected, bit 28 of OSC = automatic gating, QCH equality with the UFS word; the QCH bit layout is UNKNOWN).
- `PMU TXCO pa=17463C10 v=` and `PMU decode txco bits 2,3,10 all set=<b>` (read from source: rc.c:80,2698).
- `RAIL L16M|L17M|L18M|L19M(PCIe0|PCIe1 PHY) reg=0x53..0x56 v=0x..` and `RAIL decode ... mode=<0 off|1 standby|2 PWREN_MIF|3 on-normal> enabled= sel= uV= dt_window=` (S2MPG10 over ACPM, `acpm_s10_read`; refuses with zero ACPM traffic like `--gpu-rails`).
- `RAILS l17=<mode>/<uV> l19=<mode>/<uV> cmu_mux=<b|?> txco=<b|?>`: the one summary line.

`--wlan-en` (`shim/wlanen.c`, host test `wlanen_host_test.c`, audits in `tools/test-bluejay-boot.py`): drives WLAN_EN gpp14-2 high for 200 ms and puts it back. Write set (all `w32`, read-modify-write of the masked bits only):

| # | Register | Mask | Value | Cite |
|---|---|---|---|---|
| 1 | GPP14.DAT `0x108401C4` | `0x4` (bit 2) | set | Linux `samsung_gpio_direction_output` = DAT first (`pinctrl-samsung.c:693-696`, `samsung_gpio_set_value` `:555-570`) |
| 2 | GPP14.CON `0x108401C0` | `0xF00` (field of pin 2) | 1 = OUTPUT (observed before: 0 = INPUT, CON `0x33`) | `samsung_gpio_set_direction` `:624-653`; `PIN_CON_FUNC_OUTPUT 1` `pinctrl-samsung.h:61-62` |
| 3 | GPP14.DAT | `0x4` | back to the saved bit (0) | drive low while still an output: `dhd_wlan_power(0)`, `dhd_custom_google.c:616` |
| 4 | GPP14.CON | `0xF00` | back to the saved field (0 = input) | `samsung_gpio_direction_input` `:656-676` |

Preconditions (otherwise ZERO writes, `enable=refused`): counter running; GPP14.CON/DAT and GPH2.CON/DAT readable; WLAN_EN CON field exactly 0 and DAT bit 2 = 0; PERST (gph2-0) DAT bit 0 = 0 and its CON field <= 1.
Lines: `WLAN EN before GPP14|GPH2 ...`, `EN after enable CON= DAT=`, (after 200 ms on the counter) `EN sample GPP14|GPH2 ...`, `EN restore CON= DAT=`, and the summary
`WLAN EN enable=<ok|refused|fault> sample=<en perst clkreq devwake hostwake: 0/1/?> restore=<ok|FAILED|n/a>`. Any write that faults, or a read-back that differs, is `fault` and skips the wait; the restore runs in every case that attempted a write.
A failed restore prints `!!! RESTORE FAILED ...` and `restore=FAILED`; (guess) the pin state is volatile and does not survive a reset. Restore order (DAT low, then input) deliberately differs from "reverse of the enable" so the module is driven off before the pin is released.
The writer touches no PERST, no PHY isolation, no SMC, no PMIC rail. Expected quiet result: sample `10100` (*guess*: nothing in the chain reacts without the PHY/PERST); that is not a failure (research 29 U3/U8).
Also printed read-only before and after: `EN before|after pads GPP14 PUD= DRV=` / `CONPDN= PUDPDN=` (GPP14 +0x08/+0x0C/+0x10/+0x14, `pinctrl-exynos-arm64.c:47-50`): what the pin floats to after the restore and the drive of the high level.
**Polarity: do not "fix" it.** The DT flag `0x1` of `wl_reg_on` / `pcie,wlan-gpio` is GPIO_ACTIVE_LOW, but rc.c (`:1989-1995`) and dhd (`dhd_custom_google.c:529-566,601`) use the legacy raw GPIO calls (`gpio_direction_output(.., 1)` = ON); *guess* (Linux gpiolib legacy API, not fetched): they ignore the flag, so raw HIGH = module on.

## shim --wlan-smc-read: two EL3 SMC READs of the PHY-isolation word (W2a; docs/research/29 section 12)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --wlan-probe --wlan-smc-read
tools/probe-run --build-args "--pstore-fast --wlan-smc-read" --expect "WLAN SMC summary" --label w2a      # never run on hardware yet
```
`shim/wlansmc.c` (host test `wlansmc_host_test.c` with a fake EL3). The only SMC of the object `wlansmc.o` (the rest of the image keeps shim.c's PSCI calls and `prepare_bluejay_cpu`'s option-1 WRITE of CPU_INFORM; review 2 F5): `smc #0`, **x0 = 0x82000504** (SMC_CMD_PRIV_REG, `exynos-smc.h:45`), **x1 = 0x17463EC4** then **0x17463EC0**,
**x2 = 0 (PRIV_REG_OPTION_READ**, `exynos-el3_mon.h:21`), **x3 = 0**; results a0..a3 printed. Lines (`WLAN ...`): `SMC read pa=17463EC4 ret=<int32 a0> a0=..`, `SMC read a1= a2= a3=`, `SMC cmp pa= mmio= same=<yes|no|error>`,
`SMC mmio after pa= v=` (`!!!` if a READ changed it), and `SMC summary same=<yes|no|error> ambiguous=<0|1>`. `error` = a0 negative as int32 (EL3 refusal -EINVAL = -22, mainline `exynos-pmu.c:65,82`; SMCCC unknown function -1,
`linux-arm-smccc.h:247-249`) or the MMIO read faulted. `ambiguous=1`: a0 == 0 and the MMIO word == 0 (observed 0): the reply cannot tell a value from a status 0 (*guess* that the first run is ambiguous). Both reads, no write, no loop; the
helper refuses (no SMC) any other address. Audits: exactly one `smc` instruction in the object, x0/x2/x3 constants and the address guard checked in the disassembly (`check_wlansmc_object_text`, 9 object + 13 source mutations).

## shim --wlan-iso: release the PCIe1 PHY isolation through EL3 and put it back (W2c; docs/research/29 section 12)

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --wlan-probe --wlan-iso      # implies --wlan-smc-read; WRITER via EL3, only after the Opus review
tools/probe-run --build-args "--pstore-fast --wlan-iso" --expect "WLAN ISO release=" --label w2c       # never run on hardware yet
```
Code: the `WLAN_ISO_RUN` block of `shim/wlansmc.c` (object `wlansmc_iso.o`), host test `wlaniso_host_test.c` (fake EL3 with RMW / full-overwrite / ignore / refuse / lie / late semantics). **SMCs of the object `wlansmc_iso.o`** (scope: that object only; shim.c's PSCI calls, the reboot-to-bootloader SYSIP_DAT0 WRITE and the CPU_INFORM clear remain in the image):
(1) `smc #0` x0 = `0x82000504`, x1 = `0x17463EC4` or `0x17463EC0`, x2 = 0 (READ), x3 = 0 (the W2a reads, plus one READ of `0x17463EC4` as a precondition); (2) ONE RMW set: x0 = `0x82000504`, x1 = `0x17463EC4`, x2 = 2, x3 = 1 (mask), x4 = 1 (value);
(3) exactly ONE RMW clear after any sent set: the same with x4 = 0 (x5..x7 = 0 in both RMW calls, as Linux). Nothing else in `wlansmc_iso.o` (no WRITE option 1, no other register, no other mask or value). Linux: `exynos_pcie_phy_isolation()` = `rmw_priv_reg(pmu_alive_pa + 0x3EC4, 1, val)` (`pcie-exynos-rc.c:393-407`), as the FIRST step of
`exynos_pcie_rc_poweron()` (`:3387-3389`, before clocks/pinctrl/PHY init, no delay) and the last of `rc_poweroff` (`:3667-3669`); `PCIE_PHY_BYPASS = 1` = isolation released (`common.h:51-52`).
Preconditions (else nothing is sent, `release=refused`): timer running; `0x17463EC4` bit 0 == 0; `0x17463EC0`, GPH2.DAT, GPP14.DAT readable; PERST gph2-0 low; WLAN_EN gpp14-2 low; the SMC READ of `0x17463EC4` returns a0 >= 0 AND a0 == the MMIO word (F3). **A `--wlan-iso` image never reads the PCIe wrapper (level 2 and its SUMMARY are compiled out) and cannot contain `--wlan-en` (`build.sh` refuses the combination and `shim.c` has an `#error`; F2).**
After the set: bounded poll (<= 10 reads, 100 us apart; OUR bound, Linux has none): `ok` needs bit 0 set, a0 == 0, every other bit of `0x17463EC4` unchanged and `0x17463EC0` unchanged. a0 != 0 with nothing changed = `refused`; the word unreadable = `fault`;
anything else (accepted but unchanged, a0 != 0 but changed, other bits moved: e.g. the EL3 overwrites instead of read-modify-write) = `mismatch`. **The one clear RMW is sent after every sent set (F1; idempotent if the bit is 0)**, never a second set; it must read back bit 0 == 0 in the bounded poll AND in one more read 10 ms later (`ISO final ISO1=`).
Lines: `ISO before ISO1= ISO0=`, `ISO rmw set ret= a1=`, `ISO after set ISO1= ISO0=`, `ISO rmw clear ...`, `ISO after clear ...` and `WLAN ISO release=<ok|refused|fault|mismatch> readback=0x.. restore=<ok|FAILED|n/a>`; `!!! RESTORE FAILED` if the bit stays set.
Known blind spots (*guess*): the observed word is 0, so an overwrite instead of RMW is indistinguishable from the guessed semantics at this value; an EL3 whose late apply survives the clear and lands after the 10 ms final look would still leave the bit set (the final line would not show it); *guess*: bit 0 = 1 means the isolation cells are bypassed, not that the PHY is powered (its supplies are the LDO rails).

## Runtime MMIO pokes: `sweettouchpoke=` (xnu patch 0093)

Test a register hypothesis without a CI kernel rebuild: `tools/xrun ... --args "sweettouchclk=3 sweettouchpoke=r:10c00000;m:1e080000=1=1;d:1000;r:1e080000;"`.
Script = hex ops, **every op ends with `;`** (a script cut by the boot-args limit is a syntax error), no spaces: `r:ADDR;`, `w:ADDR=VAL;`,
`m:ADDR=MASK=VAL;` (read-modify-write), `d:US;` (0..1000000). Runs in the touch thread after `tg_cmu_enable()` and right before the SPI setup
(skipped with one line when the mux check leaves the bus alone); each op prints `SWEET TOUCH: poke N ...`. `w` does not read first; inside
SPI11 neither `w` nor `m` reads back (RX_DATA pops on read).
ADDR: physical, 4-aligned, inside SPI11 (0x1000), the USI11 sysreg page (0x1000 size is a guess), CMU_PERIC1, CMU_TOP or a touch pin bank.
CMU_PERIC1 and CMU_TOP are read-only except the words in the ADT: PERIC1 usi mux, bus mux, both auto regs, ipclk/pclk/rstnsync gates, QCH
and DIV (OPTION never), TOP mux/div/gate. Max 32 ops, 240 characters; anything bad prints one `poke: rejected (...)` line and runs nothing.
The whole boot-args line is 607 characters; `tools/mkpayload.py` now exits with an error beyond that. Parser and range table:
`bsd/dev/sweet_touch_poke.h`, tested by `shim/touchpoke_host_test.c`. Run on hardware (2026-10-22, runs touchx3 and later: `poke: 3 ops` lines, reads back the written values); a string without the final `;` is rejected (AP-10).

## tools/probe-run: one shim probe cycle, one report

```
tools/probe-run [--touch-probe[=OPTS] | --build-args "..."] [--expect REGEX]... [--timeout S] [--label NAME]   (also: tools/pix probe)
tools/probe-run --dry-run --from-dir DIR [--expect REGEX]...   # re-read DIR/usb.txt, writes nothing
tools/probe-run --self-test      # stub phone-state/to-fastboot/fboot/build/usb-log; run by check-all.sh
```
Steps, all bounded: `phone-state --expect fastboot --wait 20` (else `to-fastboot.sh` once, else ERROR with the instruction) -> `cd shim && ./build.sh
--board bluejay --avb --usb-gadget ARGS` (`--touch-probe` adds `--pstore-fast`, as the section below) -> `usb-log.py capture` in the background
**before** -> `fboot.sh --expect probe shim/out/shim-boot-avb.img` -> poll the log until every `--expect` regex matches or `--timeout` (60 s; default expect
for `--touch-probe`: `SWEET TOUCH RESULT`) -> wait up to 30 s for the phone back in fastboot or recovery (recovery -> `to-fastboot.sh`). The phone
legitimately leaves USB while the shim reboots, so absence right after the result line is normal: PASS with a "not back" note.
Run dir `logs/probe/<UTC>-<label>/`: `usb.txt`, `build.log`, `fboot.log`, `report.txt`. Report: `RESULT: PASS|FAIL|HANG|ERROR ...`, up to 12 key lines
(touch probe: the `SWEET TOUCH RESULT` lines, then `VARIANT`/`identify`/`loopback`/`rst`/`ACPM` lines), `dir: ...`. Exit 0 PASS / 1 FAIL or HANG / 2 ERROR.
FAIL = line missing, phone back. HANG = line missing and phone not back in 30 s (hold Power+VolDown). ERROR = not in fastboot at start, build or fboot failed.
Only bluejay (fboot.sh/phone-state refuse others); never raw `fastboot boot`. Verified with stubs and a dry-run of a real capture only, not on hardware.

## shim --touch-probe: touch SPI11/USI11 bring-up in seconds, no iOS boot

```
cd shim && ./build.sh --board bluejay --avb --pstore-fast --usb-gadget --touch-probe[=run[,mux][,cs][,osc|,top][,loop][,rst][,misopu][,railsr|,rails]]
tools/fboot.sh --expect probe shim/out/shim-boot-avb.img     # then read the RAM/USB/pstore log as for the other probes
```
Source `shim/touchprobe.c/.h`, host test `shim/touch_host_test.c` (run by `build.sh`), constants from `soc.json`/`board.json`
through the generated `TOUCH_*` macros (`tools/boards.py`), the same keys `tools/mkadt.py` puts into `/sweet-touch`. Options are
build-time (the shim builds in seconds); an unknown word, `osc` with `top`, or options without `run` make `build.sh` exit 2.
- `--touch-probe`: phase 1, **reads only**: CMU_PERIC1 + CMU_TOP regs, the USI11 sysreg mode, the seven pads. The USI/SPI registers
  are read **only if the sysreg mode already is SPI** (at handoff it is 0: `diag before: skipped: usi mode=0`; a hang cannot be
  caught by `try_read32`). A bus-faulting register prints `unreadable pa=...` and the dump goes on.
- `--touch-probe=run`: phase 2 after a pstore checkpoint, the writes of XNU `sweettouchclk=3 sweettouchvar=1` (patches 0088-0092:
  CMU_TOP gate, USI mux + three gates + QCH, USI mode, SPI pads, engine init, chip-id read, variants A-E until one burst completes).
  SPI/USI registers are read only after the mode was written. At handoff the mux pad gpp26-1 is an **input** (`func=0 dat=0`), so
  plain `run` writes nothing and says `leaving the bus alone`; add `mux` for that.
- Options (docs/research/21 section 3), order inside a run: dump -> `mux` -> clock chain -> `osc`/`top` -> SPI setup -> `cs` (variant F)
  -> `loop` -> `rst` -> identify (+ `misopu`) -> variants A-E (only without experiment options) -> restore:

  | Option | What it writes | Notes |
  |---|---|---|
  | `mux` | gpp26 pin 1 only: DAT := 0 first, then CON := output, as XNU 0088 `sweettouchmux=1` | read-back checked; a mismatch stops the run |
  | `cs` | X1: SPI setup is the ABL `spi_init` (USI_CON/OPTION, FB_CLK; no MODE_CFG/INT_EN/SWAP writes); variant F = GPIO CS low, `CS_REG &= ~1`, `PACKET_CNT = n\|0x10000`, TX fill, `CH_CFG \|= 3`, poll RX >= n, `CH_CFG &= ~3`, `PACKET_CNT = 0`, SW_RST, GPIO high, `CS_REG \|= 1`; identify then uses the same style | |
  | `osc` | X2: USI mux `0x10C00640` bit4 := 0, poll BUSY (bit16), divider `0x10C0180C[3:0]` := 5, poll BUSY | restored if no burst moved data |
  | `top` | X6: bit4 := 1, divider := 9 (40 MHz ipclk) | divider is writable only with `osc`/`top` |
  | `loop` | X3: `MODE_CFG` bit3 (self-loopback) with `FB_CLK` 0 then 1, 5-byte TX/RX compare | restores MODE_CFG / FB_CLK |
  | `rst` | X4: PUD := 0 on clk/mosi/miso/cs/reset, reset pad low 10 ms then high, polls the FIFO (cmd 0x87, same shape as XNU) up to 1.5 s for event 0x03 | the pulls are restored at the end; first 8 non-zero events logged |
  | `misopu` | X5: MISO PUD := 3 during the identify | restored |

  Any experiment option drops the QCH write (research 21: it hands the clock to the Q-channel handshake) and the A-E ladder.
- `railsr` / `rails` (docs/research/23, `shim/acpm.c`; need no `run`): the ACPM client. Section `TOUCH rails (ACPM)`, in three steps with a pstore
  checkpoint after each, before `run`: (0) describe the IPC channel (reads only: INIT, channel table, the descriptor of channel 2, queue pointers, INTSR0;
  `SWEET TOUCH: ACPM ...` lines), (1) READ the two enable registers (`railsr`), (2) only with `rails`: UPDATE bit 7 (value = mask = 0x80, never clear) of 0x5C then
  0x5D, ~10 ms, read both back. Every transaction prints `seq`, `req=` / `rep=` words, `ret`, `val`, `err`, `writes`, `published`, `ticks` and `us`. Replies are
  bounded to 50 ms, no retries. Nothing is ever rolled back once the request queue's front is written (the APM may be running the request): a timeout / fault then
  prints `STATE seq=.. req rear=.. front=.. rsp rear=.. front=..` and leaves the channel as it is (the next run refuses BUSY until a human has looked).
  **Gates (soc.json `acpm`)**: while `layout_verified` / `elem_unit_verified` are 0 (the shipped state) or a rail `vsel_min/vsel_max` is null, the shim prints
  `ACPM unsure: <what>` and writes NOTHING. The describe step prints the descriptor words, the pointers, the first 48 raw words of both queue buffers (one 16-byte slot per
  `ACPM req|rsp buf+OFF` line) and, on a refusal, the exact reason, WHATEVER the validation said. `vsel_min/max` ship set (L25M 0x24-0x32, L26M 0x1C-0x3C, derived from the regulator
  source and the DT limits, checked by `tools/boards.py`). Staging: `railsr` (gates closed) -> compare the dump (doc 23 erratum, `docs/HANDOFF.md` SESSION 12) -> set the two flags ->
  `railsr` again (a queue-slot write + doorbell per register, the APM reads the register: no PMIC change) -> `rails`. Both flag-set steps WRITE to the APM's SRAM: they need the Opus re-review of the fixes first.
  Last lines: `SWEET TOUCH RESULT: rails read: L25M=off L26M=off ok` / `rails update: L25M 0x23->0xa3 L26M ... on=2/2 ok`.
  Status: *observed on hardware* (run rails2, 2026-10-22: UPDATE accepted, readback `on=2/2`; the rails are off again after a reset, so the shim switches them on before the XNU jump with `--rails`); also host tests (`acpm_host_test.c`, `rails_host_test.c`).
- Safety: every write goes through one helper `w32`, which asks `w32_allowed` (per register, the exact bits that may change; a write
  that changes another bit is refused, so a bit-wise RMW bug cannot spill). Not writable: CMU OPTION, any other divider, CMU_TOP mux/div,
  the irq pad, the mux pad's pull, SPI STATUS/RX_DATA, USI wrapper words other than CON/OPTION. `tools/test-bluejay-boot.py` audits the
  source (no asm, no volatile, no pointer casts, `if (!w32_allowed(...)) return` before the write, allowlist depends on the address) and
  the object (stores only stack/argument based, `try_write32` only in `w32`), with mutation self-tests.
  All polls have a time limit and a poll budget; the fixed delays (a few us, the 10 ms reset pulse, 5 ms between ready polls) use
  `wait_ticks()` and so are not protected against a dead counter.
- Output: the XNU driver's own lines (`SWEET TOUCH: cmu base ...`, `pin clk ...`, `VARIANT F: ...`, `identify: ...`), label `SWEET` + text
  `TOUCH: ...`, so `grep 'SWEET TOUCH:'` of the RAM/USB log feeds the same parser as the XNU log; lines over the 96-byte RAM-log line are
  split at spaces with key=value tokens whole. The UART copy reads `SWEET: TOUCH: ...`. The last line is
  `SWEET TOUCH RESULT: opts=0x.. mux=.. clk=.. F=.. loop=.. rst=.. id=.. moved=F,L,R,I` (F variant F, L loopback, R reset FIFO read,
  I identify; `moved=-` means nothing moved; `id=chip-id-ok` is the FTM5 id).
- Status: *observed on hardware* (run touch1, 2026-10-22: identify `36 48 03 00` with rails on); the recipe is in XNU patch 0095 and boot-arg `sweettouchcs=1`.

## tools/phone-lock: one lock for the one phone (TP-016)

```
tools/phone-lock acquire --owner NAME [--wait S] [--ttl S] [--pid N] | release --owner NAME | status | force-release --yes
tools/phone-lock run --owner NAME [--wait S] [--ttl S] -- CMD...     # acquire, run CMD, release in a finally (also SIGTERM/INT/HUP)
tools/test_phone_lock.py                                              # host test, run by check-all.sh (unittest discover)
```
**Enforcement and queue (TP-020, 2026-10-24; review 11 fixes).** Take the phone only when you are ready to use it: `xrun` and `probe-run` build the image first and take the lock
afterwards (`--lock-wait S` waits in line; default fails at once with who holds it) and keep it until they exit. Identity is a **token**, not the owner name (the name is only a label):
`phone-lock run` and the self-locking tools create a token and export `PHONE_LOCK_TOKEN` to everything they start. `phone-lock check [--token T]` exits 0 only if that token is
the live lock's; `fboot.sh`, `fflash-userdata.sh`, `run-probe.sh`, `pstore-pull.py run` and `phone goto` (not `--dry-run`, not `state`) call it and refuse without it. So a shell that
did not get the lock from `run`/`xrun`/`probe-run` cannot flash or boot: wrap manual slots as `tools/phone-lock run --owner NAME --wait S -- bash -c '...'`. A lock with a pid ends only
when that pid is dead and its process group (`run` starts CMD in its own group) is empty, never by ttl; a bare `acquire` without `--pid` is ttl-only and warns. Waiters (`--wait`) are served in
arrival order (`$PHONE_LOCK_DIR.queue`, shown by `status`). `tools/phone-guard` is a Claude Code PreToolUse hook (wired in the super repo `.claude/settings.json`) that denies hand-typed
mutating `fastboot`/`adb` (also inside `bash -c`/`eval`); read-only queries and `adb shell getprop` pass; it is a guard rail against accidents, not a sandbox; sessions pick it up at start. Gates and builds never take the lock.
Not guarded: `sweet-log-pull.sh`, `backup-partitions.sh`, `check-udump.sh` (adb reads, run by the user with root). Tests: `tools/test_phone_lock.py`, `tools/test_phone_guard.py`, `tools/test_phone_sm.py`, `tools/probe-run --self-test`.

**Fewer false alarms, no cut tails (TP-020 follow-up, 2026-10-24).** `probe-run` waits up to 120 s (`PR_BACK`) for the phone after a shim run, because it leaves USB for 30-60 s and
returns in recovery; a slow return is a PASS note ("still rebooting"), and HANG / "hold Power+VolDown" needs the expected lines missing AND no Google USB device on the bus after the window
(on the bus but not fastboot/recovery is a FAIL pointing at `phone-state`). `xrun` waits up to 120 s (`XRUN_USB_WAIT`) for the phone to appear before asking for a button hold, gives a phone that left USB
and a hard timeout a 90 s grace window (`XRUN_GRACE`) to re-enumerate, reports a hard timeout shorter than `--restart` as PARTIAL (reset not awaited), and says "the phone reset but did not re-appear"
(not "did not fire") when USB went away. Log tails: `usb-log.py capture` drains the tty after the device node vanishes and keeps a last partial line on SIGTERM; `usbnet-log.py` drains queued
datagrams after the stop file appears. Two 2026-10-22 runs (`dvfs4-all`, `busy2`) were scored FAIL although the reset fired: `report.revised.txt` next to their `report.txt`.

Lock = directory `$PHONE_LOCK_DIR` (default `~/pixelios-artifacts/phone.lock`, outside every worktree), created with `os.mkdir`, holding `info.json`
(owner, pid, start, ttl, cmd). Stale (holder pid dead, or ttl over; default ttl 1800) = taken over and the takeover is printed. Same owner re-acquiring refreshes.
`release` by another owner exits 2; `force-release --yes` removes any lock. `run` records its own pid; a bare `acquire` records pid 0 (only the ttl ends it)
unless `--pid N`. Owner default: `$PHONE_OWNER`, else `agent-<pid>`. Last line `RESULT: PASS|FAIL|ERROR owner=NAME held_for=Ns`; exit 0 ok, 1 busy after `--wait`
(max 3600; holder and age printed) or, for `run`, CMD's own exit code, 2 error; `status` exits 0 free / 1 held. Touches no device. Tested on the host only.
