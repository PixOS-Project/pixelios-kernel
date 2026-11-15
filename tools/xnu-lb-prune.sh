# shellcheck shell=bash
# xnu-lb-prune.sh: prune old XNU work trees under $LOCALBUILD/work (sourced by tools/xnu-local-build at the start of each build; ~930 MB per tree,
# 125 trees filled the SSD to 95 % on 2026-11-07 and 2026-11-11). The offload host has its own guarded prune (xnub_prune in tools/xnub-line.sh); this is the
# lab-host one: it differs in the guard root and in the extra keeps (hour window, live process, held gate lock, free-space floor), so it is a separate function.
#   lbp_prune WORKDIR LB LABEL      rc 0 done (also when nothing to do / disabled), 98 WORKDIR is not LB/work (nothing touched). Never fails the build for a short floor.
# Env: XNU_LOCAL_PRUNE=0 disables (one loud line); XNU_LOCAL_KEEP_RECENT (8) most recently used other trees; XNU_LOCAL_PRUNE_HOURS (24) hour window;
#      XNU_LOCAL_MIN_FREE_GB (40) free-space floor on LB's filesystem; XNU_LOCAL_FREE_CMD = test seam, a command printing the free GB (default: df).
# Used time of a tree = newest of its dir mtime, its build.log mtime, and the last line of the label in LB/built-on.tsv (a rebuild rewrites build.log, not the dir) [guess: heuristic].
# Kept always: the own label, any tree with a live process whose cwd is inside it, any tree whose .<label>.gate-lock (flock, tools/gate-xnu-build) is held.
lbp_free_gb() {   # LB -> free GB (integer) on its filesystem
	if [ -n "${XNU_LOCAL_FREE_CMD:-}" ]; then $XNU_LOCAL_FREE_CMD; else df -Pk -- "$1" | awk 'NR == 2 { print int($4 / 1048576) }'; fi
}
lbp_live() {   # WORKDIR LABEL -> 0 when the tree is in use (a cwd of LBP_CWDS inside it, or its gate lock held)
	local W=$1 b=$2 c
	while IFS= read -r c; do
		case $c in "$W/$b"|"$W/$b"/*) return 0 ;; esac
	done <<<"$LBP_CWDS"
	if [ -e "$W/.$b.gate-lock" ]; then ( flock -n 9 ) 9<"$W/.$b.gate-lock" 2>/dev/null || return 0; fi
	return 1
}
lbp_used() {   # WORKDIR LB LABEL -> epoch seconds of the last use
	local t=0 v led
	v=$(stat -c %Y "$1/$3" "$1/$3/build.log" 2>/dev/null | sort -rn | head -n 1); [ -z "$v" ] || t=$v
	led=$(awk -F'\t' -v l="$3" '$3 == l { x = $1 } END { print x }' "$2/built-on.tsv" 2>/dev/null)
	if [ -n "$led" ]; then v=$(date -d "$led" +%s 2>/dev/null) && [ "$v" -gt "$t" ] && t=$v; fi
	echo "$t"
}
lbp_rm() {   # WORKDIR LABEL -> removes the tree + its gate files; sets LBP_KB (freed) ; rc 1 when it could not
	local kb
	kb=$(du -sk -- "$1/$2" 2>/dev/null | cut -f1)
	rm -rf -- "${1:?}/${2:?}" || { echo "XNU LOCAL: WARNING could not remove work tree $1/$2"; return 1; }
	rm -f -- "$1/.$2.gate-lock" "$1/.$2.gate-xnu-build.log" "$1/.$2.gate-xnu-build."*.log "$1/.$2.gate-xnu-build."*.log.stale
	LBP_KB=$((LBP_KB + ${kb:-0}))
}
lbp_prune() {
	local W=$1 LB=$2 own=$3 nrec=${XNU_LOCAL_KEEP_RECENT:-8} hrs=${XNU_LOCAL_PRUNE_HOURS:-24} floor=${XNU_LOCAL_MIN_FREE_GB:-40}
	local LBP_CWDS now d b u cand="" order recent="" pn=0 pl="" kn=0 kl="" free LBP_KB=0 lk="" tab
	tab=$(printf '\t')
	if [ "${XNU_LOCAL_PRUNE:-1}" = 0 ]; then echo "XNU LOCAL: work-tree prune DISABLED by XNU_LOCAL_PRUNE=0 (the SSD can fill up)"; return 0; fi
	[[ $nrec =~ ^[0-9]+$ ]] || nrec=8
	[[ $hrs =~ ^[0-9]+$ ]] || hrs=24
	[[ $floor =~ ^[0-9]+$ ]] || floor=40
	case $W in "$LB"/work) ;; *) echo "XNU LOCAL: REFUSING to prune $W: not $LB/work"; return 98 ;; esac
	if [ -L "$W" ]; then echo "XNU LOCAL: WARNING work dir $W is a symlink, prune skipped"; return 0; fi
	[ -d "$W" ] || return 0
	now=$(date +%s)
	LBP_CWDS=$(readlink /proc/[0-9]*/cwd 2>/dev/null | sort -u) || { rc=$?; [ $rc = 1 ] || echo "XNU LOCAL GAP: cwd scan rc=$rc" >&2; }   # (rc 1 = a foreign process unreadable) one fork for all trees (a readlink per process per tree took minutes)
	# candidates (every non-dot real dir except the own label), newest first; lines: used<TAB>label
	for d in "$W"/*/; do
		[ -d "$d" ] && [ ! -L "${d%/}" ] || continue
		d=${d%/}; b=${d##*/}
		[ "$b" != "$own" ] || continue
		cand+="$(lbp_used "$W" "$LB" "$b")$tab$b"$'\n'
	done
	order=$(printf %s "$cand" | sed '/^$/d' | LC_ALL=C sort -t "$tab" -k1,1nr -k2,2)
	recent=$(printf '%s\n' "$order" | awk -v n="$nrec" 'NR <= n && NF' | cut -f2)
	while IFS=$'\t' read -r u b; do   # newest first: keep the recent ones and the ones inside the hour window
		[ -n "$b" ] || continue
		if grep -qxF -- "$b" <<<"$recent" || [ $((now - u)) -lt $((hrs * 3600)) ] || lbp_live "$W" "$b"; then kn=$((kn + 1)); kl="$kl $b"; continue; fi
		lbp_rm "$W" "$b" && { pn=$((pn + 1)); pl="$pl $b"; }
	done <<<"$order"
	free=$(lbp_free_gb "$LB")
	if [[ $free =~ ^[0-9]+$ ]] && [ "$free" -lt "$floor" ]; then   # floor: oldest first, beyond the keep-recent count; the hour window and live trees still hold
		while IFS=$'\t' read -r u b; do
			[ -n "$b" ] && [ -d "$W/$b" ] || continue
			free=$(lbp_free_gb "$LB"); [[ $free =~ ^[0-9]+$ ]] && [ "$free" -lt "$floor" ] || break
			if [ $((now - u)) -lt $((hrs * 3600)) ] || lbp_live "$W" "$b"; then continue; fi
			lbp_rm "$W" "$b" && { pn=$((pn + 1)); pl="$pl $b"; kl=${kl/ $b/}; kn=$((kn > 0 ? kn - 1 : 0)); }
		done < <(printf '%s\n' "$order" | sed '/^$/d' | tac)
		free=$(lbp_free_gb "$LB")
	fi
	[ "$pn" = 0 ] && lk="none" || lk=$(printf '%s' "${pl# }" | sed 's/ /, /g')
	echo "XNU LOCAL: pruned $pn work tree(s) ($((LBP_KB / 1048576)).$(( (LBP_KB % 1048576) * 10 / 1048576 )) GB): $lk"
	[ "$kn" = 0 ] || echo "XNU LOCAL: kept $kn recent: $(printf '%s' "${kl# }" | sed 's/ /, /g') (+ own label $own)"
	if [[ $free =~ ^[0-9]+$ ]] && [ "$free" -lt "$floor" ]; then echo "XNU LOCAL GAP: free $free GB < floor $floor GB (nothing more may be pruned; the build goes on)"; fi
	return 0
}
