#!/bin/bash
# Root-based backup of per-device partitions (PLAN.md Phase 2). Needs `su` over adb (Magisk).
# Read-only on the phone; output stays outside git. Usage: tools/backup-partitions.sh [outdir]
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
out=${1:-$HOME/pixelios-backups/$(date -u +%Y%m%dT%H%M%SZ)}
parts="persist efs efs_backup modem_userdata devinfo"
board=${BOARD:-bluejay}	# expected device codename (tools/pix devices)
[ "$(adb shell getprop ro.product.device | tr -d '\r')" = "$board" ] || { echo "not a $board" >&2; exit 1; }
adb shell 'su -c id' | grep -q uid=0 || { echo "no root over adb (su)" >&2; exit 1; }
mkdir -p "$out"; cd "$out"
for p in $parts; do
	dev=/dev/block/by-name/$p
	size=$(adb shell "su -c 'blockdev --getsize64 $dev'" | tr -d '\r')
	adb exec-out "su -c 'dd if=$dev bs=1M 2>/dev/null'" > "$p.img"
	[ "$(stat -c %s "$p.img")" = "$size" ] || { echo "$p: size mismatch" >&2; exit 1; }
	remote=$(adb shell "su -c 'sha256sum $dev'" | cut -d' ' -f1)
	[ "$(sha256sum "$p.img" | cut -d' ' -f1)" = "$remote" ] || { echo "$p: sha256 mismatch" >&2; exit 1; }
	echo "OK $p $size $remote"
done | tee SHA256SUMS.txt
# Cross-check the UFS-read userdata LBA range (4 KiB blocks 0x22F1E6..0x1DC03FA, see HANDOFF) against Android's view.
read -r st sz < <(adb shell 'su -c "cat /sys/block/sda/sda27/start /sys/block/sda/sda27/size"' | tr -d '\r' | paste -sd' ')
[ "$st" = $((0x22F1E6 * 8)) ] && [ "$sz" = $(((0x1DC03FA - 0x22F1E6 + 1) * 8)) ] \
	&& echo "OK userdata LBA range matches" || echo "MISMATCH userdata start=$st size=$sz (512B sectors)" >&2
adb shell 'su -c "sgdisk -p /dev/block/sda 2>/dev/null || true"' > gpt-lun0.txt || true
echo "backup in $out"
