#!/usr/bin/env python3
"""Unit tests for tools/phone state machine without real hardware.

Simulates device behaviors using mock adb, fastboot, lsusb, and ping
binaries in a temporary directory prepended to PATH.
"""

from importlib.machinery import SourceFileLoader
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHONE_TOOL = os.path.join(REPO_ROOT, "tools", "phone")

# Import tools/phone as a module
_spec = importlib.util.spec_from_loader("phone", SourceFileLoader("phone", PHONE_TOOL))
phone = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(phone)

# Import the shared fakes (tools/sim/fakephone.py)
_fspec = importlib.util.spec_from_file_location("fakephone", os.path.join(REPO_ROOT, "tools", "sim", "fakephone.py"))
fakephone = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(fakephone)


class PhoneStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="phone_sm_test_")
        self.bin_dir = os.path.join(self.test_dir, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)

        # SIM-1: the fakes live in tools/sim/fakephone.py; serial kept as the marker the tests assert never reaches the output
        self.fake = fakephone.FakePhone(self.test_dir, serial="SECRET_SERIAL123")
        self.fake.write_shims(self.bin_dir)
        self.state_file = self.fake.state_file
        self.adb_log = self.fake.adb_log
        self.fastboot_log = self.fake.fastboot_log
        self.reset_log = self.fake.reset_log

        self.orig_path = os.environ.get("PATH", "")

        # Use mock bin directory first on PATH
        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.orig_path}"
        self.env["PHONE_STATE_TEST_DIR"] = self.test_dir
        self.env["PHONE_RESET_BL_BIN"] = os.path.join(self.bin_dir, "fake-sweet-reset-bl")	# AP-55: never the real UDP sender
        # TP-020: goto needs the phone lock; the tests hold their own, in a temp dir
        self.env["PHONE_LOCK_DIR"] = os.path.join(self.test_dir, "phone.lock")
        self.env["PHONE_LOCK_TOKEN"] = "sm-test-token"
        self.lock_tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-lock")
        subprocess.run([self.lock_tool, "acquire", "--owner", "sm-test", "--token", "sm-test-token"], env=self.env, capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def set_state(self, state_dict):
        self.fake.set_state(state_dict)

    def flip_after_probes(self, n, state_dict):
        self.fake.flip_after_probes(n, state_dict)

    def flip_on_reset(self, state_dict):
        self.fake.flip_on_reset(state_dict)

    def get_state(self):
        return self.fake.get_state()

    def run_phone_until(self, args, marker, state_dict, timeout=60):
        """Run tools/phone; when a stdout line contains marker, set the fake state to state_dict (the user acted after the prompt). Returns (returncode, stdout)."""
        p = subprocess.Popen([PHONE_TOOL] + list(args), env=self.env, stdout=subprocess.PIPE, text=True)
        lines = []
        t0 = time.time()
        for line in p.stdout:
            lines.append(line)
            if marker in line:
                self.set_state(state_dict)
            if time.time() - t0 > timeout:
                p.kill()
                break
        p.wait()
        return p.returncode, "".join(lines)

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

        self.flip_after_probes(3, {"state": "recovery", "product": "bluejay"})   # absent for the first 3 probes, then the phone appears

        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)

    # 4. ios -> fastboot via self-reset (state changes after the first probe)
    def test_04_ios_to_fastboot_self_reset(self):
        self.set_state({"state": "ios", "product": "bluejay"})
        self.env["PHONE_BOUND_SCALE"] = "0.1"	# the ios wait bound is 90 s: 9 s here

        self.flip_on_reset({"state": "recovery", "product": "bluejay"})   # the phone comes back once tools/phone asked for the reset

        res = self.run_phone("goto", "fastboot", "--timeout", "15")
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)
        self.assertIn("USBNET: sudo sh -c", res.stdout)
        self.assertNotIn("NEED USER", res.stdout)	# the phone reset by itself inside the bound: the user is never asked
        with open(self.reset_log) as f:	# AP-55: the fake ran; the real script cannot have (PATH-less absolute call would send UDP)
            self.assertIn("called", f.read())

    def test_04b_real_reset_bl_refuses_without_lock(self):
        # AP-55 backstop: the real script exits 2 before sending when the caller holds no phone lock (empty lock dir, no token)
        env = dict(self.env, PHONE_LOCK_DIR=os.path.join(self.test_dir, "nolock"), PHONE_LOCK_TOKEN="")
        res = subprocess.run([os.path.join(REPO_ROOT, "tools", "sweet-reset-bl")], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 2)
        self.assertIn("RESULT: ERROR", res.stdout)

    # 4b. ios -> reset command -> absent for a while -> recovery -> fastboot: no button prompt while the phone is still off the bus
    def test_04b_ios_reset_absent_then_recovery(self):
        self.set_state({"state": "ios", "product": "bluejay"})
        self.flip_on_reset({"state": "absent", "product": "bluejay"})	# the reset takes the phone off the bus (review boot-dev-merge F1)
        self.env["PHONE_BOUND_SCALE"] = "0.1"	# wait bound 150 s -> 15 s; absent for 4 s (the old 1 s absent bound prompted at once)

        import threading
        def late_recovery():
            time.sleep(4.0)
            self.set_state({"state": "recovery", "product": "bluejay"})
        th = threading.Thread(target=late_recovery)
        th.start()
        res = self.run_phone("goto", "fastboot", "--timeout", "30")
        th.join()
        self.assertEqual(res.returncode, 0)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", res.stdout)
        self.assertNotIn("NEED USER", res.stdout)
        with open(self.reset_log) as f:
            calls = f.read().split()
            self.assertEqual(calls, ["called"])	# the mock, never the real UDP sender; sent once: absent is a wait-only step, no reset loop

    # 5. hung -> NEED USER line then success after fake state changes
    def test_05_hung_need_user_and_recover(self):
        self.set_state({"state": "hung", "product": "bluejay"})
        self.env["PHONE_BOUND_SCALE"] = "0.1"	# the hung bound is 10 s: 1 s here; the fake recovers when the prompt line is seen

        rc, out = self.run_phone_until(["goto", "fastboot", "--timeout", "10"], "NEED USER: hold Power+VolDown", {"state": "fastboot", "product": "bluejay"})   # the user acts once the prompt is printed
        self.assertEqual(rc, 0)
        self.assertIn("NEED USER: hold Power+VolDown", out)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot", out)

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
        # the refusal's own message (tools/phone:587): without it a removed refusal would still end in the same RESULT line through the "unknown transition" layer (docs/reviews/sim-1-r1.md mutant D)
        self.assertIn("Safety: other-device detected; refusing commands", res.stdout)

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

    # 12. docs/reviews/sim-1-r1.md mutants A-D, F: tools/phone's other-device refusal is layered (observe_state, the goto refusals :587/:619, verify_bluejay_target, the unknown-transition table);
    # test_06 only reaches the first layers, so these two tests pin the rest.
    def _logs(self):
        out = {}
        for name, path in (("adb", self.adb_log), ("fastboot", self.fastboot_log)):
            out[name] = ""
            if os.path.exists(path):
                with open(path) as f:
                    out[name] = f.read()
        return out

    def _stub_phone_state(self, line):
        """PHONE_STATE_BIN (tools/phone) answers `line` whatever the fake says (until the reboot): observe_state is made to see exactly that state."""
        stub = os.path.join(self.bin_dir, "stub-phone-state")
        with open(stub, "w") as f:
            # until the fake adb has logged a `reboot bootloader`; afterwards the real tools/phone-state (on the fakes) takes over, so the run can end
            f.write("#!/bin/bash\ngrep -qs 'reboot bootloader' '%s' && exec '%s'\necho '%s'\n" % (self.adb_log, os.path.join(REPO_ROOT, "tools", "phone-state"), line))
        os.chmod(stub, 0o755)
        self.env["PHONE_STATE_BIN"] = stub

    def test_12a_android_goto_fastboot_sends_exactly_adb_reboot_bootloader(self):
        # mutant C: the android -> fastboot step sends `fastboot reboot` (tools/phone:105) instead of `adb reboot bootloader`.
        # The stub forces observe_state to `android`: with the fake alone the state reads `booting` (its combined getprop answer has no boot_completed), a different table row.
        self.set_state({"state": "android", "product": "bluejay"})
        self._stub_phone_state("RESULT: PASS state=android product=bluejay")
        res = self.run_phone("goto", "fastboot", "--timeout", "10")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot steps=1", res.stdout)
        logs = self._logs()
        self.assertEqual([l for l in logs["adb"].splitlines() if "reboot" in l], ["reboot bootloader"])
        self.assertNotIn("reboot", logs["fastboot"])

    def test_12b_android_view_but_other_product_is_refused_before_any_reboot(self):
        # mutants A, B, F: observe_state is made to say android/bluejay (a phone-state stub: the state changed between the observation and the command),
        # while the device answers product `sweet` to the verify getprop. Only verify_bluejay_target in safe_device_cmd stands between the tool and `adb reboot bootloader`.
        self.set_state({"state": "android", "product": "sweet"})
        self._stub_phone_state("RESULT: PASS state=android product=bluejay")
        res = self.run_phone("goto", "fastboot", "--timeout", "5")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("Device is not bluejay", res.stdout)
        self.assertRegex(res.stdout, r"RESULT: FAIL state=other-device target=fastboot steps=1")
        logs = self._logs()
        self.assertIn("getprop ro.product.device", logs["adb"])   # the verify ran (so this test cannot pass without reaching the layer it pins)
        self.assertNotIn("reboot", logs["adb"])
        self.assertNotIn("reboot", logs["fastboot"])
        self.assertNotIn("SECRET_SERIAL123", res.stdout)

    # 13. 2026-11-09 gputest-cache1 park: right after the UDP reset the phone listed in recovery before adbd answered getprop; phone-state
    # reported OTHER-DEVICE and goto refused at once. An unreadable product is `unverified`: no command, re-observe, then go on.
    def _stub_unverified(self, n):
        cnt = os.path.join(self.bin_dir, "unverified.count")
        stub = os.path.join(self.bin_dir, "stub-phone-state-unverified")
        with open(stub, "w") as f:
            f.write("#!/bin/bash\nc=$(cat '%s' 2>/dev/null || echo 0); echo $((c+1)) > '%s'\n"
                    "if [ \"$c\" -lt %d ]; then echo 'adb recovery: getprop gave no product yet (adbd starting?)'; echo 'RESULT: ERROR state=unknown product=unknown'; exit 2; fi\n"
                    "exec '%s'\n" % (cnt, cnt, n, os.path.join(REPO_ROOT, "tools", "phone-state")))
        os.chmod(stub, 0o755)
        self.env["PHONE_STATE_BIN"] = stub

    def test_13a_unverified_recovery_waits_then_reboots_to_fastboot(self):
        self.set_state({"state": "recovery", "product": "bluejay"})
        self._stub_unverified(3)
        res = self.run_phone("goto", "fastboot", "--timeout", "30")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("RESULT: PASS state=fastboot target=fastboot steps=1", res.stdout)
        self.assertEqual([l for l in self._logs()["adb"].splitlines() if "reboot" in l], ["reboot bootloader"])

    def test_13b_never_verified_is_refused_without_any_command(self):
        self.set_state({"state": "recovery", "product": "bluejay"})
        self._stub_unverified(10 ** 6)
        self.env["PHONE_BOUND_SCALE"] = "0.1"   # the 30 s settle: 3 s here
        res = self.run_phone("goto", "fastboot", "--timeout", "20")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("never verified as bluejay", res.stdout)
        logs = self._logs()
        self.assertNotIn("reboot", logs["adb"])
        self.assertNotIn("reboot", logs["fastboot"])


if __name__ == "__main__":
    unittest.main()
