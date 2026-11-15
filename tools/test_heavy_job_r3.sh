#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'r3' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# ---- r3 (review heavy-fifo-r3): F1 parent-death, F2 signal dispositions, F3 second signal, F4 grace validation, F5 TERM-handling CMD, F7 empty live ticket, F8 preflights
# F2/AP-280: CMD starts with SIGINT and SIGQUIT at their defaults (a bare `&` job ignores both; mutant: drop `env --default-signal` from the launch line)
hj run --class analysis --label r3sig --wait 5 -- bash -c 'grep ^SigIgn: /proc/$$/status'
sigign=$(sed -n 's/^SigIgn:[[:space:]]*//p' <<<"$out" | head -1)
ok "r3 F2: CMD has SIGINT/SIGQUIT at default (SigIgn=$sigign, bits 0x6 clear)" "$([ $rc = 0 ] && [[ $sigign =~ ^[0-9a-f]+$ ]] && [ $((0x$sigign & 6)) = 0 ] && echo 1 || echo 0)"
# T-4119/AP-280: the same for `detach`: its launch is a bare `&` too; a detached gate could not trap INT (mutant: drop `env --default-signal` from the detach line)
dhj --label r3sigd --log "$w/r3sigd.log" -- bash -c 'grep ^SigIgn: /proc/$$/status'
for _ in $(seq 100); do grep -q "DETACHED-END" "$w/r3sigd.log" 2>/dev/null && break; sleep 0.1; done
sigign=$(sed -n 's/^SigIgn:[[:space:]]*//p' "$w/r3sigd.log" | head -1)
ok "T-4119: detached CMD has SIGINT/SIGQUIT at default (SigIgn=$sigign, bits 0x6 clear)" "$([ $rc = 0 ] && [[ $sigign =~ ^[0-9a-f]+$ ]] && [ $((0x$sigign & 6)) = 0 ] && echo 1 || echo 0)"
# F1: SIGKILL of heavy-job's whole group (timeout -s KILL) must not leave CMD and the lock (mutant: drop setpriv --pdeathsig / the USR1 trap). survivors are looked for by their unique sleep time.
timeout -s KILL 2 "$here/tools/heavy-job" run --class analysis --label r3k1 --wait 5 -- sleep 31.71 >"$w/r3k1.out" 2>&1; rck=$?
freewait 5
ok "r3 F1: timeout -s KILL on heavy-job (rc $rck): CMD (sleep 31.71) gone and the lock free within 5 s; run.log says why" "$([ "$rck" = 137 ] && [ $lockfree = 1 ] && ! pgrep -f '^sleep 31\.71$' >/dev/null && grep -q 'heavy-job died (parent-death signal)' logs/heavy-job/r3k1/run.log && ! grep -q 'session members still hold' logs/heavy-job/r3k1/run.log && echo 1 || echo 0)"
# ... and a CMD that ignores SIGTERM is SIGKILLed after the grace, said in run.log (mutant: drop the escalation)
s=$(date +%s.%N)
HEAVY_JOB_KILL_GRACE=2 timeout -s KILL 1 "$here/tools/heavy-job" run --class analysis --label r3k2 --wait 5 -- bash -c 'trap "" TERM; sleep 31.72 & wait' >"$w/r3k2.out" 2>&1
freewait 8; el=$(elapsed "$s")
ok "r3 F1: group SIGKILL, CMD ignores SIGTERM: lock free after ${el}s (1 s + 2 s grace), no survivor, 'sending SIGKILL' in run.log" "$([ $lockfree = 1 ] && awk -v e="$el" 'BEGIN{exit !(e>=2.5 && e<7)}' && ! pgrep -f '^sleep 31\.72$' >/dev/null && grep -q 'CMD ignored SIGTERM for 2 s after heavy-job died, sending SIGKILL' logs/heavy-job/r3k2/run.log && echo 1 || echo 0)"
# F3: a second TERM during killcmd is ignored: exit 143, CMD gone after the grace, WARNING present, lock free, and CMD's own TERM handler ran ONCE (a nested abort would send a second TERM)
# (mutants: drop `trap '' INT TERM HUP` in sabort -> handler runs twice; make `cg=` the first statement of killcmd again -> CMD survives)
rm -f "$w/r3d.ready" "$w/r3d.terms" "$w/r3d.pid"
HEAVY_JOB_KILL_GRACE=3 "$here/tools/heavy-job" run --class analysis --label r3d --wait 30 -- bash -c 'echo $$ >'"$w"'/r3d.pid; trap "echo t >>'"$w"'/r3d.terms" TERM; echo 1 >'"$w"'/r3d.ready; while :; do sleep 0.2; done' >"$w/r3d.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r3d.ready" ] && break; sleep 0.1; done
kill -TERM "$hp"; sleep 0.7; kill -TERM "$hp" 2>/dev/null; { wait "$hp"; } 2>/dev/null; rcs=$?
freewait 2
ok "r3 F3: TERM twice 0.7 s apart to a CMD whose handler does not exit (grace 3): exit 143 (got $rcs), CMD gone, SIGKILL warning, '(CMD killed)', lock free, CMD saw exactly one TERM (saw $(wc -l <"$w/r3d.terms"))" "$([ "$rcs" = 143 ] && [ $lockfree = 1 ] && ! kill -0 "$(cat "$w/r3d.pid")" 2>/dev/null && [ "$(wc -l <"$w/r3d.terms")" = 1 ] && has 'WARNING: CMD ignored SIGTERM for 3 s, sending SIGKILL' "$(cat "$w/r3d.out")" | grep -q 1 && has '^RESULT: FAIL heavy-queue label=r3d aborted by SIGTERM (CMD killed)' "$(cat "$w/r3d.out")" | grep -q 1 && echo 1 || echo 0)"
# F4: HEAVY_JOB_KILL_GRACE is whole seconds 1..3600, else RESULT: ERROR exit 2 before the ticket, CMD not run (mutant: drop the check)
good=1
for g in abc 1.5 0 -1 01 99999; do
	out=$(HEAVY_JOB_KILL_GRACE=$g "$here/tools/heavy-job" run --class analysis --label r3g --wait 5 -- echo should-not-run 2>&1); rc=$?
	{ [ $rc = 2 ] && has '^RESULT: ERROR usage: HEAVY_JOB_KILL_GRACE must be whole seconds' "$out" | grep -q 1 && ! grep -q 'should-not-run$' <<<"$out" && ! grep -q 'queue: ticket' <<<"$out"; } || { good=0; echo "  bad grace '$g': rc=$rc $out"; }
done
ok "r3 F4: HEAVY_JOB_KILL_GRACE abc / 1.5 / 0 / -1 / 01 / 99999 -> RESULT: ERROR usage exit 2, no ticket, CMD not run" "$good"
hj run --class analysis --label r3g2 --wait 5 -- echo grace-ok; o1=$out; rc1=$rc
out=$(HEAVY_JOB_KILL_GRACE=5 "$here/tools/heavy-job" run --class analysis --label r3g3 --wait 5 -- echo grace5-ok 2>&1); rc5=$?
ok "r3 F4: unset and 5 are accepted" "$([ $rc1 = 0 ] && [ $rc5 = 0 ] && has '^grace-ok$' "$o1" | grep -q 1 && has '^grace5-ok$' "$out" | grep -q 1 && echo 1 || echo 0)"
# F5: a CMD that handles SIGTERM and needs 1 s to clean up gets the DEFAULT grace (mutants: poll loop in killcmd removed; default grace 10 -> 0), and its cleanup output reaches run.log
rm -f "$w/r3c.ready" "$w/r3c.cleaned"
"$here/tools/heavy-job" run --class analysis --label r3c --wait 30 -- bash -c 'trap "sleep 1; echo r3-cleaning; echo cleaned >'"$w"'/r3c.cleaned; exit 0" TERM; sleep 31.75 & echo 1 >'"$w"'/r3c.ready; wait' >"$w/r3c.out" 2>&1 & hp=$!
for _ in $(seq 100); do [ -s "$w/r3c.ready" ] && break; sleep 0.1; done
kill -TERM "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?
ok "r3 F5: CMD with a 1 s SIGTERM cleanup, default grace: cleanup finished (not SIGKILLed), exit 143 (got $rcs), no SIGKILL warning, its cleanup line is in run.log" "$([ "$rcs" = 143 ] && [ -s "$w/r3c.cleaned" ] && ! grep -q 'sending SIGKILL' "$w/r3c.out" && grep -q '^r3-cleaning$' logs/heavy-job/r3c/run.log && echo 1 || echo 0)"
# F7: a live ticket that is still empty (stake creates it, then writes it) is "being written", not UNREADABLE with an rm hint (mutant: restore the single read check)
( exec 9>>"$w/hq/000777.live"; flock -n 9; sleep 4 ) & echo $! >"$w/r3e.pid"
sleep 0.5; hj status
ok "r3 F7: status of a locked, still empty ticket: '(being written)', no UNREADABLE, no rm hint" "$([ $rc = 0 ] && has '000777\.live (being written)' "$out" | grep -q 1 && ! grep -q 'UNREADABLE\|rm ' <<<"$out" && echo 1 || echo 0)"
waitl r3e; rm -f "$w/hq/000777.live"
# F8: preflights, before the ticket: no setsid / no setpriv on PATH, bash minimum (HEAVY_JOB_MIN_BASH override; BASH_VERSINFO is read-only)
for miss in setsid setpriv; do
	mkdir -p "$w/path-no-$miss"; for f in /usr/bin/*; do [ "${f##*/}" = "$miss" ] || ln -sf "$f" "$w/path-no-$miss/" 2>/dev/null; done
	out=$(PATH=$w/path-no-$miss "$here/tools/heavy-job" run --class analysis --label r3f8 --wait 5 -- echo should-not-run 2>&1); rc=$?
	ok "r3 F8: no $miss on PATH -> RESULT: ERROR '$miss not found', exit 2, no ticket, CMD not run" "$([ $rc = 2 ] && has "^RESULT: ERROR $miss not found" "$out" | grep -q 1 && ! grep -q 'should-not-run$\|queue: ticket' <<<"$out" && echo 1 || echo 0)"
done
mkdir -p "$w/path-badenv"; for f in /usr/bin/*; do ln -sf "$f" "$w/path-badenv/" 2>/dev/null; done; rm -f "$w/path-badenv/env"; printf '#!/bin/sh\nexit 125\n' >"$w/path-badenv/env"; chmod +x "$w/path-badenv/env"   # an env without --default-signal
out=$(PATH=$w/path-badenv "$here/tools/heavy-job" run --class analysis --label r3f8 --wait 5 -- echo should-not-run 2>&1); rc=$?
ok "r3 F8: env without --default-signal -> RESULT: ERROR 'env --default-signal is not supported', exit 2, no ticket, CMD not run" "$([ $rc = 2 ] && has '^RESULT: ERROR env --default-signal is not supported' "$out" | grep -q 1 && ! grep -q 'should-not-run$\|queue: ticket' <<<"$out" && echo 1 || echo 0)"
out=$(HEAVY_JOB_MIN_BASH=999 "$here/tools/heavy-job" run --class analysis --label r3f8 --wait 5 -- echo should-not-run 2>&1); rc=$?
ok "r3 F8: bash older than the minimum -> RESULT: ERROR 'bash >= 5.1 required', exit 2, CMD not run" "$([ $rc = 2 ] && has '^RESULT: ERROR bash >= 5.1 required' "$out" | grep -q 1 && ! grep -q 'should-not-run$' <<<"$out" && echo 1 || echo 0)"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
