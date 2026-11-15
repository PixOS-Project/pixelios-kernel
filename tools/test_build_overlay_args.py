"""--overlay argument handling of tools/build-xnu-image.sh and tools/xrun (no build, no phone)."""
import atexit, os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkpayload

SH = os.path.join(HERE, 'build-xnu-image.sh')


def build(*a):
    env = dict(os.environ, XNU_IMAGE_ARGS_ONLY='1')
    return subprocess.run(['bash', SH, *a], capture_output=True, text=True, env=env)


def overlay(rel='usr/lib/x.dylib'):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    p = os.path.join(top, rel); os.makedirs(os.path.dirname(p))
    with open(p, 'wb') as f:
        f.write(b'x')
    return top


class OverlayArgs(unittest.TestCase):
    def test_mif_clk_reserves_shim_token_room(self):     # ALL-ON 2026-11-11: all_on1 991 B lost the MIF request (shim/mifclk.c MIF_TOK_MAX 128)
        base = build('--mif-clk'); self.assertEqual(base.returncode, 0, base.stderr)
        n = len(base.stdout.split('boot-args: ', 1)[1].strip())
        pad = lambda k: 'sweetpad=' + 'x' * (k - len(' sweetpad='))
        M = (mkpayload.BOOT_LINE_LENGTH - 1) - 128   # 1919 with xnu 0320
        ok = build('--mif-clk', '--args', pad(M - n)); self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(len(ok.stdout.split('boot-args: ', 1)[1].strip()), M)
        over = build('--mif-clk', '--args', pad(M + 1 - n)); self.assertEqual(over.returncode, 2, over.stdout)
        self.assertIn('limit is %d' % M, over.stderr); self.assertIn('drop 1 bytes', over.stderr)
        nomif = build('--args', pad(M + 1 - n)); self.assertEqual(nomif.returncode, 0, nomif.stderr)   # without --mif-clk the full line stays usable

    def test_mif_clk_level_implies_mif_clk_and_keeps_the_room(self):    # ALL-ON T-02: --mif-clk-level KHZ is --mif-clk with another row: same line limit, value handed to build.sh
        base = build('--mif-clk'); n = len(base.stdout.split('boot-args: ', 1)[1].strip())
        pad = lambda k: 'sweetpad=' + 'x' * (k - len(' sweetpad='))
        lv = build('--mif-clk-level', '1716000'); self.assertEqual(lv.returncode, 0, lv.stderr)
        self.assertIn('--mif-clk --mif-clk-level 1716000', lv.stdout)
        M = (mkpayload.BOOT_LINE_LENGTH - 1) - 128
        ok = build('--mif-clk-level', '1716000', '--args', pad(M - n)); self.assertEqual(ok.returncode, 0, ok.stderr)
        over = build('--mif-clk-level', '1716000', '--args', pad(M + 1 - n)); self.assertEqual(over.returncode, 2, over.stdout); self.assertIn('limit is %d' % M, over.stderr)
        self.assertNotEqual(build('--mif-clk-level').returncode, 0)    # a value is required

    def test_pmic_src_reserves_shim_token_room(self):    # ALL-ON T-02/T-24: the shim appends up to 50 B of sweetps* (after up to 101 B of sweetmif*), never truncates
        N = (mkpayload.BOOT_LINE_LENGTH - 1)   # 2047 with xnu 0320
        for flags, limit in ((('--pmic-src',), N - 50), (('--pmic-src', '--mif-clk'), N - 101 - 50), (('--pmic-src-clear',), N - 79), (('--pmic-src-clear', '--mif-clk'), N - 101 - 79)):    # --pmic-src-clear adds " sweetpsclr=NN" (14 B)
            base = build(*flags); self.assertEqual(base.returncode, 0, base.stderr)
            n = len(base.stdout.split('boot-args: ', 1)[1].strip())
            pad = lambda k: 'sweetpad=' + 'x' * (k - len(' sweetpad='))
            ok = build(*flags, '--args', pad(limit - n)); self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(len(ok.stdout.split('boot-args: ', 1)[1].strip()), limit)
            over = build(*flags, '--args', pad(limit + 1 - n)); self.assertEqual(over.returncode, 2, over.stdout)
            self.assertIn('limit is %d' % limit, over.stderr); self.assertIn('drop 1 bytes', over.stderr)

    def test_dvfs_reserves_shim_token_room_and_needs_print_patch(self):    # L-28 / xnu 0367: the shim appends up to 224 B of sweetdvfs* LAST; a kernel without sweetdvfsrc would drop them
        N = (mkpayload.BOOT_LINE_LENGTH - 1)
        d = tempfile.mkdtemp(); atexit.register(shutil.rmtree, d, True)
        kp = os.path.join(d, 'k'); open(kp, 'wb').write(b'sweetpanichold\0sweetdvfsrc\0sweetps10')
        pad = lambda k: 'sweetpad=' + 'x' * (k - len(' sweetpad='))
        for flags, limit in (((), N - 224), (('--mif-clk',), N - 224 - 128), (('--pmic-src',), N - 224 - 51)):
            base = build('--dvfs', '1803000,0,1826000', '--kernel', kp, *flags); self.assertEqual(base.returncode, 0, base.stderr)
            n = len(base.stdout.split('boot-args: ', 1)[1].strip())
            ok = build('--dvfs', '1803000,0,1826000', '--kernel', kp, *flags, '--args', pad(limit - n - 0)); self.assertEqual(ok.returncode, 0, ok.stderr)
            over = build('--dvfs', '1803000,0,1826000', '--kernel', kp, *flags, '--args', pad(limit + 1 - n)); self.assertEqual(over.returncode, 2, over.stdout)
            self.assertIn('limit is %d' % limit, over.stderr)
        no = os.path.join(d, 'k0'); open(no, 'wb').write(b'sweetpanichold')
        r = build('--dvfs', '1803000,0,1826000', '--kernel', no); self.assertEqual(r.returncode, 2, r.stdout); self.assertIn('sweetdvfsrc', r.stderr)
        self.assertEqual(build('--dvfs', 'read', '--kernel', no).returncode, 0)    # read-only dvfsr: no tokens, no check
        self.assertEqual(build('--kernel', no).returncode, 0)

    def test_pmic_flags_refuse_kernel_without_print_patch(self):    # review 0319 F1: kernel without sweetps10 / sweetpsclr -> refused; with -> ok
        d = tempfile.mkdtemp(); atexit.register(shutil.rmtree, d, True)
        def kern(name, *strs):
            p = os.path.join(d, name)
            with open(p, 'wb') as f:
                f.write(b'\0'.join(x.encode() for x in ('sweetpanichold',) + strs))
            return p
        none, p318, p319 = kern('k0'), kern('k1', 'sweetps10'), kern('k2', 'sweetps10', 'sweetpsclr')
        r = build('--kernel', none, '--pmic-src'); self.assertEqual(r.returncode, 2, r.stdout); self.assertIn('sweetps10', r.stderr)
        self.assertEqual(build('--kernel', p318, '--pmic-src').returncode, 0)
        r = build('--kernel', p318, '--pmic-src-clear'); self.assertEqual(r.returncode, 2, r.stdout); self.assertIn('sweetpsclr', r.stderr)
        r = build('--kernel', none, '--pmic-src-clear'); self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(build('--kernel', p319, '--pmic-src-clear').returncode, 0)
        self.assertEqual(build('--kernel', none).returncode, 0)    # no flag, no check

    def test_no_overlay_no_sweetdev(self):
        r = build(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('sweetdev', r.stdout)

    def test_overlay_adds_sweetdev_and_fits_budget(self):
        r = build('--overlay', overlay(), '--rw', '--usbnet', '--gui', '--restart', '120,bootloader'); self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.split('boot-args: ', 1)[1].strip()
        self.assertTrue(line.endswith('sweetdev=1'), line)
        mkpayload.check_cmdline(line)                      # the 2047-byte budget (exits when over)

    def test_over_limit_line_refused(self):   # review 0320 F5: --print-bootargs (args-only) refuses what mkpayload would refuse
        r = build('--args', 'x=' + 'p' * (mkpayload.BOOT_LINE_LENGTH + 10))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('REFUSED: boot-args are', r.stderr)
        self.assertIn('boot-args: ', r.stdout)                # the line is still printed (run-preflight reports its length)

    def test_restore_in_args_is_not_doubled(self):
        r = build('--overlay', overlay(), '--args', 'sweetdev=restore'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('sweetdev='), 1); self.assertIn('sweetdev=restore', r.stdout)

    def test_default_rd_kept_without_user_rd(self):
        r = build('--args', 'foo=1'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('rd='), 1)
        self.assertIn('rd=ufsroot0', r.stdout)

    def test_user_rd_replaces_default(self):
        r = build('--args', 'rd=usbroot0'); self.assertEqual(r.returncode, 0, r.stderr)
        toks = [w for w in r.stdout.split() if w.startswith('rd=')]
        self.assertEqual(toks, ['rd=usbroot0'], r.stdout)

    def test_rd_substring_and_explicit_default(self):  # review r75-split-r1 F2: only a whole rd= token replaces the default
        r = build('--args', 'xrd=1'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([w for w in r.stdout.split() if w.startswith('rd=')], ['rd=ufsroot0'], r.stdout)
        self.assertIn('xrd=1', r.stdout.split())
        r = build('--args', 'rd=ufsroot0'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([w for w in r.stdout.split() if w.startswith('rd=')], ['rd=ufsroot0'], r.stdout)

    def test_two_user_rd_refused(self):  # review r75-split-r1 F1
        r = build('--args', 'rd=a rd=b')
        self.assertEqual(r.returncode, 2, r.stderr); self.assertIn('2 rd= tokens', r.stderr)
        self.assertNotIn('boot-args', r.stdout)

    def test_bad_overlay_stops_before_any_build(self):
        r = build('--overlay', overlay('etc/passwd'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('not allowed on the phone', r.stderr)
        self.assertNotIn('boot-args', r.stdout)

    def test_overlay_refuses_ufsroot_arg(self):          # review 0117 F1
        for a in ('ufsroot=vendor_boot', 'sweetdev=1 ufsroot=x'):
            r = build('--overlay', overlay(), '--args', a)
            self.assertEqual(r.returncode, 2, a); self.assertIn('ufsroot=', r.stderr); self.assertNotIn('boot-args', r.stdout)
        self.assertEqual(build('--args', 'ufsroot=userdata').returncode, 0)   # no overlay: unchanged

    def test_default_passes_mkdevoverlay_args_unchanged(self):   # review devoverlay-z-1 round 2 F2: no --compress = version 1 args
        # fake python3 first on PATH records the argv of the mkdevoverlay call (the args-only hook's mkpayload length check, review 0320
        # F5, is a later python3 call: it is not recorded and passes)
        fake = tempfile.mkdtemp(); atexit.register(shutil.rmtree, fake, True)
        log = os.path.join(fake, 'argv')
        with open(os.path.join(fake, 'python3'), 'w') as f:
            f.write('#!/bin/sh\n[ "$2" = tools/mkdevoverlay.py ] || exit 0\nprintf "%%s\\n" "$@" > "%s"\n' % log)
        os.chmod(os.path.join(fake, 'python3'), 0o755)
        top = overlay(); env = dict(os.environ, XNU_IMAGE_ARGS_ONLY='1', PATH=fake + os.pathsep + os.environ['PATH'])
        r = subprocess.run(['bash', SH, '--overlay', top], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(log) as f:
            argv = f.read().split('\n')[:-1]
        self.assertEqual(len(argv), 4, argv)                      # -I tools/mkdevoverlay.py SRC OUT, nothing else
        self.assertEqual(argv[:3], ['-I', 'tools/mkdevoverlay.py', top])
        self.assertNotIn('--compress', argv)

    def test_overlay_build_runs_under_umask_077(self):     # review devd-r1 F10: the overlay may hold the devd key
        fake = tempfile.mkdtemp(); atexit.register(shutil.rmtree, fake, True)
        log = os.path.join(fake, 'umask')
        with open(os.path.join(fake, 'python3'), 'w') as f:
            f.write('#!/bin/sh\numask > "%s"\n' % log)
        os.chmod(os.path.join(fake, 'python3'), 0o755)
        env = dict(os.environ, XNU_IMAGE_ARGS_ONLY='1', PATH=fake + os.pathsep + os.environ['PATH'])
        old = os.umask(0o002)
        try:
            r = subprocess.run(['bash', SH, '--overlay', overlay()], capture_output=True, text=True, env=env)
        finally:
            os.umask(old)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(log) as f:
            self.assertEqual(f.read().strip().lstrip('0'), '77')

    def test_compress_needs_overlay(self):
        r = build('--compress')
        self.assertEqual(r.returncode, 2, r.stderr); self.assertIn('--compress needs --overlay', r.stderr)
        self.assertNotIn('boot-args', r.stdout)

    def test_docs_have_failon_and_recovery(self):         # review 0117 F2-F5 (doc conditions)
        d = open(os.path.join(HERE, '..', 'docs', 'AUTOMATION.md')).read()
        for k in ('mounting dirty non-journaled root', 'two renames', '.sweetorig` holds the previous overlay version', 'mode or owner'):
            self.assertIn(k, d)

    def test_missing_overlay_dir(self):
        self.assertNotEqual(build('--overlay', '/nonexistent-dir').returncode, 0)

    def test_xrun_passes_overlay_through(self):
        src = open(os.path.join(HERE, 'xrun')).read()
        self.assertIn('--overlay) overlay=$2; shift 2', src)
        self.assertIn('${overlay:+--overlay "$overlay"}', src)

    def test_xrun_compress_reaches_build_only_with_overlay(self):   # T-b4f9: xrun --compress is accepted and forwarded next to --overlay
        stub = os.path.join(tempfile.mkdtemp(), 'bxi-stub'); atexit.register(shutil.rmtree, os.path.dirname(stub), True)
        with open(stub, 'w') as f:
            f.write('#!/bin/bash\necho "BXI-ARGV: $*"\n')
        os.chmod(stub, 0o755)
        lock = tempfile.mkdtemp(); atexit.register(shutil.rmtree, lock, True)   # sim seams need a sandbox lock dir under $TMPDIR (AP-299)
        env = dict(os.environ, XRUN_BUILD=stub, PHONE_LOCK_DIR=lock, XRUN_NO_RUNPLAN='overlay host test, not a run')

        def xrun(*a):
            return subprocess.run([os.path.join(HERE, 'xrun'), '--print-bootargs', *a], capture_output=True, text=True, env=env, cwd=os.path.dirname(HERE))
        r = xrun('--overlay', '/ov', '--compress')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'BXI-ARGV: .*--overlay /ov --compress')
        r = xrun('--overlay', '/ov')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertNotIn('--compress', r.stdout)
        r = xrun('--compress')                       # no --overlay: the real build-xnu-image.sh refuses (test_compress_needs_overlay)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('BXI-ARGV:', r.stdout); self.assertIn('--compress', r.stdout)
        self.assertNotIn('--overlay', r.stdout)


if __name__ == '__main__':
    unittest.main()
