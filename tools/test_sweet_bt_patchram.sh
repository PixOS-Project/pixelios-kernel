#!/bin/bash
# Host test of the stock BCM.hcd download (patch 0218, T-86c9): the REAL bsd/dev/sweet_bt_uart.c (patches 0189 + 0213 + 0215 + 0216 + 0217 + 0218 applied in order) against stub kernel
# headers and a fake BCM4389 (tools/test_sweet_bt_patchram.c). Prints the C test's RESULT line; exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
#   tools/test_sweet_bt_patchram.sh                      synthetic image (CI)
#   T_HCD=/path/BCM.hcd T_VND=/path/btsnoop_hci_vnd.log tools/test_sweet_bt_patchram.sh   also compares the stream with the golden vendor snoop (both stay outside git)
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0215-bt-keep-baud 0216-bt-le-probe 0217-bt-replay-probe 0219-bt-replay-v3 0221-bt-sweep-2007-position 0223-bt-rw200c 0225-bt-nopadv 0226-bt-pubscan 0228-bt-patchram; do
	git apply --include='bsd/dev/sweet_bt_uart.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
cp "$W.apply/bsd/dev/sweet_bt_uart.c" "$W/sweet_bt_uart.c"
cp tools/test_sweet_bt_patchram.c tools/test_sweet_bt_hcdimg.c "$W/"
cd "$W"
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation'
gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -c test_sweet_bt_hcdimg.c -o hcdimg.o 2>&1 | grep -v 'note:\|^ *[0-9]* |' && { echo "RESULT: ERROR compile hcdimg"; exit 2; }
gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_patchram.c hcdimg.o -o tpr 2>&1 | grep -v 'note:\|^ *[0-9]* |' && { echo "RESULT: ERROR compile"; exit 2; }
./tpr | grep -a "${T_GREP:-FAIL\|^RESULT\|^NOTE}"
exit "${PIPESTATUS[0]}"
