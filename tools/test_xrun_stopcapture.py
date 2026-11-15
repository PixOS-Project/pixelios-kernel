#!/usr/bin/env python3
"""stop_capture() of tools/xrun (the EXIT-trap capture stop) on its real text, cut out of tools/xrun (ALL-ON T-56, 2026-11-13):
the build-only exit (XRUN_BUILD_ONLY, resident submit guard G7) removes the run dir before the EXIT trap runs; stop_capture must then still
return 0 (a failed touch of $D/stop made xrun rc 1 and G7 refused every kernel job), and with the run dir present it must still write
$D/stop AND wait for the usbnet listener and the breadcrumb reader (a fix that commented out the waits must fail here)."""
import os, re, subprocess, tempfile, unittest

XRUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xrun")


def fn_text():
    src = open(XRUN).read()
    m = re.search(r"^stop_capture\(\) \{.*?; \}; \}\n", src, re.S | re.M)   # the function ends with "; }; }"
    assert m, "stop_capture not found in tools/xrun"
    return m.group(0)


def run(d_exists):
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "run")
        if d_exists:
            os.mkdir(d)
        mark = os.path.join(t, "waited")
        script = ("set -euo pipefail\nD=%s\ncap=\"\"\nsafety_end() { :; }\n%s\n"
                  "( sleep 0.3; touch %s.usb ) & usbpid=$!\n( sleep 0.3; touch %s.br ) & brpid=$!\n"
                  "stop_capture\necho \"usbpid=[$usbpid] brpid=[$brpid]\"\n") % (d, fn_text(), mark, mark)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        return r, os.path.exists(os.path.join(d, "stop")), os.path.exists(mark + ".usb"), os.path.exists(mark + ".br")


class StopCapture(unittest.TestCase):
    def test_removed_run_dir_is_rc0(self):   # T-56: the build-only exit
        r, stop, usb, br = run(False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(usb and br, "the listener waits must run on this path too")

    def test_run_dir_gets_stop_and_waits(self):
        r, stop, usb, br = run(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(stop, "$D/stop not written")
        self.assertTrue(usb and br, "stop_capture returned before the usbnet listener / breadcrumb reader ended")
        self.assertIn("usbpid=[] brpid=[]", r.stdout)


if __name__ == "__main__":
    unittest.main()
