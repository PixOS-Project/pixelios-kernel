#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'basic' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2


hj run --class gate --label t-pass --wait 5 -- bash -c 'echo hello'
ok "run: rc 0 -> PASS exit 0, RESULT line, decision line carries mem_avail= psi= load1=" "$([ $rc = 0 ] && echo "$(has '^RESULT: PASS label=t-pass class=gate rc=0 elapsed=[0-9]*s$' "$out")$(has 'mem_avail=32.0G psi=0.50 load1=0.25' "$out")$(has hello "$out")" | grep -q 111 && echo 1 || echo 0)"
ok "run: log tee'd to logs/heavy-job/<label>/run.log" "$(grep -q hello logs/heavy-job/t-pass/run.log && echo 1 || echo 0)"
ok "run: absolute log path printed before the RESULT line" "$(has "^heavy-job: log $PWD/logs/heavy-job/t-pass/run.log\$" "$out")"
ok "run outside git: fallback note line" "$(has 'not in a git checkout' "$out")"
# W34 R22: run.log ends with the same RESULT line the tool printed (PASS, FAIL, ERROR after the log dir exists, QUIET variant)
ok "run.log last line == printed RESULT line (PASS)" "$([ "$(tail -n 1 logs/heavy-job/t-pass/run.log)" = "$(grep '^RESULT: ' <<<"$out" | tail -n 1)" ] && has '^RESULT: PASS label=t-pass' "$(tail -n 1 logs/heavy-job/t-pass/run.log)" || echo 0)"
# AP-217: inside a git worktree the log lands in the MAIN clone (parent of the common git dir), not in the worktree
git init -q "$w/main" && git -C "$w/main" -c user.email=t@t -c user.name=t commit -q --allow-empty -m i && git -C "$w/main" worktree add -q "$w/wt" -b wt
( cd "$w/wt" && "$here/tools/heavy-job" run --class gate --label t-wt --wait 5 -- bash -c 'echo wt' >"$w/wt.out" 2>&1 )
ok "run in a worktree: log in the main clone, none in the worktree, path printed" "$([ -s "$w/main/logs/heavy-job/t-wt/run.log" ] && [ ! -e "$w/wt/logs" ] && grep -q "^heavy-job: log $w/main/logs/heavy-job/t-wt/run.log\$" "$w/wt.out" && echo 1 || echo 0)"
hj run --class vm --label t-fail --wait 5 -- bash -c 'exit 7'
ok "rc 7 -> FAIL exit 1" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL label=t-fail class=vm rc=7' "$out")" = 1 ] && echo 1 || echo 0)"
ok "run.log last line == printed RESULT line (FAIL rc=7)" "$([ "$(tail -n 1 logs/heavy-job/t-fail/run.log)" = "$(grep '^RESULT: ' <<<"$out" | tail -n 1)" ] && has '^RESULT: FAIL label=t-fail class=vm rc=7' "$(tail -n 1 logs/heavy-job/t-fail/run.log)" || echo 0)"
out=$(HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" run --class gate --label t-quiet --wait 5 -- bash -c 'echo q' 2>&1 >/dev/null); rc=$?
ok "QUIET: the heavy-job: RESULT line (stderr) is also the last line of run.log" "$([ $rc = 0 ] && [ "$(tail -n 1 logs/heavy-job/t-quiet/run.log)" = "$(grep '^heavy-job: RESULT ' <<<"$out" | tail -n 1)" ] && has '^heavy-job: RESULT PASS label=t-quiet' "$(tail -n 1 logs/heavy-job/t-quiet/run.log)" || echo 0)"
hj run --class mutant --label t-two --wait 5 -- bash -c 'exit 2'
ok "rc 2 -> FAIL exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: FAIL label=t-two class=mutant rc=2' "$out")" = 1 ] && echo 1 || echo 0)"
hj run --class bogus --label x -- true
ok "bad class -> RESULT: ERROR exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR usage' "$out")" = 1 ] && echo 1 || echo 0)"
hj run --class build --label x --wait 5
ok "missing CMD -> RESULT: ERROR exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR usage' "$out")" = 1 ] && echo 1 || echo 0)"
hj status
ok "status: lock free + mem_avail/psi/load1" "$([ $rc = 0 ] && echo "$(has 'lock free' "$out")$(has 'mem_avail=32.0G psi=0.50 load1=0.25' "$out")" | grep -q 11 && echo 1 || echo 0)"

# lock: a holder WITHOUT a ticket (own queue dir = stands for an older tools/heavy-job copy); a second run must wait on the flock, print the holder, then run after it (serialised: start of B >= end of A)
# T-c7be: the holder is released by an event (B seen waiting), not after a fixed 'sleep 4' that a loaded host can outlast before B even tries the lock
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label holder --wait 30 -- bash -c 'date +%s.%N >'"$w"'/a.start; for _ in $(seq 1200); do [ -e '"$w"'/a.release ] && break; sleep 0.1; done; date +%s.%N >'"$w"'/a.end' >"$w/a.out" 2>&1 ) & echo $! >"$w/a.pid"
for _ in $(seq 1000); do [ -s "$w/a.start" ] && break; sleep 0.1; done
hj status
ok "status while held shows the holder line" "$(has 'lock held by: holder analysis' "$out")"
( "$here/tools/heavy-job" run --class analysis --label second --wait 60 -- bash -c 'date +%s.%N >'"$w"'/b.start' >"$w/b.out" 2>&1; echo $? >"$w/b.rc" ) & echo $! >"$w/b.pid"
for _ in $(seq 1500); do grep -q 'lock held by: holder analysis' "$w/b.out" && break; sleep 0.1; done
: >"$w/a.release"
wait "$(cat "$w/b.pid")" 2>/dev/null; rc=$(cat "$w/b.rc"); out=$(cat "$w/b.out")
ok "second run waits, prints the holder, then PASSes" "$([ $rc = 0 ] && echo "$(has 'lock held by: holder analysis' "$out")$(has '^RESULT: PASS label=second' "$out")" | grep -q 11 && echo 1 || echo 0)"
ok "serialised: B started after A ended" "$(awk -v a="$(cat "$w/a.end")" -v b="$(cat "$w/b.start")" 'BEGIN{print (b>=a) ? 1 : 0}')"
wait "$(cat "$w/a.pid")" 2>/dev/null
# wait timeout
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label holder2 --wait 30 -- bash -c 'echo 1 >'"$w"'/h2.start; for _ in $(seq 1200); do [ -e '"$w"'/h2.release ] && break; sleep 0.1; done' >/dev/null 2>&1 ) & echo $! >"$w/c.pid"
for _ in $(seq 1000); do [ -s "$w/h2.start" ] && break; sleep 0.1; done   # T-c7be: wait until holder2 holds the lock (event), release it only after 'late' was refused
hj run --class analysis --label late --wait 10 -- true   # T-c7be: --wait 10 not 2: under load the start-up (queue ticket, INDEX read, memory gate) is charged to the same budget and can use up 2 s before the lock is even tried; the case still ends with the lock refusal after the budget
: >"$w/h2.release"
[ $rc = 2 ] && [ "$(has '^RESULT: ERROR heavy-job lock held by: holder2' "$out")" = 1 ] || echo "DIAG late rc=$rc out: $out"
ok "--wait timeout -> RESULT: ERROR heavy-job lock held by ..., exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR heavy-job lock held by: holder2' "$out")" = 1 ] && echo 1 || echo 0)"
# review heavy-runlog-r1 F1: a same-label run refused at the lock must not touch the holder's run.log
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label samelab --wait 30 -- bash -c 'echo A-start; for _ in $(seq 1200); do [ -e '"$w"'/samelab.release ] && break; sleep 0.1; done; echo A-end' >/dev/null 2>&1 ) & echo $! >"$w/d.pid"
for _ in $(seq 3000); do [ -s logs/heavy-job/samelab/run.log ] && grep -q samelab "$HEAVY_JOB_LOCK" 2>/dev/null && break; sleep 0.1; done   # T-d543: also wait until A holds the lock (A writes its label there), else B can win it under gate load
hj run --class analysis --label samelab --wait 10 -- echo B-ran   # T-c7be: --wait 10 not 1, same reason as 'late'; A holds the lock until B has been refused
: >"$w/samelab.release"   # T-c7be: A holds the lock until B has been refused (an event; under load B's start-up alone can outlast a fixed hold and B then wins the lock)
[ $rc = 2 ] && has '^RESULT: ERROR heavy-job lock held by' "$out" | grep -q 1 || echo "DIAG samelab rc=$rc out: $out"   # T-c7be: say what B printed when the case fails
ok "same label refused at the lock: ERROR printed, B did not run" "$([ $rc = 2 ] && has '^RESULT: ERROR heavy-job lock held by' "$out" | grep -q 1 && ! grep -q B-ran <<<"$out" && echo 1 || echo 0)"
wait "$(cat "$w/d.pid")" 2>/dev/null
ok "same label: the holder's run.log has no ERROR line, ends with its own PASS" "$([ "$(grep -c '^RESULT: ERROR' logs/heavy-job/samelab/run.log)" = 0 ] && has '^RESULT: PASS label=samelab' "$(tail -n 1 logs/heavy-job/samelab/run.log)" || echo 0)"
# review F2: an err() in the post-lock sample() must stop the job (meminfo vanishes while waiting for the lock), one ERROR line in run.log, CMD not run
cp "$w/mem.big" "$w/mem.tmp"
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label f2holder --wait 30 -- bash -c 'echo 1 >'"$w"'/f2h.start; for _ in $(seq 1200); do [ -e '"$w"'/f2h.release ] && break; sleep 0.1; done' >/dev/null 2>&1 ) & echo $! >"$w/e.pid"
for _ in $(seq 1000); do [ -s "$w/f2h.start" ] && break; sleep 0.1; done   # T-c7be: events instead of 'sleep 1 / sleep 2': holder has the lock -> f2 is seen waiting -> meminfo vanishes -> holder released
( HEAVY_JOB_MEMINFO=$w/mem.tmp "$here/tools/heavy-job" run --class analysis --label f2 --wait 60 -- bash -c 'echo F2-RAN' >"$w/f2.out" 2>&1; echo $? >"$w/f2.rc" ) & echo $! >"$w/f2.pid"
for _ in $(seq 1500); do grep -q 'lock held by' "$w/f2.out" && break; sleep 0.1; done
rm -f "$w/mem.tmp"; : >"$w/f2h.release"
wait "$(cat "$w/f2.pid")" 2>/dev/null; rc=$(cat "$w/f2.rc"); out=$(cat "$w/f2.out")
ok "post-lock sample() failure stops the job: rc 2, CMD not run" "$([ $rc = 2 ] && ! grep -q F2-RAN <<<"$out" && has '^RESULT: ERROR memory-gate' "$out" || echo 0)"
ok "post-lock sample() failure: exactly one ERROR line, and it is the last line of run.log" "$([ "$(grep -c '^RESULT: ' logs/heavy-job/f2/run.log)" = 1 ] && has '^RESULT: ERROR' "$(tail -n 1 logs/heavy-job/f2/run.log)" || echo 0)"
wait "$(cat "$w/e.pid")" 2>/dev/null
# review F3a: output without a final newline must not merge into the RESULT line
hj run --class gate --label t-nonl --wait 5 -- bash -c 'printf partial'
ok "no trailing newline: RESULT on its own line in run.log" "$([ "$(tail -n 1 logs/heavy-job/t-nonl/run.log | grep -c '^RESULT: PASS label=t-nonl')" = 1 ] && [ "$(grep -c '^partial$' logs/heavy-job/t-nonl/run.log)" = 1 ] && echo 1 || echo 0)"
# nested: with the lock held by this very chain, a nested run and a wired tool skip the lock (timeout proves no deadlock)
out=$(timeout 20 "$here/tools/heavy-job" run --class gate --label outer --wait 5 -- "$here/tools/heavy-job" run --class build --label inner --wait 1 -- echo inner-ran 2>&1); rc=$?
ok "nested run: no second lock, no deadlock, inner ran, both PASS" "$([ $rc = 0 ] && echo "$(has 'heavy-job: nested in outer, no second lock' "$out")$(has inner-ran "$out")$(has '^RESULT: PASS label=outer' "$out")" | grep -q 111 && echo 1 || echo 0)"
out=$(timeout 20 "$here/tools/heavy-job" run --class gate --label outer2 --wait 5 -- bash -c 'echo "held=$HEAVY_JOB_HELD"' 2>&1)
ok "child gets HEAVY_JOB_HELD=<label>" "$(has 'held=outer2' "$out")"
wait "$(cat "$w/c.pid")" 2>/dev/null

# load1 high guard (HEAVY_JOB_MAXLOAD1=6 here to keep the boundary cases; default is 3 x nproc, T-a8b4): 6.00 refused, 5.99 admitted, unreadable refused; restore 0.25 after
echo "6.00 1 1 1/1 1" >"$w/load"
HEAVY_JOB_MAXLOAD1=6 hj run --class build --label load6 --wait 2 -- echo should-not-run
ok "load1 6.00 (boundary) refused: ERROR memory-gate, reason names load1, CMD not run" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR memory-gate' "$out")$(has 'load1 6.00 >= 6' "$out")$(has should-not-run "$out")" | grep -q 110 && echo 1 || echo 0)"
ok "refused before the lock (memory gate): nothing written to run.log (it would belong to another job's log, review F1)" "$([ ! -s logs/heavy-job/load6/run.log ] && echo 1 || echo 0)"
echo "5.99 1 1 1/1 1" >"$w/load"
HEAVY_JOB_MAXLOAD1=6 hj run --class build --label load599 --wait 5 -- echo ran-load599
ok "load1 5.99 admitted" "$([ $rc = 0 ] && has ran-load599 "$out" || echo 0)"
echo "garbage" >"$w/load"
hj run --class build --label loadbad --wait 2 -- echo should-not-run
ok "load1 unreadable refused loudly" "$([ $rc = 2 ] && echo "$(has 'load1 unreadable' "$out")$(has should-not-run "$out")" | grep -q 10 && echo 1 || echo 0)"
echo ". 1 1 1/1 1" >"$w/load"
hj run --class build --label loaddot --wait 2 -- echo should-not-run
ok "load1 '.' refused as unreadable (review heavy-job-load1-r1 F2)" "$([ $rc = 2 ] && echo "$(has 'load1 unreadable' "$out")$(has should-not-run "$out")" | grep -q 10 && echo 1 || echo 0)"
echo "41.66 24 14 1/1 1" >"$w/load"
out=$(HEAVY_JOB_MAXLOAD1=50 "$here/tools/heavy-job" run --class build --label loadenv --wait 5 -- echo ran-loadenv 2>&1); rc=$?
ok "HEAVY_JOB_MAXLOAD1 override admits load1 41.66 < 50" "$([ $rc = 0 ] && has ran-loadenv "$out" || echo 0)"
echo "0.25 0.1 0.1 1/1 1" >"$w/load"
# T-a8b4: CPU rule = cpu PSI some avg10 < 20; load1 is only a high guard (3 x nproc) and is logged
nproc_n=$(nproc); echo "$((nproc_n * 2)).00 1 1 1/1 1" >"$w/load"
hj run --class build --label loadhi-cpuok --wait 5 -- echo ran-loadhi
ok "T-a8b4: load1 = 2 x nproc with cpu_psi 7.00 (disk wait) ADMITS, decision line carries cpu_psi=7.00" "$([ $rc = 0 ] && echo "$(has ran-loadhi "$out")$(has 'load1=.*cpu_psi=7.00' "$out")" | grep -q 11 && echo 1 || echo 0)"
HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label cpuhigh --wait 2 -- echo should-not-run
ok "T-a8b4: cpu_psi 28.76 >= 20 REFUSES, reason names cpu_psi, RESULT line carries cpu_psi=, CMD not run" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR memory-gate' "$out")$(has 'cpu_psi 28.76 >= 20' "$out")$(has 'cpu_psi=28.76' "$out")$(has should-not-run "$out")" | grep -q 1110 && echo 1 || echo 0)"
HEAVY_JOB_CPU_PSI=$w/cpu.bad HEAVY_JOB_CPU_PSI_MAX=30 hj run --class build --label cpumax --wait 5 -- echo ran-cpumax
ok "T-a8b4: HEAVY_JOB_CPU_PSI_MAX=30 admits cpu_psi 28.76" "$([ $rc = 0 ] && has ran-cpumax "$out" || echo 0)"
# T-ee35: the board writes HEAVY_JOB_CPU_PSI_MAX to HEAVY_JOB_CONF; heavy-job re-reads it on every poll
echo "HEAVY_JOB_CPU_PSI_MAX=30" >"$HEAVY_JOB_CONF"
HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label confok --wait 5 -- echo ran-confok
ok "T-ee35: limit file 30 admits cpu_psi 28.76 and the change is logged with its source" "$([ $rc = 0 ] && echo "$(has ran-confok "$out")$(has "cpu_psi limit now 30 (from $HEAVY_JOB_CONF, was 20 from default)" "$out")" | grep -q 11 && echo 1 || echo 0)"
echo "HEAVY_JOB_CPU_PSI_MAX=90" >"$HEAVY_JOB_CONF"
HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label confbad --wait 2 -- echo should-not-run
ok "T-ee35: limit file 90 (outside 5-80) is refused loudly (LIMIT GAP names value and file), limit stays 20, cpu_psi 28.76 refuses" "$([ $rc = 2 ] && echo "$(has "LIMIT GAP: $HEAVY_JOB_CONF: HEAVY_JOB_CPU_PSI_MAX='90' refused" "$out")$(has 'cpu_psi 28.76 >= 20' "$out")$(has should-not-run "$out")" | grep -q 110 && echo 1 || echo 0)"
echo "HEAVY_JOB_CPU_PSI_MAX=abc" >"$HEAVY_JOB_CONF"
HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label confnan --wait 2 -- echo should-not-run
ok "T-ee35: limit file 'abc' is refused loudly too" "$([ $rc = 2 ] && has "HEAVY_JOB_CPU_PSI_MAX='abc' refused" "$out" || echo 0)"
echo "HEAVY_JOB_CPU_PSI_MAX=30" >"$HEAVY_JOB_CONF"
HEAVY_JOB_CPU_PSI=$w/cpu.bad HEAVY_JOB_CPU_PSI_MAX=10 hj run --class build --label confenv --wait 2 -- echo should-not-run
ok "T-ee35: explicit env HEAVY_JOB_CPU_PSI_MAX=10 wins over the file (30): cpu_psi 28.76 >= 10 refuses" "$([ $rc = 2 ] && has 'cpu_psi 28.76 >= 10' "$out" || echo 0)"
cp "$here/tools/heavy-job.conf.example" "$HEAVY_JOB_CONF"; HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label confexample --wait 2 -- echo should-not-run
ok "T-ee35: the committed tools/heavy-job.conf.example parses (value 20 = the default): no LIMIT GAP, 28.76 >= 20 refuses" "$([ $rc = 2 ] && [ "$(has 'LIMIT GAP' "$out")" = 0 ] && has 'cpu_psi 28.76 >= 20' "$out" || echo 0)"
# a waiting gate picks the new value on its next poll (no restart): start with the default 20 (refused), write 30 after 2 s
rm -f "$HEAVY_JOB_CONF"; ( sleep 2; echo "HEAVY_JOB_CPU_PSI_MAX=30" >"$HEAVY_JOB_CONF" ) &
HEAVY_JOB_CPU_PSI=$w/cpu.bad hj run --class build --label confpoll --wait 20 -- echo ran-confpoll; wait
ok "T-ee35: a waiting gate re-reads the file on its next poll: refused at 20, admitted after the file says 30" "$([ $rc = 0 ] && echo "$(has ran-confpoll "$out")$(has 'cpu_psi limit now 30' "$out")" | grep -q 11 && echo 1 || echo 0)"
rm -f "$HEAVY_JOB_CONF"
echo "$((nproc_n * 3)).00 1 1 1/1 1" >"$w/load"
hj run --class build --label loadguard --wait 2 -- echo should-not-run
ok "T-a8b4: load1 = 3 x nproc (high guard) refuses, names load1" "$([ $rc = 2 ] && echo "$(has "load1 $((nproc_n * 3)).00 >= $((nproc_n * 3))" "$out")$(has should-not-run "$out")" | grep -q 10 && echo 1 || echo 0)"
HEAVY_JOB_CPU_PSI=$w/cpu.missing HEAVY_JOB_LOAD=$w/load hj run --class build --label cpumiss --wait 2 -- echo should-not-run
ok "T-a8b4: CPU PSI missing is loud and falls back to load1 < 6" "$([ $rc = 2 ] && echo "$(has 'CPU PSI unavailable' "$out")$(has "load1 $((nproc_n * 3)).00 >= 6" "$out")" | grep -q 11 && echo 1 || echo 0)"
echo "0.25 0.1 0.1 1/1 1" >"$w/load"

# memory gate
export HEAVY_JOB_MEMINFO=$w/mem.small
hj run --class build --label memlow --wait 2 -- echo should-not-run
ok "small MemAvailable blocks: RESULT: ERROR memory-gate mem_avail=4.0G need=4G psi= load1=, exit 2, CMD not run" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR memory-gate mem_avail=4.0G need=4G psi=0.50 load1=0.25' "$out")$(has 'mem_avail 4.0G < need 4G + reserve 8G' "$out")" | grep -q 11 && ! grep -q should-not-run <<<"$out" && echo 1 || echo 0)"
hj run --class build --label memtiny --mem-gb 1 --wait 2 -- echo ran-small
ok "--mem-gb 1 + reserve 8 = 9 > 4: still blocked" "$([ $rc = 2 ] && echo 1 || echo 0)"
HEAVY_JOB_RESERVE_GB=1 hj run --class build --label memres --mem-gb 1 --wait 2 -- echo ran-small
ok "reserve override 1 + need 1 <= 4: passes" "$([ $rc = 0 ] && [ "$(has ran-small "$out")" = 1 ] && echo 1 || echo 0)"
# ZFS ARC credit (arc_os.c arc_evictable_memory): 4 GB MemAvailable + ARC freeable must reach need 4 + reserve 8 = 12
G=1073741824
arcf $G $((12 * G)) $((5 * G)) $((4 * G))   # size 12, clean 9, dirty 3 >= c_min 1 -> freeable = clean 9 (ghost lists not counted)
HEAVY_JOB_ARCSTATS=$w/arc.cur hj run --class build --label arcok --wait 2 -- echo ran-arc
ok "ARC: dirty >= c_min -> credit = clean 9G, 4+9 >= 12 admits, stamp says arc_free=9.0G" "$([ $rc = 0 ] && echo "$(has ran-arc "$out")$(has 'mem_avail=13.0G arc_free=9.0G' "$out")" | grep -q 11 && echo 1 || echo 0)"
arcf $((4 * G)) $((10 * G)) $((5 * G)) $((4 * G))   # clean 9, dirty 1 < c_min 4 -> freeable = size - c_min = 6
HEAVY_JOB_ARCSTATS=$w/arc.cur hj run --class build --label arcmin --wait 2 -- echo should-not-run
ok "ARC: dirty < c_min -> credit = size - c_min 6G, 4+6 < 12 blocks" "$([ $rc = 2 ] && echo "$(has 'arc_free=6.0G' "$out")$(has 'mem_avail 10.0G < need 4G' "$out")" | grep -q 11 && ! grep -q should-not-run <<<"$out" && echo 1 || echo 0)"
arcf $G $((12 * G)) $((5 * G)) $((4 * G)); echo 5 >"$w/arc.pc5"
HEAVY_JOB_ARCSTATS=$w/arc.cur HEAVY_JOB_ARC_PC=$w/arc.pc5 hj run --class build --label arcpc --wait 2 -- echo should-not-run
ok "ARC: zfs_arc_pc_percent != 0 -> no credit, said loudly" "$([ $rc = 2 ] && echo "$(has 'arc=0(zfs_arc_pc_percent=5 unsupported)' "$out")$(has 'mem_avail=4.0G' "$out")" | grep -q 11 && echo 1 || echo 0)"
printf 'size 4 garbage\n' >"$w/arc.bad"
HEAVY_JOB_ARCSTATS=$w/arc.bad hj run --class build --label arcbad --wait 2 -- echo should-not-run
ok "ARC: unparsable arcstats -> no credit, said loudly" "$([ $rc = 2 ] && [ "$(has "arc=0(unparsable $w/arc.bad)" "$out")" = 1 ] && echo 1 || echo 0)"
export HEAVY_JOB_MEMINFO=$w/mem.big HEAVY_JOB_PSI=$w/psi.bad
hj run --class vm --label psihigh --wait 2 -- echo should-not-run
ok "PSI some avg10 >= 10 blocks with the reason" "$([ $rc = 2 ] && echo "$(has 'psi 55.00 >= 10' "$out")$(has '^RESULT: ERROR memory-gate' "$out")" | grep -q 11 && echo 1 || echo 0)"
export HEAVY_JOB_PSI=$w/psi.missing
hj run --class vm --label psimiss --wait 2 -- echo psi-missing-ran
ok "PSI missing is loud (PSI unavailable) and gates on memory only" "$([ $rc = 0 ] && echo "$(has 'PSI unavailable' "$out")$(has psi-missing-ran "$out")$(has 'psi=n/a' "$out")" | grep -q 111 && echo 1 || echo 0)"
export HEAVY_JOB_PSI=$w/psi.ok

# F5: the second memory gate (after the lock) and the holder reset: the waiter passes gate 1, then the memory drops while it waits for the lock
cp "$w/mem.big" "$w/mem.live"
( HEAVY_JOB_QUEUE_DIR=$w/hq-legacy "$here/tools/heavy-job" run --class analysis --label holder3 --wait 30 -- bash -c 'echo 1 >'"$w"'/h3.start; for _ in $(seq 1200); do [ -e '"$w"'/h3.release ] && break; sleep 0.1; done' >/dev/null 2>&1 ) & echo $! >"$w/h3.pid"
for _ in $(seq 1000); do [ -s "$w/h3.start" ] && break; sleep 0.1; done
( HEAVY_JOB_MEMINFO=$w/mem.live "$here/tools/heavy-job" run --class build --label gate2 --wait 20 -- echo second-gate-ran >"$w/g2.out" 2>&1; echo $? >"$w/g2.rc" ) & echo $! >"$w/g2.pid"
for _ in $(seq 1500); do grep -q 'lock held by' "$w/g2.out" && break; sleep 0.1; done   # T-c7be: gate2 passed gate 1 and waits for the lock (event), THEN memory drops, THEN the holder ends
cp "$w/mem.small" "$w/mem.live"; : >"$w/h3.release"
wait "$(cat "$w/g2.pid")" 2>/dev/null; wait "$(cat "$w/h3.pid")" 2>/dev/null
ok "second memory gate (after the lock): memory dropped while waiting => RESULT: ERROR memory-gate, exit 2, CMD not run" "$([ "$(cat "$w/g2.rc")" = 2 ] && grep -q 'RESULT: ERROR memory-gate' "$w/g2.out" && ! grep -q second-gate-ran "$w/g2.out" && grep -q 'before exec' "$w/g2.out" && echo 1 || echo 0)"
hj status
ok "after that ERROR the lock holder text is cleared and status says lock free" "$([ ! -s "$HEAVY_JOB_LOCK" ] && [ "$(has 'lock free' "$out")" = 1 ] && echo 1 || echo 0)"
# F3 / F5: HEAVY_JOB_QUIET routes every heavy-job line to stderr and never starts a line with RESULT:
o1=$(HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" run --class vm --label quiet1 --wait 5 -- echo only-cmd 2>/dev/null)
ok "HEAVY_JOB_QUIET: stdout (stderr dropped) is only the CMD output" "$([ "$o1" = only-cmd ] && echo 1 || echo 0)"
o2=$(HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" run --class vm --label quiet2 --wait 5 -- echo c-out 2>&1; HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" run --class bogus --label q3 -- true 2>&1; HEAVY_JOB_QUIET=1 "$here/tools/heavy-job" run --class vm --label q4 -- bash -c 'exit 3' 2>&1)
ok "HEAVY_JOB_QUIET: no line starts with RESULT: (pass, usage error, fail), heavy-job's own lines are 'heavy-job: RESULT'" "$([ "$(grep -c '^RESULT:' <<<"$o2")" = 0 ] && [ "$(grep -c '^heavy-job: RESULT ' <<<"$o2")" = 3 ] && echo 1 || echo 0)"
# wired tools wrap themselves, nested ones do not
ok "check-all.sh and xnu-local-build re-exec under heavy-job (class gate / build) and skip when nested" "$(c=0; grep -q 'heavy-job run --class gate' "$here/tools/check-all.sh" && grep -q 'heavy-job" run --class build' "$here/tools/xnu-local-build" && grep -q 'nested in' "$here/tools/check-all.sh" && grep -q 'nested in' "$here/tools/xnu-local-build" && c=1; echo $c)"
out=$(HEAVY_JOB_HELD=outer GATE_HOST_LOCK='' timeout 20 bash -c 'cd "$1" && exec tools/check-all.sh --bogus' _ "$here" 2>&1)
ok "check-all.sh nested prints the nested line and does not lock" "$(has 'heavy-job: nested in outer, no second lock' "$out")"



if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
