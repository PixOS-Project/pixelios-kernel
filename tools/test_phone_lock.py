#!/usr/bin/env python3
"""Host test for tools/phone-lock (TP-016): temp PHONE_LOCK_DIR, short ttls, no device."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
from pathlib import Path
import json, re, os, shutil, signal, subprocess, sys, tempfile, time, unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone-lock")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.d.name, "phone.lock")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PHONE_LOCK_")}
        self.env["PHONE_LOCK_DIR"] = self.lock

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

    def test_wait_cap_said(self):
        # 2026-11-11: a --wait above the cap is cut loudly (rule 7), and 7200 (2 h holds of other threads) is not cut
        r = self.pl("acquire", "--owner", "a", "--wait", "9000")
        self.assertEqual(r.returncode, 0)
        self.assertIn("phone-lock GAP: --wait 9000s capped at 7200s", r.stderr)
        self.pl("release", "--owner", "a")
        r = self.pl("acquire", "--owner", "a", "--wait", "7200")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("capped", r.stderr)

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

    def _dead_pid(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        return dead.pid

    def test_status_removes_dead_holder(self):
        # 2026-11-03: a lock left by a dead xrun (pid gone) made every status caller (tools/queue phone_free, run-preflight) see the phone as held
        self.pl("acquire", "--owner", "a", "--pid", str(self._dead_pid()))
        r = self.pl("status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("phone-lock STALE: removed the lock of a", r.stderr)
        self.assertRegex(r.stdout.splitlines()[-1], r"^RESULT: PASS owner=- ")
        self.assertFalse(os.path.exists(self.lock))

    def test_status_keeps_live_holder_past_ttl(self):
        self.pl("acquire", "--owner", "a", "--pid", str(os.getpid()), "--ttl", "1")
        time.sleep(1.5)
        r = self.pl("status")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("STALE", r.stderr)
        self.assertEqual(self.info()["owner"], "a")

    def test_tick_removes_dead_holder(self):
        self.pl("acquire", "--owner", "a", "--pid", str(self._dead_pid()))
        r = self.pl("tick")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("removed the stale lock of a", r.stdout)
        self.assertFalse(os.path.exists(self.lock))

    def test_read_live_dead_holder_is_free(self):
        # tools/agent-guard reads the lock in-process through read_live(): a dead holder must not deny phone tool edits/commands
        import importlib.machinery
        self.pl("acquire", "--owner", "a", "--pid", str(self._dead_pid()))
        old = os.environ.get("PHONE_LOCK_DIR")
        os.environ["PHONE_LOCK_DIR"] = self.lock
        try:
            pl = importlib.machinery.SourceFileLoader("pl_live", TOOL).load_module()
            self.assertIsNone(pl.read_live())
            self.assertIsNotNone(pl.read())  # read_live only reports; status/tick remove the lock
            pl.write("b", os.getpid(), 100, "y", "T1")
            self.assertEqual(pl.read_live()["owner"], "b")
        finally:
            if old is None:
                os.environ.pop("PHONE_LOCK_DIR", None)
            else:
                os.environ["PHONE_LOCK_DIR"] = old

    def test_ttl_takeover(self):
        self.pl("acquire", "--owner", "a", "--ttl", "4")  # generous: the gate runs this test while the machine is busy
        self.assertEqual(self.pl("acquire", "--owner", "b").returncode, 1)
        time.sleep(4.5)
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

    def wait_queued(self, owner, limit=60):
        """Block until `phone-lock status` lists owner in its queue (the waiter registered), or fail after limit s."""
        t0 = time.time()
        while time.time() - t0 < limit:
            m = re.search(r"queue=(\S+)", self.pl("status").stdout)
            if m and owner in m.group(1).split(","):
                return
            time.sleep(0.05)
        self.fail("waiter %s never showed up in the queue" % owner)

    def test_waiters_served_in_order(self):
        self.pl("acquire", "--owner", "a")
        order = os.path.join(self.d.name, "order")
        ps = []
        for o in ("b", "c", "d"):
            ps.append(subprocess.Popen([sys.executable, TOOL, "run", "--owner", o, "--wait", "30", "--", "sh", "-c", "echo %s >> %s" % (o, order)],
                                       env=self.env, stdout=subprocess.PIPE, text=True))
            self.wait_queued(o)  # the waiter is in the queue (event), so arrival order is the spawn order whatever the load
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
        self.wait_queued("b")
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

    def test_adb_server_own_unit(self):
        # AP-230: the adb server starts in its own systemd scope, not in CMD's cgroup; temp lock dirs never touch the host adb server
        import socket
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertNotIn("adb-server", r.stdout)                      # temp PHONE_LOCK_DIR: step skipped
        fake = os.path.join(self.d.name, "bin"); os.mkdir(fake)
        args = os.path.join(self.d.name, "sr.args")
        for n, body in (("systemd-run", 'echo "$@" > %s; exit ${FAKE_SR_RC:-0}' % args), ("adb", "exit 0")):
            with open(os.path.join(fake, n), "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(os.path.join(fake, n), 0o755)
        free = socket.socket(); free.bind(("127.0.0.1", 0)); port = free.getsockname()[1]; free.close()
        self.env.update(PHONE_LOCK_ADB_SERVER="on", ANDROID_ADB_SERVER_PORT=str(port), PATH=fake + os.pathsep + self.env["PATH"],
                        PHONE_LOCK_SYSTEMD_RUN=os.path.join(fake, "systemd-run"))
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"phone-lock: adb-server started in unit pixos-adb-server-\d+-\d+")
        with open(args) as f:
            a = f.read().split()
        self.assertEqual(a[:4], ["--user", "--scope", "--collect", "--quiet"])
        self.assertEqual(a[-1], "start-server")
        os.remove(args)
        r = self.pl("acquire", "--owner", "b", "--pid", str(os.getpid()))   # xrun path (plock_take = acquire)
        self.assertIn("adb-server started in unit", r.stdout)
        self.pl("force-release", "--yes")
        self.env["FAKE_SR_RC"] = "3"
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout)                   # CMD still runs
        self.assertRegex(r.stdout, r"phone-lock: WARN adb-server start in unit \S+ failed rc=3")
        srv = socket.socket(); srv.bind(("127.0.0.1", port)); srv.listen(1)
        try:
            r = self.pl("run", "--owner", "a", "--", "true")
        finally:
            srv.close()
        self.assertIn("phone-lock: adb-server already listening on %d" % port, r.stdout)
        self.env["ANDROID_ADB_SERVER_PORT"] = "x"                       # r1 F1: bad port = WARN, CMD runs
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ANDROID_ADB_SERVER_PORT='x' is not a port", r.stdout)
        self.env["PHONE_LOCK_ADB_SERVER"] = "off"                       # r1 F2: an explicit skip is loud
        self.assertIn("adb-server step SKIPPED (PHONE_LOCK_ADB_SERVER=off)", self.pl("run", "--owner", "a", "--", "true").stdout)

    def test_info_not_world_readable(self):
        self.pl("acquire", "--owner", "x", "--pid", str(os.getpid()))
        self.assertEqual(os.stat(os.path.join(self.lock, "info.json")).st_mode & 0o077, 0)

    # ---- T-2ebf: a lock that vanishes while its holder lives must be named, and every removal must leave a history line ----
    def history(self):
        try:
            with open(self.lock + ".history") as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:   # lint-silent: expected no history file yet: an empty list
            return []

    def _holder(self):
        h = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(h.wait)
        self.addCleanup(h.kill)
        return h

    def test_history_records_every_removal(self):
        self.pl("acquire", "--owner", "a", "--pid", str(os.getpid()), "--token", "tok-a")
        self.pl("release", "--token", "tok-a")
        self.pl("acquire", "--owner", "b", "--pid", str(os.getpid()))
        self.pl("force-release", "--yes")
        self.pl("acquire", "--owner", "c", "--pid", str(self._dead_pid()))
        self.pl("status")                                              # reaps the dead holder
        self.pl("run", "--owner", "d", "--", "true")
        ev = self.history()
        self.assertEqual([e["event"] for e in ev], ["RELEASE", "FORCE-RELEASE", "STALE-REMOVE", "RUN-END"], ev)
        for e in ev:                                                   # who removed what: pid, ppid, cmd, path, holder
            self.assertEqual(e["path"], self.lock)
            self.assertTrue(e["pid"] and e["ppid"] == os.getpid() and "phone-lock" in e["cmd"], e)
            self.assertIn("%d:" % os.getpid(), " ".join(e["ancestors"]))
        self.assertEqual([e["holder"]["owner"] for e in ev], ["a", "b", "c", "d"])
        self.assertTrue(ev[0]["holder"]["pid_alive"] and not ev[2]["holder"]["pid_alive"])

    def _unshare_pl(self, *a):
        """run phone-lock in a new PID namespace with its own /proc (what tools/test_kernel_hooks.sh runmut does); skip when unshare is not permitted."""
        ns = ["unshare", "-r", "-m", "-p", "-f"]   # no --mount-proc: the offload gate container refuses mounting /proc; /proc/self/ns/pid still names the new namespace
        try:
            probe = subprocess.run(ns + ["true"], capture_output=True, text=True, timeout=30)
        except OSError as err:
            self.skipTest("unshare not runnable here: %s" % err)
        if probe.returncode != 0:   # containers without user/pid namespaces (the offload gate slot): the host run still covers it
            self.skipTest("unshare -r -m -p -f not permitted here: " + probe.stderr.strip()[:120])
        r = subprocess.run(ns + [sys.executable, TOOL, *a], env=self.env, capture_output=True, text=True, timeout=30)
        return r

    def test_live_holder_survives_status_from_another_pid_namespace(self):
        # T-b9a3: 7 STALE-REMOVE lines on 2026-11-04 were `phone-lock status` run inside the hook-mutant `unshare -p`: the holder pid does not exist
        # in the new namespace, so the live holder looked dead and the real lock was deleted. It must stay, with a loud NAMESPACE line.
        self.pl("acquire", "--owner", "a", "--pid", str(os.getpid()), "--token", "tok-a")
        self.assertTrue(self.info().get("pidns", "").startswith("pid:["), self.info())
        r = self._unshare_pl("status")
        self.assertTrue(os.path.isdir(self.lock), "the lock of a live holder was removed from another PID namespace: " + r.stdout + r.stderr)
        self.assertIn("NAMESPACE", r.stderr)
        self.assertIn("owner=a", r.stdout)
        self.assertEqual(self.info()["owner"], "a")
        self.assertFalse(os.path.exists(self.lock + ".history") and "STALE-REMOVE" in open(self.lock + ".history").read())
        r = self._unshare_pl("tick")                                    # the other reaping path
        self.assertTrue(os.path.isdir(self.lock), r.stdout + r.stderr)
        r = self._unshare_pl("acquire", "--owner", "b")                  # and the acquire takeover path: busy, no takeover
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.info()["owner"], "a")

    def test_lock_without_pidns_field_is_held_from_another_pid_namespace(self):
        self.pl("acquire", "--owner", "a", "--pid", str(os.getpid()), "--token", "tok-a")
        i = self.info()
        del i["pidns"]                                                  # a lock written by the old tool
        with open(os.path.join(self.lock, "info.json"), "w") as f:
            json.dump(i, f)
        r = self._unshare_pl("status")
        self.assertTrue(os.path.isdir(self.lock), r.stdout + r.stderr)
        self.assertIn("NAMESPACE", r.stderr)
        r = self.pl("status")                                           # from the initial namespace the old lock is judged by pid as before
        self.assertTrue(os.path.isdir(self.lock), r.stdout)

    def _mutant_tool(self):
        """copy of the tool with the T-b9a3/T-fa98 guard off (foreign_ns always False = the old 'judge any pid by kill(0)' STALE-REMOVE)."""
        src = open(TOOL).read()
        old = '    if not i.get("pid") or mine == theirs:'
        self.assertEqual(src.count(old), 1, "the foreign_ns guard line moved: update this mutant")
        d = os.path.join(self.d.name, "mut")
        os.makedirs(d)
        shutil.copy(os.path.join(os.path.dirname(TOOL), "simseams.py"), d)
        shutil.copy(os.path.join(os.path.dirname(TOOL), "phone_lock_end.py"), d)
        shutil.copy(os.path.join(os.path.dirname(TOOL), "phone_lock_root.py"), d)
        with open(os.path.join(d, "phone-lock"), "w") as f:
            f.write(src.replace(old, "    if True:"))
        return os.path.join(d, "phone-lock")

    def test_phone_lock_status_readonly(self):
        # T-fa98: `phone-lock status` (session-start hook, another PID namespace) removed T-061a's lock 6 s after it was taken (STALE-REMOVE,
        # pid_alive=false). A read-only call never removes a lock whose holder it cannot check: other PID namespace, or no readable /proc ns link.
        ns = ["unshare", "-r", "-m", "-n", "--pid", "--fork"]
        try:
            probe = subprocess.run(ns + ["true"], capture_output=True, text=True, timeout=30)
        except OSError as err:
            self.skipTest("unshare not runnable here: %s" % err)
        if probe.returncode != 0:
            self.skipTest("unshare -r -m -n --pid --fork not permitted here: " + probe.stderr.strip()[:120])
        h = self._holder()                                              # a live process outside the new namespaces
        self.pl("acquire", "--owner", "T-061a", "--pid", str(h.pid), "--token", "tok-061a")

        def status(tool):
            return subprocess.run(ns + [sys.executable, tool, "status"], env=self.env, capture_output=True, text=True, timeout=30)

        r = status(TOOL)                                                # 1. real tool, other pid + net namespace: lock stays, loud NAMESPACE line
        self.assertTrue(os.path.isdir(self.lock), "status from another PID namespace removed a live holder's lock: " + r.stdout + r.stderr)
        self.assertEqual(self.info()["token"], "tok-061a")
        self.assertIn("NAMESPACE", r.stderr)
        self.assertNotIn("STALE", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(self.lock + ".history") and "STALE-REMOVE" in Path(self.lock + ".history").read_text())
        mut = self._mutant_tool()                                       # 2. mutant (old behaviour): the same call deletes the lock = the test fails on it
        r = status(mut)
        self.assertFalse(os.path.isdir(self.lock), "mutant did not reproduce the old STALE-REMOVE: " + r.stdout + r.stderr)
        self.assertIn("STALE-REMOVE", Path(self.lock + ".history").read_text())
        # 3. no readable /proc ns link (liveness uncheckable): a DEAD pid is not enough to remove the lock from a read-only call
        self.pl("acquire", "--owner", "T-061a", "--pid", str(self._dead_pid()), "--token", "tok-dead")
        wrap = ("import importlib.machinery as m, importlib.util as u, os, sys\n"
                "real = os.readlink\n"
                "def rl(p, *a, **k):\n"
                "    if str(p).startswith('/proc/self/ns/'):\n"
                "        raise OSError(13, 'ns link unreadable (test)')\n"
                "    return real(p, *a, **k)\n"
                "os.readlink = rl\n"
                "l = m.SourceFileLoader('phone_lock_t', sys.argv[1]); s = u.spec_from_loader('phone_lock_t', l); mod = u.module_from_spec(s); l.exec_module(mod)\n"
                "sys.argv = ['phone-lock'] + sys.argv[2:]; sys.exit(mod.main())\n")
        r = subprocess.run([sys.executable, "-c", wrap, TOOL, "status"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertTrue(os.path.isdir(self.lock), "status with an unreadable /proc ns link removed a lock it could not check: " + r.stdout + r.stderr)
        self.assertIn("NAMESPACE", r.stderr)
        r = self.pl("status")                                           # normal environment: the dead holder is still reaped (no regression)
        self.assertFalse(os.path.isdir(self.lock), r.stdout + r.stderr)
        print("RESULT: PASS test_phone_lock_status_readonly")

    def test_dead_holder_in_same_namespace_is_still_removed(self):
        self.pl("acquire", "--owner", "c", "--pid", str(self._dead_pid()))
        self.pl("status")
        self.assertFalse(os.path.isdir(self.lock))

    def test_vanished_lock_with_live_holder_is_named(self):
        # the 2026-11-03 failure: the lock directory disappears, the holder lives, nobody says so. Old code: status prints "free" and logs nothing.
        h = self._holder()
        self.pl("acquire", "--owner", "T-dbe4", "--pid", str(h.pid), "--token", "tok-dbe4")
        shutil.rmtree(self.lock)                                       # the unknown remover (not a phone-lock path)
        r = self.pl("status")
        self.assertEqual(r.returncode, 0)                              # the phone is free again, but not silently
        self.assertIn("phone-lock VANISHED: the lock of T-dbe4 (pid %d alive" % h.pid, r.stderr)
        ev = self.history()
        self.assertEqual([e["event"] for e in ev], ["VANISHED"], ev)
        self.assertEqual(ev[0]["holder"]["owner"], "T-dbe4")
        self.assertTrue(ev[0]["holder"]["pid_alive"])
        self.assertIn("phone-lock", ev[0]["cmd"])
        self.assertNotIn("VANISHED", self.pl("status").stderr)         # reported once

    def test_vanished_lock_reported_by_check_tick_and_acquire(self):
        for op in (("check",), ("tick",), ("acquire", "--owner", "x")):
            h = self._holder()
            self.pl("force-release", "--yes")
            self.pl("acquire", "--owner", "o", "--pid", str(h.pid))
            shutil.rmtree(self.lock)
            self.assertIn("phone-lock VANISHED", self.pl(*op).stderr, op)

    def test_vanished_dead_holder_and_recorded_removals_are_quiet(self):
        self.pl("acquire", "--owner", "a", "--pid", str(self._dead_pid()))
        shutil.rmtree(self.lock)                                       # holder already dead: nobody lost a phone
        self.assertNotIn("VANISHED", self.pl("status").stderr)
        h = self._holder()
        self.pl("acquire", "--owner", "b", "--pid", str(h.pid), "--token", "tb")
        self.pl("release", "--token", "tb")                            # recorded removal with a live holder
        self.assertNotIn("VANISHED", self.pl("status").stderr)
        self.assertEqual([e["event"] for e in self.history()], ["RELEASE"])

    def test_lock_replaced_over_live_holder_is_named(self):
        h = self._holder()
        self.pl("acquire", "--owner", "a", "--pid", str(h.pid), "--token", "ta")
        shutil.rmtree(self.lock)
        r = self.pl("acquire", "--owner", "b", "--pid", str(os.getpid()), "--token", "tb")
        self.assertEqual(r.returncode, 0)
        self.assertIn("phone-lock VANISHED: the lock of a", r.stderr)
        self.assertEqual([e["event"] for e in self.history()], ["VANISHED"])

    def test_watch_logs_unrecorded_removal_with_candidates(self):
        h = self._holder()
        w = subprocess.Popen([sys.executable, TOOL, "watch", "--duration", "8"], env=self.env, stdout=subprocess.PIPE, text=True)
        self.addCleanup(w.wait)
        self.addCleanup(w.kill)
        self.assertIn("watch: running", w.stdout.readline())
        self.pl("acquire", "--owner", "a", "--pid", str(h.pid), "--token", "ta")
        time.sleep(0.3)
        self.pl("acquire", "--owner", "x", "--pid", str(os.getpid()))   # busy: no effect
        self.pl("release", "--token", "ta")                             # recorded: must NOT be reported
        self.pl("acquire", "--owner", "b", "--pid", str(h.pid), "--token", "tb")
        time.sleep(0.3)
        rm = subprocess.Popen(["rm", "-rf", self.lock])                 # the unrecorded remover
        rm.wait()
        out = w.communicate(timeout=20)[0]
        self.assertEqual(len([x for x in out.splitlines() if "WATCH: lock removed" in x]), 1, out)
        ev = [e for e in self.history() if e["event"] == "WATCH-REMOVED"]
        self.assertEqual(len(ev), 1, self.history())
        self.assertEqual(ev[0]["holder"]["owner"], "b")
        self.assertTrue(ev[0]["holder_alive"])
        self.assertTrue(ev[0]["candidates"], ev)


if __name__ == "__main__":
    unittest.main()
