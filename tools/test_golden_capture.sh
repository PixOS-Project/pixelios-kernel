#!/bin/bash
# Host test of tools/fboot-android and tools/golden-pull with a fake fastboot/adb on PATH (never touches a phone; seconds).
# Run: bash tools/test_golden_capture.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
mkdir -p "$w/bin"; export LOG=$w/calls.log PHONE_LOCK_DIR=$w/lock HOME=$w/home; mkdir -p "$HOME"
unset FLASH_STATE   # fboot-android --erase-userdata appends its `erased` marker to $FLASH_STATE: never the caller's real file
cat > "$w/bin/fastboot" <<'F'
#!/bin/bash
echo "fastboot $*" >> "$LOG"
if [ "$1" = devices ]; then printf '%b' "${FAKE_DEVS-FAKESN123\tfastboot\n}"; exit 0; fi
if [ "$1 $2" = "-s FAKESN123" ] || { [ -n "${FAKE_ANYSN-}" ] && [ "$1" = -s ]; }; then shift 2; else echo "NOSERIAL" >> "$LOG"; exit 1; fi
case "$1 $2" in
	"getvar product") echo "product: ${FAKE_PRODUCT-bluejay}" >&2 ;;
	"getvar unlocked") echo "unlocked: ${FAKE_UNLOCKED-yes}" >&2 ;;
	"getvar current-slot") echo "current-slot: ${FAKE_SLOT-b}" >&2 ;;
	"getvar version-bootloader") echo "version-bootloader: fake-1.0" >&2 ;;
	"getvar slot-successful:b") echo "slot-successful:b: yes" >&2 ;;
	"getvar is-userspace") echo "is-userspace: ${FAKE_USERSPACE-no}" >&2 ;;
esac
exit 0
F
cat > "$w/bin/adb" <<'F'
#!/bin/bash
echo "adb $*" >> "$LOG"; echo "env ${ANDROID_SERIAL-unset}" >> "$LOG.env"
if [ "$1" = push ] && [ -n "${FAKE_PUSHFAIL-}" ]; then exit 1; fi
if [ "$1" = get-serialno ]; then echo "${FAKE_SN-FAKESN123}"; exit 0; fi
if [ "$1 $2" = "shell dumpsys" ] && [ "$3" = window ]; then if [ -n "${FAKE_WIN2-}" ] && [ "$(grep -c 'shell dumpsys window' "$LOG")" -ge 2 ]; then printf '%s\n' "$FAKE_WIN2"; else printf '%s\n' "${FAKE_WIN-$WIN_OK}"; fi; exit 0; fi
if { [ "$1 $2" = "shell input" ] || [ "$1 $2" = "shell am" ]; } && [ -n "${FAKE_INPUTRC-}" ]; then exit "$FAKE_INPUTRC"; fi
if [ "$1 $2 $3" = "shell uiautomator dump" ]; then [ -n "${FAKE_DUMPRC2-}" ] && [ "$(grep -c "shell uiautomator dump" "$LOG")" -ge 2 ] && exit "$FAKE_DUMPRC2"; exit "${FAKE_DUMPRC-0}"; fi
if [ "$1 $2" = "shell rm" ]; then exit "${FAKE_RMRC-0}"; fi
if [ "$1 $2" = "shell getprop" ] && [ "$3" = persist.sys.locale ]; then echo "${FAKE_LOCALE-en-US}"; exit 0; fi
if [ "$1 $2" = "shell getprop" ] && [ "$3" = ro.product.locale ]; then echo "${FAKE_PLOCALE-en-US}"; exit 0; fi
if [ "$1 $2" = "exec-out cat" ] && [ "$3" = /data/local/tmp/ui.xml ]; then n=$(grep -c 'exec-out cat /data/local/tmp/ui.xml' "$LOG"); if [ -n "${FAKE_UI3-}" ] && [ "$n" -ge 3 ]; then printf '%s' "$FAKE_UI3"; elif [ -n "${FAKE_UI2-}" ] && [ "$n" -ge 2 ]; then printf '%s' "$FAKE_UI2"; else printf '%s' "${FAKE_UI-$UI_OK}"; fi; exit 0; fi
if [ "$1 $2" = "exec-out screencap" ]; then echo PNG; exit 0; fi
if [ "$1" = exec-out ] && [[ "$4" == *btsnoop_hci_vnd.log* ]]; then [ -n "${FAKE_BT_VNDEMPTY-}" ] || printf '%b' "${FAKE_BT_VNDMAGIC-btsnoop\\0\\0\\0\\0\\1\\0\\0\\3\\352}VNDDATA"; printf '\n__GP_RC=%s\n' "${FAKE_BT_VNDRC-0}"; exit 0; fi
if [ "$1" = exec-out ] && [[ "$4" == *btsnoop_hci.log* ]]; then [ -n "${FAKE_BT_EMPTYPULL-}" ] || printf '%b' "${FAKE_BT_MAGIC-btsnoop\\0\\0\\0\\0\\1\\0\\0\\3\\352}HCIDATA"; printf '\n__GP_RC=0\n'; exit 0; fi
if [ "$1" = exec-out ] && [ -n "${FAKE_BT_LOGS-}" ] && [[ "$4" == "dmesg;"* || "$4" == "logcat -d -b all;"* ]]; then printf '%s\n' "[ 1.0] Bluetooth: hci0 addr AA:BB:CC:DD:EE:FF up" "[ 1.1] bt seri${NOX-}al=SECRET123 bluetooth" "[ 1.2] bcm bare aabbccddeeff and androidboot.seri${NOX-}alno=SECRET123 bt" "[ 1.3] uart line ok"; printf '\n__GP_RC=0\n'; exit 0; fi
if [ "$1 $2 $3" = "exec-out su -c" ] && [[ "$4" == "cat /proc/cmdline;"* ]]; then if [ -n "${FAKE_SU_DENY-}" ]; then echo "Permission denied"; exit 0; fi; echo "${FAKE_CMDLINE-console=x trace_event=rwmmio:rwmmio_write seri${NOX-}al=SECRET123}"; printf '\n__GP_RC=0\n'; exit 0; fi
if [ "$1" = exec-out ]; then [ -n "${FAKE_SU_DENY-}" ] && { echo "Permission denied"; exit 0; }; [ -n "${FAKE_EMPTY-}" ] && { printf '\n__GP_RC=0\n'; exit 0; }; case "$4" in *"-cf - "*) cat "${FAKE_TAR:-$TARDIR/ok.tar}"; printf '\n__GP_RC=0\n'; exit 0 ;; *end-of-tar-stderr*) [ -n "${FAKE_TARERR-}" ] && echo "tar: x: Permission denied"; echo end-of-tar-stderr; printf '\n__GP_RC=0\n'; exit 0 ;; "ls -l /data/adb/magisk/busybox"*) printf '%s\n' "${FAKE_LSBB--rwxr-xr-x 1 root root 1234 2026-10-28 00:00 /data/adb/magisk/busybox}"; printf '\n__GP_RC=0\n'; exit 0 ;; "ls -d /data/magisk"*) printf '%s\n' "${FAKE_LSDM-ls: /data/magisk: No such file or directory}"; printf '\n__GP_RC=0\n'; exit 0 ;; "magisk --sqlite"*) printf '%s\r' "${FAKE_POLICY-uid=2000|policy=2|until=0|logging=0|notification=0}"; printf '\n__GP_RC=0\n'; exit 0 ;; "id;"*) echo "${FAKE_ID-uid=0(root) gid=0(root)}"; printf '\n__GP_RC=0\n'; exit 0 ;; "getprop ro.boot.slot_suffix;"*) printf '%b' "${FAKE_SLOTBUILD-_b\r\nBP31.250610.004\r\n}"; printf '\n__GP_RC=0\n'; exit 0 ;; "cat /dev/block/by-name/vendor_boot_b;"*) n=$(cat "$TARDIR/nvread" 2>/dev/null || echo 0); echo $((n + 1)) > "$TARDIR/nvread"; cat "$TARDIR/vboot.img"; [ -z "${FAKE_VBDIFF-}" ] || [ "$n" = 0 ] || echo x; printf '\n__GP_RC=0\n'; exit 0 ;; "cat /dev/block/by-name/boot_b;"*) n=$(cat "$TARDIR/nread" 2>/dev/null || echo 0); echo $((n + 1)) > "$TARDIR/nread"; cat "$TARDIR/boot.img"; [ -z "${FAKE_BOOTDIFF-}" ] || [ "$n" = 0 ] || echo x; printf '\n__GP_RC=0\n'; exit 0 ;; "cd /data/local/tmp/gp-magisk &&"*) printf '%b' "${FAKE_PATCHLOG-- Unpacking boot image\n- Repacking boot image\n}"; printf '\n__GP_RC=0\n'; exit 0 ;; "k=/proc/sys/kernel/kptr_restrict; o=\$(cat \$k); echo 1 > \$k; cat /proc/vmallocinfo;"*) printf '%b' "${FAKE_VMALLOC-0xffffffc00c4b0000-0xffffffc00c4b2000    8192 exynos_pcie_rc_probe+0x1/0x2 phys=0x14520000 ioremap\nkptr_restrict-restored=2 was=2\n}"; printf '\n__GP_RC=0\n'; exit 0 ;; "cat /data/local/tmp/gp-magisk/new-boot.img;"*) cat "$TARDIR/new-boot.img"; printf '\n__GP_RC=0\n'; exit 0 ;; esac; if [ -n "${FAKE_TEXT-}" ]; then cat "$FAKE_TEXT"; else echo "fake output seri${NOX-}al=SECRET123 line"; fi; printf '\n__GP_RC=0\n'; exit 0; fi
if [ "$1 $2" = "shell su" ]; then if [ -n "${FAKE_SUSH_EMPTY-}" ]; then cat >> "$LOG"; exit 0; fi; in=$(cat); printf '%s\n' "$in" >> "$LOG"; case "$in" in *"echo flags=ok"*) [ -n "${FAKE_PS_NOFLAGS-}" ] || echo flags=ok ;; *"echo suw=ok"*) [ -n "${FAKE_PS_NOSUW-}" ] || echo suw=ok ;; *"echo lock=ok"*) [ -n "${FAKE_PS_NOLOCK-}" ] || echo lock=ok ;; *"echo keys=ok"*) [ -n "${FAKE_PS_NOKEYS-}" ] || echo keys=ok ;; *"settings get global device_provisioned"*) echo 1; echo "${FAKE_PS_USC-1}"; [ -n "${FAKE_PS_NOPKG-}" ] || echo package:com.google.android.setupwizard; echo "${FAKE_PS_LOCKDIS-true}"; echo "  mCurrentFocus=Window{1a2b u0 ${FAKE_PS_FOCUS-com.google.android.apps.nexuslauncher/com.google.android.apps.nexuslauncher.NexusLauncherActivity}}"; echo "    showing=${FAKE_PS_SHOWING-false}"; echo "    secure=false" ;; *"grep -m1 mCurrentFocus"*) echo "  mCurrentFocus=Window{1a2b u0 ${FAKE_PS_FOCUS2-${FAKE_PS_FOCUS-com.google.android.apps.nexuslauncher/x}}}"; echo "    showing=${FAKE_PS_SHOWING2-${FAKE_PS_SHOWING-false}}" ;; *"echo "*"=ok"*) printf '%s\n' "$in" | grep -o 'echo [a-z_]*=ok' | sed 's/^echo //' | grep -vx "${FAKE_BT_MISS:-none}=ok"; case "$in" in *"ls /vendor/etc/bluetooth/*.conf"*) echo /vendor/etc/bluetooth/bt_vendor.conf ;; *"stat -c"*) if [ "${FAKE_BT_SNOOPSZ-4096}" = missing ]; then echo "missing /data/misc/bluetooth/logs/btsnoop_hci.log"; else echo "${FAKE_BT_SNOOPSZ-4096} /data/misc/bluetooth/logs/btsnoop_hci.log"; fi; if [ -n "${FAKE_BT_NOLAST-}" ]; then echo "missing /data/misc/bluetooth/logs/btsnoop_hci.log.last"; else echo "100 /data/misc/bluetooth/logs/btsnoop_hci.log.last"; fi; if [ -n "${FAKE_BT_NOVND-}" ]; then echo "missing /data/vendor/bluetooth/btsnoop_hci_vnd.log"; else echo "${FAKE_BT_VNDSZ-200} /data/vendor/bluetooth/btsnoop_hci_vnd.log"; fi ;; *"MISSING clk"*) [ -z "${FAKE_BT_CLKMISS-}" ] || echo "MISSING clk MUX_APM_FUNC"; [ -z "${FAKE_BT_CLKUNREAD-}" ] || echo "UNREADABLE DOUT_CLK_APM_USI1_UART clk_phase" ;; *"getprop | grep -iE \"bt|bluetooth"*) echo "[ro.boot.btmacaddr]: [${FAKE_BT_ADDR-12:34:56:78:9A:BC}]"; echo "[persist.vendor.bdaddr]: [1234567890ab]"; echo "[persist.bluetooth.btsnooplogmode]: [full]" ;; *"cat /vendor/etc/bluetooth/"*) echo "# conf addr ${FAKE_BT_ADDR-12:34:56:78:9A:BC}" ;; *"INFO: missing or unreadable"*) pi=${FAKE_BT_PININFO-0}; while [ "$pi" -gt 0 ]; do echo "INFO: missing or unreadable /sys/kernel/debug/pinctrl/x$pi/pins"; pi=$((pi - 1)); done ;; *"sha256sum"*) echo "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  /vendor/firmware/BCM4389.hcd" ;; esac ;; *"FROM policies WHERE uid=2000"*) [ -z "${FAKE_POLICY-uid}" ] || printf '%s\r\n' "${FAKE_POLICY-uid=2000|policy=2|until=0|logging=0|notification=0}" ;; *"logcat -d -s Magisk"*) [ -n "${FAKE_NOLOG-}" ] || printf '%s\n' "$in" ;; *) printf '%s\n' "$in" ;; esac; [ -z "${FAKE_SU_DENY-}" ] || exit 1; [ -z "${FAKE_SUSH_ERR-}" ] || echo "magisk: not found"; [ -z "${FAKE_SUSH_CR-}" ] || printf 'one=1\r\n'; [ -z "${FAKE_SUSH_STDERR-}" ] || echo "$FAKE_SUSH_STDERR" >&2; printf '__GP_RC=%s\n' "${FAKE_SUSH_RC-0}"; exit 0; fi
if [ "$1 $2" = "shell sha256sum" ]; then printf '%s  %s\r\n' "${FAKE_BBSHA-}" "$3"; exit 0; fi
if [ "$1 $2" = "shell i=0;" ] || [ "${2:0:4}" = "i=0;" ]; then printf '%s\r\n' "${FAKE_DONE-ok}"; exit 0; fi
if [ "$1 $2 $3" = "shell getprop ro.product.device" ]; then printf '%s\r\n' "${FAKE_DEV-bluejay}"; exit 0; fi
if [ "$1 $2 $3" = "shell cat /proc/cmdline" ] && [ -n "${FAKE_CMDLINE_PERM-}" ]; then echo "cat: /proc/cmdline: Permission denied" >&2; exit 1; fi
if [ "$1 $2 $3" = "shell cat /proc/cmdline" ]; then echo "${FAKE_CMDLINE-console=x trace_event=rwmmio:rwmmio_write seri${NOX-}al=SECRET123}"; exit 0; fi
echo "fake output seri${NOX-}al=SECRET123 line"
F
chmod +x "$w/bin/fastboot" "$w/bin/adb"; export PATH=$w/bin:$PATH
export TARDIR=$w/tars; mkdir -p "$TARDIR/t/base" "$TARDIR/s/base"; head -c 3000 /dev/urandom > "$TARDIR/t/base/model"; printf 'SN' > "$TARDIR/s/base/serial-number"
tar -C "$TARDIR/t" -cf "$TARDIR/ok.tar" base; tar -C "$TARDIR/s" -cf "$TARDIR/serial.tar" base   # binary payloads for the sentinel strip (r1 F3)
img=$w/img.bin; head -c 4096 /dev/urandom > "$img"; good=$(sha256sum "$img" | cut -d' ' -f1)
fails=0
ck() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1 (got '$3', want '$2')"; fails=$((fails + 1)); fi; }
locked() { tools/phone-lock run --owner t --wait 5 -- "$@"; }
# patched copy: PIN replaced by the temp image hash (the real script has no override option)
mkdir -p "$w/k/tools"; sed "s/^PIN=[0-9a-f]*/PIN=$good/; s/^PIN_BP31=[0-9a-zA-Z]*/PIN_BP31=UNSET/" tools/fboot-android > "$w/k/tools/fboot-android"; chmod +x "$w/k/tools/fboot-android"
ln -s "$PWD/tools/phone-lock" "$w/k/tools/phone-lock"
fb() { : > "$LOG"; locked "$w/k/tools/fboot-android" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
fr() { : > "$LOG"; locked tools/fboot-android "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
# fboot-android
ck "pin is the Magisk image sha" 98cbf66be30e4d24eaba123764cc24d9d3b278edd99626a0eef36ff63e207bf0 "$(sed -n 's/^PIN=\([0-9a-f]*\) .*/\1/p' tools/fboot-android)"
# --build (R10 BP31): cp3a unchanged; the $w/k copy keeps the placeholder path covered (PIN_BP31=UNSET), the real script carries the golden-bp31-a pin
ck "PIN_BP31 is the patched BP31 OTA boot sha" eba6a2a1078199fb37a3ea95a56051dbfc8ea5e99a20b6576f838d3d522a4134 "$(sed -n 's/^PIN_BP31=\([0-9a-f]*\) .*/\1/p' tools/fboot-android)"
ck "--build cp3a --dry-run passes and prints build + PIN" 0 "$(fb --build cp3a --dry-run)"; grep -q "^build=cp3a PIN=$good\$" "$w/out"; ck "dry-run prints build=cp3a PIN" 0 $?
ck "default dry-run is build=cp3a" 0 "$(fb --dry-run)"; grep -q "^build=cp3a PIN=$good\$" "$w/out"; ck "default dry-run prints build=cp3a PIN" 0 $?
ck "--build bp31 with the placeholder -> FAIL" 1 "$(fb --build bp31 --image "$img")"; grep -q 'bp31 PIN not set: run golden-pull magisk-patch with GOLDEN_MAGISK_IN first' "$w/out"; ck "bp31 placeholder reason named" 0 $?; grep -q '^fastboot' "$LOG"; ck "bp31 placeholder: no fastboot call" 1 $?
ck "--build bp31 --dry-run with the placeholder -> FAIL" 1 "$(fb --build bp31 --dry-run)"
ck "--build bp31 --dry-run with the REAL script passes and prints the pin" 0 "$(fr --build bp31 --dry-run)"; grep -q '^build=bp31 PIN=eba6a2a1078199fb37a3ea95a56051dbfc8ea5e99a20b6576f838d3d522a4134$' "$w/out"; ck "real bp31 dry-run prints build=bp31 PIN" 0 $?
ck "unknown --build -> ERROR" 2 "$(fb --build zz --dry-run)"
mkdir -p "$w/b31/tools"; sed "s/^PIN_BP31=[A-Za-z0-9]*/PIN_BP31=$good/; s/^PIN=[0-9a-f]*/PIN=0000000000000000000000000000000000000000000000000000000000000000/" tools/fboot-android > "$w/b31/tools/fboot-android"; chmod +x "$w/b31/tools/fboot-android"; ln -s "$PWD/tools/phone-lock" "$w/b31/tools/phone-lock"
b31() { : > "$LOG"; locked "$w/b31/tools/fboot-android" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
ck "--build bp31 with a pinned image passes" 0 "$(b31 --build bp31 --image "$img")"; grep -q '^fastboot -s FAKESN123 boot ' "$LOG"; ck "bp31 pinned: boots" 0 $?
ck "cp3a still checks its own PIN when PIN_BP31 is set" 1 "$(b31 --image "$img")"
ck "good run passes" 0 "$(fb --image "$img")"
grep -q '^fastboot -s FAKESN123 boot --cmdline trace_event=rwmmio:rwmmio_write,rwmmio:rwmmio_post_read trace_buf_size=64M trace_clock=global trace_options=nooverwrite ' "$LOG"; ck "default cmdline used" 0 $?
grep -q 'serial\|getvar all' "$LOG"; ck "no getvar all / serial" 1 $?
grep -q '^fastboot .*erase' "$LOG"; ck "no erase without flags" 1 $?
ck "wrong sha refused (real script)" 1 "$(fr --image "$img")"; grep -q '^fastboot' "$LOG"; ck "wrong sha: no fastboot call" 1 $?
ck "wrong product refused" 1 "$(FAKE_PRODUCT=sweet fb --image "$img")"; grep -q 'fastboot boot' "$LOG"; ck "wrong product: no boot" 1 $?
ck "locked refused" 1 "$(FAKE_UNLOCKED=no fb --image "$img")"; grep -q 'fastboot boot' "$LOG"; ck "locked: no boot" 1 $?
ck "slot a refused" 1 "$(FAKE_SLOT=a fb --image "$img")"
ck "slot a with --slot-ok a passes" 0 "$(FAKE_SLOT=a fb --image "$img" --slot-ok a)"
ck "slot a with --slot-ok b refused" 1 "$(FAKE_SLOT=a fb --image "$img" --slot-ok b)"
ck "no phone lock refused" 1 "$(: > "$LOG"; "$w/k/tools/fboot-android" --image "$img" > "$w/out" 2>&1 </dev/null; echo $?)"; grep -q 'fastboot' "$LOG"; ck "no lock: no fastboot call" 1 $?
ck "--erase-userdata alone refused" 1 "$(fb --image "$img" --erase-userdata)"; grep -q '^fastboot' "$LOG"; ck "erase alone: no fastboot call" 1 $?
ck "--yes alone refused" 1 "$(fb --image "$img" --yes-erase-userdata)"
ck "erase with both flags passes" 0 "$(fb --image "$img" --erase-userdata --yes-erase-userdata)"
ck "exactly one erase, of userdata" "fastboot -s FAKESN123 erase userdata" "$(grep '^fastboot -s [^ ]* erase' "$LOG")"
ck "erase marker appended (AP-179)" erased "$(tail -n 1 "$HOME/pixelios-artifacts/flashed-userdata.txt" | cut -d' ' -f1)"
ck "erase before boot" "erase boot" "$(grep -o '^fastboot -s [^ ]* \(erase\|boot\)' "$LOG" | sed 's/.* //' | tr '\n' ' ' | sed 's/ $//')"
ck "erase refused when slot wrong: no erase" 1 "$(FAKE_SLOT=a fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*erase' "$LOG"; ck "slot wrong: nothing erased" 1 $?
ck "dry-run passes" 0 "$(fb --dry-run --erase-userdata --yes-erase-userdata)"; grep -q 'would run: fastboot -s SERIAL erase userdata' "$w/out"; ck "dry-run prints erase" 0 $?
ck "dry-run: no fastboot call" 0 "$(wc -c < "$LOG")"
ck "unknown arg is ERROR (2)" 2 "$(fb -w)"; grep -q '^fastboot' "$LOG"; ck "-w: no fastboot call" 1 $?
for a in "" "--erase-userdata --yes-erase-userdata" "--dry-run" "--slot-ok a"; do FAKE_SLOT=a fb --image "$img" $a >/dev/null; grep -qE '^fastboot (.* )?-w( |$)|erase (metadata|cache|boot|vbmeta|persist)' "$LOG"; ck "never -w / other erase ($a)" 1 $?; done
ck "--sha256 is rejected (real script)" 2 "$(fr --image "$img" --sha256 "$good")"
ck "trailing --image is ERROR" 2 "$(timeout 5 tools/fboot-android --dry-run --image >/dev/null 2>&1 </dev/null; echo $?)"
ck "bad --cmdline token rejected" 2 "$(fb --image "$img" --cmdline "trace_buf_size=64M init=/bin/sh")"; grep -q '^fastboot -s' "$LOG"; ck "bad cmdline: no fastboot call" 1 $?
ck "two fastboot devices refused" 1 "$(FAKE_DEVS='A1\tfastboot\nB2\tfastboot\n' fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*\(erase\|boot\) ' "$LOG"; ck "two devices: no erase/boot" 1 $?
ck "no fastboot device refused" 1 "$(FAKE_DEVS='' fb --image "$img")"
ck "debian fastboot row (TAB DEL) accepted" 0 "$(FAKE_DEVS='FAKESN123\t \177\n' fb --image "$img")"; grep -q '^fastboot -s FAKESN123 boot ' "$LOG"; ck "debian row: boots that serial" 0 $?
ck "debian row + second device refused" 1 "$(FAKE_DEVS='FAKESN123\t \177\nB2\tfastboot\n' fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*\(erase\|boot\) ' "$LOG"; ck "debian two devices: no erase/boot" 1 $?
ck "debian row + dashed serial refused" 1 "$(FAKE_DEVS='FAKESN123\t \177\nB-2\tfastboot\n' fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*\(erase\|boot\) ' "$LOG"; ck "dashed two devices: no erase/boot" 1 $?
ck "alnum + no-permissions rows refused" 1 "$(FAKE_DEVS='FAKESN123\tfastboot\n????????????\tno permissions\n' fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*\(erase\|boot\) ' "$LOG"; ck "alnum+noperm: no erase/boot" 1 $?
ck "fastbootd refused" 1 "$(FAKE_USERSPACE=yes fb --image "$img" --erase-userdata --yes-erase-userdata)"; grep -q '^fastboot .*\(erase\|boot\) ' "$LOG"; ck "fastbootd: no erase/boot" 1 $?
ck "no-permissions row only refused" 1 "$(FAKE_ANYSN=1 FAKE_DEVS='????????????\tno permissions; see [http://developer.android.com/tools/device.html]\n' fb --image "$img")"; grep -q '^fastboot .*boot ' "$LOG"; ck "no permissions: no boot" 1 $?
fb --image "$img" --erase-userdata --yes-erase-userdata >/dev/null
ck "every erase/boot has -s" 0 "$(grep -E '^fastboot .*(erase|boot) ' "$LOG" | grep -vc '^fastboot -s ')"
grep -q 'slot-successful:b' "$LOG"; ck "slot-successful:b queried" 0 $?
grep -q "current-slot=b" "$HOME/pixelios-artifacts/golden/getvar-before.txt"; ck "getvar-before.txt written" 0 $?
grep -rq FAKESN123 "$w/out"; ck "serial not echoed" 1 $?
grep -q NOSERIAL "$LOG"; ck "every fastboot call after devices has -s SERIAL" 1 $?
grep -qF -- " $img" "$LOG"; ck "boot uses the private copy, not the caller path" 1 $?
ck "newline in --cmdline rejected" 2 "$(fb --image "$img" --cmdline $'trace_clock=global\ninit=/bin/sh')"; grep -q '^fastboot -s' "$LOG"; ck "newline cmdline: no fastboot call" 1 $?
ck "shell metachar in --cmdline rejected" 2 "$(fb --image "$img" --cmdline 'trace_clock=a;b')"
# golden-pull
gp() { : > "$LOG"; : > "$LOG.env"; tools/golden-pull "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
ck "trailing --out is ERROR" 2 "$(timeout 5 tools/golden-pull --dry-run --out >/dev/null 2>&1 </dev/null; echo $?)"
ck "pull: wrong device refused" 1 "$(FAKE_DEV=sweet gp --out "$w/g1" identity)"; grep -q 'su -c' "$LOG"; ck "wrong device: no su call" 1 $?
ck "pull: dir not created on refusal" 1 "$([ -e "$w/g1" ] && echo 0 || echo 1)"
mkdir -p "$w/repo" && git -C "$w/repo" init -q
ck "pull: in-repo outdir refused" 1 "$(gp --out "$w/repo/sub/x" identity)"; ck "in-repo: nothing created" 1 "$([ -e "$w/repo/sub" ] && echo 0 || echo 1)"
ck "pull: in-repo outdir refused (dry-run too)" 1 "$(gp --dry-run --out "$w/repo/x" start)"
ck "pull: identity ok" 0 "$(gp --out "$w/g2" identity)"; ck "SHA256SUMS written" 0 "$([ -s "$w/g2/SHA256SUMS" ] && echo 0 || echo 1)"
grep -rq SECRET123 "$w/g2" "$w/out"; ck "no serial in files/output" 1 $?
ck "pull: mark ok" 0 "$(gp --out "$w/g2" mark R1 "done")"; grep -q "echo 'MARK R1 done' > /sys/kernel/tracing/trace_marker" "$LOG"; ck "mark writes trace_marker" 0 $?
ck "pull: mark rejects quote injection" 2 "$(gp --out "$w/g2" mark "x'; reboot #")"
ck "pull: stop-pull ok" 0 "$(gp --out "$w/g2" stop-pull wifi-on)"; grep -qF "adb exec-out su -c cat /sys/kernel/tracing/trace; r=" "$LOG"; ck "pull uses exec-out su -c CMD" 0 $?; ck "trace file pulled" 0 "$([ -f "$w/g2/trace-wifi-on.txt" ] && echo 0 || echo 1)"
ck "sentinel stripped" "fake output seri${NOX-}al=SECRET123 line" "$(cat "$w/g2/trace-wifi-on.txt")"
ck "inventory: the debugfs-summary skip is its one gap" 1 "$(gp --out "$w/g4" inventory)"; grep -q 'RESULT: FAIL inventory gaps=1 ' "$w/out" 2>/dev/null || gp --out "$w/g4" inventory 2>&1 | grep -q 'GAP: clk_summary and regulator_summary skipped'; ck "inventory: skip is loud" 0 $?
ck "inventory: device tree from the real dir, serial-number + chosen excluded" 0 "$(grep -c 'tar -C /sys/firmware/devicetree -cf - --exclude=base/serial-number --exclude=base/chosen base' "$LOG" | grep -qx 1 && echo 0 || echo 1)"
grep -q '/data/vendor/wifi\|wpa_supplicant\|regmap/[^ ]*/registers' "$LOG"; ck "inventory: never pulls wpa_supplicant / regmap registers" 1 $?
ck "inventory: the only su sh script is the debugfs mount" "mountpoint -q /sys/kernel/debug || mount -t debugfs debugfs /sys/kernel/debug" "$(grep -v '^adb ' "$LOG")"
grep -q "find /config/usb_gadget -type f ! -iname '\*serial\*'" "$LOG"; ck "inventory: usb gadget serial excluded" 0 $?
ck "inventory: interrupts baseline pulled" 0 "$([ -f "$w/g4/interrupts-baseline.txt" ] && echo 0 || echo 1)"
ck "blobs passes" 0 "$(gp --out "$w/g4" blobs)"; grep -q 'tar -C / -cf - vendor_dlkm/lib/modules .* 2>/data/local/tmp/golden-tar.err' "$LOG"; ck "blobs: tar stderr kept out of the stream" 0 $?
ck "blobs: tar stderr text -> FAIL" 1 "$(FAKE_TARERR=1 gp --out "$w/g4" blobs)"; grep -q 'GAP: tar for blobs.tar wrote to stderr' "$w/out"; ck "blobs: tar stderr GAP named" 0 $?
cmp -s "$TARDIR/ok.tar" "$w/g4/blobs.tar"; ck "binary tar byte-exact after the sentinel strip (r1 F3)" 0 $?
ck "tar holding base/serial-number -> FAIL (r1 F4)" 1 "$(FAKE_TAR=$TARDIR/serial.tar gp --out "$w/g4" inventory)"; grep -q 'GAP: device-tree.tar holds base/serial-number' "$w/out"; ck "serial in DT tar named" 0 $?
ck "non-tar payload -> FAIL (r1 F6)" 1 "$(FAKE_TAR=/etc/hostname gp --out "$w/g4" blobs)"; grep -q 'GAP: blobs.tar is not a readable tar' "$w/out"; ck "non-tar GAP named" 0 $?
ck "empty pull -> FAIL (r1 F3)" 1 "$(FAKE_EMPTY=1 gp --out "$w/g5" stop-pull e)"; grep -q "GAP: pull of 'cat /sys/kernel/tracing/trace' is empty" "$w/out"; ck "empty pull GAP named" 0 $?
gp --out "$w/g4" inventory >/dev/null; for c in "cat /sys/kernel/debug/clk/clk_summary;" "cat /sys/kernel/debug/regulator/regulator_summary;"; do grep -qF -- "$c" "$LOG"; ck "inventory never reads '$c' (rebooted the phone 2026-10-27)" 1 $?; done; for c in "logcat -d -b all;" "cat /proc/interrupts;"; do grep -qF -- "$c" "$LOG"; ck "inventory pulls '$c' (r1 F8)" 0 $?; done
ck "interrupts baseline is /proc/interrupts" 0 "$(grep -c 'cat /proc/interrupts; r=' "$LOG" | grep -qx 1 && echo 0 || echo 1)"
grep -qE '/data/(vendor|misc)' "$LOG"; ck "inventory never reads /data/vendor or /data/misc (Wi-Fi config store)" 1 $?
printf 'ls: /sys/kernel/tracing/events/mali: No such file or directory\n' > "$w/errline"; ck "error text inside a loop file -> FAIL (r1 F2)" 1 "$(FAKE_TEXT=$w/errline gp --out "$w/g6" inventory)"; grep -q 'GAP: pinctrl-gpio.txt holds 1 error line' "$w/out"; ck "loop error GAP names the file" 0 $?
# golden-6 fixes (research 105 items 5/2/3): a '#' comment holding an error word is not a GAP; a real error line still is
printf '# cannot mount: stock fstab comment\nsystem /system ext4 ro\n' > "$w/fstcom"; gp --out "$w/g6" inventory >/dev/null; grep -q 'holds .* error line' "$w/out"; ck "inventory: fstab comment with 'cannot' is no GAP" 1 $?
printf '# cannot mount: comment\ncat: /vendor/etc/fstab.x: Permission denied\n' > "$w/fstreal"; FAKE_TEXT=$w/fstreal gp --out "$w/g6" inventory >/dev/null; grep -q 'GAP: pinctrl-gpio.txt holds 1 error line(s) (first: cat: /vendor/etc/fstab.x: Permission denied)' "$w/out"; ck "inventory: real Permission denied line after a comment is a GAP (only the real line counted)" 0 $?
ck "su-policy-diag: empty Magisk log only -> PASS" 0 "$(FAKE_NOLOG=1 gp --out "$w/g21" su-policy-diag)"; grep -q '^INFO: su-policy-diag: su-magisk-log.txt is empty: expected on this release build \[guess' "$w/out"; ck "su-policy-diag: empty Magisk log is a named INFO with [guess] cause" 0 $?; grep -q '^GAP' "$w/out"; ck "su-policy-diag: empty Magisk log: no GAP" 1 $?; grep -q 'RESULT: PASS su-policy-diag gaps=0 .* info=1$' "$w/out"; ck "su-policy-diag: info=1 counted on RESULT" 0 $?
ck "identity: adb stderr saved (su fails)" 0 "$(FAKE_SU_DENY=1 FAKE_CMDLINE_PERM=1 gp --out "$w/g2" identity >/dev/null; [ -f "$w/g2/cmdline-stderr.txt" ] && echo 0 || echo 1)"
gp --out "$w/g4" blobs >/dev/null; grep -qF 'vendor/etc vendor/lib64 vendor/bin/hw 2>' "$LOG"; ck "blobs list includes vendor/etc, lib64, bin/hw (r1 F8)" 0 $?
# salvaged from golden-unattended a5669f9: partition reads are read-only, events-extra, extra inventory
ck "blobs pulls partitions" 0 "$(gp --out "$w/g8" blobs)"
for p in boot init_boot vendor_boot dtbo; do grep -qF "cat /dev/block/by-name/$p\$(getprop ro.boot.slot_suffix)" "$LOG"; ck "$p of the running slot read" 0 $?; done
grep -E '/dev/block' "$LOG" | grep -vE '^cat /dev/block/by-name/(boot|init_boot|vendor_boot|dtbo)\$\(getprop ro.boot.slot_suffix\)' | grep -qvE '^adb |ls -l /dev/block/by-name'; ck "every /dev/block use is a plain cat read or the by-name listing" 1 $?
grep -qE 'of=|>[ ]*/dev/block|dd |tee .*/dev/block' "$LOG"; ck "blobs/inventory never write a block device" 1 $?
ck "events-extra passes" 0 "$(gp --out "$w/g7" events-extra)"
printf 'enabled i2c\nMISSING mali\n' > "$w/missing"; ck "events-extra: missing group -> FAIL" 1 "$(FAKE_TEXT=$w/missing gp --out "$w/g7" events-extra)"; grep -q 'GAP: event groups missing: MISSING mali' "$w/out"; ck "missing group named" 0 $?
gp --out "$w/g8" inventory >/dev/null; grep -qF 'cat /proc/config.gz;' "$LOG"; ck "kernel config pulled" 0 $?
grep -q 'regmap/\*/registers' "$LOG"; ck "regmap registers never read" 1 $?
ck "inventory dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g4" inventory >/dev/null; wc -c < "$LOG")"
ck "pull: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g2" stop-pull deny)"; grep -q 'gaps=7' "$w/out"; ck "su denied: S + 4 R gaps + kptr-restore + no-kernel-VA GAPs counted (trace, vmallocinfo, interrupts, tracing-stats)" 0 $?
ck "start: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g2" start)"
ck "identity without rwmmio, default (method B) -> PASS, no GAP" 0 "$(FAKE_CMDLINE=console=x gp --out "$w/g4" identity)"; grep -q 'GAP' "$w/out"; ck "default: no GAP for the missing trace_event" 1 $?; grep -q 'RESULT: PASS identity gaps=0' "$w/out"; ck "default: RESULT PASS gaps=0" 0 $?
ck "identity --method-a without rwmmio -> FAIL" 1 "$(FAKE_CMDLINE=console=x gp --method-a --out "$w/g4" identity)"; grep -q 'GAP: /proc/cmdline has no trace_event=rwmmio' "$w/out"; ck "--method-a: rwmmio GAP printed" 0 $?
ck "identity --method-a with rwmmio -> PASS" 0 "$(gp --method-a --out "$w/g4" identity)"
gp --out "$w/g4" identity >/dev/null; grep -q '^adb exec-out su -c cat /proc/cmdline; r=\$?; printf' "$LOG"; ck "identity reads /proc/cmdline through su (exec-out su -c form)" 0 $?; grep -q '^adb shell cat /proc/cmdline' "$LOG"; ck "identity: su ok -> no plain shell read" 1 $?
grep -q 'trace_event=rwmmio' "$w/g4/cmdline.txt"; ck "cmdline.txt holds the su-read line" 0 $?; grep -q 'SECRET123' "$w/g4/cmdline.txt"; ck "cmdline.txt: serial token filtered" 1 $?
ck "identity dry-run: su form printed" 0 "$(gp --dry-run --out "$w/g4" identity >/dev/null; grep -c 'would run: adb exec-out su -c "cat /proc/cmdline; ' "$w/out" | grep -qx 1; echo $?)"; ck "identity dry-run: method A skipped by default" 0 "$(grep -c 'would check: cmdline.txt has trace_event=rwmmio' "$w/out")"
gp --dry-run --method-a --out "$w/g4" identity >/dev/null; grep -q 'would check: cmdline.txt has trace_event=rwmmio' "$w/out"; ck "identity dry-run --method-a: check present" 0 $?
ck "identity: su fails + shell denied -> FAIL gaps=1" 1 "$(FAKE_SU_DENY=1 FAKE_CMDLINE_PERM=1 gp --out "$w/g5" identity)"; grep -q 'RESULT: FAIL identity gaps=1 ' "$w/out"; ck "identity su+shell denied: exactly 1 GAP" 0 $?; grep -c '^GAP:' "$w/out" | grep -qx 1; ck "identity su+shell denied: one GAP line" 0 $?; grep -q 'GAP: reading /proc/cmdline failed.*Permission denied' "$w/out"; ck "that GAP names the shell denial" 0 $?; grep -q 'Permission denied' "$w/g5/cmdline-stderr.txt"; ck "cmdline-stderr.txt holds the denial" 0 $?
FAKE_SU_DENY=1 gp --out "$w/g5" identity >/dev/null; grep -q 'INFO: identity: su could not read' "$w/out"; ck "identity: su fails, shell reads -> INFO, no GAP" 0 $?; grep -q '^GAP' "$w/out"; ck "identity su fallback: no GAP" 1 $?
ck "pull: kprobes ok" 0 "$(gp --out "$w/g2" kprobes)"; grep -q 'pci_bus_write_config_byte' "$LOG"; ck "kprobes written" 0 $?
ck "pull: dry-run no adb call" 0 "$(gp --dry-run --out "$w/g3" boot-capture >/dev/null; wc -c < "$LOG")"; grep -q 'cat \$d/resource; done" > .*/pci-resource.txt' "$w/out"; ck "boot-capture pulls pci-resource" 0 $?; ck "dry-run creates no dir" 1 "$([ -e "$w/g3" ] && echo 0 || echo 1)"
ck "pull: unknown subcommand 2" 2 "$(gp bogus)"
ck "pull: su-check ok" 0 "$(gp --out "$w/g2" su-check)"; grep -q "uid=0(root)" "$w/g2/su-id.txt"; ck "su-id.txt written" 0 $?
ck "pull: su-check denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g2" su-check)"
ck "pull: su-check non-root -> FAIL" 1 "$(FAKE_ID="uid=2000(shell)" gp --out "$w/g2" su-check)"
ck "su-check: policy row printed with CR passes (no GAP)" 0 "$(gp --out "$w/g2" su-check)"; grep -q GAP "$w/out"; ck "su-check ok: no GAP" 1 $?
grep -qF 'magisk --sqlite "SELECT uid,policy,until,logging,notification FROM policies WHERE uid=2000"; echo __GP_RC=$?' "$LOG"; ck "su-check: policy row read by the exact stdin script" 0 $?
grep -qF -- "su -c sh" <(grep -a 'adb shell su' "$LOG" | tail -1) || true
ck "su-check: policy readback is not an exec-out call" 0 "$(grep -a 'exec-out' "$LOG" | grep -c 'magisk --sqlite')"
ck "su-check: busybox missing -> FAIL" 1 "$(FAKE_LSBB='ls: /data/adb/magisk/busybox: No such file or directory' gp --out "$w/g2" su-check)"; grep -q 'GAP: su-check: /data/adb/magisk/busybox not present' "$w/out"; ck "su-check: busybox GAP named" 0 $?
ck "su-check: /data/magisk still present -> FAIL" 1 "$(FAKE_LSDM=/data/magisk gp --out "$w/g2" su-check)"; grep -q 'GAP: su-check: /data/magisk still present' "$w/out"; ck "su-check: /data/magisk GAP named" 0 $?
ck "su-check: policy 1 -> FAIL" 1 "$(FAKE_POLICY='uid=2000|policy=1|until=0|logging=0|notification=0' gp --out "$w/g2" su-check)"; grep -q 'GAP: su-check: policies row' "$w/out"; ck "su-check: policy GAP named" 0 $?
ck "su-check: empty policy output -> FAIL" 1 "$(FAKE_POLICY='' gp --out "$w/g2" su-check)"; grep -q 'GAP: su-check: su-policy.txt is empty' "$w/out"; ck "su-check: empty policy -> empty GAP named" 0 $?
ck "su-check: policy rc!=0 -> FAIL" 1 "$(FAKE_SUSH_RC=3 gp --out "$w/g2" su-check)"; grep -q 'GAP: su-check: su-policy.txt command did not exit 0' "$w/out"; ck "su-check: policy rc GAP named" 0 $?
ck "su-check: policy file has no CR" 0 "$(gp --out "$w/g2" su-check >/dev/null; grep -c $'\r' "$w/g2/su-policy.txt")"
gp --out "$w/g2" su-check >/dev/null; grep -qiE 'insert|update|delete|drop|create|alter|replace|pragma' <(grep -a 'FROM policies' "$LOG"); ck "su-check: policy script SELECT-only (no mutating SQL)" 1 $?
ck "su-check dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g2" su-check >/dev/null; wc -c < "$LOG")"
gp --dry-run --out "$w/g3" boot-capture >/dev/null; grep -q "tracing-state-coldboot.txt" "$w/out"; ck "boot-capture saves tracing state" 0 $?
ck "fboot: --identity-only passes" 0 "$(fb --image "$img" --identity-only)"; grep -qE "^fastboot .*(erase|boot) " "$LOG"; ck "identity-only: no erase/boot" 1 $?
ck "fboot: --identity-only with erase refused" 1 "$(fb --image "$img" --identity-only --erase-userdata --yes-erase-userdata)"
# wifi-load / wifi-probe (review golden-wifi-load-r1 F4)
ck "wifi-load takes no args" 2 "$(gp --out "$w/g9" wifi-load x)"; grep -q '^adb' "$LOG"; ck "wifi-load arg: no adb call" 1 $?
ck "wifi-load dry-run passes" 0 "$(gp --dry-run --out "$w/g9" wifi-load)"; grep -q 'modprobe -a -d /vendor/lib/modules bcmdhd4389.ko 2>&1' "$w/out"; ck "wifi-load runs the stock line" 0 $?
printf 'modprobe-rc=0\ncfg80211 1 0\nbcmdhd4389 1 0\n' > "$w/wl-ok.txt"; printf 'modprobe-rc=1\ncfg80211 1 0\nbcmdhd4389 1 0\n' > "$w/wl-rc1.txt"
ck "wifi-load PASS when both loaded and rc 0" 0 "$(FAKE_TEXT=$w/wl-ok.txt gp --out "$w/g9" wifi-load)"
ck "wifi-load FAIL on modprobe rc 1 (F3)" 1 "$(FAKE_TEXT=$w/wl-rc1.txt gp --out "$w/g9" wifi-load)"
ck "wifi-load FAIL when nothing loaded" 1 "$(gp --out "$w/g9" wifi-load)"
ck "wifi-probe dry-run pulls the blocklist" 0 "$(gp --dry-run --out "$w/g9" wifi-probe)"; grep -q 'modules.blocklist' "$w/out"; ck "wifi-probe: blocklist read" 0 $?
# wifi-probe pstore pull: an EMPTY /sys/fs/pstore left the glob literal, `tail` on it failed and the loop's last status (__GP_RC=1) became a GAP on golden-bp31-b run2 (T-36da)
ps_cmd=$(sed -n 's/^[[:space:]]*R "\(ls -la \/sys\/fs\/pstore;.*\)" pstore\.txt$/\1/p' tools/golden-pull | sed 's/\\"/"/g; s/\\\$/$/g')
ck "wifi-probe: pstore command found in golden-pull" 1 "${ps_cmd:+1}"
pst=$(mktemp -d "${TMPDIR:-/tmp}/pst.XXXXXX"); mkdir "$pst/empty" "$pst/full"; echo rec > "$pst/full/dmesg-ramoops-0"
bash -c "${ps_cmd//\/sys\/fs\/pstore/$pst/empty}" >"$pst/o1" 2>&1; ck "wifi-probe pstore: empty dir -> rc 0" 0 $?
grep -q '^== (pstore empty' "$pst/o1"; ck "wifi-probe pstore: empty dir -> named marker line" 0 $?
bash -c "${ps_cmd//\/sys\/fs\/pstore/$pst/full}" >"$pst/o2" 2>&1; ck "wifi-probe pstore: one record -> rc 0" 0 $?
grep -qx 'rec' "$pst/o2"; ck "wifi-probe pstore: record content pulled" 0 $?
rm -rf "$pst"
# boot-read / magisk-patch (driver 5, 2026-10-27)
{ printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$TARDIR/boot.img"; { printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$TARDIR/new-boot.img"
{ printf 'VNDRBOOT'; head -c 4096 /dev/urandom; } > "$TARDIR/vboot.img"
ck "boot-read takes no args" 2 "$(gp --out "$w/g10" boot-read x)"
rm -f "$TARDIR/nread"; ck "boot-read PASS" 0 "$(gp --out "$w/g10" boot-read)"; cmp -s "$TARDIR/boot.img" "$w/g10/boot_b.img"; ck "boot-read: image byte-exact" 0 $?
ck "boot-read: reread file removed" 1 "$([ -e "$w/g10/boot_b-reread.img" ] && echo 0 || echo 1)"
ck "boot-read: reads boot_b twice" 2 "$(grep -c 'exec-out su -c cat /dev/block/by-name/boot_b;' "$LOG")"
grep -E '^adb (push|shell (rm|dd)|exec-out su -c (dd|.*>))' "$LOG" | grep -q .; ck "boot-read: no push, rm, dd or redirect on the phone" 1 $?
rm -f "$TARDIR/nread"; ck "boot-read: reads differ -> FAIL" 1 "$(FAKE_BOOTDIFF=1 gp --out "$w/g11" boot-read)"; grep -q 'GAP: boot-read: the two reads of boot_b differ' "$w/out"; ck "boot-read: differ GAP named" 0 $?
ck "boot-read: slot _a -> REFUSED" 1 "$(FAKE_SLOTBUILD='_a\nBP31.250610.004\n' gp --out "$w/g11" boot-read)"; grep -q 'cat /dev/block' "$LOG"; ck "boot-read: slot _a reads no partition" 1 $?
rm -f "$TARDIR/nread" "$TARDIR/nvread"; gp --out "$w/g10" boot-read >/dev/null; cmp -s "$TARDIR/vboot.img" "$w/g10/vendor_boot_b.img"; ck "boot-read: vendor_boot_b byte-exact" 0 $?
ck "boot-read: reads vendor_boot_b twice" 2 "$(grep -c 'exec-out su -c cat /dev/block/by-name/vendor_boot_b;' "$LOG")"
ck "boot-read: vendor reread file removed" 1 "$([ -e "$w/g10/vendor_boot_b-reread.img" ] && echo 0 || echo 1)"
grep -E '^adb (push|shell (rm|dd)|exec-out su -c (dd|.*>))' "$LOG" | grep -q .; ck "boot-read: vendor read is cat only (no push, rm, dd, redirect)" 1 $?
rm -f "$TARDIR/nread" "$TARDIR/nvread"; ck "boot-read: vendor reads differ -> FAIL" 1 "$(FAKE_VBDIFF=1 gp --out "$w/g11" boot-read)"; grep -q 'GAP: boot-read: the two reads of vendor_boot_b differ' "$w/out"; ck "boot-read: vendor differ GAP named" 0 $?
{ printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$TARDIR/vboot.img"; rm -f "$TARDIR/nread" "$TARDIR/nvread"; ck "boot-read: vendor_boot_b no VNDRBOOT magic -> FAIL" 1 "$(gp --out "$w/g11" boot-read)"; grep -q 'GAP: boot-read: vendor_boot_b.img has no VNDRBOOT magic' "$w/out"; ck "boot-read: vendor magic GAP named" 0 $?
{ printf 'VNDRBOOT'; head -c 4096 /dev/urandom; } > "$TARDIR/vboot.img"
ck "boot-read: other build -> REFUSED" 1 "$(FAKE_SLOTBUILD='_b\nCP3A.260905.009\n' gp --out "$w/g11" boot-read)"
ck "magisk-patch without boot_b.img -> REFUSED" 1 "$(gp --out "$w/g12" magisk-patch)"; grep -q '^adb push' "$LOG"; ck "magisk-patch refused: no push" 1 $?
mkdir -p "$HOME/pixelios-artifacts/golden/magisk" "$w/apk/assets" "$w/apk/lib/arm64-v8a"; for f in boot_patch.sh util_functions.sh stub.apk; do echo "$f" > "$w/apk/assets/$f"; done; for f in magiskboot magiskinit magisk init-ld; do echo "$f" > "$w/apk/lib/arm64-v8a/lib$f.so"; done
(cd "$w/apk" && python3 -I -m zipfile -c "$HOME/pixelios-artifacts/golden/magisk/Magisk-v30.7.apk" assets lib)
ck "magisk-patch PASS" 0 "$(gp --out "$w/g10" magisk-patch)"; cmp -s "$TARDIR/new-boot.img" "$w/g10/boot_b-magisk.img"; ck "magisk-patch: image byte-exact" 0 $?
grep -q '^adb push .*/mp /data/local/tmp/gp-magisk$' "$LOG"; ck "magisk-patch: pushes only into /data/local/tmp/gp-magisk" 0 $?
grep -q 'KEEPVERITY=true KEEPFORCEENCRYPT=true PATCHVBMETAFLAG=false sh ./boot_patch.sh /data/local/tmp/gp-magisk/boot.img' "$LOG"; ck "magisk-patch: runs boot_patch.sh on the file copy" 0 $?
grep -E '/dev/block|flash|dd if|blockdev' "$LOG" | grep -q .; ck "magisk-patch: never names a block device or flash" 1 $?
ck "magisk-patch: abort line -> FAIL" 1 "$(FAKE_PATCHLOG='- Unpacking boot image\n! Unable to repack boot image\n' gp --out "$w/g10" magisk-patch)"; grep -q 'GAP: magisk-patch: boot_patch.sh did not finish cleanly' "$w/out"; ck "magisk-patch: abort GAP named" 0 $?
ck "magisk-patch dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g13" magisk-patch >/dev/null; wc -c < "$LOG")"
# wifi-load-gki (driver 5, 2026-10-27): the real pin refuses a fake ko; a pin-patched copy runs against the fake adb
mkdir -p "$HOME/pixelios-artifacts/golden/cp3a"; echo fake-ko > "$HOME/pixelios-artifacts/golden/cp3a/rfkill.ko"
ck "wifi-load-gki: ko sha not the pin -> REFUSED" 1 "$(gp --out "$w/g14" wifi-load-gki)"; grep -q '^adb push' "$LOG"; ck "wifi-load-gki refused: no push" 1 $?
ck "wifi-load-gki: pin is the cp3a GKI rfkill.ko" 907d2bac61b01dc8092700bdc7082c5005e54803d74b5f1c51eba54480a5165a "$(sed -n 's/.*RFPIN=\([0-9a-f]*\).*/\1/p' tools/golden-pull)"
sed "s/RFPIN=[0-9a-f]*/RFPIN=$(sha256sum "$HOME/pixelios-artifacts/golden/cp3a/rfkill.ko" | cut -d' ' -f1)/" tools/golden-pull > "$w/k/tools/golden-pull"; chmod +x "$w/k/tools/golden-pull"
gpk() { : > "$LOG"; "$w/k/tools/golden-pull" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
printf 'insmod-rc=0\nmodprobe-rc=0\nrfkill 1 0\ncfg80211 1 0\nbcmdhd4389 1 0\n' > "$w/wg-ok.txt"; printf 'insmod-rc=1\nmodprobe-rc=0\ncfg80211 1 0\nbcmdhd4389 1 0\n' > "$w/wg-rc1.txt"
ck "wifi-load-gki PASS" 0 "$(FAKE_TEXT=$w/wg-ok.txt gpk --out "$w/g14" wifi-load-gki)"
grep -q '^adb push .*/cp3a/rfkill.ko /data/local/tmp/gp-rfkill.ko$' "$LOG"; ck "wifi-load-gki: pushes only /data/local/tmp/gp-rfkill.ko" 0 $?
grep -q "insmod /data/local/tmp/gp-rfkill.ko 2>&1; echo insmod-rc=\$?; .*then modprobe -a -d /vendor/lib/modules bcmdhd4389.ko 2>&1" "$LOG"; ck "wifi-load-gki: insmod then the stock modprobe line" 0 $?
ck "wifi-load-gki: insmod rc 1 -> FAIL" 1 "$(FAKE_TEXT=$w/wg-rc1.txt gpk --out "$w/g14" wifi-load-gki)"
ck "wifi-load-gki: nothing loaded -> FAIL" 1 "$(gpk --out "$w/g14" wifi-load-gki)"
grep -E '/dev/block|flash|dd if' "$LOG" | grep -q .; ck "wifi-load-gki: never names a block device" 1 $?
printf 'overrun: 12\n' > "$w/ovr.txt"; ck "stop-pull: overrun -> FAIL" 1 "$(FAKE_TEXT=$w/ovr.txt gp --out "$w/g14" stop-pull wifi)"; grep -q 'GAP: stop-pull wifi: trace buffer overrun' "$w/out"; ck "stop-pull: overrun GAP named" 0 $?
# wifi-join / wifi-on / wifi-status (ported from golden-unattended d60b48c)
printf 'HomeNet\nPSKsecret99\n' > "$w/wsec"; ck "wifi-join passes" 0 "$(gp --out "$w/g7" wifi-join "$w/wsec")"
grep -q "cmd wifi connect-network 'HomeNet' wpa2 'PSKsecret99'" "$LOG"; ck "wifi-join sends connect-network on su stdin" 0 $?
grep -rq PSKsecret99 "$w/out" "$w/g7"; ck "wifi-join never prints or stores the PSK" 1 $?
ck "wifi-join failure -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g7" wifi-join "$w/wsec")"; grep -q PSKsecret99 "$w/out"; ck "failing wifi-join never prints the PSK" 1 $?
printf "Home'Net\nPSKsecret99\n" > "$w/wbad"; ck "quote in SSID rejected" 2 "$(gp --out "$w/g7" wifi-join "$w/wbad")"; grep -q 'su' "$LOG"; ck "bad secret: no su call" 1 $?
printf 'HomeNet\nPSK\\secret99\n' > "$w/wbs"; ck "backslash in PSK rejected" 2 "$(gp --out "$w/g7" wifi-join "$w/wbs")"
printf 'HomeNet\nshort\n' > "$w/wshort"; ck "short PSK rejected" 2 "$(gp --out "$w/g7" wifi-join "$w/wshort")"
ck "wifi-join without file -> ERROR" 2 "$(gp --out "$w/g7" wifi-join "$w/nofile")"
ck "wifi-on passes" 0 "$(gp --out "$w/g7" wifi-on)"; ck "wifi-on: only set-wifi-enabled" "cmd wifi set-wifi-enabled enabled" "$(grep -v '^adb ' "$LOG")"
printf 'Wifi is enabled\nWifi is connected to "x"\n' > "$w/ws-ok.txt"; ck "wifi-status connected PASS" 0 "$(FAKE_TEXT=$w/ws-ok.txt gp --out "$w/g7" wifi-status)"
printf 'Wifi is enabled\nWifi is not connected\n' > "$w/ws-no.txt"; ck "wifi-status not connected -> FAIL" 1 "$(FAKE_TEXT=$w/ws-no.txt gp --out "$w/g7" wifi-status)"
# review golden-wifi-load-gki-r1 F1/F3 mutant killers
ck "wifi-load-gki: push failure -> REFUSED" 1 "$(FAKE_PUSHFAIL=1 gpk --out "$w/g15" wifi-load-gki)"; grep -q '^adb exec-out' "$LOG"; ck "wifi-load-gki: push failure runs no insmod" 1 $?
FAKE_TEXT=$w/wg-ok.txt gpk --out "$w/g15" wifi-load-gki >/dev/null; ck "wifi-load-gki: exactly one push" 1 "$(grep -c '^adb push' "$LOG")"
grep -q "elif \[ \"\$(sha256sum /data/local/tmp/gp-rfkill.ko | cut -d' ' -f1)\" = [0-9a-f]\{64\} \]; then insmod" "$LOG"; ck "wifi-load-gki: sha checked on the phone before insmod (F1)" 0 $?
grep -q 'rm -f /data/local/tmp/gp-rfkill.ko;' "$LOG"; ck "wifi-load-gki: phone copy deleted" 0 $?
printf 'insmod-rc=phone-sha-mismatch\nmodprobe-rc=skipped-no-rfkill\n' > "$w/wg-mm.txt"; ck "wifi-load-gki: phone sha mismatch -> FAIL" 1 "$(FAKE_TEXT=$w/wg-mm.txt gpk --out "$w/g15" wifi-load-gki)"
printf 'insmod-rc=0\nmodprobe-rc=1\ncfg80211 1 0\nbcmdhd4389 1 0\n' > "$w/wg-mp1.txt"; ck "wifi-load-gki: modprobe rc 1 -> FAIL" 1 "$(FAKE_TEXT=$w/wg-mp1.txt gpk --out "$w/g15" wifi-load-gki)"
printf 'insmod-rc=0\nmodprobe-rc=0\ncfg80211 1 0\n' > "$w/wg-nodhd.txt"; ck "wifi-load-gki: no bcmdhd4389 -> FAIL" 1 "$(FAKE_TEXT=$w/wg-nodhd.txt gpk --out "$w/g15" wifi-load-gki)"
printf 'insmod-rc=0\nmodprobe-rc=0\nbcmdhd4389 1 0\n' > "$w/wg-nocfg.txt"; ck "wifi-load-gki: no cfg80211 -> FAIL" 1 "$(FAKE_TEXT=$w/wg-nocfg.txt gpk --out "$w/g15" wifi-load-gki)"
gp --out "$w/g7" wifi-join "$w/wsec" >/dev/null; grep -q '^adb .*PSKsecret99' "$LOG"; ck "wifi-join: PSK never in adb argv (AP-124)" 1 $?
FAKE_SU_DENY=1 gp --out "$w/g7" wifi-join "$w/wsec" >/dev/null; grep -q 'GAP: wifi-join failed' "$w/out"; ck "wifi-join failure GAP named" 0 $?
printf 'HomeNet\nPSK\tsecret99\n' > "$w/wtab"; ck "control char in PSK rejected" 2 "$(gp --out "$w/g7" wifi-join "$w/wtab")"
printf -- '-HomeNet\nPSKsecret99\n' > "$w/wdash"; ck "SSID starting with - rejected" 2 "$(gp --out "$w/g7" wifi-join "$w/wdash")"
rm -f "$TARDIR/nread"; head -c 4096 /dev/urandom > "$TARDIR/boot.img"; ck "boot-read: no ANDROID! magic -> FAIL" 1 "$(gp --out "$w/g16" boot-read)"
{ printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$TARDIR/boot.img"; rm -f "$TARDIR/nread"; gp --out "$w/g16" boot-read >/dev/null
head -c 4096 /dev/urandom > "$TARDIR/new-boot.img"; ck "magisk-patch: no ANDROID! magic -> FAIL" 1 "$(gp --out "$w/g16" magisk-patch)"; grep -q 'pin it' "$w/out"; ck "magisk-patch FAIL: no pin hint (F4)" 1 $?
{ printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$TARDIR/new-boot.img"
ck "magisk-patch: no Repacking line -> FAIL" 1 "$(FAKE_PATCHLOG='- Unpacking boot image\n' gp --out "$w/g16" magisk-patch)"
ck "magisk-patch: push failure -> REFUSED" 1 "$(FAKE_PUSHFAIL=1 gp --out "$w/g16" magisk-patch)"; grep -q 'boot_patch.sh' "$LOG"; ck "magisk-patch push failure: no patch run" 1 $?
gp --out "$w/g16" magisk-patch >/dev/null; ck "magisk-patch: exactly one push" 1 "$(grep -c '^adb push' "$LOG")"
# GOLDEN_MAGISK_IN (R10 BP31): only a pinned input sha is patched; output name carries the input sha
{ printf 'ANDROID!'; head -c 4096 /dev/urandom; } > "$w/bp31-boot.img"; gsha=$(sha256sum "$w/bp31-boot.img" | cut -d' ' -f1)
ck "pin list holds the BP31 stock boot sha" a18afa36cfe5c8568d6273d9043df56e3538e4a99f7ddc6dc6b6a34a4a12d04c "$(sed -n 's/^GOLDEN_MAGISK_PINS="\([0-9a-f ]*\)"/\1/p' tools/golden-pull)"
ck "GOLDEN_MAGISK_IN unpinned sha -> REFUSED" 1 "$(GOLDEN_MAGISK_IN=$w/bp31-boot.img gp --out "$w/g17" magisk-patch)"; grep -q 'not in the pinned list' "$w/out"; ck "unpinned: reason named" 0 $?; grep -qE '^adb (push|exec-out)' "$LOG"; ck "unpinned: no push or su call" 1 $?
ck "GOLDEN_MAGISK_IN missing file -> REFUSED" 1 "$(GOLDEN_MAGISK_IN=$w/nofile gp --out "$w/g17" magisk-patch)"
sed "s/^GOLDEN_MAGISK_PINS=\"[0-9a-f ]*\"/GOLDEN_MAGISK_PINS=\"$gsha\"/" tools/golden-pull > "$w/k/tools/golden-pull"; chmod +x "$w/k/tools/golden-pull"
ck "GOLDEN_MAGISK_IN pinned sha -> PASS" 0 "$(GOLDEN_MAGISK_IN=$w/bp31-boot.img gpk --out "$w/g17" magisk-patch)"
cmp -s "$TARDIR/new-boot.img" "$w/g17/boot-${gsha:0:8}-magisk.img"; ck "GOLDEN_MAGISK_IN: output boot-<sha8>-magisk.img" 0 $?
ck "GOLDEN_MAGISK_IN: default boot_b-magisk.img not written" 1 "$([ -e "$w/g17/boot_b-magisk.img" ] && echo 0 || echo 1)"
# F5: the pinned hash is taken from the private COPY that is then patched, never from the original path
ck "F5: GOLDEN_MAGISK_IN is never hashed in place" 0 "$(grep -c 'sha256sum "\$GOLDEN_MAGISK_IN"' tools/golden-pull)"
ck "F5: hash is taken of the copy" 1 "$(grep -c 'sha256sum "\$x/in.img"' tools/golden-pull)"
GOLDEN_MAGISK_IN=$w/bp31-boot.img gpk --out "$w/g17" magisk-patch >/dev/null; grep -q '^adb push' "$LOG"; ck "GOLDEN_MAGISK_IN: pushes" 0 $?
ck "GOLDEN_MAGISK_IN needs no boot_b.img" 0 "$([ -e "$w/g17/boot_b.img" ] && echo 1 || echo 0)"
printf 'overrun: 0\ncommit overrun: 0\ndropped events: 0\n' > "$w/ovr0.txt"; ck "stop-pull: zero overrun PASS" 0 "$(FAKE_TEXT=$w/ovr0.txt gp --out "$w/g14" stop-pull wifi)"
printf 'overrun: 0\ncommit overrun: 3\n' > "$w/ovr2.txt"; ck "stop-pull: commit overrun -> FAIL (F5)" 1 "$(FAKE_TEXT=$w/ovr2.txt gp --out "$w/g14" stop-pull wifi)"
printf 'overrun: 0\ndropped events: 7\n' > "$w/ovr3.txt"; ck "stop-pull: dropped events -> FAIL (F5)" 1 "$(FAKE_TEXT=$w/ovr3.txt gp --out "$w/g14" stop-pull wifi)"
# mmio-on
printf 'enable=1\nbuffer_size_kb=98306\n' > "$w/mm1.txt"; ck "mmio-on PASS" 0 "$(FAKE_TEXT=$w/mm1.txt gp --out "$w/g17" mmio-on)"; ck "mmio-on: only the rwmmio enable + buffer size script" "echo 98304 > /sys/kernel/tracing/buffer_size_kb; echo 1 > /sys/kernel/tracing/events/rwmmio/enable" "$(grep -v '^adb ' "$LOG")"
printf 'enable=1\nbuffer_size_kb=7\n' > "$w/mm7.txt"; ck "mmio-on: default 7 KB buffer -> FAIL" 1 "$(FAKE_TEXT=$w/mm7.txt gp --out "$w/g17" mmio-on)"
for v in 65538 98303 abc ''; do printf 'enable=1\nbuffer_size_kb=%s\n' "$v" > "$w/mmv.txt"; ck "mmio-on: buffer_size_kb '$v' -> FAIL" 1 "$(FAKE_TEXT=$w/mmv.txt gp --out "$w/g17" mmio-on)"; done
printf 'enable=1\n' > "$w/mmn.txt"; ck "mmio-on: no buffer_size_kb line -> FAIL" 1 "$(FAKE_TEXT=$w/mmn.txt gp --out "$w/g17" mmio-on)"
printf 'enable=0\n' > "$w/mm0.txt"; ck "mmio-on: enable 0 -> FAIL" 1 "$(FAKE_TEXT=$w/mm0.txt gp --out "$w/g17" mmio-on)"
# wifi-hal-restart
printf 'pid-before=1068\npid-after=13001\nstate=running\n' > "$w/hr-ok.txt"; ck "wifi-hal-restart PASS" 0 "$(FAKE_TEXT=$w/hr-ok.txt gp --out "$w/g18" wifi-hal-restart)"; ck "wifi-hal-restart: restarts exactly the HAL and wificond" 'setprop ctl.restart vendor.wifi_hal_legacy;setprop ctl.restart wificond;' "$(grep -o 'setprop ctl.restart [a-z._]*;' "$LOG" | tr -d '\n')"
printf 'pid-before=1068\npid-after=1068\nstate=running\n' > "$w/hr-same.txt"; ck "wifi-hal-restart: same pid -> FAIL" 1 "$(FAKE_TEXT=$w/hr-same.txt gp --out "$w/g18" wifi-hal-restart)"
printf 'pid-before=1068\npid-after=\nstate=restarting\n' > "$w/hr-dead.txt"; ck "wifi-hal-restart: not running -> FAIL" 1 "$(FAKE_TEXT=$w/hr-dead.txt gp --out "$w/g18" wifi-hal-restart)"
# mmio-filter-wifi / wifi-off
ck "wifi-off: only set-wifi-enabled disabled" "cmd wifi set-wifi-enabled disabled" "$(gp --out "$w/g19" wifi-off >/dev/null; grep -v '^adb ' "$LOG")"
F1='(caller0 >= 0xffffffc001000000 && caller0 < 0xffffffc001000100) || (caller0 >= 0xffffffc002000000 && caller0 < 0xffffffc002000010) || (caller0 >= 0xffffffc003000000 && caller0 < 0xffffffc003000001)'
printf 'bcmdhd4389 256 0 - Live 0xffffffc001000000\npcie_exynos_gs 16 1 cpif, Live 0xffffffc002000000\npcie_exynos_gs101_rc_cal 1 1 pcie_exynos_gs, Live 0xffffffc003000000\nkptr_restrict-restored=2 was=2\n== rwmmio_write\n%s\n== rwmmio_post_write\n%s\n== rwmmio_read\n%s\n== rwmmio_post_read\n%s\n== format\n\tfield:unsigned long caller0;\toffset:16;\n' "$F1" "$F1" "$F1" "$F1" > "$w/mf-ok.txt"
ck "mmio-filter-wifi PASS" 0 "$(FAKE_TEXT=$w/mf-ok.txt gp --out "$w/g19" mmio-filter-wifi)"; ck "mmio-filter-wifi: exact filter written" "echo '$F1' > /sys/kernel/tracing/events/rwmmio/filter" "$(grep -v '^adb ' "$LOG")"
printf 'bcmdhd4389 256 0 - Live 0x0000000000000000\npcie_exynos_gs 16 1 cpif, Live 0xffffffc002000000\npcie_exynos_gs101_rc_cal 1 1 pcie_exynos_gs, Live 0xffffffc003000000\n' > "$w/mf-zero.txt"
ck "mmio-filter-wifi: zero address -> REFUSED" 1 "$(FAKE_TEXT=$w/mf-zero.txt gp --out "$w/g19" mmio-filter-wifi)"; grep -q 'events/rwmmio/filter' "$LOG"; ck "mmio-filter-wifi: zero address writes no filter" 1 $?
printf 'pcie_exynos_gs 16 1 cpif, Live 0xffffffc002000000\n' > "$w/mf-miss.txt"; ck "mmio-filter-wifi: missing module -> REFUSED" 1 "$(FAKE_TEXT=$w/mf-miss.txt gp --out "$w/g19" mmio-filter-wifi)"
printf 'bcmdhd4389 256 0 - Live 0xffffffc001000000\npcie_exynos_gs 16 1 cpif, Live 0xffffffc002000000\npcie_exynos_gs101_rc_cal 1 1 pcie_exynos_gs, Live 0xffffffc003000000\n== filter\nnone\n' > "$w/mf-none.txt"; ck "mmio-filter-wifi: filter not taken -> FAIL" 1 "$(FAKE_TEXT=$w/mf-none.txt gp --out "$w/g19" mmio-filter-wifi)"
sed 's/kptr_restrict-restored=2 was=2/kptr_restrict-restored=0 was=2/' "$w/mf-ok.txt" > "$w/mf-kp.txt"; ck "mmio-filter-wifi: kptr_restrict not restored -> FAIL" 1 "$(FAKE_TEXT=$w/mf-kp.txt gp --out "$w/g19" mmio-filter-wifi)"
FAKE_TEXT=$w/mf-ok.txt gp --out "$w/g19" mmio-filter-wifi >/dev/null; grep -q 'echo \$o > \$k; echo kptr_restrict-restored' "$LOG"; ck "mmio-filter-wifi: kptr_restrict restored in the same shell" 0 $?
awk 'BEGIN{n=0} /^\(caller0/{n++; if(n==3){print "none"; next}} {print}' "$w/mf-ok.txt" > "$w/mf-3.txt"; ck "mmio-filter-wifi: filter on 3 of 4 events -> FAIL" 1 "$(FAKE_TEXT=$w/mf-3.txt gp --out "$w/g19" mmio-filter-wifi)"
# mmio-filter-gpu / -dpu / unknown block, screen-off/on, gpu-state
F2='(caller0 >= 0xffffffc004000000 && caller0 < 0xffffffc004000100) || (caller0 >= 0xffffffc005000000 && caller0 < 0xffffffc005000010)'
printf 'mali_kbase 256 33 - Live 0xffffffc004000000\nmali_pixel 16 2 - Live 0xffffffc005000000\nkptr_restrict-restored=2 was=2\n== rwmmio_write\n%s\n== rwmmio_post_write\n%s\n== rwmmio_read\n%s\n== rwmmio_post_read\n%s\n== format\n\tfield:unsigned long caller0;\toffset:16;\n' "$F2" "$F2" "$F2" "$F2" > "$w/mg-ok.txt"
ck "mmio-filter-gpu PASS" 0 "$(FAKE_TEXT=$w/mg-ok.txt gp --out "$w/g21" mmio-filter-gpu)"; ck "mmio-filter-gpu: exact filter written" "echo '$F2' > /sys/kernel/tracing/events/rwmmio/filter" "$(grep -v '^adb ' "$LOG")"
ck "mmio-filter-gpu: own files" "mmio-filter-gpu.txt mmio-modules-gpu.txt" "$(cd "$w/g21" && ls mmio-* | tr '\n' ' ' | sed 's/ $//')"
grep -q "grep -E '^(mali_kbase|mali_pixel) ' /proc/modules" "$LOG"; ck "mmio-filter-gpu: reads exactly its modules" 0 $?
# review golden-gpu-dpu-r1 F1/F3: each block reads exactly its module list and is refused on the first missing module, no filter written
for b in "dpu:exynos_drm|gs_panel|phy_exynos_mipi_dsim:exynos_drm" "gpu-pmu:mali_kbase|mali_pixel|exynos_pd|cmupmucal:exynos_pd" "wifi-s2mpu:bcmdhd4389|pcie_exynos_gs|pcie_exynos_gs101_rc_cal|s2mpu:bcmdhd4389" "gpu-acpm:mali_kbase|mali_pixel|exynos_pd|cmupmucal|gs_acpm:exynos_pd" "dpu-iommu:exynos_drm|gs_panel|phy_exynos_mipi_dsim|samsung_iommu|exynos_pd:exynos_drm"; do
	IFS=: read -r blk mods miss <<< "$b"
	ck "mmio-filter-$blk: refused" 1 "$(FAKE_TEXT=$w/mg-ok.txt gp --out "$w/g21" "mmio-filter-$blk")"
	grep -qF "grep -E '^($mods) ' /proc/modules" "$LOG"; ck "mmio-filter-$blk: reads exactly ($mods)" 0 $?
	grep -q "mmio-filter-$blk: $miss missing" "$w/out"; ck "mmio-filter-$blk: refused for $miss" 0 $?
	grep -q 'events/rwmmio/filter' "$LOG"; ck "mmio-filter-$blk: no filter written" 1 $?
done
# golden-6 tools: wifi-dwc GAP stub, su-policy-diag (read-only, GAP on empty)
ck "mmio-filter-wifi-dwc: GAP + FAIL" 1 "$(gp --out "$w/g21" mmio-filter-wifi-dwc)"; grep -q '^GAP: mmio-filter-wifi-dwc: no cited caller range' "$w/out"; ck "mmio-filter-wifi-dwc: GAP named" 0 $?
grep -q 'events/rwmmio/filter' "$LOG"; ck "mmio-filter-wifi-dwc: nothing written" 1 $?
ck "su-policy-diag PASS" 0 "$(gp --out "$w/g21" su-policy-diag)"; ck "su-policy-diag: 5 output files" "su-magisk-log.txt su-magisk-v.txt su-policy-all.txt su-policy-count.txt su-policy-one.txt" "$(cd "$w/g21" && ls su-magisk-* su-policy-* | tr '\n' ' ' | sed 's/ $//')"
grep -qF 'magisk --sqlite "SELECT count(*) FROM policies"' "$LOG"; ck "su-policy-diag: count query sent with plain double quotes" 0 $?
grep -qF 'magisk --sqlite "SELECT 1 AS one"' "$LOG"; ck "su-policy-diag: one query sent" 0 $?
grep -qF 'logcat -d -s Magisk' "$LOG"; ck "su-policy-diag: logcat sent" 0 $?
grep -qiE 'insert|update|delete|drop|create|alter|replace' "$LOG"; ck "su-policy-diag: never writes (no mutating SQL)" 1 $?
ck "su-policy-diag: empty output -> FAIL" 1 "$(FAKE_SUSH_EMPTY=1 gp --out "$w/g21" su-policy-diag)"; ck "su-policy-diag: empty output -> 5 GAP lines" 5 "$(grep -c '^GAP: su-policy-diag: .* is empty' "$w/out")"
ck "su-policy-diag: error text + rc 127 -> FAIL" 1 "$(FAKE_SUSH_ERR=1 FAKE_SUSH_RC=127 gp --out "$w/g21" su-policy-diag)"; ck "su-policy-diag: rc 127 -> 5 'did not exit 0' GAPs" 5 "$(grep -c '^GAP: su-policy-diag: .* did not exit 0' "$w/out")"
ck "su-policy-diag: CRLF stripped from files" 0 "$(FAKE_SUSH_CR=1 gp --out "$w/g21" su-policy-diag >/dev/null; grep -c $'\r' "$w"/g21/su-policy-one.txt)"
ck "su-policy-diag: denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g21" su-policy-diag)"
ck "su-policy-diag dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g21" su-policy-diag >/dev/null; wc -c < "$LOG")"
ck "su-policy-diag: argument -> ERROR" 2 "$(gp --out "$w/g21" su-policy-diag x)"
ck "mmio-filter-nope -> ERROR" 2 "$(gp --out "$w/g21" mmio-filter-nope)"; ck "mmio-filter-nope: no adb call" 0 "$(wc -l < "$LOG")"
ck "screen-off: only keyevent SLEEP" "input keyevent KEYCODE_SLEEP" "$(gp --out "$w/g21" screen-off >/dev/null; grep -v '^adb ' "$LOG")"
ck "screen-on: only WAKEUP + one swipe" "input keyevent KEYCODE_WAKEUP; input swipe 540 1800 540 600 200" "$(gp --out "$w/g21" screen-on >/dev/null; grep -v '^adb ' "$LOG")"
printf 'suspended\nscreen=mWakefulness=Asleep\n' > "$w/gs-ok.txt"; ck "gpu-state PASS" 0 "$(FAKE_TEXT=$w/gs-ok.txt gp --out "$w/g22" gpu-state)"
grep -q 'cat /sys/devices/platform/1c500000.mali/power/runtime_status;' "$LOG"; ck "gpu-state: reads the 1c500000.mali runtime_status" 0 $?
FAKE_TEXT=$w/gs-ok.txt gp --out "$w/g22" gpu-state >/dev/null; ck "gpu-state: two calls keep two files" 2 "$(find "$w/g22" -name 'gpu-state-*.txt' | wc -l)"
printf 'resuming\nscreen=mWakefulness=Awake\n' > "$w/gs-res.txt"; ck "gpu-state: resuming PASS" 0 "$(FAKE_TEXT=$w/gs-res.txt gp --out "$w/g22" gpu-state)"
printf 'xsuspendedx\nscreen=\n' > "$w/gs-sub.txt"; ck "gpu-state: substring is not a state -> FAIL" 1 "$(FAKE_TEXT=$w/gs-sub.txt gp --out "$w/g23" gpu-state)"
printf 'cat: x: No such file or directory\nscreen=\n' > "$w/gs-bad.txt"; ck "gpu-state: no runtime_status -> FAIL" 1 "$(FAKE_TEXT=$w/gs-bad.txt gp --out "$w/g23" gpu-state)"
# stop-pull vmallocinfo under kptr_restrict 1 (0 hashes %pK, 2 zeros it)
ck "stop-pull: vmallocinfo restored PASS" 0 "$(gp --out "$w/g20" stop-pull v)"; grep -q 'echo \$o > \$k; echo kptr_restrict-restored' "$LOG"; ck "stop-pull: kptr_restrict restored in the same shell" 0 $?
ck "stop-pull: kptr not restored -> FAIL" 1 "$(FAKE_VMALLOC='0xffffffc00c4b0000-0xffffffc00c4b2000 8192 x phys=0x1 ioremap\nkptr_restrict-restored=0 was=2\n' gp --out "$w/g20" stop-pull v)"
ck "stop-pull: zero vmalloc addresses -> FAIL" 1 "$(FAKE_VMALLOC='0x0000000000000000-0x0000000000000000 8192 x phys=0x1 ioremap\nkptr_restrict-restored=2 was=2\n' gp --out "$w/g20" stop-pull v)"; grep -q 'VA->PA join impossible' "$w/out"; ck "stop-pull: zero-address GAP named" 0 $?
ck "stop-pull: hashed vmalloc addresses -> FAIL" 1 "$(FAKE_VMALLOC='0x0000000033a0eae0-0x00000000b0666397   69632 of_iomap+0x90/0xbc phys=0x0000000010400000 ioremap\nkptr_restrict-restored=2 was=2\n' gp --out "$w/g20" stop-pull v)"; grep -q 'no ioremap row has a kernel VA' "$w/out"; ck "stop-pull: hashed-pointer GAP named" 0 $?
ck "stop-pull: kernel-VA row with trailing vmap -> PASS" 0 "$(FAKE_VMALLOC='0xffffffc00c4b0000-0xffffffc00c4b2000    8192 x phys=0x1 ioremap vmap\nkptr_restrict-restored=2 was=2\n' gp --out "$w/g20" stop-pull v)"
FAKE_VMALLOC='' gp --out "$w/g20" stop-pull v >/dev/null; grep -q 'echo 1 > \$k; cat /proc/vmallocinfo' "$LOG"; ck "stop-pull: vmallocinfo read at kptr_restrict 1" 0 $?
# wifi-dmesg (strings from golden-1 wifi-log-225059 dmesg)
printf '%s\n' '[2191.46] dhdpcie_bus_write_vars: Download, Upload and compare of NVRAM succeeded.' '[2191.47] dhdpcie_bus_download_state: Took ARM out of Reset' '[2192.10] dhdpcie_readshared: addr=0x3afc48' '[2193.01] dhd_open: EXIT' 'dmesg-rc=0' '== end-of-wifi-dmesg' > "$w/wd-ok.txt"
ck "wifi-dmesg PASS" 0 "$(FAKE_TEXT=$w/wd-ok.txt gp --out "$w/g23" wifi-dmesg)"; grep -qF "dmesg > /data/local/tmp/gp-dmesg.txt; echo dmesg-rc=\$?; grep -E 'Download, Upload and compare of NVRAM|Took ARM out of Reset|dhdpcie_readshared|dhd_open' /data/local/tmp/gp-dmesg.txt; rm -f /data/local/tmp/gp-dmesg.txt; echo '== end-of-wifi-dmesg'" "$LOG"; ck "wifi-dmesg: greps the whole dmesg, not a tail" 0 $?
grep -v 'Took ARM' "$w/wd-ok.txt" > "$w/wd-noarm.txt"; ck "wifi-dmesg: missing ARM release -> FAIL" 1 "$(FAKE_TEXT=$w/wd-noarm.txt gp --out "$w/g23" wifi-dmesg)"; grep -q "GAP: wifi-dmesg: no 'Took ARM out of Reset' line" "$w/out"; ck "wifi-dmesg: the missing string is named" 0 $?
echo '== end-of-wifi-dmesg' > "$w/wd-none.txt"; FAKE_TEXT=$w/wd-none.txt gp --out "$w/g23" wifi-dmesg >/dev/null; ck "wifi-dmesg: nothing found -> 5 GAPs (4 strings + no dmesg-rc)" 5 "$(grep -c '^GAP: wifi-dmesg' "$w/out")"
sed 's/^dmesg-rc=0$/dmesg-rc=1/' "$w/wd-ok.txt" > "$w/wd-rc1.txt"; ck "wifi-dmesg: dmesg rc 1 -> FAIL" 1 "$(FAKE_TEXT=$w/wd-rc1.txt gp --out "$w/g23" wifi-dmesg)"; grep -q 'GAP: wifi-dmesg: dmesg did not exit 0' "$w/out"; ck "wifi-dmesg: dmesg failure named" 0 $?
ck "wifi-dmesg dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g23" wifi-dmesg >/dev/null; wc -c < "$LOG")"
ck "wifi-dmesg takes no arguments" 2 "$(gp --dry-run --out "$w/g23" wifi-dmesg x)"
# ui (review golden-unattended-r1; each guard has a kill test)
export WIN_OK='WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #0 Window{a1b2 u0 StatusBar}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true
  Window #1 Window{c3d4 u0 com.android.settings/com.android.settings.Settings}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.settings appop=NONE
    isVisible=true
  mCurrentFocus=Window{c3d4 u0 com.android.settings/com.android.settings.Settings}'
export UI_OK='<hierarchy rotation="0"><node text="" package="com.android.settings" bounds="[0,0][1080,2400]" clickable="false"><node text="Wi-Fi" content-desc="" clickable="true" bounds="[0,0][500,500]" package="com.android.settings"/></node></hierarchy>'
mk() { printf '<hierarchy><node text="" package="%s" bounds="[0,0][1080,2400]" clickable="false"><node text="%s" content-desc="%s" clickable="true" bounds="[0,0][500,500]" package="%s"/></node></hierarchy>' "$1" "$2" "$3" "$1"; }
noinput() { grep -q 'input' "$LOG"; echo $?; }   # 1 = no input call
# r3 F1: attended gate (before GP_UI_ATTENDED is exported for the rest of the ui block)
ck "ui tap without GP_UI_ATTENDED refused" 1 "$(gp --out "$w/u" ui tap 10 20)"; ck "gated: no input" 1 "$(noinput)"; grep -q 'UNATTENDED USE GATED' "$w/out"; ck "gate refusal is loud" 0 $?
ck "ui tap with a wrong GP_UI_ATTENDED value refused" 1 "$(GP_UI_ATTENDED=1 gp --out "$w/u" ui tap 10 20)"
ck "ui key BACK without GP_UI_ATTENDED allowed" 0 "$(gp --out "$w/u" ui key BACK)"
ck "ui key HOME without GP_UI_ATTENDED allowed" 0 "$(gp --out "$w/u" ui key HOME)"
ck "ui dump without GP_UI_ATTENDED allowed" 0 "$(gp --out "$w/u" ui dump gate)"
gp --dry-run --out "$w/u" ui tap 10 20 >/dev/null; grep -q '^would check: attended flag' "$w/out"; ck "dry-run names the attended gate" 0 $?
export GP_UI_ATTENDED=yes-a-human-watches-the-screen
ck "ui dump passes" 0 "$(gp --out "$w/u" ui dump home)"; ck "ui dump writes the dumpsys window text (row 7b)" 0 "$(grep -q mCurrentFocus "$w/u/ui-windows-home.txt" && echo 0 || echo 1)"; ck "ui dump: bad dumpsys window is a GAP" 1 "$(FAKE_WIN=garbage gp --out "$w/u" ui dump bad)"; ck "ui dump writes xml + png" 0 "$([ -s "$w/u/ui-home.xml" ] && [ -s "$w/u/ui-home.png" ] && echo 0 || echo 1)"
ck "ui tap passes on a harmless clickable node" 0 "$(gp --out "$w/u" ui tap 10 20)"; grep -q '^adb shell input tap 10 20$' "$LOG"; ck "tap sent" 0 $?
grep -q 'ui tap 10 20 rc=0$' "$w/u/ui-actions.log"; ck "ui-actions.log line carries rc=0" 0 $?
grep -q "^env FAKESN123$" "$LOG.env"; ck "ANDROID_SERIAL pinned for later adb calls" 0 $?; ck "serial not in output" 0 "$(grep -c FAKESN123 "$w/out")"
# the test owns its literal must-refuse list (AP-124): it does NOT read the words from the code, it only asserts the code's list equals it
want=("Erase all data" "Factory data reset" "factory reset" "Reset options" "Erase downloaded SIMs" "Erase eSIMs" "Direct install" "Select and Patch" "Install to inactive slot" "OEM unlocking" "Uninstall Magisk" "Restore images" "Complete uninstall" "Format" "Download and install" "Install now" "Restart now" "Restart to update" "Update now" "System update" "Software update" "Automatic system updates" "Install update" "Emergency" "Emergency call" "Emergency SOS" "Restart" "Reboot" "Power off" "Install" "Uninstall" "Update" "Download" "DSU" "DSU Loader" "USB debugging" "Revoke USB debugging" "Wireless debugging" "Default USB configuration")
code_words=$(sed -n "s/^FORBID='\\\\b(\(.*\))'\$/\1/p" tools/golden-pull | tr '|' '\n' | sort -u)
ck "FORBID in code equals the test's literal list (missing from code: $(comm -13 <(printf '%s\n' "$code_words") <(printf '%s\n' "${want[@]}" | sort -u) | tr '\n' ,))" "" "$(comm -3 <(printf '%s\n' "$code_words") <(printf '%s\n' "${want[@]}" | sort -u))"
for t in "${want[@]}"; do for v in "$t" "$(tr '[:lower:]' '[:upper:]' <<< "$t")" "${t}s" "${t// /'&#160;'}"; do
	ck "ui tap refused on '$v' (text)" 1 "$(FAKE_UI="$(mk com.android.settings "$v" "")" gp --out "$w/u" ui tap 10 20)"; ck "no input on '$v'" 1 "$(noinput)"
	ck "ui key ENTER refused on '$v' (content-desc)" 1 "$(FAKE_UI="$(mk com.android.settings "" "$v")" gp --out "$w/u" ui key ENTER)"; ck "no input on desc '$v'" 1 "$(noinput)"; done
	nb=${t// /$'\xc2\xa0'}   # raw NBSP bytes in the dump (python sees U+00A0)
	ck "ui key ENTER refused on NBSP '$t'" 1 "$(FAKE_UI="$(mk com.android.settings "" "$nb")" gp --out "$w/u" ui key ENTER)"
	ck "ui key ENTER refused on '&amp;' variant of '$t'" 1 "$(FAKE_UI="$(mk com.android.settings "" "$t &amp; go")" gp --out "$w/u" ui key ENTER)"; done
for v in "Restart &amp; install" "Update &amp; restart" "Restart to finish installing" "Software updates" "Install updates"; do ck "ui refused on review probe '$v'" 1 "$(FAKE_UI="$(mk com.android.settings "$v" "")" gp --out "$w/u" ui tap 10 20)"; done
# F5 .. F10
ck "ui refused on a nested Magisk node" 1 "$(FAKE_UI='<hierarchy><node package="com.android.settings" bounds="[0,0][1080,2400]" clickable="false"><node package="com.topjohnwu.magisk" clickable="true" bounds="[0,0][500,500]" text="x"/></node></hierarchy>' gp --out "$w/u" ui tap 10 20)"
ck "ui refused on a second top-level Magisk window" 1 "$(FAKE_UI='<hierarchy><node package="com.android.settings" bounds="[0,0][1080,2400]" clickable="true" text="a"/><node package="com.topjohnwu.magisk" bounds="[0,0][5,5]" text="b"/></hierarchy>' gp --out "$w/u" ui key ENTER)"
# nested nodes with a bad package under an allowlisted root: windows check cannot mask these (F5/F6)
for bp in android com.android.setting com.topjohnwu.magisk; do ck "ui refused on nested package '$bp'" 1 "$(FAKE_UI="<hierarchy><node package=\"com.android.settings\" bounds=\"[0,0][1080,2400]\" clickable=\"false\"><node package=\"$bp\" text=\"x\"/></node></hierarchy>" gp --out "$w/u" ui key ENTER)"; done
ck "ui refused on a nested node without package" 1 "$(FAKE_UI='<hierarchy><node package="com.android.settings" bounds="[0,0][1080,2400]"><node text="x"/></node></hierarchy>' gp --out "$w/u" ui key ENTER)"
ck "focus package differs from the dumped package -> refused" 1 "$(FAKE_UI="$(mk com.google.android.apps.nexuslauncher Hello "")" gp --out "$w/u" ui key ENTER)"
ck "ui refused on package 'android'" 1 "$(FAKE_UI="$(mk android ok "")" gp --out "$w/u" ui key ENTER)"
ck "ui refused on a prefix package com.android.setting" 1 "$(FAKE_UI="$(mk com.android.setting ok "")" gp --out "$w/u" ui key ENTER)"
ck "ui refused on a node without package" 1 "$(FAKE_UI='<hierarchy><node text="ok" bounds="[0,0][5,5]" clickable="true"/></hierarchy>' gp --out "$w/u" ui key ENTER)"
ck "ui open refused on a FORBID screen" 1 "$(FAKE_UI="$(mk com.android.settings "Factory data reset" "")" gp --out "$w/u" ui open settings)"; grep -q 'am start' "$LOG"; ck "open on FORBID: no am start" 1 $?
ck "ui open developer is rejected" 2 "$(gp --out "$w/u" ui open developer)"
ck "recheck dump failure is fatal" 1 "$(FAKE_DUMPRC2=1 gp --out "$w/u" ui tap 10 20)"; ck "recheck fail: no input" 1 "$(noinput)"
ck "rm failure is fatal" 1 "$(FAKE_RMRC=1 gp --out "$w/u" ui tap 10 20)"; ck "rm fail: no input" 1 "$(noinput)"
ck "input rc 1 -> exit 1" 1 "$(FAKE_INPUTRC=1 gp --out "$w/u" ui tap 10 20)"; grep -q '^GAP: ui tap 10 20 failed rc=1' "$w/out"; ck "input failure prints a GAP" 0 $?; grep -q 'ui tap 10 20 rc=1$' "$w/u/ui-actions.log"; ck "rc=1 logged" 0 $?
ck "unknown serial refused" 1 "$(FAKE_SN=unknown gp --out "$w/u" ui key ENTER)"; ck "unknown serial: no input" 1 "$(noinput)"
ck "empty serial refused" 1 "$(FAKE_SN="" gp --out "$w/u" ui key ENTER)"; ck "empty serial: no input" 1 "$(noinput)"
gp --out "$w/u" ui key ENTER >/dev/null; ck "serial pinned before the device check (2nd adb call already has it)" "env FAKESN123" "$(sed -n 2p "$LOG.env")"
ck "en-XA refused" 1 "$(FAKE_LOCALE=en-XA gp --out "$w/u" ui tap 10 20)"; ck "en-XC refused" 1 "$(FAKE_LOCALE=en-XC gp --out "$w/u" ui tap 10 20)"; ck "en-GB allowed" 0 "$(FAKE_LOCALE=en-GB gp --out "$w/u" ui tap 10 20)"
ck "long-press swipe (3000 ms) refused" 1 "$(gp --out "$w/u" ui swipe 540 1800 540 600 3000)"
ck "zero-length swipe on a clickable node = tap check passes" 0 "$(gp --out "$w/u" ui swipe 10 20 10 20 300)"
ck "zero-length swipe off a clickable node refused" 1 "$(gp --out "$w/u" ui swipe 900 900 900 900 300)"
gp --dry-run --out "$w/u" ui tap 10 20 >/dev/null; grep -q '^would check: locale' "$w/out"; ck "dry-run says what it skips" 0 $?
grep -q 'would run: adb shell uiautomator dump' "$w/out"; ck "dry-run shows the would-run dump lines" 0 $?
# after-action dump (F10): FORBID visible after the action is a GAP
ck "destructive control after the action -> GAP, exit 1" 1 "$(FAKE_UI3="$(mk com.android.settings "Factory data reset" "")" gp --out "$w/u" ui tap 10 20)"
# text then ENTER (F3)
gp --out "$w/u" ui text dsu >/dev/null; ck "key ENTER right after text refused" 1 "$(: > "$LOG"; tools/golden-pull --out "$w/u" ui key ENTER >/dev/null 2>&1 </dev/null; echo $?)"
# F1: all windows ([guess] AOSP dumpsys window format, unmeasured; unparseable = refuse)
ck "windows: Magisk heads-up window visible -> refused" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 com.topjohnwu.magisk/.Hun}:
    mOwnerUid=1000 showForAllUsers=false package=com.topjohnwu.magisk appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; ck "hun: no input" 1 "$(noinput)"
ck "windows: invisible foreign window ignored" 0 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 com.topjohnwu.magisk/.Hun}:
    mOwnerUid=1000 showForAllUsers=false package=com.topjohnwu.magisk appop=NONE
    isVisible=false" gp --out "$w/u" ui tap 10 20)"
ck "windows: visible NotificationShade (bare title) refused" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 NotificationShade}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"
ck "windows: focus on a foreign package refused" 1 "$(FAKE_WIN="WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #1 Window{c3d4 u0 com.android.settings/.S}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.settings appop=NONE
    isVisible=true
  mCurrentFocus=Window{c3d4 u0 com.topjohnwu.magisk/.Main}" gp --out "$w/u" ui tap 10 20)"
BADW="  Window #3 Window{e5f6 u0 com.topjohnwu.magisk/.Hun}:
    mOwnerUid=1000 showForAllUsers=false package=com.topjohnwu.magisk appop=NONE"
ck "windows: isVisible=yes (other spelling) + Magisk window refused" 1 "$(FAKE_WIN="$WIN_OK
$BADW
    isVisible=yes" gp --out "$w/u" ui tap 10 20)"; ck "VISIBLE: no input" 1 "$(noinput)"
ck "windows: no isVisible line + Magisk window refused" 1 "$(FAKE_WIN="$WIN_OK
$BADW
    mHaveFrame=true" gp --out "$w/u" ui tap 10 20)"
grep -q 'ui tap 10 20: window .* has no single isVisible=/package= \[BP31 format\]' "$w/out"; ck "missing isVisible: named reason (not a traceback)" 0 $?
ck "windows: duplicate isVisible lines refused" 1 "$(FAKE_WIN="$WIN_OK
$BADW
    isVisible=false
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; grep -q 'no single isVisible=/package=' "$w/out"; ck "duplicate isVisible: named reason" 0 $?
ck "windows: second mCurrentFocus foreign -> refused" 1 "$(FAKE_WIN="$WIN_OK
  mCurrentFocus=Window{e5f6 u0 com.topjohnwu.magisk/.Main}" gp --out "$w/u" ui tap 10 20)"
ck "windows: unparseable EXITING focus line refused" 1 "$(FAKE_WIN="$WIN_OK
  mCurrentFocus=Window{e5f6 u0 com.android.settings/.S EXITING}" gp --out "$w/u" ui tap 10 20)"
ck "windows: focus null alone refused" 1 "$(FAKE_WIN="WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #1 Window{c3d4 u0 com.android.settings/.S}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.settings appop=NONE
    isVisible=true
  mCurrentFocus=null" gp --out "$w/u" ui tap 10 20)"
ck "windows: focus null plus a valid focus allowed" 0 "$(FAKE_WIN="$WIN_OK
  mCurrentFocus=null" gp --out "$w/u" ui tap 10 20)"
ck "windows: mCurrentFocus but no Window # block refused" 1 "$(FAKE_WIN="mCurrentFocus=Window{c3d4 u0 com.android.settings/.S}" gp --out "$w/u" ui tap 10 20)"
ck "windows: header with a space in the title refused" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 Splash Screen x}:
    mOwnerUid=1000 showForAllUsers=false package=x appop=NONE
    isVisible=false" gp --out "$w/u" ui tap 10 20)"
ck "windows: Magisk window only in the recheck dump -> refused" 1 "$(FAKE_WIN2="$WIN_OK
$BADW
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; ck "recheck window: no input" 1 "$(noinput)"
ck "windows: Magisk window only in the guard dump -> refused" 1 "$(FAKE_WIN="$WIN_OK
$BADW
    isVisible=true" FAKE_WIN2="$WIN_OK" gp --out "$w/u" ui tap 10 20)"
# BP31 format (measured golden-3 ui7b, 2026-10-28)
ck "windows: heads-up (visible NotificationShade, focus settings) refused" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 NotificationShade}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; ck "heads-up: no input" 1 "$(noinput)"; grep -q "visible window 'NotificationShade' (package com.android.systemui) is not allowlisted" "$w/out"; ck "heads-up: named reason" 0 $?
ck "windows: InputMethod + ImageWallpaper visible, allowlisted focus passes" 0 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 InputMethod}:
    mOwnerUid=1000 showForAllUsers=false package=com.google.android.inputmethod.latin appop=NONE
    isVisible=true
  Window #4 Window{e5f7 u0 com.android.systemui.wallpapers.ImageWallpaper}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"
ck "windows: activity whose package= differs from its title prefix refused" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 com.android.settings/.Fake}:
    mOwnerUid=1000 showForAllUsers=false package=com.topjohnwu.magisk appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; grep -q "is not allowlisted" "$w/out"; ck "package mismatch: named reason" 0 $?
ck "windows: missing WINDOWS section refused" 1 "$(FAKE_WIN="  Window #1 Window{c3d4 u0 com.android.settings/.S}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.settings appop=NONE
    isVisible=true
  mCurrentFocus=Window{c3d4 u0 com.android.settings/.S}" gp --out "$w/u" ui tap 10 20)"; grep -q '0 WINDOW MANAGER WINDOWS sections, need exactly 1' "$w/out"; ck "missing section: named reason" 0 $?
# real trimmed BP31 fixtures (tools/testdata/golden-ui-bp31): per state, a synthetic dump holding the focus package
for st in s1-settings-wifi:pass s2-headsup:no s3-shade:no s4-lockscreen:no s5-launcher:pass s6-keyboard:pass s7-powermenu:no s7b-immersive:no; do
	f=tools/testdata/golden-ui-bp31/${st%%:*}.txt; fp=$(sed -n 's/.*mCurrentFocus=Window{[^ ]* u[0-9]* \([^/} ]*\).*/\1/p' "$f" | head -1)
	exp=1; [ "${st##*:}" = pass ] && exp=0
	ck "fixture ${st%%:*}: $([ $exp = 0 ] && echo passes || echo refused)" $exp "$(FAKE_WIN="$(cat "$f")" FAKE_UI="$(mk "$fp" Hello "")" gp --out "$w/u" ui tap 10 20)"
	[ $exp = 0 ] || { grep -E '^REFUSED' "$w/out" | sed "s/^/  ${st%%:*}: /"; }
done
FAKE_WIN=garbage gp --out "$w/u" ui tap 10 20 >/dev/null; grep -q '^REFUSED: ui tap 10 20: no mCurrentFocus' "$w/out"; ck "refusal names the action" 0 $?
FAKE_UI='<hierarchy><node' gp --out "$w/u" ui tap 10 20 >/dev/null; grep -q '^REFUSED: ui tap 10 20: dump not parseable (ParseError)' "$w/out"; ck "unparseable dump: non-empty reason, refused" 0 $?
gp --out "$w/u" ui open wifi >/dev/null; ck "ui open wifi takes guard + recheck + after dumps" 3 "$(grep -c 'shell uiautomator dump' "$LOG")"
ck "short swipe 99 refused" 1 "$(gp --out "$w/u" ui swipe 900 900 900 999 300)"
ck "short swipe 100 passes" 0 "$(gp --out "$w/u" ui swipe 900 900 900 1000 300)"
ck "short swipe 101 passes" 0 "$(gp --out "$w/u" ui swipe 900 900 900 1001 300)"
ck "windows: non-Window focus value next to a valid one refused (r3 F2a)" 1 "$(FAKE_WIN="$WIN_OK
  mCurrentFocus=ActivityRecord{e5f6 u0 com.android.settings/.S}" gp --out "$w/u" ui tap 10 20)"
ck "windows: visible MagiskStatusBarHun title refused (r3 F2b)" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 MagiskStatusBarHun}:
    mOwnerUid=1000 showForAllUsers=false package=com.topjohnwu.magisk appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"
ck "windows: visible NavigationBarX title refused (r3 F2b)" 1 "$(FAKE_WIN="$WIN_OK
  Window #3 Window{e5f6 u0 NavigationBarX}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"
ck "tap takes 2 dumpsys window calls (r3 F2c)" 2 "$(gp --out "$w/u" ui tap 10 20 >/dev/null; grep -c 'shell dumpsys window' "$LOG")"
ck "windows: unparseable dumpsys refused" 1 "$(FAKE_WIN="garbage" gp --out "$w/u" ui tap 10 20)"
ck "windows: BACK still allowed on a bad window" 0 "$(FAKE_WIN="garbage" gp --out "$w/u" ui key BACK)"
ck "ui key BACK allowed on a FORBID screen" 0 "$(FAKE_UI="$(mk com.android.settings "Factory data reset" "")" gp --out "$w/u" ui key BACK)"; grep -q '^adb shell input keyevent KEYCODE_BACK$' "$LOG"; ck "BACK sent" 0 $?
ck "ui key HOME allowed on a FORBID screen" 0 "$(FAKE_UI="$(mk com.topjohnwu.magisk "Direct install" "")" gp --out "$w/u" ui key HOME)"
ck "ui key BACK still needs a fresh dump" 1 "$(FAKE_UI=garbage gp --out "$w/u" ui key BACK)"; ck "BACK blind: no input" 1 "$(noinput)"
ck "ui refuses without a screen dump" 1 "$(FAKE_UI=garbage gp --out "$w/u" ui key ENTER)"; ck "blind: no input" 1 "$(noinput)"
ck "ui refuses on a stale dump (uiautomator dump rc 1, cat still benign)" 1 "$(FAKE_DUMPRC=1 gp --out "$w/u" ui tap 10 20)"; ck "stale: no input" 1 "$(noinput)"
gp --out "$w/u" ui tap 10 20 >/dev/null; ck "old dump removed before every dump" 0 "$(awk '/shell rm -f \/data\/local\/tmp\/ui.xml/{r=1} /shell uiautomator dump/{if(!r)bad=1; r=0} END{print bad+0}' "$LOG")"
ck "ui refused on non-en locale" 1 "$(FAKE_LOCALE=de-DE gp --out "$w/u" ui tap 10 20)"; ck "de: no input" 1 "$(noinput)"
ck "ui locale falls back to ro.product.locale" 1 "$(FAKE_LOCALE="" FAKE_PLOCALE=fr-FR gp --out "$w/u" ui tap 10 20)"
ck "ui tap outside every clickable node refused" 1 "$(gp --out "$w/u" ui tap 900 900)"; ck "outside: no input" 1 "$(noinput)"
ck "ui tap on a non-clickable node refused" 1 "$(FAKE_UI="${UI_OK//clickable=\"true\"/clickable=\"false\"}" gp --out "$w/u" ui tap 10 20)"
ck "ui refused on Magisk foreground" 1 "$(FAKE_UI="$(mk com.topjohnwu.magisk Hello "")" gp --out "$w/u" ui tap 10 20)"; ck "magisk: no input" 1 "$(noinput)"
ck "ui refused on an unknown package" 1 "$(FAKE_UI="$(mk com.android.vending Hello "")" gp --out "$w/u" ui key ENTER)"
ck "systemui activity window refused (r1 F2: permission dialogs are systemui activities)" 1 "$(FAKE_WIN="WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #1 Window{c3d4 u0 com.android.systemui/.media.MediaProjectionPermissionActivity}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true
  mCurrentFocus=Window{c3d4 u0 com.android.systemui/.media.MediaProjectionPermissionActivity}" FAKE_UI="$(mk com.android.systemui Hello "")" gp --out "$w/u" ui key ENTER)"; ck "systemui activity: no input" 1 "$(noinput)"
ck "two WINDOWS sections refused (r1 F1)" 1 "$(FAKE_WIN="$WIN_OK
WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #9 Window{eeee u0 GlobalActionsDialogLite}:
    mOwnerUid=1000 showForAllUsers=false package=com.android.systemui appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"; ck "two sections: no input" 1 "$(noinput)"
ck "StatusBar title with a foreign package refused (r1 F6)" 1 "$(FAKE_WIN="$WIN_OK
  Window #9 Window{eeee u0 StatusBar}:
    mOwnerUid=10999 showForAllUsers=false package=com.evil.app appop=NONE
    isVisible=true" gp --out "$w/u" ui tap 10 20)"
for pk in com.google.android.apps.nexuslauncher com.google.android.GoogleCamera; do ck "ui allowed on $pk" 0 "$(FAKE_WIN="WINDOW MANAGER WINDOWS (dumpsys window windows)
  Window #1 Window{c3d4 u0 $pk/.A}:
    mOwnerUid=1000 showForAllUsers=false package=$pk appop=NONE
    isVisible=true
  mCurrentFocus=Window{c3d4 u0 $pk/.A}" FAKE_UI="$(mk $pk Hello "")" gp --out "$w/u" ui key ENTER)"; done
ck "ui refused when the second dump differs" 1 "$(FAKE_UI2="${UI_OK/Wi-Fi/Wi-Fj}" gp --out "$w/u" ui tap 10 20)"; ck "mismatch: no input" 1 "$(noinput)"
ck "ui swipe on screen passes" 0 "$(gp --out "$w/u" ui swipe 540 1800 540 600 200)"; grep -q '^adb shell input swipe 540 1800 540 600 200$' "$LOG"; ck "swipe sent" 0 $?
ck "ui swipe off screen refused" 1 "$(gp --out "$w/u" ui swipe 540 1800 540 5000 200)"; ck "swipe off: no input" 1 "$(noinput)"
ck "ui key POWER rejected" 2 "$(gp --out "$w/u" ui key POWER)"; ck "ui text with ';' rejected" 2 "$(gp --out "$w/u" ui text 'a;b')"; ck "ui swipe bad number rejected" 2 "$(gp --out "$w/u" ui swipe 1 2 3 x)"
ck "ui open camera" 0 "$(gp --out "$w/u" ui open camera)"; grep -q '^adb shell am start -a android.media.action.STILL_IMAGE_CAMERA$' "$LOG"; ck "camera intent sent" 0 $?
ck "ui open unknown page rejected" 2 "$(gp --out "$w/u" ui open reset)"
ck "ui dry-run: no adb call" 0 "$(gp --dry-run --out "$w/u" ui tap 10 20 >/dev/null; wc -c < "$LOG")"
# magisk-install (pinned apk) and wifi-cold-off
mkdir -p "$HOME/pixelios-artifacts/golden/magisk"; printf 'not the apk' > "$HOME/pixelios-artifacts/golden/magisk/Magisk-v30.7.apk"
ck "magisk-install: sha mismatch refused" 1 "$(gp --out "$w/g24" magisk-install)"; grep -q '^adb install' "$LOG"; ck "magisk-install: no install on a sha mismatch" 1 $?
# su-prep (review tp037-s0-3-su-r1 F1/F2): a real zip fixture with the busybox member; the real tool's pin refuses it (F2), a pin-patched COPY accepts it
python3 -I -c 'import sys,zipfile,os; z=zipfile.ZipFile(sys.argv[1],"w"); z.writestr("lib/arm64-v8a/libbusybox.so", os.urandom(2048)); z.writestr("AndroidManifest.xml", "x"); z.close()' "$HOME/pixelios-artifacts/golden/magisk/Magisk-v30.7.apk"
bbsha=$(python3 -I -c 'import sys,zipfile,hashlib; print(hashlib.sha256(zipfile.ZipFile(sys.argv[1]).read("lib/arm64-v8a/libbusybox.so")).hexdigest())' "$HOME/pixelios-artifacts/golden/magisk/Magisk-v30.7.apk")
ck "su-prep: valid zip with a wrong sha refused" 1 "$(gp --out "$w/g24" su-prep)"; grep -q 'sha256 differs from the pin' "$w/out"; ck "su-prep: refusal names the pin" 0 $?
grep -q '^adb push' "$LOG"; ck "su-prep: no push on a sha mismatch" 1 $?
sed "s/e0d32d2123532860f97123d927b1bb86c4e08e6fd8a48bfc6b5bee0afae9ebd5/$(sha256sum "$HOME/pixelios-artifacts/golden/magisk/Magisk-v30.7.apk" | cut -d' ' -f1)/" tools/golden-pull > "$w/k/tools/golden-pull-su"; chmod +x "$w/k/tools/golden-pull-su"
mkdir -p "$w/tmpd"
gps() { : > "$LOG"; TMPDIR=$w/tmpd "$w/k/tools/golden-pull-su" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
sucalls() { grep '^adb ' "$LOG" | sed "s#^adb push $w/tmpd/tmp\.[A-Za-z0-9]*/bb #adb push TMP/bb #"; }
want_calls='adb push TMP/bb /data/local/tmp/pixos_busybox
adb shell sha256sum /data/local/tmp/pixos_busybox
adb shell setprop debug.pixos.bb.done '"''"'
adb shell setprop debug.pixos.bb 0
adb shell setprop debug.pixos.bb 1
adb shell i=0; while [ -z "$(getprop debug.pixos.bb.done)" ] && [ $i -lt 100 ]; do sleep 0.2; i=$((i + 1)); done; getprop debug.pixos.bb.done'
ck "su-prep PASS (sha match, done=ok)" 0 "$(FAKE_BBSHA=$bbsha gps --out "$w/g24" su-prep)"
ck "su-prep PASS: exact adb call list" "$want_calls" "$(sucalls | grep -vxF -e 'adb get-serialno' -e 'adb shell getprop ro.product.device')"
ck "su-prep PASS: last line is RESULT PASS" "RESULT: PASS" "$(tail -1 "$w/out" | cut -c1-12)"
grep -q '^NEXT: reboot, then tools/golden-pull su-check' "$w/out"; ck "su-prep PASS: NEXT line" 0 $?
grep -q '^GAP' "$w/out"; ck "su-prep PASS: no always-on GAP" 1 $?
ck "su-prep: temp dir removed" "" "$(ls -A "$w/tmpd")"
ck "su-prep: readback mismatch -> FAIL" 1 "$(FAKE_BBSHA=0000 gps --out "$w/g24" su-prep)"; grep -q 'setprop' "$LOG"; ck "su-prep mismatch: no setprop" 1 $?
ck "su-prep mismatch: temp dir removed" "" "$(ls -A "$w/tmpd")"; ck "su-prep mismatch: RESULT FAIL last" "RESULT: FAIL" "$(tail -1 "$w/out" | cut -c1-12)"
ck "su-prep: done=fail -> FAIL" 1 "$(FAKE_DONE=fail FAKE_BBSHA=$bbsha gps --out "$w/g24" su-prep)"; ck "su-prep done=fail: RESULT FAIL last" "RESULT: FAIL" "$(tail -1 "$w/out" | cut -c1-12)"
ck "su-prep: done empty -> FAIL" 1 "$(FAKE_DONE='' FAKE_BBSHA=$bbsha gps --out "$w/g24" su-prep)"; grep -q "is 'empty'" "$w/out"; ck "su-prep done empty: named" 0 $?
ck "su-prep: temp dir removed after failures" "" "$(ls -A "$w/tmpd")"
ck "su-prep dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g24" su-prep >/dev/null; grep -c . "$LOG")"
printf '%s\n' 'scan-rc=0' 'off-rc=0' '[378.34] dhd_stop: ENTER' '[378.69] dhd_stop: EXIT' '== end-of-wifi-cold-off' > "$w/co-ok.txt"; ck "wifi-cold-off PASS" 0 "$(FAKE_TEXT=$w/co-ok.txt gp --out "$w/g24" wifi-cold-off)"; grep -q "echo GP-COLD-OFF-MARK > /dev/kmsg; cmd wifi set-scan-always-available disabled; echo scan-rc=.*sed -n '/GP-COLD-OFF-MARK/" "$LOG"; ck "wifi-cold-off: marker, then scan-always off, then only lines after the marker" 0 $?
sed 's/^scan-rc=0$/scan-rc=1/' "$w/co-ok.txt" > "$w/co-rc.txt"; ck "wifi-cold-off: scan-always call failed -> FAIL" 1 "$(FAKE_TEXT=$w/co-rc.txt gp --out "$w/g24" wifi-cold-off)"
echo '== end-of-wifi-cold-off' > "$w/co-none.txt"; ck "wifi-cold-off: no dhd_stop -> FAIL" 1 "$(FAKE_TEXT=$w/co-none.txt gp --out "$w/g24" wifi-cold-off)"
# golden-unattended-image (TP-037 part 1) and fboot-android --unattended: a SYNTHETIC tiny v4 image (never the 64 MiB base) with an AVB hash footer, PIN patched to its hash
# provision-skip (research 106): unattended setup-wizard + keyguard skip; PASS path, mutants that must FAIL, safety
ck "provision-skip PASS" 0 "$(gp --out "$w/g21" provision-skip)"; grep -q '^RESULT: PASS provision-skip gaps=0' "$w/out"; ck "provision-skip: RESULT line" 0 $?
ck "provision-skip: 5 output files" "provision-flags.txt provision-keys.txt provision-lock.txt provision-state.txt provision-suw.txt" "$(cd "$w/g21" && ls provision-* | tr '\n' ' ' | sed 's/ $//' | tr ' ' '\n' | sort -k1,1 -s | tr '\n' ' ' | sed 's/ $//')"
ck "provision-skip: no GAP on the PASS path" 0 "$(grep -c '^GAP' "$w/out")"
ck "provision-skip: setup wizard still focused -> FAIL" 1 "$(FAKE_PS_FOCUS='com.google.android.setupwizard/.SetupWizardActivity' gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: focus is not the launcher' "$w/out"; ck "provision-skip: focus GAP named" 0 $?; grep -q '^RESULT: FAIL provision-skip gaps=1' "$w/out"; ck "provision-skip: focus mutant: RESULT FAIL gaps=1" 0 $?
ck "provision-skip: focus launcher on the retry -> PASS" 0 "$(FAKE_PS_FOCUS='com.google.android.setupwizard/.A' FAKE_PS_FOCUS2='com.google.android.apps.nexuslauncher/x' gp --out "$w/g21" provision-skip)"
ck "provision-skip: pm disable-user without suw=ok -> FAIL" 1 "$(FAKE_PS_NOSUW=1 gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: provision-suw.txt has no suw=ok' "$w/out"; ck "provision-skip: suw GAP named" 0 $?
ck "provision-skip: flags step without flags=ok -> FAIL" 1 "$(FAKE_PS_NOFLAGS=1 gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: provision-flags.txt has no flags=ok' "$w/out"; ck "provision-skip: flags GAP named" 0 $?
ck "provision-skip: keyguard showing=true -> FAIL" 1 "$(FAKE_PS_SHOWING=true gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: keyguard not showing=false' "$w/out"; ck "provision-skip: showing GAP named" 0 $?
ck "provision-skip: user_setup_complete not 1 -> FAIL" 1 "$(FAKE_PS_USC=0 gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: device_provisioned' "$w/out"; ck "provision-skip: flag readback GAP named" 0 $?
ck "provision-skip: wizard not disabled -> FAIL" 1 "$(FAKE_PS_NOPKG=1 gp --out "$w/g21" provision-skip)"; grep -q "^GAP: provision-skip: setup wizard not in" "$w/out"; ck "provision-skip: wizard GAP named" 0 $?
ck "provision-skip: no lock=ok -> FAIL" 1 "$(FAKE_PS_NOLOCK=1 gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: provision-lock.txt has no lock=ok' "$w/out"; ck "provision-skip: lock GAP named" 0 $?
ck "provision-skip: no keys=ok -> FAIL" 1 "$(FAKE_PS_NOKEYS=1 gp --out "$w/g21" provision-skip)"; grep -q '^GAP: provision-skip: provision-keys.txt has no keys=ok' "$w/out"; ck "provision-skip: keys GAP named" 0 $?
ck "provision-skip: get-disabled not true -> FAIL" 1 "$(FAKE_PS_LOCKDIS=false gp --out "$w/g21" provision-skip)"; grep -q "^GAP: provision-skip: 'cmd lock_settings get-disabled' did not read back true" "$w/out"; ck "provision-skip: get-disabled GAP named" 0 $?
ck "provision-skip: keyguard showing=true then false on the retry -> PASS" 0 "$(FAKE_PS_SHOWING=true FAKE_PS_SHOWING2=false gp --out "$w/g21" provision-skip)"
ps_re=$(sed -n "s/^[[:space:]]*ps_forbid='\(.*\)'\$/\1/p" tools/golden-pull); ck "provision-skip: forbidden-word regex found in golden-pull" 1 "${ps_re:+1}"
for ps_w in "pm clear x" "pm uninstall x" "reboot" "dd if=/dev/zero of=/x" "fastboot flash" "magisk --install"; do printf '%s\n' "$ps_w" | grep -qE "$ps_re"; ck "provision-skip: guard regex matches '$ps_w'" 0 $?; done
printf '%s\n' "settings put system add x" | grep -qE "$ps_re"; ck "provision-skip: guard regex does not match 'add '" 1 $?
grep -qF 'grep -qE "$ps_forbid" || fail' tools/golden-pull; ck "provision-skip: guard loop uses the regex" 0 $?
ck "provision-skip: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gp --out "$w/g21" provision-skip)"
ck "provision-skip: argument -> ERROR" 2 "$(gp --out "$w/g21" provision-skip x)"
ck "provision-skip dry-run: no adb call" 0 "$(gp --dry-run --out "$w/g21" provision-skip >/dev/null; wc -c < "$LOG")"
gp --out "$w/g21" provision-skip >/dev/null; grep -qE "$ps_re" "$LOG"; ck "provision-skip: sent script text has no forbidden word" 1 $?
grep -qF 'pm disable-user --user 0 com.google.android.setupwizard && echo suw=ok; echo __GP_RC=$?' "$LOG"; ck "provision-skip: exact chained suw script sent" 0 $?
# bt-* actions (golden-bt-s0): PASS paths, mutants that must FAIL, safety
gb() { rm -rf "$w/gb"; gp --out "$w/gb" "$@"; }
gbm() { rm -rf "$w/gb"; : > "$LOG"; "$w/mut/golden-pull" --out "$w/gb" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }   # mutated copy of the tool
bt_addr_re='([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}|(^|[^0-9A-Fa-f])[0-9A-Fa-f]{12}([^0-9A-Fa-f]|$)'
bt_re=$(sed -n "s/^[[:space:]]*bt_forbid='\(.*\)'\$/\1/p" tools/golden-pull); ck "bt: forbidden-word regex found in golden-pull" 1 "${bt_re:+1}"
for bt_w in "pm clear x" "pm uninstall x" "dd if=/x" "dd of=/x" "fastboot x" "flash x" "rm -f x" "reboot" "unpair x" "cat bt_config.conf"; do printf '%s\n' "$bt_w" | grep -qE "$bt_re"; ck "bt: guard regex matches '$bt_w'" 0 $?; done
grep -qF 'grep -qE "$bt_forbid" || fail' tools/golden-pull; ck "bt: guard function uses the regex" 0 $?
# bt-static
ck "bt-static PASS" 0 "$(gb bt-static)"; grep -q '^RESULT: PASS bt-static gaps=0' "$w/out"; ck "bt-static: RESULT line" 0 $?
ck "bt-static: no GAP on the PASS path" 0 "$(grep -c '^GAP' "$w/out")"
ck "bt-static: files" "bt-bdaddr-where.txt bt-conf-list.txt bt-dt.txt bt-fw-sha.txt bt-props.txt bt-rfkill-before.txt bt-tty.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
ck "bt-static: vendor conf copied" bt_vendor.conf "$(ls "$w/gb/bt/bt-vendor-conf")"
grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-static: no 6-byte address in any output" 1 $?
grep -q '<masked>' "$w/gb/bt/bt-props.txt"; ck "bt-static: address value masked in bt-props.txt" 0 $?
grep -q 'persist.vendor.bdaddr' "$w/gb/bt/bt-props.txt"; ck "bt-static: prop NAME kept" 0 $?
grep -qx 'conf=ok' "$w/gb/bt/bt-vendor-conf/bt_vendor.conf"; ck "bt-static: marker stripped from the conf copy" 1 $?
# GP_BT_TRACE (bt-dt.txt was only __GP_RC=1 on-device in g-bt-20261028T230353Z-b2, gbp31b-20261029T081941Z-b2, gbtcold-20261029T111135Z-b2): default = unchanged, no trace, no .stderr file
grep -qx 'set -x' "$LOG"; ck "bt-static default: remote script not traced (no set -x)" 1 $?; ck "bt-static default: no .stderr file" 0 "$(find "$w/gb/bt" -name '*.stderr' | wc -l)"
GP_BT_TRACE=1 FAKE_SUSH_STDERR='+ ls -ld /proc/device-tree aa:bb:cc:dd:ee:ff' gb bt-static >/dev/null; grep -qx 'set -x' "$LOG"; ck "bt-static GP_BT_TRACE=1: remote script traced (set -x first)" 0 $?
ck "bt-static GP_BT_TRACE=1: .stderr written for bt-dt.txt" 0 "$([ -s "$w/gb/bt/bt-dt.txt.stderr" ] && grep -q 'ls -ld /proc/device-tree' "$w/gb/bt/bt-dt.txt.stderr" && echo 0 || echo 1)"
grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-static GP_BT_TRACE=1: address masked in .stderr too" 1 $?
grep -q 'ls -ld /proc/device-tree' "$w/gb/bt/bt-dt.txt"; ck "bt-static GP_BT_TRACE=1: stderr not merged into bt-dt.txt" 1 $?
for bt_mk in dt rfkill tty fw props where conflist; do ck "bt-static: no $bt_mk marker -> FAIL" 1 "$(FAKE_BT_MISS=$bt_mk gb bt-static)"; grep -q "^GAP: bt-static: .* has no '$bt_mk=ok' marker" "$w/out"; ck "bt-static: $bt_mk GAP named" 0 $?; done
ck "bt-static: no vendor conf marker -> FAIL" 1 "$(FAKE_BT_MISS=conf gb bt-static)"; grep -q "^GAP: bt-static: bt/bt-vendor-conf/bt_vendor.conf has no 'conf=ok' marker" "$w/out"; ck "bt-static: conf GAP named" 0 $?
ck "bt-static after PASS" 0 "$(gb bt-static after)"; ck "bt-static after: files" "bt-props-after.txt bt-rfkill-after.txt bt-tty-after.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-static after: no 6-byte address" 1 $?
ck "bt-static after: no rfkill marker -> FAIL" 1 "$(FAKE_BT_MISS=rfkill gb bt-static after)"
ck "bt-static: bad phase arg -> ERROR" 2 "$(gb bt-static during)"; ck "bt-static: two args -> ERROR" 2 "$(gb bt-static before x)"
gb bt-static >/dev/null; grep -qE "$bt_re" "$LOG"; ck "bt-static: sent script text has no forbidden word" 1 $?
grep -qi 'bluedroid' "$LOG"; ck "bt-static: never touches /data/misc/bluedroid" 1 $?
ck "bt-static dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-static >/dev/null; wc -c < "$LOG")"
# mutants of the tool itself: each must go red
mkdir -p "$w/mut"; sed "s/^bt_mask=.*/bt_mask='s\/zzzz\/zzzz\/'/" tools/golden-pull > "$w/mut/golden-pull"; chmod +x "$w/mut/golden-pull"
ck "bt-static MUTANT (address mask removed): run still PASSes" 0 "$(gbm bt-static)"; grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-static MUTANT (no mask): the address test goes RED (address found)" 0 $?
sed 's/getprop | grep -iE "bt|bluetooth|bdaddr" &&/rm -f x; getprop | grep -iE "bt|bluetooth|bdaddr" \&\&/' tools/golden-pull > "$w/mut/golden-pull"
ck "bt-static MUTANT (rm in a script): refused" 1 "$(gbm bt-static)"; grep -q '^REFUSED: bt-static script holds a forbidden word' "$w/out"; ck "bt-static MUTANT (rm): guard message" 0 $?; ck "bt-static MUTANT (rm): the injected script was never sent" 0 "$(grep -c 'rm -f x' "$LOG")"
sed 's/getprop | grep -iE "bt|bluetooth|bdaddr" &&/rm -f x; getprop | grep -iE "bt|bluetooth|bdaddr" \&\&/; s/|| fail "$cmd script holds a forbidden word: $s"; done; }/|| :; done; }/' tools/golden-pull > "$w/mut/golden-pull"
ck "bt guard MUTANT (guard body disabled + rm injected): the forbidden script IS sent" 1 "$(gbm bt-static >/dev/null; [ "$(grep -c 'rm -f x' "$LOG")" -gt 0 ] && echo 1 || echo 0)"
# bt-snoop-on
ck "bt-snoop-on PASS" 0 "$(gb bt-snoop-on)"; grep -q '^RESULT: PASS bt-snoop-on gaps=0' "$w/out"; ck "bt-snoop-on: RESULT line" 0 $?
ck "bt-snoop-on: files" "bt-snoop-off.txt bt-snoop-on.txt bt-snoop-set.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
gb bt-snoop-on >/dev/null; grep -qF 'setprop persist.bluetooth.btsnooplogmode full' "$LOG"; ck "bt-snoop-on: the setprop is sent" 0 $?
ck "bt-snoop-on: exactly one setprop" 1 "$(grep -c '^setprop ' "$LOG")"
grep -qF 'cmd bluetooth_manager wait-for-state:STATE_OFF' "$LOG" && grep -qF 'cmd bluetooth_manager wait-for-state:STATE_ON' "$LOG"; ck "bt-snoop-on: both waits sent" 0 $?
grep -qE "$bt_re" "$LOG"; ck "bt-snoop-on: sent script text has no forbidden word" 1 $?
for bt_mk in snoopmode state_off state_on; do ck "bt-snoop-on: no $bt_mk marker -> FAIL" 1 "$(FAKE_BT_MISS=$bt_mk gb bt-snoop-on)"; grep -q "^GAP: bt-snoop-on: .* has no '$bt_mk=ok' marker" "$w/out"; ck "bt-snoop-on: $bt_mk GAP named" 0 $?; done
ck "bt-snoop-on: failed wait does not abort (the enable script is still sent)" 1 "$(FAKE_BT_MISS=state_off gb bt-snoop-on >/dev/null; grep -c 'echo state_on=ok' "$LOG")"
ck "bt-snoop-on: argument -> ERROR" 2 "$(gb bt-snoop-on x)"
ck "bt-snoop-on dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-snoop-on >/dev/null; wc -c < "$LOG")"
# bt-static bt-dt / bt-where: /proc/device-tree is a symlink on the phone (kernel proc_symlink to /sys/firmware/devicetree/base); a plain `find /proc/device-tree` does not follow it and found n=0 on BP31 (gbtcold-20261029T111135Z-b2, gbp31b-20261029T081941Z-b2). Run the real scripts on a local fake tree behind a symlink.
dtt=$(mktemp -d "${TMPDIR:-/tmp}/dtt.XXXXXX"); mkdir -p "$dtt/real/soc/bluetooth" "$dtt/real/soc/other" "$dtt/emptyreal/soc"; echo hcicompat > "$dtt/real/soc/bluetooth/compatible"; echo secret > "$dtt/real/soc/bluetooth/local-bd-addr"; echo x > "$dtt/real/soc/other/mac-address"; ln -s "$dtt/real" "$dtt/dtlink"; ln -s "$dtt/emptyreal" "$dtt/dtempty"; ln -s "$dtt/real" "$dtt/dt link"; mkdir -p "$dtt/nb"; printf '#!/bin/sh\nexit 1\n' > "$dtt/nb/readlink"; chmod +x "$dtt/nb/readlink"
dt_script=$(sed -n "s/^[[:space:]]*b_dt='\(.*\)'\$/\1/p" tools/golden-pull); wh_script=$(sed -n "s/^[[:space:]]*b_where='\(.*\)'\$/\1/p" tools/golden-pull)
ck "bt-dt: script found in golden-pull" 1 "${dt_script:+1}"; ck "bt-where: script found in golden-pull" 1 "${wh_script:+1}"
dt_run() { local q="'"; bash -c "${1//\/proc\/device-tree/$q$2$q}" 2>&1; }
dt_out=$(dt_run "$dt_script" "$dtt/dtlink"); grep -q '^dt=ok$' <<<"$dt_out"; ck "bt-dt: symlinked DT root -> node found, dt=ok" 0 $?
grep -q "^== $dtt/real/soc/bluetooth\$" <<<"$dt_out"; ck "bt-dt: node path listed under the resolved root" 0 $?
grep -q '^dt-root=' <<<"$dt_out"; ck "bt-dt: dt-root line printed" 0 $?
grep -q -- "-> $dtt/real\$" <<<"$dt_out"; ck "bt-dt: ls -ld line shows the symlink" 0 $?
grep -q '^dt=ok$' <<<"$(dt_run "$dt_script" "$dtt/dt link")"; ck "bt-dt: root path with a space is quoted" 0 $?
grep -q '^dt=ok$' <<<"$(PATH=$dtt/nb:$PATH dt_run "$dt_script" "$dtt/real")"; ck "bt-dt: readlink failing -> falls back to the plain path" 0 $?
grep -qxF -e "-- $dtt/real/soc/bluetooth/local-bd-addr <skipped: address property>" <<<"$dt_out"; ck "bt-dt: address property skipped" 0 $?
grep -q secret <<<"$dt_out"; ck "bt-dt: address property value never dumped" 1 $?
dt_out=$(dt_run "$dt_script" "$dtt/dtempty"); grep -q '^dt=ok$' <<<"$dt_out"; ck "bt-dt: no matching node -> no dt=ok marker" 1 $?
grep -q '^dt: no bluetooth/btbcm/bt-/175b0000 node under ' <<<"$dt_out"; ck "bt-dt: no node -> named diagnostic line" 0 $?
wh_out=$(dt_run "$wh_script" "$dtt/dtlink"); grep -q "^$dtt/real/soc/other/mac-address\$" <<<"$wh_out"; ck "bt-where: symlinked DT root -> mac-address node listed" 0 $?
grep -q '^dt-where-root=' <<<"$wh_out"; ck "bt-where: dt-where-root line printed (the search is visible)" 0 $?
rm -rf "$dtt"
# bt-vnd-on (21m R22): the only writes are the 2 persist props + the BT toggle
ck "bt-vnd-on PASS" 0 "$(gb bt-vnd-on)"; grep -q '^RESULT: PASS bt-vnd-on gaps=0' "$w/out"; ck "bt-vnd-on: RESULT line" 0 $?
ck "bt-vnd-on: files" "bt-vendor-conf bt-vnd-off.txt bt-vnd-on.txt bt-vnd-set.txt" "$(cd "$w/gb/bt" && ls | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
gb bt-vnd-on >/dev/null; grep -qF 'setprop persist.bluetooth.vendor.btsnoop true' "$LOG" && grep -qF 'setprop persist.bluetooth.disable.accelerate.bt.on true' "$LOG"; ck "bt-vnd-on: both setprops sent" 0 $?
ck "bt-vnd-on: exactly two setprops, nothing else written" 2 "$(grep -o 'setprop [A-Za-z0-9._]*' "$LOG" | sort -u | wc -l)"
ck "bt-vnd-on: only the two named props" 0 "$(grep -o 'setprop [A-Za-z0-9._]*' "$LOG" | sort -u | grep -vcE '^setprop persist\.bluetooth\.(vendor\.btsnoop|disable\.accelerate\.bt\.on)$')"
grep -qF 'getprop persist.bluetooth.vendor.btsnoop' "$LOG" && grep -qF 'getprop persist.bluetooth.disable.accelerate.bt.on' "$LOG"; ck "bt-vnd-on: both props read back" 0 $?
grep -qF 'cmd bluetooth_manager wait-for-state:STATE_OFF' "$LOG" && grep -qF 'cmd bluetooth_manager wait-for-state:STATE_ON' "$LOG"; ck "bt-vnd-on: both waits sent" 0 $?
grep -qE "$bt_re" "$LOG"; ck "bt-vnd-on: sent script text has no forbidden word" 1 $?
for bt_mk in vnd state_off state_on; do ck "bt-vnd-on: no $bt_mk marker -> FAIL" 1 "$(FAKE_BT_MISS=$bt_mk gb bt-vnd-on)"; grep -q "^GAP: bt-vnd-on: .* has no '$bt_mk=ok' marker" "$w/out"; ck "bt-vnd-on: $bt_mk GAP named" 0 $?; done
ck "bt-vnd-on: failed wait does not abort (the enable script is still sent)" 1 "$(FAKE_BT_MISS=state_off gb bt-vnd-on >/dev/null; grep -c 'echo state_on=ok' "$LOG")"
ck "bt-vnd-on: argument -> ERROR" 2 "$(gb bt-vnd-on x)"
ck "bt-vnd-on dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-vnd-on >/dev/null; wc -c < "$LOG")"
# bt-vnd-on (review bt-vnd-debugfs-r1 F1/F2/F3). The fake adb only greps `echo X=ok` out of the sent text, so the && chain and the read-backs never ran: run the REAL b_set / bt_tog_script text locally against stubbed setprop/getprop/cmd. The lit_* texts are an independent pin (the allowlist): any added or changed command in golden-pull turns the exact-text rows red until a reviewer changes them here too.
lit_set='setprop persist.bluetooth.vendor.btsnoop true && setprop persist.bluetooth.disable.accelerate.bt.on true && echo "vendor.btsnoop=$(getprop persist.bluetooth.vendor.btsnoop)" && echo "disable.accelerate.bt.on=$(getprop persist.bluetooth.disable.accelerate.bt.on)" && [ "$(getprop persist.bluetooth.vendor.btsnoop)" = true ] && [ "$(getprop persist.bluetooth.disable.accelerate.bt.on)" = true ] && echo vnd=ok'
lit_off='cmd bluetooth_manager disable && echo disable=ok && cmd bluetooth_manager wait-for-state:STATE_OFF && echo state_off=ok'
lit_on='cmd bluetooth_manager enable && echo enable=ok && cmd bluetooth_manager wait-for-state:STATE_ON && echo state_on=ok'
lit_m='if [ -e /sys/kernel/debug/gpio ]; then echo debugfs=already-present; elif mountpoint -q /sys/kernel/debug; then echo debugfs=mountpoint-present-no-gpio; else mount -t debugfs debugfs /sys/kernel/debug 2>&1; mrc=$?; echo "mount-rc=$mrc"; fi; if [ -e /sys/kernel/debug/gpio ]; then if [ "${mrc:-0}" -ne 0 ]; then echo "mount_rc_nonzero: $mrc"; else grep debugfs /proc/mounts && echo mounted=ok; fi; else echo "gpio_missing: /sys/kernel/debug/gpio absent after the mount step (mount-rc=${mrc:-none})"; echo "getenforce=$(getenforce 2>&1)"; echo "id-Z=$(id -Z 2>&1)"; ls -ldZ /sys/kernel/debug 2>&1; echo "debugfs-in-filesystems=$(grep -c debugfs /proc/filesystems)"; dmesg | tail -n 40 | grep -iE "debugfs|avc"; fi'
lit_p='n=0; for d in /sys/kernel/debug/pinctrl/*; do [ -d "$d" ] || continue; n=$((n+1)); echo "== $d"; for f in pins pinmux-pins; do if [ -r "$d/$f" ]; then echo "-- $d/$f"; cat "$d/$f"; else echo "INFO: missing or unreadable $d/$f"; fi; done; done; t=0; for f in /proc/tty/driver/*; do if [ -r "$f" ]; then t=$((t+1)); echo "-- $f"; cat "$f"; fi; done; [ $t -gt 0 ] || echo "INFO: no readable /proc/tty/driver/* file"; [ $n -gt 0 ] && echo pinctrl=ok'
v_set=$(sed -n "/^[[:space:]]*bt-vnd-on)/,/^[[:space:]]*bt-debugfs-mount)/s/^[[:space:]]*b_set='\(.*\)'\$/\1/p" tools/golden-pull)
eval "$(sed -n '/^bt_tog_script()/,/^bt_mask_file/{/^bt_mask_file/!p}' tools/golden-pull)"   # the real bt_tog_script text (a function definition only)
ck "bt-vnd-on: b_set of the bt-vnd-on arm found in golden-pull" 1 "${v_set:+1}"
ck "bt-vnd-on: b_set in golden-pull is the pinned text" 0 "$([ "$v_set" = "$lit_set" ] && echo 0 || echo 1)"
ck "bt_tog_script off in golden-pull is the pinned text" 0 "$([ "$(bt_tog_script off)" = "$lit_off" ] && echo 0 || echo 1)"
ck "bt_tog_script on in golden-pull is the pinned text" 0 "$([ "$(bt_tog_script on)" = "$lit_on" ] && echo 0 || echo 1)"
gb bt-vnd-on >/dev/null
grep -qxF "$lit_set; echo __GP_RC=\$?" "$LOG"; ck "bt-vnd-on: the exact pinned b_set text is what is sent" 0 $?
grep -qxF "$lit_off; echo __GP_RC=\$?" "$LOG"; ck "bt-vnd-on: the exact pinned b_off text is what is sent" 0 $?
grep -qxF "$lit_on; echo __GP_RC=\$?" "$LOG"; ck "bt-vnd-on: the exact pinned b_on text is what is sent" 0 $?
# lrun DIR SCRIPT: run SCRIPT with PATH=DIR/bin only; every command that is not there lands in DIR/notfound.log (nothing outside the stub allowlist may run)
lrun() { PATH=$1/bin "$BASH" -c "command_not_found_handle() { echo \"\$1\" >> $1/notfound.log; return 127; }"$'\n'"$2" 2>&1; }
vb=$(mktemp -d "${TMPDIR:-/tmp}/vb.XXXXXX"); mkdir -p "$vb/bin"
cat > "$vb/bin/setprop" <<'F'
#!/bin/sh
d=${0%/*}/..
echo "setprop $*" >> "$d/calls"
if [ "${FAKE_SETPROP_FAIL-}" = 1 ] || [ "${FAKE_SETPROP_FAIL-}" = "$1" ]; then exit 1; fi
echo "$2" > "$d/prop.$1"
F
cat > "$vb/bin/getprop" <<'F'
#!/bin/sh
d=${0%/*}/..
echo "getprop $*" >> "$d/calls"
if [ "${FAKE_GET_FALSE-}" = all ] || [ "${FAKE_GET_FALSE-}" = "$1" ]; then echo false; exit 0; fi
read -r v < "$d/prop.$1" 2>/dev/null && echo "$v"
exit 0
F
cat > "$vb/bin/cmd" <<'F'
#!/bin/sh
d=${0%/*}/..
echo "cmd $*" >> "$d/calls"
case "$*" in *"${FAKE_CMD_FAIL:-@@none@@}"*) exit 1 ;; esac
exit 0
F
chmod +x "$vb"/bin/*
vrun() { rm -f "$vb/calls" "$vb/notfound.log" "$vb"/prop.*; lrun "$vb" "$1"; }
P1=persist.bluetooth.vendor.btsnoop; P2=persist.bluetooth.disable.accelerate.bt.on
vo=$(vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: both props true -> vnd=ok" 0 $?
grep -qx "setprop $P1 true" "$vb/calls" && grep -qx "setprop $P2 true" "$vb/calls"; ck "bt-vnd-on local: the two setprops carry the value true" 0 $?
ck "bt-vnd-on local: exactly two setprop calls" 2 "$(grep -c '^setprop ' "$vb/calls")"
ck "bt-vnd-on local: no command outside the stub allowlist ran" 0 "$([ -e "$vb/notfound.log" ] && echo 1 || echo 0)"
vo=$(FAKE_GET_FALSE=$P1 vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: read-back of the vendor.btsnoop prop false -> no vnd=ok" 1 $?
vo=$(FAKE_GET_FALSE=$P2 vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: read-back of the accelerate prop false -> no vnd=ok" 1 $?
vo=$(FAKE_GET_FALSE=all vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: both read-backs false -> no vnd=ok" 1 $?
vo=$(FAKE_SETPROP_FAIL=1 vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: first setprop fails -> no vnd=ok" 1 $?
ck "bt-vnd-on local: first setprop fails -> the second setprop is NOT called" 1 "$(grep -c '^setprop ' "$vb/calls")"
vo=$(FAKE_SETPROP_FAIL=$P2 vrun "$v_set"); grep -qx 'vnd=ok' <<<"$vo"; ck "bt-vnd-on local: second setprop fails -> no vnd=ok" 1 $?
for tg in off on; do
	tscr=$(bt_tog_script $tg); tmk=state_$tg; twait=STATE_${tg^^}; tcmd=disable; [ $tg = on ] && tcmd=enable
	to=$(vrun "$tscr"); grep -qx "$tmk=ok" <<<"$to"; ck "bt_tog_script $tg local: clean -> $tmk=ok" 0 $?
	grep -qx "cmd bluetooth_manager $tcmd" "$vb/calls" && grep -qx "cmd bluetooth_manager wait-for-state:$twait" "$vb/calls"; ck "bt_tog_script $tg local: $tcmd and the wait are called" 0 $?
	ck "bt_tog_script $tg local: no command outside the stub allowlist ran" 0 "$([ -e "$vb/notfound.log" ] && echo 1 || echo 0)"
	to=$(FAKE_CMD_FAIL=bluetooth_manager vrun "$tscr"); grep -qx "$tmk=ok" <<<"$to"; ck "bt_tog_script $tg local: every cmd fails -> no $tmk=ok" 1 $?
	to=$(FAKE_CMD_FAIL=wait-for-state vrun "$tscr"); grep -qx "$tmk=ok" <<<"$to"; ck "bt_tog_script $tg local: failing wait -> no $tmk=ok" 1 $?
	to=$(FAKE_CMD_FAIL=$tcmd vrun "$tscr"); grep -qx "$tmk=ok" <<<"$to"; ck "bt_tog_script $tg local: failing $tcmd -> no $tmk=ok" 1 $?
	ck "bt_tog_script $tg local: failing $tcmd -> the wait is NOT called" 0 "$(grep -c wait-for-state "$vb/calls")"
done
rm -rf "$vb"
# bt_guard mutants of the bt-vnd-on arm: a forbidden word in any of its three scripts must refuse the run before any adb call (F3)
for bt_m in "b_set:s/b_set='setprop persist.bluetooth.vendor/b_set='rm -f x; setprop persist.bluetooth.vendor/" "b_off:s/echo 'cmd bluetooth_manager disable/echo 'rm -f x; cmd bluetooth_manager disable/" "b_on:s/echo 'cmd bluetooth_manager enable/echo 'rm -f x; cmd bluetooth_manager enable/"; do
	sed "${bt_m#*:}" tools/golden-pull > "$w/mut/golden-pull"
	ck "bt-vnd-on MUTANT (rm in ${bt_m%%:*}): refused" 1 "$(gbm bt-vnd-on)"; grep -q '^REFUSED: bt-vnd-on script holds a forbidden word' "$w/out"; ck "bt-vnd-on MUTANT (rm in ${bt_m%%:*}): guard message" 0 $?
	ck "bt-vnd-on MUTANT (rm in ${bt_m%%:*}): the injected script was never sent" 0 "$(grep -c 'rm -f x' "$LOG")"
done
sed -e 's/bt_guard "$b_set" "$b_off" "$b_on"; bt_init/bt_guard "$b_set"; bt_init/' -e "s/echo 'cmd bluetooth_manager enable/echo 'rm -f x; cmd bluetooth_manager enable/" tools/golden-pull > "$w/mut/golden-pull"
ck "bt-vnd-on guard MUTANT (guard only on b_set + rm in b_on): the forbidden script IS sent (the rm rows above can go red)" 1 "$(gbm bt-vnd-on >/dev/null; [ "$(grep -c 'rm -f x' "$LOG")" -gt 0 ] && echo 1 || echo 0)"
# bt-debugfs-mount (21m R22): runtime mount + pinctrl/tty reads; host side
ck "bt-debugfs-mount PASS" 0 "$(gb bt-debugfs-mount)"; grep -q '^RESULT: PASS bt-debugfs-mount gaps=0' "$w/out"; ck "bt-debugfs-mount: RESULT line" 0 $?
ck "bt-debugfs-mount: files" "bt-debug-pinctrl.txt bt-debugfs-mount.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
gb bt-debugfs-mount >/dev/null; grep -qF 'mount -t debugfs debugfs /sys/kernel/debug' "$LOG"; ck "bt-debugfs-mount: the mount command is sent" 0 $?
grep -qE "$bt_re" "$LOG"; ck "bt-debugfs-mount: sent script text has no forbidden word" 1 $?
ck "bt-debugfs-mount: no mounted marker -> FAIL" 1 "$(FAKE_BT_MISS=mounted gb bt-debugfs-mount)"; grep -q "^GAP: bt-debugfs-mount: bt/bt-debugfs-mount.txt has no 'mounted=ok' marker" "$w/out"; ck "bt-debugfs-mount: mounted GAP named" 0 $?
ck "bt-debugfs-mount: no pinctrl marker -> FAIL" 1 "$(FAKE_BT_MISS=pinctrl gb bt-debugfs-mount)"; grep -q "^GAP: bt-debugfs-mount: bt/bt-debug-pinctrl.txt has no 'pinctrl=ok' marker" "$w/out"; ck "bt-debugfs-mount: pinctrl GAP named" 0 $?
ck "bt-debugfs-mount: INFO lines in the pinctrl file -> PASS with info=2" 0 "$(FAKE_BT_PININFO=2 gb bt-debugfs-mount)"; grep -q '^RESULT: PASS bt-debugfs-mount gaps=0 .* info=2$' "$w/out"; ck "bt-debugfs-mount: info=2 counted on RESULT" 0 $?; grep -q '^INFO: bt-debugfs-mount: 2 INFO line' "$w/out"; ck "bt-debugfs-mount: INFO named on stderr" 0 $?
for pin_n in 1 3; do ck "bt-debugfs-mount: $pin_n INFO line(s) in the pinctrl file -> PASS" 0 "$(FAKE_BT_PININFO=$pin_n gb bt-debugfs-mount)"; grep -q "^RESULT: PASS bt-debugfs-mount gaps=0 .* info=$pin_n\$" "$w/out"; ck "bt-debugfs-mount: info=$pin_n counted on RESULT" 0 $?; grep -q "^INFO: bt-debugfs-mount: $pin_n INFO line" "$w/out"; ck "bt-debugfs-mount: $pin_n INFO line(s) named on stderr" 0 $?; done
ck "bt-debugfs-mount: no INFO line in the pinctrl file -> PASS" 0 "$(FAKE_BT_PININFO=0 gb bt-debugfs-mount)"; grep -q '^RESULT: PASS bt-debugfs-mount gaps=0 .* info=0$' "$w/out"; ck "bt-debugfs-mount: no INFO line -> info=0 on RESULT" 0 $?; grep -q '^INFO: ' "$w/out"; ck "bt-debugfs-mount: no INFO line -> nothing named on stderr" 1 $?
ck "bt-debugfs-mount: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gb bt-debugfs-mount)"
ck "bt-debugfs-mount: argument -> ERROR" 2 "$(gb bt-debugfs-mount x)"
ck "bt-debugfs-mount dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-debugfs-mount >/dev/null; wc -c < "$LOG")"
# exact sent text of the debugfs arm (F2, pinned lit_m / lit_p above) and the bt_guard mutants (F3)
gb bt-debugfs-mount >/dev/null
grep -qxF "$lit_m; echo __GP_RC=\$?" "$LOG"; ck "bt-debugfs-mount: the exact pinned b_m text is what is sent" 0 $?
grep -qxF "$lit_p; echo __GP_RC=\$?" "$LOG"; ck "bt-debugfs-mount: the exact pinned b_p text is what is sent" 0 $?
for bt_m in "b_m:s/b_m='if \[ -e/b_m='rm -f x; if [ -e/" "b_p:s/b_p='n=0;/b_p='rm -f x; n=0;/"; do
	sed "${bt_m#*:}" tools/golden-pull > "$w/mut/golden-pull"
	ck "bt-debugfs-mount MUTANT (rm in ${bt_m%%:*}): refused" 1 "$(gbm bt-debugfs-mount)"; grep -q '^REFUSED: bt-debugfs-mount script holds a forbidden word' "$w/out"; ck "bt-debugfs-mount MUTANT (rm in ${bt_m%%:*}): guard message" 0 $?
	ck "bt-debugfs-mount MUTANT (rm in ${bt_m%%:*}): the injected script was never sent" 0 "$(grep -c 'rm -f x' "$LOG")"
done
sed -e 's/bt_guard "$b_m" "$b_p"; bt_init/bt_guard "$b_m"; bt_init/' -e "s/b_p='n=0;/b_p='rm -f x; n=0;/" tools/golden-pull > "$w/mut/golden-pull"
ck "bt-debugfs-mount guard MUTANT (guard only on b_m + rm in b_p): the forbidden script IS sent (the rm rows above can go red)" 1 "$(gbm bt-debugfs-mount >/dev/null; [ "$(grep -c 'rm -f x' "$LOG")" -gt 0 ] && echo 1 || echo 0)"
# the real scripts on a local fake tree (paths substituted; PATH holds only the stubs + grep/cat/ls/tail/sed: anything else is logged in notfound.log, review bt-vnd-debugfs-r1 F2)
dbt=$(mktemp -d "${TMPDIR:-/tmp}/dbt.XXXXXX"); mkdir -p "$dbt/bin" "$dbt/dbg" "$dbt/tty"; : > "$dbt/mounts"; echo 'nodev	debugfs' > "$dbt/fs"
cat > "$dbt/bin/mount" <<F
#!/bin/sh
echo "\$@" >> $dbt/mount.log
if [ -n "\${MOUNT_RC1_GPIO-}" ]; then : > $dbt/dbg/gpio; echo "mount: warning"; exit 1; fi
if [ -n "\${MOUNT_NOGPIO-}" ]; then exit 0; fi
if [ -n "\${MOUNT_OK-}" ]; then : > $dbt/dbg/gpio; exit 0; fi
echo "mount: \$4: Permission denied."
exit 255
F
printf '#!/bin/sh\nexit ${MP_RC-1}\n' > "$dbt/bin/mountpoint"
printf '#!/bin/sh\necho Enforcing\n' > "$dbt/bin/getenforce"; printf '#!/bin/sh\necho u:r:su:s0\n' > "$dbt/bin/id"; printf '#!/bin/sh\necho "avc: denied { mount } for comm=su name=debugfs"\n' > "$dbt/bin/dmesg"; chmod +x "$dbt"/bin/*
for c in grep cat ls tail sed; do ln -s "$(command -v "$c")" "$dbt/bin/$c"; done
m_script=$(sed -n "s/^[[:space:]]*b_m='\(.*\)'\$/\1/p" tools/golden-pull); p_script=$(sed -n "s/^[[:space:]]*b_p='\(.*\)'\$/\1/p" tools/golden-pull)
ck "bt-debugfs-mount: mount script found in golden-pull" 1 "${m_script:+1}"; ck "bt-debugfs-mount: pinctrl script found in golden-pull" 1 "${p_script:+1}"
ck "bt-debugfs-mount: b_m in golden-pull is the pinned text" 0 "$([ "$m_script" = "$lit_m" ] && echo 0 || echo 1)"; ck "bt-debugfs-mount: b_p in golden-pull is the pinned text" 0 "$([ "$p_script" = "$lit_p" ] && echo 0 || echo 1)"
dbnf() { [ -e "$dbt/notfound.log" ] && echo 1 || echo 0; }   # 1 = a command outside the stub allowlist ran (name in notfound.log)
dbsub() { local x=${1//\/sys\/kernel\/debug/$dbt/dbg}; x=${x//\/proc\/mounts/$dbt/mounts}; x=${x//\/proc\/filesystems/$dbt/fs}; x=${x//\/proc\/tty\/driver/$dbt/tty}; rm -f "$dbt/notfound.log"; lrun "$dbt" "$x"; }
rm -f "$dbt/mount.log"; mo=$(dbsub "$m_script")
grep -q 'mount: .*Permission denied' <<<"$mo"; ck "debugfs mount fails: the exact mount error is kept" 0 $?
grep -q '^mount-rc=255$' <<<"$mo"; ck "debugfs mount fails: mount rc kept" 0 $?
grep -q '^mounted=ok$' <<<"$mo"; ck "debugfs mount fails: no mounted=ok marker" 1 $?
grep -q '^getenforce=Enforcing$' <<<"$mo" && grep -q '^id-Z=u:r:su:s0$' <<<"$mo" && grep -q '^debugfs-in-filesystems=1$' <<<"$mo" && grep -q '^gpio_missing: ' <<<"$mo" && grep -q '^avc: denied' <<<"$mo"; ck "debugfs mount fails: getenforce, id -Z, filesystems count, avc dmesg line recorded" 0 $?
grep -q 'dbg' <<<"$(grep -E '^(d|-)' <<<"$mo")"; ck "debugfs mount fails: ls -ldZ line recorded" 0 $?
ck "debugfs mount fails: no command outside the stub allowlist ran" 0 "$(dbnf)"
ck "debugfs mount fails: exactly one mount call, exactly -t debugfs debugfs <dir>" "-t debugfs debugfs $dbt/dbg" "$(cat "$dbt/mount.log")"
# F5: mount rc 1 but gpio appears -> no marker, mount_rc_nonzero named; mount rc 0 but no gpio -> gpio_missing with mount-rc=0; debugfs already a mountpoint without gpio -> no second mount
rm -f "$dbt/mount.log" "$dbt/dbg/gpio"; mo=$(MOUNT_RC1_GPIO=1 dbsub "$m_script"); rm -f "$dbt/dbg/gpio"
grep -q '^mounted=ok$' <<<"$mo"; ck "debugfs mount rc 1 but gpio appears: no mounted=ok" 1 $?
grep -q '^mount_rc_nonzero: 1$' <<<"$mo"; ck "debugfs mount rc 1 but gpio appears: mount_rc_nonzero: 1 line" 0 $?
grep -q '^mount-rc=1$' <<<"$mo"; ck "debugfs mount rc 1 but gpio appears: mount-rc=1 kept" 0 $?
ck "debugfs mount rc 1 but gpio appears: no command outside the stub allowlist ran" 0 "$(dbnf)"
rm -f "$dbt/mount.log"; mo=$(MOUNT_NOGPIO=1 dbsub "$m_script")
grep -q '^mounted=ok$' <<<"$mo"; ck "debugfs mount rc 0 but no gpio: no mounted=ok" 1 $?
grep -q '^gpio_missing: .*mount-rc=0)$' <<<"$mo"; ck "debugfs mount rc 0 but no gpio: gpio_missing line with mount-rc=0" 0 $?
grep -q '^mount-rc=0$' <<<"$mo"; ck "debugfs mount rc 0 but no gpio: mount-rc=0 line" 0 $?
rm -f "$dbt/mount.log"; mo=$(MP_RC=0 dbsub "$m_script")
grep -q '^debugfs=mountpoint-present-no-gpio$' <<<"$mo"; ck "debugfs is a mountpoint without gpio: marker line" 0 $?
grep -q '^mounted=ok$' <<<"$mo"; ck "debugfs is a mountpoint without gpio: no mounted=ok" 1 $?
grep -q '^gpio_missing: .*mount-rc=none)$' <<<"$mo"; ck "debugfs is a mountpoint without gpio: gpio_missing with mount-rc=none" 0 $?
ck "debugfs is a mountpoint without gpio: mount not called" 0 "$([ -e "$dbt/mount.log" ] && echo 1 || echo 0)"
ck "debugfs is a mountpoint without gpio: no command outside the stub allowlist ran" 0 "$(dbnf)"
echo 'debugfs /sys/kernel/debug debugfs rw 0 0' > "$dbt/mounts"; rm -f "$dbt/mount.log"; mo=$(MOUNT_OK=1 dbsub "$m_script")
grep -q '^mounted=ok$' <<<"$mo" && grep -q '^mount-rc=0$' <<<"$mo" && grep -q '^debugfs /sys/kernel/debug debugfs' <<<"$mo"; ck "debugfs mount ok: mounted=ok + the /proc/mounts line" 0 $?
ck "debugfs mount ok: mount was called with -t debugfs" 1 "$(grep -c -- '-t debugfs debugfs' "$dbt/mount.log")"
ck "debugfs mount ok: exactly one mount call" "-t debugfs debugfs $dbt/dbg" "$(cat "$dbt/mount.log")"; ck "debugfs mount ok: no command outside the stub allowlist ran" 0 "$(dbnf)"
rm -f "$dbt/mount.log"; mo=$(dbsub "$m_script")
grep -q '^debugfs=already-present$' <<<"$mo" && grep -q '^mounted=ok$' <<<"$mo"; ck "debugfs already mounted: marker without a mount call" 0 $?
ck "debugfs already mounted: mount not called" 0 "$([ -e "$dbt/mount.log" ] && echo 1 || echo 0)"
po=$(dbsub "$p_script"); grep -q '^pinctrl=ok$' <<<"$po"; ck "pinctrl: no pinctrl dir -> no pinctrl=ok marker" 1 $?
mkdir -p "$dbt/dbg/pinctrl/a" "$dbt/dbg/pinctrl/b"; echo 'pin 0 (gpa0-0)' > "$dbt/dbg/pinctrl/a/pins"; echo 'pin 0 (gpa0-0) 175b0000.serial' > "$dbt/dbg/pinctrl/a/pinmux-pins"; echo 'pin 1 (gpa0-1)' > "$dbt/dbg/pinctrl/b/pins"; echo 'ttySAC16 uart' > "$dbt/tty/serial"
po=$(dbsub "$p_script")
grep -q '^pinctrl=ok$' <<<"$po" && grep -q '^pin 0 (gpa0-0) 175b0000.serial$' <<<"$po" && grep -q '^ttySAC16 uart$' <<<"$po"; ck "pinctrl: pins, pinmux-pins and the tty driver file are copied, marker set" 0 $?
grep -q '^INFO: no readable' <<<"$po"; ck "pinctrl: a readable tty driver file -> no 'no readable' INFO line" 1 $?
ck "pinctrl: no command outside the stub allowlist ran" 0 "$(dbnf)"; ck "pinctrl: nothing written (every pinctrl and tty file unchanged)" "pin 0 (gpa0-0) pin 0 (gpa0-0) 175b0000.serial pin 1 (gpa0-1) ttySAC16 uart" "$(cat "$dbt/dbg/pinctrl/a/pins" "$dbt/dbg/pinctrl/a/pinmux-pins" "$dbt/dbg/pinctrl/b/pins" "$dbt/tty/serial" | tr '\n' ' ' | sed 's/ $//')"
grep -q "^INFO: missing or unreadable $dbt/dbg/pinctrl/b/pinmux-pins\$" <<<"$po"; ck "pinctrl: a missing pinmux-pins is an INFO line naming it" 0 $?
rm -f "$dbt/tty/serial"; po=$(dbsub "$p_script"); grep -q '^INFO: no readable .*/tty/\* file$' <<<"$po"; ck "pinctrl: no tty driver file -> INFO line" 0 $?
rm -rf "$dbt"
# bt-toggle
ck "bt-toggle off PASS" 0 "$(gb bt-toggle off)"; ck "bt-toggle on PASS" 0 "$(gb bt-toggle on)"; ck "bt-toggle on: file" "bt-toggle-on.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
ck "bt-toggle off: no state_off -> FAIL" 1 "$(FAKE_BT_MISS=state_off gb bt-toggle off)"; ck "bt-toggle on: no state_on -> FAIL" 1 "$(FAKE_BT_MISS=state_on gb bt-toggle on)"
ck "bt-toggle: no arg -> ERROR" 2 "$(gb bt-toggle)"; ck "bt-toggle: bad arg -> ERROR" 2 "$(gb bt-toggle toggle)"; ck "bt-toggle: two args -> ERROR" 2 "$(gb bt-toggle off on)"
ck "bt-toggle dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-toggle off >/dev/null; wc -c < "$LOG")"
# bt-debug
ck "bt-debug off PASS" 0 "$(gb bt-debug off)"; ck "bt-debug off: files" "bt-debug-off-clk.txt bt-debug-off-gpio.txt bt-debug-off-regulator.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
ck "bt-debug on PASS" 0 "$(gb bt-debug on)"
ck "bt-debug: debugfs unreadable -> FAIL" 1 "$(FAKE_BT_MISS=debugfs gb bt-debug on)"; grep -q "^GAP: bt-debug: bt/bt-debug-on-gpio.txt has no 'debugfs=ok' marker" "$w/out"; ck "bt-debug: GAP named" 0 $?
ck "bt-debug: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gb bt-debug off)"; ck "bt-debug: no arg -> ERROR" 2 "$(gb bt-debug)"; ck "bt-debug: bad arg -> ERROR" 2 "$(gb bt-debug maybe)"
ck "bt-debug dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-debug on >/dev/null; wc -c < "$LOG")"
# bt-debug clock read (21o request, AP-252, research clk-summary-reboot s.4): only the cached per-clock files of the two BT UART clocks, never clk_summary / clk_dump / clk_orphan* / clk_rate
: >"$LOG"; gb bt-debug on >/dev/null; grep -qE 'clk_summary|clk_dump|clk_orphan|clk_rate' "$LOG"; ck "bt-debug: never reads clk_summary / clk_dump / clk_orphan / clk_rate" 1 $?
bt_clk_line=$(grep -m1 '^d=/sys/kernel/debug/clk;' "$LOG"); ck "bt-debug: the clock script was sent" 1 "$([ -n "$bt_clk_line" ] && echo 1 || echo 0)"
ck "bt-debug: clock script cats exactly the cached files (literal list, AP-124)" "clk_enable_count clk_prepare_count clk_protect_count clk_phase clk_flags clk_min_rate clk_max_rate" "$(grep -o 'for f in [a-z_ ]*; do' <<<"$bt_clk_line" | sed 's/^for f in //; s/; do$//')"
ck "bt-debug: clock script names exactly MUX_APM_FUNC and DOUT_CLK_APM_USI1_UART" "MUX_APM_FUNC DOUT_CLK_APM_USI1_UART" "$(grep -o 'for c in [A-Z0-9_ ]*; do' <<<"$bt_clk_line" | sed 's/^for c in //; s/; do$//')"
ck "bt-debug: clock script ends with the debugfs=ok marker" "echo debugfs=ok; echo __GP_RC=\$?" "$(sed 's/.*; \(echo debugfs=ok; echo __GP_RC=\$?\)$/\1/' <<<"$bt_clk_line")"
# the SENT text runs for real against a fake debugfs tree (the fake adb only echoes markers, AP-248)
bt_fc=$w/fclk; rm -rf "$bt_fc"; for c in MUX_APM_FUNC DOUT_CLK_APM_USI1_UART; do mkdir -p "$bt_fc/$c"; for f in clk_enable_count clk_prepare_count clk_protect_count clk_phase clk_flags clk_min_rate clk_max_rate; do echo "v-$c-$f" > "$bt_fc/$c/$f"; done; done
bt_fcrun() { sed "s|^d=/sys/kernel/debug/clk;|d=$bt_fc;|" <<<"$bt_clk_line" | sh 2>&1; }
bt_fo=$(bt_fcrun); ck "bt-debug clock script (run on a fake tree without clk_possible_parents, like the parentless vclks): 14 cached values, no MISSING/UNREADABLE, debugfs=ok,  2 clk-dir evidence lines" "14 0 1 2" "$(grep -c '^[A-Z_0-9]* clk_[a-z_]* v-' <<<"$bt_fo") $(grep -cE '^(MISSING clk|UNREADABLE) ' <<<"$bt_fo") $(grep -cx 'debugfs=ok' <<<"$bt_fo") $(grep -c '^clk-dir ' <<<"$bt_fo")"
mv "$bt_fc/MUX_APM_FUNC" "$bt_fc/gone"; bt_fo=$(bt_fcrun); ck "bt-debug clock script: missing clock dir -> 'MISSING clk MUX_APM_FUNC' line, marker still printed" "1 1" "$(grep -cx 'MISSING clk MUX_APM_FUNC' <<<"$bt_fo") $(grep -cx 'debugfs=ok' <<<"$bt_fo")"
mv "$bt_fc/gone" "$bt_fc/MUX_APM_FUNC"; rm "$bt_fc/DOUT_CLK_APM_USI1_UART/clk_phase"; mkdir "$bt_fc/DOUT_CLK_APM_USI1_UART/clk_phase"; bt_fo=$(bt_fcrun); ck "bt-debug clock script: unreadable file -> 'UNREADABLE DOUT_CLK_APM_USI1_UART clk_phase' line" 1 "$(grep -cx 'UNREADABLE DOUT_CLK_APM_USI1_UART clk_phase' <<<"$bt_fo")"
ck "bt-debug: MISSING clk line in the file -> GAP and FAIL" 1 "$(FAKE_BT_CLKMISS=1 gb bt-debug on)"; grep -q "^GAP: bt-debug: bt/bt-debug-on-clk.txt: MISSING clk MUX_APM_FUNC" "$w/out"; ck "bt-debug: MISSING clk GAP named" 0 $?
ck "bt-debug: UNREADABLE line in the file -> GAP and FAIL" 1 "$(FAKE_BT_CLKUNREAD=1 gb bt-debug on)"; grep -q "^GAP: bt-debug: bt/bt-debug-on-clk.txt: UNREADABLE DOUT_CLK_APM_USI1_UART clk_phase" "$w/out"; ck "bt-debug: UNREADABLE GAP named" 0 $?
# bt-snoop-pull
ck "bt-snoop-pull PASS" 0 "$(gb bt-snoop-pull)"; grep -q '^RESULT: PASS bt-snoop-pull gaps=0' "$w/out"; ck "bt-snoop-pull: RESULT line" 0 $?
ck "bt-snoop-pull: files" "btsnoop_hci.log btsnoop_hci.log.last btsnoop_hci_vnd.log bt-snoop-size.txt" "$(cd "$w/gb/bt" && ls | sort | sed '/^bt-vendor-conf$/d' | tr '\n' ' ' | sed 's/ $//')"
gb bt-snoop-pull >/dev/null; grep -q 'info=0' "$w/out"; ck "bt-snoop-pull: PASS row counts info=0" 0 $?
grep -qE 'for f in .* /data/vendor/bluetooth/btsnoop_hci_vnd\.log; do' "$LOG"; ck "bt-snoop-pull: size script lists the vnd path" 0 $?
grep -q HCIDATA "$w/out"; ck "bt-snoop-pull: contents never printed" 1 $?
grep -q VNDDATA "$w/out"; ck "bt-snoop-pull: vnd contents never printed" 1 $?
ck "bt-snoop-pull: no vnd file -> PASS with INFO" 0 "$(FAKE_BT_NOVND=1 gb bt-snoop-pull)"; grep -q '^INFO: bt-snoop-pull: btsnoop_hci_vnd.log missing' "$w/out"; ck "bt-snoop-pull: vnd INFO named" 0 $?; grep -q 'info=1' "$w/out"; ck "bt-snoop-pull: vnd missing counted info=1" 0 $?
ck "bt-snoop-pull: no vnd file -> not pulled" 1 "$([ -e "$w/gb/bt/btsnoop_hci_vnd.log" ] && echo 0 || echo 1)"
ck "bt-snoop-pull: vnd other magic -> PASS with INFO" 0 "$(FAKE_BT_VNDMAGIC='othermag' gb bt-snoop-pull)"; grep -q '^INFO: bt-snoop-pull: btsnoop_hci_vnd.log does not start with the btsnoop magic' "$w/out"; ck "bt-snoop-pull: vnd magic INFO named" 0 $?; grep -q 'info=1' "$w/out"; ck "bt-snoop-pull: vnd magic counted info=1" 0 $?
ck "bt-snoop-pull: no last file -> PASS with INFO" 0 "$(FAKE_BT_NOLAST=1 gb bt-snoop-pull)"; grep -q '^INFO: bt-snoop-pull: btsnoop_hci.log.last missing' "$w/out"; ck "bt-snoop-pull: INFO named" 0 $?
ck "bt-snoop-pull: main file size 0 -> FAIL" 1 "$(FAKE_BT_SNOOPSZ=0 gb bt-snoop-pull)"; grep -q '^GAP: bt-snoop-pull: .* is missing or size 0' "$w/out"; ck "bt-snoop-pull: size 0 GAP named" 0 $?
ck "bt-snoop-pull: size 0 -> no pull of the main file" 0 "$(grep -c 'exec-out su -c cat /data/misc/bluetooth/logs/btsnoop_hci.log;' "$LOG")"
ck "bt-snoop-pull: main file missing -> FAIL" 1 "$(FAKE_BT_SNOOPSZ=missing gb bt-snoop-pull)"
ck "bt-snoop-pull: empty pull -> FAIL" 1 "$(FAKE_BT_EMPTYPULL=1 gb bt-snoop-pull)"
ck "bt-snoop-pull: wrong magic -> FAIL" 1 "$(FAKE_BT_MAGIC='notsnoop\0\0' gb bt-snoop-pull)"; grep -q '^GAP: bt-snoop-pull: btsnoop_hci.log does not start with the btsnoop magic' "$w/out"; ck "bt-snoop-pull: magic GAP named" 0 $?
ck "bt-snoop-pull: size marker missing -> FAIL" 1 "$(FAKE_BT_MISS=size gb bt-snoop-pull)"
ck "bt-snoop-pull: argument -> ERROR" 2 "$(gb bt-snoop-pull x)"
# vnd cat guard (bt-snoop-vnd-r1 F3): the b_sz script holds the vnd PATH but not the text "cat /data/vendor/...", so only the bt_guard argument "cat $bv" can trip a forbid word on it
sed 's#bt_forbid=.pm clear|#bt_forbid='"'"'cat /data/vendor/bluetooth/btsnoop_hci_vnd\\.log|pm clear|#' tools/golden-pull > "$w/mut/golden-pull"; chmod +x "$w/mut/golden-pull"
grep -qF 'bt_forbid='"'"'cat /data/vendor/bluetooth/btsnoop_hci_vnd' "$w/mut/golden-pull"; ck "bt-snoop-pull vnd guard: mutant 1 (forbid word added) was built" 0 $?
ck "bt-snoop-pull vnd guard: forbid word on 'cat <vnd>' -> FAIL" 1 "$(gbm bt-snoop-pull)"; grep -q 'forbidden word' "$w/out"; ck "bt-snoop-pull vnd guard: 'forbidden word' named" 0 $?
ck "bt-snoop-pull vnd guard: refused before the size script and any pull is sent" 0 "$(grep -cE 'echo size=ok|exec-out' "$LOG")"
sed 's#bt_forbid=.pm clear|#bt_forbid='"'"'cat /data/vendor/bluetooth/btsnoop_hci_vnd\\.log|pm clear|#; s# "cat \$bv"; bt_init#; bt_init#' tools/golden-pull > "$w/mut/golden-pull"
! grep -qF '"cat $bv"; bt_init' "$w/mut/golden-pull"; ck "bt-snoop-pull vnd guard: mutant 2 (guard arg dropped) was built" 0 $?
ck "bt-snoop-pull vnd guard: mutant 2 (arg dropped) is NOT stopped by the guard -> PASS (shows row 1 binds the arg)" 0 "$(gbm bt-snoop-pull)"; grep -q 'forbidden word' "$w/out"; ck "bt-snoop-pull vnd guard: mutant 2 has no 'forbidden word'" 1 $?
# vnd size 0 / empty pull / rc != 0 (bt-snoop-vnd-r1 F5); golden-pull R: empty body -> GAP "pull of '...' is empty", rc sentinel != 0 -> GAP "pull of '...' failed"
ck "bt-snoop-pull: vnd size 0 -> PASS with INFO" 0 "$(FAKE_BT_VNDSZ=0 gb bt-snoop-pull)"; grep -q '^INFO: bt-snoop-pull: btsnoop_hci_vnd.log missing or size 0' "$w/out"; ck "bt-snoop-pull: vnd size 0 INFO named" 0 $?; grep -q 'info=1' "$w/out"; ck "bt-snoop-pull: vnd size 0 counted info=1" 0 $?
ck "bt-snoop-pull: vnd size 0 -> not pulled" 1 "$([ -e "$w/gb/bt/btsnoop_hci_vnd.log" ] && echo 0 || echo 1)"; ck "bt-snoop-pull: vnd size 0 -> no vnd cat sent" 0 "$(grep -c 'exec-out su -c cat /data/vendor/bluetooth/btsnoop_hci_vnd.log;' "$LOG")"
ck "bt-snoop-pull: vnd empty pull -> FAIL" 1 "$(FAKE_BT_VNDEMPTY=1 gb bt-snoop-pull)"; grep -q "^GAP: pull of 'cat /data/vendor/bluetooth/btsnoop_hci_vnd.log' is empty" "$w/out"; ck "bt-snoop-pull: vnd empty pull GAP names the vnd file" 0 $?
grep -q '^RESULT: FAIL bt-snoop-pull gaps=1' "$w/out"; ck "bt-snoop-pull: vnd empty pull counted gaps=1 on RESULT" 0 $?
ck "bt-snoop-pull: vnd rc 1 -> FAIL" 1 "$(FAKE_BT_VNDRC=1 gb bt-snoop-pull)"; grep -q "^GAP: pull of 'cat /data/vendor/bluetooth/btsnoop_hci_vnd.log' failed (rc sentinel '__GP_RC=1'" "$w/out"; ck "bt-snoop-pull: vnd rc 1 GAP names the vnd file and the sentinel" 0 $?
grep -q '^RESULT: FAIL bt-snoop-pull gaps=1' "$w/out"; ck "bt-snoop-pull: vnd rc 1 counted gaps=1 on RESULT" 0 $?
# each F5 row must go RED against a one-line golden-pull mutant
sed 's/ \[ "\$bz" -eq 0 \]; then/ false; then/' tools/golden-pull > "$w/mut/golden-pull"; cmp -s tools/golden-pull "$w/mut/golden-pull"; ck "bt-snoop-pull MUTANT (size-0 test removed): mutant differs" 1 $?
ck "bt-snoop-pull MUTANT (size-0 test removed): vnd size 0 is pulled (the size-0 row goes RED)" 1 "$(FAKE_BT_VNDSZ=0 gbm bt-snoop-pull >/dev/null; grep -c 'exec-out su -c cat /data/vendor/bluetooth/btsnoop_hci_vnd.log;' "$LOG")"
sed "s/\[ -s \"\$f\" \] || { echo \"GAP: pull of '\$1' is empty\" >&2; bad=\$((bad + 1)); }; }/: ; }/" tools/golden-pull > "$w/mut/golden-pull"; cmp -s tools/golden-pull "$w/mut/golden-pull"; ck "bt-snoop-pull MUTANT (R empty-pull GAP removed): mutant differs" 1 $?
ck "bt-snoop-pull MUTANT (R empty-pull GAP removed): vnd empty pull no longer FAILs (the empty row goes RED)" 0 "$(FAKE_BT_VNDEMPTY=1 gbm bt-snoop-pull)"
sed 's/then truncate -s -11 "\$f"; else echo "GAP: pull of/then truncate -s -11 "$f"; elif :; then :; else echo "GAP: pull of/' tools/golden-pull > "$w/mut/golden-pull"; cmp -s tools/golden-pull "$w/mut/golden-pull"; ck "bt-snoop-pull MUTANT (R rc GAP unreachable): mutant differs" 1 $?
ck "bt-snoop-pull MUTANT (R rc GAP unreachable): vnd rc 1 no longer FAILs (the rc row goes RED)" 0 "$(FAKE_BT_VNDRC=1 gbm bt-snoop-pull)"
gb bt-snoop-pull >/dev/null; grep -qE "$bt_re" "$LOG"; ck "bt-snoop-pull: sent script text has no forbidden word" 1 $?
ck "bt-snoop-pull dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-snoop-pull >/dev/null; wc -c < "$LOG")"
gp --dry-run --out "$w/gbd" bt-snoop-pull >/dev/null; grep -qF 'cat /data/vendor/bluetooth/btsnoop_hci_vnd.log' "$w/out"; ck "bt-snoop-pull dry-run: lists the vnd cat" 0 $?
# bt-stty (golden-bt-cold): one read-only stty, GAP unless the command succeeded
ck "bt-stty PASS" 0 "$(gb bt-stty)"; ck "bt-stty: file" "bt-stty.txt bt-vendor-conf" "$(cd "$w/gb/bt" && ls | tr '\n' ' ' | sed 's/ $//')"
: >"$LOG"; gb bt-stty >/dev/null; grep -qF 'stty -F /dev/ttySAC16 -a' "$LOG"; ck "bt-stty: sends exactly the stty -F /dev/ttySAC16 -a read" 0 $?
ck "bt-stty: stty failed (no stty=ok marker) -> FAIL" 1 "$(FAKE_BT_MISS='stty' gb bt-stty)"; grep -q "^GAP: bt-stty: bt/bt-stty.txt has no 'stty=ok' marker" "$w/out"; ck "bt-stty: GAP named" 0 $?
ck "bt-stty: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gb bt-stty)"; ck "bt-stty: argument -> ERROR" 2 "$(gb bt-stty x)"
ck "bt-stty dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-stty >/dev/null; wc -c < "$LOG")"
# bt-logs
ck "bt-logs PASS" 0 "$(gb bt-logs)"; ck "bt-logs: files" "bt-dmesg-filtered.txt bt-dmesg.txt bt-logcat-filtered.txt bt-logcat-size.txt bt-logcat.txt" "$(cd "$w/gb/bt" && ls | sort | sed '/^bt-vendor-conf$/d' | tr '\n' ' ' | sed 's/ $//')"
ck "bt-logs: su denied -> FAIL" 1 "$(FAKE_SU_DENY=1 gb bt-logs)"; ck "bt-logs: argument -> ERROR" 2 "$(gb bt-logs x)"
: >"$LOG"; gb bt-logs >/dev/null; grep -qF 'logcat -g' "$LOG"; ck "bt-logs: logcat -g (buffer sizes) is sent" 0 $?
ck "bt-logs dry-run: no adb call" 0 "$(gp --dry-run --out "$w/gbd" bt-logs >/dev/null; wc -c < "$LOG")"
ck "bt-logs: mock logs, files present" 0 "$(FAKE_BT_LOGS=1 gb bt-logs)"; grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-logs: no address (colon or bare 12-hex) in any output" 1 $?
grep -rq SECRET123 "$w/gb/bt/bt-dmesg-filtered.txt" "$w/gb/bt/bt-logcat-filtered.txt"; ck "bt-logs: serial/androidboot lines absent from the -filtered copies (review F1)" 1 $?
grep -q 'Bluetooth: hci0 addr <masked> up' "$w/gb/bt/bt-dmesg-filtered.txt"; ck "bt-logs: BT line kept with the address masked" 0 $?
grep -q 'uart line ok' "$w/gb/bt/bt-logcat-filtered.txt"; ck "bt-logs: harmless uart line kept" 0 $?
ck "bt-logs: no temp/raw file left" 0 "$(find "$w/gb/bt" -name "*tmp*" | wc -l)"
sed "s/^bt_mask=.*/bt_mask='s\/zzzz\/zzzz\/'/" tools/golden-pull > "$w/mut/golden-pull"; FAKE_BT_LOGS=1 gbm bt-logs >/dev/null; grep -rEq "$bt_addr_re" "$w/gb/bt"; ck "bt-logs MUTANT (mask removed): the address test goes RED" 0 $?
sed "s/grep -viE 'serial|imei|ssid|androidboot|cmdline'/cat/" tools/golden-pull > "$w/mut/golden-pull"; FAKE_BT_LOGS=1 gbm bt-logs >/dev/null; grep -rq SECRET123 "$w/gb/bt/bt-dmesg-filtered.txt"; ck "bt-logs MUTANT (serial filter removed): the SECRET test goes RED" 0 $?
# golden-bt.sh consent gate (review F5): exit 2 and no tool call unless GOLDEN_BT_CONSENT=yes
for bt_c in unset no; do : > "$LOG"; if [ "$bt_c" = unset ]; then (unset GOLDEN_BT_CONSENT; bash docs/golden/golden-bt.sh > "$w/out" 2>&1 </dev/null); else (GOLDEN_BT_CONSENT=no bash docs/golden/golden-bt.sh > "$w/out" 2>&1 </dev/null); fi; bt_rc=$?
	ck "golden-bt.sh consent $bt_c -> exit 2" 2 "$bt_rc"; grep -q '^RESULT: ERROR golden-bt refuses' "$w/out"; ck "golden-bt.sh consent $bt_c: RESULT ERROR line" 0 $?; ck "golden-bt.sh consent $bt_c: no adb/fastboot call" 0 "$(wc -c < "$LOG")"; done
# r1 F13: tools/third_party is gitignored and absent in a fresh worktree. Only the checks that BUILD AVB images with it (fboot-android --unattended, golden-unattended-image; up to the Wi-Fi section) need it:
# without it they are skipped with a loud SKIP line, counted in the RESULT line, and everything else (including all Wi-Fi checks) still runs. To run them: tools/fetch-android-tools.sh (or symlink the kernel clone's tools/third_party).
skips=0
if [ ! -f tools/third_party/mkbootimg.py ] || [ ! -f tools/third_party/avbtool.py ]; then echo "SKIP: tools/third_party missing (tools/fetch-android-tools.sh): the AVB-image checks of fboot-android --unattended / golden-unattended-image are NOT run"; skips=$((skips + 1))
else
mkdir -p "$w/s/tools" "$w/ui/src/etc" "$w/ui/src/sbin" "$w/ui/out" "$HOME/.android"
head -c 5000 /dev/urandom > "$w/ui/kernel"; printf 'hello\n' > "$w/ui/src/etc/a.txt"; head -c 300 /dev/urandom > "$w/ui/src/sbin/init"; chmod 755 "$w/ui/src/sbin/init"
(cd "$w/ui/src" && find . -mindepth 1 | sed 's|^\./||' | sort | cpio -o -H newc --quiet) 2>/dev/null > "$w/ui/rd.cpio"
mkimg() { python3 tools/third_party/mkbootimg.py --header_version 4 --kernel "$w/ui/kernel" --ramdisk "$1" --cmdline "" --output "$2" && python3 tools/third_party/avbtool.py add_hash_footer --partition_name boot --partition_size 131072 --image "$2" --salt 00; }   # footer, no key, fixed salt
lz4 -l -12 -c "$w/ui/rd.cpio" > "$w/ui/rd.lz4"; mkimg "$w/ui/rd.lz4" "$w/ui/base.img"
lz4 -l -1 -c "$w/ui/rd.cpio" > "$w/ui/rd1.lz4"; mkimg "$w/ui/rd1.lz4" "$w/ui/base1.img"   # a ramdisk the -12 pipeline cannot reproduce
python3 tools/third_party/mkbootimg.py --header_version 4 --kernel "$w/ui/kernel" --ramdisk "$w/ui/rd.lz4" --cmdline "" --output "$w/ui/nofoot.img"; truncate -s 131072 "$w/ui/nofoot.img"
sbase=$(sha256sum "$w/ui/base.img" | cut -d' ' -f1); s1=$(sha256sum "$w/ui/base1.img" | cut -d' ' -f1); sn=$(sha256sum "$w/ui/nofoot.img" | cut -d' ' -f1)
mkkey() { python3 -I -c 'import base64,os,sys; sys.stdout.write(base64.b64encode(os.urandom(524)).decode()+" "+sys.argv[1]+"\n")' "$1" > "$2"; }
mkkey test@host "$w/ui/key.pub"; mkkey other@host "$w/ui/other.pub"; : > "$w/ui/empty.pub"; cp "$w/ui/key.pub" "$HOME/.android/adbkey.pub"
printf -- '-----BEGIN RSA PRIV''ATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIV''ATE KEY-----\n' > "$w/ui/priv"; head -c 100 "$w/ui/key.pub" > "$w/ui/short.pub"; cat "$w/ui/key.pub" "$w/ui/other.pub" > "$w/ui/two.pub"
patch_tree() { mkdir -p "$1/tools"; sed "s/^PIN=[0-9a-f]*/PIN=$2/; s/^PIN_BP31=[0-9a-zA-Z]*/PIN_BP31=UNSET/" tools/fboot-android > "$1/tools/fboot-android"; chmod +x "$1/tools/fboot-android"; ln -sf "$PWD/tools/phone-lock" "$1/tools/phone-lock"; ln -sfn "$PWD/tools/third_party" "$1/tools/third_party"; }
patch_tree "$w/s" "$sbase"; patch_tree "$w/r" "$s1"; patch_tree "$w/n" "$sn"; patch_tree "$w/v" "$sbase"
for t in "$w/k" "$w/s" "$w/r" "$w/n"; do cp tools/golden-unattended-image "$t/tools/"; ln -sfn "$PWD/tools/third_party" "$t/tools/third_party"; done
gi() { "$w/s/tools/golden-unattended-image" "$@" > "$w/gi.out" 2>&1 </dev/null; echo $?; }
gik() { "$w/k/tools/golden-unattended-image" "$@" > "$w/gi.out" 2>&1 </dev/null; echo $?; }
ck "gui: wrong-PIN base refused" 1 "$(gik build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out")"; ck "gui: wrong PIN wrote nothing" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: wrong-PIN last line is RESULT: FAIL" "RESULT: FAIL" "$(tail -1 "$w/gi.out" | cut -c1-12)"
ck "gui: missing key refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/none.pub" --out "$w/ui/out")"
ck "gui: empty key refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/empty.pub" --out "$w/ui/out")"
ck "gui: private key file refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/priv" --out "$w/ui/out")"; grep -q 'private key' "$w/gi.out"; ck "gui: private key reason logged" 0 $?
ck "gui: short/odd key line refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/short.pub" --out "$w/ui/out")"
ck "gui: two-line key file refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/two.pub" --out "$w/ui/out")"; ck "gui: refusals wrote nothing" 0 "$(ls "$w/ui/out" | wc -l)"
git init -q "$w/gt"   # a throwaway work tree, never the repo itself (r2 N2: a broken check must not leave files in the clone)
# --build bp31 for golden-unattended-image: placeholder pin -> ERROR, no silent cp3a fallback
ck "gui: --build bp31 build without --base -> ERROR" 2 "$(gi build --build bp31 --key "$w/ui/key.pub" --out "$w/ui/out" --dry-run)"
ck "gui: --build bp31 with placeholder pin -> ERROR" 2 "$(gi build --build bp31 --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" --dry-run)"; grep -q 'no ^PIN_BP31=<sha256>' "$w/gi.out"; ck "gui: bp31 placeholder reason named" 0 $?
ck "gui: unknown --build -> ERROR" 2 "$(gi build --build zz --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" --dry-run)"
mkdir -p "$w/b31g/tools"; sed "s/^PIN_BP31=[A-Za-z0-9]*/PIN_BP31=$(sha256sum "$w/ui/base.img" | cut -d' ' -f1)/" tools/fboot-android > "$w/b31g/tools/fboot-android"; cp tools/golden-unattended-image "$w/b31g/tools/"; ln -sfn "$PWD/tools/third_party" "$w/b31g/tools/third_party"
ck "gui: --build bp31 reads PIN_BP31 (dry-run PASS)" 0 "$("$w/b31g/tools/golden-unattended-image" build --build bp31 --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" --dry-run > "$w/gi.out" 2>&1 </dev/null; echo $?)"
ck "gui: cp3a pin unaffected by PIN_BP31 (same base refused)" 1 "$("$w/b31g/tools/golden-unattended-image" build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" --dry-run > "$w/gi.out" 2>&1 </dev/null; echo $?)"
ck "gui: --out inside a git work tree refused" 1 "$(gi build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/gt/out")"; ck "gui: git work tree: nothing created" 1 "$(test -e "$w/gt/out" && echo 0 || echo 1)"
ck "gui: no git -> --out check ERROR, not skipped" 2 "$(PATH=/nonexistent "$(command -v python3)" -I "$w/s/tools/golden-unattended-image" build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?)"; grep -q 'cannot run git' "$w/gi.out"; ck "gui: no-git reason logged" 0 $?
# r2 N1: the three AVB-boundary checks, each on a base whose PIN matches it
avbset() { python3 -I -c 'import struct,sys; b=bytearray(open(sys.argv[1],"rb").read()); o=len(b)-64+12; v=struct.unpack_from("!Q",b,o)[0]; struct.pack_into("!Q",b,o,eval(sys.argv[3],{"v":v})); open(sys.argv[2],"wb").write(b)' "$@"; }
avbset "$w/ui/base.img" "$w/ui/odd.img" 'v+1'; avbset "$w/ui/base.img" "$w/ui/low.img" '4096'
mkdir -p "$w/ui/src3"; cp -a "$w/ui/src/." "$w/ui/src3/"
for f in $(seq 1000 100 9000); do head -c "$f" /dev/urandom > "$w/ui/src3/etc/fill"; (cd "$w/ui/src3" && find . -mindepth 1 | sed 's|^\./||' | sort | cpio -o -H newc --quiet) 2>/dev/null > "$w/ui/rd3.cpio"; lz4 -l -12 -c "$w/ui/rd3.cpio" > "$w/ui/rd3.lz4"; [ $(( $(stat -c %s "$w/ui/rd3.lz4") % 4096 )) -ge 3600 ] && break; done
mkimg "$w/ui/rd3.lz4" "$w/ui/full.img" >/dev/null   # ramdisk ends < 500 B before its page end: the added key + rc cannot fit
for x in odd low full; do patch_tree "$w/$x" "$(sha256sum "$w/ui/$x.img" | cut -d' ' -f1)"; cp tools/golden-unattended-image "$w/$x/tools/"; done
gix() { "$w/$1/tools/golden-unattended-image" build --base "$w/ui/$1.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?; }
ck "gui: AVB size not a page boundary refused" 1 "$(gix odd)"; grep -q 'not a page boundary' "$w/gi.out"; ck "gui: page-boundary reason logged" 0 $?
ck "gui: AVB size inside the ramdisk pages refused" 1 "$(gix low)"; grep -q 'inside the base.s ramdisk pages' "$w/gi.out"; ck "gui: inside-ramdisk reason logged" 0 $?
ck "gui: headroom exceeded refused" 1 "$(gix full)"; grep -q 'headroom exceeded' "$w/gi.out"; ck "gui: headroom reason logged" 0 $?; ck "gui: AVB refusals wrote nothing" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: --dry-run on a base with no headroom is refused too (A26, AP-150)" 1 "$("$w/full/tools/golden-unattended-image" build --dry-run --base "$w/ui/full.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?)"; grep -q 'headroom exceeded' "$w/gi.out"; ck "gui: dry-run headroom reason logged (A26)" 0 $?; ck "gui: dry-run FAIL wrote nothing (A26)" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: --dry-run on a base with room passes and logs the headroom (A26)" 0 "$(gi build --dry-run --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?)"; grep -q 'headroom [0-9]* bytes' "$w/gi.out"; ck "gui: dry-run PASS logs the headroom bytes (A26)" 0 $?; ck "gui: dry-run PASS wrote nothing (A26)" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: base without AVB footer refused" 1 "$("$w/n/tools/golden-unattended-image" build --base "$w/ui/nofoot.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?)"; grep -q 'no AVB footer' "$w/gi.out"; ck "gui: no-footer reason logged" 0 $?
ck "gui: base the pipeline cannot round-trip (lz4 -1) refused" 1 "$("$w/r/tools/golden-unattended-image" build --base "$w/ui/base1.img" --key "$w/ui/key.pub" --out "$w/ui/out" > "$w/gi.out" 2>&1 </dev/null; echo $?)"; grep -q 'round-trip mismatch' "$w/gi.out"; ck "gui: round-trip reason logged" 0 $?; ck "gui: round-trip FAIL wrote nothing" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: dry-run PASS, writes nothing" 0 "$(gi build --dry-run --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out")"; ck "gui: dry-run wrote no image" 0 "$(ls "$w/ui/out" | wc -l)"
ck "gui: build PASS" 0 "$(gi build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out")"
ck "gui: last line RESULT: PASS" "RESULT: PASS" "$(tail -1 "$w/gi.out" | cut -c1-12)"; grep -q "$(cut -c1-60 "$w/ui/key.pub")" "$w/gi.out"; ck "gui: key bytes never printed" 1 $?
out=$(ls "$w/ui/out"/golden-unattended-*.img); sha1=$(sha256sum "$out" | cut -d' ' -f1)
ck "gui: .sha256 and manifest written" 3 "$(ls "$w/ui/out" | wc -l)"; ck "gui: image keeps the base size" 131072 "$(stat -c %s "$out")"
ck "gui: manifest base sha is the PIN" "$sbase" "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_sha256"])' "$out.manifest.json")"
ck "gui: manifest lz4 level 12" 12 "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["lz4_level"])' "$out.manifest.json")"
bnd=$(python3 -I -c 'import struct,sys; b=open(sys.argv[1],"rb").read(); print(struct.unpack_from("!Q",b,len(b)-64+12)[0])' "$w/ui/base.img")
cmp -s <(tail -c +$((bnd + 1)) "$w/ui/base.img") <(tail -c +$((bnd + 1)) "$out"); ck "gui: AVB vbmeta + footer copied verbatim" 0 $?
python3 tools/third_party/avbtool.py info_image --image "$out" >/dev/null 2>&1; ck "gui: avbtool info_image parses the derived image" 0 $?
rdx() { python3 -I -c 'import struct,subprocess,sys
b=open(sys.argv[1],"rb").read(); k,r=struct.unpack_from("<II",b,8); ro=4096*(1+(k+4095)//4096)
sys.stdout.buffer.write(subprocess.run(["lz4","-d","-c"],input=b[ro:ro+r],capture_output=True).stdout)' "$1"; }
mkdir -p "$w/ui/x0" "$w/ui/x1"; rdx "$w/ui/base.img" | (cd "$w/ui/x0" && cpio -id --quiet --no-absolute-filenames 2>/dev/null); rdx "$out" | (cd "$w/ui/x1" && cpio -id --quiet --no-absolute-filenames 2>/dev/null)
ck "gui: overlay.d mode 750" 750 "$(stat -c %a "$w/ui/x1/overlay.d")"; ck "gui: overlay.d/sbin mode 750" 750 "$(stat -c %a "$w/ui/x1/overlay.d/sbin")"
ck "gui: pixos_adb_keys mode 644" 644 "$(stat -c %a "$w/ui/x1/overlay.d/sbin/pixos_adb_keys")"; ck "gui: pixos-golden.rc mode 644" 644 "$(stat -c %a "$w/ui/x1/overlay.d/pixos-golden.rc")"
cmp -s "$w/ui/key.pub" "$w/ui/x1/overlay.d/sbin/pixos_adb_keys"; ck "gui: key bytes identical" 0 $?
grep -q '^    chmod 0640 /data/misc/adb/adb_keys$' "$w/ui/x1/overlay.d/pixos-golden.rc" && grep -q 'REPLACE INTO policies (uid,policy,until,logging,notification) VALUES(2000,2,0,0,0)' "$w/ui/x1/overlay.d/pixos-golden.rc"; ck "gui: rc has the TP commands" 0 $?
rc=$w/ui/x1/overlay.d/pixos-golden.rc
grep -q 'logging=1\|0,1,0)' "$rc"; ck "gui: rc has no logging=1" 1 $?; grep -q 'start pixos_su_grant\|^service pixos_su_grant' "$rc"; ck "gui: rc has no pixos_su_grant start/service" 1 $?
ck "gui: rc --sqlite not inside post-fs-data" 0 "$(awk '/^on /{blk=$2} /--sqlite/ && blk=="post-fs-data"{n++} END{print n+0}' "$rc")"
ck "gui: rc boot_completed trigger with the policy (logging,notification 0,0)" 1 "$(awk '/^on property:sys.boot_completed=1$/{b=1;next} /^on /{b=0} b&&/--sqlite.*VALUES\(2000,2,0,0,0\)/{n++} END{print n+0}' "$rc")"
ck "gui: rc pixos_bb service runs in the magisk domain, oneshot, disabled" 4 "$(awk '/^service pixos_bb /{b=1;next} /^[a-z]/{b=0} b&&/^    (user root|seclabel u:r:magisk:s0|oneshot|disabled)$/{n++} END{print n+0}' "$rc")"
ck "gui: rc debug.pixos.bb trigger starts pixos_bb" 1 "$(awk '/^on property:debug.pixos.bb=1$/{b=1;next} /^[a-z]/{b=0} b&&/^    start pixos_bb$/{n++} END{print n+0}' "$rc")"
grep -qE '^ *copy .*(/data/local/tmp|/data/adb)' "$rc"; ck "gui: rc has no init copy from /data/local/tmp or into /data/adb" 1 $?
ck "gui: rc service pixos_bb command line exact" 1 "$(grep -cxF 'service pixos_bb /system/bin/sh -c "mkdir -p /data/magisk && cp /data/local/tmp/pixos_busybox /data/magisk/busybox.tmp && chmod 0755 /data/magisk/busybox.tmp && mv /data/magisk/busybox.tmp /data/magisk/busybox && rm -f /data/local/tmp/pixos_busybox && setprop debug.pixos.bb.done ok || setprop debug.pixos.bb.done fail"' "$rc")"
ck "gui: rc exec line (magisk seclabel, root root) exact" 1 "$(grep -cxF '    exec u:r:magisk:s0 root root -- ${MAGISKTMP}/magisk --sqlite "REPLACE INTO policies (uid,policy,until,logging,notification) VALUES(2000,2,0,0,0)"' "$rc")"
ck "gui: rc service line has no dollar sign" 0 "$(grep '^service pixos_bb ' "$rc" | grep -c '\$')"
ck "gui: no non-comment rc line mentions /data/adb" 0 "$(grep -v '^ *#' "$rc" | grep -c '/data/adb')"
ck "gui: image has no busybox entry" 1 "$(test -e "$w/ui/x1/overlay.d/sbin/busybox" && echo 0 || echo 1)"
ck "gui: original entries unchanged" "" "$(diff -r "$w/ui/x0" "$w/ui/x1" | grep -v 'overlay.d' )"
ck "gui: 2nd build gives the same sha256" "$sha1" "$(gi build --base "$w/ui/base.img" --key "$w/ui/key.pub" --out "$w/ui/out2" >/dev/null; sha256sum "$w/ui/out2"/*.img | cut -d' ' -f1)"
ck "gui: verify PASS (current key)" 0 "$(gi verify "$out")"
cp "$out" "$w/ui/tamper.img"; cp "$out.manifest.json" "$w/ui/tamper.img.manifest.json"; printf 'X' | dd of="$w/ui/tamper.img" bs=1 seek=60000 conv=notrunc 2>/dev/null
ck "gui: verify on a tampered image FAIL" 1 "$(gi verify "$w/ui/tamper.img")"
cp "$out" "$w/ui/nomf.img"; ck "gui: verify without manifest FAIL" 1 "$(gi verify "$w/ui/nomf.img")"
sed "s/\"base_sha256\": \"$sbase\"/\"base_sha256\": \"$good\"/" "$out.manifest.json" > "$w/ui/badbase.img.manifest.json"; cp "$out" "$w/ui/badbase.img"
ck "gui: verify with manifest base != PIN FAIL" 1 "$(gi verify "$w/ui/badbase.img")"
# F4a: a consistent manifest for an image whose ramdisk was altered: only the rebuild compare can catch it
cp "$out" "$w/ui/rb.img"; printf 'Z' | dd of="$w/ui/rb.img" bs=1 seek=12300 conv=notrunc 2>/dev/null
python3 -I -c 'import json,sys,hashlib; m=json.load(open(sys.argv[1])); m["image_sha256"]=hashlib.sha256(open(sys.argv[2],"rb").read()).hexdigest(); json.dump(m,open(sys.argv[2]+".manifest.json","w"))' "$out.manifest.json" "$w/ui/rb.img"
ck "gui: consistent manifest, altered ramdisk: rebuild compare FAIL" 1 "$(gi verify "$w/ui/rb.img")"; grep -q 'rebuild from the manifest inputs differs' "$w/gi.out"; ck "gui: rebuild-mismatch reason logged" 0 $?
# F2: a foreign-key image never verifies against the current host key
ck "gui: build with another key PASS" 0 "$(gi build --base "$w/ui/base.img" --key "$w/ui/other.pub" --out "$w/ui/out3")"; oth=$(ls "$w/ui/out3"/golden-unattended-*.img)
ck "gui: foreign-key image: verify FAIL" 1 "$(gi verify "$oth")"; grep -q 'built for another key' "$w/gi.out"; ck "gui: foreign-key reason logged" 0 $?
ck "gui: verify --key PUB of the build key PASS" 0 "$(gi verify "$oth" --key "$w/ui/other.pub")"
# F5: an unexpected exception still ends in a RESULT line, exit 2
python3 -I -c 'import json,sys; m=json.load(open(sys.argv[1])); m["base_path"]=[1]; json.dump(m,open(sys.argv[2]+".manifest.json","w"))' "$out.manifest.json" "$w/ui/exc.img"; cp "$out" "$w/ui/exc.img"
ck "gui: internal exception -> exit 2" 2 "$(gi verify "$w/ui/exc.img")"; ck "gui: internal exception RESULT line" "RESULT: ERROR internal TypeError" "$(tail -1 "$w/gi.out" | cut -c1-32)"
fu() { : > "$LOG"; locked "$w/s/tools/fboot-android" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
ck "unattended: tampered image refused" 1 "$(fu --unattended --image "$w/ui/tamper.img")"; ck "unattended: tampered image: fastboot LOG empty" 0 "$(wc -c < "$LOG")"
ck "unattended: manifest base != PIN refused" 1 "$(fu --unattended --image "$w/ui/badbase.img")"; ck "unattended: bad manifest: fastboot LOG empty" 0 "$(wc -c < "$LOG")"
ck "unattended: image without manifest refused" 1 "$(fu --unattended --image "$w/ui/nomf.img")"; ck "unattended: no manifest: fastboot LOG empty" 0 "$(wc -c < "$LOG")"
ck "unattended: pinned image alone is not enough" 1 "$(fu --unattended --image "$w/ui/base.img")"; ck "unattended: pinned image: fastboot LOG empty" 0 "$(wc -c < "$LOG")"
ck "unattended: rebuild mismatch refused" 1 "$(fu --unattended --image "$w/ui/rb.img")"; ck "unattended: rebuild mismatch: LOG empty" 0 "$(wc -c < "$LOG")"; grep -q 'verify did not PASS' "$w/out"; ck "unattended: rebuild mismatch: refused by verify" 0 $?
ck "unattended: foreign-key image refused" 1 "$(fu --unattended --image "$oth")"; ck "unattended: foreign key: LOG empty" 0 "$(wc -c < "$LOG")"; grep -q 'verify did not PASS' "$w/out"; ck "unattended: foreign key: refused by verify" 0 $?
ck "unattended: no image anywhere refused" 1 "$(fu --unattended)"; ck "unattended: no image: fastboot LOG empty" 0 "$(wc -c < "$LOG")"
ck "unattended: verified image boots" 0 "$(fu --unattended --image "$out")"; grep -q '^fastboot -s FAKESN123 boot ' "$LOG"; ck "unattended: fastboot boot called" 0 $?
grep -q '^fastboot .*\(flash\|erase\)' "$LOG"; ck "unattended: no flash/erase" 1 $?
# F4c: a stub verify that always PASSes: the wrapper's own checks (manifest base, copy sha) must still refuse
cp "$out" "$out.manifest.json" "$w/ui/"; cp "$w/ui/badbase.img.manifest.json" "$w/v/badbase.img.manifest.json"; cp "$out" "$w/v/badbase.img"
printf '#!/bin/bash\necho "image_sha256=$(sha256sum "$2" | cut -d" " -f1)"; echo "RESULT: PASS stub"\n' > "$w/v/tools/golden-unattended-image"; chmod +x "$w/v/tools/golden-unattended-image"
fv() { : > "$LOG"; locked "$w/v/tools/fboot-android" "$@" > "$w/out" 2>&1 </dev/null; echo $?; }
ck "unattended(stub verify PASS): manifest base != PIN still refused" 1 "$(fv --unattended --image "$w/v/badbase.img")"; ck "unattended(stub): manifest base: LOG empty" 0 "$(wc -c < "$LOG")"
printf '#!/bin/bash\necho "image_sha256=%s"; echo "RESULT: PASS stub"\n' "$good" > "$w/v/tools/golden-unattended-image"
cp "$out" "$w/v/good.img"; cp "$out.manifest.json" "$w/v/good.img.manifest.json"
ck "unattended(stub verify PASS, wrong sha): copy sha check still refuses" 1 "$(fv --unattended --image "$w/v/good.img")"; ck "unattended(stub): copy sha: LOG empty" 0 "$(wc -c < "$LOG")"
printf '#!/bin/bash\necho "image_sha256=$(sha256sum "$2" | cut -d" " -f1)"; echo "RESULT: FAIL stub"; exit 1\n' > "$w/v/tools/golden-unattended-image"   # right sha, failing verify: only the exit status can refuse
ck "unattended(stub verify FAIL, right sha): refused" 1 "$(fv --unattended --image "$w/v/good.img")"; ck "unattended(stub FAIL): LOG empty" 0 "$(wc -c < "$LOG")"; grep -q 'verify did not PASS' "$w/out"; ck "unattended(stub FAIL): refused by the verify exit status" 0 $?
printf '#!/bin/bash\necho "image_sha256=$(sha256sum "$2" | cut -d" " -f1)"; echo "RESULT: PASS stub"\n' > "$w/v/tools/golden-unattended-image"
cp "$w/ui/nofoot.img" "$w/v/nofoot.img"; printf '{"base_sha256": "%s"}\n' "$sbase" > "$w/v/nofoot.img.manifest.json"
ck "unattended(stub verify PASS): image copy without AVB footer refused" 1 "$(fv --unattended --image "$w/v/nofoot.img")"; ck "unattended(stub): no footer: LOG empty" 0 "$(wc -c < "$LOG")"; grep -q 'no AVB footer' "$w/out"; ck "unattended(stub): no footer reason" 0 $?
mkdir -p "$HOME/pixelios-factory"; cp "$out" "$out.manifest.json" "$HOME/pixelios-factory/"; cp "$oth" "$oth.manifest.json" "$HOME/pixelios-factory/"; touch "$HOME/pixelios-factory/$(basename "$oth")"
ck "unattended: default image = the one for the CURRENT key (a newer foreign-key image is ignored)" 0 "$(fu --unattended)"; grep -q '^fastboot -s FAKESN123 boot ' "$LOG"; ck "unattended default: booted" 0 $?
ck "unattended --dry-run prints the plan" 1 "$(fu --unattended --dry-run >/dev/null; grep -c 'would run FIRST: tools/golden-unattended-image verify' "$w/out")"; ck "unattended --dry-run: no fastboot call" 0 "$(wc -c < "$LOG")"
ck "no --unattended: dry-run plan unchanged" 1 "$(fu --dry-run --image "$img" >/dev/null; grep -c "would check: sha256($img) = $sbase; product=bluejay" "$w/out")"
fi   # end of the tools/third_party-dependent checks (r1 F13)
# ---- Wi-Fi golden-trace modes (20z, W36 R29): wifi-trace-probe, wifi-fw-bind/-unbind, wifi-kprobes/-off, wifi-scan, wifi-trace-pull. A dedicated fake adb ($wfb/adb, PATH in front) answers the stdin scripts
# the modes send; the kprobe scripts are REALLY executed by sh against a fake tracefs directory ($FAKE_T, paths rewritten), so the generated script logic is tested, not only its text. Everything phone-side is a model:
# what the real phone prints is UNVERIFIED until the first run. Every adb call runs with </dev/null (AP-119).
echo "== wifi trace modes"
wfb=$w/wfbin; mkdir -p "$wfb" "$w/wstate"; export WLOG=$w/wf-calls.log WIN=$w/wf-stdin.log WSTATE=$w/wstate FAKE_T=$w/ftrace PH=$w/ph
export GP_WIFI_SECRET=$w/wsecret
WSSID="a/b&c.d\\e'f"; WPSK='Sup3rSecretPsk!'; export WSSID WPSK
KOSHA=222cd8d65290a9fc913c8b6012363457454fc87e0eae94e21baf3cff1e5bcaff   # independent literal copy of WF_KO_PIN
printf '%s\n%s\n' "$WSSID" "$WPSK" > "$GP_WIFI_SECRET"; chmod 600 "$GP_WIFI_SECRET"
cat > "$wfb/adb" <<'F'
#!/bin/bash
# fake adb for the wifi modes: logs argv to $WLOG, stdin scripts to $WIN
echo "adb $*" >> "$WLOG"
STOCK=5555555555555555555555555555555555555555555555555555555555555555
KOSHA=222cd8d65290a9fc913c8b6012363457454fc87e0eae94e21baf3cff1e5bcaff   # independent literal copy of WF_KO_PIN (sha256sum of the disassembled bcmdhd4389.ko, 2026-10-29)
if [ "$1" = get-serialno ]; then echo FAKESN123; exit 0; fi
if [ "$1 $2 $3" = "shell getprop ro.product.device" ]; then echo bluejay; exit 0; fi
if [ "$1" = push ]; then
	[ -z "${FAKE_PUSHFAIL-}" ] || exit 1
	mkdir -p "$PH$(dirname "$3")"; cp "$2" "$PH$3"   # phone model: the staged copy lands under $PH
	if [ -n "${FAKE_PUSH_SLEEP-}" ] && [ "$(grep -c '^adb push' "$WLOG")" -ge 2 ]; then sleep "$FAKE_PUSH_SLEEP"; fi   # a slow 2nd push: the window for the signal test
	exit 0; fi
# phone-side bind/unbind/readback/stage-clean scripts are EXECUTED by sh against the model under $PH (paths rewritten, mount/umount/chcon/ls shims in $PH/bin), review golden-wifi-modes-r1 F12
ph_run() { local x; x=$(printf '%s\n' "$in" | sed -e "s#/proc/mounts#$PH/proc/mounts#g" -e "s#/vendor/firmware#$PH/vendor/firmware#g" -e "s#/mnt/gp-wfw#$PH/mnt/gp-wfw#g" -e "s#/data/local/tmp/gp-wfw-#$PH/data/local/tmp/gp-wfw-#g")
	PATH="$PH/bin:$PATH" sh -c "$x" 2>&1; }
if [ "$1 $2" = "shell su" ]; then
	in=$(cat); { echo "--- script ($3)"; printf '%s\n' "$in"; } >> "$WIN"
	[ -z "${FAKE_SU_SLEEP_ON-}" ] || case "$in" in *"$FAKE_SU_SLEEP_ON"*) sleep "${FAKE_SU_SLEEP:-3}" ;; esac   # a slow script: the window for the signal test
	if grep -q 'kprobe_events' <<<"$in"; then   # run the generated script for real against the fake tracefs
		TS=$FAKE_T
		x=$(printf '%s\n' "$in" | sed "s#/sys/kernel/tracing#$TS#g")
		[ -z "${FAKE_KP_FAIL-}" ] || x=$(printf '%s\n' "$x" | sed "/gpwifi\/${FAKE_KP_FAIL} /s#>> [^ ]*kprobe_events#> /nonexistent-dir/x#")
		[ -z "${FAKE_FILTER_BAD-}" ] || x=$(printf '%s\n' "$x" | sed -E "s#echo '[^']*' > ([^ ]*/${FAKE_FILTER_BAD}/filter)#echo 'garbage' > \\1#")
		[ -z "${FAKE_EN_BAD-}" ] || x=$(printf '%s\n' "$x" | sed "s#echo 1 > \\([^ ]*gpwifi/enable\\)#echo 0 > \\1#")
		[ -z "${FAKE_CFG_STUCK-}" ] || x=$(printf '%s\n' "$x" | sed -E "s#echo [01] > ([^ ]*cfg80211/enable)#true#")
		[ -z "${FAKE_KP_KEEP-}" ] || x=$(printf '%s\n' "$x" | sed -E "s#echo '-:gpwifi/${FAKE_KP_KEEP}' >> ([^ ]+)#true#")
		# r2 R2-5: the FIRST removal script ever sent fails its first `-:gpwifi/EV` write (a disarm that itself fails); the state file keeps every later script good
		if [ -n "${FAKE_KP_OFFFAIL_ONCE-}" ] && [ ! -e "$WSTATE/offfail" ]; then case "$in" in *"echo '-:gpwifi/"*) : > "$WSTATE/offfail"; x=$(printf '%s\n' "$x" | sed -E "0,/echo '-:gpwifi\/[a-z0-9_]+' >> [^ ]+/s//false/") ;; esac; fi
		x=$(printf '%s\n' "$x" | sed -E "s#echo '-:gpwifi/([a-z0-9_]+)' >> ([^ ]+)#sed -i '/^[pr]:gpwifi\\\\/\\1 /d' \\2; rm -rf $TS/events/gpwifi/\\1#")
		x=$(printf '%s\n' "$x" | sed -E "s#^echo gpwifi_left=#if ! grep -q '^[pr]:gpwifi/' $TS/kprobe_events; then rm -rf $TS/events/gpwifi; fi; echo gpwifi_left=#")
		printf '%s\n' "$x" | sh 2>&1
		exit 0
	fi
	case "$in" in
	*"echo unbind_script_end=ok"*|*"echo readback_end=ok"*|*"echo bind_script_end=ok"*|*"echo stage_clean=ok"*)
		case "$in" in *"echo unbind_script_end=ok"*) [ -z "${FAKE_BINDS_LEFT-}" ] || echo "fakedev $PH/vendor/firmware/zz ext4 ro 0 0" >> "$PH/proc/mounts" ;; esac
		if [ -n "${FAKE_RB_BAD-}" ]; then case "$in" in *"echo readback_end=ok"*) ph_run | sed -E "s#^(rb_sha_[^=]*)=.*#\\1=${FAKE_STOCK-$STOCK}#"; exit 0 ;; esac; fi
		ph_run; exit 0 ;;
	*)
		out=""
		case "$in" in
		*probe_procfs=ok*) [ -n "${FAKE_NODHD-}" ] && echo "ls: /proc/dhd_trace: No such file or directory" || echo "-r-------- 1 root root 0 2026-10-29 00:00 /proc/dhd_trace"; echo "-r-------- 1 root root 0 2026-10-29 00:00 /proc/dhd_ecounters" ;;
		*probe_params=ok*) if [ -n "${FAKE_PARAMS_EMPTY-}" ]; then echo "/sys/module/bcmdhd4389/parameters/*="; else echo "/sys/module/bcmdhd4389/parameters/info_string=Driver: dhd ssid $WSSID mac aa:bb:cc:dd:ee:ff"; echo "/sys/module/bcmdhd4389/parameters/dhd_console_ms=0"; fi ;;
		*probe_sysfs=ok*) if [ -n "${FAKE_NOSYSFS-}" ]; then echo "ls: /sys/wifi/: No such file or directory"; echo "cat: /sys/wifi/wl_dbg_level: No such file or directory"; else echo "-rw-rw---- 1 root root 4096 wl_dbg_level"; echo 3; fi ;;
		*probe_ksym=ok*) if [ -n "${FAKE_NOCFG80211-}" ]; then echo "ls: /sys/kernel/tracing/events/cfg80211/: No such file or directory"; else echo "rdev_scan"; fi
			for s in $(printf '%s\n' "$in" | grep -o "grep -wE '[^']*'" | sed "s/grep -wE '//; s/'//" | tr '|' ' '); do [ "$s" = "${FAKE_NOSYM-}" ] || echo "T $s	[bcmdhd4389]"; done ;;
		*probe_cmd=ok*) echo "  start-scan"; echo "  list-scan-results"; echo "  connect-network <ssid> open|owe|wpa2|wpa3|wep"; echo "  forget-network <networkId>" ;;
		*probe_props=ok*) echo "[wifi.interface]: [wlan0]"; echo "bcmdhd4389 1234567 0 - Live 0x0"; echo "cfg80211 1234 1 bcmdhd4389, Live 0x0" ;;
		*probe_fw=ok*) for f in fw_bcmdhd.bin bcmdhd.cal bcmdhd_clm.blob; do echo "-rw-r--r-- 1 root root u:object_r:vendor_file:s0 1 2026-01-01 /vendor/firmware/$f"; done
			for f in fw_bcmdhd.bin bcmdhd.cal bcmdhd_clm.blob; do echo "${STOCK}  /vendor/firmware/$f"; done ;;
		*ko_end=ok*) if [ -z "${FAKE_KO_NONE-}" ]; then echo "${FAKE_KO_SHA-$KOSHA}  /vendor/lib/modules/bcmdhd4389.ko"; [ -z "${FAKE_KO_SECOND-}" ] || echo "$FAKE_KO_SECOND  /vendor_dlkm/lib/modules/bcmdhd4389.ko"; fi ;;
		*probe_irq=ok*) [ -n "${FAKE_NOIRQ-}" ] || echo " 200:   1 0 0 0 PCI-MSI 0 dhd_pcie" ;;
		*predl_end=ok*) [ -n "${FAKE_PREDL_FAIL-}" ] || echo "pre_download_count=${FAKE_PREDL-0}" ;;
		*probe_mm=ok*) echo mm_rc=0; echo "ns_mm=mnt:[4026531840]"; if [ -n "${FAKE_NS_DIFF-}" ]; then echo "ns_init=mnt:[4026532999]"; else echo "ns_init=mnt:[4026531840]"; fi ;;
		*scan_list=ok*)
			if [ -n "${FAKE_NOSCAN-}" ]; then echo "No scan results"; else
				printf '    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags\n'
				printf "  %17s  %9d  %18s   %7s    %-32s  %s\n" aa:bb:cc:00:11:22 2412 "      -50         " "  1.250" "NeighborNet" "[WPA2-PSK-CCMP][ESS]"
				printf "  %17s  %9d  %18s   %7s    %-32s  %s\n" aa:bb:cc:00:11:23 5180 "      -61         " "  2.250" "$WSSID" "[WPA2-PSK-CCMP][ESS]"
				printf "  %17s  %9d  %18s   %7s    %-32s  %s\n" aa:bb:cc:00:11:24 2437 "      -70         " "  3.250" "Cafe Free WiFi" "[ESS]"
				printf "  %17s  %9d  %18s   %7s    %-32s  %s\n" aa:bb:cc:00:11:30 2462 "      -72         " "  4.250" "Abcdefghijklmnopqrstuvwxyz012345" "[WPA2-PSK-CCMP][ESS]"   # exactly 32 chars
				printf "  %17s  %9d  %18s   %7s    %s  %s\n" aa:bb:cc:00:11:31 2412 "      -73         " "  5.250" "Caf$(printf '\303\251')-$(printf '\346\227\245\346\234\254\350\252\236')" "[ESS]"   # multibyte SSID (UTF-8), padding not byte-exact on purpose
				printf "  %17s  %9d  %18s   %7s    %s  %s\n" aa:bb:cc:00:11:32 5200 "      -74         " "  6.250" "my  [net]  two" "[ESS]"   # SSID with double spaces and brackets
				[ -z "${FAKE_SCANODD2-}" ] || printf "  %17s  %9s  %18s   %7s    %-32s  %s\n" aa:bb:cc:00:11:26 notanumber "      -75         " "  7.250" "OddRow" "[ESS]"   # BSSID column ok, frequency column not a number
				[ -z "${FAKE_SCANODD-}" ] || echo "  something different aa:bb:cc:00:11:25 Hidden"
			fi ;;
		esac
		printf '%s\n' "$in" | grep -o 'echo [a-z_0-9]*=ok' | sed 's/^echo //' | grep -vx "${FAKE_MISS:-none}"
		exit 0 ;;
	esac
	exit 0
fi
if [ "$1" = exec-out ]; then
	c=$4; body=""
	case "$c" in
	dmesg\;*|"dmesg; r="*|"dmesg") body=$(printf '%s\n' "[    1.0] wlan: assoc ssid $WSSID bssid aa:bb:cc:dd:ee:ff" "[    1.1] decoy a/b&cXd\\e'f stays" "[    1.2] dhd ok") ;;
	"dmesg | grep -E"*) if [ -z "${FAKE_NOIDSUP-}" ]; then body=$(printf '%s\n' "[    5.0] idsup status:6 reason:0" "[    5.1] 4way HS finished. port authorized event sent"); fi; body=$(printf '%s\nend-of-wifi-markers' "$body") ;;
	"logcat "*) body=$(printf '%s\n' "wifi: connecting to $WSSID ap aa-bb-cc-dd-ee-ff"); [ -z "${FAKE_PSKLEAK-}" ] || body=$(printf '%s\nwpa psk=%s\n' "$body" "$WPSK") ;;
	"if [ -e /proc/dhd_trace"*) if [ -n "${FAKE_NODHD-}" ]; then body="INFO: no /proc/dhd_trace"; else body="dhd trace data"; fi ;;
	"dumpsys wifi"*) body=$(printf '%s\n' "mWifiInfo SSID: $WSSID BSSID: aa:bb:cc:dd:ee:ff") ;;
	*) body="fake pull of: $c" ;;
	esac
	case "$c" in dmesg\;*)
		[ -z "${FAKE_HEXLEAK-}" ] || { if [ "$FAKE_HEXLEAK" = colon ]; then h=$(printf '%s' "$WSSID" | od -An -v -tx1 | tr -d '\n' | sed 's/^ //; s/ /:/g'); else h=$(printf '%s' "$WSSID" | od -An -v -tx1 | tr -d ' \n'); fi; body=$(printf '%s\nwpa: ssid=%s x' "$body" "$h"); }
		[ -z "${FAKE_PSKHEX-}" ] || body=$(printf '%s\nwpa: psk hex=%s x' "$body" "$(printf '%s' "$WPSK" | od -An -v -tx1 | tr -d ' \n')") ;; esac
	[ -z "${FAKE_EMPTYPULL-}" ] || body=""
	printf '%s' "$body"; [ -z "$body" ] || printf '\n'
	printf '\n__GP_RC=%s\n' "${FAKE_PULLRC-0}"; exit 0
fi
echo "fake adb: unexpected call $*" >&2; exit 1
F
chmod +x "$wfb/adb"
# phone model for the bind/unbind/readback/stage-clean scripts (executed for real by the fake adb): a fresh $PH with three stock firmware files, an empty /proc/mounts and shims for mount, umount, chcon and `ls -Z`
# (bind = the bound path reads the source content and label until umount restores the stock one). mk_phone is called before every scenario (a leftover bind from the previous one would be a false bind_already).
realls=$(command -v ls)
mk_phone() { local n; rm -rf "$PH"; mkdir -p "$PH/bin" "$PH/proc" "$PH/vendor/firmware" "$PH/data/local/tmp" "$PH/mnt"; : > "$PH/proc/mounts"
	for n in fw_bcmdhd.bin bcmdhd.cal bcmdhd_clm.blob; do echo "stock $n" > "$PH/vendor/firmware/$n"; echo u:object_r:vendor_file:s0 > "$PH/vendor/firmware/$n.label"; done
	cat > "$PH/bin/mount" <<'S'
#!/bin/bash
echo "mount $*" >> "$PH/mount.log"
if [ "$1 $2" = "-t tmpfs" ]; then
	[ -z "${FAKE_NOTMPFS-}" ] || { echo "mount: tmpfs failed" >&2; exit 1; }
	echo "tmpfs ${*: -1} tmpfs rw 0 0" >> "$PH/proc/mounts"; exit 0; fi
if [ "$1 $2" = "-o bind" ]; then
	src=$3; dst=$4; b=$(basename "$dst")
	[ "${FAKE_BINDFAIL-}" != "$b" ] || { echo "mount: bind failed" >&2; exit 1; }
	cp -p "$dst" "$dst.stocksave"; cp "$dst.label" "$dst.labelsave"
	if [ "${FAKE_BOUNDSHA_BAD-}" = "$b" ]; then echo garbage > "$dst"; else cp "$src" "$dst"; fi
	if [ -f "$src.label" ]; then cp "$src.label" "$dst.label"; else echo u:object_r:tmpfs:s0 > "$dst.label"; fi
	echo "fakedev $dst ext4 ro 0 0" >> "$PH/proc/mounts"; exit 0; fi
echo "mount: unexpected args $*" >&2; exit 1
S
	cat > "$PH/bin/umount" <<'S'
#!/bin/bash
echo "umount $*" >> "$PH/mount.log"
p=$1
grep -q " $p " "$PH/proc/mounts" || { echo "umount: $p: not mounted" >&2; exit 1; }
if [ -f "$p.stocksave" ]; then
	if [ -z "${FAKE_UNBIND_SHA_BAD-}" ]; then cp -p "$p.stocksave" "$p"; cp "$p.labelsave" "$p.label"; fi
	rm -f "$p.stocksave" "$p.labelsave"
elif [ -d "$p" ]; then rm -f "$p"/*; fi
grep -v " $p " "$PH/proc/mounts" > "$PH/proc/mounts.new"; mv "$PH/proc/mounts.new" "$PH/proc/mounts"; exit 0
S
	cat > "$PH/bin/chcon" <<'S'
#!/bin/bash
echo "chcon $*" >> "$PH/mount.log"; echo "$1" > "$2.label"
S
	{ echo '#!/bin/bash'; echo 'if [ "$1" = -Z ]; then f=$2; if [ -f "$f.label" ]; then l=$(cat "$f.label"); else case "$f" in "$PH"/mnt/*) l=u:object_r:tmpfs:s0 ;; *) l=u:object_r:vendor_file:s0 ;; esac; fi; echo "$l $f"; exit 0; fi'; echo "exec $realls \"\$@\""; } > "$PH/bin/ls"
	chmod +x "$PH/bin/"*; }
mk_phone
# grep wrapper: records the argv of every grep and the mode of its -f file (PSK/SSID literal files must be mode 600 and never be in argv)
realgrep=$(command -v grep); mkdir -p "$w/wgrep"; cat > "$w/wgrep/grep" <<F
#!/bin/bash
echo "grep \$*" >> "$w/wgrep.log"
a=("\$@"); for ((i = 0; i < \${#a[@]}; i++)); do if [ "\${a[i]}" = -f ] && [ -f "\${a[i+1]}" ]; then echo "mode \$(stat -c %a "\${a[i+1]}")" >> "$w/wgrep.log"; fi; done
exec $realgrep "\$@"
F
chmod +x "$w/wgrep/grep"
WALL=$w/wf-all.log; : > "$WALL"
absent() { if [ -e "$1" ]; then echo 1; else echo 0; fi; }
gw() { : > "$WLOG"; : > "$WIN"; PATH="$wfb:$PATH" tools/golden-pull "$@" > "$w/out" 2>&1 </dev/null; local rc=$?; cat "$WLOG" "$WIN" >> "$WALL"; echo $rc; }
gwc() { local g=$1 rc; shift; : > "$WLOG"; : > "$WIN"; PATH="$wfb:$PATH" "$g" "$@" > "$w/out" 2>&1 </dev/null; rc=$?; cat "$WLOG" "$WIN" >> "$WALL"; echo $rc; }
# fake tracefs: the 29 gpwifi event names of the P1-P24 table (own literal list, independent of the tool's table)
KPEV="ioctl iovar ioctl_ret cfgevent supevent capcall scan connect d2hmb h2dmb h2dmbmsg d2hmbmsg wshared rxctrl rxctrl_ret rxdata frcreate frresp ioctcmplt readshared_ret wrcmpl capstr escanparams escanparamsv1 evdata edlinit d2hringcreate d2hcc rxoff"
mk_trace() { rm -rf "$FAKE_T"; mkdir -p "$FAKE_T/events/gpwifi"; for e in $KPEV ${1-}; do mkdir -p "$FAKE_T/events/gpwifi/$e"; : > "$FAKE_T/events/gpwifi/$e/filter"; done
	: > "$FAKE_T/events/gpwifi/enable"; : > "$FAKE_T/kprobe_events"; echo 1 > "$FAKE_T/tracing_on"; echo KEEPME > "$FAKE_T/trace"; echo 0 > "$FAKE_T/buffer_size_kb"
	if [ -z "${NOCFG-}" ]; then mkdir -p "$FAKE_T/events/cfg80211"; echo "${CFG0-0}" > "$FAKE_T/events/cfg80211/enable"; fi; }
# independent never-write list (own copy; AP-124/125) and the check over everything the new modes sent. SC = the ONE removal the tool may send (exact string); everything else with rm/mv/truncate/kill, and any redirect into /dev/ other than kmsg and null, fails.
NEVER='dd of=|of=/dev|/dev/block|flash|fastboot|mkfs|reboot|settings put|remount|pm clear|uninstall|memdump|assert|hang_privcmd_err|dump_start|dump_stateinfo|sssr_enab|fis_enab|wl_accel_force_reg_on|firmware_path|nvram_path|clk_summary|clk_dump|rmmod|insmod|svc |setprop|setenforce|dmesg -c|dmesg -C|logcat -c|set-wifi-enabled'
SC='rm -f /data/local/tmp/gp-wfw-fw_bcmdhd.bin /data/local/tmp/gp-wfw-bcmdhd.cal /data/local/tmp/gp-wfw-bcmdhd_clm.blob; echo stage_clean=ok'
sent_stripped() { local t; t=$(cat "$WALL"); t=${t//"$SC"/}; t=${t//"> /dev/kmsg"/}; t=${t//"/dev/null"/}; printf '%s\n' "$t"; }
never_ok() { ! sent_stripped | grep -qE "$NEVER" && ! sent_stripped | grep -qE '(^|[^a-z])(rm|mv|truncate|kill|killall|pkill) ' && ! sent_stripped | grep -qE '>>? */dev/'; }
# --- pins (independent literal copy of the 3 sha256 computed from ~/pixelios-artifacts/wlan-fw/files on 2026-10-29)
for kv in fw_bcmdhd.bin=3a6dbf5a726304f83e154ff1b265a06c77d669506eb5c784fd864f10bdda6533 bcmdhd.cal=49437aa63d15cff7393a92b884ef886480cd18b2710259b00201f6f68beae424 bcmdhd_clm.blob=eea06b8f963dd6ff771a5edd25114b5fe1f857fefa342cd8f200c4cbc65b3e2e; do
	grep -q "\[${kv%%=*}\]=${kv#*=}" tools/golden-pull; ck "pin ${kv%%=*} is ${kv#*=}" 0 $?; done
# --- dry-run of every mode: rc 0, nothing sent to adb
for m in wifi-trace-probe wifi-fw-unbind wifi-kprobes wifi-kprobes-off wifi-scan wifi-trace-pull; do
	ck "$m --dry-run rc 0" 0 "$(gw --dry-run --out "$w/wd" $m)"; ck "$m --dry-run: no adb call" 0 "$(wc -c < "$WLOG")"; grep -q "^RESULT: PASS $m gaps=0 dry=1" "$w/out"; ck "$m --dry-run: RESULT PASS dry=1" 0 $?
	ck "$m rejects an argument" 2 "$(gw --dry-run --out "$w/wd" $m x)"; done
ck "wifi-fw-bind without DIR is ERROR" 2 "$(gw --dry-run --out "$w/wd" wifi-fw-bind)"
ck "wifi-fw-bind with a missing DIR is ERROR" 2 "$(gw --dry-run --out "$w/wd" wifi-fw-bind "$w/nodir")"
grep -q 'wifi-trace-probe|wifi-fw-bind DIR|wifi-fw-unbind|wifi-kprobes|wifi-kprobes-off|wifi-scan|wifi-trace-pull' tools/golden-pull; ck "usage string lists the new modes" 0 $?
ck "unknown mode still ERROR 2 and names wifi-scan" 2 "$(gw --dry-run --out "$w/wd" nonsense)"; grep -q 'wifi-scan' "$w/out"; ck "usage text names wifi-scan" 0 $?
# --- secret handling: missing secret file refuses the masked modes (no adb script sent), default path honoured
ck "wifi-scan without the secret file is refused (rc 1)" 1 "$(GP_WIFI_SECRET=$w/nosecret gw --out "$w/ws0" wifi-scan)"; grep -q 'REFUSED: wifi-scan: secret file' "$w/out"; ck "missing secret: reason named" 0 $?
ck "missing secret: no script sent" 0 "$(wc -c < "$WIN")"
mkdir -p "$HOME/pixelios-artifacts/golden"; cp "$GP_WIFI_SECRET" "$HOME/pixelios-artifacts/golden/.wifi-join"
ck "default secret path ~/pixelios-artifacts/golden/.wifi-join is used" 0 "$(env -u GP_WIFI_SECRET PATH="$wfb:$PATH" tools/golden-pull --out "$w/ws1" wifi-scan >/dev/null 2>&1 </dev/null; echo $?)"
printf 'onlyssid\nshort\n' > "$w/badsecret"; ck "secret with a short PSK is refused" 1 "$(GP_WIFI_SECRET=$w/badsecret gw --out "$w/ws0" wifi-scan)"
# --- wifi-trace-probe
ck "wifi-trace-probe PASS rc 0" 0 "$(gw --out "$w/wp" wifi-trace-probe)"; grep -q '^RESULT: PASS wifi-trace-probe gaps=0 dry=0' "$w/out"; ck "probe RESULT line" 0 $?
ck "probe wrote 11 files" 11 "$(ls "$w/wp/wifi" | wc -l)"
grep -q "^$KOSHA  /vendor/lib/modules/bcmdhd4389.ko" "$w/wp/wifi/probe-ko.txt"; ck "probe: module sha256 pulled into probe-ko.txt" 0 $?
grep -qx 'pre_download_count=0' "$w/out"; ck "probe prints pre_download_count=0" 0 $?
ck "probe: SSID in info_string masked" 0 "$(grep -c -F "$WSSID" "$w/wp/wifi/probe-params.txt")"; grep -q 'ssid <ssid> mac <masked>' "$w/wp/wifi/probe-params.txt"; ck "probe: params file shows <ssid> and <masked>" 0 $?
grep -q 'ls -lZ /vendor/firmware/fw_bcmdhd.bin /vendor/firmware/bcmdhd.cal /vendor/firmware/bcmdhd_clm.blob' "$WIN"; ck "probe: ls -lZ of the 3 firmware files" 0 $?
grep -q 'sha256sum /vendor/firmware/fw_bcmdhd.bin' "$WIN"; ck "probe: sha256 of the firmware files" 0 $?
grep -q "grep -iE 'dhd|wlan|pcie' /proc/interrupts" "$WIN"; ck "probe: /proc/interrupts grep" 0 $?
grep -q -- 'adb shell su --mount-master -c sh' "$WLOG"; ck "probe: su --mount-master used for the rc probe" 0 $?
ck "probe: pre_download_count=7 printed" 0 "$(FAKE_PREDL=7 gw --out "$w/wp2" wifi-trace-probe)"; grep -qx 'pre_download_count=7' "$w/out"; ck "probe: N>0 line" 0 $?
ck "probe: dmesg failure (no count) is a GAP" 1 "$(FAKE_PREDL_FAIL=1 gw --out "$w/wp3" wifi-trace-probe)"; grep -q 'GAP: wifi-trace-probe: dmesg failed or was empty' "$w/out"; ck "probe: count GAP named" 0 $?
grep -q "grep -c" "$WIN"; ck "probe: count script uses grep -c" 0 $?
ck "probe: absent dhd_trace/sysfs/cfg80211 are INFO, rc 0" 0 "$(FAKE_NODHD=1 FAKE_NOSYSFS=1 FAKE_NOCFG80211=1 gw --out "$w/wp4" wifi-trace-probe)"; grep -q 'info=3$' "$w/out"; ck "probe: info=3" 0 $?
ck "probe: missing marker is a GAP" 1 "$(FAKE_MISS=probe_irq=ok gw --out "$w/wp5" wifi-trace-probe)"
ck "probe: empty params dir is a GAP" 1 "$(FAKE_PARAMS_EMPTY=1 gw --out "$w/wp6" wifi-trace-probe)"
ck "probe: missing kallsyms symbol is a GAP" 1 "$(FAKE_NOSYM=wl_cfg80211_event gw --out "$w/wp7" wifi-trace-probe)"; grep -q 'symbol wl_cfg80211_event is not in /proc/kallsyms' "$w/out"; ck "probe: symbol GAP names it" 0 $?
ck "probe: bcmdhd4389.ko with another sha is a GAP (r1 F14)" 1 "$(FAKE_KO_SHA=0000000000000000000000000000000000000000000000000000000000000000 gw --out "$w/wp10" wifi-trace-probe)"; grep -q 'is not the disassembled build' "$w/out"; ck "probe: ko mismatch GAP named" 0 $?
ck "probe: one of two ko files differing is a GAP" 1 "$(FAKE_KO_SECOND=1111111111111111111111111111111111111111111111111111111111111111 gw --out "$w/wp11" wifi-trace-probe)"
ck "probe: both ko files equal the pin: PASS" 0 "$(FAKE_KO_SECOND=$KOSHA gw --out "$w/wp12" wifi-trace-probe)"
ck "probe: no ko file found is a GAP" 1 "$(FAKE_KO_NONE=1 gw --out "$w/wp13" wifi-trace-probe)"; grep -q 'no bcmdhd4389.ko at any of' "$w/out"; ck "probe: no-ko GAP named" 0 $?
grep -q "^WF_KO_PIN=$KOSHA;" tools/golden-pull; ck "WF_KO_PIN is the sha256 of the disassembled module (independent literal)" 0 $?
ck "probe: no interrupts line is a GAP" 1 "$(FAKE_NOIRQ=1 gw --out "$w/wp8" wifi-trace-probe)"
ck "probe: init-ns differs from su --mount-master ns is a GAP" 1 "$(FAKE_NS_DIFF=1 gw --out "$w/wp9" wifi-trace-probe)"
never_ok; ck "probe/bind-free: never-write list absent from sent commands" 0 $?
# --- wifi-fw-bind / unbind: dummy files; the pins are patched in a COPY (the real script's pins are tested above)
mkdir -p "$w/wfw" "$w/wm"; for n in fw_bcmdhd.bin bcmdhd.cal bcmdhd_clm.blob; do head -c 3000 /dev/urandom > "$w/wfw/$n"; done
p1=$(sha256sum "$w/wfw/fw_bcmdhd.bin" | cut -d' ' -f1); p2=$(sha256sum "$w/wfw/bcmdhd.cal" | cut -d' ' -f1); p3=$(sha256sum "$w/wfw/bcmdhd_clm.blob" | cut -d' ' -f1)
sed "s/^declare -A WF_FW_PIN=.*/declare -A WF_FW_PIN=([fw_bcmdhd.bin]=$p1 [bcmdhd.cal]=$p2 [bcmdhd_clm.blob]=$p3)/" tools/golden-pull > "$w/wm/golden-pull"; chmod +x "$w/wm/golden-pull"
ck "real pins reject the dummy files (sha mismatch) rc 1" 1 "$(gw --out "$w/wb0" wifi-fw-bind "$w/wfw")"; grep -q 'GAP: wifi-fw-bind: sha256 of .* is not the pinned' "$w/out"; ck "mismatch: GAP names the pin" 0 $?
ck "sha mismatch: nothing pushed" 0 "$(grep -c '^adb push' "$WLOG")"; ck "sha mismatch: no su script sent" 0 "$(wc -c < "$WIN")"
ck "sha mismatch --dry-run: rc 1 and no adb call" 1 "$(gw --dry-run --out "$w/wb0" wifi-fw-bind "$w/wfw")"; ck "mismatch dry-run: no adb call" 0 "$(wc -c < "$WLOG")"
ck "bind --dry-run with matching files: rc 0" 0 "$(gwc "$w/wm/golden-pull" --dry-run --out "$w/wb0" wifi-fw-bind "$w/wfw")"; grep -q 'would run: adb push' "$w/out"; ck "bind dry-run prints the would-run push" 0 $?; ck "bind dry-run: no adb call" 0 "$(wc -c < "$WLOG")"
mkdir -p "$w/wfw2"; cp "$w/wfw/fw_bcmdhd.bin" "$w/wfw/bcmdhd.cal" "$w/wfw2/"
ck "bind --dry-run with a missing file: rc 1" 1 "$(gwc "$w/wm/golden-pull" --dry-run --out "$w/wb0" wifi-fw-bind "$w/wfw2")"; grep -q 'GAP: wifi-fw-bind: .*bcmdhd_clm.blob is not a file' "$w/out"; ck "missing file: GAP named" 0 $?
ck "bind (matching files) rc 0" 0 "$(gwc "$w/wm/golden-pull" --out "$w/wb1" wifi-fw-bind "$w/wfw")"; grep -q '^RESULT: PASS wifi-fw-bind gaps=0' "$w/out"; ck "bind RESULT PASS" 0 $?
ck "bind: exactly 3 pushes" 3 "$(grep -c '^adb push' "$WLOG")"
ck "bind: pushes go to /data/local/tmp/gp-wfw-*" 3 "$(grep '^adb push' "$WLOG" | grep -c ' /data/local/tmp/gp-wfw-')"
ck "bind: global-ns su used (bind + readback)" 2 "$(grep -c -- 'adb shell su --mount-master -c sh' "$WLOG")"
ck "bind (model): three bind mounts and one tmpfs really executed" 4 "$(grep -c '^mount ' "$PH/mount.log")"; ck "bind (model): tmpfs file mode is 0444 (chmod ran)" 444 "$(stat -c %a "$PH/mnt/gp-wfw/fw_bcmdhd.bin")"
ck "bind (model): label copied from the stock file (chcon ran for 3 files)" 3 "$(grep -c '^chcon u:object_r:vendor_file:s0 ' "$PH/mount.log")"
ck "bind (model): the bound path reads the pinned content" "$p1" "$(sha256sum "$PH/vendor/firmware/fw_bcmdhd.bin" | cut -d' ' -f1)"
ck "bind (model): staged copies are gone from /data/local/tmp" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"
grep -q 'mount -t tmpfs -o size=16m,mode=0755 tmpfs /mnt/gp-wfw' "$WIN"; ck "bind: RAM tmpfs at /mnt/gp-wfw" 0 $?
ck "bind: label copied (chcon) for each file" 3 "$(grep -c 'chcon \$l /mnt/gp-wfw/' "$WIN")"
ck "bind: bind mount over each stock path" 3 "$(grep -c 'mount -o bind /mnt/gp-wfw/.* /vendor/firmware/' "$WIN")"
ck "bind: stock sha file has 3 lines" 3 "$(wc -l < "$w/wb1/wifi/fw-stock-sha.txt")"
ck "bind: staged copies removed (stage_clean)" 1 "$(grep -cF "$SC" "$WIN")"
ck "second bind without unbind is a GAP (rc 1)" 1 "$(gwc "$w/wm/golden-pull" --out "$w/wb1" wifi-fw-bind "$w/wfw")"; grep -q 'GAP: wifi-fw-bind: already bound' "$w/out"; ck "second bind: GAP names it" 0 $?
ck "second bind: no rollback (earlier bind kept)" 0 "$(grep -c 'umount' "$WIN")"; ck "second bind: stock sha file untouched" 3 "$(wc -l < "$w/wb1/wifi/fw-stock-sha.txt")"
ck "unbind rc 0 and read-back equals the stock sha" 0 "$(gw --out "$w/wb1" wifi-fw-unbind)"; ck "unbind: umount of each file + tmpfs" 4 "$(grep -c 'umount' "$WIN")"
ck "unbind: runs in the global ns (su --mount-master)" 1 "$(grep -c -- 'adb shell su --mount-master -c sh' "$WLOG")"; ck "unbind (model): nothing mounted afterwards" 0 "$(wc -l < "$PH/proc/mounts")"
ck "unbind (model): stock content back on the stock path" "stock fw_bcmdhd.bin" "$(cat "$PH/vendor/firmware/fw_bcmdhd.bin")"
ck "unbind again: nothing bound is INFO, rc 0" 0 "$(gw --out "$w/wb1" wifi-fw-unbind)"; grep -q 'info=1$' "$w/out"; ck "unbind again: info=1" 0 $?
ck "bind after unbind works again" 0 "$(gwc "$w/wm/golden-pull" --out "$w/wb2" wifi-fw-bind "$w/wfw")"
ck "unbind with a wrong read-back sha is a GAP (rc 1)" 1 "$(FAKE_UNBIND_SHA_BAD=1 gw --out "$w/wb2" wifi-fw-unbind)"; grep -q 'GAP: wifi-fw-unbind: after unbind' "$w/out"; ck "unbind sha GAP named" 0 $?
mk_phone
ck "unbind with a binds_left mount is a GAP" 1 "$(FAKE_BINDS_LEFT=1 gw --out "$w/wb2" wifi-fw-unbind)"
ck "unbind with no recorded stock sha is a GAP" 1 "$(gw --out "$w/wb9" wifi-fw-unbind)"
mk_phone
ck "bind: wrong sha through the bound path -> GAP rc 1" 1 "$(FAKE_BOUNDSHA_BAD=bcmdhd.cal gwc "$w/wm/golden-pull" --out "$w/wb3" wifi-fw-bind "$w/wfw")"; grep -q 'GAP: wifi-fw-bind: sha256 read through the bound path .*bcmdhd.cal is not the pinned' "$w/out"; ck "bound-sha GAP named (its own message, not the read-back one; r2 R2-9 B_noBoundShaChk)" 0 $?
grep -q 'rolling the bind back' "$w/out"; ck "bind GAP: rolled back" 0 $?; ck "bind GAP: rollback left nothing bound" 0 "$(wc -l < "$PH/proc/mounts")"
ck "bind: one file not bound -> GAP rc 1 + rollback" 1 "$(FAKE_BINDFAIL=bcmdhd.cal gwc "$w/wm/golden-pull" --out "$w/wb4" wifi-fw-bind "$w/wfw")"; grep -q 'GAP: wifi-fw-bind: bcmdhd.cal was not bound' "$w/out"; ck "unbound file GAP named" 0 $?
mk_phone
ck "bind: bad read-back (2nd process) -> GAP rc 1" 1 "$(FAKE_RB_BAD=1 gwc "$w/wm/golden-pull" --out "$w/wb5" wifi-fw-bind "$w/wfw")"; grep -q 'read-back (2nd su --mount-master process)' "$w/out"; ck "read-back GAP named" 0 $?
mk_phone
ck "bind: no tmpfs -> GAP rc 1" 1 "$(FAKE_NOTMPFS=1 gwc "$w/wm/golden-pull" --out "$w/wb6" wifi-fw-bind "$w/wfw")"
mk_phone
sed 's/chcon \\\$l /true \\$l /' "$w/wm/golden-pull" > "$w/wm/gp-nolabel"; chmod +x "$w/wm/gp-nolabel"; ! cmp -s "$w/wm/golden-pull" "$w/wm/gp-nolabel"
ck "bind: bound label != stock label -> GAP rc 1 (chcon missing in a copy)" 1 "$(gwc "$w/wm/gp-nolabel" --out "$w/wb7" wifi-fw-bind "$w/wfw")"; grep -q 'SELinux label of the bound' "$w/out"; ck "label GAP named" 0 $?
mk_phone
ck "bind: push failure -> GAP rc 1, no bind script" 1 "$(FAKE_PUSHFAIL=1 gwc "$w/wm/golden-pull" --out "$w/wb8" wifi-fw-bind "$w/wfw")"; ck "push failure: no --mount-master script sent" 0 "$(grep -c -- '--mount-master' "$WLOG")"
ck "push failure: the stage-clean script is still sent (r1 F12 M16)" 1 "$(grep -c 'rm -f /data/local/tmp/gp-wfw-fw_bcmdhd.bin' "$WIN")"; ! grep -q 'wf cleanup:' "$w/out"; ck "push failure: the normal path cleaned up itself (the exit-trap teardown did not have to)" 0 $?
mk_phone; rm -f "$PH/vendor/firmware/bcmdhd.cal"
ck "bind: an unreadable stock file (no stock sha256) -> GAP rc 1 (r1 F12 M27)" 1 "$(gwc "$w/wm/golden-pull" --out "$w/wb10" wifi-fw-bind "$w/wfw")"; grep -q "GAP: wifi-fw-bind: no valid stock sha256 for bcmdhd.cal" "$w/out"; ck "bind: no-stock-sha GAP named" 0 $?
mk_phone
mk_phone
never_ok; ck "bind/unbind: never-write list absent from sent commands (only rm -f of gp-wfw staging)" 0 $?
ck "bind/unbind: the only rm is the exact stage-clean string (and it was sent)" 0 "$(sent_stripped | grep -cE '(^|[^a-z])rm ')"; grep -qF "$SC" "$WALL"; ck "bind/unbind: the stage-clean string was sent" 0 $?
# --- wifi-kprobes / -off against the fake tracefs (the generated script really runs)
mk_trace; ck "kprobes rc 0" 0 "$(gw --out "$w/wk" wifi-kprobes)"; grep -qx 'kprobes_registered=29' "$w/out"; ck "kprobes prints kprobes_registered=29" 0 $?
grep -q '^RESULT: PASS wifi-kprobes gaps=0' "$w/out"; ck "kprobes RESULT PASS" 0 $?
ck "kprobes: 29 gpwifi probes in kprobe_events" 29 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"
ck "kprobes: tracing_on untouched" 1 "$(cat "$FAKE_T/tracing_on")"; ck "kprobes: trace buffer not cleared" KEEPME "$(cat "$FAKE_T/trace")"; ck "kprobes: buffer_size_kb untouched" 0 "$(cat "$FAKE_T/buffer_size_kb")"
ck "kprobes: P2 filter read back from the fake tracefs" "cmd == 262 || cmd == 263" "$(cat "$FAKE_T/events/gpwifi/iovar/filter")"
ck "kprobes: gpwifi group enabled in the global buffer" 1 "$(cat "$FAKE_T/events/gpwifi/enable")"; ck "kprobes: cfg80211 group enabled" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"
! grep -qE 'tracing_on|/trace( |$)|instances|trace_options|buffer_size' "$WIN"; ck "kprobes: script never writes tracing_on/trace/instances/buffer" 0 $?
grep -q "/sys/kernel/tracing/events/gpwifi/enable" "$WIN"; ck "kprobes: enables in the same \$T buffer stop-pull reads" 0 $?
grep -q 'cat \$T/trace\|cat /sys/kernel/tracing/trace' tools/golden-pull; ck "stop-pull still cats the global trace" 0 $?
ck "kprobes: P1-P24 texts verbatim (P2 name, P14 nested + rxoff, P15 flowid, P20 %x20 string, P21 sym+off)" 6 "$(grep -c -F -e 'name=+0($arg4):string' -e 'posted=+12(+16($arg1)):u16' -e 'flowid=+98($arg2):u16 tid=+100($arg2):u8 ifidx=+101($arg2):u8' -e 'rxoff=+940(+16($arg1)):u32' -e 'cap=+3520(%x20):string' -e 'wl_do_escan+0xfe4 plen=%x3:u32' "$FAKE_T/kprobe_events")"
never_ok; ck "kprobes: never-write list absent" 0 $?
mk_trace; ck "kprobes: one failing probe -> GAP rc 1" 1 "$(FAKE_KP_FAIL=cfgevent gw --out "$w/wk1" wifi-kprobes)"; grep -qx 'kprobes_registered=28' "$w/out"; ck "kprobes: count 19 printed" 0 $?; grep -q 'GAP: wifi-kprobes: .*kprobes_registered=28, expected 29' "$w/out"; ck "kprobes: count GAP names 19 vs 20" 0 $?
grep -q 'probe P4 gpwifi/cfgevent was not registered' "$w/out"; ck "kprobes: failing probe named" 0 $?
mk_trace; ck "kprobes: P2 filter read-back mismatch -> GAP rc 1" 1 "$(FAKE_FILTER_BAD=iovar gw --out "$w/wk2" wifi-kprobes)"; grep -q "GAP: wifi-kprobes: filter read-back of gpwifi/iovar (P2) is 'garbage'" "$w/out"; ck "kprobes: filter GAP names the value" 0 $?
mk_trace; ck "kprobes: gpwifi enable not 1 -> GAP rc 1" 1 "$(FAKE_EN_BAD=1 gw --out "$w/wk3" wifi-kprobes)"
NOCFG=1 mk_trace; ck "kprobes: no cfg80211 group is INFO, rc 0" 0 "$(gw --out "$w/wk4" wifi-kprobes)"; grep -q 'info=1$' "$w/out"; ck "kprobes: cfg80211 absent counted as info=1" 0 $?
# table extension: a copy with ONE more row (sym+offset, %xN register fetches, a second filter); N is derived, no hand-typed number
EXT="\t'P4c|p:gpwifi/cfgevent2 wl_cfg80211_event+0x10 etype=%x1:x32 st=+8(%x1):x32 w=+4(+16(\$arg2)):u16|etype == 0x45000000'"
sed "s#^\t'P18|#$EXT\n\t'P18|#" tools/golden-pull > "$w/wm/gp-ext"; chmod +x "$w/wm/gp-ext"
NOCFG=1 mk_trace cfgevent2; ck "ext table (sym+offset, %xN, second filter) rc 0" 0 "$(gwc "$w/wm/gp-ext" --out "$w/wk5" wifi-kprobes)"; grep -qx 'kprobes_registered=30' "$w/out"; ck "ext table: N derived = 30" 0 $?
grep -q 'wl_cfg80211_event+0x10 etype=%x1:x32 st=+8(%x1):x32' "$FAKE_T/kprobe_events"; ck "ext table: sym+offset and %xN probe registered verbatim" 0 $?
ck "ext table: second filter read back" "etype == 0x45000000" "$(cat "$FAKE_T/events/gpwifi/cfgevent2/filter")"
NOCFG=1 mk_trace cfgevent2; ck "ext table: second filter mismatch -> GAP rc 1" 1 "$(FAKE_FILTER_BAD=cfgevent2 gwc "$w/wm/gp-ext" --out "$w/wk6" wifi-kprobes)"; grep -q "filter read-back of gpwifi/cfgevent2 (P4c) is 'garbage', want 'etype == 0x45000000'" "$w/out"; ck "ext table: second filter GAP names it" 0 $?
ck "ext table in the probe file lists 30 events (dry-run N derived)" 0 "$(gwc "$w/wm/gp-ext" --dry-run --out "$w/wd" wifi-kprobes)"; grep -q 'kprobes_registered=30' "$w/out"; ck "ext table dry-run expects 30" 0 $?
# guard: whitelist of fetch forms (each variant is a copy of the tool with ONE extra/changed row; refused before any adb call)
gtry() { sed "s#^\t'P18|#\t$1\n\t'P18|#" tools/golden-pull > "$w/wm/gp-g"; chmod +x "$w/wm/gp-g"; mk_trace; local rc; rc=$(gwc "$w/wm/gp-g" --out "$w/wg" wifi-kprobes); echo "$rc $([ -s "$WIN" ] && echo sent || echo none)"; }
ck "guard: memory fetch through \$arg4 (x64) refused, nothing sent" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo b=+0(\$arg4):x64'")"
ck "guard: nested fetch through \$arg4 refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo b=+0(+8(\$arg4)):u8'")"
ck "guard: :string through \$arg4 outside P2 refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo name=+0(\$arg4):string'")"
ck "guard: memory fetch through %x3 (= \$arg4) refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo b=+0(%x3):x32'")"
ck "guard: unlisted fetch form (\$stack1) refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo b=\$stack1:x32'")"
ck "guard: unlisted type refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo b=\$arg1:bitfield'")"
ck "guard: \$retval in an entry probe refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo a=\$retval:x32'")"
ck "guard: a filter naming an unknown field refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz foo a=\$arg1:x32|bogus == 1'")"
ck "guard: a second probe with a used event name refused" "1 none" "$(gtry "'P4c|p:gpwifi/ioctl foo a=\$arg1:x32'")"
ck "guard: non-gpwifi group refused" "1 none" "$(gtry "'P4c|p:other/zz foo a=\$arg1:x32'")"
ck "guard: nested fetch through \$arg1 (P14 form) accepted" "0 sent" "$(gtry "'P4c|p:gpwifi/zz foo a=+4(+8(\$arg1)):u16'")"
ck "guard: plain register \$arg4:u8 (P12 form) accepted" "0 sent" "$(gtry "'P4c|p:gpwifi/zz foo a=\$arg4:u8'")"
ck "guard: sym+offset and %x30 accepted" "0 sent" "$(gtry "'P4c|p:gpwifi/zz foo+0x1c a=%x30:u64'")"
# P21 (escan params): SSID bytes 8..50 of the buffer in %x2 are never fetched; only the whitelisted forms pass
ck "guard P21: +8(%x2) (first SSID byte) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+8(%x2):u32'")"
ck "guard P21: +50(%x2) (last byte before the scan type) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+50(%x2):u8'")"
ck "guard P21: +44(%x2) (bssid) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+44(%x2):x32'")"
ck "guard P21: +4(%x2) u64 (reaches into byte 8..11) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+4(%x2):u64'")"
ck "guard P21: +30(%x2) string refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+30(%x2):string'")"
ck "guard P21: hex offset 0x10 (inside 8..50) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0x10(%x2):u32'")"
ck "guard P21: another base register (%x5) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0(%x5):u32'")"
ck "guard P21: \$arg2 base refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0(\$arg2):u32'")"
ck "guard P21: nested fetch refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0(+60(%x2)):u32'")"
ck "guard P21: plain %x2 (the pointer) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=%x2:x64'")"
ck "guard P21: +0/+4/+6/+7(%x2) accepted" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0(%x2):u32 b=+4(%x2):u16 c=+6(%x2):u16 d=+7(%x2):u8'")"
ck "guard P21: +51(%x2) u8 (BSSID byte in the v2/v3 layout) refused (r1 F2)" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+51(%x2):u8'")"
ck "guard P21: +52(%x2) s32 (bss_type/pad in v2/v3) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+52(%x2):s32'")"
ck "guard P21: +55(%x2) u8 (last pad byte) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+55(%x2):u8'")"
ck "guard P21: +53(%x2) u32 (bytes 53..56, crosses the end) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+53(%x2):u32'")"
ck "guard P21: +56(%x2) u8 and +76(%x2) x32 and hex +0x38 accepted" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+56(%x2):u8 b=+76(%x2):x32 c=+0x38(%x2):s32'")"
ck "guard P21: +5(%x2) u32 (bytes 5..8: touches byte 8) refused (r1 F12 M06)" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+5(%x2):u32'")"
ck "guard P21: +7(%x2) u16 (bytes 7..8) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+7(%x2):u16'")"
ck "guard P21: +4(%x2) u32 (bytes 4..7) accepted" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+4(%x2):u32'")"
# keyed on the probed symbol, not the label (r1 F6)
ck "guard: the escan rule holds under another label (P99 at wl_do_escan+0xfe4)" "1 none" "$(gtry "'P99|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+8(%x2):u32'")"
ck "guard: the escan rule holds at wl_do_escan without an offset" "1 none" "$(gtry "'P99|p:gpwifi/zz1 wl_do_escan a=+8(%x2):u32'")"
ck "guard: the escan rule holds at another offset of wl_do_escan" "1 none" "$(gtry "'P99|p:gpwifi/zz1 wl_do_escan+0x10 a=+20(%x2):u8'")"
ck "guard: the escan rule holds at wldev_iovar_setbuf (receives the same buffer)" "1 none" "$(gtry "'P99|p:gpwifi/zz1 wldev_iovar_setbuf a=+8(%x2):u32'")"
ck "guard: label P21 on another symbol is not special" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 foo a=+8(%x2):u32'")"
ck "guard: the P2 \$arg4 string exception is keyed on symbol+event+filter, not the label" "1 none" "$(gtry "'P2|p:gpwifi/zz1 foo name=+0(\$arg4):string'")"
# offsets: an evaluation failure is a refusal, never a PASS (r1 F7)
ck "guard P21: +08(%x2) (arithmetic error in bash) refused, rc 1" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+08(%x2):u32'")"; grep -q 'REFUSED' "$w/out"; ck "guard P21: +08 refusal printed" 0 $?; ! grep -q 'value too great\|RESULT: PASS' "$w/out"; ck "guard P21: +08 is no arithmetic error and no PASS" 0 $?
ck "guard P21: +010(%x2) (octal) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+010(%x2):u32'")"
ck "guard P21: +0x10000(%x2) (5 hex digits) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0x10000(%x2):u8'")"
ck "guard P21: +100000(%x2) (6 digits) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+100000(%x2):u8'")"
ck "guard P21: +0xFFFFFFFFFFFFFFF8(%x2) (wraps negative) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0xFFFFFFFFFFFFFFF8(%x2):u8'")"
ck "guard P21: -8(%x2) refused" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=-8(%x2):u8'")"
ck "guard P21: +0100(%x2) (leading zero, octal 64 in bash) refused: strict decimal form only (r2)" "1 none" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+0100(%x2):u8'")"
grep -q 'is not a whitelisted fetch form' "$w/out"; ck "guard P21: +0100 is refused by the general fetch whitelist (the P21 regex's own leading-zero rule is unreachable defence in depth: mutant P_leadzero is equivalent)" 0 $?
ck "guard P21: +8(%x20) and +20(%x20) (driver struct, offsets inside 8..55 are fine there) accepted (r2 mutant P_x20)" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+8(%x20):u32 b=+20(%x20):u8'")"
ck "guard: a leading-zero offset is refused in any probe (+08(\$arg1))" "1 none" "$(gtry "'P4c|p:gpwifi/zz1 foo a=+08(\$arg1):u32'")"
ck "guard: a leading-zero symbol offset is refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz1 foo+010 a=\$arg1:u32'")"
# the reasons are printed, so a removed check cannot hide behind another refusal (r1 F12 M13/M14)
gtry "\"P4c|p:gpwifi/zz1 foo a=%x1:x32 '\"" >/dev/null; grep -q 'probe P4c holds a quote' "$w/out"; ck "guard: a quote in a probe text is refused with its own reason" 0 $?
gtry "'P4c|p:gpwifi/zz1 foo a=\$arg1:x32|a == 1 ; a'" >/dev/null; grep -q 'filter has a character outside the allowed set' "$w/out"; ck "guard: a filter character outside the set (;) is refused with its own reason" 0 $?
ck "guard: a forbidden word as a probe symbol is refused" "1 none" "$(gtry "'P4c|p:gpwifi/zz1 reboot a=\$arg1:x32'")"; grep -q 'forbidden word' "$w/out"; ck "guard: forbidden-word reason named" 0 $?
ck "guard P21: +4274(%x20) and plain %x3 accepted" "0 sent" "$(gtry "'P21|p:gpwifi/zz1 wl_do_escan+0xfe4 a=+4274(%x20):u8 b=%x3:u32'")"
ck "guard: +8(%x2) is accepted outside P21 (rule is P21-specific)" "0 sent" "$(gtry "'P4c|p:gpwifi/zz1 foo a=+8(%x2):u32'")"
ck "guard: :string through %x20 (P20 form) accepted" "0 sent" "$(gtry "'P4c|p:gpwifi/zz1 foo+0x80 cap=+3520(%x20):string'")"
# P4b evdata: the second filter of the real table is written and read back
mk_trace; gw --out "$w/wk10" wifi-kprobes >/dev/null; ck "kprobes: P4b evdata filter read back from the fake tracefs" "etype == 0x45000000" "$(cat "$FAKE_T/events/gpwifi/evdata/filter")"
mk_trace; ck "kprobes: P4b evdata filter mismatch -> GAP rc 1" 1 "$(FAKE_FILTER_BAD=evdata gw --out "$w/wk11" wifi-kprobes)"; grep -q "filter read-back of gpwifi/evdata (P4b) is 'garbage', want 'etype == 0x45000000'" "$w/out"; ck "kprobes: evdata filter GAP named" 0 $?
mk_trace; ck "kprobes: the P2 and the P4b filter are both written" 2 "$(gw --out "$w/wk12" wifi-kprobes >/dev/null; grep -c '^echo .* > .*/filter' "$WIN")"
# the guard also refuses a forbidden word (never-write list) in anything a mode sends: copy with the word injected into the scan script
sed 's/echo scan_list=ok" 90 wifi_scan_mask/echo scan_list=ok; reboot" 90 wifi_scan_mask/' tools/golden-pull > "$w/wm/gp-reboot"; chmod +x "$w/wm/gp-reboot"
ck "guard: forbidden word in a script is REFUSED (rc 1)" 1 "$(gwc "$w/wm/gp-reboot" --out "$w/wr" wifi-scan)"; grep -q 'REFUSED: wifi-scan: script holds a forbidden word' "$w/out"; ck "guard: reason named" 0 $?; ck "guard: nothing sent to adb shell su" 0 "$(wc -c < "$WIN")"
# every word of the never-write list (own copy, independent of the tool's regex) and the r1 F8 additions, injected into the scan script of a copy: refused, nothing sent
for fw in 'dd of=/data/x' 'echo x > /dev/block/sda' 'cmd flash x' 'fastboot x' 'mkfs x' 'reboot' 'settings put global x 1' 'mount -o remount /vendor' 'pm clear x' 'pm uninstall x' 'memdump' 'assert' 'hang_privcmd_err' 'dump_start' 'dump_stateinfo' 'sssr_enab' 'fis_enab' 'wl_accel_force_reg_on' 'firmware_path' 'nvram_path' 'cat clk_summary' 'cat clk_dump' 'rmmod x' 'insmod x' 'rm -f /data/other' 'rm -f /data/local/tmp/gp-wfw-x /data/other' 'svc wifi disable' 'setprop x y' 'setenforce 0' 'dmesg -c' 'dmesg -C' 'logcat -c' 'cmd wifi set-wifi-enabled disabled' 'mv a b' 'truncate -s 0 f' 'kill 1' 'killall x' 'pkill x' 'echo x > /dev/foo' 'echo x >> /dev/foo'; do
	sed "s#echo scan_list=ok\" 90 wifi_scan_mask#echo scan_list=ok; $fw\" 90 wifi_scan_mask#" tools/golden-pull > "$w/wm/gp-fw"; chmod +x "$w/wm/gp-fw"; ! cmp -s tools/golden-pull "$w/wm/gp-fw"
	ck "guard: '$fw' refused, nothing sent" "1 none" "$(rc=$(gwc "$w/wm/gp-fw" --out "$w/wr" wifi-scan); echo "$rc $([ -s "$WIN" ] && echo sent || echo none)")"; done
sed "s#echo scan_list=ok\" 90 wifi_scan_mask#echo scan_list=ok; $SC\" 90 wifi_scan_mask#" tools/golden-pull > "$w/wm/gp-fw"; chmod +x "$w/wm/gp-fw"
gwc "$w/wm/gp-fw" --out "$w/wr" wifi-scan >/dev/null; ! grep -q REFUSED "$w/out"; ck "guard: the exact stage-clean string alone is the one allowed rm (not refused; the fake answers it as a stage-clean)" 0 $?
sed "s#echo scan_list=ok\" 90 wifi_scan_mask#echo scan_list=ok; echo x > /dev/null; echo y 2> /dev/null\" 90 wifi_scan_mask#" tools/golden-pull > "$w/wm/gp-fw"; chmod +x "$w/wm/gp-fw"
ck "guard: > /dev/null and > /dev/kmsg stay allowed" "0 sent" "$(rc=$(gwc "$w/wm/gp-fw" --out "$w/wr" wifi-scan); echo "$rc $([ -s "$WIN" ] && echo sent || echo none)")"
# WFR (exec-out pulls) is guarded too (r1 F12 M12)
sed 's#WFR wifi-dmesg-full.txt "dmesg"#WFR wifi-dmesg-full.txt "dmesg; reboot"#' tools/golden-pull > "$w/wm/gp-wfr"; chmod +x "$w/wm/gp-wfr"; ! cmp -s tools/golden-pull "$w/wm/gp-wfr"
ck "guard: a forbidden word in a WFR pull command is REFUSED (rc 1)" 1 "$(gwc "$w/wm/gp-wfr" --out "$w/wr2" wifi-trace-pull)"; grep -q 'REFUSED: wifi-trace-pull: script holds a forbidden word' "$w/out"; ck "guard: WFR reason named" 0 $?; ck "guard: WFR refused before any adb exec-out" 0 "$(grep -c 'adb exec-out' "$WLOG")"
# -off
mk_trace; gw --out "$w/wk7" wifi-kprobes >/dev/null
ck "kprobes-off rc 0" 0 "$(gw --out "$w/wk7" wifi-kprobes-off)"; grep -q '^RESULT: PASS wifi-kprobes-off gaps=0' "$w/out"; ck "kprobes-off RESULT PASS" 0 $?
ck "kprobes-off: no gpwifi probe left in the fake tracefs" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"; ck "kprobes-off: cfg80211 group disabled" 0 "$(cat "$FAKE_T/events/cfg80211/enable")"
ck "kprobes-off: events/gpwifi gone" 0 "$(absent "$FAKE_T/events/gpwifi")"
mk_trace; gw --out "$w/wk8" wifi-kprobes >/dev/null
ck "kprobes-off: a probe that stays -> GAP rc 1" 1 "$(FAKE_KP_KEEP=ioctl gw --out "$w/wk8" wifi-kprobes-off)"; grep -q 'GAP: wifi-kprobes-off: gpwifi_left=' "$w/out"; ck "kprobes-off: left-over probe named" 0 $?
mk_trace; rm -rf "$FAKE_T/events/gpwifi"; ck "kprobes-off with nothing armed: INFO rc 0" 0 "$(gw --out "$w/wk9" wifi-kprobes-off)"
never_ok; ck "kprobes-off: never-write list absent" 0 $?
# --- wifi-scan
ck "wifi-scan PASS rc 0" 0 "$(gw --out "$w/wsc" wifi-scan)"; grep -q '^RESULT: PASS wifi-scan gaps=0' "$w/out"; ck "scan RESULT PASS" 0 $?
ck "scan: 6 result rows with the SSID column masked" 6 "$(grep -c '<ssid-masked>' "$w/wsc/wifi/wifi-scan.txt")"
ck "scan: no neighbour SSID left (incl. 32-char, multibyte, double-space/bracket SSIDs: whole tail masked, r1 F10)" 0 "$(grep -c -e NeighborNet -e 'Cafe Free' -e Abcdefghijklmnop -e 012345 -e 'Caf' -e 'net]' -e 'my  ' "$w/wsc/wifi/wifi-scan.txt")"
ck "scan: our SSID (with / & . \\ ') not left" 0 "$(grep -c -F "$WSSID" "$w/wsc/wifi/wifi-scan.txt")"; ck "scan: no MAC left" 0 "$(grep -cE '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' "$w/wsc/wifi/wifi-scan.txt")"
ck "scan: BSSIDs masked" 6 "$(grep -c '^  <masked>  ' "$w/wsc/wifi/wifi-scan.txt")"; grep -qx 'scan_list=ok' "$w/wsc/wifi/wifi-scan.txt"; ck "scan: scan_list=ok marker" 0 $?
exp=$(printf "  %s  %9d  %18s   %7s    %s" '<masked>' 2412 '      -50         ' '  1.250' '<ssid-masked>'); grep -qxF "$exp" "$w/wsc/wifi/wifi-scan.txt"; ck "scan: a masked row keeps exactly the 64-column prefix (rebuilt from the ScanResultUtil format; kills a shifted mask column, r1 F10/M20)" 0 $?
exp=$(printf "  %s  %9d  %18s   %7s    %s" '<masked>' 2462 '      -72         ' '  4.250' '<ssid-masked>'); grep -qxF "$exp" "$w/wsc/wifi/wifi-scan.txt"; ck "scan: the 32-char SSID row is masked to the same shape" 0 $?
grep -q 'cmd wifi start-scan && echo scan_start=ok && sleep 8 && cmd wifi list-scan-results && echo scan_list=ok' "$WIN"; ck "scan: start-scan, 8 s wait, list-scan-results chained" 0 $?
ck "scan: No scan results -> GAP rc 1" 1 "$(FAKE_NOSCAN=1 gw --out "$w/wsc2" wifi-scan)"; grep -q "GAP: wifi-scan: 'No scan results'" "$w/out"; ck "scan: No scan results GAP named" 0 $?
ck "scan: an unparseable line -> GAP rc 1" 1 "$(FAKE_SCANODD=1 gw --out "$w/wsc3" wifi-scan)"
ck "scan: a row whose frequency column is not a number -> GAP rc 1" 1 "$(FAKE_SCANODD2=1 gw --out "$w/wsc5" wifi-scan)"; grep -q 'line(s) are neither header, marker nor a masked result row' "$w/out"; ck "scan: odd-row GAP named" 0 $?
ck "scan: missing marker -> GAP rc 1" 1 "$(FAKE_MISS=scan_list=ok gw --out "$w/wsc4" wifi-scan)"
never_ok; ck "scan: never-write list absent" 0 $?
# --- wifi-trace-pull + S1
ck "wifi-trace-pull PASS rc 0" 0 "$(gw --out "$w/wt" wifi-trace-pull)"; grep -q '^RESULT: PASS wifi-trace-pull gaps=0' "$w/out"; ck "trace-pull RESULT PASS" 0 $?; grep -qx 'S1: psk_hits=0 ssid_hits=0 mac_hits=0' "$w/out"; ck "trace-pull: S1 line zero hits" 0 $?
ck "trace-pull: 5 files written" 5 "$(ls "$w/wt/wifi" | wc -l)"
grep -q '<ssid>' "$w/wt/wifi/wifi-dmesg-full.txt"; ck "pull: SSID literal (with / & . \\ ') masked to <ssid> in dmesg" 0 $?; grep -q '<ssid>' "$w/wt/wifi/wifi-logcat.txt"; ck "pull: SSID masked in logcat" 0 $?
ck "pull: no SSID literal in any pulled file" 0 "$(grep -c -F "$WSSID" "$w/wt/wifi/"* | awk -F: '{s += $2} END {print s}')"
ck "pull: MACs (colon and dash forms) masked" 0 "$(grep -cE '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}' "$w/wt/wifi/"* | awk -F: '{s += $2} END {print s}')"
grep -qF "a/b&cXd\\e'f stays" "$w/wt/wifi/wifi-dmesg-full.txt"; ck "pull: the decoy (. and / are literal in the SSID mask) is NOT masked" 0 $?
ck "pull: rc sentinel stripped" 0 "$(grep -c '__GP_RC' "$w/wt/wifi/"* | awk -F: '{s += $2} END {print s}')"
grep -q "FW supports Inband dw|sent DEEP SLEEP ACK|Deepsleep On|rx_dataoffset" "$WLOG"; ck "pull: markers grep gains the deep-sleep/rx_dataoffset strings" 0 $?
grep -q "grep -iE 'wifi|wlan|supplicant|wpa_|WifiHAL|dhd'" "$WLOG"; ck "pull: filtered logcat command" 0 $?; grep -q 'timeout 5 dd if=/proc/dhd_trace bs=4096 count=64' "$WLOG"; ck "pull: dhd_trace read with timeout dd" 0 $?; grep -q 'dumpsys wifi | head -c 2000000' "$WLOG"; ck "pull: dumpsys capped at 2000000" 0 $?
ck "pull: PSK and SSID never in any adb argv or script" 0 "$(cat "$WLOG" "$WIN" | grep -c -F -e "$WPSK" -e "$WSSID")"
ck "trace-pull: no dhd_trace is INFO, rc 0, info=1" 0 "$(FAKE_NODHD=1 gw --out "$w/wt2" wifi-trace-pull)"; grep -q 'info=1$' "$w/out"; ck "trace-pull: info=1" 0 $?
ck "trace-pull: no idsup evidence -> GAP rc 1" 1 "$(FAKE_NOIDSUP=1 gw --out "$w/wt3" wifi-trace-pull)"; grep -q 'GAP: wifi-trace-pull: no PSK_SUP evidence in dmesg' "$w/out"; ck "trace-pull: idsup GAP named" 0 $?
ck "trace-pull: pull rc != 0 -> GAP rc 1" 1 "$(FAKE_PULLRC=1 gw --out "$w/wt4" wifi-trace-pull)"
ck "trace-pull: empty pull -> GAP rc 1" 1 "$(FAKE_EMPTYPULL=1 gw --out "$w/wt5" wifi-trace-pull)"
# S1 scopes: PSK over the WHOLE out dir; SSID/MAC only over the 5 files this mode wrote
mkdir -p "$w/wt6"; printf 'trace row psk=%s\n' "$WPSK" > "$w/wt6/trace-wifi5.txt"
ck "S1: PSK planted in a stop-pull file -> hit, rc 1" 1 "$(gw --out "$w/wt6" wifi-trace-pull)"; grep -q 'GAP: S1 PSK hit: trace-wifi5.txt' "$w/out"; ck "S1: PSK hit names the file" 0 $?; ck "S1: a PSK hit in a file this run did NOT write is not deleted (evidence stays, loud GAP; r1 F3)" 1 "$(absent "$w/wt6/trace-wifi5.txt")"; grep -q 'NOT written by this run: left in place' "$w/out"; ck "S1: the not-deleted hit says so" 0 $?
grep -q -F "$WPSK" "$w/out"; ck "S1: the PSK is never printed" 1 $?; ck "S1: the PSK is still in exactly that one older file" 1 "$(grep -rlaF "$WPSK" "$w/wt6" | wc -l)"
mkdir -p "$w/wt7/wifi"; printf 'psk in raw %s\n' "$WPSK" > "$w/wt7/wifi/raw-x.txt"
ck "S1: PSK in a wifi/raw- file is still a hit (rc 1)" 1 "$(gw --out "$w/wt7" wifi-trace-pull)"; ck "S1: the wifi/raw- file was not written by this run: kept" 1 "$(absent "$w/wt7/wifi/raw-x.txt")"
ck "PSK in logcat is masked to <psk> before it reaches the disk: no S1 hit, rc 0 (r1 F4)" 0 "$(FAKE_PSKLEAK=1 gw --out "$w/wt8" wifi-trace-pull)"; grep -qx 'S1: psk_hits=0 ssid_hits=0 mac_hits=0' "$w/out"; ck "PSK masked: S1 line zero hits" 0 $?; grep -q 'wpa psk=<psk>' "$w/wt8/wifi/wifi-logcat.txt"; ck "PSK masked: logcat file holds <psk>" 0 $?; ck "PSK masked: the PSK is in no file of the out dir" 0 "$(grep -rlaF "$WPSK" "$w/wt8" | wc -l)"
mkdir -p "$w/wt9"; printf 'connected to %s at aa:bb:cc:dd:ee:ff\n' "$WSSID" > "$w/wt9/wifi-status.txt"
ck "S1: SSID + MAC planted in a wifi-status file (unmasked by design) -> NO hit, rc 0" 0 "$(gw --out "$w/wt9" wifi-trace-pull)"; grep -qx 'S1: psk_hits=0 ssid_hits=0 mac_hits=0' "$w/out"; ck "S1: wifi-status not counted" 0 $?
sed 's/^wifi_mask() {.*/wifi_mask() { cat; }/' tools/golden-pull > "$w/wm/gp-nomask"; chmod +x "$w/wm/gp-nomask"; grep -q '^wifi_mask() { cat; }' "$w/wm/gp-nomask"; ck "nomask copy built" 0 $?
ck "S1: SSID + MAC in a pulled wifi/ file (mask broken in a copy) -> hit, rc 1" 1 "$(gwc "$w/wm/gp-nomask" --out "$w/wt10" wifi-trace-pull)"; grep -q 'GAP: wifi-trace-pull: S1 SSID literal in wifi/wifi-dmesg-full.txt' "$w/out"; ck "S1: SSID hit names the dmesg file" 0 $?; grep -q 'GAP: wifi-trace-pull: S1 MAC pattern in wifi/wifi-dmesg-full.txt' "$w/out"; ck "S1: MAC hit names the dmesg file" 0 $?
grep -q -F "$WSSID" "$w/out"; ck "S1: SSID never printed" 1 $?
ck "S1: PSK/SSID literal files are mode 600 and used with grep -F -f" 0 "$(PATH="$w/wgrep:$wfb:$PATH" tools/golden-pull --out "$w/wt11" wifi-trace-pull >/dev/null 2>&1 </dev/null; echo $?)"
grep -q '^mode 600$' "$w/wgrep.log"; ck "S1: the -f literal file has mode 600" 0 $?; ck "S1: no mode other than 600" 0 "$(grep '^mode ' "$w/wgrep.log" | grep -vc '^mode 600$')"
ck "S1: PSK/SSID never in grep argv" 0 "$(grep -c -F -e "$WPSK" -e "$WSSID" "$w/wgrep.log")"
never_ok; ck "trace-pull: never-write list absent" 0 $?
mkdir -p "$w/tmpd"; TMPDIR=$w/tmpd gw --out "$w/wt12" wifi-trace-pull >/dev/null; ck "secret temp dir is removed after the run" 0 "$(ls -A "$w/tmpd" | wc -l)"
# ---- review golden-wifi-modes-r1 (F1-F14) -------------------------------------------------------------------------------------------------------------------------------------------
echo "== wifi trace modes: review golden-wifi-modes-r1"
# signals: run a mode in the background, wait until the fake adb logged a marker, send the signal, collect the exit code (SIGINT is ignored by background jobs of a non-interactive shell: TERM and HUP are tested)
sigtest() { local sg=$1 file=$2 pat=$3 g=$4 pid i rc; shift 4
	: > "$WLOG"; : > "$WIN"; PATH="$wfb:$PATH" "$g" "$@" > "$w/out" 2>&1 </dev/null & pid=$!
	for ((i = 0; i < 150; i++)); do grep -q -- "$pat" "$file" 2>/dev/null && break; sleep 0.1; done
	kill -"$sg" "$pid"; wait "$pid"; rc=$?; cat "$WLOG" "$WIN" >> "$WALL"; echo $rc; }
# F1: TERM / HUP while the 2nd firmware push runs -> the staged copies are removed, nothing is bound, RESULT FAIL
mk_phone; ck "F1 bind: TERM during the 2nd push -> rc 1" 1 "$(FAKE_PUSH_SLEEP=2 sigtest TERM "$WLOG" 'gp-wfw-bcmdhd.cal' "$w/wm/golden-pull" --out "$w/wb20" wifi-fw-bind "$w/wfw")"
grep -q '^RESULT: FAIL wifi-fw-bind interrupted by SIGTERM' "$w/out"; ck "F1 bind: RESULT FAIL names the signal" 0 $?; ck "F1 bind: no firmware copy left in /data/local/tmp" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"
ck "F1 bind: the stage-clean script was sent after the signal" 1 "$(grep -cF "$SC" "$WIN")"; ck "F1 bind: nothing was bound" 0 "$(wc -l < "$PH/proc/mounts")"; ck "F1 bind: no global-ns script was sent" 0 "$(grep -c -- '--mount-master' "$WLOG")"
mk_phone; ck "F1 bind: HUP during the 2nd push -> rc 1, copies removed" 1 "$(FAKE_PUSH_SLEEP=2 sigtest HUP "$WLOG" 'gp-wfw-bcmdhd.cal' "$w/wm/golden-pull" --out "$w/wb21" wifi-fw-bind "$w/wfw")"; ck "F1 bind: HUP leaves no copy" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"
# F1: TERM while the bind is ACTIVE (during the read-back) -> unbound again, stock content back, copies removed
mk_phone; ck "F1 bind: TERM after the bind (during the read-back) -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo readback_end=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'readback_end=ok' "$w/wm/golden-pull" --out "$w/wb22" wifi-fw-bind "$w/wfw")"
ck "F1 bind: the unfinished bind was rolled back (nothing mounted)" 0 "$(wc -l < "$PH/proc/mounts")"; ck "F1 bind: stock content is back on the stock path" "stock fw_bcmdhd.bin" "$(cat "$PH/vendor/firmware/fw_bcmdhd.bin")"
ck "F1 bind: copies removed after the rollback" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"; ck "F1 bind: the unbind script was sent" 1 "$(grep -c 'echo unbind_script_end=ok' "$WIN")"
# a normal successful bind keeps the bind (the flags are cleared): no unbind script after RESULT PASS
mk_phone; ck "F1 bind: a good bind stays bound after a normal exit" 0 "$(gwc "$w/wm/golden-pull" --out "$w/wb23" wifi-fw-bind "$w/wfw")"; ck "F1 bind: good bind: no unbind script sent" 0 "$(grep -c 'echo unbind_script_end=ok' "$WIN")"; ck "F1 bind: good bind: 3 bind mounts + tmpfs in the model" 4 "$(wc -l < "$PH/proc/mounts")"
mk_phone
# F5: a failed arm disarms again; the prior cfg80211 value is recorded and restored; -off without a record says so
CFG0=1 mk_trace; ck "F5 kprobes: GAP while arming -> rc 1" 1 "$(FAKE_KP_FAIL=cfgevent gw --out "$w/wk20" wifi-kprobes)"; grep -q 'GAPs above, disarming' "$w/out"; ck "F5 kprobes: disarm announced" 0 $?
ck "F5 kprobes: no gpwifi probe left after the failed arm" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"; ck "F5 kprobes: events/gpwifi gone" 0 "$(absent "$FAKE_T/events/gpwifi")"; ck "F5 kprobes: cfg80211/enable back to its prior value 1" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"
ck "F5 kprobes: the disarm output exists" 1 "$(absent "$w/wk20/wifi/kprobes-disarm.txt")"; ! grep -q 'wf cleanup:' "$w/out"; ck "F5 kprobes: the disarm ran in the normal path (the exit-trap teardown did not have to)" 0 $?
CFG0=1 mk_trace; ck "F5 kprobes: filter GAP also disarms" 1 "$(FAKE_FILTER_BAD=iovar gw --out "$w/wk21" wifi-kprobes)"; ck "F5 kprobes: filter GAP: nothing left armed" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"
CFG0=1 mk_trace; ck "F5 kprobes: a good arm records the prior cfg80211 value" 0 "$(gw --out "$w/wk22" wifi-kprobes)"; ck "F5 kprobes: prior value 1 recorded" 1 "$(cat "$w/wk22/wifi/cfg80211-prev.txt")"; ck "F5 kprobes: a good arm stays armed (29 probes)" 29 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"
ck "F5 kprobes-off: restores the prior value (cfg80211 stays 1)" 0 "$(gw --out "$w/wk22" wifi-kprobes-off)"; ck "F5 kprobes-off: cfg80211/enable is 1 again" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"; ck "F5 kprobes-off: the record is consumed" 0 "$(absent "$w/wk22/wifi/cfg80211-prev.txt")"
CFG0=0 mk_trace; gw --out "$w/wk28" wifi-kprobes >/dev/null; ck "F5 kprobes-off: a cfg80211/enable that does not take the restored value is a GAP (rc 1)" 1 "$(FAKE_CFG_STUCK=1 gw --out "$w/wk28" wifi-kprobes-off)"; grep -q "events/cfg80211/enable reads '1', not the recorded prior value 0" "$w/out"; ck "F5 kprobes-off: the stuck value is named" 0 $?
mk_trace; echo 1 > "$FAKE_T/events/cfg80211/enable"; ck "F5 kprobes-off without a record: writes 0, rc 0" 0 "$(gw --out "$w/wk23" wifi-kprobes-off)"; grep -q 'no recorded prior events/cfg80211/enable' "$w/out"; ck "F5 kprobes-off without a record: INFO says 0 is assumed" 0 $?; ck "F5 kprobes-off without a record: cfg80211 written 0" 0 "$(cat "$FAKE_T/events/cfg80211/enable")"
# F5/F1: TERM while the probes are being armed -> disarmed
CFG0=1 mk_trace; ck "F5 kprobes: TERM during the arm script -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo kprobes_script_end=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'kprobes_script_end=ok' tools/golden-pull --out "$w/wk24" wifi-kprobes)"
grep -q '^RESULT: FAIL wifi-kprobes interrupted by SIGTERM' "$w/out"; ck "F5 kprobes: TERM: RESULT FAIL names the signal" 0 $?; ck "F5 kprobes: TERM: no gpwifi probe left" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"; ck "F5 kprobes: TERM: cfg80211/enable restored to 1" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"
# F14: a different module build is refused before anything is armed
mk_trace; ck "F14 kprobes: another bcmdhd4389.ko sha -> GAP rc 1" 1 "$(FAKE_KO_SHA=0000000000000000000000000000000000000000000000000000000000000000 gw --out "$w/wk25" wifi-kprobes)"; grep -q 'nothing armed' "$w/out"; ck "F14 kprobes: reason printed" 0 $?
ck "F14 kprobes: no kprobe script was sent" 0 "$(grep -c 'kprobe_events' "$WIN")"; ck "F14 kprobes: nothing registered" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"
mk_trace; ck "F14 kprobes: no module file found -> GAP rc 1, nothing armed" 1 "$(FAKE_KO_NONE=1 gw --out "$w/wk26" wifi-kprobes)"; ck "F14 kprobes: none: nothing sent to the tracefs" 0 "$(grep -c 'kprobe_events' "$WIN")"
mk_trace; ck "F14 kprobes: the matching sha arms" 0 "$(gw --out "$w/wk27" wifi-kprobes)"; grep -q "^ko_sha256=222cd8d65290" "$w/out"; ck "F14 kprobes: the matched sha is printed" 0 $?
# F3: the secret file inside the out dir is refused; S1 never deletes it; a grep rc 2 is loud and its hits are still handled
mkdir -p "$w/wt13"; cp "$GP_WIFI_SECRET" "$w/wt13/.wifi-join"; ck "F3 S1: secret file inside the out dir -> REFUSED rc 1" 1 "$(GP_WIFI_SECRET=$w/wt13/.wifi-join gw --out "$w/wt13" wifi-trace-pull)"; grep -q 'is inside the output dir' "$w/out"; ck "F3 S1: reason named" 0 $?; ck "F3 S1: the secret file still exists" 1 "$(absent "$w/wt13/.wifi-join")"
mkdir -p "$w/wt13b"; cp "$GP_WIFI_SECRET" "$w/wt13b/.wifi-join"; ck "F3 S1: out = parent of the secret's dir is refused too" 1 "$(GP_WIFI_SECRET=$w/wt13b/.wifi-join gw --out "$w" wifi-trace-pull)"
if [ "$(id -u)" != 0 ]; then mkdir -p "$w/wt14"; printf 'older psk=%s\n' "$WPSK" > "$w/wt14/old.txt"; printf 'x' > "$w/wt14/unreadable"; chmod 000 "$w/wt14/unreadable"
	ck "F3 S1: an unreadable file -> grep rc 2 is a GAP, rc 1" 1 "$(gw --out "$w/wt14" wifi-trace-pull)"; grep -q 'S1 PSK grep (psk.lit) failed rc=2: the scan is INCOMPLETE' "$w/out"; ck "F3 S1: rc 2 named as INCOMPLETE" 0 $?; grep -q 'GAP: S1 PSK hit: old.txt' "$w/out"; ck "F3 S1: the hit found before the rc 2 is still reported" 0 $?; chmod 600 "$w/wt14/unreadable"
else echo "SKIP: running as root: an unreadable file cannot be made (F3 grep rc 2 test not run)"; skips=$((skips + 1)); fi
# F4: a nomask copy of the tool: the PSK reaches a pulled file, S1 deletes that file (written by this run) and says so
ck "F4 S1: PSK in a pulled file (mask broken in a copy) -> hit rc 1" 1 "$(FAKE_PSKLEAK=1 gwc "$w/wm/gp-nomask" --out "$w/wt15" wifi-trace-pull)"; grep -q 'GAP: S1 PSK hit: wifi/wifi-logcat.txt (file written by this run: deleted' "$w/out"; ck "F4 S1: the pulled file is named and deleted" 0 $?; ck "F4 S1: the logcat file is gone" 0 "$(absent "$w/wt15/wifi/wifi-logcat.txt")"
ck "F4 S1: PSK in a pulled file, mask broken: the PSK is in no file afterwards" 0 "$(grep -rlaF "$WPSK" "$w/wt15" | wc -l)"
# F4: CRLF secret file: CR stripped, so the literal masks match; a control character is a refusal
printf '%s\r\n%s\r\n' "$WSSID" "$WPSK" > "$w/wsecret-crlf"; ck "F4: CRLF secret file: pull PASS rc 0" 0 "$(FAKE_PSKLEAK=1 GP_WIFI_SECRET=$w/wsecret-crlf gw --out "$w/wt16" wifi-trace-pull)"; grep -q 'wpa psk=<psk>' "$w/wt16/wifi/wifi-logcat.txt"; ck "F4: CRLF secret: the PSK is masked (CR stripped)" 0 $?; grep -q '<ssid>' "$w/wt16/wifi/wifi-logcat.txt"; ck "F4: CRLF secret: the SSID is masked" 0 $?
printf '%s\n%s\n' "$WSSID" "Sup3r$(printf '\001')SecretPsk!" > "$w/wsecret-ctl"; ck "F4: a control character in the PSK is REFUSED" 1 "$(GP_WIFI_SECRET=$w/wsecret-ctl gw --out "$w/wt17" wifi-trace-pull)"; grep -q 'control character' "$w/out"; ck "F4: control character reason named" 0 $?
# F4: the PSK in hex is masked too; F11: SSID in hex (plain and colon-separated) is caught by S1
ck "F4: PSK in hex in dmesg is masked, rc 0" 0 "$(FAKE_PSKHEX=1 gw --out "$w/wt18" wifi-trace-pull)"; ck "F4: PSK hex: no hex form left" 0 "$(grep -c "$(printf '%s' "$WPSK" | od -An -v -tx1 | tr -d ' \n')" "$w/wt18/wifi/wifi-dmesg-full.txt")"
ck "F4 S1: PSK hex in a pulled file (mask broken) -> hit, file deleted" 1 "$(FAKE_PSKHEX=1 gwc "$w/wm/gp-nomask" --out "$w/wt19" wifi-trace-pull)"; grep -q 'GAP: S1 PSK hit: wifi/wifi-dmesg-full.txt' "$w/out"; ck "F4 S1: PSK hex hit named" 0 $?
ck "F11: SSID as hex (plain) in dmesg is masked to <ssid>, rc 0" 0 "$(FAKE_HEXLEAK=1 gw --out "$w/wt20" wifi-trace-pull)"; ck "F11: plain hex SSID: no hex form left" 0 "$(grep -c "$(printf '%s' "$WSSID" | od -An -v -tx1 | tr -d ' \n')" "$w/wt20/wifi/wifi-dmesg-full.txt")"
ck "F11: SSID as colon-separated hex is masked whole (not half-eaten by the MAC mask), rc 0" 0 "$(FAKE_HEXLEAK="colon" gw --out "$w/wt21" wifi-trace-pull)"; grep -qx 'wpa: ssid=<ssid> x' "$w/wt21/wifi/wifi-dmesg-full.txt"; ck "F11: colon hex SSID line is exactly 'ssid=<ssid>'" 0 $?
ck "F11 S1: SSID as hex in a pulled file (mask broken in a copy) -> hit, rc 1" 1 "$(FAKE_HEXLEAK=1 gwc "$w/wm/gp-nomask" --out "$w/wt22" wifi-trace-pull)"; grep -q 'GAP: wifi-trace-pull: S1 SSID hex form in wifi/wifi-dmesg-full.txt' "$w/out"; ck "F11 S1: plain hex hit names the file" 0 $?
ck "F11 S1: SSID as colon-separated hex (mask broken) -> hit, rc 1" 1 "$(FAKE_HEXLEAK="colon" gwc "$w/wm/gp-nomask" --out "$w/wt23" wifi-trace-pull)"; grep -q 'S1 SSID hex form in wifi/wifi-dmesg-full.txt' "$w/out"; ck "F11 S1: colon hex hit named" 0 $?
grep -q -F -e "$(printf '%s' "$WSSID" | od -An -v -tx1 | tr -d ' \n')" "$w/out"; ck "F11 S1: the hex is never printed" 1 $?
# ---- review golden-wifi-modes-r2 (R2-1 .. R2-9) -----------------------------------------------------------------------------------------------------------------------------------------
echo "== wifi trace modes: review golden-wifi-modes-r2"
# R2-1: the cfg80211 value was 0 (the value the arm script CHANGES; CFG0=1 hides a lost restore): TERM during the FIRST arm -> back to 0
CFG0=0 mk_trace; ck "R2-1 kprobes: TERM during the first arm (cfg80211 was 0) -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo kprobes_script_end=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'kprobes_script_end=ok' tools/golden-pull --out "$w/wk30" wifi-kprobes)"
ck "R2-1 kprobes: TERM during the first arm: cfg80211/enable is back at the original 0" 0 "$(cat "$FAKE_T/events/cfg80211/enable")"; ck "R2-1 kprobes: TERM during the first arm: no gpwifi probe left" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"
# R2-1 + a recorded value from an earlier arm wins over the device value (mutant K_prevval): file says 1, device says 0 -> 1
CFG0=0 mk_trace; mkdir -p "$w/wk31/wifi"; echo 1 > "$w/wk31/wifi/cfg80211-prev.txt"
ck "R2-1 kprobes: TERM during a re-arm with a recorded prior 1 -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo kprobes_script_end=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'kprobes_script_end=ok' tools/golden-pull --out "$w/wk31" wifi-kprobes)"
ck "R2-1 kprobes: the recorded prior value 1 is restored, not the device's 0" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"
CFG0=0 mk_trace; mkdir -p "$w/wk32/wifi"; echo 1 > "$w/wk32/wifi/cfg80211-prev.txt"; ck "R2-1 kprobes: failing arm with a recorded prior 1 -> rc 1" 1 "$(FAKE_KP_FAIL=cfgevent gw --out "$w/wk32" wifi-kprobes)"; ck "R2-1 kprobes: failing arm: the recorded 1 is restored" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"
# R2-2: cfg80211-prev.txt is validated where it is read; junk is REFUSED before anything is armed, and nothing but keep|0|1 can reach a phone script
CFG0=0 mk_trace; mkdir -p "$w/wk33/wifi"; printf '1; echo PWNED > /dev/kmsg\n' > "$w/wk33/wifi/cfg80211-prev.txt"
ck "R2-2 kprobes: junk in cfg80211-prev.txt -> REFUSED rc 1" 1 "$(gw --out "$w/wk33" wifi-kprobes)"; grep -q 'cfg80211-prev.txt does not hold 0 or 1' "$w/out"; ck "R2-2 kprobes: the refusal names the file" 0 $?
ck "R2-2 kprobes: junk: no kprobe script was sent" 0 "$(grep -c 'kprobe_events' "$WIN")"; ck "R2-2 kprobes: junk: PWNED reached the phone side nowhere" 0 "$(cat "$WLOG" "$WIN" | grep -c PWNED)"
sed 's/wf_kp_off kprobes-disarm.txt "\$wf_kp_prev" || true/wf_kp_off kprobes-disarm.txt "1; echo PWNED >> \/dev\/kmsg" || true/' tools/golden-pull > "$w/wm/gp-junkprev"; chmod +x "$w/wm/gp-junkprev"; cmp -s tools/golden-pull "$w/wm/gp-junkprev"; ck "R2-2 test copy: the junk injection changed the tool" 1 $?
CFG0=0 mk_trace; mkdir -p "$w/wk34/wifi"; echo 1 > "$w/wk34/wifi/cfg80211-prev.txt"
ck "R2-2 kpoff script: a copy that hands junk to the disarm -> rc 1" 1 "$(FAKE_KP_FAIL=cfgevent gwc "$w/wm/gp-junkprev" --out "$w/wk34" wifi-kprobes)"; ck "R2-2 kpoff script: the junk never reached the phone" 0 "$(cat "$WLOG" "$WIN" | grep -c PWNED)"
grep -q 'is not keep|0|1' "$w/out"; ck "R2-2 kpoff script: the refusal is loud" 0 $?; grep -q 'kprobes off/disarm refused' "$w/out"; ck "R2-2 kpoff script: it is a GAP" 0 $?
# R2-3: --out DIR/ (and DIR//) must still count a PSK file the run wrote as its own and delete it
ck "R2-3 S1: --out with a trailing slash, mask broken -> hit rc 1" 1 "$(FAKE_PSKLEAK=1 gwc "$w/wm/gp-nomask" --out "$w/wt24/" wifi-trace-pull)"; grep -q 'GAP: S1 PSK hit: wifi/wifi-logcat.txt (file written by this run: deleted' "$w/out"; ck "R2-3 S1: trailing slash: reported as written by this run" 0 $?
ck "R2-3 S1: trailing slash: the PSK file is gone" 0 "$(grep -rlaF "$WPSK" "$w/wt24" | wc -l)"
ck "R2-3 S1: --out with two trailing slashes, mask broken -> hit rc 1" 1 "$(FAKE_PSKLEAK=1 gwc "$w/wm/gp-nomask" --out "$w/wt25//" wifi-trace-pull)"; ck "R2-3 S1: two slashes: the PSK file is gone" 0 "$(grep -rlaF "$WPSK" "$w/wt25" | wc -l)"
# S1 never touches the secret file itself, also when it is reachable as a second hard link inside the out dir (mutant S1_noSecretSkip)
mkdir -p "$w/wt26"; ln "$GP_WIFI_SECRET" "$w/wt26/hardlink-of-secret"; ck "R2-9 S1: a hard link of the secret file inside the out dir is skipped, rc 0" 0 "$(gw --out "$w/wt26" wifi-trace-pull)"; ck "R2-9 S1: the hard link still exists" 1 "$(absent "$w/wt26/hardlink-of-secret")"
# R2-4: bind, unbind, bind, unbind in ONE out dir: the stock-sha record is reset by each bind (mutant B_notrunc)
mk_phone; gwc "$w/wm/golden-pull" --out "$w/wb40" wifi-fw-bind "$w/wfw" >/dev/null; ck "R2-4 unbind 1 (same out dir) rc 0" 0 "$(gw --out "$w/wb40" wifi-fw-unbind)"
ck "R2-4 bind 2 (same out dir) rc 0" 0 "$(gwc "$w/wm/golden-pull" --out "$w/wb40" wifi-fw-bind "$w/wfw")"; ck "R2-4 the stock-sha record has 3 lines, not 6" 3 "$(wc -l < "$w/wb40/wifi/fw-stock-sha.txt")"
ck "R2-4 unbind 2 (same out dir) rc 0, no false 'after unbind' GAP" 0 "$(gw --out "$w/wb40" wifi-fw-unbind)"
# R2-5: a disarm that itself fails keeps the flag, so the exit trap sends a second disarm and the probes are gone at the end
rm -f "$WSTATE/offfail"; CFG0=1 mk_trace; ck "R2-5 kprobes: failing arm + a failing first disarm -> rc 1" 1 "$(FAKE_KP_FAIL=cfgevent FAKE_KP_OFFFAIL_ONCE=1 gw --out "$w/wk35" wifi-kprobes)"
ck "R2-5 kprobes: two disarm scripts were sent (normal path + exit trap)" 2 "$(grep -c 'echo kprobes_off_end=ok' "$WIN")"; grep -q 'wf cleanup: .*armed kprobes=1' "$w/out"; ck "R2-5 kprobes: the exit trap announced the retry" 0 $?
ck "R2-5 kprobes: after the retry no gpwifi probe is left" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"; ck "R2-5 kprobes: after the retry cfg80211/enable is back at 1" 1 "$(cat "$FAKE_T/events/cfg80211/enable")"; rm -f "$WSTATE/offfail"
# R2-6: a signal while a SECOND bind runs must not unbind the first (good) bind
mk_phone; gwc "$w/wm/golden-pull" --out "$w/wb41" wifi-fw-bind "$w/wfw" >/dev/null; ck "R2-6 bind: first bind in the model (4 mounts)" 4 "$(wc -l < "$PH/proc/mounts")"
ck "R2-6 bind: TERM during a 2nd bind script -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo bind_script_end=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'bind_script_end=ok' "$w/wm/golden-pull" --out "$w/wb41" wifi-fw-bind "$w/wfw")"
ck "R2-6 bind: the first bind is still active" 4 "$(wc -l < "$PH/proc/mounts")"; ck "R2-6 bind: no unbind script was sent" 0 "$(grep -c 'echo unbind_script_end=ok' "$WIN")"; grep -q 'left as it was' "$w/out"; ck "R2-6 bind: the cleanup says it left the earlier bind" 0 $?
ck "R2-6 bind: TERM during the stage-clean after bind_already -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo stage_clean=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'stage_clean=ok' "$w/wm/golden-pull" --out "$w/wb41" wifi-fw-bind "$w/wfw")"
ck "R2-6 bind: stage-clean TERM: the first bind is still active" 4 "$(wc -l < "$PH/proc/mounts")"; ck "R2-6 bind: stage-clean TERM: no unbind script sent" 0 "$(grep -c 'echo unbind_script_end=ok' "$WIN")"
ck "R2-6 bind: no staged copy left" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"
# R2-7: SIGINT. A background job of a non-interactive shell starts with SIGINT ignored (not trappable); the launcher restores the default disposition and execs the tool
cat > "$w/dflint.py" <<'PYEOF'
import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])
PYEOF
CFG0=0 mk_trace; ck "R2-7 kprobes: INT during the arm script -> rc 1" 1 "$(FAKE_SU_SLEEP_ON='echo kprobes_script_end=ok' FAKE_SU_SLEEP=2 sigtest INT "$WIN" 'kprobes_script_end=ok' python3 -I "$w/dflint.py" tools/golden-pull --out "$w/wk36" wifi-kprobes)"
grep -q '^RESULT: FAIL wifi-kprobes interrupted by SIGINT' "$w/out"; ck "R2-7 kprobes: INT: RESULT FAIL names the signal" 0 $?; ck "R2-7 kprobes: INT: no gpwifi probe left" 0 "$(grep -c '^[pr]:gpwifi/' "$FAKE_T/kprobe_events")"; ck "R2-7 kprobes: INT: cfg80211/enable restored to 0" 0 "$(cat "$FAKE_T/events/cfg80211/enable")"
mk_phone; ck "R2-7 bind: INT during the 2nd push -> rc 1" 1 "$(FAKE_PUSH_SLEEP=2 sigtest INT "$WLOG" 'gp-wfw-bcmdhd.cal' python3 -I "$w/dflint.py" "$w/wm/golden-pull" --out "$w/wb42" wifi-fw-bind "$w/wfw")"; ck "R2-7 bind: INT leaves no staged copy" 0 "$(find "$PH/data/local/tmp" -type f | wc -l)"
# TERM during a masked mode removes the private temp dir
mkdir -p "$w/tmpd2"; TMPDIR=$w/tmpd2 FAKE_SU_SLEEP_ON='scan_list=ok' FAKE_SU_SLEEP=2 sigtest TERM "$WIN" 'scan_list=ok' tools/golden-pull --out "$w/wsc9" wifi-scan >/dev/null; ck "F1: TERM during wifi-scan: the secret temp dir is removed" 0 "$(ls -A "$w/tmpd2" | wc -l)"
never_ok; ck "review r1 tests: never-write list absent from everything sent" 0 $?
# r2 R2-8: a run that skipped checks is not a PASS (AP-102): RESULT: PARTIAL + exit 2 unless GOLDEN_TEST_ALLOW_SKIP=1 (then PASS with the skips= count)
sk=""; [ "$skips" = 0 ] || sk=" skips=$skips"
if [ "$fails" != 0 ]; then echo "RESULT: FAIL test_golden_capture failures=$fails$sk"; exit 1; fi
if [ "$skips" != 0 ] && [ "${GOLDEN_TEST_ALLOW_SKIP-}" != 1 ]; then echo "RESULT: PARTIAL test_golden_capture$sk (all executed checks ok; $skips group(s) of checks NOT run, see the SKIP lines; GOLDEN_TEST_ALLOW_SKIP=1 accepts that)"; exit 2; fi
echo "RESULT: PASS test_golden_capture$sk"; exit 0
