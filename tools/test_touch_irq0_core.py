"""xnu patch 0355 (sweettouchirq=2, touch interrupt STAGE 0, READ ONLY): host test of the pure core. The block between STI_CORE_BEGIN and STI_CORE_END of bsd/dev/sweet_touch_irq0.h (new-file hunk of
xnu/patches/0355-sweet-touch-irq0.patch) is extracted from the patch text and compiled with the host C compiler together with a driver that plays the alive pin controller (gpa9 pad CON/DAT and the four
EINT words). The fake bus FAILS THE TEST ON ANY WRITE (the ops table has a wr slot that the core must never call) and on any read outside the test's own list of six offsets.
Checked: mode 0 / absent = zero reads, zero log lines; 1 and other values = GAP line, off; mode 2 = exactly the six one-time reads in order and one exact log line; mapping failure, wrong pad, a failing or
refused read, all-ones windows = GAP, stage 0 off, no further read; DAT bit 0 read before each FIFO read; reset-wait log cap 40 and first-change delay; the 2x2 counters, edges, longest low/empty run, the
10 s cadence, the 95 % verdict and the 20 % low/empty GAP at their exact boundaries. Then mutants of the core (added writes, loosened allowlist, wrong offsets, swapped cells, thresholds, ...) that the
same driver must kill, and equivalent mutants pinned as equivalent. The kernel glue (mapping, the real MMIO read, the call sites) is NOT run here: only a build + a boot shows it."""
import os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from touch_irq0_core_lib import PATCH, core_text, patch_header, run
from touch_irq0_core_mut_lib import EQUIVALENT, mutants


def patch_text():
    with open(PATCH) as f:
        return f.read()


class Core(unittest.TestCase):
    def test_patch_core(self):
        ok, out = run(core_text())
        self.assertIsNotNone(ok, out)
        self.assertTrue(ok, out)
        self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        ms = mutants(core)
        self.assertGreaterEqual(len(ms), 100)
        for name, mc in ms:
            self.assertNotEqual(mc, core, "mutant did not change the core: %s" % name)
            ok, out = run(mc)
            self.assertIsNotNone(ok, "mutant does not compile (%s): %s" % (name, out))
            self.assertFalse(ok, "mutant survived: %s" % name)

    def test_equivalent_mutants_stay_equivalent(self):
        core = core_text()
        for name, a, b in EQUIVALENT:
            self.assertEqual(core.count(a), 1, name)
            ok, out = run(core.replace(a, b, 1))
            self.assertTrue(ok, "%s: not equivalent any more: %s" % (name, out))

    def test_core_has_no_write(self):
        """the core never calls a write and has no store of its own to anything but its own state"""
        core = core_text()
        self.assertNotIn("->wr(", core)
        self.assertNotIn("wr(", re.sub(r"int \(\*wr\)\(", "", core))
        self.assertNotIn("volatile", core)
        self.assertNotIn("__asm__", core)
        self.assertEqual(len(re.findall(r"o->rd\(", core)), 1)
        self.assertIn("MUST be NULL in the kernel", core)

    def test_glue_adds_no_store(self):
        """every added line of sweet_touch_gs101.c: no tg_wr / pin store / GIC / SPI attach / poke allowlist change, no volatile store"""
        t = patch_text()
        c = t[t.index("diff --git a/bsd/dev/sweet_touch_gs101.c"):t.index("diff --git a/bsd/dev/sweet_touch_irq0.h")]
        added = [ln[1:] for ln in c.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        for ln in added:
            for bad in ("tg_wr(", "tg_pin_put", "tg_pin_set", "tg_pin_set_pud", "sweet_spi_", "tg_xfer", "tg_poke", "POKE_", "ml_io_map", "*(volatile uint32_t *)(tg.alive + off) =", "thread_wakeup"):
                self.assertNotIn(bad, ln, ln)
        stores = [ln for ln in added if re.search(r"\*\s*\(\s*volatile[^)]*\)\s*\([^)]*\)\s*=[^=]", ln)]
        self.assertEqual(stores, [])
        self.assertIn("static const struct sti_ops tg_sti_ops = { .ctx = NULL, .rd = tg_sti_rd };", added)     # no .wr
        self.assertEqual([ln for ln in added if "tg_map(" in ln], ["\t\t\ttg.alive = tg_map(tg.irq.bank_pa - STI_OFF_CON, 0x1000);"])
        removed = [ln for ln in c.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        self.assertEqual(removed, [])             # nothing of the existing driver is removed or rewritten
        self.assertNotIn("sweet_touch_poke.h", "\n".join(ln for ln in c.splitlines() if ln.startswith("+") and "include" not in ln))

    def test_glue_behind_the_flag(self):
        """mode 0 byte for byte: every added call site is behind tg_sti.mode == 2, irq0_dat >= 0 or the core's own on-flag; the poll sleep line is untouched"""
        t = patch_text()
        c = t[t.index("diff --git a/bsd/dev/sweet_touch_gs101.c"):t.index("diff --git a/bsd/dev/sweet_touch_irq0.h")]
        self.assertIn("+\tif (tg_sti.mode == 2) {", c)
        self.assertIn("+\t\tint irq0_dat = sti_dat(&tg_sti, &tg_sti_ops);", c)
        self.assertEqual(c.count("+\t\tif (irq0_dat >= 0) {\n+\t\t\tsti_loop_obs(&tg_sti, irq0_dat, n, tg_now_us());"), 1)
        self.assertEqual(c.count("+\t\tif (irq0_dat >= 0) {\n+\t\t\tsti_reset_obs(&tg_sti, irq0_dat, e[0], tg_now_us());"), 1)
        self.assertEqual(c.count("sti_reset_end(&tg_sti, "), 3)
        self.assertEqual(c.count("sti_reset_begin(&tg_sti, t0);"), 1)
        self.assertNotIn("tg_sleep_ms", "\n".join(ln for ln in c.splitlines() if ln[:1] in "+-"))
        self.assertIn('PE_parse_boot_argn("sweettouchirq", &irq0, sizeof(irq0))', c)
        self.assertIn("sti_arg(&tg_sti, irq0_given, irq0);", c)
        self.assertIn("#define STI_LOG(...) printf(__VA_ARGS__)", c)

    def test_patch_text(self):
        t = patch_text()
        for s in ("NO HARDWARE WRITE", "sweettouchirq=2", "stage 1 not in this patch", "SWEET TOUCH IRQ0: gpa9 read once:", "SWEET TOUCH IRQ0: reset-wait i=%u dat=%d ev=%02x",
                  "SWEET TOUCH IRQ0: passes %u low/found %u low/empty %u high/found %u high/empty %u edges %u maxrun_low_empty %u drain_err %u verdict",
                  "NOT RUN ON HARDWARE", "docs/research/155-touch-ftm5-interrupt.md", "ORDER: after 0354", "pinctrl-gs.c:79", "0x174D0718", "0x174D0A18"):
            self.assertIn(s, t)
        h = patch_header()
        self.assertIn("fake bus that FAILS THE TEST on any write", h)
        self.assertNotIn("sweet_touch_poke", t[t.index("diff --git a/bsd/dev/sweet_touch_irq0.h"):])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s touch-irq0-core tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f))
    sys.exit(1 if f else 0)
