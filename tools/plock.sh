# Sourced by xrun and probe-run (TP-020): take the phone lock only when the phone is about to be used (image built, gates done).
# Run from the repo root. plock_take TOOL WAIT_S -> 0 held (taken now, or already ours via `phone-lock run`), 1 busy (message in PLOCK_MSG).
# Identity is a fresh token exported as PHONE_LOCK_TOKEN to everything started afterwards (fboot.sh, phone goto check it).
plock_take() {
	local tool=$1 wait=${2:-0} tok
	PLOCK_TOOK=0
	tools/phone-lock check >/dev/null 2>&1 && return 0   # PHONE_LOCK_TOKEN from an outer `phone-lock run`
	tok=$(cat /proc/sys/kernel/random/uuid)
	# pid-bound: the lock ends when this script dies, never by ttl; the ttl is only the figure shown to waiters
	PLOCK_MSG=$(tools/phone-lock acquire --owner "${PHONE_OWNER:-$tool}" --wait "$wait" --ttl 7200 --pid $$ --token "$tok" 2>&1 | head -n 2 | tr '\n' ' ')
	tools/phone-lock check --token "$tok" >/dev/null 2>&1 || return 1
	PLOCK_TOOK=1; PLOCK_TOKEN=$tok; export PHONE_LOCK_TOKEN=$tok
}
plock_drop() {
	[ "${PLOCK_TOOK:-0}" = 1 ] || return 0
	PLOCK_TOOK=0
	tools/phone-lock release --token "$PLOCK_TOKEN" >/dev/null 2>&1 || true
}
