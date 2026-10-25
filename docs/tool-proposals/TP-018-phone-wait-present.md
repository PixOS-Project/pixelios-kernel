---
id: TP-018
tool: "tools/phone"
status: proposed
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-24
exposed_by: "2026-10-24: the Wi-Fi and GPU threads both parked on `phone goto fastboot` -> `FAIL state=absent` (host lsusb showed no Google device) while the phone showed the recovery screen; they only asked the user and nothing told them when the phone came back"
extends: "TP-011"
related: "TP-006"
usage: "tools/phone state [--verbose] | tools/phone goto TARGET [--wait-present S]"
refs: "-"
---

## checked
`tools/tool-proposals check phone|absent|wait-present|enumerate`: `tools/phone` (TP-011, done) already classifies `absent` and drives
recovery -> fastboot, but on `absent` it prints only the state. `tools/hang-recover` (TP-006, done) waits after a reboot, not for
"a device that has not enumerated yet". `tools/phone-lock` serialises owners but does not wait for hardware. No existing or proposed
tool covers either gap. [read from source: tools/phone lines 91, 216, 372, 496-530]

## why
Two parts, both extensions of `tools/phone`:
1. **Evidence on `absent`:** also print the raw `lsusb` Google lines (or "none"), `adb devices` and `fastboot devices` output (serials
   redacted as today) and a one-line hint: "screen may show recovery while the host sees no USB device: try another cable/port or replug".
   An agent can then tell "not enumerated" from "wrong state" without hand-typing probes.
2. **`--wait-present S`:** bounded wait (S seconds, default off) that returns PASS the moment a bluejay device enumerates in any
   state, so a parked agent runs it in the background with `phone-lock run --wait` and is woken by the notification instead of
   ending its turn waiting for a human.
Measured cost: 2 driver threads idle ~2 h on 2026-10-24 (about 05:10 report vs the user's earlier recovery state) plus one user round-trip;
the phone was visible (`18d1:4ee0`) when checked later and neither thread noticed. [observed in thread logs, not on a bus trace]

## output
Existing last line `RESULT: PASS|FAIL|PARTIAL|ERROR state=... target=...`, exit 0/1/2. On `absent`: extra lines `usb: <lines|none>`,
`adb: <lines|none>`, `fastboot: <lines|none>`, `hint: ...`. `--wait-present` timeout -> `RESULT: FAIL state=absent waited=S`. Logs stay in
the existing phone log dir.

## risks
Read-only probes (`lsusb`, `adb devices`, `fastboot devices`); no commands are sent to a device that is not bluejay (TP-011 rule kept,
other phones are never touched). Waiting is bounded; it must not hold the phone lock longer than the caller's `phone-lock` ttl. No hardware writes.

## amendments
(none)
