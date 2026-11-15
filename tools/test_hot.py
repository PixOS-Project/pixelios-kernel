#!/usr/bin/env python3
"""test_hot.py: host tests for tools/hot (plist merge, shared key, lock token, pixps parse) and a loopback run of
hot ping/push/run/ps/restart/swap against a gcc-built devd --test-mode. No phone, no toolchain needed."""
import importlib.machinery, importlib.util, json, os, plistlib, socket, stat, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOT = os.path.join(HERE, 'hot')


def load_hot():
    loader = importlib.machinery.SourceFileLoader('hot_mod', HOT)
    spec = importlib.util.spec_from_loader('hot_mod', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hot = load_hot()


def cache(*labels):
    return {'LaunchDaemons': {'/System/Library/LaunchDaemons/%s.plist' % l: {'Label': l} for l in labels}}


class Merge(unittest.TestCase):
    def test_adds_devd_and_drops(self):
        pl = cache('com.apple.a', 'org.pixelios.pixopen')
        info = hot.merge_plist(pl, ['org.pixelios.pixopen'])
        labels = sorted(v['Label'] for v in pl['LaunchDaemons'].values())
        self.assertEqual(labels, ['com.apple.a', 'org.pixelios.devd'])
        job = pl['LaunchDaemons'][hot.ds.JOB_KEY]
        self.assertEqual(job['Program'], '/usr/local/bin/devd')
        self.assertTrue(job['RunAtLoad'])
        self.assertNotIn('KeepAlive', job)
        self.assertTrue(any('dropped job org.pixelios.pixopen' in l for l in info))

    def test_keeps_existing_devd_once(self):
        pl = cache('com.apple.a')
        hot.merge_plist(pl, [])
        info = hot.merge_plist(pl, [])
        self.assertEqual(sum(v.get('Label') == 'org.pixelios.devd' for v in pl['LaunchDaemons'].values()), 1)
        self.assertTrue(any('already in the plist' in l for l in info))

    def test_absent_drop_is_a_gap(self):
        info = hot.merge_plist(cache('com.apple.a'), ['org.nope'])
        self.assertTrue(any(l.startswith('SWEET GAP:') and 'org.nope' in l for l in info))

    def test_not_a_cache(self):
        with self.assertRaises(hot.Err):
            hot.merge_plist({'Label': 'x'}, [])


class Key(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_create_then_reuse(self):
        p = os.path.join(self.d, 'k')
        a = hot.shared_key(p)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(hot.shared_key(p), a)
        self.assertRegex(a, r'^[0-9a-f]{64}\n$')

    def test_loose_mode_refused(self):
        p = os.path.join(self.d, 'k')
        hot.shared_key(p)
        os.chmod(p, 0o644)
        with self.assertRaises(hot.Err):
            hot.shared_key(p)

    def test_bad_content_refused(self):
        p = os.path.join(self.d, 'k')
        hot.ds.write_secret(p, 'nothex\n')
        with self.assertRaises(hot.Err):
            hot.shared_key(p)


class Lock(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def write(self, **kw):
        info = {'owner': 'gpu', 'token': 'T0K', 'pid': os.getpid()}
        info.update(kw)
        with open(os.path.join(self.d, 'info.json'), 'w') as f:
            json.dump(info, f)

    def test_free(self):
        with self.assertRaises(hot.Fail):
            hot.lock_token('gpu', self.d)

    def test_own_lock(self):
        self.write()
        self.assertEqual(hot.lock_token('gpu', self.d), 'T0K')

    def test_other_owner_refused(self):
        self.write(owner='sprint-wifi')
        with self.assertRaisesRegex(hot.Fail, 'not .gpu.'):
            hot.lock_token('gpu', self.d)

    def test_dead_holder_refused(self):
        p = subprocess.Popen(['true'])
        p.wait()
        self.write(pid=p.pid)
        with self.assertRaisesRegex(hot.Fail, 'not alive'):
            hot.lock_token('gpu', self.d)


class PushDst(unittest.TestCase):
    def test_allowed(self):
        for d in ('/usr/local/bin/pixping', '/private/var/dev/x', '/usr/lib/libsweetgl.dylib'):
            hot.check_push_dst(d)

    def test_refused(self):
        for d in ('/usr/lib/libSystem.B.dylib', '/usr/lib/dyld', '/System/Library/xpc/launchd.plist', '/usr/lib/libsweetgl.dylib.x', '/tmp/x'):
            with self.assertRaises(hot.Fail, msg=d):
                hot.check_push_dst(d)

    def test_cli_refuses_before_lock(self):
        env = dict(os.environ)
        env.pop('DEVSH_TEST_LOOPBACK', None)
        p = subprocess.run([sys.executable, HOT, 'push', HOT, '/usr/lib/dyld'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn('push to /usr/lib/dyld refused', p.stdout)


class Ps(unittest.TestCase):
    def test_parse(self):
        out = 'RUNID x\n1 0 0 launchd\n52 1 501 backboardd\n77 1 0 a name with spaces\nRESULT: PASS run rc=0\n'
        self.assertEqual(hot.parse_ps(out, 'backboardd'), [(52, 'backboardd')])
        self.assertEqual(len(hot.parse_ps(out)), 3)
        # p_comm holds 16 chars (MAXCOMLEN): a long name matches its first 16
        self.assertEqual(hot.parse_ps('9 1 0 com.apple.Prefer\n', 'com.apple.Preferences'), [(9, 'com.apple.Prefer')])


class Session(unittest.TestCase):
    """hot session start builds ONE detached, held boot (no --expect, --max-derived duration/hard-timeout/restart, sweetnull devd)."""
    def test_start_command(self):
        h = load_hot()
        d = tempfile.mkdtemp()
        k, ov = os.path.join(d, "kernel"), os.path.join(d, "ov")
        open(k, "w").close(); os.makedirs(ov)
        seen = {}
        h.cmd_stage = lambda a: seen.setdefault("stage", a.into) and 0
        class R: returncode = 0
        def run(cmd, **kw):
            seen["cmd"] = cmd
            return R()
        from unittest import mock
        with mock.patch.object(h.subprocess, "run", run):   # h.subprocess IS the global module: restore it (it broke later test files)
            rc = h.main(["session", "start", "--owner", "sprint-gpu", "--overlay", ov, "--kernel", k, "--intent", "gpu", "--max", "3600",
                         "--names", "fcapctl", "--compress"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["stage"], ov)
        c = seen["cmd"]
        self.assertIn("--detach", c)
        self.assertEqual(c[c.index("--max") + 1], "3600")
        x = c[c.index("--") + 1:]
        for flag, val in (("--duration", "3420"), ("--hard-timeout", "3480"), ("--restart", "3540,bootloader"), ("--intent", "gpu"),
                          ("--label", "hot-sprint-gpu"), ("--args", "sweetnull=devd,fcapctl")):
            self.assertEqual(x[x.index(flag) + 1], val, flag)
        self.assertNotIn("--expect", x)
        self.assertIn("--compress", x)
        self.assertIn("--no-camera", x)

    def test_max_bounds(self):
        h = load_hot()
        self.assertEqual(h.main(["session", "start", "--owner", "x", "--overlay", "/", "--kernel", "/", "--max", "9000"]), 1)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Loopback(unittest.TestCase):
    """hot -> devsh -> devd --test-mode on 127.0.0.1: the forwarded commands, ps parsing on real RUN output, restart, swap."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix='hot-test-', dir='/tmp')
        cls.devd = os.path.join(cls.work, 'devd')
        c = subprocess.run(['gcc', '-O1', '-o', cls.devd, os.path.join(HERE, 'devd.c')], capture_output=True, text=True)
        if c.returncode != 0:
            raise unittest.SkipTest('gcc devd failed: ' + c.stderr[-200:])
        cls.key = os.path.join(cls.work, 'devd.key')
        hot.shared_key(cls.key)
        cls.port = free_port()
        cls.log = open(os.path.join(cls.work, 'devd.log'), 'w')
        cls.proc = subprocess.Popen([cls.devd, '--port', str(cls.port), '--test-mode', '--key-file', cls.key],
                                    stdout=cls.log, stderr=subprocess.STDOUT)
        time.sleep(0.3)
        # stand-in for pixps (pixps.c needs Darwin sysctl): prints "PID PPID UID COMM" for the sleeper below
        cls.sleeper = subprocess.Popen(['sleep', '60'])
        cls.fake_ps = os.path.join(cls.work, 'pixps')
        with open(cls.fake_ps, 'w') as f:
            f.write('#!/bin/sh\n[ "$1" = sleep ] || { echo "pixps: no process named $1" >&2; exit 1; }\n'
                    'echo "%d 1 %d sleep"\n' % (cls.sleeper.pid, os.getuid()))
        os.chmod(cls.fake_ps, 0o755)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.proc, cls.sleeper):
            p.kill()
            p.wait()
        cls.log.close()

    def hot(self, *args):
        env = dict(os.environ, DEVSH_TEST_LOOPBACK='1', PIXOS_DEVD_HOST='127.0.0.1', PIXOS_DEVD_PORT=str(self.port),
                   DEVD_KEY_FILE=self.key, PHONE_LOCK_TOKEN='loopback', HOT_PIXPS=self.fake_ps)
        p = subprocess.run([sys.executable, HOT] + list(args), env=env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_1_ping(self):
        rc, out = self.hot('ping')
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: PASS ping', out)

    def test_2_push_run(self):
        src = os.path.join(self.work, 'hello.sh')
        with open(src, 'w') as f:
            f.write('#!/bin/sh\necho hot-hello "$@"\n')
        dst = os.path.join(self.work, 'pushed', 'hello')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        rc, out = self.hot('push', src, dst)
        self.assertEqual(rc, 0, out)
        rc, out = self.hot('run', dst, 'x')
        self.assertEqual(rc, 0, out)
        self.assertIn('hot-hello x', out)

    def test_3_ps_and_missing_restart(self):
        rc, out = self.hot('ps', 'sleep')
        self.assertEqual(rc, 0, out)
        self.assertIn('%d 1' % self.sleeper.pid, out)
        rc, out = self.hot('restart', 'nothere')
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: FAIL hot restart: no running process named nothere', out)

    def test_4_swap_restarts(self):
        src = os.path.join(self.work, 'sleep.bin')
        with open(src, 'w') as f:
            f.write('x\n')
        rc, out = self.hot('swap', src, os.path.join(self.work, 'pushed', 'sleep'))
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: PASS hot restart sleep pids=%d sig=15' % self.sleeper.pid, out)
        self.assertEqual(self.sleeper.wait(timeout=10), -15)

    def test_5_phone_cmd_needs_owner(self):
        env = dict(os.environ)
        env.pop('PHONE_LOCK_TOKEN', None)
        env.pop('HOT_OWNER', None)
        p = subprocess.run([sys.executable, HOT, 'ps'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1)
        self.assertIn('needs $PHONE_LOCK_TOKEN or --owner', p.stdout)


if __name__ == '__main__':
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.wasSuccessful()
    print('RESULT: %s test_hot %d run, %d failed, %d errors' % ('PASS' if ok else 'FAIL', r.testsRun, len(r.failures), len(r.errors)))
    sys.exit(0 if ok else 1)
