#!/usr/bin/env python3
"""Host test for tools/phone-guard (TP-020): raw mutating adb/fastboot is denied, tools and read-only queries pass."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-guard")
g = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("pg", importlib.machinery.SourceFileLoader("pg", TOOL)))
importlib.machinery.SourceFileLoader("pg", TOOL).exec_module(g)

DENY = ["fastboot flash userdata x.img", "fastboot boot x.img", "adb reboot bootloader", "cd /tmp && fastboot erase userdata",
        "sudo fastboot --set-active=a", "timeout 30 adb -s ABC shell rm -rf /data", "X=1 adb push a b", "echo hi; adb root",
        "/usr/bin/fastboot oem uart enable", "fastboot -s ABC flash boot_a b.img", "adb", "fastboot reboot",
        "fastboot flash x\ny", "echo ok; fastboot erase userdata # c", "(adb reboot)", "ls\nadb push a b",
        "if true; then fastboot flash a b; fi", "for c in reboot; do fastboot $c; done", "{ fastboot flash a; }", "! fastboot oem x",
        "bash -c \"fastboot flash a b\"", "sh -c 'adb reboot bootloader'", "eval \"fastboot erase x\"", "/usr/bin/env fastboot flash a",
        "sudo -u me fastboot flash a", "env -u X fastboot flash a", "setsid fastboot boot x", "adb shell rm -rf /data", "adb install x.apk"]
ALLOW = ["tools/fboot.sh --expect xnu a.img", "tools/xrun --kernel k", "fastboot devices", "fastboot getvar product", "adb devices -l",
         "adb get-state", "timeout 15 fastboot getvar unlocked", "grep fastboot docs/AUTOMATION.md", "echo adb reboot", "git commit -m 'fastboot flash'",
         "tools/phone goto fastboot", "ls | wc -l", "python3 tools/phone state",
         "git commit -m 'a; fastboot flash x'", "python3 - <<'EOF'\nfastboot flash userdata\nEOF", "cat <<EOF > f\nadb reboot\nEOF\nls",
         "python3 -c \"print('adb reboot')\"", "echo x > /tmp/fastboot",
         "adb shell getprop ro.product.device", "adb logcat -d", "adb pull /sdcard/x .", "adb wait-for-device", "command -v fastboot",
         "bash -c 'ls -l'", "bash tools/xrun --kernel k"]


class T(unittest.TestCase):
    def test_deny(self):
        for c in DENY:
            self.assertIsNotNone(g.offending(c), c)

    def test_allow(self):
        for c in ALLOW:
            self.assertIsNone(g.offending(c), c)

    def test_hook_protocol(self):
        def run(inp):
            r = subprocess.run([sys.executable, TOOL], input=json.dumps(inp), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            return r.stdout
        out = json.loads(run({"tool_name": "Bash", "tool_input": {"command": "fastboot flash userdata x"}}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(run({"tool_name": "Bash", "tool_input": {"command": "fastboot devices"}}), "")
        self.assertEqual(run({"tool_name": "Read", "tool_input": {"file_path": "fastboot"}}), "")


if __name__ == "__main__":
    unittest.main()
