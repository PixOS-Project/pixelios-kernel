#!/usr/bin/env python3
"""Tests for tools/pixopen.c (host build, Linux, API stubbed) and the pixopen half of tools/pixping-stage. Host only, no phone.

pixopen.c is compiled with the host gcc; on Linux CFURLRef is a malloc'ed copy of the URL string and `-l LIB` loads a stub shared
library exporting SBSOpenSensitiveURLAndUnlock (one returning 1, one returning 0, one without the symbol). The iOS-toolchain test of
the stage tool (Mach-O arm64, ad-hoc signature, entitlements present in the signature via `ldid -e`) SKIPs loudly without the
theos toolchain. Last line: RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S ; exit 0 only if no failure/error.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import contextlib, importlib.machinery, importlib.util, io, os, plistlib, re, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_l = importlib.machinery.SourceFileLoader('pixping_stage_for_open', os.path.join(HERE, 'pixping-stage'))
_s = importlib.util.spec_from_loader('pixping_stage_for_open', _l)
ps = importlib.util.module_from_spec(_s)
_l.exec_module(ps)
GW = '192.0.2.77'   # TEST-NET-1 documentation address, not a real gateway

STUB_TRUE = '#include <stdio.h>\nunsigned char SBSOpenSensitiveURLAndUnlock(const void *u, unsigned char k){printf("stub: url=%s unlock=%d\\n",(const char*)u,k);return 1;}\n'
STUB_FALSE = STUB_TRUE.replace('return 1;', 'return 0;')
STUB_NOSYM = 'int unrelated_symbol(void){return 0;}\n'


class Host(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='pixopen-test-')
        d = cls.tmp.name
        cls.exe = os.path.join(d, 'pixopen')
        p = subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-o', cls.exe, os.path.join(HERE, 'pixopen.c'), '-ldl'],
                           capture_output=True, text=True)
        assert p.returncode == 0, 'host gcc failed: ' + p.stderr
        cls.lib = {}
        for name, src in (('true', STUB_TRUE), ('false', STUB_FALSE), ('nosym', STUB_NOSYM)):
            c = os.path.join(d, 'stub_%s.c' % name)
            so = os.path.join(d, 'stub_%s.so' % name)
            open(c, 'w').write(src)
            p = subprocess.run(['gcc', '-shared', '-fPIC', '-o', so, c], capture_output=True, text=True)
            assert p.returncode == 0, 'stub gcc failed: ' + p.stderr
            cls.lib[name] = so

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_po(self, *args, timeout=30):
        p = subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout

    def test_default_url_opens_wifi_and_prints_rc(self):
        rc, out = self.run_po('-d', '0', '-l', self.lib['true'])
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'(?m)^pixopen: start url=prefs:root=WIFI delay=0 tries=1 unlock=1$')
        self.assertRegex(out, r'(?m)^stub: url=prefs:root=WIFI unlock=1$')
        self.assertRegex(out, r'(?m)^pixopen: open prefs:root=WIFI rc=1$')
        self.assertNotIn('GAP', out)

    def test_any_url_as_argument_and_no_unlock_flag(self):
        rc, out = self.run_po('-d', '0', '-n', '-l', self.lib['true'], 'prefs:root=Bluetooth')
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'(?m)^stub: url=prefs:root=Bluetooth unlock=0$')
        self.assertRegex(out, r'(?m)^pixopen: open prefs:root=Bluetooth rc=1$')

    def test_false_return_is_a_gap_exit_1_and_retried(self):
        rc, out = self.run_po('-d', '0', '-r', '2', '-l', self.lib['false'])
        self.assertEqual(rc, 1, out)
        self.assertEqual(len(re.findall(r'(?m)^pixopen: open prefs:root=WIFI rc=0$', out)), 2, out)
        self.assertRegex(out, r'(?m)^pixopen: GAP open prefs:root=WIFI returned false \(try 1 of 2\), retrying$')
        self.assertRegex(out, r'(?m)^pixopen: GAP open prefs:root=WIFI returned false \(try 2 of 2\)$')

    def test_missing_library_and_missing_symbol_are_gaps_exit_3(self):
        rc, out = self.run_po('-d', '0', '-l', '/nonexistent/libnone.so')
        self.assertEqual(rc, 3, out)
        self.assertRegex(out, r'(?m)^pixopen: GAP dlopen /nonexistent/libnone\.so failed: ')
        rc, out = self.run_po('-d', '0', '-l', self.lib['nosym'])
        self.assertEqual(rc, 3, out)
        self.assertRegex(out, r'(?m)^pixopen: GAP dlsym SBSOpenSensitiveURLAndUnlock missing in ')

    def test_host_default_library_is_a_gap_not_success(self):
        # on Linux the default (iOS) library path does not exist: loud GAP, never a plain success
        rc, out = self.run_po('-d', '0')
        self.assertEqual(rc, 3, out)
        self.assertIn('pixopen: GAP dlopen', out)

    def test_argument_errors_exit_2_with_gap(self):
        for args in (['-d', '-1'], ['-d', 'x'], ['-d', '99999'], ['-r', '0'], ['-r', 'x'], ['a:b', 'c:d'], ['-z'], ['']):
            rc, out = self.run_po(*args)
            self.assertEqual(rc, 2, (args, out))
            self.assertIn('pixopen: GAP', out, args)

    def test_stdout_is_unbuffered_and_delay_waits(self):
        p = subprocess.Popen([self.exe, '-d', '2', '-l', self.lib['true']], stdout=subprocess.PIPE, text=True)
        first = p.stdout.readline()
        self.assertTrue(first.startswith('pixopen: start '), first)
        self.assertIsNone(p.poll(), 'first line must arrive while the process still waits')
        out = first + p.communicate(timeout=10)[0]
        self.assertIn('pixopen: open prefs:root=WIFI rc=1', out)

    def test_default_delay_is_60_in_source(self):
        src = open(os.path.join(HERE, 'pixopen.c')).read()
        self.assertRegex(src, r'#define DEFAULT_DELAY 60\b')
        self.assertRegex(src, r'#define DEFAULT_URL "prefs:root=WIFI"')


class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pixopen-stage-test-')
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def base(self, extra=None):
        p = os.path.join(self.d, 'base.plist')
        jobs = {'/System/Library/LaunchDaemons/a.plist': {'Label': 'a'}, ps.ds.JOB_KEY: {'Label': 'org.pixelios.devd', 'ProgramArguments': ['devd']}}
        jobs.update(extra or {})
        with open(p, 'wb') as f:
            f.write(plistlib.dumps({'LaunchDaemons': jobs, 'Version': 3}, fmt=plistlib.FMT_BINARY))
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

    def test_open_job_shape(self):
        pl = plistlib.load(open(self.base(), 'rb'))
        ps.add_open_job(pl)
        j = pl['LaunchDaemons'][ps.OPEN_JOB_KEY]
        self.assertEqual(j['Label'], 'org.pixelios.pixopen')
        self.assertEqual(j['Program'], '/usr/local/bin/pixopen')
        self.assertEqual(j['ProgramArguments'], ['pixopen', '-d', '60', '-r', '3', 'prefs:root=WIFI'])
        self.assertEqual(j['UserName'], 'mobile')
        self.assertTrue(j['RunAtLoad'])
        self.assertNotIn('KeepAlive', j)
        self.assertEqual(len(pl['LaunchDaemons']), 3)

    def test_stage_plist_adds_both_jobs_pixping_unchanged(self):
        tree = os.path.join(self.d, 'tree')
        self.assertEqual(ps.stage_plist(self.base(), tree, GW), 6, 'pixping + pixping2 + pixopen + pixstate (0999e609) added to the base jobs')
        got = plistlib.loads(open(os.path.join(tree, 'System/Library/xpc/launchd.plist'), 'rb').read())
        jobs = got['LaunchDaemons']
        self.assertEqual(jobs[ps.JOB_KEY]['ProgramArguments'], ['pixping', '-c', '10', '-s', '200', '-W', '180', GW])
        self.assertEqual(jobs[ps.JOB_KEY]['UserName'], 'root')
        self.assertEqual(jobs[ps.OPEN_JOB_KEY]['ProgramArguments'][0], 'pixopen')
        self.assertIn('org.pixelios.devd', [v['Label'] for v in jobs.values()], 'devd job survives')

    def test_refuses_base_that_already_has_the_open_job_or_is_not_a_cache(self):
        pl = plistlib.load(open(self.base(), 'rb'))
        ps.add_open_job(pl)
        with self.assertRaises(ps.Err):
            ps.add_open_job(pl)
        with self.assertRaises(ps.Err):
            ps.add_open_job({'Label': 'x'})

    def test_entitlements_plist_is_boolean_true_for_each_key(self):
        ents = plistlib.loads(ps.entitlements_xml(ps.OPEN_ENTITLEMENTS))
        self.assertEqual(ents, {'com.apple.springboard.opensensitiveurl': True, 'com.apple.springboard.launchapplications': True})

    def test_boot_arg_names_both_processes(self):
        self.assertEqual(ps.BOOT_ARG, 'sweetnull=backboardd,SpringBoard,pixping,pixopen,pixperf,pixnet')      # pixperf since fe164a0f, pixnet since e6fcbfb7

    def test_build_stage_pack_ios_binary_signed_with_entitlements(self):
        why = ps.ds.toolchain_missing()
        if why:
            print('SKIP: test_build_stage_pack_ios_binary_signed_with_entitlements: toolchain ' + why, file=sys.stderr)
            self.skipTest(why)
        out_dir = os.path.join(self.d, 'out')
        rc, out = self.main(['--out-dir', out_dir, '--launchd-base', self.base()], env=GW)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'(?m)^RESULT: PASS pixping-stage files=6 launchd=staged ')
        self.assertIn('sweetnull=backboardd,SpringBoard,pixping,pixopen', out)
        self.assertNotIn(GW, out, 'gateway is not printed')
        exe = os.path.join(out_dir, 'tree/usr/local/bin/pixopen')
        self.assertIn('Mach-O 64-bit arm64', subprocess.run(['file', '-b', exe], capture_output=True, text=True).stdout)
        self.assertEqual(os.stat(exe).st_mode & 0o777, 0o755)
        tc = ps.ds.toolchain()
        ents = subprocess.run([tc['ldid'], '-e', exe], capture_output=True, text=True).stdout
        parsed = plistlib.loads(ents.encode())
        self.assertEqual(parsed, {k: True for k in ps.OPEN_ENTITLEMENTS})
        ping_ents = subprocess.run([tc['ldid'], '-e', os.path.join(out_dir, 'tree/usr/local/bin/pixping')], capture_output=True, text=True).stdout
        self.assertNotIn('<key>', ping_ents, 'pixping stays unentitled')
        lh = subprocess.run([tc['ldid'], '-h', exe], capture_output=True, text=True).stdout
        self.assertRegex(lh, r'(?m)^Identifier=pixopen$')
        self.assertRegex(lh, r'flags=0x[0-9a-f]+\(adhoc\)')
        ents_in_overlay = ps.ds.load_mkdevoverlay().parse(open(os.path.join(out_dir, 'pixping-overlay.bin'), 'rb').read())
        self.assertEqual(sorted(e[0] for e in ents_in_overlay),
                         ['/System/Library/xpc/launchd.plist', '/usr/local/bin/pixnet', '/usr/local/bin/pixopen', '/usr/local/bin/pixperf', '/usr/local/bin/pixping', '/usr/local/bin/pixstate'])


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    bad = len(res.failures) + len(res.errors)
    print('RESULT: %s tests=%d failures=%d errors=%d skipped=%d' % ('FAIL' if bad else 'PASS', res.testsRun, len(res.failures), len(res.errors), len(res.skipped)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
