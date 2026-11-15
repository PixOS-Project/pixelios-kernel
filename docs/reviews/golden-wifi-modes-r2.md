# Review golden-wifi-modes-r2: delta 17ff515..8fca8d2 "golden-pull wifi modes: fix review golden-wifi-modes-r1 F1-F14" (tools/golden-pull, tools/test_golden_capture.sh)

Reviewer: Sonnet 5.5, adversarial, read-only (no phone, no adb, no <offload-host>, no full gate). Detached copy `/tmp/20ab/rev-8fca8d2` (worktree not edited).
Tags: [obs] = I ran it, [src] = file:line read, [guess] = unverified. Line numbers are tools/golden-pull at 8fca8d2 unless a file is named. Everything phone-side stays UNVERIFIED (author header, golden-pull:64-65).

## Verdict: SAFE WITH CONDITIONS

No write path to bootloader, modem, persist, efs, GPT, vbmeta, boot*, dtbo or any block device was found. Every mutating phone command is RAM (tmpfs, bind mounts, tracefs) or one of three transient files in /data/local/tmp/gp-wfw-*; `tracing_on`, `set_event`, `buffer_size_kb` are not written by any wifi mode [obs: grep, only start/stop-pull/mmio-on/coldboot lines 704-969 touch them]. The device pin (ANDROID_SERIAL after `ro.product.device == bluejay`, 207-210) covers every adb call. Conditions before the first phone run: R2-1 and R2-2 (both small); R2-3..R2-9 are advisory.

Evidence:
- `shellcheck -S warning tools/golden-pull tools/test_golden_capture.sh`: no findings, rc 0 [obs].
- `bash /tmp/20ab/rev-8fca8d2/tools/test_golden_capture.sh`: `RESULT: PASS test_golden_capture`, 2129 `ok`, 0 FAIL, rc 0, 1m44s [obs] (with `tools/third_party` symlinked in, see R2-8).
- Mutation testing: 77 mutant runs of tools/golden-pull against the wifi section of the test (trimmed copy, 422 ok on the unmutated file); 3 mutants were invalid or no-ops and were redone or discarded (F_devredir broke the regex, S_nopsklit did not match, B_noAlready was a no-op). 15 survived: C05 (R2-7), C07, P_leadzero, P_x20, S1_noSecretSkip, B_noBoundShaChk (R2-9), B_notrunc (R2-4), K_armedreset (R2-5), B_noNorb (R2-6), K_noprev_in_cleanup (R2-1), K_prevval (R2-2), and 4 equivalent/no-op ones (B_noAlready, B_dryflag, B_nomktempchk, X_noTrapReset). Scripts and logs: /tmp/20ab/mut (run.py, muts.py, mutres.txt, mutres2.txt, M*/log).
- Behavioural probes X1-X4 (R2-1, R2-2, R2-3, R2-6) in /tmp/20ab/probe (log, log2).

## Requirement 1: F1-F14 status

| r1 | Status | Evidence |
|---|---|---|
| F1 no trap, firmware left on interrupt | fixed | `wf_traps`/`wf_cleanup` 360-373: EXIT trap + TERM/INT/HUP turned into `exit`; flags set before the push and bind (1022, 1025), cleared only on success (436, 1049); stage-clean, unbind, kp-off in cleanup. Mutants C01-C04, C06, C08-C10, C12, C16 killed. INT trap has no test (C05 survived, see R2-7). |
| F2 P21 range 8..50 incomplete | fixed | `wf_p21_field` 283-290 refuses offset+width touching 8..55; rows now fetch `+56..` only (148-149); P_le55, P_ge8 killed. |
| F3 S1 deleted secret / anything | fixed, one regression | secret-in-out refusal 383 (C17 killed); only files this run wrote are deleted (471-472; C18 killed); rc 2 is a GAP (466; S2 killed). Regression: trailing-slash `--out` makes the run-written file look foreign, so the PSK file is kept (R2-3). |
| F4 PSK not masked / CRLF | fixed | CR strip + cntrl refusal 386-388 (C19, C20 killed); `psk` literal and hex in mask.sed 393-395 (S_nopsklit2, S_nohexpsk killed). |
| F5 no teardown on failed arm, prior cfg80211 lost | partly fixed | disarm on any GAP 1072; prior value recorded 1061; exit-trap disarm (C03, C11, C13, C14 killed). NOT fixed for an interrupt before the first `kprobes.txt` is parsed: R2-1. |
| F6 P21 keyed on label | fixed | keyed on symbol, 306 (P_sym killed). |
| F7 arithmetic error passes | fixed | regex without leading zeros / <= 4 hex digits 283, evaluation failure = `fail` 288. (P_leadzero survives but is equivalent: `08` still refuses through the arithmetic error path; stderr shows a bash error line.) |
| F8 denylist gaps, loose rm exception | fixed | 269-272: exact stage-clean string cut out (not a prefix); svc, setprop, setenforce, dmesg -c, logcat -c, set-wifi-enabled, mv/truncate/kill*, `> /dev/` added; each has a test (F_svc ... F_devredir2, F_rm, F_remount, F_rmmod all killed). |
| F9 dhd_trace assumed read-only | fixed (doc) | header 76-77 says the read consumes; I re-checked the cited disassembly [obs]: `str w6, [x19, #0x2c]` at .text 0x19353c inside dhd_dbg_ring_pull_single 0x1933e8, dhd_ring_proc_read at 0x193b38 (capstone on the pinned .ko). That `proc_read == dhd_ring_proc_read` is still a guess (the header says so). |
| F10 scan column mask | fixed | whole tail from the SSID column on is masked, 401-403; S_scan44 killed; tests for 32-char, multibyte, bracket SSIDs. |
| F11 hex forms | fixed | mask.sed hex rows 393-395, S1 hex scans 476-480; S3_noSsidHex killed. |
| F12 test gaps | mostly fixed | bind/unbind/readback/stage-clean are now executed by `sh` against a model (`ph_run`, test_golden_capture.sh:1086); B_unbindNoMM, B_noChmod, B_noRbChk, B_noStockChk, B_nolabelchk, B_nostockrec, G1-G5, P_p2guard killed. Remaining gaps: R2-4..R2-7, R2-9. |
| F13 third_party | fixed, but see R2-8 | missing dir now gives `SKIP:` and `RESULT: PASS ... skips=1` rc 0 [obs]. |
| F14 module identity | fixed | `wf_ko_check` 452-460 gates kprobes and probe; `WF_KO_PIN` 165 equals `sha256sum /tmp/21t-wfAnZ0/vendor_dlkm/lib/modules/bcmdhd4389.ko` = 222cd8d6...caff [obs]. C21-C23, K_noKoGate killed. Limit: it hashes the file on disk at two guessed paths, not the loaded module (R2-9). |

## Requirement 2: mutating on-phone commands, reversibility, teardown after failure

| Mode | Mutating command | Reversible | Undone after failure / interrupt |
|---|---|---|---|
| wifi-trace-probe | none (ls, cat, grep, sha256sum, dmesg, readlink; `su --mount-master -c sh` runs `true`) 978-1009 | n/a | n/a |
| wifi-fw-bind | `adb push` x3 to /data/local/tmp/gp-wfw-* (userdata) 1023; `mkdir -p /mnt/gp-wfw`, `mount -t tmpfs`, `cp`, `chmod 0444`, `chcon`, `mount -o bind` x3 over /vendor/firmware/* 338-346; `rm -f` of the 3 staged files 270 | yes, RAM + 3 files | stage-clean on every normal path (1046) and in the EXIT trap while `wf_fw_staged=1` (364-366); unbind rollback on any GAP (1048) and in the trap while `wf_fw_bind=1` (367). Not undone: SIGKILL, adb gone for the whole cleanup (each step 60/90 s, loud GAP, no retry), empty dir /mnt/gp-wfw stays (cosmetic). A TERM during an already-bound second bind unbinds the first (R2-6). |
| wifi-fw-unbind | `umount` x3 + tmpfs 351-357 | it is the undo | read-back sha vs recorded stock sha (421-434) |
| wifi-kprobes | `echo ... >> kprobe_events` x29, `echo FILTER > events/gpwifi/EV/filter` x2, `echo 1 > events/gpwifi/enable`, `echo 1 > events/cfg80211/enable` 316-325 | yes, RAM tracefs | GAP: disarm (1072) via `wf_kp_off`; interrupt: trap (368). cfg80211 prior value restored only if already parsed (R2-1). |
| wifi-kprobes-off | `echo 0 > events/gpwifi/enable`, `echo PREV > events/cfg80211/enable`, `echo '-:gpwifi/EV' >> kprobe_events` x29 326-335 | it is the undo | read-back checked in `wf_kp_off` 438-450 |
| wifi-scan | `echo GP-WIFI-SCAN > /dev/kmsg`, `cmd wifi start-scan` 1086 | yes (log line, a scan) | none needed |
| wifi-trace-pull | none on the phone (dmesg, logcat -d, dumpsys wifi, one `dd if=/proc/dhd_trace`, which CONSUMES the ring, header 76); host: `rm -f` of run-written files that hit the PSK 471 | host deletion not reversible, by design | n/a |

tracing_on, set_event, buffer_size_kb, trace clear: not written by any wifi mode [obs].
`set -uo pipefail` (golden-pull:114), no `set -e`: nothing aborts mid-teardown on a failing command; every cleanup step is `( ... ) || true` (364-368) and the EXIT trap runs on `err`/`fail`/`exit` and on unbound-variable aborts (all go through `exit`). SIGPIPE on a closed stdout still ran an equivalent EXIT trap [obs, /tmp/20ab/sp.sh].
Injection from device output: nothing read from the phone reaches a phone command. Values that flow back to the host are validated (`^[0-9a-f]{64}$` 455 and 1035, `^[01]$` 1061, `^[0-9]+$` 1006) or only printed (labels, 12-char sha prefixes). The one host-file-to-phone path is R2-2.

## Findings

### R2-1 [Medium, condition] An interrupt during the FIRST `wifi-kprobes` arm leaves events/cfg80211/enable=1 and poisons the recorded prior value
golden-pull:1056 (`wf_kp_prev=keep`), 1061 (prior value parsed only after the arm script returned), 326-331 (`keep` = leave cfg80211 alone). Scenario [obs X1]: cfg80211/enable was 0; TERM while the arm script runs. The trap disarms the 29 probes (probes_left=0) but cfg80211/enable stays 1 (the arm script already set it; `kprobes.txt` holds `cfg80211_prev=0` but nothing reads it on that path), no `cfg80211-prev.txt` is written, and nothing says so (the kpoff script only prints `cfg80211_untouched=1`). The next `wifi-kprobes` then reads `cfg80211_prev=1` from the device and records 1 (X1b: `recorded prev=1`), so `wifi-kprobes-off` can never again restore the true original 0. The existing test (test_golden_capture.sh:1504) uses CFG0=1, where "restored" and "left armed" give the same value, so it cannot see this; mutant K_noprev_in_cleanup survives.
Fix: in `wf_cleanup` before the kp-off call, when `wf_kp_prev = keep` and `$out/wifi/kprobes.txt` holds a `cfg80211_prev=[01]` line, use that value (`wf_kp_prev=$(sed -n 's/^cfg80211_prev=\([01]\)$/\1/p' "$out/wifi/kprobes.txt" | head -n 1)`, fall back to keep); if it is still `keep` and the group existed, print `INFO: cfg80211/enable left at its armed value 1 (prior value unknown)`. Add the test: `CFG0=0 mk_trace` + the TERM sigtest of line 1504 and assert `cat events/cfg80211/enable` = 0.

### R2-2 [Low-Medium, condition] `cfg80211-prev.txt` is read into `wf_kp_prev` without validation and is then pasted into a phone script
golden-pull:1056 (`wf_kp_prev=$(cat "$out/wifi/cfg80211-prev.txt")`) versus the validated read in `wifi-kprobes-off` (1079). The value goes into `echo $prev > $T/events/cfg80211/enable` (330) via `wf_kp_off` (disarm after a GAP, and the exit trap). [obs X2] a prev file containing `1; echo PWNED > /sys/kernel/tracing/trace_marker` was sent to the phone twice (`pwned-lines-sent=2`); `wf_guard` does not stop it (only the denylist words are checked). Needs write access to the out dir (same trust level as the tool), so it is hygiene, but the guard's purpose is exactly "nothing arbitrary reaches `su -c sh`" and the validated read already exists for `-off`.
Fix: at 1056 use `[[ "$(cat ... 2>/dev/null)" =~ ^[01]$ ]] && wf_kp_prev=$(cat ...)` (same test as 1079), and make `wf_kpoff_script` itself refuse anything but `keep|0|1` (`case $prev in keep|0|1) ;; *) fail ... ;; esac`). Add the test: pre-create the file with junk, run a failing arm, assert nothing but 0/1 is sent; also a valid pre-existing `1` with a failing arm (mutant K_prevval, which drops the file read, survives today).

### R2-3 [Low-Medium] `--out DIR/` (trailing slash, what shell tab-completion produces) turns "file written by this run" into "NOT written by this run", so a PSK-bearing file is kept
golden-pull:199 (`out` is not normalised), 464-472: grep prints `$out/wifi/f` with the doubled slash removed by grep, the `chk` list holds `$out//wifi/f`-style paths built from `w=$out/wifi`, so `mine` stays 0. [obs X3] with a broken mask: `GAP: S1 PSK hit: .../px3/wifi/wifi-logcat.txt (NOT written by this run: left in place ...)`, `psk files left: 1`. Fail-safe direction (nothing deleted wrongly) and loud (rc 1), but the PSK stays on disk and the message is wrong; it defeats the F3 contract "a hit in a run-written file is deleted" for a common invocation. Only matters when the mask is broken (S1 is the second line of defence).
Fix: after 199 add `out=${out%/}` (keep `/` safe: `[ "$out" = / ] || out=${out%/}`), and compare in the `mine` loop with `[ "$c" -ef "$f" ]` instead of string equality. Add a test with `--out "$w/px/"`.

### R2-4 [Low] Test gap: stock-sha record is appended, not reset, across re-binds in one out dir
golden-pull:1030 (`: > "$out/$wf_stock"`). Mutant B_notrunc survives: no test does bind, unbind, bind again with the same `--out`. Without the truncation the second `wf_unbind_run` sees two lines per file (`awk` 428 gives a multi-line `want`) and raises a false GAP "after unbind ... != recorded stock". Fix: add that sequence (bind, unbind, bind, unbind with one `--out`) and assert `fw-stock-sha.txt` has 3 lines and the second unbind is rc 0.

### R2-5 [Low] Test gap: a failed disarm is not retried by the exit trap
golden-pull:450 (`[ "$bad" != "$b0" ] || wf_kp_armed=0`). Mutant K_armedreset (reset `wf_kp_armed` even when the disarm had a GAP) survives, so nothing proves that a disarm which itself failed (probe_off_fail, adb timeout) is retried by `wf_cleanup`. Same for `wf_fw_staged` after a failed stage-clean on the normal path (mutant B_nostageflag0 is killed, so that half is covered). Fix: fake a `-:gpwifi/EV` write failure once (`FAKE_KP_OFFFAIL_ONCE`) and assert a second kp-off script is sent in the trap and the final rc is 1.

### R2-6 [Low] A signal during a second `wifi-fw-bind` unbinds the FIRST (good) bind
golden-pull:1025 and 1028 (`wf_fw_bind=1` is set before the bind script runs and cleared only when `bind_already=1` has been read). [obs X4] bind OK (4 mounts), then a second `wifi-fw-bind` with TERM during its bind script: `mounts-after=0`, the message says `active bind=1`. The unbind is back to stock (safe direction, reversible, loud message) but the earlier good bind that a following Wi-Fi run depends on is gone silently with respect to the PASS/FAIL of that earlier run. Mutant B_noNorb survives (the `wf_fw_bind=0` at 1028 is covered by the final reset at 1049). Fix: set `wf_fw_bind=1` only after the phone reported `tmpfs=ok` for a fresh bind, or make the phone script print `bind_started=1` first and have the cleanup unbind only when that line is in `fw-bind.txt`.

### R2-7 [Low] INT trap and the exit-code forcing have no test row
golden-pull:361 (`trap '...' INT`), 369 (`[ "$rc" != 0 ] || rc=1`). Mutants C05 (INT trap removed) and C07 (rc forcing removed) survive. C07 is effectively equivalent today (flags set at EXIT imply `bad>0`), C05 is a real gap: the test comment (test_golden_capture.sh:1477) says SIGINT cannot be sent to a background job. Also note for operators: a `golden-pull` started as a background job of a non-interactive shell ignores SIGINT from the start and the INT trap is a no-op there [src bash manual, "signals ignored upon entry cannot be trapped"]. Fix: send INT from a launcher that resets the disposition (`python3 -c 'import signal,subprocess,os; subprocess.Popen(cmd, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))'`) and assert the same teardown as TERM; or state in the header that only TERM/HUP are supported when backgrounded.

### R2-8 [Low] `tools/third_party` missing gives `RESULT: PASS ... skips=1`, rc 0
test_golden_capture.sh:938 and 1534-1536. [obs] with the dir absent: `RESULT: PASS test_golden_capture skips=1`, 2020 ok instead of 2129, rc 0, so any caller that greps `PASS` or checks rc treats a run that skipped 109 checks as green (AP-102 family, though it is loud in the RESULT line). Fix: exit 2 (or `RESULT: PARTIAL`) when `skips>0` unless `GOLDEN_TEST_ALLOW_SKIP=1`; the root skip (1516) is the same class (the gate runs unprivileged, fine).

### R2-9 [Info] Surviving mutants that are equivalent, dead, or low value
- P_leadzero: equivalent (both refuse `08`). P_x20: no test row `+8(%x20)` (driver-struct offset in 8..55 must be accepted); equivalent for today's rows.
- S1_noSecretSkip: dead code after the secret-in-out refusal (390); keep as belt and braces or drop.
- B_noBoundShaChk: the check at 1033 is covered by the 2nd-process read-back (1040-1041); a test where only the bound-path sha is wrong does not exist.
- B_dryflag, B_nomktempchk, X_noTrapReset: equivalent/no-op mutants. Residual hardening: `wf_x=$(mktemp -d)` (1014) has no `|| err`; an empty `wf_x` would make `cp "$1/$n" "$wf_x/$n"` target `/fw_bcmdhd.bin` (fails for non-root, so harmless today).
- `wf_ko_check` hashes the file at two guessed paths (165), not the loaded module; a module loaded from another path (or `insmod` of a pushed file) is not covered. A mismatching sha is a GAP, a missing file is a GAP, so the failure is loud; the pin only proves "the stock BP31 file is on the phone".
- Timing: `timeout 60/90` adb calls inside the trap; bash defers the TERM trap until the current foreground `timeout adb` returns (header 357-359). A wrapper that sends TERM then KILL after less than that (`timeout -k 30`) leaves the staged files/bind; the header already says SIGKILL cannot be caught.
- Re-running `wifi-kprobes` while armed: the arm script gets EEXIST per event, 29 GAPs, and the new disarm removes the original good arm [guess: not run, the fake tracefs does not model EEXIST]. Loud (rc 1), but destructive of a live capture; consider refusing early when `kprobe_events` already holds `gpwifi/`.

## Test coverage of each write path (requirement 3)

| Write path | Test row | Verdict |
|---|---|---|
| push x3 + stage-clean (normal, push failure, TERM, HUP) | 1483-1487 | covered (C01, C09, C16, B_nostageflag0 killed) |
| bind mounts, chcon, chmod, tmpfs, bind_already | `ph_run` model, 1086, 1296-1317 | covered (B_noChmod, B_nolabelchk, B_noAlready2 killed) |
| unbind + stock sha check, rollback on GAP, TERM rollback | 1305-1317, 1488-1491 | covered; re-bind in one out dir not covered (R2-4); first-bind-survives-second-TERM not covered (R2-6) |
| kprobe_events appends, filters, gpwifi/enable, cfg80211/enable | `sh` against fake tracefs 1077-1086, 1330 | covered |
| disarm on GAP, TERM disarm | 1495-1505 | covered with CFG0=1 only (R2-1), failed disarm retry missing (R2-5) |
| kprobes-off write set (enable 0, restore prev, `-:` removals) | 1428 | covered (K_noEnable0, K_noprobeoff, K_offprev, K_leftchk, K_noRmPrev killed) |
| /dev/kmsg marker + start-scan | 1442 | covered |
| S1 deletions (`rm -f -- "$f"`) | 1464-1474, 1518-1530 | covered, trailing slash missing (R2-3) |
| guard (denylist, probe whitelist, P21 range) | 1171-1300 | covered (all F_*, P_*, G1-G5 killed except the equivalents in R2-9) |
| SIGINT teardown | none | R2-7 |

## Suggested antipattern entry (not written: this brief allows only the review file)

"Teardown/restore test whose initial state equals the armed state (CFG0=1 for a setting the arm script writes as 1): a leak after the interrupt is invisible and a mutant that drops the restore survives. Start the fake in the value the arm script changes (CFG0=0), assert the value after the signal. Same family: comparing a path from grep -r to a path built from `--out` without normalising the trailing slash (AP-124/125 family)."

## Resolution (implementer 20ab, 2026-10-30; fixes of R2-1..R2-8; unreviewed, the reviewer and the driver decide)

Tags: [obs] = ran it. New rows were run against the pre-fix `git show 8fca8d2:tools/golden-pull`: 16 FAIL (R2-1, R2-2, R2-3, R2-6 rows) [obs]; with the fix: `RESULT: PASS test_golden_capture`, 2179 ok, rc 0 [obs]; `shellcheck -S warning` clean on both files [obs].

| Finding | Fix | Test rows |
|---|---|---|
| R2-1 | `wf_cleanup` takes `cfg80211_prev=[01]` from this run's kprobes.txt when `wf_kp_prev` is still `keep`; INFO if still unknown | CFG0=0 TERM during the first arm; recorded prior 1 vs device 0 (TERM and failing arm) |
| R2-2 | the read site refuses a prev file that is not `^[01]$` (REFUSED before arming); `wf_kpoff_script` returns 1 for anything but keep/0/1 and `wf_kp_off` turns that into a GAP | junk file; a tool copy that hands junk to the disarm (nothing PWNED reaches the phone) |
| R2-3 | `--out` trailing slashes are stripped | `DIR/` and `DIR//` with a broken mask: file deleted, "written by this run" |
| R2-4 | test only | bind, unbind, bind, unbind in one out dir: 3 stock lines, rc 0 |
| R2-5 | test only (fake `FAKE_KP_OFFFAIL_ONCE`) | failing arm + failing first disarm: 2 disarm scripts, probes 0, cfg80211 restored |
| R2-6 | `wf_fw_bind` is no longer cleared at bind_already; `wf_cleanup` skips the unbind when this run's fw-bind.txt says `bind_already=1` | TERM during a 2nd bind script and during its stage-clean: first bind (4 mounts) stays, no unbind script |
| R2-7 | test only (python launcher restores SIG_DFL for SIGINT, then execs) | INT during the arm (probes gone, cfg80211 0) and during the 2nd push |
| R2-8 | `RESULT: PARTIAL ... skips=N` + exit 2 when skips>0 unless `GOLDEN_TEST_ALLOW_SKIP=1` | checked with the final block alone (skips=2 -> rc 2; with the env -> PASS skips=2) |
| R2-9 | `wf_x=$(mktemp -d) || err`; bound-sha row greps its own message; rows for `+8(%x20)`, `+0100(%x2)`, a hard link of the secret | |

Mutants (copies under /tmp/20ab/mutm/NAME/, golden-pull only; the 11 survivors of the review and new ones for the new code): KILLED C05, P_x20, S1_noSecretSkip, B_noBoundShaChk, B_notrunc, K_armedreset, K_noprev_in_cleanup, K_prevval (8 of 11); B_noNorb is gone (its `wf_fw_bind=0` was removed with the R2-6 redesign; its replacement B_noAlreadyCleanup is KILLED). New mutants KILLED: B_noAlreadyCleanup, K_noKpTxtInCleanup, K_nojunkchk, K_nokpoffcase, K_nokpoffgap, R3_nostrip. SURVIVED, equivalent:
- C07_noRcForce: `[ "$rc" != 0 ] || rc=1` only matters when a flag is still set at EXIT with rc 0; every normal path clears the flag or counts a GAP (bad>0, rc 1) [read from source].
- P_leadzero: `wf_probe_guard`'s general fetch regex (`num` without leading zeros) refuses `+0100(%x2)` before `wf_p21_field` runs, so the p21 regex's own zero rule is unreachable defence in depth; the new row asserts the refusal reason of the general whitelist [read from source + obs].
- B_noNeverStarted, B_noRmBindTxt, K_nostaleRm: mutants of a stale-file guard for the microseconds between setting a flag and the WF truncation of fw-bind.txt/kprobes.txt; not reachable from a test, so the guards were removed (build the least) and the window is noted in the code comments [guess: window of microseconds].
