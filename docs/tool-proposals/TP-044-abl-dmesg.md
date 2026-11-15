---
id: TP-044
tool: "tools/abl-dmesg"
status: building
proposed_by: "claude-opus-5-5 (driver 21d)"
date: 2026-10-28
exposed_by: "panic auto-recovery PR-1 (super docs/GOALS.md, W25 ledger R1 (c)): where does a software reset land (fastboot 4ee0 vs recovery d001) and why"
extends: ""
related: "TP-006"
usage: "tools/abl-dmesg [--out DIR] [--label LABEL]"
refs: "super docs/research/ABL-oem-dmesg.md (67018b1), super docs/research/panic-autorecover.md section 5"
---

## checked
`tools/tool-proposals check` for `dmesg`, `oem dmesg`, `abl-log`: RESULT NONE (exists=0 proposed=0); `grep -rli "oem dmesg" tools docs`: no hit. `tools/phone` reads the state only, and TP-006 hang-recover is the host-side recovery: neither reads the bootloader log. TP-002 is "xrun stop on early hard timeout" (unrelated). Number note: TP-043 is taken by tools/tool-census on branch tools/tool-census (not on trunk yet), so this is TP-044.
**Caller:** the ba6-btaudio run plan post-run step (super docs/runplans/ba6-btaudio.md, W25 ledger R1 (c)); later panic auto-recovery PR-2. **Replaces:** nothing.

## why
Whether a SWEET-RESET-BL / 0140 reset lands in fastboot or in recovery is open since g46 (2026-10-26): g41 landed in fastboot, g46-g49 and usblog-cable1 (2026-10-28) in recovery, 91 s to recovery plus one more reboot to fastboot [obs, super research/panic-autorecover.md section 5]. ABL logs `Reboot mode: 0x%x`, RST_STAT and `fastboot enter reason` (ABL strings 0x3fe0, 0x119f0) in its current-boot ring, and `fastboot oem dmesg` is the only read path. The handler (ABL 0x3b488) is reversed READ-ONLY (stack stores, plus a log-echo flag it saves, clears and restores; no smc, msr, SYSIP_DAT0, misc, env or partition writer in its callee tree) [decompile; driver spot-check of the 39 instructions]. Hard rule: no hand-typed fastboot, so a tool is needed. Without the cause, panic auto-recovery cannot promise "fastboot within 30 s" (PR-2..PR-4 wait on it).

## output
- Precondition checks, each a refusal with exit 2: one device in fastboot whose `product` is `bluejay` (through the existing tools/phone helpers); otherwise `RESULT: ERROR not bluejay/fastboot` and no command sent. It never reboots, flashes or erases.
- Runs exactly `fastboot oem dmesg` (unlocked only; a locked phone answers `FAILoem command (dmesg) is not allowed when locked` -> `RESULT: FAIL locked`), with a timeout of 30 s.
- Writes the joined TEXT chunks (<= 252 B each, chunk boundaries anywhere) to `logs/abl-dmesg/<stamp>-<label>.txt` (gitignored), with the serial masked.
- Prints the matching lines `Reboot mode:`, `RST_STAT`, `Reboot Info:`, `fastboot enter reason` and the byte count, then `RESULT: PASS bytes=N reboot_mode=0x.. rst_stat=0x..` (fields `unknown` when not found = `RESULT: PARTIAL`, exit 1). Exit 0/1/2.

## risks
- Touches the phone over fastboot (read-only command per the decompile). Guard: product == bluejay (hard rule 2); never runs `reboot`, `flash`, `erase`, `oem` with any other argument.
- Open caveat from the reverse: the USB transmit is an indirect call (`blr` at 0x53998) shared by every fastboot reply (getvar too), so it adds no risk over existing fastboot use.
- Limitation, not a risk: the ring holds the CURRENT boot only (0xfd800000, ~512 KiB, reset at each boot; the previous-boot copy at 0xfd880000 is not printed). If the reset landed in recovery, the extra reboot to fastboot replaces the reason with that of the recovery -> bootloader reboot; the caller records the landing state first (tools/phone state) and reads the log only when the phone landed directly in fastboot.
- Caller: the ba6-btaudio run plan's post-run step (W25 R1 (c)); later PR-2. Owner of the build: driver 20d (infra).

## amendments
(none)
