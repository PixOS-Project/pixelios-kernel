#!/usr/bin/env python3
"""Host tests for xnu/patches/0287-sweet-chg-icl.patch (charger phase 3: boot-arg sweetchgicl=900, CNFG_09 CHGIN_ILIM 500 -> 900 mA, THE ONLY CHIP WRITE). No phone, no build.

    python3 tools/test_chg_icl.py  -> `RESULT: PASS chg-icl cases=N patch_sha=<sha256>` / `RESULT: FAIL ...`

Proves (evidence for the rule-8 reviewer):
  1. 0287 applies on the 0284 source and on sweet_usb.c of the built tree; the combined sweet_chg_i2c.c builds (-Wall -Wextra -Werror) in the host model and against XNU stubs;
  2. a fake HSI2C12 + chip model (tools/chg_icl_host_test.c) records every byte the chips receive: the ONLY chip write on the whole boot is [0x69 <- 0xC2, 0xA3]
     (NO_AUTOIBUS kept), the only other write is the single restore of the old value after a read-back mismatch; the write needs phase-2 OK, a configured SuperSpeed link,
     CHGTYP SDP/CDP, CNFG_00 MODE 4/5, ILIM code 4..0x22 and the open phase-1 gate; every refusal writes nothing; a NACKed write is not retried;
  3. the guard inside the single chip-write function (i2c_cnfg09_write) refuses every other (cur, value) pair on its own, independent of the callers;
  4. text lint of the chip-write call sites plus mutants (each removes one precondition or guard; a build error is an ERROR, a wall-clock timeout is not a kill);
  5. boot-arg sweetchgicl is parsed with its literal name and listed in tools/bootargs-known.txt.
"""
import glob
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
import test_chg_i2c as P2  # noqa: E402

PATCH = os.path.join(ROOT, "xnu", "patches", "0287-sweet-chg-icl.patch")
_LB = os.environ.get("LOCALBUILD") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "localbuild")   # as tools/xnu-local-build


def default_tree():
    """CHG_STACK_TREE = the patched tree of this run's own XNU build (the offload-host gate sets it). Unset: the newest local build tree; none = the path that is missing, reported by the caller."""
    t = os.environ.get("CHG_STACK_TREE")
    if t:
        return t
    c = sorted(glob.glob(os.path.join(_LB, "work", "*", "xnu", "bsd", "dev", "sweet_usb.c")), key=os.path.getmtime, reverse=True)
    return os.path.dirname(os.path.dirname(os.path.dirname(c[0]))) if c else os.path.join(_LB, "work", "<label>", "xnu")
CASES = []
run = P2.run


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chg-icl-")
        t = os.path.join(cls.tmp, "tree", "bsd", "dev")
        os.makedirs(t)
        # sweet_chg_i2c.c as patched by 0284 (extracted from that patch), then 0287 on top
        p = open(P2.PATCH).read()
        i = p.index("+++ b/bsd/dev/sweet_chg_i2c.c\n")
        body = p[i:].split("\n", 2)[2]
        j = body.find("\ndiff --git ")
        body = body[:j] if j >= 0 else body
        open(os.path.join(t, "sweet_chg_i2c.c"), "w").write("\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n")
        run("git", "-C", os.path.join(cls.tmp, "tree"), "init", "-q")
        cls.apply = run("git", "-C", os.path.join(cls.tmp, "tree"), "apply", "--include=bsd/dev/sweet_chg_i2c.c", PATCH)
        cls.src = os.path.join(t, "sweet_chg_i2c.c")
        inc = os.path.join(cls.tmp, "inc")
        for h in ("sys/param.h", "sys/systm.h", "kern/clock.h", "mach/mach_time.h", "machine/machine_routines.h", "pexpert/pexpert.h", "pexpert/device_tree.h", "libkern/libkern.h"):
            os.makedirs(os.path.dirname(os.path.join(inc, h)), exist_ok=True)
            open(os.path.join(inc, h), "w").close()
        cls.inc = inc

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def flow(self, src=None, out="icl", extra=()):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", *extra, "-DSWEET_CHG_I2C_HOST", '-DCHG_I2C_SRC="%s"' % (src or self.src), "-I", os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "tools", "chg_icl_host_test.c"), "-o", exe)
        return r, exe


class Apply(Base):
    def test_applies(self):
        self.assertEqual(self.apply.returncode, 0, self.apply.stderr)
        p = open(PATCH).read()
        self.assertEqual(len(re.findall(r"^diff --git ", p, re.M)), 2)
        self.assertIn("+sweet_usb_ss_configured(void)", p)
        full, r = self.usb_check(default_tree())
        self.assertEqual(r.returncode, 0, ("0287 %s check on the built tree failed: " % ("reverse (stack already has it)" if full else "forward (stack lacks it)")) + r.stderr)
        CASES.append("apply_usb_check")
        CASES.append("apply")

    def usb_check(self, tree):
        """(full_stack, git apply --check result) of 0287 on tree's bsd/dev/sweet_usb.c: reverse when the file already has 0287 (the full stack), forward when not.
        A missing tree FAILS (R13/rule 7), it never drops the case."""
        f = os.path.join(tree, "bsd", "dev", "sweet_usb.c")
        self.assertTrue(os.path.isfile(f), "no built XNU tree: %s is missing. CHG_STACK_TREE must be the patched tree gate-xnu-build left (the gate sets it); never skipped" % f)
        full = "sweet_usb_ss_configured(void)" in open(f).read()
        u = tempfile.mkdtemp(prefix="u", dir=self.tmp)
        os.makedirs(os.path.join(u, "bsd", "dev"))
        shutil.copy(f, os.path.join(u, "bsd", "dev"))
        run("git", "-C", u, "init", "-q")
        return full, run("git", "-C", u, "apply", *(["-R"] if full else []), "--check", "--include=bsd/dev/sweet_usb.c", PATCH)

    @staticmethod
    def fixture(post):
        """A stand-in sweet_usb.c made from the hunks of 0287 itself: context + removed lines (before) or context + added lines (post = the full stack)."""
        p = open(PATCH).read()
        i = p.index("+++ b/bsd/dev/sweet_usb.c\n")
        body = p[i:].split("\n", 1)[1]
        j = body.find("\ndiff --git ")
        body = body[:j] if j >= 0 else body
        out = []
        for l in body.split("\n"):
            if l.startswith("@@"):
                out += ["/* filler %d */" % k for k in range(40)]
            elif l[:1] in (" ", "-", "+") and not l.startswith(("+++", "---")):
                if l[0] == " " or (l[0] == "+") == post:
                    out.append(l[1:])
        return "\n".join(out) + "\n"

    def tree_of(self, post):
        t = tempfile.mkdtemp(prefix="t", dir=self.tmp)
        os.makedirs(os.path.join(t, "bsd", "dev"))
        open(os.path.join(t, "bsd", "dev", "sweet_usb.c"), "w").write(self.fixture(post))
        return t

    def test_usb_check_full_stack_reverses(self):
        full, r = self.usb_check(self.tree_of(True))
        self.assertTrue(full)
        self.assertEqual(r.returncode, 0, r.stderr)
        CASES.append("usb_check_reverse")

    def test_usb_check_pre_0287_applies_forward(self):
        full, r = self.usb_check(self.tree_of(False))
        self.assertFalse(full)
        self.assertEqual(r.returncode, 0, r.stderr)
        CASES.append("usb_check_forward")

    def test_usb_check_wrong_direction_fails(self):
        t = self.tree_of(True)   # full stack, but force a forward check: must NOT apply (the check is real)
        u = os.path.join(self.tmp, "wd")
        shutil.copytree(t, u)
        run("git", "-C", u, "init", "-q")
        self.assertNotEqual(run("git", "-C", u, "apply", "--check", "--include=bsd/dev/sweet_usb.c", PATCH).returncode, 0)
        CASES.append("usb_check_wrong_direction")

    def test_missing_tree_fails_loudly(self):
        with self.assertRaises(AssertionError) as c:
            self.usb_check(os.path.join(self.tmp, "no-such-tree"))
        self.assertIn("no built XNU tree", str(c.exception))
        self.assertIn("no-such-tree", str(c.exception))
        CASES.append("usb_check_missing_tree")


class Flow(Base):
    def test_cases(self):
        r, exe = self.flow()
        self.assertEqual(r.returncode, 0, r.stderr)
        o = run(exe)
        res = dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M))
        self.assertGreaterEqual(len(res), 45, o.stdout[-2000:])
        self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {}, o.stdout[-3500:])
        self.assertIn("SUMMARY fails=0", o.stdout)
        CASES.extend("flow:" + k for k in res)


class Glue(Base):
    def test_kernel_branch_default_and_p2_refused(self):
        exe = os.path.join(self.tmp, "glue")
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DCHG_I2C_SRC="%s"' % self.src, "-I", self.inc, os.path.join(ROOT, "tools", "chg_i2c_glue_test.c"), "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = os.path.join(self.tmp, "chg.dt")
        with open(d, "w") as f:
            for k, v in P1.mkadt_chg_props().items():
                if k.endswith("-reg") or k.startswith(("w-", "i-")):
                    f.write("%s %s\n" % (k, v.hex()))
        o = run(exe, d, "default")
        self.assertEqual(o.returncode, 0, o.stdout)
        self.assertIn("SWEET CHG P3: off (default; sweetchgicl=900", o.stdout)
        self.assertIn("GLUE nmaps=0 xfers=0 nwr=0", o.stdout)
        o = run(exe, d, "on-gate-closed")      # HSI2C12 mapped PROT_READ: a store of phase 2/3 would SIGSEGV
        self.assertEqual(o.returncode, 0, o.stdout)
        self.assertIn("SWEET CHG GAP: P3 refused: phase 2 did not finish with 0 errors", o.stdout)
        self.assertIn("nwr=0", o.stdout)
        CASES.append("glue_p3")


class Lint(Base):
    @staticmethod
    def lint(text):
        bad = []
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
        def calls(name):
            out = []
            for m in re.finditer(r"\b%s\s*\(" % name, code):
                depth, k = 1, m.end()
                while depth and k < len(code):
                    depth += (code[k] == "(") - (code[k] == ")")
                    k += 1
                out.append(re.sub(r"\s+", "", code[m.end():k - 1]))
            return out

        # the chip-write calls: exactly the 900 mA step and the restore (the definition line "uint8_t cur, uint8_t val" is not a call)
        cw = sorted(c for c in calls("i2c_cnfg09_write") if not c.startswith("uint8_t"))
        if cw != sorted(["cur,val", "p3_intended,p3_orig", "p3_intended,p3_orig"]):
            bad.append("chip-write call sites: %r" % cw)
        wv = sorted(c for c in calls("p3_write_verify") if not c.startswith("uint8_t"))
        if wv != sorted(["c9,(uint8_t)((c9&0x80u)|ICL_CODE),&r9", "c9,(uint8_t)((c9&0x80u)|ICL500_CODE),&r9"]):
            bad.append("p3_write_verify call sites: %r" % wv)
        msgs = calls("i2c_msg")
        if "0x69,ICL_REG,0,1,2,b,NULL" not in msgs:
            bad.append("the chip write message is not i2c_msg(0x69, ICL_REG, 0, 1, 2, b, NULL)")
        if len([m for m in msgs if m.split(",")[4:5] == ["2"]]) != 1:
            bad.append("a second 2-byte write message exists")
        if not re.search(r"#define ICL_CODE\s+0x22u", text) or not re.search(r"#define ICL_REG\s+0xc2u", text):
            bad.append("ICL_CODE / ICL_REG constants differ from 0x22 / 0xc2")
        if re.search(r"\b0xbf\b|\b0xBF\b|CHGPROT|CNFG_06", code.replace("C(0xbf)", "").replace("chg_i2c_read(0x69, 0xbf, 1, &c6)", "")):      # C(0xbf) and the preflight read are READS of CNFG_06
            bad.append("CNFG_06 / CHGPROT touched in code")
        # TX_DATA only from the buffer of the message
        txw = [c for c in calls("i2c_wr") if c.startswith("I_TXD,")]
        if txw != ["I_TXD,txb[got]"]:
            bad.append("TX_DATA writes: %r" % txw)
        if len(re.findall(r"\bi2c_hw_wr32\s*\(", code)) != 3:
            bad.append("i2c_hw_wr32 call count")
        # restore only on a mismatch, once
        if code.count("p3_nwrites == 1") != 1 or code.count("p3_nwrites == 0") != 2:
            bad.append("restore / first / down guards missing")
        if not re.search(r"#define ICL500_CODE\s+0x13u", text):
            bad.append("ICL500_CODE differs from 0x13")
        return bad

    def test_clean(self):
        self.assertEqual(self.lint(open(self.src).read()), [])
        CASES.append("lint_clean")

    def test_lint_mutants(self):
        base = open(self.src).read()
        muts = {
            "extra_call": ("static void\np3_done(const char *why)", "static void\nzz(void) { (void)i2c_cnfg09_write(0x93, 0xa2); }\n\nstatic void\np3_done(const char *why)"),
            "other_const": ("#define ICL_CODE        0x22u", "#define ICL_CODE        0x3fu"),
            "other_reg": ("#define ICL_REG         0xc2u", "#define ICL_REG         0xc3u"),
            "chgprot": ("b[0] = (uint8_t)ICL_REG;", "b[0] = (uint8_t)ICL_REG; /* x */ (void)0xbf;"),
            "tx_literal": ("if (i2c_wr(I_TXD, txb[got]) != 0) {", "if (i2c_wr(I_TXD, 0x55) != 0) {"),
        }
        for n, (a, b) in muts.items():
            self.assertIn(a, base, n)
            self.assertTrue(self.lint(base.replace(a, b, 1)), "lint missed %s" % n)
        CASES.append("lint_mutants")

    def test_flow_mutants(self):
        base = open(self.src).read()
        muts = [
            ("if (spd == 0) {\n\t\treturn p3_refuse(", "if (0) {\n\t\treturn p3_refuse("),
            ("if ((bc & 3u) != 1u && (bc & 3u) != 2u) {", "if (0) {"),
            ("if ((c0 & 0xfu) != 4u && (c0 & 0xfu) != 5u) {", "if (0) {"),
            ("if ((c9 & 0x7fu) < 4u) {", "if (0) {"),
            ("if ((c9 & 0x7fu) >= ICL_CODE) {", "if (0) {"),
            ("if (!i2c_gate(0)) {\n\t\treturn p3_refuse", "if (0) {\n\t\treturn p3_refuse"),
            ("if (!i2c_p2_ok) {", "if (0) {"),
            ("if (arg != ICL_ARG_UP && arg != ICL_ARG_DOWN) {", "if (0) {"),
            ("(uint8_t)((c9 & 0x80u) | ICL_CODE)", "(uint8_t)ICL_CODE"),                                    # drop the NO_AUTOIBUS keep (the guard then refuses)
            ("if (v == 0) {\n\t\tint w = i2c_cnfg09_write", "if (0) {\n\t\tint w = i2c_cnfg09_write"),   # no read-back compare
            ("if (!first && !restore && !down) {", "if (0) {"),
            ("p3_nwrites == 1 && p3_orig_valid && val == p3_orig && cur == p3_intended;", "p3_nwrites >= 1 && p3_orig_valid;"),
            ("i2c_dead = 1;\n\t\tp3_done(\"mismatch\");", "p3_done(\"mismatch\");"),
            ("if (p3_samples >= P3_SAMPLES) {", "if (p3_samples >= 2 * P3_SAMPLES) {"),
            ("p3_next_ms += P3_SAMPLE_MS;", "p3_next_ms += 2 * P3_SAMPLE_MS;"),
            ("	if (!i2c_gate(0)) {\n\t\tp3_done(\"gate\");", "	if (0) {\n\t\tp3_done(\"gate\");"),
            ("autov = (rd ? AUTO_RW : 0u) | ((rd || stop) ? AUTO_STOP : 0u) | (len & 0xffffu);", "autov = (rd ? AUTO_RW : 0u) | (rd ? AUTO_STOP : 0u) | (len & 0xffffu);"),
            ("b[1] = val;", "b[1] = (uint8_t)(val | 0x40);"),
            # review chg-0287 F2 / F3 / F4
            ("if (!sweet_usb_ss_configured()) {\n\t\tp3_lost(", "if (0) {\n\t\tp3_lost("),
            ("if (ct == 0) {\n\t\tp3_lost(\"TCPC CHGTYP (source no longer SDP/CDP)\");\n\t} else", "if (0) {\n\t\tp3_lost(\"TCPC CHGTYP (source no longer SDP/CDP)\");\n\t} else"),
            ("if (ct == 0) {\n\t\t\t\tp3_lost(", "if (0) {\n\t\t\t\tp3_lost("),
            ("p3_stage = 3;\n\t\t\t}", "p3_stage = 9;\n\t\t\t}"),
            ("if (c9 != p3_intended) {", "if (0) {"),
            ("(cur & 0x80u) != 0 && val == (uint8_t)((cur & 0x80u) | ICL_CODE)", "val == (uint8_t)((cur & 0x80u) | ICL_CODE)"),
            ("if ((c9 & 0x80u) == 0) {", "if (0) {"),
            ("if ((c9 & 0x7fu) != ICL_CODE) {\n\t\ti2c_log(\"SWEET CHG P3: CNFG_09=0x%x", "if (0) {\n\t\ti2c_log(\"SWEET CHG P3: CNFG_09=0x%x"),
            ("if (!i2c_gate(0)) {\n\t\t(void)p3_refuse(", "if (0) {\n\t\t(void)p3_refuse("),
            ("if ((C(0xc2) & 0x7fu) > 0x13u) {", "if (0) {"),
            ("code == ICL_CODE && val == (uint8_t)((cur & 0x80u) | ICL500_CODE);", "code >= 4u && val == (uint8_t)((cur & 0x80u) | ICL500_CODE);"),
        ]
        for i, (a, b) in enumerate(muts):
            self.assertIn(a, base, "mutant %d anchor" % i)
            path = os.path.join(self.tmp, "m%d.c" % i)
            open(path, "w").write(base.replace(a, b, 1) if i != 8 else base.replace(a, b))
            r, exe = self.flow(src=path, out="m%d" % i, extra=("-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-but-set-variable"))
            self.assertEqual(r.returncode, 0, "mutant %d does not build (ERROR, not a kill): %s" % (i, r.stderr[:400]))
            o = run(exe)
            self.assertNotEqual(o.returncode, 124, "mutant %d only hit the wall-clock timeout" % i)
            self.assertNotEqual(o.returncode, 0, "flow mutant %d survived: %s" % (i, a[:70]))
            self.assertRegex(o.stdout, r"CASE \w+ FAIL", "mutant %d exit code without a failing CASE" % i)
        CASES.append("flow_mutants")


class UsbPower(Base):
    """review chg-0287 F1/F2/F7: the sweet_usb.c part of the patch"""

    def blocks(self):
        p = open(PATCH).read()
        out = {}
        for name in ("power", "accessor"):
            m = re.search(r"^\+/\* BEGIN 0287 %s \*/\n(.*?)^\+/\* END 0287 %s \*/" % (name, name), p, re.M | re.S)
            self.assertIsNotNone(m, name)
            out[name] = "\n".join(l[1:] for l in m.group(1).split("\n") if l.startswith("+")) + "\n"
        return out, p

    def test_descriptor_byte_and_accessor(self):
        b, p = self.blocks()
        paths = {}
        for k, v in b.items():
            paths[k] = os.path.join(self.tmp, "usb_%s.c" % k)
            open(paths[k], "w").write(v)
        exe = os.path.join(self.tmp, "usbpow")
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DPOWER_C="%s"' % paths["power"], '-DACCESSOR_C="%s"' % paths["accessor"],
                os.path.join(ROOT, "tools", "chg_usb_power_test.c"), "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        for arg in ("absent", "900", "500", "901"):
            o = run(exe, arg)
            self.assertEqual(o.returncode, 0, "%s: %s" % (arg, o.stdout))
            self.assertIn("SUMMARY fails=0", o.stdout)
        # the 0173 array is not edited (no removed line), the byte is overridden in the served copy
        usb_part = p[p.index("diff --git a/bsd/dev/sweet_usb.c"):]
        self.assertFalse([l for l in usb_part.split("\n") if l.startswith("-") and not l.startswith("---")])
        self.assertEqual(len(re.findall(r"^\+\s+data\[8\] = ud_ss_power_byte\(\);", p, re.M)), 1)
        self.assertEqual(len(re.findall(r"^\+\s+__atomic_store_n\(&ud\.ss_bmax_sent, data\[8\], __ATOMIC_RELEASE\);", p, re.M)), 1)
        CASES.append("usb_power_byte_accessor")

    def test_flag_lifecycle_in_patch_text(self):
        _, p = self.blocks()
        # cleared in exactly three places (bus reset, disconnect, restart), set once in ud_configure with the descriptor condition
        self.assertEqual(len(re.findall(r"^\+\s+__atomic_store_n\(&ud\.ss_power_ok, false, __ATOMIC_RELEASE\);", p, re.M)), 3)
        self.assertEqual(len(re.findall(r"^\+\s+__atomic_store_n\(&ud\.ss_bmax_sent, 0, __ATOMIC_RELEASE\);", p, re.M)), 3)
        self.assertIn("__atomic_store_n(&ud.ss_power_ok, ud.ss_mode && ud.speed >= DSTS_SPEED_SS && (uint32_t)ud.ss_bmax_sent * 8u >= 896u, __ATOMIC_RELEASE);", p)
        for ctx in ("SWEET USB: bus reset", "SWEET USB: disconnect", "ud.configured = false;"):
            self.assertRegex(p, re.escape(ctx) + r"[^\n]*\n(?:[^\n]*\n)?\+\s+__atomic_store_n\(&ud\.ss_power_ok, false")
        # mutants of the accessor / flag logic are caught by the C harness
        b, _ = self.blocks()
        muts = [("bm * 8u >= 896u", "bm * 8u >= 496u"), ("ok && sp >= DSTS_SPEED_SS", "sp >= DSTS_SPEED_SS"), ("bool ok = __atomic_load_n(&ud.ss_power_ok, __ATOMIC_ACQUIRE) && ", "bool ok = "),
                ("sp >= DSTS_SPEED_SS && bm", "bm")]
        for i, (a, c) in enumerate(muts):
            self.assertIn(a, b["accessor"], a)
            pa = os.path.join(self.tmp, "ma%d.c" % i)
            open(pa, "w").write(b["accessor"].replace(a, c, 1))
            pw = os.path.join(self.tmp, "mp%d.c" % i)
            open(pw, "w").write(b["power"])
            exe = os.path.join(self.tmp, "mu%d" % i)
            r = run("gcc", "-Wall", "-Wextra", "-O1", '-DPOWER_C="%s"' % pw, '-DACCESSOR_C="%s"' % pa, os.path.join(ROOT, "tools", "chg_usb_power_test.c"), "-o", exe)
            self.assertEqual(r.returncode, 0, "accessor mutant %d does not build: %s" % (i, r.stderr[:200]))
            self.assertNotEqual(run(exe, "900").returncode, 0, "accessor mutant %d survived" % i)
        pm = b["power"].replace("(uint8_t)UD_SS_BMAXPOWER_ICL : (uint8_t)UD_SS_BMAXPOWER_DEFAULT", "(uint8_t)UD_SS_BMAXPOWER_ICL : (uint8_t)UD_SS_BMAXPOWER_ICL")
        self.assertNotEqual(pm, b["power"])
        pp = os.path.join(self.tmp, "mpow.c")
        open(pp, "w").write(pm)
        pa = os.path.join(self.tmp, "macc.c")
        open(pa, "w").write(b["accessor"])
        exe = os.path.join(self.tmp, "mpowexe")
        r = run("gcc", "-Wall", "-Wextra", "-O1", '-DPOWER_C="%s"' % pp, '-DACCESSOR_C="%s"' % pa, os.path.join(ROOT, "tools", "chg_usb_power_test.c"), "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr[:200])
        self.assertNotEqual(run(exe, "absent").returncode, 0, "default boot would declare 896 mA")
        CASES.append("usb_flag_lifecycle")


class BootArg(Base):
    def test_literal_and_known(self):
        self.assertIn('PE_parse_boot_argn("sweetchgicl"', open(self.src).read())
        self.assertRegex(open(os.path.join(ROOT, "tools", "bootargs-known.txt")).read(), r"(?m)^sweetchgicl\tpatch:0287$")
        CASES.append("bootarg_known")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase
    s = unittest.TestSuite()
    for c in (Apply, Flow, Glue, Lint, UsbPower, BootArg):
        s.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(c))
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(s)
    sha = hashlib.sha256(open(PATCH, "rb").read()).hexdigest()
    if res.wasSuccessful():
        print("RESULT: PASS chg-icl cases=%d skipped=%d patch_sha=%s" % (len(CASES), len(res.skipped), sha))
        return 0
    print("RESULT: FAIL chg-icl failures=%d errors=%d" % (len(res.failures), len(res.errors)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
