#!/usr/bin/env python3
"""Patch 0364 (lowclk, LOG ONLY Mali completion latency, boot-arg sweetcomplat): host test. Extracts the pure block between COMPLAT-PURE-BEGIN / COMPLAT-PURE-END
(histogram, percentile, cl_account, cl_reset) from xnu/patches/0364-sweet-mali-complat.patch, compiles it as C and checks percentile ranks, saturation, the dropped-sample
counters (noirq / nodone / neg / over / drop_total) and the reset, then text checks of every hook in the patch (ISR stamp at entry before the mask write, cmpxchg without
lock, thread consume + account, start stamps armed only inside check_job, per-job counter, boot-arg + GAPs, the per-second row, the unchanged SWAPT row) with a mutant per check.
`RESULT: PASS 0364-complat cases=N mutants=M`. Not a hardware or kernel-build test."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = os.path.join(os.path.dirname(HERE), "xnu", "patches", "0364-sweet-mali-complat.patch")

HARNESS = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define bzero(p, n) memset((p), 0, (n))
%s
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %%d: %%s\n", __LINE__, #c); return 1; } } while (0)
static struct cl_state S;
int main(void)
{
	struct cl_hist *h = &S.done;
	/* empty histogram: every percentile 0 */
	CHECK(cl_hist_pct(h, 1, 2) == 0 && cl_hist_pct(h, 9, 10) == 0 && cl_hist_pct(h, 1, 0) == 0);
	/* one sample is every percentile */
	cl_hist_add(h, 37);
	CHECK(cl_hist_pct(h, 1, 2) == 37 && cl_hist_pct(h, 9, 10) == 37 && h->maxv == 37 && h->n == 1);
	/* 3 samples: rank(p50) = 2, rank(p90) = ceil(2.7) = 3 */
	memset(&S, 0, sizeof S);
	cl_hist_add(h, 10); cl_hist_add(h, 20); cl_hist_add(h, 30);
	CHECK(cl_hist_pct(h, 1, 2) == 20 && cl_hist_pct(h, 9, 10) == 30 && cl_hist_pct(h, 1, 1) == 30 && h->maxv == 30);
	/* percentile 0 = the smallest sample, not bin 0 */
	CHECK(cl_hist_pct(h, 0, 1) == 10);
	/* 1..100: p50 = 50, p90 = 90 (insertion order must not matter) */
	memset(&S, 0, sizeof S);
	for (int i = 100; i >= 1; i--) cl_hist_add(h, i);
	CHECK(cl_hist_pct(h, 1, 2) == 50 && cl_hist_pct(h, 9, 10) == 90 && cl_hist_pct(h, 1, 1) == 100 && h->n == 100 && h->sat == 0);
	/* 1..10: p50 = 5, p90 = 9 */
	memset(&S, 0, sizeof S);
	for (int i = 1; i <= 10; i++) cl_hist_add(h, i);
	CHECK(cl_hist_pct(h, 1, 2) == 5 && cl_hist_pct(h, 9, 10) == 9);
	/* bin edge: 510 is exact, 511 is the first saturated value */
	memset(&S, 0, sizeof S);
	cl_hist_add(h, 510);
	CHECK(h->sat == 0 && cl_hist_pct(h, 1, 2) == 510 && h->bin[510] == 1);
	cl_hist_add(h, 511);
	CHECK(h->sat == 1 && h->bin[CL_BINS - 1] == 1);
	cl_hist_add(h, CL_BINS);
	CHECK(h->sat == 2 && h->bin[CL_BINS - 1] == 2 && h->n == 3);
	/* saturated samples: the percentile that lands there reports the true max, counted in sat; a 64-bit huge value clips to 32 bits and does not wrap into a low bin */
	memset(&S, 0, sizeof S);
	for (int i = 0; i < 8; i++) cl_hist_add(h, 20);
	cl_hist_add(h, 600); cl_hist_add(h, 100000);
	CHECK(h->n == 10 && h->sat == 2 && cl_hist_pct(h, 1, 2) == 20 && cl_hist_pct(h, 9, 10) == 100000);
	CHECK(cl_hist_pct(h, 1, 1) == 100000 && h->maxv == 100000);
	memset(&S, 0, sizeof S);
	cl_hist_add(h, 1ull << 40);
	CHECK(h->sat == 1 && h->maxv == 0xFFFFFFFFu && cl_hist_pct(h, 1, 2) == 0xFFFFFFFFu && h->bin[0] == 0);
	/* cl_account: nothing completed, no stamp -> nothing at all */
	memset(&S, 0, sizeof S);
	cl_account(&S, 0, 5000, 0, 0);
	CHECK(S.done.n == 0 && S.nodone == 0 && S.noirq == 0 && S.drop_total == 0);
	/* stamp but no completion -> nodone */
	cl_account(&S, 1000, 5000, 0, 0);
	CHECK(S.nodone == 1 && S.drop_total == 1 && S.done.n == 0);
	/* completion without stamp -> noirq per job, no sample */
	cl_account(&S, 0, 5000, 4000, 2);
	CHECK(S.noirq == 2 && S.drop_total == 3 && S.done.n == 0 && S.start.n == 0);
	/* normal: 2 jobs, done 50 us, start 20 us; ns are truncated to us */
	memset(&S, 0, sizeof S);
	cl_account(&S, 1000000, 1050999, 1020000, 2);
	CHECK(S.done.n == 2 && S.done.bin[50] == 2 && S.start.n == 1 && S.start.bin[20] == 1 && S.drop_total == 0);
	CHECK(S.noirq == 0 && S.nodone == 0 && S.neg == 0 && S.over == 0);
	/* no job started -> no start sample */
	memset(&S, 0, sizeof S);
	cl_account(&S, 1000000, 1005000, 0, 1);
	CHECK(S.done.n == 1 && S.done.bin[5] == 1 && S.start.n == 0);
	/* sub-microsecond: bin 0 */
	cl_account(&S, 1000, 1999, 0, 1);
	CHECK(S.done.bin[0] == 1);
	/* clock went backwards: neg, no sample */
	memset(&S, 0, sizeof S);
	cl_account(&S, 9000, 8000, 8500, 3);
	CHECK(S.neg == 3 && S.drop_total == 3 && S.done.n == 0 && S.start.n == 0);
	/* start before the stamp: the done sample stays, the start sample is a neg */
	memset(&S, 0, sizeof S);
	cl_account(&S, 9000, 19000, 8000, 1);
	CHECK(S.done.n == 1 && S.start.n == 0 && S.neg == 1 && S.drop_total == 1);
	/* more than CL_MAXJ jobs: CL_MAXJ sampled, the rest counted as over */
	memset(&S, 0, sizeof S);
	cl_account(&S, 1000, 11000, 0, CL_MAXJ + 3);
	CHECK(S.done.n == CL_MAXJ && S.over == 3 && S.drop_total == 3);
	CHECK(CL_MAXJ == 8 && CL_BINS == 512);   /* the printed row and the comment promise these */
	/* over with no stamp: capped jobs noirq + the rest over, nothing lost */
	memset(&S, 0, sizeof S);
	cl_account(&S, 0, 11000, 0, CL_MAXJ + 1);
	CHECK(S.noirq == CL_MAXJ && S.over == 1 && S.drop_total == CL_MAXJ + 1);
	/* reset: every per-second field zero, drop_total kept */
	cl_hist_add(&S.done, 9); S.nodone = 4; S.neg = 5;
	cl_reset(&S);
	CHECK(S.drop_total == CL_MAXJ + 1 && S.done.n == 0 && S.done.maxv == 0 && S.done.bin[9] == 0 && S.noirq == 0 && S.over == 0 && S.nodone == 0 && S.neg == 0 && S.start.n == 0);
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
    m = re.search(r"/\* COMPLAT-PURE-BEGIN[^\n]*\*/\n(.*?)/\* COMPLAT-PURE-END \*/", src, re.S)
    if not m:
        raise SystemExit("RESULT: FAIL 0364-complat: pure block markers not found in the patch")
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
    ("ISR stamps at entry, before the status read", r"uintptr_t which = \(uintptr_t\)arg;\s*uint64_t t_in = cl_on \? mach_absolute_time\(\) : 0;"),
    ("ISR stamp is one cmpxchg, only for JOB, before the mask write, after the spurious return", r"sm_irq_spur\+\+;\s*return 0;\s*\}\s*if \(cl_on\) \{[^\n]*\n\s*\(void\)os_atomic_cmpxchg\(&cl_isr_t0, 0, t_in, relaxed\);\s*\}\s*sm\.g\[R_JOB_MASK / 4\] = 0;"),
    ("boot-arg 1 turns it on", r"PE_parse_boot_argn\(\"sweetcomplat\", &pv, sizeof\(pv\)\)\) \{[^\n]*\n\s*if \(pv == 1\) \{\s*cl_on = 1;"),
    ("unknown boot-arg value is a GAP", r"\} else if \(pv != 0\) \{\s*printf\(\"SWEET MALI GAP: sweetcomplat=%u is not 0 or 1, ignored"),
    ("armed before the IRQ setup", r"cl_on = 1;.*?sm_irq_setup\(\);"),
    ("GAP without the JOB interrupt", r"if \(cl_on && !sm_irq_on\) \{\s*printf\(\"SWEET MALI GAP: sweetcomplat=1 needs the JOB interrupt"),
    ("thread consumes the stamp each wake", r"uint64_t t_isr = cl_on \? os_atomic_xchg\(&cl_isr_t0, 0, relaxed\) : 0;"),
    ("thread resets start stamp and arms around check_job", r"cl_start_t0 = 0;\s*cl_armed = 1;\s*\}\s*check_job\(\);\s*if \(cl_on\) \{\s*cl_account_job\(t_isr, cl_jobs0\);"),
    ("disarm + account after check_job", r"cl_armed = 0;\s*cl_account\(&cl_st, cl_ns\(t_isr\), cl_ns\(mach_absolute_time\(\)\), cl_ns\(cl_start_t0\), \(uint32_t\)\(cl_done_n - jobs_before\)\);"),
    ("stamp with no job in flight is counted", r"if \(cl_on && !sm\.busy && t_isr != 0\) \{[^\n]*\n\s*cl_account\(&cl_st, cl_ns\(t_isr\), 0, 0, 0\);"),
    ("start stamp after the START write (JS0 path)", r"wr\(R_JS_BASE\(slot\) \+ JS_COMMAND_NEXT, JS_CMD_START\);\s*if \(cl_armed && cl_start_t0 == 0\) \{[^\n]*\n\s*cl_start_t0 = mach_absolute_time\(\);"),
    ("start stamp after the START write (overlap path)", r"wr\(R_JS_BASE\(1\) \+ JS_COMMAND_NEXT, JS_CMD_START\);\s*if \(cl_armed && cl_start_t0 == 0\) \{[^\n]*\n\s*cl_start_t0 = mach_absolute_time\(\);"),
    ("job counter in perf_job_done_at", r"perf_jobs\+\+;\s*cl_done_n\+\+;"),
    ("tick after sm_perf_tick, lock not held", r"unlock\(&sm_lock\);\s*sm_perf_tick\(\);\s*cl_tick\(\);"),
    ("tick reads and resets under the lock", r"lck_mtx_lock\(&sm_lock\);\s*struct cl_state \*s = &cl_st;.*?cl_reset\(s\);\s*lck_mtx_unlock\(&sm_lock\);"),
    ("once per second", r"if \(now - cl_last_us < 1000000\) \{\s*return;"),
    ("row format", r"SWEET PERF: complat n %u p50 %u p90 %u max %u us start_n %u start_p50 %u start_p90 %u start_max %u us noirq %u nodone %u neg %u sat %u over %u drop_total %llu\\n"),
    ("row prints p50 and p90 from the done histogram", r"p50 = cl_hist_pct\(&s->done, 1, 2\), p90 = cl_hist_pct\(&s->done, 9, 10\)"),
    ("mach_time include", r"#include <mach/mach_time.h>"),
]
NEG = [
    ("SWAPT row not touched", r"SWEET SWAPT"),
    ("no register write in the added lines other than the existing ones", r"(?m)^\+[^\n]*\b(wr|rr)\([^\n]*\);[^\n]*cl_"),
]


def main():
    text = open(PATCH).read()
    func = extract(body(text))
    rc, out = run(func)
    if rc:
        print(out)
        print("RESULT: FAIL 0364-complat: harness")
        return 1
    cases = 1
    src = body(text)
    added = "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    for name, rx in TEXT:
        if not re.search(rx, src, re.S):
            print("RESULT: FAIL 0364-complat: patch text check failed: %s" % name)
            return 1
        cases += 1
    for name, rx in NEG:
        if re.search(rx, added):
            print("RESULT: FAIL 0364-complat: forbidden text in the added lines: %s" % name)
            return 1
        cases += 1
    mutants = [
        ("p50 rank rounds down", func.replace("((uint64_t)h->n * num + den - 1) / den", "((uint64_t)h->n * num) / den")),
        ("rank 0 allowed", func.replace("if (rank == 0) {\n\t\trank = 1;\n\t}", "")),
        ("saturated bin reports its index", func.replace("return b == CL_BINS - 1 ? h->maxv : b;", "return b;")),
        ("sat not counted", func.replace("h->sat++;", "")),
        ("max not tracked", func.replace("h->maxv = v;", "")),
        ("32-bit clip removed", func.replace("us >= 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)us", "(uint32_t)us")),
        ("bin edge off by one", func.replace("v >= CL_BINS - 1 ? CL_BINS - 1 : v", "v > CL_BINS ? CL_BINS - 1 : v")),
        ("n not counted", func.replace("h->n++;", "")),
        ("empty histogram not guarded", func.replace("if (h->n == 0 || den == 0) {\n\t\treturn 0;\n\t}", "")),
        ("nodone not counted", func.replace("s->nodone++;", "")),
        ("nodone not in drop_total", func.replace("s->nodone++;\n\t\t\ts->drop_total++;", "s->nodone++;")),
        ("noirq not counted", func.replace("s->noirq += njobs;", "")),
        ("noirq not in drop_total", func.replace("s->noirq += njobs;\n\t\ts->drop_total += njobs;", "s->noirq += njobs;")),
        ("neg not counted", func.replace("s->neg += njobs;", "")),
        ("negative interval sampled", func.replace("if (end_ns < isr_ns) {", "if (0) {")),
        ("over not counted", func.replace("s->over += njobs - CL_MAXJ;", "")),
        ("njobs not capped", func.replace("njobs = CL_MAXJ;", "")),
        ("one sample per batch instead of per job", func.replace("for (uint32_t j = 0; j < njobs; j++) {", "for (uint32_t j = 0; j < 1; j++) {")),
        ("ns not divided to us", func.replace("(end_ns - isr_ns) / 1000", "(end_ns - isr_ns)")),
        ("start sample from the end stamp", func.replace("cl_hist_add(&s->start, (start_ns - isr_ns) / 1000);", "cl_hist_add(&s->start, (end_ns - isr_ns) / 1000);")),
        ("start sampled without a start", func.replace("if (start_ns != 0) {", "if (1) {")),
        ("start before isr not a neg", func.replace("s->neg++;\n\t\t\ts->drop_total++;", "")),
        ("reset drops drop_total", func.replace("s->drop_total = d;", "")),
        ("reset keeps the histogram", func.replace("bzero(s, sizeof(*s));", "s->noirq = 0;")),
        ("CL_MAXJ 8 -> 16", func.replace("#define CL_MAXJ 8u", "#define CL_MAXJ 16u")),
    ]
    dead = 0
    for name, m in mutants:
        if m == func:
            print("RESULT: FAIL 0364-complat: mutant %s did not change the source" % name)
            return 1
        rc, _ = run(m)
        if rc == 0:
            print("RESULT: FAIL 0364-complat: mutant %s survived" % name)
            return 1
        dead += 1
    for name, rx in TEXT:
        m = re.sub(rx, "", src, count=1, flags=re.S)
        if m == src or re.search(rx, m, re.S):
            print("RESULT: FAIL 0364-complat: text mutant %s did not remove the hook" % name)
            return 1
        dead += 1
    print("RESULT: PASS 0364-complat cases=%d mutants=%d" % (cases, dead))
    return 0


import unittest  # the gate runs tests through unittest discover: wrap main() so the file counts as one test (it printed RESULT and exited before)


class RunMain(unittest.TestCase):
    def test_main_passes(self):
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
