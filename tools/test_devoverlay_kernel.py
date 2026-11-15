"""Compile the kernel's pure archive/path/inflate functions (xnu patches 0117 + 0154, between the host-test markers; system zlib stands in for libkern's) on the host with
ASan/UBSan and compare them with the Python twin tools/mkdevoverlay.py on a seeded corpus; then check that known
mutants of the C code are caught (review 18 finding 4). Skips when gcc/cc is missing."""
import os, random, re, shutil, struct, subprocess, sys, tempfile, unittest, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkdevoverlay as m

PATCH = os.path.join(HERE, '..', 'xnu', 'patches', '0117-sweet-dev-overlay.patch')
PATCH2 = os.path.join(HERE, '..', 'xnu', 'patches', '0154-sweet-devoverlay-zlib.patch')
CC = shutil.which('gcc') or shutil.which('cc')

MAIN = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <errno.h>
#include <zlib.h>
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define SD_ALLOC(n)   calloc(1, (n))
#define SD_FREE(p, n) free(p)
%s
/* stdin: repeated [u32 len][len bytes]; one result block per archive, ending "--" */
int main(void) {
	uint32_t n;
	uint8_t *vbuf = malloc(SD_CHUNK);
	while (fread(&n, 4, 1, stdin) == 1) {
		uint8_t *b = malloc(n ? n : 1);                    /* exact size: ASan sees any read past the end */
		if (fread(b, 1, n, stdin) != n) return 2;
		uint32_t count = 0, ver = 0;
		if (sd_hdr(b, n, &ver, &count) || sd_walk(b, n, ver, count)) { puts("ERR"); }
		else {
			uint64_t off = SD_HDR; struct sd_ent e;
			for (uint32_t i = 0; i < count; i++) {
				sd_next(b, n, ver, &off, &e);
				char path[SD_MAXPATH + 1], tmp[SD_MAXPATH + 16], orig[SD_MAXPATH + 16], sh[160]; size_t mk = 0;
				const char *why = sd_path_check(&e, path, &mk);
				tmp[0] = orig[0] = 0;
				if (!why && sd_names(path, tmp, sizeof(tmp), orig, sizeof(orig))) why = "names";
				sd_show(&e, sh, sizeof(sh));                /* exact-size array: ASan sees a one-byte overflow */
				char t2[40], o2[40];                        /* too-small buffers: sd_names must refuse, never truncate */
				int small = why ? 0 : sd_names(path, t2, sizeof(t2), o2, sizeof(o2)) != 0;
				int vok = sd_verify(&e, vbuf);              /* v2: the kernel's streaming inflate; a bad stream/crc is refused alone */
				printf("E %%s|%%u|%%u|%%u|%%llu|%%d|%%lld|%%s|%%s|%%d|%%s\n", why ? why : "ok", e.mode, e.uid, e.gid,
				    (unsigned long long)((e.z && !vok) ? 0 : e.usize), vok, why ? 0LL : (long long)mk, tmp, orig, small, sh);
			}
		}
		puts("--"); free(b);
	}
	free(vbuf);
	return 0;
}
'''


def full_source():
    """bsd/dev/sweet_devoverlay.c as patch 0117 creates it, then patch 0154 applied with git apply (as tools/xnu-local-build does)."""
    s = open(PATCH).read()
    s = s[s.index('+++ b/bsd/dev/sweet_devoverlay.c'):]
    lines = [l[1:] for l in s.split('\n')[2:] if l.startswith('+')]
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, 'bsd', 'dev'))
        f = os.path.join(d, 'bsd', 'dev', 'sweet_devoverlay.c')
        open(f, 'w').write('\n'.join(lines) + '\n')
        r = subprocess.run(['git', 'apply', os.path.abspath(PATCH2)], cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, 'patch 0154 does not apply on 0117: ' + r.stderr
        return open(f).read()
    finally:
        shutil.rmtree(d, True)


def kernel_source():
    src = full_source()
    a, b = src.index('/* host-test begin'), src.index('/* host-test end */')
    return src[a:b]


def build(src, out):
    code = MAIN % src
    c = out + '.c'
    open(c, 'w').write(code)
    flags = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined']   # LSan is on with ASan: a missing inflateEnd fails the run
    libs = ['-lz']   # no fallback without sanitizers (review 19 M1-new d)
    r = subprocess.run([CC, '-O1', '-Wall', '-Werror=implicit-function-declaration', *flags, '-o', out, c, *libs], capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr[-2000:] or 'sanitizer build failed')


_PYC = {}


def py(blob):
    if blob not in _PYC:
        _PYC[blob] = _py(blob)
    return _PYC[blob]


def _py(blob):
    try:
        ents = m.parse(blob)
    except ValueError:
        return ['ERR', '--']
    return ['E %s|%d|%d|%d|%d|%d|%d|%s|%s|%d|%s' % (m.path_check(p) or 'ok', mo, u, g, len(d), int(c), mkmin(p) if not m.path_check(p) else 0,
            *(((p + '.sweettmp', p + '.sweetorig') if not m.path_check(p) else ('', ''))), int(len(p) + 10 >= 40 and not m.path_check(p)), shown(p)) for p, mo, u, g, d, c in ents] + ['--']


def mkmin(p):
    """Twin of *mkmin: len(prefix)-1 under a K_DIR prefix, -1 (size_t max) for K_NAME/K_EXACT."""
    for pfx in ('/usr/lib/', '/usr/local/', '/private/var/dev/'):
        if p.startswith(pfx):
            return len(pfx) - 1
    return -1


def shown(p):
    b = p.encode('utf-8', 'surrogateescape')[:159]            # sd_show with a 160-byte buffer
    return ''.join(chr(c) if 0x20 <= c < 0x7f else '?' for c in b)


def raw(plen, mode, uid, gid, crc, size, path, data=b''):
    e = struct.pack('<HHIIIQ', plen, mode, uid, gid, crc, size) + path
    e += b'\0' * (-len(e) % 16)
    e = e + data
    return e + b'\0' * (-len(e) % 16)


def hdr(count, ver=1):
    return b'SWEETDEV' + struct.pack('<II', ver, count)


def corpus():
    R = random.Random(18)
    paths = ['/usr/lib/a', '/usr/lib/../x', '/usr/lib//x', '/usr/libx', '/usr/local/a/b', '/private/var/dev/h', '/etc/x',
             '/System/Library/xpc/launchd.plist', '/System/Library/LaunchDaemons/org.pixelios.', '/System/Library/LaunchDaemons/org.pixelios.t', '/System/Library/LaunchDaemons/org.pixelios.d/x',
             '/usr/lib/a.sweetorig', '/usr/lib/a.SweetOrig', '/usr/lib/a.SWEETTMP', '/usr/lib/a\x00b', '/usr/lib/a\x7fb', '/usr/lib/a\x1fb', '/',
             '/usr/lib/', '/usr/lib/.', '/usr/lib/a/', '/usr/lib/\xe9', '/usr/lib/' + 'n' * 245, '/usr/lib/' + 'n' * 246, '/usr/lib/x/..', '/usr/lib/x/../y',
             '/System/Library/xpc/launchd.plistX', '/System/Library/xpc/launchd.plist/x', '/usr/lib/' + 'a' * 291,
             '/usr/lib/' + 'b' * 20, '/usr/lib/' + 'b' * 21]   # last: 300 bytes, sd_show cap 160
    out = []
    # crafted: overflow and bound cases
    p = b'/usr/lib/a'
    for size in (2**64 - 1, 2**64 - 8, 2**64 - 16, 2**63, 2**32, 4096):
        out.append(hdr(1) + raw(len(p), 0o644, 0, 0, 0, size, p, b'abc'))
    for plen in (0, 1001, 65535, 11, 2000):
        out.append(hdr(1) + raw(plen, 0o644, 0, 0, 0, 3, p, b'abc'))
    out.append(hdr(1) + raw(1001, 0o644, 0, 0, 0, 0, b'/usr/lib/' + b'a' * 992) + b'\0' * 64)   # 1001-byte path with room
    out.append(hdr(4097) + b''.join(raw(len(p), 0o644, 0, 0, 0, 0, p) for _ in range(4097)))
    out.append(hdr(4096) + b''.join(raw(len(p), 0o644, 0, 0, 0, 0, p) for _ in range(4096)))
    out.append(hdr(0xffffffff)); out.append(hdr(1, 3)); out.append(hdr(1, 0)); out.append(b'SWEETDEV'); out.append(b'')
    good = m.pack([('/usr/lib/a', 0o644, 0, 0, b'abc')])
    out.append(good[:-1]); out.append(good[:-16]); out.append(good[:40])
    for q in paths:                                           # every path alone, with and without the final padding
        blob = m.pack([(q, 0o644, 0, 0, b'zz')])
        out.append(blob); out.append(blob[:-1])
    out += corpus_v2(R)
    for i in range(1500):
        ents = [(R.choice(paths), R.randrange(0, 0o1000), R.randrange(0, 600), 0, bytes(R.randrange(256) for _ in range(R.randrange(40))))
                for _ in range(R.randrange(1, 4))]
        blob = bytearray(m.pack(ents, 2 if i % 3 == 0 else 1))
        for _ in range(R.randrange(0, 4)):
            blob[R.randrange(len(blob))] = R.randrange(256)
        if R.random() < 0.3:
            blob = blob[:R.randrange(len(blob) + 1)]
        if R.random() < 0.2:
            blob += bytes(R.randrange(40))
        out.append(bytes(blob))
    return out


def z2(path, data, decl=None, tail=b'', zmut=None, crc=None, mode=0o644):
    """One v2 entry with controllable lies (declared inflated size, trailing bytes, stream mutation, crc)."""
    z = zlib.compress(data, 9)
    z = zmut(z) if zmut else z
    stored = struct.pack('<Q', len(data) if decl is None else decl) + z + tail
    return raw(len(path), mode, 0, 0, zlib.crc32(data) & 0xffffffff if crc is None else crc, len(stored), path, stored)


def corpus_v2(R):
    """Version 2: good streams (several chunks), every way a stream or its declaration can lie, and the zip bombs."""
    p = b'/usr/lib/a'
    h = lambda n: hdr(n, 2)
    data = bytes(R.randrange(256) for _ in range(300000)) + b'ab' * 700000 + bytes(R.randrange(256) for _ in range(5000))   # 2.4 MB: 3 chunks
    out = [h(1) + z2(p, data), h(1) + z2(p, b''), h(1) + z2(p, b'x'), h(2) + z2(p, b'abc') + z2(b'/usr/lib/b', data[:70000])]
    out.append(h(1) + z2(p, bytes(1 << 20)))                              # exactly one chunk
    out.append(h(1) + z2(p, bytes((1 << 20) + 1)))                        # one chunk and a byte
    out.append(h(1) + z2(p, data, crc=1))                                 # bad crc
    out.append(h(1) + z2(p, b'abc' * 1000, decl=2999)); out.append(h(1) + z2(p, b'abc' * 1000, decl=3001))   # declared size lies
    out.append(h(1) + z2(p, b'abc' * 1000, decl=0)); out.append(h(1) + z2(p, b'', decl=1))
    out.append(h(1) + z2(p, b'abc' * 1000, tail=b'\0')); out.append(h(1) + z2(p, b'abc' * 1000, tail=bytes(40)))   # bytes after the stream end
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:-1]))        # truncated: no adler byte
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:len(z) // 2]))
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:2]))
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:-1] + bytes([z[-1] ^ 1])))   # bad adler32
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: b'\x00' + z[1:]))   # bad zlib header
    out.append(h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:2] + bytes([z[2] ^ 0x40]) + z[3:]))
    # bombs: the stream really inflates to more than it declares
    big = bytes(40 << 20)                                                 # 40 MiB of zeros: a ~40 KB stream
    out.append(h(1) + z2(p, big))                                         # exactly at the cap: accepted
    out.append(h(1) + z2(p, big, decl=1000)); out.append(h(1) + z2(p, big, decl=(1 << 20)))
    out.append(h(1) + z2(p, big[:(1 << 20) + 5], decl=(1 << 20)))         # one byte over a chunk boundary
    out.append(h(1) + z2(p, big, decl=(40 << 20) - 1))
    # a consistent liar (crc over the declared prefix) so the stream-shape check alone must refuse (review devoverlay-z-1 F1)
    rnd = bytes(R.randrange(256) for _ in range(5000))
    out.append(h(1) + z2(p, rnd, decl=4999, crc=zlib.crc32(rnd[:4999])))     # one byte more than declared
    out.append(h(1) + z2(p, b'Z', decl=0, crc=0))
    out.append(h(1) + z2(p, b'abc' * 1000 + b'Z', decl=3000, crc=zlib.crc32(b'abc' * 1000)))
    out.append(h(1) + z2(p, b'abc' * 1000, decl=2999, crc=zlib.crc32((b'abc' * 1000)[:2999])))
    # u64 wrap of the sum without the per-entry cap: 30 + (2^64 - 25) = 5 (review F2)
    out.append(hdr(2, 2) + z2(b'/usr/lib/a', b'abc' * 10) + z2(b'/usr/lib/b', b'x', decl=2 ** 64 - 25))
    out.append(h(1) + z2(p, big[:1000], decl=(40 << 20) + 1))             # declares over the cap: whole archive refused
    out.append(h(1) + z2(p, b'x', decl=2 ** 64 - 1)); out.append(h(1) + z2(p, b'x', decl=2 ** 63))
    out.append(h(2) + z2(p, bytes(30 << 20)) + z2(b'/usr/lib/b', bytes(11 << 20)))   # sum 41 MiB > cap: whole archive refused
    out.append(h(2) + z2(p, bytes(30 << 20)) + z2(b'/usr/lib/b', bytes(10 << 20)))   # sum 40 MiB: ok
    # stored-length bounds: below 8, above 48 MiB
    for stored in (b'', b'\0' * 7):
        out.append(h(1) + raw(len(p), 0o644, 0, 0, 0, len(stored), p, stored))
    big_stored = bytes(8) + b'\0' * ((48 << 20) - 8 + 16)                  # 48 MiB + 8 stored bytes
    out.append(h(1) + raw(len(p), 0o644, 0, 0, 0, len(big_stored), p, big_stored))
    ok_stored = bytes(8) + b'\0' * ((48 << 20) - 8)                        # exactly 48 MiB: not refused by the size cap (the stream is bad)
    out.append(h(1) + raw(len(p), 0o644, 0, 0, 0, len(ok_stored), p, ok_stored))
    good = h(1) + z2(p, data[:50000])
    out.append(good[:-1]); out.append(good[:-16]); out.append(good[:60]); out.append(good + bytes(4096))
    # a v1 archive relabelled v2 and a v2 relabelled v1 are garbage to the other parser
    v1 = m.pack([(p.decode(), 0o644, 0, 0, b'abc' * 100)])
    out.append(v1[:8] + struct.pack('<I', 2) + v1[12:])
    out.append(good[:8] + struct.pack('<I', 1) + good[12:])
    for _ in range(200):                                                  # bit flips in v2 archives
        blob = bytearray(m.pack([('/usr/lib/a', 0o644, 0, 0, bytes(R.randrange(256) for _ in range(R.randrange(3000))) + b'abc' * R.randrange(500))], 2))
        for _ in range(R.randrange(1, 4)):
            blob[R.randrange(len(blob))] ^= 1 << R.randrange(8)
        out.append(bytes(blob))
    return out


def run_c(exe, blobs):
    data = b''.join(struct.pack('<I', len(b)) + b for b in blobs)
    r = subprocess.run([exe], input=data, capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode('utf-8', 'surrogateescape').split('\n')[:-1], r.stderr.decode(errors='replace')[:400]


def mismatches(exe, blobs):
    """Number of archives where C and Python disagree (a crash/ASan abort counts as total mismatch)."""
    rc, out, err = run_c(exe, blobs)
    if rc != 0:
        return len(blobs)
    got, cur = [], []
    for l in out:
        cur.append(l)
        if l == '--':
            got.append(cur); cur = []
    if len(got) != len(blobs):
        return len(blobs)
    return sum(1 for b, g in zip(blobs, got) if py(b) != g)


MUTANTS = [
    # no 'plen > len - off' mutant: equivalent, the aligned-offset check right after it rejects the same archives (plen <= 1000, no wrap)
    ('plen max', 'e->plen > SD_MAXPATH', 'e->plen > 70000'),
    ('size bound', 'if (off > len || e->size > len - off)', 'if (off > len)'),
    ('final padding check', 'if (off > len) {\n\t\treturn EINVAL;\n\t}\n\t*offp', '*offp'),
    ('count max', 'rd32(b + 12) > SD_MAXCOUNT', 'rd32(b + 12) > 0x7fffffffu'),
    ('header len', 'len < SD_HDR || ', ''),
    ('version', '(rd32(b + 8) != SD_VERSION && rd32(b + 8) != SD_VERSION2)', '0'),
    ('version 2 off', 'ver == SD_VERSION2', '0'),
    ('control byte', 'c < 0x20 ||', 'c < 0x1f ||'),
    ('NAME slash', "memchr(out + pl, '/', n - pl)", '0'),
    ('dotdot', "len == 2 && out[comp + 1] == '.' && out[comp + 2] == '.'", '0'),
    ('dot', "len == 1 && out[comp + 1] == '.'", '0'),
    ('empty component', 'if (len == 0) {', 'if (0) {'),
    ('suffix case', "if (c >= 'A' && c <= 'Z') {", 'if (0) {'),
    ('component 245', 'len > 245', 'len > 255'),
    ('crc poly', '0xedb88320u', '0xedb88321u'),
    # review 19 M1-new: mutants that survived the first version
    ('mkmin D', '*mkmin = pl - 1;', '*mkmin = 0;'),
    ('mkmin NAME', "memchr(out + pl, '/', n - pl)) {\n\t\t\t\tcontinue;\n\t\t\t}\n\t\t\t*mkmin = ((size_t)-1);", "memchr(out + pl, '/', n - pl)) {\n\t\t\t\tcontinue;\n\t\t\t}\n\t\t\t*mkmin = 0;"),
    ('NAME exact prefix', 'if (n == pl || memchr', 'if (memchr'),   # review 20 f3
    ('EXACT length', 'if (n != pl) {', 'if (0) {'),
    ('sd_show cap', 'e->plen < cap - 1 ? e->plen : cap - 1', 'e->plen < cap ? e->plen : cap'),
    ('C1 sizeof(tmp)', 'snprintf(tmp, tcap,', 'snprintf(tmp, sizeof(tmp),'),
    ('C1 sizeof(orig)', 'snprintf(orig, ocap,', 'snprintf(orig, sizeof(orig),'),
    ('C1 no truncation check', '(a < 0 || (size_t)a >= tcap || b < 0 || (size_t)b >= ocap)', '0'),
    # no tmp-only truncation mutant: equivalent, the longer .sweetorig always truncates first
    ('non-ASCII byte', 'c >= 0x7f', 'c == 0x7f'),
    # patch 0154 (v2 inflate): the zip-bomb and stream-shape checks. Not mutated, equivalent by design (double defence): the
    # no-progress guard vs the Z_BUF_ERROR/early-end returns in sd_rd_get, and the n/usize argument checks of sd_rd_get (callers never exceed them).
    # the per-entry cap is NOT equivalent to the total cap: without it the u64 sum wraps (30 + (2^64-25) = 5), see corpus_v2
    ('v2 entry cap', 'e->usize > SD_MAXINF', '0'),
    ('v2 more output than declared', 'r->zs.avail_out != 1 ||', ''),
    ('v2 total cap', 'total > SD_MAXINF', '0'),
    ('v2 stored cap', 'e->size > SD_MAXZ', '0'),
    ('v2 stored min', 'e->size < 8 ||', ''),
    ('v2 total counted', 'if (e.z) {', 'if (0) {'),
    ('v2 trailing bytes', 'r->zs.avail_in != 0) {', '0) {'),
    ('v2 end marker', 'inflate(&r->zs, Z_NO_FLUSH) != Z_STREAM_END ||', '0 ||'),
    ('v2 crc of inflated', 'crc = sd_crc32_cont(crc, buf, n);', ''),
    ('v2 crc compared', 'crc == e->crc;', '1;'),
    ('v2 close error', 'return sd_rd_close(&r, err) == 0 && crc', 'sd_rd_close(&r, err); return crc'),
    ('v2 inflateEnd leak', 'inflateEnd(&r->zs);', ''),
    ('v2 avail_in', '(uInt)(e->size - 8)', '(uInt)(e->size)'),

    ('absolute', "e->path[0] != '/'", '0'),
]


@unittest.skipUnless(CC, 'no gcc/cc on this host: kernel-function differential test skipped')
class KernelTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.src = kernel_source()
        cls.blobs = corpus()

    @staticmethod
    def _sizeof_hits(src):
        assert src.count('/* host-test end */') == 1
        return re.search(r'sizeof\((shown|path|tmp|orig)\)', src.split('/* host-test end */')[1])

    def test_no_sizeof_of_pointer_buffers(self):
        # review 19 C1: sd_one's buffers are char * into sd_w; sizeof(tmp) etc. is 8. Review 20 f1: scan the region AFTER the end marker.
        t = full_source()
        self.assertIsNone(self._sizeof_hits(t))
        self.assertIn('sd_names(path, tmp, sizeof(sd_w.tmp), orig, sizeof(sd_w.orig))', t)
        a = 'sd_show(e, shown, '
        self.assertEqual(t.count(a), 1)
        mut = t.replace(a, 'sd_show(e, shown, sizeof(shown)), (void)(')   # mutant: sizeof(shown) inside sd_one
        self.assertIsNotNone(self._sizeof_hits(mut), 'sizeof(shown) mutant not caught')

    def test_v1_archives_are_byte_identical(self):
        # golden sha256 of the version-1 writer output taken from trunk's tools/mkdevoverlay.py before patch 0154 (2026-10-26)
        import hashlib
        ents = [('/usr/lib/libx.dylib', 0o755, 501, 20, bytes(range(256)) * 50), ('/private/var/dev/hello', 0o644, 0, 0, b'hi'),
                ('/usr/local/a/b', 0o600, 0, 0, b'')]
        b = m.pack(ents)
        self.assertEqual(len(b), 12976)
        self.assertEqual(hashlib.sha256(b).hexdigest(), '1b6c292e25647f243cd9be5dc3dcb594a4d965f3c08afccd9b55a8b583e14d8a')
        self.assertEqual(b, m.pack(ents, 1))
        self.assertEqual(b[8:12], struct.pack('<I', 1))

    def test_v2_round_trip_big_blob(self):
        R = random.Random(5)
        # 21.8 MB dylib-like: runs of random code-ish words, repeated tables and zero padding
        parts = []
        while sum(map(len, parts)) < 21_800_000:
            parts.append(bytes(R.randrange(256) for _ in range(R.randrange(200, 3000))) if R.random() < 0.5 else bytes(R.randrange(1, 40)) * R.randrange(1, 40) if R.random() < 0.5 else bytes(R.randrange(100, 4000)))
        blob_data = b''.join(parts)[:21_800_000]
        blob = m.pack([('/usr/lib/libsweetgl.dylib', 0o755, 0, 0, blob_data)], 2)
        self.assertLess(len(blob), len(blob_data))
        ents = m.parse(blob)
        self.assertEqual(ents[0][4], blob_data); self.assertTrue(ents[0][5])
        exe = os.path.join(self.tmp, 'big')
        err = build(self.src, exe)
        self.assertIsNone(err, err)
        self.assertEqual(py(blob)[0].split('|')[4:6], ['21800000', '1'])
        self.assertEqual(mismatches(exe, [blob]), 0)            # the kernel inflate (ASan) agrees: size, crc ok

    def test_v2_refusals_agree_and_have_the_expected_outcome(self):
        p = b'/usr/lib/a'
        h = lambda n: hdr(n, 2)
        exe = os.path.join(self.tmp, 'ref')
        self.assertIsNone(build(self.src, exe))
        entry_refused = [                                       # archive parses, the entry is not written (crc/stream flag false, size 0)
            h(1) + z2(p, b'abc' * 1000, decl=2999), h(1) + z2(p, b'abc' * 1000, decl=3001), h(1) + z2(p, b'abc', tail=b'\0'),
            h(1) + z2(p, b'abc' * 1000, zmut=lambda z: z[:-1]), h(1) + z2(p, b'abc', crc=1),
            h(1) + z2(p, bytes(40 << 20), decl=1000)]           # zip bomb: 40 KB stream, 40 MiB real output, 1000 declared
        whole_refused = [                                       # the archive is refused whole (nothing written)
            h(1) + z2(p, b'x', decl=(40 << 20) + 1), h(2) + z2(p, bytes(30 << 20)) + z2(b'/usr/lib/b', bytes(11 << 20)),
            h(1) + z2(p, b'x', decl=2 ** 64 - 1), h(1) + raw(len(p), 0o644, 0, 0, 0, 7, p, bytes(7)),
            h(1) + raw(len(p), 0o644, 0, 0, 0, (48 << 20) + 8, p, bytes((48 << 20) + 8)),
            h(1) + z2(p, b'abc')[:-16], h(1) + z2(p, b'abc')[:40]]
        for b in entry_refused:
            ents = m.parse(b)
            self.assertFalse(ents[0][5]); self.assertEqual(py(b)[0].split('|')[4:6], ['0', '0'])
        for b in whole_refused:
            with self.assertRaises(ValueError):
                m.parse(b)
            self.assertEqual(py(b), ['ERR', '--'])
        self.assertEqual(mismatches(exe, entry_refused + whole_refused), 0)
        self.assertTrue(m.parse(h(1) + z2(p, bytes(40 << 20)))[0][5])   # exactly 40 MiB inflated is accepted

    def test_patch_0154_is_an_applyable_diff_of_0117(self):
        self.assertIn('inflate', full_source()); self.assertIn('SD_VERSION2', full_source())

    def test_write_pass_rechecks_crc_before_rename(self):      # review devoverlay-z-1 round 2 F1 (source structure, not behaviour)
        s = full_source()
        a = s.index('sd_write_tmp(const char *tmp, const struct sd_ent *e, uint8_t *buf)\n{')
        body = s[a:s.index('\n}\n', a)]
        for k in ('crc = sd_crc32_cont(crc, buf, n);', 'vn_rdwr(UIO_WRITE', 'sd_rd_close(&r, err)',
                  'if (!err && crc != e->crc) {', 'VATTR_INIT'):
            self.assertIn(k, body, 'sd_write_tmp lost: ' + k)
        upd = body.index('crc = sd_crc32_cont(crc, buf, n);')           # running crc over the bytes handed to vn_rdwr
        self.assertLess(upd, body.index('vn_rdwr(UIO_WRITE'))     # updated over buf before the write that uses it
        chk = body.index('if (!err && crc != e->crc) {')
        self.assertIn('err = EIO;', body[chk:chk + 80])
        self.assertLess(body.index('sd_rd_close(&r, err)'), chk)
        self.assertLess(chk, body.index('VATTR_INIT'))              # EIO set before setattr; the rename is in sd_one, after this returns

    def test_markers_present(self):
        self.assertIn('sd_next', self.src); self.assertIn('sd_path_check', self.src)

    def test_matches_python_twin(self):
        exe = os.path.join(self.tmp, 'h')
        err = build(self.src, exe)
        self.assertIsNone(err, err)
        self.assertEqual(mismatches(exe, self.blobs), 0)

    def test_mutants_are_caught(self):
        alive = []
        for i, (name, a, b) in enumerate(MUTANTS):
            self.assertEqual(self.src.count(a), 1, 'mutant %r no longer matches the kernel source' % name)
            exe = os.path.join(self.tmp, 'm%d' % i)
            err = build(self.src.replace(a, b), exe)
            self.assertIsNone(err, '%s: %s' % (name, err))
            if mismatches(exe, self.blobs) == 0:
                alive.append(name)
        self.assertEqual(alive, [], 'mutants not caught: %s' % alive)


if __name__ == '__main__':
    unittest.main()
