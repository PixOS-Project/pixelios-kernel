#!/bin/bash
# golden-bt session (super docs/runplans/golden-bt-s0.md, goal row BT-S0; derived from golden-6b.sh). UNATTENDED after one hardware PASS (until then the env gate below stays). Run ONLY as:
#   cd <kernel clone> && GOLDEN_BT_CONSENT=yes tools/phone-lock run --owner golden --wait 3600 --ttl 3600 -- bash ~/pixelios-artifacts/golden/golden-bt.sh
# Shape = golden-6.sh (same preflight, restore, waitboot, gp, chk). No su-check, no boot-capture, no Wi-Fi, no inventory, no swipes.
# Writes nothing but userdata (fboot erase, final inj8 re-flash). Ends with RESULT: PASS|FAIL|ERROR carrying gaps= and skipped=.
# GOLDEN_BT_CONSENT=yes is set by the person starting the run, NEVER here. GP_UI_ATTENDED is never set: this session sends no input (no swipes).
set -u
unset GP_UI_ATTENDED
cd "${PIXOS_KERNEL:-$HOME/.t3/projects/pixelios/pixos-project/pixelios-kernel}" || { echo "RESULT: ERROR no kernel clone"; exit 2; }
# attended gate: before anything touches the phone (exit 2 = nothing erased or flashed)
[ "${GOLDEN_BT_CONSENT-}" = "yes" ] || { echo "GBT ROW 2a: not attended; GOLDEN_BT_CONSENT=yes must be set by the person starting the run"; echo "RESULT: ERROR golden-bt refuses: no user present (nothing was erased or flashed) gaps=0 skipped=0"; exit 2; }
O=~/pixelios-artifacts/golden/g-bt-$(date -u +%Y%m%dT%H%M%SZ); O2=$O-b2
IMG=/tank/pixelios/ios-root4-inj8.img
RS=""; GAPNAMES=""; GAPS=0; SKIPPED=0; ABORT=0; ERASING=0; RESTORED=0
# Expected totals (as golden-6.sh:13): identity 2 gaps per boot x 2 boots = +4 EXPECTED (cmdline.txt empty, cmdline.txt cause unproven, rule 8); set 0 only after an observed run shows identity gaps=0. Any other gap is a FAIL.
EXP_GAPS=4; EXP_SKIPPED=0
log() { echo "GBT $(date -u +%H:%M:%S) $*"; }
row() { log "ROW $*"; }

have() {   # match the real case label (or the MMIO_MODS key for mmio-filter-*), not usage/comment text (golden-6.sh F9)
	grep -qE "^[[:space:]]*([a-z0-9-]+\|)*$1(\|[a-z0-9-]+)*\)" tools/golden-pull && return 0
	case $1 in mmio-filter-*) grep -qE "\[${1#mmio-filter-}\]=\"" tools/golden-pull ;; *) return 1 ;; esac
}
gp() {   # gp ROW OUTDIR ACTION [args]: one golden-pull action, prints its RESULT, counts gaps, returns its rc
	local r=$1 d=$2 a=$3 res g rc; shift 3
	res=$(tools/golden-pull --out "$d" "$a" "$@" 2>&1); rc=$?
	echo "$res" | grep '^GAP:' | head -20 | sed "s/^/GBT $a: /"
	echo "$res" | tail -1 | sed "s/^/GBT $a: /"
	g=$(echo "$res" | tail -1 | sed -n 's/.* gaps=\([0-9]*\).*/\1/p'); g=${g:-0}
	echo "$res" | grep -q 'kptr_restrict not restored' && ABORT=1   # stop condition 4
	if [ "$rc" != 0 ] || [ "$g" != 0 ]; then GAPS=$((GAPS + (g > 0 ? g : 1))); echo "GBT ROW $r GAP $a gaps=$g rc=$rc"; GAPNAMES="$GAPNAMES $a:$g"; fi
	return "$rc"
}
w() { timeout "$1" tail -f /dev/null; }   # one bounded wait, no polling

waitboot() {   # one bounded wait, phone-state, then at most one more bounded wait
	w 150; st=$(tools/phone-state 2>&1 | tail -1); log "phone-state after 150 s: $st"
	case "$st" in *state=android*) ;; *) w 120; st=$(tools/phone-state 2>&1 | tail -1); log "phone-state after 270 s: $st" ;; esac
	case "$st" in *state=android*) return 0 ;; *) return 1 ;; esac
}

restore() {   # always ends here once userdata was erased; phone rests in fastboot; runs at most once (RESTORED)
	[ "$RESTORED" = 0 ] || return 0
	RESTORED=1
	st=$(tools/phone-state 2>&1 | tail -1)
	case "$st" in *state=fastboot*) ;; *) tools/phone goto fastboot --timeout 120 2>&1 | tail -1 ;; esac
	if [ "${GOLDEN_NO_RESTORE-}" = 1 ]; then   # operator sets it when the next lock owner is golden again (driver 19, efficiency review FIX 3); xrun refuses XNU until a re-flash
		RS=" restore=SKIPPED(GOLDEN_NO_RESTORE=1)"
		log "restore SKIPPED: GOLDEN_NO_RESTORE=1 (userdata stays Android/erased; xrun refuses XNU until a re-flash)"
		st=$(tools/phone-state 2>&1 | tail -1); row "7 restore skipped; $st"
		case "$st" in *state=fastboot*) ;; *) GAPS=$((GAPS + 1)); echo "GBT GAP: phone does not rest in fastboot ($st)" ;; esac
		return 0
	fi
	log "restore: re-flash inj8"
	tools/fflash-userdata.sh "$IMG" --yes-erase-userdata --force --reason "golden-bt restore: re-flash the iOS root after the Android userdata erase" 2>&1 | tail -3
	fr=${PIPESTATUS[0]}
	st=$(tools/phone-state 2>&1 | tail -1); row "7 fflash rc=$fr; $st"
	[ "$fr" = 0 ] || { echo "RESULT: FAIL restore re-flash rc=$fr (phone has an erased userdata; re-run the fflash line) gaps=$GAPS skipped=$SKIPPED"; exit 1; }
	case "$st" in *state=fastboot*) ;; *) GAPS=$((GAPS + 1)); echo "GBT GAP: phone does not rest in fastboot ($st)" ;; esac
}

abort() {   # abort WHY: record, restore, FAIL
	row "ABORT $1"; restore; echo "RESULT: FAIL golden-bt aborted: $1 (out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1
}
chk() {   # stop condition 4 (kptr_restrict GAP) and, with an arg, 3 (phone no longer android)
	[ "$ABORT" = 0 ] || abort "stop condition 4: kptr_restrict not restored"
	if [ -n "${1:-}" ]; then st=$(tools/phone-state 2>&1 | tail -1); case "$st" in *state=android*) ;; *) abort "stop condition 3: phone not android after $1 ($st)" ;; esac; fi
}

snoop_ok() {   # the pulled main snoop file exists, is non-empty and starts with the btsnoop magic (never printed)
	[ -s "$O2/bt/btsnoop_hci.log" ] && [ "$(head -c 8 "$O2/bt/btsnoop_hci.log" | od -An -tx1 | tr -d ' \n')" = 6274736e6f6f7000 ]
}
log "out $O"
# preflight: every action this script calls must be a case label of the tools/golden-pull of THIS clone; exit 2 before any erase/flash
miss=""
for a in identity su-prep provision-skip ui bt-static bt-toggle bt-debug bt-snoop-on bt-snoop-pull bt-logs; do have "$a" || miss="$miss $a"; done
[ -z "$miss" ] || { echo "RESULT: ERROR preflight: tools/golden-pull in $PWD lacks:$miss (nothing was erased or flashed)"; exit 2; }
# F6: same check as tools/fflash-userdata.sh (IMG.sha256 first field vs sha256sum), BEFORE the erase
[ -f "$IMG" ] && [ -f "$IMG.sha256" ] && [ "$(cut -d' ' -f1 "$IMG.sha256")" = "$(sha256sum "$IMG" | cut -d' ' -f1)" ] || { echo "RESULT: ERROR preflight: $IMG missing or does not match its .sha256 (nothing was erased or flashed)"; exit 2; }
tools/fboot-android --unattended --erase-userdata --yes-erase-userdata --dry-run 2>&1 | tail -1 | grep -q 'RESULT: PASS' || { echo "RESULT: FAIL step 0 fboot-android dry-run"; exit 1; }
# F3: from the erase on, any signal or exit runs restore once (restore is idempotent: RESTORED guards a double flash)
on_exit() { trap - EXIT INT TERM HUP; if [ "$ERASING" = 1 ] && [ "$RESTORED" = 0 ]; then row "SIGNAL/EXIT before restore"; restore; echo "RESULT: FAIL golden-bt interrupted gaps=$GAPS skipped=$SKIPPED$RS"; fi; exit 1; }
trap on_exit EXIT INT TERM HUP
ERASING=1   # set before the call: the erase may complete before a failure is reported
out=$(tools/fboot-android --unattended --erase-userdata --yes-erase-userdata 2>&1); rc=$?
echo "$out" | tail -4
if [ "$rc" != 0 ]; then
	if echo "$out" | grep -q 'erase userdata failed\|erased_userdata=1'; then log "fboot failed AFTER the erase"; restore; echo "RESULT: FAIL step 1 fboot rc=$rc after erase (restored) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
	echo "RESULT: FAIL step 1 fboot rc=$rc before the erase (nothing written)"; exit 1
fi

if ! waitboot; then row "0a FAIL ($st): boot 1 not authorized/up"; restore; echo "RESULT: FAIL golden-bt boot 1 (out $O) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
row "0a PASS boot 1 android, authorized"
gp 0a "$O" identity || true
gp 0a "$O" su-prep
log "boot 2, same image, no erase"
tools/phone goto fastboot --timeout 120 2>&1 | tail -1
prc=${PIPESTATUS[0]}
[ "$prc" = 0 ] || { row "0b FAIL goto fastboot rc=$prc"; restore; echo "RESULT: FAIL golden-bt boot 2 goto fastboot (out $O) gaps=$GAPS skipped=$SKIPPED$RS gapnames=[$GAPNAMES ]"; exit 1; }
tools/fboot-android --unattended 2>&1 | tail -1
prc=${PIPESTATUS[0]}
[ "$prc" = 0 ] || { row "0b FAIL second fboot-android rc=$prc"; restore; echo "RESULT: FAIL golden-bt boot 2 fboot-android (out $O) gaps=$GAPS skipped=$SKIPPED$RS gapnames=[$GAPNAMES ]"; exit 1; }
if ! waitboot; then row "0b FAIL boot 2 not authorized/up"; restore; echo "RESULT: FAIL golden-bt boot 2 (out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
row "0b PASS boot 2 android, authorized"
gp 0b "$O2" identity || true

# UNATTENDED provision (research 106): flags + SUW disabled + keyguard off, then the launcher must be the focus. No human wait: not on the launcher = ABORT.
# This script never taps. A read-only `ui dump` records dumpsys window mCurrentFocus into ui-windows-probeN.txt. Unknowns U1-U7 of research 106 are settled by this run's provision-*.txt files.
gp 2a "$O2" provision-skip || abort "provision-skip failed (see provision-*.txt in $O2)"
n=0; onlauncher=0
while [ "$n" -lt 2 ]; do
	gp 2a "$O2" ui dump "probe$n" || true   # F10: a failed dump/screencap is counted as a gap
	if grep -q 'mCurrentFocus.*nexuslauncher' "$O2/ui-windows-probe$n.txt" 2>/dev/null; then onlauncher=1; break; fi
	log "GBT: launcher is not the focus after provision-skip (see $O2/ui-windows-probe$n.txt)"
	n=$((n + 1)); [ "$n" -lt 2 ] && w 10
done
[ "$onlauncher" = 1 ] || abort "launcher not reachable after provision-skip (wizard/lockscreen still up)"
row "2a launcher focus confirmed (probe$n)"
chk "launcher probe"

# golden-bt-s0 (runplans/golden-bt-s0.md): passive BT captures. Nothing is written but the one persist.bluetooth.btsnooplogmode prop and the BT toggle.
gp bt "$O2" bt-static before || true
gp bt "$O2" bt-toggle off || true   # BT must be OFF for the "off" debugfs snapshot (research 107 Q3; an already-off adapter makes the wait return at once)
gp bt "$O2" bt-debug off || true
gp bt "$O2" bt-snoop-on || true     # setprop full, BT off, wait OFF, BT on, wait ON (a failed wait is a named GAP, the run goes on)
gp bt "$O2" bt-debug on || true
gp bt "$O2" bt-static after || true
chk "bt-static after"
GAPS_BEFORE_PULL=$GAPS
gp bt "$O2" bt-snoop-pull || true
FIRST_PULL_GAPS=$((GAPS - GAPS_BEFORE_PULL))
if ! snoop_ok; then   # Q1 prediction wrong (prop not applied without a reboot): ONE fallback, +3 min
	row "bt fallback B: snoop file missing/empty/bad magic after setprop+toggle: one goto fastboot + fboot-android + provision-skip + toggle off/on + pull"
	mv "$O2/bt/bt-snoop-size.txt" "$O2/bt/bt-snoop-size-attempt1.txt" 2>/dev/null
	tools/phone goto fastboot --timeout 120 2>&1 | tail -1
	prc=${PIPESTATUS[0]}
	[ "$prc" = 0 ] || abort "bt fallback: goto fastboot rc=$prc"
	tools/fboot-android --unattended 2>&1 | tail -1
	prc=${PIPESTATUS[0]}
	[ "$prc" = 0 ] || abort "bt fallback: fboot-android rc=$prc"
	waitboot || abort "bt fallback: phone not android after the reboot ($st)"
	gp bt "$O2" provision-skip || true
	gp bt "$O2" bt-toggle off || true
	gp bt "$O2" bt-toggle on || true
	gp bt "$O2" bt-snoop-pull || true
	if snoop_ok; then   # the first pull's gaps are superseded, not silent: loud line, name kept in RESULT
		log "bt fallback B WORKED: the $FIRST_PULL_GAPS gap(s) of the first bt-snoop-pull are superseded; Q1 answer: the prop needed a reboot"
		GAPS=$((GAPS - FIRST_PULL_GAPS)); RS="$RS fallback-B=used(Q1-no-reboot-was-wrong,superseded-gaps=$FIRST_PULL_GAPS)"
	else RS="$RS fallback-B=FAILED(no-snoop-file)"; fi
fi
gp bt "$O2" bt-logs || true         # dmesg + logcat -d -b all, filtered copies
chk "bt-logs"

restore
trap - EXIT INT TERM HUP
if [ "$GAPS" = "$EXP_GAPS" ] && [ "$SKIPPED" = "$EXP_SKIPPED" ]; then
	echo "RESULT: PASS golden-bt session done (rows in the GBT ROW lines; out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS gapnames=[$GAPNAMES ]"
else
	echo "RESULT: FAIL golden-bt gaps=$GAPS (expected $EXP_GAPS) skipped=$SKIPPED$RS gapnames=[$GAPNAMES ] (expected $EXP_SKIPPED) (rows in the GBT ROW lines; out $O, $O2)"; exit 1
fi
