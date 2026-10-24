"""Unit test of tools/erom-decode.py on a SYNTHETIC EROM built here (no Broadcom/Google data): the descriptor encoder follows the same brcmfmac chip.c masks the decoder cites
(docs/research/37 s.1.3), so this proves the walk logic (component / master port / address / size / 64-bit words / skips / EOT / truncation) and the log -> dword mapping, not the 4389's real layout."""
import importlib.util
import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("erom_decode", HERE / "erom-decode.py")
ed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ed)
TABLE = HERE.parent / "soc/google/gs101/wlan_seq.json"

EOT = 0xF


def cia(cid, designer=0x4BF, cls=0):
    return (designer << 20) | (cid << 8) | (cls << 4) | 1


def cib(rev, nsw, nmw, nsp, nmp):
    return (rev << 24) | (nsw << 19) | (nmw << 14) | (nsp << 9) | (nmp << 4) | 1


def mport(uid, num):
    return (uid << 8) | (num << 4) | 3


def asd(base, port, kind, sz, gt32=False):
    return (base & 0xFFFFF000) | (port << 8) | (kind << 6) | (sz << 4) | (8 if gt32 else 0) | 5


def synthetic():
    """ChipCommon (slave 0x18000000 + slave wrapper 0x18100000), an ARM-like core with a master port (master wrapper), a core with no wrappers (skipped), a PMU with no wrappers (kept), a core with a
    64-bit address word and a size descriptor before its real register region, then EOT."""
    return [
        0x00000000,								# an empty word before the first component: skipped
        cia(0x800), cib(0x2A, 1, 0, 1, 0), asd(0x18000000, 0, 0, 0), asd(0x18100000, 1, 2, 0),
        cia(0x847), cib(5, 0, 1, 1, 1), mport(0x12, 0), asd(0x18002000, 0, 0, 1), asd(0x18102000, 0, 3, 0),
        cia(0x999), cib(1, 0, 0, 0, 0), mport(1, 0), asd(0x18999000, 0, 0, 0),		# nmw + nsw == 0: skipped by brcmfmac, its descriptors are walked over
        cia(0x827), cib(7, 0, 0, 1, 0), asd(0x18003000, 0, 0, 0),
        cia(0x83C), cib(0x11, 1, 0, 1, 0), asd(0x18008000, 0, 1, 3), 0x00010000, asd(0x1800A000, 0, 0, 3, True), 0x00004000 | 8, 0x0, asd(0x18004000, 0, 0, 0), asd(0x18104000, 1, 2, 0),
        EOT,
    ]


class EromDecode(unittest.TestCase):
    def test_walk(self):
        cores, notes, ended = ed.decode(synthetic())
        self.assertTrue(ended)
        self.assertEqual(notes, [])
        by = {c["id"]: c for c in cores}
        self.assertEqual([c["id"] for c in cores], [0x800, 0x847, 0x999, 0x827, 0x83C])
        cc = by[0x800]
        self.assertEqual((cc["rev"], cc["nsw"], cc["nmw"], cc["nsp"], cc["nmp"], cc["base"], cc["wrapper"], cc["kept"], cc["name"]), (0x2A, 1, 0, 1, 0, 0x18000000, 0x18100000, True, "ChipCommon"))
        arm = by[0x847]
        self.assertEqual((arm["base"], arm["wrapper"], arm["kept"], arm["rev"]), (0x18002000, 0x18102000, True, 5))	# an 8K slave region; the wrapper is the MASTER-wrapper kind (core has a master port)
        self.assertEqual((by[0x999]["kept"], by[0x999]["base"]), (False, None))
        self.assertEqual((by[0x827]["kept"], by[0x827]["base"], by[0x827]["wrapper"]), (True, 0x18003000, 0))		# PMU kept without wrappers; no wrapper found
        pcie = by[0x83C]
        self.assertEqual((pcie["base"], pcie["wrapper"]), (0x18004000, 0x18104000))		# the bridge (kind 1, size desc) and the 64-bit slave (size desc) do not count as 4K/8K registers
        self.assertEqual([a["kind"] for a in pcie["asd"]], ["bridge", "slave", "slave", "slave-wrapper"])

    def test_pmu_without_a_wrapper_runs_into_the_next_component(self):
        # the PMU's regaddr loop meets the next component before finding a wrapper: base kept, wrapper 0, the next component is still decoded
        cores, _, ended = ed.decode([cia(0x827), cib(1, 0, 0, 1, 0), asd(0x18003000, 0, 0, 0), cia(0x800), cib(1, 1, 0, 1, 0), asd(0x18000000, 0, 0, 0), asd(0x18100000, 1, 2, 0), EOT])
        self.assertTrue(ended)
        self.assertEqual([(c["id"], c["base"], c["wrapper"]) for c in cores], [(0x827, 0x18003000, 0), (0x800, 0x18000000, 0x18100000)])

    def test_truncated_and_odd_streams(self):
        s = synthetic()
        cores, notes, ended = ed.decode(s[:-1])			# no EOT
        self.assertFalse(ended)
        self.assertTrue(notes and "without an EOT" in notes[0])
        self.assertEqual(len(cores), 5)
        cores, notes, ended = ed.decode(s[:7])			# cut inside the ARM core
        self.assertFalse(ended)
        self.assertEqual([c["id"] for c in cores], [0x800])
        self.assertEqual(ed.decode([EOT]), ([], [], True))
        cores, notes, _ = ed.decode([cia(0x800), 0x12345678, EOT])	# a CIA not followed by a CIB
        self.assertTrue(notes and "not followed by a CIB" in notes[0])
        cores, notes, ended = ed.decode([cia(0x800), cib(1, 1, 0, 1, 0), EOT])	# EOT right after the CIB: that core is skipped, the table still ends
        self.assertTrue(ended and notes and "no master port / address" in notes[0] and not cores[0]["kept"])
        cores, notes, ended = ed.decode([cia(0x800), cib(1, 1, 0, 1, 0), asd(0x18000000, 0, 0, 0), EOT])	# EOT while the wrapper is still missing
        self.assertTrue(ended and notes and "unexpected EOT" in notes[0] and not cores[0]["kept"])
        cores, notes, ended = ed.decode([0xFFFFFFFF] * 4)		# all ones = EOT-typed, valid: ends at once with no cores (the run's all-ones stop rule fires before this)
        self.assertEqual((cores, ended), ([], True))

    def test_log_mapping_uses_the_table_row_ids(self):
        rows = json.loads(TABLE.read_text())["rows"]
        dump = [r for r in rows if r["step"] == 32 and r["blk"] == "MEM" and r["kind"] == "read_only" and r.get("log")]
        self.assertEqual([r["off"] for r in dump], [4 * i for i in range(896)])
        eromdw = synthetic()
        full = eromdw + [0] * (896 - len(eromdw))
        text = "noise\n" + "\n".join(f"[ 1.0] WLAN: LINK R32 row={r['id']} rd=0x{full[i]:08X}" for i, r in enumerate(dump)) + "\nLINK R31 row=5 rd=0xDEADBEEF\nLINK R32 row=1 rd=0x00000000\n"
        dw, missing = ed.dwords_from_log(text, rows)
        self.assertEqual((dw, missing), (full, []))
        cores, notes, ended = ed.decode(dw)
        self.assertEqual((len(cores), ended, notes), (5, True, []))
        # a lost line ends the usable dump at the gap and is reported
        lost = "\n".join(l for l in text.splitlines() if f"row={dump[10]['id']} " not in l)
        dw, missing = ed.dwords_from_log(lost, rows)
        self.assertEqual((len(dw), missing[0]), (10, 10))

    def _dump_log(self, skip=(), sha="auto", upto=None):
        doc = json.loads(TABLE.read_text())
        dump = [r for r in doc["rows"] if r["step"] == 32 and r["blk"] == "MEM" and r["kind"] == "read_only" and r.get("log")]
        full = synthetic() + [0] * (896 - len(synthetic()))
        upto = len(synthetic()) if upto is None else upto		# W-C1c: the run stops at the EOT, so the log ends with it (default: the synthetic EOT is the last dword)
        lines = [f"LINK R32 row={r['id']} rd=0x{full[i]:08X}" for i, r in enumerate(dump) if i not in skip and i < upto]
        if sha == "auto":
            sha = doc["table_sha256"][:16]
        return ([f"LINK table sha={sha} rows=1318 N=32"] if sha else []) + lines

    def _run(self, lines, *args):
        import io
        import contextlib
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "usb.txt").write_text("\n".join(lines))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = ed.main([d, "--table", str(TABLE), *args])
        return rc, out.getvalue(), err.getvalue()

    def test_require_dump_needs_every_dword_up_to_the_eot_only(self):
        # W-C1c: the run stops at the first EOT dword (0xF): every dword from 0 through it is required (a lost one FAILs), nothing after it is
        eot_i = len(synthetic()) - 1
        for skip in ({0}, {5}, {eot_i - 1}, {eot_i}):
            rc, out, err = self._run(self._dump_log(skip), "--require-dump")
            self.assertEqual(rc, 1, skip)
            self.assertIn("RESULT: FAIL", out)
        rc, out, _ = self._run(self._dump_log({5}))		# without the flag the decode up to the gap is still usable
        self.assertEqual(rc, 1)
        self.assertIn("1 dwords missing", out)
        rc, out, _ = self._run(self._dump_log(), "--require-dump")		# the log ends at the EOT: 28 lines, none of the 868 later rows
        self.assertEqual(rc, 0)
        self.assertIn("RESULT: PASS 5 components", out)
        rc, out, _ = self._run(self._dump_log(upto=896), "--require-dump")		# a full-span log (zeros after the EOT) passes too: dwords past the EOT are not required
        self.assertEqual(rc, 0)
        rc, out, _ = self._run(self._dump_log({700}, upto=896), "--require-dump")
        self.assertEqual(rc, 0)
        rc, out, _ = self._run(self._dump_log(upto=eot_i), "--require-dump")		# the EOT line itself lost: no 0xF in the log, so the whole span is required
        self.assertEqual(rc, 1)
        self.assertIn("RESULT: FAIL", out)

    def test_table_sha_check(self):
        rc, out, err = self._run(self._dump_log(sha="0123456789abcdef"))
        self.assertEqual(rc, 2)
        self.assertIn("RESULT: ERROR table sha mismatch", err)
        self.assertEqual(out, "")
        rc, out, err = self._run(self._dump_log(sha=None), "--require-dump")		# no sha line: decoded, with a notice
        self.assertEqual(rc, 0)
        self.assertIn("no `LINK table sha=` line", err)
        mixed = self._dump_log() + ["LINK table sha=0123456789abcdef rows=1 N=7"]	# a second, different table in the same capture
        self.assertEqual(self._run(mixed)[0], 2)

    def test_cli_exit_codes(self):
        import io
        import contextlib
        import tempfile
        rows = json.loads(TABLE.read_text())["rows"]
        dump = [r for r in rows if r["step"] == 32 and r["blk"] == "MEM" and r["kind"] == "read_only" and r.get("log")]
        eromdw = synthetic()
        full = eromdw + [0] * (896 - len(eromdw))
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "usb.txt"
            p.write_text("\n".join(f"LINK R32 row={r['id']} rd=0x{full[i]:08X}" for i, r in enumerate(dump)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(ed.main([d, "--table", str(TABLE)]), 0)
            self.assertIn("ChipCommon", out.getvalue())
            self.assertIn("RESULT: PASS 5 components", out.getvalue())
            q = pathlib.Path(d) / "dw.txt"
            q.write_text(" ".join(f"{x:08x}" for x in eromdw[:-1]))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ed.main(["--dwords", str(q)]), 1)
            q.write_text("")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ed.main(["--dwords", str(q)]), 2)


if __name__ == "__main__":
    unittest.main()
