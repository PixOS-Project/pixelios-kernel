#!/usr/bin/env python3
"""Host test for the phone-lock waiter wake (R37, T-a5b5): a stub HTTP server stands in for the T3 /api/hooks/:id/:token endpoint.
Needs the super repo's tools/wake (T-76a1): $PHONE_LOCK_WAKE or found through the git common dir; missing = loud failure, not a skip.
Last line: RESULT: PASS phone-lock-wake | RESULT: FAIL phone-lock-wake (N failures)."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import http.server, json, os, pathlib, shutil, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "phone-lock")
TOKEN = "tok" + "B" * 40
SEEN = []   # (path, body) of every POST the stub received


class Stub(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        SEEN.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
        mode = self.path.split("/")[3].split("%3A")[-1]   # task-id part picks the answer
        code = {"ok": 202, "gone": 404, "off": 409}.get(mode, 500)
        self.send_response(code); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        SEEN.clear()
        self.d = tempfile.TemporaryDirectory()
        self.sec = os.path.join(self.d.name, "sec"); os.mkdir(self.sec)
        self.lock = os.path.join(self.d.name, "phone.lock")
        self.env = {k: v for k, v in os.environ.items() if k == "PHONE_LOCK_WAKE" or not k.startswith(("PHONE_LOCK_", "WAKE_"))}   # PHONE_LOCK_WAKE from the caller stays: the remote gate container has no super repo to find tools/wake in (T-78bc)
        self.env.update(PHONE_LOCK_DIR=self.lock, WAKE_SECRETS_DIR=self.sec, WAKE_BASE="http://127.0.0.1:%d" % self.port,
                        WAKE_LOG=os.path.join(self.d.name, "wake.log"), WAKE_TIMEOUT="2")
        self.holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])   # a live pid keeps the lock
        self.addCleanup(self.holder.wait); self.addCleanup(self.holder.kill)
        self.addCleanup(self.d.cleanup)

    def hook(self, label, mode):
        p = pathlib.Path(self.sec) / ("%s.hook" % label)
        p.write_text("scheduled-task%%3A%s/%s\n" % (mode, TOKEN)); p.chmod(0o600)

    def pl(self, *a, env=None):
        r = subprocess.run([sys.executable, TOOL, *a], env=env or self.env, capture_output=True, text=True, timeout=60)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        return r

    def hold(self, ttl=1800):
        r = self.pl("acquire", "--owner", "holder", "--pid", str(self.holder.pid), "--ttl", str(ttl), "--token", "T1")
        self.assertEqual(r.returncode, 0, r.stdout)

    def waiter(self, role, label, mode):
        self.hook(label, mode)
        r = self.pl("waiter", "add", "--role", role, "--hook", label)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_release_posts_exactly_once_and_removes_waiter(self):
        self.hold(); self.waiter("w1", "hk1", "ok")
        r = self.pl("release", "--token", "T1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WAKE ok: role=w1 hook=hk1", r.stdout)
        self.assertEqual(len(SEEN), 1)
        self.assertEqual(SEEN[0][0], "/api/hooks/scheduled-task%3Aok/" + TOKEN)
        self.assertEqual(SEEN[0][1]["event"], "phone-lock released")
        self.assertEqual(self.pl("waiter", "list").stdout.count("waiter role="), 0)
        self.hold(); self.pl("release", "--token", "T1")
        self.assertEqual(len(SEEN), 1, "a removed waiter must not be woken twice")
        with open(self.env["WAKE_LOG"]) as f:
            self.assertNotIn(TOKEN, f.read())

    def test_oldest_waiter_only(self):
        self.hold(); self.waiter("w1", "hk1", "ok"); self.waiter("w2", "hk2", "ok")
        self.pl("release", "--token", "T1")
        self.assertEqual(len(SEEN), 1)
        self.assertIn("role=w2", self.pl("waiter", "list").stdout)

    def test_failures_print_wake_fail_exit_3_and_keep_waiter(self):
        for mode, code in (("gone", "404"), ("off", "409"), ("odd", "500")):
            with self.subTest(mode=mode):
                self.pl("waiter", "rm", "--role", "w1")
                self.hold(); self.waiter("w1", "hk1", mode)
                r = self.pl("release", "--token", "T1")
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertIn("WAKE FAIL: %s hook=hk1" % code, r.stdout)
                self.assertTrue(r.stdout.splitlines()[-1].startswith("RESULT: FAIL"), r.stdout)
                self.assertIn("role=w1", self.pl("waiter", "list").stdout)   # polling fallback stays valid

    def test_refused_prints_wake_fail(self):
        self.hold(); self.waiter("w1", "hk1", "ok")
        env = dict(self.env, WAKE_BASE="http://127.0.0.1:1")
        r = self.pl("release", "--token", "T1", env=env)
        self.assertEqual(r.returncode, 3)
        self.assertIn("WAKE FAIL: noanswer hook=hk1", r.stdout)

    def test_missing_hook_file_is_loud(self):
        self.hold()
        self.pl("waiter", "add", "--role", "w1", "--hook", "nohook")
        r = self.pl("release", "--token", "T1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("WAKE FAIL: off hook=nohook", r.stdout)

    def test_tool_missing_is_loud(self):
        self.hold(); self.waiter("w1", "hk1", "ok")
        r = self.pl("release", "--token", "T1", env=dict(self.env, PHONE_LOCK_WAKE="/nonexistent/wake"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("WAKE FAIL: tool-missing hook=hk1", r.stdout)

    def test_default_wake_tool_found_from_the_repo(self):
        # never skipped (T-78bc): a fake super/pixos-project/pixelios-kernel layout in the tmp dir, so the git-common-dir resolution is
        # exercised the same way on a host with the super repo and in the remote gate container (which has only a PHONE_LOCK_WAKE copy).
        src = os.environ.get("PHONE_LOCK_WAKE")
        if not src:
            c = subprocess.run(["git", "-C", HERE, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
            src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(c))), "tools", "wake") if c else ""
        self.assertTrue(os.path.isfile(src), "tools/wake not found (set PHONE_LOCK_WAKE): %r" % src)
        sup = os.path.join(self.d.name, "super"); kern = os.path.join(sup, "pixos-project", "pixelios-kernel")
        os.makedirs(os.path.join(sup, "tools")); os.makedirs(os.path.join(kern, "tools"))
        shutil.copy(src, os.path.join(sup, "tools", "wake")); shutil.copy(TOOL, os.path.join(kern, "tools", "phone-lock")); shutil.copy(os.path.join(HERE, "simseams.py"), os.path.join(kern, "tools", "simseams.py")); shutil.copy(os.path.join(HERE, "phone_lock_end.py"), os.path.join(kern, "tools", "phone_lock_end.py")); shutil.copy(os.path.join(HERE, "phone_lock_root.py"), os.path.join(kern, "tools", "phone_lock_root.py"))
        subprocess.run(["git", "init", "-q", kern], check=True)
        env = {k: v for k, v in self.env.items() if k != "PHONE_LOCK_WAKE"}
        self.hold(); self.waiter("w1", "hk1", "ok")
        r = subprocess.run([sys.executable, os.path.join(kern, "tools", "phone-lock"), "release", "--token", "T1"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "default resolution of tools/wake failed:\n" + r.stdout + r.stderr)
        self.assertIn("WAKE ok: role=w1 hook=hk1", r.stdout)

    def test_no_waiter_no_post(self):
        self.hold()
        r = self.pl("release", "--token", "T1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(SEEN, [])

    def test_run_keeps_cmd_exit_code_but_prints_wake_fail(self):
        self.waiter("w1", "hk1", "gone")
        r = self.pl("run", "--owner", "runner", "--", "true")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("WAKE FAIL: 404 hook=hk1", r.stdout)
        self.waiter("w1", "hk1", "ok")
        r = self.pl("run", "--owner", "runner", "--", "true")
        self.assertIn("WAKE ok: role=w1", r.stdout)
        self.assertEqual(len(SEEN), 2)

    def test_live_wait_process_queued_first_is_served_first(self):
        self.hold()
        p = subprocess.Popen([sys.executable, TOOL, "acquire", "--owner", "live", "--wait", "20"], env=self.env, stdout=subprocess.PIPE, text=True)
        time.sleep(0.6)
        self.waiter("w1", "hk1", "ok")
        self.assertIn("role=w1", self.pl("waiter", "list").stdout)
        r = self.pl("release", "--token", "T1")
        out, _ = p.communicate(timeout=20)
        self.assertEqual(SEEN, [], "the live waiter queued first must not be bypassed")
        self.assertIn("wake skipped", r.stdout)

    def test_tick_overdue_wakes_once_per_lock(self):
        self.hold(ttl=1); self.waiter("w1", "hk1", "ok"); self.waiter("w2", "hk2", "ok")
        r = self.pl("tick")
        self.assertEqual(r.returncode, 0); self.assertIn("nothing overdue", r.stdout)
        time.sleep(2.2)
        r = self.pl("tick")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(SEEN), 1); self.assertEqual(SEEN[0][1]["event"], "phone-lock overdue")
        r = self.pl("tick")
        self.assertIn("already announced", r.stdout)
        self.assertEqual(len(SEEN), 1)

    def test_tick_wake_failure_exits_3(self):
        self.hold(ttl=1); self.waiter("w1", "hk1", "gone")
        time.sleep(2.2)
        r = self.pl("tick")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("WAKE FAIL: 404 hook=hk1", r.stdout)

    def test_reregister_replaces_and_bad_labels_refused(self):
        self.waiter("w1", "hk1", "gone"); self.waiter("w1", "hk1b", "ok")
        self.assertEqual(self.pl("waiter", "list").stdout.count("role=w1"), 1)
        self.assertIn("hook=hk1b", self.pl("waiter", "list").stdout)
        r = self.pl("waiter", "add", "--role", "../x", "--hook", "h")
        self.assertEqual(r.returncode, 2)
        mode = oct(os.stat(os.path.join(self.lock + ".waiters", os.listdir(self.lock + ".waiters")[0])).st_mode & 0o777)
        self.assertEqual(mode, "0o600")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    bad = len(res.failures) + len(res.errors)
    print("RESULT: %s phone-lock-wake%s" % ("PASS" if not bad else "FAIL", "" if not bad else " (%d failures)" % bad))
    sys.exit(1 if bad else 0)
