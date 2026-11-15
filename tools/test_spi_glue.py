"""xnu patch 0346 (SPI glue): host test of the SPI table logic of pexpert/arm/pe_fiq.c. The SWEET SPI block is rebuilt WITHOUT a kernel tree: the lines patch 0272 adds to pe_fiq.c are taken
from xnu/patches/0272-sweet-spi-mali-irq.patch, patch 0346 is applied to them with `patch`, and both versions (before = 0272 only, after = 0272 + 0346) are compiled with the host compiler
against a fake GIC distributor that logs every 32-bit register write.
Covers: the GPU path is unchanged (the attach of INTID 452 and 453 produces the identical register write log and memory image before and after 0346; the dispatcher's handler-returns-0 and
handler-returns-1 behaviour is identical), the table holds 8 attaches and refuses the 9th (28) and a duplicate (22), attach_off leaves the SPI disabled (no ISENABLER write), enable and unmask
write exactly the INTID's own ISENABLER bit and refuse an unattached INTID (22, no write), a handler returning SWEET_SPI_DEFER (2) is masked but counted apart (deferred, not unhandled),
a detached or never attached INTID is never enabled, 6 mutants of the 0346 text that the cases must kill. The kernel itself is NOT run: only a build + a boot shows it."""
import os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = os.path.join(os.path.dirname(HERE), "xnu", "patches")
P272 = os.path.join(PATCHES, "0272-sweet-spi-mali-irq.patch")
P346 = os.path.join(PATCHES, "0346-sweet-spi-defer-unmask.patch")

DRIVER = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define SWEET 1
#define os_atomic_load(p, o) (*(p))
#define os_atomic_store(p, v, o) (*(p) = (v))
#define GICD_CTLR 0
#define GICD_CTLR_RWP 0x80000000u
static uintptr_t gicd_base;
static uint32_t regs[0x2000];
static char wlog[8192];
static void LOGW(uint32_t off, uint32_t v) { char b[40]; snprintf(b, sizeof b, "%%x=%%x ", (unsigned)off, (unsigned)v); strcat(wlog, b); }
static uint32_t fr32(uint32_t off) { return regs[off / 4]; }
static void fw32(uint32_t off, uint32_t v) {
	LOGW(off, v);
	if (off >= 0x100 && off < 0x180) regs[off / 4] |= v;            /* ISENABLER write-1-to-set */
	else if (off >= 0x180 && off < 0x200) regs[(off - 0x80) / 4] &= ~v;   /* ICENABLER write-1-to-clear of the ISENABLER image */
	else regs[off / 4] = v;
}
#define gicd_read32(o) fr32((o))
#define gicd_write32(o, d) fw32((o), (d))
%(block)s
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %%d: %%s\n", __LINE__, #c); bad++; } } while (0)

static int ret_val;
static int h0(void *a, uint32_t i) { (void)a; (void)i; return 0; }
static int hret(void *a, uint32_t i) { (void)a; (void)i; return ret_val; }
static void clr(void) { memset(regs, 0, sizeof regs); wlog[0] = 0; gicd_base = (uintptr_t)calloc(1, 0x10000); }

int main(int argc, char **argv) {
	uint32_t rb[4]; int rc, i; (void)argc; (void)argv;
	/* GPU path: attach 452 and 453 with the old entry point, dump the write log and the memory image */
	clr();
	CHK(sweet_spi_attach(452, h0, NULL, rb) == 0); CHK(sweet_spi_attach(453, h0, NULL, rb) == 0);
	printf("LOG %%s\n", wlog);
	{ uint32_t sum = 0; for (i = 0; i < 0x10000; i++) sum = sum * 31 + ((volatile uint8_t *)gicd_base)[i]; printf("IMG %%08x\n", sum); }
	/* dispatcher: a handler returning 0 writes nothing; returning 1 is masked and counted as unhandled */
	wlog[0] = 0; sweet_spi_dispatch(452); CHK(wlog[0] == 0);
	{ sweet_spi_detach(452); wlog[0] = 0; CHK(sweet_spi_attach(452, hret, NULL, rb) == 0); wlog[0] = 0; ret_val = 1; sweet_spi_dispatch(452); printf("DISP1 %%s unh=%%llu\n", wlog, (unsigned long long)sweet_spi_unhandled); }
	printf("TAIL\n");
#ifdef WITH_0346
	/* ---- new behaviour ---- */
	{
		uint32_t en = 9; uint64_t un0;
		sweet_spi_detach(452); sweet_spi_detach(453);
		clr();
		/* 8 slots: the 9th is refused, a duplicate is refused */
		for (i = 0; i < 8; i++) CHK(sweet_spi_attach(100 + i, h0, NULL, rb) == 0);
		CHK(sweet_spi_attach(200, h0, NULL, rb) == 28);
		CHK(sweet_spi_attach(100, h0, NULL, rb) == 22);
		for (i = 0; i < 8; i++) CHK(sweet_spi_detach(100 + i) == 0);
		/* attach_off: configured, handler in, SPI left disabled: no ISENABLER write at all, read-back says 0 */
		clr(); memset(rb, 0xff, sizeof rb);
		CHK(sweet_spi_attach_off(541, h0, NULL, rb) == 0);
		CHK(strstr(wlog, "140=") == NULL);                           /* ISENABLER word 16 of INTID 541 = 0x100 + 0x40 */
		CHK(rb[1] == 0);
		CHK(strstr(wlog, "1c0=20000000") != NULL);                   /* ICENABLER 0x180 + 0x40: bit 29 */
		/* enable: exactly one ISENABLER write of bit 29, refused for a never attached INTID */
		wlog[0] = 0; CHK(sweet_spi_enable(541, &en) == 0); CHK(en == 1); CHK(strcmp(wlog, "140=20000000 ") == 0);
		wlog[0] = 0; CHK(sweet_spi_enable(542, &en) == 22); CHK(wlog[0] == 0);
		wlog[0] = 0; CHK(sweet_spi_unmask(542) == 22); CHK(wlog[0] == 0);
		CHK(sweet_spi_enable(31, &en) == 22 && sweet_spi_enable(1020, &en) == 22 && wlog[0] == 0);
		/* handler returns DEFER (2): masked at the GIC (ICENABLER bit 29), counted as deferred, NOT unhandled; unmask re-enables and counts */
		ret_val = 2; sweet_spi_detach(541); CHK(sweet_spi_attach(541, hret, NULL, rb) == 0);
		un0 = sweet_spi_unhandled; wlog[0] = 0;
		sweet_spi_dispatch(541);
		CHK(strcmp(wlog, "1c0=20000000 ") == 0); CHK(sweet_spi_deferred == 1 && sweet_spi_unhandled == un0);
		wlog[0] = 0; CHK(sweet_spi_unmask(541) == 0); CHK(strcmp(wlog, "140=20000000 ") == 0); CHK(sweet_spi_unmasked == 1);
		/* handler returns 1: the old safety net, counted unhandled, not deferred */
		ret_val = 1; wlog[0] = 0; sweet_spi_dispatch(541);
		CHK(strcmp(wlog, "1c0=20000000 ") == 0); CHK(sweet_spi_deferred == 1 && sweet_spi_unhandled == un0 + 1);
		/* handler returns 3 (anything else non-zero): also the safety net */
		ret_val = 3; wlog[0] = 0; sweet_spi_dispatch(541); CHK(sweet_spi_deferred == 1 && sweet_spi_unhandled == un0 + 2);
		/* detached: unmask refused, nothing written; an INTID with no handler is masked by the dispatcher */
		CHK(sweet_spi_detach(541) == 0); wlog[0] = 0; CHK(sweet_spi_unmask(541) == 22); CHK(wlog[0] == 0);
		sweet_spi_dispatch(541); CHK(strcmp(wlog, "1c0=20000000 ") == 0 && sweet_spi_unhandled == un0 + 3);
		/* counts of the table for one INTID */
		{ uint64_t hd, un; uint32_t ul; clr(); sweet_spi_attach(300, h0, NULL, rb); sweet_spi_dispatch(300); sweet_spi_dispatch(300); sweet_spi_counts(300, &hd, &un, &ul); CHK(hd == 2); }
	}
#endif
	printf(bad ? "SPI FAIL %%d\n" : "SPI OK\n", bad);
	return bad != 0;
}
'''


def block_0272():
    with open(P272) as f:
        t = f.read()
    i = t.index("diff --git a/pexpert/arm/pe_fiq.c b/pexpert/arm/pe_fiq.c")
    sec = t[i:]
    adds = [l[1:] for l in sec.splitlines() if l.startswith("+") and not l.startswith("+++")]
    txt = "\n".join(adds) + "\n"
    assert "sweet_spi_dispatch" in txt and "SWEET_SPI_SLOTS       4" in txt
    return txt


def block_0346(mutate=None):
    """the 0272 block with 0346 applied by `patch`; mutate = (old, new) applied to the RESULT text (a mutated patch would have wrong hunk counts)"""
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "pexpert", "arm"))
        f = os.path.join(d, "pexpert", "arm", "pe_fiq.c")
        with open(f, "w") as fh:
            fh.write(block_0272())
        with open(P346) as fh:
            pt = fh.read()
        pf = os.path.join(d, "p.patch")
        with open(pf, "w") as fh:
            fh.write(pt)
        r = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-s", "-i", pf], cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, "patch 0346 does not apply to the 0272 block: " + r.stdout + r.stderr
        with open(f) as fh:
            out = fh.read()
        if mutate:
            assert mutate[0] in out, "mutant anchor moved: %s" % mutate[0]
            out = out.replace(mutate[0], mutate[1], 1)
        return out
    finally:
        shutil.rmtree(d, True)


def run(block, new):
    d = tempfile.mkdtemp()
    try:
        c = os.path.join(d, "t.c")
        with open(c, "w") as f:
            f.write(DRIVER.replace("%%", "%").replace("%(block)s", block))
        exe = os.path.join(d, "t")
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        args = [cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-O1"] + (["-DWITH_0346"] if new else []) + [c, "-o", exe]
        p = subprocess.run(args, capture_output=True, text=True)
        if p.returncode:
            return None, "compile: " + p.stderr[:900]
        q = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return q.returncode == 0, q.stdout
    finally:
        shutil.rmtree(d, True)


class Spi(unittest.TestCase):
    def test_new_behaviour(self):
        ok, out = run(block_0346(), True)
        self.assertIsNotNone(ok, out); self.assertTrue(ok, out); self.assertIn("SPI OK", out)

    def test_gpu_path_unchanged(self):
        """the identical register write log, memory image and dispatcher result for the GPU's INTIDs before and after 0346"""
        ok0, o0 = run(block_0272(), False)
        ok1, o1 = run(block_0346(), True)
        self.assertTrue(ok0, o0); self.assertTrue(ok1, o1)
        pick = lambda o, k: [l for l in o.splitlines() if l.startswith(k)]
        for k in ("LOG ", "IMG ", "DISP1 "):
            self.assertTrue(pick(o0, k), "no %s line" % k)
            self.assertEqual(pick(o0, k), pick(o1, k), "GPU path differs in %s" % k)

    def test_mutants_killed(self):
        base = block_0346()
        cases = [
            ("SWEET_SPI_SLOTS back to 4", ("#define SWEET_SPI_SLOTS       8", "#define SWEET_SPI_SLOTS       4")),
            ("DEFER counted as unhandled", ("\t\t\t\tsweet_spi_deferred++;", "\t\t\t\tsweet_spi_unhandled++;")),
            ("DEFER not masked", ("\t\t\t\tif (gicd_base != 0 && intid >= 32 && intid < 1020) {\n\t\t\t\t\tgicd_write32(SWEET_GICD_ICENABLER + 4 * (intid / 32), 1u << (intid % 32));\n\t\t\t\t}\n\t\t\t\tsweet_spi_deferred++;",
                                  "\t\t\t\tsweet_spi_deferred++;")),
            ("attach_off enables the SPI", ("\treturn sweet_spi_attach_x(intid, fn, arg, rb, 0);", "\treturn sweet_spi_attach_x(intid, fn, arg, rb, 1);")),
            ("unmask of an unattached INTID", ("\tif (!found) {\n\t\treturn 22;\n\t}", "\tif (0) {\n\t\treturn 22;\n\t}")),
            ("unmask does not count", ("\t\tsweet_spi_unmasked++;", "\t\t(void)0;")),
        ]
        for name, mut in cases:
            blk = block_0346(mut)
            self.assertNotEqual(blk, base, name)
            ok, out = run(blk, True)
            self.assertIsNotNone(ok, "mutant does not compile (%s): %s" % (name, out))
            if name == "SWEET_SPI_SLOTS back to 4":
                self.assertFalse(ok, "mutant survived: " + name)
            else:
                self.assertFalse(ok, "mutant survived: " + name)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s spi-glue tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
