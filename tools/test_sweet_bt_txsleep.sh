#!/bin/bash
# Host test of patch 0360 (BT patchram TX wait sleeps one tick instead of spinning on UFSTAT TXFULL, bring-up thread only): the REAL bsd/dev/sweet_bt.c + sweet_bt_uart.c (patches 0189 .. 0360 applied in
# order) on the lefeat harness with a 256-byte TX FIFO model (tools/test_sweet_bt_txsleep.c), then one mutant per property (each must FAIL). Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0215-bt-keep-baud 0216-bt-le-probe 0217-bt-replay-probe 0219-bt-replay-v3 0221-bt-sweep-2007-position 0223-bt-rw200c 0225-bt-nopadv 0226-bt-pubscan 0228-bt-patchram 0229-bt-rfwit 0230-bt-rfwit2 0231-bt-nopcf 0232-bt-pubscan-active 0274-bt-lefeat 0277-bt-romread 0285-bt-vsc-adapter 0286-bt-skywalk-channel 0295-bt-defaults-on 0296-bt-async-bringup 0360-bt-tx-sleep; do
	git apply --include='bsd/dev/sweet_bt.c' --include='bsd/dev/sweet_bt.h' --include='bsd/dev/sweet_bt_uart.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
cp tools/test_sweet_bt_txsleep.c tools/test_sweet_bt_lefeat.c tools/test_sweet_bt_hcdimg.c "$W/"
sed -i 's/struct sockaddr_ctl { u_int32_t sc_unit; };/struct sockaddr_ctl { u_int32_t sc_id, sc_unit; };/' "$W/kstub.h"
sed -i 's/^#endif$/#define CTL_FLAG_REG_SOCK_STREAM 0x4\nvoid proc_selfname(char *, int);\n#endif/' "$W/kstub.h"
cd "$W"
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation -Wno-unused-function'
gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -c test_sweet_bt_hcdimg.c -o hcdimg.o 2>&1 | grep -v 'note:\|^ *[0-9]* |' && { echo "RESULT: ERROR compile hcdimg"; exit 2; }
build() {       # $1 = sweet_bt_uart.c, $2 = binary
	cp "$W.apply/bsd/dev/sweet_bt.c" sweet_bt.c; cp "$1" sweet_bt_uart.c; cp "$W.apply/bsd/dev/sweet_bt.h" sweet_bt.h
	gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_txsleep.c hcdimg.o -o "$2" > build.log 2>&1 || { grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; }
	if grep -v 'note:\|^ *[0-9]* |' build.log | grep -q .; then grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; fi
	return 0
}
UA="$W.apply/bsd/dev/sweet_bt_uart.c"
build "$UA" tts || { echo "RESULT: ERROR compile"; exit 2; }
res=$(timeout 120 ./tts 2>&1 | grep -a "^FAIL\|^RESULT") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$res"
case "$res" in *"RESULT: PASS sweet-bt-txsleep-host"*) ;; *) exit 1 ;; esac
# mutants: name|sed expression on sweet_bt_uart.c; each must make the test FAIL
M=(
	'never-sleeps|s/(void)tsleep(\&chan, PZERO, "sweetbttx", 1);/;/'
	'flag-ignored|s/const bool sl = sbu_txsleep_ok \&\& /const bool sl = /'
	'flag-never-set|s/sbu_txsleep_ok = inline_rx; /;/'
	'flag-never-cleared|s/^\tsbu_txsleep_ok = false;$/\t;/'
	'baud-limit-off|s/return baud != 0 \&\& baud \/ 10u \/ SBU_TICK_HZ <= SBU_FIFO_DEPTH \/ 2u;/return baud != 0;/'
	'no-stall-bound|s/if (++slept > SBU_TXSLEEP_MAX) {/if (++slept > 100000000u) {/'
	'wrong-register|s/(sbu_rd(SBU_UART + U_UFSTAT) \& SBU_UFSTAT_TXFULL) == 0) {/(sbu_rd(SBU_UART + U_UTRSTAT) \& SBU_UFSTAT_TXFULL) == 0) {/'
	'no-totals-row|s/bring-up tx fifo-full waits/bring-up tx waits/'
)
fail=0
for m in "${M[@]}"; do
	IFS='|' read -r name expr <<<"$m"
	sed "$expr" "$UA" > mutant.c; cmp -s mutant.c "$UA" && { echo "MUTANT $name: ERROR sed changed nothing"; fail=1; continue; }
	if build mutant.c tmut >/dev/null 2>&1 && timeout 120 ./tmut 2>&1 | grep -aq 'RESULT: PASS'; then echo "MUTANT $name: SURVIVED"; fail=1; else echo "MUTANT $name: killed"; fi
done
[ $fail = 0 ] && echo "RESULT: PASS sweet-bt-txsleep-mutants ${#M[@]}/${#M[@]} killed" || { echo "RESULT: FAIL sweet-bt-txsleep-mutants"; exit 1; }
