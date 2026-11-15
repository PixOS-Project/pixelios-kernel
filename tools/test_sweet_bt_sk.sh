#!/bin/bash
# Host test of patch 0286 (BT Skywalk UART channel): the REAL bsd/dev/sweet_bt.c (patches 0189, 0213, 0286 applied in order) and bsd/dev/sweet_bt_sk.c against stubs of the kernel
# control and the kern_nexus/kern_channel/kern_packet API (tools/test_sweet_bt_sk.c + tools/test_sweet_bt_sk_stub.h), then one mutant per assertion group (each must FAIL).
# Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network. Style of tools/test_sweet_bt_nopcf.sh.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
out=$(python3 -I tools/test_sweet_bt_c.py --uart --keep 2>&1) || { echo "$out" | tail -5; echo "RESULT: ERROR base uart host test failed"; exit 2; }
W=$(printf '%s\n' "$out" | sed -n 's/^kept //p')
[ -n "$W" ] && [ -d "$W" ] || { echo "RESULT: ERROR no kept work dir"; exit 2; }
trap 'rm -rf -- "$W" "$W.apply"' EXIT
mkdir "$W.apply" && (cd "$W.apply" && git init -q . && for p in 0189-sweet-bt-transport 0213-sweet-bt-kctl-stream 0285-bt-vsc-adapter 0286-bt-skywalk-channel; do
	git apply --include='bsd/dev/sweet_bt.c' --include='bsd/dev/sweet_bt.h' --include='bsd/dev/sweet_bt_sk.c' "$ROOT/xnu/patches/$p.patch"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
# the files hunk must be in the patch (text check; the build lists the new file there)
grep -q '^+bsd/dev/sweet_bt_sk.c' "$ROOT/xnu/patches/0286-bt-skywalk-channel.patch" || { echo "RESULT: FAIL 0286 has no bsd/conf/files entry"; exit 1; }
cp tools/test_sweet_bt_sk.c tools/test_sweet_bt_sk_stub.h "$W/"
# kstub.h is the patch-0189 stub: add what 0213 uses (sc_id, CTL_FLAG_REG_SOCK_STREAM = 0x4 in bsd/sys/kern_control.h)
sed -i 's/struct sockaddr_ctl { u_int32_t sc_unit; };/struct sockaddr_ctl { u_int32_t sc_id, sc_unit; };/' "$W/kstub.h"
grep -q 'sc_id, sc_unit' "$W/kstub.h" || { echo "RESULT: ERROR kstub.h sockaddr_ctl not patched"; exit 2; }
sed -i 's/^#endif$/#define CTL_FLAG_REG_SOCK_STREAM 0x4\n#endif/' "$W/kstub.h"
mkdir -p "$W/inc/skywalk" "$W/inc/kern" "$W/inc/uuid"
for h in kern/locks.h uuid/uuid.h sys/proc.h skywalk/os_skywalk_private.h; do mkdir -p "$W/inc/$(dirname $h)"; printf '#include "kstub.h"\n#include "test_sweet_bt_sk_stub.h"\n' > "$W/inc/$h"; done
cd "$W"
cp test_sweet_bt_sk_stub.h skstub.h
WARN='-Wall -Wextra -Wno-format -Wno-unused-parameter -Wno-pragmas -Wno-unknown-pragmas -Wno-incompatible-pointer-types -Wno-misleading-indentation -Wno-unused-function'
build() {       # $1 = sweet_bt_sk.c, $2 = sweet_bt.c, $3 = binary
	cp "$1" sweet_bt_sk.c; cp "$2" sweet_bt.c
	local rc=0
	gcc -std=gnu11 -g -O1 $WARN -fsanitize=address,undefined -fno-sanitize-recover=all -iquote . -I inc -I . test_sweet_bt_sk.c -o "$3" -lpthread > build.log 2>&1 || rc=1
	if [ $rc -ne 0 ] || grep -v 'note:\|^ *[0-9]* |' build.log | grep -q .; then grep -v 'note:\|^ *[0-9]* |' build.log | head -20; return 1; fi
	return 0
}
SK="$W.apply/bsd/dev/sweet_bt_sk.c"; BT="$W.apply/bsd/dev/sweet_bt.c"
cp "$W.apply/bsd/dev/sweet_bt.h" sweet_bt.h
build "$SK" "$BT" tsk || { echo "RESULT: ERROR compile"; exit 2; }
res=$(timeout 60 ./tsk 2>&1 | grep -a "FAIL\|^RESULT\|ERROR") || { rc=$?; [ $rc = 1 ] || echo "BT GAP: grep rc=$rc" >&2; }
echo "$res"
case "$res" in *"RESULT: PASS sweet-bt-sk-host"*) ;; *) exit 1 ;; esac
# mutants: file|name|sed expression; each must make the test FAIL (compile error, crash, hang or a failed check)
M=(
	'sk|tx-offset-ignored|s/base + kern_buflet_get_data_offset(buf), len/base, len/'
	'sk|tx-not-consumed-on-error|s/if (last != NULL) {/if (last != NULL \&\& err == 0) {/'
	'sk|tx-error-swallowed|s/return err;     \/\* non-zero/return 0;     \/* non-zero/'
	'sk|tx-dropped-slot-not-skipped|s/if (ph == 0) {$/if (0) {/'
	'sk|tx-advance-missing|s/kern_channel_advance_slot(ring, last);//'
	'bt|tx-ignores-closed-control|s/if (!__atomic_load_n(\&sbt.busy\[SBT_IDX_SK\], __ATOMIC_ACQUIRE)) {/if (0) {/'
	'bt|tx-no-backend-check|s/if (sbt.ops == NULL || sbt.ops->tx == NULL) {$/if (0) {/'
	'sk|rx-no-notify|s/^\t\tsbsk_notify_rx();/\t\t;/'
	'sk|rx-notify-under-lock|s/ne = kern_channel_notify(r, 0);/lck_mtx_lock(\&sbsk_lock); ne = kern_channel_notify(r, 0); lck_mtx_unlock(\&sbsk_lock);/'
	'sk|rx-no-notifier-wait|s/while (sbsk.notifiers != 0) {/while (0) {/'
	'sk|rx-notifier-never-released|s/if (--sbsk.notifiers == 0) {/if (sbsk.notifiers == 0) {/'
	'sk|rx-fifo-push-no-wrap|s/sbsk.fifo\[(first + i) % SBSK_FIFO\] = p\[i\];/sbsk.fifo[first + i] = p[i];/'
	'sk|rx-fifo-pop-no-wrap|s/dst\[i\] = sbsk.fifo\[i - first\];/dst[i] = sbsk.fifo[i];/'
	'sk|rx-chunk-512|s/n = sbsk.flen < SBSK_BUF ? sbsk.flen : SBSK_BUF;/n = sbsk.flen < 512 ? sbsk.flen : 512;/'
	'sk|rx-overflow-silent|s/\*err = ENOBUFS;/*err = 0;/'
	'sk|rx-not-finalized|s/kern_packet_finalize(ph) != 0 ||/0 ||/'
	'sk|rx-advance-missing|s/kern_channel_advance_slot(ring, prev);//'
	'sk|rx-bytes-before-connected|s/sbsk.active = true;     \/\*/sbsk.active = false;    \/*/'
	'bt|rx-ignores-channel|s/if (sweet_bt_sk_rx(data, len, \&skerr)) {/if (0) {/'
	'sk|connect-second-allowed|s/if (sbsk.chan != NULL) {/if (0) {/'
	'sk|disconnected-any-channel|s/if (sbsk.chan == channel) {/if (1) {/'
	'sk|predisconnect-keeps-active|s/sbsk_deactivate_locked("closing");//'
	'sk|kctl-close-keeps-fifo|s/^\t\tsbsk.fhead = sbsk.flen = 0;$//'
	'bt|kctl-close-no-hook|s/if (i == SBT_IDX_SK) {/if (0) {/'
	'sk|connected-without-ring|s/if (sbsk.rxring == NULL) {/if (0) {/'
	'sk|spec-length-unchecked|s/if (len != SBSK_SPEC_LEN || data == NULL) {/if (0) {/'
	'sk|spec-differs-silent|s/if (spec\[0\] != SBSK_SPEC_BUF || spec\[1\] != SBSK_SPEC_SLOTS || spec\[2\] != SBSK_SPEC_SLOTS) {/if (0) {/'
	'sk|uuid-wrong|s/memcpy(data, sbsk.nx_uuid, SBSK_UUID_LEN);/memcpy(data, sbsk.prov_uuid, SBSK_UUID_LEN);/'
	'sk|uuid-len-not-set|s/\*len = SBSK_UUID_LEN;//'
	'sk|uuid-short-buffer-ok|s/if (\*len < SBSK_UUID_LEN || data == NULL) {/if (0) {/'
	'sk|anonymous-missing|/NEXUS_ATTR_ANONYMOUS, 1/d'
	'sk|slots-wrong|s/{ NEXUS_ATTR_TX_SLOTS, SBSK_SLOTS, "TX_SLOTS" },/{ NEXUS_ATTR_TX_SLOTS, 128, "TX_SLOTS" },/'
	'sk|create-retried-after-failure|s/if (sbsk.state == SKST_NONE) {/if (sbsk.state != SKST_UP) {/'
	'sk|no-deregister-on-failure|s/(void)kern_nexus_controller_deregister_provider(sbsk.ncd, sbsk.prov_uuid);//'
	'sk|no-controller-destroy-on-failure|s/^\t\tkern_nexus_controller_destroy(sbsk.ncd);$//'
	'sk|attr-leaked-on-failure|s/^\t\tkern_nexus_attr_destroy(nxa);$//'
	'sk|attr-error-ignored|s/if ((e = kern_nexus_attr_set(nxa, sbsk_attrs\[k\].type, sbsk_attrs\[k\].value)) != 0) {/if (kern_nexus_attr_set(nxa, sbsk_attrs[k].type, sbsk_attrs[k].value) == 12345) {/'
	'sk|knob-ignored|s/\&\& v == 0);/\&\& v == 99);/'
	'bt|sk-opts-on-plain-control|s/sbt_index(ref) == SBT_IDX_SK \&\& sweet_bt_sk_is_opt(opt)/sweet_bt_sk_is_opt(opt)/'
	'sk|alloc-failure-drops-bytes|s/SBSK_GAP(SKG_ALLOC, "skywalk RX: packet allocation failed (error %d); %zu bytes wait in the FIFO", (int)e, n);/sbsk.fhead = sbsk.flen = 0;/'
	'sk|spec-does-not-arm|s/sbsk.armed = true;      \/\* from here/sbsk.armed = false;     \/* from here/'
	'sk|connected-drops-queued-bytes|s/if (!sbsk.armed) {/if (1) {/'
	'sk|connected-no-kick|s/if (e == 0 \&\& kept != 0) {/if (0) {/'
	'sk|pre-connect-overflow-silent|s/if (drop != 0) {/if (0) {/'
	'sk|pre-connect-drops-newest|s/sbsk.fhead = (sbsk.fhead + drop) % SBSK_FIFO;/(void)drop;/'
	'sk|control-close-keeps-armed|s/sbsk.armed = false;     \/\* the control closed/sbsk.armed = true;      \/* the control closed/'
	'sk|notify-error-silent|s/if (ne != 0) {/if (0) {/'
	'sk|reclaim-missing|s/(void)kern_channel_reclaim(ring);       \/\* user-released/;\/\/ user-released/'
	'sk|connect-pbuf-error-ignored|s/if (e != 0 || rpp == NULL) {/if (0) {/'
)
kill=0
for m in "${M[@]}"; do
	f=${m%%|*}; r=${m#*|}; name=${r%%|*}; ex=${r#*|}
	if [ "$f" = sk ]; then src=$SK; else src=$BT; fi
	sed "$ex" "$src" > mut.c
	if cmp -s mut.c "$src"; then echo "MUTANT $name: ERROR sed changed nothing"; kill=1; continue; fi
	if [ "$f" = sk ]; then a=mut.c; b=$BT; else a=$SK; b=mut.c; fi
	if ! build "$a" "$b" tm; then echo "MUTANT $name: killed (compile)"; continue; fi
	if timeout 30 ./tm 2>/dev/null | grep -aq "RESULT: PASS"; then echo "MUTANT $name: SURVIVED"; kill=1; else echo "MUTANT $name: killed"; fi
done
[ $kill -eq 0 ] && echo "RESULT: PASS sweet-bt-sk-mutants ${#M[@]}/${#M[@]} killed" || { echo "RESULT: FAIL sweet-bt-sk-mutants"; exit 1; }
