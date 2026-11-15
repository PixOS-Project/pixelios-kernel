"""ALL-ON T-02/T-24: tools/xrun --pmic-src reaches build.sh --pmic-src through build-xnu-image.sh; off by default; refused with --el2-trap. Host only: no build, no lock, no phone.
Witness = the `build.sh argv:` line of --print-bootargs. Includes a mutant: build-xnu-image.sh without the hoarg append must lose the flag."""
import re
import atexit, os, shutil, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
WLAN = ["--intent", "wlan", "--wlan-hold", "--label", "pmicsrc-test"]
APPEND = '[ "$pmicsrc" = 0 ] || hoarg+=(--pmic-src);'


APPEND_CLR = '[ "$pmicclr" = 0 ] || hoarg+=(--pmic-src-clear);'


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/build-xnu-image.sh").read()
        cut = APPEND_CLR if mutant == "clear" else APPEND
        assert cut in src, "the append moved: update APPEND / APPEND_CLR"
        open(top + "/tools/build-xnu-image.sh", "w").write(src.replace(cut, ""))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def argv(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="pmicsrc host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    return p.returncode, out, [l for l in out.splitlines() if l.startswith("build.sh argv:")]


class PmicSrc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = checkout(); cls.mut = checkout(mutant=True); cls.mutc = checkout(mutant="clear")

    def test_flag_reaches_build_sh(self):
        rc, out, l = argv(self.top, *WLAN, "--pmic-src"); self.assertEqual(rc, 0, out)
        self.assertEqual(len(l), 1, out); self.assertIn(" --pmic-src", l[0])

    def test_off_by_default(self):
        rc, out, l = argv(self.top, *WLAN); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--pmic-src" not in x for x in l), out)

    def test_no_boot_arg_added(self):
        a = [x for x in argv(self.top, *WLAN)[1].splitlines() if x.startswith("boot-args: ")]
        b = [x for x in argv(self.top, *WLAN, "--pmic-src")[1].splitlines() if x.startswith("boot-args: ")]
        # sweetepoch= is the wall clock at build time (patch 0345): two runs straddling a second differ by 1 (gate lowclk-land6b flake); compare without it
        ep = lambda xs: [re.sub(r"sweetepoch=\d+", "sweetepoch=N", x) for x in xs]
        self.assertEqual(ep(a), ep(b))

    def test_mutant_is_caught(self):
        rc, out, l = argv(self.mut, *WLAN, "--pmic-src"); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--pmic-src" not in x for x in l), "mutant still passes --pmic-src: test_flag_reaches_build_sh would not catch it")

    def test_clear_flag_reaches_build_sh_and_implies_pmic_src(self):    # research 142: --pmic-src-clear alone passes both flags; --pmic-src alone never the write flag
        rc, out, l = argv(self.top, *WLAN, "--pmic-src-clear"); self.assertEqual(rc, 0, out)
        self.assertEqual(len(l), 1, out); self.assertIn(" --pmic-src ", l[0] + " "); self.assertIn(" --pmic-src-clear", l[0])
        rc, out, l = argv(self.top, *WLAN, "--pmic-src"); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--pmic-src-clear" not in x for x in l), out)
        rc, out, l = argv(self.top, *WLAN); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--pmic-src-clear" not in x for x in l), out)

    def test_clear_mutant_is_caught(self):
        rc, out, l = argv(self.mutc, *WLAN, "--pmic-src-clear"); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--pmic-src-clear" not in x for x in l), "mutant still passes --pmic-src-clear: test_clear_flag_reaches_build_sh would not catch it")

    def test_clear_refused_with_el2_trap(self):
        rc, out, _ = argv(self.top, "--intent", "el2", "--el2-trap", "--cpus", "1", "--pmic-src-clear", "--args", "sweetel2=1")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("--pmic-src", out)

    def test_refused_with_el2_trap(self):
        rc, out, _ = argv(self.top, "--intent", "el2", "--el2-trap", "--cpus", "1", "--pmic-src", "--args", "sweetel2=1")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("--pmic-src", out)


if __name__ == "__main__":
    unittest.main()
