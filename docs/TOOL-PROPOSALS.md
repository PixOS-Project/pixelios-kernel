# Tool proposals

One file per proposal in `docs/tool-proposals/TP-NNN-slug.md` with YAML front matter, so agents working in parallel never
edit the same file. Any agent (driver, delegate, reviewer) may add one when a step had to be done by hand or by waiting.
Use the `tool-proposal` skill (`.claude/skills/tool-proposal/SKILL.md`): it fixes the output format. The driver sets `status`
after the user decides. Nothing is built until the user says so (AGENTS.md "Use the tools, do not idle").

Rules:
1. Check first: does the tool already exist (`ls tools/`, `tools/pix` subcommands, `docs/AUTOMATION.md`, `grep -rn KEYWORD tools docs`)?
   If an existing tool almost does it, propose a flag or extension (`extends: tools/NAME`), not a new tool.
2. Is it already proposed (the index below, `grep -l KEYWORD docs/tool-proposals/*`)? Then do NOT add a file: append an entry under
   `## amendments` of that file (`- <date> <agent>: what is new / what changes / why the original is not enough`) and add `(amended)`
   to its index line. If that proposal is `done`, the new idea is a new file with `extends: TP-NNN`.
3. Next id = highest existing + 1. File name = `TP-NNN-<slug>.md`.
4. Never rewrite someone else's proposal except `status`/`refs` (driver) and `amendments` (anyone).
5. Anti-spam: a new file needs the same manual step done 2+ times with a measured cost (first occurrence = a line in the super repo `docs/ANTIPATTERNS.md`).
   At most 3 open (`proposed`) proposals; a 4th must supersede or replace one.
6. Expiry: a `proposed` entry with no decision after 14 days becomes `rejected (stale)` at housekeeping; revive it only with a new reason.
7. Housekeeping (driver, at each handoff): `tools/tool-proposals prune` (TP-017) lists stale proposals and `done` tools unused for 30 days;
   deletions need the user's approval.

Status values: proposed, approved, building, done, rejected, superseded.

## Index (one line per proposal, newest last; keep in sync with the files)

| id | tool | status |
|---|---|---|
| TP-001 | tools/phone-state | done (amended) |
| TP-002 | tools/xrun --stop-on / --early / --hard-timeout | done |
| TP-003 | tools/check-all.sh --quick | done |
| TP-004 | tools/probe-run | done |
| TP-005 | tools/ci-kernel skips cancelled runs | done |
| TP-006 | tools/hang-recover | done (amended) |
| TP-007 | tools/tool-proposals | done |
| TP-008 | tools/acpm-verify | done (amended) |
| TP-009 | tools/xnu-local-build | done |
| TP-010 | tools/qr-calibrate | rejected (amended) |
| TP-011 | tools/phone | done |
| TP-012 | tools/xrun --burst-on REGEX | rejected (amended) |
| TP-013 | tools/mg-key NAME... [--dsc CACHE] | done (amended) |
| TP-014 | tools/lint-c | done (amended) |
| TP-015 | tools/probe-run-timeout | done (amended) |
| TP-016 | tools/phone-lock | done |
| TP-017 | tools/tool-proposals prune | done |
| TP-018 | tools/phone | proposed |
| TP-019 | tools/check-site | done |
| TP-020 | tools/phone-lock | done (amended) |
| TP-021 | tools/tools-sync | done |
| TP-022 | tools/patch-numbers | done (amended) |
| TP-023 | tools/runs | approved |
