#!/usr/bin/env python3
"""Host test of tools/trace-diff with synthetic fixtures in a tempdir. Run: python3 tools/test_trace_diff.py </dev/null"""
import os
import subprocess
import sys
import tempfile
import unittest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trace-diff")
PRE = "  kworker/0:1-123  [002] d..1.  12.345678: "
VM = "0xffffffc012340000-0xffffffc012348000   32768 dhdpcie_bus_reg_map+0x40/0x90 [bcmdhd4389] phys=0x0000000060000000 ioremap\n" \
     "0xffffffc020000000-0xffffffc020001000    4096 kbase_mmio+0x1/0x2 phys=0x000000001c500000 ioremap\n" \
     "0xffffffc030000000-0xffffffc030001000    4096 foo+0x1/0x2 phys=0x0000000070000000 vmap\n"


def w(addr, val, caller="dhdpcie_bus_cfg_write+0x1c/0x40", ev="rwmmio_write", width=32):
    return PRE + "%s: %s width=%d val=%s addr=0x%x\n" % (ev, caller, width, val, addr)


def mark(row, done=False):
    return PRE + "tracing_mark_write: MARK %s%s\n" % (row, " done" if done else "")


def cw(pos, val):
    return PRE + "pcicfg/w32: (pci_bus_write_config_dword+0x4/0x60) devfn=0x0 pos=%d val=%s\n" % (pos, val)


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = self.td.name
        self.addCleanup(self.td.cleanup)

    def put(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def rd(self, p):
        with open(p) as f:
            return f.read()

    def run_tool(self, *args):
        r = subprocess.run([sys.executable, "-I", TOOL] + list(args), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def golden(self, trace, *extra, vm=VM, block="wifi"):
        self.put("trace-boot.txt", trace)
        self.put("vmallocinfo-boot.txt", vm)
        out = os.path.join(self.d, "o.csv")
        rc, o = self.run_tool("golden-regs", self.d, "--block", block, "--out", out, *extra)
        return rc, o, out

    def test_both_events_and_zero(self):
        rc, o, out = self.golden(w(0xffffffc012340010, "0") + w(0xffffffc012340014, "0x5", ev="rwmmio_post_read"), "--bar0", "0x10000000")
        self.assertIn("RESULT: PASS block=wifi rows=2", o)
        self.assertEqual(rc, 0)
        c = self.rd(out)
        self.assertIn(",W,32,", c)
        self.assertIn(",R,32,", c)
        self.assertIn(",0x0\n", c)
        self.assertIn("sha256=", c)

    def test_join_and_unjoined(self):
        rc, o, _ = self.golden(w(0xffffffc099999999, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("TRACE GAP: unjoined", o)
        self.assertIn("RESULT: PARTIAL", o)
        self.assertIn("unjoined=1 ", o)

    def test_join_phys(self):
        rc, o, out = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x70000000")
        self.assertIn("0x60000010", self.rd(out))
        self.assertIn("mmio", self.rd(out))

    def test_mark_split(self):
        rc, o, _ = self.golden(mark("A") + w(0xffffffc012340010, "0x1") + mark("B") + w(0xffffffc012340010, "0x2")
                               + w(0xffffffc012340014, "0x2", ev="rwmmio_post_read") + mark("B", True), "--bar0", "0x10000000")
        self.assertIn("mark=A writes=1 reads=0", o)
        self.assertIn("mark=B writes=1 reads=1", o)

    def test_block_filter_other(self):
        tr = w(0xffffffc012340010, "0x1") + w(0xffffffc020000010, "0x1", caller="kbase_x+0x1/0x2") + w(0xffffffc020000020, "0x1", caller="zzz+0x1/0x2")
        rc, o, out = self.golden(tr, "--bar0", "0x10000000")
        self.assertIn("rows=1", o)
        self.assertIn("other=2", o)
        rc, o, out = self.golden(tr, block="gpu")
        self.assertIn("rows=1", o)
        self.assertEqual(rc, 0)
        io = self.put("iomem.txt", "  1c500000-1c504fff : 1c500000.mali\n")
        rc, o, out = self.golden(tr, "--iomem", io, "--iomem-match", "mali", "--bar0", "0x10000000")
        self.assertIn("rows=3", o)  # kbase and zzz rows both sit in the iomem range
        self.assertIn("other=0", o)

    def test_bar0_window(self):
        tr = cw(128, "0x18000000") + w(0xffffffc012340010, "0x1") + cw(128, "0x18002000") + w(0xffffffc012340010, "0x2")
        rc, o, out = self.golden(tr, "--bar0", "0x60000000")
        self.assertEqual(rc, 0, o)
        c = self.rd(out)
        self.assertIn(",CW,32,cfg,,0x80,0x18000000", c)
        self.assertIn("bar0,0x18000000,0x10,0x1", c)
        self.assertIn("bar0,0x18002000,0x10,0x2", c)

    def test_bar0_before_window_is_gap(self):
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x60000000")
        self.assertEqual(rc, 1)
        self.assertIn("WIFI GAP: BAR0 access before any window write", o)

    def test_bar0_win_in_key(self):
        tr = cw(128, "0x18000000") + w(0xffffffc012340010, "0x1") + cw(128, "0x18002000") + w(0xffffffc012340010, "0x1")
        rc, o, g = self.golden(tr, "--bar0", "0x60000000")
        self.assertEqual(rc, 0, o)
        # run lines: the CW rows first, then bar0 rows with two different windows at the same offset
        def log(w1, w2):
            return self.put("run.log", "SWEET MMIO CW wifi cfg - 0x80 32 0x18000000\nSWEET MMIO W wifi bar0 0x%x 0x10 32 0x1\n"
                            "SWEET MMIO CW wifi cfg - 0x80 32 0x18002000\nSWEET MMIO W wifi bar0 0x%x 0x10 32 0x1\n" % (w1, w2))
        rc, o = self.run_tool("diff", "--golden", g, "--run", log(0x18000000, 0x18002000))
        self.assertEqual(rc, 0, o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", log(0x18000000, 0x18000000))
        self.assertEqual(rc, 1, o)
        self.assertIn("FIRST DIVERGENCE at golden seq 4", o)
        self.assertIn("bar0 0x18002000 0x10", o)
        # same offset, different windows must not be merged by --collapse-polls
        rc, o = self.run_tool("diff", "--golden", g, "--run", log(0x18000000, 0x18002000), "--collapse-polls")
        self.assertEqual(rc, 0, o)
        self.assertIn("merged 0 rows", o)

    def test_missing_bar0(self):
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"))
        self.assertEqual(rc, 1)
        self.assertIn("WIFI GAP: no --bar0", o)
        self.assertIn("RESULT: PARTIAL", o)

    def test_out_in_git(self):
        subprocess.run(["git", "init", "-q", self.d], check=True, stdin=subprocess.DEVNULL)
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 2)
        self.assertIn("git checkout", o)

    def test_malformed(self):
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1") + PRE + "rwmmio_write: garbage\n" + PRE + "pcicfg/w32: devfn=0x0\n", "--bar0", "0x10000000")
        self.assertIn("TRACE GAP: unparsed line 2", o)
        self.assertIn("TRACE GAP: unparsed line 3", o)
        self.assertEqual(rc, 1)

    # diff
    def mkgolden(self):
        tr = mark("A") + w(0xffffffc012340010, "0x1") + w(0xffffffc012340014, "0x2") + mark("B") + w(0xffffffc012340018, "0x3") \
            + w(0xffffffc012340018, "0x3") + w(0xffffffc01234001c, "0x4")
        return self.golden(tr, "--bar0", "0x10000000")[2]

    def runlog(self, rows):
        return self.put("run.log", "".join("[  1.0] SWEET MMIO W wifi mmio - 0x%x 32 0x%x\n" % r for r in rows))

    def test_diff_rundir_reads_usbnet_once(self):
        # AP-141: an xrun dir has usbnet.log and merged.log with the same lines; the dir must be read once (usbnet.log), not twice
        g = self.mkgolden()
        base = [(0x60000010, 1), (0x60000014, 2), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]
        self.runlog(base)
        text = open(os.path.join(self.d, "run.log")).read()
        rd = os.path.join(self.d, "rundir")
        os.mkdir(rd)
        for n in ("usbnet.log", "merged.log"):
            with open(os.path.join(rd, n), "w") as f:
                f.write(text)
        rc, o = self.run_tool("diff", "--golden", g, "--run", rd)
        self.assertEqual(rc, 0, o)
        self.assertIn("usbnet.log only", o)
        os.remove(os.path.join(rd, "usbnet.log"))
        os.rename(os.path.join(rd, "merged.log"), os.path.join(rd, "a.log"))
        with open(os.path.join(rd, "b.log"), "w") as f:
            f.write(text)
        rc, o = self.run_tool("diff", "--golden", g, "--run", rd)
        self.assertNotEqual(rc, 0, o)   # no usbnet.log: all *.log are read, so the doubled rows diverge

    def test_diff_pass_fail_end(self):
        g = self.mkgolden()
        base = [(0x60000010, 1), (0x60000014, 2), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(base))
        self.assertEqual(rc, 0, o)
        self.assertIn("RESULT: PASS no divergence through END compared=5", o)
        bad = list(base)
        bad[2] = (0x60000018, 9)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(bad))
        self.assertEqual(rc, 1)
        self.assertIn("FIRST DIVERGENCE at golden seq 3 (mark B)", o)
        self.assertIn("RESULT: FAIL first divergence seq=3", o)
        self.assertEqual(o.count("  matched seq="), 2)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(base[:3]))
        self.assertEqual(rc, 1)
        self.assertIn("| run END", o)
        self.assertIn("seq=4", o)

    def test_diff_range_and_collapse(self):
        g = self.mkgolden()
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]), "--from", "B")
        self.assertEqual(rc, 0, o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x60000018, 3), (0x6000001c, 4)]), "--from", "B")
        self.assertEqual(rc, 1)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x60000018, 3), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]),
                              "--from", "B", "--collapse-polls")
        self.assertEqual(rc, 0, o)
        self.assertIn("merged", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x60000010, 1), (0x60000014, 2)]), "--to", "A")
        self.assertEqual(rc, 0, o)

    def test_diff_no_sweet_lines(self):
        g = self.mkgolden()
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.put("run.log", "nothing here\n"))
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: PARTIAL run has no SWEET MMIO lines", o)

    # review r1 additions
    BIGVM = "0xffffffc040000000-0xffffffc040600000 6291456 dhd_big+0x1/0x2 phys=0x0000000060000000 ioremap\n"
    B = 0xffffffc040000000

    def csv_rows(self, out):
        return [x for x in self.rd(out).splitlines() if x and not x.startswith("#") and not x.startswith("seq,")]

    def test_bar_bounds(self):
        tr = cw(128, "0x1000") + cw(132, "0x2000") + w(self.B + 0x7fff, "0x1") + w(self.B + 0x8000, "0x2") \

        rc, o, out = self.golden(tr, "--bar0", "0x60000000", "--bar2", "0x60100000", vm=self.BIGVM)
        r = self.csv_rows(out)
        self.assertIn(",W,32,bar0,0x1000,0x7fff,0x1", r[2])
        self.assertIn(",W,32,mmio,,0x60008000,0x2", r[3])   # BAR0 end is exclusive

    def test_bar2_upper_bound(self):
        tr = cw(132, "0x2000") + w(self.B + 0x4fffff, "0x1") + w(self.B + 0x500000, "0x2")
        rc, o, out = self.golden(tr, "--bar0", "0x10000000", "--bar2", "0x60100000", vm=self.BIGVM)
        r = self.csv_rows(out)
        self.assertIn(",W,32,bar2,0x2000,0x3fffff,0x1", r[1])
        self.assertIn(",W,32,mmio,,0x60500000,0x2", r[2])

    def test_vm_bounds(self):
        rc, o, _ = self.golden(w(0xffffffc012348000, "0x1"), "--bar0", "0x10000000")
        self.assertIn("unjoined=1", o)  # range end is exclusive
        rc, o, out = self.golden(w(0xffffffc012340000, "0x1") + w(0xffffffc012347fff, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 0, o)
        self.assertIn("0x60007fff", self.rd(out))

    def test_zero_address_vmap_rows_gap_not_crash(self):
        # golden-6 vmallocinfo-coldboot.txt: many 0x0-0x0 rows, some vmap (phys None) tie on (start,end): rows.sort() raised TypeError (research 105 item 6)
        vm = VM + "0x0000000000000000-0x0000000000000000 4096 a+0x1/0x2 ioremap vmap\n0x0000000000000000-0x0000000000000000 4096 b+0x1/0x2 phys=0x0000000000000000 ioremap\n"
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000", vm=vm)
        self.assertNotIn("Traceback", o)
        self.assertEqual(rc, 1, o)
        self.assertIn("TRACE GAP: vmallocinfo-boot.txt has 2 of 4 ioremap/vmap rows with all-zero addresses", o)
        self.assertRegex(o, r"RESULT: PARTIAL .*gaps=[1-9]")

    def test_vmap_nophys_row(self):
        # golden-3 vmallocinfo form: pstore page vmap, no phys= (mm/vmalloc.c s_show v6.1)
        vm = VM + "0xffffffc008800000-0xffffffc008a01000 2101248 persistent_ram_new+0x584/0x634 ioremap vmap\n"
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000", vm=vm)
        self.assertEqual(rc, 0, o)
        self.assertIn("vmap_nophys=1", o)
        self.assertIn("TRACE NOTE: vmap row without phys=", o)
        self.assertNotIn("unparsed vmallocinfo", o)
        rc, o, _ = self.golden(w(0xffffffc008800010, "0x1"), "--bar0", "0x10000000", vm=vm)
        self.assertEqual(rc, 1, o)  # a row inside the vmap range is still unjoined
        self.assertIn("unjoined=1", o)
        bad = VM + "garbage ioremap vmap\n"
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000", vm=bad)
        self.assertEqual(rc, 1, o)  # r1 F3: an unparseable vmap line is a GAP, not a NOTE
        self.assertIn("unparsed vmallocinfo", o)
        nest = VM + "0xffffffc012341000-0xffffffc012342000 4096 persistent_ram_new+0x1/0x2 ioremap vmap\n"
        rc, o, _ = self.golden(w(0xffffffc012341800, "0x1"), "--bar0", "0x10000000", vm=nest)
        self.assertEqual(rc, 1, o)  # r1 F4: nested vmap is not joined to the outer ioremap range
        self.assertIn("unjoined=1", o)

    def test_nested_vm_innermost(self):
        vm = "0xffffffc010000000-0xffffffc010100000 1048576 dhd_a+0x1/0x2 phys=0x0000000060000000 ioremap\n" \
             "0xffffffc010001000-0xffffffc010002000 4096 dhd_b+0x1/0x2 phys=0x0000000070000000 ioremap\n"
        rc, o, out = self.golden(w(0xffffffc010001010, "0x1") + w(0xffffffc010050000, "0x2") + w(0xffffffc010002000, "0x3"), "--bar0", "0x10000000", vm=vm)
        self.assertEqual(rc, 0, o)
        r = self.csv_rows(out)
        self.assertIn("0x70000010", r[0])
        self.assertIn("0x60050000", r[1])
        self.assertIn("0x60002000", r[2])  # inner end is exclusive: falls back to the outer range

    def test_w16_does_not_set_window(self):
        w16 = PRE + "pcicfg/w16: (pci_bus_write_config_word+0x4/0x60) devfn=0x0 pos=128 val=0x1234\n"
        rc, o, _ = self.golden(w16 + w(self.B + 0x10, "0x1"), "--bar0", "0x60000000", vm=self.BIGVM)
        self.assertIn("WIFI GAP: BAR0 access before any window write", o)

    def test_window_reset_per_file(self):
        self.put("trace-a.txt", cw(128, "0x1000") + w(self.B + 0x10, "0x1"))
        self.put("vmallocinfo-a.txt", self.BIGVM)
        self.put("trace-b.txt", w(self.B + 0x10, "0x1"))
        self.put("vmallocinfo-b.txt", self.BIGVM)
        rc, o = self.run_tool("golden-regs", self.d, "--block", "wifi", "--out", os.path.join(self.d, "o.csv"), "--bar0", "0x60000000")
        self.assertEqual(rc, 1)
        self.assertIn("trace-b.txt line 1", o)

    def test_done_resets_mark(self):
        rc, o, out = self.golden(mark("A") + w(0xffffffc012340010, "0x1") + mark("A", True) + w(0xffffffc012340010, "0x2"), "--bar0", "0x10000000")
        r = self.csv_rows(out)
        self.assertEqual(r[0].split(",")[3], "A")
        self.assertEqual(r[1].split(",")[3], "")

    def test_rc_nonzero_gap(self):
        ret = PRE + "pcicfg/rd32_ret: (pci_bus_read_config_dword+0x4/0x60) rc=%s\n"
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1") + ret % "0", "--bar0", "0x10000000")
        self.assertEqual(rc, 0, o)
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1") + ret % "-5", "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("rc=-5", o)

    def test_input_dir_in_git_refused(self):
        subprocess.run(["git", "init", "-q", self.d], check=True, stdin=subprocess.DEVNULL)
        self.put("trace-boot.txt", w(0xffffffc012340010, "0x1"))
        self.put("vmallocinfo-boot.txt", VM)
        with tempfile.TemporaryDirectory() as other:
            rc, o = self.run_tool("golden-regs", self.d, "--block", "wifi", "--out", os.path.join(other, "o.csv"), "--bar0", "0x10000000")
        self.assertEqual(rc, 2)
        self.assertIn("git checkout", o)

    def test_zero_rows_partial(self):
        rc, o, _ = self.golden(w(0xffffffc012340010, "0x1", caller="zzz+0x1/0x2"), "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("zero rows", o)

    def test_iomem_upper_bound_inclusive(self):
        io = self.put("iomem.txt", "  1c500000-1c500fff : 1c500000.mali\n")
        rc, o, _ = self.golden(w(0xffffffc020000fff, "0x1", caller="zzz+0x1/0x2"), "--iomem", io, "--iomem-match", "mali", "--bar0", "0x10000000")
        self.assertIn("rows=1", o)

    def test_mark_line_gap(self):
        rc, o, _ = self.golden(PRE + "tracing_mark_write: marker two\n" + w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("TRACE GAP: unparsed mark line 1", o)
        self.assertIn("unparsed=1 ", o)

    def test_lost_events_and_buffer_header(self):
        rc, o, _ = self.golden("# entries-in-buffer/entries-written: 90/100   #P:8\n" + w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("entries-in-buffer/entries-written 90/100", o)
        rc, o, _ = self.golden("# entries-in-buffer/entries-written: 100/100   #P:8\n" + w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 0, o)
        rc, o, _ = self.golden("CPU:2 [LOST 5 EVENTS]\n" + w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")
        self.assertEqual(rc, 1)
        self.assertIn("LOST", o)

    def test_csv_without_block_error(self):
        g = self.put("nb.csv", "seq,ts,src,mark,caller,op,width,space,win,addr,val\n1,1.0,t,A,c,W,32,mmio,,0x10,0x1\n")
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x10, 1)]))
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR", o)

    def test_run_line_anchor_and_unparsed(self):
        g = self.mkgolden()
        base = "".join("SWEET MMIO W wifi mmio - 0x%x 32 0x%x\n" % r for r in [(0x60000010, 1), (0x60000014, 2), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)])
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.put("a.log", base.replace("0x4\n", "0x4zzz\n")))
        self.assertNotEqual(rc, 0)
        self.assertIn("RUN GAP: 1 unparsed", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.put("b.log", base + "SWEET MMIO X wifi mmio\n"))
        self.assertEqual(rc, 2)
        self.assertIn("RUN GAP", o)
        self.assertIn("PARTIAL", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.put("c.log", "SWEET MMIO W wifi mmio - 0x1 garbage\n"))
        self.assertEqual(rc, 1, o)  # a golden row the run lacks is a FAIL; the gap is still printed
        self.assertIn("RUN GAP", o)

    def test_empty_and_inverted_range(self):
        tr = mark("m1") + w(0xffffffc012340010, "0x1") + mark("m2") + w(0xffffffc012340014, "0x2", ev="rwmmio_post_read") + mark("m3") + w(0xffffffc012340018, "0x3")
        g = self.golden(tr, "--bar0", "0x10000000")[2]
        rl = self.runlog([(0x60000010, 1)])
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl, "--from", "m2", "--to", "m2")
        self.assertEqual(rc, 2, o)
        self.assertIn("RESULT: PARTIAL no golden rows", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl, "--from", "m3", "--to", "m1")
        self.assertEqual(rc, 2, o)
        self.assertIn("inverted range", o)

    def test_context_five_rows(self):
        tr = mark("A") + "".join(w(0xffffffc012340010 + 4 * i, "0x%x" % i) for i in range(1, 8))
        g = self.golden(tr, "--bar0", "0x10000000")[2]
        rows = [(0x60000010 + 4 * i, i) for i in range(1, 8)]
        rows[6] = (rows[6][0], 99)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(rows))
        self.assertEqual(rc, 1)
        self.assertEqual(o.count("  matched seq="), 5)
        self.assertIn("seq=2 ", o)
        self.assertNotIn("matched seq=1 ", o)

    def test_to_last_occurrence(self):
        tr = mark("A") + w(0xffffffc012340010, "0x1") + mark("B") + w(0xffffffc012340014, "0x2") + mark("A") + w(0xffffffc012340018, "0x3")
        g = self.golden(tr, "--bar0", "0x10000000")[2]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog([(0x60000010, 1), (0x60000014, 2), (0x60000018, 3)]), "--to", "A")
        self.assertEqual(rc, 0, o)
        self.assertIn("compared=3", o)

    def test_extra_run_rows_without_to(self):
        g = self.mkgolden()
        base = [(0x60000010, 1), (0x60000014, 2), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(base + [(0x60000020, 5)]))
        self.assertEqual(rc, 1)
        self.assertIn("golden END", o)
        self.assertIn("seq=6", o)

    def test_mask_reads(self):
        tr = w(0xffffffc012340010, "0x5", ev="rwmmio_post_read") + w(0xffffffc012340014, "0x5", ev="rwmmio_post_read")
        g = self.golden(tr, "--bar0", "0x10000000")[2]
        rl = self.put("r.log", "SWEET MMIO R wifi mmio - 0x60000010 32 0x9\nSWEET MMIO R wifi mmio - 0x60000014 32 0x5\n")
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl, "--reads")
        self.assertEqual(rc, 1)
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl, "--reads", "--mask", "0x60000010")
        self.assertEqual(rc, 0, o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl)  # reads ignored by default
        self.assertEqual(rc, 2, o)  # run has only reads: nothing to compare, never PASS

    def test_width_compared(self):
        g = self.golden(w(0xffffffc012340010, "0x1"), "--bar0", "0x10000000")[2]
        rl = self.put("w.log", "SWEET MMIO W wifi mmio - 0x60000010 16 0x1\n")
        rc, o = self.run_tool("diff", "--golden", g, "--run", rl)
        self.assertEqual(rc, 1)

    def test_gki61_arrow_caller_and_wifi_callers(self):
        # 6.1 GKI rwmmio: "func+off/len [mod] -> writel+off/len [mod]" (trace-wifi2, 2026-10-27)
        tr = "".join(w(0xffffffc012340010 + 4 * i, "0x%x" % i, caller=c + " -> writel+0x18/0x88 [pcie_exynos_gs101_rc_cal]")
                     for i, c in enumerate(("ai_corereg+0x1c/0x40 [bcmdhd4389]",
                                            "exynos_pcie_rc_poweron+0x20/0x124 [pcie_exynos_gs]",
                                            "check_exynos_pcie_reg_status+0x8/0x40 [pcie_exynos_gs]",
                                            "zzz+0x1/0x2 [m]")))
        rc, o, out = self.golden(tr, "--bar0", "0x10000000")
        self.assertIn("RESULT: PASS block=wifi rows=3", o)
        self.assertIn("other=1", o)
        c = self.rd(out)
        for name in (",ai_corereg,", ",exynos_pcie_rc_poweron,", ",check_exynos_pcie_reg_status,"):
            self.assertIn(name, c)
        self.assertNotIn("writel", c)

    def test_va_only(self):
        zero = "0x0000000000000000-0x0000000000000000   32768 dhdpcie_bus_reg_map+0x40/0x90 [bcmdhd4389] phys=0x0000000000000000 ioremap\n"
        tr = w(0xffffffc012340010, "0x7") + w(0xffffffc012340014, "0x1", caller="zzz+0x1/0x2")
        rc, o, out = self.golden(tr, "--bar0", "0x10000000", vm=zero)
        self.assertEqual(rc, 1)
        self.assertIn("all-zero ioremap addresses", o)
        self.assertIn("unjoined=1 ", o)
        rc, o, out = self.golden(tr, "--bar0", "0x10000000", "--va-only", vm=zero)
        self.assertEqual(rc, 1)
        self.assertIn("rows=1 unjoined=0", o)
        self.assertIn("TRACE GAP: --va-only: 1 rows", o)
        self.assertIn("other=1", o)
        self.assertIn(",W,32,va,,0xffffffc012340010,0x7\n", self.rd(out))
        # a joined row stays a PA row under --va-only
        rc, o, out = self.golden(w(0xffffffc012340010, "0x7"), "--bar0", "0x70000000", "--va-only")
        self.assertIn(",mmio,,0x60000010,0x7\n", self.rd(out))
        self.assertEqual(rc, 0)

    def test_gpu_dpu_callers(self):
        tr = (w(0xffffffc020000010, "0x1", caller="gpu_pm_power_on_top+0x1/0x2 [mali_kbase] -> writel+0x1/0x2 [mali_kbase]")
              + w(0xffffffc020000014, "0x2", caller="kbasep_x+0x1/0x2 [mali_kbase]")
              + w(0xffffffc020000018, "0x3", caller="decon_reg_x+0x1/0x2 [exynos_drm]"))
        rc, o, out = self.golden(tr, block="gpu")
        self.assertIn("rows=2", o)
        self.assertIn("other=1", o)
        io = self.put("iomem.txt", "  1c500000-1c504fff : 1c500000.mali\n")
        rc, o, out = self.golden(tr, "--iomem", io, "--iomem-match", "nomatch", block="dpu")
        self.assertIn("rows=1", o)
        # review golden-gpu-dpu-r1 F4: PCIe / power-domain callers are not dpu rows
        rc, o, out = self.golden(w(0xffffffc020000010, "0x1", caller="exynos_pcie_rc_poweron+0x1/0x2")
                                 + w(0xffffffc020000014, "0x1", caller="exynos_pd_power_on+0x1/0x2")
                                 + w(0xffffffc020000018, "0x1", caller="gs_chipid_x+0x1/0x2")
                                 + w(0xffffffc02000001c, "0x1", caller="exynos_drm_crtc_x+0x1/0x2"), block="dpu")
        self.assertIn("rows=1", o)
        self.assertIn("other=3", o)

    # --by-offset
    def test_by_offset(self):
        g = self.mkgolden()
        base = [(0x60000010, 1), (0x60000014, 2), (0x60000018, 3), (0x60000018, 3), (0x6000001c, 4)]
        swapped = [base[1], base[0]] + base[2:]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(swapped))
        self.assertEqual(rc, 1)
        self.assertIn("first divergence seq=1", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(swapped), "--by-offset")
        self.assertEqual(rc, 0, o)
        self.assertIn("RESULT: PASS by-offset keys=4 match=4", o)
        # (b) one value differs
        bad = list(base)
        bad[3] = (0x60000018, 9)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(bad), "--by-offset")
        self.assertEqual(rc, 1)
        self.assertIn("first_diff=1 golden_val=0x3 run_val=0x9", o)
        self.assertIn("RESULT: FAIL by-offset keys=4 match=3 values=1 missing=0 extra=0 first_match=4", o)
        # (c) golden-only key
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(base[:4]), "--by-offset")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", o)
        self.assertIn("missing=1", o)
        # (d) run-only key
        ex = base + [(0x60000020, 5)]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(ex), "--by-offset")
        self.assertEqual(rc, 1)
        self.assertIn("extra=1", o)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(ex), "--by-offset", "--to", "B")
        self.assertEqual(rc, 0, o)
        self.assertNotIn("EXTRA", o)
        self.assertIn("note: --to: ignored 1 run-only key(s) (1 rows) and 0 run rows past", o)
        self.assertIn("ignored=1", o)
        # review r1 F1/F2: under --to, run values past a key's golden count are cut AND counted in ignored=
        more = base + [(0x60000018, 7), (0x60000018, 8)]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(more), "--by-offset", "--to", "B")
        self.assertEqual(rc, 0, o)
        self.assertIn("and 2 run rows past", o)
        self.assertIn("RESULT: PASS by-offset keys=4 match=4 ignored=2", o)
        # review r1 F3: window `-` and window 0x0 are different keys
        r0 = self.runlog(base)
        with open(r0, "a") as f:
            f.write("[  1.0] SWEET MMIO W wifi mmio 0x0 0x60000010 32 0x1\n")
        rc, o = self.run_tool("diff", "--golden", g, "--run", r0, "--by-offset")
        self.assertEqual(rc, 1, o)
        self.assertIn("OFFSET W mmio 0x0 0x60000010 32 golden=0 run=1 EXTRA", o)
        # review r1 F4: non-MATCH lines are printed before MATCH lines
        self.assertLess(o.index("EXTRA"), o.index("MATCH"))
        # (e) collapse-polls before grouping: run repeats a poll write 3x, golden has 2 (collapsed to 1 each side)
        rep = base[:2] + [(0x60000018, 3)] * 3 + base[4:]
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(rep), "--by-offset")
        self.assertEqual(rc, 1)
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.runlog(rep), "--by-offset", "--collapse-polls")
        self.assertEqual(rc, 0, o)

    def test_by_offset_no_sweet_lines(self):
        g = self.mkgolden()
        rc, o = self.run_tool("diff", "--golden", g, "--run", self.put("run.log", "nothing\n"), "--by-offset")
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: PARTIAL run has no SWEET MMIO lines", o)


if __name__ == "__main__":
    unittest.main()
