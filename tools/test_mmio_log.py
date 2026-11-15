#!/usr/bin/env python3
"""Host test of the XNU-side SWEET MMIO log format (patch 0170) against tools/trace-diff diff. Run: python3 -I tools/test_mmio_log.py </dev/null
The test owns the expected printf format; the patch must contain exactly it. Rows are then formatted with it and fed to `trace-diff diff`."""
import glob
import shutil
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "trace-diff")
PATCH = glob.glob(os.path.join(HERE, "..", "xnu", "patches", "0170-sweet-mmio-log.patch"))
EXPECTED_FMT = "SWEET MMIO %c %s mmio - 0x%llx %u 0x%x\n"   # args: op, block name, PA, width, value
CAP_FMT = "SWEET MMLOG: cap hit blk=%s op=%c printed=%u, further accesses counted not printed\n"
SUM_FMT = "SWEET MMLOG: blk=%s where=%s W total=%u printed=%u capped=%d R total=%u printed=%u capped=%d\n"
BASE = 0x1C500000
ROWS = [("W", 0x100, 0x1), ("W", 0x104, 0xdeadbeef), ("W", 0x110, 0), ("W", 0x110, 0), ("W", 0x300, 0x80000001), ("W", 0x3c0, 0xff)]


def c_fmt(fmt, *args):
    """Format a C printf string with the conversions the patch uses (%c %s %llx %u %x %%)."""
    it = iter(args)

    def sub(m):
        v = next(it)
        k = m.group(1)
        return {"c": str, "s": str, "llx": lambda x: "%x" % x, "u": lambda x: "%d" % x, "d": lambda x: "%d" % x, "x": lambda x: "%x" % x}[k](v)
    return re.sub(r"%(c|s|llx|u|x|d)", sub, fmt.replace("%%", "\0")).replace("\0", "%")


def line(r, ts=""):
    return ts + c_fmt(EXPECTED_FMT, r[0], "gpu", BASE + r[1], 32, r[2])


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = self.td.name
        self.addCleanup(self.td.cleanup)
        rows = ["# block=gpu", "seq,ts,src,mark,caller,op,width,space,win,addr,val"]
        for i, r in enumerate(ROWS, 1):
            rows.append("%d,0,t,m,kbase_x,%s,32,mmio,,0x%x,0x%x" % (i, r[0], BASE + r[1], r[2]))
        self.golden = os.path.join(self.d, "g.csv")
        with open(self.golden, "w") as f:
            f.write("\n".join(rows) + "\n")
        self.run_d = os.path.join(self.d, "run")
        os.mkdir(self.run_d)

    def put(self, text):
        with open(os.path.join(self.run_d, "usbnet.log"), "w") as f:
            f.write(text)

    def diff(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "diff", "--golden", self.golden, "--run", self.run_d], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_patch_format(self):
        self.assertEqual(len(PATCH), 1, "patch 0170 missing")
        with open(PATCH[0]) as f:
            txt = f.read()
        fmts = re.findall(r'^\+.*printf\("(SWEET MMIO [^"]*)"', txt, re.M)
        self.assertEqual([f.replace("\\n", "\n") for f in fmts], [EXPECTED_FMT])
        self.assertIn(CAP_FMT.replace("\n", "\\n"), txt)
        self.assertIn(SUM_FMT.replace("\n", "\\n"), txt)
        for m in re.findall(r'^\+.*printf\("(SWEET MMLOG:[^"]*)"', txt, re.M):
            self.assertNotIn("SWEET MMIO", m)

    def test_pass(self):
        self.put("".join(line(r) for r in ROWS))
        rc, o = self.diff()
        self.assertEqual(rc, 0, o)
        self.assertIn("RESULT: PASS no divergence through END compared=6", o)

    def test_pass_with_timestamp_prefix(self):   # patch 0115 sweetlogts=1: "[SSSSS.mmm] " (12 bytes) in front of each line
        self.put("".join(line(r, "[%05d.%03d] " % (3, 40 + i)) for i, r in enumerate(ROWS)))
        rc, o = self.diff()
        self.assertEqual(rc, 0, o)

    def test_pass_with_usbnet_prefix(self):      # tools/usbnet-log.py: "<unix ms>\t<line>"
        self.put("".join(line(r, "1793130390400\t[00003.040] ") for r in ROWS))
        self.assertEqual(self.diff()[0], 0)

    def test_value_change(self):
        bad = list(ROWS)
        bad[3] = ("W", 0x110, 7)
        self.put("".join(line(r) for r in bad))
        rc, o = self.diff()
        self.assertEqual(rc, 1)
        self.assertIn("FIRST DIVERGENCE at golden seq 4", o)

    def test_meta_lines_ignored(self):
        meta = c_fmt(SUM_FMT, "gpu", "bringup", 6, 6, 0, 0, 0, 0) + c_fmt(CAP_FMT, "gpu", "W", 20000) + "SWEET MMLOG: GAP wifi not hooked yet (M-1 step 2)\n"
        self.put(line(ROWS[0]) + meta + "".join(line(r) for r in ROWS[1:]) + meta)
        rc, o = self.diff()
        self.assertEqual(rc, 0, o)
        self.assertNotIn("RUN GAP", o)

    def test_malformed_is_partial(self):
        self.put("".join(line(r) for r in ROWS) + "SWEET MMIO W gpu mmio - 0x1c500100 32 0x1 junk\n")
        rc, o = self.diff()
        self.assertEqual(rc, 2, o)
        self.assertIn("RUN GAP", o)


MALI_RR = "static uint32_t rr(uint32_t off) { uint32_t v = sm.g[off / 4]; sweet_mmio_log(SWEET_MMIO_GPU, 'R', SWEET_MMIO_GPU_BASE + off, 32, v); return v; }"
MALI_WR = "static void wr(uint32_t off, uint32_t v) { sweet_mmio_log(SWEET_MMIO_GPU, 'W', SWEET_MMIO_GPU_BASE + off, 32, v); sm.g[off / 4] = v; }"
JOB_RR = MALI_RR.replace("sm.g", "sg")
JOB_WR = MALI_WR.replace("sm.g", "sg")

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "sweet_mmio_log.h"
extern uint32_t test_mask;
int main(int argc, char **argv)
{
	(void)argc;
	test_mask = (uint32_t)strtoul(argv[1], 0, 0);
	if (!strcmp(argv[2], "basic")) {
		sweet_mmio_log(SWEET_MMIO_GPU, 'W', SWEET_MMIO_GPU_BASE + 0x104, 32, 0xdeadbeefu);
		sweet_mmio_log(SWEET_MMIO_GPU, 'R', SWEET_MMIO_GPU_BASE + 0x10, 32, 1);
		sweet_mmio_log_summary(SWEET_MMIO_GPU, "t");
	} else if (!strcmp(argv[2], "cap")) {
		for (int i = 0; i < 20005; i++) sweet_mmio_log(SWEET_MMIO_GPU, 'W', SWEET_MMIO_GPU_BASE + 4, 32, (uint32_t)i);
		for (int i = 0; i < 3; i++) sweet_mmio_log(SWEET_MMIO_GPU, 'R', SWEET_MMIO_GPU_BASE + 8, 32, (uint32_t)i);
		sweet_mmio_log_summary(SWEET_MMIO_GPU, "t");
	} else if (!strcmp(argv[2], "wifi")) {
		sweet_mmio_log(SWEET_MMIO_WIFI, 'W', 0x1000, 32, 5);
		sweet_mmio_log_summary(SWEET_MMIO_GPU, "t");
	}
	return 0;
}
"""
STUBS = {
    "sys/param.h": "",
    "sys/systm.h": "",
    "libkern/OSAtomic.h": "#include <stdint.h>\ntypedef int32_t SInt32;\ntypedef uint32_t UInt32;\n"
                          "static inline SInt32 OSIncrementAtomic(volatile SInt32 *p) { return __atomic_fetch_add(p, 1, __ATOMIC_RELAXED); }\n"
                          "static inline int OSCompareAndSwap(UInt32 o, UInt32 n, volatile UInt32 *p) { return __atomic_compare_exchange_n(p, &o, n, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED); }\n",
    "pexpert/pexpert.h": "#include <stdio.h>\n#include <stdint.h>\n#define printf(...) printf(__VA_ARGS__)\nextern uint32_t test_mask;\n"
                         "static inline int PE_parse_boot_argn(const char *n, void *p, int sz) { (void)n; (void)sz; if (!test_mask) return 0; *(uint32_t *)p = test_mask; return 1; }\n",
}


def patch_files(txt):
    """New files of the patch: {path: text}."""
    out = {}
    for m in re.finditer(r"^diff --git a/(\S+) b/\S+\nnew file mode[^\n]*\nindex[^\n]*\n--- /dev/null\n\+\+\+ b/\S+\n@@[^\n]*\n((?:\+[^\n]*\n)+)", txt, re.M):
        out[m.group(1)] = "".join(l[1:] + "\n" for l in m.group(2).split("\n")[:-1])
    return out


class C(unittest.TestCase):
    """Compile bsd/dev/sweet_mmio_log.c from the patch on the host with stub kernel headers and drive it."""

    @classmethod
    def setUpClass(cls):
        cls.gcc = shutil.which("gcc")
        if not cls.gcc:
            raise AssertionError("gcc not found: the 0170 compile test cannot run (no skip, install gcc)")
        cls.td = tempfile.TemporaryDirectory()
        d = cls.td.name
        with open(PATCH[0]) as f:
            cls.patch = f.read()
        files = patch_files(cls.patch)
        assert "bsd/dev/sweet_mmio_log.c" in files and "bsd/dev/sweet_mmio_log.h" in files, sorted(files)
        for n, t in STUBS.items():
            os.makedirs(os.path.join(d, "stub", os.path.dirname(n)), exist_ok=True)
            with open(os.path.join(d, "stub", n), "w") as f:
                f.write(t.replace("#define printf(...) printf(__VA_ARGS__)\n", ""))
        for n, t in files.items():
            with open(os.path.join(d, os.path.basename(n)), "w") as f:
                f.write(t)
        with open(os.path.join(d, "driver.c"), "w") as f:
            f.write(DRIVER + "\nuint32_t test_mask;\n")
        cls.exe = os.path.join(d, "t")
        r = subprocess.run([cls.gcc, "-O1", "-DARM64_BOARD_CONFIG_SWEET", "-Wno-format", "-I" + os.path.join(d, "stub"), "-I" + d, "-o", cls.exe,
                            os.path.join(d, "driver.c"), os.path.join(d, "sweet_mmio_log.c")], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def out(self, mask, scen):
        r = subprocess.run([self.exe, mask, scen], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    READS_ON = "SWEET MMLOG: reads on: WAIT() poll loops print per read, timeouts stretch (diagnostic only)"

    def test_writes_only(self):
        self.assertEqual(self.out("1", "basic"), ["SWEET MMIO W gpu mmio - 0x1c500104 32 0xdeadbeef",
                         "SWEET MMLOG: blk=gpu where=t W total=1 printed=1 capped=0 R total=0 printed=0 capped=0"])

    def test_reads_on(self):
        self.assertEqual(self.out("9", "basic"), [self.READS_ON, "SWEET MMIO W gpu mmio - 0x1c500104 32 0xdeadbeef", "SWEET MMIO R gpu mmio - 0x1c500010 32 0x1",
                         "SWEET MMLOG: blk=gpu where=t W total=1 printed=1 capped=0 R total=1 printed=1 capped=0"])

    def test_off(self):
        self.assertEqual(self.out("0", "basic"), [])

    def test_cap_writes_reads_independent(self):
        o = self.out("9", "cap")
        w = [i for i, l in enumerate(o) if l.startswith("SWEET MMIO W ")]
        self.assertEqual(len(w), 20000)
        self.assertEqual(o[w[-1] + 1], "SWEET MMLOG: cap hit blk=gpu op=W printed=20000, further accesses counted not printed")
        self.assertEqual(sum("cap hit" in l for l in o), 1)
        self.assertEqual([l for l in o if l.startswith("SWEET MMIO R ")], ["SWEET MMIO R gpu mmio - 0x1c500008 32 0x%x" % i for i in range(3)])
        self.assertEqual(o[-1], "SWEET MMLOG: blk=gpu where=t W total=20005 printed=20000 capped=1 R total=3 printed=3 capped=0")

    def test_gaps(self):
        self.assertEqual(self.out("2", "wifi"), ["SWEET MMLOG: GAP wifi not hooked yet (M-1 step 2)", "SWEET MMIO W wifi mmio - 0x1000 32 0x5"])   # a future wifi hook would log; the gpu summary is silent
        self.assertEqual(self.out("8", "basic"), ["SWEET MMLOG: GAP sweetmmio=0x8 enables no block", self.READS_ON])
        o = self.out("0x11", "basic")
        self.assertEqual(o[0], "SWEET MMLOG: GAP unknown sweetmmio bits 0x10")
        self.assertIn("SWEET MMIO W gpu mmio - 0x1c500104 32 0xdeadbeef", o)

    def test_hooks_pinned(self):
        added = "\n".join(l[1:] for l in self.patch.split("\n") if l.startswith("+"))
        for ln in (MALI_RR, MALI_WR, JOB_RR, JOB_WR):
            self.assertIn(ln, added)
        self.assertIn("#define SWEET_MMIO_GPU_BASE 0x1C500000ull", added)
        self.assertIn('sweet_mmio_log_summary(SWEET_MMIO_GPU, "gpujob");', added)
        self.assertIn('sweet_mmio_log_summary(SWEET_MMIO_GPU, "bringup");', added)


if __name__ == "__main__":
    unittest.main()
