"""xrun_early: the live early-stop decision of tools/xrun_report.py waitusb (fast-loop 2026-11-09, user goal "stop the run when its results are in").

  grace_of(run)    seconds to keep watching after every stop pattern was seen (run.json stop_grace, set by xrun when --expect-absent is given)
  decide(lines, run, stop, fails, all_seen, t)   -> (kind, detail): "fail" (a --fail-on line), "absent" (a forbidden --expect-absent line:
                   it cannot become unseen, so the run ends at once and scores FAIL), "seen" (every stop pattern seen first at t),
                   "stop" (seen and the grace window is over), or (None, None)
  absent_note(run) the report text for an early stop with --expect-absent: the absent patterns were checked to +Ns only
Stdlib only."""
import re, sys


def _float(v, what):
    try:
        return float(v or 0)
    except ValueError as e:
        print("xrun_early GAP: %s %r is not a number (%s): 0 used" % (what, v, e), file=sys.stderr)
        return 0.0


def grace_of(run):
    return _float(run.get("stop_grace"), "stop_grace")


def seen(lines, pats):
    return all(any(re.search(p, ln) for ln in lines) for p in pats)


def decide(lines, run, stop, fails, all_seen, t):
    hit = next((p for p in fails if seen(lines, [p])), None)
    if hit:
        return "fail", hit
    hit = next((p for p in (run.get("expect_absent", []) if stop else []) if seen(lines, [p])), None)
    if hit:
        return "absent", hit
    if stop and (all_seen is not None or seen(lines, stop)):
        first = t if all_seen is None else all_seen
        return ("stop" if t >= first + grace_of(run) else "seen"), first
    return None, None


def absent_note(run):
    if not (run.get("absent_window") and run.get("t_boot")):
        return ""
    return "; --expect-absent checked to +%ds only (--full = whole %ss)" % (
        _float(run["absent_window"], "absent_window") - _float(run["t_boot"], "t_boot"), run.get("duration") or "?")


def poll(d, run, stop, fails, st, t, lines, mark):
    """one waitusb check; st = {"all_seen": None}. Marks run.json through mark(d, [...]) and returns True when the run ends now (EARLY)."""
    kind, what = decide(lines, run, stop, fails, st["all_seen"], t)
    if kind in ("fail", "absent"):
        mark(d, ["early=%f" % t, "t_end=%f" % t, "early_fail=%s%s" % ("absent-violated: " if kind == "absent" else "", what)])
        return True
    if kind in ("seen", "stop") and st["all_seen"] is None:
        st["all_seen"] = what
        mark(d, ["stop_all_seen=%f" % what])
    if kind == "stop":
        mark(d, ["early=%f" % t, "t_end=%f" % t] + (["absent_window=%f" % t] if run.get("expect_absent") else []))
        return True
    return False


def next_check(st, run, nxt):
    """when to look again: the usual 10 s, or the end of the grace window if that comes first"""
    return nxt if st["all_seen"] is None else min(nxt, st["all_seen"] + grace_of(run))


def report_lines(run):
    out = []
    try:
        import crash_summary    # autorecover: CRASH: cause / symbolized backtrace / last log / landing, first in the report
        out += crash_summary.lines(run)
    except Exception as e:  # the report must still print when the summary breaks: one loud line instead
        out.append("CRASH GAP: crash_summary failed: %s: %s" % (type(e).__name__, e))
    try:
        import ucrash_summary   # USERLAND CRASHES: grouped userland CRASH lines, symbolicated first report per group, corpse/jetsam counts
        out += ucrash_summary.lines(run)
    except Exception as e:  # never a failed run: one loud line instead
        out.append("UCRASH GAP: ucrash_summary failed: %s: %s" % (type(e).__name__, e))
    if run.get("first_boot_after_flash"):   # tools/overlay_std.py counts boots per flash
        out.append("NOT COMPARABLE: %s after the userdata flash: iOS migration/first-launch work runs on this boot; do not score timings from it (OVERLAY STD FIRST BOOT)" % run["first_boot_after_flash"])
    if run.get("overlay_jobs"):
        out.append(run["overlay_jobs"])
    return out
