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
    def test_no_overlay_no_sweetdev(self):
        r = build(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('sweetdev', r.stdout)

    def test_overlay_adds_sweetdev_and_fits_budget(self):
        r = build('--overlay', overlay(), '--rw', '--usbnet', '--gui', '--restart', '120,bootloader'); self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.split('boot-args: ', 1)[1].strip()
        self.assertTrue(line.endswith('sweetdev=1'), line)
        mkpayload.check_cmdline(line)                      # the 607-byte budget (exits when over)

    def test_restore_in_args_is_not_doubled(self):
        r = build('--overlay', overlay(), '--args', 'sweetdev=restore'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('sweetdev='), 1); self.assertIn('sweetdev=restore', r.stdout)

    def test_bad_overlay_stops_before_any_build(self):
        r = build('--overlay', overlay('etc/passwd'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('not allowed on the phone', r.stderr)
        self.assertNotIn('boot-args', r.stdout)

    def test_overlay_refuses_ufsroot_arg(self):          # review 0117 F1
        for a in ('ufsroot=vendor_boot', 'sweetdev=1 ufsroot=x'):
            r = build('--overlay', overlay(), '--args', a)
            self.assertEqual(r.returncode, 2, a); self.assertIn('ufsroot=', r.stderr); self.assertNotIn('boot-args', r.stdout)
        self.assertEqual(build('--args', 'ufsroot=userdata').returncode, 0)   # no overlay: unchanged

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


if __name__ == '__main__':
    unittest.main()
