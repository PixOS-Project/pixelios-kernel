#!/bin/bash
# One-shot XNU boot image for bluejay: payload (kernel + ADT + boot-args) + shim, written to shim/out/shim-boot-avb.img.
#   tools/build-xnu-image.sh [--kernel K] [--adt A] [--qr SECONDS|off] [--rw] [--args "extra boot-args"] [--out FILE]
#   --qr off     (default) no QR takeover: the screen keeps the iOS GUI; use this for GUI runs
#   --qr 40      QR log loop takes over the screen 40 s after the root mount starts (read with tools/qr-log.py --esp)
#                QL2 (default format; sweetqr_fmt=1 = the old QL1) knobs go in --args: sweetqr_ver=10 sweetqr_ecc=0 sweetqr_tiles=2 sweetqr_ms=800
#                sweetqr_unit=8192 sweetqr_k=64 sweetqr_red=0 sweetqr_fec=1 sweetqr_flush=3000 sweetqr_hold=1 (docs/QR_LOG.md)
#   --restart S[,bootloader]  sweetrestart=: XNU resets the phone itself after S seconds (patch 0083; then tools/to-fastboot.sh)
#   --cpus N     cpus=N (default 8; the ADT has all 8 cores: 4x A55, 2x A76, 2x X1)
#   --gui        sweetfb_noconsole=1 sweetfb_hz=30: stop drawing kernel text over the iOS UI
#   --usbnet     sweetusb=2 instead of sweetusb=0: USB Ethernet + UDP console log (docs/research/09, host: tools/usbnet-log.py)
#   --usbdma     with --usbnet: sweetusbdma=1 (DMA experiment: kernel RAM instead of ABL's pool page)
#   --dvfs K0,K1,K2  shim asks the ACPM firmware for these CPU cluster rates (kHz; table rows below the ceilings, 0 = leave; docs/research/25) before XNU starts; --dvfs read = read the rates only (dvfsr)
#   --rails      shim switches the touch rails on via ACPM before XNU starts (observed needed for the FTM5, docs/HANDOFF.md session 12)
#   --gpu-clk-level N  with --gpu-handover: after the rail-on step the shim asks the ACPM for DT pair N (1 = G3DL2 996000 / G3D 762000 kHz = stock max .. 3 = 750000 / 572000; 0 is refused), reads both back and the BUCK2S registers (T-a214)
#   --mif-clk-level KHZ  implies --mif-clk; the ONE MIF request goes to this table row instead of the top row (tools/boards.py mif_clk_level refuses any value that is not a gs101.dtsi mif_int_map row; ALL-ON T-02)
#   --sweet-probe  shim built with build.sh --sweet-probe (READ-ONLY, LOWCLK B1/B2, docs/research/161): PSCI_VERSION + PSCI_FEATURES x3, the SLC ACPM channel 10/11 descriptors and ONE fixed SLC PT_VERSION request before the jump to XNU; adds boot-arg sweetprobe=1 (xnu patch 0363 prints the SWEET PROBE: lines, the Wi-Fi GETs and the Set 383 key log); refused without the string 'sweetprobe_pv' in the kernel
#   --mif-clk  shim reads ACPM DVFS dom 0 (MIF) / dom 1 (INT), asks MIF for its table top row once, and hands the readings to XNU as sweetmif* boot-args (xnu patch 0214; T-5030)
#   --gpu-handover  shim switches the GPU shader rail on (ACPM) before XNU starts and leaves it on; boot-arg sweetgpu=0x1C500000 makes patch 0102 read the GPU (docs/research/30)
#   --wlan-hold  shim flavor --wlan-hold (PCIe1 link left up, private notes) and boot-arg sweetwlan=1 (xnu patch 0124: READ-ONLY chip attach); --wlan-level 2 adds the three D11 wrapper reads
#   --wlanfw FILE  BCM4389 firmware container (tools/pack_wlanfw.py) appended to the payload as /chosen/memory-map WLANFW (needs --wlan-hold; xnu patch 0121 only checks it)
#   --wlancred FILE  RAM-only credential blob (tools/mkwlancred.py) appended as /chosen/memory-map WLANCRED (needs --wlan-hold; the image then holds the PSK: tools/xrun removes it, SPEC-WIFI TRANSPORT s.3); only the PATH is an argument
#   --overlay DIR  dev overlay (docs/AUTOMATION.md): pack DIR with tools/mkdevoverlay.py, add it as the payload RAM disk and sweetdev=1 (xnu patch 0117)
#   --compress   with --overlay: write archive version 2, zlib-compressed entries (xnu patch 0154 must be in the kernel; default unchanged = version 1)
#   --el2-b3     (needs --el2-trap) build.sh gets --el2-b3 too (EL2_B3, kernel branch el2/b3-trap)
#   --pmic-src-clear  implies --pmic-src; build.sh --pmic-src-clear: ADDS the one PMIC write (writes 0 to S2MPG10 OFFSRC/PWRONSRC + S2MPG11 OFFSRC after the reads, reads back once; shim/pmicsrc.h sweetpsclr=N, docs/research/142)
#   --pmic-src   shim built with build.sh --pmic-src: READ-ONLY dump of the PMIC reset-source latches (S2MPG10/S2MPG11 STATUS/PWRONSRC/OFFSRC) at boot, nothing cleared (shim/pmicsrc.c, ALL-ON T-02/T-24); no boot-arg
#   --el2-trap   TP-045: shim built with `build.sh --el2-trap --boot-xnu` (EL2_RESIDENT, B2); refused with --gpu-handover/--wlan-hold/--rails/--dvfs and when shim/build.sh has no EL2_RESIDENT (kernel branch el2/b2-report)
#   --panic-spin "REASON"  T-03ff: a debug= with DB_NMI (0x4, e.g. debug=0xc) makes a panic spin on the panic screen until the user resets the phone; refused (exit 2) unless this flag gives a reason (>= 10 chars), logged as `PANIC SPIN:`
#   --rw         ufsrw=1 rootrw=1 (UFS writes inside userdata, needed to initialise the HFS journal)
# Never use --pstore-fast here: this bootloader silently rejects the lz4 image (docs/HANDOFF.md).
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
set -euo pipefail
cd "$(dirname "$0")/.."
A=${ARTIFACTS:-$HOME/pixelios-artifacts}
kernel=$A/xnu-0085/kernel.development.sweet; adt=$A/xnu-0085-root/A.adt; qr=off; rw=0; gui=0; usbnet=0; usbdma=0; rails=0; handover=0; gpuclklvl=""; mifclk=0; sweetprobe=0; mifclevel=""; pmicsrc=0; pmicclr=0; dvfs=""; restart=""; cpus=8; extra=""; out=""; overlay=""; compress=""; wlanfw=""; wlancred=""; wlan=0; wlanlvl=1; el2=0; el2b3=0; spin=""
while [ $# -gt 0 ]; do case "$1" in
	--kernel) kernel=$2; shift 2 ;; --adt) adt=$2; shift 2 ;; --qr) qr=$2; shift 2 ;;
	--rw) rw=1; shift ;; --rails) rails=1; shift ;; --wlan-hold) wlan=1; shift ;; --wlan-level) wlanlvl=$2; shift 2 ;; --gpu-handover) handover=1; shift ;; --gpu-clk-level) gpuclklvl=$2; shift 2 ;; --mif-clk-level) mifclk=1; mifclevel=${2:?--mif-clk-level needs a MIF table row in kHz}; shift 2 ;; --mif-clk) mifclk=1; shift ;; --sweet-probe) sweetprobe=1; shift ;; --pmic-src) pmicsrc=1; shift ;; --pmic-src-clear) pmicsrc=1; pmicclr=1; shift ;; --dvfs) dvfs=$2; shift 2 ;; --gui) gui=1; shift ;; --usbnet) usbnet=1; shift ;; --usbdma) usbnet=1; usbdma=1; shift ;; --cpus) cpus=$2; shift 2 ;; --restart) restart=$2; shift 2 ;; --args) extra=$2; shift 2 ;; --out) out=$2; shift 2 ;; --overlay) overlay=$2; shift 2 ;; --compress) compress=--compress; shift ;; --wlanfw) wlanfw=$2; shift 2 ;; --wlancred) wlancred=$2; shift 2 ;; --el2-trap) el2=1; shift ;; --el2-b3) el2b3=1; shift ;; --panic-spin) spin=$2; shift 2 ;;
	*) echo "unknown option $1" >&2; exit 2 ;; esac; done
[ -z "$compress" ] || [ -n "$overlay" ] || { echo "--compress needs --overlay" >&2; exit 2; }
[ -z "$wlanfw" ] || [ "$wlan" = 1 ] || { echo "--wlanfw needs --wlan-hold" >&2; exit 2; }
[ -z "$wlancred" ] || [ "$wlan" = 1 ] || { echo "--wlancred needs --wlan-hold" >&2; exit 2; }
case "$wlanlvl" in 1|2) ;; *) echo "--wlan-level needs 1 or 2" >&2; exit 2 ;; esac
[ "$wlan" = 1 ] || [ "$wlanlvl" = 1 ] || { echo "--wlan-level needs --wlan-hold" >&2; exit 2; }
[ -z "$gpuclklvl" ] || [ "$handover" = 1 ] || { echo "--gpu-clk-level needs --gpu-handover (the shim asks the pair after the rail-on step; T-a214)" >&2; exit 2; }
[ "$el2" = 0 ] || { [ "$rails" = 0 ] && [ "$handover" = 0 ] && [ -z "$gpuclklvl" ] && [ "$wlan" = 0 ] && [ -z "$dvfs" ] && [ "$pmicsrc" = 0 ] || { echo "--el2-trap is refused with --rails/--gpu-handover/--wlan-hold/--dvfs/--pmic-src" >&2; exit 2; }; }
[ "$el2" = 0 ] || grep -Eq '^[[:space:]]*CFLAGS="\$CFLAGS -DEL2_RESIDENT"' shim/build.sh || { echo "--el2-trap: shim/build.sh has no EL2_RESIDENT; merge kernel branch el2/b2-report" >&2; exit 2; }   # backstop of tools/xrun (TP-045)
[ "$el2b3" = 0 ] || { [ "$el2" = 1 ] || { echo "--el2-b3 needs --el2-trap" >&2; exit 2; }; grep -Eq '^[[:space:]]*CFLAGS="\$CFLAGS -DEL2_B3"' shim/build.sh || { echo "--el2-b3: shim/build.sh has no EL2_B3; merge kernel branch el2/b3-trap" >&2; exit 2; }; }   # backstop of tools/xrun
[ "$el2b3" = 0 ] || case " $extra " in *" sweetgpu"*|*" sweetwlan"*|*" sweetmali"*|*" sweettouch"*) echo "--el2-b3: --args with sweetgpu*/sweetwlan*/sweetmali*/sweettouch* is refused (PMU page 0x17460000)" >&2; exit 2 ;; esac   # backstop of tools/xrun
# debug=0x8 (DB_KPRT, no DB_NMI 0x4): a panic resets by itself after the patch 0224 screen hold, no button (T-35ba); --args debug=0xc spins on the panic screen as before
args="debug=0x8 serial=3 cpus=$cpus keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1 wdt_test=no_user_checkin sweetusb=0 rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1"
[ "$usbnet" = 1 ] && args=${args/sweetusb=0/sweetusb=2}   # replace, never add: a duplicate key is ambiguous (review 17 L5: for key=value the LAST one wins in PE_parse_boot_argn, so `--gui --args sweetfb_noconsole=2` gives 2)
[ "$usbdma" = 1 ] && args="$args sweetusbdma=1"
[ "$rw" = 1 ] && args="$args ufsrw=1 rootrw=1"
[ "$gui" = 1 ] && args="$args sweetfb_noconsole=1 sweetfb_hz=30"
case "$qr" in off|0) ;; *[!0-9]*) echo "--qr needs seconds or off" >&2; exit 2 ;; *) args="$args sweetqr=$qr" ;; esac
[ -n "$restart" ] && args="$args sweetrestart=$restart"
[ "$handover" = 0 ] || args="$args sweetgpu=0x1C500000"   # patch 0102 reads the GPU; sweetgpujob=1 (patch 0103, WRITES) only via --args
[ "$wlan" = 0 ] || args="$args sweetwlan=$wlanlvl"   # patch 0124: read-only attach, needs the --wlan-hold shim flavor (link left up)
read -ra ew <<<"$extra"; nrd=0; for w in "${ew[@]}"; do case $w in rd=*) nrd=$((nrd + 1)) ;; esac; done
[ "$nrd" -le 1 ] || { echo "--args: $nrd rd= tokens; give at most one (review r75-split-r1 F1: PE_parse_boot_argn takes one, the cut cmdline in the report may show the other)" >&2; exit 2; }
[ -n "$extra" ] && case " $extra " in *" rd="*) args=${args/ rd=ufsroot0/} ;; esac   # a user rd= replaces the default one: two rd= tokens hide the real one (xrun_report cuts the cmdline)
[ -n "$extra" ] && args="$args $extra"
[ "$sweetprobe" = 0 ] || case " $args " in *" sweetprobe="*) ;; *) args="$args sweetprobe=1" ;; esac   # xnu patch 0363 gate; an explicit sweetprobe= in --args is kept (patch 0363 logs a GAP for any value but 0/1)
[ -z "$overlay" ] || case " $args " in *" ufsroot="*) echo "--overlay: ufsroot= in --args is refused (the overlay writes userdata only, AGENTS.md rule 1)" >&2; exit 2 ;; esac   # review 0117 F1
[ -z "$overlay" ] || case " $args " in *" sweetdev="*) ;; *) args="$args sweetdev=1" ;; esac   # never doubled: an explicit sweetdev= in --args is kept
railsarg=(); words=""; dvfsarg=(); hoarg=(); el2arg=(); [ "$el2" = 0 ] || el2arg=(--el2-trap); [ "$el2b3" = 0 ] || el2arg+=(--el2-b3); [ "$handover" = 0 ] || hoarg=(--gpu-handover); [ -z "$gpuclklvl" ] || hoarg+=(--gpu-clk --gpu-clk-level "$gpuclklvl"); [ "$mifclk" = 0 ] || hoarg+=(--mif-clk); [ "$sweetprobe" = 0 ] || hoarg+=(--sweet-probe); [ -z "$mifclevel" ] || hoarg+=(--mif-clk-level "$mifclevel"); [ "$pmicsrc" = 0 ] || hoarg+=(--pmic-src); [ "$pmicclr" = 0 ] || hoarg+=(--pmic-src-clear); [ "$wlan" = 0 ] || hoarg+=(--wlan-hold)
[ "$rails" = 0 ] || words=rails
if [ "$dvfs" = read ]; then words="${words:+$words,}dvfsr"
elif [ -n "$dvfs" ]; then words="${words:+$words,}dvfs"; dvfsarg=(--dvfs "$dvfs"); fi
[ -z "$words" ] || railsarg=(--touch-probe="$words")
[ -z "$overlay" ] || umask 077   # review devd-r1 F10: the overlay may hold the devd key; shim/out/shim-boot-avb.img and the run copy stay private
ptmp=/tmp; [ -z "$wlancred" ] || ptmp=${XDG_RUNTIME_DIR:-/tmp}   # a payload that holds the credential blob goes to tmpfs, not /tmp (removed by the EXIT trap below)
p=$(mktemp "$ptmp/payload.XXXXXX.bin"); ov=""; rdarg=(); trap 'rm -f "$p" "$ov"' EXIT
if [ -n "$overlay" ]; then ov=$(mktemp /tmp/devoverlay.XXXXXX.bin); python3 -I tools/mkdevoverlay.py "$overlay" "$ov" ${compress:+"$compress"} >&2 || exit 2; rdarg=(--ramdisk "$ov"); fi
bargv=(--board bluejay --avb "${el2arg[@]}" --boot-xnu "$p" "${railsarg[@]}" "${dvfsarg[@]}" "${hoarg[@]}")   # the one build.sh argv (el2arg before --boot-xnu: the B2 mode, TP-045)
# T-03ff: no boot may spin on a panic screen without a logged reason. The LAST debug= wins (PE_parse_boot_argn_internal keeps scanning after a match: xnu pexpert/gen/bootargs.c:61-185), so judge the last token of the final string.
dbg=""; read -ra _ba <<<"$args"; for _t in "${_ba[@]}"; do case $_t in debug=*) dbg=${_t#debug=} ;; esac; done
if [ -n "$dbg" ] && ! [[ $dbg =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]]; then echo "REFUSED: debug=$dbg is not a number; the panic recovery (no DB_NMI 0x4) cannot be checked (T-03ff). Use debug=0x8 or a hex/decimal value." >&2; exit 2; fi
if [ -n "$dbg" ] && [ $(( dbg & 4 )) -ne 0 ]; then
	[ "${#spin}" -ge 10 ] || { echo "REFUSED: debug=$dbg has DB_NMI (0x4): a panic would spin on the panic screen until the user resets the phone (T-03ff, user order 2026-11-07). Do instead: drop the debug= token (default debug=0x8 resets itself after the 20 s hold, patch 0224), or pass --panic-spin \"<reason, >= 10 chars>\" to spin on purpose." >&2; exit 2; }
	echo "PANIC SPIN: debug=$dbg spins on a panic by request: $spin" >&2; echo "panic-spin: $spin"
fi
if [ -n "$spin" ] && [ $(( ${dbg:-0} & 4 )) -eq 0 ]; then echo "PANIC SPIN GAP: --panic-spin given but the final debug= has no DB_NMI (0x4); the reason is ignored" >&2; fi
# boot line limit, one source: mkpayload's BOOT_LINE_LENGTH (2048 with xnu patch 0320; review 0320 F1: no hard-coded 1023). Unreadable = GAP, no size refusal.
_line=$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import mkpayload; print(mkpayload.BOOT_LINE_LENGTH - 1)' "$(dirname "$0")" 2>/dev/null) || true
[[ $_line =~ ^[0-9]+$ ]] || { echo "BOOTLINE GAP: mkpayload's BOOT_LINE_LENGTH is unreadable from $(dirname "$0") ('${_line:0:40}'): boot-arg size checks skipped here" >&2; _line=; }
# ALL-ON 2026-11-11: with --mif-clk the shim appends its sweetmif* tokens to this line and needs MIF_TOK_MAX (128, shim/mifclk.c:30) bytes of room in the
# BOOT_LINE_LENGTH field (shim/payload.h; 2048 with xnu 0320); without the room mif_clk_cmdline asks NOTHING and says so only on the screen (all_on1 991 B: MIF stayed at
# 421 MHz, XNU 'no sweetmif* boot-arg', GPU bench 50.5 vs 59.9 fps pooled [obs resident-175603-b1]). Refuse on the host instead.
if [ -n "$_line" ] && [ "$mifclk" = 1 ] && [ "${#args}" -gt $((_line - 128)) ]; then echo "REFUSED: boot-args are ${#args} bytes; with --mif-clk the limit is $((_line - 128)) (the shim needs 128 bytes for its sweetmif* tokens, shim/mifclk.c MIF_TOK_MAX, else it asks MIF for nothing). Do instead: drop $(( ${#args} - _line + 128 )) bytes of --args, or --no-mif-clk." >&2; exit 2; fi
# xnu patch 0363 (LOWCLK B1/B2): --sweet-probe makes the shim append up to 12 sweetprobe_* tokens (shim/sweetprobe.c SP_TOK_MAX 448) after the sweetmif*/sweetps* tokens; without the room sweet_probe_cmdline asks NOTHING (screen-only GAP)
sprb_max=$((${_line:-1023} - 448)); [ "$mifclk" = 0 ] || sprb_max=$((sprb_max - 101)); [ "$pmicsrc" = 0 ] || sprb_max=$((sprb_max - 79))
if [ -n "$_line" ] && [ "$sweetprobe" = 1 ] && [ "${#args}" -gt "$sprb_max" ]; then echo "REFUSED: boot-args are ${#args} bytes; with --sweet-probe$([ "$mifclk" = 0 ] || echo ' and --mif-clk')$([ "$pmicsrc" = 0 ] || echo ' and --pmic-src') the limit is $sprb_max (the shim appends up to 448 bytes of sweetprobe_* tokens, shim/sweetprobe.c SP_TOK_MAX, else it asks nothing). Do instead: drop $(( ${#args} - sprb_max )) bytes of --args." >&2; exit 2; fi
if [ "$sweetprobe" = 1 ] && [ -f "$kernel" ] && ! grep -qa sweetprobe_pv "$kernel"; then echo "REFUSED: --sweet-probe but $kernel has no 'sweetprobe_pv' string (xnu patch 0363 missing): XNU would print nothing of the probe tokens. Do instead: build the kernel with patch 0363, or leave out --sweet-probe." >&2; exit 2; fi
# ALL-ON T-02/T-24: --pmic-src makes the shim append " sweetps10=0x........ sweetps11=0x.... sweetpsrc=N" (50 characters, shim/pmicsrc.h PMIC_SRC_TOK_MAX 51) after the sweetmif* tokens (at most 101
# characters, shim/mifclk.c tok()); with --pmic-src-clear also " sweetpsclr=NN" (plus sweetpspw: 79 characters, PMIC_SRC_TOK_MAX 80); the shim never truncates (loader.c pmic_src_append appends nothing without room). Same limit as tools/resident_core.py line_limit().
psrc_tok=50; [ "$pmicclr" = 0 ] || psrc_tok=79
psrc_max=$((${_line:-1023} - psrc_tok)); [ "$mifclk" = 0 ] || psrc_max=$((${_line:-1023} - 101 - psrc_tok))
if [ -n "$_line" ] && [ "$pmicsrc" = 1 ] && [ "${#args}" -gt "$psrc_max" ]; then echo "REFUSED: boot-args are ${#args} bytes; with --pmic-src$([ "$pmicclr" = 0 ] || echo '-clear')$([ "$mifclk" = 0 ] || echo ' and --mif-clk') the limit is $psrc_max (the shim appends up to $psrc_tok bytes of sweetps* tokens$([ "$mifclk" = 0 ] || echo ' after up to 101 bytes of sweetmif* tokens'), shim/pmicsrc.h PMIC_SRC_TOK_MAX, else XNU gets no PMIC reset-source line). Do instead: drop $(( ${#args} - psrc_max )) bytes of --args, or leave out --pmic-src." >&2; exit 2; fi
# xnu patch 0367 (L-28): --dvfs K0,K1,K2 makes the shim append up to 221 characters of sweetdvfs* tokens (shim/touchprobe.h TOUCH_DVFS_TOK_MAX 224) LAST on the line, after the sweetmif*/sweetprobe_*/sweetps* ones;
# without the room loader.c appends nothing and says so only on the screen, so the USB log could not prove the CPU rows (tracker L-28). Refuse on the host; a kernel without the print patch would drop them silently.
if [ -n "$dvfs" ] && [ "$dvfs" != read ]; then
	dv_max=$((${_line:-1023} - 224)); [ "$mifclk" = 0 ] || dv_max=$((dv_max - 128)); [ "$sweetprobe" = 0 ] || dv_max=$((dv_max - 448)); [ "$pmicsrc" = 0 ] || dv_max=$((dv_max - psrc_tok - 1))
	if [ -n "$_line" ] && [ "${#args}" -gt "$dv_max" ]; then echo "REFUSED: boot-args are ${#args} bytes; with --dvfs$([ "$mifclk" = 0 ] || echo ' and --mif-clk')$([ "$sweetprobe" = 0 ] || echo ' and --sweet-probe')$([ "$pmicsrc" = 0 ] || echo ' and --pmic-src') the limit is $dv_max (the shim appends up to 224 bytes of sweetdvfs* tokens after the other shim tokens, shim/touchprobe.h TOUCH_DVFS_TOK_MAX, else the CPU DVFS result has no USB-log witness). Do instead: drop $(( ${#args} - dv_max )) bytes of --args." >&2; exit 2; fi
	if [ -f "$kernel" ] && ! grep -qa sweetdvfsrc "$kernel"; then echo "REFUSED: --dvfs but $kernel has no 'sweetdvfsrc' string (xnu patch 0367 missing): XNU would print nothing of the CPU DVFS result. Do instead: build the kernel with patch 0367." >&2; exit 2; fi
fi
# review 0319 F1 (rule 7): a kernel without the print patch would drop the shim's PMIC tokens silently (sweetps10 = patch 0318, sweetpsclr = patch 0319): refuse on the host
if [ "$pmicsrc" = 1 ] && [ -f "$kernel" ] && ! grep -qa sweetps10 "$kernel"; then echo "REFUSED: --pmic-src but $kernel has no 'sweetps10' string (xnu patch 0318 missing): XNU would print nothing of the PMIC reset-source values. Do instead: build the kernel with patch 0318, or leave out --pmic-src." >&2; exit 2; fi
if [ "$pmicclr" = 1 ] && [ -f "$kernel" ] && ! grep -qa sweetpsclr "$kernel"; then echo "REFUSED: --pmic-src-clear but $kernel has no 'sweetpsclr' string (xnu patch 0319 missing): the PMIC clear would run and XNU would not report its result. Do instead: build the kernel with patch 0319, or leave out --pmic-src-clear." >&2; exit 2; fi
[ -z "${XNU_IMAGE_ARGS_ONLY:-}" ] || { { [ "$el2" = 0 ] && [ "$mifclk" = 0 ] && [ "$pmicsrc" = 0 ] && [ "$sweetprobe" = 0 ]; } || echo "build.sh argv: ${bargv[*]}"; echo "boot-args: $args"
	# review 0320 F5: the args-only path (xrun --print-bootargs) refuses an over-limit line too (mkpayload's limit, read above), exit 2
	_n=$(printf %s "$args" | wc -c)
	if [ -n "$_line" ] && [ "$_n" -gt "$_line" ]; then echo "REFUSED: boot-args are $_n bytes, the limit is $_line (mkpayload BOOT_LINE_LENGTH - 1): the image build would refuse this line. Do instead: shorten --args." >&2; exit 2; fi
	exit 0; }   # test hook (tools/test_build_overlay_args.py)
# T-03ff: the kernel must carry patch 0224 (the 20 s screen hold + self-reset); without it debug=0x8 still resets but shows no frame of the panic text (T-35ba Log 07:56Z). rule 8: say so.
if [ -f "$kernel" ] && ! grep -qa sweetpanichold "$kernel"; then echo "RECOVERY GAP: no panic hold in $kernel (no sweetpanichold string: patch 0224 missing); a panic resets at once, the panic text is not readable on the screen" >&2; echo "recovery-gap: no panic hold (kernel lacks patch 0224)"; fi
python3 -I tools/mkpayload.py "$kernel" "$adt" "$p" --board bluejay --cmdline "$args" "${rdarg[@]}" ${wlanfw:+--wlanfw "$wlanfw"} ${wlancred:+--wlancred "$wlancred"} >/dev/null
META_ARGS="$args" XNU_UFS=1 shim/build.sh "${bargv[@]}" >/dev/null
[ -z "$out" ] || cp shim/out/shim-boot-avb.img "$out"
echo "boot-args: $args"; echo "image: ${out:-shim/out/shim-boot-avb.img}"
