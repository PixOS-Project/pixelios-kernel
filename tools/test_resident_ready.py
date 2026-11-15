#!/usr/bin/env python3
"""tools/resident ready-line race (ALL-ON T-40, lane-nav 2026-11-12): a profile ready line that arrives BEFORE devd answers must count.

resident-054539-b1 [obs]: SBCoverSheet at 05:47:52Z, devd answered only at 05:48:08Z, the boot loop dropped the pumped text, the session waited
600 s for a line it had already read and the battery park rule ended the job NOTRUN. Uses the session harness of tools/test_resident.py
(FakePhone: devd answers at +60 s; here the ready line comes at +30 s). Last line: RESULT: PASS|FAIL ..."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import contextlib, io, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_resident as TR  # noqa: E402


def cases(R):
    C = []
    case, setup, submit, run, result = TR.session_env(R, C)
    tmp, ph, s = setup()
    s.profiles["gpue"] = {"intent": "gpu", "ready": ["SWEET NAV: done"], "ready_s": 100, "nav_at": 30}
    j = submit(["--profile", "gpue", "--run", "/bin/nav", "--thread", "TE"])
    out = run(s)
    case("s_ready_before_devd", "ready lines seen" in out and "before devd answered" in out and "not seen" not in out
         and result(tmp, j["id"]).get("result") == "PASS", out[-600:])
    return C


class ReadyRace(unittest.TestCase):
    def test_ready_line_before_devd(self):
        with contextlib.redirect_stdout(io.StringIO()) as o:
            c = cases(TR.load_tool())
        self.assertEqual([n for n, ok in c if not ok], [], o.getvalue()[-2000:])


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()) as o:
        c = cases(TR.load_tool())
    bad = [n for n, ok in c if not ok]
    if bad:
        print(o.getvalue()[-2000:])
    print("RESULT: %s resident ready-race cases=%d failed=%d" % ("FAIL" if bad else "PASS", len(c), len(bad)))
    sys.exit(1 if bad else 0)
