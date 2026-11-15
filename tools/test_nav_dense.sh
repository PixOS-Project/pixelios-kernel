#!/bin/bash
# T-3c9c oracle (T0): the dense 64x64 witness of xnu patch 0247 on synthetic surfaces. The block under test is cut out of the patch itself
# (from `if (fps_dense) {` to the first context line), so the test cannot drift from the patch. Expected: RESULT: PASS nav-dense cases=8
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
p=$here/xnu/patches/0247-sweet-nav-witness.patch
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
awk '/^\+\t\tif \(fps_dense\) \{/{on=1} on && !/^\+/{exit} on{sub(/^\+/,""); print}' "$p" >"$d/block.inc"
[ -s "$d/block.inc" ] || { echo "RESULT: ERROR nav-dense could not cut the dense block out of $p"; exit 2; }
cat >"$d/t.cpp" <<'CPP'
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <algorithm>
#define IOLog printf
struct Surf { std::vector<uint8_t> mem; uint32_t offset, bytesPerRow, width, height; uintptr_t kernelAddress() { return (uintptr_t)mem.data(); } };
static uint32_t n_dist4k, n_small, dense_prev[64 * 64], n_swaps;
static bool dense_have, dense_gap_said;
static int fps_dense = 1;
static void swap_block(Surf *s) {
#include "block.inc"
}
static Surf mk(uint32_t w, uint32_t h) { Surf s; s.width = w; s.height = h; s.offset = 0; s.bytesPerRow = w * 4; s.mem.assign((size_t)w * h * 4, 0); return s; }
static void px(Surf &s, uint32_t x, uint32_t y, uint32_t v) { memcpy(&s.mem[(size_t)y * s.bytesPerRow + (size_t)x * 4], &v, 4); }
static int fails;
#define CHECK(c, what) do { if (!(c)) { printf("FAIL: %s\n", what); fails++; } } while (0)
int main() {
	Surf s = mk(1082, 2400); std::fill(s.mem.begin(), s.mem.end(), 0x7f);   /* non-zero: the first swap differs from the zeroed dense_prev, so only dense_have keeps it out of the count */
	swap_block(&s); CHECK(n_dist4k == 0 && n_small == 0, "first swap has no previous: not counted");
	swap_block(&s); CHECK(n_dist4k == 0, "identical surface: not distinct");
	/* a change at a sampled point: cell centre of gx=10, gy=20 = x (21*1081/128), y (41*2399/128) */
	uint32_t x = (uint64_t)21 * 1081 / 128, y = (uint64_t)41 * 2399 / 128;
	px(s, x, y, 0xdeadbeef); swap_block(&s); CHECK(n_dist4k == 1 && n_small == 1, "one sampled sample changed: distinct4k 1, small_chg 1");
	px(s, x, y, 0x11111111); px(s, (uint64_t)3 * 1081 / 128, y, 0x22222222); swap_block(&s); CHECK(n_dist4k == 2 && n_small == 2, "two samples changed: small_chg counts <= 2");
	for (uint32_t i = 0; i < 3; i++) px(s, (uint64_t)(2 * i + 1) * 1081 / 128, y, 0x55550000 + i);
	swap_block(&s); CHECK(n_dist4k == 3 && n_small == 2, "3 samples changed: distinct4k only, not small_chg");
	for (uint32_t i = 0; i < 6; i++) px(s, (uint64_t)(2 * i + 1) * 1081 / 128, y, 0x33330000 + i);
	swap_block(&s); CHECK(n_dist4k == 4 && n_small == 2, "6 samples changed: distinct4k only");
	/* a change on the OLD 32x32 grid (x = gx*(w-1)/31) that is not a dense sample must not count: pick a pixel on the old grid whose x,y are not dense points */
	uint32_t ox = (uint64_t)5 * 1081 / 31, oy = (uint64_t)7 * 2399 / 31;
	px(s, ox, oy, 0x44444444); uint32_t before = n_dist4k; swap_block(&s);
	CHECK(n_dist4k == before, "a pixel outside the dense sample set (but on the old 32x32 grid) is invisible to the dense grid by design");
	Surf t = mk(40, 40); swap_block(&t); CHECK(dense_gap_said, "surface < 64 is a loud GAP");
	printf(fails ? "RESULT: FAIL nav-dense fails=%d\n" : "RESULT: PASS nav-dense cases=8\n", fails);
	return fails != 0;
}
CPP
c++ -std=c++17 -O0 -I"$d" -o "$d/t" "$d/t.cpp" 2>"$d/cc.err" || { cat "$d/cc.err"; echo "RESULT: ERROR nav-dense compile failed"; exit 2; }
"$d/t"
