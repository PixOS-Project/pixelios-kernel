#!/bin/bash
# One verification gate: build both bluejay variants, host tests, boot-image safety check, unit tests, repo validation.
# Run from any checkout; full output goes to shim/out/gate.log (with a per-step timing table), stdout is ONE line:
#   "GATE PASS|FAIL <short-hash> [failed step]"                       (full gate: what must pass before a push)
#   "GATE PASS|FAIL <short-hash> quick: ran N of M steps | <failed step>"   (--quick: only the steps the diff needs)
# --quick diffs against the nearest commit that passed a FULL gate: shim/out/gate.stamp or the host registry ${GATE_PASS_DIR:-~/.cache/pixelios/gate-pass}/<sha> (both written only by a clean full PASS); none usable = full gate.
# Two lanes run in parallel: A = shim builds + host-tests + boot-safety, B = unit, rootless, validate. Lane A runs `build` first (cold: warms the audit
# memo and the host-test stamp), then the other build steps at most GATE_JOBS / -j N (default from GATE_CPU_BUDGET, see below) at a time. Every build writes its own dir shim/out/gate/<step>-<i>/
# (build.sh --out), so the gate never touches shim/out/*.img and takes no .build.lock (only shim/out/.gate.lock: one gate per worktree).
# Live state: shim/out/gate.progress (START/END per step), gate.result (RUNNING <pid> <epoch>, then the GATE line + epoch; atomic), shim/out/gate/<step>.log.
# A failing step stops the rest at their next step boundary (--keep-going: only its own lane). Per-step timeout: GATE_STEP_TIMEOUT s (default 900 build*, 300 others). Exit 0 PASS, 1 FAIL, 2 usage.
# --no-memo (or PIXELIOS_AUDIT_NO_MEMO=1) disables the audit self-test memo (tools/audit_memo.py) for this run.
# CPU budget (AP-186, AP-193): lanes A/B/C used to each assume the whole host (J=8 builds + 4 unit shards + 12 mutant threads = load1 25-30 on 16 cores).
# GATE_CPU_BUDGET=B (positive integer, default `nproc`) sizes them together, only for a lane whose own env is unset: J=max(1,B*3/8), GATE_UNIT_SHARDS=max(1,B/4),
# GATE_MUT_JOBS=max(1,B*3/8) (B=16: 6/4/6). Explicit GATE_JOBS / -j N / GATE_UNIT_SHARDS / GATE_MUT_JOBS win. A `GATE CPU: ...` line goes to gate.log, gate.progress and stderr (stdout stays one line).
# Growth (PLAN-GATE-2X K1/K4b/K5, only for a DERIVED value, GATE_*_SRC=derived): when lane A ends the unit shards grow, when A+B end the lane C mutant pool grows by 8. K5 bounds the POOL WORKERS
# (unit shards + mutant jobs) at nproc-2 TOGETHER: the shards grow to min(8, nproc-2 - GATE_MUT_JOBS), the mutant pool to min(base+8, nproc-2) when no shard runs any more. It does not count the
# parent shell/python of each step, lane A's jobs while they still run, or the children a pool worker starts. The signal files (.lanes-a-done, .lanes-ab-done) are removed at gate start and in its EXIT trap.
# GATE_CPU_PRINT_ONLY=1 (TEST HOOK, tools/test_gate_cpu_budget.py): print that line on stdout and exit 0 before any lock; -j is not parsed there (use GATE_JOBS).
# Reviewers/leads: trust a PASS line whose hash equals `git rev-parse --short HEAD` instead of re-running.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
git rev-parse --short HEAD >/dev/null 2>&1 || { echo "GATE ERROR not a git checkout"; exit 2; }
# TP-042: one heavy job per machine. GATE_HOST_LOCK=0 (offload-host container, host tests) opts out; HEAVY_JOB_HELD set = already inside tools/heavy-job (no re-wrap, no deadlock).
# heavy-job lines go to stderr here (HEAVY_JOB_QUIET) so the one-line stdout contract holds; its wait is bounded by HEAVY_JOB_WAIT (default 1800).
if [ "${GATE_HOST_LOCK:-}" != 0 ]; then
	if [ -n "${HEAVY_JOB_HELD:-}" ]; then echo "heavy-job: nested in $HEAVY_JOB_HELD, no second lock" >&2
	else HEAVY_JOB_QUIET=1 exec tools/heavy-job run --class gate --label "${GATE_LABEL:-gate-$(basename "$PWD")}" --wait "${HEAVY_JOB_WAIT:-1800}" -- bash "$0" "$@"; fi
fi
# gate_cpu: sets B/J/S/M and gate_cpu_line; invalid GATE_CPU_BUDGET = GATE ERROR exit 2. J (GATE_JOBS) is validated by the caller's env or -j.
gate_cpu() {
	local bsrc=env js ss ms
	case ${GATE_CPU_BUDGET-x} in ''|*[!0-9]*|0) [ "${GATE_CPU_BUDGET+set}" = set ] && { echo "GATE ERROR GATE_CPU_BUDGET must be a positive integer"; exit 2; } ;; esac
	if [ -z "${GATE_CPU_BUDGET:-}" ]; then B=$(nproc 2>/dev/null) || B=; bsrc=nproc; else B=$GATE_CPU_BUDGET; fi
	case $B in ''|*[!0-9]*|0) echo "GATE ERROR cannot determine the CPU budget (nproc=$B); set GATE_CPU_BUDGET"; exit 2 ;; esac
	B=$((10#$B))
	case ${GATE_JOBS:-1} in ''|*[!0-9]*|0) echo "GATE ERROR GATE_JOBS must be a positive integer"; exit 2 ;; esac
	if [ -n "${GATE_JOBS:-}" ]; then J=$GATE_JOBS; js="env"; else J=$((B*3/8)); [ "$J" -ge 1 ] || J=1; js="derived"; fi
	if [ -n "${GATE_UNIT_SHARDS:-}" ]; then S=$GATE_UNIT_SHARDS; ss="env"; else S=$((B/4)); [ "$S" -ge 1 ] || S=1; ss="derived"; fi
	if [ -n "${GATE_MUT_JOBS:-}" ]; then M=$GATE_MUT_JOBS; ms="env"; else M=$((B*3/8)); [ "$M" -ge 1 ] || M=1; ms="derived"; fi
	export GATE_UNIT_SHARDS=$S GATE_MUT_JOBS=$M GATE_UNIT_SHARDS_SRC=$ss GATE_MUT_JOBS_SRC=$ms   # *_SRC=env|derived: only a derived value may grow (K1/K4b: tools/wlan_mutant_pool.py JobGate, tools/gate-unit-shards)
	lanes_ab_done=${GATE_LANES_DONE:-shim/out/gate/.lanes-ab-done}; lanes_a_done=${GATE_LANES_A_DONE:-shim/out/gate/.lanes-a-done}; export GATE_LANES_DONE=$lanes_ab_done GATE_LANES_A_DONE=$lanes_a_done
	gate_cpu_line="GATE CPU: budget=$B ($bsrc) A.jobs=$J ($js) B.shards=$S ($ss) C.mut_jobs=$M ($ms)"
}
if [ "${GATE_CPU_PRINT_ONLY:-}" = 1 ]; then gate_cpu; echo "TEST HOOK GATE_CPU_PRINT_ONLY=1: no gate run"; echo "$gate_cpu_line"; exit 0; fi
quick=; keep=; allow_stale=
while [ $# -gt 0 ]; do case $1 in --quick) quick=1 ;; --allow-stale) allow_stale=1 ;; --keep-going) keep=1 ;; --no-memo) export PIXELIOS_AUDIT_NO_MEMO=1 ;; -j) shift; GATE_JOBS=${1:-x} ;;
	*) echo "GATE ERROR usage: check-all.sh [--quick [--allow-stale]] [--keep-going] [--no-memo] [-j N]"; exit 2 ;; esac; shift; done
if [ "${GATE_COLD:-}" = 1 ]; then   # a cold gate is a full gate with empty caches (tools/gate --cold sets both dirs); anything else would be a warm run tagged cold in the ledger
	[ -z "$quick" ] || { echo "GATE ERROR GATE_COLD=1 is a full gate, not --quick"; exit 2; }
	for v in GATE_MUT_CACHE PIXELIOS_AUDIT_MEMO_DIR; do case ${!v:-} in ''|0) echo "GATE ERROR GATE_COLD=1 needs $v=<empty dir> (tools/gate --cold)"; exit 2 ;; esac; done
	# review gate-cold-k0-r1 F3: the two dirs must exist, be empty at start and differ, or a warm run would be tagged cold in the ledger
	for v in GATE_MUT_CACHE PIXELIOS_AUDIT_MEMO_DIR; do [ -d "${!v}" ] && [ -z "$(ls -A -- "${!v}")" ] || { echo "GATE ERROR GATE_COLD=1: $v=${!v} must be an existing EMPTY directory (tools/gate --cold)"; exit 2; }; done
	[ "$GATE_MUT_CACHE" != "$PIXELIOS_AUDIT_MEMO_DIR" ] || { echo "GATE ERROR GATE_COLD=1: GATE_MUT_CACHE and PIXELIOS_AUDIT_MEMO_DIR are the same directory"; exit 2; }
fi
case ${GATE_STEP_TIMEOUT:-1} in *[!0-9]*) echo "GATE ERROR GATE_STEP_TIMEOUT must be positive seconds"; exit 2 ;; esac
[ $((10#${GATE_STEP_TIMEOUT:-1})) -gt 0 ] || { echo "GATE ERROR GATE_STEP_TIMEOUT must be positive seconds (0 disables timeout(1))"; exit 2; }
case ${GATE_JOBS:-1} in ''|*[!0-9]*|0) echo "GATE ERROR GATE_JOBS must be a positive integer"; exit 2 ;; esac
gate_cpu
mkdir -p shim/out
log=shim/out/gate.log; stamp=shim/out/gate.stamp; prog=shim/out/gate.progress; res=shim/out/gate.result; owner=shim/out/.gate.owner
# one gate per worktree (it owns shim/out/gate, gate.log, gate.result). Not .build.lock: xrun/probe-run builds into shim/out and the gate no longer does.
# tools-sync also refuses a pull while this lock is held (the gate would see mixed tool versions).
# gate.result: temp + rename, so a watcher never reads a half line. WAITING (for the lock), RUNNING while the gate runs, then the GATE line + epoch.
putres() { printf '%s\n' "$1" >"$res.$$" && mv -f "$res.$$" "$res"; }
# finish LINE RC. Once a gate has run (g0 set by `putres RUNNING`): slow=<step:obs/budget,..> (tools/gate-summary --slow, budgets in tools/gate-manifest.json; a breach is only reported, never a failure) rides on the GATE line,
# the selector's `gate: skipped STEP: ...` lines ($skips, --quick only) follow it in gate.result (readers take line 1 only: tools/run-preflight:386 readline, tools/agent-guard:689 first tokens), and one row goes to logs/gate-times.tsv (tools/gate-summary --append; best effort, loud on stderr)
finish() {
	local l=$1 sl='' el='' cold=
	# GATE_COLD=1 (tools/gate --cold: fresh worktree, empty mutant cache and audit memo): ` cold=1` on the GATE line, read by tools/gate-summary (RESULT line, ledger column `cold`, cold total budget)
	[ "${GATE_COLD:-}" = 1 ] && { cold=--cold; l="$l cold=1"; }
	if [ -n "${g0:-}" ]; then
		el=$((SECONDS-g0))
		sl=$(python3 tools/gate-summary --slow --progress "$prog" --kind "$([ -n "$quick" ] && echo quick || echo formal)" --elapsed "$el" $cold) || { echo "gate: tools/gate-summary --slow failed: no slow= on the GATE line" >&2; sl=; }
		l="$l${sl:+ slow=$sl}"
	fi
	putres "$l $(date +%s)${skips:+$'\n'$skips}"
	[ -n "${g0:-}" ] && { python3 tools/gate-summary --append --progress "$prog" --line "$l" --elapsed "$el" --label "${GATE_LABEL:-gate-$(basename "$PWD")}" --host "$(hostname -s)" >/dev/null || echo "gate: no row written to logs/gate-times.tsv (tools/gate-summary --append failed)" >&2; }
	echo "$l"; exit "$2"
}
# one gate per HOST (the heavy steps of 3 worktrees at once overload it): taken before the per-worktree lock, held by fd 8 until lanes A and B have both finished (then released with `flock -u 8` while lane C, the wlan mutants, may still run; no PID file).
# The holder's worktree is written into the lock file (opened >>, so a waiter never truncates it). GATE_HOST_LOCK=0 disables; wait is outside every step timeout.
hl=${GATE_HOST_LOCK:-/tmp/pixos-gate-host.lock}
if [ "$hl" != 0 ]; then
	exec 8>>"$hl"
	if ! flock -n 8; then
		w="gate: waiting for host gate lock held by $(cat "$hl" 2>/dev/null || echo unknown)"; echo "$w" >&2; echo "$w" >>"$prog"
		putres "WAITING $$ $(date +%s)"
		trap 'kill "$fp" 2>/dev/null; finish "GATE FAIL interrupted while waiting for the host gate lock" 1' INT TERM HUP
		flock -w "${GATE_HOST_LOCK_WAIT:-1800}" 8 & fp=$!
		wait "$fp" || finish "GATE FAIL host-lock-timeout (another gate on this host holds $hl for ${GATE_HOST_LOCK_WAIT:-1800} s)" 1
		trap - INT TERM HUP
	fi
	pwd >"$hl"
fi
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
tmp=$(mktemp -d); trap 'rm -rf "$tmp"; rm -f "$owner" "$lanes_ab_done" "$lanes_a_done"' EXIT   # the K1/K4b signals die with the gate: a stale file would make a later standalone run grow at once (review gate-cold-k14 r1 F3)
export TMPDIR="$tmp"   # every step's mkdtemp/TemporaryDirectory lands in the gate's own dir and dies with this EXIT trap: killed steps leaked ~21k dirs (3.4 GB) into the /tmp tmpfs by 2026-11-10
: > "$log"; : > "$prog"; rm -rf shim/out/gate; mkdir -p shim/out/gate
rm -f "$lanes_ab_done" "$lanes_a_done"   # stale signals of an earlier gate (PLAN-GATE-2X K1/K4b: the cold gate grows lane C jobs / unit shards when these appear)
echo "$gate_cpu_line" | tee -a "$log" "$prog" >&2
[ "${GATE_COLD:-}" = 1 ] && echo "gate: COLD run: GATE_MUT_CACHE=$GATE_MUT_CACHE PIXELIOS_AUDIT_MEMO_DIR=$PIXELIOS_AUDIT_MEMO_DIR (both empty at start, tools/gate --cold)" | tee -a "$log" "$prog" >&2
full=$(git rev-parse HEAD)
h=$(git rev-parse --short HEAD)$([ -n "$(git status --porcelain)" ] && echo "+dirty")
# Ctrl-C / kill: steps run under `timeout`, i.e. in their own process groups, so kill them (and the lane jobs) explicitly. Lanes also
# stop at their next step boundary when this shell is gone (step()). The steps' pid files are $tmp/NAME.pid (the timeout pid = its pgid).
kill_steps() { for f in "$tmp"/*.pid; do [ -e "$f" ] || continue; p=$(cat "$f" 2>/dev/null); [ -n "$p" ] && kill -KILL -- "-$p" "$p" 2>/dev/null; done; return 0; }
onint() {
	trap '' INT TERM HUP; : >"$tmp/interrupted"
	[ -n "${pa:-}" ] && kill "$pa" 2>/dev/null
	[ -n "${pc:-}" ] && kill "$pc" 2>/dev/null
	kill_steps; sleep 1; kill_steps   # the 2nd sweep catches a step that was starting during the first
	finish "GATE FAIL $h interrupted" 1
}
trap onint INT TERM HUP
putres "RUNNING $$ $(date +%s)"; g0=$SECONDS; skips=
ALL="build build-fast build-usb build-wlan build-gpu build-gpupower build-gpuclk build-gpurails build-gpurailon build-gpujob build-touch build-udump host-tests boot-safety unit wlan-tbl wlan-mutants phone-state hang-recover probe-run phone-converge rootless public-split validate rules public-guard shellcheck lint-c xnu-build stack-apply mutant-needles boot-markers lint-silent consumed inputs"
want=$ALL; fullnote=
if [ -n "$quick" ]; then
	# stderr = the selector's loud lines (`gate: skipped STEP: why` per skipped step, `gate: FULL, unresolved: FILE`): into the log, to stderr, and (via $skips) after the GATE line in gate.result
	want=$(python3 tools/gate_select.py "$stamp" 2>"$tmp/select.err"); rc=$?; tee -a "$log" <"$tmp/select.err" >&2
	[ $rc = 0 ] || finish "GATE FAIL $h gate_select (see $log)" 1
	skips=$(grep -E '^gate: (skipped|FULL|MANIFEST)' "$tmp/select.err")
	# the base commit of this quick run (the selector's best_base), for the ledger row; a full run has none
	gbase=-; [ "$want" = FULL ] || gbase=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import gate_select; print((gate_select.best_base(sys.argv[1]) or "-")[:12])' "$stamp" 2>/dev/null || echo -)
	echo "BASE ${gbase:--}" >>"$prog"
	if [ "$want" = FULL ]; then want=$ALL; quick=; fullnote=" quick: no usable stamp, ran full"
	else
		# AP-97: say how stale the stamp is (loud, not silent); the registry may still give a nearer base (AP-106: this branch only, the FULL fallback keeps its tag)
		[ -r "$stamp" ] && { sb=$(cat "$stamp"); [ "$sb" != "$full" ] && git cat-file -e "$sb^{commit}" 2>/dev/null && echo "gate: stamp $sb is $(git diff --name-only --no-renames "$sb" HEAD | wc -l) committed files behind HEAD (dirty files not counted; the registry may give a nearer base)"; }
		# A18 (AP-97): a quick run whose base (the selector's nearest full-PASS commit, else the stamp) is more than GATE_STALE_MAX committed files behind HEAD is refused: it would skip steps on a stale diff and miss a gate fix only trunk has. Escape, loud: --allow-stale
		sbase=${gbase:--}; [ "$sbase" = - ] && sbase=$(cat "$stamp" 2>/dev/null); stale_n=
		[ -n "$sbase" ] && git cat-file -e "$sbase^{commit}" 2>/dev/null && stale_n=$(git diff --name-only --no-renames "$sbase" HEAD | wc -l)
		if [ -n "$stale_n" ] && [ "$stale_n" -gt "${GATE_STALE_MAX:-400}" ]; then
			if [ -n "$allow_stale" ]; then echo "gate: STALE BASE allowed by --allow-stale: $sbase is $stale_n committed files behind HEAD (limit ${GATE_STALE_MAX:-400}); the diff-based step selection may miss a gate fix only trunk has (AP-97)"
			else echo "gate: STALE BASE: $sbase is $stale_n committed files behind HEAD (limit ${GATE_STALE_MAX:-400}); --quick would select steps from a stale diff (AP-97)" >&2
				echo "gate: do instead: run the full gate (no --quick), merge trunk into this branch first, or pass --allow-stale to accept the risk" >&2
				finish "GATE FAIL $h quick: stale base ($stale_n files behind HEAD, limit ${GATE_STALE_MAX:-400}); full gate or --allow-stale" 1; fi; fi
		# a quick run that selected every step is a full run: clear quick so the stamp and registry steps below run; "ran full" is the tag tools/agent-guard E5 keys on
		[ "$want" = "$ALL" ] && { quick=; fullnote=" quick: selected all steps, ran full"; echo "gate: quick selected all steps, counts as a full run"; }
	fi
fi
# S4 (PLAN-GATE-2X): a quick gate's unit step runs the test files gate_select.py listed in unit-only.txt; a full run (and the formal gate) never sets GATE_UNIT_ONLY
export GATE_AFFECTED_CHECKED=1   # a running gate (also one started by gate-request, not by tools/gate --run) never re-runs the pre-check inside its unit tests (the remote gate failed test_gate_summary on this, 2026-11-02)
unset GATE_UNIT_ONLY; [ -n "$quick" ] && [ -s shim/out/gate/unit-only.txt ] && export GATE_UNIT_ONLY=shim/out/gate/unit-only.txt
# step LANE NAME CMD...: skipped unless selected; a failure marks the lane and skips its remaining steps (and, unless --keep-going,
# everyone's: they stop at their next step boundary). Each step runs under timeout -k 10 (build-wlan 1500 s, other build* 900 s, unit 2700 s (without the wlan mutants), wlan-mutants 5400 s (own step, lane C from t=0: 10-90 min with runs C-F2; research 68 D4/D6), others 300 s, env GATE_STEP_TIMEOUT)
# and logs to shim/out/gate/NAME.log; steps of one lane may run in parallel (par), so every step writes only its own files.
# D5 (research 68): gate steps run at low CPU and IO priority, so a gate on a shared host does not starve the other streams' runs
lowpri=(nice -n 10); ionice -c2 -n7 true 2>/dev/null && lowpri+=(ionice -c2 -n7)
step() {
	l=$1; n=$2; shift 2
	case " $want " in *" $n "*) ;; *) return 0 ;; esac
	# the gate was interrupted or is gone: never start another step (exits the lane subshell)
	{ [ -d "$tmp" ] && [ ! -e "$tmp/interrupted" ] && kill -0 $$ 2>/dev/null; } || exit 1
	if [ -n "$keep" ]; then [ -e "$tmp/$l.lane" ] && return 0; else compgen -G "$tmp/*.fail" >/dev/null && return 0; fi
	case $n in build-wlan) b=1500 ;; build*) b=900 ;; unit) b=2700 ;; xnu-build) b=2400 ;; stack-apply) b=1200 ;; wlan-mutants) b=5400 ;; *) b=300 ;; esac; b=${GATE_STEP_TIMEOUT:-$b}
	echo "START $n $(date +%s)" >>"$prog"; s=$SECONDS
	# 9>&- below: a step's children (an adb server, a stub sleep) must not keep the gate lock after the gate ends
	# bash -c re-dispatches so that the exported shell function `needs` can run under timeout
	# GATE_STEP_BUDGET: this step's timeout, for the step command to size its own waits (xnu-build: tools/xnu-remote-build derives its offload-host queue wait from it, review xnu-remote-build-r1 F3)
	GATE_STEP_BUDGET=$b "${lowpri[@]}" timeout -k 10 "$b" bash -c '"$@"' _ "$@" >"shim/out/gate/$n.log" 2>&1 9>&- 8>&- &
	tp=$!; echo "$tp" >"$tmp/$n.pid"; wait "$tp"; rc=$?; rm -f "$tmp/$n.pid"
	[ -d "$tmp" ] && [ ! -e "$tmp/interrupted" ] || exit 1
	# lane C killed by the gate because lane A or B failed (below): not a failure of its own, the reported failed step stays the first real one
	[ -e "$tmp/stopC" ] && [ "$n" = wlan-mutants ] && { echo "$n stopped" >"$tmp/$n.time"; echo "END $n STOPPED" >>"$prog"; return 0; }
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
# K3 (PLAN-GATE-2X section 8): no serial warm-up build any more. It existed so that the other variants hit the audit memo entries `build` had just stored instead of each computing the same
# self-tests (cold or stale memo = every variant misses the same entries). audit_memo.run now takes a per-key lock (tools/audit_memo.py): variants that miss the same entry at the same time wait for the
# one that computes it and then hit, so a cold or stale memo costs one compute per distinct entry with all variants running at once, and the ~200 s serial canary (the old lane A pole) is gone. A stale memo is
# the same case as an empty one (an audit edit changes every key; a dependency edit makes the entries unloadable): no reason left to serialize either. `build` still runs the host tests with
# PIXELIOS_HOST_TESTS=always and refreshes the stamp; the other builds skip them only when that stamp exists, so on a missing stamp each variant that starts together runs them itself (duplicate CPU in parallel, same wall; the stamp write is an atomic mv).
# Inside one audit the self-tests also run in a process pool (GATE_AUDIT_JOBS, tools/audit_pool.py). K5, stated precisely (review gate-cold-k3 r1 F1): the formula A=(B-2)/J (1..4) bounds the POOL WORKERS of lane A only:
# J audits at a time x A workers <= B-2 extra worker processes. It does NOT bound the whole gate: each audit also has a parent (the real checks, objdump/nm), the J builds compile and link, and lanes B
# (S unit shards) and C (M mutant threads) run at the same time (J+S+M is about B before any pool worker), so the total can exceed nproc-2 while an audit self-test phase overlaps them. An explicit
# GATE_AUDIT_JOBS wins but is clamped to max(1, B-2) per audit (loud on stderr and in gate.log).
# bluejay-testkey.pem is created atomically by whichever build gets there first (genrsa to a temp + mv -n), so it needs no ordering.
laneA() {
	local apool_src=derived apool_max=$((B - 2)) apool_asked
	[ "$apool_max" -ge 1 ] || apool_max=1
	if [ -z "${GATE_AUDIT_JOBS:-}" ]; then GATE_AUDIT_JOBS=$(( (B - 2) / J )); [ "$GATE_AUDIT_JOBS" -ge 1 ] || GATE_AUDIT_JOBS=1; [ "$GATE_AUDIT_JOBS" -le 4 ] || GATE_AUDIT_JOBS=4
	else
		apool_src="env"
		case $GATE_AUDIT_JOBS in ''|*[!0-9]*) ;; *)  # a non-number is left for tools/audit_pool.py to reject loudly
			apool_asked=$((10#$GATE_AUDIT_JOBS))
			if [ "$apool_asked" -gt "$apool_max" ]; then
				GATE_AUDIT_JOBS=$apool_max; apool_src="env, clamped from $apool_asked"
				echo "gate: GATE_AUDIT_JOBS=$apool_asked is above max(1, budget-2)=$apool_max (budget=$B): clamped to $apool_max" | tee -a "$log" >&2
			fi ;;
		esac
	fi
	export GATE_AUDIT_JOBS
	echo "gate: audit pool GATE_AUDIT_JOBS=$GATE_AUDIT_JOBS ($apool_src; budget=$B, A.jobs=$J: bounds the pool workers only, J*A=$((J * GATE_AUDIT_JOBS)); parents, builds and lanes B/C are extra)" >>"$log"
	# informational only (the ledger and a reader can tell a warm run from a cold one); the exit code no longer selects a different schedule
	if python3 tools/test-bluejay-boot.py --memo-warm >>"shim/out/gate/memo-warm.log" 2>&1; then echo "gate: audit memo warm" >>"$log"; else echo "gate: audit memo cold or stale: all builds start at once, identical self-tests are computed once (per-key lock)" >>"$log"; fi
	par step A build    bash -c 'cd shim && PIXELIOS_HOST_TESTS=always ./build.sh --board bluejay --avb --out out/gate/build-0'
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
	# AP-258: apply the whole numbered xnu/patches stack (stagers, then patches in order) on a pristine tree; RESULT: FAIL + the first failing patch name. In parallel with the builds (network + disk, no CPU). Selected when xnu/patches/** or tools/stack-apply changed. No escape: a hunk fix that only fits a stack nobody built must not pass.
	par step A stack-apply  needs tools/stack-apply tools/stack-apply
	par step A build-udump  bash -c 'cd shim && ./build.sh --board bluejay --avb --pstore-fast --udump 1 --out out/gate/build-udump-0'
	wait
	step A host-tests   bash -c 'cd shim && d=out/gate/build-0 && { [ -s $d/host-tests.list ] || { echo "no $d/host-tests.list (build.sh did not run the host tests)"; exit 1; }; } && while read -r n; do [ -x $d/$n ] || { echo "MISSING host test binary $n"; exit 1; }; $d/$n </dev/null || { echo "FAIL $n"; exit 1; }; done <$d/host-tests.list && for f in $d/*host_test*; do if [ -x "$f" ] && ! grep -qx "${f##*/}" $d/host-tests.list; then echo "UNLISTED host test binary ${f##*/} (compile it through chk in build.sh)"; exit 1; fi; done'
	step A boot-safety  python3 tools/test-bluejay-boot.py --out shim/out/gate/build-0 shim/out/gate/build-0/shim-boot-avb.img
	# WARN-only (full gate only, never fails): findings or a broken analyzer become a count on the GATE line (below), full output in shim/out/gate/lint-c.log
	step A lint-c       bash -c 'timeout -k 5 250 python3 tools/lint-c --out shim/out/gate/build-0; rc=$?; [ $rc -le 2 ] || echo "RESULT: ERROR lint-c rc=$rc (timeout or crash) files=0 findings=0"; exit 0'
	# AP-257: compile HEAD:xnu/patches as real XNU (the steps above only build pure headers). Selected when xnu/patches or its build tools changed, always in a full gate. --always: on trunk itself
	# HEAD:xnu/patches equals trunk's tree, the default SKIPPED would hide exactly the batch that was merged unbuilt. GATE_XNU_BUILD=0 (remote-job's container: gate-pre built it on the host first) skips loudly.
	step A xnu-build    needs tools/gate-xnu-build bash -c '[ -n "${HEAVY_JOB_HELD:-}" ] || echo "xnu-build: WARNING no heavy-job lock held: a lab-host fallback build would queue on the heavy-job lock for up to HEAVY_JOB_WAIT seconds (the xnu-local-build default is far longer than this step budget); a offload-host queue wait is bounded by the step budget minus 1500 s (tools/xnu-remote-build)"; if [ "${GATE_XNU_BUILD:-}" = 0 ]; then echo "RESULT: PASS xnu-build SKIPPED by GATE_XNU_BUILD=0 (the caller built HEAD:xnu/patches first)"; exit 0; fi; exec tools/gate-xnu-build --always --label gate-xnu'
}
laneB() {
	# AP-11/144: the one untracked input (research/ipsw-x/d22ap.adt; tools/third_party is vendored, T-93e6) must resolve in this worktree; first, so a missing input is the loud first failure. Escape: GATE_NO_INPUTS="reason" (GATE line)
	step B inputs       needs tools/gate-inputs bash tools/gate-inputs
	step B validate     python3 tools/validate-repo.py
	# AP-281/277: presence pass over every mutant table (seconds, nothing compiled): MUTANT NEEDLE MISSING lines + RESULT: FAIL; a new table must be registered in the lint
	step B mutant-needles needs tools/lint-mutant-needles python3 tools/lint-mutant-needles
	# AP-229: a boot print a patch adds must not trip a failing xrun_report marker (panic/hang/watchdog, root mount failed); seconds, RESULT: FAIL + BOOT PRINT HITS MARKER lines
	step B boot-markers needs tools/lint-boot-markers python3 tools/lint-boot-markers
	# rule 8 / AP-102: no silent stub, ignored selector/key or swallowed error in shim C, the added lines of xnu/patches, python and shell; FAILS the gate; pinned exceptions in tools/lint-silent-allow.txt (T-b841)
	step B lint-silent  needs tools/lint-silent python3 tools/lint-silent
	# R48 row 2: every tag/key a host packer packs (tools/pack_wlanfw.py ...) has a consumer in shim/ or the added patch lines, or a gap log naming it; seconds, UNCONSUMED lines + RESULT: FAIL (T-ebec)
	step B consumed needs tools/lint-consumed python3 tools/lint-consumed
	step B rules        python3 tools/check-rules.py --strict
	step B shellcheck   bash -c 'command -v shellcheck >/dev/null || { echo "shellcheck is not installed (apt install shellcheck)"; exit 1; }; tools/shell-files | xargs -P 8 -n 8 shellcheck -S warning && python3 tools/lint-shellcheck-reason'
	# T-6070 (public by default, RULES R7): the tip tree of the gated head against the ratchet tools/public-push-guard-baseline.tsv (no new host name/IP/path/.env value; PUBLIC GAP line while the baseline is not empty)
	step B public-guard needs tools/public-push-guard python3 tools/public-push-guard gate HEAD
	step B public-split needs tools/make-public-tree.py python3 tools/make-public-tree.py --check
	step B unit         tools/gate-unit-shards ${GATE_UNIT_ONLY:+--selected}   # --selected only in a quick run (GATE_UNIT_ONLY is exported above for it alone); anything else ignores a stray variable
	step B wlan-tbl     needs tools/wlan-tbl-gen python3 tools/wlan-tbl-gen header-check --tsv soc/google/gs101/wlan_msgbuf_l22.tsv --hdr shim/sweet_wlan_tbl_data.h   # T-12ad: the committed C table is the generator output of the committed TSV (host test + mutants: shim/build.sh, tools/test_wlan_tbl_gen.py)
	step B phone-state  tools/phone-state --self-test
	step B hang-recover  tools/hang-recover --self-test
	step B probe-run    tools/probe-run --self-test
	step B phone-converge tools/lint-phone-converge   # R38/AP-291: no readiness checks in a run path (allowlist tools/phone-converge-allow.txt); fixture + mutants: tools/test_lint_phone_converge.py
	step B rootless     needs tools/build-ios-root-rootless.sh bash -c 'bash -n tools/build-ios-root-rootless.sh && bash -n tools/rootless/vm.sh && bash -n tools/rootless/compare-images.sh && { [ ! -s "${PIXELIOS_VM:-$HOME/pixelios-artifacts/vm}/vmlinuz" ] || ! command -v fakeroot >/dev/null || tools/build-ios-root-rootless.sh --selftest; }'
}
# lane C: the wlan chip-writer mutants (94% of the old unit time); starts at t=0 next to A and B, selected by --quick only when a file it reads changed
laneC() { step C wlan-mutants needs tools/test_wlan_wr_mutants.py python3 -m unittest discover -s tools -p test_wlan_wr_mutants.py; }
laneC & pc=$!; ( trap ': >"$lanes_a_done"' EXIT; laneA ) & pa=$!; laneB; wait "$pa"   # the EXIT trap also fires when lane A fails or exits early: lane B's unit shards grow either way
# lanes A+B are done: the heavy part is over, lane C (wlan mutants, ~130 MB) needs no exclusive host. Release the host lock now. `flock -u` unlocks the shared
# open file description, so the copies of fd 8 inherited by the lane subshells are released too (closing only the main shell's fd would not unlock).
: >"$lanes_ab_done"   # K1: exactly when the host lock goes (also when none is held, and on a lane A/B FAIL): lane C's mutant pool grows by 8 jobs (cap nproc-2)
if [ "$hl" != 0 ]; then flock -u 8; exec 8>&-; echo "host-lock released (lanes A+B done, C running)" | tee -a "$prog" >>"$log"; fi
# a lane A/B failure must not leave the (up to 5400 s) mutant lane running and the host lock held: stop it (review gate-runfast-1 #6)
if [ -z "$keep" ] && compgen -G "$tmp/*.fail" >/dev/null; then
	: >"$tmp/stopC"; p=$(cat "$tmp/wlan-mutants.pid" 2>/dev/null) && [ -n "$p" ] && kill -KILL -- "-$p" "$p" 2>/dev/null
fi
wait "$pc"
# deterministic log: steps in ALL order, then timings
for n in $ALL; do [ -s "shim/out/gate/$n.log" ] && { echo "== $n"; cat "shim/out/gate/$n.log"; }; done >>"$log"
{ echo "== timings (s)"; for n in $ALL; do cat "$tmp/$n.time" 2>/dev/null; done; } >>"$log"
fail=; for n in $ALL; do [ -e "$tmp/$n.fail" ] && { fail=$(cat "$tmp/$n.fail"); break; }; done
# a lane subshell that died outside step() (external kill, OOM) leaves neither .fail nor .time: never PASS without the step (review #7)
if [ -z "$fail" ]; then for n in $want; do [ -e "$tmp/$n.time" ] || { fail="$n (did not run)"; break; }; done; fi
total=$(echo $ALL | wc -w); ran=$(echo $want | wc -w)
if [ -n "$quick" ]; then tag=" quick: ran $ran of $total steps"; [ -n "$fail" ] && tag=" quick: $fail"; else tag=$fullnote; [ -n "$fail" ] && tag="$tag${fullnote:+:} $fail"; fi
# counted warnings ride on every GATE line, PASS and FAIL (a passing step shows only its last line, in the log): unit skipped=N when N > 0 (KNOWN-GAPS F8,
# also on an a=0 tree that registers) and the census super_new/super_stale counts (review census-hermetic-r1 F2; W26 2026-10-29). shim/out/gate is fresh per run (above).
x=$(grep -o 'skipped=[0-9]*' shim/out/gate/unit.log 2>/dev/null | tail -1); [ -n "$x" ] && [ "$x" != skipped=0 ] && tag="$tag unit $x"
# S4 / review gate-cold-s4-r1 F8: a quick gate whose unit step ran a subset says so (unit-partial=RAN/ALL, no parentheses: gate-import-remote); a full run and the formal gate never carry it
x=$(grep -o ' partial=[0-9]*/[0-9]*' shim/out/gate/unit.log 2>/dev/null | tail -1); [ -n "$quick" ] && [ -n "$x" ] && tag="$tag unit-partial=${x#*=}"
for k in super_new super_stale; do x=$(grep -o "$k=[0-9]*" shim/out/gate/validate.log 2>/dev/null | head -1); [ -n "$x" ] && tag="$tag warn census $x: run tools/tool-census --write"; done   # T-9954: "warn census": the census drift is a warning, never the failure cause
# T-9954: a failed unit / wlan-mutants step names its failing tests on the GATE line (up to 3 from SHARD FAIL <file> and SKIP NOT ALLOWED <id>, +N more): a worker must never re-queue a gate over a census warning that was not the cause. No parentheses (gate-import-remote parses the line)
case $fail in unit*|wlan-mutants*)
	fnames=$(for n in unit wlan-mutants; do awk '/^SHARD FAIL /{print $3} /^SKIP NOT ALLOWED /{print $4}' "shim/out/gate/$n.log" 2>/dev/null; done | awk 'NF && !seen[$0]++')
	if [ -n "$fnames" ]; then
		fcount=$(echo "$fnames" | wc -l); fshown=$(echo "$fnames" | head -3 | paste -sd, - | sed 's/,/, /g')
		[ "$fcount" -le 3 ] && tag="$tag failed: $fshown" || tag="$tag failed: $fshown +$((fcount - 3)) more"
	fi ;;
esac
# AP-11/144: GATE_NO_INPUTS waives the inputs step: never silent, the reason rides on the GATE line
waived=
grep -q '^RESULT: PASS inputs SKIPPED by GATE_NO_INPUTS=' shim/out/gate/inputs.log 2>/dev/null && { waived=1; tag="$tag inputs waived: $(grep -o -m1 'GATE_NO_INPUTS=.*' shim/out/gate/inputs.log | tr -d '"$`\\')"; }
# stack-apply without network (the formal container runs --network=none, review r42-gate-r1 F2): tools/stack-apply prints "STACK-APPLY NOT RUN: no network" and exits 0; never silent, the tag rides on the GATE line (no parentheses: importable)
grep -q '^STACK-APPLY NOT RUN: no network' shim/out/gate/stack-apply.log 2>/dev/null && tag="$tag stack-apply not run: no network"
# xnu-build in a repo without tools/gate-xnu-build (public split): `needs` skipped it; say so on the GATE line (gate-xnu-step-r1 F5; no parentheses, the line stays importable)
case " $want " in *" xnu-build "*) [ -z "$fail" ] && grep -q '^skip: tools/gate-xnu-build is not' shim/out/gate/xnu-build.log 2>/dev/null && tag="$tag xnu-build not built: tool absent" ;; esac
# where the kernel was built rides on the GATE line when it is not the normal offload-host build (review xnu-remote-build-r1 F7, rule 8): where=<lab-host-fallback|lab-host-local|unknown...>, fallback=lab-host, dirty=N (no parentheses: importable)
xl=$(grep -a '^RESULT: PASS xnu-build sha=' shim/out/gate/xnu-build.log 2>/dev/null | tail -n 1)
if [ -z "$fail" ] && [ -n "$xl" ]; then
	xw=$(grep -o -m1 ' where=[^ ]*' <<<"$xl"); xt=; { [ "$xw" = " where=offload-host" ] || [ "$xw" = " where=offload-slot" ]; } || xt=$xw
	case $xl in *" fallback=lab-host"*) xt="$xt fallback=lab-host" ;; esac
	xd=$(grep -o -m1 ' dirty=[^ ]*' <<<"$xl"); [ "$xd" = " dirty=0" ] || xt="$xt$xd"
	[ -z "$xt" ] || tag="$tag xnu-build$xt"
fi
# lint-c is WARN-only (TP-014): never part of $fail, always loud when it ran: warn=N findings, ERROR when the analyzer/headers were missing or it crashed (rule 8)
case " $want " in *" lint-c "*)
	lc=$(grep '^RESULT:' shim/out/gate/lint-c.log 2>/dev/null | tail -1)
	case $lc in
	"RESULT: PASS"*) ;;
	"RESULT: FAIL"*) tag="$tag lint-c warn=$(echo "$lc" | grep -o 'findings=[0-9]*' | cut -d= -f2): see shim/out/gate/lint-c.log" ;;
	*) [ -z "$fail" ] && tag="$tag lint-c ERROR: ${lc:-did not run}; see shim/out/gate/lint-c.log" ;;
	esac ;;
esac
[ -n "$fail" ] && finish "GATE FAIL $h$tag (see $log)" 1
# stamp only on a clean-tree full PASS (+dirty also covers untracked files): the next --quick diffs against exactly this commit
# HEAD moving or the tree changing during the run (a commit, an edit) means the PASS covers a state we did not keep: no stamp, +dirty
if [ "$(git rev-parse HEAD)" != "$full" ] || [ -n "$(git status --porcelain)" ]; then h=${h%+dirty}+dirty; fi
# a waived inputs step (GATE_NO_INPUTS) is not a clean full PASS: no stamp (a later --quick must not diff against it) and no registry entry (r42-gate-r1 F3)
[ -z "$quick" ] && [ "${h%+dirty}" = "$h" ] && [ -z "$waived" ] && echo "$full" >"$stamp"
# U4 (review gate-runfast-1 #9; research 69): gitignored state is invisible to the dirty check above, so the registry entry carries it: "<check-all.sh blob> <g> <a>" with g = hash of tools/third_party and a = research/ipsw-x/d22ap.adt present (gate_select.py --state; gate_select.py only accepts an entry whose blob and g match and whose a >= this tree's).
# Per-worktree shim/out/bluejay-testkey.pem and the rootless VM cannot change a result (research 69). A tree with a=1 whose unit step skipped any test that is not an environment-pinned skip (tools/unit-skip-allow.txt, reason starting "environment"; T-9fb9) is not registered (such a skip is not a full PASS).
# host-wide registry of full PASSes (research 68 D2): any worktree's --quick may diff against this commit. Only a clean full PASS writes it (a quick PASS never does). Best effort: a read-only $HOME must not fail the gate.
if [ -z "$quick" ] && [ "${h%+dirty}" = "$h" ] && [ -n "$waived" ]; then tag="$tag (not registered: inputs waived)"
elif [ -z "$quick" ] && [ "${h%+dirty}" = "$h" ]; then
	gp=${GATE_PASS_DIR:-$HOME/.cache/pixelios/gate-pass}; st=$(python3 tools/gate_select.py --state 2>/dev/null)
	# T-9fb9: gate-unit-shards fails the unit step for any skip not pinned in tools/unit-skip-allow.txt and prints "unit skips: environment=E other=O"; only O counts here (a skip pinned with an "environment" reason = a tool the gate host lacks, e.g. the devd toolchain in the gate container). No such line (older runner, partial output) and skipped=N>0: count them all as other.
	so=$(sed -n 's/^unit skips: environment=[0-9]* other=\([0-9]*\) .*/\1/p' shim/out/gate/unit.log 2>/dev/null | tail -n 1)
	if [ -z "$so" ]; then if grep -Eq 'skipped=[1-9]' shim/out/gate/unit.log 2>/dev/null; then so=1; else so=0; fi; fi
	if [ -n "$st" ] && { [ "${st#* }" = 0 ] || [ "$so" = 0 ]; }; then
		{ mkdir -p "$gp" && echo "$(git hash-object tools/check-all.sh) $st" >"$gp/$full"; } 2>/dev/null || true
	else tag="$tag (not registered: skipped tests on a tree with the ADT, or no tree state)"; fi
fi
finish "GATE PASS $h$tag" 0
