#!/usr/bin/env python3
"""run_preflight_plancheck: the PLAN-CHECK gate of tools/run-preflight (T-dd8e; user 2026-11-08 "Opus plans, Sonnet builds"). Stdlib; reads the plan text, writes one refusal line.

A boot of a stream plan (GPU / Wi-Fi / BT) or a HW-WRITE plan needs ONE read-only Opus plan check of exactly this plan text:
  `PLAN-CHECK: OK <one line> (opus-5-5, <UTC>, plan sha <8 hex>)`   written by the check thread (tools/queue plan-check T-x; brief docs/recipes/plan-check-brief.md)
The sha is plan_sha(text) = first 8 hex of sha256 over the plan with every `PLAN-CHECK:` line removed, so an edited plan needs a new check.
A stream plan also needs a CEILING line (numeric, or `unknown` with >= 3 witnesses) or >= 3 witnesses (c_ceiling refuses `unknown` with fewer only for knob boots).
Escape (loud, logged): `PLAN-CHECK-WAIVER: user <YYYY-MM-DD> "<the user's words>"`. Plans in .pre-method.txt are exempt. Host-only / tooling plans (no stream phase,
no stream intent, no Risk: HW-WRITE) are not touched.

  tools/run_preflight_plancheck sha PLAN.md     print the plan sha a PLAN-CHECK line must carry
  tools/run_preflight_plancheck --self-test
Last line: RESULT: PASS|FAIL run_preflight_plancheck ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_preflight_ceiling import c_ceiling as _c_ceiling, _plan_line, _witnesses  # noqa: E402

STREAM_INTENTS = ("gpu", "timing", "wlan", "bt")
STREAM_PHASE = re.compile(r"^(?:W-|P-W|G-|BT-)", re.I)   # GOALS rows: W-D*, P-W*, G-*, BT-S* (Wi-Fi, GPU, Bluetooth)
OK_RE = re.compile(r"^\W*PLAN-CHECK\W*:\s*OK\b(.*)$", re.I | re.M)
CHANGE_RE = re.compile(r"^\W*PLAN-CHECK\W*:\s*CHANGE\b(.*)$", re.I | re.M)
WAIVER_RE = re.compile(r"^\W*PLAN-CHECK-WAIVER\W*:\s*user\s+(\d{4}-\d\d-\d\d)\s+\"([^\"\n]{3,})\"", re.I | re.M)


def plan_sha(text):
    """First 8 hex of sha256 over the plan text without its `PLAN-CHECK:` lines (the check line may not be part of what it certifies)."""
    kept = "\n".join(l.rstrip() for l in text.splitlines() if not re.match(r"^\W*PLAN-CHECK\W*:", l, re.I))
    return hashlib.sha256(kept.strip().encode()).hexdigest()[:8]


def applies(text, intent):
    """-> why this plan needs a plan check (a short reason), or None for a host-only / tooling plan."""
    if intent in STREAM_INTENTS:
        return "stream intent %s" % intent
    ph = re.search(r"^\W*Phase\W*:[ \t]*([A-Za-z]{1,4}-[A-Za-z0-9]+)", text, re.M)
    if ph and STREAM_PHASE.match(ph.group(1)):
        return "stream phase %s" % ph.group(1)
    if re.search(r"^\W*Risk\W*:[ \t]*HW-WRITE\b", text, re.I | re.M):
        return "Risk: HW-WRITE"
    return None


def log_refusal(plan, d, why):
    """Append `UTC<TAB>plan<TAB>why` to <super>/logs/plan-check-refused.tsv (metric e, boots_refused_plan). A write error is a GAP line, never silent."""
    ad = os.path.abspath(d)
    root = os.environ.get("PIXOS_SUPER_ROOT") or (os.path.dirname(os.path.dirname(ad)) if ad.endswith(os.sep + os.path.join("docs", "runplans")) else None)
    if not root:   # a plan dir that is not <super>/docs/runplans (a test fixture): nowhere to count it
        return
    try:
        os.makedirs(os.path.join(root, "logs"), exist_ok=True)
        with open(os.path.join(root, "logs", "plan-check-refused.tsv"), "a") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), os.path.basename(plan), why))
    except OSError as e:
        print("run-preflight: PLAN-CHECK GAP: refusal not logged (%s)" % e, file=sys.stderr)


def _ceiling_gap(base, ce, n, why, esc, tip):
    if ce and re.match(r"unknown\b", ce, re.I) and n < 3:
        return ("c_plancheck: %s says 'CEILING: unknown' with %d witness(es) (%s). Why: an unknown ceiling is a measuring boot, which batches >= 3 witnesses (AGENTS rule 10). "
                "Do instead: list >= 3 on 'Witnesses: a; b; c', or replay the baseline for a numeric ceiling (tools/ceiling-check).%s%s" % (base, n, why, esc, tip))
    if not ce and n < 3:
        return ("c_plancheck: %s has no CEILING line and %d witness(es) (%s). Why: a stream boot states its best case or batches >= 3 witnesses (AGENTS rule 10; baseline: 1 of 45 plans had a "
                "numeric ceiling, T-dd8e). Do instead: add `CEILING: <value> (<script>, baseline <label>)` or `Witnesses: a; b; c`.%s%s" % (base, n, why, esc, tip))
    return None


def _check_gap(base, text, why, esc, tip):
    sha, oks = plan_sha(text), OK_RE.findall(text)
    shas = [m for l in oks for m in re.findall(r"plan sha ([0-9a-f]{8})", l)]
    if not oks:
        ch = CHANGE_RE.search(text)
        return ("c_plancheck: %s (%s) has no `PLAN-CHECK: OK` line%s. Why: the Opus plan check (oracle reproduces, numeric ceiling or >= 3 witnesses, prediction band, no duplicate knob) "
                "is what keeps a boot from chasing a metric that tracks the bench (8 G-11d boots, T-e3b7). Do instead: `tools/queue plan-check T-x --plan docs/runplans/%s` "
                "(one read-only Opus thread, <= 25 tool calls), apply its CHANGE list, re-run.%s%s" % (
                    base, why, " (the last check said CHANGE: %s)" % ch.group(1).strip()[:100] if ch else "", base, esc, tip))
    by_opus = any("opus-5-5" in l for l in oks)
    if sha not in shas or not by_opus:
        return ("c_plancheck: %s (%s) PLAN-CHECK: OK does not match this plan text (current plan sha %s, line says %s%s). Why: an edited plan is a different plan. "
                "Do instead: `tools/queue plan-check T-x --plan docs/runplans/%s` again.%s%s" % (
                    base, why, sha, ",".join(shas) or "none", "" if by_opus else ", not by opus-5-5", base, esc, tip))
    return None


def c_plancheck(plan, text, intent, d):
    base = os.path.basename(plan)
    why = applies(text, intent)
    if not why:
        return ("PASS", "")
    pre = os.path.join(d, ".pre-method.txt")
    if os.path.isfile(pre) and base in {l.strip() for l in open(pre) if l.strip() and not l.startswith("#")}:
        return ("PASS", "")
    tip = " (docs/recipes/plan-check-brief.md; docs/recipes/run-plan.md item 12; RULES H6)"
    esc = " Escape (loud, logged): a plan line `PLAN-CHECK-WAIVER: user <YYYY-MM-DD> \"<the user's words>\"`."
    wv = WAIVER_RE.search(text)
    if wv:
        print("run-preflight: PLAN-CHECK WAIVER: %s says: user %s \"%s\"" % (base, wv.group(1), wv.group(2)), file=sys.stderr)
        return ("PASS", "")
    bad = _ceiling_gap(base, _plan_line(text, "CEILING"), len(_witnesses(text)), why, esc, tip) or _check_gap(base, text, why, esc, tip)
    if bad:
        log_refusal(plan, d, "plan-check")
        return ("FAIL", bad)
    return ("PASS", "")


def c_ceiling(plan, text, intent, d):
    """Drop-in for run_preflight_ceiling.c_ceiling (the name run-preflight imports): the CEILING check first, then the PLAN-CHECK gate."""
    st, why = _c_ceiling(plan, text, intent, d)
    return (st, why) if st == "FAIL" else c_plancheck(plan, text, intent, d)


def self_test():
    sha = plan_sha("Phase: G-10b\nPLAN-CHECK: OK x (opus-5-5, t, plan sha 00000000)\nbody\n")
    ok = sha == plan_sha("Phase: G-10b\nbody") and sha != plan_sha("Phase: G-10b\nbody2")   # the check line is not part of the sha; an edit changes it
    ok = ok and applies("Phase: BT-S3\n", "none") and not applies("Phase: P-T1\n", "none") and applies("x", "gpu")
    print("RESULT: %s run_preflight_plancheck self-test" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if "--self-test" in argv:
        return self_test()
    if len(argv) == 2 and argv[0] == "sha" and os.path.isfile(argv[1]):
        print(plan_sha(open(argv[1], errors="replace").read()))
        print("RESULT: PASS run_preflight_plancheck sha")
        return 0
    print("RESULT: FAIL run_preflight_plancheck usage: sha PLAN.md | --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
