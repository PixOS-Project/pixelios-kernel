"""Unit test of tools/s33-decode.py (docs/research/38, trimmed by docs/research/40) on SYNTHETIC `LINK R33` logs built from the real table: row id -> register mapping (11 MEM logs, no core page), the PASS/FAIL expectation checks,
SYS_MEM down (the observed power-on state) and up are both informative and neither stops the run, a stop with all rows logged fails, table sha mismatch, lost rows, restore=FAILED."""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("s33_decode", HERE / "s33-decode.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)
TABLE = HERE.parent / "soc/google/gs101/wlan_seq.json"
DOC = json.loads(TABLE.read_text())
ROWS = sd.table_rows(DOC)

GOOD = {(0x18012000, 0x604): 0x20010028, (0x18012000, 0x600): 0x100, (0x18012000, 0x608): 0x2C, (0x18012000, 0x618): 0xFFFF, (0x18012000, 0x61C): 0xFFFFFF,
        (0x18120000, 0x408): 0, (0x18120000, 0x800): 1, (0x18120000, 0x804): 0, (0x18126000, 0x408): 0, (0x18126000, 0x800): 1, (0x18126000, 0x804): 0}


def log(over=None, drop=(), summary="LINK summary reached=S33 stopped=none restore=ok", sha="auto"):
    vals = {**GOOD, **(over or {})}
    lines = [] if sha is None else [f"LINK table sha={DOC['table_sha256'][:16] if sha == 'auto' else sha} rows={len(DOC['rows'])} N=33"]
    for i, w, o, _ in ROWS:
        if (w, o) in vals and (w, o) not in drop:
            lines.append(f"[ 1.0] WLAN: LINK R33 row={i} rd=0x{vals[(w, o)]:08X}")
    lines.append("LINK R31 row=1 rd=0xDEADBEEF")
    return lines + ([summary] if summary else [])


def run(lines):
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "usb.txt").write_text("\n".join(lines))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sd.main([d, "--table", str(TABLE)])
    return rc, out.getvalue(), err.getvalue()


class S33Decode(unittest.TestCase):
    def test_table_mapping(self):
        self.assertEqual(len(ROWS), 11)
        self.assertEqual([(w, o) for _, w, o, _ in ROWS], list(GOOD))
        self.assertEqual([l for _, _, _, l in ROWS][:2], ["pmucapabilities", "pmucontrol"])
        self.assertNotIn("coreinfo", [l for _, _, _, l in ROWS])		# the SYS_MEM core page moved to S35

    def test_good_run_passes(self):
        rc, out, _ = run(log())
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS S33 11 rows", out)
        self.assertIn("pmurev 0x28 (40)", out)
        self.assertIn("DOWN", out)			# the observed power-on state (run w3a-n33): informative, not a stop

    def test_pmurev_guess_refuted(self):
        rc, out, _ = run(log({(0x18012000, 0x604): 0x20010027}, drop={(0x18120000, 0x408), (0x18120000, 0x800), (0x18120000, 0x804), (0x18126000, 0x408), (0x18126000, 0x800), (0x18126000, 0x804)},
                             summary="LINK summary reached=S33 stopped=refused at=S33 row=1326 restore=ok"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E1 pmurev", out)

    def test_upper_reset_bits_fail(self):
        for w, nm in ((0x18120000, "E2"), (0x18126000, "E3")):
            rc, out, _ = run(log({(w, 0x800): 0x101}))
            self.assertEqual(rc, 1, w)
            self.assertIn(f"FAIL {nm}", out)
            self.assertIn("UPPER BITS SET", out)

    def test_arm_out_of_reset_fails_e2_only(self):	# review 16 finding 3: ARM RESET_CTL must be exactly 1 (held in reset); SYS_MEM may be 0 (informative)
        rc, out, _ = run(log({(0x18120000, 0x800): 0}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E2", out)
        rc, out, _ = run(log({(0x18126000, 0x800): 0}))
        self.assertEqual(rc, 0, out)

    def test_sys_mem_state_is_informative_only(self):
        for over, word in (({(0x18126000, 0x800): 1}, "DOWN"), ({(0x18126000, 0x408): 3}, "DOWN"), ({(0x18126000, 0x408): 1, (0x18126000, 0x800): 0}, "UP")):
            rc, out, _ = run(log(over))
            self.assertEqual(rc, 0, (over, out))
            self.assertIn(word, out)
            self.assertIn("RESULT: PASS", out)

    def test_all_rows_logged_but_the_run_stopped_fails(self):
        rc, out, _ = run(log(summary="LINK summary reached=S33 stopped=refused at=S33 row=1338 restore=ok"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E8", out)

    def test_a_lost_row_fails(self):
        rc, out, _ = run(log(drop={(0x18126000, 0x804)}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E6", out)

    def test_all_ones_fails(self):
        for k in ((0x18126000, 0x804), (0x18012000, 0x608)):
            rc, out, _ = run(log({k: 0xFFFFFFFF}))
            self.assertEqual(rc, 1, k)
            self.assertIn("FAIL E5", out)

    def test_restore_failed_and_missing_summary_fail(self):
        rc, out, _ = run(log(summary="LINK summary reached=S33 stopped=none restore=FAILED"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E7", out)
        rc, out, _ = run(log(summary=None))
        self.assertEqual(rc, 1)
        self.assertIn("no `LINK summary` line", out)

    def test_sha_mismatch_and_no_data(self):
        rc, _, err = run(log(sha="0123456789abcdef"))
        self.assertEqual(rc, 2)
        self.assertIn("table sha mismatch", err)
        rc, _, err = run(["nothing here"])
        self.assertEqual(rc, 2)
        self.assertIn("no LINK R33 rows", err)

    def test_other_step_rows_ignored(self):
        lines = log() + ["LINK R32 row=%d rd=0x00000000" % ROWS[0][0]]		# a row id of S33 under another step tag is not an R33 line
        rc, out, _ = run(lines)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
