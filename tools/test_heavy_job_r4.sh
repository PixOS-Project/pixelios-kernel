#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'r4' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# ---- r4 (review heavy-fifo-r4): F1 daemon-style session member, F2 SIGKILL during killcmd's grace, F3 leader killed alone, F4 mutant rows, F5 env must not change CMD
# Every process these rows start is either waited for or has its pid in $w/*.pid (cleanup() kills only those); no pkill/pgrep sweeps.
# F1: CMD exits on SIGTERM but leaves a TERM-ignoring background child with stdout/stderr closed (so bp ends and the old escalation never ran): heavy-job SIGKILLed.
# Expect: lock free after about 1 s + grace 2 s, the child gone, run.log says why (mutant: drop the flock wait at the end of cmd_inner)
rm -f "$w/r4d.pid"
cat >"$w/r4d.sh" <<'SH'
bash -c 'trap "" TERM; echo $$ >"$1"; exec sleep 52.31' _ "$1" >/dev/null 2>&1 &
exec sleep 53.4
SH
s=$(date +%s.%N)
HEAVY_JOB_KILL_GRACE=2 timeout -s KILL 1 "$here/tools/heavy-job" run --class analysis --label r4d --wait 5 -- bash "$w/r4d.sh" "$w/r4d.pid" >"$w/r4d.out" 2>&1
freewait 10; el=$(elapsed "$s")
ok "r4 F1: group SIGKILL, CMD leaves a TERM-ignoring daemon child: lock free after ${el}s (1 s + 2 s grace), daemon gone, 'session members still hold the lock' in run.log" "$([ -s "$w/r4d.pid" ] && [ $lockfree = 1 ] && awk -v e="$el" 'BEGIN{exit !(e>=2.5 && e<9)}' && ! kill -0 "$(cat "$w/r4d.pid")" 2>/dev/null && grep -q 'session members still hold the lock 2 s after heavy-job died, sending SIGKILL' logs/heavy-job/r4d/run.log && echo 1 || echo 0)"
# F2: TERM, then SIGKILL of heavy-job 1.5 s later while CMD ignores TERM (grace 6): the leader must survive the abort's group TERM so its pdeathsig fires (mutant: drop `trap '' TERM` at the top of cmd_inner)
rm -f "$w/r4k.ready" "$w/r4k.pid"
HEAVY_JOB_KILL_GRACE=6 "$here/tools/heavy-job" run --class analysis --label r4k --wait 30 -- bash -c 'trap "" TERM; echo $$ >'"$w"'/r4k.pid; echo 1 >'"$w"'/r4k.ready; exec sleep 54.7' >"$w/r4k.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r4k.ready" ] && break; sleep 0.1; done
s=$(date +%s.%N); kill -TERM "$hp"; sleep 1.5; kill -KILL "$hp" 2>/dev/null; { wait "$hp"; } 2>/dev/null
freewait 14; el=$(elapsed "$s")
ok "r4 F2: TERM then SIGKILL of heavy-job 1.5 s apart, CMD ignores TERM (grace 6): lock free after ${el}s, CMD gone, 'sending SIGKILL' in run.log" "$([ -s "$w/r4k.pid" ] && [ $lockfree = 1 ] && awk -v e="$el" 'BEGIN{exit !(e>=5.5 && e<13)}' && ! kill -0 "$(cat "$w/r4k.pid")" 2>/dev/null && grep -q 'CMD ignored SIGTERM for 6 s after heavy-job died, sending SIGKILL' logs/heavy-job/r4k/run.log && echo 1 || echo 0)"
# F3: SIGKILL of the CMD leader alone (its session id is its pid): CMD must not keep running and holding the lock under a plain FAIL line (mutant: restore `wait "$cg"; rc=$?; cg=`)
rm -f "$w/r4l.pid"
"$here/tools/heavy-job" run --class analysis --label r4l --wait 30 -- bash -c 'echo $$ >'"$w"'/r4l.pid; exec sleep 55.8' >"$w/r4l.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r4l.pid" ] && break; sleep 0.1; done
cp=$(cat "$w/r4l.pid" 2>/dev/null); sid=$(ps -o sid= -p "${cp:-0}" 2>/dev/null | tr -d ' ')
[ -z "$sid" ] || [ "$sid" = "$hp" ] || kill -KILL "$sid"; { wait "$hp"; } 2>/dev/null; rcs=$?
freewait 3
ok "r4 F3: CMD leader (pid ${sid:-?}) SIGKILLed alone: exit 1 (got $rcs), CMD (pid $cp) terminated, lock free, WARNING in output AND run.log, RESULT rc=137" "$([ -n "$sid" ] && [ "$rcs" = 1 ] && [ $lockfree = 1 ] && ! kill -0 "${cp:-0}" 2>/dev/null && grep -q 'WARNING: CMD leader ended with rc=137 (signal 9) but its session is still running' "$w/r4l.out" && grep -q 'WARNING: CMD leader ended with rc=137' logs/heavy-job/r4l/run.log && has '^RESULT: FAIL label=r4l class=analysis rc=137 ' "$(cat "$w/r4l.out")" | grep -q 1 && echo 1 || echo 0)"
# F3b / M08: ... and a TERM to heavy-job while the new killcmd (leader already gone, CMD ignores TERM, grace 3) is running must still end CMD (mutant: `cg=` as the first statement of killcmd: the nested sabort then sees no CMD)
rm -f "$w/r4m.pid"
HEAVY_JOB_KILL_GRACE=3 "$here/tools/heavy-job" run --class analysis --label r4m --wait 30 -- bash -c 'trap "" TERM; echo $$ >'"$w"'/r4m.pid; exec sleep 54.8' >"$w/r4m.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r4m.pid" ] && break; sleep 0.1; done
cp=$(cat "$w/r4m.pid" 2>/dev/null); sid=$(ps -o sid= -p "${cp:-0}" 2>/dev/null | tr -d ' ')
[ -z "$sid" ] || [ "$sid" = "$hp" ] || kill -KILL "$sid"
sleep 0.7; kill -TERM "$hp" 2>/dev/null; { wait "$hp"; } 2>/dev/null; rcs=$?
freewait 3
ok "r4 F3: TERM to heavy-job while it ends a leaderless CMD that ignores TERM (grace 3): exit 143 (got $rcs), CMD (pid $cp) gone, lock free, '(CMD killed)'" "$([ -n "$sid" ] && [ "$rcs" = 143 ] && [ $lockfree = 1 ] && ! kill -0 "${cp:-0}" 2>/dev/null && has '^RESULT: FAIL heavy-queue label=r4m aborted by SIGTERM (CMD killed)' "$(cat "$w/r4m.out")" | grep -q 1 && echo 1 || echo 0)"
# F3c / mutant: restoring the wait on the already reaped leader: a leaderless CMD with a 1 s SIGTERM cleanup gets its grace (5 s): cleanup finished, no 'ignored SIGTERM' (mutant: `reaped` not honoured by killcmd)
rm -f "$w/r4n.pid" "$w/r4n.cleaned"
HEAVY_JOB_KILL_GRACE=5 "$here/tools/heavy-job" run --class analysis --label r4n --wait 30 -- bash -c 'echo $$ >'"$w"'/r4n.pid; trap "sleep 1; echo cleaned >'"$w"'/r4n.cleaned; exit 0" TERM; sleep 31.76 & wait' >"$w/r4n.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r4n.pid" ] && break; sleep 0.1; done
cp=$(cat "$w/r4n.pid" 2>/dev/null); sid=$(ps -o sid= -p "${cp:-0}" 2>/dev/null | tr -d ' ')
s=$(date +%s.%N); [ -z "$sid" ] || [ "$sid" = "$hp" ] || kill -KILL "$sid"; { wait "$hp"; } 2>/dev/null; rcs=$?; el=$(elapsed "$s")
freewait 3
ok "r4 F3: leaderless CMD with a 1 s SIGTERM cleanup gets its grace and heavy-job returns when the session is gone (${el}s < 4, not the full 5 s grace): cleanup finished, no 'ignored SIGTERM' warning, lock free, exit 1 (got $rcs)" "$([ -n "$sid" ] && [ "$rcs" = 1 ] && awk -v e="$el" 'BEGIN{exit !(e<4)}' && [ $lockfree = 1 ] && [ -s "$w/r4n.cleaned" ] && ! grep -q 'ignored SIGTERM' "$w/r4n.out" && echo 1 || echo 0)"
# F4 / M17: an abort of a CMD that exits on TERM returns at once with the default grace (mutant: `kill -KILL "$ks"` -> `kill "$ks"`: sabort ignores TERM, so every abort waited the full grace)
rm -f "$w/r4q.ready" "$w/r4q.pid"
"$here/tools/heavy-job" run --class analysis --label r4q --wait 30 -- bash -c 'echo $$ >'"$w"'/r4q.pid; echo 1 >'"$w"'/r4q.ready; exec sleep 31.73' >"$w/r4q.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r4q.ready" ] && break; sleep 0.1; done
s=$(date +%s.%N); kill -TERM "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?; el=$(elapsed "$s")
ok "r4 F4: abort of a CMD that dies on SIGTERM, default grace: returns after ${el}s (< 3 s), exit 143 (got $rcs)" "$([ "$rcs" = 143 ] && awk -v e="$el" 'BEGIN{exit !(e<3)}' && ! kill -0 "$(cat "$w/r4q.pid")" 2>/dev/null && echo 1 || echo 0)"
# F4 / M10: whole seconds 1..3600 (3601 and 9999 pass the 4-digit regex and need the -le check); M12: a setpriv without --pdeathsig support; M14: the shipped minimum bash; M23/M24: the one default literal reaches CMD
good=1
for g in 3601 9999; do
	out=$(HEAVY_JOB_KILL_GRACE=$g "$here/tools/heavy-job" run --class analysis --label r4g --wait 5 -- echo should-not-run 2>&1); rc=$?
	{ [ $rc = 2 ] && has '^RESULT: ERROR usage: HEAVY_JOB_KILL_GRACE must be whole seconds' "$out" | grep -q 1 && ! grep -q 'should-not-run$' <<<"$out"; } || { good=0; echo "  bad grace '$g': rc=$rc $out"; }
done
ok "r4 F4: HEAVY_JOB_KILL_GRACE 3601 / 9999 -> RESULT: ERROR usage exit 2, CMD not run" "$good"
mkdir -p "$w/path-badpriv"; for f in /usr/bin/*; do [ "${f##*/}" = setpriv ] || ln -sf "$f" "$w/path-badpriv/" 2>/dev/null; done; printf '#!/bin/sh\nexit 1\n' >"$w/path-badpriv/setpriv"; chmod +x "$w/path-badpriv/setpriv"
out=$(PATH=$w/path-badpriv "$here/tools/heavy-job" run --class analysis --label r4f --wait 5 -- echo should-not-run 2>&1); rc=$?
ok "r4 F4: setpriv without --pdeathsig -> RESULT: ERROR 'setpriv --pdeathsig is not supported', exit 2, no ticket, CMD not run" "$([ $rc = 2 ] && has '^RESULT: ERROR setpriv --pdeathsig is not supported' "$out" | grep -q 1 && ! grep -q 'should-not-run$\|queue: ticket' <<<"$out" && echo 1 || echo 0)"
ok "r4 F4: the shipped minimum bash is 5.1 (source pin: BASH_VERSINFO is read-only, a 5.2 host cannot exercise the default)" "$(grep -q 'HEAVY_JOB_MIN_BASH:-501' "$here/tools/heavy-job" && echo 1 || echo 0)"
out=$(env -u HEAVY_JOB_KILL_GRACE "$here/tools/heavy-job" run --class analysis --label r4g2 --wait 5 -- bash -c 'echo grace=$HEAVY_JOB_KILL_GRACE' 2>&1); rc=$?
ok "r4 F4: unset HEAVY_JOB_KILL_GRACE -> CMD sees the effective default grace=10 (the one literal)" "$([ $rc = 0 ] && has '^grace=10$' "$out")"
# F4 / M16: SIGTERM in the few ms before setsid has run (a setsid stub that sleeps 2 s): the session group does not exist yet, so killcmd must signal the leader pid itself; CMD must never start
mkdir -p "$w/path-slowsetsid"; printf '#!/bin/sh\nsleep 2\nexec %s "$@"\n' "$(command -v setsid)" >"$w/path-slowsetsid/setsid"; chmod +x "$w/path-slowsetsid/setsid"
rm -f "$w/r4s.pid"
PATH=$w/path-slowsetsid:$PATH "$here/tools/heavy-job" run --class analysis --label r4s --wait 30 -- bash -c 'echo $$ >'"$w"'/r4s.pid; exec sleep 31.74' >"$w/r4s.out" 2>&1 & hp=$!
for _ in $(seq 100); do grep -q 'lock acquired' "$w/r4s.out" && break; sleep 0.1; done
sleep 0.3; s=$(date +%s.%N); kill -TERM "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?; el=$(elapsed "$s")
freewait 5
ok "r4 F4: SIGTERM before setsid ran: exit 143 (got $rcs) after ${el}s (< 4 s), CMD never started, lock free" "$([ "$rcs" = 143 ] && awk -v e="$el" 'BEGIN{exit !(e<4)}' && [ ! -e "$w/r4s.pid" ] && [ $lockfree = 1 ] && echo 1 || echo 0)"
# F5: a leading NAME=VALUE or -option word is not passed to env (it would change what CMD means; `-i` was a green run that ran nothing): RESULT: ERROR usage, nested or not
good=1
for first in FOO=bar -i; do for held in '' outer; do
	out=$(HEAVY_JOB_HELD=$held "$here/tools/heavy-job" run --class analysis --label r4e --wait 5 -- "$first" bash -c 'echo should-not-run' 2>&1); rc=$?
	{ [ $rc = 2 ] && has "^RESULT: ERROR usage: CMD must start with a program name, not '$first'" "$out" | grep -q 1 && ! grep -q 'should-not-run$\|queue: ticket' <<<"$out"; } || { good=0; echo "  first '$first' held '$held': rc=$rc $out"; }
done; done
ok "r4 F5: CMD starting with FOO=bar or -i -> RESULT: ERROR usage exit 2, CMD not run, nested or not" "$good"
# r2 F3: abort AFTER the lock (the memory gate before exec): run.log ends with the verdict, holder text cleared, ticket gone (mutant: drop the rlog_add/lock clear in sabort)
cp "$w/mem.big" "$w/mem.pl"
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label plh --wait 30 -- bash -c 'echo 1 >'"$w"'/plh.start; sleep 3' >/dev/null 2>&1 ) & echo $! >"$w/plh.pid"
for _ in $(seq 100); do [ -s "$w/plh.start" ] && break; sleep 0.1; done
HEAVY_JOB_MEMINFO=$w/mem.pl HEAVY_JOB_POLL=30 "$here/tools/heavy-job" run --class build --label plabort --wait 60 -- echo should-not-run >"$w/plabort.out" 2>&1 & hp=$!
for _ in $(seq 100); do grep -q 'lock held by' "$w/plabort.out" && break; sleep 0.1; done
cp "$w/mem.small" "$w/mem.pl"   # gate 1 passed on mem.big, the flock wait started: now memory drops, so gate 2 (before exec) will hold the lock and wait
wait "$(cat "$w/plh.pid")" 2>/dev/null
for _ in $(seq 100); do grep -q 'waiting (before exec)' "$w/plabort.out" && break; sleep 0.1; done
kill -TERM "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?
res=$(grep '^RESULT: ' "$w/plabort.out" | tail -n 1)
ok "F3: SIGTERM after the lock (waiting before exec): exit 143 (got $rcs), RESULT: FAIL aborted by SIGTERM last in run.log, lock text cleared, no ticket, CMD not run" "$([ "$rcs" = 143 ] && grep -q 'waiting (before exec)' "$w/plabort.out" && has '^RESULT: FAIL heavy-queue label=plabort aborted by SIGTERM' "$res" | grep -q 1 && [ "$(tail -n 1 logs/heavy-job/plabort/run.log)" = "$res" ] && [ ! -s "$HEAVY_JOB_LOCK" ] && [ -z "$(ls "$w"/hq/*.plabort 2>/dev/null)" ] && ! grep -q should-not-run "$w/plabort.out" && echo 1 || echo 0)"
# r2 F5: a relative HEAVY_JOB_LOCK (no slash) is resolved once: lock, queue and the printed paths are absolute and sit next to each other, from any cwd
mkdir -p "$w/cwdA" "$w/cwdB"; good=1
for d in cwdA cwdB; do
	out=$(cd "$w/$d" && env -u HEAVY_JOB_QUEUE_DIR HEAVY_JOB_LOCK=rel.lock "$here/tools/heavy-job" status 2>&1)
	o2=$(cd "$w/$d" && env -u HEAVY_JOB_QUEUE_DIR HEAVY_JOB_LOCK=rel.lock "$here/tools/heavy-job" run --class analysis --label relq --wait 5 -- bash -c 'ls "$1"/heavy-queue; ls -l /proc/$$/fd/7' _ "$w/$d" 2>&1)
	{ has "^heavy-job: lock free ($w/$d/rel.lock)\$" "$out" | grep -q 1 && has "local queue empty ($w/$d/heavy-queue)" "$out" | grep -q 1 && has '^[0-9]\{6\}\.relq$' "$o2" | grep -q 1 && has " -> $w/$d/rel.lock\$" "$o2" | grep -q 1; } || good=0
done
ok "F5: relative HEAVY_JOB_LOCK=rel.lock from two cwds: absolute lock + <cwd>/heavy-queue, ticket and fd 7 resolve there" "$good"


if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
