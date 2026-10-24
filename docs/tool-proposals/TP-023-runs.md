---
id: TP-023
tool: "tools/runs"
status: approved
proposed_by: "Claude Sonnet 5.5 (implementer, from decisions-s36.md)"
date: 2026-10-24
exposed_by: "run history is read by hand from logs/runs and logs/probe dirs; AP-14/AP-22 timeouts were chosen without the previous same-form duration (tools-audit.md, procedure-standard.md E1)"
extends: ""
related: "TP-024"
usage: "tools/runs list [--since YYYY-MM-DD] [--kind xrun|probe] [--all-worktrees] | show ID | stats --form REGEX | compare A B"
refs: "-"
---

## checked
decisions-s36.md section T1 (lines 61-71). `tools/tool-proposals check runs` expected NONE; tools-audit.md:139 says TP-023/TP-024 share one run-dir parser, written once here.

## why
Reading a run or the p50/max time of a form means opening dirs by hand. TP-024 check 4 needs `stats` for the timeout margin. [observed: xrun dirs hold report.json, run.json, report.txt; probe dirs hold report.txt and usb.times, no report.json (decisions-s36.md:65-66)]

## output
Table on stdout, last line `RESULT: PASS n=<dirs> unreadable=<k>`. Exit 0, 1 if any dir unreadable (listed), 2 bad args. `--all-worktrees` = `logs/` of every `git worktree list` path. `stats --form RX`: p50/max wall time and PASS/FAIL/HANG/ERROR counts per label stem. Files: `tools/runs` (python3 stdlib), `tools/test_runs.py` (gate step `unit`). Test: fixture copies of 3 run dirs (xrun PASS, xrun FAIL, probe report.txt). Verify: `python3 -m unittest tools/test_runs.py && tools/runs stats --form 'w3a-n3' | tail -1`.

## risks
Read-only, no device.

## amendments
- 2026-10-24 driver: status approved (user approved building, 2026-10-24).
