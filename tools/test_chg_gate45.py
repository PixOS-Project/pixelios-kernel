#!/usr/bin/env python3
"""Host test of xnu/patches/0305-sweet-chg-gate-pins45.patch: the charger P2 gate recheck (patch 0284) compares only the SCL/SDA pins 4/5 of the gpp23
CON/PUD words, because pins 0-3 are the touch SPI11 pads and touch is on by default since 2026-11-11 (observed resident-152843-b1: CON 0x220000 ->
0x221222, every battery row stopped). Builds sweet_chg.c = 0283 + 0284 + 0305, runs tools/chg_recheck_test.c (the 0284 cases must still pass) and
tools/chg_gate45_test.c (new cases); mutant: without 0305 the touch-pin cases must fail. No phone, no kernel build."""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import os, re, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_chg_probe as P1  # noqa: E402
PATCHES = [os.path.join(ROOT, "xnu", "patches", p) for p in ("0284-sweet-chg-i2c.patch", "0305-sweet-chg-gate-pins45.patch")]


def run(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=300)


def tree(tmp, patches):
    t = os.path.join(tmp, "tree"); os.makedirs(os.path.join(t, "bsd", "dev"))
    open(os.path.join(t, "bsd", "dev", "sweet_chg.c"), "w").write(P1.extract("bsd/dev/sweet_chg.c"))
    run("git", "-C", t, "init", "-q")
    for p in patches:
        r = run("git", "-C", t, "apply", "--include=bsd/dev/sweet_chg.c", p)
        assert r.returncode == 0, (p, r.stderr)
    return os.path.join(t, "bsd", "dev", "sweet_chg.c")


def cases(tmp, src, test):
    exe = os.path.join(tmp, test)
    r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DSWEET_CHG_HOST", '-DCHG_SRC="%s"' % src, "-I", os.path.join(ROOT, "tools"),
            os.path.join(ROOT, "tools", test + ".c"), "-o", exe)
    assert r.returncode == 0, r.stderr
    return dict(re.findall(r"^CASE (\w+) (ok|FAIL)", run(exe).stdout, re.M))


class Gate45(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="chg-gate45-"); cls.mut = tempfile.mkdtemp(prefix="chg-gate45-mut-")
        cls.src = tree(cls.tmp, PATCHES); cls.msrc = tree(cls.mut, PATCHES[:1])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True); shutil.rmtree(cls.mut, ignore_errors=True)

    def test_0284_cases_still_pass(self):
        res = cases(self.tmp, self.src, "chg_recheck_test")
        self.assertGreaterEqual(len(res), 13, res); self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {})

    def test_new_cases(self):
        res = cases(self.tmp, self.src, "chg_gate45_test")
        self.assertEqual(len(res), 8, res); self.assertEqual({k: v for k, v in res.items() if v != "ok"}, {})

    def test_mutant_without_0305(self):
        res = cases(self.mut, self.msrc, "chg_gate45_test")
        self.assertEqual(res.get("con_touch_pins_ignored"), "FAIL"); self.assertEqual(res.get("pud_touch_pins_ignored"), "FAIL")
        self.assertEqual(res.get("con_scl_change_refused"), "ok")


if __name__ == "__main__":
    r = unittest.main(exit=False).result
    print("RESULT: %s chg-gate45 tests=%d failures=%d" % ("PASS" if r.wasSuccessful() else "FAIL", r.testsRun, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
