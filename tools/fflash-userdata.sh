#!/bin/bash
# Flash the iOS root to userdata, once per image. DESTROYS userdata: pass --yes-erase-userdata after the user confirmed.
#   tools/fflash-userdata.sh IMG --yes-erase-userdata --reason "WHY THE ROOT IMAGE CHANGED" [--force]
# No root ping-pong (user 2026-11-10, super docs/recipes/no-flash.md): an image that is not the STANDARD root (${STANDARD_ROOT:-<artifacts>/standard-root.txt},
# first field = its sha256) is refused unless --not-overlayable "WHICH PATHS DIFFER AND WHY NO OVERLAY CAN CARRY THEM" (>= 10 chars, one line; kept in the
# reasons file). The standard root itself always passes this check; no standard-root.txt = a loud GAP line and the check is skipped.
# R54 (user 2026-10-31): a flash only when the root image itself changed; --reason (>= 10 chars, one line) is required, checked FIRST, and kept in
# ${FLASH_REASONS:-${ARTIFACTS:-$HOME/pixelios-artifacts}/flash-reasons.tsv} (UTC<TAB>sha256<TAB>reason); flashed-userdata.txt keeps its format.
# Flash cache (user 2026-11-11): a verified sparse copy of the same sha256 on the SSD (tools/flash_cache.py prep) is sent instead of the raw image; see that header.
# Every outcome ends with one RESULT line: PASS (exit 0), FAIL (1), ERROR (usage, 2).
# Refuses: no/short --reason, no formal GATE PASS on the exact clean head (T-f32e; a quick gate is not enough), an image bigger than the userdata partition (board.json, symlinks followed), an Android sparse image, a caller that does not hold the phone lock (TP-020), no IMG.sha256 / hash mismatch, the same image already flashed (state file) unless --force, not bluejay/unlocked.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
img=""; yes=0; force=0; reason=""; havereason=0; fin=0; notov=""; havenotov=0
refuse() { fin=1; echo "REFUSED: $*" >&2; echo "RESULT: FAIL $*"; exit 1; }
# rule 8: a failure that is not a REFUSED line (fastboot flash failing, a crash under set -e) still ends with a RESULT line
on_exit() { local rc=$?; [ "$rc" = 0 ] || [ "$fin" = 1 ] || echo "RESULT: FAIL rc=$rc (fastboot flash or an internal command failed, see above; a started flash leaves the newest flashed-userdata.txt line as flashing)"; }
trap on_exit EXIT
# review flash-guard-t3360 F2: a signal during the flash ends with the RESULT line too (the state file stays "flashing": fails closed)
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
printable() { local LC_ALL=C; [[ $1 != *[!\ -~]* ]]; }   # review F4: printable ASCII only, whatever the locale
while [ $# -gt 0 ]; do case "$1" in
	--yes-erase-userdata) yes=1 ;; --force) force=1 ;;
	--reason) havereason=1; reason=${2-}; [ $# -gt 1 ] && shift ;;
	--not-overlayable) havenotov=1; notov=${2-}; [ $# -gt 1 ] && shift ;;
	*) img=$1 ;; esac; shift; done
# R54: checked before every other check. The text is one line without control chars (the tab-separated reasons file stays parseable).
rt=${reason#"${reason%%[![:space:]]*}"}; rt=${rt%"${rt##*[![:space:]]}"}
if [ "$havereason" = 0 ] || [ "${#rt}" -lt 10 ] || ! printable "$reason"; then
	refuse "--reason \"TEXT\" is required (>= 10 characters, one line): a userdata flash costs minutes of wall time and erases userdata, and most changes do not need one (R54). Instead: a userland binary change: \`tools/devsh push\` + \`devsh run\` (hot reload); a kernel/shim/patch change: RAM boot with \`tools/xrun\`; only a changed root image needs a flash. Escape: pass --reason \"<why the root image changed, >= 10 chars>\""
fi
[ -f "$img" ] && [ -f "$img.sha256" ] || { fin=1; echo "usage: $0 IMG --yes-erase-userdata --reason \"TEXT\" (needs IMG.sha256)" >&2; echo "RESULT: ERROR usage: IMG with IMG.sha256 is required"; exit 2; }
[ "$yes" = 1 ] || { refuse "this erases userdata; pass --yes-erase-userdata after the user confirmed"; }
# T-f32e (user order 2026-11-02): a quick gate admits RAM boots only; flashing userdata needs the FORMAL gate on this exact clean head (tools/run-preflight --gate-check flash;
# a user waiver PREFLIGHT_GATE_WAIVER="reason >= 10 chars" is the only bypass and is printed). Before any other check, nothing is written yet.
python3 -I tools/run-preflight --gate-check flash >&2 || { refuse "no formal GATE PASS for this head (see the line above); run tools/gate --formal, a quick gate does not admit a userdata flash"; }
# The image must fit the userdata partition: board.json userdata_mib is the OBSERVED GPT size (no fastboot call, nothing written yet);
# otherwise fastboot fails part-way through the erase. Checked before the hash, the lock and any fastboot command.
mib=$(python3 -I tools/boards.py bluejay --get userdata_mib 2>/dev/null) || { refuse "no userdata_mib for bluejay (device/google/bluejay/board.json)"; }
case $mib in ''|*[!0-9]*) refuse "userdata_mib for bluejay is not a number: '$mib'" ;; esac
# -L: the size of what fastboot will send, not of a symlink to it (review fork-fixes F1). id0 (device inode size mtime) is compared again right
# before the flash (review fork-fixes-r2 F3): a file rewritten, grown or relinked after these checks and the hash is refused, not flashed
# %.Y %.Z (r3 F1): nanosecond mtime plus ctime, which userspace cannot set back, so a same-size in-place rewrite (same second, or touch -r) is caught too
id0=$(stat -L -c '%d %i %s %.Y %.Z' -- "$img") || { refuse "cannot stat $img"; }; bytes=$(echo "$id0" | cut -d' ' -f3)
[ "$bytes" -le $(( mib * 1048576 )) ] || { refuse "$img is $bytes bytes, the bluejay userdata partition is $mib MiB"; }
# an Android sparse image (magic 0xED26FF3A, little-endian) expands beyond its file size, so the check above says nothing about it: raw images only (review F2)
[ "$(head -c 4 -- "$img" | od -An -tx1 | tr -d ' \n')" != 3aff26ed ] || { refuse "$img is an Android sparse image; pass the raw image (fastboot re-sparses it itself)"; }
# Flash cache (user 2026-11-11, tools/flash_cache.py, super docs/recipes/phone-run.md "Flash cache"): the hash of an image unchanged since its last hash
# (dev/inode/size/mtime/ctime equal) comes from a stamp, not a 17 GB re-read; a missing or failing helper falls back to sha256sum with a GAP line.
fc=tools/flash_cache.py
cdir=${FLASH_CACHE_DIR:-${ARTIFACTS:-$HOME/pixelios-artifacts}/flash-cache}
want=$(cut -d' ' -f1 "$img.sha256")
if [ -f "$fc" ]; then
	got=$(python3 -I "$fc" hash "$img") || { echo "FLASH CACHE GAP: $fc hash failed rc=$?: plain sha256sum" >&2; got=$(sha256sum -- "$img" | cut -d' ' -f1); }
else echo "FLASH CACHE GAP: $fc missing: plain sha256sum, no cache" >&2; got=$(sha256sum -- "$img" | cut -d' ' -f1); fi
[ "$want" = "$got" ] || { refuse "$img does not match its .sha256"; }
# no root ping-pong (user 2026-11-10): a non-standard root needs --not-overlayable; checked before the lock and any fastboot command
stdf=${STANDARD_ROOT:-${ARTIFACTS:-$HOME/pixelios-artifacts}/standard-root.txt}; std=
if [ -r "$stdf" ]; then std=$(grep -v '^[[:space:]]*\(#\|$\)' "$stdf" | head -1 | cut -d' ' -f1); fi
if ! [[ $std =~ ^[0-9a-f]{64}$ ]]; then
	echo "fflash-userdata GAP: no standard root sha256 in $stdf: the non-standard-root check is skipped (write '<sha256> <path>' there)" >&2
elif [ "$got" != "$std" ]; then
	nt=${notov#"${notov%%[![:space:]]*}"}; nt=${nt%"${nt##*[![:space:]]}"}
	if [ "$havenotov" = 0 ] || [ "${#nt}" -lt 10 ] || ! printable "$notov"; then
		refuse "$img (${got:0:12}) is not the standard root (${std:0:12}, $stdf). A per-stream root flashed and later flashed back costs ~5-7 min of phone per flash and makes the next 2 boots of every thread not comparable. Instead: put the differing files in your run's overlay (tools/xrun --overlay TREE) or tools/hot push them; the next boot gets the standard copies back by itself (super docs/recipes/no-flash.md has the table). Escape: --not-overlayable \"which paths differ and why no overlay can carry them\""
	fi
	echo "FLASH NOT STANDARD: image ${got:0:12} != standard ${std:0:12}; not overlayable: $nt" >&2
	rt="$rt [not-overlayable: $nt]"
fi
state=${FLASH_STATE:-${ARTIFACTS:-$HOME/pixelios-artifacts}/flashed-userdata.txt}   # same path rule as tools/run-preflight flash_state (review xrun-usbport-r1 F5)
last=; if [ -e "$state" ]; then last=$(grep -v '^[[:space:]]*$' "$state" | tail -1) || echo "fflash-userdata GAP: cannot read $state (rc=$?): its newest line is ignored" >&2; fi   # r1 F6: only the NEWEST line counts (an erase or a failed flash since then means a re-flash is needed)
if [ "$force" = 0 ] && [ "${last%% *}" = "$got" ]; then
	refuse "this exact image is the last one flashed ($last); use --force to flash it again"; fi
tools/phone-lock check >&2 || { refuse "the phone lock is not held by you (tools/phone-lock run --owner NAME -- CMD, or use tools/xrun / tools/probe-run, which take it after the build)"; }
# || true: a failing or timed-out getvar must reach the loud REFUSED line below, not end the script silently under set -e/pipefail (review fork-fixes-r2 F5)
prod=$(timeout 15 fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1) || echo "fflash-userdata GAP: fastboot getvar product failed rc=$?" >&2
unl=$(timeout 15 fastboot getvar unlocked 2>&1 | sed -n 's/^unlocked: *//p' | head -1) || echo "fflash-userdata GAP: fastboot getvar unlocked failed rc=$?" >&2
[ "$prod" = bluejay ] && [ "$unl" = yes ] || { refuse "product='$prod' unlocked='$unl'"; }
# M7 (docs/PLAN-BOOT-SPEED.md): keep fastboot's own output and print what was really sent, to settle whether holes in the image are skipped
# r1 F7: mark the flash as started; a failed or interrupted flash leaves this as the newest line, so tools/xrun refuses to boot XNU on it
[ "$(stat -L -c '%d %i %s %.Y %.Z' -- "$img" || { echo "fflash-userdata GAP: stat of $img failed rc=$?" >&2; })" = "$id0" ] || { refuse "$img changed after the size and hash checks (was dev/inode/size/mtime/ctime $id0)"; }
# Flash cache: a verified Android-sparse copy of THIS image (same sha256) on the SSD is sent instead of the raw file. fastboot imports a raw image with
# sparse_file_import_auto, reading every block before the first chunk (~90 s for 17 GB on the HDD pool); the copy was written by the same library and call,
# so the chunks sent are the same, and prep proved expanded sha256 == raw sha256 with 0 DONT_CARE chunks (tools/flash_cache.py header).
# The use lock is taken shared BEFORE the lookup and kept until fastboot ends, so prep's eviction (exclusive) can never remove the copy in between (review 0294 F2).
src=$img; cstats=; ufd=
if [ -f "$fc" ]; then
	if ! command -v flock >/dev/null; then echo "FLASH CACHE GAP: flock missing: a cache copy would be sent without the use lock; using the raw image" >&2
	elif mkdir -p "$cdir" && exec {ufd}>>"$cdir/.use.lock" && flock -s "$ufd"; then
		if cl=$(python3 -I "$fc" lookup "$got"); then
			src=${cl%%$'\t'*}; cstats=${cl#*$'\t'}
			echo "FLASH CACHE HIT: sending $src (verified sparse copy of ${got:0:12})"
		else echo "FLASH CACHE MISS: ${got:0:12}: fastboot reads the raw image (slow path; prep starts in the background after the flash)" >&2; fi
	else echo "FLASH CACHE GAP: cannot take the shared use lock $cdir/.use.lock: using the raw image" >&2; fi
fi
echo "flashing $(date -u +%FT%TZ) $img" >> "$state"
mkdir -p logs; fl=logs/flash-$(date -u +%Y%m%dT%H%M%SZ).log; t0=$(date +%s)
fastboot flash userdata "$src" 2>&1 | tee "$fl"
el=$(( $(date +%s) - t0 ))
if [ -n "$ufd" ]; then exec {ufd}>&-; fi
# The old 20 s non-zero scan inside the phone hold is gone (it always timed out on a 17 GB image): a cache hit prints the stats prep measured.
if [ -n "$cstats" ]; then nzdesc=$cstats
else nzdesc="data size not measured in the hold (no cache entry; tools/flash_cache.py prep measures it)"; fi
echo "flash stats: $(grep -c "^Sending sparse" "$fl") sparse part(s) sent of $(grep -o "Sending sparse 'userdata' [0-9]*/[0-9]*" "$fl" | tail -1 | grep -o "/[0-9]*" | tr -d /) , $el s, image $(stat -L -c %s "$img") bytes apparent, source=$([ "$src" = "$img" ] && echo raw || echo cache), $nzdesc (log $fl)"
# A miss starts prep outside the phone's critical path: a detached heavy-job (class analysis; not --light: prep uses ~140 CPU-s, over the light cap), so the NEXT flash of this image is a hit.
if [ "$src" = "$img" ] && [ -f "$fc" ]; then
	if [ "${FLASH_CACHE_AUTOPREP:-1}" = 0 ]; then echo "FLASH CACHE: background prep off (FLASH_CACHE_AUTOPREP=0); run: python3 -I $fc prep $img" >&2
	elif [ -x tools/heavy-job ]; then
		tools/heavy-job detach --label "flash-prep-${got:0:12}" -- tools/heavy-job run --class analysis --label "flash-prep-${got:0:12}" -- python3 -I "$fc" prep "$img" >&2 \
			&& echo "FLASH CACHE PREP started in the background for ${got:0:12} (log: logs/heavy-job/flash-prep-${got:0:12}/)" \
			|| echo "FLASH CACHE GAP: background prep did not start (rc=$?): run python3 -I $fc prep $img" >&2
	else echo "FLASH CACHE GAP: tools/heavy-job missing: background prep not started; run python3 -I $fc prep $img" >&2; fi
fi
echo "$got $(date -u +%FT%TZ) $img" >> "$state"
echo "flashed; recorded in $state"
# R54: the reason is kept next to the state file (own file: flashed-userdata.txt is parsed by tools/run-preflight and must not change)
rf=${FLASH_REASONS:-${ARTIFACTS:-$HOME/pixelios-artifacts}/flash-reasons.tsv}; now=$(date -u +%FT%TZ)
{ mkdir -p -- "$(dirname -- "$rf")" && printf '%s\t%s\t%s\n' "$now" "$got" "$rt" >> "$rf"; } 2>/dev/null || echo "FFLASH GAP: cannot append the reason to $rf (flashes_today may be too low)" >&2
today=$(awk -F'\t' -v d="${now%%T*}" 'substr($1, 1, 10) == d { n++ } END { print n + 0 }' "$rf" 2>/dev/null || echo "?")
echo "FLASH: reason=$rt image=${got:0:12} time=$now"
fin=1; echo "RESULT: PASS flashed userdata flashes_today=$today reason=$rt"
