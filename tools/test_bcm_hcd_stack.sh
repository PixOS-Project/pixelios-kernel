#!/bin/bash
# Host test of tools/bcm-hcd-stack (review T-60ed F1-F3): a synthetic image, scratch dirs only. Prints `RESULT: PASS t60ed-bcm-hcd-stack` (exit 0) or FAIL.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -u
cd "$(dirname "$0")/.." || exit 2
T=$(mktemp -d); trap 'rm -rf -- "$T"' EXIT
fails=0; ok() { if ! "$@"; then echo "FAIL: $*"; fails=$((fails + 1)); fi; }
python3 -I - "$T/img.hcd" <<'PY'
import sys
b = bytearray()
for i in range(3): b += bytes([0x4c, 0xfc, 5]) + bytes([i] * 5)
b += bytes([0x4e, 0xfc, 4, 0xff, 0xff, 0xff, 0xff])
open(sys.argv[1], "wb").write(b)
PY
H=$(sha256sum "$T/img.hcd" | cut -d' ' -f1)
S=tools/bcm-hcd-stack
run() { python3 -I "$S" "$@" 2>&1; }
# F3: the stock hash is the default; --any-sha256 / a matching --sha256 pass
out=$(run --hcd "$T/img.hcd" --out "$T/o1"); ok grep -q 'differs from the expected 450f8c66' <<<"$out"; ok test ! -e "$T/o1"
out=$(run --hcd "$T/img.hcd" --out "$T/o1" --any-sha256); ok grep -q "RESULT: PASS bcm-hcd-stack sha256=$H" <<<"$out"; ok test -f "$T/o1/.bcm-hcd-stack"; ok test -f "$T/o1/9000-sweet-bt-hcd-image.patch"
out=$(run --hcd "$T/img.hcd" --out "$T/o2" --sha256 "$H"); ok grep -q 'RESULT: PASS' <<<"$out"
out=$(run --hcd "$T/img.hcd" --out "$T/o3" --any-sha256 --sha256 "$H"); ok grep -q 'exclude each other' <<<"$out"
# F1: a foreign non-empty directory (even one holding the input file) is never touched; a rerun keeps foreign files in a tool directory
mkdir -p "$T/foreign/sub"; cp "$T/img.hcd" "$T/foreign/BCM.hcd"; echo keep > "$T/foreign/important.txt"
out=$(run --hcd "$T/foreign/BCM.hcd" --out "$T/foreign" --any-sha256); ok grep -q 'not a directory made by this tool' <<<"$out"; ok test -f "$T/foreign/BCM.hcd"; ok test -f "$T/foreign/important.txt"; ok test -d "$T/foreign/sub"
echo extra > "$T/o1/notes.txt"; out=$(run --hcd "$T/img.hcd" --out "$T/o1" --any-sha256); ok grep -q 'RESULT: PASS' <<<"$out"; ok test -f "$T/o1/notes.txt"
touch "$T/empty_file_target"; out=$(run --hcd "$T/img.hcd" --out "$T/empty_file_target" --any-sha256); ok grep -q 'RESULT: ERROR' <<<"$out"
# F2: inside a git work tree, also when the parent of DIR does not exist yet
git init -q "$T/repo"
out=$(run --hcd "$T/img.hcd" --out "$T/repo/a/b/c" --any-sha256); ok grep -q 'inside a git work tree' <<<"$out"; ok test ! -e "$T/repo/a"
out=$(run --hcd "$T/img.hcd" --out "$T/repo" --any-sha256); ok grep -q 'inside a git work tree' <<<"$out"
# malformed image
printf '\x4c\xfc\x09\x01' > "$T/bad.hcd"; out=$(run --hcd "$T/bad.hcd" --out "$T/o4" --any-sha256); ok grep -q 'not a BCM.hcd record stream' <<<"$out"
if [ "$fails" -eq 0 ]; then echo "RESULT: PASS t60ed-bcm-hcd-stack"; else echo "RESULT: FAIL t60ed-bcm-hcd-stack ($fails)"; exit 1; fi
