#!/bin/bash
# test_golden6b_swipes: host check of the swipe rules of the golden-6b session script (run 2 abort, docs/runplans/golden-6b.md "Run 2"):
#   - every `swipe X1 Y1 X2 Y2 MS` is upward (Y1 > Y2): a DOWN swipe on the home screen opens the notification shade ("USB debugging connected" -> ui guard abort) [obs]
#   - the drawer-open swipe starts inside the hotseat ([0,1905][1080,2400] [obs ui-guard.xml]) and above the gesture bar: 1905 <= Y1 <= 2300
#   - all coordinates on screen 1080x2400, MS <= 2000
# Usage: tools/test_golden6b_swipes.sh [SCRIPT]   (default docs/golden/golden-6b.sh). Ends with RESULT: PASS|FAIL. Host only.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
S=${1:-$(dirname "$0")/../docs/golden/golden-6b.sh}
bad=0; n=0; opens=0
fail() { echo "FAIL: $*"; bad=$((bad + 1)); }
while read -r x1 y1 x2 y2 ms; do
	n=$((n + 1))
	[ "$y1" -gt "$y2" ] || fail "downward/flat swipe: $x1 $y1 $x2 $y2 $ms"
	for v in $x1 $x2; do [ "$v" -ge 0 ] && [ "$v" -le 1080 ] || fail "x off screen: $x1 $y1 $x2 $y2 $ms"; done
	for v in $y1 $y2; do [ "$v" -ge 0 ] && [ "$v" -le 2400 ] || fail "y off screen: $x1 $y1 $x2 $y2 $ms"; done
	[ "$ms" -le 2000 ] || fail "MS > 2000: $ms"
	if [ "$y1" -ge 1905 ]; then opens=$((opens + 1)); [ "$y1" -le 2300 ] || fail "drawer-open swipe in the gesture bar: $y1"; fi
done < <(grep -E '^[[:space:]]+swipe [0-9]' "$S" | sed -E 's/#.*//' | grep -oE 'swipe [0-9]+ [0-9]+ [0-9]+ [0-9]+ [0-9]+' | sed 's/^swipe //')
[ "$n" -ge 4 ] || fail "only $n swipes found (parser broke?)"
[ "$opens" -ge 1 ] || fail "no drawer-open swipe from the hotseat (y1 >= 1905)"
if [ "$bad" = 0 ]; then echo "RESULT: PASS test_golden6b_swipes swipes=$n"; exit 0; fi
echo "RESULT: FAIL test_golden6b_swipes bad=$bad"; exit 1
