#!/usr/bin/env python3
"""Tests for tools/pixping.c (host build, Linux) and tools/pixping-stage. Host only, no phone.

pixping.c is compiled with the host gcc and run against 127.0.0.1 on interface lo (needs the host to allow unprivileged ICMP
datagram sockets, net.ipv4.ping_group_range; if not, those tests print a loud `SKIP:` line and count in skipped=N).
The iOS-toolchain test of pixping-stage also SKIPs loudly without the theos toolchain. Last line:
RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S ; exit 0 only if no failure/error.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import contextlib, importlib.machinery, importlib.util, io, os, plistlib, re, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_l = importlib.machinery.SourceFileLoader('pixping_stage', os.path.join(HERE, 'pixping-stage'))
_s = importlib.util.spec_from_loader('pixping_stage', _l)
ps = importlib.util.module_from_spec(_s)
_l.exec_module(ps)
GW = '192.0.2.77'   # TEST-NET-1 documentation address, not a real gateway


class Host(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='pixping-test-')
        cls.exe = os.path.join(cls.tmp.name, 'pixping')
        p = subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-o', cls.exe, os.path.join(HERE, 'pixping.c')],
                           capture_output=True, text=True)
        assert p.returncode == 0, 'host gcc failed: ' + p.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_px(self, *args, timeout=30):
        p = subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout

    def need_icmp(self):
        rc, out = self.run_px('-c', '1', '-i', 'lo', '127.0.0.1')
        if rc != 0:
            print('SKIP: %s: host forbids unprivileged ICMP sockets or no lo: %s' % (self.id().split('.')[-1], out.strip()[-200:]), file=sys.stderr)
            self.skipTest('no ICMP datagram socket')

    def test_loopback_three_replies_exit_0(self):
        self.need_icmp()
        rc, out = self.run_px('-c', '3', '-i', 'lo', '127.0.0.1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(re.findall(r'(?m)^pixping: reply seq=\d bytes=64 rtt_us=\d+$', out)), 3, out)
        self.assertRegex(out, r'(?m)^pixping: summary sent=3 got=3 lost=0$')
        self.assertRegex(out, r'(?m)^pixping: iface lo ready addr=127\.0\.0\.1 ')

    def test_fake_interface_waits_then_gap_exit_3(self):
        rc, out = self.run_px('-W', '1', '-i', 'nosuch0', '127.0.0.1')
        self.assertEqual(rc, 3, out)
        self.assertRegex(out, r'(?m)^pixping: waiting for nosuch0: interface missing')
        self.assertRegex(out, r'(?m)^pixping: GAP interface nosuch0 not ready after 1s \(interface missing\)$')
        self.assertRegex(out, r'(?m)^pixping: summary sent=0 got=0 lost=0$')

    def test_unreachable_destination_is_loss_not_success(self):
        self.need_icmp()
        # 127.0.0.1 is answered; an unrouted TEST-NET address on lo is not: either sendto fails (GAP) or replies time out
        rc, out = self.run_px('-c', '2', '-i', 'lo', GW)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r'(?m)^pixping: summary sent=\d got=0 lost=\d$')
        self.assertTrue(re.search(r'pixping: (GAP sendto|timeout seq=)', out), out)

    def test_argument_errors_exit_2_with_gap(self):
        for args in (['-c', '0', '127.0.0.1'], ['-c', 'x', '127.0.0.1'], ['-W', '-1', '127.0.0.1'], ['not-an-ip'], [], ['1.2.3.4', '5.6.7.8'], ['-z', '1.2.3.4']):
            rc, out = self.run_px(*args)
            self.assertEqual(rc, 2, (args, out))
            self.assertIn('pixping: GAP', out, args)

    def test_stdout_is_unbuffered_line_per_event(self):
        # a pipe that is read while the child still waits shows the 'start' line before the wait ends
        p = subprocess.Popen([self.exe, '-W', '3', '-i', 'nosuch0', '127.0.0.1'], stdout=subprocess.PIPE, text=True)
        first = p.stdout.readline()
        self.assertTrue(first.startswith('pixping: start '), first)
        self.assertIsNone(p.poll(), 'first line must arrive while the process is still running')
        p.communicate(timeout=10)


class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pixping-stage-test-')
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def base(self, pl=None):
        p = os.path.join(self.d, 'base.plist')
        with open(p, 'wb') as f:
            f.write(plistlib.dumps(pl or {'LaunchDaemons': {'/System/Library/LaunchDaemons/a.plist': {'Label': 'a'},
                                                            ps.ds.JOB_KEY: {'Label': 'org.pixelios.devd', 'ProgramArguments': ['devd']}}, 'Version': 3},
                                   fmt=plistlib.FMT_BINARY))
        return p

    def main(self, argv, env=None):
        out = io.StringIO()
        old = os.environ.get('PIXOS_WIFI_GW')
        os.environ.pop('PIXOS_WIFI_GW', None)
        if env:
            os.environ['PIXOS_WIFI_GW'] = env
        try:
            with contextlib.redirect_stdout(out):
                rc = ps.main(argv)
        finally:
            os.environ.pop('PIXOS_WIFI_GW', None)
            if old is not None:
                os.environ['PIXOS_WIFI_GW'] = old
        return rc, out.getvalue()

    def test_job_shape_gateway_from_env_other_jobs_kept(self):
        tree = os.path.join(self.d, 'tree')
        pl = plistlib.load(open(self.base(), 'rb'))
        ps.add_job(pl, GW)
        j = pl['LaunchDaemons'][ps.JOB_KEY]
        self.assertEqual(j['Label'], 'org.pixelios.pixping')
        self.assertEqual(j['ProgramArguments'], ['pixping', '-c', '10', '-s', '200', '-W', '180', GW])
        self.assertEqual(j['Program'], '/usr/local/bin/pixping')
        self.assertTrue(j['RunAtLoad'])
        self.assertNotIn('KeepAlive', j)
        j2 = pl['LaunchDaemons'][ps.JOB2_KEY]       # W-UI: the second ping, delayed into the Settings window
        self.assertEqual(j2['Label'], 'org.pixelios.pixping2')
        self.assertEqual(j2['ProgramArguments'], ['pixping', '-D', str(ps.JOB2_DELAY), '-c', '10', '-s', '1000', '-W', '120', GW])
        self.assertEqual(len(pl['LaunchDaemons']), 4)
        self.assertEqual(ps.stage_plist(self.base(), tree, GW), 6, 'pixping + pixping2 + pixopen + pixstate jobs added')
        got = plistlib.loads(open(os.path.join(tree, 'System/Library/xpc/launchd.plist'), 'rb').read())
        self.assertIn('org.pixelios.devd', [v['Label'] for v in got['LaunchDaemons'].values()], 'devd job survives')

    def test_refuses_base_with_job_and_non_cache(self):
        pl = plistlib.load(open(self.base(), 'rb'))
        ps.add_job(pl, GW)
        with self.assertRaises(ps.Err):
            ps.add_job(pl, GW)
        with self.assertRaises(ps.Err):
            ps.add_job({'Label': 'x'}, GW)

    def test_gateway_required_and_validated_not_printed(self):
        rc, out = self.main(['--out-dir', self.d])
        self.assertEqual(rc, 2, out)
        self.assertRegex(out, r'RESULT: ERROR .*PIXOS_WIFI_GW')
        for bad in ('router.local', '1.2.3', '999.1.1.1', '::1', '1.2.3.4; rm'):
            rc, out = self.main(['--out-dir', self.d], env=bad)
            self.assertEqual(rc, 2, (bad, out))
            self.assertNotIn(bad, out, 'a bad value is not echoed')

    def test_build_stage_pack_checks_macho_and_job(self):
        why = ps.ds.toolchain_missing()
        if why:
            print('SKIP: test_build_stage_pack_checks_macho_and_job: toolchain ' + why, file=sys.stderr)
            self.skipTest(why)
        out_dir = os.path.join(self.d, 'out')
        rc, out = self.main(['--out-dir', out_dir, '--launchd-base', self.base()], env=GW)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'(?m)^RESULT: PASS pixping-stage files=6 launchd=staged ')
        self.assertNotIn(GW, out, 'gateway is not printed')
        self.assertIn('sweetnull=backboardd,SpringBoard,pixping,pixopen', out)
        exe = os.path.join(out_dir, 'tree/usr/local/bin/pixping')
        desc = subprocess.run(['file', '-b', exe], capture_output=True, text=True).stdout
        self.assertIn('Mach-O 64-bit arm64', desc)
        self.assertEqual(os.stat(exe).st_mode & 0o777, 0o755)
        pl = plistlib.loads(open(os.path.join(out_dir, 'tree/System/Library/xpc/launchd.plist'), 'rb').read())
        self.assertEqual(pl['LaunchDaemons'][ps.JOB_KEY]['ProgramArguments'][-1], GW)
        ents = ps.ds.load_mkdevoverlay().parse(open(os.path.join(out_dir, 'pixping-overlay.bin'), 'rb').read())
        self.assertEqual(sorted(e[0] for e in ents), ['/System/Library/xpc/launchd.plist', '/usr/local/bin/pixnet', '/usr/local/bin/pixopen', '/usr/local/bin/pixperf', '/usr/local/bin/pixping', '/usr/local/bin/pixstate'])

    def test_no_launchd_base_is_a_loud_gap(self):
        why = ps.ds.toolchain_missing()
        if why:
            print('SKIP: test_no_launchd_base_is_a_loud_gap: toolchain ' + why, file=sys.stderr)
            self.skipTest(why)
        rc, out = self.main(['--out-dir', os.path.join(self.d, 'o2')], env=GW)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'(?m)^SWEET GAP: pixping-stage: no launchd base given')
        self.assertRegex(out, r'launchd=NOT-staged')


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    bad = len(res.failures) + len(res.errors)
    print('RESULT: %s tests=%d failures=%d errors=%d skipped=%d' % ('FAIL' if bad else 'PASS', res.testsRun, len(res.failures), len(res.errors), len(res.skipped)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
