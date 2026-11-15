---
id: TP-039
tool: "tools/gate-xnu-build"
status: building
proposed_by: "Sonnet 5.5 (driver, Wi-Fi)"
date: 2026-10-28
exposed_by: "Wi-Fi G1 patch 0167: formal gate-pre PASS on f022d0b + import, then tools/xnu-local-build failed (sweet_wlan.c:409 undeclared pmap_find_phys), 2026-10-28; AP-147"
extends: "TP-009"
related: ""
usage: "tools/gate-xnu-build [--stack DIR] [--label NAME]"
refs: "-"
---

## checked
`tools/tool-proposals check xnu-local-build|gate`: tools/xnu-local-build exists (TP-009, done) and compiles the whole patched XNU, but no check-all.sh step calls it (ALL list, tools/check-all.sh:72: build-wlan builds the shim only; unit/wlan-mutants compile the extracted pure headers). So an XNU glue file (bsd/dev/sweet_wlan.c etc.) is never compiled by the gate. Extends TP-009; related AP-147.

## why
Patch 0167 passed a formal gate (1457 s on <offload-host>, imported) and the kernel did not compile; found only when the driver built it by hand: stamp void, one extra commit and a second 1450 s gate, plus a wrong kernel copied by a script after the failed build. Cost of the check: 17 s (warm, patch change) to 384 s (cold, -j4) host-only; cost of the miss: ~25 min of <offload-host> plus a day of review/run-plan churn. A gate step `xnu-build` (only when `git diff trunk -- xnu/patches` is non-empty) removes the class of error.

## output
One wrapper step: runs tools/xnu-local-build with the worktree's xnu/patches under a per-worktree label, prints the first `error:` lines and the kernel sha256; last line `RESULT: PASS xnu-build sha=<kernel sha256>` / `RESULT: FAIL xnu-build errors=N first=FILE:LINE` / `RESULT: ERROR ...`; exit 0/1/2; log in the xnu-local-build work dir. Refuses to publish the kernel on FAIL (never leaves a stale kernel.development.sweet under the new name).

## risks
No device. Cold build is ~7 min at -j4 and memory heavy (the dev machine caps -j4): run it as its own gate step on <offload-host>, one heavy job at a time, nice/ionice. Must not touch the phone, flash anything, or use root.

## amendments
- 2026-10-28 Claude Opus 5.5 (implementer, branch tools/dev-speed-gaps): built tools/gate-xnu-build as specified (builds the COMMITTED HEAD:xnu/patches via git archive into a temp stack, label gate-<checkout>, per-label flock, kernel taken from the label work tree and its sha256 checked against the printed one, never from the shared localbuild/out copy; SKIPPED only when HEAD:xnu/patches == trunk:xnu/patches, --always overrides). Wired into `tools/remote-job gate-pre` (refuses to ship a stack that does not build), NOT into tools/check-all.sh: gate-pre runs check-all.sh in a --network=none container without $LOCALBUILD (tools/remote-job:15-21), where xnu-local-build cannot run. Host test tools/test_gate_xnu_build.py with a fake xnu-local-build; no real build measured. Unreviewed, not gated.
