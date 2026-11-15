#!/bin/bash
# Host test of tools/xnu-remote-build (R26: every XNU build goes to offload-host, loud lab-host fallback, built-on ledger) with a fake ssh/rsync/docker and the REAL tools/heavy-job offload-host queue
# (its probe runs under a local `bash -s`); no network, no real host, no phone, no real XNU build. The sandbox repo (a real git repo, for the provenance rows) gets stub tools/xnu-local-build
# (plays the in-container build when XNUB_IN_SLOT=1, the lab-host build otherwise), stub tools/xnu-src-mirror, and the REAL xnu-remote-build, xnub-line.sh, heavy-job, gate-xnu-build.
# Run by the gate through tools/test_xnu_remote_build.py (unit step). Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# MODELLED: the remote command string is exactly `bash -s` (a fish-like login shell trap: anything else is rejected with rc 127, as fish rejects bash syntax), the remote part runs under
#   `env -i` with only HOME/PATH (sshd's clean environment, AP-225), rc 255 after the build started, the queue (real heavy-job FIFO + admission), corrupt copy-back of either kernel.
# UNVERIFIED until the offload-host `tools/heavy-job offload-host run -- tools/offload-stage-xnub --validate` staging run (and the first real XNU_BUILD_WHERE=offload-host build): the real fish login shell, the real
#   container uid / --userns=keep-id ownership of the git mirror and of the rsynced job dir (a non-writable mirror for another uid cannot be built on this host without root), --network=none,
#   podman semantics of `-e VAR` / `--rm --name`, the real xnu-src-mirror and xnu-local-build inside the container, rsync/ssh option behaviour, disk-full and permission failures on offload-host.
# Mutants (observed 2026-10-29, batch-20u3 fixes): built-in, run by this script itself unless XRB_MUTANT=1: nosha, silent-fallback, norecursion-guard, k2skip, nokill, noreported. Each must make the suite
#   FAIL. Hand mutants, each applied to a COPY under /tmp/20z/mut/<name>/ (never to the checkout) and run as `XRB_MUTANT=1 XRB_TOOLS_DIR=<copy> bash tools/test_xnu_remote_build.sh`, all gave RESULT: FAIL
#   (review xnu-remote-build-r1 F2 names the first 8 as survivors of the old suite): k1skip (verify only the work-tree copy), labelregex (any --label accepted), jobscap (--jobs not capped at 4),
#   nopass-check (rc 0 without RESULT: PASS accepted), nosha-line (PASS without sha256 line accepted), unreach-grep (queue-time `offload-host unreachable` not a fallback), ledger-warn (ledger failure silent
#   again), rm-stale (F9: old kernels kept), nodirty (F4: no DIRTY mark), nolockorder (F10), noadmit-fallback (F8), nobusy-fallback (F3 busy queue under the lab-host lock), nowait-derive (F3 wait not
#   derived from GATE_STEP_BUDGET), norecent (F12), noprune-guard (xnub_prune guard). Kill points (cases run before the first failure) were 5 to 91, none at case 1 (a parse error would stop there).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2
here=$PWD
export OFFLOAD_CONF=$here/tools/offload-host.conf.example   # T-dd93: the committed placeholder host config, never the real one
export LAB_HOME=/home/user
src=${XRB_TOOLS_DIR:-$here/tools}   # a mutant run points this at a mutated copy
w=$(mktemp -d) || exit 2
cleanup() { [ -z "${XRB_KEEP:-}" ] || cp -r "$w" /tmp/dd93/xrbw 2>/dev/null; [ -z "${hp:-}" ] || kill -- "-$hp" 2>/dev/null; rm -rf "$w"; }
trap cleanup EXIT
unset HEAVY_JOB_DETACHED HEAVY_JOB_DETACHED_WAIT HEAVY_JOB_NOTRUN_FILE OFFLOAD_MIN_FREE_GB HEAVY_JOB_HELD HEAVY_JOB_QUIET OFFLOAD_QUEUE_HELD XNUB_IN_SLOT XNU_BUILD_WHERE XNU_REMOTE_WAIT XNU_LEDGER_WHERE XNU_LEDGER_REASON LOCALBUILD
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; [ -z "${XRB_DEBUG:-}" ] || { echo "---- rc=${rc:-?} stdout:"; tail -n 8 <<<"${out:-}"; echo "---- stderr:"; tail -n 8 <<<"${err:-}"; echo "----"; }; bad=$((bad + 1)); [ "${XRB_MUTANT:-}" != 1 ] || { echo "RESULT: FAIL cases=$n failed=$bad (mutant run: stopped at the first failure)"; exit 1; }; fi; }
has() { grep -q -- "$1" <<<"$2" && echo 1 || echo 0; }
RSYNC=$(PATH=${XRB_ORIG_PATH:-$PATH} command -v rsync) || { echo "RESULT: ERROR rsync not installed"; exit 2; }

repo=$w/repo; fh=$w/home; export OFFLOAD_ROOT=$fh/sroot   # R64: the fake offload-host work root is $OFFLOAD_ROOT/work
xa=$fh/sroot/work/xnub-art; art=$w/art; lb=$art/localbuild
mkdir -p "$repo/tools" "$repo/xnu/patches" "$w/bin" "$w/run" "$w/q" "$fh/.local/bin" "$art"
for t in xnu-remote-build xnub-line.sh heavy-job heavy-job-canon.sh gate-xnu-build offload-conf.sh offload_conf.py; do cp "$src/$t" "$repo/tools/$t"; done
echo 'patch-one' >"$repo/xnu/patches/0001.patch"; echo 'stage' >"$repo/xnu/patches/0002.sh"
# stub of tools/xnu-local-build: XNUB_IN_SLOT=1 = the build inside the (fake) offload-host container; else the lab-host build that tools/xnu-remote-build exec's as a fallback/skip
cat >"$repo/tools/xnu-local-build" <<'S'
#!/bin/bash
# test stub of tools/xnu-local-build (uses tools/xnu-src-mirror). Flags in $FAKE_HOME (the fake offload-host, container only): berror bfail bsha bslow bnopass bnosha
ALL="$*"
stack=; label=main; while [ $# -gt 0 ]; do case $1 in --stack) stack=$2; shift 2 ;; --label) label=$2; shift 2 ;; *) shift ;; esac; done
if [ -z "${CART:-}" ]; then   # CART is set by the fake docker only: no CART = the lab-host build (also writes the kernel, so gate-xnu-build can read it)
	echo "LOCAL-STUB where=${XNU_LEDGER_WHERE:-none} reason=${XNU_LEDGER_REASON:-none} build_where=${XNU_BUILD_WHERE:-unset} args=$ALL"
	L=${LOCALBUILD:-$ARTIFACTS/localbuild}; mkdir -p "$L/out/$label" "$L/work/$label/fakeroot/System/Library/Kernels"
	{ cat "$stack"/*.patch; echo "K-$label-lab-host"; } >"$L/out/$label/kernel.development.sweet"; cp "$L/out/$label/kernel.development.sweet" "$L/work/$label/fakeroot/System/Library/Kernels/kernel.development.sweet"
	echo "RESULT: PASS kernel.development.sweet built locally in 1s${XNU_RESULT_EXTRA:+ $XNU_RESULT_EXTRA}"; echo "kernel: $L/out/$label/kernel.development.sweet"; echo "sha256: $(sha256sum "$L/out/$label/kernel.development.sweet" | cut -d' ' -f1)"; exit 0
fi
echo "slot-env XNUB_IN_SLOT=$XNUB_IN_SLOT HEAVY_JOB_HELD=${HEAVY_JOB_HELD:-} args=$ALL pwd=$PWD tools=$([ -f tools/xnu-remote-build ] && [ -f tools/xnub-line.sh ] && echo shipped) stackfiles=$(ls "$stack" | tr '\n' ,)" >>"$ARTIFACTS/stub-env"
echo "[1s] stub progress" >&2
[ ! -e "$FAKE_HOME/bslow" ] || sleep 3
[ ! -e "$FAKE_HOME/berror" ] || { echo "RESULT: ERROR stub toolchain broke"; exit 2; }
[ ! -e "$FAKE_HOME/bfail" ] || { echo "RESULT: FAIL build failed (3 error lines, make exit 2, 5s)"; echo "bsd/x.c:9:1: error: unknown type name 'foo'" >&2; exit 1; }
L=$ARTIFACTS/localbuild; CL=$CART/localbuild; mkdir -p "$L/out/$label" "$L/work/$label/fakeroot/System/Library/Kernels" "$L/work/old1" "$L/work/old2" "$L/work/old3" "$L/work/old4" "$L/work/offload-gate" "$L/work/.keep.lock"
touch -d 2020-01-01 "$L/work/old1"; touch -d 2021-01-01 "$L/work/old2"; touch -d 2022-01-01 "$L/work/old3"; touch -d 2023-01-01 "$L/work/old4"   # old4 = most recently used
cat "$stack"/*.patch >"$L/out/$label/kernel.development.sweet"; echo "K-$label" >>"$L/out/$label/kernel.development.sweet"
cp "$L/out/$label/kernel.development.sweet" "$L/work/$label/fakeroot/System/Library/Kernels/kernel.development.sweet"
echo "make log" >"$L/work/$label/build.log"
sha=$(sha256sum "$L/out/$label/kernel.development.sweet" | cut -d' ' -f1); [ ! -e "$FAKE_HOME/bsha" ] || sha=$(printf x | sha256sum | cut -d' ' -f1)
if [ -e "$FAKE_HOME/bnopass" ]; then echo "RESULT: SKIPPED weird"; echo "sha256: $sha"; exit 0; fi
echo "RESULT: PASS kernel.development.sweet built locally in 1s"; echo "kernel: $CL/out/$label/kernel.development.sweet"; [ -e "$FAKE_HOME/bnosha" ] || echo "sha256: $sha"; echo "size: 5 bytes"; echo "log: $CART/scratch/xnu-local-build.log"
S
cat >"$repo/tools/xnu-src-mirror" <<'M'
#!/bin/bash
# test stub of tools/xnu-src-mirror env (real contract: stdout empty, NOT mirrored lines + RESULT on stderr, rc 0)
d=; while [ $# -gt 0 ]; do case $1 in --dir) d=$2; shift 2 ;; *) shift ;; esac; done
echo "$d" >>"$FAKE_HOME/mirror-dirs"
[ ! -e "$FAKE_HOME/bmirrorerror" ] || { echo "RESULT: ERROR no single fetch in 0019-x.sh" >&2; exit 2; }   # the stagers of this commit do not parse
m=0; for n in libpthread hfs BearSSL; do if [ -d "$d/$n.git" ]; then m=$((m + 1)); else echo "xnu-src-mirror: NOT mirrored: $n 0000" >&2; fi; done
echo "RESULT: PASS mirrored=$m of 3 dir=$d" >&2
M
chmod +x "$repo/tools/"*
printf '#!/bin/bash\nexec "$(dirname "$0")/xnu-remote-build" "$@"\n' >"$repo/tools/xlb-remote"; chmod +x "$repo/tools/xlb-remote"   # what GATE_XNU_LOCAL_BUILD runs in the gate-xnu-build row
git -C "$repo" init -q && git -C "$repo" add -A && git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init   # a real checkout: the provenance rows (head, dirty) read it
reset_offload() { rm -rf "$fh/work" "$fh/docker.log" "$fh"/b* "$fh/noimage" "$fh/mirror-dirs" "$w/ssh-down" "$w/ssh-down-after1" "$w/ssh-drop" "$w/kill-slot" "$w/rsync-fail" "$w/rs-corrupt-out" "$w/rs-corrupt-work" "$w/ssh.log.cmds"; mkdir -p "$xa/localbuild/work" "$xa/git-mirror"; for d in sdk theos deps hostroot dxb; do mkdir -p "$xa/localbuild/$d"; done; for d in libpthread hfs BearSSL; do mkdir -p "$xa/git-mirror/$d.git"; done; : >"$w/ssh.log"; : >"$fh/docker.log"; }
cat >"$w/bin/ssh" <<'F'
#!/bin/bash
# fake ssh: drop the options and the host; the remote command string must be exactly `bash -s` (the offload-host login shell is fish: any other string is parsed by fish, which rejects bash syntax,
# so this trap answers rc 127 like fish would); the remote part runs under `env -i` with only HOME and PATH (sshd's clean environment, AP-225) and the stdin of the caller.
# Modes (files): ssh-down = refuse every call (rc 255); ssh-down-after1 = refuse from the 2nd call on (the precheck passes, the queue probe does not); ssh-drop = the BUILD ssh (the one whose
# script prints `XNUB: build rc`) runs the script and then answers rc 255 (connection lost after the build started); kill-slot = SIGTERM the caller (the slot) while the build ssh runs.
echo "ssh" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
shift
echo "$*" >>"$FAKE_LOG.cmds"
[ "$*" = "bash -s" ] || { echo "fish: Unsupported use of '$*' (the fake offload-host login shell is fish: send \`bash -s\` and the script on stdin)" >&2; exit 127; }
[ ! -e "$FAKE_DOWN" ] || { echo "ssh: connect to host fake.invalid port 22: Connection refused" >&2; exit 255; }
if [ -e "$FAKE_DOWN1" ] && [ "$(grep -c '^ssh' "$FAKE_LOG")" -gt 1 ]; then echo "ssh: connect to host fake.invalid port 22: Connection refused" >&2; exit 255; fi
scr=$(cat); bs=; grep -q 'XNUB: build rc' <<<"$scr" && bs=1
if [ -n "$bs" ] && [ -e "$FAKE_KILLSLOT" ]; then ( sleep 1; kill -TERM "$PPID" ) 2>/dev/null & fi
if [ -n "$bs" ] && [ -e "$FAKE_DROP" ]; then env -i HOME="$FAKE_HOME" PATH="$PATH" FAKE_HOME="$FAKE_HOME" "$@" <<<"$scr"; exit 255; fi
env -i HOME="$FAKE_HOME" PATH="$PATH" FAKE_HOME="$FAKE_HOME" "$@" <<<"$scr"
F
cat >"$w/bin/rsync" <<'F'
#!/bin/bash
# fake rsync: drop -e CMD, map fake.invalid:PATH to $FAKE_HOME/PATH, run the real rsync; $FAKE_RSYNC_FAIL = an existing file makes the transfers of tools/ fail
args=(); while [ $# -gt 0 ]; do case $1 in -e) shift 2 ;; fake.invalid:/*) args+=("${1#*:}"); shift ;; fake.invalid:*) args+=("$FAKE_HOME/${1#*:}"); shift ;; *) args+=("$1"); shift ;; esac; done
echo "rsync ${args[*]}" >>"$FAKE_LOG"
if [ -e "$FAKE_RSYNC_FAIL" ] && [[ "${args[*]}" == *"/tools/"* ]]; then echo "rsync: connection unexpectedly closed" >&2; exit 12; fi
mkdir -p "${args[-1]%/*}" 2>/dev/null
"$REAL_RSYNC" "${args[@]}" || exit $?
# corrupt-copy modes: the kernel that came back is altered in ONE of the two destinations (out/<label>/ or work/<label>/fakeroot/...)
if [ -e "$FAKE_RS_CORRUPT_OUT" ] && [[ "${args[-1]}" == */out/* ]]; then echo corrupt >>"${args[-1]%/}/kernel.development.sweet"; fi
if [ -e "$FAKE_RS_CORRUPT_WORK" ] && [[ "${args[-1]}" == */fakeroot/* ]]; then echo corrupt >>"${args[-1]%/}/kernel.development.sweet"; fi
exit 0
F
cat >"$fh/.local/bin/docker" <<'F'
#!/bin/bash
# fake docker: image inspect / kill / ps; `run` mimics the container: env -i + the -e vars, ARTIFACTS = the -v source of the artifacts mount, /job = the -v source of /job
echo "$*" >>"$FAKE_HOME/docker.log"
case $1 in
	image) [ -e "$FAKE_HOME/noimage" ] && exit 1; echo sha256:fakeimageid0123456789; exit 0 ;;
	kill) exit 0 ;;
	ps) exit 0 ;;
	run) shift ;;
	*) exit 0 ;;
esac
ev=(); jd=; art=
while [ $# -gt 0 ]; do case $1 in
	--rm|--network=*|--userns=*) shift ;;
	--name|--user|-w) shift 2 ;;
	-e) if [[ $2 == *=* ]]; then ev+=("$2"); else ev+=("$2=${!2}"); fi; shift 2 ;;
	-v) [[ $2 == *:/job:* ]] && jd=${2%%:/job:*}; [[ $2 == *:/home/user/pixelios-artifacts:* ]] && art=${2%%:/home/user/pixelios-artifacts:*}; shift 2 ;;
	*) break ;;
esac; done
shift   # image
a=(); for x in "$@"; do [[ $x == /job/* ]] && x=$jd${x#/job}; x=${x/#\/home\/user\/pixelios-artifacts/$art}; a+=("$x"); done   # /job and the artifacts mount (the --dir of the mirror check)
cd "$jd" || exit 9
cart=; for e in "${ev[@]}"; do [[ $e == ARTIFACTS=* ]] && cart=${e#ARTIFACTS=}; done   # the path the real container sees (the stub prints it, like the real report)
env -i PATH="$PATH" HOME="$FAKE_HOME" FAKE_HOME="$FAKE_HOME" "${ev[@]}" ARTIFACTS="$art" CART="$cart" "${a[@]}"
F
chmod +x "$w/bin/ssh" "$w/bin/rsync" "$fh/.local/bin/docker"
export FAKE_HOME=$fh FAKE_LOG=$w/ssh.log FAKE_DOWN=$w/ssh-down FAKE_DOWN1=$w/ssh-down-after1 FAKE_DROP=$w/ssh-drop FAKE_KILLSLOT=$w/kill-slot FAKE_RSYNC_FAIL=$w/rsync-fail REAL_RSYNC=$RSYNC
export FAKE_RS_CORRUPT_OUT=$w/rs-corrupt-out FAKE_RS_CORRUPT_WORK=$w/rs-corrupt-work
export REMOTE_JOB_SSH=$w/bin/ssh REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_DIR=$w/q OFFLOAD_REPROBE=1 OFFLOAD_MAXLOAD1=1000000 OFFLOAD_MINAVAIL_GB=0 OFFLOAD_FOREIGN_RE=xrb-test-never-matches-9f3a1c
export ARTIFACTS=$art
ORIG_PATH=$PATH
export PATH=$w/bin:$PATH   # the fake rsync first: tools/xnu-remote-build calls plain `rsync`
xrb() { ( cd "$w/run" && "$repo/tools/xnu-remote-build" "$@" >"$w/out" 2>"$w/err" </dev/null ); rc=$?; out=$(cat "$w/out"); err=$(cat "$w/err"); }
ledger_n() { wc -l <"$lb/built-on.tsv" 2>/dev/null || echo 0; }
sshn() { grep -c '^ssh' "$w/ssh.log"; }

# ---- 1. offload-host PASS (nested in a lab-host gate: HEAVY_JOB_HELD set is irrelevant to the offload-host slot)
reset_offload
HEAVY_JOB_HELD=lab-host-gate xrb --stack "$repo/xnu/patches" --label t1 --jobs 2 --fresh
want=$(cat "$repo/xnu/patches/0001.patch"; echo K-t1); wsha=$(printf '%s\n' "$want" | sha256sum | cut -d' ' -f1)
ok "offload-host PASS inside HEAVY_JOB_HELD: exit 0, first line XNU REMOTE: built on offload-host in Ns (label, image id)" "$([ $rc = 0 ] && [[ "$(head -n 1 <<<"$out")" =~ ^XNU\ REMOTE:\ built\ on\ offload-host\ in\ [0-9]+s\ \(label\ t1,\ xnub\ image\ fakeimageid0\) ]] && echo 1 || echo 0)"
ok "offload-host PASS: report lines RESULT: PASS / kernel: (local path) / sha256: (64 hex) / log: (local), the last RESULT: line is the PASS" "$([ "$(grep -c '^RESULT: ' <<<"$out")" = 1 ] && echo "$(has '^RESULT: PASS kernel.development.sweet' "$out")$(has "^kernel: $lb/out/t1/kernel.development.sweet\$" "$out")$(has '^sha256: [0-9a-f]\{64\}$' "$out")$(has "^log: $art/scratch/xnu-remote-build.t1.log\$" "$out")" | grep -q 1111 && echo 1 || echo 0)"
ok "offload-host PASS: both kernels copied back (out/ and work/<label>/fakeroot/...), sha256 equal the reported one" "$([ "$(sha256sum "$lb/out/t1/kernel.development.sweet" | cut -d' ' -f1)" = "$wsha" ] && [ "$(sha256sum "$lb/work/t1/fakeroot/System/Library/Kernels/kernel.development.sweet" | cut -d' ' -f1)" = "$wsha" ] && has "^sha256: $wsha\$" "$out" || echo 0)"
dl=$(cat "$fh/docker.log"); se=$(cat "$xa/stub-env" 2>/dev/null)
ok "offload-host PASS: the build ran on the fake offload-host: container line with XNUB_IN_SLOT=1 and no device, HEAVY_JOB_HELD=xnub-remote, flags + stack + the current tools/ reached it" "$(echo "$(has 'run --rm --name rj-xnub-xr-t1-[0-9]* --network=none --userns=keep-id --user 1000:1000 -e HOME=/home/user -e HEAVY_JOB_HELD -e XNUB_IN_SLOT=1' "$dl")$(has ' pixos-xnub:trixie-20261111 bash tools/xnu-local-build --stack /job/stack --label t1' "$dl")$(has 'XNUB_IN_SLOT=1 HEAVY_JOB_HELD=xnub-remote args=--stack .*/stack --label t1 --jobs 2 --fresh pwd=.* tools=shipped stackfiles=0001.patch,0002.sh,$' "$se")$(has '--device\|--privileged' "$dl")" | grep -q '^1110$' && echo 1 || echo 0)"
ok "offload-host PASS: XNUB: pruned 2 work tree(s) (old1 old2: not the label, not offload-gate, not dot dirs) shown on the console" "$(has '^XNUB: pruned 2 work tree(s): old1 old2$' "$out")"
ok "offload-host PASS: teardown removed the job dir on offload-host, the stub's work trees old1/old2 are gone, label tree stays" "$([ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && [ ! -d "$xa/localbuild/work/old1" ] && [ -d "$xa/localbuild/work/t1" ] && [ -d "$xa/localbuild/work/offload-gate" ] && echo 1 || echo 0)"
ok "offload-host PASS: ledger line date<TAB>sha256<TAB>label<TAB>offload-host<TAB>reason" "$([ "$(ledger_n)" = 1 ] && awk -F'\t' -v s="$wsha" 'NF==5 && $1 ~ /^20[0-9-]+T[0-9:]+Z$/ && $2==s && $3=="t1" && $4=="offload-host" {f=1} END{exit !f}' "$lb/built-on.tsv" && echo 1 || echo 0)"
ok "ledger-line for that kernel: XNU BUILD: offload-host (label t1) reason: head=.. dirty=0 stack=..; an unknown kernel: unknown (no ledger entry)" "$(l1=$("$repo/tools/xnu-remote-build" ledger-line "$lb/out/t1/kernel.development.sweet"); echo hello >"$w/other.k"; l2=$("$repo/tools/xnu-remote-build" ledger-line "$w/other.k"); [[ $l1 =~ ^XNU\ BUILD:\ offload-host\ \(label\ t1\)\ reason:\ head=[0-9a-f]{12}\ dirty=0\ stack=[0-9a-f]{12}$ ]] && [ "$l2" = "XNU BUILD: unknown (no ledger entry)" ] && echo 1 || echo 0)"
hd=$(git -C "$repo" rev-parse --short=12 HEAD); sh12=$(cd "$repo/xnu/patches" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)
ok "offload-host PASS (F4): the RESULT: PASS line carries where=offload-host head=<git short HEAD> dirty=0 stack=<sha256 of the stack files, 12 hex> (independent computation), no DIRTY" "$([ "$(has "^RESULT: PASS .* where=offload-host head=$hd dirty=0 stack=$sh12\$" "$out")" = 1 ] && echo 1 || echo 0)"
ok "offload-host PASS (F4): the ledger reason column is head=.. dirty=0 stack=.. (same values)" "$([ "$(awk -F'\t' '{print $5}' "$lb/built-on.tsv" | tail -n 1)" = "head=$hd dirty=0 stack=$sh12" ] && echo 1 || echo 0)"
ok "offload-host PASS (F5/r2 F1): the pinned-source mirror check ran INSIDE the container (docker run line with bash tools/xnu-src-mirror env --stack stack --dir /home/user/pixelios-artifacts/git-mirror), on the artifacts mount" "$([ "$(has 'run .* pixos-xnub:trixie-20261111 bash tools/xnu-src-mirror env --stack stack --dir /home/user/pixelios-artifacts/git-mirror' "$dl")" = 1 ] && [ "$(cat "$fh/mirror-dirs")" = "$xa/git-mirror" ] && echo 1 || echo 0)"
ok "offload-host PASS (F2): teardown killed the container by name (docker kill rj-xnub-xr-t1-<pid>) on a normal exit too" "$([ "$(has '^kill rj-xnub-xr-t1-[0-9]*$' "$dl")" = 1 ] && echo 1 || echo 0)"
ok "offload-host PASS (F11): every ssh went out as the exact command string 'bash -s' (fish-safe), at least 3 of them (precheck, prep, build)" "$([ "$(sort -u "$w/ssh.log.cmds")" = "bash -s" ] && [ "$(wc -l <"$w/ssh.log.cmds")" -ge 3 ] && echo 1 || echo 0)"
ok "offload-host PASS (F12): prune kept the 2 most recently used other work trees (old3 old4), removed old1 old2, reported both" "$([ -d "$xa/localbuild/work/old3" ] && [ -d "$xa/localbuild/work/old4" ] && [ "$(has '^XNUB: kept 2 recent work tree(s): old3 old4$' "$out")" = 1 ] && echo 1 || echo 0)"
reset_offload
xrb --stack "$repo/xnu/patches" --label t1b
ok "offload-host PASS without HEAVY_JOB_HELD, defaults (no --jobs/--fresh): exit 0, jobs 4" "$([ $rc = 0 ] && has 'args=--stack .*/stack --label t1b --jobs 4 pwd=' "$(cat "$xa/stub-env")" | grep -q 1 && echo 1 || echo 0)"
reset_offload
OFFLOAD_QUEUE_HELD=outer xrb --stack "$repo/xnu/patches" --label t1c
ok "already inside a offload-host slot (OFFLOAD_QUEUE_HELD): nested, no second ticket, still builds on offload-host" "$([ $rc = 0 ] && echo "$(has 'nested in outer, no second ticket' "$err")$(has '^XNU REMOTE: built on offload-host' "$out")" | grep -q 11 && echo 1 || echo 0)"

# ---- 2. sha mismatch, compile FAIL, remote ERROR (no fallback in any of them)
reset_offload; touch "$fh/bsha"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2
ok "sha mismatch: RESULT: FAIL xnu-remote-build (copied kernel ... sha256), exit 1, no PASS line, no ledger entry" "$([ $rc = 1 ] && echo "$(has '^RESULT: FAIL xnu-remote-build: copied kernel .* the remote build printed' "$out")$(has '^RESULT: PASS' "$out")$(has 'LOCAL-STUB' "$out")" | grep -q '^100$' && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
reset_offload; touch "$fh/bfail"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t3
ok "compile FAIL on offload-host: exit 1, RESULT: FAIL line from the build, XNU REMOTE: FAILED, error excerpt, NO fallback, no ledger entry" "$([ $rc = 1 ] && echo "$(has '^RESULT: FAIL build failed (3 error lines' "$out")$(has '^XNU REMOTE: FAILED on offload-host' "$out")$(has "error: unknown type name" "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")" | grep -q '^1110$' && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
ok "compile FAIL: teardown still ran (no job dir left on offload-host)" "$([ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
reset_offload; touch "$fh/berror"
xrb --stack "$repo/xnu/patches" --label t3e
ok "remote build ERROR (rc 2) after it started: exit 2, loud ERROR, no fallback" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR xnu-remote-build: the remote build ended rc 2' "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")" | grep -q '^10$' && echo 1 || echo 0)"

# F2 (a): only ONE of the two copied kernels corrupt -> each is verified (k1skip/k2skip mutants)
reset_offload; touch "$w/rs-corrupt-work"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2w
ok "corrupt work-tree copy only: RESULT: FAIL naming the work-tree kernel, exit 1, no PASS line, no ledger entry" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL xnu-remote-build: copied kernel .*/work/t2w/fakeroot/.* the remote build printed' "$out")$(has '^RESULT: PASS' "$out")" = 10 ] && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
reset_offload; touch "$w/rs-corrupt-out"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2o
ok "corrupt out/ copy only: RESULT: FAIL naming the out/ kernel, exit 1, no ledger entry" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL xnu-remote-build: copied kernel .*/out/t2o/.* the remote build printed' "$out")" = 1 ] && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
# F2 (b): connection lost after the build started: ERROR, never a fallback; the container is killed by name
reset_offload; touch "$w/ssh-drop"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2d
ok "ssh rc 255 after the build started: exit 2, ERROR 'ended rc 255 ... after it started; no fallback', NO FALLBACK/LOCAL-STUB, no ledger entry, no PASS line" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: the remote build ended rc 255 (ssh drop = 255) after it started; no fallback' "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")$(has '^RESULT: PASS' "$out")" = 100 ] && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
ok "ssh rc 255 after the build started: teardown killed the container (docker kill rj-xnub-xr-t2d-*) and removed the job dir" "$([ "$(has '^kill rj-xnub-xr-t2d-[0-9]*$' "$(cat "$fh/docker.log")")" = 1 ] && [ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
# F2 (c): the slot is killed mid-build: ERROR 'ended ... without a report'
reset_offload; touch "$fh/bslow" "$w/kill-slot"
xrb --stack "$repo/xnu/patches" --label t2k
ok "slot killed mid-build (SIGTERM): exit 2, ERROR 'ended rc N without a report (killed or crashed); no fallback after the build started', no PASS, no FALLBACK" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: the offload-host build ended rc [0-9]* without a report (killed or crashed); no fallback' "$out")$(has '^RESULT: PASS' "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")" = 100 ] && echo 1 || echo 0)"
ok "slot killed mid-build: the always-run teardown still killed the container and removed the job dir" "$([ "$(has '^kill rj-xnub-xr-t2k-[0-9]*$' "$(cat "$fh/docker.log")")" = 1 ] && [ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
# F2 (d): rc 0 without a PASS line / PASS without a sha256 line
reset_offload; touch "$fh/bnopass"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2p
ok "remote rc 0 without RESULT: PASS: exit 2, ERROR 'remote rc 0 without a RESULT: PASS report', no PASS line, no ledger entry" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: remote rc 0 without a RESULT: PASS report' "$out")$(has '^RESULT: PASS' "$out")" = 10 ] && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
reset_offload; touch "$fh/bnosha"; before=$(ledger_n)
xrb --stack "$repo/xnu/patches" --label t2s
ok "PASS report without a sha256 line: exit 2, ERROR 'PASS report without a sha256 line', no ledger entry" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: PASS report without a sha256 line' "$out")" = 1 ] && [ "$(ledger_n)" = "$before" ] && echo 1 || echo 0)"
# F2 (e): label and jobs validation
reset_offload; : >"$w/ssh.log"
xrb --stack "$repo/xnu/patches" --label ../x
ok "--label ../x: exit 2, ERROR 'must be one path component', no ssh" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: --label must be one path component' "$out")" = 1 ] && [ "$(sshn)" = 0 ] && echo 1 || echo 0)"
xrb --stack "$repo/xnu/patches" --label t2j --jobs 0
ok "--jobs 0: exit 2, ERROR 'positive integer', no ssh" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: --jobs needs a positive integer' "$out")" = 1 ] && [ "$(sshn)" = 0 ] && echo 1 || echo 0)"
xrb --stack "$repo/xnu/patches" --label t2j --jobs 9
ok "--jobs 9 is capped to 4 (the container gets --jobs 4)" "$([ $rc = 0 ] && [ "$(has 'args=--stack .*/stack --label t2j --jobs 4 pwd=' "$(cat "$xa/stub-env")")" = 1 ] && echo 1 || echo 0)"
# F6: a ledger write failure after a verified build: loud WARNING + PASS with ledger=FAILED (never a thrown-away build, never silent)
reset_offload; rm -f "$lb/built-on.tsv"; mkdir "$lb/built-on.tsv"
xrb --stack "$repo/xnu/patches" --label t6
ok "ledger write fails (built-on.tsv is a directory): exit 0 PASS, RESULT line ends ledger=FAILED, WARNING 'ledger entry not written' on stdout AND stderr, kernels copied back" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS .* ledger=FAILED$' "$out")$(has '^XNU REMOTE: WARNING ledger entry not written (ledger-add: cannot append to .*built-on.tsv)' "$out")$(has '^XNU REMOTE: WARNING ledger entry not written' "$err")" = 111 ] && [ -f "$lb/out/t6/kernel.development.sweet" ] && echo 1 || echo 0)"
rmdir "$lb/built-on.tsv"
# F4: a dirty tools/ tree is marked DIRTY loudly (RESULT line, WARNING on stdout+stderr, ledger-line)
echo scratch >"$repo/tools/untracked-edit"
reset_offload
xrb --stack "$repo/xnu/patches" --label t4
ok "dirty tools/ (1 untracked file): PASS with 'dirty=1' + DIRTY on the RESULT line, WARNING 'built from a DIRTY tree: 1 changed/untracked file(s)' on stdout AND stderr" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS .* where=offload-host head=[0-9a-f]* dirty=1 stack=[0-9a-f]* DIRTY$' "$out")$(has '^XNU REMOTE: WARNING built from a DIRTY tree: 1 changed/untracked file(s)' "$out")$(has '^XNU REMOTE: WARNING built from a DIRTY tree' "$err")" = 111 ] && echo 1 || echo 0)"
ok "dirty tools/: ledger-line prints 'XNU BUILD: offload-host (label t4) DIRTY reason: head=.. dirty=1 ..' (the xrun mark)" "$([[ $("$repo/tools/xnu-remote-build" ledger-line "$lb/out/t4/kernel.development.sweet") =~ ^XNU\ BUILD:\ offload-host\ \(label\ t4\)\ DIRTY\ reason:\ head=[0-9a-f]{12}\ dirty=1\  ]] && echo 1 || echo 0)"
reset_offload; touch "$w/ssh-down"
xrb --stack "$repo/xnu/patches" --label t4f
ok "dirty tools/ + lab-host fallback: the lab-host RESULT line also says dirty=1 DIRTY, fallback=lab-host, where=lab-host-fallback; ledger reason has the provenance" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS .* where=lab-host-fallback fallback=lab-host head=[0-9a-f]* dirty=1 stack=[0-9a-f]* DIRTY$' "$out")$(has 'LOCAL-STUB where=lab-host-fallback reason=offload-host unreachable.* | head=[0-9a-f]* dirty=1 ' "$out")" = 11 ] && echo 1 || echo 0)"
rm -f "$repo/tools/untracked-edit"
# F9: after a failed remote build no earlier kernel stays creditable
reset_offload; mkdir -p "$lb/out/t9" "$lb/work/t9/fakeroot/System/Library/Kernels"; echo OLD >"$lb/out/t9/kernel.development.sweet"; echo OLD >"$lb/work/t9/fakeroot/System/Library/Kernels/kernel.development.sweet"; touch "$fh/bfail"
xrb --stack "$repo/xnu/patches" --label t9
ok "F9: compile FAIL on offload-host removes the old out/ and work-tree kernels of the label (nothing creditable left)" "$([ $rc = 1 ] && [ ! -e "$lb/out/t9/kernel.development.sweet" ] && [ ! -e "$lb/work/t9/fakeroot/System/Library/Kernels/kernel.development.sweet" ] && echo 1 || echo 0)"
reset_offload; echo OLD >"$lb/out/t9/kernel.development.sweet"; echo OLD >"$lb/work/t9/fakeroot/System/Library/Kernels/kernel.development.sweet"; touch "$fh/berror"
xrb --stack "$repo/xnu/patches" --label t9
ok "F9: a remote ERROR after the build started removes them too" "$([ $rc = 2 ] && [ ! -e "$lb/out/t9/kernel.development.sweet" ] && [ ! -e "$lb/work/t9/fakeroot/System/Library/Kernels/kernel.development.sweet" ] && echo 1 || echo 0)"

# ---- 3. every pre-build unavailability prints FALLBACK (stdout AND stderr) and runs the local stub with the ledger reason
fb() {   # NAME REASON-REGEX: asserts after an xrb call
	local m="^XNU REMOTE FALLBACK: .*$2.*; building on lab-host (lock queue)\$"
	ok "fallback ($1): exit 0 from the local stub, FALLBACK line with the reason on stdout AND on stderr" "$([ $rc = 0 ] && echo "$(has "$m" "$out")$(has "$m" "$err")" | grep -q 11 && echo 1 || echo 0)"
	ok "fallback ($1): the local build ran with where=lab-host-fallback + the reason and XNU_BUILD_WHERE=local, no build container was started (a mirror-check container may have run)" "$(echo "$(has "LOCAL-STUB where=lab-host-fallback reason=.*$2.* build_where=local" "$out")" | grep -q 1 && ! grep -q 'bash tools/xnu-local-build' "$fh/docker.log" && echo 1 || echo 0)"
}
reset_offload; touch "$w/ssh-down"
xrb --stack "$repo/xnu/patches" --label f1; fb "ssh down" 'offload-host unreachable (ssh rc 255: ssh: connect to host'
reset_offload; touch "$fh/noimage"
xrb --stack "$repo/xnu/patches" --label f2; fb "image missing" 'XNU image pixos-xnub:trixie-20261111 missing on offload-host'
reset_offload; rmdir "$xa/localbuild/deps"
xrb --stack "$repo/xnu/patches" --label f3; fb "toolchain dir missing" 'XNU toolchain .*/localbuild/deps missing on offload-host'
reset_offload; rmdir "$xa/git-mirror/hfs.git"
xrb --stack "$repo/xnu/patches" --label f4; fb "git-mirror incomplete" 'XNU git mirror .* missing or incomplete in the build container (check rc 0: xnu-src-mirror: NOT mirrored: hfs'
ok "fallback (mirror): the job dir shipped before the mirror check was torn down" "$([ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
reset_offload; touch "$w/rsync-fail"
xrb --stack "$repo/xnu/patches" --label f5; fb "rsync of tools failed" 'rsync of tools/ to offload-host failed: rsync: connection unexpectedly closed'
ok "fallback (rsync): teardown ran, no job dir left" "$([ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
reset_offload; touch "$fh/bmirrorerror"
xrb --stack "$repo/xnu/patches" --label f5e
ok "mirror tool RESULT: ERROR (stagers do not parse, F5): exit 2 ERROR naming xnu-src-mirror, NO fallback (the lab-host build would fail the same), no build container, teardown ran" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: .*REFUSED rc 98: tools/xnu-src-mirror cannot derive the pinned sources' "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")" = 10 ] && ! grep -q 'bash tools/xnu-local-build' "$fh/docker.log" && [ -z "$(ls -A "$fh/sroot/work/xnub-jobs" 2>/dev/null)" ] && echo 1 || echo 0)"
reset_offload; touch "$w/ssh-down-after1"
xrb --stack "$repo/xnu/patches" --label f6; fb "unreachable only at queue time" 'offload-host unreachable'
ok "fallback (queue time): the precheck passed, the heavy-job probe found offload-host unreachable: reason names heavy-job's 'offload-host unreachable', no build container" "$([ "$(sshn)" -ge 2 ] && echo 1 || echo 0)"
reset_offload; : >"$w/ssh.log"; touch "$w/ssh-down-after1"
XNU_BUILD_WHERE=offload-host xrb --stack "$repo/xnu/patches" --label f6w
ok "queue-time unreachable with XNU_BUILD_WHERE=offload-host: exit 2 ERROR, no fallback" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: XNU_BUILD_WHERE=offload-host and offload-host is unavailable: .*offload-host unreachable' "$out")$(has 'LOCAL-STUB\|FALLBACK' "$out$err")" = 10 ] && echo 1 || echo 0)"
# F8: the load probe keeps refusing for the whole wait: loud fallback with the reason (any caller)
reset_offload
OFFLOAD_MAXLOAD1=0 XNU_REMOTE_WAIT=2 xrb --stack "$repo/xnu/patches" --label f7; fb "admission refused" 'offload-host not admitted within the 2s wait (offload-queue: not admitted within 2s: load1'
reset_offload
OFFLOAD_MAXLOAD1=0 XNU_REMOTE_WAIT=2 XNU_BUILD_WHERE=offload-host xrb --stack "$repo/xnu/patches" --label f7w
ok "admission refused with XNU_BUILD_WHERE=offload-host: exit 2 ERROR, no fallback" "$([ $rc = 2 ] && [ "$(has 'RESULT: ERROR xnu-remote-build: XNU_BUILD_WHERE=offload-host and offload-host is unavailable: offload-host not admitted' "$out")$(has 'LOCAL-STUB\|FALLBACK' "$out$err")" = 10 ] && echo 1 || echo 0)"
# F10: a fallback from inside a offload-host slot (no lab-host lock) is refused, never takes the lab-host lock
reset_offload; touch "$w/ssh-down"
OFFLOAD_QUEUE_HELD=outer xrb --stack "$repo/xnu/patches" --label f8
ok "fallback from inside a offload-host slot (OFFLOAD_QUEUE_HELD, no HEAVY_JOB_HELD): exit 2 ERROR naming the lock-order inversion, NO local build, NO FALLBACK line" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: offload-host is unavailable (.*) and this caller holds a offload-host slot (OFFLOAD_QUEUE_HELD=outer) but no lab-host lock' "$out")$(has 'LOCAL-STUB\|^XNU REMOTE FALLBACK' "$out$err")" = 10 ] && echo 1 || echo 0)"
OFFLOAD_QUEUE_HELD=outer HEAVY_JOB_HELD=lab-host-gate xrb --stack "$repo/xnu/patches" --label f9
ok "same, but the caller ALSO holds the lab-host lock (gate order): the loud fallback is allowed" "$([ $rc = 0 ] && [ "$(has '^XNU REMOTE FALLBACK: offload-host unreachable' "$out")" = 1 ] && echo 1 || echo 0)"

# ---- 4. recursion guard, WHERE modes, unsupported flags
reset_offload; : >"$w/ssh.log"
XNUB_IN_SLOT=1 xrb --stack "$repo/xnu/patches" --label g1
ok "XNUB_IN_SLOT=1 in the dispatcher: straight to the local build (where=lab-host-local), no ssh, no FALLBACK/SKIP line" "$([ $rc = 0 ] && [ "$(sshn)" = 0 ] && echo "$(has 'LOCAL-STUB where=lab-host-local reason=head=' "$out")$(has 'FALLBACK\|SKIP' "$out$err")" | grep -q '^10$' && echo 1 || echo 0)"
XNU_BUILD_WHERE=local xrb --stack "$repo/xnu/patches" --label g2
ok "XNU_BUILD_WHERE=local: local build, ledger where=lab-host-local, no ssh" "$([ $rc = 0 ] && [ "$(sshn)" = 0 ] && [ "$(has 'LOCAL-STUB where=lab-host-local reason=XNU_BUILD_WHERE=local' "$out")" = 1 ] && echo 1 || echo 0)"
for fl in "--deps-only" "--compare /x/k" "--make-args -d" "--timeout 5"; do
	f=${fl%% *}; reset_offload; : >"$w/ssh.log"
	# shellcheck disable=SC2086 # $fl is a flag and its value, split on purpose
	xrb --stack "$repo/xnu/patches" --label u1 $fl
	ok "unsupported $f: 'XNU REMOTE SKIP: $f not supported on offload-host, building on lab-host' on stdout and stderr, local build gets the flag, no ssh" "$([ $rc = 0 ] && [ "$(sshn)" = 0 ] && echo "$(has "^XNU REMOTE SKIP: $f not supported on offload-host, building on lab-host\$" "$out")$(has "^XNU REMOTE SKIP: $f not supported on offload-host, building on lab-host\$" "$err")$(has "LOCAL-STUB where=lab-host-local reason=$f not supported.* args=.*$f" "$out")" | grep -q 111 && echo 1 || echo 0)"
done
reset_offload; touch "$w/ssh-down"; : >"$w/ssh.log"
XNU_BUILD_WHERE=offload-host xrb --stack "$repo/xnu/patches" --label w1
ok "XNU_BUILD_WHERE=offload-host, offload-host unavailable: exit 2, RESULT: ERROR naming the reason, NO local build, no FALLBACK line" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR xnu-remote-build: XNU_BUILD_WHERE=offload-host and offload-host is unavailable: offload-host unreachable' "$out")$(has 'LOCAL-STUB\|FALLBACK' "$out$err")" | grep -q '^10$' && echo 1 || echo 0)"
XNU_BUILD_WHERE=offload-host xrb --stack "$repo/xnu/patches" --label w2 --deps-only
ok "XNU_BUILD_WHERE=offload-host with an unsupported flag: exit 2, no local build" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: XNU_BUILD_WHERE=offload-host but --deps-only is not supported' "$out")" = 1 ] && ! grep -q LOCAL-STUB <<<"$out" && echo 1 || echo 0)"
XNU_BUILD_WHERE=bogus xrb --label w3
ok "XNU_BUILD_WHERE=bogus: exit 2 ERROR" "$([ $rc = 2 ] && has 'XNU_BUILD_WHERE must be auto, offload-host or local' "$out" || echo 0)"

# ---- 5. waiting in the offload-host queue is NOT a fallback reason: ERROR after XNU_REMOTE_WAIT naming the holder
reset_offload
( cd "$w/run" && exec setsid "$repo/tools/heavy-job" offload-host run --owner otherdriver --label otherjob --wait 60 -- sleep 25 >"$w/holder.out" 2>&1 ) & hp=$!
for _ in $(seq 1 50); do grep -qs running "$w"/q/0*.otherjob && break; sleep 0.2; done
XNU_REMOTE_WAIT=2 xrb --stack "$repo/xnu/patches" --label q1
ok "queue busy: exit 2 after XNU_REMOTE_WAIT, ERROR names the holder (owner=otherdriver), no FALLBACK, no local build, no container" "$([ $rc = 2 ] && echo "$(has '^RESULT: ERROR xnu-remote-build: the offload-host queue slot was not granted .*XNU_REMOTE_WAIT=2s.*holder: .*owner=otherdriver' "$out")$(has 'FALLBACK\|LOCAL-STUB' "$out$err")" | grep -q '^10$' && ! grep -q ' run ' "$fh/docker.log" && echo 1 || echo 0)"
# F3: the same busy queue, but the caller holds the lab-host lock (the gate): loud fallback (nested local build), not an ERROR after a long wait
HEAVY_JOB_HELD=lab-host-gate XNU_REMOTE_WAIT=2 xrb --stack "$repo/xnu/patches" --label q2
ok "queue busy while HEAVY_JOB_HELD is set: FALLBACK 'offload-host queue busy for the 2s wait while this caller holds the lab-host lock', local build runs (where=lab-host-fallback), exit 0" "$([ $rc = 0 ] && [ "$(has '^XNU REMOTE FALLBACK: offload-host queue busy for the 2s wait while this caller holds the lab-host lock (lab-host-gate); holder: .*; building on lab-host (lock queue)$' "$out")$(has 'LOCAL-STUB where=lab-host-fallback reason=offload-host queue busy' "$out")" = 11 ] && echo 1 || echo 0)"
HEAVY_JOB_HELD=lab-host-gate XNU_REMOTE_WAIT=2 XNU_BUILD_WHERE=offload-host xrb --stack "$repo/xnu/patches" --label q3
ok "queue busy + HEAVY_JOB_HELD + XNU_BUILD_WHERE=offload-host: ERROR, no fallback" "$([ $rc = 2 ] && [ "$(has 'XNU_BUILD_WHERE=offload-host and offload-host is unavailable: offload-host queue busy' "$out")$(has 'LOCAL-STUB' "$out")" = 10 ] && echo 1 || echo 0)"
# F3: the wait is derived from the gate step budget (GATE_STEP_BUDGET - 1500, at least 1); an explicit XNU_REMOTE_WAIT wins
GATE_STEP_BUDGET=1502 xrb --stack "$repo/xnu/patches" --label q4
ok "GATE_STEP_BUDGET=1502 (no XNU_REMOTE_WAIT): the queue wait is 2 s (budget - 1500) -> ERROR names XNU_REMOTE_WAIT=2s" "$([ $rc = 2 ] && [ "$(has 'XNU_REMOTE_WAIT=2s' "$out")" = 1 ] && echo 1 || echo 0)"
GATE_STEP_BUDGET=100 xrb --stack "$repo/xnu/patches" --label q5
ok "GATE_STEP_BUDGET below the build window: the wait floors at 1 s, never negative / unbounded" "$([ $rc = 2 ] && [ "$(has 'XNU_REMOTE_WAIT=1s' "$out")" = 1 ] && echo 1 || echo 0)"
GATE_STEP_BUDGET=9999 XNU_REMOTE_WAIT=2 xrb --stack "$repo/xnu/patches" --label q6
ok "an explicit XNU_REMOTE_WAIT=2 beats GATE_STEP_BUDGET=9999" "$([ $rc = 2 ] && [ "$(has 'XNU_REMOTE_WAIT=2s' "$out")" = 1 ] && echo 1 || echo 0)"
GATE_STEP_BUDGET=abc xrb --stack "$repo/xnu/patches" --label q7
ok "GATE_STEP_BUDGET=abc: exit 2 ERROR 'must be a number of seconds'" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR xnu-remote-build: GATE_STEP_BUDGET must be a number of seconds' "$out")" = 1 ] && echo 1 || echo 0)"
kill -- "-$hp" 2>/dev/null; wait "$hp" 2>/dev/null; hp=

# ---- 6. the real tools/xnu-local-build: its dispatch guard (a copy in a sandbox whose PATH lacks the build tools: it stops at 'missing host tools', never builds or downloads)
g=$w/guard; mkdir -p "$g/tools" "$g/bin" "$g/art"
cp "$src/xnu-local-build" "$g/tools/xnu-local-build"; cp "$here/tools/heavy-job" "$here/tools/heavy-job-canon.sh" "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$g/tools/"
printf '#!/bin/bash\necho "DISPATCHER-CALLED $*"; exit 0\n' >"$g/tools/xnu-remote-build"; chmod +x "$g/tools/xnu-remote-build"
for t in bash env readlink dirname realpath mkdir flock date tee timeout cat sleep sed rm mktemp cut tr head tail grep awk basename; do ln -sf "$(command -v "$t")" "$g/bin/$t"; done
gl() { ( cd "$w/run" && env -i PATH="$g/bin" HOME="$w/home" ARTIFACTS="$g/art" "$@" "$g/tools/xnu-local-build" --label gx >"$w/gout" 2>&1 ); grc=$?; gout=$(cat "$w/gout"); }
gl HEAVY_JOB_HELD=t
ok "xnu-local-build (no guard env): the dispatcher is called first (its output, exit 0), nothing built here" "$([ $grc = 0 ] && has '^DISPATCHER-CALLED --label gx$' "$gout" || echo 0)"
gl HEAVY_JOB_HELD=t XNUB_IN_SLOT=1
ok "xnu-local-build with XNUB_IN_SLOT=1: the dispatcher is NOT called (no recursion); the local path starts (stops at missing host tools in this sandbox)" "$([ $grc = 2 ] && echo "$(has 'DISPATCHER-CALLED' "$gout")$(has 'RESULT: ERROR missing host tools' "$gout")" | grep -q '^01$' && echo 1 || echo 0)"
gl HEAVY_JOB_HELD=t XNU_BUILD_WHERE=local
ok "xnu-local-build with XNU_BUILD_WHERE=local: the dispatcher is NOT called" "$([ $grc = 2 ] && has 'DISPATCHER-CALLED' "$gout" | grep -q 0 && echo 1 || echo 0)"
ok "xnu-local-build: HEAVY_JOB_WAIT default 10800, ledger-add call with XNU_LEDGER_WHERE/REASON, skipped inside the container (static)" "$(echo "$(has '"${HEAVY_JOB_WAIT:-10800}"' "$(cat "$src/xnu-local-build")")$(has 'ledger-add .*"${XNU_LEDGER_WHERE:-lab-host-local}" --reason "${XNU_LEDGER_REASON:-}"' "$(cat "$src/xnu-local-build")")$(has 'if \[ "${XNUB_IN_SLOT:-}" != 1 \]; then$' "$(cat "$src/xnu-local-build")")" | grep -q 111 && echo 1 || echo 0)"
ok "tools/xnub-line.sh puts XNUB_IN_SLOT=1 in the container argv" "$(has '-e XNUB_IN_SLOT=1' "$(bash -c 'source "$1"; xnub_argv docker img /a /j nm /j; echo "${XF[*]}"' _ "$src/xnub-line.sh")")"

# ---- 7. ledger subcommands and the xrun mark
rm -f "$lb/built-on.tsv"; sh1=$(printf a | sha256sum | cut -d' ' -f1)
"$repo/tools/xnu-remote-build" ledger-add --dir "$lb" --sha "$sh1" --label lab --where lab-host-fallback --reason $'offload-host\tunreachable\nrc 255' >/dev/null
ok "ledger-add: one line, exactly 5 tab fields, tabs/newlines of the reason flattened" "$([ "$(wc -l <"$lb/built-on.tsv")" = 1 ] && awk -F'\t' 'NF==5 && $5=="offload-host unreachable rc 255" && $4=="lab-host-fallback"{f=1} END{exit !f}' "$lb/built-on.tsv" && echo 1 || echo 0)"
printf a >"$w/a.k"
ok "ledger-line: XNU BUILD: lab-host-fallback (label lab) reason: ..." "$([ "$("$repo/tools/xnu-remote-build" ledger-line --dir "$lb" "$w/a.k")" = "XNU BUILD: lab-host-fallback (label lab) reason: offload-host unreachable rc 255" ] && echo 1 || echo 0)"
"$repo/tools/xnu-remote-build" ledger-add --dir "$lb" --sha "$sh1" --label lab2 --where offload-host >/dev/null
ok "ledger-line: the LAST entry of a sha wins (rebuilt on offload-host later)" "$([ "$("$repo/tools/xnu-remote-build" ledger-line --dir "$lb" "$w/a.k")" = "XNU BUILD: offload-host (label lab2)" ] && echo 1 || echo 0)"
o=$("$repo/tools/xnu-remote-build" ledger-add --dir "$lb" --sha nothex --label l --where offload-host); lrc=$?
o2=$("$repo/tools/xnu-remote-build" ledger-add --dir "$lb" --sha "$sh1" --label l --where nowhere); lrc2=$?
ok "ledger-add refuses a bad sha and a bad where (exit 2, ERROR)" "$([ $lrc = 2 ] && [ $lrc2 = 2 ] && has 'RESULT: ERROR' "$o$o2" | grep -q 1 && [ "$(wc -l <"$lb/built-on.tsv")" = 2 ] && echo 1 || echo 0)"
ok "tools/xrun prints the ledger line after the run mark and marks xnu_build (static: the mark helper is tested above and in test_xrun_report.py)" "$(x=$(grep -n 'xnu-remote-build ledger-line' "$here/tools/xrun" | head -n 1); echo "$([ -n "$x" ] && echo 1 || echo 0)$(has 'mark "$D" xnu_build="$xnubuild"' "$(cat "$here/tools/xrun")")$(has 'XNU BUILD: unknown (ledger lookup failed' "$(cat "$here/tools/xrun")")" | grep -q 111 && echo 1 || echo 0)"

# ---- 8. the unchanged consumer: tools/gate-xnu-build parses the remote report and finds the kernel in the work tree
reset_offload; rm -rf "$lb/work" "$lb/out"
gout=$(cd "$repo" && GATE_XNU_LOCAL_BUILD=$repo/tools/xlb-remote "$repo/tools/gate-xnu-build" --repo "$repo" --always --label gx1 2>&1 </dev/null); grc=$?
gsha=$(sha256sum "$lb/work/gx1/fakeroot/System/Library/Kernels/kernel.development.sweet" 2>/dev/null | cut -d' ' -f1)
ok "gate-xnu-build over the dispatcher: RESULT: PASS xnu-build sha=<the copied kernel's sha> (report parsed unchanged), XNU REMOTE line on the gate console" "$([ $grc = 0 ] && echo "$(has "^RESULT: PASS xnu-build sha=$gsha label=gx1" "$gout")$(has '^XNU REMOTE: built on offload-host' "$gout")" | grep -q 11 && [ -n "$gsha" ] && echo 1 || echo 0)"

ok "gate-xnu-build RESULT line says where=offload-host (review F7)" "$(has "^RESULT: PASS xnu-build sha=$gsha label=gx1 stack=[0-9a-f]* [0-9]*s where=offload-host\$" "$gout")"
reset_offload; touch "$w/ssh-down"; rm -rf "$lb/work" "$lb/out"
gout=$(cd "$repo" && GATE_XNU_LOCAL_BUILD=$repo/tools/xlb-remote "$repo/tools/gate-xnu-build" --repo "$repo" --always --label gx2 2>&1 </dev/null); grc=$?
ok "gate-xnu-build over a lab-host FALLBACK (F7): FALLBACK line on the gate console and the RESULT line ends 'where=lab-host-fallback fallback=lab-host'" "$([ $grc = 0 ] && [ "$(has '^XNU REMOTE FALLBACK: ' "$gout")$(has '^RESULT: PASS xnu-build sha=[0-9a-f]\{64\} label=gx2 stack=[0-9a-f]* [0-9]*s where=lab-host-fallback fallback=lab-host$' "$gout")" = 11 ] && echo 1 || echo 0)"
echo scratch >"$repo/tools/untracked-edit"; reset_offload; rm -rf "$lb/work" "$lb/out"
gout=$(cd "$repo" && GATE_XNU_LOCAL_BUILD=$repo/tools/xlb-remote "$repo/tools/gate-xnu-build" --repo "$repo" --always --label gx3 2>&1 </dev/null); grc=$?
rm -f "$repo/tools/untracked-edit"
ok "gate-xnu-build over a dirty tools/ tree (F4): the RESULT line ends 'where=offload-host dirty=1' and the DIRTY WARNING is on the gate console" "$([ $grc = 0 ] && [ "$(has '^RESULT: PASS xnu-build sha=[0-9a-f]\{64\} label=gx3 stack=[0-9a-f]* [0-9]*s where=offload-host dirty=1$' "$gout")$(has '^XNU REMOTE: WARNING built from a DIRTY tree' "$gout")" = 11 ] && echo 1 || echo 0)"
ok "tools/xnu-local-build puts XNU_RESULT_EXTRA on its RESULT line (finish), so a lab-host fallback shows where= / fallback= there (static: the real build is not run here)" "$([ "$(has 'echo "RESULT: $res $1${XNU_RESULT_EXTRA:+ $XNU_RESULT_EXTRA}"' "$(cat "$src/xnu-local-build")")" = 1 ] && echo 1 || echo 0)"

# ---- 9. xnub_prune called directly (the guards, the recent-keep rule): HOME points at the fake offload-host home so the constant work dir is the fake one
pr() { ( cd "$w/run" && HOME=$fh RJW=$fh/sroot/work XNUB_KEEP_RECENT=${KR:-2} bash -c 'source "$1"; shift; xnub_prune "$@"' _ "$src/xnub-line.sh" "$@" ) 2>&1; }
mkdir -p "$w/foreign/a" "$w/foreign/b"
po=$(pr "$w/foreign" a); prc=$?
ok "xnub_prune of a foreign dir: rc 98 'REFUSING to prune', nothing deleted (the guard fires when a caller passes another path)" "$([ $prc = 98 ] && [ "$(has '^XNUB: REFUSING to prune '"$w"'/foreign' "$po")" = 1 ] && [ -d "$w/foreign/a" ] && [ -d "$w/foreign/b" ] && echo 1 || echo 0)"
xw=$xa/localbuild/work; rm -rf "$xw"; mkdir -p "$xw/k1" "$xw/o1" "$xw/o2" "$xw/o3" "$xw/.dot"; touch -d 2020-01-01 "$xw/o1"; touch -d 2021-01-01 "$xw/o2"; touch -d 2022-01-01 "$xw/o3"
po=$(KR=1 pr "$xw" k1)
ok "xnub_prune keep=1 recent: the newest other tree (o3) and KEEP (k1) stay, o1 o2 go, the dot dir stays" "$([ -d "$xw/k1" ] && [ -d "$xw/o3" ] && [ ! -e "$xw/o1" ] && [ ! -e "$xw/o2" ] && [ -d "$xw/.dot" ] && [ "$(has '^XNUB: pruned 2 work tree(s): o1 o2$' "$po")$(has '^XNUB: kept 1 recent work tree(s): o3$' "$po")" = 11 ] && echo 1 || echo 0)"
mkdir -p "$xw/o4"; echo log >"$xw/o4/build.log"; touch -d 2019-01-01 "$xw/o4"; touch -d 2030-01-01 "$xw/o4/build.log"
po=$(KR=1 pr "$xw" k1)
ok "xnub_prune: a rebuilt tree counts as recently used via its build.log mtime (o4's dir is old, its build.log new) -> o4 kept, o3 pruned" "$([ -d "$xw/o4" ] && [ ! -e "$xw/o3" ] && echo 1 || echo 0)"
po=$(KR=0 pr "$xw" k1)
ok "xnub_prune keep=0 recent: every tree except KEEP goes" "$([ -d "$xw/k1" ] && [ ! -e "$xw/o4" ] && [ "$(has '^XNUB: pruned 1 work tree(s): o4$' "$po")$(has 'kept' "$po")" = 10 ] && echo 1 || echo 0)"
mkdir -p "$w/outside"; rm -rf "$xw"; mkdir -p "$xw/k1"; ln -s "$w/outside" "$xw/lnk"
po=$(KR=0 pr "$xw" k1)
ok "xnub_prune never removes or follows a symlink entry (WARNING, target intact)" "$([ -L "$xw/lnk" ] && [ -d "$w/outside" ] && [ "$(has '^XNUB: WARNING work entry .*/lnk is a symlink, not removed$' "$po")" = 1 ] && echo 1 || echo 0)"
rm -rf "$xw"; mkdir -p "$w/realwork"; ln -s "$w/realwork" "$xw"; mkdir -p "$w/realwork/zz"
po=$(KR=0 pr "$xw" k1)
ok "xnub_prune of a work dir that is itself a symlink: skipped with a WARNING, nothing deleted" "$([ -d "$w/realwork/zz" ] && [ "$(has '^XNUB: WARNING work dir .* is a symlink, prune skipped$' "$po")" = 1 ] && echo 1 || echo 0)"
rm -f "$xw"

echo "xnu-remote-build tests: cases=$n failed=$bad"
if [ "${XRB_MUTANT:-}" != 1 ] && [ $bad = 0 ]; then
	# ---- mutants: each must make this suite FAIL
	mut() {   # NAME FILE SED-EXPR
		local d=$w/mut-$1; mkdir -p "$d"; cp "$here"/tools/xnu-remote-build "$here"/tools/xnub-line.sh "$here"/tools/heavy-job "$here"/tools/heavy-job-canon.sh "$here"/tools/gate-xnu-build "$here"/tools/xnu-local-build "$here"/tools/offload-conf.sh "$here"/tools/offload_conf.py "$d/"
		sed -i "$3" "$d/$2"
		if cmp -s "$d/$2" "$here/tools/$2"; then ok "mutant $1: the edit applied" 0; return; fi
		r=$(XRB_ORIG_PATH=$ORIG_PATH XRB_MUTANT=1 XRB_TOOLS_DIR=$d PATH=$ORIG_PATH bash "$here/tools/test_xnu_remote_build.sh" 2>&1 | tail -n 1)
		ok "mutant $1 is killed ($r)" "$(has '^RESULT: FAIL' "$r")"
	}
	mut nosha xnu-remote-build 's/^\t\t\[ "\$got" = "\$sha" \] || {/\t\t[ -n "$got" ] || {/'
	mut silent-fallback xnu-remote-build 's/^\techo "\$m"; echo "\$m" >&2$/\ttrue/'
	mut norecursion-guard xnu-local-build 's/ && \[ "\${XNUB_IN_SLOT:-}" != 1 \] && \[ "\${XNU_BUILD_WHERE:-auto}" != local \]; then exec/; then exec/'
	mut k2skip xnu-remote-build 's/^\tfor f in "\$k1" "\$k2"; do/\tfor f in "$k1"; do/'
	mut nokill xnu-remote-build 's/"\$DK" kill "rj-xnub-\$NAME" >\/dev\/null 2>&1; //'
	mut noreported xnu-remote-build 's/^\[ -e "\$st\/reported" \] || err .*$/true/'
fi
if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
