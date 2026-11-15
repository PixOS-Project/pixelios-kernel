"""charging 2026-11-10: tools/xrun adds boot-args sweetchgi2c=1 sweetchgicl=900 (patches 0284/0287, docs/sprint/2026-11-10-chg.md) to every non-el2 run;
--no-chg and an explicit sweetchgi2c=/sweetchgicl= win. Witness = the `implicit:` and `boot-args:` lines of --print-bootargs. Host only: no build, no lock, no phone.
Includes a mutant: xrun without the default line must fail the default case."""
import atexit, os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
LINE_START = 'if [ "$nochg" = 0 ] && [ "$intent" != el2 ]'


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/xrun").read().splitlines(True)
        keep = [l for l in src if not l.startswith(LINE_START)]
        assert len(keep) == len(src) - 1, "the sweetchg default line moved: update LINE_START"
        open(top + "/tools/xrun", "w").write("".join(keep))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def bootargs(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="chgdefault host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    m = re.search(r"^boot-args: (.*)$", out, re.M)
    return (m.group(1).split() if m else None), out


class ChgDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top, cls.mut = checkout(), checkout(mutant=True)

    def test_default_on(self):
        ba, out = bootargs(self.top, "--intent", "none")
        self.assertIsNotNone(ba, out)
        self.assertIn("sweetchgi2c=1", ba); self.assertIn("sweetchgicl=900", ba)
        self.assertRegex(out, r"(?m)^implicit: .*sweetchgicl=900")

    def test_no_chg(self):
        ba, out = bootargs(self.top, "--intent", "none", "--no-chg")
        self.assertIsNotNone(ba, out)
        self.assertFalse([a for a in ba if a.startswith("sweetchg")], out)

    def test_explicit_wins(self):
        ba, out = bootargs(self.top, "--intent", "none", "--args", "sweetchgicl=500")
        self.assertIsNotNone(ba, out)
        self.assertIn("sweetchgicl=500", ba); self.assertNotIn("sweetchgicl=900", ba); self.assertNotIn("sweetchgi2c=1", ba)

    def test_el2_left_out(self):
        ba, out = bootargs(self.top, "--intent", "el2")
        if ba is not None:
            self.assertFalse([a for a in ba if a.startswith("sweetchg")], out)

    def test_mutant_killed(self):
        ba, out = bootargs(self.mut, "--intent", "none")
        self.assertTrue(ba is None or "sweetchgicl=900" not in ba, "mutant without the default line still adds it")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    n, f = r.testsRun, len(r.failures) + len(r.errors)
    print("RESULT: %s xrun-chgdefault tests=%d failures=%d" % ("PASS" if f == 0 else "FAIL", n, f)); sys.exit(1 if f else 0)
