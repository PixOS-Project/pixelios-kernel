# Review: boot/dev-cycle a9c3297 (xnu 0121 sweet-cpustat) + 4563283 (xrun B1 default end)

Reviewer: Opus 5.5, adversarial, read-only, 2026-10-25. No phone, adb, fastboot or phone lock touched.
Worktree `~/.t3/worktrees/boot-dev`. XNU source cites refer to the staged tree that local build used for this branch,
`~/pixelios-artifacts/localbuild/work/bootdev/xnu` (xnu-8796.141.3 + stack, 0121 applied). Patch line numbers are
`grep -n` line numbers of `xnu/patches/0121-sweet-cpustat.patch`.

## Verdict: SAFE WITH CONDITIONS

0121 is diagnostic only. It does not change UFS I/O control flow, and no path in it can panic or hang the boot (read from source).
The 4563283 reset path still goes only through `tools/phone`, under the phone lock (read from source). Conditions:
F1 must be fixed before any "migration ended at" result is taken from the watch lines. F3 and F4 must be fixed before
the new default end is trusted unattended; the one hardware run with it left the phone in recovery (observed).

## Checked and OK

- **Applies.** `localbuild/work/bootdev/patches.txt`: 122 applied, skips only the 4 usual CI patches. The staged
  `bsd/dev/sweet_cpustat.c` is byte-identical to the patch body (observed: `diff` empty). 0121 also applies on top of 0120
  (wifi tree `localbuild/work/wifi`): `git apply --check` rc=0, hunks offset 1 (observed). The odd header
  `diff --git a/b/bsd/dev/...` (patch:10) resolves to `bsd/dev/sweet_cpustat.c` with both `git apply` and GNU patch 2.8
  (observed, scratch test). See F11.
- **UFS path unchanged.** The added lines only read `mach_absolute_time()` and update `sweet_ufs_stat[]`, all under
  `sweet_ufs_lock` (staged sweet_ufs.c:460-499). Error and resid handling is untouched. Readers do unlocked aligned 64-bit
  loads, which cannot tear on arm64 and cannot fault (read from source). Unit conversions are safe:
  `absolutetime_to_nanoseconds` -> /1e6 or /1e3, bytes>>10.
- **Kernel context.** `proc_iterate` drops `proc_list_lock` before the callout (kern_proc.c:3645-3660) and allocates the
  pid list with no lock held (3595-3606). `PROC_RETURNED` is released by the iterator (3666-3668). `task_info(TASK_ABSOLUTETIME_INFO)`
  is fine from a kernel thread (task.c:5074-5096). The continuation returning at `period == 0` terminates the thread. The
  0054 sampler uses the same pattern (0054 patch:180-216).
- **Hardware (observed, run `20261025T083851Z-bs-a1`, image head=a9c3297, 2026-10-25):** CPUSTAT lines every 60 s from
  +147.9 s (20 s after UFS init at +127.9 s) up to +448 s, and no panic. Counters are plausible (448 ios, 1600 KiB, busy 40 ms,
  maxcmd 168 us). Root mount failed with `BTOpenPath returned (5)`. The same failure (with the same `command 0x00 failed`) is
  in the 2026-10-24 runs `bl1-remote3` and `bl2-timer`, which had no 0121, so it is not caused by 0121 (observed). With no
  userland, only `kernel_task` was printed, so the watch was not exercised on hardware.
- **Reset targeting.** The host end only sends the reset through the existing EARLY branch (xrun:123-129):
  `tools/to-fastboot.sh` -> `tools/phone goto fastboot`. That path refuses without the lock (phone:588-590) and runs the
  bluejay check for every adb/fastboot command (phone:473-479). The UDP reset only goes to the host that answers 192.168.7.2
  (phone:490, sweet-reset-bl), which is unchanged. Panic (`fail_on`) and all-stop exits return before the new
  `host_end` branch (xrun_report.py:138-148 vs 152), so there is no second reset decision. Lock: `plock_take` comes before
  any phone access and `cleanup` drops it on EXIT (xrun:56-57,81); unchanged.
- **Host tests:** `PYTHONPATH=~/pixelios-artifacts/pylib python3 -m pytest tools/test_xrun_report.py -q` -> `11 passed in 0.18s` (observed).

## Findings

1. **HIGH (function): the default watch names can never match.** Patch:75-79 and 114.
   - `proc_name()` copies `p_comm` (kern_proc.c:1303-1304), and `p_comm` is `command_t` = `char[MAXCOMLEN+1]`, which is
     16 characters (param.h:95, resource_monitors.h:75). So `com.apple.datamigrator` (22 characters) appears as
     `com.apple.datami` and never compares equal.
   - `com.apple.migrationpluginwrapper` is 32 characters, so the parser's `tl < sizeof(cs_watch_names[0])` (32) drops it
     silently and it is never watched.
   - The full executable name is in `p_name` (`proc_name_t` = `char[2*MAXCOMLEN+1]`, filled by `set_proc_name`,
     kern_exec.c:976-986). It is `com.apple.datamigrator` in earlier logs (0042 prints `p->p_name`; research 11:89).
   - The M3 goal ("end of migration to 5 s") therefore yields no watch line, not even "live 0" (read from source).
   - **Fix:** in `cs_proc`, `char name[2 * MAXCOMLEN + 1]; strlcpy(name, proc_best_name(p), sizeof(name));` (proc.h:283,
     kern_proc.c:1402-1407). This also drops the second `proc_find`. Declare `cs_watch_names[CS_WATCH_MAX][2 * MAXCOMLEN + 1]`,
     keep the check `tl < sizeof(cs_watch_names[0])` (32 now fits), and use `strncmp(..., sizeof(name))`.
2. **MEDIUM: `--expect` now implies `--early`, which also shortens `--expect-absent` coverage.** xrun:42.
   - A run stops once every `--expect` pattern is seen, so a forbidden line printed later is never seen.
   - Before 4563283, `--expect-absent` covered the whole `--duration`.
   - **Fix:** `[ "$full" = 1 ] || [ ${#stops[@]} -gt 0 ] || [ ${#rawexp[@]} = 0 ] || [ ${#abs[@]} -gt 0 ] || { early=1; stops=("${rawexp[@]}"); }`.
     Also document in the header that `--expect-absent` keeps the full wait.
3. **MEDIUM (observed): the recovery budget is shorter than the reset path it waits for.** xrun:125.
   - Every run now ends in `timeout 120 tools/to-fastboot.sh 90`. `goto --timeout 90` caps the whole transition
     (phone:~596). The ios->fastboot wait step alone is 150 s (phone:138), plus `adb reboot bootloader` from recovery.
   - Observed in `20261025T083851Z-bs-a1`: the software reset worked (`RESULT: FAIL state=recovery ... Timeout exceeded`),
     but the phone was left in recovery and the report says "to-fastboot failed after the software reset request".
   - **Fix:** `timeout 270 tools/to-fastboot.sh 240` in the EARLY branch, which covers 150 s + 30 s adb + margin.
4. **MEDIUM: the forced remote reset has no working fallback in two cases.** xrun:43,50-51.
   - (a) `remote=1` is forced even with `--no-usbnet`. The UDP reset cannot reach the phone, yet the run still ends at
     `--duration` and reports the reset as requested.
   - (b) With `--restart N` and no `--duration`, duration = N. The host reset and the "fallback" timer then fire at about
     the same moment, so the timer is not a fallback. Without `--restart`, as in bs-a1, there is no fallback at all.
   - **Fix:** `[ "$noremote" = 1 ] || [ -z "$usbnet" ] || remote=1`. When `remote` is set and `secs` is non-empty, refuse
     (`RESULT: ERROR`) unless `secs >= duration + 150`. Default to `duration=300` and do not reuse `secs`.
5. **MEDIUM (rule "non-trivial logic leaves one runnable check"): the new logic is mostly untested.**
   - Observed, scratch copy, 4 of 7 mutants survive:
     - `host_end` branch without `run.get("remote")`: survives.
     - Without `not timed_out`: survives.
     - Without `not usb_present()`: survives.
     - Without `gone is not None`: survives.
     - The branch deleted, the `host_end` mark removed, or the report text removed: killed.
   - The xrun option resolution (xrun:40-51: `--expect` implies early, `--full`, the remote default, the duration
     default) has no test at all.
   - **Fix:** add waitusb tests for:
     - no `remote` at the duration end -> `DONE` with no `host_end`;
     - `remote` plus a hard timeout -> `TIMEOUT`;
     - `remote` with the phone back on 18d1 before the end -> `DONE`.
   - Also add a `--print-plan` (or dry) mode to xrun that prints `early/stops/remote/duration/args` and exits, with a test
     over the `--expect`, `--full`, `--no-remote-reset`, `--restart` and `--no-usbnet` combinations.
6. **LOW: the last ≤10 s are not checked for stop or fail patterns before the host end.** xrun_report.py:140,152.
   - Patterns are polled every 10 s. A panic or a full stop in the last window before `end` is reported as "ended by the
     host" with no `early_fail`.
   - **Fix:** before the `host_end` mark, run the same `fails` / `stop` check once more and return through those paths.
7. **LOW: a huge `sweetcpustat` makes `every` 0.** Patch:106,125.
   - `sweetcpustat` >= 0xFFFFFFFC (for example `=-1`) makes `every = (period + 4) / 5` wrap to 0, so `tick % every` is a
     division by zero. That is undefined behaviour; arm64 `udiv` returns 0 without a trap, so it is benign in practice
     (guess about the generated code).
   - **Fix:** `if (period > 3600) period = 3600;` after the parse, and compute `every` once before the loop.
8. **LOW: the 64-line cap is silent.** Patch:48,83.
   - The host cannot tell a truncated list from a complete one.
   - **Fix:** when `cs_lines == CS_MAX_LINES` after `proc_iterate`, print `CPUSTAT p truncated at 64`.
9. **LOW: `cmds` counts bounce chunks, not UFS commands, and `maxcmd` is not what it compared.** Patch:175-182.
   - A partial-block write issues a read and a write but counts 1 (staged sweet_ufs.c:479-485).
   - The value stored in `maxcmd` comes from a second `mach_absolute_time()` read, not from the value it was compared with.
   - **Fix:** `uint64_t dt = mach_absolute_time() - t_cmd; if (dt > sweet_ufs_stat[5]) sweet_ufs_stat[5] = dt;`.
     Either rename the field `chunks` in the printf and the header comment, or increment it at each
     `ufs_read_blocks` / `ufs_write_blocks` call.
10. **LOW: the sampler is on by default in a cumulative stack.** Patch:20,104.
    - With the default 60, every later kernel runs it: a 5 s wakeup plus a printf burst each minute. The measured cost is
      small (observed counters), but it adds timing noise to every boot.
    - **Fix:** default `period = 0` and pass `--args "sweetcpustat=60"` in the boot-speed runs. If default-on is wanted,
      record that decision in PLAN-BOOT-SPEED.
11. **INFO: malformed diff header.** Patch:10, `diff --git a/b/bsd/dev/sweet_cpustat.c b/bsd/dev/sweet_cpustat.c`.
    - Tools resolve it from `+++` today (observed).
    - **Fix:** `diff --git a/bsd/dev/sweet_cpustat.c b/bsd/dev/sweet_cpustat.c`.
12. **INFO: stale xrun header.** xrun:11 still says "--early / --stop-on turn --remote-reset ON by default", and
    xrun:18-19 still say "restart+300 s".
    - **Fix:** say that remote reset is always on unless `--no-remote-reset`, and that the default duration is 300 s.
13. **INFO: `proc_iterate` waits for processes in exec.** It is called without `PROC_NOWAITTRANS`, so the sampler thread
    can block in `proc_transwait` (kern_proc.c:3656-3657).
    - Only the diagnostic thread can wait. 0054 behaves the same.
    - **Fix (optional):** `proc_iterate(PROC_ALLPROCLIST | PROC_NOWAITTRANS, ...)`.
