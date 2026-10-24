#!/bin/bash
# One verification gate: build both bluejay variants, host tests, boot-image safety check, unit tests, repo validation.
# Run from any checkout; full output goes to shim/out/gate.log (with a per-step timing table), stdout is ONE line:
#   "GATE PASS|FAIL <short-hash> [failed step]"                       (full gate: what must pass before a push)
#   "GATE PASS|FAIL <short-hash> quick: ran N of M steps | <failed step>"   (--quick: only the steps the diff needs)
# --quick diffs against the commit in shim/out/gate.stamp (written by the last clean full PASS); no usable stamp = full gate.
# Two lanes run in parallel: A = shim builds + host-tests + boot-safety, B = unit, rootless, validate. Lane A runs `build` first (cold: warms the audit
# memo and the host-test stamp), then the other build steps at most GATE_JOBS (default 8) at a time. Every build writes its own dir shim/out/gate/<step>-<i>/
# (build.sh --out), so the gate never touches shim/out/*.img and takes no .build.lock (only shim/out/.gate.lock: one gate per worktree).
# Live state: shim/out/gate.progress (START/END per step), gate.result (RUNNING <pid> <epoch>, then the GATE line + epoch; atomic), shim/out/gate/<step>.log.
# A failing step stops the rest at their next step boundary (--keep-going: only its own lane). Per-step timeout: GATE_STEP_TIMEOUT s (default 900 build*, 300 others). Exit 0 PASS, 1 FAIL, 2 usage.
# Reviewers/leads: trust a PASS line whose hash equals `git rev-parse --short HEAD` instead of re-running.
cd "$(dirname "$0")/.." || exit 2
git rev-parse --short HEAD >/dev/null 2>&1 || { echo "GATE ERROR not a git checkout"; exit 2; }
quick=; keep=
for a in "$@"; do case $a in --quick) quick=1 ;; --keep-going) keep=1 ;; *) echo "GATE ERROR usage: check-all.sh [--quick] [--keep-going]"; exit 2 ;; esac; done
case ${GATE_STEP_TIMEOUT:-1} in *[!0-9]*) echo "GATE ERROR GATE_STEP_TIMEOUT must be seconds"; exit 2 ;; esac
case ${GATE_JOBS:-1} in ''|*[!0-9]*|0) echo "GATE ERROR GATE_JOBS must be a positive integer"; exit 2 ;; esac
J=${GATE_JOBS:-8}
mkdir -p shim/out
log=shim/out/gate.log; stamp=shim/out/gate.stamp; prog=shim/out/gate.progress; res=shim/out/gate.result; owner=shim/out/.gate.owner
# one gate per worktree (it owns shim/out/gate, gate.log, gate.result). Not .build.lock: xrun/probe-run builds into shim/out and the gate no longer does.
# tools-sync also refuses a pull while this lock is held (the gate would see mixed tool versions).
exec 9>shim/out/.gate.lock
if ! flock -n 9; then
	w="waiting for the gate lock held by $(cat "$owner" 2>/dev/null || echo unknown)"; echo "$w" >&2; echo "$w" >>"$prog"
	flock -w 3600 9 || { echo "GATE FAIL (another gate in this worktree holds shim/out/.gate.lock for 3600 s)"; exit 1; }
fi
echo "$$ ${USER:-?} check-all $(date +%s)" >"$owner"
: > "$log"; : > "$prog"; rm -rf shim/out/gate; mkdir -p shim/out/gate
# gate.result: temp + rename, so a watcher never reads a half line. RUNNING while the gate runs, then the GATE line + epoch.
putres() { printf '%s\n' "$1" >"$res.$$" && mv -f "$res.$$" "$res"; }
finish() { putres "$1 $(date +%s)"; echo "$1"; exit "$2"; }
putres "RUNNING $$ $(date +%s)"
h=$(git rev-parse --short HEAD)$([ -n "$(git status --porcelain)" ] && echo "+dirty")
ALL="build build-fast build-usb build-wlan build-gpu build-gpupower build-gpuclk build-gpurails build-gpurailon build-gpujob build-touch build-udump host-tests boot-safety unit phone-state hang-recover probe-run rootless public-split validate"
want=$ALL; fullnote=
if [ -n "$quick" ]; then
	want=$(python3 tools/gate_select.py "$stamp") || finish "GATE FAIL $h gate_select (see $log)" 1
	[ "$want" = FULL ] && { want=$ALL; quick=; fullnote=" quick: no usable stamp, ran full"; }
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# step LANE NAME CMD...: skipped unless selected; a failure marks the lane and skips its remaining steps (and, unless --keep-going,
# everyone's: they stop at their next step boundary). Each step runs under timeout -k 10 (build* 900 s, others 300 s, env GATE_STEP_TIMEOUT)
# and logs to shim/out/gate/NAME.log; steps of one lane may run in parallel (par), so every step writes only its own files.
step() {
	l=$1; n=$2; shift 2
	case " $want " in *" $n "*) ;; *) return 0 ;; esac
	if [ -n "$keep" ]; then [ -e "$tmp/$l.lane" ] && return 0; else compgen -G "$tmp/*.fail" >/dev/null && return 0; fi
	case $n in build*) b=900 ;; *) b=300 ;; esac; b=${GATE_STEP_TIMEOUT:-$b}
	echo "START $n $(date +%s)" >>"$prog"; s=$SECONDS
	# 9>&- below: a step's children (an adb server, a stub sleep) must not keep the gate lock after the gate ends
	# bash -c re-dispatches so that the exported shell function `needs` can run under timeout
	timeout -k 10 "$b" bash -c '"$@"' _ "$@" >"shim/out/gate/$n.log" 2>&1 9>&-; rc=$?
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
	par step A build-wlan   bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --wlan-probe" && $B --out out/gate/build-wlan-0 && $B --wlan-rails --wlan-en --out out/gate/build-wlan-1 && $B --wlan-smc-read --out out/gate/build-wlan-2 && $B --wlan-iso --out out/gate/build-wlan-3 && ! $B --wlan-iso --wlan-en --out out/gate/build-wlan-4'
	par step A build-gpu    bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --out out/gate/build-gpu-0'
	par step A build-gpupower bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --gpu-probe" && $B --gpu-power --out out/gate/build-gpupower-0 && $B --gpu-power=stack --out out/gate/build-gpupower-1'
	par step A build-gpuclk bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-clk --gpu-power --out out/gate/build-gpuclk-0'
	par step A build-gpurails bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails --out out/gate/build-gpurails-0'
	par step A build-gpurailon bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails --gpu-rail-on --gpu-power --out out/gate/build-gpurailon-0'
	par step A build-gpujob bash -c 'cd shim && B="./build.sh --board bluejay --avb --usb-gadget --gpu-probe" && $B --gpu-s2mpu --out out/gate/build-gpujob-0 && $B --gpu-job --out out/gate/build-gpujob-1'
	par step A build-touch  bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --touch-probe=run,mux,cs,osc,loop,rst,misopu,rails --out out/gate/build-touch-0'
	par step A build-udump  bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast --udump 1 --out out/gate/build-udump-0'
	wait
	step A host-tests   bash -c 'cd shim/out/gate/build-0 && ls *_host_test >/dev/null && for t in *_host_test host_test; do [ -x "$t" ] && "$t" || [ ! -x "$t" ] || exit 1; done'
	step A boot-safety  python3 tools/test-bluejay-boot.py --out shim/out/gate/build-0 shim/out/gate/build-0/shim-boot-avb.img
}
laneB() {
	step B validate     python3 tools/validate-repo.py
	step B public-split needs tools/make-public-tree.py python3 tools/make-public-tree.py --check
	step B unit         python3 -m unittest discover -s tools -p "test_*.py"
	step B phone-state  tools/phone-state --self-test
	step B hang-recover  tools/hang-recover --self-test
	step B probe-run    tools/probe-run --self-test
	step B rootless     needs tools/build-ios-root-rootless.sh bash -c 'bash -n tools/build-ios-root-rootless.sh && bash -n tools/rootless/vm.sh && bash -n tools/rootless/compare-images.sh && { [ ! -s "${PIXELIOS_VM:-$HOME/pixelios-artifacts/vm}/vmlinuz" ] || ! command -v fakeroot >/dev/null || tools/build-ios-root-rootless.sh --selftest; }'
}
laneA & laneB; wait
# deterministic log: steps in ALL order, then timings
for n in $ALL; do [ -s "shim/out/gate/$n.log" ] && { echo "== $n"; cat "shim/out/gate/$n.log"; }; done >>"$log"
{ echo "== timings (s)"; for n in $ALL; do cat "$tmp/$n.time" 2>/dev/null; done; } >>"$log"
fail=; for n in $ALL; do [ -e "$tmp/$n.fail" ] && { fail=$(cat "$tmp/$n.fail"); break; }; done
total=$(echo $ALL | wc -w); ran=$(echo $want | wc -w)
if [ -n "$quick" ]; then tag=" quick: ran $ran of $total steps"; [ -n "$fail" ] && tag=" quick: $fail"; else tag=$fullnote; [ -n "$fail" ] && tag="$tag${fullnote:+:} $fail"; fi
[ -n "$fail" ] && finish "GATE FAIL $h$tag (see $log)" 1
# stamp only on a clean-tree full PASS (+dirty also covers untracked files): the next --quick diffs against exactly this commit
[ -z "$quick" ] && [ "${h%+dirty}" = "$h" ] && git rev-parse HEAD >"$stamp"
finish "GATE PASS $h$tag" 0
