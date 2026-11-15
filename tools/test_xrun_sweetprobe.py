"""LOWCLK B1-B3 (research 161, patch 0363): tools/xrun --sweet-probe reaches build.sh --sweet-probe through build-xnu-image.sh and adds boot-arg
sweetprobe=1; off by default. Host only: no build, no lock, no phone. Witness = the `build.sh argv:` + `boot-args:` lines of --print-bootargs.
Mutant: xrun without the bxi forward must lose the flag."""
import atexit, os, shutil, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
WLAN = ["--intent", "wlan", "--wlan-hold", "--label", "sweetprobe-test"]
FWD = ' ${sweetprobe:+"$sweetprobe"}'


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/xrun").read()
        assert FWD in src, "the bxi forward moved: update FWD"
        open(top + "/tools/xrun", "w").write(src.replace(FWD, ""))
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def run(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="sweetprobe host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    return (p.returncode, out, [l for l in out.splitlines() if l.startswith("build.sh argv:")],
            [l for l in out.splitlines() if l.startswith("boot-args: ")])


class SweetProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = checkout(); cls.mut = checkout(mutant=True)

    def test_flag_reaches_build_sh_and_boot_args(self):
        rc, out, l, b = run(self.top, *WLAN, "--sweet-probe"); self.assertEqual(rc, 0, out)
        self.assertEqual(len(l), 1, out); self.assertIn(" --sweet-probe", l[0])
        self.assertTrue(any("sweetprobe=1" in x for x in b), out)

    def test_off_by_default(self):
        rc, out, l, b = run(self.top, *WLAN); self.assertEqual(rc, 0, out)
        self.assertTrue(all("--sweet-probe" not in x for x in l), out)
        self.assertTrue(all("sweetprobe=" not in x for x in b), out)

    def test_mutant_is_caught(self):
        rc, out, l, b = run(self.mut, *WLAN, "--sweet-probe")
        self.assertTrue(all("--sweet-probe" not in x for x in l), "mutant still passes --sweet-probe: the forward test would not catch it")


if __name__ == "__main__":
    unittest.main()
