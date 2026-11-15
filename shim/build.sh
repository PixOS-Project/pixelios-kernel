#!/bin/bash
# shellcheck disable=SC2089,SC2090  # CFLAGS is deliberately one word-split string (its -DPSTORE_BUILD_ID value carries literal quotes); an array would touch every compile line
set -euo pipefail
cd "$(dirname "$0")"

board=sweet
ht_filter=
in_ht=0
payload=
avb=0
pstore_fast=0
pr3_marker=0
usb_gadget=0
usb2=0
boot_xnu=0
udump_mib=0
qrlog=0
wlan_probe=0
wlan_rails=0
pmic_src=0
pmic_src_clear=0
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
gpu_clk_level=
mif_clk=0
mif_clk_level=
sweet_probe=0
gpu_rails=0
gpu_rail_on=0
gpu_handover=0
gpu_s2mpu=0
gpu_job=0
touch_probe=0
el2_trap=0
touch_opts=
touch_rails=0
touch_dvfs=0
dvfs_khz=
O=out	# output dir (--out DIR, relative to shim/): the gate gives every build its own, so builds can run in parallel
while [ "$#" -gt 0 ]; do
	case "$1" in
		--board) board="$2"; shift 2 ;;
		--out) O="$2"; shift 2 ;;
		--host-tests) ht_filter="$2"; shift 2 ;;     # sprint 2026-11-08: run only the host tests whose chk name matches the ERE (e.g. 'wlan'), then exit; no stamp
		--avb) avb=1; shift ;;
		--pstore-fast) pstore_fast=1; shift ;;
		--pr3-marker) pr3_marker=1; pstore_fast=1; shift ;;	# PR-3 DRAM-survival marker probe (pr3marker.c); implies --pstore-fast; run with REBOOT_BL=1
		--usb-gadget) usb_gadget=1; shift ;;
		--usb2) usb2=1; shift ;;	# opt out of the SuperSpeed gadget default (port of xnu patch 0173): HS-only gadget, byte for byte
		--boot-xnu) boot_xnu=1; shift ;;
		--udump) udump_mib="$2"; shift 2 ;;
		--qrlog) qrlog=1; shift ;;
		--wlan-probe) wlan_probe=1; shift ;;
		--pmic-src-clear) pmic_src=1; pmic_src_clear=1; shift ;;	# implies --pmic-src; ADDS the one PMIC write of docs/research/142: write 0 to S2MPG10 OFFSRC/PWRONSRC + S2MPG11 OFFSRC after the reads, read back once (pmicsrc_clear.c, acpm_pmic_src_clear); without it the image has no such write path
		--pmic-src) pmic_src=1; shift ;;		# READ-ONLY reset-source latches of S2MPG10/S2MPG11 (ACPM FUNC_READ allowlist of 6 registers), printed once at boot (pmicsrc.c, docs/research/139)
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
		--gpu-clk-level) gpu_clk_level="${2:?--gpu-clk-level needs a DT pair index}"; shift 2 ;;	# needs --gpu-clk; ask the DT pair (G3DL2/G3D) of that index instead of the init rate (boards.py gpu_clk_level)
		--mif-clk-level) mif_clk=1; mif_clk_level="${2:?--mif-clk-level needs a MIF table row in kHz}"; shift 2 ;;	# implies --mif-clk; the ONE MIF FREQ_REQ goes to this table row instead of the top row (boards.py mif_clk_level refuses every other value; ALL-ON T-02)
		--sweet-probe) sweet_probe=1; shift ;;	# needs --boot-xnu, gs101; READ-ONLY (xnu patch 0363, docs/research/161): PSCI_VERSION / PSCI_FEATURES x3 + the SLC channel 10/11 descriptors + ONE fixed PT_VERSION request, before the jump to XNU (sweetprobe.c, acpm_slc_version); tokens sweetprobe_* in the XNU command line
		--mif-clk) mif_clk=1; shift ;;	# needs --boot-xnu; WRITER: ACPM FREQ_GET MIF/INT + one FREQ_REQ MIF -> table top row, before the jump to XNU (mifclk.c, T-5030)
		--gpu-clk) gpu_clk=1; gpu_probe=1; shift ;;	# implies --gpu-probe; ACPM DVFS request for G3DL2/G3D (gpuclk.c)
		--gpu-power=stack) gpu_power=1; gpu_probe=1; gpu_stack=1; shift ;;	# + STACK_PWRON/PWROFF (kbase corestack path)
		--gpu-power=*) echo "unknown --gpu-power option '${1#--gpu-power=}' (only: stack)" >&2; exit 2 ;;
		--gpu-power) gpu_power=1; gpu_probe=1; shift ;;	# implies --gpu-probe (the gate lives there)
		--dvfs) dvfs_khz="$2"; shift 2 ;;
		--el2-trap) el2_trap=1; shift ;;	# EL2 stage-2 trap probe (el2.S, el2*.c, SPEC-EL2-PROXY-PROBE B1): stage 2 on for EL1, one GICD page + one .bss page trapped; shim-only, hardware-writing (EL2 system registers)
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
# tools/third_party is tracked (vendored, T-93e6; refresh with tools/fetch-android-tools.sh); the main-clone fallback below is only for a pre-T-93e6 checkout.
tp=../tools/third_party
[ -e "$tp/mkbootimg.py" ] || tp=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/tools/third_party
MKBOOTIMG=${MKBOOTIMG:-$tp/mkbootimg.py}
AVBTOOL=${AVBTOOL:-$tp/avbtool.py}
# Marker line identity: git short hash of this worktree (date fallback
# when detached from git), so the pulled pstore log names its build.
build_id=$(git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d)
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -march=armv8-a+crc -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -Werror -I$O -include $O/board_config.h -DPSTORE_BUILD_ID=\"$build_id\""
[ -z "$payload" ] || [ ! -f "$payload" ] || CFLAGS="$CFLAGS -DSHIM_PAYLOAD_SIZE=$(stat -c %s "$payload")ULL"	# boot speed: loader.c checks the ramdisk tail first (payload.c shim_find_payload_tail)
[ "$usb2" = 0 ] || [ "$usb_gadget" = 1 ] || { echo "--usb2 needs --usb-gadget (or --udump)" >&2; exit 2; }
[ "$pr3_marker" != 1 ] || [ "$boot_xnu" != 1 ] || { echo "--pr3-marker is a shim-only probe (site A is XNU RAM)" >&2; exit 2; }
[ "$pr3_marker" != 1 ] || [ "$gs101" = 1 ] || { echo "--pr3-marker needs a gs101 board (try_write32)" >&2; exit 2; }
if [ "$usb_gadget" = 1 ]; then
	# Stage-2 CDC-ACM log gadget (default OFF): bluejay only (needs try_write32).
	[ "$gs101" = 1 ] || { echo "--usb-gadget needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DUSB_GADGET"
	if [ "$usb2" = 1 ]; then
		CFLAGS="$CFLAGS -DUSB_GADGET_HS_ONLY"
		echo "USB2 GAP: --usb2: shim gadget forced to high speed (opt-out of the SuperSpeed default)" >&2
	fi
fi
if [ "$wlan_probe" = 1 ]; then
	# Read-only BCM4389 Wi-Fi path probe (default OFF); bluejay only.
	[ "$gs101" = 1 ] || { echo "--wlan-probe needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DWLAN_PROBE"
fi
if [ "$pmic_src" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--pmic-src needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DPMIC_SRC"
	[ "$pmic_src_clear" != 1 ] || CFLAGS="$CFLAGS -DPMIC_SRC_CLEAR"
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
if [ "$mif_clk" = 1 ]; then
	# T-5030: the only memory-path write: ACPM FREQ_REQ dom 0 (MIF) to the soc.json table top row (mifclk.c via acpm_dvfs_mif_top); the result goes to XNU as sweetmif* boot-arg tokens (xnu patch 0214).
	[ "$gs101" = 1 ] || { echo "--mif-clk needs a gs101 board" >&2; exit 2; }
	[ "$boot_xnu" = 1 ] || { echo "--mif-clk needs --boot-xnu (the tokens are appended to the XNU command line in the loader)" >&2; exit 2; }
	CFLAGS="$CFLAGS -DMIF_CLK"
	if [ -n "$mif_clk_level" ]; then
		# ALL-ON T-02: the request goes to ONE gs101.dtsi mif_int_map row. The value that reaches the compiler is boards.py's OUTPUT (a validated decimal), never the raw text.
		mif_lvl=$("$PYTHON" ../tools/boards.py "$board" --mif-clk-level-check "$mif_clk_level") || exit 2
		case $mif_lvl in ''|*[!0-9]*) echo "error: boards.py gave a non-numeric MIF level '$mif_lvl'" >&2; exit 2 ;; esac
		CFLAGS="$CFLAGS -DMIF_CLK_LEVEL_KHZ=$mif_lvl"
		echo "NOTE --mif-clk-level: the MIF request of this image is $mif_lvl kHz (not the top row)"
	fi
fi
if [ "$sweet_probe" = 1 ]; then
	# xnu patch 0363 (LOWCLK B1/B2, docs/research/161): read-only probes; the only request is the one fixed SLC PT_VERSION of acpm.c acpm_slc_version; the results go to XNU as sweetprobe_* tokens.
	[ "$gs101" = 1 ] || { echo "--sweet-probe needs a gs101 board" >&2; exit 2; }
	[ "$boot_xnu" = 1 ] || { echo "--sweet-probe needs --boot-xnu (the tokens are appended to the XNU command line in the loader)" >&2; exit 2; }
	CFLAGS="$CFLAGS -DSWEET_PROBE"
fi
if [ -n "$gpu_clk_level" ]; then
	# T-a214: one reviewed DT pair (tools/boards.py gpu_clk_level refuses everything but pair indexes 0..3 at or below the reviewed ceiling, with the reason).
	[ "$gpu_clk" = 1 ] || { echo "--gpu-clk-level needs --gpu-clk" >&2; exit 2; }
	[ "$gpu_handover" = 1 ] || { echo "--gpu-clk-level needs --gpu-handover (the pair is asked once, in the handover block, after the rail-on step)" >&2; exit 2; }
	[ "$gs101" = 1 ] || { echo "--gpu-clk-level needs a gs101 board" >&2; exit 2; }
	[ "$gpu_power" = 0 ] && [ "$gpu_job" = 0 ] || { echo "--gpu-clk-level takes no --gpu-power/--gpu-job (no power cycle at the raised clocks was reviewed, T-50eb review F4)" >&2; exit 2; }
	"$PYTHON" ../tools/boards.py "$board" --gpu-clk-level-check "$gpu_clk_level" >/dev/null || exit 2
	CFLAGS="$CFLAGS -DGPU_CLK_LEVEL=$((10#$gpu_clk_level))"
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
if [ "$el2_trap" = 1 ]; then
	# SPEC-EL2-PROXY-PROBE B1: bluejay only. --el2-trap --boot-xnu is the B2 mode (SPEC 3.4 (b) design item 1, decision D1 = yes + W24 option (b)): -DEL2_RESIDENT keeps stage 2 and the EL2 vectors live across loader_boot
	# (identity map, no trap page, bootFlags bit 62 handshake, hvc #0x5345 report). Every other --el2-trap combination keeps today's rules.
	[ "$gs101" = 1 ] || { echo "--el2-trap needs a gs101 board" >&2; exit 2; }
	CFLAGS="$CFLAGS -DEL2_TRAP"
	if [ "$boot_xnu" = 1 ]; then
		# review el2-b2-report-r1 F6: "exactly that pair" (spec 3.4 (b) design item 1): no other writer or probe rides along in the B2 image
		[ "$gpu_probe" = 0 ] && [ "$wlan_probe" = 0 ] && [ "$touch_probe" = 0 ] && [ "$udump_mib" = 0 ] && [ "$qrlog" = 0 ] || { echo "--el2-trap --boot-xnu (B2) takes no --gpu-*/--wlan-*/--touch-probe/--udump/--qrlog flags (reviewed as exactly that pair)" >&2; exit 2; }
		CFLAGS="$CFLAGS -DEL2_RESIDENT"
	fi
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
if [ "$pr3_marker" = 1 ]; then
	CFLAGS="$CFLAGS -DPR3_MARKER"
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
for f in start.S shim.c shim_assert.c console.c ramlog.c mmu.c fdt.c pstore.c loader.c payload.c ufs.c usbprobe.c; do
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
"$CC" $CFLAGS -c pr3marker.c -o $O/pr3marker.o
"$CC" $CFLAGS -c pr3run.c -o $O/pr3run.o
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
# Same for pmicsrc.c (read-only PMIC reset-source latches, docs/research/139); only LINKED with --pmic-src. No MMIO of its own, reads only through acpm_pmic_src_read (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c pmicsrc.c -o $O/pmicsrc.o
# pmicsrc_clear.c = the ONE PMIC write of --pmic-src-clear (docs/research/142): compiled and linked ONLY with that flag (CFLAGS then carries -DPMIC_SRC_CLEAR); no stale object from an earlier flagged build may stay.
rm -f $O/pmicsrc_clear.o
if [ "$pmic_src_clear" = 1 ]; then
	"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c pmicsrc_clear.c -o $O/pmicsrc_clear.o
fi
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
# and the --gpu-clk-level variant (DT pair 3), built and audited on every build like gpupower_stack.o, linked only through the image's own gpuclk.o
"$CC" $CFLAGS -UGPU_CLK_LEVEL -DGPU_CLK_LEVEL=3 -fno-tree-switch-conversion -fno-jump-tables -c gpuclk.c -o $O/gpuclk_level.o
# Same for mifclk.c (the opt-in MIF/INT read + one MIF request); only LINKED with --mif-clk. It has no MMIO writer of its own (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c mifclk.c -o $O/mifclk.o
# and a -DMIF_CLK copy built and audited on EVERY build (like gpuclk_level.o), so the default gate audits the real code, not an empty object
"$CC" $CFLAGS -UMIF_CLK -DMIF_CLK -fno-tree-switch-conversion -fno-jump-tables -c mifclk.c -o $O/mifclk_audit.o
# Same for sweetprobe.c (--sweet-probe: PSCI queries + SLC descriptors + one PT_VERSION); only LINKED with --sweet-probe. It has no MMIO writer of its own (audited: tools/sweetprobe_audit.py).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c sweetprobe.c -o $O/sweetprobe.o
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c sweetprobe_smc.c -o $O/sweetprobe_smc.o
# and a -DSWEET_PROBE copy built and audited on EVERY build (like mifclk_audit.o), so the default gate audits the real code
"$CC" $CFLAGS -USWEET_PROBE -DSWEET_PROBE -fno-tree-switch-conversion -fno-jump-tables -c sweetprobe.c -o $O/sweetprobe_audit.o
"$CC" $CFLAGS -USWEET_PROBE -DSWEET_PROBE -fno-tree-switch-conversion -fno-jump-tables -c sweetprobe_smc.c -o $O/sweetprobe_smc_audit.o
# Same for gpus2mpu.c (read-only S2MPU dump); only LINKED with --gpu-s2mpu (or --gpu-job). Reads only, audited like gpuprobe.o.
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpus2mpu.c -o $O/gpus2mpu.o
# Same for wlanh1.c (read-only HSI2 S2MPU + SSMT + RC DBI Command probe, docs/research/90); only LINKED with --wlan-hold. Reads only, audited like gpus2mpu.o.
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c wlanh1.c -o $O/wlanh1.o
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
# --el2-trap (SPEC-EL2-PROXY-PROBE B1): the EL2 objects are built and linked only with the flag; start_nodef.o is start.S without the define, for the audit's site diff (tools/el2_audit.py)
el2_objs=
if [ "$el2_trap" = 1 ]; then
	"$CC" $CFLAGS -c el2.S -o $O/el2_asm.o
	for f in el2 el2h el2cfg; do
		"$CC" $CFLAGS -c "$f.c" -o "$O/$f.o"
	done
	"$CC" ${CFLAGS//-DEL2_TRAP/} -c start.S -o $O/start_nodef.o
	el2_objs="$O/el2_asm.o $O/el2.o $O/el2h.o $O/el2cfg.o"
fi
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
if [ "$pmic_src" = 1 ]; then
	usb_objs="$usb_objs $O/pmicsrc.o"
	[ "$pmic_src_clear" != 1 ] || usb_objs="$usb_objs $O/pmicsrc_clear.o"
fi
if [ "$wlan_link" = 1 ]; then
	usb_objs="$usb_objs $O/wlanlink.o $O/wlansmc_link.o"
	[ "$wlan_hold" = 1 ] && usb_objs="$usb_objs $O/wlanh1.o"
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
if [ "$mif_clk" = 1 ]; then
	usb_objs="$usb_objs $O/mifclk.o"
fi
if [ "$sweet_probe" = 1 ]; then
	usb_objs="$usb_objs $O/sweetprobe.o $O/sweetprobe_smc.o"
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
if [ "$pr3_marker" = 1 ]; then
	usb_objs="$usb_objs $O/pr3marker.o $O/pr3run.o"
fi
if [ "$touch_probe" != 0 ]; then
	usb_objs="$usb_objs $O/touchprobe.o"
fi
if [ "$touch_rails" = 1 ] || [ "$touch_dvfs" = 1 ] || [ "$gpu_clk" = 1 ] || [ "$mif_clk" = 1 ] || [ "$sweet_probe" = 1 ] || [ "$gpu_rails" = 1 ] || [ "$wlan_rails" = 1 ] || [ "$pmic_src" = 1 ]; then
	usb_objs="$usb_objs $O/acpm.o"
fi
clr_obj=""; [ "$pmic_src_clear" != 1 ] || clr_obj="$O/pmicsrc_clear.o"   # SC2046: no unquoted $(...) in the readelf list
abs=$("$READELF" -r $O/shim.o $O/shim_assert.o $O/console.o $O/ramlog.o $O/mmu.o $O/fdt.o $O/pstore.o $O/pr3marker.o $O/pr3run.o $O/loader.o $O/payload.o $O/ufs.o $O/ufsdiag.o $O/ufsinit.o $O/usbprobe.o $O/usbproto.o $O/usbgadget.o $O/usbpool.o $O/udump.o $O/wlanprobe.o $O/wlanrails.o $O/pmicsrc.o ${clr_obj:+"$clr_obj"} $O/wlanen.o $O/wlansmc.o $O/wlansmc_iso.o $O/wlansmc_link.o $O/wlanlink.o $O/gpuprobe.o $O/gpupower.o $O/gpupower_stack.o $O/gpupower_job.o $O/gpujob.o $O/gpuclk.o $O/mifclk.o $O/mifclk_audit.o $O/sweetprobe.o $O/sweetprobe_smc.o $O/sweetprobe_audit.o $O/sweetprobe_smc_audit.o $O/gpus2mpu.o $O/wlanh1.o $O/gpurails.o $O/gpurailon.o $O/acpm_railon.o $O/touchprobe.o $O/acpm.o $qr_objs $el2_objs | grep -c ABS64 || { rc=$?; [ $rc = 1 ] || { echo "error: build GAP: grep -c ABS64 failed rc=$rc" >&2; exit 1; }; })
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
"$CC" $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	$O/start.o $O/shim.o $O/shim_assert.o $O/console.o $O/ramlog.o $O/mmu.o $O/fdt.o $O/pstore.o $O/loader.o $O/payload.o $O/ufs.o $O/ufsdiag.o $O/ufsinit.o $O/usbprobe.o $O/usbpool.o $usb_objs $qr_objs $el2_objs -o $O/shim.elf
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
		key=out/bluejay-testkey.pem	# shared by every --out dir (read-only for them); created atomically (genrsa to a temp + mv -n: the loser discards its own), so parallel builds need no ordering
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
chk() { local what="$1" prev="" a; shift; if [ "$in_ht" = 1 ] && [ -n "$ht_filter" ] && ! [[ $what =~ $ht_filter ]]; then echo "SKIP $what (--host-tests $ht_filter)"; return 0; fi; for a; do [ "$prev" = -o ] && case $a in *host_test*) echo "${a##*/}" >>"$O/host-tests.list" ;; esac; prev=$a; done	# a failed compile or test must stop the build; every compiled *host_test* output is listed for the gate (check-all.sh host-tests fails if one is missing)
	if "$@"; then echo "PASS $what"; else echo "FAIL $what" >&2; exit 1; fi; }
htdir=${XDG_CACHE_HOME:-$HOME/.cache}/pixelios/host-tests
# shellcheck disable=SC2046  # intentional word splitting of the file lists (an empty "" argument would break sha256sum)
htdigest() { { sha256sum *.c *.h *.in $O/board_config.h $([ -f $O/wlan_seq.h ] && echo $O/wlan_seq.h) $(grep -o '\.\./xnu/patches/[0-9][0-9]*-[A-Za-z0-9._-]*\.patch' build.sh | sort -u) build.sh; gcc -v 2>&1; } | sha256sum | cut -c1-32; }
ht=$(htdigest)
in_ht=1
if [ -e "$htdir/$ht.ok" ] && [ "${PIXELIOS_HOST_TESTS:-}" != always ] && [ -z "$ht_filter" ]; then
	echo "host tests: cached $ht"
else
	: >"$O/host-tests.list"
	# sprint 2026-11-08: a patch whose last byte is not a newline is skipped by tools/xnu-local-build ("skipped (CI skips them too)") while the awk extractions here still pass: refuse it here
	chk "wlan: xnu patches 0174/0206 new-file hunks == shim/ (tools/gen-wlan-hunks --check)" ../tools/gen-wlan-hunks --check ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch ../xnu/patches/0206-sweet-wlan-l25-headers.patch
	chk "wlan: every xnu/patches/*.patch ends with a newline" bash -c 'for p in ../xnu/patches/[0-9]*.patch; do [ "$(tail -c1 "$p" | od -An -c | tr -d " ")" = "\n" ] || { echo "no final newline: $p"; exit 1; }; done'
	chk "host_test (compile)" gcc -O1 -Wall -o $O/host_test console.c ramlog.c host_test.c
	chk "host_test" $O/host_test $O/host.ppm
	chk "ramlog_host_test (compile)" gcc -O1 -Wall -Wextra -o $O/ramlog_host_test ramlog.c ramlog_host_test.c
	chk "ramlog_host_test" $O/ramlog_host_test
	# ASan/UBSan catch a 1-byte overflow of the report buffer (review T-98a7-assert F3); if the host gcc has no sanitizer runtime the test still runs, loudly without them
	san="-fsanitize=address,undefined"
	if ! echo 'int main(void) { return 0; }' | gcc $san -x c - -o /dev/null 2>/dev/null; then san=""; echo "NOTE shim_assert_host_test: no ASan/UBSan runtime on this host, built without (an overflow of the report buffer would not be caught)"; fi
	chk "shim_assert_host_test (compile)" gcc -O1 -Wall -Wextra -Werror $san -I. -o $O/shim_assert_host_test shim_assert_host_test.c
	chk "shim_assert_host_test" $O/shim_assert_host_test
	chk "fdt_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/fdt_host_test fdt.c fdt_host_test.c
	chk "fdt_host_test" $O/fdt_host_test
	chk "pstore_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/pstore_host_test fdt.c pstore.c pstore_host_test.c
	chk "pstore_host_test" $O/pstore_host_test
	chk "pr3marker_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/pr3marker_host_test pr3marker.c pr3marker_host_test.c
	chk "pr3marker_host_test" $O/pr3marker_host_test
	chk "usbprobe_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbprobe_host_test fdt.c usbprobe.c usbprobe_host_test.c
	chk "usbprobe_host_test" $O/usbprobe_host_test
	chk "usbproto_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbproto_host_test usbproto.c usbproto_host_test.c
	chk "usbproto_host_test" $O/usbproto_host_test
	chk "usbgadget_host_test (compile)" gcc -O1 -Wall -Wextra -I. -o $O/usbgadget_host_test usbproto.c usbgadget.c usbgadget_host_test.c
	chk "usbgadget_host_test" $O/usbgadget_host_test
	chk "usbproto_host_test HS-only (compile)" gcc -O1 -Wall -Wextra -DUSB_GADGET_HS_ONLY -I. -o $O/usbproto_host_test_hs usbproto.c usbproto_host_test.c
	chk "usbproto_host_test HS-only" $O/usbproto_host_test_hs
	chk "usbgadget_host_test HS-only (compile)" gcc -O1 -Wall -Wextra -DUSB_GADGET_HS_ONLY -I. -o $O/usbgadget_host_test_hs usbproto.c usbgadget.c usbgadget_host_test.c
	chk "usbgadget_host_test HS-only" $O/usbgadget_host_test_hs
	chk "usbpool_host_test (compile)" gcc -O1 -Wall -Wextra -I. -DUSB_DMA_HI_BASE=1 -o $O/usbpool_host_test usbpool.c usbpool_host_test.c
	chk "usbpool_host_test" $O/usbpool_host_test
	# --el2-trap (el2.c, EL2_TRAP only): pure decode/dispatch/accounting, then the real printer against the run-plan regexes (spec B3 / B3p). The default image never links el2.c.
	chk "el2_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -o $O/el2_host_test el2.c el2_host_test.c
	chk "el2_host_test" $O/el2_host_test
	chk "el2_host_test_res (compile)" gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -DEL2_RESIDENT -I. -o $O/el2_host_test_res el2.c el2_host_test.c	# B2 variant: any stage-2 abort is a GAP (review el2-b2-report-r1 F2)
	chk "el2_host_test_res" $O/el2_host_test_res
	chk "el2_print_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -I$O -include $O/board_config.h -o $O/el2_print_host_test el2.c el2_print_host_test.c
	chk "el2_print_host_test" $O/el2_print_host_test
	chk "el2_handler_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -I. -I$O -include $O/board_config.h -o $O/el2_handler_host_test el2.c el2h.c el2_handler_host_test.c
	chk "el2_handler_host_test" $O/el2_handler_host_test
	chk "el2_handler_host_test_res (compile)" gcc -O1 -Wall -Wextra -Werror -DEL2_TRAP -DEL2_RESIDENT -I. -I$O -include $O/board_config.h -o $O/el2_handler_host_test_res el2.c el2h.c el2_handler_host_test.c	# B2 variant (SPEC 3.4 (b) item 1)
	chk "el2_handler_host_test_res" $O/el2_handler_host_test_res
	chk "wlanprobe_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/wlanprobe_host_test wlanprobe_host_test.c
	chk "wlanprobe_host_test" $O/wlanprobe_host_test
	# --wlan-rails (wlanrails.c) against the fake APM of acpm_host_test.c; --wlan-en (wlanen.c) against a fake GPIO bank. try_write32 is deliberately absent from the rails test (linking fails if wlanrails.c ever writes).
	chk "wlanrails_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -I$O -include $O/board_config.h -o $O/wlanrails_host_test wlanrails_host_test.c
	chk "wlanrails_host_test" $O/wlanrails_host_test
	chk "pmicsrc_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -I$O -include $O/board_config.h -o $O/pmicsrc_host_test pmicsrc_host_test.c
	chk "pmicsrc_host_test" $O/pmicsrc_host_test
	chk "pmicsrc_clear_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -DPMIC_SRC_CLEAR -I. -I$O -include $O/board_config.h -o $O/pmicsrc_clear_host_test pmicsrc_clear_host_test.c
	chk "pmicsrc_clear_host_test" $O/pmicsrc_clear_host_test
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
	chk "wlanh1_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -include $O/board_config.h -o $O/wlanh1_host_test wlanh1_host_test.c
	chk "wlanh1_host_test" $O/wlanh1_host_test
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
	chk "gpuclk_level_host_test (compile)" $GCLK -DGPU_CLK_LEVEL=3 -I$O -include $O/board_config.h -o $O/gpuclk_level_host_test gpuclk_host_test.c
	chk "gpuclk_level_host_test" $O/gpuclk_level_host_test
	chk "gpuclk_level1_host_test (compile)" $GCLK -DGPU_CLK_LEVEL=1 -I$O -include $O/board_config.h -o $O/gpuclk_level1_host_test gpuclk_host_test.c	# sprint-gpu: the booted level 1 (stock max)
	chk "gpuclk_level1_host_test" $O/gpuclk_level1_host_test
	chk "gpuclk_gate_l_host_test (compile)" $GCLK -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/gpuclk_gate_l_host_test gpuclk_host_test.c
	chk "gpuclk_gate_l_host_test" $O/gpuclk_gate_l_host_test
	# --mif-clk (mifclk.c, T-5030) against the same fake APM: happy path, refusals, the sweetmif* tokens, the layout gate variant
	MCLK="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DMIF_CLK -I."
	chk "mifclk_host_test (compile)" $MCLK -I$O -include $O/board_config.h -o $O/mifclk_host_test mifclk_host_test.c
	chk "mifclk_host_test" $O/mifclk_host_test
	chk "mifclk_gate_l_host_test (compile)" $MCLK -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/mifclk_gate_l_host_test mifclk_host_test.c
	chk "mifclk_gate_l_host_test" $O/mifclk_gate_l_host_test
	# --sweet-probe (sweetprobe.c, xnu patch 0363): the fake SLC APM (channels 10/11, 16 and 32 B elements) + fake PSCI conduit; the gate variant has ACPM_LAYOUT_VERIFIED forced to 0
	SPRB="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DSWEET_PROBE -I."
	chk "sweetprobe_host_test (compile)" $SPRB -I$O -include $O/board_config.h -o $O/sweetprobe_host_test sweetprobe_host_test.c
	chk "sweetprobe_host_test" $O/sweetprobe_host_test
	chk "sweetprobe_gate_l_host_test (compile)" $SPRB -DGATE_TEST -I$O -include $O/board_config_l0.h -o $O/sweetprobe_gate_l_host_test sweetprobe_host_test.c
	chk "sweetprobe_gate_l_host_test" $O/sweetprobe_gate_l_host_test
	# --mif-clk-level (ALL-ON T-02): EVERY allowed row (the 13 mif_int_map rows, boards.py) gets the level test (request value, tokens, refusals, allowlist sweep)
	for lv in $("$PYTHON" ../tools/boards.py "$board" --mif-clk-levels); do
		chk "mifclk_level_${lv}_host_test (compile)" $MCLK -DMIF_CLK_LEVEL_KHZ=$lv -I$O -include $O/board_config.h -o $O/mifclk_level_${lv}_host_test mifclk_host_test.c
		chk "mifclk_level_${lv}_host_test" $O/mifclk_level_${lv}_host_test
	done
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
	# a --mif-clk object has the MIF-top request and NEITHER the CPU nor the GPU one; no other object has it (T-5030)
	gcc -O1 -w -DMIF_CLK -I. -I$O -include $O/board_config.h -c -o $O/acpm_mifclk.o acpm.c
	chk "mif-clk acpm object has acpm_dvfs_mif_top and no acpm_dvfs_set / acpm_dvfs_reassert" sh -c 'test -s $O/acpm_mifclk.o && nm $O/acpm_mifclk.o | grep -q acpm_dvfs_mif_top && ! nm $O/acpm_mifclk.o | grep -q "acpm_dvfs_set\|acpm_dvfs_reassert"'
	# a --sweet-probe object has the SLC describe + the one PT_VERSION request and neither the CPU, GPU nor MIF request; no other object has them (xnu patch 0363)
	gcc -O1 -w -DSWEET_PROBE -I. -I$O -include $O/board_config.h -c -o $O/acpm_sweetprobe.o acpm.c
	chk "sweet-probe acpm object has acpm_slc_describe + acpm_slc_version and no DVFS request" sh -c 'nm $O/acpm_sweetprobe.o | grep -q acpm_slc_version && nm $O/acpm_sweetprobe.o | grep -q acpm_slc_describe && ! nm $O/acpm_sweetprobe.o | grep -q "acpm_dvfs_set\|acpm_dvfs_reassert\|acpm_dvfs_mif_top"'
	chk "acpm objects built without --sweet-probe have no acpm_slc_*" sh -c '! nm $O/acpm_ro.o $O/acpm_dvfs_ro.o $O/acpm_dvfs_rw.o $O/acpm_gpuclk.o $O/acpm_mifclk.o | grep -q acpm_slc_'
	# --mif-clk-level: the request site is the same single function; a rate that is not a table row does not compile
	gcc -O1 -w -DMIF_CLK -DMIF_CLK_LEVEL_KHZ=1716000 -I. -I$O -include $O/board_config.h -c -o $O/acpm_mifclk_level.o acpm.c
	chk "mif-clk-level acpm object has acpm_dvfs_mif_top and no acpm_dvfs_set / acpm_dvfs_reassert" sh -c 'nm $O/acpm_mifclk_level.o | grep -q acpm_dvfs_mif_top && ! nm $O/acpm_mifclk_level.o | grep -q "acpm_dvfs_set\|acpm_dvfs_reassert"'
	chk "a MIF_CLK_LEVEL_KHZ that is not a MIF table row does not compile" sh -c '! gcc -O1 -w -DMIF_CLK -DMIF_CLK_LEVEL_KHZ=1716001 -I. -I$O -include $O/board_config.h -c -o /dev/null acpm.c 2>/dev/null'
	chk "MIF_CLK_LEVEL_KHZ without MIF_CLK does not compile" sh -c '! gcc -O1 -w -DMIF_CLK_LEVEL_KHZ=1716000 -I. -I$O -include $O/board_config.h -c -o /dev/null acpm.c 2>/dev/null'
	chk "acpm objects built without --mif-clk have no acpm_dvfs_mif_top" sh -c 'test -s $O/acpm_ro.o && test -s $O/acpm_dvfs_ro.o && test -s $O/acpm_dvfs_rw.o && test -s $O/acpm_gpuclk.o && ! nm $O/acpm_ro.o $O/acpm_dvfs_ro.o $O/acpm_dvfs_rw.o $O/acpm_gpuclk.o | grep -q acpm_dvfs_mif_top'
	chk "udump_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -o $O/udump_host_test udump.c udump_host_test.c
	chk "udump_host_test" $O/udump_host_test
	# the debounce under test is the header shipped in xnu patch 0087 (new-file hunk: drop the leading "+")
	awk '/^\+\+\+ b\/bsd\/dev\/sweet_keys_gs101_debounce.h$/{f=1;next} f&&/^diff --git/{exit} f&&/^@@/{next} f{print substr($0,2)}' ../xnu/patches/0087-sweet-gs101-keys.patch > $O/sweet_keys_gs101_debounce.h
	chk "keys_gs101_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I"$O" -o $O/keys_gs101_host_test keys_gs101_host_test.c
	chk "keys_gs101_host_test" $O/keys_gs101_host_test
	# pure FTM5 protocol header of the Pixel 6a touch driver: extracted from xnu patch 0088 so the test sees the kernel's bytes
	awk '$0=="+++ b/bsd/dev/sweet_ftm5.h"{on=1;next} on&&/^--- /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0088-sweet-gs101-touch.patch > $O/sweet_ftm5.h
	# patch 0348 (nibble dispatch) changes it: apply its sweet_ftm5.h hunk the same way as the 0174 chain below
	awk '/^--- a\/bsd\/dev\/sweet_ftm5.h$/{on=1;print;next} on&&/^--- a\//{exit} on{print}' ../xnu/patches/0348-sweet-ftm5-nibble-dispatch.patch > $O/sweet_ftm5.0348.diff
	rm -rf $O/ftp; mkdir -p $O/ftp/bsd/dev; cp $O/sweet_ftm5.h $O/ftp/bsd/dev/
	patch -s -p1 -d $O/ftp < $O/sweet_ftm5.0348.diff || { echo "FAIL patch 0348 does not apply to sweet_ftm5.h" >&2; exit 1; }
	cp $O/ftp/bsd/dev/sweet_ftm5.h $O/sweet_ftm5.h
	chk "ftm5_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/ftm5_host_test ftm5_host_test.c
	chk "ftm5_host_test" $O/ftm5_host_test
	# stage-1 BCM4389 chip layer of xnu patch 0124 (new-file hunk of bsd/dev/sweet_wlan_chip.h): replayed against the values observed on hardware
	awk '$0=="+++ b/bsd/dev/sweet_wlan_chip.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0124-sweet-wlan-bwfm-core.patch > $O/sweet_wlan_chip.h
	# patch 0174 (run G2) changes it: swl_pre_ep() takes the expected EP Command (R107 #4); apply its hunk for the file the same way as the wr.h chain below
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_chip.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_chip.0174.diff
	rm -rf $O/chp; mkdir -p $O/chp/bsd/dev; cp $O/sweet_wlan_chip.h $O/chp/bsd/dev/
	patch -s -p1 -d $O/chp < $O/sweet_wlan_chip.0174.diff || { echo "FAIL patch 0174 does not apply to sweet_wlan_chip.h" >&2; exit 1; }
	cp $O/chp/bsd/dev/sweet_wlan_chip.h $O/sweet_wlan_chip.h
	chk "wlan_xnu_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_xnu_host_test wlan_xnu_host_test.c
	chk "wlan_xnu_host_test" $O/wlan_xnu_host_test
	# stage-3 chip WRITER of xnu patches 0129 + 0130 + 0131 + 0132 + 0133 + 0134 + 0135 + 0136 (new-file hunk of bsd/dev/sweet_wlan_wr.h, includes the chip header above): replayed with fault injection (mutants: tools/test_wlan_wr_mutants.py)
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
	# patch 0132 (run D) changes it once more: the same way, on top of the text after 0131
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0132-sweet-wlan-reset-vector.patch > $O/sweet_wlan_wr.0132.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0132.diff || { echo "FAIL patch 0132 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0133 (run E) changes it once more: the same way, on top of the text after 0132
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0133-sweet-wlan-arm-release.patch > $O/sweet_wlan_wr.0133.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0133.diff || { echo "FAIL patch 0133 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0134 (run F1) changes it once more: the same way, on top of the text after 0133
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0134-sweet-wlan-run-f1-dumps.patch > $O/sweet_wlan_wr.0134.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0134.diff || { echo "FAIL patch 0134 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0135 (run F2) changes it once more: the same way, on top of the text after 0134
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0135-sweet-wlan-run-f2-low-window.patch > $O/sweet_wlan_wr.0135.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0135.diff || { echo "FAIL patch 0135 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0136 (run F3) changes it a last time: the same way, on top of the text after 0135
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0136-sweet-wlan-run-f3-live-low-window.patch > $O/sweet_wlan_wr.0136.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0136.diff || { echo "FAIL patch 0136 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0137 (gap report) adds swl_gap() and a call next to every refusal: the same way, on top of the text after 0136
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0137-sweet-wlan-gap-report.patch > $O/sweet_wlan_wr.0137.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0137.diff || { echo "FAIL patch 0137 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	# patch 0174 (run G2) adds the W17 hook (struct swl_wx g2/g2rc) and the swl_pre_ep() want argument: the same way, on top of the text after 0137
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_wr.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_wr.0174.diff
	patch -s -p1 -d $O/wrp < $O/sweet_wlan_wr.0174.diff || { echo "FAIL patch 0174 does not apply to sweet_wlan_wr.h" >&2; exit 1; }
	cp $O/wrp/bsd/dev/sweet_wlan_wr.h $O/sweet_wlan_wr.h
	chk "wlan_wr_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_wr_host_test wlan_wr_host_test.c
	chk "wlan_wr_host_test" $O/wlan_wr_host_test
	# the pure NVRAM part of the same header (process_nvram_vars port): known answers, no bus
	chk "wlan_nv_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_nv_host_test wlan_nv_host_test.c
	chk "wlan_nv_host_test" $O/wlan_nv_host_test
	# msgbuf host DMA block of xnu patch 0167 (new-file hunk of bsd/dev/sweet_wlan_dma.h): known answers, no bus (mutants: tools/test_wlan_dma_mutants.py)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_dma.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0167-sweet-wlan-g1-host-dma.patch > $O/sweet_wlan_dma.h
	chk "wlan_dma_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_dma_host_test wlan_dma_host_test.c
	chk "wlan_dma_host_test" $O/wlan_dma_host_test
	# run G2 (xnu patch 0174, new-file hunk of bsd/dev/sweet_wlan_g2.h, includes the wr.h and DMA headers above): the shared-structure stores against a fake dongle (mutants: tools/test_wlan_wr_mutants.py --g2).
	# The test is copied next to the extracted headers: its quoted #include would otherwise pick shim/sweet_wlan_g2.h, not the patch's bytes.
	awk '$0=="+++ b/bsd/dev/sweet_wlan_g2.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_g2.h
	chk "sweet_wlan_g2.h in patch 0174 == shim/sweet_wlan_g2.h" cmp -s $O/sweet_wlan_g2.h sweet_wlan_g2.h
	# W-D4b: sweet_wlan_g2.h includes sweet_wlan_d4b.h (second new file of patch 0174, the last hunk of the patch)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_d4b.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_d4b.h
	# T-4adb (R29): sweet_wlan_g2.h includes sweet_wlan_g2e.h (third new file of patch 0174, the L21-L23 request sequences)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_g2e.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_g2e.h
	chk "sweet_wlan_g2e.h in patch 0174 == shim/sweet_wlan_g2e.h" cmp -s $O/sweet_wlan_g2e.h sweet_wlan_g2e.h
	# T-0a9d (R29): sweet_wlan_g2.h also includes sweet_wlan_g2d.h (fourth new file of patch 0174, the DP15 transport and the L16-L20 ring-2 helpers)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_g2d.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_g2d.h
	chk "sweet_wlan_g2d.h in patch 0174 == shim/sweet_wlan_g2d.h" cmp -s $O/sweet_wlan_g2d.h sweet_wlan_g2d.h
	# T-08fd (R29): sweet_wlan_g2.h includes sweet_wlan_g2r.h (fourth new file of patch 0174, the replay record of the scan results)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_g2r.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0174-sweet-wlan-g2-first-dma.patch > $O/sweet_wlan_g2r.h
	chk "sweet_wlan_g2r.h in patch 0174 == shim/sweet_wlan_g2r.h" cmp -s $O/sweet_wlan_g2r.h sweet_wlan_g2r.h
	cp wlan_g2_host_test.c wlan_g2_host_dongle.h wlan_g2_host_l24.h wlan_g2_host_l25wl.h sweet_wlan_l25_wl.h $O/
	cp sweet_wlan_join.h sweet_wlan_join_fw.h $O/      # T-6bef: the L24 cases include the reviewed join payloads
	chk "wlan_g2_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_g2_host_test $O/wlan_g2_host_test.c
	chk "wlan_g2_host_test" $O/wlan_g2_host_test
	# T-08 (patch 0329): the Sandbox op-2 self-check, code extracted from the patch itself (no copy to drift), KPIs stubbed
	sed -n '/^+#define SWEET_SBX_OP_CHECK/,/^[ +]sweet_sandbox_syscall/p' ../xnu/patches/0329-sweet-sandbox-selfcheck.patch | head -n -2 | sed 's/^+//' > $O/sbx_snippet.h
	chk "sweet_sbx_selfcheck_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -DSBX_SNIPPET='"sbx_snippet.h"' -I$O -o $O/sweet_sbx_selfcheck_host_test sweet_sbx_selfcheck_host_test.c
	chk "sweet_sbx_selfcheck_host_test" $O/sweet_sbx_selfcheck_host_test
	# P-W3a msgbuf table interpreter (xnu patch 0198, new-file hunk of bsd/dev/sweet_wlan_tbl.h) on the table generated from the proven run (tools/wlan-tbl-gen; mutants: tools/test_wlan_tbl_gen.py + the host test's own)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_tbl.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0198-sweet-wlan-msgbuf-table.patch > $O/sweet_wlan_tbl.h
	chk "sweet_wlan_tbl.h in patch 0198 == shim/sweet_wlan_tbl.h" cmp -s $O/sweet_wlan_tbl.h sweet_wlan_tbl.h
	chk "sweet_wlan_tbl_data.h == tools/wlan-tbl-gen of soc/google/gs101/wlan_msgbuf_l22.tsv" python3 ../tools/wlan-tbl-gen header-check --tsv ../soc/google/gs101/wlan_msgbuf_l22.tsv --hdr sweet_wlan_tbl_data.h
	cp sweet_wlan_tbl_data.h wlan_tbl_host_test.c $O/
	chk "wlan_tbl_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_tbl_host_test $O/wlan_tbl_host_test.c
	chk "wlan_tbl_host_test" $O/wlan_tbl_host_test ../tools/testdata/wlan-tbl/proven-g2-slice.log $O/wlan_tbl_emit.log
	chk "tools/replay wifi --baseline on the interpreter's store log" python3 ../tools/replay wifi --run $O/wlan_tbl_emit.log --baseline ../tools/testdata/wlan-tbl/proven-g2-slice.log
	# pure msgbuf ring library (docs/research/53 step 3): a fake dongle plays the firmware side of the ring protocol; no hardware code
	chk "wlan_msgbuf_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_msgbuf_host_test wlan_msgbuf_host_test.c
	chk "wlan_msgbuf_host_test" $O/wlan_msgbuf_host_test
	# DP15 executor swg_dp_exec (T-75c7, SPEC-WIFI TRANSPORT s.1): scripted bus + fault injection, plain and ASan/UBSan; the test is copied next to the extracted headers like the G2 one; mutants: tools/test_wlan_dp15_mutants.py
	cp wlan_dp15_host_test.c $O/wlan_dp15_host_test.c
	chk "wlan_dp15_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -I. -o $O/wlan_dp15_host_test $O/wlan_dp15_host_test.c
	chk "wlan_dp15_host_test" $O/wlan_dp15_host_test
	chk "wlan_dp15_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -I$O -I. -o $O/wlan_dp15_host_test_asan $O/wlan_dp15_host_test.c
	chk "wlan_dp15_host_test (asan)" $O/wlan_dp15_host_test_asan
	# L25/L26 (T-38cc): the data path on a scripted dongle (data block envelope, flow create + CREATE_CMPLT, 16 rx posts, rx poll, host 4-way M1-M4 from the python vectors, key installs, ARP + echo ping; the ioctl transport is a recording stub there) and the WLANCRED reader (T-f5f7 step 2); plain and ASan/UBSan. sweet_wlan_l25.h, sweet_wlan_l25_hs.h, join.h and data.h are read from shim/ (not in the glue yet); the g2.h the test includes is the one extracted from patch 0174 in $O
	for t in wlan_l25_host_test wlan_l25_wl_host_test wlan_l25_cred_host_test wlan_rsne_host_test wlan_glue_l25_host_test; do
		cp $t.c $O/$t.c
		chk "$t (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -I$O -I. -o $O/$t $O/$t.c
		chk "$t" $O/$t
		chk "$t (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -D_GNU_SOURCE -I$O -I. -o $O/${t}_asan $O/$t.c
		chk "$t (asan)" $O/${t}_asan
	done
	# sprint 2026-11-08: en0 over the live L25 engine state (wlan_en0_bus.h): 7000-frame soak, every ring wraps, rx re-post + tx reuse, plain and ASan/UBSan
	cp wlan_en0_live_host_test.c wlan_l25_host_test.c $O/
	chk "wlan_en0_live_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -I$O -I. -o $O/wlan_en0_live_host_test $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test" $O/wlan_en0_live_host_test
	chk "wlan_en0_live_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -D_GNU_SOURCE -I$O -I. -o $O/wlan_en0_live_host_test_asan $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test (asan)" $O/wlan_en0_live_host_test_asan
	# sprint-wifi Part A: the same soak over a bus with rd_blk / wr_blk in quiet mode (the batched DP15 transport, engine side), plain and ASan/UBSan
	chk "wlan_en0_live_host_test blk (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_BLK -I$O -I. -o $O/wlan_en0_live_host_test_blk $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test blk" $O/wlan_en0_live_host_test_blk
	chk "wlan_en0_live_host_test blk (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_BLK -I$O -I. -o $O/wlan_en0_live_host_test_blk_asan $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test blk (asan)" $O/wlan_en0_live_host_test_blk_asan
	# patch 0357: the same soak with an rx pool of 255 and of 64 buffers (the scripted firmware advertises it at shared +0x22, the DATA block holds 255): batched refill through the real audit, executor and judge, one doorbell per batch
	for rxn in ${RXPOOLS:-255}; do      # 255 plain, blk, ASan; the other pools (48 by boot-arg, 72 by block size) below; pool 64 added ~12 s of compiles to build-wlan (gate land6 timeout)
		chk "wlan_en0_live_host_test rxn$rxn (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_RXN=$rxn -I$O -I. -o $O/wlan_en0_live_host_test_rxn$rxn $O/wlan_en0_live_host_test.c
		chk "wlan_en0_live_host_test rxn$rxn" $O/wlan_en0_live_host_test_rxn$rxn
		chk "wlan_en0_live_host_test rxn$rxn blk (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_BLK -DEN0_LIVE_RXN=$rxn -I$O -I. -o $O/wlan_en0_live_host_test_rxn${rxn}_blk $O/wlan_en0_live_host_test.c
		chk "wlan_en0_live_host_test rxn$rxn blk" $O/wlan_en0_live_host_test_rxn${rxn}_blk
		chk "wlan_en0_live_host_test rxn$rxn (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_RXN=$rxn -I$O -I. -o $O/wlan_en0_live_host_test_rxn${rxn}_asan $O/wlan_en0_live_host_test.c
		chk "wlan_en0_live_host_test rxn$rxn (asan)" $O/wlan_en0_live_host_test_rxn${rxn}_asan
	done
	# the boot-arg beats the firmware value (fw 255, sweetwlanrxpost=48 -> 48) and the block caps it (fw 255, a block of 72 rx buffers -> 72)
	chk "wlan_en0_live_host_test arg48 (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_RXN=48 -DEN0_LIVE_FW=255 -DEN0_LIVE_ARG=48 -I$O -I. -o $O/wlan_en0_live_host_test_arg48 $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test arg48" $O/wlan_en0_live_host_test_arg48
	chk "wlan_en0_live_host_test blk72 (compile)" gcc -O1 -Wall -Wextra -Werror -D_GNU_SOURCE -DEN0_LIVE_RXN=72 -DEN0_LIVE_FW=255 -DEN0_LIVE_BLKRX=72 -I$O -I. -o $O/wlan_en0_live_host_test_blk72 $O/wlan_en0_live_host_test.c
	chk "wlan_en0_live_host_test blk72" $O/wlan_en0_live_host_test_blk72
	# sprint 2026-11-08: patch 0268 (keep mode + en0 hand-over) carries a byte copy of shim/wlan_en0_bus.h
	awk '$0=="+++ b/bsd/dev/sweet_wlan_en0_bus.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0268-sweet-wlan-keep-en0.patch > $O/sweet_wlan_en0_bus.0268.h
	chk "sweet_wlan_en0_bus.h in patch 0268 == shim/wlan_en0_bus.h" cmp -s $O/sweet_wlan_en0_bus.0268.h wlan_en0_bus.h
	# xnu patch 0206 (T-4209) carries verbatim copies of the join, cred and L25 headers (nothing includes them yet, T-8573 does): each new-file hunk must equal shim/
	for h in join join_fw cred l25 l25_hs l25_rsne l25_glue l25_wl; do
		awk -v f="+++ b/bsd/dev/sweet_wlan_$h.h" '$0==f{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0206-sweet-wlan-l25-headers.patch > $O/sweet_wlan_$h.0206.h
		chk "sweet_wlan_$h.h in patch 0206 == shim/sweet_wlan_$h.h" cmp -s $O/sweet_wlan_$h.0206.h sweet_wlan_$h.h
	done
	echo "RESULT: PASS wlan_l25_headers_in_patch"
	# firmware container parser of xnu patch 0128 (new-file hunk of bsd/dev/sweet_wlan_fw.h)
	awk '$0=="+++ b/bsd/dev/sweet_wlan_fw.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0128-sweet-wlan-fwcheck.patch > $O/sweet_wlan_fw.h
	# patch 0185 (X-W7 gap lines) adds the pure swf_gaps() to the same header: apply its hunk on top of the 0128 text
	rm -rf $O/fwp; mkdir -p $O/fwp/bsd/dev; cp $O/sweet_wlan_fw.h $O/fwp/bsd/dev/sweet_wlan_fw.h
	awk '/^diff --git a\/bsd\/dev\/sweet_wlan_fw.h /{on=1;print;next} on&&/^diff --git /{exit} on{print}' ../xnu/patches/0185-sweet-wlan-fw-gaps.patch > $O/sweet_wlan_fw.0185.diff
	patch -s -p1 -d $O/fwp < $O/sweet_wlan_fw.0185.diff || { echo "FAIL patch 0185 does not apply to sweet_wlan_fw.h" >&2; exit 1; }
	cp $O/fwp/bsd/dev/sweet_wlan_fw.h $O/sweet_wlan_fw.h
	chk "wlan_fw_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_fw_host_test wlan_fw_host_test.c
	chk "wlan_fw_host_test" $O/wlan_fw_host_test
	awk '$0=="+++ b/bsd/dev/sweet_mali_pt.h"{on=1;next} on&&/^--- /{exit} on&&/^diff /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0104-sweet-mali-driver-core.patch > $O/sweet_mali_pt.h
	chk "mali_pt_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/mali_pt_host_test mali_pt_host_test.c
	chk "mali_pt_host_test" $O/mali_pt_host_test
	# runtime MMIO poke parser of the same driver (xnu patch 0093, new-file hunk)
	awk '$0=="+++ b/bsd/dev/sweet_touch_poke.h"{on=1;next} on&&/^diff --git/{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0093-sweet-touch-poke.patch > $O/sweet_touch_poke.h
	chk "touchpoke_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/touchpoke_host_test touchpoke_host_test.c
	chk "touchpoke_host_test" $O/touchpoke_host_test
	# station boundary of the future Apple80211 handler (X-W1, header-only, own code)
	chk "wlan_sta_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_sta_host_test wlan_sta_host_test.c
	chk "wlan_sta_host_test" $O/wlan_sta_host_test
	chk "wlan_sta_codec_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I$O -o $O/wlan_sta_codec_host_test wlan_sta_codec_host_test.c
	chk "wlan_sta_codec_host_test" $O/wlan_sta_codec_host_test
	chk "wlan_traffic_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_traffic_host_test wlan_traffic_host_test.c
	chk "wlan_traffic_host_test" $O/wlan_traffic_host_test
	# host WPA2-PSK join: crypto, EAPOL M1-M4, wsec_key and join payloads vs IEEE/RFC vectors + python reference (T-5dc1, SPEC-WIFI Part E)
	chk "wlan_4way_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_4way_host_test wlan_4way_host_test.c
	chk "wlan_4way_host_test" $O/wlan_4way_host_test
	# review T-48a0 r1 F4 (T-4459): the stack-residue check (test_wipe) must hold at every optimisation level
	for ol in -O0 -O2; do
		chk "wlan_4way_host_test ($ol compile)" gcc $ol -Wall -Wextra -Werror -o $O/wlan_4way_host_test$ol wlan_4way_host_test.c
		chk "wlan_4way_host_test ($ol)" $O/wlan_4way_host_test$ol
	done
	# Wi-Fi data path: msgbuf item layouts, D2H audit, ring/flow/request-id books, ARP/ICMP engine (T-71e2, SPEC-WIFI Part E DATA block)
	chk "wlan_data_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_data_host_test wlan_data_host_test.c
	chk "wlan_data_host_test" $O/wlan_data_host_test
	# Wi-Fi en0 ifnet (T-4d29, SPEC-WIFI Part E EN0 block EN1-EN12): stub ifnet/mbuf/bpf + fake bus, plain and ASan/UBSan; mutants: tools/test_wlan_en0_mutants.py.
	# xnu patch ../xnu/patches/0199-sweet-wlan-en0-ifnet.patch is GENERATED from the shim sources (tools/gen-wlan-en0-patch): a stale copy fails here.
	chk "wlan_en0_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_en0_host_test wlan_en0_host_test.c
	chk "wlan_en0_host_test" $O/wlan_en0_host_test
	chk "wlan_en0_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -o $O/wlan_en0_host_test_asan wlan_en0_host_test.c
	chk "wlan_en0_host_test (asan)" $O/wlan_en0_host_test_asan
	chk "xnu patch 0199 == shim/ sources" ../tools/gen-wlan-en0-patch --check
	# Apple80211 ioctl forward + kev events on en0 (T-7241, P-W4, goal W-D7): fake radio / user memory / kev_post_msg + the real en0_ioctl, plain and ASan/UBSan.
	# xnu patch ../xnu/patches/0201-sweet-wlan-a80211-glue.patch is GENERATED from the shim sources (tools/gen-wlan-a80211-patch): a stale copy fails here.
	chk "wlan_a80211_glue_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_a80211_glue_host_test wlan_a80211_glue_host_test.c
	chk "wlan_a80211_glue_host_test" $O/wlan_a80211_glue_host_test
	chk "wlan_a80211_glue_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -o $O/wlan_a80211_glue_host_test_asan wlan_a80211_glue_host_test.c
	chk "wlan_a80211_glue_host_test (asan)" $O/wlan_a80211_glue_host_test_asan
	chk "xnu patch 0201 == shim/ sources" ../tools/gen-wlan-a80211-patch --check
	# T-1435 (W-UI host build): the whole wifid/Settings selector sequence through the real glue + the real 16.7 codec on a fake radio; one SEL line per selector (proof table).
	chk "wlan_a80211_fake_radio_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_a80211_fake_radio_host_test wlan_a80211_fake_radio_host_test.c
	chk "wlan_a80211_fake_radio_host_test" $O/wlan_a80211_fake_radio_host_test
	chk "wlan_a80211_fake_radio_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -o $O/wlan_a80211_fake_radio_host_test_asan wlan_a80211_fake_radio_host_test.c
	chk "wlan_a80211_fake_radio_host_test (asan)" $O/wlan_a80211_fake_radio_host_test_asan
	# swsta_radio backend over escan result events (T-431f, P-W4, goal W-D7): fake dongle link + the real A80211 handler with the 16.7 codec; part of patch 0201 (bsd/dev/sweet_wlan_radio.h).
	# T-08fd: the link rows run the real G2 L23 code (patch 0174, extracted above into $O) through the fake dongle of wlan_g2_host_test.c (wlan_radio_g2_scan.c) and replay its record.
	chk "wlan_radio_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -I. -I$O -o $O/wlan_radio_host_test wlan_radio_host_test.c wlan_radio_g2_scan.c
	chk "wlan_radio_host_test" $O/wlan_radio_host_test
	chk "wlan_radio_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -I. -I$O -o $O/wlan_radio_host_test_asan wlan_radio_host_test.c wlan_radio_g2_scan.c
	chk "wlan_radio_host_test (asan)" $O/wlan_radio_host_test_asan
	# Wi-Fi registry objects for wifid/configd (T-2144, SPEC-WIFI Part F W1-W3): property and class-chain tables vs the cited rows, consumers modelled as predicates + mutated tables.
	# xnu patch ../xnu/patches/0200-sweet-wlan-registry.patch is GENERATED from the shim sources (tools/gen-wlan-registry-patch): a stale copy fails here.
	chk "wlan_registry_host_test (compile)" gcc -O1 -Wall -Wextra -Werror -o $O/wlan_registry_host_test wlan_registry_host_test.c
	chk "wlan_registry_host_test" $O/wlan_registry_host_test
	chk "wlan_registry_host_test (asan compile)" gcc -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror -o $O/wlan_registry_host_test_asan wlan_registry_host_test.c
	chk "wlan_registry_host_test (asan)" $O/wlan_registry_host_test_asan
	chk "xnu patch 0200 == shim/ sources" ../tools/gen-wlan-registry-patch --check
	# the inputs may have been edited while the tests ran: stamp only if the digest is unchanged (otherwise the next build runs them again)
	if [ -n "$ht_filter" ]; then echo "host tests: filtered by '$ht_filter', no stamp"; npass=$(grep -c . "$O/host-tests.list" 2>/dev/null || echo 0); echo "RESULT: PASS host tests matching '$ht_filter' ($npass test binaries listed)"; exit 0; fi
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
# --pmic-src-clear: the PMIC reset-source write (docs/research/142) exists ONLY in a flagged image: no function, no FUNC_WRITE caller, no pmicsrc_clear.o in the link
if [ "$pmic_src_clear" = 0 ]; then
	chk "shipped image has no acpm_pmic_src_clear / pmic_src_clear (no --pmic-src-clear): the reset-source write is not in the image" sh -c '! nm $O/acpm.o $O/pmicsrc.o $O/shim.elf | grep -q "pmic_src_clear\|pmic_src_clr" && test ! -e $O/pmicsrc_clear.o'
else
	chk "the --pmic-src-clear image has acpm_pmic_src_clear and pmic_src_clear" sh -c 'nm $O/shim.elf | grep -q " T acpm_pmic_src_clear" && nm $O/shim.elf | grep -q " T pmic_src_clear"'
fi
if [ "$sweet_probe" = 0 ]; then
	chk "the image has no sweet_probe_cmdline / sweetprobe_smc (no --sweet-probe): no PSCI query and no SLC request code" sh -c '! nm $O/shim.elf | grep -q "sweet_probe_cmdline\|sweetprobe_smc"'
else
	chk "the --sweet-probe image has sweet_probe_cmdline and acpm_slc_version" sh -c 'nm $O/shim.elf | grep -q " T sweet_probe_cmdline" && nm $O/shim.elf | grep -q " T acpm_slc_version"'
fi
if [ "$gpu_rail_on" = 0 ]; then
	chk "shipped acpm.o has no acpm_s11_rail_set (no --gpu-rail-on): the S2MPG11 write is not in the image" sh -c '! nm $O/acpm.o | grep -q acpm_s11_rail_set'
else
	chk "shipped acpm.o of a --gpu-rail-on image has acpm_s11_rail_set" sh -c 'nm $O/acpm.o | grep -q acpm_s11_rail_set'
fi
if [ "$sweet_probe" = 0 ]; then
	chk "shipped acpm.o has no acpm_slc_* (no --sweet-probe)" sh -c 'test -s $O/acpm.o && ! nm $O/acpm.o | grep -q acpm_slc_'
else
	chk "shipped acpm.o of a --sweet-probe image has acpm_slc_version" sh -c 'nm $O/acpm.o | grep -q acpm_slc_version'
fi
if [ "$mif_clk" = 0 ]; then
	chk "shipped acpm.o has no acpm_dvfs_mif_top (no --mif-clk)" sh -c 'test -s $O/acpm.o && ! nm $O/acpm.o | grep -q acpm_dvfs_mif_top'
else
	chk "shipped acpm.o of a --mif-clk image has acpm_dvfs_mif_top" sh -c 'nm $O/acpm.o | grep -q acpm_dvfs_mif_top'
	chk "shipped mifclk.o has no MMIO accessor" sh -c 'test -s $O/mifclk.o && ! nm $O/mifclk.o | grep -q "try_write32\|try_read32"'
	chk "shipped mifclk.o reaches only the console, the timer and the acpm client" sh -c 'test -s $O/mifclk.o && ! nm -u $O/mifclk.o | awk "{print \$2}" | grep -v "^\(con_str\|counter_freq\|wait_ticks\|acpm_cfg_init\|acpm_describe\|acpm_dvfs_get\|acpm_dvfs_mif_top\|acpm_why\)$" | grep .'
fi
if [ "$gpu_clk" = 0 ]; then
	chk "shipped acpm.o has no acpm_dvfs_reassert (no --gpu-clk)" sh -c '! nm $O/acpm.o | grep -q acpm_dvfs_reassert'
else
	chk "shipped acpm.o of a --gpu-clk image has acpm_dvfs_reassert" sh -c 'nm $O/acpm.o | grep -q acpm_dvfs_reassert'
	chk "shipped gpuclk.o has no MMIO accessor" sh -c '! nm $O/gpuclk.o | grep -q "try_write32\|try_read32"'
fi
# G5 (docs/plans/PLAN-GATE-2X.md): every audit_memo.run() of the audits below appends hit|cold to this file (cold + a reason when it was not stored); one parseable line per build
export PIXELIOS_AUDIT_MEMO_STATS="$O/memo-stats.tsv"; : >"$PIXELIOS_AUDIT_MEMO_STATS"
if [ "$gs101" = 1 ]; then
	"$PYTHON" ../tools/test-bluejay-boot.py --out "$O" "$O/shim-boot.img" "$O/shim-boot-lz4.img"
fi
if [ "$el2_trap" = 1 ]; then	# the EL2 object audit + its mutation self-test (SPEC 3.8, B4): el2_asm.o / el2*.o / start.o vs start_nodef.o / shim.o call sites / shim.elf symbols
	"$PYTHON" ../tools/test-bluejay-boot.py --out "$O" --el2-objects
fi
# one parseable line: `memo hits=N cold=M`, or `memo DISABLED ...` / `memo STATS UNAVAILABLE|EMPTY ...` (never a silent hits=0 cold=0); cold reasons (miss | off | fail | not-stored:<why>): column 3 of $O/memo-stats.tsv
if [ "$gs101" = 1 ]; then	# a gs101 build runs the audits: counting none is an error, not a healthy 0/0
	"$PYTHON" ../tools/audit_memo.py --summary "$PIXELIOS_AUDIT_MEMO_STATS" --expect-runs
else
	"$PYTHON" ../tools/audit_memo.py --summary "$PIXELIOS_AUDIT_MEMO_STATS"
fi
unset PIXELIOS_AUDIT_MEMO_STATS
# provenance sidecar for the image fastboot will boot (tools/fboot.sh refuses images without a matching one)
if [ -f $O/shim-boot-avb.img ]; then
	if [ "$boot_xnu" = 1 ]; then kind=xnu; elif [ "$udump_mib" != 0 ]; then kind=udump; else kind=probe; fi
	"$PYTHON" ../tools/imgmeta.py write $O/shim-boot-avb.img --kind "$kind" --args "${META_ARGS:-}"
fi
ls -l $O/Image $O/shim-boot*.img
