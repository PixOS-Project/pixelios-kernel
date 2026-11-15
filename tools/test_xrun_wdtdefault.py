"""autorecover 2026-11-09: tools/xrun adds boot-arg sweetwdt=60 (SoC watchdog, patch 0278) to every non-el2 run; --no-wdt and an explicit
sweetwdt=<v> win. Witness = the `implicit:` and `boot-args:` lines of --print-bootargs. Host only: no build, no lock, no phone.
Includes a mutant: xrun without the default line must fail the default case."""
import atexit, os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
LINE_START = 'if [ "$nowdt" = 0 ] && [ "$intent" != el2 ]'


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/xrun").read().splitlines(True)
        keep = [l for l in src if not l.startswith(LINE_START)]
        assert len(keep) == len(src) - 1, "the sweetwdt default line moved: update LINE_START"
        open(top + "/tools/xrun", "w").write("".join(keep))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def bootargs(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="wdtdefault host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    m = re.search(r"^boot-args: (.*)$", out, re.M)
    return (m.group(1).split() if m else None), out


class WdtDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top, cls.mut = checkout(), checkout(mutant=True)

    def test_default_on(self):
        ba, out = bootargs(self.top, "--intent", "none")
        self.assertIsNotNone(ba, out)
        self.assertIn("sweetwdt=60", ba)
        self.assertRegex(out, r"(?m)^implicit: .*sweetwdt=60")

    def test_no_wdt_and_explicit_value_win(self):
        ba, out = bootargs(self.top, "--intent", "none", "--no-wdt")
        self.assertFalse([x for x in ba if x.startswith("sweetwdt=")], out)
        ba, out = bootargs(self.top, "--intent", "none", "--args", "sweetwdt=60,stop=90")
        self.assertEqual([x for x in ba if x.startswith("sweetwdt=")], ["sweetwdt=60,stop=90"], out)

    def test_period_must_outlast_stall_plus_hold(self):
        ba, out = bootargs(self.top, "--intent", "none", "--args", "sweetwdt=30")
        self.assertIsNone(ba, out)
        self.assertIn("must be > sweetstall + 2 + sweetpanichold + 10 = 52", out)
        ba, out = bootargs(self.top, "--intent", "none", "--args", "sweetwdt=40 sweetstall=10 sweetpanichold=5")
        self.assertIn("sweetwdt=40", ba or [], out)                      # 40 > 10 + 2 + 5 + 10
        ba, out = bootargs(self.top, "--intent", "none", "--args", "sweetwdt=30,stop=60")
        self.assertIn("sweetwdt=30,stop=60", ba or [], out)               # the deliberate stop test keeps its own period

    def test_mutant_is_caught(self):
        ba, out = bootargs(self.mut, "--intent", "none")
        self.assertNotIn("sweetwdt=60", ba or [], out)


if __name__ == "__main__":
    r = unittest.main(exit=False).result
    print("RESULT: %s xrun-wdtdefault tests=%d failures=%d" % ("PASS" if r.wasSuccessful() else "FAIL", r.testsRun, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
