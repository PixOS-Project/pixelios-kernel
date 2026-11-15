#!/usr/bin/env python3
"""Host test for xnu patch 0203 sweet-gpu-cmu-pll-print (T-4aa8; read-only CMU_G3D PLL_CON print). No phone, no build.

    tools/test_0203_gpu_pll_print.py [--patch FILE]

Compiles the header text carried by the patch (bsd/dev/sweet_gpu_pll.h) with the host cc and checks: (a) decode(0xA0620402) =
en 1 lock 1 mdiv 98 pdiv 4 sdiv 2 khz 150528 (the one observed word, docs/research/27); (b) pdiv 0 prints khz=0 AND a counted
`GPU GAP:` line (rule 8), never divides by zero; (c) the full print line has all five CON words; (d) patch text: the print sits
after the mux gate (`mux not selected` return) and before the Mali block map, reads the 0x140/0x180 words only, writes nothing to
cmu[], removes no line. Then mutants of the patch text, each of which must make a check FAIL. Exit 0 PASS, 1 FAIL, 2 ERROR.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "".join(l[2:] for l in open(__file__).readlines()[1:60] if l.startswith("# "))).strip()); raise SystemExit(0)
import os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE.parent / "xnu" / "patches" / "0203-sweet-gpu-cmu-pll-print.patch"
HDR = "bsd/dev/sweet_gpu_pll.h"
SRC = "bsd/dev/sweet_gpu_gs101.c"

HARNESS = r'''
#include <stdio.h>
#include <stdarg.h>
#include "sweet_gpu_pll.h"
static int cap(const char *f, ...) { va_list a; va_start(a, f); int n = vprintf(f, a); va_end(a); return n; }
int main(void) {
	struct sg_pll p;
	sg_pll_decode(0xA0620402u, &p);
	printf("D en=%u lock=%u mdiv=%u pdiv=%u sdiv=%u khz=%u gap=%d\n", p.en, p.lock, p.mdiv, p.pdiv, p.sdiv, p.khz, p.gap);
	uint32_t ok[5] = {0x10, 0x10000001, 0x8000000F, 0xA0620402u, 0xF340};
	sg_pll_print("g3d", ok, cap);
	uint32_t z[5] = {0, 0, 0, 0xA0620002u, 0};   /* pdiv 0 */
	sg_pll_print("g3dl2", z, cap);
	sg_pll_decode(0xA1F40402u, &p);              /* mdiv 500 (> 8 bits): 24576 x 500 / 16 = 768000 */
	printf("E mdiv=%u khz=%u\n", p.mdiv, p.khz);
	return 0;
}
'''


def per_file(text):
    out, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"diff --git a/(\S+) b/\S+", ln)
        if m:
            cur = m.group(1); out[cur] = []
        elif cur is not None:
            out[cur].append(ln)
    return out


def added(lines):
    return "\n".join(l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")) + "\n"


def run_checks(text):
    """Return a list of failure strings (empty = PASS)."""
    files = per_file(text)
    if HDR not in files or SRC not in files:
        return ["patch lacks %s or %s" % (HDR, SRC)]
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        return ["no C compiler (cc/gcc/clang) found"]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "sweet_gpu_pll.h").write_text(added(files[HDR]))
        (d / "t.c").write_text(HARNESS)
        r = subprocess.run([cc, "-Wall", "-o", str(d / "t"), str(d / "t.c")], capture_output=True, text=True)
        if r.returncode:
            return ["compile: " + r.stderr.strip()[-600:]]
        r = subprocess.run([str(d / "t")], capture_output=True, text=True, timeout=10)
        if r.returncode:
            return ["harness rc=%d (crash / div by zero?)" % r.returncode]
        out = r.stdout.splitlines()
    fails = []
    want = "D en=1 lock=1 mdiv=98 pdiv=4 sdiv=2 khz=150528 gap=0"
    if want not in out:
        fails.append("decode(0xA0620402): want %r, got %r" % (want, out[:1]))
    if "E mdiv=500 khz=768000" not in out:
        fails.append("decode(0xA1F40402): want mdiv 500 khz 768000")
    line = "SWEET GPU: PLL g3d con0=0x10 con1=0x10000001 con2=0x8000000f con3=0xa0620402 con4=0xf340 en=1 lock=1 mdiv=98 pdiv=4 sdiv=2 khz=150528"
    if line not in out:
        fails.append("print line missing: " + line)
    if not any(l.startswith("SWEET GPU: PLL g3dl2 ") and l.endswith("pdiv=0 sdiv=2 khz=0") for l in out):
        fails.append("pdiv 0 line must end 'pdiv=0 sdiv=2 khz=0'")
    if sum(l.startswith("GPU GAP: PLL g3dl2 ") for l in out) != 1 or sum(l.startswith("GPU GAP:") for l in out) != 1:
        fails.append("pdiv 0 must print exactly one line-start `GPU GAP: PLL g3dl2` line (and none for the good word)")
    c = added(files[SRC])
    body = "\n".join(l[1:] for l in files[SRC] if l[:1] in (" ", "+") and not l.startswith("+++"))
    g, p1, p2, m = (body.find("CMU_G3D mux not selected"), body.find('sg_pll_print("g3d"'), body.find('sg_pll_print("g3dl2"'), body.find("sg_map(mali, 0x5000)"))
    if not (0 <= g < p1 < p2 and (m < 0 or p2 < m)):
        fails.append("print must sit after the mux-gate return and before the Mali map (g=%d p1=%d p2=%d m=%d)" % (g, p1, p2, m))
    if re.search(r"\bcmu\[[^\]]*\]\s*(=[^=]|\|=|&=)", c):
        fails.append("patch writes to cmu[]")
    if "0x140 + 4 * i" not in c or "0x180 + 4 * i" not in c:
        fails.append("offsets 0x140/0x180 (+4*i, CON0..CON4) missing")
    if any(l.startswith("-") and not l.startswith("---") for l in files[SRC]):
        fails.append("patch removes lines from %s (must be additions only)" % SRC)
    return fails


def mutants(text):
    yield "formula-no-sdiv", text.replace("((uint64_t)p->pdiv << p->sdiv)", "(uint64_t)p->pdiv", 1)
    yield "pdiv-guard-removed", text.replace("p->gap = (p->pdiv == 0);", "p->gap = 0;", 1)
    yield "mdiv-mask", text.replace("(con3 >> 16) & 0x3FFu", "(con3 >> 16) & 0xFFu", 1)
    yield "gap-line-dropped", text.replace("GPU GAP: PLL", "GPU gap: PLL", 1)
    yield "write-to-cmu", text.replace("\t\tsg_pll_print(\"g3dl2\", pl, printf);", "\t\tcmu[0x140 / 4] = 0;\n\t\tsg_pll_print(\"g3dl2\", pl, printf);", 1)
    yield "wrong-l2-offset", text.replace("0x180 + 4 * i", "0x184 + 4 * i", 1)
    ctx = " \tvolatile uint32_t *g = sg_map(mali, 0x5000);\n"
    yield "print-after-mali-map", text.replace(ctx, "", 1).replace("+\t{\n+\t\t/* Read-only PLL", ctx + "+\t{\n+\t\t/* Read-only PLL", 1)

class Patch0203(unittest.TestCase):
    """The gate runs unit files through unittest: 0 tests = NO TESTS RAN = FAIL (gate 2 of T-4aa8)."""

    @classmethod
    def setUpClass(cls):
        cls.text = PATCH.read_text(encoding="utf-8")

    def test_patch_checks(self):
        self.assertEqual(run_checks(self.text), [])

    def test_mutants_are_caught(self):
        for name, mt in mutants(self.text):
            with self.subTest(mutant=name):
                self.assertNotEqual(mt, self.text, "mutant did not change the text")
                self.assertTrue(run_checks(mt), "mutant survived")


def main():
    patch = PATCH
    if "--patch" in sys.argv:
        patch = pathlib.Path(sys.argv[sys.argv.index("--patch") + 1])
    try:
        text = patch.read_text(encoding="utf-8")
    except OSError as e:
        print("ERROR: %s" % e); return 2
    fails = run_checks(text)
    for f in fails:
        print("FAIL: " + f)
    if fails:
        print("RESULT: FAIL test_0203_gpu_pll_print %d check(s)" % len(fails)); return 1
    print("decode(0xA0620402) = en 1 lock 1 mdiv 98 pdiv 4 sdiv 2 khz 150528; pdiv 0 -> khz=0 + GPU GAP line, no crash")
    bad = n = 0
    for name, mt in mutants(text):
        n += 1
        if mt == text:
            print("MUTANT %s: did not change the text (test bug)" % name); bad += 1
        elif run_checks(mt):
            print("MUTANT %s: caught" % name)
        else:
            print("MUTANT %s: SURVIVED" % name); bad += 1
    if bad:
        print("RESULT: FAIL test_0203_gpu_pll_print %d mutant problem(s)" % bad); return 1
    print("RESULT: PASS test_0203_gpu_pll_print checks ok, %d mutants caught" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
