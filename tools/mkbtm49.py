#!/usr/bin/env python3
"""mkbtm49: byte-patch the stock bluetoothd (BT-S3 row M49, cbz->nop at 0x66e4c) and fix its code-directory page hash; called by hand/run plans for the BT-S4b overlay, input never in git

  tools/mkbtm49.py --in STOCK --out PATCHED [--check]     --check verifies the input only and writes nothing
  tools/mkbtm49.py --self-test                            synthetic Mach-O fixtures (also run by tools/test_mkbtm49.py)

At file offset 0x66e4c (vaddr 0x100066e4c, __TEXT fileoff 0) the stock binary has `cbz w0, 0x100066f00` (bytes a0 05 00 34), right after the
call on the control name string at 0x1006d346a; `nop` (1f 20 03 d5) makes it take the non-Skywalk path (TIOCSETA on the control fd)
[bin: disassembled 2026-11-03 from the flashed root bdbb8c62 with capstone; decision docs/research/BT-S3-transport-decision.md M49].
The input is refused unless it is the exact stock instruction there (rule 7: never patch a different build).
The Mach-O signature carries per-page hashes: all slots of the input are recomputed first (refuse on any mismatch = oracle that the parser is right),
then the slot of the patched page in every CodeDirectory is recomputed. The CMS signature is not touched; whether this kernel checks pages is open [unknown].
Apple binaries never enter git: input and output live in the gitignored artifacts dir. Prints a loud `BT M49:` line (rule 8).
Last line: RESULT: PASS|FAIL bt-m49-... / mkbtm49 self-test"""
import argparse, hashlib, struct, sys, tempfile, os

OFF = 0x66e4c
STOCK = bytes.fromhex('a0050034')
NOP = bytes.fromhex('1f2003d5')
HASHES = {1: ('sha1', 20), 2: ('sha256', 32), 3: ('sha256', 20), 4: ('sha384', 48)}


class Refuse(Exception):
    pass


def code_directories(d):
    if d[:4] != bytes.fromhex('cffaedfe'):
        raise Refuse('not a thin 64-bit little-endian Mach-O')
    ncmds = struct.unpack_from('<I', d, 16)[0]
    off, sig = 32, None
    for _ in range(ncmds):
        cmd, sz = struct.unpack_from('<II', d, off)
        if cmd == 0x1d:
            sig = struct.unpack_from('<II', d, off + 8)
        off += sz
    if sig is None:
        raise Refuse('no LC_CODE_SIGNATURE')
    base = sig[0]
    magic, _length, count = struct.unpack_from('>III', d, base)
    if magic != 0xfade0cc0:
        raise Refuse('signature is not an embedded SuperBlob')
    out = []
    for i in range(count):
        _t, boff = struct.unpack_from('>II', d, base + 12 + 8 * i)
        if struct.unpack_from('>I', d, base + boff)[0] == 0xfade0c02:
            out.append(base + boff)
    if not out:
        raise Refuse('no CodeDirectory')
    return out


def rows(d, cd):
    (_m, _l, _v, _f, hoff, _i, _ns, ncode, limit, hsize, htype, _p, psz) = struct.unpack_from('>IIIIIIIIIBBBB', d, cd)
    if htype not in HASHES or HASHES[htype][1] != hsize:
        raise Refuse('unsupported CodeDirectory hash type %d size %d' % (htype, hsize))
    return HASHES[htype][0], hsize, 1 << psz, ncode, limit, cd + hoff


def bad_slots(d, cds):
    bad = 0
    for cd in cds:
        name, hsize, page, ncode, limit, hb = rows(d, cd)
        for i in range(ncode):
            if hashlib.new(name, bytes(d[i * page:min((i + 1) * page, limit)])).digest()[:hsize] != bytes(d[hb + i * hsize:hb + (i + 1) * hsize]):
                bad += 1
    return bad


def patch(d, check=False):
    """d: bytearray (modified in place unless check). Returns (message lines)."""
    cds = code_directories(d)
    if bytes(d[OFF:OFF + 4]) != STOCK:
        raise Refuse('bytes at %#x are %s, not the stock cbz %s (different build)' % (OFF, bytes(d[OFF:OFF + 4]).hex(), STOCK.hex()))
    n = bad_slots(d, cds)
    if n:
        raise Refuse('%d stock page hashes do not match their pages (parser or input wrong)' % n)
    msg = ['BT M49: input ok: stock cbz at %#x, %d CodeDirectories, every page hash matches' % (OFF, len(cds))]
    if check:
        return msg
    d[OFF:OFF + 4] = NOP
    for cd in cds:
        name, hsize, page, ncode, limit, hb = rows(d, cd)
        i = OFF // page
        if i >= ncode:
            raise Refuse('patched page %d is outside the CodeDirectory (%d slots)' % (i, ncode))
        d[hb + i * hsize:hb + (i + 1) * hsize] = hashlib.new(name, bytes(d[i * page:min((i + 1) * page, limit)])).digest()[:hsize]
    if bad_slots(d, cds):
        raise Refuse('page hashes do not verify after the patch')
    msg.append('BT M49: bluetoothd patched: cbz->nop at %#x (non-Skywalk control path), %d CodeDirectory page hash(es) recomputed, CMS signature untouched; sha256 %s' % (OFF, len(cds), hashlib.sha256(d).hexdigest()))
    return msg


def fixture(htype=2, stock=True):
    """Synthetic thin Mach-O: one LC_CODE_SIGNATURE with a SuperBlob holding one CodeDirectory over 4 KiB pages."""
    name, hsize = HASHES[htype]
    code = bytearray(0x68000)
    code[:4] = bytes.fromhex('cffaedfe')
    struct.pack_into('<I', code, 16, 1)
    code[OFF:OFF + 4] = STOCK if stock else NOP
    sigoff = len(code)
    ncode = (len(code) + 4095) // 4096
    hoff = 44
    cd = bytearray(struct.pack('>IIIIIIIIIBBBB', 0xfade0c02, 0, 0x20400, 0, hoff, 0, 0, ncode, len(code), hsize, htype, 0, 12))
    cd += bytes(hoff - len(cd)) + bytes(ncode * hsize)
    struct.pack_into('>I', cd, 4, len(cd))
    sb = bytearray(struct.pack('>III', 0xfade0cc0, 0, 1) + struct.pack('>II', 0, 20)) + cd
    struct.pack_into('>I', sb, 4, len(sb))
    d = bytearray(code) + sb
    struct.pack_into('<II', d, 32, 0x1d, 16)
    struct.pack_into('<II', d, 40, sigoff, len(sb))
    cdoff = sigoff + 20
    for i in range(ncode):
        h = hashlib.new(name, bytes(d[i * 4096:min((i + 1) * 4096, len(code))])).digest()[:hsize]
        d[cdoff + hoff + i * hsize:cdoff + hoff + (i + 1) * hsize] = h
    return d


def self_test():
    ok = True
    for htype in (1, 2, 3, 4):
        d = fixture(htype)
        lines = patch(d)
        ok &= d[OFF:OFF + 4] == NOP and len(lines) == 2 and bad_slots(d, code_directories(d)) == 0
        ok &= bytes(d[OFF + 4:OFF + 64]) == bytes(fixture(htype)[OFF + 4:OFF + 64])      # only the 4 bytes change in the code
    for name, mutate, want in (
        ('wrong instruction', lambda d: d.__setitem__(slice(OFF, OFF + 4), NOP), 'not the stock cbz'),
        ('corrupt page hash', lambda d: d.__setitem__(len(d) - 3, d[len(d) - 3] ^ 1), 'do not match'),
        ('not a Mach-O', lambda d: d.__setitem__(slice(0, 4), b'\0\0\0\0'), 'not a thin'),
    ):
        d = fixture()
        mutate(d)
        try:
            patch(d)
            print('self-test: mutant not refused: ' + name)
            ok = False
        except Refuse as e:
            if want not in str(e):
                print('self-test: wrong refusal for %s: %s' % (name, e))
                ok = False
    d = fixture()
    before = bytes(d)
    patch(d, check=True)
    ok &= bytes(d) == before
    print('RESULT: %s mkbtm49 self-test' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv):
    if '--self-test' in argv:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--out')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args(argv)
    if not a.check and not a.out:
        ap.error('--out is required unless --check')
    d = bytearray(open(a.src, 'rb').read())
    try:
        for line in patch(d, a.check):
            print(line)
    except Refuse as e:
        print('BT M49: refused: %s' % e)
        print('RESULT: FAIL bt-m49-%s %s' % ('check' if a.check else 'patch', e))
        return 1
    if not a.check:
        with open(a.out, 'wb') as f:
            f.write(d)
    print('RESULT: PASS bt-m49-%s' % ('check' if a.check else 'patch'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
