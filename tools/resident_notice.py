"""Notice routing of tools/resident (coordinator 2026-11-12: replaced threads kept waking on job notices addressed to the submitter):
owner files ~/.cache/pixelios/owners/<key> (first line = the current owner thread, 'former <id>' lines), resolved at send time by
resident_core.resolve_notice. Split out of resident_session_lib.py for the 800-line limit."""
import os
import re
import resident_core as rc


def event(line):   # resident_session_lib.event, late-bound (no import cycle)
    import resident_session_lib
    return resident_session_lib.event(line)


def owners_dir():
    return os.environ.get("PIXOS_OWNERS_DIR") or os.path.expanduser("~/.cache/pixelios/owners")


def load_owners():
    """{key: (current, [former])} from owners_dir() (one file per stream/goal key; `tools/resident owner` writes them)."""
    out, d = {}, owners_dir()
    for n in (sorted(os.listdir(d)) if os.path.isdir(d) else []):
        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", n):
            with open(os.path.join(d, n)) as f:
                out[n] = rc.parse_owner_file(f.read())
    return out


def job_notify(ph, job, line):
    """Send LINE for JOB to its current owner (resolved now, so a handoff also reroutes queued jobs); none = GAP in events.log."""
    tgt, how = rc.resolve_notice(job.get("thread"), job.get("owner_key"), load_owners())
    if not tgt:
        event("RESIDENT GAP: notice for job %s not sent (%s): %s" % (job["id"], how, line[:120]))
        return 0
    if tgt != job.get("thread"):
        event("RESIDENT NOTICE: job %s -> %s (%s; submitter %s)" % (job["id"], tgt, how, job.get("thread")))
    return ph.notify(tgt, line)
