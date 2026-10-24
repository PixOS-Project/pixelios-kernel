#!/usr/bin/env python3
"""Unit tests for tools/phone state machine without real hardware.

Simulates device behaviors using mock adb, fastboot, lsusb, and ping
binaries in a temporary directory prepended to PATH.
"""

from importlib.machinery import SourceFileLoader
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHONE_TOOL = os.path.join(REPO_ROOT, "tools", "phone")

# Import tools/phone as a module
phone = SourceFileLoader("phone", PHONE_TOOL).load_module()


class PhoneStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="phone_sm_test_")
        self.bin_dir = os.path.join(self.test_dir, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)

        self.state_file = os.path.join(self.test_dir, "state.json")
        self.adb_log = os.path.join(self.test_dir, "adb.log")
        self.fastboot_log = os.path.join(self.test_dir, "fastboot.log")

        self.set_state({"state": "fastboot", "product": "bluejay"})
        self._create_mock_binaries()

        self.orig_path = os.environ.get("PATH", "")

        # Use mock bin directory first on PATH
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.orig_path}"
        self.env["PHONE_STATE_TEST_DIR"] = self.test_dir
        # TP-020: goto needs the phone lock; the tests hold their own, in a temp dir
        self.env["PHONE_LOCK_DIR"] = os.path.join(self.test_dir, "phone.lock")
        self.env["PHONE_LOCK_TOKEN"] = "sm-test-token"
        self.lock_tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-lock")
        subprocess.run([self.lock_tool, "acquire", "--owner", "sm-test", "--token", "sm-test-token"], env=self.env, capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def set_state(self, state_dict):
        with open(self.state_file, "w") as f:
            json.dump(state_dict, f)

    def get_state(self):
        with open(self.state_file, "r") as f:
            return json.load(f)

    def _create_mock_binaries(self):
        # mock ping
        ping_src = f"""#!/bin/bash
state_file="{self.state_file}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
if [ "$st" = "ios" ]; then
    echo "PING 192.168.7.2 (192.168.7.2) 56(84) bytes of data."
    echo "64 bytes from 192.168.7.2: icmp_seq=1 ttl=64 time=1.0 ms"
    exit 0
else
    exit 1
fi
"""
        self._write_executable("ping", ping_src)

        # mock lsusb
        lsusb_src = f"""#!/bin/bash
state_file="{self.state_file}"
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
"""
        self._write_executable("lsusb", lsusb_src)

        # mock ip
        ip_src = f"""#!/bin/bash
state_file="{self.state_file}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
if [ "$st" = "ios" ]; then
    echo "2: usb0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 link/ether 02:00:5e:77:ee:01 brd ff:ff:ff:ff:ff:ff"
else
    echo "1: lo: <LOOPBACK,UP,LOWER_UP> link/loopback 00:00:00:00:00:00"
fi
"""
        self._write_executable("ip", ip_src)

        # mock adb
        adb_src = f"""#!/bin/bash
state_file="{self.state_file}"
echo "$@" >> "{self.adb_log}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
prod=$(grep -o '"product": "[^"]*"' "$state_file" | cut -d'"' -f4)
[ -n "$prod" ] || prod="bluejay"

case "$*" in
    "devices")
        echo "List of devices attached"
        case "$st" in
            recovery) echo -e "SECRET_SERIAL123\\trecovery" ;;
            android|booting|other-device) echo -e "SECRET_SERIAL123\\tdevice" ;;
        esac
        ;;
    "get-state")
        case "$st" in
            recovery) echo "recovery" ;;
            android|booting|other-device) echo "device" ;;
            *) exit 1 ;;
        esac
        ;;
    *"getprop ro.product.device"*)
        echo "$prod"
        ;;
    *"getprop sys.boot_completed"*)
        if [ "$st" = "android" ]; then echo "1"; else echo "0"; fi
        ;;
    *"reboot bootloader"*)
        # simulate transition
        python3 -c "import json; p='{self.state_file}'; d=json.load(open(p)); d['state']='fastboot'; json.dump(d, open(p, 'w'))"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""
        self._write_executable("adb", adb_src)

        # mock fastboot
        fastboot_src = f"""#!/bin/bash
state_file="{self.state_file}"
echo "$@" >> "{self.fastboot_log}"
st=$(grep -o '"state": "[^"]*"' "$state_file" | cut -d'"' -f4)
prod=$(grep -o '"product": "[^"]*"' "$state_file" | cut -d'"' -f4)
[ -n "$prod" ] || prod="bluejay"

case "$*" in
    "devices")
        if [ "$st" = "fastboot" ]; then
            echo -e "SECRET_SERIAL123\\tfastboot"
        fi
        ;;
    *"getvar product"*)
        echo "product: $prod" >&2
        ;;
    "reboot")
        python3 -c "import json; p='{self.state_file}'; d=json.load(open(p)); d['state']='android'; json.dump(d, open(p, 'w'))"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""
        self._write_executable("fastboot", fastboot_src)

    def _write_executable(self, name, content):
        path = os.path.join(self.bin_dir, name)
        with open(path, "w") as f:
            f.write(content)
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def run_phone(self, *args, timeout=30):
        cmd = [PHONE_TOOL] + list(args)
        res = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=timeout)
        return res

    # 1. android -> fastboot
    def test_01_android_to_fastboot(self):
        self.set_state({"state": "android", "product": "bluejay"})
        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot steps=1", res.stdout)
        self.assertNotIn("SECRET_SERIAL123", res.stdout)

    # 2. recovery -> fastboot
    def test_02_recovery_to_fastboot(self):
        self.set_state({"state": "recovery", "product": "bluejay"})
        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot steps=1", res.stdout)
        self.assertNotIn("SECRET_SERIAL123", res.stdout)

    # 3. absent then appears as recovery -> fastboot
    def test_03_absent_then_recovery_to_fastboot(self):
        self.set_state({"state": "absent", "product": "bluejay"})

        # Background thread to change state to recovery after 1 second
        import threading
        def delayed_appear():
            time.sleep(1.0)
            self.set_state({"state": "recovery", "product": "bluejay"})
        th = threading.Thread(target=delayed_appear)
        th.start()

        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        th.join()
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)

    # 4. ios -> fastboot via self-reset (state changes after N s)
    def test_04_ios_to_fastboot_self_reset(self):
        self.set_state({"state": "ios", "product": "bluejay"})
        self.env["PHONE_BOUND_SCALE"] = "0.1"	# the ios wait bound is 90 s: 9 s here, the fake resets after 1 s

        import threading
        def delayed_reset():
            time.sleep(1.0)
            self.set_state({"state": "recovery", "product": "bluejay"})
        th = threading.Thread(target=delayed_reset)
        th.start()

        res = self.run_phone("goto", "fastboot", "--timeout", "15")
        th.join()
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)
        self.assertIn("USBNET: sudo sh -c", res.stdout)
        self.assertNotIn("NEED USER", res.stdout)	# the phone reset by itself inside the bound: the user is never asked

    # 5. hung -> NEED USER line then success after fake state changes
    def test_05_hung_need_user_and_recover(self):
        self.set_state({"state": "hung", "product": "bluejay"})
        self.env["PHONE_BOUND_SCALE"] = "0.1"	# the hung bound is 10 s: 1 s here; the fake recovers after 3.5 s, so the prompt must have appeared

        import threading
        def delayed_user_action():
            time.sleep(3.5)
            self.set_state({"state": "fastboot", "product": "bluejay"})
        th = threading.Thread(target=delayed_user_action)
        th.start()

        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        th.join()
        self.assertEqual(res.returncode, 0)
        self.assertIn("NEED USER: hold Power+VolDown", res.stdout)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)

    # 6. other-device refusal with ZERO adb/fastboot invocations afterwards
    def test_06_other_device_refusal_zero_commands(self):
        self.set_state({"state": "other-device", "product": "sweet"})
        if os.path.exists(self.adb_log):
            os.remove(self.adb_log)
        if os.path.exists(self.fastboot_log):
            os.remove(self.fastboot_log)

        res = self.run_phone("goto", "fastboot", "--timeout", "5")
        self.assertEqual(res.returncode, 1)
        self.assertIn("RESULT: FAIL state=other-device target=fastboot steps=0", res.stdout)

        # Check no reboot / mutation commands were sent
        if os.path.exists(self.adb_log):
            with open(self.adb_log) as f:
                content = f.read()
                self.assertNotIn("reboot", content)
        if os.path.exists(self.fastboot_log):
            with open(self.fastboot_log) as f:
                content = f.read()
                self.assertNotIn("reboot", content)
                self.assertNotIn("flash", content)

    # 7. fastboot getvar never called when usb id is not fastboot
    def test_07_fastboot_getvar_never_called_without_fastboot_usb(self):
        # Set state to android (USB ID 18d1:4ee2, not d001/4ee0)
        self.set_state({"state": "android", "product": "bluejay"})
        if os.path.exists(self.fastboot_log):
            os.remove(self.fastboot_log)

        # Calling verify_bluejay_target("fastboot") under this environment
        # Must refuse and never invoke fastboot getvar product
        env_orig = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = f"{self.bin_dir}:{env_orig}"
            logger = phone.Logger()
            is_bluejay = phone.verify_bluejay_target("fastboot", logger=logger)
            self.assertFalse(is_bluejay)
        finally:
            os.environ["PATH"] = env_orig

        if os.path.exists(self.fastboot_log):
            with open(self.fastboot_log) as f:
                self.assertNotIn("getvar product", f.read())

    # 8. timeout => FAIL with the last state
    def test_08_timeout_fails_with_last_state(self):
        self.set_state({"state": "hung", "product": "bluejay"})
        res = self.run_phone("goto", "fastboot", "--timeout", "1")
        self.assertEqual(res.returncode, 1)
        self.assertIn("RESULT: FAIL state=hung target=fastboot", res.stdout)

    # 9. allowlist assertion
    def test_09_allowlist_assertion(self):
        with self.assertRaises(AssertionError):
            phone.assert_command_allowed(["fastboot", "flash", "boot", "boot.img"])
        with self.assertRaises(AssertionError):
            phone.assert_command_allowed(["fastboot", "erase", "userdata"])
        with self.assertRaises(AssertionError):
            phone.assert_command_allowed(["adb", "push", "file", "/data/"])

    # 10. dry-run runs nothing
    def test_10_dry_run_runs_nothing(self):
        self.set_state({"state": "recovery", "product": "bluejay"})
        if os.path.exists(self.adb_log):
            os.remove(self.adb_log)

        res = self.run_phone("goto", "fastboot", "--dry-run")
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=recovery target=fastboot steps=0", res.stdout)
        self.assertIn("dry-run: no commands executed", res.stdout)
        # Verify no adb command was called
        if os.path.exists(self.adb_log):
            with open(self.adb_log) as f:
                self.assertNotIn("reboot bootloader", f.read())

    # 11. TP-020: no lock, no command
    def test_11_goto_needs_the_lock(self):
        self.set_state({"state": "recovery", "product": "bluejay"})
        subprocess.run([self.lock_tool, "release", "--token", "sm-test-token"], env=self.env, capture_output=True, check=True)
        res = self.run_phone("goto", "fastboot", "--timeout", "5")
        self.assertEqual(res.returncode, 2)
        self.assertIn("REFUSED: the phone lock is not held", res.stdout)
        if os.path.exists(self.adb_log):
            with open(self.adb_log) as f:
                self.assertNotIn("reboot bootloader", f.read())


if __name__ == "__main__":
    unittest.main()
