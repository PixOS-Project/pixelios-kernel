# shellcheck shell=bash
# xnub-line.sh: the ONE definition of the in-slot XNU build line on offload-host (W35 R22, review offload-xnub-r1 F6). Not run directly: its TEXT is sent to offload-host
# by both callers and evaluated there, so a validation can never drift from the gate-pre path:
#   tools/remote-job (gate-pre job script, passed as RJ_XNUB) and tools/offload-stage-xnub --validate (piped to `bash -s`).
#   xnub_argv DOCKER IMAGE ART JOBDIR NAME REPODIR   sets XF (container prefix: docker run ... IMAGE) and XB (the build command run in it).
#     ART = host dir mounted at the lab-host path $LAB_HOME/pixelios-artifacts (the toolchain, git-mirror and work trees live there),
#     JOBDIR = host checkout mounted at /job (a real git checkout: tools/gate-xnu-build needs git), NAME = container name suffix (rj-xnub-NAME),
#     REPODIR = the --repo path as the build sees it (/job).
#   HEAVY_JOB_HELD is passed by name: the caller exports it (remote-job: its own; the stager: xnub-stage). XNUB_IN_SLOT=1 is set in the container (R26): tools/xnu-local-build
#   inside it never calls the offload-host dispatcher tools/xnu-remote-build (no recursion).
#   Shared checks (also evaluated on offload-host by tools/xnu-remote-build, R26):
#   xnub_check_art PREFIX DOCKER IMAGE ART STAGEHINT   image present + XNUB_LB_REQUIRED dirs staged; sets xid; else message on stdout, return 95
#   xnub_check_mirror PREFIX ART STACKDIR STAGEHINT    (cwd = the job checkout holding tools/xnu-src-mirror; XF set by xnub_argv) every pinned source in ART/git-mirror, checked INSIDE the build
#                                                      container (same image, uid, git and mounts as the build: review offload-xnub-r2 F1, AP-270, no commit-controlled code on the offload-host host);
#                                                      return 95 (mirror missing/incomplete, or the check could not run: the last 6 lines of its output are printed), 98 (no tools/xnu-src-mirror, or
#                                                      it reports RESULT: ERROR: the commit's stagers cannot be parsed, staging cannot fix that)
#   xnub_prune WORKDIR KEEP...                         guarded prune of old work trees under WORKDIR except KEEP and the XNUB_KEEP_RECENT (default 2) most recently used other trees (alternating
#                                                      labels stay warm); prints `XNUB: pruned N work tree(s): ...` (+ `XNUB: kept N recent work tree(s): ...`); return 98 when WORKDIR is not the xnub-art work dir
# Directories of $ART/localbuild that xnu-local-build needs and must therefore be staged (checked by remote-job: rc 95; staged by tools/offload-stage-xnub);
# XNUB_LB_EXTRA is staged too but not required by this check (nothing in tools/xnu-local-build names it; asked in the review, not decided).
# shellcheck disable=SC2034 # read by the scripts that evaluate this text
XNUB_LB_REQUIRED="sdk theos deps hostroot dxb"
# shellcheck disable=SC2034 # read by the scripts that evaluate this text
XNUB_LB_EXTRA="u1"
XNUB_CART=${XNUB_CART:-${LAB_HOME:-/home/user}/pixelios-artifacts}   # where the container sees the artifacts (LAB_HOME = the lab-host home, tools/offload-host.conf; the shippers prepend LAB_HOME=..: the toolchain, git-mirror and work trees are addressed by it)
xnub_argv() {
	XF=(nice -n 19 "$1" run --rm --name "rj-xnub-$5" --network=none --userns=keep-id --user 1000:1000 -e HOME=/home/user -e HEAVY_JOB_HELD -e XNUB_IN_SLOT=1 -e "ARTIFACTS=$XNUB_CART" -v "$3:$XNUB_CART:rw,Z" -v "$4:/job:rw,Z" -w /job "$2")
	XB=(bash tools/gate-xnu-build --repo "$6" --always --label offload-gate --jobs 4)
}
xnub_check_art() {
	local t
	xid=none
	xid=$("$2" image inspect --format "{{.Id}}" "$3" 2>/dev/null) && [ -n "$xid" ] || { xid=none; echo "$1: XNU image $3 missing on offload-host: $5"; return 95; }
	for t in $XNUB_LB_REQUIRED; do [ -d "$4/localbuild/$t" ] || { echo "$1: XNU toolchain $4/localbuild/$t missing on offload-host: $5"; return 95; }; done
}
xnub_check_mirror() {
	local mx mrc mrx="RESULT: PASS mirrored=([0-9]+) of ([0-9]+) " tl
	[ "${#XF[@]}" -gt 0 ] || { echo "$1: internal error: xnub_check_mirror needs the container prefix XF (call xnub_argv first)"; return 98; }
	[ -f tools/xnu-src-mirror ] || { echo "$1: REFUSED rc 98: the commit under test has no tools/xnu-src-mirror, the offline XNU build cannot fetch its pinned sources (needs the tools/xnub-mirror change in the commit)"; return 98; }
	# in the build container (image git, uid 1000, --network=none, the artifacts mount): the host-side probe judged "dubious ownership" with the wrong uid/git (AP-270); stdout is the eval text, never used
	mx=$("${XF[@]}" bash tools/xnu-src-mirror env --stack "$3" --dir "$XNUB_CART/git-mirror" 2>&1 >/dev/null); mrc=$?
	tl=$(tail -n 6 <<<"$mx" | tr "\n" ";")
	if grep -q "^RESULT: ERROR" <<<"$mx"; then echo "$1: REFUSED rc 98: tools/xnu-src-mirror cannot derive the pinned sources of this commit (the stagers of $3 do not parse; staging cannot fix that): $tl"; return 98; fi
	{ [[ $mx =~ $mrx ]] && [ "${BASH_REMATCH[1]}" = "${BASH_REMATCH[2]}" ] && [ "${BASH_REMATCH[2]}" -gt 0 ]; } || { echo "$1: XNU git mirror $2/git-mirror missing or incomplete in the build container (check rc $mrc: $tl): $4"; return 95; }
}
xnub_prune() {
	local W=$1 d b k pn=0 pl="" nrec=${XNUB_KEEP_RECENT:-2} recent="" kn=0 kl=""
	shift
	[[ $nrec =~ ^[0-9]+$ ]] || nrec=2
	# the guard is tested by calling this function with a foreign dir (tools/test_xnu_remote_build.sh): callers pass the constant today, a future caller must not delete elsewhere
	case $W in "${RJW:?RJW (the offload-host work root) is not set}"/xnub-art/localbuild/work) ;; *) echo "XNUB: REFUSING to prune $W: not under $RJW/xnub-art/localbuild/work"; return 98 ;; esac
	if [ -L "$W" ]; then echo "XNUB: WARNING work dir $W is a symlink, prune skipped"
	elif [ -d "$W" ]; then
		# the nrec most recently used other trees stay (review xnu-remote-build-r1 F12: two drivers alternating labels must not rebuild cold); used = newest mtime of the tree or its build.log
		# (a rebuild rewrites build.log without touching the directory) [guess: heuristic, the only per-tree timestamp the build leaves]
		recent=$(for d in "$W"/*/; do
			[ -d "$d" ] && [ ! -L "${d%/}" ] || continue
			d=${d%/}; b=${d##*/}
			for k in "$@"; do [ "$b" != "$k" ] || continue 2; done
			printf '%s\t%s\n' "$(stat -c %Y "$d" "$d/build.log" 2>/dev/null | sort -rn | head -n 1)" "$b"
		done | LC_ALL=C sort -t "$(printf '\t')" -k1,1nr -k2,2 | head -n "$nrec" | cut -f2)
		for d in "$W"/*/; do   # not dot-files (locks, logs); the offload-host disk is shared and small
			[ -d "$d" ] || continue   # empty work/: the unmatched glob stays literal, it is not a work tree (review r1 F2)
			d=${d%/}; b=${d##*/}
			for k in "$@"; do [ "$b" != "$k" ] || continue 2; done
			if grep -qxF -- "$b" <<<"$recent"; then kn=$((kn + 1)); kl="$kl $b"; continue; fi
			[ -n "$b" ] && [ "${d#"$W"/}" = "$b" ] || { echo "XNUB: WARNING skipped odd work entry $d"; continue; }   # never outside work/ (review r1 F4)
			[ ! -L "$d" ] || { echo "XNUB: WARNING work entry $d is a symlink, not removed"; continue; }
			# $d is a non-empty directory name directly under $W here (the checks above), so no ${d:?} (it could never fire: review offload-xnub-r2 F4)
			if rm -rf -- "$d"; then pn=$((pn + 1)); pl="$pl $b"; else echo "XNUB: WARNING could not remove work tree $d"; fi
		done
	fi
	echo "XNUB: pruned $pn work tree(s):${pl:- none}"
	[ "$kn" = 0 ] || echo "XNUB: kept $kn recent work tree(s):$kl"
}
