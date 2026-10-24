#!/usr/bin/env python3
"""Decode the `LINK R33` rows of a `--wlan-link=33` run (docs/research/38, goal W-C2) into named chip registers and check them against the expectations of research 38 s.3/s.4.

Usage: tools/s33-decode.py LOG_OR_DIR [--table soc/google/gs101/wlan_seq.json]

Input: the `LINK R33 row=<id> rd=0x<8 hex>` lines of the run (a file, or a directory searched recursively, e.g. a tools/xrun / probe-run log dir) and the `LINK summary ...` line. The row id is mapped to
(window, register) through the table (rows of step 33, logged; the window is the last cfg 0x80 write before the row). Register layout: PMU AOB `pmuregs_t` (Broadcom include_sbchipc.h:55-62), AI wrapper
IOCTL 0x408 / RESET_CTL 0x800 / RESET_ST 0x804 (aidmp.h:168,175,176), SYS_MEM coreinfo +0 (brcmfmac-chip.c:166; offset 0 for DHD is a guess).
Checks (PASS/FAIL each, research 38 s.3/s.4; E4 is informative): E1 pmurev (pmucapabilities & 0xFF) == 0x28 (the EROM PMU rev 40; a GUESS until this run); E2/E3 wrapper RESET_CTL has no bit above bit 0 set (a GUESS);
E4 SYS_MEM up = IOCTL & 3 == 1 and RESET_CTL & 1 == 0 (down is an accepted outcome: the run then stops at the gate and no coreinfo row exists); E5 no logged word is 0xFFFFFFFF (no completion); E6 every row
that should exist did (the SYS_MEM core row only when SYS_MEM is up); E7 the summary says restore=ok. ARM CA7: IOCTL bit 0x20 = CPUHALT is read from source: brcmfmac applies ARMCR4_BCMA_IOCTL_CPUHALT (brcmfmac-chip.c:78) to BCMA_CORE_ARM_CA7 (:1067-1075, :1358); the DHD value is unknown; only reported.
The log's `LINK table sha=<16 hex>` line must equal the table's table_sha256[:16], else `RESULT: ERROR table sha mismatch` (exit 2).
Exit code 0 = all checks pass, 1 = a check failed / rows missing, 2 = usage / no data / table sha mismatch."""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("erom_decode", os.path.join(HERE, "erom-decode.py"))
ed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ed)

R33_RE = re.compile(r"LINK R33 row=(\d+) rd=0x([0-9A-Fa-f]{8})")
SUM_RE = re.compile(r"LINK summary reached=S(\d+) stopped=(\w+)(?: at=S(\d+) row=(\d+))? restore=(\w+)")
PMU = {0x600: "pmucontrol", 0x604: "pmucapabilities", 0x608: "pmustatus", 0x618: "min_res_mask", 0x61C: "max_res_mask"}
WRAP = {0x408: "IOCTL", 0x800: "RESET_CTL", 0x804: "RESET_ST"}
WINDOWS = {0x18012000: "PMU", 0x18120000: "ARM_CA7 wrapper", 0x18126000: "SYS_MEM wrapper", 0x18026000: "SYS_MEM core"}


def table_rows(doc):
    """[(row id, window, offset, label)] for the logged MEM rows of step 33, in table order."""
    win, out = None, []
    for r in doc["rows"]:
        if r["step"] != 33:
            continue
        if r["kind"] == "mmio_write" and r["blk"] == "CFG" and r["off"] == 0x80:
            win = r["value"]
        elif r["kind"] == "read_only" and r["blk"] == "MEM" and r.get("log"):
            label = PMU.get(r["off"]) if win == 0x18012000 else WRAP.get(r["off"]) if win in (0x18120000, 0x18126000) else "coreinfo" if win == 0x18026000 and r["off"] == 0 else None
            out.append((r["id"], win, r["off"], label))
    return out


def check(vals, rows, summary):
    """vals: row id -> word. Returns (lines, results) with results = list of (name, ok, text)."""
    by = {(w, o): vals.get(i) for i, w, o, _ in rows}
    res, lines = [], []
    for i, w, o, label in rows:
        v = vals.get(i)
        if v is None:
            lines.append(f"{WINDOWS[w]:<16} +{o:#05x} {label:<16} (no log line)")
            continue
        note = ""
        if label == "pmucapabilities":
            note = f"pmurev {v & 0xFF:#x} ({v & 0xFF})"
        elif label == "pmustatus":
            note = f"ALPAVAIL {int(bool(v & 8))} HTAVAIL {int(bool(v & 4))} SBCLKST {(v >> 4) & 3}"
        elif label == "IOCTL":
            note = f"CLK {v & 1} FGC {(v >> 1) & 1}" + (f" bit5(CPUHALT, brcmfmac) {(v >> 5) & 1}" if w == 0x18120000 else "")
        elif label == "RESET_CTL":
            note = f"RESET {v & 1}" + ("  UPPER BITS SET" if v & 0xFFFFFFFE else "")
        lines.append(f"{WINDOWS[w]:<16} +{o:#05x} {label:<16} {v:#010x}  {note}".rstrip())
    pmu_caps = by.get((0x18012000, 0x604))
    res.append(("E1 pmurev == 0x28", pmu_caps is not None and pmu_caps & 0xFF == 0x28, f"caps {pmu_caps:#010x}" if pmu_caps is not None else "no caps row"))
    for nm, w in (("E2 ARM_CA7 RESET_CTL upper bits 0", 0x18120000), ("E3 SYS_MEM RESET_CTL upper bits 0", 0x18126000)):
        v = by.get((w, 0x800))
        res.append((nm, v is not None and v & 0xFFFFFFFE == 0, f"{v:#010x}" if v is not None else "no row"))
    io, rc = by.get((0x18126000, 0x408)), by.get((0x18126000, 0x800))
    up = io is not None and rc is not None and io & 3 == 1 and rc & 1 == 0
    res.append(("E4 SYS_MEM up (informative)", True, "UP" if up else "DOWN (a gate stop is the accepted outcome; coreinfo not read)"))
    res.append(("E5 no word is 0xFFFFFFFF", all(v != 0xFFFFFFFF for v in vals.values()), ",".join(f"row {i}" for i, v in vals.items() if v == 0xFFFFFFFF) or "ok"))
    want = [i for i, w, _, _ in rows if up or w != 0x18026000]
    missing = [i for i in want if i not in vals]
    res.append(("E6 all expected rows logged", not missing, f"missing rows {missing}" if missing else f"{len(want)} rows"))
    if summary is None:
        res.append(("E7 summary restore=ok", False, "no `LINK summary` line"))
    else:
        reached, stopped, at, row, restore = summary
        res.append(("E7 summary restore=ok", restore == "ok", f"reached=S{reached} stopped={stopped}" + (f" at=S{at} row={row}" if at else "") + f" restore={restore}"))
        if up and stopped != "none":
            res.append(("E8 clean stop state", False, f"SYS_MEM is up but the run stopped ({stopped} at S{at} row {row})"))
        if not up and rc is not None and stopped == "none":
            res.append(("E8 clean stop state", False, "SYS_MEM is down but the run did not stop at the gate"))
    return lines, res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", nargs="?", help="log file or directory with `LINK R33 row=.. rd=0x..` lines")
    ap.add_argument("--table", default=os.path.join(HERE, "..", "soc/google/gs101/wlan_seq.json"))
    a = ap.parse_args(argv)
    if not a.log:
        ap.print_usage(sys.stderr)
        print("RESULT: ERROR no input", file=sys.stderr)
        return 2
    text = ed.read_text(a.log)
    with open(a.table) as fh:
        doc = json.load(fh)
    ok_sha, msg = ed.check_table_sha(text, doc["table_sha256"])
    if not ok_sha:
        print(f"RESULT: ERROR {msg}", file=sys.stderr)
        return 2
    if msg:
        print(msg, file=sys.stderr)
    rows = table_rows(doc)
    known = {i for i, _, _, _ in rows}
    vals = {int(m.group(1)): int(m.group(2), 16) for m in R33_RE.finditer(text) if int(m.group(1)) in known}
    if not vals:
        print("RESULT: ERROR no LINK R33 rows found in the input", file=sys.stderr)
        return 2
    sm = SUM_RE.findall(text)
    summary = sm[-1] if sm else None
    lines, res = check(vals, rows, summary)
    print("\n".join(lines))
    for nm, ok, txt in res:
        print(f"{'PASS' if ok else 'FAIL'} {nm}: {txt}")
    bad = [nm for nm, ok, _ in res if not ok]
    print(f"RESULT: {'FAIL' if bad else 'PASS'} S33 {len(vals)} rows" + (f", failed: {'; '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
