"""tools/allon-score self-test + usage error (host only)."""
import importlib.machinery, importlib.util, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.join(HERE, "allon-score")


class AllonScore(unittest.TestCase):
    def test_self_test(self):
        r = subprocess.run([sys.executable, "-I", T, "--self-test"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS allon-score self-test", r.stdout)

    def test_bad_args_and_missing_dir(self):
        r = subprocess.run([sys.executable, "-I", T], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, "-I", T, "/nonexistent-run-dir"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("RESULT: ERROR allon-score", r.stdout)

    def test_o3_heavy_bench(self):   # T-54: O3 scores only the heavy bench, over the whole bench span (tools/fps-wholerun)
        l = importlib.machinery.SourceFileLoader("fw", os.path.join(HERE, "fps-wholerun"))
        fw = importlib.util.module_from_spec(importlib.util.spec_from_loader("fw", l))
        l.exec_module(fw)
        def boot(fps, secs, heavy=True):
            d = tempfile.mkdtemp(prefix="allon-o3-")
            body = fw.synth([("hv-switch-storm", fps), ("hv-settings-flings", fps)], step_s=secs / 4.0, reps=2)
            t0, t1 = body[0][:12].strip("[] "), body[-1][:12].strip("[] ")
            lines = ["[    10.000] pixping: summary sent=10 got=10", "[    11.000] BT VSC: adapter ON"]
            if heavy:
                lines += ["[%s] |pixnav| SWEET NAV: HEAVY start secs %d" % (t0, secs)]
            lines += body
            if heavy:
                lines += ["[%s] |pixnav| SWEET NAV: HEAVY done secs %d blocks 9" % (t1, secs)]
            open(os.path.join(d, "usbnet.log"), "w").write("".join("1794000000.000\t%s\n" % x for x in lines))
            open(os.path.join(d, "run.json"), "w").write('{"label": "o3-test"}')
            r = subprocess.run([sys.executable, "-I", T, d], capture_output=True, text=True, timeout=120)
            return [x for x in r.stdout.splitlines() if x.startswith("ORACLE 3 ")][0]
        self.assertTrue(boot(60, 640).startswith("ORACLE 3 PASS heavy 640 s: pooled"), boot(60, 640))
        self.assertTrue(boot(40, 640).startswith("ORACLE 3 FAIL"), boot(40, 640))
        self.assertIn("shorter than 600 s", boot(60, 300))
        self.assertIn("no heavy bench", boot(60, 640, heavy=False))


if __name__ == "__main__":
    unittest.main()
