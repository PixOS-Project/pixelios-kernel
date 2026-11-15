"""Patch 0361 (panel copy skip for unchanged presents): host test. Extracts the added pure decision function sweet_fb_skip_why() (and its types) from
xnu/patches/0361-sweet-fb-skip.patch, compiles it as C, and checks every reason in its priority order, that the swap id is the only record field ignored,
that each of the 4 layer seeds and the Mali seqno matter, and the refresh bound. Then text checks of the hooks in the patch (force events, the loop gate,
the record after a copy, the boot-arg parse + GAP, count-only never skips) and one mutant per check (each must fail). Prints `RESULT: PASS 0361-fb-skip cases=N mutants=M`.
Not a hardware test; the decision inputs (Mali seqno, surface seed) are the kernel's own counters, cited in the patch header."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0361-sweet-fb-skip.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#define SWEET_FB_SWAP_REC_SIZE 0x46c
#define SWEET_FB_LAYERS 4
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
static struct sweet_fb_last L;
static uint8_t rec[SWEET_FB_SWAP_REC_SIZE];
static uint32_t seed[SWEET_FB_LAYERS];
static void base(void) { memset(&L, 0, sizeof L); memset(rec, 0, sizeof rec); memset(seed, 0, sizeof seed); L.valid = true; L.clean = true; L.seq = 7; seed[0] = 11; seed[1] = 12; L.seed[0] = 11; L.seed[1] = 12; rec[0x54] = 5; memcpy(L.rec, rec, sizeof rec); }
static int why(bool force, bool muted, bool gpu, uint64_t seq) { return sweet_fb_skip_why(&L, rec, seed, force, muted, gpu, seq); }
int main(void)
{
	base(); L.valid = false; CHECK(why(false, true, true, 7) == SFB_W_FIRST);
	base(); CHECK(why(false, true, true, 7) == SFB_W_SKIP);
	base(); rec[0x50] = 1; rec[0x51] = 2; rec[0x52] = 3; rec[0x53] = 4; CHECK(why(false, true, true, 7) == SFB_W_SKIP);        /* the swap id changes every swap */
	base(); CHECK(why(true, true, true, 7) == SFB_W_FORCED);
	base(); CHECK(why(false, false, true, 7) == SFB_W_CONSOLE);
	base(); CHECK(why(false, true, false, 7) == SFB_W_NOSIG);
	base(); L.clean = false; CHECK(why(false, true, true, 7) == SFB_W_GPUBUSY);
	for (unsigned o = 0; o < SWEET_FB_SWAP_REC_SIZE; o++) {                                                                          /* every other record byte matters */
		if (o >= 0x50 && o < 0x54) continue;
		base(); rec[o] ^= 0x40; CHECK(why(false, true, true, 7) == SFB_W_REC);
	}
	for (int k = 0; k < SWEET_FB_LAYERS; k++) { base(); seed[k] += 1; CHECK(why(false, true, true, 7) == SFB_W_SEED); }
	base(); CHECK(why(false, true, true, 8) == SFB_W_GPU);
	base(); CHECK(why(false, true, true, 6) == SFB_W_GPU);
	base(); L.unchanged_run = 149; CHECK(why(false, true, true, 7) == SFB_W_SKIP);
	base(); L.unchanged_run = 150; CHECK(why(false, true, true, 7) == SFB_W_REFRESH);
	/* priority: not-valid > force > console > no signal > gpu busy > record > seed > gpu seq > refresh */
	base(); L.valid = false; CHECK(why(true, false, false, 9) == SFB_W_FIRST);
	base(); CHECK(why(true, false, false, 9) == SFB_W_FORCED);
	base(); CHECK(why(false, false, false, 9) == SFB_W_CONSOLE);
	base(); CHECK(why(false, true, false, 9) == SFB_W_NOSIG);
	base(); L.clean = false; rec[1] = 1; CHECK(why(false, true, true, 9) == SFB_W_GPUBUSY);
	base(); rec[1] = 1; seed[0]++; CHECK(why(false, true, true, 9) == SFB_W_REC);
	base(); seed[0]++; CHECK(why(false, true, true, 9) == SFB_W_SEED);
	puts("ok");
	return 0;
}
'''


def added(text):
    return "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))


def body(text):
    """added and context lines of the diff body (the header text before the first --- line is not code)"""
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("--- a/"):
            on = True
        if on and l[:1] in ("+", " ") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def extract(src):
    m = re.search(r"#define SWEET_FB_SWAPID_OFF.*?\nsweet_fb_skip_why\(.*?\n}\n", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0361-fb-skip: sweet_fb_skip_why not found in the patch")
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


# (name, regex that must match the added text of the patch)
TEXT = [
    ("power change forces a copy", r"fFB->fDirty = true;[^\n]*\n\s*__atomic_store_n\(&fFB->fForceCopy, true"),
    ("QR hold forces a copy", r"the QR log owns the panel[^\n]*\n\s*__atomic_store_n\(&fb->fForceCopy, true"),
    ("force consumed by atomic exchange", r"__atomic_exchange_n\(&fForceCopy, false"),
    ("copy loop gated by skip", r"layer < SWEET_FB_LAYERS && !skip;"),
    ("count-only never skips", r"sk_win_would\+\+;\s*skip = false;"),
    ("record after a copy", r"if \(sk_decided && !skip\) \{[^}]*fLast\.valid = true;[^}]*fLast\.unchanged_run = 0;[^}]*memcpy\(fLast\.rec, rec"),
    ("clean needs idle before and after", r"fLast\.clean = sk_gpu && g2 && sk_sq == sk_dn && sq2 == sk_sq;"),
    ("bad boot-arg value is a GAP", r"SWEET FB: GAP sweetfb_skip=%d is not 0, 1 or 2"),
    ("off mode decides nothing", r"if \(skip_mode != 0\) \{\s*bool force"),
    ("seed read from the surface", r"sweet_surface_seed_peek\(ls\)"),
    ("gpu signal is the Mali seqno", r"sweet_mali_seq_peek\(&sk_sq, &sk_dn\) == 0"),
    ("counts in the copy-time row", r"max %u us unchanged %llu skipped %llu"),
    ("10 s row", r"SWEET FB: panel skip \(10 s, mode %d\)"),
]


def main():
    text = open(PATCH).read()
    src = body(text)
    func = extract(added(text))
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0361-fb-skip: harness")
        return 1
    cases = 1
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0361-fb-skip: patch text check failed: %s" % name)
            return 1
        cases += 1
    mutants = [
        ("no first-frame check", func.replace("if (!l->valid) {\n\t\treturn SFB_W_FIRST;", "if (0) {\n\t\treturn SFB_W_FIRST;")),
        ("force ignored", func.replace("if (force) {", "if (0) {")),
        ("console ignored", func.replace("if (!console_muted) {", "if (0) {")),
        ("no gpu signal ignored", func.replace("if (!gpu_known) {", "if (0) {")),
        ("busy last copy ignored", func.replace("if (!l->clean) {", "if (0) {")),
        ("swap id compared", func.replace("SWEET_FB_SWAPID_OFF + 4, l->rec + SWEET_FB_SWAPID_OFF + 4, SWEET_FB_SWAP_REC_SIZE - SWEET_FB_SWAPID_OFF - 4", "SWEET_FB_SWAPID_OFF, l->rec + SWEET_FB_SWAPID_OFF, SWEET_FB_SWAP_REC_SIZE - SWEET_FB_SWAPID_OFF")),
        ("record tail not compared", func.replace("SWEET_FB_SWAP_REC_SIZE - SWEET_FB_SWAPID_OFF - 4)", "16)")),
        ("record head not compared", func.replace("memcmp(rec, l->rec, SWEET_FB_SWAPID_OFF)", "memcmp(rec, l->rec, 4)")),
        ("seed ignored", func.replace("if (memcmp(seed, l->seed, sizeof(l->seed)) != 0) {", "if (0) {")),
        ("seed layer 3 ignored", func.replace("sizeof(l->seed))", "3 * sizeof(l->seed[0]))")),
        ("seqno ignored", func.replace("if (seq_now != l->seq) {", "if (0) {")),
        ("no refresh", func.replace("if (l->unchanged_run >= SWEET_FB_SKIP_REFRESH) {", "if (0) {")),
        ("refresh off by one", func.replace("l->unchanged_run >= SWEET_FB_SKIP_REFRESH", "l->unchanged_run > SWEET_FB_SKIP_REFRESH")),
        ("never skips", func.replace("return SFB_W_SKIP;\n}", "return SFB_W_REFRESH;\n}")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0361-fb-skip: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0361-fb-skip: mutant %s survived" % name)
            return 1
        dead += 1
    # text mutants: drop one hook from the patch text, the check must notice
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0361-fb-skip: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0361-fb-skip cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
