#!/usr/bin/env python3
"""Receive the shim's UDUMP stream (wire format in shim/udump.h).

Reads the CDC-ACM gadget tty (or a saved capture), finds the UDUMP-BEGIN
text marker, parses the framed binary records that follow, verifies every
per-record CRC32, the sequence/LBA continuity and the end record's
CRC32-of-all-payload, writes logs/udump-<UTC>.bin and prints the total
CRC32 and throughput. Exit status is nonzero on ANY mismatch.

Wire format (little endian; shim/udump.h is the reference):
  text line: UDUMP-BEGIN first=<lba> count=<blocks>
  record:    "UDMP" | seq u32 | lba u64 (absolute) | nblocks u32
             | crc32 u32 (zlib CRC32 of payload) | payload nblocks*4096
  end:       same header with nblocks = 0 and crc32 = CRC32 of ALL payload
  text line: UDUMP-END ...

Text lines are tolerated only before UDUMP-BEGIN and after the end
record; anything but a record between BEGIN and the end record is an
error (the dump must not corrupt the text log, and vice versa).

Python stdlib only. Run it BEFORE `fastboot boot` (same as
tools/usb-log.py capture) so the tty is open with DTR when the dump starts:
  python3 -I tools/udump-recv.py                # live, /dev/ttyACM0
  python3 -I tools/udump-recv.py --from logs/usb-<UTC>.txt
  python3 -I tools/udump-recv.py --selftest
"""

import argparse
import datetime
import errno
import fcntl
import os
import re
import select
import struct
import sys
import termios
import time
import zlib

VID = "1209"
PID = "0001"
DEFAULT_SYSFS = "/sys/bus/usb/devices"
DEFAULT_TIMEOUT = 600.0
DEFAULT_POLL = 0.5
DEFAULT_IDLE = 60.0
READ_SIZE = 4096

BLOCK = 4096
CHUNK_MAX = 16
HDR = struct.Struct("<4sIQII")  # magic, seq, lba, nblocks, crc32

TIOCMBIS = getattr(termios, "TIOCMBIS", 0x5417)
TIOCM_DTR = getattr(termios, "TIOCM_DTR", 0x002)
CLEAN_READ_ERRNOS = (errno.ENODEV, errno.EIO)


class DumpError(Exception):
    """Any mismatch with the UDUMP wire format or checksums."""


def parse_stream(data):
    """Parse a complete capture. Returns (first, count, payload bytes).

    Raises DumpError on any format, CRC, sequence, LBA or count problem.
    """
    i = data.find(b"UDUMP-BEGIN")
    if i < 0:
        raise DumpError("no UDUMP-BEGIN marker in the capture")
    nl = data.find(b"\n", i)
    if nl < 0:
        raise DumpError("UDUMP-BEGIN line not terminated")
    m = re.search(rb"first=(\d+) count=(\d+)", data[i:nl])
    if not m:
        raise DumpError("UDUMP-BEGIN line lacks first=/count= (%r)" % data[i:nl][:80])
    first, count = int(m.group(1)), int(m.group(2))
    pos = nl + 1
    out = bytearray()
    seq = 0
    blocks = 0
    while True:
        if pos + HDR.size > len(data):
            raise DumpError("truncated: record header cut at seq=%d" % seq)
        magic, rseq, lba, nb, crc = HDR.unpack_from(data, pos)
        if magic != b"UDMP":
            raise DumpError("not a record at seq=%d (magic %r; text between "
                            "records is not allowed)" % (seq, magic[:1]))
        if rseq != seq:
            raise DumpError("seq gap: expected %d, got %d" % (seq, rseq))
        if lba != first + blocks:
            raise DumpError("lba discontinuity at seq=%d: %d != %d"
                            % (seq, lba, first + blocks))
        pos += HDR.size
        if nb == 0:
            total = zlib.crc32(out) & 0xFFFFFFFF
            if crc != total:
                raise DumpError("end record CRC %08X != CRC32-of-all-payload "
                                "%08X" % (crc, total))
            break
        if nb > CHUNK_MAX:
            raise DumpError("record seq=%d has nblocks=%d > %d"
                            % (seq, nb, CHUNK_MAX))
        plen = nb * BLOCK
        if pos + plen > len(data):
            raise DumpError("truncated: payload cut at seq=%d" % seq)
        payload = data[pos:pos + plen]
        pcrc = zlib.crc32(payload) & 0xFFFFFFFF
        if pcrc != crc:
            raise DumpError("record seq=%d CRC %08X != computed %08X"
                            % (seq, crc, pcrc))
        out += payload
        blocks += nb
        pos += plen
        seq += 1
    if blocks != count:
        raise DumpError("UDUMP-BEGIN count=%d but %d blocks received"
                        % (count, blocks))
    tail = data[pos:]
    if b"UDUMP-END" not in tail:
        raise DumpError("no UDUMP-END line after the end record")
    return first, count, bytes(out)


# ---- tty discovery/open (same conventions as tools/usb-log.py) ----

def _read_stripped(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:   # interface directories (1-1:1.0) have no idVendor/idProduct: the normal "not this device"   # lint-silent: expected interface directories have no idVendor: not this device
        return None
    except OSError as _err:
        print(f"udump-recv GAP: OSError ignored in _read_stripped(): {_err}", file=sys.stderr)
        return None


def find_tty_node(dev_dir):
    for dirpath, dirnames, _filenames in os.walk(dev_dir):
        if os.path.basename(dirpath) != "tty":
            continue
        for entry in sorted(dirnames):
            if entry.startswith("tty"):
                return os.path.join(dirpath, entry)
    return None


def scan_once(sysfs):
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:   # lint-silent: expected the sysfs directory is absent (no device): None means not present
        return None
    for name in entries:
        dev = os.path.join(sysfs, name)
        if not os.path.isdir(dev):
            continue
        if _read_stripped(os.path.join(dev, "idVendor")) != VID:
            continue
        if _read_stripped(os.path.join(dev, "idProduct")) != PID:
            continue
        node = find_tty_node(dev)
        if node is not None:
            return node
    return None


def wait_for_tty(sysfs, timeout, poll):
    deadline = time.monotonic() + timeout
    while True:
        node = scan_once(sysfs)
        if node is not None:
            return node
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def setup_tty(fd):
    """Raw mode + DTR (the gadget streams only when DTR is set)."""
    if not os.isatty(fd):
        return
    try:
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                   | termios.ISTRIP | termios.INLCR | termios.IGNCR
                   | termios.ICRNL | termios.IXON)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON
                   | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB)
        cflag |= termios.CS8
        cc[termios.VMIN] = 1
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW,
                          [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        fcntl.ioctl(fd, TIOCMBIS, struct.pack("I", TIOCM_DTR))
    except (termios.error, OSError) as exc:
        print("udump-recv: warning: tty setup failed: %s" % exc, file=sys.stderr)


def capture_tty(fd, node, idle_limit, poll):
    """Read until UDUMP-END arrives, the link dies, or the stream goes idle.

    Returns (data, seconds_between_first_record_and_end).
    """
    buf = bytearray()
    t_first = None
    t_end = None
    last_data = time.monotonic()
    while True:
        readable, _, _ = select.select([fd], [], [], poll)
        if readable:
            try:
                data = os.read(fd, READ_SIZE)
            except OSError as exc:
                if exc.errno in CLEAN_READ_ERRNOS:
                    break
                raise
            if not data:
                break
            last_data = time.monotonic()
            if t_first is None and b"UDMP" in data:
                t_first = last_data
            if b"UDUMP-END" in data and t_end is None:
                t_end = last_data
            buf += data
            if t_end is not None and time.monotonic() - t_end > 2.0:
                break  # END seen; allow 2 s for the trailing UDUMP-TIME line
        if node is not None and not os.path.exists(node):
            break
        if t_end is None and time.monotonic() - last_data > idle_limit:
            break
    for ln in bytes(buf).splitlines():
        if b"UDUMP-TIME" in ln:
            print(ln.decode("ascii", "replace"))
    dt = (t_end - t_first) if (t_first is not None and t_end is not None) else None
    return bytes(buf), dt


def default_out_path(now=None):
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime(
        "%Y%m%dT%H%M%SZ")
    return os.path.join("logs", "udump-%s.bin" % ts)


def report(first, count, payload, path, dt):
    mib = len(payload) / (1 << 20)
    rate = " rate=%.2f MiB/s" % (mib / dt) if dt else ""
    print("UDUMP OK first=%d blocks=%d bytes=%d crc32=%08X%s"
          % (first, count, len(payload), zlib.crc32(payload) & 0xFFFFFFFF, rate))
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(payload)
        print("saved %s" % path)


# ---- self-test: synthetic streams, no files ----

def _pattern(lba, i):
    return (lba * 131 + i * 7 + (lba >> 8)) & 0xFF


def _blocks(first, lo, n):
    out = bytearray()
    for lba in range(first + lo, first + lo + n):
        out += bytes(_pattern(lba, i) for i in range(BLOCK))
    return bytes(out)


def _record(seq, lba, nb, crc, payload=b""):
    return HDR.pack(b"UDMP", seq, lba, nb, crc) + payload


def _good_stream(first=0x40000, counts=(16, 8)):
    s = bytearray(b"boot noise line\r\nUDUMP noise\r\n")
    s += b"UDUMP-BEGIN first=%d count=%d\r\n" % (first, sum(counts))
    total = bytearray()
    lba = first
    for seq, nb in enumerate(counts):
        payload = _blocks(first, lba - first, nb)
        total += payload
        s += _record(seq, lba, nb, zlib.crc32(payload) & 0xFFFFFFFF, payload)
        lba += nb
    s += _record(len(counts), lba, 0, zlib.crc32(bytes(total)) & 0xFFFFFFFF)
    s += b"UDUMP-END total=DEADBEEF blocks=%d\r\ntrailing text\r\n" % sum(counts)
    return bytes(s), first, sum(counts), bytes(total)


def _rec_offsets(stream, counts):
    """offs[k] = offset of record k's magic; offs[-1] = end record."""
    o = stream.find(b"UDMP")
    offs = [o]
    for nb in counts:
        o += HDR.size + nb * BLOCK
        offs.append(o)
    return offs


def _flip(stream, off):
    return stream[:off] + bytes([stream[off] ^ 0xFF]) + stream[off + 1:]


def selftest():
    good, first, count, payload = _good_stream()
    f, c, p = parse_stream(good)
    assert (f, c, p) == (first, count, payload), "good stream must parse"
    offs = _rec_offsets(good, (16, 8))

    cases = [
        ("no BEGIN marker", lambda: b"log without any marker\r\n",
         "no UDUMP-BEGIN"),
        ("bad record CRC",
         lambda: _flip(good, offs[0] + HDR.size + 100),
         "record seq=0 CRC"),
        ("seq gap",
         lambda: good[:offs[1] + 4] + struct.pack("<I", 5) + good[offs[1] + 8:],
         "seq gap"),
        ("lba discontinuity",
         lambda: good[:offs[1] + 8] + struct.pack("<Q", first + 99) + good[offs[1] + 16:],
         "lba discontinuity"),
        ("truncated payload",
         lambda: good[:offs[1] + HDR.size + 1000],
         "truncated"),
        ("text between records",
         lambda: good[:offs[1]] + b"XXXX" + good[offs[1] + 4:],
         "not a record"),
        ("end CRC mismatch",
         lambda: good[:offs[2] + 20] + struct.pack("<I", 0xDEADBEEF) + good[offs[2] + 24:],
         "end record CRC"),
        ("count mismatch",
         lambda: good.replace(b"count=24", b"count=99", 1),
         "count=99"),
    ]
    caught = 0
    for label, fn, needle in cases:
        try:
            parse_stream(bytes(fn()))
        except DumpError as exc:
            if needle and needle not in str(exc):
                raise AssertionError("selftest %s: wrong error: %s" % (label, exc))
            caught += 1
            print("self-test caught: %s -> %s" % (label, exc))
        else:
            raise AssertionError("udump-recv self-test FAILED to catch: %s" % label)
    print("PASS udump-recv self-test: good stream parsed; %d/%d bad streams "
          "rejected" % (caught, len(cases)))
    return caught


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="udump-recv.py",
        description="Receive and verify the shim's UDUMP userdata dump "
                    "(see shim/udump.h for the wire format).")
    parser.add_argument("--sysfs", default=DEFAULT_SYSFS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for the gadget (default %(default)s)")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL)
    parser.add_argument("--idle", type=float, default=DEFAULT_IDLE,
                        help="stop after this many seconds without data "
                             "(default %(default)s)")
    parser.add_argument("--tty-path", default=None,
                        help="open this tty instead of the discovered node")
    parser.add_argument("--from", dest="from_file", default=None,
                        help="parse a saved capture file instead of the tty")
    parser.add_argument("--out", default=None,
                        help="output file (default: logs/udump-<UTC>.bin)")
    parser.add_argument("--no-save", action="store_true",
                        help="verify only; do not write the dump file")
    parser.add_argument("--selftest", action="store_true",
                        help="parse synthetic streams and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        selftest()
        return 0

    try:
        if args.from_file:
            with open(args.from_file, "rb") as f:
                data = f.read()
            dt = None
        else:
            node = wait_for_tty(args.sysfs, args.timeout, args.poll)
            if node is None:
                print("udump-recv: timed out after %gs waiting for %s:%s under %s"
                      % (args.timeout, VID, PID, args.sysfs), file=sys.stderr)
                return 2
            dev = args.tty_path or ("/dev/" + os.path.basename(node))
            fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY)
            try:
                setup_tty(fd)
                data, dt = capture_tty(fd, node, args.idle, args.poll)
            finally:
                os.close(fd)
        first, count, payload = parse_stream(data)
    except DumpError as exc:
        print("udump-recv: MISMATCH: %s" % exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print("udump-recv: %s" % exc, file=sys.stderr)
        return 1

    report(first, count, payload,
           None if args.no_save else (args.out or default_out_path()), dt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
