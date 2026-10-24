---
id: TP-015
tool: "tools/probe-run-timeout"
status: proposed
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-23
exposed_by: "session 22: w3a-n9 re-run with --timeout 600 passed (AP-14)"
extends: "TP-004"
related: ""
usage: "tools/probe-run ... [--timeout S]  (new verdict TIMEOUT)"
refs: "-"
---

## checked
tools/probe-run (TP-004, done) already has --timeout and a HANG verdict; TP-006 hang-recover is about recovery, not diagnosis. Nothing reports "capture timeout while the shim is still alive".

## why
Session 21/22: `--wlan-link=9/10` runs were cut by `--timeout 180` (shim needs ~170-190 s from fboot to the LINK steps) and reported `HANG ... phone not back within 30s`; ~10 hardware cycles and a wrong research thread (AP-14). Fix: when the expected line is missing at the timeout, check `tools/phone state`; `shim-probe` means the shim is still running, so report `TIMEOUT` (exit 1) not HANG, and print `fboot->last usb line` seconds. Also default `--timeout 600` when the build args contain `--wlan-link`.

## output
Same report; new verdict line `RESULT: TIMEOUT missing: ... shim still running (state=shim-probe), captured 178s of 180s limit`. Exit codes unchanged (1).

## risks
none (read-only: one extra `tools/phone state` call).

## amendments
(none)
- 2026-10-23 claude-sonnet-5.5 (driver): add `--reject REGEX` (default reject `stopped=(timeout|fault|refused)|restore=FAILED` for LINK summaries): `--expect "LINK summary reached=S23"` alone also matches a failed run (review 4 F1).
