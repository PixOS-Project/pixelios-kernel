#!/bin/bash
# shellcheck disable=SC2154,SC2034  # w, ok, has, hj, n, bad come from the sourced test_heavy_job_lib.sh
# Host test of T-4ecb "the offload host is never a dependency": tools/remote-job and tools/heavy-job fall back to this host when the offload host is lost after the job started,
# with exactly one `<HOST> GAP: fell back` line (rule 8); a real job FAIL on the offload host never falls back; its absence before the start runs local as before.
# Fake ssh/scp on PATH (no network, no real host, no phone), fixtures as in tools/test_remote_job.sh. Mutants: GAP line removed, mid-job fallback removed.
# Last line: RESULT: PASS <host>-fallback cases=N | RESULT: FAIL <host>-fallback ... (<host> = the --where value of the offload host, read from tools/remote-job usage so no host literal lives here)
# shellcheck source=tools/test_heavy_job_lib.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2
HW=$(sed -n 's/.*--where auto|local|\([a-z-]*\)|.*/\1/p' "$here/tools/remote-job" | head -1); [ -n "$HW" ] || { echo "RESULT: FAIL fallback cannot read the --where host word from tools/remote-job"; exit 2; }
HU=$(tr a-z A-Z <<<"$HW")
export OFFLOAD_MIN_FREE_GB=1 OFFLOAD_ROOT=$w/sroot
repo=$w/rjrepo; mkdir -p "$repo/tools" "$repo/xnu/patches" "$w/bin" "$w/sroot/work" "$w/home/work/pixos" "$w/home/.local/bin"
echo p >"$repo/xnu/patches/0001.patch"
cp "$here/tools/remote-job" "$repo/tools/remote-job"; cp "$here/tools/rj-local-transport" "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$repo/tools/"; chmod +x "$repo/tools/remote-job"
printf '#!/bin/bash\nexec "$@"\n' >"$repo/tools/heavy-job"; chmod +x "$repo/tools/heavy-job"   # stub: only the local-lane lock wrapper, never the real lock
mkdir -p "$w/sroot/work/gate-k/tools/third_party" && echo 'export ENVSH=1' >"$w/sroot/work/env.sh"
git -C "$repo" init -q && git -C "$repo" -c user.email=t@t -c user.name=t add -A && git -C "$repo" -c user.email=t@t -c user.name=t commit -qm init || exit 2
cat >"$w/bin/ssh" <<'F'
#!/bin/bash
# fake ssh: drop options and host, run the command with HOME=$FAKE_HOME and the remote stdin
echo "ssh" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
shift   # host
echo "$*" >>"$FAKE_LOG.args"   # what the remote shell is asked to run (the stage tool must send `bash -s`, never a fish-parsed command string)
[ "${FAKE_SSH_MODE:-}" = down ] && { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
[ "${FAKE_SSH_MODE:-}" = once ] && [ ! -e "$FAKE_LOG.once" ] && { : >"$FAKE_LOG.once"; echo "ssh: connect to host: Connection refused" >&2; exit 255; }   # first ssh only
s=$(cat)
if [ "${FAKE_SSH_MODE:-}" = drop ] && grep -q 'git init' <<<"$s"; then echo "ssh: connection reset" >&2; exit 255; fi
if [ -n "${FAKE_GOLDEN_TAMPER:-}" ] && grep -q 'git init' <<<"$s"; then   # between the ship and the run: damage the shipped trace
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
printf '#!/bin/bash\ncat >/dev/null; exit 0\n' >"$w/home/.local/bin/docker"; chmod +x "$w/home/.local/bin/docker"
chmod +x "$w/bin/ssh" "$w/bin/scp"
export PATH=$w/bin:$PATH FAKE_HOME=$w/home FAKE_LOG=$w/log REMOTE_JOB_HOST=fake.invalid
export OFFLOAD_QUEUE_HELD=test-bypass   # these cases test remote-job itself (no queue re-exec)
echo 0.50 >"$w/load"; export FAKE_LOADAVG=$w/load
rj() { : >"$FAKE_LOG"; out=$(cd "$repo" && tools/remote-job "$@" 2>&1); rc=$?; }
gapn() { grep -c "^$HU GAP: fell back" <<<"$out"; }
mid_ok() { [ $rc = 0 ] && [ "$(gapn)" = 1 ] && [ "$(has 'where=local' "$out")$(has 'gaps=1' "$out")$(has '^RESULT: PASS remote-job' "$out")" = 111 ] && [ "$(has 'RESULT: PASS' "$(cat "$repo/logs/remote-jobs/selftest/out/RESULT" 2>/dev/null)")" = 1 ]; }

# ---- remote-job
FAKE_SSH_MODE=drop rj selftest
ok "remote-job: ssh lost mid-run (rc 255): finishes on this host, result file written, exactly one gap line, gaps=1" "$(mid_ok && echo 1 || echo 0)"
FAKE_SSH_MODE=drop rj --where $HW selftest
ok "remote-job: --where <host> keeps its no-fallback contract (exit 2, no GAP line)" "$([ $rc = 2 ] && [ "$(gapn)" = 0 ] && echo 1 || echo 0)"
rj selftest-fail
ok "remote-job: real job FAIL on the offload host (rc 7): exit 1, ran there, NO fallback, no GAP line" "$([ $rc = 1 ] && [ "$(has "where=$HW" "$out")$(has 'job rc 7' "$out")" = 11 ] && [ "$(gapn)" = 0 ] && echo 1 || echo 0)"
FAKE_SSH_MODE=down rj selftest
ok "remote-job: offload host absent before the start: runs local as before ('REMOTE: unavailable'), no GAP-fell-back line, no gaps=" "$([ $rc = 0 ] && [ "$(has "^REMOTE: $HW unavailable" "$out")$(has 'where=local' "$out")" = 11 ] && [ "$(gapn)" = 0 ] && [ "$(has 'gaps=' "$out")" = 0 ] && echo 1 || echo 0)"
rj selftest
ok "remote-job: offload host healthy: ran there, no GAP line, no gaps=" "$([ $rc = 0 ] && [ "$(has "where=$HW" "$out")" = 1 ] && [ "$(gapn)" = 0 ] && [ "$(has 'gaps=' "$out")" = 0 ] && echo 1 || echo 0)"
# mutants: each must make the mid-run case fail
cp "$repo/tools/remote-job" "$w/rj.orig"
sed -i 's/echo "\${gapw^^} GAP: fell back to local[^;]*;//' "$repo/tools/remote-job"; cmp -s "$w/rj.orig" "$repo/tools/remote-job" && ok "mutant GAP-line-removed applied" 0
FAKE_SSH_MODE=drop rj selftest
ok "mutant: GAP line removed -> mid-run case fails (killed)" "$(mid_ok && echo 0 || echo 1)"
cp "$w/rj.orig" "$repo/tools/remote-job"
sed -i 's/fallback=\$why; fell_back "\$why"/die "mutant: no fallback"/' "$repo/tools/remote-job"; cmp -s "$w/rj.orig" "$repo/tools/remote-job" && ok "mutant fallback-removed applied" 0
FAKE_SSH_MODE=drop rj selftest
ok "mutant: mid-job fallback removed -> mid-run case fails (killed)" "$(mid_ok && echo 0 || echo 1)"
cp "$w/rj.orig" "$repo/tools/remote-job"

# ---- heavy-job offload lane (--fallback-local: host lost between the caller's probe and the admission)
unset OFFLOAD_QUEUE_HELD
fs=$w/fs; mkdir -p "$fs"
cat >"$w/fssh" <<'F'
#!/bin/bash
cat >/dev/null
[ ! -e "$FS/down" ] || { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
cat "$FS/answer"
F
chmod +x "$w/fssh"
export FS=$fs REMOTE_JOB_SSH=$w/fssh REMOTE_JOB_HOST=fake.invalid OFFLOAD_QUEUE_DIR=$w/q OFFLOAD_REPROBE=1
printf 'load=0.5\nmemkb=33554432\nrj=\nforeign=\n' >"$fs/answer"; : >"$fs/down"
hj $HW run --owner alice --label fb1 --wait 20 --fallback-local -- bash -c 'echo ran-here held=$OFFLOAD_QUEUE_HELD'
ok "heavy-job: host lost before admission + --fallback-local: CMD runs here, exactly one gap line, PASS gaps=1, ticket gone" "$([ $rc = 0 ] && [ "$(grep -c "^OFFLOAD GAP: fell back" <<<"$out")" = 1 ] && [ "$(has 'ran-here' "$out")$(has "^RESULT: PASS offload-queue label=fb1 .*gaps=1" "$out")" = 11 ] && [ -z "$(ls "$w"/q/[0-9]*.* 2>/dev/null)" ] && echo 1 || echo 0)"
hj $HW run --owner alice --label fb2 --wait 20 -- bash -c 'echo should-not-run'
ok "heavy-job: without --fallback-local the old contract holds (RESULT: ERROR unreachable, exit 2, CMD not run, no GAP-fell-back line)" "$([ $rc = 2 ] && [ "$(has "^RESULT: ERROR $HW unreachable" "$out")" = 1 ] && ! grep -q "should-not-run\|^OFFLOAD GAP: fell back" <<<"$out" && echo 1 || echo 0)"
rm -f "$fs/down"
hj $HW run --owner alice --label fb3 --wait 20 --fallback-local -- bash -c 'exit 5'
ok "heavy-job: host healthy + --fallback-local: runs under the slot, CMD rc 5 -> FAIL exit 1, no fallback, no gaps=" "$([ $rc = 1 ] && [ "$(has "^RESULT: FAIL offload-queue label=fb3" "$out")" = 1 ] && ! grep -q "OFFLOAD GAP: fell back\|gaps=" <<<"$out" && echo 1 || echo 0)"
: >"$fs/down"
mkdir -p "$w/hjm/tools"; cp "$here"/tools/heavy-job "$here"/tools/heavy-job-canon.sh "$here"/tools/heavy_job_parts.py "$here"/tools/offload-conf.sh "$here"/tools/offload_conf.py "$w/hjm/tools/"   # the mutant runs from a copy, never the real tool
sed -i 's/^\t\t\t\[ "\$sfb" != 1 \] || { sfell=\$p_err; return 3; }.*$//' "$w/hjm/tools/heavy-job"; cmp -s "$here/tools/heavy-job" "$w/hjm/tools/heavy-job" && ok "mutant heavy-job fallback-removed applied" 0
out=$(HEAVY_JOB_CANONICAL=$w/hjm/tools/heavy-job "$w/hjm/tools/heavy-job" $HW run --owner alice --label fb4 --wait 20 --fallback-local -- bash -c 'echo ran-here' 2>&1); rc=$?
ok "mutant: heavy-job fallback removed -> fallback case fails (killed)" "$([ $rc = 0 ] && grep -q ran-here <<<"$out" && echo 0 || echo 1)"

if [ $bad = 0 ]; then echo "RESULT: PASS $HW-fallback cases=$n"; exit 0; fi
echo "RESULT: FAIL $HW-fallback bad=$bad of $n"; exit 1
