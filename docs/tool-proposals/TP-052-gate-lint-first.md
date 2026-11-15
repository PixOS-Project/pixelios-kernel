---
id: TP-052
tool: "tools/gate-lint-first"
status: building
proposed_by: "claude-sonnet-5-5 (implementer 20ag)"
date: 2026-10-31
exposed_by: "AP-278 (2nd occurrence, gate d4d17g, driver 21z 2026-10-31): the first gate run spent 147 s and a gate slot to find 7 unregistered mutant tables that tools/lint-mutant-needles finds in seconds"
extends: "tools/check-all.sh"
related: "TP-050"
usage: "tools/gate-lint-first [--repo DIR] [--list]; tools/gate-request --repo DIR --label L   (GATE_REQUEST_DRY=1 prints the heavy-job command)"
refs: "-"
---

## checked
- **Caller:** tools/gate-request (every formal gate request) and drivers by hand on a branch head.
`tools/tool-proposals check` for gate, lint, preflight: tools/check-all.sh runs the lint steps but only inside a gate slot (after the lock and the
xnu-build); tools/gate-select picks steps, it does not pre-run them. No tool runs them before the slot is taken.

## why
A formal gate costs minutes and a shared slot. The cheap lint steps (inputs, validate, mutant-needles, boot-markers, lint-silent, consumed, rules,
census, public-split) take seconds and need no build; run in the gate they fail one cycle late (AP-278, twice, R42: second occurrence = hard guard).

## output
`tools/gate-lint-first`: reads the ONE table tools/gate-lint-steps.tsv (name, gate, command) and runs each row in DIR; a row whose tool is not in DIR
prints `SKIP name (no tool)`; census prints `WARN CENSUS DRIFT: run tools/tool-census --write` on super_new/stale (the gate passes it; GATE_LINT_CENSUS_STRICT=1 fails). One line per step
`PASS|FAIL|SKIP name Ns`, last line `RESULT: PASS gate-lint-first steps=N` / `RESULT: FAIL gate-lint-first failed=NAME[,NAME] first="..."`; exit 0/1/2.
`tools/gate-request`: refuses a dirty tree anywhere (check-all.sh writes no stamp for +dirty) (`RESULT: FAIL gate-request dirty=N`), runs gate-lint-first (FAIL = exit 1, no gate),
on PASS starts `tools/heavy-job detach` with gate-xnu-build + check-all.sh. Host test tools/test_gate_lint_first.py: hermetic fixtures, a
table-vs-check-all.sh drift test, 12 mutants of the two tools all killed.

## risks
No device, no network, writes nothing (gate-lint-first); gate-request only starts heavy-job. A step in the table without a `step B` line in
check-all.sh is allowed only while its tool file is absent (unmerged branch); the drift test requires the line once the tool exists.

## amendments
(none)
