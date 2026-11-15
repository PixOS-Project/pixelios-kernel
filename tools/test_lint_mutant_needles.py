"""tools/lint-mutant-needles (gate step mutant-needles, AP-281/277): the real tables pass, and a table with a bad needle fails (negative tests).
Run: python3 -I tools/test_lint_mutant_needles.py"""
import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent
LINT = TOOLS / "lint-mutant-needles"
loader = importlib.machinery.SourceFileLoader("lmn", str(LINT))
spec = importlib.util.spec_from_loader("lmn", loader)
lmn = importlib.util.module_from_spec(spec)
loader.exec_module(lmn)

EL2_FILES = ("tools/boards.py", "shim/el2.c", "shim/el2h.c", "shim/el2_print_host_test.c")


def run(*args):
    p = subprocess.run([sys.executable, "-I", str(LINT), *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def el2_tree(d):
    """A scratch repo shaped like the real one for test_el2_mutants.py (ROOT = dirname(dirname(file)))."""
    d = pathlib.Path(d)
    (d / "tools").mkdir()
    (d / "shim").mkdir()
    shutil.copy(TOOLS / "test_el2_mutants.py", d / "tools")
    for f in EL2_FILES:
        shutil.copy(TOOLS.parent / f, d / f)
    return d


class Presence(unittest.TestCase):
    def test_real_tables_pass_in_seconds(self):
        rc, out = run()
        self.assertEqual(rc, 0, out[-600:])
        last = out.strip().splitlines()[-1]
        self.assertRegex(last, r"^RESULT: PASS mutant-needles tables=31 needles=\d+ missing=0 multi=\d+$")
        self.assertNotIn("MUTANT NEEDLE MISSING", out)

    def test_el2_bad_needle_fails_naming_the_mutant(self):
        """AP-281: an edit to a line a mutant anchors on (here shim/el2h.c `f->elr += 4;`) makes the mutant's needle vanish."""
        with tempfile.TemporaryDirectory() as t:
            d = el2_tree(t)
            rc, out = run("--tools", str(d / "tools"))
            self.assertEqual(rc, 0, out[-400:])          # the copy is clean first: the failure below is the edit's
            p = d / "shim" / "el2h.c"
            src = p.read_text()
            self.assertIn("f->elr += 4;", src)
            p.write_text(src.replace("f->elr += 4;", "f->elr += 4u;"))
            rc, out = run("--tools", str(d / "tools"))
            self.assertEqual(rc, 1, out[-400:])
            self.assertRegex(out, r"MUTANT NEEDLE MISSING test_el2_mutants\.py:MUTANTS 'h-elr-not-advanced' .*shim/el2h\.c")
            self.assertRegex(out.strip().splitlines()[-1], r"^RESULT: FAIL mutant-needles .* missing=1 ")

    def test_el2_missing_target_file_fails(self):
        with tempfile.TemporaryDirectory() as t:
            d = el2_tree(t)
            (d / "shim" / "el2h.c").unlink()
            rc, out = run("--tools", str(d / "tools"))
            self.assertEqual(rc, 1)
            self.assertIn("MUTANT NEEDLE MISSING", out)
            self.assertIn("file unreadable", out)

    def test_wlan_bad_needle_fails(self):
        mod = lmn.load(str(TOOLS / "test_wlan_wr_mutants.py"))
        mod.MUTANTS.append(("bad needle", [("THIS TEXT IS NOT IN sweet_wlan_wr.h", "x")], None))
        try:
            bad = [r for r in lmn.wlan_rows(mod) if r[3] == 0]
        finally:
            mod.MUTANTS.pop()
        self.assertEqual([(r[1], r[2]) for r in bad], [("bad needle", "THIS TEXT IS NOT IN sweet_wlan_wr.h")])

    def test_gate_wiring_bad_needle_fails_and_good_ones_count_once(self):   # K1 review F2: a stale gate.run call-site needle must fail the needles step, not the unit test only
        mod = lmn.load(str(TOOLS / "test_wlan_mutants_cache.py"))
        rows = list(lmn.wlan_wiring_rows(mod))
        self.assertEqual(len(rows), len(mod.GATE_WIRING_MUTANTS))
        self.assertEqual([r[3] for r in rows], [1] * len(rows))
        mod.GATE_WIRING_MUTANTS.append(("bad needle", "THIS TEXT IS NOT IN test_wlan_wr_mutants.py", "x", "cold"))
        try:
            bad = [r for r in lmn.wlan_wiring_rows(mod) if r[3] != 1]
        finally:
            mod.GATE_WIRING_MUTANTS.pop()
        self.assertEqual([r[1] for r in bad], ["bad needle"])

    def test_gate_wiring_table_is_registered(self):
        self.assertIn(("test_wlan_mutants_cache.py", "GATE_WIRING_MUTANTS"), lmn.ADAPTERS)

    def test_devd_tables_are_registered_and_unique(self):   # T-c24f gate FAIL 483b6e2d: test_devd_mutants.py tables were unregistered
        mod = lmn.load(str(TOOLS / "test_devd_mutants.py"))
        for tname, fn in (("MUTANTS", lmn.devd_rows), ("DEVSH_MUTANTS", lmn.devsh_rows)):
            self.assertIs(lmn.ADAPTERS[("test_devd_mutants.py", tname)], fn)
            rows = list(fn(mod))
            self.assertGreaterEqual(len(rows), len(getattr(mod, tname)))
            self.assertEqual([r for r in rows if r[3] != 1], [])   # apply_mutant() needs exactly one match

    def test_devd_bad_needle_fails_and_edits_apply_in_order(self):
        mod = lmn.load(str(TOOLS / "test_devd_mutants.py"))
        anchor = mod.MUTANTS[0][2][0][0]
        mod.MUTANTS.append(("ZZ_BAD", "x", [("NOT IN devd.c", "y")], ["k"]))
        mod.MUTANTS.append(("ZZ_SEQ", "x", [(anchor, "ZZ_MARK"), ("ZZ_MARK", "z")], ["k"]))   # 2nd anchor exists only after the 1st edit
        try:
            rows = list(lmn.devd_rows(mod))
        finally:
            del mod.MUTANTS[-2:]
        self.assertEqual([(r[1], r[3]) for r in rows if r[1] == "ZZ_BAD"], [("ZZ_BAD", 0)])
        self.assertEqual([r[3] for r in rows if r[1] == "ZZ_SEQ"], [1, 1])

    def test_wlan_glue_bad_needle_fails(self):
        mod = lmn.load(str(TOOLS / "test_wlan_wr_mutants.py"))
        mod.GLUE_MUTANTS.append(("bad glue needle", [("NOT IN THE GLUE", "x")]))
        try:
            bad = [r for r in lmn.wlan_glue_rows(mod) if r[3] == 0]
        finally:
            mod.GLUE_MUTANTS.pop()
        self.assertEqual([r[1] for r in bad], ["bad glue needle"])

    def test_unregistered_table_fails(self):
        """Rule 8: a new mutant table nobody taught this lint about is a FAIL, not a silent skip."""
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_zz_mutants.py").write_text("MUTANTS = [('x', 'f', 'a', 'b')]\n")
            rc, out = run("--tools", t)
            self.assertEqual(rc, 1)
            self.assertIn("MUTANT TABLE UNREGISTERED test_zz_mutants.py:MUTANTS", out)

    def test_import_error_in_a_table_module_fails(self):
        """A table helper that raises at import (wlan `_t`/`_f` when its text is gone) is the same defect: loud FAIL."""
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_zz_mutants.py").write_text("raise ValueError('needle gone')\n")
            rc, out = run("--tools", t)
            self.assertEqual(rc, 1)
            self.assertIn("MUTANT NEEDLE MISSING test_zz_mutants.py: import failed", out)

    def test_no_table_at_all_fails(self):
        with tempfile.TemporaryDirectory() as t:
            rc, out = run("--tools", t)
            self.assertEqual(rc, 1)
            self.assertIn("no mutant table found", out)

    def test_once_makes_a_repeated_needle_fail(self):
        rc, out = run("--once")
        self.assertEqual(rc, 1)
        self.assertIn("MULTI-FAIL", out)

    def test_usage(self):
        rc, _ = run("--bogus")
        self.assertEqual(rc, 2)


class Strength(unittest.TestCase):
    """r42-gate-r1 F8: rows that kill the five mutants of tools/lint-mutant-needles the first review found alive."""

    def test_a_needle_found_exactly_twice_is_a_multi_note_not_silent(self):          # kills `elif n > 1` -> `n > 2`
        self.assertEqual(self.check({("t_mutants.py", "MUTANTS"): lambda m: [("f", "l", "n", 2)]})[3], 1)
        self.assertEqual(self.check({("t_mutants.py", "MUTANTS"): lambda m: [("f", "l", "n", 1)]})[3], 0)

    def check(self, adapters, once=False):
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_t_mutants.py").write_text("MUTANTS = [1]\n")
            adapters = {("test_t_mutants.py", k[1]): v for k, v in adapters.items()}
            return lmn.check(t, None, once, out=lambda *_: None, adapters=adapters)

    def test_a_problem_next_to_a_good_table_fails_the_run(self):                      # kills `ok = not (missing or problems) and ...` -> `not missing and ...`
        with tempfile.TemporaryDirectory() as t:
            d = el2_tree(t)
            (d / "tools" / "test_zz_mutants.py").write_text("MUTANTS = [('x', 'f', 'a', 'b')]\n")   # unregistered: problems=1, the el2 table is fine
            rc, out = run("--tools", str(d / "tools"))
            self.assertEqual(rc, 1, out[-500:])
            self.assertRegex(out.strip().splitlines()[-1], r"^RESULT: FAIL mutant-needles tables=1 needles=\d+ missing=0 multi=\d+ problems=1$")

    def test_each_problem_site_counts(self):                                          # kills `problems += 1` -> `pass` at each of the three sites
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_a_mutants.py").write_text("MUTANTS = [1]\n")                       # unregistered
            (pathlib.Path(t) / "test_b_mutants.py").write_text("raise ValueError('needle gone')\n")      # import failure
            (pathlib.Path(t) / "test_c_mutants.py").write_text("MUTANTS = [1]\n")                       # adapter failure
            def boom(_mod):
                raise RuntimeError("adapter broke")
            lines = []
            res = lmn.check(t, None, False, out=lines.append, adapters={("test_c_mutants.py", "MUTANTS"): boom})
        self.assertEqual(res, (1, 0, 0, 0, 3), lines)   # tables=1: the third table was counted before its adapter broke
        self.assertEqual(sum("MUTANT NEEDLE MISSING" in x or "UNREGISTERED" in x for x in lines), 3, lines)

    def test_glue_mutants_apply_sequentially(self):                                   # kills the glue adapter's `t = t.replace(old, new)` -> `pass`
        class Mod:
            _MOVE = "@move"
            GLUE_MUTANTS = [("chain", [("alpha", "beta"), ("beta", "gamma")])]       # the second needle exists only after the first replacement
            @staticmethod
            def glue_function_text():
                return "x alpha y"
        rows = list(lmn.wlan_glue_rows(Mod))
        self.assertEqual([(r[2], r[3]) for r in rows], [("alpha", 1), ("beta", 1)])
        class Mod2(Mod):
            GLUE_MUTANTS = [("chain", [("alpha", "beta"), ("beta", "gamma"), ("alpha", "z")])]   # the first replacement consumed every alpha
        self.assertEqual([(r[2], r[3]) for r in lmn.wlan_glue_rows(Mod2)], [("alpha", 1), ("beta", 1), ("alpha", 0)])

    def test_el2_patterns_are_multiline(self):                                         # test_el2_mutants.py:134 uses flags=re.M: `^`/`$` match per line
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "f.txt").write_text("a\nfoo\nb\nfoo\n")
            class Mod:
                ROOT = t
                MUTANTS = [("m1", "f.txt", r"^foo$", "x", "k", "w")]
            self.assertEqual([(r[0], r[3]) for r in lmn.el2_rows(Mod)], [("f.txt", 2)])

    def test_wlan_header_is_counted_once(self):                                       # kills `text = mod.final_header()` -> doubled text (every count x2)
        class Mod:
            MUTANTS = [("m1", [("needle", "x")], None)]
            @staticmethod
            def final_header():
                return "a needle b"
        self.assertEqual([(r[2], r[3]) for r in lmn.wlan_rows(Mod)], [("needle", 1)])


FIX_POOLED = """
import subprocess
import wlan_mutant_pool
MUTANTS = []
GLUE_MUTANTS = [("a", [("x", "y")])]
G2_MUTANTS = [("a", [("x", "y")])]
G2L_MUTANTS = [("a", [("x", "y")])]
D4B_MUTANTS = [("a", [("x", "y")])]
G2VA_MUTANTS = [("a", [("x", "y")])]
G2HOST_MUTANTS = [("a", [("x", "y")])]
G2FIX_MUTANTS = [("a", [("x", "y")])]
MUTANTS += []
def run_g2_list(t):
    return subprocess.run(["gcc", "-o", "t", t])
class T:
    def go(self):
        for name, subs in G2_MUTANTS:          # builds the text only: no compiler
            t = name + str(subs)
        wlan_mutant_pool.run_set("glue", GLUE_MUTANTS, run_g2_list, [])
        wlan_mutant_pool.run_set("g2", list(G2_MUTANTS), run_g2_list, [])
        wlan_mutant_pool.run_set("g2l", G2L_MUTANTS, run_g2_list, [])
        wlan_mutant_pool.run_set("d4b", D4B_MUTANTS, run_g2_list, [])
        wlan_mutant_pool.run_set("audit", [*G2VA_MUTANTS, *G2HOST_MUTANTS], run_g2_list, [], jobs=2)
        run_set("audit", mutants=G2FIX_MUTANTS, run_one=run_g2_list, key_parts=[])
"""


class Pooling(unittest.TestCase):
    """G4 (PLAN-GATE-2X): every *MUTANTS table goes through run_set; a serial loop that compiles outside it is `MUTANT SET UNPOOLED <table>`."""

    def probe(self, text, fname="test_wlan_wr_mutants.py"):
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / fname).write_text(text)
            lines = []
            n = lmn.pooling_problems(t, out=lines.append)
        return n, lines

    def test_pooled_tables_pass_with_the_d4d_names(self):
        n, lines = self.probe(FIX_POOLED)
        self.assertEqual((n, lines), (0, []))

    def test_serial_loop_with_a_direct_compiler_call_fails(self):
        n, lines = self.probe(FIX_POOLED + """
def g2_kill_test():
    for name, subs in G2_MUTANTS:
        c = subprocess.run(["gcc", "-O1", "-o", "t", "x.c"])
""")
        self.assertEqual(n, 1, lines)
        self.assertRegex(lines[0], r"^MUTANT SET UNPOOLED G2_MUTANTS \(test_wlan_wr_mutants\.py:\d+\): a serial loop")

    def test_serial_loop_through_a_helper_that_compiles_fails(self):
        n, lines = self.probe(FIX_POOLED + """
class U:
    def test_g2_list_mutants_are_killed(self):
        for name, subs in enumerate(G2L_MUTANTS):
            st, out = run_g2_list(subs)
""")
        self.assertEqual(n, 1, lines)
        self.assertIn("MUTANT SET UNPOOLED G2L_MUTANTS", lines[0])

    def test_serial_loop_with_a_cc_string_in_a_two_hop_helper_fails(self):
        n, lines = self.probe(FIX_POOLED + """
def build(t):
    return run_g2_list(t)
def d4b_kill_test():
    for name, subs in D4B_MUTANTS:
        build(subs)
""")
        self.assertEqual(n, 1, lines)
        self.assertIn("MUTANT SET UNPOOLED D4B_MUTANTS", lines[0])

    def test_table_never_given_to_run_set_fails(self):
        n, lines = self.probe("MUTANTS = []\nNEW_MUTANTS = [('a', [])]\n")
        self.assertEqual(n, 1, lines)
        self.assertIn("MUTANT SET UNPOOLED NEW_MUTANTS (test_wlan_wr_mutants.py:2): not an argument of run_set", lines[0])

    def test_header_table_is_allowed_only_with_its_reason(self):
        n, lines = self.probe("MUTANTS = []\n")
        self.assertEqual(n, 0, lines)
        lines = []
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_wlan_wr_mutants.py").write_text("MUTANTS = []\n")
            self.assertEqual(lmn.pooling_problems(t, out=lines.append, allow={}), 1)
        self.assertIn("MUTANT SET UNPOOLED MUTANTS", lines[0])

    def test_the_dma_test_file_is_checked_too_and_other_files_are_not(self):
        n, lines = self.probe("DMA_MUTANTS = [('a', [])]\n", "test_wlan_dma_mutants.py")
        self.assertEqual(n, 1, lines)
        n, lines = self.probe("DMA_MUTANTS = [('a', [])]\n", "test_other_mutants.py")
        self.assertEqual(n, 0, lines)

    def test_cli_fails_on_an_unpooled_fixture(self):
        with tempfile.TemporaryDirectory() as t:
            (pathlib.Path(t) / "test_wlan_wr_mutants.py").write_text("import subprocess\nMUTANTS = []\nX_MUTANTS = [('a', [])]\ndef f():\n    for n, s in X_MUTANTS:\n        subprocess.run(['gcc'])\n")
            rc, out = run("--tools", t)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("MUTANT SET UNPOOLED X_MUTANTS"), 2, out)       # never given to run_set + the serial loop
        self.assertRegex(out.strip().splitlines()[-1], r"^RESULT: FAIL ")

    # review gate-2x-s1-r1 F2: the three bypass fixtures (each returned 0 findings before), plus their neighbours
    def test_bypass_a_alias_and_comprehension_after_a_decoy_pooled_use_fail(self):
        n, lines = self.probe("""
import subprocess, wlan_mutant_pool
G2_MUTANTS = [("a", [("x", "y")])]
def t(): return wlan_mutant_pool.run_set("g2", G2_MUTANTS, lambda n, s: ("pass", ""), [])   # one pooled use, as a decoy
ALIAS = G2_MUTANTS
def serial_alias():
    for name, subs in ALIAS:
        subprocess.run(["gcc", "-o", "x", "x.c"])
def serial_comp():
    return [subprocess.run(["gcc", "x.c"]) for name, subs in G2_MUTANTS]
""")
        self.assertEqual(n, 2, lines)
        self.assertTrue(any("a serial loop over the table" in l for l in lines) and any("a comprehension over the table" in l for l in lines), lines)

    def test_bypass_b_muts_name_and_annotated_assignment_fail(self):
        n, lines = self.probe("""
import subprocess
G2_MUTS = [("a", [("x", "y")])]
G2L_MUTANTS: list = [("b", [("x", "y")])]
def serial():
    for name, subs in G2_MUTS:
        subprocess.run(["gcc", "x.c"])
    for name, subs in G2L_MUTANTS:
        subprocess.run(["gcc", "x.c"])
""")
        self.assertGreaterEqual(n, 2, lines)
        for t in ("G2_MUTS", "G2L_MUTANTS"):
            self.assertTrue(any(("UNPOOLED %s " % t) in l for l in lines), (t, lines))

    def test_bypass_c_local_run_set_is_not_the_pool(self):
        n, lines = self.probe("""
import subprocess
D4B_MUTANTS = [("a", [("x", "y")])]
def run_set(name, muts, run_one, key):          # local serial shim with the pooled name
    return [run_one(l, s) for l, s in muts]
def f():
    return run_set("d4b", D4B_MUTANTS, lambda l, s: subprocess.run(["gcc", "x.c"]), [])
""")
        self.assertGreaterEqual(n, 1, lines)
        self.assertTrue(any("defines its own `def run_set`" in l for l in lines), lines)
        n, lines = self.probe("import subprocess\nfrom elsewhere import run_set\nX_MUTANTS = [('a', [])]\ndef f():\n    return run_set('x', X_MUTANTS, lambda l, s: 0, [])\n")
        self.assertGreaterEqual(n, 1, lines)
        self.assertTrue(any("imports run_set from 'elsewhere'" in l for l in lines), lines)

    def test_alias_forms_that_are_pooled_or_do_not_compile_pass(self):
        n, lines = self.probe("""
import subprocess
from wlan_mutant_pool import run_set as pool_run
X_MUTANTS = [("a", [("x", "y")])]
ALL = list(X_MUTANTS)
def texts():
    for name, subs in ALL:
        t = name + str(subs)               # no compiler
    return pool_run("x", ALL, lambda l, s: subprocess.run(["gcc", "x.c"]), [])
""")
        self.assertEqual((n, lines), (0, []))

    def test_local_alias_in_a_function_and_a_helper_call_fail(self):
        n, lines = self.probe("""
import subprocess, wlan_mutant_pool
Y_MUTANTS = [("a", [("x", "y")])]
def build(subs):
    return subprocess.run(["gcc", "x.c"])
def f():
    T = sorted(Y_MUTANTS)
    for name, subs in T:
        build(subs)
    return wlan_mutant_pool.run_set("y", Y_MUTANTS, lambda l, s: ("pass", ""), [])
def g():
    return list(map(build, Y_MUTANTS))
""")
        self.assertEqual(n, 2, lines)

    def test_real_wlan_tests_are_pooled(self):
        lines = []
        self.assertEqual(lmn.pooling_problems(str(TOOLS), out=lines.append), 0, lines)


if __name__ == "__main__":
    unittest.main()
