"""Run a command with a CPU-time limit, so a SIGSTOP, host load or a slow disk cannot fail it (AP-105).

The limit counts utime+stime of the whole process session (live processes plus the waited-for children of the root).
A wall-clock backstop (wall_factor x limit) still ends a true hang that uses no CPU; it does not count time in which the
root is stopped (state T) or this watcher itself was not scheduled (one poll step is capped at 4 x poll).
Stopped time is bounded by an absolute cap, STOP_CAP = 6 h (kind 'stopped'), so a child that SIGSTOPs itself cannot live forever;
external SIGSTOP/SIGCONT pauses shorter than the cap are free."""
import os
import signal
import subprocess
import sys
import time

TCK = os.sysconf("SC_CLK_TCK")
STOP_CAP = 6 * 3600     # seconds a stopped root may stay stopped (review cpu-timeout-r1 F1; AP-105 pauses are minutes)


class CpuLimitExpired(subprocess.TimeoutExpired):
    """TimeoutExpired whose .kind says which guard fired: 'cpu', 'wall-backstop' or 'stopped' (and 'rlimit-cpu' for SIGXCPU)."""
    def __init__(self, cmd, timeout, kind):
        super().__init__(cmd, timeout)
        self.kind = kind

    def __str__(self):
        return "%s limit %s s (%s)" % (self.kind, self.timeout, " ".join(map(str, self.cmd))[:80])


def _stat(pid):
    """(state, session, own utime+stime, cutime+cstime) in seconds, from /proc/PID/stat fields 3, 6, 14-17; None if gone."""
    try:
        with open("/proc/%s/stat" % pid) as f:
            s = f.read()
        r = s[s.rindex(")") + 2:].split()
        return r[0], int(r[3]), (int(r[11]) + int(r[12])) / TCK, (int(r[13]) + int(r[14])) / TCK
    except (FileNotFoundError, ProcessLookupError):   # the normal answer: the process exited between listdir and open (None = gone)   # lint-silent: expected the process or group already exited, the normal case
        return None
    except (OSError, ValueError, IndexError) as _err:
        print(f"cpu_timeout GAP: (OSError, ValueError, IndexError) ignored in _stat(): {_err}", file=sys.stderr)
        return None


def tree_cpu(root):
    """Cumulative CPU seconds of the session led by ROOT; (cpu, root state). Live processes count utime+stime+cutime+cstime; the root's cutime+cstime holds the exited descendants it waited for."""
    me = _stat(root)
    if me is None:
        return None, None
    cpu = me[2] + me[3]
    for e in os.listdir("/proc"):
        if e.isdigit() and int(e) != root:
            st = _stat(e)
            if st is not None and st[1] == root:
                cpu += st[2] + st[3]        # own + reaped descendants (review F2)
    return cpu, me[0]


def cap_cpu(limit):
    """preexec_fn helper: RLIMIT_CPU as the second guard (a little above LIMIT so the watcher reports first)."""
    import resource
    n = int(limit) + 2
    resource.setrlimit(resource.RLIMIT_CPU, (n, n + 5))   # soft<hard: soft==hard sends SIGKILL (observed rc -9), so the SIGXCPU mapping never fired


def run_cpu_limited(cmd, cpu_limit, wall_factor=10, poll=0.5, preexec_fn=None, stop_cap=STOP_CAP, **kw):
    """Like subprocess.run(cmd, timeout=...) but returns the returncode; raises CpuLimitExpired (a TimeoutExpired) with .kind."""
    def pre():
        cap_cpu(cpu_limit)
        if preexec_fn:
            preexec_fn()
    p = subprocess.Popen(cmd, start_new_session=True, preexec_fn=pre, **kw)
    wall, stopped, peak, last = 0.0, 0.0, 0.0, time.monotonic()
    kind = None
    try:
        while True:
            try:
                rc = p.wait(timeout=poll)
            except subprocess.TimeoutExpired:
                rc = None
            if rc is not None:      # outside the try: _finish raises a TimeoutExpired subclass that must not be swallowed
                return _finish(p, cmd, cpu_limit, rc)
            now = time.monotonic()
            cpu, state = tree_cpu(p.pid)
            if state != "T":
                wall += min(now - last, 4 * poll)
            else:
                stopped += min(now - last, 4 * poll)
            last = now
            if cpu is not None:
                peak = max(peak, cpu)           # the sum never decreases when a waited child dies (review F2)
            if peak > cpu_limit:
                kind = "cpu"
            elif stopped > stop_cap:
                kind = "stopped"
            elif wall > wall_factor * cpu_limit:
                kind = "wall-backstop"
            if kind:
                break
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)    # always: members outlive an exited root (review F3)
        except ProcessLookupError:                      # the normal case: group already gone   # lint-silent: expected the process or group already exited, the normal case
            pass
        except OSError as _err:                         # anything else (EPERM) is not normal
            print(f"cpu_timeout GAP: OSError ignored in run_cpu_limited(): {_err}", file=sys.stderr)
        if p.poll() is None:
            p.wait()
    raise CpuLimitExpired(cmd, cpu_limit if kind == "cpu" else stop_cap if kind == "stopped" else wall_factor * cpu_limit, kind)


def _finish(p, cmd, cpu_limit, rc):
    try:
        os.killpg(p.pid, signal.SIGKILL)       # stragglers of the session
    except ProcessLookupError:                 # the normal case after every clean exit: nothing left in the group   # lint-silent: expected the process or group already exited, the normal case
        pass
    except OSError as _err:
        print(f"cpu_timeout GAP: OSError ignored in _finish(): {_err}", file=sys.stderr)
    if rc == -signal.SIGXCPU:
        raise CpuLimitExpired(cmd, cpu_limit, "rlimit-cpu")
    return rc
