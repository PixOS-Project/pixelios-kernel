#!/bin/bash
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
gpu_probe=0
gpu_power=0
gpu_stack=0
gpu_clk=0
gpu_rails=0
gpu_rail_on=0
gpu_s2mpu=0
gpu_job=0
touch_probe=0
touch_opts=
touch_rails=0
touch_dvfs=0
dvfs_khz=
while [ "$#" -gt 0 ]; do
	case "$1" in
		--board) board="$2"; shift 2 ;;
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
		--wlan-link=*) wlan_link=1; wlan_probe=1; wlan_link_n="${1#--wlan-link=}"; shift ;;
		--wlan-en) wlan_en=1; wlan_probe=1; shift ;;	# implies --wlan-probe; WRITER: WLAN_EN gpp14-2 high for 200 ms and back (wlanen.c, docs/research/29 W2b)
		--gpu-probe) gpu_probe=1; shift ;;
		--gpu-job) gpu_job=1; gpu_probe=1; gpu_rails=1; gpu_rail_on=1; gpu_power=1; gpu_s2mpu=1; shift ;;	# G2 stage 2: AS0 + one WRITE_VALUE job inside the powered window (gpujob.c)
		--gpu-s2mpu) gpu_s2mpu=1; gpu_probe=1; shift ;;	# READ-ONLY dump of the four G3D S2MPU blocks (gpus2mpu.c)
		--gpu-rail-on) gpu_rail_on=1; gpu_rails=1; gpu_probe=1; shift ;;	# WRITER: S2MPG11 BUCK2S mode on/restore via ACPM (gpurailon.c); implies --gpu-rails
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
MKBOOTIMG=${MKBOOTIMG:-../tools/third_party/mkbootimg.py}
AVBTOOL=${AVBTOOL:-../tools/third_party/avbtool.py}
# Marker line identity: git short hash of this worktree (date fallback
# when detached from git), so the pulled pstore log names its build.
build_id=$(git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d)
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -march=armv8-a+crc -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra -Iout -include out/board_config.h -DPSTORE_BUILD_ID=\"$build_id\""
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
if [ "$wlan_link" = 1 ]; then
	[ "$gs101" = 1 ] || { echo "--wlan-link needs a gs101 board" >&2; exit 2; }
	[ "$wlan_en" = 0 ] && [ "$wlan_iso" = 0 ] && [ "$wlan_smc_read" = 0 ] || { echo "--wlan-link cannot be combined with --wlan-en / --wlan-iso / --wlan-smc-read (separate reviewed units; the chain contains their steps)" >&2; exit 2; }
	case "$wlan_link_n" in ''|[0-9]|[0-9][0-9]) ;; *) echo "--wlan-link=N needs a step number 0..31" >&2; exit 2 ;; esac
	[ -z "$wlan_link_n" ] || [ "$wlan_link_n" -le 31 ] || { echo "--wlan-link=N: N must be <= 31 (the last step of soc/google/gs101/wlan_seq.json)" >&2; exit 2; }
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
mkdir -p out
"$PYTHON" ../tools/boards.py "$board" out/board_config.h
[ "$gs101" = 1 ] && "$PYTHON" ../tools/boards.py "$board" out/wlan_seq.h --wlan-seq
for f in start.S shim.c console.c ramlog.c mmu.c fdt.c pstore.c loader.c payload.c ufs.c usbprobe.c; do
	"$CC" $CFLAGS -c "$f" -o "out/${f%.*}.o"
done
# SoC-specific UFS code lives with the SoC (soc/<vendor>/<soc>/shim); same object names as before.
socshim=../$("$PYTHON" ../tools/boards.py "$board" --get soc_dir)/shim
for f in ufsdiag ufsinit; do
	"$CC" $CFLAGS -I"$socshim" -I. -c "$socshim/$f.c" -o "out/$f.o"
done
# Gate honesty: usbproto.c and usbgadget.c are
# cross-compiled with the shim CFLAGS on EVERY build so the object
# audits in tools/test-bluejay-boot.py always see them; they are only
# LINKED with --usb-gadget, so the default images stay unchanged.
# usbgadget.o calls try_write32, which shim.c defines under
# SOC_GS101, so --usb-gadget is only meaningful with a gs101 board.
"$CC" $CFLAGS -c usbproto.c -o out/usbproto.o
"$CC" $CFLAGS -c usbgadget.c -o out/usbgadget.o
"$CC" $CFLAGS -c usbpool.c -o out/usbpool.o
# Gate honesty: udump.c is cross-compiled with
# the shim CFLAGS on EVERY build so the object audit in
# tools/test-bluejay-boot.py always sees it; it is only LINKED with
# --udump (which also forces --usb-gadget), so default images stay
# byte-identical.
"$CC" $CFLAGS -c udump.c -o out/udump.o
# Same for wlanprobe.c (read-only audit sees it every build); only LINKED with --wlan-probe.
"$CC" $CFLAGS -c wlanprobe.c -o out/wlanprobe.o
# Same for wlanrails.c (read-only rail / CMU / PMU words, docs/research/29); only LINKED with --wlan-rails. Reads only (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c wlanrails.c -o out/wlanrails.o
# Same for wlansmc.c (W2a: two SMC READs); built with -DWLAN_SMC_READ on every build so the audit sees the real code (the one smc instruction), only LINKED with --wlan-smc-read.
# wlanlink.c (W3a, the table-driven chain; audited every build, linked only with --wlan-link) and wlansmc_link.o = wlansmc.c with the two chain steps (wlan_link_iso_set/clear) INSTEAD of wlan_iso_run
"$CC" $CFLAGS -DWLAN_LINK_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlanlink.c -o out/wlanlink.o
if [ "$wlan_link" = 1 ]; then	# the effective step bound N, read from the preprocessed wlanlink.c (its _Static_assert(N <= LAST)); N >= 20 is accepted only for an explicit --wlan-link=N
	eff_n=$("$CC" $CFLAGS -DWLAN_LINK_RUN -E wlanlink.c | sed -n 's/.*_Static_assert(\([0-9][0-9]*\)u\{0,1\} <= .*/\1/p')
	"$PYTHON" ../tools/test-bluejay-boot.py --wlan-link-effective "${eff_n:-none}" --wlan-link-explicit "$wlan_link_n"
fi
"$CC" $CFLAGS -DWLAN_SMC_READ -DWLAN_ISO_RUN -DWLAN_LINK_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o out/wlansmc_link.o
"$CC" $CFLAGS -UWLAN_ISO_RUN -UWLAN_LINK_RUN -DWLAN_SMC_READ -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o out/wlansmc.o
# and wlansmc_iso.o = the same file with the W2c RMW writer (WLAN_ISO_RUN), audited every build, linked INSTEAD of wlansmc.o with --wlan-iso (an --wlan-smc-read image contains no RMW code)
"$CC" $CFLAGS -UWLAN_LINK_RUN -DWLAN_SMC_READ -DWLAN_ISO_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlansmc.c -o out/wlansmc_iso.o
# Same for wlanen.c (the opt-in WLAN_EN writer); built with -DWLAN_EN_RUN on every build so the audit sees the real code, only LINKED with --wlan-en. Its two writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -DWLAN_EN_RUN -fno-tree-switch-conversion -fno-jump-tables -c wlanen.c -o out/wlanen.o
# Same for gpuprobe.c; only LINKED with --gpu-probe. Its name switches must not become
# .rodata pointer tables (ABS64 relocations, rejected below).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpuprobe.c -o out/gpuprobe.o
# Same for gpupower.c (the opt-in GPU power cycle); only LINKED with --gpu-power. Its writes go through one allowlisted helper (audited).
NOJOB_CFLAGS="${CFLAGS//-DGPU_JOB/}"	# the plain / stack gpupower objects never carry the --gpu-job hook (their audit forbids the extra undefined symbol)
"$CC" $NOJOB_CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o out/gpupower.o
# and its --gpu-power=stack variant (extra STACK_PWRON/PWROFF writes), also audited every build, linked only with --gpu-power=stack
"$CC" $NOJOB_CFLAGS -DGPU_POWER_STACK -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o out/gpupower_stack.o
# Same for gpuclk.c (the opt-in GPU clock request); only LINKED with --gpu-clk. It has no MMIO writer of its own (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpuclk.c -o out/gpuclk.o
# Same for gpus2mpu.c (read-only S2MPU dump); only LINKED with --gpu-s2mpu (or --gpu-job). Reads only, audited like gpuprobe.o.
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpus2mpu.c -o out/gpus2mpu.o
# Same for gpujob.c (the stage-2 job writer; only LINKED with --gpu-job) and gpupower_job.o (gpupower.c with the one `gpu_job_run()` hook in the powered window; linked instead of gpupower.o
# with --gpu-job). Both are audited on every build (writer audit / object audit in tools/test-bluejay-boot.py).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpujob.c -o out/gpujob.o
"$CC" $CFLAGS -DGPU_JOB -fno-tree-switch-conversion -fno-jump-tables -c gpupower.c -o out/gpupower_job.o
# Same for gpurails.c (read-only GPU-rail dump); only LINKED with --gpu-rails. No MMIO of its own (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c gpurails.c -o out/gpurails.o
# Same for gpurailon.c (the opt-in rail writer's orchestration); only LINKED with --gpu-rail-on. No MMIO of its own (audited). Built with -DGPU_RAIL_ON on every
# build so the audit sees the real code; acpm_railon.o is acpm.c with the GPU_RAIL_ON writer, for the audit only (the image links acpm.o built with the image's flags).
"$CC" $CFLAGS -DGPU_RAIL_ON -fno-tree-switch-conversion -fno-jump-tables -c gpurailon.c -o out/gpurailon.o
"$CC" $CFLAGS -DGPU_RAIL_ON -fno-tree-switch-conversion -fno-jump-tables -c acpm.c -o out/acpm_railon.o
# Same for touchprobe.c; only LINKED with --touch-probe. Its writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c touchprobe.c -o out/touchprobe.o
# Same for acpm.c (ACPM client for the touch rails); only LINKED with --touch-probe=railsr|rails. Its writes go through one allowlisted helper (audited).
"$CC" $CFLAGS -fno-tree-switch-conversion -fno-jump-tables -c acpm.c -o out/acpm.o
qr_objs=
if [ "$qrlog" = 1 ]; then
	for f in qrlog qrcodegen qrlog_libc; do
		"$CC" $CFLAGS -Wno-unused-parameter -Wno-sign-compare -c "../qrlog/$f.c" -o "out/$f.o"
	done
	qr_objs="out/qrlog.o out/qrcodegen.o out/qrlog_libc.o"
fi
usb_objs=
if [ "$usb_gadget" = 1 ]; then
	usb_objs="out/usbproto.o out/usbgadget.o"
fi
if [ "$udump_mib" != 0 ]; then
	usb_objs="$usb_objs out/udump.o"
fi
if [ "$wlan_probe" = 1 ]; then
	usb_objs="$usb_objs out/wlanprobe.o"
fi
if [ "$wlan_rails" = 1 ]; then
	usb_objs="$usb_objs out/wlanrails.o"
fi
if [ "$wlan_link" = 1 ]; then
	usb_objs="$usb_objs out/wlanlink.o out/wlansmc_link.o"
elif [ "$wlan_iso" = 1 ]; then
	usb_objs="$usb_objs out/wlansmc_iso.o"
elif [ "$wlan_smc_read" = 1 ]; then
	usb_objs="$usb_objs out/wlansmc.o"
fi
if [ "$wlan_en" = 1 ]; then
	usb_objs="$usb_objs out/wlanen.o"
fi
if [ "$gpu_probe" = 1 ]; then
	usb_objs="$usb_objs out/gpuprobe.o"
fi
if [ "$gpu_clk" = 1 ]; then
	usb_objs="$usb_objs out/gpuclk.o"
fi
if [ "$gpu_s2mpu" = 1 ]; then
	usb_objs="$usb_objs out/gpus2mpu.o"
fi
if [ "$gpu_rails" = 1 ]; then
	usb_objs="$usb_objs out/gpurails.o"
fi
if [ "$gpu_rail_on" = 1 ]; then
	usb_objs="$usb_objs out/gpurailon.o"
fi
if [ "$gpu_job" = 1 ]; then
	usb_objs="$usb_objs out/gpupower_job.o out/gpujob.o"
elif [ "$gpu_stack" = 1 ]; then
	usb_objs="$usb_objs out/gpupower_stack.o"
elif [ "$gpu_power" = 1 ]; then
	usb_objs="$usb_objs out/gpupower.o"
fi
if [ "$touch_probe" != 0 ]; then
	usb_objs="$usb_objs out/touchprobe.o"
fi
if [ "$touch_rails" = 1 ] || [ "$touch_dvfs" = 1 ] || [ "$gpu_clk" = 1 ] || [ "$gpu_rails" = 1 ] || [ "$wlan_rails" = 1 ]; then
	usb_objs="$usb_objs out/acpm.o"
fi
abs=$("$READELF" -r out/shim.o out/console.o out/ramlog.o out/mmu.o out/fdt.o out/pstore.o out/loader.o out/payload.o out/ufs.o out/ufsdiag.o out/ufsinit.o out/usbprobe.o out/usbproto.o out/usbgadget.o out/usbpool.o out/udump.o out/wlanprobe.o out/wlanrails.o out/wlanen.o out/wlansmc.o out/wlansmc_iso.o out/wlansmc_link.o out/wlanlink.o out/gpuprobe.o out/gpupower.o out/gpupower_stack.o out/gpupower_job.o out/gpujob.o out/gpuclk.o out/gpus2mpu.o out/gpurails.o out/gpurailon.o out/acpm_railon.o out/touchprobe.o out/acpm.o $qr_objs | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
"$CC" $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	out/start.o out/shim.o out/console.o out/ramlog.o out/mmu.o out/fdt.o out/pstore.o out/loader.o out/payload.o out/ufs.o out/ufsdiag.o out/ufsinit.o out/usbprobe.o out/usbpool.o $usb_objs $qr_objs -o out/shim.elf
"$OBJCOPY" -O binary out/shim.elf out/Image
size_hex=$("$NM" out/shim.elf | awk '$3=="_end"{print $1}')
size=$((16#$size_hex))
truncate -s "$size" out/Image
if [ -n "$payload" ]; then
	cp "$payload" out/ramdisk.bin
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

if [ "$board" = sweet ]; then
	dtb=${SWEET_DTB:-../dtb/boot_unpacked/dtb}
	"$PYTHON" "$MKBOOTIMG" --header_version 2 --kernel out/Image --ramdisk out/ramdisk.bin \
		--dtb "$dtb" --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --dtb_offset 0x1f00000 --pagesize 4096 \
		--os_version 17.0.0 --os_patch_level 2035-06 --cmdline "console=null" -o out/shim-boot.img
else
	"$PYTHON" "$MKBOOTIMG" --header_version 4 --kernel out/Image --ramdisk out/ramdisk.bin \
		--pagesize 4096 --os_version 12.0.0 --os_patch_level 2022-07 \
		--cmdline "console=null" -o out/shim-boot.img
	"$PYTHON" -c 'import lz4.frame, pathlib; d = pathlib.Path("out/Image").read_bytes(); pathlib.Path("out/Image.lz4").write_bytes(lz4.frame.compress(d))'
	"$PYTHON" "$MKBOOTIMG" --header_version 4 --kernel out/Image.lz4 --ramdisk out/ramdisk.bin \
		--pagesize 4096 --os_version 12.0.0 --os_patch_level 2022-07 \
		--cmdline "console=null" -o out/shim-boot-lz4.img
	if [ "$avb" = 1 ]; then
		key=out/bluejay-testkey.pem
		[ -f "$key" ] || openssl genrsa -out "$key" 2048
		cp out/shim-boot.img out/shim-boot-avb.img
		"$PYTHON" "$AVBTOOL" add_hash_footer --image out/shim-boot-avb.img \
			--partition_name boot --partition_size "${AVB_PARTITION_SIZE:-67108864}" \
			--key "$key" --algorithm SHA256_RSA2048
		cp out/shim-boot-lz4.img out/shim-boot-lz4-avb.img
		"$PYTHON" "$AVBTOOL" add_hash_footer --image out/shim-boot-lz4-avb.img \
			--partition_name boot --partition_size "${AVB_PARTITION_SIZE:-67108864}" \
			--key "$key" --algorithm SHA256_RSA2048
	fi
fi
gcc -O1 -Wall -o out/host_test console.c ramlog.c host_test.c && ./out/host_test
gcc -O1 -Wall -Wextra -o out/ramlog_host_test ramlog.c ramlog_host_test.c && ./out/ramlog_host_test
gcc -O1 -Wall -Wextra -I. -o out/fdt_host_test fdt.c fdt_host_test.c && ./out/fdt_host_test
gcc -O1 -Wall -Wextra -I. -o out/pstore_host_test fdt.c pstore.c pstore_host_test.c && ./out/pstore_host_test
gcc -O1 -Wall -Wextra -I. -o out/usbprobe_host_test fdt.c usbprobe.c usbprobe_host_test.c && ./out/usbprobe_host_test
gcc -O1 -Wall -Wextra -I. -o out/usbproto_host_test usbproto.c usbproto_host_test.c && ./out/usbproto_host_test
gcc -O1 -Wall -Wextra -I. -o out/usbgadget_host_test usbproto.c usbgadget.c usbgadget_host_test.c && ./out/usbgadget_host_test
gcc -O1 -Wall -Wextra -I. -DUSB_DMA_HI_BASE=1 -o out/usbpool_host_test usbpool.c usbpool_host_test.c && ./out/usbpool_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/wlanprobe_host_test wlanprobe_host_test.c && ./out/wlanprobe_host_test
# --wlan-rails (wlanrails.c) against the fake APM of acpm_host_test.c; --wlan-en (wlanen.c) against a fake GPIO bank. try_write32 is deliberately absent from the rails test (linking fails if wlanrails.c ever writes).
gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DTOUCH_RAILS_WRITE -I. -Iout -include out/board_config.h -o out/wlanrails_host_test wlanrails_host_test.c && ./out/wlanrails_host_test
gcc -O1 -Wall -Wextra -Werror -DWLAN_EN_RUN -I. -Iout -include out/board_config.h -o out/wlanen_host_test wlanen_host_test.c && ./out/wlanen_host_test
gcc -O1 -Wall -Wextra -Werror -DWLAN_SMC_READ -I. -Iout -include out/board_config.h -o out/wlansmc_host_test wlansmc_host_test.c && ./out/wlansmc_host_test
gcc -O1 -Wall -Wextra -Werror -DWLAN_SMC_READ -DWLAN_ISO_RUN -I. -Iout -include out/board_config.h -o out/wlaniso_host_test wlaniso_host_test.c && ./out/wlaniso_host_test
# --wlan-link (wlanlink.c) against a fake SoC, once per step bound N (S22 is a documented skip: one 0 us delay row; N=20..31 run in the fake SoC; S24 = read-only link report, S25 = read-only iATU layout, S26 = iATU region 0 programmed + CFG window reads, S27 = read-only endpoint config dump + iATU region 1 reset values, S28 = BAR sizing + assignment, S29 = region 1 + BAR0 window + Command MEM, S30 = the first BAR0 read, S31 = read-only ChipCommon eromptr / clk_ctl_st / capabilities_ext)
chk() { local what="$1"; shift; if "$@"; then echo "PASS $what"; else echo "FAIL $what" >&2; exit 1; fi; }	# a failed compile or test must stop the build (review 2 M2: gcc && test inside a loop did not)
for n in 7 8 10 19 20 21 22 23 24 25 26 27 28 29 30 31; do
	chk "wlanlink_host_test$n (compile)" gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DWLAN_LINK_RUN -DWLAN_LINK_N=$n -I. -Iout -include out/board_config.h -o out/wlanlink_host_test$n wlanlink_host_test.c
	chk "wlanlink_host_test$n" ./out/wlanlink_host_test$n
done
gcc -O1 -Wall -Wextra -Werror -I. -o out/gpuprobe_host_test gpuprobe_host_test.c && ./out/gpuprobe_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpuprobe_dump_host_test gpuprobe_dump_host_test.c && ./out/gpuprobe_dump_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpus2mpu_host_test gpus2mpu_host_test.c && ./out/gpus2mpu_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpujob_host_test gpus2mpu.c gpujob_host_test.c && ./out/gpujob_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/gpupower_host_test gpupower_host_test.c && ./out/gpupower_host_test
gcc -O1 -Wall -Wextra -Werror -DGPU_POWER_STACK -I. -Iout -include out/board_config.h -o out/gpupower_stack_host_test gpupower_host_test.c && ./out/gpupower_stack_host_test
# --gpu-clk (gpuclk.c) against the fake DVFS-channel APM of dvfs_host_test.c; the two gate variants use a copy of the board header with one ACPM gate forced to 0
sed 's/^#define ACPM_LAYOUT_VERIFIED 1$/#define ACPM_LAYOUT_VERIFIED 0/' out/board_config.h > out/board_config_l0.h
sed 's/^#define ACPM_ELEM_UNIT_VERIFIED 1$/#define ACPM_ELEM_UNIT_VERIFIED 0/' out/board_config.h > out/board_config_u0.h
GCLK="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -DGPU_CLK -I."
$GCLK -Iout -include out/board_config.h -o out/gpuclk_host_test gpuclk_host_test.c && ./out/gpuclk_host_test
$GCLK -DGATE_TEST -Iout -include out/board_config_l0.h -o out/gpuclk_gate_l_host_test gpuclk_host_test.c && ./out/gpuclk_gate_l_host_test
$GCLK -DGATE_TEST -DGATE_UNIT -Iout -include out/board_config_u0.h -o out/gpuclk_gate_u_host_test gpuclk_host_test.c && ./out/gpuclk_gate_u_host_test
# --gpu-rails (gpurails.c) against the fake APM of acpm_host_test.c; the gate variants again use the sed'ed headers, a null-config variant drops ACPM_S11_KNOWN
GRL="gcc -O1 -Wall -Wextra -Werror -Wno-unused-function -I."
$GRL -DTOUCH_RAILS_WRITE -Iout -include out/board_config.h -o out/gpurails_host_test gpurails_host_test.c && ./out/gpurails_host_test
$GRL -DTOUCH_RAILS_WRITE -DGATE_TEST -Iout -include out/board_config_l0.h -o out/gpurails_gate_l_host_test gpurails_host_test.c && ./out/gpurails_gate_l_host_test
$GRL -DTOUCH_RAILS_WRITE -DGATE_TEST -DGATE_UNIT -Iout -include out/board_config_u0.h -o out/gpurails_gate_u_host_test gpurails_host_test.c && ./out/gpurails_gate_u_host_test
sed 's/^#define ACPM_S11_KNOWN 1$/#define ACPM_S11_KNOWN 0/' out/board_config.h > out/board_config_s0.h
sed 's/^#define ACPM_S11_ON_KNOWN 1$/#define ACPM_S11_ON_KNOWN 0/' out/board_config.h > out/board_config_n0.h
$GRL -DTOUCH_RAILS_WRITE -DNULL_TEST -Iout -include out/board_config_s0.h -o out/gpurails_null_host_test gpurails_host_test.c && ./out/gpurails_null_host_test
# --gpu-rail-on (gpurailon.c, the S2MPG11 BUCK2S writer) against the same fake APM; gate variants and a null rail_on block
GRO="$GRL -DTOUCH_RAILS_WRITE -DGPU_RAIL_ON"
$GRO -Iout -include out/board_config.h -o out/gpurailon_host_test gpurailon_host_test.c && ./out/gpurailon_host_test
$GRO -DGATE_TEST -Iout -include out/board_config_l0.h -o out/gpurailon_gate_l_host_test gpurailon_host_test.c && ./out/gpurailon_gate_l_host_test
$GRO -DGATE_TEST -DGATE_UNIT -Iout -include out/board_config_u0.h -o out/gpurailon_gate_u_host_test gpurailon_host_test.c && ./out/gpurailon_gate_u_host_test
$GRO -DNULL_TEST -Iout -include out/board_config_n0.h -o out/gpurailon_null_host_test gpurailon_host_test.c && ./out/gpurailon_null_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/touch_host_test touch_host_test.c && ./out/touch_host_test
gcc -O1 -Wall -Wextra -Werror -DTOUCH_RAILS_WRITE -I. -Iout -include out/board_config.h -o out/acpm_host_test acpm_host_test.c && ./out/acpm_host_test
for v in 0 1; do gcc -O1 -Wall -Wextra -Werror -DRT_VERIFIED=$v -I. -Iout -include out/board_config.h -o out/rails_host_test$v rails_host_test.c && ./out/rails_host_test$v; done
# ACPM DVFS client (docs/research/25): the fake channel-1 APM; the read-only build has no request tests (no acpm_dvfs_set there).
# Every check below FAILS the build (a plain `a && b && echo PASS` chain only skips the PASS line under set -e: review dvfs-1 M1).
chk() { local what="$1"; shift; if "$@"; then echo "PASS $what"; else echo "FAIL $what" >&2; exit 1; fi; }
gcc -O1 -Wall -Wextra -Werror -DTOUCH_DVFS_WRITE -I. -Iout -include out/board_config.h -o out/dvfs_host_test dvfs_host_test.c
chk "dvfs_host_test (rw)" ./out/dvfs_host_test
gcc -O1 -Wall -Wextra -Werror -I. -Iout -include out/board_config.h -o out/dvfs_host_test_ro dvfs_host_test.c
chk "dvfs_host_test (ro)" ./out/dvfs_host_test_ro
for w in 0 1; do
	gcc -O1 -Wall -Wextra -Werror -DDP_WRITE=$w -I. -Iout -include out/board_config.h -o out/dvfs_probe_host_test$w dvfs_probe_host_test.c
	chk "dvfs_probe_host_test DP_WRITE=$w" ./out/dvfs_probe_host_test$w
done
# review 2 M3: a read-only (railsr) image must not contain the UPDATE path
gcc -O1 -w -DTOUCH_PROBE -DTOUCH_RAILS -I. -Iout -include out/board_config.h -c -o out/acpm_ro.o acpm.c
chk "railsr object has no acpm_pmic_update" sh -c '! nm out/acpm_ro.o | grep -q acpm_pmic_update'
# the same for DVFS: a dvfsr object has the read path and no request path (acpm_dvfs_set); the dvfs object has both
gcc -O1 -w -DTOUCH_PROBE -DTOUCH_DVFS -I. -Iout -include out/board_config.h -c -o out/acpm_dvfs_ro.o acpm.c
chk "dvfsr object has acpm_dvfs_get" sh -c 'nm out/acpm_dvfs_ro.o | grep -q acpm_dvfs_get'
chk "dvfsr object has no acpm_dvfs_set" sh -c '! nm out/acpm_dvfs_ro.o | grep -q acpm_dvfs_set'
gcc -O1 -w -DTOUCH_PROBE -DTOUCH_DVFS -DTOUCH_DVFS_WRITE -I. -Iout -include out/board_config.h -c -o out/acpm_dvfs_rw.o acpm.c
chk "dvfs object has acpm_dvfs_set" sh -c 'nm out/acpm_dvfs_rw.o | grep -q acpm_dvfs_set'
# a --gpu-clk object has the GPU-only request path and NOT the CPU one; a plain one has neither
gcc -O1 -w -DGPU_CLK -I. -Iout -include out/board_config.h -c -o out/acpm_gpuclk.o acpm.c
chk "gpu-clk acpm object has acpm_dvfs_reassert and no acpm_dvfs_set" sh -c 'nm out/acpm_gpuclk.o | grep -q acpm_dvfs_reassert && ! nm out/acpm_gpuclk.o | grep -q acpm_dvfs_set'
chk "acpm objects built without --gpu-clk have no acpm_dvfs_reassert" sh -c '! nm out/acpm_ro.o out/acpm_dvfs_ro.o out/acpm_dvfs_rw.o | grep -q acpm_dvfs_reassert'
# and the SHIPPED objects (built with the image's flags above): a read-only dvfs image must not carry the request path at all
if [ "$touch_dvfs" = 1 ] && [ $((touch_bits & 1024)) = 0 ] && [ "$gpu_clk" = 0 ]; then
	chk "shipped acpm.o/touchprobe.o of a dvfsr image have no acpm_dvfs_set" sh -c '! nm out/acpm.o out/touchprobe.o | grep -q acpm_dvfs_set'
fi
if [ "$gpu_job" = 0 ]; then
	chk "the image has no gpu_job_run / gpujob code (no --gpu-job)" sh -c '! nm out/shim.elf | grep -q "gpu_job_run\|gpu_job_scratch"'
else
	chk "the --gpu-job image has gpu_job_run" sh -c 'nm out/shim.elf | grep -q " T gpu_job_run"'
fi
if [ "$gpu_rail_on" = 0 ]; then
	chk "shipped acpm.o has no acpm_s11_rail_set (no --gpu-rail-on): the S2MPG11 write is not in the image" sh -c '! nm out/acpm.o | grep -q acpm_s11_rail_set'
else
	chk "shipped acpm.o of a --gpu-rail-on image has acpm_s11_rail_set" sh -c 'nm out/acpm.o | grep -q acpm_s11_rail_set'
fi
if [ "$gpu_clk" = 0 ]; then
	chk "shipped acpm.o has no acpm_dvfs_reassert (no --gpu-clk)" sh -c '! nm out/acpm.o | grep -q acpm_dvfs_reassert'
else
	chk "shipped acpm.o of a --gpu-clk image has acpm_dvfs_reassert" sh -c 'nm out/acpm.o | grep -q acpm_dvfs_reassert'
	chk "shipped gpuclk.o has no MMIO accessor" sh -c '! nm out/gpuclk.o | grep -q "try_write32\|try_read32"'
fi
gcc -O1 -Wall -Wextra -Werror -I. -o out/udump_host_test udump.c udump_host_test.c && ./out/udump_host_test
# the debounce under test is the header shipped in xnu patch 0087 (new-file hunk: drop the leading "+")
awk '/^\+\+\+ b\/bsd\/dev\/sweet_keys_gs101_debounce.h$/{f=1;next} f&&/^diff --git/{exit} f&&/^@@/{next} f{print substr($0,2)}' ../xnu/patches/0087-sweet-gs101-keys.patch > out/sweet_keys_gs101_debounce.h
gcc -O1 -Wall -Wextra -Werror -I. -o out/keys_gs101_host_test keys_gs101_host_test.c && ./out/keys_gs101_host_test
# pure FTM5 protocol header of the Pixel 6a touch driver: extracted from xnu patch 0088 so the test sees the kernel's bytes
awk '$0=="+++ b/bsd/dev/sweet_ftm5.h"{on=1;next} on&&/^--- /{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0088-sweet-gs101-touch.patch > out/sweet_ftm5.h
gcc -O1 -Wall -Wextra -Werror -Iout -o out/ftm5_host_test ftm5_host_test.c && ./out/ftm5_host_test
# runtime MMIO poke parser of the same driver (xnu patch 0093, new-file hunk)
awk '$0=="+++ b/bsd/dev/sweet_touch_poke.h"{on=1;next} on&&/^diff --git/{exit} on&&/^@@/{next} on&&/^\+/{print substr($0,2)}' ../xnu/patches/0093-sweet-touch-poke.patch > out/sweet_touch_poke.h
gcc -O1 -Wall -Wextra -Werror -Iout -o out/touchpoke_host_test touchpoke_host_test.c && ./out/touchpoke_host_test
if [ "$gs101" = 1 ]; then
	"$PYTHON" ../tools/test-bluejay-boot.py out/shim-boot.img
	"$PYTHON" ../tools/test-bluejay-boot.py out/shim-boot-lz4.img
fi
# provenance sidecar for the image fastboot will boot (tools/fboot.sh refuses images without a matching one)
if [ -f out/shim-boot-avb.img ]; then
	if [ "$boot_xnu" = 1 ]; then kind=xnu; elif [ "$udump_mib" != 0 ]; then kind=udump; else kind=probe; fi
	"$PYTHON" ../tools/imgmeta.py write out/shim-boot-avb.img --kind "$kind" --args "${META_ARGS:-}"
fi
ls -l out/Image out/shim-boot*.img
