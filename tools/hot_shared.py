"""hot_shared: the SHARED hot-reload session (several threads, one held boot). Library of tools/hot and tools/live-cut.

State = directory <phone lock dir>.shared (sibling of .queue/.waiters; never inside the lock, which exists only after the boot is taken):
  session.json   {owner, kernel, kernel_sha, overlay, created, turn_max, max, runs}   written by `hot session start --shared`
  guests/NAME    one file per joined guest                                             written by `hot join`, removed by `hot leave`
  turn.json      {holder, since, pid, cap, queue:[{name,pid}]}                         the FIFO turn queue, changed only under mutex (flock)
  mutex          flock file
  turns.log, pushes.log   JSON lines (also in the session run dir once xrun made it)
A session is ACTIVE only while the phone lock is held by session.json's owner, its holder pid is alive and the lock started after
session.json was written; anything else is stale and refuses joins. Guests never get the token themselves: `hot` reads it from the live
lock for them (token_for) after the join check. Turns: one iteration at a time (owner and guests alike), FIFO, capped at turn_max.
"""
import contextlib, fcntl, hashlib, json, os, re, shutil, time

DEFAULT_TURN_MAX = 600
POLL = 0.2


class Refused(Exception):
    """A refusal: the message says why, what to do instead, and the escape if any."""


def lock_dir():
    return os.environ.get('PHONE_LOCK_DIR') or os.path.join(os.path.expanduser('~'), 'pixelios-artifacts/phone.lock')


def shared_dir(ld=None):
    return (ld or lock_dir()) + '.shared'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


def _jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:   # lint-silent: expected no such state file yet (caller default)
        return default


def _jsave(path, obj):
    tmp = '%s.%d' % (path, os.getpid())
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):   # lint-silent: expected dead/absent pid = not alive
        return False
    except PermissionError:   # lint-silent: expected EPERM = the pid exists under another uid
        pass
    return True


def mark_shared(owner, kernel, overlay, max_s, turn_max, runs, ld=None):
    sd = shared_dir(ld)
    shutil.rmtree(sd, ignore_errors=True)
    os.makedirs(os.path.join(sd, 'guests'), mode=0o700)
    rec = {'owner': owner, 'kernel': os.path.abspath(kernel), 'kernel_sha': sha256_file(kernel), 'overlay': os.path.abspath(overlay),
           'created': time.time(), 'turn_max': turn_max, 'max': max_s, 'runs': runs}
    _jsave(os.path.join(sd, 'session.json'), rec)
    _jsave(os.path.join(sd, 'turn.json'), {'holder': None, 'queue': []})
    return rec


def unmark(ld=None):
    shutil.rmtree(shared_dir(ld), ignore_errors=True)


def record(ld=None):
    return _jload(os.path.join(shared_dir(ld), 'session.json'))


def active_or_none(ld=None):
    """(rec, lock info) of the running shared session, else None."""
    try:
        return active(ld)
    except Refused:   # lint-silent: expected no active shared session (callers that need the reason call active())
        return None


def active(ld=None):
    ld = ld or lock_dir()
    rec = record(ld)
    if not rec:
        raise Refused('no shared hot session is marked (no %s): ask the session owner to start it with `tools/hot session start ... --shared`; '
                      'otherwise queue your own boot with tools/phone-lock run' % os.path.join(shared_dir(ld), 'session.json'))
    info = _jload(os.path.join(ld, 'info.json'))
    if info is None:
        raise Refused('the shared session of %s is over (the phone lock is free): start your own boot (phone-lock run) or ask %s to start a new --shared session'
                      % (rec['owner'], rec['owner']))
    if info.get('owner') != rec['owner'] or float(info.get('start') or 0) < rec['created'] - 2 or not _alive(info.get('pid')):
        raise Refused('the phone lock is held by %r, not by the shared session owner %r (or its holder is dead / older than the session mark): '
                      'no shared session runs now; queue your own boot with phone-lock run' % (info.get('owner'), rec['owner']))
    if not info.get('token'):
        raise Refused('the lock of %s has no token (not a phone-lock run)' % rec['owner'])
    return rec, info


def _log(name, ev, ld=None):
    ev = dict(ev, t=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    d = shared_dir(ld)
    rec = record(ld) or {}
    rd = run_dir(rec) if rec else None
    for base in {d, rd} - {None}:
        if os.path.isdir(base):
            with open(os.path.join(base, name), 'a') as f:
                f.write(json.dumps(ev, sort_keys=True) + '\n')


def run_dir(rec):
    """Newest logs/runs/*-hot-OWNER of the owner's checkout that has a usbnet.log (the live-cut --session), else None."""
    runs = rec.get('runs')
    tag = '-hot-' + re.sub(r'[^A-Za-z0-9._-]', '_', rec.get('owner', ''))
    try:
        names = sorted((n for n in os.listdir(runs) if n.endswith(tag) and os.path.isfile(os.path.join(runs, n, 'usbnet.log'))), reverse=True)
    except (TypeError, OSError):   # lint-silent: expected no runs dir yet (session run dir not made): caller falls back
        return None
    return os.path.join(runs, names[0]) if names else None


def join(me, kernel=None, ld=None):
    ld = ld or lock_dir()
    rec, _ = active(ld)
    if me == rec['owner']:
        raise Refused('%s is the session owner: use tools/hot --owner %s directly (join is for other threads)' % (me, me))
    notes = []
    if kernel:
        if not os.path.isfile(kernel):
            raise Refused('--kernel %s is not a file' % kernel)
        sha = sha256_file(kernel)
        if sha != rec['kernel_sha']:
            raise Refused('your kernel %s (sha256 %s) differs from the session kernel %s (sha256 %s): guests must be on the same kernel; '
                          'rebuild/pick the session kernel, or queue your own boot with phone-lock run' % (kernel, sha[:12], rec['kernel'], rec['kernel_sha'][:12]))
    else:
        notes.append('SWEET GAP: hot join: no --kernel given, your kernel is NOT verified against the session kernel %s (sha256 %s)' % (rec['kernel'], rec['kernel_sha'][:12]))
    _jsave(os.path.join(shared_dir(ld), 'guests', me), {'name': me, 'joined': time.time()})
    _log('turns.log', {'ev': 'JOIN', 'who': me}, ld)
    return rec, notes


def role(me, ld=None):
    """'owner' | 'guest' of the active session; Refused for anyone else."""
    ld = ld or lock_dir()
    rec, _ = active(ld)
    if me == rec['owner']:
        return 'owner'
    if os.path.isfile(os.path.join(shared_dir(ld), 'guests', me)):
        return 'guest'
    raise Refused('%s has not joined the shared hot session of %s: run `tools/hot join --owner %s --kernel K` first' % (me, rec['owner'], me))


def token_for(me, ld=None):
    ld = ld or lock_dir()
    role(me, ld)
    return active(ld)[1]['token']


def leave(me, ld=None):
    ld = ld or lock_dir()
    g = os.path.join(shared_dir(ld), 'guests', me)
    if not os.path.isfile(g):
        raise Refused('%s is not a guest of a shared hot session (nothing to leave)' % me)
    with _locked(ld):
        os.unlink(g)
        st = _turn(ld)
        if st.get('holder') == me:
            st['holder'] = None
        st['queue'] = [q for q in st['queue'] if q['name'] != me]
        _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
    _log('turns.log', {'ev': 'LEAVE', 'who': me}, ld)


@contextlib.contextmanager
def _locked(ld):
    sd = shared_dir(ld)
    if not os.path.isdir(sd):
        raise Refused('the shared session state %s is gone (session ended)' % sd)
    with open(os.path.join(sd, 'mutex'), 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _turn(ld):
    return _jload(os.path.join(shared_dir(ld), 'turn.json'), {'holder': None, 'queue': []})


def _expire(st, cap, ld):
    h = st.get('holder')
    if h and (time.time() - st['since'] > st.get('cap', cap) or (st.get('pid') and not _alive(st['pid']))):
        why = 'cap %ss' % st.get('cap', cap) if time.time() - st['since'] > st.get('cap', cap) else 'holder process died'
        st['holder'] = None
        _log('turns.log', {'ev': 'TURN EXPIRED', 'who': h, 'why': why}, ld)
    st['queue'] = [q for q in st['queue'] if _alive(q.get('pid'))]


def take(me, wait=1800, pid=0, ld=None, poll=POLL):
    """Block (FIFO) until it is ME's turn; pid != 0 ties the turn to that process (a single command), 0 = explicit (cap only)."""
    ld = ld or lock_dir()
    rec, _ = active(ld)
    cap = float(rec.get('turn_max') or DEFAULT_TURN_MAX)
    end = time.time() + wait
    announced = False
    while True:
        with _locked(ld):
            st = _turn(ld)
            _expire(st, cap, ld)
            if st.get('holder') == me:
                _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
                return 'already'
            if not st.get('holder') and (not st['queue'] or st['queue'][0]['name'] == me):
                st['queue'] = [q for q in st['queue'] if q['name'] != me]
                st.update(holder=me, since=time.time(), pid=pid, cap=cap)
                _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
                _log('turns.log', {'ev': 'TURN GRANT', 'who': me, 'cap': cap}, ld)
                return 'granted'
            if not any(q['name'] == me for q in st['queue']):
                st['queue'].append({'name': me, 'pid': os.getpid()})
                _log('turns.log', {'ev': 'TURN QUEUED', 'who': me, 'behind': st.get('holder')}, ld)
            _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
            holder, ahead = st.get('holder'), [q['name'] for q in st['queue']]
        if not announced:
            print('hot: waiting for my turn (held by %s, queue %s, cap %ss)' % (holder, ahead, cap), flush=True)
            announced = True
        if time.time() >= end:
            with _locked(ld):
                st = _turn(ld)
                st['queue'] = [q for q in st['queue'] if q['name'] != me]
                _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
            raise Refused('no turn within %ss (held by %s): try again later or raise --turn-wait; the holder\'s turn ends at its cap %ss' % (wait, holder, cap))
        time.sleep(poll)


def release(me, ld=None):
    ld = ld or lock_dir()
    with _locked(ld):
        st = _turn(ld)
        if st.get('holder') != me:
            raise Refused('%s does not hold the turn (it expired at its cap and was handed on, or was never taken): take a new one with `tools/hot turn take`' % me)
        st['holder'] = None
        _jsave(os.path.join(shared_dir(ld), 'turn.json'), st)
    _log('turns.log', {'ev': 'TURN END', 'who': me}, ld)


@contextlib.contextmanager
def turn(me, wait=1800, ld=None, poll=POLL):
    """One command inside my turn: pass-through when I hold an explicit turn, else take (tied to this process) and release."""
    res = take(me, wait, pid=os.getpid(), ld=ld, poll=poll)
    try:
        yield
    finally:
        if res == 'granted':
            try:
                release(me, ld)
            except Refused as e:   # the cap expired during a long command: loud, not fatal
                print('SWEET GAP: hot: %s' % e)


def note_push(me, rl, op, args, ld=None):
    _log('pushes.log', {'who': me, 'role': rl, 'op': op, 'args': list(args)}, ld)


def status(ld=None):
    rec, info = active(ld or lock_dir())
    st = _turn(ld or lock_dir())
    guests = sorted(os.listdir(os.path.join(shared_dir(ld), 'guests')))
    return rec, st, guests
