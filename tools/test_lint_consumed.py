#!/usr/bin/env python3
"""tools/lint-consumed (gate step consumed, user R48 row 2): fixture trees for every verdict, the real CLM state, the real tree.
Run: python3 -I tools/test_lint_consumed.py   ($LINT_CONSUMED_BIN points the rows at a scratch copy of the lint: mutant runs, AP-303 only inside unshare)"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent
REPO = TOOLS.parent
LINT = os.environ.get("LINT_CONSUMED_BIN") or str(TOOLS / "lint-consumed")

# the packing lines of the real tools/pack_wlanfw.py (TAGS line and the __main__ list), the consumers of FW/NVRM as patches 0130/0131 add them, and the generic 0128 print
WLANFW = """import struct
TAGS = (b'FW  ', b'NVRM', b'CLM ')
MAGIC = b'WLANFWB1'
def pack(blobs):
    struct.pack_into('<4sIII', out, 0, tag, 1, 2, 3)
    return b''
if __name__ == '__main__':
    blob = pack([(t, open(p, 'rb').read()) for t, p in ((b'FW  ', a.fw), (b'NVRM', a.nvram), (b'CLM ', a.clm))])
"""
MKPAYLOAD = """def add_ramdisk_to_adt(adt_blob, phys, size, name='RAMDisk'):
    return adt_blob
def build():
    return add_ramdisk_to_adt(1, 2, 3, 'WLANFW')
"""
MKDEV = "MAGIC, VERSION = b'SWEETDEV', 1\n"
BASE = {"tools/pack_wlanfw.py": WLANFW, "tools/mkpayload.py": MKPAYLOAD, "tools/mkdevoverlay.py": MKDEV, "tools/lint-consumed-allow.txt": ""}


def patch(*added, file="x.c"):
    """A patch with one hunk of ADDED lines (a leading '-' or ' ' keeps the line removed/context); the hunk counts are exact."""
    ls = [(ln if ln[:1] in "- " else "+" + ln) for ln in added]
    old = sum(1 for ln in ls if ln[0] in "- ")
    new = sum(1 for ln in ls if ln[0] in "+ ")
    return "--- a/%s\n+++ b/%s\n@@ -1,%d +1,%d @@\n" % (file, file, old, new) + "".join(ln + "\n" for ln in ls)


OTHERS = patch('if (memcmp(buf, "WLANFWB1", 8) != 0) return;', 'if (memcmp(sw_tag, "SWEETDEV", 8) != 0) return;', 'dt = SecureDTGetProperty(mm, "WLANFW", &p, &sz);', 'd = mm->getProperty("RAMDisk");')
FW = patch('		if (memcmp(e[i].tag, "FW  ", 4) == 0) {')
NV = patch('		if (memcmp(e[i].tag, "NVRM", 4) == 0) {')
CLM = patch('		if (memcmp(e[i].tag, "CLM ", 4) == 0) {')
GENERIC = patch('	printf("SWEET WLAN: fw container entry %.4s %u ok\\n", e[i].tag, e[i].length);')


def make(d, files):
    for rel, text in files.items():
        p = pathlib.Path(d, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def run(files, base=BASE, extra=()):
    with tempfile.TemporaryDirectory() as d:
        make(d, {**base, **files})
        p = subprocess.run([sys.executable, "-I", LINT, "--root", d, *extra], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


def tree(**over):
    """The all-consumed tree; over= replaces/adds files (key with __ for / and _ for .: see TREE names)."""
    return {"xnu/patches/0001-others.patch": OTHERS, "xnu/patches/0130-fw.patch": FW, "xnu/patches/0131-nv.patch": NV, "xnu/patches/0185-clm.patch": CLM, **over}


class Verdicts(unittest.TestCase):
    def passes(self, files, **kw):
        rc, out = run(files, **kw)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"RESULT: PASS consumed packers=3 tags=7 unconsumed=0 ")
        return out

    def fails(self, files, needle, **kw):
        rc, out = run(files, **kw)
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        self.assertRegex(out, r"RESULT: FAIL consumed ")
        return out

    def test_all_consumed_passes(self):
        out = self.passes(tree())
        self.assertIn("CONSUMED tools/pack_wlanfw.py 'CLM ' by xnu/patches/0185-clm.patch:4", out)

    def test_tag_nobody_consumes_fails_with_file_line_and_do_instead(self):
        out = self.fails({k: v for k, v in tree().items() if "clm" not in k}, "UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '")
        self.assertIn("add a gap log naming it", out)
        self.assertIn("tools/lint-consumed-allow.txt", out)
        self.assertIn("hits=2", out)
        self.assertIn("unconsumed=1", out)

    def test_consumer_in_a_shim_source_passes(self):
        files = {k: v for k, v in tree().items() if "clm" not in k}
        files["shim/wlanfw.c"] = 'int f(const char *t) {\n\treturn memcmp(t, "CLM ", 4) == 0;\n}\n'
        self.passes(files)

    def test_consumer_by_hex_value_passes(self):
        files = {k: v for k, v in tree().items() if "clm" not in k}
        files["shim/a.h"] = "switch (tag) {\ncase 0x204d4c43: use(); break;\n}\n"      # 'CLM ' little endian
        self.passes(files)
        files["shim/a.h"] = "switch (tag) {\ncase 0x434c4d20: use(); break;\n}\n"      # big endian
        self.passes(files)

    def test_gap_log_naming_the_tag_passes(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        out = self.passes({**base, "xnu/patches/0185-gap.patch": patch('	swl_gap("fw", "CLM packed but not loaded (no clmload yet)", "high");')})
        self.assertIn("gap log xnu/patches/0185-gap.patch:4", out)
        self.passes({**base, "xnu/patches/0185-gap.patch": patch('	printf("SWEET GAP: tag CLM is packed, never loaded\\n");')})
        self.passes({**base, "shim/g.c": 'void f(void) { sweet_note_ignored("fw", "CLM", 1); }\n'})

    def test_gap_log_without_the_tag_or_tag_without_gap_log_fails(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        self.fails({**base, "xnu/patches/0185-gap.patch": patch('	swl_gap("fw", "txcap not loaded", "low");')}, "UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '")
        self.fails({**base, "xnu/patches/0185-gap.patch": patch('	printf("fw CLM packed but not loaded\\n");')}, "UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '")
        self.fails({**base, "shim/g.c": 'void f(void) { swl_gap("fw", "CLMX is something else", 1); }\n'}, "tag 'CLM '")

    def test_things_that_are_not_consumers(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        for label, extra in (
                ("host test source", {"shim/wlan_fw_host_test.c": 'CHECK(memcmp(e[2].tag, "CLM ", 4) == 0);\n'}),
                ("C comment", {"shim/a.c": '/* if (memcmp(t, "CLM ", 4) == 0) */\n// memcmp(t, "CLM ", 4)\nint x;\n'}),
                ("table without a compare", {"shim/a.c": 'static const char *tag[3] = { "FW  ", "NVRM", "CLM " };\n'}),
                ("printf naming the quoted tag", {"shim/a.c": 'void f(void) { printf("CLM "); }\n'}),
                ("removed patch line", {"xnu/patches/0185-x.patch": patch('-		if (memcmp(e[i].tag, "CLM ", 4) == 0) {')}),
                ("context patch line", {"xnu/patches/0185-x.patch": patch(' 		if (memcmp(e[i].tag, "CLM ", 4) == 0) {')}),
                ("patch comment lines (ADDED lines, AP-327)", {"xnu/patches/0185-x.patch": patch('/*', '* if (memcmp(t, "CLM ", 4) == 0) here', '*/')}),
                ("patch line comment", {"xnu/patches/0185-x.patch": patch('// if (memcmp(t, "CLM ", 4) == 0)', 'int x; /* memcmp(t, "CLM ", 4) == 0 */')}),
                ("literal and compare in different statements", {"shim/a.c": 'static const char *n = "CLM ";\nint f(int x) { return x == 1; }\n'}),
                ("wrong padding", {"shim/a.c": 'int f(char *t) { return memcmp(t, "CLM", 3) == 0; }\n'}),
                # AP-327 / review r1 F1: the compare must be ABOUT the literal
                ("S1 unrelated != next to a printf of the tag", {"shim/a.c": 'void f(int rc) { if (rc != 0) printf("CLM "); }\n'}),
                ("S2 name table selected by an unrelated compare", {"shim/a.c": 'const char *f(int x) { return x == 2 ? "CLM " : "FW  "; }\n'}),
                ("S3 #if 0 code", {"shim/dead.c": '#if 0\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n#endif\n'}),
                ("S3b #if 1 ... #else dead branch", {"shim/dead.c": '#if 1\nint x;\n#else\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n#endif\n'}),
                ("S3c dead code inside an added patch hunk", {"xnu/patches/0185-x.patch": patch("#if 0", '	if (memcmp(t, "CLM ", 4) == 0) {', "#endif")}),
                ("S4 selftest source", {"shim/wlan_fw_selftest.c": 'int f(char *b) { return memcmp(b, "CLM ", 4) == 0; }\n'}),
                ("S4b fixture and mock sources", {"shim/clm_fixture.c": 'int f(char *b) { return memcmp(b, "CLM ", 4) == 0; }\n', "shim/mock_wlan.h": 'static int f(char *b) { return memcmp(b, "CLM ", 4) == 0; }\n'}),
                ("S8 (void) discards the result", {"shim/a.c": 'void f(char *t) { (void)memcmp(t, "CLM ", 4); }\n'}),
                ("S8b bare compare statement", {"shim/a.c": 'void f(char *t) { memcmp(t, "CLM ", 4); }\n'}),
                ("S11 define of the tag and an unrelated compare", {"shim/a.h": '#define T_CLM "CLM "\n#define IS_CLM(t) ((t) == T_CLM)\n'}),
                ("S17 compare and the tag as printf arguments", {"shim/a.c": 'void f(int a, int b) { printf("%d %s", a == b, "CLM "); }\n'}),
                ("S18 macro definition naming the tag is not a gap log", {"shim/a.h": '#define swl_gap(a, m, s) printf("GAP: CLM %s\\n", m)\n'}),
                ("M17 tag only inside a longer literal", {"shim/a.c": 'void f(int rc) { if (rc != 0) printf("the CLM blob"); }\n'}),
                ("literal compared in a call that is not a compare", {"shim/a.c": 'int f(void) { return strlen("CLM ") == 4; }\n'}),
                ("literal nested in another call inside memcmp", {"shim/a.c": 'int f(char *t) { return memcmp(t, mk("CLM "), 4) == 0; }\n'}),
                ("tag only as the first argument of an unrelated compare", {"shim/a.c": 'int f(int n) { return memcmp(b, c, n) == 0 && n == 4; } const char *t = "CLM ";\n'})):
            with self.subTest(label):
                self.fails({**base, **extra}, "UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '")

    def test_gap_logs_must_name_the_tag_in_a_string_of_the_gap_call(self):
        nofw = {k: v for k, v in tree().items() if "0130" not in k}
        for label, extra in (("S10 prose word FW in a gap string", {"shim/g.c": 'void f(void) { swl_gap("wifi", "no FW update available", "low"); }\n'}),
                             ("tag word outside the gap call string", {"shim/g.c": 'void f(void) { swl_gap("wifi", "x", "low"); int FW = 1; }\n'}),
                             ("tag word in a different statement", {"shim/g.c": 'void f(void) { swl_gap("wifi", "x", "low"); puts("FW  "); }\n'}),
                             ("macro definition of swl_gap", {"shim/g.h": '#define swl_gap(a, m, s) puts("FW   GAP:")\n'})):
            with self.subTest(label):
                self.fails({**nofw, **extra}, "UNCONSUMED tools/pack_wlanfw.py:2 tag 'FW  '")
        for extra in ({"shim/g.c": 'void f(void) { swl_gap("wifi", "FW  never loaded", "low"); }\n'},
                      {"shim/g.c": 'void f(void) { swl_miss("wifi", "FW  never loaded"); }\n'},
                      {"shim/g.c": 'void f(void) { puts("GAP: " "FW  never loaded"); }\n'}):
            self.passes({**nofw, **extra})

    def test_real_consumer_shapes_pass(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        for label, extra in (("memcmp ==", {"shim/a.c": 'int f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n'}),
                             ("!memcmp", {"shim/a.c": 'int f(char *t) { if (!memcmp(t, "CLM ", 4)) return 1; return 0; }\n'}),
                             ("bcmp", {"shim/a.c": 'int f(char *t) { return !bcmp(t, "CLM ", 4); }\n'}),
                             ("!strncmp", {"shim/a.c": 'int f(char *t) { if (!strncmp(t, "CLM ", 4)) return 1; return 0; }\n'}),
                             ("strcmp without ==", {"shim/a.c": 'int f(char *t) { if (strcmp(t, "CLM ")) return 0; return 1; }\n'}),
                             ("strcasecmp", {"shim/a.c": 'int f(char *t) { return !strcasecmp(t, "CLM "); }\n'}),
                             ("DTGetProperty", {"shim/a.c": 'int f(void *m) { if (DTGetProperty(m, "CLM ", &p, &n) != 0) return 1; return 0; }\n'}),
                             ("tag as the first argument", {"shim/a.c": 'int f(char *t) { return strcmp("CLM ", t) == 0; }\n'}),
                             ("cast on the literal", {"shim/a.c": 'int f(char *t) { return memcmp(t, (const char *)"CLM ", 4) == 0; }\n'}),
                             ("== operand of the literal", {"shim/a.c": 'int f(const char *t) { return t == "CLM "; }\n'}),
                             ("hex ==", {"shim/a.c": 'int f(unsigned t) { return t == 0x204d4c43; }\n'}),
                             ("hex != reversed", {"shim/a.c": 'int f(unsigned t) { return 0x204d4c43U != t; }\n'}),
                             ("adjacent literals", {"shim/a.c": 'int f(char *t) { return memcmp(t, "CL" "M ", 4) == 0; }\n'}),
                             ("adjacent literals over a line", {"shim/a.c": 'int f(char *t) { return memcmp(t, "CL"\n\t"M ", 4) == 0; }\n'}),
                             ("tag behind a #define", {"shim/a.c": '#define T_CLM "CLM "\nint f(char *t) { return memcmp(t, T_CLM, 4) == 0; }\n'}),
                             ("tag behind a #define with continuation", {"shim/a.h": '#define T_CLM \\\n\t"CLM "\nint f(char *t) { return memcmp(t, T_CLM, 4) == 0; }\n'}),
                             ("shim subdirectory", {"shim/sub/a.c": 'int f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n'}),
                             ("#if 1 live branch", {"shim/a.c": '#if 1\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n#endif\n'}),
                             ("#if 0 then #else live branch", {"shim/a.c": '#if 0\nint x;\n#else\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n#endif\n'}),
                             ("#ifdef branch", {"shim/a.c": '#ifdef X\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n#endif\n'}),
                             ("compare spread over lines", {"shim/a.c": 'int f(char *t) {\n\treturn memcmp(t,\n\t\t"CLM ",\n\t\t4) == 0;\n}\n'}),
                             ("tag in a getProperty on an object", {"shim/a.cpp": 'void f(IORegistryEntry *r) { d = r->getProperty("CLM "); if (d) use(d); }\n'}),
                             ("macro name alone does not hide a later use", {"shim/a.h": '#define IS(t) 1\n#ifdef X\n#endif\nint f(char *t) { return memcmp(t, "CLM ", 4) == 0; }\n'})):
            with self.subTest(label):
                out = self.passes({**base, **extra})
                self.assertIn("CONSUMED tools/pack_wlanfw.py 'CLM ' by ", out)

    def test_consumer_cites_the_line_of_the_literal(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        out = self.passes({**base, "shim/a.c": '#define T_CLM \\\n\t"CLM "\n\nint f(char *t) {\n\treturn memcmp(t,\n\t\tT_CLM, 4) == 0;\n}\n'})
        self.assertIn("'CLM ' by shim/a.c:6", out)

    def test_unterminated_comment_in_one_hunk_does_not_hide_the_next(self):
        base = {k: v for k, v in tree().items() if "clm" not in k}
        two = "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+/* a comment the hunk context closes\n@@ -9,0 +9,1 @@\n+	if (memcmp(t, \"CLM \", 4) == 0) {\n"
        self.passes({**base, "xnu/patches/0185-x.patch": two})

    def test_adt_name_and_magic_packers(self):
        files = tree()
        files["xnu/patches/0001-others.patch"] = patch('if (memcmp(buf, "WLANFWB1", 8) != 0) return;', 'if (memcmp(sw_tag, "SWEETDEV", 8) != 0) return;', 'd = mm->getProperty("RAMDisk");')
        self.fails(files, "UNCONSUMED tools/mkpayload.py:4 tag 'WLANFW'")
        files["xnu/patches/0001-others.patch"] = patch('if (memcmp(buf, "WLANFWB1", 8) != 0) return;', 'dt = SecureDTGetProperty(mm, "WLANFW", &p, &sz);', 'd = mm->getProperty("RAMDisk");')
        self.fails(files, "UNCONSUMED tools/mkdevoverlay.py:1 tag 'SWEETDEV'")

    def test_uncovered_packers_are_named_not_failed(self):
        out = self.passes({**tree(), "tools/mkrec.py": "import struct\nx = struct.pack('<4sI', b'ABCD', 1)\n", "tools/test_mkrec.py": "import struct\nstruct.pack('<4sI', b'A', 1)\n"})
        self.assertIn("NOT COVERED tools/mkrec.py", out)
        self.assertNotIn("test_mkrec", out)
        self.assertIn("notcovered=1", out)       # only mkrec.py: the registered packers' own struct.pack lines do not count

    def test_list_prints_the_parsed_tags(self):
        rc, out = run(tree(), extra=("--list",))
        self.assertEqual(rc, 0, out)
        for s in ("tools/pack_wlanfw.py:2 'FW  ' hits=2", "'CLM ' hits=2", "tools/mkpayload.py:1 'RAMDisk' hits=1", "tools/mkpayload.py:4 'WLANFW' hits=1", "'SWEETDEV' hits=1"):
            self.assertIn(s, out)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_unreadable_packer_is_error_exit_2_in_both_modes_without_a_traceback(self):      # F9
        for extra in ((), ("--list",)):
            with tempfile.TemporaryDirectory() as d:
                make(d, {**BASE, **tree()})
                os.chmod(os.path.join(d, "tools/pack_wlanfw.py"), 0)
                p = subprocess.run([sys.executable, "-I", LINT, "--root", d, *extra], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("RESULT: ERROR consumed", p.stdout)
            self.assertNotIn("Traceback", p.stderr)

    def test_usage_is_exit_2(self):
        p = subprocess.run([sys.executable, "-I", LINT, "--bogus"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("RESULT: ERROR consumed usage", p.stdout)


class Stack(unittest.TestCase):
    """The patch stack is read as one: hunk counts decide what is a line, a later patch's removal cancels an earlier add (F2/F9, AP-327)."""
    NOCLM = {k: v for k, v in tree().items() if "clm" not in k}
    CMP = '	if (memcmp(e[i].tag, "CLM ", 4) == 0) {'

    def test_added_line_of_a_non_code_file_is_not_a_consumer(self):           # S5
        rc, out = run({**self.NOCLM, "xnu/patches/0185-doc.patch": patch(self.CMP, file="README.md")})
        self.assertEqual(rc, 1, out)
        self.assertIn("UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '", out)
        rc, out = run({**self.NOCLM, "xnu/patches/0185-doc.patch": patch(self.CMP, file="bsd/conf/files")})
        self.assertEqual(rc, 1, out)

    def test_header_like_added_lines_are_code_not_file_headers(self):         # F9: `+++n` is an added line `++n; ...`
        out = run({**self.NOCLM, "xnu/patches/0185-x.patch": patch('++n; if (memcmp(t, "CLM ", 4) == 0) {')})
        self.assertEqual(out[0], 0, out[1])
        self.assertIn("by xnu/patches/0185-x.patch:4", out[1])

    def test_later_patch_removing_the_consumer_cancels_it(self):               # F2 / S6
        rc, out = run({**self.NOCLM, "xnu/patches/0185-add.patch": patch(self.CMP), "xnu/patches/0186-del.patch": patch("-" + self.CMP)})
        self.assertEqual(rc, 1, out)
        self.assertIn("UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '", out)

    def test_remove_then_readd_cites_the_third_patch(self):
        rc, out = run({**self.NOCLM, "xnu/patches/0185-add.patch": patch(self.CMP), "xnu/patches/0186-del.patch": patch("-" + self.CMP),
                       "xnu/patches/0187-add.patch": patch(self.CMP)})
        self.assertEqual(rc, 0, out)
        self.assertIn("'CLM ' by xnu/patches/0187-add.patch:4", out)

    def test_removal_in_another_file_or_of_other_text_cancels_nothing(self):
        rc, out = run({**self.NOCLM, "xnu/patches/0185-add.patch": patch(self.CMP), "xnu/patches/0186-del.patch": patch("-" + self.CMP, file="y.c")})
        self.assertEqual(rc, 0, out)
        rc, out = run({**self.NOCLM, "xnu/patches/0185-add.patch": patch(self.CMP), "xnu/patches/0186-del.patch": patch("-	if (x) {")})
        self.assertEqual(rc, 0, out)

    def test_removal_in_an_earlier_numbered_patch_does_not_cancel_a_later_add(self):
        rc, out = run({**self.NOCLM, "xnu/patches/0184-del.patch": patch("-" + self.CMP), "xnu/patches/0185-add.patch": patch(self.CMP)})
        self.assertEqual(rc, 0, out)

    def test_malformed_hunks_are_findings_not_skipped(self):
        short = "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,3 @@\n+int a;\n@@ -9,0 +9,1 @@\n+int b;\n"
        rc, out = run({**self.NOCLM, "xnu/patches/0185-x.patch": short})
        self.assertEqual(rc, 1, out)
        self.assertIn("PATCH-PARSE xnu/patches/0185-x.patch:5 hunk ended before its counts", out)
        stray = "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+int a;\n+" + self.CMP + "\n"
        rc, out = run({**self.NOCLM, "xnu/patches/0185-x.patch": stray})
        self.assertEqual(rc, 1, out)
        self.assertIn("PATCH-PARSE xnu/patches/0185-x.patch:5 `+` line outside any hunk", out)


class Packers(unittest.TestCase):
    def test_missing_packer_is_a_finding(self):
        rc, out = run({}, base={k: v for k, v in BASE.items() if "mkdevoverlay" not in k})
        self.assertEqual(rc, 1, out)
        self.assertIn("PACKER-MISSING tools/mkdevoverlay.py", out)

    def test_packer_that_yields_no_tag_is_a_finding(self):
        rc, out = run(tree(**{"tools/pack_wlanfw.py": "TAGS = compute()\n"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("PACKER-EMPTY tools/pack_wlanfw.py:1", out)

    def test_unparseable_packer_is_a_finding(self):
        rc, out = run(tree(**{"tools/pack_wlanfw.py": "TAGS = (\n"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("PACKER-UNPARSEABLE tools/pack_wlanfw.py", out)

    def test_new_tag_in_the_packer_is_seen_without_touching_the_lint(self):
        rc, out = run(tree(**{"tools/pack_wlanfw.py": WLANFW.replace("b'CLM ')", "b'CLM ', b'TXCP')", 1)}))
        self.assertEqual(rc, 1, out)
        self.assertIn("tag 'TXCP'", out)

    def test_tags_differing_only_in_padding_are_ambiguous(self):
        rc, out = run(tree(**{"tools/pack_wlanfw.py": WLANFW.replace("b'CLM ')", "b'CLM ', b'CLM')", 1)}))
        self.assertEqual(rc, 1, out)
        self.assertIn("AMBIGUOUS-TAG tools/pack_wlanfw.py:2 `CLM`", out)

    def test_docstring_mentions_are_not_packing_sites(self):
        src = '"""CLM """\n' + WLANFW.replace("def pack(blobs):\n", "def pack(blobs):\n    \"\"\"CLM \"\"\"\n")
        rc, out = run({**tree(), "tools/pack_wlanfw.py": src, "xnu/patches/0185-clm.patch": ""}, extra=("--list",))
        self.assertIn("'CLM ' hits=2", out)


class Unresolved(unittest.TestCase):
    """Every packer element the extractor cannot resolve is a finding (review r1 F3), never a silent drop."""
    def bad(self, files, needle):
        rc, out = run(tree(**files))
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        self.assertRegex(out, r"RESULT: FAIL consumed ")

    def test_tag_container_shapes(self):
        base = "import struct\nMORE = (b'CLM ',)\n"
        for label, src, needle in (
                ("starred", base + "TAGS = (b'FW  ', b'NVRM', *MORE)\nMAGIC = b'WLANFWB1'\n", "PACKER-UNRESOLVED tools/pack_wlanfw.py:3 TAGS is not a str/bytes literal (Starred)"),
                ("encode call", "TAGS = (b'FW  ', b'NVRM', 'CLM '.encode())\nMAGIC = b'WLANFWB1'\n", "PACKER-UNRESOLVED tools/pack_wlanfw.py:1 TAGS is not a str/bytes literal (Call)"),
                ("augmented assign", "TAGS = (b'FW  ', b'NVRM')\nTAGS += (b'CLM ',)\nMAGIC = b'WLANFWB1'\n", "PACKER-UNRESOLVED tools/pack_wlanfw.py:2 TAGS is not a str/bytes literal (AugAssign"),
                ("name", "X = b'CLM '\nTAGS = (b'FW  ', b'NVRM', X)\nMAGIC = b'WLANFWB1'\n", "(Name)"),
                ("tuple concatenation", "TAGS = (b'FW  ',) + (b'NVRM', b'CLM ')\nMAGIC = b'WLANFWB1'\n", "(BinOp)"),
                ("unaligned unpack", "TAGS, X = (b'FW  ', b'NVRM'), 1, 2\nMAGIC = b'WLANFWB1'\n", "tuple unpack the lint cannot align"),
                ("whole value a call", "TAGS = compute()\nMAGIC = b'WLANFWB1'\n", "PACKER-EMPTY tools/pack_wlanfw.py:1")):
            with self.subTest(label):
                self.bad({"tools/pack_wlanfw.py": src}, needle)

    def test_a_second_magic_or_tag_constant_must_be_registered(self):
        self.bad({"tools/mkdevoverlay.py": MKDEV + "MAGIC2 = b'SWEETDV2'\n"}, "PACKER-UNREGISTERED tools/mkdevoverlay.py:2 constant MAGIC2")
        self.bad({"tools/pack_wlanfw.py": WLANFW + "TAG_X = b'ZZZZ'\n"}, "PACKER-UNREGISTERED tools/pack_wlanfw.py:")
        rc, out = run(tree(**{"tools/pack_wlanfw.py": WLANFW + "TAG_X = b'ZZZZ'\n"}))
        self.assertEqual(out.count("PACKER-UNREGISTERED"), 1, out)       # two PACKERS rows share the file: one report

    def test_adt_name_shapes(self):
        helper = MKPAYLOAD.split("def build")[0]
        for label, build, needle in (
                ("variable name", "def build(n):\n    return add_ramdisk_to_adt(1, 2, 3, n)\n", "PACKER-UNRESOLVED tools/mkpayload.py:4 add_ramdisk_to_adt is not a str/bytes literal (Name)"),
                ("name= variable", "def build(n):\n    return add_ramdisk_to_adt(1, 2, 3, name=n)\n", "(Name)"),
                ("kwargs", "def build(k):\n    return add_ramdisk_to_adt(1, 2, 3, **k)\n", "**kwargs"),
                ("args", "def build(a):\n    return add_ramdisk_to_adt(*a)\n", "*args"),
                ("alias", "def build():\n    fn = add_ramdisk_to_adt\n    return fn(1, 2, 3, 'WLANFW')\n", "used as a value (alias or callback)"),
                ("callback", "def build(f):\n    return f(add_ramdisk_to_adt)\n", "used as a value (alias or callback)"),
                ("second helper", "def add_prop_to_adt(a, k, v):\n    return a\ndef build():\n    return add_ramdisk_to_adt(1, 2, 3, 'WLANFW')\n", "PACKER-UNREGISTERED tools/mkpayload.py:3 function add_prop_to_adt")):
            with self.subTest(label):
                self.bad({"tools/mkpayload.py": helper + build}, needle)

    def test_resolved_shapes_still_pass(self):
        helper = MKPAYLOAD.split("def build")[0]
        for label, files in (
                ("annotated tag assignment", {"tools/pack_wlanfw.py": WLANFW.replace("TAGS = ", "TAGS: tuple = ")}),
                ("attribute call with a literal name", {"tools/mkpayload.py": helper + "def build(mp):\n    return mp.add_ramdisk_to_adt(1, 2, 3, 'WLANFW')\n"}),
                ("name= keyword literal", {"tools/mkpayload.py": helper + "def build():\n    return add_ramdisk_to_adt(1, 2, 3, name='WLANFW')\n"})):
            with self.subTest(label):
                rc, out = run(tree(**files))
                self.assertEqual(rc, 0, out)
                self.assertRegex(out, r"tags=7 ")        # the tag stayed visible (a dropped keyword or attribute call would lose WLANFW)


def pin_of(files, tag="CLM "):
    """`hits=N sha=H` of the tag as `--list` prints it for this tree (the pin a reviewer would write)."""
    rc, out = run(files, extra=("--list",))
    return re.search(r"%r hits=(\d+) sha=([0-9a-f]{8})" % tag, out).expand(r"hits=\1 sha=\2")


class Allowlist(unittest.TestCase):
    PIN = pin_of(tree())
    ROW = "tools/pack_wlanfw.py  CLM  %s  wifi: fix-21 21y, KNOWN-GAPS-WIFI X-W7\n" % PIN

    def noclm(self, allow=None, **extra):
        files = {k: v for k, v in tree().items() if "clm" not in k}
        files.update(extra)
        if allow is not None:
            files["tools/lint-consumed-allow.txt"] = allow
        return files

    def test_real_clm_state_fails_without_its_row_and_passes_with_it(self):
        """The real state: pack_wlanfw packs FW/NVRM/CLM, 0130/0131 compare FW/NVRM, 0128 prints the entry generically (no CLM consumer, no gap log)."""
        files = self.noclm(**{"xnu/patches/0128-fwcheck.patch": GENERIC})
        rc, out = run(files)
        self.assertEqual(rc, 1, out)
        self.assertIn("UNCONSUMED tools/pack_wlanfw.py:2 tag 'CLM '", out)
        self.assertEqual(out.count("UNCONSUMED"), 1, out)
        rc, out = run(self.noclm(self.ROW, **{"xnu/patches/0128-fwcheck.patch": GENERIC}))
        self.assertEqual(rc, 0, out)
        self.assertIn("allowed=1", out)
        self.assertIn("ALLOWED tools/pack_wlanfw.py 'CLM '", out)

    def test_pin_grew_and_shrunk(self):
        self.assertIn("hits=2 ", self.ROW)
        rc, out = run(self.noclm(self.ROW.replace("hits=2", "hits=1")))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-GREW tools/lint-consumed-allow.txt:1", out)
        rc, out = run(self.noclm(self.ROW.replace("hits=2", "hits=3")))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-SHRUNK tools/lint-consumed-allow.txt:1", out)

    def test_a_second_packing_site_of_an_allowed_tag_grows_the_hits(self):
        rc, out = run(self.noclm(self.ROW, **{"tools/pack_wlanfw.py": WLANFW + "other = (b'CLM ', 1)\n"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-GREW", out)

    def test_pin_is_a_fingerprint_not_only_a_count(self):                  # F7 / AP-300
        """Same literal count, other text: swapped site, edited site, second literal on one line."""
        same = self.noclm(self.ROW)
        for label, packer, needle in (
                ("site swapped for another call", WLANFW.replace("(b'CLM ', a.clm)", "(b'CLM ', a.clm2)"), "ALLOW-CHANGED"),
                ("TAGS line edited", WLANFW.replace("TAGS = (", "TAGS = (  # edited\n    ", 1).replace("b'CLM ')", "b'CLM ')", 1), "ALLOW-CHANGED"),
                ("a site removed, another added elsewhere", WLANFW.replace(", (b'CLM ', a.clm))", ")") + "other = (b'CLM ', 1)\n", "ALLOW-CHANGED"),
                ("second literal on the same line", WLANFW.replace("(b'CLM ', a.clm))", "(b'CLM ', a.clm), (b'CLM ', a.clm2))"), "ALLOW-GREW")):
            with self.subTest(label):
                rc, out = run({**same, "tools/pack_wlanfw.py": packer})
                self.assertEqual(rc, 1, out)
                self.assertIn(needle, out)
                self.assertIn("hits=", out)

    def test_pin_survives_unrelated_edits_and_moves(self):
        moved = "# a new comment line above\n" + WLANFW + "\n\n"
        rc, out = run({**self.noclm(self.ROW), "tools/pack_wlanfw.py": moved})
        self.assertEqual(rc, 0, out)

    def test_list_prints_hits_and_fingerprint(self):
        rc, out = run(tree(), extra=("--list",))
        self.assertRegex(out, r"'CLM ' hits=2 sha=[0-9a-f]{8}\n")
        self.assertEqual(len(set(re.findall(r"sha=([0-9a-f]{8})", out))), 5)    # FW, NVRM and CLM share their two lines (one fingerprint); the other four tags have their own

    def test_row_of_a_consumed_or_gap_logged_tag_is_stale(self):
        rc, out = run(tree(**{"tools/lint-consumed-allow.txt": self.ROW}))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-STALE tools/lint-consumed-allow.txt:1", out)
        rc, out = run(self.noclm(self.ROW, **{"shim/g.c": 'void f(void) { swl_gap("fw", "CLM not loaded", 1); }\n'}))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-STALE", out)

    def test_unknown_row_and_malformed_rows(self):
        rc, out = run(tree(**{"tools/lint-consumed-allow.txt": "tools/pack_wlanfw.py  TXCP  hits=1  sha=00000000  a reason that is long enough\n"}))
        self.assertEqual(rc, 1, out)
        self.assertIn("ALLOW-UNKNOWN tools/lint-consumed-allow.txt:1", out)
        for row, needle in (("tools/pack_wlanfw.py  CLM  wifi reason that is long\n", "ALLOW-NO-PIN"), ("tools/pack_wlanfw.py  CLM  hits=2  wifi reason that is long\n", "ALLOW-NO-PIN"),
                            ("tools/pack_wlanfw.py  CLM  %s  short\n" % self.PIN, "ALLOW-NO-REASON"),
                            ("tools/pack_wlanfw.py\n", "ALLOW-MALFORMED"), (self.ROW * 2, "ALLOW-DUPLICATE")):
            rc, out = run(self.noclm(row))
            self.assertEqual(rc, 1, row + out)
            self.assertIn(needle, out)

    def test_missing_allowlist_file_fails(self):
        rc, out = run(tree(), base={k: v for k, v in BASE.items() if "allow" not in k})
        self.assertEqual(rc, 1, out)
        self.assertIn("MISSING-ALLOWLIST tools/lint-consumed-allow.txt", out)

    def test_comments_and_blank_lines_are_ignored(self):
        rc, out = run(self.noclm("# header\n\n" + self.ROW))
        self.assertEqual(rc, 0, out)


class RealTree(unittest.TestCase):
    def test_real_tree_passes_with_the_two_allowlisted_tags(self):
        p = subprocess.run([sys.executable, "-I", LINT, "--root", str(REPO)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"RESULT: PASS consumed packers=3 tags=8 unconsumed=0 allowed=2 ")   # L22c (T-4adb): CLM is consumed by patch 0174; T-48a0: WLANCRED is too (L24 reader), both allow rows gone
        self.assertNotIn("NOT COVERED tools/pack_wlanfw.py", p.stdout)   # a registered packer is covered, not "not covered"
        for s in ("'FW  ' by xnu/patches/0130", "'NVRM' by xnu/patches/0131", "'WLANFW' by xnu/patches/0128", "'SWEETDEV' by xnu/patches/0117"):
            self.assertIn(s, p.stdout)

    def test_real_tree_without_the_allowlist_rows_shows_clm_consumed(self):
        """Rule 8: without the allowlist the lint fails on the real tree (the allowlisted container/credential tags), while CLM is CONSUMED by patch 0174 (L22c clmload, T-4adb) and never reported."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tools"))
            for n in ("pack_wlanfw.py", "mkpayload.py", "mkdevoverlay.py"):
                (pathlib.Path(d) / "tools" / n).write_text((REPO / "tools" / n).read_text())
            (pathlib.Path(d) / "tools" / "lint-consumed-allow.txt").write_text("")
            os.symlink(REPO / "shim", os.path.join(d, "shim"))
            os.symlink(REPO / "xnu", os.path.join(d, "xnu"))
            p = subprocess.run([sys.executable, "-I", LINT, "--root", d], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertRegex(p.stdout, r"CONSUMED tools/pack_wlanfw\.py 'CLM ' by xnu/patches/0174-[\w.-]+:\d+")
        self.assertNotIn("tag 'CLM '", p.stdout)
        self.assertNotIn("tag 'FW  '", p.stdout)
        self.assertNotIn("tag 'NVRM'", p.stdout)

    def test_manifest_row_matches_the_packer_table(self):
        import importlib.machinery
        import importlib.util
        import json
        manifest = json.loads((TOOLS / "gate-manifest.json").read_text())
        consumed_inputs = next(r for r in manifest["steps"] if r["step"] == "consumed")["inputs"]
        ldr = importlib.machinery.SourceFileLoader("lint_consumed_mod", str(TOOLS / "lint-consumed"))
        spec = importlib.util.spec_from_loader("lint_consumed_mod", ldr)
        mod = importlib.util.module_from_spec(spec)
        ldr.exec_module(mod)
        self.assertEqual(sorted({f for f in consumed_inputs if isinstance(f, str) and f.startswith("tools/") and "*" not in f} - {"tools/lint-consumed", "tools/lint-consumed-allow.txt"}), sorted({r for r, _, _ in mod.PACKERS}))


if __name__ == "__main__":
    unittest.main()
