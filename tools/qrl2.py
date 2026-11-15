"""QL2 receiver: frame texts from the QR channel (qrlog/qrstream.h) -> the phone's log.

Frames are base45 text. A stream (one phone boot = one 16-bit session id) is a numbered series of blocks; a block arrives
either plain (SRC) or inside a repair symbol (REP: XOR of a pseudo-random subset of its generation), so any ~K+2 frames of a
generation rebuild it, in any order, with any duplicates. Blocks hold compressed records (units of the reduced log, each with
its own offset), so a missing block costs only the units that touch it and the rest is decoded in place.

  r = Receiver(); r.feed(text) -> 'new' | 'dup' | 'bad' | None (not QL2)
  r.text(sid) -> bytes (the log so far, `[...gap...]` where units are missing); r.coverage(sid) -> dict
"""
import os, sys, time, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrz_codec

B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
B45_IDX = {c: i for i, c in enumerate(B45)}
GAP = "[...gap...]"
NONE16 = 0xFFFF
M32 = 0xFFFFFFFF


def b45decode(text):
    n = len(text)
    if n % 3 == 1:
        raise ValueError("bad base45 length")
    out = bytearray()
    try:
        for i in range(0, n - n % 3, 3):
            v = B45_IDX[text[i]] + B45_IDX[text[i + 1]] * 45 + B45_IDX[text[i + 2]] * 2025
            if v > 0xFFFF:
                raise ValueError("base45 overflow")
            out += v.to_bytes(2, "big")
        if n % 3:
            v = B45_IDX[text[-2]] + B45_IDX[text[-1]] * 45
            if v > 0xFF:
                raise ValueError("base45 overflow")
            out.append(v)
    except KeyError:
        raise ValueError("not base45")
    return bytes(out)


def varint(b, pos):
    v = s = 0
    while True:
        if pos >= len(b) or s > 63:
            raise ValueError("bad varint")
        c = b[pos]; pos += 1
        v |= (c & 127) << s; s += 7
        if not c & 128:
            return v, pos


def _mix32(h):
    h ^= h >> 16
    h = h * 0x85EBCA6B & M32
    h ^= h >> 13
    h = h * 0xC2B2AE35 & M32
    return h ^ h >> 16


def rep_mask(g, seed, kk):
    """Blocks of generation g covered by repair symbol `seed` (bit i = block i; kk = blocks the generation had when the frame was
    made); mirrors rep_seed/rep_bit in qrstream.c. A nonlinear hash: a GF(2)-linear generator cannot span more than its state."""
    base = _mix32(((g & M32) * 0x9E3779B1 ^ (seed & M32) * 0x85EBCA77 ^ 0xC2B2AE3D) & M32)
    m = 0
    for i in range(kk):
        if _mix32((base + i * 0x9E3779B9) & M32) >> 16 & 1:
            m |= 1 << i
    return m or 1 << (seed % kk)


class Frame:
    __slots__ = ("kind", "klog", "sid", "hi", "lo", "a", "seed", "payload")


def parse_frame(text):
    """Frame, or None when this is not a (valid) QL2 frame."""
    try:
        body = b45decode(text)
    except ValueError:   # lint-silent: expected a frame whose text is not base45 is camera noise, not a frame: None
        return None
    if len(body) < 12 or body[0] >> 4 != 2 or zlib.crc32(body[:-4]) != int.from_bytes(body[-4:], "little"):
        return None
    f = Frame()
    f.kind, f.klog, f.sid = body[0] >> 3 & 1, (body[0] & 7) + 2, body[1] | body[2] << 8
    try:
        f.hi, pos = varint(body, 3)
        f.lo, pos = varint(body, pos)
        f.a, pos = varint(body, pos)
        f.seed = 0
        if f.kind:
            f.seed, pos = varint(body, pos)
    except ValueError:   # lint-silent: expected a frame with a truncated varint is camera noise: None
        return None
    f.payload = body[pos:-4]
    return f


class Stream:
    def __init__(self, sid, klog, B):
        self.sid, self.klog, self.K, self.B, self.D = sid, klog, 1 << klog, B, B - 2
        self.blocks = {}            # block number -> B bytes
        self.reps = {}              # generation -> [(mask, payload int)]
        self.hi = self.lo = 0
        self.pending = set()        # transport offsets of records that start somewhere we have bytes for but are not parsed yet
        self.units = {}             # reduced offset -> (rlen, raw log offset, data, time recovered)
        self.seen_rep = set()
        self.bad_units = 0
        self.frames = self.dups = self.rep_frames = 0
        self.t_first = self.t_last = None

    # -- blocks
    def _have(self, k, data, now):
        self.blocks[k] = data
        f = data[0] | data[1] << 8
        if f != NONE16:
            self.pending.add(k * self.D + f)

    def _solve(self, g, now):
        K = self.K
        rows = self.reps.get(g)
        base = g * K
        K = min(K, self.hi - base)
        if not rows or K < 1 or all(base + i in self.blocks for i in range(K)):
            return
        known = {i: int.from_bytes(self.blocks[base + i], "little") for i in range(K) if base + i in self.blocks}
        piv = {}
        for mask, data in rows:
            for i, v in known.items():
                if mask >> i & 1:
                    mask ^= 1 << i
                    data ^= v
            while mask:
                b = mask & -mask
                if b in piv:
                    pm, pd = piv[b]
                    mask ^= pm
                    data ^= pd
                else:
                    piv[b] = (mask, data)
                    break
        solved = {}
        for b in sorted(piv, reverse=True):
            mask, data = piv[b]
            rest = mask ^ b
            while rest:
                j = rest & -rest
                if j not in solved:
                    break
                data ^= solved[j]
                rest ^= j
            if not rest:
                solved[b] = data
        for b, data in solved.items():
            k = base + b.bit_length() - 1
            if k not in self.blocks:
                self._have(k, data.to_bytes(self.B, "little"), now)

    def add(self, f, now):
        self.frames += 1
        self.t_first = self.t_first or now
        self.t_last = now
        self.hi, self.lo = max(self.hi, f.hi), max(self.lo, f.lo)
        if f.kind == 0:
            if f.a in self.blocks:
                self.dups += 1
                return "dup"
            self._have(f.a, f.payload, now)
            self._solve(f.a // self.K, now)
        else:
            if (f.a, f.seed) in self.seen_rep:
                self.dups += 1
                return "dup"
            self.seen_rep.add((f.a, f.seed))
            self.rep_frames += 1
            kk = min(self.K, f.hi - f.a * self.K)       # the open generation: a repair frame covers the blocks it had when sent
            if kk < 1:
                return "dup"
            self.reps.setdefault(f.a, []).append((rep_mask(f.a, f.seed, kk), int.from_bytes(f.payload, "little")))
            self._solve(f.a, now)
        self._scan(now)
        return "new"

    # -- records
    def _read(self, pos, n):
        """n bytes of the transport stream at pos, None if a block is missing."""
        out = self._read_upto(pos, n)
        return out if len(out) == n else None

    def _read_upto(self, pos, n):
        out = bytearray()
        while len(out) < n:
            k, o = divmod(pos + len(out), self.D)
            b = self.blocks.get(k)
            if b is None:
                break
            out += b[2 + o:2 + min(self.D, o + n - len(out))]
        return bytes(out)

    def _scan(self, now):
        progress = True
        while progress and self.pending:
            progress = False
            for pos in sorted(self.pending):
                r = self._record(pos, now)
                if r is False:
                    continue
                self.pending.discard(pos)
                if r:
                    self.pending.add(r)
                progress = True

    def _record(self, pos, now):
        """False = bytes missing (retry later), None = unusable, int = where the next record starts."""
        first = self._read(pos, 1)
        if first is None:
            return False
        if first[0] == 0:       # padding up to the end of the block
            return (pos // self.D + 1) * self.D
        hdr = self._read_upto(pos, 40)
        try:
            rl2, p = varint(hdr, 0)
            clen, p = varint(hdr, p)
            roff, p = varint(hdr, p)
            raw0, p = varint(hdr, p)
        except ValueError:
            return False if len(hdr) < 40 else None
        if len(hdr) < p + 4:
            return False
        crc = int.from_bytes(hdr[p:p + 4], "little")
        body = self._read(pos + p + 4, clen)
        if body is None:
            return False
        rlen, stored = rl2 >> 1, rl2 & 1
        try:
            raw = body if stored else qrz_codec.inflate(body)
        except (ValueError, zlib.error):   # lint-silent: expected a corrupt or short stream is a bad unit: counted in bad_units on the next line and reported
            raw = None
        if raw is None or len(raw) != rlen or zlib.crc32(raw) != crc:
            self.bad_units += 1
        elif roff not in self.units:
            self.units[roff] = (rlen, raw0, raw, now)
        return pos + p + 4 + clen


class Receiver:
    def __init__(self):
        self.streams = {}
        self.bad = 0
        self.other = 0

    def feed(self, text, now=None):
        now = now if now is not None else time.time()
        f = parse_frame(text)
        if f is None:
            self.bad += 1
            return "bad"
        s = self.streams.get(f.sid)
        if s is None:
            s = self.streams[f.sid] = Stream(f.sid, f.klog, len(f.payload))
        if s.klog != f.klog or s.B != len(f.payload):
            self.bad += 1
            return "bad"
        return s.add(f, now)

    def sids(self):
        return sorted(self.streams, key=lambda k: self.streams[k].t_first or 0)

    def text(self, sid):
        """(bytes, [(start line index, time), ...]): the log so far; a `[...gap...]` line where reduced bytes are missing."""
        s = self.streams[sid]
        out, times, end, n = bytearray(), [], 0, 0
        for roff in sorted(s.units):
            rlen, _raw0, data, t = s.units[roff]
            if roff != end and (end or not s.lo):
                out += (GAP + "\n").encode(); times.append((n, t)); n += 1
            out += data
            times.append((n, t)); n += data.count(b"\n")
            end = roff + rlen
        return bytes(out), times

    def coverage(self, sid):
        s = self.streams[sid]
        have, end, gaps = 0, 0, []
        for roff in sorted(s.units):
            rlen = s.units[roff][0]
            if roff != end and (end or not s.lo):     # a hole before the first unit is only a gap while the ring still holds block 0
                gaps.append((end, roff - end))
            have += rlen
            end = roff + rlen
        want = s.hi - s.lo
        return {"sid": "%04x" % sid, "start": min(s.units) if s.units else 0, "bytes": have, "total": end, "gaps": gaps, "units": len(s.units), "bad_units": s.bad_units,
                "blocks": len(s.blocks), "hi": s.hi, "lo": s.lo, "frames": s.frames, "dups": s.dups, "reps": s.rep_frames,
                "complete": bool(s.units) and not gaps and all(k in s.blocks for k in range(s.lo, s.hi)),
                "missing_blocks": [k for k in range(s.lo, s.hi) if k not in s.blocks][:64], "t_first": s.t_first, "t_last": s.t_last}

    def status(self, sid):
        """One line: what has arrived and what is still missing."""
        c = self.coverage(sid)
        miss = c["hi"] - c["lo"] - c["blocks"]
        gaps = ", ".join("@%d+%d" % g for g in c["gaps"][:4]) + (" ..." if len(c["gaps"]) > 4 else "")
        return "QL2 %s: %d frames (%d dup), blocks %d/%d (%d missing), %d units, log %d B%s%s" % (
            c["sid"], c["frames"], c["dups"], c["blocks"], c["hi"] - c["lo"], max(0, miss), c["units"], c["bytes"],
            ", gaps %d: %s" % (len(c["gaps"]), gaps) if c["gaps"] else "", ", COMPLETE" if c["complete"] else "")

    def save(self, sid, outdir):
        """qr2-<sid>.txt (the log so far, atomically replaced) + qr2-<sid>.json (coverage + when each part arrived). Returns the text path."""
        import json
        data, times = self.text(sid)
        path = os.path.join(outdir, "qr2-%04x.txt" % sid)
        os.makedirs(outdir, exist_ok=True)
        for p, blob, mode in ((path, data, "wb"), (path[:-4] + ".json", json.dumps(dict(self.coverage(sid), line_times=times)).encode(), "wb")):
            with open(p + ".tmp", mode) as f:
                f.write(blob)
            os.replace(p + ".tmp", p)
        return path


# ------------------------------------------------------------------ self test (frame level + rendered screens)

def _recover(frames, rx=None):
    rx = rx or Receiver()
    for f in frames:
        rx.feed(f)
    return rx


def selftest(images=False, verbose=True):
    """Round trips through the real C encoder (qrlog/qrstream.c via tools/qrsim.py): clean, 30% loss + duplicates + reordering +
    corrupted frames, a log that grows while frames are already on air, a late joiner, two sessions, a wrapped ring; optionally
    rendered 2-tile screens through the camera model and the image decoder."""
    import random
    import qrsim
    say = print if verbose else (lambda *a, **k: None)
    log = qrsim.synth_log(150_000, seed=7)

    def frames_of(enc, n):
        return [t for t in (enc.frame() for _ in range(n)) if t]

    def truth():
        """What a perfect channel delivers: the reduced log."""
        enc = qrsim.Encoder(log, ver=10, tiles=1, sid=0x1111)
        rx = _recover(frames_of(enc, 2000))
        data, _ = rx.text(0x1111)
        c = rx.coverage(0x1111)
        assert c["complete"] and not c["gaps"], c
        return data

    ref = truth()
    lines_in = [l for l in log.split(b"\n") if l]
    kept = [l for l in ref.split(b"\n") if l and not l.startswith(b"[~")]
    dropped = sum(int(l[2:].split()[0]) for l in ref.split(b"\n") if l.startswith(b"[~"))
    assert len(kept) + dropped == len(lines_in), "reducer lost track of lines: %d kept + %d dropped != %d" % (len(kept), dropped, len(lines_in))
    assert set(kept) <= set(lines_in), "reducer altered a line"
    say("  clean: %d B log -> %d B reduced (%d lines kept, %d dropped as repeats)" % (len(log), len(ref), len(kept), dropped))

    rng = random.Random(5)
    enc = qrsim.Encoder(log, ver=10, tiles=1, sid=0x2222)
    fs = frames_of(enc, 1500)
    n_in = len(fs)
    stream = []
    for f in fs:                                   # 30% lost, 20% duplicated up to 3x, a few corrupted
        if rng.random() < 0.30:
            continue
        for _ in range(1 + (rng.random() < 0.2) * rng.randint(1, 3)):
            g = f
            if rng.random() < 0.03:
                i = rng.randrange(len(g))
                g = g[:i] + rng.choice("0123456789ABC") + g[i + 1:]
            stream.append(g)
    for i in range(0, len(stream) - 8, 8):         # reordered inside windows of 8
        w = stream[i:i + 8]
        rng.shuffle(w)
        stream[i:i + 8] = w
    rx = _recover(stream)
    c = rx.coverage(0x2222)
    assert c["complete"] and rx.text(0x2222)[0] == ref, "lossy channel did not rebuild the log: " + rx.status(0x2222)
    assert rx.bad > 0 and c["dups"] > 0
    say("  lossy: %d of %d frames sent reached the receiver (30%% lost, duplicates, reordered, %d corrupted rejected) -> complete, identical" % (len(stream), n_in, rx.bad))

    enc = qrsim.Encoder(ver=10, tiles=1, sid=0x3333)       # the log grows while the loop is already running
    sent, step = [], 700
    for i in range(0, len(log), step):
        enc.feed(log[i:i + step])
        if (i // step) % 9 == 8:
            enc.flush()
        sent += frames_of(enc, 2)
    enc.flush()
    sent += frames_of(enc, 600)
    rng.shuffle(sent[:50])
    rx = _recover([f for f in sent if rng.random() > 0.25])
    c = rx.coverage(0x3333)
    assert c["complete"] and rx.text(0x3333)[0] == ref, "growing log: " + rx.status(0x3333)
    say("  growing log: new data first, 25%% lost -> complete, identical (%d frames)" % len(sent))

    enc = qrsim.Encoder(log, ver=10, tiles=1, sid=0x4444)
    late = frames_of(enc, 1200)[len(frames_of(qrsim.Encoder(log, ver=10, tiles=1, sid=1), 0)) + 150:]
    rx = _recover(late)
    assert rx.coverage(0x4444)["complete"] and rx.text(0x4444)[0] == ref, "late joiner: " + rx.status(0x4444)
    say("  late joiner (first 150 frames missed): complete via repair frames")

    a1, a2 = qrsim.Encoder(log, ver=10, tiles=1, sid=0x5555), qrsim.Encoder(log[:60000], ver=11, tiles=1, sid=0x6666, klog=3)
    mix = [x for p in zip(frames_of(a1, 1200), frames_of(a2, 1200)) for x in p]
    rx = _recover(mix)
    assert rx.sids() == [0x5555, 0x6666] and rx.coverage(0x5555)["complete"] and rx.coverage(0x6666)["complete"] and rx.text(0x5555)[0] == ref
    say("  two sessions interleaved (different QR version and K): kept apart")

    for klog in (2, 5, 7, 9):       # repair frames alone rebuild generations of 4 .. 512 blocks (a linear subset generator could not pass K > 32)
        enc = qrsim.Encoder(log[:60000], ver=10, tiles=1, sid=0x9000 + klog, klog=klog)
        rx = _recover([f for f in frames_of(enc, 1500) if parse_frame(f).kind == 1])
        assert rx.coverage(0x9000 + klog)["complete"], "K=%d repair-only: %s" % (1 << klog, rx.status(0x9000 + klog))
    say("  repair frames alone rebuild generations of 4, 32, 128 and 512 blocks")

    big = qrsim.synth_log(700_000, seed=3)
    enc = qrsim.Encoder(ver=10, tiles=1, sid=0x7777, tx_size=65536)    # tiny ring: the oldest blocks are overwritten
    enc.feed(big)
    enc.flush()
    rx = _recover(frames_of(enc, 900))
    c = rx.coverage(0x7777)
    data = rx.text(0x7777)[0]
    assert c["lo"] > 0 and c["bytes"] > 0 and not c["gaps"] and data and big.find(data.split(b"\n")[1]) > 0, "wrapped ring: %s" % rx.status(0x7777)
    say("  wrapped ring: retained the newest %d B (lo=%d), complete from there" % (c["bytes"], c["lo"]))

    if images:
        import cv2
        import numpy as np
        import qrcam
        enc = qrsim.Encoder(log[:30000], ver=10, tiles=2, sid=0x8888)
        dets = qrcam.detectors(cv2)
        got, shown = set(), set()
        for i in range(6):
            texts = [enc.frame(), enc.frame()]
            scr, _ = enc.screen(texts)
            img, _ = qrsim.camera(cv2, np, scr, 1280, 720, 0.27, quality="typical", rng=random.Random(i))
            shown.update(texts)
            got.update(qrcam.decode_all(cv2, img, dets, want=2, accept=lambda t: parse_frame(t) is not None))
        assert len(got) >= 0.8 * len(shown) and got <= shown, "image path: %d of %d tiles" % (len(got), len(shown))
        say("  screens: 2 tiles x 6 screens through the camera model: %d of %d frames decoded" % (len(got), len(shown)))
    say("selftest QL2 OK")


if __name__ == "__main__":
    selftest(images="--images" in sys.argv)
