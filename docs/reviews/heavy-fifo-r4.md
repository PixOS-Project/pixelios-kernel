# Review heavy-fifo-r4: tools/heavy-job + tools/test_heavy_job.sh at da020df (delta 1b48c61..da020df, fixes for heavy-fifo-r3 F1-F8)

Reviewer: Sonnet 5.5, adversarial, read-only. Checkout: detached worktree /tmp/20aa/rev-da020df (the batch worktree was not touched). Date of all runs: 2026-10-29, <lab-host> (bash 5.2.37, util-linux setsid/setpriv 2.41.5, coreutils env 9.7). Tags: [obs] = observed in a run today; [src] = read from source file:line; [guess] = not verified.
No phone, no <offload-host>, no full gate. Probes ran in /tmp/20aa/p with the test's own HEAVY_JOB_* fakes (private lock, queue, mem/psi/load files). Mutants: copies under /tmp/20aa/mut/<M>/ (never the real files).

## Verdict: SAFE WITH CONDITIONS

All eight r3 findings are fixed as claimed for the cases the new rows test (details below), and the new parent-death path (F1) works for the case it was written for (`timeout -s KILL` on a CMD that exits on SIGTERM). Conditions: F1 and F2 below are two holes in that same F1 mechanism, both leave CMD alive and the host-wide lock held (the exact failure r3 F1 was about), both reproduced and both fixed in a prototype that passes the full test file. F3-F6 are Low/test gaps and wording.

## Test results [obs]
- `bash tools/test_heavy_job.sh` on the unmodified da020df copy: `RESULT: PASS cases=156` (0 FAIL; 144 old + 12 new rows).
- `shellcheck -S warning tools/heavy-job tools/test_heavy_job.sh`: no findings (exit 0).
- Mutants (28, each a copy; each copy's test file had its `sleep 31.7x` durations renamed to a unique number, because the F1 rows use `pgrep -f '^sleep 31\.71$'` and parallel runs see each other's survivors; a first parallel attempt without that was discarded). Killed = at least one extra FAIL. Note: when a mutant leaves a surviving CMD, it holds the test's lock and cascades extra FAILs into later rows (F3/F4/F5 rows in M01/M03/M04/M19/M20/M21); the first FAIL is the real kill.
  - Killed: M01 (drop `setpriv --pdeathsig`), M02 (drop `kill -TERM -$$` in the USR1 trap), M03 (drop the SIGKILL escalation in cmd_inner), M04 (bp without `trap '' TERM`), M05 (no `env --default-signal`), M06 (env without TERM), M07 (sabort without `trap '' INT TERM HUP`), M09 (grace regex `^[0-9]+$`), M11 (regex allows leading zero/0), M13 (env preflight removed), M15 (`[ -r ]` check of F7 removed), M18 (drop `0<&0` of bp), M19 (USR1 wait branch disabled), M20 (USR1 handler leaves TERM armed), M21 (escalation message branch disabled), M22 (killcmd poll loop removed), M25 (fd 9 not closed for CMD), M26 (F7 text back to UNREADABLE), M27 (`command -v setsid` removed), M28 (bash version check removed).
  - SURVIVING (test gaps, see F4): M08 (`cg=` first in killcmd again; equivalent while M07's trap holds, so not a gap in practice), M10 (drop `-le 3600`), M12 (drop the `setpriv --pdeathsig USR1 true` preflight), M14 (default `HEAVY_JOB_MIN_BASH` 501 -> 0), M16 (drop the `kill -TERM "$c"` leader fallback), M17 (`kill -KILL "$ks"` -> `kill "$ks"`), M23 (killcmd default grace 10 -> 3), M24 (cmd_inner default grace 10 -> 1).

## Status of r3 F1-F8 (checked in the code and by running it)
- r3 F1 (group SIGKILL leaves CMD + lock): FIXED for CMD that exits on TERM [obs: rows "r3 F1" pass; `timeout -s KILL 1.5` probe: lock free, run.log has "heavy-job died (parent-death signal)"]; also OK under `bash -m heavy-job run` (lock free after SIGKILL of heavy-job, log line present) [obs]. NOT fixed for two realistic variants: new F1 and F2 below.
- r3 F2 (CMD ignores INT/QUIT): FIXED [obs: SigIgn of CMD = 0000000000000000; M05 killed]. The leader itself still has QUIT ignored (SigIgn 4 seen on the leader); harmless, it never runs user code [src :478-490].
- r3 F3 (second signal during killcmd): FIXED [obs: row passes; M07 killed]. The `cg=` move to the end (:223) is redundant with `trap ''` (M08 equivalent). Residual: a SIGKILL (not a trappable signal) during the grace is F2 below.
- r3 F4 (grace validation): FIXED [obs: abc, 1.5, 0, -1, 01, 99999 -> RESULT: ERROR exit 2 before the ticket; 5 and unset accepted]. 3601..9999 untested (M10).
- r3 F5 (TERM-cleanup row): FIXED [obs: row passes; M22 and "default grace 0" kill it]. It does not pin the default value (M23, M24) nor the abort latency (M17), see F4.
- r3 F6 (no ctty): documented in the header [src :59]. Not testable without a pty. FIXED as a doc item.
- r3 F7 (false UNREADABLE for an empty live ticket): FIXED [obs: M15, M26 killed; `[ -r ]` first, then `read` -> "(being written)"].
- r3 F8 (preflights): FIXED for the four checks [src :155-159], run before the ticket; rows exist for setsid, setpriv (missing), env, bash version. Nested runs skip the preflight on purpose (`exec "$@"` at :151). `setpriv` present but without `--pdeathsig` (:158) has no row (M12).

## Findings

F1 (Medium) after a parent-death (USR1) the leader does not wait for the other members of CMD's session: a CMD that exits on SIGTERM but leaves a TERM-ignoring (or slow) background process keeps the lock for good
tools/heavy-job:484-490 (cmd_inner USR1 branch). The escalation runs only `if [ -n "$pd" ] && kill -0 "$bp"`; once bp (CMD + both tees) is gone the leader does `exit "$rc"` at :490 even though session members still exist and still hold fd 7.
Scenario [obs]: `HEAVY_JOB_KILL_GRACE=2 timeout -s KILL 1.5 heavy-job run --class analysis --label z1 -- bash -c 'bash -c "trap \"\" TERM; exec sleep 52.31" >/dev/null 2>&1 & exec sleep 53.4'`. After 4 s: `sleep 52.31` alive, `flock -n lock true` -> LOCK HELD, run.log has only the "heavy-job died" line, no "sending SIGKILL". (When the background process keeps stdout/stderr open, bp stays alive and the escalation fires: that is the only case the test covers.) The sabort path does not have this hole: killcmd polls `kill -0 -- "-$c"` over the whole session (:217-219). So the header claim "so CMD does not keep the lock (r3 F1)" (:58) is false for daemon-style children, and no test row covers it.
Fix (prototype [obs]: scenario above now ends with LOCK FREE and run.log "session members still hold the lock 2 s after heavy-job died, sending SIGKILL"; full test file on the prototype: `RESULT: PASS cases=156`): in cmd_inner, after the existing `if [ -n "$pd" ] && kill -0 "$bp"` block and before `exit "$rc"`, add
```
if [ -n "$pd" ]; then   # wait until nobody of this session holds the lock any more (the leader drops its own fd 7 first)
	exec 7>&-
	flock -w "$g" "$lock" true || { echo "heavy-job: WARNING: session members still hold the lock ${g} s after heavy-job died, sending SIGKILL" >>"$log"; kill -KILL -- "-$$"; }
fi
```
(`lock` is the script-level absolute path, :64-65. Do not use `pgrep -s $$ | wc -l`: the substitution's own forks are session members, the count never drops to 1 [obs: that variant broke both F1 rows].) Add a row: the scenario above under `timeout -s KILL 1`, expect lock free within grace+2 s, no `sleep 52.xx` survivor, "still hold the lock" in run.log.

F2 (Medium) a SIGKILL of heavy-job during killcmd's grace leaves a TERM-ignoring CMD alive and the lock held: the leader is already dead, so the parent-death signal has nobody to fire in
tools/heavy-job:478-482 (cmd_inner has no TERM handling in the leader) with :215 (killcmd sends the group TERM).
sabort's `kill -TERM -- -$c` also hits the CMD leader, which has the default TERM disposition and dies at once [obs: `wait -n` returns the leader immediately; run.log empty]; bp and CMD then run on without any pdeathsig. If heavy-job is SIGKILLed before the grace ends, nothing escalates. The comment at :476 ("A TERM from sabort is NOT trapped: it reaches the whole group directly") hides that the leader dies of it.
Scenario [obs]: `HEAVY_JOB_KILL_GRACE=6 heavy-job run ... -- bash -c 'trap "" TERM; exec sleep 54.7' &`; `kill -TERM $hp`; 1.5 s later `kill -KILL $hp`; 7 s later: `sleep 54.7` alive, LOCK HELD, run.log has no "parent-death" line. This is realistic: `timeout -k 5 N heavy-job run ...` (TERM at N, KILL 5 s later) with the default grace 10 is exactly this; so is a harness that escalates TERM -> KILL faster than 10 s. The comment at :211 ("reaped before heavy-job exits (no orphan CMD)") is false in that case.
Fix (prototype [obs]: same scenario ends with LOCK FREE and run.log "CMD ignored SIGTERM for 6 s after heavy-job died, sending SIGKILL"; full test file on the prototype PASS cases=156): first statement of cmd_inner `trap '' TERM` so the leader outlives the group TERM and its pdeathsig stays armed (the USR1 handler already does `trap "" USR1 TERM`; bp/tees/env are unaffected: bp sets its own `trap '' TERM`, env restores TERM for CMD). killcmd's `wait -n -p w "$c" "$ks"` then returns when the whole bp is done or the grace ends, which is the intended semantics. Add a row: TERM then KILL to heavy-job 1.5 s apart, CMD ignores TERM, grace 6; expect lock free by about 8 s and "sending SIGKILL" in run.log.

F3 (Low) a SIGKILL of the CMD leader alone ends heavy-job with `RESULT: FAIL rc=137` while CMD keeps running and keeps the lock; nothing is said
tools/heavy-job:190-191 (`wait "$cg"; rc=$?; cg=`). [obs] `kill -KILL <leader>` of a running job: heavy-job prints bash's "Killed setpriv --pdeathsig ..." and `RESULT: FAIL ... rc=137 elapsed=0s`, `sleep 55.8` stays alive, LOCK HELD, `: >"$lock"` already cleared the holder text, so `status` says "lock held by: " with nothing. Same for any CMD whose leader wrapper dies (OOM killer on the wrapper is [guess]-unlikely, it picks the biggest process). Rule 8 (no silent failures) applies: a FAIL line that hides a live CMD.
Fix: in cmd_run keep the pid and look at the session after the wait: `c=$cg; wait "$c"; rc=$?; if [ "$rc" -gt 128 ] && kill -0 -- "-$c" 2>/dev/null; then cg=$c; echo "heavy-job: WARNING: CMD leader died by a signal (rc=$rc) but its session is still running; terminating it" >&2; killcmd; fi; cg=`. (Plain daemon survivors after a normal rc are the documented limit and are not touched.)

F4 (Low, test gaps) eight mutants survive
tools/test_heavy_job.sh r3 block (lines 323-380). Rows to add, each one line:
- M17 (`kill -KILL "$ks"` -> `kill "$ks"`): sabort ignores TERM and `sleep` inherits that, so `kill "$ks"` is a no-op and every abort waits the FULL grace (10 s default) even when CMD died at once [src :220 comment says so]. Test: extend the F5 row (or add one) with an elapsed assertion: abort of `sleep 31.75`-style CMD that exits on TERM, default grace, `elapsed < 3 s`.
- M10: grace 3601 and 9999 -> RESULT: ERROR (add to the `for g in` list at the F4 row).
- M12: a PATH whose `setpriv` is a stub exiting 1 for `--pdeathsig` -> RESULT: ERROR "setpriv --pdeathsig is not supported" (copy the `path-badenv` pattern).
- M23/M24: the default 10 is written in four places (header :56, validator :147, killcmd :213, cmd_inner :479) and two of them are untested. Better than a row: compute once in cmd_run (`export HEAVY_JOB_KILL_GRACE=${HEAVY_JOB_KILL_GRACE:-10}` after the validation) and read `$HEAVY_JOB_KILL_GRACE` in killcmd and cmd_inner (they only run under cmd_run / its child); then one literal remains and the existing rows cover it.
- M16 (leader-pid fallback in killcmd) is a few-ms race; no cheap test. Accept.
- M14 (default minimum bash 501 -> 0) is not testable on a 5.2 host; accept.
- M08 is equivalent, no action.

F5 (Low) `env` changes what CMD means: leading `NAME=VALUE` words and `-option` words are consumed by env instead of failing
tools/heavy-job:482 (`env --default-signal=INT,QUIT,TERM "$@"`). [obs] `heavy-job run -- FOO=bar bash -c 'echo foo=$FOO'` -> `foo=bar`, PASS (before r3 and in the nested path `exec "$@"` at :151 this is "FOO=bar: command not found", rc 127); `heavy-job run -- -i` -> PASS rc 0 (env -i with no command prints nothing and succeeds). A typo'd option-like CMD is therefore a green run (rule 8) and run and nested-run disagree. No current caller does this (callers: check-all.sh and tests [src grep]), hence Low.
Fix: `env --default-signal=INT,QUIT,TERM -- "$@"` (kills the `-i` case; NAME=VALUE still works) and one header sentence "CMD's first word must be a program (a leading NAME=VALUE is applied by env)"; or, to keep the old meaning, reject in cmd_run: `case $1 in -*|*=*) err "usage: CMD must start with a program name" ;; esac` before the nested check.

F6 (Info, wording) stale or false statements
- :58 header "so CMD does not keep the lock (r3 F1)": false for F1/F2 above. After the fixes it is true.
- :476 "A TERM from sabort is NOT trapped: it reaches the whole group directly (one TERM, not two)": true but omits that the leader dies of it (F2). After the F2 fix: say the leader ignores TERM.
- :211 "reaped before heavy-job exits (no orphan CMD)": false when heavy-job is SIGKILLed mid-grace (F2).
- :155, :158, :159 "UNVERIFIED on <offload-host>": `heavy-job run` executes on the machine that calls it (<lab-host> for check-all.sh / remote-job `--where <lab-host>`; the <offload-host> route uses `<offload-host> run`, which has none of these preflights) [src remote-job:49-61]. The values on <lab-host> are now observed (bash 5.2.37, util-linux 2.41.5, coreutils 9.7); the marker is ambiguous and should read "verified on <lab-host> 2026-10-29 (bash 5.2.37, util-linux 2.41.5, coreutils 9.7); <offload-host> not read, only matters if `run` is ever used there".
- :59 "CMD starts with SIGINT/SIGQUIT at their defaults": true, but INT is never sent to CMD (sabort sends TERM only [src :215]); a CMD with an INT-only cleanup (`trap cleanup INT`) never runs it on Ctrl-C of heavy-job. Before r2 Ctrl-C reached CMD in the shared foreground group. Add "heavy-job turns INT/HUP into TERM for CMD".
- :56 grace "10" accurate [obs: measured 1.02 s for grace 1, 3.04 s for grace 3 with a TERM-ignoring CMD].
- "CMD (not via eval), its exit code kept" (header run (4)): see F5 for the env caveat.

## Hunted and not found (checked)
- Exit code: 0/3/9 kept in the RESULT line; exit mapping 0/2/else 1 unchanged; closed stdin (`<&-`) runs CMD, piped stdin reaches CMD [obs]. `bash -m heavy-job run`: CMD waited for, rc 9 kept, and the parent-death path still fires [obs].
- Signal during lock wait / ticket: no CMD yet, `cg` empty, sabort unchanged from r3 (rows exist). The window between the fork of the launch line and `cg=$!` is covered by the pdeathsig: if sabort misses `cg` and heavy-job exits, USR1 reaches the leader [guess, microseconds; not reproduced].
- pdeathsig semantics: fires when the creating thread of the process exits; heavy-job is single-threaded, so thread death = process death [src prctl(2) PR_SET_PDEATHSIG]. Kept across the setpriv -> setsid -> bash execs and cleared on fork [src prctl(2)], so bp, tees and CMD do not have it, as the comment at :187 says. Not set for setuid binaries: none involved. Not tested: heavy-job exiting normally while the leader is alive cannot happen (cmd_run waits for it).
- Lock/ticket after heavy-job SIGKILL: the ticket lock is fd 9, closed for CMD (`9>&-`, M25 killed); queue shows the ticket STALE, not held [src :309, :393]. Lock text is left stale after SIGKILL (informational only, flock decides).
- Preflight on a host lacking a tool: `err` before the ticket and before the log dir exists, so no run.log is written and the exit is 2 with `RESULT: ERROR ...` [src :81, :155-159]; rows exist.
- HEAVY_JOB_KILL_GRACE is not re-validated in the hidden `__cmd` path; it is inherited from the validated parent, and `__cmd` is not a documented entry point (a direct `heavy-job __cmd` with a bad value would hit the arithmetic error of r3 F4). Info only.

## Antipattern candidates (not written: this brief allows writing only this file)
- AP-01 again (2026-10-29): `pkill -f 'sleep 41.1'` inside a bash command whose own text contains the pattern killed the tool shell (exit 144). Use a pid looked up with `pgrep -x sleep` and a /proc cmdline check.
- New: running host-test mutants in parallel when the test uses `pgrep -f` on fixed sleep durations and a lock shared by survivors cross-contaminates results (survivors of one mutant fail rows of another). Give each mutant unique durations or run them sequentially; read the first FAIL, not the count.

## Resolution (20aa, implementer; tags: [obs] = observed 2026-10-29 on <lab-host>, [src] = read from source)
- F1 fixed: cmd_inner waits (flock -w grace, after dropping its own fd 7) until no session member holds the lock after a parent-death signal, else SIGKILL + run.log line. Row "r4 F1". [obs: fails on f91d638 with the lock held 11 s, passes]
- F2 fixed: `trap '' TERM` first in cmd_inner (leader outlives the abort's group TERM). Row "r4 F2". [obs: fails on f91d638, passes]
- F3 fixed: cmd_run checks the session after the leader's wait when rc > 128, warns in output and run.log, ends it via `killcmd reaped`. Rows "r4 F3" (3 rows incl. TERM during that kill and a 1 s cleanup). [obs: first row fails on f91d638 alone]
- F4: M10, M12, M14 (source pin), M16 (setsid stub delays 2 s), M17 (elapsed < 3 s) killed by rows; M23/M24: the default literal is now written once (export in cmd_run), a row pins CMD sees grace=10; the other two reads are `${..:?}` so those mutants are equivalent by construction. M08 is no longer equivalent (the F3 killcmd runs untrapped): killed by "TERM while ending a leaderless CMD".
- F5: CMD starting with `-*` or `*=*` is refused with RESULT: ERROR usage (nested or not); `env NAME=VALUE prog` is the way.
- F6: header and comments corrected (no orphan claim only after F1/F2, leader ignores TERM, INT becomes TERM for CMD, "verified on <lab-host>" instead of UNVERIFIED on <offload-host>, run executes where called). No "cold"/"7-11"/"384"/"670" figures remain in tools/heavy-job [src grep].
