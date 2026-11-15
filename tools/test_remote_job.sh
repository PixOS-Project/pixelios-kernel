#!/bin/bash
# Host test of tools/remote-job with a fake ssh/scp on PATH (never touches a real host; seconds). The fake "remote" is a directory used as $HOME.
# Cases: remote available, unavailable (REMOTE line + local run), ssh drop mid-run (falls back, logs), job FAIL on remote (no fallback, exit 1),
# --where offload-host without a host (exit 2), phone-word refusal, unknown job, dirty tree refused for a remote run.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
cd "$(dirname "$0")/.." || exit 2; . tools/test_remote_job_inputs.sh   # R13 cases: r13_setup, r13_cases
w=$(mktemp -d) || exit 2; trap 'rm -rf "$w"' EXIT
unset T3CODE_THREAD_ID; export WAKE_SECRETS_DIR=$w/sec   # T-fb2c: remote-job defaults --wake-hook to the running thread's hook; a test must not wake the thread that runs it
export OFFLOAD_MIN_FREE_GB=1   # R64: the default guard is 20 G; the temp dir has less
export OFFLOAD_CONF=$PWD/tools/offload-host.conf.example   # T-dd93: the committed placeholder host config, never the real one
export OFFLOAD_OFFLINE_FILE=$w/off.list   # T-ee35: private offline list (absent = host on), never the real queue file
export LAB_HOME=/home/user   # the lab-host home as the xnub container addresses the artifacts
export OFFLOAD_ROOT=$w/sroot   # R64: the fake offload-host work root is $OFFLOAD_ROOT/work (the lab-host lane keeps $HOME/work/pixos)
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); fi; }
has() { grep -q -- "$1" <<<"$2" && echo 1 || echo 0; }

repo=$w/repo; mkdir -p "$repo/tools" "$repo/xnu/patches" "$w/bin" "$w/sroot/work" "$w/home/work/pixos"
echo p >"$repo/xnu/patches/0001.patch"
# test-only extra job: reads stdin (must get /dev/null, else it eats the runner script's trailing lines)
sed "s#^\t\tselftest-fail) #\t\tselftest-stdin) cmd='cat'; inputs=tools ;;\n\t\tselftest-slow) cmd='sleep 4'; inputs=tools ;;\n\t\tselftest-grp) cmd='sleep 3123 \& wait'; inputs=tools ;;\n\t\tselftest-refs) cmd='git for-each-ref refs/patch-numbers refs/heads; git rev-parse HEAD; readlink tools/third_party; ls tools/third_party 2>\&1; pwd'; inputs=tools ;;\n&#" tools/remote-job >"$repo/tools/remote-job"; chmod +x "$repo/tools/remote-job"
mkdir -p "$w/sroot/work/gate-k/tools/third_party" && echo ': ' >"$w/sroot/work/env.sh" && echo 'export ENVSH=1' >"$w/sroot/work/env.sh"
cat >"$repo/tools/check-all.sh" <<'C'
#!/bin/bash
# stub gate: prints what the gate-pre wrapper set up
echo "PFX=${PFX:-} GLOBA=${A:-} GLOBB=${B:-} ENVSH=${ENVSH:-} PATH0=${PATH%%:*}/ HOSTLOCK=$GATE_HOST_LOCK XNUB=${GATE_XNU_BUILD:-} PASS=$GATE_PASS_DIR MUTC=$GATE_MUT_CACHE TMPD=$TMPDIR STEPT=$GATE_STEP_TIMEOUT SHARDS=$GATE_UNIT_SHARDS VERD=$GATE_MUT_VERDICTS"
adb devices; echo "adb rc=$?"; fastboot devices; echo "fastboot rc=$?"
mkdir -p "$GATE_PASS_DIR" && echo pass >"$GATE_PASS_DIR/stamp"; echo '{}' >"$GATE_MUT_VERDICTS"   # honours GATE_MUT_VERDICTS
mkdir -p shim/out/gate; echo "full gate log stub" >shim/out/gate.log
# TP-048: MUTCACHE line like tools/test_wlan_wr_mutants.py:1565; hits = entries the earlier runs left in the persistent cache; mode file: missing / ro / mismatch
mcm=$(cat "$HOME/mcmode" 2>/dev/null); hits=$(wc -l <"$GATE_MUT_CACHE/entries" 2>/dev/null || echo 0); echo "entry" >>"$GATE_MUT_CACHE/entries"
case $mcm in missing) ;; ro) echo "MUTCACHE cache=ro hits=0 fresh=5 stored=0 store_failed=5 dedup=1 cpu_timeouts=0 infra=0 spot=0/0 salt=t ok" >>shim/out/gate.log ;;
  mismatch) echo "MUTCACHE MISMATCH: cache dir renamed $GATE_MUT_CACHE.bad-1700000001" >>shim/out/gate.log; echo "MUTCACHE cache=ok hits=$hits fresh=1 stored=1 store_failed=0 dedup=1 cpu_timeouts=0 infra=0 spot=3/3 salt=t MISMATCH" >>shim/out/gate.log ;;
  mismatch-qfail) echo "MUTCACHE MISMATCH: quarantine failed: [Errno 16] Device or resource busy" >>shim/out/gate.log; echo "MUTCACHE cache=ok hits=$hits fresh=1 stored=1 store_failed=0 dedup=1 cpu_timeouts=0 infra=0 spot=3/3 salt=t MISMATCH" >>shim/out/gate.log ;;
  *) echo "MUTCACHE cache=ok hits=$hits fresh=$((5 - hits)) stored=0 store_failed=0 dedup=1 cpu_timeouts=0 infra=0 spot=3/3 salt=t ok" >>shim/out/gate.log ;; esac
 echo "SHARD FAIL test_x.py rc=1 (log x)" >shim/out/gate/unit.log; mkdir -p shim/out/gate/build-0; echo b >shim/out/gate/build-0/f
[ ! -e "$HOME/mempeak" ] || echo 4242 >"$HOME/mempeak"   # R12: only a peak read AFTER the gate sees 4242 (the file starts as 1)
[ ! -e "$HOME/nopeakfile" ] || kill -9 $PPID   # the container dies before the peak is read
[ -e "$HOME/gatefail" ] && { echo "GATE FAIL stubgate"; exit 1; }
echo "GATE PASS stubgate"; echo "GATE STUB DONE"
C
sed -i "s#\$HOME/gatefail#$w/home/gatefail#; s#\$HOME/mcmode#$w/home/mcmode#; s#\$HOME/mempeak#$w/home/mempeak#g; s#\$HOME/nopeakfile#$w/home/nopeakfile#" "$repo/tools/check-all.sh"   # absolute: HOME in the fake container is not the fake remote home
chmod +x "$repo/tools/check-all.sh"
cat >"$repo/tools/gate-identity" <<C
#!/bin/bash
echo "image=\$GATE_IMAGE_ID"; echo "stub=\$([ -e $w/home/idalter ] && echo altered || echo ok)"
C
chmod +x "$repo/tools/gate-identity"; printf 'image=sha256:fakeimageid\nstub=ok\n' >"$repo/tools/gate-identity.expected"
printf '#!/bin/bash\necho "shards=$GATE_UNIT_SHARDS out=$GATE_UNIT_OUT"\n' >"$repo/tools/gate-unit-shards"; chmod +x "$repo/tools/gate-unit-shards"
# W35 R22 in-slot XNU build: the committed tools/gate-xnu-build is what the container runs (fake: logs argv + count, fails when $w/xnubfail exists); toolchain marker dir on the fake offload-host
printf '#!/bin/bash\necho "$*" >"%s/xnub-argv"; echo run >>"%s/xnub-count"; [ -e "%s/xnubslow" ] && sleep 2; [ -e "%s/xnubfail" ] && { echo "RESULT: FAIL xnu-build errors=1 first=bsd/dev/x.c:9 label=offload-gate"; exit 1; }; echo "RESULT: PASS xnu-build sha=0000000000000000000000000000000000000000000000000000000000000000 label=offload-gate stack=abc 33s where=offload-host"\n' "$w" "$w" "$w" "$w" >"$repo/tools/gate-xnu-build"; chmod +x "$repo/tools/gate-xnu-build"
mkdir -p "$w/sroot/work/xnub-art/localbuild/work"
for d in sdk theos deps hostroot dxb; do mkdir -p "$w/sroot/work/xnub-art/localbuild/$d"; done   # tools/xnub-line.sh XNUB_LB_REQUIRED
mkdir -p "$w/sroot/work/xnub-art/git-mirror"; for d in libpthread hfs BearSSL; do mkdir -p "$w/sroot/work/xnub-art/git-mirror/$d.git"; done
# stub of `tools/xnu-src-mirror` modelling the REAL contract (header of tools/xnu-src-mirror): `seed` prints `RESULT: PASS seeded=.. present=.. dir=..` on STDOUT, rc 0 (XSM mode seedfail:
# `RESULT: FAIL ...`, rc 1); `env` prints NOTHING on stdout (that is the eval text) and `xnu-src-mirror: NOT mirrored: N sha` + `RESULT: PASS mirrored=M of N dir=D` on STDERR, rc 0 (mode zero:
# `mirrored=0 of 0`; mode error: `RESULT: ERROR ...` rc 2). Only checks that the <name>.git dirs exist. The mode and the call log are files under $w (the container runs under env -i).
cat >"$repo/tools/xnu-src-mirror" <<'M'
#!/bin/bash
cmd=$1; shift
mode=$(cat @W@/xsm-mode 2>/dev/null)
echo "$cmd $*" >>@W@/xsm.log
d=; while [ $# -gt 0 ]; do case $1 in --dir) d=$2; shift 2 ;; *) shift ;; esac; done
if [ "$cmd" = seed ]; then
	[ "$mode" != seedfail ] || { echo "RESULT: FAIL seeded=0 present=0 failed=1 dir=$d"; exit 1; }
	echo "RESULT: PASS seeded=0 present=3 dir=$d"; exit 0
fi
[ "$mode" != error ] || { echo "RESULT: ERROR no single fetch in 0019-x.sh" >&2; exit 2; }
[ "$mode" != zero ] || { echo "RESULT: PASS mirrored=0 of 0 dir=$d" >&2; exit 0; }
m=0; for n in libpthread hfs BearSSL; do if [ -d "$d/$n.git" ]; then m=$((m + 1)); else echo "xnu-src-mirror: NOT mirrored: $n 0000" >&2; fi; done
echo "RESULT: PASS mirrored=$m of 3 dir=$d" >&2
M
sed -i "s#@W@#$w#g" "$repo/tools/xnu-src-mirror"
printf '#!/bin/bash\n# test stub of tools/xnu-local-build (the stager checks that it uses tools/xnu-src-mirror)\n' >"$repo/tools/xnu-local-build"; chmod +x "$repo/tools/xnu-local-build"
chmod +x "$repo/tools/xnu-src-mirror"
cp tools/xnub-line.sh tools/remote-job-inputs.sh tools/offload-stage-xnub tools/offload-conf.sh tools/offload_conf.py "$repo/tools/"
mkdir -p "$repo/tools/gate-image"; cp tools/gate-image/Dockerfile.xnub "$repo/tools/gate-image/"   # tools/offload-stage-xnub scp's it
# TP-050 lab-host lane: the real local transport, and a fake heavy-job that records its argv and runs the command (never the real lock)
cp tools/rj-local-transport "$repo/tools/rj-local-transport"
printf '#!/bin/bash\necho "$*" >>"%s/hj-hl"\nlab=x; while [ "$1" != -- ]; do [ "$1" = --label ] && lab=$2; shift; done; shift\nexec 7>>"$HEAVY_JOB_LOCK"; flock -n 7 || exit 2; echo "$lab gate $$ now" >"$HEAVY_JOB_LOCK"; export HEAVY_JOB_HELD=$lab; exec "$@"\n' "$w" >"$repo/tools/heavy-job"; chmod +x "$repo/tools/heavy-job"
git -C "$repo" init -q && git -C "$repo" -c user.email=t@t -c user.name=t add -A && git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init || exit 2
G="git -C $repo -c user.email=t@t -c user.name=t"
# reservation ref (blob) and another branch holding a number: both must reach the remote clone
$G branch other && b=$($G hash-object -w --stdin <<<resv) && $G update-ref refs/patch-numbers/0138 "$b"
cat >"$w/bin/ssh" <<'F'
#!/bin/bash
# fake ssh: drop options and host, run the command with HOME=$FAKE_HOME and the remote stdin
echo "ssh" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
shift   # host
echo "$*" >>"$FAKE_LOG.args"   # what the remote shell is asked to run (tools/offload-stage-xnub must send `bash -s`, never a fish-parsed command string)
[ "${FAKE_SSH_MODE:-}" = down ] && { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
[ "${FAKE_SSH_MODE:-}" = once ] && [ ! -e "$FAKE_LOG.once" ] && { : >"$FAKE_LOG.once"; echo "ssh: connect to host: Connection refused" >&2; exit 255; }   # first ssh only
s=$(cat)
if [ "${FAKE_SSH_MODE:-}" = drop ] && grep -q 'git init' <<<"$s"; then echo "ssh: connection reset" >&2; exit 255; fi
if [ -n "${FAKE_GOLDEN_TAMPER:-}" ] && grep -q 'git init' <<<"$s"; then   # between the ship and the run: damage the shipped trace on offload-host
	gt=$(ls -d "$OFFLOAD_ROOT/work"/golden/*/trace-dpu.txt | head -n 1)
	case $FAKE_GOLDEN_TAMPER in missing) rm -f "$gt" ;; alter) printf X >>"$gt" ;; symlink) cp "$gt" "$gt.real"; rm -f "$gt"; ln -s "$gt.real" "$gt" ;; esac
fi
echo "REMOTE-RAN" >>"$FAKE_LOG"
HOME=$FAKE_HOME REMOTE_LOADAVG=$FAKE_LOADAVG "$@" <<<"$s"
F
cat >"$w/bin/scp" <<'F'
#!/bin/bash
# fake scp: map host:path to $FAKE_HOME/path; last argument is the destination
args=(); while [ $# -gt 0 ]; do case $1 in -q|-r) shift ;; -o) shift 2 ;; *) args+=("$1"); shift ;; esac; done
echo "scp" >>"$FAKE_LOG"
map() { case $1 in *:/*) echo "${1#*:}" ;; *:*) echo "$FAKE_HOME/${1#*:}" ;; *) echo "$1" ;; esac; }   # real scp: an absolute remote path is absolute (R64: the work root is not under HOME)
dst=$(map "${args[-1]}"); unset 'args[-1]'
for a in "${args[@]}"; do if [ "${FAKE_SCP_MODE:-}" = golden-symlink ] && [[ $dst == *trace-*.new.* ]]; then ln -s /etc/hostname "$dst"; else cp -r "$(map "$a")" "$dst" || exit 1; fi; done   # golden-symlink: the staged copy is a symlink
[ "${FAKE_SCP_MODE:-}" = partial ] && [[ "${args[0]}" == *job.log ]] && { mkdir -p "$dst/out"; touch "$dst/out/STALE"; exit 1; }
exit 0
F
sed -i 's#^\[ "\${FAKE_SCP_MODE:-}" = partial \]#[ "${FAKE_SCP_MODE:-}" = slow ] \&\& [[ "${args[0]}" == *.bundle ]] \&\& sleep 5\n&#' "$w/bin/scp"
mkdir -p "$w/home/.local/bin"
cat >"$w/home/.local/bin/docker" <<'F'
#!/bin/bash
# fake docker: logs its args; `run` mimics the container: env -i + the -e vars, /job mapped to the -v source, cwd = that dir
echo "$*" >>"${FAKE_HOME:-$HOME}/docker.log"
case $1 in image) [ -e "${FAKE_HOME:-$HOME}/noxnubimage" ] && [[ $* == *xnub* ]] && exit 1; echo sha256:fakeimageid; exit 0 ;; kill) exit 0 ;; ps) [ ! -e "${FAKE_HOME:-$HOME}/pslist" ] || cat "${FAKE_HOME:-$HOME}/pslist"; exit 0 ;; build) echo sha256:fakexnubimage; exit 0 ;; run) shift ;; *) exit 9 ;; esac
ev=(); jd=
while [ $# -gt 0 ]; do case $1 in
	--rm|--network=*|--userns=*) shift ;;
	--name|--user|-w|--sysctl) shift 2 ;;
	-e) if [[ $2 == *=* ]]; then ev+=("$2"); else ev+=("$2=${!2}"); fi; shift 2 ;;
	-v) [[ $2 == *:/job:* ]] && jd=${2%%:/job:*}; [[ $2 == *:/home/user/pixelios-artifacts:* ]] && artsrc=${2%%:/home/user/pixelios-artifacts:*}; [[ $2 == *:/home/user/pixelios-artifacts/golden/*:ro,Z ]] && { gmp=${2##*/pixelios-artifacts/golden/}; gmp=${gmp%%:*}; [ -d "${artsrc:-/nonexistent}/golden/$gmp" ] || echo "GOLDEN-MOUNTPOINT-MISSING $gmp" >>"${FAKE_HOME:-$HOME}/docker.log"; }; [[ $2 == *:/mut-cache:rw,Z ]] && mcd=${2%%:/mut-cache:*}; [[ $2 == *:/job/research/ipsw-x/d22ap.adt:ro,Z ]] && adtsrc=${2%%:/job/*}; shift 2 ;;
	*) break ;;
esac; done
shift   # image
cd "$jd" || exit 9
[ -z "${adtsrc:-}" ] || { mkdir -p research/ipsw-x && cat "$adtsrc" >research/ipsw-x/d22ap.adt; }   # the bind mount: the container sees the shipped file at /job/research/ipsw-x/d22ap.adt
if [ "$1" = bash ] && [ "$2" = tmp/rj-check ]; then fr=$jd/tools/rj-fakeroot; mkdir -p "$fr/usr/bin" "$fr/dev"; ln -sf "$(command -v git)" "$fr/usr/bin/git"; set -- bash tmp/rj-check stubs "$fr/usr/bin" "$fr" "$fr/usr/bin"; fi   # fake container fs (the real args are in docker.log)
if [ -n "${artsrc:-}" ]; then a=(); for x in "$@"; do a+=("${x/#\/home\/user\/pixelios-artifacts/$artsrc}"); done; set -- "${a[@]}"; fi   # the artifacts bind mount: the container path of --dir is the host dir
[ ! -e "$FAKE_HOME/tmpdir-in-job" ] || ev+=("TMPDIR=/job/tmp")   # test only: a TMPDIR inside the checkout (AP-110)
[ ! -e "$FAKE_HOME/mempeak" ] || ev+=("RJ_MEMPEAK_FILE=$FAKE_HOME/mempeak")   # test only: stands in for /sys/fs/cgroup/memory.peak of the fake container (env -i drops the caller env)
ev=("${ev[@]//\/job/$jd}"); [ -z "${mcd:-}" ] || ev=("${ev[@]//=\/mut-cache/=$mcd}")   # the bind mounts
exec env -i "${ev[@]}" "$@"
F
chmod +x "$w/bin/ssh" "$w/bin/scp" "$w/home/.local/bin/docker"
export PATH=$w/bin:$PATH FAKE_HOME=$w/home FAKE_LOG=$w/log REMOTE_JOB_HOST=fake.invalid
r13_setup; export OFFLOAD_QUEUE_HELD=test-bypass   # TP-042: these cases test remote-job itself; the queue re-exec has its own cases at the end
echo 0.50 >"$w/load"; export FAKE_LOADAVG=$w/load
rj() { : >"$FAKE_LOG"; out=$(cd "$repo" && tools/remote-job "$@" 2>&1); rc=$?; }
# TP-039: gate-pre compiles HEAD's xnu/patches first; the fake records its argv and fails when $w/xnufail exists
printf '#!/bin/bash\necho "$*" >"%s/xnu-argv"; [ -e "%s/xnufail" ] && { echo "RESULT: FAIL xnu-build errors=1 first=bsd/dev/x.c:9"; exit 1; }; echo "RESULT: PASS xnu-build sha=00"\n' "$w" "$w" >"$w/bin/fake-xnu-build"; chmod +x "$w/bin/fake-xnu-build"
export REMOTE_JOB_XNU_BUILD=$w/bin/fake-xnu-build
# census pre-check (W26 2026-10-29): the fake records TOOL_CENSUS_SUPER and fails when $w/censusfail exists
printf '#!/bin/bash\necho "$TOOL_CENSUS_KERNEL $TOOL_CENSUS_SUPER $*" >"%s/census-argv"; [ -e "%s/censusfail" ] && { echo "TOOLS-MD-STALE tools/TOOLS.md differs"; echo "RESULT: FAIL tools=1"; exit 1; }; echo "RESULT: PASS tools=1"\n' "$w" "$w" >"$w/bin/fake-census"; chmod +x "$w/bin/fake-census"
export REMOTE_JOB_CENSUS=$w/bin/fake-census
# gate-manifest pre-check (AP-379 class): the fake fails when $w/manifestfail exists
printf '#!/bin/bash\n[ -e "%s/manifestfail" ] && { echo "STALE: tools/gate-manifest.json differs from a fresh render"; echo "RESULT: FAIL gate-manifest incomplete=0 stale=1"; exit 1; }; echo "RESULT: PASS gate-manifest steps=1"\n' "$w" >"$w/bin/fake-manifest"; chmod +x "$w/bin/fake-manifest"
export REMOTE_JOB_GATE_MANIFEST=$w/bin/fake-manifest

rj selftest
ok "available: pass, ran on offload-host" "$([ $rc = 0 ] && has 'where=offload-host' "$out")"
ok "available: provenance label + gcc/glibc/nice/duration" "$([ "$(has 'provenance=offload-host gcc[0-9]* (pre-validation)' "$out")$(has 'glibc=' "$out")$(has 'nice=19' "$out")$(has 'elapsed=' "$out")" = 1111 ] && echo 1 || echo 0)"
ok "available: no REMOTE line" "$([ "$(has '^REMOTE:' "$out")" = 0 ] && echo 1 || echo 0)"
ok "available: remote job dir removed" "$([ ! -e "$w/sroot/work/jobs/selftest" ] && echo 1 || echo 0)"

FAKE_SSH_MODE=down rj selftest
ok "ssh down: REMOTE line, pass, local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (.*), running local' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"

echo 99.0 >"$w/load"; rj selftest
ok "load 99: unavailable (load), local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (load' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
echo 0.50 >"$w/load"
# T-ee35: host listed offline by the board toggle: nothing new is routed there (loud OFFLOAD OFF reason, local); explicit --where offload-host fails; row removed = back on
printf 'offload-host\tuser order: shut down\n' >"$w/off.list"; rj selftest
ok "T-ee35 offline: REMOTE line carries 'OFFLOAD OFF' + reason, no ssh work, ran local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (OFFLOAD OFF: .*user order: shut down' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
rj --where offload-host selftest
ok "T-ee35 offline with --where offload-host: exit 2, not run locally" "$([ $rc = 2 ] && [ "$(has 'where=local' "$out")" = 0 ] && echo 1 || echo 0)"
rm -f "$w/off.list"; rj selftest
ok "T-ee35 row removed: back on offload-host" "$([ $rc = 0 ] && has 'where=offload-host' "$out" || echo 0)"
# shellcheck source=tools/test_remote_job_use.sh
. "$(dirname "$0")/test_remote_job_use.sh"   # per-use switches + the test-run job
REMOTE_JOB_MINFREE_GIB=99999999 rj selftest
ok "disk low: unavailable (free), local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (OFFLOAD-HOST GAP: .* [0-9]*G free < 99999999G' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
OFFLOAD_MIN_FREE_GB=99999999 rj selftest
ok "R64 free-space guard: OFFLOAD_MIN_FREE_GB above the free space => loud 'OFFLOAD-HOST GAP: <mount> <N>G free < <M>G', offload-host not used, local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (OFFLOAD-HOST GAP: .* [0-9]*G free < 99999999G' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
mv "$w/sroot/work" "$w/home/gone"; rj selftest; mv "$w/home/gone" "$w/sroot/work"
ok "no work root: unavailable, local" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host unavailable (.*no work root .*/sroot/work on offload-host' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"

FAKE_SSH_MODE=drop rj selftest
ok "drop mid-run: logged, fell back local, pass" "$([ $rc = 0 ] && [ "$(has '^REMOTE: offload-host died mid-job' "$out")$(has 'where=local' "$out")" = 11 ] && echo 1 || echo 0)"
FAKE_SSH_MODE=drop rj --where offload-host selftest
ok "drop with --where offload-host: exit 2, no fallback" "$([ $rc = 2 ] && [ "$(has 'where=local' "$out")" = 0 ] && echo 1 || echo 0)"

rj selftest-fail
ok "job FAIL on remote: exit 1, no fallback" "$([ $rc = 1 ] && [ "$(has 'where=offload-host' "$out")$(has 'job rc 7' "$out")$(has '^REMOTE:' "$out")" = 110 ] && echo 1 || echo 0)"
rj --where local selftest-fail
ok "local job FAIL: exit 1" "$([ $rc = 1 ] && has 'where=local' "$out" || echo 0)"

rj --where local selftest
ok "--where local never calls ssh" "$([ $rc = 0 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
for p in adb fastboot xrun phone probe-run; do
	rj "$p"
	ok "phone word '$p' refused (exit 2), no ssh" "$([ $rc = 2 ] && [ "$(has 'refused' "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
done
rj nosuchjob
ok "unknown job: exit 2" "$([ $rc = 2 ] && echo 1 || echo 0)"
echo x >>"$repo/tools/remote-job"; rj --where offload-host selftest
ok "dirty input refused for a remote run (exit 2)" "$([ $rc = 2 ] && [ "$(has 'uncommitted' "$out")" = 1 ] && echo 1 || echo 0)"
git -C "$repo" checkout -q tools/remote-job

git -C "$repo" checkout -q tools/remote-job
rj --where local selftest
ok "clean tree: local sha has no -dirty" "$([ "$(has 'sha=[0-9a-f]*-dirty' "$out")" = 0 ] && echo 1 || echo 0)"
echo x >>"$repo/tools/remote-job"; rj --where local selftest
ok "dirty tree, local run: sha marked -dirty" "$([ "$(has 'sha=[0-9a-f]*-dirty(1)' "$out")" = 1 ] && echo 1 || echo 0)"
git -C "$repo" checkout -q tools/remote-job
echo x >>"$repo/xnu/patches/0001.patch"; rj --where offload-host wlan-mutants
ok "dirty xnu/patches refused for a remote run (inputs)" "$([ $rc = 2 ] && [ "$(has 'uncommitted changes in xnu' "$out")" = 1 ] && echo 1 || echo 0)"
git -C "$repo" checkout -q xnu/patches/0001.patch
touch "$repo/tools/newfile"; rj --where offload-host selftest
ok "untracked new file in tools refused for a remote run" "$([ $rc = 2 ] && [ "$(has 'uncommitted' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$repo/tools/newfile"
rj gate
ok "gate job does not exist (exit 2)" "$([ $rc = 2 ] && [ "$(has 'unknown job' "$out")" = 1 ] && echo 1 || echo 0)"

FAKE_SSH_MODE=drop rj selftest
ok "drop: remote dir removed after fallback" "$([ ! -e "$w/sroot/work/jobs/selftest" ] && echo 1 || echo 0)"
FAKE_SSH_MODE=drop rj --where offload-host selftest
ok "drop --where offload-host: remote dir removed" "$([ ! -e "$w/sroot/work/jobs/selftest" ] && echo 1 || echo 0)"
FAKE_SCP_MODE=partial rj selftest
ok "partial copy-back: fell back local, stale out/ file gone" "$([ $rc = 0 ] && [ "$(has 'where=local' "$out")" = 1 ] && [ ! -e "$repo/logs/remote-jobs/selftest/out/STALE" ] && echo 1 || echo 0)"
ok "partial copy-back: remote dir removed" "$([ ! -e "$w/sroot/work/jobs/selftest" ] && echo 1 || echo 0)"
rj --where offload-host selftest-stdin
ok "job stdin is /dev/null (runner script not eaten)" "$([ $rc = 0 ] && ! grep -q 'exit 0' "$repo/logs/remote-jobs/selftest-stdin/job.log" && echo 1 || echo 0)"
rj selftest; ok "ssh opts carry ServerAliveInterval" "$(grep -q ServerAliveInterval "$repo/tools/remote-job" && echo 1 || echo 0)"
for p in /usr/bin/adb 'a;adb' 'x|fastboot' '/opt/fboot.sh'; do
	rj "$p"; ok "phone spelling '$p' refused" "$([ $rc = 2 ] && [ "$(has 'refused' "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
done
REMOTE_JOB_SSH=/usr/bin/adb rj selftest
ok "REMOTE_JOB_SSH phone word refused, no ssh" "$([ $rc = 2 ] && [ "$(has 'refused' "$out")" = 1 ] && echo 1 || echo 0)"
REMOTE_JOB_SCP='a;adb' rj selftest
ok "REMOTE_JOB_SCP phone word refused" "$([ $rc = 2 ] && [ "$(has 'refused' "$out")" = 1 ] && echo 1 || echo 0)"
REMOTE_JOB_HOST=-oProxyCommand rj selftest
ok "host starting with '-' refused" "$([ $rc = 2 ] && [ "$(has 'bad REMOTE_JOB_HOST' "$out")" = 1 ] && echo 1 || echo 0)"

# ---- requirements 1-5 (fake ssh)
rj --where offload-host selftest-refs
l=$repo/logs/remote-jobs/selftest-refs/job.log
ok "bundle: reservation ref + other branch in the remote clone, detached sha, third_party linked" "$([ "$rc" = 0 ] && [ "$(has 'refs/patch-numbers/0138' "$(cat "$l")")$(has 'refs/heads/other' "$(cat "$l")")$(has "^$(git -C "$repo" rev-parse HEAD)\$" "$(cat "$l")")$(has 'gate-k/tools/third_party' "$(cat "$l")")" = 1111 ] && echo 1 || echo 0)"
# T-d136: a commit that TRACKS tools/third_party (T-93e6 vendored it) must not get the gate-k link inside it (third_party/third_party, wrong identity hash)
ok "remote clone links gate-k third_party only when the commit does not track it" "$(grep -q '\[ ! -e src/tools/third_party \] && ln -s' tools/remote-job && echo 1 || echo 0)"
rj --where local gate-pre
ok "gate-pre --where local: exit 2, REMOTE line, no ssh" "$([ $rc = 2 ] && [ "$(has '^REMOTE: gate-pre runs on offload-host only' "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
FAKE_SSH_MODE=down rj gate-pre
ok "gate-pre with offload-host down: exit 2, REMOTE line, no local run" "$([ $rc = 2 ] && [ "$(has '^REMOTE: offload-host' "$out")$(has 'where=local' "$out")" = 10 ] && echo 1 || echo 0)"
# W35 R22 "lane jam": the offload-host route builds INSIDE the slot (job script, fake docker + fake tools/gate-xnu-build), never through the local pre-step (the lab-host lock)
xa=$w/sroot/work/xnub-art; xw=$xa/localbuild/work
rm -f "$w/xnu-argv" "$w/xnub-argv" "$w/xnub-count"; : >"$w/home/docker.log"; rj gate-pre; xl=$(cat "$w/home/docker.log"); jl=$repo/logs/remote-jobs/gate-pre
ok "xnub (a): offload-host gate-pre does NOT call the local xnu build (REMOTE_JOB_XNU_BUILD fake not run), says so loudly, still PASSes" "$([ $rc = 0 ] && [ ! -e "$w/xnu-argv" ] && [ "$(has 'offload-host route: the XNU build runs inside the offload-host slot' "$out")" = 1 ] && echo 1 || echo 0)"
ok "xnub (b): in-slot step ran once with --repo /job --always --label offload-gate (--jobs 4)" "$([ "$(cat "$w/xnub-argv" 2>/dev/null)" = "--repo /job --always --label offload-gate --jobs 4" ] && [ "$(wc -l <"$w/xnub-count")" = 1 ] && echo 1 || echo 0)"
ok "xnub (b): docker run line = image pixos-xnub, name rj-xnub-*, network none, keep-id, HEAVY_JOB_HELD, XNUB_IN_SLOT=1 (R26 recursion guard), ARTIFACTS + toolchain mounted at the SAME lab-host path, checkout at /job, no device" "$([ "$(has 'run --rm --name rj-xnub-gate-pre\.[0-9.]* --network=none --userns=keep-id --user 1000:1000 -e HOME=/home/user -e HEAVY_JOB_HELD -e XNUB_IN_SLOT=1 -e ARTIFACTS=/home/user/pixelios-artifacts' "$xl")$(has "-v $xa:/home/user/pixelios-artifacts:rw,Z" "$xl")$(has ':/job:rw,Z -w /job pixos-xnub:trixie-20261111 bash tools/gate-xnu-build' "$xl")$(has '--device\|--privileged' "$xl")" = 1110 ] && echo 1 || echo 0)"
ok "xnub: PASS puts the build RESULT line first in out/gate.log (verdict file), the gate output follows" "$([ "$(head -n 1 "$jl/out/gate.log")" = "RESULT: PASS xnu-build sha=0000000000000000000000000000000000000000000000000000000000000000 label=offload-gate stack=abc 33s where=offload-host" ] && [ "$(has 'GATE PASS stubgate' "$(cat "$jl/out/gate.log")")" = 1 ] && [ "$(cat "$jl/out/GATE_LINE")" = "GATE PASS stubgate" ] && echo 1 || echo 0)"
# (e) prune: every other work tree goes, offload-gate and dot-files stay, one loud XNUB line each (N=0 too)
mkdir -p "$xw/old1" "$xw/old2" "$xw/old3" "$xw/old4" "$xw/offload-gate"; echo x >"$xw/.offload_host-gate.gate-lock"; echo big >"$xw/old1/f"
touch -d 2020-01-01 "$xw/old1"; touch -d 2021-01-01 "$xw/old2"; touch -d 2022-01-01 "$xw/old3"; touch -d 2023-01-01 "$xw/old4"   # last used: old4 newest
rj gate-pre; jlog=$(cat "$jl/job.log")
ok "xnub (e) r1 F12: prune removes the other work trees except the 2 most recently used (old3 old4), keeps offload-gate and dot-files, prints 'XNUB: pruned 2 work tree(s): old1 old2', 'XNUB: kept 2 recent work tree(s): old3 old4' + size line" "$([ ! -e "$xw/old1" ] && [ ! -e "$xw/old2" ] && [ -d "$xw/old3" ] && [ -d "$xw/old4" ] && [ -d "$xw/offload-gate" ] && [ -e "$xw/.offload_host-gate.gate-lock" ] && [ "$(has '^XNUB: pruned 2 work tree(s): old1 old2$' "$jlog")$(has '^XNUB: kept 2 recent work tree(s): old3 old4$' "$jlog")$(has '^XNUB: work tree offload-gate size=[0-9]*MB$' "$jlog")" = 111 ] && echo 1 || echo 0)"
rj gate-pre; jlog=$(cat "$jl/job.log")
ok "xnub (e) r1 F12: N=0 case prints 'XNUB: pruned 0 work tree(s): none' too" "$([ "$(has '^XNUB: pruned 0 work tree(s): none$' "$jlog")" = 1 ] && echo 1 || echo 0)"
ok "xnub (e) r1 F3: the XNUB lines reach the console of a PASS (REMOTE: XNUB: image / build / pruned), not only job.log" "$([ $rc = 0 ] && [ "$(has '^REMOTE: XNUB: image sha256:fakeimageid$' "$out")$(has '^REMOTE: XNUB: build rc=0 RESULT: PASS xnu-build' "$out")$(has '^REMOTE: XNUB: pruned 0 work tree(s): none$' "$out")" = 111 ] && echo 1 || echo 0)"
ok "xnub (e) r1 F8: the pixos-xnub image id is on the RESULT line (xnub=<id12>, before provenance=) and in out/gate.log line 2" "$([ "$(has '^RESULT: PASS remote-job gate-pre .* xnub=fakeimageid .*provenance=' "$out")" = 1 ] && [ "$(sed -n 2p "$jl/out/gate.log")" = "XNUB: image sha256:fakeimageid" ] && echo 1 || echo 0)"
ok "xnub (e) r1 F4: the toolchain siblings of work/ (sdk theos deps hostroot dxb, git-mirror) survive the prune" "$(for t in sdk theos deps hostroot dxb; do [ -d "$xa/localbuild/$t" ] || echo x; done | grep -q x && echo 0 || { [ -d "$xa/git-mirror/hfs.git" ] && echo 1 || echo 0; })"
# r1 F2: work/ with only dot-files (no sub-directory): the unmatched glob is not a work tree
rm -rf "$xw/offload-gate" "$xw"/old*; rj gate-pre; jlog=$(cat "$jl/job.log")
ok "xnub (e) r1 F2: empty work/ (dot-file only) prints 'XNUB: pruned 0 work tree(s): none' and size missing, never 'pruned 1 ... *'" "$([ $rc = 0 ] && [ "$(has '^XNUB: pruned 0 work tree(s): none$' "$jlog")$(has '^XNUB: work tree offload-gate size=missingMB$' "$jlog")$(has 'pruned 1' "$jlog")" = 110 ] && [ -e "$xw/.offload_host-gate.gate-lock" ] && echo 1 || echo 0)"
# r1 F4: a symlink in work/ is never followed or removed
mkdir -p "$w/outside"; echo keep >"$w/outside/f"; ln -s "$w/outside" "$xw/lnk"; rj gate-pre; jlog=$(cat "$jl/job.log")
ok "xnub (e) r1 F4: a symlink work entry is kept with a WARNING and its target untouched" "$([ -L "$xw/lnk" ] && [ -f "$w/outside/f" ] && [ "$(has "^XNUB: WARNING work entry $xw/lnk is a symlink, not removed" "$jlog")$(has '^XNUB: pruned 0 work tree(s): none$' "$jlog")" = 11 ] && echo 1 || echo 0)"
rm -f "$xw/lnk"
# (c) build failure: gate not run, rc 98, RESULT + log in out/, prune still runs
mkdir -p "$xw/old3" "$xw/old5" "$xw/old6"; touch -d 2020-01-01 "$xw/old3"; touch -d 2024-01-01 "$xw/old5" "$xw/old6"; : >"$w/xnubfail"; rm -f "$w/home/gatefail"; : >"$w/gate-ran"; rj gate-pre; rm -f "$w/xnubfail"; jlog=$(cat "$jl/job.log")
ok "xnub (c): build failure -> job rc 98, message 'XNU stack did not build in the offload-host slot', build RESULT line shown, gate NOT run (no GATE line, no gate-full.log), RESULT FAIL exit 1" "$([ $rc = 1 ] && [ "$(cat "$jl/job.rc")" = 98 ] && [ "$(has 'gate-pre: XNU stack did not build in the offload-host slot' "$out")$(has '^RESULT: FAIL xnu-build errors=1 first=bsd/dev/x.c:9' "$out")$(has 'RESULT: FAIL remote-job job rc 98' "$out")" = 111 ] && [ "$(has 'GATE ' "$jlog")" = 0 ] && [ ! -e "$jl/out/gate-full.log" ] && echo 1 || echo 0)"
ok "xnub (c): failure keeps out/xnu-build.log and still prunes (old3 gone, XNUB line printed)" "$([ -s "$jl/out/xnu-build.log" ] && [ ! -e "$xw/old3" ] && [ "$(has '^XNUB: pruned 1 work tree(s): old3$' "$jlog")" = 1 ] && echo 1 || echo 0)"
ok "xnub (c) r1 F3/F8: on a failure the XNUB lines and the image id still reach the console" "$([ "$(has '^REMOTE: XNUB: pruned 1 work tree(s): old3$' "$out")$(has '^REMOTE: XNUB: build rc=1 RESULT: FAIL xnu-build' "$out")$(has ' xnub=fakeimageid ' "$out")" = 111 ] && echo 1 || echo 0)"
# (d) missing image / toolchain: rc 95 naming the stager and its caller
: >"$w/home/noxnubimage"; rm -f "$w/xnub-count"; rj gate-pre; rm -f "$w/home/noxnubimage"
ok "xnub (d): missing image -> rc 95, message names tools/offload-stage-xnub and its heavy-job caller, build not started" "$([ $rc = 1 ] && [ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has 'XNU image pixos-xnub:trixie-20261111 missing on offload-host: run tools/heavy-job offload-host run --owner NAME --label xnub-stage -- tools/offload-stage-xnub' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
mv "$xa/localbuild/theos" "$w/theos.away"; rj gate-pre; mv "$w/theos.away" "$xa/localbuild/theos"
ok "xnub (d): missing toolchain (localbuild/theos) -> rc 95, message names tools/offload-stage-xnub, build not started" "$([ $rc = 1 ] && [ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has 'localbuild/theos missing on offload-host: run tools/heavy-job offload-host run --owner NAME --label xnub-stage -- tools/offload-stage-xnub' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
mv "$xa/localbuild/hostroot" "$w/hostroot.away"; rm -f "$w/xnub-count"; rj gate-pre; mv "$w/hostroot.away" "$xa/localbuild/hostroot"
ok "xnub (d) r1 F1c: every XNUB_LB_REQUIRED dir is checked (hostroot missing) -> rc 95 naming the stager, build not started" "$([ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has 'localbuild/hostroot missing on offload-host: run tools/heavy-job offload-host run --owner NAME --label xnub-stage -- tools/offload-stage-xnub' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
mv "$xa/git-mirror/hfs.git" "$w/hfs.away"; rm -f "$w/xnub-count"; rj gate-pre; mv "$w/hfs.away" "$xa/git-mirror/hfs.git"
ok "xnub (d) r1 F1: git-mirror/hfs.git missing -> rc 95 (not rc 98 at stager 0019), names $xa/git-mirror, 'NOT mirrored: hfs' and tools/offload-stage-xnub, build not started" "$([ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has "XNU git mirror $xa/git-mirror missing or incomplete in the build container (check rc 0: xnu-src-mirror: NOT mirrored: hfs 0000;RESULT: PASS mirrored=2 of 3 .*;): run tools/heavy-job offload-host run --owner NAME --label xnub-stage -- tools/offload-stage-xnub" "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
mv "$xa/git-mirror" "$w/gm.away"; rm -f "$w/xnub-count"; rj gate-pre; mv "$w/gm.away" "$xa/git-mirror"
ok "xnub (d) r1 F1: the whole git-mirror dir missing -> rc 95 naming the path, build not started" "$([ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has "XNU git mirror $xa/git-mirror missing or incomplete" "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
ok "xnub (d) r2 F1: the mirror check ran INSIDE the build container (a docker run line of the pinned image with bash tools/xnu-src-mirror env --stack xnu/patches --dir /home/user/pixelios-artifacts/git-mirror), not as a host command" "$([ "$(has 'run .* pixos-xnub:trixie-20261111 bash tools/xnu-src-mirror env --stack xnu/patches --dir /home/user/pixelios-artifacts/git-mirror' "$xl")" = 1 ] && echo 1 || echo 0)"
echo error >"$w/xsm-mode"; rm -f "$w/xnub-count"; rj gate-pre; rm -f "$w/xsm-mode"
ok "xnub (d) r2 F5: xnu-src-mirror RESULT: ERROR (stagers do not parse) -> rc 98 'cannot derive the pinned sources', NOT rc 95 / 'run the stager', build not started" "$([ "$(cat "$jl/job.rc")" = 98 ] && [ "$(has 'REFUSED rc 98: tools/xnu-src-mirror cannot derive the pinned sources' "$out")$(has 'RESULT: ERROR no single fetch in 0019-x.sh' "$out")" = 11 ] && [ "$(has 'mirror .* missing or incomplete' "$out")" = 0 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
echo zero >"$w/xsm-mode"; rm -f "$w/xnub-count"; rj gate-pre; rm -f "$w/xsm-mode"
ok "xnub (d) r2 F6: 'mirrored=0 of 0' is not a mirror (the -gt 0 guard) -> rc 95, build not started" "$([ "$(cat "$jl/job.rc")" = 95 ] && [ "$(has 'mirror .* missing or incomplete in the build container (check rc 0: RESULT: PASS mirrored=0 of 0' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
# r1 F9: commits without the tools the in-slot build needs: rc 98 naming the cause (no silent stager failure)
for v in X Y; do cp -r "$repo" "$w/repo$v"; done
git -C "$w/repoX" rm -q tools/gate-xnu-build; git -C "$w/repoX" -c user.email=t@t -c user.name=t commit -qm "no gate-xnu-build"
git -C "$w/repoY" rm -q tools/xnu-src-mirror; git -C "$w/repoY" -c user.email=t@t -c user.name=t commit -qm "no xnu-src-mirror"
rm -f "$w/xnub-count"; out=$(cd "$w/repoX" && tools/remote-job gate-pre 2>&1); rc=$?
ok "xnub (d) r1 F9: a commit without tools/gate-xnu-build -> rc 98, loud 'REFUSED rc 98: the commit under test has no tools/gate-xnu-build', no build" "$([ $rc = 1 ] && [ "$(cat "$w/repoX/logs/remote-jobs/gate-pre/job.rc")" = 98 ] && [ "$(has 'REFUSED rc 98: the commit under test has no tools/gate-xnu-build' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
out=$(cd "$w/repoY" && tools/remote-job gate-pre 2>&1); rc=$?
ok "xnub (d) r1 F1b: a commit without tools/xnu-src-mirror -> rc 98 naming it (the offline build could not fetch its sources), no build" "$([ $rc = 1 ] && [ "$(cat "$w/repoY/logs/remote-jobs/gate-pre/job.rc")" = 98 ] && [ "$(has 'REFUSED rc 98: the commit under test has no tools/xnu-src-mirror' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
# r1 F5: the in-slot build container rj-xnub-<job dir> is the job's container for --collect / --kill-orphan
od=$w/sroot/work/jobs/gate-pre.111.222; mkdir -p "$od"; echo rj-xnub-gate-pre.111.222 >"$w/home/pslist"; : >"$w/home/docker.log"
rj --where offload-host --collect gate-pre.111.222 gate-pre
ok "xnub r1 F5: --collect is refused while rj-xnub-<dir> is alive (container=rj-xnub-gate-pre.111.222 shown), dir kept" "$([ $rc = 2 ] && [ "$(has 'container=rj-xnub-gate-pre.111.222' "$out")$(has 'still alive' "$out")" = 11 ] && [ -d "$od" ] && echo 1 || echo 0)"
rj --where offload-host --kill-orphan gate-pre; dl=$(cat "$w/home/docker.log")
ok "xnub r1 F5: --kill-orphan kills rj-xnub-<dir> of a dead runner (docker kill rj-xnub-gate-pre.111.222, 'killed-container')" "$([ "$(has '^kill rj-xnub-gate-pre\.111\.222$' "$dl")$(has 'action= killed-container' "$out")" = 11 ] && echo 1 || echo 0)"
rm -rf "$od" "$w/home/pslist"
# ---- tools/offload-stage-xnub (review offload-xnub-r1 F6 F7 F11): fake ssh/scp/rsync/docker, never a real host
art=$w/art; mkdir -p "$art/xnu-pristine" "$art/git-mirror"; for d in sdk theos deps hostroot dxb u1; do mkdir -p "$art/localbuild/$d"; done
for d in xnu-pristine git-mirror localbuild/sdk localbuild/theos localbuild/deps localbuild/hostroot localbuild/dxb localbuild/u1; do echo x >"$art/$d/.keep"; done   # non-empty: the stager refuses an empty rsync --delete source (offload-xnub-r2 F7)
cat >"$w/bin/rsync" <<'F'
#!/bin/bash
# fake rsync: `-a --delete -e X SRC/ HOST:DST/` copies SRC into $FAKE_HOME/DST and logs the pair
args=(); while [ $# -gt 0 ]; do case $1 in -a|--delete) shift ;; -e) shift 2 ;; *) args+=("$1"); shift ;; esac; done
echo "rsync ${args[0]} ${args[1]}" >>"$FAKE_HOME/rsync.log"; dst=${args[1]#*:}; case $dst in /*) ;; *) dst=$FAKE_HOME/$dst ;; esac; mkdir -p "$dst"; cp -a "${args[0]}." "$dst"
F
chmod +x "$w/bin/rsync"
st() { : >"$FAKE_LOG"; rm -f "$FAKE_LOG.args" "$w/home/rsync.log"; : >"$w/home/docker.log"; out=$(cd "$repo" && env -u OFFLOAD_QUEUE_HELD OFFLOAD_STAGE_ART="$art" "$@" 2>&1); rc=$?; }
ok "stage: bash -n clean" "$(bash -n tools/offload-stage-xnub && echo 1 || echo 0)"
st tools/offload-stage-xnub --help
ok "stage: --help exits 0 with the usage header (the repo-wide early -h handler of T-7dff:help prints the header, no RESULT line), nothing remote" "$([ $rc = 0 ] && [ "$(has 'tools/offload-stage-xnub \[--validate\] \[--dry-run\]' "$out")$(has '^RESULT: PASS offload-stage-xnub: help$' "$out")" = 10 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
st tools/offload-stage-xnub --bogus
ok "stage: unknown argument -> rc 2, RESULT: ERROR usage, nothing remote" "$([ $rc = 2 ] && [ "$(has "^RESULT: ERROR offload-stage-xnub: usage: .*unknown '--bogus'" "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
st env REMOTE_JOB_HOST=-oProxyCommand tools/offload-stage-xnub --dry-run
ok "stage: a host starting with '-' is refused (rc 2)" "$([ $rc = 2 ] && [ "$(has 'bad REMOTE_JOB_HOST' "$out")" = 1 ] && echo 1 || echo 0)"
st tools/offload-stage-xnub
ok "stage r1 F7: without the offload-host queue slot (OFFLOAD_QUEUE_HELD unset) it REFUSES before any ssh/rsync (rc 2, names the heavy-job caller)" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR offload-stage-xnub: refused: no offload-host queue slot held' "$out")$(has 'tools/heavy-job offload-host run --owner NAME --label xnub-stage -- tools/offload-stage-xnub' "$out")" = 11 ] && [ ! -s "$FAKE_LOG" ] && [ ! -e "$w/home/rsync.log" ] && echo 1 || echo 0)"
mv "$art/localbuild/u1" "$w/u1.away"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub; mv "$w/u1.away" "$art/localbuild/u1"
ok "stage: a missing toolchain source dir on this host -> rc 2 naming the path, nothing staged (no ssh, no rsync)" "$([ $rc = 2 ] && [ "$(has "toolchain source $art/localbuild/u1 missing" "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && [ ! -e "$w/home/rsync.log" ] && echo 1 || echo 0)"
st tools/offload-stage-xnub --dry-run --validate
ok "stage: --dry-run (no queue slot needed) prints every step, runs NOTHING (no ssh/scp/rsync/docker), rsyncs git-mirror and the shared run line is in the printed script" "$([ $rc = 0 ] && [ "$(has '^DRY: rsync -a --delete -e ssh -o BatchMode=yes '"$art"'/git-mirror/ fake.invalid:'"$w"'/sroot/work/xnub-art/git-mirror/$' "$out")$(has '^DRY: scp -q -o BatchMode=yes .*Dockerfile.xnub' "$out")$(has 'xnub_argv "\$HOME/.local/bin/docker"' "$out")$(has '^RESULT: PASS' "$out")" = 1111 ] && [ ! -s "$FAKE_LOG" ] && [ ! -s "$w/home/docker.log" ] && [ ! -e "$w/home/rsync.log" ] && echo 1 || echo 0)"
st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub
ok "stage: staging only (no --validate): image built, git-mirror + toolchain rsynced to offload-host:xnub-art, PASS says 'not validated', every remote command is 'bash -s'" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS offload-stage-xnub: image pixos-xnub:trixie-20261111 (sha256:fakexnubimage) + toolchain staged (not validated' "$out")" = 1 ] && grep -q "^rsync $art/git-mirror/ .*/sroot/work/xnub-art/git-mirror/" "$w/home/rsync.log" && [ "$(wc -l <"$w/home/rsync.log")" = 8 ] && [ "$(sort -u "$FAKE_LOG.args")" = "bash -s" ] && grep -q '^build -q -t pixos-xnub:trixie-20261111' "$w/home/docker.log" && echo 1 || echo 0)"
rm -f "$w/xnub-argv"; : >"$w/home/docker.log"; sl=$(grep -- ' --name rj-xnub-gate-pre' <<<"$xl" | head -n 1)
st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub --validate; vl=$(grep -- ' --name rj-xnub-stage' "$w/home/docker.log" | head -n 1)
nrm() { sed -E 's/--name rj-xnub-[^ ]+/--name N/; s# -v [^ ]*:/job:rw,Z# -v JOB:/job:rw,Z#' <<<"$1"; }
ok "stage r1 F6: --validate runs the build and passes (rc 0, tools/gate-xnu-build argv --repo /job --always --label offload-gate --jobs 4)" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS offload-stage-xnub: .*build validated with the gate-pre run line.*wall 33s .*kernel sha256 0\{64\}' "$out")" = 1 ] && [ "$(cat "$w/xnub-argv" 2>/dev/null)" = "--repo /job --always --label offload-gate --jobs 4" ] && echo 1 || echo 0)"
ok "stage r1 F6: the docker command line of --validate equals gate-pre's (image, mounts, network, user, env, build argv) apart from the container name and the job dir" "$([ -n "$sl" ] && [ -n "$vl" ] && [ "$(nrm "$sl")" = "$(nrm "$vl")" ] && echo 1 || { echo "gate-pre: $sl" >&2; echo "stage:    $vl" >&2; echo 0; })"
ok "stage r1 F6: the shipped tree is a real git checkout (HEAD commit) like gate-pre's, /job mounted :rw" "$([ -d "$w/sroot/work/xnub-stage-job/.git" ] && [ "$(has ':/job:rw,Z' "$vl")" = 1 ] && echo 1 || echo 0)"
# F11: a killed validation kills its fixed-name container
: >"$w/xnubslow"; st env OFFLOAD_QUEUE_HELD=x timeout -s TERM 1 tools/offload-stage-xnub --validate; rm -f "$w/xnubslow"
ok "stage r1 F11: SIGTERM during the validation -> 'killing the validation container' and docker kill rj-xnub-stage on offload-host" "$([ "$(has 'stage: killing the validation container rj-xnub-stage' "$out")" = 1 ] && grep -q '^kill rj-xnub-stage$' "$w/home/docker.log" && echo 1 || echo 0)"
ok "stage r1 F11: ssh/scp/rsync run BatchMode, the validation ssh has ServerAliveInterval" "$([ "$(grep -c 'scp -q -o BatchMode=yes' tools/offload-stage-xnub)" -ge 2 ] && grep -q 'ServerAliveInterval=30' tools/offload-stage-xnub && echo 1 || echo 0)"
ok "stage r2 F2: --validate runs the mirror check in the container BEFORE the build line, and the stager prints the offload-host login uid" "$(n1=$(grep -n 'bash tools/xnu-src-mirror env' "$w/home/docker.log" | head -n 1 | cut -d: -f1); n2=$(grep -n 'bash tools/gate-xnu-build' "$w/home/docker.log" | head -n 1 | cut -d: -f1); [ -n "$n1" ] && [ -n "$n2" ] && [ "$n1" -lt "$n2" ] && echo 1 || echo 0)"
printf '#!/bin/bash\n[ -n "$FAKE_UID" ] && [ "$1" = -u ] && { echo "$FAKE_UID"; exit 0; }\nexec /usr/bin/id "$@"\n' >"$w/bin/id"; chmod +x "$w/bin/id"
st env OFFLOAD_QUEUE_HELD=x FAKE_UID=1234 tools/offload-stage-xnub
ok "stage r2 F2: a offload-host login uid other than 1000 is a loud WARNING (the container runs --user 1000:1000)" "$([ "$(has 'stage: offload-host login uid 1234 ' "$out")$(has 'stage: WARNING offload-host login uid is 1234, not 1000' "$out")" = 11 ] && echo 1 || echo 0)"
st env OFFLOAD_QUEUE_HELD=x FAKE_UID=1000 tools/offload-stage-xnub
ok "stage r2 F2: uid 1000 prints the uid and no WARNING" "$([ "$(has 'stage: offload-host login uid 1000 ' "$out")$(has 'WARNING offload-host login uid' "$out")" = 10 ] && echo 1 || echo 0)"
echo zero >"$w/xsm-mode"; rm -f "$w/xnub-argv"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub --validate; rm -f "$w/xsm-mode"
ok "stage r2 F2: a failing in-container mirror check stops --validate (rc 1, RESULT: FAIL naming the mirror check) and the build is NOT started" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL offload-stage-xnub: pinned-source mirror check failed in the build container' "$out")$(has 'stage: mirror precondition failed in the build container (rc 95), build NOT started' "$out")" = 11 ] && [ ! -e "$w/xnub-argv" ] && ! grep -q 'bash tools/gate-xnu-build' "$w/home/docker.log" && echo 1 || echo 0)"
echo seedfail >"$w/xsm-mode"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub; rm -f "$w/xsm-mode"
ok "stage r2 F6a: a failing mirror seed -> rc 1, RESULT: FAIL 'mirror seed rc=1', nothing staged (no rsync, no ssh)" "$([ $rc = 1 ] && [ "$(has '^RESULT: FAIL offload-stage-xnub: mirror seed rc=1' "$out")" = 1 ] && [ ! -e "$w/home/rsync.log" ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
: >"$w/xsm.log"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub
ok "stage r2 F7: the mirror is seeded from the COMMITTED xnu/patches (a temp archive), not from the working tree" "$([ "$(grep -c '^seed --stack ' "$w/xsm.log")" = 1 ] && ! grep -q "seed --stack $repo/xnu/patches" "$w/xsm.log" && echo 1 || echo 0)"
mv "$repo/tools/xnu-src-mirror" "$w/xsm.away"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub; mv "$w/xsm.away" "$repo/tools/xnu-src-mirror"
ok "stage r2 F3/F6b: tools/xnu-src-mirror missing in the stager checkout -> rc 2 ERROR with the actionable message (stage from a checkout that has tools/xnub-mirror), nothing staged" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR offload-stage-xnub: .*tools/xnu-src-mirror missing: stage from a checkout that has tools/xnub-mirror' "$out")" = 1 ] && [ ! -e "$w/home/rsync.log" ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
cp "$repo/tools/xnu-local-build" "$w/xlb.keep"; printf '#!/bin/bash\n' >"$repo/tools/xnu-local-build"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub --dry-run; cp "$w/xlb.keep" "$repo/tools/xnu-local-build"
ok "stage r2 F3: a tools/xnu-local-build that does not use the mirror -> rc 2 (also under --dry-run)" "$([ $rc = 2 ] && [ "$(has 'tools/xnu-local-build does not use tools/xnu-src-mirror' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$art/git-mirror/.keep"; st env OFFLOAD_QUEUE_HELD=x tools/offload-stage-xnub; echo x >"$art/git-mirror/.keep"
ok "stage r2 F7: an EMPTY source dir is refused before any rsync --delete (rc 2 'is EMPTY', no ssh, no rsync)" "$([ $rc = 2 ] && [ "$(has "toolchain source $art/git-mirror is EMPTY" "$out")" = 1 ] && [ ! -e "$w/home/rsync.log" ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
rm -f "$w/xnub-count"
REMOTE_JOB_XNUB_PREFIX='env --device=/dev/bus/usb' rj gate-pre
ok "xnub: a prefix with --device is refused (rc 93, REMOTE line), no build" "$([ "$(has 'XNU build run line maps host devices' "$out")" = 1 ] && [ ! -e "$w/xnub-count" ] && echo 1 || echo 0)"
REMOTE_JOB_XNUB_PREFIX='adb x' rj gate-pre
ok "xnub: a prefix with a phone word is refused before anything runs" "$([ $rc = 2 ] && echo 1 || echo 0)"
REMOTE_JOB_CENSUS=$w/no-such-census rj gate-pre
ok "gate-pre: commit without tools/tool-census: loud SKIPPED WARNING, gate-pre goes on (r3 F2)" "$([ $rc = 0 ] && [ "$(has 'REMOTE: WARNING census pre-check SKIPPED' "$out")" = 1 ] && echo 1 || echo 0)"
touch "$w/censusfail"; rj gate-pre; rm -f "$w/censusfail"
ok "gate-pre: failing container-view census is refused before ssh (exit 2, RESULT + finding shown, override loud)" "$([ $rc = 2 ] && [ "$(has 'gate-pre refused before shipping: tool-census --check (container view) failed (RESULT: FAIL tools=1)' "$out")$(has 'TOOLS-MD-STALE' "$out")$(has 'REMOTE: WARNING census pre-check replaced' "$out")" = 111 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
REMOTE_JOB_GATE_MANIFEST=$w/no-such-manifest rj gate-pre
ok "gate-pre: commit without tools/gate-manifest: loud SKIPPED WARNING, gate-pre goes on" "$([ $rc = 0 ] && [ "$(has 'REMOTE: WARNING gate-manifest pre-check SKIPPED' "$out")" = 1 ] && echo 1 || echo 0)"
rj gate-pre
ok "gate-pre: fresh gate-manifest proceeds (rc 0, override loud)" "$([ $rc = 0 ] && [ "$(has 'REMOTE: WARNING gate-manifest pre-check replaced' "$out")" = 1 ] && echo 1 || echo 0)"
touch "$w/manifestfail"; rj gate-pre; rm -f "$w/manifestfail"
ok "gate-pre: stale gate-manifest is refused before ssh (exit 2, message + RESULT + STALE shown, no job shipped)" "$([ $rc = 2 ] && [ "$(has 'gate-pre refused before shipping: tools/gate-manifest --check failed (RESULT: FAIL gate-manifest incomplete=0 stale=1); run tools/gate-manifest --write' "$out")$(has 'STALE: tools/gate-manifest.json' "$out")" = 11 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
touch "$repo/xnu/patches/9999-untracked.patch"; rj gate-pre; rm -f "$repo/xnu/patches/9999-untracked.patch"
ok "gate-pre: untracked xnu/patches file is refused before ssh (exit 2, count shown, no job shipped; T-1a6e)" "$([ $rc = 2 ] && [ "$(has '1 untracked file(s) in xnu/patches/' "$out")" = 1 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"
ok "gate-pre: census pre-check analyses the repo under test without the super repo (TOOL_CENSUS_KERNEL=repo, TOOL_CENSUS_SUPER=/nonexistent --check; r2 F1)" "$([ "$(cat "$w/census-argv" 2>/dev/null)" = "$repo /nonexistent --check" ] && echo 1 || echo 0)"
mv "$w/sroot/work/gate-k/tools/third_party" "$w/tp.away"; rj gate-pre; r1=$rc
ok "gate-pre without third_party on offload-host fails loudly" "$([ $r1 = 1 ] && [ "$(has 'third_party missing' "$out")" = 1 ] && echo 1 || echo 0)"
mv "$w/tp.away" "$w/sroot/work/gate-k/tools/third_party"
rm -f "$w/sroot/work/mut-cache-root/fakeimageid/entries"; r13_tree   # TP-048: earlier cases already used the persistent cache; count from here
rj gate-pre; g=$repo/logs/remote-jobs/gate-pre; gl=$(cat "$g/out/gate.log" 2>/dev/null); dl=$(cat "$w/home/docker.log" 2>/dev/null); full=$(git -C "$repo" rev-parse HEAD)
ok "gate-pre: always runs in the container, formal label carries identity sha256 + image id" "$([ $rc = 0 ] && has 'provenance=offload-host gcc[0-9]* formal (offload-host container, identity sha256:[0-9a-f]\{64\}, image sha256:fakeimageid)' "$out" || echo 0)"
ok "gate-pre: RESULT persisted to out/RESULT, full sha in RESULT, GATE line copied verbatim, identity.txt copied" "$([ "$(has "sha=$full tree=clean-at-bundle" "$out")" = 1 ] && [ "$(tail -1 <<<"$out")" = "$(cat "$g/out/RESULT")" ] && [ "$(cat "$g/out/GATE_LINE")" = "GATE PASS stubgate" ] && [ -s "$g/out/identity.txt" ] && echo 1 || echo 0)"
ok "gate-pre: docker run line carries network none, keep-id, stubs PATH, GATE_* env, mounts, image" "$([ "$(has '--network=none' "$dl")$(has '--userns=keep-id' "$dl")$(has '-e PATH=/job/stubs:/usr/local/bin:/usr/bin:/bin' "$dl")$(has '-e GATE_PASS_DIR' "$dl")$(has '-e GATE_UNIT_SHARDS' "$dl")$(has '-e GATE_MUT_VERDICTS' "$dl")$(has ':/job:rw,Z' "$dl")$(has 'sroot/work/opt:/home/user/.local/opt:ro,Z' "$dl")$(has 'pixos-gate:trixie-20261111' "$dl")$(has '-e GATE_PREFIX' "$dl")" = 1111111110 ] && echo 1 || echo 0)"
ok "gate-pre: real docker run line passes the container check the real dirs (stub dir, /usr/bin.. , no dev root)" "$([ "$(has 'bash tmp/rj-check stubs /usr/local/bin:/usr/bin:/bin  /usr/bin:/usr/local/bin:/bin:/sbin:/usr/sbin' "$dl")" = 1 ] && echo 1 || echo 0)"
ok "gate-pre: docker run line sets no TMPDIR" "$([ "$(has 'TMPDIR' "$dl")" = 0 ] && echo 1 || echo 0)"; r13_cases
ok "gate-pre: real docker run line has no --device/--privileged/-v /dev" "$([ "$(has '--device' "$dl")$(has -e -- '--privileged' "$dl")$(has ' /dev' "$dl")$(has 'v /dev' "$dl")" = 0000 ] && echo 1 || echo 0)"
ok "gate-pre: stub dir first on PATH inside the container, adb/fastboot fail 127 loudly" "$([ "$(has 'PATH0=.*/stubs/ ' "$gl")$(has 'adb rc=127' "$gl")$(has 'fastboot rc=127' "$gl")$(has 'STUB adb' "$gl")" = 1111 ] && echo 1 || echo 0)"
ok "gate-pre: GATE_* env reaches the container, host lock off, GATE_XNU_BUILD=0 (gate-xnu-step-r1 F2), shards, NO GATE_STEP_TIMEOUT, no prefix var" "$([ "$(has 'PFX= ' "$gl")$(has 'ENVSH= ' "$gl")$(has 'HOSTLOCK=0 ' "$gl")$(has 'STEPT= ' "$gl")$(has 'SHARDS=3 ' "$gl")$(has 'XNUB=0 ' "$gl")" = 111111 ] && echo 1 || echo 0)"
ok "gate-pre: pass dir and verdicts under src, mut cache = the persistent offload-host dir (mount), TMPDIR NOT set by the wrapper (container /tmp)" "$([ "$(has "PASS=$w/sroot/work/jobs/gate-pre\.[0-9.]*/src/gate-pass " "$gl")$(has "MUTC=$w/sroot/work/mut-cache-root/fakeimageid " "$gl")$(has "TMPD= " "$gl")$(has "VERD=$w/sroot/work/jobs/gate-pre\.[0-9.]*/src/rj-out/verdicts.json" "$gl")" = 1111 ] && echo 1 || echo 0)"
ok "gate-pre: gate.log + verdicts.json fetched, no gate-pass/stamp copied back or left in lab-host cache" "$([ -f "$g/out/verdicts.json" ] && [ -z "$(find "$g" -name 'gate-pass' -o -name stamp)" ] && [ ! -e "$w/home/.cache" ] && [ -z "$(find "$w/sroot/work/jobs" -mindepth 1 -maxdepth 1 ! -name '.*')" ] && echo 1 || echo 0)"
ok "gate-pre: passing run imports gate-full.log and gate-logs.tgz (build dirs excluded)" "$([ "$(head -n 1 "$g/out/gate-full.log" 2>/dev/null)" = "full gate log stub" ] && tar -tzf "$g/out/gate-logs.tgz" | grep -q 'gate/unit.log' && ! tar -tzf "$g/out/gate-logs.tgz" | grep -q 'build-0' && echo 1 || echo 0)"
touch "$w/home/gatefail"; rj gate-pre; rm -f "$w/home/gatefail"
ok "gate-pre: FAILING gate still imports gate-full.log, SHARD FAIL lines appended to gate.log (rule 8)" "$([ $rc = 1 ] && [ "$(head -n 1 "$g/out/gate-full.log" 2>/dev/null)" = "full gate log stub" ] && grep -q '^SHARD FAIL test_x.py' "$g/out/gate.log" && [ "$(has 'gate-full.log' "$out")" = 0 ] && echo 1 || echo 0)"
# TP-048: persistent mutant cache. Run 1 (above) was cold, run 2 (gatefail) already saw run 1's entry; the dir lives outside the job dirs and is 0700.
mcr=$w/sroot/work/mut-cache-root; mcd=$mcr/fakeimageid; r1=$(grep -o 'mutcache=[^ ]*' "$g/out/RESULT" 2>/dev/null)
ok "mutcache: docker run line mounts the persistent offload-host cache rw,Z, GATE_MUT_CACHE passed, dir 0700 outside the job dirs" "$([ "$(has "-v $mcr:/mut-cache:rw,Z" "$dl")$(has '-e GATE_MUT_CACHE' "$dl")" = 11 ] && [ "$(stat -c %a "$mcr")" = 700 ] && [ -d "$mcd" ] && echo 1 || echo 0)"
ok "gate-pre: ~/pixelios-artifacts is a writable (rw,Z) mount of <job>/tmp/art, so a golden mount below it cannot make it root-owned (stack-apply mkdir scratch, offload-host f3 #2)" "$([ "$(has ':/home/user/pixelios-artifacts:rw,Z' "$dl")" = 1 ] && [ "$(grep -o -- '-v [^ ]*/tmp/art:/home/user/pixelios-artifacts:rw,Z' <<<"$dl" | head -n 1 | wc -l)" = 1 ] && echo 1 || echo 0)"
ok "mutcache: persistence, the second run (after the failing one) saw the first run's entry, gate-full.log of run 2 says hits=1" "$([ "$(grep -c entry "$mcd/entries")" = 2 ] && grep -q 'MUTCACHE cache=ok hits=1 fresh=4' "$g/out/gate-full.log" && echo 1 || echo 0)"
rj gate-pre
ok "mutcache: RESULT tail has mutcache=HITS/FRESH/ok warn=0 (3rd run: hits=2), no REMOTE: MUTCACHE line" "$([ $rc = 0 ] && [ "$(has 'elapsed=[0-9]*s mutcache=2/3/ok warn=0 ' "$out")$(has '^REMOTE: MUTCACHE' "$out")" = 10 ] && echo 1 || echo 0)"
echo missing >"$w/home/mcmode"; rj gate-pre
ok "mutcache: missing MUTCACHE line => REMOTE: MUTCACHE + warn=1 in the RESULT tail (no silent fallback)" "$([ "$(has '^REMOTE: MUTCACHE line missing' "$out")$(has 'mutcache=?/?/missing warn=1 ' "$out")" = 11 ] && echo 1 || echo 0)"
echo ro >"$w/home/mcmode"; rj gate-pre
ok "mutcache: cache=ro (store_failed>0) => two REMOTE: MUTCACHE lines + warn=2" "$([ "$(has '^REMOTE: MUTCACHE state cache=ro' "$out")$(has '^REMOTE: MUTCACHE store_failed=5' "$out")$(has 'mutcache=0/5/ro warn=2 ' "$out")" = 111 ] && echo 1 || echo 0)"
echo mismatch >"$w/home/mcmode"; rj gate-pre
ok "mutcache: spot-check MISMATCH, renamed => REMOTE: MUTCACHE ... quarantined: <id>.bad-N + warn=1" "$([ "$(has '^REMOTE: MUTCACHE spot-check MISMATCH, the cache dir was quarantined: offload-host:'"$w"'/sroot/work/mut-cache-root/fakeimageid.bad-1700000001 ' "$out")$(has 'NOT quarantined' "$out")$(has 'warn=1 ' "$out")" = 101 ] && echo 1 || echo 0)"
echo mismatch-qfail >"$w/home/mcmode"; rj gate-pre
ok "mutcache: spot-check MISMATCH, quarantine failed => REMOTE: MUTCACHE ... NOT quarantined (delete by hand), never 'was quarantined' (review r1 F1, rule 8)" "$([ "$(has '^REMOTE: MUTCACHE spot-check MISMATCH and the cache was NOT quarantined (MUTCACHE MISMATCH: quarantine failed: \[Errno 16\]' "$out")$(has 'was quarantined' "$out")$(has 'warn=1 ' "$out")" = 101 ] && echo 1 || echo 0)"
rm -f "$w/home/mcmode"; mkdir -p "$mcr/fakeimageid.bad-1700000000/aa"; echo x >"$mcr/fakeimageid.bad-1700000000/aa/k"; rj gate-pre
ok "mutcache: resubmit check lists mut-cache.bad-* dirs (age, size), never deletes them" "$([ "$(has '^REMOTE: MUTCACHE quarantined dir offload-host:'"$w"'/sroot/work/mut-cache-root/fakeimageid.bad-1700000000 age=[0-9]*s size=[0-9]*KiB' "$out")" = 1 ] && [ -f "$mcr/fakeimageid.bad-1700000000/aa/k" ] && echo 1 || echo 0)"
rm -rf "$mcr/fakeimageid.bad-1700000000"
# R12: memory.peak is read INSIDE the container after the gate (the fake docker sets RJ_MEMPEAK_FILE to $FAKE_HOME/mempeak when that file exists)
printf '1\n' >"$w/home/mempeak"; rj gate-pre
ok "gate-pre: container memory.peak lands on the RESULT line (mempeak=4242B) and in out/memory.peak, no REMOTE memory.peak WARN" "$([ $rc = 0 ] && [ "$(has ' mempeak=4242B ' "$out")$(has '^REMOTE: memory.peak not recorded' "$out")" = 10 ] && [ "$(cat "$g/out/memory.peak" 2>/dev/null)" = 4242 ] && echo 1 || echo 0)"
rm -f "$w/home/mempeak"; rj gate-pre
ok "gate-pre: no memory.peak in the container => mempeak=UNKNOWN + a loud REMOTE WARN line, the gate rc is kept (rule 8)" "$([ $rc = 0 ] && [ "$(has ' mempeak=UNKNOWN ' "$out")$(has '^REMOTE: memory.peak not recorded (unavailable)' "$out")" = 11 ] && echo 1 || echo 0)"
touch "$w/home/gatefail"; printf '1\n' >"$w/home/mempeak"; rj gate-pre; rm -f "$w/home/gatefail" "$w/home/mempeak"
ok "gate-pre: a FAILING gate keeps its rc through the memory.peak wrapper and still records the peak (read after the gate: 4242, not the initial 1)" "$([ $rc = 1 ] && [ "$(cat "$g/out/memory.peak" 2>/dev/null)" = 4242 ] && echo 1 || echo 0)"
ok "gate-pre: the production default path is /sys/fs/cgroup/memory.peak (cgroup of the container, not memory.current)" "$([ "$(grep -c ':-/sys/fs/cgroup/memory.peak}' tools/remote-job)" = 1 ] && echo 1 || echo 0)"
touch "$w/home/nopeakfile"; rj gate-pre; rm -f "$w/home/nopeakfile"
ok "gate-pre: container killed before the peak is read => mempeak=UNKNOWN + REMOTE line naming the missing file and the kill cause, a FAIL result (rule 8)" "$([ $rc = 1 ] && [ "$(has ' mempeak=UNKNOWN ' "$out")$(has '^REMOTE: memory.peak not recorded (out/memory.peak missing).*killed before the gate ended' "$out")" = 11 ] && echo 1 || echo 0)"
touch "$w/home/tmpdir-in-job"; rj gate-pre; rm -f "$w/home/tmpdir-in-job"
ok "gate-pre: TMPDIR inside the checkout work tree => container check REFUSES, gate not started (AP-110)" "$([ $rc = 2 ] && [ "$(has 'TMPDIR .* is inside a git work tree' "$out")$(has 'container check failed' "$out")" = 11 ] && echo 1 || echo 0)"
touch "$w/home/idalter"; rj gate-pre; rm -f "$w/home/idalter"
ok "gate-pre: altered container identity => identity MISMATCH, NOT formal, diff kept" "$([ "$(has 'identity MISMATCH (see .*identity.diff), NOT formal' "$out")$(has 'formal (offload-host' "$out")" = 10 ] && [ -s "$g/identity.diff" ] && echo 1 || echo 0)"
cp "$repo/tools/gate-identity.expected" "$w/exp.save"; sed -i 's/^stub=ok/stub=TODO/' "$repo/tools/gate-identity.expected"; git -C "$repo" -c user.email=t@t -c user.name=t commit -q -m todo tools/gate-identity.expected; rj gate-pre
ok "gate-pre: TODO field in the expected file => MISMATCH, never formal" "$([ "$(has 'identity MISMATCH (tool-side tools/gate-identity.expected missing or has TODO' "$out")$(has 'formal (offload-host' "$out")" = 10 ] && echo 1 || echo 0)"
git -C "$repo" reset -q --hard HEAD~1
REMOTE_JOB_GATE_PREFIX=true REMOTE_JOB_IDENTITY_PROOF=proof:abc rj gate-pre
ok "gate-pre: prefix 'true' + fake proof env: identity MISSING, NOT formal, proof ignored loudly" "$([ "$(has 'identity MISSING, NOT formal' "$out")$(has 'formal (offload-host' "$out")$(has '^REMOTE: WARNING REMOTE_JOB_IDENTITY_PROOF is ignored' "$out")" = 101 ] && echo 1 || echo 0)"
printf '#!/bin/bash\ncase "$*" in *"tmp/rj-identity"*) cat %s/tools/gate-identity.expected ;; *) exec "$@" ;; esac\n' "$repo" >"$w/bin/idprefix"; chmod +x "$w/bin/idprefix"
REMOTE_JOB_GATE_PREFIX="$w/bin/idprefix" rj gate-pre
ok "gate-pre: test prefix that prints the expected identity => formal" "$([ $rc = 0 ] && has 'formal (offload-host container, identity sha256:' "$out" || echo 0)"
REMOTE_JOB_GATE_PREFIX="$w/bin/idprefix" rj gate-identity
ok "gate-identity job: runs only the identity (no gate.log), shows the would-be formal label, never itself formal" "$([ $rc = 0 ] && [ "$(has 'REMOTE: gate-pre on this commit would be labelled: offload-host gcc.* formal (offload-host container, identity sha256:' "$out")$(has 'identity check only, identity OK' "$out")$(has 'provenance=.*formal' "$out")" = 110 ] && [ ! -e "$repo/logs/remote-jobs/gate-identity/out/gate.log" ] && echo 1 || echo 0)"
# ---- TP-050 LAB-HOST LANE: --where lab-host = the offload-host route through tools/rj-local-transport (no ssh/scp), FAKE podman at the pinned name, fake heavy-job; never a real container
sed 's#\$FAKE_HOME#${FAKE_HOME:-$HOME}#g; s#docker\.log#podman.log#; s#^case \$1 in image)#case $1 in info) cat "$HOME/rootless"; exit 0 ;; image)#' "$w/home/.local/bin/docker" >"$w/bin/podman"; chmod +x "$w/bin/podman"
hrj() { : >"$FAKE_LOG"; : >"$w/home/docker.log"; : >"$w/home/podman.log"; : >"$w/hj-hl"; echo "${FAKE_ROOTLESS:-true}" >"$w/home/rootless"; out=$(cd "${HR:-$repo}" && env -u REMOTE_JOB_HOST -u HEAVY_JOB_HELD -u RJ_LAB_HELD REMOTE_JOB_MINFREE_GIB=1 HOME="$w/home" HEAVY_JOB_LOCK="$w/hl.lock" RJ_HOST_TEST=1 REMOTE_JOB_LAB_PODMAN="$w/bin/podman" "$@" 2>&1); rc=$?; }
# bootstrap: seed sources = this host's ~/.local/opt, the repo's tools/gate-image (tracked) and tools/third_party (untracked, in the checkout-local exclude)
mkdir -p "$w/home/.local/opt" "$repo/tools/gate-image" "$repo/tools/third_party"; echo : >"$repo/tools/gate-image/verlist.sh"; echo : >"$repo/tools/third_party/mkbootimg.py"; echo : >"$w/home/work/pixos/gate-k/tools/third_party/mkbootimg.py"; echo /tools/third_party/ >>"$repo/.git/info/exclude"
git -C "$repo" add tools/gate-image; git -C "$repo" -c user.email=t@t -c user.name=t commit -q -m gate-image tools/gate-image
rm -f "$w/xnu-argv" "$w/xnub-argv" "$w/xnub-count"; hrj tools/remote-job --where lab-host gate-pre; pl=$(cat "$w/home/podman.log"); last=$(tail -n 1 <<<"$out")
ok "xnub (f): lab-host route still runs the LOCAL xnu pre-step (--repo <repo> --always) and no in-slot build (no rj-xnub container, no xnub argv)" "$([ "$(cat "$w/xnu-argv" 2>/dev/null)" = "--repo $repo --always" ] && [ ! -e "$w/xnub-argv" ] && [ "$(has 'rj-xnub' "$pl")" = 0 ] && echo 1 || echo 0)"
ok "lab-host: gate-pre PASS, RESULT where=lab-host, provenance=lab-host ... formal (lab-host container, identity sha256:<hex>, image <id>), no 'offload-host' in the RESULT line" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS remote-job gate-pre where=lab-host sha=[0-9a-f]\{40\} tree=clean-at-bundle ' "$last")$(has 'provenance=lab-host gcc[0-9]* formal (lab-host container, identity sha256:[0-9a-f]\{64\}, image sha256:fakeimageid)' "$last")$(has offload-host "$last")" = 110 ] && echo 1 || echo 0)"
ok "lab-host: local transport only (fake ssh/scp never called), job ran through it" "$([ ! -s "$FAKE_LOG" ] && [ -s "$repo/logs/remote-jobs/gate-pre/out/identity.txt" ] && echo 1 || echo 0)"
ok "lab-host: container runtime is the pinned podman path (rootless info asked, run line has network none/keep-id/image), the docker fake is never called" "$([ "$(has 'info --format {{.Host.Security.Rootless}}' "$pl")$(has '^run .*--network=none --userns=keep-id' "$pl")$(has 'pixos-gate:trixie-20261111' "$pl")" = 111 ] && [ ! -s "$w/home/docker.log" ] && echo 1 || echo 0)"
ok "lab-host: runs under tools/heavy-job --class gate (the driver's local lock), label rj-gate-pre-lab-host, no offload-host queue" "$([ "$(cat "$w/hj-hl")" = "run --class gate --label rj-gate-pre-lab-host --wait 1800 -- $repo/tools/remote-job --where lab-host gate-pre" ] && echo 1 || echo 0)"
touch "$w/xnufail"; hrj tools/remote-job --where lab-host gate-pre; rm -f "$w/xnufail"
ok "xnub (f): lab-host with a broken XNU stack is refused by the local pre-step (RESULT: ERROR, exit 2) before the lock" "$([ $rc = 2 ] && [ "$(has 'gate-pre refused before shipping: the XNU stack did not build locally' "$out")" = 1 ] && echo 1 || echo 0)"
ok "lab-host: remote job dir removed after collect" "$(! compgen -G "$w/home/work/pixos/jobs/gate-pre.*" >/dev/null && echo 1 || echo 0)"
mv "$w/home/work/pixos" "$w/home/work/pixos.keep"; hrj tools/remote-job --where lab-host gate-pre; hlmk=$([ -d "$w/home/work/pixos" ] && echo 1 || echo 0)
hlsd=$([ -L "$w/home/work/pixos/opt" ] && [ -f "$w/home/work/pixos/gate-image-20261111/verlist.sh" ] && [ -f "$w/home/work/pixos/gate-k/tools/third_party/mkbootimg.py" ] && [ ! -L "$w/home/work/pixos/gate-k/tools/third_party" ] && echo 1 || echo 0); rm -rf "$w/home/work/pixos"; mv "$w/home/work/pixos.keep" "$w/home/work/pixos"
ok "lab-host: a missing ~/work/pixelios-work is created with a loud REMOTE line and the gate-pre goes on (20q 2026-10-29: first lane run died in the probe)" "$([ "$(has '^REMOTE: lab-host: created .*/work/pixos' "$out")" = 1 ] && [ "$(has 'no ~/work/pixelios-work\|probe failed' "$out")" = 0 ] && [ "$hlmk" = 1 ] && echo 1 || echo 0)"
ok "lab-host: gate prerequisites (opt link, gate-image context, gate-k/tools/third_party) are seeded with one loud REMOTE line each (20q run 2: 'tools/third_party missing', rc 98)" "$([ "$(has '^REMOTE: lab-host: linked .*/opt ->' "$out")$(has '^REMOTE: lab-host: seeded .*/gate-image-20261111' "$out")$(has '^REMOTE: lab-host: seeded .*/gate-k/tools/third_party' "$out")" = 111 ] && [ "$hlsd" = 1 ] && echo 1 || echo 0)"
rm -rf "$w/home/.local/opt" "$w/home/work/pixos/opt"; mv "$w/home/work/pixos/gate-k/tools/third_party" "$w/tp.away2"; mv "$w/home/work/pixos/gate-image-20261111" "$w/gi.away"; hrj tools/remote-job --where lab-host gate-pre; mv "$w/tp.away2" "$w/home/work/pixos/gate-k/tools/third_party"; mv "$w/gi.away" "$w/home/work/pixos/gate-image-20261111"
ok "lab-host: a prerequisite that cannot be seeded (no ~/.local/opt) refuses loudly, gate not started" "$([ $rc != 0 ] && [ "$(has 'lab-host: .*/.local/opt missing, cannot seed' "$out")$(has '^run ' "$(cat "$w/home/podman.log")")" = 10 ] && echo 1 || echo 0)"
mkdir -p "$w/home/.local/opt"
# review lab-host-bootstrap-r1: (F1) a worktree's tools/third_party is a LINK into the main clone => the seed is a real copy; (F2) a dir without its marker file is refused, never trusted; (F4) the worktree fallback
mv "$w/home/work/pixos" "$w/home/work/pixos.keep"; rm -rf "$repo/tools/third_party"; mkdir -p "$w/tp.real" "$w/wt-tools"; echo : >"$w/tp.real/mkbootimg.py"; ln -s "$w/tp.real" "$repo/tools/third_party"
hrj tools/remote-job --where lab-host gate-pre; hlf1=$([ -f "$w/home/work/pixos/gate-k/tools/third_party/mkbootimg.py" ] && [ ! -L "$w/home/work/pixos/gate-k/tools/third_party" ] && echo 1 || echo 0)
rm -rf "$w/home/work/pixos/gate-k"; mkdir -p "$w/home/work/pixos/gate-k/tools/third_party"; hrj tools/remote-job --where lab-host gate-pre
ok "lab-host (r1 F1): a linked tools/third_party source is seeded as a real copy, not a link" "$hlf1"
ok "lab-host (r1 F2): an existing gate-k/tools/third_party without mkbootimg.py is refused loudly (half-copied seed), gate not started" "$([ $rc != 0 ] && [ "$(has 'third_party has no mkbootimg.py (half-copied seed?)' "$out")$(has '^run ' "$(cat "$w/home/podman.log")")" = 10 ] && echo 1 || echo 0)"
rm -rf "$w/home/work/pixos" "$repo/tools/third_party"; mv "$w/home/work/pixos.keep" "$w/home/work/pixos"; mkdir -p "$repo/tools/third_party"; echo : >"$repo/tools/third_party/mkbootimg.py"
git -C "$repo" worktree add -q "$w/wt" HEAD; mv "$w/home/work/pixos" "$w/home/work/pixos.keep"
HR="$w/wt" hrj tools/remote-job --where lab-host gate-pre; hlf4=$([ -f "$w/home/work/pixos/gate-k/tools/third_party/mkbootimg.py" ] && echo 1 || echo 0); hlf4s=$(has "seeded .*gate-k/tools/third_party from $repo/tools/third_party" "$out")
rm -rf "$w/home/work/pixos"; mv "$w/home/work/pixos.keep" "$w/home/work/pixos"; git -C "$repo" worktree remove --force "$w/wt"
ok "lab-host (r1 F4): run from a worktree without tools/third_party, the seed comes from the main clone" "$([ "$hlf4" = 1 ] && [ "$hlf4s" = 1 ] && echo 1 || echo 0)"
FAKE_ROOTLESS=false hrj tools/remote-job --where lab-host gate-pre; pl=$(cat "$w/home/podman.log")
ok "lab-host: podman not rootless => job rc 95, loud 'not rootless podman' line, gate never started, no run line" "$([ $rc = 1 ] && [ "$(has 'not rootless podman' "$out")$(has 'job rc 95' "$out")$(has '^RESULT: FAIL' "$out")" = 111 ] && [ "$(has '^run ' "$pl")" = 0 ] && [ ! -s "$w/home/docker.log" ] && echo 1 || echo 0)"
ok "lab-host (r1 F2): no own memory floor in remote-job, no --mem-gb (heavy-job's class-gate gate is the one check)" "$(! grep -q 'RJ_LAB_MIN_GB\|RJ_LAB_MEMINFO\|--mem-gb' "$repo/tools/remote-job" && echo 1 || echo 0)"
hrj env RJ_LAB_HELD=1 tools/remote-job --where lab-host gate-pre
ok "lab-host (r1 F1): RJ_LAB_HELD=1 by hand without the lock -> refused loudly, heavy-job and podman never called" "$([ $rc = 2 ] && [ "$(has 'RJ_LAB_HELD is set but the heavy-job lock is not held' "$out")" = 1 ] && [ ! -s "$w/hj-hl" ] && [ "$(has '^run ' "$(cat "$w/home/podman.log")")" = 0 ] && echo 1 || echo 0)"
echo "rj-gate-pre-lab-host gate 1 now" >"$w/hl.lock"; hrj env RJ_LAB_HELD=1 HEAVY_JOB_HELD=rj-gate-pre-lab-host tools/remote-job --where lab-host gate-pre
ok "lab-host (r1 F1): forged holder line + HEAVY_JOB_HELD but no flock -> refused ('is not locked')" "$([ $rc = 2 ] && [ "$(has 'is not locked' "$out")" = 1 ] && echo 1 || echo 0)"
hrj bash -c 'exec 7>>"$HEAVY_JOB_LOCK"; flock -n 7 || exit 9; echo "outer-gate gate $$ now" >"$HEAVY_JOB_LOCK"; HEAVY_JOB_HELD=outer-gate RJ_LAB_HELD=1 exec tools/remote-job --where lab-host gate-pre'
ok "lab-host (r2 nesting): inside an OUTER heavy-job (HEAVY_JOB_HELD=outer label, its flock held by an ancestor) the lane runs" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS remote-job gate-pre where=lab-host' "$out")" = 1 ] && echo 1 || echo 0)"
hrj env -u RJ_HOST_TEST tools/remote-job --where lab-host gate-pre
ok "lab-host (r1 F4): REMOTE_JOB_LAB_PODMAN without RJ_HOST_TEST=1 is refused, nothing started" "$([ $rc = 2 ] && [ "$(has 'host-test hook (needs RJ_HOST_TEST=1)' "$out")" = 1 ] && [ ! -s "$w/hj-hl" ] && echo 1 || echo 0)"
out=$(cd "$w" && GATE_CPU_PRINT_ONLY=1 "$repo/tools/rj-local-transport" ssh -o BatchMode=yes lab-host 'echo "pwd=$PWD gate=${GATE_CPU_PRINT_ONLY:-unset}"' 2>&1)
ok "rj-local-transport (r1 F3): ssh runs in \$HOME with a clean env (caller's GATE_* and cwd do not leak)" "$([ "$out" = "pwd=$HOME gate=unset" ] && echo 1 || echo 0)"
mkdir -p "$w/lt/src"; echo real >"$w/lt/real"; ln -sf "$w/lt/real" "$w/lt/src/link"; "$repo/tools/rj-local-transport" scp -q -r "$w/lt/src" "$w/lt/dst"
ok "rj-local-transport (r1 F5): scp -r copies the symlink target like scp, not the link" "$([ -f "$w/lt/dst/link" ] && [ ! -L "$w/lt/dst/link" ] && echo 1 || echo 0)"
for hj in unit ci-local gate-identity; do hrj tools/remote-job --where lab-host $hj
	ok "lab-host: --where lab-host $hj refused (offload-only, only gate-pre may run on lab-host): REMOTE: line + RESULT: ERROR exit 2, no ssh/podman/lock" "$([ $rc = 2 ] && [ "$(has '^REMOTE: .*offload-host only; --where lab-host refused' "$out")$(has '^RESULT: ERROR remote-job' "$out")" = 11 ] && [ ! -s "$FAKE_LOG" ] && [ ! -s "$w/home/podman.log" ] && [ ! -s "$w/hj-hl" ] && echo 1 || echo 0)"; done
hrj tools/remote-job --where lab-host selftest
ok "lab-host: a non-offload-only job (selftest) is allowed and labelled lab-host" "$([ $rc = 0 ] && [ "$(has 'where=lab-host' "$out")$(has 'provenance=lab-host gcc[0-9]* (pre-validation)' "$out")" = 11 ] && echo 1 || echo 0)"
hrj env REMOTE_JOB_LAB_PODMAN=/usr/bin/docker tools/remote-job --where lab-host gate-pre
ok "lab-host: a docker path as the container runtime is refused (basename must be podman), nothing started" "$([ $rc = 2 ] && [ "$(has 'container runtime must be podman' "$out")" = 1 ] && [ ! -s "$w/hj-hl" ] && echo 1 || echo 0)"
# offload-host behaviour byte-identical: the exact RESULT line, docker (not podman), no heavy-job lock
: >"$w/home/podman.log"; : >"$w/hj-hl"; rj gate-pre; last=$(tail -n 1 <<<"$out"); full=$(git -C "$repo" rev-parse HEAD)
norm=$(sed -E 's/elapsed=[0-9]+s/elapsed=Ns/; s/ mutcache=[^ ]+ warn=[0-9]+ mempeak=[^ ]+ xnub=fakeimageid/ mutcache=M/; s/host=[^ ]+ gcc=[^ ]* glibc=[^ ]* nice=19/PROV/; s/provenance=offload-host gcc[0-9]*/provenance=offload-host gccN/; s/identity sha256:[0-9a-f]{64}/identity sha256:HEX/' <<<"$last")
ok "offload-host unchanged: byte-identical gate-pre RESULT line (where=offload-host, provenance=offload-host ... formal (offload-host container ...)), docker used, podman and the lab-host lock untouched" "$([ "$norm" = "RESULT: PASS remote-job gate-pre where=offload-host sha=$full tree=clean-at-bundle elapsed=Ns mutcache=M PROV provenance=offload-host gccN formal (offload-host container, identity sha256:HEX, image sha256:fakeimageid) log=$repo/logs/remote-jobs/gate-pre/job.log" ] && [ ! -s "$w/home/podman.log" ] && [ ! -s "$w/hj-hl" ] && echo 1 || echo 0)"
# mutants of tools/remote-job (in a copy): each must lose a lab-host assertion
hlmut() { # NAME SED-EXPR [env...] ; runs the mutated tool's lab-host gate-pre, sets $out/$rc, leaves $w/home/{podman,docker}.log
	rm -rf "$w/repoHm"; cp -r "$repo" "$w/repoHm"; rm -rf "$w/repoHm/logs"; sed -i "$2" "$w/repoHm/tools/remote-job"
	git -C "$w/repoHm" -c user.email=t@t -c user.name=t commit -q -am mut; cmp -s "$repo/tools/remote-job" "$w/repoHm/tools/remote-job" && { echo "FAIL mutant $1 did not change the tool"; bad=$((bad+1)); }
	HR=$w/repoHm; shift 2; local fr=true a; for a; do case $a in FAKE_ROOTLESS=*) fr=${a#*=} ;; esac; done; FAKE_ROOTLESS=$fr hrj env "$@" tools/remote-job --where lab-host gate-pre; unset HR
}
hlmut norootless '/Host\.Security\.Rootless/d' FAKE_ROOTLESS=false
ok "mutation: rootless check dropped -> the rootless=false case is no longer refused (killed by the rootless refusal case)" "$([ $rc = 0 ] && [ "$(has '^run ' "$(cat "$w/home/podman.log")")" = 1 ] && echo 1 || echo 0)"
hlmut docker 's#^\tDK=\${RJ_DK:-\$HOME/\.local/bin/docker}#\tDK=$HOME/.local/bin/docker#' FAKE_ROOTLESS=true
ok "mutation: docker used on lab-host -> docker.log gets the run line and podman none (killed by the podman-only case)" "$([ -s "$w/home/docker.log" ] && [ "$(has '^run ' "$(cat "$w/home/podman.log")")" = 0 ] && echo 1 || echo 0)"
hlmut nolock 's#HEAVY_JOB_QUIET=1 RJ_LAB_HELD=1 exec #HEAVY_JOB_QUIET=1 RJ_LAB_HELD=1 true #' FAKE_ROOTLESS=true
ok "mutation: heavy-job lock wrapper dropped -> nothing runs under the lock (killed by the heavy-job argv case)" "$([ -s "$w/hj-hl" ] && echo 0 || echo 1)"
# tool-side overlay: identity script + pin come from the repo holding the running tool (toolT), never from the commit under test (REMOTE_JOB_REPO)
G2="git -c user.email=t@t -c user.name=t"
cp -r "$repo" "$w/toolT"; rm -rf "$w/toolT/logs"
cp -r "$repo" "$w/repoN"; rm -rf "$w/repoN/logs"; $G2 -C "$w/repoN" rm -q tools/gate-identity tools/gate-identity.expected; $G2 -C "$w/repoN" commit -qm "predates gate-identity"
out=$(cd "$w/repoN" && REMOTE_JOB_REPO=$w/repoN "$w/toolT/tools/remote-job" gate-pre 2>&1); rc=$?
ok "overlay: a commit WITHOUT tools/gate-identity gets the formal label from the tool-side script+pin" "$([ $rc = 0 ] && [ "$(has 'formal (offload-host container, identity sha256:' "$out")$(has 'NOT formal' "$out")" = 10 ] && [ ! -e "$w/repoN/tools/gate-identity" ] && echo 1 || echo 0)"
cp -r "$w/repoN" "$w/repoM"; rm -rf "$w/repoM/logs"
printf '#!/bin/bash\necho "image=$GATE_IMAGE_ID"; echo "stub=evil"\n' >"$w/repoM/tools/gate-identity"; chmod +x "$w/repoM/tools/gate-identity"; printf 'image=sha256:fakeimageid\nstub=evil\n' >"$w/repoM/tools/gate-identity.expected"
$G2 -C "$w/repoM" add tools/gate-identity tools/gate-identity.expected; $G2 -C "$w/repoM" commit -qm "own identity+pin"
out=$(cd "$w/repoM" && REMOTE_JOB_REPO=$w/repoM "$w/toolT/tools/remote-job" gate-pre 2>&1); rc=$?
ok "overlay mutant: a commit carrying its OWN gate-identity+pin cannot change the outcome (identity.txt is the tool's, stub=ok, still formal)" "$([ $rc = 0 ] && [ "$(has 'formal (offload-host container' "$out")" = 1 ] && grep -q '^stub=ok$' "$w/repoM/logs/remote-jobs/gate-pre/out/identity.txt" && ! grep -q evil "$w/repoM/logs/remote-jobs/gate-pre/out/identity.txt" && echo 1 || echo 0)"
cp -r "$w/toolT" "$w/toolU"; sed -i 's/^stub=ok/stub=TODO/' "$w/toolU/tools/gate-identity.expected"
out=$(cd "$w/repoM" && REMOTE_JOB_REPO=$w/repoM "$w/toolU/tools/remote-job" gate-pre 2>&1); rc=$?
ok "overlay: tool-side pin with TODO => not formal even though the commit's own pin is complete" "$([ "$(has 'identity MISMATCH (tool-side tools/gate-identity.expected missing or has TODO' "$out")$(has 'provenance=.*formal (offload-host' "$out")" = 10 ] && echo 1 || echo 0)"
rm "$w/toolU/tools/gate-identity.expected"
out=$(cd "$w/repoN" && REMOTE_JOB_REPO=$w/repoN "$w/toolU/tools/remote-job" gate-pre 2>&1); rc=$?
ok "overlay: tool-side pin absent => not formal (the commit under test never supplies it)" "$([ "$(has 'identity MISMATCH (tool-side tools/gate-identity.expected missing' "$out")$(has 'provenance=.*formal (offload-host' "$out")" = 10 ] && echo 1 || echo 0)"
REMOTE_JOB_GATE_PREFIX='env PFX=1 A=* B=t*' rj gate-pre; gl=$(cat "$g/out/gate.log")
ok "gate-pre prefix is applied (PFX=1) and glob-safe (A=* B=t* literal)" "$([ "$(has 'PFX=1 GLOBA=\* GLOBB=t\* ' "$gl")" = 1 ] && echo 1 || echo 0)"
echo x >>"$repo/xnu/patches/0001.patch"; rj --where offload-host selftest
ok "tracked change outside the job inputs refused (whole-tree check)" "$([ $rc = 2 ] && [ "$(has 'uncommitted tracked changes' "$out")" = 1 ] && echo 1 || echo 0)"
git -C "$repo" checkout -q xnu/patches/0001.patch
# ---- gate-adt-mount: the Apple ADT is shipped to offload-host:$w/sroot/work/adt and mounted READ-ONLY at /job/research/ipsw-x/d22ap.adt; identity.txt gets adt=<sha256 seen inside the container>|none
lastid() { tail -n 1 "$g/out/identity.txt" 2>/dev/null; }
: >"$w/home/docker.log"; rj gate-pre
ok "ADT absent locally: no ADT mount in the docker run line, identity.txt ends adt=none, nothing shipped, still formal" "$([ $rc = 0 ] && ! grep -q 'd22ap.adt' "$w/home/docker.log" && [ "$(lastid)" = adt=none ] && [ "$(has 'shipping the ADT' "$out")" = 0 ] && [ ! -e "$w/sroot/work/adt/d22ap.adt" ] && [ "$(has 'formal (offload-host' "$out")" = 1 ] && echo 1 || echo 0)"
mkdir -p "$repo/research/ipsw-x"; printf 'APPLE-ADT-BYTES-1' >"$repo/research/ipsw-x/d22ap.adt"; lh=$(sha256sum <"$repo/research/ipsw-x/d22ap.adt" | cut -d' ' -f1)
: >"$w/home/docker.log"; rj gate-pre
ok "ADT present locally: run line mounts the shipped file at /job/research/ipsw-x/d22ap.adt and it is :ro" "$([ $rc = 0 ] && [ "$(grep -c -- "-v $w/sroot/work/adt/d22ap.adt:/job/research/ipsw-x/d22ap.adt:ro,Z " "$w/home/docker.log")" -ge 1 ] && ! grep -q 'd22ap.adt:rw' "$w/home/docker.log" && echo 1 || echo 0)"
ok "ADT present: shipped (remote sha256 == local), identity.txt ends adt=<sha256 measured in the container>, formal" "$([ "$(sha256sum <"$w/sroot/work/adt/d22ap.adt" | cut -d' ' -f1)" = "$lh" ] && [ "$(lastid)" = "adt=$lh" ] && [ "$(has 'shipping the ADT' "$out")" = 1 ] && [ "$(has 'formal (offload-host' "$out")" = 1 ] && echo 1 || echo 0)"
ok "ADT stays out of git, the logs and the local job output (rule 4)" "$(! git -C "$repo" ls-files | grep -q research && ! grep -rq APPLE-ADT "$repo/logs" && [ "$(git -C "$repo" status --porcelain --untracked-files=no | wc -l)" = 0 ] && echo 1 || echo 0)"
rj gate-pre
ok "ADT unchanged on offload-host: not copied again (remote sha256 equal)" "$([ $rc = 0 ] && [ "$(has 'shipping the ADT' "$out")" = 0 ] && [ "$(lastid)" = "adt=$lh" ] && echo 1 || echo 0)"
printf 'APPLE-ADT-BYTES-2' >"$repo/research/ipsw-x/d22ap.adt"; lh2=$(sha256sum <"$repo/research/ipsw-x/d22ap.adt" | cut -d' ' -f1); rj gate-pre
ok "ADT changed locally: shipped again and adt= follows the new bytes" "$([ "$(has 'shipping the ADT' "$out")" = 1 ] && [ "$(lastid)" = "adt=$lh2" ] && [ "$lh" != "$lh2" ] && echo 1 || echo 0)"
printf '#!/bin/bash\ncase "$*" in *"tmp/rj-identity"*) cat %s/tools/gate-identity.expected ;; *"tmp/rj-adt"*) : ;; *) exec "$@" ;; esac\n' "$repo" >"$w/bin/noadtprefix"; chmod +x "$w/bin/noadtprefix"
REMOTE_JOB_GATE_PREFIX="$w/bin/noadtprefix" rj gate-pre
ok "container wrote no adt= line => not formal (identity MISMATCH naming adt=)" "$([ "$(has 'identity MISMATCH (no single trailing adt= line' "$out")$(has 'provenance=.*formal (offload-host' "$out")" = 10 ] && echo 1 || echo 0)"
# ---- T-78bc: (a) super-repo tools (wake, triage-settle-guard) are shipped to the remote host and mounted READ-ONLY at /super-tools with the env the three unit files read; absent = loud WARN, no mount
mkdir -p "$w/sup" "$w/nosup"; printf 'WAKE-TOOL-1\n' >"$w/sup/wake"; printf 'TRIAGE-TOOL-1\n' >"$w/sup/triage-settle-guard"
: >"$w/home/docker.log"; REMOTE_JOB_SUPER_TOOLS="$w/sup" rj gate-pre
ok "super tools present: shipped to the remote super-tools dir, run line mounts /super-tools :ro and sets JOB_WAKE_TOOL, PHONE_LOCK_WAKE, AGENT_GUARD_TRIAGE_MOD" "$([ $rc = 0 ] && cmp -s "$w/sup/wake" "$w/sroot/work/super-tools/wake" && cmp -s "$w/sup/triage-settle-guard" "$w/sroot/work/super-tools/triage-settle-guard" && [ "$(grep -c -- "-v $w/sroot/work/super-tools:/super-tools:ro,Z -e JOB_WAKE_TOOL=/super-tools/wake -e PHONE_LOCK_WAKE=/super-tools/wake -e AGENT_GUARD_TRIAGE_MOD=/super-tools/triage-settle-guard" "$w/home/docker.log")" -ge 1 ] && [ "$(has 'shipped super tools' "$out")" = 1 ] && echo 1 || echo 0)"
rm -rf "$w/sroot/work/super-tools"; : >"$w/home/docker.log"; REMOTE_JOB_SUPER_TOOLS="$w/nosup" rj gate-pre
ok "super tools absent locally: loud WARN naming REMOTE_JOB_SUPER_TOOLS, nothing shipped, no /super-tools mount" "$([ "$(has 'WARN super tools wake/triage-settle-guard not found' "$out")" = 1 ] && [ ! -e "$w/sroot/work/super-tools" ] && ! grep -q 'super-tools' "$w/home/docker.log" && echo 1 || echo 0)"
# (c) stack-apply offline: the staged pristine XNU clone + pinned stager mirror are mounted READ-ONLY below ~/pixelios-artifacts (mount points made on the host first); absent = loud WARN, no mount
mkdir -p "$w/sroot/work/xnub-art/xnu-pristine" "$w/sroot/work/xnub-art/git-mirror"; : >"$w/home/docker.log"; REMOTE_JOB_SUPER_TOOLS="$w/sup" rj gate-pre   # the dirs may be staged by the xnub cases above: kept
ok "stack-apply offline: run line mounts xnu-pristine and git-mirror from the staged xnub-art READ-ONLY at ~/pixelios-artifacts, never :rw" "$([ $rc = 0 ] && [ "$(grep -c -- "-v $w/sroot/work/xnub-art/xnu-pristine:/[^ ]*/pixelios-artifacts/xnu-pristine:ro,Z" "$w/home/docker.log")" -ge 1 ] && [ "$(grep -c -- "-v $w/sroot/work/xnub-art/git-mirror:/[^ ]*/pixelios-artifacts/git-mirror:ro,Z" "$w/home/docker.log")" -ge 1 ] && ! grep -q 'pixelios-artifacts/\(xnu-pristine\|git-mirror\):rw' "$w/home/docker.log" && echo 1 || echo 0)"
rm -rf "$w/sroot/work/super-tools"; mkdir -p "$w/aside"; mv "$w/sroot/work/xnub-art/xnu-pristine" "$w/sroot/work/xnub-art/git-mirror" "$w/aside/"; : >"$w/home/docker.log"; REMOTE_JOB_SUPER_TOOLS="$w/nosup" rj gate-pre; mv "$w/aside/xnu-pristine" "$w/aside/git-mirror" "$w/sroot/work/xnub-art/"
ok "stack-apply offline, staged dirs missing: loud WARN naming the xnub staging tool, no such mount (rule 8)" "$([ "$(has 'WARN .*xnub-art/xnu-pristine missing on the remote host (run the xnub staging tool' "$out")" = 1 ] && ! grep -q 'pixelios-artifacts/xnu-pristine' "$w/home/docker.log" && echo 1 || echo 0)"
# (b) a commit that carries tools/third_party (vendored, T-93e6) gets NO link inside it (ln -s onto a directory made untracked tools/third_party/third_party = +dirty)
mkdir -p "$repo/tools/third_party"; echo x >"$repo/tools/third_party/x"; git -C "$repo" add -f tools/third_party/x; git -C "$repo" -c user.email=t@t -c user.name=t commit -q -m vendored-tp tools/third_party/x; tp_tracked=$(git -C "$repo" ls-files tools/third_party/x)
rj selftest-refs; l=$repo/logs/remote-jobs/selftest-refs/job.log
ok "vendored tools/third_party: the remote clone keeps the tracked dir (x), no nested third_party link, no readlink output" "$([ "$tp_tracked" = tools/third_party/x ] && [ $rc = 0 ] && grep -qx 'x' "$l" && ! grep -qx 'third_party' "$l" && [ "$(has 'gate-k/tools/third_party' "$(cat "$l")")" = 0 ] && echo 1 || echo 0)"
[ "$tp_tracked" != tools/third_party/x ] || git -C "$repo" reset -q --hard HEAD~1; rm -rf "$repo/tools/third_party"
# mutants of tools/remote-job, one per new comparison: run in a copy, each must lose its assertion
adtmut() { # NAME SED-EXPR ; sets $out of the mutated tool's gate-pre run (second run: offload-host already holds the current ADT)
	rm -rf "$w/repoMu"; cp -r "$repo" "$w/repoMu"; rm -rf "$w/repoMu/logs"; sed -i "$2" "$w/repoMu/tools/remote-job"
	git -C "$w/repoMu" -c user.email=t@t -c user.name=t commit -q -am mut; cmp -s "$repo/tools/remote-job" "$w/repoMu/tools/remote-job" && echo "FAIL mutant $1 did not change the tool" && bad=$((bad+1))
	: >"$w/home/docker.log"; out=$(cd "$w/repoMu" && tools/remote-job gate-pre 2>&1); rc=$?
}
adtmut ro 's#d22ap.adt:ro,Z#d22ap.adt:rw,Z#'
ok "mutation: ADT mount made rw -> the ro assertion fails (killed)" "$(grep -q 'd22ap.adt:ro,Z ' "$w/home/docker.log" && echo 0 || echo 1)"
adtmut differs 's#\[ "\$rh" != "\$lh" \]#true#'
ok "mutation: copy-only-when-different disabled -> ships again (killed)" "$([ "$(has 'shipping the ADT' "$out")" = 1 ] && echo 1 || echo 0)"
rm -rf "$w/repoMu"; cp -r "$repo" "$w/repoMu"; rm -rf "$w/repoMu/logs"; sed -i 's#\[ "\$(grep -c .\^adt=. "\$idf")" != 1 \]#false#; s#\[ "\$(tail -n 1 "\$idf" | grep -c .\^adt=.)" != 1 \]#false#' "$w/repoMu/tools/remote-job"; git -C "$w/repoMu" -c user.email=t@t -c user.name=t commit -q -am mut
out=$(cd "$w/repoMu" && REMOTE_JOB_GATE_PREFIX="$w/bin/noadtprefix" tools/remote-job gate-pre 2>&1)
ok "mutation: adt= line check disabled + container without adt= -> formal (killed by the case above)" "$([ "$(has 'provenance=.*formal (offload-host' "$out")" = 1 ] && echo 1 || echo 0)"
adtmut filter "s#<(grep -v '^adt=' \"\$idf\")#\"\$idf\"#"
ok "mutation: pin compare without the adt= filter -> no longer formal (killed)" "$([ "$(has 'formal (offload-host' "$out")" = 0 ] && echo 1 || echo 0)"
rm -rf "$repo/research"
adtmut always 's#\[ -s "\$adt_f" \]; then adt_on=1#true; then adt_on=1#'
ok "mutation: ADT mount/ship forced on without a local ADT -> the no-mount assertion fails (killed)" "$([ $rc = 0 ] && ! grep -q 'd22ap.adt' "$w/home/docker.log" && echo 0 || echo 1)"
rm -rf "$w/repoMu"
# ---- golden traces: shipped to offload-host:$w/sroot/work/golden/TS (after the local SHA256SUMS check) and mounted READ-ONLY; absent = loud REMOTE line, no mount
gts=20261029T010203Z; lhome=$w/lhome; gdir=$lhome/pixelios-artifacts/golden/$gts
printf 'GOLDEN = os.path.expanduser("~/pixelios-artifacts/golden/%s")\n' "$gts" >"$repo/tools/test_golden_gpu_analyze.py"; $G add tools/test_golden_gpu_analyze.py; $G commit -qm golden-ts
mkdir -p "$lhome"; : >"$w/home/docker.log"; HOME=$lhome rj gate-pre
ok "golden absent locally: loud REMOTE line, no golden mount, nothing shipped (rule 8)" "$([ "$(has "REMOTE: golden traces absent locally ($gdir): test_golden_gpu_analyze RealGoldenChecks will skip and check-all.sh will not register the PASS" "$out")" = 1 ] && ! grep -q 'pixelios-artifacts/golden' "$w/home/docker.log" && [ ! -e "$w/sroot/work/golden" ] && echo 1 || echo 0)"
mkdir -p "$gdir"; printf 'GPU-TRACE' >"$gdir/trace-gpu.txt"; printf 'DPU-TRACE' >"$gdir/trace-dpu.txt"
printf '%s  trace-gpu.txt\n%s  trace-dpu.txt\n' "$(sha256sum <"$gdir/trace-gpu.txt" | cut -d' ' -f1)" "$(sha256sum <"$gdir/trace-dpu.txt" | cut -d' ' -f1)" >"$gdir/SHA256SUMS"
: >"$w/home/docker.log"; HOME=$lhome rj gate-pre
ok "golden present: run line mounts the dir READ-ONLY at /home/user/pixelios-artifacts/golden/TS, both traces shipped" "$([ $rc = 0 ] && [ "$(grep -c -- "-v $w/sroot/work/golden/$gts:/home/user/pixelios-artifacts/golden/$gts:ro,Z " "$w/home/docker.log")" -ge 1 ] && ! grep -q 'golden/[0-9TZ]*:rw' "$w/home/docker.log" && [ "$(cat "$w/sroot/work/golden/$gts/trace-gpu.txt")" = GPU-TRACE ] && [ "$(cat "$w/sroot/work/golden/$gts/trace-dpu.txt")" = DPU-TRACE ] && [ "$(has 'shipping golden trace-gpu.txt' "$out")" = 1 ] && echo 1 || echo 0)"
HOME=$lhome rj gate-pre
ok "golden present: the nested mount point <job>/tmp/art/golden/TS exists on the host BEFORE docker run (podman-made mount points are sub-uid dirs the host cannot remove)" "$([ "$(grep -c GOLDEN-MOUNTPOINT-MISSING "$w/home/docker.log")" = 0 ] && [ "$(grep -c -- ":/home/user/pixelios-artifacts/golden/$gts:ro,Z" "$w/home/docker.log")" -ge 1 ] && echo 1 || echo 0)"
ok "golden unchanged on offload-host: not copied again" "$([ $rc = 0 ] && [ "$(has 'shipping golden' "$out")" = 0 ] && echo 1 || echo 0)"
printf 'TAMPERED' >"$w/sroot/work/golden/$gts/trace-dpu.txt"; : >"$w/home/docker.log"; HOME=$lhome rj gate-pre
ok "golden changed on offload-host: shipped again (remote sha256 differs)" "$([ $rc = 0 ] && [ "$(has 'shipping golden trace-dpu.txt' "$out")" = 1 ] && [ "$(cat "$w/sroot/work/golden/$gts/trace-dpu.txt")" = DPU-TRACE ] && echo 1 || echo 0)"
printf 'EDITED' >"$gdir/trace-gpu.txt"; : >"$w/home/docker.log"; HOME=$lhome rj gate-pre
ok "golden SHA256SUMS mismatch: job refused exit 2 with a REMOTE: line naming the file and both hashes, no docker run" "$([ $rc = 2 ] && [ "$(has "REMOTE: golden $gdir/trace-gpu.txt sha256 $(sha256sum <"$gdir/trace-gpu.txt" | cut -d' ' -f1) does not match" "$out")" = 1 ] && [ "$(has "$(head -c 64 "$gdir/SHA256SUMS")" "$out")" = 1 ] && [ ! -s "$w/home/docker.log" ] && echo 1 || echo 0)"
rm -f "$gdir/SHA256SUMS"; HOME=$lhome rj gate-pre
ok "golden without a SHA256SUMS line: refused exit 2 (no line)" "$([ $rc = 2 ] && [ "$(has 'no line' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$gdir/trace-gpu.txt"; printf 'GPU-TRACE' >"$gdir/trace-gpu.txt"; printf '%s  trace-gpu.txt\n%s  trace-dpu.txt\n' "$(sha256sum <"$gdir/trace-gpu.txt" | cut -d' ' -f1)" "$(sha256sum <"$gdir/trace-dpu.txt" | cut -d' ' -f1)" >"$gdir/SHA256SUMS"
# F1: offload-side check (exit 96) after the ship: trace missing / altered / symlink => remote-job exit 2, REMOTE line, no docker run
for tm in missing alter symlink; do
	HOME=$lhome rj gate-pre   # heal offload-host (re-ship) first
	: >"$w/home/docker.log"; FAKE_GOLDEN_TAMPER=$tm HOME=$lhome rj gate-pre
	ok "golden offload-side check: remote trace $tm => job rc 96 (exit 1, RESULT: FAIL, no fallback), loud 'gate-pre: golden' line, no docker run" "$([ $rc = 1 ] && [ "$(has 'gate-pre: golden .*trace-dpu.txt' "$out")" = 1 ] && [ "$(has 'job rc 96' "$out")" = 1 ] && ! grep -q 'pixelios-artifacts/golden' "$w/home/docker.log" && echo 1 || echo 0)"
done
HOME=$lhome rj gate-pre
rm -rf "$w/repoMu"; cp -r "$repo" "$w/repoMu"; rm -rf "$w/repoMu/logs"; sed -i 's#exit 96; }#true; }#' "$w/repoMu/tools/remote-job"; git -C "$w/repoMu" -c user.email=t@t -c user.name=t commit -q -am mut
: >"$w/home/docker.log"; out=$(cd "$w/repoMu" && FAKE_GOLDEN_TAMPER=alter HOME=$lhome tools/remote-job gate-pre 2>&1); rc=$?
ok "mutation: golden offload-side exit 96 replaced by true -> the altered-trace case no longer refuses (killed)" "$([ $rc = 1 ] && [ "$(has 'job rc 96' "$out")" = 1 ] && echo 0 || echo 1)"
rm -rf "$w/repoMu"
# F3: a symlink staged by scp must never be moved into place
HOME=$lhome rj gate-pre; printf 'ALTERED' >"$w/sroot/work/golden/$gts/trace-gpu.txt"
FAKE_SCP_MODE=golden-symlink HOME=$lhome rj gate-pre
ok "golden ship: a symlink at the staged .new file is refused (exit 2), the trace is not replaced" "$([ $rc = 2 ] && [ "$(cat "$w/sroot/work/golden/$gts/trace-gpu.txt")" = ALTERED ] && [ ! -L "$w/sroot/work/golden/$gts/trace-gpu.txt" ] && echo 1 || echo 0)"
rm -rf "$w/sroot/work/golden/$gts"; HOME=$lhome rj gate-pre
# F2: an invalid TS in the commit under test => loud line, no ship, no mount
for bad_ts in '../x' 2026; do
	printf 'GOLDEN = os.path.expanduser("~/pixelios-artifacts/golden/%s")\n' "$bad_ts" >"$repo/tools/test_golden_gpu_analyze.py"; $G commit -qam "ts $bad_ts"
	rm -rf "$w/sroot/work/golden"; : >"$w/home/docker.log"; HOME=$lhome rj gate-pre
	ok "golden invalid TS '$bad_ts': loud 'invalid/unparsed' line, no ship, no mount" "$([ "$(has 'REMOTE: golden TS invalid or unparsed' "$out")" = 1 ] && [ "$(has 'absent locally' "$out")" = 0 ] && ! grep -q 'pixelios-artifacts/golden' "$w/home/docker.log" && [ ! -e "$w/sroot/work/golden" ] && echo 1 || echo 0)"
done
# verdicts.json is optional only when the commit has no GATE_MUT_VERDICTS support
for v in B C D E F G H; do cp -r "$repo" "$w/repo$v"; done
mk() { printf '#!/bin/bash\nmkdir -p shim/out; echo stub >shim/out/gate.log\n%s\necho "GATE PASS stub%s"\n' "$2" "$1" >"$w/repo$1/tools/check-all.sh"; git -C "$w/repo$1" -c user.email=t@t -c user.name=t commit -q -m "gate $1" tools/check-all.sh; }
mk B 'echo no verdict support'; mk C '# GATE_MUT_VERDICTS is supported but this stub forgets to write it'
mk D 'echo x'; # repoD: absolute adb path in a script (harmless, must now PASS); E: fake adb in the fake /usr/bin; F: heimdall there; G: fake /dev/bus/usb; H: fake ttyUSB0
mkdir -p "$w/repoD/tools"; echo '/usr/bin/adb devices' >"$w/repoD/tools/sneaky.sh"; git -C "$w/repoD" add tools/sneaky.sh; git -C "$w/repoD" -c user.email=t@t -c user.name=t commit -qm sneaky
plant() { mk "$1" 'echo x'; mkdir -p "$(dirname "$w/repo$1/tools/rj-fakeroot/$2")"; : >"$w/repo$1/tools/rj-fakeroot/$2"; git -C "$w/repo$1" add -f tools/rj-fakeroot; git -C "$w/repo$1" -c user.email=t@t -c user.name=t commit -qm plant; }
plant E usr/bin/adb; plant F usr/bin/heimdall; plant G dev/bus/usb/001; plant H dev/ttyUSB0
cp -r "$repo" "$w/repoI"; mk I 'echo "PORC0=[$(git status --porcelain | grep -v -e shim/ -e tools/third_party)]"'
out=$(cd "$w/repoI" && tools/remote-job gate-pre 2>&1); rc=$?; jl=$(cat "$w/repoI/logs/remote-jobs/gate-pre/job.log")
ok "gate-pre: job scaffolding (stubs/ tmp/ rj-out/ ...) is excluded: git status --porcelain empty when the gate starts" "$([ "$(has '^PORC0=\[\]$' "$jl")" = 1 ] && echo 1 || echo 0)"
ok "gate-pre: tree status after gate is printed (rule 8)" "$([ "$(has '^gate-pre: tree status after gate: ' "$jl")" = 1 ] && echo 1 || echo 0)"
out=$(cd "$w/repoB" && tools/remote-job gate-pre 2>&1); rc=$?
ok "verdicts absent + commit without support: PASS and 'verdicts: ABSENT' in RESULT" "$([ $rc = 0 ] && has 'verdicts: ABSENT (commit lacks GATE_MUT_VERDICTS support)' "$out" || echo 0)"
out=$(cd "$w/repoC" && tools/remote-job gate-pre 2>&1); rc=$?
ok "verdicts absent + commit WITH support: FAIL loudly" "$([ $rc = 1 ] && [ "$(has 'verdicts.json(supported by this commit but not written)' "$out")" = 1 ] && echo 1 || echo 0)"
out=$(cd "$w/repoD" && tools/remote-job gate-pre 2>&1); rc=$?
ok "gate script naming adb by absolute path: no repo scan any more, gate-pre PASSES (container check replaced it)" "$([ $rc = 0 ] && [ "$(has 'container check ok' "$(cat "$w/repoD/logs/remote-jobs/gate-pre/job.log")")" = 1 ] && echo 1 || echo 0)"
for m in "E:adb exists in the container" "F:heimdall exists" "G:dev/bus/usb is mapped" "H:ttyUSB device mapped"; do
	out=$(cd "$w/repo${m%%:*}" && tools/remote-job gate-pre 2>&1); rc=$?
	ok "container check mutant ${m%%:*} (${m#*:}): exit 2, REMOTE: line, gate not started" "$([ $rc = 2 ] && [ "$(has "^REMOTE: gate-pre REFUSED before the gate: .*${m#*:}" "$out")$(has 'GATE PASS' "$out")" = 10 ] && [ "$(has 'PATH0=' "$(cat "$w/repo${m%%:*}/logs/remote-jobs/gate-pre/job.log")")" = 0 ] && echo 1 || echo 0)"
done
printf '#!/bin/bash\nrm -rf stubs; exec "$@"\n' >"$w/bin/rmstubs"; chmod +x "$w/bin/rmstubs"
REMOTE_JOB_GATE_PREFIX="$w/bin/rmstubs" rj gate-pre; ok "container check: stub dir missing => exit 2, REMOTE: line" "$([ $rc = 2 ] && [ "$(has '^REMOTE: gate-pre REFUSED before the gate: stub dir .* missing' "$out")" = 1 ] && echo 1 || echo 0)"
REMOTE_JOB_GATE_PREFIX='env --device=/dev/bus/usb' rj gate-pre; ok "run line with --device => exit 2, REMOTE: line, nothing started" "$([ $rc = 2 ] && [ "$(has '^REMOTE: gate-pre REFUSED, container run line maps host devices' "$out")" = 1 ] && echo 1 || echo 0)"
REMOTE_JOB_GATE_PREFIX='env --privileged' rj gate-pre; ok "run line with --privileged => exit 2, REMOTE: line" "$([ $rc = 2 ] && [ "$(has 'maps host devices (--privileged)' "$out")" = 1 ] && echo 1 || echo 0)"
REMOTE_JOB_GATE_PREFIX='adb x' rj gate-pre; ok "gate-pre prefix with phone word refused" "$([ $rc = 2 ] && echo 1 || echo 0)"
rj unit; ok "unit: offload-host only, shards=3, out under job dir" "$([ $rc = 0 ] && [ "$(has 'where=offload-host' "$out")$(has 'shards=3 out=.*/jobs/unit\.[0-9.]*/out/unit-shards' "$(cat "$repo/logs/remote-jobs/unit/job.log")")" = 11 ] && echo 1 || echo 0)"
rj --where local unit; ok "unit --where local refused" "$([ $rc = 2 ] && [ "$(has '^REMOTE:' "$out")" = 1 ] && echo 1 || echo 0)"
# per-job lock: a second run of the same job from another checkout while the first runs must refuse, and must not delete the first run's dir
cp -r "$repo" "$w/repo2"
( cd "$repo" && tools/remote-job --where offload-host selftest-slow >"$w/slow1.out" 2>&1; echo $? >"$w/slow1.rc" ) & sp=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do compgen -G "$w/sroot/work/jobs/selftest-slow.*" >/dev/null && break; sleep 0.3; done
out=$(cd "$w/repo2" && tools/remote-job --where offload-host selftest-slow 2>&1); rc=$?
wait $sp
ok "per-job lock: second concurrent run refused (REMOTE already running), first one passes" "$([ $rc = 2 ] && [ "$(has 'already running on offload-host' "$out")" = 1 ] && [ "$(cat "$w/slow1.rc")" = 0 ] && echo 1 || echo 0)"
# ci-local: GPU repo commit, shipped as bundle
gpu=$w/gpu; git init -q "$gpu"; GG="git -C $gpu -c user.email=t@t -c user.name=t"; echo 1 >"$gpu/f"; $GG add f; $GG commit -qm one
git clone -q "$gpu" "$w/sroot/work/ci-local/gpuci" 2>/dev/null || { mkdir -p "$w/sroot/work/ci-local"; git clone -q "$gpu" "$w/sroot/work/ci-local/gpuci"; }
echo 2 >"$gpu/f"; $GG commit -qam two; gs=$($GG rev-parse HEAD)
mkdir -p "$w/sroot/work/ci-local/tools"; echo 'export CIENV=1' >"$w/sroot/work/ci-local/env.sh"
cat >"$w/sroot/work/ci-local/tools/ci-local" <<'C'
#!/bin/bash
h=$(git -C "${@: -1}" rev-parse HEAD); d=artifacts/runs/${h:0:12}-all-$$$RANDOM; mkdir -p "$d"; echo "args=$* env=$CIENV head=$h nice=$(nice)" >"$d/result"
C
chmod +x "$w/sroot/work/ci-local/tools/ci-local"
export REMOTE_JOB_GPU_REPO=$gpu
rj --commit "$gs" ci-local; cl=$repo/logs/remote-jobs/ci-local
ok "ci-local: gpuci updated to the commit, command args/env/nice as tested, artifacts fetched, label says ci-local not pre-validation" "$([ $rc = 0 ] && [ "$(has "head=$gs" "$(cat "$cl"/out/*/result 2>/dev/null)")$(has -- 'args=--stage all --cpus 4 --timeout 7200 .*/ci-local/gpuci env=1 .*nice=19' "$(cat "$cl"/out/*/result 2>/dev/null)")$(has "gpu=${gs:0:12}" "$out")$(has "provenance=offload-host gcc[0-9]* ci-local (GPU-repo CI on offload-host, not a kernel gate)" "$out")" = 1111 ] && echo 1 || echo 0)"
rj --where local --commit "$gs" ci-local; ok "ci-local --where local refused" "$([ $rc = 2 ] && [ "$(has '^REMOTE:' "$out")" = 1 ] && echo 1 || echo 0)"
rj ci-local; ok "ci-local without --commit refused" "$([ $rc = 2 ] && [ "$(has 'needs --commit' "$out")" = 1 ] && echo 1 || echo 0)"
rj --commit "$gs" selftest; ok "--commit on another job refused" "$([ $rc = 2 ] && [ "$(has 'only for ci-local' "$out")" = 1 ] && echo 1 || echo 0)"
rj --commit deadbeef ci-local; ok "ci-local unknown commit refused, no ssh" "$([ $rc = 2 ] && [ ! -s "$FAKE_LOG" ] && echo 1 || echo 0)"


# ---- review r2: lock refusal is not infra; ci-local freshness / failure / dirty clone; kill of both process groups; trap
( cd "$repo" && tools/remote-job --where offload-host selftest-slow >"$w/slow1.out" 2>&1 ) & sp=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do compgen -G "$w/sroot/work/jobs/selftest-slow.*" >/dev/null && break; sleep 0.3; done
out=$(cd "$w/repo2" && tools/remote-job selftest-slow 2>&1); rc=$?; wait $sp
ok "lock refusal with --where auto: exit 2, no local fallback" "$([ $rc = 2 ] && [ "$(has 'already running on offload-host' "$out")$(has 'where=local' "$out")" = 10 ] && echo 1 || echo 0)"
gh=${gs:0:12}; mkdir -p "$w/sroot/work/ci-local/artifacts/runs/$gh-old"; echo old >"$w/sroot/work/ci-local/artifacts/runs/$gh-old/result"; touch -d '2 hours ago' "$w/sroot/work/ci-local/artifacts/runs/$gh-old"
echo dirt >"$w/sroot/work/ci-local/gpuci/f"; echo junk >"$w/sroot/work/ci-local/gpuci/untracked"
rj --commit "$gs" ci-local
ok "ci-local: stale earlier run not copied, dirty persistent clone reset loudly" "$([ $rc = 0 ] && [ ! -e "$cl/out/$gh-old" ] && [ -n "$(compgen -G "$cl/out/$gh-all-*")" ] && [ "$(has 'REMOTE: gpuci clone was dirty' "$out")" = 1 ] && [ ! -e "$w/sroot/work/ci-local/gpuci/untracked" ] && echo 1 || echo 0)"
touch "$w/home/cifail"; echo '[ ! -e "$HOME/cifail" ] || exit 3' >>"$w/sroot/work/ci-local/tools/ci-local"
rj --commit "$gs" ci-local
ok "ci-local failure: exit 1, failure artifacts still copied back" "$([ $rc = 1 ] && [ -n "$(compgen -G "$cl/out/$gh-all-*/result")" ] && echo 1 || echo 0)"
rm -f "$w/home/cifail"
mkdir -p "$w/tmp"; ( cd "$repo" && exec env TMPDIR="$w/tmp" tools/remote-job --where offload-host selftest-grp >"$w/grp.out" 2>&1 ) & tp=$!
for _ in $(seq 1 30); do compgen -G "$w/sroot/work/jobs/selftest-grp.*/jpg" >/dev/null && break; sleep 0.2; done; sleep 0.5
tjg=$(cat "$w"/sroot/work/jobs/selftest-grp.*/jpg 2>/dev/null | head -n 1); kill -0 -- "-${tjg:-0}" 2>/dev/null && alive_before=1 || alive_before=0   # by pgid: a pgrep -f also matched any shell whose command line held the text (2026-11-11 false FAILs)
kill -TERM $tp; wait $tp; trc=$?
ok "TERM: remote-job exits 2 with an interrupted RESULT" "$([ $trc = 2 ] && [ "$(has 'RESULT: ERROR remote-job.*interrupted' "$(cat "$w/grp.out")")" = 1 ] && echo 1 || echo 0)"
ok "TERM: the job process group (sleep 3123, child of timeout's own group) is dead, remote dir removed" "$([ "${alive_before:-0}" -ge 1 ] && ! kill -0 -- "-${tjg:-0}" 2>/dev/null && [ -z "$(compgen -G "$w/sroot/work/jobs/selftest-grp.*")" ] && echo 1 || echo 0)"
( cd "$repo" && exec env TMPDIR="$w/tmp" FAKE_SCP_MODE=slow tools/remote-job --where offload-host selftest >"$w/b.out" 2>&1 ) & tp=$!
for _ in $(seq 1 30); do compgen -G "$w/tmp/remote-job.*.bundle" >/dev/null && break; sleep 0.2; done
had=$(compgen -G "$w/tmp/remote-job.*.bundle" | wc -l); kill -TERM $tp; wait $tp; trc=$?
ok "TERM during bundle upload: exit 2, bundle removed, remote dir removed" "$([ $trc = 2 ] && [ "$had" = 1 ] && [ -z "$(compgen -G "$w/tmp/remote-job.*.bundle")" ] && [ -z "$(compgen -G "$w/sroot/work/jobs/selftest.*")" ] && echo 1 || echo 0)"
# SIGPIPE (the tee'd stdout is gone: heavy-job aborted, its scope stopped) runs the same cleanup as TERM; stdout and stderr are both dead pipes, so on_signal's own output must not end the cleanup
rm -f "$w/pp.pid"; { cd "$repo" && echo $BASHPID >"$w/pp.pid" && exec env TMPDIR="$w/tmp" tools/remote-job --where offload-host selftest-grp; } 2>&1 | { exit 0; } &
for _ in $(seq 1 50); do compgen -G "$w/sroot/work/jobs/selftest-grp.*/jpg" >/dev/null && break; sleep 0.2; done; sleep 0.5
pp=$(cat "$w/pp.pid"); jg=$(cat "$w"/sroot/work/jobs/selftest-grp.*/jpg 2>/dev/null | head -n 1); kill -0 -- "-${jg:-0}" 2>/dev/null && alive_before=1 || alive_before=0   # the job group by pgid (a pgrep -f would also match a wrapper whose command line holds the text)
kill -PIPE "$pp"; for _ in $(seq 1 50); do kill -0 "$pp" 2>/dev/null || break; sleep 0.2; done
ok "PIPE with dead stdout+stderr pipes: remote-job ran its cleanup (job group ${jg:-?} was alive: $alive_before, now dead; remote dir removed; remote-job gone)" "$([ "$alive_before" = 1 ] && ! kill -0 "$pp" 2>/dev/null && ! kill -0 -- "-${jg:-0}" 2>/dev/null && [ -z "$(compgen -G "$w/sroot/work/jobs/selftest-grp.*")" ] && echo 1 || echo 0)"
ok "real tools/gate-identity prints its keys; armtc is a 64-hex measurement, MISSING (loud) when the toolchain is absent, and differs when a binary differs" "$(o=$(tools/gate-identity); m=$(ARM_TC=$w/nonexistent tools/gate-identity); mkdir -p "$w/tc/bin"; echo a >"$w/tc/bin/aarch64-none-elf-gcc"; chmod +x "$w/tc/bin/aarch64-none-elf-gcc"; a=$(ARM_TC=$w/tc tools/gate-identity | grep ^armtc=); echo b >"$w/tc/bin/other"; b=$(ARM_TC=$w/tc tools/gate-identity | grep ^armtc=); [ "$(has '^image=' "$o")$(has '^verlist=' "$o")$(has '^armtc=[0-9a-f]\{64\}$' "$o")$(has '^armtc=MISSING$' "$m")$(has '^third_party=' "$o")" = 11111 ] && [ -n "$a" ] && [ "$a" != "$b" ] && echo 1 || echo 0)"
exp_ok() {   # structure of an expected file: exactly the 8 keys in order, no TODO/UNMEASURED/MISSING, hex64 hashes, non-empty text values
	local f=$1 k; [ "$(cut -d= -f1 "$f" | tr '\n' ' ')" = "image verlist armtc gcc ld glibc py third_party " ] || return 1
	! grep -qE 'TODO|UNMEASURED|MISSING' "$f" || return 1
	for k in image verlist armtc gcc ld third_party; do grep -qE "^$k=[0-9a-f]{64}$" "$f" || return 1; done
	grep -qE '^glibc=glibc [0-9.]+$' "$f" && grep -qE '^py=Python 3\.' "$f"
}
ok "committed gate-identity.expected is fully measured (structure, no TODO; grep -c TODO = 0)" "$(exp_ok tools/gate-identity.expected && [ "$(grep -c TODO tools/gate-identity.expected)" = 0 ] && echo 1 || echo 0)"
cp tools/gate-identity.expected "$w/e1"; sed -i 's/^ld=.*/ld=TODO/' "$w/e1"; cp tools/gate-identity.expected "$w/e2"; sed -i 's/^armtc=.*/armtc=UNMEASURED/' "$w/e2"; grep -v '^py=' tools/gate-identity.expected >"$w/e3"; sed 's/^gcc=.*/gcc=abc/' tools/gate-identity.expected >"$w/e4"
ok "expected-file structure check rejects TODO, UNMEASURED, a missing key and a short hash" "$(r=1; for e in e1 e2 e3 e4; do ! exp_ok "$w/$e" || r=0; done; echo $r)"
if [ -d "$HOME/.local/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf" ]; then
ok "lab-host ARM toolchain measures equal to the committed armtc" "$([ "$(tools/gate-identity | grep ^armtc=)" = "$(grep ^armtc= tools/gate-identity.expected)" ] && echo 1 || echo 0)"; fi

# ---- AP-194: --collect only collects and exits, it never submits (the last collect used to fall through and run the job)
cj=$w/sroot/work/jobs
for d in selftest.100.200 selftest.300.400; do mkdir -p "$cj/$d/out"; echo 0 >"$cj/$d/job.rc"; echo "log $d" >"$cj/$d/job.log"; done
rm -rf "$repo/logs/remote-jobs/selftest"
rj --where offload-host --collect selftest.100.200 selftest
ok "collect (AP-194): one dir collected with others left: exit 0, RESULT: PASS ... (no job submitted), nothing submitted" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS collected selftest.100.200 -> .* (no job submitted)$' "$out")$(has 'where=offload-host' "$out")$(has 'provenance=' "$out")" = 100 ] && [ -e "$repo/logs/remote-jobs/selftest/selftest.100.200/job.log" ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && [ ! -e "$cj/selftest.100.200" ] && [ -d "$cj/selftest.300.400" ] && echo 1 || echo 0)"
rj --where offload-host --collect selftest.300.400 selftest
ok "collect (AP-194): the LAST dir collected: exit 0, no job submitted (this call used to run the job)" "$([ $rc = 0 ] && [ "$(has '^RESULT: PASS collected selftest.300.400 .*(no job submitted)$' "$out")$(has 'where=offload-host' "$out")" = 10 ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && [ -z "$(compgen -G "$cj/selftest.*")" ] && echo 1 || echo 0)"
rm -rf "$repo/logs/remote-jobs/selftest"
rj --where local --collect "" selftest
ok "collect (AP-194 r2 F1): --collect \"\" (empty DIR): exit 2, RESULT: ERROR needs a DIR, nothing run" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR .*--collect needs a DIR' "$out")" = 1 ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && echo 1 || echo 0)"
rm -rf "$repo/logs/remote-jobs/selftest"; mkdir -p "$cj/selftest.500.600"; echo 0 >"$cj/selftest.500.600/job.rc"
rj --where local --collect selftest.500.600 selftest
ok "collect (AP-194 F1): --where local --collect: exit 2, RESULT: ERROR needs offload-host, nothing run, remote dir kept" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR .*--collect needs offload-host' "$out")$(has 'where=' "$out")" = 10 ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && [ -d "$cj/selftest.500.600" ] && echo 1 || echo 0)"
FAKE_SSH_MODE=down rj --collect selftest.500.600 selftest
ok "collect (AP-194 F1): auto with offload-host down: exit 2, 'cannot collect, nothing submitted', no local run" "$([ $rc = 2 ] && [ "$(has 'offload-host unreachable: cannot collect, nothing submitted' "$out")$(has 'running local' "$out")" = 10 ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && echo 1 || echo 0)"
rj --collect selftest.500.600 selftest
ok "collect (AP-194 F1): auto with offload-host up collects only" "$([ $rc = 0 ] && [ "$(has '(no job submitted)$' "$out")" = 1 ] && [ ! -e "$repo/logs/remote-jobs/selftest/job.log" ] && echo 1 || echo 0)"

# ---- TP-042 OFFLOAD-HOST QUEUE: remote-job re-execs itself under `heavy-job offload-host run` (fake heavy-job records its argv; nothing else runs)
qr=$w/qr; mkdir -p "$qr/tools"; cp tools/remote-job tools/xnub-line.sh tools/remote-job-inputs.sh tools/offload-conf.sh tools/offload_conf.py "$qr/tools/"
printf '#!/bin/bash\necho "HJ-ARGV: $*"; echo "HJ-QUIET=${HEAVY_JOB_QUIET:-}"; echo "HJ-FBL=${HEAVY_JOB_FALLBACK_LOCAL:-}"\n' >"$qr/tools/heavy-job"; chmod +x "$qr/tools/heavy-job" "$qr/tools/remote-job"
qj() { out=$(cd "$w" && env -u OFFLOAD_QUEUE_HELD REMOTE_JOB_OWNER=tester "$@" 2>&1); rc=$?; }
qj "$qr/tools/remote-job" --where offload-host selftest
ok "queue: --where offload-host re-execs under heavy-job offload-host run --owner --label rj-JOB -- remote-job <original args>" "$([ "$(has "HJ-ARGV: offload-host run --owner tester --label rj-selftest --use test -- $qr/tools/remote-job --where offload-host selftest\$" "$out")$(has 'HJ-QUIET=1' "$out")" = 11 ] && echo 1 || echo 0)"
ok "queue: a pinned --where never sets the fallback env (a pinned run never falls back)" "$(has 'HJ-FBL=$' "$out")"
qj "$qr/tools/remote-job" selftest
ok "queue: --where auto (offload-host up) queues too" "$(has 'HJ-ARGV: offload-host run --owner tester --label rj-selftest --use test -- .* selftest$' "$out")"
ok "queue: --where auto passes the lost-mid-handoff fallback to the slot by env (T-4ecb)" "$(has 'HJ-FBL=1' "$out")"
qj "$qr/tools/remote-job" --where local selftest
ok "queue: --where local never queues" "$([ "$(has 'HJ-ARGV' "$out")" = 0 ] && echo 1 || echo 0)"
qj env FAKE_SSH_MODE=down "$qr/tools/remote-job" selftest
ok "queue: --where auto with offload-host down does not queue (falls back to local as before)" "$([ "$(has 'HJ-ARGV' "$out")" = 0 ] && echo 1 || echo 0)"
out=$(cd "$w" && OFFLOAD_QUEUE_HELD=outer REMOTE_JOB_OWNER=tester "$qr/tools/remote-job" --where local selftest 2>&1)
ok "queue: OFFLOAD_QUEUE_HELD set: no second queue" "$([ "$(has 'HJ-ARGV' "$out")" = 0 ] && echo 1 || echo 0)"
for fl in "--collect x.1.2" "--kill-orphan"; do
	# shellcheck disable=SC2086 # fl is a word list on purpose
	qj "$qr/tools/remote-job" --where offload-host $fl selftest
	ok "queue (r2 F1): remote-job $fl never queues (it takes no CPU; the orphan it kills is what makes admission refuse)" "$([ "$(has 'HJ-ARGV' "$out")" = 0 ] && echo 1 || echo 0)"
done
# r2 F2: first probe fails, second would succeed: the first decision is final, nothing runs on offload-host unqueued
: >"$FAKE_LOG"; rm -f "$FAKE_LOG.once"
out=$(cd "$repo" && env -u OFFLOAD_QUEUE_HELD FAKE_SSH_MODE=once REMOTE_JOB_REPO="$repo" REMOTE_JOB_OWNER=tester "$qr/tools/remote-job" selftest 2>&1); rc=$?
ok "queue (r2 F2): --where auto, queue-time probe fails, later probe would pass: runs local, never on offload-host unqueued" "$([ $rc = 0 ] && ! grep -q REMOTE-RAN "$FAKE_LOG" && [ "$(has 'running local (decided before the queue)' "$out")" = 1 ] && [ "$(has 'HJ-ARGV' "$out")" = 0 ] && echo 1 || echo 0)"
# T-afd9 (R4): --wake-hook with the queue re-exec: the OUTERMOST remote-job wakes once with heavy-job's rc, even when heavy-job refuses admission (an exec would drop the trap); the hook is stripped from the re-exec'd argv
printf '#!/bin/bash\necho "FAKE-WAKE $*" >>"%s/wakelog"\n' "$w" >"$qr/tools/job-wake"; chmod +x "$qr/tools/job-wake"
printf '#!/bin/bash\necho "HJ-ARGV: $*"; exit "${HJ_RC:-0}"\n' >"$qr/tools/heavy-job"
rm -f "$w/wakelog"; qj env HJ_RC=2 "$qr/tools/remote-job" --where auto --wake-hook h-ok selftest
ok "queue (T-afd9): heavy-job refuses admission (rc 2): one wake 'h-ok selftest 2', exit 2 kept, --wake-hook not passed on" "$([ $rc = 2 ] && [ "$(cat "$w/wakelog" 2>/dev/null)" = "FAKE-WAKE h-ok selftest 2" ] && [ "$(has 'HJ-ARGV: .*wake-hook' "$out")" = 0 ] && [ "$(has 'HJ-ARGV: .* run .* -- .*/remote-job --where auto selftest$' "$out")" = 1 ] && echo 1 || echo 0)"
rm -f "$w/wakelog"; qj env HJ_RC=0 "$qr/tools/remote-job" --wake-hook h-ok --where auto selftest
ok "queue (T-afd9): re-exec ends rc 0: exactly one wake 'h-ok selftest 0' (only the outermost process wakes)" "$([ $rc = 0 ] && [ "$(cat "$w/wakelog" 2>/dev/null)" = "FAKE-WAKE h-ok selftest 0" ] && echo 1 || echo 0)"
# W35 R22: the offload-host route has NO local XNU pre-step (it builds in the slot), so a broken stack no longer fails before the queue; the queue is taken and the local build is never started
printf '#!/bin/bash\necho x >>"%s/xnu-count"; echo "RESULT: PASS xnu-build"\n' "$w" >"$w/bin/fake-xnu2"; chmod +x "$w/bin/fake-xnu2"
cp tools/gate-identity "$qr/tools/gate-identity"
printf '#!/bin/bash\necho "HJ-ARGV: $*"; echo "HJ-CALLED" >>"%s/hj-called"; shift 5; while [ "$1" != -- ]; do shift; done; shift; OFFLOAD_QUEUE_HELD=x exec "$@"\n' "$w" >"$qr/tools/heavy-job"
rm -f "$w/xnu-count" "$w/hj-called"
qj env REMOTE_JOB_XNU_BUILD="$w/bin/fake-xnu2" "$qr/tools/remote-job" gate-pre
ok "queue (W35 R22): offload-host gate-pre queues (heavy-job called) and never runs the local XNU pre-step (no lab-host lock on the offload-host route)" "$([ -e "$w/hj-called" ] && [ ! -e "$w/xnu-count" ] && echo 1 || echo 0)"
qj "$qr/tools/remote-job" --where nowhere selftest
ok "die with exit 2 prints RESULT: ERROR (not FAIL)" "$([ $rc = 2 ] && [ "$(has '^RESULT: ERROR remote-job' "$out")" = 1 ] && echo 1 || echo 0)"
# (the W34 R22 refusal of gate-pre inside a held offload-host ticket, commit fa9adde, is gone: the offload-host route no longer takes the lab-host lock)

# review offload-mutcache-r1 F1: the real quarantine() under a REAL bind mount (a stub cannot show EBUSY). The mounted root holds the cache as a subdir: renaming the
# subdir works; renaming the mount point itself (the r1 layout) fails with EBUSY. Needs an unprivileged user+mount namespace; without one this case is a loud SKIP, never a pass.
mt=$w/mnt-test; mkdir -p "$mt/root/abc/aa" "$mt/mnt"; echo x >"$mt/root/abc/aa/k"
if unshare -rm true 2>/dev/null; then
	mo=$(unshare -rm bash -c 'mount --bind "$1/root" "$1/mnt" || exit 9
		q() { GATE_MUT_CACHE=$2 python3 -I -c "import sys; sys.path.insert(0, sys.argv[1]); import wlan_mutant_cache as C; print(\"Q\", C.quarantine())" "$3" 2>&1 | tail -n 1; }
		q x "$1/mnt/abc" "$4"; mkdir -p "$1/mnt/def"; q x "$1/mnt" "$4"' _ "$mt" "" "" "$PWD/tools" 2>&1)
	ok "mutcache real mount: quarantine renames the cache SUBDIR of the mounted root (<id>.bad-N next to it), renaming the mount point itself fails EBUSY" "$([ "$(has "^Q $mt/mnt/abc\.bad-[0-9]*\$" "$mo")$(has 'Errno 16' "$mo")" = 11 ] && ls -d "$mt"/root/abc.bad-* >/dev/null 2>&1 && [ ! -e "$mt/root/abc" ] && echo 1 || echo 0)"
else
	echo "SKIP WARNING mutcache real-mount case: no unprivileged user+mount namespace here (unshare -rm), the F1 layout is NOT re-proven in this run"; skipped=1
fi

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n${skipped:+ skipped=$skipped (WARN: mutcache real-mount case not run)}"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
