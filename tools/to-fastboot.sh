#!/bin/bash
# Get the Pixel back into fastboot without a button press after a test reset.
# A software reset (shim REBOOT_BL / XNU sweetrestart=) lands in recovery because Android no longer boots from the iOS userdata;
# adb works there, so: wait for fastboot, or for adb (recovery/device), verify bluejay, `adb reboot bootloader`.
#   tools/to-fastboot.sh [timeout_s=240]
# T-7dff:help: -h/--help prints this header and exits 0 before anything runs
case "${1:-}" in -h|--help) printf 'usage: %s\n' "${0##*/}"; sed -n '2,/^[^#]/{/^#/s/^# \{0,1\}//p}' "$0"; exit 0;; esac
here=$(dirname "$(readlink -f "$0")")
exec "$here/phone" goto fastboot --timeout "${1:-240}"
