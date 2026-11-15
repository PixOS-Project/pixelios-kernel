"""Fast checks of tools/cpu_timeout.py (AP-105)."""
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu_timeout as ct


def _stop_cont(delay, hold):
    """SIGSTOP the newest session leader child of this process after DELAY s, SIGCONT after HOLD more s."""
    def go():
        time.sleep(delay)
        me = os.getpid()
        for e in os.listdir("/proc"):
            if e.isdigit():
                st = ct._stat(e)
                if st and st[1] == int(e):
                    with open("/proc/%s/stat" % e) as f:
                        if f.read().rsplit(")", 1)[1].split()[1] == str(me):
                            os.killpg(int(e), signal.SIGSTOP)
                            time.sleep(hold)
                            os.killpg(int(e), signal.SIGCONT)
    t = threading.Thread(target=go)
    t.start()
    return t


class CpuTimeout(unittest.TestCase):
    def test_stopped_sleeper_does_not_time_out(self):
        t = _stop_cont(0.3, 1.5)
        t0 = time.monotonic()
        rc = ct.run_cpu_limited([sys.executable, "-c", "import time; time.sleep(1.2)"], 1, wall_factor=1, poll=0.1)
        t.join()
        self.assertEqual(rc, 0)
        self.assertGreater(time.monotonic() - t0, 1.5)   # wall backstop is 1 s, stopped time is not counted

    def test_busy_loop_hits_cpu_limit(self):
        # AP-152: the old bound was wall time (< 4 s; a loaded gate host measured 4.02 s). The property is "the watcher kills the child soon
        # after it used 1 s of CPU", so bound the CPU the reaped child actually used: < 2 s (10 polls of slack) is below the RLIMIT_CPU soft limit (limit + 2 = 3 s,
        # cpu_timeout.cap_cpu), so the watcher, not the rlimit, must have stopped it; host load cannot move a CPU-time figure.
        c0 = os.times()
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited([sys.executable, "-c", "while True: pass"], 1, poll=0.1)
        c1 = os.times()
        self.assertEqual(cm.exception.kind, "cpu")
        used = (c1.children_user - c0.children_user) + (c1.children_system - c0.children_system)
        self.assertGreater(used, 1.0)   # it really ran to the limit (the child is reaped by run_cpu_limited, so its CPU is in children_*)
        self.assertLess(used, 2.0)
        self.assertIn("cpu limit", str(cm.exception))

    def test_sleeper_dies_at_wall_backstop(self):
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited([sys.executable, "-c", "import time; time.sleep(60)"], 1, wall_factor=2, poll=0.1)
        self.assertEqual(cm.exception.kind, "wall-backstop")

    def test_children_cpu_counts(self):
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited(["sh", "-c", "%s -c 'while True: pass'; true" % sys.executable], 1, poll=0.1)
        self.assertEqual(cm.exception.kind, "cpu")

    def test_returncode_passthrough(self):
        self.assertEqual(ct.run_cpu_limited(["sh", "-c", "exit 3"], 5, poll=0.1), 3)


BUSY = "import time; t=time.process_time()\nwhile time.process_time()-t<1.2: pass"


def _session_procs(needle):
    """Pids whose cmdline contains NEEDLE (a unique sleep argument)."""
    out = []
    for e in os.listdir("/proc"):
        if e.isdigit():
            try:
                with open("/proc/%s/cmdline" % e, "rb") as f:
                    if needle.encode() in f.read():
                        out.append(int(e))
            except OSError:   # lint-silent: expected a /proc entry that vanished while scanning is not the needle
                pass
    return out


class CpuTimeoutReview(unittest.TestCase):
    """Review cpu-timeout-r1 tests (a)-(f)."""
    def tearDown(self):
        for n in ("1234", "1235", "1236"):
            for pid in _session_procs("sleep\x00" + n):
                os.kill(pid, signal.SIGKILL)

    def test_a_root_cutime(self):                      # exited children are only in the root's cutime
        q = "%s -c '%s'" % (sys.executable, BUSY)
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited(["sh", "-c", "%s; %s; sleep 30" % (q, q)], 2, wall_factor=10, poll=0.1)
        self.assertEqual(cm.exception.kind, "cpu")

    def test_b_nested_shell_cutime(self):              # F2: non-root member's cutime
        q = "%s -c \"%s\"" % (sys.executable, BUSY.replace("\n", "; ").replace("; while", "\nwhile"))
        inner = "%s; %s; sleep 30" % (q, q)
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited(["sh", "-c", "sh -c '%s'" % inner.replace("'", "'\\''")], 2, wall_factor=3, poll=0.1)
        self.assertEqual(cm.exception.kind, "cpu")

    def test_c_no_straggler_after_normal_exit(self):   # _finish killpg
        self.assertEqual(ct.run_cpu_limited(["sh", "-c", "sleep 1234 & exit 0"], 5, poll=0.1), 0)
        time.sleep(0.3)
        self.assertEqual(_session_procs("sleep\x001234"), [])

    def test_c2_no_straggler_after_raise(self):
        with self.assertRaises(ct.CpuLimitExpired):
            ct.run_cpu_limited(["sh", "-c", "sleep 1235 & while :; do :; done"], 1, poll=0.1)
        time.sleep(0.3)
        self.assertEqual(_session_procs("sleep\x001235"), [])

    def test_c3_root_exited_before_cleanup(self):      # F3: killpg even when the root is already gone
        orig = ct.tree_cpu
        def slow(root):
            time.sleep(0.6)                            # root exits (zombie) meanwhile
            return 999.0, "S"
        ct.tree_cpu = slow
        try:
            with self.assertRaises(ct.CpuLimitExpired):
                ct.run_cpu_limited(["sh", "-c", "sleep 1236 & sleep 0.2"], 1, poll=0.1)
        finally:
            ct.tree_cpu = orig
        time.sleep(0.3)
        self.assertEqual(_session_procs("sleep\x001236"), [])

    def test_d_rlimit_cpu(self):                       # poll too long for the watcher; RLIMIT_CPU must fire
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited([sys.executable, "-c", "while True: pass"], 1, poll=30)
        self.assertEqual(cm.exception.kind, "rlimit-cpu")

    def test_e_self_sigstop_ends(self):                # F1
        t0 = time.monotonic()
        with self.assertRaises(ct.CpuLimitExpired) as cm:
            ct.run_cpu_limited([sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGSTOP)"], 1, poll=0.1, stop_cap=1)
        self.assertEqual(cm.exception.kind, "stopped")
        self.assertLess(time.monotonic() - t0, 6)

    def test_f_poll_step_cap(self):                    # a descheduled watcher must not add a huge wall step
        class FakeTime:
            n = 0
            @staticmethod
            def monotonic():
                FakeTime.n += 1
                return FakeTime.n * 1000.0
        orig = ct.time
        ct.time = FakeTime
        t0 = time.monotonic()
        try:
            with self.assertRaises(ct.CpuLimitExpired) as cm:
                ct.run_cpu_limited(["sleep", "30"], 1, wall_factor=2, poll=0.1)
        finally:
            ct.time = orig
        self.assertEqual(cm.exception.kind, "wall-backstop")
        self.assertGreater(time.monotonic() - t0, 0.45)   # 2 s backstop / 0.4 s per capped step = 6 polls


if __name__ == "__main__":
    unittest.main()
