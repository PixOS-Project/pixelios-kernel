#!/bin/bash
# offload-conf.sh (T-dd93, R66): shell loader for tools/offload-host.conf; `. tools/offload-conf.sh` then `offload_conf_load TOOLROOT`.
# The twin for python tools is tools/offload_conf.py (same file, same keys; tools/test_offload_conf.sh proves they agree).
# Reads ONLY `KEY=VALUE` lines of the known keys (never sourced, nothing is expanded). Environment wins over the file. Sets OFFLOAD_CONF_FILE
# (empty = no file read) and prints `OFFLOAD GAP:` lines for unknown keys / an unreadable named file (no silent ignore, R8).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
[ "${BASH_SOURCE[0]:-$0}" = "$0" ] && case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
OFFLOAD_CONF_KEYS="OFFLOAD_HOST OFFLOAD_USER OFFLOAD_ROOT OFFLOAD_FOREIGN_RE LAB_HOME OFFLOAD_QUEUE_LABEL"
offload_conf_path() {   # TOOLROOT: the file to read, or nothing
	local root=${1:-.} c
	if [ -n "${OFFLOAD_CONF:-}" ]; then [ "$OFFLOAD_CONF" = none ] || echo "$OFFLOAD_CONF"; return 0; fi
	c=$(git -C "$root" rev-parse --git-common-dir 2>/dev/null) && { case $c in /*) ;; *) c=$root/$c ;; esac; c=$(cd "$c/.." 2>/dev/null && pwd) && [ -f "$c/tools/offload-host.conf" ] && { echo "$c/tools/offload-host.conf"; return 0; }; }
	[ -f "$root/tools/offload-host.conf" ] && echo "$root/tools/offload-host.conf"
	return 0
}
offload_conf_load() {   # TOOLROOT (the repo that contains the tool)
	local f k v line
	f=$(offload_conf_path "${1:-.}"); OFFLOAD_CONF_FILE=
	[ -n "$f" ] || return 0
	[ -r "$f" ] || { echo "OFFLOAD GAP: host config $f is not readable; no offload values loaded" >&2; return 0; }
	# shellcheck disable=SC2034  # OFFLOAD_CONF_FILE is read by the sourcing tools
	OFFLOAD_CONF_FILE=$f
	while IFS= read -r line || [ -n "$line" ]; do
		case $line in ''|'#'*) continue ;; esac
		k=${line%%=*}; v=${line#*=}
		[[ $k =~ ^[A-Z_]+$ ]] && [ "$k" != "$line" ] || { echo "OFFLOAD GAP: $f: ignored line without KEY=VALUE" >&2; continue; }
		case " $OFFLOAD_CONF_KEYS " in *" $k "*) ;; *) echo "OFFLOAD GAP: $f: unknown key $k ignored" >&2; continue ;; esac
		[ -n "$v" ] || continue
		[ -n "${!k:-}" ] || printf -v "$k" %s "$v"
	done <"$f"
	[ -z "${OFFLOAD_USER:-}" ] || [ -z "${OFFLOAD_HOST:-}" ] || case $OFFLOAD_HOST in *@*) ;; *) OFFLOAD_HOST=$OFFLOAD_USER@$OFFLOAD_HOST ;; esac
	OFFLOAD_ROOT=${OFFLOAD_ROOT:-}
	LAB_HOME=${LAB_HOME:-$HOME}
	[ -z "${OFFLOAD_QUEUE_LABEL:-}" ] || export OFFLOAD_QUEUE_LABEL   # exported: the probe subshells of remote-job (bash -c) read the switch with the same label
	return 0
}

# T-ee35 + per-use switches: the web board writes `<label><TAB><reason>` rows to the "offline" file of the queue (super repo). A bare `<label>` row = the MASTER switch (every use off);
# a `<label>:<use>` row switches off ONE use of the offload host. The use list is defined ONCE, here (twin: USES in tools/offload_conf.py; tools/test_offload_conf.sh proves they agree):
#   build (XNU/shim builds + staging)  gate (formal gate pre-validation, unit shards, queue gate lane, gate import)  test (light host tests / mutant runs routed by heavy-job --light)
#   sync (offload-sync git hooks)      ci (ci-local GPU CI)
# offload_off_reason TOOLROOT [USE] prints the reason (return 0) when the master row or USE's row is listed, else nothing (return 1). An unknown USE is an `OFFLOAD GAP:` line on stderr and only the master is checked.
# Callers print ONE loud line `OFFLOAD OFF: use=<use> ...` and carry on as if the host were unreachable (never fail, never wait); a job already running there is left alone.
# Label = ${OFFLOAD_QUEUE_LABEL:-offload-host} (key of tools/offload-host.conf or env: the name the queue uses for this host in that file; not an address to connect to).
# File = $OFFLOAD_OFFLINE_FILE (`none` = ignore), else $PIXOS_SUPER/docs/queue/offline, else <main clone>/../../docs/queue/offline. Missing file = host on.
# Running jobs are untouched: only NEW routing consults this (remote-job probe, heavy-job offload-host probe), so a job already on the host finishes.
OFFLOAD_USES="build gate test sync ci"; export OFFLOAD_USES
offload_off_reason() {
	local root=${1:-.} use=${2:-} f c label=${OFFLOAD_QUEUE_LABEL:-offload-host} h r hit=
	case " $OFFLOAD_USES " in *" $use "*) ;; *) [ -z "$use" ] || { echo "OFFLOAD GAP: unknown offload use '$use' (valid: $OFFLOAD_USES); only the master switch is checked" >&2; use=; } ;; esac
	if [ -n "${OFFLOAD_OFFLINE_FILE:-}" ]; then [ "$OFFLOAD_OFFLINE_FILE" = none ] && return 1; f=$OFFLOAD_OFFLINE_FILE
	elif [ -n "${PIXOS_SUPER:-}" ]; then f=$PIXOS_SUPER/docs/queue/offline
	else
		c=$(git -C "$root" rev-parse --git-common-dir 2>/dev/null) || c=
		case $c in /*) ;; '') c=$root/.git ;; *) c=$root/$c ;; esac
		f=$(cd "$c/../../.." 2>/dev/null && pwd)/docs/queue/offline
	fi
	[ -r "$f" ] || return 1
	while IFS=$'\t' read -r h r || [ -n "$h" ]; do
		case $h in ''|'#'*) continue ;; esac
		if [ "$h" = "$label" ]; then echo "${r:-no reason given} ($f)"; return 0; fi   # the master row wins: its reason is the one shown
		if [ -n "$use" ] && [ "$h" = "$label:$use" ] && [ -z "$hit" ]; then hit="${r:-no reason given} ($f)"; fi
	done <"$f"
	[ -n "$hit" ] || return 1
	echo "$hit"
}
