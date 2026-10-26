#!/usr/bin/env python3
"""Host test for the patch-series check of tools/validate-repo.py: a number held by a reservation (placeholder) fills the contiguity hole,
and a REAL patch file with a reserved number is accepted (the reservation only matters while no file holds the number)."""
import importlib.util, os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("validate_repo", os.path.join(HERE, "validate-repo.py"))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

DIFF = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vrp-")
        self.addCleanup(shutil.rmtree, self.d)
        self.old = (vr.PATCHES, vr.held_elsewhere)
        vr.PATCHES = self.d
        self.addCleanup(lambda: setattr(vr, "PATCHES", self.old[0]) or setattr(vr, "held_elsewhere", self.old[1]))

    def files(self, *nums):
        for n in nums:
            with open(os.path.join(self.d, "%04d-sweet-p%d.patch" % (n, n)), "w") as fh:
                fh.write(DIFF)

    def run_check(self, held):
        vr.held_elsewhere = lambda: set(held)
        return vr.check_patches()

    def test_contiguous_passes(self):
        self.files(1, 2, 3)
        self.assertTrue(self.run_check([])[0])

    def test_unheld_gap_fails(self):
        self.files(1, 2, 6)
        ok, _, findings = self.run_check([3])
        self.assertFalse(ok)
        self.assertIn("gap: expected 0003", findings[0])

    def test_reservation_fills_the_gap(self):
        self.files(1, 2, 6)
        self.assertTrue(self.run_check([3, 4, 5])[0])

    def test_real_patch_inside_a_reserved_range_is_accepted(self):
        # the reserved range is 0003..0005 (e.g. wifi-reserved); a real 0004 lands there and 0003/0005 stay reserved
        self.files(1, 2, 4, 6)
        self.assertTrue(self.run_check([3, 4, 5])[0])
        self.files(3, 5)   # and with every number in the range really present
        self.assertTrue(self.run_check([3, 4, 5])[0])

    def test_held_regex_matches_reservations_and_files(self):
        self.assertTrue(vr.HELD_RE.match("0132  (not committed anywhere)   RESERVED by wifi-reserved (?)"))
        self.assertTrue(vr.HELD_RE.match("0130  0130-sweet-wlan-image-copy.patch [4 refs]   RESERVED by wifi (x)"))
        self.assertFalse(vr.HELD_RE.match("0132  something else"))


if __name__ == "__main__":
    unittest.main()
