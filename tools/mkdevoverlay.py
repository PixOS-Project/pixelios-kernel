#!/usr/bin/env python3
"""Pack a directory into a SWEETDEV archive for the dev overlay (xnu patch 0117, bsd/dev/sweet_devoverlay.c).

  tools/mkdevoverlay.py DIR OUT.bin [--owner UID:GID]

File DIR/usr/lib/x becomes /usr/lib/x on the phone. Only paths the kernel allowlist accepts are packed (anything
else is an error, not a silent skip); symlinks are refused; total data is capped at 40 MiB. Output is deterministic
(sorted paths, mode = file mode & 0777, one owner, no timestamps). Format (little-endian), the kernel validates it all:
  header: "SWEETDEV" | u32 version=1 | u32 count
  entry (16-byte aligned): u16 pathlen | u16 mode | u32 uid | u32 gid | u32 crc32(data) | u64 size
                           | path | pad16 | data | pad16
parse() / path_check() below are the Python twins of the kernel checks (used by the tests).
"""
import argparse, os, stat, struct, sys, zlib

MAGIC, VERSION, HDR, ENT = b'SWEETDEV', 1, 16, 24
MAX_COUNT, MAX_PATH, MAX_TOTAL = 4096, 1000, 40 << 20   # 40 MiB: the AVB boot image is 64 MiB and holds ~16 MiB already
# (prefix, kind): D = directory prefix, X = exact path, N = file name prefix in one directory
ALLOW = [('/usr/lib/', 'D'), ('/usr/local/', 'D'), ('/private/var/dev/', 'D'),
         ('/System/Library/xpc/launchd.plist', 'X'), ('/System/Library/LaunchDaemons/org.pixelios.', 'N')]


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


def pack(entries):
    """entries: list of (path:str, mode, uid, gid, data) -> archive bytes."""
    if len(entries) > MAX_COUNT:
        raise ValueError('too many files (%d > %d)' % (len(entries), MAX_COUNT))
    out = bytearray(MAGIC + struct.pack('<II', VERSION, len(entries)))
    for path, mode, uid, gid, data in entries:
        pb = path.encode('utf-8', 'surrogateescape')
        if len(pb) > MAX_PATH:
            raise ValueError('path too long: ' + path)
        out += struct.pack('<HHIIIQ', len(pb), mode, uid, gid, zlib.crc32(data) & 0xffffffff, len(data)) + pb
        out += b'\0' * (_pad(len(out)) - len(out)) + data
        out += b'\0' * (_pad(len(out)) - len(out))
    return bytes(out)


def parse(buf):
    """Validate exactly like the kernel; returns [(path, mode, uid, gid, data, crc_ok)] or raises ValueError (a bad crc only
    flags that entry, as in the kernel)."""
    n = len(buf)
    if n < HDR or buf[:8] != MAGIC:
        raise ValueError('bad magic')
    ver, count = struct.unpack_from('<II', buf, 8)
    if ver != VERSION:
        raise ValueError('bad version')
    if count > MAX_COUNT:
        raise ValueError('bad count')
    off, ents = HDR, []
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
        off = _pad(off + size)
        if off > n:
            raise ValueError('truncated padding')
        ents.append((path.decode('utf-8', 'surrogateescape'), mode, uid, gid, data, zlib.crc32(data) & 0xffffffff == crc))
    return ents


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
            entries.append((path, st.st_mode & 0o777, uid, gid, data))
    entries.sort(key=lambda e: e[0])
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dir'); ap.add_argument('out')
    ap.add_argument('--owner', default='0:0', help='UID:GID of every file (default 0:0)')
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
        blob = pack(entries)
        parse(blob)                                        # the packer's own output must pass the kernel checks
    except ValueError as e:
        sys.exit('mkdevoverlay: %s' % e)
    open(a.out, 'wb').write(blob)
    print('mkdevoverlay: %d files, %d bytes -> %s' % (len(entries), len(blob), a.out))


if __name__ == '__main__':
    main()
