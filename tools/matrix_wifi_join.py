#!/usr/bin/env python3
"""Conformance-matrix check for the WPA2-PSK join block (SPEC-WIFI Part E rows H1-H12 and fallback F1-F7; PORT-METHOD S2; T-5dc1).
Usage: matrix_wifi_join.py --spec SPEC-WIFI.md --header shim/sweet_wlan_join.h [--block join]
       matrix_wifi_join.py --spec SPEC-WIFI.md --header shim/sweet_wlan_data.h --block data   (rows DP1-DP15, gap constants SWDP_GAP_DP<n>; T-71e2)
Rules (each failure prints `MATRIX FAIL: ...` and the run ends `RESULT: FAIL matrix-wifi-join`):
 - exactly the rows H1..H12 and F1..F7 exist once each in the spec tables (10 columns, last three: status, impl, gap);
 - status is implemented | partial | missing;
 - implemented/partial rows name an `impl` symbol that is present in the header; missing rows have impl `-`;
 - partial/missing rows name a `SWJ_GAP_<id>` constant that the header #defines with a string starting `WIFI GAP: <id> ` (rule 8);
 - implemented rows have gap `-`;
 - block data only (T-d3b0, Opus r1 F7): the header must also PRINT every partial/missing row's gap with `SWDP_GAP(SWDP_GAP_<id>)` (rule 8: a defined, never printed gap is a silent one).
Prints `RESULT: PASS matrix-wifi-join rows=19 implemented=N partial=N missing=N gaps=N spec=<path>`."""
import argparse, os, re, sys

ap = argparse.ArgumentParser()
ap.add_argument("--spec", required=True)
ap.add_argument("--header", required=True)
ap.add_argument("--block", choices=["join", "data"], default="join")
a = ap.parse_args()
def read_with_includes(path, seen=None):
    """The header text plus the text of every local #include "x.h" next to it (T-4115: the headers are split under R29)."""
    seen = set() if seen is None else seen
    if path in seen:
        return ""
    seen.add(path)
    text = open(path).read()
    for inc in re.findall(r'^#include "([^"]+)"', text, flags=re.M):
        sub = os.path.join(os.path.dirname(path), inc)
        if os.path.exists(sub):
            text += "\n" + read_with_includes(sub, seen)
    return text
spec, hdr = open(a.spec).read(), read_with_includes(a.header)
if a.block == "data":
    want, rowpat, gapprefix, label = ["DP%d" % i for i in range(1, 16)], r"^\|\s*(DP\d+)\s*\|", "SWDP_GAP_", "matrix-wifi-data"
else:
    want, rowpat, gapprefix, label = ["H%d" % i for i in range(1, 13)] + ["F%d" % i for i in range(1, 8)], r"^\|\s*([HF]\d+)\s*\|", "SWJ_GAP_", "matrix-wifi-join"
rows, errs = {}, []
for line in spec.splitlines():
    m = re.match(rowpat, line)
    if not m:
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if m.group(1) in rows:
        errs.append("duplicate row %s" % m.group(1)); continue
    if len(cells) != 10:
        errs.append("%s has %d columns, want 10" % (m.group(1), len(cells))); continue
    rows[m.group(1)] = cells
for r in want:
    if r not in rows:
        errs.append("row %s missing from the spec" % r)
for r in rows:
    if r not in want:
        errs.append("unexpected row %s" % r)
count = {"implemented": 0, "partial": 0, "missing": 0}
gaps = 0
for r in want:
    if r not in rows:
        continue
    st, impl, gap = (c.strip("`") for c in rows[r][7:10])
    if st not in count:
        errs.append("%s: status %r not implemented|partial|missing" % (r, st)); continue
    count[st] += 1
    if st == "missing":
        if impl != "-":
            errs.append("%s: missing row must have impl '-'" % r)
    elif not re.search(r"\b%s\b" % re.escape(impl), hdr):
        errs.append("%s: impl symbol %s not in the header" % (r, impl))
    if st == "implemented":
        if gap != "-":
            errs.append("%s: implemented row must have gap '-'" % r)
    else:
        if gap != gapprefix + r:
            errs.append("%s: gap must be %s%s, got %s" % (r, gapprefix, r, gap)); continue
        d = re.search(r"#define\s+%s\s+\"(WIFI GAP: %s [^\"]*)\"" % (gap, r), hdr)
        if not d:
            errs.append("%s: header has no #define %s \"WIFI GAP: %s ...\"" % (r, gap, r))
        elif a.block == "data" and not re.search(r"SWDP_GAP\(%s\)" % re.escape(gap), re.sub(r"/\*.*?\*/|//[^\n]*", "", hdr, flags=re.S)):   # a comment does not print it (Opus r2 N3)
            errs.append("%s: %s is defined but the header never prints it (no SWDP_GAP(%s)); rule 8" % (r, gap, gap))
        else:
            gaps += 1
for e in errs:
    print("MATRIX FAIL:", e)
if errs:
    print("RESULT: FAIL %s errors=%d" % (label, len(errs)))
    sys.exit(1)
print("RESULT: PASS %s rows=%d implemented=%d partial=%d missing=%d gaps=%d spec=%s" %
      (label, len(want), count["implemented"], count["partial"], count["missing"], gaps, a.spec))
