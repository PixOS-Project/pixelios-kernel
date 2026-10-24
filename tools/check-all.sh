#!/bin/bash
# One verification gate: build both bluejay variants, host tests, boot-image safety check, unit tests, repo validation.
# Run from any checkout; full output goes to shim/out/gate.log (with a per-step timing table), stdout is ONE line:
#   "GATE PASS|FAIL <short-hash> [failed step]"                       (full gate: what must pass before a push)
#   "GATE PASS|FAIL <short-hash> quick: ran N of M steps | <failed step>"   (--quick: only the steps the diff needs)
# --quick diffs against the commit in shim/out/gate.stamp (written by the last clean full PASS); no usable stamp = full gate.
# Two lanes run in parallel (max 2 jobs): A = shim builds (share shim/out) + host-tests + boot-safety, B = unit, rootless, validate.
# Reviewers/leads: trust a PASS line whose hash equals `git rev-parse --short HEAD` instead of re-running.
cd "$(dirname "$0")/.." || exit 2
mkdir -p shim/out
# shim/out is shared by every build in this worktree: one writer at a time (xrun/probe-run take the same lock around their build; they boot a private copy)
exec 9>shim/out/.build.lock; flock -w 3600 9 || { echo "GATE FAIL (shim/out busy for 3600 s: another build in this worktree holds shim/out/.build.lock)"; exit 1; }
log=shim/out/gate.log; stamp=shim/out/gate.stamp; : > "$log"
h=$(git rev-parse --short HEAD)$(git diff --quiet HEAD || echo "+dirty")
ALL="build build-fast build-usb build-wlan build-gpu build-gpupower build-gpuclk build-gpurails build-gpurailon build-gpujob build-touch build-udump host-tests boot-safety unit phone-state hang-recover probe-run rootless public-split validate"
quick=; [ "$1" = --quick ] && quick=1
want=$ALL
if [ -n "$quick" ]; then
	want=$(python3 tools/gate_select.py "$stamp") || { echo "GATE FAIL $h gate_select (see $log)"; exit 1; }
	[ "$want" = FULL ] && { want=$ALL; quick=; }
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# step LANE NAME CMD...: skipped unless selected; a failure marks the lane and skips its remaining steps.
step() {
	l=$1; n=$2; shift 2
	case " $want " in *" $n "*) ;; *) return 0 ;; esac
	[ -e "$tmp/$l.fail" ] && return 0
	echo "== $n" >>"$tmp/$l.log"; s=$SECONDS
	# 9>&- below: a step's children (an adb server, a stub sleep) must not keep shim/out locked after the gate ends
	if "$@" >>"$tmp/$l.log" 2>&1 9>&-; then echo "$n $((SECONDS-s))s" >>"$tmp/$l.times"
	else echo "$n" >"$tmp/$l.fail"; echo "$n FAILED $((SECONDS-s))s" >>"$tmp/$l.times"; fi
}
# needs FILE CMD...: run CMD only if FILE exists in this repository (the public repos hold different parts of the monorepo)
needs() { f=$1; shift; [ -e "$f" ] || { echo "skip: $f is not in this repository"; return 0; }; "$@"; }
laneA() {
	step A build        bash -c 'cd shim && ./build.sh --board bluejay --avb'
	step A build-fast   bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast'
	step A build-usb    bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget'
	step A build-wlan   bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --wlan-probe && ./build.sh --board bluejay --avb --usb-gadget --wlan-probe --wlan-rails --wlan-en && ./build.sh --board bluejay --avb --usb-gadget --wlan-probe --wlan-smc-read && ./build.sh --board bluejay --avb --usb-gadget --wlan-probe --wlan-iso && ! ./build.sh --board bluejay --avb --usb-gadget --wlan-probe --wlan-iso --wlan-en'
	step A build-gpu    bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe'
	step A build-gpupower bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-power && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-power=stack'
	step A build-gpuclk bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-clk --gpu-power'
	step A build-gpurails bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails'
	step A build-gpurailon bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-rails --gpu-rail-on --gpu-power'
	step A build-gpujob bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-s2mpu && ./build.sh --board bluejay --avb --usb-gadget --gpu-probe --gpu-job'
	step A build-touch  bash -c 'cd shim && ./build.sh --board bluejay --avb --usb-gadget --touch-probe=run,mux,cs,osc,loop,rst,misopu,rails'
	step A build-udump  bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast --udump 1'
	step A host-tests   bash -c 'cd shim && for t in out/*_host_test out/host_test; do [ -x "$t" ] && "$t" || [ ! -x "$t" ] || exit 1; done'
	step A boot-safety  python3 tools/test-bluejay-boot.py shim/out/shim-boot-avb.img
}
laneB() {
	step B unit         python3 -m unittest discover -s tools -p "test_*.py"
	step B phone-state  tools/phone-state --self-test
	step B hang-recover  tools/hang-recover --self-test
	step B probe-run    tools/probe-run --self-test
	step B rootless     needs tools/build-ios-root-rootless.sh bash -c 'bash -n tools/build-ios-root-rootless.sh && bash -n tools/rootless/vm.sh && bash -n tools/rootless/compare-images.sh && { [ ! -s "${PIXELIOS_VM:-$HOME/pixelios-artifacts/vm}/vmlinuz" ] || ! command -v fakeroot >/dev/null || tools/build-ios-root-rootless.sh --selftest; }'
	step B public-split needs tools/make-public-tree.py python3 tools/make-public-tree.py --check
	step B validate     python3 tools/validate-repo.py
}
laneA & laneB; wait
# deterministic log: lane A then lane B, then timings
cat "$tmp"/A.log "$tmp"/B.log >>"$log" 2>/dev/null
{ echo "== timings (s)"; cat "$tmp"/A.times "$tmp"/B.times 2>/dev/null; } >>"$log"
fail=$(cat "$tmp"/A.fail "$tmp"/B.fail 2>/dev/null | head -1)
total=$(echo $ALL | wc -w); ran=$(echo $want | wc -w)
if [ -n "$quick" ]; then tag=" quick: ran $ran of $total steps"; [ -n "$fail" ] && tag=" quick: $fail"; else tag=; [ -n "$fail" ] && tag=" $fail"; fi
if [ -n "$fail" ]; then echo "GATE FAIL $h$tag (see $log)"; exit 1; fi
# stamp only on a clean-tree full PASS: the next --quick diffs against exactly this commit
[ -z "$quick" ] && [ "${h%+dirty}" = "$h" ] && [ -z "$(git ls-files --others --exclude-standard)" ] && git rev-parse HEAD >"$stamp"
echo "GATE PASS $h$tag"
