#!/usr/bin/env python3
"""run-preflight cmdline check counts the sweetepoch token xrun adds (xnu 0345). Split out of test_run_preflight.py (size limit R29)."""
import unittest
import test_run_preflight as base


class E(base.T):
    """Only the epoch case is loaded (load_tests below)."""

    def test_cmdline_counts_epoch_token(self):   # xnu 0345: xrun adds sweetepoch=<10 digits> (22 bytes) to every run; the preflight cmdline check counts it like sweetremote (pattern: the value changes per second)
        pad = self.cmdline_pad(2037, "--no-remote-reset", "--no-wdt", "--no-chg", "--no-epoch")
        rc, out = self.xrun(pad, "--early", "--expect", "x", "--no-remote-reset", "--no-wdt", "--no-chg")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"cmdline  FAIL boot-args are 2059 bytes, the limit is 2047 \(mkpayload refuses; 12 over\); implicit: sweetepoch=\d{10} \(\+22\)")


def load_tests(loader, tests, pattern):   # only the epoch case; the inherited base cases run in test_run_preflight.py
    return unittest.TestSuite([E("test_cmdline_counts_epoch_token")])


if __name__ == "__main__":
    unittest.main()
