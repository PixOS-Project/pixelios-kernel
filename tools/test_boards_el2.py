"""B2 of S/docs/specs/SPEC-EL2-PROXY-PROBE.md (section 6): the --el2-trap stage-2 constants tools/boards.py derives from soc.json (cpu_id_aa64mmfr0) and board.json (dram_banks)
and emits into the generated header. Expectations here are written independently of boards.py (literals from spec 3.3.3-3.3.4 and bit slicing, the JSON read directly)."""
import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import boards  # noqa: E402

BLUEJAY = os.path.join(ROOT, "device", "google", "bluejay", "board.json")
GS101 = os.path.join(ROOT, "soc", "google", "gs101", "soc.json")
G, BLK, PAGE, NENT = 14, 25, 1 << 14, 2048


def vals():
    return copy.deepcopy(boards.BOARDS["bluejay"])


def derived(v=None):
    return boards._el2_s2_values(v if v is not None else vals())


def l2_l3_model(d, t1_pa, l2_pa=0x881000000, l3_pa=(0x881004000, 0x881008000)):
    """The L2 table the shim builds (spec 3.3.3) as {index: descriptor}, only the entries the test inspects, plus the two L3 tables (dicts, page index -> descriptor).
    L2 entries are blocks (Normal in a bank, else Device) except the T1 block and the T2 block, which link to their own L3."""
    banks = d["BANKS"]
    t1_i, t2_i = t1_pa >> d["BLK_SHIFT"], d["T2_L2_IDX"]
    l2 = {}
    for i in range(d["TBL_ENTRIES"]):
        base = i << d["BLK_SHIFT"]
        normal = any(b <= base < b + n for b, n in banks)
        l2[i] = (d["DESC_BLOCK_NORMAL"] if normal else d["DESC_BLOCK_DEVICE"]) | base
    l3 = {}
    for slot, (idx, inv) in enumerate(((t1_i, (t1_pa >> G) & (NENT - 1)), (t2_i, d["T2_L3_IDX"]))):
        l2[idx] = d["DESC_TABLE"] | l3_pa[slot]
        normal = any(b <= (idx << d["BLK_SHIFT"]) < b + n for b, n in banks)
        pg = d["DESC_PAGE_NORMAL"] if normal else d["DESC_PAGE_DEVICE"]
        l3[idx] = {p: (d["DESC_INVALID"] if p == inv else pg | ((idx << d["BLK_SHIFT"]) + (p << G))) for p in range(NENT)}
    return l2, l3


class El2Stage2(unittest.TestCase):
    def test_vtcr_value_and_fields(self):
        d = derived()
        self.assertEqual(d["VTCR"], 0x8002B05C)			# spec 3.3.4 "Derived value"
        f = d["VTCR"]
        self.assertEqual(f & 0x3F, 28)				# T0SZ [5:0]
        self.assertEqual((f >> 6) & 3, 1)			# SL0 [7:6]
        self.assertEqual((f >> 8) & 3, 0)			# IRGN0 [9:8]
        self.assertEqual((f >> 10) & 3, 0)			# ORGN0 [11:10]
        self.assertEqual((f >> 12) & 3, 3)			# SH0 [13:12]
        self.assertEqual((f >> 14) & 3, 2)			# TG0 [15:14] = 16K
        self.assertEqual((f >> 16) & 7, 2)			# PS [18:16] = 40-bit
        self.assertEqual((f >> 19) & 1, 0)			# VS [19]
        self.assertEqual(f >> 20, 0x800)			# nothing but RES1 bit 31 above bit 19
        self.assertEqual((d["T0SZ"], d["IPA_BITS"]), (28, 36))

    def test_descriptors(self):
        d = derived()
        # valid | MemAttr 0xF << 2 | S2AP_R 1<<6 | S2AP_W 1<<7 | SH 3 << 8 | AF 1<<10 ; XN (bit 54) clear
        self.assertEqual(d["DESC_BLOCK_NORMAL"], 0x1 | 0x3C | 0x40 | 0x80 | 0x300 | 0x400)
        # Device-nGnRE: MemAttr 1 << 2, no SH (spec silent), XN bit 54 set
        self.assertEqual(d["DESC_BLOCK_DEVICE"], 0x1 | 0x4 | 0x40 | 0x80 | 0x400 | (1 << 54))
        self.assertEqual(d["DESC_PAGE_NORMAL"], d["DESC_BLOCK_NORMAL"] | 2)		# level 3: type bit 1 set
        self.assertEqual(d["DESC_PAGE_DEVICE"], d["DESC_BLOCK_DEVICE"] | 2)
        self.assertEqual(d["DESC_TABLE"], 3)
        # attributes never touch the 16K output address field [47:14], so they OR cleanly with a 16K-aligned PA
        for k in ("DESC_BLOCK_NORMAL", "DESC_BLOCK_DEVICE", "DESC_PAGE_NORMAL", "DESC_PAGE_DEVICE", "DESC_TABLE"):
            self.assertEqual(d[k] & 0x0000FFFFFFFFC000, 0, k)

    def test_ipa_space_covers_banks_and_soc_bases(self):
        d = derived()
        space = 1 << (64 - d["T0SZ"])
        with open(BLUEJAY) as f:
            dev = json.load(f)
        with open(GS101) as f:
            soc = json.load(f)
        tops = [int(b, 0) + int(n, 0) for b, n in dev["dram_banks"]]
        self.assertEqual(max(tops), 0x980000000)
        for t in tops:
            self.assertLess(t - 1, space)
        n = 0
        for key, val in list(soc.items()) + list(dev.items()):
            if key.endswith("_base") and isinstance(val, str):
                n += 1
                self.assertLess(int(val, 0), space, key)
        self.assertGreaterEqual(n, 6)
        self.assertEqual(d["BANKS"], ((0x80000000, 0x80000000), (0x880000000, 0x100000000)))

    def test_t2_indices(self):
        d = derived()
        self.assertEqual(d["T2_IPA"], 0x10400000)
        self.assertEqual(d["T2_L2_IDX"], 8)			# 0x10000000-0x11FFFFFF
        self.assertEqual(d["T2_L3_IDX"], 256)			# (0x10400000 - 0x10000000) >> 14
        self.assertEqual((0x10400000 - 0x10000000) >> 14, 256)

    def test_l2_links_and_t1_index_math(self):
        d = derived()
        # sample load PAs: low bank, high bank start, an unaligned one, near the top of the high bank
        for load in (0x80080000, 0x880000000, 0x881234567, 0x97FFE0000):
            pool = load + 0x1000
            t1 = (pool + PAGE - 1) & ~(PAGE - 1)			# runtime round-up to 16K
            self.assertEqual(t1 % PAGE, 0)
            self.assertTrue(any(b <= t1 and t1 + PAGE <= b + n for b, n in d["BANKS"]))	# T1 lies inside a Normal bank (P-B5)
            self.assertEqual(t1 >> d["BLK_SHIFT"], t1 // (32 << 20))
            l2, l3 = l2_l3_model(d, t1)
            i, p = t1 >> 25, (t1 >> 14) & 2047
            # both L2 -> L3 links: table descriptors, own L3 each (different tables), in different 32 MiB blocks
            self.assertEqual(l2[i] & 3, 3)
            self.assertEqual(l2[8] & 3, 3)
            self.assertNotEqual(l2[i], l2[8])
            self.assertNotEqual(i, 8)
            self.assertEqual(l2[i] & 0x0000FFFFFFFFC000, 0x881004000)
            self.assertEqual(l2[8] & 0x0000FFFFFFFFC000, 0x881008000)
            # exactly one invalid entry per L3: T1's own page / T2's page; everything else identity with the right type
            self.assertEqual([k for k, x in l3[i].items() if x == 0], [p])
            self.assertEqual([k for k, x in l3[8].items() if x == 0], [256])
            q = (p + 1) % NENT
            self.assertEqual(l3[i][q], d["DESC_PAGE_NORMAL"] | ((i << 25) + (q << 14)))		# identity, and T1's block is Normal
            self.assertEqual(l3[8][255], d["DESC_PAGE_DEVICE"] | 0x10000000 + (255 << 14))		# GICD block neighbours are Device, identity
            self.assertEqual(l3[8][257], d["DESC_PAGE_DEVICE"] | 0x10404000)
            # every other L2 entry is a block of the right class
            for k in (0, 7, 9, 0x10, 0x400):
                self.assertEqual(l2[k] & 3, 1, k)
            if i != 0x80000000 >> 25:
                self.assertEqual(l2[0x80000000 >> 25], d["DESC_BLOCK_NORMAL"] | 0x80000000)
        # classification of a few blocks
        l2, _ = l2_l3_model(d, 0x881234000)
        self.assertEqual(l2[0x80000000 >> 25] & (0xF << 2), 0xF << 2)		# low bank: Normal
        self.assertEqual(l2[(0x880000000 >> 25) + 1] & (0xF << 2), 0xF << 2)
        self.assertEqual(l2[0xFAC00000 >> 25] & (0xF << 2), 0xF << 2)		# framebuffer + ABL USB pool sit in the low bank: Normal
        self.assertEqual(l2[0x14700000 >> 25] & (0xF << 2), 1 << 2)		# UFS: a Device block
        self.assertEqual(l2[0x14700000 >> 25] >> 54 & 1, 1)			# XN

    def test_t1_page_shares_its_16k_with_nothing(self):
        # shim.ld aligns .bss only to 16 (spec 3.3.3 "the linker only aligns .bss to 16 bytes"): the pool address is any multiple of 16
        with open(os.path.join(ROOT, "shim", "shim.ld")) as f:
            ld = f.read()
        self.assertIn("ALIGN(16);\n\t_bss_start = .;", ld)
        slack = PAGE - 16
        pool_size = PAGE + slack				# a dedicated array: nothing else is inside it
        for a in range(0, PAGE, 16):				# every 16-byte alignment of the pool start
            t1 = (a + PAGE - 1) & ~(PAGE - 1)
            self.assertEqual(t1 % PAGE, 0)
            self.assertGreaterEqual(t1, a)
            self.assertLessEqual(t1 + PAGE, a + pool_size)	# the 16K page lies wholly inside the dedicated pool
        # tight: one byte less slack and some alignment overflows the pool (the test can fail)
        bad = [a for a in range(0, PAGE, 16) if ((a + PAGE - 1) & ~(PAGE - 1)) + PAGE > a + PAGE + slack - 16]
        self.assertTrue(bad)

    def test_header(self):
        h = boards.c_header("bluejay")
        for line in ("#define EL2_VTCR 0x8002B05CUL", "#define EL2_T0SZ 28", "#define EL2_DRAM_NBANKS 2",
                     "#define EL2_DRAM_BANKS { 0x80000000UL, 0x80000000UL }, { 0x880000000UL, 0x100000000UL }", "#define EL2_T2_L3_IDX 256",
                     "#define EL2_DESC_BLOCK_NORMAL 0x7FDUL", "#define EL2_DESC_TABLE 0x3UL", "#define EL2_T2_IPA 0x10400000UL"):
            self.assertIn(line, h.splitlines())
        self.assertNotIn("EL2_", boards.c_header("sweet"))	# sweet has no cpu_id_aa64mmfr0: nothing emitted

    def test_refusals(self):
        def mod(fn):
            v = vals()
            fn(v)
            with self.assertRaises(ValueError):
                boards._el2_s2_values(v)
        mod(lambda v: v.update(cpu_id_aa64mmfr0=0x101123))			# PARange 3 (not cited)
        mod(lambda v: v.update(cpu_id_aa64mmfr0=0x001122))			# TGran16 = 0
        mod(lambda v: v.update(cpu_id_aa64mmfr0=0x101122 | (1 << 32)))		# TGran16_2 = 1
        mod(lambda v: v.update(dram_banks=((0x80001000, 0x80000000), (0x880000000, 0x100000000))))	# not 32 MiB aligned
        mod(lambda v: v.update(dram_banks=((0x80000000, 0x80001000), (0x880000000, 0x100000000))))	# size not 32 MiB multiple
        mod(lambda v: v.update(dram_banks=((0x80000000, 0x80000000), (0xC0000000, 0x100000000))))	# overlap
        mod(lambda v: v.update(dram_banks=((0x2000000000, 0x2000000),)))		# top above 2^36
        mod(lambda v: v.update(dram_banks=()))
        mod(lambda v: v.update(fb_base=0x2000000000))				# a soc/board base above 2^36
        mod(lambda v: v.update(xnu_ram=(0x1000000000, 0x2000000)))		# xnu_ram top above 2^36


if __name__ == "__main__":
    unittest.main()
