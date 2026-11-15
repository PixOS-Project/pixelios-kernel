"""Mutant table of patch 0339 (tools/test_chg_mon_refused.py runs it; tools/lint-mutant-needles checks the needles).

One table, one block (`# mutant-block: chg-mon-refused`). Row (name, needle, replacement): the needle occurs exactly once in sweet_chg_i2c.c as patched by the stack
0284+0287+0298+0307+0314+0336+0339 (source() below builds that text); the mutated source is compiled into tools/chg_icl_host_test.c and a mr_ case (or the runaway guard) must report FAIL."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STACK_NAMES = ("0287-sweet-chg-icl", "0298-sweet-chg-mon", "0307-sweet-chg-soc", "0314-sweet-pmic-diag", "0336-sweet-thermal", "0339-sweet-chg-mon-after-refuse")

# mutant-block: chg-mon-refused
CHG_MON_REFUSED_MUTANTS = [
    ("refuse_never_enters_stage10", "\t\tp3_stage = 10;\n", "\t\t(void)0;\n"),
    ("tick_never_dispatches_stage10", "\tif (p3_stage == 10) {\n\t\tp3_mon_only_tick();", "\tif (0) {\n\t\tp3_mon_only_tick();"),
    ("no_gate_check", "if (i2c_dead || !i2c_gate(0)) {\n\t\t(void)i2c_wr(I_INTEN, 0);", "if (i2c_dead) {\n\t\t(void)i2c_wr(I_INTEN, 0);"),
    ("no_period_advance", "\tp3_next_ms += P3_SAMPLE_MS;\n}\n\n/* Write val to CNFG_09", "}\n\n/* Write val to CNFG_09"),
    ("first_row_one_period_late", "p3_next_ms = p3_t0_ms;          /* first row", "p3_next_ms = p3_t0_ms + P3_SAMPLE_MS;          /* first row"),
    ("no_stage9_after_failed_read", "\t\tp3_stage = 9;           /* mon_row logged the GAP", "\t\t(void)0;           /* mon_row logged the GAP"),
    ("stage10_on_dead_bus", "\tif (!i2c_dead) {\n\t\tp3_stage = 10;", "\tif (1) {\n\t\tp3_stage = 10;"),
    ("no_gate_gap_line", "i2c_log(\"SWEET CHG GAP: MON rows after a refused P3 stop", "if (0) i2c_log(\"SWEET CHG GAP: MON rows after a refused P3 stop"),
]


def source():
    """sweet_chg_i2c.c of patch 0284 with 0287..0339 applied (the text the mutants edit)."""
    pd = os.path.join(ROOT, "xnu", "patches")
    with open(os.path.join(pd, "0284-sweet-chg-i2c.patch")) as f:
        p = f.read()
    body = p[p.index("+++ b/bsd/dev/sweet_chg_i2c.c\n"):].split("\n", 2)[2]
    j = body.find("\ndiff --git ")
    body = body[:j] if j >= 0 else body
    d = tempfile.mkdtemp(prefix="mr-src-")
    try:
        os.makedirs(os.path.join(d, "bsd", "dev"))
        with open(os.path.join(d, "bsd", "dev", "sweet_chg_i2c.c"), "w") as f:
            f.write("\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n")
        subprocess.run(["git", "-C", d, "init", "-q"], check=True)
        for n in STACK_NAMES:
            r = subprocess.run(["git", "-C", d, "apply", "--include=bsd/dev/sweet_chg_i2c.c", os.path.join(pd, n + ".patch")], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError("patch %s does not apply: %s" % (n, r.stderr))
        with open(os.path.join(d, "bsd", "dev", "sweet_chg_i2c.c")) as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TableShape(unittest.TestCase):
    """The table is data for tools/test_chg_mon_refused.py (which runs and kills every row) and tools/lint-mutant-needles; this checks its shape."""

    def test_rows_are_well_formed_and_named_once(self):
        names = [r[0] for r in CHG_MON_REFUSED_MUTANTS]
        self.assertEqual(len(names), len(set(names)))
        for row in CHG_MON_REFUSED_MUTANTS:
            self.assertEqual(len(row), 3, row[0])
            self.assertNotEqual(row[1], row[2], row[0])

    def test_needles_occur_once(self):
        s = source()
        for name, old, _new in CHG_MON_REFUSED_MUTANTS:
            self.assertEqual(s.count(old), 1, name)


if __name__ == "__main__":
    unittest.main()
