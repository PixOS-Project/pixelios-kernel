"""tools/usbnet-log.py (UDP listener) and its merge into tools/xrun_report.py; no phone, no root, loopback only."""
import contextlib, importlib.util, io, json, os, pathlib, shutil, socket, subprocess, sys, tempfile, threading, time, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xrun_report
from test_xrun_report import make_run, T0

_spec = importlib.util.spec_from_file_location("usbnet_log", HERE / "usbnet-log.py")
ul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ul)

FIX = (HERE / "testdata" / "usbnet-a.log").read_text()      # <epoch>\t<line>, as written by the listener; boot command at T0


class Listener(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usbnet-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_feed_reassembles_split_lines_and_strips_cr(self):
        out = io.BytesIO()
        buf, t0 = ul.feed(b"", None, b"SWEET USB: bus re", out, 10.0)
        self.assertEqual((buf, out.getvalue()), (b"SWEET USB: bus re", b""))
        buf, t0 = ul.feed(buf, t0, b"set\r\nSWEET USB: conn", out, 11.0)
        self.assertEqual(out.getvalue(), b"10.000\tSWEET USB: bus reset\n")
        buf, t0 = ul.feed(buf, t0, b"ected\nx\n", out, 12.0)
        self.assertEqual(out.getvalue().split(b"\n")[1:], [b"11.000\tSWEET USB: connected", b"12.000\tx", b""])
        self.assertEqual(buf, b"")

    def test_udp_loopback_writes_timestamped_file(self):
        out, port = os.path.join(self.tmp, "u.log"), []
        th = threading.Thread(target=ul.run, kwargs=dict(out_path=out, port=0, duration=10, quiet=True, ready=port.append,
                                                                  until_file=os.path.join(self.tmp, "stop")))
        th.start()
        while not port:
            time.sleep(0.01)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"line one\nline t", ("127.0.0.1", port[0]))
        time.sleep(0.2)
        s.sendto(b"wo\nlast without newline", ("127.0.0.1", port[0]))
        time.sleep(0.2)
        s.close()
        open(os.path.join(self.tmp, "stop"), "w").close()
        th.join(5)
        self.assertFalse(th.is_alive())
        rows = [r.split("\t", 1) for r in pathlib.Path(out).read_text().splitlines()]
        self.assertEqual([r[1] for r in rows], ["line one", "line two", "last without newline"])
        self.assertTrue(all(abs(float(r[0]) - time.time()) < 10 for r in rows))

    def test_datagrams_queued_at_stop_are_kept(self):
        # the stop file exists before the loop runs: datagrams already in the socket buffer are the last lines of the run
        out, stop = os.path.join(self.tmp, "q.log"), os.path.join(self.tmp, "stop2")
        open(stop, "w").close()

        def ready(port):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for i in range(5):
                s.sendto(b"queued %d\n" % i, ("127.0.0.1", port))
            s.close()
        ul.run(out_path=out, port=0, duration=10, quiet=True, ready=ready, until_file=stop)
        rows = [r.split("\t", 1)[1] for r in pathlib.Path(out).read_text().splitlines()]
        self.assertEqual(rows, ["queued %d" % i for i in range(5)])

    def test_drain_is_bounded_when_the_sender_keeps_streaming(self):
        # review 13 M2: a log storm after the stop file must not hold xrun (and the phone lock)
        out, stop = os.path.join(self.tmp, "s.log"), os.path.join(self.tmp, "stop3")
        open(stop, "w").close()
        port, run_stop = [], threading.Event()

        def sender(p):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            while not run_stop.is_set():
                s.sendto(b"storm\n", ("127.0.0.1", p))
                time.sleep(0.05)
            s.close()
        t0 = time.time()
        th = threading.Thread(target=lambda: None)
        ul_thread = threading.Thread(target=ul.run, kwargs=dict(out_path=out, port=0, duration=30, quiet=True, until_file=stop,
                                                                 ready=lambda p: (port.append(p), threading.Thread(target=sender, args=(p,), daemon=True).start())))
        ul_thread.start()
        ul_thread.join(8)
        run_stop.set()
        self.assertFalse(ul_thread.is_alive())
        self.assertLess(time.time() - t0, 5)

    def test_cli_stops_on_until_file(self):
        stop, out = os.path.join(self.tmp, "stop"), os.path.join(self.tmp, "u.log")
        p = subprocess.Popen([sys.executable, "-I", str(HERE / "usbnet-log.py"), "--out", out, "--port", "0", "--until-file", stop, "--quiet"],
                             stderr=subprocess.PIPE, text=True)
        time.sleep(1)
        open(stop, "w").close()
        self.assertEqual(p.wait(5), 0)
        self.assertIn("bytes received", p.stderr.read())


class Merge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usbnet-test-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_dir(self, usb=FIX, snaps=(), run=None, mark=True):
        d = make_run(pathlib.Path(self.tmp) / "20260101T000000Z-t", snaps=list(snaps), run=dict(run or {}, **({"usbnet": "1"} if mark else {})))
        if usb is not None:
            (d / "usbnet.log").write_text(usb)
        return str(d)

    def test_usb_only_log_gives_times_markers_and_pass(self):
        d = self.run_dir(run={"expect": ["SWEET USB: configured", "SWEET TOUCH: gs101"]})
        r = xrun_report.build(d)
        self.assertEqual(r["result"], "PASS")
        self.assertEqual(r["coverage"]["usbnet_lines"], 12)
        self.assertEqual(round(r["expect"][0]["t"], 1), 16.0)        # line 8 of the fixture: 12 + 8 * 0.5 s after the boot command
        for m in ("USB running", "USB configured (host enumerated)", "USB ue0 up"):
            self.assertEqual(r["markers"][m]["count"], 1, m)
        self.assertEqual(r["markers"]["USB errors"]["count"], 0)
        self.assertIn("usbnet: 12 line(s), first at +12s", xrun_report.render(r))

    def test_qr_tail_beyond_the_stream_is_appended(self):
        qr = "\n".join(l.split("\t", 1)[1] for l in FIX.splitlines()[4:]) + "\nSWEET RESTART: resetting now\nafter the link died\n"
        d = self.run_dir(snaps=[(60, qr)], run={"expect": ["SWEET RESTART: resetting now", "SWEET USB: soft reset"]})
        r = xrun_report.build(d)
        self.assertEqual(r["coverage"]["gaps"], 0)
        self.assertEqual(r["result"], "PASS")
        merged = pathlib.Path(d, "merged.log").read_text().splitlines()
        self.assertEqual(merged.count("SWEET USB: bus reset"), 1)       # overlap not duplicated
        self.assertEqual(merged[-2:], ["SWEET RESTART: resetting now", "after the link died"])
        self.assertEqual(round(r["expect"][0]["t"]), 60)                # QR-only line keeps its snapshot time
        self.assertEqual(round(r["expect"][1]["t"], 1), 13.5)           # USB line keeps its datagram time
        # a dry-run rebuild (what `xrun --dry-run` does) must not merge the stream twice
        r2 = xrun_report.build(d)
        self.assertEqual(pathlib.Path(d, "merged.log").read_text().splitlines(), merged)

    def test_no_datagram_is_said_plainly(self):
        d = self.run_dir(usb=None, snaps=[(30, "SWEET CPU: cpu=0 mpidr=0x0 came alive; SGI1 OK\n")])
        txt = xrun_report.render(xrun_report.build(d))
        self.assertIn("usbnet: NO DATAGRAM received", txt)

    def test_usb_failure_markers(self):
        bad = "".join("%.3f\t%s\n" % (T0 + 5 + i, l) for i, l in enumerate(
            ["SWEET USB: gs101: unexpected GSNPSID 0x00000000, not touching the controller", "SWEET USB: data endpoints setup failed"]))
        r = xrun_report.build(self.run_dir(usb=bad))
        self.assertEqual(r["markers"]["USB errors"]["count"], 2)
        self.assertEqual(r["markers"]["USB running"]["count"], 0)

    def test_no_usbnet_file_is_byte_identical_to_before(self):
        d = self.run_dir(usb=None, snaps=[(30, "a\nb\nc\nd\n")], mark=False)
        r = xrun_report.build(d)
        self.assertEqual(r["coverage"]["lines"], 4)
        self.assertEqual(r["coverage"]["text"], "1 snapshot(s), 0 gap(s), starts at boot; first line: a | last line: d")


class Stage(unittest.TestCase):
    def test_stage_prints_one_sudo_line_and_does_not_wait(self):
        out = subprocess.run([str(HERE / "sweet-usbnet.sh"), "--stage"], capture_output=True, text=True, timeout=10).stdout
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("sudo sh -c '"))
        self.assertIn("02:00:5e:77:ee:01", out)
        self.assertIn("192.168.7.1/24", out)


if __name__ == "__main__":
    unittest.main()
