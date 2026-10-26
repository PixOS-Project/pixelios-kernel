#!/bin/bash
# shellcheck disable=SC2089,SC2090  # CFLAGS is deliberately one word-split string (its -DPSTORE_BUILD_ID value carries literal quotes); an array would touch every compile line
set -euo pipefail
cd "$(dirname "$0")"

board=sweet
payload=
avb=0
pstore_fast=0
usb_gadget=0
boot_xnu=0
udump_mib=0
qrlog=0
wlan_probe=0
wlan_rails=0
wlan_en=0
wlan_smc_read=0
wlan_iso=0
wlan_link=0
wlan_link_n=
wlan_hold=0
gpu_probe=0
gpu_power=0
gpu_stack=0
gpu_clk=0
gpu_rails=0
gpu_rail_on=0
gpu_handover=0
gpu_s2mpu=0
gpu_job=0
touch_probe=0
touch_opts=
touch_rails=0
touch_dvfs=0
dvfs_khz=
O=out	# output dir (--out DIR, relative to shim/): the gate gives every build its own, so builds can run in parallel
while [ "$#" -gt 0 ]; do
	case "$1" in
		--board) board="$2"; shift 2 ;;
		--out) O="$2"; shift 2 ;;
		--avb) avb=1; shift ;;
		--pstore-fast) pstore_fast=1; shift ;;
		--usb-gadget) usb_gadget=1; shift ;;
		--boot-xnu) boot_xnu=1; shift ;;
		--udump) udump_mib="$2"; shift 2 ;;
		--qrlog) qrlog=1; shift ;;
		--wlan-probe) wlan_probe=1; shift ;;
		--wlan-rails) wlan_rails=1; wlan_probe=1; shift ;;	# implies --wlan-probe; READ-ONLY S2MPG10 PCIe-PHY LDOs (ACPM) + CMU_HSI2 + PMU words (wlanrails.c, docs/research/29)
		--wlan-smc-read) wlan_smc_read=1; wlan_probe=1; shift ;;	# implies --wlan-probe; two EL3 SMC_CMD_PRIV_REG READs of PMU 0x17463EC4 / 0x17463EC0 (wlansmc.c, docs/research/29 W2a)
		--wlan-iso) wlan_iso=1; wlan_smc_read=1; wlan_probe=1; shift ;;	# implies --wlan-smc-read --wlan-probe; WRITER via EL3: PCIe1 PHY isolation release + restore (wlansmc.c WLAN_ISO_RUN, docs/research/29 W2c)
		--wlan-link) wlan_link=1; wlan_probe=1; shift ;;	# implies --wlan-probe; the table-driven power-up chain S00..S19 (default: the last step BEFORE the PERST release) + teardown (wlanlink.c, docs/research/29 section 14)
		--wlan-hold) wlan_hold=1; wlan_link=1; wlan_probe=1; shift ;;	# needs --boot-xnu, gs101; implies --wlan-link=30 (the S30 step); chain up to S30, NO teardown, link left up for XNU (wlanlink.c wlan_link_hold, docs/research/42 section 7)
		--wlan-link=*) wlan_link=1; wlan_probe=1; wlan_link_n="${1#--wlan-link=}"; shift ;;
		--wlan-en) wlan_en=1; wlan_probe=1; shift ;;	# implies --wlan-probe; WRITER: WLAN_EN gpp14-2 high for 200 ms and back (wlanen.c, docs/research/29 W2b)
		--gpu-probe) gpu_probe=1; shift ;;
		--gpu-job) gpu_job=1; gpu_probe=1; gpu_rails=1; gpu_rail_on=1; gpu_power=1; gpu_s2mpu=1; shift ;;	# G2 stage 2: AS0 + one WRITE_VALUE job inside the powered window (gpujob.c)
		--gpu-s2mpu) gpu_s2mpu=1; gpu_probe=1; shift ;;	# READ-ONLY dump of the four G3D S2MPU blocks (gpus2mpu.c)
		--gpu-rail-on) gpu_rail_on=1; gpu_rails=1; gpu_probe=1; shift ;;	# WRITER: S2MPG11 BUCK2S mode on/restore via ACPM (gpurailon.c); implies --gpu-rails
		--gpu-handover) gpu_handover=1; gpu_rail_on=1; gpu_rails=1; gpu_probe=1; shift ;;	# needs --boot-xnu; WRITER: BUCK2S on BEFORE the jump to XNU and left on (XNU powers the cores itself, docs/research/30)
		--gpu-rails) gpu_rails=1; gpu_probe=1; shift ;;	# implies --gpu-probe; READ-ONLY S2MPG11 GPU-rail registers via ACPM (gpurails.c)
		--gpu-clk) gpu_clk=1; gpu_probe=1; shift ;;	# implies --gpu-probe; ACPM DVFS request for G3DL2/G3D (gpuclk.c)
		--gpu-power=stack) gpu_power=1; gpu_probe=1; gpu_stack=1; shift ;;	# + STACK_PWRON/PWROFF (kbase corestack path)
		--gpu-power=*) echo "unknown --gpu-power option '${1#--gpu-power=}' (only: stack)" >&2; exit 2 ;;
		--gpu-power) gpu_power=1; gpu_probe=1; shift ;;	# implies --gpu-probe (the gate lives there)
		--dvfs) dvfs_khz="$2"; shift 2 ;;
		--touch-probe) touch_probe=1; shift ;;
		--touch-probe=*) touch_probe=1; touch_opts="${1#--touch-probe=}"; shift ;;
		*)
			[ -z "$payload" ] || { echo "unexpected argument: $1" >&2; exit 2; }
			payload="$1"; shift ;;
	esac
done
case "$O" in ''|*[!A-Za-z0-9._/-]*) echo "--out DIR: only [A-Za-z0-9._/-]" >&2; exit 2 ;; esac
export O
PYTHON=${PYTHON:-python3}
soc=$("$PYTHON" ../tools/boards.py "$board" --get soc 2>/dev/null) || { echo "unknown board: $board (tools/pix devices)" >&2; exit 2; }
gs101=0; [ "$soc" = google/gs101 ] && gs101=1
if [ "$udump_mib" != 0 ]; then
	# Framed read-only userdata dump over the USB gadget
	# (default OFF). UDUMP_MAX_BLOCKS = 16384 blocks = 64 MiB.
	case "$udump_mib" in ''|*[!0-9]*) echo "--udump needs an integer MiB count" >&2; exit 2 ;; esac
	[ "$udump_mib" -ge 1 ] && [ "$udump_mib" -le 64 ] || { echo "--udump N_MIB must be 1..64" >&2; exit 2; }
	[ "$gs101" = 1 ] || { echo "--udump needs a gs101 board (streams over the gadget)" >&2; exit 2; }
	usb_gadget=1	# the dump needs the CDC-ACM gadget; --usb-gadget is implied
fi

prefix=${CROSS_PREFIX:-aarch64-linux-gnu-}
if ! command -v "${prefix}gcc" >/dev/null 2>&1 && [ -x "$HOME/.local/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf/bin/aarch64-none-elf-gcc" ]; then
	prefix="$HOME/.local/opt/arm-gnu-toolchain-15.2.rel1-x86_64-aarch64-none-elf/bin/aarch64-none-elf-"
fi
CC=${CROSS_CC:-${prefix}gcc}
OBJCOPY=${CROSS_OBJCOPY:-${prefix}objcopy}
READELF=${CROSS_READELF:-${prefix}readelf}
NM=${CROSS_NM:-${prefix}nm}
# tools/third_party is untracked (tools/fetch-android-tools.sh); linked worktrees use the main clone's copy.
tp=../tools/third_party
[ -e "$tp/mkbootimg.py" ] || tp=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/tools/third_party
MKBOOTIMG=${MKBOOTIMG:-$tp/mkbootimg.py}
AVBTOOL=${AVBTOOL:-$tp/avbtool.py}
# Marker line identity: git short hash of this worktree (date fallback
# when detached from git), so the pulled pstore log names its build.
build_id=$(git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d)
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -march=armv8-a+crc -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -Werror -I$O -include $O/board_config.h -DPSTORE_BUILD_ID=\"$build_id\""
if [ "$usb_gadget" = 1 ]; then
	# Stage-2 CDC-ACM log gadget (default OFF): bluejay only (needs try_write32).
	[ "$gs101" = 1 ] || { echo "--usb-gadget needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DUSB_GADGET"
fi
if [ "$wlan_probe" = 1 ]; then
	# Read-only BCM4389 Wi-Fi path probe (default OFF); bluejay only.
	[ "$gs101" = 1 ] || { echo "--wlan-probe needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_PROBE"
fi
if [ "$wlan_rails" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-rails needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_RAILS"
fi
if [ "$wlan_smc_read" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-smc-read needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_SMC_READ"
fi
if [ "$wlan_hold" = 1 ]; then
	[ "$boot_xnu" = 1 ] || { echo "--wlan-hold needs --boot-xnu (the hold exists to hand the link to XNU)" >&2; exit 2; }
	[ -z "$wlan_link_n" ] || [ "$wlan_link_n" = 30 ] || { echo "--wlan-hold implies --wlan-link=30 (the S30 step); --wlan-link=$wlan_link_n is refused" >&2; exit 2; }
	wlan_link_n=30
	CFLAGS="$CFLAGS -DWLAN_HOLD"
fi
if [ "$wlan_link" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-link needs a gs101 board" >&2; exit 2; }
	[ "$wlan_en" = 0 ] && [ "$wlan_iso" = 0 ] && [ "$wlan_smc_read" = 0 ] || { echo "--wlan-link cannot be combined with --wlan-en / --wlan-iso / --wlan-smc-read (separate reviewed units; the chain contains their steps)" >&2; exit 2; }
	case "$wlan_link_n" in ''|[0-9]|[0-9][0-9]) ;; *) echo "--wlan-link=N needs a step number 0..36" >&2; exit 2 ;; esac
	[ -z "$wlan_link_n" ] || [ "$wlan_link_n" -le 36 ] || { echo "--wlan-link=N: N must be <= 36 (the last step of soc/google/gs101/wlan_seq.json)" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_LINK_RUN"
	[ -z "$wlan_link_n" ] || CFLAGS="$CFLAGS -DWLAN_LINK_N=$wlan_link_n"
fi
if [ "$wlan_iso" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-iso needs a gs101 board" >&2; exit 2; }
	[ "$wlan_en" = 0 ] || { echo "--wlan-iso cannot be combined with --wlan-en (the WLAN_EN writer must not run when the PHY isolation may be left released; review 2 F2)" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_ISO_RUN"
fi
if [ "$wlan_en" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-en needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_EN_RUN"
fi
if [ "$gpu_probe" = 1 ]; then
	# Read-only Mali-G78 probe (default OFF); bluejay only.
	[ "$gs101" = 1 ] || { echo "--gpu-probe needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_PROBE"
fi
if [ "$gpu_rails" = 1 ]; then
	# Opt-in read-only S2MPG11 GPU-rail dump through the ACPM PMIC read path (gpurails.c); bluejay only.
	[ "$gs101" = 1 ] || { echo "--gpu-rails needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_RAILS"
fi
if [ "$gpu_job" = 1 ]; then
	# Opt-in WRITER (docs/research/28): AS0 programming + one WRITE_VALUE job inside the power window; needs the rail on (implied) and the plain power cycle.
	[ "$gs101" = 1 ] || { echo "--gpu-job needs a gs101 board" >&2; exit 2; }
	[ "$gpu_stack" = 0 ] || { echo "--gpu-job cannot be combined with --gpu-power=stack" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_JOB"
fi
if [ "$gpu_s2mpu" = 1 ]; then
	# Opt-in READ-ONLY dump of the G3D S2MPU registers (gpus2mpu.c); bluejay only, runs right after the read-only probe.
	[ "$gs101" = 1 ] || { echo "--gpu-s2mpu needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_S2MPU"
fi
if [ "$gpu_rail_on" = 1 ]; then
	# Opt-in WRITER (BUCK2S mode on before the power cycle, restored afterwards); bluejay only.
	[ "$gs101" = 1 ] || { echo "--gpu-rail-on needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_RAIL_ON"
fi
if [ "$gpu_clk" = 1 ]; then
	# Opt-in GPU clock request through the ACPM DVFS client (gpuclk.c); bluejay only, runs after the read-only probe and before --gpu-power.
	[ "$gs101" = 1 ] || { echo "--gpu-clk needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_CLK"
fi
if [ "$gpu_power" = 1 ]; then
	# Opt-in Mali-G78 power on/off cycle (gpupower.c, docs/research/17 stage 1); bluejay only, runs after the read-only probe.
	[ "$gs101" = 1 ] || { echo "--gpu-power needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_POWER"
fi
if [ "$touch_probe" != 0 ]; then
	# Touchscreen SPI11/USI11 probe (default OFF); bluejay only. =run adds the XNU driver's writes (allowlisted).
	[ "$gs101" = 1 ] || { echo "--touch-probe needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DTOUCH_PROBE"
	# --touch-probe=[run][,mux,cs,osc|top,loop,rst,misopu][,railsr|rails] (docs/research/21 section 3, docs/research/23); unknown words are an error.
	# railsr = ACPM read of the two touch rails (acpm.c), rails = railsr + set their enable bit 7; both work without run.
	touch_bits=0; touch_run=0; touch_spi=0; touch_need_run=0
	if [ -n "$touch_opts" ]; then
		IFS=, read -ra touch_words <<< "$touch_opts"
		for w in "${touch_words[@]}"; do
			touch_spi=1
			case "$w" in
				run) touch_run=1; b=0 ;;
				mux) b=1 ;; cs) b=2 ;; osc) b=4 ;; top) b=8 ;; loop) b=16 ;; rst) b=32 ;; misopu) b=64 ;;
				railsr) b=128; touch_spi=0 ;; rails) b=384; touch_spi=0 ;;
				dvfsr) b=512; touch_spi=0 ;; dvfs) b=1536; touch_spi=0 ;;
				*) echo "unknown --touch-probe option '$w' (run,mux,cs,osc,top,loop,rst,misopu,railsr,rails,dvfsr,dvfs)" >&2; exit 2 ;;
			esac
			touch_bits=$((touch_bits | b))
			[ "$touch_spi" = 0 ] || touch_need_run=1
		done
		[ "$touch_need_run" = 0 ] || [ "$touch_run" = 1 ] || { echo "--touch-probe=$touch_opts: 'run' is required for the SPI options (writes are opt-in)" >&2; exit 2; }
		[ $((touch_bits & 12)) != 12 ] || { echo "--touch-probe: osc and top are exclusive" >&2; exit 2; }
		CFLAGS="$CFLAGS -DTOUCH_OPTS=$touch_bits"
		[ "$touch_run" = 0 ] || CFLAGS="$CFLAGS -DTOUCH_PROBE_RUN"
		[ $((touch_bits & 128)) = 0 ] || { CFLAGS="$CFLAGS -DTOUCH_RAILS"; touch_rails=1; }
		[ $((touch_bits & 256)) = 0 ] || CFLAGS="$CFLAGS -DTOUCH_RAILS_WRITE"
		# dvfsr = ACPM FREQ_GET of the CPU clusters (docs/research/25); dvfs = dvfsr + FREQ_REQ of the --dvfs K0,K1,K2 kHz (table rows at or below the ceilings)
		[ $((touch_bits & 512)) = 0 ] || { CFLAGS="$CFLAGS -DTOUCH_DVFS"; touch_dvfs=1; }
		[ -z "$dvfs_khz" ] || [ $((touch_bits & 1024)) != 0 ] || { echo "--dvfs needs --touch-probe=dvfs (dvfsr only reads)" >&2; exit 2; }
		if [ $((touch_bits & 1024)) != 0 ]; then
			[ -n "$dvfs_khz" ] || { echo "--touch-probe=dvfs needs --dvfs CL0_KHZ,CL1_KHZ,CL2_KHZ (0 = leave that cluster)" >&2; exit 2; }
			dv=$("$PYTHON" ../tools/boards.py "$board" --dvfs-check "$dvfs_khz") || exit 2
			set -- $dv
			CFLAGS="$CFLAGS -DTOUCH_DVFS_WRITE -DDVFS_KHZ0=$1 -DDVFS_KHZ1=$2 -DDVFS_KHZ2=$3"
		fi
	fi
elif [ -n "$dvfs_khz" ]; then
	echo "--dvfs needs --touch-probe=dvfs" >&2; exit 2
fi
if [ "$boot_xnu" = 1 ]; then
	# Jump into the SWEETXNU payload (ramdisk) right after display setup; bluejay only.
	[ "$gs101" = 1 ] || { echo "--boot-xnu needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DBOOT_XNU"
fi
if [ "$gpu_handover" = 1 ]; then
	[ "$boot_xnu" = 1 ] || { echo "--gpu-handover needs --boot-xnu" >&2; exit 2; }
	CFLAGS="$CFLAGS -DGPU_HANDOVER"
fi
if [ -n "${USB_WINDOW:-}" ]; then
	CFLAGS="$CFLAGS -DUSB_GADGET_SECONDS=$USB_WINDOW"	# dev: shorter gadget window
fi
if [ -n "${REBOOT_BL:-}" ]; then
	CFLAGS="$CFLAGS -DREBOOT_BL"	# end-of-run reset goes to the bootloader (docs/research/10), no button press
fi
if [ -n "${XNU_UFS:-}" ]; then
	CFLAGS="$CFLAGS -DXNU_UFS"	# --boot-xnu: cold-init UFS before handing over (needs an ADT with /sweet-ufs)
fi
if [ -n "${UFS_DMA_HI_BASE:-}" ]; then
	CFLAGS="$CFLAGS -DUFS_DMA_HI_BASE=$UFS_DMA_HI_BASE"	# dev: UFS buffers at this PA (e.g. 0x880100000ULL)
fi
if [ -n "${USB_DMA_HI_BASE:-}" ]; then
	CFLAGS="$CFLAGS -DUSB_DMA_HI_BASE=$USB_DMA_HI_BASE"	# dev: gadget DMA pool at this PA (e.g. 0x880200000ULL)
fi
if [ -n "${UFS_ATTEMPTS:-}" ]; then
	CFLAGS="$CFLAGS -DUFS_ATTEMPTS=$UFS_ATTEMPTS"	# dev: 0 = survey only
fi
if [ "$pstore_fast" = 1 ]; then
	# Skip the ~10-minute photo pager: final pstore save, then PSCI reset
	# ~5 s later so Android comes up and the log can be pulled.
	CFLAGS="$CFLAGS -DPSTORE_FAST"
fi
if [ "$udump_mib" != 0 ]; then
	CFLAGS="$CFLAGS -DUDUMP -DUDUMP_MIB=$udump_mib"
fi
if [ "$qrlog" = 1 ]; then
	# Log as looping QR codes on the final screens (tools/qr-log.py reads them). Not with --pstore-fast (no pager).
	CFLAGS="$CFLAGS -DQRLOG -I../qrlog -I../qrlog/stubs"
fi
mkdir -p "$O"
"$PYTHON" ../tools/boards.py "$board" $O/board_config.h
[ "$gs101" = 1 ] && "$PYTHON" ../tools/boards.py "$board" $O/wlan_seq.h --wlan-seq
for f in start.S shim.c console.c ramlog.c mmu.c fdt.c pstore.c loader.c payload.c ufs.c usbprobe.c; do
	"$CC" $CFLAGS -c "$f" -o "$O/${f%.*}.o"
done
# SoC-specific UFS code lives with the SoC (soc/<vendor>/<soc>/shim); same object names as before.
socshim=../$("$PYTHON" ../tools/boards.py "$board" --get soc_dir)/shim
for f in ufsdiag ufsinit; do
	"$CC" $CFLAGS -I"$socshim" -I. -c "$socshim/$f.c" -o "$O/$f.o"
done
# Gate honesty: usbproto.c and usbgadget.c are
# cross-compiled with the shim CFLAGS on EVERY build so the object
# audits in tools/test-bluejay-boot.py always see them; they are only
# LINKED with --usb-gadget, so the default images stay unchanged.
# usbgadget.o calls try_write32, which shim.c defines under
# SOC_GS101, so --usb-gadget is only meaningful with a gs101 board.
"$CC" $CFLAGS -c usbproto.c -o $O/usbproto.o
"$CC" $CFLAGS -c usbgadget.c -o $O/usbgadget.o
"$CC" $CFLAGS -c usbpool.c -o $O/usbpool.o
# Gate honesty: udump.c is cross-compiled with
# the shim CFLAGS on EVERY build so the object audit in
# tools/test-bluejay-boot.py always sees it; it is only LINKED with
# --udump (which also forces --usb-gadget), so default images stay
# byte-identical.
"$CC" $CFLAGS -c udump.c -o $O/udump.o
# Same for wlanprobe.c (read-only audit sees it every build); only LINKED with --wlan-probe.
"$CC" $CFLAGS -c wlanprobe.c -o $O/wlanprobe.o
# Same for wlanrails.c (read-only rail / CMU / PMU words, docs/research/29); only LINKED with --wlan-rails. Reads only (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c wlanrails.c -o $O/wlanrails.o
# Same for wlansmc.c (W2a: two SMC READs); built with -DWLAN_SMC_READ on every build so the audit sees the real code (the one smc instruction), only LINKED with --wlan-smc-read.
# wlanlink.c (W3a, the table-driven chain; audited every build, linked only with --wlan-link) and wlansmc_link.o = wlansmc.c with the two chain steps (wlan_link_iso_set/clear) INSTEAD of wlan_iso_run
"$CC" $CFLAGS -DWLAN_LINK_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlanlink.c -o $O/wlanlink.o
if [ "$wlan_link" = 1 ]; then	# the effective step bound N, read from the preprocessed wlanlink.c (its _Static_assert(N <= LAST)); N >= 20 is accepted only for an explicit --wlan-link=N
	eff_n=$("$CC" $CFLAGS -DWLAN_LINK_RUN -E wlanlink.c | sed -n 's/.*_Static_assert(\([0-9][0-9]*\)u\{0,1\} <= .*/\1/p')
	"$PYTHON" ../tools/test-bluejay-boot.py --wlan-link-effective "${eff_n:-none}" --wlan-link-explicit "$wlan_link_n"
fi
"$CC" $CFLAGS -DWLAN_SMC_READ -DWLAN_ISO_RUN -DWLAN_LINK_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o $O/wlansmc_link.o
"$CC" $CFLAGS -UWLAN_ISO_RUN -UWLAN_LINK_RUN -DWLAN_SMC_READ -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o $O/wlansmc.o
# and wlansmc_iso.o = the same file with the W2c RMW writer (WLAN_ISO_RUN), audited every build, linked INSTEAD of wlansmc.o with --wlan-iso (an --wlan-smc-read image contains no RMW code)
"$CC" $CFLAGS -UWLAN_LINK_RUN -DWLAN_SMC_READ -DWLAN_ISO_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o $O/wlansmc_iso.o
# Same for wlanen.c (the opt-in WLAN_EN writer); built with -DWLAN_EN_RUN on every build so the audit sees the real code, only LINKED with --wlan-en. Its two writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -DWLAN_EN_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlanen.c -o $O/wlanen.o
# Same for gpuprobe.c; only LINKED with --gpu-probe. Its name switches must not become
# .rodata pointer tables (ABS64 relocations, rejected below).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpuprobe.c -o $O/gpuprobe.o
# Same for gpupower.c (the opt-in GPU power cycle); only LINKED with --gpu-power. Its writes go through one allowlisted helper (audited).
NOJOB_CFLAGS="${CFLAGS//-DGPU_JOB/}"	# the plain / stack gpupower objects never carry the --gpu-job hook (their audit forbids the extra undefined symbol)
"$CC" $NOJOB_CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o $O/gpupower.o
# and its --gpu-power=stack variant (extra STACK_PWRON/PWROFF writes), also audited every build, linked only with --gpu-power=stack
"$CC" $NOJOB_CFLAGS -DGPU_POWER_STACK -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o $O/gpupower_stack.o
# Same for gpuclk.c (the opt-in GPU clock request); only LINKED with --gpu-clk. It has no MMIO writer of its own (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpuclk.c -o $O/gpuclk.o
# Same for gpus2mpu.c (read-only S2MPU dump); only LINKED with --gpu-s2mpu (or --gpu-job). Reads only, audited like gpuprobe.o.
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpus2mpu.c -o $O/gpus2mpu.o
# Same for gpujob.c (the stage-2 job writer; only LINKED with --gpu-job) and gpupower_job.o (gpupower.c with the one `gpu_job_run()` hook in the powered window; linked instead of gpupower.o
# with --gpu-job). Both are audited on every build (writer audit / object audit in tools/test-bluejay-boot.py).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpujob.c -o $O/gpujob.o
"$CC" $CFLAGS -DGPU_JOB -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o $O/gpupower_job.o
# Same for gpurails.c (read-only GPU-rail dump); only LINKED with --gpu-rails. No MMIO of its own (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpurails.c -o $O/gpurails.o
# Same for gpurailon.c (the opt-in rail writer's orchestration); only LINKED with --gpu-rail-on. No MMIO of its own (audited). Built with -DGPU_RAIL_ON on every
# build so the audit sees the real code; acpm_railon.o is acpm.c with the GPU_RAIL_ON writer, for the audit only (the image links acpm.o built with the image's flags).
"$CC" $CFLAGS -DGPU_RAIL_ON -fno-tree-switch-conversion -fno-jump-tables -c gpurailon.c -o $O/gpurailon.o
"$CC" $CFLAGS -DGPU_RAIL_ON -fno-tree-switch-conversion -fno-jump-tables -c acpm.c -o $O/acpm_railon.o
# Same for touchprobe.c; only LINKED with --touch-probe. Its writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c touchprobe.c -o $O/touchprobe.o
# Same for acpm.c (ACPM client for the touch rails); only LINKED with --touch-probe=railsr|rails. Its writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c acpm.c -o $O/acpm.o
qr_objs=
if [ "$qrlog" = 1 ]; then
	for f in qrlog qrcodegen qrlog_libc; do
		"$CC" $CFLAGS -Wno-unused-parameter -Wno-sign-compare -c "../qrlog/$f.c" -o "$O/$f.o"
	done
	qr_objs="$O/qrlog.o $O/qrcodegen.o $O/qrlog_libc.o"
fi
usb_objs=
if [ "$usb_gadget" = 1 ]; then
	usb_objs="$O/usbproto.o $O/usbgadget.o"
fi
if [ "$udump_mib" != 0 ]; then
	usb_objs="$usb_objs $O/udump.o"
fi
if [ "$wlan_probe" = 1 ]; then
	usb_objs="$usb_objs $O/wlanprobe.o"
fi
if [ "$wlan_rails" = 1 ]; then
	usb_objs="$usb_objs $O/wlanrails.o"
fi
if [ "$wlan_link" = 1 ]; then
	usb_objs="$usb_objs $O/wlanlink.o $O/wlansmc_link.o"
elif [ "$wlan_iso" = 1 ]; then
	usb_objs="$usb_objs $O/wlansmc_iso.o"
elif [ "$wlan_smc_read" = 1 ]; then
	usb_objs="$usb_objs $O/wlansmc.o"
fi
if [ "$wlan_en" = 1 ]; then
	usb_objs="$usb_objs $O/wlanen.o"
fi
if [ "$gpu_probe" = 1 ]; then
	usb_objs="$usb_objs $O/gpuprobe.o"
fi
if [ "$gpu_clk" = 1 ]; then
	usb_objs="$usb_objs $O/gpuclk.o"
fi
if [ "$gpu_s2mpu" = 1 ]; then
	usb_objs="$usb_objs $O/gpus2mpu.o"
fi
if [ "$gpu_rails" = 1 ]; then
	usb_objs="$usb_objs $O/gpurails.o"
fi
if [ "$gpu_rail_on" = 1 ]; then
	usb_objs="$usb_objs $O/gpurailon.o"
fi
if [ "$gpu_job" = 1 ]; then
	usb_objs="$usb_objs $O/gpupower_job.o $O/gpujob.o"
elif [ "$gpu_stack" = 1 ]; then
	usb_objs="$usb_objs $O/gpupower_stack.o"
elif [ "$gpu_power" = 1 ]; then
	usb_objs="$usb_objs $O/gpupower.o"
fi
if [ "$touch_probe" != 0 ]; then
	usb_objs="$usb_objs $O/touchprobe.o"
fi
if [ "$touch_rails" = 1 ] || [ "$touch_dvfs" = 1 ] || [ "$gpu_clk" = 1 ] || [ "$gpu_rails" = 1 ] || [ "$wlan_rails" = 1 ]; then
	usb_objs="$usb_objs $O/acpm.o"
fi
abs=$("$READELF" -r $O/shim.o $O/console.o $O/ramlog.o $O/mmu.o $O/fdt.o $O/pstore.o $O/loader.o $O/payload.o $O/ufs.o $O/ufsdiag.o $O/ufsinit.o $O/usbprobe.o $O/usbproto.o $O/usbgadget.o $O/usbpool.o $O/udump.o $O/wlanprobe.o $O/wlanrails.o $O/wlanen.o $O/wlansmc.o $O/wlansmc_iso.o $O/wlansmc_link.o $O/wlanlink.o $O/gpuprobe.o $O/gpupower.o $O/gpupower_stack.o $O/gpupower_job.o $O/gpujob.o $O/gpuclk.o $O/gpus2mpu.o $O/gpurails.o $O/gpurailon.o $O/acpm_railon.o $O/touchprobe.o $O/acpm.o $qr_objs | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
"$CC" $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	$O/start.o $O/shim.o $O/console.o $O/ramlog.o $O/mmu.o $O/fdt.o $O/pstore.o $O/loader.o $O/payload.o $O/ufs.o $O/ufsdiag.o $O/ufsinit.o $O/usbprobe.o $O/usbpool.o $usb_objs $qr_objs -o $O/shim.elf
"$OBJCOPY" -O binary $O/shim.elf $O/Image
size_hex=$("$NM" $O/shim.elf | awk '$3=="_end"{print $1}')
size=$((16#$size_hex))
truncate -s "$size" $O/Image
if [ -n "$payload" ]; then
	cp "$payload" $O/ramdisk.bin
else
	(cd "$O" && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

if [ "$board" = sweet ]; then
	dtb=${SWEET_DTB:-../dtb/boot_unpacked/dtb}
	"$PYTHON" "$MKBOOTIMG" --header_version 2 --kernel $O/Image --ramdisk $O/ramdisk.bin \
		--dtb "$dtb" --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --dtb_offset 0x1f00000 --pagesize 4096 \
		--os_version 17.0.0 --os_patch_level 2035-06 --cmdline "console=null" -o $O/shim-boot.img
else
	"$PYTHON" "$MKBOOTIMG" --header_version 4 --kernel $O/Image --ramdisk $O/ramdisk.bin \
		--pagesize 4096 --os_version 12.0.0 --os_patch_level 2022-07 \
		--cmdline "console=null" -o $O/shim-boot.img
	"$PYTHON" -c 'import lz4.frame, pathlib, sys; o = pathlib.Path(sys.argv[1]); (o / "Image.lz4").write_bytes(lz4.frame.compress((o / "Image").read_bytes()))' "$O"
	"$PYTHON" "$MKBOOTIMG" --header_version 4 --kernel $O/Image.lz4 --ramdisk $O/ramdisk.bin \
		--pagesize 4096 --os_version 12.0.0 --os_patch_level 2022-07 \
		--cmdline "console=null" -o $O/shim-boot-lz4.img
	if [ "$avb" = 1 ]; then
		key=out/bluejay-testkey.pem	# shared by every --out dir (read-only for them; the gate creates it in its first build before the parallel ones)
		mkdir -p out; [ -f "$key" ] || { openssl genrsa -out "$key.$$" 2048 && mv -n "$key.$$" "$key"; rm -f "$key.$$"; }
		cp $O/shim-boot.img $O/shim-boot-avb.img
		"$PYTHON" "$AVBTOOL" add_hash_footer --image $O/shim-boot-avb.img \
			--partition_name boot --partition_size "${AVB_PARTITION_SIZE:-67108864}" \
			--key "$key" --algorithm SHA256_RSA2048
		cp $O/shim-boot-lz4.img $O/shim-boot-lz4-avb.img
		"$PYTHON" "$AVBTOOL" add_hash_footer --image $O/shim-boot-lz4-avb.img \
			--partition_name boot --partition_size "${AVB_PARTITION_SIZE:-67108864}" \
			--key "$key" --algorithm SHA256_RSA2048
	fi
fi
# Host tests do not depend on the variant: skip them when the same inputs already passed (stamp written only after all passed).
# PIXELIOS_HOST_TESTS=always runs them anyway (the gate's first build does) and refreshes the stamp. Inputs: shim/*.c *.h, the generated board/wlan_seq headers, the xnu patches this script extracts headers from, gcc -v, this script.
chk() { local what="$1" prev="" a; shift; for a; do [ "$prev" = -o ] && case $a in *host_test*) echo "${a##*/}" >>"$O/host-tests.list" ;; esac; prev=$a; done	# a failed compile or test must stop the build; every compiled *host_test* output is listed for the gate (check-all.sh host-tests fails if one is missing)
	if "$@"; then echo "PASS $what"; else echo "FAIL $what" >&2; exit 1; fi; }
htdir=${XDG_CACHE_HOME:-$HOME/.cache}/pixelios/host-tests
# shellcheck disable=SC2046  # intentional word splitting of the file lists (an empty "" argument would break sha256sum)
htdigest() { { sha256sum *.c *.h $O/board_config.h $([ -f $O/wlan_seq.h ] && echo $O/wlan_seq.h) $(grep -o '\.\./xnu/patches/[0-9][0-9]*-[A-Za-z0-9._-]*\.patch' build.sh | sort -u) build.sh; gcc -v 2>&1; } | sha256sum | cut -c1-32; }
ht=$(htdigest)
if [ -e "$htdir/$ht.ok" ] && [ "${PIXELIOS_HOST_TESTS:-}" != always ]; then
	echo "host tests: cached $ht"
else
	: >"$O/host-tests.list"
	chk "host_test (compile)" gcc -O1 -Wall -o $O/host_test console.c ramlog.c host_test.c
	chk "host_test" $O/host_test $O/host.ppm
	chk "ramlog_host_test (compile)" gcc -O1 -Wall -Wextra -o $O/ramlog_host_test ramlog.c ramlog_host_test.c
	chk "ramlog_host_test" $O/ramlog_host_test
	chk "fdt_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/fdt_host_test fdt.c fdt_host_test.c
	chk "fdt_host_test" $O/fdt_host_test
	chk "pstore_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/pstore_host_test fdt.c pstore.c pstore_host_test.c
	chk "pstore_host_test" $O/pstore_host_test
	chk "usbprobe_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbprobe_host_test fdt.c usbprobe.c usbprobe_host_test.c
	chk "usbprobe_host_test" $O/usbprobe_host_test
	chk "usbproto_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbproto_host_test usbproto.c usbproto_host_test.c
	chk "usbproto_host_test" $O/usbproto_host_test
	chk "usbgadget_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbgadget_host_test usbproto.c usbgadget.c usbgadget_host_test.c
	chk "usbgadget_host_test" $O/usbgadget_host_test
	chk "usbpool_host_test (compile)" gcc -O1 -Wall -Wextra -I. -DUSB_DMA_HI_BASE=1 -o $O/usbpool_host_test usbpool.c usbpool_host_test.c
	chk "usbpool_host_test" $O/usbpool_host_test
	chk "wlanprobe_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/wlanprobe_host_test wlanprobe_host_test.c
	chk "wlanprobe_host_test" $O/wlanprobe_host_test
	# --wlan-rails (wlanrails.c) against the fake APM of acpm_host_test.c; --wlan-en (wlanen.c) against a fake GPIO bank. try_write32 is deliberately absent from the rails test (linking fails if wlanrails.c ever writes).
	chk "wlanrails_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -I$O -include $O/board_config.h -o $O/wlanrails_host_test wlanrails_host_test.c
	chk "wlanrails_host_test" $O/wlanrails_host_test
	chk "wlanen_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DWLAN_EN_RUN -I. -I$O -include $O/board_config.h -o $O/wlanen_host_test wlanen_host_test.c
	chk "wlanen_host_test" $O/wlanen_host_test
	chk "wlansmc_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DWLAN_SMC_READ -I. -I$O -include $O/board_config.h -o $O/wlansmc_host_test wlansmc_host_test.c
	chk "wlansmc_host_test" $O/wlansmc_host_test
	chk "wlaniso_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DWLAN_SMC_READ -DWLAN_ISO_RUN -I. -I$O -include $O/board_config.h -o $O/wlaniso_host_test wlaniso_host_test.c
	chk "wlaniso_host_test" $O/wlaniso_host_test
	# --wlan-link (wlanlink.c) against a fake SoC, once per step bound N (S22 is a documented skip: one 0 us delay row; N=20..36 run in the fake SoC; S24 = read-only link report, S25 = read-only iATU layout, S26 = iATU region 0 programmed + CFG window reads, S27 = read-only endpoint config dump + iATU region 1 reset values, S28 = BAR sizing + assignment, S29 = region 1 + BAR0 window + Command MEM, S30 = the first BAR0 read, S31 = read-only ChipCommon eromptr / clk_ctl_st / capabilities_ext, S32 = BAR0 window move to the EROM page + 896 dword dump, S33 = read-only PMU / ARM CA7 wrapper / SYS_MEM wrapper state through three more window moves, S34 = SRPWR request, S35 = SYS_MEM core reset + bank sizing, S36 = ARM CA7 halt: the first BAR0 writes, research 40)
	for n in 7 8 10 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36; do
		chk "wlanlink_host_test$n (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DWLAN_LINK_RUN -DWLAN_LINK_N=$n -I. -I$O -include $O/board_config.h -o $O/wlanlink_host_test$n wlanlink_host_test.c
		chk "wlanlink_host_test$n" $O/wlanlink_host_test$n
	done
	chk "gpuprobe_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -o $O/gpuprobe_host_test gpuprobe_host_test.c
	chk "gpuprobe_host_test" $O/gpuprobe_host_test
	chk "gpuprobe_dump_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/gpuprobe_dump_host_test gpuprobe_dump_host_test.c
	chk "gpuprobe_dump_host_test" $O/gpuprobe_dump_host_test
	chk "gpus2mpu_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/gpus2mpu_host_test gpus2mpu_host_test.c
	chk "gpus2mpu_host_test" $O/gpus2mpu_host_test
	chk "gpujob_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/gpujob_host_test gpus2mpu.c gpujob_host_test.c
	chk "gpujob_host_test" $O/gpujob_host_test
	chk "gpupower_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/gpupower_host_test gpupower_host_test.c
	chk "gpupower_host_test" $O/gpupower_host_test
	chk "gpupower_stack_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DGPU_POWER_STACK -I. -I$O -include $O/board_config.h -o $O/gpupower_stack_host_test gpupower_host_test.c
	chk "gpupower_stack_host_test" $O/gpupower_stack_host_test
	# --gpu-clk (gpuclk.c) against the fake DVFS-channel APM of dvfs_host_test.c; the two gate variants use a copy of the board header with one ACPM gate forced to 0
	sed 's/^#define ACPM_LAYOUT_VERIFIED 1$/#define ACPM_LAYOUT_VERIFIED 0/' $O/board_config.h > $O/board_config_l0.h
	sed 's/^#define ACPM_ELEM_UNIT_VERIFIED 1$/#define ACPM_ELEM_UNIT_VERIFIED 0/' $O/board_config.h > $O/board_config_u0.h
	GCLK="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DGPU_CLK -I."
	chk "gpuclk_host_test (compile)" $GCLK -I$O -include $O/board_config.h -o $O/gpuclk_host_test gpuclk_host_test.c
	chk "gpuclk_host_test" $O/gpuclk_host_test
	chk "gpuclk_gate_l_host_test (compile)" $GCLK -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/gpuclk_gate_l_host_test gpuclk_host_test.c
	chk "gpuclk_gate_l_host_test" $O/gpuclk_gate_l_host_test
	chk "gpuclk_gate_u_host_test (compile)" $GCLK -DGATE_TEST -DGATE_UNIT -I$O -include $O/board_config_u0.h -o $O/gpuclk_gate_u_host_test gpuclk_host_test.c
	chk "gpuclk_gate_u_host_test" $O/gpuclk_gate_u_host_test
	# --gpu-rails (gpurails.c) against the fake APM of acpm_host_test.c; the gate variants again use the sed'ed headers, a null-config variant drops ACPM_S11_KNOWN
	GRL="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -I."
	chk "gpurails_host_test (compile)" $GRL -DTOUCH_RAILS_WRITE -I$O -include $O/board_config.h -o $O/gpurails_host_test gpurails_host_test.c
	chk "gpurails_host_test" $O/gpurails_host_test
	chk "gpurails_gate_l_host_test (compile)" $GRL -DTOUCH_RAILS_WRITE -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/gpurails_gate_l_host_test gpurails_host_test.c
	chk "gpurails_gate_l_host_test" $O/gpurails_gate_l_host_test
	chk "gpurails_gate_u_host_test (compile)" $GRL -DTOUCH_RAILS_WRITE -DGATE_TEST -DGATE_UNIT -I$O -include $O/board_config_u0.h -o $O/gpurails_gate_u_host_test gpurails_host_test.c
	chk "gpurails_gate_u_host_test" $O/gpurails_gate_u_host_test
	sed 's/^#define ACPM_S11_KNOWN 1$/#define ACPM_S11_KNOWN 0/' $O/board_config.h > $O/board_config_s0.h
	sed 's/^#define ACPM_S11_ON_KNOWN 1$/#define ACPM_S11_ON_KNOWN 0/' $O/board_config.h > $O/board_config_n0.h
	chk "gpurails_null_host_test (compile)" $GRL -DTOUCH_RAILS_WRITE -DNULL_TEST -I$O -include $O/board_config_s0.h -o $O/gpurails_null_host_test gpurails_host_test.c
	chk "gpurails_null_host_test" $O/gpurails_null_host_test
	# --gpu-rail-on (gpurailon.c, the S2MPG11 BUCK2S writer) against the same fake APM; gate variants and a null rail_on block
	GRO="$GRL -DTOUCH_RAILS_WRITE -DGPU_RAIL_ON"
	chk "gpurailon_host_test (compile)" $GRO -I$O -include $O/board_config.h -o $O/gpurailon_host_test gpurailon_host_test.c
	chk "gpurailon_host_test" $O/gpurailon_host_test
	chk "gpurailon_gate_l_host_test (compile)" $GRO -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/gpurailon_gate_l_host_test gpurailon_host_test.c
	chk "gpurailon_gate_l_host_test" $O/gpurailon_gate_l_host_test
	chk "gpurailon_gate_u_host_test (compile)" $GRO -DGATE_TEST -DGATE_UNIT -I$O -include $O/board_config_u0.h -o $O/gpurailon_gate_u_host_test gpurailon_host_test.c
	chk "gpurailon_gate_u_host_test" $O/gpurailon_gate_u_host_test
	chk "gpurailon_null_host_test (compile)" $GRO -DNULL_TEST -I$O -include $O/board_config_n0.h -o $O/gpurailon_null_host_test gpurailon_host_test.c
	chk "gpurailon_null_host_test" $O/gpurailon_null_host_test
	chk "touch_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/touch_host_test touch_host_test.c
	chk "touch_host_test" $O/touch_host_test
	chk "acpm_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DTOUCH_RAILS_WRITE -I. -I$O -include $O/board_config.h -o $O/acpm_host_test acpm_host_test.c
	chk "acpm_host_test" $O/acpm_host_test
	for v in 0 1; do chk "rails_host_test$v (compile)" gcc -O1 -Wall -Wextra -Werror -DRT_VERIFIED=$v -I. -I$O -include $O/board_config.h -o $O/rails_host_test$v rails_host_test.c; chk "rails_host_test$v" $O/rails_host_test$v; done
	# ACPM DVFS client (docs/research/25): the fake channel-1 APM; the read-only build has no request tests (no acpm_dvfs_set there).
	# Every check below FAILS the build (a plain `a && b && echo PASS` chain only skips the PASS line under set -e: review dvfs-1 M1).
	chk "dvfs_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DTOUCH_DVFS_WRITE -I. -I$O -include $O/board_config.h -o $O/dvfs_host_test dvfs_host_test.c
	chk "dvfs_host_test (rw)" $O/dvfs_host_test
	chk "dvfs_host_test_ro (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/dvfs_host_test_ro dvfs_host_test.c
	chk "dvfs_host_test (ro)" $O/dvfs_host_test_ro
	for w in 0 1; do
		chk "dvfs_probe_host_test$w (compile)" gcc -O1 -Wall -Wextra -Werror -DDP_WRITE=$w -I. -I$O -include $O/board_config.h -o $O/dvfs_probe_host_test$w dvfs_probe_host_test.c
		chk "dvfs_probe_host_test DP_WRITE=$w" $O/dvfs_probe_host_test$w
	done
	# review 2 M3: a read-only (railsr) image must not contain the UPDATE path
	gcc -O1 -w -DTOUCH_PROBE -DTOUCH_RAILS -I. -I$O -include $O/board_config.h -c -o $O/acpm_ro.o acpm.c
	chk "railsr object has no acpm_pmic_update" sh -c '! nm $O/acpm_ro.o | grep -q acpm_pmic_update'
	# the same for DVFS: a dvfsr object has the read path and no request path (acpm_dvfs_set); the dvfs object has both
	gcc -O1 -w -DTOUCH_PROBE -DTOUCH_DVFS -I. -I$O -include $O/board_config.h -c -o $O/acpm_dvfs_ro.o acpm.c
	chk "dvfsr object has acpm_dvfs_get" sh -c 'nm $O/acpm_dvfs_ro.o | grep -q acpm_dvfs_get'
	chk "dvfsr object has no acpm_dvfs_set" sh -c '! nm $O/acpm_dvfs_ro.o | grep -q acpm_dvfs_set'
	gcc -O1 -w -DTOUCH_PROBE -DTOUCH_DVFS -DTOUCH_DVFS_WRITE -I. -I$O -include $O/board_config.h -c -o $O/acpm_dvfs_rw.o acpm.c
	chk "dvfs object has acpm_dvfs_set" sh -c 'nm $O/acpm_dvfs_rw.o | grep -q acpm_dvfs_set'
	# a --gpu-clk object has the GPU-only request path and NOT the CPU one; a plain one has neither
	gcc -O1 -w -DGPU_CLK -I. -I$O -include $O/board_config.h -c -o $O/acpm_gpuclk.o acpm.c
	chk "gpu-clk acpm object has acpm_dvfs_reassert and no acpm_dvfs_set" sh -c 'nm $O/acpm_gpuclk.o | grep -q acpm_dvfs_reassert && ! nm $O/acpm_gpuclk.o | grep -q acpm_dvfs_set'
	chk "acpm objects built without --gpu-clk have no acpm_dvfs_reassert" sh -c '! nm $O/acpm_ro.o $O/acpm_dvfs_ro.o $O/acpm_dvfs_rw.o | grep -q acpm_dvfs_reassert'
	chk "udump_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -o $O/udump_host_test udump.c udump_host_test.c
	chk "udump_host_test" $O/udump_host_test
	# the debounce under test is the header shipped in xnu patch 0087 (new-file hunk: drop the leading "+")
	awk '/^\+\+\+ b\/bsd\/dev\/sweet_keys_gs101_debounce.h$/{f=1;next} f&&/^diff --git/{exit} f&&/^@@/{next} f{print substr($0,2)}' ../xnu/patches/0087-sweet-gs101-keys.patch > $O/sweet_keys_gs101_debounce.h
	chk "keys_gs101_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I"$O" -o $O/keys_gs101_host_test keys_gs101_host_test.c
	chk "keys_gs101_host_test" $O/keys_gs101_host_test
	# pure FTM5 protocol header of the Pixel 6a touch driver: extracted from xnu patch 0088 so the test sees the kernel's bytes
	awk '$0=="+++ b/bsd/dev/sweet_ftm5.h"{on=1;next} on&&/^--- /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0088-sweet-gs101-touch.patch > $O/sweet_ftm5.h
	chk "ftm5_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/ftm5_host_test ftm5_host_test.c
	chk "ftm5_host_test" $O/ftm5_host_test
	# stage-1 BCM4389 chip layer of xnu patch 0124 (new-file hunk of bsd/dev/sweet_wlan_chip.h): replayed against the values observed on hardware
	awk '$0=="+++ b/bsd/dev/sweet_wlan_chip.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0124-sweet-wlan-bwfm-core.patch > $O/sweet_wlan_chip.h
	chk "wlan_xnu_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_xnu_host_test wlan_xnu_host_test.c
	chk "wlan_xnu_host_test" $O/wlan_xnu_host_test
	# stage-3 chip WRITER of xnu patches 0129 + 0130 + 0131 (new-file hunk of bsd/dev/sweet_wlan_wr.h, includes the chip header above): replayed with fault injection (mutants: tools/test_wlan_wr_mutants.py)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_wr.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0129-sweet-wlan-chip-writes.patch > $O/sweet_wlan_wr.h
	# patch 0130 (run B) changes that header: apply its hunk for the file on top of the 0129 text, so the test sees the bytes the kernel gets
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0130-sweet-wlan-image-copy.patch > $O/sweet_wlan_wr.0130.diff
	rm -rf $O/wrp; mkdir -p $O/wrp/bsd/dev; cp $O/sweet_wlan_wr.h $O/wrp/bsd/dev/
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0130.diff || { echo "FAIL patch 0130 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0131 (run C) changes it again: the same way, on top of the text after 0130
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0131-sweet-wlan-nvram-rtlv.patch > $O/sweet_wlan_wr.0131.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0131.diff || { echo "FAIL patch 0131 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	chk "wlan_wr_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_wr_host_test wlan_wr_host_test.c
	chk "wlan_wr_host_test" $O/wlan_wr_host_test
	# the pure NVRAM part of the same header (process_nvram_vars port): known answers, no bus
	chk "wlan_nv_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_nv_host_test wlan_nv_host_test.c
	chk "wlan_nv_host_test" $O/wlan_nv_host_test
	# firmware container parser of xnu patch 0128 (new-file hunk of bsd/dev/sweet_wlan_fw.h)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_fw.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0128-sweet-wlan-fwcheck.patch > $O/sweet_wlan_fw.h
	chk "wlan_fw_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_fw_host_test wlan_fw_host_test.c
	chk "wlan_fw_host_test" $O/wlan_fw_host_test
	awk '$0=="+++ b/bsd/dev/sweet_mali_pt.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0104-sweet-mali-driver-core.patch > $O/sweet_mali_pt.h
	chk "mali_pt_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/mali_pt_host_test mali_pt_host_test.c
	chk "mali_pt_host_test" $O/mali_pt_host_test
	# runtime MMIO poke parser of the same driver (xnu patch 0093, new-file hunk)
	awk '$0=="+++ b/bsd/dev/sweet_touch_poke.h"{on=1;next} on&&/^diff --git/{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0093-sweet-touch-poke.patch > $O/sweet_touch_poke.h
	chk "touchpoke_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/touchpoke_host_test touchpoke_host_test.c
	chk "touchpoke_host_test" $O/touchpoke_host_test
	# the inputs may have been edited while the tests ran: stamp only if the digest is unchanged (otherwise the next build runs them again)
	if [ "$(htdigest)" = "$ht" ]; then mkdir -p "$htdir" && : >"$htdir/$ht.ok.$$" && mv -f "$htdir/$ht.ok.$$" "$htdir/$ht.ok"; else echo "host tests: inputs changed while they ran, no stamp"; fi
fi
# and the SHIPPED objects (built with the image's flags above): a read-only dvfs image must not carry the request path at all
if [ "$touch_dvfs" = 1 ] && [ $((touch_bits & 1024)) = 0 ] && [ "$gpu_clk" = 0 ]; then
	chk "shipped acpm.o/touchprobe.o of a dvfsr image have no acpm_dvfs_set" sh -c '! nm $O/acpm.o $O/touchprobe.o | grep -q acpm_dvfs_set'
fi
if [ "$gpu_job" = 0 ]; then
	chk "the image has no gpu_job_run / gpujob code (no --gpu-job)" sh -c '! nm $O/shim.elf | grep -q "gpu_job_run\|gpu_job_scratch"'
else
	chk "the --gpu-job image has gpu_job_run" sh -c 'nm $O/shim.elf | grep -q " T gpu_job_run"'
fi
if [ "$gpu_rail_on" = 0 ]; then
	chk "shipped acpm.o has no acpm_s11_rail_set (no --gpu-rail-on): the S2MPG11 write is not in the image" sh -c '! nm $O/acpm.o | grep -q acpm_s11_rail_set'
else
	chk "shipped acpm.o of a --gpu-rail-on image has acpm_s11_rail_set" sh -c 'nm $O/acpm.o | grep -q acpm_s11_rail_set'
fi
if [ "$gpu_clk" = 0 ]; then
	chk "shipped acpm.o has no acpm_dvfs_reassert (no --gpu-clk)" sh -c '! nm $O/acpm.o | grep -q acpm_dvfs_reassert'
else
	chk "shipped acpm.o of a --gpu-clk image has acpm_dvfs_reassert" sh -c 'nm $O/acpm.o | grep -q acpm_dvfs_reassert'
	chk "shipped gpuclk.o has no MMIO accessor" sh -c '! nm $O/gpuclk.o | grep -q "try_write32\|try_read32"'
fi
if [ "$gs101" = 1 ]; then
	"$PYTHON" ../tools/test-bluejay-boot.py --out "$O" "$O/shim-boot.img" "$O/shim-boot-lz4.img"
fi
# provenance sidecar for the image fastboot will boot (tools/fboot.sh refuses images without a matching one)
if [ -f $O/shim-boot-avb.img ]; then
	if [ "$boot_xnu" = 1 ]; then kind=xnu; elif [ "$udump_mib" != 0 ]; then kind=udump; else kind=probe; fi
	"$PYTHON" ../tools/imgmeta.py write $O/shim-boot-avb.img --kind "$kind" --args "${META_ARGS:-}"
fi
ls -l $O/Image $O/shim-boot*.img
