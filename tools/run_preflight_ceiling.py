#!/usr/bin/env python3
"""run_preflight_ceiling: the CEILING check of tools/run-preflight (c_ceiling, T-d4d9; moved out by T-c8df so run-preflight does not grow, R29) and filled_words. Stdlib, no I/O besides reading the plan dir."""
import os
import re
import sys

CEILING_INTENTS = ("gpu", "timing", "wlan")   # the stream intents (tools/run-intents.json): GPU frames/s, Wi-Fi level; BT runs ride intent none today
NUM = r"[-+]?\d+(?:\.\d+)?"


def _plan_line(text, key):
    m = re.search(r"^\W*" + key + r"\W*:[ \t]*(\S[^\n]*)$", text, re.I | re.M)
    return m.group(1).strip() if m else None


def _witnesses(text):
    """Distinct non-placeholder witnesses: items of a `Witnesses:` line (split on ';') and bullets of '## Batched diagnostics'."""
    items = []
    w = _plan_line(text, "Witnesses")
    if w:
        items += [x.strip() for x in w.split(";")]
    m = re.search(r"^## Batched diagnostics[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    for l in (m.group(1).splitlines() if m else []):
        if re.match(r"\s*[-*]\s+\S", l):
            items.append(l.strip()[1:].strip())
    return {x for x in items if filled_words(x) >= 1}


def c_ceiling(plan, text, intent, d):
    """T-d4d9 (user 2026-11-07, "trial and error instead of hill climb"; AGENTS rule 10): a stream boot (--intent gpu|timing|wlan, or a plan with a `Knob:` line) states how high the
    knob can possibly lift the number BEFORE the boot. Plan lines: `Knob: <the ONE change vs the baseline>` or `Witnesses: a; b; c` (>= 3), and
    `CEILING: <value> (<script>, baseline <label>)` with `Baseline score: <v> +- <noise> (<label>)` and `Ceiling check: RESULT: PASS ceiling-check script=S ceiling=C oracle=O scored=V`
    (tools/ceiling-check output: the script reproduced the scored baseline number first), the ceiling clearing baseline + noise (`Direction: lower` flips it);
    or `CEILING: unknown, witness boot` with >= 3 batched witnesses. Escape: `ceiling-waiver: <why, >= 10 chars>` (loud). Plans in .pre-method.txt are exempt (c_method)."""
    base = os.path.basename(plan)
    knob = _plan_line(text, "Knob")
    if not ((intent in CEILING_INTENTS) or (knob and not knob.startswith("<"))):
        return ("PASS", "")
    pre = os.path.join(d, ".pre-method.txt")
    if os.path.isfile(pre) and base in {l.strip() for l in open(pre) if l.strip() and not l.startswith("#")}:
        return ("PASS", "")
    tip = " (docs/recipes/run-plan.md item 11; docs/runplans/TEMPLATE.md; AGENTS rule 10)"
    wv = re.search(r"^\W*ceiling-waiver\W*:[ \t]*(\S[^\n]{9,})$", text, re.I | re.M)
    if wv:
        print("run-preflight: CEILING WAIVER: %s says: %s" % (base, wv.group(1).strip()), file=sys.stderr)
        return ("PASS", "")
    esc = " Escape (loud, logged): a plan line `ceiling-waiver: <why, >= 10 chars>`."
    ce = _plan_line(text, "CEILING")
    if not ce or ce.startswith("<"):
        return ("FAIL", "c_ceiling: %s has no filled 'CEILING:' line. Why: a stream boot without the best case the knob can reach is trial and error (7 G-F55 boots, 51.8..53.6 vs a 55.0 bench cap that a 0-boot replay showed, docs/reviews/T-fabe-proof.md). "
                        "Do instead: replay the baseline run with the cause removed (tools/ceiling-check) and write `CEILING: <value> (<script>, baseline <label>)`, or `CEILING: unknown, witness boot` with >= 3 batched witnesses.%s%s" % (base, esc, tip))
    if re.match(r"unknown\b", ce, re.I):
        n = len(_witnesses(text))
        if n < 3:
            return ("FAIL", "c_ceiling: %s says 'CEILING: unknown, witness boot' with %d batched witness(es). Why: an unknown ceiling is only allowed for a boot that measures it, which batches >= 3 witnesses (rule 10). "
                            "Do instead: list >= 3 witnesses on a 'Witnesses: a; b; c' line or as bullets under '## Batched diagnostics'.%s%s" % (base, n, esc, tip))
        print("run-preflight: CEILING UNKNOWN: %s is a witness boot (%d witnesses)" % (base, n), file=sys.stderr)
        return ("PASS", "")
    m = re.match(r"(" + NUM + r")\s*\(([^,)]+),\s*baseline\s+([A-Za-z0-9._-]+)\s*\)", ce)
    if not m:
        return ("FAIL", "c_ceiling: %s CEILING line '%s' is not '<value> (<script>, baseline <label>)' or 'unknown, witness boot'.%s%s" % (base, ce[:80], esc, tip))
    ceil, script, blab = float(m.group(1)), m.group(2).strip(), m.group(3)
    bs = _plan_line(text, "Baseline score")
    bm = re.match(r"(" + NUM + r")\s*(?:\+-|\+/-|\u00b1)\s*(" + NUM + r")", bs or "")
    if not bm:
        return ("FAIL", "c_ceiling: %s needs 'Baseline score: <value> +- <noise> (%s)' next to its CEILING. Why: the ceiling is compared with baseline + noise. Do instead: take the value and the same-stack noise from the baseline proof.%s%s" % (base, blab, esc, tip))
    val, noise = float(bm.group(1)), abs(float(bm.group(2)))
    lower = re.match(r"lower", _plan_line(text, "Direction") or "", re.I) is not None
    clears = ceil < val - noise if lower else ceil > val + noise
    if not clears:
        return ("FAIL", "c_ceiling: %s ceiling %s does not clear baseline %s %s noise %s (%s). Why: a knob whose best case is inside the noise cannot show a move, so the boot would only add a data point. "
                        "Do instead: pick a knob whose replay ceiling clears it, or batch >= 3 witnesses with 'CEILING: unknown, witness boot'.%s%s" % (base, m.group(1), val, "-" if lower else "+", noise, script, esc, tip))
    cc = _plan_line(text, "Ceiling check")
    cm = re.search(r"RESULT:\s*PASS\s+ceiling-check\b.*?\bceiling=(" + NUM + r").*?\bscored=(" + NUM + r")", cc or "")
    if not cm or abs(float(cm.group(1)) - ceil) > 1e-9 or abs(float(cm.group(2)) - val) > 1e-9:
        return ("FAIL", "c_ceiling: %s has no 'Ceiling check: RESULT: PASS ceiling-check ... ceiling=%s ... scored=%s' line (found '%s'). Why: the ceiling script must first reproduce the scored baseline number (oracle), "
                        "else its ceiling is unproven. Do instead: run tools/ceiling-check --script %s --run-dir <baseline run dir> --scored %s and paste its RESULT line.%s%s" % (base, m.group(1), bm.group(1), (cc or "")[:60], script, bm.group(1), esc, tip))
    return ("PASS", "")


def filled_words(t):
    """Words of a free-text cell after dropping <placeholders>, (parenthesised template hints) and HTML comments: the check '>= N words' must not pass on a TEMPLATE line copied verbatim."""
    return len(re.sub(r"<!--.*?-->|<[^>]*>|\([^)]*\)?", "", t, flags=re.S).split())
