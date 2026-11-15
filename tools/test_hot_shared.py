#!/usr/bin/env python3
"""test_hot_shared.py: host tests for the SHARED hot session (tools/hot_shared.py, hot join/leave/session end, live-cut --owner).
Temp PHONE_LOCK_DIR, a fake live lock held by this process; no phone, no devd."""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOT = os.path.join(HERE, 'hot')
sys.path.insert(0, HERE)
import hot_shared as hs  # noqa: E402


def load(name, fname):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(HERE, fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hot-shared-')
        self.ld = os.path.join(self.tmp, 'phone.lock')
        self.env = dict(os.environ, PHONE_LOCK_DIR=self.ld)
        os.environ['PHONE_LOCK_DIR'] = self.ld
        self.kern = os.path.join(self.tmp, 'kernel')
        with open(self.kern, 'w') as f:
            f.write('kernel-A')
        self.kern2 = os.path.join(self.tmp, 'kernel2')
        with open(self.kern2, 'w') as f:
            f.write('kernel-B')
        self.runs = os.path.join(self.tmp, 'runs')
        os.makedirs(self.runs)

    def tearDown(self):
        os.environ.pop('PHONE_LOCK_DIR', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lock(self, owner='own'):
        os.makedirs(self.ld)
        with open(os.path.join(self.ld, 'info.json'), 'w') as f:
            json.dump({'owner': owner, 'token': 'TOK', 'pid': os.getpid(), 'start': int(time.time()) + 1}, f)

    def session(self, turn_max=600):
        hs.mark_shared('own', self.kern, self.tmp, 3600, turn_max, self.runs)
        self.lock()

    def hot(self, *args):
        p = subprocess.run([sys.executable, HOT] + list(args), env=self.env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr


class Join(Base):
    def test_refused_without_shared_session(self):
        rc, out = self.hot('join', '--owner', 'g1')
        self.assertEqual(rc, 1, out)
        self.assertIn('no shared hot session', out)
        self.assertIn('--shared', out)
        self.assertTrue(out.strip().splitlines()[-1].startswith('RESULT: FAIL'))

    def test_refused_when_lock_free_or_other_owner(self):
        hs.mark_shared('own', self.kern, self.tmp, 3600, 600, self.runs)
        rc, out = self.hot('join', '--owner', 'g1')
        self.assertEqual(rc, 1)
        self.assertIn('lock is free', out)
        self.lock('someone-else')
        rc, out = self.hot('join', '--owner', 'g1')
        self.assertEqual(rc, 1)
        self.assertIn('not by the shared session owner', out)

    def test_refused_on_kernel_mismatch_and_accepted_on_match(self):
        self.session()
        rc, out = self.hot('join', '--owner', 'g1', '--kernel', self.kern2)
        self.assertEqual(rc, 1, out)
        self.assertIn('differs from the session kernel', out)
        self.assertFalse(os.path.exists(os.path.join(self.ld + '.shared', 'guests', 'g1')))
        rc, out = self.hot('join', '--owner', 'g1', '--kernel', self.kern)
        self.assertEqual(rc, 0, out)
        self.assertIn('hot --owner g1 push', out)
        self.assertIn('RESULT: PASS hot join', out)

    def test_owner_cannot_join_own_session(self):
        self.session()
        rc, out = self.hot('join', '--owner', 'own', '--kernel', self.kern)
        self.assertEqual(rc, 1)
        self.assertIn('session owner', out)

    def test_guest_gets_owner_token_only_after_join(self):
        self.session()
        h = load('hot_t', 'hot')
        import argparse
        a = argparse.Namespace(owner='g1')
        with self.assertRaisesRegex(h.Fail, 'has not joined'):
            h.devsh_env(a, True)
        self.hot('join', '--owner', 'g1', '--kernel', self.kern)
        self.assertEqual(h.devsh_env(a, True)['PHONE_LOCK_TOKEN'], 'TOK')


class Turns(Base):
    def test_two_guests_serialize(self):
        self.session()
        for g in ('g1', 'g2'):
            hs.join(g, self.kern)
        order, lock = [], threading.Lock()

        def work(g, delay):
            time.sleep(delay)
            with hs.turn(g, 20, poll=0.05):
                with lock:
                    order.append(g + ' in')
                time.sleep(0.5)
                with lock:
                    order.append(g + ' out')
        ts = [threading.Thread(target=work, args=(g, d)) for g, d in (('g1', 0), ('g2', 0.15))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(order, ['g1 in', 'g1 out', 'g2 in', 'g2 out'])
        log = open(os.path.join(self.ld + '.shared', 'turns.log')).read()
        self.assertIn('TURN QUEUED', log)
        self.assertEqual(hs.status()[1]['holder'], None)

    def test_turn_cap_expiry_hands_over(self):
        self.session(turn_max=1)
        hs.join('g1', self.kern)
        hs.join('g2', self.kern)
        hs.take('g1', 5, pid=0)   # explicit turn, never released
        t0 = time.time()
        self.assertEqual(hs.take('g2', 10, poll=0.05), 'granted')
        self.assertGreaterEqual(time.time() - t0, 0.9)
        self.assertEqual(hs.status()[1]['holder'], 'g2')
        self.assertIn('TURN EXPIRED', open(os.path.join(self.ld + '.shared', 'turns.log')).read())
        with self.assertRaisesRegex(hs.Refused, 'does not hold the turn'):
            hs.release('g1')

    def test_wait_timeout_is_refused_not_hung(self):
        self.session()
        hs.take('own', 5)
        with self.assertRaisesRegex(hs.Refused, 'no turn within'):
            hs.take('g1', 0.3, poll=0.05)
        self.assertEqual(hs.status()[1]['queue'], [])

    def test_push_logged_with_who(self):
        self.session()
        hs.join('g1', self.kern)
        hs.note_push('g1', 'guest', 'push', ['/a', '/usr/local/bin/x'])
        rec = json.loads(open(os.path.join(self.ld + '.shared', 'pushes.log')).read().splitlines()[-1])
        self.assertEqual((rec['who'], rec['role'], rec['op']), ('g1', 'guest', 'push'))


class EndLeave(Base):
    def test_guest_cannot_end_session(self):
        self.session()
        hs.join('g1', self.kern)
        rc, out = self.hot('session', 'end', '--owner', 'g1')
        self.assertEqual(rc, 1, out)
        self.assertIn('cannot end the shared session of own', out)
        self.assertTrue(out.strip().splitlines()[-1].startswith('RESULT: FAIL'))
        self.assertTrue(os.path.isdir(self.ld))   # the lock is untouched

    def test_leave_cleans_up(self):
        self.session()
        hs.join('g1', self.kern)
        hs.join('g2', self.kern)
        hs.take('g1', 5)
        done = []
        t = threading.Thread(target=lambda: done.append(hs.take('g2', 5, poll=0.05)))
        t.start()
        time.sleep(0.3)
        rc, out = self.hot('leave', '--owner', 'g1')
        self.assertEqual(rc, 0, out)
        t.join()
        self.assertEqual(done, ['granted'])   # g1's turn was released by leave, g2 got it
        self.assertFalse(os.path.exists(os.path.join(self.ld + '.shared', 'guests', 'g1')))
        rc, out = self.hot('leave', '--owner', 'g1')
        self.assertEqual(rc, 1)
        self.assertIn('not a guest', out)

    def test_stale_marks_do_not_block_other_end(self):
        hs.mark_shared('own', self.kern, self.tmp, 3600, 600, self.runs)   # lock free: no active session
        self.assertIsNone(hs.active_or_none())


class LiveCut(Base):
    def test_cut_takes_and_hands_on_turn(self):
        self.session()
        hs.join('g1', self.kern)
        sess = os.path.join(self.runs, '20260101T000000Z-hot-own')
        os.makedirs(sess)
        with open(os.path.join(sess, 'usbnet.log'), 'w') as f:
            f.write('a\nb\n')
        lc = os.path.join(HERE, 'live-cut')
        env = dict(self.env, PYTHONDONTWRITEBYTECODE='1')

        def cut(*args):
            p = subprocess.run([sys.executable, lc] + list(args), env=env, capture_output=True, text=True, timeout=30, cwd=self.tmp)
            return p.returncode, p.stdout + p.stderr
        rc, out = cut('begin', 'it-1')
        self.assertEqual(rc, 2)
        self.assertIn('--owner', out)
        rc, out = cut('--owner', 'nobody', 'begin', 'it-1')
        self.assertEqual(rc, 2)
        self.assertIn('has not joined', out)
        rc, out = cut('begin', 'it-1', '--owner', 'g1', '--session', sess)
        self.assertEqual(rc, 0, out)
        self.assertEqual(hs.status()[1]['holder'], 'g1')
        rc, out = cut('end', 'it-1', '--owner', 'g1', '--session', sess)
        self.assertEqual(rc, 0, out)
        self.assertIsNone(hs.status()[1]['holder'])


if __name__ == '__main__':
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.wasSuccessful()
    print('RESULT: %s test_hot_shared %d run, %d failed, %d errors' % ('PASS' if ok else 'FAIL', r.testsRun, len(r.failures), len(r.errors)))
    sys.exit(0 if ok else 1)
