#!/usr/bin/env python3
"""hfs_read: read ONE regular file out of an HFS+/HFSX image, read-only, no root, stdlib only (for tools/overlay_std.py).

  hfs_read.py IMAGE /abs/path OUT     write the file's data fork to OUT; exit 0 ok, 1 no such file, 2 error (one line on stderr)

Why: 7z (25.01) answers "Data Error" for /System/Library/xpc/launchd.plist of the iOS root images (observed 2026-11-09) and writes 0 bytes.
Layout from Apple TN1150 (HFS Plus Volume Format), as the removed tools/hfs-straddle-check.py (deprecated 2026-10-29, removed 2026-11-13) used it: volume header at
byte 1024 ('H+' or 'HX'), blockSize at +40, fork data 80 bytes (logicalSize u64, clumpSize u32, totalBlocks u32, 8 x (startBlock u32,
blockCount u32)) for the extents file at +192 and the catalog file at +272. B-tree node: descriptor 14 bytes (fLink u32, bLink u32,
kind s8, height u8, numRecords u16, reserved u16), record offsets as u16 from the node end; header record (node 0, offset 14):
treeDepth u16, rootNode u32, leafRecords u32, firstLeafNode u32, lastLeafNode u32, nodeSize u16. Catalog key: keyLength u16,
parentID u32, name length u16 + UTF-16BE. Leaf records: folder (1) folderID at +8; file (2) fileID at +8, BSD info at +32
(ownerFlags u8 at +41: UF_COMPRESSED 0x20 is refused), FileInfo at +48 (fdType 'hlnk' + fdCreator 'hfs+' = hard link: BSD special
(+44) = iNode number, file iNode<N> in the private folder "\\0\\0\\0\\0HFS+ Private Data"), data fork at +88. Extents-overflow key:
keyLength u16, forkType u8, pad u8, fileID u32, startBlock u32; record = 8 extents. All big endian. Root folder ID 2, parent 1.
The test (tools/test_overlay_std.py) checks the bytes against 7z on files 7z reads correctly.
"""
import struct, sys

PRIVATE = "\0\0\0\0HFS+ Private Data"


class Bad(Exception):
    pass


class HFS:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(1024)
        vh = self.f.read(512)
        if vh[:2] not in (b"H+", b"HX"):
            raise Bad("not an HFS+/HFSX volume (signature %r)" % vh[:2])
        self.bs = struct.unpack(">I", vh[40:44])[0]
        self.ext_fork = self.fork(vh, 192)
        self.cat_fork = self.fork(vh, 272)
        self.overflow = self.read_overflow()
        self.cat_hdr = self.header(self.cat_fork, 4)

    @staticmethod
    def fork(buf, o):
        size, _clump, _total = struct.unpack(">QII", buf[o:o + 16])
        ext = [struct.unpack(">II", buf[o + 16 + 8 * i:o + 24 + 8 * i]) for i in range(8)]
        return size, [e for e in ext if e[1]]

    def extents(self, fork, file_id, fork_type=0):
        size, ext = fork
        ext = list(ext)
        have = sum(c for _, c in ext)
        need = (size + self.bs - 1) // self.bs
        while have < need:
            more = self.overflow.get((fork_type, file_id, have)) if self.overflow is not None else None
            if not more:
                raise Bad("file %d: extents end at block %d of %d (no overflow record)" % (file_id, have, need))
            ext += more
            have += sum(c for _, c in more)
        return size, ext

    def read_fork(self, size, ext, off, n):
        out = bytearray()
        pos = off
        end = min(off + n, size)
        lb = 0
        for start, count in ext:
            lo, hi = lb * self.bs, (lb + count) * self.bs
            if hi > pos and lo < end:
                a, b = max(pos, lo), min(end, hi)
                self.f.seek(start * self.bs + (a - lo))
                out += self.f.read(b - a)
            lb += count
        if len(out) != max(0, end - off):
            raise Bad("short read (%d of %d bytes)" % (len(out), end - off))
        return bytes(out)

    def header(self, fork, file_id):
        size, ext = self.extents(fork, file_id) if self.overflow is not None else fork
        node0 = self.read_fork(size, ext, 0, 512)
        depth, root, _leafs, first, _last, nsize = struct.unpack(">HIIIIH", node0[14:34])
        return {"size": size, "ext": ext, "first": first, "nsize": nsize}

    def nodes(self, hdr):
        n = hdr["first"]
        seen = 0
        while n:
            node = self.read_fork(hdr["size"], hdr["ext"], n * hdr["nsize"], hdr["nsize"])
            flink, _blink, kind, _h, nrec = struct.unpack(">IIbBH", node[:12])
            if kind != -1:
                raise Bad("node %d is not a leaf (kind %d)" % (n, kind))
            offs = [struct.unpack(">H", node[hdr["nsize"] - 2 * (i + 1):hdr["nsize"] - 2 * i])[0] for i in range(nrec)]
            for o in offs:
                yield node, o
            n = flink
            seen += 1
            if seen > 10_000_000:
                raise Bad("leaf chain does not end")

    def read_overflow(self):
        self.overflow = None
        size, ext = self.ext_fork
        if size == 0:
            return {}
        node0 = self.read_fork(size, ext, 0, 512)
        _d, _r, _l, first, _la, nsize = struct.unpack(">HIIIIH", node0[14:34])
        out = {}
        for node, o in self.nodes({"size": size, "ext": ext, "first": first, "nsize": nsize}):
            klen, ftype, _pad, fid, sblock = struct.unpack(">HBBII", node[o:o + 12])
            r = o + 2 + klen
            out[(ftype, fid, sblock)] = [e for e in (struct.unpack(">II", node[r + 8 * i:r + 8 * i + 8]) for i in range(8)) if e[1]]
        return out

    def catalog(self, wanted):
        """{(parentID, name): record bytes} for the names in WANTED (a set of names)."""
        out = {}
        for node, o in self.nodes(self.cat_hdr):
            klen, parent, nlen = struct.unpack(">HIH", node[o:o + 8])
            name = node[o + 8:o + 8 + 2 * nlen].decode("utf-16-be", "replace")
            if name not in wanted:
                continue
            r = o + 2 + klen
            out[(parent, name)] = node[r:r + 248]
        return out

    def read_file(self, path):
        parts = [p for p in path.split("/") if p]
        if not parts:
            raise Bad("not a file path: %r" % path)
        cat = self.catalog(set(parts) | {PRIVATE})
        parent = 2
        rec = None
        for i, p in enumerate(parts):
            rec = cat.get((parent, p))
            if rec is None:
                return None
            rtype = struct.unpack(">h", rec[:2])[0]
            if i < len(parts) - 1:
                if rtype != 1:
                    raise Bad("%s is not a folder" % "/".join(parts[:i + 1]))
                parent = struct.unpack(">I", rec[8:12])[0]
        if struct.unpack(">h", rec[:2])[0] != 2:
            raise Bad("%s is not a regular file record" % path)
        if rec[48:52] == b"hlnk" and rec[52:56] == b"hfs+":
            inode = struct.unpack(">I", rec[44:48])[0]
            priv = cat.get((2, PRIVATE))
            if priv is None:
                raise Bad("%s is a hard link but the private folder is missing" % path)
            pid = struct.unpack(">I", priv[8:12])[0]
            name = "iNode%d" % inode
            rec = self.catalog({name}).get((pid, name))
            if rec is None:
                raise Bad("%s: hard link target %s missing" % (path, name))
        if rec[41] & 0x20:
            raise Bad("%s is HFS-compressed (UF_COMPRESSED): not supported" % path)
        fid = struct.unpack(">I", rec[8:12])[0]
        size, ext = self.extents(self.fork(rec, 88), fid)
        return self.read_fork(size, ext, 0, size)


def main(argv):
    if len(argv) != 4:
        print("usage: hfs_read.py IMAGE /abs/path OUT", file=sys.stderr)
        return 2
    try:
        data = HFS(argv[1]).read_file(argv[2])
    except (Bad, OSError, struct.error) as e:
        print("hfs_read: ERROR %s" % e, file=sys.stderr)
        return 2
    if data is None:
        print("hfs_read: no such file %s" % argv[2], file=sys.stderr)
        return 1
    with open(argv[3], "wb") as f:
        f.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
