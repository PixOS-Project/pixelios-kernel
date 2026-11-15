"""T-1499: tools/xrun --intent gpu asks for the MIF top row (--mif-clk) by default; --no-mif-clk opts out; wlan/timing keep theirs (off) unless --mif-clk is given.
Witness = the `build.sh argv:` line of --print-bootargs (build-xnu-image.sh prints it for --mif-clk). Host only: no build, no lock, no phone.
Includes a mutant: xrun without the default line must fail the gpu case."""
import atexit, os, shutil, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
GPU = ["--intent", "gpu", "--gpu-handover", "--label", "gputest-mifdefault"]
WLAN = ["--intent", "wlan", "--wlan-hold", "--label", "wlantest-mifdefault"]
POLL_LINE_START = "if [ \"$intent\" = gpu ] && [[ \" $args \" != *\" sweetmalipoll=\"* ]]; then args="
DEFAULT_LINE = 'if [ "$intent" = gpu ] && [ -z "$mifclk" ] && [ "$nomif" = 0 ]; then mifclk=--mif-clk; mifstate="on (default for --intent gpu; --no-mif-clk turns it off)"; fi\n'


def checkout(mutant=False, nopoll=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh", "boards.py"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    for d in ("soc", "device"):	# tools/boards.py (the --mif-clk-level check) reads them next to tools/
        os.symlink(os.path.join(os.path.dirname(HERE), d), top + "/" + d)
    if mutant:
        src = open(top + "/tools/xrun").read()
        assert DEFAULT_LINE in src, "the default line moved: update DEFAULT_LINE"
        open(top + "/tools/xrun", "w").write(src.replace(DEFAULT_LINE, ""))
    if nopoll:
        src = open(top + "/tools/xrun").read()
        lines = [l for l in src.splitlines(True) if not l.startswith(POLL_LINE_START)]
        assert len(lines) == len(src.splitlines(True)) - 1, "the sweetmalipoll default line moved: update POLL_LINE_START"
        open(top + "/tools/xrun", "w").write("".join(lines))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def argv(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="mifdefault host test, not a run", ARTIFACTS=top)   # no ov-gpu-hot here: the gpu intent default overlay is a GAP, not a build input
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    return p.returncode, out, [l for l in out.splitlines() if l.startswith("build.sh argv:")]


class MifDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = checkout(); cls.mut = checkout(mutant=True); cls.mutpoll = checkout(nopoll=True)

    def test_gpu_intent_has_it_by_default(self):
        rc, out, l = argv(self.top, *GPU); self.assertEqual(rc, 0, out)
        self.assertEqual(len(l), 1, out); self.assertIn(" --mif-clk", l[0])

    def test_gpu_intent_opt_out(self):
        rc, out, l = argv(self.top, *GPU, "--no-mif-clk"); self.assertEqual(rc, 0, out); self.assertEqual(l, [], out)

    def test_wlan_intent_stays_off(self):
        rc, out, l = argv(self.top, *WLAN); self.assertEqual(rc, 0, out); self.assertEqual(l, [], out)

    def test_wlan_intent_can_opt_in(self):
        rc, out, l = argv(self.top, *WLAN, "--mif-clk"); self.assertEqual(rc, 0, out)
        self.assertEqual(len(l), 1, out); self.assertIn(" --mif-clk", l[0])

    def test_note_names_the_state(self):
        self.assertIn("MIF CLK: request on (default for --intent gpu", argv(self.top, *GPU)[1])
        self.assertIn("MIF CLK: request off;", argv(self.top, *GPU, "--no-mif-clk")[1])
        self.assertIn("MIF CLK: request off;", argv(self.top, *WLAN)[1])
        self.assertIn("MIF CLK: request on (--mif-clk)", argv(self.top, *WLAN, "--mif-clk")[1])

    def test_both_flags_are_refused(self):
        rc, out, l = argv(self.top, *GPU, "--mif-clk", "--no-mif-clk"); self.assertEqual(rc, 2, out)
        self.assertIn("RESULT: ERROR --mif-clk and --no-mif-clk together", out); self.assertEqual(l, [], out)

    def test_mutant_without_the_default_is_caught(self):
        rc, out, l = argv(self.mut, *GPU); self.assertEqual(rc, 0, out)
        self.assertEqual(l, [], "mutant (no default line) still has --mif-clk: the gpu test would not catch it")


    # ALL-ON T-02 --mif-clk-level KHZ: implies --mif-clk, passed on to the build only for a MIF table row; nothing else gets as far as a build, a lock or the phone
    ROWS = ("3172000", "2730000", "2535000", "2288000", "2028000", "1716000", "1539000", "1352000", "1014000", "845000", "676000", "546000", "421000")

    def test_level_every_row_is_passed_on(self):
        for r in self.ROWS:
            rc, out, l = argv(self.top, *WLAN, "--mif-clk-level", r); self.assertEqual(rc, 0, (r, out))
            self.assertEqual(len(l), 1, out); self.assertIn(" --mif-clk", l[0]); self.assertIn(f" --mif-clk-level {r}", l[0])
            self.assertIn(f"--mif-clk-level {r}: the MIF request is that table row", out)

    def test_level_refused_values_stop_before_the_build(self):
        for bad in ("1716001", "0", "3172000x", "-1", "0x1b", "422000", "99999999999"):
            rc, out, l = argv(self.top, *WLAN, "--mif-clk-level", bad); self.assertEqual(rc, 2, (bad, out)); self.assertEqual(l, [], out)
            self.assertIn("RESULT: ERROR", out); self.assertIn("--mif-clk-level", out)

    def test_level_with_no_mif_clk_is_refused_and_is_never_a_default(self):
        rc, out, l = argv(self.top, *GPU, "--mif-clk-level", "1716000", "--no-mif-clk"); self.assertEqual(rc, 2, out); self.assertEqual(l, [], out)
        for a in (GPU, WLAN):
            rc, out, l = argv(self.top, *a); self.assertEqual(rc, 0, out); self.assertTrue(all("--mif-clk-level" not in x for x in l), out)

    def test_level_missing_value_is_refused(self):
        rc, out, l = argv(self.top, *WLAN, "--mif-clk-level"); self.assertNotEqual(rc, 0, out); self.assertEqual(l, [], out)

    # T-2b87: sweetmalipoll=20 is a boot-arg default of --intent gpu (observed score 27 -> 38, gputest-cced-poll20)
    def boot_args(self, top, *a):
        rc, out, _ = argv(top, *a); self.assertEqual(rc, 0, out)
        return rc, out, [l for l in out.splitlines() if l.startswith("boot-args: ")]

    def test_gpu_intent_has_poll20_by_default(self):
        _, out, b = self.boot_args(self.top, *GPU)
        self.assertEqual(len(b), 1, out); self.assertEqual(b[0].split().count("sweetmalipoll=20"), 1, b[0])
        self.assertRegex(out, r"implicit: .*sweetmalipoll=20")

    def test_explicit_poll_wins_and_is_not_duplicated(self):
        _, out, b = self.boot_args(self.top, *GPU, "--args", "sweetmalipoll=200")
        self.assertIn("sweetmalipoll=200", b[0]); self.assertNotIn("sweetmalipoll=20 ", b[0] + " "); self.assertEqual(b[0].count("sweetmalipoll"), 1, b[0])

    def test_other_intents_keep_the_xnu_poll(self):
        _, out, b = self.boot_args(self.top, *WLAN); self.assertNotIn("sweetmalipoll", b[0])

    # T-f7d7: sweetmem=1 (patch 0212 memory probe) is a boot-arg default of --intent gpu only; the other intents leave the probe off so its 64 MiB kalloc does not move the DMA blocks
    def test_gpu_intent_asks_for_the_mem_probe_others_do_not(self):
        _, out, b = self.boot_args(self.top, *GPU); self.assertEqual(b[0].split().count("sweetmem=1"), 1, b[0]); self.assertRegex(out, r"implicit: .*sweetmem=1")
        _, out, b = self.boot_args(self.top, *WLAN); self.assertNotIn("sweetmem", b[0])
        _, out, b = self.boot_args(self.top, *GPU, "--args", "sweetmem=0"); self.assertEqual(b[0].count("sweetmem"), 1, b[0]); self.assertIn("sweetmem=0", b[0])

    def test_mutant_without_the_poll_default_is_caught(self):
        _, out, b = self.boot_args(self.mutpoll, *GPU)
        self.assertNotIn("sweetmalipoll", b[0], "mutant (no poll default line) still passes sweetmalipoll=20: the test would not catch it")


if __name__ == "__main__":
    unittest.main()
