---
id: TP-053
tool: "tools/gate"
status: proposed
proposed_by: "claude-sonnet-5-5 (driver 20ah, infra-20)"
date: 2026-10-31
exposed_by: "USER ORDER R57 (GATE 2X): docs/plans/PLAN-GATE-2X.md D2/D6/G1-G3: the quick gate ran steps no input of the diff reaches and nothing showed which were skipped or how long each took"
extends: "tools/check-all.sh"
related: "TP-050"
usage: "tools/gate --quick --changed [--formal] [--label NAME]   (GATE_DRY=1 prints the command); tools/gate-manifest --write|--check; tools/gate_select.py --explain FILE...; tools/gate-times [--check]; tools/gate-summary"
refs: "docs/plans/PLAN-GATE-2X.md"
---

## checked
- **Caller:** tools/gate is the entry point drivers type (docs/WORKFLOW.md, R57 after the freeze lifts); tools/check-all.sh calls tools/gate-summary at the end of every gate (ledger row, `slow=` tag) and reads tools/gate-manifest.json through tools/gate_select.py; tools/test_gate_select.py runs `tools/gate-manifest --check`.
`tools/tool-proposals check` for gate, manifest, ledger, budget: no existing tool selects steps from a table or keeps a time ledger; tools/gate-request (lint-first branch) only queues a formal gate.

## why
The quick gate selected steps by hard-coded Python, skipped silently, and nothing recorded per-step seconds, so a slow step was found by hand after the fact (median 724 s quick). Cost: every worker waits on every gate.

## output
tools/gate-manifest.json: one row per step (lane, class quick|formal|always, input globs, budget_s). `tools/gate_select.py --explain FILE...` prints one `gate: ran|skipped <step>: ...` line per step and `RESULT: PASS gate_select ran=N skipped=M`; an unresolved changed file selects FULL (`gate: FULL, unresolved: <file>`). tools/gate prints `RESULT: PASS|FAIL gate kind=quick|formal elapsed=NNNs ran=N skipped=M slow=<step:obs/budget|-> base=<sha>`. logs/gate-times.tsv is the ledger; `tools/gate-times --check` exits 1 on two consecutive total-budget breaches of one kind.

## risks
No device. A manifest row with too few inputs makes a step skip when it should run: tools/test_gate_select.py has a property test (always-class steps never skip) and 25 selector/manifest mutants; the formal gate always runs everything.

## amendments
(none)
