#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, ok, has, ...) are set by the sourced test_heavy_job_lib.sh
# Part 'cycle' of the tools/heavy-job host test (T-70cd): wait-cycle breaker (tools/heavy-job-cycle.sh).
# (a) the 2026-11-03 shape: 2 old-rule copies (an aged waiter jumps the queue) + 2 canonical waiters, each side naming the other ahead, lock free: WAIT-CYCLE printed, the old ones
#     re-queued under their ticket number, the queue drains; (b) lock free but the head starting (< floor) = no WAIT-CYCLE, default floor 120; (c) the re-queued waiter keeps label, prio
#     and its wake hook (fires once, at the job end); (d) unreadable environ = HEAVY GAP line, waiter untouched.
# Mutants (each must be killed): drop the stop step, drop the seq preservation, drop the floor.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2
real=$here/tools/heavy-job
mkdir -p "$w/sc"
cat >"$w/sc/sdrun" <<'F'
#!/bin/bash
u=; for a in "$@"; do case $a in --unit=*) u=${a#--unit=} ;; esac; done
while [ "$1" != -- ]; do shift; done; shift
echo $$ >"$SC_DIR/$u.pid"; echo "$(date +%T.%N) sdrun unit=$u pid=$$ ppid=$PPID" >>"$SC_DIR/calls.log"
exec setsid "$@"
F
cat >"$w/sc/sctl" <<'F'
#!/bin/bash
[ "$1" = --user ] && shift
echo "$(date +%T.%N) sctl $* ppid=$PPID" >>"$SC_DIR/calls.log"
case $1 in
	show-environment|list-units|stop) exit 0 ;;
	is-active) p=$(cat "$SC_DIR/${2%.scope}.pid" 2>/dev/null); if kill -0 "$p" 2>/dev/null; then echo active; else echo inactive; exit 3; fi ;;
	kill) u=; for a in "$@"; do case $a in *.scope) u=${a%.scope} ;; esac; done; kill -KILL -- "-$(cat "$SC_DIR/$u.pid")" 2>/dev/null; exit 0 ;;
esac
F
chmod +x "$w/sc/sdrun" "$w/sc/sctl"
# mkvar DIR MODULE-SED|- : DIR/tools = the canonical copy under test (real tool + modules; the cycle module edited by the sed expression for a mutant)
mkvar() {
	mkdir -p "$1/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$here/tools/heavy-job-canon.sh" "$1/tools/" && cp "$real" "$1/tools/heavy-job" || return 1
	if [ "$2" = - ]; then cp "$here/tools/heavy-job-cycle.sh" "$1/tools/"; else sed "$2" "$here/tools/heavy-job-cycle.sh" >"$1/tools/heavy-job-cycle.sh"; fi
}
# OLD: the old admission rule (an aged waiter jumps the queue, T-8bf1 before graded aging), no canonical-copy module, no cycle module, a fake wake tool
mkold() {
	mkdir -p "$1/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$1/tools/" || return 1
	python3 -I - "$real" "$1/tools/heavy-job" <<'PY' || return 1
import sys
s = open(sys.argv[1]).read()
o = 'eff=$(effprio "$prio" "$ep" "$now")'
n = 'eff=$prio; [[ $ep =~ ^[0-9]+$ ]] && [ $((now - ep)) -ge "$maxage" ] && eff=-1'
assert s.count(o) == 1
open(sys.argv[2], "w").write(s.replace(o, n))
PY
	chmod +x "$1/tools/heavy-job"
	printf '#!/bin/bash\necho "wake $*" >>"%s"\n' "$2" >"$1/tools/job-wake"; chmod +x "$1/tools/job-wake"
}
fakew() {   # fakew DIR NAME SEQ EPOCH STATE EXTRA: a ticket held by a live process
	local t=$1/hq/$3.$2 p; mkdir -p "$1/hq"
	bash -c 'exec 9>>"$1"; flock -n 9 || exit 1; exec sleep 60' _ "$t" & p=$!; echo "$p" >>"$w/cyc.pid"
	for _ in $(seq 100); do [ -e "$t" ] && ! ( flock -n 3 ) 3<"$t" 2>/dev/null && break; sleep 0.05; done
	printf 'u %s %s %s %s%s\n' "$2" "$p" "$4" "$5" "$6" >"$t"; fpid=$p
}
st() { HEAVY_JOB_CANONICAL=$1/tools/heavy-job HEAVY_JOB_QUEUE_DIR=$2/hq HEAVY_JOB_LOCK=$2/lock "$1/tools/heavy-job" status 2>&1; }
chk_b() {   # chk_b VARIANT-DIR TAG: sets b1 b2 b3 (1 = as expected): fresh lock = no cycle; starting head (admitted, fresh) = no cycle; old lock + old head = WAIT-CYCLE at the default floor 120
	local d=$w/b$2 now; now=$(date +%s); mkdir -p "$d"; b1=0; b2=0; b3=0
	fakew "$d" fa 000001 $((now - 500)) waiting " blob=deadbeef"; touch "$d/lock"   # lock file changed just now, one old waiter behind it
	o=$(st "$1" "$d"); ! grep -q 'WAIT-CYCLE' <<<"$o" && b1=1
	touch -d '-300 sec' "$d/lock"; fakew "$d" fh 000002 $((now - 400)) admitted ""      # an admitted head (ticket just written) and an old lock
	o=$(st "$1" "$d"); ! grep -q 'WAIT-CYCLE' <<<"$o" && b2=1
	touch -d '-300 sec' "$d/hq/000002.fh"
	o=$(st "$1" "$d"); grep -q 'heavy-job: WAIT-CYCLE free=[0-9]*s waiters=1 edges=none' <<<"$o" && grep -q 'HEAVY GAP: CYCLE-REQUEUE ticket=000001.fa pid=[0-9]* not re-queued: no detached unit' <<<"$o" && b3=1
}
quiet() {   # quiet DIR: no ticket of DIR's queue is held by a live process
	local f; for f in "$1"/hq/[0-9]*.*; do [ -e "$f" ] && ! ( flock -n 3 ) 3<"$f" 2>/dev/null && return 1; done; return 0
}
hang_dump() {   # hang_dump DIR: the state of a queue that did not drain (DEBUG_CYC=1 prints it; T-a1e4 root-cause capture)
	local f p; { date +%T; ps -eo pid,ppid,pgid,stat,wchan:22,args | grep -F "$1" | grep -v grep
		for f in "$1"/hq/[0-9]*.*; do [ -e "$f" ] || continue; echo "== ticket ${f##*/}: $(cat "$f")"; p=$(awk '{print $3}' "$f" 2>/dev/null); [ -d "/proc/$p" ] && ls -l "/proc/$p/fd" 2>&1 | awk '{print "   fd " $9 " " $10 " " $11}'; done; cat /proc/locks; } >"$1/hang.txt" 2>&1
}
# chk_e VARIANT-DIR TAG: sets e1=1 when a waiter that wins the lock of a REPLACED predecessor file leaves the NEW ticket of the same name alone (T-a1e4 root cause: a re-queued waiter restarts under
# its old ticket name after the sweep removed the dead file; the waiter blocked on the old inode got its lock and `rm -f`'d the path = the live new ticket, which then spun invisible and never ran)
chk_e() {
	local d=$w/e$2 now po pw dl n0; now=$(date +%s); mkdir -p "$d"; e1=0
	fakew "$d" fx 000001 $((now - 10)) waiting ""; po=$fpid
	HEAVY_JOB_QUEUE_DIR=$d/hq HEAVY_JOB_LOCK=$d/lock HEAVY_JOB_REEVAL=30 HEAVY_JOB_CANONICAL=$1/tools/heavy-job "$1/tools/heavy-job" run --class analysis --label we --wait 120 -- true >"$d/we.out" 2>&1 &
	pw=$!; echo "$pw" >>"$w/cyc.pid"
	dl=$((SECONDS + 120)); until grep -q 'waiting behind 000001.fx' "$d/we.out" 2>/dev/null || [ "$SECONDS" -ge "$dl" ]; do sleep 0.1; done   # the waiter blocks on the old file (30 s slice)
	mv "$d/hq/000001.fx" "$d/hq/.old-fx"   # the old inode stays locked by its holder; the waiter has it open
	fakew "$d" fx 000001 $((now - 10)) waiting ""   # a NEW live ticket under the same name
	n0=$(grep -c 'waiting behind 000001.fx' "$d/we.out")
	kill "$po"   # the old lock is free: the waiter wins it. Fixed code re-lists (one more 'waiting behind'); the mutant removes the new ticket and runs
	dl=$((SECONDS + 120)); until [ "$(grep -c 'waiting behind 000001.fx' "$d/we.out")" -gt "$n0" ] || grep -q '^RESULT' "$d/we.out" || [ "$SECONDS" -ge "$dl" ]; do sleep 0.1; done
	[ -e "$d/hq/000001.fx" ] && ! grep -q '^RESULT' "$d/we.out" && [ "$(grep -c 'waiting behind 000001.fx' "$d/we.out")" -gt "$n0" ] && e1=1
	kill "$pw" "$fpid" 2>/dev/null
}
# scen VARIANT-DIR TAG: (a)+(c); writes $w/s$TAG/res
scen() {
	local v=$1 d=$w/s$2 old=$w/s$2/old bin=$1/tools/heavy-job i num num1 num2 lbl dl
	mkdir -p "$d/cwd" "$d/sc"; mkold "$old" "$d/wake.log" || { echo "oldfail" >"$d/res"; return; }
	printf 'T-0010\tx\tx\tx\tx\tx\tx\tx\tx\tP1\nT-0011\tx\tx\tx\tx\tx\tx\tx\tx\tP1\n' >"$d/idx"
	export SC_DIR=$d/sc HEAVY_JOB_QUEUE_DIR=$d/hq HEAVY_JOB_LOCK=$d/lock HEAVY_JOB_CANONICAL=$bin HEAVY_JOB_QUEUE_INDEX=$d/idx HEAVY_JOB_MAXAGE=100 HEAVY_JOB_REEVAL=1 HEAVY_JOB_CYCLE_FLOOR=4 HEAVY_JOB_SYSTEMD_RUN=$d/../sc/sdrun HEAVY_JOB_SYSTEMCTL=$d/../sc/sctl
	cd "$d/cwd" || return
	"$bin" run --class analysis --label hold --wait 300 -- bash -c "until [ -e $d/release ]; do sleep 0.2; done" >"$d/hold.out" 2>&1 &   # held until the test releases it (T-a1e4: a fixed 'sleep 19' ended before the old copies were even started at load 50, so no cycle formed)
	for _ in $(seq 100); do [ -e "$d/hq/000001.hold" ] && break; sleep 0.1; done
	# the trunk waiters are enqueued 110 s in the past (HEAVY_JOB_SEQ/EPOCH, the re-queue seam): one MAXAGE (100 s) old at once = graded aging P2 -> level 1, the old copies see them jump the queue; no sleep, so host load cannot move the ages (T-a1e4)
	i=2; for l in w1 w2; do HEAVY_JOB_SEQ=$i HEAVY_JOB_EPOCH=$(($(date +%s) - 110)) "$bin" run --class analysis --label $l --wait 300 -- bash -c "echo $l >>$d/order" >"$d/$l.out" 2>&1 & for _ in $(seq 600); do compgen -G "$d/hq/[0-9]*.$l" >/dev/null && break; sleep 0.1; done; i=$((i + 1)); done   # w2 only starts once w1 holds its ticket
	for i in o1-T-0010 o2-T-0011; do "$old/tools/heavy-job" detach --label $i --wake-hook hk -- "$old/tools/heavy-job" run --class analysis --label $i --wait 300 -- bash -c "echo ${i%%-*} >>$d/order" >"$d/$i.det" 2>&1 </dev/null
		for _ in $(seq 600); do compgen -G "$d/hq/[0-9]*.$i" >/dev/null && break; sleep 0.1; done   # wait for the detached copy's ticket (a fixed 0.3 s raced it on a loaded host, T-70d5 qgate4)
		num=$(compgen -G "$d/hq/[0-9]*.$i" | head -1); num=${num##*/}; num=${num%%.*}; case $i in o1*) num1=$num ;; *) num2=$num ;; esac; done   # each number read right after its own ticket appears (o1 may finish before o2's wait ends)
	[ -n "$num1" ] && [ -n "$num2" ] || echo "cycle test: ticket number missing num1=$num1 num2=$num2"
	touch "$d/release"   # all four waiters are queued behind the hold (the old copies are < MAXAGE old): free the lock, the cycle forms now
	dl=$((SECONDS + 180)); while [ "$(wc -l <"$d/order" 2>/dev/null || echo 0)" -lt 4 ] && [ "$SECONDS" -lt "$dl" ]; do sleep 0.2; done   # wall-clock drain deadline (400 x 0.2 s ran out at load 60, o2 still queued behind w2: T-70d5 qgate4)
	dl=$((SECONDS + 120)); while ! quiet "$d"; do [ "$SECONDS" -lt "$dl" ] || { why="$why a:not-quiet"; hang_dump "$d"; break; }; sleep 0.2; done   # no live ticket left: a stop-step mutant leaves the old copy of each re-queued waiter, which runs the job a second time (order gets 5-6 lines) - waiting for quiet makes that deterministic instead of a race against the old copy's exit (T-a1e4: it survived 1 run in 3)
	dl=$((SECONDS + 30)); while [ "$(wc -l <"$d/wake.log" 2>/dev/null || echo 0)" -lt 2 ] && [ "$SECONDS" -lt "$dl" ]; do sleep 0.2; done; sleep 0.5   # the wake hooks fire after the job ends; 0.5 s more catches a wrong extra wake
	lbl=$(cat "$d"/cwd/logs/heavy-job/o1-T-0010/detach-*.log "$d"/cwd/logs/heavy-job/o2-T-0011/detach-*.log 2>/dev/null)
	a=0; c=0; [ -n "${why:-}" ] || why=   # why = the (a)/(c) conditions that failed, printed by the scenario so a FAIL names its cause
	[ "$(sort "$d/order" 2>/dev/null | tr '\n' ' ')" = "o1 o2 w1 w2 " ] || why="$why a:order=[$(tr '\n' ' ' <"$d/order" 2>/dev/null)]"
	grep -h 'WAIT-CYCLE free=' "$d"/w1.out "$d"/w2.out | grep -q 'waiters=[34]' || why="$why a:no-WAIT-CYCLE-3-4"
	grep -q "CYCLE-REQUEUE unit=pixos-hj-o[12]-T-001[01]" <<<"$lbl" || why="$why a:no-CYCLE-REQUEUE"
	grep -q "admitted $num1.o1-T-0010 prio=P1" <<<"$lbl" || why="$why a:o1-not-admitted-as-$num1"
	grep -q "admitted $num2.o2-T-0011 prio=P1" <<<"$lbl" || why="$why a:o2-not-admitted-as-$num2"
	[ "$(awk 'NR==FNR{if($1=="o1")x=FNR;if($1=="o2")y=FNR;if($1=="w1")p=FNR;if($1=="w2")q=FNR;next}END{print (x<y&&p<q)?1:0}' "$d/order" "$d/order")" = 1 ] || why="$why a:fifo"
	for i in $(grep -h '^heavy-job: CYCLE-REQUEUE unit=' "$d"/w1.out "$d"/w2.out 2>/dev/null | sed -n 's/.* pid=\([0-9]*\).*/\1/p' | sort -u); do kill -0 "$i" 2>/dev/null && why="$why a:old-waiter-$i-alive"; done   # the stop step: no re-queued old copy still waits next to its new one
	[ -z "$why" ] && a=1
	[ -z "${DEBUG_CYC:-}" ] || [ -z "$why" ] || { [ -e "$d/hang.txt" ] || hang_dump "$d"; }
	[ "$(grep -c '^wake hk o1-T-0010 0$' "$d/wake.log" 2>/dev/null)" = 1 ] && [ "$(grep -c '^wake hk o2-T-0011 0$' "$d/wake.log" 2>/dev/null)" = 1 ] && [ "$(wc -l <"$d/wake.log")" = 2 ] && c=1
	[ "$c" = 1 ] || why="$why c:wake=[$(tr '\n' ';' <"$d/wake.log" 2>/dev/null)]"
	if [ -n "$why" ] && { [ "$2" = 0 ] || [ -n "${DEBUG_CYC:-}" ]; }; then echo "cycle test scenario $2:$why"; fi   # scenarios 1 and 2 are the mutants: their failure is the expected result (printed with DEBUG_CYC only)
	echo "a=$a c=$c" >"$d/res"
}
mkvar "$w/V0" - || exit 2
mkvar "$w/V1" '/"\$sc" --user kill --signal/d;/"\$sc" --user stop/d' || exit 2
mkvar "$w/V2" 's/HEAVY_JOB_SEQ="\$nn" //' || exit 2
mkvar "$w/V3" 's/^hj_cyc_floor=.*; hj_seq=/hj_cyc_floor=0; hj_seq=/' || exit 2
mkvar "$w/V4" - || exit 2; sed -i 's/^\t\t\[ "\$same" = 1 \] || continue; //' "$w/V4/tools/heavy-job"
cmp -s "$real" "$w/V4/tools/heavy-job" && { ok "mutant V4: the sed edit changed nothing (the marker moved)" 0; exit 1; }
for v in 1 2 3; do cmp -s "$here/tools/heavy-job-cycle.sh" "$w/V$v/tools/heavy-job-cycle.sh" && { ok "mutant V$v: the sed edit changed nothing (the marker moved)" 0; exit 1; }; done
for v in 0 1 2; do scen "$w/V$v" "$v" 2>/dev/null & done   # the three scenario runs side by side (own queue, lock, scopes)
wait
[ -z "${DEBUG_CYC:-}" ] || { for f in "$w"/s0/*.out "$w"/s0/*.det "$w"/s0/cwd/logs/heavy-job/*/*.log "$w"/s0/wake.log "$w"/s0/hang.txt "$w"/s0/sc/calls.log; do echo "== $f"; tail -${DEBUG_CYC_LINES:-12} "$f"; done; ls "$w/s0/hq"; }
# (d) unreadable environ: a fake process tree, environ mode 000
mkdir -p "$w/fp/90001" "$w/fp/90002"
dd=$w/d; now=$(date +%s); mkdir -p "$dd"; fakew "$dd" fu 000001 $((now - 500)) waiting " unit=pixos-hj-fu-1-1 blob=deadbeef"; touch -d '-300 sec' "$dd/lock" 2>/dev/null || { : >"$dd/lock"; touch -d '-300 sec' "$dd/lock"; }
printf '/bin/bash\0/x/tools/heavy-job\0run\0--label\0fu\0' >"$w/fp/$fpid.cmdline"; mkdir -p "$w/fp/$fpid"; mv "$w/fp/$fpid.cmdline" "$w/fp/$fpid/cmdline"; printf 'PPid:\t90002\n' >"$w/fp/$fpid/status"
printf 'bash\0-c\0script\0pixos-hj-fu-1-1\0/x/tools/heavy-job\0run\0--label\0fu\0' >"$w/fp/90002/cmdline"; printf 'PPid:\t1\n' >"$w/fp/90002/status"; echo SECRET=1 >"$w/fp/90002/environ"; chmod 000 "$w/fp/90002/environ"
od=$(HEAVY_JOB_PROC=$w/fp st "$w/V0" "$dd")
ok "d: unreadable environ = HEAVY GAP 'cannot read .../environ', waiter untouched (ticket there, process alive, no CYCLE-REQUEUE line)" "$(grep -q "HEAVY GAP: CYCLE-REQUEUE unit=pixos-hj-fu-1-1 not re-queued: cannot read $w/fp/90002/environ" <<<"$od" && ! grep -q '^heavy-job: CYCLE-REQUEUE ' <<<"$od" && [ -e "$dd/hq/000001.fu" ] && kill -0 "$fpid" 2>/dev/null && echo 1 || echo 0)"
# (f)/(g) T-842e: a fake OLD-layout wrapper (the scope's command does not start with heavy-job): the queued waiter is ENDED (CYCLE-END, process gone, ticket lock free); a ticket that is already
# running/admitted is never touched. environ/cwd/log are readable so the parse reaches the command check.
mkfake() {   # mkfake DIR TAG STATE WRAPPER-PID: queue DIR with one aged old-layout waiter; sets fpid
	local d=$1 t=$2 st=$3 wp=$4 u=pixos-hj-$2-1-1 now; now=$(date +%s); mkdir -p "$d" "$w/fp/$wp"
	fakew "$d" "$t" 000001 $((now - 500)) "$st" " unit=$u blob=deadbeef"; touch -d '-300 sec' "$d/lock" 2>/dev/null || { : >"$d/lock"; touch -d '-300 sec' "$d/lock"; }
	mkdir -p "$w/fp/$fpid" "$w/fp/$wp/fd"; printf '/bin/bash\0/x/tools/heavy-job\0run\0--label\0%s\0' "$t" >"$w/fp/$fpid/cmdline"; printf 'PPid:\t%s\n' "$wp" >"$w/fp/$fpid/status"
	printf 'bash\0-c\0script\0%s\0env\0X=1\0Y=2\0/x/tools/heavy-job\0run\0--label\0%s\0' "$u" "$t" >"$w/fp/$wp/cmdline"; printf 'PPid:\t1\n' >"$w/fp/$wp/status"
	echo A=1 >"$w/fp/$wp/environ"; ln -sfn "$d" "$w/fp/$wp/cwd"; : >"$d/unit.log"; ln -sfn "$d/unit.log" "$w/fp/$wp/fd/1"
}
mkfake "$w/f" fo waiting 90011; pf=$fpid
of=$(HEAVY_JOB_PROC=$w/fp st "$w/V0" "$w/f"); sleep 0.3
ok "f: old-layout wrapper, ticket waiting = CYCLE-END line (stdout + unit log), waiter process killed, no CYCLE-REQUEUE" "$(grep -q "^heavy-job: CYCLE-END unit=pixos-hj-fo-1-1 pid=$pf reason=the scope's command does not start with heavy-job" <<<"$of" && grep -q 'CYCLE-END unit=pixos-hj-fo-1-1' "$w/f/unit.log" && ! kill -0 "$pf" 2>/dev/null && ! grep -q '^heavy-job: CYCLE-REQUEUE ' <<<"$of" && echo 1 || echo 0)"
mkfake "$w/g" fr running 90012; pg=$fpid
og=$(HEAVY_JOB_PROC=$w/fp st "$w/V0" "$w/g"); sleep 0.3
ok "g: the same old-layout shape on a RUNNING ticket is never ended (process alive, no CYCLE-END)" "$(! grep -q 'CYCLE-END' <<<"$og" && kill -0 "$pg" 2>/dev/null && echo 1 || echo 0)"
chk_e "$w/V0" 0; ev0=$e1; chk_e "$w/V4" 4; ev4=$e1
ok "e: a waiter that wins the lock of a replaced predecessor file (a re-queued waiter's new ticket of the same name) leaves the new ticket alone and keeps waiting" "$ev0"
ok "mutant drop-the-same-file-check is killed by (e)" "$([ "$ev4" = 0 ] && echo 1 || echo 0)"
for v in 0 3; do chk_b "$w/V$v" "$v"; eval "bb$v=\"\$b1\$b2\$b3\""; done
ok "b: lock changed < 120 s ago = no WAIT-CYCLE; head starting (admitted, fresh) = none; old lock + old head = WAIT-CYCLE at the default floor 120" "$([ "$bb0" = 111 ] && echo 1 || echo 0)"
ok "a: 2 old-rule + 2 canonical waiters (the 2026-11-03 shape): WAIT-CYCLE (3-4 waiters) printed, old ones re-queued with their ticket number and prio, queue drained (o1 o2 w1 w2)" "$(grep -q 'a=1' "$w/s0/res" && echo 1 || echo 0)"
ok "c: re-queued waiter keeps label + wake hook: the hook fires exactly once per job, at the job end (rc 0), never for the stopped scope" "$(grep -q 'c=1' "$w/s0/res" && echo 1 || echo 0)"
ok "mutant drop-the-stop-step is killed by (a)" "$(grep -q 'a=0' "$w/s1/res" && echo 1 || echo 0)"
ok "mutant drop-the-seq-preservation is killed by (a)" "$(grep -q 'a=0' "$w/s2/res" && echo 1 || echo 0)"
ok "mutant drop-the-floor is killed by (b)" "$([ "$bb3" != 111 ] && echo 1 || echo 0)"
[ "$bad" = 0 ] && echo "RESULT: PASS test_heavy_job_cycle"
[ "$bad" = 0 ] && echo "RESULT: PASS cases=$n" || echo "RESULT: FAIL cases=$n failed=$bad"
[ "$bad" = 0 ]
