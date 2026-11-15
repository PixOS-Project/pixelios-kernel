#!/bin/bash
# Host test of patch 0291 (Skywalk privileges by entitlement): builds bsd/kern/sweet_amfi.c from the patches that touch it (applied in order),
# extracts the privilege section, compiles it against stubs (tools/test_sweet_sandbox_skywalk.c), then one mutant per rule (each must FAIL).
# Exit 0 PASS, 1 FAIL, 2 ERROR. No phone, no network.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
W=$(mktemp -d)
trap 'rm -rf -- "$W"' EXIT
(cd "$W" && git init -q . && for p in $(grep -l 'sweet_amfi.c' "$ROOT"/xnu/patches/*.patch | sort); do
	git apply --include=bsd/kern/sweet_amfi.c "$p"; done) || { echo "RESULT: ERROR patches do not apply in order"; exit 2; }
S="$W/bsd/kern/sweet_amfi.c"
grep -q 'IOKit/IOBSD.h' "$S" || { echo "RESULT: FAIL sweet_amfi.c does not include IOBSD.h"; exit 1; }
sec() {		# $1 = sweet_amfi.c, $2 = out
	awk '/^#define SWEET_PRIV_LOG_MAX/{p=1} /^static const struct mac_policy_ops sweet_sandbox_mac_ops/{p=0} p' "$1" > "$2"
	[ -s "$2" ] || return 1
}
build() {	# $1 = section, $2 = binary
	gcc -std=gnu11 -g -O1 -Wall -Wextra -Wno-unused-parameter -fsanitize=address,undefined -fno-sanitize-recover=all \
		-DSWEET_SECTION="\"$1\"" -I "$ROOT/tools" "$ROOT/tools/test_sweet_sandbox_skywalk.c" -o "$2" > "$W/build.log" 2>&1 || { head -20 "$W/build.log"; return 1; }
	[ -z "$(grep -v 'note:' "$W/build.log")" ] || { head -20 "$W/build.log"; return 1; }
}
sec "$S" "$W/sec.c" || { echo "RESULT: ERROR no privilege section"; exit 2; }
build "$W/sec.c" "$W/t" || { echo "RESULT: ERROR compile"; exit 2; }
res=$("$W/t" 2>&1) || { rc=$?; [ $rc = 1 ] || echo "SANDBOX GAP: test binary rc=$rc" >&2; }
echo "$res" | grep -a "FAIL\|^RESULT"
case "$res" in *"RESULT: PASS sweet-sandbox-skywalk"*) ;; *) exit 1 ;; esac
M=(
	'grant-without-entitlement|s/if (!IOCurrentTaskHasEntitlement(sweet_priv_sk_grants\[g\].entitlement)) {/if (!IOCurrentTaskHasEntitlement(sweet_priv_sk_grants[g].entitlement) \&\& 0) {/'
	'deny-with-entitlement|s/if (!IOCurrentTaskHasEntitlement(sweet_priv_sk_grants\[g\].entitlement)) {/if (!IOCurrentTaskHasEntitlement(sweet_priv_sk_grants[g].entitlement) || 1) {/'
	'user-pipe-wrong-ent|s/skywalk.register-user-pipe"/skywalk.register-kernel-pipe"/'
	'kernel-pipe-wrong-ent|s/skywalk.register-kernel-pipe"/skywalk.register-user-pipe"/'
	'net-if-wrong-ent|s/skywalk.register-net-if"/skywalk.register-flow-switch"/'
	'flow-switch-wrong-ent|s/skywalk.register-flow-switch"/skywalk.register-net-if"/'
	'observe-all-wrong-ent|s/skywalk.observe-all"/skywalk.observe-stats"/'
	'observe-stats-wrong-ent|s/skywalk.observe-stats"/skywalk.observe-all"/'
	'low-latency-wrong-ent|s/skywalk.low-latency-channel"/skywalk.observe-all"/'
	'low-latency-missing|/{ PRIV_SKYWALK_LOW_LATENCY_CHANNEL,/d'
	'user-pipe-missing|/{ PRIV_SKYWALK_REGISTER_USER_PIPE,/d'
	'observe-stats-missing|/{ PRIV_SKYWALK_OBSERVE_STATS,/d'
	'grant-line-every-time|s/if (!seen \&\& sweet_priv_sk_logged_count < SWEET_PRIV_LOG_MAX) {/if ((seen || !seen) \&\& sweet_priv_sk_logged_count < SWEET_PRIV_LOG_MAX) {/'
	'grant-log-unbounded|s/if (!seen \&\& sweet_priv_sk_logged_count < SWEET_PRIV_LOG_MAX) {/if (!seen) {/'
	'grant-line-ignores-proc|s/sweet_priv_sk_logged\[i\].priv == priv \&\&/sweet_priv_sk_logged[i].priv == priv || /'
	'grant-skywalk-before-worktest|s/sweet_sandbox_priv_skywalk(priv, name) == 0/sweet_sandbox_priv_skywalk(priv, name) != 0/'
	'denied-line-lost|s/denied (uid %u)/denied/'
)
kill=0
for m in "${M[@]}"; do
	name=${m%%|*}; ex=${m#*|}
	sed "$ex" "$W/sec.c" > "$W/mut.c"
	if cmp -s "$W/mut.c" "$W/sec.c"; then echo "MUTANT $name: ERROR sed changed nothing"; kill=1; continue; fi
	if ! build "$W/mut.c" "$W/tm"; then echo "MUTANT $name: killed (compile)"; continue; fi
	if timeout 30 "$W/tm" 2>/dev/null | grep -aq "RESULT: PASS"; then echo "MUTANT $name: SURVIVED"; kill=1; else echo "MUTANT $name: killed"; fi
done
[ $kill -eq 0 ] && echo "RESULT: PASS sweet-sandbox-skywalk-mutants ${#M[@]}/${#M[@]} killed" || { echo "RESULT: FAIL sweet-sandbox-skywalk-mutants"; exit 1; }
