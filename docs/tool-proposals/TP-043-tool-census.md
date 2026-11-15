---
id: TP-043
tool: "tools/tool-census"
status: building
proposed_by: "Claude Opus 5.5 (driver 20c)"
date: 2026-10-28
exposed_by: "user rule 2026-10-28 (via Watcher 24): every implemented tool must be used or replace a named older tool; outdated/unused tools need a deprecation path"
extends: "TP-017"
related: "TP-042"
usage: "tools/tool-census [--write] [--check] [--json]"
refs: "-"
---

## checked
`tools/tool-proposals check census|inventory|unused|prune|deprecat`: TP-017 (`tools/tool-proposals prune`, done) lists `done` proposals
whose tool was not used for 30 days. It only sees tools that have a proposal (most of the 102 non-test files in tools/ have none),
measures use by file age only, has no caller graph, no deprecation state and no gate step. TP-033 (artifacts-prune) is about
~/pixelios-artifacts, not tools. No inventory file exists (`ls tools/TOOLS.md docs/TOOLS.md`: absent).
**Replaces:** the UNUSED half of `tools/tool-proposals prune` (prune keeps its STALE-proposal half; its UNUSED lines move to the census).
**Caller:** gate step in `tools/check-all.sh` (`tool-census --check`), and the driver's handoff housekeeping (docs/TOOL-PROPOSALS.md rule 7).

## why
Measured 2026-10-28 (driver 20c, grep of each tools/ basename over tools/, the super and kernel `.claude/`, AGENTS.md, docs/WORKFLOW.md,
docs/runplans): 102 non-test tools, 6 with no reference anywhere (cpu_timeout.py, hfs-straddle-check.py, phone-postmortem,
s33-decode.py, s35-decode.py, usbroot-image-check.py) [obs, grep is lenient: a doc mention counts as a caller]. 7 proposals sit at
`proposed` although the cap is 3 and some of their tools exist (tools/phone = TP-018) [obs docs/TOOL-PROPOSALS.md]. Nothing stops a
new tool from landing with no caller; nothing marks a superseded tool, so agents keep picking old ones (cost: unknown, not measured).

## output
- Inventory: `tools/TOOLS.md`, one table row per tool file (test_* files grouped under the tool they test):
  `tool | status (active/deprecated/replaced-by X/allowlisted) | callers (file, max 3 + count; no line numbers since 2026-10-29, so a line shift in a caller never makes TOOLS.md stale) | last run use | TP id | note`.
  Status comes from a small hand-kept table `tools/tools-status.tsv` (`tool<TAB>status<TAB>replaced_by<TAB>reason<TAB>evidence<TAB>since`); every
  other column is generated. `--write` regenerates TOOLS.md; without it the tool only prints the diff.
- Callers: fixed-string grep of the tool's path/basename in tools/ (excluding itself and its own tests), tools/check-all.sh steps,
  super docs/runplans/*.md, super+kernel .claude/skills and .claude/agents, super docs/WORKFLOW.md, both AGENTS.md. A test file alone
  is not a caller. **Amendment 2026-10-29 (R5):** two caller kinds are added. `test`: a `tools/test_*.py` counts only through a Python import
  statement of the tool's module (never text, comments, docstrings, never the tool's own test); a test with an own tool is a tool->tool edge
  (counts only if that tool is reached), a test with no own tool is a root edge (the gate runs every tools/test_*.py: check-all.sh step B unit /
  tools/gate-unit-shards, step C wlan-mutants). `hook`: super and kernel `.claude/settings.json`, only the `hooks.<event>[*].hooks[*].command`
  strings (permissions and every other key do not count), a root edge; unparsable JSON = WARN HOOK-SETTINGS-UNPARSABLE + `hook_bad=N` in the tail. Last use: newest run dir `logs/runs/*/` (kernel clone + every worktree) whose `xrun.log` names the tool (run.json has no argv/tools field [obs 2026-10-28]), or the newest file in the tool's own `logs/<tool>/` dir, else "-" (= unknown, not "unused").
- `--check` (gate step): FAIL for a tool with no caller and no status row; FAIL for a `deprecated` row older than one cycle (default
  14 days, `since` column) that still exists; FAIL for a new TP whose `## checked` names no **Caller:** or **Replaces:** line
  (new rule; proposals dated before 2026-10-28 are exempt). Allowlist = status `allowlisted` + a non-empty reason.
- Deprecation: a deprecated tool's first executable line prints `DEPRECATED: use X (tools/tools-status.tsv)` to stderr and exits 2
  for one cycle; the census verifies that line exists for each `deprecated` row; removal is a later commit after a review.
- Report: one line per finding (`NOCALLER tools/x`, `STALE-DEPRECATED tools/y since ...`, `DUPLICATE? tools/a ~ tools/b (same usage words)`),
  last line `RESULT: PASS|FAIL|ERROR tools=N active=A deprecated=D nocaller=K`, exit 0/1/2. No network, no device, < 10 s.

## risks
Read-only except `--write` (TOOLS.md only). Never deletes or edits a tool. A lenient grep can hide an unused tool (a doc mention
counts as a caller); the report lists the caller files so a reviewer can judge. Gate step must not fail on the existing backlog on day
one: the first census seeds `tools-status.tsv` with `allowlisted` + reason "pre-census backlog, review by 2026-11-11" for today's
no-caller tools, so only NEW uncalled tools fail.

## amendments
- 2026-10-28 (user, via driver 20c -> 20d): every status change states a REASON so we learn from it.
  1. Deprecation (active -> deprecated/replaced): reason + evidence BEFORE the change: what went wrong, why the tool is outdated, what
     replaces it; evidence names a run label, AP-NNN, docs/reviews/ file or TP-NNN. It goes in the `tools/tools-status.tsv` row
     (`reason`, `evidence` columns), in `tools/tools-lifecycle.tsv` and in the commit message. `--check` FAILs a deprecated/replaced row
     without reason + such evidence (DEPRECATE-NO-REASON) and a lifecycle row without them (LIFECYCLE-NO-REASON / -WEAK-EVIDENCE).
  2. Promotion (new tool -> active, or replacing another): a lifecycle row with the problem it solves (reason) and the evidence it works
     (test or run label). `--check` FAILs a tool with no lifecycle row (LIFECYCLE-NO-ENTRY) or whose last row does not match its status
     (LIFECYCLE-MISMATCH), a row whose `old` is not the previous row's `new` (LIFECYCLE-GAP), and a deleted tool whose last row is not
     `removed` (LIFECYCLE-GONE).
  3. Log: `tools/tools-lifecycle.tsv` (`date<TAB>tool<TAB>old<TAB>new<TAB>reason<TAB>evidence`, oldest first, `-` = new, `removed` =
     deleted), seeded 2026-10-28 with one baseline row per existing tool. An AP-NNN cited by 2+ deprecations prints
     `WARN LIFECYCLE-REPEAT`: add or date that entry in docs/ANTIPATTERNS.md.
  4. Not gate-enforced: the commit-message copy of the reason (reviewers check it; a commit-msg hook is a later proposal if missed).
- 2026-10-28 (review r1, docs/reviews/tool-census-r1.md F1-F10): `--check` is stricter. NOCALLER is exempt only for an `allowlisted` row
  with a reason (an `active` row no longer hides it); a caller of kind tool counts only on a non-comment, non-docstring line and only if
  that caller is reachable from the gate/runplans/skills/agents/workflow (unreached tools naming each other are NOCALLER, or DOC-ONLY with a
  docs/*.md mention); `replaced` gets the same stale-cycle and `DEPRECATED: use` checks as `deprecated`; future `since`/lifecycle dates,
  LIFECYCLE-ORDER, LIFECYCLE-NOOP, LIFECYCLE-MISMATCH-DATE (status `since` == newest lifecycle row date); evidence must resolve
  (EVIDENCE-UNRESOLVED; WARN without a super repo, shown as `super=missing` on RESULT; run labels by shape only); reason >= 10 chars for
  deprecated/replaced/removed; TOOLS-MD-STALE (compared with the `last run use` column masked); UNTRACKED-DIR for tools/ subdirectories
  other than rea (tool) and check_rules/testdata/third_party/__pycache__ (listed); TP rule by number (>= 42) needing a non-empty
  **Caller:** or **Replaces:**; `NOTE: skipped N files` for unreadable/binary/oversized files. The gate step runs via `tools/validate-repo.py` step B.

**Amendment 2026-10-29 (driver 20i, W26 ask: census staleness after merges, 4 hits trunk 01fbc04, cc7962e, 21h a725733, GPU c8d3dc8).**
(a) TOOLS.md caller cells name the file, not file:line (`--json` keeps lines), so a line shift in a caller never makes TOOLS.md stale.
(b) `tools/remote-job gate-pre` runs `TOOL_CENSUS_SUPER=/nonexistent tools/tool-census --check` (the container's view) before the queue and
refuses with `RESULT: ERROR` on FAIL (~4 s locally instead of a container FAIL after minutes of <offload-host> time); untracked docs/.claude files
get a loud WARNING because the bundle does not carry them. Caller: tools/remote-job (gate-pre). Review: docs/reviews/census-render-stable-r1.md.
