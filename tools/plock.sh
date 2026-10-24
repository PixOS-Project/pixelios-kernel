# Sourced by xrun and probe-run (TP-020): take the phone lock only when the phone is about to be used (image built, gates done).
# Run from the repo root. plock_take TOOL WAIT_S -> 0 held (taken now, or already ours via `phone-lock run`), 1 busy (message in PLOCK_MSG).
plock_take() {
	local tool=$1 wait=${2:-0} owner
	PLOCK_TOOK=0
	tools/phone-lock check --strict >/dev/null 2>&1 && return 0
	owner=${PHONE_LOCK_OWNER:-${PHONE_OWNER:-$tool-$$}}
	# pid-bound: the lock ends when this script dies, never by ttl; the ttl is only the figure shown to waiters
	PLOCK_MSG=$(tools/phone-lock acquire --owner "$owner" --wait "$wait" --ttl 7200 --pid $$ 2>&1 | head -n 2 | tr '\n' ' ')
	tools/phone-lock check --strict --owner "$owner" >/dev/null 2>&1 || return 1
	PLOCK_TOOK=1; export PHONE_LOCK_OWNER=$owner
}
plock_drop() {
	[ "${PLOCK_TOOK:-0}" = 1 ] || return 0
	PLOCK_TOOK=0
	tools/phone-lock release --owner "$PHONE_LOCK_OWNER" >/dev/null 2>&1 || true
}
