"""tools/usbroot-host.py against a mock transport: the whole USB root protocol without a phone, root or network.

The phone side is modelled by PhoneModel, which follows bsd/dev/sweet_usbroot.c (sequence per attempt, fragment
bitmap, 4 tries, whole-request retry). test_constants_match_patch ties both to the numbers in xnu/patches/0157.
Last line RESULT: PASS|FAIL (unittest wrapper at the bottom).
"""
import hashlib, importlib.util, os, pathlib, queue, re, shutil, struct, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("usbroot_host", HERE / "usbroot-host.py")
uh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uh)

PATCH = HERE.parent / "xnu" / "patches" / "0157-sweet-usbroot-ro.patch"


def make_image(path, size=2 << 20):
    data = bytearray()
    i = 0
    while len(data) < size:                 # every 512-byte sector differs: a wrong lba/offset is visible
        data += hashlib.sha256(b"sector%d" % i).digest() * 16
        i += 1
    pathlib.Path(path).write_bytes(bytes(data[:size]))


class MockLink:
    """Both ends of the wire. `fault(frame, direction)` may return a list of frames to deliver instead."""

    def __init__(self):
        self.to_host, self.to_phone = queue.Queue(), queue.Queue()
        self.fault = lambda frame, direction: [frame]
        self.sent = {"h2p": 0, "p2h": 0}

    def host_end(self):
        link = self

        class T:
            def recv(self, timeout):
                try:
                    return link.to_host.get_nowait()
                except queue.Empty:
                    return None

            def send(self, frame):
                for f in link.fault(frame, "h2p"):
                    link.sent["h2p"] += 1
                    link.to_phone.put(f)
        return T()


class PhoneModel:
    """Python mirror of ur_xfer / sweet_usbroot_rx; the host server is pumped inline (single thread, deterministic)."""

    def __init__(self, link, server, tries=4):
        self.link, self.server, self.tries, self.seq = link, server, tries, 0
        self.attempts = 0

    def _pump(self):
        host = self.link.host_end()
        while True:
            try:
                frame = self.link.to_host.get_nowait()
            except queue.Empty:
                return
            for r in self.server.handle(frame):
                host.send(r)

    def xfer(self, op, lba, total):
        for _ in range(self.tries):
            self.attempts += 1
            self.seq = (self.seq + 1) & 0xFFFFFFFF or 1
            req = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, op, 0, self.seq, total, lba, 0, b"")
            req = req.ljust(60, b"\0")
            for f in self.link.fault(req, "p2h"):
                self.link.sent["p2h"] += 1
                self.link.to_host.put(f)
            self._pump()
            buf, mask, full = bytearray(total), 0, (1 << -(-total // uh.FRAG)) - 1
            status = None
            while True:
                try:
                    frame = self.link.to_phone.get_nowait()
                except queue.Empty:
                    break
                r = uh.parse(frame, uh.MAGIC_REP)
                if (r is None or r["dst"] != uh.PHONE_MAC or r["src"] != uh.HOST_MAC or r["seq"] != self.seq
                        or r["op"] != op or r["total"] != total or r["lba"] != lba):
                    continue
                if r["status"]:
                    if r["payload"]:
                        continue
                    status = r["status"]
                    break
                o, n = r["frag_off"], len(r["payload"])
                if o % uh.FRAG or o >= total or n != min(uh.FRAG, total - o):
                    continue
                buf[o:o + n] = r["payload"]
                mask |= 1 << (o // uh.FRAG)
                if mask == full:
                    status = 0
                    break
            while not self.link.to_phone.empty():       # late frames of this attempt: dropped, as the C code does
                self.link.to_phone.get_nowait()
            if status is not None:
                return status, bytes(buf) if status == 0 else b""
        return None, b""

    def read(self, off, n):
        status, data = self.xfer(uh.OP_READ, off // 512, n)
        return status, data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usbroot-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "root.img")
        make_image(self.path)
        os.chmod(self.path, 0o444)
        self.raw = pathlib.Path(self.path).read_bytes()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.image = uh.Image(self.path)
        self.addCleanup(self.image.close)
        self.server = uh.Server(self.image)
        self.link = MockLink()
        self.phone = PhoneModel(self.link, self.server)


class Protocol(Base):
    def test_info(self):
        status, data = self.phone.xfer(uh.OP_INFO, 0, uh.INFO_LEN)
        self.assertEqual(status, 0)
        self.assertEqual(struct.unpack("<QII", data), (len(self.raw) // 512, uh.READ_MAX, uh.FRAG))

    def test_reads_all_sizes_and_offsets(self):
        for off, n in [(0, 4096), (4096, 4096), (8192, 16384), (4096, 8192), (len(self.raw) - 16384, 16384),
                       (len(self.raw) - 4096, 4096), (12288, 12288)]:
            status, data = self.phone.read(off, n)
            self.assertEqual((status, data), (0, self.raw[off:off + n]), (off, n))
        self.assertEqual(self.phone.attempts, 7)        # one round trip each: reads of 4 KiB and more

    def test_every_reply_is_one_fragment_per_kib(self):
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 5, 16384, 0, 0, b"")
        replies = self.server.handle(q)
        self.assertEqual(len(replies), 16)
        self.assertTrue(all(len(r) == 14 + uh.HDR + uh.FRAG for r in replies))

    def test_refusals_carry_a_status_code(self):
        cases = [
            (uh.OP_READ, 0, 512, uh.ST_BADLEN),                        # under 4 KiB
            (uh.OP_READ, 0, 4096 + 512, uh.ST_BADLEN),
            (uh.OP_READ, 0, 0, uh.ST_BADLEN),
            (uh.OP_READ, 0, uh.READ_MAX + 4096, uh.ST_BADLEN),
            (uh.OP_READ, 1, 4096, uh.ST_BADLEN),                       # lba not a multiple of 8
            (uh.OP_READ, len(self.raw) // 512, 4096, uh.ST_RANGE),     # first sector past the end
            (uh.OP_READ, len(self.raw) // 512 - 8, 8192, uh.ST_RANGE),  # straddles the end
            (uh.OP_READ, 2 ** 64 - 8, 4096, uh.ST_RANGE),              # lba * 512 does not fit 64 bits in C
            (uh.OP_INFO, 0, 8, uh.ST_BADLEN),
            (3, 0, 4096, uh.ST_BADOP),
            (0, 0, 4096, uh.ST_BADOP),
            (255, 0, 4096, uh.ST_BADOP),                               # no write opcode exists
        ]
        for op, lba, total, want in cases:
            q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, op, 0, 9, total, lba, 0, b"")
            r = self.server.handle(q)
            self.assertEqual(len(r), 1, (op, lba, total))
            p = uh.parse(r[0], uh.MAGIC_REP)
            self.assertEqual((p["status"], p["payload"], p["seq"]), (want, b"", 9), (op, lba, total))

    def test_request_with_payload_or_offset_is_refused(self):
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 9, 4096, 0, 0, b"x" * 8)
        self.assertEqual(uh.parse(self.server.handle(q)[0], uh.MAGIC_REP)["status"], uh.ST_BADLEN)
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 9, 4096, 0, 1024, b"")
        self.assertEqual(uh.parse(self.server.handle(q)[0], uh.MAGIC_REP)["status"], uh.ST_BADLEN)

    def test_malformed_frames_get_no_reply(self):
        good = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 7, 4096, 0, 0, b"")
        self.assertEqual(len(self.server.handle(good)), 4)
        bad = {
            "truncated": good[:14 + uh.HDR - 1],
            "empty": b"",
            "crc": good[:-1] + bytes([good[-1] ^ 1]),
            "magic": good[:14] + b"PXUA" + good[18:],
            "version": good[:18] + b"\x02" + good[19:],
            "reply magic as request": uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REP, uh.OP_READ, 0, 7, 4096, 0, 0, b""),
            "wrong dst": uh.build(b"\xff" * 6, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 7, 4096, 0, 0, b""),
            "wrong src": uh.build(uh.HOST_MAC, b"\x02\0\0\0\0\x09", uh.MAGIC_REQ, uh.OP_READ, 0, 7, 4096, 0, 0, b""),
            "seq 0": uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 0, 4096, 0, 0, b""),
            "ethertype": good[:12] + b"\x08\x00" + good[14:],
        }
        flip = bytearray(good)
        flip[14 + 12] ^= 0x10                    # header byte covered by the CRC
        bad["header bit flip"] = bytes(flip)
        for name, frame in bad.items():
            self.assertEqual(self.server.handle(frame), [], name)
        self.assertEqual(self.server.ignored, len(bad))

    def test_header_length_beyond_frame_is_ignored(self):
        # frag_len claims 8 bytes but the frame ends at the header
        head = struct.pack("<4sBBHIIQIHH", uh.MAGIC_REQ, 1, uh.OP_READ, 0, 3, 4096, 0, 0, 8, 0)
        frame = uh.HOST_MAC + uh.PHONE_MAC + b"\x88\xb6" + head + struct.pack("<I", uh.frame_crc(head, b""))
        self.assertEqual(self.server.handle(frame), [])


class Faults(Base):
    def test_lost_fragment_retries_with_a_new_sequence(self):
        dropped = []

        def fault(frame, direction):
            p = uh.parse(frame, uh.MAGIC_REP) if direction == "h2p" else None
            if p and not dropped and p["frag_off"] == 2048:
                dropped.append(p["seq"])
                return []
            return [frame]
        self.link.fault = fault
        self.assertEqual(self.phone.read(0, 4096), (0, self.raw[:4096]))
        self.assertEqual(self.phone.attempts, 2)

    def test_corrupted_fragment_is_dropped_and_retried(self):
        hit = []

        def fault(frame, direction):
            if direction == "h2p" and not hit:
                hit.append(1)
                return [frame[:-1] + bytes([frame[-1] ^ 0xFF])]
            return [frame]
        self.link.fault = fault
        self.assertEqual(self.phone.read(4096, 8192), (0, self.raw[4096:12288]))
        self.assertEqual(self.phone.attempts, 2)

    def test_stale_and_duplicate_replies_do_not_satisfy_a_newer_request(self):
        stash = []

        def fault(frame, direction):
            p = uh.parse(frame, uh.MAGIC_REP) if direction == "h2p" else None
            if p is None:
                return [frame]
            if not stash or p["seq"] == stash[0][0]:
                stash.append((p["seq"], frame))     # the first attempt's replies are all lost
                return []
            return [stash[0][1], frame, stash[0][1]]  # the retry gets old-sequence copies around every fragment
        self.link.fault = fault
        self.assertEqual(self.phone.read(0, 4096), (0, self.raw[:4096]))
        self.assertEqual(self.phone.attempts, 2)

    def test_reordered_fragments_complete(self):
        def fault(frame, direction):
            return [frame]
        self.link.fault = fault
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 11, 4096, 8, 0, b"")
        replies = self.server.handle(q)[::-1]
        buf = bytearray(4096)
        for r in replies:
            p = uh.parse(r, uh.MAGIC_REP)
            buf[p["frag_off"]:p["frag_off"] + len(p["payload"])] = p["payload"]
        self.assertEqual(bytes(buf), self.raw[4096:8192])

    def test_total_loss_gives_up_after_four_tries(self):
        self.link.fault = lambda frame, direction: []
        self.assertEqual(self.phone.read(0, 4096), (None, b""))
        self.assertEqual(self.phone.attempts, 4)

    def test_error_status_is_final_without_retry(self):
        self.assertEqual(self.phone.read(len(self.raw), 4096), (uh.ST_RANGE, b""))
        self.assertEqual(self.phone.attempts, 1)

    def test_io_error_is_reported_as_status_io(self):
        class Broken(uh.Image):
            def read(self, offset, length):
                raise OSError("boom")
        self.server.image.close()
        self.server.image = Broken(self.path)
        self.addCleanup(self.server.image.close)
        self.assertEqual(self.phone.read(0, 4096), (uh.ST_IO, b""))
        self.assertEqual(self.server.io_errors, 1)


class ReadOnly(Base):
    def test_image_is_never_modified(self):
        for off in range(0, len(self.raw), 65536):
            self.phone.read(off, 16384)
        self.phone.xfer(uh.OP_INFO, 0, uh.INFO_LEN)
        self.image.close()
        self.assertEqual(hashlib.sha256(pathlib.Path(self.path).read_bytes()).hexdigest(), self.digest)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o444)

    def test_image_is_opened_rb_only_and_read_only_file_works(self):
        self.assertEqual(self.image.f.mode, "rb")
        with self.assertRaises(Exception):
            self.image.f.write(b"x")

    def test_tool_source_has_no_write_path(self):
        src = (HERE / "usbroot-host.py").read_text()
        calls = re.findall(r"(?<![\w.])open\(([^)]*)\)", src)
        self.assertEqual(len(calls), 2)                               # the image ("rb") and the sysfs MAC read
        for c in calls:
            self.assertTrue(c.endswith(', "rb"') or "," not in c, c)
        self.assertNotRegex(src, r"\.write\(|os\.write|ftruncate|os\.remove|unlink|os\.rename")

    def test_image_size_rules(self):
        for size in (0, 4095, 4097):
            p = os.path.join(self.tmp, "bad%d" % size)
            pathlib.Path(p).write_bytes(b"\0" * size)
            with self.assertRaises(uh.Refusal):
                uh.Image(p)


class Serve(Base):
    def test_serve_loop_answers_over_a_transport_and_stops(self):
        link = MockLink()
        host = link.host_end()
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 1, 4096, 0, 0, b"")
        link.to_host.put(q)
        ticks = iter(range(100))
        uh.serve(self.server, host, max_seconds=5, clock=lambda: next(ticks))
        self.assertEqual(link.to_phone.qsize(), 4)
        self.assertEqual(self.server.served, 1)

    def _flaky(self, errors):
        """Transport that delivers one READ request, then raises the given OSErrors from send()."""
        import errno as _e
        q = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 1, 4096, 0, 0, b"")

        class T:
            sent, left, given = [], list(errors), False

            def recv(self, timeout):
                if T.given:
                    return None
                T.given = True
                return q

            def send(self, frame):
                if T.left:
                    raise OSError(T.left.pop(0), "injected")
                T.sent.append(frame)

            def close(self):
                pass
        return T

    def test_enobufs_is_retried_once_then_dropped(self):
        import errno as _e
        T = self._flaky([_e.ENOBUFS])
        ticks = iter(range(100))
        uh.serve(self.server, T(), max_seconds=3, clock=lambda: next(ticks))
        self.assertEqual(len(T.sent), 4)                      # first send retried, nothing lost
        T = self._flaky([_e.ENOBUFS] * 2)
        ticks = iter(range(100))
        uh.serve(self.server, T(), max_seconds=3, clock=lambda: next(ticks))
        self.assertEqual(len(T.sent), 3)                      # one frame dropped, the others still go out

    def test_serve_itself_still_propagates_send_errors(self):
        import errno as _e
        T = self._flaky([_e.ENETDOWN])
        with self.assertRaises(OSError):
            uh.serve(self.server, T(), max_seconds=3)

    def test_main_refuses_without_the_phone_interface(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = uh.main([self.path, "--iface", "lo"])
        self.assertEqual(code, 2)
        self.assertTrue(buf.getvalue().strip().splitlines()[-1].startswith("RESULT: ERROR"))

    def test_main_bad_image_is_an_error(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = uh.main([os.path.join(self.tmp, "missing")])
        self.assertEqual(code, 2)


class StartupImageCheck(Base):
    """usbroot-host runs usbroot-image-check at startup: non-PASS = USBROOT GAP line, serving continues."""
    def run_check(self, image):
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gaps = uh.startup_image_check(image)
        return gaps, err.getvalue()

    def test_non_hfs_image_is_a_gap_but_not_fatal(self):
        gaps, err = self.run_check(self.path)      # the synthetic test image has no HFS+ header
        self.assertEqual(gaps, 1)
        self.assertIn("usbroot-image-check: RESULT: ERROR", err)
        self.assertIn("USBROOT GAP: usbroot-image-check RESULT: ERROR", err)

    def test_pass_prints_result_and_no_gap(self):
        img = os.path.join(self.tmp, "hfs.img")
        vh = bytearray(512); vh[0:2] = b"H+"; vh[4:8] = struct.pack(">I", 0x100); vh[8:12] = b"10.0"; vh[40:44] = struct.pack(">I", 4096)
        with open(img, "wb") as f:
            f.write(b"\0" * 1024 + bytes(vh) + b"\0" * 4096)
        gaps, err = self.run_check(img)
        self.assertEqual(gaps, 0, err)
        self.assertIn("usbroot-image-check: RESULT: PASS", err)
        self.assertNotIn("USBROOT GAP", err)

    def test_missing_tool_is_a_gap(self):
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gaps = uh.startup_image_check(self.path, tool=os.path.join(self.tmp, "nope.py"))
        self.assertEqual(gaps, 1)
        self.assertIn("USBROOT GAP: usbroot-image-check RESULT: ERROR", err.getvalue())

    def test_main_runs_the_check_and_keeps_serving(self):
        import io, contextlib
        saved = (uh.check_raw_capability, uh.find_iface, uh.time.sleep)
        uh.check_raw_capability, uh.find_iface = (lambda: None, lambda: None)
        uh.time.sleep = lambda d: None
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                uh.main([self.path, "--max-seconds", "0.3"])
        finally:
            uh.check_raw_capability, uh.find_iface, uh.time.sleep = saved
        self.assertIn("USBROOT GAP: usbroot-image-check", err.getvalue())
        self.assertIn("RESULT: ERROR no interface", out.getvalue())   # got past the check into the serve path


class Clock:
    """Fake time: every read advances 0.05 s, sleep() advances by its argument."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def __call__(self):
        self.t += 0.05
        return self.t

    def sleep(self, d):
        self.slept.append(d)
        self.t += d


class Fake:
    """A scripted interface: .name/.index/.mac change over time, .sock is the current inner transport."""

    def __init__(self, req):
        self.name, self.index, self.mac = None, None, uh.HOST_MAC
        self.opened, self.closed, self.lines, self.req = [], 0, [], req
        self.inner_errors = []                 # OSErrors the next inner recv() raises, in order
        self.inner_frames = []                 # frames the next inner recv() returns
        self.sent = []
        self.open_errors = []

    def finder(self):
        return self.name

    def mac_of(self, n):
        return self.mac if n == self.name else None

    def index_of(self, n):
        return self.index

    def opener(self, n):
        if self.open_errors:
            raise OSError(self.open_errors.pop(0), "injected")
        self.opened.append((n, self.index))
        fake = self

        class Inner:
            def recv(self, timeout):
                if fake.inner_errors:
                    raise OSError(fake.inner_errors.pop(0), "injected")
                return fake.inner_frames.pop(0) if fake.inner_frames else None

            def send(self, frame):
                if fake.inner_errors:
                    raise OSError(fake.inner_errors.pop(0), "injected")
                fake.sent.append(frame)

            def close(self):
                fake.closed += 1
        return Inner()


class Reconnect(Base):
    def setUp(self):
        super().setUp()
        self.clk = Clock()
        self.fake = Fake(None)
        self.fake.req = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 1, 4096, 0, 0, b"")
        self.at = uh.AutoTransport(None, self.fake.opener, self.fake.finder, self.fake.mac_of, self.fake.index_of,
                                   self.clk, self.clk.sleep, self.fake.lines.append)

    def test_waits_for_an_interface_that_does_not_exist_yet(self):
        uh.serve(self.server, self.at, max_seconds=3, clock=self.clk)
        self.assertEqual(self.at.binds, 0)
        self.assertEqual(self.fake.opened, [])
        self.assertTrue(self.clk.slept and max(self.clk.slept) <= 1.0)          # polls at most 1 s apart
        self.assertEqual(sum("waiting for the interface" in l for l in self.fake.lines), 1)   # logged once
        # now it appears mid-run and gets served
        self.fake.name, self.fake.index = "enx0", 7
        self.fake.inner_frames = [self.fake.req]
        uh.serve(self.server, self.at, max_seconds=3, clock=self.clk)
        self.assertEqual(self.fake.opened, [("enx0", 7)])
        self.assertEqual(self.server.served, 1)
        self.assertEqual(len(self.fake.sent), 4)
        self.assertTrue(any("bound enx0 (ifindex 7), bind #1" in l for l in self.fake.lines))

    def test_down_up_rebinds_and_keeps_serving(self):
        import errno as _e
        self.fake.name, self.fake.index = "enx0", 7
        self.fake.inner_errors = [_e.ENETDOWN]
        self.fake.inner_frames = [self.fake.req]
        self.at.recv(0.2)                                    # binds, then recv raises ENETDOWN: dropped, not raised
        self.assertIsNone(self.at.t)
        self.assertEqual(self.fake.closed, 1)
        self.at.send(b"x" * 60)                              # unbound: dropped silently
        self.assertEqual(self.fake.sent, [])
        self.clk.t += 2
        uh.serve(self.server, self.at, max_seconds=2, clock=self.clk)
        self.assertEqual(self.at.binds, 2)                   # rebound
        self.assertEqual(self.server.served, 1)
        self.assertTrue(any("lost the interface" in l and "recv" in l for l in self.fake.lines))

    def test_send_enetdown_drops_the_frame_and_rebinds(self):
        import errno as _e
        self.fake.name, self.fake.index = "enx0", 7
        self.assertIsNone(self.at.recv(0.2))
        self.fake.inner_errors = [_e.ENODEV]
        self.at.send(b"y" * 60)
        self.assertIsNone(self.at.t)
        self.clk.t += 2
        self.at.recv(0.2)
        self.assertEqual(self.at.binds, 2)

    def test_ifindex_change_and_vanish_rebind(self):
        self.fake.name, self.fake.index = "enx0", 7
        self.at.recv(0.2)
        self.clk.t += 2
        self.fake.index = 9                                  # same name, new ifindex (unplug/replug)
        self.at.recv(0.2)
        self.assertEqual(self.fake.opened, [("enx0", 7), ("enx0", 9)])
        self.clk.t += 2
        self.fake.name = None                                # interface vanished
        self.assertIsNone(self.at.recv(0.2))
        self.assertIsNone(self.at.t)
        self.clk.t += 2
        self.fake.name, self.fake.index = "enx1", 3          # came back under another name
        self.at.recv(0.2)
        self.assertEqual(self.at.binds, 3)
        self.assertEqual(self.at.name, "enx1")

    def test_open_failures_are_retried_other_errors_raise(self):
        import errno as _e
        self.fake.name, self.fake.index = "enx0", 7
        self.fake.open_errors = [_e.ENETDOWN, _e.ENODEV]
        for _ in range(3):
            self.at.recv(0.2)
            self.clk.t += 2
        self.assertEqual(self.at.binds, 1)
        self.fake.open_errors = [_e.EIO]
        self.at.t = None
        with self.assertRaises(OSError):
            self.at.recv(0.2)

    def test_wrong_mac_is_never_bound(self):
        self.fake.name, self.fake.index, self.fake.mac = "enx0", 7, bytes(6)
        self.at.recv(0.2)
        self.assertEqual(self.fake.opened, [])

    def test_enobufs_still_reaches_send_frame(self):
        import errno as _e
        self.fake.name, self.fake.index = "enx0", 7
        self.at.recv(0.2)
        self.fake.inner_errors = [_e.ENOBUFS]
        uh.send_frame(self.at, b"z" * 60)                    # retried once by send_frame
        self.assertEqual(len(self.fake.sent), 1)

    def test_main_waits_then_reports_error_when_the_interface_never_appears(self):
        import io, contextlib
        saved = (uh.check_raw_capability, uh.find_iface, uh.time.sleep)
        uh.check_raw_capability, uh.find_iface = (lambda: None, lambda: None)
        uh.time.sleep = lambda d: None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = uh.main([self.path, "--max-seconds", "0.3"])
        finally:
            uh.check_raw_capability, uh.find_iface, uh.time.sleep = saved
        self.assertEqual(code, 2)
        self.assertIn("RESULT: ERROR no interface", buf.getvalue())

    def test_main_survives_down_up_end_to_end(self):
        import io, contextlib, errno as _e
        fake = self.fake
        fake.name, fake.index = "enx0", 7
        fake.inner_errors = [_e.ENETDOWN]
        fake.inner_frames = [fake.req]
        saved = (uh.check_raw_capability, uh.find_iface, uh.interface_mac, uh.iface_index, uh.RawTransport, uh.time.sleep)
        uh.check_raw_capability, uh.find_iface, uh.interface_mac, uh.iface_index = (
            lambda: None, fake.finder, fake.mac_of, fake.index_of)
        uh.RawTransport = fake.opener
        uh.time.sleep = lambda d: None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = uh.main([self.path, "--max-seconds", "0.5"])
        finally:
            (uh.check_raw_capability, uh.find_iface, uh.interface_mac, uh.iface_index, uh.RawTransport,
             uh.time.sleep) = saved
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("RESULT: PASS", buf.getvalue())
        self.assertIn("2 bind(s)", buf.getvalue())


class Constants(unittest.TestCase):
    """The C file in patch 0157 and this tool must agree; parsed from the patch's + lines, not typed twice (CR-03)."""

    def defines(self):
        text = PATCH.read_text()
        self.assertEqual(text.count("+++ b/bsd/dev/sweet_usbroot.c"), 1)
        out = {}
        for m in re.finditer(r"^\+#define\s+(UR_\w+)\s+(\(?[0-9a-fx<ul() ]+\)?)\s*(?:/\*.*)?$", text, re.M):
            out[m.group(1)] = eval(m.group(2).replace("ull", "").replace("u", ""))   # numeric literals and shifts only
        return text, out

    def test_constants_match_patch(self):
        text, d = self.defines()
        want = dict(UR_ETHERTYPE=uh.ETHERTYPE, UR_VERSION=uh.VERSION, UR_ETH_HDR=uh.ETH_HDR, UR_HDR=uh.HDR,
                    UR_CRC_SPAN=uh.CRC_SPAN, UR_FRAG=uh.FRAG, UR_READ_MIN=uh.READ_MIN, UR_READ_MAX=uh.READ_MAX,
                    UR_INFO_LEN=uh.INFO_LEN, UR_MIN_BYTES=uh.MIN_BYTES, UR_MAX_BYTES=uh.MAX_BYTES,
                    UR_OP_INFO=uh.OP_INFO, UR_OP_READ=uh.OP_READ, UR_ST_OK=uh.ST_OK, UR_ST_BADVER=uh.ST_BADVER,
                    UR_ST_BADOP=uh.ST_BADOP, UR_ST_BADLEN=uh.ST_BADLEN, UR_ST_RANGE=uh.ST_RANGE, UR_ST_IO=uh.ST_IO)
        for k, v in want.items():
            self.assertEqual(d.get(k), v, k)
        self.assertEqual(d["UR_TX_MIN"], 60)
        for mac, name in ((uh.HOST_MAC, "ur_host_mac"), (uh.PHONE_MAC, "ur_phone_mac")):
            lit = ", ".join("0x%02x" % b for b in mac)
            self.assertIn("%s[6] = { %s }" % (name, lit), text)
        self.assertIn("{ 'P', 'X', 'U', 'Q' }", text)
        self.assertIn("{ 'P', 'X', 'U', 'A' }", text)
        self.assertEqual((uh.MAGIC_REQ, uh.MAGIC_REP), (b"PXUQ", b"PXUA"))

    def test_phone_never_writes(self):
        text, _ = self.defines()
        self.assertIn("return EROFS", text)
        added = "\n".join(l for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        self.assertNotRegex(added, r"\b(dwc_wr|ufs_wr|ml_io_map|FlushPoC|DEPCMD)|\*\(volatile")
        self.assertIn("*f = 0;", text)                       # DKIOCISWRITABLE
        self.assertIn("(flags & FWRITE) ? EROFS : 0", text)

    def test_root_hook_and_exclusion_present(self):
        hook = (PATCH.parent / "0150-sweet-usbroot-root-hook.patch").read_text()
        self.assertIn('strcmp(rdBootVar, "usbroot0") == 0', hook)
        text = PATCH.read_text()
        self.assertIn("if (sweet_ufs_active())", text)
        self.assertIn("if (sweet_usbroot_active())", text)
        self.assertIn("rootrw", text)


class Wrapper(unittest.TextTestResult):
    pass


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1, resultclass=Wrapper).run(suite)
    print("RESULT: %s %d tests" % ("PASS" if res.wasSuccessful() else "FAIL", res.testsRun))
    sys.exit(0 if res.wasSuccessful() else 1)
