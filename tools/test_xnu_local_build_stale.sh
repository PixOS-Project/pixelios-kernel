#!/bin/bash
# Regression test for the wlan-wui-f stale kernel (2026-11-08): a lab-host work tree that was copied from another label, then a
# change to a header that only patch-applied code uses (sweet_wlan_l25_hs.h, patch 0206), built incrementally. The old tool
# synced the new stack into the copy, but XNU's generated Makefiles (SOURCE_DIR) and every .d file still pointed at the tree it was
# copied from, so make compiled THAT tree's old sources and the build said PASS.
#   tools/test_xnu_local_build_stale.sh [--tool FILE] [--keep]
#   --tool FILE  the xnu-local-build to test (default: tools/xnu-local-build); an older version is run from a scratch copy of tools/
#   --keep       keep the scratch labels (xlbt-*) under $LOCALBUILD/work afterwards
# Case A (the wui-f case): seed label built from the current stack, `cp -a` to a second label, header-only change, incremental build.
#   The tool passes the case when the kernel it reports has the changed string, or when it FAILs loudly; a PASS with the old
#   string (a stale kernel) fails the test.
# Case B (the guard): the same as A with the tree reset turned off (XLB_TEST_NO_TREE_RESET=1, only when --tool knows it): the
#   stale-object guard must FAIL the build and leave no kernel in out/<label>.
# Real XNU builds on this host (XNU_BUILD_WHERE=local, about 4 min, each build takes the heavy-job lock itself). Exit 0 PASS, 1 FAIL, 2 ERROR.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
case "${1:-}" in -h|--help) sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.." || exit 2
ROOT=$PWD; tool=$ROOT/tools/xnu-local-build; keep=0
while [ $# -gt 0 ]; do case $1 in --tool) tool=$(readlink -f "$2"); shift 2 ;; --keep) keep=1; shift ;; *) echo "RESULT: ERROR unknown option $1"; exit 2 ;; esac; done
[ -f "$tool" ] || { echo "RESULT: ERROR no tool $tool"; exit 2; }
A=$(realpath -m -- "${ARTIFACTS:-$HOME/pixelios-artifacts}"); LB=${LOCALBUILD:-$A/localbuild}
T=$(mktemp -d "${TMPDIR:-/tmp}/xlbt.XXXXXX") || exit 2
tag=$(sha256sum "$tool" | cut -c1-6); seed=xlbt-$tag-seed; cpy=xlbt-$tag-copy; cpy2=xlbt-$tag-guard
cleanup() { rm -rf -- "$T"; [ $keep = 1 ] || rm -rf -- "$LB/work/$seed" "$LB/work/$cpy" "$LB/work/$cpy2" "$LB/out/$seed" "$LB/out/$cpy" "$LB/out/$cpy2"; }
trap cleanup EXIT
# the tool under test runs from a scratch tools/ dir: every other tool is the current one (symlinks), xnu-local-build is --tool
mkdir -p "$T/root/tools"; for f in "$ROOT"/tools/*; do ln -s "$f" "$T/root/tools/"; done; rm -f "$T/root/tools/xnu-local-build"; cp "$tool" "$T/root/tools/xnu-local-build"; chmod +x "$T/root/tools/xnu-local-build"
XLB=$T/root/tools/xnu-local-build
# stacks: S0 = the committed stack, S1 = S0 with one string changed inside patch 0206's sweet_wlan_l25_hs.h (header-only, patch-applied code)
mkdir -p "$T/s0" "$T/s1"; git archive HEAD xnu/patches | tar -x -C "$T/s0" --strip-components=2 || { echo "RESULT: ERROR git archive"; exit 2; }
cp -a "$T/s0/." "$T/s1/"; tok="L25 live link XLBT$(date +%s)"
p=$T/s1/0206-sweet-wlan-l25-headers.patch
grep -q '^+.*L25 live link' "$p" || { echo "RESULT: ERROR patch 0206 has no 'L25 live link' line (test needs a new marker line)"; exit 2; }
sed -i "s/L25 live link/$tok/" "$p"
grep -q '^+++ b/bsd/dev/sweet_wlan_l25_hs.h' "$p" || { echo "RESULT: ERROR 0206 no longer adds sweet_wlan_l25_hs.h"; exit 2; }
b() { XNU_BUILD_WHERE=local "$XLB" "$@"; }
fail=0; say() { echo "$*"; }
say "test: tool $tool (sha256 ${tag}), token '$tok'"
b --stack "$T/s0" --label "$seed" >"$T/seed.log" 2>&1 || { tail -5 "$T/seed.log"; echo "RESULT: ERROR seed build failed"; exit 2; }
# Case A
rm -rf -- "$LB/work/$cpy"; cp -a "$LB/work/$seed" "$LB/work/$cpy"
b --stack "$T/s1" --label "$cpy" >"$T/a.log" 2>&1; arc=$?
n=$(strings -a "$LB/out/$cpy/kernel.development.sweet" 2>/dev/null | grep -c "$tok")
if [ $arc -eq 0 ] && [ "$n" -gt 0 ]; then say "case A PASS: build PASS and the kernel has the changed string ($n hits)$(grep -q 'XNU TREE RESET' "$T/a.log" && echo '; tree reset seen')"
elif [ $arc -ne 0 ]; then say "case A PASS: the build FAILED loudly (rc $arc): $(grep -m1 '^RESULT' "$T/a.log" | cut -c1-200)"
else say "case A FAIL: STALE KERNEL: the build said PASS but the kernel lacks the changed string (the wui-f bug)"; fail=1; fi
# Case B
if grep -q XLB_TEST_NO_TREE_RESET "$XLB"; then
	rm -rf -- "$LB/work/$cpy2"; cp -a "$LB/work/$seed" "$LB/work/$cpy2"
	XLB_TEST_NO_TREE_RESET=1 b --stack "$T/s1" --label "$cpy2" >"$T/b.log" 2>&1; brc=$?
	if [ $brc -eq 1 ] && grep -q 'stale-object guard' "$T/b.log" && [ ! -e "$LB/out/$cpy2/kernel.development.sweet" ]; then
		say "case B PASS: the guard failed the build: $(grep -m1 'XNU BUILD GUARD' "$T/b.log" | cut -c1-200)"; grep -m3 '^  F' "$T/b.log" | cut -c1-220
	else say "case B FAIL: guard did not catch the foreign tree (rc $brc): $(grep -m1 '^RESULT' "$T/b.log" | cut -c1-200)"; fail=1; fi
else say "case B SKIP: this tool has no XLB_TEST_NO_TREE_RESET (no guard)"; fi
[ $fail = 0 ] && { echo "RESULT: PASS test_xnu_local_build_stale tool=$tag"; exit 0; }
echo "RESULT: FAIL test_xnu_local_build_stale tool=$tag"; exit 1
