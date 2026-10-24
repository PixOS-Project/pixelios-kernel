---
id: TP-021
tool: "tools/tools-sync"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-24
exposed_by: "user 2026-10-24: tools should be common between running agents; the GPU worktree had no phone-lock while the Wi-Fi tree had two reviewed generations of it"
extends: ""
related: "TP-020"
usage: "tools/tools-sync status|pull [--ref common-tools] [--force]"
refs: "branch common-tools; tools/tools-sync, tools/common-files.txt, tools/test_tools_sync.py"
---

## checked
`tools/tool-proposals check sync`: none. Each agent works in its own git worktree of the same repo (kernel clone, `pixelios-gpu/kernel`, `pixelios-d2/kernel`), so the phone-path tools (lock, phone, fboot, fflash, xrun, probe-run, log capture, guard hook) exist as diverging copies: GPU had no `phone-lock`, Wi-Fi had review 11/12 fixes, mine had review 13. Related: TP-020 (the lock this keeps in step).

## why
Today a fix reaches another agent only by a chat message with cherry-pick commands (done once for 6 commits) and drift is invisible until a tool behaves differently. Design: the agent-independent phone-path files are listed in `tools/common-files.txt`; their source of truth is the branch `common-tools` (checked out in its own worktree, edited only there); `status` reports per file same/differs/missing against that ref; `pull` copies the differing files in (never over uncommitted local changes), the agent commits. Agent-specific flags (e.g. GPU's xrun options) must be upstreamed to `common-tools` so they stop showing as drift.

## output
One line per file (`same|differs|missing|only-here  path`), last line `RESULT: PASS all N common files match <ref>` / `RESULT: FAIL k of N differ (run tools/tools-sync pull)` / `RESULT: ERROR ...`; exit 0/1/2. The gate runs `status` as a warning step only (never fails a gate on drift).

## risks
No device touched. `pull` overwrites tracked files from a ref: it refuses when a listed file has uncommitted changes (`--force` overrides) and never touches files outside the manifest. Running it mid-run would change tools under a live run (AP-18): `status` warns when `phone-lock status` shows a held lock, and `pull` refuses while the lock is held by anyone else unless `--force`.

## amendments
(none)
