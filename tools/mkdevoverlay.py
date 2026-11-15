#!/usr/bin/env python3
"""Pack a directory into a SWEETDEV archive for the dev overlay (xnu patch 0117, bsd/dev/sweet_devoverlay.c).

  tools/mkdevoverlay.py DIR OUT.bin [--owner UID:GID] [--compress]

File DIR/usr/lib/x becomes /usr/lib/x on the phone. Only paths the kernel allowlist accepts are packed (anything
else is an error, not a silent skip); symlinks are refused; total data is capped at 40 MiB (kernel); a packed overlay of 21 MiB or more is a RESULT: FAIL (observed device limit, HANDOFF.md:174). Output is deterministic
(sorted paths, mode = file mode & 0777, one owner, no timestamps). Format (little-endian), the kernel validates it all:
  header: "SWEETDEV" | u32 version=1 | u32 count
  entry (16-byte aligned): u16 pathlen | u16 mode | u32 uid | u32 gid | u32 crc32(data) | u64 size
                           | path | pad16 | data | pad16
--compress writes version 2 (xnu patch 0154): "size" is the stored length and the stored data is u64 inflated size | one zlib
stream (level 9); crc32 stays over the INFLATED bytes. Limits (kernel too): stored length <= 48 MiB, inflated size per entry and
in total <= 40 MiB (a zip bomb is refused whole). Without --compress the output is version 1, byte-identical to before.
parse() / path_check() below are the Python twins of the kernel checks (used by the tests).
"""
import argparse, os, stat, struct, sys, zlib

MAGIC, VERSION, VERSION2, HDR, ENT = b'SWEETDEV', 1, 2, 16, 24
MAX_ZLEN = 48 << 20   # v2 stored length of one entry (kernel SD_MAXZ)
MAX_COUNT, MAX_PATH, MAX_TOTAL = 4096, 1000, 40 << 20   # 40 MiB: the AVB boot image is 64 MiB and holds ~16 MiB already
# Observed device limit (docs/HANDOFF.md:174, research 41): a 14 MiB overlay boots and applies, 21 MiB and 24 MiB make the phone fall back to
# fastboot ~4-5 s after `fastboot boot` with no log. The limit lies between; the packer accepts up to MAX_TOTAL (kernel parse), so refuse loudly.
OVERLAY_OK_OBSERVED, OVERLAY_FAIL_OBSERVED = 14 << 20, 21 << 20
# (prefix, kind): D = directory prefix, X = exact path, N = file name prefix in one directory
ALLOW = [('/usr/lib/', 'D'), ('/usr/local/', 'D'), ('/private/var/dev/', 'D'),
         ('/System/Library/xpc/launchd.plist', 'X'), ('/System/Library/LaunchDaemons/org.pixelios.', 'N'),
         ('/Applications/BTApp.app/BTApp', 'X'),                                              # xnu patch 0280
         ('/private/var/mobile/Library/Preferences/com.apple.MobileBluetooth.debug.plist', 'X'),
         ('/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist', 'X')]   # xnu patch 0317
# xnu patch 0280: an empty file DIR/<path>.sweetwhiteout packs a WHITEOUT entry (mode 0xFFFF, no data) that removes <path> on the
# phone; the kernel accepts it only below WH_ALLOW and refuses it (mode above 0777) without 0280
WHITEOUT_SUFFIX, WHITEOUT_MODE = '.sweetwhiteout', 0xFFFF
WH_ALLOW = [('/usr/local/', 'D'), ('/private/var/dev/', 'D'), ('/usr/lib/libsweetgl.dylib', 'X')]
MOBILE = ('/private/var/mobile/', 501, 501)   # files there belong to mobile (standard root: MobileBluetooth.debug.plist 501:501 0600)


def path_check(p):
    """None if the kernel accepts this path, else the reason (same rules as sd_path_check in the kernel)."""
    b = p.encode('utf-8', 'surrogateescape') if isinstance(p, str) else p
    if not b or b[0:1] != b'/':
        return 'not absolute'
    if any(c < 0x20 or c >= 0x7f for c in b):
        return 'control or non-ASCII byte in path'
    for comp in b[1:].split(b'/'):
        if comp == b'':
            return 'empty component'
        if comp == b'.':
            return "'.' component"
        if comp == b'..':
            return "'..' component"
        if len(comp) > 245:
            return 'component longer than 245 bytes'
    if b.lower().endswith((b'.sweettmp', b'.sweetorig')):
        return 'reserved suffix'
    for pfx, kind in ALLOW:
        pb = pfx.encode()
        if not b.startswith(pb):
            continue
        rest = b[len(pb):]
        if (kind == 'X' and rest) or (kind == 'N' and (not rest or b'/' in rest)) or (kind == 'D' and not rest):
            continue
        return None
    return 'not in allowlist'


def _pad(n):
    return (n + 15) & ~15


def pack(entries, version=VERSION):
    """entries: list of (path:str, mode, uid, gid, data) -> archive bytes (version 2: zlib-compressed entries)."""
    if len(entries) > MAX_COUNT:
        raise ValueError('too many files (%d > %d)' % (len(entries), MAX_COUNT))
    out = bytearray(MAGIC + struct.pack('<II', version, len(entries)))
    for path, mode, uid, gid, data in entries:
        pb = path.encode('utf-8', 'surrogateescape')
        if len(pb) > MAX_PATH:
            raise ValueError('path too long: ' + path)
        stored = data if version == VERSION else struct.pack('<Q', len(data)) + zlib.compress(data, 9)
        out += struct.pack('<HHIIIQ', len(pb), mode, uid, gid, zlib.crc32(data) & 0xffffffff, len(stored)) + pb
        out += b'\0' * (_pad(len(out)) - len(out)) + stored
        out += b'\0' * (_pad(len(out)) - len(out))
    return bytes(out)


def _inflate_exact(z, usize):
    """Inflated bytes if z is ONE complete zlib stream of exactly usize bytes with nothing after it, else None (never more than usize+1 bytes)."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(z, usize + 1)
        ok = len(out) == usize and d.eof and not d.unused_data and not d.unconsumed_tail
    except zlib.error:   # lint-silent: expected a corrupt zlib stream is reported by the caller as an invalid file via the None return
        return None
    return out if ok else None


def parse(buf):
    """Validate exactly like the kernel; returns [(path, mode, uid, gid, data, crc_ok)] or raises ValueError (a bad crc only
    flags that entry, as in the kernel). Version 2: data is the inflated bytes; a bad/short/long/trailing-garbage stream or a
    bad crc gives crc_ok False and data b'' (kernel: that entry is refused alone); an inflated size above 40 MiB, a stored
    length above 48 MiB or more than 40 MiB inflated in total raises (kernel: archive refused whole)."""
    n = len(buf)
    if n < HDR or buf[:8] != MAGIC:
        raise ValueError('bad magic')
    ver, count = struct.unpack_from('<II', buf, 8)
    if ver not in (VERSION, VERSION2):
        raise ValueError('bad version')
    if count > MAX_COUNT:
        raise ValueError('bad count')
    off, ents, total = HDR, [], 0
    for i in range(count):
        if off > n or n - off < ENT:
            raise ValueError('truncated entry header')
        plen, mode, uid, gid, crc, size = struct.unpack_from('<HHIIIQ', buf, off)
        off += ENT
        if plen == 0 or plen > MAX_PATH or plen > n - off:
            raise ValueError('bad pathlen')
        path = bytes(buf[off:off + plen])
        off = _pad(off + plen)
        if off > n or size > n - off:
            raise ValueError('bad size')
        data = bytes(buf[off:off + size])
        if ver == VERSION2:
            if size < 8 or size > MAX_ZLEN:
                raise ValueError('bad stored length')
            usize = struct.unpack_from('<Q', data)[0]
            if usize > MAX_TOTAL:
                raise ValueError('inflated size too big')
            total += usize
            if total > MAX_TOTAL:
                raise ValueError('inflated total too big')
        off = _pad(off + size)
        if off > n:
            raise ValueError('truncated padding')
        if ver == VERSION2:
            data = _inflate_exact(data[8:], usize)
            if data is None:
                ents.append((path.decode('utf-8', 'surrogateescape'), mode, uid, gid, b'', False))
                continue
        ok = zlib.crc32(data) & 0xffffffff == crc
        ents.append((path.decode('utf-8', 'surrogateescape'), mode, uid, gid, data if ok or ver == VERSION else b'', ok))
    return ents


def whiteout_guard(paths):
    """review 0280 F1: the kernel cannot tell a never-replaced root-image file, so no whiteout may name a path the flashed
    standard root image HAS (e.g. /usr/local/bin/wifid-probe). Reads it with tools/hfs_read.py (image from the newest completed
    line of flashed-userdata.txt, as tools/overlay_std.py). Exits on any doubt; MKDEVOVERLAY_WH_IMAGE overrides the image (tests)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # run as python3 -I: the tools dir is not on the path
    import overlay_std
    img = os.environ.get('MKDEVOVERLAY_WH_IMAGE')
    if not img:
        root, why = overlay_std.std_root()
        if root is None:
            sys.exit('mkdevoverlay: whiteout refused: the standard root image is unknown (%s)' % why)
        img = root[2]
    import hfs_read
    try:
        h = hfs_read.HFS(img)
        for p in paths:
            if h.read_file(p) is not None:
                sys.exit('mkdevoverlay: whiteout of %s refused: the standard root image %s has that file' % (p, img))
    except (hfs_read.Bad, OSError) as e:
        sys.exit('mkdevoverlay: whiteout refused: cannot read the standard root image %s (%s)' % (img, e))


def collect(root, uid=0, gid=0):
    entries, total = [], 0
    seen = {}
    for d, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            full = os.path.join(d, name)
            if os.path.islink(full):
                sys.exit('mkdevoverlay: refusing symlink %s' % full)
        for name in sorted(files):
            full = os.path.join(d, name)
            st = os.lstat(full)                            # never open a FIFO/socket/device (open would block)
            if not stat.S_ISREG(st.st_mode):
                sys.exit('mkdevoverlay: not a regular file %s' % full)
            path = '/' + os.path.relpath(full, root).replace(os.sep, '/')
            wh = path.endswith(WHITEOUT_SUFFIX)
            if wh:
                path = path[:-len(WHITEOUT_SUFFIX)]
                if st.st_size != 0:
                    sys.exit('mkdevoverlay: whiteout marker %s must be empty' % full)
                if not any((path.startswith(p) and len(path) > len(p)) if k == 'D' else path == p for p, k in WH_ALLOW):
                    sys.exit('mkdevoverlay: whiteout of %s refused: only below %s' % (path, ', '.join(p for p, _ in WH_ALLOW)))
            why = path_check(path)
            if why:
                sys.exit('mkdevoverlay: %s is not allowed on the phone (%s); allowed: %s'
                         % (path, why, ', '.join(p for p, _ in ALLOW)))
            if path.lower() in seen:                       # the root may be case-insensitive HFS+: both would be one file (review 19 L2)
                sys.exit('mkdevoverlay: %s and %s differ only by case' % (seen[path.lower()], path))
            seen[path.lower()] = path
            data = open(full, 'rb').read()
            total += len(data)
            if total > MAX_TOTAL:
                sys.exit('mkdevoverlay: more than 40 MiB of files')
            if uid != 0 and (path.startswith('/usr/lib/') or path.startswith('/System/')):
                print('mkdevoverlay: WARNING %s will be owned by uid %d, not root' % (path, uid), file=sys.stderr)
            fu, fg = (MOBILE[1], MOBILE[2]) if path.startswith(MOBILE[0]) else (uid, gid)
            entries.append((path, WHITEOUT_MODE if wh else st.st_mode & 0o777, fu, fg, data))
    entries.sort(key=lambda e: e[0])
    wh = [e[0] for e in entries if e[1] == WHITEOUT_MODE]
    if wh:
        whiteout_guard(wh)
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dir'); ap.add_argument('out')
    ap.add_argument('--owner', default='0:0', help='UID:GID of every file (default 0:0)')
    ap.add_argument('--compress', action='store_true', help='write version 2 (zlib-compressed entries, needs xnu patch 0154)')
    a = ap.parse_args()
    try:
        uid, gid = (int(x) for x in a.owner.split(':'))
        assert 0 <= uid < 2**32 and 0 <= gid < 2**32
    except (ValueError, AssertionError):
        sys.exit('mkdevoverlay: --owner needs UID:GID')
    if not os.path.isdir(a.dir) or os.path.islink(a.dir):
        sys.exit('mkdevoverlay: %s is not a directory' % a.dir)
    entries = collect(a.dir, uid, gid)
    if not entries:
        sys.exit('mkdevoverlay: no files in %s' % a.dir)
    try:
        blob = pack(entries, VERSION2 if a.compress else VERSION)
        parse(blob)                                        # the packer's own output must pass the kernel checks
    except ValueError as e:
        sys.exit('mkdevoverlay: %s' % e)
    if len(blob) >= OVERLAY_FAIL_OBSERVED:
        print('RESULT: FAIL overlay %d bytes (%.1f MiB) >= %d MiB: on the device a 21 MiB and a 24 MiB overlay were observed to make the phone '
              'fall back to fastboot with no log (docs/HANDOFF.md:174); the limit lies above 14 MiB; deliver this through a root flash'
              % (len(blob), len(blob) / 1048576, OVERLAY_FAIL_OBSERVED >> 20))
        sys.exit(1)
    if len(blob) > OVERLAY_OK_OBSERVED:
        print('mkdevoverlay: WARNING overlay is %.1f MiB; only 14 MiB is proven to boot, 21 MiB is proven to fall back to fastboot '
              '(docs/HANDOFF.md:174), sizes in between are unmeasured' % (len(blob) / 1048576), file=sys.stderr)
    open(a.out, 'wb').write(blob)
    print('mkdevoverlay: %d files, %d bytes -> %s' % (len(entries), len(blob), a.out))


if __name__ == '__main__':
    main()
