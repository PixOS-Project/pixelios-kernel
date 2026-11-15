#!/usr/bin/env python3
"""Host test of sweet_dpu_gate() from xnu/patches/0165-sweet-dpu-dump.patch (review 0165 finding 8): the safety property
"DPU_STATUS or DISP_STATUS bit 0 clear => no block may be read" and "one block bit per run". The function text is cut out of the patch
(between SWEET_DPU_GATE_BEGIN / END), compiled with the host cc and run against a table: no phone, no MMIO (AP-55)."""
import os, re, subprocess, tempfile, unittest

PATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "xnu", "patches", "0165-sweet-dpu-dump.patch")
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
%s
int main(void) {
	static const uint32_t st[] = { 0, 1, 2, 3, 0x80000001u };
	static const uint32_t ds[] = { 0, 1, 2, 3, 0x80000001u };
	static const uint32_t mk[] = { 0, 1, 2, 4, 3, 5, 6, 7, 8, 9, 16, 32, 0x39, 0xFFFFFFFFu };
	for (unsigned a = 0; a < 5; a++) for (unsigned b = 0; b < 5; b++) for (unsigned c = 0; c < 14; c++)
		printf("%%u %%u %%u %%u\n", st[a], ds[b], mk[c], sweet_dpu_gate(st[a], ds[b], mk[c]));
	return 0;
}
'''

class T(unittest.TestCase):
    def run_gate(self):
        with open(PATCH) as f:
            src = f.read()
        m = re.search(r"SWEET_DPU_GATE_BEGIN[^\n]*\n(.*?)\+/\* SWEET_DPU_GATE_END \*/", src, re.S)
        self.assertTrue(m, "gate function markers not found in the 0165 patch")
        fn = "\n".join(l[1:] if l.startswith("+") else l for l in m.group(1).split("\n"))
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "g.c"); exe = os.path.join(d, "g")
            with open(c, "w") as f:
                f.write(HARNESS % fn)
            r = subprocess.run(["cc", "-Wall", "-Werror", "-O1", "-o", exe, c], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
        return [tuple(int(x) for x in l.split()) for l in out.splitlines()]

    def test_table(self):
        rows = self.run_gate()
        self.assertEqual(len(rows), 5 * 5 * 14)
        for s, d, mask, got in rows:
            if not (s & 1) or not (d & 1):
                self.assertEqual(got, 0, (s, d, mask))          # a power domain off: nothing is read, whatever the mask
            else:
                want = mask & 7
                want = want & (0 - want) if want & (want - 1) else want   # lowest block bit only
                self.assertEqual(got, want, (s, d, mask))
            self.assertEqual(got & ~7 & 0xFFFFFFFF, 0, (s, d, mask))      # never a SysMMU/S2MPU bit
            self.assertTrue(got == 0 or got & (got - 1) == 0, (s, d, mask))   # at most one bit

    def test_dump_uses_gate_before_any_block_map(self):
        with open(PATCH) as f:
            src = f.read()
        i = src.index("sweet_dpu_gate(st, ds, mask)")
        for blk in ("0x1C300000", "0x1C330000", "0x1C0B0000"):  # register blocks
            j = [m.start() for m in re.finditer(re.escape("sweet_dpu_map(" + blk), src)]
            self.assertTrue(j and all(k > i for k in j), blk)    # no block is mapped before the gate decision
        a = src.index("sweet_dpu_gate(st, ds, mask)")
        b = src.index("mask & 1u", a)
        self.assertIn("mask = m;", src[a:b])
        # the clock-word reads are in sweet_dpu_dump AFTER the gate decision too (CMU bases are only mapped inside sweet_dpu_clk, called after `mask = m;`)
        calls = [m.start() for m in re.finditer(r'sweet_dpu_clk\("CMU_', src)]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c > src.index("mask = m;") for c in calls))
        self.assertIn('sweet_dpu_clk("CMU_DISP", 0x1C200000ull, 0x600, 0x3008, 0x7008);', src)
        self.assertIn('sweet_dpu_clk("CMU_DPU", 0x1C000000ull, 0x600, 0x3010, 0x7010);', src)
        self.assertRegex(src, r"if \(mask & 4u\) \{\n\+\t\tsweet_dpu_clk\(\"CMU_DPU\"")   # CMU_DPU only for mask 4        # the gated, single-bit mask is what the block tests use (r2 delta finding 1)
        self.assertIn("IOSleep(500)", src)
        self.assertNotIn("IOSleep(100)", src)

if __name__ == "__main__":
    unittest.main()
