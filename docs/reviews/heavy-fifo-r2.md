# Review heavy-fifo-r2: delta d9d057e..a5b6492 (fixes for heavy-fifo-r1)

Reviewer: Sonnet 5.5, adversarial, no code edited. Scope: `git diff d9d057e a5b6492` (tools/heavy-job, tools/test_heavy_job.sh, tools/gate-xnu-build comment).
Tags: [obs] observed in this review (command run, scratch dirs under /tmp, bash 5.2.37, util-linux flock 2.41.5), [src] read from source (file:line, worktree a5b6492), [guess].

## Verdict: SAFE (merge-able); 7 follow-ups, none blocks the lane

No deadlock, no leaked queue lock, no false "lock free" found. Verified [obs]: `bash tools/test_heavy_job.sh` -> `RESULT: PASS cases=131`;
`shellcheck -S warning tools/heavy-job tools/test_heavy_job.sh` -> no findings, exit 0.

## Answers to the numbered focus points

1. **.seq / numbering.** The whole read-compute-write is inside ONE held flock: fd 8 opened [src heavy-job:246], flock acquired by a background `flock -w` on the SAME open file
   description (the lock belongs to the OFD, the parent keeps it after the child exits) [src :249-251], released only by `exec 8>&-` at the end of stake [src :269] or by process exit.
   Sweep (:252), max (:254), read (:255), decide (:256-262), tmp+mv write (:264), ticket creation (:266-268) all sit between those two lines. Children that could inherit fd 8 in
   that window are only `$(...)`/`( flock -n 3 ...)` subshells that end inside the window; CMD, both tee, the swait flock (`8>&-` :285) and the memgate sleep run after :269.
   [obs] 60 concurrent `heavy-job run -- true` (and 25 across the 999999->1000000 boundary): 0 duplicate numbers, all PASS, `.seq` = 60, queue dir left with only `.lock .seq`, no stray flock process.
   Reuse: n = max(.seq, highest EXISTING ticket)+1 and an existing-but-unsweepable ticket (e.g. unreadable) is still counted by `stickets`, so a number cannot be reused while its file exists [src :252-262].
   Sort: every ticket enumeration goes through `stickets` (numeric `sort -n`, [src :241]); consumers: swait_turn :276, stake :252/:254, qposmsg :238, sstate :349, offload_wait :379 (`tail -n 1` = highest numeric). No raw glob left (`grep -n 'stickets\|\[0-9\]' tools/heavy-job`). Boundary [obs] 999996..999999,1000000.. in order.
   Dotfiles `.lock .seq .seq.tmp` are not matched by `[0-9]*.*` (leading dot).
2. **Bounded ticket lock.** Background `flock -w REM 8 9>&- & fp=$!` then `wait "$fp"` [src :249-250]. A trapped signal interrupts `wait` and sabort kills `$fp` [src :193]; [obs] TERM sent twice
   in a row while stake waited on a held `.lock`: exit 143, one RESULT line, no `flock -w` process left, no ticket. SIGKILL of the parent leaves the child at most REM seconds (it then
   exits and the OFD lock dies with it) [guess from flock semantics; not run]. Timeout vs acquired: flock rc 1 on timeout, `[ "$frc" = 0 ]` else serr [src :251]. Budget: rem = wait - elapsed, clamped to 0
   [src :248]; [obs] `flock -w 0` is a non-blocking try (rc 0 on a free lock, rc 1 on a held one), so rem=0 cannot hang or mean "forever". Charging is real but practically a no-op: t0 is set one
   statement before stake in both callers [src :143, :325], so rem==wait (mutant "use $wait" is equivalent, see F6).
3. **Abort handlers.** `sabort SIG` removes `${ticket##*/}`-derived exact name via srelease (`rm -f "$ticket"`, then `ticket=`) [src :189-194]; ticket is assigned before the file exists, so a signal in that gap removes nothing
   (the name is always above every existing number, under the queue lock, so it can never name someone else's file). Run twice: [obs] two TERMs back to back print ONE RESULT line (second handler not re-run / ticket already empty
   anyway). Post-lock abort [obs]: HUP/TERM in the "before exec" memory gate -> exit 143, `RESULT: FAIL heavy-queue label=pl aborted by SIGTERM (ticket 000002.pl removed)`, appended to run.log, holder text cleared, ticket gone, `sleep` child gone.
   TERM during CMD: handlers are cleared at :168, default action; [obs] bash dies with 143, EXIT trap removes the ticket, **CMD keeps running and keeps the real lock (fd 7)**; the next job passed the queue and waited 5 s on the plain flock (correct, safe),
   but no RESULT line is printed and run.log/holder text are not finalised (F2). Nested (`HEAVY_JOB_HELD`) exits before any ticket/trap [src :139-142]; `<offload-host> wait` now prints `RESULT: FAIL offload-queue aborted by SIGTERM` instead of silence (no production caller of `<offload-host> wait`: grep tools/ docs/ .claude, only tests).
4. **F3 loop.** The `continue` is taken only when `[ ! -e "$pred" ]`; `stickets` lists only existing files, so the next listing no longer contains it (no sleep needed, it converges). Unopenable-but-present -> serr exit 2 immediately [src :278-281]; [obs] row passes as non-root; as root `exec 6<` always succeeds so the case cannot occur (row skipped as root: acceptable, the gate user is <offload-host> uid 1000). Same bug remains in offload_wait (F1).
5. **F5.** Old default `$HOME/.cache/pixelios/heavy-queue`; new `$(dirname "$HOME/.cache/pixelios/heavy-job.lock")/heavy-queue` = the same string/dir [src :57]. `grep -rn heavy-queue tools/ docs/` (worktree + super): only heavy-job itself (error prefix `qn=heavy-queue`) and its test; nothing hardcodes the directory. `HEAVY_JOB_LOCK` is set only by remote-job (reads the holder file, `tools/remote-job:363`) and tests. See F5 for the relative-path corner.
6. **Mutants (run in temp copies of tools/, real tree untouched).** The unmutated copy fails exactly one row (`check-all.sh nested ...`), which is the known copy artefact from r1; ignore it below.
   - A: `stickets` without `sort -n` -> KILLED by `F8: tickets 999999.lh 1000000.lB 1000001.lC ...` [obs].
   - E: drop `[ "$n" -ge "$m" ] || n=$m` (live-ticket floor) -> KILLED by 5 F1 rows (deleted, garbled, too long, empty, behind) [obs].
   - D: `[ -e "$pred" ] || continue` -> bare `continue` (the old spin) -> KILLED by the F3 row (15.0 s timeout) [obs; non-root only].
   - B: drop `rlog_add "$l"; : >"$lock"` in sabort (post-lock abort bookkeeping) -> SURVIVES (F3 below). [obs] (the extra SIGHUP FAIL in my B/D/E runs is an artefact of running them under `nohup`, which makes SIGHUP ignored; see AP-272.)
   - Reasoned survivors (not run): INT exit code 130 (acknowledged untestable), `.seq.tmp`+mv replaced by direct echo (crash window not testable), `flock -w "$wait"` instead of `"$rem"` (equivalent, F6).

## Findings

### F1 (Low) offload_wait still spins on an unopenable ticket, ignoring --wait
tools/heavy-job:379-380: `f=$(stickets | tail -n 1)` ... `{ exec 6<"$f"; } 2>/dev/null || continue` is the exact pattern r1 F3 fixed in swait_turn, but not here.
[obs] non-root, `OFFLOAD_QUEUE_DIR` with `000007.unread` mode 000: `heavy-job <offload-host> wait --wait 3` ran until `timeout 8` killed it (rc 124), 100 percent CPU loop, no output. No production caller (only tests), so Low.
Fix: in offload_wait replace the `|| continue` by `|| { [ -e "$f" ] || continue; serr "offload-queue: cannot open ticket ${f##*/} (permissions?)"; }` and add a row mirroring the F3 row (skip as root) for `<offload-host> wait`.

### F2 (Low) signal during CMD in `run` leaves an orphan CMD and no RESULT line
tools/heavy-job:168 (`trap - INT TERM HUP`), header :54 says "aborted by signal ... while waiting" so this is documented only by that word.
[obs] `kill -TERM` of `heavy-job run -- sleep 6` during CMD: rc 143, stdout ends at "memory-gate ok (before exec)" (no RESULT line, contradicts the "tools end in a RESULT line" rule and the W34 R22 "run.log ends with the verdict" promise), run.log not finalised, holder text `cmdterm analysis PID ...` left in the lock file, CMD (`sleep 6`) still running and still holding fd 7 (the successor then waited on the plain flock, correct). Safe for mutual exclusion; the gap is only the missing verdict/holder cleanup.
Fix: document it in the header (extend line 54: "a signal DURING CMD is not trapped: heavy-job dies by the default action, CMD keeps running and keeps the lock until it ends, no RESULT line"). A trap during CMD is not an option: bash defers it until the foreground pipeline ends (comment at :168), and forwarding the signal to CMD would change who stops the lock holder.

### F3 (Low) post-lock abort branch of sabort has no test
tools/heavy-job:196 (`[ -z "$rlog" ] || { rlog_add "$l"; : >"$lock"; }`). Mutant B (branch removed) passes all 131 rows [obs]. The branch itself works [obs: run.log got the FAIL line, lock text cleared, ticket and sleep child gone].
Fix: a row in tools/test_heavy_job.sh: start a job whose first gate passes and whose `HEAVY_JOB_MEMINFO` file is swapped to the small one as soon as the output contains `lock acquired`, SIGTERM it once `waiting (before exec)` appears, expect exit 143, last line of `logs/heavy-job/LABEL/run.log` == the printed `RESULT: FAIL ... aborted by SIGTERM`, `[ ! -s "$w/lock/heavy.lock" ]`, no ticket.

### F4 (Info) INT path (exit 130) is untested
Acknowledged by the implementer (non-interactive bash ignores SIGINT from a background job). The mapping line is one assignment at :192; a mutant swapping 130/129 survives. Optional: `setsid` the job with SIGINT unblocked via `python3 -c 'signal.signal(SIGINT,SIG_DFL); os.execvp(...)'` or a `bash -i` wrapper.

### F5 (Low) relative HEAVY_JOB_LOCK without a slash makes the queue cwd-dependent
tools/heavy-job:57. `dirname heavy-job.lock` is `.`, so the queue becomes `./heavy-queue` relative to the caller's cwd (the lock file is also cwd-relative, but tickets would now be created in whatever directory the caller sits in, e.g. a git checkout).
No caller does this today [obs grep]; production default unchanged. Fix: after the `lock=` assignment add `case $lock in /*) ;; *) lock=$PWD/$lock ;; esac` (before `hq=`), or document "HEAVY_JOB_LOCK must be absolute".

### F6 (Info) two mutants of the new stake code are equivalent
`flock -w "$rem"` vs `"$wait"` (t0 is set immediately before stake, rem==wait) and tmp+mv vs direct write behave identically in every host test. Not a defect; do not spend a row on them. If the budget charge matters later (e.g. a slow `logroot` before stake), a row needs a `HEAVY_JOB_*` hook to delay stake.

### F7 (Info) a dead, unreadable ticket of another user is never swept
tools/heavy-job:252: the sweep needs `3<"$f"` to open the ticket, so a dead 0600 ticket owned by another uid stays; with the F3 fix every run of a different user now exits 2 with "cannot open the predecessor ticket" (loud) where it used to spin. Single-user <lab-host> today (guess), so Info. Fix if multi-user ever matters: add `(remove it by hand: rm PATH)` to the :280 message.

## Not found (checked)
- Ticket number reused while an older ticket exists: no (max over all existing tickets under the lock).
- Queue `.lock` or ticket fd leaked into CMD/background children: CMD and both tee close 6/8/9 [src :169]; swait flock closes 8/9 [src :285]; memgate sleep closes 9 [src :115] and fd 8 is already closed there.
- Nested / `<offload-host> run` / `<offload-host> wait` regression: 131/131 [obs]; offload_run keeps its traps during CMD (unchanged from before; a TERM is deferred until CMD ends, then reports "aborted" and exits 143; pre-existing, now at least with a RESULT line).
- gate-xnu-build comment: text only; consistent with [obs] above (EXIT trap ran after default-action TERM).
- `sort -n` / `cut` under `set -o pipefail`: stickets may return 1 when the last glob item fails `[ -e ]`; every caller uses it inside `$(...)` without `set -e`, no effect.
