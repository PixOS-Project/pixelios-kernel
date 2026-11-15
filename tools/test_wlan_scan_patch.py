#!/usr/bin/env python3
"""Text check of the kernel side of patch 0292 (live scan in keep mode) and of the two seq_cst lines of swl25_sc_request / swl25_sc_abort (review 0292 F3, F6). No phone, no kernel build.

    python3 tools/test_wlan_scan_patch.py       -> `RESULT: PASS wlan-scan-patch ...` / `RESULT: FAIL ...`

The host tests cover the state machine in sweet_wlan_l25_hs.h, but no test read the gates in bsd/dev/sweet_wlan.c (xnu/patches/0292-*.patch) that keep the scan and the re-join apart,
so deleting one passed every test. check_patch() asserts, on the new side of the patch and in this order:
  1. the sink is wired before the keep loop (x->scansink = sw_scan_sink) and Set 10 reaches swl25_sc_request;
  2. the RSSI refresh is gated on swl25_sc_idle;
  3. swl25_sc_step is called once per pass, BEFORE the re-join step, under the same stop conditions;
  4. the re-join step is gated on swl25_sc_idle (a re-join never runs while a scan runs or while the re-join is held after a link loss);
  5. the finished scan is handed to iOS (swl25_sc_take -> sweet_wlan_a80211_scan_done) outside the en0 lock, after the re-join step;
  6. after the loop: swl25_sc_abort, then swl25_sc_take, then sw_keep_end (iOS is always answered when the engine ends).
check_hs() asserts the store-then-load pair of F3 in sweet_wlan_l25_hs.h. Every assertion has a mutant (the text with the gate deleted or moved) that must fail it."""
import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = glob.glob(os.path.join(ROOT, "xnu", "patches", "0292-*.patch"))[0]
HS = os.path.join(ROOT, "shim", "sweet_wlan_l25_hs.h")


def added(text):
    """The new side of the patch (added and context lines, diff headers and removed lines dropped), one string with newlines."""
    return "\n".join(l[1:] for l in text.split("\n") if (l.startswith("+") and not l.startswith("+++")) or l.startswith(" "))


def _pos(a, pat, start=0):
    m = re.compile(pat).search(a, start)
    return -1 if m is None else m.start()


def check_patch(text):
    """[] when every gate is there and in order, else one string per problem."""
    a = added(text)
    bad = []
    sink = _pos(a, r"x->scansink = sw_scan_sink;")
    req = _pos(a, r"return swl25_sc_request\(&sw_sc, &sw_gx\);")
    rssi = _pos(a, r"swl25_sc_idle\(&sw_sc\) && mach_absolute_time\(\) - t_rssi >= iv_rssi")
    step_if = _pos(a, r"if \(!x->stopped && !x->d4dstop && !b->dead\) \{[^\n]*\n[^\n]*\n\t*\(void\)swl25_sc_step\(x, &sw_sc, ")
    rj_if = _pos(a, r"if \(x->klink != 0u && swl25_sc_idle\(&sw_sc\) && !x->stopped && !x->d4dstop && !b->dead\) \{")
    rj_call = _pos(a, r"swl25_rj_step\(x, &sw_rj, ")
    take_in = _pos(a, r"if \(swl25_sc_take\(&sw_sc, &sk, &sms, &sev, &spart\)\) \{\s*/\*[^\n]*\n\s*rc = sweet_wlan_a80211_scan_done\(sk, sms, sev\);")
    ends = _pos(a, r"en0 poll thread ends after")
    abort_ = _pos(a, r"swl25_sc_abort\(x, &sw_sc, ", max(ends, 0))
    take_out = _pos(a, r"swl25_sc_take\(&sw_sc, &sk, &sms, &sev, &spart\)", max(abort_, 0))
    done_out = _pos(a, r"\(void\)sweet_wlan_a80211_scan_done\(sk, sms, sev\);", max(take_out, 0))
    kend = _pos(a, r'sw_keep_end\(x, "keep: poll thread ends"\);', max(done_out, 0))
    for name, p in (("sink wired (x->scansink = sw_scan_sink)", sink), ("Set 10 reaches swl25_sc_request", req), ("RSSI refresh gated on swl25_sc_idle", rssi),
                    ("swl25_sc_step call under the stop conditions", step_if), ("re-join gated on swl25_sc_idle", rj_if), ("re-join step call", rj_call),
                    ("scan hand-over to iOS in the loop (swl25_sc_take -> scan_done)", take_in), ("poll thread end line", ends),
                    ("swl25_sc_abort after the loop", abort_), ("swl25_sc_take after the abort", take_out), ("scan_done after the abort's take", done_out),
                    ("sw_keep_end after the answer", kend)):
        if p < 0:
            bad.append("missing or out of order: " + name)
    if bad:
        return bad
    if not sink < step_if:
        bad.append("the sink is wired after the keep loop starts")
    if not step_if < rj_if < rj_call:
        bad.append("swl25_sc_step does not come BEFORE the re-join step")
    if not rj_call < take_in:
        bad.append("the scan hand-over comes before the re-join step (it must follow the en0 lock block)")
    if not ends < abort_ < take_out < done_out < kend:
        bad.append("the thread-end answer is out of order")
    if a.count("swl25_sc_idle(&sw_sc)") != 2:
        bad.append("swl25_sc_idle must gate exactly the RSSI refresh and the re-join (found %d uses)" % a.count("swl25_sc_idle(&sw_sc)"))
    return bad


def check_hs(text):
    """F3: swl25_sc_request stores req then loads off (seq_cst, returns ENXIO); swl25_sc_abort stores off then loads req (seq_cst)."""
    bad = []
    m = re.search(r"swl25_sc_request\(.*?\n}\n", text, re.S)
    req = m.group(0) if m else ""
    st = req.find("__atomic_store_n(&sc->req, 1u, __ATOMIC_SEQ_CST);")
    ld = req.find("if (__atomic_load_n(&sc->off, __ATOMIC_SEQ_CST)) {", max(st, 0))
    if st < 0 or ld < 0 or "return (int)SWL25_SC_ENXIO;" not in req[ld:]:
        bad.append("swl25_sc_request: no seq_cst store of req followed by a seq_cst load of off that returns ENXIO")
    m = re.search(r"swl25_sc_abort\(.*?\n}\n", text, re.S)
    ab = m.group(0) if m else ""
    st = ab.find("__atomic_store_n(&sc->off, 1u, __ATOMIC_SEQ_CST);")
    ld = ab.find("__atomic_load_n(&sc->req, __ATOMIC_SEQ_CST)", max(st, 0))
    if st < 0 or ld < 0:
        bad.append("swl25_sc_abort: no seq_cst store of off followed by a seq_cst load of req")
    return bad


def _delete(text, pat):
    new, n = re.subn(pat, "", text, count=1, flags=re.S)
    assert n == 1, pat
    return new


# (name, function text -> mutated text): each must make check_patch fail
PATCH_GATE_DELETIONS = [
    ("sink not wired", lambda t: _delete(t, r"\+\tx->scansink = sw_scan_sink;[^\n]*\n")),
    ("Set 10 does not reach the request", lambda t: t.replace("return swl25_sc_request(&sw_sc, &sw_gx);", "return 0;")),
    ("RSSI refresh not gated", lambda t: t.replace("swl25_sc_idle(&sw_sc) && mach_absolute_time() - t_rssi", "mach_absolute_time() - t_rssi")),
    ("scan step deleted", lambda t: t.replace("(void)swl25_sc_step(x, &sw_sc, ns_now / 1000000ull);", "")),
    ("scan step not under the stop conditions", lambda t: t.replace("if (!x->stopped && !x->d4dstop && !b->dead) {   /* patch 0292: the live scan", "if (1) {   /* patch 0292: the live scan")),
    ("re-join not gated on the scan", lambda t: t.replace("if (x->klink != 0u && swl25_sc_idle(&sw_sc) && !x->stopped", "if (x->klink != 0u && !x->stopped")),
    ("re-join gate deleted but a second idle added elsewhere", lambda t: t.replace("if (x->klink != 0u && swl25_sc_idle(&sw_sc) && !x->stopped", "if (x->klink != 0u && !x->stopped") + "\n+\t(void)swl25_sc_idle(&sw_sc);"),
    ("scan hand-over deleted", lambda t: t.replace("rc = sweet_wlan_a80211_scan_done(sk, sms, sev);", "")),
    ("scan hand-over not conditioned on a fresh result", lambda t: t.replace("if (swl25_sc_take(&sw_sc, &sk, &sms, &sev, &spart)) {   /* patch 0292: the live scan ended", "if (1) {   /* patch 0292: the live scan ended")),
    ("abort at thread end deleted", lambda t: _delete(t, r"\+\tswl25_sc_abort\(x, &sw_sc, [^\n]*\n")),
    ("take at thread end deleted", lambda t: t.replace("\n+\tif (swl25_sc_take(&sw_sc, &sk, &sms, &sev, &spart)) {\n+\t\t(void)sweet_wlan_a80211_scan_done(sk, sms, sev);\n+\t}", "")),
    ("scan_done at thread end deleted", lambda t: t.replace("(void)sweet_wlan_a80211_scan_done(sk, sms, sev);", ";")),
    ("abort after sw_keep_end", lambda t: _swap_end(t)),
]


def _swap_end(t):
    """Move the thread-end answer behind sw_keep_end."""
    m = re.search(r'(\+\tabsolutetime_to_nanoseconds\(mach_absolute_time\(\), &ns_now\);\n\+\tswl25_sc_abort.*?\n\+\t\}\n)( \tsw_keep_end\(x, "keep: poll thread ends"\);\n)', t, re.S)
    assert m, "thread-end block not found"
    return t[:m.start()] + m.group(2) + m.group(1) + t[m.end():]


def _order(t):
    """The scan step moved behind the re-join step (to the hand-over block)."""
    m = re.search(r"(\+\t\tif \(!x->stopped && !x->d4dstop && !b->dead\) \{\s*/\* patch 0292: the live scan.*?\n\+\t\t\}\n)", t, re.S)
    assert m, "scan step block not found"
    t2 = t[:m.start()] + t[m.end():]
    k = t2.index("+\t\tif (swl25_sc_take(&sw_sc, &sk, &sms, &sev, &spart)) {   /* patch 0292: the live scan ended")
    return t2[:k] + m.group(1) + t2[k:]


PATCH_GATE_DELETIONS.append(("scan step after the re-join step", _order))

HS_GATE_DELETIONS = [
    ("request: req store is not seq_cst", lambda t: t.replace("__atomic_store_n(&sc->req, 1u, __ATOMIC_SEQ_CST);", "__atomic_store_n(&sc->req, 1u, __ATOMIC_RELEASE);")),
    ("request: no re-check of off", lambda t: t.replace("if (__atomic_load_n(&sc->off, __ATOMIC_SEQ_CST)) {", "if (0) {")),
    ("request: re-check of off is not seq_cst", lambda t: t.replace("if (__atomic_load_n(&sc->off, __ATOMIC_SEQ_CST)) {", "if (__atomic_load_n(&sc->off, __ATOMIC_ACQUIRE)) {")),
    ("request: the raced request is accepted", lambda t: re.sub(r"(__atomic_load_n\(&sc->off, __ATOMIC_SEQ_CST\)\) \{[^\n]*\n\t\treturn \(int\))SWL25_SC_ENXIO;", r"\1 0;", t, count=1)),
    ("abort: off store is not seq_cst", lambda t: t.replace("__atomic_store_n(&sc->off, 1u, __ATOMIC_SEQ_CST);", "sc->off = 1u;")),
    ("abort: req load is not seq_cst", lambda t: t.replace("__atomic_load_n(&sc->req, __ATOMIC_SEQ_CST) != 0u", "sc->req != 0u")),
]


class ScanPatch(unittest.TestCase):
    def test_patch_has_every_gate(self):
        self.assertEqual(check_patch(open(PATCH).read()), [])

    def test_patch_mutants_fail(self):
        text = open(PATCH).read()
        for name, f in PATCH_GATE_DELETIONS:
            m = f(text)
            self.assertNotEqual(m, text, "mutant changed nothing: " + name)
            self.assertNotEqual(check_patch(m), [], "mutant survived: " + name)

    def test_hs_has_the_seq_cst_pair(self):
        self.assertEqual(check_hs(open(HS).read()), [])

    def test_hs_mutants_fail(self):
        text = open(HS).read()
        for name, f in HS_GATE_DELETIONS:
            m = f(text)
            self.assertNotEqual(m, text, "mutant changed nothing: " + name)
            self.assertNotEqual(check_hs(m), [], "mutant survived: " + name)


if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScanPatch))
    if r.wasSuccessful():
        print("RESULT: PASS wlan-scan-patch cases=%d patch_mutants=%d hs_mutants=%d" % (r.testsRun, len(PATCH_GATE_DELETIONS), len(HS_GATE_DELETIONS)))
        sys.exit(0)
    print("RESULT: FAIL wlan-scan-patch")
    sys.exit(1)
