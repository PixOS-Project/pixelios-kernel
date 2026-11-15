"""xnu patch 0345 (sweetepoch): host test of the pure core of the patch (the block between SWEET_EPOCH_CORE_BEGIN/END of
xnu/patches/0345-sweet-epoch-bootarg.patch is extracted from the patch text and compiled with the host C compiler together with a small driver).
Covers: strict decimal parse, range 1704067200 <= v < 4102444800, wall = base + uptime, set stores secs - uptime (get after set = the set value, then it advances with uptime,
no jump when the uptime base changes only through the formula), 3 mutants of the core that the cases must kill.
The kernel wrappers (getGMTTimeOfDay/setGMTTimeOfDay, IOLog, boot-arg read) are NOT run here: only a build + a boot shows them."""
import os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0345-sweet-epoch-bootarg.patch")

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
%(core)s
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %%d: %%s\n", __LINE__, #c); bad++; } } while (0)
static int P(const char *s, long long *v) { *v = -1; return sweet_epoch_parse(s, v); }
int main(void) {
	long long v;
	CHK(P("1704067200", &v) == 0 && v == 1704067200LL);          /* lowest accepted */
	CHK(P("1704067199", &v) == 2 && v == -1);                    /* one below */
	CHK(P("4102444799", &v) == 0 && v == 4102444799LL);          /* highest accepted */
	CHK(P("4102444800", &v) == 2);                               /* one above */
	CHK(P("1794585637", &v) == 0 && v == 1794585637LL);          /* a 2026 host clock */
	CHK(P("0", &v) == 2);
	CHK(P("", &v) == 1);
	CHK(P("0x65920080", &v) == 1);                               /* hex refused: decimal only */
	CHK(P("-1704067200", &v) == 1);
	CHK(P("1704067200x", &v) == 1);
	CHK(P("17040672.5", &v) == 1);
	CHK(P(" 1704067200", &v) == 1);
	CHK(P("99999999999999999999999999", &v) == 2);               /* too long: no overflow, out of range */
	CHK(P("0001794585637", &v) == 2);                            /* 13 digits: refused (no leading-zero trick) */
	CHK(sweet_epoch_parse(NULL, &v) == 1);
	/* wall = base + uptime */
	CHK(sweet_epoch_get(1794585637LL, 0) == 1794585637LL);
	CHK(sweet_epoch_get(1794585637LL, 95) == 1794585732LL);
	/* settimeofday at uptime 120 to 1800000000: get at the same uptime = the set value, 10 s later +10 */
	long long b = sweet_epoch_set_base(1800000000LL, 120);
	CHK(sweet_epoch_get(b, 120) == 1800000000LL);
	CHK(sweet_epoch_get(b, 130) == 1800000010LL);
	/* a set to a time before the epoch (clock moved back): still consistent, never negative here */
	b = sweet_epoch_set_base(1704067200LL, 5000);
	CHK(sweet_epoch_get(b, 5000) == 1704067200LL && sweet_epoch_get(b, 5001) == 1704067201LL);
	/* calendar init derivation (clock.c:1052-1090): secs = get(base, sys), sys <= secs, so no 'UTC less than sys' clamp and utc_offset = secs - sys = base */
	CHK(sweet_epoch_get(1794585637LL, 37) - 37 == 1794585637LL);
	printf(bad ? "CORE FAIL %%d\n" : "CORE OK\n", bad);
	return bad != 0;
}
'''


def core_text(patch=PATCH):
    txt = open(patch).read()
    m = re.search(r"^\+/\* SWEET_EPOCH_CORE_BEGIN[^\n]*\n(.*?)^\+/\* SWEET_EPOCH_CORE_END \*/", txt, re.S | re.M)
    assert m, "no SWEET_EPOCH_CORE block in the patch"
    return "\n".join(l[1:] if l.startswith("+") else l for l in m.group(1).splitlines())


def run(core):
    d = tempfile.mkdtemp()
    try:
        c = os.path.join(d, "t.c"); open(c, "w").write(DRIVER % {"core": core})
        exe = os.path.join(d, "t")
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        p = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-O1", c, "-o", exe], capture_output=True, text=True)
        if p.returncode:
            return False, "compile: " + p.stderr[:600]
        q = subprocess.run([exe], capture_output=True, text=True)
        return q.returncode == 0, q.stdout
    finally:
        shutil.rmtree(d, True)


class Core(unittest.TestCase):
    def test_patch_core(self):
        ok, out = run(core_text())
        self.assertTrue(ok, out); self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        muts = [("v < SWEET_EPOCH_MIN", "v <= SWEET_EPOCH_MIN"), ("v >= SWEET_EPOCH_MAX", "v > SWEET_EPOCH_MAX"),
                ("return secs - uptime_s;", "return secs + uptime_s;"), ("return base + uptime_s;", "return base - uptime_s;"),
                ("if (n == 0 || *s != '\\0')", "if (*s != '\\0')"), ("if (++n > 12)", "if (++n > 30)")]
        for a, b in muts:
            self.assertIn(a, core, "mutant anchor moved: %s" % a)
            ok, out = run(core.replace(a, b, 1))
            self.assertFalse(ok, "mutant survived: %s -> %s" % (a, b))

    def test_wrapper_text_in_patch(self):
        t = open(PATCH).read()
        for s in ("SWEET GAP: no sweetepoch boot-arg", "SWEET TIME: calendar set from sweetepoch=%lld (host clock at boot image build; drift = boot delay)",
                  "sweetepoch=%s %s, calendar stays at 1970 + uptime", "PE_parse_boot_arg_str(\"sweetepoch\"", "SweetPlatformExpert::getGMTTimeOfDay", "SweetPlatformExpert::setGMTTimeOfDay"):
            self.assertIn(s, t)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s epoch-core tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
