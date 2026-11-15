"""T-5d2f (PR-4): host tests for xnu/patches/0278-sweet-wdt-pet.patch and tools/wdt-check.
Prints `RESULT: PASS wdt-build cases=N patch_sha=<sha256 of the patch>` when everything below passes (the build ticket's oracle).
Covers: the patch applies on the stack (tools/stack-apply, skipped loudly without the pristine clone), sweet_wdt.c compiled on the host with a fake register
file (tools/wdt_host_test.c: period arithmetic, arg parser, default-off = zero MMIO, arm order, pet cadence, gate/readback/SYSIP failure paths, stop test),
tools/wdt-check arith/log/matrix, the ADT node, the boot-args pin (default-off byte-identical), the bootargs-known entry."""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PATCH = os.path.join(ROOT, "xnu", "patches", "0278-sweet-wdt-pet.patch")
PATCH_EARLY = os.path.join(ROOT, "xnu", "patches", "0333-sweet-wdt-early-arm.patch")
CHECK = os.path.join(ROOT, "tools", "wdt-check")
SPEC = os.environ.get("WDT_SPEC") or os.path.join(ROOT, "..", "..", "docs", "specs", "SPEC-WDT.md")
CASES = []


def extract_0278_source():
    p = open(PATCH).read()
    i = p.index("+++ b/bsd/dev/sweet_wdt.c\n")
    body = re.sub(r"^@@.*\n", "", p[i:].split("\n", 2)[2], count=1)
    return "\n".join(l[1:] for l in body.split("\n") if l.startswith("+")) + "\n"


def early_section(path="bsd/dev/sweet_wdt.c", text=None):
    """the part of patch 0333 that edits `path` (one `--- a/` .. next `--- a/` section)"""
    if text is None:
        with open(PATCH_EARLY) as f:
            text = f.read()
    t = text
    parts = re.split(r"(?m)^(?=--- a/)", t)
    sec = [x for x in parts if x.startswith("--- a/" + path + "\n")]
    assert len(sec) == 1, path
    return sec[0]


def extract_source(early_text=None):
    """sweet_wdt.c as the kernel sees it: patch 0278's new file with patch 0333 applied on top (GNU patch, as git apply would)"""
    d = tempfile.mkdtemp(prefix="wdt-src-")
    src = os.path.join(d, "sweet_wdt.c")
    with open(src, "w") as f:
        f.write(extract_0278_source())
    r = subprocess.run(["patch", "-s", "--no-backup-if-mismatch", src], input=early_section(text=early_text), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    with open(src) as f:
        return f.read()


def extract_reset_source(early_text=None):
    """the patched sweet_machine_reset_x + wrapper + sweet_restart_test_park_fail of bsd/dev/sweet_restart.c, rebuilt from the NEW side of patch 0333's hunk
    (the hunk carries the whole function as context + added lines), with the CPU spin replaced by fk_spin() and printf by fk_printf (tools/wdt_host_env.h)."""
    sec = early_section("bsd/dev/sweet_restart.c", early_text)
    new = "\n".join(l[1:] for l in sec.splitlines() if l[:1] in (" ", "+") and not l.startswith("+++")) + "\n"
    i = new.index("/*\n * No locks, no allocation: usable from the panic path.")
    j = new.index("static int\nsweet_halt_restart")
    fn = new[i:j]
    assert fn.count('__asm__ volatile ("wfe");') == 1, "spin loop not found exactly once"
    return fn.replace('__asm__ volatile ("wfe");', "fk_spin();").replace("printf(", "fk_printf(")


def run(*argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


class HostC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wdt-host-")
        src = os.path.join(cls.tmp, "sweet_wdt.c")
        open(src, "w").write(extract_source())
        rsrc = os.path.join(cls.tmp, "sweet_reset.c")
        open(rsrc, "w").write(extract_reset_source())
        cls.exe = os.path.join(cls.tmp, "wdt_host_test")
        r = run("gcc", "-Wall", "-Wextra", "-Werror", "-O1", "-DWDT_SRC=\"%s\"" % src, "-DRESET_SRC=\"%s\"" % rsrc, "-I", os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "tools", "wdt_host_test.c"), "-o", cls.exe)
        assert r.returncode == 0, r.stderr
        r = run(cls.exe)
        cls.out = r.stdout

    def test_all_cases_pass(self):
        res = dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", self.out, re.M))
        self.assertGreaterEqual(len(res), 34, self.out)
        bad = {k: v for k, v in res.items() if v != "ok"}
        self.assertEqual(bad, {}, self.out)
        CASES.extend(res)


class Tool(unittest.TestCase):
    def test_arith_golden_android(self):
        r = run(CHECK, "arith", "30")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("prescale=0x57 count=0xFFAF wtcon=0x00015739", r.stdout)
        CASES.append("arith30")

    def test_arith_range(self):
        for bad in ("9", "61", "0"):
            self.assertEqual(run(CHECK, "arith", bad).returncode, 1, bad)
        for p in range(10, 61):
            self.assertEqual(run(CHECK, "arith", str(p)).returncode, 0, p)
        CASES.append("arith_range")

    GOOD = """SWEET WDT: sweetwdt=30 stop=60 parsed, arm follows the first poll of the timer (no USB needed) or of the USB-log drain loop
SWEET WDT: inherited wtcon=0x1af18 wtdat=0xffaf wtcnt=0x1 wtcnt10=0x1 wtmincnt=0x0 pmu1244=0x1fc pmu1220=0x7fd10 rst_stat=0x80 sysip=0x100
SWEET WDT: cluster 1 left stopped by the shim (D5)
SWEET WDT: first poll at +62000 ms after parse
SWEET WDT: armed period=30 threshold_ms=10000 wtcon=0x15739 count=0xffaf readback_wtcon=0x15739
SWEET WDT: SYSIP_DAT0 0x100 -> 0xfc
SWEET WDT: pet n=1 max_gap_ms=101 wtcnt=0xffaf wtcnt_before=0xfe8b ms_since_arm=130
SWEET WDT: pet n=61 max_gap_ms=101 wtcnt=0xffaf
SWEET WDT: test: petting stopped at +60 s, reset expected within 30 s
"""

    def _log(self, text, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
        return run(CHECK, "log", f.name, *extra)

    def test_log_good(self):
        r = self._log(self.GOOD, "--expect-stop")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RESULT: PASS wdt-log", r.stdout)
        CASES.append("log_good")

    def test_log_parsed_never_armed(self):
        r = self._log(self.GOOD.split("SWEET WDT: armed")[0])
        self.assertEqual(r.returncode, 1)
        self.assertIn("WDT GAP: parsed but never armed (no pet vehicle?)", r.stdout)
        CASES.append("log_never_armed")

    def test_log_pet_after_stop_and_bad_rate(self):
        r = self._log(self.GOOD + "SWEET WDT: pet n=121 max_gap_ms=101 wtcnt=0xffaf\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("pet line after", r.stdout)
        r = self._log(self.GOOD.replace("wtcnt_before=0xfe8b", "wtcnt_before=0xffa0"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("tick rate", r.stdout)
        CASES.append("log_bad")

    def test_log_default_off(self):
        r = self._log("noise\nSWEET WDT: soc watchdog not armed (no sweetwdt)\n")
        self.assertEqual(r.returncode, 0)
        CASES.append("log_default_off")

    def test_matrix_on_spec(self):
        if not os.path.exists(SPEC):
            self.skipTest("SPEC-WDT.md not next to this checkout (set WDT_SPEC)")
        r = run(CHECK, "matrix", SPEC)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"RESULT: PASS wdt-matrix rows=\d+")
        CASES.append("matrix")


class Wiring(unittest.TestCase):
    def test_default_off_bootargs_byte_identical(self):
        s = open(os.path.join(ROOT, "tools", "build-xnu-image.sh")).read()
        line = re.search(r'^args="([^"]*)"$', s, re.M).group(1)
        self.assertNotIn("sweetwdt", s, "build-xnu-image.sh must not add sweetwdt: the arg is opt-in via --args")
        self.assertEqual(len(line.replace("$cpus", "8")), 194, "SPEC-WDT D6: the default line is 194 B with cpus=8")
        CASES.append("bootargs_default")

    def test_bootargs_known(self):
        self.assertIn("sweetwdt\tpatch:0278\n", open(os.path.join(ROOT, "tools", "bootargs-known.txt")).read())
        CASES.append("bootargs_known")

    def test_adt_node(self):
        from tools import adt, boards, mkadt
        def node(board, **kw):
            root = mkadt.build(seed=b"\0" * 64, board=board, **kw)
            mkadt.add_wdt_node(root, board, kw.get("qemu", False))      # 0278: the node is added after build() (lint-size)
            root = adt.parse(adt.serialize(root))[0]
            for c in root.children:
                props = dict((k, v) for k, _f, v in c.props)
                if props["name"] == b"sweet-wdt\0":
                    return props
            return None
        b = boards.BOARDS["bluejay"]
        p = node("bluejay")
        self.assertEqual(p["compatible"], b"google,gs101-wdt-cl0\0")
        self.assertEqual(p["reg"], mkadt.u64(0x10060000, 0x100))
        self.assertEqual((b["pmu_wdt_mask_off"], b["pmu_wdt_cnt_off"], b["pmu_rst_stat_off"]), (0x1244, 0x1220, 0x3b44))
        self.assertIsNone(node("sweet"))
        self.assertIsNone(node("bluejay", qemu=True))
        CASES.append("adt_node")

    def test_patch_shape(self):
        p = open(PATCH).read()
        # only the allowed writes: no PMU write other than the SYSIP helper, no flash path, no thread, no CL1 address (comments stripped)
        src = re.sub(r"/\*.*?\*/", "", extract_source(), flags=re.S)
        self.assertNotIn("sweet_pmu_write", src)
        self.assertNotIn("kernel_thread_start", src)
        self.assertNotIn("0x10070000", p)
        for m in re.finditer(r"wdt_wr\((\w+)", src):
            self.assertIn(m.group(1), ("WDT_WTCON", "WDT_WTDAT", "WDT_WTCNT", "uint32_t"), m.group(0))
        self.assertEqual(len(re.findall(r"wdt_set_bootloader\(\)", src)), 1)   # exactly one call site (wdt_arm_now)
        CASES.append("patch_shape")

    def test_patch_applies(self):
        pristine = os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "xnu-pristine")   # as tools/stack-apply ($ARTIFACTS/xnu-pristine)
        if not os.path.isdir(os.path.join(pristine, ".git")):
            self.skipTest("no pristine xnu clone (tools/stack-apply makes it)")
        r = run(os.path.join(ROOT, "tools", "stack-apply"), "--only-new", "0278")
        self.assertIn("RESULT: PASS", r.stdout, r.stdout + r.stderr)
        CASES.append("stack_apply")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    ok = res.wasSuccessful()
    sha = hashlib.sha256(open(PATCH, "rb").read()).hexdigest()
    print("RESULT: %s wdt-build cases=%d skipped=%d patch_sha=%s" % ("PASS" if ok else "FAIL", len(CASES), len(res.skipped), sha))
    sys.exit(0 if ok else 1)
