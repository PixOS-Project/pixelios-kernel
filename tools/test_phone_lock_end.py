#!/usr/bin/env python3
"""Host test for the phone-lock run end (user 2026-11-09): park in fastboot after every run, hard limit (run + tick backstop), `end`, `park`,
`run --detach` + `follow` + thread notify. Temp PHONE_LOCK_DIR under $TMPDIR, fake park / t3 / systemd-run commands; no device, no systemd."""
import json, os, signal, subprocess, sys, tempfile, textwrap, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TOOL = os.path.join(HERE, "phone-lock")

FAKE_SR = textwrap.dedent("""\
    #!/usr/bin/env python3
    # fake systemd-run: honour --working-directory, --setenv, StandardOutput=append:LOG; start the command in its own session and return
    import os, subprocess, sys
    a = sys.argv[1:]; cwd = None; env = dict(os.environ); log = None
    while a and a[0].startswith("-"):
        o = a.pop(0)
        if o.startswith("--working-directory="): cwd = o.split("=", 1)[1]
        elif o.startswith("--setenv="): k, v = o.split("=", 1)[1].split("=", 1); env[k] = v
        elif o.startswith("--property=StandardOutput=append:"): log = o.split(":", 1)[1]
    with open(os.environ["FAKE_SR_ARGS"], "a") as f: f.write(" ".join(sys.argv[1:]) + "\\n")
    out = open(log, "a") if log else subprocess.DEVNULL
    subprocess.Popen(a, cwd=cwd, env=env, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)
    """)


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.d.name, "phone.lock")
        self.park_log = os.path.join(self.d.name, "park.log")
        park = os.path.join(self.d.name, "fake-park")
        with open(park, "w") as f:   # records whether the caller held the lock when it parked (tools/phone goto checks the same token)
            f.write("#!/bin/sh\nif %s check >/dev/null 2>&1; then echo held >> %s; else echo NOT-HELD >> %s; fi\necho 'RESULT: PASS state=fastboot target=fastboot steps=1'\n"
                    % (TOOL, self.park_log, self.park_log))
        os.chmod(park, 0o755)
        self.t3_log = os.path.join(self.d.name, "t3.log")
        t3 = os.path.join(self.d.name, "fake-t3")
        with open(t3, "w") as f:
            f.write("#!/bin/sh\nprintf '%%s|' \"$@\" >> %s\necho >> %s\n" % (self.t3_log, self.t3_log))
        os.chmod(t3, 0o755)
        sr = os.path.join(self.d.name, "fake-sr")
        with open(sr, "w") as f:
            f.write(FAKE_SR)
        os.chmod(sr, 0o755)
        self.env = dict(os.environ, PHONE_LOCK_DIR=self.lock, PHONE_LOCK_PARK=park, PHONE_LOCK_T3=t3, PHONE_LOCK_SYSTEMD_RUN=sr,
                        FAKE_SR_ARGS=os.path.join(self.d.name, "sr.args"), PHONE_LOCK_STOP_GRACE="1", PHONE_LOCK_ADB_SERVER="off",
                        PIXOS_OWNERS_DIR=os.path.join(self.d.name, "owners"))   # hermetic: never the real owner files
        for k in ("PHONE_LOCK_TOKEN", "PHONE_OWNER", "T3CODE_THREAD_ID", "PIXOS_SLEEP_SCALE"):
            self.env.pop(k, None)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:   # lint-silent: expected the process/file is already gone (test helper)
                pass
            p.wait()
        self.d.cleanup()

    def pl(self, *a, timeout=60, env=None):
        return subprocess.run([sys.executable, TOOL] + list(a), env=env or self.env, capture_output=True, text=True, timeout=timeout)

    def bg(self, *a):
        p = subprocess.Popen([sys.executable, TOOL] + list(a), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.procs.append(p)
        return p

    def info(self):
        try:
            return json.load(open(os.path.join(self.lock, "info.json")))
        except OSError:   # lint-silent: expected the process/file is already gone (test helper)
            return None

    def wait_for(self, pred, t=15):
        end = time.time() + t
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def history(self):
        try:
            return [json.loads(l) for l in open(self.lock + ".history")]
        except OSError:   # lint-silent: expected the process/file is already gone (test helper)
            return []

    def parks(self):
        try:
            return open(self.park_log).read().split()
        except OSError:   # lint-silent: expected the process/file is already gone (test helper)
            return []

    def test_normal_end_parks_while_holding(self):
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.parks(), ["held"])
        self.assertIn("PARK ok after command exit 0", r.stdout)
        self.assertIsNone(self.info())
        self.assertIn("park=fastboot", r.stdout)

    def test_failed_command_parks_and_keeps_rc(self):
        r = self.pl("run", "--owner", "a", "--", "sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(self.parks(), ["held"])

    def test_deadline_written(self):
        p = self.bg("run", "--owner", "a", "--max", "100", "--", "sleep", "30")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        i = self.info()
        self.assertEqual(i["kind"], "run")
        self.assertTrue(99 <= i["deadline"] - i["start"] <= 101, i)
        st = self.pl("status")
        self.assertIn("hard_limit_in=", st.stdout)

    def test_hard_limit_stops_group_parks_releases(self):
        t0 = time.time()
        r = self.pl("run", "--owner", "a", "--max", "2", "--", "sh", "-c", "sleep 60 & sleep 60", timeout=40)
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(r.returncode, 143, r.stdout)
        self.assertIn("STOP hard limit 2s", r.stdout)
        self.assertEqual(self.parks(), ["held"])
        self.assertIsNone(self.info())
        self.assertIn("HARD-LIMIT", [h["event"] for h in self.history()])

    def test_max_bounds(self):
        self.assertEqual(self.pl("run", "--owner", "a", "--max", "7201", "--", "true").returncode, 2)
        self.assertEqual(self.pl("run", "--owner", "a", "--max", "0", "--", "true").returncode, 2)

    def test_end_stops_own_run_with_reason(self):
        p = self.bg("run", "--owner", "gpu", "--", "sleep", "60")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        other = self.pl("end", "--owner", "wifi", "--reason", "not mine")
        self.assertEqual(other.returncode, 2)
        self.assertIn("end refused", other.stdout)
        self.assertIsNotNone(self.info())
        r = self.pl("end", "--owner", "gpu", "--reason", "experiment done early", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = p.communicate(timeout=30)[0]
        self.assertIn("end request by gpu: experiment done early", out)
        self.assertEqual(self.parks(), ["held"])
        self.assertIsNone(self.info())
        ev = [h["event"] for h in self.history()]
        self.assertIn("END-REQUEST", ev)
        self.assertIn("RUN-END", ev)

    def test_end_on_acquire_holder_parks_itself(self):
        holder = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(holder)
        r = self.pl("acquire", "--owner", "x", "--pid", str(holder.pid), "--pgid", str(holder.pid), "--ttl", "7200")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.info()["kind"], "acquire")
        r = self.pl("end", "--owner", "x", "--reason", "stop the bare xrun")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNotNone(holder.wait(timeout=5))
        self.assertEqual(self.parks(), ["held"])
        self.assertIsNone(self.info())

    def test_end_no_lock(self):
        r = self.pl("end", "--owner", "x", "--reason", "nothing")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no lock held", r.stdout)

    def test_park_command_free_and_busy(self):
        r = self.pl("park", "--owner", "x")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.parks(), ["held"])
        p = self.bg("run", "--owner", "gpu", "--", "sleep", "30")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        r = self.pl("park", "--owner", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("PARK skipped", r.stdout)
        self.assertEqual(self.parks(), ["held"])

    def test_tick_parks_after_dead_holder(self):
        holder = subprocess.Popen(["sleep", "0.2"], start_new_session=True)
        self.assertEqual(self.pl("acquire", "--owner", "x", "--pid", str(holder.pid), "--pgid", str(holder.pid)).returncode, 0)
        holder.wait()
        r = self.pl("tick")
        self.assertIn("removed the stale lock of x", r.stdout)
        self.assertIn("park=fastboot", r.stdout)
        self.assertEqual(self.parks(), ["held"])

    def test_tick_hard_limit_kills_hung_holder(self):
        # a holder that ignores SIGTERM and runs past its deadline: tick kills it (TERM then KILL), removes the lock, parks
        holder = subprocess.Popen(["sh", "-c", "trap '' TERM; while :; do sleep 0.2; done"], start_new_session=True)
        self.procs.append(holder)
        self.assertEqual(self.pl("acquire", "--owner", "x", "--pid", str(holder.pid), "--pgid", str(holder.pid), "--max", "1").returncode, 0)
        time.sleep(1.5)
        env = dict(self.env, PHONE_LOCK_TICK_GRACE="0")
        r = self.pl("tick", env=env, timeout=60)
        self.assertIn("HARD LIMIT ended the lock of x", r.stdout, r.stdout + r.stderr)
        self.assertIsNotNone(holder.wait(timeout=5))
        self.assertIsNone(self.info())
        self.assertEqual(self.parks(), ["held"])
        self.assertIn("HARD-LIMIT-REAP", [h["event"] for h in self.history()])

    def test_tick_legacy_lock_without_deadline(self):
        # a lock written by an older copy (no deadline, no kind): the limit is start + 7200
        holder = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(holder)
        os.mkdir(self.lock)
        with open(os.path.join(self.lock, "info.json"), "w") as f:
            json.dump({"owner": "old", "token": "t-old", "pid": holder.pid, "pgid": holder.pid, "start": int(time.time()) - 7300,
                       "ttl": 1800, "cmd": "bash -c xrun", "pidns": os.readlink("/proc/self/ns/pid")}, f)
        r = self.pl("tick", env=dict(self.env, PHONE_LOCK_TICK_GRACE="200"), timeout=60)
        self.assertNotIn("HARD LIMIT", r.stdout)
        self.assertIsNotNone(self.info())   # 100 s over, grace 200: kept
        r = self.pl("tick", env=dict(self.env, PHONE_LOCK_TICK_GRACE="50"), timeout=60)
        self.assertIn("HARD LIMIT ended the lock of old", r.stdout, r.stdout + r.stderr)
        self.assertIsNone(self.info())

    def test_tick_keeps_live_holder_before_deadline(self):
        p = self.bg("run", "--owner", "a", "--", "sleep", "30")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        r = self.pl("tick", env=dict(self.env, PHONE_LOCK_TICK_GRACE="0"))
        self.assertIsNotNone(self.info())
        self.assertNotIn("HARD LIMIT", r.stdout)

    def test_detach_follow_notify(self):
        env = dict(self.env, T3CODE_THREAD_ID="thread-abc-123")
        r = self.pl("run", "--owner", "gpu", "--detach", "--", "sh", "-c", "echo hello-from-run; exit 3", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = [l for l in r.stdout.splitlines() if l.startswith("DETACHED ")][0]
        rid = line.split()[1].split("=", 1)[1]
        self.assertIn("notify=thread:thread-abc-123", line)
        self.assertIn("--property=KillMode=mixed", open(self.env["FAKE_SR_ARGS"]).read())
        self.assertIn("--property=ManagedOOMPreference=avoid", open(self.env["FAKE_SR_ARGS"]).read())   # oomd picks a phone hold last (2026-11-11 kill)
        self.assertIn("--property=OOMScoreAdjust=-500", open(self.env["FAKE_SR_ARGS"]).read())   # and the kernel OOM killer too (it inherited 200)
        f = self.pl("follow", rid, "--timeout", "30", timeout=60)
        self.assertEqual(f.returncode, 3, f.stdout + f.stderr)
        self.assertIn("hello-from-run", f.stdout)
        self.assertIn("END id=%s rc=3 ended=normal park=fastboot" % rid, f.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.lock + ".runs", rid, "env.json")))
        self.assertEqual(self.parks(), ["held"])
        self.assertTrue(self.wait_for(lambda: os.path.exists(self.t3_log)))
        sent = open(self.t3_log).read()
        self.assertIn("thread|send|thread-abc-123|PHONE RUN END id=%s owner=gpu rc=3 ended=normal park=fastboot" % rid, sent)
        last = self.pl("follow", "last", "--owner", "gpu", "--timeout", "5")
        self.assertEqual(last.returncode, 3)
        self.assertIn("run %s ended rc=3" % rid, self.pl("runs").stdout)

    def test_detach_keeps_env_and_cwd(self):
        env = dict(self.env, MY_VAR="kept-42")
        r = subprocess.run([sys.executable, TOOL, "run", "--owner", "e", "--detach", "--notify", "none", "--", "sh", "-c", "echo var=$MY_VAR cwd=$(pwd) tok=${PHONE_LOCK_TOKEN:+set}"],
                           env=env, capture_output=True, text=True, timeout=30, cwd=self.d.name)
        rid = [l for l in r.stdout.splitlines() if l.startswith("DETACHED ")][0].split()[1].split("=", 1)[1]
        f = self.pl("follow", rid, "--timeout", "30", timeout=60)
        self.assertEqual(f.returncode, 0, f.stdout)
        self.assertIn("var=kept-42 cwd=%s tok=set" % os.path.realpath(self.d.name), f.stdout)
        self.assertFalse(os.path.exists(self.t3_log))

    def test_detached_run_survives_caller_kill_and_end_works(self):
        r = self.pl("run", "--owner", "bt", "--detach", "--notify", "none", "--", "sleep", "60")
        rid = [l for l in r.stdout.splitlines() if l.startswith("DETACHED ")][0].split()[1].split("=", 1)[1]
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        fol = self.bg("follow", rid, "--timeout", "60")
        time.sleep(0.5)
        os.killpg(fol.pid, signal.SIGKILL)   # the agent's background follower dies: the run must not notice
        fol.wait()
        time.sleep(0.5)
        self.assertIsNotNone(self.info())
        e = self.pl("end", "--owner", "bt", "--reason", "done")
        self.assertEqual(e.returncode, 0, e.stdout + e.stderr)
        f = self.pl("follow", rid, "--timeout", "30", timeout=60)
        self.assertIn("ended=end request by bt: done", f.stdout)
        self.assertEqual(self.parks(), ["held"])

    def test_follow_reports_gone_run(self):
        d = os.path.join(self.lock + ".runs", "20260101T000000Z-x-dead")
        os.makedirs(d)
        with open(os.path.join(d, "pid"), "w") as fh:
            fh.write("999999")
        f = self.pl("follow", "20260101T000000Z-x-dead", "--timeout", "30", timeout=60)
        self.assertEqual(f.returncode, 2)
        self.assertIn("ended=GONE", f.stdout)

    def test_seams_refused_outside_sandbox(self):
        env = dict(self.env)
        env.pop("PHONE_LOCK_DIR")
        r = self.pl("runs", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SIM SEAM REFUSED: PHONE_LOCK_PARK", r.stderr)

    def test_real_lock_never_parked_from_test_dir(self):
        env = dict(self.env)
        env.pop("PHONE_LOCK_PARK")
        r = self.pl("run", "--owner", "a", "--", "true", env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("PARK skipped after command exit 0 (test lock dir", r.stdout)

    STD = "a" * 64
    OTHER = "b" * 64

    def root_setup(self, state_lines, mode="ok", slow=False):
        """standard root = STD; fake fflash logs its args + lock check + info.json phase/pgid/deadline, then appends STD to the state file (mode ok),
        exits 1 (fail) or exits 0 without writing the state (noappend)."""
        d = self.d.name
        self.img = os.path.join(d, "std.img")
        open(self.img, "w").close()
        with open(self.img + ".sha256", "w") as f:
            f.write("%s  std.img\n" % self.STD)
        with open(os.path.join(d, "standard-root.txt"), "w") as f:
            f.write("# comment\n%s %s\n" % (self.STD, self.img))
        self.state = os.path.join(d, "flashed.txt")
        with open(self.state, "w") as f:
            f.write("".join(l + "\n" for l in state_lines))
        self.rlog, self.ilog, self.done = (os.path.join(d, n) for n in ("restore.log", "info.log", "done"))
        fake = os.path.join(d, "fake-restore")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nif %s check >/dev/null 2>&1; then h=held; else h=NOT-HELD; fi\necho \"$h|$PREFLIGHT_GATE_WAIVER|$*\" >> %s\n"
                    "python3 -c 'import json,os,sys;i=json.load(open(os.environ[\"PHONE_LOCK_DIR\"]+\"/info.json\"));print(i.get(\"phase\"),i.get(\"pgid\"),i.get(\"deadline\"),sys.argv[1])' $$ >> %s\n"
                    % (TOOL, self.rlog, self.ilog))
            if slow:
                f.write("for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do echo progress $i; sleep 0.1; done | tee /dev/null\n")
            if mode == "ok":
                f.write("echo '%s 2026 x' >> %s\n" % (self.STD, self.state))
            f.write("touch %s\necho 'RESULT: %s'\nexit %d\n" % (self.done, "FAIL refused by fake" if mode == "fail" else "PASS flashed", 1 if mode == "fail" else 0))
        os.chmod(fake, 0o755)
        self.env.update(PHONE_LOCK_RESTORE=fake, STANDARD_ROOT=os.path.join(d, "standard-root.txt"), FLASH_STATE=self.state)

    def restores(self):
        try:
            with open(self.rlog) as f:
                return f.read().splitlines()
        except OSError:   # lint-silent: expected the fake restore never ran (test helper)
            return []

    def inproc(self, code, env=None):
        """run CODE with the phone-lock module loaded (R = its root module) in a child python: for the checks no CLI path reaches."""
        pre = ("import importlib.machinery, importlib.util, os, sys\nl = importlib.machinery.SourceFileLoader('pl', %r)\nsp = importlib.util.spec_from_loader('pl', l)\n"
               "m = importlib.util.module_from_spec(sp); sys.modules['pl'] = m; l.exec_module(m); R = m.ple.R; C = m\n" % TOOL)
        return subprocess.run([sys.executable, "-c", pre + code], env=env or self.env, capture_output=True, text=True, timeout=60)

    def dead_run_with_notify(self):
        """a detached run whose run process and command are SIGKILLed: the lock record is left for the tick."""
        r = self.pl("run", "--owner", "dd", "--detach", "--notify", "thread:t-dead", "--", "sleep", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid") and (self.info() or {}).get("notify")))
        i = self.info()
        os.kill(i["pid"], signal.SIGKILL)
        os.killpg(i["pgid"], signal.SIGKILL)
        def gone():
            try:
                os.kill(i["pid"], 0)
                return False
            except OSError:   # lint-silent: expected the killed holder is gone (test helper)
                return True
        self.assertTrue(self.wait_for(gone))   # no `status` here: it would reap the lock itself
        return i

    def test_root_restored_when_not_standard(self):
        self.root_setup([self.OTHER + " t x"])
        r = self.pl("run", "--owner", "a", "--max", "5", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rs = self.restores()
        self.assertEqual(len(rs), 1, r.stdout)
        held, waiver, args = rs[0].split("|", 2)
        self.assertEqual(held, "held")   # fflash's `phone-lock check` passes with the run's own token
        self.assertIn("user 2026-11-11", waiver)
        self.assertTrue(args.startswith(self.img + " --yes-erase-userdata --reason phone-lock park: standard root back after a run "), args)
        self.assertIn("(command exit 0) (user 2026-11-11)", args)
        self.assertRegex(r.stdout, r"PARK ROOT restored aaaaaaaaaaaa in \d+ s")
        self.assertIn("root=restored", r.stdout)
        self.assertIsNone(self.info())
        # during the flash: phase=restore, the lock's pgid is the flash's own group (the fake's pid), the deadline was moved out past the run's 5 s
        with open(self.ilog) as f:
            phase, pgid, dl, pid = f.read().split()
        self.assertEqual((phase, pgid), ("restore", pid))
        self.assertGreater(int(dl), time.time() + 1000)

    def test_run_restore_resets_phase_and_pgid(self):
        holder = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(holder)
        self.assertEqual(self.pl("acquire", "--owner", "z", "--pid", str(holder.pid), "--pgid", "0", "--token", "TOK").returncode, 0)
        r = self.inproc("R.run_restore(['true'], dict(os.environ), 'TOK'); i = C.read(); print('AFTER', i.get('phase'), i.get('pgid'))")
        self.assertIn("AFTER park 0", r.stdout, r.stdout + r.stderr)

    def test_root_already_standard_no_flash(self):
        self.root_setup(["flashing t x", self.OTHER + " t x", "", self.STD + " t x", ""])
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(self.restores(), [])
        self.assertIn("PARK ROOT ok standard aaaaaaaaaaaa", r.stdout)

    def test_root_flashing_newest_line_restores(self):
        self.root_setup([self.STD + " t x", "flashing 2026 /x/root.img"])
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(len(self.restores()), 1, r.stdout)
        self.assertIn("PARK ROOT restored", r.stdout)

    def test_root_not_attempted_when_park_failed(self):
        self.root_setup([self.OTHER + " t x"])
        bad = os.path.join(self.d.name, "bad-park")
        with open(bad, "w") as f:
            f.write("#!/bin/sh\necho 'RESULT: FAIL no fastboot'\nexit 1\n")
        os.chmod(bad, 0o755)
        self.env["PHONE_LOCK_PARK"] = bad
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(self.restores(), [])
        self.assertIn("PARK ROOT not attempted", r.stdout)
        self.assertIsNone(self.info())

    def test_root_restore_failure_is_loud_and_lock_released(self):
        self.root_setup([self.OTHER + " t x"], mode="fail")
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(len(self.restores()), 1)
        self.assertRegex(r.stdout, r"PARK ROOT FAIL rc=1 RESULT: FAIL refused by fake NEED USER")
        self.assertIn('PREFLIGHT_GATE_WAIVER="user: standard root restore" tools/phone-lock run --owner NAME -- tools/fflash-userdata.sh ' + self.img, r.stdout)
        self.assertIsNone(self.info())
        self.assertIn("root=FAIL", r.stdout)
        self.assertTrue([h for h in self.history() if h["event"] == "RUN-END" and "root=FAIL" in h["note"]])

    def test_root_failure_recorded_in_end_json(self):
        self.root_setup([self.OTHER + " t x"], mode="fail")
        r = self.pl("run", "--owner", "a", "--detach", "--", "true")
        rid = r.stdout.split("DETACHED id=")[1].split()[0]
        f = self.pl("follow", rid, "--timeout", "60")
        self.assertIn("PARK ROOT FAIL", f.stdout)
        with open(os.path.join(self.lock + ".runs", rid, "end.json")) as fh:
            self.assertEqual(json.load(fh)["root"], "FAIL")
        self.assertIsNone(self.info())

    def test_root_state_not_standard_after_flash_is_fail(self):
        self.root_setup([self.OTHER + " t x"], mode="noappend")
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertIn("PARK ROOT FAIL flash said ok but the state file's newest line is bbbbbbbbbbbb", r.stdout)

    def test_park_command_exit_code_follows_root_fail(self):
        self.root_setup([self.OTHER + " t x"], mode="fail")
        r = self.pl("park", "--owner", "x")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("PARK ROOT FAIL", r.stdout)
        self.assertIsNone(self.info())

    def test_root_image_or_sidecar_missing_or_wrong(self):
        self.root_setup([self.OTHER + " t x"])
        os.unlink(self.img + ".sha256")
        self.assertIn("PARK ROOT FAIL standard root image or its .sha256 is missing", self.pl("run", "--owner", "a", "--", "true").stdout)
        with open(self.img + ".sha256", "w") as f:
            f.write("%s  std.img\n" % self.OTHER)
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertIn("is bbbbbbbbbbbb, not the standard sha aaaaaaaaaaaa", r.stdout)
        self.assertEqual(self.restores(), [])

    def test_root_off_is_loud(self):
        self.root_setup([self.OTHER + " t x"])
        self.env["PHONE_LOCK_RESTORE_ROOT"] = "0"
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertEqual(self.restores(), [])
        self.assertIn("PARK ROOT off: PHONE_LOCK_RESTORE_ROOT=0", r.stdout)
        env = dict(self.env)
        env.pop("PHONE_LOCK_DIR")
        self.assertEqual(self.pl("runs", env=env).returncode, 2)   # outside the sandbox the opt-out is refused like every seam

    def test_root_gap_standard_file_parsed_like_fflash(self):
        for text in ("# nothing valid\nnot-a-sha /x\n", "%s\t/x/root.img\n" % self.STD, " %s /x/root.img\n" % self.STD):
            self.root_setup([self.OTHER + " t x"])
            with open(self.env["STANDARD_ROOT"], "w") as f:
                f.write(text)
            r = self.pl("run", "--owner", "a", "--", "true")
            self.assertEqual(self.restores(), [], text)
            self.assertIn("PARK ROOT GAP", r.stdout)

    def test_test_lock_never_reaches_the_real_fflash(self):
        # guard 1: PARK seam set, no RESTORE seam, real-looking files: skipped, nothing ran
        self.root_setup([self.OTHER + " t x"])
        self.env.pop("PHONE_LOCK_RESTORE")
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertIn("PARK ROOT skipped (test lock dir", r.stdout)
        self.assertEqual(self.restores(), [])
        # guard 2 (root_step itself): even if the plan says "need", the real flash path is refused for a test lock dir; a recorder stands in for the real tool
        rec = os.path.join(self.d.name, "recorder")
        with open(rec, "w") as f:
            f.write("#!/bin/sh\ntouch %s.called\n" % rec)
        os.chmod(rec, 0o755)
        r = self.inproc("R.FFLASH = %r; R.root_plan = lambda: ('need', '', %r, %r, %r, %r); R.root_step('w', 'o', 'id'); print(R.ROOT)" % (
            rec, self.STD, self.img, self.state, self.env["STANDARD_ROOT"]))
        self.assertIn("PARK ROOT FAIL refused: the real fflash-userdata.sh must not run for a test lock dir", r.stdout, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(rec + ".called"))

    def test_root_restore_timeout_fails_loud(self):
        self.root_setup([self.OTHER + " t x"])
        slow = os.path.join(self.d.name, "slow")
        with open(slow, "w") as f:
            f.write("#!/bin/sh\nsleep 30\n")
        os.chmod(slow, 0o755)
        self.env.update(PHONE_LOCK_RESTORE=slow, PHONE_LOCK_RESTORE_MAX="1")
        r = self.pl("run", "--owner", "a", "--", "true")
        self.assertIn("PARK ROOT FAIL rc=-1", r.stdout)
        self.assertIsNone(self.info())

    def test_killed_holder_does_not_kill_the_flash(self):
        self.root_setup([self.OTHER + " t x"], slow=True)
        p = self.bg("run", "--owner", "a", "--", "true")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("phase") == "restore"))
        os.kill(self.info()["pid"], signal.SIGKILL)
        p.wait(timeout=10)
        time.sleep(0.5)
        self.assertFalse(os.path.exists(self.done))
        self.assertIsNotNone(self.info())   # the flash's group keeps the lock
        self.assertIn("held by a", self.pl("status").stdout)
        self.assertTrue(self.wait_for(lambda: os.path.exists(self.done), 20), "the flash was killed with its holder")
        with open(self.state) as f:
            self.assertTrue(f.read().rstrip().endswith("x"))   # the fake completed: STD line appended
        self.assertTrue(self.wait_for(lambda: self.pl("status").stdout.startswith("free")))

    def test_end_request_triggers_restore_and_says_so(self):
        self.root_setup([self.OTHER + " t x"])
        p = self.bg("run", "--owner", "gpu", "--", "sleep", "60")
        self.assertTrue(self.wait_for(lambda: (self.info() or {}).get("pgid")))
        r = self.pl("end", "--owner", "gpu", "--reason", "done early", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("standard root is flashed back", r.stdout)
        out = p.communicate(timeout=30)[0]
        self.assertEqual(len(self.restores()), 1, out)
        self.assertIn("(end request by gpu: done early)", self.restores()[0])
        self.assertIn("PARK ROOT restored", out)
        self.assertIsNone(self.info())

    def test_park_command_and_tick_restore(self):
        self.root_setup([self.OTHER + " t x"])
        r = self.pl("park", "--owner", "x")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(self.restores()), 1)
        self.assertTrue(self.restores()[0].startswith("held|"))   # the park lock's own token passes the check
        with open(self.state, "a") as f:
            f.write(self.OTHER + " t x\n")
        holder = subprocess.Popen(["sleep", "0.2"], start_new_session=True)
        self.assertEqual(self.pl("acquire", "--owner", "y", "--pid", str(holder.pid), "--pgid", str(holder.pid)).returncode, 0)
        holder.wait()
        r = self.pl("tick")
        self.assertEqual(len(self.restores()), 2)
        self.assertIn("after y run", self.restores()[1])
        self.assertIn("root=restored", r.stdout)

    def test_held_labels_on_end_hard_limit_and_dead_end_paths(self):
        self.root_setup([self.OTHER + " t x"])
        holder = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.procs.append(holder)
        self.assertEqual(self.pl("acquire", "--owner", "x", "--pid", str(holder.pid), "--pgid", str(holder.pid), "--ttl", "7200").returncode, 0)
        self.assertEqual(self.pl("end", "--owner", "x", "--reason", "stop the bare xrun").returncode, 0)
        self.assertIn("after x run - (end of x)", self.restores()[-1])
        with open(self.state, "a") as f:
            f.write(self.OTHER + " t x\n")
        dead = subprocess.Popen(["sleep", "0.2"], start_new_session=True)
        self.assertEqual(self.pl("acquire", "--owner", "w", "--pid", str(dead.pid), "--pgid", str(dead.pid)).returncode, 0)
        dead.wait()
        self.assertEqual(self.pl("end", "--owner", "w", "--reason", "gone").returncode, 0)
        self.assertIn("after w run - (end after a dead holder (w))", self.restores()[-1])
        with open(self.state, "a") as f:
            f.write(self.OTHER + " t x\n")
        hung = subprocess.Popen(["sh", "-c", "trap '' TERM; while :; do sleep 0.2; done"], start_new_session=True)
        self.procs.append(hung)
        self.assertEqual(self.pl("acquire", "--owner", "h", "--pid", str(hung.pid), "--pgid", str(hung.pid), "--max", "1").returncode, 0)
        time.sleep(1.5)
        env = dict(self.env, PHONE_LOCK_TICK_GRACE="0")
        self.assertIn("park=fastboot", self.pl("tick", env=env).stdout)
        self.assertIn("after h run - (hard limit of h)", self.restores()[-1])

    def test_tick_root_fail_is_loud(self):
        self.root_setup([self.OTHER + " t x"], mode="fail")
        i = self.dead_run_with_notify()
        r = self.pl("tick")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("root=FAIL", r.stdout)
        self.assertTrue([h for h in self.history() if h["event"] == "ROOT-FAIL" and h["holder"]["owner"] == "dd"])
        with open(self.t3_log) as f:
            self.assertIn("thread|send|t-dead|PHONE RUN ROOT FAIL", f.read())

    def test_tick_hard_limit_root_fail_exit_code(self):
        self.root_setup([self.OTHER + " t x"], mode="fail")
        hung = subprocess.Popen(["sh", "-c", "trap '' TERM; while :; do sleep 0.2; done"], start_new_session=True)
        self.procs.append(hung)
        self.assertEqual(self.pl("acquire", "--owner", "h", "--pid", str(hung.pid), "--pgid", str(hung.pid), "--max", "1").returncode, 0)
        time.sleep(1.5)
        r = self.pl("tick", env=dict(self.env, PHONE_LOCK_TICK_GRACE="0"))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("root=FAIL", r.stdout)

    def test_notice_and_stop_timeout_carry_root(self):
        self.root_setup([self.OTHER + " t x"])
        env = dict(self.env, T3CODE_THREAD_ID="t-live")
        r = self.pl("run", "--owner", "a", "--detach", "--", "true", env=env)
        rid = r.stdout.split("DETACHED id=")[1].split()[0]
        self.pl("follow", rid, "--timeout", "60")
        with open(self.t3_log) as f:
            self.assertRegex(f.read(), r"park=fastboot root=restored held=")
        with open(os.path.join(self.d.name, "sr.args")) as f:
            import phone_lock_end as ple
            self.assertIn("TimeoutStopSec=%d" % (ple.PARK_BUDGET + 60), f.read())

    def test_default_end_timeout_covers_the_chain(self):
        sys.path.insert(0, HERE)
        import phone_lock_end as ple
        self.assertGreaterEqual(ple.END_TIMEOUT, 40 + ple.PARK_GOTO + 60 + ple.RESTORE_MAX)
        self.assertGreater(ple.PARK_BUDGET + 60, 420)



class NoticeOwner(unittest.TestCase):   # coordinator 2026-11-12: a replaced thread's holds kept waking it; resolved at SEND time
    def setUp(self):
        import phone_lock_end
        self.ple = phone_lock_end
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.log = os.path.join(self.d.name, "t3.log")
        t3 = os.path.join(self.d.name, "fake-t3")
        with open(t3, "w") as f:
            f.write("#!/bin/sh\nprintf '%%s|' \"$@\" >> %s\n" % self.log)
        os.chmod(t3, 0o755)
        od = os.path.join(self.d.name, "owners"); os.makedirs(od)
        with open(os.path.join(od, "all-on"), "w") as f:
            f.write("# owner of all-on\nmcp:new-thread\nformer mcp:old-thread\n")
        with open(os.path.join(od, "orphan"), "w") as f:
            f.write("# no current owner\nformer mcp:gone-thread\n")
        for k, v in (("PHONE_LOCK_T3", t3), ("PIXOS_OWNERS_DIR", od)):
            self.addCleanup(lambda k=k, o=os.environ.get(k): os.environ.__setitem__(k, o) if o is not None else os.environ.pop(k, None))
            os.environ[k] = v

    def sent(self):
        return open(self.log).read() if os.path.exists(self.log) else ""

    def test_former_owner_reroutes_to_the_current_one(self):
        self.assertEqual(self.ple.notify("thread:mcp:old-thread", "PHONE RUN END id=x"), 0)
        self.assertIn("|mcp:new-thread|", "|" + self.sent())
        self.assertNotIn("old-thread", self.sent())

    def test_former_owner_without_current_is_not_sent(self):
        self.assertEqual(self.ple.notify("thread:mcp:gone-thread", "PHONE RUN END id=x"), 3)
        self.assertEqual(self.sent(), "")

    def test_unknown_thread_keeps_its_own_target(self):
        self.assertEqual(self.ple.notify("thread:mcp:someone", "PHONE RUN END id=x"), 0)
        self.assertIn("mcp:someone", self.sent())


if __name__ == "__main__":
    unittest.main()
