---
id: TP-028
tool: "tools/check-all.sh (gate speed + reliability)"
status: approved
proposed_by: "Claude Opus 5.5 (delegate, researcher)"
date: 2026-10-24
exposed_by: "integration/0124 full gate ~75 min (driver obs); one shim build = ~205 s of which ~178 s is the boot audit run twice (docs/research/gate-speed.md 1.2)"
extends: "TP-003"
related: "TP-003, TP-023, TP-024"
usage: "tools/check-all.sh [--quick] [--keep-going] [--no-memo] [-j N]"
refs: "-"
---

## checked
`tools/tool-proposals check gate|cache|audit|check-all` (integration worktree): gate -> exists tools/gate_select.py, check-all.sh;
cache -> only TP-013 (mg-key, unrelated); audit -> none; check-all -> TP-003 (`--quick`, done). TP-003 is done, so this is a new file
with `extends: TP-003` (TOOL-PROPOSALS.md rule 2). No existing flag dedupes the audit, memoizes self-tests, parallelizes builds or
writes progress/result files.

## why
Measured (super `docs/research/gate-speed.md` section 1): per build 5.5 s compile, 19 s host tests, 2 x ~89 s audit; 35 audits per
gate; ~83 of the 89 s are three self-tests whose inputs do not change between variants; the raw and lz4 audits compute the same tree
audit. Expected full gate ~60 min -> ~10 min (R1+R2), ~5 min (+R3), ~2.5 min (+R4). A failing lane B today still waits for all of
lane A (up to ~60 min). `gate.log` is empty until the end; no step has a timeout; quick gate misses host tests for patches 0093/0104.

## output
Unchanged stdout line `GATE PASS|FAIL <hash>[+dirty] [quick: ...]`. New: `shim/out/gate.progress` (`START`/`END` lines, live),
`shim/out/gate.result` (one line, atomic, `RUNNING` while running), per-step `FAIL <step> (timeout)`. Exit 0 pass / 1 fail / 2 usage
or setup error. Memo entries in `${XDG_CACHE_HOME:-~/.cache}/pixelios/audit/`, host-test stamps in `.../host-tests/`.

## risks
No device. Touches the safety audit's control flow (not its checks): the memo must provably not skip a changed input (key = audit
script sha + function + argument bytes + Python version + sha of every file the function opened; subprocess inside = not stored;
only PASS stored) and must pass the mutation check (gate-speed.md section 3) before merge; Opus review. `build.sh --out` must leave
the default `shim/out` images byte-identical for xrun/probe-run/fboot. Never loosen `tools/test-bluejay-boot.py` checks.

## amendments
(none)

---

# Implementer brief (paste; driver adds the standard "Work alone ..." header)

Base: `~/.t3/worktrees/integration` (integration/0124). Read super `docs/research/gate-speed.md` sections 2-3 (R1-R8) first.
Ship as separate commits in this order; quick-gate each.

Requirements
1. R7 reliability (tools/check-all.sh): `gate.progress` START/END lines per step, written live; `gate.result` atomic (temp + mv),
   `RUNNING <pid> <epoch>` at start, final `GATE PASS|FAIL <hash> <epoch>`; per-step `timeout -k 10 BUDGET` (BUDGET flag/env,
   default 900 s builds, 300 s others; timeout = step FAIL `(timeout)`); `+dirty` when `git status --porcelain` is non-empty;
   lock owner written to `shim/out/.build.owner` and one `waiting for lock held by ...` progress line; `quick: no usable stamp, ran
   full` when gate_select returns FULL.
2. R5: any lane's failure stops both lanes at the next step boundary (`$tmp/*.fail`); `--keep-going` restores today's behaviour;
   lane B runs validate and public-split first.
3. R6 (tools/gate_select.py): the xnu patch prefixes come from `shim/build.sh` (`../xnu/patches/NNNN` references) instead of the
   literal 0087/0088; `shim/*.md` selects no build. Update `docs/AUTOMATION.md:172`. Extend `tools/test_gate_select.py`.
4. R1 (tools/test-bluejay-boot.py, shim/build.sh:519-520): the boot_image argument takes one or more images; header checks
   (current lines 5068-5079) per image, the tree audit (5081-5257) once, one `PASS header ...` line per image. No check text changes.
5. R2: new `tools/audit_memo.py` (stdlib only) with the exact key of gate-speed.md R2 (incl. `sys.addaudithook` dependency recording
   and the subprocess refusal); wrap `wlan_seq_selftest`, `wlanlink_source_selftest`, `wlanlink_object_selftest` (refactored to take
   the disasm text; objdump stays outside). Replay captured stdout with `(memo <key16>)`. `--no-memo` / `PIXELIOS_AUDIT_NO_MEMO=1`.
   Unit test `tools/test_audit_memo.py`; mutation script `tools/test_audit_memo_mutations.sh` per gate-speed.md section 3.
6. R3 (shim/build.sh): host-test block (lines 412-486 and 506-517, not the variant `nm` checks 487-505) skipped when
   `host-tests/<digest>.ok` exists; digest over shim/*.c shim/*.h out/board_config.h out/wlan_seq.h the referenced xnu patches,
   `gcc -v`, build.sh; stamp written only after all passed.
7. R4: `shim/build.sh --out DIR` (default `out`) and `test-bluejay-boot.py --out DIR`; check-all runs `build` first, then the other
   variants with `-j N` (default 8) into `shim/out/gate/<step>-<i>/`; the gate then takes no `.build.lock`.
8. R8: `tools/hang-recover` self-test asserts the stub poll count, not `after [01]s` (line 83).
9. Never change what a check in test-bluejay-boot.py accepts or rejects; `git diff` of that file may only move code into functions,
   add arguments, and add memo wrappers.

Files: `tools/check-all.sh`, `tools/gate_select.py`, `tools/test_gate_select.py`, `tools/test-bluejay-boot.py`, `tools/audit_memo.py`
(new), `tools/test_audit_memo.py` (new), `tools/test_audit_memo_mutations.sh` (new), `shim/build.sh`, `tools/hang-recover`,
`docs/AUTOMATION.md`, `tools/public-manifest.json` (owners of the new files, AP-12), `docs/TOOL-PROPOSALS.md` index line.

Verification (implementer runs the cheap ones; the driver runs the gate):
`python3 -m unittest tools/test_gate_select.py tools/test_audit_memo.py && tools/test_audit_memo_mutations.sh; echo rc=$?`
must end `RESULT: PASS mutants=<n> caught=<n>` and `rc=0`. Then the driver: `tools/check-all.sh > /tmp/g.log; grep '^GATE PASS' /tmp/g.log`
twice (cold, warm) and compares the per-step table in `shim/out/gate.progress`; `cmp` of `shim/out/shim-boot-avb.img` built by
`shim/build.sh --board bluejay --avb` before and after R4.

RESULT / exit contract
- `tools/check-all.sh`: stdout one line as today; `gate.result` last line identical to it; exit 0 PASS, 1 FAIL (incl. timeout),
  2 usage/setup (bad flag, not a git checkout, missing third_party).
- `tools/test_audit_memo_mutations.sh`: last line `RESULT: PASS mutants=N caught=N` (exit 0) / `RESULT: FAIL survived=<name,...>`
  (exit 1) / `RESULT: ERROR <setup step>` (exit 2); log dir `logs/audit-memo-mut/<UTC>/`.

# Build order (decisions-s36.md "Procedure")
Recommend **before** T1 `tools/runs` (TP-023), at least items 1-4 (R7, R5, R6, R1): they cost ~1 implementer session
[guess ~40k tokens] and save ~25 min of every full gate plus up to ~60 min of every failing one, and T1/T2 themselves are gated
(`unit` step) [src super docs/research/decisions-s36.md "Procedure" steps 2-3]. Items 5-7 (R2 memo with Opus review, R3, R4)
go right after T1, before T2 `run-preflight`; T2 check 5 then reads `shim/out/gate.result` instead of grepping a log
(resolves decisions-s36.md U1). Item 8 any time.
