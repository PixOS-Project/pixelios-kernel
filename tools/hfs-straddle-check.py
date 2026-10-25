#!/usr/bin/env python3
"""Host check: does any HFS+/HFSX B-tree node (catalog fileID 4, extents-overflow 3, attributes 8) straddle a non-contiguous extent?
The XNU kernel maps a node as one contiguous run from its first block, so a node whose blocks are not physically adjacent reads the
wrong second half (all-zero offset table: "hfs_swap_BTNode: offsets 1 and 0 out of order", research 50 item 5, AP-52, AP-72).
Each logical block is mapped through the volume header's 8 extents plus the extents-overflow records (forkType 0) of the same fileID.
Layout, read from the HFS+ spec (TN1150): volume header at byte 1024, blockSize at +40, fork records of 80 bytes at +112 allocation,
+192 extents, +272 catalog, +352 attributes; fork = logicalSize u64, clumpSize u32, totalBlocks u32, 8 x (startBlock u32, blockCount u32);
B-tree node 0 header record at +14: nodeSize at +32, totalNodes at +36; leaf node kind byte (offset 8) = 255; overflow key =
keyLength u16, forkType u8, pad u8, fileID u32, startBlock u32, then 8 extents. All big endian.
Usage: hfs-straddle-check.py IMAGE [--offset BYTES]      --self-test is run by tools/test_hfs_straddle_check.py
Last line RESULT: PASS|FAIL ...; exit 0 no straddling node, 1 at least one straddles, 2 usage/unreadable/not HFS+."""
import struct, sys

FORKS = {3: 192, 4: 272, 8: 352}   # fileID -> VH offset of its fork record
NAMES = {3: "extents", 4: "catalog", 8: "attributes"}


class Bad(Exception):
    pass


def load(f, base):
    f.seek(base + 1024)
    vh = f.read(512)
    if len(vh) < 512 or vh[:2] not in (b"H+", b"HX"):
        raise Bad("no HFS+/HFSX volume header at offset %d" % (base + 1024))
    bs = struct.unpack(">I", vh[40:44])[0]
    if bs < 512 or bs & (bs - 1):
        raise Bad("bad block size %d" % bs)
    return vh, bs


def vh_fork(vh, o):
    total = struct.unpack(">I", vh[o + 12:o + 16])[0]
    ex = [struct.unpack(">II", vh[o + 16 + 8 * i:o + 24 + 8 * i]) for i in range(8)]
    return total, [e for e in ex if e[1]]


def phys_map(extents):
    """extent list -> list of physical block per logical block"""
    return [s + k for s, c in extents for k in range(c)]


def read_fork(f, base, bs, pm, off, n):
    out = b""
    while n > 0 and off // bs < len(pm):
        lb, within = divmod(off, bs)
        take = min(n, bs - within)
        f.seek(base + pm[lb] * bs + within)
        out += f.read(take)
        off += take
        n -= take
    return out


def overflow(f, base, bs, vh):
    """-> {fileID: [(startBlock, [extents])]} for forkType 0 records of fileIDs 3/4/8"""
    _, eex = vh_fork(vh, FORKS[3])
    pm = phys_map(eex)
    h = read_fork(f, base, bs, pm, 0, 512)
    if len(h) < 40:
        raise Bad("extents file header unreadable")
    ns = struct.unpack(">H", h[32:34])[0]
    tot = struct.unpack(">I", h[36:40])[0]
    if ns < 512 or ns & (ns - 1):
        raise Bad("bad extents node size %d" % ns)
    ov = {}
    for n in range(1, tot):
        d = read_fork(f, base, bs, pm, n * ns, ns)
        if len(d) < ns or d[8] != 255:
            continue
        for i in range(struct.unpack(">H", d[10:12])[0]):
            o = struct.unpack(">H", d[ns - 2 - 2 * i:ns - 2 * i])[0]
            kl, ft, _, fid, sb = struct.unpack(">HBBII", d[o:o + 12])
            if ft == 0 and fid in FORKS and kl == 10:
                ex = [struct.unpack(">II", d[o + 12 + 8 * j:o + 20 + 8 * j]) for j in range(8)]
                ov.setdefault(fid, []).append((sb, [e for e in ex if e[1]]))
    return ov


def check(path, base=0):
    """-> (lines, bad_count); raises Bad"""
    lines, bad = [], 0
    with open(path, "rb") as f:
        vh, bs = load(f, base)
        ov = overflow(f, base, bs, vh)
        for fid, o in FORKS.items():
            total, ex = vh_fork(vh, o)
            if total == 0:
                continue
            for _, e in sorted(ov.get(fid, [])):
                ex = ex + e
            pm = phys_map(ex)
            if len(pm) != total:
                raise Bad("%s: extents give %d blocks, volume header says %d" % (NAMES[fid], len(pm), total))
            h = read_fork(f, base, bs, pm, 0, 512)
            ns = struct.unpack(">H", h[32:34])[0] if len(h) >= 34 else 0
            tn = struct.unpack(">I", h[36:40])[0] if len(h) >= 40 else 0
            if ns < 512 or ns & (ns - 1):
                raise Bad("%s: bad node size %d" % (NAMES[fid], ns))
            bpn = max(ns // bs, 1)
            st = [n for n in range(tn) if (n + 1) * bpn <= len(pm)
                  and any(pm[n * bpn + k + 1] != pm[n * bpn + k] + 1 for k in range(bpn - 1))]
            lines.append("%s: nodeSize %d blockSize %d nodes %d straddling %d %s" % (NAMES[fid], ns, bs, tn, len(st), st[:10]))
            bad += len(st)
    return lines, bad


def main(argv):
    base = 0
    args = list(argv)
    if "--offset" in args:
        i = args.index("--offset")
        try:
            base = int(args[i + 1])
        except (IndexError, ValueError):
            print("RESULT: ERROR usage: --offset needs an integer")
            return 2
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: hfs-straddle-check.py IMAGE [--offset BYTES]\nRESULT: ERROR usage")
        return 2
    try:
        lines, bad = check(args[0], base)
    except (OSError, Bad, struct.error) as e:
        print("RESULT: ERROR %s" % e)
        return 2
    print("\n".join(lines))
    print("RESULT: %s %d straddling B-tree node(s) in %s" % ("FAIL" if bad else "PASS", bad, args[0]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
