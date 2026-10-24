#!/bin/bash
# Boot a shim image on the Pixel, but only if it is the image you think it is.
#   tools/fboot.sh --expect xnu|probe|udump IMG [--allow-stale] [--max-age-min N]
# Refuses: missing/changed/stale/wrong-kind/lz4 images, a caller that does not hold the phone lock (TP-020), and any device that is not `bluejay`.
set -euo pipefail
cd "$(dirname "$0")/.."
exp=""; img=""; flags=()
while [ $# -gt 0 ]; do case "$1" in
	--expect) exp=$2; shift 2 ;; --allow-stale) flags+=(--allow-stale); shift ;;
	--max-age-min) flags+=(--max-age-min "$2"); shift 2 ;; *) img=$1; shift ;; esac; done
[ -n "$exp" ] && [ -f "$img" ] || { echo "usage: $0 --expect KIND IMG" >&2; exit 2; }
python3 -I tools/imgmeta.py check "$img" --expect "$exp" "${flags[@]}"
tools/phone-lock check >&2 || { echo "REFUSED: the phone lock is not held by you (tools/phone-lock run --owner NAME -- CMD, or use tools/xrun / tools/probe-run, which take it after the build)" >&2; exit 1; }
prod=$(timeout 15 fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1)
[ "$prod" = bluejay ] || { echo "REFUSED: device product is '$prod', not bluejay" >&2; exit 1; }
exec fastboot boot "$img"
