#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, ok, has, ...) are set by the sourced test_heavy_job_lib.sh
# Part 'light' of the tools/heavy-job host test (T-66a6): `run --light` is the second slot (own lock + FIFO, same memory gate, CPU cap with HEAVY GAP, own status lines).
# Cases: a light job runs and exits while a main-lock holder (the "gate") holds the lock; one light job at a time; CMD does not see the slot variable; CPU overrun -> GAP line in stdout,
#   run.log, RESULT gaps=1, status, and the label's next light request is refused with a do-instead; under the cap -> no GAP; memory gate refuses a light job naming mem_avail;
#   status lists the light holder and waiters on their own lines; --light with --remote-cmd / without the module is refused.
# Mutants (each must be killed): light uses the main lock, no CPU cap, no label refusal, status without the light kind, no memory gate for light, no gaps= in RESULT, slot variable leaks into CMD.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

real=$here/tools/heavy-job
mkcopy() {   # mkcopy DIR TOOL-SED|- MODULE-SED|-  -> DIR/tools/{heavy-job,heavy-job-light.sh,...} (sed edits of the real files, or plain copies)
	mkdir -p "$1/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$here/tools/heavy-job-canon.sh" "$1/tools/" || return 1
	if [ "$2" = - ]; then cp "$real" "$1/tools/heavy-job"; else sed "$2" "$real" >"$1/tools/heavy-job"; fi
	if [ "$3" = - ]; then cp "$here/tools/heavy-job-light.sh" "$1/tools/"; else sed "$3" "$here/tools/heavy-job-light.sh" >"$1/tools/heavy-job-light.sh"; fi
	chmod +x "$1/tools/heavy-job"
}
llq() { ls "$w"/hq-light/[0-9]*.* 2>/dev/null | wc -l; }
llwait() { for _ in $(seq 150); do [ "$(llq)" -ge "$1" ] && return 0; sleep 0.1; done; return 1; }
stopbg() {   # stopbg NAME...: end the background heavy-job started as $w/NAME.pid (its subshell and the heavy-job under it)
	local x p; for x; do p=$(cat "$w/$x.pid" 2>/dev/null) || continue; pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null; wait "$p" 2>/dev/null; done
}
burn='e=$((SECONDS+3)); while [ $SECONDS -lt $e ]; do :; done'   # ~3 CPU-s of one core

# ---- checks, each a function of the tool under test $1 (the mutants run the same checks and must FAIL them)
chk_a() {   # a light job runs, passes and ends while a main-lock holder holds the main lock; CMD does not see the slot variable
	local t=$1 s0 el held=1; holdl 30; s0=$(date +%s)
	out=$(HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label la --wait 6 -- bash -c 'echo light-ran slot=${HEAVY_JOB_LIGHT_SLOT:-unset}' 2>&1); rc=$?
	el=$(( $(date +%s) - s0 )); kill -0 "$(cat "$w/lh.pid")" 2>/dev/null || held=0
	stopbg lh
	[ $rc = 0 ] && grep -qx 'light-ran slot=unset' <<<"$out" && grep -q '^RESULT: PASS label=la class=mutant rc=0 elapsed=[0-9]*s$' <<<"$out" && [ $held = 1 ] && [ "$el" -lt 6 ]
}
chk_b() {   # at most one light job at a time: a second one waits behind the first (its CMD does not run inside its short --wait)
	local t=$1
	( HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lb1 --wait 30 -- bash -c 'echo 1 >'"$w"'/lb1.start; sleep 30' >"$w/lb1.out" 2>&1 ) & echo $! >"$w/lb1.pid"
	for _ in $(seq 100); do [ -s "$w/lb1.start" ] && break; sleep 0.1; done
	out=$(HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lb2 --wait 3 -- echo lb2-ran 2>&1); rc=$?
	stopbg lb1; rm -f "$w/lb1.start"
	[ $rc = 2 ] && ! grep -q '^lb2-ran$' <<<"$out" && grep -q '^RESULT: ERROR' <<<"$out"
}
chk_d() {   # CPU overrun: GAP line (stdout + run.log + RESULT gaps=1 + status), CMD rc kept; under the cap: nothing
	local t=$1 rl st
	out=$(HEAVY_JOB_LIGHT_CAP=1 HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lcap --wait 20 -- bash -c "$burn" 2>&1); rc=$?
	rl=$(find "$w/cwd" -path '*lcap/run.log' | head -1); st=$(HEAVY_JOB_CANONICAL=$t "$t" status 2>&1)
	[ $rc = 0 ] && grep -qE '^HEAVY GAP: light job lcap used [0-9.]+ CPU-s > 1$' <<<"$out" && grep -qE '^RESULT: PASS label=lcap class=mutant rc=0 elapsed=[0-9]+s gaps=1$' <<<"$out" \
		&& [ -n "$rl" ] && grep -qE '^HEAVY GAP: light job lcap used ' "$rl" && grep -qE '^heavy-job: HEAVY GAP: light job lcap used ' <<<"$st" || return 1
	out=$(HEAVY_JOB_LIGHT_CAP=1 HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lsmall --wait 20 -- true 2>&1); rc=$?
	[ $rc = 0 ] && ! grep -q 'HEAVY GAP' <<<"$out" && grep -qE '^RESULT: PASS label=lsmall class=mutant rc=0 elapsed=[0-9]+s$' <<<"$out"
}
chk_d2() {   # the overrun label is refused on its next light request (CMD not run) with a do-instead; chk_d ran first and marked it
	local t=$1
	out=$(HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lcap --wait 5 -- echo lcap-ran 2>&1); rc=$?
	[ $rc = 2 ] && ! grep -q '^lcap-ran$' <<<"$out" && grep -q 'RESULT: ERROR light-refused: label lcap exceeded the light CPU cap' <<<"$out" && grep -q 'do instead: run it without --light' <<<"$out"
}
chk_e() {   # the memory gate applies to a light job and names the metric
	local t=$1
	out=$(HEAVY_JOB_MEMINFO=$w/mem.small HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lmem --wait 3 -- echo lmem-ran 2>&1); rc=$?
	[ $rc = 2 ] && ! grep -q '^lmem-ran$' <<<"$out" && grep -q 'RESULT: ERROR memory-gate mem_avail=4.0G need=2G' <<<"$out"
}
chk_f() {   # status: the light slot has its own lines (cap, lock, holder, waiter); the main lock line is untouched
	local t=$1 st
	( HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lf1 --wait 30 -- bash -c 'echo 1 >'"$w"'/lf1.start; sleep 30' >"$w/lf1.out" 2>&1 ) & echo $! >"$w/lf1.pid"
	for _ in $(seq 100); do [ -s "$w/lf1.start" ] && break; sleep 0.1; done
	( HEAVY_JOB_CANONICAL=$t "$t" run --light --class mutant --label lf2 --wait 30 -- true >"$w/lf2.out" 2>&1 ) & echo $! >"$w/lf2.pid"
	llwait 2; st=$(HEAVY_JOB_CANONICAL=$t "$t" status 2>&1)
	stopbg lf2 lf1; rm -f "$w/lf1.start"
	grep -q '^heavy-job: lock free ' <<<"$st" && grep -q '^heavy-job: light slot cap=120 CPU-s$' <<<"$st" && grep -q '^heavy-job: light lock held by: lf1 mutant ' <<<"$st" \
		&& grep -q '^heavy-job: light holder: owner=.* label=lf1 ' <<<"$st" && grep -q '^heavy-job: light waiter #1: .* label=lf2 ' <<<"$st"
}

for c in chk_a chk_b chk_d chk_d2 chk_e chk_f; do "$c" "$real"; r=$?; [ $r = 0 ] || echo "$out" >&2
	case $c in
	chk_a) ok "light a: a --light mutant job ran and passed in under 6 s while a main-lock holder (the gate) still held the main lock; CMD saw no slot variable" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	chk_b) ok "light b: a second light job waits behind the first (one light job at a time): rc 2, its CMD never ran" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	chk_d) ok "light d: over the cap (HEAVY_JOB_LIGHT_CAP=1, 3 CPU-s burned): 'HEAVY GAP: light job lcap used N CPU-s > 1' in stdout, run.log and status, RESULT ... gaps=1, rc 0 kept; a job under the cap prints no GAP" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	chk_d2) ok "light d2: the overrun label's next light request is refused (RESULT: ERROR light-refused ... do instead: run it without --light), CMD not run" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	chk_e) ok "light e: memory gate refuses a light job with mem_avail=4.0G need=2G (rc 2), CMD not run" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	chk_f) ok "light f: status lists the light slot (cap, lock holder, 'light holder', 'light waiter #1') on their own lines and the main 'lock free' line" "$([ $r = 0 ] && echo 1 || echo 0)" ;;
	esac
done
# non-light admission untouched: a light job takes no ticket of the main queue (lq counts the main dir only) and a main holder leaves the light dir empty
holdl 6; ( "$real" run --light --class mutant --label lg --wait 20 -- sleep 3 >"$w/lg.out" 2>&1 ) & echo $! >"$w/lg.pid"; llwait 1
ok "light g: while a light job runs and a main holder holds the main lock, the main queue has exactly 1 ticket (the holder) and the light queue 1 (the light job)" "$([ "$(lq)" = 1 ] && [ "$(llq)" = 1 ] && echo 1 || echo 0)"
waitl lh lg
ok "light g2: both ended with RESULT: PASS" "$(grep -q '^RESULT: PASS' "$w/lg.out" && grep -q '^RESULT: PASS' "$w/lh.out" && echo 1 || echo 0)"

# usage: --light with --remote-cmd is refused; a copy without the module says so (run and status)
out=$("$real" run --light --class gate --label lr --remote-cmd 'true' --wait 5 -- true 2>&1); rc=$?
ok "light h: --light with --remote-cmd is refused (rc 2, 'cannot be combined')" "$([ $rc = 2 ] && grep -q 'cannot be combined with --remote-cmd' <<<"$out" && echo 1 || echo 0)"
mkcopy "$w/NOMOD" - - || exit 2; rm -f "$w/NOMOD/tools/heavy-job-light.sh"
out=$(HEAVY_JOB_CANONICAL=$w/NOMOD/tools/heavy-job "$w/NOMOD/tools/heavy-job" run --light --class mutant --label lnm --wait 5 -- echo lnm-ran 2>&1); rc=$?
st=$(HEAVY_JOB_CANONICAL=$w/NOMOD/tools/heavy-job "$w/NOMOD/tools/heavy-job" status 2>&1)
ok "light i: heavy-job-light.sh missing: --light is refused (rc 2, CMD not run) and status says 'light slot UNAVAILABLE'" "$([ $rc = 2 ] && ! grep -q '^lnm-ran$' <<<"$out" && grep -q 'usage: --light needs ' <<<"$out" && grep -q 'light slot UNAVAILABLE' <<<"$st" && echo 1 || echo 0)"

# ---- mutants: each edit must make its check FAIL (chk_d2 reads the mark chk_d wrote, so its mutant runs chk_d first)
chk_d_d2() { chk_d "$1" >/dev/null 2>&1; chk_d2 "$1"; }
mutant() {   # mutant NAME TOOL-SED|- MODULE-SED|- CHECK
	local d=$w/mut-$1
	rm -rf "$w/lock/heavy.lock.overruns"
	mkcopy "$d" "$2" "$3" || { ok "mutant $1: cannot build the copy" 0; return; }
	if [ "$2" != - ] && cmp -s "$real" "$d/tools/heavy-job"; then ok "mutant $1: the tool edit changed nothing (the marker moved)" 0; return; fi
	if [ "$3" != - ] && cmp -s "$here/tools/heavy-job-light.sh" "$d/tools/heavy-job-light.sh"; then ok "mutant $1: the module edit changed nothing (the marker moved)" 0; return; fi
	if "$4" "$d/tools/heavy-job" >/dev/null 2>&1; then ok "mutant $1: SURVIVED ($4 still passes)" 0; else ok "mutant $1: killed ($4 fails)" 1; fi
	stopbg lh lb1 lf1 lf2; rm -rf "$w/lock/heavy.lock.overruns"
}
mutant main-lock - 's/lock=${lock}.light; hq=${hq}-light/:/' chk_a
mutant no-cap - 's/^\tawk -v u="\$used" -v c="\$cap" .*|| return 0$/\treturn 0/' chk_d
mutant no-refuse - 's/^\t\[ -f "\$m" \] || return 0$/\treturn 0/' chk_d_d2
mutant status-kind - 's/qk=light;/qk=local;/' chk_f
mutant no-memgate 's/memgate "before \(lock\|exec\)" ||/[ -n "$light" ] || memgate "before \1" ||/' - chk_e
mutant no-gaps-in-result 's/\${gaps:+ gaps=\$gaps}//' - chk_d
mutant slot-leak 's/^\tunset HEAVY_JOB_LIGHT_SLOT .*$/\t:/' - chk_a

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
