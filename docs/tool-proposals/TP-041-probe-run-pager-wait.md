---
id: TP-041
tool: "tools/probe-run"
status: proposed
proposed_by: "Sonnet 5.5 (driver, Wi-Fi)"
date: 2026-10-28
exposed_by: "wlan-runH1b (2026-10-28 15:23Z): probe-run RESULT PASS, phone absent afterwards, user had to intervene (AP-172)"
extends: "TP-004"
related: "TP-015"
usage: "tools/probe-run --kind xnu ... [--reboot-bl | --pager-wait auto]"
refs: "-"
---

## checked
TP-004 (probe-run, done) and TP-015 (probe-run timeout, done). `tools/probe-run:10-13` waits 120 s for fastboot/recovery and releases the lock; `tools/to-fastboot.sh` returns recovery to fastboot; `shim/build.sh:233` has `REBOOT_BL` (reset into the bootloader) but probe-run does not set it. No existing flag covers a shim image that ends in the pager.

## why
[obs] wlan-runH1b: shim image ends in `pager_replay()` (12 s/page, ~600 s, shim/console.c:19-20,394-440) then a plain PSCI reset (no REBOOT_BL) -> recovery. probe-run waited 120 s, printed "phone not back", `phone-lock` was released at 361 s while the pager still ran, and the phone only reached recovery after ~11 min; the user pressed buttons / waited ~15 min. Proposal: (a) probe-run builds the shim with REBOOT_BL=1 for `--kind xnu` hold images so the end-of-run reset goes to the bootloader (unverified on hardware: shim/shim.c comment "Unverified on hardware until the first run"; needs a run plan with a recovery proven first), and/or (b) when the report shows the pager (`PAGE n/m` is not in the USB log; use the image kind) wait pager 600 s + 60 s margin before judging the phone, then call `to-fastboot.sh`, and keep the lock until `phone-state` = fastboot. Removes: one user intervention per shim-only probe and a lock released while the phone is still busy.

## output
Unchanged: last line `RESULT: PASS|FAIL ...` plus a new line `PHONE: fastboot|recovery->fastboot|absent` and the elapsed wait; exit 0/1/2; log dir as today (logs/probe/<ts>-<label>).

## risks
Touches the phone only through existing tools (phone-state, to-fastboot, fboot); no new partition write. REBOOT_BL writes the volatile PMU register SYSIP_DAT0 via SMC (docs/research/10), no flash/misc write; first use must follow a run plan with proven recovery. Longer lock hold (~12 min) for pager images: queue impact. Never relock, never other partitions.

## amendments
(none)

**Prior art (driver 20i, 2026-10-29):** branch boot/w2-ufsclk (dropped, 529 commits behind trunk) had probe-run `--pstore-fast` by default
with a `--pager` opt-out, plus run-preflight modelling that default (kernel commits 060e12b, 40fd6ee, 8182216; Opus review s5 conditions 1-4).
Re-read those diffs when this TP is built; the branch's SHAs are recorded in super docs/research/branch-audit-2026-10-29.md.
