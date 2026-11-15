#!/usr/bin/env python3
"""Host tests for xnu/patches/0344-sweet-sched-recores-late.patch (boot-arg sweetrecores re-applied after the CPU start + effective mask readback; scheduler state only).
No phone, no kernel build.

    python3 tools/test_recores.py  -> `RESULT: PASS recores cases=N mutants=M patch_sha=<sha256>` / `RESULT: FAIL ...`

Proves: the patch's sweet_recores_point() (the text between its SWEET-RECORES-LOGIC marks) builds -Wall -Wextra -Werror in the host model
(tools/recores_host_test.c: a model of the scheduler's request / apply / boot-CPU rule transcribed from sched_prim.c, NOT the kernel) and the cases hold: the early
call with 0x1e leaves 0x1f in effect and says so; the late point applies and prints the exact line; no boot-arg = one late line, nothing applied; refused masks;
another caller's mask is left alone; a late point that runs first applies; failsafe / sleep override = GAP; 8 CPUs with 0xff and 0x1e. Mutants of the logic fail a case.
Text checks: the late call sits right after the 'CPUs initialized' line of SweetCPU::startAll, the early call inside patch 0270's block; the readback takes
sched_available_cores_lock; no register / bus / mapping token is added; no new boot-arg; patch 0270 comes first in the stack.
"""
if __import__("sys").argv[1:2] in (["-h"], ["--help"]):  # T-7dff:help: usage before any import or side effect
    print("usage: " + __file__.rsplit("/", 1)[-1] + "\n" + (__doc__ or "").strip()); raise SystemExit(0)
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(ROOT, "xnu", "patches", "0344-sweet-sched-recores-late.patch")
MODEL = os.path.join(ROOT, "tools", "recores_host_test.c")
BEGIN, END = "SWEET-RECORES-LOGIC-BEGIN", "SWEET-RECORES-LOGIC-END"
CASES = []

# (name, old, new): `old` exactly once in the logic text; the mutated logic must fail a case (or not build)
MUTANTS = (
    ("late-apply-removed", "\t\tsched_perfcontrol_update_recommended_cores(rc);\n", ""),
    ("applies-at-early-too", "\tif (late && valid) {", "\tif (valid) {"),
    ("other-caller-overridden", "if (req != rc && req != ALL_CORES_RECOMMENDED) {", "if (0) {"),
    ("nobody-asked-refused", "if (req != rc && req != ALL_CORES_RECOMMENDED) {", "if (req != rc) {"),
    ("other-caller-silent", "\t\t\tprintf(\"SWEET GAP: SCHED recommended cores late: the scheduler's requested mask", "\t\t\tif (0) printf(\"SWEET GAP: SCHED recommended cores late: the scheduler's requested mask"),
    ("refused-mask-applied", "bool valid = have && (rc & cpus) != 0 && (rc & ~cpus) == 0;", "bool valid = have;"),
    ("refused-silent", "\tif (late && have && !valid) {", "\tif (0 && late && have && !valid) {"),
    ("mismatch-silent", "\tif ((eff & online) != (want & online)) {", "\tif (0 && (eff & online) != (want & online)) {"),
    ("compare-ignores-online", "\tif ((eff & online) != (want & online)) {", "\tif (eff != want) {"),
    ("want-is-all-cpus", "\twant = valid ? rc : cpus;", "\twant = cpus;"),
    ("stale-readback", "\t}\n\tsweet_recores_read(&req, &eff, &online, &held);\n\twant", "\t}\n\twant"),
    ("early-noarg-prints", "\tif (!late && !valid) {\n\t\treturn;", "\tif (0) {\n\t\treturn;"),
    ("held-not-printed", "when, eff & online, want & online, held,", "when, eff & online, want & online, 0u,"),
)


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


def rd(path):
    with open(path) as f:
        return f.read()


def section(text, path):
    """the diff section of `path` in the patch"""
    m = re.search(r"^diff --git a/%s b/%s\n(.*?)(?=^diff --git |\Z)" % (re.escape(path), re.escape(path)), text, re.S | re.M)
    assert m, "patch has no section for %s" % path
    return m.group(1)


def added(text, path):
    return "\n".join(l[1:] for l in section(text, path).splitlines() if l.startswith("+") and not l.startswith("+++")) + "\n"


def logic(text):
    src = added(text, "osfmk/kern/sched_prim.c")
    a, b = src.index(BEGIN), src.index(END)
    return src[src.index("\n", a) + 1:src.rindex("\n", 0, b) + 1]


def strip_comments(src):
    """without comments; a comment whose opening line is diff context leaves only its ' * ...' lines in the added text, so those go too"""
    return re.sub(r"(?m)^ \*( .*)?$", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))


def build_and_run(logic_text):
    """-> (cases dict or None, output)"""
    d = tempfile.mkdtemp(prefix="recores-")
    lp = os.path.join(d, "logic.c")
    with open(lp, "w") as f:
        f.write(logic_text)
    exe = os.path.join(d, "t")
    r = run("gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1", '-DLOGIC_SRC="%s"' % lp, MODEL, "-o", exe)
    if r.returncode != 0:
        return None, r.stderr
    o = run(exe)
    return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", o.stdout, re.M)), o.stdout


TEXT = rd(PATCH)


class Model(unittest.TestCase):
    def test_cases(self):
        cases, out = build_and_run(logic(TEXT))
        self.assertIsNotNone(cases, "the patch's logic does not build in the host model:\n%s" % out)
        self.assertGreaterEqual(len(cases), 20, out)
        bad = {k: v for k, v in cases.items() if v != "ok"}
        self.assertEqual(bad, {}, out)
        self.assertIn("CASES ok fails=0", out)
        CASES.extend(cases)

    def test_mutants(self):
        base = logic(TEXT)
        for name, old, new in MUTANTS:
            self.assertEqual(base.count(old), 1, "mutant %s: text matches %d times (test bug)" % (name, base.count(old)))
            cases, out = build_and_run(base.replace(old, new))
            killed = cases is None or any(v != "ok" for v in cases.values())
            self.assertTrue(killed, "mutant %s survives every case" % name)


class Text(unittest.TestCase):
    def test_late_call_after_cpus_initialized(self):
        sec = section(TEXT, "iokit/Kernel/arm/SweetCPU.cpp")
        body = [l[1:] for l in sec.splitlines() if l[:1] in (" ", "+")]
        i = next(k for k, l in enumerate(body) if "sweet_recores_point(1);" in l)
        self.assertIn("topology->num_cpus, topology->num_cpus);", body[i - 1])
        self.assertIn('IOLog("SWEET CPU: %u/%u CPUs initialized', body[i - 2])
        self.assertEqual(added(TEXT, "iokit/Kernel/arm/SweetCPU.cpp").count("sweet_recores_point(1);"), 1)

    def test_early_call_in_0270_block(self):
        sec = section(TEXT, "iokit/Kernel/arm/SweetFramebuffer.cpp")
        body = [l[1:] for l in sec.splitlines() if l[:1] in (" ", "+")]
        i = next(k for k, l in enumerate(body) if "sweet_recores_point(0);" in l)
        before = "\n".join(body[:i])
        self.assertIn("sched_perfcontrol_update_recommended_cores(rc);", before, "the early readback must follow patch 0270's call")
        self.assertEqual(added(TEXT, "iokit/Kernel/arm/SweetFramebuffer.cpp").count("sweet_recores_point("), 2)   # extern + call
        self.assertNotIn("sweet_recores_point(1)", sec)

    def test_readback_under_the_scheduler_lock(self):
        src = strip_comments(added(TEXT, "osfmk/kern/sched_prim.c"))
        m = re.search(r"\nsweet_recores_read\(.*?\n}\n", src, re.S)
        self.assertTrue(m)
        f = m.group(0)
        for tok in ("splsched()", "simple_lock(&sched_available_cores_lock, LCK_GRP_NULL);", "recommended_bitmask", "sched_online_processors",
                    "perfcontrol_failsafe_active", "perfcontrol_sleep_override", "simple_unlock(&sched_available_cores_lock);", "splx(s);"):
            self.assertIn(tok, f)
        self.assertLess(f.index("simple_lock("), f.index("recommended_bitmask"))
        self.assertLess(f.index("sched_online_processors"), f.index("simple_unlock("))
        self.assertNotRegex(f, r"recommended_bitmask\s*(\|=|&=|=)[^=]", "the readback must not write the scheduler state")

    def test_scheduler_state_only(self):
        src = strip_comments("".join(added(TEXT, p) for p in ("osfmk/kern/sched_prim.c", "iokit/Kernel/arm/SweetCPU.cpp", "iokit/Kernel/arm/SweetFramebuffer.cpp")))
        for bad in ("ml_io_map", "ml_io_read", "ml_io_write", "volatile", "IOMemoryDescriptor", "sweet_pmu", "smc #", "__asm__", "kernel_thread_start", "kalloc", "IOMalloc",
                    "thread_bind", "processor_exit", "processor_start"):
            self.assertNotIn(bad, src, "patch 0344 adds %s: it must stay scheduler state only" % bad)
        self.assertEqual(len(re.findall(r"sched_perfcontrol_update_recommended_cores\(", src)), 1, "exactly one new call of patch 0270's API")

    def test_no_new_boot_arg(self):
        src = strip_comments("".join(added(TEXT, p) for p in ("osfmk/kern/sched_prim.c", "iokit/Kernel/arm/SweetCPU.cpp", "iokit/Kernel/arm/SweetFramebuffer.cpp")))
        self.assertEqual(re.findall(r'PE_parse_boot_argn\("(\w+)"', src), ["sweetrecores"])
        known = rd(os.path.join(ROOT, "tools", "bootargs-known.txt"))
        self.assertRegex(known, r"(?m)^sweetrecores\tpatch:0270$")

    def test_touches_only_three_files(self):
        self.assertEqual(re.findall(r"(?m)^diff --git a/(\S+) ", TEXT),
                         ["osfmk/kern/sched_prim.c", "iokit/Kernel/arm/SweetCPU.cpp", "iokit/Kernel/arm/SweetFramebuffer.cpp"])

    def test_after_0270(self):
        names = sorted(n for n in os.listdir(os.path.dirname(PATCH)) if n.endswith(".patch"))
        self.assertLess(names.index("0270-sweet-sched-recommended-cores.patch"), names.index(os.path.basename(PATCH)))
        self.assertIn('IOLog("SWEET SCHED: recommended cores 0x%x of 0x%x (sweetrecores, patch 0270)', section(TEXT, "iokit/Kernel/arm/SweetFramebuffer.cpp"))


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    ok = res.wasSuccessful()
    sha = hashlib.sha256(TEXT.encode()).hexdigest()
    print("RESULT: %s recores cases=%d mutants=%d patch_sha=%s" % ("PASS" if ok else "FAIL", len(CASES), len(MUTANTS), sha))
    sys.exit(0 if ok else 1)
