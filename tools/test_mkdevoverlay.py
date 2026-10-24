import atexit, os, shutil, struct, subprocess, sys, tempfile, unittest, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkdevoverlay as m

PACK = os.path.join(HERE, 'mkdevoverlay.py')


def run(*a):
    return subprocess.run([sys.executable, '-I', PACK, *a], capture_output=True, text=True)


def tree(files):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    d = os.path.join(top, 't'); os.mkdir(d)
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)
    return d


class RoundTrip(unittest.TestCase):
    def test_round_trip_and_deterministic(self):
        d = tree({'usr/lib/libx.dylib': b'x' * 1000, 'private/var/dev/hello': b'hi', 'usr/local/a/b': b''})
        os.chmod(os.path.join(d, 'private/var/dev/hello'), 0o755)
        out = os.path.join(d, '..', 'o1.bin'), os.path.join(d, '..', 'o2.bin')
        for o in out:
            r = run(d, o, '--owner', '501:20'); self.assertEqual(r.returncode, 0, r.stderr)
        a, b = (open(o, 'rb').read() for o in out)
        self.assertEqual(a, b)
        ents = m.parse(a + b'\0' * 4096)                  # trailing bytes (RAM disk page padding) are ignored
        self.assertEqual([e[0] for e in ents], ['/private/var/dev/hello', '/usr/lib/libx.dylib', '/usr/local/a/b'])
        self.assertEqual(ents[0][1:], (0o755, 501, 20, b'hi', True))
        self.assertTrue(all(e[5] for e in ents))
        self.assertEqual(len(a) % 16, 0)

    def test_bad_crc_flags_entry_only(self):
        blob = bytearray(m.pack([('/usr/lib/a', 0o644, 0, 0, b'abc')]))
        blob[-16] ^= 1                                    # data starts at 16-byte boundary before the final pad
        ents = m.parse(bytes(blob))
        self.assertFalse(ents[0][5])


class Rejects(unittest.TestCase):
    good = m.pack([('/usr/lib/a', 0o644, 0, 0, b'abc')])

    def bad(self, blob):
        with self.assertRaises(ValueError):
            m.parse(blob)

    def test_magic(self): self.bad(b'SWEETDEX' + self.good[8:])
    def test_version(self): self.bad(self.good[:8] + struct.pack('<I', 2) + self.good[12:])
    def test_count_too_big(self): self.bad(self.good[:12] + struct.pack('<I', 5000) + self.good[16:])
    def test_count_past_end(self): self.bad(self.good[:12] + struct.pack('<I', 2) + self.good[16:])
    def test_truncated(self):
        self.bad(self.good[:40])
        self.bad(self.good[:10])

    def test_size_overflow(self):
        b = bytearray(self.good); b[16 + 16:16 + 24] = struct.pack('<Q', 2**64 - 1); self.bad(bytes(b))

    def test_size_just_over(self):
        b = bytearray(self.good); b[16 + 16:16 + 24] = struct.pack('<Q', 10000); self.bad(bytes(b))

    def test_pathlen_zero_and_huge(self):
        for v in (0, 1001, 65535):
            b = bytearray(self.good); b[16:18] = struct.pack('<H', v); self.bad(bytes(b))

    def test_paths(self):
        for p in ('/usr/lib/../../etc/x', '/usr/lib/./x', '/usr/lib//x', '/usr/lib/', '/usr/lib', 'usr/lib/x', '/etc/passwd',
                  '/usr/libx/y', '/usr/lib/x\n', '/usr/lib/a.sweetorig', '/usr/lib/a.sweettmp', '/System/Library/xpc/launchd.plist/x',
                  '/System/Library/LaunchDaemons/org.pixelios.d/x', '/System/Library/LaunchDaemons/com.apple.x.plist', '/private/var/dev'):
            self.assertIsNotNone(m.path_check(p), p)
        for p in ('/usr/lib/libsweetgl.dylib', '/usr/local/x/y', '/private/var/dev/hello', '/System/Library/xpc/launchd.plist',
                  '/System/Library/LaunchDaemons/org.pixelios.t.plist'):
            self.assertIsNone(m.path_check(p), p)

    def test_packer_refuses_bad_path(self):
        d = tree({'etc/passwd': b'x'})
        r = run(d, os.path.join(d, 'o.bin'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('not allowed on the phone', r.stderr)

    def test_packer_refuses_symlink(self):
        d = tree({'usr/lib/a': b'x'})
        os.symlink('/etc', os.path.join(d, 'usr/lib/link'))
        r = run(d, os.path.join(d, 'o.bin'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('symlink', r.stderr)

    def test_packer_refuses_over_40mib(self):
        d = tree({'usr/lib/a': b'', })
        with open(os.path.join(d, 'usr/lib/a'), 'wb') as f:
            f.truncate(41 << 20)
        r = run(d, os.path.join(d, 'o.bin'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('40 MiB', r.stderr)


    def test_control_bytes_case_and_long_names(self):
        for p in ('/usr/lib/a\x7fb', '/usr/lib/a\x1fb', '/usr/lib/\xe9', '/usr/lib/a.SweetOrig', '/usr/lib/a.SWEETTMP', '/usr/lib/' + 'n' * 246):
            self.assertIsNotNone(m.path_check(p), repr(p))
        self.assertIsNone(m.path_check('/usr/lib/' + 'n' * 245))

    def test_packer_refuses_case_collision(self):
        d = tree({'usr/local/Foo': b'1', 'usr/local/foo': b'2'})
        r = run(d, os.path.join(d, 'o.bin'))
        self.assertNotEqual(r.returncode, 0); self.assertIn('only by case', r.stderr)

    def test_count_4097_rejected(self):
        blob = m.pack([('/usr/lib/a', 0o644, 0, 0, b'')] * 4096)
        self.assertEqual(len(m.parse(blob)), 4096)
        self.bad(blob[:12] + struct.pack('<I', 4097) + blob[16:])
        with self.assertRaises(ValueError):
            m.pack([('/usr/lib/a', 0o644, 0, 0, b'')] * 4097)

    def test_setuid_mode_is_masked(self):
        d = tree({'usr/lib/a': b'x'}); os.chmod(os.path.join(d, 'usr/lib/a'), 0o4755)
        o = os.path.join(d, '..', 'o.bin'); self.assertEqual(run(d, o).returncode, 0)
        with open(o, 'rb') as f:
            self.assertEqual(m.parse(f.read())[0][1], 0o755)

    def test_packer_refuses_fifo_without_opening(self):
        d = tree({'usr/lib/a': b'x'}); os.mkfifo(os.path.join(d, 'usr/lib/pipe'))
        r = subprocess.run([sys.executable, '-I', PACK, d, os.path.join(d, '..', 'o.bin')], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0); self.assertIn('not a regular file', r.stderr)

    def test_owner_warning(self):
        d = tree({'usr/lib/a': b'x'})
        r = run(d, os.path.join(d, '..', 'o.bin'), '--owner', '501:501')
        self.assertEqual(r.returncode, 0); self.assertIn('WARNING', r.stderr)


if __name__ == '__main__':
    unittest.main()
