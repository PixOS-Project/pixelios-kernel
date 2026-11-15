"""TP-045 tools/xrun --el2-trap: refusals, build.sh argv passthrough, intent el2, no-flag output unchanged. Host only: no build, no lock, no phone."""
import atexit, os, re, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOOD = ["--el2-trap", "--intent", "el2", "--cpus", "1", "--args", "sweetel2=1"]


LINE = '\t\tCFLAGS="$CFLAGS -DEL2_RESIDENT"\n'
B3LINE = '\t\tCFLAGS="$CFLAGS -DEL2_B3"\n'
GOOD3 = ["--el2-trap", "--el2-b3", "--intent", "el2", "--cpus", "1", "--args", "sweetel2=2"]
VARIANTS = {"comment": "# the B2 mode defines EL2_RESIDENT\n", "gcc": "gcc -DEL2_RESIDENT -o t t.c\n", "line": LINE}


def checkout(resident):
    """Temp checkout copy: the real xrun + build-xnu-image.sh and a fake shim/build.sh (with or without the EL2_RESIDENT token)."""
    top = tempfile.mkdtemp(); atexit.register(shutil.rmtree, top, True)
    os.makedirs(top + "/tools"); os.makedirs(top + "/shim")
    for f in ("xrun", "build-xnu-image.sh"):
        shutil.copy(os.path.join(HERE, f), top + "/tools/" + f)
    with open(top + "/shim/build.sh", "w") as f:
        f.write("#!/bin/bash\n" + (VARIANTS["line"] if resident is True else (VARIANTS["line"] + B3LINE if resident == "b3" else VARIANTS.get(resident, "# stock\n"))))
    return top


def xrun(top, *a, env=None):
    e = dict(os.environ, XRUN_NO_RUNPLAN="el2 host test, not a run"); e.update(env or {})
    p = subprocess.run([top + "/tools/xrun", *a], capture_output=True, text=True, cwd=top, env=e)
    return p.returncode, p.stdout + p.stderr


class El2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = checkout(True); cls.stock = checkout(False); cls.b3 = checkout("b3")

    def refused(self, top, args, mode, needle):
        for extra in (["--print-bootargs"], ["--dry-run"], ["--dry-run", "--print-bootargs"], ["--kernel", "/nonexistent"]):
            rc, out = xrun(top, *args, *extra)
            self.assertEqual(rc, 2, (extra, out)); self.assertIn("RESULT: ERROR", out); self.assertIn(needle, out)
            self.assertNotIn("boot-args:", out); self.assertNotIn("EL2: resident", out)

    def test_good_print_bootargs_passes_el2_trap_before_boot_xnu(self):
        rc, out = xrun(self.res, *GOOD, "--print-bootargs"); self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"build\.sh argv: --board bluejay --avb --el2-trap --boot-xnu ")
        self.assertIn("EL2: resident (build.sh --el2-trap --boot-xnu) shim sha256 not built (dry-run)", out)
        self.assertIn("sweetel2=1", out)

    def test_refuse_companions(self):
        for flag, extra in (("--gpu-handover", []), ("--wlan-hold", []), ("--rails", []), ("--dvfs", ["600000,600000,600000"])):
            args = [a for a in GOOD]; args += [flag, *extra]
            self.refused(self.res, args, "", "--el2-trap is refused with " + flag)

    def test_refuse_cpus(self):
        for c in ("8", "2"):
            self.refused(self.res, ["--el2-trap", "--intent", "el2", "--cpus", c, "--args", "sweetel2=1"], "", "--el2-trap needs --cpus 1")
        self.refused(self.res, ["--el2-trap", "--intent", "el2", "--args", "sweetel2=1"], "", "--el2-trap needs --cpus 1")   # default 8

    def test_refuse_without_sweetel2(self):
        self.refused(self.res, ["--el2-trap", "--intent", "el2", "--cpus", "1", "--args", "sweetel2=0 foo=1"], "", "must be sweetel2=1")
        self.refused(self.res, ["--el2-trap", "--intent", "el2", "--cpus", "1"], "", "exactly one sweetel2=1")

    def test_refuse_without_el2_resident_in_build_sh(self):
        self.refused(self.stock, GOOD, "", "el2/b2-report")

    def test_refuse_sweetel2_not_exactly_one_eq_1(self):
        for a in ("sweetel2=1 sweetel2=0", "sweetel2=0 sweetel2=1", "sweetel2=10", "sweetel2=1x", "sweetel2=1 sweetel2=1", "sweetel2="):
            self.refused(self.res, ["--el2-trap", "--intent", "el2", "--cpus", "1", "--args", a], "", "sweetel2")

    def test_build_sh_detection_is_the_assignment_line(self):
        for v in ("comment", "gcc"):
            self.refused(checkout(v), GOOD, "", "el2/b2-report")
            sh = checkout(v)
            p = subprocess.run([sh + "/tools/build-xnu-image.sh", "--el2-trap"], capture_output=True, text=True, cwd=sh, env=dict(os.environ, XNU_IMAGE_ARGS_ONLY="1"))
            self.assertEqual(p.returncode, 2, v); self.assertIn("EL2_RESIDENT", p.stderr)
        rc, out = xrun(checkout("line"), *GOOD, "--print-bootargs"); self.assertEqual(rc, 0, out)

    def test_refusal_goes_to_stderr_too(self):
        p = subprocess.run([self.res + "/tools/xrun", "--el2-trap", "--print-bootargs"], capture_output=True, text=True, cwd=self.res)
        self.assertIn("needs --intent el2", p.stderr); self.assertIn("RESULT: ERROR", p.stdout)

    def test_refuse_other_or_no_intent(self):
        for i in (["--intent", "gpu"], ["--intent", "none"], []):
            self.refused(self.res, ["--el2-trap", "--cpus", "1", "--args", "sweetel2=1", *i], "", "--el2-trap needs --intent el2")

    def test_build_xnu_image_backstop(self):
        sh = self.stock + "/tools/build-xnu-image.sh"
        p = subprocess.run([sh, "--el2-trap", "--cpus", "1"], capture_output=True, text=True, cwd=self.stock, env=dict(os.environ, XNU_IMAGE_ARGS_ONLY="1"))
        self.assertEqual(p.returncode, 2, p.stderr); self.assertIn("EL2_RESIDENT", p.stderr)
        p = subprocess.run([self.res + "/tools/build-xnu-image.sh", "--el2-trap", "--rails"], capture_output=True, text=True, cwd=self.res, env=dict(os.environ, XNU_IMAGE_ARGS_ONLY="1"))
        self.assertEqual(p.returncode, 2, p.stderr)
        for extra in (["--gpu-handover"], ["--wlan-hold"], ["--rails"], ["--dvfs", "600000,600000,600000"]):
            p = subprocess.run([self.res + "/tools/build-xnu-image.sh", "--el2-trap", *extra], capture_output=True, text=True, cwd=self.res, env=dict(os.environ, XNU_IMAGE_ARGS_ONLY="1"))
            self.assertEqual(p.returncode, 2, (extra, p.stderr)); self.assertIn("--el2-trap is refused", p.stderr); self.assertNotIn("boot-args", p.stdout)

    # ---- B3 (SPEC-EL2-PROXY-PROBE DRAFT 10 C9 G11/F10): --el2-b3 = sweetel2=2, no PMU-touching boot-args, build.sh with EL2_B3
    def test_b3_good_passes_both_flags_to_build_sh(self):
        rc, out = xrun(self.b3, *GOOD3, "--print-bootargs"); self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"build\.sh argv: --board bluejay --avb --el2-trap --el2-b3 --boot-xnu ")
        self.assertIn("EL2: resident (build.sh --el2-trap --el2-b3 --boot-xnu) shim sha256 not built (dry-run)", out); self.assertIn("sweetel2=2", out)

    def test_b3_refusals(self):
        base = ["--intent", "el2", "--cpus", "1"]
        self.refused(self.b3, ["--el2-b3", *base, "--args", "sweetel2=2"], "", "--el2-b3 needs --el2-trap")
        for a in ("sweetel2=1", "sweetel2=2 sweetel2=2", "sweetel2=2 sweetel2=1", "sweetel2=20", "sweetel2=", "foo=1"):
            self.refused(self.b3, ["--el2-trap", "--el2-b3", *base, "--args", a], "", "sweetel2=2")
        self.refused(self.res, ["--el2-trap", *base, "--args", "sweetel2=2"], "", "must be sweetel2=1")   # B2 rule exactly as before
        for a in ("sweetgpu=1", "sweetwlan=1", "sweetmali=1", "sweettouch=1", "sweetgpuhold=3", "sweetel2=2 sweetwlan=2"):
            self.refused(self.b3, ["--el2-trap", "--el2-b3", *base, "--args", a if "sweetel2" in a else "sweetel2=2 " + a], "", "PMU page")
        for a in ("sweetel2=2\n sweetgpu=1", "sweetel2=2\tsweetgpu=1", "sweetel2=2\u00a0sweetgpu=1"):   # xrun-el2-b3-r1 F1: a non-space separator hides a token
            self.refused(self.b3, ["--el2-trap", "--el2-b3", *base, "--args", a], "", "printable ASCII")
        self.refused(self.res, ["--el2-trap", "--el2-b3", *base, "--args", "sweetel2=2"], "", "no EL2_B3")   # build.sh with EL2_RESIDENT only
        self.refused(self.stock, ["--el2-trap", "--el2-b3", *base, "--args", "sweetel2=2"], "", "el2/b2-report")
        self.refused(self.b3, ["--el2-trap", "--el2-b3", "--intent", "el2", "--cpus", "8", "--args", "sweetel2=2"], "", "--el2-trap needs --cpus 1")
        self.refused(self.b3, ["--el2-trap", "--el2-b3", "--intent", "gpu", "--cpus", "1", "--args", "sweetel2=2"], "", "needs --intent el2")
        self.refused(self.b3, [*GOOD3, "--rails"], "", "--el2-trap is refused with --rails")

    def test_b3_mark_line_carries_the_flag(self):   # xrun-el2-b3-r1 F4: run the REAL el2desc + mark lines of tools/xrun (a real run is not reachable on the host)
        src = open(os.path.join(HERE, "xrun")).read().splitlines()
        desc = next(l for l in src if l.startswith("el2desc=")); mark = next(l for l in src if "$R mark" in l and 'el2="' in l)
        d = tempfile.mkdtemp(); atexit.register(shutil.rmtree, d, True); open(d + "/shim-boot-avb.img", "w").write("x")
        for b3, want in (("1", "resident (build.sh --el2-trap --el2-b3 --boot-xnu) shim sha256 "), ("", "resident (build.sh --el2-trap --boot-xnu) shim sha256 ")):
            p = subprocess.run(["bash", "-c", "el2=1; el2b3=$1; R=echo; D=$2\n" + desc + "\n" + mark, "x", b3, d], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr); self.assertIn("el2=" + want, p.stdout)

    def test_b3_build_xnu_image_backstop(self):
        env = dict(os.environ, XNU_IMAGE_ARGS_ONLY="1")
        for top, args, needle in ((self.res, ["--el2-trap", "--el2-b3"], "EL2_B3"), (self.b3, ["--el2-b3"], "--el2-b3 needs --el2-trap")):
            p = subprocess.run([top + "/tools/build-xnu-image.sh", *args, "--cpus", "1"], capture_output=True, text=True, cwd=top, env=env)
            self.assertEqual(p.returncode, 2, p.stderr); self.assertIn(needle, p.stderr)
        for a in ("sweetel2=2 sweetgpu=1", "sweetwlan=1", "sweetmali=1 sweetel2=2", "sweettouch=1"):   # xrun-el2-b3-r1 F3
            p = subprocess.run([self.b3 + "/tools/build-xnu-image.sh", "--el2-trap", "--el2-b3", "--cpus", "1", "--args", a], capture_output=True, text=True, cwd=self.b3, env=env)
            self.assertEqual(p.returncode, 2, (a, p.stderr)); self.assertIn("PMU page", p.stderr)
        p = subprocess.run([self.b3 + "/tools/build-xnu-image.sh", "--el2-trap", "--el2-b3", "--cpus", "1", "--args", "sweetel2=2"], capture_output=True, text=True, cwd=self.b3, env=env)
        self.assertEqual(p.returncode, 0, p.stderr); self.assertIn("build.sh argv: --board bluejay --avb --el2-trap --el2-b3 --boot-xnu", p.stdout)

    def test_no_flag_output_unchanged(self):
        for top in (self.res, self.stock):
            rc, out = xrun(top, "--print-bootargs", "--cpus", "8", "--args", "foo=1"); self.assertEqual(rc, 0, out)
            self.assertNotIn("EL2", out); self.assertNotIn("build.sh argv", out); self.assertNotIn("el2", out.lower())
            rc2, out2 = xrun(top, "--print-bootargs", "--cpus", "8", "--args", "foo=1", "--intent", "none")
            ep = lambda t: re.sub(r"sweetepoch=\d{10}", "sweetepoch=EPOCH", t)   # xnu 0345: xrun stamps the host clock, so two calls may differ in that token only
            self.assertEqual(ep(out), ep(out2))


class Report(unittest.TestCase):
    def run_report(self, *marks):
        d = tempfile.mkdtemp(); atexit.register(shutil.rmtree, d, True)
        subprocess.run([sys.executable, os.path.join(HERE, "xrun_report.py"), "mark", d, "label=x", *marks], check=True)
        p = subprocess.run([sys.executable, os.path.join(HERE, "xrun_report.py"), "error", d, "boom"], capture_output=True, text=True)
        return p.stdout

    def test_el2_run_shows_intent_and_summary_line(self):
        out = self.run_report("intent=el2", "el2=resident (build.sh --el2-trap --boot-xnu) shim sha256 0123456789abcdef")
        self.assertRegex(out.splitlines()[0], r"^RESULT: ERROR .* gaps=\d+ intent=el2$")
        self.assertIn("EL2: resident (build.sh --el2-trap --boot-xnu) shim sha256 0123456789abcdef", out)

    def test_ordinary_run_report_unchanged(self):
        out = self.run_report()
        self.assertNotIn("intent=", out); self.assertNotIn("EL2:", out)


class Intent(unittest.TestCase):
    def pf(self, *argv, label="run"):
        return subprocess.run([sys.executable, os.path.join(HERE, "run-preflight"), "--intent-check", "el2", "--label", label, "--", "tools/xrun", "--restart", "60", *argv],
                              capture_output=True, text=True, cwd=ROOT, env=dict(os.environ, XRUN_NO_RUNPLAN="el2 host test, not a run"))

    def test_intent_el2_requires_flag_cpus_and_arg(self):
        for argv, miss in ((["--cpus", "1", "--args", "sweetel2=1"], "--el2-trap"), (["--el2-trap", "--args", "sweetel2=1"], "--cpus 1"),
                           (["--el2-trap", "--cpus", "8", "--args", "sweetel2=1"], "--cpus 1"), (["--el2-trap", "--cpus", "1"], "sweetel2=1"),
                           (["--el2-trap", "--cpus", "1", "--args", "sweetel2=1 sweetel2=0"], "sweetel2=1 (found"), (["--el2-trap", "--cpus", "1", "--args", "sweetel2=10"], "sweetel2=1 (found"),
                           (["--el2-trap", "--cpus", "1", "--args", "sweetel2=1 sweetel2=1"], "sweetel2=1 (found")):
            p = self.pf(*argv); self.assertEqual(p.returncode, 2, p.stdout + p.stderr); self.assertIn("intent el2: missing", p.stdout); self.assertIn(miss, p.stdout)
        p = self.pf("--el2-trap", "--cpus", "1", "--args", "sweetel2=1"); self.assertNotIn("intent el2", p.stdout)

    def test_intent_el2_b3_wants_sweetel2_2(self):
        p = self.pf("--el2-trap", "--el2-b3", "--cpus", "1", "--args", "sweetel2=2"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr); self.assertNotIn("intent el2", p.stdout)
        p = self.pf("--el2-trap", "--el2-b3", "--cpus", "1", "--args", "sweetel2=1"); self.assertEqual(p.returncode, 2, p.stdout); self.assertIn("exactly one sweetel2=2 (found sweetel2=1)", p.stdout)
        p = self.pf("--el2-trap", "--cpus", "1", "--args", "sweetel2=2"); self.assertEqual(p.returncode, 2, p.stdout); self.assertIn("exactly one sweetel2=1 (found sweetel2=2)", p.stdout)


if __name__ == "__main__":
    unittest.main()
