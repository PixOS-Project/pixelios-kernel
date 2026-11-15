#!/usr/bin/env python3
"""Tests for tools/sim/fakephone.py (SIM-1). Fake bin dir first on PATH; nothing touches real USB or a real phone."""

import importlib.util
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("fakephone", os.path.join(HERE, "sim", "fakephone.py"))
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

SER = fp.FAKE_SERIAL
STATES = ("absent", "fastboot", "android", "recovery", "ios")
LSUSB = {
    "absent": "",
    "fastboot": "Bus 001 Device 005: ID 18d1:4ee0 Google Inc. Nexus/Pixel (fastboot)\n",
    "recovery": "Bus 001 Device 005: ID 18d1:d001 Google Inc. Nexus/Pixel (recovery)\n",
    "android": "Bus 001 Device 005: ID 18d1:4ee2 Google Inc. Nexus/Pixel (debug)\n",
    "ios": "Bus 001 Device 005: ID 1209:0001 Generic USB Ethernet\n",
}
ADB_DEVICES = {
    "recovery": "List of devices attached\n%s\trecovery\n" % SER,
    "android": "List of devices attached\n%s\tdevice\n" % SER,
}


class FakePhoneTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fakephone_test_")
        self.fake = fp.FakePhone(self.dir)
        self.bin = self.fake.write_shims(os.path.join(self.dir, "bin"))
        self.env = dict(os.environ)
        self.env["PATH"] = self.bin + ":" + self.env.get("PATH", "")   # fake bin first (AP-55)
        self.env["PHONE_RESET_BL_BIN"] = os.path.join(self.bin, "fake-sweet-reset-bl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cmd(self, *argv):
        r = subprocess.run(argv, env=self.env, capture_output=True, text=True, timeout=20)
        return r.returncode, r.stdout, r.stderr

    def test_01_outputs_per_state_and_product(self):
        for prod in ("bluejay", "other"):
            for st in STATES:
                with self.subTest(state=st, product=prod):
                    self.fake.set_state({"state": st, "product": prod})
                    rc, out, _ = self.run_cmd("fastboot", "devices")
                    self.assertEqual(out, ("%s\tfastboot\n" % SER) if st == "fastboot" else "")
                    rc, out, _ = self.run_cmd("adb", "devices")
                    self.assertEqual(out, ADB_DEVICES.get(st, "List of devices attached\n"))
                    rc, out, _ = self.run_cmd("adb", "shell", "getprop", "ro.product.device")
                    self.assertEqual(out, prod + "\n")
                    rc, out, err = self.run_cmd("fastboot", "getvar", "product")
                    self.assertEqual(err, "product: %s\n" % prod)
                    rc, out, _ = self.run_cmd("lsusb")
                    self.assertEqual(out, LSUSB[st])
                    rc, out, _ = self.run_cmd("ping", "-c1", "192.168.7.2")
                    if st == "ios":
                        self.assertEqual(rc, 0)
                        self.assertIn("64 bytes from 192.168.7.2", out)
                    else:
                        self.assertEqual((rc, out), (1, ""))

    def test_02_flip_after_probes(self):
        self.fake.set_state({"state": "absent", "product": "bluejay"})
        self.fake.flip_after_probes(3, {"state": "recovery", "product": "bluejay"})
        for _ in range(2):
            self.run_cmd("ping", "-c1", "192.168.7.2")
            self.assertEqual(self.fake.get_state()["state"], "absent")
        self.run_cmd("ping", "-c1", "192.168.7.2")
        self.assertEqual(self.fake.get_state()["state"], "recovery")

    def test_03_flip_on_reset(self):
        self.fake.set_state({"state": "ios", "product": "bluejay"})
        self.fake.flip_on_reset({"state": "absent", "product": "bluejay"})
        self.assertEqual(self.fake.get_state()["state"], "ios")
        rc, _, _ = self.run_cmd(self.env["PHONE_RESET_BL_BIN"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.get_state()["state"], "absent")
        with open(self.fake.reset_log) as f:
            self.assertEqual(f.read(), "called\n")

    def test_04_write_on_other_product_logged_and_fails(self):
        self.fake.set_state({"state": "fastboot", "product": "sweet"})
        for argv in (("fastboot", "flash", "userdata", "x.img"), ("fastboot", "erase", "userdata"),
                     ("fastboot", "boot", "x.img"), ("fastboot", "reboot")):
            rc, _, err = self.run_cmd(*argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("refused", err)
        with open(self.fake.fastboot_log) as f:
            log = f.read()
        self.assertEqual(log.splitlines(), ["flash userdata x.img", "erase userdata", "boot x.img", "reboot"])
        self.assertEqual(self.fake.get_state()["state"], "fastboot")   # no transition happened
        self.fake.set_state({"state": "android", "product": "sweet"})
        rc, _, err = self.run_cmd("adb", "reboot", "bootloader")
        self.assertEqual(rc, 1)
        with open(self.fake.adb_log) as f:
            self.assertEqual(f.read(), "reboot bootloader\n")
        self.assertEqual(self.fake.get_state()["state"], "android")

    def test_04d_getvar_battery_and_storage_temperature(self):
        """tools/safety-check fastboot reads battery-voltage and storage-temperature: defaults 3900 / 30, state keys battery_mv / storage_c, bluejay only."""
        self.fake.set_state({"state": "fastboot", "product": "bluejay"})
        self.assertEqual(self.run_cmd("fastboot", "getvar", "battery-voltage")[2], "battery-voltage: 3900\n")
        self.assertEqual(self.run_cmd("fastboot", "getvar", "storage-temperature")[2], "storage-temperature: 30\n")
        self.fake.set_state({"state": "fastboot", "product": "bluejay", "battery_mv": 3412, "storage_c": 41})
        self.assertEqual(self.run_cmd("fastboot", "-s", SER, "getvar", "battery-voltage")[2], "battery-voltage: 3412\n")
        self.assertEqual(self.run_cmd("fastboot", "getvar", "storage-temperature")[2], "storage-temperature: 41\n")
        self.assertEqual(self.run_cmd("fastboot", "getvar", "product")[2], "product: bluejay\n")   # unchanged
        self.fake.set_state({"state": "fastboot", "product": "sweet", "battery_mv": 3412})
        for var in ("battery-voltage", "storage-temperature"):
            rc, _, err = self.run_cmd("fastboot", "getvar", var)
            self.assertEqual(rc, 1, var)
            self.assertIn("refused", err)

    def test_05_write_on_bluejay_transitions(self):
        self.fake.set_state({"state": "android", "product": "bluejay"})
        self.assertEqual(self.run_cmd("adb", "reboot", "bootloader")[0], 0)
        self.assertEqual(self.fake.get_state()["state"], "fastboot")
        self.assertEqual(self.run_cmd("fastboot", "reboot")[0], 0)
        self.assertEqual(self.fake.get_state()["state"], "android")

    def _log(self, name):
        try:
            with open(os.path.join(self.dir, name)) as f:
                return f.read().splitlines()
        except FileNotFoundError:   # lint-silent: expected a fake-phone log that was never written has no lines: []
            return []

    def test_04b_other_product_read_allowlist(self):
        """On product != bluejay only the reads of tools/phone are answered; every other verb is logged and refused (review sim-1-r1 F1/F2/F4)."""
        self.fake.set_state({"state": "android", "product": "sweet"})
        for argv in (("adb", "-s", SER, "reboot", "bootloader"), ("adb", "reboot"), ("adb", "reboot", "recovery"),
                     ("adb", "sideload", "x"), ("adb", "push", "a", "b"), ("adb", "root"), ("adb", "remount"),
                     ("adb", "shell", "reboot"), ("adb", "shell", "echo getprop ro.product.device"),
                     ("fastboot", "-s", SER, "flash", "userdata", "x.img"), ("fastboot", "continue"), ("fastboot", "stage", "x"),
                     ("fastboot", "--slot", "b", "flash", "boot", "x"), ("fastboot", "-w"), ("fastboot", "-w", "devices"),
                     ("fastboot", "flash:raw", "boot", "x"), ("fastboot", "erase:x")):
            rc, _, err = self.run_cmd(*argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("refused", err, argv)
        self.assertEqual(len(self._log("adb.log")), 9)
        self.assertEqual(len(self._log("fastboot.log")), 8)
        # any leading option refuses even a read verb, and value-taking options are skipped whole: the refusal names the real verb
        for argv, verb in ((("adb", "-a", "devices"), "devices"), (("adb", "-H", "host", "devices"), "devices"),
                           (("fastboot", "-i", "0x18d1", "flash", "boot", "x"), "flash"), (("fastboot", "-S", "1M", "erase", "x"), "erase")):
            rc, _, err = self.run_cmd(*argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("refused verb '%s'" % verb, err, argv)
        self.assertEqual(len(self._log("adb.log")), 11)
        self.assertEqual(len(self._log("fastboot.log")), 10)
        self.assertEqual(self.fake.get_state()["state"], "android")
        # the reads stay answered, with -s stripped
        self.assertEqual(self.run_cmd("adb", "-s", SER, "devices")[1], ADB_DEVICES["android"])
        self.assertEqual(self.run_cmd("adb", "-s", SER, "shell", "getprop", "ro.product.device")[1], "sweet\n")
        # finding 5: a script that merely contains the text is not the getprop request, on bluejay too
        self.fake.set_state({"state": "android", "product": "bluejay"})
        rc, out, _ = self.run_cmd("adb", "shell", "echo getprop ro.product.device")
        self.assertEqual(out, "", "echo getprop must not be answered by the product")
        self.assertEqual(self.run_cmd("adb", "reboot", "bootloader", "extra")[0], 0)
        self.assertEqual(self.fake.get_state()["state"], "android", "reboot bootloader extra must not flip the state")
        self.fake.set_state({"state": "android", "product": "sweet"})
        self.fake.set_state({"state": "fastboot", "product": "sweet"})
        self.assertEqual(self.run_cmd("fastboot", "-s", SER, "devices")[1], "%s\tfastboot\n" % SER)
        self.assertEqual(self.run_cmd("fastboot", "-s", SER, "getvar", "product")[2], "product: sweet\n")

    def test_04c_adb_get_state_and_boot_completed(self):
        for prod in ("bluejay", "sweet"):
            for st, want in (("android", (0, "device\n")), ("booting", (0, "device\n")), ("other-device", (0, "device\n")),
                             ("recovery", (0, "recovery\n")), ("fastboot", (1, "")), ("absent", (1, "")), ("ios", (1, ""))):
                self.fake.set_state({"state": st, "product": prod})
                rc, out, _ = self.run_cmd("adb", "get-state")
                self.assertEqual((rc, out), want, (prod, st))
            self.fake.set_state({"state": "android", "product": prod})
            self.assertEqual(self.run_cmd("adb", "shell", "getprop", "sys.boot_completed")[1], "1\n")
            self.fake.set_state({"state": "booting", "product": prod})
            self.assertEqual(self.run_cmd("adb", "shell", "getprop", "sys.boot_completed")[1], "0\n")

    def test_05b_bluejay_option_parse(self):
        self.fake.set_state({"state": "fastboot", "product": "bluejay"})
        self.assertEqual(self.run_cmd("fastboot", "-s", SER, "reboot")[0], 0)
        self.assertEqual(self.fake.get_state()["state"], "android")
        self.fake.set_state({"state": "android", "product": "bluejay"})
        self.assertEqual(self.run_cmd("adb", "-s", SER, "reboot", "bootloader")[0], 0)
        self.assertEqual(self.fake.get_state()["state"], "fastboot")

    def test_06b_fake_sysfs_refuses_outside_root(self):
        outside = tempfile.mkdtemp(prefix="fakephone_outside_")
        try:
            open(os.path.join(outside, "keep"), "w").close()
            os.mkdir(os.path.join(outside, "sub"))
            for bad in (outside, self.dir, os.path.join(self.dir, "..", os.path.basename(outside))):
                with self.assertRaises(ValueError):
                    self.fake.fake_sysfs(bad)
            self.assertEqual(sorted(os.listdir(outside)), ["keep", "sub"])
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @staticmethod
    def _read(*parts):
        with open(os.path.join(*parts)) as f:
            return f.read()

    def test_06_fake_sysfs(self):
        sysfs = os.path.join(self.dir, "sysfs")
        # the full table, pinned against a literal (review sim-1-r1 F4): the same ids as the lsusb shim
        want = {"fastboot": ("18d1", "4ee0"), "recovery": ("18d1", "d001"), "android": ("18d1", "4ee2"), "booting": ("18d1", "4ee2"),
                "hung": ("18d1", "4ee2"), "other-device": ("18d1", "4ee2"), "ios": ("1209", "0001"), "shim-probe": ("1209", "0001")}
        self.assertEqual(fp.USB_IDS, want)
        for st in ("absent", "bogus") + tuple(want):
            self.fake.set_state({"state": st, "product": "bluejay"})
            self.fake.fake_sysfs(sysfs)
            ids = [(self._read(sysfs, d, "idVendor"), self._read(sysfs, d, "idProduct")) for d in sorted(os.listdir(sysfs))]
            self.assertEqual(ids, [(want[st][0] + "\n", want[st][1] + "\n")] if st in want else [], st)
            # the xrun check: whole-line match of 18d1 in */idVendor
            r = subprocess.run("grep -qsx 18d1 %s/*/idVendor" % sysfs, shell=True)
            self.assertEqual(r.returncode == 0, st in want and want[st][0] == "18d1", st)
            # the lsusb shim agrees with the table
            _, out, _ = self.run_cmd("lsusb")
            self.assertEqual(out != "", st in want, st)
            if st in want:
                self.assertIn("ID %s:%s " % want[st], out, st)

    def test_07_send_console(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", 0))
            s.settimeout(5)
            fp.send_console(["line one", "line two"], port=s.getsockname()[1])
            self.assertEqual(s.recvfrom(4096)[0], b"line one")
            self.assertEqual(s.recvfrom(4096)[0], b"line two")
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
