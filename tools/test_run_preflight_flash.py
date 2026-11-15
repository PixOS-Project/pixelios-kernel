#!/usr/bin/env python3
"""tools/run_preflight_flash: its self-test must pass, and tools/run-preflight --userdata-check prints the FLASH line before RESULT (R54)."""
import os, subprocess, sys, tempfile, time, unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))


class T(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "run_preflight_flash.py"), "--self-test"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def ud(self, state, reasons):
        env = dict(os.environ, FLASH_STATE=state, FLASH_REASONS=reasons)
        p = subprocess.run([sys.executable, os.path.join(TOOLS, "run-preflight"), "--userdata-check"], capture_output=True, text=True, env=env)
        return p.returncode, p.stdout

    def test_userdata_check_prints_flash_reason_line(self):   # last row and today's count, before the RESULT line; exit codes unchanged
        with tempfile.TemporaryDirectory() as t:
            state, reasons = t + "/flashed.txt", t + "/reasons.tsv"
            open(state, "w").write("9223c0f2aa" + "0" * 54 + " 2026-10-24T13:01:14Z /x.img\n")
            today = time.strftime("%Y-%m-%d", time.gmtime())
            open(reasons, "w").write("2020-01-01T00:00:00Z\t%s\told\n%sT04:05:06Z\t%s\troot image rebuilt\n" % ("a" * 64, today, "c" * 64))
            rc, out = self.ud(state, reasons)
            self.assertEqual(rc, 0, out)
            self.assertEqual(out.splitlines()[0], "FLASH: last=%sT04:05:06Z reason=root image rebuilt today=1" % today)
            self.assertTrue(out.splitlines()[-1].startswith("RESULT: PASS userdata"), out)
            rc, out = self.ud(state, t + "/none.tsv")   # no reasons file: loud, not silent
            self.assertEqual(out.splitlines()[0], "FLASH: none recorded", out)
            rc, out = self.ud(t + "/nonexistent.txt", reasons)   # a failing userdata check still shows the FLASH line
            self.assertEqual(rc, 2, out)
            self.assertIn("FLASH: last=", out)


if __name__ == "__main__":
    unittest.main()
