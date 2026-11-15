"""Host test for tools/cam_bracket.py: alternation, log, restore on stop/error, refusal on auto exposure. Fake camera, no network."""
import argparse, io, contextlib, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cam_bracket  # noqa: E402


class FakeCam:
    def __init__(self, aec=0, start=400, fail_after=None):
        self.st = {"aec": aec, "aec_value": start}
        self.sets, self.fail_after = [], fail_after

    def status(self):
        return dict(self.st)

    def set(self, var, val):
        if self.fail_after is not None and len(self.sets) >= self.fail_after and val != 400:
            raise OSError("down")
        self.sets.append((var, val))


class Bracket(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stopf, self.log = os.path.join(self.d, "stop"), os.path.join(self.d, "exp.tsv")

    def go(self, cam, stop_after=40.0):
        clock = {"t": 0.0}

        def sleep(s):
            clock["t"] += s
            if clock["t"] >= stop_after:
                open(self.stopf, "w").close()
        a = argparse.Namespace(until_file=self.stopf, log=self.log, values="180,400", period=8.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cam_bracket.run(a, cam, sleep=sleep, now=lambda: clock["t"])
        return rc, out.getvalue()

    def test_alternates_logs_and_restores(self):
        cam = FakeCam()
        rc, out = self.go(cam)
        vals = [v for _, v in cam.sets]
        self.assertEqual(rc, 0)
        self.assertEqual(vals[:4], [180, 400, 180, 400])
        self.assertEqual(vals[-1], 400)                          # restored to the start value
        self.assertEqual(len(open(self.log).read().splitlines()), len(vals) - 1)
        self.assertIn("restored to 400", out)

    def test_auto_exposure_refused(self):
        cam = FakeCam(aec=1)
        rc, out = self.go(cam)
        self.assertEqual((rc, cam.sets), (2, []))
        self.assertIn("CAM BRACKET GAP", out)

    def test_error_still_restores(self):
        cam = FakeCam(fail_after=1)
        rc, out = self.go(cam)
        self.assertEqual(rc, 1)
        self.assertEqual(cam.sets[-1], ("aec_value", 400))
        self.assertIn("restoring", out)


if __name__ == "__main__":
    r = unittest.main(exit=False).result
    print("RESULT: %s cam-bracket tests=%d failures=%d" % ("PASS" if r.wasSuccessful() else "FAIL", r.testsRun, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
