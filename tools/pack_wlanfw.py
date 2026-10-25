#!/usr/bin/env python3
"""Pack the BCM4389 firmware, NVRAM and CLM blob into one container for xnu (docs/research/39 s.3, 42 s.7 U10).

  pack_wlanfw.py OUT --fw FILE --nvram FILE --clm FILE

Layout (little endian; every offset is 8-byte aligned; the blobs stay outside git, rule 4):
  0x00  char  magic[8] = "WLANFWB1"
  0x08  u32   version = 1
  0x0c  u32   count
  0x10  entry[count] { char tag[4]; u32 length; u32 offset (from container start); u32 crc32 (zlib) }
Tags: "FW  ", "NVRM", "CLM ". The container is appended to the boot payload by mkpayload.py --wlanfw.
"""
import argparse, struct, sys, zlib

MAGIC = b'WLANFWB1'
VERSION = 1
ENTRY = 16
TAGS = (b'FW  ', b'NVRM', b'CLM ')


def pack(blobs):
    """blobs: [(tag, bytes)] -> container bytes. Refuses an unknown tag, an empty blob or a duplicate tag."""
    seen = set()
    for tag, data in blobs:
        if tag not in TAGS or tag in seen or not data:
            raise ValueError(f'bad entry {tag!r}')
        seen.add(tag)
    out = bytearray(16 + ENTRY * len(blobs))
    struct.pack_into('<8sII', out, 0, MAGIC, VERSION, len(blobs))
    for i, (tag, data) in enumerate(blobs):
        out += b'\0' * (-len(out) & 7)
        struct.pack_into('<4sIII', out, 16 + ENTRY * i, tag, len(data), len(out), zlib.crc32(data))
        out += data
    out += b'\0' * (-len(out) & 7)
    return bytes(out)


def parse(buf):
    """container bytes -> {tag: bytes}; raises ValueError on any structural or CRC error."""
    if len(buf) < 16 or buf[:8] != MAGIC:
        raise ValueError('no WLANFWB1 magic')
    version, count = struct.unpack_from('<II', buf, 8)
    if version != VERSION or count == 0 or count > len(TAGS) or 16 + ENTRY * count > len(buf):
        raise ValueError('bad header')
    res = {}
    for i in range(count):
        tag, length, off, crc = struct.unpack_from('<4sIII', buf, 16 + ENTRY * i)
        if tag not in TAGS or tag in res or off & 7 or off < 16 + ENTRY * count or length == 0 or off + length > len(buf):
            raise ValueError(f'bad entry {i}')
        data = bytes(buf[off:off + length])
        if zlib.crc32(data) != crc:
            raise ValueError(f'crc mismatch in entry {tag!r}')
        res[tag] = data
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--fw', required=True); ap.add_argument('--nvram', required=True); ap.add_argument('--clm', required=True)
    a = ap.parse_args()
    blob = pack([(t, open(p, 'rb').read()) for t, p in ((b'FW  ', a.fw), (b'NVRM', a.nvram), (b'CLM ', a.clm))])
    open(a.out, 'wb').write(blob)
    for tag, data in parse(blob).items():
        print(f'{tag.decode()!r} {len(data)} bytes crc32 {zlib.crc32(data):#010x}')
    print(f'container {len(blob)} bytes, crc32 {zlib.crc32(blob):#010x}')
    print('RESULT: PASS pack_wlanfw')
