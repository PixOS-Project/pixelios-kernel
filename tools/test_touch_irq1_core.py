"""xnu patch 0356 (sweettouchirq=1, touch interrupt STAGE 1, the first patch that WRITES the gpa9 EINT registers): host test of the pure core. The block between STI1_CORE_BEGIN and STI1_CORE_END of
bsd/dev/sweet_touch_irq1.h (new-file hunk of xnu/patches/0356-sweet-touch-irq1.patch) is extracted from the patch text and compiled with the host C compiler together with a driver that plays the alive
pin controller (the gpa9 pad words and the EINT words, other pins filled with sentinels), the FTM5 FIFO (the line is low while it holds events), the EINT unit (level latch, mask) and the GIC slot.
The fake bus judges every store against the test's OWN list (CON, ECON, EMASK, EPEND only; EPEND exactly 0x1; only pin 0's bits change) and fails the test on a log call from the ISR or the reset path and on any
GIC call from the ISR. Checked: boot-arg values; mode 0 = zero accesses; the arm order (mask, CON, ECON, EPEND, attach, unmask, enable) and its write values; every arm failure (store failure, read-back
mismatch, attach, enable) restoring every word; pre-conditions (ready, mux, pad, CON func, all-ones window, read failures) refusing with no store; the ISR (mask first, EPEND, then the sequence number, wake,
no log, no SPI); the thread pass (EPEND then unmask, no read-back, no lost wakeup, an ISR during the drain is not a missed edge); storm, stuck-low and missed-interrupt auto-off at their exact boundaries; clean
stop (disarm order, no re-arm); the reset path (one store, EMASK bit 0 := 1, silent); the 10 s summary row; the judge's refusals one by one. Then mutants of the core that the same driver must kill, and
equivalent mutants pinned as equivalent. The kernel glue (mapping, the real MMIO, the GIC helpers, assert_wait/thread_block, the call sites) is NOT run here: only a build + a boot shows it."""
import os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from touch_irq1_core_lib import PATCH, core_text, patch_header, run
from touch_irq1_core_mut_lib import EQUIVALENT, MUTANTS


def patch_text():
    with open(PATCH) as f:
        return f.read()


def glue_text():
    t = patch_text()
    return t[t.index("diff --git a/bsd/dev/sweet_touch_gs101.c"):t.index("diff --git a/bsd/dev/sweet_touch_irq1.h")]


class Core(unittest.TestCase):
    def test_patch_core(self):
        ok, out = run(core_text())
        self.assertIsNotNone(ok, out)
        self.assertTrue(ok, out)
        self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        self.assertGreaterEqual(len(MUTANTS), 100)
        names = set()
        for name, old, new in MUTANTS:
            self.assertNotIn(name, names, "duplicate mutant name")
            names.add(name)
            self.assertEqual(core.count(old), 1, "mutant site not unique/present: %s" % name)
            mc = core.replace(old, new, 1)
            self.assertNotEqual(mc, core, name)
            ok, out = run(mc)
            self.assertIsNotNone(ok, "mutant does not compile (%s): %s" % (name, out))
            self.assertFalse(ok, "mutant survived: %s" % name)

    def test_equivalent_mutants_stay_equivalent(self):
        core = core_text()
        for name, a, b in EQUIVALENT:
            self.assertEqual(core.count(a), 1, name)
            ok, out = run(core.replace(a, b, 1))
            self.assertTrue(ok, "%s: not equivalent any more: %s" % (name, out))

    def test_write_sites_in_the_core(self):
        """the core has exactly one store call (inside sti1_rmw) and one read call; every sti1_rmw call names a row of the write table"""
        core = core_text()
        self.assertEqual(len(re.findall(r"o->wr\(", core)), 1)
        self.assertEqual(len(re.findall(r"o->rd\(", core)), 1)
        self.assertNotIn("volatile int *", core)
        self.assertNotIn("__asm__", core)
        sites = re.findall(r"sti1_rmw\(st, o, (STI1_OFF_\w+), ([^,]+), (\d), (\d)\)", core)
        self.assertEqual(len(sites), 15)    # the audit list: every rmw call site of the core
        for off, val, verify, isr in sites:
            self.assertIn(off, ("STI1_OFF_CON", "STI1_OFF_ECON", "STI1_OFF_EMASK", "STI1_OFF_EPEND"), off)
        # the write rows of the table: exactly CON, ECON, EMASK, EPEND
        rows = re.findall(r"\{ (STI1_OFF_\w+),\s+(STI1_R \| STI1_W|STI1_R|STI1_W),\s+(\w+) \}", core)
        wr = sorted(r[0] for r in rows if "STI1_W" in r[1])
        self.assertEqual(wr, ["STI1_OFF_CON", "STI1_OFF_ECON", "STI1_OFF_EMASK", "STI1_OFF_EPEND"])
        self.assertEqual(sorted(r[0] for r in rows if "STI1_W" not in r[1]), ["STI1_OFF_DAT", "STI1_OFF_EFLTCON"])
        # the ISR and the reset path call the rmw with isr = 1 only (no logging)
        isr = core[core.index("sti1_isr(struct sti1 *st"):core.index("static int\nsti1_armed")]
        for off, val, verify, isr_flag in re.findall(r"sti1_rmw\(st, o, (STI1_OFF_\w+), ([^,]+), (\d), (\d)\)", isr):
            self.assertEqual((verify, isr_flag), ("0", "1"), isr)
        self.assertNotIn("STI1_LOG", isr)
        reset = core[core.index("sti1_reset_mask(struct sti1 *st"):]
        self.assertNotIn("STI1_LOG", reset)
        self.assertNotIn("spi_", reset)
        self.assertNotIn("spi_", isr)

    def test_glue_store_and_allowlist(self):
        """the kernel store: one volatile store in the new code, behind its own second allowlist (offsets and the EPEND value), no other store, no poke/SPI change"""
        c = glue_text()
        added = [ln[1:] for ln in c.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        stores = [ln for ln in added if re.search(r"\*\s*\(\s*volatile[^)]*\)\s*\([^)]*\)\s*=[^=]", ln)]
        self.assertEqual(stores, ["\t*(volatile uint32_t *)(tg.alive + off) = v;"])
        text = "\n".join(added)
        i = text.index("tg_sti1_wr(void *ctx")
        wr = text[i:text.index("static int\ntg_sti1_isr_k", i) if "static int\ntg_sti1_isr_k" in text[i:] else text.index("static int tg_sti1_isr_k", i)]
        self.assertIn("if (off != 0x0C0u && off != 0x718u && off != 0x918u && off != 0xA18u) {", wr)
        self.assertIn("if (off == 0xA18u && v != 1u) {", wr)
        self.assertLess(wr.index("0xA18u && v != 1u"), wr.index("= v;"))
        self.assertLess(wr.index("off != 0x0C0u"), wr.index("= v;"))
        for ln in added:
            for bad in ("tg_wr(", "tg_pin_put", "tg_pin_set", "tg_pin_set_pud", "tg_xfer", "tg_poke", "POKE_", "0x0C8", "0x828u"):
                if bad == "0x828u" or bad == "0x0C8":
                    self.assertNotIn(bad, ln, ln)
                else:
                    self.assertNotIn(bad, ln, ln)
        # the glue's ops table: the same four write offsets as the core's write rows
        core = core_text()
        for off in ("0x0C0u", "0x718u", "0x918u", "0xA18u"):
            self.assertIn("#define STI1_OFF_%s" % {"0x0C0u": "CON       ", "0x718u": "ECON      ", "0x918u": "EMASK     ", "0xA18u": "EPEND     "}[off] + off, core)
        removed = [ln for ln in c.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        self.assertEqual(sorted(removed), sorted(["-\t\ttg_sleep_ms(n > 0 ? 1 : 8);", "-\tif (!tg.reset_done && !tg_reset_and_ready(&logged)) {",
                                                  "-\t{   /* patch 0355: sweettouchirq 0/absent = unchanged, 2 = read-only stage 0, 1 or other = GAP (stage 1 not in this patch) */",
                                                  "-\t\tsti_arg(&tg_sti, irq0_given, irq0);"]))

    def test_glue_behind_the_flag(self):
        """mode 0 = the old loop: every added call is behind tg_sti1.mode == 1; the polled sleep survives as the else branch; stage 2 keeps 0355; arm after scan enable, mux flag from the existing check"""
        c = glue_text()
        for s in ("+\t\tif (tg_sti1.mode == 1) {\n+\t\t\tsti1_pass_begin(&tg_sti1, &tg_sti1_ops);",
                  "+\t\tif (tg_sti1.mode == 1) {\n+\t\t\tsti1_pass_end(&tg_sti1, &tg_sti1_ops, n, tg_now_us() * 1000u);",
                  "+\t\tif (tg_sti1.mode == 1 && sti1_armed(&tg_sti1)) {\n+\t\t\ttg_sti1_sleep();",
                  "+\t\t} else {\n+\t\t\ttg_sleep_ms(n > 0 ? 1 : 8);\n+\t\t}",
                  "+\t\tsti_arg(&tg_sti, irq0_given && irq0 == 2, irq0);\n+\t\tsti1_arg(&tg_sti1, irq0_given, irq0);",
                  "+\ttg_sti1_mux_ok = true;",
                  "+\tbool irq1_ready = tg.reset_done;",
                  "+\tif (!tg.reset_done && !(irq1_ready = tg_reset_and_ready(&logged))) {",
                  "(void)sti1_arm(&tg_sti1, &tg_sti1_ops, irq1_ready, tg_sti1_mux_ok, tg.irq.bank_pa, tg.irq.pin);",
                  "#define STI1_LOG(...) printf(__VA_ARGS__)"):
            self.assertEqual(c.count(s), 1, s)
        # the arm sits after the scan-enable success line and before the loop
        self.assertLess(c.index('printf("SWEET TOUCH: scanning,'), c.index("sti1_arm(&tg_sti1"))
        self.assertLess(c.index("sti1_arm(&tg_sti1"), c.index("for (;;) {"))
        # the mux flag is set after the existing check returned
        self.assertIn(" \t\treturn;\n \t}\n+\ttg_sti1_mux_ok = true;", c)
        self.assertIn("(void)tg_cmu_enable();", c)
        # the ISR trampoline calls the core ISR and nothing else
        i = c.index("tg_sti1_isr_k(void *arg, uint32_t intid)\n+{")
        body = c[i:c.index("\n+}", i)]
        self.assertIn("return sti1_isr(&tg_sti1, &tg_sti1_ops);", body)
        self.assertNotIn("printf", body)
        # the wait: assert_wait, then the re-check, then the block
        j = c.index("tg_sti1_sleep(void)\n+{")
        sl = c[j:c.index("\n+}", j)]
        self.assertLess(sl.index("assert_wait_deadline"), sl.index("sti1_wake_pending"))
        self.assertLess(sl.index("sti1_wake_pending"), sl.index("thread_block"))
        self.assertIn("clear_wait(current_thread(), THREAD_AWAKENED);", sl)

    def test_restart_hooks(self):
        """sweet_restart.c: the quiesce after the filesystem sync (next to the UFS one), the lock-free mask as the statement right before the UFS one in sweet_machine_reset_x"""
        t = patch_text()
        r = t[t.index("diff --git a/bsd/dev/sweet_restart.c"):t.index("diff --git a/bsd/dev/sweet_touch_gs101.c")]
        self.assertIn("+\tsweet_touch_irq1_mask_off();    /* patch 0356", r)
        self.assertIn("+\tsweet_touch_irq1_quiesce();     /* patch 0356", r)
        self.assertLess(r.index("sweet_touch_irq1_mask_off();"), r.index("sweet_ufs_irq_ie_off();         /* patch 0354"))
        removed = [ln for ln in r.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        self.assertEqual(removed, [])
        g = glue_text()
        q = g[g.index("sweet_touch_irq1_quiesce(void)\n+{"):]
        q = q[:q.index("\n+}")]
        self.assertIn("sti1_quiesce_begin", q)
        self.assertNotIn("sti1_teardown", q)      # the touch thread owns the state change
        m = g[g.index("sweet_touch_irq1_mask_off(void)\n+{"):]
        m = m[:m.index("\n+}")]
        self.assertIn("sti1_reset_mask(&tg_sti1, &tg_sti1_ops);", m)
        self.assertNotIn("printf", m)

    def test_patch_text(self):
        t = patch_text()
        for s in ("sweettouchirq=1", "NOT RUN ON HARDWARE", "UNREVIEWED", "rule-8", "docs/research/155-touch-ftm5-interrupt.md", "ORDER: after 0355",
                  "EXYNOS_EINT_LEVEL_LOW = 0", "EXYNOS_EINT_CON_MASK = 0xF", "pinctrl-exynos.c (exynos_irq_mask :58-87", "exynos_irq_set_type :154-210",
                  "0x174D0718", "0x174D0A18", "0x174D0918", "0x174D00C0", "EFLTCON 0x174D0828 (read only)", "SWEET TOUCH IRQ: irqs N empty N wakes N tmo_drains N tmo_found N storm N mode irq|off:<why>"):
            self.assertIn(s, t)
        h = patch_header()
        self.assertIn("The ISR makes NO SPI access and NO log call", h)
        self.assertIn("EFLTCON has NO write row", h)
        self.assertNotIn("sweet_touch_poke", t[t.index("diff --git a/bsd/dev/sweet_touch_irq1.h"):])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s touch-irq1-core tests=%d failures=%d mutants=%d" % ("PASS" if f == 0 else "FAIL", n, f, len(MUTANTS)))
    sys.exit(1 if f else 0)
