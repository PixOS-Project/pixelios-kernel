"""Patch 0333 (SoC watchdog arm/pet from a timer, panic stop): shape checks, the host cases (via tools/test_wdt_build.py's compile) and mutants.
Prints `RESULT: PASS 0333-wdt-early cases=N mutants=M` when everything holds. The behaviour cases themselves live in tools/wdt_host_test.c (timer_*, panic_*, test_panic,
reset_stops_pets / parkfail_hook = the failed-park part: sweet_machine_reset stops the pets before its first write; the patched sweet_machine_reset_x is compiled into the host test)."""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import test_wdt_build as base  # noqa: E402
from test_wdt_early_mutants import WDT_EARLY_MUTANTS  # noqa: E402

PATCH = base.PATCH_EARLY
with open(PATCH) as _f:
    TEXT = _f.read()
CASES = []

def added_lines(path):
    sec = base.early_section(path)
    return [l[1:] for l in sec.splitlines() if l.startswith("+") and not l.startswith("+++")]


def strip_comments(src):
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def compile_and_run(early_text, reset_edits=()):
    """reset_edits: (before, after) pairs applied once each to the extracted sweet_machine_reset_x source"""
    src = base.extract_source(early_text)
    rsrc = base.extract_reset_source(early_text)
    for old, new in reset_edits:
        assert rsrc.count(old) == 1, "reset mutant text %r matches %d times: test bug" % (old, rsrc.count(old))
        rsrc = rsrc.replace(old, new)
    d = tempfile.mkdtemp(prefix="wdt-mut-")
    cpath = os.path.join(d, "sweet_wdt.c")
    with open(cpath, "w") as f:
        f.write(src)
    rpath = os.path.join(d, "sweet_reset.c")
    with open(rpath, "w") as f:
        f.write(rsrc)
    exe = os.path.join(d, "t")
    r = subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-O1", '-DWDT_SRC="%s"' % cpath, '-DRESET_SRC="%s"' % rpath, "-I", HERE, os.path.join(HERE, "wdt_host_test.c"), "-o", exe],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr
    out = subprocess.run([exe], capture_output=True, text=True).stdout
    return dict(re.findall(r"^CASE (\w+) (ok|FAIL.*)$", out, re.M)), out


class Shape(unittest.TestCase):
    def test_no_new_register_or_write_site(self):
        src = strip_comments("\n".join(added_lines("bsd/dev/sweet_wdt.c")))
        for bad in ("wdt_wr(", "wdt_set_bootloader", "sweet_pmu_write", "ml_io_map", "kernel_thread_start", "lck_", "kalloc", "IOMalloc", "wdt_pmu_map", "delay_for_interval"):
            self.assertNotIn(bad, src, "patch 0333 adds %s: a new write/map/lock/allocation (rule 8)" % bad)
        full = strip_comments(base.extract_source())
        self.assertEqual(len(re.findall(r"wdt_set_bootloader\(\)", full)), 1)
        for m in re.finditer(r"wdt_wr\((\w+)", full):
            self.assertIn(m.group(1), ("WDT_WTCON", "WDT_WTDAT", "WDT_WTCNT", "uint32_t"), m.group(0))
        self.assertEqual(len(re.findall(r"\bwdt_wr\(", full)), len(re.findall(r"\bwdt_wr\(", strip_comments(base.extract_0278_source()))), "wdt_wr call sites changed")
        CASES.append("no_new_register")

    def test_thread_call_api(self):
        full = base.extract_source()
        for fn in ("thread_call_allocate_with_priority", "thread_call_enter", "thread_call_enter_delayed", "clock_interval_to_deadline"):
            self.assertIn(fn + "(", full)
        pristine = os.path.join(os.environ.get("ARTIFACTS") or os.path.expanduser("~/pixelios-artifacts"), "xnu-pristine", "osfmk", "kern", "thread_call.h")
        if os.path.exists(pristine):
            with open(pristine) as f:
                h = f.read()
            for fn in ("thread_call_allocate_with_priority", "thread_call_enter", "thread_call_enter_delayed"):
                self.assertRegex(h, r"extern\s+\w+\s+\**\s*%s\(" % fn)
            self.assertIn("THREAD_CALL_PRIORITY_KERNEL_HIGH", h)
        CASES.append("thread_call_api")

    def test_line_truthful(self):
        full = base.extract_source()
        self.assertNotIn("arm waits for the first USB-log poll", full)
        self.assertIn("SWEET WDT: arm/pet source = timer (no USB needed)", full)
        self.assertIn("before sweet_restart_start", TEXT)       # the stated gap
        self.assertIn("Not run on hardware", TEXT)
        CASES.append("line_truthful")

    def test_panic_hooks(self):
        sec = base.early_section("osfmk/kern/debug.c", TEXT)
        added = [l[1:] for l in sec.splitlines() if l.startswith("+") and not l.startswith("+++")]
        self.assertEqual(sum("sweet_wdt_panic_note();" in l for l in added), 1)
        self.assertEqual(sum("sweet_wdt_panic_text()" in l for l in added if "panic_debugger_log" in l), 1)
        lines = sec.splitlines()
        i_note = next(i for i, l in enumerate(lines) if "+\tsweet_wdt_panic_note();" in l)
        i_early = next(i for i, l in enumerate(lines) if "sweet_early_panic(panic_format_str" in l)
        self.assertLess(i_note, i_early, "the panic note must come before the first panic copy")
        i_text = next(i for i, l in enumerate(lines) if "sweet_wdt_panic_text()" in l and l.startswith("+") and "panic_debugger_log" in l)
        i_hold = next(i for i, l in enumerate(lines) if "SWEET PANIC HOLD: reset in" in l)
        self.assertLess(i_text, i_hold)
        CASES.append("panic_hooks")

    def test_bootarg_known(self):
        self.assertIn('PE_parse_boot_argn("sweetwdtabort"', TEXT)
        self.assertIn('PE_parse_boot_argn("sweetwdtparkfail"', TEXT)
        with open(os.path.join(ROOT, "tools", "bootargs-known.txt")) as f:
            known = f.read()
        self.assertIn("sweetwdtabort\tpatch:0333\n", known)
        self.assertIn("sweetwdtparkfail\tpatch:0333\n", known)
        CASES.append("bootarg_known")

    def test_reset_path_shape(self):
        """sweet_machine_reset_x: the stop is the first statement, before every reset write and every print; nothing after it can re-enable the pets; no new write site"""
        fn = base.extract_reset_source().replace("fk_spin();", '__asm__ volatile ("wfe");').replace("fk_printf(", "printf(")
        body = fn[fn.index("sweet_machine_reset_x(int to_bootloader, int test_skip_swreset)"):fn.index("static void __attribute__((noreturn))\nsweet_machine_reset(int")]
        i_stop = body.index("sweet_wdt_stop_pets();")
        self.assertEqual(body.count("sweet_wdt_stop_pets();"), 1)
        self.assertEqual(body[:i_stop].split("{", 1)[1].strip(), "uint64_t rc;", "the stop must be the first statement")
        for tok in ("sweet_pmu_write(", "printf(", "wfe"):
            self.assertLess(i_stop, body.index(tok), "%s before the stop" % tok)
        self.assertEqual(body.count("sweet_pmu_write("), 2, "exactly the two existing writes: bootloader mode and SWRESET")
        after = body[i_stop + 1:]
        for bad in ("sweet_wdt_poll", "sweet_wdt_setup", "wdt_panicked", "sweet_wdt_panic_note", "return;", "return "):
            self.assertNotIn(bad, after, "%s after the stop: the pets could be re-enabled / the function could return" % bad)
        self.assertEqual(len(re.findall(r"sweet_machine_reset_x\(", fn)), 3, "definition + the wrapper + the park-fail hook only")
        self.assertIn("sweet_machine_reset_x(to_bootloader, 0);", fn)
        self.assertIn("sweet_machine_reset_x(1, 1);", fn)
        # the flag is stored 1 and never cleared anywhere in the patched sweet_wdt.c
        full = strip_comments(base.extract_source())
        self.assertEqual(re.findall(r"ASTORE\(&wdt_panicked, (\w+)\)", full), ["1"])
        self.assertEqual(len(re.findall(r"\bwdt_panicked\s*=[^=]", full)), 0)
        CASES.append("reset_path_shape")

    def test_reset_hunk_whole(self):
        sec = base.early_section("bsd/dev/sweet_restart.c")
        self.assertIn("+\tsweet_wdt_stop_pets();", sec)
        self.assertIn("+extern void sweet_wdt_stop_pets(void);", sec)
        CASES.append("reset_hunk")


class Mutants(unittest.TestCase):
    def test_unmutated_all_ok(self):
        res, out = compile_and_run(TEXT)
        self.assertIsNotNone(res, out)
        bad = {k: v for k, v in res.items() if v != "ok"}
        self.assertEqual(bad, {}, out)
        for need in ("reset_stops_pets", "reset_before_arm_no_arm", "parkfail_hook", "timer_arms_no_usb", "timer_same_writes", "timer_cadence_all_periods", "timer_then_hang", "panic_stops_pets", "panic_text",
                     "timer_silent_gap", "timer_alloc_fail", "timer_default_off", "timer_stop_test", "test_panic"):
            self.assertEqual(res.get(need), "ok", need)
        CASES.append("host_cases")

    def test_mutants_caught(self):
        for name, kind, old, new, case, *extra in WDT_EARLY_MUTANTS:
            if kind == "patch":
                old, new = old.replace("\n", "\n+"), new.replace("\n", "\n+")      # patch lines carry a leading '+'
                self.assertEqual(TEXT.count(old), 1, "mutant %r does not match exactly once (%d): test bug" % (name, TEXT.count(old)))
                res, out = compile_and_run(TEXT.replace(old, new))
            else:
                self.assertEqual(kind, "reset", name)
                res, out = compile_and_run(TEXT, [(old, new)] + extra)
            self.assertIsNotNone(res, "mutant %r does not compile (test bug): %s" % (name, out))
            self.assertTrue(res.get(case, "").startswith("FAIL"), "mutant %r NOT caught by host case %s: %s" % (name, case, res.get(case)))
            CASES.append("mutant:" + name)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    n_mut = len([c for c in CASES if c.startswith("mutant:")])
    print("RESULT: %s 0333-wdt-early cases=%d mutants=%d" % ("PASS" if res.wasSuccessful() else "FAIL", len(CASES), n_mut))
    sys.exit(0 if res.wasSuccessful() else 1)
