#!/bin/bash
# heavy-job-light.sh: module of tools/heavy-job (sourced, never run). T-66a6: the LIGHT slot.
#   `heavy-job run --light ...` declares a job small (mutant runs, self-tests, single host tests): it takes its own lock ($HEAVY_JOB_LOCK.light) and its own
#   FIFO dir (<local queue dir>-light) instead of the main ones, so it runs while a full gate holds the main lock; at most ONE light job at a time. Everything
#   else of `run` is unchanged: same ticket code, same memory/PSI/CPU-PSI/load gate before the lock and before exec (nothing is throttled; the gate only decides
#   when a NEW job starts), same log, same rc mapping. Non-light admission is untouched (the main lock and queue are never read or written by a light job).
#   CPU cap (default ${HEAVY_JOB_LIGHT_CAP:-120} CPU-s, user+sys of CMD and every child heavy-job waited for = the kernel's RUSAGE_CHILDREN accounting (/proc/PID/stat cutime+cstime), measured, not guessed;
#   cap chosen from the 24 h table in docs/reviews/T-66a6-proof.md): a light job that used more prints `HEAVY GAP: light job LABEL used N CPU-s > CAP` (stdout, run.log), the RESULT
#   line gets gaps=1, `status` repeats it, and the label is marked in ${HEAVY_JOB_LOCK}.overruns/LABEL: the next light request with that label is refused (RESULT: ERROR light-refused)
#   with the do-instead (run it without --light, class as before) and the mark's path to remove once the job is really small again. CMD's own rc is never changed by the cap.
# shellcheck shell=bash disable=SC2154,SC2034,SC2015  # globals (lock, hq, label, rlog, gaps, out, err, sstate, ...) belong to tools/heavy-job
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac

hj_light_apply() {   # switch the lock + queue globals to the light slot (cmd_run before any ticket; cmd_inner via HEAVY_JOB_LIGHT_SLOT)
	light=1; hj_ovr=${lock}.overruns; lock=${lock}.light; hq=${hq}-light
}

hj_cpu_children() {   # CPU-s (user+sys) of every child this shell waited for, with their waited descendants: cutime+cstime of /proc/$$/stat (kernel accounting = RUSAGE_CHILDREN); "x" when unreadable
	awk -v t="$(getconf CLK_TCK 2>/dev/null || echo 100)" '{sub(/^.*\) /, ""); printf "%.1f", ($14 + $15) / t; f = 1} END{if (!f) printf "x"}' "/proc/$$/stat" 2>/dev/null || printf x
}

hj_light_refused() {   # label marked by an earlier overrun -> RESULT: ERROR (exit 2) before any ticket
	local m=$hj_ovr/$1
	[ -f "$m" ] || return 0
	err "light-refused: label $1 exceeded the light CPU cap before ($(head -1 "$m")); do instead: run it without --light (same --class), or if it is small again rm $m"
}

hj_light_after() {   # hj_light_after CPU-BEFORE: after CMD ended; sets gaps=1 and prints the GAP line when the cap was exceeded
	local used cap=${HEAVY_JOB_LIGHT_CAP:-120} l
	used=$(hj_cpu_children)
	if [[ $used =~ ^[0-9.]+$ && $1 =~ ^[0-9.]+$ ]]; then used=$(awk -v a="$used" -v b="$1" 'BEGIN{printf "%.1f", a-b}')
	else l="HEAVY GAP: light job $label: CPU time not measurable (/proc/$$/stat), cap $cap not checked"; out "$l"; rlog_add "$l"; gaps=1; return 0; fi
	awk -v u="$used" -v c="$cap" 'BEGIN{exit !(u+0>c+0)}' || return 0
	l="HEAVY GAP: light job $label used $used CPU-s > $cap"; out "$l"; rlog_add "$l"; gaps=1
	mkdir -p "$hj_ovr" 2>/dev/null && echo "$used CPU-s > $cap on $(date +%FT%T)" >"$hj_ovr/$label" || { l="HEAVY GAP: cannot mark label $label in $hj_ovr, its next light request is NOT refused"; out "$l"; rlog_add "$l"; }
}

hj_light_status() {   # status of the light slot on its own lines; sets lsu (ticket lines, so `sunits` does not list a ticketed light unit twice)
	local l=${lock}.light f
	echo "heavy-job: light slot cap=${HEAVY_JOB_LIGHT_CAP:-120} CPU-s"
	if ( exec 7>>"$l"; flock -n 7 ) 2>/dev/null; then echo "heavy-job: light lock free ($l)"; else echo "heavy-job: light lock held by: $(cat "$l" 2>/dev/null || echo unknown)"; fi
	sq=${hq}-light; qk=light; lsu=; if mkdir -p "$sq" 2>/dev/null; then lsu=$(sstate); echo "$lsu"; else echo "heavy-job: light queue UNAVAILABLE (cannot create $sq)"; fi
	for f in "${lock}.overruns"/*; do [ -f "$f" ] && echo "heavy-job: HEAVY GAP: light job ${f##*/} used $(head -1 "$f"); its next light request is refused (rm $f)"; done
	sq=$hq; qk=local; return 0
}
