#!/bin/bash
# One hardware experiment, no human needed (Pixel 6a "bluejay" only):
#   tools/run-probe.sh IMAGE [CAPTURE_SECONDS]
# adb reboot bootloader (or use the phone if already in fastboot) -> start the
# USB serial capture -> fastboot boot IMAGE (temporary, nothing is flashed) ->
# wait for the log to end -> wait for Android to come back (the shim resets the
# phone when it finishes). The log is saved as logs/usb-<UTC>.txt.
# Refuses to act unless the connected device reports the expected codename (BOARD, default bluejay).
# Needs a build with --usb-gadget; for fast cycles also --pstore-fast and
# USB_WINDOW=90 (see shim/build.sh).
set -u
board=${BOARD:-bluejay}	# device codename the safety checks expect (tools/pix devices)
cd "$(dirname "$0")/.." || exit 2
img=${1:?usage: tools/run-probe.sh IMAGE [CAPTURE_SECONDS]}
secs=${2:-300}
mkdir -p logs
die() { echo "run-probe: $*" >&2; exit 1; }
[ -f "$img" ] || die "no such image: $img"
"$(dirname "$0")/phone-lock" check >&2 || die "the phone lock is not held by you: run this under tools/phone-lock run --owner NAME -- tools/run-probe.sh ..."

if [ "$(adb get-state 2>/dev/null)" = device ]; then
	[ "$(adb shell getprop ro.product.device 2>/dev/null | tr -d '\r')" = "$board" ] || die "adb device is not $board"
	adb reboot bootloader
fi
for _ in $(seq 1 45); do fastboot devices 2>/dev/null | grep -q . && break; sleep 2; done
fastboot devices | grep -q . || die "no fastboot device: put the phone in the bootloader (Power+VolDown)"
fastboot getvar product 2>&1 | grep -q "product: $board" || die "fastboot device is not $board"

before=$(ls logs/usb-*.txt 2>/dev/null | wc -l)
timeout $((secs + 30)) python3 tools/usb-log.py --timeout "$secs" capture > logs/capture-stdout.txt 2>&1 &
cap=$!
sleep 1
fastboot boot "$img" || { kill $cap 2>/dev/null; die "fastboot boot failed"; }
wait $cap
log=$(ls -t logs/usb-*.txt 2>/dev/null | head -1)
[ "$(ls logs/usb-*.txt 2>/dev/null | wc -l)" -gt "$before" ] || echo "run-probe: no new log (device never enumerated?)" >&2
echo "run-probe: log = ${log:-none}"
for _ in $(seq 1 120); do [ "$(adb get-state 2>/dev/null)" = device ] && { echo "run-probe: Android is back"; exit 0; }; sleep 2; done
echo "run-probe: phone did not return to Android in 240 s (hung? hold Power, then Power+VolDown)" >&2
exit 3
