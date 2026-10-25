---
id: TP-023
tool: "tools/runs"
status: done
proposed_by: "Claude Sonnet 5.5 (implementer)"
date: 2026-10-24
exposed_by: "session 36: AP-14/AP-22 (a run called a hang without comparing against earlier durations of the same form); decisions-s36.md T1"
extends: ""
related: "TP-024"
usage: "tools/runs list [--since D] [--kind xrun|probe] [--all-worktrees] | show ID | stats --form REGEX | compare A B"
refs: "docs/research/decisions-s36.md (T1), docs/research/dev-tooling-gaps.md:52, tools/runs, tools/test_runs.py"
---

## checked
`tools/tool-proposals check runs` (only TP-005, CI runs, unrelated). Read from source: xrun dirs `logs/runs/<UTC stamp>-<label>/` hold `report.json`, `run.json`, `report.txt`; probe-run dirs `logs/probe/<stamp>-<label>/` hold `report.txt`, `usb.txt`, `fboot.log`, `usb.times` and no `report.json` (observed on `ls` of real dirs, 2026-10-24).

## why
Whether a run is a hang or only a too-short timeout (AP-14, AP-22) needs the wall time of earlier runs of the same form; today it is read by hand from `stat` times. `runs stats --form RX` gives p50/max wall time and PASS/FAIL/HANG/ERROR counts per label stem; `tools/run-preflight` (TP-024) reads it for its timeout check.

## output
A table on stdout, last line `RESULT: PASS n=<dirs> unreadable=<k>`; exit 0, or `RESULT: PARTIAL ...` and exit 1 when a dir is unreadable (listed above the RESULT line; e.g. xrun `demo1` has no report.json, probe `w3a-n28b_` no report.txt), exit 2 on bad arguments. Wall time: xrun = run.json `t_end - t_start`; probe = mtime(usb.txt) - mtime(fboot.log) (boot to last usb line, the TP-015 metric; guess: usb.times uses a monotonic clock, so it is not used). `stats --json` for tools. `--all-worktrees` scans every `git worktree list` path.

## risks
Read-only, host-only; no adb/fastboot. Probe wall time depends on file mtimes (a copied dir without preserved mtimes shows wrong or no duration).

## amendments
(none)
