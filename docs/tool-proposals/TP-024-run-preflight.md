---
id: TP-024
tool: "tools/run-preflight"
status: approved
proposed_by: "Claude Sonnet 5.5 (implementer, from decisions-s36.md)"
date: 2026-10-24
exposed_by: "AP-10, AP-13, AP-14, AP-21, AP-22, AP-27: each a hand-built run form that was wrong before reaching the phone (decisions-s36.md:76-87, tools-audit.md:134)"
extends: ""
related: "TP-015, TP-023, TP-028"
usage: "tools/run-preflight [--json] -- tools/xrun|tools/probe-run ARGS..."
refs: "-"
---

## checked
decisions-s36.md section T2 (lines 73-92). Depends on TP-023 (`tools/runs stats` for check 4) and should be built after TP-015 (tools-audit.md:140: TP-015 fixes the verdict that TP-024 only predicts). Check 5 reads `shim/out/gate.result` from TP-028 (resolves U1).

## why
Run forms are typed from handoffs and checked by nobody before the phone is taken. Eight checks, each with a test from its motivating antipattern: (1) every `--args` boot-arg name is parsed in xnu-8796 + patches (generated list `tools/bootargs-known.txt`; the grep is a start, not proof); (2) `sweettouchpoke=` ends in `;` (AP-10); (3) every `--expect` reachable before each accepted `--stop-on` row (AP-27); (4) `--timeout` >= max previous same-form duration + margin (AP-14/AP-22); (5) newest `GATE PASS <hash>` = HEAD (AP-21); (6) image meta matches HEAD (`tools/imgmeta.py`); (7) flashed root in `~/pixelios-artifacts/flashed-userdata.txt` is the expected one; (8) `--overlay` only after migration, phone lock free or the caller's.

## output
`RESULT: PASS` or `RESULT: FAIL <check>: <reason>`; exit 0 pass, 1 a check failed, 2 bad args/internal error. Ship standalone first; wiring into xrun/probe-run before the lock is taken is a later change. Files: `tools/run-preflight`, `tools/bootargs-known.txt` + generator, `tools/test_run_preflight.py`. Cases: AP-10 string, AP-27 form, the n9/n10 too-short-timeout runs, stale gate hash, mismatched root. Verify: `python3 -m unittest tools/test_run_preflight.py && tools/run-preflight -- tools/xrun --kernel K --args \"sweettouchpoke=1\"; echo $?` (expect FAIL, exit 1).

## risks
Read-only, no device. Review: Sonnet 5.5 adversarial. Build order: TP-015, TP-023, then this.

## amendments
- 2026-10-24 driver: status approved (user approved building, 2026-10-24). Depends on TP-015 and TP-023.
