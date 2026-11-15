#!/bin/bash
# shellcheck disable=SC2154,SC2034  # sourced library: launch/fs/repo/lockfree are set and read by the part scripts that source it
# Host test of tools/heavy-job (lock, memory gate, PSI, re-entrancy, rc mapping) and of the tools/remote-job resubmit check (TP-042 req 7),
# with a fake ssh/scp/docker; no network, no real host, no phone. Everything lives in one mktemp dir (HEAVY_JOB_LOCK, meminfo, psi, fake remote home).
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
cd "$(dirname "$0")/.." || exit 2
here=$PWD
export OFFLOAD_CONF=$here/tools/offload-host.conf.example   # T-dd93: the committed placeholder host config, never the real one
export LAB_HOME=/home/user
w=$(mktemp -d) || exit 2
# Isolation: the tests run real `detach`/remote-job jobs. Inside a ticket thread T3CODE_THREAD_ID + the real ~/.t3/scratch_sec/thread-<id>.hook would make every
# test job (d-rc3, d-pass, ...) wake the REAL thread (T-fb2c default hook). No thread id, and an empty private secrets dir, so no test job can wake anybody.
unset T3CODE_THREAD_ID; mkdir -p "$w/sec0" && export WAKE_SECRETS_DIR=$w/sec0
cleanup() { for p in $(cat "$w"/*.pid 2>/dev/null); do kill "$p" 2>/dev/null; done; rm -rf "$w"; }
trap cleanup EXIT
unset HEAVY_JOB_HELD HEAVY_JOB_QUIET OFFLOAD_QUEUE_HELD HEAVY_JOB_DETACHED HEAVY_JOB_DETACHED_WAIT HEAVY_JOB_NOTRUN_FILE   # the gate runs this test inside heavy-job, in a detached scope (T-e8ad: inherited, it turns every --wait expiry off and the cases hang; T-6269)
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; echo "     DIAG (T-c7be) rc=$rc out: $(printf %s "$out" | tail -n 6 | cut -c1-300)"; bad=$((bad + 1)); fi; }
has() { grep -q -- "$1" <<<"$2" && echo 1 || echo 0; }
export HEAVY_JOB_QUEUE_DIR=$w/hq HEAVY_JOB_LOCK=$w/lock/heavy.lock HEAVY_JOB_POLL=1 HEAVY_JOB_RESERVE_GB=8
printf 'MemTotal: 33554432 kB\nMemAvailable: 33554432 kB\n' >"$w/mem.big"   # 32 GB
printf 'MemTotal: 33554432 kB\nMemAvailable: 4194304 kB\n' >"$w/mem.small"   # 4 GB
printf 'some avg10=0.50 avg60=0.10 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n' >"$w/psi.ok"
printf 'some avg10=55.00 avg60=0.10 avg300=0.00 total=1\n' >"$w/psi.bad"
printf 'some avg10=7.00 avg60=5.00 avg300=5.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n' >"$w/cpu.ok"   # T-a8b4: measured idle-ish host with high iowait
printf 'some avg10=28.76 avg60=13.00 avg300=7.00 total=1\n' >"$w/cpu.bad"   # measured with all 16 cores busy
export HEAVY_JOB_MEMINFO=$w/mem.big HEAVY_JOB_PSI=$w/psi.ok HEAVY_JOB_CPU_PSI=$w/cpu.ok HEAVY_JOB_LOAD=$w/load; echo "0.25 0.1 0.1 1/1 1" >"$w/load"
export HEAVY_JOB_CONF=$w/hj.conf OFFLOAD_OFFLINE_FILE=$w/off.list   # T-ee35: private limit file + offline list (absent = defaults / host on), never the real ones
export HEAVY_JOB_CANONICAL=$here/tools/heavy-job   # T-d543: the copy under test IS the canonical one (else a worktree copy differing from the trunk checkout would re-exec the trunk copy and test the wrong code)
export HEAVY_JOB_ARCSTATS=$w/arc.none HEAVY_JOB_ARC_PC=$w/arc.pc; echo 0 >"$w/arc.pc"   # no ZFS by default (lab-host has real arcstats)
mkdir -p "$w/cwd" && cd "$w/cwd" || exit 2   # logs/heavy-job lands here, not in the repo
hj() { out=$("$here/tools/heavy-job" "$@" 2>&1); rc=$?; }

# helpers hoisted from the old single-file test_heavy_job.sh (shared by the parts)
arcf() { printf 'name type data\nc_min 4 %s\nsize 4 %s\nmru_evictable_data 4 %s\nmru_evictable_metadata 4 0\nmfu_evictable_data 4 %s\nmfu_evictable_metadata 4 0\nmru_ghost_evictable_data 4 99999999999\n' "$@" >"$w/arc.cur"; }
lq() { ls "$w"/hq/[0-9]*.* 2>/dev/null | wc -l; }
lqwait() { for _ in $(seq 1500); do [ "$(lq)" -ge "$1" ] && return 0; sleep 0.1; done; return 1; }
holdl() {   # holdl SECONDS: a ticketed holder "lh" that signals its start, then sleeps
	rm -f "$w/lh.start"
	( "$here/tools/heavy-job" run --class analysis --label lh --wait 60 -- bash -c 'echo 1 >'"$w"'/lh.start; sleep '"$1" >"$w/lh.out" 2>&1 ) & echo $! >"$w/lh.pid"
	for _ in $(seq 1000); do [ -s "$w/lh.start" ] && break; sleep 0.1; done
}
holdr() {   # holdr: like holdl, but the holder "lh" holds the lock until $w/lh.release exists (T-c7be: an event, not a fixed sleep that load can outlast; waitl releases it); cap 120 s
	rm -f "$w/lh.start" "$w/lh.release"
	( "$here/tools/heavy-job" run --class analysis --label lh --wait 60 -- bash -c 'echo 1 >'"$w"'/lh.start; for _ in $(seq 1200); do [ -e '"$w"'/lh.release ] && break; sleep 0.1; done' >"$w/lh.out" 2>&1 ) & echo $! >"$w/lh.pid"
	for _ in $(seq 600); do [ -s "$w/lh.start" ] && break; sleep 0.1; done
}
waitl() { : >"$w/lh.release"; for x; do wait "$(cat "$w/$x.pid")" 2>/dev/null; done; }
startl() { ( "$here/tools/heavy-job" run --class analysis --label "l$1" --wait 60 -- bash -c 'echo '"$1"' >>'"$w"'/lorder' >"$w/l$1.out" 2>&1 ) & echo $! >"$w/l$1.pid"; }
tnum() { local b; b=$(basename "$(ls "$w"/hq/*."$1" 2>/dev/null | head -1)"); echo $((10#${b%%.*})); }   # ticket number of the (one) live ticket of label $1
elapsed() { awk -v a="$1" -v b="$(date +%s.%N)" 'BEGIN{printf "%.1f", b-a}'; }
seqcase() {   # NAME CONTENT|DELETE WHY-REGEX: a ticketed holder is live while a waiter takes its number under the damaged .seq
	: >"$w/lorder"; holdr
	if [ "$2" = DELETE ]; then rm -f "$w/hq/.seq"; else printf '%s' "$2" >"$w/hq/.seq"; fi
	startl B; lqwait 2
	local nh nb; nh=$(tnum lh); nb=$(tnum lB)
	waitl lh lB
	ok "F1: .seq $1 with a live ticket: waiter numbered AFTER the holder ($nb > $nh), loud repair line, order kept, rc ok, queue empty" "$([ "$nb" -gt "$nh" ] && [ "$(tr -d '\n' <"$w/lorder")" = B ] && [ "$(lq)" = 0 ] && has "queue: \.seq repaired to $nb ($3" "$(cat "$w/lB.out")" | grep -q 1 && grep -q '^RESULT: PASS label=lB' "$w/lB.out" && echo 1 || echo 0)"
}
sigcase() {   # NAME SIGNAL RC [ENV=VAL...]; the job waits behind holder "lh" when no ENV is given, else in the memory gate
	local nm=$1 sig=$2 erc=$3 hp kids k dead=1 rcs; shift 3
	"${launch[@]}" env "$@" "$here/tools/heavy-job" run --class build --label "$nm" --wait 60 -- echo should-not-run >"$w/$nm.out" 2>&1 & hp=$!
	for _ in $(seq 1000); do grep -q 'waiting' "$w/$nm.out" && kids=$(pgrep -P "$hp") && [ -n "$kids" ] && break; sleep 0.1; done
	kill -"$sig" "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?
	for k in $kids; do for _ in $(seq 300); do kill -0 "$k" 2>/dev/null || break; sleep 0.1; done; ! kill -0 "$k" 2>/dev/null || dead=0; done
	ok "F4b/F6: SIG$sig while waiting ($nm): exit $erc (got $rcs), RESULT: FAIL ... aborted by SIG$sig, no ticket, no orphan child (had: $(echo $kids))" "$([ "$rcs" = "$erc" ] && [ -n "$kids" ] && [ "$dead" = 1 ] && [ -z "$(ls "$w"/hq/*."$nm" 2>/dev/null)" ] && has "^RESULT: FAIL heavy-queue label=$nm aborted by SIG$sig" "$(cat "$w/$nm.out")" | grep -q 1 && ! grep -q should-not-run "$w/$nm.out" && echo 1 || echo 0)"
}
f2cmd() {   # NAME SIGNAL ERC CMD-SCRIPT [ENV=VAL...]
	local nm=$1 sig=$2 erc=$3 scr=$4 hp cp sp k dead=1 rcs res; shift 4
	rm -f "$w/$nm.cmd"
	env "$@" "$here/tools/heavy-job" run --class analysis --label "$nm" --wait 30 -- bash -c 'echo $$ >'"$w/$nm.cmd"'; '"$scr" >"$w/$nm.out" 2>&1 & hp=$!
	for _ in $(seq 1000); do [ -s "$w/$nm.cmd" ] && break; sleep 0.1; done
	cp=$(cat "$w/$nm.cmd" 2>/dev/null); sp=$(pgrep -P "${cp:-0}")
	kill -"$sig" "$hp"; { wait "$hp"; } 2>/dev/null; rcs=$?
	for k in $cp $sp; do for _ in $(seq 400); do kill -0 "$k" 2>/dev/null || break; sleep 0.1; done; ! kill -0 "$k" 2>/dev/null || dead=0; done
	res=$(grep '^RESULT: ' "$w/$nm.out" | tail -n 1)
	ok "F2: SIG$sig during CMD ($nm): exit $erc (got $rcs), CMD (pid $cp) and its child ($sp) gone, RESULT: FAIL aborted by SIG$sig printed AND last line of run.log, lock text cleared, no ticket" "$([ "$rcs" = "$erc" ] && [ -n "$cp" ] && [ -n "$sp" ] && [ "$dead" = 1 ] && has "^RESULT: FAIL heavy-queue label=$nm aborted by SIG$sig (CMD killed)" "$res" | grep -q 1 && [ "$(tail -n 1 "logs/heavy-job/$nm/run.log")" = "$res" ] && [ ! -s "$HEAVY_JOB_LOCK" ] && [ -z "$(ls "$w"/hq/*."$nm" 2>/dev/null)" ] && echo 1 || echo 0)"
}
freewait() {   # freewait SECONDS: waits until the lock is free; sets lockfree=1|0
	local i; lockfree=0
	for ((i = 0; i < $1 * 10; i++)); do flock -n "$HEAVY_JOB_LOCK" true 2>/dev/null && { lockfree=1; break; }; sleep 0.1; done
}
ans() { printf 'load=%s\nmemkb=%s\nrj=%s\nforeign=%s\n' "$1" "$2" "${3:-}" "${4:-}" >"$fs/answer"; }
refuse() {   # NAME  ANSWER-ARGS...  ; expects: not admitted with reason text $REASON, RESULT: ERROR offload-queue, exit 2, CMD not run
	local nm=$1; shift; ans "$@"
	hj offload-host run --owner alice --label "s-$nm" --wait 2 -- echo should-not-run
	ok "offload-host refused ($nm): reason line, RESULT: ERROR offload-queue, exit 2, CMD not run" "$([ $rc = 2 ] && echo "$(has "not admitted: $REASON" "$out")$(has '^RESULT: ERROR offload-queue' "$out")" | grep -q 11 && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
}
qwait() { for _ in $(seq 1500); do [ "$(ls "$w"/q/[0-9]*.* 2>/dev/null | wc -l)" -ge "$1" ] && return 0; sleep 0.1; done; return 1; }
startw() { ( "$here/tools/heavy-job" offload-host run --owner "o$1" --label "q$1" --wait 60 -- bash -c 'echo '"$1"' >>'"$w"'/order; sleep 1' >"$w/q$1.out" 2>&1 ) & echo $! >"$w/q$1.pid"; }
rj() { out=$(cd "$repo" && tools/remote-job "$@" 2>&1); rc=$?; }
# fake systemd-run / systemctl (never the real ones), shared by the parts that detach
mkdir -p "$w/dt"
cat >"$w/dt/sdrun" <<'F'
#!/bin/bash
[ -e "$DT_DIR/srfail" ] && { echo "Failed to start transient scope unit: Unit already exists." >&2; exit 1; }
while [ "$1" != -- ]; do shift; done
shift
exec "$@"
F
cat >"$w/dt/sctl" <<'F'
#!/bin/bash
[ "$1" = --user ] && shift
case $1 in
	show-environment) [ ! -e "$DT_DIR/env.fail" ] ;;
	list-units) cat "$DT_DIR/units" 2>/dev/null ;;
	is-active) if [ -e "$DT_DIR/inactive" ]; then echo inactive; exit 3; else echo active; fi ;;
esac
F
chmod +x "$w/dt/sdrun" "$w/dt/sctl"
export DT_DIR=$w/dt
dhj() { out=$(HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$here/tools/heavy-job" detach "$@" 2>&1 </dev/null); rc=$?; }
endwait() { timeout 10 tail -n +1 -f "$1" 2>/dev/null | grep -q -m1 'DETACHED-END'; }   # bounded, no sleep loop
