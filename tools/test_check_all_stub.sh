#!/bin/bash
# Stub test of tools/check-all.sh: a COPY runs in a temp git repo with fake steps (no builds, ~20 s). Covers: interrupt/kill cleanup
# (no leftover child, no RUNNING, lock free), the stamp rules, GATE_STEP_TIMEOUT validation, the lock-wait WAITING state.
# Never pgrep/pkill (AP-36): processes are signalled by the PID this script spawned (its own process group via `set -m`).
# CHECK_ALL=path tests another copy of the script (e.g. an old version).
self=$(readlink -f "$0"); tools=$(dirname "$self")
CHECK_ALL=${CHECK_ALL:-$tools/check-all.sh}
set -m
T=$(mktemp -d)
# leftovers of a broken gate (recorded pids only) must not outlive the test
cleanup() {
	local f
	for f in "$T"/*.pids/pids; do
		# shellcheck disable=SC2046  # the pid list is word-split on purpose
		kill -KILL $(tr -s " \n" "  " <"$f") 2>/dev/null
	done
	rm -rf "$T"
}
trap cleanup EXIT
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
chk() { if eval "$2"; then ok "$1"; else bad "$1 [$2]"; fi; }

mkrepo() { # DIR: a repo with the files the gate's steps call, all stubs
	local d=$1; mkdir -p "$d/tools" "$d/shim"; cd "$d" || exit 2
	git init -q . && git config user.email t@t && git config user.name t
	cp "$CHECK_ALL" tools/check-all.sh; cp "$tools/gate_select.py" "$tools/shell-files" tools/
	printf 'shim/out/\n__pycache__/\n' >.gitignore
	cat >shim/build.sh <<'S'
#!/bin/bash
# stub: --out DIR creates the files the later steps need; STUB_SLEEP=<step> makes that step hang (grandchild too), recording pids in $STUB_PIDS
out=; i=; e=
while [ $# -gt 0 ]; do case $1 in --out) out=$2; shift ;; --wlan-iso) i=1 ;; --wlan-en) e=1 ;; esac; shift; done
mkdir -p "$out"; touch "$out/shim-boot-avb.img"; printf '#!/bin/sh\nexit 0\n' >"$out/x_host_test"; chmod +x "$out/x_host_test"; echo x_host_test >"$out/host-tests.list"
[ "$i$e" = 11 ] && { echo "--wlan-iso cannot be combined with --wlan-en (stub)" >&2; exit 2; }
[ -n "${STUB_FAIL:-}" ] && [[ $out == *"/$STUB_FAIL-"* ]] && exit 1
# a commit made while the gate runs (HEAD moves)
[ -n "${STUB_COMMIT:-}" ] && [[ $out == */build-0 ]] && { cd .. && echo x >new.c && git add new.c && git commit -qm moved; cd shim || exit 1; }
if [ -n "${STUB_SLEEP:-}" ] && [[ $out == *"/$STUB_SLEEP-"* ]]; then
	sleep 300 & echo "$$ $!" >>"$STUB_PIDS/pids"; touch "$STUB_PIDS/up.$STUB_SLEEP"; wait
fi
exit 0
S
	chmod +x shim/build.sh
	cat >tools/validate-repo.py <<'S'
import os, time
if os.environ.get("STUB_VALIDATE_SLEEP"):
    open(os.environ["STUB_PIDS"] + "/pids", "a").write("%d\n" % os.getpid())
    open(os.environ["STUB_PIDS"] + "/up.validate", "w").close()
    time.sleep(300)
S
	: >tools/test-bluejay-boot.py
	printf 'import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n' >tools/test_ok.py
	for s in phone-state hang-recover probe-run; do printf '#!/bin/sh\nexit 0\n' >"tools/$s"; chmod +x "tools/$s"; done
	git add -A && git commit -qm init
	mkdir -p "$STUB_PIDS"
}
alive() { local p; for p in $(tr -s ' \n' '  ' <"$STUB_PIDS/pids" 2>/dev/null); do kill -0 "$p" 2>/dev/null && return 0; done; return 1; }
waitfor() { local _; for _ in $(seq 100); do eval "$1" && return 0; sleep 0.1; done; return 1; }
res() { cat shim/out/gate.result 2>/dev/null; }

interrupt_case() { # NAME SIGNAL(INT = whole process group, TERM = pid only) UP-MARKER env...
	local name=$1 sig=$2 up=$3; shift 3
	export STUB_PIDS=$T/$name.pids; mkrepo "$T/$name"
	env "$@" bash tools/check-all.sh >"$T/$name.out" 2>&1 &
	local g=$!
	waitfor '[ -e "$STUB_PIDS/up.'$up'" ]' || bad "$name: step never started"
	[ "$name" != par ] || waitfor '[ -e "$STUB_PIDS/up.validate" ]'
	if [ "$sig" = INT ]; then kill -INT -- "-$g"; else kill -TERM "$g"; fi
	waitfor '! kill -0 '"$g"' 2>/dev/null' || { bad "$name: gate still running 10 s after the signal"; kill -KILL -- "-$g" 2>/dev/null; }
	waitfor '! alive'
	chk "$name: no leftover step process" '! alive'
	chk "$name: gate.result is the interrupted line, not RUNNING" 'res | grep -Eq "^GATE FAIL .* interrupted [0-9]+$"'
	chk "$name: lock released" 'flock -n shim/out/.gate.lock true'
	chk "$name: one-line stdout says interrupted" 'grep -q "^GATE FAIL .* interrupted" "$T/$name.out"'
	cd "$T" || exit 2
}
# the parallel phase (build-fast hangs) plus a hanging lane B step; Ctrl-C style group INT
interrupt_case par INT build-fast STUB_SLEEP=build-fast STUB_VALIDATE_SLEEP=1
# the serial first build hangs; plain TERM to the gate pid only
interrupt_case serial TERM build STUB_SLEEP=build

# --- stamp: written only by a clean, unmoved full PASS
gate() { bash tools/check-all.sh "$@" >"$T/gate.out" 2>&1; echo $? >"$T/gate.rc"; }
export STUB_PIDS=$T/st.pids; mkrepo "$T/st"
gate; chk "full PASS" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/gate.out"'
# shellcheck disable=SC2034  # head1 is read by chk through eval
head1=$(git rev-parse HEAD)
chk "full PASS stamps HEAD" '[ "$(cat shim/out/gate.stamp)" = "$head1" ]'
echo '# doc' >>.gitignore; git commit -qam doc
gate --quick; chk "quick PASS" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of" "$T/gate.out"'
chk "quick does not move the stamp" '[ "$(cat shim/out/gate.stamp)" = "$head1" ]'
rm shim/out/gate.stamp
STUB_COMMIT=1 gate; chk "HEAD moved during the run: +dirty, no stamp" 'grep -Eq "^GATE PASS [0-9a-f]+\+dirty$" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
git reset -q --hard HEAD; echo y >untracked.txt
gate; chk "untracked file at start: +dirty, no stamp" 'grep -Eq "^GATE PASS [0-9a-f]+\+dirty$" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
rm untracked.txt
STUB_FAIL=build-usb gate; chk "failing step: GATE FAIL, exit 1, no stamp" 'grep -q "^GATE FAIL .* build-usb" "$T/gate.out" && [ "$(cat "$T/gate.rc")" = 1 ] && [ ! -e shim/out/gate.stamp ]'
STUB_FAIL=build-usb gate --keep-going; chk "keep-going failure: no stamp" '[ ! -e shim/out/gate.stamp ]'
GATE_STEP_TIMEOUT=2 STUB_SLEEP=build-fast gate; chk "timeout: GATE FAIL (timeout), no stamp" 'grep -q "^GATE FAIL .* build-fast (timeout)" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
for v in 0 00 abc 1x; do GATE_STEP_TIMEOUT=$v gate; chk "GATE_STEP_TIMEOUT=$v rejected with exit 2" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "GATE_STEP_TIMEOUT" "$T/gate.out"'; done
gate; chk "stamp written again by a clean full PASS" '[ "$(cat shim/out/gate.stamp)" = "$(git rev-parse HEAD)" ]'
chk "owner file removed after the run" '[ ! -e shim/out/.gate.owner ]'

# --- lock wait: WAITING replaces the old PASS, the holder is named, the gate then runs
( flock 8; exec sleep 60 ) 8>shim/out/.gate.lock & holder=$!   # exec: the subshell pid is the sleep that holds the lock
waitfor '! flock -n shim/out/.gate.lock true'
bash tools/check-all.sh >"$T/wait.out" 2>&1 & wg=$!
waitfor 'res | grep -q "^WAITING"'; chk "lock wait: gate.result is WAITING (old PASS gone)" 'res | grep -Eq "^WAITING $wg [0-9]+$"'
chk "lock wait: unknown holder is named unknown" 'grep -q "held by unknown" "$T/wait.out"'
kill "$holder"; waitfor '! kill -0 '"$wg"' 2>/dev/null'
chk "lock wait: gate runs after the holder is gone" 'grep -Eq "^GATE PASS" "$T/wait.out"'
cd "$T" || exit 2

echo "test_check_all_stub: $([ $fail = 0 ] && echo PASS || echo FAIL)"; exit $fail
