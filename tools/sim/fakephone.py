#!/usr/bin/env python3
"""FakePhone: the fake adb/fastboot/lsusb/ping/ip/reset-bl of tools/test_phone_sm.py, generalised (SIM-1, PLAN-SIM D3).

stdlib only.  The fakes act only on their own state file and counters (AP-192/248/290):
they never derive an answer from script or argv text beyond the command verb.
A write command (fastboot flash/erase/boot/reboot..., adb reboot) is always LOGGED; on
product != bluejay only an allowlist of reads is answered; every other verb is refused (exit 1) so a test can see it.
fastboot getvar battery-voltage / storage-temperature (bluejay only; tools/safety-check fastboot) answer the optional state keys
battery_mv (default 3900) and storage_c (default 30), on stderr like getvar product.
"""

import json
import os
import socket
import stat

FAKE_SERIAL = "FAKE0000SIM"   # obviously fake, never a real serial (rule 4)

# state -> (idVendor, idProduct) as in the lsusb fake below; absent/unknown: no device
USB_IDS = {
    "fastboot": ("18d1", "4ee0"),
    "recovery": ("18d1", "d001"),
    "android": ("18d1", "4ee2"),
    "booting": ("18d1", "4ee2"),
    "hung": ("18d1", "4ee2"),
    "other-device": ("18d1", "4ee2"),
    "ios": ("1209", "0001"),
    "shim-probe": ("1209", "0001"),
}
# fastboot getvar answers (tools/safety-check fastboot); shell snippets spliced into the fastboot shim of write_shims (kept out of it: lint-size)
FB_INIT = ("bmv=$(grep -o '\"battery_mv\": *[0-9]*' \"$state_file\" | grep -o '[0-9]*$'); [ -n \"$bmv\" ] || bmv=3900; "
           "sc=$(grep -o '\"storage_c\": *[0-9]*' \"$state_file\" | grep -o '[0-9]*$'); [ -n \"$sc\" ] || sc=30")
FB_GETVAR = ('case "$key" in "getvar battery-voltage") echo "battery-voltage: $bmv" >&2 ;; '
             '"getvar storage-temperature") echo "storage-temperature: $sc" >&2 ;; *) echo "product: $prod" >&2 ;; esac')
STATES = ("absent", "fastboot", "android", "recovery", "ios")


class FakePhone:
    def __init__(self, root_dir, serial=FAKE_SERIAL):
        self.root_dir = root_dir
        self.serial = serial
        self.state_file = os.path.join(root_dir, "state.json")
        self.flip_file = os.path.join(root_dir, "flip")
        self.adb_log = os.path.join(root_dir, "adb.log")
        self.fastboot_log = os.path.join(root_dir, "fastboot.log")
        self.reset_log = os.path.join(root_dir, "reset-bl.log")
        self.bin_dir = None
        self.set_state({"state": "fastboot", "product": "bluejay"})

    def set_state(self, state_dict):
        with open(self.state_file, "w") as f:
            json.dump(state_dict, f)

    def get_state(self):
        with open(self.state_file, "r") as f:
            return json.load(f)

    def flip_after_probes(self, n, state_dict):
        """The fake phone changes to state_dict right after its n-th observe_state probe (the n-th ping): a state change tied to what the tool did, not to the clock."""
        with open(self.flip_file, "w") as f:
            f.write("%d\n%s\n" % (n, json.dumps(state_dict)))

    def flip_on_reset(self, state_dict):
        """The fake phone changes to state_dict when tools/phone calls the reset script (event, not clock)."""
        with open(self.flip_file + ".reset", "w") as f:
            json.dump(state_dict, f)

    # ---- D3 helpers ----
    def fake_sysfs(self, sysfs_dir):
        """Write <sysfs_dir>/<n>/idVendor and idProduct for the current state.
        Matches what the tools read: tools/xrun:141,142,214,216 (`grep -qsx 18d1 /sys/bus/usb/devices/*/idVendor`,
        a whole-line match, so the file holds exactly the id plus newline) and tools/xrun_report.py:221-228
        (usb_present: glob */idVendor, read_text().strip() == vid).  Absent: the dir exists and is empty."""
        root = os.path.realpath(self.root_dir)
        real = os.path.realpath(sysfs_dir)
        if real == root or not real.startswith(root + os.sep):
            raise ValueError("fake_sysfs refuses %r: it clears its dir, so it must be inside %r" % (sysfs_dir, self.root_dir))
        os.makedirs(sysfs_dir, exist_ok=True)
        for name in os.listdir(sysfs_dir):
            d = os.path.join(sysfs_dir, name)
            if os.path.isdir(d):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))
                os.rmdir(d)
        ids = USB_IDS.get(self.get_state().get("state"))
        if ids:
            d = os.path.join(sysfs_dir, "1-1")
            os.makedirs(d)
            with open(os.path.join(d, "idVendor"), "w") as f:
                f.write(ids[0] + "\n")
            with open(os.path.join(d, "idProduct"), "w") as f:
                f.write(ids[1] + "\n")
        return sysfs_dir

    # ---- shims ----
    def _write_executable(self, name, content):
        path = os.path.join(self.bin_dir, name)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def write_shims(self, bin_dir):
        """Write adb, fastboot, lsusb, ping, ip, fake-sweet-reset-bl into bin_dir. Returns bin_dir."""
        os.makedirs(bin_dir, exist_ok=True)
        self.bin_dir = bin_dir
        sf, ser = self.state_file, self.serial

        # every observe_state starts with one ping: the probe counter of flip_after_probes (event-driven state changes, no wall-clock sleeps: a shard under nice 10 on a loaded host was starved past them)
        self._write_executable("ping", f"""#!/bin/bash
state_file="{sf}"
flip="{self.flip_file}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
if [ -f "$flip" ]; then
    n=$(cat "$flip.n" 2>/dev/null || echo 0); n=$((n+1)); echo $n >"$flip.n"
    if [ "$n" -ge "$(head -1 "$flip")" ]; then tail -n +2 "$flip" >"$state_file.new" && mv "$state_file.new" "$state_file"; rm -f "$flip" "$flip.n"; fi
fi
if [ "$st" = "ios" ]; then
    echo "PING 192.168.7.2 (192.168.7.2) 56(84) bytes of data."
    echo "64 bytes from 192.168.7.2: icmp_seq=1 ttl=64 time=1.0 ms"
    exit 0
else
    exit 1
fi
""")

        self._write_executable("lsusb", f"""#!/bin/bash
state_file="{sf}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
case "$st" in
    fastboot)
        echo "Bus 001 Device 005: ID 18d1:4ee0 Google Inc. Nexus/Pixel (fastboot)" ;;
    recovery)
        echo "Bus 001 Device 005: ID 18d1:d001 Google Inc. Nexus/Pixel (recovery)" ;;
    android|booting)
        echo "Bus 001 Device 005: ID 18d1:4ee2 Google Inc. Nexus/Pixel (debug)" ;;
    ios)
        echo "Bus 001 Device 005: ID 1209:0001 Generic USB Ethernet" ;;
    shim-probe)
        echo "Bus 001 Device 005: ID 1209:0001 Generic CDC-ACM" ;;
    hung)
        echo "Bus 001 Device 005: ID 18d1:4ee2 Google Inc. Nexus/Pixel" ;;
    other-device)
        echo "Bus 001 Device 005: ID 18d1:4ee2 Google Inc. Nexus/Pixel" ;;
    absent)
        exit 0 ;;
    *)
        exit 0 ;;
esac
""")

        self._write_executable("ip", f"""#!/bin/bash
state_file="{sf}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
if [ "$st" = "ios" ]; then
    echo "2: usb0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 link/ether 02:00:5e:77:ee:01 brd ff:ff:ff:ff:ff:ff"
else
    echo "1: lo: <LOOPBACK,UP,LOWER_UP> link/loopback 00:00:00:00:00:00"
fi
""")

        # adb: verb = first arg after an optional "-s SERIAL"; only the verb and the state file decide the answer.
        self._write_executable("adb", f"""#!/bin/bash
state_file="{sf}"
echo "$@" >> "{self.adb_log}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
prod=$(grep -o '"product": "[^"]*"' "$state_file" | cut -d'"' -f4)
[ -n "$prod" ] || prod="bluejay"
a=("$@"); xopt=0
while [ "${{#a[@]}}" -gt 0 ]; do
    case "${{a[0]}}" in
        -s) a=("${{a[@]:2}}") ;;
        -t|-H|-P|-L|-S|-i|--slot|--set-active) xopt=1; a=("${{a[@]:2}}") ;;
        -*) xopt=1; a=("${{a[@]:1}}") ;;
        *) break ;;
    esac
done
verb="${{a[0]}}"; key="${{a[*]}}"
refuse() {{ echo "adb: error: FAKE refused verb '$verb' on product '$prod' (not bluejay, not an allowlisted read)" >&2; exit 1; }}
if [ "$prod" != "bluejay" ]; then
    # read allowlist (tools/phone:65-71 ALLOWED_COMMANDS; tools/phone:546 adb devices; tools/phone-state:21 the one-arg getprop pair): anything else is logged above, then refused
    case "$key" in
        "devices"|"get-state"|"shell getprop ro.product.device"|"shell getprop sys.boot_completed"|"shell getprop ro.product.device; getprop sys.boot_completed") [ "$xopt" = 0 ] || refuse ;;
        *) refuse ;;
    esac
fi

case "$key" in
    "devices")
        echo "List of devices attached"
        case "$st" in
            recovery) echo -e "{ser}\\trecovery" ;;
            android|booting|other-device) echo -e "{ser}\\tdevice" ;;
        esac
        ;;
    "get-state")
        case "$st" in
            recovery) echo "recovery" ;;
            android|booting|other-device) echo "device" ;;
            *) exit 1 ;;
        esac
        ;;
    "shell getprop ro.product.device"|"shell getprop ro.product.device; getprop sys.boot_completed")
        echo "$prod"
        ;;
    "shell getprop sys.boot_completed")
        if [ "$st" = "android" ]; then echo "1"; else echo "0"; fi
        ;;
    "reboot bootloader")
        python3 -c "import json; p='{sf}'; d=json.load(open(p)); d['state']='fastboot'; json.dump(d, open(p, 'w'))"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
""")

        self._write_executable("fastboot", f"""#!/bin/bash
state_file="{sf}"
echo "$@" >> "{self.fastboot_log}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
prod=$(grep -o '"product": "[^"]*"' "$state_file" | cut -d'"' -f4)
[ -n "$prod" ] || prod="bluejay"; {FB_INIT}
a=("$@"); xopt=0
while [ "${{#a[@]}}" -gt 0 ]; do
    case "${{a[0]}}" in
        -s) a=("${{a[@]:2}}") ;;
        -t|-H|-P|-L|-S|-i|--slot|--set-active) xopt=1; a=("${{a[@]:2}}") ;;
        -*) xopt=1; a=("${{a[@]:1}}") ;;
        *) break ;;
    esac
done
verb="${{a[0]}}"; key="${{a[*]}}"; ok=0
if [ "$prod" != "bluejay" ]; then
    # read allowlist (tools/phone:70-71 ALLOWED_COMMANDS; tools/phone:551 fastboot devices): anything else is logged above, then refused
    case "$key" in
        "devices"|"getvar product") [ "$xopt" = 0 ] && ok=1 ;;
    esac
    if [ "$ok" != 1 ]; then
        echo "FAILED (remote: FAKE refused verb '$verb' on product '$prod', not bluejay, not an allowlisted read)" >&2
        exit 1
    fi
fi

case "$key" in
    "devices")
        if [ "$st" = "fastboot" ]; then
            echo -e "{ser}\\tfastboot"
        fi
        ;;
    "getvar product"|"getvar battery-voltage"|"getvar storage-temperature")
        {FB_GETVAR}
        ;;
    "reboot")
        python3 -c "import json; p='{sf}'; d=json.load(open(p)); d['state']='android'; json.dump(d, open(p, 'w'))"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
""")

        # fake sweet-reset-bl (AP-55): the real one sends UDP SWEET-RESET-BL to 192.168.7.2:5141 and resets a live phone run
        self._write_executable("fake-sweet-reset-bl", f"#!/bin/bash\necho called >> \"{self.reset_log}\"\n[ -f \"{self.flip_file}.reset\" ] && mv \"{self.flip_file}.reset\" \"{sf}\"\nexit 0\n")   # flip_on_reset: the phone resets itself once the tool asked for it
        return bin_dir


def send_console(lines, host="127.0.0.1", port=5140):
    """Send each line as one UDP datagram (the console channel of PLAN-SIM D3). The caller passes the port; tests use an ephemeral one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for line in lines:
            s.sendto(line.encode() if isinstance(line, str) else bytes(line), (host, port))
    finally:
        s.close()
