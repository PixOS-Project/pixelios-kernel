#!/usr/bin/env python3
"""Tests for tools/devd-stage (build, sign, key, launchd job, pack). Host only, no phone.

Tests that need the iOS toolchain (theos ld/ldid/vtool/llvm-objdump + SDK + clang) print a loud `SKIP:` line and are counted in
`skipped=N` of the RESULT line when it is missing; they are never silently passed. Last line:
RESULT: PASS|FAIL tests=N failures=F errors=E skipped=S ; exit 0 only if no failure/error.
"""
import contextlib, importlib.machinery, importlib.util, io, os, plistlib, re, stat, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader('devd_stage', os.path.join(HERE, 'devd-stage'))
_spec = importlib.util.spec_from_loader('devd_stage', _loader)
ds = importlib.util.module_from_spec(_spec)
_loader.exec_module(ds)
_mk = importlib.util.spec_from_file_location('mkdevoverlay', os.path.join(HERE, 'mkdevoverlay.py'))
mk = importlib.util.module_from_spec(_mk)
_mk.loader.exec_module(mk)

BASE_JOBS = {
    '/System/Library/LaunchDaemons/com.apple.backboardd.plist': {'Label': 'com.apple.backboardd', 'ProgramArguments': ['/usr/libexec/backboardd'],
                                                                   'EnvironmentVariables': {'CA_BACKDROP_SCALE': '1'}, '_PanicOnCrasX': True},
    '/System/Library/LaunchDaemons/com.apple.configd.plist': {'Label': 'com.apple.configd', 'Program': '/usr/sbin/configd', 'KeepAlive': True},
    '/System/Library/LaunchDaemons/org.pixelios.sweetmali-test.plist': {'Label': 'org.pixelios.sweetmali-test', 'UserName': 'root',
                                                                           'RunAtLoad': True, 'StartInterval': 5},
}


def base_plist():
    return {'LaunchDaemons': {k: dict(v) for k, v in BASE_JOBS.items()}, 'Version': 3}


def tool_missing():
    return ds.toolchain_missing()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='devd-stage-test-')
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def need_toolchain(self):
        why = tool_missing()
        if why:
            print('SKIP: %s: toolchain %s' % (self.id().split('.')[-1], why), file=sys.stderr)
            self.skipTest(why)

    def write_base(self, pl=None, fmt=plistlib.FMT_BINARY):
        p = os.path.join(self.d, 'base.plist')
        with open(p, 'wb') as f:
            f.write(plistlib.dumps(pl if pl is not None else base_plist(), fmt=fmt))
        return p

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ds.main(argv)
        return rc, out.getvalue(), err.getvalue()


class PlistJob(Base):
    def test_round_trip_job_once_others_byte_equal(self):
        base = self.write_base()
        tree = os.path.join(self.d, 'tree')
        n = ds.stage_plist(base, tree)
        self.assertEqual(n, len(BASE_JOBS) + 1)
        dst = os.path.join(tree, 'System/Library/xpc/launchd.plist')
        raw = open(dst, 'rb').read()
        self.assertTrue(raw.startswith(b'bplist00'), 'binary plist like the cache')
        got = plistlib.loads(raw)
        jobs = got['LaunchDaemons']
        self.assertEqual([k for k in jobs if k == ds.JOB_KEY], [ds.JOB_KEY])
        self.assertEqual(sum(1 for j in jobs.values() if j.get('Label') == 'org.pixelios.devd'), 1)
        for k, v in BASE_JOBS.items():
            self.assertEqual(plistlib.dumps(jobs[k], fmt=plistlib.FMT_BINARY), plistlib.dumps(v, fmt=plistlib.FMT_BINARY), k)
        self.assertEqual(got['Version'], 3, 'other top-level keys survive')
        self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode), 0o644)

    def test_job_shape(self):
        pl = base_plist()
        ds.add_job(pl)
        self.assertEqual(pl['LaunchDaemons'][ds.JOB_KEY],
                         {'Label': 'org.pixelios.devd', 'Program': '/usr/local/bin/devd', 'ProgramArguments': ['devd'], 'UserName': 'root',
                          'RunAtLoad': True})
        self.assertTrue(ds.JOB_KEY.startswith('/System/Library/LaunchDaemons/org.pixelios.'), 'a bare label is ignored by launchd')

    def test_refuses_base_that_has_the_job(self):
        pl = base_plist()
        ds.add_job(pl)
        with self.assertRaises(ds.Err):
            ds.add_job(pl)
        pl2 = base_plist()
        pl2['LaunchDaemons']['/other/path.plist'] = {'Label': 'org.pixelios.devd'}
        with self.assertRaises(ds.Err):
            ds.add_job(pl2)

    def test_refuses_non_cache_plist(self):
        with self.assertRaises(ds.Err):
            ds.add_job({'Foo': 1})


class Key(Base):
    def test_key_file_mode_length_hex_and_two_calls_differ(self):
        keys = []
        for i in (1, 2):
            tree, copy = os.path.join(self.d, 't%d' % i), os.path.join(self.d, 'k%d/logs/devd/devd.key' % i)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                ds.make_key(tree, copy)
            for p in (os.path.join(tree, 'private/var/dev/devd.key'), copy):
                self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
                text = open(p).read()
                self.assertRegex(text, r'\A[0-9a-f]{64}\n\Z')
            self.assertEqual(open(os.path.join(tree, 'private/var/dev/devd.key')).read(), open(copy).read())
            keys.append(open(copy).read().strip())
            self.assertNotIn(keys[-1], out.getvalue(), 'the key is never printed')
        self.assertNotEqual(keys[0], keys[1])

    def test_existing_loose_file_is_tightened(self):
        p = os.path.join(self.d, 'devd.key')
        open(p, 'w').write('old\n')
        os.chmod(p, 0o644)
        ds.write_secret(p, 'new\n')
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(open(p).read(), 'new\n')

    def test_kernel_root_is_the_clone_not_the_worktree_git_dir(self):
        r = ds.kernel_root()
        self.assertTrue(os.path.isdir(os.path.join(r, '.git')), r)


class Pack(Base):
    def test_mkdevoverlay_accepts_the_tree(self):
        tree = os.path.join(self.d, 'tree')
        os.makedirs(os.path.join(tree, 'usr/local/bin'))
        open(os.path.join(tree, 'usr/local/bin/devd'), 'wb').write(b'\xcf\xfa\xed\xfe' + b'\0' * 100)
        os.chmod(os.path.join(tree, 'usr/local/bin/devd'), 0o755)
        ds.make_key(tree, os.path.join(self.d, 'k/devd.key'))
        ds.stage_plist(self.write_base(), tree)
        out = os.path.join(self.d, 'o.bin')
        ents, _ = ds.pack(tree, out)
        parsed = mk.parse(open(out, 'rb').read())
        modes = {e[0]: (e[1], e[2], e[3], e[5]) for e in parsed}
        self.assertEqual(modes, {'/System/Library/xpc/launchd.plist': (0o644, 0, 0, True),
                                 '/private/var/dev/devd.key': (0o600, 0, 0, True),
                                 '/usr/local/bin/devd': (0o755, 0, 0, True)})
        self.assertEqual(len(ents), 3)

    def test_oversize_overlay_is_a_fail(self):
        tree = os.path.join(self.d, 'tree')
        os.makedirs(os.path.join(tree, 'usr/local/bin'))
        open(os.path.join(tree, 'usr/local/bin/devd'), 'wb').write(os.urandom(21 << 20))
        with self.assertRaises(ds.Fail):
            ds.pack(tree, os.path.join(self.d, 'o.bin'))

    def test_path_outside_allowlist_is_a_fail(self):
        tree = os.path.join(self.d, 'tree')
        os.makedirs(os.path.join(tree, 'etc'))
        open(os.path.join(tree, 'etc/x'), 'w').write('x')
        with self.assertRaises(ds.Fail):
            ds.pack(tree, os.path.join(self.d, 'o.bin'))


class Checks(Base):
    def test_pac_filter_looks_at_mnemonics_only(self):
        sym = '1000: 1f 20 03 d5 \tnop'
        self.assertFalse(ds.BAD_INSN.search(sym.split('\t')[1]))
        for m in ('pacibsp', 'retaa', 'autiasp', 'ldapur x0, [x1]', 'stlur x0, [x1]', 'xpaci x0'):
            self.assertTrue(ds.BAD_INSN.search(m), m)
        self.assertFalse(ds.BAD_INSN.search('stp\tx28, x27, [sp, #-96]!'))

    def test_missing_toolchain_is_loud_error_2(self):
        old = {k: os.environ.get(k) for k in ('DEVD_THEOS_BIN', 'DEVD_SDK')}
        os.environ['DEVD_THEOS_BIN'] = os.path.join(self.d, 'nope')
        os.environ['DEVD_SDK'] = os.path.join(self.d, 'nosdk')
        try:
            self.assertIn('missing:', ds.toolchain_missing())
            rc, out, _ = self.run_main(['--out-dir', os.path.join(self.d, 'o'), '--key-copy', os.path.join(self.d, 'k')])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(rc, 2)
        self.assertRegex(out, r'(?m)^RESULT: ERROR devd-stage: cannot build, missing:')
        self.assertFalse(os.path.exists(os.path.join(self.d, 'k')), 'no key written when the build cannot run')

    def test_missing_launchd_base_file_is_error_2(self):
        rc, out, _ = self.run_main(['--launchd-base', os.path.join(self.d, 'absent.plist'), '--out-dir', self.d, '--key-copy', os.path.join(self.d, 'k')])
        self.assertEqual(rc, 2)
        self.assertIn('RESULT: ERROR', out)


class RealBuild(Base):
    def test_build_sign_stage_pack_without_launchd_base(self):
        self.need_toolchain()
        key_copy = os.path.join(self.d, 'logs/devd/devd.key')
        rc, out, err = self.run_main(['--out-dir', os.path.join(self.d, 'o'), '--key-copy', key_copy])
        self.assertEqual(rc, 0, out + err)
        self.assertIn('SWEET GAP: devd-stage: no launchd base given, boot job NOT staged', out)
        self.assertRegex(out, r'(?m)^RESULT: PASS devd-stage files=2 launchd=NOT-staged ')
        key = open(key_copy).read().strip()
        self.assertNotIn(key, out + err)
        # review devd-r1 F10: the packed archive holds the key, so it is private even under a host umask of 0002
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, 'o/devd-overlay.bin')).st_mode), 0o600)
        ents = mk.parse(open(os.path.join(self.d, 'o/devd-overlay.bin'), 'rb').read())
        self.assertEqual(sorted(e[0] for e in ents), ['/private/var/dev/devd.key', '/usr/local/bin/devd'])
        self.assertEqual(open(os.path.join(self.d, 'o/tree/private/var/dev/devd.key')).read().strip(), key)
        binp = os.path.join(self.d, 'o/tree/usr/local/bin/devd')
        self.assertEqual(stat.S_IMODE(os.stat(binp).st_mode), 0o755)
        # the packed binary is the signed one: ldid sees Identifier=devd and the adhoc flag
        tc = ds.toolchain()
        lh = subprocess.run([tc['ldid'], '-h', binp], capture_output=True, text=True).stdout
        self.assertIn('Identifier=devd', lh)
        self.assertRegex(lh, r'flags=0x[0-9a-f]+\(adhoc\)')

    def test_build_with_launchd_base_stages_plist(self):
        self.need_toolchain()
        rc, out, err = self.run_main(['--out-dir', os.path.join(self.d, 'o'), '--key-copy', os.path.join(self.d, 'k/devd.key'),
                                      '--launchd-base', self.write_base()])
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn('SWEET GAP: devd-stage: no launchd base', out)
        self.assertRegex(out, r'(?m)^RESULT: PASS devd-stage files=3 launchd=staged ')
        ents = {e[0]: e for e in mk.parse(open(os.path.join(self.d, 'o/devd-overlay.bin'), 'rb').read())}
        jobs = plistlib.loads(ents['/System/Library/xpc/launchd.plist'][4])['LaunchDaemons']
        self.assertIn(ds.JOB_KEY, jobs)

    def test_second_call_makes_a_new_key(self):
        self.need_toolchain()
        k = os.path.join(self.d, 'k/devd.key')
        keys = []
        for _ in (1, 2):
            rc, out, err = self.run_main(['--out-dir', os.path.join(self.d, 'o'), '--key-copy', k])
            self.assertEqual(rc, 0, out + err)
            keys.append(open(k).read())
        self.assertNotEqual(keys[0], keys[1])

    def test_compile_error_is_fail_1(self):
        self.need_toolchain()
        bad = os.path.join(self.d, 'bad.c')
        open(bad, 'w').write('int main(void) { return undefined_symbol_xyz; }\n')
        rc, out, _ = self.run_main(['--src', bad, '--out-dir', os.path.join(self.d, 'o'), '--key-copy', os.path.join(self.d, 'k')])
        self.assertEqual(rc, 1)
        self.assertRegex(out, r'(?m)^RESULT: FAIL clang failed rc')
        self.assertFalse(os.path.exists(os.path.join(self.d, 'k')), 'no key written after a failed build')


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    skipped = len(res.skipped)
    bad = len(res.failures) + len(res.errors)
    print('RESULT: %s tests=%d failures=%d errors=%d skipped=%d' % ('FAIL' if bad else 'PASS', res.testsRun, len(res.failures), len(res.errors), skipped))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
