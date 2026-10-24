---
id: TP-016
tool: "tools/phone-lock"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-23
exposed_by: "session 22: Wi-Fi bring-up (wip/w3a-wlan-link) and GPU work (separate branch) both need the one Pixel 6a"
extends: ""
related: "TP-015"
usage: "tools/phone-lock {acquire --owner NAME [--wait S] [--ttl S] | release --owner NAME | status | force-release --yes}"
refs: "edab5c7 (wip/w3a-wlan-link); wrappers for probe-run/xrun/fboot not applied, use phone-lock run"
---

## checked
`tools/tool-proposals check lock|phone`, `grep -n flock|lockfile` in tools/phone, probe-run, xrun, fboot.sh: no device lock exists.
`tools/phone` is a state machine, not an arbiter; `tools/phone-state` only reads. Related: TP-015 (probe-run), TP-006 (hang-recover).

## why
Two agents on separate branches share one phone. Without arbitration, a `fastboot boot` or a reset from one run kills the other's
capture, which looks like a hang (AP-14 class). Measured cost today: ~10 wasted hardware cycles came from misread runs; a
collision would add the same, plus risk of a flash step overlapping a probe boot. Fix: an exclusive lock file with owner, pid
and TTL, taken by every tool that touches the phone (phone, probe-run, xrun, fboot.sh, fflash-userdata.sh), so a turn is
`acquire` ... run ... `release`, and the wrapper tools do it themselves.

## output
One line per call; last line `RESULT: PASS|FAIL|ERROR owner=NAME held_for=Ns`. Exit 0 acquired/released, 1 busy after `--wait`
(prints holder, age), 2 error. Lock is a directory/file under the shared `~/pixelios-artifacts/phone.lock` (outside both
worktrees so both branches see it), contents: owner, pid, start, ttl, command. Stale = holder pid dead or ttl expired: taken
over and logged.

## risks
Touches no device. Must never block `tools/phone goto fastboot` recovery by a dead holder (stale takeover handles it).
`force-release` needs `--yes`. A tool that forgets to release is bounded by the TTL (default 1800 s, longer than any run).
Wrapping existing tools edits hardware-adjacent scripts: Opus review of the wrapper change, no behaviour change otherwise.

## amendments
(none)
