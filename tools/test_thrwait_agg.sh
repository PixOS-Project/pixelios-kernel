#!/bin/bash
# T-10ba oracle (T0): the SWEET THRWAIT aggregation (table, hash, top-24 selection) of xnu patch NNNN-sweet-nav-tap-thrwait on synthetic observations. The block under test
# is cut out of the patch itself (between the THW-AGG-BEGIN and THW-AGG-END comment lines of bsd/dev/sweet_thrwait.c), so the test cannot drift from the patch.
# Not covered (needs the kernel): the thread walk in osfmk/kern/exception.c, the locking, the printf rows. THRWAIT_PATCH=FILE overrides the patch path.
# Expected: RESULT: PASS thrwait-agg cases=12
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
p=${THRWAIT_PATCH:-}
if [ -z "$p" ]; then
	set -- "$here"/xnu/patches/[0-9][0-9][0-9][0-9]-sweet-nav-tap-thrwait.patch
	[ -f "$1" ] || { echo "RESULT: ERROR thrwait-agg no xnu/patches/NNNN-sweet-nav-tap-thrwait.patch (set THRWAIT_PATCH)"; exit 2; }
	p=$1
fi
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
awk '/^\+\/\* THW-AGG-BEGIN/{on=1} on{sub(/^\+/,""); print} /THW-AGG-END/{exit}' "$p" >"$d/agg.inc"
[ "$(wc -l <"$d/agg.inc")" -gt 20 ] || { echo "RESULT: ERROR thrwait-agg could not cut the aggregation block out of $p"; exit 2; }
cat >"$d/t.c" <<'CPP'
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define bzero(p, n) memset((p), 0, (n))
#define THW_TAB 512
#define THW_TOP 24
struct thw_ent {
	uint64_t tid, ev, pc, lr;
	uint32_t hint;
	int32_t  pid;
	int32_t  idx;
	uint32_t n;
};
#include "agg.inc"
static int fails;
#define CHECK(c, what) do { if (!(c)) { printf("FAIL: %s\n", what); fails++; } } while (0)
static struct thw_ent mk(int pid, uint64_t tid, uint32_t hint, uint64_t ev, uint64_t pc, uint64_t lr) {
	struct thw_ent e; memset(&e, 0, sizeof(e)); e.pid = pid; e.tid = tid; e.hint = hint; e.ev = ev; e.pc = pc; e.lr = lr; return e;
}
int main(void) {
	uint32_t top[THW_TOP];
	struct thw_ent a = mk(100, 7, 2, 0xdead, 0x1000, 0x2000);
	CHECK(thw_add(&a) == 0 && thw_used == 1, "first observation takes a slot");
	CHECK(thw_add(&a) == 0 && thw_used == 1 && thw_top(top) == 1 && thw_tab[top[0]].n == 2, "same key: n 2, still one entry");
	struct thw_ent b = a; b.pc = 0x1004;
	struct thw_ent c = a; c.hint = 3;
	struct thw_ent d = a; d.tid = 8;
	struct thw_ent e = a; e.pid = 101;
	struct thw_ent f = a; f.ev = 0xbeef;
	struct thw_ent g = a; g.lr = 0x2004;
	thw_add(&b); thw_add(&c); thw_add(&d); thw_add(&e); thw_add(&f); thw_add(&g);
	CHECK(thw_used == 7, "each key field (pc, hint, tid, pid, ev, lr) separates entries");
	thw_clear();
	CHECK(thw_used == 0 && thw_dropped == 0 && thw_top(top) == 0, "clear empties the table and the dropped count");
	/* fill the table with 512 distinct keys, the 513th is dropped and counted, an existing key still counts */
	for (uint64_t i = 0; i < THW_TAB; i++) { struct thw_ent x = mk(1, i, 0, 0, 0, 0); if (thw_add(&x) != 0) { printf("FAIL: slot %llu refused early\n", (unsigned long long)i); fails++; } }
	CHECK(thw_used == THW_TAB, "512 distinct keys fill the table");
	struct thw_ent extra = mk(1, 9999, 0, 0, 0, 0);
	CHECK(thw_add(&extra) == 1 && thw_dropped == 1 && thw_used == THW_TAB, "513th distinct key is dropped and counted, not silent");
	struct thw_ent again = mk(1, 5, 0, 0, 0, 0);
	CHECK(thw_add(&again) == 0 && thw_dropped == 1, "an existing key still counts in a full table");
	/* in a full table every lookup probes every slot, so a key that differs from an existing one in ONE field must be dropped: this pins each field of the comparison, whatever the hash does */
	struct thw_ent v[5] = { mk(2, 5, 0, 0, 0, 0), mk(1, 5, 1, 0, 0, 0), mk(1, 5, 0, 1, 0, 0), mk(1, 5, 0, 0, 1, 0), mk(1, 5, 0, 0, 0, 1) };
	int refused = 0;
	for (int i = 0; i < 5; i++) refused += thw_add(&v[i]);
	CHECK(refused == 5 && thw_dropped == 6, "full table: a key differing in pid / hint / ev / pc / lr alone is not merged into an existing entry");
	thw_clear();
	CHECK(thw_dropped == 0 && thw_used == 0, "clear after drops resets the dropped count");
	/* top: at most 24 rows, highest n first, equal n in slot order; 20 pseudo-random count sets (slot order is the hash order, so every insertion order is exercised) */
	thw_clear();
	for (uint64_t i = 0; i < 40; i++) { struct thw_ent x = mk(1, i, 0, 0, 0, 0); for (uint64_t k = 0; k <= i; k++) thw_add(&x); }   /* tid i observed i+1 times */
	int nt = thw_top(top);
	int ok = nt == THW_TOP;
	for (int i = 0; ok && i < nt; i++) ok = thw_tab[top[i]].n == 40 - (uint32_t)i;
	CHECK(ok, "40 entries: the 24 highest, n 40 down to 17, in order");
	ok = 1;
	for (uint32_t trial = 0; ok && trial < 20; trial++) {
		uint32_t want[60], m = 60;
		thw_clear();
		for (uint32_t i = 0; i < m; i++) {
			struct thw_ent x = mk(1, 1000 * trial + i, 0, 0, 0, 0);
			want[i] = 1 + (i * 37 + trial * 11 + (i * i) % 13) % 50;
			for (uint32_t k = 0; k < want[i]; k++) thw_add(&x);
		}
		for (uint32_t i = 0; i < m; i++) for (uint32_t j = i + 1; j < m; j++) if (want[j] > want[i]) { uint32_t t = want[i]; want[i] = want[j]; want[j] = t; }
		nt = thw_top(top);
		ok = nt == THW_TOP;
		for (int i = 0; ok && i < nt; i++) ok = thw_tab[top[i]].n == want[i] && (i == 0 || thw_tab[top[i - 1]].n > thw_tab[top[i]].n || top[i - 1] < top[i]);
	}
	CHECK(ok, "20 pseudo-random sets of 60 entries: the top 24 n values equal the sorted ones, ties in slot order");
	thw_clear();
	for (uint64_t i = 0; i < 5; i++) { struct thw_ent x = mk(1, i, 0, 0, 0, 0); thw_add(&x); thw_add(&x); }
	nt = thw_top(top);
	ok = nt == 5;
	for (int i = 1; ok && i < nt; i++) ok = top[i - 1] < top[i] && thw_tab[top[i]].n == 2;
	CHECK(ok, "5 equal entries: all listed, slot order");
	printf(fails ? "RESULT: FAIL thrwait-agg fails=%d\n" : "RESULT: PASS thrwait-agg cases=12\n", fails);
	return fails != 0;
}
CPP
cc -std=c11 -Wall -Wextra -Werror -O0 -I"$d" -o "$d/t" "$d/t.c" 2>"$d/cc.err" || { cat "$d/cc.err"; echo "RESULT: ERROR thrwait-agg compile failed"; exit 2; }
"$d/t"
