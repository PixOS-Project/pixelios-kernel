#!/bin/bash
# Host test of patch 0285 (BT VSC adapter, sprint-bt bt-proper; spec docs/research/bt-vsc-adapter-spec.md R5): the REAL bsd/dev/sweet_bt_uart.c (patches 0189 .. 0277 + 0285 applied in order) against stub kernel headers,
# a fake BCM4389 (0xfd53 capabilities, 0xfd54 multi-adv) and a fake bluetoothd client (tools/test_sweet_bt_vsc.c), then one mutant per assertion group (each must FAIL). Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0215-bt-keep-baud 0216-bt-le-probe 0217-bt-replay-probe 0219-bt-replay-v3 0221-bt-sweep-2007-position 0223-bt-rw200c 0225-bt-nopadv 0226-bt-pubscan 0228-bt-patchram 0229-bt-rfwit 0230-bt-rfwit2 0231-bt-nopcf 0232-bt-pubscan-active 0274-bt-lefeat 0277-bt-romread 0285-bt-vsc-adapter 0295-bt-defaults-on 0296-bt-async-bringup; do
	git apply --include='bsd/dev/sweet_bt_uart.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
# the sweet_bt.c hunk (disconnect hook) is outside the compiled harness: check it by text on the patch itself
awk '/^diff --git a\/bsd\/dev\/sweet_bt\.c/{f=1;next} /^diff --git/{f=0} f' "$ROOT/xnu/patches/0285-bt-vsc-adapter.patch" | grep -q '^+.*sweet_bt_uart_disc();' || { echo "RESULT: FAIL 0285 has no sweet_bt_uart_disc() call in sbt_disconnect"; exit 1; }
awk '/^diff --git a\/bsd\/dev\/sweet_bt\.h/{f=1;next} /^diff --git/{f=0} f' "$ROOT/xnu/patches/0285-bt-vsc-adapter.patch" | grep -q '^+void sweet_bt_uart_disc(void);' || { echo "RESULT: FAIL 0285 has no sweet_bt_uart_disc prototype in sweet_bt.h"; exit 1; }
cp tools/test_sweet_bt_vsc.c tools/test_sweet_bt_hcdimg.c "$W/"      # hcdimg: the patchram image slot, so the backend links
cd "$W"
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation'
build() {       # $1 = source file of sweet_bt_uart.c, $2 = binary
	cp "$1" sweet_bt_uart.c
	local rc=0      # a failed compile or any warning fails
	gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_vsc.c test_sweet_bt_hcdimg.c -o "$2" > build.log 2>&1 || rc=1
	if [ $rc -ne 0 ] || grep -v 'note:\|^ *[0-9]* |' build.log | grep -q .; then grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; fi
	return 0
}
build "$W.apply/bsd/dev/sweet_bt_uart.c" tvsc || { echo "RESULT: ERROR compile"; exit 2; }
res=$(./tvsc | grep -a "FAIL\|^RESULT") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$res"
case "$res" in *"RESULT: PASS sweet-bt-vsc cases="*) ;; *) exit 1 ;; esac
[ "${T_NO_MUTANTS:-0}" = 1 ] && exit 0
# mutants: (name, exact text in the patched source, replacement); the text must occur exactly once; each mutant must make the test FAIL
python3 -I - "$W.apply/bsd/dev/sweet_bt_uart.c" <<'PY' > mutlist.txt || { echo "RESULT: ERROR mutant table"; exit 2; }
import sys
src = open(sys.argv[1]).read()
M = [
    ("tx-power-zero", "#define SBU_VSC_TXPWR   0xf9u", "#define SBU_VSC_TXPWR   0x00u"),
    ("min-cap-4", "#define SBU_VSC_MINCAP  5u", "#define SBU_VSC_MINCAP  4u"),
    ("create-id-from-0", "for (id = 1; id < sbu_vsc_max && sbu_vsc_in[id].used; id++) {", "for (id = 0; id < sbu_vsc_max && sbu_vsc_in[id].used; id++) {"),
    ("remove-never-frees", "if (pe.aop == 0xfd49) {", "if (pe.aop == 0xfd49 && 0) {"),
    ("params-set-never", "in->params_set = true;", "in->params_set = false;"),
    ("enable-never-stored", "in->enabled = pe.en != 0;", "in->enabled = false;"),
    ("type1-allowed", "if (q[5] == 1) {", "if (q[5] == 9) {"),
    ("interval-min-0x10", "if (mn < 0x20 || mn > 0x4000", "if (mn < 0x10 || mn > 0x4000"),
    ("chmap-zero-ok", "|| q[6] == 0 || q[6] > 7", "|| q[6] > 7"),
    ("filter-4-ok", "q[6] > 7 || q[7] > 3)", "q[6] > 7 || q[7] > 4)"),
    ("duration-ignored", "q[4] != 0 || q[5] != 0 || q[6] != 0) {", "q[4] != 0 || q[5] != 0) {"),
    ("enable-2-ok", "if (q[1] > 1) {", "if (q[1] > 2) {"),
    ("enabled-update-ok", "if (in->enabled) {", "if (0) {"),
    ("update-type-change-sub4", "if (same) {", "if (same || 1) {"),
    ("adv-len-32-ok", "if (q[1] > 31) {", "if (q[1] > 32) {"),
    ("unknown-id-ok", "#define SBU_VSC_VALID(id) ((id) >= 1u && (id) < sbu_vsc_max && sbu_vsc_in[(id)].used)", "#define SBU_VSC_VALID(id) ((id) < sbu_vsc_max)"),
    ("plen-unchecked", "if (plen != want[op - 0xfd48]) {", "if (0) {"),
    ("reply-keeps-chip-opcode", "o[4] = (uint8_t)(pe.aop & 0xff);", "o[4] = p[4];"),
    ("reply-ncmd-fixed", "o[3] = p[3];", "o[3] = 1;"),
    ("cs-status-zero", "o[3] = st;", "o[3] = 0;"),
    ("stray-no-gap", 'SBU_GAP(SG_OTHER, "sweetbtvsc: 0xfd54 reply (status', 'printf("sweetbtvsc: 0xfd54 reply (status'),
    ("reset-keeps-state", "		memset(sbu_vsc_in, 0, sizeof(sbu_vsc_in));\n		if (sbu_vsc_pn != 0) {", "		if (sbu_vsc_pn != 0) {"),
    ("local-inside-packet", "if (sbu_vsc_rxn != 0 || __atomic_load_n(&sbu_vsc_an, __ATOMIC_RELAXED) == 0) {", "if (__atomic_load_n(&sbu_vsc_an, __ATOMIC_RELAXED) == 0) {"),
    ("hold-never", "hold = sbu_vsc_ttype == 0x01;", "hold = false;"),
    ("bad-tx-type-accepted", "b == 0x03 ? 4u : b == 0x05 ? 5u : 0u;     /* type + cmd", "b == 0x03 ? 4u : 5u;     /* type + cmd"),
    ("bad-rx-type-accepted", "t == 0x03 ? 4u : t == 0x05 ? 5u : 0u;", "t == 0x03 ? 4u : 5u;"),
    ("rx-oversize-ok", "if (sbu_vsc_rneed > SBU_VSC_RXMAX) {", "if (sbu_vsc_rneed > 99999u) {"),
    ("rxerr-ignored", "if (sbu_vsc_on && sbu_vsc_rxn != 0) {", "if (0) {"),
    ("conflict-ignored", "kv >= 1 && kv <= knobs[k].maxv", "kv > 99"),
    ("pubscan-2-not-a-conflict", '{ "sweetbtpubscan", 2 }', '{ "sweetbtpubscan", 1 }'),
    ("knob-off-ignored", 'PE_parse_boot_argn("sweetbtvsc", &v, sizeof(v)) && v != 1) {', 'PE_parse_boot_argn("sweetbtvsc", &v, sizeof(v)) && v > 99) {'),
    ("caps-status-ignored", '	if (st != 0) {\n		printf("BT GAP: sweetbtvsc: 0xfd53 status', '	if (0) {\n		printf("BT GAP: sweetbtvsc: 0xfd53 status'),
    ("caps-short-ok", "	if (rpn < 1) {", "	if (0) {"),
    ("caps-lt5-ok", "	if (rp[0] < SBU_VSC_MINCAP) {", "	if (0) {"),
    ("disc-no-summary", '	sbu_vsc_summary("bluetoothd disconnected");', "	(void)0;"),
    ("disc-no-orphan", "sbu_vsc_pq[(sbu_vsc_ph + k) % SBU_VSC_PEND].orphan = true;", "(void)k;"),
    ("orphan-delivered", "	if (pe.orphan) {\n		return;", "	if (0) {\n		return;"),
    ("no-trace-rewrite", '	sbu_trace("rx-vsc", o, sizeof(o));', "	(void)o;"),
    ("no-throttle", "if (n_ <= 16 || (n_ & (n_ - 1)) == 0) { \\", "if (1) { \\"),
    ("unpush-never", "		sbu_vsc_pn--;\n		sbu_vsc_nxlate--;", "		sbu_vsc_nxlate--;"),
    ("rx-vsc-bypassed", "if (sbu_vsc_on || sbu_vsc_rxn != 0) {      /* patch 0285", "if (0) {      /* patch 0285"),
    ("tx-vsc-bypassed", "if (sbu_vsc_on || SBU_VSC_TXHELD) {", "if (0) {"),
    ("held-not-flushed-at-stop", "			rc = sbu_tx(sbu_vsc_txb, m);", "			rc = 0;"),
    ("adv-id-last-missing", "sub[37] = (uint8_t)id;", "sub[37] = 0;"),
    ("create-addr-wrong-slot", "memcpy(in->addr, q + 1, 6);", "memcpy(in->addr, q + 2, 6);"),
    ("local-answer-length", "(uint8_t)(3 + b[2]);", "7;"),
    ("idle-remove-sent", "		if (!in->enabled) {\n			memset(in, 0, sizeof(*in));", "		if (0) {\n			memset(in, 0, sizeof(*in));"),
    ("cmd-ignores-off", "	if (!sbu_vsc_on) {                      /* the rx thread stopped", "	if (0) {                      /* the rx thread stopped"),
    ("tx-framing-prints-byte", 'tx framing lost at offset %u", (unsigned)i);', 'tx framing lost at offset %u byte %#x", (unsigned)i, b);'),
    ("rx-framing-prints-byte", 'rx framing lost at offset %u", (unsigned)i);', 'rx framing lost at offset %u byte %#x", (unsigned)i, t);'),
]
for i, (name, old, new) in enumerate(M):
    if src.count(old) != 1:
        print("NOTONCE %s %d" % (name, src.count(old))); sys.exit(2)
    open("mut_%02d.c" % i, "w").write(src.replace(old, new))
    print(i, name)
PY
if grep -q '^NOTONCE' mutlist.txt; then cat mutlist.txt; echo "RESULT: ERROR mutant table"; exit 2; fi
kill=0; total=0
while read -r idx name; do
	total=$((total+1))
	if ! build "mut_$(printf %02d "$idx").c" tm; then echo "MUTANT $name: killed (compile)"; continue; fi
	if ./tm 2>/dev/null | grep -aq "RESULT: PASS"; then echo "MUTANT $name: SURVIVED"; kill=1; else echo "MUTANT $name: killed"; fi
done < mutlist.txt
[ $kill -eq 0 ] && echo "RESULT: PASS sweet-bt-vsc-mutants $total/$total killed" || { echo "RESULT: FAIL sweet-bt-vsc-mutants"; exit 1; }
