#!/usr/bin/env python3
"""Patch 0366 (lowclk, LOG ONLY sampled source hash per present, boot-arg sweetfbhash): host test. Extracts the pure block between SFH-PURE-BEGIN / SFH-PURE-END
(sfh_grid, sfh_bucket, sfh_account, sfh_nohash, sfh_cost_add, sfh_cost_p50, sfh_reset) from xnu/patches/0366-sweet-fb-hash.patch, compiles it as C and checks the
grid (every sampled word changes the hash, an unsampled word does not, pitch, touch callback), the job buckets, the changed/unchanged accounting with the first-present
and nohash rules, the cost p50/max and the reset, then text checks of every hook in the patch with a mutant per check.
`RESULT: PASS 0366-fbhash cases=N mutants=M`. Not a hardware or kernel-build test."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0366-sweet-fb-hash.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
static uint32_t ntouch;
static void touch(const uint8_t *p) { (void)p; ntouch++; }
#define W 1080u
#define H 2400u
static uint32_t buf[W * H];
static uint64_t g(void) { return sfh_grid(SFH_FNV_OFF, (const uint8_t *)buf, W * 4, W, H, NULL); }
int main(void)
{
	/* sampled position count: 75 rows x 34 words (rows with offset c0 = (r/32)%%32: 1080 - c0 words / 32 rounded up) */
	uint32_t expect = 0;
	for (uint32_t r = 0; r < H; r += 32) for (uint32_t c = (r / 32) %% 32; c < W; c += 32) expect++;
	CHECK(sfh_grid(SFH_FNV_OFF, (const uint8_t *)buf, W * 4, W, H, touch) == g() && ntouch == expect);
	CHECK(expect >= 2500 && expect <= 2600);
	uint64_t base = g();
	CHECK(g() == base);
	/* every sampled word matters */
	uint32_t hit = 0, nsamp = 0;
	for (uint32_t r = 0; r < H; r += 32) for (uint32_t c = (r / 32) %% 32; c < W; c += 32) {
		buf[(uint64_t)r * W + c] ^= 0x1;
		nsamp++;
		if (g() != base) hit++;
		buf[(uint64_t)r * W + c] ^= 0x1;
	}
	CHECK(nsamp == expect && hit == nsamp);
	/* a word that is not sampled does not (row 1, row 33 col 0, col 1 of a sampled row) */
	buf[1 * W + 0] ^= 0xff; CHECK(g() == base); buf[1 * W + 0] ^= 0xff;
	buf[0 * W + 1] ^= 0xff; CHECK(g() == base); buf[0 * W + 1] ^= 0xff;
	buf[32 * W + 0] ^= 0xff; CHECK(g() == base); buf[32 * W + 0] ^= 0xff;   /* row 32 starts at column 1 */
	buf[32 * W + 1] ^= 0xff; CHECK(g() != base); buf[32 * W + 1] ^= 0xff;
	/* a different value in the same word: the value, not just a flag, is hashed (two different values give two different hashes) */
	buf[0] = 5; uint64_t h5 = g(); buf[0] = 6; CHECK(g() != h5 && h5 != base); buf[0] = 0;
	/* order matters (swap two sampled words) */
	buf[0] = 1; buf[33] = 2; uint64_t a = g(); buf[0] = 2; buf[33] = 1; CHECK(g() != a); buf[0] = buf[33] = 0;
	/* pitch: a padded surface is read with its pitch: compare with an independent index-math reference over distinct values */
	{
		static uint32_t pad[(W + 20) * 70];
		for (uint32_t i = 0; i < (W + 20) * 70; i++) pad[i] = i * 2654435761u + 1;
		uint64_t ref = SFH_FNV_OFF;
		for (uint32_t r = 0; r < 70; r += 32) for (uint32_t c = (r / 32) %% 32; c < W; c += 32) ref = (ref ^ pad[(uint64_t)r * (W + 20) + c]) * 1099511628211ull;
		CHECK(sfh_grid(SFH_FNV_OFF, (const uint8_t *)pad, (W + 20) * 4, W, 70, NULL) == ref);
	}
	/* tiny surfaces: 1x1 has one sample; 0 height / 0 width none */
	{
		uint32_t one = 9;
		ntouch = 0;
		uint64_t h1 = sfh_grid(SFH_FNV_OFF, (const uint8_t *)&one, 4, 1, 1, touch);
		CHECK(ntouch == 1 && h1 == sfh_mix(SFH_FNV_OFF, 9));
		ntouch = 0;
		CHECK(sfh_grid(SFH_FNV_OFF, (const uint8_t *)&one, 4, 0, 1, touch) == SFH_FNV_OFF && sfh_grid(SFH_FNV_OFF, (const uint8_t *)&one, 4, 1, 0, touch) == SFH_FNV_OFF && ntouch == 0);
	}
	/* buckets */
	CHECK(sfh_bucket(true, 0) == SFH_B_J0 && sfh_bucket(true, 1) == SFH_B_J12 && sfh_bucket(true, 2) == SFH_B_J12 && sfh_bucket(true, 3) == SFH_B_J34 &&
	      sfh_bucket(true, 4) == SFH_B_J34 && sfh_bucket(true, 5) == SFH_B_J5P && sfh_bucket(true, 0xfffffffeu) == SFH_B_J5P && sfh_bucket(false, 0) == SFH_B_UNK &&
	      sfh_bucket(false, 7) == SFH_B_UNK);
	/* accounting */
	static struct sfh_state s;
	memset(&s, 0, sizeof s);
	CHECK(sfh_account(&s, 10, SFH_B_J12) == -1);                      /* first: seeds only */
	for (int i = 0; i < SFH_B_N; i++) CHECK(s.pres[i] == 0 && s.chg[i] == 0);
	CHECK(sfh_account(&s, 10, SFH_B_J12) == 0 && s.pres[SFH_B_J12] == 1 && s.chg[SFH_B_J12] == 0);
	CHECK(sfh_account(&s, 11, SFH_B_J12) == 1 && s.pres[SFH_B_J12] == 2 && s.chg[SFH_B_J12] == 1);
	CHECK(sfh_account(&s, 11, SFH_B_J5P) == 0 && s.pres[SFH_B_J5P] == 1 && s.chg[SFH_B_J5P] == 0 && s.pres[SFH_B_J12] == 2);
	CHECK(sfh_account(&s, 12, SFH_B_UNK) == 1 && s.pres[SFH_B_UNK] == 1 && s.chg[SFH_B_UNK] == 1);
	CHECK(sfh_account(&s, 13, SFH_B_J0) == 1 && sfh_account(&s, 14, SFH_B_J34) == 1 && s.pres[SFH_B_J0] == 1 && s.pres[SFH_B_J34] == 1);
	/* nohash: counted, the next hashed present seeds again */
	sfh_nohash(&s);
	CHECK(s.nohash == 1 && !s.have_prev);
	CHECK(sfh_account(&s, 14, SFH_B_J12) == -1 && s.pres[SFH_B_J12] == 2);
	CHECK(sfh_account(&s, 15, SFH_B_J12) == 1 && s.pres[SFH_B_J12] == 3);
	/* a change that comes back is two changes; equal hash after a different one is unchanged against the immediately previous only */
	CHECK(sfh_account(&s, 14, SFH_B_J12) == 1 && sfh_account(&s, 14, SFH_B_J12) == 0);
	/* cost */
	CHECK(sfh_cost_p50(&s) == 0 && s.cost_max == 0);
	sfh_cost_add(&s, 40); CHECK(sfh_cost_p50(&s) == 40 && s.cost_max == 40);
	sfh_cost_add(&s, 10); sfh_cost_add(&s, 30);
	CHECK(sfh_cost_p50(&s) == 30 && s.cost_max == 40);                 /* sorted 10 30 40: rank 2 */
	sfh_cost_add(&s, 20);
	CHECK(sfh_cost_p50(&s) == 20);                                       /* sorted 10 20 30 40: rank ceil(4/2) = 2 */
	sfh_cost_add(&s, 500);
	CHECK(sfh_cost_p50(&s) == 30 && s.cost_max == 500);                /* 10 20 30 40 500: rank 3 */
	for (uint32_t i = 0; i < SFH_COST_MAX + 10; i++) sfh_cost_add(&s, 1);
	CHECK(s.cost_n == SFH_COST_MAX);                                     /* cap, no overflow */
	sfh_cost_add(&s, 9999); CHECK(s.cost_max == 9999 && s.cost_n == SFH_COST_MAX);   /* max also past the cap */
	/* reset */
	s.first_logged = 7;
	sfh_reset(&s);
	CHECK(s.nohash == 0 && s.cost_n == 0 && s.cost_max == 0 && sfh_cost_p50(&s) == 0);
	for (int i = 0; i < SFH_B_N; i++) CHECK(s.pres[i] == 0 && s.chg[i] == 0);
	CHECK(s.have_prev && s.prev == 14 && s.first_logged == 7);         /* the comparison and the first-row budget survive the second */
	puts("ok");
	return 0;
}
'''


def body(text):
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("--- a/"):
            on = True
        if on and l[:1] in ("+", " ") and not l.startswith("+++"):
            out.append(l[1:])
    return "\n".join(out)


def extract(src):
    m = re.search(r"/\* SFH-PURE-BEGIN \*/\n(.*?)/\* SFH-PURE-END \*/", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0366-fbhash: pure block markers not found in the patch")
    return m.group(1)


def run(func):
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "t.c")
        exe = os.path.join(d, "t")
        with open(c, "w") as f:
            f.write(HARNESS % func)
        r = subprocess.run(["cc", "-Wall", "-Werror", "-Wno-unused-function", "-o", exe, c], capture_output=True, text=True)
        if r.returncode:
            return 2, r.stderr
        r = subprocess.run([exe], capture_output=True, text=True)
        return r.returncode, r.stderr + r.stdout


TEXT = [
    ("boot-arg 1 turns it on", r"PE_parse_boot_argn\(\"sweetfbhash\", &hv, sizeof\(hv\)\)\) \{\s*if \(hv == 1\) \{\s*gSweetFbHash = 1;"),
    ("unknown boot-arg value is a GAP", r"\} else if \(hv != 0\) \{\s*IOLog\(\"SWEET FB: GAP sweetfbhash=%d is not 0 or 1: ignored \(off\)"),
    ("swap() records n and jobs only when on", r"if \(gSweetFbHash\) \{[^\n]*\n\s*uint64_t hsq = 0, hdn = 0;\s*bool hok = sweet_mali_seq_peek\(&hsq, &hdn\) == 0;\s*fHashSwapN = \(uint32_t\)\(sweet_fb_swap_count \+ 1\);"),
    ("jobs = seqno delta to the previous swap, unknown without one", r"fHashSwapJobs = \(hok && fHashPrevSeq != 0 && hsq >= fHashPrevSeq\) \? \(uint32_t\)\(hsq - fHashPrevSeq\) : 0xffffffffu;\s*fHashPrevSeq = hok \? hsq : 0;"),
    ("n and jobs under fLock, before the unlock", r"fHashPrevSeq = hok \? hsq : 0;\s*\}\s*IOLockUnlock\(fLock\);"),
    ("n and jobs copied with the record", r"memcpy\(rec, fb->fPending, SWEET_FB_SWAP_REC_SIZE\);\s*fb->fHashPresN = fb->fHashSwapN;[^\n]*\n\s*fb->fHashPresJobs = fb->fHashSwapJobs;"),
    ("hash pass before the copy loop, not gated by the skip", r"if \(gSweetFbHash\) \{\s*static int hinv = -1;.*?sfh_reset\(hst\);.*?\}\s*\n\s*for \(uint32_t layer = 0; layer < SWEET_FB_LAYERS && !skip; layer\+\+\) \{"),
    ("source surface hashed", r"sfh_grid\(hh, \(const uint8_t \*\)\(hs->kernelAddress\(\) \+ hs->offset\), hs->bytesPerRow, hs->width, hs->height, hinv \? sfh_flush_line : NULL\)"),
    ("inv flushes the sampled lines", r"static void\s*sfh_flush_line\(const uint8_t \*p\)\s*\{\s*flush_dcache64\(\(addr64_t\)\(uintptr_t\)p, 64, FALSE\);"),
    ("layer id and rectangles mixed in", r"hh = sfh_mix\(hh, \(\(uint64_t\)layer << 32\) \| id\);.*?get32\(rec, 0x64 \+ 16 \* layer \+ 4 \* k\) << 32\) \| get32\(rec, 0xc4 \+ 16 \* layer \+ 4 \* k\)"),
    ("unmappable surface is a GAP and a nohash present", r"ok = false;\s*if \(h_gap_n\+\+ < 5 \|\| \(h_gap_n % 1000\) == 0\) \{\s*IOLog\(\"SWEET FB: GAP sweetfbhash: layer %u surface %u not mappable"),
    ("extent check against the memory length", r"hs->memory->getLength\(\)\) \? \"extent beyond the memory\""),
    ("nohash present accounted", r"\} else \{\s*sfh_nohash\(hst\);\s*\}"),
    ("cost measured around the whole pass", r"uint64_t c0 = mach_absolute_time\(\);.*?absolutetime_to_nanoseconds\(c1 - c0, &cost_ns\);.*?sfh_cost_add\(hst, \(uint32_t\)\(cost_ns / 1000\)\);"),
    ("bucket from the present's jobs", r"int bk = sfh_bucket\(fHashPresJobs != 0xffffffffu, fHashPresJobs\);\s*int r = sfh_account\(hst, hh, bk\);"),
    ("first-changed rows capped at 20 and only in 1-2 / 3-4", r"if \(r == 1 && \(bk == SFH_B_J12 \|\| bk == SFH_B_J34\) && hst->first_logged < 20\) \{\s*hst->first_logged\+\+;\s*IOLog\(\"SWEET FB: hash changed present swap n %u jobs %u bucket %s"),
    ("once per second", r"if \(h_now - h_t0_ns >= 1000000000ull\) \{"),
    ("row format", r"SWEET FB: hash jobs0 %u/%u jobs1_2 %u/%u jobs3_4 %u/%u jobs5p %u/%u cost_p50 %u max %u us nojobs %u/%u nohash %u\\n"),
    ("row prints p50 and max", r"sfh_cost_p50\(hst\), hst->cost_max,"),
    ("state reset after the row", r"sfh_reset\(hst\);\s*h_t0_ns = h_now;"),
    ("state zeroed in start()", r"memset\(&fHash, 0, sizeof\(fHash\)\);"),
]
NEG = [
    ("SWAPT row not touched: no SWAPT IOLog added", r"IOLog\(\"SWEET SWAPT"),
    ("no existing line removed or changed", r"(?m)^-[^-]"),
    ("no register access in the added lines", r"(?m)^\+[^\n]*\b(wr|rr|writel|OSWriteLE32)\("),
    ("no write through a source pointer", r"(?m)^\+[^\n]*\*\(\(?(volatile )?uint[0-9]+_t \*\)\(hs->"),
]


def main():
    text = open(PATCH).read()
    func = extract(body(text))
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0366-fbhash: harness")
        return 1
    cases = 1
    src = body(text)
    added = "\n".join(l for l in text.splitlines() if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---")))
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0366-fbhash: patch text check failed: %s" % name)
            return 1
        cases += 1
    for name, rx in NEG:
        if re.search(rx, added):
            print("RESULT: FAIL 0366-fbhash: forbidden text in the added lines: %s" % name)
            return 1
        cases += 1
    mutants = [
        ("row stride 32 -> 64", func.replace("#define SFH_N           32u", "#define SFH_N           64u")),
        ("column stride 32 -> 64", func.replace("#define SFH_M           32u", "#define SFH_M           64u")),
        ("column shift removed", func.replace("(r / SFH_N) % SFH_M", "0")),
        ("pitch ignored", func.replace("base + (uint64_t)r * pitch", "base + (uint64_t)r * w * 4")),
        ("touch not called", func.replace("touch(row + (uint64_t)c * 4);", "(void)touch;")),
        ("value not hashed", func.replace("h = sfh_mix(h, px);", "h = sfh_mix(h, px != 0);")),
        ("mix is xor only", func.replace("return (h ^ v) * SFH_FNV_PRIME;", "return h ^ v;")),
        ("bucket 2 -> j34", func.replace("jobs <= 2 ? SFH_B_J12", "jobs <= 1 ? SFH_B_J12")),
        ("bucket 4 -> j5p", func.replace("jobs <= 4 ? SFH_B_J34", "jobs <= 3 ? SFH_B_J34")),
        ("bucket 0 merged into 1-2", func.replace("return jobs == 0 ? SFH_B_J0 :", "return")),
        ("unknown jobs as bucket 0", func.replace("return SFH_B_UNK;", "return SFH_B_J0;")),
        ("first present counted", func.replace("if (s->have_prev) {\n\t\tr = hash != s->prev;", "{\n\t\tr = hash != s->prev;")),
        ("changed not counted", func.replace("s->chg[bucket] += (uint32_t)r;", "")),
        ("present not counted", func.replace("s->pres[bucket]++;", "")),
        ("previous hash not kept", func.replace("s->prev = hash;", "")),
        ("changed compares equal", func.replace("r = hash != s->prev;", "r = hash == s->prev;")),
        ("nohash not counted", func.replace("s->nohash++;", "")),
        ("nohash keeps the comparison", func.replace("s->have_prev = false;", "")),
        ("cost cap removed", func.replace("if (s->cost_n < SFH_COST_MAX) {", "if (1) {")),
        ("cost max only inside the cap", func.replace("\tif (us > s->cost_max) {", "\tif (s->cost_n < SFH_COST_MAX && us > s->cost_max) {")),
        ("p50 rank rounds up", func.replace("(n + 1) / 2 - 1", "n / 2")),
        ("p50 not sorted", func.replace("while (j > 0 && tmp[j - 1] > v) {", "while (0) {")),
        ("p50 of empty not guarded", func.replace("if (n == 0) {\n\t\treturn 0;\n\t}", "")),
        ("reset keeps the counters", func.replace("memset(s->pres, 0, sizeof(s->pres));", "")),
        ("reset keeps the changed counters", func.replace("memset(s->chg, 0, sizeof(s->chg));", "")),
        ("reset keeps nohash", func.replace("s->nohash = 0;", "")),
        ("reset keeps the cost samples", func.replace("s->cost_n = 0;", "")),
        ("reset keeps the max", func.replace("s->cost_max = 0;", "")),
        ("reset drops the previous hash", func.replace("s->cost_max = 0;\n}", "s->cost_max = 0;\n\ts->have_prev = false;\n}")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0366-fbhash: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0366-fbhash: mutant %s survived" % name)
            return 1
        dead += 1
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0366-fbhash: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0366-fbhash cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
