#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'fifo' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# ---- local FIFO (arrival order on the local lock): ticketed holder + waiters, no network. Queue dir = $HEAVY_JOB_QUEUE_DIR ($w/hq), set at the top.
good=1
for _ in 1 2 3; do   # three waiters, three rounds: a random wake-up order passes one round with p=1/6, all three with p=1/216
	: >"$w/lorder"; holdr; startl B; lqwait 2; startl C; lqwait 3; startl D; lqwait 4
	waitl lh lB lC lD
	[ "$(tr -d '\n' <"$w/lorder")" = BCD ] || good=0
done
ok "local FIFO: B C D arrived behind a holder, ran in arrival order BCD (3 rounds)" "$good"
ok "local FIFO: waiter prints 'heavy-job: queue: waiting behind NNNNNN.label (...)'" "$(has '^heavy-job: queue: waiting behind [0-9]\{6\}\.lh (' "$(cat "$w/lB.out")")"
ok "local FIFO: no ticket is left behind after the runs" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"
# killed middle waiter is skipped
: >"$w/lorder"; holdr; startl B; lqwait 2; startl C; lqwait 3; startl D; lqwait 4
kill -9 "$(cat "$w/lC.pid")"; sleep 0.3
waitl lh lB lD
ok "local FIFO: killed middle waiter (kill -9) skipped: B then D ran, C never, no ticket left" "$([ "$(tr -d '\n' <"$w/lorder")" = BD ] && [ "$(lq)" = 0 ] && echo 1 || echo 0)"
# status: holder + waiters in order, stale ticket marked; then the sweep
echo 0 >"$w/hq/.seq"
: >"$w/lorder"; holdr; startl B; lqwait 2; startl C; lqwait 3; : >"$w/hq/000009.dead"   # created last: a later stake would sweep it
hj status
ok "status lists the local queue: holder, waiter #1, waiter #2 in order, stale ticket marked, lock held line unchanged" "$(echo "$(has 'local holder: owner=[^ ]* label=lh pid=[0-9]* age=' "$out")$(has 'local waiter #1: .*label=lB ' "$out")$(has 'local waiter #2: .*label=lC ' "$out")$(has '000009.dead STALE' "$out")$(has 'lock held by: lh analysis' "$out")" | grep -q 11111 && echo 1 || echo 0)"
waitl lh lB lC
[ -e "$w/hq/000009.dead" ] || echo "note: stale ticket vanished before the sweep run"
hj run --class analysis --label lsweep --wait 5 -- true
ok "stale ticket (owner dead, sorts after the live ones) swept by the next taker; queue empty again" "$([ $rc = 0 ] && [ ! -e "$w/hq/000009.dead" ] && [ "$(lq)" = 0 ] && [ "$(tr -d '\n' <"$w/lorder")" = BC ] && echo 1 || echo 0)"
hj status
ok "status with an empty queue: 'lock free' wording unchanged + 'local queue empty'" "$(echo "$(has "^heavy-job: lock free ($HEAVY_JOB_LOCK)\$" "$out")$(has 'local queue empty' "$out")" | grep -q 11 && echo 1 || echo 0)"
# wait expiry inside the queue
holdr
hj run --class analysis --label limp --wait 2 -- echo should-not-run
ok "wait expiry in the queue: RESULT: ERROR heavy-queue naming position and job, exit 2, CMD not run, ticket gone" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR heavy-queue: waited 2s for [0-9]\{6\}\.lh, not our turn (queue position 2)$' "$out")$(has should-not-run "$out")$([ "$(lq)" = 1 ] && echo 1 || echo 0)" | grep -q 101 && echo 1 || echo 0)"
ok "wait expiry in the queue: nothing written to run.log (never touches another job's log)" "$([ ! -s logs/heavy-job/limp/run.log ] && echo 1 || echo 0)"
waitl lh
ok "holder's ticket released at its end" "$([ "$(lq)" = 0 ] && echo 1 || echo 0)"
# nested run takes no ticket
out=$(timeout 20 "$here/tools/heavy-job" run --class gate --label nout --wait 5 -- "$here/tools/heavy-job" run --class build --label nin --wait 1 -- bash -c 'ls "$HEAVY_JOB_QUEUE_DIR"/[0-9]*.* | tr "\n" " "; echo' 2>&1); rc=$?
ok "nested run: no second ticket (only the outer's ticket exists while the inner runs)" "$([ $rc = 0 ] && echo "$(has '/[0-9]\{6\}\.nout $' "$out")$(has 'nested in nout' "$out")" | grep -q 11 && ! grep -q 'nin ' <<<"$out" && echo 1 || echo 0)"
# queue dir unusable: loud, no silent fallback to the plain flock (a regular file as parent makes mkdir fail even as root)
: >"$w/notdir"
out=$(HEAVY_JOB_QUEUE_DIR=$w/notdir/q "$here/tools/heavy-job" run --class gate --label qbad --wait 5 -- echo should-not-run 2>&1); rc=$?
ok "unwritable queue dir: RESULT: ERROR heavy-queue: cannot create, exit 2, CMD not run, lock never taken" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR heavy-queue: cannot create' "$out")$(has should-not-run "$out")$([ ! -s "$HEAVY_JOB_LOCK" ] && echo 1 || echo 0)" | grep -q 101 && echo 1 || echo 0)"
# memory gate while head of the queue: a later job cannot overtake a head that waits for memory
cp "$w/mem.small" "$w/mem.fifo"
( HEAVY_JOB_MEMINFO=$w/mem.fifo "$here/tools/heavy-job" run --class build --label mh --wait 30 -- bash -c 'echo H >>'"$w"'/lorder' >"$w/mh.out" 2>&1 ) & echo $! >"$w/mh.pid"
lqwait 1; : >"$w/lorder"
( HEAVY_JOB_MEMINFO=$w/mem.big "$here/tools/heavy-job" run --class build --label ml --wait 30 -- bash -c 'echo L >>'"$w"'/lorder' >"$w/ml.out" 2>&1 ) & echo $! >"$w/ml.pid"
lqwait 2; sleep 3; cp "$w/mem.big" "$w/mem.fifo"
wait "$(cat "$w/mh.pid")" 2>/dev/null; wait "$(cat "$w/ml.pid")" 2>/dev/null
ok "head of the queue waiting for memory is not overtaken by a later job that would pass the gate (order H L)" "$([ "$(tr -d '\n' <"$w/lorder")" = HL ] && echo 1 || echo 0)"


# ---- review heavy-fifo-r1 fixes (F1-F8). Each row below was checked to FAIL against the unfixed code or the named mutant (docs/reviews/heavy-fifo-r1.md "r1 fixes").
# F1: .seq empty / garbled / deleted / behind must never stop the lane (exit 2) nor restart numbering below a live ticket
printf '' >"$w/hq/.seq"
hj run --class analysis --label seq-empty --wait 5 -- echo seq-empty-ran
ok "F1: empty .seq (no live ticket): run proceeds rc 0, loud '.seq repaired to 1 (empty)', .seq rewritten numeric" "$([ $rc = 0 ] && echo "$(has 'queue: \.seq repaired to 1 (empty)' "$out")$(has '^seq-empty-ran$' "$out")$([ "$(cat "$w/hq/.seq")" = 1 ] && echo 1 || echo 0)" | grep -q 111 && echo 1 || echo 0)"
seqcase deleted DELETE 'missing while a live ticket'
seqcase garbled 'abc!' "garbled 'abc!'"
seqcase "too long to be a number" 99999999999999999999999 'garbled'
seqcase empty '' 'empty'
seqcase "behind the live ticket" 1 'behind the highest live ticket'
# F8: numbers past 999999: the 7-digit tickets must stay visible and ordered numerically (a 6-digit glob lost them)
echo 999998 >"$w/hq/.seq"; : >"$w/lorder"; holdr; startl B; lqwait 2; startl C; lqwait 3
n1=$(basename "$(ls "$w"/hq/*.lh)"); n2=$(basename "$(ls "$w"/hq/*.lB)"); n3=$(basename "$(ls "$w"/hq/*.lC)")
waitl lh lB lC
ok "F8: tickets 999999.lh 1000000.lB 1000001.lC (got $n1 $n2 $n3): B waits behind the holder, C behind B, order BC" "$([ "$n1" = 999999.lh ] && [ "$n2" = 1000000.lB ] && [ "$n3" = 1000001.lC ] && [ "$(tr -d '\n' <"$w/lorder")" = BC ] && has 'waiting behind 999999\.lh' "$(cat "$w/lB.out")" | grep -q 1 && has 'waiting behind 1000000\.lB' "$(cat "$w/lC.out")" | grep -q 1 && echo 1 || echo 0)"
rm -f "$w/hq/.seq"
# F2: a held queue .lock makes run give up within the --wait budget (it used to hang in an unbounded flock)
flock -x "$w/hq/.lock" sleep 5 & echo $! >"$w/ql.pid"; sleep 0.3
s=$(date +%s.%N); hj run --class analysis --label qlk --wait 1 -- echo should-not-run; el=$(elapsed "$s")
ok "F2: queue .lock held: RESULT: ERROR heavy-queue: cannot lock within 1s, exit 2, gave up after ${el}s (< 3), CMD not run, no ticket" "$([ $rc = 2 ] && awk -v e="$el" 'BEGIN{exit !(e<3)}' && has '^RESULT: ERROR heavy-queue: cannot lock .* within 1s' "$out" | grep -q 1 && ! grep -q should-not-run <<<"$out" && [ "$(lq)" = 0 ] && echo 1 || echo 0)"
kill "$(cat "$w/ql.pid")" 2>/dev/null; wait "$(cat "$w/ql.pid")" 2>/dev/null
# F3: an existing predecessor ticket that cannot be opened is a loud error, not a 100 percent CPU spin that ignores --wait (chmod 000 does not stop root)
if [ "$(id -u)" = 0 ]; then echo "note: F3 row skipped (running as root: chmod 000 does not stop root)"
else
	: >"$w/hq/000999.unread"; chmod 000 "$w/hq/000999.unread"; echo 999 >"$w/hq/.seq"
	s=$(date +%s.%N); out=$(timeout 15 "$here/tools/heavy-job" run --class analysis --label unr --wait 30 -- echo should-not-run 2>&1); rc=$?; el=$(elapsed "$s")
	ok "F3: unopenable predecessor ticket: RESULT: ERROR heavy-queue: cannot open ... 000999.unread, exit 2 after ${el}s (not the 30s budget, not a spin), CMD not run" "$([ $rc = 2 ] && awk -v e="$el" 'BEGIN{exit !(e<5)}' && has '^RESULT: ERROR heavy-queue: cannot open the predecessor ticket 000999\.unread' "$out" | grep -q 1 && ! grep -q should-not-run <<<"$out" && echo 1 || echo 0)"
	# r2 F7: the unreadable (another user's, in real life) ticket is reported loudly by the sweep, by the error and by status, never skipped silently
	ok "F7: unreadable ticket: run prints a WARNING naming it with the rm hint, and the error repeats the hint" "$(echo "$(has 'WARNING: queue: ticket 000999\.unread is unreadable' "$out")$(has 'rm .*000999\.unread' "$out")$(has 'remove by hand if its owner is gone: rm .*000999\.unread' "$out")" | grep -q 111 && echo 1 || echo 0)"
	hj status
	ok "F7: status lists the unreadable ticket as UNREADABLE with the rm hint" "$(has 'local queue: 000999\.unread UNREADABLE .*rm .*000999\.unread' "$out")"
	rm -f "$w/hq/000999.unread"
fi
# r2 F7: a dead ticket that cannot be removed (fake rm refuses it: stands for a ticket of another user in a directory we cannot write) is a loud error, not a spin
mkdir -p "$w/fakerm"; realrm=$(command -v rm)
printf '#!/bin/bash\nfor a; do case $a in *.f7dead) exit 1 ;; esac; done\nexec %s "$@"\n' "$realrm" >"$w/fakerm/rm"; chmod +x "$w/fakerm/rm"
: >"$w/hq/000500.f7dead"; echo 500 >"$w/hq/.seq"
s=$(date +%s.%N); out=$(PATH="$w/fakerm:$PATH" timeout 15 "$here/tools/heavy-job" run --class analysis --label f7 --wait 30 -- echo should-not-run 2>&1); rc=$?; el=$(elapsed "$s")
ok "F7: dead ticket that cannot be removed: WARNING at the sweep + RESULT: ERROR heavy-queue: dead predecessor ticket ... cannot be removed, exit 2 after ${el}s (not a spin), CMD not run" "$([ $rc = 2 ] && awk -v e="$el" 'BEGIN{exit !(e<5)}' && echo "$(has 'WARNING: queue: dead ticket 000500\.f7dead cannot be removed' "$out")$(has '^RESULT: ERROR heavy-queue: dead predecessor ticket 000500\.f7dead cannot be removed' "$out")" | grep -q 11 && ! grep -q should-not-run <<<"$out" && echo 1 || echo 0)"
"$realrm" -f "$w/hq/000500.f7dead" "$w/hq/.seq"
# F4a (mutant: drop 9>&- on CMD): CMD must not inherit the ticket lock fd (a daemon CMD child would keep a dead job's ticket alive). The CMD lists its own fds.
# (The forked pipeline subshell that runs CMD does keep fd 9 until CMD ends: harmless, CMD holds the real lock on fd 7 anyway; so the CMD's own fds are the check.)
hj run --class analysis --label ldm --wait 30 -- bash -c 'ls -l /proc/$$/fd/'
ok "F4a: CMD has no fd on the ticket file in $w/hq (9>&- on CMD), nor on the queue .lock (8>&-)" "$([ $rc = 0 ] && ! grep -q "$w/hq/" <<<"$out" && grep -q ' 7 -> ' <<<"$out" && echo 1 || echo 0)"
# F4b/F6: INT/TERM/HUP during a wait = RESULT: FAIL ... aborted by SIGNAME, exit 128+n, ticket gone, and no orphan flock/sleep child (mutant: drop the trap)
launch=()
holdr
sigcase sigq TERM 143 HEAVY_JOB_POLL=1
waitl lh
sigcase sigm HUP 129 HEAVY_JOB_MEMINFO="$w/mem.small" HEAVY_JOB_POLL=30
# r2 F4: INT -> 130. A background job of a non-interactive bash starts with SIGINT ignored, so the launcher resets it to the default before exec (mutant: 130 -> 129)
launch=(python3 -I -c 'import os,signal,sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])')
sigcase sigi INT 130 HEAVY_JOB_MEMINFO="$w/mem.small" HEAVY_JOB_POLL=30
launch=()
# F4c (mutant: reset t0 after the queue wait): --wait is ONE budget. Event-based (T-c7be): the holder is released 3 s after the job is seen waiting; with one budget of 14 s the job
# gives up at start+14, i.e. <= 11 s after the release; with a budget reset after the queue wait it would give up >= 14 s after the release. Load only shortens the first, never the second.
holdr
HEAVY_JOB_MEMINFO=$w/mem.small "$here/tools/heavy-job" run --class build --label bud --wait 14 -- echo should-not-run >"$w/bud.out" 2>&1 & echo $! >"$w/bud.pid"
for _ in $(seq 1500); do grep -q 'waiting behind' "$w/bud.out" && break; sleep 0.1; done
sleep 3; rel=$(date +%s.%N); : >"$w/lh.release"
wait "$(cat "$w/bud.pid")"; rc=$?; fin=$(date +%s.%N); out=$(cat "$w/bud.out"); el=$(awk -v a="$rel" -v b="$fin" 'BEGIN{printf "%.1f", b-a}')
waitl lh
ok "F4c: queue wait + closed gate, --wait 14: RESULT: ERROR memory-gate ${el}s after the holder's release (< 12: one budget; a reset budget gives >= 14), CMD not run" "$([ $rc = 2 ] && awk -v e="$el" 'BEGIN{exit !(e<12)}' && has '^RESULT: ERROR memory-gate' "$out" | grep -q 1 && ! grep -q should-not-run <<<"$out" && echo 1 || echo 0)"
# F5: HEAVY_JOB_LOCK alone moves the queue next to the lock; the shared queue ($w/hq) is not touched
mkdir -p "$w/alt"
out=$(env -u HEAVY_JOB_QUEUE_DIR HEAVY_JOB_LOCK="$w/alt/x.lock" "$here/tools/heavy-job" run --class analysis --label altq --wait 5 -- bash -c 'ls "$1"/heavy-queue' _ "$w/alt" 2>&1); rc=$?
ok "F5: only HEAVY_JOB_LOCK set: the ticket lives in <lock dir>/heavy-queue (seen while CMD runs), none in the shared queue" "$([ $rc = 0 ] && has '^[0-9]\{6\}\.altq$' "$out" | grep -q 1 && [ -z "$(ls "$w"/hq/*.altq 2>/dev/null)" ] && echo 1 || echo 0)"

# r2 F2: a signal DURING CMD: no orphan CMD (nor its child), RESULT line printed + last in run.log, lock text cleared, ticket gone, next job runs (mutant: restore `trap -`)
f2cmd f2term TERM 143 'sleep 60; :'
hj run --class analysis --label f2next --wait 5 -- echo f2-next-ran
ok "F2: the job after an aborted CMD takes the lock at once and runs" "$([ $rc = 0 ] && has f2-next-ran "$out" || echo 0)"
f2cmd f2hup HUP 129 'sleep 60; :'
# CMD that ignores SIGTERM (the ignore is inherited by its child): killed with SIGKILL after HEAVY_JOB_KILL_GRACE, said loudly
f2cmd f2kill TERM 143 'trap "" TERM; sleep 60; :' HEAVY_JOB_KILL_GRACE=2
ok "F2: CMD ignoring SIGTERM: loud 'ignored SIGTERM ... sending SIGKILL' line" "$(has 'WARNING: CMD ignored SIGTERM for 2 s, sending SIGKILL' "$(cat "$w/f2kill.out")")"
# CMD keeps its stdin (a bare `&` would give it /dev/null)
out=$(echo stdin-seen | "$here/tools/heavy-job" run --class analysis --label f2stdin --wait 5 -- cat 2>&1); rc=$?
ok "F2: CMD still reads the caller's stdin" "$([ $rc = 0 ] && has '^stdin-seen$' "$out" || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
