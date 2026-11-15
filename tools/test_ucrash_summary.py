#!/usr/bin/env python3
"""Host test for tools/ucrash_summary.py (user 2026-11-11): grouping, signal decode, symbolication through a fake ipsw, missing-ipsw GAP,
corpse/jetsam counts, the report cap, and a mutant check that a dropped frame is caught. Fixtures are synthetic (made-up addresses, no Apple data)."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os, stat, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ucrash_summary as U  # noqa: E402

SLIDE, MH = 0x10000000, 0x100000000
A = [0x190000000 + SLIDE + 0x100 * i for i in range(1, 4)]   # cache frames after the slide


def crash(name, pid, sig, ns, code, text, bt, pc=None):
    pc = pc if pc is not None else bt[0]
    return ["1794373648.5\tCRASH %s[%d] sig %d code 0x0 sub 0x0 slide 0x%x mh 0x%x" % (name, pid, sig, SLIDE, MH),
            "1794373648.5\tCRASH %s pc 0x%x lr 0x%x sp 0x16aece560 fp 0x16aece580 far 0x21232ae28" % (name, pc, bt[0]),
            "1794373648.5\tCRASH %s x0 0x0 0x1 0x2 0x3 0x4 0x5" % name,
            "1794373648.5\tCRASH %s bt %s" % (name, " ".join("0x%x" % b for b in bt)),
            "1794373648.5\tCRASH %s reason ns %d code 0x%x: %s" % (name, ns, code, text)]


def fake_ipsw(d, drop=None):
    p = os.path.join(d, "ipsw")
    with open(p, "w") as f:   # one address in, `0xADDR: fn_<addr> + 4` out, like the real tool; `drop` prints nothing for that address
        f.write("#!/bin/sh\nfor a; do :; done\n[ \"$a\" = \"%s\" ] && exit 1\necho \"   * Address location dylib=/usr/lib/libx.dylib\"\n"
                "echo \"$a: fn_$a + 4\"\n" % (drop or "none"))
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    dsc = os.path.join(d, "dsc")
    open(dsc, "w").close()
    return p, dsc


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ["PIXOS_UCRASH_CACHE"] = os.path.join(self.d, "cache.json")
        self.ipsw, self.dsc = fake_ipsw(self.d)
        os.environ["PIXOS_IPSW"], os.environ["PIXOS_DSC"] = self.ipsw, self.dsc
        self.log = (crash("Foo", 10, 6, 2, 6, "(none)", A) + crash("Foo", 11, 6, 2, 6, "(none)", A)
                    + crash("Bar", 12, 9, 7, 3, "check-in timeout", A[:1])
                    + ["1.0\tFailed to send exception EXC_CORPSE_NOTIFY. error code: 5 for pid 10",
                       "1.0\t[ 2.0] SWEET JETSAM: pkr[251] per-process limit exceeded: limit 6 MB footprint 6 MB",
                       "1.0\t[ 2.0] SWEET JETSAM: pkr[251] kill state: memlimit_active 0 fatal 1"])

    def test_grouping_decode(self):
        out = U.build(self.log)
        self.assertIn("3 userland crash(es) in 2 group(s)", out[0])
        foo = [l for l in out if l.startswith("UCRASH: Foo")]
        self.assertEqual(len(foo), 1)
        self.assertIn("x2", foo[0])
        self.assertIn("SIGABRT", foo[0])
        self.assertTrue(any("Bar sig 9" in l and "ns 7 code 0x3: check-in timeout" in l for l in out))
        self.assertTrue(any("jetsam kills: 1 (pkr x1)" in l for l in out))
        self.assertTrue(any("EXC_CORPSE_NOTIFY send failures: 1" in l for l in out))
        self.assertTrue(any("EXC_CORPSE_NOTIFY" in l and "no crash reporter receives the corpses" in l for l in out))

    def test_symbolication(self):
        out = U.build(self.log)
        self.assertTrue(any("bt0 " in l and "fn_0x%x + 4" % (A[0] - SLIDE) in l for l in out), out)
        self.assertFalse(any("?0x" in l for l in out))
        self.assertFalse(any("GAP" in l for l in out), out)

    def test_main_image_offset(self):
        out = U.build(crash("Baz", 5, 5, 2, 5, "(none)", [MH + 0x1234]))
        self.assertTrue(any("0x%x = main image 0x%x" % (MH + 0x1234, 0x100000000 + 0x1234) in l for l in out), out)

    def test_missing_ipsw_gap(self):
        os.environ["PIXOS_IPSW"] = os.path.join(self.d, "nope")
        out = U.build(self.log)
        self.assertTrue(any(l.startswith("UCRASH GAP: ipsw not found") for l in out), out)
        self.assertTrue(any(l.startswith("UCRASH:   bt0 ?0x") for l in out), out)   # frame kept, marked unresolved

    def test_outside_image_frame_is_marked(self):
        out = U.build(crash("Qux", 6, 11, 2, 11, "(none)", [0x7000]))
        self.assertTrue(any("?0x7000" in l for l in out) and any("GAP" in l and "0x7000" in l and "outside" in l for l in out), out)

    def test_no_frame_dropped_mutant(self):
        """every bt address of the first crash must appear in the block; a mutant that drops one is caught by this same check"""
        def covered(block, bt):
            return all(("0x%x" % a) in " ".join(block) for a in bt)
        block = U.build(crash("Foo", 10, 6, 2, 6, "(none)", A))
        self.assertTrue(covered(block, A))
        mutant = [l for l in block if "0x%x" % A[1] not in l]
        self.assertFalse(covered(mutant, A))

    def test_failed_a2s_frame_gap(self):
        self.ipsw, _ = fake_ipsw(self.d, drop="0x%x" % (A[1] - SLIDE))
        out = U.build(crash("Foo", 10, 6, 2, 6, "(none)", A))
        self.assertTrue(any("?0x%x" % A[1] in l for l in out), out)
        self.assertTrue(any(l.startswith("UCRASH GAP:") for l in out), out)

    def test_group_cap(self):
        log = []
        for i in range(12):
            log += crash("P%d" % i, 100 + i, 6, 2, 6, "(none)", [MH + 0x10 * i])
        out = U.build(log)
        self.assertEqual(sum(1 for l in out if "sig 6 (SIGABRT) x1 pid" in l), U.MAX_GROUPS)
        self.assertTrue(any("+4 more group(s)" in l for l in out), out)

    def test_compact_form_and_file(self):
        with open(os.path.join(self.d, "usbnet.log"), "w") as f:
            f.write("\n".join(self.log) + "\n")
        out = U.lines({"dir": self.d})
        self.assertLessEqual(len(out), 8, out)
        self.assertTrue(any(l.startswith("UCRASH: Foo sig 6") and "fn_0x" in l for l in out), out)
        with open(os.path.join(self.d, "ucrash.txt")) as f:
            self.assertIn("bt0 ", f.read())

    def test_lines_never_raises(self):
        self.assertTrue(U.lines({})[0].startswith("UCRASH GAP:"))
        self.assertTrue(U.lines({"dir": os.path.join(self.d, "absent")})[0].startswith("UCRASH GAP:"))
        with open(os.path.join(self.d, "usbnet.log"), "w") as f:
            f.write("\n".join(self.log) + "\n")
        self.assertIn("UCRASH:", U.lines({"dir": self.d})[0])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    print("RESULT: %s ucrash-summary cases=%d" % ("PASS" if r.wasSuccessful() else "FAIL", r.testsRun))
    sys.exit(0 if r.wasSuccessful() else 1)
