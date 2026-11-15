#!/usr/bin/env python3
"""phone-postmortem (rule 2): with two phones attached, every adb call after `adb devices` carries -s of the bluejay; the other phone only gets
its one addressed getprop. A fake adb on PATH logs its argv; nothing real is called."""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "phone-postmortem")
ADB = r"""#!/bin/bash
echo "$*" >>"$CALLS"
if [ "$1" = devices ]; then printf 'List of devices attached\n%s' "$DEVS"; exit 0; fi
[ "$1" = -s ] || { echo "unaddressed"; exit 0; }
case "$4 $5 $6" in "getprop ro.product.device ") [ "$2" = BJ ] && echo bluejay || echo sweet; exit 0 ;; esac
echo "probe output"
"""


class T(unittest.TestCase):
    def run_(self, devs):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "adb"), "w") as f:
            f.write(ADB)
        os.chmod(os.path.join(d, "adb"), 0o755)
        calls = os.path.join(d, "calls")
        e = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], CALLS=calls, DEVS=devs)
        r = subprocess.run(["bash", TOOL, "--out", os.path.join(d, "out")], capture_output=True, text=True, env=e)
        if not os.path.exists(calls):
            return r, []
        with open(calls) as f:
            return r, f.read().splitlines()

    def test_other_phone_first_only_bluejay_probed(self):
        r, calls = self.run_("OTHER\tdevice\nBJ\trecovery\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS postmortem 9/9", r.stdout)
        later = calls[1:]
        self.assertEqual([c for c in later if not c.startswith("-s ")], [], calls)   # no unaddressed adb call
        self.assertEqual([c for c in later if c.startswith("-s OTHER")], ["-s OTHER shell getprop ro.product.device"], calls)
        self.assertEqual(sum(c.startswith("-s BJ shell") for c in later), 10, calls)   # 1 getprop + 9 probes

    def test_no_bluejay_reads_nothing(self):
        r, calls = self.run_("OTHER\tdevice\nX\tunauthorized\n")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("RESULT: ERROR no adb device reports bluejay", r.stdout)
        self.assertEqual(calls, ["devices", "-s OTHER shell getprop ro.product.device"])   # unauthorized X is not even asked


if __name__ == "__main__":
    unittest.main()
