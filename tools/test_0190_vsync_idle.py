#!/usr/bin/env python3
"""Host test for xnu patch 0190 sweet-fb-vsync-idle (X-G7; no phone, no build). Reads the patch text.

    tools/test_0190_vsync_idle.py [--patch FILE]

The AppleCLCD vsync timer (0046/0056/0101) re-armed unconditionally. Patch 0190 re-arms it only while AppleCLCD::needsVsync() holds
and parks it otherwise; addClient restarts a parked timer. Checks on the patch's new side (context + added lines):
(a) the return expression of needsVsync, EVALUATED over its whole truth table (no client + clean + not blanking + no QR hold =
    park; each of the four reasons alone keeps the timer; a dirty frame while the panel is off, or a finished blank pass, does not);
(b) vsyncFired decides under fLock on both exits that used to re-arm (blank branch, normal end), records fVsyncIdle = !again before
    unlocking, re-arms only `if (again)` and otherwise logs `SWEET FB: vsync timer idle` (rule 8: the park is visible);
(c) the QR-hold exit still re-arms unconditionally and no other unconditional re-arm is left in vsyncFired;
(d) addClient reads and clears fVsyncIdle under fLock and, after unlocking, re-arms the timer and logs `vsync timer resumed`;
(e) the patch removes nothing but the two unconditional re-arms. Then the mutants of the patch text, each of which must FAIL a check.
Exit 0 PASS, 1 FAIL, 2 ERROR. Not run on hardware.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import pathlib, re, sys, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE.parent / "xnu" / "patches" / "0190-sweet-fb-vsync-idle.patch"
TERMS = {"fClients->getCount() != 0", "(fDirty && !fPowerOff)", "(fPowerOff && fFadeStep == 0)", "sweet_qr_hold"}


def new_side(text):
    """Context + added lines of every hunk of the SweetFramebuffer.cpp diff, one string (hunks joined by a marker line)."""
    out, in_hunk = [], False
    for l in text.split("\n"):
        if l.startswith("@@"):
            in_hunk = True
            out.append("@@" + (l.split("@@")[2] if l.count("@@") >= 2 else ""))   # keep the hunk heading: a hunk may start inside vsyncFired (0190 rebased onto 0149 by T-2b87)
        elif l.startswith("diff --git "):
            in_hunk = False
        elif in_hunk and l[:1] in (" ", "+"):
            out.append(l[1:])
    return "\n".join(out)


def removed(text):
    return [l for l in text.split("\n") if l.startswith("-") and not l.startswith("---")]


def needs_vsync_expr(n):
    m = re.search(r"AppleCLCD::needsVsync\(void\)\n\{\n\treturn (.*);\n\}", n)
    return m.group(1) if m else None


def evaluate(expr, clients, dirty, off, fade, qr):
    """Evaluate the C return expression with the AppleCLCD state; only the shapes the patch uses are translated."""
    py = (expr.replace("fClients->getCount()", "clients").replace("fDirty", "dirty").replace("fPowerOff", "off")
          .replace("fFadeStep", "fade").replace("sweet_qr_hold", "qr").replace("||", " or ").replace("&&", " and ")
          .replace("!off", " not off"))
    if re.search(r"[^A-Za-z0-9_ ()=!<>]", py.replace(" or ", "").replace(" and ", "")):
        raise ValueError("unexpected token in needsVsync expression: " + expr)
    return bool(eval(py, {"__builtins__": {}}, dict(clients=clients, dirty=dirty, off=off, fade=fade, qr=qr)))


def check(text):
    """Return a list of failure strings (empty = PASS)."""
    bad = []
    if "diff --git a/iokit/Kernel/arm/SweetFramebuffer.cpp b/iokit/Kernel/arm/SweetFramebuffer.cpp" not in text:
        return ["patch does not touch iokit/Kernel/arm/SweetFramebuffer.cpp"]
    n = new_side(text)

    # (a) needsVsync truth table (state: clients, dirty, off, fade, qr)
    expr = needs_vsync_expr(n)
    if expr is None:
        return ["(a) AppleCLCD::needsVsync() not found"]
    if set(expr.split(" || ")) != TERMS or expr.count("||") != 3:
        bad.append("(a) needsVsync terms are not exactly %s: %r" % (sorted(TERMS), expr))
    table = [  # (clients, dirty, off, fade, qr) -> must the timer keep running?
        ((0, False, False, 0, 0), False),   # the X-G7 case: no client, nothing to show -> park
        ((0, False, False, 5, 0), False),
        ((1, False, False, 0, 0), True),    # a registered client keeps vsync notifications flowing
        ((2, False, False, 0, 0), True),
        ((0, True, False, 0, 0), True),     # frame waiting, panel on
        ((0, True, True, 1, 0), False),     # stale dirty while off and blanked: nothing to show
        ((0, False, True, 0, 0), True),     # blank pass still to do
        ((0, False, True, 1, 0), False),    # blank pass done
        ((0, False, False, 0, 1), True),    # QR log holds the panel
        ((1, True, True, 1, 0), True),      # client present: always
    ]
    try:
        for state, want in table:
            if evaluate(expr, *state) != want:
                bad.append("(a) needsVsync%r should be %s" % (state, want))
    except (ValueError, SyntaxError, NameError) as e:
        bad.append("(a) %s" % e)

    # (b) both former re-arm exits decide under the lock, record the idle flag, re-arm only if needed, and log the park
    for ind, why in (("\t\t", "panel blanked"), ("\t", "nothing to show")):
        pat = (r"%sagain = fb->needsVsync\(\);\n%sfb->fVsyncIdle = !again;\n%sIOLockUnlock\(fb->fLock\);\n" % (ind, ind, ind))
        if not re.search(pat, n):
            bad.append("(b) %s exit: again/fVsyncIdle not decided before IOLockUnlock" % why)
        pat = (r"%sif \(again\) \{\n%s\tsweet_fb_rearm\(sender, now\);\n%s\} else \{\n%s\tIOLog\(\"SWEET FB: vsync timer idle \(no client, %s\)\\n\"\);\n%s\}"
               % (ind, ind, ind, ind, why, ind))
        if not re.search(pat, n):
            bad.append("(b) %s exit: re-arm not conditional on `again` with the idle log" % why)

    # (c) vsyncFired: QR-hold exit unchanged, and exactly three re-arms (qr hold + two `if (again)`)
    m = re.search(r"AppleCLCD::vsyncFired\(.*?\n\}\n", n, re.S)
    if not m:
        bad.append("(c) vsyncFired not in the patch context")
    else:
        body = m.group(0)
        if body.count("sweet_fb_rearm(sender, now)") != 3:
            bad.append("(c) vsyncFired has %d sweet_fb_rearm, expected 3 (qr hold + two conditional)" % body.count("sweet_fb_rearm(sender, now)"))
        qr = re.search(r"if \(sweet_qr_hold\) \{.*?\n\t\}\n", body, re.S)
        if not qr or "sweet_fb_rearm(sender, now);" not in qr.group(0):
            bad.append("(c) the QR-hold exit no longer re-arms")
        ls = body.split("\n")
        unc = [i for i, l in enumerate(ls) if "sweet_fb_rearm(sender, now)" in l and ls[i - 1].strip() != "if (again) {"]
        if len(unc) != 1 or not qr or ls[unc[0]] not in qr.group(0):
            bad.append("(c) unconditional re-arms in vsyncFired: %d, expected exactly the QR-hold one" % len(unc))

    # (d) addClient restarts the parked timer
    pat = (r"AppleCLCD::addClient\(.*?\{\n\tIOLockLock\(fLock\);\n\tfClients->setObject\(client\);\n\tbool kick = fVsyncIdle;\n\tfVsyncIdle = false;\n"
           r"\tIOLockUnlock\(fLock\);\n\tif \(kick\) \{\n.*?\tfVsync->setTimeoutUS\(SWEET_FB_VSYNC_US\);\n\t\tIOLog\(\"SWEET FB: vsync timer resumed \(client\)\\n\"\);\n\t\}\n\}")
    if not re.search(pat, n, re.S):
        bad.append("(d) addClient does not read/clear fVsyncIdle under fLock and re-arm + log after unlocking")
    if not re.search(r"\tbool\s+fVsyncIdle;", n):
        bad.append("(d) fVsyncIdle member missing")

    # (e) the only removed lines are the two unconditional re-arms
    rem = removed(text)
    if sorted(rem) != sorted(["-\t\tsweet_fb_rearm(sender, now);", "-\tsweet_fb_rearm(sender, now);"]):
        bad.append("(e) the patch removes lines other than the two unconditional re-arms: %r" % rem)
    return bad


def mutants(t):
    i = t.index("diff --git")    # mutate the diff, never the description line (it quotes the same expressions)

    def r(old, new, count=1):
        assert old in t[i:], old
        return t[:i] + t[i:].replace(old, new, count)
    drop = lambda term: r(" || " + term if term != "fClients->getCount() != 0" else term + " || ", "")
    return {
        "needsVsync: client term dropped": drop("fClients->getCount() != 0"),
        "needsVsync: dirty term dropped": drop("(fDirty && !fPowerOff)"),
        "needsVsync: blank-pending term dropped": drop("(fPowerOff && fFadeStep == 0)"),
        "needsVsync: qr-hold term dropped": drop("sweet_qr_hold"),
        "needsVsync: dirty ignores power-off": r("(fDirty && !fPowerOff)", "fDirty"),
        "needsVsync: always true": r("return fClients->getCount() != 0 ||", "return 1 ||"),
        "needsVsync: client term inverted": r("fClients->getCount() != 0", "fClients->getCount() == 0"),
        "normal exit re-arm unconditional": r("+\tif (again) {\n+\t\tsweet_fb_rearm(sender, now);\n+\t} else {",
                                              "+\tsweet_fb_rearm(sender, now);\n+\tif (!again) {"),
        "normal exit idle log removed": r('+\t\tIOLog("SWEET FB: vsync timer idle (no client, nothing to show)\\n");\n', "+\t\t;\n"),
        "blank exit re-arm unconditional": r("+\t\tif (again) {\n+\t\t\tsweet_fb_rearm(sender, now);\n+\t\t} else {",
                                             "+\t\tsweet_fb_rearm(sender, now);\n+\t\tif (!again) {"),
        "blank exit idle log removed": r('+\t\t\tIOLog("SWEET FB: vsync timer idle (no client, panel blanked)\\n");\n', "+\t\t\t;\n"),
        "normal exit: fVsyncIdle not recorded": r("+\tfb->fVsyncIdle = !again;\n", ""),
        "blank exit: fVsyncIdle not recorded": r("+\t\tfb->fVsyncIdle = !again;\n", ""),
        "normal exit: decided after unlock": r("+\tagain = fb->needsVsync();\n+\tfb->fVsyncIdle = !again;\n \tIOLockUnlock(fb->fLock);",
                                               " \tIOLockUnlock(fb->fLock);\n+\tagain = fb->needsVsync();\n+\tfb->fVsyncIdle = !again;"),
        "addClient: no restart": r("+\t\tfVsync->setTimeoutUS(SWEET_FB_VSYNC_US);\n", ""),
        "addClient: resume log removed": r('+\t\tIOLog("SWEET FB: vsync timer resumed (client)\\n");\n', "+\t\t;\n"),
        "addClient: idle flag not cleared": r("+\tfVsyncIdle = false;\n", ""),
        "addClient: kick read outside the lock": r("+\tbool kick = fVsyncIdle;\n+\tfVsyncIdle = false;\n \tIOLockUnlock(fLock);",
                                                   " \tIOLockUnlock(fLock);\n+\tbool kick = fVsyncIdle;\n+\tfVsyncIdle = false;"),
        "addClient: restart never taken": r("+\tif (kick) {", "+\tif (0) {"),
        "member fVsyncIdle removed": r("+\tbool          fVsyncIdle;", "+\tbool          fVsyncUnused;"),
        "QR-hold exit lost its re-arm": r(" \t\tIOLockUnlock(fb->fLock);\n \t\tsweet_fb_rearm(sender, now);\n \t\treturn;\n \t}\n \tif (fb->fPowerOff) {",
                                          " \t\tIOLockUnlock(fb->fLock);\n \t\treturn;\n \t}\n \tif (fb->fPowerOff) {"),
        "patch removes an unrelated line": t[:i] + re.sub(r"(?m)^(@@[^\n]*\n)", r"\1-\tstray removed line\n", t[i:], count=1),
    }


def main(argv):
    patch = PATCH
    if argv[1:2] == ["--patch"] and len(argv) == 3:
        patch = pathlib.Path(argv[2])
    elif len(argv) != 1:
        print("usage: test_0190_vsync_idle.py [--patch FILE]\nRESULT: ERROR bad arguments")
        return 2
    try:
        text = patch.read_text()
        base = check(text)
        for b in base:
            print("FAIL:", b)
        caught = 0
        ms = mutants(text)
        for name, mt in ms.items():
            if mt == text:
                print("ERROR: mutant did not mutate:", name)
                return 2
            hit = check(mt)
            print("mutant %-44s %s" % (name, "caught" if hit else "SURVIVED"))
            caught += bool(hit)
    except (OSError, AssertionError, KeyError, IndexError) as e:
        print("RESULT: ERROR %s: %s" % (type(e).__name__, e))
        return 2
    if base or caught != len(ms):
        print("RESULT: FAIL base_failures=%d mutants_caught=%d/%d" % (len(base), caught, len(ms)))
        return 1
    print("RESULT: PASS patch checks ok, mutants_caught=%d/%d" % (caught, len(ms)))
    return 0


class Patch0190(unittest.TestCase):
    """The gate runs `python3 -m unittest discover -s tools -p FILE`; without a TestCase it ran 0 tests (review pr-0182-r1 F1)."""

    def test_patch_and_mutants(self):
        rc = main([sys.argv[0]])
        sys.stdout.flush()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
