import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

# The tool's filename is hyphenated (usb-log.py), so import it by path.
_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "usb-log.py"
_spec = importlib.util.spec_from_file_location("usb_log", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
usb_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usb_log)

SCRIPT = _MODULE_PATH
PAYLOAD = b"gadget up\nsecond line\n"


def make_gadget(sysfs, vid="1209", pid="0001", tty="ttyACM0"):
    dev = pathlib.Path(sysfs) / "1-1"
    iface = dev / "1-1:1.0"
    (iface / "tty" / tty).mkdir(parents=True)
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")


class UsbLogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.sysfs = self.tmp / "sysfs"
        self.sysfs.mkdir()


class ScanTests(UsbLogTest):
    def test_scan_finds_matching_device(self):
        make_gadget(self.sysfs)
        node = usb_log.scan_once(str(self.sysfs))
        self.assertEqual(
            node,
            str(self.sysfs / "1-1" / "1-1:1.0" / "tty" / "ttyACM0"))

    def test_scan_ignores_other_vid_pid_and_files(self):
        make_gadget(self.sysfs, vid="18d1", pid="4ee1")
        (self.sysfs / "usb1").write_text("not a directory\n")
        self.assertIsNone(usb_log.scan_once(str(self.sysfs)))

    def test_wait_prints_dev_node(self):
        make_gadget(self.sysfs)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = usb_log.main(["--sysfs", str(self.sysfs), "wait"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "/dev/ttyACM0")

    def test_wait_timeout_exit_code_2(self):
        rc = usb_log.main(["--sysfs", str(self.sysfs), "--timeout", "0.5",
                           "--poll", "0.1", "wait"])
        self.assertEqual(rc, 2)


class CaptureTests(UsbLogTest):
    def run_tool(self, *args, timeout=60):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args),
            capture_output=True, timeout=timeout)

    def test_capture_from_plain_file(self):
        make_gadget(self.sysfs)
        src = self.tmp / "acm0"
        src.write_bytes(PAYLOAD)
        out = self.tmp / "log" / "usb.txt"
        proc = self.run_tool("--sysfs", str(self.sysfs), "--timeout", "5",
                             "--tty-path", str(src),
                             "capture", "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out.read_bytes(), PAYLOAD)
        self.assertIn(PAYLOAD, proc.stdout)
        self.assertIn(
            ("captured %d bytes to %s" % (len(PAYLOAD), out)).encode(),
            proc.stdout)

    def test_capture_times_out_when_device_missing(self):
        proc = self.run_tool("--sysfs", str(self.sysfs), "--timeout", "0.5",
                             "--poll", "0.1", "capture")
        self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_capture_ends_cleanly_when_pty_master_closes(self):
        make_gadget(self.sysfs)
        master, slave = os.openpty()
        self.addCleanup(os.close, slave)
        out = self.tmp / "pty.txt"
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--sysfs", str(self.sysfs),
             "--timeout", "30", "--tty-path", os.ttyname(slave),
             "capture", "--out", str(out)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # The output file is created after the tty is open: seeing it
            # means writing to the master cannot race the reader.
            deadline = time.monotonic() + 30
            while not out.exists():
                if time.monotonic() > deadline:
                    self.fail("capture never created its output file")
                time.sleep(0.01)
            os.write(master, PAYLOAD)
            # Closing the master hangs up and flushes pending input, so
            # close only after the child has consumed (and flushed) it all.
            deadline = time.monotonic() + 30
            while out.read_bytes() != PAYLOAD:
                if time.monotonic() > deadline:
                    self.fail("capture never consumed the pty data")
                time.sleep(0.01)
        finally:
            os.close(master)  # slave reads drain, then fail with EIO
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(out.read_bytes(), PAYLOAD)
        self.assertIn(
            ("captured %d bytes to %s" % (len(PAYLOAD), out)).encode(),
            stdout)


class TailTests(UsbLogTest):
    """the last lines of a run are the ones that matter: do not drop them when the device goes away or the capture is killed"""

    def test_node_vanishing_with_data_queued_keeps_all_data(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        node = self.tmp / "node"
        node.write_text("x")
        payload = b"".join(b"line %04d ................................\n" % i for i in range(60))
        os.write(master, payload)
        node.unlink()  # the device is gone, but its data is still queued in the tty
        old = usb_log.READ_SIZE
        usb_log.READ_SIZE = 256  # several reads are needed to empty the queue
        self.addCleanup(setattr, usb_log, "READ_SIZE", old)
        out = self.tmp / "tail.txt"
        usb_log.capture(str(node), os.ttyname(slave), str(out), io.BytesIO(), poll=0.05)
        self.assertEqual(out.read_bytes(), payload)

    def test_times_sidecar_has_one_row_per_line(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        node = self.tmp / "node"
        node.write_text("x")
        os.write(master, b"one\ntwo\nthree\n")
        node.unlink()
        out = self.tmp / "t.txt"
        usb_log.capture(str(node), os.ttyname(slave), str(out), io.BytesIO(), poll=0.05)
        rows = (self.tmp / "t.times").read_text().split("\n")[:-1]
        self.assertEqual([r.split()[1] for r in rows], ["4", "8", "14"])  # byte offsets of the line ends

    def test_sigterm_keeps_the_last_partial_line(self):
        make_gadget(self.sysfs)
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        out = self.tmp / "term.txt"
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--sysfs", str(self.sysfs), "--timeout", "30", "--tty-path", os.ttyname(slave),
             "capture", "--out", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while not out.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        os.write(master, b"first\nWLAN LINK summary reached=S99")  # no newline: sits in the pending buffer
        time.sleep(1.0)
        proc.terminate()
        proc.communicate(timeout=30)
        self.assertEqual(out.read_bytes(), b"first\nWLAN LINK summary reached=S99")


class ExtractTests(UsbLogTest):
    def test_extract_is_passthrough(self):
        src = self.tmp / "in.txt"
        src.write_bytes(b"\x00\x01binary\nlines\n")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "extract", str(src)],
            capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"\x00\x01binary\nlines\n")


class DefaultOutPathTests(unittest.TestCase):
    def test_default_path_shape(self):
        path = usb_log.default_out_path()
        self.assertEqual(os.path.dirname(path), "logs")
        name = os.path.basename(path)
        self.assertTrue(name.startswith("usb-"))
        self.assertTrue(name.endswith(".txt"))
        # The embedded stamp is UTC.
        self.assertIn("Z", name)


if __name__ == "__main__":
    unittest.main()
