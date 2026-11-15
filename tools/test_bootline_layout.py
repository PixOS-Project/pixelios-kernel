"""BOOT_LINE_LENGTH 2048 (xnu patch 0320 on 0176's 1024, research 108): producer (shim/payload.h, tools/mkpayload.py, tools/run-preflight)
and consumer (pexpert/pexpert/arm64/boot.h via the patches) must use one number, the payload must put the cmdline at 0x48 and the seg
table at 0x848 with version 3, and the shim's static layout asserts must compile on the host (and fail on a wrong length)."""
import importlib.machinery, importlib.util, os, re, shutil, struct, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from tools import adt, mkadt, mkpayload  # noqa: E402

LOADER = os.path.join(ROOT, 'shim', 'payload.h')	# the layout + its asserts (shim/loader.c and payload.c include it)
PATCH = os.path.join(ROOT, 'xnu', 'patches', '0176-sweet-boot-line-1024.patch')
PATCH320 = os.path.join(ROOT, 'xnu', 'patches', '0320-sweet-boot-line-2048.patch')
PREFLIGHT = os.path.join(HERE, 'run-preflight')
CC = shutil.which('gcc') or shutil.which('cc')
N = 2048


def read(path):
    with open(path) as f:
        return f.read()


def write(path, data):
    with open(path, 'wb' if isinstance(data, bytes) else 'w') as f:
        f.write(data)


def one(pattern, text, what):
    m = re.findall(pattern, text, re.M)
    if len(m) != 1:
        raise AssertionError('%s: %d matches for %r' % (what, len(m), pattern))
    return int(m[0], 0)


def fake_kernel():
    """Smallest Mach-O mkpayload.build accepts: one __TEXT segment at file offset 0 and an LC_UNIXTHREAD pc, no fixups."""
    va, size, pc = 0xfffffe0007004000, 0x4000, 0xfffffe0007004800
    seg = struct.pack('<II16s4Q2I2I', 0x19, 72, b'__TEXT', va, size, 0, size, 5, 5, 0, 0)
    # arm_thread_state64 (flavor 6, count 68 words): x0..x28, fp, lr, sp = 32 u64, then pc, then cpsr + pad
    thread = struct.pack('<4I', 0x5, 16 + 68 * 4, 6, 68) + struct.pack('<34Q', *([0] * 32), pc, 0)
    hdr = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 2, 2, 2, len(seg) + len(thread), 0, 0)
    buf = bytearray(size)
    buf[:len(hdr) + len(seg) + len(thread)] = hdr + seg + thread
    buf[-64:-64 + len(mkpayload.KERNEL_TAG)] = mkpayload.KERNEL_TAG   # what a patch-0176 kernel carries
    return bytes(buf), pc


class SameNumber(unittest.TestCase):
    def test_all_producers_and_the_patches_say_2048(self):
        src = read(LOADER)
        self.assertEqual(one(r'^#define BOOT_LINE_LENGTH\s+(\d+)\s*$', src, 'payload.h'), N)
        self.assertEqual(one(r'^#define PAYLOAD_VERSION\s+(\d+)\b', src, 'payload.h'), 3)
        self.assertEqual(mkpayload.BOOT_LINE_LENGTH, N)
        self.assertEqual(mkpayload.PAYLOAD_VERSION, 3)
        self.assertEqual(mkpayload.SEG_TABLE, 0x848)
        p = read(PATCH)
        self.assertEqual(one(r'^\+#define BOOT_LINE_LENGTH\s+(\d+)\s*$', p, 'patch 0176 +'), 1024)
        self.assertEqual(one(r'^-#define BOOT_LINE_LENGTH\s+(\d+)\s*$', p, 'patch 0176 -'), 608)   # Apple's value it replaces
        p = read(PATCH320)   # applied after 0176: its - line is 0176's value, its + line the one every producer uses
        self.assertEqual(one(r'^-#define BOOT_LINE_LENGTH\s+(\d+)\s*$', p, 'patch 0320 -'), 1024)
        self.assertEqual(one(r'^\+#define BOOT_LINE_LENGTH\s+(\d+)\s*$', p, 'patch 0320 +'), N)
        self.assertIn('+++ b/pexpert/pexpert/arm64/boot.h', p)
        added = [l for l in p.split('\n') if l.startswith('+') and not l.startswith('+++')]
        self.assertEqual([l for l in added if '*/' in l and not l.rstrip().endswith('*/')], [])   # no early comment close (text after a */)
        self.assertIn('+++ b/pexpert/pexpert/arm64/boot.h', p)
        loader = importlib.machinery.SourceFileLoader('rp', PREFLIGHT)
        spec = importlib.util.spec_from_loader('rp', loader)
        rp = importlib.util.module_from_spec(spec); loader.exec_module(rp)
        self.assertEqual(rp.BOOT_LINE_LENGTH, N)


class PayloadLayout(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        kernel, self.pc = fake_kernel()
        self.k = os.path.join(self.d, 'kernel'); write(self.k, kernel)
        self.a = os.path.join(self.d, 'adt')
        write(self.a, adt.serialize(mkadt.build(seed=b'\0' * 64, board='bluejay')))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_2000_byte_cmdline_at_0x48_segs_at_0x848_version_3(self):
        cl = ('x=' + 'y' * 1998)
        self.assertEqual(len(cl), 2000)
        blob, info = mkpayload.build(self.k, self.a, cl, board='bluejay')
        magic, version, nsegs = struct.unpack_from('<8sII', blob, 0)
        self.assertEqual((magic, version, nsegs), (b'SWEETXNU', 3, 1))
        self.assertEqual(struct.unpack_from('<Q', blob, 0x18)[0], self.pc)       # entry_va: the header before cmdline is intact
        self.assertEqual(blob[0x48:0x48 + 2000], cl.encode())
        self.assertEqual(blob[0x48 + 2000:0x848], b'\0' * (N - 2000))           # NUL-terminated, rest of the field zero
        vmaddr, vmsize, file_off, file_size = struct.unpack_from('<4Q', blob, 0x848)
        self.assertEqual((vmaddr, vmsize, file_size), (0xfffffe0007004000, 0x4000, 0x4000))
        self.assertEqual(file_off, 0x848 + 32)                                   # data right behind the one-entry seg table
        self.assertEqual(blob[file_off:file_off + 4], struct.pack('<I', 0xfeedfacf))

    def test_2047_accepted_2048_refused(self):
        self.assertEqual(len(mkpayload.check_cmdline('a' * 2047)), 2047)
        with self.assertRaises(SystemExit) as e:
            mkpayload.check_cmdline('a' * 2048)
        self.assertIn('the limit is 2047', str(e.exception.code))
        blob, _ = mkpayload.build(self.k, self.a, 'a' * 2047, board='bluejay')
        self.assertEqual(blob[0x48 + 2046:0x48 + 2048], b'a\0')                 # last field byte stays the NUL
        with self.assertRaises(SystemExit):
            mkpayload.build(self.k, self.a, 'a' * 2048, board='bluejay')

    def test_kernel_with_the_1024_tag_refused(self):   # a 0176-only (1024) kernel on the 2048 shim (patch 0320)
        k, _ = fake_kernel()
        old = os.path.join(os.path.dirname(self.k), 'k1024.kernel'); write(old, k.replace(mkpayload.KERNEL_TAG, b'SWEET_BOOT_LINE_LENGTH=1024\0'))
        with self.assertRaises(SystemExit):
            mkpayload.build(old, self.a, 'x', board='bluejay')

    def test_kernel_without_0176_tag_refused(self):   # review bootline-1024-r1 F1: a 608 kernel on the 1024 shim
        k, _ = fake_kernel()
        old = os.path.join(os.path.dirname(self.k), 'old.kernel'); write(old, k.replace(mkpayload.KERNEL_TAG, b'\0' * len(mkpayload.KERNEL_TAG)))
        with self.assertRaises(SystemExit) as e:
            mkpayload.build(old, self.a, 'x', board='bluejay')
        self.assertIn('(needs xnu patches 0176 + 0320)', str(e.exception.code))


@unittest.skipUnless(CC, 'no host C compiler')
class ShimAsserts(unittest.TestCase):
    """payload.h's _Static_asserts (payload segs 0x848, boot_args 2176, CommandLine 108, <= 16 KB) on the host compiler
    (LP64 like aarch64: same alignment of u16/u32/u64). -fsyntax-only: the arm64 inline asm is never assembled."""

    def compile(self, src):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, 'payload.h'); write(f, src)
            return subprocess.run([CC, '-fsyntax-only', '-x', 'c', f], capture_output=True, text=True)
        finally:
            shutil.rmtree(d)

    def test_asserts_hold(self):
        r = self.compile(read(LOADER))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_old_length_fails_the_asserts(self):   # mutant: shim left at 1024 (0176) while XNU expects 2048 (0320)
        src = read(LOADER).replace('#define BOOT_LINE_LENGTH\t2048', '#define BOOT_LINE_LENGTH\t1024')
        self.assertIn('#define BOOT_LINE_LENGTH\t1024', src)
        r = self.compile(src)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('payload header layout', r.stderr)
        self.assertIn('boot_args layout', r.stderr)



PATCH177 = os.path.join(ROOT, 'xnu', 'patches', '0177-sweet-bootargs-sysctl-trunc.patch')
# Host model of the XNU calls the 0177 function uses (own code, semantics read from bsd/kern/kern_newsysctl.c:493-517
# sysctl_io_string and :1525-1545 sysctl_old_user: ENOMEM before copying and before oldidx advances; a probe only advances oldidx).
MODEL177 = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define BOOT_LINE_LENGTH 2048
#define ENOMEM 12
#define STATIC static
#define __unused
#define relaxed 0
#define os_atomic_inc(p, m) (++*(p))
typedef uint64_t user_addr_t;
struct proc { char p_comm[17]; int pid; };
struct sysctl_req { struct proc *p; user_addr_t oldptr; size_t oldlen, oldidx; user_addr_t newptr; };
struct sysctl_oid;
static char line[BOOT_LINE_LENGTH], out[BOOT_LINE_LENGTH];
static const char *PE_boot_args(void) { return line; }
static int proc_getpid(struct proc *p) { return p->pid; }
static int SYSCTL_OUT(struct sysctl_req *r, const void *v, size_t l)
{
	if (r->oldptr) {   /* kern_newsysctl.c:1530-1540 sysctl_old_user: ENOMEM before any copy and before oldidx moves */
		if (r->oldlen - r->oldidx < l) return ENOMEM;
		memcpy(out + r->oldidx, v, l);
	}
	r->oldidx += l;
	return 0;
}
static int sysctl_io_string(struct sysctl_req *req, char *pValue, size_t valueSize, int trunc, int *changed)
{
	size_t len = strlen(pValue) + 1;
	int error;
	(void)valueSize; (void)changed;
	if (trunc && req->oldptr && req->oldlen && (req->oldlen < len)) {
		error = SYSCTL_OUT(req, pValue, req->oldlen - 1);
		if (!error) { char c = 0; error = SYSCTL_OUT(req, &c, 1); }
	} else {
		error = SYSCTL_OUT(req, pValue, len);
	}
	return error;
}
%s
int main(int argc, char **argv)
{
	struct proc wd = { "watchdogd", 50 };
	struct sysctl_req r = { &wd, argc > 3, (size_t)atoi(argv[2]), 0, 0 };
	memset(line, 0, sizeof line); memset(line, 'a', (size_t)atoi(argv[1])); if (atoi(argv[1]) >= 8) memcpy(line, "launchd_", 8);   /* filler token must be a shown name (0177 r4 allowlist) */
	if (getenv("LINE")) strlcpy(line, getenv("LINE"), sizeof line);   /* a real token line instead of the 'a' filler */
	for (int k = 0; k < (argc > 4 ? atoi(argv[4]) : 1); k++) {
		r.oldidx = 0; memset(out, 0, sizeof out);
		int e = sysctl_sysctl_bootargs(NULL, NULL, 0, &r);
		printf("rc=%%d outlen=%%zu oldidx=%%zu\n", e, strlen(out), r.oldidx);
		if (getenv("LINE") && k == 0) printf("out=[%%s]\n", out);
	}
	if (argc > 5) {   /* one more request with another buffer size, same process (same counters) */
		r.oldidx = 0; r.oldlen = (size_t)atoi(argv[5]); memset(out, 0, sizeof out);
		int e = sysctl_sysctl_bootargs(NULL, NULL, 0, &r);
		printf("rc=%%d outlen=%%zu oldidx=%%zu\n", e, strlen(out), r.oldidx);
	}
	return 0;
}
"""


def patched_function_177():
    """The sysctl_sysctl_bootargs body as 0177 leaves it: its hunk's context and + lines from the opening brace on."""
    hunk = read(PATCH177).split('\n@@', 1)[1].split('\n', 1)[1]
    body = [l[1:] for l in hunk.split('\n') if l[:1] in (' ', '+')]
    body = body[body.index('{'):]   # the hunk may start before the brace (git diff context)
    end = body.index('}') + 1
    return ('STATIC int\nsysctl_sysctl_bootargs\n(__unused struct sysctl_oid *oidp, __unused void *arg1, __unused int arg2, '
            'struct sysctl_req *req)\n' + '\n'.join(body[:end]))


@unittest.skipUnless(CC, 'no host C compiler')
class BootargsSysctl177(unittest.TestCase):
    """Patch 0177 rework (bootargs-trunc-long FAIL 2026-10-28, research bootargs-callers-0177): a buffer >= 608 (stock
    BOOT_LINE_LENGTH; watchdogd) gets the line cut + rc 0; a smaller buffer (launchd/launchd_cache_loader: 128) keeps stock
    ENOMEM so their probe-and-retry path runs; both print a GAP line (first 32, then 100, 1000, ...); a size probe, a big
    buffer or a short line are unchanged and silent."""

    @classmethod
    def build(cls, name, func):
        src = os.path.join(cls.d, name + '.c'); exe = os.path.join(cls.d, name)
        write(src, '#include <stdlib.h>\n' + MODEL177 % func)
        r = subprocess.run([CC, '-Wall', '-Werror', '-o', exe, src], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return exe

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.exe = cls.build('t', patched_function_177())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def run_it(self, linelen, oldlen, probe=False, times=1, then=None, line=None, exe=None):
        args = [exe or self.exe, str(linelen), str(oldlen)] + ([] if probe else ['buf', str(times)]) + ([str(then)] if then else [])
        env = dict(os.environ, **({'LINE': line} if line is not None else {}))
        return subprocess.run(args, capture_output=True, text=True, check=True, env=env).stdout

    def test_watchdogd_608_buffer_long_line(self):
        o = self.run_it(926, 608)
        self.assertIn('SWEET GAP: kern.bootargs truncated to 608-byte buffer, line 926 chars, error 0 x1 (watchdogd pid 50)', o)
        self.assertIn('rc=0 outlen=607 oldidx=608', o)

    def test_launchd_128_buffer_keeps_enomem(self):   # launchd 0x10003dea0: retries with a size probe only on ENOMEM
        o = self.run_it(926, 128)
        self.assertIn('SWEET GAP: kern.bootargs ENOMEM to 128-byte buffer, line 926 chars, error 12 x1 (watchdogd pid 50)', o)
        self.assertIn('rc=12 outlen=0 oldidx=0', o)

    def test_launchd_size_probe_is_silent(self):   # r2 F1: probe = oldptr NULL with launchd's oldlen 128
        o = self.run_it(926, 128, probe=True)
        self.assertIn('rc=0 outlen=0 oldidx=927', o)
        self.assertNotIn('SWEET GAP', o)

    def test_buffer_one_short_is_cut_and_logged(self):   # r2 F1: oldlen == line length (no room for the NUL)
        o = self.run_it(926, 926)
        self.assertIn('SWEET GAP: kern.bootargs truncated to 926-byte buffer, line 926 chars, error 0 x1', o)
        self.assertIn('rc=0 outlen=925 oldidx=926', o)

    def test_kinds_counted_separately(self):   # r2 F2: ENOMEM requests do not use up the cut lines
        o = self.run_it(926, 128, times=40, then=608)
        self.assertIn('truncated to 608-byte buffer, line 926 chars, error 0 x1 ', o)

    def test_boundary_607_enomem_608_cut(self):   # mutant guard: > instead of >=, or a different threshold
        self.assertIn('rc=12 outlen=0 oldidx=0', self.run_it(926, 607))
        self.assertIn('rc=0 outlen=607 oldidx=608', self.run_it(926, 608))

    def test_unchanged_cases_are_silent(self):
        for linelen, oldlen, probe, want in ((607, 608, False, 'rc=0 outlen=607 oldidx=608'),   # stock limit, fits
                                             (100, 128, False, 'rc=0 outlen=100 oldidx=101'),   # short line, launchd buffer
                                             (926, 1024, False, 'rc=0 outlen=926 oldidx=927'),  # big buffer: whole line
                                             (926, 0, True, 'rc=0 outlen=0 oldidx=927')):      # size probe
            o = self.run_it(linelen, oldlen, probe)
            self.assertIn(want, o)
            self.assertNotIn('SWEET GAP', o)

    def test_gap_line_thresholds(self):
        o = self.run_it(926, 608, times=10000)
        self.assertEqual(re.findall(r' x(\d+) ', o), [str(i) for i in range(1, 33)] + ['100', '1000', '10000'])


    # r3 view (research bootargs-userland-tokens section 6): kernel-only tokens hidden from userland by exact name.
    D4A = ('debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1 wdt_test=no_user_checkin sweetusb=2 '
           'rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 ufsrw=1 rootrw=1 sweetwlan=2 '
           'sweetwlan=12 sweetlogts=1 sweetusb3=1 sweetremote=1')   # wlan-runG2-d4a xrun.log boot-args
    VIEW = ('debug=0xc serial=3 cpus=8 wdt=-1 wdt_test=no_user_checkin rd=ufsroot0 ignition_level=1 '
            'launchd_unsecure_cache=1')

    def view_of(self, line, **kw):
        o = self.run_it(0, kw.pop('oldlen', 1024), line=line, **kw)
        m = re.search(r'^out=\[(.*)\]$', o, re.M)
        return o, m.group(1)

    def check_view_d4a(self, exe=None):
        """Assertions that every mutant below must break; returns nothing, raises AssertionError."""
        self.assertEqual(len(self.D4A), 275)
        self.assertEqual(len(self.VIEW), 111)
        o, view = self.view_of(self.D4A, exe=exe)
        self.assertEqual(view, self.VIEW)
        self.assertIn('SWEET BOOTARGS: kern.bootargs view hides 12 tokens (line 275 chars, view 111 chars)', o)
        o = self.run_it(0, 128, line=self.D4A, exe=exe)   # launchd's 128 B buffer
        self.assertIn('rc=0 outlen=111 oldidx=112', o)
        self.assertNotIn('SWEET GAP', o)
        o, view = self.view_of('sweetusb3=1 sweetusb=2 sweetfoo=1 sweetwlan2=3 keepsyms=1 keepsyms -noprogress x=1 debug=1', exe=exe)
        self.assertEqual(view, 'debug=1')   # allowlist: only debug is shown, every sweet*/x token is hidden
        self.assertIn('hides 8 tokens', o)
        self.assertEqual(o.count('SWEET BOOTARGS'), 1)
        o = self.run_it(0, 128, times=3, line=self.D4A, exe=exe)
        self.assertEqual(o.count('SWEET BOOTARGS'), 1)   # once per boot, not per request

    def test_d4a_view_fits_128(self):
        self.check_view_d4a()

    def test_view_counts_and_spacing(self):
        o, view = self.view_of('  keepsyms=1   rd=1\t-noprogress  cpus  sweetusb3=1 ')
        self.assertEqual(view, 'rd=1 cpus')   # single spaces, no leading/trailing space, order kept
        self.assertIn('hides 3 tokens (line 51 chars, view 9 chars): keepsyms -noprogress sweetusb3', o)
        o, view = self.view_of('keepsyms keepsyms=1 rd')
        self.assertEqual(view, 'rd')
        self.assertIn('hides 2 tokens', o)

    def test_no_hidden_token_still_announced(self):   # rule 8: the hide line prints even when nothing is hidden
        o, view = self.view_of('rd=1 cpus=2')
        self.assertEqual(view, 'rd=1 cpus=2')
        self.assertIn('hides 0 tokens (line 11 chars, view 11 chars)', o)

    def test_view_still_longer_than_buffer_keeps_r2(self):
        line = 'launchd_' + 'x' * 122 + ' keepsyms=1'   # view 130 chars, 128 B buffer -> ENOMEM + GAP on the VIEW length
        o = self.run_it(0, 128, line=line)
        self.assertIn('SWEET GAP: kern.bootargs ENOMEM to 128-byte buffer, line 130 chars, error 12 x1', o)
        self.assertIn('rc=12 outlen=0 oldidx=0', o)
        o = self.run_it(0, 608, line='launchd_' + 'y' * 692 + ' ufsrw=1')   # 608 B buffer: cut, rc 0
        self.assertIn('truncated to 608-byte buffer, line 700 chars, error 0 x1', o)
        self.assertIn('rc=0 outlen=607 oldidx=608', o)

    # r4 allowlist cases
    LINE280 = ('debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1 wdt_test=no_user_checkin sweetusb=2 '
               'rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 ufsrw=1 rootrw=1 '
               'sweetrestart=1500,bootloader sweetusb3=1 sweetcntvct=1 sweetremote=1')
    LINEEL2 = ('debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1 wdt_test=no_user_checkin sweetusb=2 '
               'rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 ufsrw=1 rootrw=1 '
               'sweetrestart=1650,bootloader sweetusb3=1 sweetel2=2 sweetremote=1')

    def check_allowlist(self, exe=None):
        self.assertEqual(len(self.LINE280), 280)
        want = 'debug=0xc serial=3 cpus=8 wdt=-1 wdt_test=no_user_checkin rd=ufsroot0 ignition_level=1 launchd_unsecure_cache=1'
        o, view = self.view_of(self.LINE280, exe=exe)
        self.assertEqual(view, want)
        self.assertEqual(len(view), 111)
        self.assertIn('hides 11 tokens (line 280 chars, view 111 chars)', o)
        o = self.run_it(0, 128, line=self.LINE280, exe=exe)
        self.assertIn('rc=0 outlen=111 oldidx=112', o)
        self.assertNotIn('SWEET GAP', o)
        o, view = self.view_of(self.LINEEL2, exe=exe)
        self.assertLess(len(view), 128)
        self.assertIn('debug=0xc serial=3 cpus=1 wdt=-1', view)
        self.assertEqual(len(self.run_it(0, 128, line=self.LINEEL2, exe=exe).split('GAP')) , 1)
        o, view = self.view_of('launchd_foo=1 -wdt_no_pmu cc.x=1 ramrod_exec-y wdtx=1 debugx=1 serial3=1 -restorex '
                               '-restore libtrace_kic=1 wdt_foo cpus debug serial rd=x launchd_ ', exe=exe)
        self.assertEqual(view, 'launchd_foo=1 -wdt_no_pmu cc.x=1 ramrod_exec-y -restore libtrace_kic=1 wdt_foo cpus debug serial rd=x launchd_')
        self.assertIn('hides 4 tokens', o)
        self.assertIn('): wdtx debugx serial3 -restorex\n', o)
        o, view = self.view_of('launchd_a ' + ' '.join('h%02d' % i for i in range(60)) + ' launchd_b', exe=exe)
        self.assertEqual(view, 'launchd_a launchd_b')
        self.assertIn('hides 60 tokens', o)
        m = re.search(r'\): (.*)$', o, re.M)
        self.assertTrue(m.group(1).endswith(' ...'), m.group(1))
        self.assertLess(len(m.group(1)), 160)
        o, view = self.view_of('h' * 300 + ' ' + 'k' * 10, exe=exe)   # one huge hidden name must not overflow either
        self.assertEqual(view, '')
        self.assertIn('hides 2 tokens', o)
        self.assertTrue(re.search(r'\): \.\.\.$', o, re.M) or re.search(r'\): \s?\.\.\.$', o, re.M), o)

    def test_allowlist(self):
        self.check_allowlist()

    def test_mutants_are_killed(self):
        f = patched_function_177()
        ex = 'strlen(sweet_shown_exact[i]) == nlen && strncmp(s, sweet_shown_exact[i], nlen) == 0'
        pf = 'nlen >= strlen(sweet_shown_prefix[i]) && strncmp(s, sweet_shown_prefix[i], strlen(sweet_shown_prefix[i])) == 0'
        mutants = {
            'prefix where exact required': (ex, 'strncmp(s, sweet_shown_exact[i], strlen(sweet_shown_exact[i])) == 0'),
            'exact where prefix required': (pf, 'strlen(sweet_shown_prefix[i]) == nlen && strncmp(s, sweet_shown_prefix[i], nlen) == 0'),
            'allowlist inverted': ('int hide = 1;', 'int hide = 0;'),
            'hidden buffer bound removed': ('hl + 1 + nlen + 4 < sizeof(hidden)', '1'),
            'filter skipped': ('if (hide) {', 'if (hide && 0) {'),
            'hide line every call': ('if (os_atomic_inc(&sweet_bootargs_views, relaxed) == 1)', 'if (os_atomic_inc(&sweet_bootargs_views, relaxed) > 0)'),
            'gap uses raw length': ('(unsigned long)len, error, n,', '(unsigned long)rawlen, error, n,'),
        }
        for name, (a, b) in mutants.items():
            with self.subTest(mutant=name):
                self.assertEqual(f.count(a), 1, 'mutation site not found: ' + name)
                exe = self.build('m_' + name.replace(' ', '_'), f.replace(a, b))
                with self.assertRaises((AssertionError, subprocess.CalledProcessError)):
                    self.check_view_d4a(exe)
                    self.check_allowlist(exe)
                    if name == 'gap uses raw length':   # killed by the view-longer-than-buffer case instead
                        o = self.run_it(0, 128, line='launchd_' + 'x' * 122 + ' keepsyms=1', exe=exe)
                        self.assertIn('line 130 chars', o)

    def test_patch_threshold_is_stock_608(self):
        f = patched_function_177()
        self.assertIn('#define SWEET_STOCK_BOOT_LINE_LENGTH 608', f)
        self.assertIn('trunc = req->oldlen >= SWEET_STOCK_BOOT_LINE_LENGTH;', f)
        self.assertIn('sysctl_io_string(req, buf, BOOT_LINE_LENGTH, trunc, NULL)', f)

if __name__ == '__main__':
    unittest.main()
