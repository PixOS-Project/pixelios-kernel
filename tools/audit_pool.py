#!/usr/bin/env python3
"""Fork pool for the independent mutation self-tests of one tools/test-bluejay-boot.py run (PLAN-GATE-2X K3).

run(body, jobs) calls body(); every dispatch(thunk, label) inside it starts thunk (a pure function of its inputs, normally an
audit_memo.run call) in a forked child, at most `jobs` at a time, and returns at once; the child's stdout is captured. The output
stays byte-identical to the serial run: everything body prints is buffered in segments and written in program order, a job's captured
stdout at the place its dispatch() stood. A job that failed (AssertionError, SystemExit, ...) re-raises the same exception there, after
the output before it and its own partial output, and nothing after it is written (as in the serial run, where nothing after it ran);
if body itself raises, the earlier jobs' output and failures still come first. jobs <= 1 (or no session) = thunk() inline, no fork.

Loud, never a pass (rule 8): a child that dies without a result (signal, os._exit, OOM kill) raises WorkerCrash naming the job; an
exception that cannot be pickled is re-raised as RuntimeError with the child's traceback; the child's traceback also goes to stderr.
The return value of a pooled thunk is NOT available (dispatch returns None): use it only for self-tests whose result nobody reads.
drain() writes everything buffered so far, waits for every job and ends the pooling (later dispatches run inline): call it before
code that writes to fd 1 itself (a child process with inherited stdout), which would otherwise overtake the buffered lines.
SIGTERM while pooled (step timeout): the output so far is written, the cut results are named on stderr, the workers are killed (_on_term).
Not for a thunk that starts a process or writes a shared file: keep those inline (tools/test-bluejay-boot.py names them).
GATE_AUDIT_JOBS (jobs_from_env): positive integer, default 4; check-all.sh sizes it so the lane's builds together stay under nproc-2 (K5).
"""
import io, os, pickle, shutil, signal, sys, tempfile, time, traceback

DEFAULT_JOBS = 4


class WorkerCrash(RuntimeError):
    """A pooled self-test died without reporting a result (fail closed: never a pass)."""


def jobs_from_env(env=None):
    v = (os.environ if env is None else env).get("GATE_AUDIT_JOBS", "").strip()
    if not v:
        return DEFAULT_JOBS
    if not (v.isascii() and v.isdigit()) or int(v) < 1:
        raise SystemExit(f"GATE_AUDIT_JOBS must be a positive integer (1 = serial), got {v!r}")
    return int(v)


_active = None


class _Job:
    def __init__(self, label, thunk, path):
        self.label, self.thunk, self.path = label, thunk, path
        self.pid = self.status = self.result = None


class _Session:
    def __init__(self, jobs):
        self.jobs, self.out, self.items, self.queue, self.running = jobs, sys.stdout, [], [], []
        self.tmp = tempfile.mkdtemp(prefix="audit-pool-")
        self.n = 0
        self._segment()

    def _segment(self):
        buf = io.StringIO()
        self.items.append(buf)
        sys.stdout = buf

    def submit(self, thunk, label):
        self.n += 1
        job = _Job(label, thunk, os.path.join(self.tmp, f"{self.n}.pickle"))
        self.items.append(job)
        self._segment()
        self.queue.append(job)
        self._pump()

    def _start(self, job):
        sys.stdout.flush()
        pid = os.fork()
        if pid:
            job.pid = pid
            self.running.append(job)
            return
        code = 70  # the child never returns into the parent's code
        signal.signal(signal.SIGTERM, signal.SIG_DFL)  # the parent's handler (_on_term) is for the parent only
        try:
            buf = io.StringIO()
            sys.stdout = buf
            err = None
            try:
                job.thunk()
            except BaseException as x:  # noqa: BLE001 - reported to the parent, which re-raises it
                tb = traceback.format_exc()
                try:
                    blob = pickle.dumps(x)
                    pickle.loads(blob)
                except Exception as _err:  # noqa: BLE001
                    print(f"audit_pool GAP: exception {type(x).__name__} is not picklable ({type(_err).__name__}: {_err}): reported as text only", file=sys.stderr)
                    blob = None
                err = (type(x).__name__, str(x), tb, blob)
            with open(job.path + ".tmp", "wb") as t:
                pickle.dump({"stdout": buf.getvalue(), "err": err}, t)
            os.replace(job.path + ".tmp", job.path)
            code = 0
        finally:
            os._exit(code)

    def _pump(self):
        """Reap finished children (never anyone else's), then fill the free slots."""
        for job in list(self.running):
            pid, status = os.waitpid(job.pid, os.WNOHANG)
            if pid:
                job.status = status
                self.running.remove(job)
        while self.queue and len(self.running) < self.jobs:
            self._start(self.queue.pop(0))

    def _finish(self, job):
        while job.status is None:
            self._pump()
            if job.status is None:
                time.sleep(0.01)
        try:
            with open(job.path, "rb") as t:
                job.result = pickle.load(t)
        except (OSError, pickle.PickleError, EOFError):
            raise WorkerCrash(f"audit worker for {job.label} died without a result (wait status {job.status}: {_describe(job.status)}); the audit FAILS") from None
        if job.status != 0:
            raise WorkerCrash(f"audit worker for {job.label} exited abnormally after reporting (wait status {job.status}); the audit FAILS")

    def drain(self):
        """Write every buffered segment and job output in order; raise the first job failure; stop pooling."""
        sys.stdout = self.out
        try:
            while self.items:  # an item leaves the list only after it was written: _on_term names what is still in it
                it = self.items[0]
                if isinstance(it, io.StringIO):
                    self.out.write(it.getvalue())
                    self.items.pop(0)
                    continue
                self._finish(it)
                self.out.write(it.result["stdout"])
                self.items.pop(0)
                if it.result["err"]:
                    name, msg, tb, blob = it.result["err"]
                    print(f"audit worker traceback ({it.label}):\n{tb}", file=sys.stderr)
                    x = None
                    if blob is not None:
                        x = pickle.loads(blob)
                    raise x if isinstance(x, BaseException) else RuntimeError(f"{name}: {msg} (in the audit worker for {it.label}; exception not transferable, see the traceback above)")
        except BaseException:
            self.abort()
            raise
        else:
            self.close()

    def _on_term(self, signum, frame):
        """SIGTERM to the audit (the step timeout, a Ctrl-C of the gate): write what the serial run would have shown by now (the segments and the finished jobs up to the first unfinished one), say
        which results were cut, kill the workers, then die by the same signal. Without it a hung pooled audit printed nothing and left its workers burning CPU (review gate-cold-k3 r1 F4)."""
        try:
            sys.stdout = self.out
            cut, shown = [], True
            for it in self.items:
                if isinstance(it, io.StringIO):
                    if shown:
                        self.out.write(it.getvalue())
                    continue
                if shown:
                    try:
                        with open(it.path, "rb") as t:
                            self.out.write(pickle.load(t)["stdout"])
                    except (OSError, pickle.PickleError, EOFError):   # lint-silent: expected an unreadable result file is listed as not shown in the SIGTERM summary line right below
                        shown = False
                if not shown:
                    cut.append(it.label)
            self.out.flush()
            print(f"audit pool: terminated by SIGTERM; {len(cut)} self-test result(s) not shown (unfinished or after an unfinished one): {', '.join(cut) or '-'}; the workers are killed", file=sys.stderr, flush=True)
        finally:
            self.abort()
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)

    def abort(self):
        for job in self.running:
            try:
                os.kill(job.pid, signal.SIGKILL)
            except ProcessLookupError:   # lint-silent: expected the child already exited: nothing to kill
                pass
            try:
                os.waitpid(job.pid, 0)
            except ChildProcessError:   # lint-silent: expected the child is already reaped
                pass
        self.running, self.queue = [], []
        self.close()

    def close(self):
        global _active
        sys.stdout = self.out
        if _active is self:
            _active = None
        shutil.rmtree(self.tmp, ignore_errors=True)


def _describe(status):
    if os.WIFSIGNALED(status):
        return f"killed by signal {os.WTERMSIG(status)}"
    return f"exit code {os.WEXITSTATUS(status)}"


def dispatch(thunk, label):
    """Run thunk() in the pool of the active session (returns None at once), else inline (returns its value)."""
    if _active is None:
        return thunk()
    _active.submit(thunk, label)
    return None


def drain():
    if _active is not None:
        _active.drain()


def run(body, jobs):
    """body() with its dispatch() calls pooled over `jobs` processes; jobs <= 1 = body() as is."""
    global _active
    if jobs <= 1:
        return body()
    if _active is not None:
        raise RuntimeError("audit_pool.run is not reentrant")
    s = _active = _Session(jobs)
    pending = None
    try:
        old_term = signal.signal(signal.SIGTERM, s._on_term)
    except ValueError:  # not the main thread: no handler, the step timeout then behaves as before   # lint-silent: expected not the main thread: no SIGTERM handler, the step timeout behaves as before (comment)
        old_term = None
    try:
        try:
            body()
        except KeyboardInterrupt:
            raise  # the finally below kills the children
        except BaseException as x:  # an Exception or SystemExit of the real checks: the output before it (and any earlier job failure) comes first
            pending = x
        s.drain()
    finally:
        s.abort()
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
    if pending is not None:
        raise pending
