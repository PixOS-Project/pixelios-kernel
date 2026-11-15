# Host tool speed, 2026-11-11 (branch tools/tool-speed)

Goal (user 2026-11-11): make the slowest internal tools faster by fixing root causes. Every number is observed on this host; the
machine was never idle (load average 5 to 45 on 16 cores, other gates running), so wall times are noisy. Where the CPU time (user s)
is given it is the steadier figure. Tag: [obs] = measured here, [read] = read from source.

## Findings and fixes

| file / tool | wall before | cause (proof) | fix | wall after |
|---|---|---|---|---|
| gate-lint-first (via validate -> tool-census --check) | 118 s (load 11) | `tool-census` ran one regex per (tool, source text): 274k `re.search` calls = 58 of 65 s in cProfile [obs] | substring test before the regex (same match set) | 51 s (load 23); census alone 65 s -> 5 s; output identical to the old run [obs] |
| test_agent_guard.py | 236 s total (173 s `test_self_test_passes` + 62 s mutants, load 15-35) | the self-test replays all 578 fixtures twice per rule (54 rules x off/on = 108 full replays, 169 of 173 s); the mutant test runs the full self-test per mutant (cProfile of one run: 4.2 s) [obs] | (1) the toggle replay is a lazy generator, so a toggle mutant stops at its first mismatching fixture; (2) mutant runs set `AGENT_GUARD_FAIL_FAST=1` (stop at the first failing fixture; the verdict FAIL is the same because later checks only add failures) | 4 s + 21 s at load 14 [obs]; 115 s total at load 24-45 |
| test_heavy_job.py (part basic) | 119.5 s (load 5) | the case "second memory gate" waits the whole `--wait 60` budget by design (the gate re-polls memory until the budget ends; log shows `memory-gate not met (before exec)` after 60 s) [obs] | that one case uses `--wait 20` (budget only, the assertion is unchanged) | 85 s (load 19) |
| test_unit_select.py (and the real gate's unit selection, same module) | 92.5 s user / 109 s wall (load 27) | `PyInfo` walked each module's AST 5 times, re-walked taint expressions on every fixed-point round, re-opened files for `is_py` 160k times: 54 s in cProfile for one `select_tests([])` [obs] | one `ast.walk` per module, memoised walks, memoised `is_py` | 73 s user / 83 s wall (load 27); selection of 5 change sets identical to the old module [obs] |
| test_gate_unit_shards.py | 147 s (mutant test 81 s) | each of the 10 mutants ran the whole `OnlyTest` suite even after the first failure [read+obs] | `failfast=True` in the mutant runner | 99 s (mutant test 33 s) |
| test_audit_memo_mutations.py | 98 s | each cold audit does `copy.deepcopy` of the whole wlan_seq table per mutant (351 mutants): 27 of 42 profiled s [obs] | JSON round trip (the table came from `json.loads`); self-test output byte-identical | 61 s; cold audit 42 s -> 15 s profiled |
| gate `shellcheck` step | 37-99 s in gate logs; 41 s CPU for 115 files serial [obs] | one shellcheck process over all files | `tools/shell-files \| xargs -P 8 -n 8 shellcheck -S warning` (pinned in check-all.sh, test_shellcheck_step.py, docs/AUTOMATION.md) | 13.7 s wall at load 21 [obs] |

## Final re-run of every changed test file, same branch head, load 24-45 (other gates running)

agent_guard 115 s, heavy_job 217 s (one failed run: cpu_psi / load1 cases, see below) then 106 s OK at load 40, unit_select 211 s (load 44),
audit_memo_mutations 109 s (load 32). At this load the wall numbers are worse than the quiet-host "before" numbers, so they prove nothing
either way; use the CPU and profile figures in the first table (and re-measure on a quiet host). The one heavy_job failure
(`T-a8b4 cpu_psi 28.76 >= 20 REFUSES`, `T-ee35`, `load1 = 3 x nproc` cases, each `--wait 2`) did not repeat: the same part script from the
trunk tools and from this branch both passed those cases at load 35 in a copy, and the next full run passed; I did not change those cases.
I read it as the existing load sensitivity of the `--wait 2` cases (the T-c7be comments), not as a result of the `gate2` budget change.

## Findings not fixed (with the reason)

| file | wall | cause | why not / idea |
|---|---|---|---|
| test_xnu_remote_build.sh | 103 s | 102 cases, 46 runs of xnu-remote-build; strace: 58k execve (grep 1881, mkdir 1874, cat 1793, dirname 1462, git 940, env 1038) [obs] | no single hot spot; needs a bash-level rewrite of the tool's per-run bookkeeping. Idea: one shared fixture repo instead of `mkrepo` per case |
| test_check_all_stub.sh | 102-120 s | ~30 stub gate runs of check-all.sh, 69k execve (cat 3144, rm 2066, date 1829, ionice 1696, timeout 1670) + 158 x 0.1 s event polls [obs] | check-all.sh forks ionice+timeout+cat per step; reducing it changes the gate runner, a redesign |
| wlan-mutants step (311-1851 s) | | not profiled here: pool of mutant builds, outside the host-test scope of this branch | idea: cache mutant results keyed by input hash (docs/research/gate-speed.md has the memo design) |
| kernel build variants (118-245 s) | | build time, not a tool | none |
| test_heavy_job_basic remaining 85 s | | 12 cases wait their `--wait 2` budget to expire (25 s) and `late`/`samelab` wait `--wait 10` (20 s); the T-c7be comments explain why they were raised under load | shortening them risks flakes under load; idea: a test-only env that makes the budget expiry event driven |
| gate-unit-durations.tsv (drift=7) | | refreshing it needs a full unit run (writes shim/out/gate/unit-durations.new.tsv) at a quiet load, otherwise the order is measured under noise | the parent should copy the file after the next quiet full gate |

## Findings about test strength (not changed, for the parent to judge)

1. agent-guard `--self-test` toggle mutants (rule off/on) are not meaningful at trunk: replaying the fixtures a second time already
   fails 12 fixtures (`task_status A (A5)`, `gate PASS HEAD clean`, ...) because they carry state from the first pass, so every
   toggle mutant is "killed" by those, also with the toggle disabled [obs: with `mode = None` in `verdict()` the old code still
   printed `survived=0`]. This is the same at trunk and with this branch; the lazy replay only makes it cheap. Fixing it means
   resetting every fixture's state between passes (a real change to the self-test, 169 s again once it replays fully).
2. `test_agent_guard.test_code_mutants_are_killed`: a no-op mutant (`180_000,  200_000`) in a copied mutant dir still prints
   `RESULT: FAIL` (lock fixtures fail from the symlinked phone-lock path), with and without the fail-fast flag [obs]. So that
   test cannot tell a surviving mutant from a harness failure; same at trunk.

## Proof that the changed tests still catch a broken tool

* heavy-job: in a copy of tools/, line 316 `memgate "before exec" ||` replaced by `true ||`; `bash tools/test_heavy_job_basic.sh`
  prints `FAIL second memory gate (after the lock) ...` with `--wait 20`.
* test_gate_unit_shards: `ShardMutants.mutant_fails(old, new)` returns True for `MUTANTS[0]` and False for the no-op `(old, old)`.
* test-bluejay-boot wlan_seq self-test: a copy with `return` as the first statement of `check_wlan_seq_table` raises
  `AssertionError: wlan_seq self-test FAILED to catch: an extra write beyond a window`; the unmutated output (622 lines
  `self-test caught` / `PASS`) is byte-identical before and after the deepcopy change.
* tool-census: `tools/tool-census --check` output identical to the old tool (diff empty apart from the stray old copy).
* unit_select: `select_tests` for `[]`, agent-guard, heavy-job, boards.py, unit_select.py returns identical tuples with the old and new module.
* shellcheck step: `test_shellcheck_step.test_planted_bug_fails_the_step_command` runs the new command on a planted SC2164 and passes.
* agent-guard fail-fast: see "findings about test strength" 2; the self-test itself (`test_self_test_passes`) still runs every check.
