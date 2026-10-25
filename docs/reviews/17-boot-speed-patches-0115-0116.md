# Review 17: xnu 0115 sweet-log-timestamps (fdc9d32) and 0116 sweet-console-mute-early (c2ea270)

Reviewer: Claude Opus 5.5, 2026-10-24, adversarial, read-only on code. Branch `boot-speed-patches` in
`~/.t3/worktrees/pixelios-d2/boot-speed`.

**Verdict: SAFE WITH CONDITIONS.** Neither patch can hang or change the boot when its boot-arg is absent, and neither can
hang it when the boot-arg is set. The conditions are C1-C3 below: a compile, a tools fix before any `sweetlogts=1` run is
read, and the timestamp caveat written into the plan.

Method:
- Patched copies of `sweet_ufs.c` and `serial_console.c`. Both patches were applied with `git apply` on scratch tree
  `~/pixelios-artifacts/scratch/stack-bVkj7S`, which already held the stack up to 0114 (`git apply -R --check 0114` passed).
  Line numbers marked "patched" refer to these copies. Others are from the scratch tree.
- A host harness compiling the patched `sweet_log_append` with a 64-byte ring, plus 4 mutants (described below).
- [read] = read from source, [obs] = observed (host run or file on disk), [guess] = guess.

## Requirement answers (short)

1. **Inert without the boot-arg.**
   - 0115 [read]: `sweet_log_ts_on()` returns 0 while `PE_state.bootArgs == NULL`. Once boot-args can be read it caches
     state 1. After that, every call costs one load and one compare, then runs the old loop byte for byte (patch lines 55-61).
   - 0116 [read]: costs one or two loads per `_cnputs`. Without `sweetfb_noconsole=2` it writes only
     `sweet_mute_early_checked`. Ordering stays log, then mute test, then draw (patched serial_console.c:302-309).
   - No header changes. The `extern uint64_t ml_get_hwclock(void)` in 0115 matches the definition at
     osfmk/arm64/machine_routines.c:2082. The header declaration (osfmk/arm/machine_routines.h:758) sits inside
     `#if defined(PEXPERT_KERNEL_PRIVATE) || defined(MACH_KERNEL_PRIVATE)` (:692-784), so bsd/ cannot see it, and the
     extern does not clash with it [read].
   - `PE_state` is visible from bsd: pexpert/pexpert/pexpert.h:332 has no gate, and bsd/kern/kern_csr.c:271 already uses
     it [read].
2. **Early boot is safe** [read].
   - `PE_parse_boot_argn` (pexpert/gen/bootargs.c:61-195) neither prints nor locks, so the console path cannot recurse.
   - XNU itself parses boot-args between `PE_init_platform` (osfmk/arm/arm_init.c:367) and `arm_vm_init` (:525), at
     lines 420, 479, 490 and 514, so the pointer is valid from the first call that sees it non-NULL.
   - A cache race between CPUs is benign: both CPUs compute the same value and store an aligned int.
   - `timebase_frequency_hz == 0` is handled. `pe_identify_machine` zeroes it and then sets 24 MHz
     (pe_identify_machine.c:65-68). It is called at pe_init.c:426, after `bootArgs` is set at :403, and on a single CPU.
   - `ml_get_hwclock` is `isb; mrs CNTVCT_EL0` (machine_routines.c:2065-2077). No per-CPU data is read, and EL1 reads are
     never trapped. `shim/start.S:74` zeroes CNTVOFF_EL2.
   - The divisions are native 64-bit `udiv` on arm64 (no helper call). `ms` cannot overflow, and `secs` wraps at 100000 s
     by design.
   - Ring accounting:
     - prefixes written = initial `bol` + the number of `'\n'` in `c[0..size-2]`, which is exactly `nlines`;
     - `at` advances exactly `size + 12*nlines` times;
     - each write is reduced `% SWEET_LOG_SIZE` (a power of two, 4 MiB). A chunk that straddles the ring end, or a chunk
       larger than the ring, behaves like the old code: it wraps and the later bytes win.
   - [obs] Host harness `/tmp/rv17/h.c` (ring shrunk to 64 B, 2000 random chunks of 1-150 B, compared against a reference
     model of the last 64 bytes) printed `ok`. It also checks `[    ?.???] `, `[    7.001] ` and the 100000 s wrap.
   - [obs] Mutants:
     - `nlines = 1`: killed.
     - count loop to `< size`: killed.
     - `% 10000`: killed.
     - dropping `&& (i < size - 1)`: survived. It is an equivalent mutant: `bol` is local and the loop ends after the last
       byte (finding L4).
3. **Prefix logic and consumers.** Line-start tracking across chunks is correct when one writer at a time calls it.
   - The normal path is serialized: `console_ring_try_empty` holds `flush_lock` and drains 16-byte chunks (patched
     serial_console.c:110, 438-489).
   - Unserialized writers can misplace a prefix but cannot corrupt the ring: early boot, `console_suspended`/panic, and the
     direct `_cnputs` in `console_write` (patched :569-571).
   - Consumers: USB replay (`bsd/dev/sweet_usb.c:836-870`, byte copy, unaffected), UFS flush (`sweet_ufs.c` log thread,
     unaffected), QR QL1/QL2 (`bsd/dev/sweet_qr.c:185,213`, unaffected apart from size), and the host tools. The anchored
     host regexes break (finding M1).
4. **0116.**
   - Value 1 is unchanged: `SweetFramebuffer.cpp:398-404` mutes on any non-zero value at the first `present()`.
   - A panic still draws (`!panic_active()`).
   - QR still works: it mutes itself and draws straight to `vinfo` (sweet_qr.c:182-188).
   - The bootstrap `sweet_early_mark` console does not go through `_cnputs`.
   - Lost: on-screen kernel text, plus the camera evidence PLAN-BOOT-SPEED:20 relies on ("XNU console first visible at
     +76"). See finding L6.
   - Setting `=1` and `=2` together: `key=value` args are last-wins (bootargs.c:141-148 then `goto gotit` at :173), so the
     later one decides.
5. **Numbering.**
   - `tools/patch-numbers status`: `RESULT: PASS no overlapping patch numbers across 12 refs`; 0115/0116 are on
     `boot-speed-patches` and `dev-overlay`; next free 0118 (0117 reserved by overlay) [obs].
   - The 0108-0114 blobs are identical on `boot-speed-patches`, `dev-overlay` and `wip/gpu-accel`. The merge base is the
     `wip/gpu-accel` tip 530ae07, so a merge fast-forwards [obs].
   - Both patches apply cleanly on the 0114 tree [obs]. `tools/stack-apply` was not run.
6. **Tests and verification:** see C1-C3 and section "First hardware run".

## Findings

### M1 (Medium): the host report breaks on prefixed lines when `sweetlogts=1`
- **Where:** tools/xrun_report.py
  - :34-35 `^\|SpringBoard\|`, `^IOK SpringBoard:` and the backboardd counterparts;
  - :38 `LISTED` `^SWEET (TOUCH|KEYS):`, `^SWEET CPU:`;
  - :266-275 `re.match(r"CRASH ...")`;
  - :362 `re.match(r"SWEET FB: swap (\d+)")`.
- **Scenario:** in an M1 run every line in `usbnet.log`/`merged.log` starts with `[SSSSS.mmm] `. The report then shows
  0 SpringBoard and backboardd lines, empty CPU and TOUCH/KEYS lists, no crashes and `FB swaps (max n) 0`. A reader takes
  that as "nothing happened".
- **Still correct:** `--expect` and the marker table use `re.search` (:106, :331).
- **Fix:** in xrun_report.py, right after `lines, ltime = add_usbnet(...)` (:321), write `merged.log` unchanged. Then, for
  matching only, use
  `TS = re.compile(r"^\[[ 0-9]{4}[0-9?]\.[0-9?]{3}\] "); lines = [TS.sub("", l, count=1) for l in lines]`.
  QR/USB alignment stays on the raw text, which is identical on both sides.
- **Test:** add one case to `tools/test_xrun_report*` (or a new test) with a prefixed `SWEET CPU:` and a prefixed `CRASH`
  line.

### M2 (Medium): the stamp is the drain time, not the printf time
- **Where:** patched serial_console.c:434 and :594-602.
- **Read:** `console_ring_try_empty` returns at once when the preemption level is not 0, and `console_write` only flushes
  "if preemption is not disabled". A line printed with preemption disabled (or from interrupt context) stays in the console
  ring and gets its stamp when a later caller drains it.
- **Scenario:** a burst printed with preemption disabled at T is drained at T+x. The timestamps then move a gap to the wrong
  side of that burst, which is exactly what M1 tries to locate.
- **Fix:**
  - In the 0115 patch header, add: "stamp = time the line's first byte reaches `_cnputs` (console-ring drain), not the
    printf time".
  - In PLAN-BOOT-SPEED M1, read the gap as "between drains" and confirm any gap found with a second marker printed with
    preemption enabled.
  - No code change needed.

### M3 (Medium): no build evidence for either patch
- **Read/observed:** `~/pixelios-artifacts/localbuild/out/kernel.development.sweet` is dated 15:20, the commits 15:50.
  `strings` finds no `sweetlogts` in it.
- **Fix:** run `tools/xnu-local-build` once with 0115/0116 before any hardware run (driver: one heavy job). Check that the
  string `sweetlogts` is in the kernel and that the build does not raise `-Werror` (e.g. `-Wshorten-64-to-32`). Not run here
  per the brief.

### L1 (Low): cites and facts in the 0115 header
- **Wrong or inexact:**
  - "raw CNTPCT read": it is `CNTVCT_EL0` (machine_routines.c:2076). It equals CNTPCT only because shim/start.S:74 zeroes
    CNTVOFF_EL2.
  - "per-CPU data (:2089)": it is :2095.
  - "mach_continuous_time/mach_absolute_time ... divide by rtclock_sec_divisor": they do not divide. Only a conversion to
    seconds would. rtclock.c:140 is where the divisor is set.
  - "serial_console.c:285": `sweet_log_append` is on :284.
- **Fix:** correct the header text. No code change.

### L2 (Low): the stamp is computed for chunks that need no prefix
- **Where:** patch lines 73-93. With `sweetlogts=1`, every 16-byte drain chunk runs ISB, MRS and 3 `udiv`, even when
  `nlines == 0` (most chunks).
- **Fix:** wrap the `ts` block in `if (nlines != 0) { ... }`.

### L3 (Low): misplaced prefixes at the off-to-on switch and from unserialized writers
- **Where:** `sweet_log_ts_bol` starts at 1. Output written before the state turns on (before `PE_init_platform`) can end
  mid-line, so the first prefix can land mid-line. Panic, early and suspended writers bypass `flush_lock`.
- **Impact:** cosmetic.
- **Fix:** none needed. Mention it next to the existing "racy" comment.

### L4 (Low): dead clause
- **Where:** `bol = (c[i] == '\n') && (i < size - 1);` (patch line 105). The second clause has no effect (equivalent
  mutant, harness above).
- **Fix:** optional, `bol = (c[i] == '\n');`.

### L5 (Low): wrong "first match wins" comment in a tool
- **Where:** tools/build-xnu-image.sh:27 says "the first match wins in PE_parse_boot_argn".
- **Read:** for `key=value` the parser keeps scanning after a match (bootargs.c:173 `goto gotit`), so the last match wins.
  Only `-flag` args break early (:121). This matters for A2: `xrun --gui --args "sweetfb_noconsole=2"` gives 2, because
  `--args` is appended last (build-xnu-image.sh:33).
- **Fix:** correct the comment. This is outside these patches.

### L6 (Low): with `=2`, a hang that is not a panic shows nothing on screen
- **Where:** 0116 by design.
- **Scenario:** with the USB log off or broken, a hang that is not a panic shows a frozen boot screen with no text. The
  camera timeline used in PLAN-BOOT-SPEED:20 is lost.
- **Fix:** add "only with usbnet on (xrun default), keep one run without `=2`" to the A2 run form. Optionally mention it in
  the 0116 header.

### L7 (Low): the QR tail snapshot holds fewer lines
- **Where:** QL1 uses `sweetqr_tail` (12000 B, sweet_qr.c:176). With prefixes it holds about 15% fewer lines (demo2
  average is 69 B/line [obs]).
- **Fix:** none. Note it if QR is used together with `sweetlogts=1`.

No Critical and no High findings: neither patch has a path that can hang, corrupt the ring, or change behaviour without
its boot-arg.

## Conditions

- **C1:** fix M3 (a local build compiles and contains `sweetlogts`).
- **C2:** fix M1 before the report of a `sweetlogts=1` run is read. Raw `usbnet.log` with awk is fine without it.
- **C3:** add the M2 caveat to the 0115 header or the plan.

## Host test to add (does not exist today)

`tools/test_log_ts.py`:
1. Extract `+` lines 28-106 of 0115 (the `#define`, the state, `sweet_log_ts_on` and the new `sweet_log_append`).
2. Prepend stubs for `os_atomic_add_orig`, `PE_state`, `PE_parse_boot_argn`, `gPEClockFrequencyInfo` and a fake
   `ml_get_hwclock`, with `SWEET_LOG_SIZE` set to 64.
3. Compile with `cc`.
4. Assert:
   - (a) inert: with no bootArgs, or with bootArgs and no arg, the ring holds the exact bytes and the total equals the size;
   - (b) random chunks against a reference model, including chunks larger than the ring;
   - (c) `[    ?.???] ` when hz is 0, and the formatting at 7.001 s and at the 100000 s wrap.

`/tmp/rv17/h.c` is a working prototype of this test (it caught 3 of the 4 mutants above, and the 4th is equivalent).

## First hardware run: what to grep

1. **Inert run (no new args):** `grep -cP '^[0-9.]*\t\[' usbnet.log` must be 0. The report's CPU list must not be empty.
2. **`sweetlogts=1`:**
   - `awk -F'\t' '$2 !~ /^\[[ 0-9]{4}[0-9?]\.[0-9?]{3}\] /' usbnet.log | head` should print only the replay's first
     partial line (plus rare mid-line cases, L3).
   - The stamps must not decrease (allow small drain reordering).
   - Note the stamps of `Darwin Kernel Version`, `8/8 CPUs`, `SWEET USB: running` and `BSD root: ufsroot0`.
   - Kernel-to-host offset: the minimum of (datagram epoch - stamp) over lines sent live, after the USB link is up.
3. **`sweetfb_noconsole=2`:**
   - The camera frames show no kernel text after the SWEET EARLY marks.
   - `usbnet.log` still has every line, and `SWEET FB: display on` is present.
   - Compare `exec launchd` to `display on` against demo2 (16 s).
