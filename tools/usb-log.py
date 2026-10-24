#!/usr/bin/env python3
"""Host-side helper for the USB gadget serial log (stage 2, CDC-ACM).

Subcommands:
  wait     poll sysfs for the 1209:0001 gadget and print its tty node
  capture  tee the gadget's serial output to stdout and a log file
  extract  pass a file through unchanged (test helper)

Python stdlib only: no pyserial, no root. The gadget must enumerate as
CDC-ACM so the host sees /dev/ttyACM0 without extra drivers.
"""

import argparse
import datetime
import errno
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time

VID = "1209"
PID = "0001"

DEFAULT_SYSFS = "/sys/bus/usb/devices"
DEFAULT_TIMEOUT = 600.0
DEFAULT_POLL = 0.5
READ_SIZE = 4096

# Not exposed by the termios module on every platform; Linux values.
TIOCMBIS = getattr(termios, "TIOCMBIS", 0x5417)
TIOCM_DTR = getattr(termios, "TIOCM_DTR", 0x002)

# A disconnected serial device raises these or returns EOF; all mean gone.
CLEAN_READ_ERRNOS = (errno.ENODEV, errno.EIO)


def _read_stripped(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
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
    except OSError:
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


def wait_for_tty(sysfs, timeout=DEFAULT_TIMEOUT, poll=DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while True:
        node = scan_once(sysfs)
        if node is not None:
            return node
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def make_raw(fd):
    """Apply cfmakeraw(3) to an open tty file descriptor."""
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


def raise_dtr(fd):
    fcntl.ioctl(fd, TIOCMBIS, struct.pack("I", TIOCM_DTR))


def setup_tty(fd):
    """Raw mode + DTR; best effort outside a real serial device."""
    if not os.isatty(fd):
        return
    try:
        make_raw(fd)
        raise_dtr(fd)
    except (termios.error, OSError) as exc:
        print("usb-log: warning: tty setup failed: %s" % exc, file=sys.stderr)


def default_out_path(now=None):
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime(
        "%Y%m%dT%H%M%SZ")
    return os.path.join("logs", "usb-%s.txt" % ts)


class LineTee:
    def __init__(self, out_file, out_stream):
        self.out_file = out_file
        self.out_stream = out_stream
        self.pending = bytearray()
        self.count = 0

    def feed(self, data):
        self.pending += data
        while True:
            nl = self.pending.find(b"\n")
            if nl < 0:
                break
            line = bytes(self.pending[:nl + 1])
            del self.pending[:nl + 1]
            self._emit(line)

    def finish(self):
        if self.pending:
            line, self.pending = bytes(self.pending), bytearray()
            self._emit(line)

    def _emit(self, line):
        self.out_file.write(line)
        self.out_file.flush()
        self.out_stream.write(line)
        self.out_stream.flush()
        self.count += len(line)


def drain(fd, tee, quiet=0.3):
    """read what is still buffered in the tty after the device went away (the last lines are the ones that matter)"""
    deadline = time.monotonic() + 2.0  # bounded: a device that keeps streaming must not hold the capture
    while time.monotonic() < deadline and select.select([fd], [], [], quiet)[0]:
        try:
            data = os.read(fd, READ_SIZE)
        except OSError:
            return
        if not data:
            return
        tee.feed(data)


def capture(node, tty_path, out_path, out_stream, poll=DEFAULT_POLL):
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(tty_path, os.O_RDONLY | os.O_NOCTTY)
    try:
        setup_tty(fd)
        with open(out_path, "wb") as out_file:
            tee = LineTee(out_file, out_stream)
            try:
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
                        tee.feed(data)
                    if node is not None and not os.path.exists(node):
                        drain(fd, tee)  # the node vanished with data still queued: keep it
                        break
            finally:
                tee.finish()  # also on SIGTERM / Ctrl-C: the last partial line is kept
    finally:
        os.close(fd)
    return tee.count


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="usb-log.py",
        description="Host-side USB gadget serial log tool (%s:%s)."
                    % (VID, PID))
    parser.add_argument("--sysfs", default=DEFAULT_SYSFS,
                        help="USB device sysfs root (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for the device "
                             "(default: %(default)s)")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL,
                        help="sysfs poll interval in seconds "
                             "(default: %(default)s)")
    parser.add_argument("--tty-path", dest="tty_path", default=None,
                        help="open this path instead of the discovered "
                             "/dev node")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("wait", help="wait for the gadget, print its tty node")
    cap = sub.add_parser("capture", help="tee serial output to stdout+file")
    cap.add_argument("--out", default=None,
                     help="log file (default: logs/usb-<UTC stamp>.txt)")
    ext = sub.add_parser("extract", help="pass a file through (test helper)")
    ext.add_argument("file")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.command == "extract":
        try:
            with open(args.file, "rb") as f:
                sys.stdout.buffer.write(f.read())
            sys.stdout.buffer.flush()
        except OSError as exc:
            print("usb-log: %s" % exc, file=sys.stderr)
            return 1
        return 0

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # probe-run kills the capture: let capture() flush its last line
    node = wait_for_tty(args.sysfs, args.timeout, args.poll)
    if node is None:
        print("usb-log: timed out after %gs waiting for %s:%s under %s"
              % (args.timeout, VID, PID, args.sysfs), file=sys.stderr)
        return 2
    dev = "/dev/" + os.path.basename(node)
    if args.command == "wait":
        print(dev)
        return 0

    out_path = args.out or default_out_path()
    try:
        count = capture(node, args.tty_path or dev, out_path,
                        sys.stdout.buffer, args.poll)
    except OSError as exc:
        print("usb-log: capture failed: %s" % exc, file=sys.stderr)
        return 1
    print("captured %d bytes to %s" % (count, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
