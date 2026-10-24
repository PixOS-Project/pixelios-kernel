#!/bin/bash
# One-shot XNU boot image for bluejay: payload (kernel + ADT + boot-args) + shim, written to shim/out/shim-boot-avb.img.
#   tools/build-xnu-image.sh [--kernel K] [--adt A] [--qr SECONDS|off] [--rw] [--args "extra boot-args"] [--out FILE]
#   --qr off     (default) no QR takeover: the screen keeps the iOS GUI; use this for GUI runs
#   --qr 40      QR log loop takes over the screen 40 s after the root mount starts (read with tools/qr-log.py --esp)
#                QL2 (default format; sweetqr_fmt=1 = the old QL1) knobs go in --args: sweetqr_ver=10 sweetqr_ecc=0 sweetqr_tiles=2 sweetqr_ms=800
#                sweetqr_unit=8192 sweetqr_k=64 sweetqr_red=0 sweetqr_fec=1 sweetqr_flush=3000 sweetqr_hold=1 (docs/QR_LOG.md)
#   --restart S[,bootloader]  sweetrestart=: XNU resets the phone itself after S seconds (patch 0083; then tools/to-fastboot.sh)
#   --cpus N     cpus=N (default 1; the ADT has all 8 cores: 4x A55, 2x A76, 2x X1)
#   --gui        sweetfb_noconsole=1 sweetfb_hz=30: stop drawing kernel text over the iOS UI
#   --usbnet     sweetusb=2 instead of sweetusb=0: USB Ethernet + UDP console log (docs/research/09, host: tools/usbnet-log.py)
#   --usbdma     with --usbnet: sweetusbdma=1 (DMA experiment: kernel RAM instead of ABL's pool page)
#   --dvfs K0,K1,K2  shim asks the ACPM firmware for these CPU cluster rates (kHz; table rows below the ceilings, 0 = leave; docs/research/25) before XNU starts; --dvfs read = read the rates only (dvfsr)
#   --rails      shim switches the touch rails on via ACPM before XNU starts (observed needed for the FTM5, docs/HANDOFF.md session 12)
#   --gpu-handover  shim switches the GPU shader rail on (ACPM) before XNU starts and leaves it on; boot-arg sweetgpu=0x1C500000 makes patch 0102 read the GPU (docs/research/30)
#   --rw         ufsrw=1 rootrw=1 (UFS writes inside userdata, needed to initialise the HFS journal)
# Never use --pstore-fast here: this bootloader silently rejects the lz4 image (docs/HANDOFF.md).
set -euo pipefail
cd "$(dirname "$0")/.."
A=${ARTIFACTS:-$HOME/pixelios-artifacts}
kernel=$A/xnu-0085/kernel.development.sweet; adt=$A/xnu-0085-root/A.adt; qr=off; rw=0; gui=0; usbnet=0; usbdma=0; rails=0; handover=0; dvfs=""; restart=""; cpus=1; extra=""; out=""
while [ $# -gt 0 ]; do case "$1" in
	--kernel) kernel=$2; shift 2 ;; --adt) adt=$2; shift 2 ;; --qr) qr=$2; shift 2 ;;
	--rw) rw=1; shift ;; --rails) rails=1; shift ;; --gpu-handover) handover=1; shift ;; --dvfs) dvfs=$2; shift 2 ;; --gui) gui=1; shift ;; --usbnet) usbnet=1; shift ;; --usbdma) usbnet=1; usbdma=1; shift ;; --cpus) cpus=$2; shift 2 ;; --restart) restart=$2; shift 2 ;; --args) extra=$2; shift 2 ;; --out) out=$2; shift 2 ;;
	*) echo "unknown option $1" >&2; exit 2 ;; esac; done
args="debug=0xc serial=3 cpus=$cpus keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1 wdt_test=no_user_checkin sweetusb=0 rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1"
[ "$usbnet" = 1 ] && args=${args/sweetusb=0/sweetusb=2}   # replace, never add: a duplicate key is ambiguous (review 17 L5: for key=value the LAST one wins in PE_parse_boot_argn, so `--gui --args sweetfb_noconsole=2` gives 2)
[ "$usbdma" = 1 ] && args="$args sweetusbdma=1"
[ "$rw" = 1 ] && args="$args ufsrw=1 rootrw=1"
[ "$gui" = 1 ] && args="$args sweetfb_noconsole=1 sweetfb_hz=30"
case "$qr" in off|0) ;; *[!0-9]*) echo "--qr needs seconds or off" >&2; exit 2 ;; *) args="$args sweetqr=$qr" ;; esac
[ -n "$restart" ] && args="$args sweetrestart=$restart"
[ "$handover" = 0 ] || args="$args sweetgpu=0x1C500000"   # patch 0102 reads the GPU; sweetgpujob=1 (patch 0103, WRITES) only via --args
[ -n "$extra" ] && args="$args $extra"
railsarg=(); words=""; dvfsarg=(); hoarg=(); [ "$handover" = 0 ] || hoarg=(--gpu-handover)
[ "$rails" = 0 ] || words=rails
if [ "$dvfs" = read ]; then words="${words:+$words,}dvfsr"
elif [ -n "$dvfs" ]; then words="${words:+$words,}dvfs"; dvfsarg=(--dvfs "$dvfs"); fi
[ -z "$words" ] || railsarg=(--touch-probe="$words")
p=$(mktemp /tmp/payload.XXXXXX.bin); trap 'rm -f "$p"' EXIT
python3 -I tools/mkpayload.py "$kernel" "$adt" "$p" --board bluejay --cmdline "$args" >/dev/null
META_ARGS="$args" XNU_UFS=1 shim/build.sh --board bluejay --avb --boot-xnu "$p" "${railsarg[@]}" "${dvfsarg[@]}" "${hoarg[@]}" >/dev/null
[ -z "$out" ] || cp shim/out/shim-boot-avb.img "$out"
echo "boot-args: $args"; echo "image: ${out:-shim/out/shim-boot-avb.img}"
