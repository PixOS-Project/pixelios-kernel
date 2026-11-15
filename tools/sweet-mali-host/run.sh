#!/bin/bash
# T-34eb host harness driver: builds sweet_mali_host.c against the patched bsd/dev/sweet_mali.c and runs the cases; --mutants runs the three spec mutants on a temp copy.
# SWEET_MALI_SRC = directory holding bsd/dev/sweet_mali.c (+ sweet_mali.h); default $HOME/pixelios-artifacts/scratch/t34eb-k
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
src_root=${SWEET_MALI_SRC:-$HOME/pixelios-artifacts/scratch/t34eb-k}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

build() { # build SRC_ROOT OUT
	gcc -std=gnu11 -O1 -g -Wall -Werror -DARM64_BOARD_CONFIG_SWEET -include "$here/stub/xnu_stub.h" -I"$here/stub" \
		-DSM_SRC="\"$1/bsd/dev/sweet_mali.c\"" -o "$2" "$here/sweet_mali_host.c"
}

if [ ! -f "$src_root/bsd/dev/sweet_mali.c" ]; then
	echo "RESULT: FAIL sweet-mali-host missing $src_root/bsd/dev/sweet_mali.c"
	exit 1
fi

if [ "${1:-}" != "--mutants" ]; then
	echo "using driver source: $src_root/bsd/dev/sweet_mali.c"
	if ! build "$src_root" "$work/host"; then
		echo "RESULT: FAIL sweet-mali-host build error"
		exit 1
	fi
	"$work/host"
	exit $?
fi

echo "using driver source: $src_root/bsd/dev/sweet_mali.c (mutants are applied to a temp copy)"
# Baseline first: a mutant is killed only by a case that FAILs under the mutant and does not FAIL on the unmutated source (a case that fails anyway proves nothing).
if ! build "$src_root" "$work/host_base"; then
	echo "RESULT: FAIL mutants killed=0/3 (baseline build error)"
	exit 1
fi
base_fails=$("$work/host_base" 2>&1 | grep '^FAIL ' | cut -d: -f1 | sort)
echo "baseline failing cases: ${base_fails:-none}" | tr '\n' ' '; echo
killed=0
for m in a b c; do
	d="$work/mut_$m"
	mkdir -p "$d/bsd/dev"
	cp "$src_root/bsd/dev/sweet_mali.c" "$src_root/bsd/dev/sweet_mali.h" "$d/bsd/dev/"
	f="$d/bsd/dev/sweet_mali.c"
	case $m in
	a) sed -i 's/ && sm\.busy_slot == 0 && !ov\.on/ \&\& !ov.on/' "$f" ;;                                 # drop "sm.busy_slot == 0 &&" in sm_ov_ok
	b) sed -i '/K3: F finished first/,/sm\.seq_done = sm\.busy_seq;/s/sm\.seq_done = sm\.busy_seq;/sm.seq_done = ov.seq;/' "$f" ;;   # finish F-first branch
	c) sed -i '/MMU fault with overlapped job seq=/{n;d}' "$f" ;;                                         # delete the JS1 HARD_STOP write in check_job's MMU branch
	esac
	if cmp -s "$src_root/bsd/dev/sweet_mali.c" "$f"; then
		echo "MUTANT $m: FAIL sed changed nothing"
		continue
	fi
	diff "$src_root/bsd/dev/sweet_mali.c" "$f" | sed 's/^/    /' | head -6
	if ! build "$d" "$work/host_$m"; then
		echo "MUTANT $m: not killed (build error is not a kill)"
		continue
	fi
	out=$("$work/host_$m" 2>&1)
	rc=$?
	mut_fails=$(printf '%s\n' "$out" | grep '^FAIL ' | cut -d: -f1 | sort)
	new_fails=$(comm -13 <(printf '%s\n' "$base_fails") <(printf '%s\n' "$mut_fails") | grep -v '^$' | tr '\n' ' ')
	if [ "$rc" -ne 0 ] && [ -n "$new_fails" ]; then
		echo "MUTANT $m: killed by: $new_fails"
		killed=$((killed + 1))
	else
		echo "MUTANT $m: SURVIVED (rc=$rc, no case failing beyond the baseline)"
	fi
done
if [ "$killed" -eq 3 ]; then
	echo "RESULT: PASS mutants killed=3/3"
	exit 0
fi
echo "RESULT: FAIL mutants killed=$killed/3"
exit 1
