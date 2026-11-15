"""The generator's checks for --mif-clk (T-5030): the memory domains MIF (0) / INT (1) of soc.json acpm.dvfs. MIF's ceiling is its table TOP row (the only rate
the shim may ask), INT is never requested (refused by class in dvfs_set; its ceiling = lowest row only stops a raise); the CPU --dvfs requests never touch them."""
import copy
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import boards  # noqa: E402


def acpm():
    return copy.deepcopy(boards.BOARDS["bluejay"]["acpm"])


class MifClk(unittest.TestCase):
    def test_shipped_rows(self):
        a = acpm()
        boards._acpm_dvfs_check(a)
        d = {x["name"]: x for x in a["dvfs"]["domains"]}
        self.assertEqual((d["MIF"]["idx"], d["INT"]["idx"]), (0, 1))
        self.assertEqual(len(d["MIF"]["rates_khz"]), 13)		# observed `SWEET FVMAP: dom 0 type=0 num_of_lv=13`
        self.assertEqual((d["MIF"]["rates_khz"][0], d["MIF"]["rates_khz"][-1]), (3172000, 421000))
        self.assertEqual(d["INT"]["rates_khz"], [533000, 465000, 332000, 267000, 200000, 155000, 100000])
        self.assertEqual((d["MIF"]["ceiling_khz"], d["INT"]["ceiling_khz"]), (3172000, 100000))

    def test_mif_ceiling_is_pinned_to_the_top_row(self):
        for ceil in (2730000, 421000, 1014000):
            a = acpm()
            for x in a["dvfs"]["domains"]:
                if x["name"] == "MIF":
                    x["ceiling_khz"] = ceil
            with self.assertRaises(ValueError):
                boards._acpm_dvfs_check(a)

    def test_int_ceiling_is_the_lowest_row(self):
        for ceil in (533000, 465000, 155000):
            a = acpm()
            for x in a["dvfs"]["domains"]:
                if x["name"] == "INT":
                    x["ceiling_khz"] = ceil
            with self.assertRaises(ValueError):
                boards._acpm_dvfs_check(a)

    def test_index_pins(self):
        for name, idx in (("MIF", 1), ("INT", 0), ("MIF", 2)):
            a = acpm()
            for x in a["dvfs"]["domains"]:
                if x["name"] == name:
                    x["idx"] = idx
            with self.assertRaises(ValueError):
                boards._acpm_dvfs_check(a)

    def test_macros_and_cpu_requests(self):
        h = boards.c_header("bluejay")
        for line in ("#define ACPM_DVFS_N 3", "#define ACPM_DVFS_MEM_IDXS 0, 1", "#define ACPM_DVFS_MIF_IDX 0", "#define ACPM_DVFS_MIF_KHZ 3172000"):
            self.assertIn(line, h)
        self.assertEqual(boards.dvfs_requests("bluejay", "1803000,0,0"), [1803000, 0, 0])
        with self.assertRaises(ValueError):	# the memory domains are not --dvfs targets: still exactly three values
            boards.dvfs_requests("bluejay", "1803000,0,0,3172000,100000")


ROWS = [3172000, 2730000, 2535000, 2288000, 2028000, 1716000, 1539000, 1352000, 1014000, 845000, 676000, 546000, 421000]	# gs101.dtsi mif_int_map col 1 (:878-890), observed fvmap dom 0


class MifClkLevel(unittest.TestCase):
    """ALL-ON T-02 --mif-clk-level: every table row is accepted, nothing else is; the generated allowlist macro; build.sh refuses at build time."""

    def test_every_row_accepted(self):
        for r in ROWS:
            self.assertEqual(boards.mif_clk_level("bluejay", str(r)), r)
        self.assertEqual(sorted(ROWS, reverse=True), ROWS)

    def test_refused_values(self):
        for bad in ("1716001", "1715999", "0", "", " ", "-1", "+1716000", "01716000", "1716000 ", "1716000 -DX", "0x1a2b00", "1716000.0", "1.7e6", "\u0663\u0661\u0667\u0662\u0660\u0660\u0660", "3172001", "422000", "9999999999999"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                boards.mif_clk_level("bluejay", bad)
        with self.assertRaises(ValueError):
            boards.mif_clk_level("bluejay", None)

    def test_table_pinned_to_soc_json_both_ways(self):
        a = acpm()
        for d in a["dvfs"]["domains"]:
            if d["name"] == "MIF":
                d["rates_khz"] = [x for x in d["rates_khz"] if x != 1716000]	# a table row dropped
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)
        a = acpm()
        for d in a["dvfs"]["domains"]:
            if d["name"] == "INT":
                d["rates_khz"] = [x for x in d["rates_khz"] if x != 332000]	# a mif_int_map INT value that is not a firmware INT row
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)

    def test_map_pins(self):
        self.assertEqual([m for m, _ in boards._MIF_INT_MAP], ROWS)
        self.assertEqual(dict(boards._MIF_INT_MAP)[1716000], 332000)
        self.assertEqual(dict(boards._MIF_INT_MAP)[421000], 100000)

    def test_generated_allowlist_macro(self):
        h = boards.c_header("bluejay")
        ok = [l for l in h.splitlines() if l.startswith("#define ACPM_DVFS_MIF_LEVEL_OK(k)")][0]
        for r in ROWS:
            self.assertIn(f"(k) == {r}", ok)
        self.assertEqual(ok.count("(k) =="), 13)
        self.assertIn("#define ACPM_DVFS_MIF_KHZ 3172000", h)	# the default request is unchanged

    def test_cli(self):
        tool = os.path.join(ROOT, "tools", "boards.py")
        r = subprocess.run([sys.executable, tool, "bluejay", "--mif-clk-level-check", "1716000"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "1716000"), r.stderr)
        r = subprocess.run([sys.executable, tool, "bluejay", "--mif-clk-level-check", "1716001"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, tool, "bluejay", "--mif-clk-levels"], capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [str(x) for x in ROWS])

    def test_build_sh_refuses_at_build_time(self):
        build = os.path.join(ROOT, "shim", "build.sh")
        for extra, want in ((["--mif-clk-level", "1716001"], "not a MIF table row"), (["--mif-clk-level", "3172000 -DFOO"], "plain decimal"), (["--mif-clk-level", "0"], "not a MIF table row")):
            r = subprocess.run([build, "--board", "bluejay", "--boot-xnu", "/dev/null", *extra], capture_output=True, text=True, cwd=os.path.join(ROOT, "shim"))
            self.assertEqual(r.returncode, 2, (extra, r.stdout[-300:], r.stderr[-300:]))
            self.assertIn(want, r.stderr)


if __name__ == "__main__":
    unittest.main()
