"""Mutation test of the WLANFW gap lines (xnu patch 0185, X-W7 F1-F4, rule 8; ticket T-0600): swf_gaps() in bsd/dev/sweet_wlan_fw.h decides which "WIFI GAP: fw ..." lines the
glue logs when the CLM blob, the txcap blob, the region pick or the stock path is missing/defaulted. Each mutant breaks one of them (line dropped, CLM presence ignored or inverted,
text or severity lost) in a copy of the header (0128 new file + the 0185 hunk); shim/wlan_fw_host_test.c is built against the copy and must FAIL. The unmutated header must PASS, a needle that
matches nothing fails (lint-mutant-needles). GLUE: the 0185 glue hunk must call swf_gaps() and print every result through swl_gap; removing any of those three lines must be detected.
Run: python3 -B tools/test_wlan_fw_gap_mutants.py  (also picked up by `unittest discover -s tools -p 'test_*.py'`)."""
import os
import re
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
PATCH0128 = os.path.join(ROOT, "xnu", "patches", "0128-sweet-wlan-fwcheck.patch")
PATCH0185 = os.path.join(ROOT, "xnu", "patches", "0185-sweet-wlan-fw-gaps.patch")
TEST = os.path.join(ROOT, "shim", "wlan_fw_host_test.c")
HDR = "bsd/dev/sweet_wlan_fw.h"


def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def _section(patch, path):
    out, on = [], False
    for line in _read(patch).split("\n"):
        if line.startswith("diff --git "):
            on = line.endswith(" b/" + path)
        if on:
            out.append(line)
    return "\n".join(out) + "\n" if out else ""


def final_header():
    """sweet_wlan_fw.h as the kernel gets it: the 0128 new file, then the 0185 hunk (same chain as shim/build.sh)."""
    new_file = []
    for line in _section(PATCH0128, HDR).split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            new_file.append(line[1:])
    with tempfile.TemporaryDirectory() as d:
        full = os.path.join(d, HDR)
        os.makedirs(os.path.dirname(full))
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_file) + "\n")
        r = subprocess.run(["patch", "-s", "-p1", "-d", d], input=_section(PATCH0185, HDR), capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("0185 does not apply to %s: %s" % (HDR, r.stdout + r.stderr))
        return _read(full)


def glue_added_lines():
    return [ln[1:] for ln in _section(PATCH0185, "bsd/dev/sweet_wlan.c").split("\n") if ln.startswith("+") and not ln.startswith("+++")]


# (label, old, new): every occurrence of `old` in the final header becomes `new`; the host test must fail
MUTANTS = [
    ("CLM presence ignored (always 'no CLM entry')", "\t\t\tclm = 1;\n", "\t\t\tclm = 0;\n"),
    ("CLM presence inverted", "clm ? \"CLM packed but not loaded", "!clm ? \"CLM packed but not loaded"),
    ("CLM missing reported as packed", "\"no CLM entry in the container\"", "\"CLM packed but not loaded (no clmload yet)\""),
    ("CLM tag byte 0 not compared", "e[i].tag[0] == 'C' && ", ""),
    ("CLM tag byte 3 not compared (CLM! counts)", " && e[i].tag[3] == ' '", ""),
    ("CLM only looked for in the first entry", "for (i = 0; i < n; i++) {\n\t\tif (e[i].tag[0] == 'C'", "for (i = 0; i < 1 && i < n; i++) {\n\t\tif (e[i].tag[0] == 'C'"),
    ("CLM gap severity high -> low", "\tg[k++].sev = \"high\";", "\tg[k++].sev = \"low\";"),
    ("txcap gap dropped", "\tg[k].what = \"no txcap_blob (stock has none, not loaded)\";\n\tg[k++].sev = \"low\";\n", ""),
    ("txcap gap text lost", "no txcap_blob (stock has none, not loaded)", "txcap ok"),
    ("txcap gap severity low -> high", "not loaded)\";\n\tg[k++].sev = \"low\";", "not loaded)\";\n\tg[k++].sev = \"high\";"),
    ("region gap dropped", "\tg[k].what = \"cal/clm = unsuffixed default, no region pick\";\n\tg[k++].sev = \"medium\";\n", ""),
    ("region gap text lost", "cal/clm = unsuffixed default, no region pick", "region ok"),
    ("region gap severity medium -> low", "\tg[k++].sev = \"medium\";", "\tg[k++].sev = \"low\";"),
    ("path gap dropped", "\tg[k].what = \"stock fw path unsettled (Kbuild /vendor/etc/wifi vs image /vendor/firmware)\";\n\tg[k++].sev = \"low\";\n", ""),
    ("path gap loses the Kbuild path", "(Kbuild /vendor/etc/wifi vs image /vendor/firmware)", "(image /vendor/firmware)"),
    ("path gap loses the image path", "(Kbuild /vendor/etc/wifi vs image /vendor/firmware)", "(Kbuild /vendor/etc/wifi)"),
    ("gap count not returned", "\treturn k;\n}", "\treturn 0;\n}"),
    ("gap count off by one", "\treturn k;\n}", "\treturn k - 1;\n}"),
    ("empty container changes the gap count", "unsigned i, k = 0, clm = 0;", "unsigned i, k = n ? 0 : 99, clm = 0;"),
]


def host_test_result(header_text):
    """Build shim/wlan_fw_host_test.c against `header_text`; returns (compiled, rc, output)."""
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "sweet_wlan_fw.h"), "w", encoding="utf-8") as fh:
            fh.write(header_text)
        exe = os.path.join(d, "t")
        r = subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + d, "-o", exe, TEST], capture_output=True, text=True)
        if r.returncode != 0:
            return False, r.returncode, r.stdout + r.stderr
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return True, r.returncode, r.stdout + r.stderr


class FwGapMutants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hdr = final_header()

    def test_unmutated_header_passes(self):
        ok, rc, out = host_test_result(self.hdr)
        self.assertTrue(ok, out)
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS wlan_fw_host_test", out)

    def test_every_gap_mutant_is_killed(self):
        survivors = []
        for label, old, new in MUTANTS:
            self.assertIn(old, self.hdr, "mutant needle not found: %s" % label)
            ok, rc, out = host_test_result(self.hdr.replace(old, new))
            if not ok:
                survivors.append("%s: mutant does not compile (a compile error is not a kill): %s" % (label, out[:200]))
            elif rc == 0:
                survivors.append("%s: SURVIVED" % label)
        self.assertEqual(survivors, [], "\n".join(survivors))

    def test_glue_prints_every_gap(self):
        """The glue hunk must (1) declare the result array, (2) ask swf_gaps() for it, (3) loop over ALL results into swl_gap("fw", what, sev)."""
        needles = ["\tstruct swf_gap g[SWF_GAP_MAX];", "\tng = swf_gaps(e, n, g);", "\tfor (i = 0; i < ng; i++) {", "\t\tswl_gap(\"fw\", g[i].what, g[i].sev);"]
        added = glue_added_lines()
        for nd in needles:
            self.assertIn(nd, added, "glue line missing in patch 0185: %r" % nd)
        self.assertEqual(len(re.findall(r"swl_gap\(\"fw\", g\[i\]\.what, g\[i\]\.sev\)", "\n".join(added))), 1)


if __name__ == "__main__":
    res = unittest.main(verbosity=1, exit=False).result
    good = res.wasSuccessful()
    print("RESULT: %s wlan_fw_gap_mutants mutants=%d glue_lines=4" % ("PASS" if good else "FAIL", len(MUTANTS)))
    sys.exit(0 if good else 1)
