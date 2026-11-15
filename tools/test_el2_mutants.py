"""Mutants of the --el2-trap host side (spec S/docs/specs/SPEC-EL2-PROXY-PROBE.md section 6 B2/B3/B3p): each mutant replaces EVERY occurrence of a pattern in a scratch copy of
the sources (never the repo: a fresh mkdtemp under $TMPDIR per run, removed at the end, or $PIXOS_EL2_MUT_DIR if set and kept; AP-86: the copy holds the test next to the mutated source;
AP-210: a fixed $HOME path is not writable in the gate container and collides between parallel runs) and must make its test FAIL.
`input` names the input that makes the mutant differ from the original (AP-169: a mutant that no test input separates from the original would survive for the wrong reason).
Run: python3 -I tools/test_el2_mutants.py"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = None  # set by setUpModule
SHIM = ("el2.c", "el2.h", "el2_abi.h", "el2h.c", "el2_host_test.c", "el2_print_host_test.c", "el2_handler_host_test.c", "console.h", "ramlog.h", "shim.ld")

# (id, file in the copy, regex, replacement, test, input that differs)
MUTANTS = [
    ("vtcr-sh0", "tools/boards.py", r"^_EL2_SH0_INNER = 3 ", "_EL2_SH0_INNER = 2 ", "py", "any board: VTCR 0x8002A05C != 0x8002B05C"),
    ("vtcr-tg0", "tools/boards.py", r"^_EL2_TG0_16K = 2 ", "_EL2_TG0_16K = 1 ", "py", "any board: VTCR0x8002705C"),
    ("vtcr-t0sz", "tools/boards.py", r"t0sz = 64 - ipa_bits", "t0sz = 64 - ipa_bits + 1", "py", "bluejay banks: T0SZ 29, 2^35 < top 0x980000000"),
    ("align-check", "tools/boards.py", r"if n <= 0 or b % blk or n % blk:", "if False:", "py", "dram_banks base 0x80001000 (not 32 MiB aligned)"),
    ("space-check", "tools/boards.py", r"if top > 1 << ipa_bits:", "if False:", "py", "bank 0x2000000000+0x2000000 (top above 2^36)"),
    ("parange-check", "tools/boards.py", r"parange != _EL2_PARANGE_40 or ", "", "py", "cpu_id_aa64mmfr0 0x101123 (PARange 3, no cite)"),
    ("device-xn", "tools/boards.py", r"desc\(_EL2_MT_S2_DEVICE_NGNRE, 0, 1, 0\)", "desc(_EL2_MT_S2_DEVICE_NGNRE, 0, 0, 0)", "py", "DESC_BLOCK_DEVICE bit 54"),
    ("t2-l3-idx", "tools/boards.py", r"\(gicd >> g\) & \(\(1 << idx_bits\) - 1\)", "(gicd >> (g + 1)) & ((1 << idx_bits) - 1)", "py", "gicd 0x10400004: index 128 != 256"),
    ("sas-width", "shim/el2.c", r"d\.width = 1u << \(\(esr >> 22\) & 3u\);", "d.width = 1u << ((esr >> 22) & 1u);", "c", "ESR with SAS 2 or 3 (ldr w / ldr x): width 1 instead of 4/8"),
    ("srt31", "shim/el2.c", r"d\.rt = \(esr >> 16\) & 0x1Fu;", "d.rt = (esr >> 16) & 0xFu;", "c", "only SRT >= 16: the SRT = 31 fixture (rt 15 != 31)"),
    ("fnv", "shim/el2.c", r"if \(!\(\(esr >> 10\) & 1u\)\)", "if (1)", "c", "ESR with FnV (bit 10) = 1: FAR page offset added"),
    ("ipa-shift", "shim/el2.c", r"\(hpfar & ~0xFull\) << 8", "(hpfar & ~0xFull) << 9", "c", "any HPFAR != 0"),
    ("ec-hvc-to-park", "shim/el2.c", r"return EL2_K_HVC;", "return EL2_K_PARK;", "c", "ESR EC 0x16 (0x5A000000)"),
    ("ec-type-to-gap", "shim/el2.c", r"return EL2_K_PARK;\t\t\t/\* EC 0x24/0x20 with a fault type other than 0x04 \*/", "return EL2_K_GAP_EC;", "c", "ESR 0x9381000C (permission fault type 0x0C)"),
    ("nisv-write", "shim/el2.c", r" \|\| d\.write\)", ")", "c", "ESR 0x93810007 | WnR on a trap target"),
    ("overflow-gap", "shim/el2.c", r"\t\tel2_gaps\+\+;\t\t\t\t/\* once, when the ring overflowed \*/", "", "c", "the 17th ring event: gaps 7 != 8"),
    ("bound-silenced", "shim/el2.c", r'con_str\("EL2 GAP", "bound"\);', "(void)0;", "p", "el2_bound = 1, empty ring (B3p bound fixture)"),
    ("print-nn-one-digit", "shim/el2.c", r"\t\*dst\+\+ = hex\[\(n >> 4\) & 0xF\];\n", "", "p", "any ring entry: `EL2 TRAP n=1 ipa` != `n=01`"),
    ("print-label", "shim/el2.c", r'"EL2 S2 ON hcr"', '"EL2 S2 ON hcx"', "p", "the normal run (row 2 regex)"),
    ("print-vttbr-32", "shim/el2.c", r'con_hex\("EL2 S2 ON vttbr", el2_rb\.vttbr\)', 'con_hex("EL2 S2 ON vttbr", (uint32_t)el2_rb.vttbr)', "p", "vttbr 0x881230000: only a value with a nonzero high half differs (vtcr 0x8002B05C fits 32 bits)"),
    ("print-fault-always", "shim/el2.c", r"if \(e->fault\) \{", "if (1) {", "p", "any served read: prints `EL2 GAP n=.. fault`, row 6 --reject 'EL2 GAP'"),
    ("print-vm-cleared", "shim/el2.c", r'con_str\(l, "vm cleared"\);', "(void)0;", "p", "ring entry with vm = 0"),
    ("print-no-result", "shim/el2.c", r'con_hex2\("EL2 RESULT", "traps=", el2_traps, "gaps=", el2_gaps\);', "(void)0;", "p", "the normal run (row 5)"),
    ("print-long-line", "shim/el2.c", r'con_str\("EL2 GAP:", "shim outside dram_banks"\);', 'con_str("EL2 GAP:", "shim outside dram_banks ................................................................................");', "p", "fallback 3: a 99-byte line > RAMLOG_LINE_MAX"),
    ("test-cap-detector", "shim/el2_print_host_test.c", r"return strlen\(s\) >= RAMLOG_LINE_MAX;", "(void)s; return 0;", "p", "a 96-byte line (the test's own detector)"),
    ("test-reject-row", "shim/el2_print_host_test.c", r"\{ \"6\", \"EL2 GAP\" \},", '{ "6", "EL2 RESULT" },', "p", "a normal run prints EL2 RESULT: a reject row that matches must fail"),
    # table builder and entry-8 handler (el2h.c, el2_handler_host_test.c: kind "h")
    ("h-table-type-bit", "shim/el2h.c", r"l2\[t2i\] = l3_t2 \| EL2_DESC_TABLE;", "l2[t2i] = l3_t2;", "h", "L2[8] must be a table descriptor (| 3)"),
    ("h-no-head-check", "shim/el2h.c", r"!range_in_bank\(c, head, end\) \|\|", "(head == 1 && end == 1) ||", "h", "_head below the bank (P-B5)"),
    ("h-sse-without-sf", "shim/el2h.c", r"if \(d\.sse && d\.sf\)", "if (d.sse)", "h", "ESR with SSE but not SF and a word with bit 31 set"),
    ("h-elr-not-advanced", "shim/el2h.c", r"f->elr \+= 4;", "f->elr += 0;", "h", "any served read: ELR + 4"),
    ("h-srt31-writes", "shim/el2h.c", r"if \(d\.rt < 31\)", "if (d.rt <= 31)", "h", "SRT 31: the unused frame word x[31] must stay"),
    ("h-fault-keeps-value", "shim/el2h.c", r"uint64_t v = r\.fault \? 0 : \(uint32_t\)r\.val;", "uint64_t v = (uint32_t)r.val;", "h", "el2_ld32 fault = 1 with a nonzero val (0xDEAD)"),
    ("h-bound-not-set", "shim/el2h.c", r"bound = 1;", "bound = 0;", "h", "the 64th served read: el2_bound must be 1"),
    ("h-class1-no-bound", "shim/el2h.c", r"\t\t\tbound = 1;\t\t\t\t/\* class 1", "\t\t\t(void)bound;\t\t\t\t/* class 1", "h", "class 1 (translation fault off target): el2_bound + kind 5 [review r1 F4]"),
    ("h-nisv-returns", "shim/el2h.c", r"kind == EL2_K_GAP_EC \|\| kind == EL2_K_NISV\) \{", "kind == EL2_K_GAP_EC) {", "h", "ISV = 0 on a trap target: VM must be cleared"),
    # hvc report (SPEC 3.4 (b) items 3-4)
    ("report-imm-removed", "shim/el2.c", r" && \(esr & 0xFFFFu\) == EL2_HVC_IMM_REPORT", "", "c", "ESR_HVC(0x5346) with x0 = the function id"),
    ("report-id-removed", "shim/el2.c", r" && x0 == EL2_REPORT_FN", " && (x0 | 1)", "c", "ESR_HVC(0x5345) with x0 = function id + 1"),
    ("report-ec-removed", "shim/el2.c", r"\(esr >> 26\) == EC_HVC64 && ", "", "c", "EC 0x17 / 0x24 with ISS 0x5345"),
    ("h-report-vm-constant", "shim/el2h.c", r"f->x\[1\] = el2_hcr_vm\(\) & 1u;", "f->x[1] = 1;", "h", "HCR_EL2.VM = 0 (the stage-2-dropped answer must say vm=0)"),
    ("h-report-counts-gap", "shim/el2h.c", r"\t\tel2_reports\+\+;\n", "\t\tel2_reports++;\n\t\tel2_gaps++;\n", "h", "any answered call: gaps must stay unchanged"),
    ("res-park-restored", "shim/el2.c", r"\treturn EL2_K_GAP_EC;\n#else", "\treturn EL2_K_PARK;\n#else", "r", "EL2_RESIDENT: ESR 0x9200000F (permission fault) must be GAP_EC, not PARK (review r1 F2)"),
    ("res-park-iabt-align", "shim/el2.c", r"\t\(void\)on_target;\n\treturn EL2_K_GAP_EC;", "\t(void)on_target;\n\tif (ec == EC_IABT_LOW && (esr & 0x3fu) == 0x21u)\n\t\treturn EL2_K_PARK;\n\treturn EL2_K_GAP_EC;", "r", "EL2_RESIDENT: every FSC of EC 0x20/0x24 must be GAP_EC (review r2 N1: IABT alignment parked)"),
    ("h-t1-not-a-target", "shim/el2h.c", r"return \(ipa >> EL2_GRANULE_SHIFT\) == \(el2_t1_pa >> EL2_GRANULE_SHIFT\) \|\| ", "return ", "h", "a read of the T1 page: it must be served, not a class-1 GAP"),
]


def build_tree(dst):
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.join(dst, "shim"))
    os.makedirs(os.path.join(dst, "tools"))
    for f in SHIM:
        shutil.copy(os.path.join(ROOT, "shim", f), os.path.join(dst, "shim", f))
    for f in ("boards.py", "test_boards_el2.py"):
        shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(dst, "tools", f))
    for d in ("soc", "device"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d), ignore=shutil.ignore_patterns("*.md", "*.bin", "*.txt"))


def run_test(dst, kind):
    """Returns (rc, output). py: tools/test_boards_el2.py; c: el2_host_test; p: el2_print_host_test; h: el2_handler_host_test (with the header generated from the copy's boards.py)."""
    env = dict(os.environ, TMPDIR=dst)
    shim = os.path.join(dst, "shim")
    if kind == "py":
        p = subprocess.run([sys.executable, "-I", os.path.join(dst, "tools", "test_boards_el2.py")], capture_output=True, text=True, cwd=dst, env=env)
        return p.returncode, p.stdout + p.stderr
    gcc = ["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-DEL2_TRAP", "-I."]
    if kind in ("c", "r"):	# r: the EL2_RESIDENT compile of el2_host_test
        cmd = gcc + (["-DEL2_RESIDENT"] if kind == "r" else []) + ["-o", "../el2_host_test", "el2.c", "el2_host_test.c"]
        exe = os.path.join(dst, "el2_host_test")
    else:  # "p" and "h" need the header generated from the copy's boards.py
        hdr = subprocess.run([sys.executable, "-I", os.path.join(dst, "tools", "boards.py"), "bluejay", os.path.join(dst, "board_config.h")], capture_output=True, text=True, cwd=dst)
        if hdr.returncode:
            return 99, hdr.stdout + hdr.stderr
        srcs = ["el2.c", "el2h.c", "el2_handler_host_test.c"] if kind == "h" else ["el2.c", "el2_print_host_test.c"]
        name = "el2_handler_host_test" if kind == "h" else "el2_print_host_test"
        cmd = gcc + ["-I" + dst, "-include", os.path.join(dst, "board_config.h"), "-o", "../" + name] + srcs
        exe = os.path.join(dst, name)
    c = subprocess.run(cmd, capture_output=True, text=True, cwd=shim, env=env)
    if c.returncode:
        return 98, "COMPILE FAILED: " + c.stderr
    p = subprocess.run([exe], capture_output=True, text=True, cwd=shim, env=env)
    return p.returncode, p.stdout + p.stderr


def setUpModule():
    global SCRATCH
    SCRATCH = os.environ.get("PIXOS_EL2_MUT_DIR") or tempfile.mkdtemp(prefix="el2-mut-")


def tearDownModule():
    if not os.environ.get("PIXOS_EL2_MUT_DIR"):
        shutil.rmtree(SCRATCH, ignore_errors=True)


class Mutants(unittest.TestCase):
    def test_baseline_passes(self):
        for kind in ("py", "c", "r", "p", "h"):
            dst = os.path.join(SCRATCH, "baseline-" + kind)
            build_tree(dst)
            rc, out = run_test(dst, kind)
            self.assertEqual(rc, 0, f"baseline {kind}: {out}")

    def test_every_mutant_is_killed(self):
        seen = set()
        for mid, path, pat, rep, kind, why in MUTANTS:
            self.assertNotIn(mid, seen)
            seen.add(mid)
            dst = os.path.join(SCRATCH, mid)
            build_tree(dst)
            fp = os.path.join(dst, path)
            with open(fp) as f:
                src = f.read()
            new, n = re.subn(pat, lambda _m, r=rep: r, src, flags=re.M)	# every occurrence
            self.assertGreater(n, 0, f"{mid}: pattern matched nothing (nomatch)")
            self.assertNotEqual(new, src, f"{mid}: replacement is a no-op")
            with open(fp, "w") as f:
                f.write(new)
            rc, out = run_test(dst, kind)
            # a harness crash (compile failure, build error) is not a kill: only the test's own failure exit (1) is
            self.assertEqual(rc, 1, f"{mid} SURVIVED or crashed (rc {rc}) [differs on: {why}]: {out[-400:]}")
            self.assertRegex(out, r"FAIL|FAILED \(failures=", f"{mid}: exit 1 but no test failure line (harness crash?): {out[-400:]}")
            print(f"mutant {mid}: FAIL as required (differs on: {why})")


if __name__ == "__main__":
    unittest.main()
