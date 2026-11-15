# shellcheck shell=bash
# shellcheck disable=SC2154,SC2034   # the globals of tools/remote-job (this file is sourced by it)
# tools/remote-job-inputs.sh: sourced by tools/remote-job (R13, user 2026-11-11). Read-only TEST INPUTS of the gate container that are not in git or the image (rule 4):
# the theos iOS toolchain + SDK, the newest local kernel.development.sweet (the XNU tree for test_chg_icl/test_chg_probe is NOT staged: it is the tree the job's own in-slot build just made, mounted by rj_gate_tree as CHG_STACK_TREE) and the standard root image (path + sha256 from
# ~/pixelios-artifacts/standard-root.txt). stage_inputs rsyncs them to $OFFLOAD_ROOT/work/gate-inputs (the root keyed by its sha256, --sparse, sent once, ok marker after the
# remote sha256 check); rj_input_mounts mounts them READ-ONLY into the run line (/gate-inputs/*, the root at its standard path) and sets the tests' env
# (DEVD_THEOS_BIN DEVD_SDK CRASH_TEST_KERNEL OVERLAY_STD_TEST_IMAGE). A missing or unstageable input, or a root image that would leave < OFFLOAD_MIN_FREE_GB free,
# REFUSES the job with a loud REMOTE line (exit 2); a mount whose staged dir vanished is rc 96 in the job: never a skip, never a fallback (rule 7).
# Host-test hooks: REMOTE_JOB_INPUTS_ART (default ~/pixelios-artifacts), REMOTE_JOB_RSYNC (default rsync). Uses remote-job's globals (job rwork hl host ssh_ sshopt minfree) and die/sshc.
rj_rsync() { local rs=${REMOTE_JOB_RSYNC:-rsync}; if [ "$hl" = 1 ]; then "$rs" "$@"; else "$rs" -e "$ssh_ ${sshopt[*]}" "$@"; fi; }   # lab-host lane: the "remote" dir is local, no -e
rj_rdst() { if [ "$hl" = 1 ]; then echo "$1"; else echo "$host:$1"; fi; }
stage_inputs() {   # R13: stage the read-only test inputs of the gate container; ANY problem = REFUSED line + exit 2 (rule 7: no skip, no fallback to a run that would skip tests)
	local art=${REMOTE_JOB_INPUTS_ART:-$HOME/pixelios-artifacts} lb t k sr rsha rpath need_kb need free st gi
	lb=$art/localbuild; t=$lb/theos; gi=$rwork/gate-inputs
	refuse() { echo "REMOTE: $job REFUSED, required test input: $* (R13: a missing input is staged, never skipped)"; die "refused: required test input missing or unstageable: $*"; }
	for st in ld ldid vtool llvm-objdump; do [ -x "$t/toolchain/linux/iphone/bin/$st" ] || refuse "theos tool $t/toolchain/linux/iphone/bin/$st (tests test_pixopen/test_pixping/test_devd_stage)"; done
	[ -d "$t/sdks/iPhoneOS16.5.sdk" ] || refuse "theos SDK $t/sdks/iPhoneOS16.5.sdk"
	k=$(ls -t "$lb"/out/*/kernel.development.sweet 2>/dev/null | head -n 1); [ -f "$k" ] || refuse "no local kernel build $lb/out/*/kernel.development.sweet (test_crash_summary symbolization)"
	sr=$(grep -v '^#' "$art/standard-root.txt" 2>/dev/null | head -n 1); read -r rsha rpath _ <<<"$sr"
	[[ $rsha =~ ^[0-9a-f]{64}$ ]] && [[ $rpath =~ ^/[A-Za-z0-9._/-]+$ ]] || refuse "standard root: no '<sha256> <path>' line in $art/standard-root.txt"
	[ -f "$rpath" ] || refuse "standard root image $rpath (named by $art/standard-root.txt; test_overlay_std, test_mkdevoverlay)"
	sshc bash -s <<<'mkdir -p "$RJW/gate-inputs/theos/sdks" "$RJW/gate-inputs/kernel" "$RJW/gate-inputs/root"; rm -rf "$RJW/gate-inputs/chg-tree" "$RJW/gate-inputs/chg-trees"' >/dev/null 2>&1 || refuse "ssh mkdir of $gi failed"
	rj_rsync -a --delete "$t/toolchain/" "$(rj_rdst "$gi/theos/toolchain/")" >/dev/null 2>&1 || refuse "rsync of the theos toolchain failed"
	rj_rsync -a --delete "$t/sdks/iPhoneOS16.5.sdk/" "$(rj_rdst "$gi/theos/sdks/iPhoneOS16.5.sdk/")" >/dev/null 2>&1 || refuse "rsync of the theos SDK failed"
	rj_rsync -a "$k" "$(rj_rdst "$gi/kernel/")" >/dev/null 2>&1 || refuse "rsync of the kernel $k failed"
	st=$(sshc bash -s 2>&1 <<<"d=\"\$RJW/gate-inputs/root/$rsha\"; if [ -f \"\$d/ok\" ] && [ -f \"\$d/root.img\" ]; then echo STAGED; else echo \"FREE \$(df -Pk \"\$RJW\" | awk 'NR==2{print int(\$4/1048576)}')\"; fi" | tail -n 1)
	if [ "$st" != STAGED ]; then   # not yet on the host: free-space guard first (OFFLOAD_MIN_FREE_GB), then sha256 of the local file against standard-root.txt, send, verify there
		[[ $st =~ ^FREE\ ([0-9]+)$ ]] || refuse "free-space probe of the root image target gave '${st:0:60}'"
		free=${BASH_REMATCH[1]}; need_kb=$(du -sk "$rpath" | cut -f1); need=$(( (need_kb + 1048575) / 1048576 ))
		[ $((free - need)) -ge "$minfree" ] || { echo "REMOTE: $job: root image $rpath needs $need GiB on disk, offload-host work root has $free GiB free, would leave $((free - need)) GiB < OFFLOAD_MIN_FREE_GB $minfree"; refuse "standard root image NOT staged (free-space guard); tests test_overlay_std/test_mkdevoverlay need it. Free space on the offload host or lower OFFLOAD_MIN_FREE_GB"; }
		[ "$(sha256sum <"$rpath" | cut -d' ' -f1)" = "$rsha" ] || refuse "standard root image $rpath sha256 differs from $art/standard-root.txt ($rsha)"
		echo "remote-job: staging the standard root image ($need GiB on disk, sha256 ${rsha:0:12}) to $gi/root/${rsha:0:12}.. (once, --sparse)"
		sshc bash -s <<<"mkdir -p \"\$RJW/gate-inputs/root/$rsha\"" >/dev/null 2>&1 || refuse "ssh mkdir of the root dir failed"
		rj_rsync -a --sparse -z --partial "$rpath" "$(rj_rdst "$gi/root/$rsha/root.img.part")" >/dev/null 2>&1 || refuse "rsync of the standard root image failed"
		sshc bash -s <<<"cd \"\$RJW/gate-inputs/root/$rsha\" && [ ! -L root.img.part ] && [ \"\$(sha256sum <root.img.part | cut -d' ' -f1)\" = $rsha ] && mv -f root.img.part root.img && : >ok && find .. -mindepth 1 -maxdepth 1 ! -name $rsha -exec rm -rf {} +" >/dev/null 2>&1 || refuse "the staged root image failed its sha256 check on the offload host (part file left for the next try)"
	fi
	gi_on=$(sed -n '/^rj_input_mounts()/,$p' "$tooldir/tools/remote-job-inputs.sh"); [ -n "$gi_on" ] || refuse "rj_input_mounts missing in $tooldir/tools/remote-job-inputs.sh"; gi_rsha=$rsha; gi_rpath=$rpath
	echo "remote-job: test inputs staged ($gi: theos, kernel $(basename "$(dirname "$k")"), root ${rsha:0:12})"
}
rj_input_mounts() {   # container side, inside the gate-pre job script (its TEXT, with rj_gate_tree below it, is passed as RJ_INPUTS, like tools/xnub-line.sh; RJ_ROOT_SHA/RJ_ROOT_PATH from the runner env); appends to pf
	local im=() GI gi   # R13 read-only test inputs staged by stage_inputs under $RJW/gate-inputs; a missing one is a loud rc 96, never a skip (the tests would skip silently otherwise)
	{
		GI=$RJW/gate-inputs
		for gi in theos/toolchain/linux/iphone/bin/ld theos/sdks/iPhoneOS16.5.sdk kernel/kernel.development.sweet "root/$RJ_ROOT_SHA/root.img"; do
			[ -e "$GI/$gi" ] || { echo "gate-pre: required test input $GI/$gi missing on offload-host (R13: staged by remote-job, never skipped)"; exit 96; }
		done
		[ -f "$GI/root/$RJ_ROOT_SHA/ok" ] || { echo "gate-pre: root image $GI/root/$RJ_ROOT_SHA has no ok marker (half-staged)"; exit 96; }
		im=(-v "$GI/theos:/gate-inputs/theos:ro,Z" -v "$GI/kernel:/gate-inputs/kernel:ro,Z" -v "$GI/root/$RJ_ROOT_SHA/root.img:$RJ_ROOT_PATH:ro,Z"
			-e DEVD_THEOS_BIN=/gate-inputs/theos/toolchain/linux/iphone/bin -e DEVD_SDK=/gate-inputs/theos/sdks/iPhoneOS16.5.sdk -e CRASH_TEST_KERNEL=/gate-inputs/kernel/kernel.development.sweet -e OVERLAY_STD_TEST_IMAGE="$RJ_ROOT_PATH")
	}
	pf+=("${im[@]}")
}
rj_gate_tree() {   # $1 = the XNU tree this very job's in-slot build just made ($XA/localbuild/work/offload-gate/xnu); inserted into pf before the -w option; text travels with rj_input_mounts
	local t=$1 i n=()
	if [ ! -f "$t/bsd/dev/sweet_usb.c" ]; then echo "gate-pre: WARN the gate's own XNU tree $t is missing: CHG_STACK_TREE stays unset and test_chg_icl/test_chg_probe will FAIL loudly (R13)"; return 0; fi
	for i in "${pf[@]}"; do [ "$i" != -w ] || n+=(-v "$t:/gate-inputs/gate-xnu:ro,Z" -e CHG_STACK_TREE=/gate-inputs/gate-xnu); n+=("$i"); done
	pf=("${n[@]}")
}
