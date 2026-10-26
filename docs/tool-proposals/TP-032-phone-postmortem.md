---
id: TP-032
tool: "tools/phone-postmortem"
status: proposed
proposed_by: "Claude Sonnet 5.5 (driver, GPU thread)"
date: 2026-10-26
exposed_by: "gputest25/28/31/36/40/41/42/47: unexplained early resets, the phone returns as 18d1:d001 (recovery) and the next `adb reboot bootloader` (tools/phone) hides the reset evidence; request by the watcher / project lead after g47"
extends: ""
related: "TP-031"
usage: "tools/phone-postmortem [--out DIR] [--dry-run]"
refs: "-"
---

## checked
`tools/tool-proposals check postmortem/pstore/bootreason`: none. `tools/phone` has a fixed allowlist that only knows `getprop ro.product.device` and `sys.boot_completed`; `tools/phone-state` reports only the state. TP-031 (battery via fastboot getvar) is a different phase (fastboot, after the evidence is gone); related, not a duplicate.

## why
After an unexplained reset the phone sits in Android recovery for ~90 s. Everything that could name the reset (androidboot.bootreason, /proc/cmdline|bootconfig, /sys/fs/pstore, /proc/last_kmsg, recovery dmesg, thermal zone and battery temperatures right after death) is read-only and free there, but `tools/phone goto fastboot` reboots the phone and loses it (HANDOFF "OPUS DECISION #5": ABL may rewrite the ramoops area, so the cheap fields come first). 8 unexplained resets so far, each ~15-25 min of phone time with no cause.

## output
Fixed list of 9 read-only `adb shell` probes, each `timeout 8`, saved to `DIR/NN-name.txt` (default `logs/phone/postmortem-<stamp>/`, in xrun scripts `<run dir>/postmortem`), serials redacted; prints the boot reason lines, thermal zone temps (mC) and battery fields, then `RESULT: PASS|FAIL|ERROR ...`, exit 0/1/2 (FAIL = nothing readable, ERROR = adb does not report bluejay or bad usage). Built as tools/phone-postmortem (shellcheck clean, `--dry-run` lists the probes) on the lead's request; the run scripts call it only when `tools/phone-state` says recovery.

## risks
Read-only on the attached phone and only if `adb shell getprop ro.product.device` is bluejay (AGENTS.md hard rule 2); no write, no flash, no reboot, no `su`; run under the phone lock of the caller. The probes read serial-bearing files, so serials are redacted before saving; do not add probes that dump partitions or modem/persist data.

## amendments
(none)
