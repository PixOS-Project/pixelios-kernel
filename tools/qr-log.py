#!/usr/bin/env python3
"""Read the phone's QR log (qrlog/qrlog.h) from a webcam, a video or image files.

Two frame formats are read. QL1: "QL1 <sid> <idx>/<n> <chunk crc32>\\n<chunk>" (a looping snapshot of the log tail; written to
logs/qr-<UTC>.txt once every frame of one snapshot is in). QL2 (qrlog/qrstream.h, tools/qrl2.py): base45 frames of a compressed,
append-only, repair-coded stream with up to 2 QR codes per screen; any ~1.2x frames rebuild it, in any order. The log recovered so
far is kept in logs/qr2-<sid>.txt (+ .json: coverage, gaps) and a status line says what is still missing.

  tools/qr-log.py --camera 0            # live webcam, Ctrl-C to stop
  tools/qr-log.py --esp <cam-ip>         # ESP32-CAM TCP stream
  tools/qr-log.py --video clip.mp4
  tools/qr-log.py --images 'shots/*.png'
  tools/qr-log.py --selftest            # needs the host build of qrlog (see below)
  tools/qr-log.py --esp --keep-going --out DIR [--until-file F]
                                        # save every new snapshot (qr-<UTC>-<sid>.txt) until SIGTERM / F exists;
                                        # tools/qrmerge.py DIR joins them into one log
  --tiles N       codes expected per screen (default 2); --workers N decoder threads; --average N average N similar frames (noisy cameras)
Needs opencv (pip install opencv-python-headless) and numpy.
"""
import argparse, glob, os, queue, re, subprocess, sys, tempfile, threading, time, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrcam, qrl2

HDR = re.compile(r"^QL1 ([0-9a-f]{8}) (\d+)/(\d+) ([0-9a-f]{8})\n", re.S)


class Collector:
    """QL1: one looping snapshot of the log tail."""

    def __init__(self):
        self.sid = None
        self.n = 0
        self.chunks = {}
        self.bad = 0

    def feed(self, text):
        m = HDR.match(text)
        if not m:
            return None
        sid, idx, n, crc = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4), 16)
        body = text[m.end():]
        if zlib.crc32(body.encode("latin-1")) != crc or idx >= n:
            self.bad += 1
            return None
        if sid != self.sid or n != self.n:
            self.sid, self.n, self.chunks = sid, n, {}
        self.chunks[idx] = body
        if len(self.chunks) == n:
            # sid is the crc of the raw (unsanitised) text, so only the chunk crcs are checkable
            return "".join(self.chunks[i] for i in range(n))
        return None

    def progress(self):
        return "snapshot %s: %d/%d frames, %d bad" % (self.sid, len(self.chunks), self.n, self.bad)


def is_frame(text):
    return text.startswith("QL1 ") or qrl2.parse_frame(text) is not None


def esp_frames(cv2, host, port=3333):
    """ESP32-CAM TCP stream: repeating <u32 LE len><u64 LE capture_us><JPEG>."""
    import socket, struct
    import numpy as np

    def need(sock, n):
        buf = b""
        while len(buf) < n:
            c = sock.recv(n - len(buf))
            if not c:
                raise EOFError
            buf += c
        return buf

    while True:
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                while True:
                    n, _us = struct.unpack("<IQ", need(sock, 12))
                    jpg = need(sock, n)
                    im = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
                    if im is not None:
                        yield im
        except (OSError, EOFError):
            time.sleep(1)


def frames(cv2, args):
    if args.esp:
        yield from esp_frames(cv2, args.esp)
        return
    if args.images:
        for p in sorted(glob.glob(args.images)):
            yield cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        return
    cap = cv2.VideoCapture(args.video if args.video else args.camera)
    if not cap.isOpened():
        sys.exit("cannot open the video source")
    while True:
        ok, im = cap.read()
        if not ok:
            if args.video:
                return
            time.sleep(0.05)
            continue
        yield cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)


class Run:
    """Camera frames in, QL1 snapshots / the QL2 stream out. Decoding runs on worker threads; frames that arrive while all are busy are dropped."""

    def __init__(self, cv2, args):
        import numpy as np
        self.cv2, self.np, self.a = cv2, np, args
        self.tls = threading.local()
        self.lock = threading.Lock()
        self.col, self.rx = Collector(), qrl2.Receiver()
        self.saved, self.last_line, self.last_save, self.dirty = set(), "", 0.0, set()
        self.result = None          # exit code once a one-shot run has what it wants
        self.n_img = self.n_dec = self.n_drop = self.n_ql1 = 0
        self.t_start = time.time()
        self.prev = []              # last gray images, for --average
        self.keep = getattr(args, "keep_going", False)

    def dets(self):
        if not hasattr(self.tls, "d"):
            self.tls.d = qrcam.detectors(self.cv2)
        return self.tls.d

    def decode(self, img, now):
        a = self.a
        want = max(1, a.tiles)
        if self.n_ql1 and not self.rx.streams:      # QL1 frames: one code per screen, do not hunt for a second
            want = 1
        texts = qrcam.decode_all(self.cv2, img, self.dets(), want=want, accept=is_frame)
        if len(texts) < want and a.average > 1:
            with self.lock:
                self.prev = ([img] + [p for p in self.prev if abs(float(p.mean()) - float(img.mean())) < 6])[:a.average]
                group = list(self.prev)
            if len(group) > 1:
                avg = self.np.mean([g.astype(self.np.float32) for g in group], axis=0).astype(self.np.uint8)
                texts += [t for t in qrcam.decode_all(self.cv2, avg, self.dets(), want=want, accept=is_frame) if t not in texts]
        with self.lock:
            self.n_img += 1
            self.n_dec += bool(texts)
            for t in texts:
                self.feed(t, now)

    def feed(self, text, now):
        a = self.a
        if text.startswith("QL1 "):
            self.n_ql1 += 1
            full = self.col.feed(text)
            if self.col.sid and self.col.progress() != self.last_line:
                self.last_line = self.col.progress()
                print(self.last_line, flush=True)
            if full is not None and self.col.sid not in self.saved:
                os.makedirs(a.out, exist_ok=True)
                stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                path = os.path.join(a.out, "qr-%s%s.txt" % (stamp, "-" + self.col.sid if self.keep else ""))
                open(path + ".tmp", "w").write(full)
                os.replace(path + ".tmp", path)     # a reader never sees half a snapshot
                print("saved", path, len(full), "bytes", flush=True)
                self.saved.add(self.col.sid)
                if not self.keep:
                    self.result = 0
            return
        r = self.rx.feed(text, now)
        f = qrl2.parse_frame(text)
        if r == "new" and f is not None:
            self.dirty.add(f.sid)

    def tick(self, final=False):
        """Print the status line / save the QL2 log (every few seconds, and when new data arrived)."""
        now = time.time()
        if now - self.last_save < (0.5 if final else self.a.status_every) and not final:
            return
        self.last_save = now
        with self.lock:
            for sid in self.rx.sids():
                line = self.rx.status(sid)
                if line != self.last_line or final:
                    self.last_line = line
                    print("%s | cam %.1f img/s, %d dropped" % (line, self.n_img / max(1e-3, now - self.t_start), self.n_drop), flush=True)
                if sid in self.dirty or final:
                    self.dirty.discard(sid)
                    path = self.rx.save(sid, self.a.out)
                    if final:
                        print("saved", path, flush=True)
                if self.rx.coverage(sid)["complete"] and not self.keep and not self.a.follow:
                    self.result = self.result if self.result is not None else 0

    def run(self):
        a = self.a
        from concurrent.futures import ThreadPoolExecutor
        busy = threading.Semaphore(a.workers)
        t_save = 0.0
        until = getattr(a, "until_file", None)
        with ThreadPoolExecutor(a.workers) as ex:
            for img in frames(self.cv2, a):
                if until and os.path.exists(until):
                    break
                if a.save_dir and time.time() - t_save >= a.save_every:
                    t_save = time.time()
                    os.makedirs(a.save_dir, exist_ok=True)
                    self.cv2.imwrite(os.path.join(a.save_dir, "f%s.jpg" % time.strftime("%H%M%S")), img)
                if a.images or a.video:
                    busy.acquire()          # files: decode every one, in order
                elif not busy.acquire(blocking=False):
                    self.n_drop += 1
                    continue
                now = time.time()

                def job(img=img, now=now):
                    try:
                        self.decode(img, now)
                    finally:
                        busy.release()
                ex.submit(job)
                self.tick()
                if self.result is not None:
                    break
        self.tick(final=True)
        if self.result is not None:
            return self.result
        sids = self.rx.sids()
        if sids:
            return 0 if (self.keep or a.follow or self.rx.coverage(sids[-1])["bytes"]) else 1
        print("source ended:", self.col.progress())
        return 1 if not self.saved else 0


def run(args):
    import cv2
    for k, v in (("tiles", 2), ("workers", 2), ("average", 1), ("status_every", 5.0), ("follow", False), ("keep_going", False)):
        if not hasattr(args, k):
            setattr(args, k, v)
    return Run(cv2, args).run()


def selftest():
    """QL1: render a sample log with the host qrlog build, decode the pictures. QL2: frame-level faults (loss, duplicates, reordering,
    corruption, a log that changes between loops), then rendered 2-tile screens through the image decoder."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = tempfile.mkdtemp(prefix="qrlog-")
    exe = os.path.join(d, "t")
    subprocess.check_call(["gcc", "-O1", "-I", root + "/qrlog", "-o", exe, root + "/qrlog/qrlog_host_test.c",
                           root + "/qrlog/qrlog.c", root + "/qrlog/qrcodegen.c"])
    src = os.path.join(d, "log.txt")
    text = "".join("SWEET UFS: line %04d hs-g4 %s\n" % (i, "x" * (i % 40)) for i in range(180))
    text += "tab\there \xe9 nonascii\n"
    open(src, "w", encoding="latin-1").write(text)
    subprocess.check_call([exe, src, d])
    a = argparse.Namespace(images=d + "/f*.pgm", video=None, camera=0, esp=None, save_dir=None, save_every=4, out=d, tiles=1)
    rc = run(a)
    got = [f for f in os.listdir(d) if f.startswith("qr-")]
    assert rc == 0 and got, "selftest: nothing decoded"
    out = open(os.path.join(d, got[0])).read()
    want = "".join(c if (c == "\n" or 0x20 <= ord(c) < 0x7f) else ("." if ord(c) < 0x20 else "?") for c in text)
    assert out == want, "selftest: text mismatch"
    print("selftest QL1 OK:", len(out), "bytes from", len(glob.glob(d + "/f*.pgm")), "frames")
    qrl2.selftest(images=True)
    return 0


def load_env():
    """CAM_HOST etc. from the .env of this checkout or of the main clone (tools/envfile.py)."""
    import envfile
    envfile.load_into_environ()


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--video")
    ap.add_argument("--esp", nargs="?", const="", help="ESP32-CAM host/IP (TCP stream, port 3333); no value = CAM_HOST from .env")
    ap.add_argument("--images")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"))
    ap.add_argument("--save-dir", help="also keep a frame every --save-every seconds (to read panics by eye)")
    ap.add_argument("--save-every", type=float, default=4)
    ap.add_argument("--keep-going", action="store_true", help="keep decoding successive snapshots (distinct sid), save each, stop on SIGTERM/--until-file")
    ap.add_argument("--until-file", help="stop when this file exists")
    ap.add_argument("--follow", action="store_true", help="QL2: do not stop when the stream is complete (the log keeps growing)")
    ap.add_argument("--tiles", type=int, default=2, help="QR codes expected per screen (QL2 default 2)")
    ap.add_argument("--workers", type=int, default=2, help="decoder threads (frames arriving while all are busy are dropped)")
    ap.add_argument("--average", type=int, default=1, help="when a frame does not decode, average it with up to N-1 similar recent ones")
    ap.add_argument("--status-every", type=float, default=5.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.esp == "":
        a.esp = os.environ.get("CAM_HOST") or sys.exit("no CAM_HOST in .env (tools/envfile.py path shows the file used)")
    sys.exit(selftest() if a.selftest else run(a))
