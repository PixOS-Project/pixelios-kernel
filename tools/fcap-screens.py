#!/usr/bin/env python3
"""fcap-screens: is every captured screen the screen the nav meant to reach? (user 2026-11-10 via the coordinator: no more invalid boots)

usage: tools/fcap-screens.py CAPDIR [--ref REFDIR] [--max D]
Compares each patch-0276 panel snapshot (snap-*.raw, label in the header) with the snapshot of the same label in a reference capture of a
known-good boot, on a coarse 24x52 grey thumbnail (layout, not pixels: clock digits, blur and icon content change between boots). Mean
absolute thumbnail difference > D (default 40/255) = that screen is not the expected one (Weather prompt, migration screen, app switcher...).
A label missing from the reference is reported, not judged. Last line: RESULT: PASS screens=N | RESULT: FAIL screens=N wrong=K (labels) |
RESULT: ERROR ... A FAIL means the boot is INVALID for anything measured on the wrong screens.
"""
import argparse
import glob
import os
import struct
import sys

try:
    from PIL import Image
except ImportError:
    print("RESULT: ERROR fcap-screens needs python3-pil")
    sys.exit(2)

MAGIC = 0x484E5053
SAME = 6.0   # thumbnail mean diff below which two snapshots are the same screen (clock digits / blur noise stay above 0 but small)
RENDER_ONLY = {'c20-applib-open', 'rt-applib'}   # App Library: black vs blurred backdrop is the defect, the route is checked by c22


def panels(cap):
    out = {}
    for pf in sorted(glob.glob(os.path.join(cap, "snap-*.raw"))):
        d = open(pf, "rb").read()
        if len(d) < 80:
            print("fcap-screens: %s too short, skipped (GAP)" % pf)
            continue
        mg, w, h = struct.unpack_from("<3I", d, 0)
        if mg != MAGIC:
            print("fcap-screens: %s bad magic, skipped (GAP)" % pf)
            continue
        lab = d[32:80].split(b"\0")[0].decode("ascii", "replace")
        im = Image.frombuffer("RGBA", (w, h), d[80:80 + w * h * 4], "raw", "BGRA", 0, 1).convert("L").resize((24, 52))
        out[lab] = list(im.tobytes())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cap")
    ap.add_argument("--ref", default=os.path.expanduser("~/pixelios-artifacts/fcap/ref-screens"))
    ap.add_argument("--max", type=float, default=40.0)
    a = ap.parse_args()
    cur, ref = panels(a.cap), panels(a.ref)
    if not cur:
        print("RESULT: ERROR fcap-screens no panel snapshots in %s" % a.cap)
        return 2
    if not ref:
        print("RESULT: ERROR fcap-screens no reference snapshots in %s" % a.ref)
        return 2
    wrong = []
    for lab in sorted(cur):
        if lab in RENDER_ONLY:   # these screens' look IS the defect under test (App Library backdrop): reported, never judged for nav validity
            d = sum(abs(x - y) for x, y in zip(cur[lab], ref.get(lab, cur[lab]))) / len(cur[lab])
            print("SCREEN %-24s diff %5.1f render-only (not judged)" % (lab, d))
            continue
        if lab not in ref:
            print("SCREEN %-24s no reference (not judged)" % lab)
            continue
        d = sum(abs(x - y) for x, y in zip(cur[lab], ref[lab])) / len(cur[lab])
        ok = d <= a.max
        print("SCREEN %-24s diff %5.1f %s" % (lab, d, "ok" if ok else "WRONG SCREEN"))
        if not ok:
            wrong.append(lab)
    # same-screen check (gputest-census1: all 19 snapshots were the same Apple News welcome page, and 11 still passed the mean diff against
    # white Settings references): two labels whose REFERENCES differ (> a.max) cannot show the same screen (< SAME). Every such pair is WRONG.
    def md(x, y):
        return sum(abs(p - q) for p, q in zip(x, y)) / len(x)
    labs = [l for l in sorted(cur) if l in ref]
    for i, la in enumerate(labs):
        for lb in labs[i + 1:]:
            if md(ref[la], ref[lb]) > a.max and md(cur[la], cur[lb]) < SAME:
                for l in (la, lb):
                    if l not in wrong:
                        print("SCREEN %-24s SAME SCREEN as %s (their references differ): WRONG SCREEN" % (l, lb if l == la else la))
                        wrong.append(l)
    if wrong:
        print("RESULT: FAIL screens=%d wrong=%d (%s): the boot is INVALID for these screens" % (len(cur), len(wrong), ",".join(wrong)))
        return 1
    print("RESULT: PASS screens=%d" % len(cur))
    return 0


if __name__ == "__main__":
    sys.exit(main())
