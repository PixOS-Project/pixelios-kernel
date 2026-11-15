#!/usr/bin/env python3
"""Patch 0242 (T-9d99, panic log over USB): host checks of the kernel half.

1. sweet_panicusb_chunk (osfmk/kern/debug.c) is compiled out of the patch text with the host cc and must cut every fixture
   exactly as tools/panic_capture.py's chunk() model does (the parser reassembles by that rule).
2. The panic-time sender (bsd/dev/sweet_usb.c sweet_usb_panic_send and its helpers) must not lock, allocate, sleep or print,
   and writes only the EP_TX command registers and GEVNTCOUNT (HW-WRITE scope of the review).
"""
import os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import panic_capture  # noqa: E402

PATCH = HERE.parent / "xnu" / "patches" / "0242-sweet-panic-usb.patch"


def added(path_suffix):
    """The '+' lines of the patch hunks for one file, as text."""
    out, on = [], False
    for ln in PATCH.read_text(encoding="latin-1").splitlines():
        if ln.startswith("+++ "):
            on = ln.endswith(path_suffix)
            continue
        if on and ln.startswith("+"):
            out.append(ln[1:])
    return "\n".join(out) + "\n"


def func(src, name):
    """One C function (static or not) from added text: from its return-type line to the closing brace at column 0."""
    m = re.search(r"^(?:static )?[a-z_ ]+\*?\n%s\(.*?^\}\n" % re.escape(name), src, re.S | re.M)
    if not m:
        raise AssertionError("function %s not found in the patch" % name)
    return m.group(0)


class Chunk(unittest.TestCase):
    @unittest.skipIf(shutil.which("cc") is None, "no host cc")
    def test_kernel_chunk_equals_parser_model(self):
        c = func(added("osfmk/kern/debug.c"), "sweet_panicusb_chunk")
        prog = ("#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n" + c +
                "int main(int argc, char **argv) {\n"
                "  FILE *f = fopen(argv[1], \"rb\"); static char s[1 << 16]; unsigned len = fread(s, 1, sizeof(s), f), at = 0, mx = atoi(argv[2]);\n"
                "  while (at < len) { int cont; unsigned n = sweet_panicusb_chunk(s, len, at, mx, &cont); printf(\"%u %d\\n\", n, cont); at += n; }\n"
                "  return 0; }\n")
        texts = [panic_capture.full_text(panic_capture.FIX_CAP, panic_capture.FIX_TAIL), "no newline at all " * 20,
                 "a\n" * 700, "x" * 1199 + "\n" + "y" * 5, "z" * 1200, "z" * 1201 + "\n", "\n"]
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "chunk")
            open(os.path.join(d, "c.c"), "w").write(prog)
            subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-o", exe, os.path.join(d, "c.c")], check=True)
            for t in texts:
                for mx in (37, 100, 1200):
                    p = os.path.join(d, "in")
                    open(p, "w", encoding="latin-1").write(t)
                    got = subprocess.run([exe, p, str(mx)], capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
                    want, at = [], 0
                    while at < len(t):
                        n, cont = panic_capture.chunk(t, at, mx)
                        want.append("%d %d" % (n, int(cont)))
                        at += n
                    self.assertEqual(got, want, "mx=%d text=%r" % (mx, t[:40]))


class SenderScope(unittest.TestCase):
    SENDER = ("ud_panic_spin_us", "ud_panic_tx_trb_hwo", "ud_panic_ack_events", "sweet_usb_panic_check", "sweet_usb_panic_send")

    def body(self):
        src = added("bsd/dev/sweet_usb.c")
        return re.sub(r"/\*.*?\*/", "", "".join(func(src, n) for n in self.SENDER), flags=re.S)     # code only, no comments

    def test_no_lock_alloc_sleep_print(self):
        b = self.body()
        for bad in ("lck_", "printf", "kalloc", "IOMalloc", "mbuf", "sock_", "thread_block", "assert_wait", "ud_sleep_ms",
                    "ud_epcmd", "delay(", "kprintf", "ud_start_trb"):
            self.assertNotIn(bad, b, "panic-time sender uses %s" % bad)

    def test_only_ep_tx_and_event_count_written(self):
        b = self.body()
        writes = re.findall(r"dwc_wr\(([^,]+),", b)
        self.assertEqual(sorted(set(writes)), sorted({"DEPCMDPAR0(EP_TX)", "DEPCMDPAR1(EP_TX)", "DEPCMDPAR2(EP_TX)", "DEPCMD(EP_TX)", "GEVNTCOUNT"}))
        self.assertNotIn("*(volatile uint32_t *)(ud.dwc", b)      # no raw register store around dwc_wr

    def test_trb_same_fields_as_normal_tx(self):
        b = self.body()
        self.assertIn("TRBCTL_NORMAL | TRB_HWO | TRB_LST | TRB_ISP_IMI | TRB_IOC", b)   # ud_start_trb's t[3] with ctl = TRBCTL_NORMAL
        self.assertIn("DEPCMD_STARTTRANSFER | DEPCMD_CMDACT", b)
        self.assertIn("ud.dma + UD_TX", b)

    def test_refusals_are_all_named(self):
        src = added("bsd/dev/sweet_usb.c")
        n = len(re.findall(r'^\t"[^"]+",$', src.split("ud_panic_why[] = {", 1)[1].split("};", 1)[0], re.M))
        codes = {int(x) for x in re.findall(r"return (\d+);", self.body())}
        self.assertTrue(codes and max(codes) < n, "a return code without a ud_panic_why text: %s (%d texts)" % (sorted(codes), n))



# Host model of the DWC3 for the sender harness (T-9d99 review r1 F3). Offsets and bits are Linux drivers/usb/dwc3 v6.6 core.h
# (DEPCMDPAR2/1/0/DEPCMD at 0xc800/04/08/0c + 0x10*ep, GEVNTCOUNT 0xc40c, CMDACT bit 10, STARTTRANSFER 6) and the shim's TRB bits;
# the scope test above pins that the patch writes only these names.
SENDER_STUB = r"""
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
typedef void *thread_t;
#define ETHER_ADDR_LEN 6
#define ETHER_HDR_LEN 14
#define NSEC_PER_USEC 1000ull
#define GEVNTCOUNT 0xc40c
#define DEPCMDPAR2(n) (0xc800 + 0x10 * (n))
#define DEPCMDPAR1(n) (0xc804 + 0x10 * (n))
#define DEPCMDPAR0(n) (0xc808 + 0x10 * (n))
#define DEPCMD(n)     (0xc80c + 0x10 * (n))
#define DEPCMD_STARTTRANSFER 6
#define DEPCMD_CMDACT (1u << 10)
#define TRB_HWO 1u
#define TRB_LST 2u
#define TRB_ISP_IMI (1u << 10)
#define TRB_IOC (1u << 11)
#define TRBCTL_NORMAL (1u << 4)
#define UD_TRB_TX 0x1030
#define UD_TX 0x2800
#define UD_EVT_SIZE 0x1000
#define EP_TX 3
static uint8_t dmabuf[0x4000];
static struct { uintptr_t dwc, dma; uint64_t dma_pa; bool configured, tx_busy; uint8_t data_alt; uint32_t evt_pos; uint64_t tx_frames; } ud;
static thread_t ud_thread_self, ud_tx_owner;
static uint8_t ud_host_mac[6] = {0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff};
static bool ud_host_mac_ok = true;
static const uint8_t ud_phone_mac[6] = {0x02, 0x00, 0x5e, 0x77, 0xee, 0x02};
static int mode;                /* 0 healthy, 1 CMDACT stuck, 2 HWO never cleared, 3 StartTransfer status error, 4 a prior TRB still HWO */
static thread_t current_thread(void) { return (thread_t)1; }
static uint32_t wr_log[64][2], ctl_at_start; static int nwr; static uint64_t now, reads;
static void dwc_wr(uint32_t o, uint32_t v) { if (nwr < 64) { wr_log[nwr][0] = o; wr_log[nwr][1] = v; } nwr++;
  if (o == DEPCMD(EP_TX)) ctl_at_start = ((volatile uint32_t *)(ud.dma + UD_TRB_TX))[3];
  if (o == DEPCMD(EP_TX) && mode != 2) ((volatile uint32_t *)(ud.dma + UD_TRB_TX))[3] &= ~TRB_HWO; }
static uint32_t dwc_rd(uint32_t o) { reads++; if (reads > 10000000) { puts("UNBOUNDED"); exit(4); }
  if (o == DEPCMD(EP_TX)) return mode == 1 ? DEPCMD_CMDACT : mode == 3 ? (1u << 12) : 0; return 0; }
static void ud_sync(uint32_t o, uint32_t l) { (void)o; (void)l; }
static uint64_t mach_absolute_time(void) { return now += 10; }
static void nanoseconds_to_absolutetime(uint64_t ns, uint64_t *r) { *r = ns; }
"""
SENDER_MAIN = r"""
int main(int argc, char **argv) { ud.dwc = 1; ud.dma = (uintptr_t)dmabuf; ud.dma_pa = 0x181000000ull; ud.configured = 1; ud.data_alt = 1;
  int n = atoi(argv[1]); mode = atoi(argv[2]); int chk = atoi(argv[3]);
  if (chk == 1) ud.configured = 0; if (chk == 2) ud_thread_self = (thread_t)1; if (chk == 3) ud_tx_owner = (thread_t)2; if (chk == 4) ud.dwc = 0;
  if (mode == 4) ((volatile uint32_t *)(dmabuf + UD_TRB_TX))[3] = TRB_HWO;
  char p[1600]; for (int i = 0; i < n; i++) p[i] = 'a' + i % 26;
  int rc = sweet_usb_panic_send(p, n); uint32_t *t = (uint32_t *)(dmabuf + UD_TRB_TX);
  printf("rc=%d\ntrb %08x %08x %08x %08x\n", rc, t[0], t[1], t[2], ctl_at_start);
  for (int i = 0; i < nwr && i < 64; i++) printf("wr %04x %08x\n", wr_log[i][0], wr_log[i][1]);
  if (rc == 0) { FILE *f = fopen(argv[4], "wb"); fwrite(dmabuf + UD_TX, 1, t[2], f); fclose(f); }
  return 0; }
"""


@unittest.skipIf(shutil.which("cc") is None, "no host cc")
class SenderHarness(unittest.TestCase):
    """sweet_usb_panic_send compiled from the patch against a DWC3 model: writes, TRB, frame bytes, refusals, wait bounds."""

    @classmethod
    def setUpClass(cls):
        src = added("bsd/dev/sweet_usb.c")
        body = "".join(func(src, n) for n in SenderScope.SENDER).replace('__asm__ volatile ("yield");', "")
        pre = src[src.index("#define UD_PANIC_WAIT_US"):src.index("/* Busy-wait")]
        why = src[src.index("static const char *const ud_panic_why"):src.index("const char *\nsweet_usb_panic_why")]
        cls.d = tempfile.TemporaryDirectory()
        c = os.path.join(cls.d.name, "s.c")
        cls.exe = os.path.join(cls.d.name, "s")
        open(c, "w").write(SENDER_STUB + pre + why + body + SENDER_MAIN)
        subprocess.run(["cc", "-O1", "-w", "-o", cls.exe, c], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.d.cleanup()

    def send(self, n, mode=0, chk=0):
        fr = os.path.join(self.d.name, "frame.bin")
        if os.path.exists(fr):
            os.unlink(fr)
        out = subprocess.run([self.exe, str(n), str(mode), str(chk), fr], capture_output=True, text=True, timeout=60).stdout
        rc = int(re.search(r"rc=(-?\d+)", out).group(1))
        trb = [int(x, 16) for x in re.search(r"trb (\S+) (\S+) (\S+) (\S+)", out).groups()]
        wr = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"wr (\S+) (\S+)", out)]
        frame = open(fr, "rb").read() if os.path.exists(fr) else b""
        return rc, trb, wr, frame, out

    def test_writes_trb_and_frame(self):
        for n in (1, 100, 470, 1472):
            rc, trb, wr, f, _ = self.send(n)
            self.assertEqual(rc, 0, n)
            pa_tx, pa_trb = 0x181000000 + 0x2800, 0x181000000 + 0x1030
            self.assertEqual(trb, [pa_tx & 0xffffffff, pa_tx >> 32, 14 + 28 + n + ((14 + 28 + n) % 512 == 0), 0x10 | 1 | 2 | (1 << 10) | (1 << 11)])  # PA, not VA; ctl as seen at StartTransfer
            self.assertEqual(wr, [(0xc838, pa_trb >> 32), (0xc834, pa_trb & 0xffffffff), (0xc830, 0), (0xc83c, 6 | (1 << 10))])
            ip = f[14:34]
            self.assertEqual(sum((ip[i] << 8) | ip[i + 1] for i in range(0, 20, 2)) % 0xffff, 0, "IPv4 header checksum")
            self.assertEqual((ip[0], ip[9], (ip[2] << 8) | ip[3]), (0x45, 17, 28 + n))
            udp = f[34:42]
            self.assertEqual(((udp[0] << 8) | udp[1], (udp[2] << 8) | udp[3], (udp[4] << 8) | udp[5]), (5140, 5140, 8 + n))
            self.assertEqual(bytes(f[42:42 + n]), bytes(ord("a") + i % 26 for i in range(n)))
            self.assertEqual(bytes(f[42 + n:]), b"\0" if (42 + n) % 512 == 0 else b"")   # pad byte instead of a ZLP
            self.assertEqual((f[:6], f[12:14]), (bytes([0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff]), b"\x08\x00"))

    def test_refusals_write_nothing(self):
        for (n, mode, chk), want in {(0, 0, 0): 5, (1473, 0, 0): 5, (10, 0, 1): 2, (10, 0, 2): 3, (10, 0, 3): 4, (10, 0, 4): 1}.items():
            rc, _, wr, _, _ = self.send(n, mode, chk)
            self.assertEqual((rc, wr), (want, []), (n, mode, chk))

    def test_stuck_hardware_is_bounded(self):
        for mode, want, nwr in ((1, 6, 0), (2, 9, 4), (3, 8, 4), (4, 7, 0)):
            rc, _, wr, _, out = self.send(10, mode)
            self.assertNotIn("UNBOUNDED", out)
            self.assertEqual((rc, len(wr)), (want, nwr), mode)


DEBUG_STUB = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdarg.h>
typedef int boolean_t;
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define SWEET_PANIC_CAP 8192
static char sweet_panic_cap[SWEET_PANIC_CAP]; static unsigned sweet_panic_cap_len, sweet_panic_cap_lost; static int sweet_panic_cap_state;
static unsigned sweet_panicusb_on = 1, sweet_panicusb_tail = 4096; static unsigned debugger_sync; static boolean_t force_immediate_debug_halt;
static char ring[1 << 16]; static uint64_t total;
static uint64_t sweet_log_position(void) { return total; }
static uint32_t sweet_log_copy(uint64_t *pos, char *out, uint32_t max) { uint32_t n = MIN(max, total - *pos); memcpy(out, ring + *pos, n); *pos += n; return n; }
static int sweet_usb_panic_check(void) { return 0; }
static FILE *outf; static int dropk = -1, cnt;
static int sweet_usb_panic_send(const char *p, uint32_t len) { if (len > 1472) { fprintf(stderr, "too long %u\n", len); exit(3); }
  if (cnt++ != dropk) fwrite(p, 1, len, outf); return 0; }
static const char *sweet_usb_panic_why(int c) { (void)c; return "x"; }
static bool sweet_usb_panic_host_mac_learned(void) { return 1; }
static uint64_t mach_absolute_time(void) { return 0x0badf00d; }
static void panic_debugger_log(const char *f, ...) { va_list a; va_start(a, f); vfprintf(stderr, f, a); va_end(a); }
"""
DEBUG_MAIN = r"""
int main(int argc, char **argv) { FILE *c = fopen(argv[1], "rb"); sweet_panic_cap_len = fread(sweet_panic_cap, 1, SWEET_PANIC_CAP, c);
  FILE *r = fopen(argv[2], "rb"); total = fread(ring, 1, sizeof ring, r); outf = fopen(argv[3], "wb"); sweet_panicusb_tail = atoi(argv[4]);
  dropk = atoi(argv[5]); debugger_sync = atoi(argv[6]); force_immediate_debug_halt = atoi(argv[7]);
  sweet_panic_usb(); fclose(outf); return 0; }
"""


@unittest.skipIf(shutil.which("cc") is None, "no host cc")
class EndToEnd(unittest.TestCase):
    """sweet_panic_usb (debug.c) compiled from the patch; its datagrams through panic_capture must give back the exact text."""

    @classmethod
    def setUpClass(cls):
        src = added("osfmk/kern/debug.c")
        code = src[src.index("#define SWEET_PANICUSB_TAIL_MAX"):]
        code = code[:code.index("\nstatic void\nsweet_panic_usb(void)")] + "\n" + func(src, "sweet_panic_usb")
        cls.d = tempfile.TemporaryDirectory()
        c = os.path.join(cls.d.name, "e.c")
        cls.exe = os.path.join(cls.d.name, "e")
        open(c, "w").write(DEBUG_STUB + code + DEBUG_MAIN)
        subprocess.run(["cc", "-O1", "-w", "-o", cls.exe, c], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.d.cleanup()

    def run_one(self, cap, ring, tail, drop=-1, sync=0, nmi=0):
        p = lambda n: os.path.join(self.d.name, n)
        open(p("cap"), "w", encoding="latin-1").write(cap)
        open(p("ring"), "w", encoding="latin-1").write(ring)
        r = subprocess.run([self.exe, p("cap"), p("ring"), p("dg"), str(tail), str(drop), str(sync), str(nmi)],
                           capture_output=True, text=True, timeout=60, check=True)
        return open(p("dg"), encoding="latin-1").read(), r.stderr

    def test_reassembly_equals_kernel_text(self):
        import random
        rnd = random.Random(1)
        words = lambda n, nl: "".join(rnd.choice("abc \t") if rnd.random() > nl else "\n" for _ in range(n))
        for trial in range(16):
            cap = (panic_capture.FIX_CAP + words(rnd.randint(0, 9000), rnd.choice([0.0, 0.001, 0.05])))[:8192]
            ring = words(rnd.randint(0, 20000), rnd.choice([0.0, 0.002, 0.05]))
            tail = rnd.choice([0, 100, 4096, 8192])
            dg, _ = self.run_one(cap, ring, tail)
            keep, caps = panic_capture.split(panic_capture.receive([dg], before="[ 1.0] live\n[ 2.0] half"))
            c = caps["0badf00d"]
            exp = ring[-tail:] if tail and len(ring) > tail else ring if tail else ""
            if tail and len(ring) > tail:
                i = exp.find("\n")
                exp = exp[i + 1:] if i >= 0 else exp
            self.assertEqual(c["text"], panic_capture.full_text(cap, exp), "trial %d" % trial)
            self.assertFalse(c["missing"], "trial %d" % trial)

    def test_dropped_frame_is_reported_missing(self):
        dg, _ = self.run_one(panic_capture.FIX_CAP + "x\n" * 3000, "", 0, drop=2)
        _, caps = panic_capture.split(panic_capture.receive([dg], before=""))
        self.assertTrue(caps["0badf00d"]["missing"])

    def test_cpu_not_stopped_refusals(self):
        for sync, nmi, why in ((1, 0, "did not stop for the debugger"), (0, 1, "immediate_NMI")):
            dg, err = self.run_one(panic_capture.FIX_CAP, "ring\n", 100, sync=sync, nmi=nmi)
            self.assertEqual(dg, "", (sync, nmi))
            self.assertIn("SWEET PANIC USB GAP: ", err)
            self.assertIn(why, err)


class NoRegionBacktrace(unittest.TestCase):
    """T-15e0: with panic_info == NULL SavePanicInfo returned before print_all_backtraces (cap=98 on run
    20261107T172408Z-panic-usb-forced), so patch 0242 prints the region-free part of the backtrace there."""

    def text(self):
        return added("osfmk/arm/model_dep.c")

    def body(self):
        m = re.search(r"^sweet_noregion_backtrace\(const char \*message\)\n\{\n.*?^\}\n", self.text(), re.S | re.M)
        self.assertTrue(m, "sweet_noregion_backtrace not in the patch")
        return re.sub(r"/\*.*?\*/", "", m.group(0), flags=re.S)

    def test_called_in_the_null_branch_before_return(self):
        raw = PATCH.read_text(encoding="latin-1")
        hunk = raw.split("+++ b/osfmk/arm/model_dep.c", 1)[1]
        self.assertRegex(hunk, r"\n \tif \(panic_info == NULL\) \{\n\+\t\tsweet_noregion_backtrace\(message\);[^\n]*\n \t\treturn;\n")

    def test_sweet_only(self):
        t = self.text()
        self.assertTrue(t.startswith("#if defined(ARM64_BOARD_CONFIG_SWEET)\n"), t[:60])
        self.assertIn("#endif /* ARM64_BOARD_CONFIG_SWEET */", t)

    def test_no_region_no_mmio_no_lock(self):
        b = re.sub(r'"(?:[^"\\]|\\.)*"', '""', self.body())     # code only: no string literals
        for bad in ("panic_info", "debug_buf", "gPanic", "paniclog_flush", "kdp_snapshot", "do_stackshot", "hung_cpus",
                    "UTTDBG", "volatile uint", "lck_", "kprintf", "printf(", "kmod_panic_dump", "kext_dump", "PE_boot_args",
                    "gPlatformECID", "ledger", "do_print_all_backtraces", "print_all_backtraces("):
            self.assertNotIn(bad, b, "sweet_noregion_backtrace uses %s" % bad)

    CALLS = {"current_thread", "getCpuDatap", "panic_display_kernel_aslr", "panic_display_kernel_uuid", "panic_display_last_pc_lr",
             "panic_get_thread_proc_task", "paniclog_append_noflush", "print_one_backtrace", "proc_name_kdp", "proc_pid",
             "thread_tid"}

    def test_calls_are_exactly_the_reviewed_set(self):
        # T-15e0 review r1 F1: an allowlist, not a denylist: every callee was read for panic_info / MMIO / lock / alloc
        b = re.sub(r'"(?:[^"\\]|\\.)*"', '""', self.body())
        got = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", b)) - {"if", "sizeof", "volatile", "sweet_noregion_backtrace"}
        self.assertEqual(got, self.CALLS, "new callee(s) need a review: %s" % sorted(got ^ self.CALLS))

    def test_state_restored_and_nesting_refused(self):
        # T-15e0 review r1 F2: the whole nested guard, every term
        self.assertRegex(self.body(), r"\n\tif \(sweet_noregion_bt_done \|\| panic_bt_depth != 0 \|\| cpu_data_ptr->PAB_active\) \{\n")
        b = self.body()
        for want in ("not_in_kdp = 0;", "not_in_kdp = initial_not_in_kdp;", "cpu_data_ptr->PAB_active = TRUE;",
                     "cpu_data_ptr->PAB_active = FALSE;", "panic_bt_depth++;", "panic_bt_depth--;",
                     "sweet_noregion_bt_done = TRUE;"):
            self.assertIn(want, b)
        self.assertLess(b.index("sweet_noregion_bt_done ||"), b.index("not_in_kdp = 0;"))
        self.assertIn("SWEET PANIC GAP: nested panic", b)
        self.assertIn("SWEET PANIC GAP: no panic region", b)     # rule 8: what is left out is said in the log

    def test_backtrace_kernel_pmap_no_kexts(self):
        self.assertIn('print_one_backtrace(kernel_pmap, cur_fp, "\\t", TRUE, FALSE);', self.body())

    def test_upstream_line_format_parses(self):
        # print_one_backtrace's 64-bit line (X/osfmk/arm/model_dep.c:275 "%s\t  lr: 0x%016llx  fp: 0x%016llx\n") with marker "\t"
        line = "\t" + "\t  lr: 0x%016x  fp: 0x%016x\n" % (0xfffffe0010000100, 0xfffffe1000abb010)
        self.assertEqual(panic_capture.BT.findall(line), [("0xfffffe0010000100", "0xfffffe1000abb010")])


if __name__ == "__main__":
    unittest.main()
