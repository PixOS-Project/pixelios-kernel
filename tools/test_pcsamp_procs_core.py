"""xnu patch 0353 (sweetpcsampproc): host test of the pure name parser (block SWEET_PCSAMP_PARSE_BEGIN/END of xnu/patches/0353-sweet-pcsamp-procs.patch,
extracted from the patch text and compiled with the host C compiler), static checks of the kernel wrapper text, and tools/pcsamp-sym parsing of both header forms.
The kernel wrapper (PE_parse_boot_arg_str, printf, strlcpy, proc_iterate) is NOT run here: only a build + a boot shows it."""
import importlib.machinery, importlib.util, os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0353-sweet-pcsamp-procs.patch")

DRIVER = r'''
#include <stdio.h>
#include <string.h>
#define MAXCOMLEN 16
#define PCS_NM (MAXCOMLEN + 1)
%(core)s
static int bad;
#define CHK(c) do { if (!(c)) { printf("FAIL line %%d: %%s\n", __LINE__, #c); bad++; } } while (0)
static char o[3][PCS_NM];
static unsigned g;
static int P(const char *s) { return sweet_pcsamp_parse_procs(s, o, 3, &g); }
int main(void) {
	CHK(P("coreduetd") == 1 && !strcmp(o[0], "coreduetd") && o[1][0] == 0 && o[2][0] == 0 && g == 0);
	CHK(P("coreduetd,MobileTimer,Preferences") == 3 && !strcmp(o[1], "MobileTimer") && !strcmp(o[2], "Preferences") && g == 0);
	CHK(P("a,b,c,d") == 3 && !strcmp(o[2], "c") && g == PCS_G_MANY);
	CHK(P("a,,b") == 2 && !strcmp(o[1], "b") && g == PCS_G_EMPTY);          /* empty entry skipped, the next valid name fills the slot */
	CHK(P("") == 0 && g == PCS_G_EMPTY && o[0][0] == 0);
	CHK(P(",") == 0 && g == PCS_G_EMPTY);
	CHK(P("a,") == 1 && g == PCS_G_EMPTY);
	CHK(P("0123456789abcdef") == 1 && !strcmp(o[0], "0123456789abcdef") && g == 0);   /* 16 = MAXCOMLEN accepted */
	CHK(P("0123456789abcdefg") == 0 && g == PCS_G_LONG);                      /* 17 refused */
	CHK(P("0123456789abcdefg,ok") == 1 && !strcmp(o[0], "ok") && g == PCS_G_LONG);
	CHK(P("a,b,c,,x") == 3 && g == (PCS_G_MANY | PCS_G_EMPTY));
	CHK(P("a,b,c,0123456789abcdefg") == 3 && g == (PCS_G_MANY | PCS_G_LONG));
	/* stale output of a previous call is cleared */
	P("x,y,z"); CHK(P("q") == 1 && o[1][0] == 0 && o[2][0] == 0);
	printf(bad ? "CORE FAIL %%d\n" : "CORE OK\n", bad);
	return bad != 0;
}
'''


def core_text(patch=PATCH):
    txt = open(patch).read()
    m = re.search(r"^\+/\* SWEET_PCSAMP_PARSE_BEGIN[^\n]*\n(.*?)^\+/\* SWEET_PCSAMP_PARSE_END \*/", txt, re.S | re.M)
    assert m, "no SWEET_PCSAMP_PARSE block in the patch"
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


def load_sym():
    path = os.path.join(HERE, "pcsamp-sym")
    ld = importlib.machinery.SourceFileLoader("pcsamp_sym", path)
    spec = importlib.util.spec_from_loader("pcsamp_sym", ld)
    mod = importlib.util.module_from_spec(spec)
    ld.exec_module(mod)
    return mod


OLD = """SWEET PCSAMP: pid bb 100 slide 0x1000
SWEET PCSAMP: t 5000000000 rounds 10 reached 40 hits 7 bb 5 sb 2 pf 0 entries 3 dropped 0
SWEET PCSAMP: pc bb 0xabc k 1 pc 0x2000 lr 0x2100 kpc 0x0 n 5
SWEET PCSAMP: tid bb 0xabc n 5 user 5
|backboardd| SWEETGL: BASE pid 100 image 0x7000 slide 0x0 size 4096
"""
NEW = """SWEET PCSAMP: slots p0=coreduetd p1=MobileTimer p2=-
SWEET PCSAMP: pid p0 200 slide 0x1000
SWEET PCSAMP: t 5000000000 rounds 10 reached 40 hits 7 p0 5 p1 2 p2 0 entries 3 dropped 0
SWEET PCSAMP: pc p0 0xabc k 2 pc 0x2000 lr 0x2100 kpc 0x99 n 5
SWEET PCSAMP: tid p0 0xabc n 5 user 0
|coreduetd| SWEETGL: BASE pid 200 image 0x7000 slide 0x0 size 4096
"""


class Core(unittest.TestCase):
    def test_parse_core(self):
        ok, out = run(core_text())
        self.assertTrue(ok, out); self.assertIn("CORE OK", out)

    def test_mutants_killed(self):
        core = core_text()
        muts = [("len > PCS_NM - 1", "len > PCS_NM"), ("*gaps |= PCS_G_EMPTY;", "*gaps |= 0;"), ("if (entries > np)", "if (entries > np + 1)"),
                ("} else if (taken < np) {", "} else if (taken <= np) {"), ("out[i][0] = '\\0';", ";"), ("*gaps |= PCS_G_LONG;", "*gaps |= 0;")]
        for a, b in muts:
            self.assertIn(a, core, "mutant anchor moved: %s" % a)
            ok, out = run(core.replace(a, b, 1))
            self.assertFalse(ok, "mutant survived: %s -> %s" % (a, b))

    def test_wrapper_text_in_patch(self):
        t = open(PATCH).read()
        for s in ("PE_parse_boot_arg_str(\"sweetpcsampproc\"", "SWEET PCSAMP: slots p0=%s p1=%s p2=%s", "SWEET GAP: sweetpcsampproc '%s'",
                  "pcs_names[w][0] != '\\0'", "pcs_short[0], perproc[0], pcs_short[1], perproc[1], pcs_short[2], perproc[2]",
                  'static const char *pcs_short[PCS_NP] = { "bb", "sb", "pf" };'):
            self.assertIn(s, t)

    def test_sym_both_header_forms(self):
        sym = load_sym()
        secs, slides, bases, acts = sym.parse(OLD.splitlines())
        self.assertEqual(len(secs), 1); self.assertEqual(secs[0]["rows"][0][0], "bb")
        self.assertEqual(slides, {"bb": 0x1000}); self.assertEqual(bases, {"bb": (0x7000, 4096)})
        secs, slides, bases, acts = sym.parse(NEW.encode().splitlines())
        self.assertEqual(len(secs), 1); self.assertEqual(secs[0]["rows"][0][0], "p0"); self.assertEqual(secs[0]["tids"][0][0], "p0")
        self.assertEqual(slides, {"p0": 0x1000})
        self.assertEqual(bases, {"p0": (0x7000, 4096)})   # SWEETGL BASE names the process; mapped through the slots line


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s pcsamp-procs-core tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
