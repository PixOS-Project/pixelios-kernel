#!/usr/bin/env python3
"""Host tests for xnu/patches/0283-sweet-chg-probe.patch (charger phase 1: READ-ONLY witness of the MAX77759 I2C bus state). No phone, no kernel build.

    python3 tools/test_chg_probe.py        -> `RESULT: PASS chg-probe cases=N patch_sha=<sha256 of the patch>` / `RESULT: FAIL ...`

What it proves (each is a unittest below):
  1. the patch applies (git apply --check) on the files it changes in the current stack, when a built tree is available (skipped loudly otherwise);
  2. bsd/dev/sweet_chg.c compiled on the host (-DSWEET_CHG_HOST, -Wall -Wextra -Werror) runs the flow against a fake register file (tools/chg_host_test.c):
     the HSI2C12 window is read ONLY when chg_gate_open() says open (AP-113, the main safety property; an exhaustive 1024-combination sweep against an
     independent oracle), never RX_DATA/TX_DATA, every read is on the independent allowlist, opt-out and missing node read nothing;
  3. the KERNEL branch of the same file compiled against XNU stubs (tools/chg_glue_test.c) type-checks `const volatile` and, fed the /sweet-chg properties
     that tools/mkadt.py really generated, reads the expected words and, with the gated window PROT_NONE, never touches it;
  4. NO HARDWARE STORE: a text lint of the new code (no assignment through the mapping, no `volatile` without `const`, the one dereference, no write
     helper names) plus mutants: a store added to the source makes the glue compile or this lint fail;
  5. the ADT node: window and word values equal the note's addresses (independent copy here); the node exists for bluejay only;
  6. the boot-arg sweetchgprobe is parsed with the literal name and is listed in tools/bootargs-known.txt.
"""
import glob, hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PATCH = os.path.join(ROOT, "xnu", "patches", "0283-sweet-chg-probe.patch")
CASES = []

# Independent copy of docs/research/charger-max77759-gs101.md (sections 1.1-1.4, 5.1): window base/size and word offsets
NOTE_WINDOWS = {
    "hsi2c12": (0x10D50000, 0x1000), "sysreg-peric1": (0x10C21000, 0x1000), "cmu-peric1": (0x10C00000, 0x10000),
    "cmu-top": (0x1E080000, 0x10000), "gpp23": (0x10C40060, 0x10),
}
NOTE_WORDS = {
    "cmu-usi12-mux": 0x650, "cmu-usi12-div": 0x1810, "cmu-usi12-gate-ipclk": 0x2030, "cmu-usi12-gate-pclk": 0x2050, "cmu-usi12-gate-rstn": 0x206C,
    "cmu-usi12-qch": 0x3030, "top-ip-mux": 0x10E8, "top-ip-div": 0x18E0, "top-ip-gate": 0x2104, "sysreg-usi12-conf": 0x10,
    "hsi2c12-ctl": 0x00, "hsi2c12-int-status": 0x24, "hsi2c12-fifo-status": 0x30, "hsi2c12-conf": 0x40, "hsi2c12-trans-status": 0x50,
    "hsi2c12-timing-fs1": 0x60, "hsi2c12-timing-fs2": 0x64, "hsi2c12-timing-fs3": 0x68, "hsi2c12-usi-con": 0xC4,
    "gpp23-con": 0x0, "gpp23-dat": 0x4, "gpp23-pud": 0x8,
}


def run(*argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def patch_text():
    return open(PATCH).read()


def extract(path):
    """the new file `path` from the patch (the '+' lines of its hunk)"""
    p = patch_text()
    i = p.index("+++ b/%s\n" % path)
    body = p[i:].split("\n", 2)[2]
    j = body.find("\ndiff --git ")
    if j >= 0:
        body = body[:j]
    return "\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n"


def mkadt_chg_props(board="bluejay"):
    from tools import adt, mkadt
    root = mkadt.build(seed=b"\0" * 64, board=board)
    node = mkadt.add_chg_node(root, board)
    if node is None:
        return None
    root = adt.parse(adt.serialize(root))[0]
    for c in root.children:
        props = dict((k, v) for k, _f, v in c.props)
        if props.get("name") == b"sweet-chg\0":
            return props
    return None



def _newest_tree_with(marker):
    """The newest $LOCALBUILD/work/<label>/xnu whose bsd/dev/sweet_usb.c contains MARKER, else the path that is missing (reported by the caller)."""
    lb = os.environ.get("LOCALBUILD") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "localbuild")
    for f in sorted(glob.glob(os.path.join(lb, "work", "*", "xnu", "bsd", "dev", "sweet_usb.c")), key=os.path.getmtime, reverse=True):
        try:
            if marker in open(f, errors="replace").read():
                return os.path.dirname(os.path.dirname(os.path.dirname(f)))
        except OSError:   # lint-silent: expected a tree pruned while we scan: the next one is tried
            continue
    return os.path.join(lb, "work", "<label>", "xnu")

class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chg-probe-")
        cls.src = os.path.join(cls.tmp, "sweet_chg.c")
        open(cls.src, "w").write(extract("bsd/dev/sweet_chg.c"))
        inc = os.path.join(cls.tmp, "inc")
        for h in ("sys/param.h", "sys/systm.h", "kern/clock.h", "mach/mach_time.h", "machine/machine_routines.h", "pexpert/pexpert.h",
                  "pexpert/device_tree.h", "libkern/libkern.h"):
            os.makedirs(os.path.dirname(os.path.join(inc, h)), exist_ok=True)
            open(os.path.join(inc, h), "w").close()
        cls.inc = inc

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build_flow(self, src=None, out="flow"):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DSWEET_CHG_HOST", '-DCHG_SRC="%s"' % (src or self.src), "-I", os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "tools", "chg_host_test.c"), "-o", exe)
        return r, exe

    def build_glue(self, src=None, out="glue"):
        exe = os.path.join(self.tmp, out)
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DCHG_SRC="%s"' % (src or self.src), "-I", self.inc,
                os.path.join(ROOT, "tools", "chg_glue_test.c"), "-o", exe)
        return r, exe


class PatchShape(Base):
    def test_files_and_hook(self):
        p = patch_text()
        self.assertIn("+++ b/bsd/dev/sweet_chg.c", p)
        self.assertIn("+bsd/dev/sweet_chg.c\t\t\tstandard", p)
        self.assertIn("+extern void sweet_chg_tick(int log_pending);", p)
        self.assertEqual(len(re.findall(r"^\+\s+sweet_chg_tick\(sweet_log_position\(\) > pos\);", p, re.M)), 1)
        self.assertEqual(len(re.findall(r"^diff --git ", p, re.M)), 3)
        CASES.append("patch_shape")

    def test_applies_to_built_tree(self):
        # CHG_STACK_TREE (the offload gate mounts its own build), else the newest local build tree that carries 0279, as test_chg_icl.default_tree:
        # the hardcoded wlan-rejoin tree was pruned (2026-11-11/12) and the skip failed every local formal gate. R13: missing = FAIL, never a skip
        tree = os.environ.get("CHG_STACK_TREE") or _newest_tree_with("sweet_stall_tick")
        self.assertTrue(os.path.isfile(os.path.join(tree, "bsd/dev/sweet_usb.c")) and "sweet_stall_tick" in open(os.path.join(tree, "bsd/dev/sweet_usb.c")).read(),
                        "no XNU tree patched through 0279 at %s: set CHG_STACK_TREE or build one (tools/xnu-local-build); never skipped (R13)" % tree)
        t = os.path.join(self.tmp, "applytree")
        for f in ("bsd/conf/files", "bsd/dev/sweet_usb.c"):
            os.makedirs(os.path.dirname(os.path.join(t, f)), exist_ok=True)
            shutil.copy(os.path.join(tree, f), os.path.join(t, f))
        r = run("git", "-C", t, "init", "-q")
        if os.path.isfile(os.path.join(tree, "bsd/dev/sweet_chg.c")):
            # 2026-11-11 wlan/rejoin formal gate: a tree built from the whole stack already carries 0283 and later patches that touch the same
            # files (0284+), so neither a forward nor a reverse apply fits. Check instead that 0283 is IN the tree: its build-list row, its tick
            # declaration and call in sweet_usb.c, and its boot-arg in sweet_chg.c (tools/stack-apply proves the whole stack applies)
            files = open(os.path.join(tree, "bsd/conf/files")).read()
            usb = open(os.path.join(tree, "bsd/dev/sweet_usb.c")).read()
            chg = open(os.path.join(tree, "bsd/dev/sweet_chg.c")).read()
            self.assertRegex(files, r"(?m)^bsd/dev/sweet_chg\.c\s+standard$")
            self.assertIn("extern void sweet_chg_tick(int log_pending);", usb)
            self.assertRegex(usb, r"sweet_chg_tick\(sweet_log_position\(\) > pos\);")
            self.assertIn('PE_parse_boot_argn("sweetchgprobe"', chg)
            CASES.append("apply_check")
            return
        r = run("git", "-C", t, "apply", "--check", PATCH)
        self.assertEqual(r.returncode, 0, r.stderr)
        CASES.append("apply_check")


class HostFlow(Base):
    def test_all_cases_pass(self):
        r, exe = self.build_flow()
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(exe)
        res = dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", r.stdout, re.M))
        self.assertGreaterEqual(len(res), 40, r.stdout)
        bad = {k: v for k, v in res.items() if v != "ok"}
        self.assertEqual(bad, {}, r.stdout[-3000:])
        self.assertIn("SUMMARY fails=0", r.stdout)
        CASES.extend("flow:" + k for k in res)


class KernelGlue(Base):
    def dt_file(self):
        props = mkadt_chg_props()
        self.assertIsNotNone(props)
        path = os.path.join(self.tmp, "chg.dt")
        with open(path, "w") as f:
            for k, v in props.items():
                if k.endswith("-reg") or k.startswith("w-"):
                    f.write("%s %s\n" % (k, v.hex()))
        return path

    def run_glue(self, mode):
        r, exe = self.build_glue()
        self.assertEqual(r.returncode, 0, r.stderr)
        return run(exe, self.dt_file(), mode)

    def test_open_reads_expected(self):
        r = self.run_glue("open")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        o = r.stdout
        for needle in ("SWEET CHG P1: start", "usi12_mux[0x650]=0x10 usi12_div[0x1810]=0x0 gate_ipclk[0x2030]=0x200000", "qch[0x3030]=0x3f0002",
                       "cmu_top ip_mux[0x10e8]=0x1 ip_div[0x18e0]=0x0 ip_gate[0x2104]=0x200000", "SW_CONF[0x10]=0x4",
                       "gpp23 CON[0x0]=0x220000 DAT[0x4]=0xdead0064 PUD[0x8]=0x330000", "hsi2c12 clock check: open", "ipclk 400000 kHz implied",
                       "hsi2c12 USI_CON[0xc4]=0xc0de00c4", "CTL[0x0]=0xdead0000", "CONF[0x40]=0xdead0040", "FIFO_STATUS[0x30]=0xdead0030",
                       "TIMING_FS1[0x60]=0xdead0060", "TIMING_FS3[0x68]=0x210000", "TRANS_STATUS[0x50]=0xdead0050", "INT_STATUS[0x24]=0xdead0024",
                       "CLK_DIV=33 -> SCL 735294 Hz", "done reads=22"):
            self.assertIn(needle, o)
        self.assertNotIn("0xdead0038", o)       # RX_DATA is never read
        CASES.append("glue_open")

    def test_closed_never_touches_gated_window(self):
        # patch the fake so the gate closes: PCLK gate word = 0 (via a mutated copy of the harness table)
        h = open(os.path.join(ROOT, "tools", "chg_glue_test.c")).read()
        h2 = h.replace("case 0x10C02030: case 0x10C02050: case 0x10C0206C: return 0x200000;",
                       "case 0x10C02030: case 0x10C0206C: return 0x200000;\n\tcase 0x10C02050: return 0;")
        self.assertNotEqual(h, h2)
        p = os.path.join(self.tmp, "glue_closed.c")
        open(p, "w").write(h2)
        exe = os.path.join(self.tmp, "glue_closed")
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DCHG_SRC="%s"' % self.src, "-I", self.inc, p, "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(exe, self.dt_file(), "closed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)      # a touch of the PROT_NONE window would be SIGSEGV -> exit 3
        self.assertIn("SWEET CHG GAP: hsi2c12 clock gated, block not read (", r.stdout)
        self.assertIn("gate_pclk=0x0", r.stdout)
        self.assertNotIn("USI_CON[", r.stdout)
        CASES.append("glue_closed")

    def test_optout_and_missing_node(self):
        r = self.run_glue("off")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("disabled by sweetchgprobe=0, nothing read", r.stdout)
        self.assertIn("GLUE nmaps=0", r.stdout)
        r = self.run_glue("nonode")
        self.assertIn("SWEET CHG GAP: no /sweet-chg ADT node", r.stdout)
        self.assertIn("GLUE nmaps=0", r.stdout)
        CASES.append("glue_optout_nonode")


class NoStore(Base):
    """AP-113 sibling: zero hardware stores. Lint of the new code plus mutants that must be caught."""

    @staticmethod
    def lint(text):
        bad = []
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)      # string literals
        # 1. no assignment through the mapping array or a pointer deref of it
        if re.search(r"\bchg_va\s*\[[^\]]*\]\s*\[[^\]]*\]\s*(=|\|=|&=|\^=|\+=|-=|<<=|>>=)(?!=)", code):
            bad.append("store through chg_va[][]")
        if re.search(r"\*\s*\(?\s*(\(\s*(volatile|uint32_t)[^)]*\)\s*)?chg_va\b[^;]*\)?\s*=(?!=)", code):
            bad.append("store through *chg_va")
        # 2. every `volatile` is a `const volatile`
        for m in re.finditer(r"\bvolatile\b", code):
            if not re.search(r"\bconst\s+$", code[max(0, m.start() - 8):m.start()]) and not re.search(r"\bconst\s*$", code[:m.start()][-10:]):
                bad.append("volatile without const")
        # 3. chg_va is dereferenced exactly once (chg_hw_rd32) and assigned exactly once (chg_load)
        uses = re.findall(r"\bchg_va\b", code)
        if len(uses) != 3:      # declaration, the assignment in chg_load, the load in chg_hw_rd32
            bad.append("chg_va used %d times (want 3: declaration, chg_load, chg_hw_rd32)" % len(uses))
        if not re.search(r"return\s+chg_va\[win\]\[off\s*/\s*4\];", code):
            bad.append("the single load is not `return chg_va[win][off / 4];`")
        # 4. no write-ish helpers or other mapping calls
        for pat in (r"\bsmc\w*\(", r"\bwritel\b", r"\bOSWrite\w*", r"\bwr32\b", r"\b\w*_wr\s*\(", r"\bsweet_pmu_write\b",
                    r"\bml_phys_write\w*", r"\bwritel_relaxed\b", r"\b__asm__?\b", r"\bstr\s+[wx]\d"):
            if re.search(pat, code):
                bad.append("write-ish token /%s/" % pat)
        if len(re.findall(r"\bml_io_map\b", code)) != 2:     # the extern declaration and the one call in chg_map
            bad.append("ml_io_map used outside chg_map")
        # 5. chg_hw_rd32 is called only from chg_rd
        calls = [m.start() for m in re.finditer(r"\bchg_hw_rd32\s*\(", code)]
        if len(calls) != 3:     # prototype, definition (kernel), the call in chg_rd
            bad.append("chg_hw_rd32 appears %d times (want 3)" % len(calls))
        return bad

    def test_lint_clean(self):
        self.assertEqual(self.lint(open(self.src).read()), [])
        CASES.append("nostore_lint_clean")

    def test_mutants_are_caught(self):
        base = open(self.src).read()
        muts = {
            "store_in_chg_hw": ("return chg_va[win][off / 4];", "chg_va[win][off / 4] = 0; return chg_va[win][off / 4];"),
            "drop_const": ("static const volatile uint32_t *chg_va[CHG_W_N];", "static volatile uint32_t *chg_va[CHG_W_N];"),
            "second_load_path": ("chg_wsize[w] = (uint32_t)reg[1];", "chg_wsize[w] = (uint32_t)reg[1]; (void)chg_va[w][0];"),
            "or_store": ("return chg_va[win][off / 4];", "chg_va[win][off / 4] |= 1u; return chg_va[win][off / 4];"),
        }
        for name, (a, b) in muts.items():
            self.assertIn(a, base, name)
            m = base.replace(a, b, 1)
            caught = bool(self.lint(m))
            if not caught:
                path = os.path.join(self.tmp, "mut_%s.c" % name)
                open(path, "w").write(m)
                r, _ = self.build_glue(src=path, out="mut_" + name)
                caught = r.returncode != 0
            self.assertTrue(caught, "mutant %s not caught" % name)
        # the language itself: a store through the const volatile mapping does not compile
        path = os.path.join(self.tmp, "mut_compile.c")
        open(path, "w").write(base.replace("return chg_va[win][off / 4];", "chg_va[win][off / 4] = 0; return chg_va[win][off / 4];", 1))
        r, _ = self.build_glue(src=path, out="mut_compile")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("read-only", r.stderr)
        CASES.append("nostore_mutants")

    def test_gate_mutants_are_caught_by_the_flow(self):
        base = open(self.src).read()
        muts = [
            ("(s->w[CHG_R_GP] & CHG_GATE_CGVAL) == 0 || (s->w[CHG_R_GP] & CHG_GATE_AUTO) != 0", "0"),
            ("(s->w[CHG_R_QCH] & CHG_QCH_REQ) == 0", "0"),
            ("(s->w[CHG_R_MUX] & CHG_MUX_BUSY) != 0) {\n\t\t*why = \"USI12 user mux busy\";\n\t\treturn 0;\n\t}\n\tif ((s->w[CHG_R_GIP]", "0) {\n\t\t*why = \"USI12 user mux busy\";\n\t\treturn 0;\n\t}\n\tif ((s->w[CHG_R_GIP]"),
            ("if (!chg_gate) {                /* defence in depth", "if (0) {                /* defence in depth"),
            ("(s->w[CHG_R_MUX] & CHG_MUX_SEL) != 0 && (s->w[CHG_R_TGATE] & CHG_GATE_CGVAL) == 0", "0"),
        ]
        for i, (a, b) in enumerate(muts):
            self.assertIn(a, base, "mutant %d anchor" % i)
            path = os.path.join(self.tmp, "gm%d.c" % i)
            open(path, "w").write(base.replace(a, b, 1))
            r, exe = self.build_flow(src=path, out="gm%d" % i)
            if r.returncode != 0:
                continue        # does not compile = caught
            out = run(exe)
            self.assertNotEqual(out.returncode, 0, "gate mutant %d survived:\n%s" % (i, out.stdout[-500:]))
        CASES.append("gate_mutants")


class Adt(Base):
    def test_node_matches_note(self):
        import struct
        props = mkadt_chg_props()
        self.assertIsNotNone(props)
        for name, (base, size) in NOTE_WINDOWS.items():
            self.assertEqual(props[name + "-reg"], struct.pack("<QQ", base, size), name)
        for name, off in NOTE_WORDS.items():
            self.assertEqual(struct.unpack("<I", props["w-" + name])[0], off, name)
        extra = {k for k in props if k.startswith("w-")} - {"w-" + n for n in NOTE_WORDS}
        self.assertEqual(extra, set())
        # nothing in the node is writable by construction: no 'write', 'wr-' or mask property
        self.assertFalse([k for k in props if "write" in k or k.startswith("wr-")])
        CASES.append("adt_node")

    def test_only_bluejay(self):
        from tools import boards
        for b in boards.BOARDS:
            got = mkadt_chg_props(b)
            self.assertEqual(got is not None, b == "bluejay", b)
        CASES.append("adt_bluejay_only")

    def test_driver_property_names_exist_in_node(self):
        props = mkadt_chg_props()
        src = open(self.src).read()
        names = set(re.findall(r'"(w-[a-z0-9-]+|[a-z0-9-]+-reg)"', src))
        self.assertEqual(names, {k for k in props if k.startswith("w-") or k.endswith("-reg")})
        CASES.append("adt_names_match_driver")


class BootArg(Base):
    def test_literal_name_and_known(self):
        self.assertIn('PE_parse_boot_argn("sweetchgprobe"', open(self.src).read())
        known = open(os.path.join(ROOT, "tools", "bootargs-known.txt")).read()
        self.assertRegex(known, r"(?m)^sweetchgprobe\tpatch:0283$")
        CASES.append("bootarg_known")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    sha = hashlib.sha256(open(PATCH, "rb").read()).hexdigest()
    if res.wasSuccessful():
        print("RESULT: PASS chg-probe cases=%d skipped=%d patch_sha=%s" % (len(CASES), len(res.skipped), sha))
        return 0
    print("RESULT: FAIL chg-probe failures=%d errors=%d" % (len(res.failures), len(res.errors)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
