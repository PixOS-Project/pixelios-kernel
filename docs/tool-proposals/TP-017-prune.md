---
id: TP-017
tool: "tools/tool-proposals prune"
status: done
proposed_by: "Claude Sonnet 5.5 (driver)"
date: 2026-10-23
exposed_by: "user review of proposal housekeeping, 2026-10-23: 86 files in tools/, no record of use; TP-015 undecided"
extends: "tools/tool-proposals"
related: "TP-007"
usage: "tools/tool-proposals prune [--stale-days 14] [--unused-days 30]"
refs: "tools/tool-proposals (prune)"
---

## checked
`tools/tool-proposals check` covers existence/duplicates only; nothing reports staleness or use. Related: TP-007 (the tool itself).

## why
Removes the manual audit of which proposals are stale and which `done` tools are dead. Measured: 0 usage data today for 86 tools; the driver cannot tell dead tools from live ones without grepping git log and run logs by hand.

## output
Lists (a) `proposed` entries older than `--stale-days` (candidates for `rejected (stale)`), (b) `done` tools whose last use (git log of the file, mentions in `logs/*/run.json` and HANDOFF) is older than `--unused-days` (deletion candidates). Read-only: prints, never edits or deletes. Last line `RESULT: PASS|PARTIAL ...`, exit 0 nothing to do / 1 candidates found / 2 error.

## risks
none (read-only, no device access). Deletions stay a user decision.

## amendments
(none)
