#!/bin/bash
# Host test of patch 0296 (BT bring-up in a kernel thread): the REAL bsd/dev/sweet_bt.c + sweet_bt_uart.c (patches 0189 .. 0296 applied in order) against stub kernel headers and the
# fake BCM4389 of tools/test_sweet_bt_lefeat.c (tools/test_sweet_bt_async.c). Attach returns before a command is sent, connect during the bring-up is refused, the command sequence
# is byte-identical to the synchronous one; then one mutant per property (each must FAIL). Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0215-bt-keep-baud 0216-bt-le-probe 0217-bt-replay-probe 0219-bt-replay-v3 0221-bt-sweep-2007-position 0223-bt-rw200c 0225-bt-nopadv 0226-bt-pubscan 0228-bt-patchram 0229-bt-rfwit 0230-bt-rfwit2 0231-bt-nopcf 0232-bt-pubscan-active 0274-bt-lefeat 0277-bt-romread 0285-bt-vsc-adapter 0286-bt-skywalk-channel 0295-bt-defaults-on 0296-bt-async-bringup; do
	git apply --include='bsd/dev/sweet_bt.c' --include='bsd/dev/sweet_bt.h' --include='bsd/dev/sweet_bt_uart.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
cp tools/test_sweet_bt_async.c tools/test_sweet_bt_lefeat.c tools/test_sweet_bt_hcdimg.c "$W/"
# kstub.h is the patch-0189 stub: add what 0213 uses (sc_id, CTL_FLAG_REG_SOCK_STREAM = 0x4 in bsd/sys/kern_control.h)
sed -i 's/struct sockaddr_ctl { u_int32_t sc_unit; };/struct sockaddr_ctl { u_int32_t sc_id, sc_unit; };/' "$W/kstub.h"
grep -q 'sc_id, sc_unit' "$W/kstub.h" || { echo "RESULT: ERROR kstub.h sockaddr_ctl not patched"; exit 2; }
sed -i 's/^#endif$/#define CTL_FLAG_REG_SOCK_STREAM 0x4\nvoid proc_selfname(char *, int);\n#endif/' "$W/kstub.h"
cd "$W"
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation -Wno-unused-function'
gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -c test_sweet_bt_hcdimg.c -o hcdimg.o 2>&1 | grep -v 'note:\|^ *[0-9]* |' && { echo "RESULT: ERROR compile hcdimg"; exit 2; }
build() {       # $1 = sweet_bt.c, $2 = sweet_bt_uart.c, $3 = binary
	cp "$1" sweet_bt.c; cp "$2" sweet_bt_uart.c; cp "$W.apply/bsd/dev/sweet_bt.h" sweet_bt.h
	gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_async.c hcdimg.o -o "$3" > build.log 2>&1 || { grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; }
	if grep -v 'note:\|^ *[0-9]* |' build.log | grep -q .; then grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; fi
	return 0
}
BT="$W.apply/bsd/dev/sweet_bt.c"; UA="$W.apply/bsd/dev/sweet_bt_uart.c"
build "$BT" "$UA" tas || { echo "RESULT: ERROR compile"; exit 2; }
res=$(timeout 60 ./tas 2>&1 | grep -a "FAIL\|^RESULT\|ERROR") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$res"
case "$res" in *"RESULT: PASS sweet-bt-async-host"*) ;; *) exit 1 ;; esac
# mutants: file|name|sed expression; each must make the test FAIL
M=(
	'bt|connect-not-refused|s/if (sweet_bt_uart_bringup_busy()) {/if (0) {/'
	'uart|attach-runs-sequence-inline|s/if (kernel_thread_start(sbu_bringup_thread, NULL, \&th) == KERN_SUCCESS) {/if (0 \&\& kernel_thread_start(sbu_bringup_thread, NULL, \&th) == KERN_SUCCESS) {/'
	'uart|busy-never-set|s/__atomic_store_n(\&sbu_bringup_busy, 1, __ATOMIC_RELEASE);/;/'
	'uart|busy-never-cleared|s/__atomic_store_n(\&sbu_bringup_busy, 0, __ATOMIC_RELEASE);/;/'
	'uart|rewind-skips-lefeat|s/if (sbu_knob_default_on("sweetbtlefeat")) {/if (0) {/'
	'uart|no-done-row|s/printf("BT UART: bring-up done in /printf("BT UART: bring-up finished in /'
	'uart|rx-spawned-in-thread|s/\tif (inline_rx) {        \/\* patch 0296/\tif (0) {        \/* patch 0296/'
	'uart|ops-set-after-flag|s/\t__atomic_store_n(\&sbu_bringup_busy, 0, __ATOMIC_RELEASE);      \/\* last/\t__atomic_store_n(\&sbu_bringup_busy, 0, __ATOMIC_RELEASE); sbt.ops = NULL;      \/* last/'
)
fail=0
for m in "${M[@]}"; do
	IFS='|' read -r f name expr <<<"$m"
	if [ "$f" = bt ]; then src="$BT"; else src="$UA"; fi
	sed "$expr" "$src" > mutant.c; cmp -s mutant.c "$src" && { echo "MUTANT $name: ERROR sed changed nothing"; fail=1; continue; }
	if [ "$f" = bt ]; then b="mutant.c"; u="$UA"; else b="$BT"; u="mutant.c"; fi
	if build "$b" "$u" tmut >/dev/null 2>&1 && timeout 60 ./tmut 2>&1 | grep -aq 'RESULT: PASS'; then echo "MUTANT $name: SURVIVED"; fail=1; else echo "MUTANT $name: killed"; fi
done
[ $fail = 0 ] && echo "RESULT: PASS sweet-bt-async-mutants ${#M[@]}/${#M[@]} killed" || { echo "RESULT: FAIL sweet-bt-async-mutants"; exit 1; }
