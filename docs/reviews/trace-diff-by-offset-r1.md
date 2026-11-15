# Review r1: trace-diff diff --by-offset (7269c97..ddc4558)

Verdict: SAFE WITH CONDITIONS (read-only tool, no hardware path; gaps are test strength and report visibility).

Observed: test_trace_diff.py ends OK. Without --by-offset, output is byte-identical to 7269c97 on the real golden/run for
plain, --reads and --collapse-polls --from/--to gpu-wake (cmp). The only non-flag change in cmd_diff is the `if a.by_offset` branch
after the compared=0 check (tools/trace-diff:539). Real command tail: `RESULT: FAIL by-offset keys=94 match=0 values=17 missing=77 extra=0 first_match=14 ignored=0`.
Read from source: grouping key uses -1 for win None (win is parsed hex, never negative, so no None/0 collision); precedence FAIL > PARTIAL > PASS
equals the non-offset path (:556-:560 vs by_offset tail); a.to ignored counts are printed and on RESULT.

Mutants run in /tmp/mt (copy; original untouched). Killed: MATCH flipped, ignore-run-only-key removed, first_match `bad != 0` flipped, 200-cap changed to 2.
SURVIVED: findings 1-3.

1. MEDIUM, tools/test_trace_diff.py:462-506: truncation `rv = rv[:len(gv)]` (tools/trace-diff:~428) can be deleted and the test still passes.
   No test has --to with a matched key whose run has more values. Input: golden key once, run key 3x, `--to B`: expected note `... and 2 run rows past`, ignored=2, PASS.
   Fix: add that case, assert `rc==0`, `"and 2 run rows past"` and `ignored=2`.
2. MEDIUM, same test: `ign = " ignored=%d" % (ign_rows + cut_rows)` mutated to ign_rows only still passes (same missing case as 1; rule 8 count on RESULT untested for cut rows). Fix: same test asserts `ignored=2`.
3. LOW, same test: win None vs 0 collision untested (-1 mutated to 0 passes). Fix: add run rows with the same addr in window `-` and `0x0` (if the golden CSV supports win 0) and assert two OFFSET lines.
4. MEDIUM, tools/trace-diff:~444 (`lines[:200]`): the cap is applied in addr order, not severity. With >200 keys, MATCH lines can push every VALUES/MISSING line off the output; only counts remain (observed: real run has 96 keys, so not hit yet; GPU runs with thousands of offsets will hit it). Rule 8 is met by the "... N more" line but the failures are not shown.
   Fix: print non-MATCH lines first (up to 200), then summarize MATCH as a count, or sort `lines` by (st == "MATCH") before slicing; update docstring ("sorted by addr then op, capped at 200") and the test.
5. LOW, tools/trace-diff docstring (line ~58 of the header): RESULT PASS form omits that `ignored=N` is appended with --to (PASS/PARTIAL/FAIL all do). Fix: write `[ignored=N with --to]` on all three RESULT lines. Also note with --from the run side is not range-limited (run rows before the mark show as EXTRA); same as the non-offset path, so document it.
6. INFO, tools/trace-diff:~440: for count-only R/CR keys golden val None prints `golden_val=-`; fine, and a length mismatch reports first_diff=min(len). Counts-only keys do count toward first_match (bad != 0 whenever lengths differ since min>=1). Consistent with the docstring.

Shellcheck: no shell files in the diff. No new antipattern.
