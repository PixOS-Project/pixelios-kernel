#!/bin/bash
# golden-6b session (super docs/runplans/golden-6b.md; row 2a gpu-dpu-ui-busy of docs/runplans/golden-6.md). UNATTENDED after one hardware PASS (until then the env gate below stays). Run ONLY as:
#   cd <kernel clone> && GOLDEN6B_USER_PRESENT=yes tools/phone-lock run --owner golden --wait 3600 --ttl 3600 -- bash ~/pixelios-artifacts/golden/golden-6b.sh
# Shape = golden-6.sh (same preflight, restore, waitboot, gp, chk). No su-check, no boot-capture, no Wi-Fi, no inventory.
# Writes nothing but userdata (fboot erase, final inj8 re-flash). Ends with RESULT: PASS|FAIL|ERROR carrying gaps= and skipped=.
# GOLDEN6B_USER_PRESENT=yes is set by the person starting the run, NEVER here. GP_UI_ATTENDED is set below only on the `ui swipe` command lines.
set -u
unset GP_UI_ATTENDED
cd "${PIXOS_KERNEL:-$HOME/.t3/projects/pixelios/pixos-project/pixelios-kernel}" || { echo "RESULT: ERROR no kernel clone"; exit 2; }
# attended gate: before anything touches the phone (exit 2 = nothing erased or flashed)
[ "${GOLDEN6B_USER_PRESENT-}" = "yes" ] || { echo "G6B ROW 2a: not attended; GOLDEN6B_USER_PRESENT=yes must be set by the person starting the run"; echo "RESULT: ERROR golden-6b refuses: no user present (nothing was erased or flashed) gaps=0 skipped=0"; exit 2; }
O=~/pixelios-artifacts/golden/g6b-$(date -u +%Y%m%dT%H%M%SZ); O2=$O-b2
IMG=/tank/pixelios/ios-root4-inj8.img
RS=""; GAPS=0; SKIPPED=0; ABORT=0; ERASING=0; RESTORED=0; MINSWIPES=10; WINSECS=60
# Expected totals (as golden-6.sh:13): identity 2 gaps per boot x 2 boots = +4 EXPECTED (cmdline.txt empty, cmdline.txt cause unproven, rule 8); set 0 only after an observed run shows identity gaps=0. Any other gap is a FAIL.
EXP_GAPS=4; EXP_SKIPPED=0
log() { echo "G6B $(date -u +%H:%M:%S) $*"; }
row() { log "ROW $*"; }

have() {   # match the real case label (or the MMIO_MODS key for mmio-filter-*), not usage/comment text (golden-6.sh F9)
	grep -qE "^[[:space:]]*([a-z0-9-]+\|)*$1(\|[a-z0-9-]+)*\)" tools/golden-pull && return 0
	case $1 in mmio-filter-*) grep -qE "\[${1#mmio-filter-}\]=\"" tools/golden-pull ;; *) return 1 ;; esac
}
gp() {   # gp ROW OUTDIR ACTION [args]: one golden-pull action, prints its RESULT, counts gaps, returns its rc
	local r=$1 d=$2 a=$3 res g rc; shift 3
	res=$(tools/golden-pull --out "$d" "$a" "$@" 2>&1); rc=$?
	echo "$res" | grep '^GAP:' | head -20 | sed "s/^/G6B $a: /"
	echo "$res" | tail -1 | sed "s/^/G6B $a: /"
	g=$(echo "$res" | tail -1 | sed -n 's/.* gaps=\([0-9]*\).*/\1/p'); g=${g:-0}
	echo "$res" | grep -q 'kptr_restrict not restored' && ABORT=1   # stop condition 4
	if [ "$rc" != 0 ] || [ "$g" != 0 ]; then GAPS=$((GAPS + (g > 0 ? g : 1))); echo "G6B ROW $r GAP $a gaps=$g rc=$rc"; fi
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
		case "$st" in *state=fastboot*) ;; *) GAPS=$((GAPS + 1)); echo "G6B GAP: phone does not rest in fastboot ($st)" ;; esac
		return 0
	fi
	log "restore: re-flash inj8"
	tools/fflash-userdata.sh "$IMG" --yes-erase-userdata --force --reason "golden-6b restore: re-flash the iOS root after the Android userdata erase" 2>&1 | tail -3
	fr=${PIPESTATUS[0]}
	st=$(tools/phone-state 2>&1 | tail -1); row "7 fflash rc=$fr; $st"
	[ "$fr" = 0 ] || { echo "RESULT: FAIL restore re-flash rc=$fr (phone has an erased userdata; re-run the fflash line) gaps=$GAPS skipped=$SKIPPED"; exit 1; }
	case "$st" in *state=fastboot*) ;; *) GAPS=$((GAPS + 1)); echo "G6B GAP: phone does not rest in fastboot ($st)" ;; esac
}

abort() {   # abort WHY: record, restore, FAIL
	row "ABORT $1"; restore; echo "RESULT: FAIL golden-6b aborted: $1 (out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1
}
chk() {   # stop condition 4 (kptr_restrict GAP) and, with an arg, 3 (phone no longer android)
	[ "$ABORT" = 0 ] || abort "stop condition 4: kptr_restrict not restored"
	if [ -n "${1:-}" ]; then st=$(tools/phone-state 2>&1 | tail -1); case "$st" in *state=android*) ;; *) abort "stop condition 3: phone not android after $1 ($st)" ;; esac; fi
}

SW_OK=0; SW_RETRY=0
swipe() {   # swipe X1 Y1 X2 Y2 MS: one separate `ui swipe`; a ui-guard refusal or any failure = ABORT (no more input), except the tool's own documented retryable "screen changed" refusal (max 2 per window)
	local res rc
	res=$(GP_UI_ATTENDED=yes-a-human-watches-the-screen timeout 90 tools/golden-pull --out "$O2" ui swipe "$@" 2>&1); rc=$?
	if [ "$rc" = 0 ] && echo "$res" | tail -1 | grep -q '^RESULT: PASS ui gaps=0'; then SW_OK=$((SW_OK + 1)); return 0; fi
	echo "$res" | tail -3 | sed "s/^/G6B ui swipe $*: /"
	if echo "$res" | grep -q 'the screen changed between the guard dump and the action' && [ "$SW_RETRY" -lt 2 ]; then
		SW_RETRY=$((SW_RETRY + 1)); log "G6B RETRY $SW_RETRY/2: tool-documented retryable refusal (status-bar clock [guess]); loud, counted as a gap"; GAPS=$((GAPS + 1)); return 0
	fi
	abort "ui swipe refused or failed (rc=$rc): stop swiping (foreground not allowlisted, dialog, lockscreen, wizard or tool error)"
}
uikey() {   # key HOME is allowed unattended (golden-pull ui key), so no GP_UI_ATTENDED here; a failed key is a counted gap (F5)
	local o rc; o=$(timeout 90 tools/golden-pull --out "$O2" ui key "$1" 2>&1); rc=$?
	echo "$o" | tail -1 | sed "s/^/G6B ui key $1: /"
	[ "$rc" = 0 ] || { GAPS=$((GAPS + 1)); echo "G6B GAP: ui key $1 rc=$rc"; }
}

ui_window() {   # ui_window ROW MARK FILTER-ACTION SUFFIX: busy-screen window, ~WINSECS s of attended swipes (screen 1080x2400, bounds [0,0][1080,2400] [obs g3 ui7b xml])
	local r=$1 m=$2 f=$3 s=$4 t0
	SW_OK=0; SW_RETRY=0
	gp "$r" "$O2" mmio-on && gp "$r" "$O2" "$f" || abort "window $m: trace setup (mmio-on/filter) failed, no swipe sent"
	chk
	uikey HOME; w 2
	gp "$r" "$O2" start && gp "$r" "$O2" mark "$m" || abort "window $m: trace setup (start/mark) failed, no swipe sent"
	t0=$SECONDS
	while [ $((SECONDS - t0)) -lt "$WINSECS" ] || { [ "$SW_OK" -lt "$MINSWIPES" ] && [ $((SECONDS - t0)) -lt 420 ]; }; do   # F2: count in the condition, hard cap 420 s
		# F8: vertical-only gestures, no horizontal swipe (a right swipe on the first page may open the Google feed, a non-allowlisted package [guess]); a refusal is an ABORT with restore anyway
		# r3 (run 2 obs g6b-20261028T183753Z-b2): a swipe from y=1900 starts ABOVE the hotseat ([0,1905][1080,2400]) so it did not open the drawer, and the following DOWN swipe on the home screen opened the notification shade
		# ("USB debugging connected" -> ui guard abort). So: open the drawer from inside the hotseat (y=2150), and NO downward swipe at all (upward only: drawer scroll, never pulls the shade).
		swipe 540 2150 540 700 300                                   # open the app drawer from the hotseat
		swipe 540 1800 540 600 400; swipe 540 1800 540 600 400      # app-drawer scroll
		swipe 540 1800 540 600 400; swipe 540 2150 540 700 300
		uikey HOME
	done
	gp "$r" "$O2" mark "$m" "done"; gp "$r" "$O2" stop-pull "$s"
	row "$r window $m: $SW_OK swipes PASS in $((SECONDS - t0)) s, retries=$SW_RETRY (need >= $MINSWIPES); verify greps from the run plan on $O2"
	if [ "$SW_OK" -lt "$MINSWIPES" ]; then GAPS=$((GAPS + 1)); echo "G6B GAP: window $m has only $SW_OK ui swipe PASS (< $MINSWIPES)"; fi
	chk "window $m"
}

log "out $O"
# preflight: every action this script calls must be a case label of the tools/golden-pull of THIS clone; exit 2 before any erase/flash
miss=""
for a in identity su-prep provision-skip mmio-on mmio-filter-gpu-pmu mmio-filter-dpu start mark stop-pull ui; do have "$a" || miss="$miss $a"; done
[ -z "$miss" ] || { echo "RESULT: ERROR preflight: tools/golden-pull in $PWD lacks:$miss (nothing was erased or flashed)"; exit 2; }
# F6: same check as tools/fflash-userdata.sh (IMG.sha256 first field vs sha256sum), BEFORE the erase
[ -f "$IMG" ] && [ -f "$IMG.sha256" ] && [ "$(cut -d' ' -f1 "$IMG.sha256")" = "$(sha256sum "$IMG" | cut -d' ' -f1)" ] || { echo "RESULT: ERROR preflight: $IMG missing or does not match its .sha256 (nothing was erased or flashed)"; exit 2; }
tools/fboot-android --unattended --erase-userdata --yes-erase-userdata --dry-run 2>&1 | tail -1 | grep -q 'RESULT: PASS' || { echo "RESULT: FAIL step 0 fboot-android dry-run"; exit 1; }
# F3: from the erase on, any signal or exit runs restore once (restore is idempotent: RESTORED guards a double flash)
on_exit() { trap - EXIT INT TERM HUP; if [ "$ERASING" = 1 ] && [ "$RESTORED" = 0 ]; then row "SIGNAL/EXIT before restore"; restore; echo "RESULT: FAIL golden-6b interrupted gaps=$GAPS skipped=$SKIPPED$RS"; fi; exit 1; }
trap on_exit EXIT INT TERM HUP
ERASING=1   # set before the call: the erase may complete before a failure is reported
out=$(tools/fboot-android --unattended --erase-userdata --yes-erase-userdata 2>&1); rc=$?
echo "$out" | tail -4
if [ "$rc" != 0 ]; then
	if echo "$out" | grep -q 'erase userdata failed\|erased_userdata=1'; then log "fboot failed AFTER the erase"; restore; echo "RESULT: FAIL step 1 fboot rc=$rc after erase (restored) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
	echo "RESULT: FAIL step 1 fboot rc=$rc before the erase (nothing written)"; exit 1
fi

if ! waitboot; then row "0a FAIL ($st): boot 1 not authorized/up"; restore; echo "RESULT: FAIL golden-6b boot 1 (out $O) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
row "0a PASS boot 1 android, authorized"
gp 0a "$O" identity || true
gp 0a "$O" su-prep
log "boot 2, same image, no erase"
tools/phone goto fastboot --timeout 120 2>&1 | tail -1
prc=${PIPESTATUS[0]}
[ "$prc" = 0 ] || { row "0b FAIL goto fastboot rc=$prc"; restore; echo "RESULT: FAIL golden-6b boot 2 goto fastboot (out $O) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; }
tools/fboot-android --unattended 2>&1 | tail -1
prc=${PIPESTATUS[0]}
[ "$prc" = 0 ] || { row "0b FAIL second fboot-android rc=$prc"; restore; echo "RESULT: FAIL golden-6b boot 2 fboot-android (out $O) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; }
if ! waitboot; then row "0b FAIL boot 2 not authorized/up"; restore; echo "RESULT: FAIL golden-6b boot 2 (out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS"; exit 1; fi
row "0b PASS boot 2 android, authorized"
gp 0b "$O2" identity || true

# UNATTENDED provision (research 106): flags + SUW disabled + keyguard off, then the launcher must be the focus. No human wait: not on the launcher = ABORT.
# This script never taps. A read-only `ui dump` records dumpsys window mCurrentFocus into ui-windows-probeN.txt. Unknowns U1-U7 of research 106 are settled by this run's provision-*.txt files.
gp 2a "$O2" provision-skip || abort "provision-skip failed (see provision-*.txt in $O2): no swipe was sent"
n=0; onlauncher=0
while [ "$n" -lt 2 ]; do
	gp 2a "$O2" ui dump "probe$n" || true   # F10: a failed dump/screencap is counted as a gap
	if grep -q 'mCurrentFocus.*nexuslauncher' "$O2/ui-windows-probe$n.txt" 2>/dev/null; then onlauncher=1; break; fi
	log "G6B: launcher is not the focus after provision-skip (see $O2/ui-windows-probe$n.txt)"
	n=$((n + 1)); [ "$n" -lt 2 ] && w 10
done
[ "$onlauncher" = 1 ] || abort "launcher not reachable after provision-skip (wizard/lockscreen still up): no swipe was sent"
row "2a launcher focus confirmed (probe$n)"
chk "launcher probe"

ui_window 2a-gpu gpu-ui mmio-filter-gpu-pmu gpuui
ui_window 2a-dpu dpu-ui mmio-filter-dpu dpuui

restore
trap - EXIT INT TERM HUP
if [ "$GAPS" = "$EXP_GAPS" ] && [ "$SKIPPED" = "$EXP_SKIPPED" ]; then
	echo "RESULT: PASS golden-6b session done (rows in the G6B ROW lines; out $O, $O2) gaps=$GAPS skipped=$SKIPPED$RS"
else
	echo "RESULT: FAIL golden-6b gaps=$GAPS (expected $EXP_GAPS) skipped=$SKIPPED$RS (expected $EXP_SKIPPED) (rows in the G6B ROW lines; out $O, $O2)"; exit 1
fi
