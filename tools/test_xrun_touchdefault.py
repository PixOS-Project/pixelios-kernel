"""tools/xrun: the touch recipe is a default for every intent except el2 (user 2026-11-11): boot-args sweettouchmux=1 sweettouchclk=3 sweettouchcs=1
sweettouchpoke=m:10c00640=0=10;d:100;m:10c0180c=5=f; + the shim touch rails (--touch-probe=rails). sweettouch=0 drops all of it; --no-rails only the rails.
Why [obs]: gputest-* 2026-11-10 (no rails) 'no ready event' + identify 00 00 00 00; 20261104T12*Z-demo-gpu-touch-wifi + demo1b (this recipe) identify 36 48.
Host only (--print-bootargs): no build, no lock, no phone. Mutant: xrun without the default lines must fail."""
import atexit, os, shutil, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ARGS_LINE = "\tgpu_add sweettouchmux 1; gpu_add sweettouchclk 3; gpu_add sweettouchcs 1; gpu_add sweettouchpoke 'm:10c00640=0=10;d:100;m:10c0180c=5=f;'\n"
RAILS_LINE = '\t[ -n "$rails" ] || [ "${norails:-0}" = 1 ] || { rails=--rails; gpuimplicit="${gpuimplicit:+$gpuimplicit }--rails"; }\n'
TOUCH = ("sweettouchmux=1", "sweettouchclk=3", "sweettouchcs=1", "sweettouchpoke=m:10c00640=0=10;d:100;m:10c0180c=5=f;")


def checkout(mutant=False):
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    if mutant:
        src = open(top + "/tools/xrun").read()
        assert ARGS_LINE in src and RAILS_LINE in src, "the touch default lines moved: update ARGS_LINE / RAILS_LINE"
        open(top + "/tools/xrun", "w").write(src.replace(ARGS_LINE, "\t:\n").replace(RAILS_LINE, ""))   # ":" keeps the if-block valid bash
    open(top + "/shim/build.sh", "w").write("#!/bin/bash\n# stock\n")
    return top


def argv(top, *a):
    e = dict(os.environ, XRUN_NO_RUNPLAN="touchdefault host test, not a run", ARTIFACTS=top)
    p = subprocess.run([top + "/tools/xrun", *a, "--print-bootargs"], capture_output=True, text=True, cwd=top, env=e)
    out = p.stdout + p.stderr
    return p.returncode, out, [l for l in out.splitlines() if l.startswith("build.sh argv:")]


def bootargs(out):
    return [l for l in out.splitlines() if l.startswith("boot-args:")][0].split()


def implicit(out):
    return out.split("implicit:", 1)[1].splitlines()[0].split()


class TouchDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = checkout(); cls.mut = checkout(mutant=True)

    def check_on(self, *a):
        rc, out, b = argv(self.top, *a)
        self.assertEqual(rc, 0, out)
        ba = bootargs(out)
        for t in TOUCH:
            self.assertIn(t, ba, a)
        self.assertIn("--rails", implicit(out), a)
        return out, b

    def test_gpu(self):
        out, b = self.check_on("--intent", "gpu", "--label", "gputest-touch")
        self.assertIn("--touch-probe=rails,dvfs", b[0])

    def test_wlan(self):
        self.check_on("--intent", "wlan", "--wlan-hold", "--label", "wlantest-touch")

    def test_bt(self):
        self.check_on("--intent", "bt", "--label", "bttest-touch")

    def test_escape_sweettouch0(self):
        rc, out, b = argv(self.top, "--intent", "gpu", "--args", "sweettouch=0", "--label", "gputest-touch")
        self.assertEqual(rc, 0, out)
        ba = bootargs(out)
        self.assertIn("sweettouch=0", ba)
        self.assertFalse([t for t in ba if t.startswith("sweettouch") and t != "sweettouch=0"], ba)
        self.assertNotIn("--rails", implicit(out)); self.assertIn("--touch-probe=dvfs", b[0])

    def test_no_rails_keeps_args(self):
        rc, out, b = argv(self.top, "--intent", "gpu", "--no-rails", "--label", "gputest-touch")
        self.assertEqual(rc, 0, out); self.assertNotIn("rails", b[0])
        for t in TOUCH:
            self.assertIn(t, bootargs(out))

    def test_explicit_key_wins(self):
        rc, out, b = argv(self.top, "--intent", "wlan", "--wlan-hold", "--args", "sweettouchmux=0", "--label", "wlantest-touch")
        self.assertEqual(rc, 0, out)
        ba = bootargs(out)
        self.assertIn("sweettouchmux=0", ba); self.assertNotIn("sweettouchmux=1", ba)

    def test_mutant_fails(self):
        rc, out, b = argv(self.mut, "--intent", "wlan", "--wlan-hold", "--label", "wlantest-touch")
        self.assertNotIn("sweettouchmux=1", bootargs(out))
        self.assertNotIn("--rails", implicit(out))


if __name__ == "__main__":
    unittest.main()
