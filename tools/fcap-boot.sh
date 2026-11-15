#!/bin/bash
# fcap-boot LABEL [XRUN ARGS...]: one GPU capture boot inside ONE phone-lock hold (sprint GPU 2026-11-09). Run it under
#   tools/phone-lock run --owner sprint-gpu --wait 5400 -- tools/fcap-boot LABEL ...
# 1. the standard root (standard-root.txt) on userdata: flash it only if userdata holds another image, then one
#    plain settle boot (the first boot after a flash is iOS migration with a progress bar for the whole session, gputest-fcap5)
# 2. tools/fcap-pull beside tools/xrun --intent gpu with the kernel, overlay and args below.
# Last line: the xrun RESULT lines, then RESULT: PASS|FAIL fcap-boot.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -u
if [ "${1:-}" = --self-test ]; then bash -n "$0" && echo "RESULT: PASS fcap-boot self-test (syntax)" && exit 0; echo "RESULT: FAIL fcap-boot self-test"; exit 1; fi
LABEL=$1; shift
HERE=$(cd "$(dirname "$0")/.." && pwd -P)
# standard root: ONE source, ${STANDARD_ROOT:-~/pixelios-artifacts/standard-root.txt} (`<sha256> <path>`, changed only by `tools/flash_cache.py promote`, 2026-11-11);
# a hardcoded sha here flipped the phone back to an old root after a promotion would have happened
STDF=${STANDARD_ROOT:-${ARTIFACTS:-$HOME/pixelios-artifacts}/standard-root.txt}
read -r STD_FULL IMG < <(grep -v '^[[:space:]]*\(#\|$\)' "$STDF" 2>/dev/null | head -1)
[[ ${STD_FULL:-} =~ ^[0-9a-f]{64}$ ]] && [ -f "${IMG:-}" ] || { echo "fcap-boot GAP: no valid standard root in $STDF (sha='${STD_FULL:-}' path='${IMG:-}')"; echo "RESULT: FAIL fcap-boot no standard root"; exit 1; }
STD_SHA=${STD_FULL:0:12}
KERNEL=${FCAP_KERNEL:-$HOME/pixelios-artifacts/localbuild/out/sprint-gpu-fix12/kernel.development.sweet}
OV=${FCAP_OVERLAY:-$HOME/pixelios-artifacts/ov-fcap4}
OUT=$HOME/pixelios-artifacts/fcap/$LABEL
export XRUN_NO_RUNPLAN='user 2026-11-08 no run plans' PREFLIGHT_GATE_WAIVER='user 2026-11-02 + 2026-11-08'
last=$(tail -1 "$HOME/pixelios-artifacts/flashed-userdata.txt" 2>/dev/null)
echo "fcap-boot: last flashed: $last"
case "$last" in
  "$STD_FULL "*) echo "fcap-boot: standard root $STD_SHA on userdata: no flash" ;;
  *)
    (cd "$HERE" && tools/fflash-userdata.sh "$IMG" --yes-erase-userdata --reason "sprint-gpu $LABEL: standard root $STD_SHA back (userdata held another image)") 2>&1 | tail -3
    echo "fcap-boot: settle boot after the flash (iOS migration)"
    (cd "$HERE" && tools/xrun --kernel "$KERNEL" --intent gpu --label "$LABEL-settle" --duration 600 --overlay "$OV" --compress 2>&1 | grep RESULT | tail -3)
    ;;
esac
# battery gate (2026-11-10: the phone died twice during long iOS sessions; under PixOS it charges only in fastboot): wait in fastboot until
# the battery reads >= FCAP_MIN_MV (3800 mV) before booting; every reading is logged; no reading = GAP, the boot goes ahead
MIN_MV=${FCAP_MIN_MV:-3800}
for i in $(seq 1 36); do
  prod=$(timeout 20 fastboot getvar product 2>&1 | sed -n 's/^product: //p')
  [ "$prod" = bluejay ] || { echo "SWEET GAP: fcap-boot battery gate: fastboot product '${prod:-none}' (not bluejay or not in fastboot), no reading"; break; }
  mv=$(timeout 20 fastboot getvar battery-voltage 2>&1 | sed -n 's/^battery-voltage: \([0-9]*\).*/\1/p')
  echo "fcap-boot: battery ${mv:-?} mV (gate $MIN_MV)"
  [ -z "$mv" ] && { echo "SWEET GAP: fcap-boot battery gate: no battery-voltage reading"; break; }
  [ "$mv" -ge "$MIN_MV" ] && break
  [ "$i" = 36 ] && { echo "RESULT: FAIL fcap-boot battery still ${mv} mV after 3 h in fastboot"; exit 1; }
  sleep 300
done
# settle rule (user 2026-11-10 via the coordinator): the first boot after a root flash is never a measured boot (iOS migration screen /
# first-launch work: gputest-fcap5, gputest-recnd1). If the standard root has not booted since its flash, boot it once to settle first.
nb=$(cd "$HERE" && python3 tools/overlay_std.py boots 2>/dev/null)
case "$nb" in
  "BOOTS 0") echo "fcap-boot: settle boot: the standard root has not booted since its flash"
             (cd "$HERE" && tools/xrun --kernel "$KERNEL" --intent gpu --label "$LABEL-settle" --duration 600 --overlay "$OV" --compress 2>&1 | grep RESULT | tail -3) ;;
  "BOOTS "[0-9]*) echo "fcap-boot: standard root booted ${nb#BOOTS } time(s) since its flash: no settle boot" ;;
  *) echo "SWEET GAP: fcap-boot settle rule: boot count unknown (${nb:-no answer}); no settle boot" ;;
esac
mkdir -p "$OUT"
cd "$HERE" && tools/fcap-pull.sh "$LABEL" "$OUT" > "$OUT/pull.log" 2>&1 &
PULL=$!
# measured reps ~350 s + capture rep ~220 s + 420 s pull window (patch 0275 holds the done line) -> 1150 s; the phone resets at 1190 s
EX=${FCAP_EXTRA_S:-0}   # extra seconds for a later nav start (sweetnavstart above the 130 s default): duration, hard timeout and restart move together
# foreign-app guard (coordinator 2026-11-11, 3 of 8 invalid boots were first-run/foreign screens: recnd1 migration, recns1 Weather prompt,
# census1 + nameq1 Apple News welcome): the GPU nav only ever execs Preferences (settings-open) and MobileTimer (app-launch = Clock); MobileCal
# starts by itself before the nav [obs fbo1/census1/nameq1 'exec' lines]. Any other app exec means a tap landed elsewhere: end the boot at once
# (xrun --fail-on), scored INVALID below. --fail-on replaces xrun's gpu defaults, so they are repeated here (tools/xrun gpu block).
FOREIGN='(^|\s|[|])exec (News|Weather|Tips|Stocks|MobileSafari|Maps|Health|Fitness|Books|Podcasts|Music|MobileSlideShow|Camera|Translate|Freeform|MobileNotes|Reminders|Contacts|Home|Passbook|FindMy|Shortcuts|Magnifier|VoiceMemos|Measure|Compass|Calculator|AppStore|FaceTime|MobileMail|MobileSMS|MobilePhone|TVApp|Setup|Bridge|Feedback)\s*$'
FAILS=(--fail-on 'GPU GAP: PLL' --fail-on 'hfs_mountroot failed' --fail-on 'Kernel trap at|panic\(cpu|panic:|Assertion failed' --fail-on "$FOREIGN")
[ "${FCAP_FOREIGN_GUARD:-1}" = 0 ] && FAILS=()
tools/xrun --kernel "$KERNEL" --intent gpu --label "$LABEL" --duration $((1150 + EX)) --hard-timeout $((1180 + EX)) --restart $((1190 + EX)),bootloader --overlay "$OV" --compress ${FAILS[@]+"${FAILS[@]}"} "$@"
rc=$?
RD=$(ls -dt logs/runs/*-"$LABEL" 2>/dev/null | head -1)
FA=$([ -n "$RD" ] && grep -E -m1 "$FOREIGN" "$RD/usbnet.log" 2>/dev/null | sed 's/.*exec //')
[ -n "$FA" ] && echo "RESULT: FAIL fcap-boot $LABEL INVALID foreign app '$FA' launched during the nav (tap off target or a first-run screen); boot ended early"
# the run is over (phone back in fastboot): the puller must not keep the hold (gputest-fcap6 held the lock 61 min retrying)
kill "$PULL" 2>/dev/null; pkill -P "$PULL" 2>/dev/null; wait "$PULL" 2>/dev/null
tail -3 "$OUT/pull.log"
# screen check (user 2026-11-10 via the coordinator): every panel snapshot must be the screen the nav meant to reach; a FAIL marks the boot INVALID
(cd "$HERE" && python3 tools/fcap-screens.py "$OUT" 2>&1 | tee "$OUT/screens.txt" | grep -E "WRONG|RESULT")
echo "RESULT: $([ $rc = 0 ] && echo PASS || echo FAIL) fcap-boot $LABEL rc=$rc"
exit $rc
