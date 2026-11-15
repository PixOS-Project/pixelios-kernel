"""Patch 0358 (UFS answers DKIOCISSOLIDSTATE = 1, DKIOCGETFEATURES = 0): host test.
Extracts the added sweet_ufs_disk_attr() from xnu/patches/0358-sweet-ufs-ssd.patch, compiles it against fake kernel names, and checks the answers,
the one log line per query, the ENOTTY default, that the ioctl switch dispatches both commands to it, and (mutants) that a wrong answer fails.
Prints `RESULT: PASS 0358-ufs-ssd cases=N mutants=M`. Not a hardware test."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0358-sweet-ufs-ssd.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
typedef unsigned long u_long;
#define ENOTTY 25
#define DKIOCISSOLIDSTATE 79UL
#define DKIOCGETFEATURES 76UL
#define OTHER 5UL
static int lines;
static char last[256];
#define printf(...) (lines++, snprintf(last, sizeof last, __VA_ARGS__))
%s
#undef printf
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
	uint32_t f = 77;
	CHECK(sweet_ufs_disk_attr(DKIOCISSOLIDSTATE, &f) == 0 && f == 1);
	CHECK(lines == 1 && strstr(last, "DKIOCISSOLIDSTATE answered 1"));
	f = 77;
	CHECK(sweet_ufs_disk_attr(DKIOCISSOLIDSTATE, &f) == 0 && f == 1);
	CHECK(lines == 1);                       /* second query: no second line */
	f = 77;
	CHECK(sweet_ufs_disk_attr(DKIOCGETFEATURES, &f) == 0 && f == 0);   /* no FUA 0x1, barrier 0x2, priority 0x4, unmap 0x10 */
	CHECK(lines == 2 && strstr(last, "DKIOCGETFEATURES answered 0"));
	f = 77;
	CHECK(sweet_ufs_disk_attr(DKIOCGETFEATURES, &f) == 0 && f == 0);
	CHECK(lines == 2);
	f = 77;
	CHECK(sweet_ufs_disk_attr(OTHER, &f) == ENOTTY && f == 77 && lines == 2);   /* unknown command untouched */
	puts("ok");
	return 0;
}
'''


def added(text):
    out = []
    for l in text.splitlines():
        if l.startswith("+") and not l.startswith("+++"):
            out.append(l[1:])
    return out


def extract(text):
    src = "\n".join(added(text))
    m = re.search(r"static int sweet_ufs_ssd_logged.*?\n}\n", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0358-ufs-ssd: sweet_ufs_disk_attr not found in the patch")
    return m.group(0)


def run(func):
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        with open(c, "w") as f:
            f.write(HARNESS % func)
        r = subprocess.run(["cc", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            return 2, r.stderr
        r = subprocess.run([exe], capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout


def main():
    text = open(PATCH).read()
    cases = 0
    func = extract(text)
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0358-ufs-ssd: harness")
        return 1
    cases += 9
    src = "\n".join(added(text))
    if not re.search(r"case DKIOCISSOLIDSTATE:\s*case DKIOCGETFEATURES:\s*return sweet_ufs_disk_attr\(cmd, f\);", src):
        print("RESULT: FAIL 0358-ufs-ssd: ioctl switch does not dispatch both commands")
        return 1
    cases += 1
    for bit in ("DK_FEATURE", "FORCE_UNIT_ACCESS", "0x1;", "0x10;"):
        if bit in func.split("*/")[-1]:
            print("RESULT: FAIL 0358-ufs-ssd: a feature bit appears in the code (none is supported)")
            return 1
    cases += 1
    mutants = [
        ("ssd=0", func.replace("*f = 1;", "*f = 0;")),
        ("feat=unmap", func.replace("*f = 0;\n\t\tif (!sweet_ufs_feat_logged)", "*f = 0x10;\n\t\tif (!sweet_ufs_feat_logged)")),
        ("no ssd log", func.replace("sweet_ufs_ssd_logged = 1;", "")),
        ("default success", func.replace("return ENOTTY;", "return 0;")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0358-ufs-ssd: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0358-ufs-ssd: mutant %s survived" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0358-ufs-ssd cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
