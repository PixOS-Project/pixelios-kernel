"""Compile the kernel's pure archive/path functions (xnu patch 0117, between the host-test markers) on the host with
ASan/UBSan and compare them with the Python twin tools/mkdevoverlay.py on a seeded corpus; then check that known
mutants of the C code are caught (review 18 finding 4). Skips when gcc/cc is missing."""
import os, random, re, shutil, struct, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkdevoverlay as m

PATCH = os.path.join(HERE, '..', 'xnu', 'patches', '0117-sweet-dev-overlay.patch')
CC = shutil.which('gcc') or shutil.which('cc')

MAIN = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <errno.h>
%s
/* stdin: repeated [u32 len][len bytes]; one result block per archive, ending "--" */
int main(void) {
	uint32_t n;
	while (fread(&n, 4, 1, stdin) == 1) {
		uint8_t *b = malloc(n ? n : 1);                    /* exact size: ASan sees any read past the end */
		if (fread(b, 1, n, stdin) != n) return 2;
		uint32_t count = 0;
		if (sd_hdr(b, n, &count) || sd_walk(b, n, count)) { puts("ERR"); }
		else {
			uint64_t off = SD_HDR; struct sd_ent e;
			for (uint32_t i = 0; i < count; i++) {
				sd_next(b, n, &off, &e);
				char path[SD_MAXPATH + 1]; size_t mk = 0;
				const char *why = sd_path_check(&e, path, &mk);
				printf("E %%s|%%u|%%u|%%u|%%llu|%%d\n", why ? why : "ok", e.mode, e.uid, e.gid, (unsigned long long)e.size,
				    sd_crc32(e.data, e.size) == e.crc);
			}
		}
		puts("--"); free(b);
	}
	return 0;
}
'''


def kernel_source():
    s = open(PATCH).read()
    s = s[s.index('+++ b/bsd/dev/sweet_devoverlay.c'):]
    lines = [l[1:] for l in s.split('\n')[2:] if l.startswith('+')]
    src = '\n'.join(lines)
    a, b = src.index('/* host-test begin'), src.index('/* host-test end */')
    return src[a:b]


def build(src, out):
    code = MAIN % src
    c = out + '.c'
    open(c, 'w').write(code)
    for flags in (['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined'], []):
        r = subprocess.run([CC, '-O1', '-Wall', '-Werror=implicit-function-declaration', *flags, '-o', out, c], capture_output=True, text=True)
        if r.returncode == 0:
            return None
    return r.stderr[-2000:]


def py(blob):
    try:
        ents = m.parse(blob)
    except ValueError:
        return ['ERR', '--']
    return ['E %s|%d|%d|%d|%d|%d' % (m.path_check(p) or 'ok', mo, u, g, len(d), int(c)) for p, mo, u, g, d, c in ents] + ['--']


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
             '/System/Library/xpc/launchd.plist', '/System/Library/LaunchDaemons/org.pixelios.t', '/System/Library/LaunchDaemons/org.pixelios.d/x',
             '/usr/lib/a.sweetorig', '/usr/lib/a.SweetOrig', '/usr/lib/a.SWEETTMP', '/usr/lib/a\x00b', '/usr/lib/a\x7fb', '/usr/lib/a\x1fb', '/',
             '/usr/lib/', '/usr/lib/.', '/usr/lib/a/', '/usr/lib/\xe9', '/usr/lib/' + 'n' * 245, '/usr/lib/' + 'n' * 246, '/usr/lib/x/..', '/usr/lib/x/../y']
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
    out.append(hdr(0xffffffff)); out.append(hdr(1, 2)); out.append(b'SWEETDEV'); out.append(b'')
    good = m.pack([('/usr/lib/a', 0o644, 0, 0, b'abc')])
    out.append(good[:-1]); out.append(good[:-16]); out.append(good[:40])
    for q in paths:                                           # every path alone, with and without the final padding
        blob = m.pack([(q, 0o644, 0, 0, b'zz')])
        out.append(blob); out.append(blob[:-1])
    for _ in range(1500):
        ents = [(R.choice(paths), R.randrange(0, 0o1000), R.randrange(0, 600), 0, bytes(R.randrange(256) for _ in range(R.randrange(40))))
                for _ in range(R.randrange(1, 4))]
        blob = bytearray(m.pack(ents))
        for _ in range(R.randrange(0, 4)):
            blob[R.randrange(len(blob))] = R.randrange(256)
        if R.random() < 0.3:
            blob = blob[:R.randrange(len(blob) + 1)]
        if R.random() < 0.2:
            blob += bytes(R.randrange(40))
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
    ('version', 'rd32(b + 8) != SD_VERSION', '0'),
    ('0x7f byte', ' || c == 0x7f', ''),
    ('control byte', 'c < 0x20 ||', 'c < 0x1f ||'),
    ('NAME slash', "memchr(out + pl, '/', n - pl)", '0'),
    ('dotdot', "len == 2 && out[comp + 1] == '.' && out[comp + 2] == '.'", '0'),
    ('dot', "len == 1 && out[comp + 1] == '.'", '0'),
    ('empty component', 'if (len == 0) {', 'if (0) {'),
    ('suffix case', "if (c >= 'A' && c <= 'Z') {", 'if (0) {'),
    ('component 245', 'len > 245', 'len > 255'),
    ('crc poly', '0xedb88320u', '0xedb88321u'),
    ('absolute', "e->path[0] != '/'", '0'),
]


@unittest.skipUnless(CC, 'no gcc/cc on this host: kernel-function differential test skipped')
class KernelTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.src = kernel_source()
        cls.blobs = corpus()

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
