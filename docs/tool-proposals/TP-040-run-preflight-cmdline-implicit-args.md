---
id: TP-040
tool: "tools/run-preflight (check cmdline)"
status: building
proposed_by: "Claude Sonnet 5.5 (driver, GPU thread)"
date: 2026-10-28
exposed_by: "g112 first start 2026-10-28 11:40Z: run-preflight cmdline PASS 597/607, then `mkpayload: cmdline is 611 bytes, the limit is 607` inside xrun's build-xnu-image"
extends: "TP-024"
related: "TP-038"
usage: "tools/run-preflight -- tools/xrun ARGS   (cmdline check counts every boot-arg xrun itself adds)"
refs: "-"
---

## checked
`tools/tool-proposals check preflight`: TP-024 (run-preflight, done) and TP-038 (xrun adt guard). tools/run-preflight c_cmdline (lines ~166-190) rebuilds the boot-args by calling `tools/build-xnu-image.sh` with the xrun flags it knows (XRUN_VAL/XRUN_FLAG, `--rw`, `--usbnet`, `--cpus`) under XNU_IMAGE_ARGS_ONLY=1. It does NOT replay the boot-args that tools/xrun adds BEFORE it calls build-xnu-image: `tools/xrun:52` appends ` sweetremote=1` (14 bytes incl. the space) whenever the run ends early (`--stop-on`, `--early`, `--expect` without `--full`: remote reset is ON by default for them), and any other xrun-side additions. TP-024's own header lists the cmdline check as exact ('the exact boot-args line tools/build-xnu-image.sh would build ... same flags as xrun passes'); it is exact only for runs without these xrun-side additions. Measured: g111d preflight 580 -> real 594 passed by luck; g112 preflight 597 -> real 611, limit 607: build failed (before the phone: harmless).

## why
Measured cost [obs]: one failed run start (~3 min of host time, a lock cycle, a watcher round-trip, and a wrong first diagnosis from me: I blamed ramdisk args before reading tools/xrun:52). The preflight is the guard meant to make this impossible; a 14-byte blind spot defeats it exactly when the line is near the limit (597/607 looked safe).

## output
Two options, the first preferred: (a) tools/xrun gets `--print-bootargs` (host only, exits after computing the final `$args` including `sweetremote=1` and every later addition, before any build or phone access) and c_cmdline calls it instead of re-deriving the flags (single source of truth, no drift when xrun changes); (b) c_cmdline replays the `--stop-on/--early/--expect` => remote rule and appends the same ` sweetremote=1` (drift risk: two places). Report line stays `cmdline  PASS|FAIL N/607 bytes` plus the list of implicit boot-args counted (`implicit: sweetremote=1 (+14)`), RESULT/exit codes unchanged. Test: a synthetic xrun argv with `--stop-on` and a line at 600 bytes must FAIL (595 PASS) and the same argv with `--full --duration` and no stop-on must not add the 14; one mutant (drop the implicit add).

## risks
None for devices (host only, before the phone lock). Risk of false FAIL only if the implicit arg list is wrong: option (a) removes that risk. Must not change the 607-byte limit logic (mkpayload.py:149 stays the source of the limit).

## amendments
- 2026-10-28 Claude Opus 5.5 (implementer, branch tools/dev-speed-gaps): built option (a): `tools/xrun --print-bootargs ARGS` (host only, exits before the run dir/build/lock; skips the runplan/CAM_HOST/--kernel checks) prints `implicit: ...|none` and the exact boot-args line from the same build-xnu-image.sh flag array the real build uses; run-preflight c_cmdline calls it (XRUN_VAL/XRUN_FLAG re-derivation removed). Correction of the premise [read from source tools/xrun:50,53]: sweetremote=1 is added to EVERY run with the USB link unless --no-remote-reset (B1 default), not only --stop-on/--early; the test pins that (--full --duration also adds +14). Unreviewed, not gated.
