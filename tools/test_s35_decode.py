"""Unit test of tools/s35-decode.py (docs/research/40, W-C3) on SYNTHETIC `LINK R34/R35/R36` logs built from the real table: row id -> (step, window, register, before/after) mapping, the PASS/FAIL checks for each of the
three staged runs (N = 34, 35, 36: each a strict prefix), the fault-aware cases (a stop at a named row, a dead read, CPUHALT not latched, nrb != 0, bankidx not 0), table sha mismatch, lost rows, restore=FAILED."""
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("s35_decode", HERE / "s35-decode.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)
TABLE = HERE.parent / "soc/google/gs101/wlan_seq.json"
DOC = json.loads(TABLE.read_text())
ROWS = sd.table_rows(DOC)

P, W, A = 0x18001000, 0x18126000, 0x18120000
C = 0x18026000
# (step, window, offset, phase) -> word: the expected good values (powerctl: PowerDomain id 3, request 0x200, status bit 17; SYS_MEM / ARM pre-state observed in run w3a-n33: IOCTL 0, RESET_CTL 1, RESET_ST 0)
GOOD = {(34, P, 0x1E8, "before"): 0x30000000, (34, P, 0x1E8, "after"): 0x30020200,
        (35, W, 0x408, "before"): 0, (35, W, 0x800, "before"): 1, (35, W, 0x804, "before"): 0, (35, W, 0x408, "after"): 1, (35, W, 0x800, "after"): 0, (35, W, 0x804, "after"): 0,
        (35, C, 0x000, ""): 0x5, (35, C, 0x010, ""): 0, (35, C, 0x040, ""): 0x7,
        (36, A, 0x408, "before"): 0, (36, A, 0x800, "before"): 1, (36, A, 0x804, "before"): 0, (36, A, 0x408, "after"): 0x21, (36, A, 0x800, "after"): 0, (36, A, 0x804, "after"): 0}


def log(n=36, over=None, drop=(), summary="auto", sha="auto"):
    vals = {**GOOD, **(over or {})}
    lines = [] if sha is None else [f"LINK table sha={DOC['table_sha256'][:16] if sha == 'auto' else sha} rows={len(DOC['rows'])} N={n}"]
    for i, st, w, o, _, ph in ROWS:
        k = (st, w, o, ph)
        if st <= n and k in vals and k not in drop:
            lines.append(f"[ 1.0] WLAN: LINK R{st} row={i} rd=0x{vals[k]:08X}")
    lines.append("LINK R33 row=1 rd=0xDEADBEEF")
    return lines + ([f"LINK summary reached=S{n} stopped=none restore=ok" if summary == "auto" else summary] if summary else [])


def run(lines):
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "usb.txt").write_text("\n".join(lines))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sd.main([d, "--table", str(TABLE)])
    return rc, out.getvalue(), err.getvalue()


class S35Decode(unittest.TestCase):
    def test_table_mapping(self):
        self.assertEqual(len(ROWS), 2 + 9 + 6)
        self.assertEqual([(st, w, o, ph) for _, st, w, o, _, ph in ROWS], list(GOOD))
        self.assertEqual([l for _, _, _, _, l, _ in ROWS][:2], ["powerctl", "powerctl"])
        self.assertEqual([l for _, _, _, _, l, _ in ROWS][8:11], ["coreinfo", "bankidx", "bankinfo"])
        self.assertTrue(all(i > 1339 for i, *_ in ROWS))			# after the trimmed S33

    def test_the_three_staged_runs_pass(self):
        for n, rows in ((34, 2), (35, 11), (36, 17)):
            rc, out, _ = run(log(n))
            self.assertEqual(rc, 0, out)
            self.assertIn(f"RESULT: PASS S{n} {rows} rows", out)
        rc, out, _ = run(log(36))
        for s in ("domain id 3", "bit 17 1", "bank size 65536", "DHD(rev<12): nb 5 nrb 0", "CPUHALT 1", "uniform-bank estimate 320 KiB"):
            self.assertIn(s, out)

    def test_s34_skip_path_passes_with_id_zero(self):
        ov = {(34, P, 0x1E8, "before"): 0x00030300, (34, P, 0x1E8, "after"): 0x00030300}
        rc, out, _ = run(log(35, ov) + ["LINK S34 skip: SRPWR request, domain 1 already up (no write) row=1345"])
        self.assertEqual(rc, 0, out)

    def test_s34_skip_line_with_request_not_up_fails(self):
        rc, out, _ = run(log(35, {(34, P, 0x1E8, "before"): 0x00020000}) + ["LINK S34 skip: SRPWR request, domain 1 already up (no write) row=1345"])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL P1", out)

    def test_s34_domain_id_zero_fails(self):
        rc, out, _ = run(log(34, {(34, P, 0x1E8, "before"): 0x00000000}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL P1", out)

    def test_s34_status_never_set_or_request_lost_fails(self):
        for after in (0x30000200, 0x30020000, 0x30000000):
            rc, out, _ = run(log(34, {(34, P, 0x1E8, "after"): after}))
            self.assertEqual(rc, 1, hex(after))
            self.assertIn("FAIL P2", out)

    def test_s34_status_already_up_is_informative(self):
        rc, out, _ = run(log(34, {(34, P, 0x1E8, "before"): 0x30020000}))
        self.assertEqual(rc, 0, out)
        self.assertIn("BEFORE the request = 1", out)

    def test_s35_gate_failures(self):
        for k, v, nm in (((35, W, 0x408, "after"), 3, "W1"), ((35, W, 0x408, "after"), 0, "W1"), ((35, W, 0x800, "after"), 1, "W2"), ((35, W, 0x804, "after"), 1, "W3")):
            rc, out, _ = run(log(35, {k: v}))
            self.assertEqual(rc, 1, (k, v))
            self.assertIn(f"FAIL {nm}", out)

    def test_s35_coreinfo_cases(self):
        for ci, nm in ((0xFFFFFFFF, "C1"), (0x25, "C2"), (0x0, "C2"), (0x200, "C2")):
            rc, out, _ = run(log(35, {(35, C, 0x000, ""): ci}))
            self.assertEqual(rc, 1, hex(ci))
            self.assertIn(f"FAIL {nm}", out)

    def test_s35_bankidx_and_bankinfo(self):
        rc, out, _ = run(log(35, {(35, C, 0x010, ""): 3}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL B1", out)
        rc, out, _ = run(log(35, {(35, C, 0x040, ""): 0xFFFFFFFF}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL B2", out)

    def test_s36_cpuhalt_not_latched_fails(self):
        for io_ in (0x01, 0x03, 0x23, 0x00):
            rc, out, _ = run(log(36, {(36, A, 0x408, "after"): io_}))
            self.assertEqual(rc, 1, hex(io_))
            self.assertIn("FAIL A1", out)
        rc, out, _ = run(log(36, {(36, A, 0x408, "after"): 0x40000021}))
        self.assertEqual(rc, 0, out)			# bits above 0x3FFF are not part of the test (the table masks 0x3FFF too)

    def test_s36_reset_still_asserted_fails(self):
        for k, nm in (((36, A, 0x800, "after"), "A2"), ((36, A, 0x804, "after"), "A3")):
            rc, out, _ = run(log(36, {k: 1}))
            self.assertEqual(rc, 1)
            self.assertIn(f"FAIL {nm}", out)

    def test_dead_read_fails(self):
        rc, out, _ = run(log(36, {(35, W, 0x804, "before"): 0xFFFFFFFF}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E1", out)

    def test_a_stop_is_named_and_fails(self):
        stop = [i for i, st, w, o, _, ph in ROWS if (st, w, o, ph) == (34, P, 0x1E8, "before")][0]
        row = next(r["id"] for r in DOC["rows"] if r["step"] == 34 and r["kind"] == "wait_mask")
        rc, out, _ = run(log(34, {(34, P, 0x1E8, "after"): 0x30000200}, drop={(34, P, 0x1E8, "after")}, summary=f"LINK summary reached=S34 stopped=timeout at=S34 row={row} restore=ok"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E4", out)
        self.assertIn("stopped=timeout at S34", out)
        self.assertIn("ExtPwrStatus", out)			# the table note of the wait row is quoted
        self.assertGreater(stop, 0)

    def test_lost_row_fails(self):
        rc, out, _ = run(log(36, drop={(35, C, 0x040, "")}))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E2", out)

    def test_restore_failed_and_missing_summary_fail(self):
        rc, out, _ = run(log(36, summary="LINK summary reached=S36 stopped=none restore=FAILED"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL E3", out)
        rc, out, _ = run(log(34, summary=None))
        self.assertEqual(rc, 1)
        self.assertIn("no `LINK summary` line", out)

    def test_sha_mismatch_and_no_data(self):
        rc, _, err = run(log(sha="0123456789abcdef"))
        self.assertEqual(rc, 2)
        self.assertIn("table sha mismatch", err)
        rc, _, err = run(["nothing here"])
        self.assertEqual(rc, 2)
        self.assertIn("no LINK R34/R35/R36 rows", err)

    def test_rows_under_another_step_tag_are_ignored(self):
        first = ROWS[0][0]
        rc, out, _ = run(log(34) + [f"LINK R35 row={first} rd=0x00000000", f"LINK R33 row={first} rd=0x00000000"])
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
