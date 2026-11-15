---
id: TP-047
tool: "tools/unit-hermetic"
status: proposed
proposed_by: "claude-opus-5-5 (driver 20k)"
date: 2026-10-29
exposed_by: "AP-213 (21i report to driver 20j, 2026-10-29): HOME-hermetic unit proof ran the wlan mutants cold, el2b2-unit 2272 s, 0181-unit 45+ min, gate-batch-20j timed out behind it"
extends: ""
related: "TP-043"
usage: "tools/unit-hermetic [--label L] [-k PATTERN] (runs the AP-210 HOME-hermetic unit proof through tools/heavy-job with the warm mutant cache)"
refs: "-"
---

## checked
`tools/tool-proposals check hermetic` -> EXISTS tools/test_hermetic_inputs.py, a unit test that checks inputs are hermetic, not a runner. `check GATE_MUT_CACHE` -> NONE. tools/remote-job:71 sets GATE_MUT_CACHE for its own jobs only; the gate's unit step uses the host cache. The AP-210 proof (unit tests with HOME=/nonexistent-home, before a merge) is a hand-typed command in ANTIPATTERNS AP-213, copied into each stream's notes.
**Caller:** the drivers' AP-210 HOME-hermetic unit proof before a merge gate (docs/ANTIPATTERNS.md AP-210/AP-213 "Do instead", super docs/WORKFLOW.md Commands once built). **Replaces:** the hand-typed `env -u HEAVY_JOB_HELD HOME=/nonexistent-home ... python3 -m unittest discover` line (not a tool).

## why
[obs] Typed by hand, the proof sets HOME=/nonexistent-home first, so tools/wlan_mutant_cache.py:21 (DEFAULT_DIR under ~) resolves to a missing dir and every wlan mutant runs cold: el2b2-unit 2272 s, 0181-unit 45+ min, both holding heavy-job and so stalling every other local heavy job (20j's gate-batch-20j timed out after 1800 s behind it). AP-213's fix is one env var in the right order, which every stream has to remember. With a warm cache the same proof takes about as long as gate step unit [guess: measure on the first run, record in refs]. Cost removed: ~35-45 min of heavy-job hold per proof, several per day.

## output
One command: resolves the cache path from the REAL home first (`GATE_MUT_CACHE` if set, else ~/.cache/pixelios/wlan-mutants), then runs `env -u HEAVY_JOB_HELD GATE_MUT_CACHE=<that> HOME=/nonexistent-home TMPDIR=<fresh mktemp -d> python3 -m unittest discover -s tools -p 'test_*.py'` under `tools/heavy-job --label <L, default unit-hermetic-<shorthash>>`. Prints the cache path and its entry count up front (count 0 = loud `UNIT GAP: cold mutant cache, expect 40+ min`). Last line `RESULT: PASS|FAIL label=L ran=N skipped=S failures=F elapsed=Ns log=<abs path>`; exit 0 PASS, 1 test failures, 2 usage/heavy-job busy. Log: heavy-job's log dir (main-clone logs/heavy-job/<label>/ after the AP-217 fix). Callers: super docs/WORKFLOW.md pre-merge checklist row (replaces the hand command in AP-213/AP-210).

## risks
Host-only. Must never write into the real ~/.cache other than through the mutant cache's own content-keyed entries (research 71: a hit returns the same verdict as a cold run). One heavy job at a time (heavy-job enforces). No device, no <offload-host>.

## amendments
(none)
