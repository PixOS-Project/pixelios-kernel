#!/bin/bash
# One verification gate: build both bluejay variants, host tests, boot-image safety check, unit tests, repo validation.
# Run from any checkout; full output goes to shim/out/gate.log (with a per-step timing table), stdout is ONE line:
#   "GATE PASS|FAIL <short-hash> [failed step]"                       (full gate: what must pass before a push)
#   "GATE PASS|FAIL <short-hash> quick: ran N of M steps | <failed step>"   (--quick: only the steps the diff needs)
# --quick diffs against the commit in shim/out/gate.stamp (written by the last clean full PASS); no usable stamp = full gate.
# Two lanes run in parallel: A = shim builds + host-tests + boot-safety, B = unit, rootless, validate. Lane A runs `build` first (cold: warms the audit
# memo and the host-test stamp), then the other build steps at most GATE_JOBS / -j N (default 8) at a time. Every build writes its own dir shim/out/gate/<step>-<i>/
# (build.sh --out), so the gate never touches shim/out/*.img and takes no .build.lock (only shim/out/.gate.lock: one gate per worktree).
# Live state: shim/out/gate.progress (START/END per step), gate.result (RUNNING <pid> <epoch>, then the GATE line + epoch; atomic), shim/out/gate/<step>.log.
# A failing step stops the rest at their next step boundary (--keep-going: only its own lane). Per-step timeout: GATE_STEP_TIMEOUT s (default 900 build*, 300 others). Exit 0 PASS, 1 FAIL, 2 usage.
# --no-memo (or PIXELIOS_AUDIT_NO_MEMO=1) disables the audit self-test memo (tools/audit_memo.py) for this run.
# Reviewers/leads: trust a PASS line whose hash equals `git rev-parse --short HEAD` instead of re-running.
cd "$(dirname "$0")/.." || exit 2
git rev-parse --short HEAD >/dev/null 2>&1 || { echo "GATE ERROR not a git checkout"; exit 2; }
quick=; keep=
while [ $# -gt 0 ]; do case $1 in --quick) quick=1 ;; --keep-going) keep=1 ;; --no-memo) export PIXELIOS_AUDIT_NO_MEMO=1 ;; -j) shift; GATE_JOBS=${1:-x} ;;
	*) echo "GATE ERROR usage: check-all.sh [--quick] [--keep-going] [--no-memo] [-j N]"; exit 2 ;; esac; shift; done
case ${GATE_STEP_TIMEOUT:-1} in *[!0-9]*) echo "GATE ERROR GATE_STEP_TIMEOUT must be positive seconds"; exit 2 ;; esac
[ $((10#${GATE_STEP_TIMEOUT:-1})) -gt 0 ] || { echo "GATE ERROR GATE_STEP_TIMEOUT must be positive seconds (0 disables timeout(1))"; exit 2; }
case ${GATE_JOBS:-1} in ''|*[!0-9]*|0) echo "GATE ERROR GATE_JOBS must be a positive integer"; exit 2 ;; esac
J=${GATE_JOBS:-8}
mkdir -p shim/out
log=shim/out/gate.log; stamp=shim/out/gate.stamp; prog=shim/out/gate.progress; res=shim/out/gate.result; owner=shim/out/.gate.owner
# one gate per worktree (it owns shim/out/gate, gate.log, gate.result). Not .build.lock: xrun/probe-run builds into shim/out and the gate no longer does.
# tools-sync also refuses a pull while this lock is held (the gate would see mixed tool versions).
# gate.result: temp + rename, so a watcher never reads a half line. WAITING (for the lock), RUNNING while the gate runs, then the GATE line + epoch.
putres() { printf '%s\n' "$1" >"$res.$$" && mv -f "$res.$$" "$res"; }
finish() { putres "$1 $(date +%s)"; echo "$1"; exit "$2"; }
exec 9>shim/out/.gate.lock
if ! flock -n 9; then
	w="waiting for the gate lock held by $(cat "$owner" 2>/dev/null || echo unknown)"; echo "$w" >&2; echo "$w" >>"$prog"
	# WAITING replaces the previous result (a stale PASS must not stay readable); flock runs in the background so a signal is handled at once
	putres "WAITING $$ $(date +%s)"
	trap 'kill "$fp" 2>/dev/null; finish "GATE FAIL interrupted while waiting for the gate lock" 1' INT TERM HUP
	flock -w 3600 9 & fp=$!
	wait "$fp" || finish "GATE FAIL lock-timeout (another gate in this worktree holds shim/out/.gate.lock for 3600 s)" 1
	trap - INT TERM HUP
fi
echo "$$ ${USER:-?} check-all $(date +%s)" >"$owner"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"; rm -f "$owner"' EXIT
: > "$log"; : > "$prog"; rm -rf shim/out/gate; mkdir -p shim/out/gate
full=$(git rev-parse HEAD)
h=$(git rev-parse --short HEAD)$([ -n "$(git status --porcelain)" ] && echo "+dirty")
# Ctrl-C / kill: steps run under `timeout`, i.e. in their own process groups, so kill them (and the lane jobs) explicitly. Lanes also
# stop at their next step boundary when this shell is gone (step()). The steps' pid files are $tmp/NAME.pid (the timeout pid = its pgid).
kill_steps() { for f in "$tmp"/*.pid; do [ -e "$f" ] || continue; p=$(cat "$f" 2>/dev/null); [ -n "$p" ] && kill -KILL -- "-$p" "$p" 2>/dev/null; done; return 0; }
onint() {
	trap '' INT TERM HUP; : >"$tmp/interrupted"
	[ -n "${pa:-}" ] && kill "$pa" 2>/dev/null
	kill_steps; sleep 1; kill_steps   # the 2nd sweep catches a step that was starting during the first
	finish "GATE FAIL $h interrupted" 1
}
trap onint INT TERM HUP
putres "RUNNING $$ $(date +%s)"
ALL="build build-fast build-usb build-wlan build-gpu build-gpupower build-gpuclk build-gpurails build-gpurailon build-gpujob build-touch build-udump host-tests boot-safety unit phone-state hang-recover probe-run rootless public-split validate shellcheck"
want=$ALL; fullnote=
if [ -n "$quick" ]; then
	want=$(python3 tools/gate_select.py "$stamp") || finish "GATE FAIL $h gate_select (see $log)" 1
	[ "$want" = FULL ] && { want=$ALL; quick=; fullnote=" quick: no usable stamp, ran full"; }
fi
# step LANE NAME CMD...: skipped unless selected; a failure marks the lane and skips its remaining steps (and, unless --keep-going,
# everyone's: they stop at their next step boundary). Each step runs under timeout -k 10 (build-wlan 1500 s, other build* 900 s, others 300 s, env GATE_STEP_TIMEOUT)
# and logs to shim/out/gate/NAME.log; steps of one lane may run in parallel (par), so every step writes only its own files.
step() {
	l=$1; n=$2; shift 2
	case " $want " in *" $n "*) ;; *) return 0 ;; esac
	# the gate was interrupted or is gone: never start another step (exits the lane subshell)
	{ [ -d "$tmp" ] && [ ! -e "$tmp/interrupted" ] && kill -0 $$ 2>/dev/null; } || exit 1
	if [ -n "$keep" ]; then [ -e "$tmp/$l.lane" ] && return 0; else compgen -G "$tmp/*.fail" >/dev/null && return 0; fi
	case $n in build-wlan) b=1500 ;; build*) b=900 ;; *) b=300 ;; esac; b=${GATE_STEP_TIMEOUT:-$b}
	echo "START $n $(date +%s)" >>"$prog"; s=$SECONDS
	# 9>&- below: a step's children (an adb server, a stub sleep) must not keep the gate lock after the gate ends
	# bash -c re-dispatches so that the exported shell function `needs` can run under timeout
	timeout -k 10 "$b" bash -c '"$@"' _ "$@" >"shim/out/gate/$n.log" 2>&1 9>&- &
	tp=$!; echo "$tp" >"$tmp/$n.pid"; wait "$tp"; rc=$?; rm -f "$tmp/$n.pid"
	[ -d "$tmp" ] && [ ! -e "$tmp/interrupted" ] || exit 1
	if [ $rc = 0 ]; then echo "$n $((SECONDS-s))s" >"$tmp/$n.time"; echo "END $n PASS $((SECONDS-s))" >>"$prog"
	else
		r=; { [ $rc = 124 ] || [ $rc = 137 ]; } && r=" (timeout)"
		echo "$n$r" >"$tmp/$n.fail"; : >"$tmp/$l.lane"; echo "$n FAILED$r $((SECONDS-s))s" >"$tmp/$n.time"; echo "END $n FAIL$r $((SECONDS-s))" >>"$prog"
	fi
}
# par CMD...: run CMD in the background, at most $J of this lane's jobs at a time
par() { while [ "$(jobs -rp | wc -l)" -ge "$J" ]; do wait -n; done; "$@" & }
# needs FILE CMD...: run CMD only if FILE exists in this repository (the public repos hold different parts of the monorepo)
needs() { f=$1; shift; [ -e "$f" ] || { echo "skip: $f is not in this repository"; return 0; }; "$@"; }
export -f needs
# build.sh --out is relative to shim/. Steps with several build.sh calls run them one after the other (ponytail: build-wlan is the longest, 5 calls; split it if it becomes the critical path).
# The first build is serial: it creates shim/out/bluejay-testkey.pem and warms the audit memo + host-test stamp (PIXELIOS_HOST_TESTS=always: the gate's own build always runs the host tests).
laneA() {
	step A build        bash -c 'cd shim && PIXELIOS_HOST_TESTS=always ./build.sh --board bluejay --avb --out out/gate/build-0'
	par step A build-fast   bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast --out out/gate/build-fast-0'
	par step A build-usb    bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --out out/gate/build-usb-0'
	par step A build-wlan   bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --wlan-probe" && $B --out out/gate/build-wlan-0 && $B --wlan-rails --wlan-en --out out/gate/build-wlan-1 && $B --wlan-smc-read --out out/gate/build-wlan-2 && $B --wlan-iso --out out/gate/build-wlan-3 && { o=$($B --wlan-iso --wlan-en --out out/gate/build-wlan-4 2>&1) && exit 1; echo "$o"; echo "$o" | grep -q -- "--wlan-iso cannot be combined with --wlan-en"; }'
	par step A build-gpu    bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --out out/gate/build-gpu-0'
	par step A build-gpupower bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --gpu-probe" && $B --gpu-power --out out/gate/build-gpupower-0 && $B --gpu-power=stack --out out/gate/build-gpupower-1'
	par step A build-gpuclk bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-clk --gpu-power --out out/gate/build-gpuclk-0'
	par step A build-gpurails bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails --out out/gate/build-gpurails-0'
	par step A build-gpurailon bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails --gpu-rail-on --gpu-power --out out/gate/build-gpurailon-0'
	par step A build-gpujob bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --gpu-probe" && $B --gpu-s2mpu --out out/gate/build-gpujob-0 && $B --gpu-job --out out/gate/build-gpujob-1'
	par step A build-touch  bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --touch-probe=run,mux,cs,osc,loop,rst,misopu,rails --out out/gate/build-touch-0'
	par step A build-udump  bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast --udump 1 --out out/gate/build-udump-0'
	wait
	step A host-tests   bash -c 'cd shim && d=out/gate/build-0 && { [ -s $d/host-tests.list ] || { echo "no $d/host-tests.list (build.sh did not run the host tests)"; exit 1; }; } && while read -r n; do [ -x $d/$n ] || { echo "MISSING host test binary $n"; exit 1; }; $d/$n </dev/null || { echo "FAIL $n"; exit 1; }; done <$d/host-tests.list'
	step A boot-safety  python3 tools/test-bluejay-boot.py --out shim/out/gate/build-0 shim/out/gate/build-0/shim-boot-avb.img
}
laneB() {
	step B validate     python3 tools/validate-repo.py
	step B shellcheck   bash -c 'command -v shellcheck >/dev/null || { echo "shellcheck is not installed (apt install shellcheck)"; exit 1; }; shellcheck -S warning $(tools/shell-files)'
	step B public-split needs tools/make-public-tree.py python3 tools/make-public-tree.py --check
	step B unit         python3 -m unittest discover -s tools -p "test_*.py"
	step B phone-state  tools/phone-state --self-test
	step B hang-recover  tools/hang-recover --self-test
	step B probe-run    tools/probe-run --self-test
	step B rootless     needs tools/build-ios-root-rootless.sh bash -c 'bash -n tools/build-ios-root-rootless.sh && bash -n tools/rootless/vm.sh && bash -n tools/rootless/compare-images.sh && { [ ! -s "${PIXELIOS_VM:-$HOME/pixelios-artifacts/vm}/vmlinuz" ] || ! command -v fakeroot >/dev/null || tools/build-ios-root-rootless.sh --selftest; }'
}
laneA & pa=$!; laneB; wait
# deterministic log: steps in ALL order, then timings
for n in $ALL; do [ -s "shim/out/gate/$n.log" ] && { echo "== $n"; cat "shim/out/gate/$n.log"; }; done >>"$log"
{ echo "== timings (s)"; for n in $ALL; do cat "$tmp/$n.time" 2>/dev/null; done; } >>"$log"
fail=; for n in $ALL; do [ -e "$tmp/$n.fail" ] && { fail=$(cat "$tmp/$n.fail"); break; }; done
total=$(echo $ALL | wc -w); ran=$(echo $want | wc -w)
if [ -n "$quick" ]; then tag=" quick: ran $ran of $total steps"; [ -n "$fail" ] && tag=" quick: $fail"; else tag=$fullnote; [ -n "$fail" ] && tag="$tag${fullnote:+:} $fail"; fi
[ -n "$fail" ] && finish "GATE FAIL $h$tag (see $log)" 1
# stamp only on a clean-tree full PASS (+dirty also covers untracked files): the next --quick diffs against exactly this commit
# HEAD moving or the tree changing during the run (a commit, an edit) means the PASS covers a state we did not keep: no stamp, +dirty
if [ "$(git rev-parse HEAD)" != "$full" ] || [ -n "$(git status --porcelain)" ]; then h=${h%+dirty}+dirty; fi
[ -z "$quick" ] && [ "${h%+dirty}" = "$h" ] && echo "$full" >"$stamp"
finish "GATE PASS $h$tag" 0
