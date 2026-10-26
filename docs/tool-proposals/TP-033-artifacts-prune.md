---
id: TP-033
tool: "tools/artifacts-prune"
status: proposed
proposed_by: "Claude Sonnet 5.5 (delegate)"
date: 2026-10-26
exposed_by: "disk-inventory-2026-10-26 proposed 7.7 GB for rm -rf from a naive 'unreferenced' grep (AP-73); the Opus check (OPUS DECISION 2026-10-26 #3, super docs/HANDOFF.md:204) cut it to 5.4 GB by hand"
extends: ""
related: "TP-017"
usage: "tools/artifacts-prune [--root ~/pixelios-artifacts] [--age-days 14] [--min-mb 100] [--keep-kernels 5] [--keep-file FILE] [--yes]"
refs: "-"
---

## checked
[observed 2026-10-26] `ls tools | grep -iE 'prune|disk|clean|artifact'` in the kernel clone: nothing; `ls docs/tool-proposals | grep -iE 'artifact|disk|clean'`: nothing. TP-017 prunes proposals/tools only, not disk artifacts. Scope [observed, du]: `localbuild` 13G, `scratch` 2.0G, 16 `kernel-*.sweet` at 16M each under `~/pixelios-artifacts`.

## why
Cleanup #3 was done by hand: the first list (inventory, 7.7 GB) was built by grepping tools, HANDOFF and g*.sh only and called `localbuild/work/{7a7e487806ab,fd1ad36,kernelfinal}`, `scratch/stack-{aSgNDs,D8mSb1}` and 8 kernels "unreferenced" (AP-73, docs/ANTIPATTERNS.md:471-475). They were cited as source trees behind `[src file:line]` claims and 5 kernels booted in recorded runs; deleting them would orphan rule-5/7 citations and the binaries needed to read those panics. An Opus agent had to redo the checks (OPUS DECISION 2026-10-26 #3, super docs/HANDOFF.md:204-231: exists, no running process, lsof, not a worktree path, no mention in GOALS/PLAN/g*.sh) and approved 5.4 GB. The tool makes that check one command, the same every time. Cost [observed]: one extra Opus round plus a manual multi-grep per cleanup; 3 cleanups so far.

## candidates (scan)
Targets under `--root`: `localbuild` and `localbuild/work/*` (per entry), `localbuild-overlay`, `scratch/stack-*`, `kernel-*.sweet`, old iOS roots/images (`*.img`, `/tank/pixelios/ios-root*` via `--extra-dir`). An entry is a candidate when mtime older than `--age-days` AND size over `--min-mb`, and NO keep rule hits.

## keep rules (any hit = KEEP, the reason is printed)
1. Referenced by a `logs/runs/*/run.json` (kernel clone and every `git worktree list` path): match the basename and the full path. Kernels booted in a recorded run are kept.
2. Cited in docs: `/usr/bin/grep -rlI -F` of the basename and path, one pattern per call (not a long alternation: ugrep hits complexity limits, AP-73) over the super `docs/` (research, reviews, handoff-archive, HANDOFF, GOALS, PLAN), the kernel clone `docs/` and every worktree `docs/`; counts printed per file.
3. In use: any file under it open per `lsof +D` / `fuser`, or a process whose cmdline (`ps -eo args`) contains the path (xrun, xnu-local-build, stack-apply, make, clang).
4. Listed in the KEEP file (`--keep-file`, default `$ROOT/KEEP`, one path or glob per line, `#` comments).
5. Used by a phone-lock-held script: referenced by a `$ROOT/g*.sh` or any script named in `$ROOT/phone.lock` (read the lock, do not take it; read-only).
6. Is a `git worktree list` path, or a path named in docs/GOALS.md.
7. `kernel-*.sweet`: always keep the newest `--keep-kernels` N by mtime.
Fail closed: if any scan (grep, lsof, git worktree list, run.json parse) errors, the entry is KEEP and the run ends exit 2.

## output
Dry run by default: table of `DELETE|KEEP  size  age  path  reason/ref-count`, total reclaimable, `df` before. With `--yes`: deletes only the DELETE rows (`rm -rf` after re-running keep rules 1-3 on that entry right before removal), logs each to `logs/artifacts-prune/<ts>.txt`, prints `df` after. Never follows symlinks out of `--root`; refuses paths outside the root. Last line `RESULT: PASS|PARTIAL|ERROR ...`; exit 0 nothing to delete (or deleted OK), 1 candidates found in dry run, 2 error/scan failure.

## risks
No device access (no adb/fastboot; reads `phone.lock`, never takes it). The danger is deleting a cited artifact: mitigated by the fail-closed keep rules above; deletion only with `--yes`. Needs a unit check with a fixture tree (cited, run.json-referenced, open-file, KEEP-listed, newest-N, plain-old) so a rule cannot silently break. Cleanup of anything outside `$ROOT` stays out of scope.

## implementer steps
1. `tools/tool-proposals new artifacts-prune` (or keep this file), set status building.
2. Write the tool (python3 -I, stdlib): parse args; enumerate targets; compute size (`du -sb`) and mtime.
3. Implement keep rules 1-7 as separate functions returning (hit, evidence); one test per rule on a temp fixture tree.
4. Dry-run report + RESULT line + exit codes; `--yes` path with per-entry re-check and log file.
5. Re-run the 2026-10-26 case as acceptance: on the pre-cleanup tree the tool must KEEP the 5 trees and 5 kernels of HANDOFF #3 and list the 9 deleted items.
6. shellcheck if any wrapper script; register in `docs/TOOL-PROPOSALS.md`, run `tools/tool-proposals validate`.
7. Mark AP-73 "Enforced by: tools/artifacts-prune".

## amendments
(none)
