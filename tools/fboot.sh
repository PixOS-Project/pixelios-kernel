#!/bin/bash
# Boot a shim image on the Pixel, but only if it is the image you think it is.
#   tools/fboot.sh --expect xnu|probe|udump IMG [--allow-stale] [--max-age-min N] [--panic-spin "REASON"]
# Refuses: missing/changed/stale/wrong-kind/lz4 images, a caller that does not hold the phone lock (TP-020), and any device that is not `bluejay`.
# T-03ff: an image whose recorded boot-args end in a debug= with DB_NMI (0x4) spins on a panic screen until the user resets the phone: refused unless --panic-spin gives a reason (>= 10 chars).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
exp=""; img=""; flags=(); spin=""
while [ $# -gt 0 ]; do case "$1" in
	--expect) exp=$2; shift 2 ;; --allow-stale) flags+=(--allow-stale); shift ;;
	--max-age-min) flags+=(--max-age-min "$2"); shift 2 ;; --panic-spin) spin=$2; shift 2 ;; *) img=$1; shift ;; esac; done
[ -n "$exp" ] && [ -f "$img" ] || { echo "usage: $0 --expect KIND IMG" >&2; exit 2; }
python3 -I tools/imgmeta.py check "$img" --expect "$exp" "${flags[@]}"
nmi=$(python3 -I -c 'import json,sys,re
try: a=json.load(open(sys.argv[1]+".meta.json")).get("args","")
except (OSError, ValueError): sys.exit(0)
d=re.findall(r"(?:^|\s)debug=(\S+)",a)
try: print(int(d[-1],0)&4 and d[-1] or "") if d else None
except ValueError: print("unparsable:"+d[-1])' "$img")
if [ -n "$nmi" ]; then
	[ "${#spin}" -ge 10 ] || { echo "REFUSED: the image boot-args end in debug=${nmi#unparsable:} (DB_NMI 0x4 or not a number): a panic would spin on the panic screen until the user resets the phone (T-03ff). Do instead: rebuild without that debug= (default debug=0x8 self-resets after the hold), or pass --panic-spin \"<reason, >= 10 chars>\"." >&2; exit 1; }
	echo "PANIC SPIN: image boots with debug=$nmi on request: $spin" >&2
fi
# RULES H9 (user 2026-11-12, docs/SAFETY-CHECKLIST.md): an XNU image boots only after its `tools/safety-check pre --image IMG` pass (tools/xrun runs it before the lock);
# a probe/udump shim image has no USB log, so the live checks cannot run: one loud GAP line (the checklist covers XNU boots; shim-only boots wait for the user's call)
if [ "$exp" = xnu ]; then
	python3 -I tools/safety-check stamp-check "$img" >&2 || { echo "REFUSED: no safety-check pre pass for this image (RULES H9). Do instead: boot through tools/xrun (it runs tools/safety-check pre + monitor), or run tools/safety-check pre --image IMG ... yourself and keep tools/safety-check monitor running for the whole boot" >&2; exit 1; }
else echo "SAFETY GAP: --expect $exp: a shim-only boot has no USB log; battery temp (S1/S3), soc (S4) and alive (S6) are not watched (docs/SAFETY-CHECKLIST.md)" >&2; fi
tools/phone-lock check >&2 || { echo "REFUSED: the phone lock is not held by you (tools/phone-lock run --owner NAME -- CMD, or use tools/xrun / tools/probe-run, which take it after the build)" >&2; exit 1; }
prod=$(timeout 15 fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1)
[ "$prod" = bluejay ] || { echo "REFUSED: device product is '$prod', not bluejay" >&2; exit 1; }
exec fastboot boot "$img"
