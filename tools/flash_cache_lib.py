"""flash_cache_lib: the libsparse binding and the content checks of tools/flash_cache.py (split out for lint-size R29; see that header for why and the sources)."""
import ctypes
import hashlib
import os
import re
import shutil
import struct
import subprocess

MAGIC, RAW, FILL, DONT_CARE, CRC32 = 0xED26FF3A, 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4   # AOSP libsparse/sparse_format.h (read from source 2026-11-11)
LIBSPARSE_DEFAULT = "/usr/lib/x86_64-linux-gnu/android/libsparse.so.0"   # Debian android-libsparse 34.0.5 (observed: ldd of the fastboot on this host)
BUF = 8 << 20


# ---------------------------------------------------------------------------------------------------- libsparse (the library fastboot loads)
def libsparse_path():
    p = os.environ.get("FLASH_CACHE_LIBSPARSE")
    if p:
        return p
    fb = shutil.which("fastboot")
    if fb:
        try:
            r = subprocess.run(["ldd", os.path.realpath(fb)], capture_output=True, text=True, timeout=10)
            m = re.search(r"libsparse\S*\s+=>\s+(\S+)", r.stdout)
            if m:
                return m.group(1)
        except (OSError, subprocess.SubprocessError):   # lint-silent: expected a fastboot that is a script (tests) or no ldd: the Debian default below is used and printed
            pass
    return LIBSPARSE_DEFAULT


def libsparse():
    p = libsparse_path()
    L = ctypes.CDLL(p, use_errno=True)
    vp, b, i = ctypes.c_void_p, ctypes.c_bool, ctypes.c_int
    # signatures: android-libsparse-dev 34.0.5 include/android/sparse/sparse.h (read from source)
    L.sparse_file_import_auto.restype, L.sparse_file_import_auto.argtypes = vp, [i, b, b]     # (fd, crc, verbose)
    L.sparse_file_import.restype, L.sparse_file_import.argtypes = vp, [i, b, b]               # (fd, verbose, crc)
    L.sparse_file_len.restype, L.sparse_file_len.argtypes = ctypes.c_int64, [vp, b, b]        # (s, sparse, crc)
    L.sparse_file_write.restype, L.sparse_file_write.argtypes = i, [vp, i, b, b, b]           # (s, fd, gz, sparse, crc)
    L.sparse_file_destroy.restype, L.sparse_file_destroy.argtypes = None, [vp]
    return L, p


# ---------------------------------------------------------------------------------------------------- independent parser / verifier
def expand(simg, raw_size):
    """-> (sha256 of the expanded image, stats). Raises ValueError on any format problem. Own code, not libsparse (independent check)."""
    st = dict(chunks=0, raw_blocks=0, fill_zero_blocks=0, fill_other_blocks=0, dont_care_blocks=0, crc_chunks=0)
    h = hashlib.sha256()
    with open(simg, "rb") as f:
        hdr = f.read(28)
        if len(hdr) < 28:
            raise ValueError("short header")
        magic, major, _minor, fhs, chs, blk, total_blks, total_chunks, _ck = struct.unpack("<IHHHHIIII", hdr)
        if magic != MAGIC or major != 1 or fhs < 28 or chs < 12 or blk == 0 or blk % 4:
            raise ValueError("bad header magic=%#x major=%d file_hdr=%d chunk_hdr=%d blk=%d" % (magic, major, fhs, chs, blk))
        if total_blks * blk != raw_size:
            raise ValueError("expanded size %d != raw size %d" % (total_blks * blk, raw_size))
        f.seek(fhs)
        out = 0
        for _ in range(total_chunks):
            ch = f.read(chs)
            if len(ch) < chs:
                raise ValueError("short chunk header")
            ctype, _r, csz, tsz = struct.unpack("<HHII", ch[:12])
            st["chunks"] += 1
            if ctype == RAW:
                n = csz * blk
                if tsz != chs + n:
                    raise ValueError("raw chunk total_sz %d != %d" % (tsz, chs + n))
                while n:
                    d = f.read(min(BUF, n))
                    if not d:
                        raise ValueError("raw chunk data truncated")
                    h.update(d); n -= len(d)
                st["raw_blocks"] += csz
            elif ctype == FILL:
                if tsz != chs + 4:
                    raise ValueError("fill chunk total_sz %d" % tsz)
                v = f.read(4)
                pat = v * (BUF // 4)
                n = csz * blk
                while n:
                    k = min(BUF, n)
                    h.update(pat[:k] if k < BUF else pat); n -= k
                st["fill_zero_blocks" if v == b"\0\0\0\0" else "fill_other_blocks"] += csz
            elif ctype == DONT_CARE:
                if tsz != chs:
                    raise ValueError("dont-care chunk total_sz %d" % tsz)
                st["dont_care_blocks"] += csz
                z = b"\0" * BUF
                n = csz * blk
                while n:
                    k = min(BUF, n)
                    h.update(z[:k]); n -= k
            elif ctype == CRC32:
                if tsz != chs + 4 or csz != 0:
                    raise ValueError("crc chunk sizes %d/%d" % (csz, tsz))
                f.read(4)
                st["crc_chunks"] += 1
            else:
                raise ValueError("unknown chunk type %#x" % ctype)
            out += csz
        if out != total_blks:
            raise ValueError("chunks cover %d blocks, header says %d" % (out, total_blks))
        if f.read(1):
            raise ValueError("trailing bytes after the last chunk")
    st["block_size"] = blk
    st["total_blocks"] = total_blks
    return h.hexdigest(), st


def verify(part, sha, raw_size, L):
    """All content checks of a written copy; raises OSError/ValueError on the first one that fails, else returns the stats.
    1. independent expand (own parser) sha256 == raw; 2. no DONT_CARE; 3. libsparse imports it, expanded length == raw size and
    libsparse's own expansion (sparse_file_callback, the reader fastboot uses) hashes to the raw sha256 too."""
    esha, st = expand(part, raw_size)
    if esha != sha:
        raise OSError("expanded sha256 %s != raw %s" % (esha[:12], sha[:12]))
    if st["dont_care_blocks"]:
        raise OSError("%d DONT_CARE blocks: stale data would survive on the phone" % st["dont_care_blocks"])
    pfd = os.open(part, os.O_RDONLY)
    try:
        s2 = L.sparse_file_import(pfd, True, False)
        if not s2:
            raise OSError("libsparse sparse_file_import refuses the copy")
        try:
            ln = L.sparse_file_len(s2, False, False)
            if ln != raw_size:
                raise OSError("libsparse expanded length %d != raw %d" % (ln, raw_size))
            lsha = libsparse_expand_sha(L, s2, raw_size)
            if lsha != sha:
                raise OSError("libsparse expansion sha256 %s != raw %s" % (lsha[:12], sha[:12]))
        finally:
            L.sparse_file_destroy(s2)
    finally:
        os.close(pfd)
    return st


CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)   # sparse.h: int (*write)(void *priv, const void *data, size_t len)


def libsparse_expand_sha(L, s, raw_size):
    """sha256 of libsparse's own non-sparse expansion of S (data==NULL = a skipped region, hashed as zeros); no copy of the data into Python."""
    h, n, zero, err = hashlib.sha256(), [0], b"\0" * BUF, []

    def cb(_priv, data, ln):
        try:
            if data is None:
                k = ln
                while k:
                    d = min(k, BUF); h.update(zero[:d]); k -= d
            else:
                off = 0
                while off < ln:
                    d = min(ln - off, 64 << 20)
                    h.update(memoryview((ctypes.c_char * d).from_address(data + off)))
                    off += d
            n[0] += ln
            return 0
        except Exception as e:   # an exception must not unwind through C: report it and stop the walk
            err.append(e)
            return -1
    fn = CB(cb)
    L.sparse_file_callback.restype, L.sparse_file_callback.argtypes = ctypes.c_int, [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, CB, ctypes.c_void_p]
    rc = L.sparse_file_callback(s, False, False, fn, None)
    if err or rc != 0:
        raise OSError("libsparse callback expansion failed rc=%d %s" % (rc, err[:1]))
    if n[0] != raw_size:
        raise OSError("libsparse callback expanded %d bytes, raw is %d" % (n[0], raw_size))
    return h.hexdigest()
