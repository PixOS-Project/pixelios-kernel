---
id: TP-022
tool: "tools/patch-numbers"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-24
exposed_by: "user 2026-10-24: GPU and Wi-Fi worktrees pick overlapping xnu patch numbers; wip/gpu-accel already holds 0102-0111 while every other branch stops at 0101"
extends: ""
related: "TP-021"
usage: "tools/patch-numbers status|next [--owner N --name SLUG]|release NNNN|check"
refs: "tools/patch-numbers, tools/test_patch_numbers.py (common-tools)"
---

## checked
`tools/tool-proposals check patch` (no match); `tools/validate-repo.py` and `tools/stack-apply` check contiguity and application inside ONE tree only, so two branches that each add 0102 pass their own gates and collide at merge. Related: TP-021 (shared tools).

## why
A renumbering at merge time means regenerating context-dependent patches and re-running `stack-apply` + a kernel build (~15-70 s locally, ~4-8 min CI) plus re-review of the moved patch, and it breaks every run label that cites a number. `patch-numbers next` reserves the number atomically as a git ref (`refs/patch-numbers/NNNN`, shared by all worktrees, nothing committed); `status` and `check` show overlaps across all local and remote branches.

## output
One line per number (`NNNN name [branches] RESERVED by owner`), `CONFLICT ...` lines, last line `RESULT: PASS|FAIL|ERROR`; exit 0 ok / 1 conflicts / 2 error. `next` prints the number on its first line.

## risks
No device. Writes only `refs/patch-numbers/*` in the shared git repo (and a blob). A reservation nobody uses is harmless (`release`). It does not rename or edit any patch.

## amendments
(none)
- 2026-10-24 Claude Sonnet 5.5: user asked for enforcement: added `tools/patch-guard` (Claude PreToolUse hook, denies a new patch under an unreserved number), `claim`, `precommit` and `install-hook` (git pre-commit hook shared by all worktrees); the shared patch directory idea was tried and backed out (every build would contain everyone's patches).
