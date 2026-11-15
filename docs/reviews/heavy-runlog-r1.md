# Review heavy-runlog-r1: kernel 7958585 (tools/heavy-job appends its RESULT line to run.log), base c504db5

Reviewer: Claude Sonnet 5.5, adversarial. Tags: [obs] = observed by running it now, [src] = read from source (file:line), [guess].
Scratch harness (env overrides as in tools/test_heavy_job.sh, no real host state) in /tmp/hjrev; code under review not edited.

## Verdict: SAFE WITH CONDITIONS

The change does what it says on the main paths: PASS/FAIL append and the ERROR append after the log dir exists (tools/heavy-job:56,147), the
callers are unaffected, shellcheck is clean, the 4 new rows bind (3 distinct mutants killed). Three real defects remain that make "run.log
ends with the verdict" false in reachable cases (F1 medium, F2 medium, F3 low); F2 is the one that can write a WRONG verdict into a log.

## Findings

### F1 (medium) A second invocation with the SAME label appends its ERROR into the first run's live run.log
tools/heavy-job:127 sets `rlog` right after the log dir exists, before the memory gate (:129) and the lock (:130-133). Every pre-lock refusal
(memory gate :129, lock timeout :133, usage-after-mkdir none) therefore writes `RESULT: ERROR ...` into the run.log of whatever job currently
owns that label. Fixed labels are the normal case: tools/xnu-local-build:28 (`xnu-local-build`), tools/check-all.sh:24 (`gate-$(basename $PWD)`),
tools/remote-job:371 (`rj-JOB-<lab-host>`).
[obs] 2 concurrent runs of label `same`, B with --wait 1: A's run.log became
`A-start / RESULT: ERROR heavy-job lock held by: same gate 819635 ... (waited 1s) / A-end / RESULT: PASS label=same ...`. Mid-run (before A ends) the
LAST line of A's run.log is B's ERROR: exactly the false verdict the order R22 wants to remove. (Before the change nothing was ever written by B.)
Fix: only append when this process owns the lock. In cmd_run keep `rlog=` empty until after the `printf ... >"$lock"` at :135 (set `rlog=$ld/run.log`
there) and print the "heavy-job: log" line as now. Pre-lock ERRORs then go to stdout only, as before. Then change the new host row
"run.log ends with RESULT: ERROR memory-gate when the gate refuses" (tools/test_heavy_job.sh, row after `load6`) to a post-lock refusal (the
`memgate "before exec"` err at :138) or keep the load6 row but assert the OLD behaviour (run.log absent/unchanged) and add a row that runs two
same-label jobs and asserts A's run.log ends with A's PASS and contains no ERROR. Alternative if a pre-lock verdict in run.log is wanted:
append only when `[ "$(cut -d' ' -f1 "$lock" 2>/dev/null)" != "$label" ]` (holder is another label), but that has a check-then-act race; the first fix is simpler.

### F2 (medium) `err` inside `$(sample; stamp)` (line 138) appends a stray `RESULT: ERROR` to run.log while the job continues
tools/heavy-job:136 `out "heavy-job: lock acquired ... $(sample; stamp)"` runs `sample` in a command substitution; `err` at :61 then `exit 2`s only the subshell.
Pre-existing: the ERROR text is swallowed into the "lock acquired" line and the run goes on. New with this change: err() also appends it to run.log
(:56, rlog is inherited by the subshell), so run.log gets a `RESULT: ERROR` line that no process owns; if the job then proceeds (a transient
unreadable meminfo, `memgate "before exec"` samples again at :138 and succeeds) run.log has a mid-file ERROR followed by the real PASS.
[obs] meminfo removed while waiting for the lock: stdout shows `heavy-job: lock acquired label=sub class=gate RESULT: ERROR memory-gate: no MemAvailable...` then
the real `RESULT: ERROR`, exit 2; run.log has TWO identical `RESULT: ERROR` lines. The transient variant was not reproduced (timing) [guess: same mechanism].
Fix: remove the substitution: `sample; out "heavy-job: lock acquired label=$label class=$class $(stamp)"` (stamp has no err path). This also repairs the swallowed
stdout error (AP-102 / rule 8).

### F3 (low-medium) A wrapped command that leaves its last line unterminated merges the RESULT into it; a background child can write after it
(a) tools/heavy-job:141-147: `tee` copies bytes as-is, then `echo "$res" >>"$rlog"`. [obs] `-- bash -c 'printf partial'` gives run.log last line
`partialRESULT: PASS label=nonl class=gate rc=0 elapsed=0s`; `grep -c '^RESULT:' run.log` = 0. (stdout has the same merge, pre-existing; the new
requirement "run.log ends with the RESULT line" fails for it.)
Fix before the append (both in cmd_run and err): `[ -s "$rlog" ] && [ -n "$(tail -c1 "$rlog")" ] && echo >>"$rlog"` (tail -c1 of a newline expands to empty).
(b) Requirement 2: the stderr process substitution `2> >(tee -a "$ld/run.log" >&2)` (:141) is not waited for by the pipeline (the pipeline waits only for the
stdout tee). A command that leaves a background child holding only stderr writes after the RESULT: [obs]
`-- bash -c '(sleep 1; echo LATE-STDERR >&2) >/dev/null & echo main'` gives run.log `main / RESULT: PASS ... / LATE-STDERR`.
Plain "write to stderr then exit" did NOT race: [obs] 0 bad of 100 idle, 0 of 100 with 8 busy loops, 0 of 150 with 6 busy loops pinned to the same CPU and 60000 stderr bytes
just before exit (race.sh/errcmd.sh in /tmp/hjrev). So the race is possible in principle [guess] but not demonstrated for ordinary commands.
Fix (optional, only the child case): `wait` after `rc=${PIPESTATUS[0]}` (:142) in a bash-5.2 test showed it waits for the procsub [obs /tmp/hjrev/w.sh]. Caveat: a daemon that keeps stderr open would then hold
the heavy lock; so either accept and document "a command must not outlive heavy-job holding stderr", or skip the wait. I recommend document-only.

### F4 (info) No RESULT line is written when heavy-job itself is killed
[src] tools/heavy-job has no trap in cmd_run (:111-150) (grep -n trap: only :268 <offload-host>, :316 <offload-host> wait). SIGTERM/SIGKILL/timeout of heavy-job leaves run.log without a verdict
(and stdout too); not part of this change. A run.log without RESULT therefore still means "killed or still running". Document this in the header (one sentence) so a reader does not read it as a missing append.

### F5 (info) Paths checked and fine (requirement 1)
- Nested path :121-124 (`HEAVY_JOB_HELD` set): `exec "$@"` before the log dir exists; rlog empty; no RESULT printed, nothing to append. OK [src].
- Usage errors :116-120 and mkdir failure :126: rlog empty at that point (initialised at :55, so no set -u failure) [src]; OK.
- `err` from `sample` called directly by `memgate` (:129,:138): main shell, `exit 2` is real, rlog set, appended. OK [src + obs row `load6`].
- `status` :cmd_status (:103) and `detach` (:330-): rlog never set, behaviour unchanged. `detach` prints its RESULT to stdout only; detach.log holds the start and DETACHED-END lines, no RESULT. Not run.log, and a launch verdict in detach.log would blur the job's own DETACHED-END; leave as is, but the header should not imply it (it names only run.log: fine).
- `<offload-host> run`: uses its own `sout/sres/serr` which already write the RESULT to `logs/heavy-job/<offload-host>/<label>.log` (:262-268, sres); unchanged, unaffected by rlog. Same-label concern as F1 applies there too (serr after slog set) but is pre-existing and out of this diff.
- Append failure (disk full/read-only): PASS path warns on stderr (:147) and keeps the exit code; err() path prints bash's own redirection error and still exits 2. Acceptable (no silent success).

### F6 (info) Callers (requirement 3): no regression
stdout/stderr of heavy-job is byte-identical: only added statements write the file (:56, :146-147), `out` calls are unchanged except `res=$(res_line ...)` then `out "$res"` (same text) [src diff].
Callers: tools/gate-xnu-build:51-52 greps its own `$log` (xnu-local-build's captured output) for `^RESULT: ` and `^heavy-job: RESULT `, not run.log; tools/check-all.sh:21-24, tools/xnu-local-build:28,
tools/remote-job:346,371 only exec heavy-job. `grep -rIn 'logs/heavy-job\|run\.log'` over tools/ and docs/ finds no reader of run.log other than humans (HANDOFF cites `run.log:3` of a GATE line, still line 3, an append at the end cannot move it) [obs].
Side effect to note: run.log of check-all/xnu-local-build jobs now ends with heavy-job's `RESULT` (QUIET: `heavy-job: RESULT ...`) after the inner `RESULT:` line; a human `tail -1` sees the outer verdict, as intended.

### F7 (info) Host-test rows bind; 3 distinct mutants killed (requirement 4)
`bash tools/test_heavy_job.sh` in the worktree: [obs] `RESULT: PASS cases=99`.
Mutants (copies of tools/ under /tmp/hjrev/m1..m4, the worktree untouched), each run through the full test:
- M1 skip the append in the PASS path (delete :147): FAIL rows "run.log last line == printed RESULT line (PASS)", "(FAIL rc=7)", "QUIET ..."; 3 of the 4 new rows.
- M2 skip the append in err() (drop `[ -z "$rlog" ] || echo "$l" >>"$rlog";`): FAIL row "run.log ends with RESULT: ERROR memory-gate ...".
- M3 write a different line (`heavy-job: finished rc=$rc`): FAIL PASS, FAIL rc=7 and QUIET rows.
- M4 always write the non-QUIET form (`RESULT: ${res#RESULT: }`): FAIL only the QUIET row.
The extra FAIL "check-all.sh nested prints the nested line and does not lock" appears in all four mutant copies including M4 and not in the real worktree: an artifact of running the test from a copy in /tmp (check-all.sh needs the checkout), not of the mutations.
Row construction is sound: `A && has ... || echo 0` yields 0 when either side fails; an empty last line vs an empty printed line cannot pass (the `has` pattern fails). Gaps (not covered by any row): F1 (same-label pollution), F3a (unterminated last line), post-lock ERROR (lock timeout / `before exec`), `err` in the `$(...)` (F2).
Add rows for F1 and F3a with the fixes above; each is a 3-line row.

### F8 (info) shellcheck (requirement 5)
`shellcheck -S warning tools/heavy-job tools/test_heavy_job.sh`: no findings, rc 0 [obs].

## Required before merge
F1 (move the rlog assignment after the lock is held, plus a same-label row) and F2 (drop the `$(sample; stamp)` substitution). F3a (newline guard) strongly recommended with a row. F3b, F4: document.

## Status (driver 20u)
F1 (rlog set only after the lock is held), F2 (sample not in a subshell), F3a (newline guard) fixed in this commit; F3b and F4 documented in the tool header. Host test RESULT: PASS cases=104; the reviewed version 7958585 fails 4 of the new rows (mutant check).
