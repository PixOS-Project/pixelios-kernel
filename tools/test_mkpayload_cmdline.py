import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
import mkpayload


class CmdlineLimit(unittest.TestCase):
    def test_at_limit_ok(self):
        self.assertEqual(len(mkpayload.check_cmdline('a' * 2047)), 2047)

    def test_over_limit_exits(self):
        with self.assertRaises(SystemExit):
            mkpayload.check_cmdline('a' * 2048)

    def test_default_cmdline_ok(self):
        mkpayload.check_cmdline('debug=0xc serial=3 cpus=1 keepsyms=1')


if __name__ == '__main__':
    unittest.main()
