# shellcheck shell=bash
# shellcheck disable=SC2154  # hj_self, hj_reexec and qk are set by the sourcing tools/heavy-job
# tools/heavy-job-canon.sh: the canonical-copy code of tools/heavy-job (T-d543, moved here by T-4675 to keep heavy-job under the 800-line size lint). SOURCED by tools/heavy-job from its
# own directory, never run. A missing module is one `HEAVY GAP:` line in heavy-job and the local copy runs. Needs from heavy-job: hj_self, hj_reexec, out(); it sets hj_blob_self, hj_canon, hj_canon_blob.
# Admission is decided by ONE copy, the canonical checkout's tools/heavy-job (main clone = parent of the git common dir of this script's directory) [obs 2026-11-02: 7 waiters ran a worktree copy
# with the old load1<6 rule and held the FIFO head 3-4 h]. A differing blob re-execs the canonical copy with ONE loud line; a missing/unreadable/unhashable one is a `HEAVY GAP:` line and the
# local copy runs. HEAVY_JOB_REEXEC=1 = loop guard (read once and dropped by heavy-job, so no CMD inherits it).

hj_blob() { env -u GIT_DIR -u GIT_WORK_TREE git hash-object -- "$1" 2>/dev/null; }

# hj_canon_resolve "$@": for run|detach|offload-host|status; may exec the canonical copy with the same argv
hj_canon_resolve() {
	local hj_why='' hj_g
	hj_blob_self=$(hj_blob "$hj_self")
	if [ -n "$hj_reexec" ]; then hj_canon=$hj_self; hj_canon_blob=$hj_blob_self; return 0; fi
	if [ -n "${HEAVY_JOB_CANONICAL:-}" ]; then hj_canon=$HEAVY_JOB_CANONICAL
	elif hj_g=$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$(dirname "$hj_self")" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) && [ -d "$hj_g" ]; then hj_canon=$(dirname "$hj_g")/tools/heavy-job
	else hj_why="$(dirname "$hj_self") is not in a git checkout"; fi
	if [ -z "$hj_why" ]; then
		if [ ! -e "$hj_canon" ]; then hj_why="$hj_canon does not exist"
		elif [ ! -r "$hj_canon" ]; then hj_why="$hj_canon is not readable"
		else hj_canon_blob=$(hj_blob "$hj_canon"); [ -n "$hj_canon_blob" ] || hj_why="cannot hash $hj_canon (git hash-object failed)"; fi
	fi
	if [ -z "$hj_blob_self" ] && [ -z "$hj_why" ]; then hj_why="cannot hash $hj_self (git hash-object failed)"; hj_canon_blob=; fi
	if [ -n "$hj_why" ]; then
		hj_canon=; out "HEAVY GAP: canonical heavy-job not found ($hj_why), running $hj_self"   # T-d543:gap
	elif [ "$hj_canon_blob" != "$hj_blob_self" ]; then
		out "heavy-job: STALE COPY $hj_self blob=$hj_blob_self -> exec $hj_canon blob=$hj_canon_blob"
		shopt -s execfail
		# shellcheck disable=SC2093  # execfail is set: a failed exec returns here and says so below
		HEAVY_JOB_REEXEC=1 exec "$BASH" "$hj_canon" "$@"   # T-d543:exec
		shopt -u execfail
		out "HEAVY GAP: canonical heavy-job not found (exec of $hj_canon failed), running $hj_self"; hj_canon=; hj_canon_blob=
	fi
}

# T-d543: the heavy-job script a live PID runs, from /proc/PID/cmdline (a ticket of a copy older than T-d543 has no blob field); empty = cannot tell
waiter_blob() {
	local -a a; local s='' i cwd
	[ -r "/proc/$1/cmdline" ] || return 0
	mapfile -d '' -t a <"/proc/$1/cmdline" 2>/dev/null || return 0
	for i in 0 1 2; do case ${a[$i]:-} in */heavy-job|heavy-job) s=${a[$i]}; break ;; esac; done
	[ -n "$s" ] || return 0
	case $s in /*) ;; *) cwd=$(readlink "/proc/$1/cwd" 2>/dev/null) || return 0; s=$cwd/$s ;; esac
	hj_blob "$s"
}

# hj_stale_check PID REST LABEL TICKET: a live ticket whose script is not the canonical one can hold the head with an old admission rule (REST = the ticket line after the state word; qk = queue kind)
hj_stale_check() {
	local bl='' wb
	[[ $2 =~ blob=([0-9a-f]+) ]] && bl=${BASH_REMATCH[1]}   # ticket: owner label pid epoch state [unit=U] [prio=P] [blob=B]
	wb=$bl; [ -n "$wb" ] || wb=$(waiter_blob "$1")
	if [ -z "$wb" ]; then echo "heavy-job: $qk STALE-WAITER pid=$1 blob=unknown canonical=$hj_canon_blob label=$3 ticket=$4 (no blob in the ticket and none readable from /proc/$1/cmdline)"   # T-d543:stale
	elif [ "$wb" != "$hj_canon_blob" ]; then echo "heavy-job: $qk STALE-WAITER pid=$1 blob=$wb canonical=$hj_canon_blob label=$3 ticket=$4"; fi   # T-d543:stale
}
