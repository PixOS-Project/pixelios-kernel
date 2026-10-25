#!/bin/bash
# Flash the iOS root to userdata, once per image. DESTROYS userdata: pass --yes-erase-userdata after the user confirmed.
#   tools/fflash-userdata.sh IMG --yes-erase-userdata [--force]
# Refuses: a caller that does not hold the phone lock (TP-020), no IMG.sha256 / hash mismatch, the same image already flashed (state file) unless --force, not bluejay/unlocked.
set -euo pipefail
cd "$(dirname "$0")/.."
img=""; yes=0; force=0
for a in "$@"; do case "$a" in --yes-erase-userdata) yes=1 ;; --force) force=1 ;; *) img=$a ;; esac; done
[ -f "$img" ] && [ -f "$img.sha256" ] || { echo "usage: $0 IMG --yes-erase-userdata (needs IMG.sha256)" >&2; exit 2; }
[ "$yes" = 1 ] || { echo "REFUSED: this erases userdata; pass --yes-erase-userdata after the user confirmed" >&2; exit 1; }
want=$(cut -d' ' -f1 "$img.sha256"); got=$(sha256sum "$img" | cut -d' ' -f1)
[ "$want" = "$got" ] || { echo "REFUSED: $img does not match its .sha256" >&2; exit 1; }
state=${FLASH_STATE:-$HOME/pixelios-artifacts/flashed-userdata.txt}
if [ "$force" = 0 ] && grep -q "^$got " "$state" 2>/dev/null; then
	echo "REFUSED: this exact image was already flashed ($(grep "^$got " "$state" | tail -1)); use --force to flash it again" >&2; exit 1; fi
tools/phone-lock check >&2 || { echo "REFUSED: the phone lock is not held by you (tools/phone-lock run --owner NAME -- CMD, or use tools/xrun / tools/probe-run, which take it after the build)" >&2; exit 1; }
prod=$(timeout 15 fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1)
unl=$(timeout 15 fastboot getvar unlocked 2>&1 | sed -n 's/^unlocked: *//p' | head -1)
[ "$prod" = bluejay ] && [ "$unl" = yes ] || { echo "REFUSED: product='$prod' unlocked='$unl'" >&2; exit 1; }
# M7 (docs/PLAN-BOOT-SPEED.md): keep fastboot's own output and print what was really sent, to settle whether holes in the image are skipped
mkdir -p logs; fl=logs/flash-$(date -u +%Y%m%dT%H%M%SZ).log; t0=$(date +%s)
fastboot flash userdata "$img" 2>&1 | tee "$fl"
echo "flash stats: $(grep -c "^Sending sparse" "$fl") sparse part(s) sent of $(grep -o "Sending sparse 'userdata' [0-9]*/[0-9]*" "$fl" | tail -1 | grep -o "/[0-9]*" | tr -d /) , $(( $(date +%s) - t0 )) s, image $(stat -c %s "$img") bytes apparent / $(( $(stat -c %b "$img") * 512 )) allocated (log $fl)"
echo "$got $(date -u +%FT%TZ) $img" >> "$state"
echo "flashed; recorded in $state"
