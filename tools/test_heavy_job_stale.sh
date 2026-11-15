#!/bin/bash
# shellcheck disable=SC2154,SC2034  # variables (w, here, n, bad, ok, has, ...) are set by the sourced test_heavy_job_lib.sh
# Part 'stale' of the tools/heavy-job host test (T-d543): a copy of tools/heavy-job whose blob differs from the canonical one re-execs the canonical copy with one loud line,
# the same blob runs in place, a missing/unreadable canonical copy is a `HEAVY GAP:` line (local copy runs), and `status` flags live tickets of other blobs as STALE-WAITER.
# Mutants (each must be killed): drop the exec, drop the loop guard, drop the GAP line, drop the STALE-WAITER comparison.
# Last line: RESULT: PASS cases=N | RESULT: FAIL ...
# shellcheck source=tools/test_heavy_job_lib.sh
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
. "$(dirname "$0")/test_heavy_job_lib.sh" || exit 2

real=$here/tools/heavy-job
cb=$(git hash-object "$real")
# copies of the tool in their own dirs (heavy-job sources tools/offload-conf.sh next to itself); each is never the real file
mkcopy() {   # mkcopy DIR SED-EXPR|-  -> DIR/tools/heavy-job (a sed edit of the real tool, or a plain copy)
	mkdir -p "$1/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$here/tools/heavy-job-canon.sh" "$1/tools/" || return 1
	if [ "$2" = - ]; then cp "$real" "$1/tools/heavy-job"; else sed "$2" "$real" >"$1/tools/heavy-job"; fi
	chmod +x "$1/tools/heavy-job"
}
# S: an OLD admission rule stand-in (runs STALE-RULE-RAN and exits 9 if it ever reaches cmd_run); S2: differs only by a comment line (logic intact); SAME: byte-identical
mkcopy "$w/S" 's/^cmd_run() {$/cmd_run() { echo STALE-RULE-RAN; exit 9;/' || exit 2
mkcopy "$w/S2" '$a # stale marker' || exit 2
mkcopy "$w/SAME" - || exit 2
cmp -s "$real" "$w/SAME/tools/heavy-job" || exit 2
sb=$(git hash-object "$w/S/tools/heavy-job"); s2b=$(git hash-object "$w/S2/tools/heavy-job")
[ "$sb" != "$cb" ] && [ "$s2b" != "$cb" ] || exit 2
export GIT_CEILING_DIRECTORIES=$w   # a copy under $w is never inside some outer git repo
args=(run --class analysis --label st --wait 20 -- true)
# the stand-in canonical copy: proves WHICH file ran and what environment the re-exec passed
cat >"$w/standin" <<'SI'
#!/bin/bash
echo "standin reexec=${HEAVY_JOB_REEXEC:-unset} args=$*"
SI
chmod +x "$w/standin"

# ---- checks, each a function of the copy under test (the mutants run the same checks and must FAIL them)
chk_a() {   # (a) a differing blob prints the STALE COPY line and the canonical rule admits the job (STALE-RULE-RAN never printed)
	out=$(HEAVY_JOB_CANONICAL=$real "$1" "${args[@]}" 2>&1); rc=$?
	[ $rc = 0 ] && grep -qF "heavy-job: STALE COPY $1 blob=$sb -> exec $real blob=$cb" <<<"$out" && grep -q '^RESULT: PASS' <<<"$out" && ! grep -q STALE-RULE-RAN <<<"$out"
}
chk_guard() {   # the re-exec passes HEAVY_JOB_REEXEC=1 and the same argv
	out=$(HEAVY_JOB_CANONICAL=$w/standin "$1" "${args[@]}" 2>&1); rc=$?
	grep -qF "standin reexec=1 args=run --class analysis --label st --wait 20 -- true" <<<"$out"
}
chk_gap() {   # (c) canonical file missing: a HEAVY GAP line and the local copy still runs
	out=$(HEAVY_JOB_CANONICAL=$w/no-such-heavy-job "$1" "${args[@]}" 2>&1); rc=$?
	[ $rc = 0 ] && grep -qF "HEAVY GAP: canonical heavy-job not found ($w/no-such-heavy-job does not exist), running $1" <<<"$out" && grep -q '^RESULT: PASS' <<<"$out"
}
# live fake waiters for the status checks: a process holding the flock of a ticket file, pid written into the ticket
holdwait() { for _ in $(seq 100); do [ -e "$1" ] && ! ( flock -n 3 ) 3<"$1" 2>/dev/null && return 0; sleep 0.05; done; return 1; }   # the file exists AND a process holds its lock   # the probe holds the lock for an instant: the fake waiters take it with `flock -w 5` (not -n), else a probe that wins the race kills the fake (T-eae6)
fakew() {   # fakew NAME TICKETLINE-SUFFIX -> ticket $w/hqd/<n>.<name>, held by a live process
	local t=$w/hqd/$3.$1
	mkdir -p "$w/hqd"
	local p
	bash -c 'exec 9>>"$1"; flock -w 5 9 || exit 1; exec sleep 120.71' _ "$t" & p=$!; echo "$p" >>"$w/fakew.pid"
	holdwait "$t"
	printf 'u %s %s %s waiting%s\n' "$1" "$p" "$(date +%s)" "$2" >"$t"
}
fakew fa " blob=deadbeef00000000000000000000000000000000" 000001
fakew fb " blob=$cb" 000002
pa=$(sed -n 's/^u fa \([0-9]*\) .*/\1/p' "$w/hqd/000001.fa"); pb=$(sed -n 's/^u fb \([0-9]*\) .*/\1/p' "$w/hqd/000002.fb")
mkdir -p "$w/old/tools"; printf '#!/bin/bash\nexec 9>>"$1"; flock -w 5 9 || exit 1; sleep 120.72; :\n' >"$w/old/tools/heavy-job"
ob=$(git hash-object "$w/old/tools/heavy-job")
bash "$w/old/tools/heavy-job" "$w/hqd/000003.fc" & echo $! >>"$w/fakew.pid"; pc=$!
holdwait "$w/hqd/000003.fc"
printf 'u fc %s %s waiting\n' "$pc" "$(date +%s)" >"$w/hqd/000003.fc"   # no blob field: the blob comes from /proc/PID/cmdline
fakew fd "" 000004; pd=$(sed -n 's/^u fd \([0-9]*\) .*/\1/p' "$w/hqd/000004.fd")   # no blob field and not a heavy-job process: blob=unknown
cp "$w/fakew.pid" "$w/fakew2.pid" 2>/dev/null   # cleanup() kills every pid in $w/*.pid
chk_d() {   # (d) status prints STALE-WAITER for the differing waiters, not for the canonical-blob one; $1 = the status script (its own blob is the canonical one)
	local cbl; cbl=$(git hash-object "$1")
	out=$(HEAVY_JOB_QUEUE_DIR=$w/hqd HEAVY_JOB_CANONICAL=$1 "$1" status 2>&1); rc=$?
	grep -qF "STALE-WAITER pid=$pa blob=deadbeef00000000000000000000000000000000 canonical=$cbl" <<<"$out" \
		&& grep -qF "STALE-WAITER pid=$pc blob=$ob canonical=$cbl" <<<"$out" \
		&& grep -qF "STALE-WAITER pid=$pd blob=unknown canonical=$cbl" <<<"$out" \
		&& ! grep -qF "STALE-WAITER pid=$pb " <<<"$out" && grep -q '^RESULT: PASS status' <<<"$out"
}

# ---- (a)
chk_a "$w/S/tools/heavy-job"; r=$?; ok "stale a: a copy with another blob prints 'STALE COPY <path> blob=x -> exec <canonical> blob=y' and the canonical rule admits (RESULT: PASS, old rule never ran): $(tail -n 2 <<<"$out" | head -1 | cut -c1-160)" "$([ $r = 0 ] && echo 1 || echo 0)"
chk_guard "$w/S/tools/heavy-job"; r=$?; ok "stale a: the re-exec passes HEAVY_JOB_REEXEC=1 and the same argv" "$([ $r = 0 ] && echo 1 || echo 0)"
out=$(HEAVY_JOB_REEXEC=1 HEAVY_JOB_CANONICAL=$real "$w/S2/tools/heavy-job" run --class analysis --label stg --wait 20 -- bash -c 'echo "reexec=[${HEAVY_JOB_REEXEC-unset}]"' 2>&1)
ok "stale a: HEAVY_JOB_REEXEC=1 (the loop guard) runs the copy in place: no STALE COPY line, no GAP, and the guard is not passed on to CMD (reexec=[unset])" "$(! grep -q 'STALE COPY\|HEAVY GAP' <<<"$out" && grep -qF 'reexec=[unset]' <<<"$out" && grep -q '^RESULT: PASS' <<<"$out" && echo 1 || echo 0)"
# ---- (b)
out=$(HEAVY_JOB_CANONICAL=$real "$w/SAME/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
ok "stale b: a byte-identical copy runs in place: no STALE COPY line, no HEAVY GAP, RESULT: PASS" "$(! grep -q 'STALE COPY\|HEAVY GAP' <<<"$out" && [ $rc = 0 ] && grep -q '^RESULT: PASS' <<<"$out" && echo 1 || echo 0)"
out=$("$real" "${args[@]}" 2>&1); rc=$?
ok "stale b: the canonical file itself (HEAVY_JOB_CANONICAL=itself) runs in place without an extra line" "$(! grep -q 'STALE COPY\|HEAVY GAP' <<<"$out" && [ $rc = 0 ] && echo 1 || echo 0)"
# ---- (c)
chk_gap "$w/S2/tools/heavy-job"; r=$?; ok "stale c: canonical missing -> 'HEAVY GAP: canonical heavy-job not found (... does not exist), running <path>' and the local copy ran (RESULT: PASS)" "$([ $r = 0 ] && echo 1 || echo 0)"
if [ "$(id -u)" != 0 ]; then
	cp "$real" "$w/unreadable"; chmod 000 "$w/unreadable"
	out=$(HEAVY_JOB_CANONICAL=$w/unreadable "$w/S2/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
	ok "stale c: canonical unreadable -> 'HEAVY GAP: ... is not readable' and the local copy ran" "$(grep -qF "HEAVY GAP: canonical heavy-job not found ($w/unreadable is not readable), running" <<<"$out" && [ $rc = 0 ] && echo 1 || echo 0)"
else ok "stale c: canonical unreadable (SKIPPED: running as root, chmod 000 does not stop root)" 1; fi
mkdir -p "$w/nogit/tools"; cp "$w/S2/tools/"* "$w/nogit/tools/"
out=$(env -u HEAVY_JOB_CANONICAL "$w/nogit/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
ok "stale c: no HEAVY_JOB_CANONICAL and the copy is not in a git checkout -> 'HEAVY GAP: ... is not in a git checkout', local copy ran" "$(grep -qF 'HEAVY GAP: canonical heavy-job not found (' <<<"$out" && grep -qF 'is not in a git checkout), running' <<<"$out" && [ $rc = 0 ] && echo 1 || echo 0)"
# default resolution: a linked worktree resolves the main clone's copy; the main clone runs in place
repo=$w/repo; mkdir -p "$repo/tools" && cp "$real" "$here/tools/heavy-job-canon.sh" "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$repo/tools/"
g() { git -C "$repo" -c user.email=t@t -c user.name=t "$@"; }
g init -q -b trunk && g add tools && g commit -qm c && g worktree add -q "$w/wt" -b wt >/dev/null 2>&1
echo '# changed in the worktree' >>"$w/wt/tools/heavy-job"; wb=$(git hash-object "$w/wt/tools/heavy-job")
out=$(env -u HEAVY_JOB_CANONICAL "$w/wt/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
ok "stale c: default resolver: a worktree copy finds the main clone's tools/heavy-job (STALE COPY ... -> exec <main clone>/tools/heavy-job) and the job passes" "$(grep -qF "heavy-job: STALE COPY $w/wt/tools/heavy-job blob=$wb -> exec $repo/tools/heavy-job blob=$cb" <<<"$out" && [ $rc = 0 ] && grep -q '^RESULT: PASS' <<<"$out" && echo 1 || echo 0)"
out=$(env -u HEAVY_JOB_CANONICAL "$repo/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
ok "stale c: default resolver: the main clone's own copy runs in place (no extra line)" "$(! grep -q 'STALE COPY\|HEAVY GAP' <<<"$out" && [ $rc = 0 ] && echo 1 || echo 0)"
# ---- (d)
chk_d "$real"; r=$?; [ $r = 0 ] || echo "$out" >&2; ok "stale d: status prints STALE-WAITER pid=N blob=x canonical=y for a waiter with another blob in its ticket, one derived from /proc/PID/cmdline (old ticket without blob), and blob=unknown; none for the canonical blob" "$([ $r = 0 ] && echo 1 || echo 0)"
# a real waiter: tickets written by a copy record its blob (run in place by the loop guard behind a real holder), and status flags it
holdl 8
( HEAVY_JOB_REEXEC=1 "$w/S2/tools/heavy-job" run --class analysis --label sw --wait 60 -- true >"$w/sw.out" 2>&1 ) & echo $! >"$w/sw.pid"
lqwait 2
tk=$(ls "$w"/hq/[0-9]*.sw 2>/dev/null | head -1)
st=$(HEAVY_JOB_CANONICAL=$real "$real" status 2>&1)
ok "stale d: a live waiter run by another blob: its ticket line ends 'blob=$s2b' and status shows STALE-WAITER ... blob=$s2b canonical=$cb label=sw" "$([ -n "$tk" ] && grep -q "blob=$s2b" "$tk" && grep -qF "STALE-WAITER pid=" <<<"$st" && grep -q "STALE-WAITER pid=[0-9]* blob=$s2b canonical=$cb label=sw" <<<"$st" && echo 1 || echo 0)"
waitl lh sw
ok "stale d: after the holder ended the stale-blob waiter ran and passed (the FIFO is not blocked by a flag)" "$(grep -q '^RESULT: PASS' "$w/sw.out" && echo 1 || echo 0)"

# T-4675: the canonical-copy code is a sourced module; without it the tool says so in one HEAVY GAP line and the local copy runs
mkdir -p "$w/NOMOD/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$real" "$w/NOMOD/tools/"
out=$(HEAVY_JOB_CANONICAL=$real "$w/NOMOD/tools/heavy-job" "${args[@]}" 2>&1); rc=$?
ok "stale e: heavy-job-canon.sh missing -> one 'HEAVY GAP: canonical-copy module ... missing' line and the local copy ran (RESULT: PASS)" "$([ "$(grep -c 'HEAVY GAP: canonical-copy module' <<<"$out")" = 1 ] && [ $rc = 0 ] && grep -q '^RESULT: PASS' <<<"$out" && echo 1 || echo 0)"

# ---- mutants: each edit of the tool must make its check FAIL
mutant() {   # mutant NAME BASE-SED-EXPR MUTATION-SED-EXPR CHECK [CHECK-ARG-DIR]
	local d=$w/mut-$1 base=$2 mut=$3 chk=$4
	mkdir -p "$d/tools" && cp "$here/tools/offload-conf.sh" "$here/tools/offload_conf.py" "$d/tools/" || return 1
	sed "$base" "$real" >"$d/tools/heavy-job"; chmod +x "$d/tools/heavy-job"   # T-4675: the mutation edits the sourced module (heavy-job-canon.sh), the base edit the tool
	sed "$mut" "$here/tools/heavy-job-canon.sh" >"$d/tools/heavy-job-canon.sh"
	if cmp -s "$here/tools/heavy-job-canon.sh" "$d/tools/heavy-job-canon.sh"; then ok "mutant $1: the sed edit changed nothing (the marker moved)" 0; return; fi
	if "$chk" "$d/tools/heavy-job" >/dev/null 2>&1; then ok "mutant $1: SURVIVED ($chk still passes)" 0; else ok "mutant $1: killed ($chk fails)" 1; fi
}
sS='s/^cmd_run() {$/cmd_run() { echo STALE-RULE-RAN; exit 9;/'
mutant drop-exec "$sS" 's/^.*# T-d543:exec$/\t\t\t:/' chk_a
mutant drop-guard "$sS" 's/HEAVY_JOB_REEXEC=1 exec "\$BASH"/exec "$BASH"/' chk_guard
mutant drop-gap-line '$a # stale marker' 's/^.*# T-d543:gap$/\t\t\thj_canon=/' chk_gap
mutant drop-stale-flag 's/^$/\n/' 's/\[ "\$wb" != "\$hj_canon_blob" \]/false/;s/if \[ -z "\$wb" \]; then echo/if false; then echo/' chk_d

if [ $bad = 0 ]; then echo "RESULT: PASS cases=$n"; exit 0; fi
echo "RESULT: FAIL cases=$n failed=$bad"; exit 1
