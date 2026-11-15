"""phone_lock_root: the ROOT step of the park in tools/phone-lock (imported by phone_lock_end.py; not run directly).

User 2026-11-11: ending a run early must always be safe. After a park that reached fastboot, while the lock is still held, restore the STANDARD root:
if the newest line of flashed-userdata.txt (a sha256, or `flashing` = interrupted flash) is not the sha of standard-root.txt, run
tools/fflash-userdata.sh <standard path> --yes-erase-userdata --reason "phone-lock park: ..." with PREFLIGHT_GATE_WAIVER. fflash's lock check
(`phone-lock check`) passes because the child inherits PHONE_LOCK_TOKEN (the holder's own token). The flash runs in its own session with its output in
<lock>.restore.log (no pipe: a killed holder must not kill the flash through SIGPIPE) and the lock's pgid points at it, so a SIGKILLed holder keeps the lock
until the flash ends. Outcome ROOT["state"]: ok-standard | restored | FAIL | GAP-> gap | off | skipped | not-attempted (one `PARK ROOT ...` line each).
Seams (sandbox only): PHONE_LOCK_RESTORE=CMD replaces fflash (CMD PATH --yes-erase-userdata --reason TEXT), PHONE_LOCK_RESTORE_MAX=S, PHONE_LOCK_RESTORE_ROOT=0 (off).
"""
import json, os, re, signal, subprocess, time

C = None   # the tools/phone-lock module (bind)
HERE = os.path.dirname(os.path.abspath(__file__))
FFLASH = os.path.join(HERE, "fflash-userdata.sh")
PARK_GOTO = 200               # tools/phone goto fastboot --timeout (iOS -> fastboot observed ~95-150 s after the UDP reset)
RESTORE_MAX = 900             # cap of one fflash-userdata.sh restore: the flash is ~7 min raw (~3 min from the SSD cache) + sha256 of the image + getvars
# stop 30 s TERM + 10 s KILL, park (goto fastboot up to PARK_GOTO + 60 s), restore (RESTORE_MAX), slack: how long a run's lock is kept alive for its end steps
PARK_BUDGET = 40 + PARK_GOTO + 60 + RESTORE_MAX + 120
END_TIMEOUT = PARK_BUDGET + 200   # `end --timeout` default = the whole chain above plus the signal/poll slack
ROOT = {"state": "-"}


def bind(core):
    global C
    C = core


def real_lock():
    return os.path.realpath(C.LOCK) == os.path.realpath(C.DEFAULT_LOCK)


def seam_num(name, default):
    v = os.environ.get(name)
    return float(v) if v else default


def lock_update(token, **kv):
    """rewrite the live lock's info.json (and .seen) with KV added, only while TOKEN is still its token; atomic. False when not done (a GAP line says why)."""
    i = C.read()
    if not i or i.get("token") != token:
        return False
    i.update(kv)
    try:
        for path in (C.INFO, C.SEEN):
            tmp = path + ".%d" % os.getpid()
            with open(tmp, "w") as f:
                json.dump(i, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    except OSError as e:
        print("phone-lock GAP: cannot update the lock record (%s): the hard limit may not cover the park/restore steps" % e)
        return False
    return True


def newest_state(path):
    """first field of the newest non-blank line of flashed-userdata.txt (what userdata holds: a sha256, or `flashing` for an interrupted flash); '' when none."""
    try:
        with open(path) as f:
            lines = [l.split() for l in f if l.strip()]
    except OSError:   # lint-silent: expected no state file yet: nothing was ever flashed (the caller treats '' as not standard)
        return ""
    return lines[-1][0] if lines else ""


def read_standard(stdf):
    """(sha, path, why) read exactly like fflash-userdata.sh: the first line that is not blank or a comment, sha = text before the FIRST space, path = the rest."""
    try:
        with open(stdf) as f:
            rows = [l.rstrip("\n") for l in f if not re.match(r"\s*(#|$)", l)]
    except OSError as e:
        return "", "", "cannot read: %s" % e
    sha, _, path = (rows[0] if rows else "").partition(" ")
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not path.strip():
        return "", "", "first line is not '<sha256> <path>' (one space; fflash reads it the same way)" if rows else "no line"
    return sha, path.strip(), ""


def root_plan():
    """what the root step must do: (kind, msg, std_sha, std_path, state_file, standard_file); kind off|skipped|gap|ok|need."""
    art = os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts")
    stdf = os.environ.get("STANDARD_ROOT") or os.path.join(art, "standard-root.txt")
    statef = os.environ.get("FLASH_STATE") or os.path.join(art, "flashed-userdata.txt")
    if os.environ.get("PHONE_LOCK_RESTORE_ROOT") == "0":
        return "off", "PARK ROOT off: PHONE_LOCK_RESTORE_ROOT=0 (test only): the standard root is NOT restored", "", "", statef, stdf
    if not os.environ.get("PHONE_LOCK_RESTORE") and not real_lock():
        return "skipped", "PARK ROOT skipped (test lock dir %s, no PHONE_LOCK_RESTORE seam)" % C.LOCK, "", "", statef, stdf
    sha, path, why = read_standard(stdf)
    if not sha:
        return "gap", "PARK ROOT GAP no valid standard root in %s (%s): no restore, userdata stays as it is" % (stdf, why), "", "", statef, stdf
    if newest_state(statef) == sha:
        return "ok", "PARK ROOT ok standard %s" % sha[:12], sha, path, statef, stdf
    return "need", "", sha, path, statef, stdf


def clean(x, n=100):
    return re.sub(r"[^ -~]", "?", str(x)).strip()[:n]


def run_restore(cmd, env, token):
    """run the flash in its own session, output to <lock>.restore.log; the lock's pgid points at it meanwhile. (rc, last lines of the log)."""
    keep = (C.read() or {}).get("pgid", 0)
    log = C.LOCK + ".restore.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        os.close(fd)
    lock_update(token, pgid=p.pid, phase="restore")
    note = ""
    try:
        rc = p.wait(timeout=seam_num("PHONE_LOCK_RESTORE_MAX", RESTORE_MAX))
    except subprocess.TimeoutExpired:
        rc, note = -1, "TIMEOUT: the restore flash was stopped after the cap (the state file says flashing: the next park flashes again)"
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
                p.wait(timeout=20)
                break
            except (ProcessLookupError, subprocess.TimeoutExpired):   # lint-silent: expected gone already / still dying: the next signal follows
                continue
    finally:
        lock_update(token, pgid=keep, phase="park")
    try:
        with open(log, errors="replace") as f:
            tail = f.read().splitlines()[-30:]
    except OSError:   # lint-silent: expected no log (the open above failed would have raised): empty tail
        tail = []
    return rc, "\n".join(tail + ([note] if note else []))


def plan_failure(path, sha):
    """why the flash must not start ('' = go): image and sidecar present, sidecar = the standard sha (fflash skips its standard check on an odd standard-root line)."""
    if not os.path.isfile(path) or not os.path.isfile(path + ".sha256"):
        return "standard root image or its .sha256 is missing: %s" % path
    try:
        with open(path + ".sha256") as f:
            got = (f.readline().split(" ")[0]).strip()
    except OSError as e:
        return "cannot read %s.sha256: %s" % (path, e)
    return "" if got == sha else "%s.sha256 is %s, not the standard sha %s" % (path, got[:12] or "empty", sha[:12])


def fail_line(why_fail, held, path):
    return ("phone-lock: PARK ROOT FAIL %s NEED USER: userdata is not the standard root (%s; flash log %s.restore.log); the lock is released anyway. With the phone in fastboot run: "
            "PREFLIGHT_GATE_WAIVER=\"user: standard root restore\" tools/phone-lock run --owner NAME -- tools/fflash-userdata.sh %s --yes-erase-userdata "
            "--reason \"standard root back\"" % (why_fail, held[:12], os.path.basename(C.LOCK), path))


def root_step(why, owner, runid):
    """after the phone reached fastboot, lock still held: flash the standard root back when userdata holds anything else. One PARK ROOT line."""
    kind, msg, sha, path, statef, stdf = root_plan()
    ROOT["state"] = {"ok": "ok-standard"}.get(kind, kind)
    if kind != "need":
        print("phone-lock: " + msg, flush=True)
        return
    reason = "phone-lock park: standard root back after %s run %s (%s) (user 2026-11-11)" % (clean(owner, 40), clean(runid or "-", 60), clean(why, 80))
    seam = os.environ.get("PHONE_LOCK_RESTORE")
    cmd = [seam or FFLASH, path, "--yes-erase-userdata", "--reason", reason]
    env = dict(os.environ, PREFLIGHT_GATE_WAIVER="user 2026-11-11: standard root restore at run end", STANDARD_ROOT=stdf, FLASH_STATE=statef)
    held = newest_state(statef) or "nothing recorded"
    t0 = time.time()
    why_fail = ""
    if cmd[0] == FFLASH and not real_lock():   # second guard (review 0295 F3): the real flash tool never runs for a test lock dir
        why_fail = "refused: the real fflash-userdata.sh must not run for a test lock dir (%s)" % C.LOCK
    else:
        why_fail = plan_failure(path, sha)
    if not why_fail:
        print("phone-lock: PARK ROOT restoring: userdata holds %s, standard is %s (%s): fflash-userdata.sh runs now, up to %ds" % (held[:12], sha[:12], os.path.basename(path), RESTORE_MAX), flush=True)
        try:
            rc, out = run_restore(cmd, env, os.environ.get("PHONE_LOCK_TOKEN", ""))
        except OSError as e:
            rc, out = -1, "%s: %s" % (type(e).__name__, e)
        tail = [l for l in out.splitlines() if l.strip()]
        res = ([l for l in tail if l.startswith("RESULT:")] or tail[-1:] or ["no output"])[-1]
        if rc != 0:
            why_fail = "rc=%d %s" % (rc, clean(res, 200))
        elif newest_state(statef) != sha:
            why_fail = "flash said ok but the state file's newest line is %s, not %s" % (newest_state(statef)[:12] or "empty", sha[:12])
    if why_fail:
        ROOT["state"] = "FAIL"
        print(fail_line(why_fail, held, path), flush=True)
        return
    ROOT["state"] = "restored"
    print("phone-lock: PARK ROOT restored %s in %d s" % (sha[:12], time.time() - t0), flush=True)
