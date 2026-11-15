"""xnu patch 0354 (sweetufsirq): host test of the pure core. The block between SUI_CORE_BEGIN and SUI_CORE_END of bsd/dev/sweet_ufs_irq.h (new-file hunk of xnu/patches/0354-sweet-ufs-irq.patch)
is extracted from the patch text and compiled with the host C compiler together with a driver that plays the UFS host controller (fake VER/IS/IE/UTRIACR/UTRLCNR/UTRLDBR registers with W1C on IS, a
fake GIC, a fake clock, a fake assert_wait / block) and records EVERY register access and call as a token, so the tests assert exact sequences:
  arm = UTRLDBR check, GIC attach (disabled), stale IS W1C of the bits read, UTRIACR read, IE := 0x1, IE read back, SPI enable, armed last; every failure path disarms (IE 0, IS, detach) with a GAP;
  ISR = read IS and IE, W1C exactly the bits read, one sequence bump + wake per UTRCS interrupt, spurious / other-bit accounting, the storm guard (>100 entries without UTRCS per second);
  wait = spin, assert_wait, RE-READ the doorbell, block, 1 ms (or deadline) fallback wake, hits / poll_found / fast classification, a completion between the ISR's IS read and the waiter's doorbell
         scan, a completion between assert_wait and the re-read, the 200-commands-without-interrupt disarm, deadline passes to the caller;
  ack = W1C of UTRCS only, other bits logged (rate limited); ro = reads only; mode 0 = zero register accesses, zero notes; the judge (allowlist + value rules) refuses everything else.
Then mutants of the core (swapped arm steps, blanket IS write, ack after, wrong thresholds, loosened allowlist, ...) that the same driver must kill, and equivalent mutants pinned as equivalent.
The kernel glue (struct sui_ops implementations in sweet_ufs.c, the GIC calls, assert_wait / thread_block, the log lines, the thread) is NOT run here: only a build + a boot shows it."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufs_irq_core_lib import PATCH, core_text, run
from ufs_irq_core_mut_lib import EQUIVALENT, mutants


class Core(unittest.TestCase):
    def test_patch_core(self):
        ok, out = run(core_text())
        self.assertIsNotNone(ok, out)
        self.assertTrue(ok, out)
        self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        ms = mutants(core)
        self.assertGreaterEqual(len(ms), 150)
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

    def test_glue_text_in_patch(self):
        with open(PATCH) as f:
            t = f.read()
        for s in ('PE_parse_boot_argn("sweetufsirq"', "#define SU_INTID 564u", "sweet_spi_attach_off(SU_INTID", "sweet_spi_enable(SU_INTID", "sweet_spi_detach(SU_INTID",
                  "SWEET UFS IRQ: irqs %u spurious %u poll_found %u cmds %u mode %s", "SWEET UFS IRQ: ro cmds %u is0 %u", "sweet_ufs_irq_quiesce();", "sui_start();",
                  "ORDER: after 0351", "needs the rule-8 Opus review", "UNREVIEWED", "docs/research/154-ufs-completion-interrupt.md", "ufshcd.c:6838-6877", "gs101.dtsi"):
            self.assertIn(s, t)

    def test_reset_path_ie_off_glue(self):
        """review 0354 finding 2: sweet_machine_reset_x calls the lock-free IE := 0 right after the pets stop; the glue function has no lock, no print, no GIC call; the quiesce logs the no-lock path"""
        with open(PATCH) as f:
            t = f.read()
        r = t[t.index("diff --git a/bsd/dev/sweet_restart.c"):t.index("diff --git a/bsd/dev/sweet_ufs.c")]
        self.assertIn("+extern void sweet_ufs_irq_ie_off(void);", r)
        self.assertRegex(r, r"\n \tsweet_wdt_stop_pets\(\);\n\+\tsweet_ufs_irq_ie_off\(\);")
        self.assertEqual(r.count("sweet_ufs_irq_ie_off();"), 1)
        u = t[t.index("diff --git a/bsd/dev/sweet_ufs.c"):t.index("diff --git a/bsd/dev/sweet_ufs_irq.h")]
        i = u.index("sweet_ufs_irq_ie_off(void)\n+{")
        body = u[i:u.index("\n+}", i)]
        self.assertEqual(body.split("\n+{\n")[1].strip(), "+\tsui_ie_off(&sui, &sui_kops);")
        for bad in ("lck_", "printf", "sweet_spi_", "delay", "thread_"):
            self.assertNotIn(bad, body)
        self.assertIn("quiesce without the driver lock", u)
        self.assertNotIn("(offset %#x, step %u)", u)          # the bus GAP no longer carries the old step argument
        core = core_text()
        i = core.index("sui_ie_off(struct sui")
        fn = core[i:core.index("\n}\n", i)]
        self.assertEqual(fn.count("sui_wr("), 1)
        self.assertNotIn("note(", fn)
        self.assertNotIn("spi_detach", fn)

    def test_ack_has_no_write(self):
        """review 0354 finding 1: sui_ack of the core never writes (no sui_wr, no o->wr)"""
        core = core_text()
        i = core.index("sui_ack(struct sui")
        fn = core[i:core.index("\n}\n", i)]
        self.assertNotIn("sui_wr(", fn)
        self.assertNotIn("->wr(", fn)

    def test_mode0_glue_is_behind_the_boot_arg(self):
        """mode 0 byte for byte: no existing statement of ufs_scsi's polled path is removed except the two that the patch rewrites on purpose (the loop condition and the IS write, whose old
        text stays in the polled branch); the write path (ufs_write_blocks, its bounds check, the doorbell write) is not touched; every added call site is behind sui.mode / irq_wait"""
        with open(PATCH) as f:
            t = f.read()
        body = t[t.index("diff --git a/bsd/dev/sweet_restart.c"):]
        i = body.index("diff --git a/bsd/dev/sweet_ufs.c")
        j = body.index("diff --git a/bsd/dev/sweet_ufs_irq.h")
        ufs = body[i:j]
        removed = [ln for ln in ufs.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        self.assertEqual(removed, ["-\twhile (ufs_rd(REG_UTRLDBR) & slot_bit) {", "-\tufs_wr(REG_IS, ufs_rd(REG_IS));         /* acknowledge completion status */"])
        for forbidden in ("ufs_write_blocks", "ufs_sync_cache", "REG_UTRLBA", "REG_UTRLRSR", "REG_HCE", "REG_UTRLDBR, "):
            self.assertFalse([ln for ln in ufs.splitlines() if ln.startswith("+") and forbidden in ln], forbidden)
        added = [ln for ln in ufs.splitlines() if ln.startswith("+")]
        self.assertEqual([ln for ln in added if "REG_UTRLCLR" in ln], ["+\t\t\tufs_wr(REG_UTRLCLR, su.gs101 ? slot_bit : ~slot_bit);"])   # only the copy of the old timeout write
        self.assertIn("+\tconst bool irq_wait = sui.mode == SUI_MODE_ON &&", ufs)
        self.assertIn("+\twhile (irqw != SUI_W_DONE && (ufs_rd(REG_UTRLDBR) & slot_bit)) {", ufs)
        self.assertIn("+\t\tufs_wr(REG_IS, ufs_rd(REG_IS));         /* acknowledge completion status */", ufs)
        self.assertIn("+\tsui_ro_cmd(&sui, &sui_kops);", ufs)
        self.assertIn("+\tsui_start();", ufs)
        self.assertNotIn("ufs_write_blocks", "\n".join(added))
        self.assertNotIn("sweet_ufs_ioctl", "\n".join(added))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s ufs-irq-core tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f))
    sys.exit(1 if f else 0)
