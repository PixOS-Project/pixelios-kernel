---
id: TP-051
tool: "tools/xrun-secret-scan"
status: proposed
proposed_by: "claude-opus-5-5 (driver 21z, fix-21)"
date: 2026-10-31
exposed_by: "F5 of docs/plans/PLAN-FAST-PATH-2026-10-31.md: the user gave a WPA2 test network (kernel .env PIXOS_WIFI_SSID/PSK/SECURITY); super HANDOFF 21y item 5 requires a check that FAILS a run if the PSK reaches any log, before F6 (first join run)"
extends: "tools/xrun_report.py"
related: "TP-037"
usage: "tools/xrun-secret-scan RUN_DIR [--env .env] [--keys PIXOS_WIFI_PSK]   (also called by xrun_report.py as a run-summary step)"
refs: "-"
---

## checked
- **Caller:** tools/xrun_report.py (run-summary step, every xrun run; FAIL turns the run RESULT into FAIL) and the F6+ Wi-Fi run plans (`--expect` on its PASS line).
`tools/tool-proposals check` for psk, secret, leak, credential: none exist or are proposed. TP-037 (golden unattended image) only keeps the
golden wifi-join PSK out of its own output (mutant test on that one step); it does not scan a run's collected logs. tools/xrun_report.py builds
the run summary (RESULT line at :613) but checks no secret. So: one small step in xrun_report plus a standalone CLI for already-collected run dirs.

## why
From F6 on, the shim/XNU will carry the WPA2 PSK (firmware-side supplicant, iovars `wsec`/`wpa_auth`/`pmk` per brcmfmac) and print a lot of the
control path in hex. One stray hexdump of the pmk iovar payload would write the user's key into `logs/` (gitignored, but copied into run labels,
handoffs, review docs and delegate prompts). A manual check means grepping every run dir by hand without printing the key: impossible to do
safely by hand, so it does not get done. Cost today: none yet (no run carries the key); cost of one miss: a leaked user credential (hard rule 4).

## output
Reads the values of `--keys` (default PIXOS_WIFI_PSK; the SSID is NOT a secret here, its length may be printed) from `--env` (default the kernel
`.env`) inside the process only. Scans every regular file under RUN_DIR (logs, UDP capture, run.json, report.json, collected dmesg) for each value in
these encodings: raw bytes, lower/upper hex (with and without separators ` `, `:`), base64, and the 32-byte PBKDF2 PMK derived from SSID+PSK
(raw + hex), since a pmk iovar dump carries the PMK, not the passphrase.
Prints per hit only `LEAK: <relative file>:<line> key=<KEY NAME> form=<raw|hex|b64|pmk>`, never the value or the surrounding bytes.
Last line `RESULT: PASS no secret in N files` / `RESULT: FAIL leaks=K files=M` / `RESULT: ERROR <why>` (env missing a key = ERROR, never a silent
pass). Exit 0/1/2. Inside xrun_report: a FAIL turns the run RESULT into FAIL with reason `secret-leak` and adds `SECRET GAP:` to the GAPS block.
Host test: a fake run dir with the key planted in each form must FAIL with the right form; the test output must not contain the key (self-check).

## risks
No device. Must never print, log, cache or write the key or the PMK anywhere (no temp files, no exception text containing it; catch and
re-raise without the value). Must not open `.env` for anything else. Bounded: skip files > 512 MiB with a loud `SKIP:` line (counted, not silent).

## amendments
(none)
