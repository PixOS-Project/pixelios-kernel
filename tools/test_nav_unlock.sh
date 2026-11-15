#!/bin/bash
# T-b34d oracle (T0): the unlock decision of xnu patch 0260 (accepted = Preferences exec'd in the window, or >= 20 distinct frames; swaps >= 30 only when sweetfps is off).
# The three decision lines are cut out of the patch itself (from `uint64_t d = sweet_fb_swap_count - self->fNavSw0, dd` through the `bool unlocked` line), so the test cannot drift.
# Cases come from the runs: nav15 rep 0 (164 swaps, 0 distinct, no exec) must NOT be accepted; nav14/nav13 final window (100+ distinct) accepted; Preferences exec alone accepted.
# Expected: RESULT: PASS nav-unlock cases=9
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
p=$here/xnu/patches/0260-sweet-nav-unlock-distinct-display-state.patch
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
awk '/^\+\t\tuint64_t d = sweet_fb_swap_count - self->fNavSw0, dd/{on=1} on{sub(/^\+/,""); print} on && /bool unlocked/{exit}' "$p" >"$d/block.inc"
[ "$(wc -l <"$d/block.inc")" = 3 ] || { echo "RESULT: ERROR nav-unlock could not cut the 3 decision lines out of $p"; exit 2; }
cat >"$d/t.cpp" <<'CPP'
#include <cstdint>
#include <cstdio>
static uint64_t sweet_fb_swap_count, sweet_fb_distinct_count;
static uint32_t sweet_exec_pref_count;
static bool live;
static bool sweet_fb_distinct_live(void) { return live; }
struct S { uint64_t fNavSw0, fNavDs0; uint32_t fNavPref0; } st, *self = &st;
static int fails;
static void run(const char *what, bool lv, uint64_t sw, uint64_t ds, bool exec, bool want) {
	st.fNavSw0 = 100; st.fNavDs0 = 50; st.fNavPref0 = 7;
	sweet_fb_swap_count = 100 + sw; sweet_fb_distinct_count = 50 + ds; sweet_exec_pref_count = 7 + (exec ? 1 : 0); live = lv;
#include "block.inc"
	if (unlocked != want) { printf("FAIL: %s (got %d want %d)\n", what, (int)unlocked, (int)want); fails++; }
	(void)d; (void)dd;
}
int main() {
	run("nav15 rep0: 164 swaps, 0 distinct, no exec", true, 164, 0, false, false);
	run("nav14 retry window: 0 swaps", true, 0, 0, false, false);
	run("nav13 real unlock: 150 swaps 120 distinct", true, 150, 120, false, true);
	run("19 distinct is below the line", true, 150, 19, false, false);
	run("20 distinct is the line", true, 40, 20, false, true);
	run("Preferences exec with 0 distinct", true, 0, 0, true, true);
	run("no sweetfps: 29 swaps", false, 29, 0, false, false);
	run("no sweetfps: 30 swaps", false, 30, 0, false, true);
	run("no sweetfps: distinct ignored", false, 5, 500, false, false);
	if (fails) { printf("RESULT: FAIL nav-unlock fails=%d\n", fails); return 1; }
	printf("RESULT: PASS nav-unlock cases=9\n");
	return 0;
}
CPP
g++ -std=c++17 -Wall -Wextra -Wno-unused-variable -I"$d" -o "$d/t" "$d/t.cpp"
"$d/t"
