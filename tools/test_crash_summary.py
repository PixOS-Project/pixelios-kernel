#!/usr/bin/env python3
"""Host test for tools/crash_summary.py (autorecover 2026-11-09): cause classes and symbolization on a synthetic panic capture.

The symbolization case uses a real kernel when one is on this host (CRASH_TEST_KERNEL, default the newest local build), else it is skipped
(said in the skip reason). Runtime addresses = the kernel's own symbol addresses + a made-up slide; the block must name the functions."""
import glob, json, os, subprocess, sys, tempfile, shutil, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crash_summary, panic_capture  # noqa: E402

T0 = 1_800_000_000.0
SLIDE = 0x10000000
_LB = os.environ.get("LOCALBUILD") or os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "localbuild")   # as tools/xnu-local-build
KERNEL = os.environ.get("CRASH_TEST_KERNEL") or next(iter(sorted(glob.glob(os.path.join(_LB, "out", "*", "kernel.development.sweet")), key=os.path.getmtime, reverse=True)), "")


def nm(kernel, name):
    tool = crash_summary.symbolizer().replace("llvm-symbolizer", "llvm-nm")
    for l in subprocess.run([tool, kernel], capture_output=True, text=True).stdout.splitlines():
        f = l.split()
        if len(f) == 3 and f[2] == name:
            return int(f[0], 16)
    return None


class Summary(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def rundir(self, log, run):
        with open(os.path.join(self.d, "usbnet.log"), "w") as f:
            for i, l in enumerate(log):
                f.write("%f\t%s\n" % (T0 + 10 + i, l))
        r = dict({"t_boot": str(T0), "dir": self.d}, **run)
        json.dump(r, open(os.path.join(self.d, "run.json"), "w"))
        return r

    def test_clean_run_has_no_block(self):
        self.assertEqual(crash_summary.lines(self.rundir(["SWEET CPU: up"], {"host_end": "1", "early": str(T0 + 99), "usb_back_id": "18d1:4ee0"})), [])

    def test_watchdog_reset_names_the_watchdog(self):
        r = self.rundir(["SWEET WDT: armed period=30 threshold_ms=10000", "last line"], {"usb_back_id": "18d1:d001", "usb_back": str(T0 + 200)})
        out = crash_summary.lines(r)
        self.assertIn("cause=watchdog-reset", out[0])
        self.assertIn("CRASH: last log +11s: last line", out)
        self.assertTrue(out[-1].startswith("CRASH: phone back as 18d1:d001 at +200s"))

    def test_unexplained_recovery_landing(self):
        out = crash_summary.lines(self.rundir(["x"], {"usb_back_id": "18d1:d001"}))
        self.assertIn("cause=reset-unexplained", out[0])

    def test_no_slide_line_with_text_base_means_slide_zero(self):
        cap = "panic(cpu 0 caller 0xfffffe0010000000): x\nKernel text base:  0xfffffe0007004000\n\t  lr: 0xfffffe0010000008  fp: 0x0\n"
        log = panic_capture.receive(panic_capture.datagrams(panic_capture.full_text(cap, "")))
        out = crash_summary.lines(self.rundir(log, {"kernel": "/nonexistent", "usb_back_id": "18d1:d001"}))
        self.assertIn("CRASH: slide 0 (text base line); panicked thread backtrace 1 frames (return addresses symbolized at -4)", out)

    def test_stall_panic_is_named_and_raw_without_kernel(self):
        cap = ("panic(cpu 2 caller 0xfffffe0010000000): SWEET STALL: the usb-log drain thread did not run for 20012 ms (limit 20 s, 9 ticks before)\n"
               "Debugger message: x\nKernel slide:      0x%016x\nCORE 0: PC=0xfffffe0010000004, LR=0xfffffe0010000008, FP=0x1\n"
               "\t  lr: 0xfffffe001000000c  fp: 0x0\n" % SLIDE)
        log = ["before"] + panic_capture.receive(panic_capture.datagrams(panic_capture.full_text(cap, "tail line\n")))
        out = crash_summary.lines(self.rundir(log, {"kernel": "/nonexistent", "usb_back_id": "18d1:d001"}))
        self.assertIn("cause=stall", out[0])
        self.assertTrue(any(l.startswith("CRASH GAP: kernel /nonexistent not on this host") for l in out), out)
        self.assertIn("CRASH: core 0 pc 0xfffffe0010000004 ? lr 0xfffffe0010000008 ?", out)

    @unittest.skipIf(not KERNEL or not crash_summary.symbolizer(), "no local kernel build or no llvm-symbolizer on this host")
    def test_symbolized_frames(self):
        p, q = nm(KERNEL, "_panic"), nm(KERNEL, "_sweet_restart_poll")
        self.assertTrue(p and q, "symbols missing in %s" % KERNEL)
        cap = ("panic(cpu 1 caller 0x%x): test\nKernel slide:      0x%016x\nCORE 3: PC=0x%x, LR=0x%x, FP=0x0\nCORE 1 is the one that panicked. Check the full backtrace for details.\n"
               "\t  lr: 0x%016x  fp: 0x0\n" % (q + SLIDE + 8, SLIDE, p + SLIDE + 0x10, q + SLIDE + 8, p + SLIDE + 8))
        log = panic_capture.receive(panic_capture.datagrams(panic_capture.full_text(cap, "")))
        out = crash_summary.lines(self.rundir(log, {"kernel": KERNEL, "usb_back_id": "18d1:d001"}))
        txt = "\n".join(out)
        self.assertIn("cause=panic", out[0])
        self.assertIn("CRASH: caller 0x%x sweet_restart_poll+0x4" % (q + SLIDE + 8), txt)        # return address: looked up at -4
        self.assertIn("CRASH: frame 0 0x%016x panic+0x4" % (p + SLIDE + 8), txt)
        self.assertIn("lr 0x%x sweet_restart_poll+0x4" % (q + SLIDE + 8), txt)
        self.assertIn("pc 0x%x panic+0x10" % (p + SLIDE + 0x10), txt)
        self.assertIn("CRASH: core 1 is the one that panicked", txt)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    print("RESULT: %s crash-summary tests=%d failures=%d skipped=%d" % ("PASS" if r.wasSuccessful() else "FAIL", r.testsRun, len(r.failures) + len(r.errors), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
