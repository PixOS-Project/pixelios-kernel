#!/usr/bin/env python3
"""Tests for tools/run-preflight: one case per antipattern that motivates a check (fixture repo + run dirs in a tempdir)."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "run-preflight")
GOOD = "debug=0xc cpus=8 -noprogress sweettouchpoke=m:10c00640=0=10;d:100;m:10c0180c=5=f;"


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = self.root = self.t.name
        for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", "-C", r, *c], check=True)
        put(r + "/f", "x")
        put(r + "/.gitignore", "shim/\nlogs/\nlock/\n*.txt\n")
        subprocess.run(["git", "-C", r, "add", "f", ".gitignore"], check=True)
        subprocess.run(["git", "-C", r, "commit", "-qm", "c"], check=True)
        self.h = subprocess.run(["git", "-C", r, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        put(r + "/shim/out/gate.result", "GATE PASS %s 123\n" % self.h)
        put(r + "/known.txt", "debug\txnu\ncpus\txnu\nnoprogress\txnu\nsweettouchpoke\tpatch:0093\n")
        put(r + "/extra.txt", "")
        put(r + "/flashed.txt", "9223c0f2aa 2026-10-24T13:01:14Z /x.img\n")
        self.env = dict(os.environ, PREFLIGHT_ROOT=r, PREFLIGHT_KNOWN=r + "/known.txt", PREFLIGHT_EXTRA=r + "/extra.txt",
                        FLASH_STATE=r + "/flashed.txt", RUNS_ROOT=r, PHONE_LOCK_DIR=r + "/lock")

    def tearDown(self):
        self.t.cleanup()

    def pf(self, *a):
        p = subprocess.run([sys.executable, TOOL, *a], capture_output=True, text=True, env=self.env)
        return p.returncode, p.stdout

    def xrun(self, args=GOOD, *more):
        return self.pf("--", "tools/xrun", "--kernel", "K", "--args", args, *more)

    def test_pass(self):
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.rstrip().endswith("RESULT: PASS"), out)

    def test_ap10_poke_without_semicolon(self):
        rc, out = self.xrun("sweettouchpoke=m:10c0180c=5=f")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL syntax:", out)

    def test_unknown_bootarg(self):
        rc, out = self.xrun(GOOD + " nosuchthing=1")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL bootargs:", out)
        self.assertIn("nosuchthing", out)
        put(self.root + "/extra.txt", "nosuchthing  # cited somewhere\n")
        self.assertEqual(self.xrun(GOOD + " nosuchthing=1")[0], 0)

    def test_ap27_expect_after_stop_row(self):
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1407 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL reach:", out)
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1397 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 0, out)

    def probe_dir(self, name, secs, args):
        d = self.root + "/logs/probe/" + name
        put(d + "/report.txt", "RESULT: PASS x\n"); put(d + "/run.args", args + "\n")
        put(d + "/fboot.log", "f"); put(d + "/usb.txt", "u")
        os.utime(d + "/fboot.log", (1000, 1000)); os.utime(d + "/usb.txt", (1000 + secs, 1000 + secs))

    def test_timeout_too_short_vs_history(self):   # review B2: the n9/n10 runs have DIFFERENT labels and the same build args
        self.probe_dir("20261023T065219Z-w3a-n8_", 178, "--wlan-link=8 --wlan-probe")
        a = ("--build-args", "--wlan-link=9 --wlan-probe", "--label", "w3a-n9")
        rc, out = self.pf("--", "tools/probe-run", *a, "--timeout", "180")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL timeout:", out)
        rc, out = self.pf("--", "tools/probe-run", *a, "--timeout", "230")   # between mx (178) and mx + margin (238): still FAIL (B6)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.pf("--", "tools/probe-run", *a, "--timeout", "600")[0], 0)
        self.assertEqual(self.pf("--", "tools/probe-run", "--build-args", "--other", "--timeout", "5")[0], 0)   # other form: no history, SKIP

    def test_timeout_default_when_omitted(self):   # --wlan-link defaults to 600 in tools/probe-run, --touch-probe to 240, else 60
        self.probe_dir("20261023T065219Z-x_", 100, "--pstore-fast --touch-probe=run")
        rc, out = self.pf("--", "tools/probe-run", "--touch-probe=run")   # default 240 >= 100 + 60
        self.assertEqual(rc, 0, out)
        self.probe_dir("20261023T065220Z-y_", 200, "--pstore-fast --touch-probe=run")
        rc, out = self.pf("--", "tools/probe-run", "--touch-probe=run")   # 240 < 200 + 60
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL timeout:", out)

    def test_stale_gate_hash(self):
        put(self.root + "/shim/out/gate.result", "GATE PASS deadbee 123\n")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate:", out)
        put(self.root + "/shim/out/gate.result", "GATE FAIL %s x 123\n" % self.h)
        self.assertEqual(self.xrun()[0], 1)
        os.remove(self.root + "/shim/out/gate.result")
        self.assertEqual(self.xrun()[0], 1)

    def test_gate_tree_changed_after_gate(self):   # review B3
        self.assertEqual(self.xrun()[0], 0)
        put(self.root + "/f", "changed")
        rc, out = self.xrun()
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL gate: tree changed", out)

    def test_gate_quick_note(self):
        put(self.root + "/shim/out/gate.result", "GATE PASS %s quick: ran 3 of 9 steps 123\n" % self.h)
        rc, out = self.xrun()
        self.assertEqual(rc, 0, out)
        self.assertIn("quick run", out)

    def test_extra_entry_needs_citation(self):   # review B7
        put(self.root + "/extra.txt", "nosuchthing\n")
        rc, out = self.xrun(GOOD + " nosuchthing=1")
        self.assertEqual(rc, 1, out)
        self.assertIn("without citation", out)

    def test_ap27_expect_equal_to_stop_row(self):   # review B6: late = r >= s
        rc, out = self.pf("--", "tools/probe-run", "--expect", "LINK R36 row=1398 rd=", "--expect", "summary stopped=refused at=S36 row=(1398|1405)")
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL reach:", out)

    def test_root_mismatch(self):
        self.assertEqual(self.pf("--root-sha", "9223c0f2", "--", "tools/xrun", "--args", GOOD)[0], 0)
        rc, out = self.pf("--root-sha", "ffff", "--", "tools/xrun", "--args", GOOD)
        self.assertEqual(rc, 1, out)
        self.assertIn("RESULT: FAIL root:", out)

    def test_lock_held_by_other(self):
        lib = os.path.join(HERE, "phone-lock")
        p = subprocess.Popen([sys.executable, lib, "run", "--owner", "other", "--", "sleep", "30"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if os.path.exists(self.root + "/lock/info.json"):
                    break
                subprocess.run(["sleep", "0.1"])
            rc, out = self.xrun()
            self.assertEqual(rc, 1, out)
            self.assertIn("RESULT: FAIL lock:", out)
        finally:
            p.terminate(); p.wait()

    def test_bad_args(self):
        for a in ([], ["--", "tools/other"], ["--bogus", "--", "tools/xrun"]):
            rc, out = self.pf(*a)
            self.assertEqual(rc, 2, out)
            self.assertIn("RESULT: ERROR", out)

    def test_generator_names(self):
        ldr = importlib.machinery.SourceFileLoader("rp", TOOL)
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("rp", ldr)); ldr.exec_module(m)
        src = ('PE_parse_boot_argn("a_arg", &x, 4); PE_parse_boot_argn("-flag", &y, 4); TUNABLE(int, v, "t_arg", 1);\n'
               'TUNABLE_DT(uint32_t, p, "/chosen", "prop", "dt_arg", 0, TUNABLE_DT_NONE);\n'
               'TUNABLE_STR(s, 8, "str_arg", "d"); PE_parse_boot_argn(name_var, &z, 4); /* comment "x" */')
        self.assertEqual(m.names_in(src), {"a_arg", "flag", "t_arg", "dt_arg", "str_arg"})


if __name__ == "__main__":
    unittest.main()
