#!/bin/bash
# Stub test of tools/check-all.sh: a COPY runs in a temp git repo with fake steps (no builds, ~20 s). Covers: interrupt/kill cleanup
# (no leftover child, no RUNNING, lock free), the stamp rules, GATE_STEP_TIMEOUT validation, the lock-wait WAITING state.
# Never pgrep/pkill (AP-36): processes are signalled by the PID this script spawned (its own process group via `set -m`).
# CHECK_ALL=path tests another copy of the script (e.g. an old version).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
self=$(readlink -f "$0"); tools=$(dirname "$self")
CHECK_ALL=${CHECK_ALL:-$tools/check-all.sh}
set -m
# The gate runs this test with its own GATE_* environment (remote-job exports GATE_XNU_BUILD=0, GATE_JOBS, GATE_UNIT_SHARDS, ... into the offload-host container):
# the nested stub gates must see none of it, or their steps are skipped/resized (offload-host f3 runs #1+#2: xnu-build never ran, "xnu.args: No such file").
# Only the GATE_* vars this script sets itself below survive.
while read -r v; do unset "$v"; done < <(compgen -e | grep '^GATE_')
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
export GATE_PASS_DIR=$T/gate-pass   # the host registry of full PASSes: never the real one (research 68 D2)
export HEAVY_JOB_HELD=stub-test   # TP-042: the temp-repo copies have no tools/heavy-job; the host-lock cases below test the older GATE_HOST_LOCK
export GATE_TIMES_FILE=$T/gate-times.tsv   # the ledger row every gate appends (tools/gate-summary --append): never the real logs/ one, and not inside a stub repo (an untracked file there would make the next gate +dirty)
export GATE_HOST_LOCK=0   # the stub gates must not queue behind real gates on this host; the host-lock case below sets its own file
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
chk() { if eval "$2"; then ok "$1"; else bad "$1 [$2]"; fi; }

mkrepo() { # DIR: a repo with the files the gate's steps call, all stubs
	local d=$1; mkdir -p "$d/tools" "$d/shim"; cd "$d" || exit 2
	git init -q . && git config user.email t@t && git config user.name t
	cp "$CHECK_ALL" tools/check-all.sh; cp "$tools/gate_select.py" "$tools/unit_select.py" "$tools/gate-manifest.json" "$tools/gate-summary" "$tools/shell-files" "$tools/gate-unit-shards" tools/
	printf 'shim/out/\n__pycache__/\n' >.gitignore
	cat >shim/build.sh <<'S'
#!/bin/bash
# stub: --out DIR creates the files the later steps need; STUB_SLEEP=<step> makes that step hang (grandchild too), recording pids in $STUB_PIDS
out=; i=; e=
while [ $# -gt 0 ]; do case $1 in --out) out=$2; shift ;; --wlan-iso) i=1 ;; --wlan-en) e=1 ;; esac; shift; done
mkdir -p "$out"; touch "$out/shim-boot-avb.img"; printf '#!/bin/sh\nexit 0\n' >"$out/x_host_test"; chmod +x "$out/x_host_test"; echo x_host_test >"$out/host-tests.list"
[ "$i$e" = 11 ] && { echo "--wlan-iso cannot be combined with --wlan-en (stub)" >&2; exit 2; }
# STUB_KILL_LANE=<step>: kill -KILL the lane subshell (the nearest ancestor running check-all.sh) from inside that step: a lane that vanishes without recording
if [ -n "${STUB_KILL_LANE:-}" ] && [[ $out == *"/$STUB_KILL_LANE-"* ]]; then
	p=$$; while [ "$p" -gt 1 ]; do p=$(awk '{print $4}' /proc/$p/stat); tr '\0' ' ' </proc/$p/cmdline | grep -q check-all.sh && { kill -KILL "$p"; break; }; done
fi
[ -n "${STUB_FAIL:-}" ] && [[ $out == *"/$STUB_FAIL-"* ]] && exit 1
# STUB_BUILD_PEER=1 (K3): the `build` variant fails unless build-usb has STARTED (its --out dir exists) within 10 s = `build` ran in the first wave with the others, not as a serial canary before them
if [ -n "${STUB_BUILD_PEER:-}" ] && [[ $out == */build-0 ]]; then
	for _ in $(seq 100); do [ -d out/gate/build-usb-0 ] && break; sleep 0.1; done
	[ -d out/gate/build-usb-0 ] || { echo "stub: build ran before build-usb started (serial canary)" >&2; exit 1; }
fi
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
open(os.environ["STUB_PIDS"] + "/nice", "w").write(str(os.nice(0)))
def _stat(pid):  # (nice, ppid) from /proc; the comm field may contain spaces, so split after the last ")"
    f = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()
    return int(f[16]), int(f[1])
# The gate lane is step -> timeout -> lane bash. Compare with that bash's CURRENT nice, not with 0: a daemon (ananicy-cpp on offload-host) or a
# container runtime moves the baseline (observed -4 and 19); AP-111.
open(os.environ["STUB_PIDS"] + "/nice_lane", "w").write(str(_stat(_stat(os.getppid())[1])[0]))
if os.environ.get("STUB_VALIDATE_SLEEP"):
    open(os.environ["STUB_PIDS"] + "/pids", "a").write("%d\n" % os.getpid())
    open(os.environ["STUB_PIDS"] + "/up.validate", "w").close()
    time.sleep(300)
if os.environ.get("STUB_VALIDATE_TAIL"):
    print(os.environ["STUB_VALIDATE_TAIL"])
S
	: >tools/test-bluejay-boot.py
	# the wlan mutant test: gate_select.py derives the step's inputs from its os.path.join(ROOT, ...) literals
	printf 'import os, unittest\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nT = os.path.join(ROOT, "shim", "wlan_wr_host_test.c")\nclass M(unittest.TestCase):\n    def test_m(self):\n        e = os.environ.get("STUB_MUT_ENV")\n        if e: open(e, "w").write(" ".join(k + "=" + os.environ.get(k, "-") for k in ("GATE_MUT_JOBS", "GATE_MUT_JOBS_SRC", "GATE_UNIT_SHARDS_SRC", "GATE_LANES_DONE", "GATE_LANES_A_DONE")))\n        import time; time.sleep(float(os.environ.get("STUB_MUT_SLEEP", "0")))\n        d = os.environ.get("STUB_MUT_DONE")\n        if d: open(d, "w").write("1")\n        w = os.environ.get("STUB_MUT_WAITFILE"); t = time.time()\n        while w and not os.path.exists(w) and time.time() - t < 120: time.sleep(0.1)\n        self.assertFalse(os.environ.get("STUB_MUT_FAIL"))\n' >tools/test_wlan_wr_mutants.py
	: >shim/wlan_wr_host_test.c
	: >tools/check-rules.py
	printf 'import unittest\nimport os\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    @unittest.skipIf(os.environ.get("STUB_SKIP"), "stub skip")\n    def test_b(self): pass\n' >tools/test_ok.py
	# lint-c stub: STUB_LINT=<findings> -> RESULT: FAIL findings=N (exit 1); STUB_LINT=broken -> no RESULT line (exit 2)
	printf 'import os, sys\nn = os.environ.get("STUB_LINT", "0")\nif n == "broken":\n    sys.exit(2)\nprint("RESULT: %%s files=1 findings=%%s" %% ("FAIL" if n != "0" else "PASS", n))\nsys.exit(1 if n != "0" else 0)\n' >tools/lint-c
	# gate-xnu-build stub (AP-257): records its arguments; STUB_XNU=fail -> RESULT: FAIL xnu-build (exit 1)
	printf '#!/bin/sh\necho "xnu-build args: $*" >>"$STUB_PIDS/xnu.args"\necho "budget=${GATE_STEP_BUDGET:-unset}" >>"$STUB_PIDS/xnu.budget"\n[ "${STUB_XNU:-}" = fail ] && { echo "RESULT: FAIL xnu-build errors=1 first=bsd/x.c:1"; exit 1; }\necho "RESULT: PASS xnu-build sha=stub${STUB_XNU_EXTRA:-}"\n' >tools/gate-xnu-build; chmod +x tools/gate-xnu-build
	# stack-apply stub (AP-258): STUB_STACK=fail -> RESULT: FAIL naming the patch (exit 1); lint-mutant-needles stub (AP-281): STUB_NEEDLES=fail -> MUTANT NEEDLE MISSING + RESULT: FAIL
	printf '#!/bin/sh\necho "stack-apply ran" >>"$STUB_PIDS/stack.ran"\n[ "${STUB_STACK:-}" = nonet ] && { echo "STACK-APPLY NOT RUN: no network (stub)"; echo "RESULT: SKIPPED stack-apply NOT RUN: no network"; exit 0; }\n[ "${STUB_STACK:-}" = fail ] && { echo "RESULT: FAIL 3/5 ok, first failure 0004-x.patch (1 not tried)"; echo "FIRST FAILURE 0004-x.patch (git apply error, 10 lines):"; exit 1; }\necho "RESULT: PASS 5/5 patches apply cleanly in order"\n' >tools/stack-apply; chmod +x tools/stack-apply
	printf 'import os, sys\nif os.environ.get("STUB_NEEDLES") == "fail":\n    print("MUTANT NEEDLE MISSING test_x_mutants.py:MUTANTS %%r -> shim/x.c: %%r" %% ("m1", "gone"))\n    print("RESULT: FAIL mutant-needles tables=1 needles=1 missing=1 multi=0")\n    sys.exit(1)\nprint("RESULT: PASS mutant-needles tables=1 needles=1 missing=0 multi=0")\n' >tools/lint-mutant-needles
	# gate-inputs stub (AP-11/144): STUB_INPUTS=fail -> MISSING INPUT + RESULT: FAIL; GATE_NO_INPUTS=reason -> the real tool's SKIPPED line
	printf '#!/bin/sh\necho "inputs ran" >>"$STUB_PIDS/inputs.ran"\n[ -n "${GATE_NO_INPUTS:-}" ] && { echo "RESULT: PASS inputs SKIPPED by GATE_NO_INPUTS=$GATE_NO_INPUTS"; exit 0; }\n[ "${STUB_INPUTS:-}" = fail ] && { echo "MISSING INPUT research/ipsw-x/d22ap.adt (in stub)"; echo "RESULT: FAIL inputs missing the gitignored input above"; exit 1; }\necho "RESULT: PASS inputs stub"\n' >tools/gate-inputs; chmod +x tools/gate-inputs
	# lint-boot-markers stub (AP-229): STUB_BOOTMARK=fail -> BOOT PRINT HITS MARKER + RESULT: FAIL
	printf 'import os, sys\nif os.environ.get("STUB_BOOTMARK") == "fail":\n    print("BOOT PRINT HITS MARKER 0004-x.patch:9 marker %%r" %% "panic/hang/watchdog")\n    print("RESULT: FAIL boot-markers patches=1 statements=1 hits=1")\n    sys.exit(1)\nprint("RESULT: PASS boot-markers patches=1 statements=1 hits=0")\n' >tools/lint-boot-markers
	# lint-shellcheck-reason stub (T-eff6): the shellcheck step runs it after shellcheck; the real tool has its own test (test_lint_shellcheck_reason.py)
	printf 'print("RESULT: PASS shellcheck-reason files=1 disables=0 bare=0")\n' >tools/lint-shellcheck-reason
	for s in phone-state hang-recover probe-run lint-phone-converge; do printf '#!/bin/sh\nexit 0\n' >"tools/$s"; chmod +x "tools/$s"; done
	git add -A && git commit -qm init
	mkdir -p "$STUB_PIDS"
}
# a zombie is dead: in a container whose pid 1 does not reap orphans (the gate image runs bash as pid 1) killed grandchildren stay Z forever (AP-111)
alive() { local p st; for p in $(tr -s ' \n' '  ' <"$STUB_PIDS/pids" 2>/dev/null); do
	kill -0 "$p" 2>/dev/null || continue; st=$(cat "/proc/$p/stat" 2>/dev/null) || continue; st=${st##*) }; [ "${st%% *}" = Z ] || return 0; done; return 1; }
waitfor() { local _; for _ in $(seq "${WAITFOR_TICKS:-900}"); do eval "$1" && return 0; sleep 0.1; done; return 1; }
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

chk "interrupted gates write no registry entry" '[ ! -e "$GATE_PASS_DIR" ] || [ -z "$(ls "$GATE_PASS_DIR")" ]'

# --- stamp: written only by a clean, unmoved full PASS
gate() { bash tools/check-all.sh "$@" >"$T/gate.out" 2>&1; echo $? >"$T/gate.rc"; }
export STUB_PIDS=$T/st.pids; mkrepo "$T/st"
gate; chk "full PASS" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/gate.out"'
chk "full gate runs the wlan-mutants step (lane C)" 'grep -q "^END wlan-mutants PASS" shim/out/gate.progress'
# shellcheck disable=SC2034  # head1 is read by chk through eval
head1=$(git rev-parse HEAD)
chk "full PASS stamps HEAD" '[ "$(cat shim/out/gate.stamp)" = "$head1" ]'
chk "full PASS registers HEAD in the host registry (content = check-all.sh blob + 2 state fields)" '[ "$(cut -d" " -f1 "$GATE_PASS_DIR/$head1")" = "$(git hash-object tools/check-all.sh)" ] && [ "$(wc -w <"$GATE_PASS_DIR/$head1")" = 3 ]'
# KNOWN-GAPS F8 + census-hermetic-r1 F2: unit skips and the census super counts reach the GATE line (copy: the stamp/registry of $T/st stay as they are)
rm -rf "$T/st3"; cp -a "$T/st" "$T/st3"; printf 'test_ok.py::T.test_b | stub skip | the stub skips this test on purpose (STUB_SKIP)\n' >"$T/skip-allow.txt"; ( cd "$T/st3" && GATE_UNIT_SKIP_ALLOW="$T/skip-allow.txt" STUB_SKIP=1 STUB_VALIDATE_TAIL='RESULT: PASS tools=3 super_new=1 (a) super_stale=2 (b)' bash tools/check-all.sh >"$T/st3.out" 2>&1 )
chk "counted warnings on the GATE line: unit skipped=1, census super_new=1 and super_stale=2 (T-9954: rendered as warn census)" 'grep -Eq "^GATE PASS [0-9a-f]+ unit skipped=1 warn census super_new=1: run tools/tool-census --write warn census super_stale=2: run tools/tool-census --write$" "$T/st3.out"'
# T-9fb9: on a tree WITH the ADT (a=1) an unit skip pinned with an "environment" reason still registers the PASS (the gate container lacks the devd toolchain); a skip pinned for any other reason does not. Both stay on the GATE line as "unit skipped=1".
for kind in env other; do
	rm -rf "$T/st4$kind"; cp -a "$T/st" "$T/st4$kind"; mkdir -p "$T/st4$kind/research/ipsw-x"; : >"$T/st4$kind/research/ipsw-x/d22ap.adt"
	if [ $kind = env ]; then why="environment (toolchain): the stub gate host lacks a tool"; else why="the stub skips this test on purpose (STUB_SKIP)"; fi
	printf 'test_ok.py::T.test_b | stub skip | %s\n' "$why" >"$T/skip-allow-$kind.txt"
	( cd "$T/st4$kind" && printf 'research/\n' >>.gitignore && git add .gitignore && git commit -qm adt-ignore && GATE_PASS_DIR="$T/gp-$kind" GATE_UNIT_SKIP_ALLOW="$T/skip-allow-$kind.txt" STUB_SKIP=1 bash tools/check-all.sh >"$T/st4$kind.out" 2>&1 )
done
chk "ADT tree, skip pinned 'environment': PASS keeps unit skipped=1, is registered, no (not registered" 'grep -Eq "^GATE PASS [0-9a-f]+ unit skipped=1$" "$T/st4env.out" && [ -n "$(ls "$T/gp-env" 2>/dev/null)" ] && grep -q "^unit skips: environment=1 other=0 " "$T/st4env/shim/out/gate/unit.log"'
chk "ADT tree, skip pinned for another reason: not registered, importable tag says so" 'grep -Eq "^GATE PASS [0-9a-f]+ unit skipped=1 \(not registered: skipped tests on a tree with the ADT, or no tree state\)$" "$T/st4other.out" && [ -z "$(ls "$T/gp-other" 2>/dev/null)" ] && grep -q "^unit skips: environment=0 other=1 " "$T/st4other/shim/out/gate/unit.log"'
# T-9954: a failing unit test names itself on the GATE FAIL line, next to a census warning that is NOT the cause
rm -rf "$T/st4"; cp -a "$T/st" "$T/st4"; printf 'import unittest\nclass T(unittest.TestCase):\n    def test_x(self): self.fail("stub")\n' >"$T/st4/tools/test_bad.py"; ( cd "$T/st4" && STUB_VALIDATE_TAIL='RESULT: PASS tools=3 super_new=1 (a) super_stale=0 (b)' bash tools/check-all.sh >"$T/st4.out" 2>&1; echo $? >"$T/st4.rc" )
chk "failing unit test: GATE FAIL names it (failed: test_bad.py), the census rides as warn census, exit 1" 'grep -Eq "^GATE FAIL [0-9a-f]+(\+dirty)? unit warn census super_new=1: run tools/tool-census --write warn census super_stale=0: run tools/tool-census --write failed: test_bad.py " "$T/st4.out" && [ "$(cat "$T/st4.rc")" = 1 ]'
# lint-c is WARN-only: findings ride on the GATE line as warn=N, a broken analyzer as ERROR, and the gate still PASSes (rule 8)
rm -rf "$T/st4"; cp -a "$T/st" "$T/st4"; ( cd "$T/st4" && STUB_LINT=3 bash tools/check-all.sh >"$T/st4.out" 2>&1 )
chk "lint-c findings: GATE PASS with lint-c warn=3" 'grep -Eq "^GATE PASS [0-9a-f]+ lint-c warn=3: see shim/out/gate/lint-c.log$" "$T/st4.out"'
rm -rf "$T/st5"; cp -a "$T/st" "$T/st5"; ( cd "$T/st5" && STUB_LINT=broken bash tools/check-all.sh >"$T/st5.out" 2>&1 )
chk "lint-c broken: GATE PASS with lint-c ERROR" 'grep -Eq "^GATE PASS [0-9a-f]+ lint-c ERROR: did not run" "$T/st5.out"'
chk "lint-c ERROR tag stays importable (no \" (see \", gate-import-remote:86, review r5-callers-r1 F1)" 'grep -q "^GATE PASS" "$T/st5.out" && ! grep -q " (see " "$T/st5.out"'
chk "xnu-build step budget covers a cold build: 2400 s (gate-xnu-step-r1 F2/F3)" 'grep -q "xnu-build) b=2400" tools/check-all.sh'
chk "full gate runs the real XNU build with --always (AP-257)" 'grep -q "^END xnu-build PASS" shim/out/gate.progress && grep -q "xnu-build args: --always --label gate-xnu" "$STUB_PIDS/xnu.args"'
# review xnu-remote-build-r1 F3/F7: the xnu-build step exports its own timeout (tools/xnu-remote-build derives its offload-host wait from it), the stale "1800 s" warning is gone, and a build that did not
# come from offload-host (fallback, lab-host-local, dirty tree) rides on the GATE line; a normal offload-host build adds nothing
chk "xnu-build step gets GATE_STEP_BUDGET = its timeout (2400)" 'grep -qx "budget=2400" "$STUB_PIDS/xnu.budget"'
chk "check-all no longer claims the xnu-local-build queue is 1800 s" '! grep -q "queue up to 1800 s" tools/check-all.sh'
rm -rf "$T/st7"; cp -a "$T/st" "$T/st7"; ( cd "$T/st7" && STUB_XNU_EXTRA=" 71s where=lab-host-fallback fallback=lab-host dirty=2" GATE_STEP_TIMEOUT=2000 bash tools/check-all.sh >"$T/st7.out" 2>&1 )
chk "xnu-build fallback + dirty tree: GATE PASS tagged 'xnu-build where=lab-host-fallback fallback=lab-host dirty=2', no parentheses" 'grep -Eq "^GATE PASS [0-9a-f]+(\+dirty)? xnu-build where=lab-host-fallback fallback=lab-host dirty=2$" "$T/st7.out"'
chk "GATE_STEP_TIMEOUT=2000 reaches the xnu-build step as GATE_STEP_BUDGET=2000" 'grep -qx "budget=2000" "$STUB_PIDS/xnu.budget"'
rm -rf "$T/st8"; cp -a "$T/st" "$T/st8"; ( cd "$T/st8" && STUB_XNU_EXTRA=" 71s where=offload-host dirty=0" bash tools/check-all.sh >"$T/st8.out" 2>&1 )
chk "xnu-build built on offload-host (dirty=0): no tag on the GATE line" 'grep -Eq "^GATE PASS [0-9a-f]+(\+dirty)?$" "$T/st8.out"'
# gate-xnu-step-r1 F5: a repo without tools/gate-xnu-build (public split) says so on the GATE line, the gate still passes
# G2: a step over its manifest budget puts slow=<step:obs/budget> on the GATE line and in the ledger row; it never fails the gate (here wlan-mutants: budget_s 1 in the copy's manifest, the stub sleeps 2 s)
rm -rf "$T/stS"; cp -a "$T/st" "$T/stS"; ( cd "$T/stS" && python3 - <<'PY' && STUB_MUT_SLEEP=2 bash tools/check-all.sh >"$T/stS.out" 2>&1
import json
m = json.load(open("tools/gate-manifest.json"))
for r in m["steps"]:
    if r["step"] == "wlan-mutants":
        r["budget_s"] = 1
json.dump(m, open("tools/gate-manifest.json", "w"), indent=1)
PY
)
chk "over-budget step: slow=wlan-mutants:N/1 on the PASS line, gate still PASS" 'grep -Eq "^GATE PASS [0-9a-f]+(\+dirty)? slow=wlan-mutants:[0-9]+/1( |$)" "$T/stS.out"'
chk "over-budget step: the ledger row is formal and has wlan-mutants seconds" 'tail -n 1 "$GATE_TIMES_FILE" | awk -F"\t" "NF==10 && \$3==\"formal\" && \$5==\"-\" && \$9 ~ /wlan-mutants:[2-9]/ {f=1} END{exit !f}"'
rm -rf "$T/st6"; cp -a "$T/st" "$T/st6"; ( cd "$T/st6" && rm tools/gate-xnu-build && bash tools/check-all.sh >"$T/st6.out" 2>&1 )
chk "xnu-build tool absent: GATE PASS tagged 'xnu-build not built: tool absent', no parentheses" 'grep -Eq "^GATE PASS [0-9a-f]+(\+dirty)? xnu-build not built: tool absent$" "$T/st6.out"'
echo '# doc' >>.gitignore; git commit -qam doc
gate --quick; chk "quick PASS" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of" "$T/gate.out"'
chk "quick does not move the stamp" '[ "$(cat shim/out/gate.stamp)" = "$head1" ]'
chk "quick PASS is not registered" '[ "$(ls "$GATE_PASS_DIR")" = "$head1" ]'
chk "quick on an unrelated change: wlan-mutants not started" '! grep -q "START wlan-mutants" shim/out/gate.progress'
chk "quick on an unrelated change: xnu-build not started" '! grep -q "START xnu-build" shim/out/gate.progress'
chk "quick on an unrelated change: stack-apply and mutant-needles not started (AP-258/281)" '! grep -q "START stack-apply" shim/out/gate.progress && ! grep -q "START mutant-needles" shim/out/gate.progress'
# AP-257: a batch that changes xnu/patches builds XNU; a build failure fails the gate and names the step; GATE_XNU_BUILD=0 skips loudly
mkdir -p xnu/patches; echo '--- a' >xnu/patches/0001-x.patch; git add xnu/patches/0001-x.patch; git commit -qm xnupatch
gate --quick; chk "quick after an xnu/patches change: xnu-build runs and passes" 'grep -q "^END xnu-build PASS" shim/out/gate.progress && grep -Eq "^GATE PASS" "$T/gate.out"'
STUB_XNU=fail gate --quick; chk "quick: a failing XNU build fails the gate on the xnu-build step" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: xnu-build " "$T/gate.out" && grep -q "RESULT: FAIL xnu-build" shim/out/gate/xnu-build.log'
chk "quick after an xnu/patches change: stack-apply ran and passed (AP-258)" 'grep -q "^END stack-apply PASS" shim/out/gate.progress && grep -q "^RESULT: PASS 5/5" shim/out/gate/stack-apply.log'
STUB_STACK=fail gate --quick; chk "quick: a stack that does not apply fails the gate on stack-apply and the log names the patch (AP-258)" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: stack-apply " "$T/gate.out" && grep -q "^RESULT: FAIL .*first failure 0004-x.patch" shim/out/gate/stack-apply.log'
# r42-gate-r1 F2: no network = the step skips loudly, exits 0, and the GATE line carries the tag (never silent); the registry/stamp rules are not changed by it (the xnu-build step still built the stack)
STUB_STACK=nonet gate --quick; chk "quick: stack-apply without network: GATE PASS tagged stack-apply not run: no network, the log line is loud (r42-gate-r1 F2)" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of [0-9]+ steps stack-apply not run: no network$" "$T/gate.out" && grep -q "^STACK-APPLY NOT RUN: no network" shim/out/gate/stack-apply.log'
gate --quick; chk "quick: a normal stack-apply run carries no no-network tag" '! grep -q "stack-apply not run" "$T/gate.out"'
gate --quick; chk "quick: the mutant-needles pass did not run for a plain patch change" '! grep -q "START mutant-needles" shim/out/gate.progress'
chk "quick after an xnu/patches change: boot-markers ran and passed (AP-229)" 'grep -q "^END boot-markers PASS" shim/out/gate.progress && grep -q "^RESULT: PASS boot-markers" shim/out/gate/boot-markers.log'
STUB_BOOTMARK=fail gate --quick; chk "quick: a boot print that trips a run marker fails the gate on boot-markers and the log names it (AP-229)" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: boot-markers " "$T/gate.out" && grep -q "^BOOT PRINT HITS MARKER " shim/out/gate/boot-markers.log'
GATE_XNU_BUILD=0 STUB_XNU=fail gate --quick; chk "GATE_XNU_BUILD=0: the step is skipped loudly, not failed" 'grep -Eq "^GATE PASS" "$T/gate.out" && grep -q "SKIPPED by GATE_XNU_BUILD=0" shim/out/gate/xnu-build.log'
# AP-11/144: a build-selecting change runs inputs; a missing input fails the gate on inputs and names it; GATE_NO_INPUTS=reason waives it loudly on the GATE line
echo '// shim' >>shim/wlan_wr_host_test.c; git commit -qam shimtouch
gate --quick; chk "quick after a shim change: inputs ran and passed (AP-11/144)" 'grep -q "^END inputs PASS" shim/out/gate.progress && grep -Eq "^GATE PASS" "$T/gate.out"'
STUB_INPUTS=fail gate --quick; chk "quick: a missing input fails the gate on inputs and the log names it (AP-11/144)" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: inputs " "$T/gate.out" && grep -q "^MISSING INPUT research/ipsw-x/d22ap.adt" shim/out/gate/inputs.log'
GATE_NO_INPUTS="reason x" STUB_INPUTS=fail gate --quick; chk "quick: GATE_NO_INPUTS waives inputs and says so on the GATE line" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of [0-9]+ steps inputs waived: GATE_NO_INPUTS=reason x" "$T/gate.out"'
git revert --no-edit HEAD >/dev/null   # drop the shim change again: the stamp diff is net, later rows must not select unit/build steps
# AP-281: a changed mutant table selects the presence pass; a missing needle fails the gate on mutant-needles and the log carries the MUTANT NEEDLE MISSING line
printf 'import unittest\nclass T(unittest.TestCase):\n    def test_t(self): pass\n' >tools/test_zz_mutants.py; git add tools/test_zz_mutants.py; git commit -qm mutants
gate --quick; chk "quick after a mutant table change: mutant-needles runs and passes (AP-281)" 'grep -q "^END mutant-needles PASS" shim/out/gate.progress && grep -Eq "^GATE PASS" "$T/gate.out"'
# PLAN-GATE-2X D6/G1: the quick above skipped steps. The selector's skip lines are in the log and stderr and follow the GATE line in gate.result (line 1 stays the GATE line: run-preflight/agent-guard read only it); BASE and one ledger row are recorded
chk "quick: skipped steps are named in the log and on stderr (rule 8)" 'grep -q "^gate: skipped lint-c: " shim/out/gate.log && grep -q "^gate: skipped lint-c: " "$T/gate.out"'
chk "quick: gate.result line 1 is the GATE line, the skip lines follow it" 'res | head -n 1 | grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of [0-9]+ steps [0-9]+$" && res | sed -n 2,\$p | grep -q "^gate: skipped lint-c: " && [ "$(res | grep -vc "^gate: skipped")" = 1 ]'
chk "quick: BASE <sha> is in gate.progress" 'grep -Eq "^BASE [0-9a-f]{12}$" shim/out/gate.progress'
chk "quick: one ledger row (10 tab fields, kind quick, 12-char base, ran+skipped = 35)" 'tail -n 1 "$GATE_TIMES_FILE" | awk -F"\t" "NF==10 && \$3==\"quick\" && \$5 ~ /^[0-9a-f]{12}\$/ && \$6+\$7==35 && \$8 ~ /^[0-9]+\$/ {f=1} END{exit !f}"'
STUB_NEEDLES=fail gate --quick; chk "quick: a missing mutant needle fails the gate on mutant-needles and the log names it (AP-281)" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: mutant-needles " "$T/gate.out" && grep -q "^MUTANT NEEDLE MISSING " shim/out/gate/mutant-needles.log && grep -q "^RESULT: FAIL mutant-needles" shim/out/gate/mutant-needles.log'
echo '// wlan' >>shim/wlan_wr_host_test.c; git commit -qam wlan
gate --quick; chk "quick after a change to a file the mutant test reads: wlan-mutants runs and passes" 'grep -q "^END wlan-mutants PASS" shim/out/gate.progress && grep -Eq "^GATE PASS" "$T/gate.out"'
rm shim/out/gate.stamp
gate --quick; chk "quick without a stamp diffs against the host registry (not a full gate)" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of" "$T/gate.out"'
chk "still only the full PASS is registered" '[ "$(ls "$GATE_PASS_DIR" | wc -l)" = 1 ]'
# --- L3: a quick run that selects every step is a full run (stamp + registry written); a stale stamp is reported (AP-97)
# the union of the earlier commits since the stamp (mutant table, wlan test input) plus: a shell tool (shellcheck), a common shim/*.c (the variant builds; a host-test-only shim change no longer selects them) and a patch (xnu-build, stack-apply, boot-markers) and a run-path tool (phone-state, hang-recover, probe-run; T-2712 made the formal steps complete a run only once every other step is selected)
printf '#!/bin/sh\nexit 0\n' >tools/allsteps.sh; : >shim/common.c; : >tools/probe-run-allsteps; mkdir -p xnu/patches soc/google/gs101; : >xnu/patches/9999-allsteps.patch; : >soc/google/gs101/wlan_msgbuf_l22.tsv; git add tools/allsteps.sh shim/common.c tools/probe-run-allsteps xnu/patches/9999-allsteps.patch soc/google/gs101/wlan_msgbuf_l22.tsv; git commit -qm allsteps
echo "$head1" >shim/out/gate.stamp   # the earlier case removed the stamp; a stale one makes quick report it
GATE_STALE_MAX=0 gate --quick; chk "quick on a base more than GATE_STALE_MAX files behind HEAD is refused with the do-instead (A18, AP-97)" 'grep -Eq "^GATE FAIL [0-9a-f]+ quick: stale base \([0-9]+ files behind HEAD, limit 0\); full gate or --allow-stale" "$T/gate.out" && grep -q "^gate: do instead: run the full gate" "$T/gate.out" && [ "$(cat "$T/gate.rc")" = 1 ] && ! grep -q "START build" shim/out/gate.progress'
chk "a refused stale quick does not move the stamp" '[ "$(cat shim/out/gate.stamp)" = "$head1" ]'
GATE_STALE_MAX=0 gate --quick --allow-stale; chk "quick selecting all steps: PASS counts as full (--allow-stale past the limit says STALE BASE, A18)" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: selected all steps, ran full$" "$T/gate.out" && grep -q "^gate: quick selected all steps" "$T/gate.out"'
# shellcheck disable=SC2034  # read by chk through eval
head2=$(git rev-parse HEAD)
chk "quick that ran all steps stamps HEAD and registers it" 'head2=$(git rev-parse HEAD); [ "$(cat shim/out/gate.stamp)" = "$head2" ] && [ -e "$GATE_PASS_DIR/$head2" ]'
chk "--allow-stale logs STALE BASE allowed (A18)" 'grep -Eq "^gate: STALE BASE allowed by --allow-stale: [0-9a-f]+ is [0-9]+ committed files behind HEAD" "$T/gate.out"'
chk "quick reports the stale stamp (AP-97)" 'grep -Eq "^gate: stamp [0-9a-f]{40} is [0-9]+ committed files behind HEAD" "$T/gate.out"'
rm -f shim/out/gate.stamp "$GATE_PASS_DIR/$head2"   # restore the state the later cases expect (no stamp, one registry entry)
# AP-106 / review B1: no stamp and an empty registry = gate_select FULL; the fallback keeps its own tag (agent-guard E5 keys on "ran full")
GATE_PASS_DIR=$T/gp-empty gate --quick; chk "quick with no stamp and no registry: PASS line keeps the fallback tag" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: no usable stamp, ran full$" "$T/gate.out" && ! grep -q "selected all steps" "$T/gate.out"'
rm -rf shim/out/gate.stamp "$T/gp-empty"   # the fallback ran full and stamped HEAD: restore no stamp
STUB_COMMIT=1 gate; chk "HEAD moved during the run: +dirty, no stamp" 'grep -Eq "^GATE PASS [0-9a-f]+\+dirty$" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
git reset -q --hard HEAD; echo y >untracked.txt
gate; chk "untracked file at start: +dirty, no stamp" 'grep -Eq "^GATE PASS [0-9a-f]+\+dirty$" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
rm untracked.txt
chk "+dirty full PASSes were not registered" '[ "$(ls "$GATE_PASS_DIR" | wc -l)" = 1 ]'
STUB_FAIL=build-usb gate; chk "failing step: GATE FAIL, exit 1, no stamp" 'grep -q "^GATE FAIL .* build-usb" "$T/gate.out" && [ "$(cat "$T/gate.rc")" = 1 ] && [ ! -e shim/out/gate.stamp ]'
export GATE_PASS_DIR=$T/gp-fail   # fresh registry: nothing may be registered by the failing gates below
STUB_MUT_FAIL=1 gate; chk "failing wlan-mutants: GATE FAIL wlan-mutants, exit 1, no stamp, not registered" 'grep -q "^GATE FAIL .* wlan-mutants" "$T/gate.out" && [ "$(cat "$T/gate.rc")" = 1 ] && [ ! -e shim/out/gate.stamp ] && [ ! -e "$GATE_PASS_DIR/$(git rev-parse HEAD)" ]'
chk "failing wlan-mutants: gate.result shows the step" 'res | grep -Eq "^GATE FAIL .* wlan-mutants"'
# T-883e: the old bound was wall clock (gate over in <60 s), which a loaded host breaks. The event is "the mutant stub never reached the end of its 120 s sleep" (marker written only after it)
rm -f "$T/mut.done"; STUB_MUT_DONE=$T/mut.done STUB_MUT_SLEEP=120 STUB_FAIL=build-usb gate
chk "lane A failure stops the mutant lane (mutant stub killed before its sleep ended, names the real step)" '[ ! -e "$T/mut.done" ] && grep -q "^GATE FAIL .* build-usb" "$T/gate.out" && ! grep -q "wlan-mutants" "$T/gate.out"'
STUB_KILL_LANE=build gate; chk "a lane that dies without recording its step fails the gate" 'grep -q "^GATE FAIL .* build (did not run)" "$T/gate.out" && [ "$(cat "$T/gate.rc")" = 1 ] && [ ! -e shim/out/gate.stamp ]'
chk "none of the failing gates registered anything" '[ ! -e "$GATE_PASS_DIR" ] || [ -z "$(ls "$GATE_PASS_DIR")" ]'
export GATE_PASS_DIR=$T/gate-pass
STUB_FAIL=build-usb gate --keep-going; chk "keep-going failure: no stamp" '[ ! -e shim/out/gate.stamp ]'
GATE_STEP_TIMEOUT=2 STUB_SLEEP=build-fast gate; chk "timeout: GATE FAIL (timeout), no stamp" 'grep -q "^GATE FAIL .* build-fast (timeout)" "$T/gate.out" && [ ! -e shim/out/gate.stamp ]'
for v in 0 00 abc 1x; do GATE_STEP_TIMEOUT=$v gate; chk "GATE_STEP_TIMEOUT=$v rejected with exit 2" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "GATE_STEP_TIMEOUT" "$T/gate.out"'; done
# --- cold gate (PLAN-GATE-2X K0): GATE_COLD=1 is a full gate with the two cache dirs set (tools/gate --cold does that); the GATE line and the ledger row say cold=1
GATE_COLD=1 gate; chk "GATE_COLD=1 without cache dirs: rejected with exit 2" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "GATE_COLD=1 needs GATE_MUT_CACHE" "$T/gate.out"'
mkdir -p "$T/cold-m" "$T/cold-a"   # the two cache dirs must exist and be empty (review gate-cold-k0-r1 F3)
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-none PIXELIOS_AUDIT_MEMO_DIR=$T/cold-a gate; chk "GATE_COLD=1 with a missing cache dir: rejected" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "must be an existing EMPTY directory" "$T/gate.out"'
mkdir -p "$T/cold-used"; : >"$T/cold-used/x"
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-used PIXELIOS_AUDIT_MEMO_DIR=$T/cold-a gate; chk "GATE_COLD=1 with a used (non-empty) cache dir: rejected" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "must be an existing EMPTY directory" "$T/gate.out"'
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-m PIXELIOS_AUDIT_MEMO_DIR=$T/cold-m gate; chk "GATE_COLD=1 with both vars on the same dir: rejected" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "are the same directory" "$T/gate.out"'
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-m PIXELIOS_AUDIT_MEMO_DIR=0 gate; chk "GATE_COLD=1 with the memo disabled (0): rejected" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "GATE_COLD=1 needs PIXELIOS_AUDIT_MEMO_DIR" "$T/gate.out"'
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-m PIXELIOS_AUDIT_MEMO_DIR=$T/cold-a gate --quick; chk "GATE_COLD=1 --quick: rejected" '[ "$(cat "$T/gate.rc")" = 2 ] && grep -q "GATE_COLD=1 is a full gate" "$T/gate.out"'
GATE_COLD=1 GATE_MUT_CACHE=$T/cold-m PIXELIOS_AUDIT_MEMO_DIR=$T/cold-a gate
chk "cold full PASS: ' cold=1' ends the GATE line" 'grep -Eq "^GATE PASS [0-9a-f]+ cold=1$" "$T/gate.out" && head -1 shim/out/gate.result | grep -Eq "^GATE PASS [0-9a-f]+ cold=1 [0-9]+$"'
chk "cold full PASS: the ledger row ends with cold=1 (10 columns)" 'tail -n 1 "$GATE_TIMES_FILE" | awk -F"\t" "{exit !(NF==10 && \$10==1)}"'
chk "cold full PASS: gate.log names the two cache dirs" 'grep -q "gate: COLD run: GATE_MUT_CACHE=$T/cold-m PIXELIOS_AUDIT_MEMO_DIR=$T/cold-a" shim/out/gate.log'
# review gate-cold-k0-r1 F2: a cold run is judged against the cold total only; a step over its WARM per-step budget (wlan-mutants budget 1 s, stub sleeps 2 s) must not show in slow= on a cold line
rm -rf "$T/stC" "$T/stC-m" "$T/stC-a"; mkdir -p "$T/stC-m" "$T/stC-a"; cp -a "$T/st" "$T/stC"; ( cd "$T/stC" && python3 - <<'PY' && GATE_COLD=1 GATE_MUT_CACHE=$T/stC-m PIXELIOS_AUDIT_MEMO_DIR=$T/stC-a STUB_MUT_SLEEP=2 bash tools/check-all.sh >"$T/stC.out" 2>&1
import json
m = json.load(open("tools/gate-manifest.json"))
for r in m["steps"]:
    if r["step"] == "wlan-mutants":
        r["budget_s"] = 1
json.dump(m, open("tools/gate-manifest.json", "w"), indent=1)
PY
)
chk "cold run: a step over its warm budget is NOT in slow= (cold is judged by the total only)" 'grep -Eq "^GATE PASS [0-9a-f]+(\+dirty)? cold=1$" "$T/stC.out" && ! grep -q "slow=" "$T/stC.out"'
gate; chk "a warm gate after it: no cold= on the line, ledger column 0" '! grep -q "cold=" "$T/gate.out" && tail -n 1 "$GATE_TIMES_FILE" | awk -F"\t" "{exit !(NF==10 && \$10==0)}"'
gate; chk "stamp written again by a clean full PASS" '[ "$(cat shim/out/gate.stamp)" = "$(git rev-parse HEAD)" ]'
# --- K3/K5 audit pool sizing (review gate-cold-k3 r1 F1/F2): the formula (B-2)/J clamped to 1..4, the gate.log line, an explicit value (clamped to max(1, B-2)), and `build` in the first wave
pool_case() { # BUDGET EXPECTED-JOBS [env...]: a full gate; gate.log says GATE_AUDIT_JOBS=EXPECTED
	local b=$1 want=$2; shift 2
	rm -f shim/out/gate.log; env GATE_CPU_BUDGET="$b" "$@" bash tools/check-all.sh >"$T/gate.out" 2>&1; echo $? >"$T/gate.rc"
	chk "audit pool budget=$b $*: gate PASS and gate.log GATE_AUDIT_JOBS=$want" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/gate.out" && grep -q "^gate: audit pool GATE_AUDIT_JOBS='"$want"' (" shim/out/gate.log'
}
pool_case 4 2
chk "audit pool log line (budget 4): derived, J*A and the honest scope" 'grep -q "^gate: audit pool GATE_AUDIT_JOBS=2 (derived; budget=4, A.jobs=1: bounds the pool workers only, J\*A=2; parents, builds and lanes B/C are extra)$" shim/out/gate.log'
pool_case 16 2
chk "audit pool log line (budget 16): J*A=12" 'grep -q "A.jobs=6: bounds the pool workers only, J\*A=12;" shim/out/gate.log'
pool_case 64 2
pool_case 16 4 GATE_JOBS=1            # (16-2)/1 = 14 -> capped at 4
pool_case 16 1 GATE_JOBS=14           # (16-2)/14 = 1
pool_case 16 3 GATE_AUDIT_JOBS=3      # explicit wins
chk "explicit GATE_AUDIT_JOBS=3 at budget 16: marked env, no clamp line" 'grep -q "GATE_AUDIT_JOBS=3 (env; budget=16" shim/out/gate.log && ! grep -q "clamped" "$T/gate.out"'
pool_case 4 2 GATE_AUDIT_JOBS=9       # explicit 9 > max(1, 4-2)=2 -> clamped, loudly
chk "explicit GATE_AUDIT_JOBS=9 at budget 4: clamp line on stderr and in gate.log, log says env, clamped from 9" 'grep -q "^gate: GATE_AUDIT_JOBS=9 is above max(1, budget-2)=2 (budget=4): clamped to 2$" "$T/gate.out" && grep -q "^gate: GATE_AUDIT_JOBS=9 is above" shim/out/gate.log && grep -q "(env, clamped from 9;" shim/out/gate.log'
pool_case 2 1 GATE_AUDIT_JOBS=5       # B-2 = 0 -> max(1, 0) = 1
rm -f shim/out/gate.log; GATE_AUDIT_JOBS=abc GATE_CPU_BUDGET=16 bash tools/check-all.sh >"$T/gate.out" 2>&1
chk "a non-numeric GATE_AUDIT_JOBS is not clamped by the gate (tools/audit_pool.py rejects it)" 'grep -q "GATE_AUDIT_JOBS=abc (env; budget=16" shim/out/gate.log && ! grep -q "clamped" "$T/gate.out"'
STUB_BUILD_PEER=1 gate; chk "build runs in the first wave: build-usb started while build was still running (no serial canary)" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/gate.out" && grep -q "^END build PASS" shim/out/gate.progress'
chk "default host-lock wait is 1800 s" 'grep -q "GATE_HOST_LOCK_WAIT:-1800" tools/check-all.sh && ! grep -q "10800" tools/check-all.sh'
gate; chk "steps run at low priority (nice +10 over their lane's, or at the max 19)" '[ "$(cat "$STUB_PIDS/nice")" -ge 19 ] || [ $(( $(cat "$STUB_PIDS/nice") - $(cat "$STUB_PIDS/nice_lane") )) -ge 10 ]'
# r42-gate-r1 F3: a PASS that waived the inputs step is not a clean full gate: no stamp, no registry entry, and the importable "(not registered" text says why (check-all.sh:236-242)
rm -f shim/out/gate.stamp; GATE_PASS_DIR=$T/gp-waived GATE_NO_INPUTS="reason w" gate
chk "full gate with GATE_NO_INPUTS: PASS says inputs waived and not registered" 'grep -Eq "^GATE PASS [0-9a-f]+ inputs waived: GATE_NO_INPUTS=reason w \(not registered: inputs waived\)$" "$T/gate.out"'
chk "full gate with GATE_NO_INPUTS: no stamp and no registry entry" '[ ! -e shim/out/gate.stamp ] && { [ ! -e "$T/gp-waived" ] || [ -z "$(ls "$T/gp-waived")" ]; }'
chk "full gate without GATE_NO_INPUTS: still stamps and registers (the waiver rule is not blanket)" 'gate; [ "$(cat shim/out/gate.stamp)" = "$(git rev-parse HEAD)" ] && ! grep -q "inputs waived" "$T/gate.out"'
chk "owner file removed after the run" '[ ! -e shim/out/.gate.owner ]'

# --- lock wait: WAITING replaces the old PASS, the holder is named, the gate then runs
( flock 8; exec sleep 60 ) 8>shim/out/.gate.lock & holder=$!   # exec: the subshell pid is the sleep that holds the lock
waitfor '! flock -n shim/out/.gate.lock true'
bash tools/check-all.sh >"$T/wait.out" 2>&1 & wg=$!
waitfor 'res | grep -q "^WAITING"'; chk "lock wait: gate.result is WAITING (old PASS gone)" 'res | grep -Eq "^WAITING $wg [0-9]+$"'
chk "lock wait: unknown holder is named unknown" 'grep -q "held by unknown" "$T/wait.out"'
kill "$holder"; waitfor '! kill -0 '"$wg"' 2>/dev/null'
chk "lock wait: gate runs after the holder is gone" 'grep -Eq "^GATE PASS" "$T/wait.out"'
# --- host lock: two gates sharing GATE_HOST_LOCK run serially; GATE_HOST_LOCK=0 does not wait
HL=$T/host.lock
( flock 8; exec sleep 60 ) 8>>"$HL" & holder=$!
waitfor '! flock -n "$HL" true'
cd "$T/st" || exit 2
GATE_HOST_LOCK=$HL bash tools/check-all.sh >"$T/hl.out" 2>&1 & hg=$!
waitfor 'grep -q "gate: waiting for host gate lock" "$T/hl.out"'; chk "host lock: waiter prints the waiting line" 'grep -q "^gate: waiting for host gate lock held by" "$T/hl.out"'
chk "host lock: waiter has not finished a gate yet" '! grep -q "^GATE" "$T/hl.out"'
rm -rf "$T/st0"; cp -a "$T/st" "$T/st0"; ( cd "$T/st0" && GATE_HOST_LOCK=0 bash tools/check-all.sh --quick >"$T/hl0.out" 2>&1 ); chk "host lock: GATE_HOST_LOCK=0 does not wait" '! grep -q "waiting for host" "$T/hl0.out" && grep -q "^GATE" "$T/hl0.out"'
kill "$holder"; waitfor '! kill -0 '"$hg"' 2>/dev/null'
chk "host lock: waiter runs after the holder ends" 'grep -Eq "^GATE PASS" "$T/hl.out"'
cd "$T" || exit 2
# --- host lock is released when lanes A+B are done, while the slow lane C (wlan mutants) still runs
rm -rf "$T/st2"; cp -a "$T/st" "$T/st2"; cd "$T/st2" || exit 2; HL2=$T/host2.lock
rm -f shim/out/gate.progress shim/out/gate.log   # cp -a carries the earlier runs' END lines: the check below must see only this run
GATE_HOST_LOCK=$HL2 STUB_MUT_WAITFILE=$T/mut.go STUB_MUT_ENV=$T/mutenv bash tools/check-all.sh >"$T/hl2.out" 2>&1 & hg=$!
for _ in $(seq 2400); do grep -q "host-lock released" shim/out/gate.progress && break; sleep 0.1; done   # up to 240 s: a loaded host runs the stub steps slowly
chk "host lock released after A+B while lane C runs: lock free and C not ended" 'flock -n "$HL2" true && ! grep -q "^END wlan-mutants" shim/out/gate.progress && kill -0 '"$hg"
# K1/K4b wiring (review gate-cold-k14 r1 F1/F3): lane C's pool and the unit shards grow on files check-all.sh writes; a deleted line would otherwise only show in a 1.5 h cold run
waitfor '[ -s "$T/mutenv" ]'
chk "K1: .lanes-ab-done exists once A+B are done (lane C still running)" '[ -e shim/out/gate/.lanes-ab-done ]'
chk "K4b: .lanes-a-done exists once lane A is done" '[ -e shim/out/gate/.lanes-a-done ]'
mj=$(( $(nproc) * 3 / 8 )); [ "$mj" -ge 1 ] || mj=1
chk "lane C inherits the derived pool size, the SRC markers and the signal path" 'grep -qx "GATE_MUT_JOBS=$mj GATE_MUT_JOBS_SRC=derived GATE_UNIT_SHARDS_SRC=derived GATE_LANES_DONE=shim/out/gate/.lanes-ab-done GATE_LANES_A_DONE=shim/out/gate/.lanes-a-done" "$T/mutenv"'
chk "the unit step sees lane C's base jobs in its cap (global cap, F5)" 'grep -q "gate: unit shard cap .* lane C mutant jobs $mj)" shim/out/gate/unit.log'
touch "$T/mut.go"
wait "$hg"; chk "host lock release: gate still PASSes" 'grep -Eq "^GATE PASS" "$T/hl2.out"'
chk "F3: both signal files are removed when the gate ends (a standalone run in this worktree must not grow at once)" '[ ! -e shim/out/gate/.lanes-ab-done ] && [ ! -e shim/out/gate/.lanes-a-done ]'
cd "$T" || exit 2
# lane A FAILS with --keep-going (lane C keeps running): the A exit trap still writes .lanes-a-done, .lanes-ab-done still appears, and an explicit GATE_MUT_JOBS is marked env
rm -rf "$T/st3"; cp -a "$T/st" "$T/st3"; cd "$T/st3" || exit 2
rm -f shim/out/gate.progress shim/out/gate.log
GATE_MUT_JOBS=3 STUB_FAIL=build-fast STUB_MUT_WAITFILE=$T/mut3.go STUB_MUT_ENV=$T/mutenv3 bash tools/check-all.sh --keep-going >"$T/kg.out" 2>&1 & hg=$!
waitfor '[ -e shim/out/gate/.lanes-ab-done ] && [ -s "$T/mutenv3" ]'
chk "lane A fails (--keep-going): .lanes-ab-done is still written" '[ -e shim/out/gate/.lanes-ab-done ]'
chk "lane A fails: the A exit trap still writes .lanes-a-done" '[ -e shim/out/gate/.lanes-a-done ]'
chk "explicit GATE_MUT_JOBS=3 is exported with SRC=env (never grows)" 'grep -q "^GATE_MUT_JOBS=3 GATE_MUT_JOBS_SRC=env GATE_UNIT_SHARDS_SRC=derived " "$T/mutenv3"'
touch "$T/mut3.go"
wait "$hg"; chk "lane A fail: the gate FAILs" 'grep -Eq "^GATE FAIL" "$T/kg.out"'
chk "lane A fail: signals removed at the end" '[ ! -e shim/out/gate/.lanes-ab-done ] && [ ! -e shim/out/gate/.lanes-a-done ]'
cd "$T" || exit 2

# S4 / review gate-cold-s4-r1 F1, F8: a quick gate whose unit step ran a subset says so on the GATE line; the formal (full) gate never selects, even with a stray GATE_UNIT_ONLY in its environment
cd "$T" || exit 2
export STUB_PIDS=$T/pt.pids; mkrepo "$T/pt"
gate; chk "partial-unit: base full PASS" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/gate.out"'
rm -rf "$T/ptf"; cp -a "$T/pt" "$T/ptf"
printf 'import unittest\nclass T(unittest.TestCase):\n    def test_pt(self): pass\n' >tools/test_pt.py; git add tools/test_pt.py; git commit -qm pt
gate --quick; chk "partial-unit: a quick gate with a one-file test selection: GATE PASS tagged unit-partial=1/2, no parentheses" 'grep -Eq "^GATE PASS [0-9a-f]+ quick: ran [0-9]+ of [0-9]+ steps unit-partial=1/2$" "$T/gate.out" && [ "$(cat shim/out/gate/unit-only.txt)" = test_pt.py ] && grep -Eq "^RESULT: PASS 1 tests in 1 files, .* partial=1/2 drift=[0-9]+ gaps=[0-9]+$" shim/out/gate/unit.log'
cd "$T/ptf" || exit 2; export STUB_PIDS=$T/ptf.pids; mkdir -p "$STUB_PIDS"
printf '1\n' >"$T/stray-only.txt"
GATE_UNIT_ONLY=$T/stray-only.txt bash tools/check-all.sh >"$T/ptf.out" 2>&1
chk "partial-unit: a full gate with a stray GATE_UNIT_ONLY in its environment runs every test file and carries no unit-partial tag" 'grep -Eq "^GATE PASS [0-9a-f]+$" "$T/ptf.out" && grep -q "^RESULT: PASS 2 tests in 1 files" shim/out/gate/unit.log && ! grep -q "partial=\|ignoring inherited" shim/out/gate/unit.log'
cd "$T" || exit 2

echo "test_check_all_stub: $([ $fail = 0 ] && echo PASS || echo FAIL)"; exit $fail
