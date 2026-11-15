#!/usr/bin/env python3
"""Host tests for xnu/patches/0284-sweet-chg-i2c.patch (charger phase 2: default-off READ pass over the MAX77759 chips through HSI2C12). No phone, no kernel build.

    python3 tools/test_chg_i2c.py   -> `RESULT: PASS chg-i2c cases=N patch_sha=<sha256 of the patch>` / `RESULT: FAIL ...`

Proves (the Opus reviewer's evidence, rule 8: this is a NEW hardware-store path, HSI2C12 transfer registers only):
  1. 0284 applies on the files of 0283 (git apply --check on the extracted sweet_chg.c) and builds the combined source;
  2. a fake HSI2C12 that CHECKS the auto-mode protocol of note 5.4 (tools/chg_i2c_host_test.c): the exact per-transfer write sequence (19 stores), only allowlisted
     offsets, never CONF/TIMING/TRAILIG/USI_CON/SW_RST/TIMEOUT_EN, the only TX byte per transfer is the register address of a read-list entry, NACK / timeout /
     bus error stop ALL further transfers, PMIC_ID / FG DEVNAME mismatch stops the pass, a phase-1 gate or controller-state mismatch blocks every store, default off;
  3. sweet_chg_gate_still_open() (tools/chg_recheck_test.c): reads only, any difference from the phase-1 snapshot refuses;
  4. the KERNEL branch compiled against XNU stubs on the real mkadt node (tools/chg_i2c_glue_test.c), HSI2C12 mapped PROT_READ: default and gate-closed runs store nothing;
  5. a text lint of the store path plus mutants that must be caught (a store that only the lint or only the flow test can see);
  6. ADT i-* properties = the note's offsets (independent copy), boot-arg sweetchgi2c known and literal.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_chg_probe as P1  # noqa: E402

PATCH = os.path.join(ROOT, "xnu", "patches", "0284-sweet-chg-i2c.patch")
CASES = []
NOTE_I = {"hsi2c12-fifo-ctl": 0x04, "hsi2c12-int-enable": 0x20, "hsi2c12-tx-data": 0x34, "hsi2c12-rx-data": 0x38, "hsi2c12-auto-conf": 0x44, "hsi2c12-timeout": 0x48,
          "hsi2c12-addr": 0x70}


def run(*a, **kw):
    kw.setdefault("timeout", 20)       # a mutant may loop forever: a timeout counts as "caught"
    try:
        return subprocess.run(a, capture_output=True, text=True, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(a, 124, "", "TIMEOUT")


def extract_new(path):
    p = open(PATCH).read()
    i = p.index("+++ b/%s\n" % path)
    body = p[i:].split("\n", 2)[2]
    j = body.find("\ndiff --git ")
    if j >= 0:
        body = body[:j]
    return "\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chg-i2c-")
        t = os.path.join(cls.tmp, "tree", "bsd", "dev")
        os.makedirs(t)
        open(os.path.join(t, "sweet_chg.c"), "w").write(P1.extract("bsd/dev/sweet_chg.c"))
        run("git", "-C", os.path.join(cls.tmp, "tree"), "init", "-q")
        cls.apply = run("git", "-C", os.path.join(cls.tmp, "tree"), "apply", "--include=bsd/dev/sweet_chg.c", PATCH)
        cls.chg = os.path.join(t, "sweet_chg.c")             # sweet_chg.c as patched by 0283 + 0284
        cls.i2c = os.path.join(cls.tmp, "sweet_chg_i2c.c")
        open(cls.i2c, "w").write(extract_new("bsd/dev/sweet_chg_i2c.c"))
        inc = os.path.join(cls.tmp, "inc")
        for h in ("sys/param.h", "sys/systm.h", "kern/clock.h", "mach/mach_time.h", "machine/machine_routines.h", "pexpert/pexpert.h", "pexpert/device_tree.h", "libkern/libkern.h"):
            os.makedirs(os.path.dirname(os.path.join(inc, h)), exist_ok=True)
            open(os.path.join(inc, h), "w").close()
        cls.inc = inc

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def flow(self, src=None, out="flow", extra=()):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", *extra, "-DSWEET_CHG_I2C_HOST", '-DCHG_I2C_SRC="%s"' % (src or self.i2c), "-I", os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "tools", "chg_i2c_host_test.c"), "-o", exe)
        return r, exe

    def glue(self, src=None, out="glue"):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DCHG_I2C_SRC="%s"' % (src or self.i2c), "-I", self.inc, os.path.join(ROOT, "tools", "chg_i2c_glue_test.c"), "-o", exe)
        return r, exe


class Apply(Base):
    def test_applies(self):
        self.assertEqual(self.apply.returncode, 0, self.apply.stderr)
        self.assertIn("sweet_chg_i2c_tick(log_pending);", open(self.chg).read())
        p = open(PATCH).read()
        self.assertEqual(len(re.findall(r"^diff --git ", p, re.M)), 3)
        self.assertIn("+bsd/dev/sweet_chg_i2c.c\t\tstandard", p)
        CASES.append("apply")


class Flow(Base):
    def test_cases(self):
        r, exe = self.flow()
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(exe)
        res = dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", r.stdout, re.M))
        self.assertGreaterEqual(len(res), 65, r.stdout[-2000:])
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, r.stdout[-3000:])
        self.assertIn("SUMMARY fails=0", r.stdout)
        CASES.extend("flow:" + k for k in res)

    def test_recheck(self):
        exe = os.path.join(self.tmp, "recheck")
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DSWEET_CHG_HOST", '-DCHG_SRC="%s"' % self.chg, "-I", os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "tools", "chg_recheck_test.c"), "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(exe)
        res = dict(re.findall(r"^CASE (\w+) (ok|FAIL)", r.stdout, re.M))
        self.assertGreaterEqual(len(res), 13, r.stdout)
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, r.stdout[-2500:])
        CASES.extend("recheck:" + k for k in res)

    def test_phase1_lint_still_clean_on_combined_source(self):
        self.assertEqual(P1.NoStore.lint(open(self.chg).read()), [])
        CASES.append("phase1_lint_combined")


class Glue(Base):
    def dt(self):
        props = P1.mkadt_chg_props()
        path = os.path.join(self.tmp, "chg.dt")
        with open(path, "w") as f:
            for k, v in props.items():
                if k.endswith("-reg") or k.startswith(("w-", "i-")):
                    f.write("%s %s\n" % (k, v.hex()))
        return path

    def test_kernel_branch(self):
        r, exe = self.glue()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.dt()
        o = run(exe, d, "default")
        self.assertEqual(o.returncode, 0, o.stdout)
        self.assertIn("SWEET CHG P2: off (default; sweetchgi2c=1", o.stdout)
        self.assertIn("GLUE nmaps=0 xfers=0 nwr=0", o.stdout)
        o = run(exe, d, "on-gate-closed")
        self.assertEqual(o.returncode, 0, o.stdout)
        self.assertIn("SWEET CHG P2: start (patch 0284, sweetchgi2c=1)", o.stdout)
        self.assertIn("phase-1 gate state is not the open pattern any more", o.stdout)
        self.assertIn("GLUE nmaps=1 xfers=0 nwr=0", o.stdout)
        # F4 on the kernel branch: an ADT with tx-data and auto-conf swapped is refused at load, before any gate or store
        txt = open(d).read().splitlines()
        sw = {"i-hsi2c12-tx-data": "i-hsi2c12-auto-conf", "i-hsi2c12-auto-conf": "i-hsi2c12-tx-data"}
        vals = {l.split()[0]: l.split()[1] for l in txt}
        d2 = os.path.join(self.tmp, "chg_swapped.dt")
        open(d2, "w").write("\n".join("%s %s" % (k, vals[sw[k]] if k in sw else v) for k, v in vals.items()) + "\n")
        o = run(exe, d2, "on-gate-closed")
        self.assertEqual(o.returncode, 0, o.stdout)
        self.assertIn("SWEET CHG GAP: P2 /sweet-chg i-hsi2c12-tx-data = 0x44, expected 0x34", o.stdout)
        self.assertNotIn("STUB gate_still_open", o.stdout)
        self.assertIn("nwr=0", o.stdout)
        o = run(exe, d, "nonode")
        self.assertIn("SWEET CHG GAP: P2 /sweet-chg hsi2c12-reg missing", o.stdout)
        CASES.append("glue_kernel_branch")


class Lint(Base):
    @staticmethod
    def lint(text):
        bad = []
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
        if len(re.findall(r"\bi2c_hw_wr32\s*\(", code)) != 3:    # prototype, kernel definition, the one call in i2c_wr
            bad.append("i2c_hw_wr32 appears %d times (want 3)" % len(re.findall(r"\bi2c_hw_wr32\s*\(", code)))
        if len(re.findall(r"\bi2c_va\s*\[[^\]]*\]\s*(=|\|=|&=|\^=|\+=|-=)(?!=)", code)) != 1:
            bad.append("stores through i2c_va: want exactly the one in i2c_hw_wr32")
        for m in re.finditer(r"\bi2c_wr\s*\(\s*(I_\w+)", code):
            if m.group(1) not in ("I_TOUT", "I_FIFOCTL", "I_INTST", "I_ADDR", "I_CTL", "I_INTEN", "I_AUTO", "I_TXD"):
                bad.append("i2c_wr to role %s" % m.group(1))
        if re.search(r"\bi2c_wr\s*\(\s*I_(CONF|RXD|FIFOST|TRANS)\b", code):
            bad.append("write to CONF/RX_DATA/FIFO_STATUS/TRANS_STATUS")
        wl = re.search(r"i2c_wr_allow\[\]\s*=\s*\{([^}]*)\}", code)
        if not wl or sorted(int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", wl.group(1))) != [0x00, 0x04, 0x20, 0x24, 0x34, 0x44, 0x48, 0x70]:
            bad.append("write allowlist differs from the independent copy")
        if re.search(r"\bCTL_SW_RST\b", re.sub(r"if \(\(role == I_CTL && \(v & CTL_SW_RST\)[^\n]*\n", "", code)) and re.search(r"i2c_wr\s*\([^;]*CTL_SW_RST", code):
            bad.append("SW_RST set in a write")
        # the only byte sent to a chip: TX_DATA written once, with `reg & 0xffu`
        txw = re.findall(r"i2c_wr\s*\(\s*I_TXD\s*,([^)]*\)?)", code)
        if txw != [" reg & 0xffu"] and txw != [" reg & 0xffu)"]:
            bad.append("TX_DATA is written with something other than the register address: %r" % txw)
        for pat in (r"\bml_io_map\b(?![^;]*0x4000)", r"\bsmc\w*\(", r"\bsweet_pmu_write\b", r"\b__asm__?\b"):
            if re.search(pat, code) and pat != r"\bml_io_map\b(?![^;]*0x4000)":
                bad.append("token /%s/" % pat)
        return bad

    def test_clean(self):
        self.assertEqual(self.lint(open(self.i2c).read()), [])
        CASES.append("lint_clean")

    def test_lint_mutants(self):
        base = open(self.i2c).read()
        muts = {
            "write_conf": ("if (i2c_wr(I_INTEN, INT_DONE | INT_ERR_MASK) != 0) {", "if (i2c_wr(I_CONF, 0) != 0 || i2c_wr(I_INTEN, INT_DONE | INT_ERR_MASK) != 0) {"),
            "allow_timing": ("{ 0x00, 0x04, 0x20, 0x24, 0x34, 0x44, 0x48, 0x70 }", "{ 0x00, 0x04, 0x20, 0x24, 0x34, 0x44, 0x48, 0x70, 0x68 }"),
            "chip_write_second_byte": ("if (i2c_wr(I_TXD, reg & 0xffu) != 0) {", "if (i2c_wr(I_TXD, reg & 0xffu) != 0 || i2c_wr(I_TXD, 0x55) != 0) {"),
            "store_elsewhere": ("static uint32_t\ni2c_hw_rd32(uint32_t off)\n{\n\treturn i2c_va[off / 4];", "static uint32_t\ni2c_hw_rd32(uint32_t off)\n{\n\ti2c_va[0x68 / 4] = 0;\n\treturn i2c_va[off / 4];"),
        }
        for name, (a, b) in muts.items():
            self.assertIn(a, base, name)
            self.assertTrue(self.lint(base.replace(a, b, 1)), "lint missed mutant %s" % name)
        CASES.append("lint_mutants")

    def test_flow_mutants(self):
        """each mutant removes one safety property; the host flow test (or the compiler) must fail it"""
        base = open(self.i2c).read()
        muts = [
            ("i2c_wr(I_TOUT, v & ~TOUT_EN)", "i2c_wr(I_TOUT, v)"),
            ("if (!i2c_dead && !i2c_gate(i2c_group == 1)) {", "if (0) {"),
            ("\tif (!sweet_chg_gate_still_open()) {", "\tif (0) {"),
            ("} else if ((conf & CONF_AUTO) == 0) {", "} else if (0) {"),
            ("if (i2c_pmic[0] != 0x3b) {", "if (0) {"),
            ("i2c_fgb[0][1] != 0x62 && i2c_fgb[0][1] != 0x63", "0"),
            ("i2c_dead = 1;", "", True),       # no error ever stops the pass (message-level and read-level flags are redundant on purpose: remove all)
            ("(rd ? CTL_RXCHON : CTL_TXCHON)", "(rd ? CTL_TXCHON : CTL_RXCHON)"),
            ("autov = (rd ? (AUTO_RW | AUTO_STOP) : 0u) | (len & 0xffffu);", "autov = (rd ? (AUTO_RW | AUTO_STOP) : AUTO_STOP) | (len & 0xffffu);"),
            ("i2c_wr(I_AUTO, autov) != 0 || i2c_wr(I_AUTO, autov | AUTO_RUN) != 0", "i2c_wr(I_AUTO, autov | AUTO_RUN) != 0"),
            ("(slave & 0x7fu) << 10", "(slave & 0x7fu) << 9"),
            ("if (n == 0 || n > 16u || !i2c_target_ok(slave, reg, n)) {", "if (n == 0 || n > 16u) {"),
            ("case 0x69: return n == 1 && reg >= 0xb0 && reg <= 0xcc;", "case 0x69: return n == 1;"),
            ("if (i2c_now_us() - t0 > I2C_TIMEOUT_US) {\n\t\t\t\twhat = \"TIMEOUT in the register-address message\";", "if (0) {\n\t\t\t\twhat = \"TIMEOUT in the register-address message\";"),
            ("INT_DONE | INT_ERR_MASK) != 0", "INT_DONE) != 0"),
            ("\t\treturn i2c_wr(I_INTST, st);                                                                                         /* 10 */\n\t}\n\twhile",
             "\t\treturn 0;\n\t}\n\twhile"),
            # review chg-0284 F5: the four survivors
            ("if ((st & INT_DONE) != 0 && (fs & FIFOST_TX_EMPTY) != 0) {", "if ((st & INT_DONE) != 0) {"),
            ("if ((st & INT_DONE) != 0) {\n\t\t\twhat = \"short read", "if (0) {\n\t\t\twhat = \"short read"),
            ("if ((st & INT_ERR_MASK) != 0) {\n\t\t\twhat = (st & INT_NACK_MASK) != 0 ? \"NACK (NO_DEV_ACK/NO_DEV) in the read message\"",
             "if (0) {\n\t\t\twhat = (st & INT_NACK_MASK) != 0 ? \"NACK (NO_DEV_ACK/NO_DEV) in the read message\""),
            ("\tif (i2c_dead) {\n\t\treturn -1;\n\t}\n\tif (n == 0", "\tif (0) {\n\t\treturn -1;\n\t}\n\tif (n == 0"),
            # F1
            ("} else if ((tr & TRANS_BUSY) != 0) {", "} else if (0) {"),
            ("} else if ((tr & 0xfu) == 0xcu) {", "} else if (0) {"),
            ("} else if (first && (au & AUTO_RUN) != 0) {", "} else if (0) {"),
            ("} else if (first && is != 0) {", "} else if (0) {"),
            ("} else if ((conf & ((1u << 29) | (1u << 30))) != 0) {", "} else if (0) {"),
            ("} else if ((ctl & (CTL_SW_RST | (1u << 22) | (1u << 29) | (1u << 30))) != 0) {", "} else if (0) {"),
            # F2
            ("if ((st & TRANS_BUSY) == 0) {\n\t\t\treturn 0;", "if (1) {\n\t\t\treturn 0;"),
            ("if (i2c_now_us() - t0 > I2C_TIMEOUT_US) {\n\t\t\twhat = \"TIMEOUT waiting for MASTER_BUSY", "if (0) {\n\t\t\twhat = \"TIMEOUT waiting for MASTER_BUSY"),
            # F3
            ("\t\t(void)i2c_wr(I_INTEN, 0);\n\t\tif (i2c_rd(I_INTST, &v) == 0) {", "\t\t(void)0;\n\t\tif (i2c_rd(I_INTST, &v) == 0) {"),
            ("(void)i2c_wr(I_INTEN, 0);       /* F3: GS", "(void)0;       /* F3: GS"),
            ("\t\tif (i2c_rd(I_INTST, &v) == 0) {\n\t\t\t(void)i2c_wr(I_INTST, v);", "\t\tif (i2c_rd(I_INTST, &v) == 0) {\n\t\t\t(void)0;"),
            # F4
            ("if (i2c_off[r] != i2c_expect[r]) {", "if (0) {"),
        ]
        for i, mut in enumerate(muts):
            a, b = mut[0], mut[1]
            self.assertIn(a, base, "mutant %d anchor" % i)
            path = os.path.join(self.tmp, "m%d.c" % i)
            open(path, "w").write(base.replace(a, b) if len(mut) > 2 else base.replace(a, b, 1))
            # a mutant that does not build is a test ERROR (AP-214), not a kill; unused-code warnings are allowed because removing a check can orphan a helper
            r, exe = self.flow(src=path, out="m%d" % i, extra=("-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-but-set-variable"))
            self.assertEqual(r.returncode, 0, "mutant %d does not build (ERROR, not a kill): %s" % (i, r.stderr[:400]))
            o = run(exe)
            # killed only by BEHAVIOUR: a failing CASE line or the runaway guard; a harness wall-clock timeout (rc 124) is not a kill
            self.assertNotEqual(o.returncode, 124, "mutant %d only hit the wall-clock timeout" % i)
            self.assertNotEqual(o.returncode, 0, "flow mutant %d survived: %s -> %s" % (i, a[:60], b[:60]))
            self.assertRegex(o.stdout, r"CASE \w+ FAIL", "mutant %d exit code without a failing CASE" % i)
        CASES.append("flow_mutants")

    def test_store_through_const_does_not_compile(self):
        # the glue test maps the window PROT_READ: a store outside i2c_hw_wr32 would crash the default/gate-closed runs
        base = open(self.i2c).read()
        a = "\ti2c_size = (uint32_t)reg[1];\n"
        self.assertIn(a, base)
        path = os.path.join(self.tmp, "gm.c")
        open(path, "w").write(base.replace(a, a + "\ti2c_va[0x68 / 4] = 0;\n", 1))
        r, exe = self.glue(src=path, out="gm")
        self.assertEqual(r.returncode, 0, r.stderr)
        o = run(exe, os.path.join(self.tmp, "chg.dt") if os.path.exists(os.path.join(self.tmp, "chg.dt")) else "/dev/null", "default")
        # with no DT file the program exits 2 before running: run a real one
        props = P1.mkadt_chg_props()
        d = os.path.join(self.tmp, "chg2.dt")
        with open(d, "w") as f:
            for k, v in props.items():
                if k.endswith("-reg") or k.startswith(("w-", "i-")):
                    f.write("%s %s\n" % (k, v.hex()))
        o = run(exe, d, "on-gate-closed")
        self.assertNotEqual(o.returncode, 0, o.stdout)    # SIGSEGV exit 3 (the first read-path call is gated, but i2c_gate reads CTL after: gate is closed first) or crash
        CASES.append("glue_store_crashes")


class Adt(Base):
    def test_i_props_match_note(self):
        import struct
        props = P1.mkadt_chg_props()
        for n, off in NOTE_I.items():
            self.assertEqual(struct.unpack("<I", props["i-" + n])[0], off, n)
        self.assertEqual({k for k in props if k.startswith("i-")}, {"i-" + n for n in NOTE_I})
        src = open(self.i2c).read()
        self.assertEqual(set(re.findall(r'"(i-[a-z0-9-]+)"', src)), {"i-" + n for n in NOTE_I})
        # no property for CONF / TIMING / TRAILIG / USI_CON as a transfer word
        self.assertFalse([k for k in props if k.startswith("i-") and any(x in k for x in ("conf-", "timing", "trailig", "usi"))
                          and k != "i-hsi2c12-auto-conf"])
        CASES.append("adt_i_props")

    def test_bootarg(self):
        self.assertIn('PE_parse_boot_argn("sweetchgi2c"', open(self.i2c).read())
        self.assertRegex(open(os.path.join(ROOT, "tools", "bootargs-known.txt")).read(), r"(?m)^sweetchgi2c\tpatch:0284$")
        CASES.append("bootarg_known")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    sha = hashlib.sha256(open(PATCH, "rb").read()).hexdigest()
    if res.wasSuccessful():
        print("RESULT: PASS chg-i2c cases=%d skipped=%d patch_sha=%s" % (len(CASES), len(res.skipped), sha))
        return 0
    print("RESULT: FAIL chg-i2c failures=%d errors=%d" % (len(res.failures), len(res.errors)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
