#!/usr/bin/env python3
"""Decode the `LINK R34` / `R35` / `R36` rows of a `--wlan-link=34|35|36` run (docs/research/40, goal W-C3: the first chip register writes) into named chip registers and check them against the expectations of research 40 s.3/s.4.

Usage: tools/s35-decode.py LOG_OR_DIR [--table soc/google/gs101/wlan_seq.json]

Input: the `LINK R3x row=<id> rd=0x<8 hex>` lines of the run (a file, or a directory searched recursively, e.g. a tools/xrun / probe-run log dir) and the `LINK summary ...` line. The row id is mapped to (step, window, register, before/after)
through the table (logged MEM rows of steps 34..36; the window is the last cfg 0x80 write before the row). Register layout: PCIe core powerctl at +0x1E8 (include_pcie_core.h:296; PowerOnRequest [11:8], ExtPwrStatus [19:16], PowerDomain id [31:28],
include_sbchipc.h:5199-5209), AI wrapper IOCTL 0x408 / RESET_CTL 0x800 / RESET_ST 0x804 (aidmp.h:168,175,176), SYS_MEM coreinfo +0, bankidx +0x10, bankinfo +0x40 (g4389 include_sbsysmem.h:166-167).
Steps present are decided by the summary (`reached=Snn`); each step has its checks (PASS/FAIL, research 40 s.3/s.4):
  S34  P1 powerctl before: PowerDomain id [31:28] != 0 (or, when the run logged `LINK S34 skip: SRPWR request`, bits 17 and 9 already set) and not all ones (U-N3: the PCIe core page + 0x1E8 is the SRPWR register DHD reaches via BAR0+0x2000+0x1E8); P2 powerctl after: request bit 9 (0x200) latched AND ExtPwrStatus bit 17 (0x20000) = domain 1 up; the status
       before the request is printed (U-N4, polarity, informative).
  S35  W1 SYS_MEM IOCTL after the reset sequence & 0x3FFF == 0x1 (CLK, FGC dropped); W2 RESET_CTL & 1 == 0; W3 RESET_ST == 0 (the up gate, same three tests as the table's one-shot gate rows); C1 coreinfo is not all ones; C2 nrb == 0 and nb >= 1 with the DHD rev < 12 masks
       (nb = coreinfo & 0x1F, nrb = (coreinfo & 0x3E0) >> 5; the brcmfmac decode (coreinfo & 0xF0) >> 4 is printed for comparison, U-N2); B1 bankidx reads back 0 (when the run logged `LINK S35 skip: bankidx select`, C2 instead needs nrb != 0 and B1 is informative: ROM banks, no bankidx write, the bankinfo is of the unselected bank); B2 bankinfo is not all ones, bank 0 size = 8192 * ((bankinfo & 0x7F) + 1) is printed with the uniform-bank
       estimate nb * bank0 (a GUESS: bank 0 only was read).
  S36  A1 ARM CA7 IOCTL after & 0x3FFF == 0x21 (CPUHALT 0x20 | CLK 0x1; the read-back proves the bit latches, not that the CA7 halts, U-N1); A2 RESET_CTL & 1 == 0; A3 RESET_ST == 0.
  All  E1 no logged word is 0xFFFFFFFF (a dead read), except a deliberately logged one is still flagged; E2 every row of the reached steps was logged; E3 the summary says restore=ok; E4 the run completed (stopped=none) - else FAIL naming the stop row and its table note.
The log's `LINK table sha=<16 hex>` line must equal the table's table_sha256[:16], else `RESULT: ERROR table sha mismatch` (exit 2).
Exit code 0 = all checks pass, 1 = a check failed / rows missing / the run stopped, 2 = usage / no data / table sha mismatch."""
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

R_RE = re.compile(r"LINK R(3[4-6]) row=(\d+) rd=0x([0-9A-Fa-f]{8})")
SUM_RE = re.compile(r"LINK summary reached=S(\d+) stopped=(\w+)(?: at=S(\d+) row=(\d+))? restore=(\w+)")
BANK_SKIP_RE = re.compile(r"LINK S35 skip: bankidx select[^\n]* row=(\d+)")
WRAP = {0x408: "IOCTL", 0x800: "RESET_CTL", 0x804: "RESET_ST"}
CORE = {0x000: "coreinfo", 0x010: "bankidx", 0x040: "bankinfo"}
WINDOWS = {0x18001000: "PCIe core", 0x18126000: "SYS_MEM wrapper", 0x18026000: "SYS_MEM core", 0x18120000: "ARM_CA7 wrapper"}
ALL1 = 0xFFFFFFFF


def table_rows(doc):
    """[(row id, step, window, offset, label, phase)] for the logged MEM rows of steps 34..36 in table order; phase is 'before' / 'after' (the first / second occurrence of a register in a step's window), '' for the core page."""
    win, out, seen = None, [], {}
    for r in doc["rows"]:
        if r["kind"] == "mmio_write" and r["blk"] == "CFG" and r["off"] == 0x80:
            win = r["value"]
        if r["step"] < 34 or not (r["kind"] == "read_only" and r["blk"] == "MEM" and r.get("log")):
            continue
        label = "powerctl" if win == 0x18001000 and r["off"] == 0x1E8 else WRAP.get(r["off"]) if win in (0x18126000, 0x18120000) else CORE.get(r["off"]) if win == 0x18026000 else "?"
        key = (r["step"], win, r["off"])
        seen[key] = seen.get(key, 0) + 1
        phase = "" if win == 0x18026000 else "before" if seen[key] == 1 else "after"
        out.append((r["id"], r["step"], win, r["off"], label, phase))
    return out


def powerctl_text(v):
    return f"domain id {(v >> 28) & 0xF}, ExtPwrStatus {(v >> 16) & 0xF:#x} (bit 17 {int(bool(v & 0x20000))}), PowerOnRequest {(v >> 8) & 0xF:#x} (bit 9 {int(bool(v & 0x200))})"


def word_text(label, v, win, bank_skipped=False):
    if label == "powerctl":
        return powerctl_text(v)
    if label == "IOCTL":
        return f"CLK {v & 1} FGC {(v >> 1) & 1}" + (f" CPUHALT {(v >> 5) & 1}" if win == 0x18120000 else "")
    if label == "RESET_CTL":
        return f"RESET {v & 1}" + ("  UPPER BITS SET" if v & 0xFFFFFFFE else "")
    if label == "coreinfo":
        return f"DHD(rev<12): nb {v & 0x1F} nrb {(v & 0x3E0) >> 5}; brcmfmac (0xF0): {(v & 0xF0) >> 4}"
    if label == "bankinfo":
        return "unselected bank (bankidx select skipped)" if bank_skipped else f"bank size {8192 * ((v & 0x7F) + 1)} bytes"
    return ""


def check(vals, rows, summary, notes, skipped=False, bank_skipped=False):
    """vals: row id -> word. Returns (lines, results) with results = list of (name, ok, text)."""
    by = {}
    for i, st, w, o, label, ph in rows:
        by[(st, w, o, ph)] = vals.get(i)
    res, lines = [], []
    reached = int(summary[0]) if summary else max((st for i, st, _, _, _, _ in rows if i in vals), default=0)
    for i, st, w, o, label, ph in rows:
        v = vals.get(i)
        pre = f"S{st} {WINDOWS[w]:<15} +{o:#05x} {label:<10} {ph:<6}"
        lines.append(f"{pre} (no log line)" if v is None else f"{pre} {v:#010x}  {word_text(label, v, w, bank_skipped)}".rstrip())
    g = lambda st, w, o, ph="": by.get((st, w, o, ph))

    def add(name, ok, text):
        res.append((name, ok, text))

    if reached >= 34:
        b, a = g(34, 0x18001000, 0x1E8, "before"), g(34, 0x18001000, 0x1E8, "after")
        up = b is not None and b & 0x20200 == 0x20200
        add("P1 powerctl before: domain id != 0, or (skip line logged) bits 17 and 9 already up, not all ones (U-N3, review 18)", b is not None and b != ALL1 and ((b >> 28) & 0xF != 0 or (skipped and up)) and (up or not skipped), f"{b:#010x}: {powerctl_text(b)}" if b is not None else "no row")
        if b is not None:
            lines.append(f"     U-N4: ExtPwrStatus bit 17 BEFORE the request = {int(bool(b & 0x20000))} (informative)")
        add("P2 powerctl after: request bit 9 latched and status bit 17 up", a is not None and a != ALL1 and bool(a & 0x200) and bool(a & 0x20000), f"{a:#010x}: {powerctl_text(a)}" if a is not None else "no row")
    if reached >= 35:
        W = 0x18126000
        io, rc, rs = g(35, W, 0x408, "after"), g(35, W, 0x800, "after"), g(35, W, 0x804, "after")
        add("W1 SYS_MEM IOCTL & 0x3FFF == 0x1 after the reset sequence", io is not None and io != ALL1 and io & 0x3FFF == 1, f"{io:#010x}" if io is not None else "no row")
        add("W2 SYS_MEM RESET_CTL & 1 == 0", rc is not None and rc != ALL1 and rc & 1 == 0, f"{rc:#010x}" if rc is not None else "no row")
        add("W3 SYS_MEM RESET_ST == 0", rs == 0, f"{rs:#010x}" if rs is not None else "no row")
        ci, bi, bk = g(35, 0x18026000, 0x000), g(35, 0x18026000, 0x010), g(35, 0x18026000, 0x040)
        add("C1 coreinfo is not all ones", ci is not None and ci != ALL1, f"{ci:#010x}" if ci is not None else "no row")
        if ci is not None and ci != ALL1:
            nb, nrb = ci & 0x1F, (ci & 0x3E0) >> 5
            if bank_skipped:
                add("C2 (bankidx select skipped) ROM banks present: nrb != 0 (DHD rev < 12 masks; U-N2)", nrb != 0, f"coreinfo {ci:#010x}: nb {nb}, nrb {nrb}; brcmfmac decode {(ci & 0xF0) >> 4}")
            else:
                add("C2 nrb == 0 and nb >= 1 (DHD rev < 12 masks; U-N2)", nrb == 0 and nb >= 1, f"coreinfo {ci:#010x}: nb {nb}, nrb {nrb}; brcmfmac decode {(ci & 0xF0) >> 4}")
        elif bank_skipped:
            add("C2 (bankidx select skipped) coreinfo shows ROM banks", False, "coreinfo missing or all ones")
        if bank_skipped:
            lines.append(f"     bankidx select SKIPPED (ROM banks, no write): bankidx read {bi:#010x}" if bi is not None else "     bankidx select SKIPPED (ROM banks, no write): no bankidx row")
        else:
            add("B1 bankidx reads back 0", bi == 0, f"{bi:#010x}" if bi is not None else "no row")
        add("B2 bankinfo is not all ones", bk is not None and bk != ALL1, f"{bk:#010x}" if bk is not None else "no row")
        if bk is not None and bk != ALL1 and bank_skipped:
            lines.append(f"     bankinfo {bk:#010x} is the UNSELECTED bank (no bank was selected): informative, no RAM size derived")
        elif bk is not None and bk != ALL1:
            size = 8192 * ((bk & 0x7F) + 1)
            est = f"; uniform-bank estimate {size * (ci & 0x1F) // 1024} KiB over nb {ci & 0x1F} [GUESS: bank 0 only was read]" if ci is not None and ci != ALL1 else ""
            lines.append(f"     bank 0 size {size} bytes ({size // 1024} KiB){est}")
    if reached >= 36:
        A = 0x18120000
        io, rc, rs = g(36, A, 0x408, "after"), g(36, A, 0x800, "after"), g(36, A, 0x804, "after")
        add("A1 ARM CA7 IOCTL & 0x3FFF == 0x21 (CPUHALT | CLK; U-N1)", io is not None and io != ALL1 and io & 0x3FFF == 0x21, f"{io:#010x}" if io is not None else "no row")
        add("A2 ARM CA7 RESET_CTL & 1 == 0", rc is not None and rc != ALL1 and rc & 1 == 0, f"{rc:#010x}" if rc is not None else "no row")
        add("A3 ARM CA7 RESET_ST == 0", rs == 0, f"{rs:#010x}" if rs is not None else "no row")
    add("E1 no word is 0xFFFFFFFF", all(v != ALL1 for v in vals.values()), ",".join(f"row {i}" for i, v in vals.items() if v == ALL1) or "ok")
    want = [i for i, st, _, _, _, _ in rows if st <= reached]
    missing = [i for i in want if i not in vals]
    add("E2 all rows of the reached steps logged", not missing, f"missing rows {missing}" if missing else f"{len(want)} rows")
    if summary is None:
        add("E3 summary restore=ok", False, "no `LINK summary` line")
    else:
        rch, stopped, at, row, restore = summary
        add("E3 summary restore=ok", restore == "ok", f"reached=S{rch} stopped={stopped}" + (f" at=S{at} row={row}" if at else "") + f" restore={restore}")
        if stopped != "none":
            add("E4 the run completed", False, f"stopped={stopped} at S{at} row {row}" + (f": {notes.get(int(row), '')}" if row else ""))
        else:
            add("E4 the run completed", True, "stopped=none")
    return lines, res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", nargs="?", help="log file or directory with `LINK R34/R35/R36 row=.. rd=0x..` lines")
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
    known = {i: st for i, st, _, _, _, _ in rows}
    vals = {}
    for m in R_RE.finditer(text):
        i = int(m.group(2))
        if known.get(i) == int(m.group(1)):	# the R-tag step must be the step of that row
            vals[i] = int(m.group(3), 16)
    if not vals:
        print("RESULT: ERROR no LINK R34/R35/R36 rows found in the input", file=sys.stderr)
        return 2
    sm = SUM_RE.findall(text)
    summary = sm[-1] if sm else None
    notes = {r["id"]: r.get("note", "") for r in doc["rows"]}
    bank_rows = [int(x) for x in BANK_SKIP_RE.findall(text)]
    bank_ids = {r["id"] for r in doc["rows"] if r.get("step") == 35 and r.get("blk") == "MEM" and r.get("off") == 0x10 and r.get("kind") in ("mmio_write", "wait_eq")}
    lines, res = check(vals, rows, summary, notes, "LINK S34 skip: SRPWR request" in text, bool(bank_rows))
    if bank_rows:
        res.append(("S1 bank skip rows", bool(bank_ids) and set(bank_rows) <= bank_ids, f"skip line rows {sorted(set(bank_rows))} must be S35 MEM 0x10 bankidx rows {sorted(bank_ids)}"))
    print("\n".join(lines))
    for nm, ok, txt in res:
        print(f"{'PASS' if ok else 'FAIL'} {nm}: {txt}")
    bad = [nm for nm, ok, _ in res if not ok]
    top = max((known[i] for i in vals), default=0)
    print(f"RESULT: {'FAIL' if bad else 'PASS'} S{top} {len(vals)} rows" + (f", failed: {'; '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
