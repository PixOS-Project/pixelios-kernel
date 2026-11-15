#!/usr/bin/env python3
"""Host unit test of tools/lint_g2_stores.py (the file keeps its old name test_virtual_mmio.py: the Gemini "virtual MMIO simulator" it used to hold
is gone; the lint that replaces it is the only thing tested here). Run: python3 tools/test_virtual_mmio.py </dev/null

Fixtures are REAL excerpts, not invented data:
  RUNLOG = the SWEET WLAN lines of logs/runs/20261030T231749Z-wlan-runG2-d4c16/merged.log (observed on hardware; the 97 printed G2 store lines, the armed
           line, the read-evidence lines, the done line);
  GOLDEN = rows 274882..274906 of the trace-diff golden-regs --va-only CSV of golden 20261028T034021Z trace-wifi4.txt (HOSTREADY, the DS ack ff000001,
           dd020001, the wake ff000002).
Each rule has a clean baseline and a failing-first mutant. A mutant is KILLED only when the lint reports the expected rule as a FINDING and the baseline did
not; an exception or a parse refusal is counted as ERROR, never as a kill (AP-169, AP-214). A null mutant (no change) must SURVIVE, so a harness that
kills everything is caught (AP-321). No percentages: raw counts.
"""
import contextlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lint_g2_stores as L  # noqa: E402

ROOT = os.path.dirname(HERE)
SOC = json.load(open(L.DEF_SOC))
BOARD = json.load(open(L.DEF_BOARD))

RUNLOG = r"""[  959.022] SWEET WLAN: G2 armed: block pa 0x884d9c000 + 0x2b000, alias 0x84d9c000 inside [0x822a0000, 0x86000000)
[  963.592] SWEET WLAN: G2 store flags2 bp 0x3cf434 old 0x40943041 := 0xc0943041 read back 0xc0943041
[  963.592] SWEET WLAN: G2 store host_cap bp 0x3cf438 old 0 := 0x10823407 read back 0x10823407
[  963.593] SWEET WLAN: G2 store host_trap_addr bp 0x3cf43c old 0 := 0x84dc4000 read back 0x84dc4000
[  963.593] SWEET WLAN: G2 store host_trap_addr bp 0x3cf440 old 0 := 0x8 read back 0x8
[  963.593] SWEET WLAN: G2 store d2h_w_idx_hostaddr bp 0x3b1bb8 old 0 := 0x84dc3c80 read back 0x84dc3c80
[  963.594] SWEET WLAN: G2 store d2h_w_idx_hostaddr bp 0x3b1bbc old 0 := 0x8 read back 0x8
[  963.594] SWEET WLAN: G2 store h2d_r_idx_hostaddr bp 0x3b1bb0 old 0 := 0x84dc3b80 read back 0x84dc3b80
[  963.594] SWEET WLAN: G2 store h2d_r_idx_hostaddr bp 0x3b1bb4 old 0 := 0x8 read back 0x8
[  963.594] SWEET WLAN: G2 store h2d_w_idx_hostaddr bp 0x3b1ba8 old 0 := 0x84dc3a80 read back 0x84dc3a80
[  963.595] SWEET WLAN: G2 store h2d_w_idx_hostaddr bp 0x3b1bac old 0 := 0x8 read back 0x8
[  963.595] SWEET WLAN: G2 store d2h_r_idx_hostaddr bp 0x3b1bc0 old 0 := 0x84dc3d00 read back 0x84dc3d00
[  963.595] SWEET WLAN: G2 store d2h_r_idx_hostaddr bp 0x3b1bc4 old 0 := 0x8 read back 0x8
[  963.595] SWEET WLAN: G2 store ring0 base bp 0x3b1be8 old 0 := 0x84d9c000 read back 0x84d9c000
[  963.596] SWEET WLAN: G2 store ring0 base bp 0x3b1bec old 0 := 0x8 read back 0x8
[  963.596] SWEET WLAN: G2 store ring0 max_item|len_items bp 0x3b1be4 old 0 := 0x280080 read back 0x280080
[  963.596] SWEET WLAN: G2 store ring0 wr slot bp 0x4634c0 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.597] SWEET WLAN: G2 store ring0 rd slot bp 0x45a280 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.597] SWEET WLAN: G2 store ring1 base bp 0x3b1bf8 old 0 := 0x84d9d400 read back 0x84d9d400
[  963.597] SWEET WLAN: G2 store ring1 base bp 0x3b1bfc old 0 := 0x8 read back 0x8
[  963.597] SWEET WLAN: G2 store ring1 max_item|len_items bp 0x3b1bf4 old 0 := 0x200800 read back 0x200800
[  963.598] SWEET WLAN: G2 store ring1 wr slot bp 0x4634c4 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.598] SWEET WLAN: G2 store ring1 rd slot bp 0x45a284 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.598] SWEET WLAN: G2 store ring2 base bp 0x3b1c08 old 0 := 0x84dad400 read back 0x84dad400
[  963.599] SWEET WLAN: G2 store ring2 base bp 0x3b1c0c old 0 := 0x8 read back 0x8
[  963.599] SWEET WLAN: G2 store ring2 max_item|len_items bp 0x3b1c04 old 0 := 0x180040 read back 0x180040
[  963.599] SWEET WLAN: G2 store ring2 wr slot bp 0x47d780 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.600] SWEET WLAN: G2 store ring2 rd slot bp 0x47d700 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.600] SWEET WLAN: G2 store ring3 base bp 0x3b1c18 old 0 := 0x84dada00 read back 0x84dada00
[  963.601] SWEET WLAN: G2 store ring3 base bp 0x3b1c1c old 0 := 0x8 read back 0x8
[  963.601] SWEET WLAN: G2 store ring3 max_item|len_items bp 0x3b1c14 old 0 := 0x180800 read back 0x180800
[  963.601] SWEET WLAN: G2 store ring3 wr slot bp 0x47d784 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.602] SWEET WLAN: G2 store ring3 rd slot bp 0x47d704 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.602] SWEET WLAN: G2 store ring4 base bp 0x3b1c28 old 0 := 0x84db9a00 read back 0x84db9a00
[  963.602] SWEET WLAN: G2 store ring4 base bp 0x3b1c2c old 0 := 0x8 read back 0x8
[  963.603] SWEET WLAN: G2 store ring4 max_item|len_items bp 0x3b1c24 old 0 := 0x280400 read back 0x280400
[  963.603] SWEET WLAN: G2 store ring4 wr slot bp 0x47d788 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.603] SWEET WLAN: G2 store ring4 rd slot bp 0x47d708 old 0 := 0 read back 0 (16-bit store, upper half unchanged)
[  963.604] SWEET WLAN: G2 store scratch addr bp 0x3cf41c old 0 := 0x84dc3a00 read back 0x84dc3a00
[  963.604] SWEET WLAN: G2 store scratch addr bp 0x3cf420 old 0 := 0x8 read back 0x8
[  963.604] SWEET WLAN: G2 store scratch len bp 0x3cf418 old 0 := 0x8 read back 0x8
[  963.605] SWEET WLAN: G2 store rc command off 0x4 old 0x100000 := 0x507 read back 0x100507
[  963.605] SWEET WLAN: G2 store ep command off 0x4 old 0x100002 := 0x6 read back 0x100006
[  963.606] SWEET WLAN: G2 store hostready off 0x144 old 0 := 0x12345678 read back 0x12345678
[  963.709] SWEET WLAN: D4b ro h2d_r0 0x0 d2h_w0 host 0x1 tcm 0x1 ring2 w 0x1800024 0x0 0x0 0x2 0x0 0x1800026 type 0x24 epoch 0x1 xor ok c30 0x1
[  963.710] SWEET WLAN: G2 store d4b mask off 0xc34 old 0 := 0xffff read back 0xffff
[  963.710] SWEET WLAN: G2 store d4b item w0 host 0 old 0 := 0x180000d read back 0x180000d
[  963.710] SWEET WLAN: G2 store d4b item w1 host 0x4 old 0 := 0x1 read back 0x1
[  963.711] SWEET WLAN: G2 store d4b item w2 host 0x8 old 0 := 0x2000 read back 0x2000
[  963.711] SWEET WLAN: G2 store d4b item w3 host 0xc old 0 := 0 read back 0
[  963.711] SWEET WLAN: G2 store d4b item w4 host 0x10 old 0 := 0x84dc5000 read back 0x84dc5000
[  963.711] SWEET WLAN: G2 store d4b item w5 host 0x14 old 0 := 0x8 read back 0x8
[  963.711] SWEET WLAN: G2 store d4b item w6 host 0x18 old 0 := 0 read back 0
[  963.712] SWEET WLAN: G2 store d4b item w7 host 0x1c old 0 := 0 read back 0
[  963.712] SWEET WLAN: G2 store d4b item w8 host 0x20 old 0 := 0 read back 0
[  963.712] SWEET WLAN: G2 store d4b item w9 host 0x24 old 0 := 0 read back 0
[  963.712] SWEET WLAN: G2 store d4b h2d_w slot 0 host 0x27a80 old 0 := 0x1 read back 0x1
[  963.712] SWEET WLAN: G2 store d4b db0 off 0x140 := 0xff000001 (old and read-back not made, golden writes only)
[  965.631] SWEET WLAN: D4b poll h2d_r0 0x0 d2h_w0 0x1 ring2 item 1 type 0x0 epoch 0x0 xor n/a c30 0x1 tcm d2h_w 0x1 0x0 0x0
[  965.632] SWEET WLAN: G2 store d4c ack item w0 host 0x28 old 0 := 0x2800023 read back 0x2800023
[  965.632] SWEET WLAN: G2 store d4c ack item w1 host 0x2c old 0 := 0 read back 0
[  965.633] SWEET WLAN: G2 store d4c ack item w2 host 0x30 old 0 := 0x2 read back 0x2
[  965.633] SWEET WLAN: G2 store d4c ack item w3 host 0x34 old 0 := 0 read back 0
[  965.633] SWEET WLAN: G2 store d4c ack item w4 host 0x38 old 0 := 0 read back 0
[  965.633] SWEET WLAN: G2 store d4c ack item w5 host 0x3c old 0 := 0 read back 0
[  965.634] SWEET WLAN: G2 store d4c ack item w6 host 0x40 old 0 := 0 read back 0
[  965.634] SWEET WLAN: G2 store d4c ack item w7 host 0x44 old 0 := 0 read back 0
[  965.634] SWEET WLAN: G2 store d4c ack item w8 host 0x48 old 0 := 0 read back 0
[  965.635] SWEET WLAN: G2 store d4c ack item w9 host 0x4c old 0 := 0 read back 0
[  965.635] SWEET WLAN: G2 store d4c ack h2d_w slot 0 host 0x27a80 old 0x1 := 0x2 read back 0x2
[  965.635] SWEET WLAN: G2 store d4c ack db0 off 0x140 := 0xff000002 (old and read-back not made, golden writes only)
[  965.635] SWEET WLAN: G2 store d4c rd1 d2h_r slot 2 host 0x27d00 old 0 := 0x1 read back 0x1
[  965.636] SWEET WLAN: G2 store d4c rd1 db0 off 0x140 := 0xdd020001 (old and read-back not made, golden writes only)
[  965.636] SWEET WLAN: D4c ring-2 item 0 DS_ENTER_REQ answered (DS_ACK)
[  965.636] SWEET WLAN: G2 store d4c wake item w0 host 0x50 old 0 := 0x3800023 read back 0x3800023
[  965.636] SWEET WLAN: G2 store d4c wake item w1 host 0x54 old 0 := 0 read back 0
[  965.637] SWEET WLAN: G2 store d4c wake item w2 host 0x58 old 0 := 0x40 read back 0x40
[  965.637] SWEET WLAN: G2 store d4c wake item w3 host 0x5c old 0 := 0 read back 0
[  965.637] SWEET WLAN: G2 store d4c wake item w4 host 0x60 old 0 := 0 read back 0
[  965.637] SWEET WLAN: G2 store d4c wake item w5 host 0x64 old 0 := 0 read back 0
[  965.638] SWEET WLAN: G2 store d4c wake item w6 host 0x68 old 0 := 0 read back 0
[  965.638] SWEET WLAN: G2 store d4c wake item w7 host 0x6c old 0 := 0 read back 0
[  965.638] SWEET WLAN: G2 store d4c wake item w8 host 0x70 old 0 := 0 read back 0
[  965.638] SWEET WLAN: G2 store d4c wake item w9 host 0x74 old 0 := 0 read back 0
[  965.639] SWEET WLAN: G2 store d4c wake h2d_w slot 0 host 0x27a80 old 0x2 := 0x3 read back 0x3
[  965.639] SWEET WLAN: G2 store d4c wake db0 off 0x140 := 0xff000003 (old and read-back not made, golden writes only)
[  965.640] SWEET WLAN: D4c exit-note poll after 1 ms: h2d_r0 0x3 d2h_w0 0x2 ring2 item 1 w 0x2800024 0x0 0x0 0x4 0x0 0x2800020
[  965.641] SWEET WLAN: G2 store d4c deas item w0 host 0x78 old 0 := 0x4800023 read back 0x4800023
[  965.641] SWEET WLAN: G2 store d4c deas item w1 host 0x7c old 0 := 0 read back 0
[  965.641] SWEET WLAN: G2 store d4c deas item w2 host 0x80 old 0 := 0x20 read back 0x20
[  965.641] SWEET WLAN: G2 store d4c deas item w3 host 0x84 old 0 := 0 read back 0
[  965.642] SWEET WLAN: G2 store d4c deas item w4 host 0x88 old 0 := 0 read back 0
[  965.642] SWEET WLAN: G2 store d4c deas item w5 host 0x8c old 0 := 0 read back 0
[  965.642] SWEET WLAN: G2 store d4c deas item w6 host 0x90 old 0 := 0 read back 0
[  965.642] SWEET WLAN: G2 store d4c deas item w7 host 0x94 old 0 := 0 read back 0
[  965.643] SWEET WLAN: G2 store d4c deas item w8 host 0x98 old 0 := 0 read back 0
[  965.643] SWEET WLAN: G2 store d4c deas item w9 host 0x9c old 0 := 0 read back 0
[  965.643] SWEET WLAN: G2 store d4c deas h2d_w slot 0 host 0x27a80 old 0x3 := 0x4 read back 0x4
[  965.644] SWEET WLAN: G2 store d4c deas db0 off 0x140 := 0xff000004 (old and read-back not made, golden writes only)
[  965.644] SWEET WLAN: G2 store d4c rd2 d2h_r slot 2 host 0x27d00 old 0x1 := 0x2 read back 0x2
[  965.644] SWEET WLAN: G2 store d4c rd2 db0 off 0x140 := 0xdd020002 (old and read-back not made, golden writes only)
[  965.645] SWEET WLAN: G2 store d4b unmask off 0xc34 := 0 read back 0
[  965.646] SWEET WLAN: G2 done rc=0 stores=99 mask 0xffffffff restore=ok"""
GOLDEN = r"""seq,ts,src,mark,caller,op,width,space,win,addr,val
274882,80.055082,trace-wifi4.txt,wifi-on4,ai_corereg,W,32,va,,0xffffffc01fc32144,0x12345678
274883,80.055084,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32144,0x12345678
274884,80.055969,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,R,32,va,,0xffffffc00c44a000,0x1004000
274885,80.055971,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,W,32,va,,0xffffffc00c44a000,0x1004000
274886,80.055974,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,R,32,va,,0xffffffc00c44a004,0x0
274887,80.055975,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,W,32,va,,0xffffffc00c44a004,0x0
274888,80.055977,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,R,32,va,,0xffffffc00c44a008,0x32000000
274889,80.055978,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,W,32,va,,0xffffffc00c44a008,0x32000000
274890,80.055985,trace-wifi4.txt,wifi-on4,,CR,32,cfg,,0x90,
274891,80.055999,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c30,0x1
274892,80.056003,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c34,0xffff
274893,80.056006,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c30,0x1
274894,80.056007,trace-wifi4.txt,wifi-on4,ai_corereg,W,32,va,,0xffffffc01fc32c30,0x1
274895,80.056011,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c30,0x0
274896,80.056016,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c34,0xffff
274897,80.056016,trace-wifi4.txt,wifi-on4,ai_corereg,W,32,va,,0xffffffc01fc32c34,0x0
274898,80.056020,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c34,0x0
274899,80.056058,trace-wifi4.txt,wifi-on4,dhdpcie_bus_ringbell_fast,W,32,va,,0xffffffc01fc32140,0xff000001
274900,80.056065,trace-wifi4.txt,wifi-on4,dhdpcie_bus_ringbell_fast,W,32,va,,0xffffffc01fc32140,0xdd020001
274901,80.056084,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c34,0x0
274902,80.056085,trace-wifi4.txt,wifi-on4,ai_corereg,W,32,va,,0xffffffc01fc32c34,0xffff
274903,80.056088,trace-wifi4.txt,wifi-on4,ai_corereg,R,32,va,,0xffffffc01fc32c34,0xffff
274904,80.072388,trace-wifi4.txt,wifi-on4,dhdpcie_bus_ringbell_fast,W,32,va,,0xffffffc01fc32140,0xff000002
274905,80.072795,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,R,32,va,,0xffffffc00c44a000,0xc000
274906,80.072796,trace-wifi4.txt,wifi-on4,exynos_pcie_rc_irq_handler,W,32,va,,0xffffffc00c44a000,0xc000"""
STATS = {"mutants": 0, "killed": 0, "survived": 0, "error": 0}


def lines(text):
    return text.split("\n")


def run_log(ls):
    return L.lint_text([x + "\n" for x in ls], "run-log", "fixture", SOC, BOARD)


def golden(ls):
    return L.lint_text([x + "\n" for x in ls], "golden-csv", "fixture", SOC, BOARD)


def rules(res):
    return sorted({f[0] for f in res["findings"]})


def mutate(base_fn, base_lines, fn, want):
    """Apply fn to a copy of base_lines; classify as killed / survived / error (counted, not asserted here)."""
    STATS["mutants"] += 1
    ls = fn(list(base_lines))
    try:
        res = base_fn(ls)
    except Exception as e:  # ParseError or a crash: never a kill
        STATS["error"] += 1
        return "error", repr(e)
    if want is None:
        STATS["survived"] += 1
        return "survived", rules(res)
    if want in rules(res):
        STATS["killed"] += 1
        return "killed", rules(res)
    STATS["survived"] += 1
    return "survived", rules(res)


def sub(old, new, count=1):
    def f(ls):
        n = 0
        for i, x in enumerate(ls):
            if old in x and n < count:
                ls[i] = x.replace(old, new)
                n += 1
        assert n == count, "mutation anchor %r matched %d times, want %d" % (old, n, count)
        return ls
    return f


def after(anchor, new):
    def f(ls):
        i = next(i for i, x in enumerate(ls) if anchor in x)
        ls.insert(i + 1, new)
        return ls
    return f


def drop(anchor):
    def f(ls):
        i = [i for i, x in enumerate(ls) if anchor in x]
        assert len(i) == 1, "drop anchor %r matched %d lines" % (anchor, len(i))
        del ls[i[0]]
        return ls
    return f


def move_before(anchors, target):
    def f(ls):
        moved = [x for x in ls if any(a in x for a in anchors)]
        assert len(moved) == len(anchors)
        ls = [x for x in ls if x not in moved]
        i = next(i for i, x in enumerate(ls) if target in x)
        return ls[:i] + moved + ls[i:]
    return f


class AllowlistTests(unittest.TestCase):
    def test_bar2_range_from_real_soc(self):
        r, gaps = L.allowlist_from_soc(SOC, BOARD)
        self.assertEqual(r["bar2"][:2], (0, 0x400000))     # soc.json wlan/tcm_bytes
        self.assertEqual(r["xnu_ram"][:2], (0x880000000, 0x980000000))

    def test_missing_ranges_are_gaps_not_guesses(self):
        r, gaps = L.allowlist_from_soc(SOC, BOARD)
        for sp in ("bar0", "tcm", "pcie_core", "rc_cfg", "ep_cfg"):
            self.assertNotIn(sp, r)
            self.assertTrue(any("space %s" % sp in g for g in gaps), sp)

    def test_range_appears_when_soc_gains_the_key(self):
        soc = json.loads(json.dumps(SOC))
        soc["wlan"]["pcie_core_bytes"] = "0x1000"
        r, gaps = L.allowlist_from_soc(soc, BOARD)
        self.assertEqual(r["pcie_core"][:2], (0, 0x1000))
        self.assertFalse(any("space pcie_core" in g for g in gaps))

    def test_removed_key_becomes_a_gap(self):
        soc = json.loads(json.dumps(SOC))
        del soc["wlan"]["tcm_bytes"]
        r, gaps = L.allowlist_from_soc(soc, BOARD)
        self.assertNotIn("bar2", r)
        self.assertTrue(any("space bar2" in g for g in gaps))


class RunLogTests(unittest.TestCase):
    def base(self):
        return lines(RUNLOG)

    def test_baseline_clean_and_counted(self):
        res = run_log(self.base())
        self.assertEqual(res["findings"], [])
        self.assertEqual(res["stores"], 97)
        self.assertTrue(any("printed no line" in u for u in res["unchecked"]))   # G2 done says 99, 97 logged
        self.assertEqual(len(res["gaps"]), 5)

    def test_null_mutant_survives(self):
        k, _ = mutate(run_log, self.base(), lambda ls: ls, None)
        self.assertEqual(k, "survived")

    MUT = [
        ("A1 odd bp address", sub("bp 0x3cf438", "bp 0x3cf439"), "A1"),
        ("A1 odd 16-bit store address", sub("bp 0x4634c0 old 0 := 0 read back 0 (16-bit", "bp 0x4634c1 old 0 := 0 read back 0 (16-bit"), "A1"),
        ("A2 host offset beyond the armed block", sub("host 0x4c ", "host 0x2b000 "), "A2"),
        ("A2 armed block outside xnu_ram", sub("block pa 0x884d9c000", "block pa 0x990000000"), "A2"),
        ("R2 read evidence removed", drop("D4b ro "), "R2"),
        ("R2 read evidence has type 0", sub("type 0x24 epoch 0x1 xor ok c30 0x1", "type 0x0 epoch 0x1 xor ok c30 0x1"), "R2"),
        ("R3 RD doorbell before the DS ack", move_before(["d4c rd1 d2h_r slot 2", "d4c rd1 db0"], "d4c ack item w0"), "R3"),
        ("R4 TCM store in DEV_SLEEP", after("d4c ack db0", "[  965.636] SWEET WLAN: G2 store ring0 wr slot bp 0x4634c0 old 0 := 0 read back 0"), "R4"),
        ("R4 other PCIe-core register in DEV_SLEEP", after("d4c ack db0", "[  965.636] SWEET WLAN: G2 store hostready off 0x1e8 old 0 := 0x12345678 read back 0x12345678"), "R4"),
        ("R4 cfg store in DEV_SLEEP", after("d4c ack db0", "[  965.636] SWEET WLAN: G2 store ep command off 0x4 old 0x100006 := 0x2 read back 0x100002"), "R4"),
    ]
    CLEAN_MUT = [
        ("bp store AFTER the wake doorbell is not a DEV_SLEEP store", after("d4c wake db0", "[  965.640] SWEET WLAN: G2 store ring0 wr slot bp 0x4634c0 old 0 := 0 read back 0")),
        ("host store inside DEV_SLEEP is allowed (s.6.4)", after("d4c ack db0", "[  965.636] SWEET WLAN: G2 store d4c rd1 d2h_r slot 2 host 0x27d00 old 0 := 0x1 read back 0x1")),
    ]

    def test_each_rule_has_a_failing_first_mutant(self):
        for name, fn, want in self.MUT:
            k, got = mutate(run_log, self.base(), fn, want)
            self.assertEqual(k, "killed", "%s: %s %s" % (name, k, got))

    def test_allowed_mutations_stay_clean(self):
        for name, fn in self.CLEAN_MUT:
            res = run_log(fn(self.base()))
            self.assertEqual(res["findings"], [], name)

    def test_refusals(self):
        bad = [
            ("store without read back", sub("old 0x40943041 := 0xc0943041 read back 0xc0943041", "old 0x40943041 := 0xc0943041"), "fixture:"),
            ("bad hex digit", sub("bp 0x3cf438", "bp 0x3cfZ38"), "fixture:"),
            ("unknown space token", sub("flags2 bp 0x3cf434", "flags2 xx 0x3cf434"), "fixture:"),
            ("name not in the source tables (off)", sub("G2 store ep command off", "G2 store zzz command off"), "U-3"),
            ("armed line truncated", sub("alias 0x84d9c000 inside [0x822a0000, 0x86000000)", "alias 0x84d9c000"), "fixture:"),
            ("D4b poll line truncated", sub("xor n/a c30 0x1 tcm d2h_w 0x1 0x0 0x0", "xor n/a"), "fixture:"),
        ]
        for name, fn, tok in bad:
            ls = fn(self.base())
            with self.assertRaises(L.ParseError, msg=name) as cm:
                run_log(ls)
            self.assertIn("fixture:", str(cm.exception), name)
            if tok != "fixture:":
                self.assertIn(tok, str(cm.exception), name)

    def test_refusal_names_the_line_number(self):
        ls = self.base()
        ls[9] = ls[9].replace("read back", "readback")
        with self.assertRaises(L.ParseError) as cm:
            run_log(ls)
        self.assertIn("fixture:10:", str(cm.exception))

    def test_empty_and_wrong_file_refused(self):
        for ls in ([""], ["hello", "world"], ["[ 1.0] SWEET WLAN: something else"]):
            with self.assertRaises(L.ParseError):
                run_log(ls)


class GoldenTests(unittest.TestCase):
    def base(self):
        return lines(GOLDEN)

    def test_baseline_clean_and_window_checked(self):
        res = golden(self.base())
        self.assertEqual(res["findings"], [])
        self.assertFalse(any("no HOSTREADY" in u or "no wake" in u for u in res["unchecked"]))

    def test_null_mutant_survives(self):
        self.assertEqual(mutate(golden, self.base(), lambda ls: ls, None)[0], "survived")

    def row(self, seq, caller, op, width, space, addr, val):
        return "%d,80.056066,trace-wifi4.txt,wifi-on4,%s,%s,%d,%s,,%s,%s" % (seq, caller, op, width, space, addr, val)

    def test_mutants(self):
        a = "0xffffffc01fc32140"
        muts = [
            ("A1 misaligned 32-bit store", sub(",W,32,va,,0xffffffc01fc32c30,0x1", ",W,32,va,,0xffffffc01fc32c31,0x1"), "A1"),
            ("R3 RD doorbell before the ack doorbell", lambda ls: [x.replace("0xff000001", "0xTMP").replace("0xdd020001", "0xff000001").replace("0xTMP", "0xdd020001") for x in ls], "R3"),
            ("R4 dongle TCM write in DEV_SLEEP", after("0xdd020001", self.row(274900, "dhdpcie_bus_wtcm64", "W", 64, "va", "0xffffffc020231f68", "0x1")), "R4"),
            ("R4 other core register in DEV_SLEEP", after("0xdd020001", self.row(274900, "ai_corereg", "W", 32, "va", "0xffffffc01fc321e8", "0x200")), "R4"),
            ("R4 cfg write in DEV_SLEEP", after("0xdd020001", self.row(274900, "", "CW", 16, "cfg", "0x80", "0x40")), "R4"),
            ("A2 bar2 offset beyond tcm_bytes", after("0xdd020001", self.row(274900, "dhdpcie_bus_wtcm64", "W", 32, "bar2", "0x400000", "0x1")), "A2"),
        ]
        for name, fn, want in muts:
            k, got = mutate(golden, self.base(), fn, want)
            self.assertEqual(k, "killed", "%s: %s %s" % (name, k, got))

    def test_allowed_in_window_stays_clean(self):
        ok = [
            after("0xdd020001", self.row(274900, "exynos_pcie_rc_irq_handler", "W", 32, "va", "0xffffffc00c44a000", "0x1")),   # RC side, not a dongle access
            after("0xff000002", self.row(274905, "dhdpcie_bus_wtcm64", "W", 64, "va", "0xffffffc020231f68", "0x1")),         # after the wake doorbell
        ]
        for fn in ok:
            self.assertEqual(golden(fn(self.base()))["findings"], [])

    def test_refusals(self):
        b = self.base()
        cases = [
            ("wrong header", [b[0].replace("seq,ts", "seq,time")] + b[1:]),
            ("10 columns", b[:1] + [b[1].rsplit(",", 1)[0]] + b[2:]),
            ("unknown op", b[:1] + [b[1].replace(",W,", ",X,", 1)] + b[2:]),
            ("unknown space", b[:1] + [b[1].replace(",va,", ",io,", 1)] + b[2:]),
            ("bad hex", b[:1] + [b[1].replace("0xffffffc", "0xzzzzzzc", 1)] + b[2:]),
            ("write without value", b[:1] + ["274884,1.0,t,m,c,W,32,va,,0x10,"] + b[2:]),
            ("header only", [b[0]]),
            ("empty", [""]),
        ]
        for name, ls in cases:
            with self.assertRaises(L.ParseError, msg=name):
                golden(ls)

    def test_refusal_names_the_line_number(self):
        b = self.base()
        b[3] = b[3].replace(",W,", ",Q,", 1) if ",W," in b[3] else b[3].replace(",R,", ",Q,", 1)
        with self.assertRaises(L.ParseError) as cm:
            golden(b)
        self.assertIn("fixture:4:", str(cm.exception))


class CliTests(unittest.TestCase):
    def cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = L.main(argv)
        return rc, out.getvalue()

    def write(self, name, text):
        import tempfile
        d = tempfile.mkdtemp(prefix="lintg2-")
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_partial_exit_and_result_line(self):
        rc, out = self.cli(["--format", "run-log", self.write("a.log", RUNLOG + "\n")])
        self.assertEqual(rc, 1)
        self.assertIn("RESULT: PARTIAL stores=97 findings=0 gaps=5", out)

    def test_fail_exit_on_finding(self):
        text = RUNLOG.replace("bp 0x3cf438", "bp 0x3cf439")
        rc, out = self.cli(["--format", "run-log", self.write("a.log", text + "\n")])
        self.assertEqual(rc, 1)
        self.assertIn("LINT FINDING A1", out)
        self.assertIn("RESULT: FAIL stores=97 findings=1", out)

    def test_parse_error_is_fail_with_file_and_line(self):
        text = RUNLOG.replace("read back 0x10823407", "readback 0x10823407")
        p = self.write("a.log", text + "\n")
        rc, out = self.cli(["--format", "run-log", p])
        self.assertEqual(rc, 1)
        self.assertIn("%s:3:" % p, out)
        self.assertIn("RESULT: FAIL parse error", out)

    def test_pass_when_ranges_exist_and_nothing_unchecked(self):
        # not reachable on real data (golden R2 is unchecked by nature); the run-log path with all keys present and the done count matching
        soc = json.loads(json.dumps(SOC))
        soc["wlan"].update({"bar0_bytes": "0x8000", "ram_base": "0x0", "ram_bytes": "0x1000000", "pcie_core_bytes": "0x1000", "dbi_bytes": "0x1000", "ep_cfg_bytes": "0x1000"})
        text = RUNLOG.replace("stores=99", "stores=97")
        res = L.lint_text([x + "\n" for x in text.split("\n")], "run-log", "fixture", soc, BOARD)
        self.assertEqual((res["findings"], res["gaps"], res["unchecked"]), ([], [], []))

    def test_missing_file_is_error(self):
        rc, out = self.cli(["--format", "run-log", "/nonexistent/x.log"])
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR", out)


# The real-log lint is a by-hand step, not a unit test (no run log or golden CSV exists on a gate host, and an env-gated
# skip is not allowed there: tools/unit-skip-allow.txt, T-2f05): `python3 tools/lint_g2_stores.py --format run-log|golden-csv FILE`.


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    r = unittest.TextTestRunner(verbosity=1).run(suite)
    print("mutants=%d killed=%d survived=%d error=%d (null mutants must survive: %d)" % (STATS["mutants"], STATS["killed"], STATS["survived"], STATS["error"], 2))
    ok = r.wasSuccessful()
    print("RESULT: %s tests=%d failures=%d errors=%d skipped=%d" % ("PASS" if ok else "FAIL", r.testsRun, len(r.failures), len(r.errors), len(r.skipped)))
    sys.exit(0 if ok else 1)
