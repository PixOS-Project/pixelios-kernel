#!/usr/bin/env python3
"""usbroot-host: serve ONE file, strictly read-only, as the phone's USB root disk (patches 0157/0150).

  tools/usbroot-host.py IMAGE [--iface NAME] [--max-seconds N] [--idle-exit N]

Protocol v1 (docs/research/44-usbroot-readonly.md): raw Ethernet frames, EtherType 0x88B6, little-endian,
one request outstanding. Frame = 14 byte Ethernet header + 36 byte header + payload; header:
  0 magic "PXUQ" (request) / "PXUA" (reply)   4 version=1   5 op (1 INFO, 2 READ)   6 status u16 (reply)
  8 seq u32   12 total u32   16 lba u64 (512 byte sectors)   24 frag_off u32   28 frag_len u16   30 zero u16
  32 crc32 u32 over bytes 0..31 followed by the fragment payload
INFO (total 16) replies sectors u64, max_read u32, frag u32. READ (lba % 8 == 0, total a multiple of 4096, at
most 16384) replies in fragments of 1024 bytes. Every reply echoes op, seq, total and lba. A request that is
refused gets ONE reply with a status code and no payload (1 version, 2 op, 3 length, 4 range, 5 io); a request
that fails the CRC, MAC or magic check gets no reply (the phone retries with a fresh sequence).
The image is only ever opened "rb"; there is no write opcode.

Speed (docs/specs/SPEC-USBROOT-FAST.md): image bytes are read in aligned chunks (--chunk-kib, default 256) into an
in-memory LRU cache (--cache-mib, default 256; 0 = no cache, every request reads the file as before), and after a
reply is sent the chunk after a sequential access is read ahead while the phone is busy. The cache drops itself and
logs when the image's size or mtime changes. The wire protocol is unchanged (v1, one request outstanding).
Every frame that is not a valid request, every refusal, I/O error and repeated read is logged on stderr; the end
summary prints STATS lines (host service time, phone+USB time between a reply and the next request, cache) and a
`USBROOT GAP:` line when anything unexpected happened; RESULT carries gaps=N.
At startup tools/usbroot-image-check.py runs on the image (60 s timeout); a non-PASS result prints a `USBROOT GAP:` line, counts in gaps=N and serving continues.

Needs CAP_NET_RAW (AF_PACKET). Without it the tool stops with RESULT: ERROR and exit 2; give the capability
yourself, this tool never escalates. The interface is the one whose MAC is 02:00:5e:77:ee:01 (the phone's ECM
host MAC, xnu/patches/0073-sweet-usb-ecm.patch); nothing is sent on any other interface.
The interface may not exist yet and may go down/up or vanish/return as the phone reboots: the tool waits for it
(polling at most every 1 s, bounded by --max-seconds, 0 = unbounded), rebinds its socket on ENETDOWN/ENODEV/ifindex
change and logs every (re)bind with a timestamp on stderr; it never exits on those errors.
Last line RESULT: PASS|FAIL|ERROR. Exit 0 served until the window ended, 1 a request failed to be served
(I/O error), 2 usage/permission/setup error or the interface never appeared within --max-seconds.
"""
import errno
import argparse, collections, os, signal, socket, struct, subprocess, sys, time, zlib

ETHERTYPE = 0x88B6
VERSION = 1
HDR = 36
ETH_HDR = 14
CRC_SPAN = 32
FRAG = 1024
READ_MIN = 4096
READ_MAX = 16384
INFO_LEN = 16
MIN_BYTES = 1 << 20
MAX_BYTES = 64 << 30
OP_INFO, OP_READ = 1, 2
ST_OK, ST_BADVER, ST_BADOP, ST_BADLEN, ST_RANGE, ST_IO = 0, 1, 2, 3, 4, 5
HOST_MAC = bytes.fromhex("02005e77ee01")
PHONE_MAC = bytes.fromhex("02005e77ee02")
MAGIC_REQ, MAGIC_REP = b"PXUQ", b"PXUA"
SECTOR = 512


class Refusal(Exception):
    """The image cannot be served (size rules); setup error."""


def frame_crc(head32, payload):
    return zlib.crc32(payload, zlib.crc32(head32)) & 0xFFFFFFFF


def build(dst, src, magic, op, status, seq, total, lba, frag_off, payload):
    head = struct.pack("<4sBBHIIQIHH", magic, VERSION, op, status, seq, total, lba, frag_off, len(payload), 0)
    assert len(head) == CRC_SPAN
    body = head + struct.pack("<I", frame_crc(head, payload)) + payload
    return dst + src + struct.pack(">H", ETHERTYPE) + body


def parse(frame, want_magic):
    """Header fields of a valid frame, or None. Everything is checked before a byte is used."""
    if len(frame) < ETH_HDR + HDR or struct.unpack(">H", frame[12:14])[0] != ETHERTYPE:
        return None
    h = frame[ETH_HDR:]
    magic, ver, op, status, seq, total, lba, frag_off, frag_len, rsv = struct.unpack("<4sBBHIIQIHH", h[:CRC_SPAN])
    if magic != want_magic or ver != VERSION or rsv != 0 or len(h) - HDR < frag_len:
        return None
    payload = h[HDR:HDR + frag_len]
    if struct.unpack("<I", h[CRC_SPAN:HDR])[0] != frame_crc(h[:CRC_SPAN], payload):
        return None
    return dict(op=op, status=status, seq=seq, total=total, lba=lba, frag_off=frag_off, payload=payload,
                dst=frame[0:6], src=frame[6:12])


class Image:
    """A file opened read-only. The only file operations are seek/read."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")           # read-only by construction: no write mode exists in this tool
        size = os.fstat(self.f.fileno()).st_size
        if size % READ_MIN or size == 0 or size > MAX_BYTES:
            self.f.close()
            raise Refusal("image size %d must be a non-zero multiple of %d and at most %d" % (size, READ_MIN, MAX_BYTES))
        self.size = size

    def read(self, offset, length):
        self.f.seek(offset)
        data = self.f.read(length)
        if len(data) != length:
            raise OSError("short read at %d" % offset)
        return data

    def signature(self):
        st = os.fstat(self.f.fileno())
        return st.st_size, st.st_mtime_ns

    def close(self):
        self.f.close()


class BlockCache:
    """Read-only LRU cache of aligned image chunks in front of Image, plus sequential read-ahead.

    read() is served from chunks of `chunk` bytes (the last one may be shorter); a miss reads the whole chunk with
    one Image.read. When a request ends where the previous one ended, the chunk after the one holding its last
    byte is queued and read by idle() (called by serve() after the reply frames are sent). check() compares the
    image's size and mtime at most every CHECK_S seconds and drops every chunk when they changed (logged)."""

    CHECK_S = 1.0

    def __init__(self, image, cap_bytes, chunk, say=None, clock=time.monotonic):
        if chunk < READ_MAX or chunk % READ_MAX:
            raise Refusal("chunk %d must be a multiple of %d" % (chunk, READ_MAX))
        if cap_bytes < chunk:
            raise Refusal("cache %d smaller than one chunk %d" % (cap_bytes, chunk))
        self.image, self.size, self.chunk, self.cap = image, image.size, chunk, cap_bytes // chunk
        self.say = say or log
        self.clock = clock
        self.chunks = collections.OrderedDict()
        self.pending = None
        self.last_end = None
        self.sig = image.signature()
        self.checked = clock()
        self.hits = self.misses = self.prefetched = self.prefetch_used = self.evicted = 0
        self.file_bytes = 0
        self.sequential = 0
        self.reads = 0
        self.changes = 0
        self._from_prefetch = set()

    def _load(self, idx):
        start = idx * self.chunk
        data = self.image.read(start, min(self.chunk, self.size - start))
        self.file_bytes += len(data)
        self.chunks[idx] = data
        while len(self.chunks) > self.cap:
            old, _ = self.chunks.popitem(last=False)
            self._from_prefetch.discard(old)
            self.evicted += 1
        return data

    def _get(self, idx):
        data = self.chunks.get(idx)
        if data is None:
            self.misses += 1
            return self._load(idx)
        self.hits += 1
        if idx in self._from_prefetch:
            self._from_prefetch.discard(idx)
            self.prefetch_used += 1
        self.chunks.move_to_end(idx)
        return data

    def check(self):
        now = self.clock()
        if now - self.checked < self.CHECK_S:
            return
        self.checked = now
        sig = self.image.signature()
        if sig != self.sig:
            self.changes += 1
            self.say("USBROOT GAP: image %s changed while serving (size/mtime %s -> %s); dropped %d cached chunk(s). "
                     "The phone may hold stale metadata: reboot it" % (self.image.path, self.sig, sig, len(self.chunks)))
            self.sig = sig
            self.size = min(self.size, sig[0])
            self.chunks.clear()
            self._from_prefetch.clear()
            self.pending = None

    def read(self, offset, length):
        self.check()
        if offset + length > self.size:
            raise OSError("read past the image end at %d" % offset)
        self.reads += 1
        if offset == self.last_end:
            self.sequential += 1
            nxt = (offset + length) // self.chunk + 1
            if nxt * self.chunk < self.size and nxt not in self.chunks:
                self.pending = nxt
        self.last_end = offset + length
        out = []
        pos, end = offset, offset + length
        while pos < end:
            idx = pos // self.chunk
            data = self._get(idx)
            skip = pos - idx * self.chunk
            take = min(end - pos, len(data) - skip)
            out.append(memoryview(data)[skip:skip + take])     # no copy: handle() slices fragments from it
            pos += take
        return out[0] if len(out) == 1 else b"".join(out)

    def idle(self):
        """Read ahead the queued chunk (if any). An I/O error here is logged and left for the real request."""
        idx, self.pending = self.pending, None
        if idx is None or idx in self.chunks:
            return
        try:
            self._load(idx)
        except OSError as e:
            self.say("read-ahead of chunk %d failed: %s (the request itself will report it)" % (idx, e))
            return
        self.prefetched += 1
        self._from_prefetch.add(idx)

    def summary(self):
        return ("cache: chunk %d KiB, %d/%d chunks held, hits %d, misses %d, read-ahead %d (used %d), evicted %d, "
                "%.1f MiB read from the image file, sequential requests %d of %d, image changes %d" % (
                    self.chunk >> 10, len(self.chunks), self.cap, self.hits, self.misses, self.prefetched,
                    self.prefetch_used, self.evicted, self.file_bytes / 1048576.0, self.sequential, self.reads,
                    self.changes))


class Hist:
    """Bounded log2 histogram of durations in microseconds (a service runs for days: no per-request list)."""

    def __init__(self):
        self.b = [0] * 40
        self.n = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, seconds):
        us = max(0.0, seconds * 1e6)
        self.b[min(len(self.b) - 1, int(us).bit_length())] += 1
        self.n += 1
        self.total += us
        self.max = max(self.max, us)

    def pct(self, p):
        """Upper bound (us) of the bucket that holds the p-th percentile."""
        want, seen = p / 100.0 * self.n, 0
        for i, c in enumerate(self.b):
            seen += c
            if c and seen >= want:
                return float(1 << i)
        return self.max

    def text(self, unit=1000.0, name="ms"):
        if not self.n:
            return "n 0"
        return "n %d mean %.3f p50<=%.3f p90<=%.3f p99<=%.3f max %.3f %s" % (
            self.n, self.total / self.n / unit, self.pct(50) / unit, self.pct(90) / unit, self.pct(99) / unit,
            self.max / unit, name)


class Server:
    def __init__(self, image, cache=None, say=None):
        self.image = image
        self.cache = cache                   # BlockCache in front of image, or None (read the file per request)
        self.say = say or log
        self.served = 0
        self.refused = 0
        self.io_errors = 0
        self.ignored = 0
        self.repeats = 0
        self.last_read = None
        self.service = Hist()                # request received -> last reply frame handed to the socket
        self.phone_time = Hist()             # last reply frame sent -> next request received (USB + phone side)
        self.bytes_served = 0

    @property
    def store(self):
        """BlockCache or the Image itself: both have read(offset, length)."""
        return self.cache if self.cache is not None else self.image

    def idle(self):
        if self.cache is not None:
            self.cache.idle()

    def gaps(self):
        changes = self.cache.changes if self.cache is not None else 0
        return self.ignored + self.refused + self.io_errors + changes

    def stats_lines(self):
        out = ["STATS: requests served %d (%.1f MiB), refused %d, ignored %d, io errors %d, repeated reads %d" % (
                   self.served, self.bytes_served / 1048576.0, self.refused, self.ignored, self.io_errors, self.repeats),
               "STATS: host service time per request: %s" % self.service.text(),
               "STATS: reply sent -> next request (USB + phone): %s" % self.phone_time.text()]
        if self.cache is not None:
            out.append("STATS: " + self.cache.summary())
        else:
            out.append("STATS: cache off (--cache-mib 0)")
        if self.gaps():
            out.append("USBROOT GAP: %d unexpected event(s): %d ignored frame(s), %d refusal(s), %d io error(s), "
                       "%d image change(s); each is logged above" % (
                           self.gaps(), self.ignored, self.refused, self.io_errors,
                           self.cache.changes if self.cache is not None else 0))
        return out

    def _reply(self, q, status, payload=b"", frag_off=0, total=None):
        return build(PHONE_MAC, HOST_MAC, MAGIC_REP, q["op"], status, q["seq"], q["total"] if total is None else total,
                     q["lba"], frag_off, payload)

    def handle(self, frame):
        """One received frame -> list of reply frames (possibly none)."""
        q = parse(frame, MAGIC_REQ)
        if q is None or q["dst"] != HOST_MAC or q["src"] != PHONE_MAC or q["seq"] == 0:
            self.ignored += 1
            self.say("USBROOT GAP: ignored frame #%d (%s), %d bytes, head %s" % (
                self.ignored, reject_reason(frame), len(frame), bytes(frame[:ETH_HDR + HDR]).hex()))
            return []
        if q["frag_off"] != 0 or q["payload"]:
            return self._refuse(q, ST_BADLEN)
        if q["op"] == OP_INFO:
            if q["total"] != INFO_LEN or q["lba"] != 0:
                return self._refuse(q, ST_BADLEN)
            info = struct.pack("<QII", self.image.size // SECTOR, READ_MAX, FRAG)
            self.served += 1
            return [self._reply(q, ST_OK, info)]
        if q["op"] != OP_READ:
            return self._refuse(q, ST_BADOP)
        total, lba = q["total"], q["lba"]
        if total == 0 or total > READ_MAX or total % READ_MIN or lba % (READ_MIN // SECTOR):
            return self._refuse(q, ST_BADLEN)
        off = lba * SECTOR
        if off >= self.image.size or total > self.image.size - off:
            return self._refuse(q, ST_RANGE)
        if self.last_read == (lba, total):
            self.repeats += 1
            self.say("repeated read #%d of lba 0x%x total %d, seq %d (a lost reply makes the phone retry after "
                     "2000 ms; see the phone-side time in STATS)" % (self.repeats, lba, total, q["seq"]))
        self.last_read = (lba, total)
        try:
            data = self.store.read(off, total)
        except OSError as e:
            self.io_errors += 1
            self.say("USBROOT GAP: I/O error #%d reading lba 0x%x total %d: %s; replied status %d" % (
                self.io_errors, lba, total, e, ST_IO))
            return [self._reply(q, ST_IO)]
        self.served += 1
        self.bytes_served += total
        return [self._reply(q, ST_OK, data[o:o + FRAG], o) for o in range(0, total, FRAG)]

    def _refuse(self, q, status):
        self.refused += 1
        self.say("USBROOT GAP: refused request #%d op %d seq %d lba 0x%x total %d frag_off %d payload %d: status %d" % (
            self.refused, q["op"], q["seq"], q["lba"], q["total"], q["frag_off"], len(q["payload"]), status))
        return [self._reply(q, status)]


def reject_reason(frame):
    """Why handle() ignored a frame (for the log only; parse() is the check)."""
    if len(frame) < ETH_HDR + HDR:
        return "shorter than %d bytes" % (ETH_HDR + HDR)
    if struct.unpack(">H", frame[12:14])[0] != ETHERTYPE:
        return "EtherType 0x%04x" % struct.unpack(">H", frame[12:14])[0]
    h = frame[ETH_HDR:]
    magic, ver, _op, _st, seq, _t, _l, _fo, frag_len, rsv = struct.unpack("<4sBBHIIQIHH", h[:CRC_SPAN])
    if magic != MAGIC_REQ:
        return "magic %r" % magic
    if ver != VERSION:
        return "version %d" % ver
    if rsv != 0:
        return "reserved field %d" % rsv
    if len(h) - HDR < frag_len:
        return "frag_len %d beyond the frame" % frag_len
    if struct.unpack("<I", h[CRC_SPAN:HDR])[0] != frame_crc(h[:CRC_SPAN], h[HDR:HDR + frag_len]):
        return "CRC mismatch"
    if frame[0:6] != HOST_MAC or frame[6:12] != PHONE_MAC:
        return "MAC dst %s src %s" % (bytes(frame[0:6]).hex(), bytes(frame[6:12]).hex())
    if seq == 0:
        return "seq 0"
    return "unknown"


def interface_mac(name):
    try:
        with open("/sys/class/net/%s/address" % name) as f:
            return bytes.fromhex(f.read().strip().replace(":", ""))
    except (FileNotFoundError, NotADirectoryError):   # /sys/class/net entries without an address file (bonding_masters, a vanished interface): not the phone   # lint-silent: expected sysfs entry without an address file is not the phone interface
        return None
    except (OSError, ValueError) as _err:
        print(f"usbroot-host GAP: (OSError, ValueError) ignored in interface_mac(): {_err}", file=sys.stderr)
        return None


def find_iface():
    for name in sorted(os.listdir("/sys/class/net")):
        if interface_mac(name) == HOST_MAC:
            return name
    return None


class RawTransport:
    """AF_PACKET socket bound to one interface and the private EtherType."""

    def __init__(self, iface):
        self.s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETHERTYPE))
        self.s.bind((iface, ETHERTYPE))

    def recv(self, timeout):
        self.s.settimeout(timeout)
        try:
            return self.s.recv(4096)
        except socket.timeout:
            return None

    def send(self, frame):
        self.s.send(frame)

    def close(self):
        self.s.close()


RETRY_ERRNOS = (errno.ENETDOWN, errno.ENODEV, errno.ENXIO, errno.ENOENT, errno.EADDRNOTAVAIL)
POLL = 1.0


def stamp():
    t = time.time()
    return "%s.%03d" % (time.strftime("%H:%M:%S", time.localtime(t)), int(t * 1000) % 1000)


def log(msg):
    print("[%s] usbroot-host: %s" % (stamp(), msg), file=sys.stderr, flush=True)


class AutoTransport:
    """A transport that survives the interface going down/up, changing ifindex or not existing yet.

    The interface is found by MAC (or by --iface name), checked at most every POLL seconds; the AF_PACKET socket
    is (re)opened whenever the name or ifindex differs from the bound one and whenever recv/send fail with
    ENETDOWN/ENODEV/... . Nothing here ends the run: while unbound recv() sleeps and returns None, send() drops
    the frame (the phone retries). Every (re)bind and every loss is logged with a timestamp."""

    def __init__(self, iface=None, opener=None, finder=None, mac_of=None, index_of=None, clock=time.monotonic,
                 sleep=time.sleep, say=log):
        self.want = iface
        self.opener = opener or RawTransport
        self.finder = finder or find_iface
        self.mac_of = mac_of or interface_mac
        self.index_of = index_of or iface_index
        self.clock, self.sleep, self.say = clock, sleep, say
        self.t = None
        self.name = None
        self.index = None
        self.binds = 0
        self.checked = None
        self.waiting = False

    def _resolve(self):
        name = self.want or self.finder()
        if name is None or self.mac_of(name) != HOST_MAC:
            return None, None
        return name, self.index_of(name)

    def _drop(self, why):
        if self.t is not None:
            try:
                self.t.close()
            except OSError:   # lint-silent: expected closing a USB handle that is already gone
                pass
        self.t, self.name, self.index = None, None, None
        self.say("lost the interface (%s); waiting for it" % why)

    def _ensure(self):
        now = self.clock()
        if self.t is not None and self.checked is not None and now - self.checked < POLL:
            return True
        self.checked = now
        name, index = self._resolve()
        if self.t is not None:
            if (name, index) == (self.name, self.index):
                return True
            self._drop("now %s ifindex %s, was %s ifindex %s" % (name, index, self.name, self.index))
        if name is None:
            if not self.waiting:
                self.waiting = True
                self.say("waiting for the interface with MAC 02:00:5e:77:ee:01")
            return False
        try:
            self.t = self.opener(name)
        except PermissionError:
            raise
        except OSError as e:
            if e.errno not in RETRY_ERRNOS:
                raise
            self.say("cannot bind %s yet: %s" % (name, e))
            return False
        self.name, self.index = name, index
        self.binds += 1
        self.waiting = False
        self.say("bound %s (ifindex %s), bind #%d" % (name, index, self.binds))
        return True

    def recv(self, timeout):
        if not self._ensure():
            self.sleep(min(timeout, POLL))
            return None
        try:
            return self.t.recv(timeout)
        except OSError as e:
            if e.errno not in RETRY_ERRNOS:
                raise
            self._drop("recv: %s" % e)
            return None

    def send(self, frame):
        if self.t is None:
            return
        try:
            self.t.send(frame)
        except OSError as e:
            if e.errno not in RETRY_ERRNOS:
                raise
            self._drop("send: %s" % e)

    def close(self):
        if self.t is not None:
            self.t.close()
            self.t = None


def iface_index(name):
    try:
        return socket.if_nametoindex(name)
    except OSError as _err:
        if str(_err) != "no interface with this name":   # observed: CPython raises OSError(errno=None, that text) for an absent interface, the normal answer while it is not up yet (_ensure polls)
            print(f"usbroot-host GAP: OSError ignored in iface_index(): {_err}", file=sys.stderr)
        return None


def check_raw_capability():
    """Raises PermissionError without CAP_NET_RAW, before any waiting."""
    socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETHERTYPE)).close()


def serve(server, transport, max_seconds=0.0, idle_exit=0.0, stop=lambda: False, clock=time.monotonic,
          timer=time.perf_counter, stats_every=0.0, say=log):
    """Serve until the window ends, the phone was silent for idle_exit seconds (0 = never) or stop() is true.

    Per served request: host service time (frame received -> last reply frame handed to the socket) and the time
    from the previous reply to this request (USB + phone side) go into server's histograms; after the replies are
    sent the cache reads ahead (server.idle()). stats_every > 0 logs the STATS lines every N seconds of traffic."""
    start = last = clock()
    sent_at = None
    reported = (0, start)
    while not stop():
        now = clock()
        if max_seconds and now - start >= max_seconds:
            break
        if idle_exit and now - last >= idle_exit:
            break
        if stats_every and now - reported[1] >= stats_every:
            if server.served != reported[0]:
                for line in server.stats_lines():
                    say(line)
            reported = (server.served, now)
        frame = transport.recv(0.2)
        if frame is None:
            continue
        t0 = timer()
        replies = server.handle(frame)
        if replies:
            last = clock()
        for r in replies:
            send_frame(transport, r)
        if replies:
            t1 = timer()
            server.service.add(t1 - t0)
            if sent_at is not None:
                server.phone_time.add(t0 - sent_at)
            sent_at = t1
            server.idle()


def send_frame(transport, frame):
    """Send one reply. A full TX queue (ENOBUFS) is retried once after 1 ms, then the frame is dropped (the phone
    retries the request); any other OSError (interface gone) propagates to main, which ends with a RESULT line."""
    try:
        transport.send(frame)
    except OSError as e:
        if e.errno != errno.ENOBUFS:
            raise
        time.sleep(0.001)
        try:
            transport.send(frame)
        except OSError as e2:
            if e2.errno != errno.ENOBUFS:
                raise


def result(res, msg, code):
    print("RESULT: %s %s" % (res, msg))
    return code


IMAGE_CHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usbroot-image-check.py")


def startup_image_check(path, timeout=60, tool=IMAGE_CHECK):
    """Run tools/usbroot-image-check.py on the image before serving (read-only, subprocess). Prints its RESULT line on stderr;
    on anything but exit 0 prints a `USBROOT GAP:` line and returns 1 (serving continues: the patched read-only mount 0151 is
    what protects the image). Returns the number of gaps (0 or 1)."""
    try:
        r = subprocess.run([sys.executable, "-I", tool, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        rc = r.returncode
        text = (r.stdout + r.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as e:
        rc, text = -1, "RESULT: ERROR cannot run usbroot-image-check: %s" % e
    res = [l for l in text.splitlines() if l.startswith("RESULT:")]
    line = res[-1] if res else "RESULT: ERROR no RESULT line (exit %d)" % rc
    print("usbroot-image-check: %s" % line, file=sys.stderr)
    if rc == 0:
        return 0
    print("USBROOT GAP: usbroot-image-check %s (exit %d); serving anyway, the patched read-only mount (0151) protects the image" % (line, rc),
          file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image")
    ap.add_argument("--iface", help="interface (default: the one with MAC 02:00:5e:77:ee:01)")
    ap.add_argument("--max-seconds", type=float, default=0, help="stop after N seconds (0 = until SIGINT/SIGTERM)")
    ap.add_argument("--idle-exit", type=float, default=0, help="stop after N seconds without a served request (0 = never)")
    ap.add_argument("--cache-mib", type=int, default=256, help="in-memory chunk cache in MiB (0 = off, read the file per request)")
    ap.add_argument("--chunk-kib", type=int, default=256, help="cache chunk = read-ahead unit in KiB (multiple of 16)")
    ap.add_argument("--stats-every", type=float, default=60, help="log STATS every N seconds of traffic (0 = only at the end)")
    a = ap.parse_args(argv)
    if a.cache_mib < 0 or a.chunk_kib <= 0:
        return result("ERROR", "--cache-mib must be >= 0 and --chunk-kib > 0", 2)
    try:
        image = Image(a.image)
        cache = BlockCache(image, a.cache_mib << 20, a.chunk_kib << 10) if a.cache_mib else None
    except (OSError, Refusal) as e:
        return result("ERROR", "image: %s" % e, 2)
    if image.size < MIN_BYTES:
        print("warning: image under 1 MiB; the phone refuses it", file=sys.stderr)
    if a.iface and interface_mac(a.iface) not in (None, HOST_MAC):
        return result("ERROR", "%s does not have MAC 02:00:5e:77:ee:01, refusing to send" % a.iface, 2)
    try:
        check_raw_capability()
    except PermissionError:
        return result("ERROR", "AF_PACKET needs CAP_NET_RAW (run as root or grant the capability to the interpreter)", 2)
    except OSError as e:
        return result("ERROR", "socket: %s" % e, 2)
    startup_gaps = startup_image_check(a.image)
    transport = AutoTransport(a.iface)
    stopped = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.append(1))
    server = Server(image, cache)
    try:
        serve(server, transport, a.max_seconds, a.idle_exit, stop=lambda: bool(stopped), stats_every=a.stats_every)
    except PermissionError:
        return result("ERROR", "AF_PACKET needs CAP_NET_RAW (run as root or grant the capability to the interpreter)", 2)
    except OSError as e:
        return result("ERROR", "network: %s" % e, 2)
    finally:
        transport.close()
        image.close()
    if transport.binds == 0:
        return result("ERROR", "no interface with MAC 02:00:5e:77:ee:01 appeared (phone not enumerated?)", 2)
    for line in server.stats_lines():
        print(line)
    msg = "served %d requests, refused %d, ignored %d, io errors %d, %d bind(s), last on %s gaps=%d" % (
        server.served, server.refused, server.ignored, server.io_errors, transport.binds, transport.name or "-",
        server.gaps() + startup_gaps)
    return result("FAIL" if server.io_errors else "PASS", msg, 1 if server.io_errors else 0)


if __name__ == "__main__":
    sys.exit(main())
