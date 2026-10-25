# Review 13: false "hold Power+VolDown" verdicts and cut log tails (TP-020 follow-up)

Scope: `git diff bf7d82d 330e123` (branch `tp-020-phone-lock`, worktree pixelios-d2). Reviewer: Claude Opus 5.5, 2026-10-24. Adversarial brief.
No phone touched; all tests ran with a temp `PHONE_LOCK_DIR`; mutations ran on a copy in /tmp/rv13mut.

## Verdict: SAFE WITH CONDITIONS

No path found where the new code makes probe-run or xrun fire at a hung phone: every next run still needs fastboot (`probe-run:90-93`,
`xrun:66-70`). But the main xrun change rests on a wrong premise (H1), the xrun pre-check still raises the false alarm the change is meant
to remove, on the queued-lock path (H2), and the AP-14/AP-22 false HANG is not fixed (H3). Fix H1-H3 before merging; M1-M5 in the same series.

## Tests run (observed, 2026-10-24, HEAD 330e123)
`test_xrun_report.py` 6 OK; `test_usb_log.py` 11 OK; `test_usbnet.py` 10 OK; `test_phone_lock.py` 28 OK; `probe-run --self-test` PASS (8 checks).

Mutations (observed): revert drain call in usb-log (killed), drop SIGTERM handler (killed), finish() only on normal exit (killed), usbnet drain off
(killed), late rule off (killed), PARTIAL rule off (killed), `if gone:` text branch off (killed), probe-run missing+USB FAIL line off (killed),
HANG->FAIL (killed). **Survived:** waitusb grace window off (`xrun_report.py:125` `if False:`), the `"[none]" not in ...` clause
(`xrun_report.py:355`), probe-run found+USB PASS line (`probe-run:113`). Untested: xrun 120 s USB wait (`xrun:54-55`), `BACK` default 120.

## probe-run verdict table (requirement 1, read from source `tools/probe-run:109-118`)
| expects | phone after BACK | Google 18d1 on bus | verdict | right? |
|---|---|---|---|---|
| found | fastboot / recovery(->fastboot) | any | PASS "phone back" | yes |
| found | absent / other | yes | PASS "on USB, not fastboot/recovery" | yes |
| found | absent / other | no | PASS "still rebooting ... (not a failure)" | verdict as before; text overclaims (M3) |
| missing | fastboot / recovery | any | FAIL | yes |
| missing | absent / other | yes | FAIL "on USB ... tools/phone-state" | yes for the Pixel; wrong if the 18d1 is another device (M4) |
| missing | absent / other | no | HANG | yes, unless the shim is still logging past `--timeout` (H3) |

The shim gadget is VID 1209 (`shim/usbproto.c:43`, `tools/usb-log.py:25`), so a shim hung while enumerated does NOT satisfy `$USBP`: a real hang
still says HANG (read from source). `$USBP` as an unquoted string: word splitting + glob work; no match -> grep gets the literal path, `-s` hides the
error, exit 2 = absent (correct). No `set -e` in probe-run. Exit codes unchanged (PASS 0, FAIL/HANG 1). Lock held up to ~120 s + `timeout 120 $TOFB` longer
than before; bounded.

## Findings

### H1 (High) `usb_gone` is the fboot departure, not the reset: grace window, FAIL text and "late" rule all assume the opposite
- Where: `tools/xrun_report.py:125-132` (comment "the phone left USB (the reset fired)"), `:380-382` ("the phone reset (USB gone at +%ds)"), `:355`; `docs/AUTOMATION.md:432-433`.
- Evidence (observed, run.json of 40 runs in `pixelios-kernel/logs/runs/2026102[2]*`): `usb_gone - t_boot` = +2 s in every run that has it. The phone leaves 18d1 as soon as fboot jumps to XNU, whose gadget is 1209 (`xnu/patches/0071-sweet-usb-device-ep0.patch:198`, `0073:98`).
- Failing scenario: XNU hangs at +100 s, sweetrestart never fires, `--hard-timeout` hits. Report now says "FAIL HARD TIMEOUT: the phone reset (USB gone at +2s) but did not re-appear", i.e. it tells the reader the reset fired when it did not (the AP-14 wrong-research-thread pattern). The `else` ("never left USB") is reachable only when fboot never left fastboot. The grace window also runs on EVERY hard timeout (6 of 7 hard-timeout runs in those logs ended with `usb ids [none]`: +90 s each with the lock held, beyond the "absolute cap" promised at `tools/xrun:12`).
- Fix: record when the phone left USB *after* the XNU gadget came up, or drop the `gone` distinction. Minimal fix: in `build()` keep the old wording `"sweetrestart did not fire or the phone hung: phone never re-appeared on USB within %ds"` (no "the phone reset" claim). In `waitusb` start the grace only when `restart` was due: `if timed_out and gone is not None and now() - t0 >= restart_s:` (parse `restart_s` as in build), and note the grace in the `--hard-timeout` header line (`xrun:12`: "cap + up to XRUN_GRACE s"). Fix the AUTOMATION.md sentence "says the phone reset but did not re-appear ... when USB went away".

### H2 (High) xrun's 120 s USB wait runs before the build and before the phone lock: a false "needs Power+VolDown hold" for queued runs
- Where: `tools/xrun:52-56` (before `plock_take` at `:65`). The brief's "moved to-fastboot step" is not in this diff: to-fastboot was already after the lock (`:66-70`).
- Scenario: agent A holds the lock and its phone runs XNU/shim (gadget 1209, no 18d1). Agent B starts `xrun --lock-wait 3600`. B's pre-check sees no 18d1, waits 120 s, then dies "no Google USB device (18d1) appeared ...: needs Power+VolDown hold". A user who follows it interrupts A's run. Pre-existing location, but the change touched these lines for exactly this false alarm and left it.
- Fix: delete `xrun:52-56` and do the bounded 18d1 wait inside the `if [ "$check" = 1 ]` block at `:66`, after `plock_take`, before `tools/to-fastboot.sh 120`. Add a dry check (env hook for the sysfs glob, like `PR_USBPRESENT`) and a test.

### H3 (High) The documented false HANG (AP-14, AP-22: capture timeout while the shim still logs) is not fixed, and that tail is still cut
- Where: `tools/probe-run:100-107` then `:118`.
- Scenario (AP-22, w3a-n32): last usb.txt line at fboot+596 s of `--timeout 600`, shim still dumping. The loop breaks at the timeout, `kill "$cap"` stops the capture (tail lost), the phone (still in the shim, 1209) is not back within 120 s and no 18d1 is present -> `RESULT: HANG ... hold Power+VolDown`. The 120 s wait only rescues runs that finish within 120 s of the limit, and then reports FAIL "missing" although the lines may have been printed after the kill.
- Fix: (a) keep the capture running through `phone_back` and kill it after (`kill "$cap"; wait "$cap"`), then `check_expects "$log"` again before the verdict; (b) if the loop ended on the timeout with the capture alive and `$(( $(date +%s) - $(stat -c %Y "$log") )) -lt 15`, report `FAIL "still logging at the ${timeout}s limit (last line Ns ago): raise --timeout; not a hang"` instead of HANG. Self-test: usblog stub that keeps appending, expect FAIL with "still logging".

### M1 (Medium) The SIGTERM flush does not reach the verdict
- Where: `tools/probe-run:102-107`. `missing` is computed before `kill "$cap"`; on the timeout path (`:103`) it is never recomputed, and there is no `wait "$cap"`. A last line without "\n" (kept only by the new `tee.finish()` on SIGTERM, `usb-log.py:303-304`) lands in usb.txt and in `keylines`, while the verdict says it is missing.
- Fix: `kill "$cap" 2>/dev/null; wait "$cap" 2>/dev/null; cap=""; check_expects "$log"` (covered by H3a).

### M2 (Medium) usbnet-log drain is unbounded and ignores SIGTERM and --duration
- Where: `tools/usbnet-log.py:49-58`; consumer `tools/xrun:39` (`wait "$usbpid"`, no timeout).
- Observed (mutation dir, loopback): sender every 0.1 s, stop file at 1 s, `duration=2` -> `run()` returned after 20.3 s, i.e. only when the sender stopped. SIGTERM only appends to `stop`, which the drain loop never reads. Real runs have >=0.3 s gaps (observed: max gap 6-10 s in the last 60 s of 6 usbnet.log files), so this does not bite today, but a log storm (panic loop, per-frame `SWEET FB: swap`) holds xrun, and the phone lock, until SIGKILL.
- Fix: `deadline = time.time() + 2.0` and `while not stop and time.time() < deadline:` around the drain (or `sock.setblocking(False)` and read until `BlockingIOError`, which takes only what was queued at stop). Test: the streaming-sender case above must return within 3 s.

### M3 (Medium) "phone still rebooting after 120s (not a failure)" asserts something not checked
- Where: `tools/probe-run:114`. Expects found, no fastboot/recovery, no 18d1 after 120 s: the shim can be hung in its teardown after the result line (the S32 "hang after EOT" class). The old text was neutral. Verdict PASS/exit 0 unchanged.
- Fix: `report PASS "all ... found; phone not back and no Google USB device after ${BACK}s: if tools/phone-state still shows absent, hold Power+VolDown"`.

### M4 (Medium) "late" rule: any 18d1 id on the bus turns a hard-timed-out run into PASS
- Where: `tools/xrun_report.py:355-357`. With H1, `gone` is always set, so the rule is "lsusb shows any 18d1 at the end". `probe()` (`xrun:94`) lists every 18d1/05ac device. A second Google-VID device plugged in during the run (AGENTS rule 2: another phone may be attached; a Xiaomi in fastboot also enumerates as 18d1 [guess, not checked]) scores a hung run PASS "reset observed (late)". A reboot from an unlogged panic/watchdog before `restart` is also PASS. `reset_at` is set to `t_end - t0`, a time that was not observed; the two `report.revised.txt` files repeat "reset observed at +286s/+466s" (rule 5). The same applies to `probe-run:117` FAIL vs HANG (text only, exit 1 either way).
- Fix: require the bluejay itself and a due reset: `phone = str(run.get("phone","")); if restart and reset_at is None and run.get("timeout") and ("adb [recovery]" in phone or "fastboot product [bluejay]" in phone) and waited is not None and waited >= restart_s:`; text `"reset inferred (phone in recovery/fastboot at the end, re-enumeration not seen)"`, not "observed". Drop the dead `"[none]"` clause (mutation survived). Add a test with `usb ids [18d1:4ee7 ] adb [error: no devices...]` -> not PASS.

### M5 (Medium) Claims stronger than the evidence
- TP-020 amendment ("fixed with tests that fail on the old code"): the grace window, the xrun USB wait, `BACK=120` and `probe-run:113` have no failing test (mutations survived / untested).
- `docs/AUTOMATION.md:430` "it leaves USB for 30-60 s and returns in recovery" and `probe-run:10`: no run cited; tag as guess or cite a run.
- usb-log drain: only pty-tested. On a real cdc-acm disconnect the tty is hung up and Linux may flush the ldisc input on hangup [guess, not checked against source], so "drains the tty after the device node vanishes" is unverified on hardware: say "unit-tested on a pty".
- Fix: add the missing tests (grace with a fake `usb_present`, xrun wait via an env glob hook) and tag the two claims.

### L1 (Low) Stale docs: `probe-run:7` still says "within 30 s"; `xrun:12` says `--hard-timeout` is an absolute cap (now + XRUN_GRACE + probe). Fix the two lines.
### L2 (Low) Env parsing: `XRUN_USB_WAIT=abc` -> `seq` error, no wait, die says "within abc s" (observed: bash keeps going under `set -e`); `XRUN_GRACE=abc` -> ValueError in waitusb, `how` empty, `timeout=1` never marked, report loses "HARD TIMEOUT". Fix: `[[ ${XRUN_USB_WAIT:-120} =~ ^[0-9]+$ ]] || die ...`; in Python `float(os.environ.get("XRUN_GRACE") or 90)` inside try/except -> 90.
### L3 (Low) PARTIAL rule (`xrun_report.py:377`) hides missing expectations from the reason (exit is 1 either way, `EXIT` at `:20`). Fix: append `"; %d expectation(s) missing" % len(bad)` when `bad`.
### L4 (Low) usb-log `drain()` loops as long as data keeps coming with <0.3 s gaps after the node vanished (only possible with `--tty-path` on another device). Fix: same 2 s deadline as M2.

Not defects (checked): SIGTERM handler -> SystemExit(0) runs `finally: tee.finish()` and `os.close`; exit 0; SIGTERM during `wait_for_tty` exits 0 with no file (probe-run never waits on `$cap`). `run["late_back"]` mutates a local dict only (`report()` saves report.json, not run.json, `:468-471`). No consumer maps PARTIAL differently (xrun exits with `report`'s code; `tools/ci-kernel` does not call xrun). No existing test was weakened (diff only adds).
