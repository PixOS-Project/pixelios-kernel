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

    def test_same_label_is_busy_same_token_refreshes(self):
        # TP-020 F2: the owner name is a label; two agents sharing it must not both hold the lock
        self.pl("acquire", "--owner", "a", "--token", "T1", "--pid", str(os.getpid()))
        self.assertEqual(self.pl("acquire", "--owner", "a", "--pid", str(os.getpid())).returncode, 1)
        r = self.pl("acquire", "--owner", "a", "--token", "T1", "--pid", str(os.getpid()))
        self.assertEqual(r.returncode, 0)
        self.assertIn("already held", r.stdout)

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
        env_a = dict(self.env, PHONE_LOCK_TOKEN="TA")
        self.assertEqual(self.pl("check").returncode, 1)  # no lock
        self.pl("acquire", "--owner", "a", "--token", "TA", "--pid", str(os.getpid()))
        r = self.pl("check")  # a shell without the token: the F1 case, must NOT pass while someone holds the lock
        self.assertEqual(r.returncode, 1)
        self.assertIn("no token", r.stdout)
        self.assertEqual(self.pl("check", "--token", "TB").returncode, 1)  # wrong token
        self.assertEqual(self.pl("check", "--token", "TA").returncode, 0)
        self.env = env_a
        self.assertEqual(self.pl("check").returncode, 0)  # token from the environment

    def test_check_stale(self):
        self.pl("acquire", "--owner", "a", "--ttl", "1", "--token", "TA")
        time.sleep(1.5)
        self.assertEqual(self.pl("check", "--token", "TA").returncode, 1)

    def test_run_exports_token(self):
        r = self.pl("run", "--owner", "a", "--", sys.executable, TOOL, "check")  # inner check sees PHONE_LOCK_TOKEN
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.pl("check").returncode, 1)  # and a plain shell still does not pass (lock is free here)

    def test_run_inner_cannot_be_impersonated(self):
        # while `run` holds the lock, a second shell with the same label but no token fails the check
        p = subprocess.Popen([sys.executable, TOOL, "run", "--owner", "a", "--", "sleep", "30"], env=self.env, stdout=subprocess.PIPE, text=True)
        for _ in range(100):
            if os.path.exists(os.path.join(self.lock, "info.json")) and self.info().get("pgid"):
                break
            time.sleep(0.05)
        try:
            self.env["PHONE_OWNER"] = "a"
            self.assertEqual(self.pl("check").returncode, 1)
        finally:
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=10)

    def test_sigkill_of_run_does_not_free_the_phone(self):
        # TP-020 F3: the child (xrun's fastboot) outlives a SIGKILLed `run`; the lock stays until the group is gone
        p = subprocess.Popen([sys.executable, TOOL, "run", "--owner", "a", "--", "sleep", "30"], env=self.env, stdout=subprocess.PIPE, text=True)
        for _ in range(100):
            if os.path.exists(os.path.join(self.lock, "info.json")) and self.info().get("pgid"):
                break
            time.sleep(0.05)
        pgid = self.info()["pgid"]
        p.kill(); p.wait()
        try:
            self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 1)
        finally:
            os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.3)
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 0)

    def test_free_lock_with_ghost_queue_head_does_not_spin(self):
        # TP-020 F6: a live-pid ticket that never takes the lock must not hang other acquirers past their wait
        os.makedirs(self.lock + ".queue")
        open(os.path.join(self.lock + ".queue", "%020d-%d-ghost" % (time.time_ns(), os.getpid())), "w").close()
        t = time.time()
        r = self.pl("acquire", "--owner", "x", "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertLess(time.time() - t, 5)
        self.assertIn("queue head", r.stdout)

    def test_stale_takeover_never_removes_a_different_lock(self):
        # TP-020 F5: decided stale on an old read, but the lock was replaced by a live one meanwhile
        os.environ["PHONE_LOCK_DIR"] = self.lock
        import importlib.machinery
        pl = importlib.machinery.SourceFileLoader("pl_mod", TOOL).load_module()
        os.makedirs(self.lock)
        old = {"owner": "dead", "token": "T0", "pid": 0, "pgid": 0, "start": 1, "ttl": 1, "cmd": "x"}
        pl.write("new", os.getpid(), 100, "y", "T1")
        self.assertFalse(pl.take_over_stale(old))
        self.assertEqual(self.info()["token"], "T1")
        pl.write("dead", 0, 1, "x", "T0")
        self.assertTrue(pl.take_over_stale(pl.read()))
        self.assertFalse(os.path.exists(self.lock))

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


    def sh(self, script, *pre):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([*pre, "bash", "-c", ". tools/plock.sh; " + script], cwd=root, env=self.env, capture_output=True, text=True, timeout=30)

    def test_plock_take_and_drop(self):
        r = self.sh('plock_take t 0 || exit 9; tools/phone-lock check >/dev/null || exit 8; echo took=$PLOCK_TOOK; plock_drop; test ! -e "$PHONE_LOCK_DIR"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("took=1", r.stdout)

    def test_plock_take_busy_and_under_run(self):
        self.pl("acquire", "--owner", "other", "--pid", str(os.getpid()))
        r = self.sh("plock_take t 0; echo rc=$?; echo $PLOCK_MSG")
        self.assertIn("rc=1", r.stdout)
        self.assertIn("busy", r.stdout)
        self.pl("force-release", "--yes")
        # already held by an outer run: no second lock, no release of the outer one
        r = self.pl("run", "--owner", "o", "--", "bash", "-c", '. tools/plock.sh; plock_take t 0; echo took=$PLOCK_TOOK; plock_drop; tools/phone-lock check >/dev/null && echo still-held')
        self.assertIn("took=0", r.stdout)
        self.assertIn("still-held", r.stdout)

    def test_release_needs_token_for_pid_locks(self):
        # review 12: a shell without the token must not free a live run/xrun lock by guessing the owner label
        self.pl("acquire", "--owner", "A", "--token", "TA", "--pid", str(os.getpid()))
        self.assertEqual(self.pl("release", "--owner", "A").returncode, 2)
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(self.pl("release", "--token", "TA").returncode, 0)

    def test_acquire_pgid_keeps_lock_after_pid_dies(self):
        # review 12: xrun/probe-run are SIGKILLed, their fastboot child survives in the group
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        self.pl("acquire", "--owner", "x", "--pid", str(dead.pid), "--pgid", str(child.pid))
        try:
            self.assertEqual(self.pl("acquire", "--owner", "y").returncode, 1)
        finally:
            child.kill(); child.wait()
        self.assertEqual(self.pl("acquire", "--owner", "y").returncode, 0)

    def test_info_not_world_readable(self):
        self.pl("acquire", "--owner", "x", "--pid", str(os.getpid()))
        self.assertEqual(os.stat(os.path.join(self.lock, "info.json")).st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
