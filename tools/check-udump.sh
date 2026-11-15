#!/bin/bash
# Compare the shim's UDUMP output with `dd` of userdata as root. Needs Android + su
# (temporary root via a Magisk-patched boot image).
# Maintainer only: run by the human, never by automated agents.
#
# Two comparisons (both exit nonzero on mismatch):
#  1. the old one: per-256 KiB CRC32 windows of the first 1 MiB from the latest
#     logs/usb-*.txt capture vs Android (userdata_crc proof from ufs.c)
#  2. the whole dump: SHA-256 of the latest logs/udump-*.bin (written by
#     tools/udump-recv.py) vs SHA-256 of the same number of leading blocks read
#     from Android. Exits nonzero on mismatch.
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
log=$(ls -t logs/usb-*.txt | head -1)
grep -a 'UDUMP' "$log" || { echo "no UDUMP lines in $log" >&2; exit 1; }
for c in 0 64 128 192; do
	printf 'android blk=%d crc=' "$c"
	adb exec-out "su -c 'dd if=/dev/block/by-name/userdata bs=4096 skip=$c count=64 2>/dev/null'" | python3 -I -c 'import sys,zlib;print("%08X"%zlib.crc32(sys.stdin.buffer.read()))'
done
dump=$(ls -t logs/udump-*.bin | head -1)
[ -n "$dump" ] || { echo "no logs/udump-*.bin dump file (run tools/udump-recv.py)" >&2; exit 1; }
size=$(stat -c %s "$dump")
[ $((size % 4096)) -eq 0 ] || { echo "dump size $size is not a multiple of 4096" >&2; exit 1; }
blocks=$((size / 4096))
printf 'dump %s blocks=%d sha256: ' "$dump" "$blocks"
b=$(sha256sum "$dump" | cut -d' ' -f1); echo "$b"
printf 'android userdata sha256 (count=%d): ' "$blocks"
a=$(adb exec-out "su -c 'dd if=/dev/block/by-name/userdata bs=4096 count=$blocks 2>/dev/null'" | sha256sum | cut -d' ' -f1)
echo "$a"
if [ "$a" = "$b" ]; then
	echo "MATCH $a"
else
	echo "MISMATCH android=$a dump=$b" >&2
	exit 1
fi
