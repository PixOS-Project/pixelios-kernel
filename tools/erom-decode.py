#!/usr/bin/env python3
"""Decode the BCM4389 EROM dump of `--wlan-link=32` (docs/research/37 s.9.1 run B) into a core list.

Usage: tools/erom-decode.py LOG_OR_DIR [--table soc/google/gs101/wlan_seq.json] [--json]
       tools/erom-decode.py --dwords FILE            (FILE: one hex dword per line / whitespace separated, EROM order)

Input: the `LINK R32 row=<id> rd=0x<8 hex>` lines of the run (a file, or a directory searched recursively, e.g. a tools/xrun log dir). The row id is mapped to the EROM dword index through the table
(rows of step 32, block MEM, read_only, logged: offset / 4); lines from other steps are ignored by that mapping. Missing dwords (log lines lost) end the decode at the first gap and are reported.

Format source (read from source, NOT invented): mainline Linux brcmfmac v6.12 `brcmfmac/chip.c` (torvalds/linux adc218676eef, ~/pixelios-artifacts/refs/wlan-stage3/brcmfmac-chip.c):
  descriptor masks :21-71 (type = bits 3:0; COMPONENT 0x1, MASTER_PORT 0x3, ADDRESS 0x5 (bit 3 = a 64-bit upper word follows), EOT 0xF; CIA: partnum 19:8, designer 31:20, class 7:4; CIB: revision 31:24,
  slave wrappers 23:19, master wrappers 18:14, slave ports 13:9, master ports 8:4; ADDRESS: base 31:12, slave port 11:8, kind 7:6 (0 slave, 1 bridge, 2 slave wrapper, 3 master wrapper),
  size type 5:4 (0 4K, 1 8K, 2 16K, 3 = a size descriptor follows)) and the walk :812-960 (brcmf_chip_dmp_get_desc / _get_regaddr / _erom_scan). The same hardware format is walked by Broadcom DHD
  aiutils.c:ai_scan (docs/research/37 s.1.3). Core ids: bcma.h:97-159 (wlan-stage3). The base/wrapper choice is brcmfmac's (first 4K/8K slave-kind address = base; first master-wrapper kind if the core
  has a master port descriptor right after its CIB, else slave-wrapper kind = wrapper); `kept` = brcmfmac keeps the core (nmw + nsw != 0, or PMU / GCI).
Exit code 0 = decoded to EOT, 1 = truncated / gap / malformed, 2 = usage / no data."""
import argparse
import json
import os
import re
import sys

DESC_MSK, D_COMPONENT, D_MASTER, D_ADDRESS, D_GT32, D_EOT = 0xF, 0x1, 0x3, 0x5, 0x8, 0xF
CORE_NAMES = {0x800: "ChipCommon", 0x80E: "INTERNAL_MEM", 0x812: "80211 (D11)", 0x827: "PMU", 0x82A: "ARM_CM3", 0x83C: "PCIE2", 0x83E: "ARM_CR4", 0x840: "GCI", 0x847: "ARM_CA7", 0x849: "SYS_MEM"}
KIND = ("slave", "bridge", "slave-wrapper", "master-wrapper")
SIZE = {0: 0x1000, 1: 0x2000, 2: 0x4000}
R32_RE = re.compile(r"LINK R32 row=(\d+) rd=0x([0-9A-Fa-f]{8})")


class Truncated(Exception):
    pass


def decode(dw):
    """dw: EROM dwords in order. Returns (cores, notes, ended): ended = EOT reached. Raises nothing for truncation: it ends the list and says so in the notes."""
    pos = 0
    cores, notes = [], []

    def get():
        nonlocal pos
        if pos >= len(dw):
            raise Truncated()
        v = dw[pos]
        pos += 1
        t = v & DESC_MSK
        if (t & ~D_GT32) == D_ADDRESS:
            t = D_ADDRESS
        return v, t

    def regaddr():
        """brcmf_chip_dmp_get_regaddr: (base, wrap, asd list). Raises ValueError on an unexpected first descriptor / EOT."""
        nonlocal pos
        asd = []
        v, t = get()
        if t == D_MASTER:
            wtype = 3
        elif t == D_ADDRESS:
            pos -= 1
            wtype = 2
        else:
            pos -= 1
            raise ValueError("no master port / address descriptor after the CIB")
        base = wrap = 0
        while base == 0 or wrap == 0:
            while True:
                v, t = get()
                if t == D_EOT:
                    pos -= 1
                    raise ValueError("unexpected EOT inside a core")
                if t in (D_ADDRESS, D_COMPONENT):
                    break
            if t == D_COMPONENT:
                pos -= 1
                return base, wrap, asd
            up = None
            if v & D_GT32:
                up, _ = get()
            sz = (v >> 4) & 3
            szdesc = None
            if sz == 3:
                szdesc, _ = get()
                if szdesc & D_GT32:
                    get()
            kind = (v >> 6) & 3
            asd.append({"base": v & 0xFFFFF000, "upper": up, "port": (v >> 8) & 0xF, "kind": KIND[kind], "size": SIZE.get(sz) if sz != 3 else szdesc})
            if sz not in (0, 1):
                continue
            if base == 0 and kind == 0:
                base = v & 0xFFFFF000
            if wrap == 0 and kind == wtype:
                wrap = v & 0xFFFFF000
        return base, wrap, asd

    t = 0
    ended = False
    try:
        while t != D_EOT:
            v, t = get()
            if not v & 1 or t == 0:
                continue
            if t == D_EOT:
                ended = True
                break
            if t != D_COMPONENT:
                continue
            cid, designer, cls = (v >> 8) & 0xFFF, v >> 20, (v >> 4) & 0xF
            cib, tt = get()
            if (cib & DESC_MSK) != D_COMPONENT:
                notes.append(f"dword {pos - 1}: CIA {v:#010x} not followed by a CIB ({cib:#010x}); decode stopped")
                return cores, notes, False
            c = {"id": cid, "name": CORE_NAMES.get(cid, "?"), "designer": designer, "class": cls, "rev": cib >> 24, "nsw": (cib >> 19) & 0x1F, "nmw": (cib >> 14) & 0x1F,
                 "nsp": (cib >> 9) & 0x1F, "nmp": (cib >> 4) & 0x1F, "dword": pos - 2, "base": None, "wrapper": None, "asd": [], "kept": False}
            c["kept"] = c["nmw"] + c["nsw"] != 0 or cid in (0x827, 0x840)
            if c["nmw"] + c["nsw"] == 0 and cid not in (0x827, 0x840):
                cores.append(c)		# brcmfmac skips it before reading its address descriptors: the walk continues at the next component
                continue
            try:
                base, wrap, asd = regaddr()
            except ValueError as e:
                notes.append(f"core {cid:#x} at dword {c['dword']}: {e} (brcmfmac skips it)")
                c["kept"] = False
                cores.append(c)
                continue
            c["base"], c["wrapper"], c["asd"] = base, wrap, asd
            cores.append(c)
        ended = True
    except Truncated:
        notes.append(f"the dump ends after {len(dw)} dwords without an EOT (descriptor 0xF): a follow-up run must move cfg 0x80 := eromptr + 0x1000 (research 37 s.9.1 step 8)")
    return cores, notes, ended


def dwords_from_log(text, table_rows):
    """table_rows: the rows list of wlan_seq.json. Returns (dwords, missing) with dwords ascending from index 0 until the first gap."""
    idx = {r["id"]: r["off"] // 4 for r in table_rows if r["step"] == 32 and r["blk"] == "MEM" and r["kind"] == "read_only" and r.get("log")}
    got = {}
    for m in R32_RE.finditer(text):
        row = int(m.group(1))
        if row in idx:
            got[idx[row]] = int(m.group(2), 16)
    n = 0
    while n in got:
        n += 1
    return [got[i] for i in range(n)], sorted(set(idx.values()) - set(got))


def read_text(path):
    if os.path.isdir(path):
        out = []
        for d, _, fs in os.walk(path):
            for f in sorted(fs):
                p = os.path.join(d, f)
                if os.path.getsize(p) <= 20_000_000:
                    with open(p, "r", errors="replace") as fh:
                        out.append(fh.read())
        return "\n".join(out)
    with open(path, "r", errors="replace") as fh:
        return fh.read()


def render(cores, notes, ended, ndw):
    lines = [f"EROM: {ndw} dwords read, {'EOT reached' if ended else 'NO EOT'}, {len(cores)} components ({sum(c['kept'] for c in cores)} kept by brcmfmac's rule)"]
    lines.append(f"{'id':>6} {'name':<14} {'rev':>3} {'nsw':>3} {'nmw':>3} {'nsp':>3} {'nmp':>3} {'base':>10} {'wrapper':>10} kept")
    for c in cores:
        f = lambda x: "-" if x is None else f"{x:#010x}"
        lines.append(f"{c['id']:#6x} {c['name']:<14} {c['rev']:>3} {c['nsw']:>3} {c['nmw']:>3} {c['nsp']:>3} {c['nmp']:>3} {f(c['base']):>10} {f(c['wrapper']):>10} {'yes' if c['kept'] else 'no'}")
    lines += [f"note: {n}" for n in notes]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", nargs="?", help="log file or directory with `LINK R32 row=.. rd=0x..` lines")
    ap.add_argument("--table", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "soc/google/gs101/wlan_seq.json"))
    ap.add_argument("--dwords", help="a file of hex dwords instead of a log")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    notes = []
    if a.dwords:
        with open(a.dwords) as fh:
            dw = [int(x, 16) for x in fh.read().split()]
    elif a.log:
        text = read_text(a.log)
        with open(a.table) as fh:
            rows = json.load(fh)["rows"]
        dw, missing = dwords_from_log(text, rows)
        if missing and dw:
            notes.append(f"{len(missing)} dwords missing from the log (first missing index {missing[0]}): decoded the {len(dw)} dwords before the gap")
    else:
        ap.print_usage(sys.stderr)
        print("RESULT: ERROR no input", file=sys.stderr)
        return 2
    if not dw:
        print("RESULT: ERROR no EROM dwords found in the input", file=sys.stderr)
        return 2
    cores, n2, ended = decode(dw)
    notes += n2
    print(json.dumps({"dwords": len(dw), "eot": ended, "cores": cores, "notes": notes}, indent=1) if a.json else render(cores, notes, ended, len(dw)))
    ok = ended and not notes
    print(f"RESULT: {'PASS' if ok else 'PARTIAL'} {len(cores)} components, EOT {'seen' if ended else 'NOT seen'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
