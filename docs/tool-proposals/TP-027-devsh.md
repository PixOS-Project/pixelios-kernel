---
id: TP-027
tool: "tools/devsh + org.pixelios.devd"
status: approved
proposed_by: "Claude Sonnet 5.5 (implementer, from decisions-s36.md)"
date: 2026-10-24
exposed_by: "no way to run a command or move a file on the booted iOS root without a rebuild and reflash (dev-tooling-gaps.md:47-51)"
extends: ""
related: ""
usage: "tools/devsh push SRC DST | run CMD... | pull SRC DST | sysctl K[=V] [--timeout S]"
refs: "-"
---

## checked
decisions-s36.md section T5 (lines 120-126). DEFERRED: status is approved but nothing is built until the preconditions below hold.

## why
Preconditions (all required): 0117 overlay first-run checklist passed on hardware; a research note settling `launchd_unsecure_cache=1` semantics, the ad-hoc platform binary rule (0035 MNT_ROOTFS) and userland TCP on ue0 (only kernel UDP observed so far). Daemon gated by boot-arg `sweetdevd=1` (absent = off), binds 192.168.7.2 only.

## output
Per-command report ending in a `RESULT:` line; exit 0/1/2. Verify: host test against a loopback fake daemon, then one hello run via the overlay.

## risks
Root exec on the phone: hardware-touching userland, UNREVIEWED; Opus 5.5 review required. Do not start before the preconditions.

## amendments
- 2026-10-24 driver: status approved (user approved building, 2026-10-24). DEFERRED until the preconditions in section why hold.
