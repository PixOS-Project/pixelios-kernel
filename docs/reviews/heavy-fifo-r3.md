# Review heavy-fifo-r3: tools/heavy-job + tools/test_heavy_job.sh at 1b48c61 (delta 2aa4955..1b48c61, fixes for heavy-fifo-r2 F1-F7)

Reviewer: Sonnet 5.5, adversarial. Checkout: detached worktree review-1b48c61 (the batch worktree was not used). Tags: [obs] = observed in a run today on <lab-host> (bash 5.2.37, util-linux setsid 2.41.5); [src] = read from source file:line; [guess] = not verified.
No phone, no <offload-host>, no full gate. Probes ran in /tmp/20y/p with the test's own HEAVY_JOB_* fakes (mem/psi/load files, private lock and queue dirs).

## Verdict: SAFE WITH CONDITIONS

All 7 r2 findings are fixed as claimed and the new setsid/`__cmd` path works for the normal cases. Conditions: F1 and F2 (both are regressions against 2aa4955 in how CMD is signalled by anything other than heavy-job's own trap) and F3/F4 (the new killcmd has two input-dependent holes). F5-F8 are test gaps and hardening.

## Test results [obs]
- `bash tools/test_heavy_job.sh`: `RESULT: PASS cases=144` (2m35s).
- `bash tools/test_remote_job.sh`: `RESULT: PASS cases=223` (40s).
- `shellcheck -S warning tools/heavy-job tools/test_heavy_job.sh`: no findings (exit 0).
- Mutants of the new code, each a COPY under /tmp/20y/mut2/<M>/ run with the full test file (baseline of a copy has 1 environment-only failure, "check-all.sh nested prints the nested line and does not lock", present in every copy, passes in the real checkout; so "killed" below means one extra FAIL):
  - A: delete the poll loop in killcmd (line 203): SURVIVES (only the baseline FAIL). 
  - B: delete `kill "$ks"; wait "$ks"` (line 205): killed (F2 "job after an aborted CMD takes the lock at once").
  - C: delete `0<&0` (line 174): killed (F2 "CMD still reads the caller's stdin").
  - E: default grace `:-10` -> `:-0` (line 198): SURVIVES.
  The tests that exist kill the setsid, group-kill (`-$c`), SIGKILL-escalation and stdin mutations; nothing tests a CMD that handles SIGTERM and needs time to clean up (F5).

## Checked and fine [obs unless noted]
- CMD rc preserved exactly in the RESULT line: 0, 1, 2 (-> exit 2), 3, 124, 130, 143, SIGKILLed CMD (rc=137), missing CMD (rc=127). Exit mapping unchanged (0 / 2 / else 1).
- stdout and stderr stay separate through the new `__cmd` (OUT only on stdout, ERR only on stderr); stdin pipe reaches CMD (`echo hello | heavy-job run -- cat`); closed stdin (`<&-`) still runs CMD.
- `heavy-job run` inside `heavy-job run`: prints "nested in outer, no second lock", CMD rc kept. `heavy-job run -- heavy-job <offload-host> run -- CMD` (R26 form): CMD sees HEAVY_JOB_HELD=o1 and OFFLOAD_QUEUE_HELD=s1, rc 3 kept; aborting the outer with SIGTERM while the nested <offload-host> CMD sleeps ends in 0.1 s, rc 143, both queues empty, no survivor.
- Group kill does not reach the caller: a sibling `sleep` in the caller's group survived a sabort (`kill -TERM -- -$c` targets the setsid group only; setsid does not fork because a `&` child of a non-interactive shell is not a group leader).
- HEAVY_JOB_KILL_GRACE empty -> default 10 (`:-`); a CMD with a 2 s TERM handler under grace 10 finishes its cleanup (took 2.0 s).
- `bash -m heavy-job run` (monitor mode): CMD still waited for, rc 9 kept (setsid did not detach early).
- Relative HEAVY_JOB_LOCK (F5 of r2) covered by the new row; F7 warn/error paths do not fail a healthy lane (only emitted when a ticket is unreadable or unremovable, `[ -e "$f" ] && [ ! -r "$f" ]` is false for a normal ticket) [src :273-275].
- `<offload-host> run` code path itself is unchanged except `sabort`'s `[ -z "$cg" ]` (cg empty there) [src :212]; 223 remote-job cases pass.

## Findings

F1 (Medium) a group-wide SIGKILL of the caller no longer reaches CMD: CMD survives and keeps the host-wide lock
tools/heavy-job:174 (`setsid "$BASH" ... __cmd ... &`).
CMD now lives in its own session, so `timeout -s KILL N heavy-job run ...`, `kill -KILL -- -PGID` of the calling job, or a harness killing the process group no longer takes CMD down. heavy-job dies (SIGKILL cannot be trapped), CMD keeps running and keeps fd 7, so the lock stays held until CMD ends on its own, with no verdict line.
Scenario [obs, same script, two versions]: `timeout -s KILL 2 heavy-job run --class analysis --label tk -- bash -c 'sleep 31 & wait'`; afterwards `ps` shows `heavy-job __cmd` x2, `bash -c sleep 31 & wait`, `sleep 31` alive; `flock -n heavy.lock true` -> LOCK HELD. The same script on 2aa4955: lock free, no survivors. Also `setsid bash -c "heavy-job run ..."` then `kill -KILL -- -PGID`: new = survivors + HELD, old = free.
The old foreground form had the same weakness for `kill -9 <heavy-job pid>` alone, but not for group kills, which is what timeout and agent harnesses do. A gate (class gate, 30+ min) left running after its requester was killed jams every other gate.
Fix (prototype verified [obs] for the group-SIGKILL case and for rc/TERM paths; the full test file was not run against it):
1. line 174: `setpriv --pdeathsig TERM env --default-signal=INT,QUIT setsid "$BASH" "${BASH_SOURCE[0]}" __cmd "$ld/run.log" "$@" 0<&0 6>&- 8>&- 9>&- & cg=$!` (the `env` part is F2).
2. `cmd_inner` (line 457): make the leader react to TERM at once instead of blocking in a foreground pipeline:
```
cmd_inner() { local log=$1 rc bp; shift; trap 'kill -TERM -- "-$$" 2>/dev/null' TERM
	{ { "$@"; } 2> >(tee -a "$log" >&2) | tee -a "$log"; exit "${PIPESTATUS[0]}"; } & bp=$!
	wait "$bp"; rc=$?; [ "$rc" -le 128 ] || wait "$bp" 2>/dev/null; exit "$rc"; }
```
(pdeathsig on the leader alone is not enough: SIGKILL of the leader leaves CMD and the tees alive; the trap turns the parent-death TERM into a group TERM.) Add a test row: `timeout -s KILL 2 heavy-job run ... -- sleep 31`, then `flock -n lock true` and `pgrep -f 'sleep 31'` empty. If pdeathsig is not wanted, at least document the limit in the header line 54-56 ("a SIGKILL of heavy-job's group leaves CMD running and holding the lock").

F2 (Medium) CMD now starts with SIGINT and SIGQUIT ignored
tools/heavy-job:174. A `&` job of a non-interactive bash is started with SIGINT/SIGQUIT set to SIG_IGN, and `setsid`/`exec` pass that on. [obs] `/proc/$$/status` of CMD: new `SigIgn: 0000000000000006`, old `SigIgn: 0000000000000000`.
Effect: a bash CMD (check-all.sh, xrun, tools with `trap cleanup INT`) cannot trap SIGINT any more ("signals ignored upon entry cannot be trapped" [bash manual]); python CMDs do not install KeyboardInterrupt; every grandchild inherits the ignore. sabort sends only TERM, so INT-only cleanups in CMD silently never run (old: Ctrl-C reached CMD in the shared foreground group).
Fix (prototype [obs]: SigIgn becomes `0000000000000000`): `env --default-signal=INT,QUIT setsid "$BASH" ...` (coreutils >= 8.31 [guess for <offload-host>]; env is already used by the tests). Add a test row: CMD `grep SigIgn /proc/$$/status` == all zero in INT/QUIT bits. Recorded as AP-280 in docs/ANTIPATTERNS.md.

F3 (Medium) a second INT/TERM/HUP during killcmd exits heavy-job with CMD still alive
tools/heavy-job:198 (`cg=` cleared first thing in killcmd) and :212.
[obs] CMD `trap "" TERM; sleep 40 & wait`, grace 5; send TERM, 0.7 s later TERM again: heavy-job exits 143 after printing `RESULT: FAIL ... aborted by SIGTERM (ticket removed)` (no "(CMD killed)"), CMD pid and its `sleep 40` stay alive, lock HELD by CMD, no SIGKILL ever follows. Cause: the second TERM interrupts `wait -n`, the nested sabort sees `cg` empty and skips killcmd, `exit` runs; the first killcmd never reaches its SIGKILL line. A user pressing Ctrl-C twice (or a harness TERM then a second TERM) gets the orphan the F2 fix of r2 was meant to remove.
Fix: first statement of `sabort`: `trap '' INT TERM HUP` (further signals are ignored while aborting), and in killcmd leave `cg` set until the function ends (`cg=` as its last line, not in the `local` line). Row: send TERM twice 0.7 s apart to a CMD that ignores TERM with grace 3; expect exit 143, CMD gone, WARNING line present, lock free.

F4 (Medium) HEAVY_JOB_KILL_GRACE is not validated: non-integer values give an immediate SIGKILL with a false message, or a crash
tools/heavy-job:198, :203, :207.
[obs] CMD with a 2 s SIGTERM handler, TERM sent:
- `HEAVY_JOB_KILL_GRACE=1.5` -> `((: 1.5: syntax error: invalid arithmetic operator`, poll loop skipped, immediate SIGKILL (took 0.0 s) and the line "CMD ignored SIGTERM for 1.5 s" is false (it was killed at once).
- `HEAVY_JOB_KILL_GRACE=abc` -> `heavy-job: line 203: abc: unbound variable` (set -u), `sleep: invalid time interval`, heavy-job exits rc=1 with NO RESULT line and no SIGKILL.
- `0` and `-1` -> immediate SIGKILL, message claims a grace of 0 / -1 s.
Fix: validate once, before the lock, in cmd_run next to the `--wait` check: `[[ ${HEAVY_JOB_KILL_GRACE:-10} =~ ^[0-9]+$ ]] || err "usage: HEAVY_JOB_KILL_GRACE must be whole seconds"`; and in killcmd use `g=$((${HEAVY_JOB_KILL_GRACE:-10}))`-free integer after that check. Row for `abc` -> RESULT: ERROR exit 2, CMD not run.

F5 (Low, test gap) two mutants survive: no row for a CMD that handles SIGTERM and needs time to clean up
tests: mutant A (poll loop at :203 removed) and mutant E (default grace 0 at :198) pass all 144 rows; the existing F2 rows use a CMD that exits on TERM or ignores it entirely. With A or E a docker/ssh/make CMD with a TERM cleanup is SIGKILLed at once (the F4 `1.5` case shows exactly this behaviour) and nothing notices.
Fix: one row in tools/test_heavy_job.sh after line 318: CMD `trap 'sleep 1; echo cleaned >"$w/cleaned"; exit 0' TERM; sleep 60 & wait`, HEAVY_JOB_KILL_GRACE unset (default), TERM after the child started; expect `[ -s "$w/cleaned" ]` and exit 143. It kills A and E (cost: 1 s).

F6 (Low) CMD has no controlling terminal and is no longer stopped by Ctrl-Z
tools/heavy-job:174 [src; setsid semantics, not tested on a pty: this session has no tty, the `?` TTY column I saw proves nothing]. `ssh -t`, `docker run -it`, `sudo` password prompts and `read </dev/tty` in a CMD behave differently from before; Ctrl-Z stops only heavy-job. Nobody in tools/ is known to do this (`grep -rn 'heavy-job run' tools` shows check-all.sh and the test only), so Low. Fix: add one sentence to the header (lines 54-56): "CMD has no controlling terminal and is not stopped by Ctrl-Z".

F7 (Low) status prints a false UNREADABLE (with an `rm` hint) for a live ticket that is still being written
tools/heavy-job:377 (`read -r o l pid ep st <"$f" || { echo ... UNREADABLE ... rm $f }`). stake creates the ticket empty under the queue lock (:293) and writes the content at :294, but `sstate` does not take the queue lock; `read` of an empty file returns 1. [obs] with a locked, empty ticket: `heavy-job: local queue: 000777.live UNREADABLE (another user's? remove by hand if dead: rm ...)`. r2 F7 asked for loudness, but this tells a reader to delete a live waiter's ticket. Window is microseconds, hence Low.
Fix: distinguish by openability: `[ -r "$f" ] || { echo ...UNREADABLE...; continue; }; read -r ... <"$f" || { echo "heavy-job: $qk queue: ${f##*/} (being written)"; continue; }`.

F8 (Info) missing preflights for the new dependencies
- `setsid` absent [obs, PATH without it]: CMD not run, `line 174: setsid: command not found`, `RESULT: FAIL ... rc=127`, exit 1: loud, but indistinguishable from "CMD not found" and it happens after the lock and ticket were taken. Fix: in cmd_run before `stake`: `command -v setsid >/dev/null || err "setsid not found (util-linux)"`.
- `wait -n -p` needs bash >= 5.1 [src: bash manual, guess for the <offload-host> bash version]; on older bash the error is hidden by `2>/dev/null` (:201), `w` stays empty, and the grace is skipped (immediate SIGKILL). Fix: next to the setsid check: `(( BASH_VERSINFO[0] * 100 + BASH_VERSINFO[1] >= 501 )) || err "bash >= 5.1 required (wait -p)"`.

## Not found (checked)
- Exit code, stdin/stdout/stderr passthrough, nested run in run, `<offload-host> run` nested in `run`, relative HEAVY_JOB_LOCK from two cwds, F7 paths on a healthy lane, zombie/early return of `wait "$cg"` (a specific-pid wait; a signal without a trap kills heavy-job as before), lock release on SIGTERM/HUP/INT during CMD.
- Lock text: `: >"$lock"` after CMD and in sabort; the flock itself is held by CMD (inherits fd 7 on purpose) so mutual exclusion holds on every path; the only "lock held after exit" case is F1/F3, where CMD is still running.
- HEAVY_JOB_LOCK as a symlink or with a nonexistent parent: queue is created next to the given path (`dirname` of the path as written), `mkdir -p` creates the parent; consistent between callers that pass the same path. A `~`-prefixed unexpanded value would create a literal `$PWD/~/...` directory (caller error, not changed by this commit).
