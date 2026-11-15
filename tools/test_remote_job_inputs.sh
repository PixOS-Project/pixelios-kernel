# shellcheck shell=bash disable=SC2154,SC2034
# tools/test_remote_job_inputs.sh: sourced by tools/test_remote_job.sh (R13 cases of the gate-container test inputs: tools/remote-job-inputs.sh). Uses its variables (w repo ok has rj out rc dl gl).
r13_setup() {
ch=${R13_CONTAINER_HOME:-/home/user}   # the HOME inside the gate container (fixed by the run line -e HOME), not a path of this host
# R13 gate-container test inputs: a fake artifacts dir (theos toolchain + SDK, a kernel, the 0279 tree, a tiny "root image" + standard-root.txt) and a fake rsync that logs and copies
ia=$w/inart; mkdir -p "$ia/localbuild/theos/toolchain/linux/iphone/bin" "$ia/localbuild/theos/sdks/iPhoneOS16.5.sdk" "$ia/localbuild/out/k1" "$w/inroot"
for x in ld ldid vtool llvm-objdump; do printf '#!/bin/sh\n' >"$ia/localbuild/theos/toolchain/linux/iphone/bin/$x"; chmod +x "$ia/localbuild/theos/toolchain/linux/iphone/bin/$x"; done
echo sdk >"$ia/localbuild/theos/sdks/iPhoneOS16.5.sdk/x"; echo kern >"$ia/localbuild/out/k1/kernel.development.sweet"
echo rootimg >"$w/inroot/root.img"; rsha=$(sha256sum <"$w/inroot/root.img" | cut -d' ' -f1); printf '# standard root\n%s %s\n' "$rsha" "$w/inroot/root.img" >"$ia/standard-root.txt"
cat >"$w/bin/rsync-in" <<'F'
#!/bin/bash
# fake rsync for remote-job stage_inputs: logs "OPTS SRC DST" (-e X dropped), RSYNC_FAIL=PATTERN fails matching sources (rc 23), copies SRC (a dir when it ends in /) to the mapped destination
o=(); args=(); while [ $# -gt 0 ]; do case $1 in -e) shift 2 ;; -*) o+=("$1"); shift ;; *) args+=("$1"); shift ;; esac; done
echo "${o[*]} ${args[0]} ${args[1]}" >>"$FAKE_HOME/rsync-in.log"
[ -z "${RSYNC_FAIL:-}" ] || [[ ${args[0]} != *$RSYNC_FAIL* ]] || exit 23
dst=${args[1]#*:}
if [[ ${args[0]} == */ ]]; then mkdir -p "$dst"; cp -a "${args[0]}." "$dst"; elif [[ $dst == */ ]]; then mkdir -p "$dst"; cp -a "${args[0]}" "$dst"; else mkdir -p "$(dirname "$dst")"; cp -a "${args[0]}" "$dst"; fi
F
chmod +x "$w/bin/rsync-in"; export REMOTE_JOB_INPUTS_ART=$ia REMOTE_JOB_RSYNC=$w/bin/rsync-in
}
r13_tree() { mkdir -p "$w/sroot/work/xnub-art/localbuild/work/offload-gate/xnu/bsd/dev"; echo usb >"$w/sroot/work/xnub-art/localbuild/work/offload-gate/xnu/bsd/dev/sweet_usb.c"; }   # the tree the in-slot build leaves (fake); called right before the gate-pre the cases inspect
r13_cases() {
# R13: the container gets ping sockets and the staged read-only test inputs (this block runs more gate-pres: the later cases keep their run state, see the restore at its end)
_dl=$dl; _gl=$gl; _out=$out; cp "$w/sroot/work/mut-cache-root/fakeimageid/entries" "$w/entries.keep"
gi=$w/sroot/work/gate-inputs; ril=$(cat "$w/home/rsync-in.log" 2>/dev/null)
ok "R13: gate-pre run line carries --sysctl net.ipv4.ping_group_range=1000 1000 (ICMP datagram sockets for uid 1000)" "$([ "$(has '--sysctl net.ipv4.ping_group_range=1000 1000 ' "$dl")" = 1 ] && echo 1 || echo 0)"
ok "R13: run line mounts the staged theos/kernel :ro,Z and the root image at the standard-root.txt path :ro,Z, and tells the tests (DEVD_THEOS_BIN DEVD_SDK CRASH_TEST_KERNEL OVERLAY_STD_TEST_IMAGE) and the gate's own built XNU tree (:ro,Z) as CHG_STACK_TREE, placed before -w" "$([ "$(has "-v $gi/theos:/gate-inputs/theos:ro,Z" "$dl")$(has "-v $gi/kernel:/gate-inputs/kernel:ro,Z" "$dl")$(has "-v $w/sroot/work/xnub-art/localbuild/work/offload-gate/xnu:/gate-inputs/gate-xnu:ro,Z -e CHG_STACK_TREE=/gate-inputs/gate-xnu .*-w /job " "$dl")$(has "-v $gi/root/$rsha/root.img:$w/inroot/root.img:ro,Z" "$dl")$(has '-e DEVD_THEOS_BIN=/gate-inputs/theos/toolchain/linux/iphone/bin' "$dl")$(has '-e DEVD_SDK=/gate-inputs/theos/sdks/iPhoneOS16.5.sdk' "$dl")$(has '-e CRASH_TEST_KERNEL=/gate-inputs/kernel/kernel.development.sweet' "$dl")$(has "-e OVERLAY_STD_TEST_IMAGE=$w/inroot/root.img" "$dl")" = 11111111 ] && echo 1 || echo 0)"
ok "R13: inputs rsynced to offload-host work/gate-inputs (toolchain, SDK, newest kernel, root --sparse; NO chg trees), root keyed by sha256 with an ok marker, no part file left" "$([ "$(has "^-a --delete $ia/localbuild/theos/toolchain/ " "$ril")$(has "^-a --delete $ia/localbuild/theos/sdks/iPhoneOS16.5.sdk/ " "$ril")$(has "^-a $ia/localbuild/out/k1/kernel.development.sweet " "$ril")$(has 'exclude=.git' "$ril" | tr 01 10)$(has "^-a --sparse -z --partial $w/inroot/root.img .*/gate-inputs/root/$rsha/root.img.part" "$ril")" = 11111 ] && [ -f "$gi/root/$rsha/ok" ] && [ ! -e "$gi/chg-trees" ] && [ -f "$gi/root/$rsha/root.img" ] && [ ! -e "$gi/root/$rsha/root.img.part" ] && [ -x "$gi/theos/toolchain/linux/iphone/bin/ldid" ] && echo 1 || echo 0)"
ok "R13: the root image is sent ONCE: a second gate-pre re-rsyncs the small inputs but not the root (one --sparse line in the log in total)" "$(rj gate-pre; [ "$(grep -c -- '--sparse' "$w/home/rsync-in.log")" = 1 ] && echo 1 || echo 0)"
: >"$w/home/docker.log"; : >"$w/home/rsync-in.log"; rj gate-identity; ok "R13: gate-identity stages and mounts nothing (identity check only)" "$([ ! -s "$w/home/rsync-in.log" ] && grep -q '^run ' "$w/home/docker.log" && ! grep -q 'gate-inputs' "$w/home/docker.log" && echo 1 || echo 0)"
refuse_case() {   # NAME PATTERN: the last rj run was refused loudly (rc 2, REFUSED line, ERROR result), no container started
	ok "R13 $1" "$([ $rc = 2 ] && [ "$(has "REMOTE: gate-pre REFUSED, required test input: .*$2" "$out")$(has 'RESULT: ERROR remote-job gate-pre: refused: required test input' "$out")" = 11 ] && ! grep -q '^run ' "$w/home/docker.log" && echo 1 || echo 0)"
}
mv "$ia/localbuild/theos/toolchain/linux/iphone/bin/ldid" "$w/ldid.away"; : >"$w/home/docker.log"; rj gate-pre; mv "$w/ldid.away" "$ia/localbuild/theos/toolchain/linux/iphone/bin/ldid"
refuse_case "missing theos ldid: job refused loudly (no skip, no container)" "theos tool .*/ldid"
mv "$ia/localbuild/out/k1" "$w/k1.away"; : >"$w/home/docker.log"; rj gate-pre; mv "$w/k1.away" "$ia/localbuild/out/k1"
refuse_case "no local kernel build: job refused loudly" "no local kernel build"
mv "$ia/standard-root.txt" "$w/srt.away"; : >"$w/home/docker.log"; rj gate-pre; mv "$w/srt.away" "$ia/standard-root.txt"
refuse_case "no standard-root.txt: job refused loudly" "standard root: no"
: >"$w/home/docker.log"; RSYNC_FAIL=toolchain rj gate-pre
refuse_case "rsync failure of the toolchain: job refused loudly (no fallback to a run that skips)" "rsync of the theos toolchain failed"
rm -rf "$gi/root"; : >"$w/home/docker.log"; : >"$w/home/rsync-in.log"; mv "$w/inroot/root.img" "$w/inroot/root.away"; echo tampered >"$w/inroot/root.img"; rj gate-pre; mv -f "$w/inroot/root.away" "$w/inroot/root.img"
refuse_case "local root image whose sha256 differs from standard-root.txt: refused before any send" "sha256 differs"
ok "R13 sha mismatch: nothing of the root was sent" "$([ "$(grep -c -- '--sparse' "$w/home/rsync-in.log")" = 0 ] && [ ! -e "$gi/root/$rsha/ok" ] && echo 1 || echo 0)"
fgb=$(df -Pk "$w/sroot/work" | awk 'NR==2{print int($4/1048576)}'); : >"$w/home/docker.log"; : >"$w/home/rsync-in.log"
OFFLOAD_MIN_FREE_GB=$fgb rj gate-pre
refuse_case "root image that would leave < OFFLOAD_MIN_FREE_GB free is NOT staged: refused, numbers printed" "standard root image NOT staged"
ok "R13 free-space guard: the REMOTE line names need, free and the minimum; nothing of the root was sent or left" "$([ "$(has "REMOTE: gate-pre: root image $w/inroot/root.img needs 1 GiB on disk, offload-host work root has $fgb GiB free, would leave $((fgb - 1)) GiB < OFFLOAD_MIN_FREE_GB $fgb" "$out")" = 1 ] && [ "$(grep -c -- '--sparse' "$w/home/rsync-in.log")" = 0 ] && [ ! -e "$gi/root/$rsha/root.img" ] && echo 1 || echo 0)"
rj gate-pre; ok "R13: after the refusals a normal gate-pre stages the root and passes again" "$([ $rc = 0 ] && [ -f "$gi/root/$rsha/ok" ] && echo 1 || echo 0)"
# the container-side function itself (its TEXT is what the job script evals): mounts + mount points + rc 96 when a staged input vanished
mt=$(RJW=$w/sroot/work J=$w/mt RJ_ROOT_SHA=$rsha RJ_ROOT_PATH=$w/inroot/root.img bash -c 'mkdir -p "$J"; eval "$(sed -n "/^rj_input_mounts()/,\$p" tools/remote-job-inputs.sh)"; pf=(); rj_input_mounts; printf "%s\n" "${pf[*]}"' 2>&1)
ok "R13 container side: rj_input_mounts builds the :ro,Z mounts (theos kernel root = 3 -v)" "$([ "$(grep -o -- ' -v ' <<<" $mt" | wc -l)" = 3 ] && echo 1 || echo 0)"
gt=$(bash -c 'eval "$(sed -n "/^rj_input_mounts()/,\$p" tools/remote-job-inputs.sh)"; pf=(run --rm -e A=1 -w /job IMG); rj_gate_tree "$1"; printf "%s\n" "${pf[*]}"' _ "$w/sroot/work/xnub-art/localbuild/work/offload-gate/xnu" 2>&1)
ok "R13 gate tree: rj_gate_tree inserts the job's own built tree :ro,Z + CHG_STACK_TREE before -w, image stays last" "$([ "$gt" = "run --rm -e A=1 -v $w/sroot/work/xnub-art/localbuild/work/offload-gate/xnu:/gate-inputs/gate-xnu:ro,Z -e CHG_STACK_TREE=/gate-inputs/gate-xnu -w /job IMG" ] && echo 1 || echo 0)"
gt=$(bash -c 'eval "$(sed -n "/^rj_input_mounts()/,\$p" tools/remote-job-inputs.sh)"; pf=(run -w /job IMG); rj_gate_tree "$1"; printf "%s\n" "${pf[*]}"' _ "$w/no-such-xnu" 2>&1)
ok "R13 gate tree: a missing built tree is a loud WARN (the chg tests then FAIL loudly), nothing mounted, no stale tree substituted" "$([ "$(has 'WARN the gate.s own XNU tree .*no-such-xnu is missing: CHG_STACK_TREE stays unset' "$gt")$(has '^run -w /job IMG$' "$gt")" = 11 ] && echo 1 || echo 0)"
rm -rf "$gi/kernel"
mt=$(RJW=$w/sroot/work J=$w/mt RJ_ROOT_SHA=$rsha RJ_ROOT_PATH=$w/inroot/root.img bash -c 'eval "$(sed -n "/^rj_input_mounts()/,\$p" tools/remote-job-inputs.sh)"; pf=(); rj_input_mounts' 2>&1); mrc=$?
ok "R13 container side: a vanished staged input is a loud rc 96 naming it, never a skipped mount" "$([ $mrc = 96 ] && [ "$(has 'required test input .*/kernel/kernel.development.sweet missing on offload-host' "$mt")" = 1 ] && echo 1 || echo 0)"
dl=$_dl; gl=$_gl; out=$_out; cp "$w/entries.keep" "$w/sroot/work/mut-cache-root/fakeimageid/entries"
}
