#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, fs, ...) are set by the sourced test_heavy_job_lib.sh
# Part 'route' of the tools/heavy-job host test: per-use offload switches and the shared load of light host tests (tools/heavy-job-offload.sh, user order 2026-11-03).
# Fake ssh (REMOTE_JOB_SSH) for the probe and a STUB tools/remote-job that runs the `test-run` command locally and writes the job log/rc the real tool writes; the phone-word scan is the REAL remote-job --scan-only.
# Cases: `run --light -- python3 tools/test_*` routed to the offload host when it admits (ROUTE remote, output copied back, where=offload-host, rc kept); the master row and the `:test` row switch it off with ONE
#   `OFFLOAD OFF: use=test` line and a local run, the `:gate` row does not; dirty tree / untracked script / argument outside the checkout / phone word / busy FIFO / refused / unreachable / no host = ONE ROUTE local line;
#   a remote failure, an infra failure or a skip in a passing run is never trusted (re-run here, gaps=1); `bash -c 'cd CHECKOUT && TEXT'` is offered; a quick gate that would run FULL goes remote (a real FAIL is the verdict, infra or a refused import runs here), a subset stays local; the gate ROUTE honours the `:gate` row; `offload-host status` lists the uses.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; echo "${out:-}" | tail -n 30 | sed 's/^/     | /'; bad=$((bad + 1)); fi; }   # a failure shows the tail of the last command output

k=$w/k   # a throw-away tool+repo tree: the real heavy-job and its modules, a stub remote-job
mkdir -p "$k/tools" && cp "$here"/tools/heavy-job "$here"/tools/heavy-job-*.sh "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$k/tools/" || exit 2
cat >"$k/tools/remote-job" <<'S'
#!/bin/bash
# stub: --scan-only = the REAL phone-word scan; test-run -- CMD = run CMD in REMOTE_JOB_REPO like the container would, write job.log/job.rc under logs/remote-jobs/test-run
echo "$*" >>"$STUB_LOG"
if [ "$1" = --scan-only ]; then exec "$REAL_RJ" "$@"; fi
if [ "$1 $2 $3" = "--where auto gate-pre" ]; then
	d=$REMOTE_JOB_REPO/logs/remote-jobs/gate-pre/out; mkdir -p "$d"
	case ${STUB_GATE:-pass} in
		pass) echo "GATE PASS abc quick: no usable stamp, ran full" >"$d/gate.log"; echo "RESULT: PASS remote-job gate-pre"; exit 0 ;;
		fail) echo "GATE FAIL abc unit failed" >"$d/gate.log"; echo "RESULT: FAIL remote-job job rc 1"; exit 1 ;;
		*) echo "RESULT: ERROR remote-job: ssh lost"; exit 2 ;;
	esac
fi
[ "$1 $2 $3" = "--where offload-host test-run" ] || { echo "RESULT: ERROR remote-job stub: unexpected args $*"; exit 2; }
shift 4
d=$REMOTE_JOB_REPO/logs/remote-jobs/test-run; mkdir -p "$d"
case ${STUB_MODE:-ok} in
	infra) echo "RESULT: ERROR remote-job: offload-host run failed (ssh run failed), no fallback"; exit 2 ;;
	lack) echo "bash: python3: command not found" >"$d/job.log"; echo 127 >"$d/job.rc"; echo "RESULT: FAIL remote-job job rc 127"; exit 1 ;;
	*) ;;
esac
(cd "$REMOTE_JOB_REPO" && "$@") >"$d/job.log" 2>&1; r=$?; echo $r >"$d/job.rc"
if [ $r = 0 ]; then echo "RESULT: PASS remote-job test-run where=offload-host"; exit 0; fi
echo "RESULT: FAIL remote-job job rc $r"; exit 1
S
chmod +x "$k/tools/remote-job"
printf 'print("PASS-OUTPUT-OF-TEST")\n' >"$k/tools/test_pass.py"; printf 'import sys\nprint("FAIL-OUTPUT-OF-TEST")\nsys.exit(3)\n' >"$k/tools/test_fail.py"
printf '#!/bin/bash\necho "check-all ran: $*"\n' >"$k/tools/check-all.sh"; printf '#!/bin/bash\necho "gate ran: $*"\n' >"$k/tools/gate"; chmod +x "$k/tools/check-all.sh" "$k/tools/gate"
cp "$here/tools/gate-route-remote" "$k/tools/"
printf 'import os\nprint(os.environ.get("STUB_SELECT") or "FULL")\n' >"$k/tools/gate_select.py"
cat >"$k/tools/gate-import-remote" <<'S'
#!/bin/bash
echo "import $*" >>"$STUB_LOG"
[ "${STUB_IMPORT:-ok}" = ok ] || { echo "RESULT: FAIL gate-import-remote refused"; exit 1; }
mkdir -p "$2/shim/out"; echo "GATE PASS abc 1" >"$2/shim/out/gate.result"; echo "GATE PASS abc quick: no usable stamp, ran full"; echo "RESULT: PASS gate-import-remote"
S
chmod +x "$k/tools/gate-import-remote"
printf '/logs/\n/shim/\n' >"$k/.gitignore"
printf 'print("ran 3 tests: OK (skipped=1)")\n' >"$k/tools/test_skip.py"
G="git -C $k -c user.email=t@t -c user.name=t"
git -C "$k" init -q . && $G add -A && $G commit -qm init || exit 2
export HEAVY_JOB_CANONICAL=$k/tools/heavy-job STUB_LOG=$w/stub.log REAL_RJ=$here/tools/remote-job
fs=$w/fs; mkdir -p "$fs"; export FS=$fs
cat >"$w/fssh" <<'F'
#!/bin/bash
cat >/dev/null
[ ! -e "$FS/down" ] || { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
cat "$FS/answer"
F
chmod +x "$w/fssh"
export REMOTE_JOB_SSH=$w/fssh REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_DIR=$w/oq OFFLOAD_REPROBE=1 OFFLOAD_CONF=none
unset PIXOS_SUPER
cd "$k" || exit 2
rt() { : >"$STUB_LOG"; out=$("$k/tools/heavy-job" run --light --class mutant --label "${LBL:-rt}" --wait 20 -- "$@" 2>&1); rc=$?; }   # a light host test
cnt() { grep -c -- "$1" <<<"$out"; }
called() { grep -c -- '^--where offload-host test-run' "$STUB_LOG"; }   # how often the stub ran a test-run job
ans 0.5 33554432

rt python3 tools/test_pass.py
ok "routed: ROUTE remote line, the test output copied back, where=offload-host, rc 0, the stub ran test-run -- the SAME command" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote label=rt use=test')$(cnt '^PASS-OUTPUT-OF-TEST')$(cnt 'RESULT: PASS label=rt .*rc=0 .*where=offload-host')" = 111 ] && grep -q -- '--where offload-host test-run -- python3 tools/test_pass.py' "$STUB_LOG" && echo 1 || echo 0)"
ok "routed: run.log has the route line and the output; the route is in logs/offload-route.log (use test, remote)" "$([ "$(grep -c 'ROUTE remote' logs/heavy-job/rt/run.log)$(grep -c PASS-OUTPUT logs/heavy-job/rt/run.log)" = 11 ] && grep -q $'\ttest\tremote\t' logs/offload-route.log && echo 1 || echo 0)"
ok "routed: no local light ticket was taken, nothing left in the offload queue" "$([ -z "$(ls "$w"/hq-light/[0-9]*.* 2>/dev/null)" ] && [ -z "$(ls "$w"/oq/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"

LBL=rtf rt python3 tools/test_fail.py
ok "remote FAIL (rc 3) is re-run here: loud GAP line, local verdict (heavy-job exit 1, rc=3 on the RESULT line), RESULT FAIL with gaps=1, the failing output once (the local run)" "$([ $rc = 1 ] && [ "$(cnt 'OFFLOAD GAP: the host test failed on the offload host (rc 3')$(cnt 'RESULT: FAIL .*rc=3 .*gaps=1')$(cnt '^FAIL-OUTPUT-OF-TEST')" = 111 ] && echo 1 || echo 0)"
LBL=rtl STUB_MODE=lack rt python3 tools/test_pass.py
ok "container lacks something (remote rc 127): never a FAIL of the test - re-run here PASSES (rc 0), gaps=1, GAP line" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD GAP: the host test failed on the offload host (rc 127')$(cnt 'RESULT: PASS .*gaps=1')$(cnt '^PASS-OUTPUT-OF-TEST')" = 111 ] && echo 1 || echo 0)"
LBL=rti STUB_MODE=infra rt python3 tools/test_pass.py
ok "remote infra failure (remote-job exit 2): local run, rc 0, gaps=1, GAP line names the cause" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD GAP: the offload route failed (rc 2')$(cnt 'RESULT: PASS .*gaps=1')$(cnt '^PASS-OUTPUT-OF-TEST')" = 111 ] && echo 1 || echo 0)"

# ---- the switches
printf 'offload-host:test\tnoisy tests\n' >"$w/off.list"; LBL=sw1 rt python3 tools/test_pass.py
ok "use=test row: ONE 'OFFLOAD OFF: use=test' line with the reason, ran local, the stub was never asked, rc 0" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD OFF')$(cnt 'ROUTE local: OFFLOAD OFF: use=test switched off by order, running local: noisy tests')$(cnt '^PASS-OUTPUT-OF-TEST')" = 111 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
printf 'offload-host\tall off\n' >"$w/off.list"; LBL=sw2 rt python3 tools/test_pass.py
ok "master row: the same single line, local" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD OFF')$(cnt 'all off')" = 11 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
printf 'offload-host:gate\tgate busy\noffload-host:build\tb\n' >"$w/off.list"; LBL=sw3 rt python3 tools/test_pass.py
ok "the :gate and :build rows do not switch the test use off: routed" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote')$(cnt 'OFFLOAD OFF')" = 10 ] && [ "$(called)" = 1 ] && echo 1 || echo 0)"
rm -f "$w/off.list"

# ---- local with exactly one ROUTE line
dirt() { echo '# x' >>tools/test_pass.py; }
undirt() { git -C "$k" checkout -q tools/test_pass.py; }
dirt; LBL=d1 rt python3 tools/test_pass.py; undirt
ok "dirty tracked file: ONE 'ROUTE local: dirty tree' line, ran local" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: dirty tree')$(cnt 'ROUTE ')" = 11 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
echo y >tools/untracked_helper.py; LBL=d2 rt python3 tools/test_pass.py; rm -f tools/untracked_helper.py
ok "untracked file under tools/: local (the remote checkout would lack it)" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: dirty tree')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
printf 'print("U")\n' >tools/test_new.py; LBL=d3 rt python3 tools/test_new.py; rm -f tools/test_new.py
ok "an untracked test script is never offered (not in the committed HEAD): local, one ROUTE local line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
# shellcheck disable=SC2088  # the literal tilde is the point of this case: the string must be refused, not expanded
for a in /etc/passwd ../x.txt --out=/tmp/x '$'HOME '~'/x; do   # built in two pieces: test_no_fixed_home.py scans the text
	LBL=a1 rt python3 tools/test_pass.py "$a"
	ok "argument '$a' may write outside the checkout: local, one line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: CMD argument')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
done
for a in adb fastboot; do
	LBL=p1 rt python3 tools/test_pass.py "$a"
	ok "phone word '$a' in CMD: local, one line from the real remote-job --scan-only, the offload host never asked" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: remote-job refused the command')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
done
# a live ticket in the offload FIFO: busy, local at once (no waiting)
mkdir -p "$w/oq"; ( exec 3>"$w/oq/000001.other"; flock 3; sleep 25 ) & bp=$!; sleep 0.5
LBL=b1 rt python3 tools/test_pass.py
ok "offload FIFO busy (one job at a time stays): ONE ROUTE local line, ran local, did not wait" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: offload host busy')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
kill "$bp" 2>/dev/null; wait "$bp" 2>/dev/null; rm -f "$w"/oq/0*.other
ans 99 33554432; LBL=r1 rt python3 tools/test_pass.py
ok "offload host over the load limit: ROUTE local: refused (load1), local" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: offload host refused: load1 99')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
ans 0.5 1048576; LBL=r2 rt python3 tools/test_pass.py
ok "offload host short of memory: refused, local" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: offload host refused: MemAvailable')" = 1 ] && echo 1 || echo 0)"
ans 0.5 33554432; : >"$fs/down"; LBL=u1 rt python3 tools/test_pass.py; rm -f "$fs/down"
ok "offload host unreachable: ROUTE local: unreachable, local, rc 0" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: offload host unreachable: ssh failed')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
LBL=n1 rt python3 tools/test_pass.py; hold=$out
out=$(REMOTE_JOB_HOST='' "$k/tools/heavy-job" run --light --class mutant --label n1 --wait 20 -- python3 tools/test_pass.py 2>&1); rc=$?
ok "no offload host configured: one 'OFFLOAD GAP: no offload host configured' ROUTE local line, local, rc 0" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: OFFLOAD GAP: no offload host configured')" = 1 ] && echo 1 || echo 0)"
out=$hold
LBL=n2 rt sleep 0
ok "a light job that is not a host test (sleep): nothing offered, ONE ROUTE local line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: not a plain host test')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
LBL=n3 rt bash -c 'echo a-bash-c-is-not-offered'
ok "bash -c without 'cd CHECKOUT &&' is not offered: ONE ROUTE local line, local" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: not a plain host test')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"

# ---- quick gates: FULL ones go remote (same job as a formal gate), real subsets stay local
qg() { : >"$STUB_LOG"; out=$("$k/tools/heavy-job" run --class gate --label "$1" --wait 20 -- "${@:2}" 2>&1); rc=$?; }
export STUB_SELECT=FULL
qg qg1 tools/check-all.sh --quick
ok "quick gate that would run FULL: ROUTE remote, gate-pre + import ran, the GATE line is in the output, rc 0, not run here" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote label=qg1')" = 1 ] && [ "$(cnt '^GATE PASS abc quick: no usable stamp, ran full')" = 1 ] && [ "$(cnt 'check-all ran')" = 0 ] && grep -q -- '--where auto gate-pre' "$STUB_LOG" && grep -q '^import ' "$STUB_LOG" && echo 1 || echo 0)"
qg qg2 bash "$k/tools/gate" --quick --changed --run
ok "same through tools/gate --quick --changed --run, any path prefix" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote label=qg2')" = 1 ] && [ "$(cnt 'gate ran')" = 0 ] && echo 1 || echo 0)"
STUB_GATE=fail qg qg3 tools/check-all.sh --quick
ok "the gate ran and FAILed there: that is the verdict: rc 1, GATE FAIL in the output and in gate.result, not re-run here" "$([ $rc = 1 ] && [ "$(cnt '^GATE FAIL abc unit failed')" = 1 ] && [ "$(cnt 'check-all ran')" = 0 ] && grep -q '^GATE FAIL abc unit failed' "$k/shim/out/gate.result" && echo 1 || echo 0)"
STUB_GATE=infra qg qg4 tools/check-all.sh --quick
ok "infra failure: never a dependency: one OFFLOAD GAP line, the gate runs here, rc 0" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD GAP: remote-job gate-pre rc=2')" = 1 ] && [ "$(cnt 'check-all ran: --quick')" = 1 ] && echo 1 || echo 0)"
STUB_IMPORT=no qg qg5 tools/check-all.sh --quick
ok "a refused import: same, the gate runs here, rc 0" "$([ $rc = 0 ] && [ "$(cnt 'OFFLOAD GAP: gate-pre passed but its import was refused')" = 1 ] && [ "$(cnt 'check-all ran: --quick')" = 1 ] && echo 1 || echo 0)"
STUB_SELECT="unit lint" qg qg6 tools/check-all.sh --quick
ok "a quick gate that selects a subset: stays local, ONE line, the host never asked" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: quick gate selects only the steps')$(cnt 'check-all ran: --quick')" = 11 ] && [ ! -s "$STUB_LOG" ] && echo 1 || echo 0)"
GATE_NO_ROUTE=why qg qg7 tools/check-all.sh --quick
ok "GATE_NO_ROUTE keeps it local, said once" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: quick gate stays local (GATE_NO_ROUTE: why)')$(cnt 'check-all ran: --quick')" = 11 ] && echo 1 || echo 0)"
printf 'offload-host:gate\tgate off\n' >"$w/off.list"
qg qg8 tools/check-all.sh --quick
ok "the :gate row switches it off: one OFFLOAD OFF use=gate line, the gate runs here" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: remote host OFFLOAD OFF: use=gate')$(cnt 'check-all ran: --quick')" = 11 ] && echo 1 || echo 0)"
: >"$w/off.list"
echo '# dirty' >>"$k/tools/check-all.sh"
qg qg9 tools/check-all.sh --quick
ok "dirty tree: stays local, one line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: dirty tree')$(cnt 'check-all ran: --quick')" = 11 ] && [ ! -s "$STUB_LOG" ] && echo 1 || echo 0)"
git -C "$k" checkout -q -- tools/check-all.sh
qg qg10 tools/check-all.sh
ok "a full gate command gets no quick-gate line" "$([ $rc = 0 ] && [ "$(cnt 'quick gate')" = 0 ] && echo 1 || echo 0)"

# ---- a skip in a passing remote run is not trusted; `bash -c 'cd CHECKOUT && TEXT'` is offered
rt python3 tools/test_skip.py
ok "remote PASS whose output shows skipped=1: re-run here, gaps=1, GAP line names the skip" "$([ $rc = 0 ] && [ "$(cnt 'passed on the offload host but its output shows a skip')" = 1 ] && [ "$(called)" = 1 ] && [ "$(cnt 'gaps=1')" -ge 1 ] && echo 1 || echo 0)"
rt bash -c "cd $k && python3 tools/test_pass.py | tail -3"
ok "bash -c 'cd THIS && TEXT' with a test runner: routed, the remote command is TEXT without the cd" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote label=rt use=test')" = 1 ] && [ "$(called)" = 1 ] && grep -q -- 'test-run -- bash -c python3 tools/test_pass.py | tail -3' "$STUB_LOG" && echo 1 || echo 0)"
rt bash -c "cd /tmp && python3 $k/tools/test_pass.py"
ok "bash -c in another directory: local, one line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: bash -c runs in another directory')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
rt bash -c "cd $k && python3 tools/test_pass.py > /tmp/outside.txt"
ok "bash -c text with an absolute path: local, one line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: shell command could reach outside')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"
rt bash -c "cd $k && echo hello"
ok "bash -c text with no test runner: local, one line" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: shell command names no tools/test_')" = 1 ] && [ "$(called)" = 0 ] && echo 1 || echo 0)"

# ---- the gate ROUTE (--remote-cmd) honours the :gate row and logs its decision
printf 'offload-host:gate\tgate lane busy\n' >"$w/off.list"
out=$("$k/tools/heavy-job" run --class gate --label g1 --wait 20 --remote-cmd 'echo remote-cmd-ran' -- echo local-cmd-ran 2>&1); rc=$?
ok "gate ROUTE with the :gate row: ONE ROUTE local line carrying 'OFFLOAD OFF: use=gate', the local command ran" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE local: remote host OFFLOAD OFF: use=gate')$(cnt '^local-cmd-ran')$(cnt 'remote-cmd-ran')" = 110 ] && echo 1 || echo 0)"
printf 'offload-host:test\tt\n' >"$w/off.list"
out=$("$k/tools/heavy-job" run --class gate --label g2 --wait 20 --remote-cmd 'echo remote-cmd-ran' -- echo local-cmd-ran 2>&1); rc=$?
ok "gate ROUTE with only the :test row: still goes remote" "$([ $rc = 0 ] && [ "$(cnt 'ROUTE remote label=g2')$(cnt '^remote-cmd-ran')$(cnt '^local-cmd-ran')" = 110 ] && echo 1 || echo 0)"
ok "gate decisions are in logs/offload-route.log (use gate, local and remote)" "$(grep -q $'\tgate\tlocal\t' logs/offload-route.log && grep -q $'\tgate\tremote\t' logs/offload-route.log && echo 1 || echo 0)"

# ---- status lists the uses
printf 'offload-host:ci\tci busy\n' >"$w/off.list"
out=$("$k/tools/heavy-job" offload-host status 2>&1); rc=$?
ok "offload-host status: one line per use, the switched-off one says OFF with its reason" "$([ $rc = 0 ] && [ "$(cnt 'offload use build: on')$(cnt 'offload use gate: on')$(cnt 'offload use test: on')$(cnt 'offload use sync: on')$(cnt 'offload use ci: OFF (ci busy')" = 11111 ] && echo 1 || echo 0)"
rm -f "$w/off.list"

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
