#!/bin/bash
# heavy-job-offload.sh: module of tools/heavy-job (sourced, never run). Per-use offload routing (the web board's switches, tools/offload-conf.sh OFFLOAD_USES).
#   hj_route_offload CMD...  (called by `run` before the light/gate handling; reads cmd_run's class lreq offloadc label by dynamic scope)
#     * `run --light -- python3|bash tools/test_*` (a host test or mutant runner), use `test`: offered to the offload host when it admits NOW (one probe, its FIFO empty, no waiting):
#       tools/remote-job test-run runs the SAME command on the committed HEAD in the pinned gate image (--network=none), its output is copied back here, a pass exits 0 with
#       `heavy-job: ROUTE remote ...` and `where=offload-host` on the RESULT line. Anything else is ONE `heavy-job: ROUTE local: <reason>` line and the normal local light path:
#       switch off (`OFFLOAD OFF: use=test ...`), no host configured, dirty tree, untracked script, a CMD argument that could write outside the checkout, a phone word (tools/remote-job --scan-only),
#       host unreachable / refused / busy. A remote CMD that FAILED is not trusted either (the container may lack a tool the command needs): the command is re-run here, its rc is the verdict, the RESULT line
#       carries gaps=1 and an `OFFLOAD GAP:` line says so (rule 8); a lost transport is the same.
#       A remote PASS whose output shows a skip is not trusted either (the container has no ADT/golden mounts, so a test can skip and still exit 0). `bash -c 'cd <this checkout> && <shell text>'` counts too (how long mutant runs are started):
#       the text runs as is in the container, unless it names a path outside the checkout.
#     * `run --class gate` WITHOUT --remote-cmd and CMD = a quick gate (check-all.sh --quick / gate --quick), use `gate`: when tools/gate_select.py says FULL (no base: the local run would be the full gate), it is the same job as a formal gate, so it takes the
#       ROUTE of --remote-cmd with tools/gate-route-remote (remote-job gate-pre + gate-import-remote; shim/out/gate.result, the GATE line and the stamp come out as a local full gate leaves them, `ran full` counts as formal in run-preflight). Offered only when the tree is clean;
#       a quick gate that really selects a subset of steps stays local (it is the cheap one, and its result cannot be imported). GATE_NO_ROUTE=why keeps it local. Anything else: ONE `ROUTE local` line.
#   hj_uses_status: one line per use for `offload-host status`.
# shellcheck shell=bash disable=SC2154,SC2034  # globals (hj_root, shost, sq, huse, route_line, gaps, gapw, p_err, sreason, class, lreq, offloadc, label, ...) belong to tools/heavy-job

hj_uses_status() {
	local u r
	for u in $OFFLOAD_USES; do
		if r=$(offload_off_reason "$hj_root" "$u"); then echo "heavy-job: offload use $u: OFF ($r)"; else echo "heavy-job: offload use $u: on"; fi
	done
}

hj_rlog() {   # USE remote|local TEXT: one tab-separated row of the last route decisions, <main clone>/logs/offload-route.log (the web board shows the newest row per use); best effort, kept to ~400 rows
	local f; f=$(logroot 2>/dev/null)/logs/offload-route.log
	{ mkdir -p "${f%/*}" && printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "${3//[$'\t\n']/ }" >>"$f" && { [ "$(wc -l <"$f")" -le 800 ] || { tail -n 400 "$f" >"$f.tmp" && mv -f "$f.tmp" "$f"; }; }; } 2>/dev/null || true
}

hj_route_local() { route_line="heavy-job: ROUTE local: $*"; out "$route_line"; hj_rlog "${huse:-test}" local "$route_line"; return 0; }   # the ONE line of a decision that stays local (cmd_run appends it to run.log)

hj_is_quick_gate() {   # CMD...: a check-all.sh / gate invocation with --quick among its first words
	local a n=0 g=0 q=0
	for a in "$@"; do
		n=$((n + 1)); [ "$n" -le 6 ] || break
		case ${a##*/} in check-all.sh|gate) g=1 ;; --quick) q=1 ;; *) ;; esac
	done
	[ "$g" = 1 ] && [ "$q" = 1 ]
}

hj_test_script() {   # CMD...: prints the tools/ script when CMD is `python3|bash [-I..] tools/test_* | tools/*mutant*`; else return 1 (not a host test: nothing is offered)
	local a
	case ${1##*/} in python3|python|bash|sh) ;; *) return 1 ;; esac
	shift
	for a in "$@"; do
		case $a in -I|-u|-B|-E|-s|-S) continue ;; *) ;; esac
		[[ $a =~ ^(\./)?tools/(test_[A-Za-z0-9_.-]+\.(py|sh)|[A-Za-z0-9_-]*mutant[A-Za-z0-9_-]*(\.(py|sh))?)$ ]] || return 1
		echo "${a#./}"; return 0
	done
	return 1
}

hj_shell_ok() {   # TEXT of a `bash -c`: prints why it could reach outside the checkout (absolute path, .., ~, command substitution), return 1
	local re='(^|[[:space:]=:<>|;&])/'
	[[ $1 =~ $re ]] && { echo "absolute path"; return 1; }
	case $1 in *..*) echo ".."; return 1 ;; *'~'*) echo "~"; return 1 ;; *'$('*|*'`'*) echo "command substitution"; return 1 ;; *) ;; esac
	return 0
}

hj_quick_offer() {   # CMD is a quick gate: set offloadc when it would run FULL anyway, the ROUTE of cmd_run does the rest (probe, admission, its one line, the late re-offer); else one ROUTE local line
	local top n u st
	huse=gate
	[ -z "${GATE_NO_ROUTE:-}" ] || { hj_route_local "quick gate stays local (GATE_NO_ROUTE: $GATE_NO_ROUTE)"; return 0; }
	[ -n "$shost" ] || { hj_route_local "OFFLOAD GAP: no offload host configured (set OFFLOAD_HOST in tools/offload-host.conf), running local"; return 0; }
	top=$(git rev-parse --show-toplevel 2>/dev/null) || { hj_route_local "not in a git checkout, running local"; return 0; }
	[[ $top =~ ^[A-Za-z0-9_./-]+$ ]] || { hj_route_local "checkout path '$top' cannot go into the remote command, running local"; return 0; }
	n=$(git status --porcelain --untracked-files=no 2>/dev/null | wc -l)
	u=$(git status --porcelain --untracked-files=normal -- tools xnu shim soc device 2>/dev/null | wc -l)
	[ "$n$u" = 00 ] || { hj_route_local "dirty tree ($n tracked changes, $u in tools xnu shim soc device): the offload job gates the committed HEAD, running local"; return 0; }
	[ -x "$hj_root/tools/gate-route-remote" ] || { hj_route_local "OFFLOAD GAP: $hj_root/tools/gate-route-remote missing, running local"; return 0; }
	st=$(cd "$top" && python3 tools/gate_select.py shim/out/gate.stamp 2>/dev/null) || st=
	[ "$st" = FULL ] || { hj_route_local "quick gate selects only the steps its diff needs (not a full run), stays local"; return 0; }
	offloadc="$hj_root/tools/gate-route-remote $top"
	return 0
}

hj_args_ok() {   # CMD...: prints the first argument that could name a place outside the checkout (absolute path, .., ~, $, =/ or :/ values, shell characters), return 1
	local a
	for a in "$@"; do
		[[ $a =~ ^[A-Za-z0-9_.,:=@+-][A-Za-z0-9_./,:=@+-]*$ ]] && [[ $a != *..* && $a != *[=:]/* ]] || { echo "$a"; return 1; }
	done
	return 0
}

hj_route_offload() {
	local sc top bad f n=0 u o rl jd jrc rc t0r el last
	[ -z "$offloadc" ] || return 0
	if [ "$class" = gate ]; then
		! hj_is_quick_gate "$@" || hj_quick_offer
		return 0
	fi
	[ -n "$lreq" ] || return 0
	huse="test"
	local re='^cd[[:space:]]+([^[:space:];&|]+)[[:space:]]*&&[[:space:]]*(.+)$' shell=0
	if [ "${1##*/}" = bash ] && [ "${2:-}" = -c ] && [ $# = 3 ] && [[ $3 =~ $re ]]; then   # `bash -c 'cd X && TEXT'`: X must be this checkout; only this function's "$@" changes, a local run keeps the original command
		[ "$(cd "${BASH_REMATCH[1]}" 2>/dev/null && pwd -P)" = "$(git rev-parse --show-toplevel 2>/dev/null)" ] || { hj_route_local "bash -c runs in another directory (${BASH_REMATCH[1]}), running local"; return 0; }
		set -- bash -c "${BASH_REMATCH[2]}"; shell=1
		sc=$(grep -oE '(\./)?tools/(test_[A-Za-z0-9_.-]+\.(py|sh)|[A-Za-z0-9_-]*mutant[A-Za-z0-9_-]*(\.(py|sh))?)' <<<"$3" | head -n 1); sc=${sc#./}
		[ -n "$sc" ] || { hj_route_local "shell command names no tools/test_* or mutant runner, running local"; return 0; }
		bad=$(hj_shell_ok "$3") || { hj_route_local "shell command could reach outside the checkout ($bad), running local"; return 0; }
	else
		sc=$(hj_test_script "$@") || { hj_route_local "not a plain host test (python3|bash tools/test_* or a mutant runner), running local"; return 0; }
	fi
	if o=$(offload_off_reason "$hj_root" test); then hj_route_local "OFFLOAD OFF: use=test switched off by order, running local: $o"; return 0; fi
	[ -n "$shost" ] || { hj_route_local "OFFLOAD GAP: no offload host configured (set OFFLOAD_HOST in tools/offload-host.conf), running local"; return 0; }
	top=$(git rev-parse --show-toplevel 2>/dev/null) || { hj_route_local "not in a git checkout (the offload job runs the committed HEAD), running local"; return 0; }
	git ls-files --error-unmatch -- "$sc" >/dev/null 2>&1 || { hj_route_local "$sc is not tracked (the offload job runs the committed HEAD), running local"; return 0; }
	n=$(git status --porcelain --untracked-files=no 2>/dev/null | wc -l)
	u=$(git status --porcelain --untracked-files=normal -- tools xnu shim soc device 2>/dev/null | wc -l)
	[ "$n$u" = 00 ] || { hj_route_local "dirty tree ($n tracked changes, $u in tools xnu shim soc device): the offload job runs the committed HEAD, running local"; return 0; }
	[ "$shell" = 1 ] || bad=$(hj_args_ok "${@:2}") || { hj_route_local "CMD argument '$bad' could name a place outside the job checkout, running local"; return 0; }
	o=$("$hj_root/tools/remote-job" --scan-only test-run -- "$@" 2>&1) || { hj_route_local "remote-job refused the command (${o##*$'\n'}), running local"; return 0; }
	n=0; for f in "$sq"/[0-9]*.*; do [ -e "$f" ] || continue; ( flock -n 3 ) 3<"$f" 2>/dev/null && continue; n=$((n + 1)); done   # live tickets of the offload FIFO only
	[ "$n" = 0 ] || { hj_route_local "offload host busy (its queue holds $n live job(s); one at a time), running local"; return 0; }
	if ! sprobe; then hj_route_local "offload host unreachable: $p_err, running local"; return 0; fi
	if ! sadmit; then hj_route_local "offload host refused: $sreason, running local"; return 0; fi
	jd=$top/logs/remote-jobs/test-run; rm -f "$jd/job.rc" "$jd/job.log"
	rl=$(logroot)/logs/heavy-job/$label; mkdir -p "$rl" || { hj_route_local "cannot create $rl, running local"; return 0; }
	mark_started; t0r=$(date +%s)
	route_line="heavy-job: ROUTE remote label=$label use=test ($(sstamp))"; out "$route_line"; echo "$route_line" >>"$rl/run.log"; hj_rlog test remote "$route_line"
	( export HEAVY_JOB_DETACHED_WAIT=20; offload_run --owner "$(id -un 2>/dev/null || echo unknown)" --label "$label" --wait 20 -- env REMOTE_JOB_REPO="$top" "$hj_root/tools/remote-job" --where offload-host test-run -- "$@" ) >"$rl/offload-route.log" 2>&1 9>&-
	rc=$?; jrc=$(cat "$jd/job.rc" 2>/dev/null); el=$(($(date +%s) - t0r))
	local skipre='(^|[^[:alnum:]_])SKIP(PED)?([^[:alnum:]_]|$)|skipped=[1-9]' skipped=0
	if [ "$rc" = 0 ] && [ "$jrc" = 0 ] && grep -aqE "$skipre" "$jd/job.log"; then skipped=1; fi
	if [ "$rc" = 0 ] && [ "$jrc" = 0 ] && [ "$skipped" = 0 ]; then
		cat "$jd/job.log"; cat "$jd/job.log" >>"$rl/run.log"
		o=$(res_line PASS "label=$label class=$class rc=0 elapsed=${el}s where=offload-host"); out "$o"; echo "$o" >>"$rl/run.log"
		exit 0
	fi
	last=$(grep -a -E '^(REMOTE:|RESULT:|OFFLOAD)' "$rl/offload-route.log" | tail -n 1 | cut -c1-200)
	if [ "$skipped" = 1 ]; then o="$gapw GAP: the host test passed on the offload host but its output shows a skip (the container has no ADT/golden mounts; its output: $jd/job.log); not trusted, re-running it here: this run's verdict"
	elif [[ $jrc =~ ^[0-9]+$ ]] && [ "$rc" = 1 ]; then o="$gapw GAP: the host test failed on the offload host (rc $jrc, its output: $jd/job.log); not trusted (the container may lack something the command needs), re-running it here: this run's verdict"
	else o="$gapw GAP: the offload route failed (rc $rc, ${last:-no detail}; see $rl/offload-route.log), running local"; fi
	gaps=1; route_line="heavy-job: ROUTE local: $o"; out "$route_line"; hj_rlog test local "$route_line"
	return 0
}
