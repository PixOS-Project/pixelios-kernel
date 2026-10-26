---
id: TP-031
tool: "tools/phone-battery"
status: proposed
proposed_by: "Claude Sonnet 5.5 (driver, GPU thread)"
date: 2026-10-26
exposed_by: "gputest25/28/31/36/40: the phone resets 40-700 s into XNU with no panic line (early reset #1-#6), while gputest24/30/32 ran 900-1250 s untouched"
extends: ""
related: ""
usage: "tools/phone-battery [--record RUN_DIR]"
refs: "-"
---

## checked
`tools/tool-proposals check battery` / `check getvar`: no tool and no proposal. `grep -rn battery-voltage tools docs`: nothing. The shim/kernel only has a static fallback (`SWEET BATTERY: static fallback, 100% 4200mV external=1 charging=0`), so nothing in the port knows the real charge. `tools/phone state` only reports fastboot/ios/recovery. No related TP.

## why
Hypothesis to test (not proven, [guess]): the Pixel runs from USB with no charger driver, the battery drains over a night of back-to-back runs with the display on and 8 CPUs, and the PMIC resets/powers off the phone at a low state of charge, which looks like an unexplained reset with no panic. The ABL exposes `fastboot getvar battery-voltage`, `battery-soc-ok` and `charger-screen-enabled` [guess: standard Pixel bootloader variables, not yet read on this phone]. Reading them costs ~2 s per run against 6 unexplained resets, each of which burned a 15-25 min phone cycle (~2.5 h so far) and could not be diagnosed after the fact.

## output
One line per variable then `RESULT: PASS battery-voltage=NNNN mV soc-ok=yes|no` (FAIL when a variable is missing, ERROR when the phone is not in fastboot with product bluejay). Exit 0/1/2. With `--record RUN_DIR` the values are written into `RUN_DIR/run.json` (`battery_mv`, `battery_soc_ok`) so xrun's report can print them; bounded to 10 s (`timeout 8 fastboot getvar`), details in `logs/phone/<stamp>/phone.log`.

## risks
Read-only `fastboot getvar` on the attached phone, only when `ro.product`/`product` is bluejay (AGENTS.md hard rule 2); no flash, no write, no reboot. Runs under the phone lock of the caller (xrun already holds it). Must never run `fastboot oem` or any write variable.

## amendments
(none)
