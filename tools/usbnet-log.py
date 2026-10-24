#!/usr/bin/env python3
"""Receive the phone's live kernel console over USB Ethernet (UDP 5140, patch 0075) into a timestamped file.

  tools/usbnet-log.py --out FILE [--port 5140] [--until-file F] [--duration S] [--quiet]

Needs no root (port > 1024). The interface itself still needs 192.168.7.1/24: `tools/sweet-usbnet.sh --stage` prints the one
sudo line for that. FILE gets one line per console line: "<unix time with ms>\\t<text>" (time of the datagram that started the
line; a line split over datagrams is reassembled). The phone replays its last 64 KiB each time the link comes up, so a
re-plug repeats lines. Stops on SIGINT/SIGTERM, when --until-file exists, or after --duration seconds.
"""
import argparse, os, signal, socket, sys, threading, time


def feed(buf, t0, data, out, now):
    """buf = unfinished line bytes, t0 = its start time. Writes complete lines to out. -> (buf, t0)."""
    data = buf + data
    if not buf:
        t0 = now
    *done, rest = data.split(b"\n")
    for ln in done:
        out.write(b"%.3f\t%s\n" % (t0, ln.rstrip(b"\r")))
        t0 = now
    return rest, (t0 if rest else None)


def run(out_path, port=5140, until_file=None, duration=None, quiet=False, ready=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.5)
    if ready:
        ready(sock.getsockname()[1])
    stop = []
    if threading.current_thread() is threading.main_thread():
        for s in (signal.SIGINT, signal.SIGTERM):
            signal.signal(s, lambda *_: stop.append(1))
    end = time.time() + duration if duration else None
    buf, t0, n = b"", None, 0
    with open(out_path, "ab", buffering=0) as out:
        while not stop and not (until_file and os.path.exists(until_file)) and not (end and time.time() > end):
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            n += len(data)
            buf, t0 = feed(buf, t0, data, out, time.time())
            if not quiet:
                sys.stdout.buffer.write(data); sys.stdout.flush()
        sock.settimeout(0.3)
        drain_end = time.time() + 2.0  # bounded: a sender that keeps streaming must not hold xrun (and the phone lock)
        while time.time() < drain_end:  # datagrams already queued when the stop file appeared are the last lines of the run: keep them
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                break
            n += len(data)
            buf, t0 = feed(buf, t0, data, out, time.time())
            if not quiet:
                sys.stdout.buffer.write(data); sys.stdout.flush()
        if buf:
            out.write(b"%.3f\t%s\n" % (t0, buf.rstrip(b"\r")))
    sock.close()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=5140)
    ap.add_argument("--until-file")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--quiet", action="store_true", help="do not echo to stdout")
    a = ap.parse_args()
    print("listening on udp/%d -> %s" % (a.port, a.out), file=sys.stderr)
    got = run(a.out, a.port, a.until_file, a.duration, a.quiet)
    print("usbnet-log: %d bytes received" % got, file=sys.stderr)
