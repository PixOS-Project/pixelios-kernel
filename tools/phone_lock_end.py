"""phone_lock_end: run end, hard limit, park, end, detach/follow/notify for tools/phone-lock (imported by it; not run directly).

Run end, hard limit, notify (user 2026-11-09: no stale or broken locks, the phone rests in fastboot, the thread learns the run ended):
  phone-lock run --owner NAME [--wait S] [--max S] [--detach] [--notify auto|none|thread:ID] -- CMD...
  phone-lock end --owner NAME --reason "..." [--force] [--timeout S]   stop the owner's run NOW: CMD stopped, phone to fastboot, lock released
  phone-lock park --owner NAME [--wait S]   take the free lock, put the phone in fastboot (tools/phone goto fastboot), release (an orphan iOS phone)
  phone-lock follow ID|last [--timeout S]   block until detached run ID ends; prints the log tail + `END id=.. rc=..`, exit = the run's rc (safe to kill)
  phone-lock runs                           the last 10 detached runs with their state
- PARK: every `run` ends with the phone in fastboot: after CMD (normal end, failure, signal, end request, hard limit) and while the lock is still
  held, `tools/phone goto fastboot` (iOS: UDP SWEET-RESET-BL, needs sweetremote=1; recovery/Android: adb reboot bootloader; fastboot: no step).
  One `phone-lock: PARK ok|FAIL ...` line (a FAIL carries tools/phone's NEED USER line). Only for the real lock; a test lock dir skips it (one line).
  ROOT (user 2026-11-11: ending a run early must always be safe): right after a park that reached fastboot, lock still held, the PARK step restores the
  STANDARD root: if the newest line of flashed-userdata.txt (a sha256, or `flashing` = interrupted flash) is not the sha of standard-root.txt it runs
  tools/fflash-userdata.sh <standard path> --yes-erase-userdata --reason "phone-lock park: standard root back after OWNER run ID (WHY) (user 2026-11-11)"
  with PREFLIGHT_GATE_WAIVER set. fflash's lock check (`phone-lock check`) passes because the step runs in the holder (run, or the tick/end/park
  process under its own park lock) and the child inherits PHONE_LOCK_TOKEN: the same token as info.json, no bypass. So no owner script needs a
  flash-back step and a killed run leaves the standard root. One line: `PARK ROOT ok standard SHA12` | `restored SHA12 in N s` | `FAIL why NEED USER`
  (the lock is released anyway; the outcome is `root` in end.json, the notice and the history) | `GAP` (standard-root.txt missing/invalid: no flash) |
  `not attempted` (the park failed: no flash) | `off` (PHONE_LOCK_RESTORE_ROOT=0, sandbox only) | `skipped` (test lock dir without the seam). Timing: the lock's
  `deadline` moves to now + PARK_BUDGET when a park starts (the tick cannot reap it), `phase` park|restore is in info.json (`end` says "restore running"),
  and the lock's pgid points at the flash while it runs (a SIGKILLed holder keeps the lock until the flash ends: its output goes to <lock>.restore.log, not a pipe; the tick then parks and checks again).
  A tick/enforce park whose restore FAILed exits 3, writes history ROOT-FAIL and tells the run's notify thread (stored in info.json). Details: tools/phone_lock_root.py.
  A restore is capped at RESTORE_MAX (900 s: ~7 min flash + image hash); `end --timeout` defaults to PARK_BUDGET + 200 = stop 40 + park 260 + restore 900 + slack.
- HARD LIMIT: `--max S` (default 3600, capped at 7200; counted from the moment the lock is taken, the --wait is not part of it) is written as
  `deadline` in info.json. At the deadline `run` stops CMD's whole process group (SIGTERM, 30 s, SIGKILL), parks the phone, releases (history
  HARD-LIMIT). Backstop for a `run` that is SIGKILLed or hung, in `tick` (the 2-minute user timer pixos-phone-lock-tick): a dead holder's lock is
  removed AND the phone parked; a live holder past deadline + grace (420 s for a run, 90 s for others) is killed (pid and process group, TERM then
  KILL), its lock removed (history HARD-LIMIT-REAP), the phone parked, the next waiter woken. A lock without `deadline` (older copies of this tool,
  xrun's acquire) has deadline = start + min(ttl, 7200) for acquire --pid, start + 7200 otherwise.
- END: `end` checks the owner label (refused for another owner unless --force), records the reason (history END-REQUEST) and signals the holder:
  SIGUSR1 to a `run` of this version (kind=run) = the same stop/park/release path as the hard limit; SIGTERM to any other holder (xrun's EXIT
  trap releases), then `end` parks the phone itself. Waits up to --timeout (default PARK_BUDGET + 200 = 1500 s: stop + park + root restore) for the lock to go; exit 0 released, 1 still held.
- DETACH: `run --detach` starts the same run in its own transient systemd user SERVICE (unit pixos-run-<id>, KillMode=mixed, ManagedOOMPreference=avoid + OOMScoreAdjust=-500 (kernel OOM killer: a user unit may go down to user@UID's -900; 2026-11-11: systemd-oomd killed a hold right after a flash; avoid is honoured for memory-pressure kills when the unit and the monitored user@UID cgroup share the owner, man systemd.resource-control), TimeoutStopSec PARK_BUDGET + 60 = 1380 (stop + park + restore),
  RuntimeMaxSec = wait + max + 900 as a third limit) and returns at once with `DETACHED id=..`: an agent harness timeout, a turn end or a killed
  shell no longer kills the run. Env + cwd are kept (env file 0600 under <lock>.runs/<id>/, deleted when the run starts); log <lock>.runs/<id>/run.log.
  At the end `end.json` {rc, how, park} is written and the thread is told: --notify auto (default with --detach) = thread:$T3CODE_THREAD_ID when set,
  through `t3 thread send` (one line `PHONE RUN END id=.. rc=.. ended=.. park=..`, no log text); NOTIFY ok|FAIL line in the log. An attached run
  notifies only with an explicit --notify.
Test seams (sandbox only: PHONE_LOCK_DIR under $TMPDIR, else exit 2): PHONE_LOCK_PARK=CMD replaces tools/phone goto fastboot; PHONE_LOCK_T3=CMD replaces
  t3; PHONE_LOCK_STOP_GRACE / PHONE_LOCK_TICK_GRACE = seconds instead of 30 / the tick grace;
  PHONE_LOCK_RESTORE=CMD replaces fflash-userdata.sh (called CMD PATH --yes-erase-userdata --reason TEXT; STANDARD_ROOT / FLASH_STATE name the files, as for fflash);
  PHONE_LOCK_RESTORE_MAX = seconds instead of 900; PHONE_LOCK_RESTORE_ROOT=0 turns the root step off (one loud line). PHONE_LOCK_SYSTEMD_RUN (above) also replaces systemd-run of --detach.
"""
import argparse, json, os, re, shutil, signal, subprocess, sys, threading, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_lock_root as R  # noqa: E402  (the root step of the park)
from phone_lock_root import PARK_GOTO, RESTORE_MAX, PARK_BUDGET, END_TIMEOUT, root_plan  # noqa: E402,F401

C = None   # the tools/phone-lock module (bind): lock primitives read/acquire/write/hist/...


def real_sleep(s):
    """the ONE raw sleep of this half: every wait here is real time and never scaled (grace, stop, end, follow, tick limit)."""
    time.sleep(s)


def bind(core):
    global C
    C = core
    R.bind(core)


def runs_root():   # detached runs: <id>/{spec.json (0600, removed at start), env.json (0600, removed at start), run.log, pid, end.json}
    return C.LOCK + ".runs"


HERE = os.path.dirname(os.path.abspath(__file__))
MAXHOLD = 7200                # hard cap of --max (and of a lock without a deadline)
DEFAULT_MAX = 3600            # longest observed hold 2026-11-04..09 was 1697 s (phone.lock.history RUN-END ages), p90 1359 s
SEAMS = ("PHONE_LOCK_PARK", "PHONE_LOCK_T3", "PHONE_LOCK_STOP_GRACE", "PHONE_LOCK_TICK_GRACE", "PHONE_LOCK_RESTORE", "PHONE_LOCK_RESTORE_MAX", "PHONE_LOCK_RESTORE_ROOT")
ROOT = R.ROOT                 # outcome of the root step of the last park in this process (tools/phone_lock_root.py)


def _seam_num(name, default):
    v = os.environ.get(name)
    return float(v) if v else default


def stop_grace():
    return _seam_num("PHONE_LOCK_STOP_GRACE", 30.0)


def tick_grace(i):
    return _seam_num("PHONE_LOCK_TICK_GRACE", 420.0 if i.get("kind") == "run" else 90.0)


def hard_deadline(i):
    """epoch after which a lock with a pid is ended by force (see HARD LIMIT); None for a pid-less lock (its ttl already ends it)."""
    if not i or not i.get("pid"):
        return None
    if i.get("deadline"):
        return int(i["deadline"])
    lim = min(int(i.get("ttl") or MAXHOLD), MAXHOLD) if i.get("kind") == "acquire" or i.get("cmd") == "acquire" else MAXHOLD
    return int(i.get("start", 0)) + lim


def real_lock():
    return os.path.realpath(C.LOCK) == os.path.realpath(C.DEFAULT_LOCK)


def zombie(pid):
    """True when PID has exited but is not reaped yet (state Z): it uses no phone, kill(pid, 0) still succeeds on it."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):   # lint-silent: expected no /proc entry (gone or no procfs): not a zombie, kill() decided
        return False


def group_members_live(pgid):
    """True when a non-zombie process of group PGID exists (procfs scan); None when /proc cannot be read (then kill() decides)."""
    try:
        names = os.listdir("/proc")
    except OSError:   # lint-silent: expected no procfs here: the caller falls back to kill() (None)
        return None
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % n) as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):   # lint-silent: expected the process ended during the scan
            continue
        if len(fields) > 3 and fields[3] == str(pgid) and fields[0] != "Z":
            return True
    return False


def tick():
    """OVERDUE: the lock is held past its ttl (a live pid keeps it, so the ttl is only the announced figure): wake the head waiter, once per lock."""
    dead = C.reap_stale()
    if dead is not None:
        out = "not-needed (pid-less lock)"
        if dead.get("pid"):   # the holder died without its end step: the phone may sit in iOS/recovery with nobody to bring it back
            out, _ = park_with_own_lock("phone-lock-tick", "dead holder %s" % dead.get("owner", "?"), held=(dead.get("owner", "?"), dead.get("runid")))
        rc = C.wake_next("phone-lock holder dead", dead.get("owner", ""))
        rc = root_fail_loud(dead, "dead holder", rc)
        return C.result(rc, "-", 0, "tick: removed the stale lock of %s (dead pid %s), park=%s root=%s" % (dead.get("owner", "?"), dead.get("pid", "?"), out, ROOT["state"]))
    i = C.read()
    dl = hard_deadline(i)
    if i and dl and not C.foreign_ns(i) and time.time() > dl + tick_grace(i):
        return enforce_limit(i)
    if not i or not i.get("token") or C.age(i) <= i.get("ttl", 0):
        return C.result(0, (i or {}).get("owner", "-"), C.age(i) if i else 0, "tick: nothing overdue")
    mark = os.path.join(C.WAITERS, ".overdue-" + re.sub(r"[^A-Za-z0-9]", "", i["token"])[:16])
    if os.path.exists(mark):
        return C.result(0, i.get("owner", "-"), C.age(i), "tick: overdue already announced for this lock")
    before = len(C.waiters())
    if not before:
        return C.result(0, i.get("owner", "-"), C.age(i), "tick: overdue (age %ds > ttl %ds) but no waiter registered" % (C.age(i), i.get("ttl", 0)))
    rc = C.wake_next("phone-lock overdue", i.get("owner", ""))
    if rc == 0 and len(C.waiters()) < before:   # woken: remember, so the next tick stays quiet for this lock
        os.makedirs(C.WAITERS, mode=0o700, exist_ok=True)
        open(mark, "w").close()
    return C.result(rc, i.get("owner", "-"), C.age(i), "tick: lock of %s overdue (age %ds > ttl %ds)" % (i.get("owner", "?"), C.age(i), i.get("ttl", 0)))


def park_phone(why):
    """Put the phone in fastboot while the caller holds the lock (PHONE_LOCK_TOKEN in os.environ). Returns a short outcome word; one visible line."""
    seam = os.environ.get("PHONE_LOCK_PARK")
    if seam:
        cmd = [seam]
    elif not real_lock():
        print("phone-lock: PARK skipped after %s (test lock dir %s, not the real phone)" % (why, C.LOCK))
        return "skipped-test-lock"
    else:
        cmd = [os.path.join(HERE, "phone"), "goto", "fastboot", "--timeout", str(PARK_GOTO)]
    sys.stdout.flush()
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PARK_GOTO + 60)
        rc, lines = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    except (OSError, subprocess.SubprocessError) as e:
        rc, lines = -1, ["%s: %s" % (type(e).__name__, e)]
    res = [l for l in lines if l.startswith("RESULT:")][-1:] or lines[-1:] or ["no output"]
    need = [l for l in lines if "NEED USER" in l][:1]
    if rc == 0:
        print("phone-lock: PARK ok after %s: %s" % (why, res[0][:200]))
        return "fastboot"
    print("phone-lock: PARK FAIL after %s: rc=%d %s%s" % (why, rc, res[0][:200], (" | " + need[0][:160]) if need else ""))
    return "FAIL"


def root_fail_loud(i, what, rc):
    """tick paths (nobody reads the run's log): a failed root restore is a history event ROOT-FAIL, a notice to the run's thread and exit 3, never a PASS."""
    if ROOT["state"] != "FAIL":
        return rc
    C.hist("ROOT-FAIL", i, "standard root NOT restored after %s of %s (see the journal / %s.restore.log): userdata may hold a test root; NEED USER" % (what, i.get("owner", "?"), C.LOCK))
    notify(i.get("notify"), "PHONE RUN ROOT FAIL id=%s owner=%s: the standard root was NOT restored after %s; userdata may hold a test root. NEED USER: tools/phone-lock history / the tick journal" % (
        i.get("runid") or "-", i.get("owner", "?"), what))
    return rc or 3


def park(why, owner="?", runid=None):
    """park_phone, then the root step when the phone is in fastboot. The lock's hard limit is moved out first so the tick never reaps the steps."""
    ROOT["state"] = "-"
    tok = os.environ.get("PHONE_LOCK_TOKEN", "")
    cur = C.read() or {}
    R.lock_update(tok, deadline=max(int(cur.get("deadline") or 0), int(time.time()) + PARK_BUDGET), phase="park")
    out = park_phone(why)
    if out == "fastboot":
        R.root_step(why, owner, runid)
    elif out == "FAIL":
        ROOT["state"] = "not-attempted"
        print("phone-lock: PARK ROOT not attempted: the park did not reach fastboot, so no flash was started (a later park restores it)", flush=True)
    return out


def park_with_own_lock(owner, why, wait=0, held=None):
    """Take the (free) lock under a fresh token, park, release. For a phone whose holder is gone (tick, end, `park`). (outcome, code)."""
    token = str(uuid.uuid4())
    code, msg = C.acquire(owner, wait, PARK_BUDGET, os.getpid(), "park: " + why, token, maxhold=PARK_BUDGET, kind="park")
    if code:
        print("phone-lock: PARK skipped after %s: %s" % (why, msg))
        return "skipped-busy", 1
    old = os.environ.get("PHONE_LOCK_TOKEN")
    os.environ["PHONE_LOCK_TOKEN"] = token
    try:
        out = park(why, *(held or (owner, None)))
    finally:
        if old is None:
            os.environ.pop("PHONE_LOCK_TOKEN", None)
        else:
            os.environ["PHONE_LOCK_TOKEN"] = old
        i = C.read()
        if i and i.get("token") == token:
            C.forget_seen()
            C.hist("RELEASE", i, "park released its own lock (%s) root=%s" % (why, ROOT["state"]))
            shutil.rmtree(C.LOCK, ignore_errors=True)
    return out, 0 if out in ("fastboot", "skipped-test-lock") and ROOT["state"] != "FAIL" else 1


def stop_group(pgid, child=None, label="", grace=None):
    """SIGTERM the process group, wait up to grace s, then SIGKILL. Returns 'TERM', 'KILL' or 'gone'. Reaps CHILD while waiting."""
    grace = stop_grace() if grace is None else grace
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 10)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:   # lint-silent: expected the group already ended: nothing left to stop
            return "gone" if sig == signal.SIGTERM else "TERM"
        except PermissionError as e:
            print("phone-lock GAP: cannot signal process group %d (%s): %s" % (pgid, label, e))
            return "denied"
        t = time.time() + wait
        while time.time() < t:
            if child is not None:
                child.poll()
            if not C.group_alive(pgid):
                return "TERM" if sig == signal.SIGTERM else "KILL"
            real_sleep(0.2)
    return "KILL-survived"


def resolve_owner(tid):
    """(thread or None, how) at SEND time (coordinator 2026-11-12: a replaced thread's holds kept waking it). Owner files
    ${PIXOS_OWNERS_DIR:-~/.cache/pixelios/owners}/<key> (written by `tools/resident owner`): first non-comment line = the current owner,
    'former <id>' lines = replaced owners. A former owner of a key -> that key's current owner (none -> nobody, a GAP); else TID itself."""
    d = os.environ.get("PIXOS_OWNERS_DIR") or os.path.expanduser("~/.cache/pixelios/owners")
    for n in (sorted(os.listdir(d)) if os.path.isdir(d) else []):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", n):
            continue
        cur, former = None, []
        try:
            text = open(os.path.join(d, n)).read()
        except OSError as e:
            print("phone-lock: NOTIFY GAP: owner file %s unreadable (%s)" % (n, e))
            continue
        for ln in text.splitlines():
            ln = ln.strip()
            if ln.startswith("former "):
                former.append(ln.split(None, 1)[1].strip())
            elif ln and not ln.startswith("#") and cur is None:
                cur = ln
        if tid in former:
            return (cur, "thread replaced: owner %s" % n) if cur else (None, "thread replaced (owner %s) and no current owner" % n)
    return tid, "own thread"


def notify(target, line):
    """Tell the agent thread TARGET (thread:ID) that the run ended: `t3 thread send` (queued as its next turn). One visible line."""
    if not target or target == "none":
        return 0
    m = re.fullmatch(r"thread:([A-Za-z0-9:._-]{3,80})", target)
    if not m:
        print("phone-lock: NOTIFY FAIL: target %r is not thread:<id>" % target[:60])
        return 3
    tid, how = resolve_owner(m.group(1))
    if not tid:
        print("phone-lock: NOTIFY GAP: not sent (%s): %s" % (how, line[:120]))
        return 3
    if tid != m.group(1):
        print("phone-lock: NOTIFY reroute %s -> %s (%s)" % (m.group(1), tid, how))
    t3 = os.environ.get("PHONE_LOCK_T3") or shutil.which("t3") or os.path.expanduser("~/.local/bin/t3")
    try:
        r = subprocess.run([t3, "thread", "send", tid, line], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        rc, why = r.returncode, ((r.stderr or r.stdout or "").strip().splitlines() or ["no output"])[-1][:160]
    except (OSError, subprocess.SubprocessError) as e:
        rc, why = -1, "%s: %s" % (type(e).__name__, e)
    if rc == 0:
        print("phone-lock: NOTIFY ok thread=%s" % tid)
        return 0
    print("phone-lock: NOTIFY FAIL thread=%s rc=%d %s (the end record stays: tools/phone-lock follow)" % (tid, rc, why))
    return 3


def end_request_reason():
    try:
        with open(os.path.join(C.LOCK, "end-request.json")) as f:
            j = json.load(f)
        return "end request by %s: %s" % (j.get("by", "?"), j.get("reason", "?"))
    except (OSError, ValueError):   # lint-silent: expected no request file: SIGUSR1 without `end` (kill -USR1 by hand)
        return "end request (SIGUSR1, no reason recorded)"


def run(a):
    token = str(uuid.uuid4())
    maxhold = min(max(int(getattr(a, "max", None) or DEFAULT_MAX), 1), MAXHOLD)
    runid = getattr(a, "runid", None)
    code, msg = C.acquire(a.owner, a.wait, a.ttl, os.getpid(), " ".join(a.cmd), token, maxhold=maxhold, kind="run", runid=runid)
    if msg:
        print(msg)
    if code:
        if runid:
            write_end(runid, code, "lock busy: %s" % (msg or ""), "not-touched", 0, a)
        return C.result(code, a.owner, 0)
    t0 = time.time()
    deadline = (C.read() or {}).get("deadline") or int(t0) + maxhold
    print("phone-lock: hard limit %ds: at %s the command is stopped, the phone goes to fastboot, the lock is released" % (
        maxhold, time.strftime("%H:%M:%S", time.localtime(deadline))))
    child = None
    os.environ["PHONE_LOCK_TOKEN"] = token  # xrun/probe-run/fboot inside CMD see that the lock is already ours
    os.environ["PHONE_LOCK_OWNER"] = a.owner
    os.environ["PHONE_OWNER"] = a.owner
    stop = {"why": None, "thread": None}

    def trigger(why):
        """stop the command's group once (deadline timer thread or signal handler); the work runs in its own thread, main stays in wait()."""
        if stop["why"] is not None or child is None:
            return
        stop["why"] = why

        def work():
            C.hist("HARD-LIMIT" if why.startswith("hard limit") else "STOP", C.read(), why)
            print("phone-lock: STOP %s: stopping the command's process group %d" % (why, child.pid), flush=True)
            print("phone-lock: STOP done (%s)" % stop_group(child.pid, None, a.owner), flush=True)
        stop["thread"] = threading.Thread(target=work, daemon=True)
        stop["thread"].start()

    def on_sig(sig, _frame):
        if child is None:   # before the command started: nothing to stop yet, remember it
            stop["why"] = stop["why"] or (end_request_reason() if sig == signal.SIGUSR1 else "signal %d" % sig)
            return
        trigger(end_request_reason() if sig == signal.SIGUSR1 else "signal %d" % sig)

    rc, how, parked = 2, "normal", "not-run"
    try:
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1):
            signal.signal(s, on_sig)
        C.adb_server()
        sys.stdout.flush()
        child = subprocess.Popen(a.cmd, start_new_session=True)  # own process group: leftovers keep the lock if `run` is SIGKILLed
        cur = C.read()
        if cur and cur.get("token") == token:  # never overwrite a lock somebody force-released and retook
            C.write(a.owner, os.getpid(), a.ttl, " ".join(a.cmd), token, child.pid, deadline, "run", runid)
            if getattr(a, "notify", None) not in (None, "none"):
                R.lock_update(token, notify=a.notify)   # the tick tells this thread if the run dies and its root restore fails
        early = stop["why"]
        if early:   # a signal arrived before the command started
            stop["why"] = None
            trigger(early)
        timer = threading.Timer(max(0.0, deadline - time.time()), trigger, ("hard limit %ds" % maxhold,))
        timer.daemon = True
        timer.start()
        r = child.wait()   # blocking, no polling; the timer / a signal stop the group and this returns
        timer.cancel()
        if stop["thread"] is not None:
            stop["thread"].join()
        if stop["why"]:
            how = stop["why"]
        else:
            for _ in range(60):  # children left in the group (a backgrounded fastboot) still use the phone: keep the lock up to 30 s
                if not C.group_alive(child.pid):
                    break
                real_sleep(0.5)   # real time: the grace that keeps the lock while a backgrounded fastboot lives must not scale (review xrun-seams-r1 F3)
            else:
                print("phone-lock: WARN processes of the command's group %d outlived it by 30 s: stopping them before the release" % child.pid)
                print("phone-lock: STOP done (%s)" % stop_group(child.pid, None, a.owner))
        rc = 128 - r if r < 0 else r  # killed by signal N -> 128+N
    except OSError as e:
        print("cannot run %s: %s" % (a.cmd[0], e))
        rc = 127
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 2
    finally:
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        i = C.read()
        if i and i.get("token") == token:  # not if it was force-released and taken meanwhile
            parked = park(how if how != "normal" else "command exit %s" % rc, a.owner, runid)
            i = C.read()
            if i and i.get("token") == token:
                C.forget_seen()
                C.hist("RUN-END", i, "run released its own lock, command exit %s, ended=%s, park=%s, root=%s" % (rc, how, parked, ROOT["state"]))
                shutil.rmtree(C.LOCK, ignore_errors=True)
                C.wake_next("phone-lock released", a.owner)   # failure is printed (WAKE FAIL); CMD's exit code stays CMD's
        else:
            parked = "lock-lost"
            print("phone-lock: WARN the lock was no longer ours at the end (force-released or reaped): no park, no release")
    return finish(a, runid, rc, how, parked, int(time.time() - t0))


def finish(a, runid, rc, how, parked, held):
    """end record (detached), thread notice, RESULT line; returns the command's rc."""
    if runid:
        write_end(runid, rc, how, parked, held, a)
    note = getattr(a, "notify", None)
    if note and note != "none":
        notify(note, "PHONE RUN END id=%s owner=%s rc=%s ended=%s park=%s root=%s held=%ds. The phone lock is released.%s" % (
            runid or "-", a.owner, rc, re.sub(r"[^A-Za-z0-9 ._=:/-]", "?", how)[:100], parked, ROOT["state"], held,
            (" Result: tools/phone-lock follow %s" % runid) if runid else ""))
    C.result(0 if rc == 0 else 1, a.owner, held, ("command exit %d" % rc if rc else "") + ("" if how == "normal" else " ended=%s" % how) + " park=%s root=%s" % (parked, ROOT["state"]))
    return rc


def run_dir(runid):
    return os.path.join(runs_root(), runid)


def write_end(runid, rc, how, parked, held, a):
    d = run_dir(runid)
    try:
        tmp = os.path.join(d, "end.json.tmp")
        with open(tmp, "w") as f:
            json.dump({"id": runid, "owner": a.owner, "rc": rc, "how": how, "park": parked, "root": ROOT["state"], "held_for": held, "end": int(time.time())}, f)
        os.replace(tmp, os.path.join(d, "end.json"))
    except OSError as e:
        print("phone-lock GAP: cannot write the end record of run %s: %s (follow will report the run as gone)" % (runid, e))


def resolve_notify(v, detached):
    if v in (None, "auto"):
        if v is None and not detached:
            return "none"
        tid = os.environ.get("T3CODE_THREAD_ID")
        if not tid:
            print("phone-lock: NOTIFY none: no T3CODE_THREAD_ID in the environment (not a T3 agent shell); use tools/phone-lock follow")
            return "none"
        return "thread:" + tid
    return v


def detach(a):
    owner = re.sub(r"[^A-Za-z0-9-]", "-", a.owner)[:40] or "agent"
    runid = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + owner + "-" + uuid.uuid4().hex[:4]
    d = run_dir(runid)
    os.makedirs(runs_root(), mode=0o700, exist_ok=True)
    os.makedirs(d, mode=0o700)
    notify_to = resolve_notify(a.notify, True)
    spec = {"owner": a.owner, "wait": a.wait, "ttl": a.ttl, "max": a.max, "notify": notify_to, "cmd": a.cmd, "cwd": os.getcwd()}
    for name, obj in (("spec.json", spec), ("env.json", dict(os.environ))):
        fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
    log = os.path.join(d, "run.log")
    maxhold = min(max(int(a.max or DEFAULT_MAX), 1), MAXHOLD)
    unit = "pixos-run-" + runid
    sr = os.environ.get("PHONE_LOCK_SYSTEMD_RUN") or shutil.which("systemd-run")
    if not sr:
        return C.result(2, a.owner, 0, "detach: systemd-run not found; no plain background fallback (run without --detach)")
    cmd = [sr, "--user", "--unit=" + unit, "--collect", "--quiet", "--property=KillMode=mixed", "--property=ManagedOOMPreference=avoid", "--property=OOMScoreAdjust=-500", "--property=TimeoutStopSec=%d" % (PARK_BUDGET + 60),
           "--property=RuntimeMaxSec=%d" % (int(min(a.wait, C.MAXWAIT)) + maxhold + PARK_BUDGET + 300),
           "--property=StandardOutput=append:" + log, "--property=StandardError=append:" + log,
           "--working-directory=" + os.getcwd(), "--setenv=PHONE_LOCK_DIR=" + C.LOCK,
           sys.executable, "-u", os.path.join(HERE, "phone-lock"), "_detached", d]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        rc, why = r.returncode, ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1][:200]
    except (OSError, subprocess.SubprocessError) as e:
        rc, why = -1, str(e)
    if rc != 0:
        shutil.rmtree(d, ignore_errors=True)
        return C.result(2, a.owner, 0, "detach: systemd-run failed rc=%d: %s" % (rc, why))
    print("DETACHED id=%s unit=%s log=%s notify=%s" % (runid, unit, log, notify_to))
    print("wait for it: tools/phone-lock follow %s   (blocks until the end; background it; killing it does not touch the run)" % runid)
    print("stop it early: tools/phone-lock end --owner %s --reason \"...\"" % a.owner)
    return C.result(0, a.owner, 0)


def detached_main(d):
    """Entry of the systemd unit: restore env + cwd from the run dir, then the ordinary `run`."""
    with open(os.path.join(d, "spec.json")) as f:
        spec = json.load(f)
    with open(os.path.join(d, "env.json")) as f:
        env = json.load(f)
    os.unlink(os.path.join(d, "env.json"))   # may hold tokens: it lived only until the run started
    with open(os.path.join(d, "pid"), "w") as f:
        f.write(str(os.getpid()))
    keep = os.environ.get("PHONE_LOCK_DIR")
    os.environ.clear()
    os.environ.update(env)
    if keep:
        os.environ["PHONE_LOCK_DIR"] = keep
    os.chdir(spec["cwd"])
    print("phone-lock: detached run %s started pid=%d cwd=%s cmd=%s" % (os.path.basename(d), os.getpid(), spec["cwd"], " ".join(spec["cmd"])[:300]))
    a = argparse.Namespace(owner=spec["owner"], wait=spec["wait"], ttl=spec["ttl"], max=spec["max"], cmd=spec["cmd"],
                           notify=spec["notify"], runid=os.path.basename(d))
    return run(a)


def run_state(runid):
    d = run_dir(runid)
    try:
        with open(os.path.join(d, "end.json")) as f:
            return "ended", json.load(f)
    except (OSError, ValueError):   # lint-silent: expected no end record yet: the run is live or died (decided below)
        pass
    try:
        pid = int(open(os.path.join(d, "pid")).read().strip())
    except (OSError, ValueError):   # lint-silent: expected no pid file yet: the unit has not started the run (pid 0 = starting/gone below)
        pid = 0
    if pid and C.alive(pid):
        return "running", {"pid": pid}
    if not pid and time.time() - os.stat(d).st_mtime < 60:
        return "starting", {}
    return "gone", {"pid": pid}


def list_runs():
    try:
        ids = sorted(n for n in os.listdir(runs_root()) if os.path.isdir(run_dir(n)))
    except FileNotFoundError:   # lint-silent: expected no detached run was ever started with this lock dir
        ids = []
    return ids


def follow(runid, timeout, owner=None):
    if runid == "last":
        ids = [n for n in list_runs() if not owner or ("-" + re.sub(r"[^A-Za-z0-9-]", "-", owner)[:40] + "-") in n]
        if not ids:
            return C.result(2, owner or "-", 0, "follow: no detached run found in %s" % runs_root())
        runid = ids[-1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", runid) or not os.path.isdir(run_dir(runid)):
        return C.result(2, "-", 0, "follow: no detached run %r in %s" % (runid, runs_root()))
    end = time.time() + timeout
    gone_since = None
    while True:
        st, info = run_state(runid)
        if st == "ended":
            break
        if st == "gone":
            gone_since = gone_since or time.time()
            if time.time() - gone_since > 5:   # the end record is written before the process exits: 5 s covers the gap
                break
        if time.time() > end:
            print("FOLLOW: timeout after %ds; run %s is still %s (follow again, or tools/phone-lock end)" % (timeout, runid, st))
            return 3
        real_sleep(1)
    log = os.path.join(run_dir(runid), "run.log")
    try:
        tail = open(log, errors="replace").read().splitlines()[-25:]
    except OSError:
        tail = ["(no log at %s)" % log]
    for l in tail:
        print("  | " + l[:300])
    if st != "ended":
        print("END id=%s rc=? ended=GONE: the run process died without an end record (SIGKILL?); tick parks the phone and frees the lock" % runid)
        return 2
    print("END id=%s rc=%s ended=%s park=%s held_for=%ss log=%s" % (runid, info.get("rc"), info.get("how"), info.get("park"), info.get("held_for"), log))
    rc = info.get("rc")
    return rc if isinstance(rc, int) and 0 <= rc < 256 else 2


def runs_cmd():
    for n in list_runs()[-10:]:
        st, info = run_state(n)
        extra = " rc=%s ended=%s park=%s" % (info.get("rc"), info.get("how"), info.get("park")) if st == "ended" else ""
        print("run %s %s%s" % (n, st, extra))
    return C.result(0, "-", 0)


def end_cmd(owner, reason, force, timeout):
    i = C.read()
    if i is None:
        return C.result(0, owner, 0, "end: no lock held (phone free); an orphan phone in iOS: tools/phone-lock park --owner %s" % owner)
    if C.stale(i):
        dead = C.reap_stale()
        out, code = park_with_own_lock(owner, "end after a dead holder (%s)" % (dead or i).get("owner", "?"), held=((dead or i).get("owner", "?"), (dead or i).get("runid")))
        C.wake_next("phone-lock holder dead", (dead or i).get("owner", ""))
        return C.result(code, owner, 0, "end: the holder %s was already dead; lock removed, park=%s" % (i.get("owner", "?"), out))
    if not force and i.get("owner") != owner:
        return C.result(2, owner, C.age(i), "end refused: the lock is held by %s, not %s (never end another owner's run; --force only with the user's go)" % (i.get("owner", "?"), owner))
    if C.foreign_ns(i) or not i.get("pid"):
        return C.result(2, owner, C.age(i), "end refused: the holder pid cannot be signalled from here (other PID namespace or a pid-less lock: release --owner)")
    try:
        with open(os.path.join(C.LOCK, "end-request.json"), "w") as f:
            json.dump({"by": owner, "reason": reason, "pid": os.getpid(), "ts": int(time.time())}, f)
    except OSError as e:
        print("phone-lock GAP: cannot write the end request file: %s (the run logs no reason)" % e)
    C.hist("END-REQUEST", i, "by %s%s: %s" % (owner, " (force)" if force else "", reason))
    is_run = i.get("kind") == "run"
    sig = signal.SIGUSR1 if is_run else signal.SIGTERM   # an older `run` (no kind) would die on SIGUSR1 without its cleanup
    try:
        os.kill(i["pid"], sig)
    except ProcessLookupError:   # lint-silent: expected the holder ended between read and kill: the wait below sees the lock go
        pass
    print("end: sent %s to %s pid %d (%s); waiting up to %ds for the stop, park and release" % (
        signal.Signals(sig).name, i.get("owner"), i["pid"], "run" if is_run else i.get("kind") or "older run/acquire", timeout))
    if i.get("phase") == "restore":
        print("end: a standard-root restore flash is ALREADY RUNNING for %s (the run's command is gone); waiting for it, up to %ds" % (i.get("owner"), timeout))
    elif root_plan()[0] == "need":
        print("end: after the stop and the park the standard root is flashed back (userdata holds something else): up to %ds more; the wait covers it" % RESTORE_MAX)
    sys.stdout.flush()
    t, said = time.time() + timeout, i.get("phase") == "restore"
    while time.time() < t:
        j = C.read()
        if j is None or j.get("token") != i.get("token"):
            break
        if j.get("phase") == "restore" and not said:
            said = True
            print("end: the standard-root restore flash is running now (a few minutes); still waiting", flush=True)
        if C.stale(j):
            C.reap_stale()
            break
        real_sleep(0.5)
    else:
        return C.result(1, owner, C.age(i), "end: the lock of %s is still held after %ds; tick ends it at the hard limit" % (i.get("owner"), timeout))
    if is_run:
        return C.result(0, owner, C.age(i), "end: run of %s stopped; it parked the phone and released the lock (see its log)" % i.get("owner"))
    out, code = park_with_own_lock(owner, "end of %s" % i.get("owner"), held=(i.get("owner", "?"), i.get("runid")))
    return C.result(code, owner, C.age(i), "end: holder of %s stopped, lock released, park=%s" % (i.get("owner"), out))


def enforce_limit(i):
    """tick: a live holder past its hard deadline + grace: kill pid and group, remove the lock, park. Returns the result code."""
    dl = hard_deadline(i)
    over = int(time.time() - dl)
    C.hist("HARD-LIMIT-REAP", i, "tick: %ds past the hard deadline (grace %ds): killing the holder" % (over, tick_grace(i)))
    print("phone-lock: HARD LIMIT: lock of %s is %ds past its deadline: killing pid %s and group %s" % (i.get("owner"), over, i.get("pid"), i.get("pgid")))
    for sig, wait in ((signal.SIGTERM, stop_grace()), (signal.SIGKILL, 10)):
        for fn, n in ((os.kill, i.get("pid")), (os.killpg, i.get("pgid"))):
            if n and n != os.getpgrp():
                try:
                    fn(n, sig)
                except (ProcessLookupError, PermissionError):   # lint-silent: expected already gone / not ours: liveness is re-read below
                    pass
        t = time.time() + wait
        while time.time() < t:
            j = C.read()
            if j is None or j.get("token") != i.get("token") or C.stale(j):
                break
            real_sleep(0.5)
        j = C.read()
        if j is None or j.get("token") != i.get("token") or C.stale(j):
            break
    j = C.read()
    if j is not None and j.get("token") == i.get("token"):
        if not C.stale(j) or not C.take_over_stale(j):
            return C.result(1, i.get("owner", "-"), C.age(i), "tick: HARD LIMIT: the holder of %s survived SIGKILL; lock kept (GAP)" % i.get("owner"))
    out, _ = park_with_own_lock("phone-lock-tick", "hard limit of %s" % i.get("owner"), held=(i.get("owner", "?"), i.get("runid")))
    rc = C.wake_next("phone-lock released (hard limit)", i.get("owner", ""))
    rc = root_fail_loud(i, "hard limit", rc)
    return C.result(rc, i.get("owner", "-"), C.age(i), "tick: HARD LIMIT ended the lock of %s (%ds over), park=%s root=%s" % (i.get("owner"), over, out, ROOT["state"]))


OPS = ("end", "park", "follow", "runs", "_detached", "tick", "run")


def seams_check():
    C.simseams.require_sandbox(SEAMS)
    for n in SEAMS:
        if os.environ.get(n):
            print("SIM SEAM ACTIVE: %s=%s" % (n, os.environ[n]), file=sys.stderr)


def limit_note(i):
    dl = hard_deadline(i)
    return " hard_limit_in=%ds" % int(dl - time.time()) if dl else ""


def add_lock_args(n, s):
    """the acquire/release/run options of this half (called per parser by phone-lock main)."""
    if n != "release":
        s.add_argument("--max", type=int, default=None, help="hard limit in s from the moment the lock is taken (run default %d, cap %d)" % (DEFAULT_MAX, MAXHOLD))
    if n == "run":
        s.add_argument("--detach", action="store_true", help="run in its own systemd user service; returns at once (DETACHED id=..)")
        s.add_argument("--notify", default=None, help="auto | none | thread:ID (default: auto with --detach, none otherwise)")
        s.add_argument("cmd", nargs=argparse.REMAINDER)


def add_parsers(sub):
    e = sub.add_parser("end")
    e.add_argument("--owner", default=os.environ.get("PHONE_OWNER") or "")
    e.add_argument("--reason", required=True)
    e.add_argument("--force", action="store_true")
    e.add_argument("--timeout", type=float, default=END_TIMEOUT, help="seconds to wait for stop + park + standard-root restore (default %d)" % END_TIMEOUT)
    k = sub.add_parser("park")
    k.add_argument("--owner", default=os.environ.get("PHONE_OWNER") or "agent-%d" % os.getpid())
    k.add_argument("--wait", type=float, default=0)
    f = sub.add_parser("follow")
    f.add_argument("id")
    f.add_argument("--timeout", type=float, default=C.MAXWAIT + MAXHOLD + 900)
    f.add_argument("--owner", default=None, help="with `last`: the newest run of this owner")
    sub.add_parser("runs")
    sub.add_parser("_detached").add_argument("dir")


def run_cmd(a):
    a.cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not a.cmd:
        return C.result(2, a.owner, 0, "run needs a command after --")
    if a.max is not None and not 0 < a.max <= MAXHOLD:
        return C.result(2, a.owner, 0, "--max must be 1..%d s (got %d)" % (MAXHOLD, a.max))
    if a.detach:
        return detach(a)
    a.notify = resolve_notify(a.notify, False)
    return run(a)


def dispatch(a):
    if a.op == "run":
        return run_cmd(a)
    if a.op == "tick":
        C.check_vanished()
        return tick()
    if a.op == "end":
        if not a.owner:
            return C.result(2, "-", 0, "end needs --owner NAME (or $PHONE_OWNER): the owner label of the run to stop")
        return end_cmd(a.owner, a.reason, a.force, a.timeout)
    if a.op == "park":
        out, code = park_with_own_lock(a.owner, "park request by %s" % a.owner, min(max(a.wait, 0), C.MAXWAIT))
        return C.result(code, a.owner, 0, "park=%s" % out)
    if a.op == "follow":
        return follow(a.id, a.timeout, a.owner)
    if a.op == "runs":
        return runs_cmd()
    return detached_main(a.dir)
