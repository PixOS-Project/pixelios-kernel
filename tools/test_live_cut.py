#!/usr/bin/env python3
"""Host test for tools/live-cut: a growing session usbnet.log, begin/end cuts whole lines into a new run dir."""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        os.makedirs(os.path.join(self.d, "tools"))
        shutil.copy(os.path.join(HERE, "live-cut"), os.path.join(self.d, "tools", "live-cut"))
        shutil.copy(os.path.join(HERE, "hot_shared.py"), os.path.join(self.d, "tools", "hot_shared.py"))   # live-cut imports it (shared hot sessions)
        self.sess = os.path.join(self.d, "logs", "runs", "20261109T000000Z-sess")
        os.makedirs(self.sess)
        self.log = os.path.join(self.sess, "usbnet.log")
        open(self.log, "w").write("boot line\n")

    def lc(self, *a):
        return subprocess.run([sys.executable, os.path.join(self.d, "tools", "live-cut")] + list(a), capture_output=True, text=True, timeout=30)

    def test_cut_whole_lines_since_begin(self):
        self.assertEqual(self.lc("begin", "it1").returncode, 0)
        with open(self.log, "a") as f:
            f.write("SWEET NAV: a\nSWEET NAV: b\npartial")
        r = self.lc("end", "it1")
        self.assertEqual(r.returncode, 0, r.stdout)
        d = r.stdout.split("LIVE CUT: ")[1].split()[0]
        self.assertEqual(open(os.path.join(d, "usbnet.log")).read(), "SWEET NAV: a\nSWEET NAV: b\n")
        self.assertEqual(json.load(open(os.path.join(d, "run.json")))["live_cut"], "1")
        self.assertIn("cut it1", self.lc("list").stdout)

    def test_end_without_begin_and_no_session(self):
        self.assertEqual(self.lc("end", "nope").returncode, 2)
        os.utime(self.log, (0, 0))
        self.assertEqual(self.lc("begin", "x").returncode, 2)   # nothing written in the last 120 s: no live session


if __name__ == "__main__":
    unittest.main()
