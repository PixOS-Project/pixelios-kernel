#!/bin/bash
# Host test for the offload-sync triggers (TP-055): tools/hooks/{post-commit,post-merge,post-rewrite} + _offload-sync-trigger in a scratch kernel clone with a FAKE tools/offload-sync.
# Rows: a commit / merge / amend each start the tool with --background; a commit does not wait for a slow tool; a failing tool never fails the commit; a missing tool
# is a OFFLOAD-HOST GAP line in logs/offload-sync.log; a commit in a worktree of the clone calls the MAIN checkout's tool. Never touches the real clone or offload-host.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/offload-sync-hook-test.XXXXXX") || { echo "RESULT: FAIL mktemp"; exit 1; }
trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
cases=0 fails=0
ok() { cases=$((cases + 1)); }
bad() { cases=$((cases + 1)); fails=$((fails + 1)); echo "FAIL: $1"; }
K=$T/k; mkdir -p "$K/tools" "$T/hp"; git -C "$K" init -q -b trunk; touch "$K/f"; git -C "$K" add f; git -C "$K" commit -qm init
cp "$HERE"/hooks/post-commit "$HERE"/hooks/post-merge "$HERE"/hooks/post-rewrite "$HERE"/hooks/_offload-sync-trigger "$T/hp/"
git -C "$K" config core.hooksPath "$T/hp"
fake() { printf '#!/bin/sh\n%s\n' "$1" >"$K/tools/offload-sync"; chmod +x "$K/tools/offload-sync"; }
waitfor() { local n; for _ in $(seq 1 50); do n=$(grep -c . "$T/calls" 2>/dev/null); [ "${n:-0}" -ge "$1" ] && return 0; sleep 0.1; done; return 1; }
fake "echo \"\$* \$(pwd)\" >>$T/calls"
echo a >"$K/f"; git -C "$K" commit -qam a; waitfor 1 && grep -q '^--background ' "$T/calls" && ok || bad "post-commit starts tools/offload-sync --background"
git -C "$K" checkout -q -b feat; echo b >"$K/f"; git -C "$K" commit -qam b; git -C "$K" checkout -q trunk; echo c >"$K/g"; git -C "$K" add g; git -C "$K" commit -qm c
git -C "$K" merge -q --no-edit feat >/dev/null 2>&1; waitfor 4 && ok || bad "post-merge starts it ($(wc -l <"$T/calls") calls)"
n=$(grep -c . "$T/calls"); git -C "$K" commit -q --amend -m amended; waitfor $((n + 2)) && ok || bad "post-rewrite starts it after an amend ($(grep -c . "$T/calls") calls, want >= $((n + 2)))"
fake "sleep 5; echo late >>$T/late"
s=$(date +%s); echo d >"$K/f"; git -C "$K" commit -qam d; e=$(date +%s)
{ [ $((e - s)) -le 2 ] && [ ! -e "$T/late" ]; } && ok || bad "a commit does not wait for a slow tool ($((e - s)) s)"
fake "exit 1"
echo e >"$K/f"; git -C "$K" commit -qam e && ok || bad "a failing tool never fails the commit"
rm "$K/tools/offload-sync"; echo f >"$K/f"; git -C "$K" commit -qam f && ok || bad "a missing tool never fails the commit"
grep -q 'OFFLOAD-HOST GAP: hook: .*tools/offload-sync is missing' "$K/logs/offload-sync.log" 2>/dev/null && ok || bad "a missing tool is a OFFLOAD-HOST GAP line in logs/offload-sync.log"
fake "echo \"wt \$(pwd)\" >>$T/calls2"
git -C "$K" worktree add -q -b wtb "$T/wt" trunk; echo w >"$T/wt/f"; git -C "$T/wt" commit -qam w
for _ in $(seq 1 50); do [ -s "$T/calls2" ] && break; sleep 0.1; done
grep -q '^wt ' "$T/calls2" 2>/dev/null && ok || bad "a commit in a worktree runs the MAIN checkout's tool"
if [ $fails = 0 ]; then echo "RESULT: PASS offload-sync-hook cases=$cases"; exit 0; fi
echo "RESULT: FAIL offload-sync-hook cases=$cases fails=$fails"; exit 1
