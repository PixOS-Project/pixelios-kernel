"""Host-side speed of tools/usbroot-host.py (docs/specs/SPEC-USBROOT-FAST.md): cache, read-ahead, stats, loud logs.

A fake phone (FakePhone) is the transport of the real serve() loop: it sends one v1 request at a time, checks every
reply fragment the way sweet_usbroot_rx does (xnu/patches/0157-sweet-usbroot-ro.patch: magic, CRC, seq/op/total/lba
echo, frag_len == min(1024, rest)) and compares the bytes with the image. The read pattern is shaped like USB-root
probe 4 (16 KiB reads, ~94% next-16-KiB, a few long runs; research 63 and the spec), scaled to a small test image.
No root, no network, no phone. Speed lines are printed, not asserted (timing on a shared host is noisy).

  python3 -I tools/test_usbroot_fast.py                       unit tests + probe-4-shaped replay, RESULT line last
  python3 -I tools/test_usbroot_fast.py --image IMG --log merged.log [--limit N]
        replay the `request op 2 lba 0x.. total N` lines of an xrun log against a real image (read-only), cache off
        vs on; reports host service time. Needs only read access to IMG.
"""
import hashlib, importlib.util, io, os, pathlib, random, re, shutil, sys, tempfile, time, unittest
from contextlib import redirect_stderr

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("usbroot_host", HERE / "usbroot-host.py")
uh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uh)
_tspec = importlib.util.spec_from_file_location("test_usbroot_host", HERE / "test_usbroot_host.py")
th = importlib.util.module_from_spec(_tspec)
_tspec.loader.exec_module(th)

KIB16 = 16384


def probe4_shaped(size, seed=4):
    """(lba, total) list: long sequential runs + short runs at random offsets, all 16 KiB (probe 4: 10131 reads,
    9552 sequential successors, 579 runs, the 4 longest 2048 reads each; spec section 1). Scaled to `size`."""
    rnd = random.Random(seed)
    slots = size // KIB16
    out = []
    long_run = max(8, slots // 16)
    for k in range(4):                                          # 4 long runs (probe 4: 4 x 32 MiB)
        start = rnd.randrange(0, slots - long_run)
        out += [((start + i) * KIB16 // 512, KIB16) for i in range(long_run)]
        for _ in range(40):                                     # short runs in between (probe 4: median run 2)
            start = rnd.randrange(0, slots - 3)
            out += [((start + i) * KIB16 // 512, KIB16) for i in range(rnd.choice((1, 1, 2, 2, 3)))]
    return out


class FakePhone:
    """Transport for uh.serve(): the phone end. One request outstanding, like ur_xfer."""

    def __init__(self, pattern, raw=None, reader=None):
        self.pattern, self.raw, self.reader = pattern, raw, reader
        self.i, self.seq, self.waiting = 0, 0, False
        self.buf = self.mask = self.full = None
        self.bad = self.mismatch = 0
        self.done = False

    def _request(self):
        lba, total = self.pattern[self.i]
        self.seq = (self.seq + 1) & 0xFFFFFFFF or 1
        self.buf, self.mask, self.full = bytearray(total), 0, (1 << -(-total // uh.FRAG)) - 1
        self.waiting = True
        return uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, self.seq, total, lba, 0,
                        b"").ljust(60, b"\0")

    def recv(self, timeout):
        if self.done or self.waiting:
            return None
        return self._request()

    def send(self, frame):
        lba, total = self.pattern[self.i]
        r = uh.parse(frame, uh.MAGIC_REP)
        if (r is None or r["dst"] != uh.PHONE_MAC or r["src"] != uh.HOST_MAC or r["seq"] != self.seq or
                r["op"] != uh.OP_READ or r["total"] != total or r["lba"] != lba or r["status"] != 0):
            self.bad += 1
            return
        o, n = r["frag_off"], len(r["payload"])
        if o % uh.FRAG or o >= total or n != min(uh.FRAG, total - o):
            self.bad += 1
            return
        self.buf[o:o + n] = r["payload"]
        self.mask |= 1 << (o // uh.FRAG)
        if self.mask == self.full:
            off = lba * 512
            want = self.raw[off:off + total] if self.raw is not None else self.reader(off, total)
            if bytes(self.buf) != want:
                self.mismatch += 1
            self.waiting = False
            self.i += 1
            self.done = self.i == len(self.pattern)

    def close(self):
        pass


class SlowImage(uh.Image):
    """Model of a backing store with a fixed cost per read call (cold ZFS ARC / disk). A model, not a measurement."""
    DELAY = 0.0005

    def read(self, offset, length):
        time.sleep(self.DELAY)
        return super().read(offset, length)


class CountingImage(uh.Image):
    calls = 0

    def read(self, offset, length):
        self.calls += 1
        return super().read(offset, length)


def replay(image, pattern, cache_mib, chunk_kib=256, raw=None, reader=None):
    cache = uh.BlockCache(image, cache_mib << 20, chunk_kib << 10, say=lambda m: None) if cache_mib else None
    server = uh.Server(image, cache, say=lambda m: None)
    phone = FakePhone(pattern, raw=raw, reader=reader)
    t0 = time.perf_counter()
    uh.serve(server, phone, stop=lambda: phone.done)
    wall = time.perf_counter() - t0
    return server, phone, wall


class Base(unittest.TestCase):
    SIZE = 32 << 20

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="usbroot-fast-")
        cls.path = os.path.join(cls.tmp, "root.img")
        th.make_image(cls.path, cls.SIZE)
        os.chmod(cls.path, 0o444)
        cls.raw = pathlib.Path(cls.path).read_bytes()
        cls.pattern = probe4_shaped(cls.SIZE)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)


class Replay(Base):
    def test_cached_replay_is_byte_exact_and_reads_the_file_less(self):
        img0, img1 = CountingImage(self.path), CountingImage(self.path)
        self.addCleanup(img0.close)
        self.addCleanup(img1.close)
        s0, p0, _ = replay(img0, self.pattern, 0, raw=self.raw)
        s1, p1, _ = replay(img1, self.pattern, 64, raw=self.raw)
        for s, p in ((s0, p0), (s1, p1)):
            self.assertTrue(p.done)
            self.assertEqual((p.bad, p.mismatch, s.gaps()), (0, 0, 0))
            self.assertEqual(s.served, len(self.pattern))
            self.assertEqual(s.service.n, len(self.pattern))
            self.assertEqual(s.phone_time.n, len(self.pattern) - 1)
        self.assertEqual(img0.calls, len(self.pattern))
        self.assertLess(img1.calls * 4, img0.calls)               # 256 KiB chunks: one file read per ~16 requests
        self.assertGreater(s1.cache.prefetch_used, 0)
        self.assertGreater(s1.cache.sequential, len(self.pattern) // 2)

    def test_small_cache_evicts_and_stays_correct(self):
        img = uh.Image(self.path)
        self.addCleanup(img.close)
        s, p, _ = replay(img, self.pattern, 1, chunk_kib=256, raw=self.raw)   # 4 chunks only
        self.assertEqual((p.done, p.bad, p.mismatch), (True, 0, 0))
        self.assertGreater(s.cache.evicted, 0)
        self.assertLessEqual(len(s.cache.chunks), 4)

    def test_chunk_straddling_and_tail_chunk(self):
        img = uh.Image(self.path)
        self.addCleanup(img.close)
        c = uh.BlockCache(img, 1 << 20, 48 << 10, say=lambda m: None)    # chunk not a power of two: requests straddle
        for off, n in ((32768, 16384), (40960, 16384), (len(self.raw) - 4096, 4096), (len(self.raw) - 16384, 16384)):
            self.assertEqual(c.read(off, n), self.raw[off:off + n], (off, n))
        with self.assertRaises(OSError):
            c.read(len(self.raw) - 4096, 8192)

    def test_bad_cache_geometry_is_refused(self):
        img = uh.Image(self.path)
        self.addCleanup(img.close)
        for cap, chunk in ((1 << 20, 4096), (1 << 20, 20480), (8192, 16384)):
            with self.assertRaises(uh.Refusal):
                uh.BlockCache(img, cap, chunk)


class Loud(Base):
    def test_unexpected_frames_refusals_and_repeats_are_logged(self):
        img = uh.Image(self.path)
        self.addCleanup(img.close)
        said = []
        s = uh.Server(img, uh.BlockCache(img, 1 << 20, 256 << 10, say=said.append), say=said.append)
        good = uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 7, 4096, 0, 0, b"")
        bad = [good[:20], good[:12] + b"\x08\x00" + good[14:], good[:-1] + bytes([good[-1] ^ 1]),
               uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REP, uh.OP_READ, 0, 7, 4096, 0, 0, b""),
               uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, uh.OP_READ, 0, 0, 4096, 0, 0, b"")]
        reasons = ["shorter than", "EtherType 0x0800", "CRC mismatch", "magic b'PXUA'", "seq 0"]
        for f in bad:
            self.assertEqual(s.handle(f), [])
        for r in reasons:
            self.assertTrue(any("USBROOT GAP: ignored frame" in m and r in m for m in said), r)
        s.handle(uh.build(uh.HOST_MAC, uh.PHONE_MAC, uh.MAGIC_REQ, 9, 0, 8, 4096, 0, 0, b""))
        self.assertTrue(any("USBROOT GAP: refused request" in m and "status 2" in m for m in said))
        s.handle(good)
        s.handle(good)
        self.assertTrue(any(m.startswith("repeated read #1 of lba 0x0") for m in said))
        lines = s.stats_lines()
        self.assertTrue(lines[-1].startswith("USBROOT GAP: 6 unexpected event(s): 5 ignored frame(s), 1 refusal(s)"))
        self.assertEqual(s.gaps(), 6)

    def test_image_change_drops_the_cache_loudly(self):
        path = os.path.join(self.tmp, "rw.img")
        shutil.copyfile(self.path, path)
        img = uh.Image(path)
        self.addCleanup(img.close)
        said, now = [], [0.0]
        c = uh.BlockCache(img, 1 << 20, 256 << 10, say=said.append, clock=lambda: now[0])
        self.assertEqual(c.read(0, 4096), self.raw[:4096])
        with open(path, "r+b") as f:                            # the test edits its own copy, never the tool
            f.write(b"\xff" * 4096)
        os.utime(path, ns=(1, 1))
        self.assertEqual(c.read(0, 4096), self.raw[:4096])      # within CHECK_S: still the cached bytes
        now[0] = 2.0
        self.assertEqual(c.read(0, 4096), b"\xff" * 4096)
        self.assertEqual(c.changes, 1)
        self.assertTrue(any("USBROOT GAP: image" in m and "changed while serving" in m for m in said))

    def test_periodic_stats_lines(self):
        img = uh.Image(self.path)
        self.addCleanup(img.close)
        said = []
        s = uh.Server(img, None, say=said.append)
        phone = FakePhone(self.pattern[:50], raw=self.raw)
        ticks = iter(range(10 ** 6))
        uh.serve(s, phone, stop=lambda: phone.done, clock=lambda: next(ticks) * 0.5, stats_every=10, say=said.append)
        self.assertTrue(any(m.startswith("STATS: host service time per request: n ") for m in said))
        self.assertTrue(any(m == "STATS: cache off (--cache-mib 0)" for m in said))


class ProtocolWithCache(th.Protocol):
    """Every protocol test of test_usbroot_host.py again, with the cache in front of the image."""

    def setUp(self):
        super().setUp()
        self.server = uh.Server(self.image, uh.BlockCache(self.image, 1 << 20, 64 << 10, say=lambda m: None),
                                say=lambda m: None)
        self.phone = th.PhoneModel(self.link, self.server)


def speed_report(path, raw, pattern, label):
    rows = []
    for name, cls in (("page-cached file", uh.Image), ("slow-store model %.1f ms/read call" % (SlowImage.DELAY * 1e3),
                                                         SlowImage)):
        res = {}
        for mib in (0, 64):
            img = cls(path)
            try:
                s, p, wall = replay(img, pattern, mib, raw=raw)
            finally:
                img.close()
            if p.bad or p.mismatch or not p.done:
                print("FAIL %s cache %d: bad %d mismatch %d done %s" % (name, mib, p.bad, p.mismatch, p.done))
                return False
            res[mib] = (s.service.total / s.service.n, wall, s)
        a, b = res[0][0], res[64][0]
        rows.append("SPEEDUP [%s, %s, %d reads]: host service mean %.1f us (cache off) -> %.1f us (cache on), x%.2f; "
                    "%s" % (label, name, len(pattern), a, b, a / b if b else 0, res[64][2].cache.summary()))
    for r in rows:
        print(r)
    return True


def replay_log(image_path, log_path, limit):
    pat = re.compile(r"request op 2 lba 0x([0-9a-f]+) total (\d+) seq \d+ attempt 0")
    pattern = []
    with open(log_path, errors="replace") as f:
        for line in f:
            m = pat.search(line)
            if m:
                pattern.append((int(m.group(1), 16), int(m.group(2))))
                if limit and len(pattern) >= limit:
                    break
    if not pattern:
        print("RESULT: ERROR no `request op 2` lines in %s" % log_path)
        return 2
    check = uh.Image(image_path)
    try:
        rows = []
        for mib in (0, 256):
            img = uh.Image(image_path)
            try:
                s, p, wall = replay(img, pattern, mib, reader=check.read)
            finally:
                img.close()
            if p.bad or p.mismatch or not p.done:
                print("RESULT: FAIL cache %d: bad %d mismatch %d" % (mib, p.bad, p.mismatch))
                return 1
            rows.append((mib, s, wall))
            print("REPLAY cache %d MiB: %d reads, wall %.2f s; host service: %s" % (mib, len(pattern), wall,
                                                                                s.service.text()))
            if s.cache is not None:
                print("REPLAY " + s.cache.summary())
    finally:
        check.close()
    a, b = (r[1].service.total / r[1].service.n for r in rows)
    print("RESULT: PASS replayed %d reads byte-exact; host service mean %.1f us -> %.1f us (x%.2f)" % (
        len(pattern), a, b, a / b if b else 0))
    return 0


def main(argv):
    if "--image" in argv:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--image", required=True)
        ap.add_argument("--log", required=True)
        ap.add_argument("--limit", type=int, default=0)
        a = ap.parse_args(argv)
        return replay_log(a.image, a.log, a.limit)
    buf = io.StringIO()
    with redirect_stderr(buf):
        res = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    tmp = tempfile.mkdtemp(prefix="usbroot-fast-speed-")
    try:
        path = os.path.join(tmp, "root.img")
        th.make_image(path, Base.SIZE)
        raw = pathlib.Path(path).read_bytes()
        ok = speed_report(path, raw, probe4_shaped(Base.SIZE), "probe-4-shaped")
    finally:
        shutil.rmtree(tmp)
    good = res.wasSuccessful() and ok
    print("RESULT: %s %d tests%s" % ("PASS" if good else "FAIL", res.testsRun, "" if ok else ", speed replay failed"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
