#!/usr/bin/env python3
"""tools/root_gaps.py: its self-test must pass, and xrun_report counts run.json root_gaps lines as GAPs."""
import os, subprocess, sys, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "root_gaps.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS root_gaps self-test", r.stdout)

    def test_unreadable_image_is_an_error(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "root_gaps.py"), os.devnull], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ROOT GAP: ", r.stdout)

    def test_report_counts_root_gaps(self):
        import xrun_report
        line = "BT GAP: root sets bluetoothd defaults LE/SupportsMultipleAdvertising=false (stand-in: test)"
        g = xrun_report.all_gaps([], None, xrun_report.extra_gap_text({"usb_port": "", "root_gaps": line}))
        self.assertEqual(g["distinct"], 1, g)
        self.assertEqual(g["top"][0]["area"], "BT")


if __name__ == "__main__":
    unittest.main()
