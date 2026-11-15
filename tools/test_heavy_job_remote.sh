#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, launch, lockfree, ...) are set by the sourced test_heavy_job_lib.sh and its helpers
# Part 'remote' of the tools/heavy-job host test (split from test_heavy_job.sh, PLAN-GATE-2X K4a); setup + helpers: test_heavy_job_lib.sh.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

# ---- remote-job resubmit check with a fake ssh/scp/docker
repo=$w/repo; mkdir -p "$repo/tools" "$w/bin" "$w/sroot/work/jobs" "$w/home/.local/bin"
cp "$here/tools/remote-job" "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$repo/tools/"; echo x >"$repo/f"
git -C "$repo" init -q && git -C "$repo" -c user.email=t@t -c user.name=t add -A && git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init || exit 2
echo "run" >/dev/null
cat >"$w/bin/ssh" <<'F'
#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
shift
s=$(cat)
HOME=$FAKE_HOME REMOTE_LOADAVG=$FAKE_LOADAVG "$@" <<<"$s"
F
cat >"$w/bin/scp" <<'F'
#!/bin/bash
args=(); while [ $# -gt 0 ]; do case $1 in -q|-r) shift ;; -o) shift 2 ;; *) args+=("$1"); shift ;; esac; done
map() { case $1 in *:/*) echo "${1#*:}" ;; *:*) echo "$FAKE_HOME/${1#*:}" ;; *) echo "$1" ;; esac; }
dst=$(map "${args[-1]}"); unset 'args[-1]'
for a in "${args[@]}"; do cp -r "$(map "$a")" "$dst" || exit 1; done
F
cat >"$w/home/.local/bin/docker" <<'F'
#!/bin/bash
case $1 in ps) [ ! -e "$FAKE_HOME/docker.psfail" ] || { echo "Cannot connect to the Docker daemon" >&2; exit 1; }; cat "$FAKE_HOME/docker.ps" 2>/dev/null; exit 0 ;; kill) echo "$2" >>"$FAKE_HOME/docker.killed"; : >"$FAKE_HOME/docker.ps"; exit 0 ;; *) exit 9 ;; esac
F
chmod +x "$w/bin/ssh" "$w/bin/scp" "$w/home/.local/bin/docker"
export OFFLOAD_ROOT=$w/sroot OFFLOAD_MIN_FREE_GB=1   # R64: the fake offload-host work root is $OFFLOAD_ROOT/work
export PATH=$w/bin:$PATH FAKE_HOME=$w/home FAKE_LOADAVG=$w/load REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_HELD=test-bypass   # this section tests remote-job itself, not the queue
J=$w/sroot/work/jobs
rj --where offload-host selftest
ok "no leftovers: selftest runs on offload-host, PASS, dir removed" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS remote-job selftest where=offload-host' "$out")" = 1 ] && [ -z "$(find "$J" -mindepth 1 -maxdepth 1 -not -name '.*')" ] && echo 1 || echo 0)"
ok "heavy lock holder written on offload-host" "$(grep -q '^selftest ' "$J/.heavy.lock" && echo 1 || echo 0)"
mkdir -p "$J/selftest.1700000000.4242/out"; echo 0 >"$J/selftest.1700000000.4242/job.rc"; echo finished >"$J/selftest.1700000000.4242/job.log"; echo 99999999 >"$J/selftest.1700000000.4242/pid"
rj --where offload-host selftest
ok "uncollected result refuses: RESULT: ERROR resubmit selftest has uncollected result, exit 2, entry printed" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR resubmit selftest has uncollected result' "$out")$(has 'ENTRY selftest.1700000000.4242 rc=0 age=' "$out")" | grep -q 11 && echo 1 || echo 0)"
ok "refused run did not touch the leftover" "$([ -d "$J/selftest.1700000000.4242" ] && echo 1 || echo 0)"
mkdir -p "$J/other.1700000000.1"; echo 0 >"$J/other.1700000000.1/job.rc"
rj --where offload-host --collect selftest.1700000000.4242 selftest
ok "--collect copies job.log/job.rc/out to logs/remote-jobs/JOB/DIR, removes the remote dir, then the run proceeds" "$([ $rc = 0 ] && [ -f "$repo/logs/remote-jobs/selftest/selftest.1700000000.4242/job.log" ] && [ -f "$repo/logs/remote-jobs/selftest/selftest.1700000000.4242/job.rc" ] && [ ! -d "$J/selftest.1700000000.4242" ] && echo 1 || echo 0)"
ok "a different job's dir is never touched" "$([ -d "$J/other.1700000000.1" ] && echo 1 || echo 0)"
# orphan: no job.rc, runner dead, container alive
mkdir -p "$J/selftest.1700000001.5"; echo 99999999 >"$J/selftest.1700000001.5/pid"; echo "rj-selftest.1700000001.5" >"$w/home/docker.ps"; echo "rj-other.1.1" >>"$w/home/docker.ps"
rj --where offload-host selftest
ok "orphan container refuses: RESULT: ERROR resubmit selftest has orphan, container named" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR resubmit selftest has orphan' "$out")$(has 'container=rj-selftest.1700000001.5' "$out")" | grep -q 11 && echo 1 || echo 0)"
rj --where offload-host --kill-orphan selftest
ok "--kill-orphan kills only that dir's container (rj-other untouched), then still refuses until collected" "$([ "$(cat "$w/home/docker.killed")" = rj-selftest.1700000001.5 ] && [ $rc = 2 ] && echo 1 || echo 0)"
# a live runner pid is not an orphan to kill and cannot be collected
sleep 30 & lp=$!; echo $lp >"$w/e.pid"; echo $lp >"$J/selftest.1700000001.5/pid"
echo rj-selftest.1700000001.5 >"$w/home/docker.ps"; kb=$(cat "$w/home/docker.killed")
rj --where offload-host --kill-orphan selftest
ok "live runner pid + live container: --kill-orphan does not kill the container (runner-dead guard)" "$([ "$(cat "$w/home/docker.killed")" = "$kb" ] && echo 1 || echo 0)"
ok "live runner pid: --kill-orphan does nothing to it" "$(kill -0 "$lp" 2>/dev/null && echo 1 || echo 0)"
rj --where offload-host --collect selftest.1700000001.5 selftest
ok "--collect refuses while the runner is alive" "$([ $rc = 2 ] && [ "$(has 'still alive' "$out")" = 1 ] && [ -d "$J/selftest.1700000001.5" ] && echo 1 || echo 0)"
kill "$lp" 2>/dev/null; rm -rf "$J/selftest.1700000001.5" "$J/other.1700000000.1"
# --keep marker: kept dir is skipped by the next check
rj --where offload-host --keep selftest
kd=$(ls -d "$J"/selftest.*.* 2>/dev/null | head -1)
ok "--keep leaves the dir with a keep marker" "$([ $rc = 0 ] && [ -n "$kd" ] && [ -e "$kd/keep" ] && echo 1 || echo 0)"
rj --where offload-host selftest
ok "a dir with a keep marker does not block the next run" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS remote-job' "$out")" = 1 ] && echo 1 || echo 0)"
# machine-wide heavy lock: held by someone else -> bounded wait, then an operator-style refusal (no local fallback)
( flock 8; sleep 8 ) 8>>"$J/.heavy.lock" & echo $! >"$w/f.pid"; sleep 0.5
REMOTE_JOB_HEAVY_WAIT=2 rj --where offload-host selftest
ok "offload-host heavy lock held: bounded wait then refusal, no local fallback" "$([ $rc = 2 ] && [ "$(has 'heavy lock' "$out")" = 1 ] && echo 1 || echo 0)"

# ---- r2 F7: the REAL probe script runs under a local `bash -s` (fake ssh executes what it receives), with fake docker/pgrep first on PATH
# (the script reads /proc/loadavg and /proc/meminfo directly, so those two are the real values of this host; limits are lifted for that reason)
mkdir -p "$w/fb" "$w/q2"
printf '#!/bin/bash\necho "$*" >"%s/pgrep.args"; echo "4242 node vitest"\n' "$w" >"$w/fb/pgrep"; chmod +x "$w/fb/pgrep"
cat >"$w/fb/sshx" <<'F'
#!/bin/bash
while [ "$1" = -o ]; do shift 2; done
shift   # host
s=$(cat)
HOME=$FAKE_HOME PATH=$FB:$PATH "$@" <<<"$s"
F
chmod +x "$w/fb/sshx"
export FB=$w/fb REMOTE_JOB_SSH=$w/fb/sshx OFFLOAD_QUEUE_DIR=$w/q2 OFFLOAD_REPROBE=1 OFFLOAD_MAXLOAD1=1000000 OFFLOAD_MINAVAIL_GB=0
echo "rj-gate-pre.7," | tr -d ',' >"$w/home/docker.ps"; rm -f "$w/home/docker.psfail"
RE='vitest|/mnt/offload-data/other-work.*(node|tsc|esbuild)'
out=$(env -u OFFLOAD_QUEUE_HELD "$here/tools/heavy-job" offload-host status 2>&1); rc=$?
ok "real probe script via bash -s: rj- container and foreign process from the fake tools reach the verdict" "$([ $rc = 0 ] && echo "$(has 'rj=rj-gate-pre.7' "$out")$(has 'foreign=4242 node vitest' "$out")$(has 'would refuse' "$out")" | grep -q 111 && echo 1 || echo 0)"
ok "real probe script: the %q-quoted foreign regex arrives intact at pgrep" "$([ "$(cat "$w/pgrep.args")" = "-af -- $RE" ] && echo 1 || echo 0)"
touch "$w/home/docker.psfail"
out=$(env -u OFFLOAD_QUEUE_HELD "$here/tools/heavy-job" offload-host run --owner alice --label s-dkfail --wait 20 -- echo should-not-run 2>&1); rc=$?
ok "real probe script: docker ps failing is a probe ERROR, CMD not run (F3)" "$([ $rc = 2 ] && [ "$(has 'docker check failed on offload-host: docker ps failed' "$out")" = 1 ] && ! grep -q '^should-not-run' <<<"$out" && echo 1 || echo 0)"
rm -f "$w/home/docker.psfail" "$w/home/docker.ps"; printf '#!/bin/bash\necho "$*" >"%s/pgrep.args"\n' "$w" >"$w/fb/pgrep"
# ---- r2 F7b: remote-job's REAL queue branch with the real heavy-job (no OFFLOAD_QUEUE_HELD): ticket, admission, re-exec, run on the fake offload-host
cp "$here/tools/heavy-job" "$here/tools/heavy-job-canon.sh" "$repo/tools/"; git -C "$repo" -c user.email=t@t -c user.name=t add tools/heavy-job tools/heavy-job-canon.sh tools/offload-conf.sh tools/offload_conf.py && git -C "$repo" -c user.email=t@t -c user.name=t commit -qm hj
out=$(cd "$repo" && env -u OFFLOAD_QUEUE_HELD REMOTE_JOB_OWNER=tester tools/remote-job --where offload-host selftest 2>&1); rc=$?
ok "remote-job --where offload-host queues through the real heavy-job: ticket, admitted, job ran, one RESULT: PASS remote-job" "$([ $rc = 0 ] && echo "$(has 'offload-host queue: ticket .*rj-selftest owner=tester' "$out")$(has 'offload-host admitted' "$out")$(has '^RESULT: PASS remote-job selftest where=offload-host' "$out")$(has 'heavy-job: RESULT PASS' "$out")" | grep -q 1111 && echo 1 || echo 0)"
ok "queue ticket gone after the queued remote-job" "$([ -z "$(ls "$w"/q2/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"

# ---- detach (AP-227): fake systemd-run / systemctl, never the real ones
dhj --label d-pass --log "$w/d1.log" -- bash -c 'echo "args:$1|$2"; exit 0' x 'a b' 'c  d'
w1=$out
endwait "$w/d1.log"
ok "detach: PASS, RESULT line, unit pixos-hj-<label>-<epoch>, detached line" "$([ $rc = 0 ] && echo "$(has "^RESULT: PASS detach unit=pixos-hj-d-pass-[0-9]*-[0-9]* log=$w/d1.log\$" "$out")$(has "^heavy-job: detached unit=pixos-hj-d-pass-[0-9]*-[0-9]* log=$w/d1.log pid=[0-9]* end-line=.* waiting-line=.unit=pixos-hj-d-pass-[0-9]*-[0-9]* $w/d1.log [0-9]*.\$" "$out")" | grep -q 11 && echo 1 || echo 0)"
ok "detach: log has the start line first, args with spaces intact, DETACHED-END rc=0 with the unit" "$([ "$(head -1 "$w/d1.log" | grep -c '^heavy-job: detach unit=pixos-hj-d-pass-[0-9]*-[0-9]* start=[0-9T:Z-]* cmd=')" = 1 ] && echo "$(has 'args:a b|c  d' "$(cat "$w/d1.log")")$(has '^heavy-job: DETACHED-END rc=0 unit=pixos-hj-d-pass-' "$(cat "$w/d1.log")")" | grep -q 11 && echo 1 || echo 0)"
touch "$w/dt/inactive"; dhj --label d-rc3 --log "$w/d2.log" -- bash -c 'exit 3'; rm -f "$w/dt/inactive"   # scope not seen active: the end line decides
endwait "$w/d2.log"
ok "detach: CMD rc 3 within the launch check -> RESULT: FAIL exit 1 naming rc=3, log DETACHED-END rc=3 (r1 F2)" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL detach unit=.*rc=3' "$out")" = 1 ] && [ "$(has '^heavy-job: DETACHED-END rc=3 unit=' "$(cat "$w/d2.log")")" = 1 ] && echo 1 || echo 0)"
out=$(HEAVY_JOB_SYSTEMD_RUN=$w/dt/none HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$here/tools/heavy-job" detach --label d-nosr --log "$w/d3.log" -- true 2>&1); rc=$?
ok "detach: systemd-run missing -> RESULT: ERROR exit 2, nothing launched" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR detach: .*not found' "$out")" = 1 ] && [ ! -e "$w/d3.log" ] && echo 1 || echo 0)"
touch "$w/dt/env.fail"; dhj --label d-env --log "$w/d4.log" -- true; rm -f "$w/dt/env.fail"
ok "detach: show-environment failing -> RESULT: ERROR exit 2, nothing launched" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR detach: .*show-environment' "$out")" = 1 ] && [ ! -e "$w/d4.log" ] && echo 1 || echo 0)"
touch "$w/dt/inactive"; dhj --label d-inact --log "$w/d5.log" -- bash -c "echo \$\$ > $w/d5.pid; exec timeout 60 tail -f /dev/null"   # pid file: cleanup() kills it
ok "detach: inactive unit and no end line -> RESULT: ERROR exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR detach: unit pixos-hj-d-inact-[0-9]*-[0-9]* not active after 10 s and no end line' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$w/dt/inactive"; kill "$(cat "$w/d5.pid" 2>/dev/null)" 2>/dev/null
# r1 F1: systemd-run fails ("already exists") while the log holds an older unit's end line -> ERROR, not PASS
printf 'heavy-job: DETACHED-END rc=0 unit=pixos-hj-d-stale-1-1\n' >"$w/d7.log"; touch "$w/dt/inactive" "$w/dt/srfail"
dhj --label d-stale --log "$w/d7.log" -- true; rm -f "$w/dt/inactive" "$w/dt/srfail"
ok "detach: systemd-run failure + stale end line of another unit -> RESULT: ERROR exit 2 with its rc (r1 F1/F2)" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR detach: systemd-run exited rc=1 without starting' "$out")" = 1 ] && echo 1 || echo 0)"
# r1 F4: a caller holding the lock/queue does not pass it on to the detached job
out=$(HEAVY_JOB_HELD=outer OFFLOAD_QUEUE_HELD=outer HEAVY_JOB_QUIET=1 HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$here/tools/heavy-job" detach --label d-env2 --log "$w/d8.log" -- bash -c 'echo "held=${HEAVY_JOB_HELD-unset} q=${OFFLOAD_QUEUE_HELD-unset} quiet=${HEAVY_JOB_QUIET-unset}"' 2>&1 </dev/null); rc=$?
endwait "$w/d8.log"
ok "detach: HEAVY_JOB_HELD/OFFLOAD_QUEUE_HELD/HEAVY_JOB_QUIET cleared for the job (r1 F4)" "$([ $rc = 0 ] && [ "$(has '^held=unset q=unset quiet=unset$' "$(cat "$w/d8.log")")" = 1 ] && echo 1 || echo 0)"
dhj --label 'bad label!' --log "$w/d6.log" -- true
ok "detach: bad label -> RESULT: ERROR usage exit 2" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR usage' "$out")" = 1 ] && echo 1 || echo 0)"
ok "detach: WARNING line when the systemd hooks are set" "$(has '^heavy-job: WARNING systemd-run/systemctl replaced by env (host test only)$' "$w1")"
dhj --label d-def -- true
endwait "$PWD/logs/heavy-job/d-def/detach.log"
ok "detach: default log is <root>/logs/heavy-job/<label>/detach-<unit>.log (T-e8ad: one file per unit), detach.log links to the latest" "$([ $rc = 0 ] && [ "$(has "log=$PWD/logs/heavy-job/d-def/detach-pixos-hj-d-def-[0-9]*-[0-9]*.log " "$out")" = 1 ] && [ -L "$PWD/logs/heavy-job/d-def/detach.log" ] && [ "$(grep -c 'DETACHED-END' "$PWD/logs/heavy-job/d-def/detach.log")" = 1 ] && echo 1 || echo 0)"

# ---- T-e8ad (tools/heavy-job host test, part remote): a detached job that never ran its CMD is never a job end.
#   1 queued behind a holder / memory gate longer than its --wait: still queued, `status` shows unit=U state=queued, then it runs and ends with DETACHED-END rc=0 (mutant: wait=$cap removed)
#   2 a heavy-job that never started its CMD (bad class, signal while queued): DETACHED-NOTRUN reason=.. unit=U, no DETACHED-END, no wake line (mutant: NOTRUN branch off)
#   3 a heavy-job that DID run a CMD earlier in the scope and fails later: still DETACHED-END (the .started file)
#   4 a reused --log with an old PASS/END line: moved aside, the new log has none of it
export WAKE_SECRETS_DIR=$w/sec JOB_WAKE_TOOL=$w/no-wake-tool   # a wake attempt would print "WAKE FAIL: tool-missing" into the log: its absence proves no wake was tried
mkdir -p "$w/sec"; : >"$w/sec/hk.hook"
uend() { grep -c "^heavy-job: DETACHED-END rc=[0-9]* unit=$1\$" "$2" 2>/dev/null; }
unr() { grep -c "^heavy-job: DETACHED-NOTRUN reason=.* unit=$1\$" "$2" 2>/dev/null; }
unit_of() { sed -n 's/^heavy-job: detach unit=\([^ ]*\) .*/\1/p' "$1" | head -1; }
waitfor() { local f=$1 re=$2; for _ in $(seq 150); do grep -q -- "$re" "$f" 2>/dev/null && return 0; sleep 0.1; done; return 1; }

# queued_case BIN TAG: memory gate closed, explicit --wait 2 (the old expiry), BIN = heavy-job under test. Echoes "queued" if after 4 s the job is still queued, "gone" if it ended.
queued_case() {
	local bin=$1 tag=$2 u st
	cp "$w/mem.small" "$w/mem.$tag"
	out=$(HEAVY_JOB_MEMINFO=$w/mem.$tag HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$bin" detach --label "$tag" --log "$w/$tag.log" -- "$bin" run --class analysis --label "$tag" --wait 2 -- echo job-ran-$tag 2>&1 </dev/null); rc=$?
	u=$(unit_of "$w/$tag.log"); sleep 4
	if [ -n "$u" ] && [ "$(uend "$u" "$w/$tag.log")$(unr "$u" "$w/$tag.log")" = 00 ]; then st=queued; else st=gone; fi
	echo "$st $u"
}

r=$(queued_case "$here/tools/heavy-job" q1); st=${r% *}; u1=${r#* }
HEAVY_JOB_SYSTEMCTL=$w/dt/sctl hj status
ok "1: detached run with --wait 2 and a closed memory gate is still queued after 4 s (no end line of either kind); the --wait is ignored, said in the log" "$([ "$st" = queued ] && [ "$(has '--wait 2 ignored, waiting up to' "$(cat "$w/q1.log")")" = 1 ] && echo 1 || echo 0)"
ok "1: heavy-job status shows it: unit=U state=queued pos=0 (head, waiting for the memory gate)" "$(has "^heavy-job: local unit=$u1 state=queued pos=0\$" "$out")"
cp "$w/mem.big" "$w/mem.q1"   # the gate opens: the same job must now run, and END with rc=0 and its own unit
waitfor "$w/q1.log" "^heavy-job: DETACHED-END rc=0 unit=$u1\$"
ok "1: gate opened -> the job runs and ends DETACHED-END rc=0 unit=U (CMD output in the log, no NOTRUN)" "$([ "$(uend "$u1" "$w/q1.log")" = 1 ] && [ "$(unr "$u1" "$w/q1.log")" = 0 ] && [ "$(has 'job-ran-q1' "$(cat "$w/q1.log")")" = 1 ] && echo 1 || echo 0)"

holdl 7
out=$(HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$here/tools/heavy-job" detach --label q2 --log "$w/q2.log" -- "$here/tools/heavy-job" run --class analysis --label q2 --wait 2 -- echo job-ran-q2 2>&1 </dev/null); u2=$(unit_of "$w/q2.log")
sleep 4; HEAVY_JOB_SYSTEMCTL=$w/dt/sctl hj status
ok "1b: queued behind a ticketed holder for 4 s (> --wait 2): no end line, status: unit=U state=queued pos=1" "$([ "$(uend "$u2" "$w/q2.log")$(unr "$u2" "$w/q2.log")" = 00 ] && [ "$(has "^heavy-job: local unit=$u2 state=queued pos=1\$" "$out")" = 1 ] && echo 1 || echo 0)"
printf 'pixos-hj-q2x-1-1.scope loaded active running x\npixos-hj-act-9-9.scope loaded active running y\n' | sed "s/pixos-hj-q2x-1-1/$u2/" >"$w/dt/units"; HEAVY_JOB_SYSTEMCTL=$w/dt/sctl hj status; rm -f "$w/dt/units"
ok "1b: status lists an active unit without a ticket as state=active pos=-, and a ticketed unit once (not again as active)" "$([ "$(has '^heavy-job: unit=pixos-hj-act-9-9 state=active pos=-$' "$out")" = 1 ] && [ "$(grep -c "unit=$u2 " <<<"$out")" = 1 ] && echo 1 || echo 0)"
waitl lh; waitfor "$w/q2.log" "^heavy-job: DETACHED-END rc=0 unit=$u2\$"
ok "1b: holder done -> the queued job ran: DETACHED-END rc=0, queue empty" "$([ "$(uend "$u2" "$w/q2.log")" = 1 ] && [ "$(has 'job-ran-q2' "$(cat "$w/q2.log")")" = 1 ] && [ "$(lq)" = 0 ] && echo 1 || echo 0)"

# ---- 2: never ran
touch "$w/dt/inactive"   # the scope is not seen active: the end/notrun line decides the launch check (as in the rc3 case of the remote part)
dhj --label n1 --log "$w/n1.log" --wake-hook hk -- "$here/tools/heavy-job" run --class bogus --label n1 -- echo should-not-run; un1=$(unit_of "$w/n1.log"); rm -f "$w/dt/inactive"
ok "2: bad --class inside the scope: launch check says RESULT: FAIL ... CMD never ran, exit 1" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL detach unit=.*: CMD never ran: .*usage: --class' "$out")" = 1 ] && echo 1 || echo 0)"
ok "2: log ends with DETACHED-NOTRUN reason=.. unit=U, has NO DETACHED-END and NO WAKE line (a not-run is not a job end)" "$([ "$(unr "$un1" "$w/n1.log")" = 1 ] && [ "$(uend "$un1" "$w/n1.log")" = 0 ] && [ "$(grep -c '^WAKE' "$w/n1.log")" = 0 ] && [ "$(has 'should-not-run' "$(grep -v '^heavy-job: detach unit' "$w/n1.log")")" = 0 ] && echo 1 || echo 0)"
ok "2: the notrun/started scratch files are removed" "$([ -z "$(ls "$w"/n1.log.notrun-* 2>/dev/null)" ] && echo 1 || echo 0)"
# a signal while queued behind a holder: heavy-job TERMed before it started a CMD
holdl 6
dhj --label n2 --log "$w/n2.log" --wake-hook hk -- bash -c "$here/tools/heavy-job run --class analysis --label n2 --wait 60 -- echo should-not-run & p=\$!; sleep 1.5; kill -TERM \$p; wait \$p"; un2=$(unit_of "$w/n2.log")
waitfor "$w/n2.log" "DETACHED-" ; waitl lh
ok "2: TERM while queued -> DETACHED-NOTRUN reason=aborted by SIGTERM before CMD started (not DETACHED-END rc=143), no WAKE" "$([ "$(unr "$un2" "$w/n2.log")" = 1 ] && [ "$(has 'reason=.*aborted by SIGTERM before CMD started' "$(cat "$w/n2.log")")" = 1 ] && [ "$(uend "$un2" "$w/n2.log")" = 0 ] && [ "$(grep -c '^WAKE' "$w/n2.log")" = 0 ] && echo 1 || echo 0)"
# the same unit's wake still fires for a real end (control)
dhj --label n3 --log "$w/n3.log" --wake-hook hk -- bash -c 'exit 0'; un3=$(unit_of "$w/n3.log"); waitfor "$w/n3.log" '^WAKE'
ok "2 control: a real end DOES print the WAKE line after DETACHED-END" "$([ "$(uend "$un3" "$w/n3.log")" = 1 ] && [ "$(grep -c '^WAKE' "$w/n3.log")" = 1 ] && echo 1 || echo 0)"

# ---- 3: a CMD ran, a later heavy-job in the same scope fails before its CMD: a real end
dhj --label r1 --log "$w/r1.log" --wake-hook hk -- bash -c "$here/tools/heavy-job run --class analysis --label r1a --wait 5 -- echo first-ran; $here/tools/heavy-job run --class bogus --label r1b -- true"; ur1=$(unit_of "$w/r1.log")
waitfor "$w/r1.log" '^WAKE'
ok "3: CMD ran earlier in the scope, a later usage error (rc 2) -> DETACHED-END rc=2, never NOTRUN" "$([ "$(has "^heavy-job: DETACHED-END rc=2 unit=$ur1\$" "$(cat "$w/r1.log")")" = 1 ] && [ "$(unr "$ur1" "$w/r1.log")" = 0 ] && echo 1 || echo 0)"

# ---- 4: reused log
printf 'RESULT: PASS gate old-run\nheavy-job: DETACHED-END rc=0 unit=pixos-hj-old-1-1\nGATE PASS oldsha\n' >"$w/s.log"
dhj --label s1 --log "$w/s.log" -- bash -c 'echo new-run; exit 3'; us=$(unit_of "$w/s.log")
ok "4: --log with an old PASS/END/GATE PASS: moved to s.log.prev-*, the new log has none of those lines, only its own unit's end (rc=3)" "$([ -n "$us" ] && [ "$(grep -c 'old-run\|oldsha\|unit=pixos-hj-old' "$w/s.log")" = 0 ] && [ "$(cat "$w"/s.log.prev-* | grep -c 'old-run')" = 1 ] && [ "$(has "^heavy-job: DETACHED-END rc=3 unit=$us\$" "$(cat "$w/s.log")")" = 1 ] && echo 1 || echo 0)"

# ---- mutants: the checks above must fail against a heavy-job with the fix removed
mkdir -p "$w/mut/tools" && cp "$here/tools/job-wake" "$w/mut/tools/" 2>/dev/null
mut() {   # NAME SED-EXPR: a copy of tools/heavy-job with one edit; returns 0 when the copy differs from the original
	sed "$2" "$here/tools/heavy-job" >"$w/mut/tools/hj-$1"; chmod +x "$w/mut/tools/hj-$1"; ! cmp -s "$here/tools/heavy-job" "$w/mut/tools/hj-$1"
}
mut old1800 's/^\twait=\$cap$/\t:/' || echo "mutant old1800 did not change the file"
export HEAVY_JOB_CANONICAL=$w/mut/tools/hj-old1800   # T-d543: a mutant copy is its own canonical copy (else it would re-exec the real tool and the mutation never runs)
r=$(queued_case "$w/mut/tools/hj-old1800" m1); mst=${r% *}
ok "M1 mutant (detached wait=\$cap removed = the old --wait expiry): the job is NOT queued after 4 s -> case 1 catches it" "$([ "$mst" = gone ] && echo 1 || echo 0)"
mut nonotrun 's/\[ "\$rc" != 0 \] \&\& \[ -s "\$nr" \]/[ "$rc" = 99 ] \&\& [ -s "$nr" ]/' || echo "mutant nonotrun did not change the file"
touch "$w/dt/inactive"; export HEAVY_JOB_CANONICAL=$w/mut/tools/hj-nonotrun
out=$(HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$w/mut/tools/hj-nonotrun" detach --label m2 --log "$w/m2.log" --wake-hook hk -- "$w/mut/tools/hj-nonotrun" run --class bogus --label m2 -- true 2>&1 </dev/null); um=$(unit_of "$w/m2.log"); rm -f "$w/dt/inactive"
ok "M2 mutant (NOTRUN branch off): the not-run ends as DETACHED-END rc=2 -> case 2 catches it" "$([ "$(unr "$um" "$w/m2.log")" = 0 ] && [ "$(uend "$um" "$w/m2.log")" = 1 ] && echo 1 || echo 0)"
mut nofresh 's/^\tif \[ -s "\$log" \]; then mv -f.*$/\t:/' || echo "mutant nofresh did not change the file"
printf 'heavy-job: DETACHED-END rc=0 unit=pixos-hj-old-1-1\n' >"$w/m3.log"; export HEAVY_JOB_CANONICAL=$w/mut/tools/hj-nofresh
out=$(HEAVY_JOB_SYSTEMD_RUN=$w/dt/sdrun HEAVY_JOB_SYSTEMCTL=$w/dt/sctl "$w/mut/tools/hj-nofresh" detach --label m3 --log "$w/m3.log" -- bash -c 'exit 3' 2>&1 </dev/null)
export HEAVY_JOB_CANONICAL=$here/tools/heavy-job
ok "M3 mutant (no log rotation): the old END line stays in the log -> case 4 catches it" "$([ "$(grep -c 'unit=pixos-hj-old' "$w/m3.log")" = 1 ] && echo 1 || echo 0)"

# ---- T-c68c: class gate + --remote-cmd is offered to the remote build host FIRST (fake ssh answers, no network)
fs=$w/fs; mkdir -p "$fs"
cat >"$w/fssh" <<'F'
#!/bin/bash
cat >/dev/null
[ ! -e "$FS/down" ] || { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
cat "$FS/answer"
F
chmod +x "$w/fssh"
export FS=$fs REMOTE_JOB_SSH=$w/fssh REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_DIR=$w/q OFFLOAD_REPROBE=1 OFFLOAD_MAXLOAD1=8 OFFLOAD_MINAVAIL_GB=12
# ---- T-c68c: class gate with --remote-cmd is offered to the remote host FIRST (ROUTE remote | ROUTE local: remote host <reason>); local load 20 must not matter
rm -f "$fs/down"; echo "20.00 9.0 9.0 1/1 1" >"$w/load"; rm -f "$w/local-ran" "$w/remote-ran"
ans 0.5 33554432
hj run --class gate --label rt-remote --wait 20 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran"
ok "T-c68c: local load1 20, the remote host admits -> ROUTE remote line, job ran remote, not local" "$([ $rc = 0 ] && [ "$(has '^heavy-job: ROUTE remote label=rt-remote' "$out")" = 1 ] && [ -e "$w/remote-ran" ] && [ ! -e "$w/local-ran" ] && echo 1 || echo 0)"
rm -f "$w/local-ran" "$w/remote-ran"; : >"$fs/down"; echo "0.25 0.1 0.1 1/1 1" >"$w/load"
hj run --class gate --label rt-down --wait 20 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran"
ok "T-c68c: the remote host unreachable -> one ROUTE local: remote host unreachable line, job runs locally" "$([ $rc = 0 ] && [ "$(has '^heavy-job: ROUTE local: remote host unreachable' "$out")" = 1 ] && [ -e "$w/local-ran" ] && [ ! -e "$w/remote-ran" ] && echo 1 || echo 0)"
rm -f "$w/local-ran" "$w/remote-ran" "$fs/down"; ans 9.5 33554432
hj run --class gate --label rt-busy --wait 20 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran"
ok "T-c68c: the remote host busy (load1 9.5) -> ROUTE local: remote host refused: load1 9.5 >= 8, job runs locally, run.log carries the line" "$([ $rc = 0 ] && [ "$(has '^heavy-job: ROUTE local: remote host refused: load1 9.5 >= 8' "$out")" = 1 ] && [ -e "$w/local-ran" ] && [ ! -e "$w/remote-ran" ] && grep -q 'ROUTE local: remote host refused' "$w/cwd/logs/heavy-job/rt-busy/run.log" && echo 1 || echo 0)"
rm -f "$w/local-ran"; ans 0.5 33554432
hj run --class build --label rt-build --wait 20 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran"
ok "T-c68c: non-gate class keeps local behaviour, loud ROUTE local line" "$([ $rc = 0 ] && [ "$(has 'ROUTE local: --remote-cmd is for class gate only' "$out")" = 1 ] && [ -e "$w/local-ran" ] && [ ! -e "$w/remote-ran" ] && echo 1 || echo 0)"
# ---- 2026-11-01: a gate queued locally (remote busy at submit) is re-offered to the remote host while it waits; it leaves the local FIFO when the remote frees
rm -f "$w/local-ran" "$w/remote-ran" "$w/hold-go"; echo "0.25 0.1 0.1 1/1 1" >"$w/load"; ans 9.5 33554432
"$here/tools/heavy-job" run --class build --label rt-hold --wait 30 -- bash -c "until [ -e $w/hold-go ]; do sleep 0.2; done" >"$w/hold.out" 2>&1 & hp=$!
for _ in $(seq 50); do grep -q 'lock acquired label=rt-hold' "$w/hold.out" 2>/dev/null && break; sleep 0.1; done
( sleep 2; ans 0.5 33554432 ) &
out=$(HEAVY_JOB_REMOTE_REPROBE=1 "$here/tools/heavy-job" run --class gate --label rt-late --wait 30 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran" 2>&1); rc=$?
: >"$w/hold-go"; wait "$hp"
ok "late route: queued locally behind a holder, remote frees -> ROUTE remote (late), ran remote, not local" "$([ $rc = 0 ] && [ "$(has '^heavy-job: ROUTE local: remote host refused: load1 9.5' "$out")" = 1 ] && [ "$(has '^heavy-job: ROUTE remote (late) label=rt-late' "$out")" = 1 ] && [ -e "$w/remote-ran" ] && [ ! -e "$w/local-ran" ] && echo 1 || echo 0)"
rm -f "$w/local-ran" "$w/remote-ran" "$w/hold-go"; ans 9.5 33554432
"$here/tools/heavy-job" run --class build --label rt-hold2 --wait 30 -- bash -c "until [ -e $w/hold-go ]; do sleep 0.2; done" >"$w/hold.out" 2>&1 & hp=$!
for _ in $(seq 50); do grep -q 'lock acquired label=rt-hold2' "$w/hold.out" 2>/dev/null && break; sleep 0.1; done
( sleep 3; : >"$w/hold-go" ) &
out=$(HEAVY_JOB_REMOTE_REPROBE=1 "$here/tools/heavy-job" run --class gate --label rt-stay --wait 30 --remote-cmd "echo remote-ran >$w/remote-ran" -- bash -c "echo local-ran >$w/local-ran" 2>&1); rc=$?
wait "$hp"
ok "late route: remote stays busy -> keeps its local turn and runs locally" "$([ $rc = 0 ] && [ "$(has 'ROUTE remote (late)' "$out")" = 0 ] && [ -e "$w/local-ran" ] && [ ! -e "$w/remote-ran" ] && echo 1 || echo 0)"

# T-9954: inside a detached scope a nested CMD running the offload-queue subcommand with --wait 2 against a never-admitting fake host honours its --wait (mark_started unsets HEAVY_JOB_DETACHED); mutant (unset removed) -> the wait is ignored, timeout 30 fires
oq="sm""uks"; nf=$w/nfs; mkdir -p "$nf"
printf '#!/bin/bash\ncat >/dev/null\nprintf "load=9.9\\nmemkb=33554432\\nrj=\\nforeign=\\n"\n' >"$w/nfssh"; chmod +x "$w/nfssh"
out=$(env -u OFFLOAD_QUEUE_HELD -u HEAVY_JOB_HELD REMOTE_JOB_SSH=$w/nfssh OFFLOAD_QUEUE_DIR=$w/nq OFFLOAD_REPROBE=1 HEAVY_JOB_DETACHED=pixos-hj-t9954 timeout 30 "$here/tools/heavy-job" run --class analysis --label d9954 --wait 30 -- bash -c "'$here/tools/heavy-job' $oq run --owner alice --label n9954 --wait 2 -- echo should-not-run" 2>&1); rc=$?
ok "T-9954: nested offload-queue run in a detached scope honours --wait 2: RESULT: ERROR within 30 s (rc=$rc), not wedged" "$([ $rc != 124 ] && [ "$(has '^RESULT: ERROR' "$out")" = 1 ] && ! grep -q 'wait 2 ignored' <<<"$out" && echo 1 || echo 0)"
unset FS REMOTE_JOB_SSH REMOTE_JOB_HOST OFFLOAD_QUEUE_DIR OFFLOAD_REPROBE OFFLOAD_MAXLOAD1 OFFLOAD_MINAVAIL_GB

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
