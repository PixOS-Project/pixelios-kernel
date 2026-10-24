#!/usr/bin/env python3
"""Host test for tools/phone-lock (TP-016): temp PHONE_LOCK_DIR, short ttls, no device."""
import json, os, signal, subprocess, sys, tempfile, time, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-lock")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.d.name, "phone.lock")
        self.env = dict(os.environ, PHONE_LOCK_DIR=self.lock)

    def tearDown(self):
        self.d.cleanup()

    def pl(self, *a):
        r = subprocess.run([sys.executable, TOOL, *a], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertRegex(r.stdout.splitlines()[-1], r"^RESULT: (PASS|FAIL|ERROR) owner=\S+ held_for=\d+s$")
        return r

    def info(self):
        with open(os.path.join(self.lock, "info.json")) as f:
            return json.load(f)

    def test_acquire_busy_release(self):
        self.assertEqual(self.pl("acquire", "--owner", "a").returncode, 0)
        r = self.pl("acquire", "--owner", "b")
        self.assertEqual(r.returncode, 1)
        self.assertIn("held by a", r.stdout)
        self.assertEqual(self.pl("release", "--owner", "a").returncode, 0)
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 0)

    def test_wait_timeout_bounded(self):
        self.pl("acquire", "--owner", "a")
        t = time.time()
        self.assertEqual(self.pl("acquire", "--owner", "b", "--wait", "1").returncode, 1)
        self.assertTrue(0.8 < time.time() - t < 5)

    def test_wait_gets_lock_when_released(self):
        self.pl("acquire", "--owner", "a")
        p = subprocess.Popen([sys.executable, TOOL, "acquire", "--owner", "b", "--wait", "10"], env=self.env,
                             stdout=subprocess.PIPE, text=True)
        time.sleep(0.5)
        self.pl("release", "--owner", "a")
        out, _ = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0, out)
        self.assertEqual(self.info()["owner"], "b")

    def test_stale_pid_takeover(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.pl("acquire", "--owner", "a", "--pid", str(dead.pid))
        r = self.pl("acquire", "--owner", "b")
        self.assertEqual(r.returncode, 0)
        self.assertIn("takeover", r.stdout)
        self.assertEqual(self.info()["owner"], "b")

    def test_live_pid_not_taken(self):
        self.pl("acquire", "--owner", "a", "--pid", str(os.getpid()))
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 1)

    def test_ttl_takeover(self):
        self.pl("acquire", "--owner", "a", "--ttl", "1")
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 1)
        time.sleep(1.5)
        r = self.pl("acquire", "--owner", "b")
        self.assertEqual(r.returncode, 0)
        self.assertIn("takeover", r.stdout)

    def test_same_owner_refresh(self):
        self.pl("acquire", "--owner", "a", "--ttl", "100")
        r = self.pl("acquire", "--owner", "a", "--ttl", "200")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.info()["ttl"], 200)

    def test_wrong_owner_release_and_force(self):
        self.pl("acquire", "--owner", "a")
        self.assertEqual(self.pl("release", "--owner", "b").returncode, 2)
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(self.pl("force-release").returncode, 2)  # no --yes
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(self.pl("force-release", "--yes").returncode, 0)
        self.assertFalse(os.path.exists(self.lock))

    def test_status(self):
        self.assertEqual(self.pl("status").returncode, 0)
        self.pl("acquire", "--owner", "a")
        r = self.pl("status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("held by a", r.stdout)

    def test_run_exit_code_and_release(self):
        r = self.pl("run", "--owner", "a", "--", sys.executable, "-c", "import sys; sys.exit(0)")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.lock))
        r = self.pl("run", "--owner", "a", "--", sys.executable, "-c", "import sys; sys.exit(7)")
        self.assertEqual(r.returncode, 7)
        self.assertIn("RESULT: FAIL", r.stdout)
        self.assertFalse(os.path.exists(self.lock))
        r = self.pl("run", "--owner", "a", "--", "/nonexistent/cmd")
        self.assertEqual(r.returncode, 127)
        self.assertFalse(os.path.exists(self.lock))

    def test_run_busy(self):
        self.pl("acquire", "--owner", "a")
        self.assertEqual(self.pl("run", "--owner", "b", "--", "true").returncode, 1)

    def test_run_releases_on_sigterm(self):
        p = subprocess.Popen([sys.executable, TOOL, "run", "--owner", "a", "--", "sleep", "60"], env=self.env,
                             stdout=subprocess.PIPE, text=True)
        for _ in range(100):
            if os.path.exists(os.path.join(self.lock, "info.json")):
                break
            time.sleep(0.05)
        self.assertEqual(self.info()["pid"], p.pid)
        time.sleep(0.3)  # handlers + child are installed after the lock is written
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 128 + 15, out)
        self.assertIn("RESULT: FAIL owner=a", out)
        self.assertFalse(os.path.exists(self.lock))

    def test_live_pid_beats_ttl(self):
        # TP-020: a running holder (a 35-minute flash) must not lose the lock because its ttl ran out
        self.pl("acquire", "--owner", "a", "--ttl", "1", "--pid", str(os.getpid()))
        time.sleep(1.5)
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 1)
        self.assertIn("held by a", self.pl("status").stdout)

    def test_check(self):
        self.assertEqual(self.pl("check", "--owner", "a").returncode, 1)  # no lock
        self.pl("acquire", "--owner", "a")
        self.assertEqual(self.pl("check", "--owner", "a").returncode, 0)
        self.assertEqual(self.pl("check", "--owner", "b").returncode, 1)  # someone else's
        self.assertEqual(self.pl("check").returncode, 0)  # no owner known: transition rule accepts a live lock, with a warning
        self.assertIn("owner unknown", self.pl("check").stdout)
        self.env["PHONE_OWNER"] = "a"
        self.assertEqual(self.pl("check").returncode, 0)
        self.env["PHONE_LOCK_OWNER"] = "b"  # PHONE_LOCK_OWNER wins
        self.assertEqual(self.pl("check").returncode, 1)

    def test_check_stale(self):
        self.pl("acquire", "--owner", "a", "--ttl", "1")
        time.sleep(1.5)
        self.assertEqual(self.pl("check", "--owner", "a").returncode, 1)

    def test_run_exports_owner(self):
        r = self.pl("run", "--owner", "a", "--", sys.executable, TOOL, "check")  # inner check sees PHONE_LOCK_OWNER=a
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_waiters_served_in_order(self):
        self.pl("acquire", "--owner", "a")
        order = os.path.join(self.d.name, "order")
        ps = []
        for o in ("b", "c", "d"):
            ps.append(subprocess.Popen([sys.executable, TOOL, "run", "--owner", o, "--wait", "30", "--", "sh", "-c", "echo %s >> %s" % (o, order)],
                                       env=self.env, stdout=subprocess.PIPE, text=True))
            time.sleep(0.4)  # distinct arrival times
        self.assertIn("queue=b,c,d", self.pl("status").stdout)
        self.pl("release", "--owner", "a")
        for p in ps:
            out, _ = p.communicate(timeout=20)
            self.assertEqual(p.returncode, 0, out)
        with open(order) as f:
            self.assertEqual(f.read().split(), ["b", "c", "d"])

    def test_no_queue_jumping(self):
        self.pl("acquire", "--owner", "a")
        w = subprocess.Popen([sys.executable, TOOL, "acquire", "--owner", "b", "--wait", "5"], env=self.env, stdout=subprocess.PIPE, text=True)
        time.sleep(0.5)
        self.pl("release", "--owner", "a")
        # c arrives right after the release but b (queued earlier) must win
        r = self.pl("acquire", "--owner", "c")
        out, _ = w.communicate(timeout=10)
        self.assertEqual(w.returncode, 0, out)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.info()["owner"], "b")

    def test_dead_waiter_dropped(self):
        self.pl("acquire", "--owner", "a")
        w = subprocess.Popen([sys.executable, TOOL, "acquire", "--owner", "b", "--wait", "30"], env=self.env, stdout=subprocess.DEVNULL)
        time.sleep(0.5)
        w.kill(); w.wait()
        self.pl("release", "--owner", "a")
        self.assertEqual(self.pl("acquire", "--owner", "c").returncode, 0)


if __name__ == "__main__":
    unittest.main()
