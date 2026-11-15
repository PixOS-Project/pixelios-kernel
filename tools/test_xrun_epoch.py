"""wall clock 2026-11-13 (research 47 a1, xnu patch 0345): tools/xrun adds boot-arg sweetepoch=<host unix seconds> to every non-el2 run;
--no-epoch and an explicit sweetepoch= win; the token counts in the boot-line length checks (build-xnu-image.sh --print-bootargs refuses an over-limit line).
Witness = the `implicit:` and `boot-args:` lines of --print-bootargs. Host only: no build, no lock, no phone. Includes a mutant (xrun without the default line)."""
import atexit, os, re, shutil, subprocess, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
LINE_START = 'if [ "$noepoch" = 0 ] && [ "$intent" != el2 ]'
TOKEN = re.compile(r"^sweetepoch=(\d{10})$")


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh", "mkpayload.py", "adt.py", "boards.py", "pack_wlanfw.py", "mkwlancred.py", "envfile.py", "xrun_secret_scan.py"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/xrun").read().splitlines(True)
        keep = [l for l in src if not l.startswith(LINE_START)]
        assert len(keep) == len(src) - 1, "the sweetepoch default line moved: update LINE_START"
        open(top + "/tools/xrun", "w").write("".join(keep))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def bootargs(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="epoch host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    m = re.search(r"^boot-args: (.*)$", out, re.M)
    return (m.group(1).split() if m else None), out, p.returncode


def epoch_tokens(ba):
    return [a for a in ba if a.startswith("sweetepoch")]


class EpochDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top, cls.mut = checkout(), checkout(mutant=True)

    def test_default_on_and_fresh(self):
        t0 = int(time.time())
        ba, out, _ = bootargs(self.top, "--intent", "none")
        t1 = int(time.time())
        self.assertIsNotNone(ba, out)
        toks = epoch_tokens(ba)
        self.assertEqual(len(toks), 1, out)
        m = TOKEN.match(toks[0]); self.assertTrue(m, toks)
        self.assertTrue(t0 - 1 <= int(m.group(1)) <= t1 + 1, "token %s is not the host clock now (%d..%d)" % (toks[0], t0, t1))
        self.assertRegex(out, r"(?m)^implicit: .*sweetepoch=\d{10}")

    def test_no_epoch(self):
        ba, out, _ = bootargs(self.top, "--intent", "none", "--no-epoch")
        self.assertIsNotNone(ba, out)
        self.assertEqual(epoch_tokens(ba), [], out)
        self.assertNotIn("sweetepoch", out)

    def test_explicit_wins(self):
        ba, out, _ = bootargs(self.top, "--intent", "none", "--args", "sweetepoch=1800000000")
        self.assertIsNotNone(ba, out)
        self.assertEqual(epoch_tokens(ba), ["sweetepoch=1800000000"], out)

    def test_el2_left_out(self):
        ba, out, _ = bootargs(self.top, "--intent", "el2")
        if ba is not None:
            self.assertEqual(epoch_tokens(ba), [], out)

    def test_line_length_counts_token(self):
        # the limit is mkpayload's BOOT_LINE_LENGTH - 1; a line that fits only without the token must be refused, and the same with --no-epoch must pass
        sys.path.insert(0, HERE)
        import mkpayload
        lim = mkpayload.BOOT_LINE_LENGTH - 1
        base, _, _ = bootargs(self.top, "--intent", "none", "--no-epoch")
        self.assertIsNotNone(base)
        n0 = len(" ".join(base))
        pad = "x=" + "a" * (lim - n0 - 1 - len("x=") - 5)   # one token: line = n0 + 1 + len(pad) = lim - 5 without the epoch token, lim - 5 + 22 with it
        ba, out, rc = bootargs(self.top, "--intent", "none", "--no-epoch", "--args", pad)
        self.assertIsNotNone(ba, out); self.assertEqual(rc, 0, out)
        self.assertEqual(len(" ".join(ba)), lim - 5)
        ba, out, rc = bootargs(self.top, "--intent", "none", "--args", pad)
        self.assertEqual(rc, 2, out)
        self.assertIn("REFUSED: boot-args are %d bytes, the limit is %d" % (lim - 5 + 1 + len("sweetepoch=0000000000"), lim), out)

    def test_mutant_killed(self):
        ba, out, _ = bootargs(self.mut, "--intent", "none")
        self.assertTrue(ba is None or not epoch_tokens(ba), "mutant without the default line still adds it")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s xrun-epoch tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
