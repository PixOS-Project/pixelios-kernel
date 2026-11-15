# Review heavy-fifo-r1: d9d057e "heavy-job: arrival-ordered FIFO on the local lock"

Reviewer: Sonnet 5.5, adversarial. Scope: `git diff c2c2d97 d9d057e` (tools/heavy-job, tools/test_heavy_job.sh). No code edited.
Tags: [obs] observed in this review (command run), [src] read from source (file:line), [guess].

## Verdict: SAFE WITH CONDITIONS

The ordering core is sound: ticket liveness is the kernel flock on the ticket file (no pid, no pid-reuse or container problem), the ticket is
removed by an EXIT trap on every non-SIGKILL path, a SIGKILLed waiter leaves a lock-free ticket that the next taker or the next waiter removes,
and the real flock on $HEAVY_JOB_LOCK stays as the mutual exclusion. No exploitable race found. Conditions: fix F1 and F2 (availability of every
lane) before merging to trunk; F3-F6 may follow. Verified [obs]: `bash tools/test_heavy_job.sh` -> `RESULT: PASS cases=117` (no FAIL line);
`shellcheck -S warning tools/heavy-job tools/test_heavy_job.sh` -> no findings, exit 0.

## Answers to the focus points

1. Ticket lifecycle [src heavy-job:139 trap srelease EXIT; trap sabort INT TERM HUP; :181-182]. Removed on normal exit, rc!=0, err()/serr() (exit 2),
   timeout, INT/TERM/HUP (sabort kills the background child, srelease, exit 143). TERM in the CMD phase (trap cleared at :161): [obs] a bash with a
   default-action TERM/HUP ran its EXIT trap and exited at once (bash -c 'trap ... EXIT; sleep 3', rc 143/129), so the ticket is removed there too; if
   it were not, the ticket is lock-free = stale, same as kill -9. kill -9 of the waiter: the ticket file stays, its lock dies with the process; swept by
   the next `stake` (:226) or by the successor in `swait_turn` (flock acquired -> `rm -f "$pred"` :250). Dead = "nobody holds the flock", never a pid,
   so pid reuse, containers and PID namespaces cannot fake liveness. A dead ticket at the head cannot block: its successor gets the lock at once.
   The only way a dead owner keeps a live lock is an inherited ticket fd 9: it is closed for sleep (:107), flock children (:141,:248), CMD and both tee
   (:162); the remaining pre-CMD children are short-lived `$(...)`. Orphan CMD after heavy-job is killed holds fd 7 (the real lock), so the successor
   passes the queue and waits on the plain flock: safe.
2. Ordering/budget: ticket before the memory gate [src :141-143]. Head-of-line blocking by a head that waits for memory is intended and documented
   (header :24-27) and tested (test lines 111-118 `mh`/`ml`). ONE budget: t0 set at :135, used by swait_turn (rem), memgate (:105) and the lock wait
   (:148); t0 is reset only after the lock (:156) for the second gate, as before. A head that fails its gate leaves (exit 2, ticket removed) and the
   next job becomes head. Side effect (info, F7): a class gate/build head (needs 12 GB available) now delays an analysis job that would have passed 10 GB.
3. Race on take: serialised by `flock 8` on `$sq/.lock`, number from `$sq/.seq` incremented under that lock, name `%06d.label` so ties are impossible
   and lexicographic = numeric order. .seq lives in ~/.cache so it survives reboots. Findings F1 (corrupt/missing .seq), F8 (overflow at 10^6).
4. Nested: `HEAVY_JOB_HELD` check [src :131-134] is before any ticket/trap, `exec "$@"`, no ticket, no deadlock (test 203-206 nin/nout passes). The <offload-host>
   path diff is only the parameterisation (qn/qw/qk/qpos) with defaults `offload-queue`/`<offload-host> queue`/`<offload-host>`/empty [src :176-177]; qposmsg returns
   nothing when qpos is empty. cmd_run sets sq/qn/qw/qk only inside the `run` process, `<offload-host> run` is a separate process. All <offload-host> tests pass [obs].
5. hq-legacy [src test:50,59,64,72,141]: five tests that need a holder the new waiter cannot see in its queue, to reach the plain flock path
   (lock-held line, flock wait expiry, same label, run.log not polluted, post-lock memory gate). The holder is a new-version process with a ticket in a
   different queue dir, so it is a faithful stand-in for an older ticket-less copy as far as the waiter can tell. Hides nothing: the same behaviours
   are not re-tested through the ticket path, but that path has its own tests (160-209). Note the user-visible side: for ticketed holders the
   timeout message is now `RESULT: ERROR heavy-queue: waited Ns for NNNNNN.x, not our turn (queue position K)` instead of `heavy-job lock held by: ...`.
6. Consumers: [obs] grep of tools/ docs/WORKFLOW.md .claude for `heavy-job status`, `lock held by`, `waiting up to`, `memory-gate not met`,
   `heavy-job:` parsers: no consumer of `status`; gate-xnu-build:52 greps `^heavy-job: RESULT ` (the new queue ERROR in QUIET mode matches it and is
   reported as heavy-job's verdict, intended); remote-job hl_lock_ok reads the holder line from $lock (unchanged format, still written after the
   flock). The new line `heavy-job: queue: ticket ...` is not a RESULT line and goes through out(): stderr in QUIET mode. No break found.
7. Mutants (all against tools/test_heavy_job.sh, read, not run):
   - M1 srelease no-op / drop EXIT trap: CAUGHT ("no ticket is left behind", "holder's ticket released", test 80/103).
   - M2 drop the dead-ticket sweep in stake: CAUGHT (000009.dead sweep test 93-94). Drop `rm -f "$pred"` in swait_turn: CAUGHT only as a 60 s
     timeout (D spins, never runs; kill -9 test 83-85 expects BD).
   - M3 reverse/`tail` ordering or `%d` instead of `%06d`: CAUGHT (3 rounds BCD; glob would not match). Move stake after memgate: CAUGHT (mh/ml 111-118).
   - M4 drop `9>&-` on CMD/tee/sleep (a daemon child keeps the ticket, queue stalls behind a finished job): SURVIVES (F4).
   - M5 delete `trap sabort INT TERM HUP`: SURVIVES (no INT/TERM test; harmless on TERM per the [obs] above, but the background-child kill is untested).
   - M6 reset t0 after swait_turn (budget no longer shared): SURVIVES (expiry test 98-100 only expires inside the queue).
8. shellcheck: clean, both files [obs].

## Findings

### F1 (Medium) .seq empty/unreadable stops every lane; deleted .seq with live tickets reorders
heavy-job:231 `[[ $n =~ ^[0-9]+$ ]] || serr ... refusing to restart numbering` and :232 `echo "$n" >"$sq/.seq"` (truncate-then-write, exit code unchecked).
Scenario: disk full (or kill -9 in the truncate window) leaves an empty .seq; after space is back every `heavy-job run` (all gates, xnu-local-build,
the <lab-host> lane) exits 2 until a human edits the file. Before this commit only the <offload-host> lane had this failure mode. If instead .seq is simply
deleted while live tickets exist, `[ -e .seq ]` is false, numbering restarts at 1 and a new job sorts before queued ones (the reordering the message
warns about).
Fix: in stake, derive the floor from the tickets: `m=$(for f in $(stickets); do b=${f##*/}; echo $((10#${b%%.*})); done | sort -n | tail -1)`; use
`n=max(.seq if numeric, m, 0)`, print a loud `heavy-job: queue: .seq repaired to N` line when .seq was invalid/missing; write atomically
(`echo $n >$sq/.seq.tmp && mv -f $sq/.seq.tmp $sq/.seq || serr ...`). Keep the existing <offload-host> .seq test but expect the repair for both queues,
and add a local test (empty .seq with a live ticket: new ticket number > live one).

### F2 (Medium) stake blocks without a timeout on `flock 8` and defers signals
heavy-job:228 `exec 8>>"$sq/.lock"; flock 8 || ...` is a foreground, unbounded flock. If anything holds `.lock` (a stake wedged on a stopped/NFS-stalled
process, or a future child that inherits fd 8), every new run hangs forever, ignoring `--wait`, and a bash trap (TERM/INT/HUP) is deferred until
flock returns, so the job is not even stoppable except by SIGKILL (leaving a stale ticket, harmless). Today only stake itself holds fd 8, briefly, and
all children close it (:141-:248, :162), so this is latent, but the blast radius is now every gate and phone build.
Fix: use the file's own pattern: `flock -w "$wait" 8 9>&- & fp=$!; wait "$fp" || serr "$qn: cannot lock $sq/.lock within ${wait}s"; fp=` (fd 8
is shared with the child via the same open file description, so the lock is held by the parent). Add a test: hold `$w/hq/.lock` from a helper for 3 s,
run with --wait 1, expect `RESULT: ERROR heavy-queue: cannot lock` exit 2 and no ticket.

### F3 (Low) swait_turn spins when the predecessor cannot be opened
heavy-job:244 `{ exec 6<"$pred"; } 2>/dev/null || continue` re-lists and retries with no sleep and no budget check (the `rem` test is on :245,
after the open). A ticket the user cannot read (mode 0600 created by another user or by sudo with umask 077 in the shared dir) makes the waiter spin
at 100 percent CPU forever ignoring --wait. Same code on the <offload-host> lane.
Fix: compute `rem` and serr before the `exec 6<`, and on open failure of an existing file `serr "$qn: cannot open ${pred##*/}"` instead of `continue`
(continue only when `[ ! -e "$pred" ]`).

### F4 (Low) test gaps that let mutants M4, M5, M6 survive
Add to tools/test_heavy_job.sh (local FIFO section): (a) CMD that starts a background `sleep 30 >/dev/null 2>&1 &` and exits; the next run must start
within 2 s (ticket fd not leaked); after the test kill the sleep. (b) a waiter in the queue and a waiter in the memory gate each get SIGTERM: exit 143,
no ticket, no orphan `sleep`/`flock` child (check with `pgrep -P`). (c) --wait 4 with a 3 s queue wait and a memory-gate that stays closed: the gate
must give up at about 4 s, not 7 s (one budget).

### F5 (Low) overriding HEAVY_JOB_LOCK does not move the queue
heavy-job:49 queue default is `$HOME/.cache/pixelios/heavy-queue` independent of the lock path. A script or test that sets only HEAVY_JOB_LOCK
(to isolate itself) queues behind the real user's tickets for up to --wait. [obs] no such caller in the tree today (test_remote_job.sh sets HOME and
stubs heavy-job). Fix: default `hq=${HEAVY_JOB_QUEUE_DIR:-$(dirname "$lock")/heavy-queue}` after `lock=` (same directory by default, so production
paths are unchanged), and drop the explicit export from the test or keep it.

### F6 (Low) aborted runs end without a RESULT line (AGENTS.md rule 8 / "tools end in a RESULT line")
heavy-job:182 `sabort` exits 143 silently, and the memory-gate error after a long queue wait (:143) does not name the queue position that the header
promises for "a job whose wait expires" (header :27). Fix: sabort prints `RESULT: ERROR aborted by signal (ticket NNNNNN.label removed)` through
sres/res_line (not serr, which would recurse into the trap), and the :143 err text appends `$(qposmsg)`-style info or "after Ns in the queue".

### F7 (Info) head-of-line and message changes are behaviour changes, not bugs
(a) A gate/build head (needs 12 GB) delays a later analysis job (needs 10 GB) that would have passed alone; documented intent. (b) the usual lock
timeout text changed (see answer 5); no parser depends on it [obs]. Record both in docs/WORKFLOW.md where heavy-job is described, with the
observed-on date once a real contended run exists (rule 5: this is [src]+host test only, not yet observed on the real lanes).

### F8 (Info) ticket numbers beyond 999999
heavy-job:222 glob needs exactly six digits then `.`; 1000000.label is invisible to `stickets`, so that job would wait behind the last visible
ticket and then run unordered. At the observed rate this is years away. Fix when touched: `%06d` -> keep, and make the glob `[0-9]*.*` with a numeric sort.

### F9 (Info) comment contradicts what was observed
tools/gate-xnu-build:44 says "bash runs no EXIT trap when killed by a signal with the default action". [obs] on this host (bash of Debian 13) the EXIT
trap ran after a default-action TERM and HUP while a foreground child was running. heavy-job's correctness does not depend on either answer (stale
tickets are flock-detected), but the comment should name the bash version/case or be corrected.

## Not found (checked)
- Ticket name collision, counter non-monotonic across processes or reboots: no (F1 aside).
- Pid reuse / container / other-user liveness: not used; flock only.
- Nested call taking a ticket or deadlocking on the parent: no.
- <Offload-host> lane regression: no, 117/117 incl. <offload-host> run/status/wait and remote-job via real heavy-job.

## r1 fixes (implementer 20x, commit a5b6492 on tools/heavy-fifo; tags: [obs] host test, [src] source)
- F1 fixed: next number = max(.seq, highest live ticket)+1, .seq written tmp+mv under the queue .lock, loud `queue: .seq repaired to N (empty|garbled 'x'|missing while a live ticket K exists|behind the highest live ticket K)`; never exit 2. The <offload-host> test row "empty .seq is a loud error" now expects the repair (the lane must keep running). Rows: empty, deleted, garbled, too long, behind. [obs] 131/131.
- F2 fixed: `flock -w REM 8 9>&-` in the background (REM = --wait minus elapsed, the ONE budget), trappable; timeout = `RESULT: ERROR heavy-queue: cannot lock ... within Ns`. Row: lock held, --wait 1, gave up after 1.0 s.
- F3 fixed: an existing predecessor that cannot be opened is `RESULT: ERROR ...: cannot open the predecessor ticket` (exit 2), a vanished one is re-listed. Row (skipped as root): the old code spun until `timeout 15` killed it [obs on d9d057e].
- F4 fixed: rows for M4 (CMD lists its own fds, no ticket fd), M5 (SIGTERM in the queue, SIGHUP in the memory gate: exit code, RESULT line, no ticket, no orphan flock/sleep child), M6 (3 s queue + closed gate, --wait 4 ends at 4.0 s, not 7). Mutants applied in temp copies: M4 -> FAIL F4a; M5 -> FAIL both F4b/F6 rows; M6 -> FAIL F4c (7.0 s); real copy passes those rows. [obs]
  Correction to answer 1: the forked pipeline subshell that runs CMD keeps ticket fd 9 until CMD ends (found with a first draft of the F4a row: after kill -9 of heavy-job the ticket stayed locked). Harmless: CMD holds the real lock on fd 7 for as long, so a successor waits the same time. The tee 9>&- redirections are not covered by a row.
- F5 fixed: queue dir default is `dirname $HEAVY_JOB_LOCK`/heavy-queue (production path unchanged). Row: only HEAVY_JOB_LOCK set.
- F6 fixed: INT/TERM/HUP print `RESULT: FAIL heavy-queue label=X aborted by SIGNAME (ticket N removed)`, exit 130/143/129, run.log and holder text handled after the lock; `<offload-host> wait` uses the same handler. SIGINT cannot be tested from a background job (non-interactive bash ignores it); TERM and HUP are. The :143 memory-gate message still does not name the queue time (not changed; info).
- F7 fixed: head-of-line semantics, message change and numbering documented in the tools/heavy-job header. docs/WORKFLOW.md not touched.
- F8 fixed: `stickets` matches any digit run and sorts numerically. Row: 999999.lh 1000000.lB 1000001.lC in order.
- F9 fixed: tools/gate-xnu-build:44 comment corrected. [obs] bash 5.2.37: `trap 'echo EXIT' EXIT; sleep 5` ran the EXIT trap after kill -TERM and -HUP; with `trap 'exit 143' TERM` the trap waits for the foreground `sleep 3` to return (3.0 s).
- Unfixed rows against the old script (same tests, d9d057e heavy-job): 14 FAIL (one is a check-all.sh row that also fails in the temp copy of tools/) incl. F1, F2, F3 (spin), F5, F6 HUP, F8, <offload-host> .seq. [obs]
