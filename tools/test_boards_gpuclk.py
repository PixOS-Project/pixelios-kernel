"""The generator's checks for --gpu-clk: the two GPU domains of soc.json acpm.dvfs (G3D 5 / G3DL2 6, ceiling = the firmware's init level)
and the pins in tools/boards.py (_ACPM_DVFS_DOMS, _GPU_PINS clk_khz); the CPU --dvfs requests never touch them."""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools import boards  # noqa: E402


def acpm():
    return copy.deepcopy(boards.BOARDS["bluejay"]["acpm"])


class GpuClk(unittest.TestCase):
    def test_shipped_rows(self):
        a = acpm()
        boards._acpm_dvfs_check(a)
        d = {x["name"]: x for x in a["dvfs"]["domains"]}
        self.assertEqual((d["G3D"]["idx"], d["G3DL2"]["idx"]), (5, 6))
        self.assertEqual(d["G3D"]["rates_khz"], [848000, 762000, 701000, 572000, 510000, 471000, 400000, 351000, 302000, 251000, 202000, 151000])
        self.assertEqual(d["G3DL2"]["rates_khz"], [996000, 885000, 750000, 603000, 434000, 302000, 151000])
        self.assertEqual((d["G3D"]["ceiling_khz"], d["G3DL2"]["ceiling_khz"]), (762000, 996000))

    def test_gpu_ceiling_is_pinned(self):
        for name, ceil in (("G3D", 151000), ("G3D", 202000), ("G3D", 572000), ("G3D", 848000), ("G3DL2", 151000), ("G3DL2", 302000), ("G3DL2", 750000), ("G3D", 510000), ("G3DL2", 603000)):
            a = acpm()
            for x in a["dvfs"]["domains"]:
                if x["name"] == name:
                    x["ceiling_khz"] = ceil
            with self.assertRaises(ValueError):
                boards._acpm_dvfs_check(a)

    def test_domain_set_and_indices_are_pinned(self):
        a = acpm()
        a["dvfs"]["domains"][3]["idx"] = 7
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)
        a = acpm()
        a["dvfs"]["domains"].pop()
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)

    def test_pin_clk_khz(self):
        g = copy.deepcopy(boards.BOARDS["bluejay"]["gpu"])
        boards._gpu_check(g)
        g["clk_khz"] = "202000"
        with self.assertRaises(ValueError):
            boards._gpu_check(g)

    def test_macros_and_cpu_requests(self):
        h = boards.c_header("bluejay")
        for line in ("#define ACPM_DVFS_N 3", "#define ACPM_DVFS_NALL 7", "#define ACPM_DVFS_GPU_IDXS 5, 6",
                     "#define ACPM_DVFS_IDXS 2, 3, 4, 5, 6, 0, 1", "#define GPU_CLK_KHZ 0x24dd8UL"):
            self.assertIn(line, h)
        self.assertIn("#define ACPM_DVFS_CEILS 1803000, 2253000, 2802000, 762000, 996000, 3172000, 100000", h)
        self.assertEqual(boards.dvfs_requests("bluejay", "1803000,0,0"), [1803000, 0, 0])
        with self.assertRaises(ValueError):	# the GPU clocks are not --dvfs targets: still exactly three values
            boards.dvfs_requests("bluejay", "1803000,0,0,151000,151000")


class GpuPairs(unittest.TestCase):
    """--gpu-clk-level (T-a214): the DT v2 pair table is a pinned constant, every value an fvmap row, G3DL2 >= G3D, pairs 1..3 are at or below the ceiling (row 1 = 996000/762000 = stock max, sprint-gpu 2026-11-08); row 0 is refused."""

    def test_pair_table_macro(self):
        h = boards.c_header("bluejay")
        self.assertIn("#define GPU_CLK_NLEVELS 4", h)
        self.assertIn("#define GPU_CLK_PAIRS { 996000, 848000 }, { 996000, 762000 }, { 885000, 701000 }, { 750000, 572000 }", h)
        self.assertEqual(len(boards._GPU_DVFS_V2), 12)
        # T-50eb review F1: the observed fvmap uV of the ceiling pair, aligned with the pairs, 0 = not observed
        self.assertIn("#define GPU_CLK_UVS { 775000, 756250 }, { 775000, 718750 }, { 775000, 700000 }, { 731250, 662500 }", h)

    def test_uv_pin_is_required_for_the_ceiling_pair(self):
        saved = dict(boards._GPU_DVFS_UV)
        try:
            boards._GPU_DVFS_UV.clear()
            with self.assertRaises(ValueError) as cm:
                boards._gpu_clk_pairs_check(acpm()["dvfs"])
            self.assertIn("_GPU_DVFS_UV", str(cm.exception))
        finally:
            boards._GPU_DVFS_UV.update(saved)
        self.assertEqual(boards._GPU_DVFS_UV, {(996000, 848000): (775000, 756250), (996000, 762000): (775000, 718750), (885000, 701000): (775000, 700000), (750000, 572000): (731250, 662500)})

    def test_levels_0_to_3_are_accepted(self):
        self.assertEqual(boards.gpu_clk_level("bluejay", "3"), (750000, 572000))
        self.assertEqual(boards.gpu_clk_level("bluejay", "1"), (996000, 762000))
        self.assertEqual(boards.gpu_clk_level("bluejay", "2"), (885000, 701000))

    def test_other_levels_are_refused_with_a_reason(self):
        for bad, why in (("0", "above the ceiling"),                          ("4", "only DT pair indexes 0..3"), ("-1", "only DT pair indexes"), ("11", "only DT pair indexes"), ("x", "not a number"), ("", "not a number"), ("3.0", "not a number")):
            with self.assertRaises(ValueError) as cm:
                boards.gpu_clk_level("bluejay", bad)
            self.assertIn(why, str(cm.exception), bad)

    def test_mutants_are_refused(self):
        saved = (boards._GPU_DVFS_V2, dict(boards._GPU_DVFS_CEIL))
        try:
            # a pair whose value is not an fvmap row
            boards._GPU_DVFS_V2 = saved[0][:3] + ((750000, 573000),) + saved[0][4:]
            with self.assertRaises(ValueError):
                boards._gpu_clk_pairs_check(acpm()["dvfs"])
            # G3DL2 below G3D
            boards._GPU_DVFS_V2 = saved[0][:3] + ((603000, 701000),) + saved[0][4:]
            with self.assertRaises(ValueError):
                boards._gpu_clk_pairs_check(acpm()["dvfs"])
            # a ceiling that is not an accepted pair
            boards._GPU_DVFS_V2 = saved[0]
            boards._GPU_DVFS_CEIL.update({"G3D": 510000, "G3DL2": 750000})
            with self.assertRaises(ValueError):
                boards._gpu_clk_pairs_check(acpm()["dvfs"])
        finally:
            boards._GPU_DVFS_V2, boards._GPU_DVFS_CEIL = saved[0], saved[1]
            boards._GPU_DVFS_CEIL.update(saved[1])
        boards._gpu_clk_pairs_check(acpm()["dvfs"])

    def test_raised_ceiling_or_broken_order_in_soc_json_is_refused(self):
        a = acpm()
        for x in a["dvfs"]["domains"]:
            if x["name"] == "G3DL2":
                x["ceiling_khz"] = 750000
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)
        a = acpm()
        for x in a["dvfs"]["domains"]:
            if x["name"] == "G3D":
                x["rates_khz"] = [x for x in x["rates_khz"] if x != 572000]
        with self.assertRaises(ValueError):
            boards._acpm_dvfs_check(a)


class S11(unittest.TestCase):
    """The S2MPG11 block read by --gpu-rails (soc.json acpm.s2mpg11): pinned values, a null makes the shim refuse."""

    def test_shipped_block_is_known_and_pinned(self):
        a = acpm()
        self.assertTrue(boards._acpm_s11_check(a))
        h = boards.c_header("bluejay")
        for line in ("#define ACPM_S11_KNOWN 1", "#define ACPM_S11_SPEEDY 1", "#define ACPM_S11_PMIC_ADDR 1", "#define ACPM_S11_NREG 10",
                     "#define ACPM_S11_REGS 0xc, 0x15, 0x16, 0x17, 0x25, 0x26, 0x27, 0x41, 0x42, 0x52"):
            self.assertIn(line, h)

    def test_moved_pin_fails(self):
        for key, bad in (("speedy_ch", 0), ("pmic_addr", 2), ("buck_min_uv", 250000), ("ldo1_step_uv", 6250)):
            a = acpm()
            a["s2mpg11"][key] = bad
            with self.assertRaises(ValueError):
                boards._acpm_s11_check(a)
        a = acpm()
        a["s2mpg11"]["regs"][1]["reg"] = "0x16"
        with self.assertRaises(ValueError):
            boards._acpm_s11_check(a)
        a = acpm()
        a["s2mpg11"]["regs"].pop()
        with self.assertRaises(ValueError):
            boards._acpm_s11_check(a)

    def test_null_means_unknown_and_no_macros(self):
        a = acpm()
        a["s2mpg11"]["speedy_ch"] = None
        self.assertFalse(boards._acpm_s11_check(a))
        a = acpm()
        a["s2mpg11"]["regs"][3]["reg"] = None
        self.assertFalse(boards._acpm_s11_check(a))


class GpuJob(unittest.TestCase):
    """The values shim/gpujob.c writes are derived in boards._gpu_job_values() from the kbase field layouts; they must equal the numbers of docs/research/28."""

    def test_values_match_the_design_doc(self):
        v = boards._gpu_job_values()
        self.assertEqual((v["TRANSCFG_ON"], v["TRANSCFG_OFF"]), (0x42000006, 0x42000001))
        self.assertEqual((v["MEMATTR_LO"], v["MEMATTR_HI"]), (0x888D8F88, 0x4C8D))
        self.assertEqual(v["JS_CONFIG"], 0x83B00)
        self.assertEqual((v["PTE_TABLE"], v["PTE_LEAF"]), (0x403, 0x457))
        self.assertEqual((v["HDR_W4"], v["WV_IMM64"], v["JOB_VA"], v["TARGET_VA"]), (0x10004, 7, 0x1000, 0x1100))
        self.assertEqual((v["MAGIC_HI"] << 32) | v["MAGIC_LO"], int.from_bytes(b"PIXELIOS", "big"))
        h = boards.c_header("bluejay")
        for line in ("#define GPU_JOB_TRANSCFG_ON 0x42000006u", "#define GPU_JOB_PTE_LEAF 0x457u", "#define GPU_S2MPU_G3D3 0x200b0000UL"):
            self.assertIn(line, h)

    def test_s2mpu_bases_are_pinned(self):
        g = copy.deepcopy(boards.BOARDS["bluejay"]["gpu"])
        boards._gpu_check(g)
        g["s2mpu_g3d2"] = "0x200A1000"
        with self.assertRaises(ValueError):
            boards._gpu_check(g)


if __name__ == "__main__":
    unittest.main()
