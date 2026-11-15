"""CR-02 for xnu patch 0167 (G1, docs/research/83): every guard of bsd/dev/sweet_wlan_dma.h is removed or bent in a private copy (ALL occurrences, python -B), shim/wlan_dma_host_test.c is built
against the copy and must FAIL; a mutant that still passes is a survivor and fails this test unless it is listed with the clause that makes it equivalent. Also text audits of the G1 glue
(sweet_wlan_g1() in sweet_wlan.c after patches 0120..0137 + 0167): no bus access, plan before allocation, audit before flush, a swl_gap() on the line before every refusing printf (patch 0137 rule),
level 11 gate."""
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_wlan_wr_mutants as W  # noqa: E402  (apply_chain / new_file / strip_comments of the chip-writer test)
import wlan_mutant_pool  # noqa: E402  (AP-368: the mutant list runs on the GATE_MUT_JOBS pool)

PATCH0167 = os.path.join(ROOT, "xnu", "patches", "0167-sweet-wlan-g1-host-dma.patch")
TEST = os.path.join(ROOT, "shim", "wlan_dma_host_test.c")
HDR = "bsd/dev/sweet_wlan_dma.h"


def _edits_glue(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return re.search(r"^\+\+\+ b/bsd/dev/sweet_wlan\.c$", fh.read(), re.M) is not None


def glue_chain():
    ps = sorted(p for p in glob.glob(os.path.join(ROOT, "xnu", "patches", "0*.patch")) if os.path.basename(p)[:4] <= "0167" and _edits_glue(p))
    return W.apply_chain(W.GLUE_PATH, tuple(ps))


def header():
    return W.new_file(PATCH0167, HDR)


def build_and_run(hdr):
    """0 = the test passed, 1 = it failed, 2 = the copy does not compile (a mutant that breaks the build is not a kill)."""
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "sweet_wlan_dma.h"), "w", encoding="utf-8") as fh:
            fh.write(hdr)
        r = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d, "-o", os.path.join(d, "t"), TEST], capture_output=True, text=True)
        if r.returncode != 0:
            return 2
        return subprocess.run([os.path.join(d, "t")], capture_output=True, text=True, timeout=60).returncode


# (label, [(regex, replacement)], equivalent-clause or None). Every regex must match (a stale mutant is a failure) and is applied to ALL occurrences.
MUTANTS = [
    ("pad 128 -> 64", [(r"#define SWD_PAD\s+128u", "#define SWD_PAD         64u")], None),
    ("page 16384 -> 4096", [(r"#define SWD_PAGE\s+16384u", "#define SWD_PAGE        4096u")], None),
    ("pad rounds down", [(r"return \(n \+ SWD_PAD - 1u\) / SWD_PAD \* SWD_PAD;", "return n / SWD_PAD * SWD_PAD;")], None),
    ("pad adds a whole pad", [(r"return \(n \+ SWD_PAD - 1u\) / SWD_PAD \* SWD_PAD;", "return (n + SWD_PAD) / SWD_PAD * SWD_PAD;")], None),
    ("rw_index_sz 1 accepted", [(r"rw_index_sz != 2u && rw_index_sz != 4u", "rw_index_sz != 2u && rw_index_sz != 4u && rw_index_sz != 1u")], None),
    ("rw_index_sz 8 accepted", [(r"rw_index_sz != 2u && rw_index_sz != 4u", "rw_index_sz != 2u && rw_index_sz != 4u && rw_index_sz != 8u")], None),
    ("rw_index_sz 2 refused", [(r"rw_index_sz != 2u && rw_index_sz != 4u", "rw_index_sz != 4u")], None),
    ("rw_index_sz 0 accepted", [(r"if \(rw_index_sz != 2u && rw_index_sz != 4u\) \{\n\t\treturn -1;\n\t\}", "")], None),
    ("max_sub 0 accepted", [(r"max_sub == 0 \|\| ", "")], None),
    ("max_cpl 0 accepted", [(r"max_cpl == 0 \|\| ", "")], None),
    ("max_sub bound off by one", [(r"max_sub > SWD_MAX_RINGS", "max_sub >= SWD_MAX_RINGS")], None),
    ("max_cpl bound off by one", [(r"max_cpl > SWD_MAX_RINGS", "max_cpl >= SWD_MAX_RINGS")], None),
    ("max_sub unbounded", [(r" \|\| max_sub > SWD_MAX_RINGS", "")], None),
    ("max_cpl unbounded", [(r" \|\| max_cpl > SWD_MAX_RINGS", "")], None),
    ("NULL plan accepted", [(r"if \(!p\) \{\n\t\treturn -3;\n\t\}", "")], "a NULL plan then faults in the first store; the host test has no way to observe that without crashing, the check is the -3 line it does call"),
    ("h2d arrays use max_cpl", [(r"\(i == SWD_IDX_H2D_W \|\| i == SWD_IDX_H2D_R\) \? max_sub : max_cpl", "(i == SWD_IDX_H2D_W || i == SWD_IDX_H2D_R) ? max_cpl : max_sub")], None),
    ("h2d rd array uses max_cpl", [(r"\(i == SWD_IDX_H2D_W \|\| i == SWD_IDX_H2D_R\)", "(i == SWD_IDX_H2D_W)")], None),
    ("index size ignored", [(r"swd_pad\(rw_index_sz \* ", "swd_pad(4u * ")], None),
    ("offsets restart", [(r"p->b\[i\]\.off = off;", "p->b[i].off = 0;")], None),
    ("total not summed", [(r"p->total = off;", "p->total = off - p->b[SWD_NBUF - 1].len;")], None),
    ("scratch item 8 -> 4", [(r'\{ "scratch", 1, 8 \}', '{ "scratch", 1, 4 }')], "4 B pads to the same 128 B; the 8 is cited at G:140 and is not observable through padded lengths"),
    ("ctrl submit depth 128 -> 129", [(r"#define SWD_R0_ITEMS\s+128u", "#define SWD_R0_ITEMS    129u")], None),
    ("rxpost item 32 -> 16", [(r'\{ "h2d rxpost", SWD_R1_ITEMS, 32 \}', '{ "h2d rxpost", SWD_R1_ITEMS, 16 }')], None),
    ("ctrl complete item 24 -> 16", [(r'\{ "d2h ctrl complete", SWD_R2_ITEMS, 24 \}', '{ "d2h ctrl complete", SWD_R2_ITEMS, 16 }')], None),
    ("tx complete depth 2048 -> 1024", [(r"#define SWD_R3_ITEMS\s+2048u", "#define SWD_R3_ITEMS    1024u")], None),
    ("rx complete depth 1024 -> 512", [(r"#define SWD_R4_ITEMS\s+1024u", "#define SWD_R4_ITEMS    512u")], None),
    ("rx complete item 40 -> 32", [(r'\{ "d2h rx complete", SWD_R4_ITEMS, 40 \}', '{ "d2h rx complete", SWD_R4_ITEMS, 32 }')], None),
    ("trap len 4096 -> 2048", [(r"#define SWD_TRAP_LEN\s+4096u", "#define SWD_TRAP_LEN    2048u")], None),
    ("trap offset not aligned up", [(r"off = \(off \+ SWD_TRAP_LEN - 1u\) / SWD_TRAP_LEN \* SWD_TRAP_LEN;", "(void)off;")], None),
    ("trap offset rounds down", [(r"off = \(off \+ SWD_TRAP_LEN - 1u\) / SWD_TRAP_LEN \* SWD_TRAP_LEN;", "off = off / SWD_TRAP_LEN * SWD_TRAP_LEN;")], None),
    ("trap audited with the 128 B alignment", [(r"i == SWD_TRAP \? SWD_TRAP_LEN : i == SWD_EVBUF \? SWD_EVBUF_ALIGN : \(i == SWD_RESPBUF \|\| i == SWD_IOCTBUF\) \? SWD_D4D_ALIGN : SWD_PAD", "i == SWD_EVBUF ? SWD_EVBUF_ALIGN : (i == SWD_RESPBUF || i == SWD_IOCTBUF) ? SWD_D4D_ALIGN : SWD_PAD")], None),
    ("evbuf audited with the 128 B alignment (W-D4b D-10)", [(r"i == SWD_TRAP \? SWD_TRAP_LEN : i == SWD_EVBUF \? SWD_EVBUF_ALIGN : \(i == SWD_RESPBUF \|\| i == SWD_IOCTBUF\) \? SWD_D4D_ALIGN : SWD_PAD", "i == SWD_TRAP ? SWD_TRAP_LEN : (i == SWD_RESPBUF || i == SWD_IOCTBUF) ? SWD_D4D_ALIGN : SWD_PAD")], None),
    ("evbuf len 8192 -> 4096 (W-D4b D-10)", [(r"#define SWD_EVBUF_LEN\s+8192u", "#define SWD_EVBUF_LEN   4096u")], None),
    ("evbuf offset not aligned up (W-D4b D-10)", [(r"off = \(off \+ SWD_EVBUF_ALIGN - 1u\) / SWD_EVBUF_ALIGN \* SWD_EVBUF_ALIGN;", "(void)off;")], "equivalent: the trap page is 4096 B at a 4096 aligned offset, so its end (the event buffer start) is always 4 KiB aligned; the round-up is defence in depth"),
    ("evbuf offset rounds down (W-D4b D-10)", [(r"off = \(off \+ SWD_EVBUF_ALIGN - 1u\) / SWD_EVBUF_ALIGN \* SWD_EVBUF_ALIGN;", "off = off / SWD_EVBUF_ALIGN * SWD_EVBUF_ALIGN;")], "equivalent: the trap page is 4096 B at a 4096 aligned offset, so its end (the event buffer start) is always 4 KiB aligned; the round-up is defence in depth"),
    ("evbuf dropped from the total (W-D4b D-10)", [(r"(\tp->total = off;)", "\tp->total = off - (p->b[SWD_EVBUF].len);")], None),
    ("respbuf/ioctbuf audited with the 128 B alignment (W-D4d)", [(r"\(i == SWD_RESPBUF \|\| i == SWD_IOCTBUF\) \? SWD_D4D_ALIGN : SWD_PAD", "SWD_PAD")], None),
    ("evbuf count 8 -> 1 (W-D4e)", [(r"#define SWD_EVBUF_N\s+8u", "#define SWD_EVBUF_N     1u")], None),
    ("respbuf count 8 -> 1 (W-D4e)", [(r"#define SWD_RESPBUF_N\s+8u", "#define SWD_RESPBUF_N   1u")], None),
    ("evbuf plan len is one buffer (W-D4e)", [(r"len = SWD_EVBUF_N \* SWD_EVBUF_LEN;", "len = SWD_EVBUF_LEN;")], None),
    ("respbuf plan len is one buffer (W-D4e)", [(r"SWD_RESPBUF_N \* SWD_RESPBUF_LEN :", "SWD_RESPBUF_LEN :")], None),
    ("respbuf len 8192 -> 4096 (W-D4d)", [(r"#define SWD_RESPBUF_LEN\s+8192u", "#define SWD_RESPBUF_LEN 4096u")], None),
    ("ioctbuf len 8200 -> 8192 (W-D4d)", [(r"#define SWD_IOCTBUF_LEN\s+8200u", "#define SWD_IOCTBUF_LEN 8192u")], None),
    ("ioctbuf not padded (W-D4d)", [(r"swd_pad\(SWD_IOCTBUF_LEN\)", "SWD_IOCTBUF_LEN")], None),
    ("respbuf/ioctbuf alignment 4096 -> 128 (W-D4d)", [(r"#define SWD_D4D_ALIGN\s+4096u", "#define SWD_D4D_ALIGN  128u")], None),
    ("respbuf offset not aligned up (W-D4d)", [(r"off = \(off \+ SWD_D4D_ALIGN - 1u\) / SWD_D4D_ALIGN \* SWD_D4D_ALIGN;", "(void)off;")], "equivalent: the event buffer is 8192 B at a 4096 aligned offset, so its end is always 4 KiB aligned; the round-up is defence in depth"),
    ("respbuf offset rounds down (W-D4d)", [(r"off = \(off \+ SWD_D4D_ALIGN - 1u\) / SWD_D4D_ALIGN \* SWD_D4D_ALIGN;", "off = off / SWD_D4D_ALIGN * SWD_D4D_ALIGN;")], "equivalent: as above"),
    ("respbuf dropped from the total (W-D4d)", [(r"(\tp->total = off;)", "\tp->total = off - (p->b[SWD_RESPBUF].len);")], None),
    ("ioctbuf dropped from the total (W-D4d)", [(r"(\tp->total = off;)", "\tp->total = off - (p->b[SWD_IOCTBUF].len);")], None),
    ("respbuf and ioctbuf swapped (W-D4d)", [(r'p->b\[i\].name = i == SWD_RESPBUF \? "respbuf" : "ioctbuf";', 'p->b[i].name = i == SWD_RESPBUF ? "ioctbuf" : "respbuf";')], None),
    ("trap page dropped from the plan", [(r"(\tp->total = off;)", "\tp->total = off - (p->b[SWD_TRAP].len);")], None),
    ("audit: pa 0 accepted", [(r"if \(pa == 0\) \{\n\t\treturn 1;\n\t\}", "")], None),
    ("audit: alignment not checked", [(r"if \(\(pa & \(uint64_t\)\(align - 1u\)\) != 0\) \{\n\t\treturn 2;\n\t\}", "(void)align;")], None),
    ("audit: alignment mask off by one", [(r"align - 1u", "align - 2u")], None),
    ("audit: only the low word is tested for zero", [(r"if \(pa == 0\)", "if (lo == 0)")], None),
    ("audit: len 0 accepted", [(r"len == 0 \|\| ", "")], None),
    ("audit: len multiple not checked", [(r" \|\| len % SWD_PAD != 0", "")], None),
    ("audit: wrap not checked", [(r"if \(\(uint32_t\)\(lo \+ len\) < lo\) \{\n\t\treturn 4;\n\t\}", "(void)lo;")], None),
    ("audit: wrap test on the 64-bit end", [(r"\(uint32_t\)\(lo \+ len\) < lo", "(pa + len) < pa + 0 * lo")], None),
    ("audit: end at exactly 4 GiB accepted", [(r"\(uint32_t\)\(lo \+ len\) < lo", "(uint32_t)(lo + len) < lo && (uint32_t)(lo + len) != 0")], None),
    ("audit: wrap test reversed", [(r"\(uint32_t\)\(lo \+ len\) < lo", "(uint32_t)(lo + len) > lo")], None),
    ("contig: always contiguous", [(r"return pa_last == pa_first \+ sz - page \? 0 : 1;", "return 0 * (int)(pa_first + pa_last + sz + page);")], None),
    ("contig: last page off by one page", [(r"pa_first \+ sz - page", "pa_first + sz")], None),
    ("contig: size not a whole number of pages accepted", [(r" \|\| sz % page != 0", "")], None),
    ("contig: size 0 accepted", [(r"sz == 0 \|\| ", "")], None),
    ("all: sub-buffers not audited", [(r"rc == 0 && i < SWD_NBUF", "rc == 0 && i < SWD_NBUF && p->total == 0")], None),
    ("all: sub-buffer offset ignored", [(r"swd_audit\(pa \+ p->b\[i\]\.off,", "swd_audit(pa,")], None),
    ("all: plan may exceed the block", [(r"if \(rc == 0 && p->total > sz\) \{\n\t\trc = 3;[^\n]*\n\t\}", "")], None),
    ("all: block not audited", [(r"int rc = swd_audit\(pa, sz, SWD_PAGE\);", "int rc = 0;")], None),
    ("all: block audited with sub-buffer alignment", [(r"swd_audit\(pa, sz, SWD_PAGE\)", "swd_audit(pa, sz, SWD_PAD)")], None),
    ("audit: pa rule order (len before pa)", [(r"if \(pa == 0\) \{\n\t\treturn 1;\n\t\}\n(.*?)\n\tif \(len == 0 \|\| len % SWD_PAD != 0\) \{\n\t\treturn 3;\n\t\}", r"if (len == 0 || len % SWD_PAD != 0) {\n\t\treturn 3;\n\t}\n\tif (pa == 0) {\n\t\treturn 1;\n\t}\n\1")], None),
]


class Mutants(unittest.TestCase):
    def test_unmutated_header_passes(self):
        self.assertEqual(build_and_run(header()), 0)

    def test_every_mutant_is_killed(self):
        hdr = header()

        def run_one(label, subs):
            m = hdr
            for rx, rep in subs:
                if not re.findall(rx, m, re.S):
                    return "nomatch", "STALE (pattern matches nothing)"
                m = re.sub(rx, rep, m, flags=re.S)
            st = build_and_run(m)       # 0 pass, 2 does not compile, anything else (1, or a signal such as -11) = the test failed = killed, as before
            return ("pass" if st == 0 else "compile" if st == 2 else "fail"), str(st)

        with open(TEST, encoding="utf-8") as fh:
            test_c = fh.read()
        res = wlan_mutant_pool.run_set("dma", MUTANTS, run_one, [hdr, test_c, "-O1 -Wall -Wextra -Werror"])      # AP-368: pooled and cached like the wr sets; None = skipped by GATE_MUT_ONLY
        if res is None:
            return
        bad = []
        for (label, _subs, equiv), (st, msg) in zip(MUTANTS, res):
            if st == "nomatch":
                bad.append("%s: %s" % (label, msg))
            elif st == "pass" and equiv is None:
                bad.append("%s: SURVIVED" % label)
            elif st == "compile":
                bad.append("%s: does not compile" % label)
            elif st == "fail" and msg == "1" and equiv is not None:     # only an exit status 1 counts, as before (a signal is killed too)
                bad.append("%s: listed equivalent but killed (remove the equivalence note)" % label)
            elif st not in ("pass", "fail"):
                bad.append("%s: %s %s" % (label, st, msg))
        self.assertEqual(bad, [], "\n".join(bad))


class Glue(unittest.TestCase):
    def setUp(self):
        self.glue = glue_chain()
        m = re.search(r"^sweet_wlan_g1\(.*?^}$", self.glue, re.S | re.M)
        self.assertIsNotNone(m, "sweet_wlan_g1 missing from sweet_wlan.c")
        self.g1 = m.group(0)
        self.code = W.strip_comments(self.g1)

    def test_no_bus_access(self):
        self.assertIsNone(re.search(r"\b(sw3_\w+|sw_rd|sw_map|swl_store|swl_wr|swl_stage\w+|ml_io_map)\b|(?:\.|->)\s*(?:wr|rd)\b|\bvolatile\b|__asm__|\bmemcpy\b|\bbcopy\b", self.code))

    def test_order_plan_alloc_audit_flush_print(self):
        c = self.code
        pos = [c.find(k) for k in ("swd_plan_make(", "kmem_alloc_contig(", "pmap_find_phys(", "swd_contig(", "swd_audit_all(pa, (uint32_t)sz", "flush_dcache64(", 'printf("SWEET WLAN: G1 %s', 'printf("SWEET WLAN: G1 done')]
        self.assertNotIn(-1, pos)
        self.assertEqual(pos, sorted(pos))

    def test_page_size_assert(self):
        self.assertIn("_Static_assert(SWD_PAGE == PAGE_SIZE", self.glue)

    def test_alloc_flags(self):
        self.assertIn("KMA_DATA | KMA_ZERO | KMA_PERMANENT", self.code)
        self.assertIn("PAGE_MASK", self.code)

    def test_every_refusal_printf_has_a_gap_call(self):
        rx = re.compile(r'printf\("SWEET WLAN: G1 [^"]*(refused|failed|no contiguous|no physical)')
        L = self.g1.split("\n")
        hits = [i for i, ln in enumerate(L) if rx.search(ln)]
        self.assertEqual(len(hits), 5)  # plan refused, no memory, no PA, not contiguous, audit failed
        for i in hits:
            self.assertIn("swl_gap(", L[i - 1], L[i])
        self.assertEqual(sum(1 for ln in L if "swl_gap(" in ln), 5)  # the five refusals
        self.assertEqual(sum(1 for ln in L if 'swl_miss("g1", SWL_M_G1, "low")' in ln), 1)  # the pinned-inputs gap is a deferred miss (W-D4b gaps): G2 verifies the pins and resolves it, else swl_gap_summary() prints it

    def test_level_gate(self):
        init = re.search(r"^sweet_wlan_init\(.*?^}$", self.glue, re.S | re.M).group(0)
        self.assertIn("level > 11 ||", init)
        self.assertRegex(init, r"sweet_wlan_stage3\(&hw, level > 10 \? 10 : \(int\)level\);\n\t\t\tif \(level >= 11\) \{\n\t\t\t\tsweet_wlan_g1\(\);\n")
        self.assertEqual(self.glue.count("sweet_wlan_g1()"), 1)  # called once (the definition is "sweet_wlan_g1(void)")

    def test_pinned_inputs_equal_run_f3(self):
        h = header()
        self.assertRegex(h, r"#define SWD_RW_INDEX_SZ 4u")
        self.assertRegex(h, r"#define SWD_MAX_SUB\s+43u")
        self.assertRegex(h, r"#define SWD_MAX_CPL\s+4u")


if __name__ == "__main__":
    unittest.main()
