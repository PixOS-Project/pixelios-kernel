#!/bin/bash
# Host test of patch 0231 sweetbtnopcf=1 (T-b58f, W-BTSCAN; review T-b58f-0231 F1-F3 cases 9-13) and patch 0232 sweetbtpubscan=2 (T-0936, cases 14-15): the REAL bsd/dev/sweet_bt_uart.c (patches 0189 .. 0231 applied in order) against stub kernel headers, a fake
# BCM4389 and a fake bluetoothd client (tools/test_sweet_bt_nopcf.c), then one mutant per assertion group (each must FAIL). Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0215-bt-keep-baud 0216-bt-le-probe 0217-bt-replay-probe 0219-bt-replay-v3 0221-bt-sweep-2007-position 0223-bt-rw200c 0225-bt-nopadv 0226-bt-pubscan 0228-bt-patchram 0229-bt-rfwit 0230-bt-rfwit2 0231-bt-nopcf 0232-bt-pubscan-active; do
	git apply --include='bsd/dev/sweet_bt_uart.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
cp tools/test_sweet_bt_nopcf.c tools/test_sweet_bt_hcdimg.c "$W/"      # hcdimg: the boot-arg stub is no longer constant 0, so the patchram path links
cd "$W"
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation'
build() {       # $1 = source file of sweet_bt_uart.c, $2 = binary
	cp "$1" sweet_bt_uart.c
	local rc=0      # a failed compile or any warning fails (pipefail made the old `gcc | grep && return 1` return 0 on a failed compile)
	gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_nopcf.c test_sweet_bt_hcdimg.c -o "$2" > build.log 2>&1 || rc=1
	if [ $rc -ne 0 ] || grep -v 'note:\|^ *[0-9]* |' build.log | grep -q .; then grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; fi
	return 0
}
build "$W.apply/bsd/dev/sweet_bt_uart.c" tnp || { echo "RESULT: ERROR compile"; exit 2; }
res=$(./tnp | grep -a "FAIL\|^RESULT") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$res"
case "$res" in *"RESULT: PASS nopcf-host"*) ;; *) exit 1 ;; esac
# regression: the 0226 pubscan parser cases of tools/test_sweet_bt_le_probe.c on the 0231 parser (sbu_txrw now copies into an out buffer; the adapter checks every byte comes out)
sed 's|^static void txrw(uint8_t \*p, size_t n) { sbu_pubscan_n += sbu_txrw(p, n); }|static void txrw(uint8_t *p, size_t n) { uint8_t o[600]; unsigned r; size_t m = sbu_txrw(p, n, o, \&r); if (m != n) { printf("FAIL txrw adapter: %zu of %zu bytes out\\n", m, n); } memcpy(p, o, m); sbu_pubscan_n += r; }|' \
	"$ROOT/tools/test_sweet_bt_le_probe.c" > test_sweet_bt_le_probe.c
grep -q 'txrw adapter' test_sweet_bt_le_probe.c || { echo "RESULT: ERROR le_probe txrw adapter not applied"; exit 2; }
cp "$W.apply/bsd/dev/sweet_bt_uart.c" sweet_bt_uart.c
gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_le_probe.c -o tle 2>&1 | grep -v 'note:\|^ *[0-9]* |' && { echo "RESULT: ERROR compile le_probe on 0231"; exit 2; }
lp=$(./tle | grep -a "FAIL\|^RESULT") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$lp" | sed 's/^/0226-on-0231: /'
case "$lp" in *"RESULT: PASS t0c77-le-probe-host"*) ;; *) exit 1 ;; esac
# mutants: name|sed expression on the patched source; each must make the test FAIL
M=(
	'no-withhold|s/sbu_pcf_ans\[k\]\.sub == sbu_pcf_sub && sbu_pcf_ans\[k\]\.act == b/0/'
	'inject-inside-event|s/} else if (sbu_rw_off != 0) {/} else if (0) {/'
	'wrong-answer-0702|s/0x07, 0x02, 0x08 }/0x07, 0x02, 0x07 }/'
	'knob-ignored|s/(pos == 0 && sbu_nopcf_on && b == 0x01)/(pos == 0 \&\& b == 0x01)/'
	'drop-never-ends|s/^\t\t\tsbu_pcf_drop = !end;$/\t\t\tsbu_pcf_drop = true;/'
	'no-flush-on-pass|s/if (v == 1 || (v == 0 && end)) {/if (v == 1 \&\& 0) {/'
	'queue-no-full-check|s/>= SBU_PCF_Q) {/>= 1000u) {/'
	'lost-not-set|s/^\t\t\t\tsbu_rw_lost = true;$//'
	'synth-not-traced|s/^\t\tsbu_trace("rx-synth".*$//'
	'framer-off-without-rw|s/ || (sbu_nopcf_rx \&\& !sbu_rw_lost)) {$/) {/'
	'buf-no-room|s/uint8_t buf\[SBU_TXRW_MAX + sizeof(sbu_pcf_hold)\];/uint8_t buf[SBU_TXRW_MAX];/'
	'v2-drop-always|s/^\t\t\t\tsbu_pcf_drop = !end;$/\t\t\t\tsbu_pcf_drop = true;/'
	'rxerr-no-nopcf|s/ || (sbu_nopcf_rx \&\& !sbu_rw_lost)) \&\& sbu_rw_off != 0) {/) \&\& sbu_rw_off != 0) {/'
	'bootarg-any|s/^\t\tif (v == 1) {$/\t\tif (v != 0) {/'
	'rx-cleared-by-txp-off|s/^\t\tsbu_nopcf_on = false;$/\t\tsbu_nopcf_on = false; sbu_nopcf_rx = false;/'
	'p0232-active-never|s/ \&\& !(sbu_pubscan_active \&\& sbu_tx_scantype == 0x01)) {/) {/'
	'p0232-active-without-knob|s/!(sbu_pubscan_active \&\& sbu_tx_scantype == 0x01)/!(sbu_tx_scantype == 0x01)/'
	'p0232-any-scan-type|s/!(sbu_pubscan_active \&\& sbu_tx_scantype == 0x01)/!(sbu_pubscan_active \&\& sbu_tx_scantype != 0x00)/'
	'p0232-no-active-row|s/if (e == 0 \&\& k >= nrw - sbu_txrw_nact) {/if (0) {/'
	'p0232-nact-not-reset|s/^\tsbu_txrw_nact = 0;$//'
	'p0232-parse-any|s/if (v == 1 || v == 2) {/if (v != 0) {/'
	'p0232-parse-2-passive|s/sbu_pubscan_active = v == 2;/sbu_pubscan_active = false;/'
	'no-f3-row|s/^\t\t\t\tif (sbu_nopcf_rx) {     \/\* review F3/\t\t\t\tif (0) {     \/* review F3/'
)
kill=0
for m in "${M[@]}"; do
	name=${m%%|*}; ex=${m#*|}
	sed "$ex" "$W.apply/bsd/dev/sweet_bt_uart.c" > mut.c
	if cmp -s mut.c "$W.apply/bsd/dev/sweet_bt_uart.c"; then echo "MUTANT $name: ERROR sed changed nothing"; kill=1; continue; fi
	if ! build mut.c tm; then echo "MUTANT $name: killed (compile)"; continue; fi
	if ./tm 2>/dev/null | grep -aq "RESULT: PASS"; then echo "MUTANT $name: SURVIVED"; kill=1; else echo "MUTANT $name: killed"; fi
done
[ $kill -eq 0 ] && echo "RESULT: PASS nopcf-mutants ${#M[@]}/${#M[@]} killed" || { echo "RESULT: FAIL nopcf-mutants"; exit 1; }
