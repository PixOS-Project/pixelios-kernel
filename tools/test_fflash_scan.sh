#!/bin/bash
# Host test for the flash-cache steps of tools/fflash-userdata.sh (user 2026-11-11; replaces the old 20 s non-zero scan test): a temp copy of the script
# with a fake fastboot, phone-lock and heavy-job; the REAL tools/flash_cache.py and libsparse build the sparse copy.
# Never touches a phone: the fake fastboot must be the one on PATH (checked before every run).
#   bash tools/test_fflash_scan.sh  ->  last line RESULT: PASS|FAIL cases=N
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
mkdir -p "$w/k/tools" "$w/bin"
cp "$here/tools/fflash-userdata.sh" "$here/tools/boards.py" "$here/tools/flash_cache.py" "$here/tools/flash_cache_lib.py" "$w/k/tools/"
cp -r "$here/device" "$here/soc" "$w/k/"   # the size guard reads userdata_mib through boards.py (review fork-fixes-r2 F1)
printf '#!/bin/bash\nexit 0\n' >"$w/k/tools/phone-lock"
printf 'print("RESULT: PASS gate admitted-by=formal")\n' >"$w/k/tools/run-preflight"   # T-f32e: the formal-gate check has its own tests
printf '#!/bin/bash\necho "$*" >>"%s/hj.log"\necho "RESULT: PASS fake heavy-job detach"\n' "$w" >"$w/k/tools/heavy-job"
printf '#!/bin/bash\ncase "$*" in "getvar product") echo "product: bluejay" ;; "getvar unlocked") echo "unlocked: yes" ;; *) echo "$*" >>"%s/fb.log"; if flock -n -x "%s/cache/.use.lock" true 2>/dev/null; then echo UNLOCKED >>"%s/fb.log"; else echo LOCKED >>"%s/fb.log"; fi; echo "FAKE fastboot $*" ;; esac\n' "$w" "$w" "$w" "$w" >"$w/bin/fastboot"
chmod +x "$w/k/tools/fflash-userdata.sh" "$w/k/tools/phone-lock" "$w/k/tools/run-preflight" "$w/k/tools/heavy-job" "$w/bin/fastboot"
{ head -c 4096 /dev/urandom; head -c 8192 /dev/zero; head -c 4096 /dev/urandom; } >"$w/img"; sha256sum "$w/img" >"$w/img.sha256"
sha=$(cut -d' ' -f1 "$w/img.sha256")
printf '%s %s\n' "$sha" "$w/img" >"$w/standard-root.txt"   # the scratch image IS the standard root: never read the real standard-root.txt
export FLASH_CACHE_DIR="$w/cache" FLASH_CACHE_MIN_FREE_GB=0 STANDARD_ROOT="$w/standard-root.txt" FLASH_CACHE_ALLOW_HDD=1   # mktemp is on tmpfs here: device type unknown
n=0; bad=0
ok() { n=$((n + 1)); if [ "$2" = 1 ]; then echo "ok   $1"; else echo "FAIL $1"; bad=$((bad + 1)); printf '%s\n' "$out" | tail -n 15; fi; }
run() {   # $@ = extra env assignments
	rm -f "$w/state" "$w/reasons.tsv" "$w/fb.log" "$w/hj.log"
	[ "$(PATH="$w/bin:$PATH" command -v fastboot)" = "$w/bin/fastboot" ] || { echo "RESULT: FAIL fake fastboot not first on PATH (refusing to run)"; exit 1; }
	out=$(cd "$w" && env PATH="$w/bin:$PATH" FLASH_STATE="$w/state" FLASH_REASONS="$w/reasons.tsv" "$@" bash "$w/k/tools/fflash-userdata.sh" "$w/img" --yes-erase-userdata --reason "cache test: scratch image" 2>&1); rc=$?
}
has() { grep -q -- "$1" <<<"$out" && echo 1 || echo 0; }
sent() { grep -q -- "^flash userdata $1\$" "$w/fb.log" 2>/dev/null && echo 1 || echo 0; }

run FLASH_CACHE_AUTOPREP=0
ok "miss: raw image sent, source=raw, size not measured, prep off said, PASS" "$([ $rc = 0 ] && [ "$(sent "$w/img")$(has 'FLASH CACHE MISS')$(has 'source=raw')$(has 'not measured in the hold')$(has 'background prep off')" = 11111 ] && echo 1 || echo 0)"
ok "R54: ends with one RESULT: PASS line; reasons.tsv has the sha256" "$([ "$(tail -n 1 <<<"$out")" = "RESULT: PASS flashed userdata flashes_today=1 reason=cache test: scratch image" ] && [ "$(cut -f2 "$w/reasons.tsv")" = "$sha" ] && echo 1 || echo 0)"
run
ok "miss with prep on: one detached heavy-job (not the light slot) runs flash_cache.py prep IMG" "$([ $rc = 0 ] && grep -q -- "detach --label flash-prep-${sha:0:12} -- tools/heavy-job run --class analysis --label .* -- python3 -I tools/flash_cache.py prep $w/img" "$w/hj.log" && [ "$(has 'FLASH CACHE PREP started')" = 1 ] && echo 1 || echo 0)"
pr=$(cd "$w/k" && python3 -I tools/flash_cache.py prep "$w/img" 2>&1); prc=$?
ok "prep of the scratch image: READY" "$([ $prc = 0 ] && grep -q 'FLASH CACHE READY' <<<"$pr" && echo 1 || echo 0)"
run
ok "hit: the verified simg is sent under the shared use lock, source=cache, prep stats printed, PASS" "$([ $rc = 0 ] && [ "$(sent "$w/cache/$sha.simg")$(has 'FLASH CACHE HIT')$(has 'source=cache')$(has 'RAW chunks (2 of 4')$(has 'FLASH CACHE PREP')" = 11110 ] && echo 1 || echo 0)"
ok "hit: the unchanged raw image was not hashed again (stamp)" "$(has 'FLASH HASH STAMP')"
ok "hit: fastboot runs while the use lock is held shared (an eviction cannot take it)" "$(grep -qx LOCKED "$w/fb.log" && echo 1 || echo 0)"
touch "$w/cache/$sha.simg"
run FLASH_CACHE_AUTOPREP=0
ok "an simg changed after prep is refused, the raw image is sent, the copy is left for gc (the flash holds the use lock)" "$([ $rc = 0 ] && [ "$(sent "$w/img")$(has 'changed since prep')$(has 'left for gc')" = 111 ] && echo 1 || echo 0)"
g=$(cd "$w/k" && python3 -I tools/flash_cache.py gc 2>&1)
ok "gc then drops the changed copy" "$([ ! -e "$w/cache/$sha.simg" ] && grep -q 'RESULT: PASS flash_cache gc removed=1' <<<"$g" && echo 1 || echo 0)"
mv "$w/k/tools/flash_cache.py" "$w/fc.py"
run
ok "helper missing: loud GAP, plain sha256sum, raw sent, PASS" "$([ $rc = 0 ] && [ "$(sent "$w/img")$(has 'FLASH CACHE GAP: tools/flash_cache.py missing')" = 11 ] && echo 1 || echo 0)"
mv "$w/fc.py" "$w/k/tools/flash_cache.py"
printf 'raise SystemExit(5)\n' >"$w/k/tools/flash_cache.py"
run
ok "helper failing: loud GAP rc=5, plain sha256sum, raw sent, PASS" "$([ $rc = 0 ] && [ "$(sent "$w/img")$(has 'FLASH CACHE GAP: tools/flash_cache.py hash failed rc=5')" = 11 ] && echo 1 || echo 0)"
[ "$bad" = 0 ] && echo "RESULT: PASS cases=$n" || { echo "RESULT: FAIL cases=$n failed=$bad"; exit 1; }
