#!/usr/bin/env python3
"""lint_g2_stores.py - bounds / ordering LINT for the Wi-Fi G2 store lists. NOT a simulator: it never models the dongle and proves nothing about
hardware behaviour (AGENTS rule 5/7). Host-only, stdlib only, never touches a phone. Replaces the Gemini "virtual MMIO simulator" idea
(tools/test_virtual_mmio.py is now the unit test of this file).

Usage:
  tools/lint_g2_stores.py --format run-log     FILE [--soc soc.json] [--board board.json]
  tools/lint_g2_stores.py --format golden-csv  FILE [--soc soc.json] [--board board.json]
Callers: tools/test_virtual_mmio.py (unit test, imports it) and tools/trace2seq.py (imports parse_golden). Intended documented command for the
PORT-METHOD conformance-matrix step on every new G2 run log before the Opus HW-WRITE review; that doc line is NOT written yet (driver). It is NOT in the gate
(tools/check-all.sh is not edited by this change).

INPUT FORMATS (rule 7; each is a REAL format, nothing is invented here):
 run-log    console lines of an XNU G2 run (logs/runs/*/usbnet.log or merged.log; merged.log may repeat lines, AP-141: prefer usbnet.log).
            Store lines, read from the printf calls of shim/sweet_wlan_g2.h at branch wip/w4-d4c (lines 524, 1168, 1179, 435) and
            observed in logs/runs/20261030T231749Z-wlan-runG2-d4c16/merged.log:
              SWEET WLAN: G2 store NAME (bp|off) 0xA old 0xO := 0xV read back 0xR[ (16-bit store, upper half unchanged)]   (:524)
              SWEET WLAN: G2 store NAME (host|off) 0xA old 0xO := 0xV read back 0xR                                         (:1179)
              SWEET WLAN: G2 store NAME off 0xA := 0xV (old and read-back not made, golden writes only)                    (:1168)
              SWEET WLAN: G2 store NAME off 0xA := 0 read back 0xR                                                          (:435)
            Space meaning (g2.h:1040-1073): bp = dongle backplane/TCM address, host = byte offset into the G1 host block,
            off = config-space or PCIe-core offset (which one: the NAME, table SPACE_OF_NAME below).
            Armed line (xnu/patches/0174-sweet-wlan-g2-first-dma.patch:835): SWEET WLAN: G2 armed: block pa 0xP + 0xN, alias 0xA inside [0xL, 0xH)
            Read evidence lines (g2.h:1437, 1525, 1345): `D4b ro ... ring2 w W0.. type 0xT` (ring-2 item 0), `D4b poll ... ring2 item K type 0xT`,
            `D4c exit-note poll ... ring2 item 1 w W0 ...` (type = W0 & 0xff: a msg_type 0x24 shows as w0 0x2800024 in the d4c16 log).
            Every other `SWEET WLAN:` or console line is counted as ignored=N on the RESULT line, never parsed. A line that starts a store/read family
            (`G2 store `, `D4b ro `, `D4b poll `, `D4c exit-note poll `, `G2 armed:`) and does not match its exact grammar is a FAIL naming file:line.
 golden-csv the CSV of `tools/trace-diff golden-regs DIR --block wifi [--bar0 PA --bar2 PA | --va-only]` (tools/trace-diff:14-18):
            `#` header lines, then `seq,ts,src,mark,caller,op,width,space,win,addr,val`; op W|R|CW|CR; space bar0|bar2|cfg|mmio|va. The golden-4 vmallocinfo is
            hashed (AP-132), so the join gives space=va only (333752 of 340031 rows, observed 2026-10-31); the golden has no host memory, so item payloads
            (the "read" of a D2H item) are NOT in it.

RULES
 A1 alignment     every store address is aligned to its width (run-log: 4, or 2 for a logged 16-bit store; golden: width/8). The brief says 4; the golden
                  holds real 16-bit stores (`CW 16 cfg 0x78`), which are 2-aligned by nature: stated deviation.
 A2 range         every store address inside the allowed range of its space, GENERATED from soc.json / board.json (no table in this file):
                  golden bar2: [0, soc wlan/tcm_bytes); run-log host: [0, armed block length) and the armed block inside board.json xnu_ram.
                  Every space whose soc.json key is missing is a GAP line and the rule is NOT CHECKED for it (REQUIRED_KEYS below). Nothing is guessed.
 R2 read-before-RD a D2H read-index store (`... d2h_r slot ... := V`) must come after the read of ring-2 item V-1 with a non-zero type
                  (W-D4c-ds-ack.md s.3 "consume before RD update", s.6.7 step 1). run-log only. Golden: NOT CHECKED (payloads are host memory, s.6 header).
 R3 ack-before-RD the first D2H-RD doorbell (0xDD0200NN, dhd_msgbuf.c:114) must come after the DS ack doorbell: run-log: the store named `d4c ack db0`;
                  golden: the first 0xFF0000NN doorbell after the HOSTREADY write (an inference, W-D4c-ds-ack.md s.6 header). Only the FIRST RD update is
                  covered: the golden itself holds `dd020003` right after `dd020002` with no ff between (observed), so a general rule would be false.
 R4 no-store-in-DEV_SLEEP between the ack doorbell and the wake doorbell only stores proven safe in W-D4c-ds-ack.md s.6.4 are allowed: host-memory stores,
                  PCIe-core +0x140 (DB0) and +0xC34 (mailbox mask); everything else (TCM/backplane `bp`, cfg, other core registers) is a finding
                  (s.6.7 step 5). Golden: the first ack->wake pair after HOSTREADY, dongle-side callers only (^(dhd|dhdpcie|si_|osl_|bcm|ai_), trace-diff
                  CALLERS minus the RC driver). Later DS cycles: NOT CHECKED (the host_active_cnt state is not in an MMIO trace, s.6.3).

UNKNOWN (these paths refuse to run, RESULT FAIL, never a guess):
 U-1 the BAR0 / PCIe-core / RC DBI / EP config / dongle RAM sizes: not in soc.json -> A2 NOT CHECKED for those spaces (printed as LINT GAP).
 U-2 whether a golden DS cycle after the first one follows the same ack/wake ordering: no datum -> NOT CHECKED.
 U-3 a run-log whose store NAME is not in SPACE_OF_NAME: FAIL (the name->space map is read from g2.h:213-232 and :406-415 and must be extended by a human).

RESULT: PASS (exit 0): no finding, no gap, nothing unchecked. FAIL (exit 1): a finding or a parse error. PARTIAL (exit 1): no finding but gaps or
unchecked rules. ERROR (exit 2): usage / unreadable file.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEF_SOC = os.path.join(ROOT, "soc", "google", "gs101", "soc.json")
DEF_BOARD = os.path.join(ROOT, "device", "google", "bluejay", "board.json")

# Constants quoted from the cited sources; each has its citation here, none is a hardware guess.
HOSTREADY_OFF, HOSTREADY_VAL = 0x144, 0x12345678      # g2.h:41-42 SWG_HOSTREADY / SWG_HOSTREADY_OFF (branch wip/w4-d4c @ebcddc3)
DB0_OFF, MBX_MASK_OFF = 0x140, 0xC34                   # g2.h:96 SWG_DB0_OFF, :94 SWG_MBX_MASK_OFF; golden DB0 = BAR0+0x2140 (W-D4c-ds-ack.md s.6.1)
DS_SAFE_OFF = {DB0_OFF: "DB0 doorbell", MBX_MASK_OFF: "mailbox mask"}   # W-D4c-ds-ack.md s.6.4 (a)(b)
DD_CLASS, FF_CLASS = 0xDD, 0xFF                        # doorbell value classes dhd_msgbuf.c:114 / :116 via g2.h SWG_D4C_DB_*
DONGLE_CALLER = re.compile(r"^(dhd|dhdpcie|si_|osl_|bcm|ai_)")   # tools/trace-diff CALLERS['wifi'] minus pcie_/exynos_pcie_rc_ (RC side)

# store NAME -> space for `off` stores (g2.h:213-232 swg_g2 table rows with SWG_G_CMD, :406-415 swg_d4_name, :1040-1073 swg_d4_where).
OFF_CORE = ("hostready", "d4b mask", "d4b unmask")
OFF_CFG_RC, OFF_CFG_EP = "rc command", "ep command"
RE_NAME_DB0 = re.compile(r"^d4[bc]( \w+)? db0$")


def space_of(space, name):
    """Return the logical space of a run-log store, or None when the (space, NAME) pair is not in the source tables."""
    if space == "bp":
        return "tcm"
    if space == "host":
        return "host"
    if name in OFF_CORE or RE_NAME_DB0.match(name):
        return "pcie_core"
    if name == OFF_CFG_RC:
        return "rc_cfg"
    if name == OFF_CFG_EP:
        return "ep_cfg"
    return None


# space -> soc.json keys that must exist for A2 to be checkable. All but tcm_bytes are ABSENT today (observed in soc.json 2026-10-31): they become GAPs.
REQUIRED_KEYS = {
    "bar2": ("wlan/tcm_bytes",),
    "bar0": ("wlan/bar0_bytes",),
    "tcm": ("wlan/ram_base", "wlan/ram_bytes"),
    "pcie_core": ("wlan/pcie_core_bytes",),
    "rc_cfg": ("wlan/dbi_bytes",),
    "ep_cfg": ("wlan/ep_cfg_bytes",),
}


class ParseError(Exception):
    def __init__(self, where, msg):
        Exception.__init__(self, "%s: %s" % (where, msg))


def _get(d, path):
    for k in path.split("/"):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _num(v):
    return int(v, 0) if isinstance(v, str) else v


def allowlist_from_soc(soc, board):
    """Allowed ranges per space from soc.json/board.json. Returns (ranges, gaps): ranges[space] = (lo, hi, source), gaps = list of GAP texts."""
    ranges, gaps = {}, []
    for sp, keys in REQUIRED_KEYS.items():
        vals = [_get(soc, k) for k in keys]
        miss = [k for k, v in zip(keys, vals) if v is None]
        if miss:
            gaps.append("space %s: soc.json lacks %s" % (sp, ", ".join(miss)))
            continue
        if sp == "bar2":
            ranges["bar2"] = (0, _num(vals[0]), "soc.json wlan/tcm_bytes")
        elif sp == "tcm":
            ranges["tcm"] = (_num(vals[0]), _num(vals[0]) + _num(vals[1]), "soc.json wlan/ram_base+ram_bytes")
        else:
            ranges[sp] = (0, _num(vals[0]), "soc.json " + keys[0])
    ram = board.get("xnu_ram") if isinstance(board, dict) else None
    if ram is None:
        gaps.append("space host: board.json lacks xnu_ram")
    else:
        ranges["xnu_ram"] = (_num(ram[0]), _num(ram[0]) + _num(ram[1]), "board.json xnu_ram")
    return ranges, gaps


# ---- run-log parser ----
H = r"0x([0-9a-fA-F]+)"
RE_STORE_PRE = re.compile(r"SWEET WLAN: G2 store ")
N0 = r"(?:0x[0-9a-fA-F]+|0)"    # printf %#x: zero prints as a bare 0 (observed in the d4c16 log: `host 0 old 0 := 0x180000d`)
RE_ST = [
    re.compile(r"SWEET WLAN: G2 store (?P<n>.+?) (?P<s>bp|off) (?P<a>" + N0 + ") old " + N0 + " := (?P<v>" + N0 + ") read back " + N0 + r"(?P<w16> \(16-bit store, upper half unchanged\))?\s*$"),
    re.compile(r"SWEET WLAN: G2 store (?P<n>.+?) (?P<s>host|off) (?P<a>" + N0 + ") old " + N0 + " := (?P<v>" + N0 + ") read back " + N0 + r"(?P<w16>)\s*$"),
    re.compile(r"SWEET WLAN: G2 store (?P<n>.+?) (?P<s>off) (?P<a>" + N0 + ") := (?P<v>" + N0 + r") \(old and read-back not made, golden writes only\)\s*$"),
    re.compile(r"SWEET WLAN: G2 store (?P<n>.+?) (?P<s>off) (?P<a>" + N0 + ") := (?P<v>0) read back " + N0 + r"(?P<w16>)\s*$"),
]
RE_ARMED = re.compile(r"SWEET WLAN: G2 armed: block pa " + H + " \\+ " + H + ", alias " + H + " inside \\[" + H + ", " + H + r"\)\s*$")
RE_RO = re.compile(r"SWEET WLAN: D4b ro h2d_r0 " + H + " d2h_w0 host " + H + " tcm " + H + " ring2 w " + H + " " + H + " " + H + " " + H + " " + H + " " + H + " type " + H + " epoch " + H + r" xor \S+ c30 " + H + r"\s*$")
RE_POLL = re.compile(r"SWEET WLAN: D4b poll h2d_r0 " + H + " d2h_w0 " + H + r" ring2 item (\d+) type " + H + " epoch " + H + r" xor \S+ c30 " + H + " tcm d2h_w " + H + " " + H + " " + H + r"\s*$")
RE_NOTE = re.compile(r"SWEET WLAN: D4c exit-note poll after (\d+) ms: h2d_r0 " + H + " d2h_w0 " + H + r" ring2 item (\d+) w " + H + " " + H + " " + H + " " + H + " " + H + " " + H + r"\s*$")
RE_DONE = re.compile(r"SWEET WLAN: G2 done rc=(-?\d+) stores=(\d+) mask 0x[0-9a-fA-F]+ restore=\w+\s*$")   # observed d4c16; shim/wlan_g2_host_test.c:2037
FAMILY = re.compile(r"SWEET WLAN: (G2 armed:|G2 done |D4b ro |D4b poll |D4c exit-note poll )")


def parse_runlog(lines, src):
    """-> (events, ignored). event = dict(kind, line, ...). Raises ParseError on a family line that does not match."""
    ev, ignored = [], 0
    for n, line in enumerate(lines, 1):
        where = "%s:%d" % (src, n)
        if RE_STORE_PRE.search(line):
            m = next((r.search(line) for r in RE_ST if r.search(line)), None)
            if m is None:
                raise ParseError(where, "unparsable G2 store line: %r" % line.strip()[:160])
            d = m.groupdict()
            addr, val = int(d["a"], 0), int(d["v"], 0)
            sp = space_of(d["s"], d["n"])
            if sp is None:
                raise ParseError(where, "store name %r in space %r is not in the source tables (U-3)" % (d["n"], d["s"]))
            ev.append(dict(kind="store", line=n, where=where, name=d["n"], space=sp, raw_space=d["s"], addr=addr, val=val, w16=bool(d.get("w16"))))
        elif FAMILY.search(line):
            m = RE_ARMED.search(line)
            if m:
                pa, ln = int(m.group(1), 16), int(m.group(2), 16)
                ev.append(dict(kind="armed", line=n, where=where, pa=pa, length=ln))
                continue
            m = RE_DONE.search(line)
            if m:
                ev.append(dict(kind="done", line=n, where=where, stores=int(m.group(2))))
                continue
            m = RE_RO.search(line)
            if m:
                ev.append(dict(kind="read", line=n, where=where, item=0, type=int(m.group(10), 16)))
                continue
            m = RE_POLL.search(line)
            if m:
                ev.append(dict(kind="read", line=n, where=where, item=int(m.group(3)), type=int(m.group(4), 16)))
                continue
            m = RE_NOTE.search(line)
            if m:
                ev.append(dict(kind="read", line=n, where=where, item=int(m.group(4)), type=int(m.group(5), 16) & 0xFF))
                continue
            raise ParseError(where, "unparsable family line: %r" % line.strip()[:160])
        else:
            ignored += 1
    return ev, ignored


def check_runlog(ev, ranges):
    """-> (findings, unchecked). findings = list of (rule, where, msg); unchecked = list of 'RULE: reason'."""
    findings, unchecked, seen_gap = [], [], set()
    if not any(e["kind"] == "store" for e in ev):
        raise ParseError("input", "no `SWEET WLAN: G2 store` line at all (empty or wrong file)")
    armed = None
    reads, ack_seen, dd_seen, ds, nlogged = {}, False, False, False, 0
    for e in ev:
        if e["kind"] == "armed":
            armed = e
            reads, ack_seen, dd_seen, ds, nlogged = {}, False, False, False, 0
            xr = ranges.get("xnu_ram")
            if xr and not (xr[0] <= e["pa"] and e["pa"] + e["length"] <= xr[1]):
                findings.append(("A2", e["where"], "host block pa 0x%x + 0x%x lies outside %s [0x%x, 0x%x)" % (e["pa"], e["length"], xr[2], xr[0], xr[1])))
            continue
        if e["kind"] == "done":
            if e["stores"] != nlogged:
                unchecked.append("A1/A2/R4 for %d store(s) the run made but printed no line for (`G2 done` says stores=%d, %d logged)" % (abs(e["stores"] - nlogged), e["stores"], nlogged))
            continue
        if e["kind"] == "read":
            if e["type"] != 0:
                reads[e["item"]] = e["type"]
            continue
        nlogged += 1
        w, sp, a = e["where"], e["space"], e["addr"]
        al = 2 if e["w16"] else 4
        if a % al:
            findings.append(("A1", w, "%s store address 0x%x is not %d-byte aligned" % (e["name"], a, al)))
        if sp == "host":
            if armed is None:
                if "A2:host" not in seen_gap:
                    unchecked.append("A2 host: no `G2 armed` line before the first host store, block length unknown")
                    seen_gap.add("A2:host")
            elif a + 4 > armed["length"]:
                findings.append(("A2", w, "%s host offset 0x%x outside the armed block length 0x%x" % (e["name"], a, armed["length"])))
        elif sp in ranges:
            lo, hi, srcs = ranges[sp]
            if not (lo <= a < hi):
                findings.append(("A2", w, "%s address 0x%x outside %s [0x%x, 0x%x)" % (e["name"], a, srcs, lo, hi)))
        # R2
        if "d2h_r slot" in e["name"]:
            item = e["val"] - 1
            if reads.get(item, 0) == 0:
                findings.append(("R2", w, "%s := %d without an earlier read of ring-2 item %d with a non-zero type" % (e["name"], e["val"], item)))
        # R3 / R4 state
        if sp == "pcie_core" and a == DB0_OFF:
            cls = e["val"] >> 24
            if e["name"] == "d4c ack db0":
                ack_seen, ds = True, True
            elif e["name"] == "d4c wake db0":
                ds = False
            if cls == DD_CLASS and not dd_seen:
                dd_seen = True
                if not ack_seen:
                    findings.append(("R3", w, "first D2H-RD doorbell 0x%x before the DS ack doorbell (`d4c ack db0`)" % e["val"]))
            continue
        if ds:
            safe = sp == "host" or (sp == "pcie_core" and a in DS_SAFE_OFF)
            if not safe:
                findings.append(("R4", w, "%s (%s 0x%x) stored between the DS ack doorbell and the wake doorbell: not proven safe in DEV_SLEEP (W-D4c-ds-ack.md s.6.4)" % (e["name"], e["raw_space"], a)))
    for sp in sorted({e["space"] for e in ev if e["kind"] == "store"} - {"host"} - set(ranges)):
        unchecked.append("A2 %s: soc.json range missing (see LINT GAP)" % sp)
    return findings, unchecked


# ---- golden CSV ----
COLS = "seq,ts,src,mark,caller,op,width,space,win,addr,val"
SPACES = ("bar0", "bar2", "cfg", "mmio", "va")
OPS = ("W", "R", "CW", "CR")


def parse_golden(lines, src):
    rows, hdr = [], False
    for n, line in enumerate(lines, 1):
        where = "%s:%d" % (src, n)
        line = line.rstrip("\n")
        if line.startswith("#") or not line.strip():
            continue
        if not hdr:
            if line != COLS:
                raise ParseError(where, "expected the header %r, got %r" % (COLS, line[:120]))
            hdr = True
            continue
        f = line.split(",")
        if len(f) != 11:
            raise ParseError(where, "expected 11 columns, got %d" % len(f))
        try:
            seq, width = int(f[0]), int(f[6])
            float(f[1])
            addr = int(f[9], 16)
            val = int(f[10], 16) if f[10] != "" else None
        except ValueError:
            raise ParseError(where, "unparsable numeric field in %r" % line[:160])
        if f[5] not in OPS:
            raise ParseError(where, "unknown op %r" % f[5])
        if f[7] not in SPACES:
            raise ParseError(where, "unknown space %r" % f[7])
        if width not in (8, 16, 32, 64):
            raise ParseError(where, "unknown width %d" % width)
        if f[5] in ("W", "CW") and val is None:
            raise ParseError(where, "write row without a value")
        rows.append(dict(where=where, seq=seq, ts=f[1], mark=f[3], win=f[8], caller=f[4], op=f[5], width=width, space=f[7], addr=addr, val=val))
    if not hdr:
        raise ParseError(src, "no header line (empty or wrong file)")
    if not rows:
        raise ParseError(src, "header but zero rows")
    return rows, 0


def check_golden(rows, ranges):
    findings, unchecked = [], []
    for r in rows:
        if r["op"] not in ("W", "CW"):
            continue
        nat = r["width"] // 8
        if r["addr"] % nat:
            findings.append(("A1", r["where"], "%s %d-bit store address 0x%x is not %d-byte aligned" % (r["op"], r["width"], r["addr"], nat)))
        if r["space"] == "bar2" and "bar2" in ranges:
            lo, hi, srcs = ranges["bar2"]
            if not (lo <= r["addr"] < hi):
                findings.append(("A2", r["where"], "bar2 offset 0x%x outside %s [0x%x, 0x%x)" % (r["addr"], srcs, lo, hi)))
    sp = {}
    for r in rows:
        if r["op"] in ("W", "CW"):
            sp[r["space"]] = sp.get(r["space"], 0) + 1
    for s, c in sorted(sp.items()):
        if s != "bar2" or "bar2" not in ranges:
            unchecked.append("A2 golden space %s: %d store rows, no soc.json range for it (va has no PA; cfg/bar0/mmio sizes missing)" % (s, c))
    unchecked.append("R2 golden: item payloads are host memory, not in the MMIO trace (W-D4c-ds-ack.md s.6 header)")
    # ordering: first ack -> wake pair after HOSTREADY
    h = next((i for i, r in enumerate(rows) if r["op"] == "W" and r["val"] == HOSTREADY_VAL and DONGLE_CALLER.match(r["caller"]) and (r["addr"] - HOSTREADY_OFF) % 0x1000 == 0), None)
    if h is None:
        unchecked.append("R3/R4 golden: no HOSTREADY write (0x12345678 at core+0x144) in the input")
        return findings, unchecked
    base, spc = rows[h]["addr"] - HOSTREADY_OFF, rows[h]["space"]
    db0 = [i for i in range(h + 1, len(rows)) if rows[i]["op"] == "W" and rows[i]["space"] == spc and rows[i]["addr"] == base + DB0_OFF]
    ff = [i for i in db0 if rows[i]["val"] >> 24 == FF_CLASS]
    dd = [i for i in db0 if rows[i]["val"] >> 24 == DD_CLASS]
    if not ff:
        unchecked.append("R3/R4 golden: no 0xFF doorbell after HOSTREADY")
        return findings, unchecked
    if dd and dd[0] < ff[0]:
        findings.append(("R3", rows[dd[0]]["where"], "first D2H-RD doorbell 0x%x before the first 0xFF doorbell (DS ack) 0x%x" % (rows[dd[0]]["val"], rows[ff[0]]["val"])))
    if len(ff) < 2:
        unchecked.append("R4 golden: no wake doorbell after the ack, DEV_SLEEP window not closed")
    else:
        for i in range(ff[0] + 1, ff[1]):
            r = rows[i]
            if r["op"] not in ("W", "CW"):
                continue
            if r["op"] == "W" and not DONGLE_CALLER.match(r["caller"]):
                continue
            if r["op"] == "W" and r["space"] == spc and (r["addr"] - base) in DS_SAFE_OFF:
                continue
            findings.append(("R4", r["where"], "%s %s 0x%x by %r between the ack and the wake doorbell: not proven safe in DEV_SLEEP (s.6.4)" % (r["op"], r["space"], r["addr"], r["caller"])))
    unchecked.append("R4 golden: DS cycles after the first ack->wake pair are not checked (state is not in an MMIO trace, s.6.3)")
    return findings, unchecked


def lint_text(text_lines, fmt, src, soc, board):
    ranges, gaps = allowlist_from_soc(soc, board)
    if fmt == "run-log":
        ev, ign = parse_runlog(text_lines, src)
        findings, unchecked = check_runlog(ev, ranges)
        n = sum(1 for e in ev if e["kind"] == "store")
    else:
        rows, ign = parse_golden(text_lines, src)
        findings, unchecked = check_golden(rows, ranges)
        n = sum(1 for r in rows if r["op"] in ("W", "CW"))
    return dict(findings=findings, gaps=gaps, unchecked=unchecked, stores=n, ignored=ign)


def main(argv=None):
    p = argparse.ArgumentParser(prog="lint_g2_stores", description=__doc__.split("\n")[0])
    p.add_argument("--format", required=True, choices=("run-log", "golden-csv"))
    p.add_argument("file")
    p.add_argument("--soc", default=DEF_SOC)
    p.add_argument("--board", default=DEF_BOARD)
    a = p.parse_args(argv)
    try:
        with open(a.soc) as f:
            soc = json.load(f)
        with open(a.board) as f:
            board = json.load(f)
        with open(a.file, errors="replace") as f:
            lines = f.readlines()
    except (OSError, ValueError) as e:
        print("RESULT: ERROR %s" % e)
        return 2
    try:
        r = lint_text(lines, a.format, a.file, soc, board)
    except ParseError as e:
        print("LINT PARSE: %s" % e)
        print("RESULT: FAIL parse error")
        return 1
    for rule, where, msg in r["findings"]:
        print("LINT FINDING %s %s: %s" % (rule, where, msg))
    for g in r["gaps"]:
        print("LINT GAP: %s" % g)
    for u in sorted(set(r["unchecked"])):
        print("NOT CHECKED: %s" % u)
    tail = "stores=%d findings=%d gaps=%d not_checked=%d ignored=%d" % (r["stores"], len(r["findings"]), len(r["gaps"]), len(set(r["unchecked"])), r["ignored"])
    if r["findings"]:
        print("RESULT: FAIL " + tail)
        return 1
    if r["gaps"] or r["unchecked"]:
        print("RESULT: PARTIAL " + tail)
        return 1
    print("RESULT: PASS " + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
