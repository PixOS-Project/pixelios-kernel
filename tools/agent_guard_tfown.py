#!/usr/bin/env python3
"""agent_guard_tfown: agent-guard TF own-ticket predicate and fixtures (T-2197); imported by tools/agent-guard rule TF

A thread may keep committing the results of ITS OWN ticket after the claim moved on (R20 ticket-first, user 2026-11-07):
  - `waiting`: the thread is the ticket owner or the LAST claimant (Log `claimed by` / `adopted by` / `early resume by`, `pending` ignored);
  - `done` / `split` / `failed` closed at most TF_OWN_H hours ago (header `closed:`) by the thread (`closed-by:` in the Outcome) or, when the
    queue closed it (`closed-by: queue`), with the thread as last claimant in docs/queue/archive/<id>.log.
Never granted: a chat with no ticket, a thread that is not that ticket's owner/last claimant/closer, a ticket closed longer ago.
Every grant is logged by tf_own_ok (`own-ticket allow`).

  tools/agent_guard_tfown.py --self-test    predicate checks in a temp queue (the guard's own fixtures are tf_own_fixtures)
Last line: RESULT: PASS|FAIL agent_guard_tfown ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import calendar
import os
import re
import sys
import tempfile
import time

TF_OWN_H = 24
TF_CLAIM = re.compile(r"^- \S+ (?:claimed by|adopted by|early resume by) (\S+)")


def last_claimant(lines):
    """the newest real claimant in Log lines (`claimed by pending` is a placeholder), or ''."""
    claims = [m.group(1) for m in (TF_CLAIM.match(ln) for ln in lines) if m and m.group(1) != "pending"]
    return claims[-1] if claims else ""


def tf_own_tickets(root, thread, now=None):
    """[(ticket id, why)] tickets whose results `thread` may still commit though they are not `running` under it (see the module doc)."""
    qd, out = os.path.join(root, "docs", "queue"), []
    if not thread or thread in ("queue", "-"):
        return out
    now = time.time() if now is None else now
    try:
        names = sorted(os.listdir(qd))
    except OSError:
        return out
    for n in names:
        if not re.match(r"T-[0-9a-f]{4}-.*\.md$", n):
            continue
        try:
            txt = open(os.path.join(qd, n), errors="replace").read()
            hdr = txt.split("```", 2)[1]
        except (OSError, IndexError):   # lint-silent: expected a ticket renamed mid-scan or a queue file without a ticket block is not a ticket of this thread
            continue
        h = {k.strip(): v.strip() for k, _, v in (ln.partition(": ") for ln in hdr.splitlines()[1:])}
        st, tid = h.get("status", ""), h.get("id", "?")
        if st == "waiting":
            if thread in txt and (h.get("owner") == thread or last_claimant(txt.splitlines()) == thread):
                out.append((tid, "waiting"))
        elif st in ("done", "split", "failed"):
            try:
                age_h = (now - calendar.timegm(time.strptime(h.get("closed", ""), "%Y-%m-%dT%H:%MZ"))) / 3600.0
            except ValueError:   # lint-silent: expected a closed ticket without a parsable `closed:` stamp qualifies for nothing
                continue
            if not 0 <= age_h <= TF_OWN_H:
                continue
            who = next((ln[len("closed-by: "):].strip() for ln in txt.splitlines() if ln.startswith("closed-by: ")), "")
            last = ""
            if who != thread:   # closed by the queue/tick: the Log (archived at close) still names the last claimant
                try:
                    last = last_claimant(open(os.path.join(qd, "archive", tid + ".log"), errors="replace").read().splitlines())
                except OSError:   # lint-silent: expected a ticket closed without an archived Log has no claimant to name
                    last = ""
            if thread in (who, last):
                out.append((tid, "%s %.1fh ago, %s" % (st, age_h, "closed-by" if who == thread else "last claimant")))
    return out


def tf_own_ok(root, thread, tool, rel, log):
    """True (and one `own-ticket allow` line through `log(root, line)`) when `thread` may write for one of its own parked/closed tickets."""
    own = tf_own_tickets(root, thread)
    if own:
        log(root, "own-ticket allow tool=%s thread=%s path=%s ticket=%s (%s)" % (tool, thread, rel, own[0][0], own[0][1]))
    return bool(own)


def write_fixture_queue(qdir, now):
    """the fixture tickets (waiting / closed at several ages) under `qdir`; `closed:` stamps are relative to `now`."""
    stamp = lambda hours: time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(now - hours * 3600))  # noqa: E731
    fx = (("c001", "waiting", "closed: -", "- 2026-11-07T10:00Z claimed by pending\n- 2026-11-07T10:01Z adopted by FX-WAIT\n"),
          ("c002", "waiting", "closed: -", "- 2026-11-07T10:00Z adopted by FX-OLDWAIT\n- 2026-11-07T11:00Z claimed by FX-NEWWAIT\n"),
          ("c003", "done", "closed: " + stamp(2), "closed-by: FX-CLOSED\n"),
          ("c004", "split", "closed: " + stamp(3), "closed-by: FX-SPLIT\n"),
          ("c005", "failed", "closed: " + stamp(5), "closed-by: FX-FAIL\n"),
          ("c006", "done", "closed: " + stamp(30), "closed-by: FX-STALE\n"),
          ("c007", "done", "closed: " + stamp(2), "closed-by: queue\n"),
          ("c008", "done", "closed: " + stamp(2), "closed-by: queue\n"))   # c008: closed by the tick; its archived Log names the last claimant
    os.makedirs(os.path.join(qdir, "archive"), exist_ok=True)
    with open(os.path.join(qdir, "archive", "T-c008.log"), "w") as fh:
        fh.write("- 2026-11-07T10:00Z adopted by FX-OLDCLAIM\n- 2026-11-07T11:00Z claimed by FX-QCLAIM\n")
    for tid, st, cl, body in fx:
        with open(os.path.join(qdir, "T-%s-x.md" % tid), "w") as fh:
            fh.write("```ticket\nid: T-%s\nstatus: %s\nowner: -\n%s\n```\n## Log\n%s" % (tid, st, cl, body))


CASES = (("own ticket waiting (last adopter) -> allow, logged", "FX-WAIT", "allow", "c001"),
         ("waiting ticket claimed again by another thread: earlier claimant denied", "FX-OLDWAIT", "deny:TF", None),
         ("waiting ticket: the last claimant is allowed", "FX-NEWWAIT", "allow", "c002"),
         ("own ticket done 2 h ago by this thread -> allow, logged", "FX-CLOSED", "allow", "c003"),
         ("own ticket split 3 h ago by this thread -> allow", "FX-SPLIT", "allow", "c004"),
         ("own ticket failed 5 h ago by this thread -> allow", "FX-FAIL", "allow", "c005"),
         ("own ticket closed 30 h ago -> deny", "FX-STALE", "deny:TF", None),
         ("a ticket closed by the queue gives the thread `queue` no right -> deny", "queue", "deny:TF", None),
         ("ticket closed by the queue 2 h ago: its last claimant -> allow", "FX-QCLAIM", "allow", "c008"),
         ("ticket closed by the queue: an earlier claimant -> deny", "FX-OLDCLAIM", "deny:TF", None),
         ("another thread's parked/closed ticket -> deny", "FX-OTHER", "deny:TF", None))


def tf_own_fixtures(add, tmp):
    """agent-guard --self-test cases; run after tf_fixtures(), which builds <tmp>/tfsuper and the transcripts."""
    sup = os.path.join(tmp, "tfsuper")
    write_fixture_queue(os.path.join(sup, "docs", "queue"), time.time())
    tkt, chat = os.path.join(tmp, "tf-tkt.jsonl"), os.path.join(tmp, "tf-chat.jsonl")
    thread = lambda t: (lambda: os.environ.__setitem__("T3CODE_THREAD_ID", t) if t else os.environ.pop("T3CODE_THREAD_ID", None))  # noqa: E731
    W = lambda tp: dict(tool_name="Write", tool_input={"file_path": os.path.join(sup, "tools", "new"), "content": "x"}, transcript_path=tp, cwd=sup)  # noqa: E731
    log = os.path.join(sup, "logs", "ticket-first.log")
    logged = lambda who, tid: (lambda: "" if os.path.isfile(log) and "own-ticket allow tool=Write thread=%s path=tools/new ticket=T-%s" % (who, tid) in open(log).read()  # noqa: E731
                               else "+nolog:" + who)
    for name, who, want, tid in CASES:
        add("TF " + name, W(tkt), want, setup=thread(who), **({"check": logged(who, tid)} if tid else {}))
    add("TF chat with no ticket stays denied (R20)", dict(tool_name="Bash", tool_input={"command": "git commit -m x -- tools/keep"}, transcript_path=chat, cwd=sup),
        "deny:TF", setup=thread("FX-CHAT"))
    add("TF no thread id never matches a ticket -> deny", W(chat), "deny:TF", setup=thread(""))


def self_test():
    """the predicate over the fixture queue: the CASES table says who is granted which ticket."""
    bad = []
    with tempfile.TemporaryDirectory() as d:
        qd = os.path.join(d, "docs", "queue")
        os.makedirs(qd)
        now = time.time()
        write_fixture_queue(qd, now)
        for name, who, want, tid in CASES:
            got = [t for t, _ in tf_own_tickets(d, who, now)]
            if got != (["T-" + tid] if tid else []):
                bad.append("%s: %s got %s" % (name, who, got))
        if tf_own_tickets(d, "", now) or tf_own_tickets(os.path.join(d, "nowhere"), "FX-WAIT", now):
            bad.append("empty thread or missing queue must grant nothing")
    for b in bad:
        print("FAIL " + b)
    print("RESULT: %s agent_guard_tfown self-test cases=%d failures=%d" % ("FAIL" if bad else "PASS", len(CASES) + 1, len(bad)))
    return 1 if bad else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    print("RESULT: FAIL agent_guard_tfown is a module for tools/agent-guard; use --self-test")   # no silent stub (rule 8)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
