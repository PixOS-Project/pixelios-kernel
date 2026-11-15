#!/bin/bash
# Host test for the kernel repo hooks (TP-054): tools/hooks/pre-commit (patch numbers + trunk guard), tools/hooks/commit-msg (trailer + role),
# tools/install-hooks, tools/claude-settings.json. Everything runs in scratch repos under mktemp, never in the real clone.
# Mutants: copies of the files with ONE rule disabled run this same test inside `unshare -r -m -p -f --mount-proc`; each must fail it (AP-303: a mutant
# never runs outside a throwaway namespace, and these only commit in scratch repos). KH_TOOLS = the tools dir under test (default: this file's dir);
# KH_NO_MUTANTS=1 = inner run. Review kernel-hooks-r1 F1-F10, F14 are rows of this file. Last line RESULT: PASS|FAIL ...; exit 0/1.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
KH_TOOLS=${KH_TOOLS:-$HERE}
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_PREFIX   # a caller inside a git hook exports these: `git -C scratch ...` in a test then hits the REAL repo (2026-11-02: stray commit, core.bare=true, fixture branches)
ROOT=$(cd "$KH_TOOLS/.." && pwd)
SUPER_REAL=${SUPER_REAL:-$HOME/.t3/projects/pixelios}
T=$(mktemp -d "${TMPDIR:-/tmp}/kernel-hooks-test.XXXXXX") || { echo "RESULT: FAIL mktemp"; exit 1; }
trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset PIXOS_ROLES_FILE PIXOS_WORKTREES_DIR
cases=0 fails=0
ok() { cases=$((cases + 1)); }
bad() { cases=$((cases + 1)); fails=$((fails + 1)); echo "FAIL: $1"; [ -n "${2:-}" ] && echo "      $2" | head -5; }

# ---- scratch super + kernel clone ---------------------------------------------------------------------------------------------------------
A=mcp:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa   # infra-20 current
A0=mcp:11111111-1111-4111-8111-111111111111  # infra-20 retired
B=mcp:bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb   # fix-21 current
C=mcp:cccccccc-cccc-4ccc-8ccc-cccccccccccc   # gpu (no numeric suffix)
W=mcp:launch:$(printf 'd%.0s' $(seq 64))     # watcher
S=$T/super
K=$S/pixos-project/pixelios-kernel
mkdir -p "$S/docs" "$K/tools"
{
	printf '# test registry\nrole\tthread_id\tgeneration\tsince_utc\tpredecessor_id\tmodel\tstatus\n'
	printf 'infra-20\t%s\t1\tunknown\t-\topus\tretired\n' "$A0"
	printf 'infra-20\t%s\t2\tunknown\t%s\topus\tcurrent\n' "$A" "$A0"
	printf 'fix-21\t%s\t1\tunknown\t-\topus\tcurrent\n' "$B"
	printf 'gpu\t%s\t1\tunknown\t-\topus\tcurrent\n' "$C"
	printf 'watcher\t%s\t1\tunknown\t-\topus\tcurrent\n' "$W"
	printf 'wifi-g2\t-\t2\t2026-10-29T21:40:00Z\t-\tlast thread settled\tclosed\n'
} >"$S/docs/ROLES.tsv"
cp -r "$KH_TOOLS/hooks" "$KH_TOOLS/install-hooks" "$KH_TOOLS/claude-settings.json" "$K/tools/"
cp "$ROOT/.gitignore" "$K/.gitignore"
cat >"$K/tools/patch-numbers" <<'PYEOF'
import os, sys
sys.exit(int(os.environ.get("FAKE_PN_RC", "0")))
PYEOF
cat >"$K/tools/validate-repo.py" <<'PYEOF'
import os, sys
print("fake validate-repo")
print("RESULT: PASS" if os.environ.get("FAKE_VR_RC", "0") == "0" else "RESULT: FAIL (1 of 8 checks failed)")
sys.exit(int(os.environ.get("FAKE_VR_RC", "0")))
PYEOF
git -C "$K" init -q -b trunk
export PIXOS_UNGATED_TRUNK="test_kernel_hooks scratch repo"   # reference-transaction: the scratch trunk has no gate PASS; its own cases unset this
export GATE_PASS_DIR="$T/gate-pass"
echo base >"$K/f"
git -C "$K" add -A
PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m base || { echo "RESULT: FAIL scratch setup"; exit 1; }
WT=$T/wts
mkdir -p "$WT" "$T/other"
git -C "$K" worktree add -q -b w1 "$WT/w1"
git -C "$K" worktree add -q -b w2 "$T/other/w2"
SRC=$K/tools/claude-settings.json

# attempt NAME WANT(0|1) PATTERN [VAR=val ...] -- git-commit-args : change a file, run git commit in the scratch kernel with a clean hook env
attempt() {
	local name=$1 want=$2 pat=$3 rc
	shift 3
	local envs=()
	while [ "$1" != -- ]; do envs+=("$1"); shift; done
	shift
	echo "$RANDOM" >>"$K/f"
	git -C "$K" add f
	(cd "$K" && env -u T3CODE_THREAD_ID -u PIXOS_TRUNK_DIRECT -u PIXOS_ALLOW_NO_TRAILER -u PIXOS_TRAILER_ROLE_OVERRIDE -u FAKE_PN_RC \
		PIXOS_ROLES_FILE="$S/docs/ROLES.tsv" "${envs[@]}" git commit -q "$@") >"$T/out" 2>&1
	rc=$?
	[ $rc = 0 ] || git -C "$K" reset -q --hard HEAD   # a refused commit leaves f staged; keep branch switches clean
	if { [ "$want" = 0 ] && [ $rc != 0 ]; } || { [ "$want" = 1 ] && [ $rc = 0 ]; }; then bad "$name (rc=$rc, wanted $want)" "$(cat "$T/out")"; return; fi
	if [ -n "$pat" ] && ! grep -q -- "$pat" "$T/out"; then bad "$name (no '$pat' in output)" "$(cat "$T/out")"; return; fi
	ok
}
tr_() { echo "PixOS-Worker: $1"; }   # trailer paragraph

# install the hooks into the scratch clone with the tool under test (also the first install-hooks row)
(cd "$K" && PIXOS_WORKTREES_DIR=$WT tools/install-hooks) >"$T/inst0" 2>&1
HP=$K/.git/pixos-hooks
[ "$(git -C "$K" config --get core.hooksPath)" = "$HP" ] && ok || bad "install-hooks sets core.hooksPath to the hook copies in the git dir (F2)" "$(cat "$T/inst0")"
{ [ -x "$HP/pre-commit" ] && [ -x "$HP/commit-msg" ] && cmp -s "$K/tools/hooks/pre-commit" "$HP/pre-commit" && cmp -s "$K/tools/hooks/commit-msg" "$HP/commit-msg"; } && ok || bad "install-hooks copied both hooks, executable, byte-identical (F2)" "$(cat "$T/inst0")"

# ---- post-checkout (T-e23b, AP-144): a NEW worktree gets its gitignored inputs linked by the main clone's tools/worktree-new --link-only ----------------
printf '#!/bin/sh\necho "$@" >>"%s/wn-calls"\n' "$T" >"$K/tools/worktree-new"; chmod +x "$K/tools/worktree-new"
[ -x "$HP/post-checkout" ] && ok || bad "install-hooks copies post-checkout into the hook dir (T-e23b)"
git -C "$K" worktree add -q -b autolink "$T/autolink-wt" HEAD >"$T/wt0" 2>&1
grep -qF -- "--link-only $T/autolink-wt" "$T/wn-calls" 2>/dev/null && ok || bad "a raw git worktree add runs tools/worktree-new --link-only on the new worktree (T-e23b)" "$(cat "$T/wt0" "$T/wn-calls" 2>&1)"
rm -f "$T/wn-calls"; git -C "$T/autolink-wt" switch -q -c autolink2 HEAD
[ ! -e "$T/wn-calls" ] && ok || bad "an ordinary branch switch does not relink (T-e23b)"
rm "$K/tools/worktree-new"; git -C "$K" worktree add -q -b autolink3 "$T/autolink-wt3" HEAD >"$T/wt1" 2>&1
grep -q "WORKTREE-AUTOLINK FAIL: .*worktree-new missing" "$T/wt1" && ok || bad "a missing tools/worktree-new is a loud WORKTREE-AUTOLINK FAIL line, the checkout still succeeds (T-e23b)" "$(cat "$T/wt1")"
git -C "$K" worktree remove --force "$T/autolink-wt" >/dev/null 2>&1; git -C "$K" worktree remove --force "$T/autolink-wt3" >/dev/null 2>&1

# ---- trunk guard + patch numbers (pre-commit) -----------------------------------------------------------------------------------------------
attempt "trunk non-merge commit denied" 1 "DENIED, a non-merge commit on trunk" -- -m m -m "$(tr_ t)"
attempt "trunk deny names the escape" 1 "PIXOS_TRUNK_DIRECT" -- -m m -m "$(tr_ t)"
attempt "trunk deny names do-instead" 1 "git worktree add" -- -m m -m "$(tr_ t)"
attempt "trunk empty PIXOS_TRUNK_DIRECT still denied" 1 "DENIED" PIXOS_TRUNK_DIRECT= -- -m m -m "$(tr_ t)"
attempt "trunk commit allowed with PIXOS_TRUNK_DIRECT=reason" 0 "allowed by PIXOS_TRUNK_DIRECT" PIXOS_TRUNK_DIRECT=census -- -m m -m "$(tr_ t)"
attempt "trunk deny names the census escape command (F1)" 1 "census-write after BRANCH merge" -- -m m -m "$(tr_ t)"
attempt "census commit on trunk: documented escape + role trailer allowed (F1)" 0 "allowed by PIXOS_TRUNK_DIRECT" T3CODE_THREAD_ID=$A PIXOS_TRUNK_DIRECT="census-write after feat merge" -- -m m -m "$(tr_ infra-20)"
git -C "$K" tag trunk
attempt "a tag named trunk does not disable the trunk guard (F3)" 1 "DENIED, a non-merge commit on trunk" -- -m m -m "$(tr_ t)"
git -C "$K" tag -d trunk >/dev/null
rm -rf "$K/tools/hooks"   # a checkout of a branch without tools/hooks in the main checkout: the installed copies must still run (F2)
attempt "trunk guard still runs when tools/hooks is gone from the main checkout (F2)" 1 "DENIED, a non-merge commit on trunk" -- -m m -m "$(tr_ t)"
git -C "$K" checkout -q -- tools/hooks
git -C "$K" switch -q -c feat
attempt "branch commit allowed (no trunk guard)" 0 "" -- -m m -m "$(tr_ t)"
attempt "patch-numbers rc 3 denies the commit" 1 "" FAKE_PN_RC=3 -- -m m -m "$(tr_ t)"
attempt "patch-numbers internal error (rc 1) lets it through, loudly" 0 "patch-numbers: internal error" FAKE_PN_RC=1 -- -m m -m "$(tr_ t)"
git -C "$K" switch -q trunk
attempt "patch-numbers rc 3 still denies on trunk with the escape set" 1 "" FAKE_PN_RC=3 PIXOS_TRUNK_DIRECT=x -- -m m -m "$(tr_ t)"
# clean merge of feat into trunk (no conflict: git runs pre-merge-commit, not pre-commit; commit-msg still runs)
(cd "$K" && env -u T3CODE_THREAD_ID PIXOS_ROLES_FILE="$S/docs/ROLES.tsv" git merge -q --no-ff feat -m "Merge feat" -m "$(tr_ t)") >"$T/out" 2>&1 && ok || bad "clean merge on trunk allowed" "$(cat "$T/out")"
# conflicted merge: concluded with `git commit` while MERGE_HEAD exists, this is the path that runs pre-commit on a merge
git -C "$K" switch -q -c c1 && echo one >"$K/conf" && git -C "$K" add conf && PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m c1
git -C "$K" switch -q trunk && echo two >"$K/conf" && git -C "$K" add conf && PIXOS_TRUNK_DIRECT=t PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m c2
git -C "$K" merge c1 >/dev/null 2>&1
[ -f "$K/.git/MERGE_HEAD" ] || bad "scratch: the conflicting merge did not leave MERGE_HEAD"
echo resolved >"$K/conf"
git -C "$K" add conf
attempt "conflict-resolved merge commit on trunk allowed (MERGE_HEAD)" 0 "" -- -m "Merge c1" -m "$(tr_ t)"

# ---- pre-merge-commit (A04, AP-121): a clean merge that brings in tools/ or docs/ changes runs validate-repo first
merge_try() { # merge_try NAME WANT(0|1) PATTERN BRANCH [VAR=val ...] : merge BRANCH into trunk with a clean hook env; a refused merge is aborted
	local name=$1 want=$2 pat=$3 br=$4 rc
	shift 4
	(cd "$K" && env -u T3CODE_THREAD_ID -u PIXOS_TRUNK_DIRECT -u PIXOS_NO_VALIDATE_MERGE -u FAKE_VR_RC PIXOS_ROLES_FILE="$S/docs/ROLES.tsv" "$@" \
		git merge -q --no-ff "$br" -m "Merge $br" -m "$(tr_ t)") >"$T/out" 2>&1
	rc=$?
	[ $rc = 0 ] || git -C "$K" merge --abort >/dev/null 2>&1 || git -C "$K" reset -q --hard HEAD
	if { [ "$want" = 0 ] && [ $rc != 0 ]; } || { [ "$want" = 1 ] && [ $rc = 0 ]; }; then bad "$name (rc=$rc, wanted $want)" "$(cat "$T/out")"; return; fi
	if [ -n "$pat" ] && ! grep -q -- "$pat" "$T/out"; then bad "$name (no '$pat' in output)" "$(cat "$T/out")"; return; fi
	ok
}
mkbr() { # mkbr BRANCH FILE : a branch from trunk that adds FILE; back on trunk
	git -C "$K" switch -q -c "$1" trunk && mkdir -p "$(dirname "$K/$2")" && echo "$1" >"$K/$2" && git -C "$K" add "$2" && PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m "$1" && git -C "$K" switch -q trunk
}
mkbr vt tools/newtool; mkbr vd docs/note.md; mkbr vs shim/x.c; mkbr vt2 tools/newtool2; mkbr vt3 tools/newtool3; mkbr vt4 tools/newtool4
merge_try "merge with a tools/ change and a failing validate-repo is denied" 1 "DENIED, validate-repo FAILED" vt FAKE_VR_RC=1
merge_try "the validate deny shows validate-repo's own output" 1 "RESULT: FAIL (1 of 8 checks failed)" vt FAKE_VR_RC=1
merge_try "the validate deny names the do-instead" 1 "fix the branch until" vt FAKE_VR_RC=1
merge_try "the validate deny names the escape" 1 "PIXOS_NO_VALIDATE_MERGE" vt FAKE_VR_RC=1
merge_try "a denied merge left no merge commit" 1 "" vt FAKE_VR_RC=1
! git -C "$K" log -1 --format=%s | grep -q '^Merge vt$' && ok || bad "denied merge: trunk tip is not the refused merge"
merge_try "merge with a docs/ change and a failing validate-repo is denied" 1 "DENIED, validate-repo FAILED" vd FAKE_VR_RC=1
merge_try "merge with only a shim/ change does not run validate-repo (left to the gate)" 0 "" vs FAKE_VR_RC=1
merge_try "merge with a tools/ change and a passing validate-repo is allowed and prints its RESULT" 0 "pre-merge-commit: validate-repo RESULT: PASS" vt2
merge_try "PIXOS_NO_VALIDATE_MERGE=reason allows a failing validate-repo and prints the reason" 0 "skipped by PIXOS_NO_VALIDATE_MERGE=\"census race\"" vt3 FAKE_VR_RC=1 "PIXOS_NO_VALIDATE_MERGE=census race"
merge_try "an empty PIXOS_NO_VALIDATE_MERGE does not skip validate-repo" 1 "DENIED, validate-repo FAILED" vt4 FAKE_VR_RC=1 PIXOS_NO_VALIDATE_MERGE=

git -C "$K" switch -q -c feat3   # the trailer/role rows run on a branch (trunk is denied for every direct commit)
attempt "missing trailer denied" 1 "no 'PixOS-Worker" -- -m m
attempt "missing trailer names the escape" 1 "PIXOS_ALLOW_NO_TRAILER" -- -m m
attempt "missing trailer allowed with PIXOS_ALLOW_NO_TRAILER=1" 0 "" PIXOS_ALLOW_NO_TRAILER=1 -- -m m
attempt "human (no T3CODE_THREAD_ID) with trailer: role check skipped, printed" 0 "no T3CODE_THREAD_ID" -- -m m -m "$(tr_ whatever)"
attempt "infra-20 thread, trailer = its role" 0 "" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ infra-20)"
attempt "infra-20 thread, trailer = its worker label 20ad" 0 "" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 20ad)"
attempt "infra-20 RETIRED id maps to the same role (label 20r ok)" 0 "" T3CODE_THREAD_ID=$A0 -- -m m -m "$(tr_ 20r)"
attempt "infra-20 RETIRED id maps to the same role (label 21y denied)" 1 "DENIED" T3CODE_THREAD_ID=$A0 -- -m m -m "$(tr_ 21y)"
attempt "infra-20 thread, trailer = other role fix-21 denied" 1 "another registered role" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ fix-21)"
attempt "infra-20 thread, trailer = watcher denied" 1 "DENIED" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ watcher)"
attempt "infra-20 thread, label 21y (fix-21 series) denied" 1 "starts with 21" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 21y)"
attempt "wrong-role deny names the escape" 1 "PIXOS_TRAILER_ROLE_OVERRIDE" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 21y)"
attempt "wrong role allowed with PIXOS_TRAILER_ROLE_OVERRIDE=reason, printed" 0 "allowed by PIXOS_TRAILER_ROLE_OVERRIDE" T3CODE_THREAD_ID=$A PIXOS_TRAILER_ROLE_OVERRIDE=handover -- -m m -m "$(tr_ 21y)"
attempt "empty PIXOS_TRAILER_ROLE_OVERRIDE does not override" 1 "DENIED" T3CODE_THREAD_ID=$A PIXOS_TRAILER_ROLE_OVERRIDE= -- -m m -m "$(tr_ 21y)"
attempt "fix-21 thread, label 21x" 0 "" T3CODE_THREAD_ID=$B -- -m m -m "$(tr_ 21x)"
attempt "fix-21 thread, label 20ad denied" 1 "DENIED" T3CODE_THREAD_ID=$B -- -m m -m "$(tr_ 20ad)"
attempt "watcher thread (launch id), trailer watcher" 0 "" T3CODE_THREAD_ID=$W -- -m m -m "$(tr_ watcher)"
attempt "watcher thread, trailer infra-20 denied" 1 "DENIED" T3CODE_THREAD_ID=$W -- -m m -m "$(tr_ infra-20)"
attempt "gpu thread (no numeric suffix), numeric label: NOT checked, printed" 0 "NOT checked" T3CODE_THREAD_ID=$C -- -m m -m "$(tr_ 20ad)"
attempt "undecidable trailer (W42): NOT checked, printed" 0 "neither a registered role nor a numeric label" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ W42)"
attempt "thread not in the registry: skipped, printed" 0 "is not in" T3CODE_THREAD_ID=mcp:99999999-9999-4999-8999-999999999999 -- -m m -m "$(tr_ 21y)"
attempt "unreadable registry: skipped, printed" 0 "not readable" T3CODE_THREAD_ID=$A PIXOS_ROLES_FILE=/nonexistent/ROLES.tsv -- -m m -m "$(tr_ 21y)"
attempt "infra-20 thread, trailer FIX-21 (case spoof) denied (F7)" 1 "another registered role" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ FIX-21)"
attempt "infra-20 thread, trailer Watcher (case spoof) denied (F7)" 1 "DENIED" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ Watcher)"
attempt "infra-20 thread, trailer INFRA-20 allowed (F7)" 0 "" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ INFRA-20)"
attempt "infra-20 thread, label 21Y (case) denied (F7)" 1 "starts with 21" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 21Y)"
attempt "infra-20 thread, label 20AD (case) allowed (F7)" 0 "" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 20AD)"
attempt "two trailers, foreign role first then own: denied (F7)" 1 "DENIED" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ fix-21; tr_ infra-20)"
attempt "two trailers, own then foreign role: denied (F7)" 1 "DENIED" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ infra-20; tr_ fix-21)"
attempt "two own trailers (role + label) allowed (F7)" 0 "" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ infra-20; tr_ 20ad)"
attempt "uppercase thread id maps to the role (label 21y denied) (F14)" 1 "DENIED" T3CODE_THREAD_ID="$(printf '%s' "$A" | tr a-z A-Z)" -- -m m -m "$(tr_ 21y)"
attempt "label 2021ad from infra-20 denied, the number must match exactly (F14)" 1 "starts with 2021" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ 2021ad)"
attempt "trailer 'role' (the registry header word) is not a registered role (F14)" 0 "neither a registered role" T3CODE_THREAD_ID=$A -- -m m -m "$(tr_ role)"
# python3 missing: loud note, commit allowed (F14)
mkdir -p "$T/nopy"
for f in /usr/bin/*; do case ${f##*/} in python*) ;; *) ln -s "$f" "$T/nopy/${f##*/}" 2>/dev/null ;; esac; done
attempt "python3 missing: patch numbers NOT checked, printed, commit allowed (F14)" 0 "python3 not found" PATH="$T/nopy" -- -m m -m "$(tr_ t)"
# default registry location (no PIXOS_ROLES_FILE): super/docs/ROLES.tsv two levels above the main kernel checkout, also from a linked worktree
echo "$RANDOM" >>"$WT/w1/f"
git -C "$WT/w1" add f
(cd "$WT/w1" && env -u PIXOS_ROLES_FILE T3CODE_THREAD_ID=$A git commit -q -m m -m "$(tr_ 21y)") >"$T/out" 2>&1 && bad "linked worktree finds the default registry (commit should be denied)" "$(cat "$T/out")" || { grep -q "DENIED" "$T/out" && ok || bad "linked worktree finds the default registry" "$(cat "$T/out")"; }
(cd "$WT/w1" && env -u PIXOS_ROLES_FILE T3CODE_THREAD_ID=$A git commit -q -m m -m "$(tr_ 20ad)") >"$T/out" 2>&1 && ok || bad "linked worktree: allowed label commits" "$(cat "$T/out")"

# ---- install-hooks ----------------------------------------------------------------------------------------------------------------------------
cd "$K" || exit 1
ih() { (cd "${1:-$K}" && PIXOS_WORKTREES_DIR=$WT tools/install-hooks "${2:-}") >"$T/out" 2>&1; }
# fresh scratch clone: --check must FAIL and change nothing
F=$T/fresh
git -C "$K" switch -q trunk
git clone -q "$K" "$F" || { echo "RESULT: FAIL scratch clone"; exit 1; }
(cd "$F" && PIXOS_WORKTREES_DIR=$WT/none tools/install-hooks --check) >"$T/out" 2>&1; rc=$?
{ [ $rc = 1 ] && grep -q "^RESULT: FAIL" "$T/out" && ! git -C "$F" config --get core.hooksPath >/dev/null && [ ! -e "$F/.claude" ] && [ ! -e "$F/.git/pixos-hooks" ] && ! grep -qx '\.claude/' "$F/.git/info/exclude" 2>/dev/null; } && ok || bad "install-hooks --check on a fresh clone: FAIL, nothing changed (rc=$rc)" "$(cat "$T/out")"
# installed state from the first call: both trees have the copy, the outside worktree does not
{ cmp -s "$SRC" "$K/.claude/settings.json" && cmp -s "$SRC" "$WT/w1/.claude/settings.json"; } && ok || bad "install-hooks wrote .claude/settings.json into the clone and the worktree under the worktrees dir" "$(cat "$T/inst0")"
[ ! -e "$T/other/w2/.claude" ] && ok || bad "install-hooks left a worktree outside the worktrees dir alone"
[ -z "$(git -C "$K" status --porcelain .claude)" ] && ok || bad ".claude/ is ignored in the kernel (never committed)" "$(git -C "$K" status --porcelain .claude)"
ih "$K" --check; rc=$?
{ [ $rc = 0 ] && tail -1 "$T/out" | grep -q "^RESULT: PASS"; } && ok || bad "install-hooks --check after install: PASS (rc=$rc)" "$(cat "$T/out")"
ih "$K"; rc=$?
{ [ $rc = 0 ] && tail -1 "$T/out" | grep -q "^RESULT: PASS" && ! grep -q "^wrote" "$T/out"; } && ok || bad "install-hooks is idempotent (second run writes nothing)" "$(cat "$T/out")"
ih "$WT/w1" ""; rc=$?
[ $rc = 0 ] && ok || bad "install-hooks from a linked worktree resolves the main checkout (rc=$rc)" "$(cat "$T/out")"
echo tamper >"$WT/w1/.claude/settings.json"
ih "$K" --check; rc=$?
{ [ $rc = 1 ] && grep -q "^MISSING $WT/w1/.claude/settings.json" "$T/out" && [ "$(cat "$WT/w1/.claude/settings.json")" = tamper ]; } && ok || bad "--check flags a differing copy and does not touch it (rc=$rc)" "$(cat "$T/out")"
ih "$K"; rc=$?
{ [ $rc = 0 ] && cmp -s "$SRC" "$WT/w1/.claude/settings.json" && grep -q "replaced a differing file" "$T/out"; } && ok || bad "install repairs a differing copy, loudly (rc=$rc)" "$(cat "$T/out")"
(cd "$K" && tools/install-hooks --bogus) >"$T/out" 2>&1; rc=$?
[ $rc = 2 ] && ok || bad "install-hooks usage error is exit 2 (rc=$rc)"
# the .gitignore line is required
sed -i '/^\.claude\/$/d' "$F/.gitignore"
(cd "$F" && PIXOS_WORKTREES_DIR=$WT/none tools/install-hooks) >"$T/out" 2>&1   # installs the copies and the exclude line; then both ignore rules are removed again
sed -i '/^\.claude\/$/d' "$F/.git/info/exclude"
(cd "$F" && PIXOS_WORKTREES_DIR=$WT/none tools/install-hooks --check) >"$T/out" 2>&1; rc=$?
{ [ $rc = 1 ] && grep -q "not ignored" "$T/out" && tail -1 "$T/out" | grep -q "FAIL 2 problem"; } && ok || bad "install-hooks --check fails when .claude/ is not ignored, each problem counted (rc=$rc)" "$(cat "$T/out")"

# F2: an edited or removed hook copy is flagged by --check and repaired by the install
echo "# tampered" >>"$HP/pre-commit"
ih "$K" --check; rc=$?
{ [ $rc = 1 ] && grep -q "^MISSING $HP/pre-commit" "$T/out"; } && ok || bad "--check flags a differing hook copy (rc=$rc)" "$(cat "$T/out")"
ih "$K"; rc=$?
{ [ $rc = 0 ] && cmp -s "$K/tools/hooks/pre-commit" "$HP/pre-commit" && [ -x "$HP/pre-commit" ]; } && ok || bad "install repairs a differing hook copy (rc=$rc)" "$(cat "$T/out")"
chmod -x "$HP/commit-msg"
ih "$K" --check; rc=$?
{ [ $rc = 1 ] && grep -q "^MISSING $HP/commit-msg" "$T/out"; } && ok || bad "--check flags a non-executable hook copy (rc=$rc)" "$(cat "$T/out")"
ih "$K" >/dev/null
# F14: usage and source checks
(cd "$K" && tools/install-hooks --check x) >"$T/out" 2>&1; rc=$?
[ $rc = 2 ] && ok || bad "install-hooks --check with an extra argument is exit 2 (rc=$rc)" "$(cat "$T/out")"
chmod -x "$K/tools/hooks/pre-commit"
ih "$K"; rc=$?
{ [ $rc = 2 ] && grep -q "not executable" "$T/out"; } && ok || bad "a non-executable hook in the main checkout is exit 2 (rc=$rc)" "$(cat "$T/out")"
chmod +x "$K/tools/hooks/pre-commit"
# F4: symlinks are never followed
echo victim >"$T/victim"
rm -f "$WT/w1/.claude/settings.json"; ln -s "$T/victim" "$WT/w1/.claude/settings.json"
ih "$K"; rc=$?
{ [ $rc = 1 ] && grep -q "symlink, not touched" "$T/out" && [ "$(cat "$T/victim")" = victim ]; } && ok || bad "a symlinked settings.json is refused and its target is untouched (F4) (rc=$rc)" "$(cat "$T/out")"
ih "$K" --check; rc=$?
[ $rc = 1 ] && ok || bad "--check also flags a symlinked settings.json (F4) (rc=$rc)"
rm -f "$WT/w1/.claude/settings.json"
mkdir -p "$T/outdir"; mv "$WT/w1/.claude" "$T/w1-claude-moved"; ln -s "$T/outdir" "$WT/w1/.claude"
ih "$K"; rc=$?
{ [ $rc = 1 ] && grep -q "symlink, not touched" "$T/out" && [ -z "$(ls -A "$T/outdir")" ]; } && ok || bad "a symlinked .claude is refused and its target stays empty (F4) (rc=$rc)" "$(cat "$T/out")"
rm -f "$WT/w1/.claude"; mv "$T/w1-claude-moved" "$WT/w1/.claude"
mkdir "$WT/w1/.claude/settings.json" 2>/dev/null || { rm -f "$WT/w1/.claude/settings.json"; mkdir "$WT/w1/.claude/settings.json"; }
ih "$K"; rc=$?
{ [ $rc = 1 ] && grep -q "FAILED  $WT/w1/.claude/settings.json" "$T/out" && [ -d "$WT/w1/.claude/settings.json" ] && [ ! -e "$WT/w1/.claude/settings.json/settings.json" ]; } && ok || bad "a directory named settings.json is refused, nothing moved into it (F4) (rc=$rc)" "$(cat "$T/out")"
rmdir "$WT/w1/.claude/settings.json"
# F14: a write that fails is counted (".claude" is a plain file, mkdir -p fails even as root)
mv "$WT/w1/.claude" "$T/w1-claude-moved"; echo notadir >"$WT/w1/.claude"
ih "$K"; rc=$?
{ [ $rc = 1 ] && grep -q "^FAILED  $WT/w1/.claude/settings.json" "$T/out" && tail -1 "$T/out" | grep -q "^RESULT: FAIL"; } && ok || bad "a failed settings copy is FAILED and counted (rc=$rc)" "$(cat "$T/out")"
rm -f "$WT/w1/.claude"; mv "$T/w1-claude-moved" "$WT/w1/.claude"
ih "$K" >/dev/null
# F5: a worktree's own differing settings.json is kept as a backup
rm -f "$WT/w1/.claude/settings.json.pre-install-hooks"   # an earlier row (tamper) already made one: the first backup is kept
echo '{"permissions":{"allow":["custom"]}}' >"$WT/w1/.claude/settings.json"
ih "$K"; rc=$?
{ [ $rc = 0 ] && grep -q 'custom' "$WT/w1/.claude/settings.json.pre-install-hooks" && cmp -s "$SRC" "$WT/w1/.claude/settings.json" && grep -q "settings.json.pre-install-hooks" "$T/out"; } && ok || bad "a differing settings.json is backed up before it is replaced (F5) (rc=$rc)" "$(cat "$T/out")"
# F6: no temp file; an unwritable TMPDIR must not turn into a PASS over zero trees
(cd "$K" && TMPDIR=/nonexistent PIXOS_WORKTREES_DIR=$WT tools/install-hooks --check) >"$T/out" 2>&1; rc=$?
{ [ $rc = 0 ] && tail -1 "$T/out" | grep -q "^RESULT: PASS .*settings=2 " && ! grep -qi "no such file" "$T/out"; } && ok || bad "install-hooks --check with TMPDIR=/nonexistent still examines both trees (F6) (rc=$rc)" "$(cat "$T/out")"
# F8: a worktree whose branch .gitignore lacks .claude/ is covered by the shared info/exclude
git -C "$K" worktree add -q -b w3 "$WT/w3"
sed -i '/^\.claude\/$/d' "$WT/w3/.gitignore"
PIXOS_ALLOW_NO_TRAILER=1 git -C "$WT/w3" commit -q -am "no .claude ignore" || echo "scratch: w3 commit failed"
ih "$K"; rc=$?
{ [ $rc = 0 ] && cmp -s "$SRC" "$WT/w3/.claude/settings.json" && [ -z "$(git -C "$WT/w3" status --porcelain .claude)" ] && grep -qx '\.claude/' "$K/.git/info/exclude"; } && ok || bad "a worktree on a branch without the .gitignore line does not show .claude as untracked (F8) (rc=$rc)" "$(cat "$T/out"; git -C "$WT/w3" status --porcelain)"
# F11: trailing slash in PIXOS_WORKTREES_DIR, prunable worktree
(cd "$K" && PIXOS_WORKTREES_DIR="$WT/" tools/install-hooks --check) >"$T/out" 2>&1; rc=$?
{ [ $rc = 0 ] && tail -1 "$T/out" | grep -q "settings=3 "; } && ok || bad "a trailing slash in PIXOS_WORKTREES_DIR still finds the worktrees (F11) (rc=$rc)" "$(cat "$T/out")"
git -C "$K" worktree add -q -b w4 "$WT/w4"
rm -rf "$WT/w4"
ih "$K"; rc=$?
{ [ $rc = 0 ] && [ ! -e "$WT/w4" ] && grep -q "^skip    $WT/w4" "$T/out"; } && ok || bad "a prunable worktree is skipped, not resurrected (F11) (rc=$rc)" "$(cat "$T/out")"
git -C "$K" worktree prune

# ---- claude-settings.json: the same hooks as the super settings, and each command runs from this tree ----------------------------------------
python3 -I - "$KH_TOOLS/claude-settings.json" "$SUPER_REAL/.claude/settings.json" "$ROOT" "$SUPER_REAL" "$T" <<'PYEOF' >"$T/py.out" 2>&1
import json, os, re, subprocess, sys
mine, sup, root, super_root, tmp = sys.argv[1:6]
EXPECT = {("PreToolUse", "Bash", "phone-guard", ""), ("PreToolUse", "Write|Edit|MultiEdit|Bash", "patch-guard", ""),
          }   # sprint mode (user 2026-11-09): the agent-guard hooks are not in the settings, template or super
def entries(path):
    out = set()
    for ev, lst in json.load(open(path))["hooks"].items():
        for m in lst:
            for h in m["hooks"]:
                c = h["command"]
                script = re.search(r"tools/([\w-]+)", c).group(1)
                a = re.search(r'python3 "\$P"([^;]*);', c)
                mt = m.get("matcher", "")
                out.add((ev, mt, script, (a.group(1) if a else "").strip()))
    return out
def norm(s):
    return {(e, "ANY" if (m or "").startswith("Read|") else m, sc, a) for e, m, sc, a in s}
mine_e = norm(entries(mine))
exp = norm({(e, ("ANY" if m is None else m), s, a) for e, m, s, a in EXPECT})
if mine_e != exp:
    print("FAIL structure: missing %s extra %s" % (sorted(exp - mine_e), sorted(mine_e - exp)))
    sys.exit(1)
print("ok structure")
K_ABS = "$HOME/.t3/projects/pixelios/pixos-project/pixelios-kernel"
S_ABS = "$HOME/.t3/projects/pixelios"
cfg0 = json.load(open(mine))
cmds = [h["command"] for lst in cfg0["hooks"].values() for m in lst for h in m["hooks"]]
for c in cmds:   # F9: the reviewed main-checkout script comes first, the worktree's own copy only as a fallback
    if "$CLAUDE_PROJECT_DIR" in c and "/pixelios-kernel/tools/" in c and c.index("/pixelios-kernel/tools/") > c.index("$CLAUDE_PROJECT_DIR"):
        print("FAIL F9 order: the worktree copy is tried before the main checkout: %s" % c[:100]); sys.exit(1)
if sum(1 for c in cmds if c.count(K_ABS + "/tools/") >= 1) < 2:
    print("FAIL F9: fewer than 2 commands name the main-checkout script"); sys.exit(1)
stub_n = [0]
def stubrun(c, abs_dir, abs_code, proj_dir=None, proj_code=None):
    """run hook command C with the absolute roots replaced by ABS_DIR (stub exit ABS_CODE; None = no stub) and CLAUDE_PROJECT_DIR=PROJ_DIR"""
    stub_n[0] += 1
    base = os.path.join(tmp, "stub%d" % stub_n[0])
    name = re.search(r"tools/([\w-]+)", c).group(1)
    def put(root, code):
        os.makedirs(os.path.join(root, "tools"), exist_ok=True)
        open(os.path.join(root, "tools", name), "w").write("import sys\nsys.exit(%d)\n" % code)
    ad = os.path.join(base, "abs")
    if abs_code is not None:
        put(ad, abs_code)
    pd = os.path.join(base, "proj")
    if proj_code is not None:
        put(pd, proj_code)
    c2 = c.replace(K_ABS, ad).replace(S_ABS + "/tools/role", ad + "/tools/role")
    return subprocess.run(c2, shell=True, input="{}", capture_output=True, text=True, timeout=30, env=dict(os.environ, CLAUDE_PROJECT_DIR=pd))
for c in cmds:
    nm = re.search(r"tools/([\w-]+)", c).group(1)
    for code, want in ((2, 1), (0, 0), (1, 1)):   # a guard's exit 2 (blocking) is mapped to 1 (non-blocking) like the super settings
        r = stubrun(c, "", code)
        if r.returncode != want:
            print("FAIL F14 %s: stub exit %d gave rc %d, wanted %d" % (nm, code, r.returncode, want)); sys.exit(1)
    r = stubrun(c, "", None)
    if r.returncode != 1 or "hook script missing" not in r.stderr:
        print("FAIL F14 %s: missing script rc=%d stderr=%r" % (nm, r.returncode, r.stderr[:80])); sys.exit(1)
    if nm != "role":
        r = stubrun(c, "", None, None, 0)
        if r.returncode != 0:
            print("FAIL F14 %s: the worktree copy is not used when the main-checkout script is absent (rc %d)" % (nm, r.returncode)); sys.exit(1)
        r = stubrun(c, "", 0, None, 1)
        if r.returncode != 0:
            print("FAIL F9 %s: the worktree copy beat the main-checkout script (rc %d)" % (nm, r.returncode)); sys.exit(1)
print("ok stub exit codes, missing script is a loud exit 1 (non-blocking, as in the super settings), fallback order")
# Hooks the super settings run that the kernel template must NOT carry, each with its reason (T-8886). An entry is exempt only from the "only-super" side:
# a template hook missing from super, or an exempt hook that the template starts to carry, still fails (remove the entry then).
SUPER_ONLY = {}   # sprint mode (user 2026-11-09): the super settings run no super-only hook (tool-lane-guard is gone)
def cmp_super(s, m, allow):
    """(only_super, only_mine) of super entries S against template entries M, minus the exempt ALLOW keys that super really has"""
    return sorted(s - m - set(allow)), sorted(m - s)
a, b = cmp_super({("E", "m", "x", ""), ("E", "m", "y", "")}, {("E", "m", "x", "")}, {("E", "m", "y", "")})   # self-check: the exemption works only for the named hook ...
if a or b:
    print("FAIL self-check: an exempt super-only hook was reported (%s %s)" % (a, b)); sys.exit(1)
a, b = cmp_super({("E", "m", "x", "")}, {("E", "m", "x", ""), ("E", "m", "z", "")}, {("E", "m", "y", "")})   # ... a template hook missing from super is never hidden
if b != [("E", "m", "z", "")]:
    print("FAIL self-check: a template hook missing from super was hidden (%s %s)" % (a, b)); sys.exit(1)
a, b = cmp_super({("E", "m", "x", ""), ("E", "m", "w", "")}, {("E", "m", "x", "")}, {("E", "m", "y", "")})   # ... and an unlisted super-only hook is still reported
if a != [("E", "m", "w", "")]:
    print("FAIL self-check: an unlisted super-only hook was not reported (%s %s)" % (a, b)); sys.exit(1)
a, b = cmp_super({("E", "m", "x", "")}, {("E", "m", "x", ""), ("E", "m", "y", "")}, {("E", "m", "y", "")})   # ... an exempt hook the template carries but super lacks fails too
if b != [("E", "m", "y", "")]:
    print("FAIL self-check: an exempt hook that only the template carries was hidden (%s %s)" % (a, b)); sys.exit(1)
if os.path.exists(sup):
    s = norm(entries(sup))
    only_super, only_mine = cmp_super(s, mine_e, SUPER_ONLY)
    if only_super or only_mine:
        print("FAIL differs from super .claude/settings.json: only-super %s only-mine %s" % (only_super, only_mine))
        sys.exit(1)
    for k in sorted(set(SUPER_ONLY) & s):
        print("NOTE super-only hook not in the kernel template (exempt): %s %s %s: %s" % (k[0], k[1], k[2], SUPER_ONLY[k]))
    for k in sorted(set(SUPER_ONLY) - s):
        print("FAIL stale exemption: super settings no longer run %s %s %s, remove it from SUPER_ONLY" % k[:3]); sys.exit(1)
    print("ok same hooks as the super settings (plus %d exempt super-only)" % len(set(SUPER_ONLY) & s))
else:
    print("SKIP super settings not present (%s): cross-check not done" % sup)
env = dict(os.environ, CLAUDE_PROJECT_DIR=root, AGENT_GUARD_STATE=os.path.join(tmp, "ag-state"), AGENT_GUARD_OUT=os.path.join(tmp, "ag-out"))
cfg = json.load(open(mine))
fb = "FAKE"
def run(ev, matcher_has, payload, want_deny=None, skip_if_missing=None):
    for m in cfg["hooks"][ev]:
        for h in m["hooks"]:
            if matcher_has not in h["command"]:
                continue
            if skip_if_missing and not os.path.exists(skip_if_missing):
                print("SKIP %s %s: %s absent" % (ev, matcher_has, skip_if_missing)); return
            payload = dict(payload, cwd=root, session_id="s", transcript_path=os.path.join(tmp, "none.jsonl"))
            r = subprocess.run(h["command"], shell=True, input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
            denied = '"permissionDecision": "deny"' in r.stdout
            if r.returncode != 0 or (want_deny is not None and denied != want_deny):
                print("FAIL %s %s rc=%d deny=%s out=%s err=%s" % (ev, matcher_has, r.returncode, denied, r.stdout[:120], r.stderr[:200])); sys.exit(1)
            print("ok %s %s rc=0 deny=%s" % (ev, matcher_has, denied)); return
    print("FAIL no hook %s %s" % (ev, matcher_has)); sys.exit(1)
fb_cmd = {"tool_name": "Bash", "tool_input": {"command": "fastboot flash boot x.img"}}
ok_cmd = {"tool_name": "Bash", "tool_input": {"command": "true"}}
run("PreToolUse", "tools/phone-guard", dict(fb_cmd, hook_event_name="PreToolUse"), True)
run("PreToolUse", "tools/patch-guard", dict(ok_cmd, hook_event_name="PreToolUse"), False)
PYEOF
rc=$?
if [ $rc = 0 ]; then ok; grep -E "^SKIP" "$T/py.out" | sed 's/^/NOTE: /'; else bad "claude-settings.json structure / hook commands run" "$(grep -v '^ok' "$T/py.out" | head -5)"; fi

# ---- reference-transaction: trunk moves only to a formally gated commit (user 2026-11-10) ------------------------------------------------------
rt_try() { # rt_try NAME WANT(0|1) PATTERN [VAR=val ...] : fast-forward trunk to branch rt with the escape unset
	local name=$1 want=$2 pat=$3 rc
	shift 3
	(cd "$K" && env -u PIXOS_UNGATED_TRUNK "$@" git merge -q --ff-only rt) >"$T/out" 2>&1
	rc=$?
	if { [ "$want" = 0 ] && [ $rc != 0 ]; } || { [ "$want" = 1 ] && [ $rc = 0 ]; }; then bad "$name (rc=$rc, wanted $want)" "$(cat "$T/out")"; return; fi
	if [ -n "$pat" ] && ! grep -q -- "$pat" "$T/out"; then bad "$name (no '$pat' in output)" "$(cat "$T/out")"; return; fi
	ok
}
git -C "$K" switch -q trunk && git -C "$K" switch -q -c rt && echo rt1 >"$K/rtfile" && git -C "$K" add rtfile && PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m rt1 && git -C "$K" switch -q trunk
rt_try "ungated fast-forward of trunk refused" 1 "REFUSED trunk"
rt_try "refusal names the escape" 1 "PIXOS_UNGATED_TRUNK"
rt_try "escape with a reason lets trunk move" 0 "WITHOUT a formal gate PASS" PIXOS_UNGATED_TRUNK=why
git -C "$K" switch -q rt && echo rt2 >"$K/rtfile" && git -C "$K" add rtfile && PIXOS_ALLOW_NO_TRAILER=1 git -C "$K" commit -q -m rt2 && mkdir -p "$GATE_PASS_DIR" && : >"$GATE_PASS_DIR/$(git -C "$K" rev-parse HEAD)" && git -C "$K" switch -q trunk
rt_try "gated fast-forward of trunk allowed" 0 ""

# ---- mutants ----------------------------------------------------------------------------------------------------------------------------------
survived=0 killed=0 mut_note=
if [ "${KH_NO_MUTANTS:-}" != 1 ]; then
	if ! unshare -r -m -p -f --mount-proc true >/dev/null 2>&1; then
		mut_note=" mutants=SKIPPED(unshare -r -m -p -f --mount-proc unavailable here: run on a host that has it)"
		echo "NOTE: mutants SKIPPED, unshare -r -m -p -f --mount-proc is unavailable here"
	else
		mkmut() { # mkmut NAME FILE SED-EXPR : copy the tools under test, apply one sed expression to FILE (relative to tools/, or ../.gitignore), prove it changed
			local d=$T/mut/$1
			mkdir -p "$d/tools"
			cp -r "$KH_TOOLS/hooks" "$KH_TOOLS/install-hooks" "$KH_TOOLS/claude-settings.json" "$d/tools/"
			cp "$KH_TOOLS/phone-guard" "$KH_TOOLS/patch-guard" "$KH_TOOLS/agent-guard" "$KH_TOOLS/patch-numbers" "$d/tools/" 2>/dev/null
			cp "$HERE/test_kernel_hooks.sh" "$d/tools/"   # a mutant of the test file itself (M47, M48) runs its own copy
			cp "$ROOT/.gitignore" "$d/.gitignore"
			if [ -n "${2:-}" ]; then
				cp "$d/tools/$2" "$d/before"
				sed -i "$3" "$d/tools/$2"
				if cmp -s "$d/before" "$d/tools/$2"; then echo "ERROR: mutant $1 did not change $2"; return 1; fi
			fi
		}
		runmut() { # runmut NAME : run the test on the mutant tree inside a throwaway namespace
			unshare -r -m -p -f --mount-proc env PHONE_LOCK_DIR="$T/phone.lock" KH_TOOLS="$T/mut/$1/tools" KH_NO_MUTANTS=1 SUPER_REAL="$SUPER_REAL" bash "$T/mut/$1/tools/test_kernel_hooks.sh" >"$T/mut/$1.out" 2>&1
		}
		mkmut M0 "" "" && if runmut M0; then ok; else bad "mutant control M0 (no change) must PASS inside the namespace" "$(tail -5 "$T/mut/M0.out")"; fi
		mut() { # mut NAME FILE SED-EXPR
			if ! mkmut "$1" "$2" "$3"; then bad "mutant $1 could not be built"; return; fi
			if runmut "$1"; then survived=$((survived + 1)); bad "mutant $1 SURVIVED ($2: $3)"
			else killed=$((killed + 1)); echo "killed $1 by: $(grep -m2 '^FAIL' "$T/mut/$1.out" | tr '\n' ' ' | cut -c1-150)"; ok; fi
		}
		mut M1-trunk-guard-off hooks/pre-commit 's/\[ "\$branch" = refs\/heads\/trunk \]/[ "$branch" = refs\/heads\/trunkX ]/'
		mut M2-merge-head-exemption-removed hooks/pre-commit 's/! git rev-parse -q --verify MERGE_HEAD >\/dev\/null 2>&1/true/'
		mut M3-trunk-escape-ignored hooks/pre-commit 's/-n "\${PIXOS_TRUNK_DIRECT:-}"/-n ""/'
		mut M4-trunk-escape-always hooks/pre-commit 's/-n "\${PIXOS_TRUNK_DIRECT:-}"/-z ""/'
		mut M5-patch-numbers-rc3-ignored hooks/pre-commit 's/\[ \$prc = 3 \] && rc=1/[ $prc = 3 ] \&\& rc=0/'
		mut M6-missing-trailer-allowed hooks/commit-msg 's/if \[ -z "\$trailers" \]; then/if false; then/'
		mut M7-other-role-allowed hooks/commit-msg 's/deny "'"'"'\$trailer'"'"' is another registered role"/true/'
		mut M8-label-series-unchecked hooks/commit-msg 's/deny "label /exit 0 # deny "label /'
		mut M9-role-override-ignored hooks/commit-msg 's/if \[ -n "\${PIXOS_TRAILER_ROLE_OVERRIDE:-}" \]; then/if false; then/'
		mut M10-role-override-always hooks/commit-msg 's/if \[ -n "\${PIXOS_TRAILER_ROLE_OVERRIDE:-}" \]; then/if true; then/'
		mut M11-no-trailer-escape-ignored hooks/commit-msg 's/\[ "\${PIXOS_ALLOW_NO_TRAILER:-}" = 1 \] && {/false \&\& {/'
		mut M12-retired-id-unmapped hooks/commit-msg 's/{ id = tolower(\$2);/{ if ($7 == "retired") next; id = tolower($2);/'
		mut M13-skip-loud-removed hooks/commit-msg 's/echo "commit-msg: NOTE trailer role check skipped: \$\*" >&2; exit 0/exit 0/'
		mut M14-check-writes install-hooks 's/elif \[ \$mode = check \]; then say "MISSING core.hooksPath/elif false; then say "MISSING core.hooksPath/'
		mut M15-worktrees-skipped install-hooks 's/"\$main" | "\$wt_dir"\/\*)/"$main")/'
		mut M16-outside-worktree-written install-hooks 's/"\$main" | "\$wt_dir"\/\*)/*)/'
		mut M17-gitignore-unchecked install-hooks 's#if ! git -C "\$wt" check-ignore#if false \&\& git -C "$wt" check-ignore#'
		mut M20-short-ref-tag-bypass hooks/pre-commit 's/symbolic-ref -q HEAD/symbolic-ref -q --short HEAD/; s/= refs\/heads\/trunk \]/= trunk ]/'
		mut M21-only-last-trailer-checked hooks/commit-msg 's/^for trailer in \$trailers; do/for trailer in $(printf "%s\\n" "$trailers" | tail -n 1); do/'
		mut M22-trailer-case-kept hooks/commit-msg "s#sed 's/\[\[:space:\]\]\*\$//' | tr 'A-Z' 'a-z')#sed 's/[[:space:]]*\$//')#"
		mut M23-label-prefix-matches hooks/commit-msg 's/"\$rnum") exit 0 ;;/"$rnum"*) exit 0 ;;/'
		mut M24-registry-header-is-a-role hooks/commit-msg 's/ || \$1 == "role"//g'
		mut M25-hook-exec-bit-unchecked install-hooks 's/\[ -x "\$f" \] || { echo "RESULT: ERROR \$f is not executable"/[ -e "$f" ] || { echo "RESULT: ERROR $f is not executable"/'
		mut M26-symlink-followed install-hooks 's/if \[ -L "\$wt\/.claude" \] || \[ -L "\$dst" \]; then/if false; then/'
		mut M27-no-backup install-hooks 's/\[ -e "\$dst.pre-install-hooks" \] || cp -p "\$dst" "\$dst.pre-install-hooks"/true/'
		mut M28-exclude-not-written install-hooks 's/elif mkdir -p "\$common\/info" \&\& echo/elif false \&\& echo/'
		mut M29-hooks-not-copied install-hooks 's/cp "\$main\/tools\/hooks\/\$h" "\$want\/\$h.new.\$\$"/: > "$want\/$h.new.$$"/'
		mut M30-hook-copy-not-compared install-hooks 's/cmp -s "\$main\/tools\/hooks\/\$h" "\$want\/\$h" \&\& //'
		mut M31-prunable-resurrected install-hooks 's/if \[ ! -d "\$wt" \]; then/if false; then/'
		mut M32-trailing-slash-kept install-hooks 's/^wt_dir=\${wt_dir%\/}$/wt_dir=$wt_dir/'
		mut M33-settings-dir-written-into install-hooks 's/elif \[ -d "\$dst" \]; then/elif false; then/'
		mut M34-failed-copy-not-counted install-hooks 's#else rm -f "\$dst.new.\$\$"; say "FAILED  \$dst"; bad=\$((bad + 1)); fi#else rm -f "$dst.new.$$"; say "FAILED  $dst"; fi#'
		mut M35-ignore-failure-not-counted install-hooks 's#the copy would be committed"; bad=\$((bad + 1))#the copy would be committed"#'
		mut M36-extra-argument-accepted install-hooks 's/^\[ \$# -le 1 \] || .*$/true/'
		mut M37-guard-exit-2-blocking claude-settings.json 's/\[ \$r = 2 \] \&\& exit 1; //g'
		mut M38-missing-script-passes claude-settings.json 's/>\&2; exit 1; }/>\&2; exit 0; }/g'
		mut M39-main-checkout-guard-wrong-path claude-settings.json 's#P=\$HOME/.t3/projects/pixelios/pixos-project/pixelios-kernel/tools/phone-guard;#P=/nonexistent/phone-guard;#'
		mut M41-validate-failure-ignored hooks/pre-merge-commit 's/^exit 1$/exit 0/'
		mut M42-path-filter-removed hooks/pre-merge-commit "s/grep -Eq '\^(tools|docs)\/'/grep -Eq '.'/"
		mut M43-validate-escape-ignored hooks/pre-merge-commit 's/\[ -n "\${PIXOS_NO_VALIDATE_MERGE:-}" \] \&\&/false \&\&/'
		mut M44-validate-escape-always hooks/pre-merge-commit 's/\[ -n "\${PIXOS_NO_VALIDATE_MERGE:-}" \] \&\&/[ -z "" ] \&\&/'
		mut M45-validate-docs-skipped hooks/pre-merge-commit "s/\^(tools|docs)\//^(tools)\//"
		mut M47-exemption-hides-template-hooks test_kernel_hooks.sh 's/return sorted(s - m - set(allow)), sorted(m - s)/return sorted(s - m - set(allow)), []/'
		mut M48-exemption-covers-all-super-only test_kernel_hooks.sh 's/return sorted(s - m - set(allow)), sorted(m - s)/return [], sorted(m - s)/'
		mut M46-autolink-always-skipped hooks/post-checkout 's/^\[ "\$1" = 0000000000000000000000000000000000000000 \] \&\& \[ "\$3" = 1 \] || exit 0$/exit 0/'
		mut M49-ungated-trunk-allowed hooks/reference-transaction 's/^\t\[ -f "\$gp\/\$c" \] \&\& continue$/\tcontinue/'
		mut M50-trunk-check-off hooks/reference-transaction 's/^\[ "\$1" = prepared \] || exit 0$/exit 0/'
		mut M40-no-python-note-silent hooks/pre-commit 's/echo "patch-numbers: python3 not found, patch numbers NOT checked for this commit" >\&2/true/'
	fi
fi
if [ $fails = 0 ]; then echo "RESULT: PASS cases=$cases mutants_killed=$killed mutants_survived=$survived$mut_note"; exit 0; fi
echo "RESULT: FAIL cases=$cases failed=$fails mutants_killed=$killed mutants_survived=$survived$mut_note"
exit 1
